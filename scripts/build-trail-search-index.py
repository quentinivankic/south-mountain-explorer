#!/usr/bin/env python3
"""Build the global trail-name search index for the app's Browse search.

The app's lightweight areas-index.json carries AREA names only, so trail-name
search (BrowseView.trailResults -> AreaDataService.trailSearchHits) can only
match trails whose area geom is already cached — a trail isn't findable until
you've opened its park. Fine when Arizona-only; a real discoverability gap at
~9,500 areas.

This emits a compact GLOBAL index — one `[trailName, areaId, trailId]` per
trail across the shipped (iOS-bundle) area set — so the app can search every
trail name and fetch that area's geom on tap. Area name/state are NOT repeated
here: the app joins areaId to its existing summaries, keeping this lean.

Generated FROM the same area set the app ships (the iOS bundle), so every hit
maps to a navigable area. Served from R2 like index.json (regenerated fresh in
sync-geom-to-r2 so it never drifts from the geom).

    python3 scripts/build-trail-search-index.py --out /tmp/trail-search.json
"""
from __future__ import annotations

import argparse
import json
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BUNDLE = os.path.join(_ROOT, "ios", "SouthMountainExplorer", "Resources", "areas-index.json")
_GEOM = os.path.join(_ROOT, "public", "areas", "geom")


def build(bundle_path: str, geom_dir: str) -> list[list]:
    bundle = json.load(open(bundle_path))
    slugs = [r[0] for r in bundle if r]
    out: list[list] = []
    for slug in slugs:
        try:
            g = json.load(open(os.path.join(geom_dir, f"{slug}.json")))
        except Exception:
            continue
        if not isinstance(g, dict) or "cached_at" in g:
            continue
        for t in g.get("trails", []):
            nm, tid = t.get("name"), t.get("id")
            if nm and tid:
                # [name, areaId, trailId, distanceMi, difficulty] — distance +
                # difficulty included so a global search result shows full
                # detail (uniform with an already-loaded-area hit) without
                # first fetching the area geom. Area name/state still joined
                # client-side from summaries.
                out.append([nm, slug, tid, t.get("distanceMi"), t.get("difficulty")])
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bundle", default=_BUNDLE, help="iOS bundle areas-index.json (the shipped area set)")
    ap.add_argument("--geom-dir", default=_GEOM)
    ap.add_argument("--out", required=True, help="output path (R2-served, not committed)")
    args = ap.parse_args(argv)

    entries = build(args.bundle, args.geom_dir)
    json.dump(entries, open(args.out, "w"), separators=(",", ":"))
    size = os.path.getsize(args.out)
    print(f"{len(entries):,} trail entries -> {args.out} ({size/1_048_576:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
