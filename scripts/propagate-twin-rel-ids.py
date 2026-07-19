#!/usr/bin/env python3
"""Propagate `osm_relation_id` between same-name index twins. No network.

A multi-state area is seeded once PER STATE, so one OSM relation can own several
index rows (`waterton-glacier-...-mt` and `waterton-glacier-...-ca-ab`). Seeding
sometimes attaches the rel id to only one of them, leaving the twin a bare row
that `publish_areas.py`'s boundary fetch can never reach.

Those twins are recoverable without touching Overpass: if a row lacking a rel id
has an identically-NAMED row at the SAME center, they are the same physical place
and the id can simply be copied across.

Guards: exact case-folded name match AND an effectively-identical center
(`--max-km`, default 0.05). The tight default is the real invariant, not a tuned
knob: the seeder derives a row's center FROM its relation, so every per-state row
of one relation stores the SAME center by construction. A non-zero distance is
therefore evidence the name matched but the relation did NOT.

Measured on the 2026-07-19 index: 96 name-matches, 92 at an identical center and
4 apart. Those 4 are exactly the false positives — e.g.
`appalachian-trail-scenic-open-space-ma` vs `-ct`, 22.6 km apart, which are
separate parcels of AT corridor sharing one generic name, NOT one relation.
Raise `--max-km` only to review such cases by hand; do not loosen the default.

    python3 scripts/propagate-twin-rel-ids.py --dry-run
    python3 scripts/propagate-twin-rel-ids.py            # writes index.json

NOTE: run this only when no publish workflow is in flight — the publish commits
`public/areas/index.json` and would conflict.
"""
from __future__ import annotations
import argparse
import json
import math
from collections import defaultdict
from pathlib import Path

INDEX = Path(__file__).resolve().parent.parent / "public" / "areas" / "index.json"


def km(alat: float, alon: float, blat: float, blon: float) -> float:
    return math.hypot((blat - alat) * 111.0,
                      (blon - alon) * 111.0 * math.cos(math.radians(alat)))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-km", type=float, default=0.05,
                    help="center-distance cap for calling two rows the same place; "
                         "the default demands an identical center (see module docstring)")
    args = ap.parse_args()

    rows = json.loads(INDEX.read_text())
    donors: dict[str, list[list]] = defaultdict(list)
    for r in rows:
        if len(r) > 7 and r[7]:
            donors[r[1].casefold()].append(r)

    filled = 0
    report: list[str] = []
    for r in rows:
        if len(r) > 7 and r[7]:
            continue
        if len(r) < 5 or not isinstance(r[3], (int, float)):
            continue
        cands = donors.get(r[1].casefold())
        if not cands:
            continue
        best = min(cands, key=lambda c: km(r[3], r[4], c[3], c[4]))
        d = km(r[3], r[4], best[3], best[4])
        if d > args.max_km:
            continue
        while len(r) < 8:
            r.append(None)
        r[7] = best[7]
        filled += 1
        report.append(f"  {r[0]:52} <- rel {best[7]} from {best[0]} ({d:.1f}km)")

    print(f"propagated {filled} rel id(s) "
          f"({'dry-run, not written' if args.dry_run else 'writing index.json'})")
    for line in report[:40]:
        print(line)
    if len(report) > 40:
        print(f"  … and {len(report) - 40} more")
    if not args.dry_run and filled:
        INDEX.write_text(json.dumps(rows, separators=(",", ":")))


if __name__ == "__main__":
    main()
