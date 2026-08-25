# cade-db-queries — query recipes

Curated catalog of read-only SQL recipes for the CADE production DB, grouped by functional area. Load on demand — for column-level schema reference, see `schema.md`.

## Conventions used in this file

- `'<uuid>'` — a UUID literal, e.g. `'b1f24c00-...-...-...'`
- `'<hostname>'` — a domain hostname, e.g. `'example.com'`
- `'<keyword>'` — a keyword string, e.g. `'plumber near me'`
- `<n>` — a numeric literal you choose
- Most tables carry a standard `TimestampMixin` (`created_at`, `updated_at`). Recipes use those freely; if a table lacks them, swap for the table's own date column.
- Every listing query has an explicit `LIMIT`. The wrapper auto-adds one if missing — but being explicit makes intent obvious.
- All queries are SELECT/WITH/EXPLAIN/SHOW only. The wrapper rejects anything else.

---

## 1. Domains

The root entity. Most investigations start here.

```sql
-- 1.1 Find a domain by hostname (the most common starting query)
SELECT id, domain, country, language, page_count, created_at
FROM domains
WHERE domain ILIKE '<hostname>'
LIMIT 5;

-- 1.2 Domain breakdown by country / language (operational snapshot)
SELECT country, language, count(*) AS domains
FROM domains
GROUP BY country, language
ORDER BY domains DESC
LIMIT 50;

-- 1.3 Recently registered domains
SELECT id, domain, country, page_count, created_at
FROM domains
ORDER BY created_at DESC
LIMIT 25;

-- 1.4 Domains with no organization yet (intake gap)
SELECT d.id, d.domain, d.created_at
FROM domains d
LEFT JOIN organizations o ON o.domain_id = d.id
WHERE o.id IS NULL
ORDER BY d.created_at DESC
LIMIT 50;

-- 1.5 Domains whose topical_map is empty (knowledge map not built)
SELECT id, domain, created_at
FROM domains
WHERE topical_map IS NULL
   OR topical_map = '{}'::jsonb
   OR jsonb_typeof(topical_map) = 'null'
ORDER BY created_at DESC
LIMIT 50;
```

---

## 2. Domain context (intake form)

`domain_contexts` is 1:1 with `domains`. `validated_at` distinguishes validated from unvalidated rows.

```sql
-- 2.1 Validated vs unvalidated context counts
SELECT
  count(*) FILTER (WHERE validated_at IS NOT NULL) AS validated,
  count(*) FILTER (WHERE validated_at IS NULL)     AS unvalidated,
  count(*)                                          AS total
FROM domain_contexts;

-- 2.2 Domains missing a context entirely (no row at all)
SELECT d.id, d.domain
FROM domains d
LEFT JOIN domain_contexts dc ON dc.domain_id = d.id
WHERE dc.id IS NULL
LIMIT 50;

-- 2.3 Most recently validated contexts
SELECT dc.id, d.domain, dc.validated_at
FROM domain_contexts dc
JOIN domains d ON d.id = dc.domain_id
WHERE dc.validated_at IS NOT NULL
ORDER BY dc.validated_at DESC
LIMIT 25;

-- 2.4 Contexts with low extraction confidence on a specific field
-- (replace 'description' with any field tracked in extraction_confidence)
SELECT d.domain,
       (dc.extraction_confidence ->> 'description')::float AS description_conf
FROM domain_contexts dc
JOIN domains d ON d.id = dc.domain_id
WHERE (dc.extraction_confidence ->> 'description')::float < 0.5
ORDER BY description_conf ASC NULLS LAST
LIMIT 25;
```

---

## 3. Authors & organizations

```sql
-- 3.1 Author count per domain (intake completeness signal)
SELECT d.domain, count(a.id) AS author_count
FROM domains d
LEFT JOIN authors a ON a.domain_id = d.id
GROUP BY d.domain
ORDER BY author_count DESC
LIMIT 25;

-- 3.2 Author lookup by slug within a domain
SELECT id, first_name, last_name, slug, email, job_title
FROM authors
WHERE domain_id = '<uuid>' AND slug = '<slug>'
LIMIT 5;

-- 3.3 Authors not linked to any platform_profile (orphaned)
SELECT a.id, d.domain, a.slug, a.email
FROM authors a
JOIN domains d ON d.id = a.domain_id
LEFT JOIN platform_profiles pp ON pp.author_id = a.id
WHERE pp.id IS NULL
ORDER BY a.created_at DESC
LIMIT 50;

-- 3.4 Organization summary for a domain
SELECT id, name, url, logo_url, telephone, email, address_locality, address_region
FROM organizations
WHERE domain_id = '<uuid>';
```

