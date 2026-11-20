"""Run the invariant check scripts without Odoo.

The scripts are written for `odoo shell` stdin: they expect the shell's `env`
global and run their checks at the end of the file. `load()` executes a script's
definitions (imports, functions, constants), never its calls, in a namespace
that stands in for the shell: `env` maps model names to the stubs a test passes
and holds a cursor answering each query from a table of canned rows, and every
`print` lands in `Shell.lines`. `run()` executes a whole script there, calls
included, the way `cat core local | odoo shell` does.

Stdlib only: `python3 -m unittest discover -s <this folder>`.
"""

import ast
import os
import pathlib
from unittest import mock

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
CORE = SCRIPTS / "invariant-check.py"
LOCAL = SCRIPTS / "invariant_local.py"


class Cursor:
    """Answers `execute(query)` + `fetchall()` with the rows of the first `answers`
    pair whose text the query contains, else no rows; keeps every query and rollback."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.queries = []
        self.rollbacks = 0
        self._rows = []

    def execute(self, query, params=()):
        self.queries.append((query, params))
        self._rows = next((rows for text, rows in self.answers if text in query), [])

    def fetchall(self):
        return list(self._rows)

    def rollback(self):
        self.rollbacks += 1


class Env(dict):
    """`env[model]` and `model in env`, plus the cursor as `env.cr`."""

    def __init__(self, models, cursor):
        super().__init__(models)
        self.cr = cursor


class Shell:
    """The namespace a script runs in. `answers` is a list of (text in the query, rows),
    `models` maps model names to stubs, `environ` sets INV_* variables while a script's
    definitions are read (SINCE, SKIP and TIER are read then)."""

    def __init__(self, answers=(), models=None, environ=None):
        self.cr = Cursor(answers)
        self.env = Env(models or {}, self.cr)
        self.environ = {"INV_SINCE": "2026-01-01 00:00:00", **(environ or {})}
        self.lines = []
        # odoo shell runs its stdin as __main__
        self.scope = {"__name__": "__main__", "env": self.env, "print": self._print}

    def _print(self, *args, **_kwargs):
        self.lines.append(" ".join(str(a) for a in args))

    def _exec(self, filename, body):
        # the test's INV_* only: one set in the caller's shell would change what a test sees
        environ = {key: value for key, value in os.environ.items() if not key.startswith("INV_")}
        with mock.patch.dict(os.environ, {**environ, **self.environ}, clear=True):
            exec(compile(ast.Module(body=body, type_ignores=[]), filename, "exec"), self.scope)

    def load(self, *paths):
        """Execute the definitions of each script in order: what a test calls by name."""
        for path in paths:
            tree = ast.parse(pathlib.Path(path).read_text(), str(path))
            kept = (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.ClassDef, ast.Assign, ast.AnnAssign)
            self._exec(str(path), [node for node in tree.body if isinstance(node, kept)])
        return self

    def run(self, *paths):
        """Execute the scripts whole, calls included, as one stdin, the way `cat core local | odoo
        shell` reads them: a syntax error in one stops them all."""
        source = "\n".join(pathlib.Path(path).read_text() for path in paths)
        self._exec("<stdin>", ast.parse(source, "<stdin>").body)
        return self

    def call(self, name, *args):
        return self.scope[name](*args)

    def invariant(self, name):
        """The status and detail of the INVARIANT line of check `name` (None if none)."""
        prefix = "INVARIANT %s: " % name
        for line in self.lines:
            if line.startswith(prefix):
                status, _, detail = line[len(prefix):].partition(" -- ")
                return status, detail
        return None
