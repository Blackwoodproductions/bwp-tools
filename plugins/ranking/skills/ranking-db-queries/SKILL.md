---
name: ranking-db-queries
description: Run safe, read-only SQL queries against the Ranking Service production MariaDB (`bwp_ranking_service`, plus the external `bwp_seo` DB the auto-queue reads) through the bwp-core DBHub MCP server. Use this skill whenever the user asks to query the prod ranking database, look up records, check the state of specific rows, count or aggregate data, run ad-hoc SELECTs, audit data, or directly invokes "ranking-db-queries". Triggers on phrases like "query the prod ranking db", "how many reports are PENDING", "show me failed serp tasks", "what's the score history for keyword X", "why wasn't domain X auto-queued", "select from serp_reports", "audit DataForSEO costs", "show me rows where". The schema is owned by the ranking-service repo — `app/models/*.py` + Alembic migrations are the authoritative column reference (external bwp_seo tables: `app/models/external/*.py`). Four-layer read-only safety contract (MariaDB role + READ ONLY transaction + DBHub SQL classifier + timeout). Read-only by construction — never mutates production. For source-code investigation use Read/Grep; this skill is one input to investigations, not their orchestrator.
---

# ranking-db-queries

Read-only SQL access to the Ranking Service production MariaDB. One job, done safely.

- **Tools** — provided by the `bwp-core` plugin's `dbhub` MCP server, source id `ranking` (full ids: `mcp__plugin_bwp-core_dbhub__<tool>`):
  - `execute_sql_ranking` `{ "sql": "..." }` — read-only, 30 s timeout, no row cap.
  - `search_objects_ranking` `{ "object_type": "table|view|column|index", "pattern": "serp%", "detail_level": "names|summary|full", "table": "..." }` — progressive disclosure: `names` first, `full` only for the one table you need.
  - `explain_sql_ranking` `{ "sql": "..." }` — returns the plan **without executing** (replaces the old `--explain`).
- **Schema reference**: `references/schema.md` — table catalog for `bwp_ranking_service` and the relevant `bwp_seo` tables, verified against live prod. Load when you need column-level detail.
- **Query recipes**: `references/recipes.md` — read-only SELECTs grouped by area (domains/keywords, pipeline health, failures, costs, scores, auto-queue). Load when the user's request matches a common pattern — copy a recipe and narrow the `WHERE` rather than starting from scratch.
- **Connection**: the `bwp-core` plugin points at the hosted DBHub gateway (`https://dbhub.imagehosting.space/mcp`); its read-only token ships with the plugin, so there is nothing to configure per user or per call.

## Database identity

Two databases on the same production MariaDB server; the `ranking` source defaults to the first:

1. **`bwp_ranking_service`** (default) — this service's own DB: 5 tables (`domain`, `keywords`, `serp_reports`, `serp_tasks`, `serp_rank_scores`) plus `alembic_version`. The schema is owned by the ranking-service repo: `app/models/*.py` + `app/core/enums.py` + Alembic migrations are the authoritative column reference.
2. **`bwp_seo`** — the external legacy DB the auto-queue reads (~52 tables). Reach it with **qualified names** (`bwp_seo.bwp_domains`) — there is no `--db` switch any more. The 5 tables this service touches are modeled read-only in `app/models/external/*.py`: `bwp_domains`, `bwp_domain_settings`, `bwp_services`, `bwp_bubblefeed`, `bwp_bubblefeedsupport`. For anything else, use live discovery (`SHOW COLUMNS FROM bwp_seo.<table>`).

**Enum casing gotcha:** enum columns store the UPPERCASE Python enum *names*, not the lowercase values — in raw SQL write `WHERE status = 'PENDING'`, never `'pending'`. See `references/schema.md`.

## Environment selection

**The MCP connection is prod only.** If the user says "staging" / "stg" / "local" / "dev", do **not** run it against the `ranking` source — tell the user non-prod isn't wired to this skill.

## When to use

Trigger on any "look at the prod ranking data" request. Examples:

- "How many reports are stuck in IN_PROCESS?"
- "Show me the FAILED serp_tasks for report X and their failure reasons"
- "What did we spend on DataForSEO yesterday?"
- "Score history for keyword 'plumber near me' on example.com"
- "Why wasn't example.com auto-queued?" (→ `bwp_seo` eligibility chain)
- "Run this SELECT on the prod ranking db"

If the user wants source-code investigation, that lives in `Read` / `Grep` and `code-explorer`. This skill may be one *input* into a larger investigation — but it is not the orchestrator.

## Safety contract — read-only by construction

Four layers. Any single layer being bypassed leaves the others standing:

1. **MariaDB role** — the read source connects as `claude_readonly` (`SELECT` only). Operator setup snippet below.
2. **READ ONLY transaction** — DBHub runs every `execute_sql_ranking` inside `START TRANSACTION READ ONLY; ...; ROLLBACK`. MariaDB rejects writes server-side with error `1792`.
3. **SQL classifier** — DBHub strips comments and string literals, allows only statements starting with `SELECT / WITH / EXPLAIN / SHOW / DESCRIBE / DESC`, and rejects any mutating keyword (`INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|MERGE|GRANT|REVOKE|RENAME|REPLACE INTO`) anywhere in the body — including inside CTEs. On MariaDB, DDL implicitly commits and is stopped **only** by this layer, so layer 1 matters.
4. **Timeout** — `query_timeout = 30` s. No row cap.

If a call is rejected, that's the guardrail doing its job — rephrase, don't try to bypass.

