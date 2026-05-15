#!/usr/bin/env python3
"""One-shot: normalize every entry's `state` field in
public/areas/index.json to its full state name.

Background: scripts/seed-areas.py used to only map 8 state codes
(AZ, CA, NV, UT, NM, CO, OR, WA) to full names, so areas seeded
from those states displayed as "Arizona" / "California" while
later-seeded states (AL, AK, AR, ...) fell through the
.get(state_code, state_code) and stored bare 2-letter codes.
After seed-areas.py was expanded to all 50 + DC, this one-shot
backfills the existing index so the iOS Browse tab reads
consistent full names. Hand-curated non-US entries (e.g. the
Fredericia, Denmark sample) are preserved as-is.

Run once, commit the diff. The seed script now produces full
names natively so this script is single-use.

Usage:
    python3 scripts/normalize-states.py
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX_PATH = ROOT / "public" / "areas" / "index.json"

CODE_TO_NAME = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut",
    "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida",
    "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky",
    "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio",
    "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming",
}

# Full names we accept as already-normalized.
VALID_NAMES = set(CODE_TO_NAME.values())


def main() -> None:
    data = json.loads(INDEX_PATH.read_text())

    before = Counter(r[2] for r in data)
    out: list[list] = []
    for row in data:
        state = row[2]
        if state in CODE_TO_NAME:
            row[2] = CODE_TO_NAME[state]
        out.append(row)

    out.sort(key=lambda r: (r[2], r[1]))
    INDEX_PATH.write_text(json.dumps(out, separators=(",", ":")))

    after = Counter(r[2] for r in out)
    print(f"Read  {len(data)} entries from {INDEX_PATH}")
    print(f"Wrote {len(out)} entries")
    print("\nBefore:")
    for k, v in before.most_common():
        print(f"  {k!r}: {v}")
    print("\nAfter:")
    for k, v in after.most_common():
        print(f"  {k!r}: {v}")


if __name__ == "__main__":
    main()
