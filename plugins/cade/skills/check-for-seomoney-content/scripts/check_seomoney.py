"""Check whether one or more domains have SEOMoney content — the pre-purge probe.

Answers the question you ask before running the SEOMoney purge/regen recipe:
"Does this domain have SEOMoney publications, and how many ``domain_content`` rows
would a purge remove?" Per domain it reports:

    - seomoney content_publications count            (via the domain_content join)
    - domain_content rows with a seomoney pub        (the PURGE TARGET)
    - total domain_content rows for the domain
    - orphan seomoney pubs by URL                    (pub URL mentions the domain but
                                                      is not linked to it — recipe gotcha)
    - a breakdown of ALL publications by platform    (context: what else exists)
    - a one-line verdict

Read-only by construction: reuses the cade-db-queries credentials and runs inside a
read-only psycopg2 session with a statement timeout. It only issues SELECTs — it never
writes to the database and writes no files. The actual purge is a separate, explicit
step (the psql shim in the seomoney-purge-regen recipe), NOT this skill.

Usage::

    python <skill-dir>/scripts/check_seomoney.py "a.com,b.com"
    python <skill-dir>/scripts/check_seomoney.py a.com b.com
    python <skill-dir>/scripts/check_seomoney.py --env stg "a.com"
    python <skill-dir>/scripts/check_seomoney.py --selftest
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import psycopg2

# Anchor to the repo root so the skill runs from any CWD.
REPO_ROOT = next((p for p in [Path.cwd().resolve(), *Path.cwd().resolve().parents] if (p / ".claude").is_dir()), Path.cwd().resolve())  # ponytail: plugin copy — repo root = nearest ancestor of cwd with .claude/; run from the cade-service checkout
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))  # <plugin>/scripts/load_settings.py

from load_settings import load as load_settings  # noqa: E402  (path injected above)

DEFAULT_TIMEOUT_MS = 30000

# Per-domain aggregates. LEFT JOINs so a domain with 0 domain_content still returns a
# row (all zeros); a domain absent from `domains` returns no row (reported "NOT IN DB").
SQL_SUMMARY = """
SELECT d.domain,
       d.id::text                                                     AS domain_id,
       count(DISTINCT dc.id)                                          AS total_dc,
       count(DISTINCT dc.id) FILTER (WHERE sm.id IS NOT NULL)         AS dc_with_seomoney,
       count(DISTINCT sm.id)                                          AS seomoney_pubs
FROM domains d
LEFT JOIN domain_content dc ON dc.domain_id = d.id
LEFT JOIN content_publications sm
       ON sm.domain_content_id = dc.id AND lower(sm.platform) = 'seomoney'
WHERE d.domain = ANY(%(domains)s)
GROUP BY d.domain, d.id;
"""

# What publications exist, by platform — the "only WP drafts" context.
SQL_BREAKDOWN = """
SELECT d.domain,
       lower(cp.platform)   AS platform,
       cp.publication_type,
       cp.status,
       count(*)             AS n
FROM content_publications cp
JOIN domain_content dc ON dc.id = cp.domain_content_id
JOIN domains d         ON d.id = dc.domain_id
WHERE d.domain = ANY(%(domains)s)
GROUP BY d.domain, lower(cp.platform), cp.publication_type, cp.status
ORDER BY d.domain, platform, cp.publication_type, cp.status;
"""

# Orphan gotcha: seomoney content pubs whose platform_url mentions the domain
# (seomoney `content` URLs embed the customer domain) but that are NOT linked to
# this domain's domain_content. A domain_content-only purge would miss these.
SQL_ORPHANS = """
SELECT r.domain, count(*) AS orphan_pubs
FROM (SELECT unnest(%(domains)s::text[]) AS domain) r
JOIN content_publications cp
  ON lower(cp.platform) = 'seomoney'
 AND cp.platform_url ILIKE '%%' || r.domain || '%%'
