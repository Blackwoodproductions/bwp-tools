# Ranking Service prod DB — schema reference

Detailed table reference for the `ranking-db-queries` skill. Load only when you need column-level detail — for a high-level question, the discovery snippets in `SKILL.md` are usually enough.

Verified against live prod (`information_schema`, MariaDB 10.5.29) on 2026-07-02.

## Canonical schema sources

- **`bwp_ranking_service`** — owned by this repo. Authoritative reference: `app/models/{domain,keyword,serp_report,serp_task,serp_rank_score}.py`, enums in `app/core/enums.py`, DDL history in `alembic/versions/`.
- **`bwp_seo`** — external legacy DB (managed upstream). The 5 tables this service reads are modeled read-only in `app/models/external/*.py`. The other ~47 tables are unmodeled — use `SHOW COLUMNS FROM <table>` or `information_schema.columns`.

## The enum-casing trap

SQLAlchemy stores the enum **names**, so the DB enum literals are UPPERCASE even though the Python enum *values* are lowercase:

| Python (`app/core/enums.py`) | SQL literal |
|---|---|
| `ReportStatus.PENDING = "pending"` | `'PENDING'` |
| `TaskStatus.QUEUED = "queued"` | `'QUEUED'` |
| `SearchEngine.GOOGLE = "google"` | `'GOOGLE'` |
| `SerpType.IMAGE_SEARCH = "images_search"` | `'IMAGE_SEARCH'` |

`WHERE status = 'pending'` silently matches 0 rows. Always use the UPPERCASE name in raw SQL.

---

## bwp_ranking_service (default DB)

5 app tables + `alembic_version`. Conventions across all tables:

- **PKs are UUID strings** — `id varchar(36)`, no auto-increment integers anywhere.
- **Every table has `created_at` / `updated_at`** (`datetime`, naive UTC, set by the ORM mixin).
- **Real FK constraints exist** (unlike bwp_seo) — `information_schema.key_column_usage` works.

### `domain` — ~33 rows (note: singular table name)

Root entity. One row per tracked domain.

| Column | Type | Notes |
|---|---|---|
| `id` | varchar(36) PK | UUID |
| `domain` | varchar(255), indexed | hostname, e.g. `example.com` |
| `country` | varchar(10) | |
| `language` | varchar(10) | default `en` |
| `priority` | int | 1 = highest, 10 = lowest |

### `keywords` — ~2.1K rows

| Column | Type | Notes |
|---|---|---|
| `id` | varchar(36) PK | |
| `domain_id` | varchar(36) → `domain.id`, indexed | |
| `parent_id` | varchar(36) → `keywords.id`, nullable, indexed | self-FK: secondary keywords point at their primary |
| `keyword` | text | **no index** — filter via `domain_id` first, never `LIKE`-scan alone |
| `level` | enum(`'PRIMARY'`,`'SECONDARY'`) | |

### `serp_reports` — ~123 rows

Domain-level report: both the job-queue entry and the historical record. One report per domain per check.

| Column | Type | Notes |
|---|---|---|
| `id` | varchar(36) PK | |
| `domain_id` | varchar(36) → `domain.id` | |
| `status` | enum(`'PENDING'`,`'IN_PROCESS'`,`'PROCESSED'`) | aggregate of its tasks' states |
| `priority` | int | default 10 |
| `date_recorded` | datetime | when the check was queued |
| `completed_at` | datetime, nullable | |
| `task_id` | varchar(255), nullable, indexed | Celery task id |

Indexes: (`domain_id`,`status`), (`domain_id`,`date_recorded`), (`status`,`priority`).

### `serp_tasks` — ~32K rows

Individual SERP collection task. Each keyword in a report spawns 4 tasks (Google/Yahoo/Bing organic + Google images) → total tasks = keywords × 4.

| Column | Type | Notes |
|---|---|---|
| `id` | varchar(36) PK | |
| `report_id` | varchar(36) → `serp_reports.id`, indexed | |
| `keyword_id` | varchar(36) → `keywords.id`, nullable, indexed | |
| `search_engine` | enum(`'GOOGLE'`,`'YAHOO'`,`'BING'`,`'DUCKDUCKGO'`) | |
| `serp_type` | enum(`'ORGANIC'`,`'IMAGE_SEARCH'`,`'RICH_SNIPPET'`,`'AI_OVERVIEW'`,`'LOCAL_PACK'`,`'PEOPLE_ALSO_ASK'`) | |
| `tag` | varchar(255), UNIQUE, indexed | DataForSEO correlation tag |
| `data_source_task_id` | varchar(255), nullable | DataForSEO task id |
| `status` | enum(`'QUEUED'`,`'COLLECTING'`,`'COLLECTED'`,`'COMPLETED'`,`'FAILED'`), indexed | |
| `failure_reason` | text, nullable | prefixed `QueuedError:` / `CollectingError:` / `CollectedError:` — the prefix drives retry strategy |
| `retry_count` | int | max retries 5 |
| `cost` | float, nullable | DataForSEO spend; **`0.0` = Redis cache hit** (no API call) |
| `collected_at` | datetime, nullable | |

