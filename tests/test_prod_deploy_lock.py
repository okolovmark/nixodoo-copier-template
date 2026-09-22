#!/usr/bin/env python3
"""Corpus for the deploy lock (template/.../skills/deploy/scripts/prod-deploy-lock.sh.jinja)
and the lock guard in prod-deploy-modules.sh.jinja.

Run from the repo root: python3 tests/test_prod_deploy_lock.py

Both scripts are rendered with a minimal answer set and run against a fake
`ssh` that executes the remote command locally under a throwaway $HOME, so the
lock directory really is created, contended, released and marked — no box
involved. Every case is one claim SKILL.md makes about the lock.
"""

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

import jinja2

SKILL = pathlib.Path(
    "template/{% if use_claude_code %}.claude{% endif %}/skills/"
    "{% if prod_ssh_host and custom_repo_name %}deploy{% endif %}/scripts")

ANSWERS = dict(
    prod_local_ssh_host="", test_ssh_hosts={"t1.kepi": "10.0.0.1"},
    prod_remote_project_dir="~/proj", project_dir_var="TEST_PROJECT_DIR",
    odoo_version="16.0", module_prefix="foo", nix_profile_rel=".nix-profile",
    custom_repo_name="addons", prod_remote_odoo_conf="~/proj/odoo.conf",
    prod_db_name="odoo", prod_link_addons_cmd="true", service_suffix="",
)

FAKE_SSH = """#!/usr/bin/env bash
# fake ssh: drop -A / -F <cfg> / <host>, run the remote command locally, stdin through
while [ $# -gt 0 ]; do case "$1" in -F) shift 2 ;; -A) shift ;; *) break ;; esac; done
shift  # host
[ -z "${FAKE_SSH_FAIL:-}" ] || exit 255
exec bash -c "$*"
"""


def render(name, into):
    env = jinja2.Environment(trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=True)
    out = into / name
    out.write_text(env.from_string((SKILL / f"{name}.jinja").read_text()).render(**ANSWERS))
    out.chmod(0o755)
    return out


class Box:
    def __init__(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="deploy-lock-"))
        self.home = self.tmp / "home"
        (self.home / "proj").mkdir(parents=True)          # the box's project checkout
        project = self.tmp / "project"
        (project / ".ssh").mkdir(parents=True)
        (project / ".ssh" / "config").write_text("Host prod\n")
        fakebin = self.tmp / "bin"
        fakebin.mkdir()
        (fakebin / "ssh").write_text(FAKE_SSH)
        (fakebin / "ssh").chmod(0o755)
        self.lock = render("prod-deploy-lock.sh", self.tmp)
        self.deploy = render("prod-deploy-modules.sh", self.tmp)
        self.env = dict(
            os.environ, HOME=str(self.home), PATH=f"{fakebin}:{os.environ['PATH']}",
            TEST_PROJECT_DIR=str(project), DEPLOY_LOCK_HOLDER="tester",
            DEPLOY_LOCK_POLL_SECONDS="1", DEPLOY_LOCK_RETRY_SECONDS="1",
        )

    def run(self, script, *args, **env):
        proc = subprocess.run([str(script), *args], capture_output=True, text=True,
                              env={**self.env, **env})
        return proc.returncode, proc.stdout + proc.stderr

    def lock_dir(self):
        return self.home / "proj" / ".deploy-lock"

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


@case("status on a fresh box is LOCK_FREE, exit 0")
def _(b):
    rc, out = b.run(b.lock, "prod", "status")
    expect(rc == 0 and "LOCK_FREE" in out, (rc, out))


@case("acquire creates the dir and reports holder/pr/state")
def _(b):
    rc, out = b.run(b.lock, "prod", "acquire", "1234")
    expect(rc == 0 and "LOCK_ACQUIRED holder=tester pr=1234 since=" in out and "state=deploying" in out, (rc, out))
    expect(b.lock_dir().is_dir(), "no lock dir")
    rc, out = b.run(b.lock, "prod", "status")
    expect(rc == 3 and "LOCK_HELD holder=tester pr=1234" in out and "age=" in out, (rc, out))


@case("a second acquire without --wait loses at once: LOCK_HELD, exit 3, info untouched")
def _(b):
    b.run(b.lock, "prod", "acquire", "1")
    rc, out = b.run(b.lock, "prod", "acquire", "2", DEPLOY_LOCK_HOLDER="other")
    expect(rc == 3 and "LOCK_HELD holder=tester pr=1" in out and "WAITING" not in out, (rc, out))
    expect("holder=tester pr=1 " in (b.lock_dir() / "info").read_text(), "info overwritten")


@case("--wait expires: WAITING lines, then LOCK_HELD, exit 3")
def _(b):
    b.run(b.lock, "prod", "acquire", "1")
    t0 = time.time()
    rc, out = b.run(b.lock, "prod", "acquire", "2", "--wait", "2", DEPLOY_LOCK_HOLDER="other")
    expect(rc == 3 and "WAITING holder=tester pr=1" in out and out.rstrip().splitlines()[-1].startswith("LOCK_HELD"), (rc, out))
    expect(1.5 <= time.time() - t0 <= 10, f"waited {time.time() - t0:.1f}s")