WHERE NOT EXISTS (
    SELECT 1 FROM domain_content dc JOIN domains d ON d.id = dc.domain_id
    WHERE dc.id = cp.domain_content_id AND d.domain = r.domain
)
GROUP BY r.domain;
"""


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
        for token in re.split(r"[,\s]+", arg):
            d = normalize_domain(token)
            if d and d not in seen:
                seen.add(d)
                out.append(d)
    return out


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Check whether domains have SEOMoney content (pre-purge probe).",
    )
    p.add_argument(
        "domains",
        nargs="*",
        help='Domains, comma- or space-separated: "a.com,b.com" or a.com b.com',
    )
    p.add_argument("--env", choices=("prod", "stg", "local"), default="prod",
                   help="Credentials env (default: prod).")
    p.add_argument("--db", default=None,
                   help="Database name override (default: the env's db-default).")
    p.add_argument("--timeout-ms", type=int, default=DEFAULT_TIMEOUT_MS,
                   help=f"statement_timeout in ms (default: {DEFAULT_TIMEOUT_MS}).")
    p.add_argument("--selftest", action="store_true",
                   help="Run the domain-parser self-check (no DB) and exit.")
    return p.parse_args(argv)


def _selftest() -> int:
    assert normalize_domain(" HTTPS://Foo.com/bar ") == "foo.com"
    assert normalize_domain("foo.com.") == "foo.com"
    assert parse_domains(["a.com, b.com", "a.com c.com"]) == ["a.com", "b.com", "c.com"]
    assert parse_domains(["  "]) == []
    print("selftest OK")
    return 0


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if args.selftest:
        return _selftest()

    domains = parse_domains(args.domains)
    if not domains:
        print("No valid domains parsed from arguments.", file=sys.stderr)
        return 2

    cfg = load_settings(start=REPO_ROOT, env=args.env)
    conn = psycopg2.connect(
        host=cfg["host"], port=cfg["port"], user=cfg["user"],
        password=cfg["pass"], dbname=args.db or cfg["db-default"],
    )
    conn.set_session(readonly=True, autocommit=False)

    params = {"domains": domains}
    try:
        with conn, conn.cursor() as cur:
            cur.execute(f"SET statement_timeout = {int(args.timeout_ms)};")
            cur.execute(SQL_SUMMARY, params)
            summary = {r[0]: r[1:] for r in cur.fetchall()}
            cur.execute(SQL_BREAKDOWN, params)
            breakdown: dict[str, list[tuple]] = {}
            for domain, platform, pub_type, status, n in cur.fetchall():
                breakdown.setdefault(domain, []).append((platform, pub_type, status, n))
            cur.execute(SQL_ORPHANS, params)
            orphans = {r[0]: r[1] for r in cur.fetchall()}
    finally:
        conn.close()

    purge_targets = 0
    for domain in domains:
        print(f"\n{domain}")
        if domain not in summary:
            print("  → NOT IN DB (check spelling / TLD)")
            continue
        domain_id, total_dc, dc_with_sm, sm_pubs = summary[domain]
        orphan_pubs = orphans.get(domain, 0)
        print(f"  domain_id                 : {domain_id}")
        print(f"  seomoney publications     : {sm_pubs}")
        print(f"  domain_content w/ seomoney: {dc_with_sm}   <- PURGE TARGET")
        print(f"  total domain_content      : {total_dc}")
        print(f"  orphan seomoney pubs (URL): {orphan_pubs}")
        rows = breakdown.get(domain, [])
        if rows:
            print("  all publications:")
            for platform, pub_type, status, n in rows:
                print(f"    {platform}/{pub_type}/{status}: {n}")

        if sm_pubs:
            purge_targets += dc_with_sm
            print(f"  → PURGE would remove {dc_with_sm} domain_content row(s) "
                  f"via {sm_pubs} seomoney pub(s)")
        elif orphan_pubs:
            print(f"  → NO linked seomoney content, but {orphan_pubs} orphan seomoney "
                  f"pub(s) match this domain by URL — investigate before purge")
        else:
            print("  → nothing to purge (no seomoney content)")

    print(f"\n=== TOTAL domain_content that a purge would remove: {purge_targets} ===")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
