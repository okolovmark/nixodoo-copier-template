#!/usr/bin/env python3
"""Corpus for the deploy lock (template/.../skills/deploy/scripts/prod-deploy-lock.sh.jinja),
the lock guard in prod-deploy-modules.sh.jinja, and the deploy phases both stamp
on the lock (deploy-phase.sh.jinja).

Run from the repo root: python3 tests/test_prod_deploy_lock.py

Both scripts are rendered with a minimal answer set and run against a fake
`ssh` that executes the remote command locally under a throwaway $HOME, so the
lock directory really is created, contended, released and marked — no box
involved. On that throwaway box `git`, `python` and `systemctl` are stubs in
~/.nix-profile/bin: the update and the restart only log themselves, and the
Odoo health probe (`python -c`) answers once the stub restart has run. Every
case is one claim SKILL.md or scripts/README.md makes about the lock.
"""

import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time

import jinja2

SKILL = pathlib.Path(
    "template/{% if use_agent_layer %}.claude{% endif %}/skills/"
    "{% if prod_ssh_host and custom_repo_name %}deploy{% endif %}/scripts")

ANSWERS = dict(
    prod_local_ssh_host="", test_ssh_hosts={"t1.lan": "10.0.0.1"},
    prod_remote_project_dir="~/proj", project_dir_var="TEST_PROJECT_DIR",
    odoo_version="16.0", module_prefix="foo", nix_profile_rel=".nix-profile",
    custom_repo_name="addons", prod_remote_odoo_conf="~/proj/odoo.conf",
    prod_db_name="odoo", test_db_name="{short}", prod_link_addons_cmd="true", service_suffix="",
)

# The box's tools. Each one drains its stdin, as a tool that reads it would: the
# remote script itself arrives on stdin, so a call without `< /dev/null` would
# swallow the rest of the deploy. The update stub keeps a copy of the lock info as it stands
# while the update runs, the restart stub one as it stands during the restart,
# so a case can read the phase each step saw.
STUB_PYTHON = """#!/usr/bin/env bash
if [ "$1" = -c ]; then echo "$3:$4" > "$HOME/probe-target"; [ -f "$HOME/odoo-answers" ]; exit; fi
cat > /dev/null
echo "python $*" >> "$HOME/calls"
cp "$HOME/proj/.deploy-lock/info" "$HOME/info-during-update" 2>/dev/null
exit "${FAKE_UPDATE_EXIT:-0}"
"""
STUB_SYSTEMCTL = """#!/usr/bin/env bash
cat > /dev/null
echo "systemctl $*" >> "$HOME/calls"
cp "$HOME/proj/.deploy-lock/info" "$HOME/info-during-restart" 2>/dev/null
[ -n "${FAKE_ODOO_STAYS_DOWN:-}" ] || touch "$HOME/odoo-answers"
"""
STUB_GIT = """#!/usr/bin/env bash
cat > /dev/null
echo "git $*" >> "$HOME/calls"
"""

FAKE_SSH = """#!/usr/bin/env bash
# fake ssh: drop -A / -F <cfg> / <host>, run the remote command locally, stdin through
while [ $# -gt 0 ]; do case "$1" in -F) shift 2 ;; -A) shift ;; *) break ;; esac; done
shift  # host
[ -z "${FAKE_SSH_FAIL:-}" ] || exit 255
exec bash -c "$*"
"""


def render(name, into, **answers):
    env = jinja2.Environment(trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=True)
    out = into / name
    out.write_text(env.from_string((SKILL / f"{name}.jinja").read_text()).render(**{**ANSWERS, **answers}))
    out.chmod(0o755)
    return out


