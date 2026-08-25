#!/usr/bin/env python
"""Term merge operator tool — the human-approval executor of design W4.

Environment-aware (`--env production` default; `staging`, `local`). Five
subcommands:

    check   Find domains with proposed merge candidates (max 5 per run) and
            write a merge plan per domain. Skips domains that already have a
            pending (unapplied) plan, so re-running is idempotent. READ-ONLY
            apart from the gitignored plan files.

    plan    Write a merge plan for ONE domain (same output, explicit target).

    merge   Apply pending reviewed plans (newest per domain), or one plan via
            --plan. LIVE WordPress + DB writes; outside --env local also
            requires --confirm.

    auto    Apply only the groups that need no judgement — normalized-name
            merges and zero-post deletes that were never merge candidates.
            Semantic/mixed groups are never offered, so they stay proposed
            for the human loop rather than being sticky-rejected. Skips any
            domain with a pending plan (a human's, or a crashed apply).

    verify  Re-assert an applied run: no normalized duplicate left proposed,
            no retired name left in domain_content.meta, and the live site
            agrees. Runs automatically after every apply; exits 1 on failure.

Run artifacts live in .claude/cade-terms-merge-runs/<env>-<domain>-<ts>/
(repo-anchored, gitignored): plan.md is the human review surface — the edit
IS the approval; before-state.json / result.json land next to it on apply.
A plan dir counts as "pending" until it contains result.json.

Apply, per merge group: snapshot the losers' posts -> move posts to the
winner (id union; WP replaces lists wholesale) -> delete the loser term ->
mirror row deleted -> rewrite domain_content.meta loser->winner (else a
republish resurrects the duplicate) -> resolve open suggestions matching the
loser to the winner -> mark applied candidates merged; plan-omitted
candidates rejected (sticky) -> clear the site's term cache.

WordPress is reached through the app's WordpressApiClient, which already owns
impersonation, direct-then-proxy fallback over CRAWLER_PROXY_URLS, retries and
the per-site circuit breaker. WordPress has no batch endpoint, so post moves and
pagination run `_BATCH` calls in flight, each thread on its own Session (a
curl_cffi Session is not shareable); a failure anywhere in a batch aborts the
group BEFORE its term is deleted.

Blog and FAQ scope. All `app.*` imports happen AFTER the env overrides land
(Settings reads env at import).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = next((p for p in [Path.cwd().resolve(), *Path.cwd().resolve().parents] if (p / ".claude").is_dir()), Path.cwd().resolve())  # ponytail: plugin copy — repo root = nearest ancestor of cwd with .claude/; run from the cade-service checkout
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from load_settings import load as load_skill_settings  # noqa: E402

# Scopes this tool will plan and apply. knowledge_base is excluded: the app
# adapter has no endpoint mapping for it (term_field raises), so admitting
# KB rows would only build groups that fail at apply time.
_MERGEABLE_CONTENT_TYPES = ("blog", "faq")


def _wp_endpoints():
    """The app owns scope -> WordPress endpoint strings; do not re-derive.

    Lazy import: no `app.*` may load before _configure_environment has put
    the target env in os.environ.
    """
    from app.infrastructure.adapters.wordpress import (  # noqa: PLC0415
        wordpress_term_merge_adapter as adapter,
    )

    return adapter


def _term_field(content_type: str, kind: str) -> str:
    return _wp_endpoints().term_field(content_type, kind)


def _term_path(content_type: str, kind: str) -> str:
    return _wp_endpoints().term_path(content_type, kind)


def _posts_path(content_type: str) -> str:
    return _wp_endpoints().posts_path(content_type)
_PAGE = 100
# WordPress ships no batch endpoint, so N calls in flight is the stand-in.
_BATCH = 10
# WordPress answers these when the term is already gone: 404 not found,
# 410 already trashed. A delete that finds nothing to delete has succeeded.
_ALREADY_GONE = (404, 410)
_RUN_DIR_RE = re.compile(r"^(?P<env>[a-z]+)-(?P<domain>.+)-\d{8}-\d{6}$")
_PLAN_DOMAIN_RE = re.compile(r"^# term-merge plan — (?P<domain>\S+)")


def _configure_environment(env_arg: str | None) -> str:
    """Point the app config at the target env BEFORE any `app.*` import.

    Env vars beat `.env` values in pydantic-settings, so overriding
    DATABASE_URL / CREDENTIAL_ENCRYPTION_KEY here retargets `SessionLocal`
    and credential decryption without touching the repo's `.env`. For
    `local` with no settings block, the repo `.env` is used as-is.
    """
    env, block = load_skill_settings(env_arg)
    if block:
        if not block.get("database_url"):
            raise SystemExit(
                f"'cade-terms-merge' block for env '{env}' is missing "
                "'database_url' (must be a WRITE-capable role — the merge "
                "updates mirror rows, suggestions, candidates, and meta)."
            )
        os.environ["DATABASE_URL"] = block["database_url"]
        if block.get("credential_encryption_key"):
            os.environ["CREDENTIAL_ENCRYPTION_KEY"] = block[
                "credential_encryption_key"
            ]
        if block.get("crawler_proxy_urls"):
            os.environ["CRAWLER_PROXY_URLS"] = block["crawler_proxy_urls"]
    return env


def _runs_root() -> Path:
    return _REPO_ROOT / ".claude" / "cade-terms-merge-runs"


def _pending_plans(env: str) -> dict[str, Path]:
    """Newest unapplied plan per domain: dir has plan.md but no result.json."""
    newest: dict[str, Path] = {}
    root = _runs_root()
    if not root.is_dir():
        return newest
    for run_dir in sorted(root.iterdir()):
        match = _RUN_DIR_RE.match(run_dir.name)
        if match is None or match.group("env") != env:
            continue
        if not (run_dir / "plan.md").is_file() or (run_dir / "result.json").is_file():
            continue
        newest[match.group("domain")] = run_dir / "plan.md"  # sorted -> last wins
    return newest


# ----------------------------------------------------------------------
# Shared resolution helpers
# ----------------------------------------------------------------------


def _resolve_connection(db, domain_name: str, mods):
    Domain = mods.models.Domain
    domain = db.query(Domain).filter(Domain.domain == domain_name).first()
    if domain is None:
        sys.exit(f"domain not found: {domain_name}")
    connection = mods.crud.crud_platform_connection.get_default_for_domain_platform(
        db, domain_id=str(domain.id), platform_type="wordpress"
    )
    if connection is None:
        sys.exit(f"no default WordPress connection for {domain_name}")
    return domain, connection


class _WP:
    """The app's WordpressApiClient plus one curl_cffi Session per thread.

    The client is stateless (it only reads settings in ``__init__``) and already
    owns the parts that matter: impersonate="chrome", direct-then-proxy fallback
    over CRAWLER_PROXY_URLS on captcha / non-JSON 403 / 401 / libcurl IP-block
    signatures, credential-masked logs, retry-with-backoff, and the per-site
    circuit breaker. Only the Session is unshareable, so only the Session is
    per-thread — that is what makes `_in_batches` safe.
    """

    def __init__(self, connection) -> None:
        from app.infrastructure.adapters.wordpress.worpress_api_client import (  # noqa: PLC0415
            WordpressApiClient,
        )

        credentials = connection.credentials_decrypted or {}
        self.site_url = connection.site_url
        self.client = WordpressApiClient()
        self._user = credentials.get("username", "")
        self._password = credentials.get("application_password", "")
        self._local = threading.local()
        self._opened: list = []
        self._lock = threading.Lock()

    @property
    def session(self):
        session = getattr(self._local, "session", None)
        if session is None:
            session = self.client._create_client(self._user, self._password)
            self._local.session = session
            with self._lock:
                self._opened.append(session)
        return session

    def get(self, path: str, **data):
        return self.client.get(
            self.site_url, self.session, path=path, data=data, cleanup=False
        )

    def post(self, path: str, data: dict):
        return self.client.post(
            self.site_url, self.session, path=path, data=data, cleanup=False
        )

    def delete(self, path: str, **data):
        return self.client.delete(
            self.site_url, self.session, path=path, data=data, cleanup=False
        )

    def close(self) -> None:
        with self._lock:
            for session in self._opened:
                session.close()
            self._opened.clear()


def _wp(connection) -> _WP:
    return _WP(connection)


def _in_batches(work: list, call):
    """Run `call` over `work`, `_BATCH` in flight, preserving order.

    Raises the first failure only after every task has settled. A partial
    failure MUST abort the group: the loser term is force-deleted right after
    the post moves, so a post that never received the winner would lose its
    only term.
    """
    if not work:
        return []
    results, error = [], None
    with ThreadPoolExecutor(max_workers=_BATCH) as pool:
        for future in [pool.submit(call, item) for item in work]:
            try:
                results.append(future.result())
            except Exception as exc:  # noqa: BLE001 - re-raised once all settle
                error = error or exc
    if error is not None:
        raise error
    return results


# ----------------------------------------------------------------------
# check / plan
# ----------------------------------------------------------------------


def _domains_with_candidates(db, mods, limit: int) -> list[tuple[str, int]]:
    """Domains carrying proposed mergeable-scope candidates, most pairs first."""
    import sqlalchemy as sa

    Candidate = mods.models.TermMergeCandidate
    Term = mods.models.PlatformTerm
    Connection = mods.models.PlatformConnection
    Domain = mods.models.Domain
    rows = (
        db.query(Domain.domain, sa.func.count().label("pairs"))
        .select_from(Candidate)
        .join(Term, Term.id == Candidate.term_a_id)
        .join(Connection, Connection.id == Candidate.connection_id)
        .join(Domain, Domain.id == Connection.domain_id)
        .filter(
            Candidate.status == "proposed",
            Term.content_type.in_(_MERGEABLE_CONTENT_TYPES),
        )
        .group_by(Domain.domain)
        .order_by(sa.desc("pairs"))
        .limit(limit)
        .all()
    )
    return [(row[0], int(row[1])) for row in rows]


def _build_entries(db, connection, mods) -> tuple[list, list]:
    """Everything a plan could offer for one domain: (entries, mirror rows).

    Blog and faq scope. KB mirror rows are excluded, which silently drops
    kb candidate pairs too (build_plan skips pairs whose terms aren't in
    rows_by_pk).
    """
    rows = [
        row
        for row in mods.crud.crud_platform_term.get_active_for_connection(
            db, connection_id=str(connection.id)
        )
        if row.content_type in _MERGEABLE_CONTENT_TYPES
    ]
    rows_by_pk = {str(row.id): row for row in rows}
    candidates = mods.crud.crud_term_merge_candidate.get_proposed_for_connection(
        db, connection_id=str(connection.id)
    )
    merge_entries = mods.merge_plan.build_plan(candidates, rows_by_pk)
    # Terms that WERE merge candidates but whose pairing didn't survive
    # grouping. They fall through to the zero-post sweep; the plan labels them
    # so a demotion doesn't read as a routine dead-term delete.
    # 3-tuples, matching build_zero_post_entries' key. A 2-tuple never
    # matches, the "was a merge candidate" note never attaches, and
    # partition_safe then AUTO-DELETES terms that are still open candidates.
    claimed = {
        (entry.content_type, entry.kind, platform_id)
        for entry in merge_entries
        for platform_id in (*entry.merge_ids, *filter(None, (entry.winner_id,)))
    }
    in_candidates = set()
    for candidate in candidates:
        for pk in (str(candidate.term_a_id), str(candidate.term_b_id)):
            row = rows_by_pk.get(pk)
            if row is not None:
                in_candidates.add(
                    (row.content_type, row.kind, str(row.platform_term_id))
                )
    # Zero-post DELETES stay blog-only. usage_count mirrors WordPress'
    # PUBLISHED count, so a CPT with no published posts (faqs still in draft,
    # or not launched) reads as "every term is dead" and the sweep would offer
    # the whole faq taxonomy for deletion. Merging faq duplicates is the
    # feature; purging the faq taxonomy is not.
    entries = merge_entries + mods.merge_plan.build_zero_post_entries(
        [row for row in rows if row.content_type == "blog"],
        merge_entries,
        in_candidates - claimed,
    )
    return entries, rows


def _write_run_dir(entries, rows, out_dir: Path, domain_name: str, mods,
                   *, note: str | None = None) -> None:
    """Lay down the two files a run dir needs before it can be applied."""
    rows_by_platform_id = {
        (row.content_type, row.kind, str(row.platform_term_id)): row for row in rows
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    text = mods.merge_plan.render_plan(
        entries, rows_by_platform_id, domain=domain_name
    )
    if note:
        # parse_plan ignores '#' lines, so the banner rides along inertly.
        lines = text.splitlines()
        lines.insert(1, f"# {note}")
        text = "\n".join(lines)
    (out_dir / "plan.md").write_text(text)
    # What the reviewer was OFFERED. Settling needs this to tell "operator
    # deleted this group" (-> rejected, sticky) from "grouping never proposed
    # the pair" (-> stays proposed for the next scan). Auto mode leans on the
    # same rule: it offers only safe groups, so the judgement calls it withheld
    # read as "never proposed" and survive to the next check.
    (out_dir / "offered.json").write_text(
        json.dumps(
            [
                {
                    "content_type": e.content_type,
                    "kind": e.kind,
                    "basis": e.basis,
                    "winner_id": e.winner_id,
                    "merge_ids": list(e.merge_ids),
                    "delete_ids": list(e.delete_ids),
                }
                for e in entries
            ],
            indent=1,
        )
    )


def _summarize(entries) -> str:
    merges = sum(len(e.merge_ids) for e in entries)
    deletes = sum(len(e.delete_ids) for e in entries)
    return f"groups={len(entries)} merges={merges} deletes={deletes}"


def _write_plan(db, domain, connection, out_dir: Path, mods) -> bool:
    """Generate one domain's plan. Returns False when there is nothing to do."""
    entries, rows = _build_entries(db, connection, mods)
    if not entries:
        print(f"  {domain.domain}: nothing to merge")
        return False
    _write_run_dir(entries, rows, out_dir, domain.domain, mods)
    print(
        f"  {domain.domain}: plan written — {_summarize(entries)}"
        f"\n    {out_dir / 'plan.md'}"
    )
    return True


