# CADE API — endpoint catalog

Curated map of the CADE API grouped by area. This is a **navigation aid**, not an authoritative schema — the live OpenAPI spec is. When you need the exact request body shape, run:

```bash
python <skill-dir>/scripts/list_endpoints.py --json /api/v1/<path>
```

All paths are under `/api/v1/`. The wrapper prefixes that for you — pass `/domains/example.com/keywords`, not `/api/v1/domains/example.com/keywords`. `{d}` below is the bare domain (`example.com`).

**Markers**
- *(write)* — `POST`/`PUT`/`PATCH`, needs `--confirm`. *(destructive)* — `DELETE`, needs `--confirm --destructive`.
- *(slow)* — does its work inline (LLM, WordPress round-trips, site probes) instead of handing off to Celery. Can take minutes; see *Timeouts* in SKILL.md.
- *(no auth)* — the route has no auth dependency. *(plugin key ok)* — also accepts `X-WordPress-Plugin-Key`.
- Everything else takes `X-API-Key`, which the wrapper sends by default. All `POST`s that generate/crawl/classify return `202` + a `task_id` quickly — poll `GET /tasks/{task_id}`.

---

## System / tasks

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/system/health` | Deep health probe (DB, Redis). *(no auth)* |
| `GET`  | `/system/workers` | Celery worker status. |
| `GET`  | `/system/queues` | Queue depths. |
| `POST` | `/sanity-checks` | Run sanity checks against a WordPress site (`site_url`, `username`, `app_password`, `callback_url`). *(write, slow)* |
| `GET`  | `/tasks` | List tasks. |
| `GET`  | `/tasks/{task_id}` | Task status by ID — the poll target for every `202`. |
| `GET`  | `/tasks/{task_id}/costs` | LLM costs for a task. |
| `POST` | `/task-cancellations` | Cancel a running task. *(write)* |

```bash
python <skill-dir>/scripts/call_api.py GET /system/health
python <skill-dir>/scripts/call_api.py GET /tasks/<task_id>
```

## Domain core

| Method | Path | Purpose |
|---|---|---|
| `GET`   | `/domains/{d}` | Domain overview. |
| `GET`   | `/domains/{d}/context` | Domain context (intake data). |
| `PATCH` | `/domains/{d}/context` | Update domain context. *(write)* |
| `POST`  | `/domains/{d}/context/validation` | Mark context validated — gates keyword/content generation. *(write)* |
| `POST`  | `/domains/{d}/crawls` | Crawl a domain. *(write)* |
| `POST`  | `/domains/{d}/classifications` | Categorize a domain. *(write)* |
| `POST`  | `/domains/{d}/classifications/css` | Analyze domain CSS. *(write)* |
| `POST`  | `/domains/classifications/bulk` | Categorize many domains. *(write)* |
| `POST`  | `/domains/{d}/summaries` | Generate a domain summary. *(write)* |
| `POST`  | `/domains/summaries/bulk` | Summaries in bulk. *(write)* |
| `POST`  | `/domains/{d}/knowledge-base` | Generate a knowledge base. *(write)* |
| `POST`  | `/domains/{d}/term-syncs` | Trigger a term-mirror sync. *(write)* |
| `GET`/`PATCH` | `/domains/{d}/styling` | Read / update domain styling. |
| `GET`/`PUT`/`PATCH` | `/domains/{d}/template-config` | Read / replace / patch template configuration. |
| `GET`   | `/domains/{d}/publishing-readiness` | Pre-flight check for the publish pipeline. |

If a generation `POST` returns `403 "Domain context must be validated"`, validate first.

## Keywords

| Method | Path | Purpose |
|---|---|---|
| `POST`   | `/domains/{d}/keywords` | Queue keyword generation. *(write)* |
| `GET`    | `/domains/{d}/keywords` | List keywords — `page`, `per_page` (≤100), `status` (`rejected`/`used`/`unused`, repeatable), `order_by`. |
| `POST`   | `/domains/{d}/keyword-rejections` | Bulk reject keyword IDs with a reason. *(write)* |
| `GET`    | `/keywords/{keyword_id}` | Single keyword with supporting keywords/questions. |
| `PUT`    | `/keywords/{keyword_id}` | Full replacement update. *(write)* |
| `DELETE` | `/keywords/{keyword_id}` | Hard-delete a keyword and children. *(destructive)* |

```bash
python <skill-dir>/scripts/call_api.py GET /domains/example.com/keywords \
  --query '{"status":["unused"],"per_page":50}'
```

## BRON (Premium SEO cutover / self-onboarding)

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/domains/{d}/bron/register` | Register a bare `domains` row. No body. Idempotent: `201` created, `200` + `created:false` if it exists. *(write)* |
| `POST` | `/domains/{d}/bron/link-sync` | Queue a BRON link sync for a cut-over domain. `409` when link sync is disabled. *(write)* |
| `GET`  | `/domains/{d}/bron/cutover` | Plan for the cutover off Premium SEO — probes seo-service and the live site. *(slow)* |
| `POST` | `/domains/{d}/bron/cutover` | Queue one stage: `{stage, publish_status}`; stages `detect → prepare → remove → finish`, plus `rollback`. `409` if out of order or already passed. *(write)* |
| `GET`  | `/domains/{d}/bron/cutover/record` | Saved cutover record from the DB only — no site probes. Prefer this for status. |
| `GET`  | `/domains/{d}/bron/cutover/verification` | Verify a finished cutover, `?profile=rescue|full`. Fetches every checked URL. *(slow)* |

