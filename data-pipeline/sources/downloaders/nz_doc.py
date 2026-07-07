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
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from common import raw_dir

# Public DOC ArcGIS layers, verified against the DOC Open Spatial Data
# hub (org 3JjYDyG3oajxU6HO). Tracks live on layer 1 of the Walking
# Experiences service; PCL on layer 0 of its own. Override via
# DOC_TRACKS_URL / DOC_PCL_URL if DOC's April-2026 platform migration
# moves them.
DEFAULT_TRACKS = ("https://services1.arcgis.com/3JjYDyG3oajxU6HO/arcgis/rest/services/"
                  "DOC_Walking_Experiences/FeatureServer/1/query")
DEFAULT_PCL = ("https://services1.arcgis.com/3JjYDyG3oajxU6HO/arcgis/rest/services/"
               "DOC_Public_Conservation_Land/FeatureServer/0/query")
PAGE = 2000


def _normalize(base_url: str) -> str:
    """Accept whatever you copy from the DOC hub's API panel: a bare
    `.../FeatureServer/<n>` layer URL, a `.../query` endpoint, or either
    with a `?outFields=…&where=…` query string already appended. We
    always drive our own params, so strip any existing query string and
    ensure the path ends in `/query`."""
    u = base_url.split("?", 1)[0].rstrip("/")
    return u if u.endswith("query") else u + "/query"


# Hard cap on pages: if a server ignores `resultOffset` (no pagination
# support) it returns a full page forever — bail instead of looping.
MAX_PAGES = 500


def fetch_arcgis_geojson(base_url: str, dest: Path, *, force: bool) -> Path:
    if dest.exists() and not force:
        print(f"  [skip] {dest.name} present ({dest.stat().st_size:,} bytes)")
        return dest
    query_url = _normalize(base_url)
    features: list = []
    offset = 0
    for _ in range(MAX_PAGES):
        params = {
            "where": "1=1", "outFields": "*", "outSR": "4326",
            "f": "geojson", "resultOffset": offset, "resultRecordCount": PAGE,
        }
        url = query_url + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": "trekdex-pipeline/0.1"})
        try:
            with urllib.request.urlopen(req) as resp:
                page = json.load(resp)
        except urllib.error.HTTPError as exc:
            # ArcGIS returns a JSON error body explaining WHY (bad layer,
            # bad param, etc.) — surface it so a wrong URL is obvious.
            body = ""
            try:
                body = exc.read().decode("utf-8", "replace")[:500]
            except Exception:  # noqa: BLE001
                pass
            sys.stderr.write(
                f"ERROR fetching DOC layer {query_url} @ offset {offset}: {exc}\n"
                f"  server said: {body}\n")
            raise SystemExit(4)
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"ERROR fetching DOC layer {query_url} @ offset {offset}: {exc}\n")
            raise SystemExit(4)
        # An ArcGIS error can arrive as HTTP 200 with an {"error": ...} body.
        if isinstance(page, dict) and "error" in page:
            sys.stderr.write(f"ERROR DOC layer {query_url}: {json.dumps(page['error'])[:500]}\n")
            raise SystemExit(4)
        batch = page.get("features", [])
        features.extend(batch)
        print(f"  [page] +{len(batch)} (total {len(features)})")
        # Paginate on ArcGIS's `exceededTransferLimit`, not `len < PAGE`:
        # the service caps a page at its own maxRecordCount (PCL returned
        # exactly 1000 for a 2000 request), so a short page can still have
        # more behind it. Advance by the ACTUAL returned count so a
        # sub-PAGE cap doesn't skip records.
        exceeded = page.get("exceededTransferLimit") \
            or (page.get("properties") or {}).get("exceededTransferLimit")
        if not batch or (not exceeded and len(batch) < PAGE):
            break
        offset += len(batch)
    dest.write_text(json.dumps({"type": "FeatureCollection", "features": features}),
                    encoding="utf-8")
    print(f"  [ok  ] {dest.name} ({len(features)} features)")
    return dest


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch NZ DOC tracks + PCL boundaries")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    d = raw_dir("nz_doc")
    # `or` (not get's default): the workflow passes DOC_TRACKS_URL="" when
    # the override input is blank, and get() returns that empty string —
    # which normalized to a hostless "/query". Empty must fall through.
    tracks_url = os.environ.get("DOC_TRACKS_URL") or DEFAULT_TRACKS
    pcl_url = os.environ.get("DOC_PCL_URL") or DEFAULT_PCL
    fetch_arcgis_geojson(tracks_url, d / "doc_tracks.geojson", force=args.force)
    fetch_arcgis_geojson(pcl_url, d / "doc_pcl.geojson", force=args.force)
    print(f"DOC data in {d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
