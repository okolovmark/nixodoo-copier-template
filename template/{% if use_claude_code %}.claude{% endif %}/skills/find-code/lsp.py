#!/usr/bin/env python3
"""CLI + daemon over odoo_ls_server (the official Odoo Language Server).

A per-project daemon keeps one indexed server alive (cold index ~16-20s,
queries afterwards are sub-second) behind a unix socket; the CLI starts it
on demand and exits. Line numbers are 1-based, as in grep -n and editors.

Commands:
  def   <file> <line> <symbol|col>   definition (MRO-aware, XML ref/model-aware)
  refs  <file> <line> <symbol|col>   references (see SKILL.md for blind spots)
  hover <file> <line> <symbol|col>   type + owning modules + docstring
  who   <identifier>                 one command: definition(s) + every reference
  sym   <query>                      workspace-wide symbol search
  model <model.name>                 every class defining/extending the model
  open  <file>                       push a file's current content to the server
  bump  <version>                    install an odoo-ls release, point 'current' at it
  status | restart | stop            daemon lifecycle
"""

import hashlib
import json
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
from glob import glob

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(SKILL_DIR, "..", "..", ".."))
BIN_DIR = os.path.expanduser("~/.local/share/odoo-ls")  # shared with the editor (Zed points here too)
RUNTIME_DIR = os.path.join(os.path.expanduser("~"), ".cache", "odoo-ls-cli")
KEY = hashlib.sha1(ROOT.encode()).hexdigest()[:10]
SOCK = os.path.join(RUNTIME_DIR, KEY + ".sock")
LOG = os.path.join(RUNTIME_DIR, KEY + ".log")
IDLE_EXIT = 3 * 3600  # daemon exits after 3h without requests

SYMBOL_KINDS = {5: "class", 6: "method", 12: "function", 13: "variable", 14: "constant", 8: "field"}


def find_bin():
    env = os.environ.get("ODOO_LS_BIN")
    if env and os.path.exists(env):
        return env
    current = os.path.join(BIN_DIR, "current", "odoo_ls_server")
    if os.path.exists(current):
        return current
    cands = glob(os.path.join(BIN_DIR, "*", "odoo_ls_server"))
    cands += glob(os.path.expanduser("~/.local/share/zed/extensions/work/odoo/*/odoo_ls_server"))
    if cands:
        def ver(p):
            m = re.search(r"/([\d.]+)/odoo_ls_server$", p)
            return tuple(int(x) for x in m.group(1).split(".")) if m else ()
        return max(cands, key=ver)
    from shutil import which
    w = which("odoo_ls_server")
    if w:
        return w
    sys.exit("odoo_ls_server not found: run 'lsp.py bump <version>' or set ODOO_LS_BIN")


def read_profile():
    with open(os.path.join(ROOT, "odools.toml")) as f:
        m = re.search(r'^name\s*=\s*"([^"]+)"', f.read(), re.M)
    if not m:
        sys.exit("no [[config]] name in odools.toml")
    return m.group(1)


def bump(version):
    """Install an odoo-ls release into BIN_DIR/<version> and flip the 'current' symlink."""
    import platform
    import tarfile
    import urllib.request
    import zipfile
    osname = {"Linux": "linux", "Darwin": "darwin"}.get(platform.system())
    arch = {"x86_64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64"}.get(platform.machine())
    if not osname or not arch:
        sys.exit(f"unsupported platform {platform.system()}/{platform.machine()}")
    dest = os.path.join(BIN_DIR, version)
    os.makedirs(dest, exist_ok=True)
    base = f"https://github.com/odoo/odoo-ls/releases/download/{version}"
    tgz = os.path.join(dest, "pkg.tar.gz")
    print(f"downloading odoo-{osname}-{arch}-{version}.tar.gz ...", file=sys.stderr)
    urllib.request.urlretrieve(f"{base}/odoo-{osname}-{arch}-{version}.tar.gz", tgz)
    with tarfile.open(tgz) as t:
        t.extractall(dest)
    os.remove(tgz)
    zp = os.path.join(dest, "typeshed.zip")
    print("downloading typeshed.zip ...", file=sys.stderr)
    urllib.request.urlretrieve(f"{base}/typeshed.zip", zp)
    with zipfile.ZipFile(zp) as z:
        z.extractall(os.path.join(dest, "typeshed"))
    os.remove(zp)
    os.chmod(os.path.join(dest, "odoo_ls_server"), 0o755)
    tmp = os.path.join(BIN_DIR, "current.new")
    if os.path.lexists(tmp):
        os.remove(tmp)
    os.symlink(version, tmp)
    os.replace(tmp, os.path.join(BIN_DIR, "current"))
    print(f"odoo-ls {version} installed, current -> {version}")
    try:
        cli_send({"op": "stop"}, timeout=5)
        print("daemon stopped; it restarts on the next query")
    except Exception:
        pass