---

## 4. Keywords

`domain_keywords` (trending) → `supporting_keywords` (sub) + `relevant_questions` (FAQ source) + `domain_keyword_rejections` (per-domain rejects).

```sql
-- 4.1 Find a keyword (case-insensitive)
SELECT id, keyword, search_intent, country, subdivision_code
FROM domain_keywords
WHERE lower(keyword) = lower('<keyword>')
LIMIT 10;

-- 4.2 Keywords NOT yet turned into content (publication backlog)
SELECT dk.id, dk.keyword, dk.search_intent, dk.country
FROM domain_keywords dk
LEFT JOIN domain_content dc ON dc.keyword_id = dk.id
WHERE dc.id IS NULL
LIMIT 50;

-- 4.3 Top reject reasons for a domain (why are keywords getting bounced?)
SELECT reason, count(*) AS rejects
FROM domain_keyword_rejections
WHERE domain_id = '<uuid>'
GROUP BY reason
ORDER BY rejects DESC
LIMIT 25;

-- 4.4 Reject reasons across all domains in the last 7 days
SELECT reason, count(*) AS rejects
FROM domain_keyword_rejections
WHERE rejected_at > now() - interval '7 days'
GROUP BY reason
ORDER BY rejects DESC
LIMIT 25;

-- 4.5 Supporting-keyword counts per trending keyword for a domain
-- (high count = rich cluster, low/zero = thin keyword)
SELECT dk.id, dk.keyword, count(sk.id) AS supporting_count
FROM domain_keywords dk
LEFT JOIN supporting_keywords sk ON sk.keyword_id = dk.id
WHERE dk.id IN (SELECT keyword_id FROM domain_content WHERE domain_id = '<uuid>')
GROUP BY dk.id, dk.keyword
ORDER BY supporting_count DESC
LIMIT 25;

-- 4.6 Question count per keyword (FAQ pipeline source health)
SELECT dk.keyword, count(rq.id) AS question_count
FROM domain_keywords dk
JOIN relevant_questions rq ON rq.domain_keyword_id = dk.id
WHERE dk.id IN (SELECT keyword_id FROM domain_content WHERE domain_id = '<uuid>')
GROUP BY dk.keyword
ORDER BY question_count DESC
LIMIT 25;
```

---

## 5. Content generation (`domain_content` + `acg_process` + `domain_content_acg_processes`)

```sql
-- 5.1 Content count by type for a domain
SELECT content_type, count(*) AS rows
FROM domain_content
WHERE domain_id = '<uuid>'
GROUP BY content_type
ORDER BY rows DESC
LIMIT 25;

-- 5.2 Latest content for a domain (most recent first)
SELECT id, content_type, version, author, created_at, updated_at
FROM domain_content
WHERE domain_id = '<uuid>'
ORDER BY created_at DESC
LIMIT 20;

-- 5.3 Content generation stage breakdown (where is everything stuck?)
SELECT dcap.content_generation_stage, count(*) AS rows
FROM domain_content_acg_processes dcap
GROUP BY dcap.content_generation_stage
ORDER BY rows DESC;

-- 5.4 ACG processes by status, last 7 days
SELECT status, count(*) AS processes
FROM acg_process
WHERE started_at > now() - interval '7 days'
GROUP BY status
ORDER BY processes DESC;

-- 5.5 Stuck ACG processes (started but never completed, > 1h ago)
SELECT id, job_id, status, started_at,
       extract(epoch FROM (now() - started_at)) / 60 AS age_minutes
FROM acg_process
WHERE completed_at IS NULL
  AND started_at < now() - interval '1 hour'
ORDER BY started_at ASC
LIMIT 50;

-- 5.6 ACG duration percentiles for completed processes (last 7d)
SELECT
  count(*) AS samples,
  percentile_cont(0.5) WITHIN GROUP (ORDER BY completed_at - started_at)  AS p50,
  percentile_cont(0.95) WITHIN GROUP (ORDER BY completed_at - started_at) AS p95,
  percentile_cont(0.99) WITHIN GROUP (ORDER BY completed_at - started_at) AS p99
FROM acg_process
WHERE completed_at IS NOT NULL
  AND started_at > now() - interval '7 days';

-- 5.7 Trace one keyword's content for a domain (brief / outline / seo presence)
SELECT id, content_type, version, author,
       brief IS NOT NULL  AS has_brief,
       outline IS NOT NULL AS has_outline,
       seo IS NOT NULL    AS has_seo,
       content IS NOT NULL AS has_content,
       created_at
FROM domain_content
WHERE domain_id = '<uuid>'
  AND keyword_id = '<uuid>'
ORDER BY version DESC
LIMIT 10;
```

