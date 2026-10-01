#!/usr/bin/env python3
"""Apply `public/areas/nonhiking-trails.json` to published geom.

Removing a trail changes trail_count and total_mi, so this recomputes both and
updates the main index. An area is never emptied by the external sidecar. A real
write holds the shared persistent parking-sweep gate for its full read/write
phase; a PREPARED parking transaction must be recovered by rerunning its exact
sweep command first.

    python3 scripts/sweep-nonhiking-trails.py --dry-run
    python3 scripts/sweep-nonhiking-trails.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
sys.path.insert(0, os.path.join(_ROOT, "trailforge", "serve"))
import _parking_geom_guard as geom_guard  # noqa: E402
import degenerate  # noqa: E402


def _run(args) -> int:
    sidecar = json.load(open(args.sidecar))
    index = json.load(open(args.index))
    by_slug = {row[0]: row for row in index if row}

    reasons, removed, missing = Counter(), [], []
    changed: dict[str, tuple[int, float]] = {}
    for slug, wanted in sorted(sidecar.items()):
        path = os.path.join(args.geom_dir, slug + ".json")
        if not os.path.exists(path):
            missing.append(slug)
            continue
        document = json.load(open(path))
        trails = document.get("trails") or []
        keep = [trail for trail in trails if trail.get("id") not in wanted]
        gone = [trail for trail in trails if trail.get("id") in wanted]
        for trail in gone:
            verdict = wanted[trail["id"]]
            reasons[verdict.get("reason", "?")] += 1
            removed.append((
                slug, trail.get("name"), trail.get("distanceMi"),
                verdict.get("evidence"),
            ))
        if gone and not keep:
            print(
                f"  !! REFUSING to empty {slug} — {len(gone)} flagged, "
                f"0 would remain. Review the sidecar for this area.",
                file=sys.stderr,
            )
            continue
        if not gone:
            continue
        new_mi = degenerate.area_miles(keep)
        changed[slug] = (len(keep), new_mi)
        if not args.dry_run:
            document["trails"] = keep
            document["trail_count"] = len(keep)
            document["total_mi"] = new_mi
            geom_guard.atomic_write_json(path, document)
            row = by_slug.get(slug)
            if row:
                while len(row) < 8:
                    row.append(None)
                row[5], row[6] = len(keep), new_mi

    if not args.dry_run and changed:
        geom_guard.atomic_write_json(args.index, index)

    print(f"{'DRY-RUN — ' if args.dry_run else ''}removed {sum(reasons.values())} "
          f"trail(s) from {len(changed)} area(s)")
    for reason, count in reasons.most_common():
        print(f"  {count:5}  {reason}")
    print(f"  total miles removed: "
          f"{sum(miles or 0 for _, _, miles, _ in removed):.1f}")
    print("\nremoved:")
    for slug, name, miles, evidence in sorted(removed, key=lambda row: -(row[2] or 0)):
        print(f"   {str(miles):6} mi  {str(name)[:32]:34} -> {evidence}  ({slug})")
    if missing:
        print(f"\nsidecar names {len(missing)} area(s) with no geom: {missing[:5]}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="apply the non-hiking sidecar to geom")
    parser.add_argument("--geom-dir", default=os.path.join(_ROOT, "public", "areas", "geom"))
    parser.add_argument("--index", default=os.path.join(_ROOT, "public", "areas", "index.json"))
    parser.add_argument("--sidecar", default=os.path.join(
        _ROOT, "public", "areas", "nonhiking-trails.json",
    ))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.dry_run:
        return _run(args)
    try:
        with geom_guard.geom_writer(args.geom_dir):
            return _run(args)
    except geom_guard.LiveSweepInProgress as error:
        print(f"REFUSING: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
