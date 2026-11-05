#!/usr/bin/env python3
"""Run this project's Claude Code hooks and pipeline gates under Antigravity (.agents/hooks.json).

The guards and nudges stay one copy, in .claude/hooks, wired in .claude/settings.json.
This script reads that wiring, so a hook added there for Bash, Edit, Write, Grep or Read
applies here too. It also syncs .agents/plugins/* UI sidecar panels into
~/.gemini/config/plugins/ and enforces active pipeline-gates runs.

Usage:
  claude_hook.py pre | post | session-start
  claude_hook.py pipeline-gate [status | start [TASK ...] | end | phase <0..7> [TASK] | qc-verdict <pass|blocking> <summary> | open <status|grill|qc|rounds> <reason> | tasks ...]
"""

import ast
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SETTINGS = ROOT / ".claude" / "settings.json"
ANTIGRAVITY_DIR = Path(os.environ.get("ANTIGRAVITY_APP_DATA_DIR") or (Path.home() / ".gemini" / "antigravity"))
RUNS_FILE = ANTIGRAVITY_DIR / "plugin_data" / "pipeline-gates" / "runs.json"

# Claude hooks that only make sense in a Claude Code session: entire records Claude
# Code sessions, and its fan-out replays Claude payloads.
CLAUDE_ONLY = ("entire-fanout.sh",)

# Antigravity tool -> (Claude tool, tool_input) the hooks expect.
EDIT_TOOLS = {
    "write_file": "Write",
    "edit_file": "Edit",
    "write_to_file": "Write",
    "replace_file_content": "Edit",
    "multi_replace_file_content": "Edit",
}
READ_TOOLS = {"read_file", "view_file"}
SEARCH_TOOLS = {"grep_search", "list_dir", "find_by_name"}

SENSITIVE_PATH_RE = re.compile(
    r"(?:^|/)(?:\.ssh|\.gnupg|\.aws|\.kube|\.netrc|\.env(?:\.[A-Za-z0-9_.-]+)?)(?:/|$)"
    r"|^/etc/(?:shadow|sudoers|gshadow)"
)
PROTECTED_CONFIG_RE = re.compile(
    r"(?:^|/)\.gemini/config/(?:config\.json|projects/)"
    r"|(?:^|/)(?:\.bashrc|\.zshrc|\.profile|\.bash_profile|\.gitconfig)$"
)
SAFE_USER_UNIT_RE = re.compile(r"^(?:postgres[\w.-]*|odoo[\w@-]*|neo4j-kb[\w.-]*)(?:\.service)?$")

# Explicit allowlists for shell commands (default-ask: anything not matched here prompts the user).
PURE_NOFILE_CMDS = {
    "echo", "printf", "true", "false", "test", "[", "expr", "seq", "sleep",
    "pwd", "date", "id", "whoami", "hostname", "uname", "arch",
    "ps", "pgrep", "lsof", "ss", "df", "du", "free", "uptime", "pg_isready",
    "direnv", "which", "whereis", "type", "basename", "dirname", "tr",
}
READONLY_FILE_CMDS = {
    "ls", "cat", "head", "tail", "less", "more", "grep", "egrep", "fgrep", "rg",
    "diff", "comm", "cmp", "stat", "file", "realpath", "readlink",
    "sha256sum", "sha1sum", "md5sum", "base64", "xxd", "od", "strings",
    "jq", "yq", "plantuml", "dot", "sort", "uniq", "wc", "cut",
    "nl", "fold", "fmt", "paste", "join", "column",
}
FS_MUTATE_CMDS = {
    "rm", "rmdir", "unlink", "mv", "cp", "mkdir", "touch", "ln", "chmod", "patch", "tee", "mktemp",
}
GIT_ALLOWED_SUBCMDS = {
    "status", "diff", "log", "show", "blame", "rev-parse", "rev-list", "for-each-ref",
    "ls-files", "ls-tree", "ls-remote", "cat-file", "describe", "shortlog", "reflog",
    "grep", "name-rev", "merge-base", "symbolic-ref", "var", "version", "help",
    "diff-tree", "diff-index", "diff-files", "check-ignore", "check-attr",
    "verify-commit", "verify-tag",
    "add", "commit", "fetch", "pull", "merge", "rebase", "cherry-pick", "revert",
    "stash", "tag", "notes", "switch", "restore", "rm", "mv", "apply", "am", "format-patch",
}
GIT_WORKTREE_SUBCMDS = {"list", "add", "remove", "prune", "lock", "unlock"}
GIT_REMOTE_READ_SUBCMDS = {"show", "get-url", "-v", "--verbose"}
GH_PR_SUBCMDS = {"view", "list", "status", "checks", "diff", "create", "comment", "edit", "ready", "review"}
GH_ISSUE_SUBCMDS = {"view", "list", "status"}
GH_RUN_SUBCMDS = {"view", "list", "watch"}
GH_SEARCH_SUBCMDS = {"prs", "issues", "commits", "code", "repos"}
SYSTEMCTL_READ_VERBS = {
    "status", "is-active", "is-enabled", "is-failed", "show", "cat",
    "list-units", "list-unit-files", "list-timers", "list-sockets", "list-dependencies", "daemon-reload",
}
SYSTEMCTL_SERVICE_VERBS = {"start", "stop", "restart", "reload", "reload-or-restart", "reset-failed"}
NIX_ALLOWED_SUBCMDS = {
    "run", "flake", "build", "eval", "develop", "fmt", "search",
    "path-info", "log", "show-derivation", "--version",
}
UV_ALLOWED_SUBCMDS = {"lock", "sync", "tree", "version", "--version", "venv"}
UV_PIP_READ_SUBCMDS = {"list", "show", "tree", "check", "freeze"}
COPIER_ALLOWED_SUBCMDS = {"copy", "recopy", "update", "check", "--version", "--help"}
PYTHON_SAFE_M_MODULES = {
    "unittest", "pytest", "py_compile", "compileall", "json.tool",
    "ast", "dis", "timeit", "doctest", "cProfile", "ruff",
}
SAFE_PYTHON_MODULES = {
    "ast", "base64", "bisect", "collections", "copy", "csv", "datetime",
    "decimal", "difflib", "enum", "fnmatch", "functools", "glob", "gzip",
    "hashlib", "heapq", "hmac", "html", "inspect", "io", "itertools",
    "json", "keyword", "locale", "logging", "math", "operator", "os",
    "pathlib", "platform", "pprint", "random", "re", "shlex", "statistics",
    "string", "struct", "sys", "textwrap", "time", "timeit", "tokenize",
    "tomllib", "traceback", "types", "typing", "unicodedata", "unittest",
    "urllib", "uuid", "warnings", "xml", "zipfile", "zlib",
}
SAFE_OS_ATTRS = {
    "path", "environ", "getcwd", "listdir", "scandir", "walk", "stat",
    "lstat", "sep", "linesep", "pathsep", "extsep", "devnull", "curdir",
    "pardir", "name", "cpu_count", "getpid", "getuid", "getgid", "readlink",
    "fspath", "fsencode", "fsdecode", "getenv",
}
FORBIDDEN_PYTHON_ATTRS = {
    "__subclasses__", "__globals__", "__builtins__", "__code__", "__closure__",
    "__import__", "__loader__", "__spec__",
    "unlink", "remove", "rmtree", "rmdir", "removedirs", "rename", "replace",
    "makedirs", "mkdir", "chmod", "chown", "lchmod", "symlink_to", "hardlink_to",
    "write_text", "write_bytes", "touch", "popen", "system", "spawn", "exec",
    "fork", "kill", "killpg", "urlopen", "urlretrieve", "home", "expanduser",
}
def deploy_allow_res(path=Path(__file__).with_name("deploy-allow.json")):
    """The deploy skill's commands that run without a prompt. The project's repos, its ssh host
    aliases and its Odoo unit come from deploy-allow.json, rendered from the copier answers; a
    missing or broken file leaves only `gh pr merge` without -R, and every deploy script prompts."""
    try:
        allow = json.loads(path.read_text())
    except (OSError, ValueError):
        allow = {}
    def alt(names):
        return "|".join(re.escape(name) for name in names) or r"(?!)"  # (?!) matches nothing

    repos, hosts = alt(allow.get("merge_repos", [])), alt(allow.get("hosts", []))
    service = re.escape(allow.get("prod_service") or "odoo.service")
    script = r'^bash\s+"?\S*\.claude/skills/deploy/scripts/'
    return (
        re.compile(
            r"^gh\s+pr\s+merge\s+\d+\s+"
            rf"(?:(?:-R|--repo)\s+(?:{repos})\s+)?"
            r"(?:--merge|--squash(?:\s+--delete-branch)?)\s+--match-head-commit\s+[0-9a-f]{40}$"
        ),
        re.compile(rf'{script}prod-requeue-jobs\.sh"?\s+(?:{hosts}|--apply)$'),
        re.compile(rf'{script}prod-refresh-inventory-analysis\.sh"?\s+(?:{hosts}|--apply)$'),
        re.compile(
            rf'{script}prod-deploy-modules\.sh"?\s+'
            rf"(?:{hosts})\s+(?:-u|-i)\s+[a-z0-9_,]+(?:\s+--link-addons)?$"
        ),
        re.compile(
            rf'{script}prod-deploy-lock\.sh"?\s+'
            rf"(?:{hosts})\s+(?:acquire|status|release|mark-failed)"
            r"(?:\s+\d+)?(?:\s+--wait\s+\d+)?(?:\s+--force)?$"
        ),
        re.compile(
            rf'^ssh\s+-A\s+-F\s+"?\S*\.ssh/config"?\s+prod\s+\x27systemctl\s+--user\s+(?:stop|start)\s+{service}\x27$'
        ),
    )


