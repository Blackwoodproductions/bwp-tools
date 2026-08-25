# ranking-db-queries — query recipes

Curated catalog of read-only SQL recipes for the Ranking Service prod MariaDB, grouped by area. Load on demand — for column-level reference, see `schema.md` and the SQLAlchemy models in `app/models/`.

## Conventions used in this file

- `'<hostname>'` — a domain hostname, e.g. `'example.com'`
- `'<uuid>'` — a varchar(36) UUID primary/foreign key
- `<n>` — a numeric literal you choose
- **Enum literals are UPPERCASE names**: `'PENDING'`, `'QUEUED'`, `'GOOGLE'`, `'ORGANIC'` — never lowercase.
- All columns verified against live `information_schema` at the time of writing.
- Every listing query has an explicit `LIMIT`. The wrapper auto-adds one if missing — but being explicit makes intent obvious.
- All queries are `SELECT/WITH/EXPLAIN/SHOW/DESCRIBE` only. The wrapper rejects anything else. The keyword filter is word-boundary based — avoid standalone aliases like `check` or `change` (`score_change`, `date_recorded` are fine).
- Recipes in §1–§5 run against the default DB. §6 needs `--db bwp_seo`.

---

## 1. Domains & keywords

```sql
-- 1.1 Find a domain by hostname (most investigations start here)
SELECT id, domain, country, language, priority, created_at
FROM domain
WHERE domain LIKE '<hostname>%'
LIMIT 5;

-- 1.2 Keyword counts per domain, split by level
SELECT d.domain,
       SUM(k.level = 'PRIMARY') AS primary_kws,
       SUM(k.level = 'SECONDARY') AS secondary_kws
FROM domain d
LEFT JOIN keywords k ON k.domain_id = d.id
GROUP BY d.id, d.domain
ORDER BY primary_kws DESC
LIMIT 50;

-- 1.3 Keyword tree for a domain (primaries with their secondaries)
SELECT p.keyword AS primary_kw, s.keyword AS secondary_kw
FROM keywords p
LEFT JOIN keywords s ON s.parent_id = p.id
WHERE p.domain_id = '<uuid>'
  AND p.level = 'PRIMARY'
ORDER BY p.keyword, s.keyword
LIMIT 100;

-- 1.4 Find a keyword id by text (always scope by domain — keyword is an unindexed TEXT column)
SELECT k.id, k.keyword, k.level, k.parent_id
FROM keywords k
JOIN domain d ON d.id = k.domain_id
WHERE d.domain = '<hostname>'
  AND k.keyword LIKE '%<term>%'
LIMIT 10;
```

---

## 2. Pipeline health

Report flow: `PENDING → IN_PROCESS → PROCESSED`. Task flow: `QUEUED → COLLECTING → COLLECTED → COMPLETED/FAILED`.

