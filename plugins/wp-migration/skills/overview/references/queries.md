# Queries

## Q1 — sites in flight (CADE, `execute_sql_cade`)
Every cutover not rolled back, plus finished ones still inside the 14-day watch.

```sql
SELECT d.domain, c.bwp_domain_id, c.started_by, c.stage, c.stage_at, c.running_stage, c.running_since,
       (c.running_stage IS NOT NULL AND c.running_since < now() - interval '15 minutes') AS stalled,
       left(c.last_error, 300) AS last_error, c.detected_state, c.verified_at, c.verify_failed,
       c.verify_running_since, c.link_synced_at
FROM bron_cutovers c JOIN domains d ON d.id = c.domain_id
WHERE c.stage IS DISTINCT FROM 'rolled_back'
  AND (c.stage IS DISTINCT FROM 'finish' OR c.stage_at > now() - interval '14 days')
ORDER BY c.started_by NULLS LAST, c.stage_at DESC NULLS LAST;
```
Filter to one operator with `AND c.started_by = '<email>'`, added before the ORDER BY line.

## Q2 — bwp health (bwp_seo, `execute_sql_seo`)
```sql
SELECT id, status, wp_plugin, script_version FROM bwp_domains WHERE id IN (<bwp_domain_id list from Q1>);
```
Skip Q2 if the list is empty (never `IN ()`).
Healthy after `finish` = `status = 2 AND wp_plugin = 1`.
