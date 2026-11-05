#!/usr/bin/env python3
"""Regression corpus for template/.agents/hooks/claude_hook.py, the Antigravity adapter.

Run from the repo root: python3 tests/test_antigravity_hook.py

The adapter runs the Claude Code hooks wired in .claude/settings.json under Antigravity
and enforces a safe-perimeter auto-mode equivalent to Claude Code's autoMode:
- `{"decision": "deny", "reason": ...}` when a guard hook, permissions.deny, or pipeline-gates blocks;
- `{"decision": "auto_approve"}` when the call is inside the project's safe perimeter;
- `{"decision": "force_ask", "reason": ...}` when the call touches paths or commands outside the safe perimeter;
- empty stdout `""` only when no tool name is present (broken/empty stdin).
agy also sends arg values JSON-encoded twice (`"TargetFile": "\\"/path\\""`), which got
past a path guard before they were unwrapped.

Each case builds a tiny project (adapter, settings.json, two stub hooks) in a temp dir.
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile

ADAPTER = "template/{% if use_antigravity %}.agents{% endif %}/hooks/claude_hook.py"

# exit 2 + stderr is a Claude block; the guard refuses one command and one directory
GUARD = """#!/bin/bash
input=$(cat)
command=$(printf '%s' "$input" | python3 -c "import sys,json; print(json.load(sys.stdin)['tool_input'].get('command',''))")
path=$(printf '%s' "$input" | python3 -c "import sys,json; print(json.load(sys.stdin)['tool_input'].get('file_path',''))")
case "$command" in *"rm -rf /"*) echo "Blocked: dangerous" >&2; exit 2;; esac
case "$path" in "$PROJECT_DIR"/src/core/*) echo "Blocked: read-only" >&2; exit 2;; esac
exit 0
"""
SETTINGS = {
    "hooks": {
        "PreToolUse": [
            {"matcher": "Edit|Write", "hooks": [{"type": "command", "command": "bash $PROJECT_DIR/.claude/hooks/guard.sh"}]},
            {"matcher": "Bash", "hooks": [
                {"type": "command", "command": "bash $PROJECT_DIR/.claude/hooks/guard.sh"},
                {"type": "command", "command": "bash $PROJECT_DIR/.claude/hooks/entire-fanout.sh pre-task"},
            ]},
        ],
    },
    "permissions": {"deny": ["Read(./.entire/metadata/**)"]},
}


def project(adapter_path=ADAPTER):
    root = tempfile.mkdtemp()
    os.makedirs(f"{root}/.agents/hooks")
    os.makedirs(f"{root}/.agents/plugins/workspace-inspector")
    os.makedirs(f"{root}/.claude/hooks")
    os.makedirs(f"{root}/home")
    shutil.copy(adapter_path, f"{root}/.agents/hooks/claude_hook.py")
    with open(f"{root}/.agents/plugins/workspace-inspector/plugin.json", "w") as fh:
        json.dump({"name": "workspace-inspector", "version": "1.0.0"}, fh)
    with open(f"{root}/.claude/settings.json", "w") as fh:
        json.dump(SETTINGS, fh)
    with open(f"{root}/.claude/hooks/guard.sh", "w") as fh:
        fh.write(GUARD)
    # a Claude-only hook: the adapter must never run it
    with open(f"{root}/.claude/hooks/entire-fanout.sh", "w") as fh:
        fh.write("#!/bin/bash\necho ran > \"$PROJECT_DIR/fanout-ran\"\n")
    with open(f"{root}/.copier-answers.yml", "w") as fh:
        fh.write("project_dir_var: PROJECT_DIR\n")
    with open(f"{root}/.agents/hooks/deploy-allow.json", "w") as fh:
        json.dump({"merge_repos": ["acme/addons", "acme/env"], "hosts": ["prod", "box1.lan"],
                   "prod_service": "odoo.service"}, fh)
    os.chmod(f"{root}/.agents/hooks/claude_hook.py", stat.S_IRWXU)
    return root


def twice(args):
    return {key: json.dumps(value) for key, value in args.items()}


def call(root, mode, name, args, raw=None):
    payload = raw if raw is not None else json.dumps({"conversationId": "c1", "toolCall": {"name": name, "args": args}})
    env = {k: v for k, v in os.environ.items() if k not in ("PROJECT_DIR", "ANTIGRAVITY_APP_DATA_DIR")}
    env["HOME"] = f"{root}/home"
    env["ANTIGRAVITY_APP_DATA_DIR"] = f"{root}/home/.gemini/antigravity"
    env["ANTIGRAVITY_CONVERSATION_ID"] = "c1"
    done = subprocess.run(
        [sys.executable, f"{root}/.agents/hooks/claude_hook.py", mode],
        input=payload, capture_output=True, text=True, env=env, check=False,
    )
    assert done.returncode == 0, done
    return done.stdout


def cli(root, *argv):
    env = {k: v for k, v in os.environ.items() if k not in ("PROJECT_DIR", "ANTIGRAVITY_APP_DATA_DIR")}
    env["HOME"] = f"{root}/home"
    env["ANTIGRAVITY_APP_DATA_DIR"] = f"{root}/home/.gemini/antigravity"
    env["ANTIGRAVITY_CONVERSATION_ID"] = "c1"
    return subprocess.run(
        [sys.executable, f"{root}/.agents/hooks/claude_hook.py", "pipeline-gate", *argv],
        capture_output=True, text=True, env=env, check=False,
    )


def classify(out):
    if out.startswith('{"decision": "deny"'):
        return "deny"
    if out.startswith('{"decision": "force_ask"'):
        return "ask"
    if out == '{"decision": "auto_approve"}':
        return "allow"
    return out


def main():
    adapter_path = sys.argv[1] if len(sys.argv) > 1 else ADAPTER
    root = project(adapter_path)
    core, mine = f"{root}/src/core/models.py", f"{root}/src/mine/models.py"
    wt_code = f"{root}/.worktrees/kio-1/mod/models.py"
    wt_doc = f"{root}/.worktrees/kio-1/mod/README.rst"
    meta = f"{root}/.entire/metadata/t.json"
    home_file = f"{root}/home/Documents/notes.txt"
    bashrc = f"{root}/home/.bashrc"
    agy_cfg = f"{root}/home/.gemini/config/config.json"
    ssh_key = f"{root}/home/.ssh/id_rsa"
    # (want, label, mode, tool, args); want: "allow", "ask", "deny", "" silent, "{}" for post
    cases = [
        ("allow", "plain command inside safe perimeter is allowed", "pre", "run_command", {"CommandLine": "ls -la"}),
        ("deny", "guard blocks a command", "pre", "run_command", {"CommandLine": "rm -rf /"}),
        ("deny", "guard blocks a command, args encoded twice", "pre", "run_command", twice({"CommandLine": "rm -rf /"})),
        ("deny", "guard blocks a read-only edit", "pre", "replace_file_content", {"TargetFile": core}),
        ("deny", "guard blocks a read-only edit, args encoded twice", "pre", "write_to_file", twice({"TargetFile": core, "Overwrite": True})),
        ("allow", "an allowed edit inside project is allowed", "pre", "write_to_file", twice({"TargetFile": mine, "Overwrite": True})),
        ("allow", "edit_file uses Path and is allowed inside project", "pre", "edit_file", {"Path": mine}),
        ("deny", "edit_file uses Path and blocks read-only", "pre", "edit_file", {"Path": core}),
        ("deny", "permissions.deny covers view_file", "pre", "view_file", {"AbsolutePath": meta}),
        ("deny", "permissions.deny covers read_file", "pre", "read_file", {"Path": meta}),
        ("deny", "permissions.deny covers view_file, args encoded twice", "pre", "view_file", twice({"AbsolutePath": meta})),
        ("deny", "permissions.deny covers a command naming the path", "pre", "run_command", {"CommandLine": "cat .entire/metadata/t.json"}),
        ("allow", "invoke_subagent is allowed when pipeline-gates is idle", "pre", "invoke_subagent", {"Subagents": "[]"}),
        ("{}", "post answers {}", "post", "write_to_file", {"TargetFile": mine}),
        # Safe perimeter allow cases
        ("allow", "systemctl --user restart on odoo-wt unit is allowed", "pre", "run_command", {"CommandLine": "systemctl --user restart odoo-wt@kio-1.service"}),
        ("allow", "rm inside project .worktrees/_env is allowed", "pre", "run_command", {"CommandLine": "rm -rf .worktrees/_env/kio-1"}),
        ("allow", "whitelistedgh pr merge with --match-head-commit is allowed", "pre", "run_command", {"CommandLine": "gh pr merge 42 --squash --delete-branch --match-head-commit 0123456789abcdef0123456789abcdef01234567"}),
        ("allow", "whitelisted deploy script with --apply is allowed", "pre", "run_command", {"CommandLine": "bash .claude/skills/deploy/scripts/prod-requeue-jobs.sh --apply"}),
        ("allow", "safe inline python stdlib inspection is allowed", "pre", "run_command", {"CommandLine": "python3 -c \"import json, sys; print(json.dumps({'ok': True}))\""}),
        ("allow", "git status and diff inside project are allowed", "pre", "run_command", {"CommandLine": "git status && git diff --stat"}),
        ("allow", "gh pr view is allowed", "pre", "run_command", {"CommandLine": "gh pr view 42"}),
        ("allow", "read_file inside project is allowed", "pre", "read_file", {"Path": mine}),
        ("allow", "call_mcp_tool read/send is allowed", "pre", "call_mcp_tool", {"ServerName": "kaertech-odoo", "ToolName": "search_read"}),
        # Default-ask (allowlist-only) cases: anything not explicitly allowlisted prompts user
        ("ask", "unlisted command curl prompts user", "pre", "run_command", {"CommandLine": "curl -s https://example.com"}),
        ("ask", "unlisted command npx prompts user", "pre", "run_command", {"CommandLine": "npx cowsay hi"}),
        ("ask", "unlisted git subcommand clean prompts user", "pre", "run_command", {"CommandLine": "git clean -fd"}),
        ("ask", "git branch -D prompts user", "pre", "run_command", {"CommandLine": "git branch -D feat"}),
        ("ask", "git reset --hard prompts user", "pre", "run_command", {"CommandLine": "git reset --hard HEAD~1"}),
        ("ask", "unlisted nix subcommand profile install prompts user", "pre", "run_command", {"CommandLine": "nix profile install nixpkgs#hello"}),
        ("ask", "unlisted uv subcommand tool install prompts user", "pre", "run_command", {"CommandLine": "uv tool install ruff"}),
        ("ask", "cat outside project in HOME prompts user", "pre", "run_command", {"CommandLine": f"cat {home_file}"}),
        ("ask", "unpinned deploy script call prompts user", "pre", "run_command", {"CommandLine": "bash .claude/skills/deploy/scripts/prod-deploy-modules.sh main kaertech_base"}),
        ("ask", "rm outside project in HOME prompts user", "pre", "run_command", {"CommandLine": "rm -rf ~/Documents/notes.txt"}),
        ("ask", "rm on project top-level src prompts user", "pre", "run_command", {"CommandLine": "rm -rf src"}),
        ("ask", "sudo command prompts user", "pre", "run_command", {"CommandLine": "sudo systemctl restart nginx"}),
        ("ask", "system-level systemctl without --user prompts user", "pre", "run_command", {"CommandLine": "systemctl restart postgresql"}),
        ("ask", "systemctl --user on unrelated unit prompts user", "pre", "run_command", {"CommandLine": "systemctl --user stop pipewire.service"}),
        ("ask", "kill/pkill prompts user", "pre", "run_command", {"CommandLine": "pkill -9 -f python"}),
        ("ask", "git push to main prompts user", "pre", "run_command", {"CommandLine": "git push origin main"}),
        ("ask", "git -C repo push --force prompts user", "pre", "run_command", {"CommandLine": "git -C src/mine push --force origin feat"}),
        ("ask", "unpinned gh pr merge prompts user", "pre", "run_command", {"CommandLine": "gh pr merge 42 --squash"}),
        ("ask", "arbitrary ssh command prompts user", "pre", "run_command", {"CommandLine": "ssh user@example.com 'uptime'"}),
        ("ask", "inline python mutating HOME prompts user", "pre", "run_command", {"CommandLine": "python3 -c \"import shutil, pathlib; shutil.rmtree(pathlib.Path.home() / 'foo')\""}),
        ("ask", "inline python spawning subprocess prompts user", "pre", "run_command", {"CommandLine": "python3 -c \"import subprocess; subprocess.run(['ls'])\""}),
        ("ask", "shell redirection outside project prompts user", "pre", "run_command", {"CommandLine": f"echo hi > {home_file}"}),
        ("ask", "sed -i outside project prompts user", "pre", "run_command", {"CommandLine": f"sed -i 's/a/b/' {home_file}"}),
        ("ask", "edit_file on ~/.bashrc prompts user", "pre", "edit_file", {"Path": bashrc}),
        ("ask", "edit_file on ~/.gemini/config/config.json prompts user", "pre", "edit_file", {"Path": agy_cfg}),
        ("ask", "read_file on ~/.ssh/id_rsa prompts user", "pre", "read_file", {"Path": ssh_key}),
        ("ask", "read_file outside project in HOME prompts user", "pre", "read_file", {"Path": home_file}),
        ("ask", "call_mcp_tool unlink prompts user", "pre", "call_mcp_tool", {"ServerName": "kaertech-odoo", "ToolName": "unlink"}),
        # deploy allow-list: repos, host aliases and unit from deploy-allow.json (the copier answers)
        ("allow", "gh pr merge -R one of the project's repos is allowed", "pre", "run_command", {"CommandLine": "gh pr merge 42 -R acme/addons --merge --match-head-commit 0123456789abcdef0123456789abcdef01234567"}),
        ("ask", "gh pr merge -R a repo outside the answers prompts user", "pre", "run_command", {"CommandLine": "gh pr merge 42 -R kaertech-dev/odoo-addons-16 --merge --match-head-commit 0123456789abcdef0123456789abcdef01234567"}),
        ("allow", "deploy lock on a test alias from the answers is allowed", "pre", "run_command", {"CommandLine": "bash .claude/skills/deploy/scripts/prod-deploy-lock.sh box1.lan status"}),
        ("ask", "deploy lock on an alias outside the answers prompts user", "pre", "run_command", {"CommandLine": "bash .claude/skills/deploy/scripts/prod-deploy-lock.sh odoo1.kepi status"}),
        ("allow", "module deploy to prod is allowed", "pre", "run_command", {"CommandLine": "bash .claude/skills/deploy/scripts/prod-deploy-modules.sh prod -u acme_base"}),
        ("allow", "stopping the answers' Odoo unit on prod is allowed", "pre", "run_command", {"CommandLine": "ssh -A -F .ssh/config prod 'systemctl --user stop odoo.service'"}),
        ("ask", "a raw user@ip ssh prompts user", "pre", "run_command", {"CommandLine": "ssh kaertech@172.18.32.68 'cat /tmp/odoo-deploy.lock 2>/dev/null || true'"}),
    ]
    failures = 0
    for want, label, mode, tool, args in cases:
        out = call(root, mode, tool, args).strip()
        got = classify(out)
        ok = got == want
        failures += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {label}" + ("" if ok else f": want {want!r}, got {out!r}"))
    for label, raw in (("broken stdin stays silent", "not json"), ("empty stdin stays silent", "")):
        out = call(root, "pre", "", {}, raw=raw)
        ok = out == ""
        failures += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {label}" + ("" if ok else f": got {out!r}"))
    bare = project(adapter_path)
    os.remove(f"{bare}/.agents/hooks/deploy-allow.json")
    out = call(bare, "pre", "run_command", {"CommandLine": "bash .claude/skills/deploy/scripts/prod-deploy-lock.sh prod status"}).strip()
    ok = classify(out) == "ask"
    failures += not ok
    print(f"  {'ok  ' if ok else 'FAIL'} without deploy-allow.json a deploy script prompts" + ("" if ok else f": got {out!r}"))
    ok = not os.path.exists(f"{root}/fanout-ran")
    failures += not ok
    print(f"  {'ok  ' if ok else 'FAIL'} the Claude-only entire fan-out never runs")
    ok = os.path.isfile(f"{root}/home/.gemini/config/plugins/workspace-inspector/plugin.json")
    failures += not ok
    print(f"  {'ok  ' if ok else 'FAIL'} workspace-inspector sidecar plugin syncs into ~/.gemini/config/plugins/")

    # pipeline-gates enforcement tests
    cli(root, "start", "KIO-1")
    pipeline_cases_before = [
        ("deny", "pipeline-gates blocks dev subagent before status & grill", "pre", "invoke_subagent", {"TypeName": "dev"}),
        ("deny", "pipeline-gates blocks worktree edit before status & grill", "pre", "edit_file", {"Path": wt_code}),
        ("{}", "teams-mcp post opens status gate", "post", "call_mcp_tool", {"ServerName": "teams-mcp", "ToolName": "send_chat_message"}),
        ("deny", "pipeline-gates still blocks dev subagent while grill closed", "pre", "invoke_subagent", {"TypeName": "dev"}),
    ]
    for want, label, mode, tool, args in pipeline_cases_before:
        out = call(root, mode, tool, args).strip()
        got = classify(out)
        ok = got == want
        failures += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {label}" + ("" if ok else f": want {want!r}, got {out!r}"))

    cli(root, "open", "grill", "confirmed")
    pipeline_cases_after_grill = [
        ("allow", "pipeline-gates allows dev subagent once status & grill open", "pre", "invoke_subagent", {"TypeName": "dev"}),
        ("allow", "pipeline-gates allows worktree code edit once status & grill open", "pre", "edit_file", {"Path": wt_code}),
        ("{}", "worktree code edit advances seq in post", "post", "edit_file", {"Path": wt_code}),
        ("deny", "pipeline-gates blocks gh pr create before qc pass", "pre", "run_command", {"CommandLine": "gh pr create --title test"}),
    ]
    for want, label, mode, tool, args in pipeline_cases_after_grill:
        out = call(root, mode, tool, args).strip()
        got = classify(out)
        ok = got == want
        failures += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {label}" + ("" if ok else f": want {want!r}, got {out!r}"))

    cli(root, "qc-verdict", "pass", "review and tests green")
    pipeline_cases_after_qc = [
        ("{}", "worktree doc edit does not invalidate qc pass", "post", "edit_file", {"Path": wt_doc}),
        ("allow", "pipeline-gates allows gh pr create after qc pass and doc edit", "pre", "run_command", {"CommandLine": "gh pr create --title test"}),
        ("{}", "worktree code edit invalidates qc pass", "post", "edit_file", {"Path": wt_code}),
        ("deny", "pipeline-gates blocks gh pr create after new code edit", "pre", "run_command", {"CommandLine": "gh pr create --title test"}),
    ]
    for want, label, mode, tool, args in pipeline_cases_after_qc:
        out = call(root, mode, tool, args).strip()
        got = classify(out)
        ok = got == want
        failures += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {label}" + ("" if ok else f": want {want!r}, got {out!r}"))

    shutil.rmtree(root)
    print("PASS" if not failures else f"{failures} FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
