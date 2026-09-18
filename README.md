# nixodoo-copier-template

A [Copier](https://copier.readthedocs.io) template that generates a complete Odoo development
environment on Nix flakes: native processes, systemd user services, a project-local PostgreSQL
cluster, and a Python environment locked with uv.

No container, no global install, no `pip` in sight. The same generated project also runs as a test
or production server.

## Quickstart

```bash
uvx copier copy --trust gh:okolovmark/nixodoo-copier-template my-odoo-project
cd my-odoo-project

nix run .#create-env          # .env: ports, database credentials
nix run .#update-repos        # clone Odoo and the addon repos, build the symlink farm
nix run .#bootstrap-deps      # import Odoo's requirements.txt into uv.lock
nix profile add .#dev-server  # odoo, psql, ruff and friends into your profile
nix run .#setup-dev           # postgres cluster, odoo.conf, nginx, systemd units
systemctl --user enable --now postgres.service odoo.service nginx.service odoo-logrotate.timer
```

Odoo is then on the port the template derived from your version, with its own PostgreSQL, its own
addons farm and a log rotating daily. The generated `README.md` walks through the test and production
flows the same way.

You need [Nix](https://nixos.org/download) with flakes enabled, [uv](https://docs.astral.sh/uv/)
(`uvx` runs copier without installing it), and a systemd user session. Starting from nothing:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
sh <(curl --proto '=https' --tlsv1.2 -L https://nixos.org/nix/install) --daemon
echo 'experimental-features = nix-command flakes' >> ~/.config/nix/nix.conf
sudo systemctl restart nix-daemon
```

## What it generates

- **Odoo 16.0 to 19.0** on Python 3.10 to 3.14 and PostgreSQL 13 to 17, chosen at generation time
  and verified end to end for every version.
- **Three toolchains** as flake packages: `dev-server`, `test-server`, `prod-server`, each wrapping
  `odoo` and the Postgres client tools against this project's cluster.
- **Pinned sources**: `repos.yaml` for Odoo, `addons.yaml` for OCA and custom addon repos, pinned by
  branch or commit, with a symlink farm builder.
- **Generated configuration**: `.env`, `odoo.conf` with a random master password, systemd user
  units, a log-rotation timer, and an nginx reverse proxy for dev and test - production is left to
  whatever already fronts it.
- **Python dependencies straight from Odoo's own `requirements.txt`**, locked with uv and built with
  uv2nix, so the flake and your shell agree.
- **Claude Code integration**, optional: `CLAUDE.md`, guard hooks, Odoo skills including semantic
  code navigation over the official language server, a requirements interview, a development
  pipeline, deploy and production-operations runbooks, and a persistent memory.
- **Optional extras**, each behind a question: a custom addons repo, S3 backup restore with native
  neutralization, SSH helpers for the prod and test boxes, OCA `queue_job`.

The full inventory, including every skill and agent, is in [docs/features.md](docs/features.md).

## The choices that matter

Copier asks about twenty questions; most have a sensible default derived from your Odoo version.
These are the ones worth a thought:

| Question | Default | Why you might change it |
| --- | --- | --- |
| `odoo_version` | `19.0` | everything else follows from it |
| `custom_repo_pattern` / `custom_repo_name` | empty | your own addons repo; unlocks the development pipeline |
| `use_claude_code` | `true` | the whole `.claude/` layer |
| `use_kb` | `false` | memory in [kb](https://github.com/okolovmark/kb) instead of markdown files |
| `service_suffix` | empty | set it to run two generated projects on one machine |
| `project_dir_var` | `ODOO<major>_PROJECT_DIR` | same reason, for the project-root variable |
| `status_mcp` / `tickets_mcp` | `none` | wire status posting and ticket tracking to Teams and Odoo |
| `backup_s3_bucket` | empty | restore production dumps locally |
| `prod_ssh_host` / `test_ssh_host` | empty | the deploy and prod-ops runbooks |

Every question and its default: [docs/questions.md](docs/questions.md).

## Keeping a project current

```bash
uvx copier update --trust
```

Template improvements land in the generated project. A few files are yours and are never overwritten
(`odools.toml`, the estimate calibration table); for the rest, resolve conflicts in favour of the
project where you have edited it. Hook arrays in `.claude/settings.json` deserve a second look after
an update: copier can add a second key with the same name, and JSON keeps only the last one.

## More

- [docs/features.md](docs/features.md) — everything a generated project contains
- [docs/questions.md](docs/questions.md) — every question copier asks
- [docs/notes.md](docs/notes.md) — gotchas worth knowing before they bite
- `nix flake check` shellchecks every generated script; `python3 tests/test_nudge_find_code.py`
  runs the hook regression corpus

## License

MIT