def cmd_check(db, env: str, mods, max_domains: int) -> None:
    pending = _pending_plans(env)
    candidates = _domains_with_candidates(db, mods, limit=max_domains + len(pending))
    if not candidates:
        print("no domains with proposed merge candidates")
        return
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    written = 0
    for domain_name, pairs in candidates:
        if written >= max_domains:
            break
        if domain_name in pending:
            print(f"  {domain_name}: plan already pending — {pending[domain_name]}")
            continue
        domain, connection = _resolve_connection(db, domain_name, mods)
        out_dir = _runs_root() / f"{env}-{domain_name}-{stamp}"
        if _write_plan(db, domain, connection, out_dir, mods):
            written += 1
    print(
        f"check complete: {written} plan(s) written, "
        f"{len(pending)} already pending. Review each plan.md, then run merge."
    )


# ----------------------------------------------------------------------
# merge (apply)
# ----------------------------------------------------------------------


def _posts_with_term(client: _WP, content_type: str, kind: str,
                     term_id: str) -> list[dict]:
    """Every post carrying the term, any status. Pages fetched _BATCH at a time.

    Post path, filter param and `_fields` all derive from the same scope:
    request `_fields` for a taxonomy the post type does not have and the
    read-modify-write below starts from an empty set and wipes the post.
    """
    field = _term_field(content_type, kind)

    def fetch(number: int):
        return client.get(
            _posts_path(content_type),
            **{
                field: term_id,
                "per_page": _PAGE,
                "page": number,
                "_fields": f"id,{field},status",
                "status": "any",
            },
        )

    first = fetch(1)
    posts = list(first.json())
    total = int(first.headers.get("X-WP-TotalPages", "") or 0)
    if total > 1:
        for resp in _in_batches(list(range(2, total + 1)), fetch):
            posts.extend(resp.json())
        return posts
    # No usable X-WP-TotalPages (proxies and WAFs do strip headers) — walk.
    number = 1
    while len(posts) % _PAGE == 0 and posts:
        number += 1
        batch = fetch(number).json()
        posts.extend(batch)
        if len(batch) < _PAGE:
            break
    return posts


