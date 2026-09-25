---
name: seolocal-db-queries
description: Run safe, read-only SQL queries against the SEO Local production MariaDB (`bwp_seo`) through the bwp-core DBHub MCP server. Use this skill whenever the user asks to query the prod SEO database, look up records, check the state of specific rows, count or aggregate data, run ad-hoc SELECTs, audit data, or directly invokes "seolocal-db-queries". Triggers on phrases like "query the seo db", "look up domain X in prod", "how many resellers are there", "what's the state of order Y in the SEO db", "select from bwp_domains", "show me rows where", "audit the bwp_register table". The Prisma schema in `packages/prisma/prisma/bwpSeo/schema.prisma` is the authoritative column reference. Four-layer read-only safety contract (MariaDB role + READ ONLY transaction + DBHub SQL classifier + timeout). Read-only by construction — never mutates production. For source-code investigation use Read/Grep; this skill is one input to investigations, not their orchestrator.
---

# seolocal-db-queries

Read-only SQL access to the SEO Local production MariaDB. One job, done safely.

- **Tools** — provided by the `bwp-core` plugin's `dbhub` MCP server, source id `seo` (full ids: `mcp__plugin_bwp-core_dbhub__<tool>`):
  - `execute_sql_seo` `{ "sql": "..." }` — read-only, 30 s timeout, no row cap.
  - `search_objects_seo` `{ "object_type": "table|view|column|index", "pattern": "bwp_dom%", "detail_level": "names|summary|full", "table": "..." }` — progressive disclosure: `names` first, `full` only for the one table you need.
  - `explain_sql_seo` `{ "sql": "..." }` — returns the plan **without executing** (replaces the old `--explain`).
- **Schema reference**: `references/schema.md` — pointer at the canonical Prisma schemas plus a curated table summary. Load when you need column-level detail.
- **Query recipes**: `references/recipes.md` — read-only SELECTs grouped by area (domains, register, resellers, orders, rank tracking). Load when the user's request matches a common pattern — copy a recipe and narrow the `WHERE` rather than starting from scratch.
- **Connection**: the `bwp-core` plugin points at the hosted DBHub gateway (`https://dbhub.imagehosting.space/mcp`); its read-only token ships with the plugin, so there is nothing to configure per user or per call.

## Database identity

The `seo` source connects to **`bwp_seo`** on the BWP production MariaDB server. The Prisma schema at `packages/prisma/prisma/bwpSeo/schema.prisma` is the authoritative column reference. Use camelCase Prisma names only when reading the schema file; in raw SQL always use the underlying snake_case names (e.g. `bwp_domains.domain_name`, not `bwpDomains.domainName`).

`bwp_ranking_service` lives on the same server — reach it with qualified names (`bwp_ranking_service.serp_reports`) or, better, use the `ranking-db-queries` skill / `execute_sql_ranking` from the ranking plugin.

The legacy tunneled instance (`freerele_blackwoodproductions`, schema-identical) is **not** wired via MCP; DBHub supports a per-source SSH tunnel if it's ever needed.

## Environment selection

**The MCP connection is prod only.** If the user says "staging" / "stg" / "local" / "dev", do **not** run it against the `seo` source — tell the user non-prod isn't wired to this skill.

## When to use

Trigger on any "look at the SEO data" request. Examples:

- "Query the prod db: how many active domains do we have?"
- "Show me the bwp_register row for user X"
- "Audit bwp_resellers — how many have paid = 0?"
- "Look up the domain settings for seolocal.it.com"
- "Run this SELECT on the seo db"

If the user wants source-code investigation, that lives in `Read` / `Grep` and `code-explorer`. This skill may be one *input* into a larger investigation — but it is not the orchestrator.

## Safety contract — read-only by construction

Four layers. Any single layer being bypassed leaves the others standing:

1. **MariaDB role** — the read source connects as `claude_readonly` (`SELECT` only). Operator setup snippet below.
2. **READ ONLY transaction** — DBHub runs every `execute_sql_seo` inside `START TRANSACTION READ ONLY; ...; ROLLBACK`. MariaDB rejects writes server-side with error `1792`.
3. **SQL classifier** — DBHub strips comments and string literals, allows only statements starting with `SELECT / WITH / EXPLAIN / SHOW / DESCRIBE / DESC`, and rejects any mutating keyword (`INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|MERGE|GRANT|REVOKE|RENAME|REPLACE INTO`) anywhere in the body — including inside CTEs. On MariaDB, DDL implicitly commits and is stopped **only** by this layer, so layer 1 matters.
4. **Timeout** — `query_timeout = 30` s. No row cap.

If a call is rejected, that's the guardrail doing its job — rephrase, don't try to bypass.

### Server side (operator)

