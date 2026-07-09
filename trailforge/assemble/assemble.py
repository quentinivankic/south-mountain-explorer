#!/usr/bin/env python3
"""Assemble trail objects from an .osm.pbf → GeoJSON (SPEC.md §2).

Thin pyosmium reader around `model.assemble`. Two passes:
  pass 1: index route relations, POI nodes, and every trailish way's
          node refs + tags.
  pass 2: resolve node coordinates for the referenced nodes.

pyosmium's default handler doesn't expose way-node coordinates without a
location cache, so we read node locations with a NodeLocationsForWays-style
second pass (apply_file with locations=True) — memory bounded by the AOI /
hiking subset, never the planet.

Usage:
    python3 assemble.py --in data/aoi/sedona.osm.pbf --out data/aoi/sedona.trails.geojson
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import model  # noqa: E402


def _poi_kind(tags) -> bool:
    for k, v in tags:
        if (k, v) in model.DESTINATION_POIS:
            return True
    return False


def read_pbf(path: str):
    import osmium

    nodes: dict[int, tuple[float, float]] = {}
    ways: dict[int, dict] = {}
    relations: dict[int, dict] = {}
    pois: list[dict] = []
    want_nodes: set[int] = set()

    class Pass1(osmium.SimpleHandler):
        def way(self, w):
            tags = {t.k: t.v for t in w.tags}
            if not (model._is_trailish(tags) or "highway" in tags):
                return
            nds = [n.ref for n in w.nodes]
            if len(nds) < 2:
                return
            ways[w.id] = {"tags": tags, "nodes": nds}
            want_nodes.update(nds)

        def relation(self, r):
            tags = {t.k: t.v for t in r.tags}
            if tags.get("type") != "route":
                return
            members = [(m.type, m.ref, m.role) for m in r.members]
            relations[r.id] = {"tags": tags, "members": members}

        def node(self, n):
            tags = {t.k: t.v for t in n.tags}
            if tags and _poi_kind(n.tags):
                pois.append({"id": n.id,
                             "coord": (n.location.lon, n.location.lat),
                             "tags": tags,
                             "name": tags.get("name")})

    Pass1().apply_file(path)

    # pass 2: resolve coordinates for the referenced way nodes.
    class Pass2(osmium.SimpleHandler):
        def node(self, n):
            if n.id in want_nodes:
                nodes[n.id] = (n.location.lon, n.location.lat)

    Pass2().apply_file(path)
    return nodes, ways, relations, pois


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Assemble trail objects from OSM PBF")
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--only-area", dest="only_area",
                    help="keep only trails inside area(s) whose name contains this "
                         "(case-insensitive; unions all matches). Boundaries are "
                         "assembled from the same --in PBF.")
    ap.add_argument("--min-length-mi", dest="min_length_mi", type=float, default=0.0,
                    help="drop assembled trails shorter than this (miles); 0 = keep all")
    ap.add_argument("--min-inside-frac", dest="min_inside_frac", type=float, default=0.25,
                    help="with --only-area, keep a trail only if more than this "
                         "fraction of its length is inside the area (default 0.25)")
    args = ap.parse_args(argv)

    nodes, ways, relations, pois = read_pbf(args.inp)
    print(f"read: {len(ways):,} ways, {len(relations):,} route relations, "
          f"{len(pois):,} POIs, {len(nodes):,} nodes", file=sys.stderr)

    trails = model.assemble(nodes, ways, relations, pois,
                            min_length_mi=args.min_length_mi)
    features = [t.to_feature() for t in trails]

    area_note = ""
    if args.only_area:
        import areas as areamod
        union, names = areamod.union_matching(
            areamod.assemble_areas(args.inp), args.only_area)
        if union is None:
            print(f"WARNING: no area matched '{args.only_area}' in {args.inp} "
                  f"— leaving trails unfiltered", file=sys.stderr)
        else:
            before = len(features)
            features = areamod.filter_features_inside(
                features, union, min_inside_frac=args.min_inside_frac)
            matched = ", ".join(sorted(n for n in names if n)) or "(unnamed)"
            area_note = (f"; inside '{args.only_area}' [{matched}]: "
                         f"{before} -> {len(features)}")

    fc = {"type": "FeatureCollection", "features": features,
          "coverage": model.coverage_stats(ways, relations, pois)}
    Path(args.out).write_text(json.dumps(fc), encoding="utf-8")

    welded = sum(1 for f in features if f["properties"].get("welds"))
    from_rel = sum(1 for f in features if f["properties"].get("source") == "relation")
    print(f"assembled {len(features):,} trails "
          f"({from_rel:,} from relations, {welded:,} with welded spurs){area_note} -> {args.out}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
