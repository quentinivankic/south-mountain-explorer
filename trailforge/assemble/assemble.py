#!/usr/bin/env python3
"""Assemble trail objects from an .osm.pbf → GeoJSON (SPEC.md §2).

Thin pyosmium reader around `model.assemble`. Three passes:
  pass 1: index route relations and their complete direct way references.
  pass 2: index POIs plus every trail/highway/direct-relation way's refs + tags.
  pass 3: resolve node coordinates for the referenced nodes.

pyosmium's default handler doesn't expose way-node coordinates without a
location cache, so we read node locations with a NodeLocationsForWays-style
second pass (apply_file with locations=True) — memory bounded by the AOI /
hiking subset, never the planet.

Usage:
    python3 assemble.py --in data/aoi/sedona.osm.pbf --out data/aoi/sedona.trails.geojson
"""
from __future__ import annotations

import argparse
import hashlib
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
    direct_relation_way_ids: set[int] = set()

    class RelationPass(osmium.SimpleHandler):
        def relation(self, relation):
            tags = {tag.k: tag.v for tag in relation.tags}
            if model.member_safety.normalize(tags.get("type")) != "route":
                return
            members = [(member.type, member.ref, member.role)
                       for member in relation.members]
            relations[relation.id] = {"tags": tags, "members": members}
            direct_relation_way_ids.update(
                ref for member_type, ref, _role in members
                if member_type == "w")

    RelationPass().apply_file(path)

    class FeaturePass(osmium.SimpleHandler):
        def way(self, way):
            tags = {tag.k: tag.v for tag in way.tags}
            if not (model._is_trailish(tags) or "highway" in tags
                    or way.id in direct_relation_way_ids):
                return
            node_ids = [node.ref for node in way.nodes]
            if len(node_ids) < 2:
                return
            ways[way.id] = {"tags": tags, "nodes": node_ids}
            want_nodes.update(node_ids)

        def node(self, node):
            tags = {tag.k: tag.v for tag in node.tags}
            if tags and _poi_kind(node.tags):
                pois.append({"id": node.id,
                             "coord": (node.location.lon, node.location.lat),
                             "tags": tags,
                             "name": tags.get("name")})

    FeaturePass().apply_file(path)

    # Final pass: resolve coordinates for the referenced way nodes.
    class NodePass(osmium.SimpleHandler):
        def node(self, node):
            if node.id in want_nodes:
                nodes[node.id] = (node.location.lon, node.location.lat)

    NodePass().apply_file(path)
    return nodes, ways, relations, pois


def _boundary_record(area: dict) -> dict:
    return {
        "name": area.get("name"),
        "osm_type": area.get("osm_type"),
        "osm_id": area.get("osm_id"),
    }