WHITELISTED_DEPLOY_RES = deploy_allow_res()

PHASES = (
    "Memory bootstrap",
    "Task reading",
    "Development",
    "QC gate",
    "Documentation",
    "Demo GIF (optional)",
    "Pull request",
    "Memory closure",
)
GATE_LABELS = {"status": "Teams status", "grill": "grill alignment", "qc": "QC verdict"}
WORKTREE_RE = re.compile(r"/\.worktrees/(?!_env/)[A-Za-z0-9][A-Za-z0-9._-]*/")
DOC_PATH_RE = re.compile(r"/readme/|/README\.(?:rst|md)$|/static/description/|\.puml$")
PR_CREATE_RE = re.compile(r"\bgh\s+(?:(?:-R|--repo)\s+\S+\s+)?pr\s+(?:create|new)\b")
RESUME_REFUSAL = (
    "pipeline-gates: a fix round goes to a fresh dev agent, not into one that already worked: "
    "spawn dev with a brief (the verified findings with their decisive lines, and "
    "`git diff --cached --stat <REVIEWED_TREE>` for what earlier rounds changed)."
)


def project_dir_var():
    """The env var the hook commands use for the project root (project_dir_var in the answers)."""
    try:
        answers = (ROOT / ".copier-answers.yml").read_text()
        found = re.search(r"^project_dir_var:\s*(\w+)", answers, re.M)
        if found:
            return found.group(1)
    except OSError:
        pass
    return "ODOO16_PROJECT_DIR"


def absolute(path):
    if not path:
        return ""
    return str(path) if os.path.isabs(path) else str(ROOT / path)


def decode_args(args):
    """Unwrap arg values Antigravity encoded twice; plain values pass unchanged."""
    decoded = {}
    for key, value in args.items():
        if isinstance(value, str):
            try:
                inner = json.loads(value)
            except ValueError:
                inner = None
            if isinstance(inner, (str, dict, list)):
                value = inner
        decoded[key] = value
    return decoded


def target_path_of(args):
    return absolute(args.get("Path") or args.get("TargetFile") or args.get("AbsolutePath") or "")


def claude_call(name, args):
    if name == "run_command":
        return "Bash", {"command": args.get("CommandLine") or ""}
    if name in EDIT_TOOLS:
        return EDIT_TOOLS[name], {"file_path": target_path_of(args)}
    if name == "grep_search":
        return "Grep", {"pattern": args.get("Query") or "", "path": absolute(args.get("SearchPath"))}
    if name in READ_TOOLS:
        return "Read", {"file_path": target_path_of(args)}
    return None, {}


def commands(event, tool):
    """The hook commands .claude/settings.json runs for this event and Claude tool."""
    groups = json.loads(SETTINGS.read_text()).get("hooks", {}).get(event, [])
    for group in groups:
        matcher = group.get("matcher") or ""
        if matcher and not re.fullmatch(matcher, tool):
            continue
        for hook in group.get("hooks", []):
            command = hook.get("command") or ""
            if hook.get("type", "command") == "command" and not any(name in command for name in CLAUDE_ONLY):
                yield command, hook.get("timeout", 30)


def run(command, payload, timeout):
    env = dict(os.environ)
    env.setdefault(project_dir_var(), str(ROOT))
    env["AGENT_HOST"] = "antigravity"
    return subprocess.run(
        ["bash", "-c", command],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=ROOT,
        env=env,
        timeout=timeout,
        check=False,
    )


def read_denies():
    """Path prefixes .claude/settings.json denies to Read, e.g. Read(./.entire/metadata/**)."""
    rules = json.loads(SETTINGS.read_text()).get("permissions", {}).get("deny", [])
    for rule in rules:
        found = re.fullmatch(r"Read\(\./(.+?)/?\*{0,2}\)", rule)
        if found:
            yield found.group(1).rstrip("/")


def denied_path(args):
    """Claude enforces permissions.deny itself; here every tool that can read is checked."""
    paths = [
        args.get(key) or ""
        for key in ("Path", "AbsolutePath", "SearchPath", "SearchDirectory", "DirectoryPath", "TargetFile")
    ]
    command = args.get("CommandLine") or ""
    for rel in read_denies():
        if any(absolute(path).startswith(str(ROOT / rel)) for path in paths if path) or rel in command:
            return f"Blocked: {rel}/ is read-denied in this project (.claude/settings.json permissions.deny)."
    return None


def common(data):
    return data.get("common") or data


def tool_call(data):
    for key in ("preToolHookArgs", "postToolHookArgs"):
        if key in data:
            return data[key].get("toolCall") or {}
    return data.get("toolCall") or {}


def conversation_id_of(data):
    base = common(data)
    return base.get("conversationId") or os.environ.get("ANTIGRAVITY_CONVERSATION_ID") or ""


def claude_payload(data, event, tool=None, tool_input=None):
    base = common(data)
    workspaces = base.get("workspacePaths") or [str(ROOT)]
    payload = {
        "session_id": conversation_id_of(data),
        "transcript_path": base.get("transcriptPath") or "",
        "cwd": workspaces[0],
        "hook_event_name": event,
    }
    if tool:
        payload.update(tool_name=tool, tool_input=tool_input)
    return payload


# === pipeline-gates state & enforcement ===


def load_runs():
    try:
        if RUNS_FILE.exists():
            return json.loads(RUNS_FILE.read_text())
    except Exception:  # noqa: BLE001
        pass
    return {}


def save_runs(runs):
    try:
        RUNS_FILE.parent.mkdir(parents=True, exist_ok=True)
        RUNS_FILE.write_text(json.dumps(runs, indent=2))
    except Exception:  # noqa: BLE001
        pass


def is_gate_open(gate):
    return isinstance(gate, dict) and gate.get("isOpen") is True


def closed_for_dev(p_run):
    return [g for g in ("status", "grill") if not is_gate_open(p_run.get(g))]


def qc_holds(p_run):
    return is_gate_open(p_run.get("qc")) and (p_run.get("qcSeq") or 0) > (p_run.get("lastChange") or 0)


def qc_missing(p_run):
    last_change = p_run.get("lastChange") or 0
    missing = []
    if (p_run.get("lastReview") or 0) <= last_change:
        missing.append("a review agent that finished after the last code change")
    if (p_run.get("lastGreen") or 0) <= last_change:
        missing.append("a green Odoo test run (more than 0 tests) after the last code change")
    return missing


def record_block(runs, conv_id, p_run, what):
    now_ms = int(time.time() * 1000)
    blocked = [{"what": what, "at": now_ms}, *(p_run.get("blocked") or [])][:5]
    p_run["blocked"] = blocked
    runs[conv_id] = p_run
    save_runs(runs)


def check_pipeline_pre(conv_id, name, args):
    if not conv_id:
        return None
    runs = load_runs()
    p_run = runs.get(conv_id)
    if not isinstance(p_run, dict) or p_run.get("isDone"):
        return None

    is_dev_agent = name == "invoke_subagent" and args.get("TypeName") == "dev"
    path = target_path_of(args) if name in EDIT_TOOLS else ""
    is_wt_edit = bool(path and WORKTREE_RE.search(path))

    closed = closed_for_dev(p_run)
    if (is_dev_agent or is_wt_edit) and closed:
        what = "spawning the dev agent" if is_dev_agent else f"editing {path}"
        record_block(runs, conv_id, p_run, what)
        labels = " and ".join(GATE_LABELS[g] for g in closed)
        how = []
        if "status" in closed:
            how.append("the status gate opens on a successful Teams send (my-status send)")
        if "grill" in closed:
            how.append("the grill gate opens when the user clicks confirm or mechanical in the Pipeline panel")
        return (
            f"pipeline-gates: {what} is refused while {labels} closed ({'; '.join(how)}). "
            "Open the Pipeline panel in Overview -> Plugins to confirm."
        )

    phase = p_run.get("phase") or 0
    is_new_round = 2 <= phase <= 3 and (p_run.get("lastReview") or 0) > (p_run.get("roundSeq") or 0)
    if is_new_round and not closed:
        if name == "send_message":
            dev_ids = set(p_run.get("devAgentIds") or [])
            recipient = str(args.get("Recipient") or "").strip()
            if recipient in dev_ids or not dev_ids:
                record_block(runs, conv_id, p_run, "a fix round sent into an old dev agent")
                return RESUME_REFUSAL
        if is_dev_agent:
            max_rounds = 3 + (p_run.get("extraRounds") or 0)
            fix_rounds = p_run.get("fixRounds") or 0
            if fix_rounds >= max_rounds:
                record_block(runs, conv_id, p_run, f"fix round {fix_rounds + 1}")
                return (
                    f"pipeline-gates: fix round {fix_rounds + 1} is refused, the QC gate still blocks "
                    f"after {fix_rounds} rounds. Stop and report to the user; only the user grants +3 rounds "
                    "in the Pipeline panel."
                )
            seq = (p_run.get("seq") or 0) + 1
            p_run["seq"] = seq
            p_run["fixRounds"] = fix_rounds + 1
            p_run["roundSeq"] = seq
            runs[conv_id] = p_run
            save_runs(runs)

    if name == "run_command":
        cmd = args.get("CommandLine") or ""
        if PR_CREATE_RE.search(cmd) and not qc_holds(p_run):
            record_block(runs, conv_id, p_run, "gh pr create")
            missing = [*qc_missing(p_run), "a pass recorded in the Pipeline panel (or claude_hook.py pipeline-gate qc-verdict pass)"]
            return f"pipeline-gates: gh pr create is refused until the QC gate passes; missing: {', '.join(missing)}."

    return None


