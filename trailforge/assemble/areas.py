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


def assemble_areas(pbf_path: str) -> list[dict[str, Any]]:
    """Assemble park-ish area polygons from an OSM PBF.

    Returns [{"name", "tags", "geom" (shapely)}]. Requires a SORTED PBF —
    osmium extract output (what aoi.sh produces) always is.
    """
    import osmium
    import shapely.wkb

    wkbfab = osmium.geom.WKBFactory()
    out: list[dict[str, Any]] = []

    class Handler(osmium.SimpleHandler):
        def area(self, a):
            tags = {t.k: t.v for t in a.tags}
            if not any(k in tags for k in _AREA_KEYS):
                return
            try:
                geom = shapely.wkb.loads(wkbfab.create_multipolygon(a), hex=True)
            except Exception:  # noqa: BLE001 — skip an unassemblable area
                return
            out.append({"name": _display_name(tags), "tags": tags, "geom": geom})

    Handler().apply_file(pbf_path, locations=True)
    return out


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
                          min_inside_mi: float = 0.05) -> list[dict[str, Any]]:
    """Clip each trail to the area boundary, keeping only its in-park portion.

    The correct model for a trail that straddles the edge: a connector that
    leaves the park to reach a road keeps the piece inside the boundary and
    loses the piece outside. Trails entirely outside clip to nothing and
    drop on their own — no fraction threshold needed. A clipped trail's
    reported `length_mi` becomes its in-park length (the original is kept as
    `full_length_mi`, and `clipped: true` is flagged). A trail whose in-park
    remnant is below `min_inside_mi` — a mere boundary sliver — is dropped.
    A trail that dips out and back in becomes a MultiLineString of its
    in-park pieces.
    """
    import model
    from shapely.geometry import shape

    kept = []
    for f in features:
        try:
            clipped = shape(f["geometry"]).intersection(area_union)
        except Exception:  # noqa: BLE001 — invalid geometry; skip rather than crash
            continue
        parts = _line_parts(clipped)
        if not parts:
            continue
        lines = [[(c[0], c[1]) for c in ln.coords] for ln in parts]
        inside_mi = round(sum(model.line_mi(l) for l in lines), 3)
        if inside_mi < min_inside_mi:
            continue
        props = dict(f["properties"])
        full = props.get("length_mi")
        props["length_mi"] = inside_mi
        if full is not None and abs(full - inside_mi) > 1e-6:
            props["full_length_mi"] = full
            props["clipped"] = True
        kept.append({**f, "properties": props,
                     "geometry": {"type": "MultiLineString",
                                  "coordinates": [[list(p) for p in l] for l in lines]}})
    return kept
