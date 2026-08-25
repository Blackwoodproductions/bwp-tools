# seolocal-db-queries — query recipes

Curated catalog of read-only SQL recipes for the legacy SEO Local MariaDB, grouped by area. Load on demand — for column-level reference, see `schema.md` and the canonical Prisma schema at `packages/prisma/prisma/bwpSeo/schema.prisma`.

## Conventions used in this file

- `'<hostname>'` — a domain hostname, e.g. `'seolocal.it.com'`
- `'<email>'` — a user email
- `<n>` — a numeric literal you choose
- All columns shown were verified against live `information_schema` at the time of writing.
- Every listing query has an explicit `LIMIT`. The wrapper auto-adds one if missing — but being explicit makes intent obvious.
- All queries are `SELECT/WITH/EXPLAIN/SHOW/DESCRIBE` only. The wrapper rejects anything else.

---

## 1. Domains

The root entity. Most investigations start here.

```sql
-- 1.1 Find a domain by hostname (the most common starting query)
SELECT id, userid, domain_name, domain_url, domain_country, status, datesignup
FROM bwp_domains
WHERE domain_name LIKE '<hostname>%'
ORDER BY id DESC
LIMIT 5;

-- 1.2 Domain breakdown by country (operational snapshot)
SELECT domain_country, COUNT(*) AS domains
FROM bwp_domains
GROUP BY domain_country
ORDER BY domains DESC
LIMIT 50;

-- 1.3 Domains by status code, joined to the lookup
SELECT s.id AS status_code, s.status AS status_name, s.Description, COUNT(d.id) AS domains
FROM bwp_domain_status s
LEFT JOIN bwp_domains d ON d.status = s.id
GROUP BY s.id, s.status, s.Description
ORDER BY domains DESC
LIMIT 25;

-- 1.4 Recently signed-up domains
SELECT id, userid, domain_name, domain_country, datesignup
FROM bwp_domains
WHERE datesignup IS NOT NULL
ORDER BY datesignup DESC
LIMIT 25;

-- 1.5 Per-user domain count (top customers)
SELECT userid, COUNT(*) AS domain_count
FROM bwp_domains
GROUP BY userid
ORDER BY domain_count DESC
LIMIT 25;

-- 1.6 Domain settings for one domain (joins to bwp_domain_settings)
SELECT d.id, d.domain_name, ds.useumami, ds.umamiid, ds.cade_level, ds.lastchurn
FROM bwp_domains d
LEFT JOIN bwp_domain_settings ds ON ds.domainid = d.id
WHERE d.id = <n>
LIMIT 1;
```

---

## 2. Users (bwp_register)

PII-heavy table. Project columns explicitly; never `SELECT *`. Mask emails when relaying results to the user.

```sql
-- 2.1 Look up a user by email (admin-safe projection)
SELECT id, firstname, lastname, usertype, status, deleted, active, createdDate, login_time
FROM bwp_register
WHERE email = '<email>'
LIMIT 1;

-- 2.2 User → all their domains
SELECT d.id, d.domain_name, d.status, d.datesignup
FROM bwp_register r
JOIN bwp_domains d ON d.userid = r.id
WHERE r.email = '<email>'
ORDER BY d.datesignup DESC
LIMIT 25;

-- 2.3 Recently created users
SELECT id, firstname, lastname, usertype, createdDate
FROM bwp_register
WHERE deleted = 0
ORDER BY createdDate DESC
LIMIT 25;

-- 2.4 Active vs deleted breakdown
SELECT deleted, active, COUNT(*) AS users
FROM bwp_register
GROUP BY deleted, active
LIMIT 25;

-- 2.5 Users without any domains (intake gap)
SELECT r.id, r.email, r.createdDate
FROM bwp_register r
LEFT JOIN bwp_domains d ON d.userid = r.id
WHERE d.id IS NULL
  AND r.deleted = 0
ORDER BY r.createdDate DESC
LIMIT 50;
```

---

## 3. Resellers

