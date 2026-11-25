# Side-effect dry run. dry-run-side-effects.sh pipes this file into `odoo shell`;
# it runs the ticket script named in SE_SCRIPT, records every ORM create, write
# and unlink with the call path that made it, and before rolling back prints the
# records that code the script called changed, ahead of the script's own writes.
import datetime
import functools
import ipaddress
import linecache
import os
import re
import sys
import traceback

import psycopg2 as _se_pg
import psycopg2.extensions as _se_pgext
from odoo import models as _se_models
from odoo import sql_db as _se_sql_db

_SE_SCRIPT = os.path.realpath(os.environ["SE_SCRIPT"])
_SE_SHOW = int(os.environ.get("SE_SHOW") or 40)
_SE_OPS = ("create", "write", "unlink")
_SE_REPORT_WORDS = ("describe", "report", "show", "print", "summar", "dump")
_SE_BOOKKEEPING = {
    "mail.message", "mail.tracking.value", "mail.followers", "mail.notification",
    "mail.activity", "bus.bus", "ir.logging",
}
_SE_SKIP_COLUMNS = {"write_date", "write_uid"}
_SE_STAT_SQL = (
    "SELECT relname, n_tup_ins, n_tup_upd, n_tup_del FROM pg_stat_xact_user_tables "
    "WHERE schemaname = current_schema()"
)
_se = {"on": False, "in_script": False, "done": False, "depth": 0, "log": [],
       "late": 0, "modules": {}, "found": 0, "reporting": 0}


def _se_module(path):
    folder = os.path.dirname(path)
    if folder not in _se["modules"]:
        found, cur = None, folder
        while cur and cur != os.path.dirname(cur):
            if os.path.isfile(os.path.join(cur, "__manifest__.py")):
                found = (os.path.basename(cur), cur)
                break
            cur = os.path.dirname(cur)
        _se["modules"][folder] = found
    return _se["modules"][folder]


def _se_origin(method):
    frames, f = [], sys._getframe(2)
    while f is not None:
        frames.append((f.f_code.co_filename, f.f_lineno, f.f_code.co_name))
        f = f.f_back
    frames.reverse()
    script, after = [], []
    for frame in frames:
        if frame[0] == _SE_SCRIPT:
            script.append(frame)
            after = []
        elif script:
            after.append(frame)
    chain = []
    for path, line, name in after:
        module = _se_module(path)
        if module:
            chain.append((module[0], os.path.relpath(path, module[1]), line, name))
    direct = _se["depth"] == 0 and all(step[3] == method for step in chain)
    return script, chain, direct


def _se_names(records):
    on, _se["on"] = _se["on"], False
    try:
        return {r.id: r.display_name for r in records.exists()}
    except Exception:
        return {}
    finally:
        _se["on"] = on


def _se_wrap(method):
    orig = getattr(_se_models.BaseModel, method)

    @functools.wraps(orig)
    def wrapper(self, *args, **kwargs):
        if not _se["on"]:
            return orig(self, *args, **kwargs)
        if _se["done"]:
            _se["late"] += 1
            return orig(self, *args, **kwargs)
        script, chain, direct = _se_origin(method)
        entry = {"op": method, "model": self._name, "script": script, "chain": chain,
                 "direct": direct, "ids": [], "fields": {}, "names": {}}
        if method == "write":
            entry["ids"] = [i for i in self._ids if i]
            entry["fields"] = dict(args[0] if args else kwargs.get("vals") or {})
        elif method == "unlink":
            entry["ids"] = list(self._ids)
            entry["names"] = _se_names(self)
        _se["depth"] += 1
        try:
            result = orig(self, *args, **kwargs)
        finally:
            _se["depth"] -= 1
        if method == "create":
            vals_list = args[0] if args else kwargs.get("vals_list") or []
            if isinstance(vals_list, dict):
                vals_list = [vals_list]
            entry["ids"] = list(result._ids)
            entry["fields"] = {k: v for vals in vals_list for k, v in vals.items()}
        _se["log"].append(entry)
        return result

    return wrapper


