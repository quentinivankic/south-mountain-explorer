#!/usr/bin/env python3
"""Fail-closed validation for the runner-local Mols Bjerge QA pilot."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

_ASSEMBLE_DIR = Path(__file__).resolve().parents[1] / "assemble"
if str(_ASSEMBLE_DIR) not in sys.path:
    sys.path.insert(0, str(_ASSEMBLE_DIR))
import member_safety  # noqa: E402

AREA_ID = "nationalpark-mols-bjerge-dk"
AREA_NAME = "Nationalpark Mols Bjerge"
STATE = "Denmark"
ATTRIBUTION = "© OpenStreetMap contributors"
VISUAL_REVIEW_URL = "http://localhost:8000/viewer/?aoi=mols-bjerge"
_UNNAMED = re.compile(r"Unnamed \d+")
OVERLAP_THRESHOLD = 0.90
RESTORED_ROAD_MAX_RELATION_SHARE = 0.10
RESTORED_ROAD_MAX_RELATION_MILES = 1.0
RESTORED_ROAD_MAX_AGGREGATE_SHARE = 0.10
_DECISIVE_TAGS = member_safety.DECISIVE_TAG_KEYS
RETAINED_ROUTE_INGEST_CATEGORY = "retained-signed-route-member"
RELATION_MEMBERS_RELATIVE = "data/aoi/mols-bjerge.relation-members.json"
_QUALITY_REMOVAL_CATEGORIES = {
    "disconnected-name-stitch",
    "dk-unqualified-road-track",
    "nested-name-stitch-overlap",
}
_ALLOWED_RELATION_REASONS = {
    "shared-osm-node", "boundary-induced-split",
}
_ALLOWED_REMAINING_OVERLAPS = {
    "preserved-shared-signed-routes",
    "preserved-explicit-walking-or-path",
    "preserved-nonweak-overlap",
}

REQUIRED_JSON_FILES = {
    "manifest": "manifest.json",
    "relation_members": RELATION_MEMBERS_RELATIVE,
    "raw": "data/aoi/mols-bjerge.raw.geojson",
    "trails": "data/aoi/mols-bjerge.trails.geojson",
    "removed": "data/aoi/mols-bjerge.removed.geojson",
    "ingest_dropped": "data/aoi/mols-bjerge.ingest-dropped.geojson",
    "areas": "data/aoi/mols-bjerge.areas.geojson",
    "curation": "data/aoi/mols-bjerge.curation.json",
    "curation_diff": "data/aoi/mols-bjerge.curation-diff.json",
    "selected_discovery": "discovery/selected.json",
    "discovery_report": "discovery/report.json",
    "assembly_report": "reports/assembly.json",
    "publish_report": "reports/publish.json",
}
REQUIRED_TEXT_FILES = {
    "readme": "README.md",
    "discovery_log": "discovery/discovery.log",
    "publish_log": "reports/publish.log",
    "viewer_index": "viewer/index.html",
    "viewer_server": "viewer/serve.py",
}
OPTIONAL_DROPPED_ROUTES = "data/aoi/mols-bjerge.dropped-routes.geojson"


def _positive_decimal(value: str) -> int | None:
    if not re.fullmatch(r"[0-9]+", value or ""):
        return None
    parsed = int(value)
    return parsed if parsed > 0 else None


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _load_json(path: Path, label: str, errors: list[str]) -> Any:
    if not path.is_file():
        errors.append(f"missing {label}: {path}")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        errors.append(f"invalid {label}: {error}")
        return None


def _number(value: Any) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(float(value)))


def _candidate_shape_errors(record: Any, label: str) -> list[str]:
    errors = []
    if not isinstance(record, dict):
        return [f"{label} is not an object"]
    row = record.get("index_row")
    if not isinstance(row, list) or len(row) != 5:
        return [f"{label}.index_row must contain exactly 5 values"]
    if not all(isinstance(row[index], str) and row[index].strip()
               for index in (0, 1, 2)):
        errors.append(f"{label}.index_row identity fields are invalid")
    if row[2] != STATE:
        errors.append(f"{label} state must be {STATE!r}")
    if not _number(row[3]) or not -90 <= float(row[3]) <= 90:
        errors.append(f"{label} latitude is invalid")
    if not _number(row[4]) or not -180 <= float(row[4]) <= 180:
        errors.append(f"{label} longitude is invalid")
    relation_id = record.get("osm_relation_id")
    if relation_id is not None and (
            not isinstance(relation_id, int) or isinstance(relation_id, bool)
            or relation_id <= 0):
        errors.append(f"{label} relation id is invalid")
    return errors


def _target_candidate_errors(record: Any, expected_relation_id: int,
                             label: str) -> list[str]:
    errors = _candidate_shape_errors(record, label)
    if errors:
        return errors
    row = record["index_row"]
    if row[0] != AREA_ID:
        errors.append(f"{label} area id must be {AREA_ID!r}")
    if row[1] != AREA_NAME:
        errors.append(f"{label} name must be {AREA_NAME!r}")
    if row[2] != STATE:
        errors.append(f"{label} state must be {STATE!r}")
    if record.get("osm_relation_id") != expected_relation_id:
        errors.append(
            f"{label} relation id must equal {expected_relation_id}")
    return errors


def _validate_discovery_document(document: Any, expected_relation_id: int,
                                 errors: list[str]) -> dict | None:
    if not isinstance(document, dict):
        errors.append("discovery report must be an object")
        return None
    if document.get("schema_version") != 1:
        errors.append("discovery report schema_version must be 1")
    if document.get("requested_region_codes") != ["DK"]:
        errors.append("discovery report must cover only DK")
    if document.get("attribution") != ATTRIBUTION:
        errors.append("discovery report attribution is invalid")
    candidates = document.get("candidates")
    if not isinstance(candidates, list):
        errors.append("discovery report candidates must be a list")
        return None
    if document.get("candidate_count") != len(candidates):
        errors.append("discovery report candidate_count does not match candidates")
    for index, candidate in enumerate(candidates):
        errors.extend(_candidate_shape_errors(candidate, f"candidate[{index}]"))
    targets = [
        candidate for candidate in candidates
        if isinstance(candidate, dict)
        and isinstance(candidate.get("index_row"), list)
        and candidate["index_row"]
        and candidate["index_row"][0] == AREA_ID
    ]
    if len(targets) != 1:
        errors.append(
            f"expected exactly one discovery candidate for {AREA_ID}; found {len(targets)}")
        return None
    errors.extend(_target_candidate_errors(
        targets[0], expected_relation_id, "selected discovery"))
    return targets[0]


def _run_discovery(args: argparse.Namespace) -> int:
    errors: list[str] = []
    expected = _positive_decimal(args.expected_osm_relation_id)
    if expected is None:
        errors.append("expected relation id must be a positive decimal")
    document = _load_json(Path(args.discovery_report), "discovery report", errors)
    selected = None
    if expected is not None and document is not None:
        selected = _validate_discovery_document(document, expected, errors)

    result = {
        "schema_version": 1,
        "stage": "discovery",
        "status": "failed" if errors else "ok",
        "area_id": AREA_ID,
        "expected_osm_relation_id": expected,
        "errors": errors,
    }
    if not errors and selected is not None:
        selected_record = {
            "schema_version": 1,
            "index_row": selected["index_row"],
            "osm_relation_id": selected["osm_relation_id"],
            "attribution": ATTRIBUTION,
        }
        publisher_row = [*selected["index_row"], None, None,
                         selected["osm_relation_id"]]
        try:
            _write_json(Path(args.selected_record_out), selected_record)
            _write_json(Path(args.index_out), [publisher_row])
            result["selected_record"] = selected_record
        except OSError as error:
            errors.append(f"could not write discovery outputs: {error}")
            result["status"] = "failed"
            result["errors"] = errors
    _write_json(Path(args.result_json), result)
    return 0 if result["status"] == "ok" else 1


def _feature_collection(document: Any, label: str, errors: list[str]) -> list:
    if not isinstance(document, dict) or document.get("type") != "FeatureCollection":
        errors.append(f"{label} must be a GeoJSON FeatureCollection")
        return []
    features = document.get("features")
    if not isinstance(features, list):
        errors.append(f"{label}.features must be a list")
        return []
    return features


def _raw_way_evidence(features: list, errors: list[str]) -> dict[int, dict]:
    """Index independent ``osmium export --add-unique-id=type_id`` ways."""
    ways = {}
    for index, feature in enumerate(features):
        if not isinstance(feature, dict):
            errors.append(f"raw[{index}] is not an object")
            continue
        feature_id = feature.get("id")
        geometry = feature.get("geometry") or {}
        is_line = geometry.get("type") == "LineString"
        if isinstance(feature_id, str) and re.fullmatch(r"[nr][1-9][0-9]*", feature_id):
            continue
        if not is_line:
            if isinstance(feature_id, str) and feature_id.startswith("w"):
                errors.append(f"raw[{index}] way geometry must be a LineString")
            continue
        if not isinstance(feature_id, str) or not re.fullmatch(r"w[1-9][0-9]*", feature_id):
            errors.append(f"raw[{index}] has malformed or missing way id")
            continue
        way_id = int(feature_id[1:])
        if way_id in ways:
            errors.append(f"raw[{index}] duplicates way id w{way_id}")
            continue
        coordinates = geometry.get("coordinates")
        if not isinstance(coordinates, list) or len(coordinates) < 2:
            errors.append(f"raw[{index}] way coordinates are invalid")
            continue
        points = [_point(point) for point in coordinates]
        if any(point is None for point in points):
            errors.append(f"raw[{index}] way coordinates are invalid")
            continue
        properties = feature.get("properties")
        if not isinstance(properties, dict):
            errors.append(f"raw[{index}] way tags are invalid")
            continue
        ways[way_id] = {
            "coordinates": points,
            "tags": {key: properties[key] for key in _DECISIVE_TAGS
                     if key in properties},
        }
    return ways


def _canonical_json_sha256(value: Any) -> str:
    payload = (json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":")) + "\n").encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _graph_hierarchy(root_id: int, relations: dict[int, dict],
                     seen: set[int] | None = None) -> list[int]:
    reached = seen if seen is not None else set()
    if root_id in reached or root_id not in relations:
        return []
    reached.add(root_id)
    out = [root_id]
    for member in relations[root_id]["members"]:
        if member["type"] == "relation":
            out.extend(_graph_hierarchy(member["ref"], relations, reached))
    return out


def _graph_member_entries(root_id: int, relations: dict[int, dict],
                          seen: set[int] | None = None,
                          ancestor_spur_role: str | None = None) -> list[dict]:
    reached = seen if seen is not None else set()
    if root_id in reached or root_id not in relations:
        return []
    reached.add(root_id)
    out = []
    for member in relations[root_id]["members"]:
        role = member["role"]
        if member["type"] == "way":
            out.append({
                "relation_id": root_id,
                "member_index": member["sequence"],
                "way_id": member["ref"],
                "role": role,
                "effective_role": ancestor_spur_role or role or "main",
            })
        elif member["type"] == "relation":
            inherited = ancestor_spur_role
            if inherited is None and role in {"approach", "connection"}:
                inherited = role
            out.extend(_graph_member_entries(
                member["ref"], relations, reached, inherited))
    return out


def _validate_relation_graph(document: Any, manifest: Any,
                             raw_ways: dict[int, dict],
                             errors: list[str]) -> dict[str, Any]:
    """Validate and index the independent AOI PBF relation graph."""
    empty = {"relations": {}, "accepted_relation_ids": [], "source": None}
    if not isinstance(document, dict):
        errors.append("relation-members evidence must be an object")
        return empty
    if document.get("schema_version") != 1:
        errors.append("relation-members schema_version must be 1")
    if document.get("attribution") != ATTRIBUTION:
        errors.append("relation-members attribution is invalid")
    if document.get("accepted_route_values") != [
            "foot", "hiking", "running", "walking"]:
        errors.append("relation-members accepted route values are invalid")
    source = document.get("source")
    source_sha = None
    if not isinstance(source, dict):
        errors.append("relation-members source identity is missing")
    else:
        source_sha = source.get("sha256")
        if source.get("kind") != "runner-local-osm-pbf":
            errors.append("relation-members source kind is invalid")
        if not re.fullmatch(r"[0-9a-f]{64}", str(source_sha or "")):
            errors.append("relation-members source SHA-256 is invalid")
        if (not isinstance(source.get("bytes"), int)
                or isinstance(source.get("bytes"), bool)
                or source["bytes"] <= 0):
            errors.append("relation-members source byte size is invalid")

    relation_values = document.get("relations")
    if not isinstance(relation_values, list):
        errors.append("relation-members relations must be a list")
        return empty
    relations = {}
    accepted = []
    way_evidence: dict[int, tuple[bool, dict | None]] = {}
    prior_relation_id = 0
    for relation_index, relation in enumerate(relation_values):
        label = f"relation-members.relations[{relation_index}]"
        if not isinstance(relation, dict):
            errors.append(f"{label} is not an object")
            continue
        relation_id = relation.get("relation_id")
        if (not isinstance(relation_id, int) or isinstance(relation_id, bool)
                or relation_id <= prior_relation_id):
            errors.append(f"{label} id is invalid, duplicate, or out of order")
            continue
        prior_relation_id = relation_id
        tags = relation.get("tags")
        if (not isinstance(tags, dict)
                or not all(isinstance(key, str) and isinstance(value, str)
                           for key, value in tags.items())):
            errors.append(f"{label} tags are invalid")
            tags = {}
        derived_accepted = (
            member_safety.normalize(tags.get("type")) == "route"
            and member_safety.normalize(tags.get("route")) in {
                "hiking", "foot", "walking", "running"})
        if relation.get("accepted_hiking_route") is not derived_accepted:
            errors.append(f"{label} accepted-route flag is inconsistent")
        if derived_accepted:
            accepted.append(relation_id)

        members_value = relation.get("members")
        members = []
        if not isinstance(members_value, list):
            errors.append(f"{label} members are invalid")
            members_value = []
        for sequence, member in enumerate(members_value):
            member_label = f"{label}.members[{sequence}]"
            if not isinstance(member, dict):
                errors.append(f"{member_label} is not an object")
                continue
            member_type = member.get("type")
            ref = member.get("ref")
            role = member.get("role")
            if (member.get("sequence") != sequence
                    or member_type not in {"node", "way", "relation"}
                    or not isinstance(ref, int) or isinstance(ref, bool) or ref <= 0
                    or not isinstance(role, str)):
                errors.append(f"{member_label} identity is invalid")
                continue
            members.append({
                "sequence": sequence, "type": member_type,
                "ref": ref, "role": role,
            })
        expected_direct = list(dict.fromkeys(
            member["ref"] for member in members if member["type"] == "way"))
        if relation.get("direct_way_ids") != expected_direct:
            errors.append(f"{label} direct way ids disagree with ordered members")
        direct_values = relation.get("direct_ways")
        if not isinstance(direct_values, list):
            errors.append(f"{label} direct way evidence is invalid")
            direct_values = []
        direct_ways = {}
        for position, direct in enumerate(direct_values):
            direct_label = f"{label}.direct_ways[{position}]"
            if not isinstance(direct, dict):
                errors.append(f"{direct_label} is not an object")
                continue
            way_id = direct.get("way_id")
            present = direct.get("present")
            way_tags = direct.get("tags")
            if (not isinstance(way_id, int) or isinstance(way_id, bool)
                    or way_id <= 0 or way_id in direct_ways
                    or not isinstance(present, bool)):
                errors.append(f"{direct_label} identity is invalid")
                continue
            if present:
                if (not isinstance(way_tags, dict)
                        or not all(isinstance(key, str) and isinstance(value, str)
                                   for key, value in way_tags.items())):
                    errors.append(f"{direct_label} tags are invalid")
                    way_tags = {}
            elif way_tags is not None:
                errors.append(f"{direct_label} missing way must have null tags")
                way_tags = None
            direct_ways[way_id] = {"present": present, "tags": way_tags}
            prior = way_evidence.get(way_id)
            current = (present, way_tags)
            if prior is not None and prior != current:
                errors.append(f"{direct_label} contradicts another relation")
            way_evidence[way_id] = current
        if list(direct_ways) != expected_direct:
            errors.append(f"{label} direct way evidence order is invalid")
        relations[relation_id] = {
            "relation_id": relation_id,
            "accepted_hiking_route": derived_accepted,
            "tags": tags,
            "members": members,
            "direct_way_ids": expected_direct,
            "direct_ways": direct_ways,
        }

    if document.get("relation_count") != len(relation_values):
        errors.append("relation-members relation count is inconsistent")
    if document.get("accepted_relation_ids") != accepted:
        errors.append("relation-members accepted relation ids are inconsistent")
    relevant_relation_ids = set()
    for relation_id in accepted:
        relevant_relation_ids.update(_graph_hierarchy(relation_id, relations))
    relevant_way_ids = {
        way_id
        for relation_id in relevant_relation_ids
        for way_id in relations[relation_id]["direct_way_ids"]
    }
    expected_missing = sorted(
        way_id for way_id in relevant_way_ids
        if not way_evidence.get(way_id, (False, None))[0])
    if document.get("missing_direct_way_ids") != expected_missing:
        errors.append("relation-members missing-way ledger is inconsistent")

    for relation_id in accepted:
        for hierarchy_id in _graph_hierarchy(relation_id, relations):
            for member in relations[hierarchy_id]["members"]:
                if (member["type"] == "relation"
                        and member["ref"] not in relations):
                    errors.append(
                        "relation-members missing-source-relation unavailable "
                        f"evidence r{member['ref']}")
    for way_id in sorted(relevant_way_ids):
        present, tags = way_evidence.get(way_id, (False, None))
        if not present:
            errors.append(
                "relation-members missing-source-way unavailable evidence "
                f"w{way_id}")
            continue
        raw = raw_ways.get(way_id)
        if raw is None:
            errors.append(
                "relation-members direct way lacks raw evidence "
                f"w{way_id}")
            continue
        graph_decisive = member_safety.decisive_tags(tags or {})
        if graph_decisive != raw["tags"]:
            errors.append(
                "relation-members direct way contradicts raw tags "
                f"w{way_id}")

    seal = manifest.get("relation_members_evidence") \
        if isinstance(manifest, dict) else None
    expected_seal = {
        "path": RELATION_MEMBERS_RELATIVE,
        "sha256": _canonical_json_sha256(document),
        "source_pbf_sha256": source_sha,
    }
    if seal != expected_seal:
        errors.append("manifest relation-members evidence seal is invalid")
    return {"relations": relations, "accepted_relation_ids": accepted,
            "source": source}


def _point(value: Any) -> tuple[float, float] | None:
    if (not isinstance(value, (list, tuple)) or len(value) < 2
            or not _number(value[0]) or not _number(value[1])):
        return None
    return float(value[0]), float(value[1])


def _line_component_count(lines: Any) -> int:
    if not isinstance(lines, (list, tuple)) or not lines:
        return 0
    vertices = []
    for line in lines:
        if not isinstance(line, (list, tuple)) or len(line) < 2:
            return 0
        points = {_point(point) for point in line}
        if None in points:
            return 0
        vertices.append(points)
    reached = {0}
    while True:
        joined = set().union(*(vertices[index] for index in reached))
        additions = {
            index for index, points in enumerate(vertices)
            if index not in reached and joined.intersection(points)
        }
        if not additions:
            break
        reached.update(additions)
    return 1 if len(reached) == len(vertices) else 1 + _line_component_count(
        [line for index, line in enumerate(lines) if index not in reached])


def _positive_int_list(value: Any) -> bool:
    return (isinstance(value, list) and bool(value)
            and all(isinstance(item, int) and not isinstance(item, bool) and item > 0
                    for item in value))


def _nonnegative_int_list(value: Any) -> bool:
    return (isinstance(value, list)
            and all(isinstance(item, int) and not isinstance(item, bool) and item > 0
                    for item in value))


def _source_component_count(records: list[dict]) -> int:
    if not records:
        return 0
    remaining = {record["way_id"]: set(record["node_ids"]) for record in records}
    components = 0
    while remaining:
        components += 1
        way_id, reached_nodes = remaining.popitem()
        del way_id
        changed = True
        while changed:
            changed = False
            for candidate, node_ids in list(remaining.items()):
                if reached_nodes.intersection(node_ids):
                    reached_nodes.update(node_ids)
                    del remaining[candidate]
                    changed = True
    return components


def _walking_identity(records: list[dict]) -> str:
    rendered = [record.get("tags") or {} for record in records
                if record.get("renders_in_area") is True]
    if not rendered:
        return "unknown"
    for tags in rendered:
        if str(tags.get("foot", "")).strip().lower() in {
                "yes", "designated", "permissive"}:
            return "explicit"
        if str(tags.get("trail", "")).strip().lower() in {"yes", "designated"}:
            return "explicit"
        if str(tags.get("route", "")).strip().lower() in {
                "hiking", "foot", "walking", "running"}:
            return "explicit"
        if str(tags.get("sac_scale", "")).strip() \
                or str(tags.get("trail_visibility", "")).strip():
            return "explicit"
        if str(tags.get("hiking", "")).strip().lower() in {
                "yes", "designated", "permissive"}:
            return "explicit"
        designation = str(tags.get("designation", "")).strip().lower()
        if any(word in designation for word in ("foot", "path", "hiking", "walking")):
            return "explicit"
        if str(tags.get("highway", "")).strip().lower() in {
                "path", "footway", "steps", "bridleway", "via_ferrata", "pedestrian"}:
            return "explicit"
    if all(str(tags.get("highway", "")).strip().lower() == "track"
           for tags in rendered):
        return "weak-track"
    return "other"


def _relation_member_decision(tags: dict) -> member_safety.MemberDecision:
    return member_safety.dk_relation_member_decision(tags)


def _relation_member_should_render(tags: dict) -> bool:
    return _relation_member_decision(tags).eligible


def _is_expected_restored_member(tags: dict) -> bool:
    return _relation_member_decision(tags).restored


def _shapely_line_parts(geometry) -> list:
    if geometry.is_empty:
        return []
    if geometry.geom_type == "LineString":
        return [geometry]
    if geometry.geom_type in {"MultiLineString", "GeometryCollection"}:
        return [part for item in geometry.geoms
                for part in _shapely_line_parts(item)]
    return []


def _haversine_miles(left: tuple[float, float],
                      right: tuple[float, float]) -> float:
    lon1, lat1 = left
    lon2, lat2 = right
    radius = 3958.7613
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)
    value = (math.sin(delta_phi / 2) ** 2
             + math.cos(phi1) * math.cos(phi2)
             * math.sin(delta_lon / 2) ** 2)
    return 2 * radius * math.asin(min(1.0, math.sqrt(value)))


def _linework_miles(lines: list) -> float:
    total = 0.0
    for line in lines:
        points = [_point(point) for point in line]
        if len(points) < 2 or any(point is None for point in points):
            continue
        total += sum(_haversine_miles(points[index], points[index + 1])
                     for index in range(len(points) - 1))
    return total


def _clipped_record_miles(record: dict, boundary) -> float:
    from shapely.geometry import LineString

    if boundary is None:
        return 0.0
    intersection = LineString(record["coordinates"]).intersection(boundary)
    return _linework_miles([
        [[float(x), float(y)] for x, y in part.coords]
        for part in _shapely_line_parts(intersection)
    ])


def _restored_measurements(
        feature: dict, records: list[dict], restored_ids: list[int],
        direct: dict[int, list[int]], boundary
        ) -> list[tuple[dict, float, float,
                        dict[tuple[int, int], float], set[tuple[int, int]]]]:
    """Independently measure each relation's raw-bound direct way tuples."""
    by_id = {
        record["way_id"]: record for record in records
        if record.get("_raw_bound") is True
    }
    restored = set(restored_ids)
    properties = feature.get("properties") or {}
    relation_ids = list(dict.fromkeys(properties.get("relation_ids") or []))
    out = []
    for relation_id in relation_ids:
        relation_way_ids = []
        tuple_miles = {}
        restored_tuples = set()
        for way_id in dict.fromkeys(direct.get(relation_id, [])):
            record = by_id.get(way_id)
            if (record is None
                    or relation_id not in record.get("direct_relation_ids", [])):
                continue
            relation_way_ids.append(way_id)
            relation_way = (relation_id, way_id)
            tuple_miles[relation_way] = _clipped_record_miles(record, boundary)
            if way_id in restored:
                restored_tuples.add(relation_way)
        restored_way_ids = [
            way_id for way_id in relation_way_ids
            if (relation_id, way_id) in restored_tuples
        ]
        relation_miles = sum(tuple_miles.values())
        restored_miles = sum(
            tuple_miles[relation_way] for relation_way in restored_tuples)
        share = restored_miles / relation_miles if relation_miles > 0 else 0.0
        out.append(({
            "name": properties.get("name"),
            "key": properties.get("ckey"),
            "relation_id": relation_id,
            "relation_way_ids": relation_way_ids,
            "restored_way_ids": restored_way_ids,
            "restored_way_count": len(restored_way_ids),
            "restored_miles": round(restored_miles, 6),
            "total_relation_miles": round(relation_miles, 6),
            "restored_share": round(share, 6),
        }, restored_miles, relation_miles, tuple_miles, restored_tuples))
    return out


