#!/usr/bin/env python3
"""Corpus for nix/scripts/setup-postgres.sh: the cluster's log stays capped, old clusters included.

Run from the repo root: python3 tests/test_setup_postgres.py

The log was meant to be capped by size rotation into the same file name, but PostgreSQL never
truncates a file whose name stays the same, so postgresql.log grew without bound (hundreds of GB).
The cap is now a timer that empties the file past a size, and the logging settings live in a file
setup-postgres rewrites on every run, so a cluster made before a change still gets it. Each case
renders the script and runs it in a scratch project with initdb, pg_ctl, psql and systemctl
stubbed; the live case runs a real cluster and skips when initdb is not on PATH. CI runs this file
once per push (the setup-postgres job).
"""

import os
import pathlib
import shlex
import shutil
import subprocess
import sys
import tempfile

import jinja2

SRC = pathlib.Path("template/nix/scripts/setup-postgres.sh.jinja")
ANSWERS = dict(postgres_port="5432", db_user="odoo", db_password="odoo", db_name="odoo",
               service_suffix="", nix_profile_rel=".nix-profile")
INCLUDE = "include_if_exists = 'logging.conf'"
# What a cluster made before this change carries at the end of postgresql.conf.
OLD_BLOCK = """\
log_destination = 'stderr'
logging_collector = on
log_directory = 'log'
log_filename = 'postgresql.log'
log_statement = 'all'
log_rotation_size = 100MB
log_rotation_age = 0
log_truncate_on_rotation = on
"""
STUBS = {
    "initdb": 'mkdir -p "$PGDATA" && echo "# initdb" > "$PGDATA/postgresql.conf"',
    "pg_ctl": 'echo "pg_ctl $*" >> "$STUB_LOG"; [ "$1" = status ] && exit "${STUB_PG_STATUS:-3}"; exit 0',
    "psql": 'echo "psql $*" >> "$STUB_LOG"',
    "systemctl": 'echo "systemctl $*" >> "$STUB_LOG"',
}
CASES = []


def case(fn):
    CASES.append(fn)
    return fn


def scratch():
    return tempfile.TemporaryDirectory(dir="/tmp" if os.path.isdir("/tmp") else None)


def stub_bin(root, names):
    bin_dir = root / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name in names:
        path = bin_dir / name
        path.write_text(f"#!/bin/sh\n{STUBS[name]}\n")
        path.chmod(0o755)
    return bin_dir


