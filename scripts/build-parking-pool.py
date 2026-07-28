#!/usr/bin/env python3
"""Build the GLOBAL parking pool — every qualifying lot once, owned by nobody.

WHY OWNERSHIP WAS THE PROBLEM. Parking ships inside each area's geom, so the
pipeline has to decide which area a lot BELONGS to. That single decision is the
source of a long tail of pain: `_FED_EDGE_BUFFER_M` and its "which blank area
gets this orphan" tiebreak (#487), the misattribution risk that made widening it
a judgement call (#38), NPS overlook lots flowing onto a nested wilderness
(#495), and the 2,010 parking-blank areas that have no `osm_relation_id` at all
and therefore can never be edge-filled no matter what the buffer is.

None of it is inherent. The app only ever draws the <=3 nearest lots within 805 m
of the SELECTED TRAIL (`Area.nearestParking`) — it never asks who owns a lot. And
the format already disagrees with ownership: of 39,512 shipped lots only 29,365
are distinct positions, so 4,519 lots already appear in more than one area.

So: keep containment as a QUALITY FILTER and drop it as OWNERSHIP. A lot is in
the pool because it passed the gate somewhere, not because one area won it.
Proximity alone would not do — it cannot tell "inside the park" from "across the
road", which is why Thunderbird went 26 lots to 12 (a neighbour's lot 26 m from a
perimeter trail). The gate stays; only the adjudication goes.

WHAT THIS DOES NOT FIX YET. Built from shipped geom, so it inherits whatever
ownership already dropped. Widening it to the pre-ownership set means emitting
the pool from `add-parking.py` at fetch time, after the containment and road
gates but before assignment. That is the follow-up; this file is the shape and
the distribution path.

Emitted fresh into R2 by sync-geom-to-r2 alongside trail-search.json, never
committed, so it cannot drift from the geom. Geom `parking` keeps shipping
unchanged — v1.0 is in App Store review and reads it.

    python3 scripts/build-parking-pool.py --out /tmp/parking.json
"""
from __future__ import annotations

import argparse
import json
import math
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_GEOM = os.path.join(_ROOT, "public", "areas", "geom")
_BUNDLE = os.path.join(_ROOT, "ios", "SouthMountainExplorer", "Resources",
                       "areas-index.json")
# Two lots this close are the same facility seen twice — the agencies ship one
# car park as several polygons and we centroid each. Matches PARKING_DEDUP_M in
# add-parking.py; kept in sync deliberately rather than imported, because that
# module needs shapely and this one must run anywhere.
DEDUP_M = 40.0


def hav(a1: float, o1: float, a2: float, o2: float) -> float:
    R, p = 6371000.0, math.radians
    x = (math.sin(p(a2 - a1) / 2) ** 2
         + math.cos(p(a1)) * math.cos(p(a2)) * math.sin(p(o2 - o1) / 2) ** 2)
    return 2 * R * math.asin(min(1.0, math.sqrt(x)))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bundle", default=_BUNDLE,
                    help="iOS bundle areas-index.json — the shipped area set")
    ap.add_argument("--geom-dir", default=_GEOM)
    ap.add_argument("--out", required=True, help="output path (R2-served, not committed)")
    args = ap.parse_args(argv)

    shipped = {r[0] for r in json.load(open(args.bundle)) if r}

    # Bucket by a ~275 m cell so dedup compares neighbours, not all 39k lots.
    cells: dict[tuple[int, int], list[dict]] = {}
    C = 0.0025
    kept: list[dict] = []
    seen_areas = dropped = 0
    for f in sorted(os.listdir(args.geom_dir)):
        if not f.endswith(".json"):
            continue
        slug = f[:-5]
        if slug not in shipped:
            continue                      # only areas the app can actually open
        try:
            g = json.load(open(os.path.join(args.geom_dir, f)))
        except Exception:                  # noqa: BLE001
            continue
        lots = g.get("parking") or []
        if lots:
            seen_areas += 1
        for lot in lots:
            la, lo = lot.get("lat"), lot.get("lon")
            if la is None or lo is None:
                continue
            ci, cj = int(la / C), int(lo / C)
            dupe = None
            for i in (ci - 1, ci, ci + 1):
                for j in (cj - 1, cj, cj + 1):
                    for other in cells.get((i, j), ()):
                        if hav(la, lo, other["lat"], other["lon"]) <= DEDUP_M:
                            dupe = other
                            break
                    if dupe:
                        break
                if dupe:
                    break
            if dupe is not None:
                dropped += 1
                # Merge rather than discard: a name or trailhead flag present on
                # only one copy is real information about the same facility.
                if not dupe.get("name") and lot.get("name"):
                    dupe["name"] = lot["name"]
                if lot.get("trailhead"):
                    dupe["trailhead"] = True
                if not dupe.get("source") and lot.get("source"):
                    dupe["source"] = lot["source"]
                if dupe.get("fee") is None and lot.get("fee") is not None:
                    dupe["fee"] = bool(lot["fee"])
                continue
            rec = {"lat": round(la, 6), "lon": round(lo, 6)}
            if lot.get("name"):
                rec["name"] = lot["name"]
            if lot.get("source"):
                rec["source"] = lot["source"]
            if lot.get("trailhead"):
                rec["trailhead"] = True
            if lot.get("fee") is not None:
                rec["fee"] = bool(lot["fee"])
            cells.setdefault((ci, cj), []).append(rec)
            kept.append(rec)

    # Positional array, like index.json and trail-search.json: [lat, lon, name,
    # source, trailhead, fee]. Trailing nulls are cheap and the app decodes by
    # index. `fee` is carried so a pooled lot keeps the paid/free label a
    # per-area lot has — omitting it would make the pool a quiet regression on
    # exactly the detail people care about.
    def fee_flag(r):
        return None if r.get("fee") is None else (1 if r["fee"] else 0)

    out = [[r["lat"], r["lon"], r.get("name"), r.get("source"),
            1 if r.get("trailhead") else 0, fee_flag(r)] for r in kept]
    out.sort(key=lambda r: (r[0], r[1]))
    json.dump(out, open(args.out, "w"), separators=(",", ":"))

    size = os.path.getsize(args.out)
    named = sum(1 for r in out if r[2])
    fed = sum(1 for r in out if r[3])
    th = sum(1 for r in out if r[4])
    feed = sum(1 for r in out if r[5] is not None)
    print(f"parking pool: {len(out)} lots from {seen_areas} area(s), "
          f"{dropped} duplicate copies merged")
    print(f"  named {named}  federal {fed}  trailhead-flagged {th}  fee known {feed}")
    print(f"  wrote {args.out} ({size / 1e6:.2f} MB raw)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