def update_pipeline_post(conv_id, name, args):
    if not conv_id:
        return
    runs = load_runs()
    p_run = runs.get(conv_id)
    if not isinstance(p_run, dict) or p_run.get("isDone"):
        return

    now_ms = int(time.time() * 1000)
    changed = False

    if name in EDIT_TOOLS:
        path = target_path_of(args)
        if path and WORKTREE_RE.search(path) and not DOC_PATH_RE.search(path):
            seq = (p_run.get("seq") or 0) + 1
            p_run["seq"] = seq
            p_run["lastChange"] = seq
            changed = True
    elif name == "invoke_subagent":
        sub_type = args.get("TypeName") or ""
        if sub_type in ("dev", "review"):
            seq = (p_run.get("seq") or 0) + 1
            p_run["seq"] = seq
            if sub_type == "dev":
                p_run["lastChange"] = seq
            else:
                p_run["lastReview"] = seq
            changed = True
    elif name == "call_mcp_tool":
        tool_name = args.get("ToolName") or ""
        if tool_name in ("send_chat_message", "send_channel_message") and not is_gate_open(p_run.get("status")):
            p_run["status"] = {"isOpen": True, "how": f"sent via teams-mcp ({tool_name})", "at": now_ms}
            changed = True

    if changed:
        runs[conv_id] = p_run
        save_runs(runs)


def _safe_read_roots():
    return (
        ROOT,
        ROOT.parent / "nixodoo-copier-template",
        Path("/tmp"),
        ANTIGRAVITY_DIR,
        Path.home() / ".gemini" / "config" / "plugins",
        Path.home() / ".claude",
        Path("/nix/store"),
        Path("/opt/Antigravity/resources"),
    )


def _safe_write_roots():
    return (
        ROOT,
        ROOT.parent / "nixodoo-copier-template",
        Path("/tmp"),
        ANTIGRAVITY_DIR / "brain",
        Path.home() / ".claude" / "projects",
    )


def _norm_path(path_str, cwd=ROOT):
    if not path_str:
        return ""
    p = str(path_str).strip()
    home = str(Path.home())
    if p == "~" or p.startswith("~/"):
        p = home + p[1:]
    p = p.replace("$HOME", home).replace("${HOME}", home)
    for var in (project_dir_var(), "PROJECT_DIR", "ODOO16_PROJECT_DIR"):
        p = p.replace(f"${var}", str(ROOT)).replace(f"${{{var}}}", str(ROOT))
    if not os.path.isabs(p):
        p = os.path.join(str(cwd), p)
    return os.path.abspath(p)


def _is_under(abs_path, roots):
    if not abs_path:
        return False
    home = os.path.abspath(str(Path.home()))
    in_test_home = home.startswith("/tmp/") and (abs_path == home or abs_path.startswith(home + "/"))
    for r in roots:
        r_abs = os.path.abspath(str(r))
        if in_test_home and (r_abs == "/tmp" or home.startswith(r_abs + "/")):
            continue
        if abs_path == r_abs or abs_path.startswith(r_abs + "/"):
            return True
    return False


def _is_protected_top_dir(abs_path):
    protected = {
        os.path.abspath(str(ROOT)),
        "/tmp",
        os.path.abspath(str(ROOT / ".git")),
        os.path.abspath(str(ROOT / "src")),
        os.path.abspath(str(ROOT / "backup")),
        os.path.abspath(str(ROOT / ".claude")),
        os.path.abspath(str(ROOT / ".agents")),
    }
    src_dir = ROOT / "src"
    if src_dir.is_dir():
        try:
            for child in src_dir.iterdir():
                protected.add(os.path.abspath(str(child)))
        except OSError:
            pass
    return abs_path in protected


def _split_shell_statements(cmd):
    """Split shell command into statements on ;, &&, ||, |, newline (quote/heredoc-aware), and extract $(...)."""
    statements = []
    subshells = []
    buf = []
    in_squote = False
    in_dquote = False
    escape = False
    paren_depth = 0
    sub_start = None
    heredoc_eof = None
    i = 0
    n = len(cmd)

    while i < n:
        ch = cmd[i]
        if heredoc_eof is not None:
            line_end = cmd.find("\n", i)
            if line_end == -1:
                line = cmd[i:]
                i = n
            else:
                line = cmd[i:line_end]
                i = line_end + 1
            if line.strip() == heredoc_eof:
                heredoc_eof = None
            continue

        if escape:
            buf.append(ch)
            escape = False
            i += 1
            continue

        if ch == "\\" and not in_squote:
            escape = True
            buf.append(ch)
            i += 1
            continue

        if ch == "'" and not in_dquote:
            in_squote = not in_squote
            buf.append(ch)
            i += 1
            continue

        if ch == '"' and not in_squote:
            in_dquote = not in_dquote
            buf.append(ch)
            i += 1
            continue

        if not in_squote:
            if cmd.startswith("$(", i):
                if paren_depth == 0:
                    sub_start = i + 2
                paren_depth += 1
                buf.append("__SUBSHELL__")
                i += 2
                continue
            if ch == "(" and paren_depth > 0:
                paren_depth += 1
                i += 1
                continue
            if ch == ")" and paren_depth > 0:
                paren_depth -= 1
                if paren_depth == 0 and sub_start is not None:
                    subshells.append(cmd[sub_start:i])
                    sub_start = None
                i += 1
                continue

        if not in_squote and not in_dquote and paren_depth == 0:
            if cmd.startswith("<<", i) and not cmd.startswith("<<<", i):
                m = re.match(r"<<-?\s*(?:'([^']+)'|\"([^\"]+)\"|([A-Za-z0-9_]+))", cmd[i:])
                if m:
                    heredoc_eof = m.group(1) or m.group(2) or m.group(3)
                    line_end = cmd.find("\n", i)
                    if line_end == -1:
                        buf.append(cmd[i:])
                        break
                    buf.append(cmd[i:line_end])
                    statements.append("".join(buf).strip())
                    buf = []
                    i = line_end + 1
                    continue

            if cmd.startswith("&&", i) or cmd.startswith("||", i):
                statements.append("".join(buf).strip())
                buf = []
                i += 2
                continue
            if ch in (";", "|", "\n"):
                statements.append("".join(buf).strip())
                buf = []
                i += 1
                continue

        buf.append(ch)
        i += 1

    if in_squote or in_dquote or paren_depth > 0:
        return None, None
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return [s for s in statements if s and not s.startswith("#")], subshells


def _extract_redirections_and_clean_tokens(tokens):
    """Separate output and input redirection targets from command tokens."""
    clean = []
    write_redirs = []
    read_redirs = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in ("2>&1", "1>&2", ">&2", ">&1", "</dev/null", ">/dev/null", "2>/dev/null", "&>/dev/null"):
            i += 1
            continue
        if tok.startswith("<<<"):
            i += 2 if tok == "<<<" else 1
            continue
        if tok.startswith("<<"):
            i += 2 if tok in ("<<", "<<-") else 1
            continue
        m_out = re.match(r"^(?:\d*|&)?>>?(.*)$", tok)
        if m_out:
            rest = m_out.group(1)
            if rest in ("&1", "&2", "/dev/null"):
                i += 1
                continue
            if rest:
                write_redirs.append(rest)
                i += 1
            elif i + 1 < len(tokens):
                target = tokens[i + 1]
                if target not in ("&1", "&2", "/dev/null"):
                    write_redirs.append(target)
                i += 2
            else:
                i += 1
            continue
        m_in = re.match(r"^(?:\d*)<(.*)$", tok)
        if m_in:
            rest = m_in.group(1)
            if rest in ("&0", "/dev/null"):
                i += 1
                continue
            if rest:
                read_redirs.append(rest)
                i += 1
            elif i + 1 < len(tokens):
                target = tokens[i + 1]
                if target not in ("&0", "/dev/null"):
                    read_redirs.append(target)
                i += 2
            else:
                i += 1
            continue
        clean.append(tok)
        i += 1
    return clean, write_redirs, read_redirs