def _apply_entry(db, client: _WP, connection, entry, report: dict, mods) -> None:
    list_key = _term_field(entry.content_type, entry.kind)
    posts_path = _posts_path(entry.content_type)
    for loser_id in entry.merge_ids + entry.delete_ids:
        posts = _posts_with_term(client, entry.content_type, entry.kind, loser_id)
        report["snapshot"].append(
            {"term_id": loser_id, "content_type": entry.content_type,
             "kind": entry.kind, "posts": posts}
        )

        def move(post: dict, loser_id: str = loser_id) -> None:
            # WP replaces term lists wholesale, so this is read-modify-write.
            terms = set(post.get(list_key) or [])
            terms.discard(int(loser_id))
            if entry.winner_id is not None:
                terms.add(int(entry.winner_id))
            client.post(f"{posts_path}/{post['id']}", {list_key: sorted(terms)})

        # Raises if ANY post failed — before the delete below, so a post that
        # never got the winner can't be stranded by the term disappearing.
        _in_batches(posts, move)

        try:
            client.delete(
                f"{_term_path(entry.content_type, entry.kind)}/{loser_id}",
                force=True,
            )
        except mods.wp_4xx_error as exc:
            # 404/410 = the term is already gone (deleted in wp-admin, or by an
            # earlier group in this same plan). The deletion intent is already
            # satisfied, so retire the mirror row and carry on — one stale id
            # must not abort every remaining group. Anything else is real.
            if exc.status_code not in _ALREADY_GONE:
                raise
            print(f"    term {loser_id} already gone ({exc.status_code}) — retiring")
        _retire_in_db(db, connection, entry, loser_id, mods)
        report["executed"].append(
            {"term_id": loser_id, "content_type": entry.content_type,
             "kind": entry.kind, "winner": entry.winner_id,
             "posts_moved": len(posts)}
        )


