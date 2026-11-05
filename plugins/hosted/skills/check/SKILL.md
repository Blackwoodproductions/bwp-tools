---
name: check
description: Check a hosted site's rewrite rules or DNS and explain each failure: who answered (the renderer, Vercel, Netlify, Railway, Cloudflare, nginx, Apache), whether the route reached the renderer, the tenant resolved and the origin secret arrived, and the exact fix for that host. Use for "check the rewrite rules for {domain}", "is {domain} live on hosted", "why is {domain} unhealthy", "verify hosted {domain}", "check it again".
---

# hosted: check

A probe: CADE fetches the public base's status page with a fresh nonce and checks the renderer. It changes nothing on the client's site. Any operator may run it, and it needs no confirmation.

1. Call `hosted_check(domain)`. It always runs fresh. The answer comes back as `status` plus two checks:
   - `edge`: the public base reaches the renderer;
   - `renderer`: the renderer serves this tenant.

   Each check carries `passed`, `code`, `answered_by` and `message`. "has no hosted connection" means the site isn't onboarded: hand off to `hosted:site`.
2. Report one line: `PASS edge · PASS renderer`, or the failing check with its code and who answered (`FAIL edge: not_deployed · vercel`).
3. For each failing check, from `references/checks.md`, in this order:
   - who answered;
   - what it means: did the route reach the renderer, did the tenant resolve, did the origin secret arrive (for a subdomain, DNS and TLS);
   - the fix for that host, quoting the matching block from `hosted:site` → `references/edge-recipes.md` when the fix is a config change.

   Use CADE's `message` as the evidence: it names the host, path or status it saw.
4. A passing check with a `code` is a warning (`subdomain_proxied`): give its fix, but the site is live.
5. When the operator says it's fixed, run it again. `unreachable_dns` or `unreachable_tls` right after a DNS change is usually "correct, wait for the TTL" (up to an hour). Say that instead of asking them to undo the change.

`renderer_down` is ours, not the client's. Say so, and point at the hosted-renderer box (cade-service runbook). Don't send the client a fix.

## Never

- Call a site healthy from `hosted_sites` alone when the operator asked for a check: run `hosted_check`.
- Change the client's host or DNS yourself: hand over the fix.
- Guess a fix for a code that isn't in `references/checks.md`: give CADE's message and escalate to the hosted-renderer owner.
