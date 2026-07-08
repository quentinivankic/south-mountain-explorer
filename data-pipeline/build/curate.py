#!/usr/bin/env python3
"""Select the SHIPPED trail set — the "baked" curation mode (spec §4/§8).

The confidence score is a curation POLICY, not tile data (§4). This applies
it once at BUILD time to choose which trails ship: keep the high + medium
bands, drop low. The score itself is NEVER written into the tiles — only
the *selection* it drives is. This is exactly the `baked` curation_mode
build_tiles.sh documents (the caller pre-filters trails.geojson).

It also re-emits the areas index over the CURATED set, so a park's trail
count reflects what actually ships — an area left with only anonymous
path-fragments (all `low`) drops out entirely, which is the point.

Scored with build/scoring_reference.py + weights.default.json, the same
policy the on-device authoring lab uses, so build-time curation and the
lab never disagree.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scoring_reference as sr  # noqa: E402
from assign_areas import _area_id, _miles  # noqa: E402

DEFAULT_KEEP = ("high", "medium")


def curate(trails_fc: dict[str, Any], areas_fc: dict[str, Any],
           weights: dict[str, Any] | None = None,
           keep: tuple[str, ...] = DEFAULT_KEEP) -> tuple[dict[str, Any], dict[str, Any], dict[str, int]]:
    """Return (curated trails FC, curated areas index, band counts)."""
    from shapely.geometry import shape

    w = weights or sr.load_weights()
    keep_set = set(keep)

    kept, counts = [], {"high": 0, "medium": 0, "low": 0}
    for f in trails_fc.get("features", []):
        band = sr.score_and_band(f.get("properties", {}) or {}, w)[1]
        counts[band] = counts.get(band, 0) + 1
        if band in keep_set:
            kept.append(f)
    curated = {"type": "FeatureCollection", "features": kept}

    # Areas index over the CURATED trails only.
    meta: dict[str, dict[str, Any]] = {}
    for af in areas_fc.get("features", []):
        p = af.get("properties", {}) or {}
        aid = _area_id(p)
        meta[aid] = {"area_id": aid, "name": p.get("name"),
                     "authority_rank": p.get("authority_rank"),
                     "trail_count": 0, "total_mi": 0.0}
    for f in kept:
        mi = _miles(shape(f["geometry"]))
        for aid in f.get("properties", {}).get("area_ids") or []:
            if aid in meta:
                meta[aid]["trail_count"] += 1
                meta[aid]["total_mi"] = round(meta[aid]["total_mi"] + mi, 2)
    index = sorted((s for s in meta.values() if s["trail_count"] > 0),
                   key=lambda s: -s["trail_count"])
    return curated, {"areas": index}, counts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Curate the shipped trail set (baked high+medium)")
    ap.add_argument("--trails", required=True, help="trails GeoJSON with area_ids + flags")
    ap.add_argument("--areas", required=True)
    ap.add_argument("--trails-out", required=True, help="curated trails GeoJSON (what tiles)")
    ap.add_argument("--index-out", required=True, help="curated areas index JSON")
    ap.add_argument("--keep", nargs="+", default=list(DEFAULT_KEEP),
                    choices=["high", "medium", "low"])
    ap.add_argument("--weights", help="weights JSON (default weights.default.json)")
    args = ap.parse_args(argv)

    with open(args.trails, encoding="utf-8") as fh:
        trails = json.load(fh)
    with open(args.areas, encoding="utf-8") as fh:
        areas = json.load(fh)
    weights = sr.load_weights(args.weights) if args.weights else None

    curated, index, counts = curate(trails, areas, weights, tuple(args.keep))
    Path(args.trails_out).write_text(json.dumps(curated), encoding="utf-8")
    Path(args.index_out).write_text(json.dumps(index, indent=2), encoding="utf-8")

    total = sum(counts.values())
    kept = len(curated["features"])
    print(f"curated: kept {kept:,}/{total:,} trails "
          f"(high {counts['high']:,} + medium {counts['medium']:,}; "
          f"dropped low {counts['low']:,}); {len(index['areas']):,} areas ship",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