def _retire_in_db(db, connection, entry, loser_platform_id: str, mods) -> None:
    PlatformTerm = mods.models.PlatformTerm
    TermSuggestion = mods.models.TermSuggestion
    # platform_terms is unique on (connection, content_type, kind, term id);
    # without the scope .first() can return the faq row for a blog merge.
    loser = (
        db.query(PlatformTerm)
        .filter(
            PlatformTerm.connection_id == str(connection.id),
            PlatformTerm.content_type == entry.content_type,
            PlatformTerm.kind == entry.kind,
            PlatformTerm.platform_term_id == str(loser_platform_id),
        )
        .first()
    )
    if loser is None:
        return
    winner = None
    if entry.winner_id is not None:
        winner = (
            db.query(PlatformTerm)
            .filter(
                PlatformTerm.connection_id == str(connection.id),
                PlatformTerm.content_type == entry.content_type,
                PlatformTerm.kind == entry.kind,
                PlatformTerm.platform_term_id == str(entry.winner_id),
            )
            .first()
        )

    # Blog only: content_metadata's categories/tags are the BLOG taxonomy's
    # buckets and nothing in the FAQ publish path reads them. Rewriting them for
    # an faq merge would strip a same-named blog category off every article.
    if entry.content_type == "blog":
        _rewrite_meta(
            db, connection, entry.kind, loser.name,
            winner.name if winner else None, mods,
        )

    # Open suggestions that would re-create the loser resolve to the winner.
    if entry.winner_id is not None:
        db.query(TermSuggestion).filter(
            TermSuggestion.domain_id == str(connection.domain_id),
            # Without the scope an faq merge of "Pricing" resolves a BLOG
            # suggestion to an faq term id — a cross-taxonomy id in
            # resolved_platform_term_id.
            TermSuggestion.content_type == entry.content_type,
            TermSuggestion.kind == entry.kind,
            TermSuggestion.normalized_name == mods.canonicalize(loser.name),
            TermSuggestion.status.in_(["pending", "parked"]),
        ).update(
            {
                "status": "matched",
                "resolved_platform_term_id": str(entry.winner_id),
            },
            synchronize_session=False,
        )

    loser.status = "deleted"
    db.commit()


