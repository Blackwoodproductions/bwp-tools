# Change a domain's package with a new keyword set

A customer on **SEOM 5** upgrades to **Agentic 20** and sends a CSV with their new keyword sets. This tutorial moves the package and applies the whole set in one transaction, then shows what happens on the customer's site and on partner sites afterwards, and how to check it.

The same flow covers every direction (BRON → SEOM, Agentic → BRON, Webworks → SEOM, …). Only the shape of the keyword lines changes.

## Why not the legacy admin page

The old way was the legacy admin page: switch the package, press **Delete All Keywords**, import the CSV. Don't. Two things go wrong:

1. **Deleted keywords come back.** Every legacy "edit domain" save re-creates a live `bwp_bubblefeed` row for each title still in the hidden `bwp_domains.keywords` column. On alarm21.com the eight old keywords returned the day after the change, and partner sites started linking to pages that did not exist.
2. **Nothing retires the old articles.** The removed keywords' articles and Resources posts stayed live on the customer's WordPress site.

The tool below empties that column, soft-deletes the rows the way CADE expects, and CADE's link sync retires the old posts within minutes.

## What you need

- The `seolocal` plugin and the `cade-mcp` connector, signed in (top-level README).
- Your account must be an SEO Local staff admin. Anyone else gets `seo-service 403: … is not an SEO Local staff admin`.
- The customer's CSV, or a list you can type.

## Step 1: look the domain up

Ask the assistant for the domain's package and keywords, or run the SQL yourself with `execute_sql_seo` (read-only):

```sql
-- the domain, its package and cap
SELECT d.id, d.servicetype, s.servicetype AS package, s.keywords AS cap, d.packageoverride
  FROM bwp_domains d LEFT JOIN bwp_services s ON s.id = d.servicetype
 WHERE d.domain_name = 'brightpath-plumbing.com' AND d.deleted = 0;
```

| id | servicetype | package | cap | packageoverride |
|---|---|---|---|---|
| 118204 | 385 | SEOM 5 | 5 | 0 |

```sql
-- its live keywords: mains (agenticid NULL) and Agentic members (agenticid = the main's id)
SELECT id, restitle, agenticid, status FROM bwp_bubblefeed WHERE domainid = 118204 AND deleted != 1 ORDER BY id;
-- SEOM/BRON supports, two per main
SELECT id, restitle, bubblefeedid FROM bwp_bubblefeedsupport WHERE domainid = 118204 AND deleted != 1 ORDER BY id;
```

Say the domain holds three SEOM sets:

| main (bwp_bubblefeed) | supports (bwp_bubblefeedsupport) |
|---|---|
| 601233 Plumber Austin | 701110 Emergency Plumber Austin · 701111 Austin Drain Repair |
| 601234 Water Heater Repair Austin | 701112 Tankless Water Heater Austin · 701113 Water Heater Installation |
| 601235 Drain &amp;amp; Sewer | 701114 Sewer Line Repair · 701115 Sewer Camera Inspection |

(`Drain &amp;amp; Sewer` is a real prod shape: titles are often entity-encoded, sometimes twice. You do not need to clean that up; the tool matches on the decoded title.)

Then the target package:

```sql
SELECT id, servicetype, keywords AS cap FROM bwp_services WHERE active = 1 AND servicetype LIKE 'Agentic%' ORDER BY id;
```

| id | servicetype | cap |
|---|---|---|
| 412 | Agentic 20 | 20 |

So `sku_id = 412`.

## Step 2: turn the CSV into sets

The CSV convention: **one set per line, the main first.**

| Target family | A line means | Tool argument |
|---|---|---|
| `Agentic …` | `main, member, member, …` (1 to 50 members) | `groups=[{"main": …, "supports": [members…]}]` |
| `SEOM …` / `BRON …` | `main, support, support` (exactly two) | `groups=[{"main": …, "supports": [a, b]}]` |
| anything else | every cell is a keyword | `keywords=[…]` |

The customer's CSV for Agentic 20:

```csv
Plumber Austin,Emergency Plumber Austin,Austin Drain Repair,Plumber Near Me Austin
Water Heater Repair Austin,Tankless Water Heater Austin
Drain & Sewer,Sewer Line Repair,Hydro Jetting Austin
Slab Leak Detection Austin,Slab Leak Repair
```

Paste it into the chat and say what you want:

> Move brightpath-plumbing.com to Agentic 20 (sku 412) with these keyword sets, one set per line, main first:
> ```
> Plumber Austin,Emergency Plumber Austin,Austin Drain Repair,Plumber Near Me Austin
> Water Heater Repair Austin,Tankless Water Heater Austin
> Drain & Sewer,Sewer Line Repair,Hydro Jetting Austin
> Slab Leak Detection Austin,Slab Leak Repair
> ```

The assistant builds the call. Whitespace and HTML entities are normalised server-side, so `Drain & Sewer` matches the stored `Drain &amp;amp; Sewer`.

