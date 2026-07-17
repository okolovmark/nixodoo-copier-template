# Post-deploy invariant check -- READ-ONLY. Designed to pipe into odoo shell:
#
#   python odoo-bin shell -c <conf> -d <db> --no-http < invariant-check.py
#
# Window: env INV_SINCE ("YYYY-MM-DD HH:MM:SS", UTC) -- set it to the deploy
# timestamp; default is now-2h. Skip checks: INV_SKIP=name,name.
# Cron grace: INV_CRON_GRACE_HOURS (default 2).
#
# Contract: prints one line per check --
#   INVARIANT <name>: PASS|FAIL|WARN|SKIP -- <detail>
# Callers grep ": FAIL" (broken invariant) and ": WARN" (needs a look).
# FAIL = data written since INV_SINCE violates a rule that must always hold.
# WARN = suspicious but possibly pre-existing/environmental.
#
# Project-local checks: append your file on stdin --
#   cat invariant-check.py invariant_local.py | python odoo-bin shell ...
# The local file reuses sql()/report()/table_exists()/check() and SINCE.
import os
from datetime import datetime, timedelta, timezone

SINCE = os.environ.get("INV_SINCE") or (
    datetime.now(timezone.utc) - timedelta(hours=2)
).strftime("%Y-%m-%d %H:%M:%S")
SKIP = {s.strip() for s in os.environ.get("INV_SKIP", "").split(",") if s.strip()}
CRON_GRACE = int(os.environ.get("INV_CRON_GRACE_HOURS", "2"))


def sql(query, params=()):
    env.cr.execute(query, params)  # noqa: F821 -- `env` is the odoo shell global
    return env.cr.fetchall()  # noqa: F821


def table_exists(name):
    return bool(sql(
        "SELECT 1 FROM information_schema.tables WHERE table_name = %s", (name,)
    ))


def report(name, status, detail=""):
    print("INVARIANT %s: %s%s" % (name, status, " -- " + detail if detail else ""))


def check(name, fn):
    if name in SKIP:
        report(name, "SKIP", "via INV_SKIP")
        return
    try:
        fn()
    except Exception as e:  # one broken check must not kill the run
        env.cr.rollback()  # noqa: F821 -- failed SQL poisons the cursor
        report(name, "WARN", "check errored: %s" % e)


def unbalanced_moves():
    """Posted journal entries must balance -- always. A violation since the
    deploy means some code path bypasses the posting constraint (raw SQL,
    context tricks)."""
    if not table_exists("account_move_line"):
        report("unbalanced_moves", "SKIP", "account not installed")
        return
    rows = sql(
        """
        SELECT am.id, am.name
        FROM account_move am
        JOIN account_move_line aml ON aml.move_id = am.id
        WHERE am.state = 'posted' AND am.write_date >= %s
        GROUP BY am.id, am.name
        HAVING ROUND(CAST(SUM(aml.debit) - SUM(aml.credit) AS numeric), 4) != 0
        LIMIT 10
        """,
        (SINCE,),
    )
    if rows:
        names = ", ".join(r[1] or str(r[0]) for r in rows)
        report("unbalanced_moves", "FAIL",
               "%d unbalanced posted entries since %s: %s" % (len(rows), SINCE, names))
    else:
        report("unbalanced_moves", "PASS", "no unbalanced posted entries since %s" % SINCE)


def failed_queue_jobs():
    if not table_exists("queue_job"):
        report("failed_queue_jobs", "SKIP", "queue_job not installed")
        return
    rows = sql(
        """
        SELECT model_name, method_name, COUNT(*)
        FROM queue_job
        WHERE state = 'failed' AND date_created >= %s
        GROUP BY model_name, method_name
        ORDER BY 3 DESC
        LIMIT 5
        """,
        (SINCE,),
    )
    if rows:
        total = sum(r[2] for r in rows)
        top = "; ".join("%s.%s x%d" % r for r in rows)
        report("failed_queue_jobs", "FAIL",
               "%d failed jobs since %s: %s" % (total, SINCE, top))
    else:
        report("failed_queue_jobs", "PASS", "no failed jobs since %s" % SINCE)


def stuck_crons():
    rows = sql(
        """
        SELECT COALESCE(cron_name->>'en_US', cron_name::text), nextcall
        FROM ir_cron
        WHERE active AND nextcall < (now() AT TIME ZONE 'UTC') - make_interval(hours => %s)
        ORDER BY nextcall
        LIMIT 10
        """,
        (CRON_GRACE,),
    )
    if rows:
        names = "; ".join("%s (due %s)" % (r[0], r[1]) for r in rows)
        report("stuck_crons", "WARN",
               "%d active crons overdue > %dh: %s" % (len(rows), CRON_GRACE, names))
    else:
        report("stuck_crons", "PASS", "no cron overdue > %dh" % CRON_GRACE)


def negative_internal_stock():
    """Freshly-negative on-hand in internal locations. Projects with a custom
    availability model (dead quants) should INV_SKIP this and implement their
    own in invariant_local.py."""
    if not table_exists("stock_quant"):
        report("negative_internal_stock", "SKIP", "stock not installed")
        return
    rows = sql(
        """
        SELECT sq.product_id, sq.location_id, ROUND(CAST(SUM(sq.quantity) AS numeric), 3)
        FROM stock_quant sq
        JOIN stock_location sl ON sl.id = sq.location_id
        WHERE sl.usage = 'internal'
        GROUP BY sq.product_id, sq.location_id
        HAVING SUM(sq.quantity) < -0.001 AND MAX(sq.write_date) >= %s
        LIMIT 10
        """,
        (SINCE,),
    )
    if rows:
        pairs = "; ".join("product %d @ location %d: %s" % r for r in rows)
        report("negative_internal_stock", "WARN",
               "%d product/location pairs went negative since %s: %s"
               % (len(rows), SINCE, pairs))
    else:
        report("negative_internal_stock", "PASS",
               "no internal location went negative since %s" % SINCE)


for _name, _fn in [
    ("unbalanced_moves", unbalanced_moves),
    ("failed_queue_jobs", failed_queue_jobs),
    ("stuck_crons", stuck_crons),
    ("negative_internal_stock", negative_internal_stock),
]:
    check(_name, _fn)

print("INVARIANT_CORE_DONE since=%s" % SINCE)
