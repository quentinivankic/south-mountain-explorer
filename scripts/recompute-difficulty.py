#!/usr/bin/env python3
"""Re-label per-trail difficulty from gainFt already baked into geom.

No DEM resample is needed. Only trails with gainFt are touched; System-1 legacy
areas remain unchanged. A real write holds the shared persistent parking-sweep
gate for its full read/write phase.

    python3 scripts/recompute-difficulty.py --dry-run
    python3 scripts/recompute-difficulty.py
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from collections import Counter

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
sys.path.insert(0, os.path.join(_ROOT, "trailforge", "serve"))
import _parking_geom_guard as geom_guard  # noqa: E402
import elevation  # noqa: E402


def _run(args) -> int:
    moves = Counter()
    files_changed = 0
    trails_seen = 0
    examples = []

    for path in sorted(glob.glob(os.path.join(args.geom_dir, "*.json"))):
        try:
            document = json.load(open(path))
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(document, dict) or "cached_at" in document:
            continue
        changed = False
        for trail in document.get("trails") or []:
            gain = trail.get("gainFt")
            miles = trail.get("distanceMi")
            if gain is None or miles is None:
                continue
            trails_seen += 1
            old = trail.get("difficulty")
            new = elevation.difficulty_label(float(miles), float(gain))
            if new != old:
                moves[f"{old}->{new}"] += 1
                if len(examples) < 25:
                    examples.append((
                        round(float(gain) / max(float(miles), 0.05)), old, new,
                        trail.get("name"), os.path.basename(path)[:-5],
                    ))
                if not args.dry_run:
                    trail["difficulty"] = new
                    changed = True
        if changed and not args.dry_run:
            geom_guard.atomic_write_json(path, document)
            files_changed += 1

    total = sum(moves.values())
    print(f"{'DRY-RUN — ' if args.dry_run else ''}"
          f"{trails_seen} trails with gainFt; relabelled {total} "
          f"({(total / trails_seen * 100 if trails_seen else 0):.1f}%)"
          f"{'' if args.dry_run else f' across {files_changed} files'}")
    for move, count in moves.most_common():
        print(f"  {count:>5}  {move}")
    print("\nexamples (grade ft/mi, old -> new, name, area):")
    for grade, old, new, name, slug in sorted(examples, reverse=True):
        print(f"  {grade:>5} ft/mi  {old} -> {new}  {name!r}  ({slug})")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="recompute difficulty from baked gainFt")
    parser.add_argument("--geom-dir", default=os.path.join(_ROOT, "public", "areas", "geom"))
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
