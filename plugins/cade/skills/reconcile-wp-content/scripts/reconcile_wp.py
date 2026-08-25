#!/usr/bin/env python
"""Reconcile one domain's content_publications (+ pages.url) against live WordPress.

Reads every wordpress content_publication for a domain, asks the live WP REST
API for each post's real status + permalink (authenticated, so it sees the exact
draft/publish/pending/private/future/trash), and reconciles:
  - content_publications.status   -> the real WP status
  - content_publications.platform_url -> the real permalink
  - pages.url                     -> the same permalink (crawl row keyed by url)

WordPress is reached the way CADE reaches it: curl_cffi with impersonate="chrome",
Basic app-password auth, and direct-then-proxy fallback over CRAWLER_PROXY_URLS
(a per-IP WAF 401 / non-JSON 403 / captcha / IP-block on the direct egress routes
to the proxy — the same reason CADE can read sites a plain request can't).

Read-only by default: writes reconcile SQL + a JSON report and prints the plan.
Only --apply --confirm executes the UPDATEs (one transaction, DB creds from the
cade-db-queries settings block).

Only --domain is required: site_url, username and application password fall back
to the domain's default wordpress platform_connection, decrypted with the Fernet
key in .claude/skills.settings.{env}.json. Any explicitly passed value wins.

MUST run under the repo venv (needs curl_cffi + cryptography):
  venv/bin/python <skill-dir>/scripts/reconcile_wp.py \
    --domain theposbrokers.com
"""
from __future__ import annotations

import argparse
import base64
import csv
import io
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

# WP post status -> content_publications.PublicationStatus value
WP_TO_DB = {
    "publish": "publish", "future": "scheduled", "draft": "draft",
    "pending": "pending", "private": "private", "trash": "trashed",
}
WP_STATUS_CSV = "publish,future,draft,pending,private,trash"
ROUTE = {"content": "posts", "faq": "faqs"}  # cade_faq CPT rest_base is 'faqs'
DB_SKILL_KEY = "cade-db-queries"
TERMS_SKILL_KEY = "cade-terms-merge"  # where credential_encryption_key actually lives today
CRED_KEY = "credential_encryption_key"


# --------------------------------------------------------------------------
# settings / repo discovery (mirrors cade-db-queries load_settings)
# --------------------------------------------------------------------------
def find_up(name_parts: list[str], start: Path | None = None) -> Path | None:
    start = (start or Path.cwd()).resolve()
    rel = Path(*name_parts)
    for base in [start, *start.parents]:
        if (base / rel).is_file():
            return base / rel
    return None


def settings_path(env: str) -> Path:
    path = find_up([".claude", f"skills.settings.{env}.json"])
    if not path:
        sys.exit(f"error: could not find .claude/skills.settings.{env}.json walking up from cwd")
    return path


def load_db_settings(env: str) -> dict:
    path = settings_path(env)
    data = json.loads(path.read_text())
    if DB_SKILL_KEY not in data:
        sys.exit(f"error: {path} is missing the '{DB_SKILL_KEY}' block")
    return data[DB_SKILL_KEY]


def load_encryption_key(env: str) -> str:
    """Fernet key that decrypts platform_connections.credentials_encrypted.

    Env var wins; otherwise the first settings block carrying the key. The
    explicit head of the search order keeps resolution deterministic — today
    the key lives in the cade-terms-merge block, not cade-db-queries.
    """
    if os.environ.get("CREDENTIAL_ENCRYPTION_KEY"):
        return os.environ["CREDENTIAL_ENCRYPTION_KEY"]
    path = settings_path(env)
    data = json.loads(path.read_text())
    for name in (DB_SKILL_KEY, TERMS_SKILL_KEY, *data):
        block = data.get(name)
        if isinstance(block, dict) and block.get(CRED_KEY):
            return block[CRED_KEY]
    sys.exit(f"error: no '{CRED_KEY}' in any block of {path} (and CREDENTIAL_ENCRYPTION_KEY "
             f"is unset). It normally lives in the '{TERMS_SKILL_KEY}' block.")


