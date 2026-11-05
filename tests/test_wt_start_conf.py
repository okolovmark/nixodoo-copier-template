#!/usr/bin/env python3
"""Corpus for the per-session odoo.conf that worktree-env's wt-start.sh writes.

Run from the repo root: python3 tests/test_wt_start_conf.py

The heredoc used to hardcode server_wide_modules=base,web,queue_job,
longpolling_port and db_user/db_password odoo, whatever the answers said: a
project without queue_job logged "Couldn't load module queue_job" in every
session, and Odoo 17+ got the pre-17 port name. Each case renders the script
with an answer set and reads the [options] block back.

The wt-test.sh cases run the rendered wrapper with psql and odoo stubbed, plus
its module-state query against a live PostgreSQL when one answers (else skip).
The Claude-worktree cases run the rendered wt-start.sh up to its bootstrap step,
the exposure cases all of it with its helper scripts, git and psql stubbed.
The wt-restore.sh cases run it with psql, pg_restore and odoo stubbed on a
worktree with a scripted git history. The wt-stop.sh cases share wt-test.sh's stubs.
The --env-repo cases run wt-start.sh, wt-stop.sh and wt-list.sh on a project that is
a real git repo, git stubbed for the addons repo only.
CI runs this file once per push (the wt-scripts job); it needs jinja2, bash, flock and git.
"""

import itertools
import os
import pathlib
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time

import jinja2

SRC = pathlib.Path(
    "template/{% if use_agent_layer %}.claude{% endif %}/skills"
    "/{% if custom_repo_name %}worktree-env{% endif %}/scripts/wt-start.sh.jinja"
)
TEST_SRC = SRC.with_name("wt-test.sh.jinja")
STOP_SRC = SRC.with_name("wt-stop.sh.jinja")
COMMON_SRC = SRC.with_name("wt-common.sh.jinja")

BASE = dict(
    odoo_version="16.0", odoo_major="16", use_queue_job=True, db_user="odoo", db_password="odoo",
    postgres_port=16432, service_suffix="", backup_s3_bucket="bucket",
)


def render(**answers):
    env = jinja2.Environment(trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=True)
    return env.from_string(SRC.read_text()).render(**{**BASE, **answers})


def sections(text):
    return parse_conf(re.search(r'cat > "\$\w+" <<EOF\n(.*?)\nEOF\n', text, re.S).group(1))


def parse_conf(block):
    found, current = {}, None
    for line in block.splitlines():
        if re.fullmatch(r"\[[a-z_]+\]", line):
            current = found.setdefault(line[1:-1], {})
        elif line:
            key, value = line.split(" = ", 1)
            current[key] = value
    return found


def conf(text):
    return sections(text)["options"]


def render_script(src):
    env = jinja2.Environment(trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=True)
    answers = dict(BASE, project_dir_var="WT_TEST_PROJECT_DIR", project_name="p", custom_repo_name="acme-addons",
                   nix_profile_rel=".nix-profile", module_prefix="acme")
    return env.from_string(src.read_text()).render(**answers)


CASES = []


class Skip(Exception):
    pass


def case(fn):
    CASES.append(fn)
    return fn


@case
def queue_job_follows_the_answer():
    assert conf(render(use_queue_job=True))["server_wide_modules"] == "base,web,queue_job"
    assert conf(render(use_queue_job=False))["server_wide_modules"] == "base,web"


@case
def port_name_follows_the_major():
    for major, key, other in (("16", "longpolling_port", "gevent_port"),
                              ("17", "gevent_port", "longpolling_port"),
                              ("20", "gevent_port", "longpolling_port")):
        c = conf(render(odoo_version=f"{major}.0", odoo_major=major))
        assert c.get(key) == "$((PORT + 1000))", (major, c)
        assert other not in c, (major, c)


@case
def db_connection_comes_from_wt_common():
    # host/port/password are wt-common's WT_* (answers by default, .env overrides);
    # the role stays the answer
    c = conf(render(db_user="erp", db_password="s3cret"))
    assert (c["db_user"], c["db_host"], c["db_port"], c["db_password"]) == \
        ("erp", "$WT_DB_HOST", "$WT_DB_PORT", "$WT_DB_PASSWORD"), c


def common_vars(env_lines=None, **answers):
    """Source the rendered wt-common.sh in bash (PROJ = a temp dir holding .env)
    and return the WT_* values and PSQL it ends up with."""
    import subprocess, tempfile
    text = jinja2.Environment(trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=True).from_string(
        COMMON_SRC.read_text()).render(**{**BASE, "project_dir_var": "PROJ_DIR", "project_name": "proj",
                                          "custom_repo_name": "addons", **answers})
    with tempfile.TemporaryDirectory() as d:
        if env_lines is not None:
            pathlib.Path(d, ".env").write_text("\n".join(env_lines) + "\n")
        common = pathlib.Path(d, "wt-common.sh")
        common.write_text(text)
        names = ("WT_DB_HOST", "WT_DB_PORT", "WT_DB_PASSWORD", "WT_HTTP_INTERFACE", "WT_HOST_SUFFIX", "PSQL", "PGPASSWORD")
        out = subprocess.run(
            ["bash", "-c", 'set -euo pipefail; source "$1"; for v in ' + " ".join(names) + '; do printf "%s=%s\\n" "$v" "${!v}"; done; session_host wt_x',
             "_", str(common)],
            env={"PATH": os.environ["PATH"], "HOME": d, "PROJ_DIR": d}, capture_output=True, text=True, check=True).stdout
    lines = out.splitlines()
    vals = dict(line.split("=", 1) for line in lines[:-1])
    vals["session_host"] = lines[-1]
    return vals


@case
def without_overrides_a_session_is_local():
    for env_lines in (None, ["PGHOST=elsewhere", "ODOO_URL=http://x"]):
        v = common_vars(env_lines, db_password="s3cret", postgres_port=16432)
        assert (v["WT_DB_HOST"], v["WT_DB_PORT"], v["WT_DB_PASSWORD"], v["WT_HTTP_INTERFACE"], v["WT_HOST_SUFFIX"]) == \
            ("localhost", "16432", "s3cret", "::1", "localhost"), (env_lines, v)
        assert v["PSQL"] == "psql -X -h localhost -p 16432 -U odoo" and v["PGPASSWORD"] == "s3cret", v
        assert v["session_host"] == "wt_x.localhost", v


@case
def a_shared_test_server_overrides_from_dotenv():
    v = common_vars(["PGPASSWORD=ignored", "WT_DB_HOST=10.0.0.5", "WT_DB_PORT=5432", "WT_DB_PASSWORD=p=w d",
                     "WT_HTTP_INTERFACE=0.0.0.0", "WT_HOST_SUFFIX=box1.lan", "WT_EVIL=1", "#WT_DB_HOST=no"])
    assert (v["WT_DB_HOST"], v["WT_DB_PORT"], v["WT_DB_PASSWORD"], v["WT_HTTP_INTERFACE"], v["WT_HOST_SUFFIX"]) == \
        ("10.0.0.5", "5432", "p=w d", "0.0.0.0", "box1.lan"), v
    assert v["PSQL"] == "psql -X -h 10.0.0.5 -p 5432 -U odoo" and v["PGPASSWORD"] == "p=w d", v
    assert v["session_host"] == "wt_x.box1.lan", v


@case
def a_plain_login_gets_the_project_dir_exported():
    # wt-test.sh runs the odoo wrapper, which takes the cwd for the project when the
    # variable is unset: an SSH login on a test server has no dev shell to set it
    text = jinja2.Environment(trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=True).from_string(
        COMMON_SRC.read_text()).render(**{**BASE, "project_dir_var": "PROJ_DIR", "project_name": "proj",
                                          "custom_repo_name": "addons"})
    with tempfile.TemporaryDirectory() as d:
        common = pathlib.Path(d, "wt-common.sh")
        common.write_text(text)
        out = subprocess.run(
            ["bash", "-c", 'set -euo pipefail; source "$1"; bash -c \'printf %s "$PROJ_DIR"\'', "_", str(common)],
            env={"PATH": os.environ["PATH"], "HOME": d}, capture_output=True, text=True, check=True).stdout
    assert out == f"{d}/proj", out


@case
def every_database_drop_goes_through_drop_db():
    # WITH (FORCE) errors for a non-superuser role as soon as an autovacuum worker
    # sits on the DB (fresh restore on a shared server); only drop_db falls back
    env = jinja2.Environment(trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=True)
    for name in ("wt-restore.sh.jinja", "wt-stop.sh.jinja"):
        text = env.from_string(SRC.with_name(name).read_text()).render(**{**BASE, "project_dir_var": "PROJ_DIR"})
        assert "WITH (FORCE)" not in text, name
        assert "drop_db " in text, name
    common = COMMON_SRC.read_text()
    body = common[common.index("drop_db() {"):]
    body = body[:body.index("\n}\n")]
    assert body.index("WITH (FORCE)") < body.index("usename=current_user") < body.rindex("IF EXISTS $1;"), body


@case
def no_report_url_option():
    assert "report_url" not in conf(render())


@case
def report_url_param_pinned_before_the_restart():
    text = render()
    marker = "VALUES ('report.url', 'http://$HOST:$PORT')"
    assert marker in text, "wt-start does not pin report.url"
    pin = text.index(marker)
    assert text.index('bash "$WT_SCRIPT_DIR/wt-restore.sh"') < pin < text.index("systemctl --user restart"), \
        "report.url must be pinned after the restore and before the restart"


@case
def the_host_names_the_database():
    # http://wt_<slug>.localhost:<port>: %d is the first label of the Host, and
    # *.localhost resolves to ::1, the only interface a session listens on (a
    # prod copy with admin/admin stays off the LAN)
    # (locally; a shared test server moves both through WT_HTTP_INTERFACE / WT_HOST_SUFFIX)
    c = conf(render())
    assert (c["dbfilter"], c["http_interface"]) == ("^%d\\$", "$WT_HTTP_INTERFACE"), c
    text = render()
    assert 'HOST="$(session_host "$DB")"' in text
    assert 'echo "URL: http://$HOST:$PORT/web"' in text


@case
def no_attachment_seed_from_a_main_database():
    assert "SEED_FILESTORE" not in render()


@case
def the_queue_job_runner_reaches_the_session():
    # the runner's job URL is http://<host>:<port>/queue_job/runjob: left to http_interface
    # the host is ::1 (an invalid URL host), and only the DB-named host passes ^%d$
    c = sections(render(use_queue_job=True))["queue_job"]
    assert c == {"channels": "root:3", "host": "$HOST", "port": "$PORT"}, c
    assert "queue_job" not in sections(render(use_queue_job=False))


@case
def a_test_run_talks_to_ipv4_loopback_with_the_session_db():
    text = render_script(TEST_SRC)
    assert 'ODOO_BIN="$HOME/.nix-profile/bin/odoo"' in text, text
    run = text[text.index('ODOO=("$ODOO_BIN"'):]
    for flag in ('-c "$CONF"', "--http-interface 127.0.0.1", '--http-port "$PORT"', '--db-filter "^${DB}\\$"'):
        assert flag in run[:run.index(")\n")], (flag, run)
    assert 'exec "${ODOO[@]}" "$@"' in run and '"$@" <&0 >&7 2>&1 7>&- &' in run, run


# what `odoo server --help` prints, captured from Odoo 16 and 19: wt-test.sh reads its options from it
SERVER_HELP = {major: pathlib.Path(f"tests/fixtures/odoo-{major}-server-help.txt").resolve() for major in ("16", "19")}


def stub(argv_file, body):
    return f'#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "{argv_file}"\n{body}'


def read_argv(path):
    return path.read_text().splitlines() if path.exists() else None


def psql_vars(argv):
    return dict(argv[i + 1].split("=", 1) for i, arg in enumerate(argv or []) if arg == "-v")


