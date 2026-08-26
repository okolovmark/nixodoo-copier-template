# nixodoo-copier-template

[Copier](https://copier.readthedocs.io) template that generates a complete,
reproducible **Odoo development environment on Nix flakes**: native processes,
systemd user services, a project-local PostgreSQL cluster, and a python env
locked with uv/uv2nix.

## What you get

- **Odoo 16.0 / 17.0 / 18.0 / 19.0**, **Python 3.10–3.13**, **PostgreSQL 13–17** — picked at
  generation time; every Odoo version runtime-verified end-to-end (clone → lock → build → `-i base`)
- Nix flake with three installable toolchains (`dev-server`, `test-server`, `prod-server`):
  wrapped `odoo`, `psql`/`pg_dump`/... bound to the project cluster, `ruff`, `uv`, `ccze`;
  `setup-dev` leaves a `./.venv` over the base interpreter for editors that build their
  own debug adapter (Zed), with a `dev-python` symlink pinning the debuggee to the project env
- Pinnable source management: `repos.yaml` (odoo) + `addons.yaml` (OCA/custom addon repos,
  branch- or commit-pinned) with an addons symlink farm builder
- Generated configs: `.env`, `odoo.conf` (random master password), nginx reverse proxy,
  systemd user units incl. daily log rotation
- Python deps imported straight from Odoo's own `requirements.txt` and locked with `uv`
- **Claude Code integration** (optional): `CLAUDE.md`, guard hooks (read-only OCA/core,
  dangerous-command blocker, ruff auto-format), Odoo dev skills (code patterns, style,
  testing, commit conventions, pre-PR checklist, **semantic code navigation** over the
  official Odoo Language Server (`super()` chains across `_inherit`, XML `ref`/model
  targets, find-usages, model inheritance maps), pdb debugging,
  isolated worktree envs, a **grill** requirements interview — one question at a
  time, decisions routed to their owner, explicit assumptions — and
  **domain-modeling**: a `CONTEXT.md` glossary of canonical terms mapped to Odoo
  models + `docs/adr/` decision records, both maintained as design conversations
  resolve), `dev`/`review`/`documenter` agents, MCP servers config, a persistent
  **memory template** (journal / state / standup), and optional workflow skills:
  the **pipeline** orchestrator (task → dev → QC gate → docs → demo GIF → PR), **my-status**
  (status posting, Teams), **teams-message** (readable Teams messages: markdown Teams
  actually renders, recipient verified by email, draft before send),
  **odoo-tickets** (ticket tracking in a prod Odoo),
  **deploy-checks** (blast-radius classification of a change set + read-only
  post-deploy invariant check: unbalanced postings, failed queue jobs, stuck
  crons, negative stock), **prod-ops** (working with the prod box outside a
  deploy: read-channel choice — RPC vs local prod-dump DB vs live odoo shell —
  prod forensics, and a named-script runner with a dry → commit → read-back
  ladder), **deploy** (the prod runbook itself: resolve the merged
  PR, derive the `-u` list from what the pull actually lands, approval gate, one
  named write script per prod step, T+0/T+60 invariant checks — with the
  announcement and ticket-note beats gated on your `status_mcp`/`tickets_mcp`
  answers) and **estimate** (effort estimates priced from a calibration table of
  your own closed tickets, not from gut feel)
- Optional (asked during generation): custom addons repo wiring, S3 production-backup
  restore with **native Odoo neutralization** (`odoo neutralize` + dev fixups),
  SSH helpers for prod/test servers, OCA `queue_job` wiring
- `nix flake check` shellchecks every project script

## Requirements

- [Nix](https://nixos.org/download) with flakes enabled
- [uv](https://docs.astral.sh/uv/) (`uvx` runs copier without installing it)
- systemd user session (Linux)

### Install uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Install Nix

```bash
sh <(curl --proto '=https' --tlsv1.2 -L https://nixos.org/nix/install) --daemon
```

Enable flakes — add to `~/.config/nix/nix.conf` (create the file if it doesn't exist):

```ini
experimental-features = nix-command flakes
```

Then restart the daemon:

```bash
sudo systemctl restart nix-daemon
```

## Usage

```bash
uvx copier copy --trust gh:okolovmark/nixodoo-copier-template my-odoo-project
cd my-odoo-project

nix run .#create-env          # .env (ports, DB creds)
nix run .#update-repos        # clone odoo + addon repos, build the farm
nix run .#bootstrap-deps      # import odoo requirements -> uv.lock
nix profile add .#dev-server  # odoo/psql/ruff/... into your profile
nix run .#setup-dev           # local postgres, odoo.conf, nginx, systemd units
systemctl --user enable --now postgres.service odoo.service nginx.service odoo-logrotate.timer
```

The same generated project also deploys as a **test** or **production** server:
`nix run .#setup-test` / `nix run .#setup-prod` + `nix profile add .#test-server` /
`.#prod-server` (leaner toolchain, odoo + nginx units, external PostgreSQL).
The generated `README.md` documents all three flows step by step.

Later, to pull template improvements into a generated project:

```bash
uvx copier update --trust
```

## Questions asked

| Question | Default |
|---|---|
| `project_name` | `odoo-dev-env` |
| `odoo_version` | `19.0` (16.0–19.0) |
| `python_version` | per Odoo version (16→3.10, 17→3.11, 18/19→3.12) |
| `postgres_version` | 15 for Odoo ≤17, 17 for 18+ |
| ports (http / gevent / nginx / postgres) | derived from the Odoo major (`16.0` → 1669/1672/16069/16432) |
| `db_name` / `db_user` / `db_password` | `develop` / `odoo` / `odoo` |
| `project_dir_var` (project-root env var; make unique to run two projects on one Odoo version) | `ODOO<major>_PROJECT_DIR` |
| `use_queue_job` | `false` |
| `editor` | `none` (or `vscode` → settings generator; `zed` → debug config; `odools.toml` ships with `use_claude_code` or `zed`) |
| `default_repo_pattern` | `https://github.com/OCA/{}.git` |
| `custom_repo_pattern` / `custom_repo_name` | empty → no custom addons repo |
| `use_claude_code` | `true` — CLAUDE.md, hooks, skills, agents, MCP config |
| `module_prefix` / `ticket_prefix` | first word of project name / `TASK` |
| `status_mcp` | `none` (or `teams` → my-status + teams-message skills, Teams MCP + bootstrap questions) |
| `tickets_mcp` | `none` (or `odoo` → odoo-tickets skill, ticket links, `ODOO_*_PROD` in `.env`) |
| `odoo_prod_url` | asked when `tickets_mcp=odoo` |
| `use_pipeline` | `true` (asked when a custom addons repo is set) |
| `backup_s3_bucket` | empty → no backup tooling |
| `prod_ssh_host` (+user/url), `test_ssh_host` (+user/port/forward/url) | empty → no SSH helpers |
| `prod_remote_project_dir` / `prod_remote_odoo_conf` / `prod_db_name` / `prod_link_addons_cmd` (where this same project sits on the box, for the deploy and prod-ops skills) | `~/<project_name>` / `<that dir>/odoo.conf` / `odoo` / `nix run .#update-repos` |

## Notes

- `.mcp.json` points the `odoo` and `teams` MCP servers at
  [okolovmark's](https://github.com/okolovmark) forks — swap the URLs for your
  own if you prefer.
- The `find-code` skill ships `lsp.py`, a daemon/CLI over
  [odoo-ls](https://github.com/odoo/odoo-ls) reading the generated `odools.toml`. Install
  the server once with `python3 .claude/skills/find-code/lsp.py bump <version>` (use
  1.5.1 or newer — earlier find-usages misses `with_company()` chains and XML `<field>`
  usages); it lands in `~/.local/share/odoo-ls/<version>` behind a `current` symlink, so
  an editor pointed at that symlink runs the same server as the CLI.
- `nudge-find-code.py` (PreToolUse) bounces a recursive grep whose pattern is an
  identifier and points at `lsp.py` instead; re-running the same grep passes. Budget:
  three bounces per session, one per distinct pattern; `FINDCODE_NUDGE=0` disables.
  Regression corpus: `python3 tests/test_nudge_find_code.py`.
- `odools.toml` and `.claude/skills/estimate/references/calibration.md` are
  generated once (`_skip_if_exists`): the first because `addons_paths` is
  hand-tuned per project, the second because it holds measured actuals. Template
  updates never overwrite either. The estimate anchors ship as a **prior** from
  the project this skill came from — replace them with your own spread once the
  table has rows.
- The `deploy` skill writes to production, so its write steps are named scripts
  under `skills/deploy/scripts/` with validated arguments, never inline remote
  commands: that is one permission decision instead of a fresh judgement call
  every deploy. Nothing there runs before the skill's approval gate, and Claude
  cannot grant itself the permission entry — it composes it and you paste it.
- `postgres-mcp` in `.mcp.json` runs with `--access-mode=unrestricted` — it
  targets the **local dev database** only (`DATABASE_URI` from `.env`).
- `postgres-mcp` and `mcp-pdb` run with `--with 'mcp<2'`: both import
  `mcp.server.fastmcp`, which the `mcp` SDK dropped in 2.0.0, and neither caps
  its own dependency. The `odoo` server needs no pin — it goes through the
  `fastmcp` package, which caps `mcp` itself.

## License

MIT
