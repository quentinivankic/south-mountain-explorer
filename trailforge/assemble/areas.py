#!/usr/bin/env python3
"""Area-boundary filtering for assembled trails — the trail↔area step.

The assembler produces every trail in a rectangular AOI; this clips them
to a named area (a park/preserve boundary), keeping each trail's in-park
portion and discarding whatever runs outside. Area
polygons are assembled straight from the PBF with libosmium's area
assembler (via pyosmium) — it handles both closed-way areas AND
multipolygon/boundary relations (South Mountain is a `type=boundary`
relation), which `osmium export` does not reliably emit.

Matching unions every area whose name CONTAINS the query (case-insensitive)
— so "south mountain" folds the 'South Mountain Park and Preserve'
boundary AND its 'South Mountain Preserve' member polygons into one
boundary — then clips trails to it, keeping each trail's in-park portion.

Needs pyosmium + shapely (both installed by the pipeline). Kept out of
model.py so the core assembly stays pure/stdlib.
"""
from __future__ import annotations

from typing import Any

# Only keep park-ish areas (skip buildings/water/etc. that the assembler
# also produces) — bounds memory in urban-fringe AOIs.
_AREA_KEYS = ("boundary", "leisure", "landuse", "protect_class", "protected_area")


def _display_name(tags: dict) -> str | None:
    if tags.get("name"):
        return tags["name"]
    for k, v in tags.items():
        if k.startswith("name:") and v:
            return v
    return None


def _is_park_area(tags: dict) -> bool:
    """A protected park/preserve/forest — the granularity that scopes a
    same-name merge. Deliberately EXCLUDES admin boundaries (counties are too
    coarse — they'd re-fuse cross-park trails) and landuse noise."""
    if tags.get("boundary") in ("national_park", "protected_area", "national_forest"):
        return True
    if tags.get("leisure") in ("nature_reserve", "park"):
        return True
    return "protect_class" in tags


def merge_areas(pbf_path: str) -> list[dict[str, Any]]:
    """Park polygons for per-area merge scoping (SPEC §6b), as plain rings so
    model.py can point-test them without shapely.

    Returns [{"name", "bbox": (x0,y0,x1,y1), "rings": [[(x,y),...], ...],
    "osm_id", "osm_type"}]. Requires a SORTED PBF (osmium/prefilter output
    always is).

    `osm_id`/`osm_type` come from `a.orig_id()` and `a.from_way()` — the way or
    relation the area was assembled from. The assembler has always known this and
    always discarded it, which is why 6,151 of 9,060 shipped areas have no
    boundary id and fall back to proximity-only parking (measured 2026-07-29).
    """
    import osmium
    import shapely.wkb

    wkbfab = osmium.geom.WKBFactory()
    out: list[dict[str, Any]] = []

    class Handler(osmium.SimpleHandler):
        def area(self, a):
            tags = {t.k: t.v for t in a.tags}
            if not _is_park_area(tags):
                return
            name = _display_name(tags)
            if not name:
                return
            try:
                geom = shapely.wkb.loads(wkbfab.create_multipolygon(a), hex=True)
            except Exception:  # noqa: BLE001 — skip an unassemblable area
                return
            polys = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
            rings = [[(x, y) for x, y in p.exterior.coords]
                     for p in polys if p.geom_type == "Polygon"]
            if rings:
                out.append({"name": name, "bbox": geom.bounds, "rings": rings,
                            "osm_id": a.orig_id(),
                            "osm_type": "way" if a.from_way() else "relation"})

    Handler().apply_file(pbf_path, locations=True)
    return out


# Denmark registry areas (dk-v2) also include woods, heaths and islands, which the
# legacy keys above never matched. Passed explicitly; the default stays legacy.
DK_EXTRA_AREA_TAGS = {"natural": {"wood", "heath"}, "place": {"island"}}
# Polygons a Denmark registry entry may name as its container by explicit id
# (registry container_by_id): a real forest / wood / nature-reserve / protected area.
DK_CONTAINER_TAGS = {"landuse": {"forest"}, "natural": {"wood"},
                     "leisure": {"nature_reserve"},
                     "boundary": {"protected_area", "national_park"}}


