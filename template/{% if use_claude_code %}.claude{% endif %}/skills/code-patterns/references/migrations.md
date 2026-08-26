# Migrations & schema stability

Facts verified on Odoo 16 source (`odoo/modules/loading.py`, `migration.py`,
`models.py`); the mechanisms are version-independent unless marked.

## Contents

- Migrations run only on UPGRADE, never on first install
- A migration can silently not run even on an upgrade
- An override must never change a core field's computed schema
- Removing a model: what the loader will NOT clean up
- Re-testing a migration locally

## Migrations run only on UPGRADE, never on first install

A cross-cutting data-cleanup migration must NOT live in a module that is a
first-time install on the target — its `migrations/` are skipped there. Put it
in an **already-installed** module the deploy will `-u`: bump that manifest
version and add `migrations/<version>/post-migrate.py`
(`env = api.Environment(cr, SUPERUSER_ID, {})`, guard `if not version: return`).

## A migration can silently not run even on an upgrade

`MigrationManager` can decide there is nothing to run while the file sits in
the right directory at the right version — the module reaches the new version,
the deploy exits 0, the log simply never says `Running upgrade`. Known
suspects: `_get_files` globs version dirs once at graph build and drops
versions whose glob was empty; module path resolution takes the first hit when
a box holds two checkouts.

**Verify the EFFECT, never the exit code**: after any deploy that depends on a
migration, count the rows it was supposed to change. Keep a standalone twin of
the migration body so it can be run by hand when the loader skips it.

## An override must never change a core field's computed schema

`load_module_graph` computes the expected schema **once per updated package**
with a partial registry, then once more with the full registry. An override in
a late-loading module that changes a core field's computed schema makes the
passes disagree — and each one rewrites the database to its own answer, on
every deploy, forever, against a live service.

Three attributes do it:

| attribute on a late module's override | what flips |
|---|---|
| `required=True` | implied `ondelete` (restrict vs set null) AND the column's NOT NULL |
| explicit `ondelete=` differing from the base module's implied one | the FK |
| `digits=` on a plain `Float` | column TYPE (`numeric` vs `float8`) → full table rewrite |

It is destructive, not just wasteful: `check_foreign_keys` drops-then-adds on a
mismatch, the DROP swallows a lost deadlock while the ADD has no savepoint — so
duplicate constraints accumulate, or the registry fails to load mid-deploy.
Explicit `ondelete=` on the late module does NOT help (the earlier pass does
not see the field at all); moving the override earlier only narrows the window.

**The rule: an override may change behaviour, never the computed schema.** Move
the invariant to a `_sql_constraints` CHECK, an `@api.ondelete` guard, or an
explicit check in `create` (note `create` validates `@api.constrains` only over
field names present in the values, so a constraint alone cannot cover a create
that omits the field).

Diagnose in one command — a clean deploy prints **nothing**:

```bash
odoo -c <conf> -u <module> --workers 0 --stop-after-init \
     --log-handler odoo.schema:DEBUG --logfile=/dev/stdout | grep -E "odoo.schema:"
```

Duplicates accumulated so far:

```sql
SELECT c.relname, a.attname, count(*) FROM pg_constraint fk
JOIN pg_class c ON fk.conrelid = c.oid
JOIN pg_attribute a ON a.attrelid = c.oid AND fk.conkey[1] = a.attnum
WHERE fk.contype = 'f' AND array_length(fk.conkey, 1) = 1
GROUP BY 1, 2 HAVING count(*) > 1 ORDER BY 3 DESC;
```

## Removing a model: what the loader will NOT clean up

1. **The table survives.** `ir.model._drop_table` drops a table only while the
   model still resolves in the registry; a model deleted from source logs
   *"could not be dropped because it did not exist in the registry"* and keeps
   its table. Drop it in a post-migrate (`DROP TABLE IF EXISTS x CASCADE`).
2. **Stored view arches still naming a removed field break the form** — search
   `ir.ui.view` with `active_test=False` on `arch_db like '<field>'`, strip the
   nodes with lxml, write back. Catches hand-edits made straight in prod.
3. **Delete the module's own `ir.cron` records in the migration, in dependency
   order** — otherwise `_process_end` deletes the cron's `ir.actions.server`
   parent before the `ir_cron` row and the whole registry load dies on the FK.
4. Dead `queue_job` rows keyed by `model_name` are not cleaned either.
5. **`DROP TABLE` deadlocks against a live service** — a migration that drops
   a table needs a stopped-service deploy. Odoo commits per module, so a
   failure can leave the DB half-migrated under a running old registry;
   recovery = stop, re-run the same `-u` detached, start.

## Re-testing a migration locally

```sql
UPDATE ir_module_module SET latest_version = '<previous>' WHERE name = '<module>';
```

then re-run `-u`. Odoo commits per module, so a load that failed late still
left earlier phases committed — account for that when reading the result.
Validate a data migration with a real `-u` on a clone, never a shell-imported
function call: lazy flushes (e.g. a `qty_invoiced` recompute crash) only fire
under the full update.
