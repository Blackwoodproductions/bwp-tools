# Choosing the delivery mode

Prefer a subpath (`/articles`): the articles then sit on the client's own domain. Use a subdomain only when nobody can change the site's routing.

| The client's site | Mode | Public base | Origin secret |
|---|---|---|---|
| On Vercel or Netlify, or on their own server (nginx, Apache) whose config someone can change | `subpath_host` | `https://{domain}/articles` | No (Vercel and Netlify can't hold it) |
| On Railway, and someone can add a route to the app's Node server | `subpath_host` | `https://{domain}/articles` | No (the route doesn't send it) |
| A builder (Framer, Webflow, Lovable, Wix …) whose DNS is on Cloudflare, root record proxied (orange cloud) | `subpath_cloudflare` | `https://{domain}/articles` | Only if an admin hands it over as a Worker secret |
| Nobody can change routing, but someone can add a DNS record | `subdomain` | `https://articles.{domain}` | Never |

## From `hosted_detect`

`hosted_detect(domain)` reads public DNS, the same read the dashboard's hosted setup shows.

| It found | Suggest |
|---|---|
| `cloudflareProxied: true` | `subpath_cloudflare`: Cloudflare is in front, so a Worker runs. |
| `host: vercel` or `netlify`, not proxied | `subpath_host`: a rewrite on that host. A Worker would not run until the root record is proxied. |
| `host: railway`, not proxied | `subpath_host`: a route in the app (the Node / Express recipe). If nobody can change the app: Cloudflare in front (`subpath_cloudflare`), or `subdomain`. |
| `cloudflareDns: true`, no host, not proxied | Ask: the root record can be proxied for a Worker (`subpath_cloudflare`), or it's their own server (`subpath_host`), or `subdomain`. |
| Nothing | Ask where it's hosted (below). |

A builder (Framer, Webflow, Lovable, Wix …) shows up as nothing or as Cloudflare. Never pick `subdomain` just because the site is on a builder: behind proxied Cloudflare it takes `subpath_cloudflare`.

Railway's edge can't rewrite or proxy a path (its Edge rules only block, allow, challenge, redirect or override caching), so a Railway site takes a route in its app, Cloudflare in front, or `subdomain`. Behind proxied Cloudflare it's the Cloudflare case: detection reads no host, and the Worker runs before Railway sees the request.

If detection found nothing and the operator doesn't know where the site is hosted, the homepage's headers say: `curl -sI https://{domain}/`.
- `x-vercel-id` means Vercel.
- `x-nf-request-id` means Netlify.
- `x-railway-request-id` or `server: railway-…` means Railway. With `cf-ray` too, it's Railway behind Cloudflare: the Worker (`subpath_cloudflare`).
- `cf-ray` means Cloudflare is in front; the `server` header names what's behind it.
- `server: nginx` or `server: Apache` means their own server.

A builder that isn't behind Cloudflare, where no one can change routing, takes `subdomain`.

## What CADE accepts

CADE refuses anything else with a `CADE 422` or `CADE 409` message.

- The public base is `https`.
- `subpath_host` and `subpath_cloudflare`: a path on `{domain}` or `www.{domain}` (`https://{domain}/articles`, `https://www.{domain}/articles`). Use whichever host the site actually serves; the other usually redirects to it.
- `subdomain`: a host under `{domain}` with no path, not `www` (`https://articles.{domain}`).
- One hosted connection per domain. None on a domain that already has a WordPress (or other) connection.
- `front_door` is not built.