def _is_safe_read_path(path_str, cwd=ROOT):
    if not path_str or path_str in ("-", "/dev/null", "/dev/stdin", "/dev/stdout", "/dev/stderr", "__SUBSHELL__"):
        return True, ""
    norm = _norm_path(path_str, cwd)
    if SENSITIVE_PATH_RE.search(norm) or PROTECTED_CONFIG_RE.search(norm):
        return False, f"Reading sensitive or protected path '{path_str}' requires confirmation."
    if _is_under(norm, _safe_read_roots()):
        return True, ""
    return False, f"Reading '{path_str}' outside the project safe perimeter requires confirmation."


def _is_safe_write_path(path_str, cwd=ROOT, allow_top_dir=True):
    if not path_str or path_str.startswith("-"):
        return False, f"Write target '{path_str}' requires confirmation."
    if path_str in ("/dev/null", "/dev/stdout", "/dev/stderr"):
        return True, ""
    norm = _norm_path(path_str, cwd)
    if SENSITIVE_PATH_RE.search(norm) or PROTECTED_CONFIG_RE.search(norm):
        return False, f"Touching protected path '{path_str}' requires confirmation."
    git_dir = os.path.abspath(str(ROOT / ".git"))
    if norm == git_dir or norm.startswith(git_dir + "/"):
        return False, f"Modifying '{path_str}' inside .git/ requires confirmation."
    if not _is_under(norm, _safe_write_roots()):
        return False, f"Modifying '{path_str}' outside the project safe perimeter requires confirmation."
    if not allow_top_dir and _is_protected_top_dir(norm):
        return False, f"Deleting or moving top-level directory '{path_str}' requires confirmation."
    return True, ""


def _check_read_args(args, cwd, skip_first_non_flag=False, flags_with_arg=(), file_flags=()):
    """Verify that all positional file arguments and file-flag values are inside _safe_read_roots()."""
    skip_next_file = False
    skip_next_val = False
    after_dd = False
    seen_first = False
    for a in args:
        if skip_next_file:
            skip_next_file = False
            ok, reason = _is_safe_read_path(a, cwd)
            if not ok:
                return False, reason
            continue
        if skip_next_val:
            skip_next_val = False
            if a.startswith(("/", "~", "../")):
                ok, reason = _is_safe_read_path(a, cwd)
                if not ok:
                    return False, reason
            continue
        if a == "--" and not after_dd:
            after_dd = True
            continue
        if not after_dd and a.startswith("-") and a != "-":
            if a in file_flags:
                skip_next_file = True
                continue
            if a in flags_with_arg:
                skip_next_val = True
                continue
            if "=" in a:
                k, v = a.split("=", 1)
                if k in file_flags or v.startswith(("/", "~", "../")):
                    ok, reason = _is_safe_read_path(v, cwd)
                    if not ok:
                        return False, reason
            continue
        if skip_first_non_flag and not seen_first:
            seen_first = True
            continue
        seen_first = True
        ok, reason = _is_safe_read_path(a, cwd)
        if not ok:
            return False, reason
    return True, ""


