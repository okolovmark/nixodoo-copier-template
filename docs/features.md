# What a generated project contains

The full inventory behind the summary in [the README](../README.md#what-it-generates).

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
  targets, find-usages, model inheritance maps), a debug recipe,
  isolated worktree envs, a **grill** requirements interview — one question at a
  time, decisions routed to their owner, explicit assumptions — and
  **domain-modeling**: a `CONTEXT.md` glossary of canonical terms mapped to Odoo
  models + `docs/adr/` decision records, both maintained as design conversations
  resolve), `dev`/`review`/`verify`/`documenter` agents, MCP servers config, a persistent
  **memory template** (journal / state / standup), and optional workflow skills:
  the **pipeline** orchestrator (task → dev → QC gate → docs → demo GIF → PR), **my-status**
  (status posting, Teams), **teams-message** (readable Teams messages: HTML Teams
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

## Memory

With `use_kb = false` a generated project carries a markdown memory seed under
`.claude/memory-template`: an index, a journal, a `state.md` the standup reads, and
`init-memory.sh` to install it into Claude Code's per-project memory directory.

With `use_kb = true` the seed is not generated at all. Memory lives in
[kb](https://github.com/okolovmark/kb) instead, and the SessionStart and SessionEnd hooks
open the session record, print the index and the standup, and close the session.