### `serp_rank_scores` — ~22K rows

One score per (report, keyword, serp_type, engine, level).

| Column | Type | Notes |
|---|---|---|
| `id` | varchar(36) PK | |
| `report_id` | varchar(36) → `serp_reports.id`, indexed | |
| `keyword_id` | varchar(36) → `keywords.id`, indexed | |
| `serp_type` | enum — same literals as `serp_tasks.serp_type`, indexed | |
| `engine` | enum — same literals as `serp_tasks.search_engine` | |
| `level` | int, default 1 | only ORGANIC can have multiple levels (domain at positions 1, 15, 23 → levels 1, 2, 3); all other types are first-match-only |
| `score` | int | |
| `score_change` | int, nullable | `NULL` = first time tracked; keyword seen before but not in this type = `1000 - current_score`; both exist = `old - new` (**positive = improvement**) |
| `rank_url` | text, nullable | URL that ranked |
| `query_url` | text, nullable | SERP query URL |

Composite index: (`report_id`,`keyword_id`).

### Status flows

- **Report**: `PENDING` → `IN_PROCESS` → `PROCESSED`
- **Task**: `QUEUED` → `COLLECTING` → `COLLECTED` → `COMPLETED` (or `FAILED` at any stage)

Driven by the 5 Celery tasks in `app/workers/serp_worker/`: `queue_domains` → `post_serp_tasks` → `collect_serp_results` → `score_serp_results` (+ `retry_failed_tasks`).

---

## bwp_seo (external DB, `--db bwp_seo`)

The legacy DB the auto-queue (`serp_queue_service` / `queue_domains`) reads. Integer PKs, **no declared FK constraints** — relations are by convention (`domainid → bwp_domains.id`). Only the columns the service uses are listed; the live tables have more.

### The 5 modeled tables (`app/models/external/*.py`)

| Table | Rows | Columns the service uses |
|---|---:|---|
| `bwp_domains` | ~64K | `id`, `userid`, `domain_name`, `domain_url`, `domain_country` (default US), `servicetype` (varchar! cast to int to join `bwp_services.id`), `keywords`, `status` (int; **2 = active**), `deleted`, `skipfeedchecker` |
| `bwp_domain_settings` | ~19K | `id`, `domainid`, `setcancel` (1 = cancelled → excluded) |
| `bwp_services` | ~459 | `id`, `level`, `servicetype`, `packageid`, `description`, `price`, `keywords`, `rankdays` (check cadence in days), `sitemapdays`, `active`, `rankingreports` |
| `bwp_bubblefeed` | ~129K | `id`, `categoryid`, `domainid`, `restitle` (**the primary keyword text**), `active`, `deleted` |
| `bwp_bubblefeedsupport` | ~29K | `id`, `categoryid`, `bubblefeedid` (→ parent bubblefeed), `domainid`, `restitle` (secondary keyword text), `active`, `deleted` |

### Auto-queue eligibility rule

From `app/repositories/serp_queue_repository.py` — a domain is auto-queued only when ALL of:

1. `bwp_domains.status = 2 AND deleted = 0 AND skipfeedchecker = 0`
2. `bwp_domain_settings.setcancel = 0` (or no settings row at all)
3. `bwp_services.active = 1 AND rankdays > 0`, joined via `bwp_services.id = CAST(bwp_domains.servicetype AS INT)`
4. days since the domain's last `serp_reports.date_recorded` ≥ `rankdays` (or never recorded)
5. at least one primary keyword: `bwp_bubblefeed` row with `active = 1 AND deleted = 0` and non-empty `restitle`

"Why wasn't domain X queued?" = walk this chain; see `recipes.md` §6.

### bwp_seo cautions

- **PII**: `bwp_register` (~53K users — emails, names, hashed passwords) and `bwp_creditcardinfo`. Never `SELECT *`; mask when relaying.
- **Huge log tables**: `bwp_log_page` ~6.2M, `bwp_modification_log` ~1.1M, `bwp_reviewpage_keywords` ~700K. `EXPLAIN` before joining; always filter + LIMIT.
- **Soft deletes** via `deleted` int flag; **status codes are ints** (lookup: `bwp_domain_status`).
- **Legacy datetimes** are naive `datetime`, historically US/Pacific.