def parse_creds(ciphertext: str, key: str) -> tuple[str | None, str | None]:
    """Fernet-decrypt a credentials blob -> (username, application_password)."""
    from cryptography.fernet import Fernet  # lazy so --selftest needs no dep
    creds = json.loads(Fernet(key.encode()).decrypt(ciphertext.encode()).decode())
    return creds.get("username"), creds.get("application_password")


def load_proxies(args) -> list[str]:
    if args.no_proxy:
        return []
    if args.proxy_url:
        return [args.proxy_url]
    raw = os.environ.get("WP_PROXY_URL") or os.environ.get("CRAWLER_PROXY_URLS")
    if not raw:
        env_file = find_up([".env"])
        if env_file:
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if line.startswith("CRAWLER_PROXY_URLS"):
                    raw = line.split("=", 1)[1]
                    break
    if not raw:
        return []
    raw = raw.strip().strip('"').strip("'")
    return [p.strip().strip('"').strip("'") for p in raw.split(",") if p.strip()]


# --------------------------------------------------------------------------
# WordPress client — curl_cffi, impersonate=chrome, direct->proxy fallback
# --------------------------------------------------------------------------
class WPClient:
    def __init__(self, site_url: str, user: str, app_password: str,
                 proxies: list[str], timeout: int = 20):
        site = site_url.strip().rstrip("/")
        if "://" not in site:
            site = "https://" + site
        p = urlsplit(site)
        self.api = f"{p.scheme}://{p.netloc}/wp-json/wp/v2"
        self.host = p.netloc
        self.auth = "Basic " + base64.b64encode(f"{user}:{app_password}".encode()).decode()
        self.proxies = proxies
        self.timeout = timeout
        self.preferred = None  # egress that last worked (None=direct)
        from curl_cffi import requests as _cffi  # imported lazily so --selftest needs no dep
        self._cffi = _cffi

    def _egresses(self):
        order = [self.preferred] + [e for e in [None, *self.proxies] if e != self.preferred]
        # dedupe preserving order
        seen, out = set(), []
        for e in order:
            k = e or "__direct__"
            if k not in seen:
                seen.add(k)
                out.append(e)
        return out

    @staticmethod
    def _blocked(resp) -> bool:
        ct = resp.headers.get("Content-Type", "")
        if resp.status_code == 401:
            return True  # per-IP WAF/cooldown — CADE routes 401 to proxy fallback
        if resp.status_code == 403 and "application/json" not in ct:
            return True  # non-JSON 403 = WAF/firewall
        if "application/json" not in ct and resp.status_code == 200:
            body = resp.text[:400].lower()
            if any(m in body for m in ("just a moment", "captcha", "cf-chl", "cloudflare")):
                return True
        return False

    def request(self, path: str, params: dict | None = None):
        """Return (resp | None). Loops egresses; None if every egress was blocked/errored."""
        url = f"{self.api}{path}"
        last = None
        for egress in self._egresses():
            try:
                kw = {"impersonate": "chrome"}
                if egress:
                    kw["proxy"] = egress
                with self._cffi.Session(**kw) as s:
                    s.headers.update({"Authorization": self.auth})
                    resp = s.request("GET", url, params=params, timeout=self.timeout)
            except Exception as e:  # noqa: BLE001 - any transport failure -> next egress
                last = ("exc", repr(e)[:160])
                continue
            if self._blocked(resp):
                last = ("blocked", resp.status_code, resp.text[:160])
                continue
            self.preferred = egress
            return resp
        self._last_failure = last
        return None

    def _auth_via_collection(self):
        """Fallback credential probe. Many hosts block /wp/v2/users outright as
        user-enumeration hardening (403, often an HTML error page) even though the
        app password is perfectly good. `context=edit` on any collection is refused
        for anonymous callers, so a 200 here proves the credentials work."""
        r = self.request("/posts", {"context": "edit", "per_page": 1, "_fields": "id"})
        if r is not None and r.status_code == 200 and isinstance(_json_or_none(r), list):
            return f"authenticated via /posts?context=edit (/users/me blocked) via {self._egress_label()}"
        return None

    def validate_auth(self):
        r = self.request("/users/me", {"context": "edit", "_fields": "id,name"})
        if r is None:
            alt = self._auth_via_collection()
            if alt:
                return alt
            last = getattr(self, "_last_failure", None)
            hint = ""
            if last and last[0] == "blocked" and last[1] == 401:
                hint = (" — every egress returned 401, so the app password is likely wrong "
                        "(or all egresses are WAF-blocked). WP said: " + str(last[2])[:120])
            sys.exit(f"error: WP auth/reach failed on all egresses (direct + {len(self.proxies)} proxy){hint}")
        if r.status_code == 200:
            who = _json_or_none(r)
            if not isinstance(who, dict):
                alt = self._auth_via_collection()
                if alt:
                    return alt
                sys.exit("error: /users/me returned 200 with a non-JSON body on all egresses "
                         "(WAF interstitial or a plugin printing ahead of the REST response) — "
                         "the site is unreadable, not empty. First bytes: " + repr(r.text[:120]))
            return f"authenticated as {who.get('name')} (id {who.get('id')}) via {self._egress_label()}"
        alt = self._auth_via_collection()
        if alt:
            return alt
        sys.exit(f"error: WP auth failed ({r.status_code}) on all egresses: {r.text[:200]}")

    def _egress_label(self):
        if self.preferred is None:
            return "direct"
        return "proxy:" + (self.preferred.split("@")[-1] if "@" in self.preferred else self.preferred)

    def route_available(self, route: str) -> tuple[bool, bool]:
        """(available, certain). certain=False means the probe itself failed, so
        'absent' is unknown -- never a delete signal. Only 200/404 are conclusive."""
        r = self.request(f"/{route}", {"per_page": 1, "context": "edit", "_fields": "id"})
        if r is None:
            return False, False  # blocked on every egress != route absent
        return r.status_code == 200, r.status_code in (200, 404)

    def fetch_all(self, route: str) -> tuple[dict[str, dict], bool]:
        """post_id(str) -> {'status','link'} across every status incl trash.
        Second element is False if any page request failed: a partial sweep must
        never be read as 'these posts are gone'."""
        out: dict[str, dict] = {}
        page = 1
        while page <= 200:  # 100/page * 200 = 20k cap
            r = self.request(f"/{route}", {
                "context": "edit", "status": WP_STATUS_CSV, "per_page": 100,
                "page": page, "_fields": "id,status,link",
            })
            if r is None or r.status_code != 200:
                return out, False
            items = _json_or_none(r)
            if not isinstance(items, list):
                return out, False   # 200 with a non-JSON/wrong-shape body != empty site
            if not items:
                break
            for it in items:
                if not isinstance(it, dict) or "id" not in it:
                    return out, False
                out[str(it["id"])] = {"status": it.get("status"), "link": it.get("link")}
            total_pages = int(r.headers.get("X-WP-TotalPages", "1") or "1")
            if page >= total_pages:
                break
            page += 1
        return out, True


