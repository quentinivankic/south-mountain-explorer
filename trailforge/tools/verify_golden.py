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
        return _snap(doc, args.snap, Path(args.golden))
    return 0


def _snap(doc: dict, pbf: str, golden_path: Path) -> int:
    """Snap each destination/endpoint to the nearest matching OSM POI.

    Golden coords are knowledge-seeded approximations. For each coordinate
    we scan destination POIs in the subset within a search radius and pick
    the nearest whose tags/name are consistent with `osm_hint` (loose
    keyword match). Writes golden.snapped.json; never mutates golden.json.
    Requires pyosmium + the hiking subset.
    """
    try:
        import osmium
    except ImportError:
        print("--snap needs pyosmium (pip install osmium)", file=sys.stderr)
        return 2
    import math

    def hav_mi(a, b):
        r = 3958.7613
        p1, p2 = math.radians(a[1]), math.radians(b[1])
        dp = math.radians(b[1] - a[1]); dl = math.radians(b[0] - a[0])
        h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        return 2 * r * math.asin(min(1.0, math.sqrt(h)))

    POI_TAGS = {"natural", "waterway", "tourism", "mountain_pass"}
    pois: list[dict] = []

    class H(osmium.SimpleHandler):
        def node(self, n):
            tags = {t.k: t.v for t in n.tags}
            if POI_TAGS & set(tags):
                pois.append({"coord": (n.location.lon, n.location.lat),
                             "tags": tags, "name": tags.get("name", "")})

    H().apply_file(pbf)
    print(f"snap: indexed {len(pois):,} POIs", file=sys.stderr)

    SEARCH_MI = 1.5
    snapped = 0
    for t in doc["trails"]:
        hint = (t.get("osm_hint", "") + " " + t["name"]).lower()
        coords = ([t["destination"]] if t["kind"] == "destination"
                  else t["endpoints"])
        for c in coords:
            here = (c["lon"], c["lat"])
            near = [(hav_mi(here, p["coord"]), p) for p in pois
                    if hav_mi(here, p["coord"]) <= SEARCH_MI]
            if not near:
                continue
            # prefer a POI whose name/tag-values appear in the hint
            def score(pr):
                d, p = pr
                name_hit = p["name"] and p["name"].lower() in hint
                tag_hit = any(v.lower() in hint for v in p["tags"].values() if v)
                return (0 if name_hit else 1 if tag_hit else 2, d)
            _, best = min(near, key=score)
            c["lon"], c["lat"] = best["coord"]
            c["_snapped_to"] = best["name"] or str(best["tags"])
            snapped += 1

    out = golden_path.with_name("golden.snapped.json")
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"snap: wrote {out.name} ({snapped} coordinates snapped)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
