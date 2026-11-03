# Check codes

CADE probes `{public base}/.cade/status?nonce=…`.
- Its status page echoes the nonce, the tenant it resolved and whether the origin secret arrived.
- When something else answers, CADE reads the response headers to tell who answered (`answered_by`):
  - `renderer`: the renderer's own "Not found";
  - `vercel`: `x-vercel-id`;
  - `netlify`: `x-nf-request-id`;
  - `cloudflare`: `cf-ray`;
  - `nginx` or `apache`: the `server` header;
  - `other`: anything else, builders included.
- In the subpath modes CADE also probes `/.cade/status` at the site root, which only the renderer can answer.

Recipes are in `hosted:site` → `references/edge-recipes.md`.

## Edge

| code | Means | Fix |
|---|---|---|
| `wrong_tenant` | The route reached the renderer, but with the wrong tenant: a typo in the domain inside the rewrite destination (`/t/{domain}/`), or `$host` giving `www.`. Also seen in the first minute after the connection is created, before the renderer picks it up. | Correct the destination to `/t/{domain}/` exactly, as in the host's recipe, and redeploy. If the connection is under a minute old, check again first. |
| `not_deployed` | The host answered 404 itself: the rewrite isn't deployed yet, or its `source`/`from`/`location` path differs from the public base. | Vercel and Netlify: confirm the deploy that has the rule is live (a preview deploy doesn't count). A Vercel dashboard rule whose destination ends in `:path*` instead of `$1` lands here too: the rule is live, but Vercel sends `:path*` literally and the renderer answers 404. nginx and Apache: reload, and compare the `location` or `ProxyPass` path with the public base. |
| `catch_all_first` | The host answered 200 with its own page: the rewrite is missing, or an SPA catch-all (`/* → /index.html`) runs before it. | Put our rule above the catch-all and redeploy. Netlify: first `[[redirects]]` or first `_redirects` line. Vercel, by where the client set it up: a dashboard routing rule must be **Published** and switched on (a Draft does nothing); `vercel.json` rewrites must come first and reach a Production deploy, and if the file has `routes`, Vercel ignores `rewrites`, so use the routes form; a `routes` entry must sit above `{ "handle": "filesystem" }`. |
| `slash_unrouted` | The bare status page reaches the renderer, but `{BASE}/.cade/status/` (trailing slash) gets the client's own page, so articles (`{slug}-{id}/`) do too. Almost always Vercel's old `{BASE}/:path*` rewrite behind a catch-all. Also returned, with no `answered_by`, when that probe can't be fetched. | Vercel `vercel.json`: change the rewrite to `{BASE}/:path(.*)` → `…/t/{TENANT}/:path` and deploy to Production, or switch to the dashboard routing rule. Other hosts: make the rule cover every path under `{BASE}`. |
| `http_status` | The host (or the renderer) answered another error status. | 5xx from the client's host: the rule is there but the proxy fails; check its upstream is `https://sites.imagehosting.space` with SNI on (nginx `proxy_ssl_server_name on`, Apache `SSLProxyEngine on`). 5xx with `answered_by: renderer`: ours; escalate. |
| `cf_no_route` | Cloudflare answered from the client's site: the request went through Cloudflare, but no Worker route matched. | Add the route `{domain}{BASE}*` to the Worker (and the `www.` one if the site serves www). |
| `not_proxied` | In Cloudflare mode, the answer didn't come through Cloudflare: the record isn't proxied, or the DNS change hasn't reached everyone. | Turn the proxy (orange cloud) on for the root record (and `www`). Right after a nameserver change, wait and check again. |
| `wrong_target` | In subdomain mode, something other than the renderer answers the subdomain. | Point the subdomain at `sites.imagehosting.space` with a CNAME (DNS only), and remove any A/AAAA record on that name. |
| `cached_copy` | Our status page came back, but a cached copy (old nonce): a CDN or cache in front stores `{BASE}/.cade/*`. | Exclude `{BASE}/.cade/*` from that cache (a Cloudflare Cache Rule "bypass", or the host's cache settings). `answered_by` names the cache. |
| `origin_secret_missing` | The route works and the tenant resolves, but the required `x-cade-origin` header didn't arrive. Articles answer 403 until it does. | Either an admin sets the secret in the host's secret store (Worker secret `ORIGIN_SECRET`, or the nginx/Apache environment variable in the recipe), or the connection stops requiring it. Never through this chat. |
| `route_too_broad` | The client's site root also reaches the renderer: the Worker route or rewrite covers the whole site, so their homepage now shows our pages. **Urgent for the client.** | Narrow the route to `{domain}{BASE}*` (Worker) or the rule to `{BASE}/*` (others). |
| `unreachable_dns` | The public base's host doesn't resolve. | Subdomain: add the CNAME. Right after adding it, wait for the TTL. |
| `unreachable_tls` | The TLS handshake failed: no valid certificate yet. | Subdomain: the record must be DNS only, and CAA (if any) must allow `letsencrypt.org`; the next check retries the certificate. Subpath: the client's own certificate is broken; their host must fix it. |
| `unreachable_connect` | Nothing accepted a connection on port 443. | The host is down, or DNS points at a dead address: check the A/CNAME records. |
| `unreachable_timeout` | No answer within 15 s. | Usually the client's host or its proxy hanging; check again, then look at their host's logs. |
| `not_public` | The public base isn't public https on the domain: plain http, or it resolves to a private address. | Fix the DNS record. CADE only accepts https bases, so plain http here means the host redirects to http. |
| `bad_redirect` | The public base redirects to another host, or more than 5 times. | Usually `{domain}` ↔ `www.{domain}`: recreate the connection on the host the site really serves (an admin deletes the old one), or stop the redirect for `{BASE}`. |

Warning on a passing edge check:

| code | Means | Fix |
|---|---|---|
| `subdomain_proxied` | The subdomain works, but through Cloudflare's proxy. The certificate's issue or renewal will fail. | Set the subdomain's record to DNS only (grey cloud). |

## Renderer

| code | Means | Fix |
|---|---|---|
| `renderer_down` | The renderer itself didn't serve the tenant's status page. | Ours, not the client's: tell the hosted-renderer owner. Nothing to send the client. |