def _normalize_direct_members(value: Any, label: str,
                              errors: list[str]) -> dict[int, list[int]] | None:
    if not isinstance(value, dict):
        errors.append(f"{label} direct relation membership is invalid")
        return None
    normalized = {}
    for relation_id, way_ids in value.items():
        try:
            parsed = int(relation_id)
        except (TypeError, ValueError):
            errors.append(f"{label} direct relation id is invalid")
            return None
        if parsed <= 0 or str(parsed) != str(relation_id) and not isinstance(relation_id, int):
            errors.append(f"{label} direct relation id is invalid")
            return None
        if not _nonnegative_int_list(way_ids) or len(way_ids) != len(set(way_ids)):
            errors.append(f"{label} direct relation way ids are invalid")
            return None
        normalized[parsed] = way_ids
    return normalized


def _expected_relation_claim(relation_ids: list[int], authority: dict,
                             label: str, errors: list[str]
                             ) -> tuple[list[int], dict[int, list[int]]]:
    relations = authority["relations"]
    claimed = set(relation_ids)
    missing = sorted(claimed - set(relations))
    if missing:
        errors.append(f"{label} relation ids lack graph authority: {missing!r}")
    referenced = {
        member["ref"]
        for relation_id in claimed.intersection(relations)
        for member in relations[relation_id]["members"]
        if member["type"] == "relation" and member["ref"] in claimed
    }
    roots = [
        relation_id for relation_id in authority["accepted_relation_ids"]
        if relation_id in claimed and relation_id not in referenced
    ]
    expected_ids = []
    seen = set()
    for root_id in roots:
        expected_ids.extend(_graph_hierarchy(root_id, relations, seen))
    if not roots or expected_ids != relation_ids:
        errors.append(f"{label} relation id order/hierarchy contradicts graph")
    expected_direct = {
        relation_id: list(relations[relation_id]["direct_way_ids"])
        for relation_id in expected_ids if relation_id in relations
    }
    return expected_ids, expected_direct


