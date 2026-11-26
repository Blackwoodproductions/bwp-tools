---
name: jobs
description: Run a CADE job for {domain} through the cade-mcp tools domain_job, summary_generate, task_retry and task_cancel - a fresh crawl, categorization, CSS analysis, keyword discovery, a WordPress term sync, a keyword summary - or retry or cancel one of its scheduled tasks. Checks what is already queued or running first, since crawl, categorization and CSS analysis are not deduplicated. Use for "recrawl {domain}", "recategorize {domain}", "rerun the CSS analysis on {domain}", "find new keywords for {domain}", "sync the WordPress categories on {domain}", "summary for {keyword} on {domain}", "retry task {id}", "cancel the stuck task on {domain}". Link sync, cutover stages and verification belong to the wp-migration skills.
---

# cade: jobs

**Writes.** Every job here queues work in CADE, and most of it spends LLM money. One domain per confirmation. Every tool repeats the domain in `confirm_domain`. If a tool says another operator is migrating the site, tell the user who, and pass `force=true` only when they say a hand-over was agreed. If any tool fails with "Pomerium identity", tell the user to reconnect the cade-mcp connector (Settings → Connectors) and stop.

## Check first

Crawl, categorization and CSS analysis queue a new row on every call; CADE doesn't check for one already in flight. Before any of them, list what is queued or running:

`api_get("/domains/{domain}/scheduled-tasks", params={"states": ["queued", "processing"]})`

If a row of the same `task_type` is there, say so and don't queue another. Don't filter by strategy: these rows belong to the domain, not to BRON or CADE.

## The jobs

| Job | Call | What it does | Run it when |
|---|---|---|---|
| Crawl | `domain_job(job="crawl")` | Crawls up to 50 pages of the site, then categorizes them in the same row (stages `crawl`, `categorization`) | The site changed a lot, or the stored description is empty or a bot-challenge page (`related_domains` answers `candidates_checked: 0`) |
| Categorization | `domain_job(job="categorization")` | Classifies the domain from the pages already crawled: description, categories, organization | The pages are fine but the description or categories are wrong. Not after a crawl, which already did it |
| CSS analysis | `domain_job(job="css-analysis")` | Reads the site's stylesheets and stores the styling CADE's articles use | After a redesign or theme change, when articles look off-brand |
| Keyword discovery | `domain_job(job="keywords")` | Finds new trending and supporting keywords for the CADE pipeline | A CADE pair is short of keywords. BRON keywords come from seo-service, not from here. It works from the domain's categories and services, so fix a wrong description first |
| Term sync | `domain_job(job="term-sync")` | Refreshes CADE's copy of the categories and tags on each WordPress connection and scans them for duplicates | The client changed categories in wp-admin, or before a term merge (`cade:cade-terms`) |
| Summary | `summary_generate(keyword, refresh=false)` | A 75 to 90 word summary of the business for one keyword, stored in CADE | Someone needs the summary for that keyword. `refresh=true` only to replace a stored one that is wrong |

Notes:
- When a matching keyword job is already running, CADE answers with it instead of starting another. Term sync is safe to repeat: an overlapping sync on the same connection skips.
- Term sync needs a WordPress connection (404 otherwise). A 409 "Term sync is disabled" means it is switched off estate-wide: tell the user, don't retry.
- A summary is stored per keyword, so asking again returns the stored text at no cost. It does not write onto a bwp keyword row.

## Retry or cancel a scheduled task

Read the row first: `api_get("/scheduled-tasks/{id}")` for `state`, `error`, `retry_count` and `task_type`. The tools refuse a row from another domain.

| Tool | Allowed from | What happens |
|---|---|---|
| `task_retry(scheduled_task_id, force_refresh=false)` | `failed` only | The row is queued again and resumes at the stage that failed. `force_refresh=true` starts over and regenerates. A CADE article takes quota again, unless it failed after formatting and isn't forced |
| `task_cancel(scheduled_task_id)` | `queued`, `processing`, `failed` | The row becomes `canceled` and a running worker is stopped. A CADE article's quota comes back unless it had already reached the publish call |

Retry refusals (409): `not_retryable_state` (not failed), `retry_cap_exceeded` (already retried 5 times), `quota_exhausted`, `no_platform_connection`. Fix what `error` names before retrying, or the retry fails the same way. A WordPress 401 means the stored application password is wrong or revoked.

## After

- Scheduled rows (crawl, categorization, CSS analysis, retries): poll `api_get("/scheduled-tasks/{id}")` until `state` is `completed` or `failed`, and report `error` on failure.
- Keyword discovery and summaries answer with a Celery `task_id`: `api_get("/tasks/{task_id}")`.
- Term sync answers with one `task_id` per WordPress connection, but it keeps no job row, so `/tasks/{task_id}` answers 404 for it. Report it as queued.
- New articles, republishing and FAQs are `cade:content`.

## Not here

| The user wants | Go to |
|---|---|
| BRON link sync | `wp-migration:link-sync` (`link_sync`) |
| Cutover stages, rollback, blog-page visibility, the seo-service reconnect crawl (`seo_crawl`) | `wp-migration:site` |
| Post-cutover verification | `wp-migration:verification` |
| Merging duplicate WordPress terms | `cade:cade-terms` (Claude Code only) |
| Live Celery state, revoking a raw Celery task | `cade:cade-flower` (Claude Code only) |

`seo_crawl` is seo-service's reconnect crawl of a migrated site. It is not the CADE crawl above; don't swap one for the other.

## Never

- Queue a crawl, categorization or CSS analysis while a row of the same type is queued or processing.
- `api_write` to the job routes these tools cover.
- Loop over several domains without the user's go.