def run(root, stubs=tuple(STUBS), **env):
    script = root / "setup-postgres.sh"
    script.write_text(jinja2.Environment(keep_trailing_newline=True).from_string(SRC.read_text()).render(**ANSWERS))
    project = root / "project"
    project.mkdir(exist_ok=True)
    (root / "home").mkdir(exist_ok=True)
    full_env = {**os.environ, "HOME": str(root / "home"), "STUB_LOG": str(root / "calls.log"),
                "POSTGRES_BIN": "/stub/postgres", "USER": "dev",
                "PATH": f"{stub_bin(root, stubs)}:{os.environ['PATH']}", **env}
    proc = subprocess.run(["bash", str(script)], cwd=project, env=full_env, capture_output=True, text=True,
                          timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout


def calls(root):
    log = root / "calls.log"
    return log.read_text().splitlines() if log.exists() else []


def units(root):
    return root / "home/.config/systemd/user"


@case
def a_new_cluster_logs_through_logging_conf_and_gets_the_cap_timer():
    with scratch() as tmp:
        root = pathlib.Path(tmp)
        run(root)
        pgdata = root / "project/.postgres"
        conf = (pgdata / "postgresql.conf").read_text()
        assert conf.splitlines()[-1] == INCLUDE and "log_statement" not in conf, conf
        logging = (pgdata / "logging.conf").read_text()
        for line in ("log_filename = 'postgresql.log'", "log_statement = 'all'", "log_rotation_size = 0"):
            assert line in logging.splitlines(), logging
        assert (pgdata / "log").is_dir()
        assert (units(root) / "postgres-log-cap.timer").exists(), list(units(root).iterdir())
        assert "systemctl --user enable --now postgres-log-cap.timer" in calls(root), calls(root)


@case
def an_old_cluster_gets_the_include_once_after_its_own_logging_lines():
    with scratch() as tmp:
        root = pathlib.Path(tmp)
        pgdata = root / "project/.postgres"
        pgdata.mkdir(parents=True)
        (pgdata / "postgresql.conf").write_text("# initdb\n" + OLD_BLOCK)
        run(root)
        run(root)
        lines = (pgdata / "postgresql.conf").read_text().splitlines()
        assert lines.count(INCLUDE) == 1 and lines[-1] == INCLUDE, lines
        assert not any(c.startswith("pg_ctl reload") for c in calls(root)), calls(root)
        assert not any(c.startswith(("psql", "pg_ctl start")) for c in calls(root)), calls(root)


@case
def a_running_old_cluster_is_reloaded():
    with scratch() as tmp:
        root = pathlib.Path(tmp)
        pgdata = root / "project/.postgres"
        pgdata.mkdir(parents=True)
        (pgdata / "postgresql.conf").write_text(OLD_BLOCK)
        out = run(root, STUB_PG_STATUS="0")
        assert "pg_ctl reload" in calls(root) and "Reloaded" in out, (calls(root), out)


@case
def the_cap_empties_a_log_past_1g_and_leaves_a_smaller_one():
    with scratch() as tmp:
        root = pathlib.Path(tmp)
        run(root)
        unit = (units(root) / "postgres-log-cap.service").read_text()
        exec_start = next(line for line in unit.splitlines() if line.startswith("ExecStart="))
        argv = shlex.split(exec_start.removeprefix("ExecStart="))
        log = root / "project/.postgres/log/postgresql.log"
        for size, left in ((2**30, 2**30), (2**30 + 1, 0)):
            with open(log, "wb") as f:
                f.truncate(size)  # sparse: no disk used
            subprocess.run(argv, check=True, timeout=20)
            assert log.stat().st_size == left, (size, log.stat().st_size, argv)


@case
def live_an_old_clusters_settings_lose_to_logging_conf():
    if not shutil.which("initdb"):
        print("    skipped: initdb not on PATH")
        return
    with scratch() as tmp:
        root = pathlib.Path(tmp)
        pgdata = root / "project/.postgres"
        subprocess.run(["initdb", "-D", str(pgdata), "--auth=trust", "--no-locale", "-E", "UTF8"],
                       check=True, capture_output=True, timeout=120)
        with open(pgdata / "postgresql.conf", "a") as f:
            f.write(f"unix_socket_directories = '{pgdata}'\nlisten_addresses = ''\n{OLD_BLOCK}")
        run(root, stubs=("systemctl",))
        ctl = ["pg_ctl", "-D", str(pgdata), "-l", str(root / "start.log"), "-w"]
        subprocess.run([*ctl, "start"], check=True, capture_output=True, timeout=60)
        try:
            shown = subprocess.run(
                ["psql", "-h", str(pgdata), "-d", "postgres", "-At", "-c",
                 "select string_agg(setting, ',' order by name) from pg_settings where name in "
                 "('log_filename', 'log_rotation_size', 'log_statement')"],
                check=True, capture_output=True, text=True, timeout=30).stdout.strip()
        finally:
            subprocess.run([*ctl, "-m", "fast", "stop"], capture_output=True, timeout=60)
        assert shown == "postgresql.log,0,all", shown


if __name__ == "__main__":
    failed = 0
    for fn in CASES:
        try:
            fn()
            print(f"  ok   {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL {fn.__name__}: {exc}")
    print("PASS" if not failed else f"{failed} FAILED")
    sys.exit(1 if failed else 0)
