# CADE Database Schema Reference

Detailed table reference for the `cade-db-queries` skill. Load only when you need column-level detail — for a high-level question, the schema-discovery snippets in SKILL.md are usually enough.

**Canonical relationship chain:**
```
Domain → PlatformConnection → PlatformProfile → PlatformPage
Domain → DomainContext (1:1, intake form)
Domain → DomainContent → ContentPublication
Domain → Author
```

> **Note on author ↔ connection linkage:** The author↔connection relationship lives on `PlatformProfile` (since March 2026). The only valid path is `PlatformConnection → PlatformProfile → Author`. `PlatformConnection` and `PlatformPage` do **not** have `author_id`.

---

## 1. Core domain model

| Table | Purpose | Key columns |
|---|---|---|
| **domains** | Root entity for each registered website. | `id` (UUID PK), `domain` (unique), `page_count`, `topical_map` (JSONB), `css_rules` (JSONB, GIN), `country`, `language` |
| **domain_categories** | Many-to-many: domains ↔ categories with confidence. | `id`, `domain_id` (FK), `category_id` (FK), `confidence_score` (float, default 1.0). Composite uniqueness on (domain_id, category_id). |

---

## 2. Platform integrations

| Table | Purpose | Key columns |
|---|---|---|
| **platform_connections** | One per (domain, platform) — e.g., a WordPress install. | `id`, `domain_id` (FK, indexed), `platform_type`, `site_url`, `credentials_encrypted`, `status` (default `"unchecked"`), `schema_status`, `schema_verified_at`, `last_checked_at` |
| **platform_profiles** | Author bound to a connection. Unique on (platform_connection_id, author_id). | `id`, `platform_connection_id` (FK), `author_id` (FK, RESTRICT), `username`, `email`, `credentials_encrypted`, `platform_data` (JSONB — typed via `PLATFORM_DATA_REGISTRY`), `schema_status`, `platform_page_id` (FK, SET NULL) |
| **platform_pages** | CADE-managed page/post on an external platform. | `id`, `platform_connection_id` (FK), `remote_page_id`, `remote_url`, `page_type`, `status` (default `"published"`) |

Credentials are encrypted at both the connection and profile level.

---

## 3. Domain context (intake form)

| Table | Purpose | Key columns |
|---|---|---|
| **domain_contexts** | 1:1 intake per domain — business profile used across content generation. | `id`, `domain_id` (FK, unique), `validated_at` (nullable). Body covers: business basics, services offered/not_offered, service area, brand voice, differentiators, contact, content prefs, legal, template config, target keywords, `extraction_confidence` (JSONB). |

`is_validated` = `validated_at IS NOT NULL`. Canonical field map lives in `app/domain/services/organization_sync.py` (`ORG_CONTEXT_FIELD_MAP`).

---

## 4. Authors & organizations

| Table | Purpose | Key columns |
|---|---|---|
| **authors** | Person entity scoped to domain (schema.org Person). | `id`, `domain_id` (FK), `first_name`, `last_name`, `email`, `slug`, `url`, `image_url`, `job_title`, `socials` (JSONB), `knows_about` (JSONB). Unique on (domain_id, slug), (domain_id, email). |
| **organizations** | 1:1 Organization per domain (schema.org Organization). | `id`, `domain_id` (FK, unique), `name`, `url`, `logo_url`, `address_*`, `telephone`, `email`, `area_served` |

---

## 5. Content & keywords

| Table | Purpose | Key columns |
|---|---|---|
| **domain_keywords** | Trending keyword. | `id`, `keyword`, `search_intent`, `embedding` (Vector 1536), `country` (indexed), `subdivision_code`. Indexed on `lower(keyword)`. |
| **supporting_keywords** | Sub-keywords under a trending keyword. Unique on (keyword, keyword_id). | `id`, `keyword_id` (FK), `keyword`, `search_intent`, `relevant_questions` |
| **relevant_questions** | Questions derived from keywords (feeds FAQ generation). | `id`, `domain_keyword_id` (FK), `question` |
| **domain_keyword_rejections** | Per-domain rejection record (keyword rejected for domain A can still win for domain B). Unique on (domain_keyword_id, domain_id). | `id`, `domain_keyword_id` (FK), `domain_id` (FK), `reason`, `rejected_at` |
| **categories** | Global category for classification, shared across domains. | `id`, `value`, `embedding` (Vector 1536, nullable) |

---

## 6. Content generation lifecycle

