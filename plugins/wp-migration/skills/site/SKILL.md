---
name: site
description: Drive one site through the Premium SEO → CADE migration runbook (plan → detect → prepare → switch Premium SEO off → remove → reconnect crawl → finish → verify), or roll it back. Use for "migrate {domain}", "next step for {domain}", "run prepare on {domain}", "why did {domain} fail", "roll back {domain}", or several domains at once ("kick prepare on a.com, b.com").
---

# wp-migration: site

**Prod only. `prepare`, `remove`, `finish`, `rollback`, link sync, a live `seo_crawl` and `seo_keyword_status` write to live customer sites and bwp.** Never run anything the user didn't ask for; one stage per confirmation.

## Before any write
1. `cutover_plan(domain)`. If `blocked_on` is set → explain it from `references/codes.md` and stop.
2. Show the plan briefly (state, premium_seo running?, placeholder pages with body) and the stage you're about to queue, and **ask the user to confirm**. Then call the write tool with `confirm_domain` = the domain.
3. If the tool says another operator owns the site: tell the user who; only pass `force=true` if they explicitly say they agreed a hand-over.

## The runbook
| # | Step | How | Done when |
|---|---|---|---|
| 1 | detect | `cutover_stage(stage="detect")` | record `stage = detect` |
| 2 | prepare | `cutover_stage(stage="prepare")` (ask: publish or draft? default publish) | `stage = prepare`, no last_error |
| 3 | ⏸ **Human** | "Switch Premium SEO **off** in wp-admin → Plugins → Deactivate." Skip when detected_state is A or B. | `cutover_plan` shows premium_seo not running. Stop here — don't queue the next stage until the user says it's done. |
| 4 | remove | `cutover_stage(stage="remove")` | `stage = remove` |
| 5 | reconnect | `seo_crawl(dry_run=true)` first (~30 s, writes nothing): show `verdict` and `predictedStatus`. Only if it predicts 2 and the user confirms, `seo_crawl(dry_run=false)`. If the dry run predicts anything else, stop and explain; don't run it live | the live crawl answers `status 2`, `wpPlugin 1` (or `seo_domain_facts` / overview Q2 shows `2/1`) |
| 6 | finish | `cutover_stage(stage="finish")` | `stage = finish` |
| 7 | verify | hand off to `wp-migration:verification` | 0 FAIL |

After queueing, poll `cutover_record` every ~30 s (tell the user you're waiting) until `running_stage` is null; then report `stage` moved or explain `last_error` via `references/codes.md`. A stage running > 15 min is stalled: tell the user; re-queue only after they confirm.

A refusal that starts `CADE <status>:` is CADE's own message; match it against `references/codes.md`. Anything else is the connector refusing (wrong `confirm_domain`, site owned by another operator): relay it as is.

## Several sites
Queue the same stage for each confirmed domain one after another (each is a separate background job), then show `wp-migration:overview` instead of polling each one.

## Rollback
1. Human first: switch Premium SEO back **on** in wp-admin. (State A sites can't be rolled back — say so and stop.)
2. Confirm with the user, then `cutover_stage(stage="rollback")` → poll. It drafts posts and removes the footer; nothing is deleted.
3. `cutover_record` should read `stage = rolled_back`.

Note: a `blocked_on` in the plan (e.g. `seo_service_unreachable`) does not by itself stop a rollback. The "Before any write" blocker stop applies to forward stages; for rollback the gate is Premium SEO being switched back on (CADE refuses with `CutoverNotRollbackable` otherwise).

## Never
- Run `seo_crawl(dry_run=false)` without a dry run in the same session predicting status 2.
- Run on staging (shares prod bwp/seo-service) — the connector is prod-only anyway.
- Edit a cut-over site's content in Content Management during the migration.
