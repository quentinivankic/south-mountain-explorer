#!/usr/bin/env python3
"""Scan the prefiltered hiking PBF for the ROAD-TRACK-NAME ingest drops — the
one ingest bucket with genuine FALSE-NEGATIVE risk.

A `road-track-name` drop is a `highway=track` way kept out of the trail set
ONLY because its NAME reads road-like (a Road/Lane/Drive suffix, an FR/BLM
code, or a PLSS grid address). Most are real service roads — but a genuine
foot trail mis-tagged `highway=track` and given a road-ish name would land
here too, and unlike a stray historical-name footpath (which is correctly
KEPT), a wrongly-dropped trail is an actual MISSING trail a hiker wanted.
That's why this bucket is worth eyeballing and the rail/route name review was
not: this is the direction where real content hides.

Reuses `model.ingest_drop_reason` so it can never drift from what the pipeline
actually drops. Homelab-only (needs the prefiltered pbf + pyosmium). Emits
GeoJSON (with the OSM way id per feature) for road-track-review.html.

    # after `make prefilter` (data/hiking.osm.pbf exists):
    python3 trailforge/viewer/scan-road-track-drops.py \\
        --in trailforge/data/hiking.osm.pbf
    python3 trailforge/viewer/serve-review.py    # then open road-track-review.html
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "assemble"))
import model  # noqa: E402

_DEFAULT_IN = os.path.join(_HERE, "..", "data", "hiking.osm.pbf")
_DEFAULT_OUT = os.path.join(_HERE, "road-track-drops.geojson")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="inp", default=_DEFAULT_IN,
                    help="prefiltered hiking.osm.pbf")
    ap.add_argument("--out", default=_DEFAULT_OUT)
    args = ap.parse_args(argv)

    import osmium

    ways: dict[int, dict] = {}
    want: set[int] = set()

    class Pass1(osmium.SimpleHandler):
        def way(self, w):
            tags = {t.k: t.v for t in w.tags}
            if tags.get("highway") != "track" or not tags.get("name"):
                return                         # only NAMED tracks are reviewable
            v = model.ingest_drop_reason(tags)
            if not v or v[0] != "road-track-name":
                return                         # keep only the fuzzy NAME drops
            nds = [n.ref for n in w.nodes]
            if len(nds) < 2:
                return
            ways[w.id] = {"tags": tags, "nodes": nds}
            want.update(nds)

    Pass1().apply_file(args.inp)

    nodes: dict[int, tuple[float, float]] = {}

    class Pass2(osmium.SimpleHandler):
        def node(self, n):
            if n.id in want:
                nodes[n.id] = (n.location.lon, n.location.lat)

    Pass2().apply_file(args.inp)

    feats = []
    for wid, w in ways.items():
        coords = [nodes[n] for n in w["nodes"] if n in nodes]
        if len(coords) < 2:
            continue
        t = w["tags"]
        feats.append({
            "type": "Feature",
            "properties": {
                "name": t.get("name"),
                "osm_way": wid,
                "surface": t.get("surface", ""),
                "tracktype": t.get("tracktype", ""),
                "foot": t.get("foot", ""),
                "access": t.get("access", ""),
                "sac_scale": t.get("sac_scale", ""),
            },
            "geometry": {"type": "LineString", "coordinates": coords},
        })

    json.dump({"type": "FeatureCollection", "features": feats},
              open(args.out, "w"), separators=(",", ":"))
    print(f"{len(feats)} road-track-name drops -> {args.out}")
    print("open road-track-review.html (via serve-review.py) to eyeball them; "
          "each carries its OSM way id for a 1-click inspect/fix.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
