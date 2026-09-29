---
name: watch
description: Run the post-migration watch checks over every finished Premium SEO → CADE migration (48 h close watch, then daily to day 14) and flag stop-rule breaches. Use for "watch check", "how are the migrated sites doing", "daily migration check", "any stop rules tripped". Read-only.
---

# wp-migration: watch

1. `whoami`; run Q1 (`references/queries.md` of `overview`) with `AND c.stage = 'finish'` — all operators unless the user says "mine".
2. Q2 for their bwp ids.
3. For each site compute `day = floor((now - stage_at)/1 day)`; phase = `close` if < 2 days else `daily`.
4. Flag (any one = **STOP** for that site, list first):
   - bwp not `2/1` (the crawler no longer sees our code: placements, link rendering and generation stop). Status 10 is the fall the plan names; status 8 is the DUMB road to the purge.
   - `verify_failed > 0`, or `verified_at` NULL after day 1.
   - `link_synced_at` NULL or older than 24 h.
   - `last_error` set.
5. For each STOP: say what it means and the first step — bwp not 2/1 → check the site loads and cade-seo is active, then dashboard **Reconnect**; verify failed → `wp-migration:verification`; link sync stale → `wp-migration:link-sync`.
6. **Wave stop rule.** The execution plan (`docs/premium-seo-migration/approach_2/10-execution-plan.md` §5 in cade-service) says any ONE stop rule halts the current step or wave, and "never start a wave while a site from a previous wave has **fallen** to status 10 after its cutover". So if **any** site is STOP on the bwp signal (fell to status 10, status 8 pending, or not back at 2/1 after its crawl), tell the user to pause new cutovers and alert the migration lead. Other STOPs (verify, link sync, error) halt that site's follow-up; escalate them to the lead as well. Signals the database cannot show — a security tool removing **cade-seo**, blank pages, a scanner stripping the footer block, a 404 ticket flood, a cut-over schedule auto-paused by the link sync, draft `bwp_bubblefeed` rows, rankings trailing peers — list at the end as "check by hand" for the operator; do not claim they are clear.
7. Summary line: `N in watch · X STOP · Y leaving watch today (day 14)`.
