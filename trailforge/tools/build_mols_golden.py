#!/usr/bin/env python3
"""Bind the Mols validation-only golden record to current OSM output."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
from typing import Any

RELATION_ID = 8350111
TRAIL_NAME = "Mols Bjerge-stien Bjergetapen"


def _point(value: Any) -> tuple[float, float] | None:
    if (not isinstance(value, (list, tuple)) or len(value) < 2
            or not all(isinstance(item, (int, float))
                       and not isinstance(item, bool)
                       and math.isfinite(float(item))
                       for item in value[:2])):
        return None
    return float(value[0]), float(value[1])


def _lines(geometry: dict):
    coordinates = geometry.get("coordinates")
    if geometry.get("type") == "LineString" and isinstance(coordinates, list):
        yield coordinates
    elif geometry.get("type") == "MultiLineString" \
            and isinstance(coordinates, list):
        yield from coordinates


def _haversine(left, right) -> float:
    lon1, lat1 = left
    lon2, lat2 = right
    radius = 3958.7613
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    value = (math.sin(dphi / 2) ** 2
             + math.cos(phi1) * math.cos(phi2) * math.sin(dlon / 2) ** 2)
    return 2 * radius * math.asin(min(1.0, math.sqrt(value)))


def _miles(lines) -> float:
    total = 0.0
    for line in lines:
        points = [_point(point) for point in line]
        if len(points) < 2 or any(point is None for point in points):
            raise ValueError("Bjergetapen geometry is incomplete")
        total += sum(_haversine(points[index], points[index + 1])
                     for index in range(len(points) - 1))
    return total


def _canonical_relation_ways(authority: dict, root_id: int) -> list[dict]:
    relations = {
        row.get("relation_id"): row
        for row in authority.get("relations") or []
        if isinstance(row, dict)
    }
    seen_relations = set()
    seen_ways = set()
    output = []

    def visit(relation_id: int) -> None:
        if relation_id in seen_relations:
            return
        seen_relations.add(relation_id)
        relation = relations.get(relation_id)
        if relation is None:
            raise ValueError(
                f"Bjergetapen hierarchy lacks relation {relation_id}")
        direct = {
            way.get("way_id"): way
            for way in relation.get("direct_ways") or []
            if isinstance(way, dict)
        }
        for member in relation.get("members") or []:
            role = str(member.get("role") or "").strip().casefold()
            if role not in {"", "main"}:
                continue
            if member.get("type") == "relation":
                visit(member.get("ref"))
            elif member.get("type") == "way" \
                    and member.get("ref") not in seen_ways:
                way_id = member.get("ref")
                seen_ways.add(way_id)
                way = direct.get(way_id)
                if way is None:
                    raise ValueError(
                        f"Bjergetapen hierarchy lacks way {way_id}")
                output.append(way)

    visit(root_id)
    return output


def build_record(template: dict, authority: dict, trails: dict,
                 source_sha: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise ValueError("source SHA must be a full Git SHA")
    relation = next((
        row for row in authority.get("relations") or []
        if row.get("relation_id") == RELATION_ID), None)
    if relation is None or (relation.get("tags") or {}).get("name") != TRAIL_NAME:
        raise ValueError("raw authority lacks exact Bjergetapen relation identity")
    matches = [
        feature for feature in trails.get("features") or []
        if (feature.get("properties") or {}).get("name") == TRAIL_NAME
        and (feature.get("properties") or {}).get("source") == "relation"
        and RELATION_ID in (
            (feature.get("properties") or {}).get("relation_ids") or [])
        and RELATION_ID in (
            (feature.get("properties") or {}).get("root_relation_ids") or [])
    ]
    if len(matches) != 1:
        raise ValueError("assembled output lacks one relation-backed Bjergetapen")
    feature = matches[0]
    preclip_lines = []
    for way in _canonical_relation_ways(authority, RELATION_ID):
        coordinates = way.get("coordinates")
        if (way.get("status") != "present"
                or not isinstance(coordinates, list)
                or len(coordinates) < 2):
            raise ValueError("Bjergetapen raw authority geometry is incomplete")
        preclip_lines.append(coordinates)
    if not preclip_lines:
        raise ValueError("Bjergetapen has no preclip source geometry")
    exact_lines = list(_lines(feature.get("geometry") or {}))
    if not exact_lines:
        raise ValueError("Bjergetapen has no exact-area output geometry")
    preclip_miles = round(_miles(preclip_lines), 6)
    exact_miles = round(_miles(exact_lines), 6)
    preclip_km = round(preclip_miles * 1.609344, 3)
    exact_km = round(exact_miles * 1.609344, 3)
    record = json.loads(json.dumps(template))
    record["osm_measurement"] = {
        "source_sha": source_sha,
        "raw_authority_sha256": (authority.get("source") or {}).get("sha256"),
        "preclip_miles": preclip_miles,
        "preclip_km": preclip_km,
        "preclip_delta_from_approximate_km": round(preclip_km - 20, 3),
        "exact_area_miles": exact_miles,
        "exact_area_km": exact_km,
        "exact_area_delta_from_approximate_km": round(exact_km - 20, 3),
        "disposition": "reported-for-human-review-no-numeric-pass-tolerance",
    }
    return record


def _atomic_write(path: Path, value: dict) -> None:
    payload = (json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True)
               + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", required=True, type=Path)
    parser.add_argument("--relation-authority", required=True, type=Path)
    parser.add_argument("--trails", required=True, type=Path)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        record = build_record(
            json.loads(args.template.read_text(encoding="utf-8")),
            json.loads(args.relation_authority.read_text(encoding="utf-8")),
            json.loads(args.trails.read_text(encoding="utf-8")),
            args.source_sha)
        _atomic_write(args.out, record)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps({
        "out": str(args.out),
        "sha256": hashlib.sha256(args.out.read_bytes()).hexdigest(),
        "measurement": record["osm_measurement"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