---

## 6. Publishing (`content_publications` + `publishing_processes`)

```sql
-- 6.1 Publication count by platform & status (live operational view)
SELECT platform, status, count(*) AS rows
FROM content_publications
GROUP BY platform, status
ORDER BY platform, rows DESC;

-- 6.2 Failed publications, most recent first
SELECT id, domain_content_id, platform, status, platform_url, last_updated_at
FROM content_publications
WHERE status = 'FAILED'
ORDER BY last_updated_at DESC NULLS LAST
LIMIT 50;

-- 6.3 Publications stuck in PENDING for > N hours
SELECT id, domain_content_id, platform, created_at,
       extract(epoch FROM (now() - created_at)) / 3600 AS age_hours
FROM content_publications
WHERE status = 'PENDING'
  AND created_at < now() - interval '<n> hours'
ORDER BY created_at ASC
LIMIT 50;

-- 6.4 Publications for a domain (joined for hostname readability)
SELECT cp.id, cp.platform, cp.status, cp.platform_url, cp.published_at
FROM content_publications cp
JOIN domain_content dc ON dc.id = cp.domain_content_id
WHERE dc.domain_id = '<uuid>'
ORDER BY cp.published_at DESC NULLS LAST
LIMIT 25;

-- 6.5 Publishing-process stage distribution for the last 24h
SELECT stage, count(*) AS rows
FROM publishing_processes
WHERE created_at > now() - interval '24 hours'
GROUP BY stage
ORDER BY rows DESC;

-- 6.6 Publishing duration percentiles (last 7d, completed only)
SELECT
  count(*) AS samples,
  percentile_cont(0.5)  WITHIN GROUP (ORDER BY finished_at - started_at) AS p50,
  percentile_cont(0.95) WITHIN GROUP (ORDER BY finished_at - started_at) AS p95
FROM publishing_processes
WHERE finished_at IS NOT NULL
  AND started_at > now() - interval '7 days';
```

---

## 7. Platform integrations (`platform_connections` + `platform_profiles` + `platform_pages`)

Hierarchy: `Domain → PlatformConnection → PlatformProfile → PlatformPage`.

```sql
-- 7.1 Connection status breakdown (the "unchecked" stuck-cache pattern)
SELECT platform_type, status, count(*) AS rows
FROM platform_connections
GROUP BY platform_type, status
ORDER BY platform_type, rows DESC;

-- 7.2 Connections that haven't been health-checked in > 7 days
SELECT id, domain_id, platform_type, site_url, status, last_checked_at
FROM platform_connections
WHERE last_checked_at IS NULL
   OR last_checked_at < now() - interval '7 days'
ORDER BY last_checked_at ASC NULLS FIRST
LIMIT 50;

-- 7.3 All connections for a domain (includes status + schema verification state)
SELECT id, platform_type, site_url, status,
       schema_status, schema_verified_at, last_checked_at
FROM platform_connections
WHERE domain_id = '<uuid>'
ORDER BY platform_type;

-- 7.4 Profile count per connection (which connections have authors wired up?)
SELECT pc.id, pc.platform_type, pc.site_url, count(pp.id) AS profile_count
FROM platform_connections pc
LEFT JOIN platform_profiles pp ON pp.platform_connection_id = pc.id
WHERE pc.domain_id = '<uuid>'
GROUP BY pc.id, pc.platform_type, pc.site_url
ORDER BY profile_count DESC;

-- 7.5 Pages managed by CADE per connection
SELECT pc.platform_type, pc.site_url,
       count(pg.id) AS page_count,
       count(*) FILTER (WHERE pg.status = 'published') AS published
FROM platform_connections pc
LEFT JOIN platform_pages pg ON pg.platform_connection_id = pc.id
WHERE pc.domain_id = '<uuid>'
GROUP BY pc.platform_type, pc.site_url
ORDER BY page_count DESC;

-- 7.6 Schema verification health (per platform)
SELECT platform_type,
       count(*) FILTER (WHERE schema_status = 'verified') AS verified,
       count(*) FILTER (WHERE schema_status = 'failed')   AS failed,
       count(*) FILTER (WHERE schema_status IS NULL)      AS unchecked,
       count(*)                                            AS total
FROM platform_connections
GROUP BY platform_type
ORDER BY platform_type;
```

