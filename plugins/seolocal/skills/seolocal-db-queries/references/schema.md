# SEO Local Legacy DB — schema reference

Detailed table reference for the `seolocal-db-queries` skill. Load only when you need column-level detail — for a high-level question, the discovery snippets in `SKILL.md` are usually enough.

## Canonical schema source

The legacy DB `freerele_blackwoodproductions` is **schema-identical to `bwp_seo`** (the database the modern Next.js dashboard queries via Prisma). The authoritative column reference is therefore the Prisma schema file:

```
packages/prisma/prisma/bwpSeo/schema.prisma
```

When the user asks about a column type, default value, nullability, or relation, **open that file first** instead of guessing or running `DESCRIBE`. It models 43 of the most-used tables.

**Naming convention:** the Prisma schema uses camelCase model + field names (e.g. `BwpDomains.domainName`) via `@map`/`@@map` directives. **In raw SQL you must use the underlying snake_case names** (e.g. `bwp_domains.domain_name`). When porting a Prisma query to SQL or vice versa, translate identifiers — never paste camelCase into SQL.

## Modeled vs unmodeled tables

The legacy DB contains **~180 tables** but the Prisma schema only models **~43** of them. The unmodeled tables fall into a few categories:

| Category | Examples | Notes |
|---|---|---|
| Per-feed log tables | `bwp_feed_log_172_16_224_110`, `bwp_feed_log_69_16_218_16` | One per ingestion source IP. Append-only, very large (~5M rows each). |
| Modification / activity logs | `bwp_modification_log`, `bwp_domainactivity_log`, `bwp_log_page` | Audit trails. Multi-million row scale. |
| Snapshots / backups | `bwp_bubblefeed_backup02132015`, `bwp_bubblefeed_copy`, `bwp_domain_feedstyle_old`, `tmp_*` | Point-in-time copies — usually **not** the source of truth. Confirm with the user before using. |
| Scratch / staging | `tmp_categories`, `tmp_reseller_performance`, `tmpreg`, `bwp_task_2_27_19` | Operational scratch. Treat as untrusted unless the user confirms. |
| Webdesk / webring | `bwp_webdesk*`, `bwp_webring*` | Legacy subsystems still in production but not modeled by the modern app. |
| Pure lookups | `bwp_country`, `bwp_states_us`, `bwp_states_canada`, `bwp_paypal_*` | Reference data. Schema rarely changes. |

For any unmodeled table use `SHOW COLUMNS FROM <table>` (cheap) or `information_schema.columns` (filterable).

---

## Curated table catalog

Grouped by domain concern. Counts are rough live row estimates from `information_schema` (planner stats — cheap, may lag).

### 1. Domain core

| Table | Rows | Purpose |
|---|---:|---|
| `bwp_domains` | ~60K | Root entity. One row per registered customer domain. Key columns: `id`, `userid` (→ `bwp_register.id`), `domain_name`, `domain_url`, `domain_country`, `status` (int), `package`/`packagepriceoverride`, `datesignup`. |
| `bwp_domains_disabled` | ~35K | Domains taken out of service. Mirrors `bwp_domains` columns. |
| `bwp_domain_settings` | ~19K | Per-domain feature flags and configuration. |
| `bwp_domain_status` | 13 | Lookup for `bwp_domains.status` integer codes. |
| `bwp_domains_subaccounts` | ~99 | Sub-account memberships under a domain. |
| `bwp_domain_overrides` | ~34 | Per-domain manual config overrides. |

### 2. Users & access

| Table | Rows | Purpose |
|---|---:|---|
| `bwp_register` | ~52K | User records. **PII-heavy** — emails, names, hashed passwords, contact info. The "user" referenced by `bwp_domains.userid`, `bwp_resellers.userid`, etc. **Never `SELECT *` here.** |
| `bwp_resellers` | ~1.1K | Reseller / whitelabel partner. **Look up by `userid`** (FK to `bwp_register.id`), **never by `id`** — see CLAUDE.md "Things Claude Should NOT Do". Filter on `deleted = 0` (soft delete) and `paid = 1` (active billing). |
| `bwp_affiliate` | ~534 | Affiliate accounts. |
| `bwp_password_resets` | ~2.3K | Password reset tokens. PII-adjacent. |
| `bwp_log_sessions` | ~32K | Auth session log. |
| `bwp_log_signup_attempt` | ~4.4K | Signup attempt audit log. |
| `bwp_user_sessions`, `bwp_user_tokens` | 0 | Legacy auth tables, currently empty. |

### 3. Billing

