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
#   bash trailforge/extract/fetch-us-extract.sh            # fetch/refresh (full 12 GB)
#   bash trailforge/extract/fetch-us-extract.sh --check    # report age only
#   bash trailforge/extract/fetch-us-extract.sh --update   # apply daily diffs instead
#
# WHY --update EXISTS. The full extract is 12 GB and Geofabrik publishes a daily
# diff per region. Nine days stale cost 7 diffs of a few hundred MB rather than a
# 12 GB re-download, so a weekly refresh becomes cheap enough to actually do —
# and a stale extract is how you get audits that quietly describe last month's
# data.
#
# NOT pyosmium, despite the task title. `pyosmium-up-to-date` is the purpose-built
# tool, but pyosmium here is a partial system install with no `osmium.replication`
# module and pip refuses to touch it (PEP 668 externally-managed). The osmium CLI
# 1.16 is the one OSM tool that has never let this project down, and it can apply
# changes directly, so --update is built on `osmium apply-changes` plus curl. One
# fewer dependency, and the diff URL layout is verified below.
set -euo pipefail

DEST_DIR="${TREKDEX_OSM_DIR:-/mnt/raid/trekdex/osm}"
NAME="us-latest.osm.pbf"
URL="https://download.geofabrik.de/north-america/${NAME}"
DEST="${DEST_DIR}/${NAME}"

human() { numfmt --to=iec --suffix=B "$1" 2>/dev/null || echo "$1 bytes"; }

hdr() { osmium fileinfo -g "header.option.$1" "$2" 2>/dev/null | head -1; }

# Regenerating the derived extracts is NOT optional. `us-hiking` and `us-access`
# are cut FROM `us-latest`; leaving them behind after an update means a trail
# audit and a parking audit silently describe different days. Same failure family
# as the stale-geom cache bug (#424) and the publish that reported success while
# doing nothing — the numbers look fine and disagree with reality.
regen_derived() {
  local src="$1"
  local hiking="${DEST_DIR}/us-hiking.osm.pbf"
  local access="${DEST_DIR}/us-access.osm.pbf"
  local here; here="$(cd "$(dirname "$0")" && pwd)"
  echo ">> derived extracts are now stale — regenerating from the updated base"
  if [[ -x "${here}/prefilter.sh" ]]; then
    bash "${here}/prefilter.sh" "$src" "$hiking"
  else
    echo "!! ${here}/prefilter.sh missing — us-hiking.osm.pbf LEFT STALE" >&2
  fi
  if [[ -x "${here}/prefilter-access.sh" ]]; then
    bash "${here}/prefilter-access.sh" "$src" "$access"
  else
    echo "!! ${here}/prefilter-access.sh missing — us-access.osm.pbf LEFT STALE" >&2
  fi
}

