# The pipeline runs in a mother session and child sessions

Status: accepted (grill of 2026-10-22, recorded in the maintainer's kb)

One pipeline run used to live in one session, from reading the ticket to closing it, so the
grill, the ticket work, every dev and QC round and the deploy all sat in one context that each
call re-read. The run is now split. A **mother** session reads the task, loads what kb knows
about its areas (`kb context`), grills, sends the status and writes the ticket with its test
cases. Then it writes a brief and starts a **child** session (title `[child] …`, in the base
checkout) that brings up its own worktree env and runs dev, the QC gate, docs, the demo GIF
and the PR, and ends with a fixed report. Afterwards the mother checks the PR and CI, deploys
only on the user's command, and closes the ticket and the kb task.

- **Grouping is the mother's call**: related tickets (one module, the same files) go to one
  child with one env and one PR, independent ones to children in parallel.
- **Questions go up**: a child asks the mother (`SendMessage`), the mother asks the user,
  writes the answer on the ticket and replies. After the PR the mother forwards review
  comments and CI failures, and the child fixes them.
- **The brief has fixed sections**: task and goal, the Alignment Summary, the rules as kb
  record ids with one line each, acceptance and test cases (on the ticket), what not to do,
  the report format.
- **The child's SessionStart output is slim**: the kb hook recognises the `[child] ` prefix of
  `session_title` and prints the pinned rules and a one-line standup count, about 7 KB instead
  of about 31 KB, which is over the 10,000 characters Claude Code keeps of a hook's output.
  If the app does not fill `session_title` for a session it starts, a child gets the full
  output: that fallback was agreed. **Superseded by [0002](0002-the-kb-session-start-fits-the-hook-limit.md)**:
  the app passes no `session_title` at all, and every session now gets the slim output.
- **No session tool, one session**: in terminal `claude`, in Antigravity or for a teammate
  without the desktop app, the whole pipeline runs in one session, as before, and says so.

## Considered options

- **One session, as before.** Rejected: the context cost grows with every task, and the dev
  rounds re-read the grill they do not need.
- **The child as a subagent of the mother.** Rejected: a subagent cannot spawn the `dev`,
  `review` and `documenter` agents the child runs, and cannot ask the user for the demo GIF
  itself.
- **Agent teams.** Rejected for the reasons in the skill's "Agent economy": a teammate ignores
  its definition's `skills:`, spawns only in an interactive CLI session and does not survive
  `/resume`.

## Consequences

- Two sessions are more moving parts, and the brief is a single point of failure: a thin brief
  costs questions through the mother. Accepted.
- The child's report reaching the mother at the end of its turn, and `SendMessage` from child to
  mother, are assumptions of the desktop app; if either fails, the user relays by hand.
- The pipeline-gates mod keeps one run per session. Without a change the child's dev work and
  the mother's closure were refused until the user opened each gate by hand. The maintainer chose
  (2026-10-22, through the mother session) that a child (the skill loaded as
  `child <TASK-KEYS>`) takes the status and grill gates of an active run in another session
  for the same task, read from the mod's store, which every session shares. It does so only
  when both were opened in that run itself, never when that run took them from another. Each
  taken gate names the run it came from. The mother's Phase 7 opens once a run for its tasks
  recorded a QC pass or a PR. Nothing in the child is gated less: its QC gate in front of
  `gh pr create` stays its own.
