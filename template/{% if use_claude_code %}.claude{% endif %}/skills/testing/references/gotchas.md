# Testing gotchas

Each entry is a measured failure mode, not a style preference.

## Contents

- `cr.savepoint()` around `assertRaises` — only when SQL was already emitted
- `mute_logger("odoo.sql_db")` — only for psycopg2-level errors
- CacheMiss in a traceback = an AccessError the cache was hiding
- Function descriptor binding when captured as class attribute
- Magic numbers — assertion RHS only
- Multi-company: what a green fixture can hide
- Restored-prod-dump fixture traps
- Believing a test run (count, skips, busy port)
- Browser tests: silent skip, `_wait_ready`, selector syntax

## `cr.savepoint()` around `assertRaises` — only when SQL was already emitted

`TransactionCase` wraps the whole test in one transaction; the savepoint trick
is needed only when the failing call already emitted SQL:

- **PG-level errors** (`psycopg2.IntegrityError` from `_sql_constraints`,
  unique indexes): Postgres aborted the transaction — every later statement
  raises `InFailedSqlTransaction`. Savepoint mandatory.
- **`ValidationError` from `@api.constrains` during create/write**: constrains
  fire inside `flush_all`, after INSERT/UPDATE reached PG. Wrap with savepoint
  for hygiene (loops, subTests).

```python
@mute_logger("odoo.sql_db")
def test_unique(self):
    with self.assertRaises(ValidationError), self.cr.savepoint():
        self.Model.create({...})
```

NOT needed when the exception is raised in pure Python before any DB write — a
guard clause `UserError` at the top of a method gets a bare `assertRaises`.
Rule of thumb: stack goes through `flush`/`create`/`write`/`unlink`/
`cr.execute` → savepoint; raises from a top-of-method `if` → bare.

## `mute_logger("odoo.sql_db")` — only for psycopg2-level errors

The decorator suppresses ERROR logs from the PostgreSQL wrapper. Use it only
when the test deliberately triggers `IntegrityError` / `ProgrammingError` /
`DataError`. A `ValidationError` from `@api.constrains` never touches the
sql_db logger — decorating every `assertRaises(ValidationError)` "just in case"
is a cargo-cult smell reviewers flag. Empirical check: run without the
decorator; no `odoo.sql_db ERROR` on stderr = the mute was unnecessary.

## CacheMiss in a traceback = an AccessError the cache was hiding

A bare `odoo.exceptions.CacheMiss` surfacing from a test is usually a hidden
AccessError on a model the acting user cannot read — **read the traceback to
the bottom**: the first exception names the field, the last one the cause.

The DB read happens only when the value is not cached, so a warm cache makes
the code look correct and the failure looks flaky (a different test each run,
same class). `HttpCase.authenticate(user, pwd)` deliberately flushes + clears
the cache mid-test — tests that call it, or run after one that did, are the
ones that "flake". In production every request starts cold, so the "flaky
test" can be a permanent user-facing failure. Core gives `base.group_user`
zero access on `ir.model.fields` — anything resolving a field by name at
runtime needs `sudo()`.

Regression tests for this class MUST assert with a cold cache:
`self.env.invalidate_all()` right before the action under test.

## Function descriptor binding when captured as class attribute

Capturing a plain function (e.g. a registry method) as a **class attribute**
for later restoration: wrap it in `staticmethod(...)`, otherwise reading it
back via `self.attr` returns a method bound to the test case.

```python
# WRONG — bound to the test case on read-back
cls._original_name_search = cls._partner_cls.name_search
# RIGHT
cls._original_name_search = staticmethod(cls._partner_cls.name_search)
```

Local-variable storage is safe; only class-attribute storage triggers this.
Symptom: `AttributeError` deep in framework stacks with `self` mysteriously
being the test case.

## Magic numbers — assertion RHS only

"No magic numbers" applies to **assertion expected-values**, not setup
literals. Assertion RHS: derive from setup (`assertEqual(deleted,
len(old_logs))`). Setup-side literal (`_make_logs(5)`): fine inline. The test:
if production logic changed, would this number need a lockstep change, and
would the test silently pass if it didn't?

## Multi-company: what a green fixture can hide

- A cross-company regression test must put data (stock, prices, terms) in a
  **second ACTIVE company** — merely widening `company_ids` proves nothing.
- Under `TransactionCase` `env.user` is OdooBot running `su=True`, so a
  `setUpClass` granting `cls.env.user.company_ids = [...both...]` props up any
  heuristic that reads user membership: N green tests prove nothing. The guard
  that catches it drives the code as a user belonging to **neither** company.
- Verifying company-scoped values in a shell/test needs
  `env.invalidate_all()` between two company reads — see the code-patterns
  skill, references/fields-computes.md (`depends_context`).

## Restored-prod-dump fixture traps

On a database restored from a production dump:

- **A constant-factor diff in expected-vs-actual money is currency
  conversion.** A companyless product takes the main company's currency; a
  test partner gets its own default pricelist. Fixture rule: give test
  products `company_id = env.company` and pin the order's pricelist to that
  company's currency. Amounts the fixture writes directly never convert.
- **Anonymous portal `HttpCase` routes 404** on a multi-db cluster: `url_open`
  without a session cookie cannot infer the db → nodb routing. Fix in the
  test: `self.authenticate(None, None)` in `setUp`.
- **Sequence-drawn names collide** (`stock.picking` "Reference must be unique
  per company"): imported documents keep their original names and never
  advanced the local `ir_sequence`. `setval()` past the per-company data max.
  Same-prefix sequences of several picking types must get DISJOINT ranges, and
  PG `nextval` is non-transactional — a rolled-back attempt still advances.
- **A fixture may not reuse a record it did not create** — the "first account
  of the right type" on a restore is a real one carrying company history, and
  exact-equality assertions measure that history. Always create.
- **The company may not be open for posting** — a restore carries
  `fiscalyear_lock_date` / `period_lock_date` / `tax_lock_date`; clear them in
  `setUpClass` if the fixtures post moves.

## Believing a test run (count, skips, busy port)

- **`-u <module>` runs ZERO tests, silently, when the module is uninstalled**
  in the target db — the run ends `0 failed, 0 error(s) of 0 tests` and reads
  as green. Always read the test COUNT; if 0, check `ir_module_module.state`
  and use `-i`.
- **A skip is neither a failure nor an error** — grep the log for `skipped`
  before believing coverage.
- **A busy HTTP port turns every HttpCase into a failure**: if the dev service
  is up against the same config, the run logs `Address already in use` (early,
  easy to scroll past) and every request reaches the SERVICE process, which
  answers `Odoo Session Expired`. Stop the service before an HttpCase run;
  check for that line before believing HttpCase failures.

## Browser tests: silent skip, `_wait_ready`, selector syntax

- `start_tour` / `browser_js` need **websocket-client**; without it Odoo skips
  them silently (see the run-believing rules above). Verify a new browser test
  goes RED on deliberately broken JS before trusting it.
- `browser_js`'s ready expression is compared to a **boolean result exactly** —
  a DOM node result never matches, polls the full timeout, then the next
  evaluate raises `TimeoutError`. Write `!!document.querySelector(...)`.
- `Missing dependencies:` / `Some modules could not be started` warnings before
  the lazy frontend bundle lands are transient noise — `Tour Manager is ready.`
  afterwards proves the frontend started.
- Test selector for one class: `--test-tags /module_name:TestClass`
  (`tag/module:class.method`). A file name silently matches nothing and runs
  `0 tests`.