---

## 8. FAQ (`faqs` + `processed_questions`)

```sql
-- 8.1 FAQ count per domain
SELECT domain_id, count(*) AS faqs
FROM faqs
WHERE domain_id IS NOT NULL
GROUP BY domain_id
ORDER BY faqs DESC
LIMIT 25;

-- 8.2 FAQs not yet linked to a publication (generated but unpublished)
SELECT id, domain_id, domain_content_id, created_at
FROM faqs
WHERE publication_id IS NULL
ORDER BY created_at DESC
LIMIT 50;

-- 8.3 Recent FAQs for a domain (titles only — answers can be huge)
SELECT id, question_id, publication_id, length(answer) AS answer_chars, created_at
FROM faqs
WHERE domain_id = '<uuid>'
ORDER BY created_at DESC
LIMIT 25;

-- 8.4 Question→FAQ conversion: questions seen vs FAQs created (last 7d)
SELECT
  (SELECT count(*) FROM relevant_questions
     WHERE created_at > now() - interval '7 days')                  AS questions_added,
  (SELECT count(*) FROM faqs
     WHERE created_at > now() - interval '7 days')                  AS faqs_created;
```

---

## 9. Jobs

`jobs` maps Celery `task_id` to domain work. The default hard timeout is 45 minutes — anything `RUNNING` longer is suspect.

```sql
-- 9.1 Recent jobs for a domain
SELECT id, job_type, status, task_id, created_at, completed_at
FROM jobs
WHERE domain_id = '<uuid>'
ORDER BY created_at DESC
LIMIT 25;

-- 9.2 Live job-type / status matrix
SELECT job_type, status, count(*) AS rows
FROM jobs
WHERE created_at > now() - interval '24 hours'
GROUP BY job_type, status
ORDER BY job_type, rows DESC;

-- 9.3 Stuck jobs (RUNNING longer than the 45-minute hard timeout)
SELECT id, domain_id, job_type, task_id, created_at,
       extract(epoch FROM (now() - created_at)) / 60 AS age_minutes
FROM jobs
WHERE status = 'RUNNING'
  AND created_at < now() - interval '45 minutes'
ORDER BY created_at ASC
LIMIT 50;

-- 9.4 Failed jobs by type, last 7 days
SELECT job_type, count(*) AS failures
FROM jobs
WHERE status = 'FAILED'
  AND created_at > now() - interval '7 days'
GROUP BY job_type
ORDER BY failures DESC;

-- 9.5 Job + ACG status side by side (where in the pipeline did it die?)
SELECT j.id AS job_id, j.job_type, j.status AS job_status,
       ap.id AS acg_id, ap.status AS acg_status,
       j.created_at, j.completed_at
FROM jobs j
LEFT JOIN acg_process ap ON ap.job_id = j.id
WHERE j.domain_id = '<uuid>'
ORDER BY j.created_at DESC
LIMIT 25;
```

---

## 10. Scheduling (`cade_scheduling` schema)

Tables here live in the `cade_scheduling` schema — fully qualify them.