**Always look up by `userid`** — `bwp_resellers.id` is the autoincrement PK and almost never what you want. `DEFAULT_RESELLER_ID`, `bwp_register.underaffiliate`, and any "reseller id" reference all mean `userid`. See CLAUDE.md "Things Claude Should NOT Do".

```sql
-- 3.1 Look up the reseller record for a register-id (the canonical pattern)
SELECT id, userid, name, email, labeltype, paid, deleted, createddate
FROM bwp_resellers
WHERE userid = <register_id>
  AND deleted = 0
LIMIT 1;

-- 3.2 Active billing resellers (paid + not deleted)
SELECT id, userid, name, labeltype, currency, createddate
FROM bwp_resellers
WHERE deleted = 0
  AND paid = 1
ORDER BY createddate DESC
LIMIT 50;

-- 3.3 Resellers grouped by label type
SELECT labeltype, paid, deleted, COUNT(*) AS resellers
FROM bwp_resellers
GROUP BY labeltype, paid, deleted
ORDER BY resellers DESC
LIMIT 25;

-- 3.4 Reseller → all the users underneath it (via bwp_register.underaffiliate)
SELECT res.userid AS reseller_userid, res.name AS reseller_name,
       u.id AS user_id, u.email, u.createdDate
FROM bwp_resellers res
JOIN bwp_register u ON u.underaffiliate = res.userid
WHERE res.userid = <reseller_userid>
  AND res.deleted = 0
  AND u.deleted = 0
ORDER BY u.createdDate DESC
LIMIT 50;

-- 3.5 Reseller → domain count
SELECT res.userid AS reseller_userid, res.name, COUNT(d.id) AS domains
FROM bwp_resellers res
LEFT JOIN bwp_register u ON u.underaffiliate = res.userid
LEFT JOIN bwp_domains d ON d.userid = u.id
WHERE res.deleted = 0
GROUP BY res.userid, res.name
ORDER BY domains DESC
LIMIT 25;
```

---

## 4. Orders & billing

Money-touching tables. Don't aggregate without `WHERE status` — there are refunded and abandoned orders mixed in.

```sql
-- 4.1 Orders for a domain
SELECT id, userid, service_id, service_charge, currency, status, order_date
FROM bwp_orders
WHERE domainid = '<n>'
ORDER BY order_date DESC
LIMIT 25;

-- 4.2 Orders for a user
SELECT id, domainid, service_id, service_charge, currency, status, order_date
FROM bwp_orders
WHERE userid = <n>
ORDER BY order_date DESC
LIMIT 25;

-- 4.3 Order count + revenue by status (last 90 days)
SELECT status, COUNT(*) AS orders, SUM(service_charge) AS revenue
FROM bwp_orders
WHERE order_date >= DATE_SUB(NOW(), INTERVAL 90 DAY)
GROUP BY status
ORDER BY orders DESC
LIMIT 25;

-- 4.4 Daily order volume (last 30 days)
SELECT DATE(order_date) AS day, COUNT(*) AS orders, SUM(service_charge) AS revenue
FROM bwp_orders
WHERE order_date >= DATE_SUB(NOW(), INTERVAL 30 DAY)
GROUP BY DATE(order_date)
ORDER BY day DESC
LIMIT 30;

-- 4.5 Order → status text (joins lookup)
SELECT o.id, o.service_charge, o.currency, o.order_date,
       os.status AS status_name, os.Description
FROM bwp_orders o
LEFT JOIN bwp_order_status os ON os.id = o.status
WHERE o.id = <n>
LIMIT 1;

-- 4.6 Commissions per order (last 30 days)
SELECT o.id AS order_id, o.service_charge, c.commission, c.userid AS payee_id
FROM bwp_orders o
JOIN bwp_order_commissions c ON c.orderid = o.id
WHERE o.order_date >= DATE_SUB(NOW(), INTERVAL 30 DAY)
ORDER BY o.order_date DESC
LIMIT 25;
```

---

