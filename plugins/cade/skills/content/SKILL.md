---
name: content
description: Generate new BRON or CADE articles for {domain}, republish one CADE article, or retry a failed article, through the cade-mcp tools content_schedule, content_publish and task_retry. Reads the domain's connections, schedules, cutover record, BRON queue and CADE quota first, works out the platform and strategy pair (wordpress, php or hosted with cade or bron, or content_only with bron) and picks the one tool that fits. Use for "generate articles for {domain}", "queue 3 BRON articles on {domain}", "write CADE content for {domain}", "republish article {id} on {domain}", "the article never reached the site", "retry the failed article on {domain}". Never hand-builds api_write bodies and never sends WordPress credentials.
---

# cade: content

**Writes.** Generation spends LLM money, and a CADE article takes one unit of the customer's quota. One domain per confirmation. Every write tool repeats the domain in `confirm_domain`. If a tool says another operator is migrating the site, tell the user who, and pass `force=true` only when they say a hand-over was agreed. If any tool fails with "Pomerium identity", tell the user to reconnect the cade-mcp connector (Settings → Connectors) and stop.

## Read first

| Read | What to take from it |
|---|---|
| `api_get("/domains/{domain}/platform-connections")` | Each `platform_type` with `is_default`, `status`, and for WordPress whether `credentials` are stored |
| `api_get("/domains/{domain}/schedules")` | Every article and FAQ schedule row: `platform`, `strategy`, `enabled`, `publish_status` |
| `cutover_record(domain)` | `null` or `rolled_back`: not migrated. `finish`: BRON has moved to WordPress. `detect`, `prepare`, `remove`, or `running_stage` set: mid-migration |
| `seo_content_queue(domain)` | BRON work waiting in seo-service |
| `api_get("/domains/{domain}/subscription/detail")` | CADE quota: `quota_used` of `quota_limit`. Add `params={"platform": "php"}` or `"hosted"` for those pairs |

The schedule rows tell you which pairs the domain runs. `content_schedule` refuses a pair with no schedule row.

## The pairs

| Pair | Produces | Needs | Default for the platform |
|---|---|---|---|
| `wordpress` + `cade` | CADE articles posted to the client's WordPress; FAQs; billed against CADE quota | Default WordPress connection with stored username and application password | yes |
| `wordpress` + `bron` | BRON articles and feedtext on a migrated WordPress site, written back to the bwp row | WordPress connection with credentials; BRON work in seo-service; enabled schedule | no |
| `php` + `bron` (legacy `seomoney`) | BRON articles and feedtext written back to the bwp row; the site's feed script shows them | No connection; BRON work in seo-service; enabled schedule | yes |
| `php` + `cade` (legacy `seomoney_php`) | CADE articles on a pull PHP site; FAQs; billed against CADE quota | Default `php` connection (its `site_url`, no credentials) | no |
| `hosted` + `cade` | CADE articles served by the hosted renderer; FAQs; billed against CADE quota | Default hosted connection | yes |
| `hosted` + `bron` | BRON articles and feedtext served by the hosted renderer, written back to the bwp row | Hosted connection; BRON work in seo-service; enabled schedule | no |
| `content_only` + `bron` | BRON articles and feedtext written back to seo-service; shown on no site | No connection (CADE refuses one); BRON work in seo-service; enabled schedule | yes |

CADE fills in what each pair needs: WordPress credentials from the connection and the BRON envelope from seo-service. BRON pairs never touch CADE quota, and only CADE pairs have FAQs. A legacy `seomoney` row is the `php` + `bron` pair under its old name.

## Pick the tool

| The user wants | Call |
|---|---|
| New articles, any pair | `content_schedule(domain, confirm_domain, count, strategy, platform?)` |
| One CADE article republished (failed, or never reached the site) | `content_publish(domain, confirm_domain, article_id, platform, publish_status)`, `publish_status` = `publish` or `draft` (ask) |
| A failed scheduled article, BRON or CADE | `task_retry(domain, confirm_domain, scheduled_task_id)`; `force_refresh=true` only to regenerate from scratch |
| FAQs | No tool. They come from the domain's FAQ schedule (after each published article, or on its cadence), CADE pairs only |
| A BRON article republished | `task_retry` on its scheduled row, or the cutover stages (`wp-migration:site`). `content_publish` is CADE only: CADE rejects a BRON publish without the bwp envelope |

`content_schedule` details:
- `strategy` is required. Leave `platform` out and CADE uses the domain's own row for that strategy (an enabled one first). Name `platform` only when the domain has rows for the same strategy on two platforms. A legacy `seomoney` row is only reachable with `platform` left out.
- CADE pairs: `count` is articles (1 to 100). Quota is reserved up front. A paused CADE schedule still takes on-demand articles; `publish_status` comes from the schedule row.
- BRON pairs: `count` is work groups (up to 100). A group is a money keyword plus its supporting keywords, so one group can queue several articles. It may queue nothing when no work is pending or all of it is already in flight. Check `seo_content_queue` first.

Show the user the pair, the count and what it costs (CADE quota or none), then confirm before calling.

## Refusals

| Answer | Meaning |
|---|---|
| 409 no blog scheduling config | The domain has no schedule row for that pair. One has to be set up (dashboard or an admin) |
| 409 `SCHEDULE_OFF` | The BRON schedule is paused, and BRON never runs on demand while it is. Resuming it is the customer's or an admin's call. CADE refuses to enable `wordpress` + `bron` or `hosted` + `bron` schedules while that pair's switch is off |
| 409 `NO_DEFAULT_CONNECTION` | The pair needs a connection the domain doesn't have |
| 409 `quota_exhausted` / `no_active_subscription` | CADE quota is spent, or there is no subscription. Nothing was queued |
| 503 `WORK_SOURCE_UNREACHABLE` | seo-service didn't answer. Nothing was queued; try again later |

## After

- `content_schedule` answers with scheduled-task ids and `task_retry` with the re-queued row. Follow them with `api_get("/scheduled-tasks/{id}")` (`state`, `error`, `jobs`) or the list `api_get("/domains/{domain}/scheduled-tasks", params={"states": "failed"})`.
- `content_publish` answers with a Celery `task_id`: `api_get("/tasks/{task_id}")`.
- A failed row's `error` names the cause. Fix it before retrying. A WordPress 401 means the stored application password is wrong or revoked. Retrying a row is covered in `cade:jobs`.

## Never

- `api_write` to `/articles`, `/articles/bulk`, `/articles/faqs` or `/articles/publications`. Those routes skip the pair's checks; that is how WordPress publishes went out with no credentials.
- WordPress usernames or application passwords in any body or in chat.
- `content_publish` for a BRON pair.
- BRON content on a site that is mid-migration. Finish it or roll it back with `wp-migration:site` first.
- Loop over several domains without the user's go.