def _json_or_none(resp):
    """Parsed JSON, or None if the body is not JSON. A WAF challenge / error page
    served with a 200 must be treated as an unreadable response, never as data."""
    try:
        return resp.json()
    except Exception:
        return None


# --------------------------------------------------------------------------
# DB access via psql (mirrors cade-db-queries)
# --------------------------------------------------------------------------
def _psql_cmd(cfg: dict, db: str | None, csv_out: bool) -> list[str]:
    cmd = ["psql", "-h", cfg["host"], "-p", str(cfg["port"]), "-U", cfg["user"],
           "-d", db or cfg.get("db-default"), "--no-psqlrc", "-v", "ON_ERROR_STOP=1", "-X"]
    cmd += ["--csv"] if csv_out else ["-A", "-F", "\t"]
    return cmd


def db_read(cfg: dict, sql: str, db: str | None) -> list[dict]:
    wrapped = f"BEGIN READ ONLY;\nSET LOCAL statement_timeout = 30000;\n{sql.rstrip().rstrip(';')};\nROLLBACK;\n"
    env = {**os.environ, "PGPASSWORD": cfg["pass"]}
    proc = subprocess.run(_psql_cmd(cfg, db, True), input=wrapped, text=True, env=env, capture_output=True)
    if proc.returncode != 0:
        sys.exit(f"error: DB read failed:\n{proc.stderr}")
    lines = [ln for ln in proc.stdout.splitlines() if ln not in ("BEGIN", "SET", "ROLLBACK")]
    return list(csv.DictReader(io.StringIO("\n".join(lines))))


