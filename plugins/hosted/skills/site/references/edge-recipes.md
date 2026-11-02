# Edge setup per host

Fill in two values before handing a block over:
- `{TENANT}` is the domain exactly as CADE has it, e.g. `example-clinic.test`, never `www.`. A typo there is the `wrong_tenant` failure.
- `{BASE}` is the public base's path, e.g. `/resources`.

The renderer's host is always `sites.imagehosting.space`, and every block sends `{BASE}` and everything under it there.

Two rules hold for every subpath host:
- **Order:** our rule goes **above** any catch-all (an SPA's `/* → /index.html`). Below it, the site answers with its own page: `catch_all_first`.
- **Media:** the rule must cover everything under `{BASE}`, `{BASE}/media/*` included, not only article paths.

When the block is live, run `hosted:check {domain}`.

The Vercel, Netlify, Cloudflare Worker and nginx blocks are the ones proved on real hosts in the hosted-renderer proof (Tier B). The Apache block has not run yet.

## Vercel (`subpath_host`)

In `vercel.json`, as the first entries of `rewrites`:

```json
{
  "rewrites": [
    { "source": "{BASE}/:path*", "destination": "https://sites.imagehosting.space/t/{TENANT}/:path*" },
    { "source": "{BASE}", "destination": "https://sites.imagehosting.space/t/{TENANT}/" }
  ]
}
```

Merge these into the existing `rewrites`; don't replace the file. Then redeploy. Vercel can't send the origin secret from a secret store, so this connection runs with `require_origin_secret=false`.

## Netlify (`subpath_host`)

In `netlify.toml`, above any `from = "/*"` rule:

```toml
[[redirects]]
  from = "{BASE}/*"
  to = "https://sites.imagehosting.space/t/{TENANT}/:splat"
  status = 200
  force = true

[[redirects]]
  from = "{BASE}"
  to = "https://sites.imagehosting.space/t/{TENANT}/"
  status = 200
  force = true
```

The same in a `_redirects` file, as its first lines:

```
{BASE}/*  https://sites.imagehosting.space/t/{TENANT}/:splat  200!
{BASE}    https://sites.imagehosting.space/t/{TENANT}/        200!
```

Then redeploy. No origin secret, as for Vercel.

## Cloudflare Worker (`subpath_cloudflare`)

1. The client's root record (and `www`, if the site serves it) must be **proxied** (orange cloud). A Worker only runs on proxied records; otherwise the check says `not_proxied`.
2. Workers & Pages → Create → Worker, with this script:

```js
const RENDERER = "https://sites.imagehosting.space";
const TENANT = "{TENANT}";
const BASE = "{BASE}";

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    // The route is {BASE}*, which also matches e.g. {BASE}-old: hand those back to the site.
    if (url.pathname !== BASE && !url.pathname.startsWith(BASE + "/")) return fetch(request);
    const rest = url.pathname.slice(BASE.length).replace(/^\//, "");
    const headers = new Headers(request.headers);
    if (env.ORIGIN_SECRET) headers.set("x-cade-origin", env.ORIGIN_SECRET);
    return fetch(`${RENDERER}/t/${TENANT}/${rest}${url.search}`, { method: request.method, headers, redirect: "manual" });
  },
};
```

3. The Worker's Settings → Domains & Routes → add the route `{TENANT}{BASE}*` on the zone `{TENANT}`. Add `www.{TENANT}{BASE}*` too if the site serves `www`. **Never `{TENANT}/*`:** that hands the client's whole site to the renderer, and the check says `route_too_broad`.
4. Only if the connection requires the origin secret: an admin adds the Worker secret `ORIGIN_SECRET` themselves (Settings → Variables and Secrets, type Secret). It's never pasted into the script or into this chat.

## nginx (`subpath_host`)

In the site's `server` block:

```nginx
location {BASE}/ {
    resolver 1.1.1.1 8.8.8.8 valid=300s;
    rewrite ^{BASE}/(.*)$ /t/{TENANT}/$1 break;
    proxy_pass https://sites.imagehosting.space;
    proxy_ssl_server_name on;
    proxy_set_header Host sites.imagehosting.space;
}
location = {BASE} { return 301 {BASE}/; }
```

Put `{TENANT}` in literally: `$host` breaks on `www.`. Reload nginx (`nginx -t && nginx -s reload`).

Only if the connection requires the origin secret, add `proxy_set_header x-cade-origin "${CADE_ORIGIN_SECRET}";` inside the `location`. The value comes from the environment through the official image's `/etc/nginx/templates/*.template` substitution, never written into the file. An admin sets that variable.

## Apache (`subpath_host`, not yet proved on a real host)

Needs `mod_proxy`, `mod_proxy_http`, `mod_ssl` and `mod_headers`. In the site's `VirtualHost`:

```apache
SSLProxyEngine on
ProxyPreserveHost Off
ProxyPass        "{BASE}/" "https://sites.imagehosting.space/t/{TENANT}/"
ProxyPassReverse "{BASE}/" "https://sites.imagehosting.space/t/{TENANT}/"
RedirectMatch 301 "^{BASE}$" "{BASE}/"
```

Reload Apache (`apachectl configtest && apachectl graceful`). Tell the operator this block is unproven: if `hosted:check` fails, the answer it gives (who answered, which code) is the starting point.

Only if the secret is required: `RequestHeader set x-cade-origin "${CADE_ORIGIN_SECRET}"`, with the variable set in Apache's environment (`envvars`) by an admin, never in the file.

## DNS (`subdomain`)

At the client's DNS provider:

| Type | Name | Value | Note |
|---|---|---|---|
| CNAME | `resources` (the subdomain's label) | `sites.imagehosting.space` | On Cloudflare: **DNS only** (grey cloud). Proxied, certificate issue and renewal fail (`subdomain_proxied`). |
| CAA | `{TENANT}` | `0 issue "letsencrypt.org"` | Only if the domain already has CAA records. With none, nothing is needed. |

The first check after the record resolves also triggers the certificate. A `unreachable_dns` or `unreachable_tls` right after the change usually means "wait for the record's TTL", not "undo it".
