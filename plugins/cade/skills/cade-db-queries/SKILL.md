---
name: cade-db-queries
description: Run safe, read-only SQL queries against the CADE production PostgreSQL database through the bwp-core DBHub MCP server. Use this skill whenever the user asks to query the prod db, look up records in production, check the state of specific rows, count or aggregate data, run ad-hoc SELECTs, audit data, or directly invokes "cade-db-queries". Triggers on phrases like "query prod", "check the production database", "how many X are there in prod", "what's the state of Y in the db", "select from", "show me rows where", "audit the X table", "look up Z in the database". Four-layer read-only safety contract (DB role + READ ONLY transaction + DBHub SQL classifier + timeout/row cap) and a maintained schema reference covering every model in `app/models/`. Read-only by construction — never mutates production. For telemetry/log analysis or source-code investigation, use other tools — this skill is one input to those workflows, not their orchestrator.
---

# cade-db-queries

Read-only SQL access to the CADE production Postgres. One job, done safely.

- **Tools** — provided by the `bwp-core` plugin's `dbhub` MCP server, source id `cade` (full ids: `mcp__plugin_bwp-core_dbhub__<tool>`):
  - `execute_sql_cade` `{ "sql": "..." }` — read-only, 30 s timeout, hard cap 500 rows. **Add your own `LIMIT` (≤ 100 unless the user needs more)** — the cap is a backstop, not a default.
  - `search_objects_cade` `{ "object_type": "table|view|column|index|schema|function|procedure", "pattern": "%name%", "detail_level": "names|summary|full", "schema": "public", "table": "..." }` — progressive disclosure: `names` first, `full` only for the one table you need.
  - `explain_sql_cade` `{ "sql": "..." }` — returns the plan **without executing** (replaces the old `--explain`).
- **Schema reference**: `references/schema.md` — full table-by-table breakdown. Load when you need column-level detail.
- **Query recipes**: `references/recipes.md` — curated catalog of read-only queries by area (domains, keywords, content, publishing, platform connections, jobs, scheduling, crawl, cross-cutting traces). Load when the user's request matches a common pattern — copy a recipe and narrow the `WHERE` rather than starting from scratch.
- **Connection**: the `bwp-core` plugin points at the hosted DBHub gateway (`https://dbhub.imagehosting.space/mcp`); its read-only token ships with the plugin, so there is nothing to configure per user or per call. Search path is `public, cade_scheduling`; qualify other schemas explicitly.

## Environment selection

**The MCP connection is prod only.** If the user says "staging" / "stg" / "local" / "dev", do **not** run it against the `cade` source. Use the repo-local wrapper if the checkout still has it (`python <skill-dir>/scripts/prod_query.py --env stg "..."` reads `.claude/skills.settings.stg.json`); otherwise tell the user non-prod isn't wired.

## When to use

Trigger on any "I want to look at production data" request. Examples:

- "Query the prod db: how many domains have status = ACTIVE?"
- "Show me the last 20 jobs for domain X"
- "Audit the platform_connections table — how many are stale?"
- "Look up the row in domain_content where keyword_id = ..."
- "Run this SELECT on prod"

If the user wants telemetry, log analysis, or code reading, this skill may be one *input* to that workflow — but the orchestration belongs elsewhere. Don't try to do those things from inside this skill.

## Safety contract — read-only by construction

Four layers. Any single layer being bypassed leaves the others standing:

1. **Postgres role** — the read source connects as `claude_readonly` (`SELECT` only). Operator setup snippet below.
2. **Read-only transaction** — DBHub runs every `execute_sql_cade` inside `BEGIN READ ONLY; ...; ROLLBACK`. Postgres rejects writes server-side.
3. **SQL classifier** — DBHub strips comments and string literals, allows only statements starting with `SELECT / WITH / EXPLAIN / SHOW`, and rejects any mutating keyword (`INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|MERGE|GRANT|REVOKE|RENAME`) anywhere in the body — including inside CTEs — with "Read-only mode is enabled".
4. **Timeout + row cap** — `query_timeout = 30` s, `max_rows = 500`. A capped result carries `"truncated": true`; run `COUNT(*)` for the true total instead of raising the cap.

If a call is rejected, that's the guardrail doing its job — rephrase, don't try to bypass. Caveat: a read-only transaction does not constrain privileged-role *functions* (`pg_read_file`, `lo_export`, `dblink`, `COPY ... TO PROGRAM`) — which is why layer 1 matters.

### First-time setup (operator)

Roles live server-side: the read path uses `claude_readonly`, the update path `claude_rw` (`SELECT, UPDATE` only). Their DSNs are the `DBHUB_CADE_RO_DSN` / `DBHUB_CADE_RW_DSN` secrets in `seo-money-deployments` (see `docs/setup-dbhub-tutorial.md` there). Create the read role with:

```sql
CREATE ROLE claude_readonly LOGIN PASSWORD '<strong-random>';
GRANT CONNECT ON DATABASE "seo-acg" TO claude_readonly;
GRANT USAGE ON SCHEMA public, cade_scheduling TO claude_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO claude_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA cade_scheduling TO claude_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT ON TABLES TO claude_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA cade_scheduling
  GRANT SELECT ON TABLES TO claude_readonly;
```

Add `claude_rw` the same way with `GRANT SELECT, UPDATE` instead of `GRANT SELECT` — nothing else, so INSERT/DELETE/TRUNCATE/DDL stay impossible.

## How to query

```jsonc
// Simple query — always carry a LIMIT
execute_sql_cade   { "sql": "SELECT id, status FROM domains ORDER BY created_at DESC LIMIT 20" }

// Aggregate before listing
execute_sql_cade   { "sql": "SELECT status, count(*) FROM domain_content GROUP BY status" }

// Plan only (never executes)
explain_sql_cade   { "sql": "SELECT ... big join ..." }

// Discover columns of one table
search_objects_cade { "object_type": "column", "schema": "public", "table": "domain_content", "detail_level": "summary" }

// Another schema — qualify it
execute_sql_cade   { "sql": "SELECT * FROM cade_scheduling.schedule LIMIT 5" }
```

One statement per call. Results come back as JSON rows; relay them as a compact table.

## Updating rows (explicit request only)

Reads never mutate. Updates go through a **separate** tool, `execute_sql_cade_rw`, provided by the `bwp-rw` plugin (Claude Code only — it prompts each person for their own token; Desktop/Cowork users have read access only). Rules:

1. Only when the user explicitly asks to change data. Never "fix" something you noticed while reading.
2. `SELECT` the exact rows first with `execute_sql_cade` and show them to the user.
3. One `UPDATE … WHERE <primary key>` per call — never a bare `UPDATE`, never a range unless the user spelled it out. Relay the affected-row count back.
4. The DB user (`claude_rw`) can only `SELECT` and `UPDATE`: `INSERT`, `DELETE`, `TRUNCATE` and DDL are refused by the engine — don't try.
5. If `execute_sql_cade_rw` isn't available, say so: the user installs `bwp-rw@bwp-tools` and enters their token (`/plugin` → bwp-rw → configuration).

```jsonc
execute_sql_cade     { "sql": "SELECT id, status, updated_at FROM domains WHERE id = 123" }      // 1. look
execute_sql_cade_rw  { "sql": "UPDATE domains SET status = 'ACTIVE' WHERE id = 123" }      // 2. change, after confirmation
```

## Query etiquette

You're talking to prod. Be polite:

- **Inspect before guessing.** Use `references/schema.md` or `search_objects_cade` for column names; fall back to `information_schema.columns`. Don't fish.
- **Aggregate before listing.** `SELECT status, count(*) FROM x GROUP BY status` beats 1000 raw rows.
- **EXPLAIN first** on joins across large tables — `explain_sql_cade` returns the plan without executing.
- **Never `SELECT *`** on wide or PII-adjacent tables. Project the columns you actually need.
- **Mask sensitive data** (emails, tokens, API keys, encrypted blobs) when relaying results to the user. The server doesn't redact for you.
- **One question per query.** Two simple queries the user can verify beat one clever join they have to reverse-engineer.

## Schema discovery snippets

When `references/schema.md` and `search_objects_cade` don't have what you need (e.g. a system table):

```sql
-- Tables in a schema
SELECT table_name FROM information_schema.tables
WHERE table_schema='public' ORDER BY table_name;

-- Columns of a table
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema='public' AND table_name='<table>'
ORDER BY ordinal_position;

-- Index list for a table
SELECT indexname, indexdef FROM pg_indexes
WHERE schemaname='public' AND tablename='<table>';

-- Enum values
SELECT unnest(enum_range(NULL::<enum_type>));

-- Cheap row-count estimate (planner stats — no scan)
SELECT reltuples::bigint AS estimate
FROM pg_class WHERE relname = '<table>';
```

## What this skill is NOT

- **Not a Logfire client** — for telemetry, use the `logfire` MCP tools from `bwp-core` (`mcp__plugin_bwp-core_logfire__query_run`, `query_schema_reference`, `issue_list`, ...).
- **Not a Flower client** — for live Celery state, use the `cade-flower` skill.
- **Not a code reader** — for source-level analysis, use `Read` / `Grep`.
- **Not an investigation orchestrator** — that's a higher-level workflow that may *call* this skill alongside others.
- **Not a general write path** — the only write is `UPDATE` through `execute_sql_cade_rw` (see *Updating rows*); INSERT, DELETE and DDL are impossible by DB grant. Anything else the user runs themselves.