class Box:
    def __init__(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="deploy-lock-"))
        self.home = self.tmp / "home"
        (self.home / "proj" / "src" / "addons").mkdir(parents=True)  # the box's project checkout
        # Not the default port, so the probe has to read it from here.
        (self.home / "proj" / "odoo.conf").write_text("[options]\nhttp_port = 8169\n")
        tools = self.home / ".nix-profile" / "bin"
        tools.mkdir(parents=True)
        for name, body in (("python", STUB_PYTHON), ("systemctl", STUB_SYSTEMCTL), ("git", STUB_GIT)):
            (tools / name).write_text(body)
            (tools / name).chmod(0o755)
        project = self.tmp / "project"
        (project / ".ssh").mkdir(parents=True)
        (project / ".ssh" / "config").write_text("Host prod\n")
        fakebin = self.tmp / "bin"
        fakebin.mkdir()
        (fakebin / "ssh").write_text(FAKE_SSH)
        (fakebin / "ssh").chmod(0o755)
        self.public = self.tmp / "public"                 # the proxy-readable directory
        self.public.mkdir()
        render("deploy-phase.sh", self.tmp, deploy_status_file=str(self.public / "status"))
        self.lock = render("prod-deploy-lock.sh", self.tmp)
        self.deploy = render("prod-deploy-modules.sh", self.tmp)
        self.env = dict(
            os.environ, HOME=str(self.home), PATH=f"{fakebin}:{os.environ['PATH']}",
            TEST_PROJECT_DIR=str(project), DEPLOY_LOCK_HOLDER="tester",
            DEPLOY_LOCK_POLL_SECONDS="1", DEPLOY_LOCK_RETRY_SECONDS="1",
            DEPLOY_ODOO_WAIT_SECONDS="4",
        )

    def run(self, script, *args, **env):
        proc = subprocess.run([str(script), *args], capture_output=True, text=True,
                              env={**self.env, **env})
        return proc.returncode, proc.stdout + proc.stderr

    def lock_dir(self):
        return self.home / "proj" / ".deploy-lock"

    def info(self, name="proj/.deploy-lock/info"):
        return (self.home / name).read_text()

    def history(self):
        path = self.home / "proj" / ".deploy-history"
        return path.read_text().splitlines() if path.exists() else []

    def status(self):
        path = self.public / "status"
        return path.read_text() if path.exists() else None

    def calls(self):
        path = self.home / "calls"
        return path.read_text().splitlines() if path.exists() else []

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


def field(info, key):
    m = re.search(rf"(?:^| ){key}=(\S+)", info)
    return m.group(1) if m else None


def eta_seconds(info):
    """eta - phase_at of an info line, in seconds."""
    def ts(v):
        return time.mktime(time.strptime(v, "%Y-%m-%dT%H:%M:%SZ"))
    return int(ts(field(info, "eta")) - ts(field(info, "phase_at")))


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
    rc, out = b.run(b.lock, "t1.lan", "status")
    expect(rc == 0 and "LOCK_FREE" in out, ("test alias refused", rc, out))


@case("prod-deploy-modules.sh refuses to pull without the lock, DEPLOY_EXIT=3")
def _(b):
    rc, out = b.run(b.deploy, "prod", "-u", "foo_sale")
    expect(rc == 3 and "DEPLOY_LOCK_MISSING" in out and "DEPLOY_EXIT=3" in out, (rc, out))
    b.run(b.lock, "prod", "acquire", "1")
    rc, out = b.run(b.deploy, "prod", "-u", "foo_sale")
    expect("DEPLOY_LOCK_MISSING" not in out and rc != 3, ("guard fired with the lock held", rc, out))


@case("a deploy stamps update, then restart, then up, and records both phases' durations")
def _(b):
    b.run(b.lock, "prod", "acquire", "5")
    rc, out = b.run(b.deploy, "prod", "-u", "foo_sale")
    expect(rc == 0 and "DEPLOY_EXIT=0" in out and "ODOO_ANSWERING http://127.0.0.1:8169" in out, (rc, out))
    expect((b.home / "probe-target").read_text().strip() == "127.0.0.1:8169", "the probe asked another port")
    expect(out.index("DEPLOY_PHASE update ") < out.index("DEPLOY_PHASE restart ") < out.index("DEPLOY_PHASE up phase_at="), out)
    during = b.info("info-during-update")
    expect(field(during, "phase") == "update" and eta_seconds(during) == 60, ("update stamp, default eta", during))
    during = b.info("info-during-restart")
    expect(field(during, "phase") == "restart" and eta_seconds(during) == 15, ("restart stamp, default eta", during))
    info = b.info()
    expect(field(info, "phase") == "up" and field(info, "eta") is None, ("up: no eta", info))
    expect(info.startswith("holder=tester pr=5 since=") and "state=deploying" in info and info.count("\n") == 1, info)
    hist = b.history()
    expect(len(hist) == 2 and " pr=5 phase=update seconds=" in hist[0] and " pr=5 phase=restart seconds=" in hist[1], hist)
    calls = b.calls()
    expect([c.split()[0] for c in calls] == ["git", "python", "systemctl"], calls)
    expect("-d odoo -u foo_sale --stop-after-init --dev=xml" in calls[1], calls)