def db_apply(cfg: dict, sql_text: str, db: str | None) -> str:
    env = {**os.environ, "PGPASSWORD": cfg["pass"]}
    proc = subprocess.run(_psql_cmd(cfg, db, False), input=sql_text, text=True, env=env, capture_output=True)
    if proc.returncode != 0:
        sys.exit(f"error: apply failed (transaction rolled back):\n{proc.stderr}")
    return proc.stdout + proc.stderr


def resolve_connection(cfg: dict, env: str, domain: str, db: str | None) -> tuple[str, str | None, str | None]:
    """(site_url, username, app_password) from the domain's default WP connection.

    site_url is a column; username/password come out of the Fernet blob in
    credentials_encrypted. is_default first — the partial unique index allows
    at most one default per (domain, platform), but sibling rows are legal.
    """
    rows = db_read(cfg, (
        "SELECT pc.site_url, pc.credentials_encrypted "
        "FROM platform_connections pc "
        "JOIN domains d ON d.id = pc.domain_id "
        f"WHERE lower(d.domain) = lower({q(domain)}) AND pc.platform_type = 'wordpress' "
        "ORDER BY pc.is_default DESC, pc.updated_at DESC"), db)
    if not rows:
        sys.exit(f"error: no wordpress platform_connection for '{domain}'. "
                 f"Pass --site-url/--user/--app-password explicitly.")
    if len(rows) > 1:
        print(f"[creds] note: {len(rows)} wordpress connections for {domain}; "
              f"using the default/most-recent ({rows[0]['site_url']}).", flush=True)
    site_url = rows[0]["site_url"]
    ct = (rows[0].get("credentials_encrypted") or "").strip()
    if not ct:
        return site_url, None, None  # SEOMoney-style credential-less row
    try:
        user, app_pw = parse_creds(ct, load_encryption_key(env))
    except Exception as e:  # noqa: BLE001 - bad key, rotated key, corrupt blob
        sys.exit(f"error: could not decrypt credentials for '{domain}' ({type(e).__name__}). "
                 f"Wrong {CRED_KEY} for --env {env}?")
    return site_url, user, app_pw


# --------------------------------------------------------------------------
# diff + SQL
# --------------------------------------------------------------------------
def norm_url(u: str | None) -> str:
    if not u:
        return ""
    p = urlsplit(u)
    host = p.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return f"{host}{p.path.rstrip('/')}"


def confirm_missing(wp, rows, wp_maps, route_available):
    """A post absent from the paged sweep is NOT proof it is gone: offset pagination
    drifts when the site publishes mid-sweep, silently skipping a row. Absence only
    becomes a delete signal after a direct per-id GET returns 404.
    Rescued posts are merged into wp_maps. Returns (unreadable_ids, rescued_count)."""
    unread, rescued = [], 0
    for r in rows:
        typ, pid = r["publication_type"], r["platform_post_id"]
        if not route_available.get(typ, True) or pid in wp_maps.get(typ, {}):
            continue
        resp = wp.request(f"/{ROUTE[typ]}/{pid}", {"context": "edit", "_fields": "id,status,link"})
        if resp is None or resp.status_code not in (200, 404):
            unread.append(f"{ROUTE[typ]}/{pid}")
        elif resp.status_code == 200:
            j = _json_or_none(resp)
            if not isinstance(j, dict):
                unread.append(f"{ROUTE[typ]}/{pid}")   # unparseable 200 is not a delete signal
                continue
            wp_maps.setdefault(typ, {})[pid] = {"status": j.get("status"), "link": j.get("link")}
            rescued += 1
    return unread, rescued