def _graph_relation_name(tags: dict) -> str | None:
    if tags.get("name"):
        return tags["name"]
    for key in sorted(tags):
        if key.startswith("name:") and tags[key]:
            return tags[key]
    return None


def _expected_relation_audit(root_id: int, authority: dict) -> dict:
    relations = authority["relations"]
    relation = relations[root_id]
    hierarchy = _graph_hierarchy(root_id, relations)
    direct = {
        str(relation_id): list(relations[relation_id]["direct_way_ids"])
        for relation_id in hierarchy
    }
    missing_relations = sorted({
        member["ref"]
        for relation_id in hierarchy
        for member in relations[relation_id]["members"]
        if member["type"] == "relation" and member["ref"] not in relations
    })
    records = []
    main_ways = []
    spur_ways = []
    for entry in _graph_member_entries(root_id, relations):
        evidence = relations[entry["relation_id"]]["direct_ways"].get(
            entry["way_id"], {"present": False, "tags": None})
        tags = evidence.get("tags") or {}
        effective_role = entry["effective_role"]
        if not evidence.get("present"):
            decision = member_safety.MemberDecision(
                False, False, "missing-source-way")
        elif effective_role == "main":
            decision = member_safety.dk_relation_member_decision(tags)
        elif effective_role in {"approach", "connection"}:
            decision = member_safety.standalone_member_decision(tags)
        else:
            decision = member_safety.MemberDecision(
                False, False,
                f"unsupported-role-{effective_role or 'empty'}")
        included = evidence.get("present") is True and decision.eligible
        if included and effective_role == "main":
            main_ways.append(entry["way_id"])
        elif included and effective_role in {"approach", "connection"}:
            spur_ways.append(entry["way_id"])
        records.append({
            **entry,
            "source_status": "available" if evidence.get("present") else "missing",
            "tags": member_safety.decisive_tags(tags),
            "status": "included" if included else "excluded",
            "exclusion_reason": None if included else decision.reason,
            "decision": decision.to_dict(),
        })
    return {
        "relation_id": root_id,
        "name": _graph_relation_name(relation["tags"]),
        "tags": relation["tags"],
        "relation_ids": hierarchy,
        "direct_relation_way_ids": direct,
        "missing_relation_ids": missing_relations,
        "direct_way_members": records,
        "eligible_main_way_ids": list(dict.fromkeys(main_ways)),
        "included_spur_way_ids": list(dict.fromkeys(spur_ways)),
    }