# --------------------------------------------------------------------------- daemon

class LSP:
    def __init__(self):
        os.makedirs(RUNTIME_DIR, exist_ok=True)
        self.started = time.time()
        self.bin = find_bin()
        self.proc = subprocess.Popen(
            [self.bin, "--config-path", os.path.join(ROOT, "odools.toml"),
             "--selected-config", read_profile(),
             "--log-level", "warn", "--logs-directory", RUNTIME_DIR,
             "--client-process-id", str(os.getpid())],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=open(LOG, "ab"), cwd=ROOT)
        self._wlock = threading.Lock()
        self._pending = {}
        self._id = 0
        self._progress = set()
        self._progress_seen = False
        self.docs = {}  # abs path -> (version, content sha1)
        threading.Thread(target=self._reader, daemon=True).start()
        self._initialize()

    def _write(self, obj):
        b = json.dumps(obj).encode()
        with self._wlock:
            self.proc.stdin.write(b"Content-Length: %d\r\n\r\n" % len(b) + b)
            self.proc.stdin.flush()

    def _reader(self):
        f = self.proc.stdout
        while True:
            line = f.readline()
            if not line:
                break
            if not line.startswith(b"Content-Length:"):
                continue
            n = int(line.split(b":")[1])
            f.readline()
            try:
                self._handle(json.loads(f.read(n)))
            except Exception:
                pass

    def _handle(self, m):
        if "method" in m and "id" in m:  # server -> client request
            res = None
            if m["method"] == "workspace/configuration":
                res = [{"selectedProfile": read_profile(), "autoRefresh": "onSave",
                        "autoRefreshDelay": 1000, "diagMissingImportLevel": "none"}
                       for _ in m["params"]["items"]]
            self._write({"jsonrpc": "2.0", "id": m["id"], "result": res})
        elif m.get("method") == "$/progress":
            v = m["params"].get("value", {})
            if v.get("kind") == "begin":
                self._progress.add(m["params"].get("token"))
                self._progress_seen = True
            elif v.get("kind") == "end":
                self._progress.discard(m["params"].get("token"))
        elif "id" in m:
            q = self._pending.pop(m["id"], None)
            if q is not None:
                q.put(m)

    def request(self, method, params, timeout=120):
        self._id += 1
        rid = self._id
        q = queue.Queue()
        self._pending[rid] = q
        self._write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        try:
            m = q.get(timeout=timeout)
        except queue.Empty:
            self._pending.pop(rid, None)
            raise TimeoutError(f"{method} timed out after {timeout}s")
        if "error" in m and m["error"]:
            raise RuntimeError(json.dumps(m["error"]))
        return m.get("result")

    def notify(self, method, params):
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def _initialize(self):
        self.request("initialize", {
            "processId": os.getpid(),
            "rootUri": "file://" + ROOT,
            "workspaceFolders": [{"uri": "file://" + ROOT, "name": os.path.basename(ROOT)}],
            "capabilities": {
                "workspace": {"configuration": True, "workspaceFolders": True,
                              "symbol": {"symbolKind": {"valueSet": list(range(1, 27))}}},
                "textDocument": {
                    "definition": {"linkSupport": True},
                    "references": {},
                    "hover": {"contentFormat": ["markdown", "plaintext"]},
                    "documentSymbol": {"hierarchicalDocumentSymbolSupport": True},
                },
                "window": {"workDoneProgress": True},
            },
            "initializationOptions": {},
        }, timeout=90)
        self.notify("initialized", {})

    def wait_ready(self, timeout=300):
        start = time.time()
        empty_since = None
        while time.time() - start < timeout:
            if self.proc.poll() is not None:
                raise RuntimeError("odoo_ls_server died, see " + LOG)
            if self._progress_seen and not self._progress:
                empty_since = empty_since or time.time()
                if time.time() - empty_since > 1.5:
                    return True
            else:
                empty_since = None
                if not self._progress_seen and time.time() - start > 60:
                    return True
            time.sleep(0.2)
        return False

    def open_doc(self, path):
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
        sha = hashlib.sha1(text.encode()).hexdigest()
        cur = self.docs.get(path)
        if cur and cur[1] == sha:
            return
        uri = "file://" + path
        lang = {"py": "python", "xml": "xml", "csv": "csv"}.get(path.rsplit(".", 1)[-1], "plaintext")
        if cur:
            self.notify("textDocument/didClose", {"textDocument": {"uri": uri}})
        version = (cur[0] + 1) if cur else 1
        self.notify("textDocument/didOpen", {"textDocument": {
            "uri": uri, "languageId": lang, "version": version, "text": text}})
        self.docs[path] = (version, sha)


