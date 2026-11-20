"""The project-local invariants (scripts/invariant_local.py) -- PROJECT-OWNED, like
that script: give each local check its cases here, on canned query answers
(see harness.py), and keep your version on copier update conflicts.

    shell = Shell([("FROM sale_order", [("S00042",)])]).load(CORE, LOCAL)
    shell.call("orders_have_amounts")
    self.assertEqual(shell.invariant("orders_have_amounts"), ("WARN", "S00042"))
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import CORE, LOCAL, Shell  # noqa: E402


class LocalScript(unittest.TestCase):
    def test_the_local_script_runs_after_the_core_one_on_the_same_stdin(self):
        # what `cat invariant-check.py invariant_local.py | odoo shell` does: the local
        # file finds sql(), report(), check(), deep_check() and SINCE defined
        shell = Shell().run(CORE, LOCAL)
        self.assertTrue(any(line.startswith("INVARIANT_CORE_DONE") for line in shell.lines), shell.lines)


if __name__ == "__main__":
    unittest.main()
