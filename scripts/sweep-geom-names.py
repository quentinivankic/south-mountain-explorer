#!/usr/bin/env python3
"""One-time (repeatable) sweep: re-apply trailforge's NAME-based curation
filters directly to already-published per-area geom.

The publisher SKIPS any area whose OSM boundary isn't in the state extract
(BLM wilderness study areas, some preserves), leaving that area's old geom
untouched — so junk filtered out later (thru-hikes, motorized routes, road
codes, 1–2 char stubs) can linger there indefinitely. This is the belt to the
publisher's suspenders: it needs no boundary, so it reaches skipped areas.

Reuses the exact `model` predicates, so it can't drift from the assembler.
Only touches trailforge geom (System-1 legacy carries `cached_at`; skipped).
Recomputes trail_count + total_mi and updates the master index in place.

    python3 scripts/sweep-geom-names.py --dry-run   # report, write nothing
    python3 scripts/sweep-geom-names.py             # rewrite geom + master index
Then: python3 scripts/filter-ios-bundle.py ; commit public/areas + the bundle.
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
sys.path.insert(0, os.path.join(_ROOT, "trailforge", "assemble"))
import model  # noqa: E402


def drop_reason(name: str | None, region: str | None = None) -> str | None:
    """Which NAME filter fires (mirrors model.removal_reason's name checks,
    minus the source-gated generic rule and the length rule that needs geometry
    the assembler already applied). None = keep. `region` (a 2-letter state
    code, from the slug suffix) enables region-scoped thru-hike names like
    Vermont's 'Long Trail' — see model.is_thru_hike_name."""
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
    if model.is_grid_address_name(name):
        return "grid-address"
    return None


def _miles(segments) -> float:
    R = 3958.8
    tot = 0.0
    for seg in segments or []:
        for i in range(1, len(seg)):
            a, b = seg[i - 1], seg[i]
            if len(a) < 2 or len(b) < 2:
                continue
            lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
            h = (math.sin((lat2 - lat1) / 2) ** 2
                 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
            tot += 2 * R * math.asin(min(1.0, math.sqrt(h)))
    return tot


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="sweep name-junk from published geom")
    ap.add_argument("--geom-dir", default=os.path.join(_ROOT, "public", "areas", "geom"))
    ap.add_argument("--index", default=os.path.join(_ROOT, "public", "areas", "index.json"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    index = json.load(open(args.index))
    by_slug = {r[0]: r for r in index if r}
    reasons = Counter()
    removed_examples = []
    changed = {}                       # slug -> (new_count, new_mi)

    for path in sorted(glob.glob(os.path.join(args.geom_dir, "*.json"))):
        try:
            d = json.load(open(path))
        except Exception:
            continue
        if "cached_at" in d:           # System-1 legacy — leave it
            continue
        slug = os.path.splitext(os.path.basename(path))[0]
        # Slugs carry the state as a trailing '-xx' suffix (…-vt, …-nm) — the
        # region context region-scoped thru-hike names (Vermont's 'Long Trail')
        # need to avoid eating same-named local trails elsewhere.
        tail = slug.rsplit("-", 1)[-1]
        region = tail if len(tail) == 2 and tail.isalpha() else None
        ts = d.get("trails") or []
        kept, dropped = [], []
        for t in ts:
            r = drop_reason(t.get("name"), region)
            (dropped if r else kept).append((t, r))
        if not any(r for _, r in dropped):
            continue
        for t, r in dropped:
            reasons[r] += 1
            if len(removed_examples) < 60:
                removed_examples.append((slug, r, t.get("name")))
        new_trails = [t for t, _ in kept]
        new_mi = round(sum(_miles(t.get("segments")) for t in new_trails), 1)
        changed[slug] = (len(new_trails), new_mi)
        if not args.dry_run:
            d["trails"] = new_trails
            d["trail_count"] = len(new_trails)
            d["total_mi"] = new_mi
            json.dump(d, open(path, "w"))
            row = by_slug.get(slug)
            if row:
                while len(row) < 8:
                    row.append(None)
                row[5], row[6] = len(new_trails), new_mi

    if not args.dry_run and changed:
        json.dump(index, open(args.index, "w"))

    total = sum(reasons.values())
    print(f"{'DRY-RUN — ' if args.dry_run else ''}"
          f"swept {len(changed)} area files, removed {total} trails")
    for r, n in reasons.most_common():
        print(f"  {n:>5}  {r}")
    print("\nexamples:")
    for slug, r, name in removed_examples:
        print(f"  [{r:11}] {name!r}  ({slug})")
    if total > 60:
        print(f"  … and {total - 60} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
