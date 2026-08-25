---
name: check-for-seomoney-content
description: Check whether one or more domains have SEOMoney content in the CADE prod DB, and report how many domain_content rows a SEOMoney purge would remove. Use this skill whenever the user asks whether a domain has seomoney publications/content, wants the pre-purge probe before running the seomoney-purge-regen recipe, or asks "are there content_publications with platform seomoney for X" / "does X have seomoney content" / "what domain_content is tied to X's seomoney pubs" / the direct invocation "/check-for-seomoney-content". Takes a comma- or space-separated domain list. Read-only against the database.
---

# check-for-seomoney-content

> **Paths:** `<skill-dir>` is this skill's directory — Claude Code prints it as *Base directory for this skill* when the skill loads (under `~/.claude/plugins/cache/bwp-tools/cade/<version>/skills/<name>`). Run commands from the **cade-service repo root** so `venv/bin/python`, `.claude/skills.settings.{env}.json` and `misc/` resolve.

The **pre-purge probe** for SEOMoney content. Given one or more domains, report whether they have
SEOMoney publications and exactly how many `domain_content` rows a purge would remove — the question
you answer before running the [[seomoney-purge-regen-recipe]]. Built on the same credentials and
read-only contract as the sibling `cade-db-queries` skill.

## When invoked

You are given a **comma- or space-separated list of domains** as the argument. Run the bundled
script, then relay the per-domain verdict and the purge-target total:

```bash
python <skill-dir>/scripts/check_seomoney.py "<domains>"
```

The script parses the domains itself (commas, spaces, pasted URLs), queries prod read-only, and
prints a report. You do not write SQL by hand.

## What it reports (per domain)

- **seomoney publications** — count of `content_publications` with `platform = 'seomoney'` linked
  to the domain via `domain_content`.
- **domain_content w/ seomoney** — the **PURGE TARGET**: distinct `domain_content` rows that have a
  seomoney pub. This is what recipe step 2 (`DELETE FROM domain_content WHERE EXISTS (seomoney pub)`)
  removes; everything else CASCADEs.
- **total domain_content** — all content rows for the domain (context).
- **orphan seomoney pubs (URL)** — seomoney pubs whose `platform_url` mentions the domain (seomoney
  `content` URLs embed the customer domain) but that are **not** linked to it via `domain_content`.
  A domain_content-only purge would miss these — investigate before purging if > 0.
- **all publications** — a `platform/type/status` breakdown so you can see what else exists (e.g.
  WordPress drafts) even when there is no seomoney content.
- **verdict** — one of: `nothing to purge`, `PURGE would remove N rows`, or the orphan warning.

## How to run

```bash
# Primary form — comma-separated (what /check-for-seomoney-content passes)
python <skill-dir>/scripts/check_seomoney.py "a.com,b.com"

# Space-separated also works
python <skill-dir>/scripts/check_seomoney.py a.com b.com

# Target staging / local credentials (only when the user explicitly says so)
python <skill-dir>/scripts/check_seomoney.py --env stg "a.com"

# Verify the domain parser without touching the DB
python <skill-dir>/scripts/check_seomoney.py --selftest
```

Flags: `--env {prod,stg,local}` (default `prod`), `--db NAME` (default the env's `db-default`),
`--timeout-ms` (default `30000`), `--selftest` (parser self-check, no DB).

## Environment selection

Defaults to **prod**. Reuses `cade-db-queries`' credential loader — same
`.claude/skills.settings.{env}.json` → `cade-db-queries` block. Only pass `--env stg` / `--env local`
when the user explicitly asks for a non-prod environment.

## Safety contract — read-only by construction

1. **Read-only session** — `conn.set_session(readonly=True)`; Postgres rejects any write server-side.
2. **SELECTs only** — the script issues the three SELECTs above plus `SET statement_timeout`.
3. **Statement timeout** — 30s default.

It writes no files and modifies no rows. **This skill does not purge.** The actual delete is a
separate, explicit step: the docker `postgres:16-alpine` psql shim in the seomoney-purge-regen
recipe (run only when the user explicitly asks to delete).

## Prerequisites

- Run from inside the **cade-service repo** (paths anchor to the repo root; reads
  `.claude/skills.settings.prod.json`).
- `.claude/skills.settings.prod.json` must contain the `cade-db-queries` block. This skill shares
  those credentials — it has none of its own.
- `psycopg2` in the active Python environment.
- Prod Postgres allowlists egress IPs; re-allowlist if a connection hangs after a network change.

## What this skill is NOT

- **Not a purge / DB write path** — read-only by design; use the recipe's psql shim to delete.
- **Not a content exporter** — to dump the actual feedtext/data bodies, use `dump-clustered-content`.
- **Not an ad-hoc query tool** — for arbitrary read-only SQL, use `cade-db-queries`.
