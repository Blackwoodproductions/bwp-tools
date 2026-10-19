# Troubleshooting package and keyword changes

What a refusal means, what a surprising preview means, and what to check when the result on the sites is not what you expected. Messages come back as `seo-service <status>: <text>`; the connector's own refusals have no prefix.

## Refusals

| Message | Meaning | What to do |
|---|---|---|
| `confirm_domain must repeat the domain exactly (example.com); nothing was sent` | The connector's guard against a typo in the domain | Say the domain again; the assistant sets `confirm_domain` |
| `seo-service 403: you@blackwoodproductions.com is not an SEO Local staff admin` | Your `bwp_register` account is not `adminlevel 10`, or is not the one you signed in with | Ask Fernando to raise the level, or sign in with the right account |
| `seo-service 404: Domain 118204 not found` | The domain row is deleted, or the name resolved to the wrong id | Check `bwp_domains … AND deleted = 0` |
| `seo-service 400: Package 412 not found or inactive` | Wrong `sku_id`, or the package is `active = 0` | Look it up in `bwp_services`; activate it with `seo_update_package` if it should be sellable |
| `seo-service 400: Agentic 20 takes groups (main + 1..50 members), not keywords` | Shape mismatch for the **target** package | Rebuild the list as groups |
| `seo-service 400: SEOM 5 takes groups (main + 2 supports), not keywords` | same | same |
| `seo-service 400: Webworks Local 5 takes a flat keywords list, not groups` | same, the other way | Send `keywords=[…]` |
| `seo-service 400: BRON 90 groups take exactly two supports: Roof Repair Denver` | A SEOM/BRON line with one or three supports | Fix that line |
| `seo-service 400: Agentic 20 groups take at least one member: Invisalign Harborview` | An Agentic line with the main alone | Give it a member, or make it a member of another main |
| `seo-service 400: Send at least one keyword, or omit groups/keywords to change the package alone` | The set came through empty | A blank CSV is usually a parsing slip; to wipe a domain's keywords deliberately, use the dashboard |
| `seo-service 400: Empty keyword` / `Keyword over 255 characters: …` / `Invalid characters in keyword: Tools & Dies` | A **new** title fails the legacy rules (letters, digits, spaces, `- . ' ,`) | Reword it. Kept titles are never checked, so an old `&` title survives a change as long as it stays on the list |
| `seo-service 409: Keyword repeated in the set: plumber austin` | The same title twice in your list (case, spacing and entities ignored), in any role | Remove the duplicate line or cell |
| `seo-service 409: Keyword already on the domain or repeated: …` | `seo_add_keywords` only: the title is already live here | Use a package change with the full set if you mean to move it |
| `seo-service 422: Agentic 20 allows 20 keywords; this set keeps and adds 23 (3 over)` | Package change: kept + added mains and members exceed the cap | Shorten the set or pick a bigger package. Supports never count |
| `seo-service 422: BRON 90 allows 30 keywords; the domain has 28 active and this adds 4` | `seo_add_keywords` over the cap | same |
| `seo-service 422: Agentic members on this domain still link out instead of carrying agenticid; convert them with scripts/agentic-linkout-to-agenticid.ts first` | The domain is on the legacy Agentic pattern (member rows with a URL in `linkouturl`, no `agenticid`) | Run the conversion script in seolocal-app for this domain (it writes a reviewable plan first), then retry |
| `seo-service 422: SEOM 5 allows 5 keywords and the domain has 8 active. Send trimKeywords=true to deactivate the newest 3, or remove keywords first.` | A package change **without** a set into a smaller cap | Either send the set you want kept (the better option: you choose which keywords stay), or confirm `trim_keywords=true` |

## The preview is not what I expected

**A keyword I kept shows under `remove` and again under `add`.** Same title, different role. A SEOM support that becomes an Agentic member, a main demoted to a member, a member promoted to a main: each of those is a new row in a different table or with a different meaning, so the old row is retired and a new one inserted. The old article is retired with it. If you wanted the article kept, keep the keyword in its old role.

