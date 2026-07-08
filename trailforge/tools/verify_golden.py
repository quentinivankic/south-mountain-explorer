#!/usr/bin/env python3
"""Validate (and on the homelab, snap-verify) the golden-trail suite.

Two modes:

  --schema-only   Pure-stdlib structural validation. Runs anywhere,
                  including CI and the cloud sandbox (no network, no
                  OSM data needed). This is the default.

  --snap DATA.pbf Homelab mode: for each entry, find the OSM POI that
                  matches `osm_hint` near the seeded coordinate and
                  rewrite the coordinate to the real feature (writes
                  golden.snapped.json, never mutates golden.json).
                  Golden coordinates are knowledge-seeded approximations;
                  reach tests are only meaningful after snapping.
                  Requires pyosmium + the prefiltered hiking subset.

Exit codes: 0 ok, 1 validation errors, 2 usage.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

GOLDEN = Path(__file__).resolve().parents[1] / "golden" / "golden.json"

KINDS = {"destination", "through"}
CONFIDENCES = {"high", "medium", "low"}
REQUIRED = ("id", "name", "country", "kind", "osm_hint", "coord_confidence", "why")


def _is_coord(o) -> bool:
    return (isinstance(o, dict)
            and isinstance(o.get("lat"), (int, float)) and -90 <= o["lat"] <= 90
            and isinstance(o.get("lon"), (int, float)) and -180 <= o["lon"] <= 180)


def validate(doc: dict) -> list[str]:
    errs: list[str] = []
    if doc.get("version") != 1:
        errs.append("version must be 1")
    trails = doc.get("trails")
    if not isinstance(trails, list) or not trails:
        return errs + ["trails must be a non-empty list"]

    seen: set[str] = set()
    for i, t in enumerate(trails):
        tag = f"trails[{i}] ({t.get('id', '?')})"
        for k in REQUIRED:
            if not t.get(k):
                errs.append(f"{tag}: missing {k}")
        tid = t.get("id", "")
        if tid in seen:
            errs.append(f"{tag}: duplicate id")
        seen.add(tid)
        if t.get("kind") not in KINDS:
            errs.append(f"{tag}: kind must be one of {sorted(KINDS)}")
        if t.get("coord_confidence") not in CONFIDENCES:
            errs.append(f"{tag}: coord_confidence must be one of {sorted(CONFIDENCES)}")
        if t.get("kind") == "destination" and not _is_coord(t.get("destination")):
            errs.append(f"{tag}: destination must be a valid lat/lon")
        if t.get("kind") == "through":
            eps = t.get("endpoints")
            if not (isinstance(eps, list) and len(eps) == 2 and all(_is_coord(e) for e in eps)):
                errs.append(f"{tag}: through needs exactly 2 valid endpoints")
        mi = t.get("expected_one_way_mi")
        if mi is not None and not (isinstance(mi, (int, float)) and 0 < mi < 500):
            errs.append(f"{tag}: expected_one_way_mi out of range")
    return errs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Validate the golden-trail suite")
    ap.add_argument("--golden", default=str(GOLDEN))
    ap.add_argument("--schema-only", action="store_true", default=True)
    ap.add_argument("--snap", metavar="HIKING_PBF",
                    help="homelab: snap destinations to real OSM POIs from this prefiltered PBF")
    args = ap.parse_args(argv)

    doc = json.loads(Path(args.golden).read_text(encoding="utf-8"))
    errs = validate(doc)
    if errs:
        for e in errs:
            print(f"ERROR: {e}", file=sys.stderr)
        return 1
    n = len(doc["trails"])
    kinds = {}
    for t in doc["trails"]:
        kinds[t["kind"]] = kinds.get(t["kind"], 0) + 1
    print(f"golden: {n} trails valid ({kinds}); "
          f"{sum(1 for t in doc['trails'] if t['coord_confidence'] != 'high')} need snap-verify")

    if args.snap:
        # Deliberately unimplemented in the sandbox: needs pyosmium + the
        # hiking subset + (optionally) Nominatim. The homelab session
        # implements the POI match per SPEC.md and writes
        # golden.snapped.json next to golden.json.
        print("--snap: implement on the homelab (see HOMELAB.md step 3)", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
