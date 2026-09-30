---
name: verification
description: Run and analyse the post-cutover verification for a migrated site, and say exactly what to do about each failing row. Use for "verify {domain}", "is {domain} done", "why did verification fail", "check the migration of {domain}".
---

# wp-migration: verification

Reads the live site through CADE and writes nothing to it. Row-by-row explanations are in `references/rows.md`; error messages are in `wp-migration:site` → `references/codes.md`.

1. `cutover_record(domain)`: must be at `finish` (or `rolled_back` to confirm a rollback). Otherwise say which stage it is at and hand off to `wp-migration:site`.
2. Confirm with the user, then `start_verification(domain, confirm_domain=domain, profile="rescue")`. Use `full` only if asked: it expects the schedule enabled.
3. Poll `verification_report(domain)` every ~30 s until it is no longer running (a run reads the site ~200 times, a few minutes). Tell the user you are waiting.
4. Report `PASS n · FAIL n · SKIP n · MANUAL n`. For each FAIL, from `references/rows.md`: the row, what it checked, the likely cause and the concrete next step (use the row's `hint` from the report when it has one). Group the WAF-prone rows (4, 5, 17) separately as "check in a browser first". List MANUAL rows as "a person checks", not as failures.
5. 0 FAIL on a rescue run unlocks the site's schedule in the dashboard: say so. Any FAIL: the site stays locked; list the actions in order (fix, re-run the stage named, verify again).

Row 10 and 11 failures go to `wp-migration:link-sync`. Where `references/rows.md` says "escalate to the migration lead", say exactly that.