def build_plan(rows, wp_maps, route_available, domain):
    cp_updates, pages_updates, mismatches = [], [], []
    from collections import Counter
    kinds = Counter()
    for r in rows:
        typ, pid = r["publication_type"], r["platform_post_id"]
        db_status, db_url = r["status"], r["platform_url"]
        wp = wp_maps.get(typ, {}).get(pid)
        if wp is None:
            if not route_available.get(typ, True):
                obs, kind = "faq_route_missing", "gone_route_missing"
            else:
                obs, kind = "not_found", "gone_deleted"
            if db_status != "trashed":
                kinds[kind] += 1
                cp_updates.append((r["id"], "trashed", None))
                mismatches.append(_mm(r, kind, None, None, "trashed", None, obs))
            continue
        tgt_status = WP_TO_DB.get(wp["status"], wp["status"])
        tgt_url = wp["link"]
        status_diff = db_status != tgt_status
        url_diff = norm_url(db_url) != norm_url(tgt_url)
        if not (status_diff or url_diff):
            continue
        kind = ("status_and_url" if status_diff and url_diff
                else "status_only" if status_diff else "url_only")
        kinds[kind] += 1
        cp_updates.append((r["id"], tgt_status, tgt_url))
        if url_diff and db_url and tgt_url:
            pages_updates.append((domain, db_url, tgt_url))
        mismatches.append(_mm(r, kind, wp["status"], tgt_url, tgt_status, tgt_url, wp["status"]))
    return cp_updates, pages_updates, mismatches, dict(kinds)


def _mm(r, kind, wp_status, wp_link, to_status, to_url, observed):
    return {"pub_id": r["id"], "publication_type": r["publication_type"],
            "platform_post_id": r["platform_post_id"], "db_status": r["status"],
            "db_url": r["platform_url"], "wp_status": wp_status, "wp_link": wp_link,
            "observed": observed, "kind": kind,
            "reconcile_to": {"status": to_status, "platform_url": to_url}}


def q(s):
    return "'" + s.replace("'", "''") + "'" if s is not None else "NULL"


def render_sql(domain, cp_updates, pages_updates, ts):
    L = [f"-- reconcile {domain} vs live WordPress   generated {ts}",
         f"-- content_publications rows: {len(cp_updates)}   pages.url rewrites: {len(pages_updates)}",
         "-- keyed by content_publications.id (PK); pages by (domain_id, url).", "", "BEGIN;", ""]
    if cp_updates:
        L += ["-- content_publications: status + platform_url -> live WP reality",
              "UPDATE content_publications AS cp SET",
              "    status          = v.status,",
              "    platform_url    = COALESCE(v.platform_url, cp.platform_url),",
              "    last_updated_at = now()",
              "FROM (VALUES"]
        rows = [f"    ({q(pid)}::uuid, {q(st)}::text, {q(url)}::text)" for pid, st, url in cp_updates]
        L.append(",\n".join(rows))
        L += [") AS v(id, status, platform_url)", "WHERE cp.id = v.id;", ""]
    if pages_updates:
        L += ["-- pages.url: stale draft url -> live permalink (same domain)",
              "UPDATE pages AS p SET url = v.new_url, updated_at = now()", "FROM (VALUES"]
        rows = [f"    ({q(dom)}::text, {q(old)}::text, {q(new)}::text)" for dom, old, new in pages_updates]
        L.append(",\n".join(rows))
        L += [") AS v(domain, old_url, new_url)",
              "JOIN domains d ON lower(d.domain) = lower(v.domain)",
              "WHERE p.domain_id = d.id AND p.url = v.old_url;", ""]
    L += ["COMMIT;", ""]
    return "\n".join(L)


