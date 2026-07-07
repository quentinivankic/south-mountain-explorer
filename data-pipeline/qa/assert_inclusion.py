#!/usr/bin/env python3
"""Post-build guard: prove no score-filter silently dropped trails (§7.1).

The spec is emphatic (§4.4, §7): EVERY legally-shippable trail ships to
R2 — `informal`, restricted `access`, and `abandoned`/`disused` lifecycle
trails included. Those are exactly the features a stray confidence filter
would wrongly drop, and dropping them corrupts fog-of-war completion math
and hides safety-relevant trails. So after building a region's trail
layer we assert a NON-ZERO count of the "risky" categories actually
survived into the output.

This does NOT assert they render in the shipped app (that's the curation
config's job) — only that they are PRESENT IN THE TILES, so curation
stays a live, no-rebuild decision (curation_mode: shipped_filter).

Also asserts no forbidden baked score field leaked into the tiles.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

FORBIDDEN_KEYS = ("confidence", "score", "band")


def audit(fc: dict[str, Any]) -> dict[str, Any]:
    feats = fc.get("features", [])
    informal = access_restricted = dead = leaked = 0
    for f in feats:
        p = f.get("properties", {})
        if str(p.get("informal", "")).lower() in {"yes", "true", "1"} or p.get("informal") is True:
            informal += 1
        if str(p.get("access", "")).lower() in {"no", "private", "discouraged"}:
            access_restricted += 1
        if str(p.get("lifecycle", "")).lower() in {"abandoned", "disused"}:
            dead += 1
        if any(k in p for k in FORBIDDEN_KEYS):
            leaked += 1
    return {
        "total": len(feats),
        "informal": informal,
        "access_restricted": access_restricted,
        "abandoned_or_disused": dead,
        "leaked_score_fields": leaked,
    }


def check(stats: dict[str, Any], *, expect_risky: bool = True) -> list[str]:
    """Return failure messages (empty == pass)."""
    fails: list[str] = []
    if stats["total"] == 0:
        fails.append("output has ZERO trails — build produced nothing")
    if stats["leaked_score_fields"] > 0:
        fails.append(
            f"{stats['leaked_score_fields']} features carry a baked score field "
            f"({'/'.join(FORBIDDEN_KEYS)}) — the score must be on-device, not in tiles (§4,§7)"
        )
    if expect_risky:
        risky = stats["informal"] + stats["access_restricted"] + stats["abandoned_or_disused"]
        if risky == 0:
            fails.append(
                "expected some informal/restricted/abandoned trails to survive into the "
                "tiles but found none — a score-filter may be wrongly dropping them (§7.1). "
                "If this region genuinely has none, pass --allow-no-risky."
            )
    return fails


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Assert tile inclusion invariants (§7.1)")
    ap.add_argument("--trails", required=True, help="built trails GeoJSON (pre-tiling)")
    ap.add_argument("--allow-no-risky", action="store_true",
                    help="permit zero informal/restricted/abandoned trails")
    args = ap.parse_args(argv)

    with open(args.trails, encoding="utf-8") as fh:
        fc = json.load(fh)
    stats = audit(fc)
    print(json.dumps(stats, indent=2))
    fails = check(stats, expect_risky=not args.allow_no_risky)
    if fails:
        print("INCLUSION CHECK FAILED:", file=sys.stderr)
        for f in fails:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print("inclusion check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
