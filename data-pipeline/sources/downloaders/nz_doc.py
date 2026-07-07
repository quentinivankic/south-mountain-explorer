#!/usr/bin/env python3
"""Download NZ DOC tracks + Public Conservation Land (spec §3.3, registry nz_doc).

DOC publishes on an ArcGIS Hub / REST endpoint under CC-BY-4.0. We pull
GeoJSON via the ArcGIS REST `query` interface (f=geojson, paginated with
resultOffset). Two layers:
  * tracks    — authoritative trail geometry (conflation target + whitelist)
  * pcl       — Public Conservation Land boundaries (area polygons + the
                official-boundary test for the §5 phantom rule)

Layer query URLs occasionally change; they are read from env so a VERIFY
at build time can override without a code edit:
  DOC_TRACKS_URL, DOC_PCL_URL  (…/FeatureServer/<n>/query defaults below)

Attribution (fixed by registry, do not edit here):
  "Sourced from the NZ Department of Conservation (CC BY 4.0)"
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from common import raw_dir

# Public DOC ArcGIS layers (VERIFY at build time — see registry note).
DEFAULT_TRACKS = ("https://services1.arcgis.com/HAipYSwmVDgObQlm/arcgis/rest/services/"
                  "DOC_Tracks/FeatureServer/0/query")
DEFAULT_PCL = ("https://services1.arcgis.com/HAipYSwmVDgObQlm/arcgis/rest/services/"
               "Public_Conservation_Land/FeatureServer/0/query")
PAGE = 2000


def fetch_arcgis_geojson(base_url: str, dest: Path, *, force: bool) -> Path:
    if dest.exists() and not force:
        print(f"  [skip] {dest.name} present ({dest.stat().st_size:,} bytes)")
        return dest
    features: list = []
    offset = 0
    while True:
        params = {
            "where": "1=1", "outFields": "*", "outSR": "4326",
            "f": "geojson", "resultOffset": offset, "resultRecordCount": PAGE,
        }
        url = base_url + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": "trekdex-pipeline/0.1"})
        try:
            with urllib.request.urlopen(req) as resp:
                page = json.load(resp)
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"ERROR fetching DOC layer {base_url} @ offset {offset}: {exc}\n")
            raise SystemExit(4)
        batch = page.get("features", [])
        features.extend(batch)
        print(f"  [page] +{len(batch)} (total {len(features)})")
        if len(batch) < PAGE:
            break
        offset += PAGE
    dest.write_text(json.dumps({"type": "FeatureCollection", "features": features}),
                    encoding="utf-8")
    print(f"  [ok  ] {dest.name} ({len(features)} features)")
    return dest


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch NZ DOC tracks + PCL boundaries")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    d = raw_dir("nz_doc")
    fetch_arcgis_geojson(os.environ.get("DOC_TRACKS_URL", DEFAULT_TRACKS),
                         d / "doc_tracks.geojson", force=args.force)
    fetch_arcgis_geojson(os.environ.get("DOC_PCL_URL", DEFAULT_PCL),
                         d / "doc_pcl.geojson", force=args.force)
    print(f"DOC data in {d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