def wt_test_project(root, odoo_body, missing="", psql_rc=0, psql_body="", python_body="", systemctl_body="",
                    major="16"):
    """Lay out a project at `root` with the rendered wt-test.sh and wt-stop.sh for slug `x`,
    psql, odoo and systemctl stubbed.

    The psql stub runs `psql_body`, answers the module-state query with `missing`
    and exits `psql_rc`; the odoo stub answers `server --help` as Odoo `major` does and
    otherwise runs `odoo_body`, systemctl `systemctl_body`. With `python_body`, a
    python3 on PATH runs it before the real one. Returns the command of a run and
    its environment.
    """
    scripts, bin_dir, odoo_bin = root / "scripts", root / "bin", root / ".nix-profile/bin"
    for d in (scripts, bin_dir, odoo_bin, root / ".worktrees/_env/x"):
        d.mkdir(parents=True)
    (root / ".worktrees/_env/x/odoo.conf").write_text("[options]\n")
    for src in (TEST_SRC, STOP_SRC, COMMON_SRC):
        (scripts / src.name.removesuffix(".jinja")).write_text(render_script(src))
    (bin_dir / "psql").write_text(stub(root / "psql.argv", f"{psql_body}cat > /dev/null\n"
                                                           f"printf '%s' {shlex.quote(missing)}\nexit {psql_rc}\n"))
    (odoo_bin / "odoo").write_text(stub(root / "odoo.argv", odoo_body).replace(
        "\n", f'\n[ "$*" != "server --help" ] || exec cat {SERVER_HELP[major]}\n', 1))
    (bin_dir / "python3").write_text(f'#!/usr/bin/env bash\n{python_body}exec {shlex.quote(sys.executable)} "$@"\n')
    (bin_dir / "systemctl").write_text(f"#!/usr/bin/env bash\n{systemctl_body}")
    for f in (bin_dir / "psql", odoo_bin / "odoo", bin_dir / "python3", bin_dir / "systemctl"):
        f.chmod(0o755)
    env = {**os.environ, "HOME": str(root), "WT_TEST_PROJECT_DIR": str(root), "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    return ["bash", str(scripts / "wt-test.sh"), "x"], env


def run_wt_test(args, missing="", psql_rc=0, odoo_out="", odoo_rc=0, major="16"):
    """Run the rendered wt-test.sh for slug `x` with psql and odoo stubbed.

    The odoo stub logs `odoo_out` to stderr, as Odoo does, and exits `odoo_rc`; its
    `server --help` is Odoo `major`'s. Returns (exit code, stdout+stderr, psql argv,
    odoo argv), an argv being None when that program did not run.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        cmd, env = wt_test_project(root, f'printf "%s\\n" {shlex.quote(odoo_out)} >&2\nexit {odoo_rc}\n',
                                   missing, psql_rc, major=major)
        proc = subprocess.run([*cmd, *args], capture_output=True, text=True, env=env)
        return (proc.returncode, proc.stdout + proc.stderr,
                read_argv(root / "psql.argv"), read_argv(root / "odoo.argv"))


RESULT = "INFO wt_x odoo.tests.result: {} failed, 0 error(s) of {} tests when loading database 'wt_x'"


def wait_for(predicate, what, timeout=10):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        time.sleep(0.02)


def start_bg(cmd, env, out_path):
    """Start `cmd` in its own process group, its output going to `out_path`."""
    with open(out_path, "w") as out:
        return subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, env=env,
                                start_new_session=True)


def finish_bg(procs, timeout=30):
    """Wait for `procs`; any still running after `timeout` is killed with its group and fails."""
    hung = []
    for proc in procs:
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            hung.append(proc.args)
    assert not hung, f"hung for {timeout}s: {hung}"


TEE_RUN = ["-u", "acme_mod", "--test-enable"]
EXEC_RUN = ["-u", "acme_mod", "--test-file", "acme_mod/tests/test_x.py"]


@case
def a_second_run_on_the_env_waits_for_the_first():
    # either path holding, the tee one (test count) and the exec one (Odoo inherits fd 9)
    for holder_args, waiter_args in ((TEE_RUN, EXEC_RUN), (EXEC_RUN, TEE_RUN)):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            events, release = root / "events", root / "release"
            cmd, env = wt_test_project(
                root, f'echo "start $$" >> {events}\nwhile [ ! -e {release} ]; do sleep 0.02; done\n'
                      f'echo "end $$" >> {events}\nprintf "%s\\n" {shlex.quote(RESULT.format(0, 1))} >&2\n',
                psql_body=f'echo "psql $$" >> {events}\n',
                python_body=f'case "$*" in *socket*) echo "port $$" >> {events} ;; esac\n')
            outs = [root / "holder.out", root / "waiter.out"]
            runs = [start_bg([*cmd, *holder_args], env, outs[0])]
            try:
                wait_for(lambda: events.exists() and "start" in events.read_text(), "the holder's Odoo")
                runs.append(start_bg([*cmd, *waiter_args], env, outs[1]))
                wait_for(lambda: "waiting" in outs[1].read_text(), "the waiter's waiting line")
            finally:
                release.touch()
                finish_bg(runs)
            texts = [out.read_text() for out in outs]
            log = [line.split() for line in events.read_text().splitlines()]
            assert [run.returncode for run in runs] == [0, 0], texts
            # the waiter's module-state check waits for the holder (it may install a module),
            # and so does its port pick (a port picked before a long wait may be taken by then)
            assert [kind for kind, _pid in log] == ["psql", "port", "start", "end"] * 2, (log, texts)
            assert log[2][1] == log[3][1] and log[6][1] == log[7][1], log
            assert f"(pid {runs[0].pid}: " in texts[1] and "waiting" not in texts[0], texts


@case
def a_run_wrapped_in_the_env_lock_runs_under_it():
    for wrapper in (["flock", "{lock}"], ["flock", "-o", "{lock}"],
                    ["bash", "-c", 'exec 8>>"$0"; flock 8; "$@"', "{lock}"],
                    ["bash", "-c", 'exec 8>>"$0"; flock 8; exec "$@"', "{lock}"]):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            lock = root / ".worktrees/_env/x/test.lock"
            cmd, env = wt_test_project(root, f'printf "%s\\n" {shlex.quote(RESULT.format(0, 1))} >&2\n')
            run = start_bg([*(arg.format(lock=lock) for arg in wrapper), *cmd, *TEE_RUN], env, root / "out")
            finish_bg([run], timeout=20)
            out = (root / "out").read_text()
            assert run.returncode == 0 and "running under its lock" in out and "waiting" not in out, (wrapper, out)
            assert RESULT.format(0, 1) in out, (wrapper, out)


@case
def a_lock_held_by_a_command_beside_the_run_is_waited_for_and_named():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        lock, held, release = root / ".worktrees/_env/x/test.lock", root / "held", root / "release"
        cmd, env = wt_test_project(root, f'printf "%s\\n" {shlex.quote(RESULT.format(0, 1))} >&2\n')
        procs = [start_bg(["flock", str(lock), "sh", "-c", f"touch {held}; while [ ! -e {release} ]; do sleep 0.02; done"],
                          env, root / "holder.out")]
        try:
            wait_for(held.exists, "the hand-run holder")
            procs.append(start_bg([*cmd, *TEE_RUN], env, root / "out"))
            wait_for(lambda: "waiting" in (root / "out").read_text(), "the run's waiting line")
        finally:
            release.touch()
            finish_bg(procs)
        out = (root / "out").read_text()
        assert procs[1].returncode == 0 and f"(pid {procs[0].pid}: flock {lock} sh -c" in out, out


@case
def a_lone_run_holds_the_env_test_lock_without_waiting():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        lock = root / ".worktrees/_env/x/test.lock"
        cmd, env = wt_test_project(root, f'flock -n {lock} true || echo "lock held" >&2\n'
                                         f'printf "%s\\n" {shlex.quote(RESULT.format(0, 2))} >&2\n')
        proc = subprocess.run([*cmd, "--test-enable"], capture_output=True, text=True, env=env)
        out = proc.stdout + proc.stderr
        assert proc.returncode == 0 and "lock held" in out and "waiting" not in out, (proc.returncode, out)


def hold_lock(lock, env, out, held, release, then=""):
    """A command beside the runs holding `lock` until `release` exists, running `then` before it lets go."""
    return start_bg(["flock", str(lock), "sh", "-c",
                     f"touch {held}; while [ ! -e {release} ]; do sleep 0.02; done; {then}"], env, out)


@case
def wt_stop_waits_for_a_test_run_on_the_env_then_tears_down():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        events, release, env_dir = root / "events", root / "release", root / ".worktrees/_env/x"
        cmd, env = wt_test_project(
            root, f'echo "start $$" >> {events}\nwhile [ ! -e {release} ]; do sleep 0.02; done\necho "end $$" >> {events}\n',
            psql_body=f'echo "psql $*" >> {events}\n', systemctl_body=f'echo "systemctl $*" >> {events}\n')
        outs = [root / "test.out", root / "stop.out"]
        procs = [start_bg([*cmd, *EXEC_RUN], env, outs[0])]
        try:
            wait_for(lambda: events.exists() and "start" in events.read_text(), "the test run's Odoo")
            procs.append(start_bg(["bash", str(root / "scripts/wt-stop.sh"), "x"], env, outs[1]))
            wait_for(lambda: "waiting" in outs[1].read_text(), "wt-stop's waiting line")
            text = events.read_text()
            assert "DROP DATABASE" not in text and "systemctl" not in text and env_dir.is_dir(), text
        finally:
            release.touch()
            finish_bg(procs)
        out, log = outs[1].read_text(), events.read_text().splitlines()
        assert procs[1].returncode == 0 and "STATUS: removed" in out and not env_dir.exists(), out
        assert f"(pid {procs[0].pid}: " in out and "then stopping the env" in out, out
        end = next(i for i, line in enumerate(log) if line.startswith("end "))
        assert all(i > end for i, line in enumerate(log)
                   if "pg_database" in line or "DROP DATABASE" in line or "systemctl" in line), log
        assert any(line.startswith("systemctl --user stop odoo-wt@x.service") for line in log), log


@case
def a_worktree_dirtied_while_wt_stop_waits_is_kept():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        events, held, release, worktree = root / "events", root / "held", root / "release", root / ".worktrees/x"
        cmd, env = wt_test_project(root, "", psql_body=f'echo "psql $*" >> {events}\n')
        worktree.mkdir()
        git(worktree, "init", "-qb", "main")
        commit(worktree, "2026-10-01", {"acme_a/__manifest__.py": "{}\n"})
        procs = [hold_lock(root / ".worktrees/_env/x/test.lock", env, root / "holder.out", held, release)]
        try:
            wait_for(held.exists, "the test run")
            procs.append(start_bg(["bash", str(root / "scripts/wt-stop.sh"), "x"], env, root / "out"))
            wait_for(lambda: "waiting" in (root / "out").read_text(), "wt-stop's waiting line")
            (worktree / "acme_a/models.py").write_text("a\n")
        finally:
            release.touch()
            finish_bg(procs)
        out = (root / "out").read_text()
        assert procs[1].returncode == 0 and "DIRTY-kept" in out, out
        assert not events.exists() and (root / ".worktrees/_env/x/odoo.conf").exists(), out


@case
def a_run_still_waiting_when_the_env_is_stopped_exits_without_odoo():
    # the env removed (as wt-stop leaves it), removed and re-created (a wt-start of the slug
    # since), and its conf deleted by hand
    for then in ("rm -rf {env}", "rm -rf {env}; mkdir {env}; echo '[options]' > {env}/odoo.conf; : > {env}/test.lock",
                 "rm {env}/odoo.conf"):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            env_dir, held, release = root / ".worktrees/_env/x", root / "held", root / "release"
            cmd, env = wt_test_project(root, "")
            procs = [hold_lock(env_dir / "test.lock", env, root / "holder.out", held, release,
                               then.format(env=shlex.quote(str(env_dir))))]
            try:
                wait_for(held.exists, "the holder")
                procs.append(start_bg([*cmd, *TEE_RUN], env, root / "out"))
                wait_for(lambda: "waiting" in (root / "out").read_text(), "the run's waiting line")
            finally:
                release.touch()
                finish_bg(procs)
            out = (root / "out").read_text()
            assert procs[1].returncode == 1 and "env x was stopped while this run waited" in out, (then, out)
            assert read_argv(root / "odoo.argv") is None and read_argv(root / "psql.argv") is None, (then, out)


@case
def a_run_that_cannot_take_the_env_lock_says_so_without_odoo():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        cmd, env = wt_test_project(root, "")
        (root / ".worktrees/_env/x/test.lock").mkdir()
        proc = subprocess.run([*cmd, *TEE_RUN], capture_output=True, text=True, env=env, timeout=20)
        out = proc.stdout + proc.stderr
        assert proc.returncode == 1 and "cannot take" in out and "was stopped" not in out, out
        assert read_argv(root / "odoo.argv") is None and read_argv(root / "psql.argv") is None, out


def stop_project(root):
    """wt_test_project with psql and systemctl logging to root/events; returns wt-stop's command,
    its environment and the events file."""
    events = root / "events"
    _cmd, env = wt_test_project(root, "", psql_body=f'echo "psql $*" >> {events}\n',
                                systemctl_body=f'echo "systemctl $*" >> {events}\n')
    return ["bash", str(root / "scripts/wt-stop.sh"), "x"], env, events


@case
def wt_stop_waits_again_for_a_run_on_an_env_recreated_while_it_waited():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        env_dir, out = root / ".worktrees/_env/x", root / "stop.out"
        held, release, recreated = root / "held", root / "release", root / "recreated"
        held_new, release_new = root / "held_new", root / "release_new"
        cmd, env, events = stop_project(root)
        quoted = shlex.quote(str(env_dir))
        # the old env's run lets go only once a run on the re-created env holds the new lock
        procs = [hold_lock(env_dir / "test.lock", env, root / "old.out", held, release,
                           f"rm -rf {quoted}; mkdir {quoted}; touch {recreated}; "
                           f"while [ ! -e {held_new} ]; do sleep 0.02; done")]
        try:
            wait_for(held.exists, "the old env's run")
            procs.append(start_bg(cmd, env, out))
            wait_for(lambda: "waiting" in out.read_text(), "wt-stop's waiting line")
            release.touch()
            wait_for(recreated.exists, "the re-created env")
            procs.append(hold_lock(env_dir / "test.lock", env, root / "new.out", held_new, release_new))
            wait_for(lambda: out.read_text().count("waiting for it to end") == 2, "wt-stop's wait on the new env")
            assert env_dir.is_dir() and not events.exists(), (out.read_text(), env_dir.is_dir())
        finally:
            release.touch()
            held_new.touch()
            release_new.touch()
            finish_bg(procs)
        text = out.read_text()
        assert procs[1].returncode == 0 and "STATUS: removed" in text and not env_dir.exists(), text
        assert f"(pid {procs[2].pid}: flock " in text and "was removed while" not in text, text


@case
def wt_stop_whose_env_is_removed_while_it_waits_says_so_and_finishes():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        env_dir, out, held, release = root / ".worktrees/_env/x", root / "stop.out", root / "held", root / "release"
        cmd, env, events = stop_project(root)
        procs = [hold_lock(env_dir / "test.lock", env, root / "holder.out", held, release,
                           f"rm -rf {shlex.quote(str(env_dir))}")]
        try:
            wait_for(held.exists, "the test run")
            procs.append(start_bg(cmd, env, out))
            wait_for(lambda: "waiting" in out.read_text(), "wt-stop's waiting line")
        finally:
            release.touch()
            finish_bg(procs)
        text = out.read_text()
        assert procs[1].returncode == 0 and "env x was removed while this waited" in text, text
        assert "STATUS: removed (x)" in text and not env_dir.exists() and "systemctl --user stop" in events.read_text()


@case
def wt_stop_that_cannot_lock_the_env_leaves_it_and_its_service():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        cmd, env, events = stop_project(root)
        (root / ".worktrees/_env/x/test.lock").mkdir()
        run = start_bg(cmd, env, root / "out")
        finish_bg([run], timeout=20)
        out = (root / "out").read_text()
        assert run.returncode == 1 and "cannot take" in out and "STATUS: removed" not in out, out
        assert (root / ".worktrees/_env/x/odoo.conf").exists() and not events.exists(), out


@case
def wt_stop_whose_lock_wait_fails_with_the_env_gone_removes_the_rest():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        cmd, env, events = stop_project(root)
        env_dir, flock = root / ".worktrees/_env/x", root / "bin/flock"
        # the lock looks held, and its wait fails once something else removed the env
        flock.write_text(f'#!/usr/bin/env bash\ncase "$*" in\n  "-n 9") exit 1 ;;\n'
                         f'  9) rm -rf {shlex.quote(str(env_dir))}; exit 1 ;;\nesac\n'
                         f'exec {shlex.quote(shutil.which("flock"))} "$@"\n')
        flock.chmod(0o755)
        run = start_bg(cmd, env, root / "out")
        finish_bg([run], timeout=20)
        out = (root / "out").read_text()
        assert run.returncode == 0 and "STATUS: removed (x)" in out and "cannot take" not in out, out
        assert not env_dir.exists() and "systemctl --user stop odoo-wt@x.service" in events.read_text(), out


@case
def a_symlinked_test_lock_is_taken_by_a_run():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        cmd, env = wt_test_project(root, "")
        (root / ".worktrees/_env/x/test.lock").symlink_to(root / "elsewhere.lock")
        run = start_bg([*cmd, *EXEC_RUN], env, root / "out")
        finish_bg([run], timeout=20)
        out = (root / "out").read_text()
        assert run.returncode == 0 and read_argv(root / "odoo.argv") is not None and "stopped" not in out, out


@case
def a_run_wrapped_in_flock_on_a_symlinked_test_lock_runs_under_it():
    # flock(1) locks what the link points to: compared with the link's own inode the run
    # did not see its wrapper holding the lock and waited on it forever
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        cmd, env = wt_test_project(root, f'printf "%s\\n" {shlex.quote(RESULT.format(0, 1))} >&2\n')
        lock = root / ".worktrees/_env/x/test.lock"
        lock.symlink_to(root / "elsewhere.lock")
        run = start_bg(["flock", str(lock), *cmd, *TEE_RUN], env, root / "out")
        finish_bg([run], timeout=20)
        out = (root / "out").read_text()
        assert run.returncode == 0 and "running under its lock" in out and "waiting" not in out, out


@case
def the_holder_of_a_symlinked_test_lock_is_named():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        held, release = root / "held", root / "release"
        cmd, env = wt_test_project(root, f'printf "%s\\n" {shlex.quote(RESULT.format(0, 1))} >&2\n')
        lock = root / ".worktrees/_env/x/test.lock"
        lock.symlink_to(root / "elsewhere.lock")
        procs = [hold_lock(lock, env, root / "holder.out", held, release)]
        try:
            wait_for(held.exists, "the hand-run holder")
            procs.append(start_bg([*cmd, *TEE_RUN], env, root / "out"))
            wait_for(lambda: "waiting" in (root / "out").read_text(), "the run's waiting line")
        finally:
            release.touch()
            finish_bg(procs)
        out = (root / "out").read_text()
        assert procs[1].returncode == 0 and f"(pid {procs[0].pid}: flock {lock} sh -c" in out, out


@case
def a_symlinked_test_lock_is_taken_by_wt_stop():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        cmd, env, _events = stop_project(root)
        env_dir, target = root / ".worktrees/_env/x", root / "elsewhere.lock"
        (env_dir / "test.lock").symlink_to(target)
        run = start_bg(cmd, env, root / "out")
        finish_bg([run], timeout=20)
        out = (root / "out").read_text()
        assert run.returncode == 0 and "STATUS: removed (x)" in out and not env_dir.exists(), out
        assert target.is_file(), "the link removed, not what it points to"


@case
def wt_stop_waits_for_a_restore_building_the_env():
    # it dropped <DB>_restoring and removed the env dir under a restore in flight
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        cmd, env, events = stop_project(root)
        env_dir, held, release, out = root / ".worktrees/_env/x", root / "held", root / "release", root / "out"
        procs = [hold_lock(env_dir / "start.lock", env, root / "holder.out", held, release)]
        try:
            wait_for(held.exists, "the restore")
            procs.append(start_bg(cmd, env, out))
            wait_for(lambda: "waiting" in out.read_text(), "wt-stop's waiting line")
            assert not events.exists() and env_dir.is_dir(), out.read_text()
        finally:
            release.touch()
            finish_bg(procs)
        text = out.read_text()
        assert procs[1].returncode == 0 and "STATUS: removed (x)" in text and not env_dir.exists(), text
        assert f"start.lock (pid {procs[0].pid}: flock " in text and "then stopping the env" in text, text
        assert "DROP DATABASE IF EXISTS wt_x_restoring" in events.read_text(), text


@case
def wt_stop_drops_the_db_a_start_it_waited_for_recorded():
    # the name was read before the wait: the old one was dropped, the new DB left behind
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        cmd, env, events = stop_project(root)
        env_dir, held, release, out = root / ".worktrees/_env/x", root / "held", root / "release", root / "out"
        procs = [hold_lock(env_dir / "start.lock", env, root / "holder.out", held, release,
                           f"echo wt_x_102826 > {shlex.quote(str(env_dir / 'db'))}")]
        try:
            wait_for(held.exists, "the start")
            procs.append(start_bg(cmd, env, out))
            wait_for(lambda: "waiting" in out.read_text(), "wt-stop's waiting line")
        finally:
            release.touch()
            finish_bg(procs)
        log = events.read_text()
        assert procs[1].returncode == 0 and "DROP DATABASE IF EXISTS wt_x_102826_restoring" in log, log
        assert "wt_x_restoring" not in log, log


@case
def wt_stop_moves_the_env_aside_before_deleting_it():
    # deleted in place, its lock files went first and a newcomer took fresh ones mid-teardown
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        cmd, env, _events = stop_project(root)
        env_dir, mid, release, out = root / ".worktrees/_env/x", root / "mid", root / "release", root / "out"
        rm = root / "bin/rm"
        rm.write_text(f'#!/usr/bin/env bash\ncase "$*" in *.stopping-x-*) touch {mid}\n'
                      f'  while [ ! -e {release} ]; do sleep 0.02; done ;; esac\nexec {shutil.which("rm")} "$@"\n')
        rm.chmod(0o755)
        run = start_bg(cmd, env, out)
        try:
            wait_for(mid.exists, "the deletion")
            assert not env_dir.exists() and len(list(env_dir.parent.glob(".stopping-x-*"))) == 1, out.read_text()
        finally:
            release.touch()
            finish_bg([run])
        assert run.returncode == 0 and not list(env_dir.parent.iterdir()), list(env_dir.parent.iterdir())


@case
def wt_stop_without_an_env_dir_removes_the_rest_without_a_lock():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        cmd, env, events = stop_project(root)
        shutil.rmtree(root / ".worktrees/_env/x")
        run = start_bg(cmd, env, root / "out")
        finish_bg([run], timeout=20)
        lines = (root / "out").read_text().splitlines()
        assert run.returncode == 0 and len(lines) == 1 and lines[0].startswith("STATUS: removed (x)"), lines
        assert not (root / ".worktrees/_env/x").exists() and "DROP DATABASE IF EXISTS wt_x_restoring" in events.read_text()


@case
def a_caller_holding_the_env_lock_does_not_deadlock_wt_stop_or_the_restore():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        cmd, env = wt_test_project(root, "")
        lock = root / ".worktrees/_env/x/test.lock"
        run = start_bg(["flock", str(lock), "bash", str(root / "scripts/wt-stop.sh"), "x"], env, root / "out")
        finish_bg([run], timeout=20)
        out = (root / "out").read_text()
        assert run.returncode == 0 and "running under its lock" in out and "STATUS: removed" in out, out
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        cmd, env, _calls = wt_restore_project(root, exists="1", neutralized=False)
        run = start_bg(["flock", str(root / ".worktrees/_env/x/test.lock"), *cmd], env, root / "out")
        finish_bg([run], timeout=20)
        out = (root / "out").read_text()
        assert run.returncode == 0 and "running under its lock" in out and "db-ready" in out, out


@case
def a_restore_waits_for_another_holding_the_start_lock_and_runs_silently_under_its_caller():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        cmd, env, calls = wt_restore_project(root)
        held, release, out = root / "held", root / "release", root / "out"
        procs = [hold_lock(root / ".worktrees/_env/x/start.lock", env, root / "holder.out", held, release)]
        try:
            wait_for(held.exists, "the other start")
            procs.append(start_bg(cmd, env, out))
            wait_for(lambda: "waiting" in out.read_text(), "the restore's waiting line")
            assert not calls.exists(), calls.read_text()
        finally:
            release.touch()
            finish_bg(procs)
        text = out.read_text()
        assert procs[1].returncode == 0 and "then restoring wt_x" in text and "db-ready" in text, text
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        cmd, env, _calls = wt_restore_project(root)
        run = start_bg(["flock", str(root / ".worktrees/_env/x/start.lock"), *cmd], env, root / "out")
        finish_bg([run], timeout=20)
        text = (root / "out").read_text()
        assert run.returncode == 0 and "db-ready" in text and "start.lock" not in text, text


@case
def a_restore_of_an_env_that_does_not_exist_says_so():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        cmd, env, calls = wt_restore_project(root)
        shutil.rmtree(root / ".worktrees/_env/x")
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=20)
        out = proc.stdout + proc.stderr
        assert proc.returncode == 1 and "no env x" in out and not calls.exists(), out


def env_repo_stop_project(root):
    """stop_project in a project that is a git repo with an env-repo worktree for slug x."""
    cmd, env, events = stop_project(root)
    git(root, "init", "-qb", "main")
    (root / "README.md").write_text("a\n")
    git(root, "add", "README.md")
    git(root, "commit", "-qm", "init")
    git(root, "worktree", "add", "-qb", "x", str(root / ".worktrees/_envrepo/x"))
    return cmd, env, events


@case
def wt_stop_removes_a_clean_env_repo_worktree_and_keeps_its_branch():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        cmd, env, _events = env_repo_stop_project(root)
        tree = root / ".worktrees/_envrepo/x"
        git(tree, "commit", "-q", "--allow-empty", "-m", "the task's script")
        run = subprocess.run(cmd, capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, timeout=60)
        out = run.stdout + run.stderr
        assert run.returncode == 0 and "STATUS: removed (x) — worktree + env-repo worktree" in out, out
        assert not tree.exists() and not (root / ".worktrees/_env/x").exists(), out
        log = subprocess.run(["git", "-C", str(root), "log", "-1", "--format=%s", "x"],
                             capture_output=True, text=True, check=True).stdout.strip()
        assert log == "the task's script", "the branch keeps its commits"


@case
def wt_stop_keeps_everything_while_the_env_repo_worktree_is_dirty():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        cmd, env, events = env_repo_stop_project(root)
        tree = root / ".worktrees/_envrepo/x"
        (tree / "scripts").mkdir()
        (tree / "scripts/fix.py").write_text("pass\n")
        run = subprocess.run(cmd, capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, timeout=60)
        out = run.stdout + run.stderr
        assert run.returncode == 0 and f"DIRTY-kept ({tree})" in out, out
        assert (tree / "scripts/fix.py").exists() and (root / ".worktrees/_env/x/odoo.conf").exists(), out
        assert "DROP DATABASE" not in (events.read_text() if events.exists() else ""), out


@case
def wt_list_shows_the_env_repo_worktree_state():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        _cmd, env, _events = env_repo_stop_project(root)
        (root / "bin/systemctl").write_text("#!/usr/bin/env bash\necho inactive\nexit 3\n")  # as systemctl does
        (root / "scripts/wt-list.sh").write_text(render_script(SRC.with_name("wt-list.sh.jinja")))
        rows = {}
        for when in ("clean", "dirty"):
            if when == "dirty":
                (root / ".worktrees/_envrepo/x/draft.py").write_text("wip\n")
            run = subprocess.run(["bash", str(root / "scripts/wt-list.sh")], capture_output=True, text=True,
                                 env=env, stdin=subprocess.DEVNULL, timeout=60)
            assert run.returncode == 0, run.stdout + run.stderr
            rows[when] = [line.split() for line in run.stdout.splitlines()]
        assert rows["clean"][0][-2:] == ["WORKTREE", "ENVREPO"], rows
        assert rows["clean"][1] == ["x", "?", "wt_x", "inactive", "none", "clean"], rows
        assert rows["dirty"][1][-1] == "dirty", rows


@case
def an_update_of_a_module_the_db_lacks_stops_before_odoo():
    rc, out, psql, odoo = run_wt_test(["-u", "acme_new", "--test-enable", "--test-tags", "/acme_new"],
                                      missing="acme_new uninstalled\n")
    assert (rc, psql_vars(psql).get("modules"), odoo) == (2, "acme_new", None), (rc, psql, odoo, out)
    assert "acme_new is uninstalled in wt_x" in out and "-i acme_new" in out, out


@case
def every_update_and_init_spelling_reaches_the_module_check():
    # optparse keeps the last of a repeated option, so Odoo updates d and installs g only
    for major in ("16", "19"):
        for args, expected in ((["-u", "a,b", "--test-tags=/a"], ("a,b", "")),
                               (["--update=c", "-i", "e", "--test-enable"], ("c", "e")),
                               (["-ud", "--init=f", "--test-enable"], ("d", "f")),
                               (["-u", "a,b", "--update=c", "-ud", "-i", "e", "--init=f", "-ig", "--test-tags=/a"],
                                ("d", "g")),
                               (["--upd", "h", "--in", "k", "--test-en"], ("h", "k"))):
            _rc, out, psql, _odoo = run_wt_test(args, odoo_out=RESULT.format(0, 3), major=major)
            found = psql_vars(psql)
            assert (found.get("modules"), found.get("init", "")) == expected, (major, args, psql, out)


@case
def a_value_of_another_option_is_no_update():
    # --test-tags takes the next argument whatever it looks like; Odoo updates nothing here
    for args in (["--test-tags", "-utm"], ["--log-handler", "-u:INFO", "--test-enable"], ["-d", "-ix", "--test-enable"]):
        rc, out, psql, odoo = run_wt_test(args, odoo_out=RESULT.format(0, 2))
        assert (rc, psql, odoo is not None) == (0, None, True), (args, rc, psql, out)
        assert "WARNING" not in out and "has a space" not in out, (args, out)


@case
def odoo_19_test_tags_short_option_counts_its_tests():
    rc, out, _psql, odoo = run_wt_test(["-t", "/acme_mod"], odoo_out=RESULT.format(0, 0), major="19")
    assert rc == 3 and "executed 0 tests" in out and odoo is not None, (rc, out)
    rc, out, _psql, _odoo = run_wt_test(["-t", "/acme_mod"], odoo_out=RESULT.format(0, 0), major="16")
    assert rc == 0 and "do not read as Odoo's options (no such option: -t)" in out, (rc, out)


@case
def odoo_19_without_demo_takes_no_value_before_an_option():
    _rc, out, psql, _odoo = run_wt_test(["--without-demo", "-u", "acme_mod"], major="19")
    assert psql_vars(psql).get("modules") == "acme_mod", (psql, out)
    # Odoo 16 reads the -u as --without-demo's value, so nothing is updated
    _rc, out, psql, _odoo = run_wt_test(["--without-demo", "-u", "acme_mod"], major="16")
    assert psql is None, (psql, out)


@case
def arguments_odoo_refuses_skip_the_checks_and_reach_odoo():
    for args in (["--no-such-option", "--test-enable"], ["--test-enable", "-u"]):
        rc, out, psql, odoo = run_wt_test(args, odoo_out=RESULT.format(0, 0), odoo_rc=2)
        assert (rc, psql, odoo is not None) == (2, None, True), (args, rc, psql, out)
        assert "do not read as Odoo's options" in out and "executed 0 tests" not in out, (args, out)


@case
def without_a_readable_server_help_the_checks_are_skipped_with_a_warning():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        cmd, env = wt_test_project(root, f'printf "%s\\n" {shlex.quote(RESULT.format(0, 0))} >&2\n')
        odoo = root / ".nix-profile/bin/odoo"
        odoo.write_text(odoo.read_text().replace("|| exec cat", "|| exit 1 #"))
        proc = subprocess.run([*cmd, "-u", "acme_mod", "--test-enable"], capture_output=True, text=True, env=env)
        out = proc.stdout + proc.stderr
        assert proc.returncode == 0 and "no --update among the options" in out, out
        assert read_argv(root / "psql.argv") is None and read_argv(root / "odoo.argv") is not None, out


@case
def the_module_check_skips_psqlrc_and_stops_on_a_sql_error():
    _rc, out, psql, _odoo = run_wt_test(["-u", "acme_mod"])
    assert "-X" in (psql or []) and psql_vars(psql).get("ON_ERROR_STOP") == "1", (psql, out)


@case
def unreadable_module_states_stop_before_odoo():
    rc, out, _psql, odoo = run_wt_test(["-u", "acme_mod", "--test-enable"], psql_rc=2)
    assert (rc, odoo) == (1, None), (rc, odoo, out)
    assert "cannot read module states from wt_x" in out, out


@case
def a_module_list_with_a_space_is_refused_with_its_spelling():
    for args in (["-u", "acme_sale, acme_stock"], ["-u", "acme_sale", "-i", "acme_x ,acme_y"]):
        rc, out, psql, odoo = run_wt_test([*args, "--test-enable"])
        assert (rc, psql, odoo) == (2, None, None), (args, rc, psql, odoo, out)
        assert "write " + args[-1].replace(" ", "") in out, out


STATE_FIXTURE = ("(VALUES ('a', 'installed'), ('b', 'to upgrade'), ('c', 'to install'), ('d', 'uninstalled'))"
                 " AS ir_module_module(name, state)")


def missing_modules(modules, init=""):
    """The module-state query of the rendered wt-test.sh, run on the live server
    against STATE_FIXTURE in place of ir_module_module (read-only)."""
    sql = re.search(r"<<'SQL'\n(.*?\n)SQL\n", render_script(TEST_SRC), re.S).group(1)
    assert sql.count("ir_module_module") == 1, sql
    psql = ["psql", "-X", "-h", "localhost", "-p", str(BASE["postgres_port"]), "-U", BASE["db_user"], "-d", "postgres"]
    # read-only whatever the query turns into: it runs on whatever server answers there
    env = {**os.environ, "PGPASSWORD": BASE["db_password"], "PGCONNECT_TIMEOUT": "3",
           "PGOPTIONS": "-c default_transaction_read_only=on"}
    probe = shutil.which("psql") and subprocess.run([*psql, "-c", "SELECT 1"], capture_output=True, text=True, env=env)
    if not probe or probe.returncode:
        raise Skip(f"no PostgreSQL on localhost:{BASE['postgres_port']}")
    proc = subprocess.run([*psql, "-tA", "-F", " ", "-v", "ON_ERROR_STOP=1", "-v", f"modules={modules}",
                           "-v", f"init={init}"], input=sql.replace("ir_module_module", STATE_FIXTURE),
                          capture_output=True, text=True, env=env)
    assert proc.returncode == 0, proc.stderr
    return set(proc.stdout.splitlines())


@case
def the_module_check_passes_what_an_update_run_loads():
    found = missing_modules("a,b,c,d,e,all,")
    assert found == {"d uninstalled", "e absent"}, found
    found = missing_modules("a,d,e", init="d,e")
    assert found == set(), found


@case
def a_test_run_that_ran_no_test_fails():
    for selector in (["--test-tags", "acme_mod.test_file"], ["--test-tags=/acme_mod:NoSuchClass"]):
        rc, out, _psql, odoo = run_wt_test(selector, odoo_out=RESULT.format(0, 0))
        assert rc == 3 and odoo is not None, (selector, rc, odoo, out)
        assert "executed 0 tests" in out, out


@case
def a_test_run_with_tests_keeps_odoos_exit_code_and_output():
    rc, out, _psql, _odoo = run_wt_test(["-u", "acme_mod", "--test-enable"], odoo_out=RESULT.format(0, 12))
    assert rc == 0 and RESULT.format(0, 12) in out, (rc, out)
    rc, out, _psql, _odoo = run_wt_test(["--test-tags", "/acme_mod"], odoo_out=RESULT.format(2, 12), odoo_rc=1)
    assert rc == 1 and "executed 0 tests" not in out, (rc, out)


@case
def the_test_summary_shows_under_any_log_level():
    _rc, out, _psql, odoo = run_wt_test(["--test-enable", "--log-level=warn"], odoo_out=RESULT.format(0, 4))
    assert ("--log-handler", "odoo.tests.result:INFO") in itertools.pairwise(odoo), (odoo, out)


@case
def a_test_file_alone_is_not_counted():
    rc, out, _psql, odoo = run_wt_test(["--test-file", "acme_mod/tests/test_x.py"])
    assert rc == 0 and odoo is not None and "WARNING" not in out and "0 tests" not in out, (rc, odoo, out)


@case
def a_test_run_without_a_summary_keeps_odoos_exit_code_and_warns():
    rc, out, _psql, _odoo = run_wt_test(["--test-enable", "--logfile=/var/tmp/own.log"])
    assert rc == 0 and "no test summary in the output" in out and "executed 0 tests" not in out, (rc, out)


@case
def a_crashed_test_run_keeps_its_exit_code():
    rc, out, _psql, _odoo = run_wt_test(["--test-enable"], odoo_out="CRITICAL wt_x odoo.service.server: boom",
                                        odoo_rc=255)
    assert rc == 255 and "executed 0 tests" not in out and "WARNING" not in out, (rc, out)


@case
def a_signal_to_a_test_run_reaches_odoo():
    # the tee path: a foreground pipeline left Odoo running when the wrapper's pid got the signal
    for sig, code in ((signal.SIGTERM, 143), (signal.SIGHUP, 129)):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            started, got = root / "started", root / "got"
            cmd, env = wt_test_project(root, f"trap 'echo \"$$ TERM\" > {got}; exit 143' TERM\n"
                                             f"echo $$ > {started}\nwhile :; do sleep 0.02; done\n")
            run = start_bg([*cmd, *TEE_RUN], env, root / "out")
            try:
                wait_for(lambda: started.exists() and started.read_text().strip(), "the run's Odoo")
                os.kill(run.pid, sig)
            finally:
                finish_bg([run], timeout=20)
            odoo_pid = started.read_text().strip()
            assert run.returncode == code, (sig, run.returncode, (root / "out").read_text())
            assert got.exists() and got.read_text().split() == [odoo_pid, "TERM"], sig
            assert not pathlib.Path(f"/proc/{odoo_pid}").exists(), "Odoo outlived the wrapper"


ODOO_SIGNALS = """\
import os, signal, sys, time
got = []
def note(sig, _frame):
    got.append(signal.Signals(sig).name)
    open(sys.argv[1], "a").write(signal.Signals(sig).name + "\\n")
signal.signal(signal.SIGINT, note)  # as Odoo's ThreadedServer re-arms it
signal.signal(signal.SIGTERM, note)
open(sys.argv[2], "w").write(str(os.getpid()))
while not got:
    time.sleep(0.02)
time.sleep(0.5)  # a second signal, if one comes, is noted too
sys.exit(130)
"""


@case
def a_terminal_ctrl_c_reaches_odoo_once():
    # the tty sends INT to the whole group; Odoo has it, and a forwarded TERM on top was its
    # second signal, a forced shutdown
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        got, started = root / "got", root / "started"
        (root / "odoo_signals.py").write_text(ODOO_SIGNALS)
        cmd, env = wt_test_project(root, f"exec python3 {root / 'odoo_signals.py'} {got} {started}\n")
        run = start_bg([*cmd, *TEE_RUN], env, root / "out")
        try:
            wait_for(lambda: started.exists() and started.read_text().strip(), "the run's Odoo")
            os.killpg(run.pid, signal.SIGINT)
        finally:
            finish_bg([run], timeout=20)
        assert run.returncode == 130 and got.read_text().split() == ["SIGINT"], (run.returncode, got.read_text())


@case
def a_signalled_run_ends_though_a_browser_holds_its_output():
    # Odoo 16 leaves its test browser up when the run is stopped, the browser holding stdout
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        started, orphan = root / "started", root / "orphan"
        cmd, env = wt_test_project(root, f"trap 'sleep 60 9>&- & echo $! > {orphan}; exit 143' TERM\n"
                                         f"echo $$ > {started}\nwhile :; do sleep 0.02; done\n")
        run = start_bg([*cmd, *TEE_RUN], env, root / "out")
        try:
            wait_for(lambda: started.exists() and started.read_text().strip(), "the run's Odoo")
            os.kill(run.pid, signal.SIGTERM)
            finish_bg([run], timeout=20)
            free = subprocess.run(["flock", "-n", str(root / ".worktrees/_env/x/test.lock"), "true"]).returncode
            assert run.returncode == 143 and free == 0, (run.returncode, free, (root / "out").read_text())
        finally:
            if orphan.exists():
                try:
                    os.kill(int(orphan.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass


@case
def a_signal_before_odoo_starts_stops_the_run_without_it():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        cmd, env = wt_test_project(root, f'printf "%s\\n" {shlex.quote(RESULT.format(0, 1))} >&2\n')
        mkfifo = root / "bin/mkfifo"
        mkfifo.write_text(f'#!/usr/bin/env bash\nkill -TERM $PPID\nexec {shlex.quote(shutil.which("mkfifo"))} "$@"\n')
        mkfifo.chmod(0o755)
        run = start_bg([*cmd, *TEE_RUN], env, root / "out")
        finish_bg([run], timeout=20)
        free = subprocess.run(["flock", "-n", str(root / ".worktrees/_env/x/test.lock"), "true"]).returncode
        assert (run.returncode, read_argv(root / "odoo.argv"), free) == (143, None, 0), (root / "out").read_text()


@case
def a_run_without_tests_is_not_counted():
    rc, out, psql, odoo = run_wt_test(["-u", "all"])
    assert (rc, psql_vars(psql).get("modules"), odoo is not None) == (0, "all", True), (rc, psql, odoo, out)


@case
def every_line_of_the_block_is_one_option_or_a_section():
    for answers in ({}, {"use_queue_job": False}, {"odoo_version": "20.0", "odoo_major": "20"}):
        block = re.search(r'<<EOF\n(.*?)\nEOF\n', render(**answers), re.S).group(1).splitlines()
        assert block[0] == "[options]", block[0]
        assert all(re.fullmatch(r"[a-z_]+ = \S.*|\[[a-z_]+\]|", line) for line in block[1:]), (answers, block)



def run_wt_start_from(where, allow=False):
    """Run the rendered wt-start.sh for slug `x` with the cwd at `where` under the project.

    wt-bootstrap.sh is a stub that exits 7, so a run that got past the Claude-worktree
    check stops there. Returns (exit code, stdout+stderr).
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        scripts, cwd = root / "scripts", root / where
        for d in (scripts, cwd):
            d.mkdir(parents=True, exist_ok=True)
        for src in (SRC, SRC.with_name("wt-common.sh.jinja")):
            (scripts / src.name.removesuffix(".jinja")).write_text(render_script(src))
        (scripts / "wt-bootstrap.sh").write_text("echo BOOTSTRAP; exit 7\n")
        env = {**os.environ, "HOME": tmp, "WT_TEST_PROJECT_DIR": tmp}
        env.pop("WT_ALLOW_CLAUDE_WORKTREE", None)
        if allow:
            env["WT_ALLOW_CLAUDE_WORKTREE"] = "1"
        proc = subprocess.run(["bash", str(scripts / "wt-start.sh"), "x"], cwd=cwd,
                              capture_output=True, text=True, env=env)
        return proc.returncode, proc.stdout + proc.stderr


@case
def a_claude_worktree_session_is_refused_before_anything_is_made():
    # the desktop app's worktree guard refuses Edit/Write in the base checkout, and the
    # env's code worktree would be there: the session would get an env it cannot edit
    rc, out = run_wt_start_from(".claude/worktrees/w/src")
    assert rc == 4 and "BOOTSTRAP" not in out, (rc, out)
    assert "Claude worktree" in out and "/.worktrees/x" in out and "WT_ALLOW_CLAUDE_WORKTREE=1" in out, out


@case
def the_base_checkout_and_its_addons_worktrees_go_through():
    for where in (".", "src/acme-addons", ".worktrees/x", ".claude/skills"):
        rc, out = run_wt_start_from(where)
        assert (rc, out.strip()) == (7, "BOOTSTRAP"), (where, rc, out)


@case
def the_override_lets_a_claude_worktree_through():
    rc, out = run_wt_start_from(".claude/worktrees/w", allow=True)
    assert (rc, out.strip()) == (7, "BOOTSTRAP"), (rc, out)


def run_wt_start(root, interface=None, argv=("x",), extra_env=None):
    """Run the rendered wt-start.sh with `argv` (slug `x`) in project `root` (made on the first call) with
    WT_HTTP_INTERFACE=`interface` in .env when given; its helper scripts are no-ops, the worktree
    and port exist, psql and systemctl are stubs, and so is git for the addons repo (the project's
    own repo gets the real one). Returns (exit code, stdout+stderr, conf path)."""
    scripts, bin_dir, env_dir = root / "scripts", root / "bin", root / ".worktrees/_env/x"
    if not scripts.exists():
        for d in (scripts, bin_dir, env_dir, root / "backup"):
            d.mkdir(parents=True)
        (root / "backup/odoo.dump").write_bytes(b"")
        (env_dir / "port").write_text("1700\n")
        for src in (SRC, COMMON_SRC):
            (scripts / src.name.removesuffix(".jinja")).write_text(render_script(src))
        for name in ("wt-bootstrap.sh", "wt-link.sh", "wt-restore.sh"):
            (scripts / name).write_text("exit 0\n")
        stubs = {"git": f'case "$*" in "-C {root}/src/acme-addons "*) ;; *) exec {shlex.quote(shutil.which("git"))} "$@" ;; esac\n'
                        f'case "$*" in *"worktree list"*) echo "worktree {root}/.worktrees/x" ;; esac\n',
                 "psql": "", "systemctl": ""}
        for name, body in stubs.items():
            (bin_dir / name).write_text(f"#!/usr/bin/env bash\n{body}")
            (bin_dir / name).chmod(0o755)
    (root / ".env").write_text(f"WT_HTTP_INTERFACE={interface}\n" if interface else "")
    proc = subprocess.run(["bash", str(scripts / "wt-start.sh"), *argv], cwd=root, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL,
                          env={**os.environ, "HOME": str(root), "WT_TEST_PROJECT_DIR": str(root),
                               "PATH": f"{bin_dir}:{os.environ['PATH']}", **(extra_env or {})})
    return proc.returncode, proc.stdout + proc.stderr, env_dir / "odoo.conf"


@case
def a_loopback_session_keeps_the_dev_conf():
    for interface in (None, "::1", "127.0.0.1", "localhost", "127.8.9.10"):
        with tempfile.TemporaryDirectory() as tmp:
            rc, out, conf_path = run_wt_start(pathlib.Path(tmp), interface)
            assert rc == 0, (interface, out)
            options = parse_conf(conf_path.read_text())["options"]
            assert options["admin_passwd"] == "odoo" and options.keys() == conf(render()).keys(), (interface, options)
            assert options["http_interface"] == (interface or "::1"), options


@case
def a_session_beyond_loopback_gets_a_random_master_password_and_no_database_manager():
    for interface in ("0.0.0.0", "::", "10.0.0.7"):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            passwords = []
            for _run in range(2):
                rc, out, conf_path = run_wt_start(root, interface)
                assert rc == 0, (interface, out)
                found = parse_conf(conf_path.read_text())
                assert found["options"].get("list_db") == "False", (interface, found)
                assert "list_db" not in found.get("queue_job", {}), found
                passwords.append(found["options"]["admin_passwd"])
            assert all(re.fullmatch(r"[A-Za-z0-9]{24,}", password) for password in passwords), passwords
            assert passwords[0] != passwords[1], passwords


@case
def the_session_conf_is_private_even_when_it_existed():
    # it carries db_password, and beyond loopback the master password
    for interface in (None, "0.0.0.0"):
        with tempfile.TemporaryDirectory() as tmp:
            modes = []
            for _run in range(2):
                rc, out, conf_path = run_wt_start(pathlib.Path(tmp), interface)
                assert rc == 0, (interface, out)
                modes.append(conf_path.stat().st_mode & 0o777)
                conf_path.chmod(0o644)  # as a wt-start of before left it
            assert modes == [0o600, 0o600], (interface, [oct(mode) for mode in modes])


@case
def a_failed_conf_write_keeps_the_previous_conf():
    # written in place, a failure before the list_db line left a conf with the database manager on
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        rc, out, conf_path = run_wt_start(root, "0.0.0.0")
        assert rc == 0, out
        before = conf_path.read_text()
        sed = root / "bin/sed"
        sed.write_text(f'#!/usr/bin/env bash\n[ "$1" = -i ] && exit 1\nexec {shlex.quote(shutil.which("sed"))} "$@"\n')
        sed.chmod(0o755)
        rc, out, conf_path = run_wt_start(root, "0.0.0.0")
        assert rc != 0 and conf_path.read_text() == before, (rc, out)
        assert sorted(path.name for path in conf_path.parent.iterdir()) == ["data", "db", "dump", "odoo.conf", "port",
                                                                            "start.lock", "test.lock"], out


def newest(path):
    """Write an empty dump at `path`, its mtime a minute ahead so `ls -t` takes it first."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    later = time.time() + 60
    os.utime(path, (later, later))
    return path


@case
def a_dumps_day_names_the_db_and_a_recorded_name_wins():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        (root / "wt-common.sh").write_text(render_script(COMMON_SRC))

        def call(*args):
            return subprocess.run(["bash", "-c", 'source "$1"; shift; "$@"', "_", str(root / "wt-common.sh"), *args],
                                  capture_output=True, text=True, env={**os.environ, "WT_TEST_PROJECT_DIR": str(root)},
                                  check=True).stdout.strip()

        for dump, name in (("/b/odoo_2026-10-20-18-30-06.dump", "wt_kio_1_x_102026"),
                           ("odoo_2027-01-05-00-00-00.dump", "wt_kio_1_x_010527"),
                           ("/b/prod.dump", "wt_kio_1_x"), ("/b/odoo_2026-10-20.dump", "wt_kio_1_x")):
            assert call("dated_db_name", "kio-1-x", dump) == name, (dump, name)
        assert call("db_name_for", "kio-1-x") == "wt_kio_1_x"
        (root / ".worktrees/_env/kio-1-x").mkdir(parents=True)
        (root / ".worktrees/_env/kio-1-x/db").write_text("wt_kio_1_x_102026\n")
        assert call("db_name_for", "kio-1-x") == "wt_kio_1_x_102026"


@case
def a_new_env_is_named_after_its_dump_and_records_both():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        rc, out, conf_path = run_wt_start(root)
        assert rc == 0 and "DB: wt_x\n" in out, out
        dated = newest(root / "backup/odoo_2026-10-20-18-30-06.dump")
        rc, out, conf_path = run_wt_start(root)
        sections_ = parse_conf(conf_path.read_text())
        assert rc == 0 and "DB: wt_x_102026\n" in out and "URL: http://wt_x_102026.localhost:1700/web" in out, out
        assert sections_["options"]["db_name"] == "wt_x_102026", sections_
        assert sections_["queue_job"]["host"] == "wt_x_102026.localhost", sections_
        env_dir = conf_path.parent
        assert (env_dir / "db").read_text() == "wt_x_102026\n" and (env_dir / "dump").read_text() == f"{dated}\n"


@case
def wt_dump_names_the_dump_a_new_env_restores():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        elsewhere = newest(root / "elsewhere/odoo_2026-10-21-08-52-07.dump")
        rc, out, conf_path = run_wt_start(root, extra_env={"WT_DUMP": str(elsewhere)})
        assert rc == 0 and "DB: wt_x_102126\n" in out, out
        assert (conf_path.parent / "dump").read_text() == f"{elsewhere}\n"
        rc, out, _conf = run_wt_start(root, argv=("y",), extra_env={"WT_DUMP": str(root / "gone.dump")})
        assert rc != 0 and f"WT_DUMP={root / 'gone.dump'}: no such dump" in out, out
        assert not (root / ".worktrees/_env/y").exists(), out


@case
def an_env_whose_db_exists_keeps_its_name():
    # an older env (no db file, wt_x exists) and an env with a recorded name are reused as they are
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        run_wt_start(root)
        env_dir = root / ".worktrees/_env/x"
        (env_dir / "db").unlink()
        (env_dir / "dump").unlink()
        newest(root / "backup/odoo_2026-10-20-18-30-06.dump")
        (root / "bin/psql").write_text('#!/usr/bin/env bash\ncase "$*" in *pg_database*) echo 1 ;; esac\n')
        rc, out, conf_path = run_wt_start(root)
        assert rc == 0 and "DB: wt_x\n" in out and not (env_dir / "dump").exists(), out
        (env_dir / "db").write_text("wt_x_101326\n")
        rc, out, conf_path = run_wt_start(root)
        assert rc == 0 and "DB: wt_x_101326\n" in out, out
        assert parse_conf(conf_path.read_text())["options"]["db_name"] == "wt_x_101326"


@case
def a_relative_wt_dump_is_recorded_absolute_under_its_own_name():
    # wt-restore reads the recorded dump back, maybe from another cwd
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        elsewhere = newest(root / "elsewhere/odoo_2026-10-21-08-52-07.dump")
        rc, out, conf_path = run_wt_start(root, extra_env={"WT_DUMP": "elsewhere/odoo_2026-10-21-08-52-07.dump"})
        recorded = pathlib.Path((conf_path.parent / "dump").read_text().strip())
        assert rc == 0 and recorded.is_absolute() and recorded.resolve() == elsewhere.resolve(), (recorded, out)
        # a link keeps its own name, which carries the day
        link = root / "links/odoo_2026-10-22-01-00-00.dump"
        link.parent.mkdir()
        link.symlink_to(elsewhere)
        rc, out, _conf = run_wt_start(root, argv=("y",), extra_env={"WT_DUMP": "links/odoo_2026-10-22-01-00-00.dump"})
        recorded = pathlib.Path((root / ".worktrees/_env/y/dump").read_text().strip())
        assert rc == 0 and "DB: wt_y_102226\n" in out, out
        assert recorded.is_absolute() and recorded.name == link.name and recorded.resolve() == elsewhere.resolve()


@case
def wt_start_holds_the_start_lock_through_the_restore():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        run_wt_start(root)
        lock = root / ".worktrees/_env/x/start.lock"
        (root / "scripts/wt-restore.sh").write_text(f'flock -n {lock} true && echo "start lock free" || echo "start lock held"\n')
        rc, out, _conf = run_wt_start(root)
        assert rc == 0 and "start lock held" in out and "start.lock" not in out, out


@case
def git_gets_no_env_lock_fd():
    # a `git maintenance --auto` git detaches would hold an env lock after wt-start is gone
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        run_wt_start(root)
        git_stub = root / "bin/git"
        git_stub.write_text(git_stub.read_text().replace(
            "#!/usr/bin/env bash\n",
            '#!/usr/bin/env bash\nfor fd in 8 9; do [ ! -e /proc/$$/fd/$fd ] || echo "git got fd $fd" >&2; done\n', 1))
        rc, out, _conf = run_wt_start(root)
        assert rc == 0 and "git got fd" not in out, out


@case
def wt_starts_service_restart_waits_for_a_test_run():
    # the restart put the service on the DB beside a wt-test -u
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        run_wt_start(root)
        events, held, release, out = root / "events", root / "held", root / "release", root / "out"
        for name in ("psql", "systemctl"):
            (root / "bin" / name).write_text(f'#!/usr/bin/env bash\necho "{name} $*" >> {events}\n')
        env = {**os.environ, "HOME": str(root), "WT_TEST_PROJECT_DIR": str(root),
               "PATH": f"{root / 'bin'}:{os.environ['PATH']}"}
        procs = [hold_lock(root / ".worktrees/_env/x/test.lock", env, root / "holder.out", held, release)]
        try:
            wait_for(held.exists, "the test run")
            with open(out, "w") as sink:
                procs.append(subprocess.Popen(["bash", str(root / "scripts/wt-start.sh"), "x"], cwd=root,
                                              stdin=subprocess.DEVNULL, stdout=sink, stderr=subprocess.STDOUT,
                                              env=env, start_new_session=True))
            wait_for(lambda: "waiting" in out.read_text(), "wt-start's waiting line")
            text = events.read_text() if events.exists() else ""
            assert "report.url" not in text and "restart" not in text, text
        finally:
            release.touch()
            finish_bg(procs)
        text, log = out.read_text(), events.read_text()
        assert procs[1].returncode == 0 and "then restarting the session service" in text, text
        assert "report.url" in log and "systemctl --user restart odoo-wt@x.service" in log, log


def env_repo_project(tmp):
    """A project cloned from a bare origin whose default branch is main (README.md "a").
    Returns (project root, origin, a second clone to push from)."""
    origin, seed, root = tmp / "origin.git", tmp / "seed", tmp / "proj"
    git(tmp, "init", "-q", "--bare", "-b", "main", str(origin))
    git(tmp, "clone", "-q", str(origin), str(seed))
    git(seed, "checkout", "-qb", "main")
    commit(seed, "2026-10-01", {"README.md": "a\n"})
    git(seed, "push", "-q", "origin", "main")
    git(tmp, "clone", "-q", str(origin), str(root))
    return root, origin, seed


def head_of(tree):
    return subprocess.run(["git", "-C", str(tree), "rev-parse", "--abbrev-ref", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()


@case
def env_repo_flag_checks_out_the_project_repo_on_the_slug_off_origin():
    with tempfile.TemporaryDirectory() as tmp:
        root, _origin, seed = env_repo_project(pathlib.Path(tmp))
        commit(seed, "2026-10-02", {"README.md": "b\n"})   # origin moved on since the clone
        git(seed, "push", "-q", "origin", "main")
        commit(root, "2026-10-03", {"local.txt": "x\n"})   # a local main commit is not the base
        rc, out, _conf = run_wt_start(root, argv=("x", "--env-repo"))
        tree = root / ".worktrees/_envrepo/x"
        assert rc == 0 and f"ENVREPO: {tree}" in out.splitlines(), out
        assert head_of(tree) == "x" and (tree / "README.md").read_text() == "b\n", out
        assert not (tree / "local.txt").exists(), "branched off local main, not origin's"


@case
def an_env_repo_worktree_is_kept_and_reported_by_every_later_run():
    with tempfile.TemporaryDirectory() as tmp:
        root, _origin, _seed = env_repo_project(pathlib.Path(tmp))
        tree = root / ".worktrees/_envrepo/x"
        rc, out, _conf = run_wt_start(root, argv=("--env-repo", "x"))
        assert rc == 0 and tree.is_dir(), out
        (tree / "draft.py").write_text("wip\n")
        for argv in (("x", "--env-repo"), ("x",)):
            rc, out, _conf = run_wt_start(root, argv=argv)
            assert rc == 0 and f"ENVREPO: {tree}" in out.splitlines(), (argv, out)
            assert (tree / "draft.py").read_text() == "wip\n", argv


@case
def an_env_repo_branch_that_exists_is_checked_out_with_its_commits():
    with tempfile.TemporaryDirectory() as tmp:
        root, _origin, _seed = env_repo_project(pathlib.Path(tmp))
        git(root, "checkout", "-qb", "x")
        commit(root, "2026-10-02", {"scripts/KIO-1/fix.py": "pass\n"})
        git(root, "checkout", "-q", "main")
        rc, out, _conf = run_wt_start(root, argv=("x", "--env-repo"))
        tree = root / ".worktrees/_envrepo/x"
        assert rc == 0 and head_of(tree) == "x" and (tree / "scripts/KIO-1/fix.py").exists(), out


@case
def without_the_flag_there_is_no_env_repo_worktree():
    with tempfile.TemporaryDirectory() as tmp:
        root, _origin, _seed = env_repo_project(pathlib.Path(tmp))
        rc, out, _conf = run_wt_start(root)
        assert rc == 0 and "ENVREPO" not in out and not (root / ".worktrees/_envrepo").exists(), out


@case
def the_flag_on_a_project_that_is_no_git_clone_stops_before_anything_is_made():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        rc, out, conf_path = run_wt_start(root, argv=("x", "--env-repo"))
        assert rc == 1 and "--env-repo needs" in out and not conf_path.exists(), (rc, out)


@case
def an_unknown_option_or_a_second_slug_is_refused_before_anything_is_made():
    for argv in (("x", "--envrepo"), ("x", "y"), ()):
        with tempfile.TemporaryDirectory() as tmp:
            rc, out, conf_path = run_wt_start(pathlib.Path(tmp), argv=argv)
            assert rc == 2 and "usage: wt-start.sh <slug> [--env-repo]" in out, (argv, rc, out)
            assert not conf_path.exists(), argv


RESTORE_SRC = SRC.with_name("wt-restore.sh.jinja")
DUMP = "odoo_2026-10-13-18-30-06.dump"
INSTALLED = ("base", *(f"acme_{name}" for name in "abcdefghi"))
CHANGED = "acme_b,acme_c,acme_d,acme_f,acme_g,acme_h,acme_i"
EVERY_CUSTOM = "acme_a,acme_b,acme_c,acme_d,acme_f,acme_g,acme_h,acme_i"
FILESTORE = ".worktrees/_env/x/data/filestore"


def git(repo, *args, when=None):
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "t",
           "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    if when:
        stamp = when if "T" in when else f"{when}T12:00:00+0000"
        env |= {"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


def commit(repo, when, files=(), removed=(), moved=()):
    for path, text in dict(files).items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    for path in removed:
        git(repo, "rm", "-rq", path)
    for source, target in moved:
        git(repo, "mv", source, target)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", f"change of {when}", when=when)


def acme_history(repo, dirty=True, session=False, origin=True):
    """acme_a..i on 10-01; acme_a changes on 10-10 and at 09:00 on 10-12, acme_f at 11:00
    on 10-12 (after the dump day's first instant anywhere, 10:00 UTC), acme_g at 05:00 on
    the dump's day 10-13; on 10-14 acme_b, README and a new acme_new change, acme_e goes, a
    file moves from acme_h to acme_b and acme_i gains a non-ASCII path; a branch on acme_c
    written on 10-12 is merged on 10-15; acme_d is changed and not committed (all UTC).
    origin/16.0 is main; a `session` branch on top has a commit of 10-11 on acme_a."""
    git(repo, "init", "-qb", "main")
    commit(repo, "2026-10-01", {f"acme_{name}/__manifest__.py": "{}\n" for name in "abcdefghi"}
           | {"README.md": "r\n", "acme_h/data.py": "h\n"})
    commit(repo, "2026-10-10", {"acme_a/models.py": "a\n"})
    git(repo, "checkout", "-qb", "feature")
    commit(repo, "2026-10-12", {"acme_c/models.py": "c\n"})
    git(repo, "checkout", "-q", "main")
    commit(repo, "2026-10-12T09:00:00+0000", {"acme_a/models.py": "a2\n"})
    commit(repo, "2026-10-12T11:00:00+0000", {"acme_f/models.py": "f\n"})
    commit(repo, "2026-10-13T05:00:00+0000", {"acme_g/models.py": "g\n"})
    commit(repo, "2026-10-14", {"acme_b/models.py": "b\n", "README.md": "r2\n", "acme_new/__manifest__.py": "{}\n",
                                "acme_i/static/ü.txt": "i\n"},
           removed=["acme_e"], moved=[("acme_h/data.py", "acme_b/data.py")])
    git(repo, "merge", "-q", "--no-ff", "-m", "merge feature", "feature", when="2026-10-15")
    if origin:
        git(repo, "update-ref", "refs/remotes/origin/16.0", "main")
    if session:
        git(repo, "checkout", "-qb", "session")
        commit(repo, "2026-10-11", {"acme_a/session.py": "s\n"})
    if dirty:
        (repo / "acme_d/__manifest__.py").write_text("{'version': 2}\n")


# Stands in for `odoo shell` reading a script on stdin: the script runs against an odoo whose
# neutralize module logs the modules asked for, and an env whose cursor logs what it runs.
SHELL_STUB = '''\
import sys, types
calls = open(sys.argv[1], "a")
def get_neutralization_queries(modules):
    modules = list(modules)
    calls.write("neutralization-queries %s\\n" % ",".join(modules))
    for module in modules:
        yield "-- %s/data/neutralize.sql" % module
class Cursor:
    def execute(self, query):
        calls.write("execute %s\\n" % query)
    def commit(self):
        calls.write("commit\\n")
odoo, modules, neutralize = (types.ModuleType(name) for name in ("odoo", "odoo.modules", "odoo.modules.neutralize"))
neutralize.get_neutralization_queries = get_neutralization_queries
odoo.modules, modules.neutralize = modules, neutralize
sys.modules.update({"odoo": odoo, "odoo.modules": modules, "odoo.modules.neutralize": neutralize})
exec(sys.stdin.read(), {"__name__": "__main__", "odoo": odoo, "env": types.SimpleNamespace(cr=Cursor())})
calls.close()
sys.exit(int(sys.argv[2]))
'''


def wt_restore_project(root, dump=DUMP, installed=INSTALLED, installed_after=None, update_rc=0, shell_rc=0,
                       exists="", neutralized=True, tz="UTC", filestore=(), update_log_dir=False, **history):
    """Lay out a project at `root` with the rendered wt-restore.sh for slug `x` on an acme_history worktree.

    psql, pg_restore and the profile's odoo are stubs logging their argv, in call
    order, to one file; an odoo on PATH logs as PATH-odoo and fails. psql finds
    wt_x when `exists` is "1" (neutralized unless `neutralized` is false) and
    answers the installed-module query with `installed`, after a `-u` with
    `installed_after` when given; odoo on a `-u` files an attachment in the
    staging DB's filestore and exits `update_rc`, and `odoo shell` runs its stdin
    under SHELL_STUB and exits `shell_rc`. `filestore` lists files there
    beforehand; `update_log_dir` makes update.log a directory.
    Returns the command of a run, its environment and the calls file.
    """
    scripts, bin_dir, odoo_bin, calls = root / "scripts", root / "bin", root / ".nix-profile/bin", root / "calls"
    for d in (scripts, bin_dir, odoo_bin, root / "backup", root / ".worktrees/_env/x", root / ".worktrees/x"):
        d.mkdir(parents=True)
    (root / ".worktrees/_env/x/odoo.conf").write_text("[options]\n")
    if update_log_dir:
        (root / ".worktrees/_env/x/update.log").mkdir()
    (root / "backup" / dump).write_bytes(b"")
    for path in filestore:
        (root / FILESTORE / path).parent.mkdir(parents=True, exist_ok=True)
        (root / FILESTORE / path).write_text("old\n")
    acme_history(root / ".worktrees/x", **history)
    for src in (RESTORE_SRC, COMMON_SRC):
        (scripts / src.name.removesuffix(".jinja")).write_text(render_script(src))
    (root / "shell.py").write_text(SHELL_STUB)
    attachment, updated = root / FILESTORE / "wt_x_restoring/ab/ab12", root / "updated"
    after = shlex.join(installed if installed_after is None else installed_after)
    stubs = {
        bin_dir / "psql": f'case "$*" in\n  *pg_database*) printf "%s" {shlex.quote(exists)} ;;\n'
                          f'  *is_neutralized*) echo {"true" if neutralized else ""} ;;\n'
                          '  *"FROM res_users"*) echo 1 ;;\n'
                          f'  *ir_module_module*) if [ -e {updated} ]; then printf "%s\\n" {after}; '
                          f'else printf "%s\\n" {shlex.join(installed)}; fi ;;\nesac\n',
        bin_dir / "pg_restore": "",
        bin_dir / "odoo": "exit 1\n",
        odoo_bin / "odoo": f'case " $* " in\n'
                           f'  *" -u "*) mkdir -p {attachment.parent}; echo new > {attachment}; touch {updated}\n'
                           f'    exit {update_rc} ;;\n'
                           f'  *" shell "*) exec {shlex.quote(sys.executable)} {root / "shell.py"} {calls} {shell_rc} ;;\n'
                           'esac\n',
    }
    for path, body in stubs.items():
        name = "PATH-odoo" if path == bin_dir / "odoo" else path.name
        path.write_text(f'#!/usr/bin/env bash\necho "{name} $*" >> "{calls}"\n{body}')
        path.chmod(0o755)
    env = {**os.environ, "HOME": str(root), "WT_TEST_PROJECT_DIR": str(root), "TZ": tz,
           "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1", "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    return ["bash", str(scripts / "wt-restore.sh"), "x"], env, calls


def run_wt_restore(dump=DUMP, **options):
    """Run wt_restore_project's wt-restore.sh; returns the exit code, stdout+stderr, the calls
    and the filestore's files."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        cmd, env, calls = wt_restore_project(root, dump, **options)
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL)
        files = sorted(str(p.relative_to(root / FILESTORE)) for p in (root / FILESTORE).rglob("*") if p.is_file())
        return (proc.returncode, proc.stdout + proc.stderr, calls.read_text().splitlines() if calls.exists() else [],
                files)


def call_index(calls, program, *words):
    found = [i for i, line in enumerate(calls)
             if line.split()[0] == program and all(word in line.split() for word in words)]
    return found[0] if found else None


def updated_modules(calls):
    i = call_index(calls, "odoo", "-u")
    return None if i is None else calls[i].split()[calls[i].split().index("-u") + 1]


@case
def the_restore_updates_the_installed_modules_changed_since_the_dump():
    # acme_a changed before the dump, acme_new is not installed, acme_e is gone; acme_c
    # was written before the dump and merged after it, acme_d is changed in the worktree
    rc, out, calls, _files = run_wt_restore()
    assert (rc, updated_modules(calls)) == (0, CHANGED), (rc, calls, out)
    assert "-d wt_x_restoring" in calls[call_index(calls, "odoo", "-u")], calls
    assert f"since the dump (2026-10-13): {CHANGED}" in out and "WARNING" not in out, out
    assert "ERROR" not in out, out  # the EXIT trap is off once the DB has its name


@case
def the_dump_day_starts_at_its_earliest_instant_in_any_zone():
    for tz in ("UTC", "Etc/GMT+12", "Pacific/Kiritimati"):
        rc, out, calls, _files = run_wt_restore(tz=tz)
        modules = (updated_modules(calls) or "").split(",")
        assert rc == 0 and "acme_f" in modules and "acme_g" in modules and "acme_a" not in modules, (tz, calls, out)


@case
def a_moved_file_and_a_non_ascii_path_name_their_module():
    rc, out, calls, _files = run_wt_restore()
    modules = (updated_modules(calls) or "").split(",")
    assert rc == 0 and "acme_h" in modules and "acme_i" in modules, (calls, out)


@case
def a_session_branch_commit_older_than_the_dump_is_updated():
    rc, out, calls, _files = run_wt_restore(session=True)
    assert (rc, updated_modules(calls)) == (0, "acme_a," + CHANGED), (rc, calls, out)


@case
def with_no_mainline_commit_before_the_dump_every_custom_module_is_updated():
    rc, out, calls, _files = run_wt_restore("odoo_2026-09-01-00-00-00.dump")
    assert (rc, updated_modules(calls)) == (0, EVERY_CUSTOM), (rc, calls, out)


@case
def a_dump_named_otherwise_updates_every_installed_custom_module_and_warns():
    rc, out, calls, _files = run_wt_restore("prod.dump")
    assert (rc, updated_modules(calls)) == (0, EVERY_CUSTOM), (rc, calls, out)
    assert "WARNING: prod.dump is not named" in out, out


@case
def a_git_error_fails_the_restore_before_pg_restore():
    rc, out, calls, _files = run_wt_restore(origin=False)
    assert rc == 1 and "cannot list the custom modules" in out, (rc, out)
    assert call_index(calls, "pg_restore") is None and not any("CREATE DATABASE" in line for line in calls), calls


@case
def the_restore_runs_odoo_from_the_project_profile():
    rc, out, calls, _files = run_wt_restore()
    assert rc == 0 and call_index(calls, "PATH-odoo") is None, (rc, calls, out)
    assert None not in (call_index(calls, "odoo", "neutralize"), call_index(calls, "odoo", "-u")), calls


@case
def the_update_runs_after_neutralize_and_before_the_rename():
    _rc, _out, calls, _files = run_wt_restore()
    order = [call_index(calls, "odoo", "neutralize"), call_index(calls, "odoo", "-u"),
             call_index(calls, "psql", "RENAME")]
    assert None not in order and order == sorted(order), (order, calls)


@case
def the_updates_attachments_follow_the_db_to_its_name():
    for before, after in (((), ["wt_x/ab/ab12"]), (["wt_x/cd/cd34"], ["wt_x/ab/ab12", "wt_x/cd/cd34"])):
        rc, out, _calls, files = run_wt_restore(filestore=before)
        assert (rc, files) == (0, after), (before, rc, files, out)


@case
def nothing_changed_since_the_dump_skips_the_update():
    for dump, installed in (("odoo_2026-10-20-01-00-00.dump", INSTALLED), (DUMP, ("base", "acme_a"))):
        rc, out, calls, _files = run_wt_restore(dump, installed=installed, dirty=dump == DUMP)
        assert (rc, updated_modules(calls)) == (0, None), (dump, rc, calls, out)
        assert call_index(calls, "psql", "RENAME") is not None and "no update" in out, (dump, calls, out)


@case
def a_failed_update_drops_the_copy_and_fails_the_restore():
    rc, out, calls, files = run_wt_restore(update_rc=1)
    update = call_index(calls, "odoo", "-u")
    assert rc == 1 and update is not None and call_index(calls, "psql", "RENAME") is None, (rc, calls, out)
    assert any("DROP DATABASE IF EXISTS wt_x_restoring" in line for line in calls[update:]), calls
    assert files == [], files
    assert "ERROR" in out and CHANGED in out, out


@case
def a_step_failing_after_neutralize_drops_the_copy():
    # update.log as a directory: `: >` fails, and set -e exits outside any `|| abort`
    rc, out, calls, _files = run_wt_restore(update_log_dir=True)
    neutralize = call_index(calls, "odoo", "neutralize")
    assert rc != 0 and neutralize is not None and call_index(calls, "psql", "RENAME") is None, (rc, calls, out)
    assert any("DROP DATABASE IF EXISTS wt_x_restoring" in line for line in calls[neutralize:]), calls
    assert "stopped" in out and "dropped" in out, out


@case
def a_reused_db_is_not_updated():
    rc, out, calls, _files = run_wt_restore(exists="1")
    assert rc == 0 and "db-reused" in out and call_index(calls, "odoo") is None, (rc, calls, out)


@case
def a_module_the_update_installs_gets_its_neutralization_before_the_rename():
    # `odoo neutralize` ran before the update, so a dependency the update installed missed it
    rc, out, calls, _files = run_wt_restore(installed_after=(*INSTALLED, "acme_dep"))
    shell = call_index(calls, "odoo", "shell")
    assert rc == 0 and shell is not None, (rc, calls, out)
    assert "-d wt_x_restoring" in calls[shell] and {"--no-http", "--stop-after-init"} <= set(calls[shell].split()), calls
    asked = [line for line in calls if line.startswith("neutralization-queries ")]
    assert asked == ["neutralization-queries acme_dep"], calls
    order = [call_index(calls, "odoo", "-u"), shell, call_index(calls, "neutralization-queries"),
             call_index(calls, "execute", "acme_dep/data/neutralize.sql"), call_index(calls, "commit"),
             call_index(calls, "psql", "-f"), call_index(calls, "psql", "RENAME")]
    assert None not in order and order == sorted(order), (order, calls)


@case
def nothing_new_installed_runs_no_neutralization_shell():
    for options in ({}, {"dump": "odoo_2026-10-20-01-00-00.dump"}):
        rc, out, calls, _files = run_wt_restore(**options)
        assert rc == 0 and call_index(calls, "odoo", "shell") is None, (options, calls, out)


@case
def a_failed_neutralization_of_new_modules_drops_the_copy():
    rc, out, calls, files = run_wt_restore(installed_after=(*INSTALLED, "acme_dep"), shell_rc=1)
    shell = call_index(calls, "odoo", "shell")
    assert rc == 1 and shell is not None and call_index(calls, "psql", "RENAME") is None, (rc, calls, out)
    assert any("DROP DATABASE IF EXISTS wt_x_restoring" in line for line in calls[shell:]), calls
    assert files == [] and "neutralizing acme_dep failed" in out, (files, out)


@case
def the_restore_waits_for_a_test_run_before_replacing_the_db():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        cmd, env, calls = wt_restore_project(root, exists="1", neutralized=False)
        held, release = root / "held", root / "release"
        procs = [hold_lock(root / ".worktrees/_env/x/test.lock", env, root / "holder.out", held, release)]
        try:
            wait_for(held.exists, "the test run")
            procs.append(start_bg(cmd, env, root / "out"))
            wait_for(lambda: "waiting" in (root / "out").read_text(), "the restore's waiting line")
            text = calls.read_text()
            assert "IF EXISTS wt_x " not in text and "RENAME" not in text and "dev-fixup" in text, text
            assert (root / FILESTORE / "wt_x_restoring/ab/ab12").exists(), "the filestore moved before the wait"
        finally:
            release.touch()
            finish_bg(procs)
        out = (root / "out").read_text()
        assert procs[1].returncode == 0 and "db-ready" in out, out
        assert f"(pid {procs[0].pid}: flock " in out and "then replacing wt_x" in out, out
        assert call_index(calls.read_text().splitlines(), "psql", "RENAME") is not None


@case
def a_restore_whose_env_is_stopped_during_its_wait_drops_the_copy_and_says_why():
    # the env dir removed as wt-stop leaves it, and only its lock file (the staging filestore stays to be dropped)
    for then in ("rm -rf {env}", "rm {env}/test.lock"):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            env_dir = root / ".worktrees/_env/x"
            cmd, env, calls = wt_restore_project(root, exists="1", neutralized=False)
            held, release = root / "held", root / "release"
            procs = [hold_lock(env_dir / "test.lock", env, root / "holder.out", held, release,
                               then.format(env=shlex.quote(str(env_dir))))]
            try:
                wait_for(held.exists, "the test run")
                procs.append(start_bg(cmd, env, root / "out"))
                wait_for(lambda: "waiting" in (root / "out").read_text(), "the restore's waiting line")
            finally:
                release.touch()
                finish_bg(procs)
            out, log = (root / "out").read_text(), calls.read_text().splitlines()
            assert procs[1].returncode == 1 and "env x was stopped while the restore waited" in out, (then, out)
            fixup = call_index(log, "psql", "-f")
            assert fixup is not None and any("IF EXISTS wt_x_restoring" in line for line in log[fixup:]), (then, log)
            assert not any("IF EXISTS wt_x " in line or "RENAME" in line for line in log), (then, log)
            assert not (root / FILESTORE / "wt_x_restoring").exists(), then


@case
def a_restore_that_cannot_take_the_env_lock_drops_the_copy_and_says_why():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        cmd, env, calls = wt_restore_project(root, exists="1", neutralized=False)
        (root / ".worktrees/_env/x/test.lock").mkdir()
        run = start_bg(cmd, env, root / "out")
        finish_bg([run], timeout=20)
        out, log = (root / "out").read_text(), calls.read_text().splitlines()
        assert run.returncode == 1 and "cannot take" in out and "to replace wt_x" in out, out
        assert "was stopped" not in out, out
        fixup = call_index(log, "psql", "-f")
        assert fixup is not None and any("IF EXISTS wt_x_restoring" in line for line in log[fixup:]), log
        assert not any("IF EXISTS wt_x " in line or "RENAME" in line for line in log), log
        assert not (root / FILESTORE / "wt_x_restoring").exists(), out


@case
def a_reused_db_takes_no_test_lock():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        cmd, env, _calls = wt_restore_project(root, exists="1")
        held, release = root / "held", root / "release"
        procs = [hold_lock(root / ".worktrees/_env/x/test.lock", env, root / "holder.out", held, release)]
        try:
            wait_for(held.exists, "the test run")
            run = start_bg(cmd, env, root / "out")
            finish_bg([run], timeout=10)
        finally:
            release.touch()
            finish_bg(procs)
        out = (root / "out").read_text()
        assert run.returncode == 0 and "db-reused" in out and "waiting" not in out, out


@case
def the_restore_takes_the_dump_and_db_wt_start_recorded():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        cmd, env, calls = wt_restore_project(root)
        recorded = root / "elsewhere/odoo_2026-10-12-18-30-06.dump"
        recorded.parent.mkdir()
        recorded.write_bytes(b"")
        (root / ".worktrees/_env/x/dump").write_text(f"{recorded}\n")
        (root / ".worktrees/_env/x/db").write_text("wt_x_101226\n")
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL)
        out, lines = proc.stdout + proc.stderr, calls.read_text().splitlines()
        assert proc.returncode == 0 and "db-ready (wt_x_101226)" in out, out
        restores = [line.split() for line in lines if line.startswith("pg_restore ")]
        assert restores and restores[0][-1] == str(recorded), restores
        assert call_index(lines, "psql", "wt_x_101226_restoring", "RENAME", "wt_x_101226;") is not None, lines


