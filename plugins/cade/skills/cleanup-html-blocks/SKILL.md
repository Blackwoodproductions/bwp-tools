---
name: cleanup-html-blocks
description: >-
  Clean the injected CSS `<!-- wp:html --><style>…</style><!-- /wp:html -->` block
  out of published WordPress content — both articles (`/wp/v2/posts`) and the
  `cade_faq` CPT (`/wp/v2/faqs`) — using an authenticated WordPress application
  password so it edits the raw Gutenberg markup. Strips the block by default;
  replaces it when given `--html-block`, which is how a new CADE style block is
  rolled out across already-published posts. Use this skill whenever the user
  wants to remove, clean, strip, or swap the style/CSS block from WP posts or
  FAQs, says "cleanup the html blocks", "strip the style block from the posts",
  "the old CSS is still on the published articles", "roll out the new style
  block", "remove the wp:html style block from the faqs", or invokes
  "cleanup-html-blocks" directly. Takes site-url + user + application password.
  Reaches WordPress the way CADE does (curl_cffi impersonation +
  CRAWLER_PROXY_URLS proxy fallback). Dry-run by default and backs up every block
  it touches; only `--apply` writes to the live site.
---

# cleanup-html-blocks

> **Paths:** `<skill-dir>` is this skill's directory — Claude Code prints it as *Base directory for this skill* when the skill loads (under `~/.claude/plugins/cache/bwp-tools/cade/<version>/skills/<name>`). Run commands from the **cade-service repo root** so `venv/bin/python`, `.claude/skills.settings.{env}.json` and `misc/` resolve.

Point it at one WordPress site with an application password; it removes the injected
CSS `wp:html` block from every article and FAQ, leaving all other blocks alone.

## What it does

For each requested post type it pages the authenticated REST collection with
`context=edit` (the only way `content.raw` comes back), finds the **first `wp:html`
block whose body contains `<style`**, and either removes it or swaps it for
`--html-block`. Only `{"content": …}` is sent, so status, slug, author, meta and
taxonomies survive untouched.

| `--post-type` | CPT slug | REST route |
|---|---|---|
| `post` | `post` (core) | `/wp-json/wp/v2/posts` |
| `faq` | `cade_faq` | `/wp-json/wp/v2/faqs` |

Both are swept by default. A `404` on a collection means the CPT isn't registered on
that site — skipped, not fatal.

**Targeting is by CSS content, never "the first block".** CADE posts carry 1–8 `wp:html`
blocks — table wrappers and iframes are `wp:html` too, and the style block is usually
the *last* one. First-block targeting would have corrupted 37/48 posts on the first
site this ran against.

Two shapes match: a block containing `<style`, and one WordPress has **de-tagged** —
the `<style>` element eaten, its CSS left as `<p>` body text, so it renders as visible
text on the page (48/171 posts on triumphroofs.com, 2026-08-04). The second is matched
structurally: only `p`/`br` tags, 2+ CSS rules. A sweep that predates this reports
those as `no_block`, which reads as clean — re-run to catch them.

## How it reaches WordPress (matches CADE)

Uses `curl_cffi` with `impersonate="chrome"` and Basic app-password auth via
`WordpressApiClient`, including the **direct-then-proxy fallback** CADE uses: a per-IP
WAF `401`, a captcha challenge, or an IP-block on the direct egress routes the request
through each `CRAWLER_PROXY_URLS` proxy (read from `.env`). If every egress returns
`401`, the app password is almost certainly wrong.

## Prerequisites

- Run under the **repo venv** — it needs `curl_cffi` and imports
  `WordpressApiClient`, so a valid `.env` is required: `venv/bin/python ...`.
  (`--selftest` is the exception: pure regex, no imports, no `.env`.)
- A WordPress **application password** (not a login password) for a user with edit
  rights on both `post` and `cade_faq`. Pass it via `WP_APP_PASSWORD` to keep it out
  of shell history; `--app-pass` also works.

## Usage

```bash
# Dry-run (default): reports what it would strip, writes a backup, NO site writes
WP_APP_PASSWORD='xxxx xxxx xxxx xxxx' \
venv/bin/python <skill-dir>/scripts/cleanup_html_blocks.py \
  --site-url https://example.com \
  --user admin

# Prove it on one post first
WP_APP_PASSWORD='...' venv/bin/python <skill-dir>/scripts/cleanup_html_blocks.py \
  --site-url https://example.com --user admin --post-type faq --limit 1 --apply

# Full sweep
WP_APP_PASSWORD='...' venv/bin/python <skill-dir>/scripts/cleanup_html_blocks.py \
  --site-url https://example.com --user admin --apply

# Replace instead of strip (rolling out a new CADE style block)
... --html-block "$(cat /tmp/block.txt)" --apply

# Offline logic check
venv/bin/python <skill-dir>/scripts/cleanup_html_blocks.py --selftest
```

