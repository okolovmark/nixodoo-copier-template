# Project-local invariant checks -- PROJECT-OWNED: edit freely, keep YOUR
# version on copier update conflicts.
#
# Appended AFTER invariant-check.py on the same stdin:
#   cat invariant-check.py invariant_local.py | python odoo-bin shell ...
# so every helper defined there is available here: sql(), report(),
# table_exists(), check(), SINCE, SKIP.
#
# Example -- replace with checks that encode YOUR project's guarantees:
#
# def orders_have_amounts():
#     rows = sql(
#         "SELECT name FROM sale_order "
#         "WHERE state = 'sale' AND amount_total = 0 AND write_date >= %s LIMIT 5",
#         (SINCE,),
#     )
#     report("orders_have_amounts", "WARN" if rows else "PASS",
#            ", ".join(r[0] for r in rows) if rows else "")
#
# check("orders_have_amounts", orders_have_amounts)

print("INVARIANT_LOCAL_DONE (no local checks defined)")
