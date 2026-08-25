"""Dump clustered (SEOMoney) publications for one or more domains.

"Clustered content" = an article written from a *keyword cluster* (one head keyword +
its supporting keywords) by CADE's cluster-mode pipeline. Those articles are tagged with
one of the 12 cluster content types (see ``CLUSTER_CONTENT_TYPES``), as opposed to the 8
single-keyword types (listicle, guide, local_guide, ...). The default filter selects exactly
those cluster content types.

For each requested domain, every ``domain_content`` of a cluster content type that also has
an SEOMoney publication is exported to::

    <out>/<domain>/<keyword-slug>/
        feedtext.html   <- content_publications.content->>'feedtext'  (seomoney feedtext)
        data.html       <- content_publications.content->>'data'      (seomoney content)
        metadata.json   <- domain_content.meta (pretty-printed)

Read-only by construction: reuses the cade-db-queries credentials and runs inside a
read-only psycopg2 session with a statement timeout. It only ever issues the single SELECT
below — it never writes to the database. The only writes are local files under ``--out``.

Usage::

    python <skill-dir>/scripts/dump_clustered.py "a.com,b.com"
    python <skill-dir>/scripts/dump_clustered.py a.com b.com
    python <skill-dir>/scripts/dump_clustered.py --env stg "a.com"
    python <skill-dir>/scripts/dump_clustered.py --all-types "a.com"
    python <skill-dir>/scripts/dump_clustered.py --content-types buyer_guide,cost_page "a.com"
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

import psycopg2

# Anchor everything to the repo root so the skill runs from any CWD.
REPO_ROOT = next((p for p in [Path.cwd().resolve(), *Path.cwd().resolve().parents] if (p / ".claude").is_dir()), Path.cwd().resolve())  # ponytail: plugin copy — repo root = nearest ancestor of cwd with .claude/; run from the cade-service checkout
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))  # <plugin>/scripts/load_settings.py

from load_settings import load as load_settings  # noqa: E402  (path injected above)

DEFAULT_OUT = REPO_ROOT / "misc" / "clustered"
DEFAULT_TIMEOUT_MS = 30000

# The 12 cluster-mode content types — the authoritative definition of "clustered content".
# Source of truth: the ClusterClassifierResponseFormat.content_type Literal in
#   app/domain/content_generation/pipelines/content_brief/content_type_classifiers/
#     clustered_content_type_classifier.py
# and the matching members of the ContentType enum in
#   app/domain/content_generation/models/planning/content_brief.py
# The cluster classifier emits these (keyword cluster -> one article); the OTHER 8 enum
# members (listicle, tools_listicle, guide, comprehensive_guide, how_to_guide, article,
# brand_comparison, local_guide) are the single-keyword types and are intentionally excluded.
# Keep this list in sync if new cluster archetypes are added to the enum.
CLUSTER_CONTENT_TYPES: tuple[str, ...] = (
    "location_hub",
    "localized_service_page",
    "service_page_national",
    "cost_page",
    "urgency_service_page",
    "brand_service_page",
    "symptom_diagnostic_page",
    "buyer_guide",
    "comparison_page",
    "regulatory_page",
    "property_type_page",
    "faq_cluster_page",
)

# Two LATERAL joins grab the latest SEOMoney feedtext pub and content pub for the article.
# {content_type_clause} is filled in at runtime (empty for --all-types).
SQL_TEMPLATE = """
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
      AND platform = 'seomoney'
      AND publication_type = 'feedtext'
    ORDER BY published_at DESC NULLS LAST
    LIMIT 1
) fp ON true
LEFT JOIN LATERAL (
    SELECT content FROM content_publications
    WHERE domain_content_id = dc.id
      AND platform = 'seomoney'
      AND publication_type = 'content'
    ORDER BY published_at DESC NULLS LAST
    LIMIT 1
) cp ON true
WHERE d.domain = ANY(%(domains)s)
  AND EXISTS (
      SELECT 1 FROM content_publications x
      WHERE x.domain_content_id = dc.id AND x.platform = 'seomoney'
  )
  {content_type_clause}
