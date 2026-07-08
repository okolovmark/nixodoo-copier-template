#!/bin/bash
# PreToolUse hook: block dangerous bash commands
set -euo pipefail

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('tool_input',{}).get('command',''))")

# Dangerous patterns (POSIX ERE, matched with =~)
PATTERNS=(
    'rm -rf /'
    'rm -rf ~'
    'rm -rf \.($|[[:space:]])'
    'git push --force'
    'git push -f($|[[:space:]])'
    'git reset --hard'
    'git clean -fd'
    'git checkout -- \.($|[[:space:]])'
    '[Dd][Rr][Oo][Pp] [Dd][Aa][Tt][Aa][Bb][Aa][Ss][Ee]'
    'dropdb($|[[:space:]])'
)

for pattern in "${PATTERNS[@]}"; do
    if [[ "$COMMAND" =~ $pattern ]]; then
        echo "Blocked: dangerous command detected — '$pattern'. Run manually if intended." >&2
        exit 2
    fi
done

exit 0
