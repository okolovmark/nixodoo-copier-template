# Fields & computes — behavior the declarations don't show

Facts verified on Odoo 16 source; re-verify on later majors where marked.

## Contents

- Non-stored compute without `@api.depends` is frozen inside the form
- Stored compute without `@api.depends` never runs on `create()`
- Repairing a stale stored compute
- Overriding a core compute drops its `@api.depends_context`
- `search=` kwarg fires only for non-stored fields
- o2m showing archived rows: `active_test` on the MODEL field
- m2m/m2o `domain=` is client-side only
- `check_company=True` is inert without `_check_company_auto`
- Removing a field declared by two modules can drop the column
- `ormcache` keyed off `res.company`
- A LIST `_inherit` needs an explicit `_name`
- Multi-company scoping: a different mechanism per field kind

## Non-stored compute without `@api.depends` is frozen inside the form

Nothing invalidates it. `modified()` walks the dependency triggers, so a compute
that declares none is never in anybody's trigger list: the value computed on the
first read stays in cache for the rest of the transaction, and in a form for the
whole edit session. The client's onchange round-trip diffs a snapshot of the
view's fields, sees no change, and keeps what it had.

The field is right on every read of a record built in one `create()` call, which
is what makes this survive review AND tests. It only shows once the field is
read while an input it derives from moves:

```python
# WRONG - the flag is right on create and stale ever after
is_intercompany = fields.Boolean(compute="_compute_is_intercompany")

def _compute_is_intercompany(self):
    ic_partner_ids = set(self.env["res.partner"]._intercompany_partner_ids())
    for order in self:
        order.is_intercompany = order.partner_id.id in ic_partner_ids
```

On a form this is a live bug the moment anything reads the field - an `attrs`, a
`column_invisible`, a widget, `<field ... invisible="1"/>` feeding a `parent.`
domain. Picking a customer that flips the flag leaves the dependent columns and
requireds as they were until the record is saved and reloaded.

**Two dependency sets, and only one is expressible.** `partner_id` is; "the ids
of the partners standing for one of our companies" is not - it is a set read
from another model. A partial `@api.depends` is still the fix: it covers the
input the user actually changes, and the inexpressible half degrades to
transaction-scoped staleness. Core lives with the same limit and says so at
`base/models/res_company.py:124` (`# TODO @api.depends(): currently now way to
formulate the dependency on the partner's contact address`).

**When there is genuinely nothing to name** - the value comes from the context,
`env.user`, a config parameter, `id` (which `api.depends` refuses outright), or
a search no field path reaches - write the empty decorator and say why:

```python
@api.depends()  # env.user only; no field of this record feeds it
def _compute_can_edit(self):
    ...
```

`@api.depends()` is legal and core uses it (`base/models/ir_model.py:211`). It
is the difference between "asked and answered" and "never asked", which is what
review and the CI check read. On a STORED field it is not an answer, though -
see the next entry: it means the column is written NULL on `create()`.

Verified on 16.0; the mechanism is unchanged in 17 and 18.

## Stored compute without `@api.depends` never runs on `create()`

A field declared `store=True, compute="_x", readonly=False` with **no
`@api.depends`** is not added to the recompute set on `create()` — the column is
written NULL (Boolean reads back `False`). The compute logic can be perfectly
correct; it just never fires on the normal create path. An explicit `write()`
survives.

To seed such a field from the creation **context**, use a context-reading
`default=`, not a compute:

```python
supplier = fields.Boolean(default=lambda self: self.env.context.get("res_partner_search_mode") == "supplier")
```

Adding `@api.depends_context(...)` instead re-runs the compute on every read and
can overwrite user edits — avoid for editable fields.

## Repairing a stale stored compute

When a stored compute's dependency will never change again (imported data, dead
records), the obvious repairs do NOT work:

```python
env.add_to_compute(Model._fields["state"], records)
records.flush_recordset(["state"])        # -> zero rows changed
```

Recomputing outside the ORM's protocol fills the cache without scheduling the
row update. Worse:

```python
chunk._compute_state()                    # correct value lands in cache
target = {r.id: r.state for r in chunk}   # <- reads the OLD value back
```

