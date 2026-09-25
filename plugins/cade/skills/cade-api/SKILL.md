---
name: cade-api
description: Call CADE FastAPI endpoints against prod (default), staging, or local from the repo. Builds request payloads, attaches the right auth header, enforces client-side write gating (reads free, POST/PUT/PATCH require --confirm, DELETE requires --confirm --destructive), and persists every run to `.claude/cade-api-run/[timestamp]-[endpoint].json` as `{"inputs": {...}, "outputs": {...}}`. Use this skill whenever the user asks to hit a CADE endpoint, trigger a task via the API, fire a keywords/content/crawl/publishing request against prod, re-run a past API call, run a BRON cutover stage or check its record, read/retract/delete a publication by keyword, inspect which endpoints exist, or directly invokes "cade-api". Triggers on phrases like "call the API", "hit the /keywords endpoint", "POST to prod", "trigger content generation via the API", "fire the crawler endpoint", "call the publishing endpoint", "hit the domain context endpoint in prod", "what endpoints are available", "list cade endpoints", "replay that API call", "send this payload to the API", "run the endpoint against staging", "check the bron cutover", "verify the cutover", "get the publication by keyword". This is a live production write surface — always honor the gating rather than bypass it. For DB row state use `cade-db-queries`; for live Celery state use `cade-flower`; for telemetry use the `logfire` MCP tools (bwp-core). This skill owns the HTTP surface.
---

# cade-api

> **Paths:** `<skill-dir>` is this skill's directory — Claude Code prints it as *Base directory for this skill* when the skill loads (under `~/.claude/plugins/cache/bwp-tools/cade/<version>/skills/<name>`). Run commands from the **cade-service repo root** so `venv/bin/python`, `.claude/skills.settings.{env}.json` and `misc/` resolve.

HTTP client for the CADE FastAPI service. One job: construct a well-formed request, send it to the right environment with the right auth header, and save a reproducible record of inputs and outputs.

- **Wrapper**: `scripts/call_api.py` — builds the request, enforces write-gating, persists the run.
- **Endpoint enumeration**: `scripts/list_endpoints.py` — reads the live OpenAPI spec (cached) and prints method/path/summary.
- **Endpoint catalog**: `references/endpoints.md` — curated grouping of common endpoints by area (system/tasks, domain core, keywords, BRON cutover, publications, content, scheduling, subscription, platform, schema markup, reference) with *(write)*/*(slow)*/*(no auth)* markers. Load when the user's request maps to one of those areas.
- **Credentials**: read from `.claude/skills.settings.{env}.json` under the `cade-api` key (gitignored via `.claude/*`). Env defaults to `prod` — see *Environment selection* below.

## Environment selection

**Default env is `prod`.** The wrapper loads credentials and the base URL from `.claude/skills.settings.prod.json` unless an env is explicitly requested. Valid envs: `prod`, `stg`, `local`.

**Rule when invoking this skill:**

| User's query contains… | Env to use | Flag | Settings file |
|---|---|---|---|
| nothing about environment | `prod` | *(none — default)* | `.claude/skills.settings.prod.json` |
| "prod" / "production" | `prod` | `--env prod` (or none) | `.claude/skills.settings.prod.json` |
| "stg" / "staging" / "stage" | `stg` | `--env stg` | `.claude/skills.settings.stg.json` |
| "local" / "dev" / "development" | `local` | `--env local` | `.claude/skills.settings.local.json` |

For batch invocations (sub-agents, scripts) set `CADE_SKILL_ENV=stg` once instead of passing `--env` on every call.

If the selected env's settings file is missing, the wrapper fails fast with a clear error — it does **not** silently fall back to `prod`. Same contract as the other cade-* skills.

### Base URL resolution

The wrapper reads `api-url` from the settings block; if it's empty, it falls back to these defaults:

| Env | Default base URL |
|---|---|
| `prod` | `https://seo-acg-api.prod.seosara.ai` |
| `stg`  | `https://seo-acg-api.stg.seosara.ai`  |
| `local` | `http://localhost:8000` |

All CADE endpoints live under `/api/v1/`. The wrapper prefixes that for you — pass `/keywords` and it sends to `/api/v1/keywords`.

### Settings file shape

Top-level keys are siblings, one per skill. This skill reads only its own block:

```json
{
  "cade-api": {
    "api-url": "",
    "api-key": "<X-API-Key value>",
    "wp-plugin-api-key": "<X-WordPress-Plugin-Key value>"
  },
  "cade-db-queries": { "...": "..." },
  "cade-flower": { "...": "..." }
}
```

