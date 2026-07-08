---
name: reference_ir_cron_trigger_async_pattern
description: Native Odoo action-triggered background work without queue_job — cron._trigger() + a cursor field on the source record
metadata: 
  node_type: memory
  type: reference
  created: 2026-06-08
  updated: 2026-06-08
  originSessionId: da4edc01-0ebc-4255-acda-218830908245
---

# ir.cron + ir.cron.trigger pattern for action-triggered background jobs

When a UI button needs to kick off long-running work that must NOT block the
HTTP response and must commit progress incrementally (so partial failure
doesn't lose hours of work), the native Odoo solution is `ir.cron` + the
`ir.cron.trigger` mechanism. No queue_job dependency required.

## How it works

`ir.cron.trigger` is a system model added in Odoo 14.0: each row is a
`(cron_id, call_at)` pair = "fire this cron at `call_at`". The cron runner
LISTENs on PG channel `cron_trigger`; `cron._trigger(at=None)` does
INSERT-row + `NOTIFY cron_trigger`, so a cron worker picks it up
**immediately** rather than waiting for the next scheduled `nextcall`.

- **Multi-worker prod** (`workers > 0, max_cron_threads > 0`): separate
  cron-worker processes pick up triggers in parallel; mutual exclusion via
  `SELECT ... FOR UPDATE SKIP LOCKED` on trigger rows. HTTP worker doesn't
  block.
- **Single-process dev** (`workers=0`): background thread in the same
  process, still its own transaction.
- **Each cron run is its own transaction** — committed by the cron
  framework after the method returns successfully; rolled back on
  exception.

## The pattern (canonical shape)

```python
# Model: source record carries cursor + running flag
init_running = fields.Boolean(readonly=True)
init_last_id = fields.Integer(readonly=True)

# UI entry point — fires from a button
def action_initialize(self):
    self.ensure_one()
    # ...validate prerequisites (active, fields present, etc.)
    if self.init_running:
        raise UserError(_("Already running."))
    self.write({"init_running": True, "init_last_id": 0})
    self.env.ref("module_name.cron_xid")._trigger()
    return {"type": "ir.actions.client", "tag": "display_notification", ...}

# Cron entry — picks one pending source, processes ONE chunk, re-triggers
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

## Cron XML (idle, trigger-driven)

```xml
<record id="cron_xid" model="ir.cron">
    <field name="name">Module: run pending background jobs</field>
    <field name="model_id" ref="model_source_model"/>
    <field name="state">code</field>
    <field name="code">model._cron_run_jobs()</field>
    <field name="interval_number">1</field>
    <field name="interval_type">days</field>
    <field name="numbercall">-1</field>
    <field name="active" eval="True"/>
</record>
```

`active=True` + far `nextcall` = framework registers the cron but it doesn't
fire on its own; `_trigger()` is what wakes it up.

## Key properties

- **Resumable**: cursor (`init_last_id`) on the source record survives any
  failure; next cron run picks up where the previous left off.
- **Incremental**: each chunk commits independently; no megalithic
  transaction holding locks for hours.
- **Non-blocking**: HTTP worker that fired the action returns immediately
  after `_trigger()` (just inserts a row + NOTIFY).
- **Deduplicated**: backend `if self.init_running: raise UserError(...)` is
  the gate; UI `invisible="init_running"` may show stale state because
  forms don't auto-refresh — rely on the backend gate, not the modifier.

## Testing

Real cron workers don't run inside `TransactionCase`. Tests call the
methods directly:

- `config._process_chunk(chunk_size=N)` for chunk semantics (cursor
  advance, completion detection).
- `Config._cron_run_jobs(chunk_size=N)` for dispatcher behavior (picks one
  pending, no-op when empty).
- `_pending_triggers = env["ir.cron.trigger"].search([("cron_id", "=", cron.id)])`
  to verify a trigger row was queued after `action_initialize`.

## Cursor caveat in tests

`init_last_id=0` starts at the lowest id, which picks up demo data
before any test-created records. To test chunking against test data only:
capture `existing_max = Model.with_context(active_test=False).search([], order="id desc", limit=1).id`
BEFORE creating fixtures, then set `init_last_id=existing_max`.

## Links

- **Related:** [[reference_odoo_autovacuum_pattern]] — also uses `_trigger()`
  for re-arming, but for system-driven cleanup rather than user-triggered jobs.

**Odoo version:** `ir.cron.trigger` exists since 14.0, so this works on 16 and 17.
