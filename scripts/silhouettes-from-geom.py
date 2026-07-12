#!/usr/bin/env python3
"""Regenerate area silhouettes FROM the published trailforge geom.

The Explore cards and Browse thumbnails render a lightweight "silhouette"
(`public/areas/silhouettes/<slug>.json`) — a downsampled polyline sketch of an
area's trail network. Historically these were built by the System-1 seeder
(`build-trail-counts.py`) straight from Overpass, so they show the OLD,
uncurated network: every road/utility/junk way trailforge now filters out is
still drawn. After a trailforge publish the geom is clean but the silhouette is
stale — Acadia ships 156 clean geom trails but a 402-line silhouette.

This script closes that gap by deriving each silhouette from the geom that
actually ships. It only touches areas whose geom is trailforge-clean (no
`cached_at` — the same gate `filter-ios-bundle.py` uses); System-1 areas keep
their matching System-1 silhouettes until trailforge publishes them too.

The geom→silhouette transform mirrors `finalize_area`'s silhouette tail: map
the full difficulty label back to its single char, sort trails longest-first,
cap at SILHOUETTE_MAX_TRAILS, re-downsample each segment at the coarser
silhouette spacing, and recompute the bbox. Reusing the geom (already
assembled + clipped + curated) means the silhouette can never disagree with the
trails the app draws.

    python3 scripts/silhouettes-from-geom.py            # write
    python3 scripts/silhouettes-from-geom.py --dry-run  # report only

Idempotent: re-running over unchanged geom reproduces byte-identical files.
"""
from __future__ import annotations

import argparse
import json

from _seed_constants import (
    GEOM_DIR,
    SILHOUETTE_DECIMALS,
    SILHOUETTE_MAX_TRAILS,
    SILHOUETTE_SPACING_M,
    _downsample,
    write_silhouette,
)

# Geom stores the full iOS label; silhouettes store the single char. Inverse of
# _seed_constants._difficulty_label.
_DIFF_CODE = {"Easy": "e", "Moderate": "m", "Hard": "h"}


def silhouette_from_geom(geom: dict) -> dict | None:
    """Build a `{b, l}` silhouette from one area's geom dict, or None if the
    area has no drawable geometry. Longest trails first, capped, re-downsampled
    at the coarser silhouette spacing."""
    trails = geom.get("trails") or []
    if not trails:
        return None
    # Longest-first, matching finalize_area's silhouette ordering, then cap.
    ordered = sorted(trails, key=lambda t: -(t.get("distanceMi") or 0.0))
    ordered = ordered[:SILHOUETTE_MAX_TRAILS]

    lines: list = []
    min_lat = min_lon = float("inf")
    max_lat = max_lon = float("-inf")
    for trail in ordered:
        d = _DIFF_CODE.get(trail.get("difficulty"), "e")
        for seg in trail.get("segments") or []:
            ds = _downsample(seg, SILHOUETTE_SPACING_M)
            if len(ds) < 2:
                continue
            pts = [[round(p[0], SILHOUETTE_DECIMALS),
                    round(p[1], SILHOUETTE_DECIMALS)] for p in ds]
            for la, lo in pts:
                if la < min_lat: min_lat = la
                if la > max_lat: max_lat = la
                if lo < min_lon: min_lon = lo
                if lo > max_lon: max_lon = lo
            lines.append({"d": d, "p": pts})

    if not lines:
        return None
    return {
        "b": [
            round(min_lon, SILHOUETTE_DECIMALS),
            round(min_lat, SILHOUETTE_DECIMALS),
            round(max_lon, SILHOUETTE_DECIMALS),
            round(max_lat, SILHOUETTE_DECIMALS),
        ],
        "l": lines,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="regenerate silhouettes from trailforge-clean geom")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would change; write nothing")
    args = ap.parse_args(argv)

    wrote = skipped_cached = empty = 0
    for geom_path in sorted(GEOM_DIR.glob("*.json")):
        try:
            geom = json.loads(geom_path.read_text())
        except Exception:
            continue
        if not isinstance(geom, dict):
            continue
        # Only trailforge-clean geom (no cached_at) — same gate as the bundle
        # filter. System-1 areas keep their System-1 silhouettes.
        if geom.get("cached_at") is not None:
            skipped_cached += 1
            continue
        area_id = geom.get("id") or geom_path.stem
        sil = silhouette_from_geom(geom)
        if sil is None:
            empty += 1
            continue
        if not args.dry_run:
            write_silhouette(area_id, sil)
        wrote += 1

    verb = "would write" if args.dry_run else "wrote"
    print(f"{verb} {wrote} silhouettes from trailforge geom; "
          f"skipped {skipped_cached} System-1 areas (cached_at); "
          f"{empty} clean areas had no drawable geometry")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
