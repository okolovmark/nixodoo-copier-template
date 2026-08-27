#!/usr/bin/env python3
"""Field questions the source tree cannot answer: ir.model.fields + fill rates.

`lsp.py sym` indexes functions and classes only -- a field assignment is
invisible to it -- and the source tree knows nothing about fields added by
modules outside it, by Studio, or by hand. "Does this field exist, who owns
it, how populated is it" is a DATABASE question, answered here.

Usage:
  dbfields.py <model.name>              every field of the model + fill rates
  dbfields.py <model.name> <pattern>    only fields whose name matches
  dbfields.py <pattern>                 which models carry a matching field
                                        (no dot in the argument = this mode)

Connection comes from odoo.conf at the project root, so this answers for the
DATABASE THAT CONF POINTS AT (here: the local restore of the prod dump).
"""

import configparser
import os
import re
import sys

import psycopg2

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(SKILL_DIR, "..", "..", ".."))

# Fill rates are computed per column with count(*) FILTER; chunked so a model
# with hundreds of fields does not build one enormous statement.
FILL_CHUNK = 60


def connect():
    cfg = configparser.ConfigParser()
    cfg.read(os.path.join(ROOT, "odoo.conf"))
    opt = cfg["options"]
    return psycopg2.connect(
        host=opt.get("db_host", "localhost"),
        port=opt.get("db_port", "5432"),
        dbname=opt["db_name"],
        user=opt.get("db_user", "odoo"),
        password=opt.get("db_password", "odoo"),
    )


def owners(cr, field_ids):
    """field id -> defining modules, from ir_model_data. An empty list means
    no module claims it: made by Studio, by hand, or data-created."""
    if not field_ids:
        return {}
    cr.execute(
        "SELECT res_id, array_agg(DISTINCT module ORDER BY module) FROM ir_model_data "
        "WHERE model = 'ir.model.fields' AND res_id = ANY(%s) GROUP BY res_id",
        (list(field_ids),),
    )
    return dict(cr.fetchall())


def search_everywhere(cr, pattern):
    cr.execute(
        "SELECT f.id, f.model, f.name, f.ttype, f.relation, f.store "
        "FROM ir_model_fields f WHERE f.name ~* %s ORDER BY f.model, f.name",
        (pattern,),
    )
    rows = cr.fetchall()
    if not rows:
        sys.exit(f"no field matching /{pattern}/ in ir_model_fields")
    own = owners(cr, [r[0] for r in rows])
    for fid, model, name, ttype, relation, store in rows:
        rel = f" -> {relation}" if relation else ""
        mods = ",".join(own.get(fid, [])) or "NO MODULE (studio/manual/data)"
        print(f"{model}.{name}  [{ttype}{rel}{'' if store else ', not stored'}]  {mods}")
    print(f"({len(rows)} fields; pass a model name for fill rates)")


def model_report(cr, model, pattern):
    cr.execute(
        "SELECT f.id, f.name, f.ttype, f.relation, f.store, f.field_description "
        "FROM ir_model_fields f WHERE f.model = %s AND f.name ~* %s ORDER BY f.name",
        (model, pattern or ".*"),
    )
    rows = cr.fetchall()
    if not rows:
        sys.exit(f"model {model} has no field matching /{pattern or '.*'}/ -- check ir_model_fields spelling")
    own = owners(cr, [r[0] for r in rows])

    table = model.replace(".", "_")
    cr.execute("SELECT column_name FROM information_schema.columns WHERE table_name = %s", (table,))
    columns = {c for (c,) in cr.fetchall()}
    if not columns:
        print(f"({model}: no table {table} -- abstract or _auto=False; no fill rates)")

    total = 0
    fill = {}
    if columns:
        cr.execute(f'SELECT count(*) FROM "{table}"')
        total = cr.fetchone()[0]
        counted = [name for _fid, name, _t, _r, _s, _d in rows if name in columns]
        for i in range(0, len(counted), FILL_CHUNK):
            chunk = counted[i : i + FILL_CHUNK]
            selects = ", ".join(f'count("{c}")' for c in chunk)
            cr.execute(f'SELECT {selects} FROM "{table}"')
            fill.update(zip(chunk, cr.fetchone()))

    print(f"{model}  ({table}: {total} rows)")
    for fid, name, ttype, relation, store, desc in rows:
        rel = f" -> {relation}" if relation else ""
        mods = ",".join(own.get(fid, [])) or "NO MODULE (studio/manual/data)"
        if name in fill:
            n = fill[name]
            pct = f"{100 * n / total:.1f}%" if total else "-"
            filled = f"filled {n}/{total} ({pct})"
        elif store and columns:
            filled = "no column (m2m/o2m or column dropped)"
        else:
            filled = "not stored"
        label = (desc or {}).get("en_US") if isinstance(desc, dict) else desc
        print(f"  {name}  [{ttype}{rel}]  {mods}  {filled}  {label!r}")


def main():
    args = sys.argv[1:]
    if not args or len(args) > 2:
        sys.exit(__doc__.strip())
    conn = connect()
    try:
        cr = conn.cursor()
        if "." in args[0]:
            model_report(cr, args[0], args[1] if len(args) > 1 else None)
        else:
            search_everywhere(cr, args[0])
    finally:
        conn.close()


if __name__ == "__main__":
    main()
