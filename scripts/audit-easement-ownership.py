#!/usr/bin/env python3
"""Flag seeded areas that show an actual RED FLAG for private/restricted
land — for manual review, not auto-drop.

v1 of this script tried to REQUIRE positive proof of public ownership
(a government tag, a recognized land-trust name, a "State Forest"-style
designation) before considering an area safe. Real NY data showed that
doesn't scale: it flagged 439 areas for review, and sampling them showed
the overwhelming majority were legitimate — `Sands Point Preserve`
(operator: County of Nassau), `Spring Creek Park` (National Park Service),
a dozen `North Shore Land Alliance` preserves — all correctly public, just
using operator names or org-naming conventions (County of X, Town of X,
"Alliance", "Foundation", "Heritage Trust", ...) that no fixed keyword list
can ever fully enumerate. Proving "this operator is legitimate" is an
unbounded problem across thousands of local land trusts and municipalities.

v2 instead only flags a NARROW, evidence-based set of actual red flags —
the same signals that caught the two REAL bad examples we found (Bucktown
LLC Conservation Easement, tied to a mine; Eagle Creek Renewable Energy
Conservation Easement, same pattern):

  - description/title mentions mine/mining/quarry (industrial land)
  - title/description mentions hunting (club/preserve/lodge — private
    membership land, not public hiking)
  - title mentions water supply/watershed (often a closed municipal
    reservoir protection zone, not open to public recreation even though
    it's government-owned)

Everything else that already passed is_quality() (which requires a
protect_class whitelist match or a name keyword, and now also hard-excludes
access=private / ownership=private) is trusted by default. This flips the
burden of proof to something actually achievable: catch the identifiable
bad pattern, not enumerate every possible good one.

Usage:
    python3 scripts/audit-easement-ownership.py NY
    python3 scripts/audit-easement-ownership.py NY --out ny-review.txt

Run on the homelab (needs live Overpass). Read-only — writes nothing to
index.json; use the printed slugs to decide what (if anything) to exclude
before a real publish.
"""
from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPTS_DIR))
from _seed_constants import is_quality  # noqa: E402

# seed-areas.py has a hyphen, so it can't be a plain `import` target.
_spec = importlib.util.spec_from_file_location(
    "seed_areas", _SCRIPTS_DIR / "seed-areas.py")
_seed_areas = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_seed_areas)
overpass_query = _seed_areas.overpass_query
fetch_overpass = _seed_areas.fetch_overpass

# Each is a narrow, real-example-backed pattern — not a guess at what "bad"
# might look like in general. Checked against name + protection_title +
# description, the only free-text fields likely to carry this signal.
_RED_FLAGS = {
    "mine/mining/quarry": re.compile(r"\b(mine|mining|quarry|quarries)\b", re.IGNORECASE),
    "hunting club/preserve": re.compile(r"\bhunting\b", re.IGNORECASE),
    "water supply/watershed": re.compile(r"\b(water supply|watershed)\b", re.IGNORECASE),
}


def _red_flag(tags: dict) -> str | None:
    text = " ".join(str(tags.get(k) or "") for k in ("name", "protection_title", "description"))
    for label, pattern in _RED_FLAGS.items():
        if pattern.search(text):
            return label
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("state", help="2-letter state code, e.g. NY")
    ap.add_argument("--out", help="also write the review list to this file")
    args = ap.parse_args(argv)

    print(f"Querying Overpass for {args.state}...", file=sys.stderr)
    data = fetch_overpass(overpass_query(args.state))

    review: list[tuple[dict, str]] = []
    safe = dropped = 0
    for el in data.get("elements", []):
        if el.get("type") not in ("relation", "way"):
            continue
        tags = el.get("tags") or {}
        if not is_quality(tags):
            dropped += 1
            continue
        flag = _red_flag(tags)
        if flag is None:
            safe += 1
            continue
        review.append((tags, flag))

    print(f"\n{args.state}: {safe} trusted (no red flag), {dropped} already "
          f"excluded (access/ownership=private), {len(review)} flagged for review\n")

    lines = []
    for tags, flag in sorted(review, key=lambda t: t[0].get("name") or ""):
        name = tags.get("name") or "(unnamed)"
        operator = tags.get("operator") or "(no operator tag)"
        desc = tags.get("description") or ""
        line = f"  [{flag}]  {name!r}  operator={operator!r}"
        if desc:
            line += f"  desc={desc!r}"
        lines.append(line)
        print(line)

    if args.out:
        Path(args.out).write_text("\n".join(lines) + "\n")
        print(f"\nWrote {len(lines)} review lines to {args.out}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
