# Background jobs without queue_job — `ir.cron.trigger` patterns

Two native patterns sharing one mechanism: `cron._trigger()` inserts an
`ir.cron.trigger` row + `NOTIFY cron_trigger`, so a cron worker picks the job up
immediately instead of waiting for `nextcall`. Exists since 14.0.

## Contents

- Action-triggered background work (button → chunked job)
- Autovacuum chunked-delete (`_gc_*` methods)

## Action-triggered background work

For a UI button kicking off long work that must not block the HTTP response and
must commit progress incrementally. Each cron run is its own transaction —
committed after the method returns, rolled back on exception.

```python
# Source record carries cursor + running flag
init_running = fields.Boolean(readonly=True)
init_last_id = fields.Integer(readonly=True)

def action_initialize(self):
    self.ensure_one()
    if self.init_running:
        raise UserError(_("Already running."))
    self.write({"init_running": True, "init_last_id": 0})
    self.env.ref("module_name.cron_xid")._trigger()
    return {"type": "ir.actions.client", "tag": "display_notification", ...}

@api.model
def _cron_run_jobs(self, chunk_size=None):
    record = self.search([("init_running", "=", True)], limit=1)
    if not record:
        return
    record._process_chunk(chunk_size or _CHUNK_SIZE)
    if self.search_count([("init_running", "=", True)]):
        self.env.ref("module_name.cron_xid")._trigger()  # chain

def _process_chunk(self, chunk_size):
    self.ensure_one()
    chunk_ids = (
        target.with_context(active_test=False)
        .search([("id", ">", self.init_last_id)], order="id asc", limit=chunk_size)
        .ids
    )
    if not chunk_ids:
        self.init_running = False
        return
    # ...do the work...
    self.init_last_id = chunk_ids[-1]
    if len(chunk_ids) < chunk_size:
        self.init_running = False
```

Cron XML — registered but idle, `_trigger()` is what wakes it:

```xml
<record id="cron_xid" model="ir.cron">
    <field name="name">Module: run pending background jobs</field>
    <field name="model_id" ref="model_source_model"/>
    <field name="state">code</field>
    <field name="code">model._cron_run_jobs()</field>
    <field name="interval_number">1</field>
    <field name="interval_type">days</field>
    <field name="numbercall">-1</field>
</record>
```

Properties: resumable (cursor survives any failure), incremental (chunk-sized
transactions), non-blocking (the action only inserts a row), deduplicated (the
backend `init_running` guard is the gate — the form's `invisible` modifier can
show stale state).

Testing: real cron workers don't run inside `TransactionCase` — call
`_process_chunk` / `_cron_run_jobs` directly, and assert a trigger row was
queued (`env["ir.cron.trigger"].search([("cron_id", "=", cron.id)])`). Cursor
caveat: `init_last_id=0` starts at demo data; capture the pre-fixture max id
and start the cursor there.

## Autovacuum chunked-delete (`_gc_*` methods)

High-volume cleanup on write-heavy tables (audit logs, queues, notifications) —
core's own pattern (`bus.bus._gc_messages`, `mail.notification._gc_notifications`):

- Cap one tick at `models.GC_UNLINK_LIMIT` (100_000 — import it, don't clone
  the constant).
- If `deleted >= limit`, re-trigger `base.autovacuum_job` via `_trigger()` so
  the next pass drains the remainder without waiting 24h.

Why chunking: a single big DELETE holds RowExclusiveLock for minutes against
concurrent INSERTs, generates huge WAL, and rolls back entirely on mid-flight
failure; chunks commit progress.

- ORM path: `records = self.search(domain, limit=models.GC_UNLINK_LIMIT);
  records.unlink()`; raw-SQL path (log tables, no per-row hooks):
  `DELETE FROM t WHERE id IN (SELECT id FROM t WHERE <cutoff> LIMIT %s)` then
  `cr.rowcount`. Postgres has **no `DELETE ... LIMIT`** — the `IN (SELECT ...
  LIMIT)` subquery is the idiom.
- **Don't `ORDER BY id`** — deletion order isn't observable, chunking is
  idempotent, and the sort forces a worse plan.
- Expose the retention cutoff as an `ir.config_parameter` (method-side
  `max_age_days=None` param lets tests bypass it); pre-create the parameter row
  in a `noupdate="1"` data file for discoverability — `get_param` already
  defaults safely, the data file is UX, not safety.
