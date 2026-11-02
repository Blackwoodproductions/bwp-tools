---
name: overview
description: Show every site on CADE's hosted renderer with its last rewrite check and the next action for each. Use for "what's the state of my hosted sites", "hosted overview/board/status", "which hosted sites need me", "hosted watch check". Reads only; a watch check re-runs the probe, which changes nothing on any site.
---

# hosted: overview

1. Call `hosted_sites()`. If it fails with "Pomerium identity", tell the operator to reconnect the cade-mcp connector (Settings → Connectors) and stop. An empty list means no site is on the hosted renderer yet: say so and point at `hosted:site`.
2. Render one table, failing rows first: `domain | mode | public base | check | next action`.
   - `check`: `status` with the age of `last_checked_at` (`unhealthy 2 h ago`), then `last_check.code · last_check.answered_by` when set (`not_deployed · vercel`).
3. **Next action**, first matching rule wins:

| Condition | Next action |
|---|---|
| `status = unchecked` | Edge not checked yet. If the client hasn't deployed the setup, hand it over (`hosted:site` step 4); once they have, `hosted:check {domain}`. |
| `status = unhealthy` | `last_check.message`, then `hosted:check {domain}` for the fix on that host. |
| `healthy` and `last_check.code` set | A warning on a passing check (e.g. `subdomain_proxied`): its fix from `hosted:check` → `references/checks.md`. |
| `healthy`, `last_checked_at` older than 7 days | Nothing re-checks a site on its own yet: run `hosted:check {domain}`. |
| otherwise | Live; nothing to do. |

4. End with counts: live / failing / not checked yet.

## Watch check

For "hosted watch check" or "check them all": call `hosted_check(domain)` for each row, one at a time (a few seconds each), then redo the table from the results. Over 20 rows, ask first. Report what changed since the table you showed before.

## The look of a site

The header and footer (shell capture) aren't in this plugin yet. If asked, say a site serves the simple shell until a captured one is approved, and that cade-service's runbook (§5) covers it for now.

## Never

- Create, change or delete anything from this skill. Hand off to `hosted:site` or `hosted:check`.
