#!/usr/bin/env python3
"""Corpus for the per-host database in the deploy / prod-ops box scripts
(prod-deploy-modules.sh, prod-requeue-jobs.sh, prod-run-odoo-script.sh).

Run from the repo root: python3 tests/test_db_per_host.py

Odoo creates a database it cannot find and installs base into it, so a script
that names prod's database on a test box does not fail: it runs on a new empty
database and exits 0. Each script is rendered with a minimal answer set and run
against a fake `ssh` that records the remote command instead of running it; the
cases assert the `-d` it carries for every host.
"""

import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

import jinja2

SKILLS = pathlib.Path("template/{% if use_claude_code %}.claude{% endif %}/skills")
DEPLOY = SKILLS / "{% if prod_ssh_host and custom_repo_name %}deploy{% endif %}/scripts"
OPS = SKILLS / "{% if prod_ssh_host and custom_repo_name %}prod-ops{% endif %}/scripts"
SCRIPTS = {
    "prod-deploy-modules.sh": (DEPLOY / "prod-deploy-modules.sh.jinja", ("-u", "foo_sale")),
    "prod-requeue-jobs.sh": (DEPLOY / "{% if use_queue_job %}prod-requeue-jobs.sh{% endif %}.jinja", ()),
    "prod-run-odoo-script.sh": (OPS / "prod-run-odoo-script.sh.jinja", ("SCRIPT", "dry")),
}

ANSWERS = dict(
    prod_local_ssh_host="10.0.0.9", test_ssh_hosts={"t1.kepi": "10.0.0.1", "t2": "10.0.0.2"},
    prod_remote_project_dir="~/proj", project_dir_var="TEST_PROJECT_DIR",
    odoo_version="16.0", module_prefix="foo", nix_profile_rel=".nix-profile",
    custom_repo_name="addons", prod_remote_odoo_conf="~/proj/odoo.conf",
    prod_db_name="odoo", test_db_name="{short}", prod_link_addons_cmd="true",
    service_suffix="",
)

FAKE_SSH = """#!/usr/bin/env bash
# fake ssh: drop -A / -F <cfg>, record host + remote command, answer every marker
while [ $# -gt 0 ]; do case "$1" in -F) shift 2 ;; -A) shift ;; *) break ;; esac; done
printf '%s\\n' "$*" >> "$FAKE_SSH_LOG"
cat > /dev/null
echo "SCRIPT_DONE mode=dry"
echo "REQUEUED 0"
"""


class Run:
    def __init__(self, **answers):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="db-per-host-"))
        project = self.tmp / "project"
        (project / ".ssh").mkdir(parents=True)
        (project / ".ssh" / "config").write_text("Host prod\n")
        fakebin = self.tmp / "bin"
        fakebin.mkdir()
        (fakebin / "ssh").write_text(FAKE_SSH)
        (fakebin / "ssh").chmod(0o755)
        self.script = self.tmp / "probe.py"
        self.script.write_text("print('SCRIPT_DONE mode=dry')\n")
        self.log = self.tmp / "ssh.log"
        env = jinja2.Environment(trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=True)
        self.rendered = {}
        for name, (src, _) in SCRIPTS.items():
            out = self.tmp / name
            out.write_text(env.from_string(src.read_text()).render(**{**ANSWERS, **answers}))
            out.chmod(0o755)
            self.rendered[name] = out
        self.env = dict(os.environ, PATH=f"{fakebin}:{os.environ['PATH']}",
                        TEST_PROJECT_DIR=str(project), FAKE_SSH_LOG=str(self.log))

    def db(self, name, host):
        """The -d the script sends to <host>, or None when it sent nothing."""
        self.log.write_text("")
        args = [a if a != "SCRIPT" else str(self.script) for a in SCRIPTS[name][1]]
        proc = subprocess.run([str(self.rendered[name]), host, *args],
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, env=self.env)
        sent = self.log.read_text()
        if proc.returncode != 0 or not sent:
            raise AssertionError((name, host, proc.returncode, proc.stdout + proc.stderr))
        expect(sent.split()[0] == host, (name, "wrong host", sent))
        m = re.search(r"odoo-bin[^\n]* -d (\S+) ", sent)  # not the `[ -d <lock> ]` test
        return m.group(1) if m else None

    def cleanup(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


CASES = []


def case(label):
    def deco(fn):
        CASES.append((label, fn))
        return fn
    return deco


def expect(cond, detail):
    if not cond:
        raise AssertionError(detail)


def each_script(run, host, want):
    for name in SCRIPTS:
        got = run.db(name, host)
        expect(got == want, (name, host, f"-d {got}, want -d {want}"))


@case("prod and prod-local get prod_db_name")
def _():
    run = Run()
    try:
        each_script(run, "prod", "odoo")
        each_script(run, "prod-local", "odoo")
    finally:
        run.cleanup()


@case("{short}: a test box gets its alias up to the first dot")
def _():
    run = Run()
    try:
        each_script(run, "t1.kepi", "t1")
        each_script(run, "t2", "t2")
    finally:
        run.cleanup()


@case("{alias}: a test box gets its full alias")
def _():
    run = Run(test_db_name="db_{alias}")
    try:
        each_script(run, "t1.kepi", "db_t1.kepi")
    finally:
        run.cleanup()


@case("the default (test_db_name = prod_db_name) keeps the old behaviour")
def _():
    run = Run(test_db_name="odoo")
    try:
        each_script(run, "t1.kepi", "odoo")
    finally:
        run.cleanup()


@case("no test boxes: prod still resolves, rendered scripts pass bash -n")
def _():
    run = Run(test_ssh_hosts={}, prod_local_ssh_host="")
    try:
        each_script(run, "prod", "odoo")
        for name, path in run.rendered.items():
            proc = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
            expect(proc.returncode == 0, (name, proc.stderr))
    finally:
        run.cleanup()


@case("an unknown host is still refused before any ssh")
def _():
    run = Run()
    try:
        for name, (_, args) in SCRIPTS.items():
            args = [a if a != "SCRIPT" else str(run.script) for a in args]
            proc = subprocess.run([str(run.rendered[name]), "t3", *args],
                                  stdin=subprocess.DEVNULL, capture_output=True, text=True, env=run.env)
            expect(proc.returncode == 2 and (not run.log.exists() or not run.log.read_text()),
                   (name, proc.returncode, proc.stdout + proc.stderr))
    finally:
        run.cleanup()


def main():
    failures = 0
    for label, fn in CASES:
        try:
            fn()
            print(f"ok    {label}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL  {label}\n      {exc}")
    print(f"\n{len(CASES) - failures}/{len(CASES)} passed")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
