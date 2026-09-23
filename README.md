# bwp-tools

Blackwood Productions' Claude plugins. The repo **is** the marketplace (`.claude-plugin/marketplace.json`); it works in **Claude Code, Claude Desktop and Cowork**.

| Plugin | Gives you | Who installs it |
|---|---|---|
| `bwp-core` | MCP connectors: **DBHub** (hosted, read-only SQL over CADE Postgres + `bwp_seo` / `bwp_ranking_service` MariaDB) and **Logfire** | pulled in automatically by the three below |
| `seolocal` | `seolocal-db-queries` | seolocal-app people |
| `cade` | `cade-db-queries`, `cade-api`, `cade-flower`, `check-for-seomoney-content`, `dump-clustered-content`, `cleanup-html-blocks`, `reconcile-wp-content`, `cade-terms` + agent `cade-task-failure-auditor` | cade-service people |
| `ranking` | `ranking-db-queries` | ranking-service people |
| `bwp-rw` | `execute_sql_{cade,seo,ranking}_rw` — **UPDATE-capable** DB access (SELECT + UPDATE only, never DELETE). Claude Code only, personal token | only people who need to fix row state |

Skills are namespaced by plugin: `/cade:cade-db-queries`, `/seolocal:seolocal-db-queries`, `/ranking:ranking-db-queries`, `/cade:cade-terms`, …

## Install — Claude Code (terminal or the Desktop *Code* tab)

```bash
claude plugin marketplace add Blackwoodproductions/bwp-tools
cd ~/work/blackwood-productions/cade-workdir/cade-service          && claude plugin install cade@bwp-tools     --scope local
cd ~/work/blackwood-productions/seo-local-workdir/seolocal-app      && claude plugin install seolocal@bwp-tools --scope local
cd ~/work/blackwood-productions/ranking-app-workdir/ranking-service && claude plugin install ranking@bwp-tools  --scope local
# optional, for UPDATEs — prompts for your personal token
claude plugin install bwp-rw@bwp-tools
```

`--scope local` writes `enabledPlugins` into that repo's `.claude/settings.local.json` (gitignored). `bwp-core` is installed and enabled alongside each project plugin. Nothing to configure: the read connector's token ships inside the plugin.

## Install — Claude Desktop / Cowork

Customize → Plugins → **Add marketplace** → `Blackwoodproductions/bwp-tools` (you need read access to this private repo and a paid Claude plan) → install `cade`, `seolocal` or `ranking`. `bwp-core` comes with them; the `dbhub` and `logfire` connectors appear under the plugin — sign in to Logfire when prompted.

What works where:

| | Claude Code | Desktop *Code* tab | Desktop chat / Cowork |
|---|---|---|---|
| Skills + `*-db-queries` (read) | ✅ | ✅ | ✅ |
| `cade-task-failure-auditor` agent | ✅ | ✅ | Cowork only |
| Logfire | ✅ | ✅ | ✅ |
| `bwp-rw` (UPDATE) | ✅ | ✅ | ✗ (no way to hold a personal token) |
| Script-based cade skills (`cade-api`, `cade-flower`, `cleanup-html-blocks`, `reconcile-wp-content`, `cade-terms`, …) | ✅ from the cade-service checkout with `venv/bin/python` | ✅ same | ✗ |

## How DB access works

```
you ── HTTPS ──▶ dbhub.imagehosting.space   (caddy-edge, seo-money-deployments)
   bwp-core   /mcp     ─▶ dbhub     readonly tools, DB user claude_readonly (SELECT)
   bwp-rw     /rw/mcp  ─▶ dbhub-rw  execute_sql_*_rw, DB user claude_rw (SELECT, UPDATE)
```

- Read tools: `execute_sql_{cade,seo,ranking}` (readonly, 30 s timeout, no row cap), `search_objects_*`, `explain_sql_*`.
- Update tools: `execute_sql_{cade,seo,ranking}_rw`. The DB role cannot INSERT, DELETE, TRUNCATE or run DDL — the engine refuses. Skills require a SELECT first and a primary-key `WHERE`.
- The read token is a GitHub secret (`DBHUB_RO_TOKEN`) that CI renders into `plugins/bwp-core/.mcp.json`; repo read access = read access to the DBs. Update tokens are per person and never stored in this repo.
- Server side: `seo-money-deployments` → `docs/setup-dbhub-tutorial.md` (DNS, DB users, secrets, deploy, rotation).

## Maintain

```bash
claude plugin validate plugins/bwp-core   # and bwp-rw / seolocal / cade / ranking / .
claude --plugin-dir plugins/bwp-core --plugin-dir plugins/cade   # live-reload dev; /reload-plugins in-session
```

- Never edit `plugins/bwp-core/.mcp.json` by hand — edit `.mcp.json.tpl`; the **Render bwp-core .mcp.json** workflow commits the rendered file.
- Ship a change: bump `version` in the plugin's `plugin.json`, push; Claude Code users run `claude plugin update <plugin>@bwp-tools`, Desktop users click **Update** on the marketplace.
- Rotate the read token: new value in `DBHUB_RO_TOKEN` (here) **and** `DBHUB_AUTH_TOKEN` (deployments env `dbhub-production`) → run **Deploy DBHub** → run **Render bwp-core .mcp.json** → users update.

## Layout

```
.claude-plugin/marketplace.json
.github/workflows/render-mcp.yml
plugins/
  bwp-core/   .claude-plugin/plugin.json  .mcp.json.tpl  .mcp.json (CI-rendered)
  bwp-rw/     .claude-plugin/plugin.json  .mcp.json  (userConfig: dbhub_rw_token)
  seolocal/   .claude-plugin/plugin.json  skills/seolocal-db-queries/
  cade/       .claude-plugin/plugin.json  skills/*  agents/*  scripts/load_settings.py
  ranking/    .claude-plugin/plugin.json  skills/ranking-db-queries/
```
