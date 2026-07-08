---
name: reference_odoo_x2many_active_test
description: "To show archived rows in an o2m field, set `context={'active_test': False}` on the MODEL field, not on the `<field>` view tag"
metadata: 
  node_type: memory
  type: reference
  created: 2026-06-08
  updated: 2026-06-08
  originSessionId: da4edc01-0ebc-4255-acda-218830908245
---

To make an Odoo o2m field display archived rows in the form view (typical case: a parent has `cascade_archive` of children, the form must still show the cascaded children muted instead of vanishing), set `context={"active_test": False}` on the **field definition in the model**, NOT on the `<field>` tag in the view.

```python
# Model: WORKS
method_config_ids = fields.One2many(
    "my.module.method_config",
    "config_id",
    context={"active_test": False},
)
```

```xml
<!-- View-only: does NOT work for the o2m initial load -->
<field name="method_config_ids" context="{'active_test': False}">
```

**Why.** `odoo/fields.py::One2many.convert_to_record_multi` filters corecords by the model's `_active_name` when:

```python
self.context.get('active_test', record.env.context.get('active_test', True))
```

`self.context` is the **field's static context** from the model definition. The view-level `context="..."` only gets applied to nested-field fetches via `web_read::field_spec.context`, never to the initial `self[field_name]` access that loads the o2m. Default `record.env.context.get('active_test', True)` returns True, so the filter fires unless the field itself overrides it.

The user-facing symptom: the user toggles a row's `active=False` inline, saves, the row vanishes from the o2m list. Hard refresh / restart doesn't help because the implicit active filter applies on every fetch.

**How to apply:**
- New o2m where children carry an `active` field and should remain visible after archive: put `context={"active_test": False}` on the model field.
- View-level context is fine to leave (defensive), but it alone isn't enough.
- Decoration: pair with `<tree decoration-muted="not active">` for visual distinction.
- Odoo core uses this pattern: `addons/mail/views/discuss_channel_views.xml` (`channel_member_ids`), `addons/account/views/account_move_views.xml`, etc. — but the FIELD definitions in their respective models also carry the context.

**Odoo version:** verified on 17; the `convert_to_record_multi` active filter is the same in 16.
