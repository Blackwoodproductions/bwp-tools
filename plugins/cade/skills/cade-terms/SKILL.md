---
name: cade-terms
description: "WordPress duplicate category/tag cleanup for CADE domains — the term-merge flow. CHECK: scan term_merge_candidates for domains with proposed duplicate-term pairs (max 5 domains per run) and write an editable, reviewable plan.md per domain (idempotent; read-only apart from the plan files). MERGE: apply reviewed plans to the LIVE WP site — merge losers into winners, delete losers, rewrite stored content metadata, close candidates (applied → merged, plan-omitted → rejected, sticky). Also AUTO (apply only no-judgment groups) and VERIFY (assert an applied run landed). Environment-aware — defaults to --env production; outside local, merge requires --confirm. Use whenever the user asks to check for merge requests/candidates, see what term merges are waiting, scan for duplicate categories/tags, generate merge plans, apply/execute/approve a merge plan, merge the duplicates for a domain, auto-merge the safe groups, verify a merge, or invokes 'cade-terms'. Triggers on: 'check for term merges', 'any merge requests?', 'scan for duplicate terms', 'generate merge plans', 'apply the merge plan', 'run the merge', 'approve the term merges', 'merge the duplicates for <domain>', 'auto-merge the safe groups', 'verify the merge landed'. NEVER apply a plan the user has not reviewed — the plan.md edit is the approval artifact; auto mode is the ONLY exception, and only for groups that need no judgment."
---

# cade-terms

> **Paths:** `<skill-dir>` is this skill's directory — Claude Code prints it as *Base directory for this skill* when the skill loads. Run commands from the **cade-service repo root** so `.claude/skills.settings.{env}.json`, the repo `.env` (local) and `.claude/cade-terms-merge-runs/` resolve. One script, `<skill-dir>/scripts/merge_terms.py`, owns every mode below. Examples write `python` — use the cade-service `venv/bin/python`. Settings blocks per skill: see the bwp-tools README.

The term-merge flow (design W4, `docs/category-dedup/merge-playbook.md`): **check** writes reviewable plans, **merge** applies them, **auto** handles the no-judgment groups, **verify** asserts a run landed.

## Check — generate plans

Finds domains with `status='proposed'` rows in `term_merge_candidates` (blog + FAQ scope), takes the top **5** by pair count, and writes one editable `plan.md` per domain. A domain with a pending (unapplied) plan is skipped and its existing plan path printed — re-running is safe.

```bash
# default env is production
python <skill-dir>/scripts/merge_terms.py check --env production
python <skill-dir>/scripts/merge_terms.py check --env local
python <skill-dir>/scripts/merge_terms.py check --max-domains 10

# single explicit domain instead of the fleet scan
python <skill-dir>/scripts/merge_terms.py plan --domain example.com --env production
```

Output per domain: `.claude/cade-terms-merge-runs/<env>-<domain>-<ts>/plan.md` — groups of merges (WINNER + merge lines), plus zero-post delete rows. **Present the summary (groups/merges/deletes per domain and the plan paths) to the user**; the human review of plan.md is the approval step, then merge applies.

To clear the no-judgment groups first (normalized merges + never-was-a-candidate zero-post deletes) and leave only the semantic ones to review here, run **auto** mode, then re-run check.

Notes:
- "nothing to merge" / no domains listed → either genuinely clean, or the mirrors were never synced (`platform_connections.terms_synced_at IS NULL`) — check that before concluding the fleet is clean.
- Only candidate-bearing domains surface; a domain with only zero-post dead terms (no duplicate pairs) won't appear in check — use `plan --domain` for those.

## Merge — apply reviewed plans

Applies pending reviewed plans from `.claude/cade-terms-merge-runs/` — a plan is "pending" until its run dir contains `result.json`; only the newest pending plan per domain is applied.

```bash
# apply ALL pending reviewed plans for the env
python <skill-dir>/scripts/merge_terms.py merge --env production --confirm

# just one domain's pending plan / one explicit plan file
python <skill-dir>/scripts/merge_terms.py merge --domain example.com --env production --confirm
python <skill-dir>/scripts/merge_terms.py merge --plan <path>/plan.md --env local
```

What apply does per group: snapshot the losers' posts → move posts to the winner (id union; WP replaces lists wholesale) → force-delete the loser term → mirror row → `deleted` → rewrite `domain_content.meta` loser→winner (else a republish resurrects the duplicate) → resolve open suggestions matching the loser to the winner → applied candidates → `merged`, plan-omitted candidates → `rejected` (**sticky**) → `term_cache.clear_site`. `before-state.json` + `result.json` land next to the plan.