@case("the eta is the median of the phase's last 10 runs once three are recorded")
def _(b):
    # 12 runs: the two oldest fall out of the window; of the 10 left
    # (10..100) the lower median is 50.
    runs = (999, 999, 10, 100, 20, 90, 30, 80, 40, 70, 50, 60)
    (b.home / "proj" / ".deploy-history").write_text("".join(
        f"2026-01-01T00:00:{i:02d}Z pr={i} phase=update seconds={s}\n" for i, s in enumerate(runs)))
    b.run(b.lock, "prod", "acquire", "5")
    rc, out = b.run(b.deploy, "prod", "-u", "foo_sale")
    expect(rc == 0, (rc, out))
    expect(eta_seconds(b.info("info-during-update")) == 50, b.info("info-during-update"))
    expect(eta_seconds(b.info("info-during-restart")) == 15, ("restart has no record yet", b.info("info-during-restart")))


@case("a failed update stops before the restart: DEPLOY_EXIT=1, the phase stays update")
def _(b):
    b.run(b.lock, "prod", "acquire", "5")
    rc, out = b.run(b.deploy, "prod", "-u", "foo_sale", FAKE_UPDATE_EXIT="1")
    expect(rc == 1 and "DEPLOY_EXIT=1" in out and "DEPLOY_PHASE restart" not in out, (rc, out))
    expect(not any(c.startswith("systemctl") for c in b.calls()), b.calls())
    expect(field(b.info(), "phase") == "update" and field(b.info(), "eta"), b.info())


@case("Odoo not answering after the restart: ODOO_NOT_ANSWERING, DEPLOY_EXIT=4, the phase stays restart")
def _(b):
    b.run(b.lock, "prod", "acquire", "5")
    rc, out = b.run(b.deploy, "prod", "-u", "foo_sale", FAKE_ODOO_STAYS_DOWN="1")
    expect(rc == 4 and "ODOO_NOT_ANSWERING" in out and "DEPLOY_EXIT=4" in out, (rc, out))
    expect(field(b.info(), "phase") == "restart", b.info())
    rc, out = b.run(b.lock, "prod", "mark-failed", "5")
    expect(rc == 0 and "state=failed" in b.info() and field(b.info(), "pr") == "5", (rc, out, b.info()))


@case("phase <PR> stopped / up: the Step-2b stamps, checked against the lock's PR")
def _(b):
    rc, out = b.run(b.lock, "prod", "phase", "5", "stopped")
    expect(rc == 4 and "LOCK_FREE" in out, (rc, out))
    b.run(b.lock, "prod", "acquire", "5")
    rc, out = b.run(b.lock, "prod", "phase", "6", "stopped")
    expect(rc == 4 and "LOCK_MISMATCH" in out and field(b.info(), "phase") is None, (rc, out))
    rc, out = b.run(b.lock, "prod", "phase", "5", "stopped")
    expect(rc == 0 and "LOCK_PHASE holder=tester pr=5" in out and eta_seconds(b.info()) == 120, (rc, out))
    rc, out = b.run(b.lock, "prod", "phase", "5", "up")
    expect(rc == 6 and "ODOO_NOT_ANSWERING" in out and field(b.info(), "phase") == "stopped", ("up waits for Odoo", rc, out))
    (b.home / "odoo-answers").touch()
    rc, out = b.run(b.lock, "prod", "phase", "5", "up")
    expect(rc == 0 and "ODOO_ANSWERING http://127.0.0.1:8169" in out, (rc, out))
    expect(field(b.info(), "phase") == "up" and field(b.info(), "eta") is None, (rc, out))
    expect(len(b.history()) == 1 and " pr=5 phase=stopped seconds=" in b.history()[0], b.history())
    rc, out = b.run(b.lock, "prod", "status")
    expect(rc == 3 and "phase=up" in out and "age=" in out, (rc, out))
    rc, out = b.run(b.lock, "prod", "release", "5")
    expect(rc == 0 and not b.lock_dir().exists() and len(b.history()) == 1, ("history outlives the lock", rc, out))