```sql
-- 2.1 Report status breakdown (the one-glance health query)
SELECT status, COUNT(*) AS reports,
       MIN(date_recorded) AS oldest, MAX(date_recorded) AS newest
FROM serp_reports
GROUP BY status
LIMIT 10;

-- 2.2 Task status breakdown, overall
SELECT status, COUNT(*) AS tasks
FROM serp_tasks
GROUP BY status
LIMIT 10;

-- 2.3 Task rollup for one report (is it actually done?)
SELECT t.status, COUNT(*) AS tasks, SUM(t.cost) AS cost
FROM serp_tasks t
WHERE t.report_id = '<uuid>'
GROUP BY t.status
LIMIT 10;

-- 2.4 Stuck reports: not PROCESSED and older than 24h, with task-status rollup
SELECT r.id, d.domain, r.status, r.date_recorded,
       SUM(t.status = 'QUEUED') AS queued,
       SUM(t.status = 'COLLECTING') AS collecting,
       SUM(t.status = 'COLLECTED') AS collected,
       SUM(t.status = 'COMPLETED') AS completed,
       SUM(t.status = 'FAILED') AS failed
FROM serp_reports r
JOIN domain d ON d.id = r.domain_id
LEFT JOIN serp_tasks t ON t.report_id = r.id
WHERE r.status <> 'PROCESSED'
  AND r.date_recorded < DATE_SUB(NOW(), INTERVAL 24 HOUR)
GROUP BY r.id, d.domain, r.status, r.date_recorded
ORDER BY r.date_recorded ASC
LIMIT 25;

-- 2.5 Latest report per domain (window function, MariaDB 10.5)
SELECT domain, report_id, status, date_recorded, completed_at
FROM (
  SELECT d.domain, r.id AS report_id, r.status, r.date_recorded, r.completed_at,
         ROW_NUMBER() OVER (PARTITION BY r.domain_id ORDER BY r.date_recorded DESC) AS rn
  FROM serp_reports r
  JOIN domain d ON d.id = r.domain_id
) latest
WHERE rn = 1
ORDER BY date_recorded DESC
LIMIT 50;

-- 2.6 Reports queued per day (last 14 days)
SELECT DATE(date_recorded) AS day, COUNT(*) AS reports
FROM serp_reports
WHERE date_recorded >= DATE_SUB(NOW(), INTERVAL 14 DAY)
GROUP BY DATE(date_recorded)
ORDER BY day DESC
LIMIT 14;
```

---

## 3. Failures & retries

`failure_reason` is prefixed by failure stage — `QueuedError:` / `CollectingError:` / `CollectedError:` — which drives `retry_failed_tasks`.

```sql
-- 3.1 FAILED tasks grouped by failure-stage prefix
SELECT SUBSTRING_INDEX(failure_reason, ':', 1) AS stage,
       COUNT(*) AS tasks, MAX(retry_count) AS max_retries
FROM serp_tasks
WHERE status = 'FAILED'
GROUP BY stage
ORDER BY tasks DESC
LIMIT 10;

-- 3.2 FAILED task detail for a report
SELECT t.id, k.keyword, t.search_engine, t.serp_type,
       t.retry_count, LEFT(t.failure_reason, 200) AS failure_reason
FROM serp_tasks t
LEFT JOIN keywords k ON k.id = t.keyword_id
WHERE t.report_id = '<uuid>'
  AND t.status = 'FAILED'
ORDER BY t.retry_count DESC
LIMIT 25;

-- 3.3 Retry-exhausted tasks (max retries is 5)
SELECT id, report_id, search_engine, serp_type, retry_count,
       LEFT(failure_reason, 150) AS failure_reason
FROM serp_tasks
WHERE status = 'FAILED'
  AND retry_count >= 5
ORDER BY updated_at DESC
LIMIT 25;

-- 3.4 Tasks stuck in COLLECTING (posted to DataForSEO, never collected)
SELECT t.id, t.tag, t.data_source_task_id, t.updated_at, d.domain
FROM serp_tasks t
JOIN serp_reports r ON r.id = t.report_id
JOIN domain d ON d.id = r.domain_id
WHERE t.status = 'COLLECTING'
  AND t.updated_at < DATE_SUB(NOW(), INTERVAL 6 HOUR)
ORDER BY t.updated_at ASC
LIMIT 25;

-- 3.5 Top failure reasons verbatim (spot a systemic error)
SELECT LEFT(failure_reason, 120) AS reason, COUNT(*) AS tasks
FROM serp_tasks
WHERE status = 'FAILED'
GROUP BY reason
ORDER BY tasks DESC
LIMIT 15;
```

---

## 4. Costs

`cost` is DataForSEO spend per task; **`cost = 0.0` means Redis cache hit** (no API call), `NULL` means never posted.

