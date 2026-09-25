"""Call the CADE production Flower HTTP API.

Read operations run directly. Write operations (anything that mutates
a task, worker, or queue consumer) require `--confirm` — without it the
wrapper prints the equivalent curl command and exits without touching
prod. Destructive worker operations (shutdown) additionally require
`--destructive` so an accidental `--confirm` can't take down a worker.

Credentials: `.claude/skills.settings.{env}.json` under the `cade-flower`
key. Default env is `prod`; pass `--env stg` / `--env local` to target
another environment (see SKILL.md → "Environment selection"). Transport:
HTTP Basic Auth against the Flower URL, default https.

Exit codes: 0 on success, 2 on usage error, 3 on refused write (no
--confirm), and the underlying HTTP status code class for network /
server failures.
"""

from __future__ import annotations

import argparse
import ast
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
from load_settings import load as load_settings  # noqa: E402


DEFAULT_TIMEOUT_S = 10.0

# Endpoints that mutate state. Every one of these requires --confirm.
_WRITE_PATHS = (
    "/api/task/revoke/",
    "/api/task/abort/",
    "/api/task/apply/",
    "/api/task/async-apply/",
    "/api/task/send-task/",
    "/api/task/timeout/",
    "/api/task/rate-limit/",
    "/api/worker/shutdown/",
    "/api/worker/pool/restart/",
    "/api/worker/pool/grow/",
    "/api/worker/pool/shrink/",
    "/api/worker/pool/autoscale/",
    "/api/worker/queue/add-consumer/",
    "/api/worker/queue/cancel-consumer/",
)
# A subset that is flat-out destructive. Needs --destructive too.
_DESTRUCTIVE_PATHS = ("/api/worker/shutdown/",)


# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------

def _base_url(raw: str) -> str:
    raw = raw.strip().rstrip("/")
    if not raw.startswith(("http://", "https://")):
        raw = f"https://{raw}"
    return raw


def _auth_header(user: str, password: str) -> str:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return f"Basic {token}"


def _is_write(path: str) -> bool:
    return any(path.startswith(p) for p in _WRITE_PATHS)


def _is_destructive(path: str) -> bool:
    return any(path.startswith(p) for p in _DESTRUCTIVE_PATHS)


def _curl_preview(method: str, url: str, body: dict | None, user: str) -> str:
    # Masks the password — this is the string Claude shows the user when
    # --confirm is absent. The user can run the curl themselves if they want.
    parts = [f"curl -sS -u '{user}:<password>'", f"-X {method}"]
    if body is not None:
        parts.append("-H 'Content-Type: application/json'")
        parts.append(f"--data '{json.dumps(body)}'")
    parts.append(f"'{url}'")
    return " \\\n  ".join(parts)


def _request(
    settings: dict,
    method: str,
    path: str,
    *,
    query: dict | None = None,
    body: dict | None = None,
    timeout: float = DEFAULT_TIMEOUT_S,
    confirm: bool = False,
    destructive: bool = False,
) -> tuple[int, str]:
    base = _base_url(settings["url"])
    if query:
        path = f"{path}?{urllib.parse.urlencode(query)}"
    url = f"{base}{path}"

    if _is_write(path):
        if _is_destructive(path) and not destructive:
            print(
                "refused: this endpoint shuts down a worker. "
                "Re-run with --confirm --destructive to proceed.",
                file=sys.stderr,
            )
            print(_curl_preview(method, url, body, settings["user"]), file=sys.stderr)
            return 3, ""
        if not confirm:
            print(
                "refused: this endpoint mutates prod. "
                "Re-run with --confirm to proceed. Preview:",
                file=sys.stderr,
            )
            print(_curl_preview(method, url, body, settings["user"]), file=sys.stderr)
            return 3, ""

    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", _auth_header(settings["user"], settings["password"]))
    req.add_header("Accept", "application/json")

    data: bytes | None = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req, data=data, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        # Flower returns useful error bodies (404 "Unknown task ...") — surface them.
        return e.code, e.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        print(f"network error: {e.reason}", file=sys.stderr)
        return 599, ""