# Denmark registry `container_from_routes` (dk-v3 only): an area with NO usable OSM
# polygon gets a container DERIVED from the union of its include_trails route
# relations. The derivation is fixed and recorded in the sealed evidence
# (evidence_type "route-cluster") so the container cannot drift:
#   linework = every member way (nested route relations followed) of every include
#   projection = local equirectangular at the linework's mean latitude,
#                1 degree = 111,320 m (x scaled by cos(latitude))
#   container = buffer(union of linework, ROUTE_CLUSTER_BUFFER_M, 8 segments/quarter)
#   vertices rounded to 6 decimals; must be a valid polygon
ROUTE_CLUSTER_VERSION = "route-cluster-v1"
ROUTE_CLUSTER_BUFFER_M = 150
ROUTE_CLUSTER_MIN_ROUTES = 2
ROUTE_CLUSTER_ROUTE_KINDS = {"hiking", "foot"}     # route=* of an include relation
ROUTE_CLUSTER_MAX_SPAN_DEG = 0.12                  # per axis (about 13 km N-S, 7 km E-W)
ROUTE_CLUSTER_DK_LON, ROUTE_CLUSTER_DK_LAT = (6.0, 16.0), (54.0, 58.0)
_M_PER_DEG = 111320.0


def _route_cluster_linework(relations: dict, ways: dict, nodes: dict,
                            rel_id: int, seen: set) -> tuple[list, str | None]:
    """[(way id, [(lon, lat)...])] of one route relation, nested route relations
    followed; (None, reason) when any member way has missing geometry."""
    if rel_id in seen:
        return [], None
    seen.add(rel_id)
    out = []
    for kind, ref, _role in relations[rel_id]["members"]:
        if kind == "w":
            way = ways.get(ref)
            if way is None:
                return [], f"member way {ref} of route relation {rel_id} is not in the extract"
            if any(n not in nodes for n in way["nodes"]):
                return [], f"member way {ref} of route relation {rel_id} has missing nodes"
            out.append((ref, [nodes[n] for n in way["nodes"]]))
        elif kind == "r" and ref in relations:
            sub, failure = _route_cluster_linework(relations, ways, nodes, ref, seen)
            if failure:
                return [], failure
            out += sub
    return out, None


def route_cluster_container(relations: dict, ways: dict, nodes: dict,
                            include_ids: list[int], anchor_id: int,
                            name: str) -> tuple[dict | None, str | None]:
    """Denmark opt-in (registry container_from_routes): derive the container of an
    area that has no usable OSM polygon from the whole geometry of its include_trails
    routes. Fail closed (None, reason) on: fewer than two distinct routes, an
    anchor that is not one of them, a missing / non-hiking route, incomplete
    geometry, a cluster wider than ROUTE_CLUSTER_MAX_SPAN_DEG, outside Denmark or
    an invalid polygon. Returns an exact-area dict labelled with the registry name
    (osm_type/osm_id = the anchor route relation, evidence in ``route_cluster``)."""
    import hashlib
    import json
    import math
    from shapely.geometry import LineString
    from shapely.ops import transform, unary_union

    ids = sorted(set(include_ids))
    if len(ids) != len(include_ids) or len(ids) < ROUTE_CLUSTER_MIN_ROUTES:
        return None, (f"route-cluster needs at least {ROUTE_CLUSTER_MIN_ROUTES} "
                      "distinct include_trails routes")
    if anchor_id not in ids:
        return None, f"route-cluster anchor relation {anchor_id} is not an included route"
    lines: list[tuple[int, int, list]] = []
    for rid in ids:
        rel = relations.get(rid)
        if rel is None:
            return None, f"route relation {rid} is not in the extract"
        kind = (rel["tags"].get("route") or "").strip().lower()
        if kind not in ROUTE_CLUSTER_ROUTE_KINDS:
            return None, f"route relation {rid} is route={kind!r}, not a hiking/foot route"
        part, failure = _route_cluster_linework(relations, ways, nodes, rid, set())
        if failure:
            return None, failure
        if not part:
            return None, f"route relation {rid} has no member ways"
        lines += [(rid, way_id, coords) for way_id, coords in part]
    lons = [c[0] for _r, _w, cs in lines for c in cs]
    lats = [c[1] for _r, _w, cs in lines for c in cs]
    box = [min(lons), min(lats), max(lons), max(lats)]
    if (box[2] - box[0] > ROUTE_CLUSTER_MAX_SPAN_DEG
            or box[3] - box[1] > ROUTE_CLUSTER_MAX_SPAN_DEG):
        return None, (f"route-cluster is {box[2] - box[0]:.3f} x {box[3] - box[1]:.3f} "
                      f"degrees, over the {ROUTE_CLUSTER_MAX_SPAN_DEG} cap per axis")
    if not (ROUTE_CLUSTER_DK_LON[0] <= box[0] and box[2] <= ROUTE_CLUSTER_DK_LON[1]
            and ROUTE_CLUSTER_DK_LAT[0] <= box[1] and box[3] <= ROUTE_CLUSTER_DK_LAT[1]):
        return None, f"route-cluster bbox {box} is outside Denmark"
    lon0, lat0 = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    kx = _M_PER_DEG * math.cos(math.radians(lat0))

    def forward(xs, ys):
        return ([(x - lon0) * kx for x in xs], [(y - lat0) * _M_PER_DEG for y in ys])

    def inverse(xs, ys):
        return ([round(lon0 + x / kx, 6) for x in xs],
                [round(lat0 + y / _M_PER_DEG, 6) for y in ys])

    union = unary_union([transform(forward, LineString(cs)) for _r, _w, cs in lines
                         if len(cs) >= 2])
    poly = transform(inverse, union.buffer(ROUTE_CLUSTER_BUFFER_M, 8))
    if not poly.is_valid:
        # Rounding to 6 decimals can pinch a narrow neck into a self-intersection
        # (seen on a real cluster); buffer(0) is the deterministic repair. The result
        # must then still hold every route (checked below), so it can never shrink
        # onto the routes it was derived from.
        poly = poly.buffer(0)
    if poly.is_empty or not poly.is_valid or poly.geom_type not in ("Polygon", "MultiPolygon"):
        return None, "route-cluster container is not a valid polygon"
    route_lines = unary_union([LineString(cs) for _r, _w, cs in lines if len(cs) >= 2])
    if not poly.covers(route_lines):
        return None, "route-cluster container does not cover every included route"
    # Input pin (independent of GEOS): the exact route linework the container came from.
    linework = sorted([rid, way_id, [[round(x, 7), round(y, 7)] for x, y in cs]]
                      for rid, way_id, cs in lines)
    linework_sha = hashlib.sha256(json.dumps(
        linework, separators=(",", ":")).encode("utf-8")).hexdigest()
    evidence = {
        "evidence_type": "route-cluster", "version": ROUTE_CLUSTER_VERSION,
        "route_relation_ids": ids, "anchor_relation_id": anchor_id,
        "buffer_m": ROUTE_CLUSTER_BUFFER_M, "route_way_count": len({w for _r, w, _c in lines}),
        "route_linework_sha256": linework_sha,
        "linework_bbox": [round(v, 6) for v in box]}
    return {"name": name, "osm_type": "relation", "osm_id": anchor_id,
            "tags": {}, "geom": poly, "route_cluster": evidence}, None


