---
name: reference_mute_logger_sql_db
description: "`@mute_logger('odoo.sql_db')` is needed only for psycopg2-level errors (IntegrityError/ProgrammingError), NOT for ValidationError from @api.constrains"
metadata: 
  node_type: memory
  type: reference
  created: 2026-06-08
  updated: 2026-06-08
  originSessionId: da4edc01-0ebc-4255-acda-218830908245
---

`@mute_logger("odoo.sql_db")` suppresses ERROR logs emitted by Odoo's
PostgreSQL wrapper. Use it only when the test deliberately triggers a
**psycopg2-level** error:

- `psycopg2.IntegrityError` — UNIQUE / FK / NOT NULL / CHECK constraint
  violations from `_sql_constraints` or partial unique indexes.
- `psycopg2.ProgrammingError` — bad SQL.
- `psycopg2.DataError` — type mismatches.

Pure `ValidationError` from `@api.constrains` does NOT go through the
sql_db logger: it's a Python-level exception raised inside Odoo's ORM
during the flush callback, never touches psycopg2's error path.

**Cargo-cult smell**: decorating every `assertRaises(ValidationError)`
test with `mute_logger("odoo.sql_db")` "just in case". Reviewers flag
this. Strip the decorator unless the expected exception is `IntegrityError`
or similar.

**Empirical check**: run the test without the decorator and observe
stderr for an `odoo.sql_db ERROR` line. If none appears, the mute was
unnecessary.

## Links

- **Related:** [[reference_test_savepoint_when_needed]] — companion rule for
  the `cr.savepoint()` context manager, which has analogous "only if SQL
  was actually emitted" reasoning.
