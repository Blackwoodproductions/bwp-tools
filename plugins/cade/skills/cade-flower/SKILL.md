---
name: cade-flower
description: Query and control the CADE production Celery cluster via the Flower HTTP API. Use this skill whenever the user asks about a specific Celery task_id (status, payload, retries, error, result), the live state of the worker pools (categorizer/content/crawler/keywords/publisher), queue depths, or needs to terminate / revoke / retry a running task. Triggers on phrases like "task status", "task id 123", "what's the payload for task X", "how many times has task X failed", "error for task X", "is the keyword worker alive", "terminate task", "revoke task", "restart task", "retry task", "flower", "celery task", "queue length", "queue depth", "which workers are up", "shut down worker", "pool grow", "pool shrink", or direct invocation "cade-flower". Includes a safety contract (read-only by default; writes gated behind --confirm; worker-shutdown behind --confirm --destructive). For database state (domain rows, content status) use cade-db-queries; for telemetry / traces use Logfire; this skill owns live Celery state only.
---

# cade-flower

> **Paths:** `<skill-dir>` is this skill's directory — Claude Code prints it as *Base directory for this skill* when the skill loads (under `~/.claude/plugins/cache/bwp-tools/cade/<version>/skills/<name>`). Run commands from the **cade-service repo root** so `venv/bin/python`, `.claude/skills.settings.{env}.json` and `misc/` resolve. Examples write `python` — use the cade-service `venv/bin/python`. Settings blocks per skill: see the bwp-tools README.

Live read + controlled write access to the CADE production Celery cluster via Flower's HTTP API. One job: answer questions about running tasks and workers, and surface (but gate) the mutations.

- **Wrapper**: `scripts/flower.py` — stdlib-only Python HTTP client over Flower's REST API.
- **API reference**: `references/api.md` — every endpoint, param, and response field. Load when the question is outside the common-questions table below.
- **Credentials**: read from `.claude/skills.settings.{env}.json` under the `cade-flower` key (gitignored via `.claude/*`). Env defaults to `prod` — see *Environment selection* below.

## Environment selection

**Default env is `prod`.** The wrapper loads credentials from `.claude/skills.settings.prod.json` unless an env is explicitly requested. Valid envs: `prod`, `stg`, `local`.

**Rule when invoking this skill:**

| User's query contains… | Env to use | Flag | Settings file |
|---|---|---|---|
| nothing about environment | `prod` | *(none — default)* | `.claude/skills.settings.prod.json` |
| "prod" / "production" | `prod` | `--env prod` (or none) | `.claude/skills.settings.prod.json` |
| "stg" / "staging" / "stage" | `stg` | `--env stg` | `.claude/skills.settings.stg.json` |
| "local" / "dev" / "development" | `local` | `--env local` | `.claude/skills.settings.local.json` |

Place `--env` before the subcommand, alongside the other global flags:

```bash
python3 <skill-dir>/scripts/flower.py --env stg task status $TID
python3 <skill-dir>/scripts/flower.py --env stg --confirm task revoke $TID
```

For batch invocations (sub-agents, scripts) set `CADE_SKILL_ENV=stg` once instead of passing `--env` on every call.

If the selected env's settings file is missing, the wrapper fails fast with a clear error — it does **not** silently fall back to `prod`. Combined with `--confirm` / `--destructive`, this keeps "I thought I was on staging" incidents out of reach: the env file defines which Flower host you talk to, full stop.

## When to use

Trigger on any "live Celery state" or "control a task/worker" request. Examples:

- "What's the status of task abc-123?"
- "Show me the payload / args / kwargs for task abc-123"
- "How many times has task abc-123 retried?"
- "What's the exception and traceback for task abc-123?"
- "Is the keywords worker alive?"
- "How many messages are queued in the content queue?"
- "How many tasks failed in the last hour?"
- "Revoke / terminate task abc-123 (running)"
- "Retry task abc-123 with its original args"
- "Grow the keywords pool by 2"

If the question is about *persisted* state (rows in `domain_content`, job records), use `cade-db-queries`. If it's about *traces / spans / log events*, use Logfire. This skill covers only what Flower can see — Celery's live in-memory state plus broker depths. Flower keeps at most `--max_tasks` tasks (10k default); older tasks fall off and return `404`.

