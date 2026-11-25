#!/usr/bin/env python3
"""Corpus for the side-effect dry run (template/.../skills/prod-ops/scripts/
dry-run-side-effects.sh.jinja and side_effects_prelude.py).

Run from the repo root: python3 tests/test_dry_run_side_effects.py

The runner is rendered with a minimal answer set and run against a stub `odoo`
under a throwaway $HOME that records its arguments, environment and stdin, and
prints what a case gives it. The cases are the runner's own promises: it
refuses a database that is not the project cluster (host, port) or is given as
a connection string before Odoo starts, hands the cluster's port to the prelude, keeps libpq's server variables away from it, always runs the
script dry, never passes a commit switch on, and turns the prelude's markers
into exit codes. The prelude itself needs a live Odoo; here it is only
compiled.
"""

import os
import pathlib
import py_compile
import shutil
import subprocess
import sys
import tempfile

import jinja2

SCRIPTS = pathlib.Path(
    "template/{% if use_agent_layer %}.claude{% endif %}/skills/"
    "{% if prod_ssh_host and custom_repo_name %}prod-ops{% endif %}/scripts")
ONLY_16 = "{% if odoo_version == '16.0' %}", "{% endif %}"
RUNNER = SCRIPTS / ("dry-run-side-effects.sh".join(ONLY_16) + ".jinja")
PRELUDE = SCRIPTS / "side_effects_prelude.py".join(ONLY_16)
SKILL = SCRIPTS.parent / "SKILL.md.jinja"

ANSWERS = dict(nix_profile_rel=".nix-profile", postgres_port=16432)

STUB_ODOO = """#!/usr/bin/env bash
printf '%s\\n' "$@" > "$HOME/args"
{ echo "SCRIPT_MODE=$SCRIPT_MODE"; echo "SE_SCRIPT=$SE_SCRIPT"; echo "SE_PG_PORT=$SE_PG_PORT"
  env | grep -E '_COMMIT=|^PG' ; } > "$HOME/env"
cat > "$HOME/stdin"
printf '%b' "${FAKE_OUT:-}"
exit "${FAKE_EXIT:-0}"
"""

DONE = "SE_SUMMARY side_effects=0 while_reporting=0\\nSCRIPT_DONE mode=dry\\n"


class Run:
    def __init__(self, db_host="localhost", db_name="copy", db_port="16432"):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="dry-run-se-"))
        self.home = self.tmp / "home"
        stub = self.home / ".nix-profile" / "bin" / "odoo"
        stub.parent.mkdir(parents=True)
        stub.write_text(STUB_ODOO)
        stub.chmod(0o755)
        self.conf = self.tmp / "odoo.conf"
        port_line = "db_port = %s\n" % db_port if db_port is not None else ""
        self.conf.write_text("[options]\ndb_host = %s\n%sdb_name = %s\n" % (db_host, port_line, db_name))
        self.script = self.tmp / "fix.py"
        self.script.write_text("print('SCRIPT_DONE mode=dry')\n")
        runner = SCRIPTS / "dry-run-side-effects.sh"
        out = self.tmp / runner.name
        env = jinja2.Environment(trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=True)
        out.write_text(env.from_string(RUNNER.read_text()).render(**ANSWERS))
        out.chmod(0o755)
        shutil.copy(PRELUDE, self.tmp / "side_effects_prelude.py")
        self.runner = out

    def run(self, *args, out="", **env):
        args = args or (str(self.conf), str(self.script))
        environ = dict(os.environ, HOME=str(self.home), FAKE_OUT=out, **env)
        return subprocess.run([str(self.runner), *args], stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, env=environ)

    def seen(self, name):
        path = self.home / name
        return path.read_text() if path.exists() else None

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


def with_run(fn, **kw):
    run = Run(**kw)
    try:
        fn(run)
    finally:
        run.cleanup()


@case("a db_host that is not local is refused before odoo starts")
def _():
    def check(run):
        proc = run.run(out=DONE)
        expect(proc.returncode == 3, (proc.returncode, proc.stderr))
        expect("REFUSED" in proc.stderr, proc.stderr)
        expect(run.seen("args") is None, "odoo ran")
    with_run(check, db_host="10.1.2.3")
    with_run(check, db_host="db.example.com")


@case("localhost, 127.0.0.1, ::1, a socket directory and no db_host count as local")
def _():
    for host in ("localhost", "127.0.0.1", "::1", "/run/postgresql", "False", ""):
        def check(run):
            proc = run.run(out=DONE)
            expect(proc.returncode == 0, (host, proc.returncode, proc.stdout + proc.stderr))
        with_run(check, db_host=host)


