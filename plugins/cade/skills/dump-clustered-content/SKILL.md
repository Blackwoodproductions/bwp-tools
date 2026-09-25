---
name: dump-clustered-content
description: Export each domain's clustered SEOMoney publication pair (feedtext + content body) plus its content metadata from the CADE prod DB into per-keyword folders under misc/clustered/<domain>/<slug>/. Use this skill whenever the user wants to dump, export, extract, or pull the clustered content / SEOMoney feedtext+data for one or more domains — phrases like "dump clustered content for X", "export the clustered files for these domains", "extract the seomoney pairs for a.com,b.com", or the direct invocation "/dump-clustered-content". Takes a comma- or space-separated domain list as its argument. Read-only against the database.
---

# dump-clustered-content

> **Paths:** `<skill-dir>` is this skill's directory — Claude Code prints it as *Base directory for this skill* when the skill loads (under `~/.claude/plugins/cache/bwp-tools/cade/<version>/skills/<name>`). Run commands from the **cade-service repo root** so `venv/bin/python`, `.claude/skills.settings.{env}.json` and `misc/` resolve. Examples write `python` — use the cade-service `venv/bin/python`. Settings blocks per skill: see the bwp-tools README.

Export the **clustered SEOMoney publications** for one or more domains to local files. One job,
done safely and repeatably. Built on the same credentials and read-only contract as the sibling
`cade-db-queries` skill.

## When invoked

You are given a **comma- or space-separated list of domains** as the argument (e.g.
`"yesaba.com,brightercareaba.com"`). Run the bundled script with that list, then relay the
folder tally and any warnings back to the user:

```bash
python <skill-dir>/scripts/dump_clustered.py "<domains>"
```

The script parses the domains itself (handles commas, spaces, and pasted URLs), queries prod
read-only, and writes the folders. You do not need to write any SQL or build paths by hand.

## What it produces

For each domain, one folder per clustered article, named after the keyword slug:

```
misc/clustered/<domain>/<keyword-slug>/
├── feedtext.html    # content_publications.content->>'feedtext'  (SEOMoney feedtext pub)
├── data.html        # content_publications.content->>'data'      (SEOMoney content pub)
└── metadata.json    # domain_content.meta — title, slug, tags, summary, description,
                     # categories, content_type, publish_date, language, author
```

`feedtext.html` and `data.html` come from the **latest** SEOMoney `feedtext` and `content`
publications for that article; `metadata.json` is the pretty-printed `domain_content.meta`.

## What counts as "clustered content"

A clustered article is written from a **keyword cluster** (one head keyword + its supporting
keywords) by CADE's cluster-mode pipeline, which tags it with one of **12 cluster content
types**. The default filter keeps exactly those types and excludes the 8 single-keyword types
(`listicle`, `guide`, `local_guide`, `comprehensive_guide`, `how_to_guide`, `tools_listicle`,
`brand_comparison`, `article`).

The 12 cluster content types:

```
location_hub            localized_service_page   service_page_national   cost_page
urgency_service_page    brand_service_page       symptom_diagnostic_page buyer_guide
comparison_page         regulatory_page          property_type_page      faq_cluster_page
```

This list is the authoritative definition, sourced from the `ContentType` enum and the
`ClusteredContentTypeClassifier` in
`app/domain/content_generation/.../clustered_content_type_classifier.py`. The script filters on
`domain_content.meta->>'content_type'` against this list — a direct, semantic match, not a proxy.

> History: an earlier version filtered on "more than 2 supporting keywords." In current prod
> that gave identical results (the two generations of content separate cleanly), but it was a
> fragile proxy — a cluster article with only 2 supporting keywords, or one of the newer types
> like `cost_page`/`regulatory_page`, could slip through. Filtering on `content_type` is exact.

## How to run

