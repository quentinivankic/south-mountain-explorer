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
    ap.add_argument("--min-inside-mi", dest="min_inside_mi", type=float, default=0.05,
                    help="with --only-area, drop a clipped trail whose in-park "
                         "remnant is shorter than this (miles); guards against "
                         "boundary slivers (default 0.05)")
    ap.add_argument("--per-area-merge", dest="per_area_merge", action="store_true",
                    help="scope same-name merge to within each park boundary. For "
                         "unclipped region/state runs, so same-named trails in "
                         "different parks don't fuse into one scattered object.")
    ap.add_argument("--region",
                    help="2-letter state code (e.g. vt, nm) — enables region-scoped "
                         "thru-hike drops whose bare name collides with unrelated "
                         "local trails elsewhere (Vermont's 'Long Trail', NM's "
                         "'Skyline Trail'). Match what the publisher ships.")
    args = ap.parse_args(argv)

    nodes, ways, relations, pois = read_pbf(args.inp)
    print(f"read: {len(ways):,} ways, {len(relations):,} route relations, "
          f"{len(pois):,} POIs, {len(nodes):,} nodes", file=sys.stderr)

    areas_arg = None
    if args.per_area_merge:
        import areas as areamod
        areas_arg = areamod.merge_areas(args.inp)
        print(f"per-area merge: scoping same-name merge to {len(areas_arg):,} "
              f"park areas", file=sys.stderr)

    removed: list = []
    trails = model.assemble(nodes, ways, relations, pois,
                            min_length_mi=args.min_length_mi, areas=areas_arg,
                            collect_removed=removed, region=args.region)
    features = [t.to_feature() for t in trails]

    area_note = ""
    if args.only_area:
        import areas as areamod
        union, names = areamod.union_matching(
            areamod.assemble_areas(args.inp), args.only_area)
        if union is None:
            print(f"WARNING: no area matched '{args.only_area}' in {args.inp} "
                  f"— leaving trails unclipped", file=sys.stderr)
        else:
            before = len(features)
            features = areamod.clip_features_to_area(
                features, union, min_inside_mi=args.min_inside_mi)
            clipped = sum(1 for f in features if f["properties"].get("clipped"))
            matched = ", ".join(sorted(n for n in names if n)) or "(unnamed)"
            area_note = (f"; clipped to '{args.only_area}' [{matched}]: "
                         f"{before} -> {len(features)} ({clipped} trimmed at boundary)")

    fc = {"type": "FeatureCollection", "features": features,
          "coverage": model.coverage_stats(ways, relations, pois)}
    Path(args.out).write_text(json.dumps(fc), encoding="utf-8")

    # Sidecar base: strip '.trails.geojson' (or '.geojson') down to the stem so
    # the sidecars land next to the trails output (vermont.trails.geojson ->
    # vermont.removed.geojson / vermont.areas.geojson).
    base = Path(args.out).with_suffix("")          # strip .geojson
    if base.suffix == ".trails":
        base = base.with_suffix("")

    # Sidecar 1: the trails curation dropped, each carrying a plain-language
    # `removed_reason`, so the QA viewer can show/hide them and explain WHY.
    # Kept separate from the trails output so publish + the app never see them.
    removed_path = base.with_name(base.name + ".removed.geojson")
    removed_fc = {"type": "FeatureCollection",
                  "features": [t.to_feature() for t in removed]}
    removed_path.write_text(json.dumps(removed_fc), encoding="utf-8")

    # Sidecar 2: the park-area polygons the merge/clip scope to. The viewer
    # draws them (green fills) AND its "Only trails in an area" + clip toggles
    # test trail vertices against them — WITHOUT this file every trail reads as
    # "not in an area" and the toggle hides them all. Assembled from the same
    # AOI PBF (already the source for --only-area / --per-area-merge).
    areas_path = base.with_name(base.name + ".areas.geojson")
    try:
        import areas as areamod
        from shapely.geometry import mapping as shp_mapping
        area_objs = areamod.assemble_areas(args.inp)
        area_feats = [{"type": "Feature",
                       "properties": {"name": a.get("name")},
                       "geometry": shp_mapping(a["geom"])}
                      for a in area_objs if a.get("geom") is not None]
    except Exception as e:
        area_feats = []
        print(f"note: could not export park areas ({e})", file=sys.stderr)
    areas_path.write_text(
        json.dumps({"type": "FeatureCollection", "features": area_feats}),
        encoding="utf-8")
    print(f"exported {len(area_feats):,} park areas -> {areas_path}", file=sys.stderr)

    welded = sum(1 for f in features if f["properties"].get("welds"))
    from_rel = sum(1 for f in features if f["properties"].get("source") == "relation")
    print(f"assembled {len(features):,} trails "
          f"({from_rel:,} from relations, {welded:,} with welded spurs){area_note} -> {args.out}",
          file=sys.stderr)
    print(f"removed {len(removed):,} trails (curation) -> {removed_path}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