@case("a clean run is dry, gets the prelude on stdin and the script's absolute path")
def _():
    def check(run):
        proc = run.run(out=DONE)
        expect(proc.returncode == 0, (proc.returncode, proc.stdout + proc.stderr))
        expect("DRY_RUN_OK" in proc.stdout, proc.stdout)
        env = run.seen("env")
        expect("SCRIPT_MODE=dry" in env, env)
        expect("SE_SCRIPT=%s" % run.script.resolve() in env, env)
        expect(run.seen("stdin") == PRELUDE.read_text(), "stdin is not the prelude")
        args = run.seen("args").split("\n")
        expect(args[:3] == ["shell", "-c", str(run.conf)] and "--no-http" in args, args)
        expect("--logfile=" in args, ("the Odoo log must reach the run's output", args))
        expect("-d" not in args, args)
    with_run(check)


@case("the optional database goes to odoo as -d")
def _():
    def check(run):
        proc = run.run(str(run.conf), str(run.script), "other_copy", out=DONE)
        expect(proc.returncode == 0, (proc.returncode, proc.stderr))
        args = run.seen("args").split("\n")
        expect(args[args.index("-d") + 1] == "other_copy", args)
    with_run(check)


@case("a commit switch from the caller never reaches odoo")
def _():
    def check(run):
        proc = run.run(out=DONE, TASK1_COMMIT="1", FIX_COMMIT="yes", SCRIPT_MODE="commit")
        expect(proc.returncode == 0, (proc.returncode, proc.stderr))
        env = run.seen("env")
        expect("_COMMIT=" not in env, env)
        expect("SCRIPT_MODE=dry" in env, env)
    with_run(check)


@case("a database given as a URI or a key=value string is refused before odoo starts")
def _():
    for db in ("postgresql://10.1.2.3/odoo", "postgres://prod/odoo", "host=10.1.2.3 dbname=odoo",
               "dbname=odoo", "copy;drop", "-x"):
        def check(run):
            proc = run.run(str(run.conf), str(run.script), db, out=DONE)
            expect(proc.returncode == 3, (db, proc.returncode, proc.stderr))
            expect(run.seen("args") is None, (db, "odoo ran"))
        with_run(check)
    for name in ("postgresql://10.1.2.3/odoo", "host=10.1.2.3 dbname=odoo"):
        def check(run):
            proc = run.run(out=DONE)
            expect(proc.returncode == 3, (name, proc.returncode, proc.stderr))
            expect(run.seen("args") is None, (name, "odoo ran"))
        with_run(check, db_name=name)


@case("a db_port other than the project cluster's is refused, and so is a missing one")
def _():
    for port in ("5432", "15432", None, "False"):
        def check(run):
            proc = run.run(out=DONE)
            expect(proc.returncode == 3, (port, proc.returncode, proc.stderr))
            expect("16432" in proc.stderr, proc.stderr)
            expect(run.seen("args") is None, (port, "odoo ran"))
        with_run(check, db_port=port)


@case("the project cluster's port reaches the prelude, which checks the server against it")
def _():
    def check(run):
        proc = run.run(out=DONE)
        expect(proc.returncode == 0, (proc.returncode, proc.stderr))
        expect("SE_PG_PORT=16432" in run.seen("env"), run.seen("env"))
    with_run(check)


@case("libpq's server variables never reach odoo")
def _():
    def check(run):
        proc = run.run(out=DONE, PGHOST="10.1.2.3", PGHOSTADDR="10.1.2.3", PGPORT="5433",
                       PGSERVICE="prod", PGSERVICEFILE="/tmp/x", PGDATABASE="odoo")
        expect(proc.returncode == 0, (proc.returncode, proc.stderr))
        env = run.seen("env")
        for var in ("PGHOST", "PGHOSTADDR", "PGPORT", "PGSERVICE", "PGSERVICEFILE", "PGDATABASE"):
            expect(var + "=" not in env, (var, env))
    with_run(check)


@case("the prelude finding a server that is not local refuses with exit 3")
def _():
    def check(run):
        proc = run.run(out="SE_REFUSED_NOT_LOCAL: connected to 10.1.2.3\\n", FAKE_EXIT="3")
        expect(proc.returncode == 3, (proc.returncode, proc.stderr))
        expect("REFUSED" in proc.stderr, proc.stderr)
    with_run(check)


@case("a blocked transaction end fails the run, a changed transaction says so first")
def _():
    def check(run):
        proc = run.run(out="SE_BLOCKED: connection.commit()\\n" + DONE + "SCRIPT_FAILED: _SeBlocked\\n")
        expect(proc.returncode == 1, (proc.returncode, proc.stderr))
        expect("COMMIT_ATTEMPTED" in proc.stderr, proc.stderr)
        proc = run.run(out="SE_BLOCKED: behind the driver\\nSE_TXN_CHANGED: ended\\n" + DONE)
        expect(proc.returncode == 1, (proc.returncode, proc.stderr))
        expect("TRANSACTION_CHANGED" in proc.stderr and "COMMIT_ATTEMPTED" not in proc.stderr, proc.stderr)
    with_run(check)


@case("a commit guard the script changed fails the run")
def _():
    def check(run):
        proc = run.run(out="SE_GUARD_TAMPERED: the guard function was replaced\\n" + DONE)
        expect(proc.returncode == 1, (proc.returncode, proc.stderr))
        expect("GUARD_TAMPERED" in proc.stderr, proc.stderr)
    with_run(check)


