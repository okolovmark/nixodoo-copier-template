---
name: conventions_journal_format
description: Template and rules for writing journal entries
type: reference
created: 2026-04-09
updated: 2026-04-09
---

# Journal entry format

Each day-file (`journal/YYYY-MM-DD.md`) starts with a top-level date heading,
then optionally a daily standup block, then one or more sessions.
All section headers use fixed names so grep works predictably.

**Time zone:** all dates use the local timezone (`date +%Y-%m-%d`).
Day rollover happens at local midnight.

**Mid-session midnight rollover:** if a session spans local midnight, the
journal entry goes in the day-file matching the date when the entry is
**written** (end time, not start time). The previous day's day-file is
left as-is. This means a session that started yesterday and ends today
will create a new day-file for today without a standup block at the top
(the standup runs at the *next* fresh session, not retroactively).

**Note on terminology:** in this project, "session" means **one conversation
thread** (one Claude Code conversation, not the technical OS process).
The standup runs once per local-day's first conversation.

## Day-file template — standup + sessions

A typical day-file with a standup block at the top and one session.
Note that items in the standup were **opened in a previous day's session**
(their dates are earlier than the day-file's date) — that's why they
appear in the standup before any of today's work.

```markdown
# 2026-04-08

## Daily standup — 2026-04-08

### Ship blockers
_(empty)_

### Blocked
- [42] 2026-04-05: legal review for MSL token format — see journal/2026-04-05 [stale: 30 days]

### Ready for review
_(empty)_

### WIP
_(empty)_

### Open threads
- [44] 2026-04-07: Verify multi-company behavior — see journal/2026-04-07

---

## Session 1 — 15:45

**Intent:** Fix multi-company validation bug in stock_picking
**Tags:** #custom_stock #custom_mrp #bug #validation #compliance
**Files touched:**
- M src/odoo-addons/custom_stock/models/stock_picking.py
- M src/odoo-addons/custom_stock/views/stock_picking_views.xml
- A src/odoo-addons/custom_stock/tests/test_picking_validation.py

**Nodes referenced:** [[feedback_testing]], [[project_msl_module]]
**Nodes created:** [[project_picking_validation_rule]]
**State changes:** added [45] open thread, closed [44] (verified)

### Done
- Added validation for picking type X when scrap_reason_id is set
- Updated form view to show scrap_reason_id only when relevant
- Wrote TransactionCase test covering both branches
- Closed [44]: Verify multi-company behavior — confirmed working with multi-company

### Decisions
- Used computed field (non-stored) for scrap_visible flag instead of
  related field — avoids cache invalidation on parent changes.
  Tradeoff: recomputed on every read; acceptable here since the form
  is rarely opened in batch.

### Notes
Mark mentioned the legal team is reviewing scrap categories next week —
might need to revisit the rule then.

### Open threads
- [45] Test coverage missing for edge case where scrap_reason_id is set
  but picking type changes mid-flow

---

## Session 2 — 17:00

(another session same day, same structure)
```

The `## Daily standup` block is **not** a session — it doesn't count
toward session numbering (see "Session numbering" below).

## Section order within a session block (fixed)

1. **Intent:** (optional, recommended) — what Claude was trying to do
2. **Tags:** (required)
3. **Files touched:** (required, write "none" if empty)
4. **Nodes referenced:** (optional)
5. **Nodes created:** (optional)
6. **State changes:** (optional, recommended) — IDs added/closed in state.md
7. **Done** (required)
8. **Decisions** (required, write "none" if empty)
9. **Notes** (optional)
10. **Open threads** (required, write "none" if empty) — **always last**

## Session numbering

Within a day-file, sessions are numbered sequentially: `Session 1`, `Session 2`, ...

Counting rule: `N` = (number of existing `## Session ` headers in the day-file) + 1.

The standup block (`## Daily standup`) does **not** count as a session.

## Timestamp

Single timestamp = end time of the session (when Claude wrote the entry).
Format: `HH:MM` in 24-hour local time (`date +%H:%M`).

`## Session 1 — 15:45` means session 1 ended at 15:45.

## Files touched format (git-porcelain style)

Each file gets an A/M/D action prefix matching git's add/modify/delete.
This avoids markdown ambiguity (`- - file.py` would be a nested list)
and is familiar to anyone who knows git.

```markdown
**Files touched:**
- A src/path/to/new_file.py        (added)
- M src/path/to/existing.py        (modified)
- D src/path/to/old_file.py        (deleted)
```

Only files that were **changed** are listed. Files that Claude only read
as routine context are NOT listed (would balloon the journal).

Paths are **relative to project root**,
not to the memory directory.

Grep recipes:
```bash
grep "^- A " journal/*.md   # all added files
grep "^- M " journal/*.md   # all modified files
grep "^- D " journal/*.md   # all deleted files
```

## Tag conventions

Tags are space-separated `#hashtag` strings on the `Tags:` line. Lowercase,
underscores for multi-word. Always use the `#` prefix so grep is precise
(`grep "#bug"` matches only the tag, not the word "bug" in prose).

The full vocabulary lives in [[conventions_tag_vocabulary]] — edit it freely
to add/remove tags as the project evolves.

A typical session has 2-5 tags from different categories: one module,
one work-type, zero or more domain tags, optionally a status tag.

## Tag rules

- **Be consistent** — `#custom_stock` not `#stock`, `#custom-stock`, or `#CustomStock`
- **Use existing tags first** — `grep -roh "#[a-z_]\+" journal/ | sort -u`
  shows what's already in use; check [[conventions_tag_vocabulary]] for the canonical list
- **Don't invent unique tags per session** — tags are an index, not labels
- **Status tags must sync to state.md** — if a session has `#wip`, `#blocked`,
  `#ship_blocker`, or `#ready_for_review`, the corresponding entry must also
  be added to `state.md` (see [[conventions_behavior_protocol]] for the
  state.md update protocol)

## Search recipes

All examples assume `cd` to the memory directory or replace bare `journal/`
with `$MEM/journal/` where `MEM` is the memory location. `state.md` is
always referenced via `$MEM` for clarity (it's one level up from `journal/`).

```bash
# What did I do on a specific date?
cat journal/2026-04-08.md

# All sessions tagged with the MSL module (use the full canonical tag)
grep -l "#msl_module" journal/*.md

# All bug-fix sessions
grep -l "#bug" journal/*.md

# Combine tags: bugs in custom_stock
grep -l "#custom_stock" journal/*.md | xargs grep -l "#bug"

# All work in progress
grep -l "#wip" journal/*.md

# Anything currently open — read state.md instead!
cat "$MEM/state.md"

# Show open threads from a specific journal file
awk '/^### Open threads/{flag=1; next} /^### |^## |^---/{flag=0} flag' journal/2026-04-08.md

# Show notes from a specific journal file
awk '/^### Notes/{flag=1; next} /^### |^## |^---/{flag=0} flag' journal/2026-04-08.md

# Show intents from all sessions in a day
grep "^\*\*Intent:\*\*" journal/2026-04-08.md

# Last 5 days
ls -t journal/*.md | head -5

# When did I last touch a specific file?
grep -l "stock_picking.py" journal/*.md | tail -1

# All compliance work this month (dynamic)
grep -l "#compliance" "journal/$(date +%Y-%m)-"*.md 2>/dev/null

# All compliance work in a specific month (manual)
grep -l "#compliance" journal/2026-04-*.md

# Show all tags in use, sorted by frequency
grep -roh "#[a-z_]\+" journal/ | sort | uniq -c | sort -rn

# Recent file additions (last 20 added-file lines from journal)
grep "^- A " journal/*.md | tail -20

# Files added in the last 7 day-files
ls -t journal/*.md | head -7 | xargs grep -h "^- A " 2>/dev/null

# Find sessions that referenced a specific node
grep -l "\[\[project_msl_module\]\]" journal/*.md

# Find a specific state ID across all journals (where it was opened/closed)
grep "\[42\]" journal/*.md
```
