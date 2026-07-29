#!/usr/bin/env python3
"""Give shipped areas back the OSM id of the polygon they were cut from.

THE PROBLEM. `add-parking.py`'s primary quality gate is CONTAINMENT: a lot ships
only if it sits inside the area's real boundary, because proximity alone cannot
tell "the lot inside the park" from "the shop across the road". The gate needs a
polygon, and the polygon is looked up by `osm_relation_id`. Measured 2026-07-29:
only **2,909 of 9,060** shipped areas carry one. The other 6,151 fall back to
proximity-only, and 4,150 of them already ship parking that way — so this is a
QUALITY problem, not a coverage one.

WHY THE ID IS MISSING, and it is deliberate: `seed-areas.py` records the id only
for RELATION-sourced areas. The app's live Overpass fallback computes an area id
as `osmId + 3_600_000_000`, the relation-only offset, so storing a WAY id there
would silently point it at the wrong polygon. Way-sourced areas therefore got
None — and nothing else ever wrote the id down, so today neither `index.json` nor
the geom has it.

WHAT THIS DOES. Reads park polygons straight from the local extract with the same
libosmium area assembler the pipeline already uses (`assemble/areas.py`), which
handles closed ways and multipolygon relations alike, and matches each
boundary-less area to one by NAME plus POSITION. The id goes into a NEW field,
`osm_way_id`, so `osm_relation_id` keeps the meaning the app depends on.

Name alone is not enough — "Riverside Park" exists in most states — so a match
also has to contain the area's centre, or failing that overlap its bbox by more
than half, AND then hold at least 90% of the area's own trail vertices. Anything
ambiguous is left alone: a wrong boundary would silently delete a park's parking,
which is far worse than leaving it on proximity.

The 90% floor came from the measured distribution, not from taste (2026-07-29):

    0-25%    395      50-75%    440      90-99%   1,569
    25-50%   239      75-90%    639      99-100%  2,734

It is not 100% because publish keeps boundary-STRADDLING trails on purpose, so
some vertices lie outside even the correct polygon; and the containment gate
buffers the boundary out 30 m anyway. It is not 50% because the rows in that
band are the ones a threshold actually decides — Providence Mountains State
Recreation Area at 45%, McCarty Hill State Forest at 58% — and adopting a
boundary that holds half an area's trails would drop parking near the other
half, which is worse than the proximity-only behaviour it replaces.

    python3 scripts/backfill-area-boundary-ids.py --pbf <parks.osm.pbf> --dry-run
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
GEOM = _ROOT / "public" / "areas" / "geom"

# Same predicate as trailforge/assemble/areas.py::_is_park_area — admin
# boundaries are excluded on purpose (a county would swallow whole parks).
_BOUNDARY = ("national_park", "protected_area", "national_forest")
_LEISURE = ("nature_reserve", "park")


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _is_park_area(tags: dict) -> bool:
    if tags.get("boundary") in _BOUNDARY:
        return True
    if tags.get("leisure") in _LEISURE:
        return True
    return "protect_class" in tags


def _display_name(tags: dict) -> str | None:
    if tags.get("name"):
        return tags["name"]
    for k, v in tags.items():
        if k.startswith("name:") and v:
            return v
    return None


def norm(name: str) -> str:
    """Loose comparison key. Punctuation and case vary between our slugs and
    OSM's names far more often than the words do."""
    keep = [c.lower() if c.isalnum() else " " for c in name]
    return " ".join("".join(keep).split())


def load_park_areas(pbf: str) -> list[dict]:
    """Every named park polygon in the extract, with the id it came from.

    `a.orig_id()` is the WAY or RELATION id behind an assembled area, and
    `a.from_way()` says which. That pair is exactly what was thrown away at seed
    time, and the assembler has had it all along.
    """
    import osmium
    import shapely.wkb

    wkbfab = osmium.geom.WKBFactory()
    out: list[dict] = []

    class Handler(osmium.SimpleHandler):
        def area(self, a):
            tags = {t.k: t.v for t in a.tags}
            if not _is_park_area(tags):
                return
            name = _display_name(tags)
            if not name:
                return
            try:
                geom = shapely.wkb.loads(wkbfab.create_multipolygon(a), hex=True)
            except Exception:              # noqa: BLE001 — unassemblable ring
                return
            if geom.is_empty:
                return
            out.append({"name": name, "key": norm(name), "geom": geom,
                        "bounds": geom.bounds,
                        "osm_id": a.orig_id(),
                        "osm_type": "way" if a.from_way() else "relation"})

    Handler().apply_file(pbf, locations=True)
    return out


