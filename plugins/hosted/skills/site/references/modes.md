# Choosing the delivery mode

Prefer a subpath (`/resources`): the articles then sit on the client's own domain. Use a subdomain only when nobody can change the site's routing.

| The client's site | Mode | Public base | Origin secret |
|---|---|---|---|
| On Vercel or Netlify, or on their own server (nginx, Apache) whose config someone can change | `subpath_host` | `https://{domain}/resources` | No (Vercel and Netlify can't hold it) |
| A builder (Framer, Webflow, Lovable, Wix …) whose DNS is on Cloudflare, root record proxied (orange cloud) | `subpath_cloudflare` | `https://{domain}/resources` | Only if an admin hands it over as a Worker secret |
| Nobody can change routing, but someone can add a DNS record | `subdomain` | `https://resources.{domain}` | Never |

If the operator doesn't know where the site is hosted, the homepage's headers say: `curl -sI https://{domain}/`.
- `x-vercel-id` means Vercel.
- `x-nf-request-id` means Netlify.
- `cf-ray` means Cloudflare is in front; the `server` header names what's behind it.
- `server: nginx` or `server: Apache` means their own server.

A builder that isn't behind Cloudflare, where no one can change routing, takes `subdomain`.

## What CADE accepts

CADE refuses anything else with a `CADE 422` or `CADE 409` message.

- The public base is `https`.
- `subpath_host` and `subpath_cloudflare`: a path on `{domain}` or `www.{domain}` (`https://{domain}/resources`, `https://www.{domain}/resources`). Use whichever host the site actually serves; the other usually redirects to it.
- `subdomain`: a host under `{domain}` with no path, not `www` (`https://resources.{domain}`).
- One hosted connection per domain. None on a domain that already has a WordPress (or other) connection.
- `front_door` is not built.