def _is_allowed_python_inline(code, cwd=ROOT):
    """AST-based allowlist check for `python3 -c <code>`: only read-only stdlib calls inside safe roots."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return False, "Inline Python with syntax error requires confirmation."

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top not in SAFE_PYTHON_MODULES or alias.name == "urllib.request":
                    return False, f"Inline Python importing '{alias.name}' requires confirmation."
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            top = mod.split(".")[0]
            if top not in SAFE_PYTHON_MODULES or mod == "urllib.request":
                return False, f"Inline Python importing from '{mod}' requires confirmation."
            if mod == "os":
                for alias in node.names:
                    if alias.name not in SAFE_OS_ATTRS:
                        return False, f"Inline Python importing 'os.{alias.name}' requires confirmation."
        elif isinstance(node, ast.Attribute):
            if node.attr in FORBIDDEN_PYTHON_ATTRS:
                return False, f"Inline Python using '.{node.attr}' requires confirmation."
            if isinstance(node.value, ast.Name) and node.value.id == "os" and node.attr not in SAFE_OS_ATTRS:
                return False, f"Inline Python using 'os.{node.attr}' requires confirmation."
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id in ("eval", "exec", "compile", "__import__", "breakpoint", "input"):
                    return False, f"Inline Python calling '{node.func.id}()' requires confirmation."
                if node.func.id == "open":
                    if len(node.args) >= 2:
                        mode_arg = node.args[1]
                        if not (isinstance(mode_arg, ast.Constant) and isinstance(mode_arg.value, str)):
                            return False, "Inline Python open() with dynamic mode requires confirmation."
                        if any(c in mode_arg.value for c in ("w", "a", "x", "+")):
                            return False, f"Inline Python open() with write mode '{mode_arg.value}' requires confirmation."
                    for kw in node.keywords:
                        if kw.arg == "mode":
                            if not (isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str)):
                                return False, "Inline Python open() with dynamic mode requires confirmation."
                            if any(c in kw.value.value for c in ("w", "a", "x", "+")):
                                return False, f"Inline Python open() with write mode '{kw.value.value}' requires confirmation."
                    if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                        ok, reason = _is_safe_read_path(node.args[0].value, cwd)
                        if not ok:
                            return False, reason
            elif isinstance(node.func, ast.Attribute) and node.func.attr == "open":
                if node.args:
                    mode_arg = node.args[0]
                    if not (isinstance(mode_arg, ast.Constant) and isinstance(mode_arg.value, str)):
                        return False, "Inline Python Path.open() with dynamic mode requires confirmation."
                    if any(c in mode_arg.value for c in ("w", "a", "x", "+")):
                        return False, f"Inline Python Path.open() with write mode '{mode_arg.value}' requires confirmation."
                for kw in node.keywords:
                    if kw.arg == "mode":
                        if not (isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str)):
                            return False, "Inline Python Path.open() with dynamic mode requires confirmation."
                        if any(c in kw.value.value for c in ("w", "a", "x", "+")):
                            return False, f"Inline Python Path.open() with write mode '{kw.value.value}' requires confirmation."
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            val = node.value.strip()
            if (val.startswith("/") and len(val) > 1 and not val.startswith("//")) or val.startswith("~/") or val == "~":
                if " " not in val and "\n" not in val:
                    ok, reason = _is_safe_read_path(val, cwd)
                    if not ok:
                        return False, reason

    return True, ""


def _is_project_script(norm_script):
    if not _is_under(norm_script, (ROOT, ROOT.parent / "nixodoo-copier-template")):
        return False
    deploy_scripts = os.path.abspath(str(ROOT / ".claude" / "skills" / "deploy" / "scripts"))
    if norm_script == deploy_scripts or norm_script.startswith(deploy_scripts + "/"):
        return False
    return norm_script.endswith((".sh", ".py"))


def _is_statement_allowed(cmd_token, rest, cwd):
    """Allowlist-only check for a single simple command statement. Returns (True, '') or (False, reason)."""
    cmd_name = os.path.basename(cmd_token)

    if "/" in cmd_token:
        norm_bin = _norm_path(cmd_token, cwd)
        if _is_project_script(norm_bin):
            return _check_read_args(rest, cwd)
        if not (
            _is_under(norm_bin, _safe_read_roots())
            or norm_bin.startswith(("/usr/", "/bin/", "/run/current-system/", "/nix/store/"))
        ):
            return False, f"Executing '{cmd_token}' outside allowed binary roots requires confirmation."

    if cmd_name in PURE_NOFILE_CMDS:
        for a in rest:
            if a.startswith(("/", "~", "../")) or (not a.startswith("-") and "/" in a):
                ok, reason = _is_safe_read_path(a, cwd)
                if not ok:
                    return False, reason
        return True, ""

    if cmd_name in READONLY_FILE_CMDS:
        if cmd_name == "sort" and any(a == "-o" or a.startswith(("-o", "--output")) for a in rest):
            return False, "sort with -o/--output requires confirmation."
        if cmd_name in ("grep", "egrep", "fgrep", "rg"):
            has_e_or_f = any(
                a in ("-e", "--regexp", "-f", "--file")
                or (a.startswith("-") and not a.startswith("--") and ("e" in a[1:] or "f" in a[1:]))
                or a.startswith(("--regexp=", "--file="))
                for a in rest
            )
            return _check_read_args(
                rest,
                cwd,
                skip_first_non_flag=not has_e_or_f,
                flags_with_arg=(
                    "-e", "--regexp", "-m", "--max-count", "-A", "--after-context",
                    "-B", "--before-context", "-C", "--context", "-g", "--glob",
                    "-t", "--type", "--type-add", "--type-not", "--include",
                    "--exclude", "--exclude-dir", "--color", "--colour",
                    "-d", "--directories", "--devices", "--binary-files",
                    "--label", "-j", "--threads", "--max-depth", "--max-filesize",
                    "--encoding", "-E", "--replace",
                ),
                file_flags=("-f", "--file"),
            )
        if cmd_name in ("jq", "yq"):
            has_f = any(a in ("-f", "--from-file") for a in rest)
            return _check_read_args(
                rest,
                cwd,
                skip_first_non_flag=not has_f,
                flags_with_arg=("--arg", "--argjson", "--indent", "-L"),
                file_flags=("-f", "--from-file", "--slurpfile", "--rawfile"),
            )
        return _check_read_args(
            rest,
            cwd,
            flags_with_arg=("-n", "-c", "-d", "-f", "-t", "-k", "-U", "-L", "-w", "-s"),
        )

    if cmd_name == "awk":
        has_f = any(a in ("-f", "--file") or a.startswith("-f") for a in rest)
        prog_checked = has_f
        skip_val = False
        skip_file = False
        for a in rest:
            if skip_file:
                skip_file = False
                ok, reason = _is_safe_read_path(a, cwd)
                if not ok:
                    return False, reason
                continue
            if skip_val:
                skip_val = False
                continue
            if a in ("-f", "--file"):
                skip_file = True
                continue
            if a in ("-F", "-v"):
                skip_val = True
                continue
            if a.startswith("-"):
                continue
            if not prog_checked:
                prog_checked = True
                if re.search(r"\bsystem\s*\(|\bgetline\b", a):
                    return False, "awk program using system() or getline requires confirmation."
                continue
            ok, reason = _is_safe_read_path(a, cwd)
            if not ok:
                return False, reason
        return True, ""

    if cmd_name == "sed":
        is_inplace = any(a == "-i" or a.startswith("-i") or a.startswith("--in-place") for a in rest)
        has_explicit_expr = any(a in ("-e", "-f") or a.startswith(("-e", "-f")) for a in rest)
        pos = []
        skip_val = False
        skip_file = False
        for a in rest:
            if skip_file:
                skip_file = False
                ok, reason = _is_safe_read_path(a, cwd)
                if not ok:
                    return False, reason
                continue
            if skip_val:
                skip_val = False
                continue
            if a == "-f":
                skip_file = True
                continue
            if a == "-e":
                skip_val = True
                continue
            if a.startswith("-"):
                continue
            pos.append(a)
        files = pos if has_explicit_expr else pos[1:]
        if is_inplace:
            if not files:
                return False, "sed -i without explicit target files requires confirmation."
            for f in files:
                ok, reason = _is_safe_write_path(f, cwd, allow_top_dir=True)
                if not ok:
                    return False, reason
            return True, ""
        for f in files:
            ok, reason = _is_safe_read_path(f, cwd)
            if not ok:
                return False, reason
        return True, ""

    if cmd_name == "find":
        if any(a in ("-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0", "-fprintf", "-fls") for a in rest):
            return False, "find with mutating or exec flags requires confirmation."
        for a in rest:
            if a.startswith("-") or a in ("(", ")", "!", ","):
                break
            ok, reason = _is_safe_read_path(a, cwd)
            if not ok:
                return False, reason
        return True, ""

    if cmd_name == "xargs":
        idx = 0
        while idx < len(rest):
            a = rest[idx]
            if a in ("-0", "--null", "-r", "--no-run-if-empty", "-t", "--verbose", "-p", "--interactive", "-x", "--exit"):
                idx += 1
            elif a in ("-n", "--max-args", "-L", "--max-lines", "-s", "--max-chars", "-P", "--max-procs", "-I", "-i", "--replace", "-d", "--delimiter", "-E", "-e", "--eof"):
                idx += 2
            elif a in ("-a", "--arg-file") and idx + 1 < len(rest):
                ok, reason = _is_safe_read_path(rest[idx + 1], cwd)
                if not ok:
                    return False, reason
                idx += 2
            elif a.startswith("-"):
                idx += 1
            else:
                break
        inner = rest[idx:]
        if not inner:
            return True, ""
        return _is_bash_auto_approved(" ".join(shlex.quote(t) for t in inner))

    if cmd_name in FS_MUTATE_CMDS:
        if cmd_name == "mktemp":
            for a in rest:
                if not a.startswith("-"):
                    ok, reason = _is_safe_write_path(a, cwd, allow_top_dir=True)
                    if not ok:
                        return False, reason
                elif a.startswith("--tmpdir="):
                    ok, reason = _is_safe_write_path(a.split("=", 1)[1], cwd, allow_top_dir=True)
                    if not ok:
                        return False, reason
            return True, ""

        pos = []
        skip_next = False
        for a in rest:
            if skip_next:
                skip_next = False
                continue
            if cmd_name == "patch" and a in ("-i", "-d", "-o", "-r"):
                if idx_arg := rest[rest.index(a) + 1 : rest.index(a) + 2]:
                    if a == "-i":
                        ok, reason = _is_safe_read_path(idx_arg[0], cwd)
                    else:
                        ok, reason = _is_safe_write_path(idx_arg[0], cwd, allow_top_dir=True)
                    if not ok:
                        return False, reason
                skip_next = True
                continue
            if a == "--" or a.startswith("-"):
                continue
            pos.append(a)

        if cmd_name == "chmod":
            targets = pos[1:] if len(pos) > 1 else []
        elif cmd_name in ("cp", "ln"):
            if len(pos) < 2:
                return False, f"{cmd_name} requires source and destination paths."
            sources, dest = pos[:-1], pos[-1]
            for src in sources:
                ok, reason = _is_safe_read_path(src, cwd)
                if not ok:
                    return False, f"{cmd_name} source '{src}' outside safe roots requires confirmation."
            targets = [dest]
        elif cmd_name == "patch":
            targets = [str(cwd)]
        else:
            targets = pos

        if not targets:
            return False, f"{cmd_name} without explicit target path requires confirmation."
        allow_top = cmd_name not in ("rm", "rmdir", "unlink", "mv")
        for t in targets:
            ok, reason = _is_safe_write_path(t, cwd, allow_top_dir=allow_top)
            if not ok:
                return False, reason
        return True, ""

    if cmd_name == "git":
        idx = 0
        while idx < len(rest):
            tok = rest[idx]
            if tok in ("-C", "--git-dir", "--work-tree"):
                if idx + 1 >= len(rest):
                    return False, f"git {tok} missing path argument."
                ok, reason = _is_safe_read_path(rest[idx + 1], cwd)
                if not ok:
                    return False, f"git {tok} '{rest[idx + 1]}' outside the project safe perimeter requires confirmation."
                idx += 2
            elif tok.startswith(("--git-dir=", "--work-tree=")):
                ok, reason = _is_safe_read_path(tok.split("=", 1)[1], cwd)
                if not ok:
                    return False, reason
                idx += 1
            elif tok == "-c":
                if idx + 1 >= len(rest):
                    return False, "git -c missing config argument."
                cfg_key = rest[idx + 1].split("=", 1)[0].strip().lower()
                if cfg_key.startswith(("core.sshcommand", "core.fsmonitor", "core.hookspath", "core.pager", "core.editor", "alias.", "credential.", "filter.", "diff.external")):
                    return False, f"git -c '{rest[idx + 1]}' requires confirmation."
                idx += 2
            elif tok in ("--no-pager", "-p", "--paginate", "--bare", "--no-replace-objects", "--literal-pathspecs", "-P"):
                idx += 1
            elif tok in ("--version", "--help", "-h"):
                return True, ""
            else:
                break
        if idx >= len(rest):
            return False, "git without subcommand requires confirmation."
        subcmd = rest[idx]
        sub_args = rest[idx + 1 :]
        if subcmd in GIT_ALLOWED_SUBCMDS:
            return True, ""
        if subcmd == "worktree":
            wt_verb = next((a for a in sub_args if not a.startswith("-")), "list")
            if wt_verb in GIT_WORKTREE_SUBCMDS:
                return True, ""
            return False, f"git worktree '{wt_verb}' requires confirmation."
        if subcmd == "remote":
            if not sub_args or sub_args[0] in GIT_REMOTE_READ_SUBCMDS:
                return True, ""
            return False, f"git remote '{sub_args[0]}' requires confirmation."
        if subcmd == "branch":
            if any(a in ("-D", "--delete") for a in sub_args):
                return False, "git branch -D/--delete requires confirmation."
            return True, ""
        if subcmd == "checkout":
            if any(a in ("-f", "--force") for a in sub_args):
                return False, "git checkout --force requires confirmation."
            return True, ""
        if subcmd == "reset":
            if any(a in ("--hard", "--merge") for a in sub_args):
                return False, f"git reset {sub_args} requires confirmation."
            return True, ""
        if subcmd == "config":
            if any(a in ("--global", "--system", "--file", "-f") or a.startswith("--file=") for a in sub_args):
                return False, "git config outside local repository requires confirmation."
            return True, ""
        if subcmd == "push":
            for a in sub_args:
                if a in ("--force", "-f", "--force-with-lease", "--delete", "-d") or a.startswith("--force"):
                    return False, f"git push '{a}' requires confirmation."
                if re.search(r"(?:^|:)(?:refs/heads/)?(?:main|master|16\.0)$", a):
                    return False, f"git push to protected branch '{a}' requires confirmation."
            return True, ""
        return False, f"git subcommand '{subcmd}' is not in the auto-approved allowlist."

    if cmd_name == "gh":
        idx = 0
        while idx < len(rest):
            if rest[idx] in ("-R", "--repo"):
                idx += 2
            elif rest[idx].startswith("--repo="):
                idx += 1
            else:
                break
        rem = rest[idx:]
        if not rem:
            return False, "gh without subcommand requires confirmation."
        sub = rem[0]
        action = rem[1] if len(rem) > 1 else ""
        if sub == "pr" and action in GH_PR_SUBCMDS:
            return True, ""
        if sub == "issue" and action in GH_ISSUE_SUBCMDS:
            return True, ""
        if sub == "run" and action in GH_RUN_SUBCMDS:
            return True, ""
        if sub == "search" and action in GH_SEARCH_SUBCMDS:
            return True, ""
        if sub == "repo" and action == "view":
            return True, ""
        if sub == "auth" and action == "status":
            return True, ""
        if sub in ("status", "version", "--version", "--help"):
            return True, ""
        if sub == "api":
            for i_a, a in enumerate(rem[1:], 1):
                if a in ("-X", "--method") and i_a + 1 < len(rem) and rem[i_a + 1].upper() != "GET":
                    return False, f"gh api {rem[i_a + 1]} requires confirmation."
                if a.startswith(("-X", "--method=")):
                    val = a.split("=", 1)[1] if "=" in a else a[2:]
                    if val.upper() != "GET":
                        return False, f"gh api {val} requires confirmation."
                if a.startswith(("-f", "-F", "--field", "--raw-field", "--input")):
                    return False, "gh api mutation with fields/input requires confirmation."
            return True, ""
        return False, f"gh '{' '.join(rem[:2])}' is not in the auto-approved allowlist."

    if cmd_name == "systemctl":
        if "--user" not in rest:
            return False, "System-level systemctl (without --user) requires confirmation."
        non_flags = [a for a in rest if not a.startswith("-")]
        verb = non_flags[0] if non_flags else ""
        units = non_flags[1:] if len(non_flags) > 1 else []
        if verb in SYSTEMCTL_READ_VERBS:
            return True, ""
        if verb in SYSTEMCTL_SERVICE_VERBS:
            if units and all(SAFE_USER_UNIT_RE.match(u) for u in units):
                return True, ""
            return False, f"systemctl --user {verb} on '{' '.join(units)}' requires confirmation."
        return False, f"systemctl --user '{verb}' is not in the auto-approved allowlist."

    if cmd_name == "journalctl":
        if any(a.startswith(("--vacuum", "--rotate", "--flush", "--relinquish", "--sync")) for a in rest):
            return False, "journalctl administration flag requires confirmation."
        return True, ""

    if cmd_name in ("kb", "entire", "ruff", "pytest"):
        return _check_read_args(rest, cwd)

    if cmd_name == "odoo":
        for i_a, a in enumerate(rest):
            if a in ("-c", "--config") and i_a + 1 < len(rest):
                ok, reason = _is_safe_read_path(rest[i_a + 1], cwd)
                if not ok:
                    return False, f"odoo config '{rest[i_a + 1]}' outside safe roots requires confirmation."
            elif a.startswith(("-c=", "--config=")):
                ok, reason = _is_safe_read_path(a.split("=", 1)[1], cwd)
                if not ok:
                    return False, reason
        return True, ""

    if cmd_name in ("psql", "pg_dump", "pg_restore", "createdb", "dropdb"):
        db_names = []
        skip_next = False
        for i_a, a in enumerate(rest):
            if skip_next:
                skip_next = False
                continue
            if a in ("-h", "--host") and i_a + 1 < len(rest):
                host = rest[i_a + 1]
                if host not in ("localhost", "127.0.0.1", "::1") and not host.startswith("/"):
                    return False, f"{cmd_name} to remote host '{host}' requires confirmation."
                skip_next = True
                continue
            if a.startswith("--host="):
                host = a.split("=", 1)[1]
                if host not in ("localhost", "127.0.0.1", "::1") and not host.startswith("/"):
                    return False, f"{cmd_name} to remote host '{host}' requires confirmation."
                continue
            if a in ("-f", "--file", "-o", "--output") and i_a + 1 < len(rest):
                target = rest[i_a + 1]
                if cmd_name == "pg_dump" or a in ("-o", "--output"):
                    ok, reason = _is_safe_write_path(target, cwd, allow_top_dir=True)
                else:
                    ok, reason = _is_safe_read_path(target, cwd)
                if not ok:
                    return False, reason
                skip_next = True
                continue
            if a in ("-p", "-U", "-d", "-c", "-v", "-t", "-n", "-F", "-j", "--port", "--username", "--dbname", "--command", "--set", "--table", "--schema", "--format", "--jobs", "--maintenance-db"):
                skip_next = True
                continue
            if not a.startswith("-"):
                db_names.append(a)
        if cmd_name in ("createdb", "dropdb"):
            if not db_names or not all(db.startswith(("wt_", "test_")) for db in db_names):
                return False, f"{cmd_name} on '{' '.join(db_names)}' requires confirmation."
        return True, ""

    if cmd_name == "nix":
        if not rest or rest[0] not in NIX_ALLOWED_SUBCMDS:
            return False, f"nix '{rest[0] if rest else ''}' is not in the auto-approved allowlist."
        if "-c" in rest or "--command" in rest:
            flag = "-c" if "-c" in rest else "--command"
            idx = rest.index(flag)
            inner = rest[idx + 1 :]
            if not inner:
                return False, f"nix {flag} without command requires confirmation."
            return _is_bash_auto_approved(" ".join(shlex.quote(t) for t in inner))
        return True, ""

    if cmd_name == "uv":
        if not rest:
            return False, "uv without subcommand requires confirmation."
        sub = rest[0]
        if sub in UV_ALLOWED_SUBCMDS:
            return True, ""
        if sub == "pip" and len(rest) >= 2 and rest[1] in UV_PIP_READ_SUBCMDS:
            return True, ""
        if sub == "run":
            idx = 1
            while idx < len(rest):
                a = rest[idx]
                if a in ("--python", "--with", "--directory", "--project", "--env-file", "-p", "-m"):
                    if a == "-m":
                        break
                    idx += 2
                elif a.startswith("-"):
                    idx += 1
                else:
                    break
            inner = rest[idx:]
            if not inner:
                return False, "uv run without inner command requires confirmation."
            if inner[0] == "-m":
                inner = ["python3", *inner]
            return _is_bash_auto_approved(" ".join(shlex.quote(t) for t in inner))
        return False, f"uv '{sub}' is not in the auto-approved allowlist."

    if cmd_name == "copier":
        if not rest or rest[0] not in COPIER_ALLOWED_SUBCMDS:
            return False, f"copier '{rest[0] if rest else ''}' is not in the auto-approved allowlist."
        if rest[0] in ("--version", "--help"):
            return True, ""
        pos = []
        skip_next = False
        for a in rest[1:]:
            if skip_next:
                skip_next = False
                continue
            if a in ("-d", "--data", "--data-file", "-r", "--vcs-ref", "--answers-file"):
                skip_next = True
                continue
            if a.startswith("-"):
                continue
            pos.append(a)
        for p in pos[:-1]:
            ok, reason = _is_safe_read_path(p, cwd)
            if not ok:
                return False, reason
        if pos:
            ok, reason = _is_safe_write_path(pos[-1], cwd, allow_top_dir=True)
            if not ok:
                return False, reason
        return True, ""

    if cmd_name in ("python", "python3"):
        idx = 0
        while idx < len(rest):
            a = rest[idx]
            if a in ("-u", "-B", "-E", "-s", "-S", "-O", "-OO", "-I", "-q", "-v"):
                idx += 1
            elif a in ("-W", "-X"):
                idx += 2
            elif a in ("--version", "-V", "--help", "-h"):
                return True, ""
            else:
                break
        if idx >= len(rest):
            return False, f"{cmd_name} interactive REPL requires confirmation."
        first = rest[idx]
        rem = rest[idx + 1 :]
        if first == "-c":
            if not rem:
                return False, f"{cmd_name} -c without code requires confirmation."
            return _is_allowed_python_inline(rem[0], cwd)
        if first == "-m":
            if not rem or rem[0] not in PYTHON_SAFE_M_MODULES:
                return False, f"{cmd_name} -m '{rem[0] if rem else ''}' is not in the auto-approved allowlist."
            return _check_read_args(rem[1:], cwd)
        if first.startswith("-"):
            return False, f"{cmd_name} flag '{first}' requires confirmation."
        norm_script = _norm_path(first, cwd)
        if not _is_project_script(norm_script):
            return False, f"Running Python script '{first}' outside project scripts requires confirmation."
        return _check_read_args(rem, cwd)

    if cmd_name in ("bash", "sh"):
        idx = 0
        while idx < len(rest):
            a = rest[idx]
            if a in ("-e", "-u", "-x", "-o", "+e", "+u", "+x"):
                idx += 2 if a == "-o" else 1
            elif a.startswith("-") and a != "-c" and all(c in "eux" for c in a[1:]):
                idx += 1
            else:
                break
        if idx >= len(rest):
            return False, f"{cmd_name} interactive shell requires confirmation."
        if rest[idx] == "-c":
            if idx + 1 >= len(rest):
                return False, f"{cmd_name} -c without command requires confirmation."
            return _is_bash_auto_approved(rest[idx + 1])
        if rest[idx].startswith("-"):
            return False, f"{cmd_name} flag '{rest[idx]}' requires confirmation."
        norm_script = _norm_path(rest[idx], cwd)
        if not _is_project_script(norm_script):
            return False, f"Running shell script '{rest[idx]}' requires confirmation."
        return _check_read_args(rest[idx + 1 :], cwd)

    return False, f"Command '{cmd_name}' is not in the auto-approved allowlist."


def _is_bash_auto_approved(cmd):
    """Allowlist-only validator for run_command. Returns (True, '') only if every part is explicitly allowed."""
    stripped = cmd.strip()
    if not stripped:
        return True, ""

    for pattern in WHITELISTED_DEPLOY_RES:
        if pattern.match(stripped):
            return True, ""

    if "`" in stripped:
        return False, "Backtick command substitution requires confirmation."
    if SENSITIVE_PATH_RE.search(stripped) or PROTECTED_CONFIG_RE.search(stripped):
        return False, "Command referencing sensitive or protected configuration path requires confirmation."

    statements, subshells = _split_shell_statements(stripped)
    if statements is None:
        return False, "Unbalanced quotes or subshells require confirmation."

    for sub in subshells:
        ok, reason = _is_bash_auto_approved(sub)
        if not ok:
            return False, reason

    cwd = ROOT
    for stmt in statements:
        for pattern in WHITELISTED_DEPLOY_RES:
            if pattern.match(stmt.strip()):
                break
        else:
            try:
                raw_tokens = shlex.split(stmt, posix=True)
            except ValueError:
                return False, "Complex shell quoting requires confirmation."
            if not raw_tokens:
                continue

            tokens, write_redirs, read_redirs = _extract_redirections_and_clean_tokens(raw_tokens)
            for r_target in write_redirs:
                ok, reason = _is_safe_write_path(r_target, cwd, allow_top_dir=True)
                if not ok:
                    return False, reason
            for r_source in read_redirs:
                ok, reason = _is_safe_read_path(r_source, cwd)
                if not ok:
                    return False, reason

            while tokens and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=.*$", tokens[0]):
                val = tokens.pop(0).split("=", 1)[1]
                if val.startswith(("/", "~", "../")):
                    ok, reason = _is_safe_read_path(val, cwd)
                    if not ok:
                        return False, reason
            while tokens and tokens[0] in ("env", "time", "nice", "nohup", "command"):
                tokens.pop(0)
                while tokens and (tokens[0].startswith("-") or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=.*$", tokens[0])):
                    tok = tokens.pop(0)
                    if "=" in tok and not tok.startswith("-"):
                        val = tok.split("=", 1)[1]
                        if val.startswith(("/", "~", "../")):
                            ok, reason = _is_safe_read_path(val, cwd)
                            if not ok:
                                return False, reason
            if tokens and tokens[0] in ("timeout", "stdbuf"):
                wrapper = tokens.pop(0)
                while tokens and tokens[0].startswith("-"):
                    tokens.pop(0)
                if wrapper == "timeout" and tokens:
                    tokens.pop(0)
            if not tokens:
                continue

            cmd_token = tokens[0]
            rest = tokens[1:]

            if cmd_token == "cd":
                if len(rest) > 1:
                    return False, "cd with multiple arguments requires confirmation."
                target = rest[0] if rest else str(Path.home())
                ok, reason = _is_safe_read_path(target, cwd)
                if not ok:
                    return False, f"cd to '{target}' outside the project safe perimeter requires confirmation."
                cwd = Path(_norm_path(target, cwd))
                continue

            ok, reason = _is_statement_allowed(cmd_token, rest, cwd)
            if not ok:
                return False, reason

    return True, ""


def auto_decision(name, args):
    """Classify an unblocked PreToolUse call into 'auto_approve' (explicitly allowlisted) or 'force_ask' (prompt user).

    Note: In Antigravity's language_server (EnsurePermissions / EnsurePermissions.func1):
    - 'allow' is a no-op that keeps the baseline Ask prompt in Auto mode; 'auto_approve' overrides Ask -> Allow.
    - 'ask' does not override a baseline Allow grant; 'force_ask' forces a user prompt with AskSource=Hook.
    """
    if not name:
        return None

    if name in READ_TOOLS or name in SEARCH_TOOLS:
        raw_path = (
            args.get("Path")
            or args.get("AbsolutePath")
            or args.get("SearchPath")
            or args.get("SearchDirectory")
            or args.get("DirectoryPath")
            or args.get("TargetFile")
            or ""
        )
        ok, reason = _is_safe_read_path(raw_path, ROOT)
        if not ok:
            return {"decision": "force_ask", "reason": reason}
        return {"decision": "auto_approve"}

    if name in EDIT_TOOLS:
        raw_path = args.get("Path") or args.get("TargetFile") or args.get("AbsolutePath") or ""
        if not raw_path:
            return {"decision": "force_ask", "reason": "Edit tool called without a target path."}
        ok, reason = _is_safe_write_path(raw_path, ROOT, allow_top_dir=True)
        if not ok:
            return {"decision": "force_ask", "reason": reason}
        return {"decision": "auto_approve"}

    if name == "run_command":
        cmd = args.get("CommandLine") or ""
        ok, reason = _is_bash_auto_approved(cmd)
        if not ok:
            return {"decision": "force_ask", "reason": reason}
        return {"decision": "auto_approve"}

    if name in ("invoke_subagent", "send_message"):
        return {"decision": "auto_approve"}

    if name == "call_mcp_tool":
        tool_name = args.get("ToolName") or ""
        if tool_name in ("unlink", "delete_channel_message", "delete_chat_message"):
            return {"decision": "force_ask", "reason": f"MCP tool '{tool_name}' deletes remote records and requires confirmation."}
        return {"decision": "auto_approve"}

    return None


def pre(data):
    call = tool_call(data)
    name, args = call.get("name") or "", decode_args(call.get("args") or {})
    if not name:
        return None
    reason = denied_path(args)
    if reason:
        return {"decision": "deny", "reason": reason}
    pg_reason = check_pipeline_pre(conversation_id_of(data), name, args)
    if pg_reason:
        return {"decision": "deny", "reason": pg_reason}
    tool, tool_input = claude_call(name, args)
    if tool:
        payload = claude_payload(data, "PreToolUse", tool, tool_input)
        for command, timeout in commands("PreToolUse", tool):
            done = run(command, payload, timeout)
            if done.returncode == 2:
                return {"decision": "deny", "reason": done.stderr.strip() or "Blocked by " + command}
    return auto_decision(name, args)


def post(data):
    call = tool_call(data)
    name, args = call.get("name") or "", decode_args(call.get("args") or {})
    update_pipeline_post(conversation_id_of(data), name, args)
    tool, tool_input = claude_call(name, args)
    if tool:
        payload = claude_payload(data, "PostToolUse", tool, tool_input)
        for command, timeout in commands("PostToolUse", tool):
            run(command, payload, timeout)
    return {}


def session_start(data):
    """kb memory: open the session record, inject the index and today's standup."""
    script = ROOT / ".claude" / "hooks" / "kb-session-start.sh"
    payload = claude_payload(data, "SessionStart")
    payload["source"] = "startup"
    if not script.exists() or not payload["session_id"]:
        return {}
    text = run(f'bash "{script}"', payload, 55).stdout.strip()
    if not text:
        return {}
    text += (
        "\n\nkb: this Antigravity conversation is kb session id {id}. Pass `--id {id}` to "
        "`kb session note` and `kb session close`: a Claude Code session may be open at the "
        "same time, and without --id kb picks the newest one."
    ).format(id=payload["session_id"])
    return {"injectSteps": [{"ephemeralMessage": text}]}


