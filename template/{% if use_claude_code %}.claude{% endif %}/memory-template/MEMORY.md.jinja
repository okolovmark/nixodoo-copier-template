# Memory Index

Always-loaded index for Claude's persistent memory. Keep under 200 lines.

> **This is a template.** `__MEMORY_DIR__` is a placeholder for the absolute
> path to the installed memory directory (e.g.
> `$HOME/.claude/projects/<project-slug>/memory`). Running
> `scripts/init-memory.sh` substitutes it automatically; if you copy by hand,
> replace every `__MEMORY_DIR__` with the real absolute path.

## At session start (priority — run this first)

### Memory location

Memory lives at:

```
__MEMORY_DIR__
```

### Standup procedure

This is **non-negotiable** — it's the daily continuity layer. Run once
before responding to the user's first message.

**One command** — use the literal absolute path (no variables, no
compound `MEM=...; ...` — those break the exact-match permission rule):

```bash
bash __MEMORY_DIR__/scripts/standup.sh
```

All logic (trigger check, stale markers, orphan node scan, journal write)
lives in the script. **Do not re-implement it inline with Bash compound
commands** — that path requires `Bash(cmd:*)` allowlist wildcards which are
rejected for security reasons.

**How to interpret the output:**

- Starts with `SKIP:` → today's journal already has a session block; do
  not show anything, proceed to the user's message.
- Starts with `EMPTY:` → state has zero items and no orphans; do not show
  anything, proceed.
- Otherwise → the script has already written the rendered standup block
  to `__MEMORY_DIR__/journal/$(date +%Y-%m-%d).md`. **Show the output
  verbatim to the user**, then respond to their first message.

**On-demand standup** (user asks mid-session): run
`bash __MEMORY_DIR__/scripts/standup.sh --force`. Same output conventions.

For the full behavior protocol (when to write nodes, journal, state.md
updates, ID generation, recovery), see [[conventions_behavior_protocol]].

## Raw drops (external input the user dropped in `raw/`)

Separate input channel from conversation: the user drops URLs, notes,
screenshots, pasted articles into `__MEMORY_DIR__/raw/` — typically via a
shell alias (`bash __MEMORY_DIR__/scripts/raw-drop.sh`), or by dropping
files directly into the folder.

**When to process:** on explicit user ask ("process my drops", "digest
raw"). **Do not auto-process every session** — too expensive, and the user
may add items over multiple days before they want the batch done.

**How:**

1. Run:
   ```bash
   bash __MEMORY_DIR__/scripts/digest.sh
   ```
   Output starts with `EMPTY:` (nothing to do) or a list of unprocessed
   paths (relative to `raw/`).
2. For each listed item: read it, then either update an existing node, create
   a new node ([[conventions_node_format]]), or skip. Append the relative path
   to `__MEMORY_DIR__/raw/.processed` so the next digest skips it.
3. When the batch is done, write a `## Session` journal block summarising what
   was added/updated/skipped.

## How this works

Claude reads this file at the start of every session in this project.
Individual nodes in `nodes/` are read on demand (Claude greps by keyword
or follows `[[wikilinks]]` from a relevant node).

Journal entries in `journal/` are NEVER listed here — they live outside
the index by design and are read only when continuity is needed.

The mutable file `state.md` (next to this index) is the source of truth
for currently-open work. Standups read it directly, not the journal.
The `state_counter` file next to it holds the next ID to assign.

## Nodes

<!-- One line per node, grouped by type. Add as Claude creates them. -->
<!-- This is the single index — no duplicate list elsewhere in this file. -->

### Reference (conventions and external pointers)
- [Node format rules](nodes/conventions_node_format.md) — how to write/link nodes
- [Journal format rules](nodes/conventions_journal_format.md) — how to write session logs
- [Tag vocabulary](nodes/conventions_tag_vocabulary.md) — editable list of valid hashtags
- [Behavior protocol](nodes/conventions_behavior_protocol.md) — full Claude behavior rules
- [Bootstrap questions](nodes/bootstrap_questions.md) — template for first-time onboarding

### Reference (Odoo framework facts)
<!-- Framework knowledge belongs in the SKILLS (code-patterns/testing/debug/style
     references), which every generated project shares — a memory node here is
     for facts about THIS environment. Add framework nodes only for behavior not
     yet folded into a skill, and migrate them there once they stop being a
     surprise and become a step. -->

### User
- [User role and preferences](nodes/user_role.md) — who the user is, how they work (stub — fill on bootstrap)
- [User identity for automations](nodes/user_identity.md) — name/logins/chat ids for Teams + Odoo; whose name the setup acts under (stub — fill on bootstrap; placeholders keep integrations disabled); read it before any Teams/Odoo automation

### Feedback
<!-- corrections and confirmations from the user — rules to apply going forward -->

### Project
<!-- non-obvious facts about the project: decisions, gotchas, history -->
