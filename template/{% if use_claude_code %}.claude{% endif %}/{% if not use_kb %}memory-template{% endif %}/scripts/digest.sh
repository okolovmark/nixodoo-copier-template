#!/usr/bin/env bash
# List unprocessed files in $MEM/raw/ so Claude can decide what to do with
# each one (update an existing node, create a new node, or skip).
#
# Tracking is via $MEM/raw/.processed — a newline-separated list of paths
# (relative to raw/) that have already been digested. To mark an item
# processed, Claude appends the relative path to that file (covered by
# the path-based Edit(memory/**) permission rule, no new rule needed).
#
# Usage: bash digest.sh
# Output:
#   "EMPTY: <reason>"   — nothing to process
#   otherwise           — human-readable list of unprocessed items

set -euo pipefail

MEM="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RAW="$MEM/raw"
MANIFEST="$RAW/.processed"

if [ ! -d "$RAW" ]; then
  echo "EMPTY: raw/ does not exist yet"
  exit 0
fi

touch "$MANIFEST"

# Portable file size: GNU stat first, BSD stat fallback (macOS).
get_size() {
  stat -c %s "$1" 2>/dev/null || stat -f %z "$1" 2>/dev/null || echo 0
}

unprocessed=()
while IFS= read -r -d '' f; do
  rel="${f#$RAW/}"
  [ "$rel" = ".processed" ] && continue
  case "$rel" in processed/*) continue ;; esac
  if ! grep -qxF -- "$rel" "$MANIFEST"; then
    unprocessed+=("$rel")
  fi
done < <(find "$RAW" -type f -print0 | sort -z)

if [ ${#unprocessed[@]} -eq 0 ]; then
  echo "EMPTY: no unprocessed items in raw/"
  exit 0
fi

processed_count=$(awk 'NF {n++} END {print n+0}' "$MANIFEST")

echo "Unprocessed items in raw/ (${#unprocessed[@]} new, $processed_count already processed):"
echo ""
for item in "${unprocessed[@]}"; do
  path="$RAW/$item"
  size=$(get_size "$path")
  case "$item" in
    *.png|*.jpg|*.jpeg|*.gif|*.webp)
      echo "  $item  [image, ${size} bytes]"
      ;;
    *.pdf)
      echo "  $item  [PDF, ${size} bytes]"
      ;;
    *)
      lines=$(wc -l < "$path" 2>/dev/null || echo 0)
      echo "  $item  [${lines} lines, ${size} bytes]"
      ;;
  esac
done

echo ""
echo "To process: Read each file, decide the action (update existing node /"
echo "create new node / skip), then append the relative path to:"
echo "  $MANIFEST"
echo "(one path per line; existing Edit(memory/**) permission covers the"
echo "append, no new rule needed)."
