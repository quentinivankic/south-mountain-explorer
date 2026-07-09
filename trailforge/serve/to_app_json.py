#!/usr/bin/env python3
"""Convert a trailforge trails.geojson -> the app's per-area JSON (the shape
cdn.trekdex.app/<slug>.json serves, decoded as AreaRow/Trail), and — the
whole point of the first pass — DIFF trail-ids against the live area file so
we see exactly how many completions would re-bind vs. orphan BEFORE anything
ships.

Binding rule (from the iOS code): completion is keyed areaId -> trailId.
trailId is slugify(name); the app strips a trailing '-<1-3 digits>' on load
(Trail.canonicalTrailId). So a completion re-lights iff the new data has a
trail whose slug canonicalizes to the same value. Orphaned completions are
HIDDEN, not deleted (recoverable on revert); GPS tracks are never touched.

Usage:
  python3 to_app_json.py --in data/aoi/south-mountain.trails.geojson \
    --area-id south-mountain-park-and-preserve-az \
    --name "South Mountain Park and Preserve" --state Arizona \
    --center-lat 33.3359 --center-lon -112.0704 --osm-relation-id 9244885 \
    --out /tmp/sm.app.json --compare /tmp/sm.live.json
"""
from __future__ import annotations
import argparse
import json
import re
import sys


def trail_slug(name: str) -> str:
    """Mirror scripts/_seed_constants._trail_slug / AreaDataService.slugify."""
    parts = [p for p in re.split(r"[^a-z0-9]+", (name or "").lower()) if p]
    return "-".join(parts)[:60]


def canonical(tid: str) -> str:
    """Mirror Trail.canonicalTrailId: strip a trailing '-<1-3 digits>' suffix."""
    return re.sub(r"-\d{1,3}$", "", tid or "")


def difficulty(sac: str | None, vis: str | None, miles: float) -> str:
    """Mirror _difficulty_label (Easy/Moderate/Hard)."""
    sac = (sac or "").strip()
    if sac and sac != "hiking":
        return "Hard"
    if miles > 4:
        return "Hard"
    if miles > 2 or (vis or "") == "intermediate":
        return "Moderate"
    return "Easy"


def convert(fc: dict, area_id: str, name: str, state: str,
            center: tuple[float, float], osm_rel: int | None,
            include_kinds: set[str]) -> dict:
    trails = []
    slug_counts: dict[str, int] = {}
    minlat = minlon = 1e9
    maxlat = maxlon = -1e9
    # longest-first = a stable, deterministic collision order.
    feats = sorted(fc["features"], key=lambda f: -f["properties"].get("length_mi", 0))
    for f in feats:
        p = f["properties"]
        if (p.get("kind") or "trail") not in include_kinds:
            continue
        nm = p.get("name")
        if not nm:
            continue
        base = trail_slug(nm)
        seen = slug_counts.get(base, 0)
        slug_counts[base] = seen + 1
        tid = base if seen == 0 else f"{base}-{seen}"
        segs = []
        for line in f["geometry"]["coordinates"]:
            pts = [[round(lat, 6), round(lon, 6)] for lon, lat in line]  # [lon,lat]->[lat,lon]
            if len(pts) < 2:
                continue
            segs.append(pts)
            for lat, lon in pts:
                minlat, maxlat = min(minlat, lat), max(maxlat, lat)
                minlon, maxlon = min(minlon, lon), max(maxlon, lon)
        if not segs:
            continue
        miles = round(p.get("length_mi", 0.0), 2)
        trails.append({
            "id": tid, "name": nm, "distanceMi": miles,
            "difficulty": difficulty(p.get("sac_scale"), p.get("trail_visibility"),
                                     p.get("length_mi", 0.0)),
            "segments": segs,
        })
    bbox = ([round(minlon, 6), round(minlat, 6), round(maxlon, 6), round(maxlat, 6)]
            if trails else None)
    return {
        "id": area_id, "name": name, "state": state,
        "center_lat": center[0], "center_lon": center[1], "zoom": 13,
        "bbox": bbox, "trails": trails,
        "trail_count": len(trails),
        "total_mi": round(sum(t["distanceMi"] for t in trails), 1),
        "osm_relation_id": osm_rel,
    }


def diff(new_row: dict, live: dict) -> None:
    new_by_canon = {}
    for t in new_row["trails"]:
        new_by_canon.setdefault(canonical(t["id"]), t["name"])
    live_by_canon = {}
    for t in live.get("trails", []):
        live_by_canon.setdefault(canonical(t["id"]), t["name"])
    new_ids, live_ids = set(new_by_canon), set(live_by_canon)
    matched = new_ids & live_ids
    new_only = new_ids - live_ids
    orphaned = live_ids - new_ids
    print(f"\n=== orphan diff (canonical trail-ids) ===")
    print(f"live:  {len(live.get('trails', []))} trails, {len(live_ids)} canonical ids")
    print(f"new:   {len(new_row['trails'])} trails, {len(new_ids)} canonical ids")
    print(f"  matched  (completion re-binds):  {len(matched)}")
    print(f"  new      (no prior completion):  {len(new_only)}")
    print(f"  ORPHANED (completion would hide): {len(orphaned)}")
    if orphaned:
        print("  orphaned (live trail -> would no longer match):")
        for cid in sorted(orphaned):
            print(f"    - {cid}   ({live_by_canon[cid]})")
    if new_only:
        print("  new trails (not in live):")
        for cid in sorted(new_only):
            print(f"    + {cid}   ({new_by_canon[cid]})")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="trailforge geojson -> app AreaRow json + orphan diff")
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--area-id", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--state", default="Arizona")
    ap.add_argument("--center-lat", type=float, required=True)
    ap.add_argument("--center-lon", type=float, required=True)
    ap.add_argument("--osm-relation-id", type=int, default=None)
    ap.add_argument("--out")
    ap.add_argument("--compare", help="live area json to diff trail-ids against")
    ap.add_argument("--no-routes", action="store_true",
                    help="exclude kind=route overlays (default: include everything)")
    args = ap.parse_args(argv)

    fc = json.load(open(args.inp))
    kinds = {"trail", "hike"} if args.no_routes else {"trail", "hike", "route"}
    row = convert(fc, args.area_id, args.name, args.state,
                  (args.center_lat, args.center_lon), args.osm_relation_id, kinds)
    print(f"converted {row['trail_count']} trails, {row['total_mi']} mi", file=sys.stderr)
    if args.out:
        json.dump(row, open(args.out, "w"))
        print(f"wrote {args.out}", file=sys.stderr)
    if args.compare:
        diff(row, json.load(open(args.compare)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