@case("side effects found exit 4 and name the count")
def _():
    def check(run):
        proc = run.run(out="SE_SUMMARY side_effects=3 while_reporting=1\\nSCRIPT_DONE mode=dry\\n")
        expect(proc.returncode == 4, (proc.returncode, proc.stdout))
        expect("SIDE_EFFECTS_FOUND=3" in proc.stdout, proc.stdout)
    with_run(check)


@case("a report that raised fails the run instead of passing as no side effects")
def _():
    def check(run):
        proc = run.run(out="SIDE-EFFECT REPORT FAILED:\\nTraceback (most recent call last):\\nSCRIPT_DONE mode=dry\\n")
        expect(proc.returncode == 1, (proc.returncode, proc.stdout, proc.stderr))
        expect("REPORT_FAILED" in proc.stderr and "DRY_RUN_OK" not in proc.stdout, proc.stdout + proc.stderr)
    with_run(check)


@case("a report that was skipped or never printed fails the run")
def _():
    for out in ("SE_SUMMARY side_effects=unknown while_reporting=unknown\\nSCRIPT_DONE mode=dry\\n",
                "SCRIPT_DONE mode=dry\\n",
                "SE_SUMMARY side_effects=0 while_reporting=0\\n"
                "SE_SUMMARY side_effects=unknown while_reporting=unknown\\nSCRIPT_DONE mode=dry\\n"):
        def check(run):
            proc = run.run(out=out)
            expect(proc.returncode == 1, (out, proc.returncode, proc.stdout, proc.stderr))
            expect("REPORT_MISSING" in proc.stderr and "DRY_RUN_OK" not in proc.stdout,
                   (out, proc.stdout + proc.stderr))
        with_run(check)


@case("the tool and its SKILL.md section render for Odoo 16 only")
def _():
    env = jinja2.Environment(trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=True,
                             undefined=jinja2.ChainableUndefined)
    for version, present in (("16.0", True), ("17.0", False), ("20.0", False)):
        for path in (RUNNER, PRELUDE):
            name = env.from_string(path.name).render(odoo_version=version)
            expect(bool(name.replace(".jinja", "")) == present, (version, path.name, name))
        text = env.from_string(SKILL.read_text()).render(odoo_version=version)
        for needle in ("dry-run-side-effects.sh", "**Side effects: the dry run that shows them.**"):
            expect((needle in text) == present, (version, needle))
        expect("The report only reads" in text, (version, "the report-only-reads rule is for every version"))
        expect("\n\n\n" not in text.split("The report only reads", 1)[1][:2000], (version, "blank lines"))


@case("a script that called commit() fails the run")
def _():
    def check(run):
        proc = run.run(out="COMMIT BLOCKED: a side-effect dry run never commits\\n" + DONE)
        expect(proc.returncode == 1, (proc.returncode, proc.stdout))
        expect("COMMIT_ATTEMPTED" in proc.stderr, proc.stderr)
    with_run(check)


@case("a missing SCRIPT_DONE marker fails the run")
def _():
    def check(run):
        proc = run.run(out="SE_SUMMARY side_effects=0 while_reporting=0\\n")
        expect(proc.returncode == 1, (proc.returncode, proc.stdout))
        expect("MARKER_MISSING" in proc.stderr, proc.stderr)
    with_run(check)


@case("a script that raised fails the run even after its marker")
def _():
    def check(run):
        proc = run.run(out=DONE + "SCRIPT_FAILED: Exception\\n")
        expect(proc.returncode == 1, (proc.returncode, proc.stdout))
    with_run(check)


@case("odoo itself failing fails the run")
def _():
    def check(run):
        proc = run.run(out=DONE, FAKE_EXIT="2")
        expect(proc.returncode == 1, (proc.returncode, proc.stderr))
        expect("shell_exit=2" in proc.stderr, proc.stderr)
    with_run(check)


@case("a missing config or script is a usage error")
def _():
    def check(run):
        for args in ((), (str(run.conf),), (str(run.tmp / "none.conf"), str(run.script)),
                     (str(run.conf), str(run.tmp / "none.py"))):
            proc = run.run(*args) if args else subprocess.run(
                [str(run.runner)], capture_output=True, text=True, stdin=subprocess.DEVNULL,
                env=dict(os.environ, HOME=str(run.home)))
            expect(proc.returncode == 2, (args, proc.returncode, proc.stderr))
    with_run(check)


@case("the prelude compiles")
def _():
    py_compile.compile(str(PRELUDE), doraise=True,
                       cfile=os.path.join(tempfile.mkdtemp(prefix="se-pyc-"), "prelude.pyc"))


def main():
    failures = 0
    for label, fn in CASES:
        try:
            fn()
            print(f"ok    {label}")
        except (AssertionError, py_compile.PyCompileError) as exc:
            failures += 1
            print(f"FAIL  {label}\n      {exc}")
    print(f"\n{len(CASES) - failures}/{len(CASES)} passed")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
