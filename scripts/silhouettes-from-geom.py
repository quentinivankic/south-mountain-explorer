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


def silhouette_from_geom(geom: dict, max_trails=SILHOUETTE_MAX_TRAILS) -> dict | None:
    """Build a `{b, l}` silhouette from one area's geom dict, or None if the
    area has no drawable geometry. Longest trails first, optionally capped at
    `max_trails` (None = no cap), re-downsampled at the coarser silhouette
    spacing."""
    trails = geom.get("trails") or []
    if not trails:
        return None
    # Longest-first, matching finalize_area's silhouette ordering, then cap.
    ordered = sorted(trails, key=lambda t: -(t.get("distanceMi") or 0.0))
    ordered = ordered[:max_trails]

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
    ap.add_argument("--all", action="store_true",
                    help="regenerate EVERY area's silhouette from its geom, "
                         "including System-1 (cached_at) areas. Default only "
                         "touches trailforge-clean geom (the bundle-filter gate).")
    ap.add_argument("--index-only", action="store_true", default=True,
                    help="only areas present in the master index (the ones the "
                         "app actually shows). On by default.")
    ap.add_argument("--max-trails", type=int, default=None,
                    help="cap trails per silhouette (default: the "
                         "SILHOUETTE_MAX_TRAILS constant, currently uncapped). "
                         "Pass a number to draw only the N longest trails.")
    args = ap.parse_args(argv)

    max_trails = args.max_trails if args.max_trails is not None else SILHOUETTE_MAX_TRAILS

    # The app only ever renders index areas; skip the ~10k legacy geom files
    # that have no index row so we don't emit orphan silhouettes.
    import os
    index = json.load(open(os.path.join(
        GEOM_DIR.parent, "index.json")))
    index_slugs = {r[0] for r in index if r}

    wrote = changed = skipped_cached = skipped_nonindex = empty = 0
    for geom_path in sorted(GEOM_DIR.glob("*.json")):
        try:
            geom = json.loads(geom_path.read_text())
        except Exception:
            continue
        if not isinstance(geom, dict):
            continue
        area_id = geom.get("id") or geom_path.stem
        if area_id not in index_slugs:
            skipped_nonindex += 1
            continue
        # By default only trailforge-clean geom (no cached_at) — same gate as
        # the bundle filter. --all also regenerates System-1 areas.
        if geom.get("cached_at") is not None and not args.all:
            skipped_cached += 1
            continue
        sil = silhouette_from_geom(geom, max_trails=max_trails)
        if sil is None:
            empty += 1
            continue
        sil_path = GEOM_DIR.parent / "silhouettes" / f"{area_id}.json"
        prior = None
        if sil_path.exists():
            try:
                prior = json.loads(sil_path.read_text())
            except Exception:
                prior = None
        if prior != sil:
            changed += 1
        if not args.dry_run:
            write_silhouette(area_id, sil)
        wrote += 1

    verb = "would write" if args.dry_run else "wrote"
    scope = "ALL index areas" if args.all else "trailforge-clean index areas"
    print(f"{verb} {wrote} silhouettes ({scope}); {changed} differ from the "
          f"current file; skipped {skipped_cached} System-1 (cached_at, "
          f"use --all to include), {skipped_nonindex} non-index geom, "
          f"{empty} with no drawable geometry")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
