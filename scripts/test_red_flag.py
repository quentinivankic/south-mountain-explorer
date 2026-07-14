#!/usr/bin/env python3
"""Regression tests for red_flag() — the seed-time private/restricted-land gate.

Plain `assert`s, no pytest dependency: run with `python3 scripts/test_red_flag.py`.
Every case is backed by a real OSM example (mostly surfaced by the #27
all-states audit, scripts/audit-easement-ownership.py). The point of this file
is to lock in the narrow, evidence-backed behavior so a future edit can't
silently re-flag legitimate public land or un-flag genuinely restricted land.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _seed_constants import red_flag  # noqa: E402


# (label, tags, expected red_flag() result)  — None means "trusted, keep".
CASES = [
    # --- Public water-supply operators we DO trust (keep) ---
    ("NYC DEP watershed (public access program)",
     {"name": "Neversink Reservoir Unit", "protection_title": "Watershed",
      "operator": "New York City Department of Environmental Protection"},
     None),
    ("Mt Tam watershed — MMWD (public hiking)",
     {"name": "Mount Tamalpais Watershed", "protection_title": "Watershed",
      "operator": "Marin Municipal Water District"},
     None),
    ("Sebago Lake Land Reserve — Portland Water District (public hiking)",
     {"name": "Sebago Lake Land Reserve", "description": "water supply watershed",
      "operator": "Portland Water District"},
     None),

    # --- Genuinely closed watersheds we DON'T whitelist (still flag) ---
    ("SF PUC Alameda Watershed (permit/closed)",
     {"name": "Alameda Watershed", "protection_title": "Watershed",
      "operator": "San Francisco PUC"},
     "water supply/watershed"),
    ("Seattle Public Utilities Tolt watershed (closed)",
     {"name": "South Fork Tolt Reservoir Watershed", "description": "watershed",
      "operator": "Seattle Public Utilities"},
     "water supply/watershed"),
    ("Providence Water Scituate Reservoir (closed)",
     {"name": "Scituate Reservoir Protection Area", "description": "watershed",
      "operator": "Providence Water Supply Board"},
     "water supply/watershed"),
    ("Small municipal reservoir buffer, no operator (plausibly closed)",
     {"name": "Town of Chester Water Supply", "description": "water supply"},
     "water supply/watershed"),

    # --- Government operator override still wins over a mine desc ---
    ("Mongaup Valley WMA — NY DEC, historical mine note",
     {"name": "Mongaup Valley Wildlife Management Area", "description": "mine",
      "operator": "New York State Department of Environmental Conservation"},
     None),

    # --- Genuinely restricted land still flags ---
    ("Bucktown LLC — private mining easement",
     {"name": "Bucktown LLC Conservation Easement", "description": "mine",
      "operator": "Bucktown LLC"},
     "mine/mining/quarry"),
    ("Private hunting club",
     {"name": "Amigo Hunting Club"},
     "hunting club/preserve"),

    # --- 'Trail(s)' in the name is an unambiguous public signal (keep) ---
    ("Name says Trails, despite a water-supply title",
     {"name": "Middletown Reservoir Trails", "protection_title": "Water Supply"},
     None),

    # --- A plain public park is never flagged ---
    ("Ordinary state park",
     {"name": "Blackwater River State Forest"},
     None),
]


def main() -> int:
    failures = []
    for label, tags, expected in CASES:
        got = red_flag(tags)
        if got != expected:
            failures.append(f"  FAIL: {label}\n        expected {expected!r}, got {got!r}")
    if failures:
        print(f"{len(failures)}/{len(CASES)} red_flag cases FAILED:")
        print("\n".join(failures))
        return 1
    print(f"OK — all {len(CASES)} red_flag cases pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