```bash
python <skill-dir>/scripts/call_api.py GET /domains/example.com/bron/cutover/record
python <skill-dir>/scripts/call_api.py GET /domains/example.com/bron/cutover/verification \
  --query '{"profile":"full"}'
```

## Publications

| Method | Path | Purpose |
|---|---|---|
| `GET`    | `/domains/{d}/publications` | List publications. |
| `GET`    | `/domains/{d}/publications/{publication_id}` | Single publication. |
| `PUT`    | `/domains/{d}/publications/{publication_id}` | Save edited content and push it to the platform. *(write, slow)* |
| `POST`   | `/domains/{d}/publications/{publication_id}/media` | Regenerate the image, upload, republish. *(write, slow — longest in the API)* |
| `PUT`    | `/domains/{d}/publications/{publication_id}/status` | Retract / restore a published BRON article. *(write, slow)* |
| `GET`    | `/domains/{d}/publications/by-keyword` | BRON keyword row's WordPress article or Resources post — query `kind`, `bwp_domain_id`, `keyword_id`, `publication_type=content|feedtext`. |
| `PUT`    | `/domains/{d}/publications/by-keyword/status` | Retract / restore by bwp row: `{kind, bwp_domain_id, keyword_id, status}`. *(write, slow)* |
| `DELETE` | `/domains/{d}/publications/by-keyword` | Delete by bwp row — **JSON body**; `force: true` permanently deletes instead of WP trash. *(destructive, slow)* |

## Content (articles, FAQs, feedtext)

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/articles` | Generate content for a domain. *(write)* |
| `POST` | `/articles/bulk` | Generate content for many domains. *(write)* |
| `GET`  | `/articles/{article_id}` | Single article. |
| `PUT`  | `/articles/{article_id}` | Update article structured-data fields. *(write)* |
| `POST` | `/articles/publications/{article_id}` | Publish an article to its platform. *(write)* |
| `POST` | `/articles/publications/bulk` | Publish in bulk. *(write)* |
| `POST` | `/articles/faqs/{article_id}` | Generate an FAQ answer. *(write)* |
| `GET`  | `/articles/faqs/{article_id}` | FAQs for an article. |
| `POST` | `/articles/faqs/bulk` | FAQs in bulk. *(write)* |
| `POST` | `/articles/feedtext` | Generate feedtext on demand. *(write)* |
| `GET`  | `/domains/{d}/articles` | List a domain's articles. |
| `GET`  | `/domains/{d}/articles/{article_id}` | Full content detail (management modal). |
| `GET`  | `/domains/{d}/faqs` | List a domain's FAQs. |
| `GET`  | `/domains/{d}/faqs/unprocessed-summary` | Unprocessed FAQ summary. |

Publishing commits to WordPress/SEOMoney — externally visible, treat `--confirm` seriously.

## Scheduling

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/domains/{d}/schedules` | Every blog + FAQ schedule, one per platform. |
| `GET`/`PUT`/`PATCH`/`DELETE` | `/domains/{d}/schedules/article` | Blog schedule get / upsert / patch / delete. |
| `GET`/`PUT`/`PATCH`/`DELETE` | `/domains/{d}/schedules/faq` | FAQ schedule, same four. |
| `GET`  | `/domains/{d}/scheduled-tasks` | List, `?states=&task_types=` (repeatable). *(no auth)* |
| `POST` | `/domains/{d}/scheduled-tasks/{categorization,crawl,css-analysis}` | Queue one task. *(write, no auth)* |
| `POST` | `/domains/{d}/scheduled-tasks/feedtext` | Queue feedtext for one work group. *(write, no auth)* |
| `POST` | `/domains/{d}/scheduled-tasks/summary` | Queue a summary for one bwp keyword row. *(write, no auth)* |
| `POST` | `/domains/{d}/scheduled-tasks/article/bulk` | Bulk-queue article generation. *(write, no auth)* |
| `POST` | `/domains/{d}/scheduled-tasks/publication/bulk` | Bulk-schedule publication (articles + FAQs). *(write, no auth)* |
| `PATCH` | `/scheduled-tasks/bulk` | Bulk cancel / dismiss / complete. *(write)* |
| `GET`   | `/scheduled-tasks/{scheduled_task_id}` | Single scheduled task. |
| `PATCH` | `/scheduled-tasks/{scheduled_task_id}` | Apply a state transition. *(write)* |
| `POST`  | `/scheduled-tasks/{scheduled_task_id}/retries` | Re-queue a failed task. *(write)* |

