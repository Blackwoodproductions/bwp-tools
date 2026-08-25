# The WordPress REST surface this skill drives

Routes, permissions, side effects, and a symptom→cause table. Read when a run reports
something other than a clean `updated/no_block` split.

## Routes

| `--post-type` | CPT slug | REST route | Registered by |
|---|---|---|---|
| `post` | `post` (core) | `/wp-json/wp/v2/posts` | WordPress core |
| `faq` | `cade_faq` | `/wp-json/wp/v2/faqs` | `cade-seo/src/Faq/FaqPostType.php` |

`POST_TYPE_ROUTES` in the script duplicates this two-entry map instead of importing
`WordPressEndpoints` from `app/infrastructure/adapters/wordpress/constants.py`, because
that import pulls `app.infrastructure.adapters.__init__` → the AI adapters → settings,
which would make `--selftest` require a valid `.env`. `reconcile_wp.py` made the same
call with its `ROUTE` map. **`constants.py` is the source of truth if routes change.**

Not covered: `cade_kb` → `/kb-articles` (legacy `cade-wp-plugin`) carries the same
injected CSS. Add it to `POST_TYPE_ROUTES` if it's ever needed.

### Why `/faqs` and not `/cade_faq`

`register_post_type('cade_faq', [...])` sets `'rest_base' => 'faqs'`, and `rest_base`
wins over the slug for the REST path. Also set: `show_in_rest => true`,
`supports` including `'editor'` (so `content` is readable *and* writable),
`capability_type => 'post'` with no `capabilities` override and `map_meta_cap` unset —
so the caps are literally the built-in post caps. Any Editor/Administrator app password
works; no custom capability handling needed.

The same slug is registered by the legacy `cade-wp-plugin` at `init` priority 10, and
`FaqPostType::register()` guards with `post_type_exists()` at priority 20. On a site
running both, the legacy registration wins — the arg arrays are equivalent (same
`rest_base`, `supports`, `capability_type`), so `/faqs` behaves identically either way.

## Reading

`GET /wp-json/wp/v2/{route}?context=edit&status=<statuses>&per_page=100&page=N`

- **`context=edit` is mandatory** — it is the only way `content.raw` (the raw Gutenberg
  markup) comes back. Without it you get `content.rendered`, which is post-filter HTML
  and useless for a block edit.
- Pagination is driven by the **`X-WP-TotalPages`** response header. `per_page=100` is
  WP's maximum.
- Default statuses are `publish,future,draft,pending,private`. **`trash` is
  deliberately excluded** — cleaning trashed posts is wasted writes.

## Writing

`POST /wp-json/wp/v2/{route}/{id}` with body `{"content": "<raw gutenberg>"}`.

WP treats `POST` to a single-post route as an update. **Only `content` is sent**, so
`status`, `date`, `slug`, `author`, meta and taxonomies are untouched.

**Never add a `meta` object.** cade-seo registers its meta with
`additionalProperties: false` (`src/Core/Plugin.php`, `src/Seo/KeywordTitle.php`), so a
single unregistered key rejects the *entire* request with `400 rest_invalid_param`.

## Side effects of every write

1. **A revision row.** The FAQ CPT has `revisions` in `supports`. A large sweep grows
   `wp_posts` accordingly.
2. **A callback into cade-service.** `cade-seo/src/StateChange/PublicationSyncListener.php`
   hooks `post_updated` for exactly `['post', 'cade_faq']`. When `post_content` changes
   it sets `content_updated: true`, writes `_cade_seo_pending_sync` postmeta, and
   schedules a WP-Cron single event ~10s out that POSTs a state-change payload to
   cade-service with bounded retry/backoff. **A 200-FAQ sweep queues 200 callbacks.**
   It self-disables when `SyncConfig::isConfigured()` is false; assume it is configured
   on prod.
3. **Front-end cache lag.** REST output updates immediately; a page cache or CDN may
   serve the old CSS until it expires.

## How it reaches the site

Via `WordpressApiClient` (`app/infrastructure/adapters/wordpress/worpress_api_client.py`),
which brings, unconfigured:

- `curl_cffi` with `impersonate="chrome"` — gets past Cloudflare/WAF fingerprinting.
- Basic auth: `base64(user:app_password)` in an `Authorization: Basic` header.
- **Direct-then-proxy fallback.** A per-IP WAF `401`, a non-JSON `403`, a captcha, or an
  IP block on the direct egress re-runs the request through each `CRAWLER_PROXY_URLS`
  proxy from `.env`.
- A per-domain `pybreaker` circuit breaker, `@retry_with_exponential_backoff`
  (5 retries), and a 15s per-request timeout.

Note `format_domain()` collapses the input to `scheme://netloc`, so **any path in
`--site-url` is silently dropped** — a WP install in a subdirectory is not reachable
this way.

## Symptom → cause

| What you see | What it means |
|---|---|
| `raw_missing > 0` | `content.raw` was withheld. The app-password user lacks edit rights on that post type — **not** "nothing to clean". |
| `no_block = total` | Either the posts genuinely have no style block (fine, or already cleaned), or you are reading `content.rendered`. Check `raw_missing` first. |
| `route /faqs not registered on this site — skipped` | Neither cade-seo nor cade-wp-plugin is active. Not fatal; the sweep continues to the next type. |
| `auth check failed: … 401` on every egress | The app password really is wrong. A 401 on *only* the direct path is a per-IP WAF block and is retried through the proxies automatically. |
| `cannot reach <url>` | Host unreachable, or every egress blocked. Check the URL first — a typo lands here. |
| `400` on update | Something other than `content` went in the body. Only `content` is safe (see meta note above). |
| `already_current` climbing | Replace mode only — those posts already hold the exact `--html-block`. This is what makes re-runs idempotent. |
| Style block still visible on the site | Front-end cache/CDN, or the block was `<p>`-wrapped by a Classic-Editor round-trip. Re-fetch `?context=edit` to see the truth. |

## Rollback

`misc/wp-scripts/backups/<host>-<post-types>-<UTC stamp>.json` holds
`[{post_type, post_id, link, old_block}]`, written in **both** dry-run and apply mode,
in a `finally` so a mid-sweep crash still records what was already written.

To roll a post back: `GET /{route}/{id}?context=edit`, splice `old_block` back where the
new one is (or where it was removed), `POST` it back. Same mechanics as the script.