```sql
-- 10.1 Scheduled-content state breakdown
SELECT state, count(*) AS rows
FROM cade_scheduling.scheduled_content
GROUP BY state
ORDER BY rows DESC;

-- 10.2 Stuck scheduled content (in_progress, picked up > N minutes ago, no completion)
SELECT id, domain_id, content_type, state, attempts, picked_up_at, error
FROM cade_scheduling.scheduled_content
WHERE state = 'in_progress'
  AND picked_up_at IS NOT NULL
  AND picked_up_at < now() - interval '<n> minutes'
  AND processed_at IS NULL
ORDER BY picked_up_at ASC
LIMIT 50;

-- 10.3 Scheduled content for a specific domain, most recent first
SELECT id, content_type, state, status, scheduled_at, picked_up_at, processed_at, attempts
FROM cade_scheduling.scheduled_content
WHERE domain_id = '<uuid>'
ORDER BY scheduled_at DESC
LIMIT 50;

-- 10.4 Per-stage failure rate (last 7 days)
SELECT stage,
       count(*) FILTER (WHERE state = 'failed')    AS failures,
       count(*)                                    AS total,
       round(100.0 * count(*) FILTER (WHERE state = 'failed') / count(*), 2) AS fail_pct
FROM cade_scheduling.scheduled_content_job
WHERE started_at > now() - interval '7 days'
GROUP BY stage
ORDER BY fail_pct DESC;

-- 10.5 Blog schedule overview (which domains are enabled, when do they next run?)
SELECT domain_id, frequency, enabled, articles_per_batch,
       publish_status, last_run_at, next_run_at, last_error
FROM cade_scheduling.blog_scheduling_config
WHERE enabled = true
ORDER BY next_run_at ASC NULLS LAST
LIMIT 50;

-- 10.6 FAQ schedules with a recent error
SELECT domain_id, schedule_type, daily_count, every_other_day_count,
       last_run_at, last_error
FROM cade_scheduling.faq_scheduling_config
WHERE last_error IS NOT NULL
ORDER BY last_run_at DESC NULLS LAST
LIMIT 25;
```

---

## 11. Crawl & RAG (`pages` + `content_chunks` + `content_chunk_meta`)

```sql
-- 11.1 Page count + importance distribution for a domain
SELECT count(*) AS pages,
       avg(importance_score)::numeric(10,4) AS avg_importance,
       max(importance_score)                AS max_importance
FROM pages
WHERE domain_id = '<uuid>';

-- 11.2 Top-importance pages for a domain
SELECT id, url, title, status_code, importance_score, crawled_at
FROM pages
WHERE domain_id = '<uuid>'
ORDER BY importance_score DESC NULLS LAST
LIMIT 25;

-- 11.3 Pages with a non-2xx status code (crawl errors)
SELECT id, domain_id, url, status_code, crawled_at
FROM pages
WHERE status_code IS NOT NULL
  AND status_code >= 400
ORDER BY crawled_at DESC
LIMIT 50;

-- 11.4 Pages without any chunks (chunking pipeline gap)
SELECT p.id, p.domain_id, p.url, p.crawled_at
FROM pages p
LEFT JOIN content_chunks cc ON cc.page_id = p.id
WHERE cc.id IS NULL
ORDER BY p.crawled_at DESC
LIMIT 50;

-- 11.5 Chunk count per page (for one domain)
SELECT p.url, count(cc.id) AS chunk_count
FROM pages p
LEFT JOIN content_chunks cc ON cc.page_id = p.id
WHERE p.domain_id = '<uuid>'
GROUP BY p.url
ORDER BY chunk_count DESC
LIMIT 25;
```

---

## 12. Subscriptions (`seo_money_subscription_detail`)

```sql
-- 12.1 Quota usage snapshot for the current week
SELECT domain, weekly_limit, quota_used, quota_remaining, year_week, reset_at
FROM seo_money_subscription_detail
ORDER BY quota_remaining ASC
LIMIT 25;

-- 12.2 Domains within 10% of their weekly limit
SELECT domain, quota_used, weekly_limit, year_week,
       round(100.0 * quota_used / NULLIF(weekly_limit, 0), 2) AS pct_used
FROM seo_money_subscription_detail
WHERE weekly_limit > 0
  AND quota_used >= weekly_limit * 0.9
ORDER BY pct_used DESC
LIMIT 25;
```

---

## 13. Cross-cutting traces

The most useful queries — these stitch entities together so you can see one workflow end-to-end.

