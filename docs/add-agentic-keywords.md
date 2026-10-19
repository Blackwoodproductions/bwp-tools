# Add Agentic keyword sets

An Agentic domain gets a new set: one **main** and its **members**. Until now Agentic was refused by `seo_add_keywords` ("add them from the dashboard"); it takes groups now.

## What an Agentic set is

| | Stored where | Has a page of its own | Linked from partner sites |
|---|---|---|---|
| main | `bwp_bubblefeed`, `agenticid NULL` | yes (article + Resources post) | yes |
| member | `bwp_bubblefeed`, `agenticid = main's id` | no; it gets a short summary and links to its main's page | no, only through the main |

So a member is not a SEOM/BRON support: it lives in the same table as the main, it counts against the package cap, and it never gets an article. Legacy Agentic members pointed at their main with a URL in `linkouturl` instead of `agenticid`; a domain still on that pattern has to be converted first (`scripts/agentic-linkout-to-agenticid.ts` in seolocal-app) before a package change will touch it. Adds don't check this; package changes do.

## Step 1: the domain and its cap

```sql
SELECT d.id, s.servicetype AS package, s.keywords AS cap,
       (SELECT COUNT(*) FROM bwp_bubblefeed b WHERE b.domainid = d.id AND b.active = 1 AND b.deleted != 1) AS active
  FROM bwp_domains d LEFT JOIN bwp_services s ON s.id = d.servicetype
 WHERE d.domain_name = 'harborview-dental.com' AND d.deleted = 0;
-- 131560 | Agentic 20 | 20 | 14
```

`active` counts mains **and** members (14 here). The cap is 20, so there is room for six more rows.

```sql
SELECT id, restitle, agenticid FROM bwp_bubblefeed WHERE domainid = 131560 AND deleted != 1 ORDER BY agenticid, id;
```

| id | restitle | agenticid |
|---|---|---|
| 655001 | Dentist Harborview | NULL |
| 655002 | Dental Implants Harborview | NULL |
| 655003 | Family Dentist Harborview | 655001 |
| 655004 | Emergency Dentist Harborview | 655001 |
| … | | |

## Step 2: the set

> Add this Agentic set to harborview-dental.com: main "Invisalign Harborview", members "Clear Aligners Harborview", "Invisalign Cost Harborview", "Invisalign vs Braces".

```text
seo_add_keywords(
  domain="harborview-dental.com",
  confirm_domain="harborview-dental.com",
  groups=[{"main": "Invisalign Harborview",
           "supports": ["Clear Aligners Harborview", "Invisalign Cost Harborview", "Invisalign vs Braces"]}]
)
```

The field is still called `supports` for every family; on Agentic its entries are the members. One to fifty per main.

Dry-run answer:

```json
{
  "dryRun": true,
  "domainId": 131560,
  "package": "Agentic 20",
  "keywordCap": 20,
  "activeKeywords": 14,
  "shape": "members",
  "mains": ["Invisalign Harborview"],
  "supports": [["Clear Aligners Harborview", "Invisalign Cost Harborview", "Invisalign vs Braces"]]
}
```

`shape: "members"` confirms the package was read as Agentic. 14 active + 1 main + 3 members = 18 of 20.

## Step 3: apply

Confirm; the assistant repeats with `dry_run=false`. The main is inserted first, then each member with `agenticid` set to the new main's id, in one transaction with an audit row (`via: "internal/keywords"`).

```sql
SELECT id, restitle, agenticid, status FROM bwp_bubblefeed WHERE domainid = 131560 AND id > 655010 ORDER BY id;
```

| id | restitle | agenticid | status |
|---|---|---|---|
| 660120 | Invisalign Harborview | NULL | publish |
| 660121 | Clear Aligners Harborview | 660120 | publish |
| 660122 | Invisalign Cost Harborview | 660120 | publish |
| 660123 | Invisalign vs Braces | 660120 | publish |

## What happens next

- The main is new content work for CADE on the domain's schedule: an article and, on a cut-over site, a Resources post (created by the next link sync, within about five minutes, so partner links to `/invisalign-harborview-660120/` have somewhere to land straight away).
- Members get short summaries from the summary backfill; they never get an article.
- Partner sites start placing links on the new main on services-app's next pass over the domain.

## Refusals you may see

| Message | Why | Fix |
|---|---|---|
| `seo-service 400: Agentic 20 takes groups (main + 1..50 members), not keywords` | You sent `keywords=[…]` to an Agentic domain | Send `groups` |
| `seo-service 400: Agentic 20 groups take at least one member: Invisalign Harborview` | A group with an empty `supports` | Give the main at least one member, or add it as part of a package change instead |
| `seo-service 409: Keyword already on the domain or repeated: Family Dentist Harborview` | The title is already a main or member here (case and entities ignored), or appears twice in your list | Drop it from the list; to move it to another main use a package change with the full set |
| `seo-service 422: Agentic 20 allows 20 keywords; the domain has 14 active and this adds 7` | Mains + members would pass the cap | Trim the set, or move the domain to a bigger package first |
| `seo-service 400: Invalid characters in keyword: Invisalign & Braces` | New titles allow letters, digits, spaces and `- . ' ,` only | Reword it (`and`); existing titles with other characters are fine, only new ones are checked |

## Adding to SEOM/BRON instead

Same tool, `groups` with exactly two supports per main, and only mains count against the cap:

```text
seo_add_keywords(domain="…", confirm_domain="…",
  groups=[{"main": "Dentist Harborview", "supports": ["Emergency Dentist Harborview", "Dental Cleaning Harborview"]}])
```

Flat packages (Webworks, SEOM Free) take `keywords=["…", "…"]`; those also get appended to the legacy comma column, which is where that package family's keyword list has always lived.
