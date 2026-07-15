#!/usr/bin/env python3
"""Re-label per-trail `difficulty` from the `gainFt` already baked into
published geom — no DEM re-sample needed.

WHY. `trailforge/serve/elevation.py::difficulty_label` gained a steepness
floor (a SHORT brutal climb like Acadia's Precipice — 966 ft in 0.67 mi —
scored "Easy" under the NPS `sqrt(2·gain·mi)` rating alone, because that
rating scales with distance). `add-elevation.py` writes difficulty during the
DEM pass, but re-running it needs Terrarium tile egress the sandbox lacks.
Since `gainFt` is already in the geom, this recomputes the label in place
using the SAME `difficulty_label`, so a formula change reaches shipped data
without touching geometry or re-sampling elevation.

Only trails that carry `gainFt` are touched (the ones the elevation pass
reached — clean trailforge geom). Trails with no gain, and System-1 legacy
areas (top-level `cached_at`), are left exactly as-is: they aren't in the app
bundle and never had a sampled gain to re-label from.

    python3 scripts/recompute-difficulty.py --dry-run   # report only
    python3 scripts/recompute-difficulty.py             # rewrite geom in place
Then: sync-geom-to-r2 (auto on push) republishes geom + rebuilds
trail-search.json, so search results pick up the new labels too.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from collections import Counter

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "trailforge", "serve"))
import elevation  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="recompute difficulty from baked gainFt")
    ap.add_argument("--geom-dir", default=os.path.join(_ROOT, "public", "areas", "geom"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    moves = Counter()
    files_changed = 0
    trails_seen = 0
    examples = []

    for path in sorted(glob.glob(os.path.join(args.geom_dir, "*.json"))):
        try:
            d = json.load(open(path))
        except Exception:
            continue
        if not isinstance(d, dict) or "cached_at" in d:
            continue                       # System-1 legacy — no sampled gain
        changed = False
        for t in d.get("trails") or []:
            g = t.get("gainFt")
            mi = t.get("distanceMi")
            if g is None or mi is None:
                continue                   # no DEM gain -> leave the label alone
            trails_seen += 1
            old = t.get("difficulty")
            new = elevation.difficulty_label(float(mi), float(g))
            if new != old:
                moves[f"{old}->{new}"] += 1
                if len(examples) < 25:
                    examples.append(
                        (round(float(g) / max(float(mi), 0.05)), old, new,
                         t.get("name"), os.path.basename(path)[:-5]))
                if not args.dry_run:
                    t["difficulty"] = new
                    changed = True
        if changed and not args.dry_run:
            json.dump(d, open(path, "w"))
            files_changed += 1

    total = sum(moves.values())
    print(f"{'DRY-RUN — ' if args.dry_run else ''}"
          f"{trails_seen} trails with gainFt; relabelled {total} "
          f"({(total / trails_seen * 100 if trails_seen else 0):.1f}%)"
          f"{'' if args.dry_run else f' across {files_changed} files'}")
    for k, n in moves.most_common():
        print(f"  {n:>5}  {k}")
    print("\nexamples (grade ft/mi, old -> new, name, area):")
    for grade, old, new, name, slug in sorted(examples, reverse=True):
        print(f"  {grade:>5} ft/mi  {old} -> {new}  {name!r}  ({slug})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