@case
def wt_stop_drops_the_db_the_env_recorded():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        cmd, env, events = stop_project(root)
        (root / ".worktrees/_env/x/db").write_text("wt_x_102026\n")
        run = start_bg(cmd, env, root / "out")
        finish_bg([run], timeout=20)
        text = events.read_text()
        assert run.returncode == 0 and "DROP DATABASE IF EXISTS wt_x_102026_restoring" in text, text
        assert "wt_x_restoring" not in text, text


BOOTSTRAP_SRC = SRC.with_name("wt-bootstrap.sh.jinja")


def bootstrap_project(root, with_pre_commit=True, with_config=True):
    """Lay out a project at `root`: the rendered wt-bootstrap.sh and wt-common.sh, the addons repo
    src/acme-addons as a real git repo carrying an entire-style commit-msg hook, systemctl stubbed,
    and (with_pre_commit) a pre-commit stub that logs its calls and writes a hook as pre-commit does.
    PATH holds only the stubs, git and the system dirs, so a pre-commit on the caller's PATH stays out.
    Returns the command, its environment, the addons repo and the events log."""
    scripts, bin_dir, repo = root / "scripts", root / "bin", root / "src/acme-addons"
    for d in (scripts, bin_dir, repo):
        d.mkdir(parents=True)
    for src in (BOOTSTRAP_SRC, COMMON_SRC):
        (scripts / src.name.removesuffix(".jinja")).write_text(render_script(src))
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".git/hooks/commit-msg").write_text("#!/bin/sh\n# entire\n")
    if with_config:
        (repo / ".pre-commit-config.yaml").write_text("repos: []\n")
    events = root / "events"
    (bin_dir / "systemctl").write_text("#!/usr/bin/env bash\n")
    (bin_dir / "git").symlink_to(shutil.which("git"))
    if with_pre_commit:
        (bin_dir / "pre-commit").write_text(
            f'#!/usr/bin/env bash\necho "pre-commit $* (in $PWD)" >> {events}\n'
            'hook="$(git rev-parse --path-format=absolute --git-path hooks)/pre-commit"\n'
            'printf "#!/usr/bin/env bash\\n# File generated by pre-commit: https://pre-commit.com\\n" > "$hook"\n')
    for f in bin_dir.iterdir():
        if not f.is_symlink():
            f.chmod(0o755)
    env = {"HOME": str(root), "WT_TEST_PROJECT_DIR": str(root), "PATH": f"{bin_dir}:/usr/bin:/bin"}
    return ["bash", str(scripts / "wt-bootstrap.sh")], env, repo, events


