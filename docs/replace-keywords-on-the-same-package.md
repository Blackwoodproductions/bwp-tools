# Replace or remove keywords on the current package

The package stays; the keywords change. A BRON 90 customer wants two of their groups dropped and one new one added. `seo_add_keywords` can only add, and the dashboard's per-keyword delete leaves the old article live. The answer is `seo_change_package` with the domain's **current** `sku_id` and the **full** new set: whatever is not in the set is removed, whatever is new is added, everything else keeps its row untouched.

## Step 1: the current set

```sql
SELECT d.id, d.servicetype, s.servicetype AS package, s.keywords AS cap
  FROM bwp_domains d LEFT JOIN bwp_services s ON s.id = d.servicetype
 WHERE d.domain_name = 'summit-roofing-denver.com' AND d.deleted = 0;
-- 120877 | 397 | BRON 90 | 30
```

BRON 90's cap is **30 mains**; the 60 supports do not count. The domain holds 30 groups; three of them:

| main | supports |
|---|---|
| 588201 Roof Repair Denver | 690410 Emergency Roof Repair Denver · 690411 Roof Leak Repair Denver |
| 588202 Roof Replacement Denver | 690412 Asphalt Shingle Roof Denver · 690413 Metal Roof Installation Denver |
| 588203 Hail Damage Roof Denver | 690414 Hail Damage Roof Inspection · 690415 Storm Damage Roof Repair Denver |
| … 27 more | |

The customer wants `Roof Replacement Denver` and `Hail Damage Roof Denver` gone, and `Commercial Roofing Denver` added.

## Step 2: the CSV is the whole set

Every group the customer keeps must be on the list, with its two supports, or it is removed. The easiest way: export the current groups, delete two lines, add one.

```csv
Roof Repair Denver,Emergency Roof Repair Denver,Roof Leak Repair Denver
Commercial Roofing Denver,Flat Roof Repair Denver,TPO Roofing Denver
… the other 27 kept lines, unchanged …
```

BRON and SEOM lines take **exactly two** supports. One or three is refused with `seo-service 400: BRON 90 groups take exactly two supports: <main>`.

## Step 3: dry run

> Replace summit-roofing-denver.com's keywords with this list, same package (BRON 90, sku 397):
> ```
> Roof Repair Denver,Emergency Roof Repair Denver,Roof Leak Repair Denver
> Commercial Roofing Denver,Flat Roof Repair Denver,TPO Roofing Denver
> …
> ```

```text
seo_change_package(domain="summit-roofing-denver.com", confirm_domain="summit-roofing-denver.com", sku_id=397,
  groups=[{"main": "Roof Repair Denver", "supports": ["Emergency Roof Repair Denver", "Roof Leak Repair Denver"]},
          {"main": "Commercial Roofing Denver", "supports": ["Flat Roof Repair Denver", "TPO Roofing Denver"]},
          …])
```

```json
{
  "dryRun": true,
  "domainId": 120877,
  "from": "BRON 90",
  "to": "BRON 90",
  "keywordCap": 30,
  "keep": [
    { "id": 588201, "title": "Roof Repair Denver", "role": "main" },
    { "id": 690410, "title": "Emergency Roof Repair Denver", "role": "support", "main": "Roof Repair Denver" },
    { "id": 690411, "title": "Roof Leak Repair Denver", "role": "support", "main": "Roof Repair Denver" },
    … 28 kept mains and their 56 supports …
  ],
  "remove": [
    { "id": 588202, "title": "Roof Replacement Denver", "role": "main" },
    { "id": 588203, "title": "Hail Damage Roof Denver", "role": "main" },
    { "id": 690412, "title": "Asphalt Shingle Roof Denver", "role": "support", "main": "Roof Replacement Denver" },
    { "id": 690413, "title": "Metal Roof Installation Denver", "role": "support", "main": "Roof Replacement Denver" },
    { "id": 690414, "title": "Hail Damage Roof Inspection", "role": "support", "main": "Hail Damage Roof Denver" },
    { "id": 690415, "title": "Storm Damage Roof Repair Denver", "role": "support", "main": "Hail Damage Roof Denver" }
  ],
  "add": [
    { "title": "Commercial Roofing Denver", "role": "main" },
    { "title": "Flat Roof Repair Denver", "role": "support", "main": "Commercial Roofing Denver" },
    { "title": "TPO Roofing Denver", "role": "support", "main": "Commercial Roofing Denver" }
  ],
  "moneyUrlCleared": [],
  "legacyColumnCleared": true,
  "featuresSynced": true
}
```

Read the `keep` count first: 29 mains kept + 1 added = 30, at the cap. If the customer had sent 31 groups the dry run would stop with `seo-service 422: BRON 90 allows 30 keywords; this set keeps and adds 31 (1 over)`.

A removed main takes its two supports with it (they are listed so you can see them). A support that the new set keeps under a *different* main is kept and re-pointed, not removed.

`featuresSynced: true` means the domain's feature flags are re-synced from the package row even though the package did not change. That is harmless unless someone hand-tuned flags on this domain without turning Package Override on; if the preview shows `featuresSynced: false`, the override is on and the flags are left alone.

## Step 4: apply

Confirm; the assistant repeats the call with `dry_run=false`.

## Step 5: afterwards

- On a cut-over WordPress site, CADE's next link sync (about five minutes; `link_sync` to hurry it) trashes the two removed mains' Resources posts and articles and the four removed supports' articles, and creates the Resources post for `Commercial Roofing Denver`. Its article and its supports' articles follow on the domain's schedule.
- Partner links to the two removed pages disappear on the next render (PHP-plugin hosts) or the hosts' next link sync (WordPress hosts, within ~25 minutes). Links to the new main point at `/commercial-roofing-denver-<newid>/`, which forwards to the Resources post until the article exists.
- The legacy `bwp_domains.keywords` column is emptied, so a legacy admin save cannot bring the two removed mains back.

## Removing keywords only

Same call, a shorter list. A domain going from 30 groups to 20 sends the 20 it keeps; the preview shows 10 mains and 20 supports under `remove` and nothing under `add`. The cap is not an issue when shrinking.

## When a title appears twice in the live rows

Some older domains hold the same title twice in one role (a re-import that didn't clean up). The tool keeps the **oldest** row, since it is the one with the article, the Resources post and the placements, and lists the younger copy under `remove` with `duplicateOf`:

```json
"remove": [
  { "id": 612990, "title": "roof repair denver", "role": "main", "duplicateOf": 588201 }
]
```

That is expected. If you wanted the newer one for some reason, there is no switch; say so and leave the set as it is.

## Removing a single keyword

For one row, the dashboard's delete still works, and so does this tool with the full set minus that one line. Prefer the tool on a cut-over site: it is the only path that also trashes the WordPress article and Resources post.
