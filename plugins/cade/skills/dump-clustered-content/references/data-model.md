# Data model — what `dump_clustered.py` reads

Map for anyone modifying the query (add a field, change platform, adjust the filter). The script
issues exactly one SELECT; everything below is read-only.

## Tables touched

| Table | Role in the dump |
|---|---|
| `domains` | `domain` string → the `--out/<domain>/` folder. Filter: `domain = ANY(%(domains)s)`. |
| `domain_content` | One row per article. `meta` (JSONB) → `metadata.json`; `meta->>'content_type'` drives the cluster filter. |
| `domain_keywords` | `keyword` → the per-article folder slug. |
| `content_publications` | The published artifacts. Two rows per clustered article on `platform = 'seomoney'`: one `publication_type = 'feedtext'`, one `publication_type = 'content'`. |

## The SEOMoney publication pair

A clustered (SEOMoney) article publishes two `content_publications` rows, both with
`platform = 'seomoney'`, distinguished by `publication_type`:

- `feedtext` → `content->>'feedtext'` is the feedtext HTML → `feedtext.html`
- `content`  → `content->>'data'` is the article body HTML → `data.html`

`content` is a JSONB column; the `->>'key'` operator extracts the text value. Each is pulled via
a `LATERAL` subquery taking the **latest** row (`ORDER BY published_at DESC NULLS LAST LIMIT 1`),
so re-publishes don't duplicate and the freshest artifact wins. Either side can be absent (an
article published only one type) — the script writes whatever exists and warns about the rest.

## The clustered-content filter (by content_type)

"Clustered content" = an article written from a keyword cluster (one head keyword + supporting
keywords) by CADE's cluster-mode pipeline. The pipeline tags it with one of **12 cluster content
types**. The filter is a direct match on `domain_content.meta->>'content_type'`:

```
location_hub            localized_service_page   service_page_national   cost_page
urgency_service_page    brand_service_page       symptom_diagnostic_page buyer_guide
comparison_page         regulatory_page          property_type_page      faq_cluster_page
```

**Source of truth** — keep `CLUSTER_CONTENT_TYPES` in the script in sync with:
- `ClusterClassifierResponseFormat.content_type` (the `Literal`) in
  `app/domain/content_generation/pipelines/content_brief/content_type_classifiers/clustered_content_type_classifier.py`
- the matching members of the `ContentType` enum in
  `app/domain/content_generation/models/planning/content_brief.py`

The OTHER 8 enum members are the single-keyword types and are intentionally excluded:
`listicle, tools_listicle, guide, comprehensive_guide, how_to_guide, article, brand_comparison, local_guide`.

`--all-types` drops the content_type clause entirely; `--content-types A,B,C` overrides the allowlist.

### Why content_type, not "supporting_keywords > 2"

The original one-off (`misc/extract_clustered.py`) filtered on `count(supporting_keywords) > 2`.
In a prod snapshot that gave identical results — the two content generations separate cleanly
(every cluster type sat at 3+ supporting keywords; every single-keyword type at ≤2). But the
count is a structural side-effect, not the definition: a cluster article with only 2 supporting
keywords, or a newer type like `cost_page`/`regulatory_page` (not yet seen in SEOMoney pubs),
could be misclassified. Filtering on `content_type` is exact and future-proof.

## Full query

`{content_type_clause}` is `AND dc.meta->>'content_type' = ANY(%(content_types)s)`, or empty for
`--all-types`.

```sql
SELECT d.domain,
       dk.keyword,
       dc.meta::text                AS meta_json,
       fp.content->>'feedtext'      AS feedtext_html,
       cp.content->>'data'          AS data_html
FROM domains d
JOIN domain_content dc  ON dc.domain_id = d.id
JOIN domain_keywords dk ON dk.id = dc.keyword_id
LEFT JOIN LATERAL (
    SELECT content FROM content_publications
    WHERE domain_content_id = dc.id
      AND platform = 'seomoney' AND publication_type = 'feedtext'
    ORDER BY published_at DESC NULLS LAST LIMIT 1
) fp ON true
LEFT JOIN LATERAL (
    SELECT content FROM content_publications
    WHERE domain_content_id = dc.id
      AND platform = 'seomoney' AND publication_type = 'content'
    ORDER BY published_at DESC NULLS LAST LIMIT 1
) cp ON true
WHERE d.domain = ANY(%(domains)s)
  AND EXISTS (SELECT 1 FROM content_publications x
              WHERE x.domain_content_id = dc.id AND x.platform = 'seomoney')
  {content_type_clause}
ORDER BY d.domain, dk.keyword;
```

Bind params: `%(domains)s` = list of domains, `%(content_types)s` = the cluster content-type
allowlist (omitted under `--all-types`).