def _se_rows(cr, table, ids):
    if not ids:
        return {}
    cr.execute('SELECT * FROM "%s" WHERE id = ANY(%%s)' % table, [list(ids)])
    cols = [d[0] for d in cr.description]
    return {row[cols.index("id")]: dict(zip(cols, row)) for row in cr.fetchall()}


def _se_fmt(env, model, col, value):
    if value is None or value is False:
        return "(empty)"
    if hasattr(value, "_name"):
        return ", ".join("%s (%s %s)" % (r.display_name, r._name, r.id) for r in value) or "(empty)"
    field = env[model]._fields.get(col)
    if field is not None and field.type == "many2one" and isinstance(value, int):
        rec = env[field.comodel_name].browse(value).exists()
        return "%s (%s %s)" % (rec.display_name if rec else "?", field.comodel_name, value)
    if isinstance(value, datetime.datetime):
        text = value.strftime("%Y-%m-%d %H:%M:%S")
    elif isinstance(value, datetime.date):
        text = value.isoformat()
    else:
        text = value if isinstance(value, str) else repr(value)
    text = " ".join(text.split())
    return text if len(text) <= 90 else text[:87] + "..."


def _se_reporting(script):
    for path, line, name in reversed(script):
        if any(word in name.lower() for word in _SE_REPORT_WORDS):
            return "%s()" % name
    for path, line, name in reversed(script):
        if "print(" in (linecache.getline(path, line) or ""):
            return "a print() at line %d" % line
    return None


def _se_src(path, line):
    return (linecache.getline(path, line) or "").strip()


def _se_plumbing(name):
    return any(name == op or name.endswith("_" + op) for op in _SE_OPS)


def _se_writer(entry):
    for step in reversed(entry["chain"]):
        if not _se_plumbing(step[3]):
            return step
    return None


def _se_path(entry):
    steps = []
    for step in entry["chain"]:
        if _se_plumbing(step[3]):
            continue
        if steps and (steps[-1][0], steps[-1][3]) == (step[0], step[3]):
            continue
        steps.append(step)
    if len(steps) > 6:
        steps = steps[:2] + [None] + steps[-3:]
    return " -> ".join("..." if s is None else "%s() [%s]" % (s[3], s[0]) for s in steps)


def _se_caller(script):
    if not script:
        return "Code outside the script"
    path, line, name = script[-1]
    return "Line %d" % line if name == "<module>" else "%s() at line %d" % (name, line)