def find_ls():
    """Find Antigravity LanguageServer (address, csrf_token) from env or /proc."""
    if str(Path.home()).startswith("/tmp/"):
        return None, None
    ls_addr = os.environ.get("ANTIGRAVITY_LS_ADDRESS")
    csrf = os.environ.get("ANTIGRAVITY_CSRF_TOKEN")
    if ls_addr and csrf:
        return ls_addr, csrf
    proc = Path("/proc")
    if not proc.is_dir():
        return None, None
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "environ").read_bytes()
        except OSError:
            continue
        if b"ANTIGRAVITY_LS_ADDRESS=" not in raw or b"ANTIGRAVITY_CSRF_TOKEN=" not in raw:
            continue
        env_map = {}
        for item in raw.split(b"\0"):
            if b"=" in item:
                k, v = item.split(b"=", 1)
                if k in (b"ANTIGRAVITY_LS_ADDRESS", b"ANTIGRAVITY_CSRF_TOKEN"):
                    env_map[k.decode()] = v.decode(errors="ignore")
        if env_map.get("ANTIGRAVITY_LS_ADDRESS") and env_map.get("ANTIGRAVITY_CSRF_TOKEN"):
            return env_map["ANTIGRAVITY_LS_ADDRESS"], env_map["ANTIGRAVITY_CSRF_TOKEN"]
    return None, None