## 5. Ranking summary

`bwp_rank_summary` holds per-domain rank snapshots. Each row is the latest aggregate counts (top 10/20/30/40/50) per search engine.

```sql
-- 5.1 Latest rank summary for a domain
SELECT domainid, lastcheck, kwcount,
       google_10, google_20, google_30,
       bing_10, yahoo_10, duck_10
FROM bwp_rank_summary
WHERE domainid = <n>
ORDER BY lastcheck DESC
LIMIT 1;

-- 5.2 Top domains by tracked keyword count
SELECT domainid, kwcount, lastcheck
FROM bwp_rank_summary
ORDER BY kwcount DESC
LIMIT 25;

-- 5.3 Domains with stale rank data (no check in last 14 days)
SELECT rs.domainid, d.domain_name, rs.lastcheck
FROM bwp_rank_summary rs
JOIN bwp_domains d ON d.id = rs.domainid
WHERE rs.lastcheck < DATE_SUB(NOW(), INTERVAL 14 DAY)
ORDER BY rs.lastcheck ASC
LIMIT 25;
```

---

## 6. Link placement

```sql
-- 6.1 Active links for a domain (showing on domainid)
SELECT id, domainid, showondomainid, bubblefeedid, feedsection, created, lastchecked
FROM bwp_link_placement
WHERE showondomainid = <n>
  AND deleted = 0
ORDER BY created DESC
LIMIT 25;

-- 6.2 Link placement count by domain
SELECT domainid, COUNT(*) AS placements
FROM bwp_link_placement
WHERE deleted = 0
GROUP BY domainid
ORDER BY placements DESC
LIMIT 25;
```

---

## 7. Cross-cutting traces

Pivot from one identity (email, hostname, register id) into related rows.

```sql
-- 7.1 Email → user → resellers + domains + recent orders (one of each)
WITH user_row AS (
  SELECT id FROM bwp_register WHERE email = '<email>' LIMIT 1
)
SELECT 'user' AS kind, id AS ref_id, NULL AS extra FROM user_row
UNION ALL
SELECT 'reseller', userid, name FROM bwp_resellers
  WHERE userid IN (SELECT id FROM user_row) AND deleted = 0
UNION ALL
SELECT 'domain', id, domain_name FROM bwp_domains
  WHERE userid IN (SELECT id FROM user_row)
  ORDER BY ref_id DESC
LIMIT 25;

-- 7.2 Hostname → domain → owner + reseller + active link count
SELECT d.id AS domain_id, d.domain_name, d.userid,
       r.email AS owner_email,
       res.name AS reseller_name, res.userid AS reseller_userid,
       (SELECT COUNT(*) FROM bwp_link_placement lp
         WHERE lp.showondomainid = d.id AND lp.deleted = 0) AS active_links
FROM bwp_domains d
LEFT JOIN bwp_register r ON r.id = d.userid
LEFT JOIN bwp_resellers res ON res.userid = r.underaffiliate AND res.deleted = 0
WHERE d.domain_name = '<hostname>'
LIMIT 1;
```

---

## 8. Discovery shortcuts

When you don't know the column or even the table:

```sql
-- 8.1 Find tables matching a name pattern
SELECT table_name, table_rows
FROM information_schema.tables
WHERE table_schema = 'freerele_blackwoodproductions'
  AND table_name LIKE '%order%'
ORDER BY table_rows DESC
LIMIT 25;

-- 8.2 Find columns matching a name pattern (handy for figuring out FKs by convention)
SELECT table_name, column_name, data_type
FROM information_schema.columns
WHERE table_schema = 'freerele_blackwoodproductions'
  AND column_name LIKE '%domainid%'
ORDER BY table_name
LIMIT 100;

-- 8.3 Status code lookups
SELECT 'domain' AS kind, id, status AS status_name, Description FROM bwp_domain_status
UNION ALL SELECT 'order', id, status, Description FROM bwp_order_status
ORDER BY kind, id
LIMIT 50;
```
