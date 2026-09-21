#!/usr/bin/env python3
"""The sweep must kill our strays and nothing else.

sweep_orphans() picks processes to SIGTERM/SIGKILL by reading command lines out
of /proc, so the discriminator is the dangerous part of it: an editor runs the
same odoo_ls_server binary out of the same ~/.local/share/odoo-ls, and killing
its server would take the user's editor support down with it. Three fakes stand
in for the three cases - our stray, our live one, somebody else's.

Usage: python3 .claude/skills/find-code/tests/test_sweep_orphans.py
"""

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import lsp  # noqa: E402

failures = []


def check(what, cond):
    print(f"  {'ok  ' if cond else 'FAIL'} {what}")
    if not cond:
        failures.append(what)


def a_dead_pid():
    """A pid that has certainly exited (reaped, so it is nobody's)."""
    done = subprocess.Popen(["true"])
    done.wait()
    return done.pid


def fake_server(bindir, logs_dir, client_pid):
    """An odoo_ls_server-shaped process that only sleeps.

    It is bash under our own name, because the sweep reads /proc: a shebang
    script shows up there as its interpreter and python rewrites its argv[0] to
    the real binary, so both would be invisible to the matcher and the test
    would pass while finding nothing. Both were tried first. The command has to
    be compound, or bash execs sleep and drops the name too.
    """
    binary = bindir / "odoo_ls_server"
    if not binary.exists():
        binary.symlink_to(shutil.which("bash"))
    # Its own session, so the sleep it spawns can be cleaned up with it: killing
    # the bash alone leaves the sleep behind for five minutes.
    return subprocess.Popen(
        start_new_session=True,
        args=[str(binary), "-c", "sleep 300; :", "fake",
         "--config-path", "/nowhere/odools.toml",
         "--selected-config", "test", "--log-level", "warn",
         "--logs-directory", str(logs_dir),
         "--client-process-id", str(client_pid)])


def main():
    tmp = Path(tempfile.mkdtemp())
    bindir = tmp / "bin"
    bindir.mkdir()
    elsewhere = tmp / "editor-logs"
    elsewhere.mkdir()

    stray = fake_server(bindir, lsp.RUNTIME_DIR, a_dead_pid())
    live = fake_server(bindir, lsp.RUNTIME_DIR, os.getpid())
    editor = fake_server(bindir, elsewhere, a_dead_pid())
    time.sleep(0.5)

    try:
        found = lsp.find_orphans()
        check("the stray is found", stray.pid in found)
        check("a server whose daemon is alive is left alone", live.pid not in found)
        check("a server logging elsewhere is left alone", editor.pid not in found)

        killed = lsp.sweep_orphans()
        check("the stray is reported as swept", stray.pid in killed)
        time.sleep(0.5)
        check("the stray is gone", stray.poll() is not None)
        check("the live one survived", live.poll() is None)
        check("the editor's survived", editor.poll() is None)

        check("nothing is left to find", not lsp.find_orphans())
    finally:
        for proc in (stray, live, editor):
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            proc.wait()

    print("PASS" if not failures else f"FAIL ({len(failures)})")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