def _rewrite_meta(db, connection, kind: str, loser_name: str, winner_name, mods) -> None:
    """domain_content.meta still holds loser names; a republish would
    resurrect the deleted term through the publish-time safety net."""
    DomainContent = mods.models.DomainContent
    key = "categories" if kind == "category" else "tags"
    rows = (
        db.query(DomainContent)
        .filter(DomainContent.domain_id == str(connection.domain_id))
        .all()
    )
    for row in rows:
        meta = row.content_metadata
        if meta is None:
            continue
        bucket = getattr(meta, key) or []
        if loser_name not in bucket:
            continue
        replacement = [name for name in bucket if name != loser_name]
        if winner_name and winner_name not in replacement:
            replacement.append(winner_name)
        setattr(meta, key, replacement)
        row.content_metadata = meta
    db.commit()


def _co_merged_pairs(groups, pk_by_platform) -> set:
    """Every unordered term pair that ends up inside one merge group.

    A pair is only settled as "merged" when BOTH its terms land in the SAME
    group: a plan that merges A and B into different winners says nothing
    about the pair A~B, and stamping it merged would silently bury it.
    """
    pairs = set()
    for group in groups:
        members = {
            pk_by_platform.get((group["kind"], platform_id))
            for platform_id in (
                *filter(None, (group["winner_id"],)),
                *group["merge_ids"],
            )
        }
        members.discard(None)
        for a in members:
            for b in members:
                if a != b:
                    pairs.add(frozenset((a, b)))
    return pairs


def _pk_by_platform(db, connection, mods) -> dict:
    """(kind, platform id) -> mirror pk for the connection's ACTIVE terms.

    Must be captured BEFORE the apply loop: `_retire_in_db` flips every loser
    to `deleted`, and a loser missing from this map makes `_co_merged_pairs`
    unable to form its pair — the candidate would then never be stamped
    `merged` and would sit `proposed` forever, pointing at a deleted term.
    """
    return {
        (row.kind, str(row.platform_term_id)): str(row.id)
        for row in mods.crud.crud_platform_term.get_active_for_connection(
            db, connection_id=str(connection.id)
        )
    }


def _close_candidates(db, connection, entries, offered, mods,
                      pk_by_platform) -> tuple[int, int]:
    """Settle the domain's proposed candidates against what the plan did.

    Three outcomes, because "rejected" is STICKY and must mean a human said
    no — not merely "absent from the plan":

    - merged   : both terms landed in the same surviving group.
    - rejected : the pair was OFFERED in one group and the reviewer removed it.
    - proposed : grouping never offered the pair (left alone; the next scan
                 re-surfaces it). Blanket-rejecting these buries valid
                 candidates forever.
    """
    surviving = _co_merged_pairs(
        [
            {
                "kind": e.kind,
                "winner_id": e.winner_id,
                "merge_ids": e.merge_ids,
            }
            for e in entries
        ],
        pk_by_platform,
    )
    # No offered.json (a plan written before this existed): fall back to
    # merges only, never rejecting on a guess.
    offered_pairs = _co_merged_pairs(offered or [], pk_by_platform)

    merged = rejected = 0
    for candidate in mods.crud.crud_term_merge_candidate.get_proposed_for_connection(
        db, connection_id=str(connection.id)
    ):
        pair = frozenset((str(candidate.term_a_id), str(candidate.term_b_id)))
        if pair in surviving:
            candidate.status = "merged"
            merged += 1
        elif pair in offered_pairs:
            candidate.status = "rejected"
            rejected += 1
    db.commit()
    return merged, rejected


