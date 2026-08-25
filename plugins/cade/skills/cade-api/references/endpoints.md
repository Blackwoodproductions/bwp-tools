# CADE API — endpoint catalog

Curated map of the CADE API grouped by area. This is a **navigation aid**, not an authoritative schema — the live OpenAPI spec is. When you need the exact request body shape, run:

```bash
python <skill-dir>/scripts/list_endpoints.py --json /api/v1/<path>
```

All paths are under `/api/v1/`. The wrapper prefixes that for you — pass `/keywords`, not `/api/v1/keywords`.

**Auth** — every row below uses `X-API-Key` unless it says *(plugin key)*, in which case the wrapper auto-routes to `X-WordPress-Plugin-Key`.

---

## System / health

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/system/health` | Deep health probe (DB, Redis, workers). Unauthenticated. |
| `GET` | `/system/sanity-check` | Sanity-check of critical integrations. |

```bash
python <skill-dir>/scripts/call_api.py GET /system/health
```

## Domain & crawling

| Method | Path | Purpose |
|---|---|---|
| `GET`    | `/domain/{domain}` | Fetch a domain record. |
| `POST`   | `/domain` | Create/register a domain. *(write)* |
| `POST`   | `/crawler` | Trigger a crawl. Returns `202` + `task_id`. *(write)* |
| `GET`    | `/tasks/crawl/...` | Inspect crawl task state (see *Tasks*). |

```bash
# Lookup
python <skill-dir>/scripts/call_api.py GET /domain/example.com

# Kick off a crawl
python <skill-dir>/scripts/call_api.py POST /crawler \
  --body '{"domain":"example.com","user_id":"u1","request_id":"req-123"}' \
  --confirm
```

## Domain context (categorization)

| Method | Path | Purpose |
|---|---|---|
| `GET`   | `/domain/context/{domain}` | Read the domain's categorization context. |
| `PATCH` | `/domain/context/{domain}` | Update fields on the context. *(write)* |
| `POST`  | `/domain/context/validation/{domain}` | Mark context as validated — gates content generation. *(write)* |

Content and keyword generation endpoints **require** the domain context to be `is_validated=True`. If a POST returns `403 "Domain context must be validated"`, validate first.

## Keywords

| Method | Path | Purpose |
|---|---|---|
| `POST`   | `/keywords` | Queue keyword generation for a domain. Returns `202` + `task_id`. *(write)* |
| `GET`    | `/keywords/domain/{domain}` | List keywords for a domain (paginated). |
| `GET`    | `/keywords/{keyword_id}` | Fetch single keyword with supporting/questions. |
| `PUT`    | `/keywords/{keyword_id}` | Full replacement update of keyword + children. *(write)* |
| `POST`   | `/keywords/rejections` | Bulk reject keyword IDs with a reason. *(write)* |
| `DELETE` | `/keywords/{keyword_id}` | Hard-delete a keyword and all its children. *(destructive)* |

```bash
# List unused keywords for a domain
python <skill-dir>/scripts/call_api.py GET /keywords/domain/example.com \
  --query '{"status":"unused","page_size":50}'

# Queue generation
python <skill-dir>/scripts/call_api.py POST /keywords \
  --body-file payload.json --confirm

# Bulk reject
python <skill-dir>/scripts/call_api.py POST /keywords/rejections \
  --body '{"domain_id":"...","keyword_ids":["...","..."],"reason":"off_topic"}' \
  --confirm
```

## Content generation

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/content` | Queue content generation. Returns `202` + `task_id`. *(write)* |
| `GET`  | `/tasks/content/...` | Inspect content task state (see *Tasks*). |
| `POST` | `/tasks/content/termination/...` | Request termination of a running content task. *(write)* |

Requires the target domain to have a validated `DomainContext`.

## Publishing

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/publication/schedule` | Schedule published content. *(write)* |
| `POST` | `/publication/publish` | Trigger immediate publish to the connected platform. *(write)* |

Publishing dispatches Celery `publisher` workers and commits to WordPress/SEOMoney — effects are observable externally, treat `--confirm` seriously.

## Tasks (status / control)

Task status endpoints are the poll-side counterpart to the `POST`s above.

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/tasks/status` | Generic task-status probe by `task_id` query param. |
| `GET`  | `/tasks/crawl/{task_id}` | Crawl-specific status. |
| `GET`  | `/tasks/categorization/{task_id}` | Categorization-specific status. |
| `GET`  | `/tasks/content/{task_id}` | Content-specific status. |
| `GET`  | `/tasks/domain/{task_id}` | Domain-level aggregate task status. |

Example after a `POST /keywords`:

```bash
# The POST response body contains data.status_url — fetch it:
python <skill-dir>/scripts/call_api.py GET /tasks/status \
  --query '{"task_id":"<task_id>"}'
```

## Scheduling

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/scheduling/blog/...` | Blog scheduling entrypoint. *(write)* |
| `POST` | `/scheduling/faq/...` | FAQ scheduling entrypoint. *(write)* |
| `GET`  | `/scheduling/scheduled-content/...` | Read scheduled-content state. |

## Subscription *(plugin key)*

Use the WordPress plugin key — the wrapper auto-routes when the path starts with `/subscription/`.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/subscription/activate` | Activate a subscription for a domain. *(write)* |
| `POST` | `/subscription/deactivate` | Deactivate a subscription. *(write)* |

## Organization / authors / articles / platform connections

| Method | Path | Purpose |
|---|---|---|
| `GET`/`POST`/`PATCH`/`DELETE` | `/domain/{domain}/organization` | Organization CRUD. |
| `GET`/`POST`/`PATCH`/`DELETE` | `/domain/{domain}/authors` | Author CRUD. |
| `GET`/`POST`/`PATCH`/`DELETE` | `/domain/{domain}/articles` | Article CRUD. |
| `GET`/`POST`/`PATCH`/`DELETE` | `/domain/{domain}/platform-connections` | Platform connection CRUD. |
| `GET`/`POST`/`PATCH`/`DELETE` | `/domain/{domain}/platform-profiles` | Platform profile CRUD. |
| `GET`/`POST`/`PATCH`/`DELETE` | `/domain/{domain}/profile-pages` | Profile page CRUD. |

Use `list_endpoints.py --filter <area>` + `--json <path>` for the exact method list and request body shape — these routers carry many variants.

## Schema markup

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/schema-markup/generation/...` | Generate schema markup. *(write)* |
| `POST` | `/schema-markup/faq/...` | Generate FAQ schema. *(write)* |
| `POST` | `/schema-markup/homepage/...` | Generate homepage schema. *(write)* |
| `POST` | `/schema-markup/profile-page/...` | Generate profile-page schema. *(write)* |

## Location options

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/location/...` | Read supported country/region options for targeting. |

## Publishing readiness

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/domain/{domain}/publishing-readiness` | Pre-flight check for the publish pipeline. |

---

## Body-schema lookup recipe

When you need the exact request body a `POST` expects, don't guess — pull from OpenAPI:

```bash
python <skill-dir>/scripts/list_endpoints.py --json /api/v1/keywords \
  | python -c 'import json,sys; d=json.load(sys.stdin); print(json.dumps(d["post"]["requestBody"]["content"]["application/json"]["schema"], indent=2))'
```

Follow `$ref` into `components.schemas` in the full spec (cached at `<skill-dir>/.cache/openapi-<env>.json`) for nested shapes.