def _audit_resolution(audit: dict) -> tuple[list[str], dict | None]:
    members = audit["direct_way_members"]
    excluded = [record for record in members if record["status"] == "excluded"]
    missing_way_ids = list(dict.fromkeys(
        record["way_id"] for record in members
        if record["source_status"] == "missing"))
    reasons = []
    if audit["missing_relation_ids"]:
        reasons.append("missing-source-relation")
    if missing_way_ids:
        reasons.append("missing-source-way")
    status = audit.get("assembly_status")
    if status == "entirely-filtered":
        reasons.append("entirely-filtered-route")
    elif status == "no-renderable-geometry":
        reasons.append("no-renderable-route-geometry")
    elif status not in {"emitted", "removed-thru-hike"}:
        reasons.append("invalid-assembly-status")
    if excluded:
        reasons.append("excluded-direct-member")
    if not reasons:
        return reasons, None
    return reasons, {
        "relation_id": audit["relation_id"],
        "name": audit["name"],
        "assembly_status": status,
        "reasons": reasons,
        "excluded_way_ids": list(dict.fromkeys(
            record["way_id"] for record in excluded)),
        "missing_way_ids": missing_way_ids,
        "missing_relation_ids": audit["missing_relation_ids"],
    }


def _validate_relation_member_audit(quality: dict, authority: dict,
                                    candidates: list[dict],
                                    errors: list[str]) -> None:
    audits = quality.get("relation_member_audit")
    if not isinstance(audits, list):
        errors.append("assembly relation-member audit is missing")
        return
    if [audit.get("relation_id") for audit in audits
            if isinstance(audit, dict)] != authority["accepted_relation_ids"]:
        errors.append("assembly relation-member audit relation order is incomplete")
    exact_area_ids = {
        relation_id
        for feature in candidates
        if isinstance(feature, dict)
        for relation_id in (feature.get("properties") or {}).get(
            "relation_ids", [])
    }
    unresolved = []
    for audit_index, audit in enumerate(audits):
        label = f"relation-member-audit[{audit_index}]"
        if not isinstance(audit, dict):
            errors.append(f"{label} is not an object")
            continue
        relation_id = audit.get("relation_id")
        if relation_id not in authority["accepted_relation_ids"]:
            errors.append(f"{label} lacks accepted graph authority")
            continue
        expected = _expected_relation_audit(relation_id, authority)
        for field, expected_value in expected.items():
            if audit.get(field) != expected_value:
                errors.append(f"{label} {field} contradicts relation graph")
        status = audit.get("assembly_status")
        if not expected["eligible_main_way_ids"] and status != "entirely-filtered":
            errors.append(f"{label} entirely filtered route status is dishonest")
        if relation_id in exact_area_ids and status != "emitted":
            errors.append(f"{label} emitted route status is dishonest")
        emitted = relation_id in exact_area_ids
        if audit.get("emitted_in_exact_area") is not emitted:
            errors.append(f"{label} exact-area emission flag is inconsistent")
        reasons, record = _audit_resolution({**expected, **{
            "assembly_status": status,
        }})
        expected_review = "unresolved" if reasons else "accepted"
        if audit.get("review_status") != expected_review:
            errors.append(f"{label} review status is inconsistent")
        if audit.get("unresolved_reasons") != reasons:
            errors.append(f"{label} unresolved reasons are inconsistent")
        if record is not None:
            unresolved.append(record)
            errors.append(
                f"{label} contains unresolved member exclusions or evidence")
    if quality.get("relation_member_unresolved") != unresolved:
        errors.append("assembly relation-member unresolved ledger is inconsistent")
    if quality.get("relation_member_unresolved_count") != len(unresolved):
        errors.append("assembly relation-member unresolved count is inconsistent")


