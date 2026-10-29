#!/usr/bin/env python3
"""Build a deterministic, network-free Mols pilot SVG and review record."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any, Iterable

AREA_NAME = "Nationalpark Mols Bjerge"
ATTRIBUTION = "© OpenStreetMap contributors"
WIDTH = 1400
HEIGHT = 1000
MARGIN = 48


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _atomic_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _lines(geometry: dict) -> Iterable[list[list[float]]]:
    kind = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if kind == "LineString" and isinstance(coordinates, (list, tuple)):
        yield coordinates
    elif kind == "MultiLineString" and isinstance(coordinates, (list, tuple)):
        yield from coordinates
    elif kind == "Polygon" and isinstance(coordinates, (list, tuple)):
        yield from coordinates
    elif kind == "MultiPolygon" and isinstance(coordinates, (list, tuple)):
        for polygon in coordinates:
            yield from polygon


def _valid_line(line: Any) -> bool:
    return (isinstance(line, (list, tuple)) and len(line) >= 2
            and all(isinstance(point, (list, tuple)) and len(point) >= 2
                    and isinstance(point[0], (int, float))
                    and not isinstance(point[0], bool)
                    and isinstance(point[1], (int, float))
                    and not isinstance(point[1], bool)
                    and math.isfinite(float(point[0]))
                    and math.isfinite(float(point[1]))
                    for point in line))


def _exact_boundary(areas: dict) -> dict:
    matches = [
        feature for feature in areas.get("features") or []
        if (feature.get("properties") or {}).get("name") == AREA_NAME
    ]
    if len(matches) != 1:
        raise ValueError("areas must contain exactly one Mols Bjerge boundary")
    if not list(_lines(matches[0].get("geometry") or {})):
        raise ValueError("exact boundary has no polygon rings")
    return matches[0]


def _bounds(lines: Iterable[list[list[float]]]) -> tuple[float, float, float, float]:
    points = [point for line in lines if _valid_line(line) for point in line]
    if not points:
        raise ValueError("visual extent has no finite coordinates")
    return (min(float(point[0]) for point in points),
            min(float(point[1]) for point in points),
            max(float(point[0]) for point in points),
            max(float(point[1]) for point in points))


def _projector(bounds):
    min_lon, min_lat, max_lon, max_lat = bounds
    center_lat = (min_lat + max_lat) / 2
    lon_scale = max(0.01, math.cos(math.radians(center_lat)))
    span_x = max((max_lon - min_lon) * lon_scale, 1e-9)
    span_y = max(max_lat - min_lat, 1e-9)
    scale = min((WIDTH - 2 * MARGIN) / span_x,
                (HEIGHT - 2 * MARGIN) / span_y)

    def project(point):
        lon, lat = float(point[0]), float(point[1])
        x = MARGIN + (lon - min_lon) * lon_scale * scale
        y = HEIGHT - MARGIN - (lat - min_lat) * scale
        return x, y

    return project


def _path(line, project, *, close=False) -> str:
    points = [project(point) for point in line]
    command = "M" + " L".join(f"{x:.3f},{y:.3f}" for x, y in points)
    return command + (" Z" if close else "")


def _feature_path_attributes(feature: dict, feature_index: int,
                             part_index: int) -> str:
    properties = feature.get("properties") or {}
    values = {
        "data-feature-index": feature_index,
        "data-part-index": part_index,
    }
    if feature.get("id"):
        values["data-osm-id"] = feature["id"]
    elif properties.get("osm_id"):
        prefix = "r" if properties.get("osm_type") == "relation" else "w"
        values["data-osm-id"] = f"{prefix}{properties['osm_id']}"
    if properties.get("ckey"):
        values["data-key"] = properties["ckey"]
    if properties.get("relation_ids"):
        values["data-relations"] = ",".join(
            str(value) for value in properties["relation_ids"])
    if properties.get("member_ways"):
        values["data-ways"] = ",".join(
            str(value) for value in properties["member_ways"])
    if properties.get("name"):
        values["data-name"] = properties["name"]
    return " ".join(
        f'{key}="{html.escape(str(value), quote=True)}"'
        for key, value in values.items())


def _feature_paths(document: dict, project, class_name: str) -> tuple[list[str], int]:
    out = []
    count = 0
    for feature_index, feature in enumerate(document.get("features") or []):
        geometry = feature.get("geometry") or {}
        polygon = geometry.get("type") in {"Polygon", "MultiPolygon"}
        for part_index, line in enumerate(_lines(geometry)):
            if not _valid_line(line):
                continue
            attributes = _feature_path_attributes(
                feature, feature_index, part_index)
            out.append(
                f'<path class="{class_name}" {attributes} '
                f'd="{_path(line, project, close=polygon)}"/>')
            count += 1
    return out, count


def _authority_ways(authority: dict) -> dict[int, dict]:
    ways = {}
    for relation in authority.get("relations") or []:
        for way in relation.get("direct_ways") or []:
            if way.get("status") != "present":
                continue
            prior = ways.get(way["way_id"])
            if prior is not None and prior != way:
                raise ValueError(f"authority contradicts w{way['way_id']}")
            ways[way["way_id"]] = way
    return ways


def build_svg(*, raw: dict, areas: dict, trails: dict, removed: dict,
              authority: dict, assembly: dict) -> tuple[bytes, dict]:
    boundary = _exact_boundary(areas)
    boundary_lines = list(_lines(boundary["geometry"]))
    bounds = _bounds(boundary_lines)
    project = _projector(bounds)
    raw_paths, raw_count = _feature_paths(raw, project, "raw-context")
    boundary_paths, boundary_count = _feature_paths(
        {"features": [boundary]}, project, "exact-boundary")
    trail_paths, trail_count = _feature_paths(trails, project, "emitted")
    removed_paths, removed_count = _feature_paths(removed, project, "removed")

    ways = _authority_ways(authority)
    quality = (assembly.get("quality") or {})
    audits = quality.get("relation_member_audit") or []
    safe_ids = set()
    unsafe_ids = set()
    for audit in audits:
        for member in audit.get("direct_way_members") or []:
            way_id = member.get("way_id")
            if member.get("status") == "included":
                safe_ids.add(way_id)
            elif member.get("status") == "excluded":
                unsafe_ids.add(way_id)
    safe_paths = []
    unsafe_paths = []
    for way_id in sorted(safe_ids):
        way = ways.get(way_id)
        if way and _valid_line(way.get("coordinates")):
            safe_paths.append(
                f'<path class="safe-member" data-way="{way_id}" '
                f'd="{_path(way["coordinates"], project)}"/>')
    for way_id in sorted(unsafe_ids):
        way = ways.get(way_id)
        if way and _valid_line(way.get("coordinates")):
            unsafe_paths.append(
                f'<path class="unsafe-member" data-way="{way_id}" '
                f'd="{_path(way["coordinates"], project)}"/>')

    endpoints = []
    for feature_index, feature in enumerate(trails.get("features") or []):
        properties = feature.get("properties") or {}
        if properties.get("quality_disposition") not in {
                "unresolved-relation-gap", "missing-source-way"}:
            continue
        marker_index = 0
        for line in _lines(feature.get("geometry") or {}):
            if not _valid_line(line):
                continue
            for endpoint_index, point in enumerate((line[0], line[-1])):
                x, y = project(point)
                endpoints.append(
                    f'<circle class="unresolved-endpoint" '
                    f'data-feature-index="{feature_index}" '
                    f'data-marker-index="{marker_index}" '
                    f'data-endpoint="{endpoint_index}" '
                    f'cx="{x:.3f}" cy="{y:.3f}" r="3.5"/>')
                marker_index += 1

    style = """
    <style>
      .background { fill: #f8f6ef; }
      .raw-context { fill: none; stroke: #c8c5ba; stroke-width: .45; opacity: .62; }
      .exact-boundary { fill: #7dbb6a; fill-opacity: .08; stroke: #256d31; stroke-width: 2.2; }
      .removed { fill: none; stroke: #8b7a6b; stroke-width: 1.0; stroke-dasharray: 4 3; opacity: .75; }
      .safe-member { fill: none; stroke: #2274a5; stroke-width: 1.25; opacity: .5; }
      .unsafe-member { fill: none; stroke: #d1495b; stroke-width: 3.0; stroke-dasharray: 6 3; }
      .emitted { fill: none; stroke: #6a1b9a; stroke-width: 2.0; opacity: .85; }
      .unresolved-endpoint { fill: #ffb000; stroke: #5c4100; stroke-width: 1; }
      text { font-family: -apple-system, BlinkMacSystemFont, sans-serif; fill: #222; }
      .title { font-size: 22px; font-weight: 700; }
      .legend { font-size: 13px; }
      .attribution { font-size: 12px; }
    </style>
    """
    legend = """
      <g class="legend" transform="translate(58 72)">
        <rect x="-12" y="-25" width="325" height="132" rx="7" fill="#fff" fill-opacity=".9"/>
        <text class="title" x="0" y="0">Mols Bjerge run #35 offline evidence</text>
        <text x="0" y="26">Purple: emitted OSM geometry</text>
        <text x="0" y="44">Blue: safe signed-relation members</text>
        <text x="0" y="62">Red dashed: excluded/unsafe relation members</text>
        <text x="0" y="80">Gray dashed: removals; orange: unresolved endpoints</text>
        <text x="0" y="98">Green: exact relation 7046785 boundary</text>
      </g>
    """
    content = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" '
        f'height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}" '
        'preserveAspectRatio="xMidYMid meet">',
        '<metadata>Network-free OpenStreetMap-derived QA; no external tiles or raster imagery.</metadata>',
        style,
        f'<rect class="background" width="{WIDTH}" height="{HEIGHT}"/>',
        '<g id="raw-osm-context">', *raw_paths, '</g>',
        '<g id="removed-output">', *removed_paths, '</g>',
        '<g id="safe-relation-members">', *safe_paths, '</g>',
        '<g id="emitted-output">', *trail_paths, '</g>',
        '<g id="unsafe-relation-members">', *unsafe_paths, '</g>',
        '<g id="exact-boundary">', *boundary_paths, '</g>',
        '<g id="unresolved-endpoints">', *endpoints, '</g>',
        legend,
        f'<text class="attribution" x="{MARGIN}" y="{HEIGHT - 16}">'
        f'{html.escape(ATTRIBUTION)} · ODbL · network-free SVG</text>',
        '</svg>',
    ]
    raw_svg = ("\n".join(content) + "\n").encode("utf-8")
    review = {
        "schema_version": 1,
        "artifact": "visual/mols-bjerge.svg",
        "sha256": hashlib.sha256(raw_svg).hexdigest(),
        "bytes": len(raw_svg),
        "geometry_source": "OpenStreetMap",
        "external_network_resources": False,
        "raster_or_external_tiles": False,
        "layers": {
            "raw_context_paths": raw_count,
            "exact_boundary_paths": boundary_count,
            "emitted_paths": trail_count,
            "removed_paths": removed_count,
            "safe_relation_member_paths": len(safe_paths),
            "unsafe_relation_member_paths": len(unsafe_paths),
            "unresolved_endpoint_markers": len(endpoints),
        },
        "kiro_disposition": "generated-and-structurally-validated",
        "human_disposition": "not-yet-recorded",
        "attribution": ATTRIBUTION,
    }
    return raw_svg, review


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", required=True, type=Path)
    parser.add_argument("--areas", required=True, type=Path)
    parser.add_argument("--trails", required=True, type=Path)
    parser.add_argument("--removed", required=True, type=Path)
    parser.add_argument("--relation-authority", required=True, type=Path)
    parser.add_argument("--assembly-report", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--review-out", required=True, type=Path)
    args = parser.parse_args(argv)
    raw_svg, review = build_svg(
        raw=_load(args.raw), areas=_load(args.areas),
        trails=_load(args.trails), removed=_load(args.removed),
        authority=_load(args.relation_authority),
        assembly=_load(args.assembly_report))
    _atomic_write(args.out, raw_svg)
    _atomic_write(
        args.review_out,
        (json.dumps(review, ensure_ascii=False, sort_keys=True, indent=2)
         + "\n").encode("utf-8"))
    print(json.dumps({"svg": str(args.out), **review}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
