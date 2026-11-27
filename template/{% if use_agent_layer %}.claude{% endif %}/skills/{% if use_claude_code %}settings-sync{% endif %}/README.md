# settings-sync

The team's Claude Code settings, kept in the repository and spread onto any machine. They live
outside the repository otherwise: `~/.claude/settings.json` (user) and the checkout's
`.claude/settings.local.json` (gitignored), so a new laptop or a new teammate starts without them.

```text
/settings-sync          show what the team settings would add to yours; writes nothing
/settings-sync apply    add it
```

The same script runs from a shell too, before Claude is set up:

```bash
python3 .claude/skills/settings-sync/sync.py check
python3 .claude/skills/settings-sync/sync.py apply
```

## Two layers

| Layer | Where | Owned by | Holds |
|---|---|---|---|
| template | `.claude/skills/settings-sync/baseline/` | the copier template | settings any project made from the template shares: preferences, local dev rules |
| project | `.claude/settings-sync/` | this project | what only this project needs: its connectors, its production and deploy rules, its own wording |

The project layer is applied over the template layer: where both set a value, the project's wins.
Each holds `user.json` (for `~/.claude/settings.json`), `local.json` (for `.claude/settings.local.json`),
`files/` (a hook's script under `~/.claude/hooks/`) and `connectors.json`. The project layer also holds
`maintainer`, the git email whose `save` writes it.

`~/.claude/settings.json` applies in every project on the machine, so `user.json` holds preferences
only. Anything that names this project (its services, databases, hosts, `autoMode` rules, the
project root variable in `env`) goes into `local.json`.

## What it does

- **Shows first.** The layers sit in a repository the whole team can change, so read them before
  they land. With no word the command only reports. Lines marked `!` run code or let Claude act
  unasked: hooks, permission rules, `autoMode`, `env`, plugins, a restored script with its sha256.
  `apply` writes.
- **Adds, never overwrites.** It adds a missing key, a missing list item (a permission rule, an
  `autoMode` line, a hook) and a missing file. Your own values stay: a setting you have with another
  value is kept and the report lists both (`= theme: yours "light", baseline "dark"`).
- **Backs up first.** Before it writes, the file is copied beside itself as `<file>.bak-<time>`. If
  nothing is missing, nothing is written.
- **Fits your machine.** Paths are stored as `@HOME@`, `@PROJECT@` (the checkout) and
  `@PROJECT_SLUG@` (its folder name under `~/.claude/projects`).
- **Finds your connectors.** The desktop app names a claude.ai connector by an id that differs per
  account, so a rule for one is stored as `mcp__@NAME@__tool`, and `connectors.json` declares
  `NAME` by the tools that tell its server apart (`{"SHOP": ["get_order", "list_orders"]}`).
  `/settings-sync` finds the server carrying them in the session and fills it in. From a shell the
  script skips those rules and says so; run `/settings-sync apply` once Claude is up.
- **Hook scripts come along.** A script under `~/.claude/hooks/` that a setting calls is restored if
  it is missing. Nothing outside `~/.claude/hooks/` is restored, and a path with `..` is refused.

## New laptop or new teammate

1. Clone the repository (or `git pull`).
2. Start Claude Code in the checkout, run `/settings-sync`, read the `!` lines, then
   `/settings-sync apply`.
3. Start a new session: settings are read when a session starts.

## Keeping the layers

- **Template layer**: edited in the copier template and brought here by `copier update`; never by
  hand in a project.
- **Project layer**: its maintainer refreshes it from their own settings, from a shell:

  ```bash
  python3 .claude/skills/settings-sync/sync.py save
  ```

  It writes the settings less what the template layer already gives. The first `save` names its git
  email the maintainer; anyone else's is refused, so nobody replaces the team settings with their
  own by accident. To template a connector's rules, pass its server: `--connector NAME=SERVER`.
  `save` leaves out what looks like a secret and says so: environment variables named like one
  (`*KEY*`, `*TOKEN*`, `*PASSWORD*`, …), any value shaped like a key or a token (`sk-…`, `ghp_…`,
  `Bearer …`, `password=…`, a private key) and a hook script holding one. Read the diff before you
  commit it: a one-off approval you would not want shared comes out by hand.

## Develop

```bash
python3 -m unittest discover -s .claude/skills/settings-sync/tests -p 'test_*.py'
claude plugin validate .claude/skills/settings-sync
claude plugin test .claude/skills/settings-sync
```