ORDER BY d.domain, dk.keyword;
"""

CONTENT_TYPE_CLAUSE = "AND dc.meta->>'content_type' = ANY(%(content_types)s)"


def slug(keyword: str) -> str:
    """Filesystem-safe folder name from a keyword (lowercase, non-alnum -> '_')."""
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", keyword.lower().strip())).strip("_")


def normalize_domain(raw: str) -> str:
    """Tolerate pasted URLs: strip scheme, path, surrounding whitespace and dots."""
    d = raw.strip().lower()
    if "://" in d:
        d = d.split("://", 1)[1]
    return d.split("/", 1)[0].strip().strip(".")


def parse_domains(raw_args: list[str]) -> list[str]:
    """Split comma- and/or space-separated args into a de-duplicated domain list."""
    out: list[str] = []
    seen: set[str] = set()
    for arg in raw_args:
        for token in arg.split(","):
            d = normalize_domain(token)
            if d and d not in seen:
                seen.add(d)
                out.append(d)
    return out


def split_csv(raw: str) -> list[str]:
    """Split a comma-separated override list (e.g. --content-types) into clean tokens."""
    return [t.strip().lower() for t in raw.split(",") if t.strip()]


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Dump clustered SEOMoney publications for one or more domains.",
    )
    p.add_argument(
        "domains",
        nargs="+",
        help='Domains, comma- or space-separated: "a.com,b.com" or a.com b.com',
    )
    p.add_argument(
        "--env",
        choices=("prod", "stg", "local"),
        default="prod",
        help="Credentials env (default: prod).",
    )
    p.add_argument(
        "--out",
        default=str(DEFAULT_OUT),
        help=f"Output root (default: {DEFAULT_OUT}).",
    )
    # Content-type selection. Default = the 12 cluster content types. The two overrides
    # are mutually exclusive: narrow the allowlist, or drop it entirely.
    type_group = p.add_mutually_exclusive_group()
    type_group.add_argument(
        "--content-types",
        default=None,
        metavar="A,B,C",
        help=(
            "Comma-separated content_type allowlist to use instead of the default 12 "
            "cluster types (e.g. buyer_guide,cost_page)."
        ),
    )
    type_group.add_argument(
        "--all-types",
        action="store_true",
        help="Disable the content_type filter — dump EVERY SEOMoney-published article "
        "for the domains, regardless of content type (includes local_guide, guide, ...).",
    )
    p.add_argument(
        "--db",
        default=None,
        help="Database name override (default: the env's db-default).",
    )
    p.add_argument(
        "--timeout-ms",
        type=int,
        default=DEFAULT_TIMEOUT_MS,
        help=f"statement_timeout in ms (default: {DEFAULT_TIMEOUT_MS}).",
    )
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    domains = parse_domains(args.domains)
    if not domains:
        print("No valid domains parsed from arguments.", file=sys.stderr)
        return 2

    # Resolve the content_type filter: None => no filter (--all-types).
    if args.all_types:
        content_types: list[str] | None = None
    elif args.content_types:
        content_types = split_csv(args.content_types)
    else:
        content_types = list(CLUSTER_CONTENT_TYPES)

    out_root = Path(args.out)
    cfg = load_settings(start=REPO_ROOT, env=args.env)

    conn = psycopg2.connect(
        host=cfg["host"],
        port=cfg["port"],
        user=cfg["user"],
        password=cfg["pass"],
        dbname=args.db or cfg["db-default"],
    )
    conn.set_session(readonly=True, autocommit=False)

    params: dict[str, object] = {"domains": domains}
    clause = ""
    if content_types is not None:
        clause = CONTENT_TYPE_CLAUSE
        params["content_types"] = content_types
    sql = SQL_TEMPLATE.format(content_type_clause=clause)

    try:
        with conn, conn.cursor() as cur:
            cur.execute(f"SET statement_timeout = {int(args.timeout_ms)};")
            cur.execute(sql, params)
            rows = cur.fetchall()
    finally:
        conn.close()

    per_domain: Counter[str] = Counter()
    warnings: list[str] = []

    for domain, keyword, meta_json, feedtext_html, data_html in rows:
        s = slug(keyword)
        if not s:
            warnings.append(f"{domain}: empty slug for keyword {keyword!r} — skipped")
            continue
        outdir = out_root / domain / s
        outdir.mkdir(parents=True, exist_ok=True)

        if feedtext_html is not None:
            (outdir / "feedtext.html").write_text(feedtext_html, encoding="utf-8")
        else:
            warnings.append(f"{domain}/{s}: no feedtext pub")
        if data_html is not None:
            (outdir / "data.html").write_text(data_html, encoding="utf-8")
        else:
            warnings.append(f"{domain}/{s}: no content (data) pub")

        meta = json.loads(meta_json) if meta_json else {}
        (outdir / "metadata.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        per_domain[domain] += 1
        print(f"  {domain}/{s}")

    selection = "all content types" if content_types is None else f"{len(content_types)} content type(s)"
    print(f"\n=== folder tally (out: {out_root}; filter: {selection}) ===")
    for domain in domains:
        print(f"  {domain}: {per_domain[domain]}")
    print(f"  TOTAL: {sum(per_domain.values())}")

    missing = [d for d in domains if per_domain[d] == 0]
    if missing:
        print("\n=== domains with NO matching content found ===")
        for d in missing:
            print(
                f"  ! {d} — not in DB, no SEOMoney pubs, or no articles of the selected "
                f"content types (try --all-types to see everything published)"
            )

    if warnings:
        print("\n=== warnings (folder still written) ===")
        for w in warnings:
            print(f"  ! {w}")

    return 0 if sum(per_domain.values()) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