**A keyword I kept shows under `remove` with `duplicateOf`.** The domain holds that title twice in one role; the oldest row is kept and the younger copy removed. Expected; see [Replace or remove keywords](replace-keywords-on-the-same-package.md#when-a-title-appears-twice-in-the-live-rows).

**`keep` is missing a keyword that is on my list.** Compare the stored title and yours after decoding entities and collapsing spaces; a different word order or an extra word is a different keyword. Run

```sql
SELECT id, restitle FROM bwp_bubblefeed WHERE domainid = <id> AND deleted != 1 AND restitle LIKE '%drain%';
```

and fix the CSV line to match the stored wording.

**`moneyUrlCleared` lists rows I didn't touch.** Every kept SEOM/BRON main that carries a money URL is listed when the target is Agentic: on Agentic that column marks a legacy link-out and the link context would hide the row. The URL is emptied so the keyword stays visible. If the customer needs that money URL, it belongs on the Agentic main's page content, not on the row.

**`featuresSynced: true` on a same-package change.** The feature flags are re-copied from the package row. Only matters if someone hand-tuned this domain's flags without Package Override; then turn Package Override on first (dashboard) and the preview shows `false`.

## After the change

**A removed keyword's article is still live on the customer's WordPress site.**

1. Is the site cut over? `cutover_record(domain)` (wp-migration plugin) must show `stage: finish`. A site on the PHP plugin or Premium SEO has no WordPress articles for CADE to retire; its Resources page comes straight from bwp_seo and is already correct.
2. Has the link sync run since the change? It runs every five minutes, 25 domains per tick, stalest first. Ask for `link_sync(domain)` and read the result: `retired` is the number of keywords whose posts were trashed. `ok: false` with a `reason` means the run stopped before retiring anything; fix the reason (an unusable WordPress connection, a site that no longer declares `resource_posts`) and run again.
3. Did bwp really 404 the row? The retire only fires when seo-service answers 404 for the keyword, which it does for `deleted = 1`:
   ```sql
   SELECT id, restitle, deleted, active FROM bwp_bubblefeed WHERE id = <keyword id>;
   ```
   `deleted = 0` means the row was not removed by the change (check the audit row below); the link sync is right to keep it.
4. Still there? The link sync logs `bron_link_sync_retire_failed` with the keyword id when WordPress refuses the trash (a security plugin answering 403, for example). The row is kept and retried on the next full run; fix the site and run `link_sync` again.

**Partner sites still link to a removed keyword.** PHP-plugin and Premium SEO hosts render live and should be clean on the next view. A WordPress host rewrites on its own link sync, within about 25 minutes of the change; a host whose plugin keeps a saved copy of the page updates when its plugin next syncs. Placements themselves are cleaned up when services-app processes the domain, which the `lastlinkcheck` reset schedules for its next 5-minute batch:

```sql
SELECT lastlinkcheck FROM bwp_domains WHERE id = <id>;   -- NULL right after the change, a timestamp once processed
```

**The removed keywords came back.** That is the legacy page's `DomainKeywordsToBubblefeed` re-seeding from the comma column, which the tool empties:

```sql
SELECT keywords FROM bwp_domains WHERE id = <id>;   -- '' after a package change
```

If it is not empty, someone saved the legacy page with keywords typed into its box after the change; the titles in that box are what came back. Run the package change again with the right set and leave the legacy box empty.

**What exactly did the change write?** The audit row:

```sql
SELECT `date`, userid, `before`, `change` FROM bwp_modification_log
 WHERE `table` = 'bwp_domains' AND recordid = <id> ORDER BY id DESC LIMIT 3;
```

`change` is JSON with `via: "internal/package"`, `from`/`to`, `keep` and `remove` as row ids, `add` as titles, `moneyUrlCleared` as ids, and `userid` is the staff admin who confirmed it. A `seo_add_keywords` call writes `via: "internal/keywords"` on table `bwp_bubblefeed` with the inserted ids.

**The change failed halfway.** It can't: the package, the rows, the legacy column and the audit row are one transaction (120 s budget, enough for a 500-row CSV). A failure rolls everything back and the error names the cause; re-run the dry run after fixing it.
