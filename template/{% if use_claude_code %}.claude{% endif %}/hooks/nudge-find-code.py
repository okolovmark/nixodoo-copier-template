#!/usr/bin/env python3
"""PreToolUse nudge, once per session: a recursive grep for a SYMBOL is a
find-code question. Exit 2 bounces the call with a hint (Claude sees stderr
and either switches to lsp.py or re-runs the grep, which then passes).

Interference budget: at most ONE bounced call per session; single-file greps,
string greps and every later call pass untouched. FINDCODE_NUDGE=0 disables.
"""

import json
import os
import re
import sys

if os.environ.get("FINDCODE_NUDGE") == "0":
    sys.exit(0)

d = json.load(sys.stdin)
marker = "/tmp/.findcode-nudge-" + re.sub(r"[^A-Za-z0-9-]", "", str(d.get("session_id") or "nosid"))
if os.path.exists(marker):
    sys.exit(0)

tool = d.get("tool_name", "")
ti = d.get("tool_input") or {}

BARE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")           # one identifier / model name
DEFN_HUNT = re.compile(r"_inherit|_name\s*=|\bdef\s+\w+")       # definition hunting


def symbol_shaped(pat):
    pat = (pat or "").strip()
    return bool(BARE_IDENT.match(pat) or DEFN_HUNT.search(pat))


hit = False
if tool == "Grep":
    path = str(ti.get("path") or "")
    if not re.search(r"\.\w+$", path) and symbol_shaped(ti.get("pattern")):  # dir scope = recursive
        hit = True
elif tool == "Bash":
    cmd = ti.get("command") or ""
    for m in re.finditer(r"\b(grep|rg)\s+([^|;&]*)", cmd):
        prog, rest = m.group(1), m.group(2)
        recursive = prog == "rg" or re.search(r"(^|\s)-[a-zA-Z]*[rR]|(^|\s)--recursive\b", rest)
        if not recursive:
            continue
        qm = re.search(r"'([^']*)'|\"([^\"]*)\"", rest)
        if qm:
            pat = qm.group(1) if qm.group(1) is not None else qm.group(2)
        else:
            toks = [t for t in rest.split() if not t.startswith("-")]
            pat = toks[0] if toks else ""
        if symbol_shaped(pat):
            hit = True
            break

if not hit:
    sys.exit(0)

open(marker, "w").close()
sys.stderr.write(
    "find-code nudge (once per session, not a block): a recursive grep for a symbol "
    "(definition, usages, _inherit chain, xml id) is answered faster and MRO-aware by the "
    "find-code skill: python3 .claude/skills/find-code/lsp.py def|refs|sym|model ... "
    "If this grep is really about plain text, just re-run it — it will pass now.\n")
sys.exit(2)