# --------------------------------------------------------------------------
def run(args):
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if args.apply and not args.confirm:
        sys.exit("refuse: --apply requires --confirm (this writes to prod).")

    cfg = load_db_settings(args.env)

    # WP credentials: explicit flag/env wins; fill any gap from platform_connections.
    app_pw = args.app_password or os.environ.get("WP_APP_PASSWORD")
    given = (bool(args.site_url), bool(args.user), bool(app_pw))
    if not all(given):
        c_url, c_user, c_pw = resolve_connection(cfg, args.env, args.domain, args.db)
        args.site_url = args.site_url or c_url
        args.user = args.user or c_user
        app_pw = app_pw or c_pw

        def _src(was_given, value, flag_label="flag"):
            return flag_label if was_given else ("db" if value else "unresolved")

        # print the resolved site_url: a stored connection can legitimately point at
        # a different host than the domain name (shared/subdomain WP installs).
        print(f"[creds] site_url={_src(given[0], args.site_url)} ({args.site_url}) "
              f"user={_src(given[1], args.user)} "
              f"password={_src(given[2], app_pw, 'flag/env')}", flush=True)
    unresolved = [n for n, v in (("--site-url", args.site_url), ("--user", args.user),
                                 ("--app-password", app_pw)) if not v]
    if unresolved:
        sys.exit("error: could not resolve " + ", ".join(unresolved) +
                 " (not passed and not stored on the domain's wordpress platform_connection).")

    proxies = load_proxies(args)
    types = [t.strip() for t in args.post_types.split(",") if t.strip()]

    # 1. DB rows for this domain
    sql = (f"SELECT cp.id, cp.publication_type, cp.status, cp.platform_post_id, cp.platform_url "
           f"FROM content_publications cp "
           f"JOIN domain_content dc ON dc.id = cp.domain_content_id "
           f"JOIN domains d ON d.id = dc.domain_id "
           f"WHERE cp.platform = 'wordpress' AND lower(d.domain) = lower({q(args.domain)}) "
           f"AND cp.platform_post_id IS NOT NULL")
    rows = db_read(cfg, sql, args.db)
    rows = [r for r in rows if r["publication_type"] in types]
    print(f"[{args.domain}] DB rows to check: {len(rows)}  (env={args.env}, proxies={len(proxies)})", flush=True)
    if not rows:
        sys.exit("nothing to do: no wordpress publications with a platform_post_id for this domain.")

    # 2. WP: authenticate + sweep each post type
    wp = WPClient(args.site_url, args.user, app_pw, proxies, timeout=args.timeout)
    print("[wp] " + wp.validate_auth(), flush=True)
    wp_maps, route_available = {}, {}
    unreadable = []
    for typ in types:
        route = ROUTE[typ]
        avail, certain = wp.route_available(route)
        route_available[typ] = avail
        complete = True
        if avail:
            wp_maps[typ], complete = wp.fetch_all(route)
        else:
            wp_maps[typ] = {}
        state = "ok" if avail else ("MISSING" if certain else "UNREADABLE")
        if not certain or not complete:
            state = "UNREADABLE"
            unreadable.append(f"/{route}")
        print(f"[wp] {typ} route /{route}: {state} live_ids={len(wp_maps[typ])}", flush=True)

    # A read we could not complete is NOT evidence the posts are gone. Without this
    # guard a WAF-blocked sweep silently plans "trash every row on the domain".
    if unreadable:
        sys.exit(
            "refuse: could not read " + ", ".join(unreadable) + " completely "
            f"(WAF/proxy blocked on all egresses -- last: {getattr(wp, '_last_failure', None)}).\n"
            "        A failed read is indistinguishable from 'post deleted', so no plan was written.\n"
            "        Retry later, or add a working egress via --proxy-url / CRAWLER_PROXY_URLS."
        )

    # A row missing from the sweep is re-checked one-by-one before we call it deleted.
    missing_unread, rescued = confirm_missing(wp, rows, wp_maps, route_available)
    if rescued:
        print(f"[wp] sweep missed {rescued} live post(s); rescued via direct GET", flush=True)
    if missing_unread:
        sys.exit(
            "refuse: could not confirm " + ", ".join(missing_unread[:10])
            + (f" (+{len(missing_unread)-10} more)" if len(missing_unread) > 10 else "")
            + "\n        Unconfirmed absence is not a delete signal; no plan was written."
        )

    # 3. diff -> plan
    cp_updates, pages_updates, mismatches, kinds = build_plan(rows, wp_maps, route_available, args.domain)
    print(f"[plan] mismatches={len(mismatches)} {kinds}", flush=True)

    # 4. write report + SQL
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    report = {"domain": args.domain, "site_url": args.site_url, "generated_at": ts,
              "wp_egress": wp._egress_label(), "route_available": route_available,
              "rows_checked": len(rows), "counts_by_kind": kinds,
              "mismatches": mismatches}
    (out / f"{args.domain}.json").write_text(json.dumps(report, indent=2))
    sql_text = render_sql(args.domain, cp_updates, pages_updates, ts)
    sql_path = out / f"reconcile_{args.domain}.sql"
    sql_path.write_text(sql_text)
    print(f"[out] {out / (args.domain + '.json')}\n[out] {sql_path}")

    # 5. apply (gated)
    if not args.apply:
        print("\nDRY-RUN: no prod writes. Re-run with --apply --confirm to execute, "
              "or run the SQL above yourself.")
        return
    if not cp_updates and not pages_updates:
        print("\n[apply] nothing to update.")
        return
    print("\n[apply] executing against prod (one transaction)...")
    print(db_apply(cfg, sql_text, args.db).strip())


