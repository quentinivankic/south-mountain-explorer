#!/usr/bin/env python3
"""Generate dist/attributions.json per region (spec §2, §8 Attribution UI).

For each region tile, collect the `attribution` string of every source
that contributed, plus any `attribution_overrides` triggered by the
countries actually present in that region. The app renders these in the
map corner / About screen.

Invariants (spec §2):
  * `© OpenStreetMap contributors` is ALWAYS present (OSM is the
    universal backbone). Guaranteed even if a caller forgets to list osm.
  * A per-country override (e.g. Estonia/Finland CDDA text) is emitted
    only when that country is in the region's country set.
  * Only shippable sources contribute — a source that fails the licensing
    gate never appears in a tile, so it must never appear in attribution
    either. We assert that here as a second line of defense.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sources"))
from validate_registry import (  # noqa: E402
    is_shippable, load_registry, sources_by_id,
)

ALWAYS = "© OpenStreetMap contributors"


def build_attribution(registry: dict[str, Any], source_ids: list[str],
                      countries: list[str]) -> dict[str, Any]:
    """Return {region attribution strings} for the given sources+countries.

    Raises ValueError if a requested source is not shippable — that would
    be a licensing-gate violation and must stop the build, not degrade.
    """
    by_id = sources_by_id(registry)
    lines: list[str] = []
    used: list[str] = []
    country_set = {c.upper() for c in countries}

    for sid in source_ids:
        src = by_id.get(sid)
        if src is None:
            raise ValueError(f"attribution requested for unknown source '{sid}'")
        if not is_shippable(src):
            raise ValueError(
                f"attribution requested for non-shippable source '{sid}' — "
                f"licensing gate would have excluded its geometry; it must "
                f"not appear in attribution either"
            )
        base = src.get("attribution")
        if base and base not in lines:
            lines.append(base)
        used.append(sid)

        # Country-triggered overrides (e.g. CDDA EE/FI wording).
        overrides = src.get("attribution_overrides", {}) or {}
        for cc, text in overrides.items():
            if cc.upper() in country_set and text not in lines:
                lines.append(text)

    if ALWAYS not in lines:
        lines.insert(0, ALWAYS)

    return {
        "always_present": ALWAYS,
        "sources": used,
        "countries": sorted(country_set),
        "attribution": lines,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Generate a region's attribution strings")
    ap.add_argument("--registry", default=None)
    ap.add_argument("--sources", nargs="+", required=True, help="contributing source ids")
    ap.add_argument("--countries", nargs="*", default=[], help="ISO country codes in region")
    ap.add_argument("--region", required=True)
    ap.add_argument("--out", help="write JSON here; also merges into dist/attributions.json")
    args = ap.parse_args(argv)

    registry = load_registry(args.registry) if args.registry else load_registry()
    try:
        block = build_attribution(registry, args.sources, args.countries)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    payload = {args.region: block}
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
