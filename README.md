# nixodoo-copier-template

[Copier](https://copier.readthedocs.io) template that generates a complete,
reproducible **Odoo development environment on Nix flakes**: native processes,
systemd user services, a project-local PostgreSQL cluster, and a python env
locked with uv/uv2nix.

## What you get

- **Odoo 16.0 / 17.0 / 18.0 / 19.0**, **Python 3.10–3.13**, **PostgreSQL 13–17** — picked at generation time
- Nix flake with three installable toolchains (`dev-server`, `test-server`, `prod-server`):
  wrapped `odoo`, `psql`/`pg_dump`/... bound to the project cluster, `ruff`, `uv`, `ccze`
- Pinnable source management: `repos.yaml` (odoo) + `addons.yaml` (OCA/custom addon repos,
  branch- or commit-pinned) with an addons symlink farm builder
- Generated configs: `.env`, `odoo.conf` (random master password), nginx reverse proxy,
  systemd user units incl. daily log rotation
- Python deps imported straight from Odoo's own `requirements.txt` and locked with `uv`
- **Claude Code integration** (optional): `CLAUDE.md`, guard hooks (read-only OCA/core,
  dangerous-command blocker, ruff auto-format), Odoo dev skills (code patterns, style,
  testing, commit conventions, pre-PR checklist, model inspector, pdb debugging,
  isolated worktree envs), `dev`/`review` agents, MCP servers config
- Optional (asked during generation): custom addons repo wiring, S3 production-backup
  restore with **native Odoo neutralization** (`odoo neutralize` + dev fixups),
  SSH helpers for prod/test servers, OCA `queue_job` wiring
- `nix flake check` shellchecks every project script

## Requirements

- [Nix](https://nixos.org/download) with flakes enabled
- [copier](https://copier.readthedocs.io) (easiest: `uvx copier`)
- systemd user session (Linux)

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
| `default_repo_pattern` | `https://github.com/OCA/{}.git` |
| `custom_repo_pattern` / `custom_repo_name` | empty → no custom addons repo |
| `use_claude_code` | `true` — CLAUDE.md, hooks, skills, agents, MCP config |
| `module_prefix` / `ticket_prefix` | first word of project name / `TASK` |
| `backup_s3_bucket` | empty → no backup tooling |
| `prod_ssh_host` (+user/url), `test_ssh_host` (+user/port/forward/url) | empty → no SSH helpers |

## License

MIT
