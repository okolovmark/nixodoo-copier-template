#!/usr/bin/env python3
"""PreToolUse nudge: the first edit of a view arch, an OWL template or a
stylesheet in a session loads the ui-design skill first. Exit 2 bounces that
one edit with the hint; the retried edit passes, and so does every later one.

Quiet when the transcript already shows the skill loaded (a Skill call or the
/ui-design command). The dev and review agents preload it, so a bounce there
costs one retry. UIDESIGN_NUDGE=0 disables.
"""

import json
import os
import re
import sys

if os.environ.get("UIDESIGN_NUDGE") == "0":
    sys.exit(0)

d = json.load(sys.stdin)
if d.get("tool_name") not in ("Edit", "Write", "MultiEdit"):
    sys.exit(0)

path = (d.get("tool_input") or {}).get("file_path") or ""
VIEW_FILE = re.compile(r"/(?:views|wizards?)/[^/]+\.xml$|/static/src/.+\.(?:xml|scss|css)$")
if not VIEW_FILE.search(path):
    sys.exit(0)

sid = re.sub(r"[^A-Za-z0-9-]", "", str(d.get("session_id") or "nosid"))
marker = "/tmp/.uidesign-nudge-" + sid
if os.path.exists(marker):
    sys.exit(0)
open(marker, "w").close()

LOADED = re.compile(r'"skill"\s*:\s*"ui-design"|<command-name>/?ui-design</command-name>')
transcript = d.get("transcript_path") or ""
try:
    with open(transcript, encoding="utf-8", errors="replace") as fh:
        if any(LOADED.search(line) for line in fh):
            sys.exit(0)
except OSError:
    pass

sys.stderr.write(
    f"ui-design: {os.path.basename(path)} is a view or a style. Load the `ui-design` "
    "skill first (Skill tool, skill=\"ui-design\"): built-in arch, widgets and "
    "Bootstrap utilities before any stylesheet, and the phone check (375px) before "
    "the view is done. Already loaded? Re-run the edit; this fires once per session.\n"
)
sys.exit(2)
