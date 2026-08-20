#!/usr/bin/env python3
"""Regression corpus for template/.../hooks/nudge-find-code.py.

Run from the repo root: python3 tests/test_nudge_find_code.py

Every PASS case below is a grep the hook must leave alone; every NUDGE case is a
symbol lookup that find-code answers better. The first three cases are the
v0.17.1 field report: two call-site greps sailed through while a plain-text grep
whose pattern contained BRE `\\|` was bounced, and that single false positive
spent the whole once-per-session budget.
"""

import json
import shutil
import subprocess
import sys

HOOK = "template/{% if use_claude_code %}.claude{% endif %}/hooks/nudge-find-code.py"

CASES = [
    ("NUDGE", "call-site grep: symbol + parens + alternation",
     'grep -rn "_get_available_quantity(\\|_update_reserved_quantity(\\|_gather(" addons/ --include=*.py'),
    ("NUDGE", "bare-identifier alternation over the custom addons tree",
     'grep -rn "_gather\\|_get_available_quantity\\|_update_reserved_quantity" --include=*.py .'),
    ("PASS", "plain-text grep whose pattern holds quotes and BRE alternation",
     'cd /tmp && grep -rn "context.get(\'warehouse\')\\|context\\[\'warehouse\'\\]\\|\'warehouse\':"'
     ' stock/ mrp/ --include=*.py 2>/dev/null | grep -v tests | head -40'),
    ("PASS", "single-file outline grep",
     'grep -n "def _action_done\\|def write" src/odoo/addons/stock/models/stock_move.py'),
    ("PASS", "TODO sweep", 'grep -rn "TODO\\|FIXME" .'),
    ("NUDGE", "rg on a field name", 'rg "qty_available" src/'),
    ("PASS", "prose string", 'grep -rn "Reference must be unique" src/'),
    ("NUDGE", "dotted model name", 'grep -rn "stock.quant" src/custom-addons'),
    ("NUDGE", "_inherit hunt", 'grep -rn "_inherit = \\"stock.move\\"" src/'),
    ("PASS", "bare lowercase word", 'grep -rln "warehouse" src/custom-addons/mod/views/'),
    ("NUDGE", "class definition hunt", 'grep -rn "class StockQuant" src/'),
    ("PASS", "log grep on one file", 'grep -c error odoo.log'),
    ("NUDGE", "-e pattern form", 'grep -rn -e "_compute_qty" src/'),
    ("PASS", "unbalanced quotes: nothing reliable to inspect", 'grep -rn "_broken src/'),
]


def verdict(cmd, session):
    proc = subprocess.run(
        [sys.executable, HOOK],
        input=json.dumps({"session_id": session, "tool_name": "Bash", "tool_input": {"command": cmd}}),
        capture_output=True, text=True)
    return proc.returncode


def main():
    failures = 0
    for want, label, cmd in CASES:
        shutil.rmtree("/tmp/.findcode-nudge-CORPUS", ignore_errors=True)
        got = "NUDGE" if verdict(cmd, "CORPUS") == 2 else "PASS"
        ok = got == want
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'} want={want:5s} got={got:5s}  {label}")
    shutil.rmtree("/tmp/.findcode-nudge-CORPUS", ignore_errors=True)

    # One nudge per distinct question, at most MAX_NUDGES (6) per session.
    shutil.rmtree("/tmp/.findcode-nudge-BUDGET", ignore_errors=True)
    same = 'grep -rn "_get_available_quantity(" src/'
    seq = [verdict(same, "BUDGET"), verdict(same, "BUDGET"),
           verdict('grep -rn "_update_reserved_quantity(" src/', "BUDGET"),
           verdict('grep -rn "_gather(" src/', "BUDGET"),
           verdict('grep -rn "_collect_available_data(" src/', "BUDGET"),
           verdict('grep -rn "_compute_quantities(" src/', "BUDGET"),
           verdict('grep -rn "_apply_putaway(" src/', "BUDGET"),
           verdict('grep -rn "_seventh_distinct_symbol(" src/', "BUDGET")]
    expected = [2, 0, 2, 2, 2, 2, 2, 0]
    ok = seq == expected
    failures += not ok
    print(f"{'ok  ' if ok else 'FAIL'} budget sequence {seq} (2=nudge, 0=pass), expected {expected}")
    shutil.rmtree("/tmp/.findcode-nudge-BUDGET", ignore_errors=True)

    # Three spellings of ONE question share a slot (2026-08-20 field report:
    # hashing the raw pattern let `hr.employee.leave` alone eat the budget).
    shutil.rmtree("/tmp/.findcode-nudge-SPELLING", ignore_errors=True)
    seq = [verdict('grep -rn "_name = \\"hr.employee.leave" src/', "SPELLING"),
           verdict('grep -rn "hr.employee.leave\\b" src/', "SPELLING"),
           verdict('grep -rn "hr.employee.leave" src/', "SPELLING")]
    expected = [2, 0, 0]
    ok = seq == expected
    failures += not ok
    print(f"{'ok  ' if ok else 'FAIL'} spelling collapse {seq}, expected {expected}")
    shutil.rmtree("/tmp/.findcode-nudge-SPELLING", ignore_errors=True)

    # A symbol grep cut short by head/tail is answered WRONG, so it nudges even
    # with the budget spent (`grep -rn leave_rendered ... | head -20` dropped the
    # two references that mattered and a wrong conclusion reached a ticket).
    shutil.rmtree("/tmp/.findcode-nudge-TRUNC", ignore_errors=True)
    for index in range(6):
        verdict(f'grep -rn "_filler_{index}(" src/', "TRUNC")
    seq = [verdict('grep -rn "_still_nudged(" src/', "TRUNC"),
           verdict('grep -rn "leave_rendered" --include=*.py . | head -20', "TRUNC"),
           verdict('grep -rn "leave_rendered" --include=*.py . | head -20', "TRUNC")]
    expected = [0, 2, 0]
    ok = seq == expected
    failures += not ok
    print(f"{'ok  ' if ok else 'FAIL'} truncation bypasses the budget {seq}, expected {expected}")
    shutil.rmtree("/tmp/.findcode-nudge-TRUNC", ignore_errors=True)

    print("FAILURES:", failures)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