```sql
-- 13.1 Trace one keyword: keyword → content → publication, for a domain
SELECT dk.keyword, dk.search_intent,
       dc.id AS content_id, dc.content_type, dc.version,
       cp.platform, cp.status AS publication_status, cp.platform_url, cp.published_at
FROM domain_keywords dk
JOIN domain_content dc ON dc.keyword_id = dk.id
LEFT JOIN content_publications cp ON cp.domain_content_id = dc.id
WHERE dc.domain_id = '<uuid>'
  AND dk.keyword ILIKE '<keyword>'
ORDER BY dc.created_at DESC, cp.published_at DESC NULLS LAST
LIMIT 25;

-- 13.2 Job → ACG → content → publication (full lifecycle for one job)
SELECT j.id  AS job_id,  j.job_type, j.status   AS job_status,
       ap.id AS acg_id,  ap.status AS acg_status,
       dcap.content_generation_stage,
       dc.id AS content_id, dc.content_type,
       cp.platform, cp.status AS publication_status, cp.platform_url
FROM jobs j
LEFT JOIN acg_process ap                       ON ap.job_id = j.id
LEFT JOIN domain_content_acg_processes dcap    ON dcap.acg_process_id = ap.id
LEFT JOIN domain_content dc                    ON dc.id = dcap.domain_content_id
LEFT JOIN content_publications cp              ON cp.domain_content_id = dc.id
WHERE j.id = '<uuid>'
ORDER BY cp.published_at DESC NULLS LAST
LIMIT 25;

-- 13.3 Domain health snapshot (one row per domain, key counts)
SELECT d.domain,
       (SELECT count(*) FROM domain_content    WHERE domain_id = d.id) AS content_rows,
       (SELECT count(*) FROM content_publications cp
          JOIN domain_content dc ON dc.id = cp.domain_content_id
         WHERE dc.domain_id = d.id)                                    AS publications,
       (SELECT count(*) FROM platform_connections WHERE domain_id = d.id) AS connections,
       (SELECT count(*) FROM faqs WHERE domain_id = d.id)              AS faqs,
       (SELECT count(*) FROM jobs WHERE domain_id = d.id
                          AND status = 'FAILED'
                          AND created_at > now() - interval '7 days')  AS recent_failed_jobs
FROM domains d
WHERE d.id = '<uuid>';

-- 13.4 Find every Celery task_id touching a domain in the last 24h
SELECT j.task_id, j.job_type, j.status, j.created_at
FROM jobs j
WHERE j.domain_id = '<uuid>'
  AND j.created_at > now() - interval '24 hours'
ORDER BY j.created_at DESC
LIMIT 50;

-- 13.5 Who published what, when, for a domain (audit)
SELECT a.first_name || ' ' || coalesce(a.last_name, '') AS author,
       cp.platform, cp.platform_url, cp.published_at, cp.status
FROM content_publications cp
JOIN domain_content dc ON dc.id = cp.domain_content_id
LEFT JOIN authors a    ON a.id = dc.author_id
WHERE dc.domain_id = '<uuid>'
  AND cp.published_at IS NOT NULL
ORDER BY cp.published_at DESC
LIMIT 50;
```

---

## 14. Content auditing (outline & published payloads)

For any question about "what did the pipeline actually write / publish / link to," prefer querying these JSONB columns over fetching the live site. The DB is ground truth for CADE's output; live sites can be post-processed by the platform, cached, 404'd, or Cloudflare-challenged.

**JSONB shape reference:**
- `domain_content.content` — keys: `content_gutenberg`, `content_html`, `content_markdown`, `feedtext`, `money_url_summary`.
- `domain_content.outline` — keys: `title`, `introduction`, `table_of_contents`, `mid_sections`, `conclusion`, `cta`, `references` (the Resources section, array of `{title,url}`), `keyword_hyperlinks` (in-body anchors, array of `{keyword,url}`), `suggested_media`, `media_requirements`.
- `content_publications.content` — keys: `data` (final rendered HTML/Gutenberg string), `feedtext`, `platform`, `platform_format`. `data IS NULL` means no payload was sent — if the live URL still renders content, the platform is filling it in.

**Pick the latest content per keyword — always, and do it by timestamp not by `version`.** `domain_content` can accumulate multiple rows per `(domain_id, keyword_id)` as content is regenerated. A raw `JOIN` returns every historical row mixed together, and stale rows' references differ from what's live today. Every audit query below starts from a `latest_content` CTE that projects the latest row per keyword via `DISTINCT ON (keyword_id) ... ORDER BY keyword_id, updated_at DESC NULLS LAST, created_at DESC`. **Do not order by `version`** — it is known to be buggy (not always incremented on regeneration), so a higher-version row can actually be older. Time-ordered selection is reliable; version-ordered selection isn't.