def _se_report(env):
    cr = env.cr
    notes = []
    _se_desynced(cr._cnx)
    try:
        env.flush_all()
    except Exception as exc:
        notes.append("(flush failed: %s - the report shows what reached the database)" % exc)
    try:
        cr.execute("SELECT 1")
    except Exception:
        print("SIDE-EFFECT REPORT SKIPPED: the error aborted the transaction, nothing to read.")
        print("SE_SUMMARY side_effects=unknown while_reporting=unknown")
        return
    broken = _se_guard_broken(cr._cnx)
    if broken:
        print("SE_GUARD_TAMPERED: %s; a COMMIT in this transaction may have gone through." % broken)
    cr.execute("SELECT pg_current_xact_id()::text")
    if cr.fetchone()[0] != _se["xid"]:
        print("SE_TXN_CHANGED: the transaction this run started in ended under the tracer (a COMMIT or")
        print("ROLLBACK that bypassed the cursor). What the script wrote before it may be COMMITTED on")
        print("%s; the report below covers only what came after." % cr.dbname)
    now = cr.now()
    cr.execute(_SE_STAT_SQL)
    delta = {}
    for table, ins, upd, dele in cr.fetchall():
        b = _se["base"].get(table, (0, 0, 0))
        d = (ins - b[0], upd - b[1], dele - b[2])
        if any(d):
            delta[table] = d
    tables = {}
    for name in env.registry:
        model = env.registry[name]
        if not model._abstract and model._auto:
            tables.setdefault(model._table, name)

    log = _se["log"]
    created = {}
    for entry in log:
        if entry["op"] == "create":
            for rid in entry["ids"]:
                created[(entry["model"], rid)] = entry
    last_write = {}
    for entry in log:
        if entry["op"] == "write":
            for rid in entry["ids"]:
                for fname in entry["fields"]:
                    last_write[(entry["model"], rid, fname)] = entry

    blocks = {}  # one block per script call that led to a side effect

    def add(entry, model, rid, kind, change):
        script = entry["script"]
        first = entry["chain"][0] if entry["chain"] else None
        key = (tuple(script[-1:]), first and (first[0], first[3]))
        block = blocks.setdefault(key, {"entry": entry, "records": {}})
        rec = block["records"].setdefault((model, rid), {"kind": kind, "changes": []})
        if kind == "deleted" or (kind == "created" and rec["kind"] != "deleted"):
            rec["kind"] = kind
        rec["changes"].append((change, entry))

    recomputed, sql_only, planned, same = [], [], {}, {}
    cr.execute("SELECT pg_current_xact_id_if_assigned()::text")
    top = cr.fetchone()[0]
    xmin = str(int(top) % 4294967296) if top else "0"
    old_cr = _se_sql_db.db_connect(cr.dbname).cursor()
    try:
        for table, (ins, upd, dele) in sorted(delta.items()):
            mname = tables.get(table)
            if not mname:
                sql_only.append("%s: +%d ~%d -%d rows (no model: a relation table, or SQL)" % (table, ins, upd, dele))
                continue
            model = env[mname]
            if model._transient or not model._log_access:
                continue
            unlinked = sum(len(e["ids"]) for e in log if e["op"] == "unlink" and e["model"] == mname)
            if dele > unlinked:
                sql_only.append("%s: %d row(s) deleted without unlink() (SQL, or a database ON DELETE CASCADE)"
                                % (table, dele - unlinked))
            cr.execute('SELECT id FROM "%s" WHERE write_date = %%s OR xmin::text = %%s' % table, [now, xmin])
            ids = [r[0] for r in cr.fetchall()]
            existing = [i for i in ids if (mname, i) not in created]
            new_rows = _se_rows(cr, table, existing)
            old_rows = _se_rows(old_cr, table, existing)
            names = _se_names(model.browse(existing)) if existing else {}
            for rid in existing:
                new, old = new_rows.get(rid, {}), old_rows.get(rid)
                if old is None:
                    sql_only.append("%s %s %s: row inserted without create() (SQL)" % (mname, rid, names.get(rid, "")))
                    continue
                if all(col in _SE_SKIP_COLUMNS or old.get(col) == value for col, value in new.items()):
                    same.setdefault(table, []).append(rid)
                    continue
                for col, value in new.items():
                    if col in _SE_SKIP_COLUMNS or old.get(col) == value:
                        continue
                    change = "%s: %s -> %s" % (col, _se_fmt(env, mname, col, old.get(col)),
                                               _se_fmt(env, mname, col, value))
                    writer = last_write.get((mname, rid, col))
                    field = model._fields.get(col)
                    label = "%s %s %s" % (mname, rid, names.get(rid, ""))
                    if writer is None:
                        if field is not None and field.compute and field.store:
                            deps = dict.fromkeys(env.registry.field_depends.get(field) or field._depends or ())
                            recomputed.append("%s  %s\n        computed from: %s" % (label, change, ", ".join(deps) or "?"))
                        else:
                            sql_only.append("%s  %s  (no create/write recorded: SQL)" % (label, change))
                    elif writer["direct"]:
                        planned.setdefault(("write", mname), set()).add(rid)
                    else:
                        add(writer, mname, rid, "existing", change)
    finally:
        old_cr.close()

    for entry in log:
        model = entry["model"]
        if env[model]._transient:
            continue
        op = entry["op"]
        if entry["direct"]:
            if op != "write":
                planned.setdefault((op, model), set()).update(entry["ids"])
            continue
        if op == "create":
            for rid in entry["ids"]:
                add(entry, model, rid, "created", None)
        elif op == "unlink":
            for rid in entry["ids"]:
                add(entry, model, rid, "deleted", "deleted (was %s)" % (entry["names"].get(rid) or "?"))
        else:
            for rid in entry["ids"]:
                if (model, rid) not in created:
                    continue
                rec = env[model].browse(rid).exists()
                for fname in entry["fields"]:
                    if last_write.get((model, rid, fname)) is not entry:
                        continue
                    value = rec[fname] if rec and fname in rec._fields else None
                    add(entry, model, rid, "created", "%s: set to %s" % (fname, _se_fmt(env, model, fname, value)))

    def quiet(model):
        return model in _SE_BOOKKEEPING or model.endswith(".log")

    for block in blocks.values():
        block["main"] = {k: v for k, v in block["records"].items() if not quiet(k[0])}
        block["books"] = {k: v for k, v in block["records"].items() if quiet(k[0])}
        block["reporting"] = _se_reporting(block["entry"]["script"])
    main = [b for b in blocks.values() if b["main"]]
    main.sort(key=lambda b: (b["reporting"] is None,
                             not any(r["kind"] in ("existing", "deleted") for r in b["main"].values())))
    records = {k for b in main for k in b["main"]}
    reporting_records = {k for b in main if b["reporting"] for k in b["main"]}
    _se["found"], _se["reporting"] = len(records), len(reporting_records)

    bar = "=" * 78
    print()
    print(bar)
    if records:
        print("SIDE EFFECTS: %d record(s) changed by code the script called, not by the script itself"
              % len(records))
        if reporting_records:
            print("!! %d of them while the script was printing its report, which must only read"
                  % len(reporting_records))
        print("   Each block below: the script line, what it called, and every record that call")
        print("   changed, with the function that wrote it.")
    else:
        print("SIDE EFFECTS: none - every write came from the script's own create/write/unlink calls")
    print("   dry run on %s, rolled back after this report" % cr.dbname)
    print(bar)
    for note in notes:
        print(note)

    kind_text = {"existing": "existing record", "created": "created by this call",
                 "deleted": "deleted by this call"}
    order = {"existing": 0, "deleted": 1, "created": 2}
    for n, block in enumerate(main, 1):
        entry = block["entry"]
        script = entry["script"]
        print()
        if script:
            path, line, name = script[-1]
            where = "line %d" % line if name == "<module>" else "line %d in %s()" % (line, name)
            print("[%d] %s: %s" % (n, where, _se_src(path, line)))
            if len(script) > 1:
                print("    reached from line %d: %s" % (script[0][1], _se_src(script[0][0], script[0][1])))
        else:
            print("[%d] code outside the script" % n)
        if block["reporting"]:
            print("    !! this ran while %s was printing the script's report - a report must only read" % block["reporting"])
        first = entry["chain"][0] if entry["chain"] else None
        if first and not _se_plumbing(first[3]):
            print("    what happened: %s called %s() in %s; that call changed %d record(s):" % (
                _se_caller(script), first[3], first[0], len(block["main"])))
        else:
            print("    what happened: %s called %s(); inside it, Odoo changed %d other record(s):" % (
                _se_caller(script), first[3] if first else entry["op"], len(block["main"])))
        items = sorted(block["main"].items(),
                       key=lambda kv: (order[kv[1]["kind"]], kv[0][0] == "ir.sequence", kv[0]))
        for (model, rid), rec in items[:_SE_SHOW]:
            name = _se_names(env[model].browse(rid)).get(rid, "") if rec["kind"] != "deleted" else ""
            print("    - %s %s %s (%s)" % (model, rid, name, kind_text[rec["kind"]]))
            by_writer = {}
            for change, writer_entry in rec["changes"]:
                writer = _se_writer(writer_entry)
                key = ("%s/%s:%d %s()" % writer if writer else "the ORM itself (inverse, cascade or default)",
                       _se_path(writer_entry))
                slot = by_writer.setdefault(key, {"ops": [], "changes": []})
                if writer_entry["op"] not in slot["ops"]:
                    slot["ops"].append(writer_entry["op"])
                if change:
                    slot["changes"].append(change)
            for (writer, path), slot in by_writer.items():
                for change in slot["changes"]:
                    print("        %s" % change)
                verbs = ", ".join({"create": "created", "write": "written", "unlink": "deleted"}[op]
                                  for op in slot["ops"])
                print("        %s by %s" % (verbs, writer))
                if path and " -> " in path:
                    print("        path: %s" % path)
            if model == "ir.sequence":
                print("        note: PostgreSQL sequences do not roll back; this dry run used these numbers up")
        if len(items) > _SE_SHOW:
            print("    ... %d more record(s) (SE_SHOW=%d lists them all)" % (len(items) - _SE_SHOW, len(items)))
        if block["reporting"]:
            print("    to do: take what the report prints from fields; do not call %s() while printing"
                  % (first[3] if first else "a method that writes"))
        else:
            print("    to do: confirm each change above is intended before the commit run, and make the")
            print("           script print it as planned; if one is not, avoid the call that makes it")

    if recomputed:
        print()
        print("--- Stored computed fields recomputed on existing records: no write() call; Odoo")
        print("    recomputed them because something they depend on changed")
        for text in recomputed[:_SE_SHOW]:
            print("    " + text)
        if len(recomputed) > _SE_SHOW:
            print("    ... %d more" % (len(recomputed) - _SE_SHOW))
    if sql_only:
        print()
        print("--- Written below the ORM: no create/write/unlink recorded for these")
        for text in sql_only:
            print("    " + text)
    books = {}
    for block in blocks.values():
        for (model, rid), rec in block["books"].items():
            books.setdefault(model, set()).add(rid)
    if books:
        print()
        print("--- Chatter and bookkeeping written along the way")
        for model, ids in sorted(books.items()):
            print("    %s x%d" % (model, len(ids)))
    print()
    print("--- The script's own writes (planned)")
    for (op, model), ids in sorted(planned.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        ids = sorted(ids)
        more = " ... %d more" % (len(ids) - 10) if len(ids) > 10 else ""
        print("    %-7s %s x%d: %s%s" % (op, model, len(ids), ", ".join(map(str, ids[:10])), more))
    if not planned:
        print("    none")
    if same:
        print()
        print("--- Rows rewritten with the same values (no change; listed so every table count is explained)")
        for table, ids in sorted(same.items()):
            print("    %s: %s" % (table, ", ".join(map(str, sorted(ids)[:10])) + (" ..." if len(ids) > 10 else "")))
    print()
    print("--- Every table written in this transaction (+inserted ~updated -deleted)")
    for table, (ins, upd, dele) in sorted(delta.items()):
        print("    %-40s +%d ~%d -%d" % (table, ins, upd, dele))
    if not delta:
        print("    none")
    print(bar)
    print("SE_SUMMARY side_effects=%d while_reporting=%d" % (_se["found"], _se["reporting"]))


def _se_finish(env):
    if _se["done"]:
        return
    _se["done"] = True
    on, _se["on"] = _se["on"], False
    try:
        _se_report(env)
    except Exception:
        print("SIDE-EFFECT REPORT FAILED:")
        traceback.print_exc()
    finally:
        _se["on"] = on


_se_cr = env.cr  # noqa: F821 - odoo shell provides env
_SE_PG_PORT = os.environ.get("SE_PG_PORT") or ""


def _se_proc(pid):
    """(parent pid, start time in epoch seconds, argv[0]) of a process on this machine, or None."""
    try:
        with open("/proc/%d/stat" % pid) as fh:
            data = fh.read()
        with open("/proc/%d/cmdline" % pid, "rb") as fh:
            argv0 = fh.read().split(b"\0")[0]
        with open("/proc/stat") as fh:
            btime = next(int(line.split()[1]) for line in fh if line.startswith("btime "))
    except (OSError, StopIteration, ValueError):
        return None
    fields = data[data.rindex(")") + 2:].split()
    return int(fields[1]), btime + int(fields[19]) / os.sysconf("SC_CLK_TCK"), argv0


def _se_not_local(raw_cur):
    """Why the server behind this cursor is not this machine's project cluster, or ''.

    Positive identity: the backend serving this connection is a process here whose parent
    is a postgres server started when the server says it started."""
    raw_cur.execute("SELECT host(inet_server_addr()), current_setting('port'), pg_backend_pid(), "
                    "extract(epoch FROM pg_postmaster_start_time())")
    addr, port, pid, started = raw_cur.fetchone()
    reasons = []
    if addr is not None:
        ip = ipaddress.ip_address(addr)
        if not (getattr(ip, "ipv4_mapped", None) or ip).is_loopback:
            reasons.append("the server answers on %s" % addr)
    if not _SE_PG_PORT:
        reasons.append("SE_PG_PORT is not set (run this through dry-run-side-effects.sh)")
    elif port != _SE_PG_PORT:
        reasons.append("the server's port is %s, not the project cluster's %s" % (port, _SE_PG_PORT))
    backend = _se_proc(pid)
    postmaster = backend and _se_proc(backend[0])
    if not (backend and backend[2].startswith(b"postgres") and postmaster
            and os.path.basename(postmaster[2]).startswith(b"postgres")
            and abs(postmaster[1] - float(started)) <= 5):
        reasons.append("backend %d is not a child of a PostgreSQL server started on this machine "
                       "when the server says it started" % pid)
    return "; ".join(reasons)


_se_why = _se_not_local(_se_cr._obj)
if _se_why:
    print("SE_REFUSED_NOT_LOCAL: %s; nothing ran." % _se_why)
    raise SystemExit(3)


class _SeBlocked(Exception):
    pass


def _se_block(what):
    print("SE_BLOCKED: %s - a side-effect dry run never ends its transaction" % what)
    raise _SeBlocked(what) from None


# On the server, whatever the client does: a deferred constraint trigger that
# always raises fires at COMMIT, so an armed transaction cannot commit, however
# the COMMIT is sent. Every transaction is armed before its first statement.
_SE_GUARD_BODY = "BEGIN RAISE EXCEPTION 'side-effect dry run: this transaction never commits'; END"
_SE_GUARD_TGTYPE = 5  # ROW | INSERT, AFTER
_SE_ARM_SQL = (
    "CREATE TEMP TABLE IF NOT EXISTS se_never_commit (x int); "
    "CREATE OR REPLACE FUNCTION pg_temp.se_never_commit() RETURNS trigger LANGUAGE plpgsql AS "
    "$f$" + _SE_GUARD_BODY + "$f$; "
    "DROP TRIGGER IF EXISTS se_never_commit ON pg_temp.se_never_commit; "
    "CREATE CONSTRAINT TRIGGER se_never_commit AFTER INSERT ON pg_temp.se_never_commit "
    "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION pg_temp.se_never_commit(); "
    "INSERT INTO pg_temp.se_never_commit VALUES (1)"
)
_se_base_cursor = _se_pgext.connection.cursor
_se_base_rollback = _se_pgext.connection.rollback
_se_base_autocommit = _se_pgext.connection.autocommit
_se_base_isolation = _se_pgext.connection.set_isolation_level
_se_base_session = _se_pgext.connection.set_session
_se_orig_rollback = _se_sql_db.Cursor.rollback


def _se_arm(cnx):
    raw = _se_base_cursor(cnx)
    try:
        # Under session_replication_role = replica the trigger would not be queued at
        # all; arm under origin, then give the script its own setting back.
        raw.execute("SELECT current_setting('session_replication_role')")
        role = raw.fetchone()[0]
        if role != "origin":
            raw.execute("SET LOCAL session_replication_role = origin")
        raw.execute(_SE_ARM_SQL)
        if role in ("replica", "local"):
            raw.execute("SET LOCAL session_replication_role = %s" % role)
    finally:
        raw.close()


def _se_guard_broken(cnx):
    """'' while this transaction's guard is in place, else what is wrong with it."""
    raw = _se_base_cursor(cnx)
    try:
        raw.execute("SELECT to_regclass('pg_temp.se_never_commit') IS NOT NULL")
        if not raw.fetchone()[0]:
            return "the guard table is gone"
        raw.execute("SELECT count(*) FROM pg_temp.se_never_commit")
        rows = raw.fetchone()[0]
        raw.execute("SELECT t.tgenabled::text, t.tgtype, t.tgdeferrable, t.tginitdeferred, t.tgconstraint <> 0, "
                    "t.tgfoid = to_regprocedure('pg_temp.se_never_commit()'), p.prosrc, l.lanname "
                    "FROM pg_trigger t JOIN pg_proc p ON p.oid = t.tgfoid JOIN pg_language l ON l.oid = p.prolang "
                    "WHERE t.tgrelid = 'pg_temp.se_never_commit'::regclass")
        triggers = raw.fetchall()
    finally:
        raw.close()
    if rows != 1:
        return "the guard table holds %s rows, not 1" % rows
    if len(triggers) != 1:
        return "the guard table has %d triggers, not 1" % len(triggers)
    enabled, tgtype, deferrable, deferred, constraint, own_function, body, language = triggers[0]
    if enabled not in ("O", "A"):
        return "the guard trigger is disabled (%s)" % enabled
    if (tgtype, deferrable, deferred, constraint) != (_SE_GUARD_TGTYPE, True, True, True):
        return "the guard trigger is no longer a deferred constraint trigger on insert"
    if not own_function or language != "plpgsql" or body != _SE_GUARD_BODY:
        return "the guard trigger no longer calls the guard function as armed"
    return ""


def _se_desynced(cnx):
    """psycopg2 believes a transaction is open, the server says none is: something ended it
    behind the driver, and from here every statement would run on its own. Resync and say so."""
    if cnx.status == _se_pgext.STATUS_BEGIN and \
            cnx.info.transaction_status == _se_pgext.TRANSACTION_STATUS_IDLE:
        print("SE_TXN_CHANGED: the transaction ended behind the driver; the server-side guard "
              "refuses a COMMIT, but the run's analysis is incomplete.")
        _se_base_rollback(cnx)
        return True
    return False


_SE_TXN_SQL = re.compile(
    r"(?:^|;)\s*(?:commit|end|abort|rollback(?!\s+to\b)|begin|start\s+transaction|prepare\s+transaction)\b",
    re.IGNORECASE)
_SE_SQL_COMMENT = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)
_SE_DBLINK = re.compile(r"\bdblink", re.IGNORECASE)
_SE_GUARD_NAME = re.compile(r"se_never_commit", re.IGNORECASE)