# --------------------------------------------------------------------------
def selftest():
    """No network/DB. Proves the diff + SQL logic."""
    rows = [
        {"id": "11111111-1111-1111-1111-111111111111", "publication_type": "content",
         "status": "draft", "platform_post_id": "7951", "platform_url": "https://x.com/?p=7951"},
        {"id": "22222222-2222-2222-2222-222222222222", "publication_type": "content",
         "status": "publish", "platform_post_id": "6774", "platform_url": "https://x.com/gone/"},
        {"id": "33333333-3333-3333-3333-333333333333", "publication_type": "faq",
         "status": "draft", "platform_post_id": "50", "platform_url": "https://x.com/?p=50"},
    ]
    wp_maps = {"content": {"7951": {"status": "publish", "link": "https://x.com/live-post/"}},
               "faq": {"50": {"status": "draft", "link": "https://x.com/?p=50"}}}
    cp, pages, mm, kinds = build_plan(rows, wp_maps, {"content": True, "faq": True}, "x.com")
    # 7951 draft->publish + url change ; 6774 publish->trashed (deleted) ; 50 draft==draft no-op
    assert len(cp) == 2, cp
    assert ("11111111-1111-1111-1111-111111111111", "publish", "https://x.com/live-post/") in cp
    assert ("22222222-2222-2222-2222-222222222222", "trashed", None) in cp
    assert pages == [("x.com", "https://x.com/?p=7951", "https://x.com/live-post/")], pages
    sql = render_sql("x.com", cp, pages, "T")
    assert "BEGIN;" in sql and "COMMIT;" in sql
    assert "UPDATE content_publications" in sql and "UPDATE pages" in sql
    assert norm_url("https://www.x.com/a/") == norm_url("http://x.com/a")  # www/scheme/slash ignored

    try:
        from cryptography.fernet import Fernet
    except ImportError:
        print("selftest: skipping creds round-trip (no cryptography outside the venv)")
    else:
        k = Fernet.generate_key()
        blob = json.dumps({"username": "wp_admin", "application_password": "a b c"})
        assert parse_creds(Fernet(k).encrypt(blob.encode()).decode(), k.decode()) == ("wp_admin", "a b c")
    print("selftest OK:", kinds)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--domain", help="CADE domain (domains.domain), e.g. theposbrokers.com")
    ap.add_argument("--site-url", help="WordPress base URL, e.g. https://theposbrokers.com")
    ap.add_argument("--user", help="WordPress username for the application password")
    ap.add_argument("--app-password", help="WP application password (prefer WP_APP_PASSWORD env)")
    ap.add_argument("--env", choices=("prod", "stg", "local"), default="prod",
                    help="DB creds source: .claude/skills.settings.{env}.json (default prod)")
    ap.add_argument("--post-types", default="content,faq", help="which types to check (default content,faq)")
    ap.add_argument("--proxy-url", help="override proxy (default: CRAWLER_PROXY_URLS from .env)")
    ap.add_argument("--no-proxy", action="store_true", help="disable proxy fallback (direct only)")
    ap.add_argument("--timeout", type=int, default=20, help="per-request timeout seconds (default 20)")
    ap.add_argument("--db", help="override DB name")
    ap.add_argument("--out", default="misc/mismatches", help="output dir (default misc/mismatches)")
    ap.add_argument("--apply", action="store_true", help="execute the UPDATEs (requires --confirm)")
    ap.add_argument("--confirm", action="store_true", help="acknowledge the prod write")
    ap.add_argument("--selftest", action="store_true", help="run the offline logic check and exit")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if not args.domain:
        ap.error("missing required: --domain")
    run(args)


if __name__ == "__main__":
    main()
