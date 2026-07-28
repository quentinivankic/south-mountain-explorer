#!/usr/bin/env python3
"""Apply `public/areas/nonhiking-trails.json` to already-published geom.

The sidecar records trails the LANDOWNER says are not for walking, with the
evidence for each. `build-nonhiking-list.py` produces it; this applies it. Same
two-path arrangement as #30 and #41: a sweep so the fix lands in minutes on
shipped geom, and a gate in `publish_areas.py` so a republish honours it without
needing the agency dataset at publish time.

Removing a trail changes `trail_count` and `total_mi`, so this recomputes both
and patches the master index, exactly as `sweep-degenerate-trails.py` does. Run
`filter-ios-bundle.py` afterwards and commit `public/areas` plus the bundle.

    python3 scripts/sweep-nonhiking-trails.py --dry-run
    python3 scripts/sweep-nonhiking-trails.py

Reversible by design: delete an entry from the sidecar and the next publish
brings that trail back. Nothing here edits OSM's side of the pipeline.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "trailforge", "serve"))
import degenerate  # noqa: E402  — reuse its area-total math so totals agree


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="apply the non-hiking sidecar to geom")
    ap.add_argument("--geom-dir", default=os.path.join(_ROOT, "public", "areas", "geom"))
    ap.add_argument("--index", default=os.path.join(_ROOT, "public", "areas", "index.json"))
    ap.add_argument("--sidecar",
                    default=os.path.join(_ROOT, "public", "areas", "nonhiking-trails.json"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    side = json.load(open(args.sidecar))
    index = json.load(open(args.index))
    by_slug = {r[0]: r for r in index if r}

    reasons, removed, missing = Counter(), [], []
    changed: dict[str, tuple[int, float]] = {}
    for slug, wanted in sorted(side.items()):
        path = os.path.join(args.geom_dir, slug + ".json")
        if not os.path.exists(path):
            missing.append(slug)
            continue
        d = json.load(open(path))
        ts = d.get("trails") or []
        keep = [t for t in ts if t.get("id") not in wanted]
        gone = [t for t in ts if t.get("id") in wanted]
        for t in gone:
            v = wanted[t["id"]]
            reasons[v.get("reason", "?")] += 1
            removed.append((slug, t.get("name"), t.get("distanceMi"),
                            v.get("evidence")))
        # An area must never be emptied by a curation sidecar — that would make
        # it vanish from Browse entirely on the strength of an external dataset.
        if gone and not keep:
            print(f"  !! REFUSING to empty {slug} — {len(gone)} flagged, 0 would "
                  f"remain. Review the sidecar for this area.", file=sys.stderr)
            continue
        if not gone:
            continue
        new_mi = degenerate.area_miles(keep)
        changed[slug] = (len(keep), new_mi)
        if not args.dry_run:
            d["trails"] = keep
            d["trail_count"] = len(keep)
            d["total_mi"] = new_mi
            json.dump(d, open(path, "w"))
            row = by_slug.get(slug)
            if row:
                while len(row) < 8:
                    row.append(None)
                row[5], row[6] = len(keep), new_mi

    if not args.dry_run and changed:
        json.dump(index, open(args.index, "w"))

    print(f"{'DRY-RUN — ' if args.dry_run else ''}removed {sum(reasons.values())} "
          f"trail(s) from {len(changed)} area(s)")
    for r, n in reasons.most_common():
        print(f"  {n:5}  {r}")
    print(f"  total miles removed: {sum(mi or 0 for _, _, mi, _ in removed):.1f}")
    print("\nremoved:")
    for slug, name, mi, ev in sorted(removed, key=lambda r: -(r[2] or 0)):
        print(f"   {str(mi):6} mi  {str(name)[:32]:34} -> {ev}  ({slug})")
    if missing:
        print(f"\nsidecar names {len(missing)} area(s) with no geom: {missing[:5]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