def _candidate_source_evidence(feature: dict, boundary_feature: dict | None,
                               raw_ways: dict[int, dict],
                               relation_authority: dict,
                               label: str, errors: list[str]) -> dict[str, Any]:
    """Independently rebuild source topology, clipping, and walking identity."""
    from shapely.geometry import LineString, shape
    from shapely.ops import unary_union

    properties = feature.get("properties") or {}
    member_ways = properties.get("member_ways")
    geometry_way_ids = properties.get("source_geometry_way_ids")
    if not _positive_int_list(member_ways):
        errors.append(f"{label} has no source way members")
        member_ways = []
    if (not _positive_int_list(geometry_way_ids)
            or not set(geometry_way_ids).issubset(set(member_ways))
            or len(geometry_way_ids) != len(set(geometry_way_ids))):
        errors.append(f"{label} source geometry way ids are invalid")
        geometry_way_ids = []

    records_value = properties.get("source_ways")
    if not isinstance(records_value, list):
        errors.append(f"{label} source-way evidence is invalid")
        records_value = []
    records = []
    seen_way_ids = set()
    raw_missing_way_ids = set()
    missing_node_way_ids = set()
    node_coordinates: dict[int, tuple[float, float]] = {}
    boundary = None
    if boundary_feature is not None:
        try:
            boundary = shape(boundary_feature["geometry"])
            if boundary.is_empty:
                raise ValueError("empty boundary")
        except Exception:  # noqa: BLE001 - malformed boundary is already a gate failure
            errors.append(f"{label} cannot use the exact boundary geometry")
            boundary = None

    for record_index, record in enumerate(records_value):
        record_label = f"{label}.source_ways[{record_index}]"
        if not isinstance(record, dict):
            errors.append(f"{record_label} is not an object")
            continue
        way_id = record.get("way_id")
        node_ids = record.get("node_ids")
        missing_node_ids = record.get("missing_node_ids", [])
        coordinates = record.get("coordinates")
        direct_ids = record.get("direct_relation_ids")
        if (not isinstance(way_id, int) or isinstance(way_id, bool) or way_id <= 0
                or way_id in seen_way_ids):
            errors.append(f"{record_label} way id is invalid")
            continue
        seen_way_ids.add(way_id)
        if (not _positive_int_list(node_ids) or len(node_ids) < 2
                or len(node_ids) != len(set(node_ids)) and node_ids[0] != node_ids[-1]):
            errors.append(f"{record_label} node ids are invalid")
            continue
        if (not _nonnegative_int_list(missing_node_ids)
                or set(node_ids).intersection(missing_node_ids)
                or len(missing_node_ids) != len(set(missing_node_ids))):
            errors.append(f"{record_label} missing node ids are invalid")
            continue
        if (not isinstance(coordinates, list) or len(coordinates) != len(node_ids)):
            errors.append(f"{record_label} coordinates are invalid")
            continue
        points = [_point(point) for point in coordinates]
        if any(point is None for point in points):
            errors.append(f"{record_label} coordinates are invalid")
            continue
        if not isinstance(record.get("tags"), dict):
            errors.append(f"{record_label} tags are invalid")
            continue
        if not _nonnegative_int_list(direct_ids) or len(direct_ids) != len(set(direct_ids)):
            errors.append(f"{record_label} direct relation ids are invalid")
            continue
        for node_id, point in zip(node_ids, points):
            previous = node_coordinates.get(node_id)
            if previous is not None and previous != point:
                errors.append(f"{record_label} redefines source node {node_id}")
            node_coordinates[node_id] = point
        renders = False
        if boundary is not None:
            try:
                clipped = LineString(points).intersection(boundary)
                renders = not clipped.is_empty and clipped.length > 0
            except Exception:  # noqa: BLE001
                errors.append(f"{record_label} cannot be clipped to the exact boundary")
        if record.get("renders_in_area") is not renders:
            errors.append(f"{record_label} rendered-area flag is inconsistent")
        raw = raw_ways.get(way_id)
        raw_bound = False
        if raw is None:
            raw_missing_way_ids.add(way_id)
            errors.append(
                f"{record_label} missing-source-way unavailable raw evidence w{way_id}")
        else:
            raw_coordinates = raw["coordinates"]
            oriented_raw = [raw_coordinates, list(reversed(raw_coordinates))]
            geometry_matches = False
            if missing_node_ids:
                missing_node_way_ids.add(way_id)
                for orientation in oriented_raw:
                    position = 0
                    for point in points:
                        while (position < len(orientation)
                               and orientation[position] != point):
                            position += 1
                        if position == len(orientation):
                            break
                        position += 1
                    else:
                        geometry_matches = True
                        break
                if not geometry_matches:
                    errors.append(
                        f"{record_label} contradicts raw way geometry w{way_id}")
                errors.append(
                    f"{record_label} missing-source-node unavailable evidence "
                    f"w{way_id}")
            else:
                geometry_matches = points in oriented_raw
                if not geometry_matches:
                    errors.append(
                        f"{record_label} contradicts raw way geometry w{way_id}")
            tags_match = record["tags"] == raw["tags"]
            if not tags_match:
                errors.append(f"{record_label} contradicts raw way tags w{way_id}")
            raw_bound = not missing_node_ids and geometry_matches and tags_match
        records.append({**record, "way_id": way_id, "node_ids": node_ids,
                        "missing_node_ids": missing_node_ids,
                        "coordinates": coordinates, "renders_in_area": renders,
                        "_raw_bound": raw_bound})

    record_way_ids = [record["way_id"] for record in records]
    missing_records = sorted(set(geometry_way_ids) - set(record_way_ids))
    extra_records = sorted(set(record_way_ids) - set(geometry_way_ids))
    if missing_records:
        errors.append(
            f"{label} missing-source-way unavailable evidence: {missing_records!r}")
    if extra_records:
        errors.append(
            f"{label} source-way records contain non-geometry ways: {extra_records!r}")
    if (not missing_records and not extra_records
            and record_way_ids != geometry_way_ids):
        errors.append(f"{label} source-way record order disagrees with geometry way ids")
    raw_absent = set(geometry_way_ids) - set(raw_ways)
    for way_id in sorted(raw_absent - raw_missing_way_ids):
        errors.append(
            f"{label} missing-source-way unavailable raw evidence w{way_id}")
    missing_way_ids = sorted(
        set(missing_records) | raw_missing_way_ids | raw_absent
        | missing_node_way_ids)
    source_components = _source_component_count(records)

    rendered_geometry = None
    exact_clip = False
    if boundary is not None and records:
        try:
            expected = unary_union([
                LineString(record["coordinates"]) for record in records
            ]).intersection(boundary)
            rendered_geometry = shape(feature["geometry"])
            if not expected.is_empty and not rendered_geometry.is_empty:
                exact_clip = expected.equals(rendered_geometry)
        except Exception:  # noqa: BLE001
            errors.append(f"{label} geometry cannot be checked against source clipping")

    lines = _geometry_lines_for_validation(feature)
    postclip_components = _line_component_count(lines)
    boundary_split = (
        source_components == 1
        and postclip_components > 1
        and exact_clip
    )
    accepted = (
        not missing_way_ids
        and source_components == 1
        and postclip_components > 0
        and exact_clip
        and (postclip_components == 1 or boundary_split)
    )
    reasons = []
    if source_components == 1:
        reasons.append("shared-osm-node")
    if boundary_split:
        reasons.append("boundary-induced-split")
    if properties.get("boundary_induced_split") is not boundary_split:
        errors.append(f"{label} boundary-induced-split fact is inconsistent")

    connectivity = properties.get("connectivity")
    if not isinstance(connectivity, dict):
        errors.append(f"{label} lacks source connectivity evidence")
        connectivity = {}
    expected_status = "accepted" if accepted else "rejected"
    if missing_way_ids:
        expected_status = "unavailable-evidence"
    if connectivity.get("status") != expected_status:
        errors.append(f"{label} connectivity status disagrees with source evidence")
    if connectivity.get("source_components") != source_components:
        errors.append(f"{label} source component count disagrees with source nodes")
    if connectivity.get("postclip_components") != postclip_components:
        errors.append(f"{label} postclip component evidence is invalid")
    if connectivity.get("missing_way_ids") != missing_way_ids:
        errors.append(f"{label} missing source-way evidence is inconsistent")
    if connectivity.get("exact_boundary_clip") is not exact_clip:
        errors.append(f"{label} exact-boundary clip evidence is inconsistent")
    if connectivity.get("accepted_reasons") != reasons:
        errors.append(f"{label} connectivity reasons disagree with source evidence")
    if any(reason not in _ALLOWED_RELATION_REASONS for reason in reasons):
        errors.append(f"{label} has invalid connectivity reasons")

    rendered_way_ids = [record["way_id"] for record in records
                        if record["renders_in_area"]]
    if properties.get("rendered_source_way_ids") != rendered_way_ids:
        errors.append(f"{label} rendered source way ids are inconsistent")
    identity = _walking_identity(records)
    if properties.get("walking_identity") != identity:
        errors.append(f"{label} walking identity disagrees with source evidence")

    source = properties.get("source")
    relation_ids = properties.get("relation_ids")
    direct = _normalize_direct_members(
        properties.get("direct_relation_way_ids"), label, errors)
    confirmed_direct: dict[int, list[int]] = {}
    if source == "relation":
        if not _positive_int_list(relation_ids):
            errors.append(f"{label} has no signed relation identity")
            relation_ids = []
        expected_relation_ids, graph_direct = _expected_relation_claim(
            relation_ids, relation_authority, label, errors)
        if direct != graph_direct:
            errors.append(
                f"{label} direct relation membership contradicts independent graph")
        elif expected_relation_ids == relation_ids:
            confirmed_direct = graph_direct
    elif source == "name-stitch":
        if relation_ids != [] or direct not in ({}, None):
            errors.append(f"{label} rootless stitch carries relation identity")
    else:
        errors.append(f"{label} has unknown assembly source")

    restored_ids = properties.get("restored_relation_way_ids")
    if (not _nonnegative_int_list(restored_ids)
            or len(restored_ids) != len(set(restored_ids))
            or not set(restored_ids).issubset(set(geometry_way_ids))
            or (source == "relation"
                and not set(restored_ids).issubset({
                    way_id for way_ids in confirmed_direct.values()
                    for way_id in way_ids
                }))):
        errors.append(f"{label} restored relation way ids are invalid")
        restored_ids = []
    expected_restored_ids = []
    if source == "relation" and confirmed_direct:
        direct_way_ids = {
            way_id for way_ids in confirmed_direct.values() for way_id in way_ids
        }
        expected_restored_ids = [
            record["way_id"] for record in records
            if record["way_id"] in direct_way_ids
            and _is_expected_restored_member(record.get("tags") or {})
        ]
        if restored_ids != expected_restored_ids:
            errors.append(f"{label} restored-road identity disagrees with source tags")
    elif source == "name-stitch" and restored_ids != []:
        errors.append(f"{label} rootless stitch claims restored relation roads")

    if source == "relation":
        for record in records:
            expected_direct = [
                relation_id for relation_id in relation_ids or []
                if record["way_id"] in confirmed_direct.get(relation_id, [])
            ]
            if record.get("direct_relation_ids") != expected_direct:
                errors.append(f"{label} source-way direct membership is inconsistent")
            if (expected_direct
                    and not _relation_member_should_render(record.get("tags") or {})):
                errors.append(
                    f"{label} has unsafe direct relation member w{record['way_id']}")

    restored_measurements = []
    if source == "relation" and confirmed_direct:
        restored_measurements = _restored_measurements(
            feature, records, expected_restored_ids, confirmed_direct, boundary)

    return {
        "accepted": accepted,
        "source_components": source_components,
        "postclip_components": postclip_components,
        "boundary_split": boundary_split,
        "exact_clip": exact_clip,
        "identity": identity,
        "records": records,
        "missing_way_ids": missing_way_ids,
        "restored_measurements": restored_measurements,
    }


def _validate_trails(features: list, boundary_feature: dict | None,
                     raw_ways: dict[int, dict], relation_authority: dict,
                     errors: list[str]) -> None:
    if not features:
        errors.append("assembled trails must be nonzero")
        return
    for index, feature in enumerate(features):
        if not isinstance(feature, dict):
            errors.append(f"trail[{index}] is not an object")
            continue
        properties = feature.get("properties")
        name = properties.get("name") if isinstance(properties, dict) else None
        if not isinstance(name, str) or not name.strip():
            errors.append(f"trail[{index}] has no name")
        elif _UNNAMED.fullmatch(name):
            errors.append(f"trail[{index}] uses forbidden numeric unnamed name {name!r}")
        if not isinstance(properties, dict) or properties.get("area") != AREA_NAME:
            errors.append(f"trail[{index}] does not carry the exact selected area")
        geometry = feature.get("geometry")
        if not isinstance(geometry, dict):
            errors.append(f"trail[{index}] has invalid geometry")
            continue
        geometry_type = geometry.get("type")
        coordinates = geometry.get("coordinates")
        if geometry_type == "LineString":
            coordinates = [coordinates]
        elif geometry_type != "MultiLineString":
            errors.append(f"trail[{index}] geometry must be linework")
            continue
        if _line_component_count(coordinates) == 0:
            errors.append(f"trail[{index}] has invalid linework")
            continue
        rebuilt = _candidate_source_evidence(
            feature, boundary_feature, raw_ways, relation_authority,
            f"trail[{index}]", errors)
        if not rebuilt["accepted"]:
            if rebuilt["missing_way_ids"]:
                errors.append(
                    f"trail[{index}] missing-source-way unavailable evidence: "
                    f"{rebuilt['missing_way_ids']!r}")
            elif properties.get("source") == "relation":
                errors.append(f"trail[{index}] unresolved-relation-gap")
            else:
                errors.append(f"trail[{index}] has disconnected name-stitch linework")
        disposition = properties.get("quality_disposition")
        if rebuilt["missing_way_ids"]:
            if disposition != "missing-source-way":
                errors.append(f"trail[{index}] lacks missing-source-way disposition")
        elif disposition == "missing-source-way":
            errors.append(f"trail[{index}] has unsupported missing-source-way disposition")
        if (disposition == "boundary-induced-split"
                and not rebuilt["boundary_split"]):
            errors.append(f"trail[{index}] has unsupported boundary-split disposition")
        if properties.get("source") == "name-stitch":
            if rebuilt["identity"] in {"weak-track", "unknown"}:
                errors.append(f"trail[{index}] has unqualified Denmark source identity")
            if rebuilt["identity"] == "other":
                errors.append(f"trail[{index}] preserved-other-needs-review")


def _validate_quality_removals(features: list, boundary_feature: dict | None,
                               raw_ways: dict[int, dict],
                               relation_authority: dict,
                               errors: list[str]) -> dict[str, int]:
    counts = {category: 0 for category in _QUALITY_REMOVAL_CATEGORIES}
    for index, feature in enumerate(features):
        if not isinstance(feature, dict) or not isinstance(feature.get("properties"), dict):
            continue
        properties = feature["properties"]
        category = properties.get("removed_category")
        if category not in _QUALITY_REMOVAL_CATEGORIES:
            continue
        counts[category] += 1
        label = f"removed[{index}]"
        if (not isinstance(properties.get("removed_reason"), str)
                or not properties["removed_reason"].strip()):
            errors.append(f"{label} quality reason is missing")
        if properties.get("area") != AREA_NAME:
            errors.append(f"{label} lacks the exact selected area")
        if properties.get("quality_disposition") != category:
            errors.append(f"{label} disposition disagrees with removal category")
        if _line_component_count(_geometry_lines_for_validation(feature)) == 0:
            errors.append(f"{label} has empty postclip geometry")
        rebuilt = _candidate_source_evidence(
            feature, boundary_feature, raw_ways, relation_authority,
            label, errors)
        if category == "disconnected-name-stitch":
            if properties.get("source") != "name-stitch" or rebuilt["accepted"]:
                errors.append(f"{label} disconnected stitch evidence is invalid")
        elif category in {"dk-unqualified-road-track",
                          "nested-name-stitch-overlap"}:
            if (properties.get("source") != "name-stitch"
                    or not rebuilt["accepted"]
                    or rebuilt["identity"] != "weak-track"):
                errors.append(f"{label} weak-track evidence is invalid")
    return {category: count for category, count in counts.items() if count}


