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
  every deploy. Nothing there runs before the skill's approval gate, and Claude
  cannot grant itself the permission entry — it composes it and you paste it.
- `postgres-mcp` in `.mcp.json` runs with `--access-mode=unrestricted` — it
  targets the **local dev database** only (`DATABASE_URI` from `.env`).
- `postgres-mcp` runs with `--with 'mcp<2'`: it imports
  `mcp.server.fastmcp`, which the `mcp` SDK dropped in 2.0.0, and does not cap
  its own dependency. The `odoo` server needs no pin — it goes through the
  `fastmcp` package, which caps `mcp` itself.
