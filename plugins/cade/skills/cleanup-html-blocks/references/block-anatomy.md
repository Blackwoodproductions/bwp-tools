# What the style block actually is

Read this before deciding between a strip and a `--html-block` replace, and before
hand-authoring a replacement block.

## The targeting rule, and why

CADE posts carry **1–8 `wp:html` blocks**. Only one of them holds `<style>`; the rest
are content. Verified on poweredwheelchairs.medequipped.com (48 posts, 2026-07-23): the
style block was the **last** `wp:html`, never reliably the first, and 37/48 posts had a
*table* as their first `wp:html`. A "replace the first `wp:html` block" script would
have destroyed 37 tables.

So: match `<!-- wp:html -->…<!-- /wp:html -->`, keep only the one whose body contains
`<style` (case-insensitive). Each post has at most one, which makes the match
unambiguous. Posts with none are normal and are counted as `no_block`.

The other `wp:html` blocks you will see, and must not touch:

| Block | Emitted by |
|---|---|
| `<div class="responsive-table-wrapper"><table>…` | `app/domain/content_formatting/renderers.py` (`platform_format == "gutenberg"`) |
| `<iframe …>` embeds | content pipeline |

## Articles (`post`)

The style block is appended last by the assembler
(`app/domain/content_formatting/assembler.py`, `_wrap_style_block`):

```python
if platform == ContentPlatform.WORDPRESS:
    return f"<!-- wp:html -->\n{style_block}\n<!-- /wp:html -->"
```

`style_block` is the domain's own CSS, already filtered by
`template_config.style_sections` for the `wordpress` platform
(`app/domain/content_formatting/style_filter.py`). Default sections are
`[toc, tables, quotes]`.

**Stripping an article's block removes domain CSS only.** The theme still styles the
post. This is the low-risk case.

## FAQs (`cade_faq`) — the block carries more than domain CSS

`app/domain/content_generation/faqs/service.py` (`_get_faq_wordpress_code`) builds
**one** trailing `wp:html` and packs up to three things into it:

```python
faq_css = (
    _FAQ_QUERY_STYLES
    if has_domain_css
    else _FAQ_ANSWER_STYLES + _FAQ_QUERY_STYLES
)
bottom_parts = [f"    <style>\n{faq_css}    </style>"]
if has_domain_css:
    bottom_parts.append(style_block)
```

where `has_domain_css = bool(css_prefix and style_block)`. So:

| Domain has `css_rules`? | That one block contains |
|---|---|
| Yes | `_FAQ_QUERY_STYLES` + the domain `style_block` |
| No | `_FAQ_ANSWER_STYLES` + `_FAQ_QUERY_STYLES` |

`_FAQ_QUERY_STYLES` styles the **dynamic Related-FAQs `core/query` loop** at the
bottom of every FAQ — post titles, excerpt colour, "Read More »", and the separator
opacity. It is deliberately independent of the domain CSS and is *always* kept by
CADE.

`_FAQ_ANSWER_STYLES` is the **answer-body fallback** (`.wp-block-post-content`
font-size/line-height/link/list rules), kept only when the domain has no CSS of its
own — otherwise the domain block styles the answer through the
`{prefix}-container` div that wraps the whole FAQ.

**Therefore a bare strip on a FAQ also removes the Related-FAQs styling, and on a
domain with no `css_rules` the answer body's font sizing too.** The script prints a
warning when `faq` is in scope with no `--html-block`.

### Keeping the FAQ base styles while dropping domain CSS

Pass a `--html-block` containing just `_FAQ_QUERY_STYLES`. Current value, verbatim
from `faqs/service.py` — re-check it against the source before use, it is not
generated:

```html
<!-- wp:html -->
    <style>
        .wp-block-query .wp-block-post-title { text-decoration: none; font-weight: 600; }
        .wp-block-query .wp-block-post-title:hover { text-decoration: underline; }
        .wp-block-post-excerpt__excerpt { color: var(--wp--preset--color--contrast); }
        .wp-block-read-more { font-weight: 500; text-decoration: none; }
        .wp-block-read-more:hover { text-decoration: underline; }
        .wp-block-separator { opacity: 0.2; }
    </style>
<!-- /wp:html -->
```

If the FAQ's domain has **no** `css_rules`, prepend `_FAQ_ANSWER_STYLES` too or the
answer body loses its sizing.

## Rest of the FAQ document (do not touch)

`_FAQ_GROUP_TEMPLATE` wraps everything in `wp:group` → constrained inner group →
native answer blocks → related-article `wp:list` → `wp:separator` → "Related FAQs"
`wp:heading` → a `wp:query` loop bound to `"postType":"cade_faq"`. All native
Gutenberg — the script never matches any of it, since none of it is `wp:html`.

Answers used to be emitted as an opaque `wp:html` `<div>`; they are now native
`wp:paragraph`/`wp:heading`/`wp:list` blocks so the block editor exposes them as
click-and-type text (`format_faq_answer_gutenberg`, `app/utils/formatting.py`).
**Legacy FAQs published before that change still have an answer-bearing `wp:html`
div** — it holds no `<style>`, so this script correctly ignores it. Converting those
is a separate job.

## `<p><style>` in the live post is not CADE

CADE emits `<!-- wp:html -->\n<style>…` with no `<p>`. A `<p><style>…</style></p>`
wrap seen on a live site is a **WordPress-side mutation** — the signature matches a
TinyMCE/Classic-Editor round-trip that swallowed the whole style element into one
paragraph while preserving the block comments. The regex still matches the enclosing
`wp:html` block, so the script handles it; just don't chase it as a CADE bug.

## The de-tagged block — CSS as visible page text

A worse form of the same round-trip, found on triumphroofs.com (2026-08-04, 48 of 171
posts): WordPress ate the `<style>` **element** and kept only its text, wrapped in a
`<p>`. The block comments survive:

```html
<!-- wp:html -->
<p>.wp-block-read-more { font-weight: 500; } .triumphroofs-cade-td { padding:0.75rem 1rem; } …</p>
<!-- /wp:html -->
```

With no `<style>` element the browser has nothing to apply, so **the CSS renders as a
wall of visible text at the bottom of the post**. That is the user-facing symptom;
`var(--wp--preset--color--contrast)` showing as `var(–wp–preset–color–contrast)` on
the rendered page is the same texturizer pass and confirms the diagnosis.

Targeting on `<style>` alone misses these — they count as `no_block`, which reads as
"already clean". So the script matches them **structurally** (`_is_bare_css`): a
`wp:html` block whose only tags are `p`/`br` and whose text holds 2+ `selector { prop:
value }` rules. The tag allowlist excludes table wrappers, iframes and `<script>`; the
2-rule floor excludes prose. Legacy answer-bearing `wp:html` divs are safe on both
counts.

Check for survivors after a sweep by grepping raw content for a rule fragment rather
than for `<style`:

```python
"<style" in raw.lower() or "wp-block-read-more {" in raw or "-cade-table {" in raw
```

## Undoing a strip properly

The backup JSON gives you `old_block` per post, but re-inserting it is a manual
splice. If what you actually want is *current* CADE output, a re-publish with
`force_refresh` replaces the whole `post_content` and is the cleaner path — this
script does surgical block edits, not regeneration.
