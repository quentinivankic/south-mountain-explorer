#!/usr/bin/env bash
# Fetch (or refresh) the Geofabrik US extract onto the NAS.
#
# WHY THIS EXISTS
# Every slow thing in this pipeline traces back to asking Overpass one question
# at a time. Measured 2026-07-19/20 on the homelab:
#   * TIGER provenance audit  — ~70 s PER AREA, ~4 h for 276 areas, almost all
#     of it Overpass rate-limit backoff
#   * nationwide parking roll — 57 min, dominated by Overpass
#   * whole-US publish        — one region stalled 2h42m on boundary fetches
# Holding the extract locally turns those into osmium passes. The NAS reads at
# ~104-111 MB/s over NFS, so an 11 GB scan is ~2 minutes.
#
# WHAT STILL NEEDS OVERPASS: boundary relations by id. Resolving full relation
# membership is what Overpass is genuinely good at. Everything else — trails,
# roads, parking, tiger:* tags — is a tag scan a PBF answers directly.
#
# STORAGE GOES ON THE NAS, NOT LOCAL DISK. The homelab root is ~91% full with
# under 10 GB free; this file alone is 11.2 GB. /mnt/raid has 19 TB free.
# The Pi NAS is for STORAGE ONLY — do the CPU work here and read over NFS.
# Do not try to run an Overpass instance on it: Overpass wants RAM and random
# I/O, which is the worst case for NFS-backed ARM hardware.
#
# FRESHNESS is the trade. Overpass is always current; this is a snapshot.
# Geofabrik rebuilds daily. For trail data a weekly refresh is generous — the
# underlying features change on a scale of years.
#
#   bash trailforge/extract/fetch-us-extract.sh            # fetch/refresh
#   bash trailforge/extract/fetch-us-extract.sh --check    # report age only
set -euo pipefail

DEST_DIR="${TREKDEX_OSM_DIR:-/mnt/raid/trekdex/osm}"
NAME="us-latest.osm.pbf"
URL="https://download.geofabrik.de/north-america/${NAME}"
DEST="${DEST_DIR}/${NAME}"

human() { numfmt --to=iec --suffix=B "$1" 2>/dev/null || echo "$1 bytes"; }

if [[ "${1:-}" == "--check" ]]; then
  if [[ -f "$DEST" ]]; then
    age_days=$(( ( $(date +%s) - $(stat -c %Y "$DEST") ) / 86400 ))
    echo "local : $DEST ($(human "$(stat -c%s "$DEST")")), ${age_days}d old"
  else
    echo "local : MISSING"
  fi
  remote=$(curl -sIL --max-time 60 "$URL" | awk 'BEGIN{IGNORECASE=1}/^content-length:/{n=$2} END{gsub(/\r/,"",n); print n}')
  echo "remote: $(human "${remote:-0}")"
  exit 0
fi

mkdir -p "$DEST_DIR"

# Refuse to write to local disk by accident — an 11 GB file would fill root.
avail=$(df -P "$DEST_DIR" | awk 'NR==2{print $4*1024}')
if (( avail < 20000000000 )); then
  echo "!! only $(human "$avail") free at $DEST_DIR — need ~20 GB (file is ~11 GB" \
       "plus room for a filtered copy). Refusing." >&2
  exit 1
fi

echo ">> fetching $URL"
echo ">> into    $DEST"
# -C - resumes a partial file rather than restarting an 11 GB download; the
# retry flags survive the connection drops a long transfer inevitably hits.
curl -fL --retry 8 --retry-delay 10 --retry-all-errors -C - -o "$DEST" "$URL"

# VERIFY THE OUTPUT, not the exit code. A truncated PBF still exits 0 on some
# proxy failures, and a silently-partial extract would produce quietly wrong
# answers everywhere downstream — the exact failure class that cost hours on
# 2026-07-19 (a publish step reported success while doing nothing).
got=$(stat -c%s "$DEST")
want=$(curl -sIL --max-time 60 "$URL" | awk 'BEGIN{IGNORECASE=1}/^content-length:/{n=$2} END{gsub(/\r/,"",n); print n}')
if [[ -n "$want" && "$got" != "$want" ]]; then
  echo "!! SIZE MISMATCH: got $(human "$got"), expected $(human "$want"). Not usable." >&2
  exit 1
fi

if command -v osmium >/dev/null 2>&1; then
  echo ">> verifying the PBF actually parses…"
  osmium fileinfo "$DEST" | sed -n '1,12p'
else
  echo "!! osmium not on PATH — size checked but contents unverified" >&2
fi

echo ">> OK: $(human "$got")"
echo ">> next: bash trailforge/extract/prefilter.sh $DEST <out.pbf>"
