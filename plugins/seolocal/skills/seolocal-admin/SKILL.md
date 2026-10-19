---
name: seolocal-admin
description: Create an SEO Local domain on a package, add keywords to a domain, change a domain's package (with or without a new keyword set from the operator's CSV), or create/edit a package (service type) itself in prod bwp_seo, through the cade-mcp connector's seo_create_domain / seo_add_keywords / seo_change_package / seo_create_package / seo_update_package tools. Use for "set up a package for X", "add a domain", "onboard X on BRON 90", "add keywords to X", "add these keyword groups", "move X to SEOM 60", "upgrade/downgrade the package", "move X to Agentic with these keywords", "replace X's keywords with this CSV", "create a new package / service type", "make a BRON 120 like BRON 90", "change the price of package X". Only SEO Local staff admins (adminlevel 10) can write; everyone else is refused by seo-service. Never raw SQL INSERTs; never the ranking DB; never the legacy admin page's Delete All Keywords button.
---

# seolocal-admin

**Prod writes to bwp_seo.** These are the legacy admin dashboard's "Add Domain", "Manage Keywords", "Change Service Package" and "Create/Edit Package" actions, now run through seo-service with the same rules. Look things up with `execute_sql_seo` (the read-only `seolocal-db-queries` skill). Write only with the five tools below. Never try an INSERT through DBHub, because the grant refuses it.

## Always
1. Resolve ids with read-only SQL first (recipes below) and show the user what you found.
2. Call the tool with `dry_run=true` (the default) and show the user the plan it returns: package, cap, the keywords it would insert, any trim; for a package change with a keyword set, the `keep` / `remove` / `add` lists; for packages, the full row (create) or the before/after diff and `domainsOnPackage` (update).
3. **Ask the user to confirm.** Then repeat the same call with `dry_run=false`. Set `confirm_domain` to the domain (`confirm_name` / `confirm_sku_id` for packages).
4. Relay the result. If the tool refuses with `seo-service 403`, the signed-in person is not an SEO Local staff admin; say so and stop. Relay `422` (package cap, unconverted Agentic members) and `409` (duplicate) messages as they are.

## Tools
| Job | Tool | Notes |
|---|---|---|
| New domain on a package | `seo_create_domain(domain, confirm_domain, sku_id, affiliate_id, mode, …)` | `mode`: `own-account` (the reseller owns it), `existing-user` + `owner_id` (must be under that reseller), `new-user` + `new_user={firstName, lastName, email}` (sends an invite email). Keywords optional, same shapes as below. Status is set from price: free → 0, paid → 4 (New PO). |
| Add keywords | `seo_add_keywords(domain, confirm_domain, groups=… or keywords=…)` | Add-only. Refused past the package's keyword cap. |
| Change package | `seo_change_package(domain, confirm_domain, sku_id, trim_keywords=false)` | Re-syncs feature flags unless Package Override is on. A smaller cap is refused unless `trim_keywords=true`, which deactivates the newest keywords beyond the cap. Ask before trimming. |
| Change package with a new keyword set | `seo_change_package(domain, confirm_domain, sku_id, groups=… or keywords=…)` | The whole new set, applied with the package change in one transaction. See the section below. Works with the domain's current `sku_id` too, to replace or remove keywords without changing the package. |
| New package | `seo_create_package(name, confirm_name, from_sku_id=…, fields={…})` | `from_sku_id` copies every column of an existing package; `fields` override it. Without it, unset fields take the legacy form's defaults (features off, Business Collective on, active). 409 if an active package already has the name. |
| Edit package | `seo_update_package(sku_id, confirm_sku_id, fields={…})` | Writes only the fields that differ. Domains already on the package **keep their own feature flags** (they are copied on assignment); only the package row changes. A rename into another family is refused. |

**Package `fields`** (camelCase): `keywords` (cap, main rows), `price`, `ukprice`, `description`, `rankdays`, `sitemapdays`, `webringlinks`, `active`, the booleans `rankingreports`, `resources`, `webringFeed`, `businessCollective`, `spyderMap`, `relatedArticles`, `uptimeMonitor`, `skipFeedChecker`, `webtabs`, `webring`, `webworkscms`, `autogeosilos`, `deeplinking`, and the labels (≤30 chars) `resourcesLabel`, `webringFeedLabel`, `businessCollectiveLabel`, `spyderMapLabel`, `relatedArticlesLabel`. Prefer cloning the nearest existing package over spelling flags out.

**The package name decides its family**, and the family decides keyword shape and CADE content strategy: `SEOM ` and `BRON ` (with the space), `Agentic`, `Webworks Local` / `Webworks National`. Say which family the dry run reports before confirming. An active package also shows up in the signup and reseller catalogs; create with `active: false` if it isn't ready to sell.

