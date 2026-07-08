---
name: reference_test_savepoint_when_needed
description: "Wrap `assertRaises` with `self.cr.savepoint()` only when the failing call already emitted SQL; not for errors raised before any DB write"
metadata: 
  node_type: memory
  type: reference
  created: 2026-06-08
  updated: 2026-06-08
  originSessionId: da4edc01-0ebc-4255-acda-218830908245
---

# `cr.savepoint()` rule for `assertRaises` blocks

`TransactionCase` wraps the whole test in one transaction. Some failures
poison that transaction or leave it dirty; others don't. The savepoint
trick is needed only for the first kind.

## When savepoint IS required

The failing call already emitted SQL when the error fired:

- **PG-level errors** (`psycopg2.IntegrityError`, `DatabaseError`):
  Postgres aborted the transaction. Any subsequent statement raises
  `InFailedSqlTransaction` until `ROLLBACK`. `tearDown`'s final flush
  blows up. **Savepoint mandatory.**
- **`ValidationError` from `@api.constrains`** during `create`/`write`:
  constrains fire inside `flush_all`, **after** INSERT/UPDATE SQL has
  been emitted to PG. PG tx is fine (no constraint violation at the SQL
  level), but the current transaction holds half-finished records. For
  test hygiene (especially with loops or subTests where state would
  leak between iterations), wrap with savepoint.

Typical companion: `@mute_logger("odoo.sql_db")` — the rolled-back path
emits noisy stack traces through this logger.

```python
@mute_logger("odoo.sql_db")
def test_field_must_belong_to_model(self):
    with self.assertRaises(ValidationError), self.cr.savepoint():
        self.Config.create({...})   # INSERT + flush_all -> constrains -> ValidationError
```

## When savepoint is NOT needed

The exception is raised in pure Python **before** any DB write reaches
the cursor:

- `UserError` / `ValidationError` / `AccessError` / `MissingError` from
  guard clauses at the top of a method
- Anything `raise`d before `self.env[...].create/write/search` is called

No SQL emitted → PG tx untouched → no recovery needed.

```python
def action_initialize(self):
    self.ensure_one()
    if not self.active:
        raise UserError(_("..."))   # <-- no SQL emitted, no savepoint needed in test

# Test:
def test_action_archived_config_raises(self):
    self.config.active = False
    with self.assertRaises(UserError):
        self.config.action_initialize()
```

## Rule of thumb

- Error from already-emitted SQL → `with assertRaises(...), self.cr.savepoint():`
  + `@mute_logger("odoo.sql_db")`.
- Error from a Python guard before any DB call → bare `with assertRaises(...):`.

When in doubt, look at the stack the error comes from. If it goes through
`flush`, `create`, `write`, `unlink`, or `self.env.cr.execute`, you need
savepoint. If it raises from a top-of-method `if ...: raise ...`, you
don't.

## Links

- **Related:** [[reference_mute_logger_sql_db]] — companion "only if SQL was emitted" rule for the logger mute.
