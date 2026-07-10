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
import model                        # noqa: E402 — merge_key for rescue dedupe

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


# Designator words dropped when comparing park names — the distinctive part
# ("South Mountain") is what identifies the same park across split polygons.
_AREA_STOPWORDS = {
    "national", "state", "park", "preserve", "forest", "forests", "wilderness",
    "monument", "area", "areas", "recreation", "recreational", "conservation",
    "natural", "nature", "reserve", "refuge", "and", "the", "of", "district",
    "study", "regional", "county", "memorial", "management", "critical",
    "environmental", "concern", "wildlife", "riparian",
}


def _sig_tokens(name: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", (name or "").lower())
            if t and t not in _AREA_STOPWORDS}


def _clip_one(g, f, area_union, min_inside_mi, route_clamp_mi):
    """Decide one trail's presence in an area. A trail belongs to an area if its
    geometry enters the boundary with at least `min_inside_mi` inside (filters
    boundary grazes). A ROUTE-scale trail (kind=route OR full length >=
    route_clamp_mi) is CLAMPED to its in-park segment — we never drop a whole
    thru-route into a park it merely crosses. A local named trail is kept WHOLE
    (full geometry + full length) so a boundary-straddler like South Sixmile
    Canyon shows as the real trail, not a clipped sliver. Returns the output
    feature or None."""
    try:
        inter = g.intersection(area_union)
    except Exception:  # noqa: BLE001 — invalid geometry
        return None
    parts = areamod._line_parts(inter)
    if not parts:
        return None
    inside_lines = [[(c[0], c[1]) for c in ln.coords] for ln in parts]
    inside_mi = round(sum(model.line_mi(l) for l in inside_lines), 3)
    if inside_mi < min_inside_mi:
        return None
    props = dict(f["properties"])
    full = props.get("length_mi")
    is_route = (props.get("kind") == "route"
                or (full is not None and full >= route_clamp_mi))
    if is_route:
        props["length_mi"] = inside_mi
        if full is not None and abs(full - inside_mi) > 1e-6:
            props["full_length_mi"] = full
            props["clipped"] = True
        geom = {"type": "MultiLineString",
                "coordinates": [[list(p) for p in l] for l in inside_lines]}
    else:
        geom = f["geometry"]                 # keep the whole trail
    return {**f, "properties": props, "geometry": geom}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="batch-publish trailforge areas -> app JSON")
    ap.add_argument("--trails", required=True, help="statewide trails.geojson (--per-area-merge)")
    ap.add_argument("--hiking", required=True, help="hiking.osm.pbf for boundary assembly")
    ap.add_argument("--index", default=_DEFAULT_INDEX)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--state", default="Arizona")
    ap.add_argument("--min-inside-mi", type=float, default=0.25,
                    help="a trail needs at least this many miles inside the area "
                         "to be included (filters boundary grazes)")
    ap.add_argument("--route-clamp-mi", type=float, default=30.0,
                    help="a trail this long (or kind=route) is route-scale: CLAMP "
                         "it to its in-park segment instead of keeping it whole")
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

    print("assembling park boundaries from the PBF…", file=sys.stderr)
    from shapely.ops import unary_union
    geoms = {}
    for b in areamod.merge_areas(args.hiking):
        if b.get("name"):
            g = _union_from_rings(b["rings"])
            if g is not None:
                # OSM often carries MORE THAN ONE boundary relation under the
                # same name (a park split into several relations/multipolygons).
                # A plain dict assignment keeps only the last and silently clips
                # away every trail that fell in the others — that's the South
                # Mountain 78->76 drop, which the different-name sibling-fold
                # below can't reach (a same-named piece is skipped by the
                # index_names guard AND never lands in geoms). Union same-named
                # boundaries so every piece contributes.
                geoms[b["name"]] = (unary_union([geoms[b["name"]], g])
                                    if b["name"] in geoms else g)
    index_names = {m["name"].casefold() for m in az.values()}

    # Selection is now TOUCH-based (see the per-area loop): a trail belongs to
    # every area its geometry actually enters, which subsumes the old rep-point
    # assignment AND the separate boundary-straddler rescue — a straddler like
    # DC-Ray Connector is picked up simply because it touches the park.
    from shapely.geometry import shape as _shape

    def siblings(primary: str) -> list[str]:
        """primary boundary + any OSM boundary that overlaps it, shares a name
        token, and is NOT itself a separate app area — folds 'loose' split
        polygons (e.g. 'South Mountain Preserve') into their parent while
        keeping nested app areas (a wilderness inside a forest) independent."""
        pg = geoms.get(primary)
        names = [primary]
        if pg is None or pg.area <= 0:
            return names
        ptok = _sig_tokens(primary)
        for nm, g in geoms.items():
            if nm == primary or nm.casefold() in index_names or not (ptok & _sig_tokens(nm)):
                continue
            try:
                if pg.intersection(g).area > 0.3 * min(pg.area, g.area):
                    names.append(nm)
            except Exception:
                pass
        return names

    def existing_diff(slug: str, row: dict):
        """removed/added trail names vs the currently-published file, plus any
        duplicate-named entries the run itself emits. Uses multiset (per-name
        count) semantics so a rescued straddler that shares a name with an
        existing trail — invisible to a set diff — still surfaces as '(xN)'."""
        from collections import Counter
        path = os.path.join(args.out_dir, f"{slug}.json")
        if not os.path.exists(path):
            return None
        try:
            live = json.load(open(path))
        except Exception:
            return None
        new_c = Counter(t["name"] for t in row["trails"])
        live_c = Counter(t["name"] for t in live.get("trails", []))
        removed = sorted(f"{n} (x{live_c[n]-new_c[n]})" if live_c[n]-new_c[n] > 1 else n
                         for n in live_c if live_c[n] > new_c[n])
        added = sorted(f"{n} (x{new_c[n]-live_c[n]})" if new_c[n]-live_c[n] > 1 else n
                       for n in new_c if new_c[n] > live_c[n])
        dups = sorted(f"{n} (x{c})" for n, c in new_c.items() if c > 1)
        return removed, added, dups

    kinds = {"trail", "hike"} if args.no_routes else {"trail", "hike", "route"}
    published, skipped, failed, changes = [], [], [], []
    touch_gain = []                     # (slug, name, full_length_mi) for --touch-report

    # Selection is TOUCH-based: a trail belongs to every area its geometry enters,
    # not just the one its midpoint fell in — that's what keeps a boundary-
    # straddling trail in the park instead of clipping it to a sliver. Precompute
    # each trail's shape + bounds once, then bbox-prefilter per area.
    all_shapes = []
    for f in fc["features"]:
        try:
            g = _shape(f["geometry"])
            all_shapes.append((g.bounds, g, f))
        except Exception:  # noqa: BLE001
            pass

    count = 0
    for slug, meta in sorted(az.items()):
        if args.limit and count >= args.limit:
            break
        primary = next((nm for nm in geoms if nm.casefold() == meta["name"].casefold()), None)
        if primary is None:
            skipped.append((slug, "no boundary in PBF")); continue
        sib = siblings(primary)
        union = unary_union([geoms[n] for n in sib])
        ux0, uy0, ux1, uy1 = union.bounds
        # Every trail whose bbox overlaps the area is a candidate; _clip_one
        # decides in/out (>= min_inside), keep-whole (local trail) vs clamp
        # (route-scale). Dedupe by merge_key so two same-name objects reaching
        # the same park don't both land.
        clipped, have = [], set()
        for (bx0, by0, bx1, by1), g, f in all_shapes:
            if bx1 < ux0 or bx0 > ux1 or by1 < uy0 or by0 > uy1:
                continue
            r = _clip_one(g, f, union, args.min_inside_mi, args.route_clamp_mi)
            if r is None:
                continue
            k = model.merge_key(r["properties"].get("name") or "")
            if k and k in have:
                continue
            have.add(k)
            clipped.append(r)
        if not clipped:
            skipped.append((slug, "no trails touch this area")); continue
        row = conv.convert({"features": clipped}, slug, meta["name"], meta["state"],
                           meta["center"], meta["osm_rel"], kinds)
        problems = validate(row)
        if problems:
            failed.append((slug, problems)); continue
        d = existing_diff(slug, row)
        if d and (d[0] or d[1] or d[2]):
            changes.append((slug, d[0], d[1], d[2]))
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
    if changes:
        print(f"\n=== trail changes vs currently-published files ({len(changes)} areas) ===")
        for slug, removed, added, dups in changes:
            print(f"  {slug}:  -{len(removed)} / +{len(added)}"
                  + (f"  [{len(dups)} duplicate-named]" if dups else ""))
            for nm in removed:
                print(f"      REMOVED: {nm}")
            for nm in added:
                print(f"      added:   {nm}")
            for nm in dups:
                print(f"      DUP-NAME: {nm}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
