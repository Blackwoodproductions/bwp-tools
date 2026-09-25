# bwp-tools

Blackwood Productions' Claude plugins. This private repo **is** the marketplace (`.claude-plugin/marketplace.json`).

| Plugin | Gives you | Install it if you work on |
|---|---|---|
| `cade` | `cade-db-queries`, `cade-api`, `cade-flower`, `cade-terms`, `check-for-seomoney-content`, `dump-clustered-content`, `cleanup-html-blocks`, `reconcile-wp-content` + agent `cade-task-failure-auditor` | cade-service |
| `seolocal` | `seolocal-db-queries` | seolocal-app |
| `ranking` | `ranking-db-queries` | ranking-service |
| `bwp-rw` | `execute_sql_{cade,seo,ranking}_rw`: **UPDATE** access to prod (SELECT + UPDATE only, never DELETE) | only if you need to fix row state |
| `bwp-core` | The shared connectors: **DBHub** (read-only SQL over prod) and **Logfire** | installed automatically with the three above |

Skills are namespaced by plugin: `/cade:cade-api`, `/seolocal:seolocal-db-queries`, …

## 1. Install (Claude Code)

You need read access to this GitHub repo.

```bash
claude plugin marketplace add Blackwoodproductions/bwp-tools

# from inside each repo you work on
cd cade-service     && claude plugin install cade@bwp-tools     --scope local
cd seolocal-app     && claude plugin install seolocal@bwp-tools --scope local
cd ranking-service  && claude plugin install ranking@bwp-tools  --scope local
```

`--scope local` enables the plugin for that repo only (written to its gitignored `.claude/settings.local.json`). Start Claude in the repo, or run `/reload-plugins` in an open session. Sign in to Logfire when prompted.

That's it for the `*-db-queries` skills and Logfire: the read-only DB token ships inside `bwp-core`.

## 2. Extra setup for the cade script skills

`cade-api`, `cade-flower`, `cade-terms`, `check-for-seomoney-content`, `dump-clustered-content`, `cleanup-html-blocks` and `reconcile-wp-content` run Python scripts. For those:

- Run Claude from the **cade-service repo root**, with its `venv/` set up (the scripts use `venv/bin/python`) and a valid `.env`.
- Create `.claude/skills.settings.prod.json` in cade-service (gitignored). Add `…stg.json` / `…local.json` only if you target those envs.

| Block | Keys | Used by |
|---|---|---|
| `cade-api` | `api-key` · `api-url` *(optional, bare host)* · `wp-plugin-api-key` *(optional)* | cade-api |
| `cade-flower` | `url` · `user` · `password` | cade-flower |
| `cade-db-queries` | `host` · `port` · `user` · `pass` · `db-default` | dump-clustered-content, check-for-seomoney-content, reconcile-wp-content |
| `cade-terms-merge` | `database_url` *(write-capable role)* · `credential_encryption_key` · `crawler_proxy_urls` *(optional)* | cade-terms (reconcile-wp-content reads its encryption key too) |

```json
{
  "cade-api":         { "api-key": "…" },
  "cade-flower":      { "url": "…", "user": "…", "password": "…" },
  "cade-db-queries":  { "host": "…", "port": 5432, "user": "…", "pass": "…", "db-default": "seo-acg" },
  "cade-terms-merge": { "database_url": "postgresql://…", "credential_encryption_key": "…" }
}
```

Fill in only the blocks for the skills you use. Ask Fernando for the values.

- **Environment:** scripts default to prod. Use `--env stg|local` per call, or `export CADE_SKILL_ENV=stg` for a session. A missing settings file fails loudly and never falls back to prod.
- **Direct-DB scripts** (dump, seomoney check, reconcile, cade-terms) connect straight to prod Postgres, so your IP must be allowlisted. `reconcile-wp-content` also needs `psql`.

## 3. UPDATE access (`bwp-rw`, optional)

Ask Fernando for your personal update token, then:

```bash
claude plugin install bwp-rw@bwp-tools   # user scope: available in every repo; prompts for the token
```

To change or clear the token: `/plugin` → **Installed** → `bwp-rw` → **Configure options**.

## 4. Claude Desktop / Cowork

Customize → Plugins → **Add marketplace** → `Blackwoodproductions/bwp-tools` → install `cade`, `seolocal` or `ranking`. You need read access to this repo and a paid plan.

| | Claude Code / Desktop *Code* tab | Desktop chat / Cowork |
|---|---|---|
| `*-db-queries` skills (read) + Logfire | ✅ | ✅ |
| `cade-task-failure-auditor` agent | ✅ | Cowork only |
| `bwp-rw` (UPDATE) | ✅ | ✗ (can't hold a personal token) |
| cade script skills (§2) | ✅ | ✗ |

## 5. Updating

```
/plugin marketplace update bwp-tools
/reload-plugins
```

Desktop: click **Update** on the marketplace.

---

## Maintainers

**How DB access works**

```
you ── HTTPS ──▶ dbhub.imagehosting.space   (caddy-edge, seo-money-deployments)
   bwp-core   /mcp     ─▶ dbhub     readonly tools, DB user claude_readonly (SELECT)
   bwp-rw     /rw/mcp  ─▶ dbhub-rw  execute_sql_*_rw, DB user claude_rw (SELECT, UPDATE)
```

- Read tools: `execute_sql_*`, `search_objects_*`, `explain_sql_*` (readonly, 30 s timeout). Update tools: `execute_sql_*_rw`. The `claude_rw` role cannot INSERT, DELETE, TRUNCATE or run DDL.
- **Read token:** GitHub secret `DBHUB_RO_TOKEN`, rendered by CI into `plugins/bwp-core/.mcp.json`, so repo read access = prod read access. **Update tokens:** one per person, in the deployments secret `DBHUB_RW_AUTH_TOKEN` (comma-separated), never in this repo.
- Server side (DNS, DB users, secrets, deploy): `seo-money-deployments/docs/setup-dbhub-tutorial.md`.

**Change and release**

```bash
claude --plugin-dir plugins/bwp-core --plugin-dir plugins/cade   # live dev; /reload-plugins in-session
claude plugin validate plugins/cade                              # each plugin, and `.`
```

- Release: bump `version` in the plugin's `plugin.json`, merge to `main`. Users then update (§5).
- Never hand-edit `plugins/bwp-core/.mcp.json`. Edit `.mcp.json.tpl` and the **Render bwp-core .mcp.json** workflow commits the result.
- Rotate the read token: set the new value in `DBHUB_RO_TOKEN` (here) **and** `DBHUB_AUTH_TOKEN` (deployments env `dbhub-production`), run **Deploy DBHub**, run **Render bwp-core .mcp.json**, then users update.
- Revoke someone's update token: remove it from `DBHUB_RW_AUTH_TOKEN`, run **Deploy DBHub**.