## Step 3: the dry run

The assistant calls the tool with the default `dry_run=true`, which writes nothing:

```text
seo_change_package(
  domain="brightpath-plumbing.com",
  confirm_domain="brightpath-plumbing.com",
  sku_id=412,
  groups=[
    {"main": "Plumber Austin", "supports": ["Emergency Plumber Austin", "Austin Drain Repair", "Plumber Near Me Austin"]},
    {"main": "Water Heater Repair Austin", "supports": ["Tankless Water Heater Austin"]},
    {"main": "Drain & Sewer", "supports": ["Sewer Line Repair", "Hydro Jetting Austin"]},
    {"main": "Slab Leak Detection Austin", "supports": ["Slab Leak Repair"]}
  ]
)
```

and shows you the preview:

```json
{
  "dryRun": true,
  "domainId": 118204,
  "from": "SEOM 5",
  "to": "Agentic 20",
  "keywordCap": 20,
  "keep": [
    { "id": 601233, "title": "Plumber Austin", "role": "main" },
    { "id": 601234, "title": "Water Heater Repair Austin", "role": "main" },
    { "id": 601235, "title": "Drain &amp;amp; Sewer", "role": "main" }
  ],
  "remove": [
    { "id": 701110, "title": "Emergency Plumber Austin", "role": "support", "main": "Plumber Austin" },
    { "id": 701111, "title": "Austin Drain Repair", "role": "support", "main": "Plumber Austin" },
    { "id": 701112, "title": "Tankless Water Heater Austin", "role": "support", "main": "Water Heater Repair Austin" },
    { "id": 701113, "title": "Water Heater Installation", "role": "support", "main": "Water Heater Repair Austin" },
    { "id": 701114, "title": "Sewer Line Repair", "role": "support", "main": "Drain &amp;amp; Sewer" },
    { "id": 701115, "title": "Sewer Camera Inspection", "role": "support", "main": "Drain &amp;amp; Sewer" }
  ],
  "add": [
    { "title": "Emergency Plumber Austin", "role": "member", "main": "Plumber Austin" },
    { "title": "Austin Drain Repair", "role": "member", "main": "Plumber Austin" },
    { "title": "Plumber Near Me Austin", "role": "member", "main": "Plumber Austin" },
    { "title": "Tankless Water Heater Austin", "role": "member", "main": "Water Heater Repair Austin" },
    { "title": "Sewer Line Repair", "role": "member", "main": "Drain & Sewer" },
    { "title": "Hydro Jetting Austin", "role": "member", "main": "Drain & Sewer" },
    { "title": "Slab Leak Detection Austin", "role": "main" },
    { "title": "Slab Leak Repair", "role": "member", "main": "Slab Leak Detection Austin" }
  ],
  "moneyUrlCleared": [
    { "id": 601233, "title": "Plumber Austin", "url": "https://brightpath-plumbing.com/plumbing/" }
  ],
  "legacyColumnCleared": true,
  "featuresSynced": true
}
```

How to read it:

- **keep** — the same title in the same role. The row keeps its id, so its article, its Resources post and every partner link pointing at it stay exactly as they are. Here all three mains survive.
- **remove** — live rows the new set does not have *in that role*. `Emergency Plumber Austin` was a SEOM support and is now an Agentic member: that is a **remove plus an add**, because a support row and a member row live in different tables with different meanings. Its old support article will be retired.
- **add** — new rows. Members are inserted under their main's `agenticid`. `Slab Leak Detection Austin` is a brand-new main and gets its own page.
- **moneyUrlCleared** — SEOM/BRON mains carry the customer's money URL in a column that, on Agentic, means "legacy member, hide me". The tool empties it on the way in and tells you which rows and which URLs, so nothing silently disappears from the Agentic link context.
- **keywordCap** — counted against mains + members (supports never counted). Here 3 kept + 8 added = 11 of 20.
- **legacyColumnCleared / featuresSynced** — the legacy `bwp_domains.keywords` column is emptied, and the domain's feature flags are re-synced from the package (unless Package Override is on, which the preview would show as `false`).

If anything is off (a typo in the CSV shows up as a remove + add instead of a keep), fix the list and dry-run again. Nothing has been written.

## Step 4: confirm and apply

Tell the assistant to go ahead. It repeats the identical call with `dry_run=false`. The answer is the same shape with `"dryRun": false`.