def _write_report(path: str | None, report: dict) -> None:
    if not path:
        return
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _pbf_identity(path: str) -> dict:
    source = Path(path)
    digest = hashlib.sha256()
    size = 0
    with source.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
    return {"sha256": digest.hexdigest(), "bytes": size}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Assemble trail objects from OSM PBF")
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--only-area", dest="only_area",
                    help="keep only trails inside area(s) whose name contains this "
                         "(case-insensitive; unions all matches). Boundaries are "
                         "assembled from the same --in PBF.")
    ap.add_argument("--require-exact-area", action="store_true",
                    help="require exactly one case-sensitive --only-area boundary "
                         "from the expected relation; never fall back to unclipped")
    ap.add_argument("--expected-area-relation-id", type=int,
                    help="relation id required by --require-exact-area")
    ap.add_argument("--report-json",
                    help="write versioned machine-readable assembly evidence")
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

    if args.require_exact_area:
        if not args.only_area:
            ap.error("--require-exact-area requires --only-area")
        if args.expected_area_relation_id is None:
            ap.error("--require-exact-area requires --expected-area-relation-id")
        if args.expected_area_relation_id <= 0:
            ap.error("--expected-area-relation-id must be positive")
        if not args.report_json:
            ap.error("--require-exact-area requires --report-json")
    elif args.expected_area_relation_id is not None:
        ap.error("--expected-area-relation-id requires --require-exact-area")

    input_pbf = _pbf_identity(args.inp) if args.require_exact_area else None
    nodes, ways, relations, pois = read_pbf(args.inp)
    print(f"read: {len(ways):,} ways, {len(relations):,} route relations, "
          f"{len(pois):,} POIs, {len(nodes):,} nodes", file=sys.stderr)
    coverage = model.coverage_stats(ways, relations, pois)

    area_objs = None
    exact_area = None
    boundary_matches: list[dict] = []
    if args.require_exact_area:
        import areas as areamod
        area_objs = areamod.assemble_areas(args.inp)
        exact_matches = areamod.select_exact(area_objs, args.only_area)
        boundary_matches = [_boundary_record(area) for area in exact_matches]
        failure = None
        if len(exact_matches) != 1:
            failure = (f"expected exactly one boundary named {args.only_area!r}; "
                       f"found {len(exact_matches)}")
        elif exact_matches[0].get("osm_type") != "relation":
            failure = (f"boundary {args.only_area!r} came from "
                       f"{exact_matches[0].get('osm_type')!r}, not a relation")
        elif int(exact_matches[0].get("osm_id") or 0) != args.expected_area_relation_id:
            failure = (f"boundary relation id {exact_matches[0].get('osm_id')!r} "
                       f"does not match expected {args.expected_area_relation_id}")
        if failure:
            _write_report(args.report_json, {
                "schema_version": 1,
                "status": "failed",
                "failure": failure,
                "exact_area_required": True,
                "area_query": args.only_area,
                "expected_area_relation_id": args.expected_area_relation_id,
                "input_pbf": input_pbf,
                "boundary_match_count": len(exact_matches),
                "boundary_matches": boundary_matches,
                "boundary": None,
                "clip_applied": False,
                "pre_clip_trail_count": None,
                "post_clip_trail_count": None,
                "assembled_trail_count": None,
                "coverage": coverage,
            })
            print(f"ERROR: exact-area validation failed: {failure}", file=sys.stderr)
            return 2
        exact_area = exact_matches[0]

    areas_arg = None
    if args.per_area_merge:
        import areas as areamod
        areas_arg = areamod.merge_areas(args.inp)
        print(f"per-area merge: scoping same-name merge to {len(areas_arg):,} "
              f"park areas", file=sys.stderr)

    removed: list = []
    ingest_dropped: list = []
    relation_member_audit: list[dict] = []
    trails = model.assemble(nodes, ways, relations, pois,
                            min_length_mi=args.min_length_mi, areas=areas_arg,
                            collect_removed=removed, region=args.region,
                            collect_ingest_dropped=ingest_dropped,
                            collect_relation_member_audit=relation_member_audit)
    features = [t.to_feature() for t in trails]
    pre_clip_count = len(features)
    clip_applied = False
    quality_report = None
    quality_removed: list[dict] = []
    qualitymod = None
    if (args.require_exact_area
            and (args.region or "").strip().lower() == "dk"):
        import quality as qualitymod
        qualitymod.prepare_feature_sources(features, trails, ways)

    area_note = ""
    if args.only_area:
        import areas as areamod
        if exact_area is not None:
            union = exact_area["geom"]
            names = {exact_area["name"]}
            selected_area_name = exact_area["name"]
        else:
            if area_objs is None:
                area_objs = areamod.assemble_areas(args.inp)
            union, names = areamod.union_matching(area_objs, args.only_area)
            selected_area_name = None
        if union is None:
            print(f"WARNING: no area matched '{args.only_area}' in {args.inp} "
                  f"— leaving trails unclipped", file=sys.stderr)
        else:
            before = len(features)
            features = areamod.clip_features_to_area(
                features, union, min_inside_mi=args.min_inside_mi,
                area_name=selected_area_name)
            clip_applied = True
            if qualitymod is not None:
                features, quality_removed, quality_report = qualitymod.curate_exact_area(
                    features, ways=ways, nodes=nodes, area_union=union,
                    area_name=selected_area_name, region=args.region,
                    relation_member_audit=relation_member_audit)
            clipped = sum(1 for f in features if f["properties"].get("clipped"))
            matched = ", ".join(sorted(n for n in names if n)) or "(unnamed)"
            area_note = (f"; clipped to '{args.only_area}' [{matched}]: "
                         f"{before} -> {len(features)} ({clipped} trimmed at boundary)")

    # Retained-route ingest truth depends on the final diagnostic-published
    # population, after exact clipping and Denmark quality disposition.
    model.reconcile_ingest_dropped(ingest_dropped, features, args.region)

    fc = {"type": "FeatureCollection", "features": features,
          "coverage": coverage}
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
                  "features": [t.to_feature() for t in removed] + quality_removed}
    removed_path.write_text(json.dumps(removed_fc), encoding="utf-8")

    # Sidecar 1a: NAMED ways a TAG gate filtered out before assembly (foot=no,
    # ski piste, road-like track, motor vehicle). Each carries a category +
    # reason so the viewer's 'ingest-filtered' layer makes a named trail
    # wrongly eaten by a tag rule visible instead of silently gone.
    ingest_path = base.with_name(base.name + ".ingest-dropped.geojson")
    ingest_path.write_text(
        json.dumps({"type": "FeatureCollection", "features": ingest_dropped}),
        encoding="utf-8")
    print(f"ingest-filtered (named, tag rules): {len(ingest_dropped)} -> {ingest_path}",
          file=sys.stderr)

    # Sidecar 1b: curation snapshot + run-to-run DIFF. Maps each trail's stable
    # `ckey` (its sorted member OSM ways — unchanged when only a rule is tuned)
    # to its verdict this run ('kept' or the removal category). If a prior
    # snapshot sits beside it (an earlier assemble of this AOI in this session),
    # emit a diff so the viewer can show ONLY what moved — newly removed, newly
    # kept, or shifted between removal reasons — instead of re-reviewing the
    # whole area. (data/ is scratch: between sessions the first run has no
    # baseline and is a full review, which is correct.)
    snap_path = base.with_name(base.name + ".curation.json")
    diff_path = base.with_name(base.name + ".curation-diff.json")
    new_snap = {}
    for f in features:
        k = f["properties"].get("ckey")
        if k:
            new_snap[k] = "kept"
    for f in removed_fc["features"]:
        k = f["properties"].get("ckey")
        if k:
            new_snap[k] = f["properties"].get("removed_category") or "removed"
    has_baseline = snap_path.exists()
    diff = {"has_baseline": has_baseline, "new_removed": [], "new_kept": [],
            "reason_changed": []}
    if has_baseline:
        try:
            old_snap = json.loads(snap_path.read_text())
        except Exception:
            old_snap = {}
        name_by_key = {f["properties"].get("ckey"): f["properties"].get("name")
                       for f in features + removed_fc["features"]}
        for k, cat in new_snap.items():
            old = old_snap.get(k)
            if old is None or old == cat:
                continue                       # brand-new way, or unchanged
            entry = {"ckey": k, "name": name_by_key.get(k)}
            if old == "kept":
                diff["new_removed"].append({**entry, "category": cat})
            elif cat == "kept":
                diff["new_kept"].append({**entry, "was": old})
            else:
                diff["reason_changed"].append({**entry, "from": old, "to": cat})
    diff_path.write_text(json.dumps(diff), encoding="utf-8")
    snap_path.write_text(json.dumps(new_snap), encoding="utf-8")
    if has_baseline:
        print(f"curation diff vs last run: {len(diff['new_removed'])} newly removed, "
              f"{len(diff['new_kept'])} newly kept, "
              f"{len(diff['reason_changed'])} reason-changed -> {diff_path}",
              file=sys.stderr)
    else:
        print(f"curation snapshot written (no baseline — full review) -> {snap_path}",
              file=sys.stderr)

    # Sidecar 2: the park-area polygons the merge/clip scope to. The viewer
    # draws them (green fills) AND its "Only trails in an area" + clip toggles
    # test trail vertices against them — WITHOUT this file every trail reads as
    # "not in an area" and the toggle hides them all. Assembled from the same
    # AOI PBF (already the source for --only-area / --per-area-merge).
    areas_path = base.with_name(base.name + ".areas.geojson")
    try:
        import areas as areamod
        from shapely.geometry import mapping as shp_mapping
        if area_objs is None:
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

    report_boundary = (_boundary_record(exact_area) if exact_area is not None else None)
    _write_report(args.report_json, {
        "schema_version": 1,
        "status": "ok",
        "failure": None,
        "exact_area_required": bool(args.require_exact_area),
        "area_query": args.only_area,
        "expected_area_relation_id": args.expected_area_relation_id,
        "input_pbf": input_pbf,
        "boundary_match_count": len(boundary_matches),
        "boundary_matches": boundary_matches,
        "boundary": report_boundary,
        "clip_applied": clip_applied,
        "pre_clip_trail_count": pre_clip_count,
        "post_clip_trail_count": len(features),
        "assembled_trail_count": len(features),
        "coverage": coverage,
        "quality": quality_report,
    })
    welded = sum(1 for f in features if f["properties"].get("welds"))
    from_rel = sum(1 for f in features if f["properties"].get("source") == "relation")
    print(f"assembled {len(features):,} trails "
          f"({from_rel:,} from relations, {welded:,} with welded spurs){area_note} -> {args.out}",
          file=sys.stderr)
    print(f"removed {len(removed) + len(quality_removed):,} trails (curation) -> {removed_path}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
