---
name: reconcile-wp-content
description: >-
  Reconcile a single domain's CADE content_publications (status + platform_url)
  AND the crawl pages.url against the LIVE WordPress site, using an authenticated
  WordPress application password so it reads each post's exact status
  (publish/draft/pending/private/future/trash) and real permalink. Use this
  skill whenever the user says the DB status or platform_url doesn't match the
  real site, wants to fix/sync content_publications with WordPress, asks to
  "reconcile wp content", "fix the platform_url mismatches", "the posts are
  published on WP but the db says draft", "update the db to match the live
  site", "check what status these posts actually are on wordpress", or invokes
  "reconcile-wp-content" directly. Takes just the domain — site-url, user and
  application password fall back to that domain's default WordPress
  `platform_connection` (decrypted with the Fernet key in
  `.claude/skills.settings.{env}.json`) whenever they are not passed
  explicitly. Reaches WordPress the way CADE does (curl_cffi impersonation +
  CRAWLER_PROXY_URLS proxy fallback). Read-only by default (writes reconcile SQL
  + a JSON report); only `--apply --confirm` writes to the prod DB.
---

# reconcile-wp-content

> **Paths:** `<skill-dir>` is this skill's directory — Claude Code prints it as *Base directory for this skill* when the skill loads (under `~/.claude/plugins/cache/bwp-tools/cade/<version>/skills/<name>`). Run commands from the **cade-service repo root** so `venv/bin/python`, `.claude/skills.settings.{env}.json` and `misc/` resolve. Examples write `python` — use the cade-service `venv/bin/python`. Settings blocks per skill: see the bwp-tools README.

Point it at one domain with a WordPress application password; it makes CADE's
`content_publications` and `pages` rows reflect what WordPress actually shows.

## What it does

For every `content_publications` row on the domain (platform `wordpress`, with a
`platform_post_id`), it asks the live WP REST API — authenticated, `context=edit`
— for the post's real status and permalink, then reconciles three columns:

| Column | To |
|---|---|
| `content_publications.status` | the real WP status, mapped: `publish→publish`, `draft→draft`, `pending→pending`, `private→private`, `future→scheduled`, `trash→trashed`. Post not found at all (even in trash) → `trashed`. |
| `content_publications.platform_url` | the real permalink (fixes the stale `?p=<id>` draft URL). |
| `pages.url` | the same permalink — the crawl `pages` row keyed by `(domain, old url)`. |

Only rows that actually differ are touched. It checks both post types: articles
(`/wp/v2/posts`) and the `cade_faq` CPT (`/wp/v2/faqs`); a missing FAQ route means
those FAQ posts are gone → `trashed`.

## How it reaches WordPress (matches CADE)

Uses `curl_cffi` with `impersonate="chrome"` and Basic app-password auth, and the
**direct-then-proxy fallback** CADE uses: a per-IP WAF `401`, a non-JSON `403`, a
captcha challenge, or an IP-block on the direct egress routes the request through
each `CRAWLER_PROXY_URLS` proxy (read from `.env`). This is why it can read sites
that block a plain request (e.g. iThemes Security REST restriction). If every
egress returns `401`, the app password is almost certainly wrong.

## Prerequisites

- Run under the **repo venv** — it needs `curl_cffi` and `cryptography`:
  `venv/bin/python ...`.
- DB credentials come from the `cade-db-queries` block of
  `.claude/skills.settings.{env}.json` (default env `prod`), same as the
  `cade-db-queries` skill.
- The Fernet key that decrypts stored WP credentials is read from the first
  block of that same file carrying `credential_encryption_key` — searched in
  order `cade-db-queries`, `cade-terms-merge`, then any block. Today it lives
  in `cade-terms-merge`. `CREDENTIAL_ENCRYPTION_KEY` in the environment
  overrides it.
- Proxy is read from `CRAWLER_PROXY_URLS` in the repo `.env` automatically
  (override with `--proxy-url`, disable with `--no-proxy`).