### Server side (operator)

The `claude_readonly` / `claude_rw` DB users, their secrets and the DBHub deploy live in `seo-money-deployments` — see `docs/setup-dbhub-tutorial.md` there.

## How to query

```jsonc
// Simple query — always carry a LIMIT (aggregate where you can)
execute_sql_ranking    { "sql": "SELECT status, COUNT(*) FROM serp_reports GROUP BY status" }

// External bwp_seo DB — qualify the table (auto-queue investigations)
execute_sql_ranking    { "sql": "SELECT id, domain_name, status, deleted, skipfeedchecker FROM bwp_seo.bwp_domains WHERE domain_name = 'example.com' LIMIT 1" }

// Plan only (never executes)
explain_sql_ranking    { "sql": "SELECT ... big join ..." }

// Discover columns of one table
search_objects_ranking { "object_type": "column", "table": "serp_tasks", "detail_level": "summary" }

// Wide row — project the columns instead of SELECT *
execute_sql_ranking    { "sql": "SELECT id, status, failure_reason, created_at FROM serp_tasks WHERE id = '<uuid>'" }
```

One statement per call. Results come back as JSON rows; relay them as a compact table.

## Updating rows (explicit request only)

Reads never mutate. Updates go through a **separate** tool, `execute_sql_ranking_rw`, provided by the `bwp-rw` plugin (Claude Code only — it prompts each person for their own token; Desktop/Cowork users have read access only). Rules:

1. Only when the user explicitly asks to change data. Never "fix" something you noticed while reading.
2. `SELECT` the exact rows first with `execute_sql_ranking` and show them to the user.
3. One `UPDATE … WHERE <primary key>` per call — never a bare `UPDATE`, never a range unless the user spelled it out. Relay the affected-row count back.
4. The DB user (`claude_rw`) can only `SELECT` and `UPDATE`: `INSERT`, `DELETE`, `TRUNCATE` and DDL are refused by the engine — don't try.
5. If `execute_sql_ranking_rw` isn't available, say so: the user installs `bwp-rw@bwp-tools` and enters their token (`/plugin` → bwp-rw → configuration).

```jsonc
execute_sql_ranking     { "sql": "SELECT id, status, failure_reason FROM serp_tasks WHERE id = '<uuid>'" }      // 1. look
execute_sql_ranking_rw  { "sql": "UPDATE serp_tasks SET status = 'PENDING' WHERE id = '<uuid>'" }      // 2. change, after confirmation
```

## Query etiquette

You're talking to prod. Be polite:

- **Inspect before guessing.** Open `app/models/*.py` (or `app/models/external/*.py` for bwp_seo), use `search_objects_ranking`, or fall back to `SHOW COLUMNS FROM <table>`. Don't fish.
- **Enum literals are UPPERCASE names** in SQL (`'PENDING'`, `'QUEUED'`, `'GOOGLE'`, `'ORGANIC'`) even though the Python enum values are lowercase.
- **Aggregate before listing.** `SELECT status, COUNT(*) FROM serp_tasks GROUP BY status` beats 1000 raw rows.
- **EXPLAIN first** on joins across the big tables (`serp_tasks` ~32K, `serp_rank_scores` ~22K are fine; `bwp_seo.bwp_log_page` ~6.2M and `bwp_seo.bwp_modification_log` ~1.1M are not) — `explain_sql_ranking` returns the plan without executing.
- **PII lives in bwp_seo**, not the ranking DB. Never `SELECT *` on `bwp_seo.bwp_register` or `bwp_seo.bwp_creditcardinfo`; project columns and mask emails/card data when relaying results.
- **One question per query.** Two simple queries the user can verify beat one clever join they have to reverse-engineer.

## Schema discovery snippets

When `references/schema.md` and `search_objects_ranking` don't have what you need (swap the `table_schema` literal for `bwp_seo` when exploring the external DB):

```sql
-- Tables in the database
SHOW TABLES;

-- Columns of a table (compact)
SHOW COLUMNS FROM serp_tasks;

-- Columns with full info
SELECT column_name, data_type, is_nullable, column_default
FROM information_schema.columns
WHERE table_schema = 'bwp_ranking_service'
  AND table_name = 'serp_tasks'
ORDER BY ordinal_position;

-- Index list for a table
SHOW INDEXES FROM serp_reports;

-- Approximate row count (information_schema, no scan)
SELECT table_name, table_rows
FROM information_schema.tables
WHERE table_schema = 'bwp_ranking_service'
ORDER BY table_rows DESC
LIMIT 25;

-- Foreign keys for a table
SELECT column_name, referenced_table_name, referenced_column_name
FROM information_schema.key_column_usage
WHERE table_schema = 'bwp_ranking_service'
  AND table_name = 'serp_tasks'
  AND referenced_table_name IS NOT NULL;
```

## What this skill is NOT

- **Not a CADE client** — for the CADE/sara content service, use the `cade-db-queries` skill (cade plugin).
- **Not the dashboard client** — for general `bwp_seo` questions, prefer the `seolocal-db-queries` skill (seolocal plugin); this skill only reaches into `bwp_seo` for auto-queue eligibility.
- **Not a code reader** — for source-level analysis, use `Read` / `Grep`.
- **Not an investigation orchestrator** — that's a higher-level workflow that may *call* this skill alongside others.
- **Not a general write path** — the only write is `UPDATE` through `execute_sql_ranking_rw` (see *Updating rows*); INSERT, DELETE and DDL are impossible by DB grant. Anything else the user runs themselves (or via Alembic).