def _se_guard(cur, query):
    cnx = cur.connection
    if _se_desynced(cnx):
        _se_block("the transaction was ended behind the driver")
    text = query
    if hasattr(query, "as_string"):
        try:
            text = query.as_string(cur)
        except Exception:
            text = ""
    elif isinstance(query, bytes):
        text = query.decode("utf-8", "replace")
    if isinstance(text, str):
        if _SE_DBLINK.search(text):
            _se_block("dblink opens its own connection, which this dry run cannot hold back")
        if _SE_GUARD_NAME.search(text):
            _se_block("the script touched the dry run's own commit guard")
        # An early, readable refusal; the server-side guard is what actually holds.
        if _SE_TXN_SQL.search(_SE_SQL_COMMENT.sub(" ", text)):
            _se_block("transaction control in SQL: %s" % " ".join(text.split())[:100])
    if cnx.status == _se_pgext.STATUS_READY and not _se_base_autocommit.__get__(cnx):
        _se_arm(cnx)


def _se_after(cur):
    if _se_desynced(cur.connection):
        _se_block("a statement ended the transaction")


class _SeCursor:
    """Every psycopg2 cursor of this process, so no statement reaches the server unchecked."""

    __slots__ = ("_se_cur",)

    def __init__(self, cur):
        object.__setattr__(self, "_se_cur", cur)

    def __getattr__(self, name):
        return getattr(self._se_cur, name)

    def __setattr__(self, name, value):
        setattr(self._se_cur, name, value)

    def __iter__(self):
        return iter(self._se_cur)

    def __enter__(self):
        self._se_cur.__enter__()
        return self

    def __exit__(self, *exc):
        return self._se_cur.__exit__(*exc)

    def _run(self, query, method, *args, **kwargs):
        _se_guard(self._se_cur, query)
        try:
            result = method(*args, **kwargs)
        except BaseException:
            _se_desynced(self._se_cur.connection)
            raise
        _se_after(self._se_cur)
        return result

    def execute(self, query, vars=None):
        return self._run(query, self._se_cur.execute, query, vars)

    def executemany(self, query, vars_list):
        return self._run(query, self._se_cur.executemany, query, vars_list)

    def callproc(self, procname, *args, **kwargs):
        return self._run(procname, self._se_cur.callproc, procname, *args, **kwargs)

    def copy_expert(self, sql, file, *args, **kwargs):
        return self._run(sql, self._se_cur.copy_expert, sql, file, *args, **kwargs)


