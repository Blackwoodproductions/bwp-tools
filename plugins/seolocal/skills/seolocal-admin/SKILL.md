---
name: seolocal-admin
description: Create an SEO Local domain on a package, add keywords to a domain, change a domain's package, or create/edit a package (service type) itself in prod bwp_seo, through the cade-mcp connector's seo_create_domain / seo_add_keywords / seo_change_package / seo_create_package / seo_update_package tools. Use for "set up a package for X", "add a domain", "onboard X on BRON 90", "add keywords to X", "add these keyword groups", "move X to SEOM 60", "upgrade/downgrade the package", "create a new package / service type", "make a BRON 120 like BRON 90", "change the price of package X". Only SEO Local staff admins (adminlevel 10) can write; everyone else is refused by seo-service. Never raw SQL INSERTs; never the ranking DB.
---

# seolocal-admin

**Prod writes to bwp_seo.** These are the legacy admin dashboard's "Add Domain", "Manage Keywords", "Change Service Package" and "Create/Edit Package" actions, now run through seo-service with the same rules. Look things up with `execute_sql_seo` (the read-only `seolocal-db-queries` skill). Write only with the five tools below. Never try an INSERT through DBHub, because the grant refuses it.

## Always
1. Resolve ids with read-only SQL first (recipes below) and show the user what you found.
2. Call the tool with `dry_run=true` (the default) and show the user the plan it returns: package, cap, the keywords it would insert, any trim; for packages, the full row (create) or the before/after diff and `domainsOnPackage` (update).
3. **Ask the user to confirm.** Then repeat the same call with `dry_run=false`. Set `confirm_domain` to the domain (`confirm_name` / `confirm_sku_id` for packages).
4. Relay the result. If the tool refuses with `seo-service 403`, the signed-in person is not an SEO Local staff admin; say so and stop. Relay `422` (package cap, Agentic) and `409` (duplicate) messages as they are.

## Tools
| Job | Tool | Notes |
|---|---|---|
| New domain on a package | `seo_create_domain(domain, confirm_domain, sku_id, affiliate_id, mode, …)` | `mode`: `own-account` (the reseller owns it), `existing-user` + `owner_id` (must be under that reseller), `new-user` + `new_user={firstName, lastName, email}` (sends an invite email). Keywords optional, same shapes as below. Status is set from price: free → 0, paid → 4 (New PO). |
| Add keywords | `seo_add_keywords(domain, confirm_domain, groups=… or keywords=…)` | Add-only. Refused past the package's keyword cap. |
| Change package | `seo_change_package(domain, confirm_domain, sku_id, trim_keywords=false)` | Re-syncs feature flags unless Package Override is on. A smaller cap is refused unless `trim_keywords=true`, which deactivates the newest keywords beyond the cap. Ask before trimming. |

| New package | `seo_create_package(name, confirm_name, from_sku_id=…, fields={…})` | `from_sku_id` copies every column of an existing package; `fields` override it. Without it, unset fields take the legacy form's defaults (features off, Business Collective on, active). 409 if an active package already has the name. |
| Edit package | `seo_update_package(sku_id, confirm_sku_id, fields={…})` | Writes only the fields that differ. Domains already on the package **keep their own feature flags** (they are copied on assignment); only the package row changes. A rename into another family is refused. |

**Package `fields`** (camelCase): `keywords` (cap, main rows), `price`, `ukprice`, `description`, `rankdays`, `sitemapdays`, `webringlinks`, `active`, the booleans `rankingreports`, `resources`, `webringFeed`, `businessCollective`, `spyderMap`, `relatedArticles`, `uptimeMonitor`, `skipFeedChecker`, `webtabs`, `webring`, `webworkscms`, `autogeosilos`, `deeplinking`, and the labels (≤30 chars) `resourcesLabel`, `webringFeedLabel`, `businessCollectiveLabel`, `spyderMapLabel`, `relatedArticlesLabel`. Prefer cloning the nearest existing package over spelling flags out.

**The package name decides its family**, and the family decides keyword shape and CADE content strategy: `SEOM ` and `BRON ` (with the space), `Agentic`, `Webworks Local` / `Webworks National`. Say which family the dry run reports before confirming. An active package also shows up in the signup and reseller catalogs; create with `active: false` if it isn't ready to sell.

**Keyword shape is decided by the domain's package:**
- `SEOM …` / `BRON …` packages: `groups=[{"main": "plumber austin", "supports": ["emergency plumber austin", "austin drain repair"]}]`. Each group needs exactly two supports.
- Every other package: `keywords=["plumber austin", "drain repair austin"]`.
- `Agentic …` packages are refused. Those are set up from the dashboard.
- Allowed characters are letters, digits, spaces and `- . ' ,`, up to 255 characters each. A keyword already on the domain (main or support, any case) is refused.

New keywords have no article yet, so CADE picks them up as content work on the domain's schedule.

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
```

## Not here
- Deleting or editing keywords, deleting packages, billing/orders, CADE level: use the dashboard. To retire a package, `seo_update_package(fields={"active": false})`.
- Ranking (`bwp_ranking_service`): never.