## Auto mode

Applies only the groups that need no judgment, with no hand review:

```bash
python <skill-dir>/scripts/merge_terms.py auto --env production --confirm
python <skill-dir>/scripts/merge_terms.py auto --env local --max-domains 3
```

- **Safe** = `normalized` merge groups (canonical-name equality) + `zero-post` deletes of terms that were never merge candidates.
- **Held back** = `semantic`, `mixed`, an unrecognized basis, and zero-post terms annotated `was a merge candidate`. Auto never offers these, so they stay `proposed` for the human loop — **it never sticky-rejects anything**. Run check afterwards to plan them.
- Skips any domain with a pending plan (a human's, or a crashed apply's `result.partial.json`). It never retries a crashed run — resolve that with `merge --domain`.
- Idempotent: a second pass reports `no safe groups`. Report the applied counts and the leftover review count to the user.

## Verify

```bash
python <skill-dir>/scripts/merge_terms.py verify --domain example.com --env production
```

Runs automatically at the end of every apply (WARN-only there — it never fails an apply). Standalone it exits 1 on failure. Checks: no `normalized` pair left proposed with both terms live, no retired name left in `domain_content.meta`, the loser 404s on the live site, and a post it carried now carries the winner. Results land in `verify.json` next to `result.json`.

## How it reaches WordPress

The app's `WordpressApiClient` — it already owns impersonation and direct-then-proxy fallback over `CRAWLER_PROXY_URLS` (triggered by captcha, non-JSON 403, 401-as-WAF-cooldown, or libcurl IP-block), with credentials masked in logs. Add `crawler_proxy_urls` to the settings block to enable proxies.

On top of it: **10 calls in flight** for post moves and pagination (WP has no batch endpoint), each thread on its own curl_cffi Session since a Session isn't shareable. A failure anywhere in a batch aborts the group *before* the term is deleted, so a post that never got the winner can't be stranded. `merge_terms.py selftest` checks the batch cap offline.

A 10-wide burst can open the client's per-site circuit breaker on a flaky site; that shows up as a crashed run (`result.partial.json`, dir stays pending, auto skips it) and is resolved with `merge --domain` — idempotent.

## Environment selection

**Default env is `production`.** Valid: `production`/`prod`, `staging`/`stg`, `local`/`dev`. Credentials come from `.claude/skills.settings.{env}.json` under the **`cade-terms-merge`** key; a missing block fails fast (no cross-env fallback), except `local` which falls back to the repo `.env`.

```json
{
  "cade-terms-merge": {
    "database_url": "postgresql://... (WRITE-capable role — not the cade-db-queries read-only one)",
    "credential_encryption_key": "<that env's Fernet key — decrypts WP app passwords>",
    "crawler_proxy_urls": "http://... (optional, Cloudflare-guarded sites)"
  }
}
```

The settings key stays `cade-terms-merge` (not `cade-terms`) — it matches `SKILL_KEY` in `scripts/load_settings.py`, so existing settings files keep working. Same for the run folder name, `.claude/cade-terms-merge-runs/`.

## Safety contract

1. **The reviewed plan file IS the approval.** Never run merge on a plan the user hasn't reviewed/edited; when the user says "approve", confirm which plan(s) and relay the group/merge/delete counts before applying.
2. **`--confirm` required outside `local`** — asserts the review happened.
3. **Applying a plan settles the whole domain**: every still-proposed candidate for that connection closes — pairs covered by applied groups → `merged`, everything else (including groups the reviewer deleted) → `rejected`, permanently (re-scans stop flagging them). There is no per-pair deferral: if the user isn't ready to decide some groups, don't apply that domain's plan yet — pending plans cost nothing.
4. A plan parsing to zero groups is **skipped** (candidates stay proposed) — delete its run dir to dismiss it.
5. Prod DB egress shares the `cade-db-queries` IP-allowlist caveat: silent TCP timeout = egress IP not allowlisted.
6. **Auto mode never applies a judgment call and never sticky-rejects.** It offers only `normalized` and never-was-a-candidate `zero-post` groups; everything it withholds settles as "never offered" and survives to the next scan. Rule 3 still holds for the groups auto DOES apply — but since it withholds the rest by construction, applying its plan cannot bury them.

## Related

- `docs/category-dedup/merge-playbook.md` — the full operator playbook (cade-service repo).
- `misc/term-merge/replay_thresholds.py` — threshold tuning against a raw WP dump.
