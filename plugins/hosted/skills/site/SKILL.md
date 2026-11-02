---
name: site
description: Onboard one site onto CADE's hosted renderer. Read its facts, agree the public base and mode, create the hosted connection (admins), and hand over the exact rewrite or DNS change for the client's host (Vercel, Netlify, Cloudflare Worker, nginx, Apache, DNS). Use for "onboard {domain} on hosted", "set up the hosted renderer for {domain}", "connect {domain} to hosted", "edge setup for {domain} on Vercel".
---

# hosted: site

**Prod only. `hosted_connect(dry_run=false)` writes to prod CADE: the renderer serves the tenant within a minute, and the capture worker starts on the client's site.** Never act beyond what the operator asked; one write per confirmation.

## Before the write

- The operator **types the domain** to confirm. Pass exactly what they typed as `confirm_domain`; never fill it in yourself. A mismatch is refused with "nothing was sent".
- Only admins (`CADE_MCP_ADMINS`) may create a connection. Anyone else gets a refusal: relay it and stop.

## The runbook

| # | Step | How | Done when |
|---|---|---|---|
| 1 | Facts | `seo_domain_facts(domain)` and `api_get("/domains/{domain}/platform-connections")`. | bwp shows the domain, and CADE knows it (a 404 means it doesn't: stop). It has no connection yet. A WordPress or other connection: stop, because a domain has one platform and there is no switch. A hosted one already: it's onboarded; go to step 4 or `hosted:check`. |
| 2 | Agree | Ask for the public base (`https://{domain}/resources` or `https://resources.{domain}`), where the site is hosted, and whether to require the origin secret (below). Pick the mode from `references/modes.md` and say why. | The operator agrees base, mode, host and secret. |
| 3 | Create | `hosted_connect(domain, confirm_domain, mode, public_base, require_origin_secret, dry_run=true)`: show `would_create` and `existing`. Ask the operator to type the domain, then repeat the call with `dry_run=false` and their text as `confirm_domain`. | The answer carries the new connection's `id`. |
| 4 | Edge setup | The host's block in `references/edge-recipes.md`, with `{TENANT}` = the domain and `{BASE}` = the public base's path (e.g. `/resources`). Hand it over whole, with the block's notes. | The operator has the snippet for the client's developer. |
| 5 | ⏸ **Human** | The client's developer deploys it. Stop here until the operator says it's live. | The operator says it's deployed. |
| 6 | Verify | Hand off to `hosted:check`. | `PASS edge · PASS renderer` |

The site serves the simple shell (a plain header and footer) until a captured one is approved. That step isn't in this plugin yet: cade-service's runbook §5 covers it.

A refusal starting `CADE <status>:` is CADE's own message, for example a public base that doesn't fit the mode, or a second hosted connection: explain it and fix the input. Anything else is the connector refusing: relay it as is.

## The origin secret

The renderer can require an `x-cade-origin` header carrying one secret shared by every tenant.

- **Default: don't require it** (`require_origin_secret=false`). `robots.txt` on the renderer keeps crawlers off its own copies, and every article names the client's address as canonical.
- Require it only when an admin decides to and hands the value over themselves, outside Claude, into the host's secret store (a Worker secret, nginx or Apache environment).
- You never see, print or ask for the value, and it never goes into a committed file such as `vercel.json` or `netlify.toml`. Vercel and Netlify rewrites can't read a secret store, so those hosts run without it.
- Subdomain mode never uses it.

## Never

- Print, ask for, or accept the origin secret.
- Create a second hosted connection, or one on a domain with another platform's connection.
- Use `api_write` on `platform-connections` routes: `hosted_connect` is the gated path.
- Offer `front_door`: it isn't built.
- Change the client's host from this skill, even when a Vercel or Cloudflare tool is connected: hand over the snippet.
