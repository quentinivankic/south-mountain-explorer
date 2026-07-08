#!/usr/bin/env python3
"""Download any region's OSM extract from Geofabrik, driven by config.

Global replacement for the NZ-only osm_nz.py: reads `geofabrik_extract`
for --region from config/regions.json and fetches

    https://download.geofabrik.de/<extract>-latest.osm.pbf

to raw/osm/<region>.osm.pbf. Adding a country is then just a config row —
no new downloader. Geofabrik regional .pbf only, never the runtime
Overpass API (spec §0).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from common import download, raw_dir

GEOFABRIK = "https://download.geofabrik.de/{extract}-latest.osm.pbf"
DEFAULT_CFG = Path(__file__).resolve().parents[2] / "config" / "regions.json"


def assert_pbf(path: Path) -> None:
    """Fail loudly if `path` isn't an OSM PBF.

    Geofabrik serves an HTML page (with a 200) for a bad slug, so a wrong
    geofabrik_extract otherwise slips through as a tiny file and only blows
    up ~40 lines later inside osmium ("invalid BlobHeader size"). A real
    .osm.pbf begins with a header blob whose type string "OSMHeader" sits
    in the first bytes; if it's missing, surface the slug problem here.
    """
    with path.open("rb") as fh:
        head = fh.read(128)
    if b"OSMHeader" not in head:
        size = path.stat().st_size
        snippet = head[:100].decode("utf-8", "replace").replace("\n", " ")
        raise SystemExit(
            f"ERROR: {path.name} is not a valid OSM PBF ({size:,} bytes). "
            f"Geofabrik probably served an error/HTML page — check the "
            f"geofabrik_extract slug for this region.\n  first bytes: {snippet!r}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch a region's OSM extract (Geofabrik)")
    ap.add_argument("--region", required=True, help="region id in config/regions.json")
    ap.add_argument("--config", default=str(DEFAULT_CFG))
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as fh:
        regions = json.load(fh).get("regions", {})
    if args.region not in regions:
        raise SystemExit(f"unknown region '{args.region}' — add it to {args.config}")
    extract = regions[args.region].get("geofabrik_extract")
    if not extract:
        raise SystemExit(f"region '{args.region}' has no geofabrik_extract in {args.config}")

    url = GEOFABRIK.format(extract=extract)
    dest = raw_dir("osm") / f"{args.region}.osm.pbf"
    download(url, dest, force=args.force)
    assert_pbf(dest)
    print(f"OSM {args.region} extract ({extract}) at {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
