# Journal

Chronological **immutable** record of Claude sessions on this project.
Append-only. NOT in the memory index by design — read only when continuity
is needed.

For currently-open items (blockers, WIP, ready for review, open threads),
see `../state.md` instead. The journal is history; state.md is the
current working set.

## File naming

`YYYY-MM-DD.md` — one file per calendar day. All sessions on the same
day append to the same file. Time zone is local.

## When Claude writes here

At the end of a session, IF any of these are true:
- One or more files were changed (created/modified/deleted)
- A non-trivial decision was made (architectural, scoping, tool choice)
- Something was left intentionally incomplete (open thread, #wip, #blocked, #ready_for_review)
- A new memory node was created or significantly updated

Claude does NOT write a journal entry for pure Q&A or single trivial edits.

## How to search

- **By date:** filename
- **By tag:** `grep -l "#msl_module" journal/*.md` (precise — use the full canonical tag)
- **By file touched:** `grep -l "src/.../msl.py" journal/*.md`
- **By node referenced:** `grep -l "\[\[feedback_testing\]\]" journal/*.md`
- **By action on a file:** `grep "^- A " journal/*.md` (added), `^- M ` (modified), `^- D ` (deleted)
- **By state ID** (where opened/closed): `grep "\[42\]" journal/*.md`
- **Open threads from a specific day:** `awk '/^### Open threads/{flag=1; next} /^### |^## |^---/{flag=0} flag' journal/2026-04-08.md`
- **Combine tags:** `grep -l "#msl_module" journal/*.md | xargs grep -l "#bug"`
- **All tags in use:** `grep -roh "#[a-z_]\+" journal/ | sort | uniq -c | sort -rn`

For currently-active items, **read state.md, not journal**:
```bash
cat "$MEM/state.md"
```

## Entry format

See [[conventions_journal_format]] for the exact structure of each session
block, the section order, the timestamp format, and the canonical day-file
template (with standup at the top).