def _print(body: str, raw: bool) -> None:
    if not body:
        return
    if raw:
        sys.stdout.write(body)
        return
    try:
        parsed = json.loads(body)
        print(json.dumps(parsed, indent=2, sort_keys=True))
    except json.JSONDecodeError:
        sys.stdout.write(body)


# ---------------------------------------------------------------------------
# Helpers that project subsets of task info (answers to common questions)
# ---------------------------------------------------------------------------

def _task_info(settings: dict, task_id: str, timeout: float) -> dict[str, Any] | None:
    status, body = _request(
        settings, "GET", f"/api/task/info/{urllib.parse.quote(task_id, safe='')}",
        timeout=timeout,
    )
    if status == 200:
        return json.loads(body)
    print(f"HTTP {status}: {body}", file=sys.stderr)
    return None


def _parse_repr(value: Any) -> Any:
    """Flower stores args/kwargs as Python repr strings. Best-effort parse."""
    if not isinstance(value, str):
        return value
    try:
        return ast.literal_eval(value)
    except (ValueError, SyntaxError):
        return value


def _ts_to_iso(value: Any) -> Any:
    """Unix epoch float → ISO 8601 UTC. Pass through on failure."""
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return value


def _brief_record(task_id: str, rec: dict) -> dict:
    """Compact projection of one task record (~400B vs ~25KB raw).

    Keeps only the fields that answer "what task, in what state, with what
    outcome, how long did it take" — drops args/kwargs/traceback/children/
    parent chain. For full args or traceback, fetch the task individually
    via `task payload <id>` or `task error <id>`.
    """
    exc = rec.get("exception") or ""
    first_line = next((ln for ln in exc.splitlines() if ln.strip()), "")
    if len(first_line) > 300:
        first_line = first_line[:297] + "..."

    received = rec.get("received")
    failed = rec.get("failed")
    runtime = rec.get("runtime")
    if runtime is None and received and failed:
        try:
            runtime = round(float(failed) - float(received), 1)
        except (TypeError, ValueError):
            runtime = None

    return {
        "task_id": task_id,
        "state": rec.get("state"),
        "name": rec.get("name"),
        "worker": rec.get("worker") or rec.get("hostname") or rec.get("client"),
        "queue": rec.get("routing_key"),
        "received": _ts_to_iso(received),
        "started": _ts_to_iso(rec.get("started")),
        "failed": _ts_to_iso(failed),
        "runtime_seconds": runtime,
        "retries": rec.get("retries"),
        "exception_firstline": first_line or None,
    }


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def cmd_task_info(settings, args) -> int:
    status, body = _request(
        settings, "GET", f"/api/task/info/{urllib.parse.quote(args.task_id, safe='')}",
        timeout=args.timeout,
    )
    _print(body, args.raw)
    return 0 if status == 200 else 1


def cmd_task_status(settings, args) -> int:
    info = _task_info(settings, args.task_id, args.timeout)
    if info is None:
        return 1
    # state, plus timing context that makes the state actionable
    out = {
        "task-id": info.get("task-id") or info.get("uuid"),
        "state": info.get("state"),
        "name": info.get("name"),
        "worker": info.get("worker"),
        "received": info.get("received"),
        "started": info.get("started"),
        "succeeded": info.get("succeeded"),
        "failed": info.get("failed"),
        "retries": info.get("retries"),
        "runtime": info.get("runtime"),
    }
    print(json.dumps(out, indent=2))
    return 0


def cmd_task_payload(settings, args) -> int:
    info = _task_info(settings, args.task_id, args.timeout)
    if info is None:
        return 1
    out = {
        "task-id": info.get("task-id") or info.get("uuid"),
        "name": info.get("name"),
        "args": _parse_repr(info.get("args")),
        "kwargs": _parse_repr(info.get("kwargs")),
    }
    print(json.dumps(out, indent=2, default=repr))
    return 0