## Subscription / WordPress webhook *(plugin key ok)*

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/domains/{d}/subscription` | Subscription info. |
| `GET`  | `/domains/{d}/subscription/detail` | Subscription details (plugin key = dashboard key or webhook secret). |
| `GET`  | `/domains/{d}/subscription/active` | Is the subscription active. |
| `POST` | `/domains/{d}/subscription/usage` | Consume quota. *(write)* |
| `POST` | `/domains/{d}/publication-sync` | Webhook from the cade-seo WP plugin — reconciles a post state change and forwards it to seo-service. *(write)* |

## Organization / authors / platform

| Method | Path | Purpose |
|---|---|---|
| `GET`/`PUT` | `/domains/{d}/organization` | Read / upsert organization. |
| `POST`/`GET` | `/domains/{d}/authors` | Create / list authors. |
| `GET`/`PUT`/`DELETE` | `/domains/{d}/authors/{author_id}` | Author get / update / delete. |
| `GET`/`POST` | `/domains/{d}/platform-connections` | List / create connections. |
| `GET`/`PATCH`/`DELETE` | `/domains/{d}/platform-connections/{connection_id}` | Connection get / update / delete. |
| `POST` | `/domains/{d}/platform-connections/{connection_id}/health-checks` | Run connection health checks. *(write, slow)* |
| `GET`  | `/domains/{d}/platform-connections/{connection_id}/plugin-status` | Is the CADE SEO plugin installed. *(slow)* |
| `GET`  | `/domains/{d}/platform-connections/{connection_id}/remote-users` | Users on the remote platform. *(slow)* |
| `PUT`  | `/domains/{d}/platform-connections/{connection_id}/default-profile` | Set a connection's default profile. *(write)* |
| `PUT`  | `/domains/{d}/platform-connections/default-connection` | Set the domain's default connection. *(write)* |
| `GET`/`POST` | `/domains/{d}/platform-profiles` | List / create profiles. *(slow)* |
| `POST` | `/domains/{d}/platform-profiles/links` | Link an existing platform user. *(write, slow)* |
| `GET`/`PATCH`/`DELETE` | `/domains/{d}/platform-profiles/{profile_id}` | Profile get / update / delete. *(slow)* |
| `GET`/`POST` | `/domains/{d}/platform-templates` | List / toggle archive templates. *(slow)* |
| `GET`/`POST`/`DELETE` | `/domains/{d}/profile-pages` | List / create / delete profile pages — `DELETE` takes a **JSON body**; `POST` creates pages one by one. *(slow)* |

## Platform content (live WordPress proxy — every call is *(slow)*)

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/domains/{d}/platform-content` | List posts on the live site. |
| `GET` | `/domains/{d}/platform-content/stats` | Content stats. |
| `GET`/`PATCH`/`DELETE` | `/domains/{d}/platform-content/{post_id}` | Post get / update / delete. |
| `PATCH`/`DELETE` | `/domains/{d}/platform-content/bulk` | Bulk update / delete — `DELETE` takes a **JSON body**. |
| `PATCH` | `/domains/{d}/platform-content/status` | Change one post's status. |
| `PATCH` | `/domains/{d}/platform-content/status/bulk` | Bulk status change. |

## Schema markup

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/schema-markup/organization/{d}` | Generate Organization schema. |
| `GET` | `/schema-markup/person` | Generate Person schema. |
| `GET` | `/schema-markup/blog-posting` | Generate BlogPosting schema. |
| `GET` | `/schema-markup/faq/{faq_id}` | Generate FAQ schema. |
| `GET` | `/schema-markup/profile-page/schema` | Generate ProfilePage schema. |
| `POST`/`DELETE` | `/schema-markup/homepage/{connection_id}` | Inject / remove homepage schema on the site. *(slow)* |
| `GET` | `/schema-markup/homepage/verification/{connection_id}` | Verify homepage schema. *(slow)* |
| `POST`/`DELETE` | `/schema-markup/profile-page/{profile_id}` | Inject / remove profile-page schema. *(slow)* |
| `GET` | `/schema-markup/profile-page/verification/{profile_id}` | Verify profile-page schema. *(slow)* |

## Reference data *(no auth)*

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/reference/locations/countries` | All countries. |
| `GET` | `/reference/locations/subdivisions/{country_code}` | Subdivisions for a country. |
| `GET` | `/reference/locations/cities/{country_code}` | Cities for a country. |
| `GET` | `/reference/business-models` | Supported business models. |
| `GET` | `/reference/categories` | Supported categories. |

---

## Body-schema lookup recipe

When you need the exact request body a `POST` expects, don't guess — pull from OpenAPI:

```bash
python <skill-dir>/scripts/list_endpoints.py --json '/api/v1/domains/{domain}/keywords' \
  | python -c 'import json,sys; d=json.load(sys.stdin); print(json.dumps(d["post"]["requestBody"]["content"]["application/json"]["schema"], indent=2))'
```

Follow `$ref` into `components.schemas` in the full spec (cached at `<skill-dir>/.cache/openapi-<env>.json`) for nested shapes.
