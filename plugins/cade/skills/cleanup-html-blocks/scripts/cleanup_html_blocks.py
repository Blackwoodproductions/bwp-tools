"""Strip (or replace) the <style> <!-- wp:html -->...<!-- /wp:html --> block in WP posts.

Sweeps a WordPress site via the REST API (context=edit, so it operates on raw
Gutenberg markup) and cleans the first wp:html block CONTAINING A <style> TAG out
of every post. Targeting by <style> (not "first block") is required: CADE posts
carry other wp:html blocks (table wrappers, iframes) before the style block.
Posts without a style block are skipped and counted.

Default is STRIP (remove the block). Pass --html-block to replace it instead.
Sweeps both articles (/posts) and the cade_faq CPT (/faqs) by default.

Dry-run by default; pass --apply to write. Old blocks are saved to
misc/wp-scripts/backups/<host>-<post-type>-<timestamp>.json in both modes.

MUST run under the repo venv (imports WordpressApiClient → needs a valid .env).
--selftest is the exception: pure regex, no imports, no .env.

Usage:
    venv/bin/python <skill-dir>/scripts/cleanup_html_blocks.py \
        --site-url https://example.com --user admin  # app password via WP_APP_PASSWORD

    # replace instead of strip
    ... --html-block "$(cat block.txt)"
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

REPO_ROOT = next((p for p in [Path.cwd().resolve(), *Path.cwd().resolve().parents] if (p / ".claude").is_dir()), Path.cwd().resolve())  # ponytail: plugin copy — repo root = nearest ancestor of cwd with .claude/; run from the cade-service checkout
sys.path.insert(0, str(REPO_ROOT))

# Artifacts land in misc/ like every other skill; keeps new backups next to the
# 2026-07-23 ones this script's predecessor wrote.
DEFAULT_OUT = "misc/wp-scripts/backups"

# ponytail: regex on block comments, not a Gutenberg parser — wp:html blocks don't nest.
BLOCK_RE = re.compile(r"<!--\s*wp:html\s*-->.*?<!--\s*/wp:html\s*-->", re.DOTALL)

# Second mutation seen live (client-c.com, 2026-08-04): WP ate the <style>
# ELEMENT and left its CSS as body text in a <p>, keeping the block comments. The
# CSS then renders as visible page text, and there is no "<style" left to match on.
# Detected structurally: a block whose only tags are p/br and whose text is CSS rules.
_INNER_RE = re.compile(r"^<!--\s*wp:html\s*-->(.*?)<!--\s*/wp:html\s*-->$", re.DOTALL)
_TAG_RE = re.compile(r"</?([a-z][a-z0-9]*)", re.IGNORECASE)
_CSS_RULE_RE = re.compile(r"\{[^{}]*[a-z-]+\s*:[^{}]+\}")
_CSS_SAFE_TAGS = {"p", "br"}


def _is_bare_css(block: str) -> bool:
    """True when the block is a de-tagged style block: no markup but p/br, 2+ CSS rules.

    Requiring 2+ rules and an empty tag allowlist keeps prose and content blocks out:
    a table/iframe/script block fails on its tags, a paragraph fails on the rule count.
    """
    match = _INNER_RE.match(block)
    inner = match.group(1) if match else block
    if any(tag.lower() not in _CSS_SAFE_TAGS for tag in _TAG_RE.findall(inner)):
        return False
    return len(_CSS_RULE_RE.findall(inner)) >= 2

WP_STATUSES = "publish,future,draft,pending,private"

# Mirrors WordPressEndpoints in app/infrastructure/adapters/wordpress/constants.py
# (BLOG.articles / FAQ.articles). Duplicated on purpose: importing that module pulls
# app.infrastructure.adapters.__init__ → the AI adapters → settings, which would make
# --selftest need a valid .env. Same call reconcile_wp.py made with its ROUTE map.
POST_TYPE_ROUTES = {"post": "/posts", "faq": "/faqs"}


def _strip_span(raw: str, start: int, end: int) -> str:
    """Remove raw[start:end] plus the whitespace hugging it, leaving one clean separator."""
    while start > 0 and raw[start - 1] in " \t\r\n":
        start -= 1
    while end < len(raw) and raw[end] in " \t\r\n":
        end += 1
    before, after = raw[:start], raw[end:]
    return f"{before}\n\n{after}" if before and after else before + after


def clean_style_block(raw: str, html_block: str | None = None) -> tuple[str, str | None]:
    """Strip (html_block=None) or replace the wp:html block carrying the CSS.

    Matches both shapes: an intact <style> block, and one WP de-tagged into bare CSS
    text (see _is_bare_css). Returns (new_raw, old_block); old_block is None when no
    wp:html block carries CSS.
    """
    for match in BLOCK_RE.finditer(raw):
        block = match.group(0)
        if "<style" not in block.lower() and not _is_bare_css(block):
            continue
        if html_block is None:
            return _strip_span(raw, match.start(), match.end()), match.group(0)
        # String splice, not re.sub: re.sub would read "\2014" as a group reference.
        return raw[: match.start()] + html_block + raw[match.end() :], match.group(0)
    return raw, None


def selftest() -> None:
    block = '<!-- wp:html -->\n<style>.new { content: "\\2014"; }</style>\n<!-- /wp:html -->'
    # style block LAST, table wrapper first — the real CADE layout
    three_blocks = (
        "<!-- wp:html --><div class='responsive-table-wrapper'><table>t</table></div><!-- /wp:html -->\n"
        "<!-- wp:html --><iframe src='x'></iframe><!-- /wp:html -->\n"
        "<!-- wp:html --><style>.old {}</style><!-- /wp:html -->"
    )
    new_raw, old = clean_style_block(three_blocks, block)
    assert old == "<!-- wp:html --><style>.old {}</style><!-- /wp:html -->"
    assert block in new_raw, "backslashes in replacement must survive"
    assert "responsive-table-wrapper" in new_raw and "<iframe src='x'>" in new_raw, "content blocks must be untouched"

    untouched, old = clean_style_block("<!-- wp:html --><table>no style here</table><!-- /wp:html -->", block)
    assert old is None and "<table>no style here</table>" in untouched

    spaced = "<!--wp:html--><style>old</style><!--/wp:html-->"
    _, old = clean_style_block(spaced, block)
    assert old == spaced, "whitespace-less block comments must match"

    # --- strip mode ---
    stripped, old = clean_style_block(three_blocks)
    assert old == "<!-- wp:html --><style>.old {}</style><!-- /wp:html -->"
    assert "<style" not in stripped, "style block must be gone"
    assert "responsive-table-wrapper" in stripped and "<iframe src='x'>" in stripped
    assert not stripped.endswith("\n"), f"no trailing scar when the block was last: {stripped[-20:]!r}"

    # style block in the MIDDLE — separator collapses to exactly one blank line
    middle = (
        "<!-- wp:paragraph --><p>a</p><!-- /wp:paragraph -->\n\n"
        "    <!-- wp:html -->\n    <style>.x{}</style>\n    <!-- /wp:html -->\n\n"
        "<!-- wp:paragraph --><p>b</p><!-- /wp:paragraph -->"
    )
    stripped, old = clean_style_block(middle)
    assert stripped == (
        "<!-- wp:paragraph --><p>a</p><!-- /wp:paragraph -->\n\n"
        "<!-- wp:paragraph --><p>b</p><!-- /wp:paragraph -->"
    ), repr(stripped)

    # strip is idempotent: a second pass finds nothing
    again, old = clean_style_block(stripped)
    assert old is None and again == stripped

    # strip on a post with no style block is a no-op
    no_style = "<!-- wp:html --><table>t</table><!-- /wp:html -->"
    same, old = clean_style_block(no_style)
    assert old is None and same == no_style

    # --- de-tagged block: WP ate <style>, left the CSS as <p> text (client-c post 1410) ---
    detagged = (
        "<!-- wp:html -->\n<p>.wp-block-read-more { font-weight: 500; text-decoration: none; } "
        ".client-c-cade-td { padding:0.75rem 1rem; color:#fbd022; } "
        "@media (max-width: 768px) { .x { display: block; } .toc &gt; div { margin-bottom: 0.25rem !important; } }</p>\n"
        "<!-- /wp:html -->"
    )
    assert _is_bare_css(detagged)
    doc = f"<!-- wp:paragraph --><p>keep me</p><!-- /wp:paragraph -->\n\n{detagged}"
    stripped, old = clean_style_block(doc)
    assert old == detagged and stripped == "<!-- wp:paragraph --><p>keep me</p><!-- /wp:paragraph -->", repr(stripped)

    # ...and must NOT swallow real content blocks
    for content in (
        "<!-- wp:html --><p>Prose about pricing: it costs money.</p><!-- /wp:html -->",
        "<!-- wp:html --><div class='responsive-table-wrapper'><table>t</table></div><!-- /wp:html -->",
        "<!-- wp:html --><iframe src='x'></iframe><!-- /wp:html -->",
        "<!-- wp:html --><p>A legacy FAQ answer.</p><p>Second para, no CSS.</p><!-- /wp:html -->",
        "<!-- wp:html --><script>var a = { b: 1, c: 2 };</script><!-- /wp:html -->",
    ):
        assert not _is_bare_css(content), content
        same, old = clean_style_block(content)
        assert old is None and same == content, content

    assert POST_TYPE_ROUTES["post"] == "/posts" and POST_TYPE_ROUTES["faq"] == "/faqs"
    print("selftest OK")


def sweep(client, session, site_url, post_type, args, counts, backups) -> bool:
    """Sweep one post type. Returns False on a hard listing failure (not a missing route)."""
    route = POST_TYPE_ROUTES[post_type]
    page = 1
    while True:
        resp = client.get(
            site_url,
            session,
            path=route,
            data={"context": "edit", "status": args.statuses, "per_page": 100, "page": page},
            raise_for_status=False,
            cleanup=False,
        )
        if resp.status_code == 404:
            print(f"[{post_type}] route {route} not registered on this site — skipped")
            return True
        if resp.status_code != 200:
            print(f"[{post_type}] list page={page} failed: {resp.status_code} {resp.text[:200]}")
            return False
        posts = resp.json()
        total_pages = int(resp.headers.get("X-WP-TotalPages", "1") or "1")

        for post in posts:
            if args.limit and counts["total"] >= args.limit:
                print(f"[{post_type}] --limit {args.limit} reached")
                return True
            counts["total"] += 1
            post_id = post["id"]
            content = post.get("content", {})
            if "raw" not in content:
                # No content.raw means context=edit was denied — a permissions problem,
                # NOT "nothing to clean". Counted separately so it can't read as a no-op.
                counts["raw_missing"] += 1
                continue
            raw = content.get("raw") or ""
            new_raw, old_block = clean_style_block(raw, args.html_block)
            if old_block is None:
                counts["no_block"] += 1
                continue
            if old_block == args.html_block:
                counts["already_current"] += 1
                continue
            backups.append(
                {"post_type": post_type, "post_id": post_id, "link": post.get("link"), "old_block": old_block}
            )
            if not args.apply:
                counts["updated"] += 1
                continue
            update = client.post(
                site_url, session, path=f"{route}/{post_id}", data={"content": new_raw},
                raise_for_status=False, cleanup=False,
            )
            if update.status_code < 300:
                counts["updated"] += 1
                print(f"[{post_type}] updated {post_id} ({post.get('link')})")
            else:
                counts["failed"] += 1
                print(f"[{post_type}] FAILED {post_id}: {update.status_code} {update.text[:200]}")

        if page >= total_pages or not posts:
            return True
        page += 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-url", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--app-pass", help="WP application password (or set WP_APP_PASSWORD)")
    parser.add_argument("--html-block", help="Replacement <!-- wp:html -->...<!-- /wp:html --> block (default: strip)")
    parser.add_argument("--post-type", default="post,faq", help=f"Comma list of {sorted(POST_TYPE_ROUTES)} (default: post,faq)")
    parser.add_argument("--statuses", default=WP_STATUSES)
    parser.add_argument("--limit", type=int, default=0, help="Stop after N posts across all types (0 = all)")
    parser.add_argument("--out", default=DEFAULT_OUT, help=f"Backup dir, relative to repo root (default: {DEFAULT_OUT})")
    parser.add_argument("--apply", action="store_true", help="Write changes (default: dry-run)")
    args = parser.parse_args()

    app_pass = args.app_pass or os.environ.get("WP_APP_PASSWORD")
    if not app_pass:
        print("error: no application password. Pass --app-pass or set WP_APP_PASSWORD.")
        return 1

    post_types = [t.strip() for t in args.post_type.split(",") if t.strip()]
    unknown = [t for t in post_types if t not in POST_TYPE_ROUTES]
    if unknown or not post_types:
        print(f"error: --post-type must be a comma list of {sorted(POST_TYPE_ROUTES)}, got {args.post_type!r}")
        return 1

    if args.html_block is None:
        print("MODE: STRIP — the <style> wp:html block is removed, not replaced.")
        if "faq" in post_types:
            # faqs/service.py packs the domain style_block AND CADE's own FAQ query/
            # answer styles into that single trailing block. Stripping takes both.
            print("WARNING: on FAQs this also removes CADE's built-in Related-FAQs and")
            print("         answer fallback styles. Pass --html-block to keep them.")
    else:
        print("MODE: REPLACE — the <style> wp:html block is swapped for --html-block.")

    from app.infrastructure.adapters.wordpress.worpress_api_client import WordpressApiClient

    site_url = args.site_url.rstrip("/")
    client = WordpressApiClient()
    session = client._create_client(args.user, app_pass)

    try:
        me = client.get(site_url, session, path="/users/me", data={"context": "edit"}, raise_for_status=False, cleanup=False)
    except Exception as exc:  # unreachable host / every egress blocked — a typo'd --site-url lands here
        print(f"cannot reach {site_url}: {type(exc).__name__}: {exc}")
        return 1
    if me.status_code != 200:
        print(f"auth check failed: GET /users/me -> {me.status_code} {me.text[:200]}")
        return 1
    print(f"authenticated as {me.json().get('name')} on {site_url}")

    counts = {"updated": 0, "already_current": 0, "no_block": 0, "raw_missing": 0, "failed": 0, "total": 0}
    backups: list[dict] = []
    listing_ok = True
    try:
        for post_type in post_types:
            if not sweep(client, session, site_url, post_type, args, counts, backups):
                listing_ok = False
                break
    finally:
        # finally, not after: a mid-sweep exception must not lose the rollback record
        # for posts already written.
        if backups:
            host = urlsplit(site_url).netloc or site_url
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            out_dir = Path(args.out) if Path(args.out).is_absolute() else REPO_ROOT / args.out
            backup_path = out_dir / f"{host}-{'+'.join(post_types)}-{stamp}.json"
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            backup_path.write_text(json.dumps(backups, indent=2))
            print(f"old blocks backed up to {backup_path}")

    mode = "APPLY" if args.apply else "DRY-RUN (would_update)"
    print(
        f"[{mode}] updated={counts['updated']} already_current={counts['already_current']} "
        f"no_block={counts['no_block']} raw_missing={counts['raw_missing']} "
        f"failed={counts['failed']} total={counts['total']}"
    )
    if counts["raw_missing"]:
        print("NOTE: raw_missing>0 — content.raw was withheld. The user likely lacks edit rights on that type.")
    if not args.apply and counts["updated"]:
        print("DRY-RUN: nothing was written. Re-run with --apply to execute.")
    return 1 if (counts["failed"] or not listing_ok) else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
        sys.exit(0)
    sys.exit(main())
