#!/usr/bin/env python3
"""One-off: quantify how many NEW areas the way-vs-relation seed fix surfaces.

Calls fetch_state() directly (bypassing --dry-run's plain-text printer) so we
can diff candidate slugs against the CURRENT index.json and report exactly
how many are genuinely new — not just re-printing every candidate, including
ones we already have via their relation.

    python3 scripts/quantify-way-fix.py NY VT NH MA CO AZ

Run on the homelab (needs live Overpass). Prints one line per state plus a
total; --sample lets you eyeball the actual new area names.
"""
import argparse
import importlib.util
import json
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPTS_DIR))
from _seed_constants import INDEX_PATH  # noqa: E402

# seed-areas.py has a hyphen, so it can't be a plain `import` target.
_spec = importlib.util.spec_from_file_location(
    "seed_areas", _SCRIPTS_DIR / "seed-areas.py")
_seed_areas = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_seed_areas)
fetch_state = _seed_areas.fetch_state


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("states", nargs="+", help="state codes, e.g. NY VT CO")
    ap.add_argument("--sample", type=int, default=10,
                     help="how many new-area names to print per state")
    args = ap.parse_args()

    existing_slugs = set()
    if INDEX_PATH.exists():
        for row in json.loads(INDEX_PATH.read_text()):
            if row:
                existing_slugs.add(row[0])

    total_new = 0
    for state in args.states:
        rows = fetch_state(state)
        new = [row for row, _osm_id in rows if row[0] not in existing_slugs]
        total_new += len(new)
        print(f"{state}: {len(rows)} total candidates, {len(new)} NEW "
              f"(not already in index.json)")
        for row in new[: args.sample]:
            print(f"    + {row[1]}  ({row[2]})")
        if len(new) > args.sample:
            print(f"    ... and {len(new) - args.sample} more")

    print(f"\nTOTAL new areas across {len(args.states)} state(s): {total_new}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