def _validate_ingest_dropped(features: list, trail_features: list,
                             errors: list[str]) -> None:
    published_members = set()
    relation_bindings: dict[int, set[int]] = {}
    for trail_index, trail in enumerate(trail_features):
        if not isinstance(trail, dict) or not isinstance(trail.get("properties"), dict):
            continue
        properties = trail["properties"]
        members = properties.get("member_ways")
        if not _positive_int_list(members):
            continue
        published_members.update(members)
        if properties.get("source") != "relation":
            continue
        relation_ids = properties.get("relation_ids")
        if not _positive_int_list(relation_ids):
            continue
        direct = _normalize_direct_members(
            properties.get("direct_relation_way_ids"),
            f"trail[{trail_index}] ingest binding", errors)
        if direct is None:
            continue
        for relation_id in relation_ids:
            for way_id in direct.get(relation_id, []):
                if way_id in members:
                    relation_bindings.setdefault(way_id, set()).add(relation_id)

    seen = set()
    for index, feature in enumerate(features):
        label = f"ingest-dropped[{index}]"
        if not isinstance(feature, dict) or not isinstance(feature.get("properties"), dict):
            errors.append(f"{label} is not an object with properties")
            continue
        properties = feature["properties"]
        way_id = properties.get("way_id")
        if (not isinstance(way_id, int) or isinstance(way_id, bool) or way_id <= 0
                or way_id in seen or properties.get("ckey") != f"w{way_id}"):
            errors.append(f"{label} way identity is invalid")
            continue
        seen.add(way_id)
        overlaps_output = way_id in published_members
        if overlaps_output:
            expected_relations = sorted(relation_bindings.get(way_id, set()))
            if not expected_relations:
                errors.append(f"{label} contradicts a published non-relation member")
                continue
            if properties.get("retained_in_route") is not True:
                errors.append(f"{label} overlaps published route without annotation")
            if properties.get("removed_category") != RETAINED_ROUTE_INGEST_CATEGORY:
                errors.append(f"{label} has contradictory ingest-dropped category")
            if properties.get("relation_ids") != expected_relations:
                errors.append(f"{label} retained-route relation binding is invalid")
            if (not isinstance(properties.get("standalone_drop_category"), str)
                    or not properties["standalone_drop_category"].strip()
                    or not isinstance(properties.get("standalone_drop_reason"), str)
                    or not properties["standalone_drop_reason"].strip()):
                errors.append(f"{label} lacks standalone diagnostic provenance")
        else:
            if properties.get("retained_in_route") is not False:
                errors.append(f"{label} claims an absent retained route")
            if properties.get("relation_ids") != []:
                errors.append(f"{label} has relation ids without published overlap")
            if properties.get("removed_category") == RETAINED_ROUTE_INGEST_CATEGORY:
                errors.append(f"{label} has retained category without published overlap")


def _geometry_lines_for_validation(feature: dict) -> list:
    geometry = feature.get("geometry") or {}
    coordinates = geometry.get("coordinates")
    if geometry.get("type") == "LineString":
        return [coordinates] if isinstance(coordinates, (list, tuple)) else []
    if (geometry.get("type") == "MultiLineString"
            and isinstance(coordinates, (list, tuple))):
        return coordinates
    return []


def _overlap_shares(left: dict, right: dict) -> tuple[float, float, float] | None:
    from shapely.geometry import shape

    try:
        left_geometry = shape(left["geometry"])
        right_geometry = shape(right["geometry"])
        left_length = left_geometry.length
        right_length = right_geometry.length
        if left_length <= 0 or right_length <= 0:
            return 0.0, 0.0, 0.0
        overlap = left_geometry.intersection(right_geometry).length
    except Exception:  # noqa: BLE001 - malformed topology must fail validation
        return None
    left_share = min(1.0, overlap / left_length)
    right_share = min(1.0, overlap / right_length)
    return (min(1.0, overlap / min(left_length, right_length)),
            left_share, right_share)


def _final_disposition(feature: dict) -> str:
    properties = feature["properties"]
    return (properties.get("removed_category")
            or properties.get("quality_disposition") or "kept")


def _expected_removed_candidates(left: dict, right: dict) -> list[dict]:
    out = []
    for side, feature in (("left", left), ("right", right)):
        properties = feature["properties"]
        category = properties.get("removed_category")
        if category in _QUALITY_REMOVAL_CATEGORIES:
            out.append({
                "side": side,
                "candidate_index": properties.get("quality_candidate_index"),
                "key": properties.get("ckey"),
                "name": properties.get("name"),
                "category": category,
            })
    return out


def _expected_overlap_disposition(left: dict, right: dict) -> str:
    removed = _expected_removed_candidates(left, right)
    if removed:
        categories = {record["category"] for record in removed}
        if len(categories) == 1:
            return f"removed-{next(iter(categories))}"
        return "removed-multiple-candidates"
    left_properties = left["properties"]
    right_properties = right["properties"]
    left_relation = left_properties.get("source") == "relation"
    right_relation = right_properties.get("source") == "relation"
    if left_relation and right_relation:
        return "preserved-shared-signed-routes"
    if left_relation != right_relation:
        rootless = right if left_relation else left
        identity = rootless["properties"].get("walking_identity")
        if identity == "explicit":
            return "preserved-explicit-walking-or-path"
        if identity == "other":
            return "preserved-other-needs-review"
        return "needs-review-missing-source-evidence"
    if "other" in {left_properties.get("walking_identity"),
                    right_properties.get("walking_identity")}:
        return "preserved-other-needs-review"
    return "preserved-nonweak-overlap"