```sql
-- 4.1 Spend per day (last 30 days, by collected_at)
SELECT DATE(collected_at) AS day,
       COUNT(*) AS tasks, ROUND(SUM(cost), 4) AS spend,
       SUM(cost = 0) AS cache_hits
FROM serp_tasks
WHERE collected_at >= DATE_SUB(NOW(), INTERVAL 30 DAY)
GROUP BY DATE(collected_at)
ORDER BY day DESC
LIMIT 30;

-- 4.2 Spend per domain (all time)
SELECT d.domain, COUNT(t.id) AS tasks, ROUND(SUM(t.cost), 4) AS spend
FROM serp_tasks t
JOIN serp_reports r ON r.id = t.report_id
JOIN domain d ON d.id = r.domain_id
GROUP BY d.id, d.domain
ORDER BY spend DESC
LIMIT 25;

-- 4.3 Cache-hit ratio (cache hits cost nothing)
SELECT COUNT(*) AS collected_tasks,
       SUM(cost = 0) AS cache_hits,
       ROUND(100 * SUM(cost = 0) / COUNT(*), 1) AS hit_pct
FROM serp_tasks
WHERE cost IS NOT NULL
LIMIT 1;

-- 4.4 Cost of one report
SELECT r.id, d.domain, COUNT(t.id) AS tasks, ROUND(SUM(t.cost), 4) AS spend
FROM serp_reports r
JOIN domain d ON d.id = r.domain_id
LEFT JOIN serp_tasks t ON t.report_id = r.id
WHERE r.id = '<uuid>'
GROUP BY r.id, d.domain
LIMIT 1;
```

---

## 5. Scores

`score_change`: `NULL` = first time tracked; positive = improvement (`old - new`); keyword seen before but new to this serp_type = `1000 - current_score`. Only `ORGANIC` has multiple `level` rows per keyword.

```sql
-- 5.1 Score history for one keyword (join reports for the date axis)
SELECT r.date_recorded, s.serp_type, s.engine, s.level, s.score, s.score_change, s.rank_url
FROM serp_rank_scores s
JOIN serp_reports r ON r.id = s.report_id
WHERE s.keyword_id = '<uuid>'
ORDER BY r.date_recorded DESC, s.serp_type, s.engine, s.level
LIMIT 50;

-- 5.2 All scores in one report, worst first
SELECT k.keyword, s.serp_type, s.engine, s.level, s.score, s.score_change
FROM serp_rank_scores s
JOIN keywords k ON k.id = s.keyword_id
WHERE s.report_id = '<uuid>'
ORDER BY s.score DESC
LIMIT 100;

-- 5.3 Biggest movers in a report (largest |score_change|)
SELECT k.keyword, s.serp_type, s.engine, s.score, s.score_change
FROM serp_rank_scores s
JOIN keywords k ON k.id = s.keyword_id
WHERE s.report_id = '<uuid>'
  AND s.score_change IS NOT NULL
ORDER BY ABS(s.score_change) DESC
LIMIT 25;

-- 5.4 Score coverage by serp_type/engine (what are we actually collecting?)
SELECT serp_type, engine, COUNT(*) AS scores, ROUND(AVG(score), 1) AS avg_score
FROM serp_rank_scores
GROUP BY serp_type, engine
ORDER BY scores DESC
LIMIT 25;

-- 5.5 Latest scores for a domain (via its most recent PROCESSED report)
SELECT k.keyword, s.serp_type, s.engine, s.level, s.score, s.score_change
FROM serp_rank_scores s
JOIN keywords k ON k.id = s.keyword_id
WHERE s.report_id = (
  SELECT r.id FROM serp_reports r
  JOIN domain d ON d.id = r.domain_id
  WHERE d.domain = '<hostname>' AND r.status = 'PROCESSED'
  ORDER BY r.date_recorded DESC LIMIT 1
)
ORDER BY k.keyword, s.serp_type
LIMIT 100;
```

---

## 6. Auto-queue (bwp_seo) — run with `--db bwp_seo`

The `queue_domains` worker reads bwp_seo. Eligibility chain (see `schema.md`): `bwp_domains.status = 2, deleted = 0, skipfeedchecker = 0` → `setcancel = 0` (or no settings row) → service `active = 1, rankdays > 0` → due by rankdays → has active primary keywords.

