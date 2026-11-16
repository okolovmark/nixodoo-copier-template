#!/usr/bin/env python3
"""Regression corpus for guard-kb-task.py.

BLOCK = the hook must bounce it (exit 2). PASS = it must let it through.
Run from the repo root: python3 tests/test_guard_kb_task.py
(the hook is read straight from the template; no rendering needed)
"""

import json
import subprocess
import sys

HOOK = sys.argv[1] if len(sys.argv) > 1 else (
    "template/{% if use_agent_layer %}.claude{% endif %}/hooks/"
    "{% if use_kb %}guard-kb-task.py{% endif %}"
)

CASES = [
    # a task, however it is spelled
    ("BLOCK", "plain task", 'kb add "Fix the thing" --scope acme-odoo-16 --tag workflow'),
    ("BLOCK", "pipeline OPEN_THREAD shape",
     'kb add "dead field — models/x.py:12" --scope acme-odoo-16 --tag sale --link RELATED:12'),
    ("BLOCK", "explicit --kind task", 'kb add "x" --kind task --tag sale'),
    ("BLOCK", "short -k task", 'kb add "x" -k task --tag sale'),
    ("BLOCK", "with a due date", 'kb add "x" --tag sale --due 2026-11-02'),
    ("BLOCK", "global --json first", 'kb --json add "x" --tag sale'),
    ("BLOCK", "absolute path to kb", '/home/u/.local/bin/kb add "x" --tag sale'),
    ("BLOCK", "env assignment in front", 'KB_SCOPE=x kb add "x" --tag sale'),
    ("BLOCK", "after &&", 'kb tags && kb add "x" --tag sale'),
    ("BLOCK", "after ; with no space", 'kb tags;kb add "x" --tag sale'),
    ("BLOCK", "inside $( )", 'ID=$(kb add "x" --tag sale --json | jq -r .id) && echo $ID'),
    ("BLOCK", "inside backticks", 'echo `kb add x --tag sale`'),
    ("BLOCK", "on the second line", 'kb show 12\nkb add "x" --tag sale'),
    ("BLOCK", "with a heredoc body", "kb add \"x\" --tag sale --body - <<'EOF'\nit's a body\nEOF"),
    ("BLOCK", "unbalanced quotes still judged", 'kb add "it is broken --tag sale'),

    # the user picked kb (or asked for this task)
    ("PASS", "marker", 'kb add "x" --tag sale  # user-picked-kb'),
    ("PASS", "marker after a chain", 'kb tags && kb add "x" --tag sale  # user-picked-kb'),

    # memory, not work
    ("PASS", "note", 'kb add "x" --kind note --summary "s" --tag sale'),
    ("PASS", "--kind=decision", 'kb add "x" --kind=decision --summary "s" --tag sale'),
    ("PASS", "-k feedback", 'kb add "x" -k feedback --tag workflow'),
    ("PASS", "-kreference glued", 'kb add "x" -kreference --tag workflow'),
    ("PASS", "howto with a heredoc body",
     "kb add \"x\" --kind howto --tag sale --body - <<'EOF'\nit's a body\nkb add y\nEOF"),
    ("PASS", "unbalanced quotes with --kind note", 'kb add "it is broken --kind note --tag sale'),
    ("PASS", "--once notice", 'kb add "PR 113 merged" --once --tag workflow'),
    ("PASS", "help", "kb add --help"),

    # other kb commands
    ("PASS", "show", "kb show 12"),
    ("PASS", "append that mentions kb add", 'kb append 12 "kb add was refused here"'),
    ("PASS", "session note", 'kb session note "filed nothing; asked about 3 items"'),
    ("PASS", "promote", "kb promote 12 PROJ-1234"),
    ("PASS", "done", "kb done 12"),

    # nothing to do with kb
    ("PASS", "echo of the words", 'echo "kb add x"'),
    ("PASS", "grep for the words", "grep -rn 'kb add' .claude/skills"),
    ("PASS", "commit message", 'git commit -m "guard: kb add of a task asks first"'),
    ("PASS", "a path that merely contains kb", "cat /home/u/projects/kb/README.md"),
    ("PASS", "kb-setup", "kb-setup --dry-run"),
]

fails = 0
for expected, name, command in CASES:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    proc = subprocess.run([sys.executable, HOOK], input=payload, capture_output=True, text=True)
    got = "BLOCK" if proc.returncode == 2 else "PASS"
    if proc.returncode not in (0, 2):
        got = f"CRASH({proc.returncode})"
    if got != expected:
        fails += 1
        print(f"FAIL  expected {expected}, got {got}: {name}\n      {command!r}")
        if proc.stderr:
            print("      stderr:", proc.stderr.strip().splitlines()[-1][:110])
print(f"{len(CASES) - fails}/{len(CASES)} cases pass")
sys.exit(1 if fails else 0)