def run_daemon():
    os.makedirs(RUNTIME_DIR, exist_ok=True)
    if os.path.exists(SOCK):
        try:  # already running?
            cli_send({"op": "ping"}, timeout=5)
            return
        except Exception:
            os.unlink(SOCK)
    srv = socket.socket(socket.AF_UNIX)
    try:
        srv.bind(SOCK)
    except OSError:
        return  # lost the race to another daemon
    srv.listen(8)
    lsp = LSP()
    state = {"last": time.time()}
    qlock = threading.Lock()

    def idle_watch():
        while True:
            time.sleep(60)
            if time.time() - state["last"] > IDLE_EXIT or lsp.proc.poll() is not None:
                try:
                    os.unlink(SOCK)
                finally:
                    os._exit(0)

    threading.Thread(target=idle_watch, daemon=True).start()

    def serve(conn):
        try:
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = conn.recv(65536)
                if not chunk:
                    break
                buf += chunk
            req = json.loads(buf)
            state["last"] = time.time()
            op = req.get("op")
            if op == "ping":
                resp = {"ok": 1, "pid": os.getpid(), "bin": os.path.realpath(lsp.bin),
                        "ready": bool(lsp._progress_seen and not lsp._progress),
                        "uptime": int(time.time() - lsp.started), "root": ROOT}
            elif op == "stop":
                conn.sendall(b'{"ok": 1}\n')
                conn.close()
                try:
                    os.unlink(SOCK)
                finally:
                    os._exit(0)
            elif op == "open":
                lsp.wait_ready()
                with qlock:
                    lsp.open_doc(req["path"])
                resp = {"ok": 1}
            elif op == "query":
                lsp.wait_ready()
                with qlock:
                    if req.get("path"):
                        lsp.open_doc(req["path"])
                    result = lsp.request(req["method"], req["params"],
                                         timeout=req.get("timeout", 120))
                resp = {"ok": 1, "result": result}
            else:
                resp = {"ok": 0, "error": "unknown op"}
        except Exception as e:
            resp = {"ok": 0, "error": f"{type(e).__name__}: {e}"}
        try:
            conn.sendall((json.dumps(resp) + "\n").encode())
            conn.close()
        except Exception:
            pass

    while True:
        conn, _ = srv.accept()
        threading.Thread(target=serve, args=(conn,), daemon=True).start()


# --------------------------------------------------------------------------- client

def cli_send(req, timeout=360):
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(timeout)
    s.connect(SOCK)
    s.sendall((json.dumps(req) + "\n").encode())
    buf = b""
    while not buf.endswith(b"\n"):
        chunk = s.recv(65536)
        if not chunk:
            break
        buf += chunk
    resp = json.loads(buf)
    if not resp.get("ok"):
        sys.exit("daemon error: " + str(resp.get("error")))
    return resp


def ensure_daemon():
    try:
        return cli_send({"op": "ping"}, timeout=5)
    except Exception:
        pass
    os.makedirs(RUNTIME_DIR, exist_ok=True)
    print("starting odoo-ls daemon (first query waits for the index, ~20s)...", file=sys.stderr)
    subprocess.Popen([sys.executable, os.path.abspath(__file__), "daemon"],
                     stdout=open(LOG, "ab"), stderr=subprocess.STDOUT,
                     start_new_session=True, cwd=ROOT)
    for _ in range(60):
        time.sleep(0.5)
        try:
            return cli_send({"op": "ping"}, timeout=5)
        except Exception:
            continue
    sys.exit("daemon failed to start, see " + LOG)


