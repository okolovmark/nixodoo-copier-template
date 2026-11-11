# Every question copier asks

Defaults in the table; anything you leave empty turns its feature off. See [the README](../README.md#the-choices-that-matter) for the ones worth thinking about.

| Question | Default |
|---|---|
| `project_name` | `odoo-dev-env` |
| `odoo_version` | `20.0` (16.0–20.0) |
| `python_version` | per Odoo version (16→3.10, 17→3.11, 18/19/20→3.12; 20 refuses < 3.12) |
| `postgres_version` | 15 for Odoo ≤17, 17 for 18+ (20 needs ≥ 16) |
| ports (http / gevent / nginx / postgres) | derived from the Odoo major (`16.0` → 1669/1672/16069/16432) |
| `db_name` / `db_user` / `db_password` | `develop` / `odoo` / `odoo` |
| `project_dir_var` (project-root env var; make unique to run two projects on one Odoo version) | `ODOO<major>_PROJECT_DIR` |
| `service_suffix` (systemd unit + nix profile suffix; set e.g. `-19` so a second generated project can coexist on one machine — `odoo-19.service`, own profile `~/.local/state/nix/profiles/<project>`) | empty = classic names + `~/.nix-profile` |
| `use_queue_job` | `false` |
| `queue_job_channels` (asked with `use_queue_job`: `[queue_job] channels` of every odoo.conf the project writes, e.g. `root:3,root.ai_help:2`) | `root:3` |
| `editor` | `none` (or `vscode` → settings generator; `odools.toml` ships with `use_claude_code`) |
| `default_repo_pattern` | `https://github.com/OCA/{}.git` |
| `custom_repo_pattern` / `custom_repo_name` | empty → no custom addons repo |
| `use_claude_code` | `true` — CLAUDE.md, hooks, skills, agents, MCP config |
| `module_prefix` / `ticket_prefix` | first word of project name / `TASK` |
| `status_mcp` | `none` (or `teams` → my-status + teams-message skills + bootstrap questions; Teams write goes through the claude.ai Microsoft 365 connector) |
| `tickets_mcp` | `none` (or `odoo` → odoo-tickets skill, ticket links, `ODOO_*_PROD` in `.env`) |
| `odoo_prod_url` | asked when `tickets_mcp=odoo` |
| `task_keys` (prod Odoo runs OCA project_key: every task has a `key` like PROJ-123; `task_key_route` is asked only then) | `false` → a task's reference is `<ticket_prefix>-<id>`, looked up by its record id |
| `task_software_fields` (prod Odoo runs kaertech_project_software: `project.task` fields `task_type`, `tshirt_estimate`, `priority_level`, `difficulty`, `knowledge_area_level`, `questions`, `answers`, `solution`, models `project.task.testcase` and `project.task.subtask`) | `false` → the ticket skills never read them; clarifying Q&A, the technical solution and test cases go to internal notes; no checklist |
| `task_timeline` (prod Odoo runs OCA project_timeline: `planned_date_start` / `planned_date_end`) | `false` → a task is scheduled by `date_deadline` alone |
| `task_pr_uri` (prod Odoo runs OCA project_task_pull_request: `pr_uri`, required before the done stage) | `false` → a task closes by its stage move; the PR link goes to a `Progress` note |
| `use_pipeline` | `true` (asked when a custom addons repo is set) |
| `extra_ticket_prefixes` (more ticket prefixes the pipeline takes as task keys, e.g. a second project's) | empty → `ticket_prefix` alone |
| `use_kb` | `false` — SessionStart hook: `kb index` + `kb today` into every session (needs `kb` from okolovmark/kb; silent when absent) |
| `backup_s3_bucket` | empty → no backup tooling |
| `prod_ssh_host` (+user/url; `prod_local_ssh_host` adds a `prod-local` alias for the office-LAN address), `test_ssh_hosts` (YAML mapping alias → host, +user/port/url pattern with `{alias}`) | empty → no SSH helpers |
| `prod_remote_project_dir` / `prod_remote_odoo_conf` / `prod_db_name` / `prod_link_addons_cmd` (where this same project sits on the box, for the deploy and prod-ops skills) | `~/<project_name>` / `<that dir>/odoo.conf` / `odoo` / `nix run .#update-repos` |