@case("--wait wins when the holder releases meanwhile")
def _(b):
    b.run(b.lock, "prod", "acquire", "1")
    releaser = subprocess.Popen(
        ["bash", "-c", f"sleep 2; '{b.lock}' prod release 1"], env=b.env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    rc, out = b.run(b.lock, "prod", "acquire", "2", "--wait", "15", DEPLOY_LOCK_HOLDER="other")
    releaser.wait()
    expect(rc == 0 and "WAITING" in out and "LOCK_ACQUIRED holder=other pr=2" in out, (rc, out))


@case("release refuses another PR's lock (LOCK_MISMATCH, exit 4) and drops its own")
def _(b):
    b.run(b.lock, "prod", "acquire", "7")
    rc, out = b.run(b.lock, "prod", "release", "8")
    expect(rc == 4 and "LOCK_MISMATCH" in out and b.lock_dir().is_dir(), (rc, out))
    rc, out = b.run(b.lock, "prod", "release", "7")
    expect(rc == 0 and "LOCK_RELEASED pr=7" in out and not b.lock_dir().exists(), (rc, out))
    rc, out = b.run(b.lock, "prod", "release", "7")
    expect(rc == 0 and "LOCK_FREE" in out, (rc, out))


@case("release --force drops whoever holds it")
def _(b):
    b.run(b.lock, "prod", "acquire", "7")
    rc, out = b.run(b.lock, "prod", "release", "--force")
    expect(rc == 0 and "LOCK_RELEASED pr=7 forced" in out and not b.lock_dir().exists(), (rc, out))


@case("mark-failed keeps the lock, state=failed; a waiting acquire refuses to wait on it")
def _(b):
    b.run(b.lock, "prod", "acquire", "7")
    rc, out = b.run(b.lock, "prod", "mark-failed", "8")
    expect(rc == 4 and "LOCK_MISMATCH" in out, (rc, out))
    rc, out = b.run(b.lock, "prod", "mark-failed", "7")
    expect(rc == 0 and "LOCK_MARKED_FAILED holder=tester pr=7" in out and "state=failed failed_at=" in out, (rc, out))
    info = (b.lock_dir() / "info").read_text()
    expect(info.count("\n") == 1 and "state=failed" in info and "state=deploying" not in info, repr(info))
    t0 = time.time()
    rc, out = b.run(b.lock, "prod", "acquire", "9", "--wait", "30", DEPLOY_LOCK_HOLDER="other")
    expect(rc == 3 and "state=failed" in out and "LOCK_FAILED_DEPLOY" in out and time.time() - t0 < 5, (rc, out))


@case("an unreachable box: three attempts, SSH_UNREACHABLE, exit 5, no lock")
def _(b):
    rc, out = b.run(b.lock, "prod", "acquire", "1", "--wait", "30", FAKE_SSH_FAIL="1")
    expect(rc == 5 and out.count("box unreachable") == 2 and "SSH_UNREACHABLE" in out, (rc, out))
    expect(not b.lock_dir().exists(), "lock created while unreachable")
    rc, out = b.run(b.lock, "prod", "status", FAKE_SSH_FAIL="1")
    expect(rc == 255 and "SSH_UNREACHABLE" in out, (rc, out))


@case("argument validation: host, action, PR, --wait, --force, status takes no PR")
def _(b):
    for args in (("box", "status"), ("prod", "grab", "1"), ("prod", "acquire"), ("prod", "acquire", "12a"),
                 ("prod", "acquire", "1", "--wait", "x"), ("prod", "acquire", "1", "--force"),
                 ("prod", "release"), ("prod", "status", "1"), ("prod", "mark-failed")):
        rc, out = b.run(b.lock, *args)
        expect(rc == 2, (args, rc, out))
    rc, out = b.run(b.lock, "t1.kepi", "status")
    expect(rc == 0 and "LOCK_FREE" in out, ("test alias refused", rc, out))


@case("prod-deploy-modules.sh refuses to pull without the lock, DEPLOY_EXIT=3")
def _(b):
    rc, out = b.run(b.deploy, "prod", "-u", "foo_sale")
    expect(rc == 3 and "DEPLOY_LOCK_MISSING" in out and "DEPLOY_EXIT=3" in out, (rc, out))
    b.run(b.lock, "prod", "acquire", "1")
    rc, out = b.run(b.deploy, "prod", "-u", "foo_sale")
    expect("DEPLOY_LOCK_MISSING" not in out and rc != 3, ("guard fired with the lock held", rc, out))


def main():
    failures = 0
    for label, fn in CASES:
        box = Box()
        try:
            fn(box)
            print(f"ok    {label}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL  {label}\n      {exc}")
        finally:
            box.cleanup()
    print(f"\n{len(CASES) - failures}/{len(CASES)} passed")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
