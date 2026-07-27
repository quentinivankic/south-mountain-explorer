#!/usr/bin/env python3
"""Collapse duplicate FEDERAL parking pins in already-published geom (task #41).

The defect, from the 2026-07-27 national roll: an agency ships one facility as
several polygons and we centroid each into its own pin. NPS gave
black-canyon-of-the-gunnison-wilderness-co "East Portal Parking" 3x and
"So. Rim Visitor Ctr." 2x, centroids 100-200 m apart — far enough to survive
`PARKING_DEDUP_M` (40 m), which compares position and not identity.

Same name AND within `FED_SAME_NAME_M` is one facility. The radius is what makes
it safe: name alone is not identity, because agency names are often placeholders.
Saguaro Wilderness has NINETEEN distinct NPS lots called "Parking Lot", and
name-only dedup collapsed it 24 -> 5, destroying 18 real separate lots.

OSM lots are never touched: authoritative, already containment-gated, and none of
the observed defects were OSM.

A distance-to-trail cap was also built, measured and REJECTED — it measures our
trail coverage rather than the pin, and at any useful threshold it deletes real
trailheads (Springer Mountain, Carver's Gap, Bridge of the Gods). The full
numbers are in `add-parking.py::clean_federal_lots`; read them before
re-proposing it.

The rule lives in `add-parking.py::clean_federal_lots` and is called by both this
sweep and the parking run itself, so the two cannot drift — same arrangement as
`degenerate.py` for #30. Cleaning shipped geom directly means this lands in
minutes instead of waiting on a ~4 h national parking re-run.

    python3 scripts/sweep-federal-parking.py --dry-run
    python3 scripts/sweep-federal-parking.py
Parking is geom-only (not in the index), so no index or bundle regen is needed.
"""
from __future__ import annotations

import argparse
import glob
import importlib.util
import json
import os
import sys
from collections import Counter

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_AP = os.path.join(_ROOT, "scripts", "add-parking.py")
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
# add-parking.py has a dash, so it cannot be imported by name. Loading it by
# path is deliberate: the alternative is copying the rule here, and a copied
# curation rule is exactly what drifts.
_spec = importlib.util.spec_from_file_location("add_parking", _AP)
ap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ap)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="sanity-clean federal parking pins")
    p.add_argument("--geom-dir", default=os.path.join(_ROOT, "public", "areas", "geom"))
    p.add_argument("--same-name-m", type=float, default=ap.FED_SAME_NAME_M,
                   help="two same-named federal pins closer than this are one "
                        "facility; further apart they are distinct places that "
                        "share a placeholder name")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    reasons = Counter()
    changed: dict[str, tuple[int, int]] = {}
    examples: list[tuple[str, str, str]] = []
    lost_all: list[str] = []

    for path in sorted(glob.glob(os.path.join(args.geom_dir, "*.json"))):
        try:
            d = json.load(open(path))
        except Exception:  # noqa: BLE001
            continue
        if "cached_at" in d:
            continue
        lots = d.get("parking") or []
        if not any(l.get("source") for l in lots):
            continue                       # no federal pins here
        pts = [(la, lo) for t in (d.get("trails") or [])
               for s in (t.get("segments") or []) for la, lo in s]
        if not pts:
            continue

        kept, stats = ap.clean_federal_lots(lots, pts, same_name_m=args.same_name_m)
        if not stats:
            continue
        slug = os.path.splitext(os.path.basename(path))[0]
        reasons.update(stats)
        changed[slug] = (len(lots), len(kept))
        if not kept:
            lost_all.append(slug)
        dropped = {(l["lat"], l["lon"]) for l in lots} - {(l["lat"], l["lon"]) for l in kept}
        for l in lots:
            if (l["lat"], l["lon"]) in dropped and len(examples) < 30:
                examples.append((slug, l.get("source") or "?", l.get("name") or "?"))
        if not args.dry_run:
            if kept:
                d["parking"] = kept
            else:
                d.pop("parking", None)
            json.dump(d, open(path, "w"))

    total = sum(reasons.values())
    print(f"{'DRY-RUN — ' if args.dry_run else ''}cleaned {len(changed)} area(s), "
          f"removed {total} federal pin(s)  (same-name radius {args.same_name_m:.0f} m)")
    for r, n in reasons.most_common():
        print(f"  {n:>5}  {r}")
    print("\nexamples:")
    for slug, src, name in examples:
        print(f"  [{src:4}] {name[:42]:44s} {slug}")
    if total > 30:
        print(f"  … and {total - 30} more")
    print("\nbiggest reductions:")
    for slug, (before, after) in sorted(changed.items(),
                                        key=lambda kv: kv[1][1] - kv[1][0])[:12]:
        print(f"  {before:3d} -> {after:3d}  {slug}")
    # An area whose ONLY parking was bogus goes back to honestly blank. That is
    # the right outcome, but it is a visible loss, so name them.
    print(f"\nareas that end with NO parking at all: {len(lost_all)}")
    for slug in lost_all[:12]:
        print(f"  {slug}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