The first read prefetches from the DATABASE for the whole prefetch set,
overwriting what the compute just cached. A single-record probe looks fine
(cache hit) — exactly how it passes a spot check and then writes N rows
unchanged.

**What works:** derive the target value in SQL (or plain Python) from the
source data, mirroring the compute method including its special cases, then
`write()` it. Guard the write: skip records whose source data still looks live;
print the planned distribution before writing. Watch for records the ORM can
never close (e.g. a compute that pins a valueless record to a state its actions
cannot leave) — those need a direct `UPDATE`.

## Overriding a core compute drops its `@api.depends_context`

`@api.depends_context` sits on the compute **method**, not the field.
Redefining a field with `compute=` pointing at your own method drops every
context key the core method declared. The field then stops being
context-dependent for the ORM cache: **one cached value per record per
transaction**, whatever `with_company()` / `with_context(...)` the reader used.

- When overriding a core compute, **copy the `@api.depends` AND
  `@api.depends_context` lines across**, then diff against the core decorators.
- Choose the key set by grepping the whole engine call chain for
  `context.get` / `context[` — not just the compute body.
- Symptom: a missing `depends` shows as a stale value; a missing
  `depends_context` shows only under a second context, which is why it survives
  review and tests. `env.invalidate_all()` between two reads changing the
  answer = the cache key is the defect, not the engine.

## `search=` kwarg fires only for non-stored fields

`fields.X(search="_method")` is invoked only when the field is **non-stored**.
For stored fields `osv/expression.py` runs default SQL column comparison and
silently ignores `search=` (the `not field.store` gate). Bites hardest with
`fields.Json`: `("my_json_field", "=", 42)` compares the raw column.

Workaround for stored fields — override `_search` and rewrite the domain leaf:

```python
@api.model
def _search(self, domain, offset=0, limit=None, order=None, count=False, access_rights_uid=None):
    rewritten = list(domain or [])
    for index, term in enumerate(rewritten):
        if isinstance(term, (list, tuple)) and len(term) == 3 and term[0] == "res_ids":
            rewritten[index] = self._rewrite_res_ids_leaf(*term)
    return super()._search(rewritten, offset=offset, limit=limit, order=order,
                           count=count, access_rights_uid=access_rights_uid)
```

(The `count=` kwarg exists on 16 and is gone in 17 — match your major's
signature.) The rewrite loads matched ids into memory as `("id", "in", [...])` —
fine for thousands, not millions. A jsonb `@>` rewrite pairs with a GIN index
only if profiling shows the seq scan matters; `index=` doesn't accept `'gin'`,
create it in an `init()` override.

## o2m showing archived rows: `active_test` on the MODEL field

To keep archived children visible in an o2m (instead of vanishing on archive),
set the context on the **field definition in the model**, not the view:

```python
method_config_ids = fields.One2many("my.model.line", "config_id", context={"active_test": False})
```

`One2many.convert_to_record_multi` filters by the field's **static** context;
view-level `context="..."` never reaches the initial o2m load. Pair with
`<tree decoration-muted="not active">`.

## m2m/m2o `domain=` is client-side only

`domain=` on relational fields is a **search-widget hint**, not server
validation (`Many2many.write_real` never consults it). It does not catch: ORM
writes, JSON-RPC, data loads, and the most common UI flow — the user changes
the parent field controlling the domain and already-selected rows pass through
unchanged.

Two-layer pattern: `@api.constrains(...)` on the field (mandatory — the only
path catching all four), plus optional `@api.onchange(parent_field)` clearing
the dependent m2m via `[Command.clear()]` for UX.

## `check_company=True` is inert without `_check_company_auto`

`write`/`_create` call `_check_company()` **only** when the model sets
`_check_company_auto = True`. On a model without the flag, `check_company=True`
changes exactly one thing: a default company domain in the UI. Core is
inconsistent (16: `account.move`, `sale.order` set the flag; `purchase.order`
does not) — that asymmetry, not your field, is often why a cross-company value
is refused on one document type and accepted on another.

Turning the flag on is a **model-wide** decision: `_check_company()` then
validates every `check_company` field on any write touching one of them or
`company_id`, record-level and state-blind — historical done/cancelled records
with cross-company values start refusing ordinary writes. Alternatives:

- rule needed only while the document is live → `@api.constrains(field,
  "state", "company_id")` skipping historical states;
- core checks too broadly → override `_check_company(fnames=None)` and drop the
  one field from `fnames` for the historical subset (call super twice). Do not
  clear `_check_company_auto` — it unhooks every other checked field.
- Constraints run **before** `_check_company` in `write`, so a custom
  constraint wins the error message and core stays as backstop.

## Removing a field declared by two modules can drop the column

When the same field is declared by two modules and one declaration is removed,
`ir.model._process_end` keeps the column only if another `ir.model.data` xid
still points at the same `ir.model.fields` record. On any DB where the
*surviving* declarer is not installed, the field record is unlinked and the
column dropped — **silent data loss**.

Before removing: check xid ownership (`ir_model_data` rows named
`field_<model>__<field>`) on every target DB. Mitigations: keep the field in
the always-installed module; or a migration re-owning the xid
(`UPDATE ir_model_data SET module='<survivor>' WHERE module='<removed>' AND name=...`);
or accept the drop after verifying 0 rows and 0 consumers.

## A LIST `_inherit` needs an explicit `_name`

`_inherit = "purchase.order"` (single string) implies the model name, but the
moment it becomes a **list** — the normal way to mix an AbstractModel into an
existing model — `_build_model`'s fallback drops to the **class name** and the
registry dies at `_auto_init` with `ValueError: The _name attribute
PurchaseOrderExt is not valid` — reads like a naming complaint, is a missing
`_name`:

```python
class PurchaseOrderExt(models.Model):
    _name = "purchase.order"
    _inherit = ["purchase.order", "my.mixin"]
```

When adding such a mixin: keep `@api.constrains` on the CONCRETE models with
the body on the mixin (a constrains on the abstract referencing undeclared
fields only warns, but stays confusing); keep `write`/`copy_data` plumbing that
needs the module's own `super()` and context in the concrete model — only pure
logic travels; a module referencing the mixin by name should declare the owning
module in `depends` even when it already arrives transitively.

## `ormcache` keyed off `res.company`

`@tools.ormcache()` on a custom helper memoizing company-derived data needs no
custom invalidation for create/write: base `res.company.create()`/`.write()`
call `clear_caches()` unconditionally. Only `unlink()` is not covered — add a
`clear_caches()` override there. Test isolation is framework-handled
(`TransactionCase` registers `addCleanup(registry.clear_caches)` per test).

Related trap: reading a column on a cross-company m2o target inside a guard
trips record rules (`AccessError` for single-company users); m2o **truthiness**
does not read the target row. Use `.sudo()` for such guard reads.

## Multi-company scoping: a different mechanism per field kind

An engine that takes a `company` argument but runs under a user with different
active companies must scope every read explicitly — each kind differently:

| data | mechanism |
|---|---|
| company-dependent (`property`) fields | `record.with_company(company).property_x` — property fields key on `env.company` |
| `product.supplierinfo`, `mrp.bom` (company_id + record rule) | filter: `seller_ids.filtered(lambda s: s.company_id.id in (False, company.id))`, `_bom_find(product, company_id=company.id)` — mandatory under `sudo()`, which bypasses the rule |
| stock quantities (`qty_available`, `free_qty`, …) | `product.with_context(allowed_company_ids=company.ids)` — resolved by `env.companies`, NOT `env.company`; `with_company` does not move them |

Corollaries:

- **Never scope a plain `Many2one` through `with_company`** — inert, and it
  tells the next reader the field is company-dependent when it is not.
- **An override that re-reads a property can UNDO core's correct scoping** —
  core usually opens with `self = self.with_company(self.company_id)`; your
  override reading the same property without scoping overwrites the right value
  with the acting user's. When a company-dependent field is wrong, diff it
  against a plain field written by the same code path.
- **`env.user.company_ids` is never the basis for a business decision** — under
  `sudo` (RPC, shell, cron) `env.user` is OdooBot and the branch flips on every
  non-UI path. Decide from the record (`self.company_id`, `self._origin`).
