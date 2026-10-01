#!/usr/bin/env python3
"""Sweep ~zero-length trails out of already-published per-area geom.

Companion to `sweep-geom-names.py`, same shape and same reason for existing: the
publisher only rewrites areas whose boundary it can assemble, so a gate added to
publish today does not reach geom published yesterday — and never reaches areas
the publisher skips outright. This sweep needs no boundary and no OSM extract, so
it cleans everything, in minutes rather than a multi-hour republish.

The classification lives in `trailforge/serve/degenerate.py` and is shared with
`publish_areas.py`, so the sweep and the publisher cannot drift. Read that
module for why connectors are kept and why the junction tolerance is 11 m.

Only touches trailforge geom (System-1 legacy carries `cached_at`; skipped).
Recomputes trail_count + total_mi and updates the main index in place. A real
write holds the shared persistent parking-sweep gate for its full read/write
phase.

    python3 scripts/sweep-degenerate-trails.py --dry-run   # report, write nothing
    python3 scripts/sweep-degenerate-trails.py             # rewrite geom + index
Then: python3 scripts/filter-ios-bundle.py ; commit public/areas + the bundle.
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
import degenerate  # noqa: E402


def _run(args) -> int:
    index = json.load(open(args.index))
    by_slug = {r[0]: r for r in index if r}

    reasons = Counter()
    examples: list[tuple[str, str, str | None, float]] = []
    changed: dict[str, tuple[int, float]] = {}
    would_empty: list[str] = []
    heavy: list[tuple[str, int, int]] = []

    for path in sorted(glob.glob(os.path.join(args.geom_dir, "*.json"))):
        try:
            d = json.load(open(path))
        except Exception:  # noqa: BLE001
            continue
        if "cached_at" in d:
            continue
        slug = os.path.splitext(os.path.basename(path))[0]
        ts = d.get("trails") or []
        if not ts:
            continue

        verdicts = degenerate.classify(ts)
        dropped = [(t, v) for t, v in zip(ts, verdicts) if v]
        if not dropped:
            continue
        if len(dropped) == len(ts):
            would_empty.append(slug)
            continue

        kept, counts = degenerate.prune(ts)
        reasons.update(counts)
        for t, v in dropped:
            if len(examples) < 40:
                examples.append((slug, v, t.get("name"), degenerate.trail_miles(t)))
        if len(dropped) / len(ts) >= 0.3:
            heavy.append((slug, len(dropped), len(ts)))

        new_mi = degenerate.area_miles(kept)
        changed[slug] = (len(kept), new_mi)
        if not args.dry_run:
            d["trails"] = kept
            d["trail_count"] = len(kept)
            d["total_mi"] = new_mi
            geom_guard.atomic_write_json(path, d)
            row = by_slug.get(slug)
            if row:
                while len(row) < 8:
                    row.append(None)
                row[5], row[6] = len(kept), new_mi

    if not args.dry_run and changed:
        geom_guard.atomic_write_json(args.index, index)

    total = sum(reasons.values())
    print(f"{'DRY-RUN — ' if args.dry_run else ''}"
          f"swept {len(changed)} area file(s), removed {total} trail(s)")
    for reason, count in reasons.most_common():
        print(f"  {count:>5}  {reason}")

    print("\nexamples (name, true geometry length):")
    for slug, reason, name, miles in examples:
        print(f"  [{reason:8}] {miles:6.4f} mi  {name!r}  ({slug})")
    if total > 40:
        print(f"  … and {total - 40} more")

    print(f"\nareas that would lose >=30% of their trails: {len(heavy)}")
    for slug, count, total_trails in sorted(heavy, key=lambda x: -(x[1] / x[2]))[:12]:
        print(f"  {count}/{total_trails}  {slug}")

    print(f"\nareas left untouched because EVERY trail is degenerate: "
          f"{len(would_empty)}")
    for slug in would_empty[:12]:
        print(f"  {slug}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="sweep ~zero-length trails from geom")
    parser.add_argument("--geom-dir", default=os.path.join(_ROOT, "public", "areas", "geom"))
    parser.add_argument("--index", default=os.path.join(_ROOT, "public", "areas", "index.json"))
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