| Table | Purpose | Key columns |
|---|---|---|
| **domain_content** | Content at every stage — brief → outline → media → SEO → content. Unique on (keyword_id, domain_id). | `id`, `domain_id` (FK), `keyword_id` (FK), `content_type` (indexed), `author`, `author_id` (FK), `version`, `segment`, `outline_embedding` (Vector 1536), `brief`/`meta`/`outline`/`media`/`seo`/`content` (JSONB, GIN) |
| **acg_process** | Agent Content Generation process — one per job. | `id`, `job_id` (FK, unique, nullable), `started_at`, `completed_at`, `status` (indexed, default `ACGProcessStatus.PENDING`) |
| **domain_content_acg_processes** | Join: one domain_content tracked through one ACG process. | `id`, `domain_content_id` (FK, indexed), `acg_process_id` (FK, indexed), `content_generation_stage` (default `ContentGenerationStage.PENDING`) |
| **content_publications** | Immutable publication record per (platform, content). | `id`, `domain_content_id` (FK), `platform_post_id`, `platform_url`, `content` (JSONB, GIN), `status` (default `PublicationStatus.PENDING`), `platform`, `publication_type`, `published_at` |
| **publishing_processes** | Publishing stage, decoupled from ACG timeline. | `id`, `content_publication_id` (FK), `acg_process_id` (FK, nullable), `stage` (indexed, default `ContentPublishingStage.PENDING`), `started_at`, `finished_at` |

---

## 7. Crawled content & RAG

| Table | Purpose | Key columns |
|---|---|---|
| **pages** | One row per crawled page. | `id`, `domain_id` (FK), `url`, `title`, `html`, `markdown`, `crawled_at`, `status_code`, `importance_score` (indexed). Composite index on (domain_id, importance_score). |
| **content_chunks** | Text chunk from a page for semantic search / RAG. | `id`, `page_id` (FK), `content`, `title`, `summary`, `sequence_number`, `embedding` (Vector 1536, nullable). Composite index on (page_id, sequence_number). |
| **content_chunk_meta** | Chunk provenance. | `id`, `chunk_id` (FK), `source`, `chunk_size`, `crawled_at`, `url_path`. Composite indexes on (chunk_id, chunk_size), (chunk_id, source). |

---

## 8. FAQ & questions

| Table | Purpose | Key columns |
|---|---|---|
| **faqs** | Generated FAQ answer. | `id`, `domain_id` (FK, nullable), `domain_content_id` (FK, nullable), `job_id` (FK, nullable), `question_id` (FK, nullable), `publication_id` (FK, nullable), `answer` |
| **processed_questions** | Append-only audit: which questions → which FAQs. | `id`, `faq_id` (FK, indexed, nullable), `processed_at` |

---

## 9. Jobs

| Table | Purpose | Key columns |
|---|---|---|
| **jobs** | Celery job ↔ domain work mapping. | `id` (UUID PK), `domain_id` (FK), `task_id`, `job_type` (indexed, default `JobType.CRAWL`), `status` (indexed, default `JobStatus.PENDING`), `user_id`, `request_id` (indexed), `target_request_id`, `callback_url`, `completed_at`. Composite indexes: (id, domain_id), (id, request_id). |

---

## 10. Scheduling (`cade_scheduling` schema)

Tables in this section live in the **`cade_scheduling` schema**, not `public`. Always fully-qualify: `cade_scheduling.scheduled_content`.

| Table | Purpose | Key columns |
|---|---|---|
| **scheduled_content** | Append-only lifecycle tracker for scheduler firings. | `id`, `domain_id` (FK), `content_type`, `state`, `status`, `content_identifier` (JSONB), `scheduled_at`, `picked_up_at`, `processed_at`, `error`, `attempts`, `tracker_metadata` (JSONB, **column name `metadata`**). Indexes: (domain_id, state, scheduled_at), (state, picked_up_at). |
| **scheduled_content_job** | One row per (stage, attempt) for a scheduled_content. Unique on (scheduled_content_id, stage, sequence). | `id`, `scheduled_content_id` (FK), `job_id` (FK, nullable), `stage`, `sequence` (default 1), `state`, `started_at`, `finished_at`, `error` |
| **blog_scheduling_config** | Per-domain blog schedule. | `domain_id` (PK via FK), `enabled` (default true), `frequency` (`"daily"`, `"weekly"`, ...), `articles_per_batch`, `publish_status` (default `"draft"`), `last_run_at`, `next_run_at`, `last_error` |
| **faq_scheduling_config** | Per-domain FAQ schedule. | `domain_id` (PK via FK), `enabled`, `schedule_type` (`"daily"`, `"every_other_day"`, ...), `daily_count`, `every_other_day_count`, `publish_status`, `last_run_at`, `next_run_at`, `last_error` |

---

## 11. Subscriptions

| Table | Purpose | Key columns |
|---|---|---|
| **seo_money_subscription_detail** | Weekly quota tracking for SEOMoney platform. | `id`, `domain`, `weekly_limit`, `year_week`, `quota_used`, `quota_remaining`, `reset_at` |