## Safety contract

| Class | What it does | How it's gated |
|---|---|---|
| **Read** (`task info/status/payload/retries/error/result/list/types`, `workers`, `queues lengths`, `healthcheck`, `metrics`) | HTTP GET | runs directly, no flag |
| **Write** (`task revoke/abort/retry/send`, `task timeout/ratelimit`, `worker pool-grow/shrink/restart/autoscale`, `worker add-consumer/cancel-consumer`) | HTTP POST that mutates Celery state | requires `--confirm`. Without it, the wrapper prints the equivalent curl command and exits 3 |
| **Destructive** (`worker shutdown`) | shuts down a worker — it will not auto-restart | requires BOTH `--confirm` AND `--destructive` |

Default for Claude: **only run reads**. For writes: surface the plan to the user, confirm intent, and either pass `--confirm` yourself *if the user explicitly asked to mutate*, or hand them the previewed curl command to run. Mirror cade-db-queries' spirit — surface findings, let the user pull the trigger on production changes.

## Flag placement

Global flags (`--confirm`, `--destructive`, `--timeout`, `--raw`) come **before** the subcommand:

```bash
# correct
python3 <skill-dir>/scripts/flower.py --confirm task revoke <id> --terminate
python3 <skill-dir>/scripts/flower.py --raw healthcheck

# WRONG — argparse will reject
python3 <skill-dir>/scripts/flower.py task revoke <id> --terminate --confirm
```

## Common questions → commands

All examples assume you are inside the `cade-service` repo. `$TID` is a Celery task UUID.

```bash
# --- task inspection (READ) -------------------------------------------------
# status + timing (projected fields)
python3 <skill-dir>/scripts/flower.py task status   $TID
# args + kwargs (Python repr → parsed back to native types)
python3 <skill-dir>/scripts/flower.py task payload  $TID
# Celery retry count (note: this is Celery's retry, not CADE's 4-layer retry)
python3 <skill-dir>/scripts/flower.py task retries  $TID
# exception + traceback (null unless state is FAILURE)
python3 <skill-dir>/scripts/flower.py task error    $TID
# full info dict — use when the projections above aren't enough
python3 <skill-dir>/scripts/flower.py task info     $TID
# result from the result backend (503 if backend disabled)
python3 <skill-dir>/scripts/flower.py task result   $TID --result-timeout 5

# --- filtered task list (READ) ----------------------------------------------
# DEFAULT is full records (~25KB/task — tracebacks + args repr). For >3 tasks,
# always add --brief; for "how many?" questions, use --count-only. See
# "Handling large task list output" below.
python3 <skill-dir>/scripts/flower.py task list --count-only --state FAILURE
python3 <skill-dir>/scripts/flower.py task list --brief --state FAILURE --limit 50
python3 <skill-dir>/scripts/flower.py task list --brief --workername 'keywords@492e16d64f4a' --limit 50
python3 <skill-dir>/scripts/flower.py task list --brief --taskname app.workers.keyword_worker.generate_keywords
python3 <skill-dir>/scripts/flower.py task list --brief --state STARTED  # currently running
python3 <skill-dir>/scripts/flower.py task list --brief --received-start '2026-04-18 00:00' --received-end '2026-04-18 23:59'
python3 <skill-dir>/scripts/flower.py task list --brief --search some-domain.com
# Full-fat records (rare — only when you need args/traceback for every task and
# you're redirecting to a file for downstream processing, not inlining):
python3 <skill-dir>/scripts/flower.py task list --state FAILURE --limit 20 > /tmp/failures.json

# --- workers / queues (READ) -----------------------------------------------
python3 <skill-dir>/scripts/flower.py workers --status-only  # {name: alive_bool}
python3 <skill-dir>/scripts/flower.py workers --workername 'keywords@...'  # full stats
python3 <skill-dir>/scripts/flower.py queues                  # broker queue depths
python3 <skill-dir>/scripts/flower.py task types              # seen task type names

# --- task control (WRITE — needs --confirm) --------------------------------
# revoke before it starts (no-op if already running without --terminate)
python3 <skill-dir>/scripts/flower.py --confirm task revoke $TID
# kill a running task (SIGTERM; use SIGKILL to force)
python3 <skill-dir>/scripts/flower.py --confirm task revoke $TID --terminate
python3 <skill-dir>/scripts/flower.py --confirm task revoke $TID --terminate --signal SIGKILL
# resubmit (composite: fetch info, parse args/kwargs, send-task by name)
python3 <skill-dir>/scripts/flower.py --confirm task retry  $TID
# fire a new task by name with explicit args/kwargs
python3 <skill-dir>/scripts/flower.py --confirm task send \
    app.workers.keyword_worker.generate_keywords \
    --args  '[42]' \
    --kwargs '{"force_refresh": true}'

# --- worker control (WRITE) ------------------------------------------------
python3 <skill-dir>/scripts/flower.py --confirm worker pool-grow    'keywords@...' --n 2
python3 <skill-dir>/scripts/flower.py --confirm worker pool-shrink  'keywords@...' --n 1
python3 <skill-dir>/scripts/flower.py --confirm worker pool-restart 'keywords@...'
python3 <skill-dir>/scripts/flower.py --confirm worker pool-autoscale 'keywords@...' --min 2 --max 8
python3 <skill-dir>/scripts/flower.py --confirm worker add-consumer    'keywords@...' --queue keywords
python3 <skill-dir>/scripts/flower.py --confirm worker cancel-consumer 'keywords@...' --queue keywords

# --- per-task-type limits (WRITE) ------------------------------------------
python3 <skill-dir>/scripts/flower.py --confirm task timeout   \
    app.workers.keyword_worker.generate_keywords --workername 'keywords@...' --soft 900 --hard 1800
python3 <skill-dir>/scripts/flower.py --confirm task ratelimit \
    app.workers.keyword_worker.generate_keywords --workername 'keywords@...' --ratelimit 100/m

# --- DESTRUCTIVE (needs --confirm --destructive) ---------------------------
python3 <skill-dir>/scripts/flower.py --confirm --destructive worker shutdown 'keywords@...'

# --- monitor (READ, no auth) -----------------------------------------------
python3 <skill-dir>/scripts/flower.py --raw healthcheck
python3 <skill-dir>/scripts/flower.py metrics | head -50
```