def _validate_quality_report(quality: Any, trail_features: list,
                             removed_features: list,
                             boundary_feature: dict | None,
                             raw_ways: dict[int, dict],
                             relation_authority: dict,
                             errors: list[str]) -> None:
    if not isinstance(quality, dict) or quality.get("enabled") is not True:
        errors.append("assembly Denmark quality evidence is missing")
        return
    if quality.get("schema_version") != 5:
        errors.append("assembly Denmark quality schema_version must be 5")
    if quality.get("region") != "dk" or quality.get("area") != AREA_NAME:
        errors.append("assembly Denmark quality scope is invalid")
    if quality.get("connectivity_authority") != \
            "shared-osm-node-or-exact-boundary-clip":
        errors.append("assembly connectivity authority is invalid")
    if (not _number(quality.get("overlap_threshold"))
            or abs(float(quality["overlap_threshold"]) - OVERLAP_THRESHOLD) > 1e-9):
        errors.append("assembly overlap threshold is invalid")
    if quality.get("kept_count") != len(trail_features):
        errors.append("assembly quality kept count disagrees with trails GeoJSON")

    removal_counts = _validate_quality_removals(
        removed_features, boundary_feature, raw_ways, relation_authority,
        errors)
    quality_removed = [
        feature for feature in removed_features
        if isinstance(feature, dict) and isinstance(feature.get("properties"), dict)
        and feature["properties"].get("removed_category") in
        _QUALITY_REMOVAL_CATEGORIES
    ]
    if quality.get("candidate_count") != len(trail_features) + len(quality_removed):
        errors.append("assembly quality candidate count disagrees with dispositions")
    if quality.get("removed_count") != len(quality_removed):
        errors.append("assembly quality removed count disagrees with sidecar")
    if quality.get("removed_counts") != removal_counts:
        errors.append("assembly quality removal categories disagree with sidecar")

    candidates = trail_features + quality_removed
    indexed = {}
    for feature in candidates:
        if (not isinstance(feature, dict)
                or not isinstance(feature.get("properties"), dict)):
            errors.append("assembly quality candidate shape is invalid")
            continue
        index = feature["properties"].get("quality_candidate_index")
        if (not isinstance(index, int) or isinstance(index, bool) or index < 0
                or index in indexed):
            errors.append("assembly quality candidate indexes are invalid")
            continue
        indexed[index] = feature
    if set(indexed) != set(range(len(candidates))):
        errors.append("assembly quality candidate indexes are incomplete")
    ordered = [indexed[index] for index in sorted(indexed)]
    _validate_relation_member_audit(
        quality, relation_authority, candidates, errors)

    unresolved = []
    for feature in trail_features:
        if (not isinstance(feature, dict)
                or not isinstance(feature.get("properties"), dict)):
            continue
        properties = feature["properties"]
        if (properties.get("source") == "relation"
                and properties.get("quality_disposition") in {
                    "unresolved-relation-gap", "missing-source-way"}):
            connectivity = properties.get("connectivity") or {}
            unresolved.append({
                "name": properties.get("name"),
                "key": properties.get("ckey"),
                "relation_ids": properties.get("relation_ids"),
                "miles": properties.get("length_mi", 0),
                "source_components": connectivity.get("source_components"),
                "postclip_components": connectivity.get("postclip_components"),
                "missing_way_ids": connectivity.get("missing_way_ids"),
            })
    unresolved_miles = round(sum(
        float(record["miles"]) for record in unresolved
        if _number(record.get("miles"))
    ), 3)
    if quality.get("signed_relation_unresolved") != unresolved:
        errors.append("assembly signed-relation unresolved ledger is inconsistent")
    if quality.get("signed_relation_unresolved_count") != len(unresolved):
        errors.append("assembly signed-relation unresolved count is inconsistent")
    if quality.get("signed_relation_unresolved_miles") != unresolved_miles:
        errors.append("assembly signed-relation unresolved miles are inconsistent")
    if unresolved:
        names = ", ".join(repr(record.get("name")) for record in unresolved)
        errors.append(f"unresolved-relation-gap population is nonzero: {names}")

    expected_limits = {
        "max_relation_share": RESTORED_ROAD_MAX_RELATION_SHARE,
        "max_relation_miles": RESTORED_ROAD_MAX_RELATION_MILES,
        "max_aggregate_share": RESTORED_ROAD_MAX_AGGREGATE_SHARE,
    }
    if quality.get("restored_road_limits") != expected_limits:
        errors.append("assembly restored-road limits are invalid")
    expected_restored_relations = []
    aggregate_relation_way_miles: dict[tuple[int, int], float] = {}
    aggregate_restored_relation_ways: set[tuple[int, int]] = set()
    for index, feature in enumerate(trail_features):
        properties = feature.get("properties") if isinstance(feature, dict) else None
        if not isinstance(properties, dict) or properties.get("source") != "relation":
            continue
        rebuilt = _candidate_source_evidence(
            feature, boundary_feature, raw_ways, relation_authority,
            f"restored relation[{index}]", errors)
        for (measurement, restored_miles, relation_miles,
             relation_way_miles, restored_relation_ways) in \
                rebuilt["restored_measurements"]:
            expected_restored_relations.append(measurement)
            for relation_way, miles in relation_way_miles.items():
                previous = aggregate_relation_way_miles.get(relation_way)
                if previous is not None and abs(previous - miles) > 1e-12:
                    errors.append(
                        "restored-road relation-way evidence is inconsistent")
                aggregate_relation_way_miles.setdefault(relation_way, miles)
            aggregate_restored_relation_ways.update(restored_relation_ways)
            share = (restored_miles / relation_miles
                     if relation_miles > 0 else 0.0)
            if restored_miles > RESTORED_ROAD_MAX_RELATION_MILES + 1e-12:
                errors.append(
                    f"restored relation[{index}] id "
                    f"{measurement['relation_id']} exceeds restored-road mile limit")
            if share > RESTORED_ROAD_MAX_RELATION_SHARE + 1e-12:
                errors.append(
                    f"restored relation[{index}] id "
                    f"{measurement['relation_id']} exceeds restored-road share limit")
    if quality.get("restored_road_relations") != expected_restored_relations:
        errors.append("assembly restored-road relation ledger is inconsistent")
    aggregate_relation_miles = sum(aggregate_relation_way_miles.values())
    aggregate_restored_miles = sum(
        aggregate_relation_way_miles[relation_way]
        for relation_way in aggregate_restored_relation_ways
        if relation_way in aggregate_relation_way_miles
    )
    aggregate_share = (
        aggregate_restored_miles / aggregate_relation_miles
        if aggregate_relation_miles > 0 else 0.0
    )
    expected_aggregate = {
        "restored_way_count": len(aggregate_restored_relation_ways),
        "restored_miles": round(aggregate_restored_miles, 6),
        "total_relation_miles": round(aggregate_relation_miles, 6),
        "restored_share": round(aggregate_share, 6),
    }
    if quality.get("restored_road_aggregate") != expected_aggregate:
        errors.append("assembly restored-road aggregate is inconsistent")
    if aggregate_share > RESTORED_ROAD_MAX_AGGREGATE_SHARE + 1e-12:
        errors.append("assembly restored-road aggregate share exceeds limit")

    if quality.get("validation_failures") != []:
        errors.append("assembly quality report contains unresolved failures")

    evidence = quality.get("overlap_evidence")
    remaining = quality.get("remaining_overlaps")
    if not isinstance(evidence, list) or not isinstance(remaining, list):
        errors.append("assembly overlap evidence is invalid")
        return

    expected_pairs = {}
    for left_index, left in enumerate(ordered):
        for right in ordered[left_index + 1:]:
            shares = _overlap_shares(left, right)
            if shares is None:
                errors.append("assembly overlap geometry cannot be recomputed")
                continue
            ratio, left_share, right_share = shares
            if ratio + 1e-12 < OVERLAP_THRESHOLD:
                continue
            pair = (left["properties"]["quality_candidate_index"],
                    right["properties"]["quality_candidate_index"])
            expected_pairs[pair] = (left, right, ratio, left_share, right_share)

    seen = {}
    nested_ledger_indexes = set()
    for record_index, record in enumerate(evidence):
        if not isinstance(record, dict):
            errors.append(f"overlap[{record_index}] is not an object")
            continue
        pair_value = record.get("candidate_indexes")
        if (not isinstance(pair_value, list) or len(pair_value) != 2
                or not all(isinstance(value, int) and not isinstance(value, bool)
                           for value in pair_value)):
            errors.append(f"overlap[{record_index}] candidate indexes are invalid")
            continue
        pair = tuple(pair_value)
        if pair in seen:
            errors.append(f"overlap[{record_index}] duplicates a pair")
            continue
        seen[pair] = record
        expected = expected_pairs.get(pair)
        if expected is None:
            errors.append(f"overlap[{record_index}] is extra or below threshold")
            continue
        left, right, ratio, left_share, right_share = expected
        left_properties = left["properties"]
        right_properties = right["properties"]
        exact_fields = {
            "names": [left_properties.get("name"), right_properties.get("name")],
            "keys": [left_properties.get("ckey"), right_properties.get("ckey")],
            "sources": [left_properties.get("source"), right_properties.get("source")],
            "relation_ids": [left_properties.get("relation_ids", []),
                             right_properties.get("relation_ids", [])],
            "final_dispositions": [_final_disposition(left),
                                   _final_disposition(right)],
            "removed_candidates": _expected_removed_candidates(left, right),
            "disposition": _expected_overlap_disposition(left, right),
        }
        for field, expected_value in exact_fields.items():
            if record.get(field) != expected_value:
                errors.append(f"overlap[{record_index}] {field} is inconsistent")
        expected_shares = {
            "shorter_overlap_ratio": round(ratio, 6),
            "left_overlap_ratio": round(left_share, 6),
            "right_overlap_ratio": round(right_share, 6),
        }
        for field, expected_value in expected_shares.items():
            if not _number(record.get(field)) or record.get(field) != expected_value:
                errors.append(f"overlap[{record_index}] {field} is inconsistent")
        for removed_record in exact_fields["removed_candidates"]:
            if removed_record["category"] == "nested-name-stitch-overlap":
                nested_ledger_indexes.add(removed_record["candidate_index"])

    if set(seen) != set(expected_pairs):
        errors.append("assembly overlap ledger omits qualifying pairs")

    expected_remaining = {
        pair for pair, (left, right, _ratio, _left_share, _right_share)
        in expected_pairs.items()
        if not _expected_removed_candidates(left, right)
    }
    seen_remaining = set()
    for index, record in enumerate(remaining):
        if not isinstance(record, dict):
            errors.append(f"remaining overlap[{index}] is not an object")
            continue
        pair_value = record.get("candidate_indexes")
        pair = tuple(pair_value) if isinstance(pair_value, list) else None
        if pair not in seen or record != seen[pair]:
            errors.append(f"remaining overlap[{index}] lacks exact detection evidence")
            continue
        seen_remaining.add(pair)
        if record.get("disposition") not in _ALLOWED_REMAINING_OVERLAPS:
            errors.append(f"remaining overlap[{index}] is unresolved")
    if seen_remaining != expected_remaining:
        errors.append("remaining overlap ledger is incomplete or contradictory")

    expected_nested_indexes = {
        feature["properties"]["quality_candidate_index"]
        for feature in quality_removed
        if feature["properties"].get("removed_category") ==
        "nested-name-stitch-overlap"
    }
    if nested_ledger_indexes != expected_nested_indexes:
        errors.append("nested overlap evidence disagrees with removed sidecar")


def _validate_assembly(report: Any, trail_features: list, expected: int,
                       removed_features: list,
                       boundary_feature: dict | None,
                       raw_ways: dict[int, dict],
                       relation_authority: dict,
                       errors: list[str]) -> None:
    if not isinstance(report, dict):
        errors.append("assembly report must be an object")
        return
    if report.get("schema_version") != 1 or report.get("status") != "ok":
        errors.append("assembly report does not record success")
    if report.get("exact_area_required") is not True:
        errors.append("assembly did not require an exact area")
    if report.get("area_query") != AREA_NAME:
        errors.append("assembly area name is not exact Mols Bjerge")
    if report.get("boundary_match_count") != 1:
        errors.append("assembly did not find exactly one exact boundary")
    boundary = report.get("boundary")
    if not isinstance(boundary, dict):
        errors.append("assembly boundary evidence is missing")
    else:
        if boundary.get("name") != AREA_NAME:
            errors.append("assembly boundary name is wrong")
        if boundary.get("osm_type") != "relation":
            errors.append("assembly boundary did not come from a relation")
        if boundary.get("osm_id") != expected:
            errors.append("assembly boundary relation id is wrong")
    if report.get("expected_area_relation_id") != expected:
        errors.append("assembly expected relation id is wrong")
    graph_source = relation_authority.get("source")
    expected_input = ({"sha256": graph_source.get("sha256"),
                       "bytes": graph_source.get("bytes")}
                      if isinstance(graph_source, dict) else None)
    if report.get("input_pbf") != expected_input:
        errors.append(
            "assembly input PBF identity disagrees with relation-members evidence")
    if report.get("clip_applied") is not True:
        errors.append("assembly exact-area clip was not applied")
    coverage = report.get("coverage")
    if not isinstance(coverage, dict):
        errors.append("assembly coverage evidence is missing")
    else:
        for key in ("raw_trailish_ways", "named_trailish_ways"):
            value = coverage.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                errors.append(f"assembly coverage {key} must be nonzero")
    trail_count = len(trail_features)
    if report.get("assembled_trail_count") != trail_count:
        errors.append("assembly trail count disagrees with trails GeoJSON")
    if report.get("post_clip_trail_count") != trail_count:
        errors.append("assembly post-clip count disagrees with trails GeoJSON")
    before = report.get("pre_clip_trail_count")
    if not isinstance(before, int) or isinstance(before, bool) or before < trail_count:
        errors.append("assembly pre-clip count is invalid")
    _validate_quality_report(
        report.get("quality"), trail_features, removed_features,
        boundary_feature, raw_ways, relation_authority, errors)


