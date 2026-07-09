#!/usr/bin/env python3
"""Batch-publish trailforge areas into the app's per-area JSON.

Uses the per-area merge we built: a statewide `--per-area-merge` run tags
every trail with its `area`, so we split that ONE run by area, clip each to
its park boundary (the DC-Ray fix), convert to the app's AreaRow/Trail shape,
VALIDATE for crash-safety, and write into public/areas/geom/ + bump the index
count — all in one pass, no 94 separate assembles.

Only areas that (a) are in the app's index for the given --state and (b) have
a boundary assembled from the PBF get published; everything else is reported
and skipped. Each output is validated (unique canonical ids — the crash that
bit #306 — valid difficulties, coords in range, non-empty); a failing area is
skipped, never shipped.

Usage:
  python3 serve/publish_areas.py \
    --trails data/aoi/arizona.trails.geojson \
    --hiking data/hiking.osm.pbf \
    --out-dir ../public/areas/geom --state Arizona [--limit N] [--dry-run] [--no-routes]
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "assemble"))
import to_app_json as conv          # noqa: E402
import areas as areamod             # noqa: E402

_DEFAULT_INDEX = os.path.join(os.path.dirname(__file__), "..", "..",
                              "ios", "SouthMountainExplorer", "Resources", "areas-index.json")


def _canonical(tid: str) -> str:
    return re.sub(r"-\d{1,3}$", "", tid or "")


def validate(row: dict) -> list[str]:
    """Crash-safety + sanity checks. Returns a list of problems (empty = ok)."""
    problems = []
    ts = row.get("trails") or []
    if not ts:
        problems.append("no trails")
    canon = [_canonical(t["id"]) for t in ts]
    dups = {c for c in canon if canon.count(c) > 1}
    if dups:
        problems.append(f"duplicate canonical ids: {sorted(dups)[:5]}")
    for t in ts:
        if t.get("difficulty") not in ("Easy", "Moderate", "Hard"):
            problems.append(f"bad difficulty on {t.get('id')}"); break
        if not t.get("segments"):
            problems.append(f"empty segments on {t.get('id')}"); break
        pt = t["segments"][0][0]
        if not (-90 <= pt[0] <= 90 and -180 <= pt[1] <= 180):
            problems.append(f"coord out of [lat,lon] range on {t.get('id')}"); break
    return problems


def _union_from_rings(rings):
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    polys = [Polygon(r) for r in rings if len(r) >= 4]
    if not polys:
        return None
    u = unary_union(polys)
    return u if u.is_valid else u.buffer(0)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="batch-publish trailforge areas -> app JSON")
    ap.add_argument("--trails", required=True, help="statewide trails.geojson (--per-area-merge)")
    ap.add_argument("--hiking", required=True, help="hiking.osm.pbf for boundary assembly")
    ap.add_argument("--index", default=_DEFAULT_INDEX)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--state", default="Arizona")
    ap.add_argument("--min-inside-mi", type=float, default=0.05)
    ap.add_argument("--no-routes", action="store_true")
    ap.add_argument("--limit", type=int, help="only publish the first N areas (a first wave)")
    ap.add_argument("--dry-run", action="store_true", help="report matches; write nothing")
    args = ap.parse_args(argv)

    index = json.load(open(args.index))
    az = {r[0]: {"name": r[1], "state": r[2], "center": (r[3], r[4]),
                 "osm_rel": r[7] if len(r) > 7 else None}
          for r in index if len(r) >= 5 and r[2] == args.state}
    print(f"index: {len(az)} '{args.state}' areas", file=sys.stderr)

    fc = json.load(open(args.trails))
    by_area: dict[str, list] = {}
    for f in fc["features"]:
        a = f["properties"].get("area")
        if a:
            by_area.setdefault(a, []).append(f)

    print("assembling park boundaries from the PBF…", file=sys.stderr)
    bnd = {b["name"].casefold(): b for b in areamod.merge_areas(args.hiking) if b.get("name")}

    kinds = {"trail", "hike"} if args.no_routes else {"trail", "hike", "route"}
    published, skipped, failed = [], [], []
    count = 0
    for slug, meta in sorted(az.items()):
        if args.limit and count >= args.limit:
            break
        feats = by_area.get(meta["name"], [])
        b = bnd.get(meta["name"].casefold())
        if not feats:
            skipped.append((slug, "no trails assigned to this area")); continue
        if not b:
            skipped.append((slug, "no boundary in PBF")); continue
        union = _union_from_rings(b["rings"])
        clipped = (areamod.clip_features_to_area(feats, union, min_inside_mi=args.min_inside_mi)
                   if union is not None else feats)
        row = conv.convert({"features": clipped}, slug, meta["name"], meta["state"],
                           meta["center"], meta["osm_rel"], kinds)
        problems = validate(row)
        if problems:
            failed.append((slug, problems)); continue
        count += 1
        if args.dry_run:
            published.append((slug, row["trail_count"], "dry-run"))
            continue
        json.dump(row, open(os.path.join(args.out_dir, f"{slug}.json"), "w"))
        for r in index:
            if r and r[0] == slug:
                while len(r) < 8:
                    r.append(None)
                r[5], r[6] = row["trail_count"], row["total_mi"]
                break
        published.append((slug, row["trail_count"], row["total_mi"]))

    if not args.dry_run:
        json.dump(index, open(args.index, "w"))

    print(f"\n=== published {len(published)} areas "
          f"({'dry-run, nothing written' if args.dry_run else 'wrote geom + updated index'}) ===")
    for slug, n, mi in published:
        print(f"  {slug}: {n} trails ({mi})")
    print(f"\nskipped {len(skipped)} (no trails / no boundary):")
    for slug, why in skipped[:40]:
        print(f"  {slug}: {why}")
    if failed:
        print(f"\nFAILED validation {len(failed)} (NOT written):")
        for slug, probs in failed:
            print(f"  {slug}: {probs}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