## Things the wrapper is smart about

- **Auth**: HTTP Basic Auth via `Authorization: Basic <user:pass>`. Credentials never appear on the command line, never in logs — read from the settings file and sent as a header.
- **URL scheme**: `url` from settings can be bare host (`seo-acg-flower.prod.seosara.ai`) — scheme defaults to `https://`. Full scheme URLs pass through untouched.
- **Preview-before-mutate**: without `--confirm`, any write endpoint prints the equivalent curl command (with the password redacted to `<password>`) and exits `3`. No network call is made.
- **Projections**: `task status / payload / retries / error` hit `/api/task/info/{id}` once and project the fields you asked for — the full dict is ~30 fields and mostly noise for a given question. `task list` has matching projections: `--count-only` (just `{"count": N}`) and `--brief` (per-task ~500B record with ISO timestamps + first line of the exception).
- **`args` / `kwargs` repr parsing**: Flower stores these as Python repr strings (`"[3, 4]"`, `"{'force': True}"`) — `task payload` and `task retry` run `ast.literal_eval` to give you native types back. See `references/api.md > Gotchas`.
- **`task retry`** is a composite: `GET /api/task/info/{id}` → extract `name` + `args` + `kwargs` → `POST /api/task/send-task/{name}`. Flower has no native retry endpoint.
- **Worker names have `@`** — urlencoded into the path so you can pass them literally (`--workername 'keywords@492e16d64f4a'`).

## Handling large `task list` output

Flower's `/api/tasks` returns **every field** for every task — including `traceback` (~800B) and `args`/`kwargs` as Python repr strings (often 1KB+ each). A plain `task list --limit 50` can exceed 100KB; Claude Code's Bash tool persists oversized output to a file, and that file routinely exceeds `Read`'s 25k-token ceiling. You cannot inspect it directly, only process it. So: **compress server-side or at the pipe, not on the way back in**.

