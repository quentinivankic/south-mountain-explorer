#!/usr/bin/env python3
"""Area-boundary filtering for assembled trails — the trail↔area step.

The assembler produces every trail in a rectangular AOI; this keeps only
the ones actually inside a named area (a park/preserve boundary). Area
polygons are assembled straight from the PBF with libosmium's area
assembler (via pyosmium) — it handles both closed-way areas AND
multipolygon/boundary relations (South Mountain is a `type=boundary`
relation), which `osmium export` does not reliably emit.

Matching unions every area whose name CONTAINS the query (case-insensitive)
— so "south mountain" folds the 'South Mountain Park and Preserve'
boundary AND its 'South Mountain Preserve' member polygons into one
boundary — then keeps trails whose representative point sits inside.

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


def filter_features_inside(features: list[dict[str, Any]], area_union) -> list[dict[str, Any]]:
    """Keep features whose representative point is inside the area union."""
    from shapely.geometry import shape

    kept = []
    for f in features:
        try:
            rep = shape(f["geometry"]).representative_point()
        except Exception:  # noqa: BLE001
            continue
        if area_union.covers(rep):
            kept.append(f)
    return kept
