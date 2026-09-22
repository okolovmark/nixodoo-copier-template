# Notes and gotchas

Things that surprised somebody once, kept out of [the README](../README.md) so it stays short.

- `.mcp.json` points the `odoo` MCP server at
  [okolovmark/odoo-fast-mcp](https://github.com/okolovmark/odoo-fast-mcp) —
  swap the URL for your own if you prefer.
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
