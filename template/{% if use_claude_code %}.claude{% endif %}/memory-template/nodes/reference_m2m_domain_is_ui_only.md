---
name: reference_m2m_domain_is_ui_only
description: "Relational `domain=` is a client-side widget hint, not server validation — add `@api.constrains` for real enforcement"
metadata: 
  node_type: memory
  type: reference
  created: 2026-06-08
  updated: 2026-06-08
  originSessionId: da4edc01-0ebc-4255-acda-218830908245
---

`domain=` parameter on `Many2many` and `Many2one` fields in Odoo (17 and prior, incl. 16) is a **client-side hint for the search widget**, NOT a server-side validation.

**Authoritative source**: `odoo/fields.py` docstring of the `domain` parameter:

> "an optional domain to set on candidate values on the **client side** (domain or a python expression that will be evaluated to provide domain)"

**Verified in source**: `Many2many.write_real` executes `INSERT INTO <relation_table> ... ON CONFLICT DO NOTHING` directly on writes; `self.domain` is never consulted. The single `get_domain_list` call in that method is `invf.get_domain_list(comodel)` for the **inverse field** to update its cache, not the m2m's own domain.

**Therefore — `domain=` doesn't catch:**
- Programmatic ORM writes: `record.write({"m2m_field": [(6, 0, [...])]})`
- JSON-RPC / `call_kw` from the web client
- Migration / data XML loads
- **Most common — UI flow**: user changes a parent `Many2one` (e.g. `model_id`) on a form that controls the m2m's domain. Odoo re-renders the picker widget but does NOT auto-clear already-selected `field_ids`. Stale selections pass through write unchanged.

**The two-layer pattern:**
1. **Server-side defense (mandatory)**: `@api.constrains(...)` on the `Many2many`/`Many2one` field validates on every write. This is the only path that catches the four scenarios above.
2. **UX layer (optional but nice)**: `@api.onchange(parent_field)` clears the dependent m2m via `[Command.clear()]`. This prevents the form-editing user from ever hitting the constraint's ValidationError. Only fires in `Form` contexts (web client / `Form` test helper); has no effect on direct `write` calls.

When reviewers ask "why a constraint when there's already a `domain=`", the answer is the four scenarios above — `domain=` is a UI hint, not a server contract.

**Empirical proof technique**: you can confirm this by overriding the model's memoized `_constraint_methods` cache to `[]` (constraints can't be patched via `unittest.mock.patch.object` because `@api.constrains` registers via `getmembers` and caches function references), then writing a domain-violating value successfully.

## Links

- **Related:** [[reference_m2m_picker_new_button]] — another m2m UI-vs-server distinction.