- A WordPress **application password** (not a login password) for a user with
  edit rights — only if you are overriding the stored one. Pass it via the
  `WP_APP_PASSWORD` env var to keep it out of shell history; `--app-password`
  also works.

## Credentials: stored by default, flags override

With only `--domain`, the script resolves all three WordPress inputs from that
domain's default `platform_connections` row (`platform_type = 'wordpress'`,
`is_default` first): `site_url` from the column, `username` /
`application_password` from the Fernet-decrypted `credentials_encrypted` blob.

Each field is filled independently, and anything you pass explicitly wins. The
run prints where each one came from, so you can see at a glance what it used:

```
[creds] site_url=db (https://example.com) user=db password=db
[creds] site_url=flag (https://dentist.example.ca) user=db password=flag/env
```

The resolved `site_url` is printed because a stored connection can legitimately
point at a **different host** than the domain name (shared or subdomain WP
installs) — check it before running `--apply`.

If the domain has no WordPress connection, or the row has no stored
credentials, the script names exactly which of `--site-url` / `--user` /
`--app-password` you still need to supply.

## Usage

```bash
# Dry-run (default): credentials from platform_connections, NO prod writes
venv/bin/python <skill-dir>/scripts/reconcile_wp.py \
  --domain theposbrokers.com

# Apply to prod (one transaction) — both flags required
venv/bin/python <skill-dir>/scripts/reconcile_wp.py \
  --domain theposbrokers.com --apply --confirm

# Override any subset; the rest still comes from the stored connection
WP_APP_PASSWORD='xxxx xxxx xxxx xxxx xxxx xxxx' \
venv/bin/python <skill-dir>/scripts/reconcile_wp.py \
  --domain theposbrokers.com --user admin

# Subdomain WordPress install where the stored site_url is wrong or absent
... --domain smilofamilydental.ca --site-url https://dentist.smilofamilydental.ca --user admin
```

### Flags

| Flag | Meaning |
|---|---|
| `--domain` | CADE domain (`domains.domain`). **The only required flag.** |
| `--site-url` | WordPress base URL (use the real WP host — may be a subdomain). Defaults to the stored connection's `site_url`. |
| `--user` | WordPress username for the app password. Defaults to the stored `username`. |
| `--app-password` / `WP_APP_PASSWORD` | The application password. Defaults to the stored `application_password`. Prefer the env var over the flag. |
| `--env` | DB creds env: `prod` (default) / `stg` / `local`. |
| `--post-types` | `content,faq` (default). |
| `--proxy-url` / `--no-proxy` | Override or disable the proxy fallback. |
| `--out` | Output dir (default `misc/mismatches`). |
| `--apply` `--confirm` | Execute the UPDATEs on prod. Both required; either alone is refused / dry-run. |
| `--selftest` | Offline logic check (no network/DB), then exit. |

## Output

- `misc/mismatches/<domain>.json` — per-row report: db vs WP status, old vs new URL, kind, chosen reconcile target, and which egress (direct/proxy) served the reads.
- `misc/mismatches/reconcile_<domain>.sql` — the exact `BEGIN; …UPDATE content_publications…; …UPDATE pages…; COMMIT;` it would run. Keyed by `content_publications.id` (PK — `platform_post_id` repeats across sites). You can run this yourself instead of `--apply`.

## Safety

- **Read-only by default.** No prod write happens without `--apply --confirm`.
- `--apply` runs both UPDATEs in a single transaction; any error rolls the whole thing back (`ON_ERROR_STOP`).
- `pages(domain_id, url)` has no unique constraint, so the URL rewrite cannot conflict.
- The DB write uses the configured `cade-db-queries` role; if that role is read-only, `--apply` fails cleanly (nothing changes).

## Verify a change

After `--selftest` and before trusting a new domain, sanity-check the plan: the
JSON report lists every mismatch with its `db_status`/`wp_status` and the exact
URLs, and the `.sql` file is human-readable. Spot-check a couple of post IDs by
hand against the site if in doubt.
