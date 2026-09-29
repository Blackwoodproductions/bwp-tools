---
name: overview
description: Show the state of every Premium SEO → CADE site migration in flight and the next action for each. Use when the user asks "what's going on with my migrations", "migration overview/board/status", "which sites are waiting on me", "what's stuck", or starts a migration session. Reads only; never queues anything.
---

# wp-migration: overview

1. Call `cade-mcp` `whoami` → the operator's email. If it fails with "Pomerium identity", tell them to reconnect the cade-mcp connector (Settings → Connectors) and stop.
2. Run Q1 from `references/queries.md` via `execute_sql_cade`, filtered to that email (append the `AND …` before the ORDER BY line) unless the user asked for "all"/"everyone"/"the team".
3. Run Q2 via `execute_sql_seo` for the `bwp_domain_id`s returned (skip NULLs; if the list is empty, skip Q2 entirely, never send `IN ()`).
4. Render one table, stalled and errored rows first: `domain | owner | stage | now | bwp | next action`.
   - `now`: `running <stage> (Xm)`, `STALLED <stage> (Xm)`, `error`, `verifying`, or `idle`.
   - `bwp`: `status/wp_plugin`, e.g. `2/1`.
5. **Next action** — first matching rule wins:

| Condition | Next action |
|---|---|
| `stalled` | Stage stuck > 15 min: re-queue the same stage (`wp-migration:site`) — CADE allows it once stale. |
| `running_stage` set | Wait; check again in a few minutes. |
| `last_error` set | Explain it using `wp-migration:site` → `references/codes.md`; the fix is usually "fix X, then re-run `<stage>`". |
| `stage` NULL | First detect was blocked (see last_error); fix the blocker and run `detect` again. |
| `stage = detect` | Run `prepare` (writes to the live site). |
| `stage = prepare` | Human: switch Premium SEO **off** in wp-admin → Plugins (skip if detected_state is A or B), then run `remove`. |
| `stage = remove` | Human: click **Reconnect** in the dashboard for this site (the crawl), then run `finish`. |
| `verify_running_since` set and < 15 min old | Verification running; wait. |
| `stage = finish`, `verified_at` NULL or `verify_failed > 0` | Run `wp-migration:verification`. |
| `stage = finish`, verified clean | In watch (day N of 14): `wp-migration:watch`. |

6. End with counts: in flight / waiting on you / stuck / in watch. Never queue anything from this skill — hand off to `wp-migration:site`.