```bash
# Primary form — comma-separated (what /dump-clustered-content passes)
python <skill-dir>/scripts/dump_clustered.py "a.com,b.com"

# Space-separated also works
python <skill-dir>/scripts/dump_clustered.py a.com b.com

# Target staging / local credentials (only when the user explicitly says so)
python <skill-dir>/scripts/dump_clustered.py --env stg "a.com"

# Narrow to specific content types
python <skill-dir>/scripts/dump_clustered.py --content-types buyer_guide,cost_page "a.com"

# Dump EVERYTHING published to SEOMoney, ignoring content type (includes local_guide, guide, ...)
python <skill-dir>/scripts/dump_clustered.py --all-types "a.com"

# Write somewhere other than misc/clustered/
python <skill-dir>/scripts/dump_clustered.py --out /tmp/dump "a.com"
```

Flags: `--env {prod,stg,local}` (default `prod`), `--content-types A,B,C` (override the 12-type
allowlist), `--all-types` (disable the content_type filter entirely — mutually exclusive with
`--content-types`), `--out PATH` (default `misc/clustered`), `--db NAME` (default the env's
`db-default`), `--timeout-ms` (default `30000`).

## Environment selection

Defaults to **prod**. The script reuses `cade-db-queries`' credential loader, so it reads the
same settings file: `.claude/skills.settings.{env}.json` → the `cade-db-queries` block. Only
pass `--env stg` / `--env local` when the user explicitly asks for a non-prod environment.

## Safety contract — read-only by construction

The script cannot mutate the database:

1. **Read-only session** — `conn.set_session(readonly=True)`; Postgres rejects any write server-side.
2. **Single SELECT** — the script only ever issues the one query above (plus `SET statement_timeout`).
3. **Statement timeout** — 30s by default, so a runaway query can't hammer prod.

The only things it writes are local files under `--out`. It does not publish, does not touch
WordPress, and does not modify any DB row.

## Examples

**Example 1 — single domain**
Input: `/dump-clustered-content "yesaba.com"`
Result: 16 folders under `misc/clustered/yesaba.com/`, each with the full
`{metadata.json, feedtext.html, data.html}` triple.

**Example 2 — multiple domains, comma-separated**
Input: `/dump-clustered-content "macraesfarmandranch.ca,brightercareaba.com"`
Result: 10 + 10 folders, no warnings.

**Example 3 — a domain that mixes local pages and cluster articles**
Input: `/dump-clustered-content "recoverywaysidaho.com"`
Result: 8 folders — only the cluster articles. The domain's ~80 `local_guide` pages are
single-keyword content and are excluded. Add `--all-types` to dump those too.

## Reading the output

- **`folder tally`** — folders written per domain, plus the total and which content-type filter
  was applied. Re-running a domain is idempotent: it overwrites with identical content.
- **`domains with NO matching content found`** — a requested domain produced 0 folders. Causes:
  the domain isn't in the DB (check spelling / TLD), it has no SEOMoney publications, or it has
  no articles of the selected content types. Try `--all-types` to see everything it published.
- **`warnings (folder still written)`** — an article was missing either its feedtext or its
  content pub. Normal when only one of the two publication types exists; the folder is still
  written with whatever is present.

The process exits non-zero (`1`) only if **zero** folders were written across all requested
domains — a clear signal nothing came back.

## Prerequisites

- Run from inside the **cade-service repo** (the script anchors paths to the repo root and reads
  `.claude/skills.settings.prod.json`).
- `.claude/skills.settings.prod.json` must contain the `cade-db-queries` block (host, port, user,
  pass, db-default). This skill shares those credentials — it has none of its own.
- `psycopg2` available in the active Python environment.
- Prod Postgres allowlists egress IPs. If the connection hangs/times out after a network change,
  re-allowlist the current IP (a reconnect can silently change it).

## Notes

- **Idempotent.** Re-running refreshes folders in place (`mkdir(exist_ok)` + overwrite).
- **Does not touch `misc/clustered.zip`.** If an archive is wanted, zip it separately.
- **Supersedes `misc/extract_clustered.py`** — the original scratch one-off this skill was
  generalized from. Prefer this skill going forward.
- For the exact tables/columns, the cluster content types, and the full query, see
  `references/data-model.md`.

## What this skill is NOT

- **Not a DB write path** — read-only by design.
- **Not a publisher / WordPress exporter** — it reads already-published SEOMoney rows, it does
  not publish or push anything.
- **Not an ad-hoc query tool** — for arbitrary read-only SQL, use `cade-db-queries`.
