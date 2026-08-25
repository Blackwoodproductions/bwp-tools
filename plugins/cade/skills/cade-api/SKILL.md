---
name: cade-api
description: Call CADE FastAPI endpoints against prod (default), staging, or local from the repo. Builds request payloads, attaches the right auth header, enforces client-side write gating (reads free, POST/PUT/PATCH require --confirm, DELETE requires --confirm --destructive), and persists every run to `.claude/cade-api-run/[timestamp]-[endpoint].json` as `{"inputs": {...}, "outputs": {...}}`. Use this skill whenever the user asks to hit a CADE endpoint, trigger a task via the API, fire a keywords/content/crawl/publishing request against prod, re-run a past API call, inspect which endpoints exist, or directly invokes "cade-api". Triggers on phrases like "call the API", "hit the /keywords endpoint", "POST to prod", "trigger content generation via the API", "fire the crawler endpoint", "call the publishing endpoint", "hit the domain context endpoint in prod", "what endpoints are available", "list cade endpoints", "replay that API call", "send this payload to the API", "run the endpoint against staging". This is a live production write surface — always honor the gating rather than bypass it. For DB row state use `cade-db-queries`; for live Celery state use `cade-flower`; for telemetry use the `logfire` MCP tools (bwp-core). This skill owns the HTTP surface.
---

# cade-api

> **Paths:** `<skill-dir>` is this skill's directory — Claude Code prints it as *Base directory for this skill* when the skill loads (under `~/.claude/plugins/cache/bwp-tools/cade/<version>/skills/<name>`). Run commands from the **cade-service repo root** so `venv/bin/python`, `.claude/skills.settings.{env}.json` and `misc/` resolve.

HTTP client for the CADE FastAPI service. One job: construct a well-formed request, send it to the right environment with the right auth header, and save a reproducible record of inputs and outputs.

- **Wrapper**: `scripts/call_api.py` — builds the request, enforces write-gating, persists the run.
- **Endpoint enumeration**: `scripts/list_endpoints.py` — reads the live OpenAPI spec (cached) and prints method/path/summary.
- **Endpoint catalog**: `references/endpoints.md` — curated grouping of common endpoints by area (domains, keywords, content, publishing, tasks, scheduling, subscription). Load when the user's request maps to one of those areas.
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
- `wp-plugin-api-key` — required for `/subscription/*` endpoints (goes in `X-WordPress-Plugin-Key`). The wrapper picks the right one automatically; override with `--auth api|plugin` if you need to.

## When to use

Trigger on any "I want to hit a CADE API endpoint" request. Examples:

- "Call POST /keywords for example.com in prod"
- "Trigger content generation via the API against staging"
- "Hit /content/status for task `abc123`"
- "POST to `/publication/schedule` with this body"
- "List the keywords endpoints"
- "Reject these keyword IDs via the API"
- "Re-run yesterday's call to `/crawler`"

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

- Paths under `/api/v1/subscription/*` → `X-WordPress-Plugin-Key` (value from `wp-plugin-api-key`).
- Everything else → `X-API-Key` (value from `api-key`).
- Override either with `--auth api` or `--auth plugin` when the heuristic is wrong.

## Run log

Every non-`--dry-run` call writes `.claude/cade-api-run/[YYYYMMDDTHHMMSSZ]-[endpoint-slug].json`:

```json
{
  "inputs": {
    "method": "POST",
    "path": "/api/v1/keywords",
    "url": "https://seo-acg-api.prod.seosara.ai/api/v1/keywords",
    "query": {},
    "body": { "domain": "example.com", "request_id": "..." },
    "env": "prod",
    "base_url": "https://seo-acg-api.prod.seosara.ai",
    "auth": "api",
    "headers": { "Accept": "application/json", "X-API-Key": "***" },
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
python <skill-dir>/scripts/call_api.py GET /keywords/domain/example.com

# GET with query params
python <skill-dir>/scripts/call_api.py GET /keywords/domain/example.com \
  --query '{"status":"unused","page":1}'

# POST — requires --confirm
python <skill-dir>/scripts/call_api.py POST /keywords \
  --body '{"domain":"example.com","request_id":"abc","user_id":"u1"}' \
  --confirm

# POST with a body from file (keeps huge payloads tidy)
python <skill-dir>/scripts/call_api.py POST /publication/schedule \
  --body-file payload.json --confirm

# DELETE — requires --confirm --destructive
python <skill-dir>/scripts/call_api.py DELETE /keywords/<keyword_id> \
  --confirm --destructive

# Target staging or local
python <skill-dir>/scripts/call_api.py GET /system/health --env stg
python <skill-dir>/scripts/call_api.py GET /system/health --env local

# Subscription endpoints auto-use the plugin key
python <skill-dir>/scripts/call_api.py POST /subscription/activate \
  --body '{"domain":"..."}' --confirm

# Dry-run — print the request *as it would be sent*, don't hit the network
python <skill-dir>/scripts/call_api.py POST /keywords \
  --body '{"domain":"example.com"}' --confirm --dry-run
```

### Enumerate what's available

```bash
# Full list, prod
python <skill-dir>/scripts/list_endpoints.py

# Filter by path substring
python <skill-dir>/scripts/list_endpoints.py --filter keywords

# Filter by method
python <skill-dir>/scripts/list_endpoints.py --method POST

# Dump the raw OpenAPI operations for a path (useful when you need the request schema)
python <skill-dir>/scripts/list_endpoints.py --json /api/v1/keywords

# Force a fresh fetch (by default the spec is cached at <skill-dir>/.cache/)
python <skill-dir>/scripts/list_endpoints.py --refresh
```

## Calling etiquette

You're talking to prod (by default). Be polite:

- **Inspect before firing.** Run `list_endpoints.py --filter <area>` and `--json <path>` to see the exact request schema before guessing a payload.
- **Prefer `--dry-run` first** for any `POST`/`PUT`/`PATCH`/`DELETE`. It prints the exact request body *as encoded*, so you can eyeball IDs, flags, and typos without hitting the network.
- **One endpoint per call.** Don't script a workflow of 10 API calls from the skill — each deserves its own run log entry.
- **Poll status endpoints, don't wait silently.** `POST`s on the task routes return a `task_id` + `status_url`. Hit the `status_url` (that's another `GET` call) to track progress; don't assume the job ran just because you got `202`.
- **Mask sensitive output.** The wrapper masks API keys in the input log, not in the body of API responses. If a response contains a user's email, credential, or token, summarize rather than paste verbatim.
- **Aggregate before listing.** Prefer `?page_size=20` over pulling 500 rows just to count them — for that, use `cade-db-queries`.

## What this skill is NOT

- **Not a DB client** — for row state, use `cade-db-queries`.
- **Not a Celery client** — for task state / worker health, use `cade-flower`.
- **Not a Logfire client** — for telemetry / traces / exceptions, use the `logfire` MCP tools from the `bwp-core` plugin (`mcp__plugin_bwp-core_logfire__query_run`, `query_schema_reference`, `issue_list`).
- **Not a workflow orchestrator** — higher-level investigations may *call* this skill alongside the others; that orchestration belongs to the caller, not here.
- **Not a source reader** — to understand endpoint behavior, `Read`/`Grep` `app/api/v1/endpoints/`. This skill *calls* the API; it doesn't interpret it.
- **Not a silent bypass of write-gating.** If a call needs `--confirm`, add it on purpose. Don't wrap this script in something that sets it unconditionally.
