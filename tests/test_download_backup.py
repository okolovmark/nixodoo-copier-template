#!/usr/bin/env python3
"""Corpus for nix/scripts/download-backup.sh: --skip-download restores only the newest dump in backup/.

Run from the repo root: python3 tests/test_download_backup.py

The restore passed backup/*.dump to pg_restore, which takes one input file, so two dumps in
backup/ made it stop with "too many command-line arguments" and the run end with "restore
incomplete". A box that keeps older dumps in backup/ always has two. The restore now takes the
last match, which the dated names make the newest; a download still in progress as .dump.part
does not match, and with no dump left the script restores nothing. Each case renders the script
and runs it in a scratch project with psql, pg_restore, pg_isready, pg_ctl and odoo stubbed; the
stub pg_restore refuses more than one input file the way the real one does. CI runs this file
once per push (the download-backup job).
"""

import os
import pathlib
import subprocess
import sys
import tempfile

import jinja2

SRC = pathlib.Path("template/nix/scripts/{% if backup_s3_bucket %}download-backup.sh{% endif %}.jinja")
ANSWERS = dict(backup_s3_bucket="example-pgdump", service_suffix="")
# A remote db_host keeps the script off the project-local cluster (pg_ctl start).
ODOO_CONF = "db_host = 192.0.2.1\ndb_port = 5432\ndb_user = odoo\ndb_password = odoo\ndb_name = odoo\n"
OLD, NEW = "backup/odoo_2026-10-29-18-30-00.dump", "backup/odoo_2026-11-18-18-30-00.dump"
PART = "backup/odoo_2026-11-19-18-30-00.dump.part"
STUBS = {
    "psql": 'echo "psql $*" >> "$STUB_LOG"; case "$*" in *"FROM res_users"*) echo 5;; esac',
    "pg_restore": ('echo "pg_restore $*" >> "$STUB_LOG"; n=0; for a in "$@"; do case "$a" in *.dump) '
                   'n=$((n+1));; esac; done; [ "$n" -le 1 ] || { echo "too many command-line arguments" >&2; '
                   'exit 1; }'),
    "pg_isready": 'echo "pg_isready $*" >> "$STUB_LOG"',
    "pg_ctl": 'echo "pg_ctl $*" >> "$STUB_LOG"',
    "odoo": 'echo "odoo $*" >> "$STUB_LOG"',
}
CASES = []


def case(fn):
    CASES.append(fn)
    return fn


def scratch():
    return tempfile.TemporaryDirectory(dir="/tmp" if os.path.isdir("/tmp") else None)


def stub_bin(root):
    bin_dir = root / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, body in STUBS.items():
        path = bin_dir / name
        path.write_text(f"#!/bin/sh\n{body}\n")
        path.chmod(0o755)
    return bin_dir


def run(root, files=(), backup_dir=True):
    script = root / "download-backup.sh"
    script.write_text(jinja2.Environment(keep_trailing_newline=True).from_string(SRC.read_text()).render(**ANSWERS))
    project = root / "project"
    project.mkdir(exist_ok=True)
    (project / "odoo.conf").write_text(ODOO_CONF)
    if backup_dir:
        (project / "backup").mkdir(exist_ok=True)
    for name in files:
        (project / name).touch()
    env = {**os.environ, "STUB_LOG": str(root / "calls.log"), "DEV_FIXUP_SQL": "/dev/null",
           "PATH": f"{stub_bin(root)}:{os.environ['PATH']}"}
    proc = subprocess.run(["bash", str(script), "odoo", "--skip-download"], cwd=project, env=env,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout


def calls(root, prefix):
    log = root / "calls.log"
    lines = log.read_text().splitlines() if log.exists() else []
    return [line for line in lines if line.startswith(prefix)]


@case
def two_dumps_and_a_part_restore_only_the_newest_dump():
    with scratch() as tmp:
        root = pathlib.Path(tmp)
        out = run(root, files=(OLD, NEW, PART))
        restores = calls(root, "pg_restore")
        assert len(restores) == 1 and restores[0].endswith(f" {NEW}"), restores
        assert f"Restoring {NEW} to staging database 'odoo_restoring'" in out, out
        assert "Backup restored and neutralized into database 'odoo'" in out, out


@case
def one_dump_is_restored_neutralized_and_renamed_into_place():
    with scratch() as tmp:
        root = pathlib.Path(tmp)
        run(root, files=(NEW,))
        assert calls(root, "pg_restore")[0].endswith(f" -d odoo_restoring {NEW}"), calls(root, "pg_restore")
        assert calls(root, "odoo") == ["odoo neutralize -c " + str(root / "project/odoo.conf") + " -d odoo_restoring"], \
            calls(root, "odoo")
        assert any("ALTER DATABASE odoo_restoring RENAME TO odoo" in c for c in calls(root, "psql")), \
            calls(root, "psql")


@case
def a_pg_restore_log_alone_restores_nothing():
    with scratch() as tmp:
        root = pathlib.Path(tmp)
        out = run(root, files=("backup/pg_restore.log",))
        assert "No backup found to restore." in out, out
        assert not calls(root, "pg_restore") and not calls(root, "psql"), calls(root, "")


@case
def a_missing_backup_folder_restores_nothing():
    with scratch() as tmp:
        root = pathlib.Path(tmp)
        out = run(root, backup_dir=False)
        assert "No backup found to restore." in out, out
        assert not calls(root, "pg_restore"), calls(root, "pg_restore")


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
