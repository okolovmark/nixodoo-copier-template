#!/usr/bin/env bash
# Render a Teams markdown draft through the exact pipeline teams-mcp uses
# (marked, GFM + breaks, then the DOMPurify allowlist) and print the HTML that
# Graph will receive. Anything missing from the output was silently dropped.
#
# Usage: preview.sh <draft.md>
#        preview.sh - < draft.md
set -euo pipefail

draft=${1:?usage: preview.sh <draft.md>}
if [ "$draft" = "-" ]; then
  draft=$(mktemp)
  trap 'rm -f "$draft"' EXIT
  cat >"$draft"
fi

# newest copy in the npx cache (several hashed dirs can coexist)
util=""
for cand in "$HOME"/.npm/_npx/*/node_modules/@okolovmark/teams-mcp/dist/utils/markdown.js; do
  [ -f "$cand" ] || continue
  if [ -z "$util" ] || [ "$cand" -nt "$util" ]; then
    util=$cand
  fi
done

if [ -z "$util" ]; then
  echo "teams-mcp is not in the npx cache yet (call any teams MCP tool once); skipping the preview" >&2
  exit 0
fi

DRAFT="$draft" node --input-type=module -e "
import { readFileSync } from 'node:fs';
import { markdownToHtml } from '$util';
console.log(await markdownToHtml(readFileSync(process.env.DRAFT, 'utf8')));
"
