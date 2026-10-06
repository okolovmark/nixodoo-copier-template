#!/usr/bin/env python3
"""Regression corpus for template/.../hooks/nudge-ui-design.py.

Run from the repo root: python3 tests/test_nudge_ui_design.py

Each case runs in a fresh session unless it names the session of the case
before it; NUDGE means the hook bounced the edit (exit 2).
"""

import json
import os
import subprocess
import sys
import tempfile

HOOK = "template/{% if use_claude_code %}.claude{% endif %}/hooks/nudge-ui-design.py"
MOD = "/w/src/custom-addons/x_appraisal"
SKILL_CALL = '{"type":"tool_use","name":"Skill","input":{"skill":"ui-design"}}\n'

# (want, label, tool, file_path, session, transcript text, env)
CASES = [
    ("NUDGE", "form view arch", "Edit", f"{MOD}/views/hr_appraisal_views.xml", "s1", "", {}),
    ("PASS", "second view edit, same session", "Edit", f"{MOD}/views/menus.xml", "s1", "", {}),
    ("NUDGE", "stylesheet written", "Write", f"{MOD}/static/src/scss/appraisal.scss", "s2", "", {}),
    ("NUDGE", "OWL template", "Edit", f"{MOD}/static/src/components/grid/grid.xml", "s3", "", {}),
    ("NUDGE", "wizard arch", "Edit", f"{MOD}/wizard/close_wizard_views.xml", "s4", "", {}),
    ("PASS", "model code", "Edit", f"{MOD}/models/hr_appraisal.py", "s5", "", {}),
    ("PASS", "data file", "Edit", f"{MOD}/data/mail_templates.xml", "s6", "", {}),
    ("PASS", "component JS", "Edit", f"{MOD}/static/src/components/grid/grid.js", "s7", "", {}),
    ("PASS", "skill already loaded", "Edit", f"{MOD}/views/hr_appraisal_views.xml", "s8", SKILL_CALL, {}),
    ("PASS", "slash command already run", "Edit", f"{MOD}/views/hr_appraisal_views.xml", "s9",
     "<command-name>/ui-design</command-name>\n", {}),
    ("PASS", "read is not an edit", "Read", f"{MOD}/views/hr_appraisal_views.xml", "s10", "", {}),
    ("PASS", "disabled by env", "Edit", f"{MOD}/views/hr_appraisal_views.xml", "s11", "",
     {"UIDESIGN_NUDGE": "0"}),
]


def verdict(tool, path, session, transcript_text, env, tmpdir):
    transcript = os.path.join(tmpdir, f"{session}.jsonl")
    with open(transcript, "a", encoding="utf-8") as fh:
        fh.write(transcript_text)
    proc = subprocess.run(
        [sys.executable, HOOK],
        input=json.dumps({
            "session_id": f"UICORPUS-{session}",
            "transcript_path": transcript,
            "tool_name": tool,
            "tool_input": {"file_path": path},
        }),
        capture_output=True, text=True, env={**os.environ, **env})
    return proc.returncode


def main():
    sessions = {case[4] for case in CASES}
    for session in sessions:
        try:
            os.remove(f"/tmp/.uidesign-nudge-UICORPUS-{session}")
        except FileNotFoundError:
            pass
    failures = 0
    with tempfile.TemporaryDirectory() as tmpdir:
        for want, label, tool, path, session, transcript_text, env in CASES:
            got = "NUDGE" if verdict(tool, path, session, transcript_text, env, tmpdir) == 2 else "PASS"
            ok = got == want
            failures += not ok
            print(f"{'ok  ' if ok else 'FAIL'} want={want:5s} got={got:5s}  {label}")
    for session in sessions:
        try:
            os.remove(f"/tmp/.uidesign-nudge-UICORPUS-{session}")
        except FileNotFoundError:
            pass
    print(f"\n{len(CASES) - failures}/{len(CASES)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
