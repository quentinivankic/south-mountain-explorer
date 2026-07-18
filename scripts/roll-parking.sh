#!/usr/bin/env bash
# Resilient nationwide parking roll driver (homelab — needs Overpass egress).
#
# add-parking.py fetches OSM parking + boundaries + federal sources + road data
# per state, so a 50-state run against the flaky public Overpass will hit 504s.
# This runs states one at a time, retries each a few times with backoff, keeps
# going when one state fails, and prints a per-state summary so you can eyeball
# the federal-fill counts before committing.
#
# Usage:
#   scripts/roll-parking.sh az ut nv            # dry-run these states
#   scripts/roll-parking.sh --write az ut nv    # REAL write
#   scripts/roll-parking.sh                     # dry-run a default 8-state batch
#
# Cadence (matches trailforge publish): dry-run a batch -> eyeball the summary
# -> re-run with --write -> review the geom diff -> commit + push (R2 syncs).
# Keep batches small (~5-8 states); Overpass punishes long runs.
set -uo pipefail
cd "$(dirname "$0")/.."

DRY="--dry-run"
if [ "${1:-}" = "--write" ]; then DRY=""; shift; fi

STATES="$*"
if [ -z "$STATES" ]; then
  STATES="az ut nv nm co wy id mt"   # a default western/BLM-heavy batch
fi

LOG="roll-parking.log"
: > "$LOG"
echo "roll: mode=${DRY:-WRITE} states: $STATES"

for st in $STATES; do
  ok=0
  for attempt in 1 2 3; do
    echo "===== $st (attempt $attempt) ====="
    out="$(python3 scripts/add-parking.py --state "$st" $DRY 2>&1)"
    rc=$?
    echo "$out" | grep -E "boundaries loaded|federal road gate|federal fill added|GOLDEN|FAIL|would write|wrote parking"
    if [ $rc -eq 0 ]; then ok=1; break; fi
    echo "  $st failed (rc=$rc); backing off 60s"; sleep 60
  done
  gate="$(echo "${out:-}" | grep -oE 'federal road gate: kept [0-9]+/[0-9]+' | tail -1)"
  fill="$(echo "${out:-}" | grep -oE 'federal fill added [0-9]+ pin.* to [0-9]+ of [0-9]+' | tail -1)"
  if [ $ok -eq 1 ]; then
    echo "$st OK    | $gate | $fill" | tee -a "$LOG"
  else
    echo "$st FAIL  (retries exhausted — rerun this state later)" | tee -a "$LOG"
  fi
done

echo
echo "===== ROLL SUMMARY ($LOG) ====="
cat "$LOG"
echo
echo "Next: eyeball the fills above. If clean, re-run with --write, then review"
echo "the geom diff (git diff --stat public/areas/geom/) and commit + push."
