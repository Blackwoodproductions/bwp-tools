---
name: seo-api
description: Call seo-service (the NestJS API over bwp_seo) from the cade-service repo — prod by default, or local. Same client and gating as cade-api (reads free, POST/PUT/PATCH need --confirm, DELETE needs --confirm --destructive), runs saved to `.claude/seo-api-run/`. Use during Premium SEO → CADE migrations and BRON/SEOMoney work whenever you need what only seo-service knows or writes — bwp domain facts (status, wp_plugin, script version, legitscript, agentic), a domain's link context, the pending bubblefeed content queue, a bubblefeed row's stored article/status, the reconnect crawl, retract/restore a row, fix a row's cut-over url. Triggers on "call seo-service", "hit the seo-service api", "domain facts for <domain>", "link context", "content queue", "bubblefeed row <id>", "reconnect <domain>", "run the crawl for <domain>", "retract keyword <id>", "set the url on keyword <id>", "list seo-service endpoints", or direct invocation "seo-api". This is a live production write surface over the customer-facing bwp data — honor the gating.
---

# seo-api

> **Paths:** the scripts belong to the sibling `cade-api` skill: run `<skill-dir>/../cade-api/scripts/call_api.py --service seo ...` from the **cade-service repo root** (for `.claude/skills.settings.{env}.json`). Credentials: the `seo-api` block, `{"api-url": "https://api.imagehosting.space", "api-key": "<SEO_SERVICE_API_KEY>"}`.

```bash
S=<skill-dir>/../cade-api/scripts
python $S/call_api.py --service seo GET /internal/cade-domain-facts --query '{"domain":"example.com"}'
python $S/call_api.py --service seo POST /internal/domains/123/crawl --body '{"dryRun":true}' --confirm
python $S/list_endpoints.py --service seo --filter internal          # live OpenAPI, cached
```

- **Envs:** `prod` (default) and `local` (`http://localhost:3003`). There is **no staging**: CADE staging talks to the prod seo-service, so `--env stg` is refused.
- **Auth:** `X-API-Key` only. The key opens every `/internal/*` route, reads and writes alike.
- Responses are bare JSON (no `{data}` envelope). Errors are Nest's `{statusCode, message}`.
- Rows are addressed by the **bwp domain id** (`domainId` from `cade-domain-facts`), never the name, and by **table**: `keywords` = `bwp_bubblefeed`, `supporting-keywords` = `bwp_bubblefeedsupport`. The two tables' ids collide, so never guess the table from an id.

## `/internal` routes

| Method + path | What | Write? |
|---|---|---|
| GET `/internal/cade-domain-facts?domain=` | bwp id, status, wpPlugin, scriptVersion, label, legitscript, pluginHistory, agentic. Any status; 404 unknown, 409 two live rows | — |
| GET `/internal/cade-subscription?domain=` | weekly CADE article quota | — |
| GET `/internal/cade-content-queue?domain=&limit=` | pending bubblefeed work (no article yet) | — |
| GET `/internal/domains/:id/link-context` | pages that can carry partner links, the links they render, addresses link sync needs | — |
| GET `/internal/domains/:id/{keywords,supporting-keywords}/:kw/content` | row's stored article RAW, summary, meta, status, createdDate. Does **not** include url/resourcesUrl — read those with `seolocal-db-queries` | — |
| POST `/internal/domains/:id/crawl` `{dryRun}` | the runbook's **Reconnect**: services-app crawler inline (~30 s), returns verdict + predictedStatus | **yes unless `dryRun: true`** — always dry-run first |
| PUT `.../{keywords,supporting-keywords}/:kw/status` `{status: publish\|draft}` | retract/restore; pings the site | yes |
| PUT `.../keywords/:kw/url` `{url?, resourcesUrl?}` (supporting: `url` only) | cut-over address; `null` clears (rollback). Never pings the site | yes |
| PUT `.../{keywords,supporting-keywords}/:kw/content`, `.../keywords/:kw/feedtext`, `.../:kw/summary` | CADE's write-back — normally CADE's job, not a hand call | yes |
| DELETE `.../{keywords,supporting-keywords}/:kw/content` | blanks all bodies, marks row `deleted` | **destructive** |
| POST `/internal/notifications/email` | sends a real customer email | **never by hand** |

User-scoped routes (`/domains/...`) also need a signed `X-Acting-Context` token this client can't mint. Stick to `/internal`.

Colleagues in Claude Chat get a curated, domain-keyed subset through cade-mcp (`seo_domain_facts`, `seo_link_context`, `seo_content_queue`, `seo_keyword`, `seo_crawl`, `seo_keyword_status`). This skill is the full surface, for operators only.