def assemble_areas(pbf_path: str,
                   extra_tags: dict[str, set[str]] | None = None) -> list[dict[str, Any]]:
    """Assemble park-ish area polygons from an OSM PBF.

    Returns [{"name", "tags", "geom" (shapely), "osm_id", "osm_type"}].
    Requires a SORTED PBF — osmium extract output (what aoi.sh produces)
    always is.
    """
    import osmium
    import shapely.wkb

    wkbfab = osmium.geom.WKBFactory()
    out: list[dict[str, Any]] = []

    class Handler(osmium.SimpleHandler):
        def area(self, a):
            tags = {t.k: t.v for t in a.tags}
            if not (any(k in tags for k in _AREA_KEYS)
                    or any(tags.get(k) in vals for k, vals in (extra_tags or {}).items())):
                return
            try:
                geom = shapely.wkb.loads(wkbfab.create_multipolygon(a), hex=True)
            except Exception:  # noqa: BLE001 — skip an unassemblable area
                return
            out.append({
                "name": _display_name(tags),
                "tags": tags,
                "geom": geom,
                "osm_id": a.orig_id(),
                "osm_type": "way" if a.from_way() else "relation",
            })

    Handler().apply_file(pbf_path, locations=True)
    return out


def select_exact(areas: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    """Return boundaries whose display name exactly matches, case-sensitively."""
    return [area for area in areas if area.get("name") == name]


def union_matching(areas: list[dict[str, Any]], name_substr: str):
    """Union every area whose name contains `name_substr`. -> (geom|None, names)."""
    from shapely.ops import unary_union

    q = name_substr.strip().casefold()
    matched = [a for a in areas if a["name"] and q in a["name"].casefold()]
    if not matched:
        return None, set()
    return unary_union([a["geom"] for a in matched]), {a["name"] for a in matched}


def _line_parts(geom) -> list:
    """Flatten a shapely geometry to its LineString parts (drop points/polys).

    line∩polygon can yield a LineString, a MultiLineString, or a
    GeometryCollection with stray boundary-tangent Points — we want only
    the line pieces.
    """
    if geom.is_empty:
        return []
    gt = geom.geom_type
    if gt == "LineString":
        return [geom]
    if gt in ("MultiLineString", "GeometryCollection"):
        out = []
        for g in geom.geoms:
            out.extend(_line_parts(g))
        return out
    return []


def clip_features_to_area(features: list[dict[str, Any]], area_union,
                          min_inside_mi: float = 0.05,
                          area_name: str | None = None,
                          collect_dropped: list[dict[str, Any]] | None = None,
                          keep_whole: dict[int, dict[str, Any]] | None = None
                          ) -> list[dict[str, Any]]:
    """Clip each trail to the area boundary, keeping only its in-park portion.

    When the caller selected one exact boundary, ``area_name`` stamps that
    producer-owned assignment onto every surviving feature. This is done here,
    where survival is known, rather than weakening downstream validation.

    The correct model for a trail that straddles the edge: a connector that
    leaves the park to reach a road keeps the piece inside the boundary and
    loses the piece outside. Trails entirely outside clip to nothing and
    drop on their own — no fraction threshold needed. A clipped trail's
    reported `length_mi` becomes its in-park length (the original is kept as
    `full_length_mi`, and `clipped: true` is flagged). A trail whose in-park
    remnant is below `min_inside_mi` — a mere boundary sliver — is dropped.
    A trail that dips out and back in becomes a MultiLineString of its
    in-park pieces. When requested, exact-area drops retain their full source
    geometry plus typed evidence for independent final validation.

    ``keep_whole`` (Denmark dk-v3 only; {feature index: evidence}) lists
    features published with their complete geometry instead of the in-area
    remnant. They are flagged ``dk_whole_route`` and never ``clipped``; their
    coordinates are the assembled source coordinates, untouched. Default None:
    every caller outside dk-v3 behaves exactly as before.
    """
    import model
    from shapely.geometry import shape

    def record_drop(feature: dict[str, Any], *, category: str,
                    reason: str, inside_mi: float) -> None:
        if collect_dropped is None:
            return
        properties = dict(feature.get("properties") or {})
        if area_name is not None:
            properties["area"] = area_name
        properties.update({
            "removed_category": category,
            "removed_reason": reason,
            "drop_evidence": {
                "kind": category,
                "area": area_name,
                "min_inside_mi": float(min_inside_mi),
                "inside_mi": round(float(inside_mi), 12),
            },
        })
        collect_dropped.append({**feature, "properties": properties})

    kept = []
    for index, f in enumerate(features):
        if keep_whole and index in keep_whole:
            whole_props = dict(f["properties"])
            if area_name is not None:
                whole_props["area"] = area_name
            whole_props.update({
                "dk_whole_route": True,
                "dk_inside_mi": keep_whole[index]["inside_mi"],
                "dk_route_share": keep_whole[index]["share"],
            })
            if keep_whole[index].get("curated_include"):
                whole_props["dk_curated_include_id"] = \
                    keep_whole[index]["curated_include"]
            kept.append({**f, "properties": whole_props})
            continue
        try:
            source_geometry = shape(f["geometry"])
            clipped_geometry = source_geometry.intersection(area_union)
        except Exception:  # noqa: BLE001 — invalid geometry; skip rather than crash
            continue
        parts = _line_parts(clipped_geometry)
        if not parts:
            record_drop(
                f, category="outside-exact-area", inside_mi=0.0,
                reason="No line geometry intersects the exact selected area.")
            continue
        lines = [[(c[0], c[1]) for c in ln.coords] for ln in parts]
        unrounded_inside_mi = sum(model.line_mi(line) for line in lines)
        inside_mi = round(unrounded_inside_mi, 3)
        # Exact-area Denmark QA uses boundary truth before display rounding.
        # The legacy path intentionally preserves HEAD's rounded floor.
        floor_miles = unrounded_inside_mi if area_name is not None else inside_mi
        if floor_miles < min_inside_mi:
            record_drop(
                f, category="min-inside-mi", inside_mi=unrounded_inside_mi,
                reason=(f"Exact-area remnant is {inside_mi} mi, below the "
                        f"{min_inside_mi} mi inside-area minimum."))
            continue
        props = dict(f["properties"])
        if area_name is not None:
            props["area"] = area_name
        full = props.get("length_mi")
        props["length_mi"] = inside_mi
        # Decide clipping from exact geometry before display-mile rounding. A
        # real sub-rounding boundary cut must still carry clipping evidence.
        if not source_geometry.equals(clipped_geometry):
            if full is not None:
                props["full_length_mi"] = full
            props["clipped"] = True
        kept.append({**f, "properties": props,
                     "geometry": {"type": "MultiLineString",
                                  "coordinates": [[list(p) for p in line]
                                                  for line in lines]}})
    return kept
