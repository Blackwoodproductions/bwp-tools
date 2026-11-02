# Onboard a site on the hosted renderer

A client's site, `example-clinic.test`, is a single-page app on Vercel. They want CADE's articles at `https://example-clinic.test/resources`, served by CADE's hosted renderer, with no WordPress involved.

This tutorial takes the site from nothing to live:
1. read the facts;
2. create the connection;
3. hand the client's developer one rewrite;
4. check it until it passes.

Every name here is made up.

## What you need

- The `hosted` plugin and the `cade-mcp` connector, signed in with your `@blackwoodproductions.com` account (top-level README).
- Creating the connection is for admins (`CADE_MCP_ADMINS`). Anyone else gets `… may not create a hosted connection; ask an admin` and nothing is sent. Reading and checking are open to every operator.
- Someone who can deploy the client's site: their developer, or the client.

## Step 1: the facts

Say "onboard example-clinic.test on hosted". The `hosted:site` skill reads two things:

```
seo_domain_facts("example-clinic.test")
api_get("/domains/example-clinic.test/platform-connections")
```

The domain is in bwp, and CADE knows it with no connection: good to go. Two other answers stop here.
- A WordPress connection: a domain has one platform, and there is no switch yet.
- A hosted connection already: the site is onboarded; skip to step 3.

## Step 2: base, mode and secret

The assistant asks three questions:

| Question | Answer here | Why |
|---|---|---|
| Public base | `https://example-clinic.test/resources` | A subpath keeps the articles on the client's own domain. Use `https://resources.example-clinic.test` only when nobody can change the site's routing. |
| Where the site is hosted | Vercel | `curl -sI https://example-clinic.test/` shows `x-vercel-id`. Vercel, Netlify and their own server take `subpath_host`; a builder behind Cloudflare takes `subpath_cloudflare`; a DNS-only change takes `subdomain`. |
| Origin secret | No | The secret is shared by every client and Vercel can only carry it written into `vercel.json`, which would publish it in the client's repo. The renderer's `robots.txt` and the articles' canonical already keep search engines on the client's address. |

## Step 3: create the connection

First the dry run. It sends nothing:

```
hosted_connect("example-clinic.test", confirm_domain="example-clinic.test", mode="subpath_host",
               public_base="https://example-clinic.test/resources", require_origin_secret=false, dry_run=true)
```

```json
{
  "dry_run": true,
  "would_create": {
    "platform_type": "hosted",
    "site_url": "https://example-clinic.test/resources",
    "platform_data": { "mode": "subpath_host", "require_origin_secret": false }
  },
  "existing": []
}
```

The assistant then asks you to **type the domain**, and sends exactly what you typed as `confirm_domain` with `dry_run=false`. A typo is refused with "nothing was sent". CADE answers with the new connection and its `id`.

Within a minute the renderer serves the tenant. Its pages use a plain header and footer (the simple shell) until a captured one is approved; that step is in cade-service's runbook §5 for now.

CADE refuses some inputs with a message that says what to change:
- a public base that doesn't fit the mode, for example a subpath base with no path, or `http://`;
- a second hosted connection;
- a domain on another platform.

## Step 4: the rewrite

The assistant fills in the Vercel recipe for the client's developer:

```json
{
  "rewrites": [
    { "source": "/resources/:path*", "destination": "https://sites.imagehosting.space/t/example-clinic.test/:path*" },
    { "source": "/resources", "destination": "https://sites.imagehosting.space/t/example-clinic.test/" }
  ]
}
```

These go **first** in the existing `rewrites`, above the app's own `/(.*) → /index.html`, then the site is redeployed. The skill stops here until you say it's deployed.

Other hosts (Netlify, a Cloudflare Worker, nginx, Apache, a subdomain's DNS record) have their own block in `plugins/hosted/skills/site/references/edge-recipes.md`.

## Step 5: check it

Say "check the rewrite rules for example-clinic.test":

```
hosted_check("example-clinic.test")
```

The developer deployed to a preview, not to production:

```
FAIL edge: not_deployed · vercel
Vercel answered HTTP 404: the /resources rewrite is not deployed yet, or its path is wrong
```

`answered_by: vercel` says the request reached Vercel and Vercel itself had no rule for `/resources`. The fix is to promote the deploy that carries the rule. Other answers point elsewhere:
- `catch_all_first`: the rule sits below the app's catch-all.
- `wrong_tenant · renderer`: a typo in `/t/example-clinic.test/`.
- `route_too_broad`: the rule covers the whole site.

The full list is in `plugins/hosted/skills/check/references/checks.md`.

After the promote, "check it again":

```
PASS edge · PASS renderer
```

The site is live: `https://example-clinic.test/resources` lists the client's articles, and each article names itself canonical on the client's domain.

## Step 6: keep an eye on it

"What's the state of my hosted sites" lists every hosted site:

| domain | mode | public base | check | next action |
|---|---|---|---|---|
| example-clinic.test | subpath_host | https://example-clinic.test/resources | healthy 2 min ago | Live; nothing to do. |

Nothing re-checks a site on its own yet. "Hosted watch check" runs the check on every site; do it after a client redeploys.