Everything inside happened in one transaction: the package column, the soft-deletes, the inserts, the money-URL clear, the legacy column, `lastlinkcheck = NULL` (so services-app re-picks the domain's partner links on its next 5-minute batch) and an audit row. A failure anywhere rolls all of it back.

## Step 5: what happens next, and how to check it

### In bwp_seo, immediately

```sql
SELECT id, restitle, agenticid, active, deleted, status FROM bwp_bubblefeed WHERE domainid = 118204 ORDER BY id;
```

| id | restitle | agenticid | active | deleted | status |
|---|---|---|---|---|---|
| 601233 | Plumber Austin | NULL | 1 | 0 | publish |
| 601234 | Water Heater Repair Austin | NULL | 1 | 0 | publish |
| 601235 | Drain &amp;amp; Sewer | NULL | 1 | 0 | publish |
| 640001 | Emergency Plumber Austin | 601233 | 1 | 0 | publish |
| 640002 | Austin Drain Repair | 601233 | 1 | 0 | publish |
| … | … | … | | | |
| 640007 | Slab Leak Detection Austin | NULL | 1 | 0 | publish |
| 640008 | Slab Leak Repair | 640007 | 1 | 0 | publish |

```sql
SELECT id, restitle, active, deleted FROM bwp_bubblefeedsupport WHERE domainid = 118204 ORDER BY id;
-- every old support: active 0, deleted 1
SELECT servicetype, keywords, lastlinkcheck FROM bwp_domains WHERE id = 118204;
-- 412 | '' | NULL
SELECT `date`, userid, `change` FROM bwp_modification_log
 WHERE `table` = 'bwp_domains' AND recordid = 118204 ORDER BY id DESC LIMIT 1;
-- the audit row: via "internal/package", kept/removed ids, added titles, moneyUrlCleared ids
```

### On the customer's site, within about five minutes

If the site has been cut over to CADE (a BRON WordPress site running `cade-seo`), CADE's link sync runs every five minutes, 25 domains per tick. The package change changes the domain's link context, so its next turn is a full run, which:

- creates a Resources post for every new main (so a partner link to `/slab-leak-detection-austin-640007/` forwards somewhere real at once),
- trashes the Resources posts and the article posts of every removed keyword and support, and deletes CADE's records of them,
- rewrites every page's link tree.

To hurry it, ask the assistant to run `link_sync` on the domain (the `wp-migration` plugin's tool); it queues the sync now and reports `retired: 6` and the rest. Check on the CADE side with `execute_sql_cade`:

```sql
SELECT bwp_keyword_id, platform_post_id, platform_url FROM bron_resource_posts WHERE bwp_domain_id = 118204 ORDER BY bwp_keyword_id;
-- the three kept mains plus 640007; the removed supports never had one
SELECT bwp_keyword_id, bwp_kind, publication_type, status FROM content_publications
 WHERE bwp_domain_id = 118204 AND platform = 'wordpress' AND strategy = 'bron' ORDER BY bwp_keyword_id, bwp_kind;
-- no supportingKeyword rows left; the new rows appear as their articles get written
```

New keywords' articles are written on the domain's normal content schedule; nothing is queued out of turn. A kept main whose members changed keeps its article (a re-order is the way to refresh it).

A site that is **not** cut over (still on the PHP plugin or Premium SEO) renders its Resources page from bwp_seo on every request, so the removed keywords vanish from it immediately and nothing else is needed.

### On partner sites

Ask the assistant for `seo_link_context("brightpath-plumbing.com")`: the `pages` list is what every partner host renders. The removed supports are gone from the groups, `Slab Leak Detection Austin` is there with `url: https://brightpath-plumbing.com/slab-leak-detection-austin-640007/`.

- Partner hosts on the PHP plugin or Premium SEO render that list live: the next page view shows the new set.
- Partner hosts on cut-over WordPress sites rewrite on their own link sync; with 25 per tick, every host is updated within about 25 minutes.
- Placements on the removed keywords are cleaned up when services-app processes the domain again, which the `lastlinkcheck` reset puts at the front of its next batch instead of up to 12 hours away.
- Once CADE writes the new article's address back to bwp, WordPress hosts switch to the clean URL; `/slab-leak-detection-austin-640007/` keeps forwarding (301) for hosts that cached it.

### The legacy-page check

Open the domain on the legacy admin page and save it once without changing anything. Then:

```sql
SELECT COUNT(*) FROM bwp_bubblefeed WHERE domainid = 118204 AND deleted != 1;
```

The count is unchanged. Before this tool, that save brought the old titles back.

## Variations

- **Agentic → Agentic, members shuffled.** A member that moves from one main to another is a `keep`; its `agenticid` is re-pointed to the new main. Nothing is retired.
- **Agentic → SEOM/BRON.** Members cannot become supports by keeping their row (different table): they show as `remove` + `add`. Mains keep their rows and their articles.
- **Anything → Webworks or another flat package.** Send `keywords=[…]`; every title is a main; children of the old package are removed.
- **Same package, new set.** Pass the domain's current `sku_id`. See [Replace or remove keywords on the current package](replace-keywords-on-the-same-package.md).
- **No set at all.** `seo_change_package(domain, confirm_domain, sku_id)` moves the package only. A smaller cap is refused unless `trim_keywords=true`, which deactivates the newest keywords beyond it (legacy behaviour). The legacy column and `lastlinkcheck` are still cleared.