```sql
-- 6.1 "Why wasn't domain X queued?" — walk the whole eligibility chain in one row
SELECT bd.id, bd.domain_name,
       bd.status AS domain_status,        -- must be 2
       bd.deleted,                        -- must be 0
       bd.skipfeedchecker,                -- must be 0
       ds.setcancel,                      -- must be 0 or NULL
       bd.servicetype,
       s.active AS service_active,        -- must be 1
       s.rankdays,                        -- must be > 0
       (SELECT COUNT(*) FROM bwp_bubblefeed bf
         WHERE bf.domainid = bd.id AND bf.active = 1 AND bf.deleted = 0
           AND bf.restitle IS NOT NULL AND bf.restitle <> '') AS primary_kws  -- must be > 0
FROM bwp_domains bd
LEFT JOIN bwp_domain_settings ds ON ds.domainid = bd.id
LEFT JOIN bwp_services s ON s.id = CAST(bd.servicetype AS INT)
WHERE bd.domain_name = '<hostname>'
LIMIT 5;

-- 6.2 All currently eligible domains (ignoring the rankdays-due condition,
--     which needs serp_reports from the other DB)
SELECT bd.id, bd.domain_name, bd.domain_country, s.rankdays
FROM bwp_domains bd
LEFT JOIN bwp_domain_settings ds ON ds.domainid = bd.id
JOIN bwp_services s ON s.id = CAST(bd.servicetype AS INT)
WHERE bd.status = 2 AND bd.deleted = 0 AND bd.skipfeedchecker = 0
  AND (ds.setcancel = 0 OR ds.id IS NULL)
  AND s.active = 1 AND s.rankdays > 0
  AND EXISTS (SELECT 1 FROM bwp_bubblefeed bf
               WHERE bf.domainid = bd.id AND bf.active = 1 AND bf.deleted = 0)
ORDER BY bd.id
LIMIT 100;

-- 6.3 Keywords bwp_seo says to track for a domain (primary + secondary)
SELECT 'PRIMARY' AS level, bf.id, bf.restitle AS keyword
FROM bwp_bubblefeed bf
JOIN bwp_domains bd ON bd.id = bf.domainid
WHERE bd.domain_name = '<hostname>' AND bf.active = 1 AND bf.deleted = 0
UNION ALL
SELECT 'SECONDARY', bs.id, bs.restitle
FROM bwp_bubblefeedsupport bs
JOIN bwp_domains bd ON bd.id = bs.domainid
WHERE bd.domain_name = '<hostname>' AND bs.active = 1 AND bs.deleted = 0
LIMIT 100;
-- Compare against the default DB: SELECT keyword, level FROM keywords k
--   JOIN domain d ON d.id = k.domain_id WHERE d.domain = '<hostname>'
-- (two queries — the wrapper connects to one DB at a time; the ranking DB has
--  no cross-DB grants, so qualified bwp_seo.<table> joins are not portable)

-- 6.4 Service/package cadence overview
SELECT id, servicetype, description, rankdays, active, rankingreports
FROM bwp_services
WHERE active = 1
ORDER BY rankdays
LIMIT 50;
```

---

## 7. Discovery shortcuts

When you don't know the column or even the table (swap the schema literal for `bwp_seo` as needed):

```sql
-- 7.1 Find tables matching a name pattern
SELECT table_name, table_rows
FROM information_schema.tables
WHERE table_schema = 'bwp_ranking_service'
  AND table_name LIKE '%serp%'
ORDER BY table_rows DESC
LIMIT 25;

-- 7.2 Find columns matching a name pattern (handy for figuring out FKs)
SELECT table_name, column_name, column_type
FROM information_schema.columns
WHERE table_schema = 'bwp_ranking_service'
  AND column_name LIKE '%keyword%'
ORDER BY table_name
LIMIT 100;

-- 7.3 Enum literals straight from the live DB (settles casing questions)
SELECT table_name, column_name, column_type
FROM information_schema.columns
WHERE table_schema = 'bwp_ranking_service'
  AND data_type = 'enum'
ORDER BY table_name
LIMIT 25;

-- 7.4 Current Alembic migration head on prod
SELECT version_num FROM alembic_version LIMIT 1;
```