@case
def bootstrap_installs_the_pre_commit_hook_once_beside_entire_s():
    with tempfile.TemporaryDirectory() as tmp:
        cmd, env, repo, events = bootstrap_project(pathlib.Path(tmp))
        for _ in range(2):
            proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
            assert proc.returncode == 0, proc.stdout + proc.stderr
        assert events.read_text().splitlines() == [f"pre-commit install --hook-type pre-commit (in {repo})"], \
            events.read_text()
        assert "File generated by pre-commit" in (repo / ".git/hooks/pre-commit").read_text()
        assert (repo / ".git/hooks/commit-msg").read_text() == "#!/bin/sh\n# entire\n"


@case
def a_worktree_of_the_addons_repo_runs_the_same_hook():
    with tempfile.TemporaryDirectory() as tmp:
        cmd, env, repo, _ = bootstrap_project(pathlib.Path(tmp))
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
                        "--allow-empty", "--no-verify", "-m", "init"], check=True)
        worktree = pathlib.Path(tmp, ".worktrees/kio-1")
        subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", "-b", "kio-1", str(worktree)], check=True)
        assert subprocess.run(cmd, capture_output=True, text=True, env=env).returncode == 0
        hook = subprocess.run(["git", "-C", str(worktree), "rev-parse", "--path-format=absolute", "--git-path",
                               "hooks/pre-commit"], capture_output=True, text=True, check=True).stdout.strip()
        assert pathlib.Path(hook) == repo / ".git/hooks/pre-commit" and pathlib.Path(hook).exists(), hook


@case
def without_pre_commit_on_path_or_a_config_the_repo_is_left_alone():
    for kwargs in (dict(with_pre_commit=False), dict(with_config=False)):
        with tempfile.TemporaryDirectory() as tmp:
            cmd, env, repo, events = bootstrap_project(pathlib.Path(tmp), **kwargs)
            proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
            assert proc.returncode == 0, proc.stdout + proc.stderr
            assert not (repo / ".git/hooks/pre-commit").exists() and not events.exists(), kwargs


if __name__ == "__main__":
    failed = 0
    for fn in CASES:
        try:
            fn()
            print(f"  ok   {fn.__name__}")
        except Skip as exc:
            print(f"  skip {fn.__name__}: {exc}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL {fn.__name__}: {exc}")
    print("PASS" if not failed else f"{failed} FAILED")
    sys.exit(1 if failed else 0)