def _apply_plan(db, plan_path: Path, mods, term_cache) -> None:
    text = plan_path.read_text()
    header = _PLAN_DOMAIN_RE.match(text.splitlines()[0]) if text else None
    if header is None:
        sys.exit(f"{plan_path}: not a merge plan (missing header line)")
    domain_name = header.group("domain")
    entries = mods.merge_plan.parse_plan(text)
    if not entries:
        print(f"  {domain_name}: plan has no surviving groups — skipped "
              "(candidates stay proposed; delete the run dir to dismiss)")
        return
    domain, connection = _resolve_connection(db, domain_name, mods)
    print(f"  {domain_name}: applying {len(entries)} group(s) -> {connection.site_url}")
    client = _wp(connection)
    report: dict = {"snapshot": [], "executed": []}
    out_dir = plan_path.parent
    offered_path = out_dir / "offered.json"
    offered = (
        json.loads(offered_path.read_text()) if offered_path.is_file() else None
    )
    # Snapshot while the losers are still active — see _pk_by_platform.
    pk_by_platform = _pk_by_platform(db, connection, mods)
    completed = False
    try:
        for entry in entries:
            _apply_entry(db, client, connection, entry, report, mods)
        completed = True
    finally:
        client.close()
        (out_dir / "before-state.json").write_text(
            json.dumps(report["snapshot"], indent=1)
        )
        # result.json is what marks a plan applied (_pending_plans keys off it),
        # so a crashed run must NOT write it — else a half-applied plan looks
        # finished and the next check happily writes a fresh one over the mess.
        (out_dir / ("result.json" if completed else "result.partial.json")).write_text(
            json.dumps(report["executed"], indent=1)
        )
    merged, rejected = _close_candidates(
        db, connection, entries, offered, mods, pk_by_platform
    )
    term_cache.clear_site(site_url=connection.site_url)
    # Moving posts changes the winners' usage_count, which nothing here writes
    # back. Without this re-sync the next plan reads stale zeros and proposes
    # deleting terms that now carry content.
    asyncio.run(
        mods.term_sync_service.sync_connection(db, connection_id=str(connection.id))
    )
    print(
        f"  {domain_name}: {len(report['executed'])} terms retired "
        f"({merged} candidates merged, {rejected} rejected) — results in {out_dir}/"
    )
    try:
        _verify_run(db, connection, out_dir, mods)
    except Exception as exc:  # noqa: BLE001 - a report, never a gate
        # The writes already landed; a failed check must not look like a failed
        # apply, and must never touch result.json.
        print(f"    WARN could not verify: {exc}")


def cmd_merge(db, env: str, mods, term_cache, plan: Path | None,
              domain_filter: str | None) -> None:
    if plan is not None:
        plans = [plan]
    else:
        pending = _pending_plans(env)
        if domain_filter:
            pending = {d: p for d, p in pending.items() if d == domain_filter}
        plans = list(pending.values())
    if not plans:
        print("no pending plans to apply — run check first")
        return
    for plan_path in plans:
        if not plan_path.is_file():
            sys.exit(f"plan not found: {plan_path}")
        _apply_plan(db, plan_path, mods, term_cache)


def cmd_auto(db, env: str, mods, term_cache, max_domains: int) -> None:
    """Apply the groups that need no judgement; leave the rest proposed.

    Only normalized merges and never-was-a-candidate zero-post deletes are
    written to the plan, so `_close_candidates` sees the semantic/mixed pairs
    as "never offered" and leaves them proposed for the human loop. Nothing
    auto mode declines is ever sticky-rejected.
    """
    pending = _pending_plans(env)
    candidates = _domains_with_candidates(db, mods, limit=max_domains + len(pending))
    if not candidates:
        print("no domains with proposed merge candidates")
        return
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    applied = held = 0
    for domain_name, _pairs in candidates:
        if applied >= max_domains:
            break
        if domain_name in pending:
            # Either a human plan is mid-review or an earlier apply crashed and
            # left result.partial.json. Auto must not act on top of either.
            print(f"  {domain_name}: plan already pending — {pending[domain_name]}")
            continue
        domain, connection = _resolve_connection(db, domain_name, mods)
        entries, rows = _build_entries(db, connection, mods)
        safe, review = mods.merge_plan.partition_safe(entries)
        held += len(review)
        if not safe:
            print(f"  {domain_name}: no safe groups "
                  f"({len(review)} need review)")
            continue
        out_dir = _runs_root() / f"{env}-{domain_name}-{stamp}"
        _write_run_dir(
            safe, rows, out_dir, domain_name, mods,
            note="AUTO-APPLIED safe groups only — not human-reviewed.",
        )
        print(f"  {domain_name}: auto-applying — {_summarize(safe)}")
        _apply_plan(db, out_dir / "plan.md", mods, term_cache)
        applied += 1
        if review:
            print(f"    {len(review)} group(s) need review — left proposed")
    print(
        f"auto complete: {applied} domain(s) applied, {held} group(s) left for "
        "review. Run check to plan those."
    )


# ----------------------------------------------------------------------
# verify
# ----------------------------------------------------------------------


def _latest_applied_run(env: str, domain: str) -> Path | None:
    """Newest run dir for this domain that ran to completion."""
    newest: Path | None = None
    root = _runs_root()
    if not root.is_dir():
        return None
    for run_dir in sorted(root.iterdir()):
        match = _RUN_DIR_RE.match(run_dir.name)
        if match is None or match.group("env") != env:
            continue
        if match.group("domain") != domain:
            continue
        if (run_dir / "result.json").is_file():
            newest = run_dir  # sorted -> last wins
    return newest


