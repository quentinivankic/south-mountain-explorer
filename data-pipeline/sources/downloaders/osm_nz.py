#!/usr/bin/env python3
"""Download the OSM New Zealand extract from Geofabrik (spec §0, §3.1).

Geofabrik regional .osm.pbf only — NEVER the runtime Overpass API. The
extract is filtered to trail ways + area polygons downstream by osmium /
build/trails.sql.
"""
from __future__ import annotations

import argparse

from common import download, raw_dir

GEOFABRIK = "https://download.geofabrik.de/australia-oceania/new-zealand-latest.osm.pbf"


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch OSM NZ extract (Geofabrik)")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    dest = raw_dir("osm") / "new-zealand-latest.osm.pbf"
    download(GEOFABRIK, dest, force=args.force)
    print(f"OSM NZ extract at {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
