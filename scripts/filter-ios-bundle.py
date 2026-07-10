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
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _seed_constants import (  # noqa: E402
    COUNTRY_CODES,
    INDEX_PATH,
    STATE_NAMES,
    code_from_slug,
)

# Regions eligible for the iOS bundle: every US state + Canadian province. This
# is the OUTER gate — the actual per-area gate is "has a clean trailforge geom
# file" (below), so a state only appears once we've published it. Publishing a
# new state therefore adds it to the bundle automatically, no edit here. The
# master index keeps every seeded region (incl. EU) as the re-enable source.
BUNDLED_REGION_CODES: set[str] = {
    code for code in STATE_NAMES
    if (code not in COUNTRY_CODES and "-" not in code)   # US state codes
    or code.startswith("CA-")                            # or CA province
}

# The real gate: ship an area ONLY if it has a clean, trailforge-published geom
# file. Fails when: (a) no geom file — seeded but never published, so shipping
# it would 404 the app; (b) the geom is System-1 output, RELIABLY marked by a
# top-level `cached_at` field that build-trail-counts.py writes and trailforge
# never does; (c) empty, or the old System-1 "Unnamed <way-id>" trail signature
# (kept as a belt-and-suspenders). Geom-driven, so the rule self-maintains:
# publish a state cleanly and its areas join the bundle on their own; areas
# trailforge can't publish (cross-state boundaries, redundant park parents,
# still-System-1 areas) stay out.
GEOM_DIR = Path(__file__).resolve().parent.parent / "public" / "areas" / "geom"
_SYS1_UNNAMED = re.compile(r"^Unnamed \d+$")


def _clean_geom(slug: str) -> dict | None:
    """The parsed geom dict if it's a clean trailforge publish, else None."""
    try:
        data = json.loads((GEOM_DIR / f"{slug}.json").read_text())
    except Exception:
        return None  # no geom file -> not published -> would 404 -> don't ship
    if "cached_at" in data:      # System-1 signature — not trailforge-published
        return None
    trails = data.get("trails") or []
    if not trails:
        return None
    if any(_SYS1_UNNAMED.match(t.get("name") or "") for t in trails):
        return None
    return data


def filter_rows(rows: list[list]) -> tuple[list[list], dict[str, int]]:
    """Return (filtered_rows, drop_counts_by_code). drop_counts_by_code
    aggregates how many rows were dropped per region code, for the workflow
    summary. Kept rows take their trail_count/total_mi from the geom file —
    the geom is authoritative for a published area, and the master index row
    may still hold stale System-1 seed counts."""
    kept: list[list] = []
    dropped: dict[str, int] = {}
    for row in rows:
        slug = row[0]
        code = code_from_slug(slug) or ""
        geom = _clean_geom(slug) if code in BUNDLED_REGION_CODES else None
        if geom is None:
            dropped[code] = dropped.get(code, 0) + 1
            continue
        row = list(row)                       # don't mutate the source row
        if len(row) > 6:                       # [id,name,state,lat,lon,count,mi,...]
            if geom.get("trail_count") is not None:
                row[5] = geom["trail_count"]
            if geom.get("total_mi") is not None:
                row[6] = geom["total_mi"]
        kept.append(row)
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