def _verify_run(db, connection, run_dir: Path, mods) -> bool:
    """Post-apply assertions — playbook step 2d, minus "watch the next scan".

    Prints one PASS/FAIL line per check and drops verify.json next to
    result.json. Never mutates anything.
    """
    executed = json.loads((run_dir / "result.json").read_text())
    checks: list[tuple[str, bool, str]] = []

    # (a) Every normalized duplicate this connection knew about is settled.
    # get_proposed_for_connection is content-type blind, so filter to the
    # scopes the plan was built from or kb pairs read as failures.
    plannable_pks = {
        str(row.id)
        for row in mods.crud.crud_platform_term.get_active_for_connection(
            db, connection_id=str(connection.id)
        )
        if row.content_type in _MERGEABLE_CONTENT_TYPES
    }
    still_open = [
        candidate
        for candidate in mods.crud.crud_term_merge_candidate.get_proposed_for_connection(
            db, connection_id=str(connection.id)
        )
        if candidate.basis == "normalized"
        and str(candidate.term_a_id) in plannable_pks
        and str(candidate.term_b_id) in plannable_pks
    ]
    checks.append(
        ("no normalized duplicates left proposed", not still_open,
         f"{len(still_open)} still proposed")
    )

    # (b) A retired name left in meta resurrects the term on the next publish.
    PlatformTerm = mods.models.PlatformTerm
    DomainContent = mods.models.DomainContent
    retired: list[tuple[str, str]] = []
    for item in executed:
        term = (
            db.query(PlatformTerm)
            .filter(
                PlatformTerm.connection_id == str(connection.id),
                PlatformTerm.content_type == item.get("content_type", "blog"),
                PlatformTerm.kind == item["kind"],
                PlatformTerm.platform_term_id == str(item["term_id"]),
            )
            .first()
        )
        if term is not None:
            retired.append(
                (item.get("content_type", "blog"), item["kind"], term.name)
            )
    content_rows = (
        db.query(DomainContent)
        .filter(DomainContent.domain_id == str(connection.domain_id))
        .all()
    )
    stale: list[str] = []
    # Blog only, mirroring _rewrite_meta: a retired faq name appearing in an
    # article's blog buckets is a name collision, not a leak.
    for content_type, kind, name in retired:
        if content_type != "blog":
            continue
        key = "categories" if kind == "category" else "tags"
        for row in content_rows:
            meta = row.content_metadata
            if meta is not None and name in (getattr(meta, key) or []):
                stale.append(f'"{name}" on domain_content {row.id}')
    checks.append(
        ("no retired names left in domain_content.meta", not stale,
         "; ".join(stale[:3]))
    )

    # (c) The live site agrees: the loser is gone, and a post it used to carry
    # now carries the winner.
    spot = next((item for item in executed if item.get("winner")), None)
    if spot is not None:
        client = _wp(connection)
        try:
            gone = False
            try:
                client.get(
                    f"{_term_path(spot.get('content_type', 'blog'), spot['kind'])}"
                    f"/{spot['term_id']}"
                )
            except mods.wp_4xx_error as exc:
                gone = exc.status_code in _ALREADY_GONE
            checks.append(
                (f"term {spot['term_id']} is gone from the live site", gone,
                 "it still resolves")
            )
            snapshot = json.loads((run_dir / "before-state.json").read_text())
            moved = next(
                (
                    post
                    for entry in snapshot
                    if str(entry["term_id"]) == str(spot["term_id"])
                    for post in entry["posts"]
                ),
                None,
            )
            if moved is not None:
                spot_scope = spot.get("content_type", "blog")
                key = _term_field(spot_scope, spot["kind"])
                post = client.get(
                    f"{_posts_path(spot_scope)}/{moved['id']}",
                    _fields=f"id,{key}",
                ).json()
                checks.append(
                    (f"post {moved['id']} carries winner {spot['winner']}",
                     int(spot["winner"]) in (post.get(key) or []),
                     "the winner is missing")
                )
        finally:
            client.close()

    for label, passed, detail in checks:
        print(f"    {'PASS' if passed else 'FAIL'}  {label}"
              + ("" if passed else f" — {detail}"))
    (run_dir / "verify.json").write_text(
        json.dumps(
            [
                {"check": c, "passed": p, "detail": "" if p else d}
                for c, p, d in checks
            ],
            indent=1,
        )
    )
    return all(passed for _, passed, _ in checks)


def cmd_verify(db, env: str, mods, domain_name: str, run: Path | None) -> None:
    _domain, connection = _resolve_connection(db, domain_name, mods)
    run_dir = run or _latest_applied_run(env, domain_name)
    if run_dir is None:
        sys.exit(f"no applied run found for {domain_name} in env {env}")
    if not (run_dir / "result.json").is_file():
        sys.exit(f"{run_dir}: no result.json — that plan was never applied")
    print(f"  {domain_name}: verifying {run_dir}")
    if not _verify_run(db, connection, run_dir, mods):
        sys.exit(1)


# ----------------------------------------------------------------------
# selftest — no network, no DB
# ----------------------------------------------------------------------


