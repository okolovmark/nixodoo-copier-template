#!/usr/bin/env python3
"""Results in files that no longer exist are skipped, never a traceback.

The daemon is shared by every session of a project. A session that opens a file
of its own worktree leaves that file in the server's index after the worktree is
removed, and `who` then crashed opening it. The location helpers must drop such
results and say so, and resolving a position in a missing file must exit with a
message, not a FileNotFoundError.

Usage: python3 .claude/skills/find-code/tests/test_missing_files.py
"""

import contextlib
import io
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import lsp  # noqa: E402

failures = []


def check(what, cond):
    print(f"  {'ok  ' if cond else 'FAIL'} {what}")
    if not cond:
        failures.append(what)


def location(path, line_0):
    return {"uri": "file://" + str(path), "range": {"start": {"line": line_0, "character": 0}}}


def run(fn, *args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        value = fn(*args)
    return value, out.getvalue(), err.getvalue()


with tempfile.TemporaryDirectory() as tmp:
    alive = Path(tmp) / "alive.py"
    alive.write_text("x = 1\n")
    gone = Path(tmp) / "removed-worktree" / "gone.py"

    check("an existing file is alive", lsp.loc_alive(f"{alive}:1"))
    check("a missing file is not alive", not lsp.loc_alive(f"{gone}:1"))

    n, out, err = run(lsp.print_locs, [location(alive, 0), location(gone, 4)], "none")
    check("print_locs prints only the existing file", out.split() == [f"{lsp.os.path.relpath(alive, lsp.ROOT)}:1"])
    check("print_locs counts the result it printed", n == 1)
    check("print_locs names the skipped result", "1 result(s) in files that no longer exist" in err)

    n, out, err = run(lsp.print_locs, [location(gone, 0)], "no definition found")
    check("only missing files read as nothing found", n == 0 and out.strip() == "no definition found")
    check("and still say why", "no longer exist" in err)

    n, out, err = run(lsp.print_locs, None, "no references found")
    check("an empty answer is unchanged", n == 0 and out.strip() == "no references found" and err == "")

    try:
        lsp.resolve_pos(str(gone), 1, "x")
        check("resolve_pos refuses a missing file", False)
    except SystemExit as exc:
        check("resolve_pos exits with a message for a missing file", "no longer exists" in str(exc))
    except FileNotFoundError:
        check("resolve_pos exits with a message for a missing file (got FileNotFoundError)", False)

    check("resolve_pos still works on an existing file", lsp.resolve_pos(str(alive), 1, "x") == 0)

print("PASS" if not failures else f"{len(failures)} FAILED")
sys.exit(1 if failures else 0)