def ls_request(method, payload):
    """Call a Connect-JSON method on Antigravity's LanguageServerService."""
    import urllib.request

    ls_addr, csrf = find_ls()
    if not (ls_addr and csrf):
        return None
    try:
        req = urllib.request.Request(
            f"http://{ls_addr}/exa.language_server_pb.LanguageServerService/{method}",
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Connect-Protocol-Version": "1",
                "x-codeium-csrf-token": csrf,
            },
            method="POST",
        )
        return json.loads(urllib.request.urlopen(req, timeout=2).read().decode())
    except Exception as exc:  # noqa: BLE001
        return {"_error": repr(exc)}


def sync_ui_plugins():
    """Keep ~/.gemini/config/plugins/<name> in sync with .agents/plugins/<name> for UI sidecar plugins.

    Antigravity's SidecarManager only discovers sidecar UI panels under ~/.gemini/config/plugins/,
    so the template ships the UI plugins in .agents/plugins/ and mirrors them into
    ~/.gemini/config/plugins/ when a hook runs.
    """
    plugins_root = ROOT / ".agents" / "plugins"
    if not plugins_root.is_dir():
        return
    changed_ids = set()
    wanted_ids = set()
    for src in sorted(plugins_root.iterdir()):
        has_sidecar = (src / "sidecars" / "panel" / "server.mjs").exists()
        if not src.is_dir() or not (has_sidecar or src.name == "workspace-inspector"):
            continue
        sidecar_id = f"{src.name}/panel"
        if has_sidecar:
            wanted_ids.add(sidecar_id)
        dst = Path.home() / ".gemini" / "config" / "plugins" / src.name
        changed = False
        for path in src.rglob("*"):
            if path.is_dir():
                continue
            target = dst / path.relative_to(src)
            data = path.read_bytes()
            if not target.exists() or target.read_bytes() != data:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                changed = True
        if changed and has_sidecar:
            changed_ids.add(sidecar_id)

    kb_global = Path.home() / ".gemini" / "config" / "plugins" / "kb" / "sidecars" / "panel" / "server.mjs"
    if kb_global.exists():
        wanted_ids.add("kb/panel")

    stamp_file = ANTIGRAVITY_DIR / "plugin_data" / "sidecar_sync.stamp"
    now = time.time()
    recent = False
    try:
        recent = stamp_file.exists() and (now - stamp_file.stat().st_mtime) < 30
    except OSError:
        pass
    if not changed_ids and recent:
        return

    info = ls_request("GetSidecars", {})
    if not info or "_error" in info:
        return
    try:
        stamp_file.parent.mkdir(parents=True, exist_ok=True)
        stamp_file.write_text(str(now))
    except OSError:
        pass

    sidecar_list = info.get("sidecars") or info.get("sidecarInfos") or info.get("statuses") or []
    running = {
        s.get("id") or s.get("sidecarId"): s.get("status")
        for s in sidecar_list
        if isinstance(s, dict) and (s.get("id") or s.get("sidecarId"))
    }
    for sidecar_id in sorted(wanted_ids):
        if sidecar_id in changed_ids:
            ls_request("ManageSidecar", {"sidecarId": sidecar_id, "action": "SIDECAR_ACTION_RESTART"})
        elif running.get(sidecar_id) != "SIDECAR_STATUS_RUNNING":
            ls_request("ManageSidecar", {"sidecarId": sidecar_id, "action": "SIDECAR_ACTION_START"})


