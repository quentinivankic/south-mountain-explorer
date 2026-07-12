#!/usr/bin/env python3
"""Flag seeded areas whose only public-land signal is an ambiguous
easement/conservation-style designation with no ownership evidence either
way — for manual review, not auto-drop.

Context: `is_quality()` already hard-excludes `access=private` and
`ownership=private` (explicit, unambiguous tags — safe to auto-act on). But
plenty of areas assert NEITHER public NOR private ownership at all — the tag
is just missing. That's genuinely ambiguous: could be a real, welcoming land
trust preserve, could be private land nobody bothered to tag. A name/operator
heuristic here has real false-positive risk in both directions (a legitimate
land trust can be LLC-structured for legal reasons; plenty of real public
land is simply under-tagged) — see the CLAUDE.md discussion this script came
out of. So this reports candidates for a human to read, rather than guessing.

A candidate is SAFE (skipped, no review needed) if either:
  - its protection_title/name uses a designation that's legally always
    public in the US regardless of tagging completeness (State Forest,
    State Park, National Forest, Wildlife Management Area, Wilderness,
    Wild Forest, National Wildlife Refuge, State Recreation Area, ...), OR
  - it has an explicit positive ownership signal (ownership=state/national/
    county/municipal/public, operator:type=government, governance=
    government_managed, or a recognized land-trust/conservancy operator).

Everything else that passed is_quality() lands in the REVIEW bucket.

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

# Legally-always-public US land-management designations. Deliberately
# NARROW and NARROW ONLY to bureaucratic category terms (not park names),
# which are far more nationally consistent than arbitrary naming — every
# state uses "State Forest"/"Wildlife Management Area"/etc. the same way,
# unlike a park's own name. "Preserve" is deliberately excluded — that word
# spans both public state preserves AND private land-trust preserves, so
# it's exactly the ambiguous case this script exists to catch.
_DEFINITIVE_PUBLIC = re.compile(
    r"\b(state forest|state park|state recreation area|state game land|"
    r"national forest|national park|national wildlife refuge|"
    r"national recreation area|wildlife management area|wild forest|"
    r"wilderness area|wilderness)\b",
    re.IGNORECASE,
)

_PUBLIC_OWNERSHIP_VALUES = {"state", "national", "county", "municipal", "public",
                            "federal", "local_authority"}

_LAND_TRUST_OPERATOR = re.compile(
    r"\b(land trust|nature conservancy|conservancy|audubon|land conservancy)\b",
    re.IGNORECASE,
)


def _has_public_signal(tags: dict) -> bool:
    if tags.get("ownership") in _PUBLIC_OWNERSHIP_VALUES:
        return True
    if tags.get("operator:type") == "government":
        return True
    if tags.get("governance") == "government_managed":
        return True
    if _LAND_TRUST_OPERATOR.search(tags.get("operator") or ""):
        return True
    return False


def _is_definitively_public(tags: dict) -> bool:
    title = tags.get("protection_title") or ""
    name = tags.get("name") or ""
    return bool(_DEFINITIVE_PUBLIC.search(title) or _DEFINITIVE_PUBLIC.search(name))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("state", help="2-letter state code, e.g. NY")
    ap.add_argument("--out", help="also write the review list to this file")
    args = ap.parse_args(argv)

    print(f"Querying Overpass for {args.state}...", file=sys.stderr)
    data = fetch_overpass(overpass_query(args.state))

    review: list[dict] = []
    safe = dropped = 0
    for el in data.get("elements", []):
        if el.get("type") not in ("relation", "way"):
            continue
        tags = el.get("tags") or {}
        if not is_quality(tags):
            dropped += 1
            continue
        if _is_definitively_public(tags) or _has_public_signal(tags):
            safe += 1
            continue
        review.append(tags)

    print(f"\n{args.state}: {safe} definitively public, {dropped} already "
          f"excluded (access/ownership=private), {len(review)} need review\n")

    lines = []
    for tags in sorted(review, key=lambda t: t.get("name") or ""):
        name = tags.get("name") or "(unnamed)"
        operator = tags.get("operator") or "(no operator tag)"
        title = tags.get("protection_title") or tags.get("protect_class") or "?"
        desc = tags.get("description") or ""
        line = f"  {name!r:55s}  operator={operator!r:35s}  title={title!r}"
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