def _publication_record_errors(record: Any, expected: int, label: str) -> list[str]:
    if not isinstance(record, dict):
        return [f"{label} is not an object"]
    errors = []
    if record.get("area_id") != AREA_ID:
        errors.append(f"{label} area id is wrong")
    if record.get("name") != AREA_NAME:
        errors.append(f"{label} name is wrong")
    if record.get("osm_relation_id") != expected:
        errors.append(f"{label} relation id is wrong")
    count = record.get("trail_count")
    if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
        errors.append(f"{label} trail count must be nonzero")
    miles = record.get("total_miles")
    if not _number(miles) or float(miles) <= 0:
        errors.append(f"{label} total miles must be positive")
    return errors


def _validate_publication(report: Any, expected: int, errors: list[str]) -> None:
    if not isinstance(report, dict):
        errors.append("publish report must be an object")
        return
    if report.get("schema_version") != 1:
        errors.append("publish report schema_version must be 1")
    if report.get("state") != STATE:
        errors.append("publisher state is not Denmark")
    if report.get("dry_run") is not True or report.get("write_mode") != "dry-run":
        errors.append("publisher was not in dry-run mode")
    if report.get("canonical_write") is not False:
        errors.append("publisher report permits a canonical write")
    if report.get("index_area_count") != 1:
        errors.append("publisher index must contain exactly one area")
    validated = report.get("validated_areas")
    published = report.get("published_areas")
    if not isinstance(validated, list) or len(validated) != 1:
        errors.append("publisher must validate exactly one area")
    else:
        errors.extend(_publication_record_errors(validated[0], expected, "validated area"))
    if not isinstance(published, list) or len(published) != 1:
        errors.append("publisher must publish exactly one dry-run area")
    else:
        errors.extend(_publication_record_errors(published[0], expected, "published area"))
    if isinstance(validated, list) and isinstance(published, list) and validated != published:
        errors.append("validated and published area records disagree")
    if report.get("skipped_areas") != []:
        errors.append("publisher reported skipped areas")
    if report.get("validation_failures") != []:
        errors.append("publisher reported validation failures")
    routes = report.get("routes")
    if not isinstance(routes, dict) or any(
            not isinstance(routes.get(key), int) or isinstance(routes.get(key), bool)
            or routes.get(key) < 0 for key in ("kept", "dropped")):
        errors.append("publisher route counts are invalid")


def _expected_inputs(expected: int) -> dict[str, str]:
    return {
        "extract": "europe/denmark",
        "state": "Denmark",
        "dry_run": "true",
        "no_routes": "false",
        "touch_report": "false",
        "elevation": "false",
        "region_code": "dk",
        "pilot_area_id": AREA_ID,
        "pilot_bbox": "10.40,56.08,10.90,56.35",
        "expected_osm_relation_id": str(expected),
    }


def _validate_manifest(manifest: Any, expected: int, errors: list[str]) -> None:
    if not isinstance(manifest, dict):
        errors.append("manifest must be an object")
        return
    if manifest.get("schema_version") != 1:
        errors.append("manifest schema_version must be 1")
    if manifest.get("branch_ref") != "chat/denmark-trails":
        errors.append("manifest branch ref is wrong")
    if not re.fullmatch(r"[0-9a-f]{40}", str(manifest.get("source_sha") or "")):
        errors.append("manifest source SHA is invalid")
    if manifest.get("inputs") != _expected_inputs(expected):
        errors.append("manifest dispatch inputs are not the exact pilot contract")
    if manifest.get("visual_review_url") != VISUAL_REVIEW_URL:
        errors.append("manifest visual-review URL is wrong")
    if manifest.get("attribution") != ATTRIBUTION:
        errors.append("manifest attribution is invalid")


def _run_final(args: argparse.Namespace) -> int:
    errors: list[str] = []
    expected = _positive_decimal(args.expected_osm_relation_id)
    if expected is None:
        errors.append("expected relation id must be a positive decimal")
    root = Path(args.qa_dir)
    expected_relation_path = (root / RELATION_MEMBERS_RELATIVE).resolve()
    supplied_relation_path = Path(args.relation_members).resolve()
    if supplied_relation_path != expected_relation_path:
        errors.append(
            "relation-members path must identify the QA package evidence file")
    documents = {
        label: _load_json(root / relative, label, errors)
        for label, relative in REQUIRED_JSON_FILES.items()
    }
    texts = {}
    for label, relative in REQUIRED_TEXT_FILES.items():
        path = root / relative
        if not path.is_file():
            errors.append(f"missing {label}: {path}")
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            errors.append(f"invalid {label}: {error}")
            continue
        if not text.strip():
            errors.append(f"{label} is empty")
        texts[label] = text

    optional_path = root / OPTIONAL_DROPPED_ROUTES
    if optional_path.exists():
        optional = _load_json(optional_path, "dropped routes", errors)
        _feature_collection(optional, "dropped routes", errors)

    if expected is not None:
        _validate_manifest(documents.get("manifest"), expected, errors)
        full_discovery_errors: list[str] = []
        selected_from_full = _validate_discovery_document(
            documents.get("discovery_report"), expected, full_discovery_errors)
        errors.extend(full_discovery_errors)
        selected = documents.get("selected_discovery")
        if not isinstance(selected, dict):
            errors.append("selected discovery record must be an object")
        else:
            if selected.get("schema_version") != 1:
                errors.append("selected discovery schema_version must be 1")
            if selected.get("attribution") != ATTRIBUTION:
                errors.append("selected discovery attribution is invalid")
            errors.extend(_target_candidate_errors(
                selected, expected, "selected discovery record"))
            if selected_from_full is not None and (
                    selected.get("index_row") != selected_from_full.get("index_row")
                    or selected.get("osm_relation_id")
                    != selected_from_full.get("osm_relation_id")):
                errors.append("selected discovery record disagrees with full diagnostics")

    raw_features = _feature_collection(documents.get("raw"), "raw", errors)
    if not raw_features:
        errors.append("raw GeoJSON must contain features")
    raw_ways = _raw_way_evidence(raw_features, errors)
    relation_authority = _validate_relation_graph(
        documents.get("relation_members"), documents.get("manifest"),
        raw_ways, errors)
    trail_features = _feature_collection(documents.get("trails"), "trails", errors)
    removed_features = _feature_collection(documents.get("removed"), "removed", errors)
    ingest_dropped_features = _feature_collection(
        documents.get("ingest_dropped"), "ingest-dropped", errors)
    area_features = _feature_collection(documents.get("areas"), "areas", errors)
    exact_areas = [
        feature for feature in area_features
        if isinstance(feature, dict)
        and isinstance(feature.get("properties"), dict)
        and feature["properties"].get("name") == AREA_NAME
    ]
    boundary_feature = exact_areas[0] if len(exact_areas) == 1 else None
    if boundary_feature is None:
        errors.append("areas GeoJSON must contain exactly one exact Mols boundary")
    _validate_trails(
        trail_features, boundary_feature, raw_ways, relation_authority, errors)
    _validate_ingest_dropped(ingest_dropped_features, trail_features, errors)
    if not isinstance(documents.get("curation"), dict):
        errors.append("curation snapshot must be an object")
    if not isinstance(documents.get("curation_diff"), dict):
        errors.append("curation diff must be an object")

    if expected is not None:
        _validate_assembly(
            documents.get("assembly_report"), trail_features, expected,
            removed_features, boundary_feature, raw_ways,
            relation_authority, errors)
        _validate_publication(documents.get("publish_report"), expected, errors)

    readme = texts.get("readme", "")
    if readme and (ATTRIBUTION not in readme or VISUAL_REVIEW_URL not in readme):
        errors.append("README lacks the visual-review URL or OSM attribution")

    result = {
        "schema_version": 1,
        "stage": "final",
        "status": "failed" if errors else "ok",
        "area_id": AREA_ID,
        "expected_osm_relation_id": expected,
        "checked_files": sorted([
            *REQUIRED_JSON_FILES.values(),
            *REQUIRED_TEXT_FILES.values(),
            *([OPTIONAL_DROPPED_ROUTES] if optional_path.exists() else []),
        ]),
        "assembled_trail_count": len(trail_features),
        "errors": errors,
    }
    _write_json(Path(args.result_json), result)
    return 0 if result["status"] == "ok" else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    discovery = subparsers.add_parser(
        "discovery", help="verify discovery and build a one-row temporary index")
    discovery.add_argument("--discovery-report", required=True)
    discovery.add_argument("--expected-osm-relation-id", required=True)
    discovery.add_argument("--selected-record-out", required=True)
    discovery.add_argument("--index-out", required=True)
    discovery.add_argument("--result-json", required=True)

    final = subparsers.add_parser(
        "final", help="verify every final QA artifact and structured report")
    final.add_argument("--qa-dir", required=True)
    final.add_argument("--relation-members", required=True)
    final.add_argument("--expected-osm-relation-id", required=True)
    final.add_argument("--result-json", required=True)
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "discovery":
        return _run_discovery(args)
    return _run_final(args)


if __name__ == "__main__":
    raise SystemExit(main())