def _se_cnx_cursor(self, *args, **kwargs):
    return _SeCursor(_se_base_cursor(self, *args, **kwargs))


def _se_cnx_commit(self):
    _se_block("connection.commit()")


def _se_cnx_autocommit(self, value):
    if value:
        _se_block("connection.autocommit = True")
    _se_base_autocommit.__set__(self, value)


def _se_cnx_isolation(self, level):
    if level == _se_pgext.ISOLATION_LEVEL_AUTOCOMMIT:
        _se_block("connection.set_isolation_level(AUTOCOMMIT)")
    return _se_base_isolation(self, level)


def _se_cnx_session(self, *args, **kwargs):
    if kwargs.get("autocommit") or (len(args) > 3 and args[3]):
        _se_block("connection.set_session(autocommit=True)")
    return _se_base_session(self, *args, **kwargs)


_se_base_connect = _se_pg.connect


def _se_connect(*args, **kwargs):
    factory = kwargs.get("connection_factory")
    if factory is None:
        kwargs["connection_factory"] = _se_sql_db.PsycoConnection
    elif not issubclass(factory, _se_sql_db.PsycoConnection):
        _se_block("a database connection outside Odoo's connection class")
    cnx = _se_base_connect(*args, **kwargs)
    raw = _se_base_cursor(cnx)
    try:
        why = _se_not_local(raw)
    finally:
        raw.close()
    _se_base_rollback(cnx)
    if why:
        cnx.close()
        _se_block("a connection to a server that is not local (%s)" % why)
    return cnx