@case("phase argument validation: a known phase name, one only, phase takes it only")
def _(b):
    for args in (("prod", "phase", "5"), ("prod", "phase", "5", "half"), ("prod", "phase", "5", "up", "up"),
                 ("prod", "phase"), ("prod", "release", "5", "up")):
        rc, out = b.run(b.lock, *args)
        expect(rc == 2, (args, rc, out))


@case("a stamp that cannot be written never fails the deploy")
def _(b):
    b.run(b.lock, "prod", "acquire", "5")
    (b.lock_dir() / "info").write_text("garbage\n")
    rc, out = b.run(b.deploy, "prod", "-u", "foo_sale")
    expect(rc == 0 and "DEPLOY_PHASE_SKIPPED update" in out and "DEPLOY_EXIT=0" in out, (rc, out))
    expect(b.info() == "garbage\n", ("the info line was rewritten", b.info()))


@case("the public status exists only while the service is affected or the deploy failed")
def _(b):
    b.run(b.lock, "prod", "acquire", "5")
    expect(b.status() is None, ("published before the deploy touched anything", b.status()))
    b.run(b.deploy, "prod", "-u", "foo_sale")
    expect(b.status() is None, ("still published once Odoo answers again", b.status()))
    b.run(b.lock, "prod", "release", "5")
    b.run(b.lock, "prod", "acquire", "6")
    (b.home / "odoo-answers").unlink()
    rc, out = b.run(b.deploy, "prod", "-u", "foo_sale", FAKE_ODOO_STAYS_DOWN="1")
    st = b.status()
    expect(st.startswith("state=deploying phase=restart phase_at=") and " eta=" in st, repr(st))
    expect("holder" not in st and "pr=" not in st and "since" not in st, repr(st))
    expect(oct((b.public / "status").stat().st_mode & 0o777) == "0o644", oct((b.public / "status").stat().st_mode))
    b.run(b.lock, "prod", "mark-failed", "6")
    expect(b.status().startswith("state=failed phase=restart") and "failed_at" not in b.status(), repr(b.status()))
    b.run(b.lock, "prod", "release", "--force")
    expect(b.status() is None, ("status left after release", b.status()))


@case("a stale public status is cleared by a release of the free lock")
def _(b):
    (b.public / "status").write_text("state=deploying\n")
    rc, out = b.run(b.lock, "prod", "release", "5")
    expect(rc == 0 and "LOCK_FREE" in out and b.status() is None, (rc, out, b.status()))


@case("a box without the status directory publishes nothing, and the deploy runs")
def _(b):
    b.public.rmdir()
    b.run(b.lock, "prod", "acquire", "5")
    rc, out = b.run(b.deploy, "prod", "-u", "foo_sale")
    expect(rc == 0 and "DEPLOY_EXIT=0" in out and "SKIPPED" not in out and not b.public.exists(), (rc, out))


@case("a phase left after a failure is not recorded as a duration")
def _(b):
    b.run(b.lock, "prod", "acquire", "5")
    b.run(b.deploy, "prod", "-u", "foo_sale", FAKE_UPDATE_EXIT="1")
    b.run(b.lock, "prod", "mark-failed", "5")
    rc, out = b.run(b.deploy, "prod", "-u", "foo_sale")
    expect(rc == 0 and not any("phase=update" in h for h in b.history()), (rc, b.history()))


def main():
    failures = 0
    for label, fn in CASES:
        box = Box()
        try:
            fn(box)
            print(f"ok    {label}")
        except Exception as exc:  # a crash in a case (a missing file, a parse) fails that case only
            failures += 1
            print(f"FAIL  {label}\n      {type(exc).__name__}: {exc}")
        finally:
            box.cleanup()
    print(f"\n{len(CASES) - failures}/{len(CASES)} passed")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