if [[ "${1:-}" == "--update" ]]; then
  command -v osmium >/dev/null 2>&1 || { echo "!! osmium not on PATH" >&2; exit 1; }
  [[ -f "$DEST" ]] || { echo "!! no local extract — run without --update first" >&2; exit 1; }

  base="$(hdr osmosis_replication_base_url "$DEST")"
  have="$(hdr osmosis_replication_sequence_number "$DEST")"
  stamp="$(hdr timestamp "$DEST")"
  [[ -n "$base" && -n "$have" ]] || {
    echo "!! this PBF carries no replication header — full re-download required" >&2
    exit 1; }
  echo ">> local  : sequence $have  ($stamp)"

  want="$(curl -fsS --max-time 60 "${base}/state.txt" \
          | sed -n 's/^sequenceNumber=//p' | tr -d '\r')"
  [[ -n "$want" ]] || { echo "!! could not read upstream state.txt" >&2; exit 1; }
  echo ">> remote : sequence $want"

  if (( want <= have )); then
    echo ">> already current — nothing to do"
    exit 0
  fi
  echo ">> applying $(( want - have )) daily diff(s)"

  work="$(mktemp -d "${DEST_DIR}/.upd.XXXXXX")"
  # A partial update must never land on the working extract: apply into the temp
  # dir, verify, then move. An interrupted run leaves the 12 GB base untouched.
  trap 'rm -rf "$work"' EXIT

  diffs=()
  for (( seq = have + 1; seq <= want; seq++ )); do
    d="$(printf '%09d' "$seq")"
    url="${base}/${d:0:3}/${d:3:3}/${d:6:3}.osc.gz"
    out="${work}/${d}.osc.gz"
    echo "   fetching seq ${seq}"
    curl -fL --retry 6 --retry-delay 5 --retry-all-errors --max-time 900 \
         -o "$out" "$url"
    # Verify each diff decompresses. A truncated .osc.gz that osmium then reads
    # partially is exactly the silent-corruption case this file already warns
    # about for the full download.
    gzip -t "$out" || { echo "!! seq ${seq} is not valid gzip" >&2; exit 1; }
    [[ -s "$out" ]] || { echo "!! seq ${seq} is empty" >&2; exit 1; }
    diffs+=("$out")
  done

  # The replication headers must be written EXPLICITLY. `osmium apply-changes`
  # applies the edits correctly but does not carry the replication bookkeeping
  # into the output header, so the result has no sequence number — and a PBF with
  # no sequence number can never be incrementally updated again, only
  # re-downloaded whole. This is the bookkeeping `pyosmium-up-to-date` exists to
  # do. The verification below caught exactly this on the first real run: the
  # apply was fine (+0.12% nodes, a plausible week) and the header was empty.
  want_stamp="$(curl -fsS --max-time 60 \
      "${base}/$(printf '%09d' "$want" | sed 's|\(...\)\(...\)\(...\)|\1/\2/\3|').state.txt" \
      | sed -n 's/^timestamp=//p' | tr -d '\r' | sed 's|\\||g')"
  [[ -n "$want_stamp" ]] || { echo "!! no timestamp for seq $want" >&2; exit 1; }

  newpbf="${work}/updated.osm.pbf"
  echo ">> osmium apply-changes (${#diffs[@]} change file(s)) -> seq $want ($want_stamp)"
  osmium apply-changes "$DEST" "${diffs[@]}" -o "$newpbf" --overwrite \
    --output-header="osmosis_replication_base_url=$base" \
    --output-header="osmosis_replication_sequence_number=$want" \
    --output-header="osmosis_replication_timestamp=$want_stamp" \
    --output-header="timestamp=$want_stamp"

  # VERIFY THE OUTPUT, NOT THE EXIT CODE.
  got_seq="$(hdr osmosis_replication_sequence_number "$newpbf")"
  got_stamp="$(hdr timestamp "$newpbf")"
  old_nodes="$(osmium fileinfo -e -g data.count.nodes "$DEST" 2>/dev/null | head -1)"
  new_nodes="$(osmium fileinfo -e -g data.count.nodes "$newpbf" 2>/dev/null | head -1)"
  echo ">> result : sequence ${got_seq:-?}  (${got_stamp:-?})"
  echo ">> nodes  : ${old_nodes} -> ${new_nodes}"
  if [[ "${got_seq:-0}" != "$want" ]]; then
    echo "!! sequence did not advance to $want — refusing to replace" >&2
    echo "!! inspect ${newpbf}" >&2
    trap - EXIT
    exit 1
  fi
  # A real week of edits moves the node count by a fraction of a percent. A big
  # swing means the apply went wrong, not that America changed.
  if [[ -n "$old_nodes" && -n "$new_nodes" ]] \
     && ! awk -v a="$old_nodes" -v b="$new_nodes" \
              'BEGIN{d=(b-a)/a; exit !(d>-0.02 && d<0.05)}'; then
    echo "!! node count moved more than expected (${old_nodes} -> ${new_nodes})" >&2
    echo "!! refusing to replace; inspect ${newpbf}" >&2
    trap - EXIT
    exit 1
  fi

  mv -f "$newpbf" "$DEST"
  rm -rf "$work"
  trap - EXIT
  echo ">> OK: $DEST now at sequence $want ($(human "$(stat -c%s "$DEST")"))"
  regen_derived "$DEST"
  exit 0
fi

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
