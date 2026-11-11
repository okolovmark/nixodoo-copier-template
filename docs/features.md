# What a generated project contains

The full inventory behind the summary in [the README](../README.md#what-it-generates).

- **Odoo 16.0 / 17.0 / 18.0 / 19.0 / 20.0**, **Python 3.10–3.14**, **PostgreSQL 13–18** — picked at
  generation time; every Odoo version runtime-verified end-to-end (clone → lock → build → `-i base`)
- Nix flake with three installable toolchains (`dev-server`, `test-server`, `prod-server`):
  wrapped `odoo`, `psql`/`pg_dump`/... bound to the project cluster (dev/test; `prod-server`
  ships Odoo without a proxy or a database client), `ruff`, `uv`, `ccze`
- Pinnable source management: `repos.yaml` (odoo) + `addons.yaml` (OCA/custom addon repos,
  branch- or commit-pinned) with an addons symlink farm builder
- Generated configs: `.env`, `odoo.conf` (random master password), systemd user units incl.
  daily log rotation, and an nginx reverse proxy for test only, and dev without worktree envs
  (`prod-server` ships no proxy - production keeps its own front end)
- Python deps imported straight from Odoo's own `requirements.txt` and locked with `uv`
- **Claude Code integration** (optional): `CLAUDE.md`, guard hooks (read-only OCA/core,
  dangerous-command blocker, ruff auto-format), Odoo dev skills (code patterns, style,
  testing, commit conventions, pre-PR checklist, **semantic code navigation** over the
  official Odoo Language Server (`super()` chains across `_inherit`, XML `ref`/model
  targets, find-usages, model inheritance maps), a debug recipe,
  isolated worktree envs, an **assess** pass that decides whether an idea is worth
  building at all before anything is specified — go / clarify / kill, where a kill
  is a successful outcome — a **grill** requirements interview for what survives —
  one question at a time, decisions routed to their owner, every exemption and
  state rule confirmed by the requester as a table with real examples, the case
  against, an appetite agreed before options are drawn — and
  **domain-modeling**: a `CONTEXT.md` glossary of canonical terms mapped to Odoo
  models + `docs/adr/` decision records, both maintained as design conversations
  resolve; `adr-number.sh` numbers a record across every branch and worktree, so
  two parallel branches cannot take one number), `dev`/`review`/`verify`/`documenter` agents, a persistent
  **memory template** (journal / state / standup), and optional workflow skills:
  the **pipeline** orchestrator (task → dev → QC gate → docs → demo GIF → PR, split into a
  mother session that grills and deploys and a child session that develops; see
  [ADR 0001](adr/0001-pipeline-mother-and-child-sessions.md); with `use_release_notes`, a
  user-facing release note written after the docs as a file in the PR, which the deploy skill
  stamps with its deployer), **release-note** (with `use_release_notes`: how that note is
  written, and the holder module `<module_prefix>_release_notes` that keeps the note files for
  the shared `release_notes` engine), **my-status**
  (status posting, Teams), **teams-message** (readable Teams messages: HTML Teams
  actually renders, recipient verified by email, draft before send),
  **odoo-tickets** (ticket tracking in a prod Odoo),
  **deploy-checks** (blast-radius classification of a change set + read-only
  post-deploy invariant check: unbalanced postings, failed queue jobs, stuck
  crons, negative stock), **prod-ops** (working with the prod box outside a
  deploy: read-channel choice — RPC vs local prod-dump DB vs live odoo shell —
  prod forensics, a named-script runner with a dry → commit → read-back
  ladder, and the ticket-script conventions: `scripts/<TICKET>/` layout,
  docstring header, landing via PR and a `RUNS.html` run record), **deploy** (the prod runbook itself: check the PR is mergeable,
  derive the `-u` list from what the pull actually lands, show the plan, take
  the box's deploy lock — waiting on another developer's deploy instead of
  colliding with it — merge, one named write script per prod step, T+0/T+60
  invariant checks, release the lock — with the announcement and ticket-note
  beats gated on your `status_mcp`/`tickets_mcp` answers) and **estimate** (effort estimates priced from a calibration table of
  your own closed tickets, not from gut feel)
- **Claude Code mods** (function-hook plugins under `.claude/skills/<name>/`, loaded in a
  trusted workspace): **odoo-env-band**, a row above the prompt with the checkouts this
  session works in (env repo, addons, its worktree env: branch, changes, ahead/behind as `✓ ±N ↑N
  ↓N`, port, and an `open` button for its address) after the postgres cluster's state, a
  worktree's port and state beside its label and a bar between each two groups;
  **settings-sync**, `/settings-sync` then `/settings-sync apply`: the team's Claude settings
  (user and project-local) added to a machine's, never overwriting, from two layers, the template's
  (`.claude/skills/settings-sync/baseline/`) and the project's own (`.claude/settings-sync/`, written
  by its maintainer's `sync.py save`), with machine paths and claude.ai connector ids filled in;
  **deploy-watch**, the deploy skill's T+60 re-check as a timer that survives a restart,
  with a countdown, run-now and cancel; **pipeline-gates** (with the pipeline), the pipeline's gates as code: no `dev`
  agent and no worktree edit before the Teams status and the user's grill press, no
  `gh pr create` before a QC pass that rests on a review and a green test run, with a phase pane;
  a step that waits on the grill press pushes to the phone and asks in a dialog (Confirm /
  Mechanical / Not yet); a child session runs on the status and grill gates the user opened in
  its mother's run, each naming that run, behind a QC gate of its own, and the mother closes
  once a run for its tasks recorded a QC pass or a PR; a block of tasks is one run whose progress (done, in work, pending)
  the pane and band show, each task with an open-in-Odoo link (its record URL, or its key's
  `/tasks/<key>` with `task_key_route`); only the person opens a gate or ends a run by hand
- Optional (asked during generation): custom addons repo wiring, S3 production-backup
  restore with **native Odoo neutralization** (`odoo neutralize` + dev fixups),
  SSH helpers for prod/test servers, OCA `queue_job` wiring, and on Odoo 16.0 and 20.0 the
  shared modules of [okolovmark/nixodoo-addons](https://github.com/okolovmark/nixodoo-addons)
  in `addons.yaml`: `release_notes` with `use_release_notes`, `project_software` with
  `task_software_fields`
- `nix flake check` shellchecks every project script

## Memory

With `use_kb = false` a generated project carries a markdown memory seed under
`.claude/memory-template`: an index, a journal, a `state.md` the standup reads, and
`init-memory.sh` to install it into Claude Code's per-project memory directory.

With `use_kb = true` the seed is not generated at all. Memory lives in
[kb](https://github.com/okolovmark/kb) instead, and the SessionStart and SessionEnd hooks
open the session record, print the pinned rules and a standup count, under the 10,000
characters Claude Code keeps of a hook's output, and close the session. A compacted session is
asked for its session note first, and a stale kb schema is named. Notices (`kb add --once`) are
the user's: they show in the kb pane and never reach the model's context. `.kb/tags.toml`
seeds the project's kb area vocabulary once, with the areas of the tooling the project has; the
project owns it from then on.
`CLAUDE.md` gets a "Memory: kb" section, so the instructions live in the repository rather
than in Claude Code's per-user `MEMORY.md`; turn auto-memory off locally
(`autoMemoryEnabled: false` in `.claude/settings.local.json`) so the harness stops asking for
memory files there.
