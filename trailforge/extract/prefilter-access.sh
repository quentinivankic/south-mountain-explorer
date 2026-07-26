#!/usr/bin/env bash
# trailforge ACCESS prefilter — full US extract -> "how do I get there" subset.
#
# Companion to prefilter.sh, which keeps TRAILS. This keeps the things that
# answer a different question: where do you park, and where does a drivable
# road come closest to the trail.
#
# WHY A SECOND FILTER RATHER THAN WIDENING THE FIRST
# prefilter.sh's output feeds trail ASSEMBLY, where every extra object is noise
# that curation then has to reject. Roads in particular would be actively
# harmful there — the pipeline works hard to keep roads OUT of the trail set.
# Access is a separate question asked at a separate time, so it gets its own
# small extract instead.
#
# WHAT THIS ANSWERS, measured on the shipped data 2026-07-19/20:
#   * 45% of trails (41,856 of 92,360) have parking in their area but none
#     within the 805 m gate — median nearest lot 2.7 mi
#   * 12% are in an area with no parking data at all
#   * Trail 463 (Helena-Lewis and Clark NF) reported its nearest lot 34.7 mi
#     away, while OSM has a gravel road 1.2 km from it. The access point out
#     there is a road shoulder, not a car park — which no parking-only filter
#     can ever see.
#
# ALSO KEEPS tiger:* PROVENANCE. The 2007 TIGER import loaded every US road
# into OSM; where nobody cleaned it up, those ways were retagged highway=path
# and named years later, and we ingest them as trails. Prevalence is LOCAL and
# ranges 0-95% between neighbouring forests, so it has to be measured per area
# rather than assumed. Answering that over Overpass took ~70 s per area (~4 h
# for 276); over a local PBF it is one pass.
#
#   bash trailforge/extract/prefilter-access.sh \
#        /mnt/raid/trekdex/osm/us-latest.osm.pbf \
#        /mnt/raid/trekdex/osm/us-access.osm.pbf
set -euo pipefail

IN="${1:?input .osm.pbf (the full US extract)}"
OUT="${2:-/mnt/raid/trekdex/osm/us-access.osm.pbf}"

mkdir -p "$(dirname "$OUT")"

# DRIVABLE roads only — matches _DRIVABLE_HW in scripts/add-parking.py so the
# road gate and this extract agree about what counts as reachable by car.
# `track` is deliberately included: in a national forest the access IS a dirt
# road, and excluding it would drop exactly the remote cases this exists for.
osmium tags-filter "$IN" \
  w/highway=motorway,trunk,primary,secondary,tertiary,unclassified,residential,service,track \
  w/highway=motorway_link,trunk_link,primary_link,secondary_link,tertiary_link \
  wr/amenity=parking \
  n/amenity=parking \
  n/amenity=parking_entrance \
  n/highway=trailhead \
  -o "$OUT" --overwrite

echo ">> wrote $OUT"
osmium fileinfo "$OUT" | sed -n '1,10p'
