#!/usr/bin/env bash
# Quick drop into the memory raw/ folder — for use from the user's shell,
# typically via an alias in ~/.bashrc (or equivalent):
#
#   alias raw='bash __MEMORY_DIR__/scripts/raw-drop.sh'
#
# Usage:
#   raw https://example.com/article
#   raw "idea: refactor msl_module scrap reasons"
#   raw < file.md                             # pipe in a file's content
#
# Each invocation creates a new timestamped file under raw/drops/YYYY-MM/.
# Per-drop files keep the digest manifest sane — a single accumulating
# drops.md would appear "already processed" after the first pass even
# when new lines have been appended. mktemp handles same-second collisions.

set -euo pipefail

MEM="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RAW="$MEM/raw"
MONTH_DIR="$RAW/drops/$(date +%Y-%m)"
TS=$(date '+%Y-%m-%d-%H%M%S')

mkdir -p "$MONTH_DIR"

if [ $# -gt 0 ]; then
  FILE=$(mktemp "$MONTH_DIR/$TS-line-XXXXXX.md")
  printf '%s %s\n' "$TS" "$*" > "$FILE"
  echo "Saved: $FILE"
elif [ ! -t 0 ]; then
  FILE=$(mktemp "$MONTH_DIR/$TS-piped-XXXXXX.md")
  {
    printf '# Piped drop at %s\n\n' "$TS"
    cat
    printf '\n'
  } > "$FILE"
  echo "Saved: $FILE"
else
  cat <<USAGE
Usage:
  raw <text or URL>            one-line drop
  raw < file.md                pipe content in

Drops go to: $RAW/drops/YYYY-MM/
USAGE
  exit 1
fi
