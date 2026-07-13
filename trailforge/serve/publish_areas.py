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


# A route "belongs" to a park when that park holds the MAJORITY of its
# length. That's the definition of belonging, not a tuned knob: a trail more
# inside one park than outside it is that park's trail; a route no single park
# mostly contains is a traverse (PCT/AZT/Hayduke) that would only leave pokers.
_MAJORITY = 0.5

# Minimum TOTAL clipped trail mileage for an area to be worth publishing. An
# area whose trails only graze its boundary clips down to a near-zero-length
# sliver (e.g. a 2-metre stub of a trail whose real length lives in a
# neighbouring area), and ships to the app as a broken "1 trail, 0.0 mi"
# entry. Verified against the whole-US publish (2026-07-13): everything under
# 0.1 mi total is a degenerate clip artifact (97 areas, 93 single-trail
# ~0-length stubs), while the 0.1-0.3 mi band holds REAL short preserve trails
# (Susquehanna Wetland Trail, Matthews Island Trail, Cane Creek's Steep
# Trail) — so 0.1 is the safe floor, no legit small hike lost. Self-healing:
# if a boundary later catches the real trail, the area re-publishes with real
# mileage.
_MIN_AREA_MI = 0.1


def _inside_mi(g, area_union):
    """Miles of g's line geometry that fall inside `area_union` (0 on error)."""
    try:
        inter = g.intersection(area_union)
    except Exception:  # noqa: BLE001 — invalid geometry
        return 0.0
    parts = areamod._line_parts(inter)
    if not parts:
        return 0.0
    return sum(model.line_mi([(c[0], c[1]) for c in ln.coords]) for ln in parts)


def _line_geom_and_mi(lines):
    """(MultiLineString geojson, rounded miles) from a list of coord-lists."""
    mi = round(sum(model.line_mi(l) for l in lines), 3)
    geom = {"type": "MultiLineString",
            "coordinates": [[list(p) for p in l] for l in lines]}
    return geom, mi


def _trim_to_parks(g, all_parks):
    """Trim only the DANGLING ends of a trail that hang outside every park — a
    residential/unmanaged dead-end like DC-Ray Connector running into a
    neighbourhood. Everything BETWEEN the first and last point where the trail
    touches a park is kept, so an in->gap->in trail that links two parks stays
    whole (the connecting gap is not a dangle). Returns (lines, miles); ([], 0)
    if the trail never touches a park.

    Per component: keep the sub-line between the first and last in-park vertex
    (linear-referenced onto the component). A component entirely outside every
    park is dropped; a dangling tail past the last park-contact is trimmed; a
    mid-trail gap survives because it sits between two in-park contacts."""
    from shapely.geometry import LineString, MultiLineString, Point
    from shapely.ops import substring
    comps = list(g.geoms) if isinstance(g, MultiLineString) else [g]
    out = []
    for comp in comps:
        if not isinstance(comp, LineString) or comp.length == 0:
            continue
        try:
            inside = comp.intersection(all_parks)
        except Exception:  # noqa: BLE001 — invalid geometry
            continue
        parts = areamod._line_parts(inside)
        if not parts:
            continue                      # component never touches a park
        ds = [comp.project(Point(c)) for ln in parts for c in ln.coords]
        seg = substring(comp, min(ds), max(ds))
        segs = list(seg.geoms) if isinstance(seg, MultiLineString) else [seg]
        for s in segs:
            if isinstance(s, LineString) and s.length > 0:
                out.append([(x, y) for x, y in s.coords])
    return out, sum(model.line_mi(l) for l in out)


def _clamped_feature(g, f, all_parks, cache):
    """Feature with its dangling out-of-park ends trimmed (see _trim_to_parks),
    length_mi updated to the trimmed length (full_length_mi + clipped flag kept
    when it shrank). Returns (feature, clamped_mi), or (None, 0) if the trail
    touches no park. The trim is cached per trail (same for every area)."""
    key = id(f)
    if key not in cache:
        cache[key] = _trim_to_parks(g, all_parks)
    lines, clamped_mi = cache[key]
    if not lines:
        return None, 0.0
    geom, mi = _line_geom_and_mi(lines)
    props = dict(f["properties"])
    full = props.get("length_mi")
    props["length_mi"] = mi
    # Only flag genuine clipping — a fully-in-park trail is unchanged, but
    # shapely's intersection nudges coords, so ignore sub-~50ft float noise.
    if full is not None and full - mi > 0.01:
        props["full_length_mi"] = full
        props["clipped"] = True
    return {**f, "properties": props, "geometry": geom}, clamped_mi


