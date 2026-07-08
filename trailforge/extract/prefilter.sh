#!/usr/bin/env bash
# trailforge prefilter — planet/extract PBF -> hiking-relevant subset.
#
# The 16 GB homelab strategy: NEVER load the planet into RAM or a DB.
# osmium tags-filter STREAMS the ~80 GB planet and emits only the objects
# we care about; the result (a few GB) is what every downstream step —
# assembly, AOI loops, golden verification — operates on.
#
# TAG SET v0 (SPEC.md refines after research). Differences vs Systems 1/2
# are deliberate and are THE fix for the Devils Bridge class of bug:
#   + highway=steps        (Devils Bridge's final staircase; Grouse Grind)
#   + highway=via_ferrata  (Half Dome cables / chained scrambles)
#   + highway=pedestrian   (trailhead plazas that connect trails)
#   + destination POI NODES (peaks, arches, waterfalls, viewpoints, huts,
#     passes, trailheads) — trail assembly needs to know where the payoff
#     is; Systems 1/2 never fetched these at all.
#
# osmium tags-filter includes objects REFERENCED by matches by default
# (nodes of matched ways, members of matched relations), so route
# relations bring their member ways along even when a member way wouldn't
# match the highway filter on its own.
set -euo pipefail

IN="${1:?input .osm.pbf (planet or extract)}"
OUT="${2:-data/hiking.osm.pbf}"

mkdir -p "$(dirname "$OUT")"

osmium tags-filter "$IN" \
  w/highway=path,footway,steps,track,bridleway,via_ferrata,pedestrian \
  "w/abandoned:highway" \
  r/route=hiking,foot,walking,running \
  wr/boundary=protected_area,national_park \
  wr/leisure=nature_reserve \
  wr/landuse=forest \
  n/natural=peak,arch,saddle,cliff,rock,stone \
  n/waterway=waterfall \
  n/tourism=viewpoint,alpine_hut,wilderness_hut \
  n/mountain_pass=yes \
  n/highway=trailhead \
  -o "$OUT" --overwrite

osmium fileinfo -e "$OUT" | grep -E "Number of|Bounding" || true
echo "prefilter: $IN -> $OUT"
