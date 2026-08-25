"""Enumerate CADE API endpoints from the live OpenAPI spec.

Fetches `/v1/openapi.json` from the target env (cached under
`<skill-dir>/.cache/openapi-{env}.json`), then prints a
table of `METHOD  PATH  summary`. Filters narrow the output.

Usage:
    python list_endpoints.py
    python list_endpoints.py --filter keywords
    python list_endpoints.py --method POST
    python list_endpoints.py --env stg
    python list_endpoints.py --refresh          # bust the cache
    python list_endpoints.py --json /keywords   # dump raw OpenAPI for one path
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from urllib import request as urlrequest
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).parent))
from load_settings import load as load_settings, base_host  # noqa: E402


DEFAULT_BASE_URLS: dict[str, str] = {
    "prod": "https://seo-acg-api.prod.seosara.ai",
    "stg": "https://seo-acg-api.stg.seosara.ai",
    "local": "http://localhost:8000",
}

OPENAPI_PATH = "/v1/openapi.json"
CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache"

# OpenAPI reserves these keys at the path-item level — everything else is a
# verb. Filter them out when iterating operations.
_NON_METHOD_KEYS = {
    "parameters",
    "servers",
    "summary",
    "description",
    "$ref",
}


def _base_url(settings: dict[str, Any], env: str) -> str:
    host = base_host(settings.get("api-url"))
    return host or DEFAULT_BASE_URLS[env].rstrip("/")


def _fetch_spec(env: str, refresh: bool) -> dict[str, Any]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"openapi-{env}.json"
    if cache_path.exists() and not refresh:
        return json.loads(cache_path.read_text())

    settings = load_settings(env=env)
    url = _base_url(settings, env) + OPENAPI_PATH
    try:
        with urlrequest.urlopen(url, timeout=30) as resp:
            spec = json.loads(resp.read().decode("utf-8"))
    except URLError as e:
        raise RuntimeError(
            f"Could not fetch OpenAPI from {url}: {e.reason}. "
            f"Is the target environment reachable?"
        )
    cache_path.write_text(json.dumps(spec, indent=2))
    return spec


def _iter_endpoints(spec: dict[str, Any]):
    for path, ops in sorted(spec.get("paths", {}).items()):
        if not isinstance(ops, dict):
            continue
        for method, op in ops.items():
            if method in _NON_METHOD_KEYS:
                continue
            if not isinstance(op, dict):
                continue
            yield method.upper(), path, op


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--env", choices=("prod", "stg", "local"), default="prod")
    parser.add_argument("--filter", help="Substring match on path (case-insensitive).")
    parser.add_argument("--method", help="Filter to one HTTP method (GET/POST/PUT/...).")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Force a fresh fetch of the OpenAPI spec, busting the cache.",
    )
    parser.add_argument(
        "--json",
        metavar="PATH",
        help="Dump the raw OpenAPI operations for PATH (useful for request-body schema).",
    )
    args = parser.parse_args()

    try:
        spec = _fetch_spec(args.env, args.refresh)
    except (RuntimeError, FileNotFoundError, KeyError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if args.json:
        ops = spec.get("paths", {}).get(args.json, {})
        if not ops:
            print(f"no such path: {args.json}", file=sys.stderr)
            return 3
        print(json.dumps(ops, indent=2))
        return 0

    rows = []
    for method, path, op in _iter_endpoints(spec):
        if args.filter and args.filter.lower() not in path.lower():
            continue
        if args.method and args.method.upper() != method:
            continue
        rows.append((method, path, op.get("summary", "") or ""))

    if not rows:
        print("(no endpoints matched)")
        return 0

    m_w = max(len(m) for m, _, _ in rows)
    p_w = max(len(p) for _, p, _ in rows)
    for method, path, summary in rows:
        print(f"{method:<{m_w}}  {path:<{p_w}}  {summary}")
    print(f"\n({len(rows)} endpoints; env={args.env})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