def pipeline_gate_cli(argv):
    """CLI helper to inspect or advance pipeline-gates state from the terminal."""
    conv_id = os.environ.get("ANTIGRAVITY_CONVERSATION_ID") or ""
    runs = load_runs()
    if not conv_id and runs:
        conv_id = list(runs.keys())[-1]
    if not conv_id:
        conv_id = "default"
    now_ms = int(time.time() * 1000)
    verb = argv[0] if argv else "status"
    rest = argv[1:]
    current = runs.get(conv_id)

    if verb == "start":
        task = rest[0].upper() if rest else "PIPELINE"
        current = {
            "task": task,
            "startedAt": now_ms,
            "phase": 0,
            "phaseAt": [now_ms] + [0] * (len(PHASES) - 1),
            "status": None,
            "grill": None,
            "qc": None,
            "qcSeq": 0,
            "seq": 0,
            "lastChange": 0,
            "lastReview": 0,
            "lastGreen": 0,
            "lastTests": "",
            "fixRounds": 0,
            "roundSeq": 0,
            "extraRounds": 0,
            "tasks": [{"key": k.upper(), "title": "", "state": "pending", "at": now_ms, "url": ""} for k in rest],
            "blocked": [],
            "isDone": False,
        }
        runs[conv_id] = current
        save_runs(runs)
    elif verb == "phase" and rest:
        ph = int(rest[0])
        task = rest[1].upper() if len(rest) > 1 else ""
        if not current or current.get("isDone"):
            pipeline_gate_cli(["start", *( [task] if task else [] )])
            runs = load_runs()
            current = runs[conv_id]
        closed = closed_for_dev(current)
        if ph >= 2 and closed:
            sys.stdout.write(f"pipeline-gates: Phase {ph} refused; closed gates: {', '.join(closed)}\n")
            sys.exit(2)
        if ph >= 6 and not qc_holds(current):
            sys.stdout.write("pipeline-gates: Phase 6 refused until QC verdict passes.\n")
            sys.exit(2)
        current["phase"] = ph
        if task:
            current["task"] = task
        phase_at = current.get("phaseAt") or [0] * len(PHASES)
        if 0 <= ph < len(phase_at) and phase_at[ph] == 0:
            phase_at[ph] = now_ms
        current["phaseAt"] = phase_at
        current["isDone"] = ph == len(PHASES) - 1
        runs[conv_id] = current
        save_runs(runs)
    elif verb == "qc-verdict" and len(rest) >= 2:
        verdict, summary = rest[0], " ".join(rest[1:])
        if current and not current.get("isDone"):
            seq = (current.get("seq") or 0) + 1
            current["seq"] = seq
            current["qc"] = {"isOpen": verdict == "pass", "how": f"{verdict}: {summary}", "at": now_ms}
            current["qcSeq"] = seq
            runs[conv_id] = current
            save_runs(runs)
    elif verb == "open" and rest:
        gate = rest[0]
        reason = " ".join(rest[1:]) or "confirmed"
        if current and not current.get("isDone"):
            if gate == "rounds":
                current["extraRounds"] = (current.get("extraRounds") or 0) + 3
            elif gate in ("status", "grill", "qc"):
                current[gate] = {"isOpen": True, "how": reason, "at": now_ms}
                if gate == "qc":
                    seq = (current.get("seq") or 0) + 1
                    current["seq"] = seq
                    current["qcSeq"] = seq
            runs[conv_id] = current
            save_runs(runs)
    elif verb == "end" and current:
        current["isDone"] = True
        runs[conv_id] = current
        save_runs(runs)

    current = runs.get(conv_id)
    if not current:
        sys.stdout.write("pipeline-gates: no pipeline run in this session.\n")
        return
    sys.stdout.write(
        f"pipeline {current.get('task') or '(task?)'} · phase {current.get('phase')} {PHASES[current.get('phase', 0)]}\n"
        f"status: {'open' if is_gate_open(current.get('status')) else 'closed'} · "
        f"grill: {'open' if is_gate_open(current.get('grill')) else 'closed'} · "
        f"qc: {'open' if qc_holds(current) else 'closed'}\n"
    )


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "pipeline-gate":
        sync_ui_plugins()
        pipeline_gate_cli(sys.argv[2:])
        return
    try:
        sync_ui_plugins()
        data = json.loads(sys.stdin.read() or "{}")
        result = {"pre": pre, "post": post, "session-start": session_start}[mode](data)
    except Exception as error:  # noqa: BLE001 - a broken adapter must not stop the agent
        sys.stderr.write(f"claude_hook.py {mode}: {error!r}\n")
        result = None if mode == "pre" else {}
    if result is not None:
        sys.stdout.write(json.dumps(result) + "\n")


if __name__ == "__main__":
    main()