### Flags

| Flag | Meaning |
|---|---|
| `--site-url` | WordPress base URL (use the real WP host — may be a subdomain). **Required.** |
| `--user` | WordPress username for the app password. **Required.** |
| `--app-pass` / `WP_APP_PASSWORD` | The application password. Prefer the env var. |
| `--html-block` | Replacement block. **Omit to strip** — that's the default op. |
| `--post-type` | `post,faq` (default). |
| `--statuses` | `publish,future,draft,pending,private` (default; trash excluded). |
| `--limit` | Stop after N posts across all types (0 = all). |
| `--out` | Backup dir, relative to repo root (default `misc/wp-scripts/backups`). |
| `--apply` | Execute the writes. Without it: dry-run. |
| `--selftest` | Offline logic check (no network, no `.env`), then exit. |

## References

Load these when the summary line isn't self-explanatory or you need to author a
`--html-block`:

- **`references/block-anatomy.md`** — what the style block contains for articles vs
  FAQs, which CADE code emits it, exactly what a strip removes, and a ready-to-use
  replacement block that preserves the FAQ base styles. **Read this before choosing
  strip vs replace on FAQs.**
- **`references/wp-rest.md`** — routes and why `cade_faq` answers on `/faqs`,
  `context=edit`/`content.raw`, the write side effects (revisions + the cade-service
  callback fan-out), and a symptom→cause table for every counter and error.

## Output

- `misc/wp-scripts/backups/<host>-<post-types>-<UTC stamp>.json` — every block it
  touched, as `[{post_type, post_id, link, old_block}]`. Written in **both** dry-run
  and apply mode. This is the rollback record: splice `old_block` back into the post's
  raw content and POST it.
- A summary line: `updated / already_current / no_block / raw_missing / failed / total`.
  `raw_missing > 0` means `content.raw` was withheld — a **permissions problem**, not
  "nothing to clean". Exit code is non-zero if any post failed.

## Safety

- **Dry-run by default.** No site write happens without `--apply`.
- **Only `content` is sent** — never `meta`. cade-seo registers meta with
  `additionalProperties: false`, so one unregistered key would 400 the whole request.
- **Idempotent.** A cleaned post has no style block, so a re-run counts it under
  `no_block` and writes nothing.
- **The backup is written after the sweep**, so a crash mid-apply loses the record for
  posts already written. Use `--limit 1 --apply` as the first real write.

## Know before you apply

- **Stripping a FAQ removes more than the domain CSS.** CADE packs the domain
  `style_block` *and* its own Related-FAQs query/separator styles — plus the answer
  fallback styles when the domain has no `css_rules` — into that single trailing
  `wp:html`. A bare strip takes all of it; the script warns when `faq` is in scope with
  no `--html-block`. `references/block-anatomy.md` has the replacement block that keeps
  them.
- **Every write fans out a callback.** `cade-seo`'s `PublicationSyncListener` hooks
  `post_updated` for exactly `['post','cade_faq']` and schedules a WP-Cron POST back
  into cade-service ~10s after each content change. A 200-FAQ sweep queues 200
  callbacks.
- A page cache or CDN may serve the old CSS until it expires.

## Verify a change

Run `--selftest`, then a dry-run, and read the backup JSON before applying: each
`old_block` must be the style-bearing block, not a table wrapper. After
`--limit 1 --apply`, re-fetch that post's `content.raw` via `?context=edit` — the style
block gone, every other `wp:html` intact, `status` unchanged — then re-run the dry-run
and confirm it now falls under `no_block`.

## What this skill is NOT

- It does **not** touch the CADE database. For `content_publications` status/URL drift
  against the live site, use `reconcile-wp-content`.
- It does **not** re-publish or regenerate content. A CADE re-publish with
  `force_refresh` replaces the whole `post_content` — use that when you want the
  *current* CADE output, not a surgical block edit.
- It does **not** cover `kb-articles`. That CPT carries the same injected CSS but is
  out of scope; add `cade_kb` → `/kb-articles` to `POST_TYPE_ROUTES` if it's ever
  needed.

Full doc (committed, survives a fresh clone): `docs/claude/cleanup-html-blocks.md`.