- `api-url` — optional; **bare host only** (`https://seo-acg-api.prod.seosara.ai`). Do **not** include `/api/v1` — the wrapper adds it. Trailing `/api/v1` / `/api` / `/` is stripped defensively, but keep the value clean. Fall back to env defaults above when empty.
- `api-key` — required for ~all endpoints (goes in `X-API-Key` header).
- `wp-plugin-api-key` — optional; only sent with `--auth plugin` (goes in `X-WordPress-Plugin-Key`). Every route accepts `X-API-Key`, so you rarely need it.

## When to use

Trigger on any "I want to hit a CADE API endpoint" request. Examples:

- "Call POST /domains/example.com/keywords in prod"
- "Trigger content generation via the API against staging"
- "What's the status of task `abc123`" → `GET /tasks/abc123`
- "Run the `prepare` cutover stage for example.com" / "verify the cutover"
- "Retract the BRON article for keyword 42"
- "List the keywords endpoints"
- "Re-run yesterday's crawl call"

If the user wants DB row state, use `cade-db-queries`. Live Celery task state belongs to `cade-flower`. Telemetry/traces live in the `logfire` MCP tools (bwp-core). The API is *your* surface only.

## Safety contract — write-gating by method

Three tiers. The API does not know the difference — this is client-side discipline.

| Method | Runs by default? | Extra flag required |
|---|---|---|
| `GET`, `HEAD`, `OPTIONS` | yes | *(none)* |
| `POST`, `PUT`, `PATCH` | no | `--confirm` |
| `DELETE` | no | `--confirm --destructive` |

Why: prod `POST`s dispatch Celery jobs (content generation, crawling, publishing) that are hard to unwind; prod `DELETE`s cascade through foreign keys. Force yourself — and anyone else re-running from a saved run log — to pause and think.

If the wrapper rejects a call, that's the wrapper doing its job. Add the flag only once you've confirmed the intent; don't paper over.

### Auth routing

- Default → `X-API-Key` (value from `api-key`). Every route accepts it, including the plugin-key routes (`/domains/{d}/subscription*`, `/domains/{d}/publication-sync`).
- `--auth plugin` → `X-WordPress-Plugin-Key` (value from `wp-plugin-api-key`), for testing the plugin-side auth path.
- Some routes have no auth at all (`/system/health`, `/reference/*`, `/domains/{d}/scheduled-tasks*`) — the header is sent anyway and ignored.

## Run log

Every non-`--dry-run` call writes `.claude/cade-api-run/[YYYYMMDDTHHMMSSZ]-[endpoint-slug].json`:

```json
{
  "inputs": {
    "method": "POST",
    "path": "/api/v1/domains/example.com/keywords",
    "url": "https://seo-acg-api.prod.seosara.ai/api/v1/domains/example.com/keywords",
    "query": {},
    "body": { "request_id": "..." },
    "env": "prod",
    "base_url": "https://seo-acg-api.prod.seosara.ai",
    "auth": "api",
    "headers": { "Accept": "application/json", "X-API-Key": "***" },
    "timeout_s": null,
    "timestamp": "2026-04-20T14:30:22.123456+00:00"
  },
  "outputs": {
    "status_code": 202,
    "body": { "success": true, "data": { "task_id": "...", "status": "PENDING" } },
    "duration_ms": 412,
    "response_headers": { "content-type": "application/json" },
    "error": null
  }
}
```

- API keys are always replaced with `***` in the log — safe to check in if needed, though the default is to keep them in the gitignored `.claude/` tree.
- The file name encodes the endpoint, so `ls .claude/cade-api-run/` is a chronological audit log.
- Re-running a past call is just: read the log, pass `inputs.body` back through `--body-file`.

## How to call