```sql
-- 14.1 Discover JSONB top-level keys (meta — when the shape is unknown)
WITH s AS (
  SELECT content, outline FROM domain_content
  WHERE content IS NOT NULL AND outline IS NOT NULL
  LIMIT 1
)
SELECT 'content' AS col,
       (SELECT jsonb_agg(DISTINCT k ORDER BY k) FROM s, jsonb_object_keys(s.content) k)::text AS keys
FROM s
UNION ALL
SELECT 'outline',
       (SELECT jsonb_agg(DISTINCT k ORDER BY k) FROM s, jsonb_object_keys(s.outline) k)::text
FROM s;

-- 14.2 Flatten every Resources-section reference for a domain → (keyword, title, url).
-- Latest row per keyword (by timestamp) so stale historical rows don't contaminate the view.
-- Use this instead of WebFetch-ing every live URL.
WITH latest_content AS (
  SELECT DISTINCT ON (dc.keyword_id)
         dc.id, dc.keyword_id, dc.outline, dc.updated_at, dc.created_at
  FROM domain_content dc
  WHERE dc.domain_id = '<uuid>'
  ORDER BY dc.keyword_id, dc.updated_at DESC NULLS LAST, dc.created_at DESC
)
SELECT dk.keyword, lc.updated_at, ref->>'title' AS title, ref->>'url' AS url
FROM latest_content lc
JOIN domain_keywords dk ON dk.id = lc.keyword_id,
LATERAL jsonb_array_elements(lc.outline->'references') AS ref
ORDER BY dk.keyword;

-- 14.3 Competitor audit — references whose anchor text or URL names a competitor,
-- joined to the live publication URL so you can click through and verify.
-- Filters out publications with NULL data payload (those aren't rendering CADE content).
-- IMPORTANT: Postgres POSIX regex uses `\y` for word boundary, NOT `\b` (which is
-- backspace — `\btds\b` silently matches nothing). Prefer SIMILAR TO with % wildcards.
-- Swap the competitor list for `domain_contexts.competitors` + domain knowledge.
WITH latest_content AS (
  SELECT DISTINCT ON (dc.keyword_id) dc.id, dc.keyword_id, dc.outline
  FROM domain_content dc
  WHERE dc.domain_id = '<uuid>'
  ORDER BY dc.keyword_id, dc.updated_at DESC NULLS LAST, dc.created_at DESC
)
SELECT dk.keyword,
       ref->>'title' AS anchor_text,
       ref->>'url'   AS href,
       (SELECT string_agg(cp.platform_url, E' | ' ORDER BY cp.published_at DESC)
          FROM content_publications cp
          WHERE cp.domain_content_id = lc.id
            AND cp.status = 'publish'
            AND cp.content->'data' IS NOT NULL
            AND jsonb_typeof(cp.content->'data') <> 'null') AS live_urls
FROM latest_content lc
JOIN domain_keywords dk ON dk.id = lc.keyword_id,
LATERAL jsonb_array_elements(lc.outline->'references') AS ref
WHERE lower(ref->>'title') SIMILAR TO
        '%(sparklight|digital bridge|google fiber|xfinity|comcast|verizon|fios|hughesnet|viasat|mediacom|frontier|windstream|starlink|ziply|quantum fiber|cable one|cableone|centurylink|century link|rise broadband|risebroadband|t-mobile)%'
   OR lower(ref->>'title') ~* '(^|[^a-z0-9])tds([^a-z0-9]|$)'
   OR lower(ref->>'url') SIMILAR TO
        '%(sparklight|digital-bridge|xfinity|comcast|verizon|fios|hughesnet|viasat|mediacom|frontier|starlink|ziply|cableone|centurylink|tdstelecom)%'
ORDER BY dk.keyword;

-- 14.4 Flatten in-body keyword hyperlinks (structurally different from references)
WITH latest_content AS (
  SELECT DISTINCT ON (dc.keyword_id) dc.id, dc.keyword_id, dc.outline
  FROM domain_content dc
  WHERE dc.domain_id = '<uuid>'
  ORDER BY dc.keyword_id, dc.updated_at DESC NULLS LAST, dc.created_at DESC
)
SELECT dk.keyword, kh->>'keyword' AS anchor_keyword, kh->>'url' AS url
FROM latest_content lc
JOIN domain_keywords dk ON dk.id = lc.keyword_id,
LATERAL jsonb_array_elements(lc.outline->'keyword_hyperlinks') AS kh
ORDER BY dk.keyword;

-- 14.5 Reference and publication counts per article (sanity / coverage)
-- Also reports total rows per keyword so you can spot regeneration activity.
-- (Don't select dc.version — that column is unreliable; see note above.)
WITH latest_content AS (
  SELECT DISTINCT ON (dc.keyword_id)
         dc.id, dc.keyword_id, dc.outline, dc.updated_at, dc.created_at
  FROM domain_content dc
  WHERE dc.domain_id = '<uuid>'
  ORDER BY dc.keyword_id, dc.updated_at DESC NULLS LAST, dc.created_at DESC
)
SELECT dk.keyword,
       lc.updated_at                                                       AS last_updated,
       (SELECT count(*) FROM domain_content dc2
          WHERE dc2.keyword_id = lc.keyword_id)                            AS total_rows,
       COALESCE(jsonb_array_length(lc.outline->'references'), 0)           AS n_references,
       COALESCE(jsonb_array_length(lc.outline->'keyword_hyperlinks'), 0)   AS n_keyword_links,
       (SELECT count(*) FROM content_publications cp
          WHERE cp.domain_content_id = lc.id)                              AS n_publications
FROM latest_content lc
JOIN domain_keywords dk ON dk.id = lc.keyword_id
ORDER BY lc.updated_at DESC NULLS LAST, lc.created_at DESC;

-- 14.6 Grep article body (content_markdown) for a term — case-insensitive
-- Returns snippet windows so you see surrounding context without dumping the full blob.
WITH latest_content AS (
  SELECT DISTINCT ON (dc.keyword_id) dc.id, dc.keyword_id, dc.content
  FROM domain_content dc
  WHERE dc.domain_id = '<uuid>'
  ORDER BY dc.keyword_id, dc.updated_at DESC NULLS LAST, dc.created_at DESC
)
SELECT dk.keyword,
       substring(lc.content->>'content_markdown' FROM '.{0,80}<term>.{0,80}') AS snippet
FROM latest_content lc
JOIN domain_keywords dk ON dk.id = lc.keyword_id
WHERE lc.content->>'content_markdown' ILIKE '%<term>%'
LIMIT 25;

-- 14.7 Publications whose content payload is NULL (platform is rendering fallback,
-- not CADE's content). If the live URL still shows something, the platform is
-- injecting boilerplate — not a CADE content bug.
SELECT cp.platform_url, cp.published_at::date, cp.platform,
       jsonb_typeof(cp.content->'data') AS data_type
FROM content_publications cp
JOIN domain_content dc ON dc.id = cp.domain_content_id
WHERE dc.domain_id = '<uuid>'
  AND (cp.content->'data' IS NULL OR jsonb_typeof(cp.content->'data') = 'null')
ORDER BY cp.published_at DESC;

-- 14.8 Size / presence of final rendered payload per publication
SELECT cp.platform_url, cp.published_at::date, cp.platform,
       jsonb_typeof(cp.content->'data')                             AS data_type,
       CASE WHEN jsonb_typeof(cp.content->'data') = 'string'
            THEN length(cp.content->>'data') END                    AS data_len_chars
FROM content_publications cp
JOIN domain_content dc ON dc.id = cp.domain_content_id
WHERE dc.domain_id = '<uuid>'
ORDER BY cp.published_at DESC
LIMIT 50;
```

**When live-site fetching is still the right tool:** when the investigation is specifically about what the *platform* rendered (post-publish transforms, platform-side contamination, CDN caching), fetching via `curl` and grepping the raw HTML is the correct approach — but even then, prefer `curl` + `grep` over `WebFetch`, because `WebFetch` routes HTML through an LLM that can hallucinate or shuffle results across parallel calls.

---

## Tips when adapting these

- **Copy a recipe, then narrow the WHERE clause.** Recipes aim for "show me the shape"; real investigations need specific identifiers.
- **Add an `EXPLAIN`** in front of any cross-table query you haven't run before. Use the `--explain` flag of `prod_query.py`.
- **Replace `'<uuid>'` placeholders with actual values** before running — `WHERE id = '<uuid>'` will return zero rows but won't error.
- **Status / enum literal values** (`'COMPLETED'`, `'FAILED'`, `'in_progress'`, `'verified'`, etc.) may differ from what's literal here — confirm with `SELECT unnest(enum_range(NULL::<enum_type>));` or grep `app/constants/`.
- **Don't run a join across two large tables without a `WHERE` on an indexed column.** The `EXPLAIN` will show a nested-loop or sequential scan — that's your signal to add a filter.