---

## 12. Knowledge base

| Table | Purpose | Key columns |
|---|---|---|
| **domain_knowledge_base_processes** | Knowledge-base pipeline (separate from content generation). | `id`, `domain_id` (FK, nullable), `acg_process_id` (FK, nullable), `process_type` (indexed, default `KnowledgeBaseProcessType.NEW`), `stage` (indexed, default `KnowledgeBaseProcessStage.PENDING`) |

---

## Enums & constants

From `app/constants/`:

- **JobStatus** — `PENDING`, `RUNNING`, `COMPLETED`, `FAILED`, `CANCELLED`
- **JobType** — `CRAWL`, `CONTENT_GENERATION`, `FAQ_GENERATION`, ...
- **ACGProcessStatus** — `PENDING`, `RUNNING`, `COMPLETED`, `FAILED`
- **ContentGenerationStage** — tracks `outline → media → seo → content`
- **ContentPublishingStage** — `PENDING`, `FORMATTING`, `PUBLISHING`, `COMPLETED`
- **KnowledgeBaseProcessType** — `NEW`, `UPDATE`
- **KnowledgeBaseProcessStage** — `PENDING`, `PROCESSING`, `COMPLETED`
- **PublicationStatus** — `PENDING`, `PUBLISHED`, `FAILED`
- **SupportedPublicationType** — `CONTENT`, `FAQ`

Always confirm exact enum values by running:
```sql
SELECT unnest(enum_range(NULL::<enum_type_name>));
```
…or reading the constants file directly — enums drift.

---

## Renamed / deprecated

- **`structured_data` → `schema_markup`** (renamed March 2026). Any reference to `structured_data` in older code/docs/URLs is stale.
- **`author_id` on `PlatformConnection` / `PlatformPage`** — removed. Author↔connection linkage moved to `PlatformProfile` (March 2026).

---

## Common starter queries

```sql
-- All content for a domain, most recent first
SELECT id, content_type, version, author, created_at
FROM domain_content
WHERE domain_id = '<uuid>'
ORDER BY created_at DESC
LIMIT 20;

-- Trace a keyword end-to-end
SELECT dc.id AS content_id, dk.keyword, cp.platform, cp.status, cp.published_at
FROM domain_content dc
JOIN domain_keywords dk ON dk.id = dc.keyword_id
LEFT JOIN content_publications cp ON cp.domain_content_id = dc.id
WHERE dc.domain_id = '<uuid>' AND dk.keyword = '<kw>';

-- Platform connection health
SELECT platform_type, site_url, status, schema_status, last_checked_at
FROM platform_connections
WHERE domain_id = '<uuid>';

-- Job progression for a domain (generation + publishing)
SELECT j.id, j.job_type, j.status, ap.status AS acg_status,
       dcap.content_generation_stage, j.created_at
FROM jobs j
LEFT JOIN acg_process ap ON ap.job_id = j.id
LEFT JOIN domain_content_acg_processes dcap ON dcap.acg_process_id = ap.id
WHERE j.domain_id = '<uuid>'
ORDER BY j.created_at DESC
LIMIT 25;

-- Stuck scheduled content
SELECT id, content_type, state, scheduled_at, picked_up_at, attempts, error
FROM cade_scheduling.scheduled_content
WHERE state IN ('pending','in_progress')
  AND scheduled_at < now() - interval '1 hour'
ORDER BY scheduled_at ASC
LIMIT 50;

-- FAQ pipeline health for a domain
SELECT f.id, f.domain_content_id, cp.status AS publication_status,
       cp.published_at, f.created_at
FROM faqs f
LEFT JOIN content_publications cp ON cp.id = f.publication_id
WHERE f.domain_id = '<uuid>'
ORDER BY f.created_at DESC
LIMIT 50;
```

---

## JSONB / vector conventions

- **GIN-indexed JSONB**: `domain_content.{brief, meta, outline, media, seo}`, `content_publications.content`, `domains.css_rules`. Use `->`, `->>`, `@>`, `?` operators — they can hit the index.
- **pgvector columns**: `domain_keywords.embedding`, `content_chunks.embedding`, `domain_content.outline_embedding`, `categories.embedding`. 1536-dim. Use `<->` (L2), `<=>` (cosine), `<#>` (inner product).
- **Encrypted columns**: `credentials_encrypted` on `platform_connections` and `platform_profiles`. These are ciphertext in the DB — don't try to decode from SQL.

---

_This file is maintained by Claude. When `app/models/*.py` changes on a commit, refresh this file to reflect the new/changed schema before committing — see `.claude/CLAUDE.md`._