def cmd_task_retries(settings, args) -> int:
    info = _task_info(settings, args.task_id, args.timeout)
    if info is None:
        return 1
    out = {
        "task-id": info.get("task-id") or info.get("uuid"),
        "name": info.get("name"),
        "state": info.get("state"),
        "retries": info.get("retries"),
        "retried": info.get("retried"),
    }
    print(json.dumps(out, indent=2))
    return 0


def cmd_task_error(settings, args) -> int:
    info = _task_info(settings, args.task_id, args.timeout)
    if info is None:
        return 1
    out = {
        "task-id": info.get("task-id") or info.get("uuid"),
        "state": info.get("state"),
        "exception": info.get("exception"),
        "traceback": info.get("traceback"),
        "failed": info.get("failed"),
    }
    print(json.dumps(out, indent=2))
    return 0


def cmd_task_result(settings, args) -> int:
    query = {"timeout": args.result_timeout} if args.result_timeout else None
    status, body = _request(
        settings, "GET",
        f"/api/task/result/{urllib.parse.quote(args.task_id, safe='')}",
        query=query, timeout=args.timeout,
    )
    _print(body, args.raw)
    return 0 if status == 200 else 1


def cmd_task_list(settings, args) -> int:
    query = {}
    for name in ("limit", "offset", "sort_by", "workername", "taskname",
                 "state", "received_start", "received_end", "search"):
        value = getattr(args, name, None)
        if value is not None:
            query[name] = value
    status, body = _request(settings, "GET", "/api/tasks", query=query or None,
                            timeout=args.timeout)
    if status != 200:
        _print(body, args.raw)
        return 1
    if not (args.count_only or args.brief):
        _print(body, args.raw)
        return 0
    # Projections — Flower returns {task_id: record, ...}
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        _print(body, args.raw)
        return 1
    if args.count_only:
        print(json.dumps({"count": len(data)}))
        return 0
    brief = [_brief_record(tid, rec) for tid, rec in data.items()]
    print(json.dumps(brief, indent=2))
    return 0


def cmd_task_types(settings, args) -> int:
    status, body = _request(settings, "GET", "/api/task/types", timeout=args.timeout)
    _print(body, args.raw)
    return 0 if status == 200 else 1


def cmd_task_revoke(settings, args) -> int:
    query = {
        "terminate": "true" if args.terminate else "false",
        "signal": args.signal,
    }
    status, body = _request(
        settings, "POST",
        f"/api/task/revoke/{urllib.parse.quote(args.task_id, safe='')}",
        query=query, timeout=args.timeout,
        confirm=args.confirm, destructive=args.destructive,
    )
    _print(body, args.raw)
    return 0 if status == 200 else (3 if status == 3 else 1)


def cmd_task_abort(settings, args) -> int:
    status, body = _request(
        settings, "POST",
        f"/api/task/abort/{urllib.parse.quote(args.task_id, safe='')}",
        timeout=args.timeout,
        confirm=args.confirm,
    )
    _print(body, args.raw)
    return 0 if status == 200 else (3 if status == 3 else 1)


def cmd_task_retry(settings, args) -> int:
    # Flower has no native "retry" — reconstruct from task info + send-task.
    info = _task_info(settings, args.task_id, args.timeout)
    if info is None:
        return 1
    name = info.get("name")
    if not name:
        print("task info has no 'name' field — cannot resubmit", file=sys.stderr)
        return 1
    body = {
        "args": _parse_repr(info.get("args")) or [],
        "kwargs": _parse_repr(info.get("kwargs")) or {},
    }
    print(f"# resubmitting '{name}' with:", file=sys.stderr)
    print(json.dumps(body, indent=2, default=repr), file=sys.stderr)
    status, resp = _request(
        settings, "POST",
        f"/api/task/send-task/{urllib.parse.quote(name, safe='')}",
        body=body, timeout=args.timeout,
        confirm=args.confirm,
    )
    _print(resp, args.raw)
    return 0 if status == 200 else (3 if status == 3 else 1)


