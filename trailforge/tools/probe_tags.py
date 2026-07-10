#!/usr/bin/env python3
"""Diagnostic: dump the OSM tags of ways whose name matches a sample set.

Used to design content filters from real data when the sandbox can't reach
Overpass. Reads a prefiltered hiking PBF and prints every tag on each way that
carries one of the sample names, plus a histogram of tag KEYS across all
matches — so a distinguishing marker (e.g. mtb:scale, oneway) is obvious.

    python3 tools/probe_tags.py data/hiking.osm.pbf
"""
from __future__ import annotations

import collections
import sys

import osmium

# Bike-park / ski names that slipped past _is_nonhiking in the Colorado audit.
SAMPLE = {
    "Gluteus Minimus", "Holy Diver", "Holy Roller", "Dual Slalom Flow",
    "Cool Whip", "Banzai Donwhill", "Helter Skelter", "High Speed Dirt",
    "Jam Rock", "Kindwinder", "Milky Way", "Toad Alley", "Suz's Cruise",
    "Lift 8 Tower", "Peak 8 Road", "Snowflake Access", "Lionshead Access",
    "Uphill Skinning Egress (No Hiking)", "Pedal Up Power Down", "Papa Smurf",
}


class Probe(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.keys = collections.Counter()
        self.hits = 0

    def way(self, w):
        name = w.tags.get("name")
        if name not in SAMPLE:
            return
        self.hits += 1
        print(f"--- {name!r} (way {w.id})")
        for t in w.tags:
            print(f"      {t.k} = {t.v}")
            self.keys[t.k] += 1


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "data/hiking.osm.pbf"
    p = Probe()
    p.apply_file(path)
    print(f"\n=== {p.hits} sample ways matched; tag-key frequency ===")
    for k, n in p.keys.most_common():
        print(f"  {n:>3}  {k}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
