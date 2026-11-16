#!/usr/bin/env python3
"""Regression corpus for the kb SessionStart hook (kb-session-start.sh).

Renders the hook with jinja2 and runs it with a stub `kb` on a PATH of its own, so the
real kb and its database are never touched. Covers what the hook must do with a current
kb and with an older one that predates --title, schema_stale or anything newer: every
session gets the pinned rules and a standup count, under the 10,000 characters Claude Code
keeps of a hook's output however busy kb is; notices (kb add --once) live in the kb pane, so
the hook never asks kb for them and never names them; a compacted session is told to write
its session note first, a stale schema says so, a missing kb or an unregistered checkout gets
one line naming `nix run .#kb-setup`, and no kb error ever reaches the context.

Run from the repo root: python3 tests/test_kb_session_start.py (needs jinja2, bash,
python3, awk, sed, grep).
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import jinja2

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / (
    "template/{% if use_agent_layer %}.claude{% endif %}/hooks/"
    "{% if use_kb %}kb-session-start.sh{% endif %}.jinja"
)
SCOPE = "acme-odoo-16"
# Claude Code keeps 10,000 characters of a hook's output in context, and past that the
# first 2,000 only: what the model must act on comes first, and the whole stays under the cap.
CONTEXT_CAP = 10_000
PREVIEW = 2_000

PINNED = [f"- [{i}] feedback_rule_{i} — " + "a pinned rule the code follows " * 5 for i in range(30)]
RECENT = [f"- [{i}] note_{i} — " + "a recent record nobody pinned " * 5 for i in range(100, 220)]
INDEX = "\n".join(
    [f"# kb index 2026-10-22 ({SCOPE}) — generated, do not edit", "", "## Pinned", *PINNED, "", "## Recent", *RECENT]
)
INDEX_NO_PINS = "\n".join([f"# kb index 2026-10-22 ({SCOPE}) — generated", "", "## Recent", *RECENT[:3]])
TASKS = [{"id": i, "level": 3 if i < 4 else 2, "title": f"task {i}"} for i in range(9)]
STATUS_OK = {"bolt_reachable": True, "schema_ok": True, "upgrade_pending": False}

COMPACT_LINE = "kb: the context was just compacted"
SCHEMA_LINE = "kb: schema stale or incomplete; run kb-setup"
INSTALL_LINE = "kb: kb is not installed, so this session has no project memory. To set it up: nix run .#kb-setup"
REGISTER_LINE = "kb: this checkout is not registered as the kb scope"
STANDUP_LINE = "kb today: 9 on the standup, 4 loud, 5 others; the list is left out here: kb today prints it, kb next --all every task."
RECENT_LINE = "120 recent records are left out here: kb index lists them, kb search finds any record."
LEFT_OUT = "pinned rules left out to fit the context: kb index lists them"
CUT_LINE = "kb: the session start was cut here to fit the context; kb index and kb today print the rest"

# A busy kb: twice the pinned rules a 10,000-character context holds, a long standup, a sync
# that names many possible duplicates, sessions without summary, and chatty local extras.
BUSY_PINNED = [f"- [{i}] feedback_busy_rule_{i:03} — " + "a rule the model must follow, said at length " * 5 for i in range(90)]
BUSY_INDEX = "\n".join(
    [f"# kb index 2026-10-22 ({SCOPE}) — generated, do not edit", "", "## Pinned", *BUSY_PINNED, "", "## Recent", *RECENT]
)
# The same busy kb with CRITICAL rules among the last pinned ones, where the trim starts: kb
# index lists critical pins first today, but the hook must not count on it.
CRITICAL_AT = (3, 60, 87, 88, 89)
CRITICAL_PINNED = [rule.replace("] feedback_", "] CRITICAL feedback_") if i in CRITICAL_AT else rule
                   for i, rule in enumerate(BUSY_PINNED)]
CRITICAL_INDEX = "\n".join(
    [f"# kb index 2026-10-22 ({SCOPE}) — generated, do not edit", "", "## Pinned", *CRITICAL_PINNED, "", "## Recent", *RECENT]
)
BUSY_TASKS = [{"id": i, "level": 4 if i < 5 else 3, "title": f"busy task {i} " * 8} for i in range(60)]
BUSY_SYNC = "\n".join(
    f"possible duplicate: [{i}] a new ticket title ~ [{i + 1}] an old thread title: kb promote {i + 1} PROJ-{i}, then kb merge {i} {i + 1}"
    for i in range(12)
)
NO_SUMMARY = [{"id": i, "slug": f"2026-10-2{i}-1", "session_id": f"sid-{i}", "closed_at": None} for i in range(5)]

# The stub: answers from the KB_STUB_* environment and logs every call, one JSON argv a line.
# It answers `kb --json notices` as a current kb does, so a hook that asked would get a count.
STUB = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["KB_STUB_LOG"], "a") as log:
    log.write(json.dumps(args) + "\n")
env = os.environ.get
if args[:2] == ["--json", "status"]:
    print(env("KB_STUB_STATUS", "{}"))
    sys.exit(0 if '"schema_ok": true' in env("KB_STUB_STATUS", "") else 1)
if args[:2] == ["scope", "which"]:
    mode = env("KB_STUB_SCOPE", "ok")
    if mode == "old":
        print("Usage: kb [OPTIONS] COMMAND [ARGS]...\n\nError: No such command 'scope'.", file=sys.stderr)
        sys.exit(2)
    if mode == "none":
        print(f"{args[2]} is in no scope", file=sys.stderr)
        sys.exit(3)
    print("someone-else" if mode == "other" else "acme-odoo-16")
    sys.exit(0)
if args[:2] == ["service", "start"]:
    sys.exit(1 if env("KB_STUB_START_FAILS") else 0)
if args[:2] == ["sync", "odoo"]:
    print(env("KB_STUB_SYNC", "odoo: 9 open tasks, 0 new, 0 updated, 0 closed"))
    sys.exit(0)
if args[:2] == ["--json", "notices"]:
    if env("KB_STUB_OLD"):
        print("Usage: kb [OPTIONS] COMMAND [ARGS]...\nTry 'kb --help' for help.\n\nError: No such command 'notices'.", file=sys.stderr)
        sys.exit(2)
    count = int(env("KB_STUB_NOTICES", "0"))
    print(json.dumps({"scope": "acme-odoo-16", "count": count, "ids": list(range(count))}))
    sys.exit(0)
if args[:2] == ["session", "open"]:
    if any(a.startswith("--title") for a in args) and env("KB_STUB_OLD"):
        print("Usage: kb session open [OPTIONS]\nTry 'kb session open --help' for help.\n\nError: No such option '--title'.", file=sys.stderr)
        sys.exit(2)
    print("[42] 2026-10-22-1")
    sys.exit(0)
if args[:1] == ["index"]:
    sys.stdout.write(open(env("KB_STUB_INDEX")).read() + "\n")
    sys.exit(0)
if args[:1] == ["today"] and "--json" in args:
    if env("KB_STUB_TODAY_ERROR"):
        print(json.dumps({"error": "neo4j went away", "tasks": []}))
    else:
        today = {"date": "2026-10-22", "scope": "x", "tasks": json.loads(env("KB_STUB_TASKS")),
                 "quiet": 2, "others": 5, "no_summary": json.loads(env("KB_STUB_NO_SUMMARY", "[]"))}
        if env("KB_STUB_NO_OTHERS"):
            del today["others"]
        print(json.dumps(today))
    sys.exit(0)
print(f"kb: no such command {args!r}", file=sys.stderr)
sys.exit(2)
'''


def render():
    env = jinja2.Environment(trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=True)
    return env.from_string(SRC.read_text()).render(project_name=SCOPE)


class Rig:
    """A temp dir holding the rendered hook, a tools-only bin dir and a stub-kb bin dir."""

    def __init__(self):
        self.dir = Path(tempfile.mkdtemp(prefix="kb-hook-"))
        self.hook = self.dir / "hooks" / "kb-session-start.sh"
        self.hook.parent.mkdir()
        self.hook.write_text(render())
        self.tools = self.dir / "tools"
        self.tools.mkdir()
        for tool in ("bash", "python3", "awk", "sed", "grep", "cat", "dirname", "env", "seq"):
            found = shutil.which(tool)
            if found is None:
                sys.exit(f"missing tool: {tool}")
            (self.tools / tool).symlink_to(found)
        self.stub = self.dir / "stub"
        self.stub.mkdir()
        kb = self.stub / "kb"
        kb.write_text(STUB)
        kb.chmod(0o755)
        (self.dir / "index.txt").write_text(INDEX)
        (self.dir / "index-no-pins.txt").write_text(INDEX_NO_PINS)
        (self.dir / "index-busy.txt").write_text(BUSY_INDEX)
        (self.dir / "index-critical.txt").write_text(CRITICAL_INDEX)

    def run(self, payload, *, with_kb=True, index="index.txt", status=STATUS_OK, **stub_env):
        log = self.dir / "calls.log"
        log.write_text("")
        env = {
            "PATH": f"{self.stub}:{self.tools}" if with_kb else str(self.tools),
            "HOME": str(self.dir),
            "KB_STUB_LOG": str(log),
            "KB_STUB_INDEX": str(self.dir / index),
            "KB_STUB_STATUS": status if isinstance(status, str) else json.dumps(status),
            "KB_STUB_TASKS": json.dumps(TASKS),
            **stub_env,
        }
        data = payload if isinstance(payload, str) else json.dumps(payload)
        proc = subprocess.run(
            [str(self.tools / "bash"), str(self.hook)],
            input=data, capture_output=True, text=True, env=env, timeout=60,
        )
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        return proc, calls


def opens(calls):
    return [c for c in calls if c[:2] == ["session", "open"]]


def no_notices(out, calls):
    """Notices live in the kb pane: kb is never asked for them and the output never names them."""
    return "notice" not in out.lower() and not any("notices" in c for c in calls)


def payload(source="startup", title=None):
    d = {"session_id": "sess-1", "source": source, "hook_event_name": "SessionStart"}
    if title is not None:
        d["session_title"] = title
    return d


fails = 0


def check(name, ok, detail=""):
    global fails
    if not ok:
        fails += 1
        print(f"FAIL  {name}" + (f"\n      {detail}" if detail else ""))


def main():
    rig = Rig()

    # a plain session: the pinned rules, the recent records as a count, a standup count
    proc, calls = rig.run(payload())
    out = proc.stdout
    check("plain: exit 0", proc.returncode == 0, proc.stderr)
    check("plain: index header", out.splitlines()[2].startswith("# kb index"), out[:300])
    check("plain: every pinned rule", "## Pinned" in out and all(rule in out for rule in PINNED), out[:300])
    check("plain: no recent records, their count", "## Recent" not in out and RECENT[0] not in out
          and RECENT_LINE in out, out[-600:])
    check("plain: the standup count", STANDUP_LINE in out, out[-400:])
    check("plain: no standup list", "task 3" not in out)
    check("plain: nothing recorded as shown", not any("--record-shown" in c for c in calls), calls)
    check("plain: no title, no --title", all(not any(a.startswith("--title") for a in c) for c in opens(calls)), calls)
    check("plain: no compaction note", COMPACT_LINE not in out)
    check("plain: no schema line", SCHEMA_LINE not in out)
    check("plain: no notices line, kb notices never called", no_notices(out, calls), calls)
    check("plain: nothing left out", LEFT_OUT not in out and CUT_LINE not in out)
    check(f"plain: under {CONTEXT_CAP} characters", len(out) < CONTEXT_CAP, f"{len(out)} characters")
    check("plain: nothing on stderr", proc.stderr == "", proc.stderr)
    check("rig: the stub index alone is over the cap, so cutting it means something",
          len(INDEX) > CONTEXT_CAP, len(INDEX))

    # a titled session: the title is stored; a "[child] " title changes nothing else
    proc, calls = rig.run(payload(title="Fix the  label\nprinter"))
    check("titled: title passed, whitespace folded",
          opens(calls) == [["session", "open", "--scope", SCOPE, "--id", "sess-1", "--source", "startup",
                            "--title=Fix the label printer"]], calls)
    check("titled: the same output", proc.stdout == out, proc.stdout[:300])
    proc, calls = rig.run(payload(title="[child] ACME-12 label printer"))
    check("child: title stored", any("--title=[child] ACME-12 label printer" in c for c in opens(calls)), calls)
    check("child: the same output as any session", proc.stdout == out, proc.stdout[:300])

    # notices waiting: they live in the kb pane, so no line, kb notices never called, the same output
    for count in (1, 3):
        proc, calls = rig.run(payload(), KB_STUB_NOTICES=str(count))
        check(f"notices {count}: no notices line, kb notices never called", no_notices(proc.stdout, calls), calls)
        check(f"notices {count}: the same output as without", proc.stdout == out, proc.stdout[:500])

    # sessions closed without a summary: one line naming kb session close
    proc, _ = rig.run(payload(), KB_STUB_NO_SUMMARY=json.dumps(NO_SUMMARY[:1]))
    check("one session without summary: its id", "kb: 1 session without summary: 2026-10-20-1; write them: "
          "kb session close --id sid-0 --body-file <f>" in proc.stdout, proc.stdout[-400:])
    proc, _ = rig.run(payload(), KB_STUB_NO_SUMMARY=json.dumps(NO_SUMMARY))
    check("five sessions without summary: three named", "kb: 5 sessions without summary: 2026-10-20-1, 2026-10-21-1, "
          "2026-10-22-1 and 2 more; write them: kb session close --id <session_id>" in proc.stdout, proc.stdout[-400:])

    # a kb from before `others`: its whisper-only `quiet` stands in
    proc, _ = rig.run(payload(), KB_STUB_NO_OTHERS="1")
    check("no others: quiet stands in", "kb today: 9 on the standup, 4 loud, 2 others;" in proc.stdout,
          proc.stdout[-400:])

    # a kb that cannot answer the standup gives no count line, and no error
    proc, _ = rig.run(payload(), KB_STUB_TODAY_ERROR="1")
    check("today error: no count line", "kb today:" not in proc.stdout, proc.stdout[-300:])
    check("today error: index still printed", "## Pinned" in proc.stdout)

    # a kb that prints no Pinned section: the header and the recent count
    proc, _ = rig.run(payload(), index="index-no-pins.txt")
    check("no pins: header and the recent count",
          "# kb index" in proc.stdout and "3 recent records are left out here" in proc.stdout
          and RECENT[2] not in proc.stdout, proc.stdout[:400])

    # a busy kb: the last pinned rules give way, everything else stays, under the cap
    local = rig.hook.with_name("kb-session-start.local.sh")
    local.write_text('#!/usr/bin/env bash\necho "local: $1 $2"\n')
    local.chmod(0o755)
    proc, calls = rig.run(payload(source="compact", title="[child] ACME-12"), index="index-busy.txt",
                          KB_STUB_TASKS=json.dumps(BUSY_TASKS), KB_STUB_SYNC=BUSY_SYNC, KB_STUB_NOTICES="2",
                          KB_STUB_NO_SUMMARY=json.dumps(NO_SUMMARY), KB_STUB_SCOPE="none",
                          KB_STUB_STATUS=json.dumps({**STATUS_OK, "schema_stale": ["record_text"]}))
    out = proc.stdout
    kept = [rule for rule in BUSY_PINNED if rule in out]
    check("busy: exit 0, nothing on stderr", proc.returncode == 0 and proc.stderr == "", proc.stderr)
    check(f"busy: under {CONTEXT_CAP} characters", len(out) < CONTEXT_CAP, f"{len(out)} characters")
    check(f"busy: under {CONTEXT_CAP} UTF-16 units", len(out.encode("utf-16-le")) // 2 < CONTEXT_CAP)
    check("rig: the busy output is far over the cap untrimmed", len(BUSY_INDEX) > 2 * CONTEXT_CAP, len(BUSY_INDEX))
    check("busy: the first pinned rules kept, in order", kept == BUSY_PINNED[:len(kept)] and len(kept) >= 20, len(kept))
    check("busy: the left-out line counts the rest",
          f"+{len(BUSY_PINNED) - len(kept)} {LEFT_OUT}" in out, out[-1500:])
    check("busy: compaction note first", out.splitlines()[0].startswith(COMPACT_LINE), out[:300])
    for name, needle in (("register line", REGISTER_LINE), ("schema line", SCHEMA_LINE)):
        check(f"busy: {name} inside the preview", 0 <= out.find(needle) < PREVIEW, out[:PREVIEW])
    check("busy: no notices line, kb notices never called", no_notices(out, calls), calls)
    check("busy: the standup count", "kb today: 60 on the standup, 60 loud, 5 others" in out, out[-600:])
    check("busy: the sessions line", "kb: 5 sessions without summary" in out, out[-600:])
    check("busy: the local extras", out.rstrip().endswith("local: compact sess-1"), out[-300:])
    check("busy: no cut", CUT_LINE not in out)

    # CRITICAL rules late in the pinned list: every one kept, the others give way from the end
    proc, calls = rig.run(payload(source="compact"), index="index-critical.txt",
                          KB_STUB_TASKS=json.dumps(BUSY_TASKS), KB_STUB_SYNC=BUSY_SYNC)
    out = proc.stdout
    critical = [CRITICAL_PINNED[i] for i in CRITICAL_AT]
    others = [rule for i, rule in enumerate(CRITICAL_PINNED) if i not in CRITICAL_AT]
    kept = [rule for rule in others if rule in out]
    check("critical: every CRITICAL rule kept", all(rule in out for rule in critical), out[-1500:])
    check("critical: the other rules give way from the end", kept == others[:len(kept)] and len(kept) < len(others),
          len(kept))
    check("critical: the left-out line counts them", f"+{len(others) - len(kept)} {LEFT_OUT}" in out, out[-1500:])
    check("critical: pinned order kept", [line for line in out.splitlines() if line.startswith("- [")]
          == [rule for rule in CRITICAL_PINNED if rule in out])
    check(f"critical: under {CONTEXT_CAP} characters", len(out) < CONTEXT_CAP, f"{len(out)} characters")

    # a sync that fills the budget before the index: the cut keeps the CRITICAL rules all the same
    flood = "\n".join(f"possible duplicate: [{i}] a long ticket title about a stock move ~ [{i + 1}] its older thread"
                      for i in range(160))
    proc, calls = rig.run(payload(), index="index-critical.txt", KB_STUB_SYNC=flood)
    out = proc.stdout
    check("critical cut: every CRITICAL rule kept", all(rule in out for rule in critical), out[-1500:])
    check("critical cut: ends with the cut line", out.rstrip().endswith(CUT_LINE), out[-300:])
    check(f"critical cut: under {CONTEXT_CAP} characters", len(out) < CONTEXT_CAP, f"{len(out)} characters")
    check("rig: the sync alone passes the budget", len(flood) > 9_500, len(flood))

    # local extras that print more than the whole budget: cut at a line, said so, still under the cap
    local.write_text('#!/usr/bin/env bash\nfor i in $(seq 400); do echo "local: a chatty extra line number $i"; done\n')
    proc, calls = rig.run(payload(source="compact"), index="index-busy.txt", KB_STUB_NOTICES="2")
    out = proc.stdout
    check(f"flood: under {CONTEXT_CAP} characters", len(out) < CONTEXT_CAP, f"{len(out)} characters")
    check("flood: ends with the cut line", out.rstrip().endswith(CUT_LINE), out[-300:])
    check("flood: what comes first is kept", out.splitlines()[0].startswith(COMPACT_LINE) and "## Pinned" in out)
    check("flood: no notices line, kb notices never called", no_notices(out, calls), calls)
    local.unlink()

    # an older kb: --title refused with a usage error; nothing leaks
    proc, calls = rig.run(payload(title="[child] ACME-12 label printer"), KB_STUB_OLD="1")
    out = proc.stdout
    check("old kb: retried without --title",
          [c for c in opens(calls) if not any(a.startswith("--title") for a in c)] != [], calls)
    check("old kb: session line printed once", out.count("kb: session [42]") == 1, out[:400])
    check("old kb: no usage error in context", "No such" not in out and "Usage:" not in out, out[:400])
    check("old kb: no notices line, kb notices never called", no_notices(out, calls), calls)
    check("old kb: pinned rules and the standup count", PINNED[-1] in out and STANDUP_LINE in out)

    # compaction: the note instruction comes first, inside the preview
    proc, _ = rig.run(payload(source="compact"))
    first = proc.stdout.splitlines()[0] if proc.stdout else ""
    check("compact: instruction first", first.startswith(COMPACT_LINE), first)
    check("compact: names kb session note", "kb session note" in first, first)
    check("compact: inside the preview", proc.stdout.find(COMPACT_LINE) < PREVIEW)
    for source in ("startup", "resume", "clear"):
        check(f"{source}: no compaction note", COMPACT_LINE not in rig.run(payload(source=source))[0].stdout)

    # the schema line: schema_ok false, a non-empty schema_stale; never for unknown or an old kb
    for name, status, expected in (
        ("schema_ok false", {**STATUS_OK, "schema_ok": False}, True),
        ("schema_stale named", {**STATUS_OK, "schema_stale": ["record_text"]}, True),
        ("schema_stale empty", {**STATUS_OK, "schema_stale": []}, False),
        ("old kb without schema_stale", STATUS_OK, False),
    ):
        proc, _ = rig.run(payload(), status=status)
        check(f"schema: {name}", (SCHEMA_LINE in proc.stdout) is expected, proc.stdout[:300])
        if expected:
            check(f"schema: {name}, inside the preview", proc.stdout.find(SCHEMA_LINE) < PREVIEW)

    # Neo4j down: started, the status read again (its schema then counts); or NO MEMORY, no schema line
    proc, calls = rig.run(payload(), status={"bolt_reachable": False, "schema_ok": None})
    check("down: service started", ["service", "start"] in calls, calls)
    check("down: status read again", calls.count(["--json", "status"]) == 2, calls)
    check("down: unknown schema is not stale", SCHEMA_LINE not in proc.stdout)
    proc, calls = rig.run(payload(), status={"bolt_reachable": False, "schema_ok": None}, KB_STUB_START_FAILS="1")
    check("down for good: NO MEMORY", "kb: NO MEMORY" in proc.stdout and proc.returncode == 0, proc.stdout)
    check("down for good: stops there", not opens(calls) and SCHEMA_LINE not in proc.stdout, calls)

    # a status that is no JSON at all: no crash, no false alarm
    proc, _ = rig.run(payload(), status="not json")
    check("garbled status: exit 0", proc.returncode == 0, proc.stderr)

    # unreadable input: the hook still starts the session and exits 0
    proc, _ = rig.run("{not json")
    check("bad input: exit 0", proc.returncode == 0, proc.stderr)
    check("bad input: index still printed", "## Pinned" in proc.stdout)

    # no kb on PATH: one line naming the fix, nothing else
    proc, _ = rig.run(payload(title="[child] ACME-12"), with_kb=False)
    check("no kb: exit 0", proc.returncode == 0, proc.stderr)
    check("no kb: one line naming nix run .#kb-setup", proc.stdout == INSTALL_LINE + "\n", proc.stdout)
    check("no kb: nothing on stderr", proc.stderr == "", proc.stderr)

    # registration: this checkout must be the kb scope the hook uses
    proc, calls = rig.run(payload())
    check("registered: asks kb scope which for the repo root",
          any(c[:2] == ["scope", "which"] and len(c) == 3 for c in calls), calls)
    check("registered: no registration line", REGISTER_LINE not in proc.stdout)
    for mode in ("none", "other"):
        proc, calls = rig.run(payload(), KB_STUB_SCOPE=mode)
        out = proc.stdout
        check(f"scope {mode}: the line", REGISTER_LINE in out and "nix run .#kb-setup" in out, out[:400])
        check(f"scope {mode}: inside the preview", out.find(REGISTER_LINE) < PREVIEW)
        check(f"scope {mode}: the session still opens", opens(calls) != [], calls)
        check(f"scope {mode}: nothing on stderr", proc.stderr == "", proc.stderr)
    proc, _ = rig.run(payload(), KB_STUB_SCOPE="old")
    check("kb without scope command: no line", REGISTER_LINE not in proc.stdout, proc.stdout[:300])
    check("kb without scope command: no usage error", "No such command" not in proc.stdout + proc.stderr)
    proc, _ = rig.run(payload(source="compact"), KB_STUB_SCOPE="none")
    check("compact and unregistered: the compaction note stays first",
          proc.stdout.splitlines()[0].startswith(COMPACT_LINE), proc.stdout[:300])

    # the local extras still run, with source and session id
    local = rig.hook.with_name("kb-session-start.local.sh")
    local.write_text('#!/usr/bin/env bash\necho "local: $1 $2"\n')
    local.chmod(0o755)
    proc, _ = rig.run(payload(source="resume", title="[child] ACME-12"))
    check("local extras: called", "local: resume sess-1" in proc.stdout, proc.stdout[-300:])
    local.unlink()

    shutil.rmtree(rig.dir, ignore_errors=True)
    print(f"{'FAIL' if fails else 'ok'}: kb-session-start corpus, {fails} failure(s)")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    os.chdir(ROOT)
    main()