def cmd_task_send(settings, args) -> int:
    body = {
        "args": json.loads(args.args) if args.args else [],
        "kwargs": json.loads(args.kwargs) if args.kwargs else {},
    }
    status, resp = _request(
        settings, "POST",
        f"/api/task/send-task/{urllib.parse.quote(args.taskname, safe='')}",
        body=body, timeout=args.timeout,
        confirm=args.confirm,
    )
    _print(resp, args.raw)
    return 0 if status == 200 else (3 if status == 3 else 1)


def cmd_workers(settings, args) -> int:
    query = {}
    if args.status_only:
        query["status"] = "true"
    if args.refresh:
        query["refresh"] = "true"
    if args.workername:
        query["workername"] = args.workername
    status, body = _request(settings, "GET", "/api/workers", query=query or None,
                            timeout=args.timeout)
    _print(body, args.raw)
    return 0 if status == 200 else 1


def cmd_queues(settings, args) -> int:
    status, body = _request(settings, "GET", "/api/queues/length", timeout=args.timeout)
    _print(body, args.raw)
    return 0 if status == 200 else 1


def cmd_worker_shutdown(settings, args) -> int:
    status, body = _request(
        settings, "POST",
        f"/api/worker/shutdown/{urllib.parse.quote(args.workername, safe='')}",
        timeout=args.timeout,
        confirm=args.confirm, destructive=args.destructive,
    )
    _print(body, args.raw)
    return 0 if status == 200 else (3 if status == 3 else 1)


def cmd_worker_pool(settings, args) -> int:
    op = args.op
    path_map = {
        "grow": f"/api/worker/pool/grow/{urllib.parse.quote(args.workername, safe='')}",
        "shrink": f"/api/worker/pool/shrink/{urllib.parse.quote(args.workername, safe='')}",
        "restart": f"/api/worker/pool/restart/{urllib.parse.quote(args.workername, safe='')}",
        "autoscale": f"/api/worker/pool/autoscale/{urllib.parse.quote(args.workername, safe='')}",
    }
    query: dict[str, str] = {}
    if op in ("grow", "shrink"):
        query["n"] = str(args.n)
    elif op == "autoscale":
        query["min"] = str(args.min)
        query["max"] = str(args.max)
    status, body = _request(
        settings, "POST", path_map[op], query=query or None,
        timeout=args.timeout, confirm=args.confirm,
    )
    _print(body, args.raw)
    return 0 if status == 200 else (3 if status == 3 else 1)


def cmd_worker_consumer(settings, args) -> int:
    op = args.op  # add | cancel
    path = (
        f"/api/worker/queue/{'add-consumer' if op == 'add' else 'cancel-consumer'}/"
        f"{urllib.parse.quote(args.workername, safe='')}"
    )
    status, body = _request(
        settings, "POST", path, query={"queue": args.queue},
        timeout=args.timeout, confirm=args.confirm,
    )
    _print(body, args.raw)
    return 0 if status == 200 else (3 if status == 3 else 1)


def cmd_task_timeout(settings, args) -> int:
    query = {"workername": args.workername}
    if args.soft is not None:
        query["soft"] = str(args.soft)
    if args.hard is not None:
        query["hard"] = str(args.hard)
    status, body = _request(
        settings, "POST",
        f"/api/task/timeout/{urllib.parse.quote(args.taskname, safe='')}",
        query=query, timeout=args.timeout, confirm=args.confirm,
    )
    _print(body, args.raw)
    return 0 if status == 200 else (3 if status == 3 else 1)


def cmd_task_ratelimit(settings, args) -> int:
    query = {"workername": args.workername, "ratelimit": args.ratelimit}
    status, body = _request(
        settings, "POST",
        f"/api/task/rate-limit/{urllib.parse.quote(args.taskname, safe='')}",
        query=query, timeout=args.timeout, confirm=args.confirm,
    )
    _print(body, args.raw)
    return 0 if status == 200 else (3 if status == 3 else 1)