def bbox_overlap_frac(a, b) -> float:
    """Fraction of bbox `a` covered by bbox `b`. Used only as the fallback when
    the area's centre is outside every candidate — a park whose stored centre is
    a label point rather than a centroid."""
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    iy = max(0.0, min(ay1, by1) - max(ay0, by0))
    area = (ax1 - ax0) * (ay1 - ay0)
    return (ix * iy / area) if area > 0 else 0.0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pbf", required=True,
                    help="a park-tagged extract (osmium tags-filter of us-latest)")
    ap.add_argument("--geom-dir", default=str(GEOM))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit-report", type=int, default=25)
    ap.add_argument("--min-trail-cover", type=float, default=0.9,
                    help="reject a matched boundary that holds less than this "
                         "share of the area's own trail vertices — the check "
                         "that separates the right polygon from a same-named "
                         "one (default 0.9)")
    args = ap.parse_args(argv)

    from shapely.geometry import Point

    log(f"reading park polygons from {args.pbf}")
    parks = load_park_areas(args.pbf)
    by_key: dict[str, list[dict]] = collections.defaultdict(list)
    for p in parks:
        by_key[p["key"]].append(p)
    log(f"{len(parks):,} named park polygons, {len(by_key):,} distinct names")

    targets = []
    for f in sorted(os.listdir(args.geom_dir)):
        if not f.endswith(".json"):
            continue
        path = os.path.join(args.geom_dir, f)
        try:
            g = json.load(open(path))
        except Exception:                  # noqa: BLE001
            continue
        if g.get("osm_relation_id") or g.get("osm_way_id"):
            continue
        targets.append((path, g))
    log(f"{len(targets):,} shipped areas with no boundary id")

    stats = collections.Counter()
    hits, misses = [], []
    covers = []                            # (frac, slug, name, type, id) for every candidate
    for path, g in targets:
        cands = by_key.get(norm(g.get("name") or ""))
        if not cands:
            stats["no name match"] += 1
            misses.append((g.get("id"), g.get("name"), "no name match"))
            continue
        pt = Point(g["center_lon"], g["center_lat"])
        inside = [c for c in cands if c["geom"].contains(pt)]
        how = "centre inside"
        if not inside and g.get("bbox"):
            b = (g["bbox"][0], g["bbox"][1], g["bbox"][2], g["bbox"][3])
            inside = [c for c in cands if bbox_overlap_frac(b, c["bounds"]) > 0.5]
            how = "bbox overlap >50%"
        if not inside:
            stats["name matched, wrong place"] += 1
            misses.append((g.get("id"), g.get("name"), "name matched, wrong place"))
            continue
        if len(inside) > 1:
            # Several same-named polygons in the same place is the normal case
            # for a park split across relations. Prefer a RELATION (the app's
            # field can use it) and then the largest, which is the whole park
            # rather than one of its pieces.
            inside.sort(key=lambda c: (c["osm_type"] != "relation",
                                       -(c["bounds"][2] - c["bounds"][0]) *
                                       (c["bounds"][3] - c["bounds"][1])))
        pick = inside[0]

        # THE SAFETY GATE, and the reason this is not just a name lookup. A
        # boundary that does not hold this area's own trails is the wrong
        # boundary — a same-named sub-park, or the right name in the right city
        # but the wrong polygon — and adopting it would make the containment gate
        # delete the area's parking. So the polygon has to cover the trails we
        # already publish for it. Sampled, because some areas carry 100k vertices
        # and the answer does not change.
        pts = []
        for t in (g.get("trails") or []):
            for seg in (t.get("segments") or []):
                pts.extend(seg[::max(1, len(seg) // 12)][:12])
        if not pts:
            stats["no trail vertices to check"] += 1
            continue
        step = max(1, len(pts) // 400)
        sample = pts[::step][:400]
        poly = pick["geom"]
        covered = sum(1 for p in sample if poly.contains(Point(p[1], p[0])))
        frac = covered / len(sample)
        covers.append((frac, g.get("id"), g.get("name"), pick["osm_type"], pick["osm_id"]))
        if frac < args.min_trail_cover:
            stats[f"REJECTED: covers only <{args.min_trail_cover:.0%} of trails"] += 1
            misses.append((g.get("id"), g.get("name"),
                           f"boundary covers {frac:.0%} of trails"))
            continue

        stats[f"matched ({how})"] += 1
        stats[f"  as {pick['osm_type']}"] += 1
        hits.append((path, g, pick, how, frac))

    # The threshold is the whole decision, so show the distribution rather than
    # asserting a number. A boundary that holds only part of an area's trails
    # would make the containment gate drop parking near the rest — worse than
    # the proximity-only behaviour it replaces.
    if covers:
        import bisect
        fr = sorted(c[0] for c in covers)
        print("\n  trail coverage of the matched boundary:")
        for lo, hi in ((0,.25),(.25,.5),(.5,.75),(.75,.9),(.9,.99),(.99,1.01)):
            n = bisect.bisect_left(fr, hi) - bisect.bisect_left(fr, lo)
            print(f"    {lo:.0%}-{hi:.0%}  {n:6,}  {'#' * (n // 60)}")
        near = sorted(c for c in covers if 0.45 <= c[0] <= 0.95)
        print(f"\n  10 rows just above the cut (the ones a threshold decides):")
        for frac, slug, name, typ, oid in near[::max(1, len(near)//10 or 1)][:10]:
            print(f"    {frac:5.0%}  {slug:46s} {typ} {oid}")
    log("outcome:")
    for k, v in sorted(stats.items(), key=lambda kv: -kv[1]):
        print(f"    {v:6,}  {k}")

    print(f"\n  --- {min(args.limit_report, len(hits))} matches to eyeball ---")
    step = max(1, len(hits) // max(1, args.limit_report))
    for path, g, pick, how, frac in hits[::step][:args.limit_report]:
        print(f"   {g['id']:48s} -> {pick['osm_type']} {pick['osm_id']}  "
              f"[{how}, holds {frac:.0%} of trails]")
        print(f"        ours {g.get('name')!r}   osm {pick['name']!r}")

    print(f"\n  --- {min(10, len(misses))} misses ---")
    for slug, name, why in misses[::max(1, len(misses) // 10 or 1)][:10]:
        print(f"   {slug:48s} {why}   {name!r}")

    if args.dry_run:
        log("dry run — nothing written")
        return 0

    wrote = 0
    for path, g, pick, _, _frac in hits:
        # RELATION matches go in the app's own field; way matches go in the new
        # one. The app computes an Overpass area id as osmId + 3_600_000_000,
        # the relation-only offset, so a way id in that field would point it at
        # a different polygon entirely.
        key = "osm_relation_id" if pick["osm_type"] == "relation" else "osm_way_id"
        g[key] = int(pick["osm_id"])
        Path(path).write_text(json.dumps(g))
        wrote += 1
    log(f"wrote {wrote:,} geom file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
