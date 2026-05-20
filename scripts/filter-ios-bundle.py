#!/usr/bin/env python3
"""Filter public/areas/index.json down to the regions we ship in the
iOS bundle.

The master `public/areas/index.json` is the source of truth — every
seeded region lives there (US states, Canadian provinces, DK/IS/CH,
EU members, …). The iOS bundle at
`ios/SouthMountainExplorer/Resources/areas-index.json` is intentionally
a subset: we only ship regions whose tagging and coverage we've
manually verified, and we keep the master in sync so re-enabling a
region is a one-line edit here.

Today's shipped set: North America (US states + Canadian provinces).
The EU seed data is kept in `public/areas/` (the R2 geom + silhouette
files stay populated) but is not bundled into the app — the OSM
tagging in those regions produces too many low-signal "preserves" /
fragments that drown out the marquee destinations until we extend
NAME_KEYWORD_RE per-language and / or add a min-trail cull.

Usage:
    python3 scripts/filter-ios-bundle.py
    python3 scripts/filter-ios-bundle.py --in public/areas/index.json \\
        --out ios/SouthMountainExplorer/Resources/areas-index.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _seed_constants import (  # noqa: E402
    COUNTRY_CODES,
    INDEX_PATH,
    STATE_NAMES,
    code_from_slug,
)

# Codes whose rows ship in the iOS bundle. Computed at import time
# from STATE_NAMES so adding a new US state / CA province automatically
# flows through; the explicit logic is "everything in STATE_NAMES that
# isn't a foreign country." To re-enable a foreign region, drop its
# code from the exclusion below or replace this with an inline set.
BUNDLED_REGION_CODES: set[str] = {
    code for code in STATE_NAMES
    # US state codes: 2-letter, no hyphen, not a country.
    if (code not in COUNTRY_CODES and "-" not in code)
    # OR Canadian province / territory.
    or code.startswith("CA-")
}


def filter_rows(rows: list[list]) -> tuple[list[list], dict[str, int]]:
    """Return (filtered_rows, drop_counts_by_code). drop_counts_by_code
    aggregates how many rows were dropped per region code, for the
    workflow summary."""
    kept: list[list] = []
    dropped: dict[str, int] = {}
    for row in rows:
        code = code_from_slug(row[0]) or ""
        if code in BUNDLED_REGION_CODES:
            kept.append(row)
        else:
            dropped[code] = dropped.get(code, 0) + 1
    return kept, dropped


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--in",
        dest="src",
        default=str(INDEX_PATH),
        help="Master index.json to read.",
    )
    ap.add_argument(
        "--out",
        dest="dst",
        default="ios/SouthMountainExplorer/Resources/areas-index.json",
        help="iOS bundle path to write.",
    )
    args = ap.parse_args()

    src = Path(args.src)
    dst = Path(args.dst)
    rows = json.loads(src.read_text())
    kept, dropped = filter_rows(rows)

    dst.parent.mkdir(parents=True, exist_ok=True)
    # Match the master's formatting (compact, single-line) so the diff
    # surface in git is identical to a bare `cp` modulo the row count.
    dst.write_text(json.dumps(kept, separators=(",", ":")))

    total_dropped = sum(dropped.values())
    print(
        f"Wrote {len(kept)} rows to {dst} "
        f"(filtered from {len(rows)}; dropped {total_dropped} non-NA rows "
        f"across {len(dropped)} region codes).",
        file=sys.stderr,
    )
    if dropped:
        for code in sorted(dropped):
            print(f"  dropped {dropped[code]:>4d} from {code}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