def cmd_healthcheck(settings, args) -> int:
    status, body = _request(settings, "GET", "/healthcheck", timeout=args.timeout)
    _print(body, args.raw)
    return 0 if status == 200 else 1


def cmd_metrics(settings, args) -> int:
    # Prometheus text format — always emit raw.
    status, body = _request(settings, "GET", "/metrics", timeout=args.timeout)
    sys.stdout.write(body)
    return 0 if status == 200 else 1


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S,
                   help=f"HTTP timeout seconds (default: {DEFAULT_TIMEOUT_S})")
    p.add_argument("--raw", action="store_true",
                   help="Print response body verbatim (skip JSON pretty-print).")
    p.add_argument("--confirm", action="store_true",
                   help="Required for any write operation. Without it, writes are previewed.")
    p.add_argument("--destructive", action="store_true",
                   help="Required (together with --confirm) for worker shutdown.")
    p.add_argument(
        "--env",
        choices=("prod", "stg", "local"),
        default=os.environ.get("CADE_SKILL_ENV", "prod"),
        help=(
            "Which `.claude/skills.settings.{env}.json` file to load credentials "
            "from (default: $CADE_SKILL_ENV or prod)."
        ),
    )

    sub = p.add_subparsers(dest="cmd", required=True)

    # ---- task ----
    task = sub.add_parser("task", help="task-level operations").add_subparsers(
        dest="sub", required=True
    )
    for name, handler, helpstr in (
        ("info", cmd_task_info, "full task info dict"),
        ("status", cmd_task_status, "state + timing"),
        ("payload", cmd_task_payload, "args + kwargs"),
        ("retries", cmd_task_retries, "retries + retried"),
        ("error", cmd_task_error, "exception + traceback"),
    ):
        sp = task.add_parser(name, help=helpstr)
        sp.add_argument("task_id")
        sp.set_defaults(func=handler)

    sp = task.add_parser("result", help="task result (needs result backend)")
    sp.add_argument("task_id")
    sp.add_argument("--result-timeout", type=float, default=None,
                    help="wait up to N seconds for result")
    sp.set_defaults(func=cmd_task_result)

    sp = task.add_parser("list", help="list / filter tasks")
    sp.add_argument("--limit", type=int)
    sp.add_argument("--offset", type=int)
    sp.add_argument("--sort-by", dest="sort_by",
                    choices=["name", "state", "received", "started"])
    sp.add_argument("--workername")
    sp.add_argument("--taskname")
    sp.add_argument("--state",
                    choices=["PENDING", "RECEIVED", "STARTED", "SUCCESS",
                             "FAILURE", "RETRY", "REVOKED"])
    sp.add_argument("--received-start", dest="received_start",
                    help='format "%%Y-%%m-%%d %%H:%%M"')
    sp.add_argument("--received-end", dest="received_end",
                    help='format "%%Y-%%m-%%d %%H:%%M"')
    sp.add_argument("--search", help="free-text search across name/args/kwargs/result")
    sp.add_argument(
        "--brief",
        action="store_true",
        help="project each task to task_id/state/name/worker/queue/"
        "received/started/failed/runtime/retries/exception-first-line "
        "(~400B/task vs ~25KB raw). Use for lists of >3 tasks.",
    )
    sp.add_argument(
        "--count-only",
        dest="count_only",
        action="store_true",
        help='fetch but only emit {"count": N} — for "how many?" questions.',
    )
    sp.set_defaults(func=cmd_task_list)

    sp = task.add_parser("types", help="list seen task types")
    sp.set_defaults(func=cmd_task_types)

    sp = task.add_parser("revoke", help="(WRITE) revoke; with --terminate kills running task")
    sp.add_argument("task_id")
    sp.add_argument("--terminate", action="store_true",
                    help="kill it if it is running")
    sp.add_argument("--signal", default="SIGTERM",
                    help="signal to send on --terminate (e.g. SIGTERM, SIGKILL)")
    sp.set_defaults(func=cmd_task_revoke)

    sp = task.add_parser("abort", help="(WRITE) abort — AbortableTask only")
    sp.add_argument("task_id")
    sp.set_defaults(func=cmd_task_abort)

    sp = task.add_parser("retry", help="(WRITE) resubmit with original name+args+kwargs")
    sp.add_argument("task_id")
    sp.set_defaults(func=cmd_task_retry)

    sp = task.add_parser("send", help="(WRITE) fire a new task by name")
    sp.add_argument("taskname")
    sp.add_argument("--args", help="JSON array, e.g. '[1, 2]'")
    sp.add_argument("--kwargs", help='JSON object, e.g. \'{"a": 1}\'')
    sp.set_defaults(func=cmd_task_send)

    sp = task.add_parser("timeout", help="(WRITE) set soft/hard time limits for a task type")
    sp.add_argument("taskname")
    sp.add_argument("--workername", required=True)
    sp.add_argument("--soft", type=float)
    sp.add_argument("--hard", type=float)
    sp.set_defaults(func=cmd_task_timeout)

    sp = task.add_parser("ratelimit", help="(WRITE) set rate limit for a task type")
    sp.add_argument("taskname")
    sp.add_argument("--workername", required=True)
    sp.add_argument("--ratelimit", required=True, help='e.g. "200/m"')
    sp.set_defaults(func=cmd_task_ratelimit)

    # ---- workers ----
    sp = sub.add_parser("workers", help="list workers / stats")
    sp.add_argument("--status-only", action="store_true",
                    help="return only {worker: alive_bool}")
    sp.add_argument("--refresh", action="store_true",
                    help="run inspect to refresh cached worker list")
    sp.add_argument("--workername", help="limit to a single worker")
    sp.set_defaults(func=cmd_workers)

    # ---- queues ----
    sp = sub.add_parser("queues", help="broker queue lengths")
    sp.set_defaults(func=cmd_queues)

    # ---- worker (control) ----
    worker = sub.add_parser("worker", help="worker control operations").add_subparsers(
        dest="sub", required=True
    )
    sp = worker.add_parser("shutdown",
                           help="(DESTRUCTIVE) shut a worker down — needs --confirm --destructive")
    sp.add_argument("workername")
    sp.set_defaults(func=cmd_worker_shutdown)

    for op, n_required in (("grow", True), ("shrink", True),
                           ("restart", False), ("autoscale", False)):
        sp = worker.add_parser(f"pool-{op}", help=f"(WRITE) pool {op}")
        sp.add_argument("workername")
        if op in ("grow", "shrink"):
            sp.add_argument("--n", type=int, default=1)
        if op == "autoscale":
            sp.add_argument("--min", type=int, required=True)
            sp.add_argument("--max", type=int, required=True)
        sp.set_defaults(func=cmd_worker_pool, op=op)

    for op in ("add", "cancel"):
        sp = worker.add_parser(f"{op}-consumer",
                               help=f"(WRITE) {op} consumer for a queue on this worker")
        sp.add_argument("workername")
        sp.add_argument("--queue", required=True)
        sp.set_defaults(func=cmd_worker_consumer, op=op)

    # ---- monitor ----
    sp = sub.add_parser("healthcheck", help="GET /healthcheck (returns 'OK')")
    sp.set_defaults(func=cmd_healthcheck)
    sp = sub.add_parser("metrics", help="GET /metrics (Prometheus text)")
    sp.set_defaults(func=cmd_metrics)

    return p


def main() -> int:
    args = _build_parser().parse_args()
    try:
        settings = load_settings(env=args.env)
    except (ValueError, KeyError, FileNotFoundError) as e:
        print(f"settings error: {e}", file=sys.stderr)
        return 2
    return args.func(settings, args)


if __name__ == "__main__":
    sys.exit(main())
