#!/usr/bin/env python3
"""Regression corpus for nudge-kb-truncation.py.

BLOCK = the hook must bounce it (exit 2). PASS = it must let it through.
Run from the repo root: python3 tests/test_kb_truncation_nudge.py\n(the hook is read straight from the template; no rendering needed)
"""

import json
import subprocess
import sys

HOOK = sys.argv[1] if len(sys.argv) > 1 else (
    "template/{% if use_claude_code %}.claude{% endif %}/hooks/"
    "{% if use_kb %}nudge-kb-truncation.py{% endif %}"
)

CASES = [
    # the measured misfire that started this
    ("BLOCK", "sed range plus cut on a record",
     "kb show user_identity --scope global 2>/dev/null | sed -n '1,45p' | cut -c1-150"),
    ("BLOCK", "cut -c alone", "kb show project_kb_tool | cut -c1-120"),
    ("BLOCK", "head on a search", "kb search 'payment terms' | head -5"),
    ("BLOCK", "tail on today", "kb today --scope kaertech-odoo-16 | tail -3"),
    ("BLOCK", "head on a query", "kb query 'MATCH (r:Record) RETURN r' | head -2"),
    ("BLOCK", "awk NR filter", "kb index | awk 'NR<=10'"),
    ("BLOCK", "grep -m on a record", "kb show feedback_testing | grep -m 1 test"),
    ("BLOCK", "sed range on links", "kb links 68 | sed -n '1,5p'"),
    ("BLOCK", "cut after a grep", "kb show 68 | grep -i odoo | cut -c1-90"),
    ("BLOCK", "less on a record", "kb show 68 | less"),

    # journals are expected to be read in slices
    ("PASS", "service logs sliced", "kb service logs -n 200 | tail -20"),
    ("PASS", "event log sliced", "kb log 283 | head -5"),

    # filters that keep every character
    ("PASS", "fold wraps rather than cuts", "kb show 68 | fold -w 100"),
    ("PASS", "cut -f on a delimited stream", "kb query 'RETURN 1' | cut -f1"),
    ("PASS", "grep without -m keeps every match", "kb show 68 | grep -i odoo"),
    ("PASS", "sed substitution is not a range", "kb config show | sed 's/password/xxx/'"),
    ("PASS", "whole record", "kb show user_identity"),
    ("PASS", "redirected to a file", "kb show user_identity > /tmp/id.txt"),

    # the deliberate escape hatch
    ("PASS", "explicit marker", "kb show 68 | head -3  # truncation-ok"),

    # nothing to do with kb
    ("PASS", "some other command", "git log --oneline | head -5"),
    ("PASS", "a path that merely contains kb", "cat /home/mark/projects/kb/README.md | head -5"),
    ("PASS", "kb-setup is not a read", "kb-setup --dry-run | head -5"),
    ("PASS", "unbalanced quotes are left alone", "kb show 'broken | head -3"),
]

fails = 0
for expected, name, command in CASES:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    proc = subprocess.run([sys.executable, HOOK], input=payload, capture_output=True, text=True)
    got = "BLOCK" if proc.returncode == 2 else "PASS"
    if got != expected:
        fails += 1
        print(f"FAIL  expected {expected}, got {got}: {name}\n      {command}")
        if proc.stderr:
            print("      stderr:", proc.stderr.strip().splitlines()[0][:110])
print(f"{len(CASES) - fails}/{len(CASES)} cases pass")
sys.exit(1 if fails else 0)