def _se_rollback():
    _se_finish(env)  # noqa: F821
    _se_orig_rollback(_se_cr)


def _se_blocked_commit(self):
    if self is _se_cr:
        if _se["in_script"]:
            print("COMMIT BLOCKED: a side-effect dry run never commits")
        _se_finish(env)  # noqa: F821
    return _se_orig_rollback(self)


_se_arm(_se_cr._cnx)
_se_cr.rollback = _se_rollback
_se_sql_db.Cursor.commit = _se_blocked_commit
_se_cr._obj = _SeCursor(_se_cr._obj)
_se_pg.connect = _se_connect
_se_sql_db.PsycoConnection.cursor = _se_cnx_cursor
_se_sql_db.PsycoConnection.commit = _se_cnx_commit
_se_sql_db.PsycoConnection.autocommit = property(_se_base_autocommit.__get__, _se_cnx_autocommit)
_se_sql_db.PsycoConnection.set_isolation_level = _se_cnx_isolation
_se_sql_db.PsycoConnection.set_session = _se_cnx_session
for _se_method in _SE_OPS:
    setattr(_se_models.BaseModel, _se_method, _se_wrap(_se_method))

os.environ["SCRIPT_MODE"] = "dry"
for _se_key in [k for k in os.environ if k.endswith("_COMMIT")]:
    del os.environ[_se_key]
_se_cr.execute("SELECT pg_current_xact_id()::text")
_se["xid"] = _se_cr.fetchone()[0]
_se_cr.execute(_SE_STAT_SQL)
_se["base"] = {r[0]: (r[1], r[2], r[3]) for r in _se_cr.fetchall()}
_se["on"] = _se["in_script"] = True
_se_failed = None
try:
    with open(_SE_SCRIPT) as _se_fh:
        _se_code = compile(_se_fh.read(), _SE_SCRIPT, "exec")
    exec(_se_code, globals())
except BaseException as _se_exc:  # noqa: B036 - SystemExit from the script included
    _se_failed = _se_exc
    if not isinstance(_se_exc, SystemExit) or _se_exc.code not in (None, 0):
        traceback.print_exc()
finally:
    _se["in_script"] = False
    _se_finish(env)  # noqa: F821
    _se_orig_rollback(_se_cr)
    if _se["late"]:
        print("NOTE: %d create/write/unlink call(s) ran after the script rolled back; they were not "
              "analysed and are rolled back too." % _se["late"])
if _se_failed is not None and not (isinstance(_se_failed, SystemExit) and _se_failed.code in (None, 0)):
    print("SCRIPT_FAILED: %s" % _se_failed.__class__.__name__)