def selftest() -> None:
    """Prove the batching rules offline. No network, no DB."""
    import threading  # noqa: PLC0415

    # _in_batches really runs _BATCH at once: the barrier only releases when
    # _BATCH threads are inside `work` together, so a serial implementation
    # (or a smaller pool) times out instead of quietly passing.
    lock, live, peak = threading.Lock(), [0], [0]
    barrier = threading.Barrier(_BATCH)

    def work(n):
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        try:
            barrier.wait(timeout=10)
            return n * 2
        finally:
            with lock:
                live[0] -= 1

    assert _in_batches(list(range(_BATCH * 5)), work) == [
        n * 2 for n in range(_BATCH * 5)
    ]
    assert peak[0] == _BATCH, f"peak {peak[0]} in flight, expected {_BATCH}"
    assert _in_batches([], work) == []

    # A failure anywhere propagates — the caller must not go on to delete.
    def boom(n):
        if n == 7:
            raise RuntimeError("nope")
        return n

    try:
        _in_batches(list(range(20)), boom)
    except RuntimeError as exc:
        assert str(exc) == "nope"
    else:  # pragma: no cover
        raise AssertionError("_in_batches swallowed a failure")

    print(f"selftest OK (batch cap {_BATCH}, peak observed {peak[0]})")


# ----------------------------------------------------------------------
# entrypoint
# ----------------------------------------------------------------------


class _Mods:
    """Late-bound app modules, importable only after env configuration."""

    def __init__(self) -> None:
        from app import crud, models  # noqa: PLC0415
        from app.domain.content_publishing.terms import merge_plan  # noqa: PLC0415
        from app.domain.content_publishing.terms.normalize import (  # noqa: PLC0415
            canonicalize,
        )
        from app.domain.content_publishing.terms.sync_service import (  # noqa: PLC0415
            term_sync_service,
        )
        from app.infrastructure.adapters.wordpress.circuit_breaker import (  # noqa: PLC0415
            WordPress4xxError,
        )

        self.crud = crud
        self.models = models
        self.merge_plan = merge_plan
        self.canonicalize = canonicalize
        self.term_sync_service = term_sync_service
        self.wp_4xx_error = WordPress4xxError


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_env(p: argparse.ArgumentParser) -> None:
        p.add_argument("--env", default="production",
                       help="production (default) | staging | local")

    p_check = subparsers.add_parser("check", help="find domains + write plans")
    add_env(p_check)
    p_check.add_argument("--max-domains", type=int, default=5)

    p_plan = subparsers.add_parser("plan", help="write one domain's plan")
    add_env(p_plan)
    p_plan.add_argument("--domain", required=True)

    p_merge = subparsers.add_parser("merge", help="apply pending reviewed plans")
    add_env(p_merge)
    p_merge.add_argument("--domain", help="only this domain's pending plan")
    p_merge.add_argument("--plan", type=Path, help="apply exactly this plan file")
    p_merge.add_argument(
        "--confirm", action="store_true",
        help="required outside --env local (live WP + DB writes)",
    )

    p_auto = subparsers.add_parser(
        "auto", help="apply the safe groups; leave judgement calls proposed"
    )
    add_env(p_auto)
    p_auto.add_argument("--max-domains", type=int, default=5)
    p_auto.add_argument(
        "--confirm", action="store_true",
        help="required outside --env local (live WP + DB writes)",
    )

    p_verify = subparsers.add_parser("verify", help="assert an applied run landed")
    add_env(p_verify)
    p_verify.add_argument("--domain", required=True)
    p_verify.add_argument(
        "--run", type=Path, help="a specific run dir (default: newest applied)"
    )

    subparsers.add_parser("selftest", help="offline check of the batching rules")

    args = parser.parse_args()
    if args.command == "selftest":
        return selftest()
    env = _configure_environment(args.env)
    if args.command in ("merge", "auto") and env != "local" and not args.confirm:
        sys.exit(
            f"{args.command} on env '{env}' writes to the LIVE WordPress site "
            f"and the {env} database. Re-run with --confirm."
        )

    mods = _Mods()
    from app.db.session import SessionLocal  # noqa: PLC0415
    from app.infrastructure.adapters.wordpress.term_cache import (  # noqa: PLC0415
        term_cache,
    )
    db = SessionLocal()
    try:
        print(f"env={env}")
        if args.command == "check":
            cmd_check(db, env, mods, max_domains=args.max_domains)
        elif args.command == "plan":
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            domain, connection = _resolve_connection(db, args.domain, mods)
            out_dir = _runs_root() / f"{env}-{args.domain}-{stamp}"
            _write_plan(db, domain, connection, out_dir, mods)
        elif args.command == "auto":
            cmd_auto(db, env, mods, term_cache, max_domains=args.max_domains)
        elif args.command == "verify":
            cmd_verify(db, env, mods, domain_name=args.domain, run=args.run)
        else:
            cmd_merge(
                db, env, mods, term_cache,
                plan=args.plan, domain_filter=args.domain,
            )
    finally:
        db.close()


if __name__ == "__main__":
    main()
