#!/usr/bin/env python3
"""Emit Bucket B precomputed flags onto trail features (spec §4.2).

Per §4 the confidence SCORE is computed on-device, so this module is
deliberately THIN: it does not compute or bake a final score. It only
attaches the handful of flags that require build-time cross-referencing
of large datasets you don't want on the phone:

    authoritative_match   (bool)  — from conflation/match.py
    matched_source        (enum)  — nps/usfs/doc/… when matched
    in_official_whitelist (bool)  — inside an NPS/ParksCanada/DOC official boundary
    low_trust_editor      (bool)  — from OSM changeset/editor stats
    region_trust          (enum)  — small regional-trust bucket for the region

Bucket A raw signals (§4.1 — highway, informal, access, sac_scale, …)
are carried straight through from OSM by build/trails.sql and are NOT
touched here; the device scores them live.

Nothing in this file drops a trail. The only build-time exclusion is the
licensing gate (sources/validate_registry.py). This step runs AFTER the
gate and only enriches the surviving features.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Bucket B keys this module is authoritative for. Anything else on the
# feature is left untouched.
BUCKET_B_KEYS = (
    "authoritative_match",
    "matched_source",
    "in_official_whitelist",
    "low_trust_editor",
    "region_trust",
)

# Guard: score-ish keys that must NEVER be written into tiles (§4, §7).
FORBIDDEN_KEYS = ("confidence", "score", "band")


def apply_bucket_b(
    feature: dict[str, Any],
    *,
    matches: dict[str, dict[str, Any]],
    region_trust: str,
    low_trust_osm_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Return a shallow-copied feature with Bucket B flags set.

    `matches` maps osm_id -> {"matched": bool, "source": str|None,
    "whitelist": bool} as produced by conflation/match.py. Missing ids
    default to no match (fail-open is fine here: an unmatched way is just
    a lower-confidence way, never a licensing problem).
    """
    props = dict(feature.get("properties", {}))
    osm_id = str(props.get("osm_id", ""))
    m = matches.get(osm_id, {})

    props["authoritative_match"] = bool(m.get("matched", False))
    props["matched_source"] = m.get("source") if m.get("matched") else None
    props["in_official_whitelist"] = bool(m.get("whitelist", False))
    props["low_trust_editor"] = bool(osm_id in (low_trust_osm_ids or set()))
    props["region_trust"] = region_trust

    for k in FORBIDDEN_KEYS:
        props.pop(k, None)  # defensively strip any leaked score field

    out = dict(feature)
    out["properties"] = props
    return out


def apply_to_collection(
    fc: dict[str, Any],
    *,
    matches: dict[str, dict[str, Any]],
    region_trust: str,
    low_trust_osm_ids: set[str] | None = None,
) -> dict[str, Any]:
    feats = [
        apply_bucket_b(f, matches=matches, region_trust=region_trust,
                       low_trust_osm_ids=low_trust_osm_ids)
        for f in fc.get("features", [])
    ]
    return {"type": "FeatureCollection", "features": feats}


def _load(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Attach Bucket B flags to trail features (§4.2)")
    ap.add_argument("--trails", required=True, help="trails GeoJSON FeatureCollection")
    ap.add_argument("--matches", required=True,
                    help="conflation match index JSON (osm_id -> {matched,source,whitelist})")
    ap.add_argument("--region-trust", default="medium",
                    choices=["high", "medium", "low"])
    ap.add_argument("--low-trust-ids", help="optional JSON array of low-trust osm_ids")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    fc = _load(args.trails)
    matches = _load(args.matches)
    low = set(map(str, _load(args.low_trust_ids))) if args.low_trust_ids else set()

    out = apply_to_collection(fc, matches=matches, region_trust=args.region_trust,
                              low_trust_osm_ids=low)
    Path(args.out).write_text(json.dumps(out), encoding="utf-8")
    print(f"wrote {len(out['features'])} features with Bucket B flags -> {args.out}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
