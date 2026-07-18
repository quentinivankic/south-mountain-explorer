#!/usr/bin/env python3
"""Fan-in for the parallel whole-US publish (`trailforge-publish-us.yml`).

Each matrix job publishes its states' clean geom into an ARTIFACT (a dir of
`<slug>.json` files) and commits nothing — that's what lets the regions run in
parallel without racing the git push. This script runs in the single fan-in
job: copy every artifact geom file into `public/areas/geom/`, and refresh each
area's master-index row `trail_count` / `total_mi` FROM its geom (the geom is
the source of truth). Areas not present in any artifact — Canada, boundary-less
skips, unchanged states — are left untouched.

Each area belongs to exactly one state and each state to one region chunk, so
slugs never collide across artifacts.

    python3 scripts/merge-published-geom.py --artifacts-root artifacts
Then: python3 scripts/filter-ios-bundle.py ; commit public/areas + the bundle.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="merge per-region geom artifacts into the master")
    ap.add_argument("--artifacts-root", required=True,
                    help="dir holding the downloaded artifacts (subdirs of <slug>.json)")
    ap.add_argument("--geom-dir", default=os.path.join(_ROOT, "public", "areas", "geom"))
    ap.add_argument("--index", default=os.path.join(_ROOT, "public", "areas", "index.json"))
    args = ap.parse_args(argv)

    index = json.load(open(args.index))
    by_slug = {r[0]: r for r in index if r}
    os.makedirs(args.geom_dir, exist_ok=True)

    copied = updated = 0
    missing_row: list[str] = []
    for path in sorted(glob.glob(os.path.join(args.artifacts_root, "**", "*.json"),
                                 recursive=True)):
        try:
            d = json.load(open(path))
        except Exception:
            continue
        if not isinstance(d, dict) or "trail_count" not in d:
            continue                         # not an area geom (e.g. a stray json)
        slug = os.path.splitext(os.path.basename(path))[0]
        dst = os.path.join(args.geom_dir, f"{slug}.json")
        # Preserve the parking layer add-parking.py wrote into the shipped geom.
        # The artifact geom (rebuilt from assembly) has no parking, so a blind
        # copy would wipe every area's pins on a whole-US republish. Carry the
        # existing parking into the incoming geom before writing.
        prev_parking = None
        if os.path.exists(dst):
            try:
                prev_parking = json.load(open(dst)).get("parking")
            except Exception:
                pass
        if prev_parking:
            d["parking"] = prev_parking
            json.dump(d, open(dst, "w"))
        else:
            shutil.copyfile(path, dst)
        copied += 1
        row = by_slug.get(slug)
        if row is None:
            missing_row.append(slug)         # geom with no seeded index row
            continue
        # Pad to 7, NOT 8 — a row with no real osm_relation_id should stay a
        # 7-tuple (element absent) rather than an 8-tuple with an explicit
        # trailing null. iOS's JSONValue decoder has no null case, so ANY
        # null anywhere in the index array fails the whole-array decode —
        # found via Otter Creek State Forest never appearing in the app
        # despite correct CDN data: 524 of 3,163 bundle rows had this exact
        # 8-element-trailing-null shape, silently breaking revalidate() for
        # every user, every time, since the NY publish that introduced them.
        while len(row) < 7:
            row.append(None)
        row[5], row[6] = d.get("trail_count"), d.get("total_mi")
        updated += 1

    json.dump(index, open(args.index, "w"))
    print(f"merged {copied} geom files, refreshed {updated} index rows")
    if missing_row:
        print(f"WARNING: {len(missing_row)} geom files had no index row "
              f"(geom copied, index NOT updated): {missing_row[:12]}"
              f"{' …' if len(missing_row) > 12 else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