**Keyword shape is decided by the package** (the domain's current one for `seo_add_keywords`, the target one for `seo_change_package`):
- `SEOM …` / `BRON …` packages: `groups=[{"main": "plumber austin", "supports": ["emergency plumber austin", "austin drain repair"]}]`. Each group needs exactly two supports.
- `Agentic …` packages: `groups=[{"main": "roof repair", "supports": ["roof leak repair", "gutter repair"]}]`. The `supports` are the set's members (one or more, up to 50); they become `bwp_bubblefeed` rows under the main's `agenticid` and link to the main's page, never to a page of their own. Members count against the cap; supports on SEOM/BRON do not.
- Every other package: `keywords=["plumber austin", "drain repair austin"]`.
- Allowed characters for a new keyword are letters, digits, spaces and `- . ' ,`, up to 255 characters each. A keyword already on the domain (main, member or support, any case) is refused by `seo_add_keywords`.

New keywords have no article yet, so CADE picks them up as content work on the domain's schedule.

## Change package with a keyword set
The customer's full new set usually arrives as a CSV. Parse it yourself and pass lists; the tool takes no file.

**CSV convention:** one set per line, main first. Agentic: `main, member, member, …` (one or more members). SEOM/BRON: `main, support, support` (exactly two). Every other package: one keyword per cell, every cell a main. Decode HTML entities and trim before comparing; stored titles may be entity-encoded.

1. Look up the domain, its current package and its live keywords (recipes below) and show them.
2. Build `groups` (Agentic, SEOM, BRON) or `keywords` (other packages) for the **target** package and call `seo_change_package(domain, confirm_domain, sku_id, groups=… / keywords=…)` as a dry run.
3. Show the preview: `keep` (same title in the same role: keeps its row id, article, Resources post and partner links), `remove` (soft-deleted; a main that becomes a member, or a support that becomes a member, is a remove plus an add) and `add` (new rows), plus `keywordCap`. Over the cap or an unconverted Agentic domain (members still linking out; `scripts/agentic-linkout-to-agenticid.ts` fixes that) is a 422: relay it and stop.
4. **Ask the user to confirm**, then repeat with `dry_run=false`.
5. Say what happens next:
   - seo-service wrote the package, the keyword rows and the audit row in one transaction, emptied the legacy `bwp_domains.keywords` column (so a legacy admin save cannot re-create removed keywords) and reset `lastlinkcheck` so services-app re-picks the domain's partner links on its next 5-minute batch.
   - On a cut-over WordPress site, CADE's next link sync (within ~5 minutes) trashes the removed keywords' Resources posts and articles. To hurry it, run the cade-mcp `link_sync(domain, confirm_domain)` tool right after the change. New keywords' articles come on the domain's schedule; until then their partner links forward to the new Resources post.
   - Partner links move by themselves: feed-app hosts render the new set on the next request; WordPress hosts rewrite on their own link sync (all of them within ~25 minutes).

Never do a package change on the legacy admin page with "Delete All Keywords" plus a CSV import: the next legacy save re-inserts every deleted title from `bwp_domains.keywords`, and nothing retires the old articles.

## Lookups (`execute_sql_seo`)
```sql
-- one package's full row (the template to clone, or before an edit)
SELECT * FROM bwp_services WHERE id = <sku_id>;
-- packages (sku_id = id; keywords = cap)
SELECT id, servicetype, keywords, price FROM bwp_services WHERE active = 1 AND servicetype LIKE 'BRON%' ORDER BY id;
-- a paid, active reseller (affiliate_id)
SELECT r.id, r.email, r.firstname, r.lastname FROM bwp_register r JOIN bwp_resellers s ON s.userid = r.id
 WHERE r.isaffiliate = 1 AND r.deleted = 0 AND s.paid = 1 AND s.deleted = 0 AND r.email LIKE '%name%';
-- an existing owner under that reseller (owner_id)
SELECT id, email FROM bwp_register WHERE underaffiliate = <affiliate_id> AND deleted = 0 AND email LIKE '%name%';
-- a domain's package and active keyword count
SELECT d.id, d.servicetype, s.servicetype AS package, s.keywords AS cap, d.packageoverride,
       (SELECT COUNT(*) FROM bwp_bubblefeed b WHERE b.domainid = d.id AND b.active = 1 AND b.deleted != 1) AS active
  FROM bwp_domains d LEFT JOIN bwp_services s ON s.id = d.servicetype WHERE d.domain_name = 'example.com' AND d.deleted = 0;
-- a domain's live keywords, as the package change diffs them (agenticid = member of that main)
SELECT id, restitle, agenticid, status FROM bwp_bubblefeed WHERE domainid = <domain_id> AND deleted != 1 ORDER BY id;
SELECT id, restitle, bubblefeedid FROM bwp_bubblefeedsupport WHERE domainid = <domain_id> AND deleted != 1 ORDER BY id;
```

## Not here
- Editing a keyword's title, deleting packages, billing/orders, CADE level: use the dashboard. To retire a package, `seo_update_package(fields={"active": false})`. To remove or replace keywords, send the full new set through `seo_change_package` with the domain's current `sku_id`.
- Ranking (`bwp_ranking_service`): never.
