# Notes and gotchas

Things that surprised somebody once, kept out of [the README](../README.md) so it stays short.

- The project ships no Odoo MCP server. Production Odoo is the claude.ai Odoo connector each
  developer connects with their own account (a server such as
  [okolovmark/odoo-fast-mcp](https://github.com/okolovmark/odoo-fast-mcp) behind it); a worktree
  env is read with `odoo shell` on its own conf or `psql`.
- With the worktree-env skill (Claude Code and a custom addons repo), development runs no main
  Odoo, no nginx and no main database: `setup-dev` skips `setup-prod`/`setup-test`, the
  `dev-server` profile carries no nginx, and `download-backup` without an `odoo.conf` only
  fetches the dump. A session's websocket works on its own port (`workers = 0`).
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
- `nudge-ui-design.py` (PreToolUse on Edit/Write) bounces the first edit of a
  view arch, an OWL template or a stylesheet in a session with a hint to load
  the `ui-design` skill; the retried edit passes, and the hook stays quiet for
  the rest of the session or once the transcript shows the skill loaded. The
  `dev` and `review` agents preload the skill. `UIDESIGN_NUDGE=0` disables.
  Corpus: `python3 tests/test_nudge_ui_design.py`.
- The `deploy` skill writes to production, so its write steps are named scripts
  under `skills/deploy/scripts/` with validated arguments, never inline remote
  commands: that is one permission decision instead of a fresh judgement call
  every deploy. Nothing there runs before the skill has shown its plan, and Claude
  cannot grant itself the permission entry — it composes it and you paste it.
- The deploy skill merges the PR itself, under a **deploy lock** on the box
  (`prod-deploy-lock.sh`: an atomic `mkdir` of `<prod project dir>/.deploy-lock`
  with holder, PR and time inside). Several developers deploying from their own
  clones used to learn of each other's deploys from a chat message; the lock is
  the machine check — taken before the merge, released after the result, kept
  raised (`state=failed`) when the deploy broke prod, and never broken by the
  skill on its own. `prod-deploy-modules.sh` refuses to run without it.
  Corpus: `python3 tests/test_prod_deploy_lock.py`.
- `nudge-kb-truncation.py` (PreToolUse, rendered with `use_kb`) refuses a Bash command that
  reads kb through a truncating filter: `head`, `tail`, `cut -c`, a `sed -n` range, an
  `awk NR` filter, `grep -m`, `less`. Unlike the find-code nudge it has no session budget
  and re-running does not make it pass, because a dropped sentence leaves no trace. Read
  the record whole or send it to a file; `kb log` and `kb service logs` are exempt as
  journals; `# truncation-ok` in the command is the deliberate escape. `KB_TRUNCATION_GUARD=0`
  disables it. Corpus: `python3 tests/test_kb_truncation_nudge.py`.
- `guard-kb-task.py` (PreToolUse, rendered with `use_kb`) refuses `kb add` of a task (no
  `--kind`, or `--kind task`) unless the command carries `# user-picked-kb`. Work outside the
  task at hand is asked about first, per item: here, a kb task, a ticket, or skip. Filing
  every finding unasked cost 65 archived tasks in one day (2026-10-28), 22 from a single
  session's sweep of review findings. Memory records (`--kind note|howto|feedback|reference|decision`)
  and `--once` notices pass; `$(…)`, backticks, chains and heredoc bodies are parsed, and
  unbalanced quotes are judged on the raw text rather than let through. `KB_TASK_GUARD=0`
  disables it. Corpus: `python3 tests/test_guard_kb_task.py`.
- With `use_entire`, every session pushes a checkpoint, so `refs/entire/*` and the loose-object
  count climb until any git command trips `gc.auto` and an unbounded `git repack` runs inside
  whatever process made that call — measured at 1325 refs and 76,636 loose objects: 16 GB RSS,
  ~600 % CPU, an OOM-killed editor, and a repack that restarts from scratch on the next launch.
  The generated README carries the caps and the capped `systemd-run` pack under
  [Entire and git auto-gc](../template/README.md.jinja).
- A worktree session's `odoo.conf` is written by `wt-start.sh`, not by `create-odoo-config`,
  so it follows the answers on its own: `queue_job` in `server_wide_modules` only with
  `use_queue_job`, `gevent_port` from Odoo 17 (`longpolling_port` before), the database role
  from `db_user` / `db_password`. Reports read the `report.url` system parameter, not any
  config option, so `wt-start` pins it to the session's own `http://127.0.0.1:<port>`; without
  it a session on a restored production dump renders reports against the dump's
  `web.base.url`, i.e. production. Corpus: `python3 tests/test_wt_start_conf.py` (needs
  jinja2), which CI runs once per push in its `wt-scripts` job.
- A new `revision:` in `addons.yaml` moves nothing on disk: the shared checkouts under `src/`,
  which every worktree env links, move only with `nix run .#update-repos`. `wt-start.sh` refuses
  a new env on a checkout off its pin (exit 3; `WT_SKIP_PIN_CHECK=1` goes on), warns of a
  pinned repo not cloned, and `wt-list.sh` names both after its table.
