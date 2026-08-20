#!/usr/bin/env python3
"""PreToolUse nudge: a recursive grep for a SYMBOL is a find-code question.
Exit 2 bounces the call with a hint (Claude sees stderr and either switches to
lsp.py or re-runs the grep, which then passes).

Interference budget: at most MAX_NUDGES bounced calls per session and at most
one per distinct pattern, so a single false positive costs one slot instead of
disarming the hook for the rest of the session. FINDCODE_NUDGE=0 disables.

Parsing rules, each one a v0.17.1 misfire:
- split with shlex, never a `[^|;&]*` regex — `\\|` inside a grep pattern is BRE
  alternation, not a pipe, and cutting the command there left an unbalanced
  quote whose inner `'word'` then read as a bare identifier (false positive
  that burned the whole session budget);
- test symbol shape per ALTERNATIVE of `a\\|b\\|c`, and treat a trailing `(` as
  part of the shape — `grep -rn "_method(" src/` (who calls X) is the most
  common real case and used to pass untouched;
- a lone lowercase word ('warehouse', 'tests') is NOT a symbol: an identifier
  must carry `_`, `.` or a call paren, or hunt a definition.

Two more, from a session that spent the whole budget and then got a wrong answer:
- the marker was `sha1(raw pattern)`, so `_name = "hr.employee.leave`,
  `hr.employee.leave\\b` and `hr.employee.leave` each took a slot and ONE
  question exhausted the session in four minutes; every symbol grep after that
  passed unnudged. The key is now the normalised identifier (`dedup_key`);
- a symbol grep piped into `head`/`tail` answers a completeness question with a
  truncated answer, and that is where a silent miss becomes a wrong conclusion
  (`grep -rn leave_rendered ... | head -20` dropped the two references that
  mattered). Those bypass the budget: worth an interruption every time.
"""

import hashlib
import json
import os
import re
import shlex
import sys

MAX_NUDGES = 6

if os.environ.get("FINDCODE_NUDGE") == "0":
    sys.exit(0)

d = json.load(sys.stdin)
tool = d.get("tool_name", "")
ti = d.get("tool_input") or {}

GREPS = {"grep", "egrep", "fgrep", "rg", "ggrep"}
# A who-uses-X search cut short by one of these is a lower bound presented as an
# answer; nudging those is worth a slot every time.
TRUNCATORS = {"head", "tail"}
OPERATORS = {"|", "||", "&&", ";", "&", ">", ">>", "<", "|&"}
# flags whose VALUE is the next token, so that token is never the pattern
VALUE_FLAGS = {"-e", "-f", "-m", "-A", "-B", "-C", "--include", "--exclude", "-g", "-t", "--max-count"}

IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*\(?$")
CORE = re.compile(r"[A-Za-z_][A-Za-z0-9_.]{2,}")
DEFN_HUNT = re.compile(r"_inherit\b|_name\s*=|\b(?:def|class)\s+\w")
NOISE = re.compile(r"^[-<>\[\]^$.*?+|\\()\s]*$")


def _alternatives(pat):
    """BRE `\\|` and ERE `|` both split alternatives; strip anchors/word marks."""
    for alt in re.split(r"\\\||\|", pat or ""):
        yield alt.strip().lstrip("^").rstrip("$").replace("\\b", "").replace("\\s*", "")


def symbol_shaped(pat):
    pat = (pat or "").strip()
    if not pat or NOISE.match(pat) or "/" in pat:
        return False
    if DEFN_HUNT.search(pat):
        return True
    for alt in _alternatives(pat):
        if IDENT.match(alt) and (alt.endswith("(") or "_" in alt or "." in alt):
            return True
    return False


def dedup_key(pat):
    """One key per QUESTION, not per spelling.

    `_name = "hr.employee.leave`, `hr.employee.leave\\b` and `hr.employee.leave`
    are the same lookup; hashing the raw pattern let one question eat the whole
    session budget. Prefer the first identifier-shaped alternative, else the
    longest identifier anywhere in the pattern (`_name = "x.y"` -> `x.y`).
    """
    for alt in _alternatives(pat):
        if IDENT.match(alt):
            return alt.rstrip("(").lower()
    found = CORE.findall(pat or "")
    if found:
        return max(found, key=len).lower()
    return (pat or "").strip().lower()


def invocations(tokens):
    """(program, args) per grep-family call on a shell command line."""
    current = None
    for tok in tokens:
        if tok in OPERATORS:
            if current:
                yield current
            current = None
            continue
        base = os.path.basename(tok)
        if base in GREPS:
            if current:
                yield current
            current = (base, [])
            continue
        if current:
            current[1].append(tok)
    if current:
        yield current


def pattern_of(prog, args):
    """(pattern, recursive) for one grep invocation."""
    recursive = prog == "rg"
    pattern = None
    positionals = []
    skip_next = False
    for index, arg in enumerate(args):
        if skip_next:
            skip_next = False
            continue
        if arg.startswith("-") and len(arg) > 1:
            head = arg.split("=", 1)[0]
            if head in VALUE_FLAGS and "=" not in arg:
                if head in ("-e", "-f") and index + 1 < len(args):
                    pattern = args[index + 1]
                skip_next = True
                continue
            if not head.startswith("--") and re.search(r"[rR]", arg[1:]):
                recursive = True
            continue
        positionals.append(arg)
    if pattern is None and positionals:
        pattern = positionals.pop(0)
    # A directory target (or rg's implicit cwd) means the whole tree.
    if not recursive:
        recursive = any(
            path.endswith("/") or (os.path.isdir(path) and not re.search(r"\.\w+$", path))
            for path in positionals
        )
    return pattern, recursive


hits = []
truncated = False
if tool == "Grep":
    path = str(ti.get("path") or "")
    if not re.search(r"\.\w+$", path) and symbol_shaped(ti.get("pattern")):
        hits.append(str(ti.get("pattern")))
    truncated = bool(ti.get("head_limit"))
elif tool == "Bash":
    try:
        tokens = shlex.split(ti.get("command") or "", posix=True)
    except ValueError:  # unbalanced quotes: nothing reliable to inspect
        sys.exit(0)
    truncated = "|" in tokens and any(os.path.basename(tok) in TRUNCATORS for tok in tokens)
    for prog, args in invocations(tokens):
        pattern, recursive = pattern_of(prog, args)
        if recursive and symbol_shaped(pattern):
            hits.append(pattern)

if not hits:
    sys.exit(0)

pattern = hits[0]
sid = re.sub(r"[^A-Za-z0-9-]", "", str(d.get("session_id") or "nosid"))
marker_dir = "/tmp/.findcode-nudge-" + sid
os.makedirs(marker_dir, exist_ok=True)
marker = os.path.join(marker_dir, hashlib.sha1(dedup_key(pattern).encode()).hexdigest()[:16])
# The per-question marker always wins, so the documented "just re-run it" escape
# hatch keeps working. The session budget does not apply to a truncated search:
# that one is answered wrong, not merely answered the slow way.
if os.path.exists(marker):
    sys.exit(0)
if len(os.listdir(marker_dir)) >= MAX_NUDGES and not truncated:
    sys.exit(0)
open(marker, "w").close()

symbol = next((alt for alt in _alternatives(pattern) if IDENT.match(alt)), pattern).rstrip("(")
sys.stderr.write(
    "find-code nudge (not a block; this question is never nudged twice): '{sym}' is a symbol, "
    "so who-defines / who-calls / who-overrides it is answered MRO-aware and over the WHOLE "
    "tree (core + third-party + custom) by the find-code skill — a hand-scoped grep silently "
    "misses the repos you did not list:\n"
    "  python3 .claude/skills/find-code/lsp.py sym {sym}\n"
    "  python3 .claude/skills/find-code/lsp.py refs <file> <def-line> {sym}\n"
    "If this grep is really about plain text, just re-run it — it will pass now.\n".format(sym=symbol)
)
if truncated:
    sys.stderr.write(
        "This one also pipes into head/tail: a 'who uses {sym}' answer cut to the first N lines "
        "is a lower bound. Drop the truncation or ask lsp.py, and never conclude 'nothing else "
        "uses it' from a truncated search.\n".format(sym=symbol)
    )
sys.exit(2)
