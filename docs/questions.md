# Every question copier asks

Defaults in the table; anything you leave empty turns its feature off. See [the README](../README.md#the-choices-that-matter) for the ones worth thinking about.

| Question | Default |
|---|---|
| `project_name` | `odoo-dev-env` |
| `odoo_version` | `19.0` (16.0–19.0) |
| `python_version` | per Odoo version (16→3.10, 17→3.11, 18/19→3.12) |
| `postgres_version` | 15 for Odoo ≤17, 17 for 18+ |
| ports (http / gevent / nginx / postgres) | derived from the Odoo major (`16.0` → 1669/1672/16069/16432) |
| `db_name` / `db_user` / `db_password` | `develop` / `odoo` / `odoo` |
| `project_dir_var` (project-root env var; make unique to run two projects on one Odoo version) | `ODOO<major>_PROJECT_DIR` |
| `service_suffix` (systemd unit + nix profile suffix; set e.g. `-19` so a second generated project can coexist on one machine — `odoo-19.service`, own profile `~/.local/state/nix/profiles/<project>`) | empty = classic names + `~/.nix-profile` |
| `use_queue_job` | `false` |
| `editor` | `none` (or `vscode` → settings generator; `zed` → debug config; `odools.toml` ships with `use_claude_code` or `zed`) |
| `default_repo_pattern` | `https://github.com/OCA/{}.git` |
| `custom_repo_pattern` / `custom_repo_name` | empty → no custom addons repo |
| `use_claude_code` | `true` — CLAUDE.md, hooks, skills, agents, MCP config |
| `module_prefix` / `ticket_prefix` | first word of project name / `TASK` |
| `status_mcp` | `none` (or `teams` → my-status + teams-message skills + bootstrap questions; Teams write goes through the claude.ai Microsoft 365 connector, nothing installed) |
| `tickets_mcp` | `none` (or `odoo` → odoo-tickets skill, ticket links, `ODOO_*_PROD` in `.env`) |
| `odoo_prod_url` | asked when `tickets_mcp=odoo` |
| `use_pipeline` | `true` (asked when a custom addons repo is set) |
| `use_kb` | `false` — SessionStart hook: `kb index` + `kb today` into every session (needs `kb` from okolovmark/kb; silent when absent) |
| `backup_s3_bucket` | empty → no backup tooling |
| `prod_ssh_host` (+user/url), `test_ssh_host` (+user/port/forward/url) | empty → no SSH helpers |
| `prod_remote_project_dir` / `prod_remote_odoo_conf` / `prod_db_name` / `prod_link_addons_cmd` (where this same project sits on the box, for the deploy and prod-ops skills) | `~/<project_name>` / `<that dir>/odoo.conf` / `odoo` / `nix run .#update-repos` |
