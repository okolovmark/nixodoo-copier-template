---
name: reference_m2m_picker_new_button
description: "Hiding the 'New' button in the m2m picker dialog comes from the embedded `<tree create=\"false\">`, not field `options no_create`"
metadata: 
  node_type: memory
  type: reference
  created: 2026-06-08
  updated: 2026-06-08
  originSessionId: da4edc01-0ebc-4255-acda-218830908245
---

# Hiding the "New" button in the m2m picker dialog

When clicking "Add a line" on a `Many2many` field in a form view, Odoo
opens `SelectCreateDialog` — the picker modal with a "Select" + "New" +
"Close" footer. Hiding the "New" button is NOT done via
`options="{'no_create': True}"` on the field. That option only affects the
inline autocomplete dropdown.

## The real control path

`addons/web/static/src/views/fields/x2many/x2many_field.js`:

```js
const selectCreate = useSelectCreate({
    resModel: ...,
    activeActions: this.activeActions,  // from embedded <tree> archInfo
    ...
});
```

`addons/web/static/src/views/fields/relational_utils.js`:

```js
addDialog(SelectCreateDialog, {
    noCreate: !activeActions.create,   // <-- this is the prop
    ...
});
```

`addons/web/static/src/views/view_dialogs/select_create_dialog.xml`:

```xml
<t t-if="!props.noCreate">
    <button class="btn btn-primary o_create_button" ...>New</button>
</t>
```

So `activeActions.create` flows from the embedded tree's `create` attribute
into `noCreate` of the dialog. Default `create="true"` → button shown.

## Correct usage

```xml
<field name="field_ids" options="{'no_open': True}">
    <tree create="false" edit="false">
        <field name="name"/>
        ...
    </tree>
</field>
```

- `<tree create="false">` — hides "New" in the picker dialog (and disables
  inline create on the embedded tree).
- `<tree edit="false">` — disables inline editing in the embedded tree.
- `options="{'no_open': True}"` — prevents row-click from navigating to the
  linked record's form (covers "no edit through the relation").
- `<tree delete="false">` — removes the ✕ icon that unlinks an m2m
  relation (does NOT delete the underlying record). Usually you want to
  keep this enabled so users can remove mistaken selections; set to false
  only when the relation is intentionally append-only.

## Common confusion

- `options="{'no_create': True}"` on the field — controls the **inline
  autocomplete dropdown's** create entry, not the picker dialog. Set it
  for belt-and-suspenders, but the picker dialog ignores it.
- `options="{'no_create_edit': True}"` — controls the "Create and edit..."
  autocomplete entry. Also dialog-unrelated.
- `options="{'no_open': True}"` — disables row-click-to-open. THIS is what
  prevents editing the related record via a click.

## Links

- **Related:** [[reference_m2m_domain_is_ui_only]] — another m2m server-vs-UI
  distinction (UI hints aren't server contracts).

**Odoo version:** OWL paths verified on 17; the same archInfo→noCreate flow
exists in 16 (web OWL views).
