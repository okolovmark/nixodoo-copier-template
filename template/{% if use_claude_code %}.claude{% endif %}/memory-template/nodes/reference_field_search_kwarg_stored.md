---
name: reference_field_search_kwarg_stored
description: "`search=` kwarg on `fields.X` fires only for non-stored fields; stored fields ignore it — override `_search` instead"
metadata: 
  node_type: memory
  type: reference
  created: 2026-06-08
  updated: 2026-06-08
  originSessionId: da4edc01-0ebc-4255-acda-218830908245
---

`fields.X(search="_method")` provides a custom search function that Odoo
calls when building a SQL query for a domain leaf on this field. The
**critical undocumented constraint**: this function is invoked only when
the field is **non-stored** (`store=False`).

For stored fields, Odoo's `osv/expression.py` runs default SQL comparison
against the column. The `search=` kwarg is silently ignored. This bites
hardest with `fields.Json`: `("my_json_field", "=", 42)` does literal
column equality `WHERE my_json_field = '42'`, which never matches an
array stored as JSON.

**Reference**: `odoo/osv/expression.py` (the `not field.store` gate):
```python
elif not field.store:
    # Non-stored field should provide an implementation of search.
    if not field.search:
        _logger.error("Non-stored field %s cannot be searched.", field, ...)
    ...
    domain = field.determine_domain(model, operator, right)
```
The `not field.store` gate is what makes `search=` stored-incompatible.

**Workaround for stored fields**: override `_search` on the model and
rewrite the offending domain leaf into an `("id", "in", [...])`
sub-result computed via raw SQL. Example pattern (from a
field-change-tracker log model):

```python
@api.model
def _search(self, domain, offset=0, limit=None, order=None, access_rights_uid=None):
    rewritten = list(domain or [])
    for index, term in enumerate(rewritten):
        if isinstance(term, (list, tuple)) and len(term) == 3 and term[0] == "res_ids":
            rewritten[index] = self._rewrite_res_ids_leaf(*term)
    return super()._search(
        rewritten, offset=offset, limit=limit, order=order, access_rights_uid=access_rights_uid
    )

def _rewrite_res_ids_leaf(self, _field, operator, value):
    # Use jsonb @> operator with parameter binding; pair with a GIN index.
    ...
    return ("id", "in", matched_ids)
```

**GIN index** is optional. Without one, `jsonb @>` queries do a seq scan,
which is fine on small/medium tables. Only add a GIN index if profiling
shows the seq scan is a real bottleneck. Odoo's `index=` field kwarg
doesn't accept `'gin'`, so create via `init()` override when needed:
```python
def init(self):
    super().init()
    self.env.cr.execute(
        "CREATE INDEX IF NOT EXISTS my_idx ON %s USING gin (col)" % self._table
    )
```

**Trade-off** of the `_search` override approach: the override loads all
matched ids into Python memory before pushing as `("id", "in", [...])` to
the rest of the domain. Fine for typical query sizes (thousands), bad
for millions. For very large tables consider a raw `_search` returning a
`Query` object with an injected SQL CTE — heavier code, marginal benefit
at our scale.

**Odoo version:** verified on 17; the `not field.store` gate and the
`_search` override pattern are identical in 16.
