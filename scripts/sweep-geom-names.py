#!/usr/bin/env python3
"""Repeatably re-apply trailforge's name filters to published area geom.

The publisher skips areas whose OSM boundary cannot be assembled, so a filter
added today does not necessarily reach older geom. This sweep uses the shared
model predicates, recomputes trail_count/total_mi, and updates the main index.
A real write holds the shared persistent parking-sweep gate for its full
read/write phase.

    python3 scripts/sweep-geom-names.py --dry-run
    python3 scripts/sweep-geom-names.py
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import sys
from collections import Counter

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
sys.path.insert(0, os.path.join(_ROOT, "trailforge", "assemble"))
import _parking_geom_guard as geom_guard  # noqa: E402
import model  # noqa: E402


def drop_reason(name: str | None, region: str | None = None) -> str | None:
    """Return the matching shared name filter, or None to retain the trail."""
    if model.is_closed_name(name):
        return "closed"
    if model.is_thru_hike_name(name, region):
        return "thru-hike"
    if model.is_road_code_name(name):
        return "road-code"
    if name is not None and len(name.strip()) <= 2:
        return "short-name"
    if model.is_offtrail_name(name):
        return "off-trail"
    if model.is_motorized_name(name):
        return "motorized"
    if model.is_utility_corridor_name(name):
        return "utility-corridor"
    if model.is_nontrail_feature_name(name):
        return "non-trail-feature"
    if model.is_named_road_name(name):
        return "named-road"
    if model.is_grid_address_name(name):
        return "grid-address"
    return None


def _miles(segments) -> float:
    radius_miles = 3958.8
    total = 0.0
    for segment in segments or []:
        for index in range(1, len(segment)):
            first, second = segment[index - 1], segment[index]
            if len(first) < 2 or len(second) < 2:
                continue
            lat1, lon1, lat2, lon2 = map(
                math.radians, (first[0], first[1], second[0], second[1]),
            )
            h = (math.sin((lat2 - lat1) / 2) ** 2
                 + math.cos(lat1) * math.cos(lat2)
                 * math.sin((lon2 - lon1) / 2) ** 2)
            total += 2 * radius_miles * math.asin(min(1.0, math.sqrt(h)))
    return total


def _run(args) -> int:
    index = json.load(open(args.index))
    by_slug = {row[0]: row for row in index if row}
    reasons = Counter()
    removed_examples = []
    changed = {}

    for path in sorted(glob.glob(os.path.join(args.geom_dir, "*.json"))):
        try:
            document = json.load(open(path))
        except Exception:  # noqa: BLE001
            continue
        if "cached_at" in document:
            continue
        slug = os.path.splitext(os.path.basename(path))[0]
        tail = slug.rsplit("-", 1)[-1]
        region = tail if len(tail) == 2 and tail.isalpha() else None
        trails = document.get("trails") or []
        kept, dropped = [], []
        for trail in trails:
            reason = drop_reason(trail.get("name"), region)
            (dropped if reason else kept).append((trail, reason))
        if not any(reason for _, reason in dropped):
            continue
        for trail, reason in dropped:
            reasons[reason] += 1
            if len(removed_examples) < 60:
                removed_examples.append((slug, reason, trail.get("name")))
        new_trails = [trail for trail, _ in kept]
        new_mi = round(sum(_miles(trail.get("segments")) for trail in new_trails), 1)
        changed[slug] = (len(new_trails), new_mi)
        if not args.dry_run:
            document["trails"] = new_trails
            document["trail_count"] = len(new_trails)
            document["total_mi"] = new_mi
            geom_guard.atomic_write_json(path, document)
            row = by_slug.get(slug)
            if row:
                while len(row) < 8:
                    row.append(None)
                row[5], row[6] = len(new_trails), new_mi

    if not args.dry_run and changed:
        geom_guard.atomic_write_json(args.index, index)

    total = sum(reasons.values())
    print(f"{'DRY-RUN — ' if args.dry_run else ''}"
          f"swept {len(changed)} area files, removed {total} trails")
    for reason, count in reasons.most_common():
        print(f"  {count:>5}  {reason}")
    print("\nexamples:")
    for slug, reason, name in removed_examples:
        print(f"  [{reason:11}] {name!r}  ({slug})")
    if total > 60:
        print(f"  … and {total - 60} more")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="sweep name-junk from published geom")
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
