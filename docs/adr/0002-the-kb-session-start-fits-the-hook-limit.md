# The kb session start fits in the 10,000 characters a hook keeps

Status: accepted (the maintainer, 2026-10-26, in the kb record of 0001's grill); supersedes the child's slim output in
[0001](0001-pipeline-mother-and-child-sessions.md)

Claude Code keeps a hook's output in context only up to 10,000 characters. Past that the model
gets a file path and the first 2,000 characters. The kb SessionStart hook printed the whole
index and the standup, 29,954 characters on one production project on 2026-10-26 (pinned 6,587,
recent 19,917, standup 3,211), so the standup and most pinned rules never reached the model.
The slim output of 0001 was meant for pipeline children, keyed on a `[child] ` title, but the
desktop app passes no `session_title` to the hook, so it never fired.

The maintainer chose what the start carries, the same for every session:

- **The pinned rules, one line each**, as `kb index` prints them (slug and a summary cut at 160
  characters). Their bodies (299 KB) cannot fit; `kb show` reads one.
- **The standup as one count line**: open tasks, loud, quiet. `kb today` prints the list; the
  user sees it in the kb band. Nothing is recorded as shown.
  **Amended by the maintainer, 2026-10-29**: the line counts the tasks on the standup, loud, and others
  (`kb today --json` `others`: every open task below normal, silent ones included); `quiet`
  counted whisper alone and hid 41 of 90 open tasks.
- **The recent records as a count**: `kb index` lists them.
- **Notices** (`kb add --once`) **as a count**: the model tells the user and hands them
  `kb notices show` to run in their own terminal. The notices close when it prints them, and
  their text never enters the model's context. Shown only in the kb band, the user would miss them.
  **Superseded by a later kb decision** (the maintainer, 2026-10-26): notices live only in the kb pane, with
  a dismiss button and a count in the band; the session start prints no notices line and never
  calls `kb notices`, and `kb notices show` no longer closes them.
- **No child mode**: one output fits every session.

The sessions closed without a summary keep their one line: an instruction to the model, not
part of the task list.

The hook holds the whole output under 9,500 characters, counted as Claude Code counts them
(UTF-16 code units). Past that, the last pinned rules give way first, to a line naming
`kb index`; if that is not enough, the output is cut at a line with a line saying so. What the
model must act on prints first, so it is kept longest. Measured with the real kb on
2026-10-26: 7,240 characters, with room for about 12 more pinned rules before any give way.

## Considered options

- **Pinned rules cut to 100 characters, or slug only.** Rejected: the summary is the rule, and
  the cut end is where the exception lives. Slug only means a `kb show` per rule.
- **The most urgent standup tasks that fit, or the loud ones only.** Rejected for a count: 20
  of 23 tasks were loud, so a loud-only list saved little, and the user has the list in the kb
  band.
- **The 10 newest recent records.** Rejected: `kb index` on demand.

## Consequences

- `kb today --record-shown` no longer runs at the session start, so a `--once` task is closed
  by `kb notices show` (kb PR okolovmark/kb#7). A kb from before `kb notices` prints no
  count: its notices wait until kb is upgraded. **Superseded by that later kb decision**: a notice
  closes by `kb notices dismiss`, the kb pane's dismiss button.
- A model that needs the standup list or a recent record runs `kb today` or `kb index`.
