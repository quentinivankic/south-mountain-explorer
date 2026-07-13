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
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "assemble"))
import model  # noqa: E402 — for merge_key (same-trail matching across renames)
sys.path.insert(0, os.path.dirname(__file__))
import elevation  # noqa: E402 — single source of truth for the difficulty label


def trail_slug(name: str) -> str:
    """Mirror scripts/_seed_constants._trail_slug / AreaDataService.slugify."""
    parts = [p for p in re.split(r"[^a-z0-9]+", (name or "").lower()) if p]
    return "-".join(parts)[:60]


def canonical(tid: str) -> str:
    """Mirror Trail.canonicalTrailId: strip a trailing '-<1-3 digits>' suffix."""
    return re.sub(r"-\d{1,3}$", "", tid or "")


def difficulty(sac: str | None, vis: str | None, miles: float,
               gain_ft: float | None = None) -> str:
    """Easy/Moderate/Hard via elevation.difficulty_label (single source of
    truth). gain_ft=None keeps the legacy length-only behaviour for the
    convert path, which has no DEM; the elevation post-process passes a real
    gain to upgrade it."""
    return elevation.difficulty_label(miles, gain_ft, sac, vis)


def convert(fc: dict, area_id: str, name: str, state: str,
            center: tuple[float, float], osm_rel: int | None,
            include_kinds: set[str],
            id_by_mergekey: dict[str, str] | None = None) -> dict:
    trails = []
    slug_counts: dict[str, int] = {}
    used_canonical: set[str] = set()
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
        # id continuity: if this is the SAME trail as a live one (same
        # merge_key) but our name-normalization changed the slug, keep the
        # LIVE id so the completion re-binds instead of orphaning.
        preserved = id_by_mergekey.get(model.merge_key(nm)) if id_by_mergekey else None
        if preserved and preserved not in slug_counts:
            tid = preserved
            slug_counts[preserved] = 1
        else:
            base = trail_slug(nm)
            seen = slug_counts.get(base, 0)
            slug_counts[base] = seen + 1
            tid = base if seen == 0 else f"{base}-{seen}"
        # The app strips a trailing -<1-3 digits> on load (canonicalTrailId),
        # so "Foo" + "Foo 2" both collapse to "foo" and crash its id-keyed
        # dict. Guarantee the CANONICAL id is unique; '-x' survives the strip.
        while canonical(tid) in used_canonical:
            tid += "-x"
        used_canonical.add(canonical(tid))
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


_DEFAULT_INDEX = os.path.join(os.path.dirname(__file__), "..", "..",
                              "ios", "SouthMountainExplorer", "Resources", "areas-index.json")


def _index_lookup(index_path: str, area_id: str) -> dict | None:
    """Row for area_id from the bundled areas-index.json tuple array:
    [id, name, state, lat, lon, trail_count?, total_mi?, osm_relation_id?]."""
    for row in json.load(open(index_path)):
        if row and row[0] == area_id:
            return {"name": row[1], "state": row[2],
                    "center": (row[3], row[4]),
                    "osm_rel": row[7] if len(row) > 7 else None}
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="trailforge geojson -> app AreaRow json + orphan diff")
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--area-id", required=True)
    # name/state/center/osm-rel auto-fill from --index by area-id; override here.
    ap.add_argument("--name")
    ap.add_argument("--state")
    ap.add_argument("--center-lat", type=float)
    ap.add_argument("--center-lon", type=float)
    ap.add_argument("--osm-relation-id", type=int)
    ap.add_argument("--index", default=_DEFAULT_INDEX,
                    help="areas-index.json to auto-fill area metadata (and --update-index)")
    ap.add_argument("--out")
    ap.add_argument("--update-index", action="store_true",
                    help="patch the area's trail_count + total_mi in --index in place")
    ap.add_argument("--compare", help="live area json to diff trail-ids against")
    ap.add_argument("--preserve-ids-from",
                    help="live area json: keep its id for a same-trail (merge_key) match "
                         "so completions re-bind across a rename (opt-in)")
    ap.add_argument("--no-routes", action="store_true",
                    help="exclude kind=route overlays (default: include everything)")
    args = ap.parse_args(argv)

    meta = _index_lookup(args.index, args.area_id) if os.path.exists(args.index) else None
    name = args.name or (meta and meta["name"])
    state = args.state or (meta and meta["state"]) or "Arizona"
    center = ((args.center_lat, args.center_lon) if args.center_lat is not None
              else (meta and meta["center"]))
    osm_rel = args.osm_relation_id if args.osm_relation_id is not None else (meta and meta["osm_rel"])
    if not name or not center:
        ap.error(f"no metadata for '{args.area_id}' in {args.index}; pass --name/--center-lat/--center-lon")

    id_by_mergekey = None
    if args.preserve_ids_from:
        live = json.load(open(args.preserve_ids_from))
        id_by_mergekey = {model.merge_key(t["name"]): canonical(t["id"])
                          for t in live.get("trails", [])}

    fc = json.load(open(args.inp))
    kinds = {"trail", "hike"} if args.no_routes else {"trail", "hike", "route"}
    row = convert(fc, args.area_id, name, state, center, osm_rel, kinds, id_by_mergekey)
    print(f"converted {row['trail_count']} trails, {row['total_mi']} mi", file=sys.stderr)
    if args.out:
        json.dump(row, open(args.out, "w"))
        print(f"wrote {args.out}", file=sys.stderr)
    if args.update_index:
        idx = json.load(open(args.index))
        for r in idx:
            if r and r[0] == args.area_id:
                while len(r) < 8:
                    r.append(None)
                r[5], r[6] = row["trail_count"], row["total_mi"]
                break
        json.dump(idx, open(args.index, "w"))
        print(f"updated index {args.area_id}: {row['trail_count']} trails, "
              f"{row['total_mi']} mi in {args.index}", file=sys.stderr)
    if args.compare:
        diff(row, json.load(open(args.compare)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
