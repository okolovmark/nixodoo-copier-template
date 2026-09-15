#!/usr/bin/env bash
# Allocate the next state.md item ID(s) atomically.
#
# Usage:
#   bash next_id.sh          — print one fresh ID
#   bash next_id.sh 3        — print three consecutive fresh IDs, one per line
#
# Replaces the read-then-write pair (`cat state_counter` … `echo N > state_counter`)
# the behavior protocol used to document. That pair is not atomic, and two Claude
# Code sessions in one project fall into the gap: both read the same value and both
# file an item under the same ID, which then has to be renumbered by hand. The read
# and the increment happen here under one lock, so a concurrent caller waits and
# gets the next number instead of the same one.
#
# The counter is rebuilt from state.md + the journal when it is missing or
# corrupted (the "Lost ID counter" recovery of the protocol), so a fresh memory
# dir and a damaged one both work without a manual step.
set -euo pipefail

MEM="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COUNTER="$MEM/state_counter"
LOCK="$MEM/.state_counter.lock"
STATE="$MEM/state.md"
JOURNAL="$MEM/journal"

COUNT="${1:-1}"
[[ "$COUNT" =~ ^[0-9]+$ ]] && [ "$COUNT" -ge 1 ] || {
  echo "next_id.sh: how many IDs? got '$COUNT', want a positive integer" >&2
  exit 2
}

# Highest ID ever seen, across both live sources. Used only when the counter
# file itself is unusable — never to hand out IDs, because a deleted max-ID item
# would make max() reissue an ID the journal still describes.
rebuild_from_sources() {
  local max_state max_journal
  max_state=$(grep -ohE '\[[0-9]+\]' "$STATE" 2>/dev/null | tr -d '[]' | sort -n | tail -1)
  max_journal=$(grep -rhoE '\[[0-9]+\]' "$JOURNAL" 2>/dev/null | tr -d '[]' | sort -n | tail -1)
  printf '%s\n%s\n0\n' "${max_state:-0}" "${max_journal:-0}" | sort -n | tail -1
}

allocate() {
  local cur next
  cur=$(grep -oE '^[0-9]+$' "$COUNTER" 2>/dev/null | head -1 || true)
  if [ -z "$cur" ]; then
    cur=$(( $(rebuild_from_sources) + 1 ))
    echo "next_id.sh: state_counter was missing or corrupt, rebuilt to $cur" >&2
  fi
  next=$(( cur + COUNT ))
  # Write the new value BEFORE printing: a caller that dies mid-print then
  # burns IDs (harmless, they are only required to be monotonic) instead of
  # handing the same one out twice.
  printf '%s\n' "$next" > "$COUNTER"
  seq "$cur" $(( next - 1 ))
}

# NEXT_ID_NO_FLOCK=1 forces the mkdir path — the only way to exercise it on a
# machine that has flock.
if [ -z "${NEXT_ID_NO_FLOCK:-}" ] && command -v flock >/dev/null 2>&1; then
  exec 9>"$LOCK"
  flock -w 15 9 || {
    echo "next_id.sh: could not take $LOCK within 15s — another session is holding it" >&2
    exit 1
  }
  allocate
  # fd 9 closes with the process, releasing the lock
else
  # No flock (macOS ships without it). `mkdir` is atomic on POSIX filesystems,
  # so a lock directory gives the same mutual exclusion.
  LOCKDIR="$LOCK.d"
  for _ in $(seq 1 150); do
    if mkdir "$LOCKDIR" 2>/dev/null; then
      trap 'rmdir "$LOCKDIR" 2>/dev/null || true' EXIT
      allocate
      exit 0
    fi
    sleep 0.1
  done
  echo "next_id.sh: could not take $LOCKDIR within 15s — another session is holding it" >&2
  exit 1
fi