Pick the smallest mode that answers the question:

| Question | Mode | Bytes per task | When |
|---|---|---|---|
| "How many tasks match X?" | `--count-only` | 0 | counts, rate checks, dashboards |
| "Show me the matching tasks" | `--brief` | ~500 B | >3 tasks; any listing you plan to read |
| "Dump everything for one task" | `task info $TID` | full | single task, investigation |
| "Bulk analysis: parse args, extract domain, classify exception, aggregate" | pipe raw JSON to a Python script in the same Bash call | full, in-memory | >10 tasks AND you need args/traceback per task |

**Do not**: run plain `task list --limit 50` inline. The output balloons, the harness persists it, and you've now burned a round-trip producing a file you can barely read.

**Bulk-analysis pattern** (when `--brief` isn't enough because you need parsed `args` or full tracebacks):

⚠️ **Never pipe into `python3 - <<'HEREDOC'`.** The `-` tells Python to read the *script* from stdin, so the pipe and the heredoc both get concatenated into the script. JSON's `null` / `true` / `false` tokens then raise `NameError` when Python tries to eval them as source. Always use `python3 -c '...'` so stdin stays free for `json.load`:

```bash
# Generate + consume the full JSON inside a single command; process in-memory,
# emit only a condensed summary. Never rely on reading the persisted file.
# Quoting note: outer shell quotes are single ('), so use only " inside the
# Python script. Avoid f-strings (which want mixed quotes); use .format() or
# %-formatting instead. This keeps the snippet copy-pasteable.
python3 <skill-dir>/scripts/flower.py task list \
    --state FAILURE --taskname app.workers.content_worker.generate_content --limit 50 \
  | python3 -c '
import ast, json, sys

def parse_args(raw):
    # Flower serves args as Python repr. Guard broadly — some records slip
    # through with JSON-style null/true/false that literal_eval rejects.
    # Mirror the wrapper _parse_repr() defensiveness.
    if not raw:
        return ()
    try:
        return ast.literal_eval(raw)
    except Exception:
        return ()

data = json.load(sys.stdin)
for tid, rec in data.items():
    args = parse_args(rec.get("args"))
    payload = args[0] if args and isinstance(args[0], dict) else {}
    domain = payload.get("domain", "?")
    exc = (rec.get("exception") or "")[:80]
    print("{}  {:30}  {}".format(tid[:8], domain, exc))
'
```

If the pipe output *itself* is too large, write the condensed summary to a file under `misc/` and have the next step read that — never the raw persisted blob.

## What this skill is NOT

- **Not a database client** — rows in `domain_content`, `author`, `platform_connection` etc. belong to `cade-db-queries`.
- **Not a log reader** — traces and log events are Logfire. Flower knows what's *now*; Logfire knows what *happened*.
- **Not a Celery ingress** — CADE's workers are started by docker/systemd, not by this skill. `worker shutdown` is for taking a worker *offline*; there is no "bring it back online" here.
- **Not a workflow orchestrator** — it's a thin wrapper over Flower. A prod investigation may *call* it alongside db-queries and Logfire, but the orchestration lives above.

## When Flower says "404 Unknown task"

Doesn't mean the task never existed. Flower only keeps the last `--max_tasks` (10k default) in memory. For tasks older than that:

1. Try the result backend: `task result $TID --result-timeout 0` — requires the backend to have retained the result.
2. Fall back to `cade-db-queries` — most CADE task outcomes are persisted to domain/content/job rows.
3. Fall back to Logfire — spans for worker runs persist longer than Flower's window.

## Known worker pools

CADE runs five worker pool processes — hostnames are container IDs, but the prefix is stable:

| Pool prefix | Purpose | Primary queues |
|---|---|---|
| `categorizer@...` | domain classification | `categorizer` |
| `content@...` | article generation + publish | `content` |
| `crawler@...` | page discovery + scraping | `crawler` |
| `keywords@...` | SEO keyword pipeline | `keywords` |
| `publisher@...` | platform publishing (WP etc.) | `publisher` |

Pass the full hostname (including `@<container-id>`) when targeting a specific worker. `workers --status-only` gives you the current names.