```bash
# Simple GET — no gating needed (default env=prod)
python <skill-dir>/scripts/call_api.py GET /domains/example.com/keywords

# GET with query params (lists repeat: {"status":["unused","used"]})
python <skill-dir>/scripts/call_api.py GET /domains/example.com/keywords \
  --query '{"status":["unused"],"page":1}'

# POST — requires --confirm
python <skill-dir>/scripts/call_api.py POST /domains/example.com/keywords \
  --body-file payload.json --confirm

# Poll the task a 202 handed back
python <skill-dir>/scripts/call_api.py GET /tasks/<task_id>

# BRON cutover: read the saved record, then queue a stage
python <skill-dir>/scripts/call_api.py GET /domains/example.com/bron/cutover/record
python <skill-dir>/scripts/call_api.py POST /domains/example.com/bron/cutover \
  --body '{"stage":"prepare","publish_status":"publish"}' --confirm

# Slow synchronous route — bound it yourself (see Timeouts)
python <skill-dir>/scripts/call_api.py GET /domains/example.com/bron/cutover/verification \
  --query '{"profile":"full"}' --timeout 540

# DELETE — requires --confirm --destructive
python <skill-dir>/scripts/call_api.py DELETE /keywords/<keyword_id> \
  --confirm --destructive

# Target staging or local
python <skill-dir>/scripts/call_api.py GET /system/health --env stg
python <skill-dir>/scripts/call_api.py GET /system/health --env local

# Dry-run — print the request *as it would be sent*, don't hit the network
python <skill-dir>/scripts/call_api.py POST /domains/example.com/keywords \
  --body-file payload.json --confirm --dry-run
```

### Enumerate what's available

```bash
# Full list, prod
python <skill-dir>/scripts/list_endpoints.py

# Filter by path substring
python <skill-dir>/scripts/list_endpoints.py --filter keywords

# Filter by method
python <skill-dir>/scripts/list_endpoints.py --method POST

# Dump the raw OpenAPI operations for a path (exact spec key, placeholders included)
python <skill-dir>/scripts/list_endpoints.py --json '/api/v1/domains/{domain}/keywords'

# Force a fresh fetch (by default the spec is cached at <skill-dir>/.cache/)
python <skill-dir>/scripts/list_endpoints.py --refresh
```

## Timeouts

The wrapper **never times out on its own** — it waits until the server answers. You decide how long a call may take:

- `--timeout N` (seconds, float) bounds the call; on expiry it exits `3` with the error in the run log. `inputs.timeout_s` records what was used (`null` = unbounded).
- The **Bash tool has its own limit** (120 s default, 600 s max). For a slow route either set the Bash call's `timeout` above the expected duration, or run it with `run_in_background: true` and read the run log / stdout when it finishes. Pair `--timeout` just under the Bash limit so the wrapper, not Bash, ends the call and still writes the log.
- Upstream, the openresty gateway cuts requests at ~90 s; a `504` there is the gateway, not the wrapper — the work may still be running server-side.
- Known slow synchronous routes (marked *(slow)* in `references/endpoints.md`): `POST /domains/{d}/publications/{id}/media` (the slowest), `GET /domains/{d}/bron/cutover`, `GET /domains/{d}/bron/cutover/verification`, `POST /sanity-checks`, `POST /domains/{d}/profile-pages`, and the `/domains/{d}/platform-content` operations. Everything that queues Celery work returns in seconds.

## Calling etiquette

You're talking to prod (by default). Be polite:

- **Inspect before firing.** Run `list_endpoints.py --filter <area>` and `--json <path>` to see the exact request schema before guessing a payload.
- **Prefer `--dry-run` first** for any `POST`/`PUT`/`PATCH`/`DELETE`. It prints the exact request body *as encoded*, so you can eyeball IDs, flags, and typos without hitting the network.
- **One endpoint per call.** Don't script a workflow of 10 API calls from the skill — each deserves its own run log entry.
- **Poll status endpoints, don't wait silently.** Queueing `POST`s return `202` + a `task_id`. Poll `GET /tasks/{task_id}` to track progress; don't assume the job ran just because you got `202`.
- **Mask sensitive output.** The wrapper masks API keys in the input log, not in the body of API responses. If a response contains a user's email, credential, or token, summarize rather than paste verbatim.
- **Aggregate before listing.** Prefer `?per_page=20` over pulling 500 rows just to count them — for that, use `cade-db-queries`.

## What this skill is NOT

- **Not a DB client** — for row state, use `cade-db-queries`.
- **Not a Celery client** — for task state / worker health, use `cade-flower`.
- **Not a Logfire client** — for telemetry / traces / exceptions, use the `logfire` MCP tools from the `bwp-core` plugin (`mcp__plugin_bwp-core_logfire__query_run`, `query_schema_reference`, `issue_list`).
- **Not a workflow orchestrator** — higher-level investigations may *call* this skill alongside the others; that orchestration belongs to the caller, not here.
- **Not a source reader** — to understand endpoint behavior, `Read`/`Grep` `app/api/v1/endpoints/`. This skill *calls* the API; it doesn't interpret it.
- **Not a silent bypass of write-gating.** If a call needs `--confirm`, add it on purpose. Don't wrap this script in something that sets it unconditionally.
