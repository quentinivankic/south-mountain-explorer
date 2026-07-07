#!/usr/bin/env python3
"""Download LINZ boundary/basemap layers (spec §3.3, registry nz_linz).

LINZ Data Service serves via WFS/exports gated behind a free API key.
Key is read from $LINZ_API_KEY (never committed). The specific layer id
is a VERIFY item — pass --layer or set LINZ_LAYER (default is the NZ
protected-areas / conservation boundary layer commonly used).

For the NZ pilot, DOC PCL already supplies conservation boundaries, so
LINZ is optional/secondary here (basemap + cross-check). This downloader
exists so the source is wired and licence-attributed; skip with make
LINZ_SKIP=1 if you only need DOC.
"""
from __future__ import annotations

import argparse
import os

from common import download, env_key, raw_dir

# LINZ WFS GetFeature -> GeoJSON. Layer id is a VERIFY item.
DEFAULT_LAYER = "layer-53564"  # placeholder; VERIFY the exact LINZ layer id at build time
WFS = ("https://data.linz.govt.nz/services;key={key}/wfs?service=WFS&version=2.0.0"
       "&request=GetFeature&typeNames={layer}&outputFormat=application/json&srsName=EPSG:4326")


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch a LINZ layer (WFS GeoJSON)")
    ap.add_argument("--layer", default=os.environ.get("LINZ_LAYER", DEFAULT_LAYER))
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    key = env_key("LINZ_API_KEY", source_id="nz_linz")
    url = WFS.format(key=key, layer=args.layer)
    dest = raw_dir("nz_linz") / f"{args.layer}.geojson"
    download(url, dest, force=args.force)
    print(f"LINZ layer {args.layer} at {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
