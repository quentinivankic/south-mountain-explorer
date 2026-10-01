#!/usr/bin/env python3
"""Fan-in for the parallel whole-US publish (``trailforge-publish-us.yml``).

Each matrix job publishes its states' clean geom into an ARTIFACT (a dir of
``<slug>.json`` files) and commits nothing. This script runs in the single
fan-in job: copy every artifact geom file into ``public/areas/geom/``, and
refresh each area's index metrics from its geom. Areas absent from artifacts
are untouched.

The canonical merge phase holds the shared persistent writer gate: the same
exclusive geom resource and shared live-journal resource as every canonical
writer. A durable PREPARED parking sweep therefore refuses this merge before
any read-dependent write, even after the sweep process has exited.

    python3 scripts/merge-published-geom.py --artifacts-root artifacts
Then: python3 scripts/filter-ios-bundle.py ; commit public/areas + the bundle.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
_TOOLS = _SCRIPTS / "parking-adjud" / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))
import _parking_geom_guard as geom_guard  # noqa: E402

_ROOT = _SCRIPTS.parent


def _merge_locked(args) -> tuple[int, int, list[str]]:
    """Perform the canonical geom/index writer phase under its caller's lock."""
    index = json.load(open(args.index))
    by_slug = {row[0]: row for row in index if row}
    os.makedirs(args.geom_dir, exist_ok=True)

    copied = updated = 0
    missing_row: list[str] = []
    for path in sorted(glob.glob(os.path.join(args.artifacts_root, "**", "*.json"),
                                 recursive=True)):
        try:
            document = json.load(open(path))
        except Exception:
            continue
        if not isinstance(document, dict) or "trail_count" not in document:
            continue                         # not an area geom (e.g. a stray json)
        slug = os.path.splitext(os.path.basename(path))[0]
        destination = os.path.join(args.geom_dir, f"{slug}.json")
        # Preserve the parking layer add-parking.py wrote into shipped geom.
        # Rebuilt artifact geom has no parking, so a blind copy would wipe pins.
        previous_parking = None
        if os.path.exists(destination):
            try:
                previous_parking = json.load(open(destination)).get("parking")
            except Exception:
                pass
        if previous_parking:
            document["parking"] = previous_parking
            geom_guard.atomic_write_json(destination, document)
        else:
            geom_guard.atomic_write_bytes(destination, Path(path).read_bytes())
        copied += 1
        row = by_slug.get(slug)
        if row is None:
            missing_row.append(slug)
            continue
        # Pad to 7, not 8: iOS's JSONValue decoder has no null case, so a real
        # missing osm_relation_id must remain an absent trailing element.
        while len(row) < 7:
            row.append(None)
        row[5], row[6] = document.get("trail_count"), document.get("total_mi")
        updated += 1

    geom_guard.atomic_write_json(args.index, index)
    return copied, updated, missing_row


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="merge per-region geom artifacts into the canonical set",
    )
    parser.add_argument(
        "--artifacts-root", required=True,
        help="dir holding downloaded artifacts (subdirs of <slug>.json)",
    )
    parser.add_argument(
        "--geom-dir", default=str(_ROOT / "public" / "areas" / "geom"),
    )
    parser.add_argument(
        "--index", default=str(_ROOT / "public" / "areas" / "index.json"),
    )
    args = parser.parse_args(argv)

    geom_resource = Path(args.geom_dir).expanduser().absolute()
    try:
        with geom_guard.geom_writer(geom_resource):
            copied, updated, missing_row = _merge_locked(args)
    except geom_guard.LiveSweepInProgress as error:
        print(f"REFUSING: {error}", file=sys.stderr)
        return 2

    print(f"merged {copied} geom files, refreshed {updated} index rows")
    if missing_row:
        print(f"WARNING: {len(missing_row)} geom files had no index row "
              f"(geom copied, index NOT updated): {missing_row[:12]}"
              f"{' …' if len(missing_row) > 12 else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
