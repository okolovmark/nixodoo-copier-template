---
name: reference_odoo_autovacuum_pattern
description: High-volume cleanup on write-heavy tables — chunk by models.GC_UNLINK_LIMIT then re-trigger the autovacuum cron
metadata: 
  node_type: memory
  type: reference
  created: 2026-06-08
  updated: 2026-06-08
  originSessionId: da4edc01-0ebc-4255-acda-218830908245
---

For autovacuum cleanup on write-heavy tables (audit logs, message queues, notification streams), Odoo core uses a consistent chunked-delete pattern.

**The pattern:**
- `models.GC_UNLINK_LIMIT` (defined in `odoo/models.py`, value `100_000`) caps one autovacuum tick at a single chunk.
- If `deleted >= limit`, the cleanup re-triggers the `base.autovacuum_job` cron via `_trigger()` so the next pass picks up the remainder without waiting 24h for the daily tick.

**Core examples (canonical references):**
- `bus.bus._gc_messages` (`addons/bus/models/bus.py`)
- `mail.notification._gc_notifications` (`addons/mail/models/mail_notification.py`)
- `ir.cron` log cleanup (`addons/base/models/ir_cron.py`)

**Why chunking matters (the operational answer to "why not just `DELETE WHERE old`?"):**
- **Lock contention with concurrent INSERT**: a single big DELETE holds `RowExclusiveLock` minutes; concurrent writers wait or deadlock.
- **WAL pressure**: massive single-tx delete generates huge WAL chunks → replication lag, autovacuum starvation elsewhere.
- **Recovery**: a 700k-row tx that fails mid-flight rolls back entirely; chunked deletes commit progress incrementally.

**ORM vs raw SQL paths — bound is identical, idiom differs:**
- ORM path (core uses this): `records = self.search(domain, limit=models.GC_UNLINK_LIMIT); records.unlink(); if len(records) >= limit: trigger(...)`
- Raw SQL path (faster for log tables, no per-row hooks): `cr.execute("DELETE FROM t WHERE id IN (SELECT id FROM t WHERE <cutoff> LIMIT %s)", ...)` then `cr.rowcount` and `if deleted >= limit: trigger(...)`.

**PostgreSQL gotcha**: `DELETE ... LIMIT N` is NOT supported (MySQL extension). The `WHERE id IN (SELECT ... LIMIT ...)` subquery is the idiomatic Postgres workaround. `ctid IN (...)` is marginally faster but obscures the intent.

**Retention as `ir.config_parameter`**: when adding a `_gc_*` method, expose the cutoff as `ir.config_parameter` so operators tune via Settings -> Technical -> Parameters without redeploy. A method-side parameter (`max_age_days=None`) lets tests bypass the system parameter for isolation; production calls fall through to a `_<model>_retention_days()` helper that reads the param with int-fallback to a module default.

**Pre-create the parameter row via data file with `noupdate="1"`** so it's visible in Settings -> Technical -> Parameters out of the box (discoverability) but NOT overwritten on module upgrade (operator-set values survive). `get_param(key)` already has a built-in `default=False` second argument — it doesn't raise when the key is missing — so the data file is purely for UX, not for safety.

**Don't ORDER BY id**: deletion order isn't observable in autovacuum; chunking is idempotent (re-trigger drains the rest); `ORDER BY id LIMIT N` either forces an extra sort or pushes the planner onto a less efficient PK-scan-with-filter path. Let the planner pick freely.

## Links

- **Related:** [[reference_ir_cron_trigger_async_pattern]] — same `_trigger()` re-arming mechanism, applied to user-triggered jobs.