| Table | Rows | Purpose |
|---|---:|---|
| `bwp_orders` | ~73K | Customer orders. Key columns: `id`, `userid`, `domainid`, `service_id`, `service_charge` (double, not `total`), `customer_email`, `currency`, `status` (int), `order_date` (datetime), `commissionstatus`. |
| `bwp_order_commissions` | ~46K | Commission split per order. |
| `bwp_orders_batch`, `bwp_orders_batchitems` | ~98 / ~3K | Batch invoicing groups. |
| `bwp_order_status` | 10 | Status code lookup. |
| `bwp_invoices` | ~767 | Invoice records. |
| `bwp_invoice_status` | 10 | Status code lookup. |
| `bwp_creditcardinfo` | ~665 | **PCI-adjacent** — last-4 digits, expiry, billing address. **Never select full card data; mask before relaying.** |
| `bwp_log_paypal` | ~46K | PayPal IPN/callback log. |
| `bwp_payment_method`, `bwp_paypal_settings` | ~5 / ~3 | Configuration. |

### 4. Content & feed

| Table | Rows | Purpose |
|---|---:|---|
| `bwp_bubblefeed` | ~242K | Primary content feed records. |
| `bwp_bubblefeedsupport` | ~14K | Support metadata for feed entries. |
| `bwp_articles` | ~12K | Article content. |
| `bwp_cms`, `bwp_cmspages` | ~1K / ~1.7K | Generic CMS storage. |
| `bwp_domain_feedstyle`, `bwp_domain_feedstyle_alt` | ~5K / ~143 | Per-domain feed styling presets. |
| `bwp_link_placement` | ~71K | Link placement records. |
| `bwp_links`, `bwp_links_history` | ~1.4M / ~9K | Outbound links inventory + history. |

### 5. Ranking / SEO measurement

| Table | Rows | Purpose |
|---|---:|---|
| `bwp_ranking_overall_summary` | ~208K | Aggregated ranking summary. |
| `bwp_rank_summary` | ~21K | Per-keyword rank snapshot. |
| `bwp_rankensure`, `bwp_rankensure_dumb`, `bwp_rankensure_pubs` | ~821 / ~28K / ~16K | Rank-ensure subsystem state. |
| `rank_history` | ~11K | Historic rank trace. |
| `bwp_topkeywords` | ~87K | Per-domain top keywords. |
| `bwp_alternatekeywords`, `bwp_alternatekeywords_index` | ~21 / ~13 | Keyword alternates. |
| `bwp_semrush`, `bwp_semrush_keyword` | ~53K / ~3.6K | SEMrush data import. |
| `bwp_quicksprout` | ~1.9K | QuickSprout data import. |
| `bwp_pagerank` | ~630 | Legacy PageRank cache. |

### 6. Categories & lookups

| Table | Rows | Purpose |
|---|---:|---|
| `bwp_maincategories` | ~94 | Top-level category taxonomy. |
| `bwp_domain_category`, `bwp_domain_additional_categories`, `bwp_relatedcategories`, `bwp_category_exclusions` | ~1K / ~71 / ~1.4K / ~5 | Category-membership and relation tables. |
| `bwp_country`, `bwp_states_us`, `bwp_states_canada` | 259 / 58 / 13 | Geographic lookups. |

### 7. Operations & monitoring

| Table | Rows | Purpose |
|---|---:|---|
| `bwp_healthcheck` | ~77K | Health-check probe results. |
| `bwp_website_monitor`, `bwp_website_monitor_history` | ~1.9K / ~7.7K | Uptime monitoring. |
| `bwp_domain_uptime` | ~2.5K | Per-domain uptime stats. |
| `bwp_domain_malware` | ~4.6K | Malware scan results. |
| `bwp_log_email`, `bwp_log_seoreports`, `bwp_log_feed_api_calls`, `bwp_log_proxycall`, `bwp_log_payroll` | varies | Domain-specific operational logs. |
| `bwp_bannedips` | ~99 | IP blocklist. |

---

## Legacy schema gotchas

- **Status codes are integers, not strings.** `bwp_domains.status` is `int(10)`, not an enum. Cross-reference `bwp_domain_status` for the meaning of each code. Pass integer literals in `WHERE`, never quoted strings — see CLAUDE.md "Never pass string literals to Prisma `Int?` fields when porting PHP."
- **Soft deletes via `deleted` column.** Several tables (e.g. `bwp_resellers`) have a `deleted` int flag. `WHERE deleted = 0` for "live" rows.
- **No declared foreign keys on most tables.** Relations are by convention (`userid → bwp_register.id`, `domainid → bwp_domains.id`). Don't expect `information_schema.key_column_usage` to surface them.
- **Legacy date columns are `datetime`, not `timestamp`.** No timezone metadata. Treat values as the application's reporting timezone (US/Pacific historically).
- **`bwp_resellers` has both `id` and `userid`.** `id` is the autoincrement PK; `userid` is the FK to `bwp_register.id`. **Always look up resellers by `userid`** when the input is a "reseller id" or `underaffiliate` value — see CLAUDE.md.
- **Many fields are `varchar(255)` storing CSV / pipe-separated lists.** `bwp_register.domains`, `bwp_domains.keywords`, etc. Parse client-side; don't try to `IN`-match them in SQL.