The `claude_readonly` / `claude_rw` DB users, their secrets and the DBHub deploy live in `seo-money-deployments` — see `docs/setup-dbhub-tutorial.md` there.

## How to query

```jsonc
// Simple query — always carry a LIMIT
execute_sql_seo    { "sql": "SELECT id, domain_name, status FROM bwp_domains ORDER BY id DESC LIMIT 20" }

// Aggregate before listing
execute_sql_seo    { "sql": "SELECT status, count(*) FROM bwp_domains GROUP BY status" }

// Plan only (never executes)
explain_sql_seo    { "sql": "SELECT ... big join ..." }

// Discover columns of one table
search_objects_seo { "object_type": "column", "table": "bwp_register", "detail_level": "summary" }

// Cross-db on the same server — qualify the table
execute_sql_seo    { "sql": "SELECT id, status FROM bwp_ranking_service.serp_reports LIMIT 5" }
```

One statement per call. Results come back as JSON rows; relay them as a compact table.

## Updating rows (explicit request only)

Reads never mutate. Updates go through a **separate** tool, `execute_sql_seo_rw`, provided by the `bwp-rw` plugin (Claude Code only — it prompts each person for their own token; Desktop/Cowork users have read access only). Rules:

1. Only when the user explicitly asks to change data. Never "fix" something you noticed while reading.
2. `SELECT` the exact rows first with `execute_sql_seo` and show them to the user.
3. One `UPDATE … WHERE <primary key>` per call — never a bare `UPDATE`, never a range unless the user spelled it out. Relay the affected-row count back.
4. The DB user (`claude_rw`) can only `SELECT` and `UPDATE`: `INSERT`, `DELETE`, `TRUNCATE` and DDL are refused by the engine — don't try.
5. If `execute_sql_seo_rw` isn't available, say so: the user installs `bwp-rw@bwp-tools` and enters their token (`/plugin` → bwp-rw → configuration).

```jsonc
execute_sql_seo     { "sql": "SELECT id, domain_name, status FROM bwp_domains WHERE id = 123" }      // 1. look
execute_sql_seo_rw  { "sql": "UPDATE bwp_domains SET status = 1 WHERE id = 123" }      // 2. change, after confirmation
```

## Query etiquette

You're talking to prod. Be polite:

- **Inspect before guessing.** Open `packages/prisma/prisma/bwpSeo/schema.prisma` or use `search_objects_seo` for column names, or fall back to `SHOW COLUMNS FROM <table>`. Don't fish.
- **Aggregate before listing.** `SELECT status, count(*) FROM x GROUP BY status` beats 1000 raw rows.
- **EXPLAIN first** on joins across large tables (`bwp_log_*`, `bwp_rank_*`, `bwp_modification_log`) — `explain_sql_seo` returns the plan without executing.
- **Never `SELECT *`** on wide or PII-adjacent tables (`bwp_register`, `bwp_creditcardinfo`). Project the columns you actually need.
- **Mask sensitive data** (emails, credit-card snippets, passwords, API keys) when relaying results to the user. The server doesn't redact for you.
- **One question per query.** Two simple queries the user can verify beat one clever join they have to reverse-engineer.

## Schema discovery snippets

When `references/schema.md` and `search_objects_seo` don't have what you need:

```sql
-- Tables in the database
SHOW TABLES;

-- Columns of a table (compact)
SHOW COLUMNS FROM bwp_domains;

-- Columns with full info
SELECT column_name, data_type, is_nullable, column_default
FROM information_schema.columns
WHERE table_schema = 'bwp_seo'
  AND table_name = 'bwp_domains'
ORDER BY ordinal_position;

-- Index list for a table
SHOW INDEXES FROM bwp_domains;

-- Approximate row count (information_schema, no scan)
SELECT table_name, table_rows
FROM information_schema.tables
WHERE table_schema = 'bwp_seo'
ORDER BY table_rows DESC
LIMIT 25;

-- Foreign keys for a table
SELECT column_name, referenced_table_name, referenced_column_name
FROM information_schema.key_column_usage
WHERE table_schema = 'bwp_seo'
  AND table_name = 'bwp_domains'
  AND referenced_table_name IS NOT NULL;
```

## What this skill is NOT

- **Not a CADE client** — for the CADE/sara content service, use the `cade-db-queries` skill (cade plugin).
- **Not the ranking client** — for `bwp_ranking_service` questions, prefer the `ranking-db-queries` skill (ranking plugin).
- **Not a code reader** — for source-level analysis, use `Read` / `Grep`.
- **Not an investigation orchestrator** — that's a higher-level workflow that may *call* this skill alongside others.
- **Not a general write path** — the only write is `UPDATE` through `execute_sql_seo_rw` (see *Updating rows*); INSERT, DELETE and DDL are impossible by DB grant. Anything else the user runs themselves.
