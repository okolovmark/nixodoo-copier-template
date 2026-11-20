"""The core invariant script (scripts/invariant-check.py) on canned query answers."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import CORE, Shell  # noqa: E402

TABLES = "information_schema.tables"


def core(answers=(), **environ):
    return Shell(answers, environ=environ).load(CORE)


class CoreChecks(unittest.TestCase):
    def test_an_unbalanced_entry_since_the_deploy_fails(self):
        shell = core([(TABLES, [(1,)]), ("account_move", [(7, "INV/1"), (8, None)])])
        shell.call("unbalanced_moves")
        status, detail = shell.invariant("unbalanced_moves")
        self.assertEqual(status, "FAIL")
        self.assertIn("2 unbalanced posted entries since 2026-01-01 00:00:00: INV/1, 8", detail)

    def test_balanced_entries_pass_and_without_accounting_the_check_skips(self):
        shell = core([(TABLES, [(1,)])])
        shell.call("unbalanced_moves")
        self.assertEqual(shell.invariant("unbalanced_moves")[0], "PASS")
        shell = core()
        shell.call("unbalanced_moves")
        self.assertEqual(shell.invariant("unbalanced_moves"), ("SKIP", "account not installed"))

    def test_failed_queue_jobs_fail_with_their_total(self):
        shell = core([(TABLES, [(1,)]), ("FROM queue_job", [("sale.order", "_send", 3), ("res.partner", "_sync", 2)])])
        shell.call("failed_queue_jobs")
        status, detail = shell.invariant("failed_queue_jobs")
        self.assertEqual(status, "FAIL")
        self.assertIn("5 failed jobs", detail)
        self.assertIn("sale.order._send x3; res.partner._sync x2", detail)

    def test_an_overdue_cron_warns_with_the_grace(self):
        shell = core([("FROM ir_cron", [("Mail queue", "2026-01-01 00:00:00")])], INV_CRON_GRACE_HOURS="5")
        shell.call("stuck_crons")
        self.assertEqual(shell.invariant("stuck_crons"),
                         ("WARN", "1 active crons overdue > 5h: Mail queue (due 2026-01-01 00:00:00)"))
        self.assertEqual(shell.cr.queries[-1][1], (5,))

    def test_freshly_negative_stock_warns(self):
        shell = core([(TABLES, [(1,)]), ("stock_quant", [(11, 8, -2.0)])])
        shell.call("negative_internal_stock")
        status, detail = shell.invariant("negative_internal_stock")
        self.assertEqual(status, "WARN")
        self.assertIn("product 11 @ location 8: -2.0", detail)


class Registration(unittest.TestCase):
    def test_a_skipped_check_says_so_and_does_not_run(self):
        shell = core(INV_SKIP="a, b")
        calls = []
        shell.call("check", "b", lambda: calls.append(1))
        self.assertEqual((calls, shell.invariant("b")), ([], ("SKIP", "via INV_SKIP")))

    def test_a_check_that_raises_warns_and_rolls_the_cursor_back(self):
        shell = core()

        def broken():
            raise ValueError("column x does not exist")

        shell.call("check", "broken", broken)
        self.assertEqual(shell.invariant("broken"), ("WARN", "check errored: column x does not exist"))
        self.assertEqual(shell.cr.rollbacks, 1)

    def test_a_deep_check_runs_on_high_and_unset_tiers_only(self):
        for tier, ran in (("high", True), ("", True), (None, True), ("medium", False), ("Low", False)):
            shell = core(**({} if tier is None else {"INV_TIER": tier}))
            calls = []
            shell.call("deep_check", "deep", lambda: calls.append(1))
            self.assertEqual(bool(calls), ran, tier)
            if not ran:
                self.assertEqual(shell.invariant("deep")[0], "SKIP", tier)


class WholeScript(unittest.TestCase):
    def test_every_core_check_prints_its_line_and_the_run_ends_with_the_marker(self):
        shell = Shell().run(CORE)
        names = ("unbalanced_moves", "failed_queue_jobs", "stuck_crons", "negative_internal_stock")
        self.assertEqual([shell.invariant(name)[0] for name in names], ["SKIP", "SKIP", "PASS", "SKIP"])
        self.assertTrue(shell.lines[-1].startswith("INVARIANT_CORE_DONE since=2026-01-01 00:00:00"), shell.lines)


if __name__ == "__main__":
    unittest.main()
