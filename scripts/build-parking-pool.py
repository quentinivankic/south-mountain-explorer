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

THE PRE-OWNERSHIP SET (task #44). Shipped geom alone inherits whatever ownership
already dropped — 87% of USFS trailheads and 95% of NPS lots, because some
OVERLAPPING unit already had parking. `add-parking.py --pool-sidecar` writes
those points out at fetch time, after the containment and road gates but before
assignment, and `--extra` folds them in here. Measured 2026-07-29: 1,462 lots
(strict) that shipped geom cannot see, reaching 919 trails inside the 805 m
display gate across 167 areas.

The sidecar is COMMITTED, unlike this file's output. It has to be: it is produced
by the parking roll, which has network and shapely, and consumed by
sync-geom-to-r2, which installs neither.

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
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _parking_verdicts as verdicts_mod  # noqa: E402  — pure Python, no shapely

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
    ap.add_argument("--extra", action="append", default=[], metavar="PATH",
                    help="pool sidecar from add-parking.py --pool-sidecar: "
                         "road-gated federal trailheads captured BEFORE "
                         "ownership assignment. Repeatable. Missing file is "
                         "not an error — the pool is still valid without it.")
    ap.add_argument("--verdicts", default=verdicts_mod.DEFAULT_PATH, metavar="PATH",
                    help="per-lot KEEP/DROP verdicts from the vision adjudication "
                         "(task #53). A lot a verdict DROPs is kept out of the pool; "
                         "see scripts/_parking_verdicts.py for how a lot is matched. "
                         "Missing file is not an error.")
    ap.add_argument("--add-keeps", action="store_true",
                    help="also ADD every judged-KEEP lot that no pool lot covers — a "
                         "real public lot the geometric gates never let through. "
                         "Adds the `certain` and `strong` verdicts; `leaning` ones are "
                         "listed but held unless --add-leaning-keeps. Without the flag "
                         "the candidates are only listed.")
    ap.add_argument("--add-leaning-keeps", action="store_true",
                    help="with --add-keeps, also add KEEPs the judge marked `leaning` "
                         "(plausible but soft — a pull-out with no cars in frame).")
    args = ap.parse_args(argv)

    shipped = {r[0] for r in json.load(open(args.bundle)) if r}
    verdicts = verdicts_mod.load(args.verdicts)

    # Bucket by a ~275 m cell so dedup compares neighbours, not all 39k lots.
    cells: dict[tuple[int, int], list[dict]] = {}
    C = 0.0025
    kept: list[dict] = []
    seen_areas = dropped = 0

    def consider(lot: dict) -> bool:
        """Add one lot to the pool unless a lot within DEDUP_M is already there.
        Returns True if it became a new entry. Shared by the geom pass and the
        sidecar pass so the sidecar cannot introduce a second copy of a lot that
        already ships."""
        nonlocal dropped
        la, lo = lot.get("lat"), lot.get("lon")
        if la is None or lo is None:
            return False
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
            return False
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
        return True

    # Shipped geom FIRST, deliberately. A lot that already ships keeps its exact
    # position and identity, and a sidecar lot within 40 m merges INTO it rather
    # than displacing it — so adding the sidecar can only ever add pins, never
    # move one the app already draws.
    #
    # The verdicts are applied HERE, per area, and not inside consider(): the
    # refuse-to-empty guard needs to know an area's whole lot list. A verdict
    # sidecar must never remove the last lot serving an area — leaving a real
    # trailhead unmarked is a smaller harm than telling a hiker a park has
    # nowhere to park (the nonhiking-trails.json rule, applied to parking).
    verdict_dropped = 0
    refused: list[tuple[str, int]] = []
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
        if verdicts is not None and lots:
            doomed = [lot for lot in lots if verdicts.drop_for(lot) is not None]
            if doomed and len(doomed) == len(lots):
                refused.append((slug, len(doomed)))
                doomed = []
            verdict_dropped += len(doomed)
            lots = [lot for lot in lots if not any(lot is d for d in doomed)]
        for lot in lots:
            consider(lot)

    from_geom = len(kept)
    extra_seen = extra_new = 0
    for path in args.extra:
        if not os.path.exists(path):
            print(f"  sidecar {path}: not present — skipping")
            continue
        try:
            doc = json.load(open(path))
        except Exception as e:             # noqa: BLE001
            print(f"  sidecar {path}: unreadable ({e}) — skipping")
            continue
        states = doc.get("states") if isinstance(doc, dict) else None
        if not isinstance(states, dict):
            print(f"  sidecar {path}: no 'states' object — skipping")
            continue
        for code in sorted(states):
            for lot in states[code] or []:
                extra_seen += 1
                # Federal points carry no OSM identity, but a judged footprint
                # can still cover one; the DROP applies to them as well.
                if verdicts is not None and verdicts.drop_for(lot) is not None:
                    verdict_dropped += 1
                    continue
                if consider(lot):
                    extra_new += 1
        print(f"  sidecar {path}: {len(states)} state(s), "
              f"{extra_seen} lot(s) read, {extra_new} new so far")

    keep_candidates: list[dict] = []
    keeps_added = keeps_merged = 0
    keeps_held: list[dict] = []
    if verdicts is not None:
        # Every judged-KEEP lot that nothing in the pool covers is a public lot
        # the vision confirmed and the geometric gates never let through
        # (trailheads sit outside park polygons by nature — 300 to 1,300 m from
        # where our trail geometry starts, past the roll's 250 m gate). Listed
        # always; added on --add-keeps. The user read the first list of 66
        # (2026-09-13) and chose to ship the certain + strong ones and hold
        # the leaning ones — hence the confidence split.
        def covered(e: dict) -> bool:
            ci, cj = int(e["lat"] / C), int(e["lon"] / C)
            for i in (ci - 1, ci, ci + 1):
                for j in (cj - 1, cj, cj + 1):
                    for other in cells.get((i, j), ()):
                        if verdicts_mod.covers(e, other["lat"], other["lon"]) is not None:
                            return True
            return False

        for e in sorted(verdicts.by_verdict("KEEP"), key=lambda e: e["_key"]):
            if covered(e):
                continue
            keep_candidates.append(e)
            if not args.add_keeps:
                continue
            # Allowlist, not a denylist: a verdict with no confidence or a word
            # this code does not know is held with the leaning ones rather than
            # shipped by default. The rule this feature rests on is that a pin
            # is added because someone read the list, so unknown fails closed.
            conf = e.get("confidence")
            if conf not in ("certain", "strong") and not (conf == "leaning" and args.add_leaning_keeps):
                keeps_held.append(e)
                continue
            # Pool-only, owned by nobody, like every other pool lot: position and
            # name. No trailhead / fee flags — the verdict did not judge those.
            lot = {"lat": e["lat"], "lon": e["lon"]}
            if e.get("name"):
                lot["name"] = e["name"]
            if consider(lot):
                keeps_added += 1
            else:
                # Two judged KEEPs within DEDUP_M of each other (an overflow
                # pad beside its main lot) become one pin, as any two lots do.
                keeps_merged += 1

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
    if args.extra:
        print(f"  from shipped geom {from_geom}  "
              f"+ pre-ownership sidecar {extra_new} NEW "
              f"(of {extra_seen} read; the rest already ship)")
    if verdicts is not None:
        print(f"  verdicts: {verdict_dropped} judged-DROP lot(s) kept out of the pool"
              + (f", refused to empty {len(refused)} area(s): "
                 + ", ".join(f"{s} ({n})" for s, n in refused) if refused else ""))
        if args.add_keeps:
            print(f"  verdicts: {len(keep_candidates)} judged-KEEP lot(s) nothing in the pool "
                  f"covered — ADDED {keeps_added}"
                  + (f", {keeps_merged} merged into a neighbouring pin" if keeps_merged else "")
                  + (f", {len(keeps_held)} leaning held (no --add-leaning-keeps)" if keeps_held else ""))
            for e in keeps_held:
                print(f"     held  {e['_key']:22} {str(e.get('name'))[:36]:38} {e.get('area')}")
        else:
            print(f"  verdicts: {len(keep_candidates)} judged-KEEP lot(s) nothing in the pool "
                  f"covers — not added (no --add-keeps)")
            for e in keep_candidates[:12]:
                print(f"     {e['_key']:22} {str(e.get('name'))[:36]:38} {e.get('area')}")
            if len(keep_candidates) > 12:
                print(f"     ... {len(keep_candidates) - 12} more")
    print(f"  named {named}  federal {fed}  trailhead-flagged {th}  fee known {feed}")
    print(f"  wrote {args.out} ({size / 1e6:.2f} MB raw)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
