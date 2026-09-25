"""Call a CADE API endpoint against a chosen environment (prod by default).

Every call is persisted to `.claude/cade-api-run/[timestamp]-[slug].json`
as a single document:

    {"inputs": {...}, "outputs": {...}}

Write-gating — the whole reason this wrapper exists instead of raw curl:

- `GET` / `HEAD` / `OPTIONS` run freely
- `POST` / `PUT` / `PATCH` require `--confirm`
- `DELETE` requires both `--confirm` and `--destructive`

The gating is client-side discipline: the API itself can't tell you're
being careful, this script can. That's the point.

Usage:
    python call_api.py GET /domains/example.com/keywords
    python call_api.py GET /domains/example.com/keywords --query '{"status":"unused"}'
    python call_api.py POST /domains/example.com/keywords --body '{...}' --confirm
    python call_api.py POST /articles/publications/bulk --body-file payload.json --confirm
    python call_api.py DELETE /keywords/<id> --confirm --destructive
    python call_api.py GET /domains/example.com/bron/cutover/verification --timeout 900
    python call_api.py GET /domains/example.com/keywords --env stg
    python call_api.py POST /domains/example.com/keywords --body '{...}' --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).parent))
from load_settings import load as load_settings, base_host  # noqa: E402


READ_METHODS = {"GET", "HEAD", "OPTIONS"}
WRITE_METHODS = {"POST", "PUT", "PATCH"}
DESTRUCTIVE_METHODS = {"DELETE"}
ALL_METHODS = READ_METHODS | WRITE_METHODS | DESTRUCTIVE_METHODS

DEFAULT_BASE_URLS: dict[str, str] = {
    "prod": "https://seo-acg-api.prod.seosara.ai",
    "stg": "https://seo-acg-api.stg.seosara.ai",
    "local": "http://localhost:8000",
}

API_PREFIX = "/api/v1"

# Run logs live outside the skill folder, per the skill's public contract.
# Anyone can grep .claude/cade-api-run/ to audit what was called.
RUN_LOG_DIR = Path(".claude/cade-api-run")


# ---------------------------------------------------------------------------
# Path and auth resolution
# ---------------------------------------------------------------------------


def _normalize_path(path: str) -> str:
    """Accept `/keywords`, `keywords`, or `/api/v1/keywords` and normalize.

    The canonical form is `/api/v1/<...>`. We accept shorthand because users
    think in endpoint names, not URL prefixes.
    """
    if not path.startswith("/"):
        path = "/" + path
    if path.startswith(API_PREFIX):
        return path
    return f"{API_PREFIX}{path}"


def _resolve_auth(explicit: str | None) -> str:
    """Decide which API key to attach: 'api' or 'plugin'.

    Every route accepts `X-API-Key` — the plugin-key dependencies
    (subscription, publication-sync) take it too — so it's the default.
    `--auth plugin` sends `X-WordPress-Plugin-Key` instead, for testing
    the plugin-side path.
    """
    return explicit or "api"


def _resolve_base_url(settings: dict[str, Any], env: str) -> str:
    """Settings wins; fall back to the baked-in env defaults.

    `api-url` is treated as a bare host — any trailing `/api/v1` or `/api`
    is stripped so the caller can paste either form.
    """
    host = base_host(settings.get("api-url"))
    if host:
        return host
    return DEFAULT_BASE_URLS[env].rstrip("/")


def _require_key(settings: dict[str, Any], which: str) -> str:
    field = "api-key" if which == "api" else "wp-plugin-api-key"
    value = (settings.get(field) or "").strip()
    if not value:
        raise RuntimeError(
            f"Missing '{field}' in the cade-api settings block. "
            f"Edit `.claude/skills.settings.<env>.json` and fill it in."
        )
    return value


# ---------------------------------------------------------------------------
# Write gating
# ---------------------------------------------------------------------------


def _check_gating(method: str, *, confirm: bool, destructive: bool) -> None:
    if method in WRITE_METHODS and not confirm:
        raise RuntimeError(
            f"{method} mutates state in the target environment. "
            f"Re-run with `--confirm` when you're sure."
        )
    if method in DESTRUCTIVE_METHODS and not (confirm and destructive):
        raise RuntimeError(
            f"{method} is destructive and irreversible. "
            f"Re-run with `--confirm --destructive` when you're sure."
        )


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def _do_call(
    req: urlrequest.Request, timeout: float | None
) -> tuple[int | None, Any, dict[str, str], int | None, str | None]:
    """Issue the request, classify the response.

    Returns `(status_code, body, response_headers, duration_ms, error)`.
    A non-2xx is still a "successful" call from this function's point of
    view — we return the status and let the caller decide. Only true
    transport failures populate `error`.
    """
    start = time.monotonic()
    try:
        with urlrequest.urlopen(req, timeout=timeout) as resp:
            body_bytes = resp.read()
            status = resp.status
            headers = dict(resp.headers.items())
    except HTTPError as e:
        body_bytes = e.read() if e.fp else b""
        status = e.code
        headers = dict(e.headers.items()) if e.headers else {}
    except URLError as e:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return None, None, {}, elapsed_ms, f"URLError: {e.reason}"
    except TimeoutError:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return None, None, {}, elapsed_ms, f"TimeoutError after {timeout}s"

    elapsed_ms = int((time.monotonic() - start) * 1000)
    body: Any
    if body_bytes:
        try:
            body = json.loads(body_bytes.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            # Response isn't JSON — save it as a string rather than lying.
            body = body_bytes.decode("utf-8", errors="replace")
    else:
        body = None
    return status, body, headers, elapsed_ms, None


# ---------------------------------------------------------------------------
# Run log
# ---------------------------------------------------------------------------


def _slugify_endpoint(method: str, path: str) -> str:
    """Filesystem-friendly slug: `keywords-domain-example-post`."""
    suffix = path[len(API_PREFIX):] or "/"
    cleaned = (
        suffix.strip("/")
        .replace("/", "-")
        .replace("{", "")
        .replace("}", "")
        .replace("?", "-")
    )
    if not cleaned:
        cleaned = "root"
    return f"{cleaned}-{method.lower()}"


def _save_run(inputs: dict[str, Any], outputs: dict[str, Any]) -> Path:
    RUN_LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = _slugify_endpoint(inputs["method"], inputs["path"])
    out_path = RUN_LOG_DIR / f"{ts}-{slug}.json"
    out_path.write_text(
        json.dumps({"inputs": inputs, "outputs": outputs}, indent=2, default=str)
    )
    return out_path


def _mask_headers(headers: dict[str, str]) -> dict[str, str]:
    """Never persist API keys in the run log. Replace with a sentinel."""
    masked = dict(headers)
    for key in ("X-API-Key", "X-WordPress-Plugin-Key", "Authorization"):
        if key in masked:
            masked[key] = "***"
    return masked


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run(
    method: str,
    path: str,
    *,
    body: Any | None = None,
    query: dict[str, Any] | None = None,
    env: str,
    auth: str | None,
    confirm: bool,
    destructive: bool,
    timeout: float | None,
    dry_run: bool,
) -> int:
    method = method.upper()
    if method not in ALL_METHODS:
        raise ValueError(
            f"Unsupported HTTP method: {method}. "
            f"One of: {', '.join(sorted(ALL_METHODS))}."
        )
    path = _normalize_path(path)
    _check_gating(method, confirm=confirm, destructive=destructive)

    settings = load_settings(env=env)
    base_url = _resolve_base_url(settings, env)
    which_auth = _resolve_auth(auth)
    key = _require_key(settings, which_auth)

    query_str = "?" + urlencode(query, doseq=True) if query else ""
    url = f"{base_url}{path}{query_str}"

    headers: dict[str, str] = {"Accept": "application/json"}
    if which_auth == "api":
        headers["X-API-Key"] = key
    else:
        headers["X-WordPress-Plugin-Key"] = key

    inputs: dict[str, Any] = {
        "method": method,
        "path": path,
        "url": url.replace(key, "***") if key else url,
        "query": query or {},
        "body": body,
        "env": env,
        "base_url": base_url,
        "auth": which_auth,
        "headers": _mask_headers(headers),
        "timeout_s": timeout,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    if dry_run:
        # Emit the full input record but DO NOT hit the network or write a log.
        print(json.dumps({"inputs": inputs, "dry_run": True}, indent=2, default=str))
        return 0

    data_bytes: bytes | None = None
    if body is not None:
        data_bytes = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urlrequest.Request(url, data=data_bytes, headers=headers, method=method)
    status, resp_body, resp_headers, duration_ms, error = _do_call(req, timeout)

    outputs: dict[str, Any] = {
        "status_code": status,
        "body": resp_body,
        "duration_ms": duration_ms,
        "response_headers": {
            k: v for k, v in resp_headers.items() if k.lower() != "set-cookie"
        },
        "error": error,
    }

    log_path = _save_run(inputs, outputs)
    print(f"run saved: {log_path}", file=sys.stderr)
    print(json.dumps(outputs, indent=2, default=str))

    if error is not None:
        return 3
    if status is None or not (200 <= status < 300):
        return 1
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _load_body(args: argparse.Namespace) -> Any | None:
    if args.body_file and args.body:
        raise ValueError("Pass either --body or --body-file, not both.")
    if args.body_file:
        return json.loads(Path(args.body_file).read_text())
    if args.body:
        return json.loads(args.body)
    return None


def _load_query(args: argparse.Namespace) -> dict[str, Any] | None:
    if not args.query:
        return None
    obj = json.loads(args.query)
    if not isinstance(obj, dict):
        raise ValueError("--query must be a JSON object")
    return obj


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "method",
        type=str.upper,
        choices=sorted(ALL_METHODS),
        help="HTTP method",
    )
    parser.add_argument(
        "path",
        help=(
            "Endpoint path, e.g. `/domains/example.com/keywords`. "
            "Leading `/api/v1` is optional — it's added for you."
        ),
    )
    parser.add_argument("--body", help="JSON request body inline.")
    parser.add_argument("--body-file", help="Path to a file containing the JSON body.")
    parser.add_argument(
        "--query",
        help="Query params as a JSON object, e.g. '{\"status\":\"unused\"}'.",
    )
    parser.add_argument(
        "--env",
        choices=("prod", "stg", "local"),
        default=os.environ.get("CADE_SKILL_ENV", "prod"),
        help=(
            "Which `.claude/skills.settings.{env}.json` to load "
            "(default: $CADE_SKILL_ENV or prod)."
        ),
    )
    parser.add_argument(
        "--auth",
        choices=("api", "plugin"),
        help="Override auto-detected auth header.",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Required for POST/PUT/PATCH — mutations against the target env.",
    )
    parser.add_argument(
        "--destructive",
        action="store_true",
        help="Required (together with --confirm) for DELETE. Irreversible.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help=(
            "Seconds to wait for a response. Omit to wait until the server "
            "responds — the caller decides how long a call may take."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the request that would be sent without calling the API.",
    )
    args = parser.parse_args()

    try:
        body = _load_body(args)
        query = _load_query(args)
        return run(
            args.method,
            args.path,
            body=body,
            query=query,
            env=args.env,
            auth=args.auth,
            confirm=args.confirm,
            destructive=args.destructive,
            timeout=args.timeout,
            dry_run=args.dry_run,
        )
    except (ValueError, RuntimeError, KeyError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