def resolve_pos(path, line_1, sym_or_col):
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.read().split("\n")
    if line_1 < 1 or line_1 > len(lines):
        sys.exit(f"line {line_1} out of range for {path}")
    text = lines[line_1 - 1]
    if sym_or_col.isdigit():
        return int(sym_or_col) - 1
    i = text.find(sym_or_col)
    if i < 0:
        sys.exit(f"'{sym_or_col}' not found on {path}:{line_1}: {text.strip()!r}")
    return i + max(0, (len(sym_or_col) - 1) // 2)


def fmt_loc(loc):
    uri = loc.get("uri") or loc.get("targetUri") or ""
    rng = loc.get("range") or loc.get("targetSelectionRange") or loc.get("targetRange") or {}
    p = uri[7:] if uri.startswith("file://") else uri
    rel = os.path.relpath(p, ROOT) if p.startswith("/") else p
    return f"{rel}:{rng.get('start', {}).get('line', 0) + 1}"


def print_locs(result, empty_msg):
    if not result:
        print(empty_msg)
        return 0
    locs = result if isinstance(result, list) else [result]
    seen = []
    for loc in locs:
        s = fmt_loc(loc)
        if s not in seen:
            seen.append(s)
    for s in seen:
        print(s)
    return len(seen)


def clean_md(v):
    v = re.sub(r"\s*\\+\s*$", "", v, flags=re.M)
    v = v.replace("&nbsp;", " ").replace("***", "").replace("```python", "").replace("```", "")
    return "\n".join(line.rstrip() for line in v.split("\n") if line.strip())


def positional_query(method, path, line_1, sym, extra=None, timeout=120):
    ensure_daemon()
    path = os.path.abspath(path)
    char = resolve_pos(path, line_1, sym)
    params = {"textDocument": {"uri": "file://" + path},
              "position": {"line": line_1 - 1, "character": char}}
    params.update(extra or {})
    resp = cli_send({"op": "query", "method": method, "params": params,
                     "path": path, "timeout": timeout})
    if not resp.get("result"):  # index may still be settling right after didOpen
        time.sleep(1.5)
        resp = cli_send({"op": "query", "method": method, "params": params,
                         "path": path, "timeout": timeout})
    return resp.get("result")


def main():
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__.strip())
    cmd, rest = args[0], args[1:]

    if cmd == "daemon":
        run_daemon()
    elif cmd == "bump":
        if len(rest) != 1:
            sys.exit("usage: lsp.py bump <version>   (a release tag from github.com/odoo/odoo-ls/releases)")
        bump(rest[0])
    elif cmd == "status":
        try:
            info = cli_send({"op": "ping"}, timeout=5)
            print(json.dumps(info, indent=2))
        except SystemExit:
            raise
        except Exception:
            print("daemon not running")
    elif cmd == "stop":
        try:
            cli_send({"op": "stop"}, timeout=5)
            print("stopped")
        except Exception:
            print("daemon not running")
    elif cmd == "restart":
        try:
            cli_send({"op": "stop"}, timeout=5)
        except Exception:
            pass
        for _ in range(20):
            if not os.path.exists(SOCK):
                break
            time.sleep(0.2)
        info = ensure_daemon()
        print(json.dumps(info, indent=2))
    elif cmd == "open":
        ensure_daemon()
        cli_send({"op": "open", "path": os.path.abspath(rest[0])})
        print("opened " + rest[0])
    elif cmd in ("def", "refs", "hover"):
        if len(rest) != 3:
            sys.exit(f"usage: lsp.py {cmd} <file> <line> <symbol|col>")
        path, line_1, sym = rest[0], int(rest[1]), rest[2]
        if cmd == "def":
            n = print_locs(positional_query("textDocument/definition", path, line_1, sym),
                           "no definition found")
            sys.exit(0 if n else 1)
        elif cmd == "refs":
            result = positional_query("textDocument/references", path, line_1, sym,
                                      extra={"context": {"includeDeclaration": True}}, timeout=180)
            n = print_locs(result, "no references found")
            print("(refs miss dynamic call sites; treat as lower bound, cross-check with grep)",
                  file=sys.stderr)
            sys.exit(0 if n else 1)
        else:
            result = positional_query("textDocument/hover", path, line_1, sym)
            if not result:
                sys.exit("no hover info")
            contents = result.get("contents")
            v = contents.get("value") if isinstance(contents, dict) else str(contents)
            print(clean_md(v))
    elif cmd == "who":
        # The one-command replacement for `grep -r <identifier> src/`: find the
        # definition(s) by exact name, then run references at each. Exists
        # because refs alone needs a file+line first — a two-step flow loses to
        # the one-step grep reflex every time.
        if len(rest) != 1:
            sys.exit("usage: lsp.py who <identifier>   (definition(s) + every reference, one command)")
        ident = rest[0].rstrip("(")
        if "." in ident:
            sys.exit(f"'{ident}' looks like a model name — use: lsp.py model {ident}")
        ensure_daemon()
        # Definition sites, two sources merged: workspace/symbol (knows methods
        # and classes, but is fuzzy, capped and blind to FIELDS in odoo-ls
        # 1.5.1) + a definition-shaped grep (`def x(` / `x = fields.`), which
        # catches what the index does not. References themselves stay semantic.
        resp = cli_send({"op": "query", "method": "workspace/symbol",
                         "params": {"query": ident}, "timeout": 90})
        defs, seen = [], set()
        for h in resp.get("result") or []:
            if h.get("name", "").strip("\"'") != ident:
                continue
            loc = fmt_loc(h.get("location", {}))
            if loc not in seen:
                seen.add(loc)
                defs.append((loc, SYMBOL_KINDS.get(h.get("kind"), str(h.get("kind")))))
        rg = subprocess.run(
            ["grep", "-rn", "--include=*.py", "-E",
             rf"(def {re.escape(ident)}\(|{re.escape(ident)} = fields\.)",
             os.path.join(ROOT, "src")],
            capture_output=True, text=True)
        for hit in rg.stdout.splitlines():
            path, line, text = hit.split(":", 2)
            loc = f"{os.path.relpath(path, ROOT)}:{line}"
            if loc not in seen:
                seen.add(loc)
                defs.append((loc, "field" if "fields." in text else "method"))
        if not defs:
            sys.exit(f"no definition of '{ident}' found — check the spelling, or search: lsp.py sym {ident}")
        cap = 8  # a name defined in more places than this needs narrowing, not scrolling
        total_refs = 0
        printed = set()  # overrides of one method share their reference set; print each site once
        for loc, kind in defs[:cap]:
            print(f"def: {loc}  [{kind}]")
            path, line = loc.rsplit(":", 1)
            try:
                result = positional_query("textDocument/references",
                                          os.path.join(ROOT, path), int(line), ident,
                                          extra={"context": {"includeDeclaration": False}},
                                          timeout=180)
            except SystemExit as e:
                print(f"  (references failed here: {e})")
                continue
            locs = result if isinstance(result, list) else ([result] if result else [])
            refs = []
            for one in locs:
                where = fmt_loc(one)
                if where != loc and where not in printed and where not in refs:
                    refs.append(where)
            printed.update(refs)
            for where in refs:
                print(f"  {where}")
            if not refs:
                print("  (no new references)")
            total_refs += len(refs)
        if len(defs) > cap:
            print(f"... +{len(defs) - cap} more definitions (narrow with: lsp.py sym {ident})")
        print("(refs are a lower bound — dynamic call sites, lambdas in filtered()/mapped() and "
              "attrs=/domain strings are invisible; cross-check with grep before claiming 'unused'. "
              "Never pipe this into head/tail.)", file=sys.stderr)
        sys.exit(0 if total_refs else 1)
    elif cmd in ("sym", "model"):
        if not rest:
            sys.exit(f"usage: lsp.py {cmd} <query>")
        query = rest[0]
        ensure_daemon()
        resp = cli_send({"op": "query", "method": "workspace/symbol",
                         "params": {"query": query}, "timeout": 90})
        result = resp.get("result") or []
        rows = []
        for h in result:
            name = h.get("name", "")
            if cmd == "model" and name.strip("\"'") != query:
                continue
            loc = h.get("location", {})
            row = (name, SYMBOL_KINDS.get(h.get("kind"), str(h.get("kind"))), fmt_loc(loc))
            if row not in rows:
                rows.append(row)
        if not rows:
            print("no symbols found")
            sys.exit(1)
        cap = 60
        for name, kind, where in rows[:cap]:
            print(f"{name}  [{kind}]  {where}")
        if len(rows) > cap:
            print(f"... +{len(rows) - cap} more (narrow the query)")
    else:
        sys.exit(f"unknown command '{cmd}'\n\n" + __doc__.strip())


if __name__ == "__main__":
    main()