def _clip_one(g, f, area_union, min_inside_mi, all_parks, cache):
    """Decide + shape a NON-route trail for one area. Geometry is clamped to the
    all-parks union (residential tails dropped, cross-park stretches kept).
    Membership needs a meaningful in-area portion — at least `min_inside_mi` OR
    the majority of the CLAMPED length. Measuring against the clamped length
    (not the raw length) is what brings back a trail like DC-Ray whose entire
    park-portion sits in this one park but was dwarfed by a residential tail.
    Returns the clamped feature or None."""
    feat, clamped_mi = _clamped_feature(g, f, all_parks, cache)
    if feat is None:
        return None
    inside = _inside_mi(g, area_union)
    if inside >= min_inside_mi or (clamped_mi and inside >= _MAJORITY * clamped_mi):
        return feat
    return None


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
    ap.add_argument("--no-routes", action="store_true",
                    help="(default behavior) drop kind=route thru-hikes; kept as an "
                         "accepted no-op so existing dispatchers don't break")
    ap.add_argument("--multi-area-report", action="store_true",
                    help="diagnostic: list trails that touch >=2 areas — the nested "
                         "designations 'one home park' would wrongly strip. Writes nothing.")
    ap.add_argument("--limit", type=int, help="only publish the first N areas (a first wave)")
    ap.add_argument("--dry-run", action="store_true", help="report matches; write nothing")
    args = ap.parse_args(argv)
    if args.multi_area_report:
        args.dry_run = True             # pure diagnostic — never write

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
    # merge_key -> {name, kind, full, slugs[]} for --multi-area-report: which
    # trails a "one home park" (argmax) rule would move out of a second area.
    membership = {}

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

    # Park boundaries (each unioned with its loose siblings) computed ONCE and
    # shared by the route pre-pass and the per-area loop, in deterministic order.
    area_unions = {}                        # slug -> (union, bounds)
    for slug, meta in sorted(az.items()):
        primary = next((nm for nm in geoms if nm.casefold() == meta["name"].casefold()), None)
        if primary is None:
            skipped.append((slug, "no boundary in PBF")); continue
        u = unary_union([geoms[n] for n in siblings(primary)])
        area_unions[slug] = (u, u.bounds)

    # Union of EVERY park — the clamp target. A trail's geometry outside this
    # (a residential/unmanaged tail) is clipped off; geometry inside any park
    # (including a neighbour park it crosses into) is kept. Clamp is cached per
    # trail in clamp_cache. `all_parks` is None only when no area has a boundary.
    all_parks = unary_union([u for u, _ in area_unions.values()]) if area_unions else None
    clamp_cache = {}

    # THRU-ROUTE decision (global, geometric, nesting-immune). A route is a real
    # trail for a park only if that park holds the MAJORITY of its length; a
    # route no single park mostly contains is a traverse (AZT/Maricopa/Hayduke)
    # and is dropped everywhere, so it leaves no sub-mile pokers. A route fully
    # inside Saguaro NP reads ~100% in the wilderness AND the park, so it's kept
    # whole in both. No length threshold — containment is the whole signal.
    route_home = {}                         # id(f) -> set(slugs) kept in (empty = dropped)
    kept_routes, dropped_routes = [], []    # (name, miles, homes|reason, feature)
    for bounds, g, f in all_shapes:
        if f["properties"].get("kind") != "route":
            continue
        total = f["properties"].get("length_mi") or _inside_mi(g, g.envelope) or 0.0
        bx0, by0, bx1, by1 = bounds
        homes, best_frac, best_slug, touched = set(), 0.0, None, 0
        for slug, (u, (ux0, uy0, ux1, uy1)) in area_unions.items():
            if bx1 < ux0 or bx0 > ux1 or by1 < uy0 or by0 > uy1:
                continue
            inside = _inside_mi(g, u)
            if inside >= args.min_inside_mi:
                touched += 1
            frac = inside / total if total else 0.0
            if frac >= _MAJORITY:
                homes.add(slug)
            if frac > best_frac:
                best_frac, best_slug = frac, slug
        route_home[id(f)] = homes
        nm = f["properties"].get("name")
        if homes:
            kept_routes.append((nm, total, sorted(homes)))
        else:
            reason = (f"thru-hike: spans {touched} areas, "
                      f"largest holds {best_frac * 100:.0f}%"
                      + (f" ({best_slug})" if best_slug else ""))
            dropped_routes.append((nm, total, reason, f))

    count = 0
    for slug, (union, (ux0, uy0, ux1, uy1)) in area_unions.items():
        if args.limit and count >= args.limit:
            break
        meta = az[slug]
        # Every trail whose bbox overlaps the area is a candidate. All geometry
        # is clamped to the all-parks union (residential tails off, cross-park
        # stretches kept). A kept route lands in its home park(s); a non-route
        # needs a meaningful in-area portion. Dedupe by merge_key so two
        # same-name objects reaching the same park don't both land.
        clipped, have = [], set()
        for (bx0, by0, bx1, by1), g, f in all_shapes:
            if bx1 < ux0 or bx0 > ux1 or by1 < uy0 or by0 > uy1:
                continue
            if f["properties"].get("kind") == "route":
                if slug not in route_home.get(id(f), ()):
                    continue
                r, _mi = _clamped_feature(g, f, all_parks, clamp_cache)
                if r is None:
                    continue
            else:
                r = _clip_one(g, f, union, args.min_inside_mi, all_parks, clamp_cache)
                if r is None:
                    continue
            k = model.merge_key(r["properties"].get("name") or "")
            if k and k in have:
                continue
            have.add(k)
            clipped.append(r)
            if k:
                p = r["properties"]
                full = p.get("full_length_mi", p.get("length_mi"))
                m = membership.setdefault(
                    k, {"name": p.get("name") or "", "kind": p.get("kind"),
                        "full": full, "slugs": []})
                m["slugs"].append(slug)
        if not clipped:
            skipped.append((slug, "no trails touch this area")); continue
        row = conv.convert({"features": clipped}, slug, meta["name"], meta["state"],
                           meta["center"], meta["osm_rel"], kinds)
        if row["total_mi"] < _MIN_AREA_MI:
            skipped.append((slug, f"degenerate clip ({row['total_mi']} mi total)"))
            continue
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
                # Pad to 7, NOT 8 — see merge-published-geom.py's comment on
                # the same pattern. A row with no real osm_relation_id must
                # stay a 7-tuple (element absent), not an 8-tuple with an
                # explicit trailing null — iOS's JSONValue decoder has no
                # null case, so any null anywhere fails the whole-array
                # decode for every user.
                while len(r) < 7:
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

    # Thru-route verdicts — the eyeball table (KEEP <park(s)> / DROP <reason>).
    print(f"\n=== thru-route decisions: {len(kept_routes)} kept, "
          f"{len(dropped_routes)} dropped ===")
    for nm, mi, homes in sorted(kept_routes, key=lambda x: -(x[1] or 0)):
        print(f"  KEEP  {mi or 0:7.2f}  {nm}  ->  {', '.join(homes)}")
    for nm, mi, reason, f in sorted(dropped_routes, key=lambda x: -(x[1] or 0)):
        print(f"  DROP  {mi or 0:7.2f}  {nm}  [{reason}]")

    # Dropped routes -> a geojson the QA viewer can overlay in its show/hide
    # 'removed' panel. Written even on a dry run so you can compare before any
    # publish. Each feature carries a plain-language `removed_reason`.
    if dropped_routes:
        base = args.trails
        for suf in (".trails.geojson", ".geojson"):
            if base.endswith(suf):
                base = base[:-len(suf)]; break
        drop_path = base + ".dropped-routes.geojson"
        feats = [{"type": "Feature", "geometry": f["geometry"],
                  "properties": {**f["properties"], "removed_reason": reason,
                                 "removed_category": "route-traverse"}}
                 for nm, mi, reason, f in dropped_routes]
        json.dump({"type": "FeatureCollection", "features": feats}, open(drop_path, "w"))
        print(f"\ndropped-routes geojson -> {drop_path} ({len(feats)} features)")

    if args.multi_area_report:
        # `membership` is every published trail (kept routes live only in their
        # home park(s)). A trail sitting in >=2 areas is almost always a
        # nested designation (wilderness inside a park, a monument over a
        # forest) — which is why we credit every area it enters instead of
        # forcing one "home park". This lists them so that stays visible.
        from collections import Counter
        multi = sorted((m for m in membership.values() if len(set(m["slugs"])) >= 2),
                       key=lambda m: -len(set(m["slugs"])))
        dist = Counter(len(set(m["slugs"])) for m in multi)
        print(f"\n=== multi-area report ===")
        print(f"trails touching >=2 areas: {len(multi)}")
        print(f"span distribution (n areas -> trails):")
        for n in sorted(dist):
            print(f"  {n} areas: {dist[n]}")
        print(f"\ntrails in >=2 areas (name — miles — areas):")
        for m in multi[:60]:
            slugs = sorted(set(m["slugs"]))
            mi = f"{m['full']:.2f}mi" if m["full"] is not None else "?"
            print(f"  {m['name']!r} — {mi} — {', '.join(slugs)}")
        if len(multi) > 60:
            print(f"  … and {len(multi) - 60} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
