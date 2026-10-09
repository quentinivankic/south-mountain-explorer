#!/usr/bin/env python3
"""Fail-closed validation for the runner-local Mols Bjerge QA pilot."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import unicodedata
from pathlib import Path
from types import SimpleNamespace
from typing import Any
import xml.etree.ElementTree as ET

_TOOLS_DIR = Path(__file__).resolve().parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))
import build_mols_pilot_visual as canonical_visual  # noqa: E402

_ASSEMBLE_DIR = Path(__file__).resolve().parents[1] / "assemble"
if str(_ASSEMBLE_DIR) not in sys.path:
    sys.path.insert(0, str(_ASSEMBLE_DIR))
import member_safety  # noqa: E402
import model as assembly_model  # noqa: E402

AREA_ID = "nationalpark-mols-bjerge-dk"
AREA_NAME = "Nationalpark Mols Bjerge"
STATE = "Denmark"
ATTRIBUTION = "© OpenStreetMap contributors"
VISUAL_REVIEW_URL = "http://localhost:8000/viewer/?aoi=mols-bjerge"
_UNNAMED = re.compile(r"Unnamed \d+")
OVERLAP_THRESHOLD = 0.90
_DESTINATION_POI_CLASSES = (
    (("natural", "peak"), "natural=peak"),
    (("natural", "arch"), "natural=arch"),
    (("natural", "saddle"), "natural=saddle"),
    (("natural", "cliff"), "natural=cliff"),
    (("natural", "rock"), "natural=rock"),
    (("natural", "stone"), "natural=stone"),
    (("tourism", "viewpoint"), "tourism=viewpoint"),
    (("tourism", "attraction"), "tourism=attraction"),
    (("historic", "archaeological_site"), "historic=archaeological_site"),
    (("historic", "castle"), "historic=castle"),
    (("historic", "ruins"), "historic=ruins"),
    (("amenity", "shelter"), "amenity=shelter"),
    (("highway", "trailhead"), "highway=trailhead"),
)
_DECISIVE_TAGS = member_safety.DECISIVE_TAG_KEYS
RETAINED_ROUTE_INGEST_CATEGORY = "retained-signed-route-member"
AOI_RELATION_MEMBERS_RELATIVE = (
    "data/aoi/mols-bjerge.relation-members.aoi.json")
RAW_RELATION_MEMBERS_RELATIVE = (
    "data/source/mols-bjerge.relation-members.raw.json")
PREFILTER_RELATION_MEMBERS_RELATIVE = (
    "data/source/mols-bjerge.relation-members.prefilter.json")
AOI_PBF_RELATIVE = "data/aoi/mols-bjerge.osm.pbf"
RAW_WAY_TOPOLOGY_RELATIVE = "data/aoi/mols-bjerge.way-topology.json"
EXACT_AREA_RELATIVE = "data/aoi/mols-bjerge.exact-area.geojson"
RAW_SCOPE_RECEIPT_RELATIVE = "data/source/mols-bjerge.scope.raw.json"
PREFILTER_SCOPE_RECEIPT_RELATIVE = (
    "data/source/mols-bjerge.scope.prefilter.json")
AOI_SCOPE_RECEIPT_RELATIVE = "data/aoi/mols-bjerge.scope.aoi.json"
PREFILTER_TRANSFORM_RECEIPT_RELATIVE = (
    "data/source/mols-bjerge.prefilter-transformation.json")
RAW_PBF_RELATIVE = "data/source/mols-bjerge.raw.osm.pbf"
PREFILTER_PBF_RELATIVE = "data/source/mols-bjerge.prefilter.osm.pbf"
PBF_RELATIVES_BY_SCOPE = {
    "raw-denmark": RAW_PBF_RELATIVE,
    "prefiltered-denmark": PREFILTER_PBF_RELATIVE,
    "aoi": AOI_PBF_RELATIVE,
}
RELATION_LEDGER_RELATIVES_BY_SCOPE = {
    "raw-denmark": RAW_RELATION_MEMBERS_RELATIVE,
    "prefiltered-denmark": PREFILTER_RELATION_MEMBERS_RELATIVE,
    "aoi": AOI_RELATION_MEMBERS_RELATIVE,
}
SCOPE_RECEIPT_RELATIVES = {
    "raw-denmark": RAW_SCOPE_RECEIPT_RELATIVE,
    "prefiltered-denmark": PREFILTER_SCOPE_RECEIPT_RELATIVE,
    "aoi": AOI_SCOPE_RECEIPT_RELATIVE,
}
PILOT_AOI_NAME = "mols-bjerge"
PILOT_BBOX = "10.40,56.08,10.90,56.35"
PILOT_BBOX_COORDINATES = (10.40, 56.08, 10.90, 56.35)
PREVIEW_RELATIVE = "reports/app-preview.json"
GOLDEN_RELATIVE = "golden/mols-bjerge.json"
VISUAL_RELATIVE = "visual/mols-bjerge.svg"
VISUAL_REVIEW_RELATIVE = "visual/review.json"
PILOT_MIN_LENGTH_MI = 0.1
PILOT_MIN_INSIDE_MI = 0.05
_QUALITY_REMOVAL_CATEGORIES = {
    "disconnected-name-stitch",
    "dk-unqualified-road-track",
    "nested-name-stitch-overlap",
    assembly_model.GEOMETRY_DUPLICATE_ABSORBED_CATEGORY,
}
_CURATION_REMOVAL_CATEGORIES = {
    "access", "closed", "generic", "grid-address", "min-length",
    "motorized", "named-road", "non-trail-feature", "nonhiking-route",
    "off-trail", "road-code", "short-name", "thru-hike", "utility",
}
_CLIP_REMOVAL_CATEGORIES = {"min-inside-mi", "outside-exact-area"}
_ABSORPTION_REMOVAL_CATEGORIES = {
    assembly_model.GEOMETRY_DUPLICATE_ABSORBED_CATEGORY,
}
_ALLOWED_RELATION_REASONS = {
    "shared-osm-node", "out-of-area-member-gap", "boundary-induced-split",
}
_ALLOWED_REMAINING_OVERLAPS = {
    "preserved-shared-signed-routes",
    "preserved-explicit-walking-or-path",
    "preserved-nonweak-overlap",
}

REQUIRED_JSON_FILES = {
    "manifest": "manifest.json",
    "raw_relation_members": RAW_RELATION_MEMBERS_RELATIVE,
    "prefilter_relation_members": PREFILTER_RELATION_MEMBERS_RELATIVE,
    "aoi_relation_members": AOI_RELATION_MEMBERS_RELATIVE,
    "raw_scope_receipt": RAW_SCOPE_RECEIPT_RELATIVE,
    "prefilter_scope_receipt": PREFILTER_SCOPE_RECEIPT_RELATIVE,
    "aoi_scope_receipt": AOI_SCOPE_RECEIPT_RELATIVE,
    "prefilter_transform_receipt": PREFILTER_TRANSFORM_RECEIPT_RELATIVE,
    "raw": "data/aoi/mols-bjerge.raw.geojson",
    "raw_way_topology": RAW_WAY_TOPOLOGY_RELATIVE,
    "trails": "data/aoi/mols-bjerge.trails.geojson",
    "removed": "data/aoi/mols-bjerge.removed.geojson",
    "ingest_dropped": "data/aoi/mols-bjerge.ingest-dropped.geojson",
    "areas": "data/aoi/mols-bjerge.areas.geojson",
    "exact_area": EXACT_AREA_RELATIVE,
    "curation": "data/aoi/mols-bjerge.curation.json",
    "curation_diff": "data/aoi/mols-bjerge.curation-diff.json",
    "selected_discovery": "discovery/selected.json",
    "discovery_report": "discovery/report.json",
    "assembly_report": "reports/assembly.json",
    "publish_report": "reports/publish.json",
    "preview": PREVIEW_RELATIVE,
    "golden": GOLDEN_RELATIVE,
    "visual_review": VISUAL_REVIEW_RELATIVE,
}
REQUIRED_TEXT_FILES = {
    "readme": "README.md",
    "discovery_log": "discovery/discovery.log",
    "publish_log": "reports/publish.log",
    "viewer_index": "viewer/index.html",
    "viewer_server": "viewer/serve.py",
    "visual": VISUAL_RELATIVE,
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
        geometry_type = geometry.get("type")
        if isinstance(feature_id, str) and re.fullmatch(r"[nr][1-9][0-9]*", feature_id):
            continue
        if geometry_type not in {"LineString", "Polygon"}:
            if isinstance(feature_id, str) and feature_id.startswith("w"):
                errors.append(
                    f"raw[{index}] way geometry must be a LineString or Polygon")
            continue
        if not isinstance(feature_id, str) or not re.fullmatch(r"w[1-9][0-9]*", feature_id):
            errors.append(f"raw[{index}] has malformed or missing way id")
            continue
        way_id = int(feature_id[1:])
        if way_id in ways:
            errors.append(f"raw[{index}] duplicates way id w{way_id}")
            continue
        coordinates = geometry.get("coordinates")
        if geometry_type == "Polygon":
            if (not isinstance(coordinates, list) or len(coordinates) != 1
                    or not isinstance(coordinates[0], list)):
                errors.append(f"raw[{index}] way polygon exterior is invalid")
                continue
            coordinates = coordinates[0]
        if not isinstance(coordinates, list) or len(coordinates) < 2:
            errors.append(f"raw[{index}] way coordinates are invalid")
            continue
        points = [_point(point) for point in coordinates]
        if (any(point is None for point in points)
                or (geometry_type == "Polygon" and points[0] != points[-1])):
            errors.append(f"raw[{index}] way coordinates are invalid")
            continue
        properties = feature.get("properties")
        if not isinstance(properties, dict):
            errors.append(f"raw[{index}] way tags are invalid")
            continue
        ways[way_id] = {
            "coordinates": points,
            "geometry_type": geometry_type,
            "tags": {key: properties[key] for key in _DECISIVE_TAGS
                     if key in properties},
        }
    return ways


def _raw_destination_point_evidence(
        features: list, errors: list[str]) -> dict[int, dict]:
    """Index policy-eligible OSM node points from the sealed raw GeoJSON."""
    points = {}
    for index, feature in enumerate(features):
        if not isinstance(feature, dict):
            continue
        feature_id = feature.get("id")
        if not isinstance(feature_id, str) or not re.fullmatch(
                r"n[1-9][0-9]*", feature_id):
            continue
        node_id = int(feature_id[1:])
        geometry = feature.get("geometry") or {}
        properties = feature.get("properties")
        if geometry.get("type") != "Point":
            errors.append(f"raw[{index}] node geometry must be a Point")
            continue
        coordinate = _point(geometry.get("coordinates"))
        if (coordinate is None or not -180 <= coordinate[0] <= 180
                or not -90 <= coordinate[1] <= 90):
            errors.append(f"raw[{index}] node coordinates are invalid")
            continue
        if (not isinstance(properties, dict)
                or not all(isinstance(key, str) and isinstance(value, str)
                           for key, value in properties.items())):
            errors.append(f"raw[{index}] node tags are invalid")
            continue
        eligibility_class = _destination_eligibility_class(properties)
        if eligibility_class is None:
            continue
        if node_id in points:
            errors.append(f"raw[{index}] duplicates destination node n{node_id}")
            continue
        points[node_id] = {
            "node_id": node_id,
            "tags": dict(properties),
            "name": _destination_display_name(properties),
            "coordinate": list(coordinate),
            "eligibility_class": eligibility_class,
        }
    return points


def _validate_destination_ledger_completeness(
        raw_points: dict[int, dict], destination_pois: dict[int, dict],
        errors: list[str]) -> None:
    for node_id in sorted(set(raw_points) - set(destination_pois)):
        errors.append(
            f"raw destination point n{node_id} is missing from AOI topology ledger")
    for node_id in sorted(set(destination_pois) - set(raw_points)):
        errors.append(
            f"AOI topology destination n{node_id} is absent from raw GeoJSON points")
    for node_id in sorted(set(raw_points).intersection(destination_pois)):
        if raw_points[node_id] != destination_pois[node_id]:
            errors.append(
                f"raw destination point n{node_id} contradicts AOI topology ledger")


def _destination_eligibility_class(tags: dict[str, str]) -> str | None:
    normalized = {
        str(key).strip().casefold(): str(value).strip().casefold()
        for key, value in tags.items()
    }
    for (key, value), eligibility_class in _DESTINATION_POI_CLASSES:
        if normalized.get(key) == value:
            return eligibility_class
    return None


def _destination_display_name(tags: dict[str, str]) -> str | None:
    keys = ["name", *sorted(
        key for key in tags if key.startswith("name:"))]
    for key in keys:
        value = tags.get(key)
        if not isinstance(value, str):
            continue
        normalized = " ".join(unicodedata.normalize("NFC", value).split())
        if normalized:
            return normalized
    return None


def _way_topology_evidence(
        document: Any, root: Path, rendered_ways: dict[int, dict],
        errors: list[str]) -> tuple[dict[int, dict], dict[int, dict],
                                    dict[int, dict]]:
    """Validate AOI source topology; GeoJSON geometry typing is visual-only."""
    if not isinstance(document, dict) or document.get("schema_version") != 3:
        errors.append("raw way-topology schema_version must be 3")
        return {}, {}, {}
    if document.get("attribution") != ATTRIBUTION:
        errors.append("raw way-topology attribution is invalid")
    source = document.get("source")
    pbf_path = root / AOI_PBF_RELATIVE
    try:
        pbf_raw = pbf_path.read_bytes()
    except OSError as error:
        errors.append(f"cannot bind raw way topology to AOI PBF: {error}")
        pbf_raw = b""
    expected_source = {
        "kind": "runner-local-osm-pbf",
        "artifact": AOI_PBF_RELATIVE,
        "sha256": hashlib.sha256(pbf_raw).hexdigest(),
        "bytes": len(pbf_raw),
    }
    if source != expected_source:
        errors.append("raw way topology source disagrees with archived AOI PBF")
    values = document.get("ways")
    if not isinstance(values, list):
        errors.append("raw way-topology ways must be a list")
        return {}, {}, {}
    ways = {}
    prior_id = 0
    incomplete = []
    for index, value in enumerate(values):
        label = f"raw-way-topology.ways[{index}]"
        if not isinstance(value, dict):
            errors.append(f"{label} is not an object")
            continue
        way_id = value.get("way_id")
        tags = value.get("tags")
        node_ids = value.get("node_ids")
        coordinates = value.get("coordinates")
        missing = value.get("missing_node_ids")
        if (not isinstance(way_id, int) or isinstance(way_id, bool)
                or way_id <= prior_id):
            errors.append(f"{label} id is invalid, duplicate, or out of order")
            continue
        prior_id = way_id
        if (not isinstance(tags, dict)
                or not all(isinstance(key, str) and isinstance(item, str)
                           for key, item in tags.items())):
            errors.append(f"{label} tags are invalid")
            continue
        if (not _positive_int_list(node_ids) or len(node_ids) < 2
                or not isinstance(coordinates, list)
                or len(coordinates) != len(node_ids)
                or not _nonnegative_int_list(missing)
                or len(missing) != len(set(missing))):
            errors.append(f"{label} topology is invalid")
            continue
        points = [_point(point) if point is not None else None
                  for point in coordinates]
        expected_missing = [node_id for node_id, point in zip(node_ids, points)
                            if point is None]
        if (missing != expected_missing
                or any(point is None and node_id not in missing
                       for node_id, point in zip(node_ids, points))):
            errors.append(f"{label} missing-node evidence is inconsistent")
            continue
        if missing:
            incomplete.append(way_id)
        ways[way_id] = {
            "node_ids": node_ids,
            "coordinates": points,
            "geometry_type": (rendered_ways.get(way_id) or {}).get(
                "geometry_type"),
            "tags": member_safety.decisive_tags(tags),
        }
        rendered = rendered_ways.get(way_id)
        if rendered is None:
            continue
        if ways[way_id]["tags"] != rendered["tags"]:
            errors.append(f"raw GeoJSON tags contradict source topology w{way_id}")
        if not missing:
            oriented = [points, list(reversed(points))]
            if rendered["coordinates"] not in oriented:
                errors.append(
                    f"raw GeoJSON geometry contradicts source topology w{way_id}")
    if document.get("way_count") != len(ways):
        errors.append("raw way-topology count is inconsistent")
    if document.get("incomplete_way_ids") != incomplete:
        errors.append("raw way-topology incomplete-way ledger is inconsistent")
    missing_topology = sorted(set(rendered_ways) - set(ways))
    if missing_topology:
        errors.append(
            f"raw GeoJSON ways lack source topology: {missing_topology!r}")

    relation_values = document.get("relations")
    if not isinstance(relation_values, list):
        errors.append("raw way-topology relations must be a list")
        relation_values = []
    relations = {}
    prior_relation_id = 0
    for index, value in enumerate(relation_values):
        label = f"raw-way-topology.relations[{index}]"
        if not isinstance(value, dict):
            errors.append(f"{label} is not an object")
            continue
        relation_id = value.get("relation_id")
        tags = value.get("tags")
        members_value = value.get("members")
        if (not isinstance(relation_id, int) or isinstance(relation_id, bool)
                or relation_id <= prior_relation_id):
            errors.append(f"{label} id is invalid, duplicate, or out of order")
            continue
        prior_relation_id = relation_id
        if (not isinstance(tags, dict)
                or not all(isinstance(key, str) and isinstance(item, str)
                           for key, item in tags.items())):
            errors.append(f"{label} tags are invalid")
            tags = {}
        if not isinstance(members_value, list):
            errors.append(f"{label} members are invalid")
            members_value = []
        members = []
        for sequence, member in enumerate(members_value):
            member_label = f"{label}.members[{sequence}]"
            if (not isinstance(member, dict)
                    or member.get("sequence") != sequence
                    or member.get("type") not in {"node", "way", "relation"}
                    or not isinstance(member.get("ref"), int)
                    or isinstance(member.get("ref"), bool)
                    or member["ref"] <= 0
                    or not isinstance(member.get("role"), str)):
                errors.append(f"{member_label} identity is invalid")
                continue
            members.append({
                "sequence": sequence,
                "type": member["type"],
                "ref": member["ref"],
                "role": member["role"],
            })
        relations[relation_id] = {
            "relation_id": relation_id,
            "tags": tags,
            "members": members,
        }
    if document.get("relation_count") != len(relations):
        errors.append("raw way-topology relation count is inconsistent")

    destination_values = document.get("destination_pois")
    if not isinstance(destination_values, list):
        errors.append("raw way-topology destination POIs must be a list")
        destination_values = []
    destination_pois = {}
    prior_node_id = 0
    for index, value in enumerate(destination_values):
        label = f"raw-way-topology.destination_pois[{index}]"
        if not isinstance(value, dict) or set(value) != {
                "node_id", "tags", "name", "coordinate",
                "eligibility_class"}:
            errors.append(f"{label} schema is invalid")
            continue
        node_id = value.get("node_id")
        tags = value.get("tags")
        coordinate = _point(value.get("coordinate"))
        if (not isinstance(node_id, int) or isinstance(node_id, bool)
                or node_id <= prior_node_id):
            errors.append(f"{label} id is invalid, duplicate, or out of order")
            continue
        prior_node_id = node_id
        if (not isinstance(tags, dict)
                or not all(isinstance(key, str) and isinstance(item, str)
                           for key, item in tags.items())):
            errors.append(f"{label} tags are invalid")
            continue
        if (coordinate is None or not -180 <= coordinate[0] <= 180
                or not -90 <= coordinate[1] <= 90):
            errors.append(f"{label} coordinate is invalid")
            continue
        eligibility_class = _destination_eligibility_class(tags)
        if (eligibility_class is None
                or value.get("eligibility_class") != eligibility_class):
            errors.append(f"{label} eligibility class contradicts complete tags")
            continue
        expected_name = _destination_display_name(tags)
        if value.get("name") != expected_name:
            errors.append(f"{label} normalized display name contradicts tags")
            continue
        destination_pois[node_id] = {
            "node_id": node_id,
            "tags": tags,
            "name": expected_name,
            "coordinate": list(coordinate),
            "eligibility_class": eligibility_class,
        }
    if document.get("destination_poi_count") != len(destination_pois):
        errors.append("raw way-topology destination POI count is inconsistent")
    return ways, relations, destination_pois


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


def _validate_relation_ledger_v2(document: Any, scope: str,
                                 errors: list[str]) -> dict[str, Any]:
    """Validate one target-only exporter ledger without assembly dependencies."""
    empty = {"relations": {}, "ways": {}, "accepted_relation_ids": [],
             "root_relation_ids": [], "source": None, "scope": scope}
    label = f"{scope} relation-members"
    if not isinstance(document, dict):
        errors.append(f"{label} evidence must be an object")
        return empty
    if document.get("schema_version") != 2:
        errors.append(f"{label} schema_version must be 2")
    if document.get("scope") != scope:
        errors.append(f"{label} scope is invalid")
    if document.get("attribution") != ATTRIBUTION:
        errors.append(f"{label} attribution is invalid")
    if document.get("accepted_route_values") != [
            "foot", "hiking", "running", "walking"]:
        errors.append(f"{label} accepted route values are invalid")
    source = document.get("source")
    if (not isinstance(source, dict)
            or source.get("kind") != "runner-local-osm-pbf"
            or source.get("artifact") != PBF_RELATIVES_BY_SCOPE[scope]
            or not re.fullmatch(r"[0-9a-f]{64}", str(source.get("sha256") or ""))
            or not isinstance(source.get("bytes"), int)
            or isinstance(source.get("bytes"), bool) or source["bytes"] <= 0):
        errors.append(f"{label} source identity is invalid")
        source = None
    roots = document.get("root_relation_ids")
    if (not isinstance(roots, list) or roots != sorted(roots)
            or len(roots) != len(set(roots))
            or any(not isinstance(value, int) or isinstance(value, bool)
                   or value <= 0 for value in roots)):
        errors.append(f"{label} root relation ids are invalid")
        roots = []
    relation_values = document.get("relations")
    if not isinstance(relation_values, list):
        errors.append(f"{label} relations must be a list")
        return empty
    relations = {}
    ways = {}
    prior_id = 0
    for relation_index, relation in enumerate(relation_values):
        row_label = f"{label}.relations[{relation_index}]"
        if not isinstance(relation, dict):
            errors.append(f"{row_label} is not an object")
            continue
        relation_id = relation.get("relation_id")
        if (not isinstance(relation_id, int) or isinstance(relation_id, bool)
                or relation_id <= prior_id):
            errors.append(f"{row_label} id is invalid, duplicate, or out of order")
            continue
        prior_id = relation_id
        tags = relation.get("tags")
        if (not isinstance(tags, dict)
                or not all(isinstance(key, str) and isinstance(value, str)
                           for key, value in tags.items())):
            errors.append(f"{row_label} tags are invalid")
            tags = {}
        accepted = (
            member_safety.normalize(tags.get("type")) == "route"
            and member_safety.normalize(tags.get("route")) in {
                "hiking", "foot", "walking", "running"})
        if relation.get("accepted_hiking_route") is not accepted:
            errors.append(f"{row_label} accepted-route flag is inconsistent")
        member_values = relation.get("members")
        members = []
        if not isinstance(member_values, list):
            errors.append(f"{row_label} members are invalid")
            member_values = []
        for sequence, member in enumerate(member_values):
            member_label = f"{row_label}.members[{sequence}]"
            if (not isinstance(member, dict)
                    or member.get("sequence") != sequence
                    or member.get("type") not in {"node", "way", "relation"}
                    or not isinstance(member.get("ref"), int)
                    or isinstance(member.get("ref"), bool)
                    or member["ref"] <= 0
                    or not isinstance(member.get("role"), str)):
                errors.append(f"{member_label} identity is invalid")
                continue
            members.append({
                "sequence": sequence, "type": member["type"],
                "ref": member["ref"], "role": member["role"],
            })
        expected_direct = list(dict.fromkeys(
            member["ref"] for member in members if member["type"] == "way"))
        if relation.get("direct_way_ids") != expected_direct:
            errors.append(f"{row_label} direct way ids disagree with members")
        direct_values = relation.get("direct_ways")
        if not isinstance(direct_values, list):
            errors.append(f"{row_label} direct way evidence is invalid")
            direct_values = []
        direct_ways = {}
        for position, direct in enumerate(direct_values):
            direct_label = f"{row_label}.direct_ways[{position}]"
            if not isinstance(direct, dict):
                errors.append(f"{direct_label} is not an object")
                continue
            way_id = direct.get("way_id")
            status = direct.get("status")
            if (not isinstance(way_id, int) or isinstance(way_id, bool)
                    or way_id <= 0 or way_id in direct_ways
                    or status not in {"present", "incomplete", "missing"}):
                errors.append(f"{direct_label} identity is invalid")
                continue
            tags_value = direct.get("tags")
            node_ids = direct.get("node_ids")
            coordinates = direct.get("coordinates")
            missing_nodes = direct.get("missing_node_ids")
            if status == "missing":
                if any(value is not None for value in (
                        tags_value, node_ids, coordinates, missing_nodes)):
                    errors.append(f"{direct_label} missing way carries source data")
            else:
                if (not isinstance(tags_value, dict)
                        or not all(isinstance(key, str) and isinstance(value, str)
                                   for key, value in tags_value.items())
                        or not isinstance(node_ids, list)
                        or not isinstance(coordinates, list)
                        or len(coordinates) != len(node_ids)
                        or not isinstance(missing_nodes, list)
                        or any(not isinstance(node_id, int)
                               or isinstance(node_id, bool) or node_id <= 0
                               for node_id in node_ids)
                        or any(not isinstance(node_id, int)
                               or isinstance(node_id, bool) or node_id <= 0
                               for node_id in missing_nodes)):
                    errors.append(f"{direct_label} source geometry is invalid")
                else:
                    points = [None if point is None else _point(point)
                              for point in coordinates]
                    if any(point is not None and parsed is None
                           for point, parsed in zip(coordinates, points)):
                        errors.append(f"{direct_label} coordinates are invalid")
                    expected_missing_nodes = [
                        node_id for node_id, point in zip(node_ids, coordinates)
                        if point is None
                    ]
                    if missing_nodes != expected_missing_nodes:
                        errors.append(
                            f"{direct_label} missing-node ledger is inconsistent")
                    if status == "present" and (
                            len(node_ids) < 2 or missing_nodes
                            or any(point is None for point in points)):
                        errors.append(f"{direct_label} present status is invalid")
            normalized = {
                "way_id": way_id, "status": status,
                "present": status == "present",
                "tags": tags_value, "node_ids": node_ids,
                "coordinates": coordinates,
                "missing_node_ids": missing_nodes,
            }
            direct_ways[way_id] = normalized
            prior = ways.get(way_id)
            if prior is not None and prior != normalized:
                errors.append(f"{direct_label} contradicts another relation")
            ways[way_id] = normalized
        if list(direct_ways) != expected_direct:
            errors.append(f"{row_label} direct way evidence order is invalid")
        relations[relation_id] = {
            "relation_id": relation_id,
            "accepted_hiking_route": accepted,
            "tags": tags,
            "members": members,
            "direct_way_ids": expected_direct,
            "direct_ways": direct_ways,
        }
    if document.get("relation_count") != len(relation_values):
        errors.append(f"{label} relation count is inconsistent")
    expected_missing_relations = sorted({
        member["ref"]
        for relation in relations.values()
        for member in relation["members"]
        if member["type"] == "relation" and member["ref"] not in relations
    })
    if document.get("missing_relation_ids") != expected_missing_relations:
        errors.append(f"{label} missing relation ledger is inconsistent")
    expected_missing_ways = sorted(
        way_id for way_id, way in ways.items() if way["status"] == "missing")
    expected_incomplete_ways = sorted(
        way_id for way_id, way in ways.items() if way["status"] == "incomplete")
    if document.get("missing_direct_way_ids") != expected_missing_ways:
        errors.append(f"{label} missing way ledger is inconsistent")
    if document.get("incomplete_direct_way_ids") != expected_incomplete_ways:
        errors.append(f"{label} incomplete way ledger is inconsistent")
    for root_id in roots:
        relation = relations.get(root_id)
        if relation is not None and not relation["accepted_hiking_route"]:
            errors.append(f"{label} root r{root_id} is not a hiking route")
    return {
        "relations": relations,
        "ways": ways,
        "accepted_relation_ids": roots,
        "root_relation_ids": roots,
        "source": source,
        "scope": scope,
    }


def _transform_way_matches(raw: dict, witness: dict) -> bool:
    if raw["status"] == "missing" or witness["status"] == "missing":
        return raw["status"] == witness["status"]
    if (raw.get("tags") != witness.get("tags")
            or raw.get("node_ids") != witness.get("node_ids")):
        return False
    return all(witness_point is None or witness_point == raw_point
               for raw_point, witness_point in zip(
                   raw.get("coordinates") or [],
                   witness.get("coordinates") or []))


def _validate_relation_scopes(raw_document: Any, prefilter_document: Any,
                              aoi_document: Any, raw_geojson_ways: dict[int, dict],
                              errors: list[str]) -> dict[str, Any]:
    scopes = {
        "raw-denmark": _validate_relation_ledger_v2(
            raw_document, "raw-denmark", errors),
        "prefiltered-denmark": _validate_relation_ledger_v2(
            prefilter_document, "prefiltered-denmark", errors),
        "aoi": _validate_relation_ledger_v2(aoi_document, "aoi", errors),
    }
    raw = scopes["raw-denmark"]
    roots = raw["root_relation_ids"]
    if any(scope["root_relation_ids"] != roots for scope in scopes.values()):
        errors.append("relation authority scopes select different AOI roots")
    missing_roots = sorted(set(roots) - set(raw["relations"]))
    if missing_roots:
        errors.append(f"raw Denmark is missing selected roots: {missing_roots!r}")
    for scope_name in ("prefiltered-denmark", "aoi"):
        witness = scopes[scope_name]
        for relation_id, relation in witness["relations"].items():
            source = raw["relations"].get(relation_id)
            if source is None:
                errors.append(
                    f"{scope_name} relation r{relation_id} is absent from raw Denmark")
            elif (relation["tags"] != source["tags"]
                  or relation["members"] != source["members"]):
                errors.append(
                    f"{scope_name} relation r{relation_id} contradicts raw Denmark")
        for way_id, way in witness["ways"].items():
            source = raw["ways"].get(way_id)
            if source is None or (way["status"] != "missing"
                                  and source["status"] == "missing"):
                errors.append(f"{scope_name} way w{way_id} is absent from raw Denmark")
            elif (way["status"] != "missing" and source["status"] != "missing"
                  and not _transform_way_matches(source, way)):
                errors.append(f"{scope_name} way w{way_id} contradicts raw Denmark")
    for way_id, way in scopes["aoi"]["ways"].items():
        if way["status"] != "present":
            continue
        raw_geojson = raw_geojson_ways.get(way_id)
        if raw_geojson is None:
            errors.append(f"AOI ledger way w{way_id} lacks raw GeoJSON evidence")
            continue
        if member_safety.decisive_tags(way.get("tags") or {}) != raw_geojson["tags"]:
            errors.append(f"AOI ledger way w{way_id} contradicts raw GeoJSON tags")
        points = [tuple(point) for point in way.get("coordinates") or []]
        if points not in [raw_geojson["coordinates"],
                          list(reversed(raw_geojson["coordinates"]))]:
            errors.append(f"AOI ledger way w{way_id} contradicts raw GeoJSON geometry")
    raw["scopes"] = scopes
    return raw


def _scope_root_hash(roots: list[int]) -> str:
    raw = json.dumps(roots, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _root_bbox_hash(roots: list[int], *, name: str = PILOT_AOI_NAME,
                    bbox: str = PILOT_BBOX) -> str:
    value = {"bbox": bbox, "name": name, "root_relation_ids": roots}
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _pinned_aoi_command() -> list[str]:
    return [
        "osmium", "extract", "--strategy=smart", "--bbox", PILOT_BBOX,
        "data/hiking.osm.pbf", "-o", AOI_PBF_RELATIVE, "--overwrite",
    ]


def _pinned_prefilter_command() -> list[str]:
    return [
        "osmium", "tags-filter", "data/raw/denmark.osm.pbf",
        "w/highway=path,footway,steps,track,bridleway,via_ferrata,pedestrian",
        "w/abandoned:highway",
        "r/route=hiking,foot,walking,running",
        "wr/boundary=protected_area,national_park",
        "wr/leisure=nature_reserve",
        "wr/landuse=forest",
        "n/natural=peak,arch,saddle,cliff,rock,stone",
        "n/waterway=waterfall",
        "n/tourism=viewpoint,alpine_hut,wilderness_hut",
        "n/mountain_pass=yes",
        "n/highway=trailhead",
        "n/tourism=attraction",
        "n/historic=archaeological_site,castle,ruins",
        "n/amenity=shelter",
        "-o", "data/hiking.osm.pbf", "--overwrite",
    ]


def _byte_identity(raw: bytes) -> dict[str, Any]:
    return {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}


def _trusted_identity(value: Any, label: str,
                      errors: list[str]) -> dict[str, Any]:
    if (not isinstance(value, dict) or set(value) != {"sha256", "bytes"}
            or not re.fullmatch(r"[0-9a-f]{64}", str(value.get("sha256") or ""))
            or not isinstance(value.get("bytes"), int)
            or isinstance(value.get("bytes"), bool)
            or value.get("bytes", 0) <= 0):
        errors.append(f"{label} trusted byte identity is invalid")
        return {}
    return value


def _load_scope_trust(path: Path, qa_root: Path,
                      errors: list[str]) -> dict[str, Any]:
    """Load stage outputs from outside the package generation boundary."""
    try:
        lexical_path = path.expanduser().absolute()
        lexical_root = qa_root.expanduser().absolute()
        resolved_path = path.resolve(strict=True)
        resolved_root = qa_root.resolve(strict=True)
    except OSError as error:
        errors.append(f"scope trust file is unavailable: {error}")
        return {}
    if (lexical_path == lexical_root or lexical_root in lexical_path.parents
            or resolved_path == resolved_root
            or resolved_root in resolved_path.parents):
        errors.append("scope trust file must be outside the QA package")
        return {}
    document = _load_json(resolved_path, "scope trust file", errors)
    if not isinstance(document, dict):
        return {}
    if document.get("schema_version") != 1:
        errors.append("scope trust schema_version must be 1")
    if document.get("pilot") != {
            "name": PILOT_AOI_NAME, "bbox": PILOT_BBOX}:
        errors.append("scope trust pilot identity is invalid")
    values = document.get("scopes")
    if not isinstance(values, dict) or set(values) != set(PBF_RELATIVES_BY_SCOPE):
        errors.append("scope trust scope set is invalid")
        values = {}
    normalized = {}
    for scope in PBF_RELATIVES_BY_SCOPE:
        value = values.get(scope)
        expected_keys = {
            "parent", "slice", "receipt", "relation_ledger",
            "root_set_sha256",
        }
        if scope == "aoi":
            expected_keys.update({"root_bbox_sha256", "way_topology"})
        if not isinstance(value, dict) or set(value) != expected_keys:
            errors.append(f"{scope} scope trust record is invalid")
            value = {}
        root_hash = value.get("root_set_sha256")
        if not re.fullmatch(r"[0-9a-f]{64}", str(root_hash or "")):
            errors.append(f"{scope} trusted root-set identity is invalid")
        root_bbox_hash = value.get("root_bbox_sha256")
        if (scope == "aoi"
                and not re.fullmatch(r"[0-9a-f]{64}",
                                     str(root_bbox_hash or ""))):
            errors.append("AOI trusted root/bbox identity is invalid")
        normalized[scope] = {
            "parent": _trusted_identity(
                value.get("parent"), f"{scope} parent", errors),
            "slice": _trusted_identity(
                value.get("slice"), f"{scope} slice", errors),
            "receipt": _trusted_identity(
                value.get("receipt"), f"{scope} receipt", errors),
            "relation_ledger": _trusted_identity(
                value.get("relation_ledger"),
                f"{scope} relation ledger", errors),
            "root_set_sha256": root_hash,
        }
        if scope == "aoi":
            normalized[scope]["root_bbox_sha256"] = root_bbox_hash
            normalized[scope]["way_topology"] = _trusted_identity(
                value.get("way_topology"), "AOI way topology", errors)
    transform = document.get("prefilter_transformation")
    if (not isinstance(transform, dict)
            or set(transform) != {"input", "output", "receipt"}):
        errors.append("prefilter transformation trust record is invalid")
        transform = {}
    normalized_transform = {
        key: _trusted_identity(
            transform.get(key), f"prefilter transformation {key}", errors)
        for key in ("input", "output", "receipt")
    }
    return {"scopes": normalized,
            "prefilter_transformation": normalized_transform}


def _validate_semantic_ledger_trust(root: Path, trust: dict[str, Any],
                                    errors: list[str]) -> set[str]:
    """Check creator-owned semantic bytes before loading ledger JSON."""
    trusted_relatives: set[str] = set()
    trusted_scopes = trust.get("scopes", {})
    for scope, relative in RELATION_LEDGER_RELATIVES_BY_SCOPE.items():
        path = root / relative
        try:
            actual = _byte_identity(path.read_bytes())
        except OSError as error:
            errors.append(f"missing {scope} relation ledger: {error}")
            continue
        if trusted_scopes.get(scope, {}).get("relation_ledger") != actual:
            errors.append(
                f"{scope} relation ledger disagrees with trusted stage output")
        else:
            trusted_relatives.add(relative)
    topology_path = root / RAW_WAY_TOPOLOGY_RELATIVE
    try:
        topology_identity = _byte_identity(topology_path.read_bytes())
    except OSError as error:
        errors.append(f"missing AOI way topology ledger: {error}")
    else:
        if trusted_scopes.get("aoi", {}).get(
                "way_topology") != topology_identity:
            errors.append(
                "AOI way topology disagrees with trusted stage output")
        else:
            trusted_relatives.add(RAW_WAY_TOPOLOGY_RELATIVE)
    return trusted_relatives


def _receipt_counts(value: Any, label: str,
                    errors: list[str]) -> dict[str, int] | None:
    if (not isinstance(value, dict) or set(value) != {
            "nodes", "ways", "relations"}
            or any(not isinstance(item, int) or isinstance(item, bool)
                   or item < 0 for item in value.values())):
        errors.append(f"{label} object counts are invalid")
        return None
    return value


def _validate_scope_receipt(
        receipt: Any, *, scope: str, source_label: str,
        output_artifact: str, upstream_stage: str, output_raw: bytes,
        ledger_source: dict, roots: list[int], errors: list[str]) -> dict:
    label = f"{scope} scope receipt"
    if not isinstance(receipt, dict):
        errors.append(f"{label} is missing")
        return {}
    if (receipt.get("schema_version") != 1
            or receipt.get("receipt_kind") != "scope"
            or receipt.get("scope") != scope
            or receipt.get("canonical_source_path_label") != source_label
            or receipt.get("upstream_workflow_stage") != upstream_stage
            or receipt.get("root_relation_ids") != roots
            or receipt.get("root_set_sha256") != _scope_root_hash(roots)):
        errors.append(f"{label} identity or root binding is invalid")
    parent = receipt.get("parent_pbf")
    output = receipt.get("compact_output")
    if (not isinstance(parent, dict)
            or not re.fullmatch(r"[0-9a-f]{64}", str(parent.get("sha256") or ""))
            or not isinstance(parent.get("bytes"), int)
            or isinstance(parent.get("bytes"), bool)
            or parent.get("bytes", 0) <= 0):
        errors.append(f"{label} parent identity is invalid")
        parent = {}
    parent_counts = _receipt_counts(
        parent.get("object_counts"), f"{label} parent", errors)
    expected_output = {
        "artifact": output_artifact,
        **_byte_identity(output_raw),
    }
    if (not isinstance(output, dict)
            or any(output.get(key) != value
                   for key, value in expected_output.items())):
        errors.append(f"{label} compact output identity is invalid")
        output = {}
    output_counts = _receipt_counts(
        output.get("object_counts"), f"{label} compact output", errors)
    if ledger_source != {
            "kind": "runner-local-osm-pbf", **expected_output}:
        errors.append(f"{label} does not bind its relation ledger")
    osmium = receipt.get("osmium")
    command = osmium.get("command") if isinstance(osmium, dict) else None
    version = osmium.get("version") if isinstance(osmium, dict) else None
    if (not isinstance(version, str) or not version.strip()
            or not isinstance(command, list)
            or not all(isinstance(value, str) and value for value in command)):
        errors.append(f"{label} osmium command/version is invalid")
        command = []
    if scope in {"raw-denmark", "prefiltered-denmark"}:
        if (len(command) != 9
                or command[:4] != ["osmium", "getid", "-r", "--id-file"]
                or not command[4]
                or command[5] != source_label
                or command[6] != "-o" or not command[7]
                or command[8] != "--overwrite"):
            errors.append(f"{label} root-closure command is invalid")
        if "extraction" in receipt:
            errors.append(f"{label} must not carry AOI extraction identity")
    else:
        extraction = receipt.get("extraction")
        expected_extraction = {
            "name": PILOT_AOI_NAME,
            "bbox": PILOT_BBOX,
            "root_bbox_sha256": _root_bbox_hash(roots),
        }
        if extraction != expected_extraction:
            errors.append(f"{label} root/bbox identity is invalid")
        if command != _pinned_aoi_command():
            errors.append(f"{label} AOI extract command is invalid")
    return {
        "parent": parent,
        "parent_counts": parent_counts,
        "output": output,
        "output_counts": output_counts,
        "command": command,
    }


def _validate_scope_pbf_sources(root: Path, authority: dict[str, Any],
                                receipts: dict[str, Any],
                                transform_receipt: Any, manifest: Any,
                                trust: dict[str, Any],
                                errors: list[str]) -> dict[str, Any]:
    """Bind package evidence to non-rewriteable workflow-stage identities."""
    scopes = authority.get("scopes", {})
    trusted_scopes = trust.get("scopes", {})
    artifacts = manifest.get("artifacts", {}) if isinstance(manifest, dict) else {}
    expected_receipts = {
        "raw-denmark": (
            "data/raw/denmark.osm.pbf", RAW_PBF_RELATIVE,
            "relation-root-closure-extract"),
        "prefiltered-denmark": (
            "data/hiking.osm.pbf", PREFILTER_PBF_RELATIVE,
            "relation-root-closure-extract"),
        "aoi": (
            "data/hiking.osm.pbf", AOI_PBF_RELATIVE, "aoi-extract"),
    }
    normalized = {}
    compact_identities = {}
    roots = authority.get("root_relation_ids") or []
    expected_root_hash = _scope_root_hash(roots)
    for scope, relative in PBF_RELATIVES_BY_SCOPE.items():
        source = scopes.get(scope, {}).get("source")
        path = root / relative
        receipt_relative = SCOPE_RECEIPT_RELATIVES[scope]
        receipt_path = root / receipt_relative
        try:
            raw = path.read_bytes()
        except OSError as error:
            errors.append(f"missing archived {scope} PBF slice: {error}")
            continue
        try:
            receipt_raw = receipt_path.read_bytes()
        except OSError as error:
            errors.append(f"missing archived {scope} scope receipt: {error}")
            receipt_raw = b""
        slice_identity = _byte_identity(raw)
        receipt_identity = _byte_identity(receipt_raw)
        compact_identities[scope] = slice_identity
        expected_source = {
            "kind": "runner-local-osm-pbf",
            "artifact": relative,
            **slice_identity,
        }
        if source != expected_source:
            errors.append(
                f"archived {scope} PBF identity disagrees with its ledger")
        source_label, output_artifact, stage = expected_receipts[scope]
        normalized[scope] = _validate_scope_receipt(
            receipts.get(scope), scope=scope, source_label=source_label,
            output_artifact=output_artifact, upstream_stage=stage,
            output_raw=raw, ledger_source=source or {}, roots=roots,
            errors=errors)
        trusted = trusted_scopes.get(scope, {})
        if trusted.get("slice") != slice_identity:
            errors.append(f"{scope} PBF disagrees with trusted stage output")
        if trusted.get("receipt") != receipt_identity:
            errors.append(
                f"{scope} scope receipt disagrees with trusted stage output")
        parent = normalized[scope].get("parent") or {}
        parent_identity = {
            key: parent.get(key) for key in ("sha256", "bytes")}
        if trusted.get("parent") != parent_identity:
            errors.append(
                f"{scope} parent disagrees with trusted stage output")
        if trusted.get("root_set_sha256") != expected_root_hash:
            errors.append(
                f"{scope} roots disagree with trusted stage output")
        if scope == "aoi" and trusted.get("root_bbox_sha256") != \
                _root_bbox_hash(roots):
            errors.append("AOI root/bbox disagrees with trusted stage output")
        for artifact_relative, trusted_identity in (
                (relative, trusted.get("slice")),
                (receipt_relative, trusted.get("receipt"))):
            seal = artifacts.get(artifact_relative)
            if (not isinstance(seal, dict)
                    or {key: seal.get(key) for key in ("sha256", "bytes")}
                    != trusted_identity):
                errors.append(
                    f"manifest {artifact_relative} seal disagrees with "
                    "trusted stage output")

    aoi_identity = compact_identities.get("aoi")
    for scope in ("raw-denmark", "prefiltered-denmark"):
        if aoi_identity and compact_identities.get(scope) == aoi_identity:
            errors.append(
                f"AOI compact PBF is byte-identical to {scope} closure")

    transform_relative = PREFILTER_TRANSFORM_RECEIPT_RELATIVE
    try:
        transform_raw = (root / transform_relative).read_bytes()
    except OSError as error:
        errors.append(f"missing prefilter transformation receipt: {error}")
        transform_raw = b""
    transform_trust = trust.get("prefilter_transformation", {})
    transform_receipt_identity = _byte_identity(transform_raw)
    if transform_trust.get("receipt") != transform_receipt_identity:
        errors.append(
            "prefilter transformation receipt disagrees with trusted stage output")
    transform_seal = artifacts.get(transform_relative)
    if (not isinstance(transform_seal, dict)
            or {key: transform_seal.get(key) for key in ("sha256", "bytes")}
            != transform_trust.get("receipt")):
        errors.append(
            "manifest prefilter transformation seal disagrees with trusted "
            "stage output")
    if not isinstance(transform_receipt, dict):
        errors.append("prefilter transformation receipt is missing")
        return normalized
    transform_parent = transform_receipt.get("parent_pbf")
    transform_output = transform_receipt.get("output_pbf")
    osmium = transform_receipt.get("osmium")
    command = osmium.get("command") if isinstance(osmium, dict) else None
    if (transform_receipt.get("schema_version") != 1
            or transform_receipt.get("receipt_kind") != "transformation"
            or transform_receipt.get("scope") != "prefiltered-denmark"
            or transform_receipt.get("canonical_source_path_label") !=
            "data/raw/denmark.osm.pbf"
            or transform_receipt.get("upstream_workflow_stage") != "prefilter"
            or not isinstance(osmium, dict)
            or not isinstance(osmium.get("version"), str)
            or not osmium.get("version", "").strip()
            or command != _pinned_prefilter_command()):
        errors.append("prefilter transformation command/provenance is invalid")
    raw_parent = normalized.get("raw-denmark", {}).get("parent") or {}
    prefilter_parent = normalized.get("prefiltered-denmark", {}).get("parent") or {}
    aoi_parent = normalized.get("aoi", {}).get("parent") or {}
    raw_parent_identity = {
        key: raw_parent.get(key) for key in ("sha256", "bytes")}
    prefilter_parent_identity = {
        key: prefilter_parent.get(key) for key in ("sha256", "bytes")}
    transform_parent_identity = (
        {key: transform_parent.get(key) for key in ("sha256", "bytes")}
        if isinstance(transform_parent, dict) else {})
    transform_output_identity = (
        {key: transform_output.get(key) for key in ("sha256", "bytes")}
        if isinstance(transform_output, dict) else {})
    if transform_trust.get("input") != transform_parent_identity:
        errors.append("prefilter input disagrees with trusted stage output")
    if transform_trust.get("output") != transform_output_identity:
        errors.append("prefilter output disagrees with trusted stage output")
    if transform_trust.get("input") != raw_parent_identity:
        errors.append("trusted prefilter input disagrees with raw scope parent")
    if transform_trust.get("output") != prefilter_parent_identity:
        errors.append("trusted prefilter output disagrees with prefilter parent")
    if transform_parent != raw_parent:
        errors.append("prefilter receipt input disagrees with raw scope parent")
    expected_transform_output = {
        "artifact": "data/hiking.osm.pbf",
        **{key: prefilter_parent.get(key) for key in (
            "sha256", "bytes", "object_counts")},
    }
    if transform_output != expected_transform_output:
        errors.append("prefilter receipt output disagrees with prefilter parent")
    if aoi_parent != prefilter_parent:
        errors.append("AOI scope parent disagrees with the prefilter output")
    if raw_parent_identity == prefilter_parent_identity:
        errors.append("raw and prefilter scopes do not have distinct parents")
    if (compact_identities.get("raw-denmark") ==
            compact_identities.get("prefiltered-denmark")
            and (raw_parent_identity == prefilter_parent_identity
                 or transform_trust.get("receipt") !=
                 transform_receipt_identity)):
        errors.append(
            "identical raw/prefilter closures lack distinct trusted "
            "transformation proof")

    raw_parent_counts = normalized.get("raw-denmark", {}).get("parent_counts")
    prefilter_parent_counts = normalized.get(
        "prefiltered-denmark", {}).get("parent_counts")
    if raw_parent_counts is not None and prefilter_parent_counts is not None:
        raw_total = sum(raw_parent_counts.values())
        prefilter_total = sum(prefilter_parent_counts.values())
        if raw_total <= prefilter_total:
            errors.append(
                "prefilter transformation lacks a reduced parent object-count witness")

    ledger_views = []
    for scope in ("raw-denmark", "prefiltered-denmark", "aoi"):
        value = scopes.get(scope, {})
        ledger_views.append((value.get("relations"), value.get("ways")))
    has_ledger_witness = any(view != ledger_views[0]
                             for view in ledger_views[1:])
    count_vectors = []
    for scope in ("raw-denmark", "prefiltered-denmark", "aoi"):
        parent_counts = normalized.get(scope, {}).get("parent_counts")
        output_counts = normalized.get(scope, {}).get("output_counts")
        if parent_counts is not None and output_counts is not None:
            count_vectors.append((tuple(parent_counts.values()),
                                  tuple(output_counts.values())))
    has_count_witness = len(count_vectors) == 3 and len(set(count_vectors)) > 1
    if not has_ledger_witness and not has_count_witness:
        errors.append("scope distinction lacks a substantive cross-scope witness")
    return normalized


def _topology_intersects_bbox(way: dict, bbox_geometry) -> bool | None:
    from shapely.geometry import LineString

    coordinates = way.get("coordinates") or []
    if len(coordinates) < 2 or any(point is None for point in coordinates):
        return None
    try:
        return LineString(coordinates).intersects(bbox_geometry)
    except Exception:  # noqa: BLE001 - malformed topology is already terminal
        return None


def _is_verified_smart_completion_relation(_relation_id: int,
                                           tags: dict[str, str]) -> bool:
    """Return whether prefilter policy can smart-complete this AOI relation."""
    boundary = member_safety.normalize(tags.get("boundary"))
    return (
        boundary in {"protected_area", "national_park"}
        or member_safety.normalize(tags.get("leisure")) == "nature_reserve"
        or member_safety.normalize(tags.get("landuse")) == "forest"
    )


def _validate_aoi_content_scope(
        raw_ways: dict[int, dict], topology_relations: dict[int, dict],
        destination_pois: dict[int, dict], authority: dict[str, Any],
        scope_evidence: dict[str, Any], boundary,
        trails: list, removed: list, errors: list[str]) -> None:
    """Prove AOI topology and rendered population match the pinned bbox cut."""
    from shapely.geometry import box

    bbox_geometry = box(*PILOT_BBOX_COORDINATES)
    if boundary is None or boundary.is_empty or not boundary.intersects(bbox_geometry):
        errors.append("exact selected boundary does not intersect the pinned AOI bbox")

    aoi_scope = authority.get("scopes", {}).get("aoi", {})
    graph_bound_way_ids = set(aoi_scope.get("ways", {}))
    aoi_graph_relations = aoi_scope.get("relations", {})
    for relation_id, relation in aoi_graph_relations.items():
        topology = topology_relations.get(relation_id)
        if topology is None:
            errors.append(
                f"selected hiking relation r{relation_id} is absent from AOI topology")
        elif (topology.get("tags") != relation.get("tags")
              or topology.get("members") != relation.get("members")):
            errors.append(
                f"selected hiking relation r{relation_id} contradicts AOI topology")
    if 7046785 not in topology_relations:
        errors.append("exact boundary relation r7046785 is absent from AOI topology")

    smart_relation_ids = {
        relation_id for relation_id, relation in topology_relations.items()
        if _is_verified_smart_completion_relation(
            relation_id, relation.get("tags") or {})
    }
    smart_completion_way_ids = {
        member["ref"]
        for relation_id in smart_relation_ids
        for member in topology_relations[relation_id].get("members", [])
        if member.get("type") == "way"
    }
    any_topology_intersection = False
    for way_id, way in raw_ways.items():
        intersects = _topology_intersects_bbox(way, bbox_geometry)
        if intersects is None:
            errors.append(f"AOI topology w{way_id} cannot be classified against bbox")
            way["_aoi_scope_class"] = "unclassified"
        elif intersects:
            any_topology_intersection = True
            way["_aoi_scope_class"] = "bbox-selected"
        elif way_id in graph_bound_way_ids:
            way["_aoi_scope_class"] = "smart-reference"
        elif way_id in smart_completion_way_ids:
            way["_aoi_scope_class"] = "smart-area-reference"
        else:
            way["_aoi_scope_class"] = "out-of-bbox-unbound"
            errors.append(
                f"AOI topology w{way_id} is outside bbox without selected-root "
                "or verified smart-area binding")
    if not any_topology_intersection:
        errors.append("AOI topology has no feature intersecting the pinned bbox")

    root_intersects = False
    relations = aoi_graph_relations
    for root_id in authority.get("root_relation_ids", []):
        for relation_id in _graph_hierarchy(root_id, relations):
            relation = relations.get(relation_id, {})
            for way in relation.get("direct_ways", {}).values():
                if way.get("status") != "present":
                    continue
                if _topology_intersects_bbox(way, bbox_geometry) is True:
                    root_intersects = True
                    break
            if root_intersects:
                break
        if root_intersects:
            break
    if not root_intersects:
        errors.append("no selected root feature intersects the pinned AOI bbox")

    counts = scope_evidence.get("aoi", {}).get("output_counts")
    parent_counts = scope_evidence.get("aoi", {}).get("parent_counts")
    if counts is not None:
        unique_nodes = {
            node_id for way in raw_ways.values()
            for node_id in (way.get("node_ids") or [])
        } | set(destination_pois)
        if counts.get("ways") != len(raw_ways):
            errors.append("AOI receipt way count contradicts source topology")
        if counts.get("nodes", 0) < len(unique_nodes):
            errors.append("AOI receipt node count is smaller than source topology")
        if counts.get("relations") != len(topology_relations):
            errors.append("AOI receipt relation count contradicts source topology")
    if counts is not None and parent_counts is not None \
            and sum(counts.values()) >= sum(parent_counts.values()):
        errors.append("AOI extraction receipt does not prove a reduced bbox population")

    for index, feature in enumerate(trails):
        properties = feature.get("properties") if isinstance(feature, dict) else None
        if not isinstance(properties, dict):
            continue
        rendered_ids = properties.get("rendered_source_way_ids") or []
        for way_id in rendered_ids:
            way = raw_ways.get(way_id)
            if way is None:
                errors.append(
                    f"trail[{index}] rendered way w{way_id} is absent from AOI topology")
            elif way.get("_aoi_scope_class") != "bbox-selected":
                errors.append(
                    f"trail[{index}] renders non-standalone AOI reference w{way_id}")
        if properties.get("source") == "name-stitch":
            for way_id in properties.get("source_geometry_way_ids") or []:
                way = raw_ways.get(way_id)
                if way is not None and way.get("_aoi_scope_class") != "bbox-selected":
                    errors.append(
                        f"trail[{index}] standalone source w{way_id} is not bbox-selected")
    for index, feature in enumerate(removed):
        properties = feature.get("properties") if isinstance(feature, dict) else None
        if not isinstance(properties, dict) or properties.get("source") != "name-stitch":
            continue
        for way_id in properties.get("member_ways") or []:
            way = raw_ways.get(way_id)
            if way is None:
                errors.append(
                    f"removed[{index}] standalone way w{way_id} is absent from AOI topology")
            elif way.get("_aoi_scope_class") != "bbox-selected":
                errors.append(
                    f"removed[{index}] treats AOI smart reference w{way_id} as standalone")


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
            "restored_ways": [
                {
                    "way_id": way_id,
                    "tags": dict(by_id[way_id].get("tags") or {}),
                }
                for way_id in restored_way_ids
            ],
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


def _graph_relation_name(tags: dict) -> str | None:
    if tags.get("name"):
        return tags["name"]
    for key in sorted(tags):
        if key.startswith("name:") and tags[key]:
            return tags[key]
    return None


def _authoritative_root_display_name(root_id: int, expected: dict,
                                     authority: dict) -> str | None:
    """Mirror relation then first-eligible-main-way producer naming."""
    relation_name = _graph_relation_name(
        authority.get("relations", {}).get(root_id, {}).get("tags") or {})
    if relation_name:
        return relation_name
    raw_ways = authority.get("scopes", {}).get(
        "raw-denmark", authority).get("ways", {})
    for member in expected.get("direct_way_members", []):
        if (member.get("status") != "included"
                or member.get("effective_role") not in {"", "main"}):
            continue
        way_name = _graph_relation_name(
            raw_ways.get(member.get("way_id"), {}).get("tags") or {})
        if way_name:
            return way_name
    return None


def _derived_removed_thru_hike_relation_ids(authority: dict) -> set[int]:
    """Derive superroute parents and route children from raw graph authority."""
    raw_scope = authority.get("scopes", {}).get("raw-denmark", authority)
    relations = raw_scope.get("relations", {})
    route_ids = {
        relation_id for relation_id, relation in relations.items()
        if relation.get("accepted_hiking_route") is True
    }
    removed = set()
    for relation_id in route_ids:
        children = {
            member["ref"] for member in relations[relation_id].get("members", [])
            if member.get("type") == "relation" and member.get("ref") in route_ids
        }
        if children:
            removed.add(relation_id)
            removed.update(children)
    return removed


def _derived_assembly_status(root_id: int, expected: dict,
                             authority: dict) -> str:
    # Producer precedence is safety-first: a hierarchy with no eligible main
    # geometry stays entirely-filtered even when it belongs to a superroute.
    if not expected.get("eligible_main_way_ids"):
        return "entirely-filtered"
    if root_id in _derived_removed_thru_hike_relation_ids(authority):
        return "removed-thru-hike"
    return "emitted"


def _expected_relation_audit(root_id: int, authority: dict,
                             boundary=None) -> dict:
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
        raw_evidence = relations[entry["relation_id"]]["direct_ways"].get(
            entry["way_id"], {"status": "missing", "present": False,
                              "tags": None})
        scope_evidence = {}
        for scope_name, scope in authority.get("scopes", {}).items():
            relation_record = scope["relations"].get(entry["relation_id"])
            scope_evidence[scope_name] = (
                relation_record.get("direct_ways", {}).get(entry["way_id"])
                if relation_record is not None else None)
        tags = raw_evidence.get("tags") or {}
        effective_role = entry["effective_role"]
        if raw_evidence.get("status") != "present":
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
        included = raw_evidence.get("status") == "present" and decision.eligible
        if included and effective_role == "main":
            main_ways.append(entry["way_id"])
        elif included and effective_role in {"approach", "connection"}:
            spur_ways.append(entry["way_id"])
        raw_status = raw_evidence.get("status", "missing")
        prefilter_evidence = scope_evidence.get("prefiltered-denmark") or {}
        aoi_evidence = scope_evidence.get("aoi") or {}
        records.append({
            **entry,
            "source_status": (
                "available" if raw_status == "present" else raw_status),
            "raw_status": raw_status,
            "prefilter_status": prefilter_evidence.get("status", "missing"),
            "aoi_status": aoi_evidence.get("status", "missing"),
            "source_node_ids": raw_evidence.get("node_ids"),
            "source_coordinates": raw_evidence.get("coordinates"),
            "source_missing_node_ids": raw_evidence.get("missing_node_ids"),
            "tags": member_safety.decisive_tags(tags),
            "status": "included" if included else "excluded",
            "exclusion_reason": None if included else decision.reason,
            "decision": decision.to_dict(),
        })
    expected = {
        "relation_id": root_id,
        "name": None,
        "tags": relation["tags"],
        "relation_ids": hierarchy,
        "relation_scope_statuses": {
            str(relation_id): {
                "raw_status": (
                    "present" if relation_id in authority.get("scopes", {}).get(
                        "raw-denmark", authority)["relations"] else "missing"),
                "prefilter_status": (
                    "present" if relation_id in authority.get("scopes", {}).get(
                        "prefiltered-denmark", {"relations": {}})["relations"]
                    else "missing"),
                "aoi_status": (
                    "present" if relation_id in authority.get("scopes", {}).get(
                        "aoi", {"relations": {}})["relations"] else "missing"),
            }
            for relation_id in list(dict.fromkeys(hierarchy + missing_relations))
        },
        "direct_relation_way_ids": direct,
        "missing_relation_ids": missing_relations,
        "direct_way_members": records,
        "eligible_main_way_ids": list(dict.fromkeys(main_ways)),
        "included_spur_way_ids": list(dict.fromkeys(spur_ways)),
    }
    expected["name"] = _authoritative_root_display_name(
        root_id, expected, authority)
    if boundary is not None:
        _classify_expected_audit(
            expected, boundary,
            _derived_assembly_status(root_id, expected, authority), authority)
    return expected


def _expected_relation_area_status(relation_id: int, authority: dict,
                                   boundary) -> str:
    """Recompute recursive canonical/main relation relevance from raw scope."""
    from shapely.geometry import LineString

    raw_scope = authority.get("scopes", {}).get("raw-denmark", authority)
    relations = raw_scope.get("relations", {})
    seen: set[int] = set()

    def visit(current_id: int) -> list[str]:
        if current_id in seen:
            return []
        seen.add(current_id)
        relation = relations.get(current_id)
        if relation is None:
            return ["unknown"]
        results = []
        direct_ways = relation.get("direct_ways", {})
        for member in relation.get("members", []):
            role = str(member.get("role") or "").strip().casefold()
            if role not in {"", "main"}:
                continue
            if member.get("type") == "relation":
                results.extend(visit(member.get("ref")))
            elif member.get("type") == "way":
                way = direct_ways.get(member.get("ref"))
                coordinates = (way or {}).get("coordinates")
                if ((way or {}).get("status") != "present"
                        or not isinstance(coordinates, list)
                        or len(coordinates) < 2
                        or any(_point(point) is None for point in coordinates)):
                    results.append("unknown")
                    continue
                try:
                    clipped = LineString(coordinates).intersection(boundary)
                except Exception:  # noqa: BLE001
                    results.append("unknown")
                    continue
                results.append(
                    "intersects" if not clipped.is_empty and clipped.length > 0
                    else "outside")
        return results

    results = visit(relation_id)
    if "intersects" in results:
        return "intersects"
    if not results or "unknown" in results:
        return "unknown"
    return "outside"


def _classify_expected_audit(expected: dict, boundary,
                             assembly_status: str,
                             authority: dict) -> None:
    """Independently rebuild producer scope/area member dispositions."""
    from shapely.geometry import LineString

    removed_root = assembly_status == "removed-thru-hike"
    for relation_key, status in expected["relation_scope_statuses"].items():
        try:
            relation_id = int(relation_key)
        except (TypeError, ValueError):
            relation_id = 0
        exact_status = _expected_relation_area_status(
            relation_id, authority, boundary)
        status["exact_area_status"] = exact_status
        terminal_reason = None
        if removed_root:
            disposition = "removed-thru-hike-informational"
        elif status["raw_status"] != "present":
            disposition = "missing-source-relation"
            terminal_reason = "missing-source-relation"
        elif status["prefilter_status"] != "present":
            if exact_status == "outside":
                disposition = "out-of-area-prefilter-relation-absence"
            elif exact_status == "intersects":
                disposition = "prefilter-data-loss-relation"
                terminal_reason = "prefilter-data-loss-relation"
            else:
                disposition = "relation-location-unknown"
                terminal_reason = "relation-location-unknown"
        elif status["aoi_status"] != "present":
            if exact_status == "outside":
                disposition = "out-of-aoi-relation-informational"
            elif exact_status == "intersects":
                disposition = "aoi-relation-loss"
                terminal_reason = "aoi-relation-loss"
            else:
                disposition = "relation-location-unknown"
                terminal_reason = "relation-location-unknown"
        else:
            disposition = "relation-present-in-aoi"
        status["render_disposition"] = disposition
        status["terminal_reason"] = terminal_reason
    for record in expected["direct_way_members"]:
        coordinates = record.get("source_coordinates")
        exact_status = "unknown"
        if (isinstance(coordinates, list) and len(coordinates) >= 2
                and all(_point(point) is not None for point in coordinates)):
            try:
                clipped = LineString(coordinates).intersection(boundary)
                exact_status = (
                    "intersects" if not clipped.is_empty and clipped.length > 0
                    else "outside")
            except Exception:  # noqa: BLE001
                exact_status = "unknown"
        record["exact_area_status"] = exact_status
        terminal_reason = None
        if removed_root:
            disposition = "removed-thru-hike-informational"
        elif record["raw_status"] != "present":
            disposition = "raw-source-unavailable"
            terminal_reason = "raw-missing-relevant-member"
        elif record["prefilter_status"] != "present":
            if exact_status == "outside":
                disposition = "out-of-area-prefilter-absence"
            else:
                disposition = "prefilter-data-loss"
                terminal_reason = "prefilter-data-loss"
        elif record["aoi_status"] != "present":
            if exact_status == "intersects":
                disposition = "aoi-extraction-loss"
                terminal_reason = "aoi-extraction-loss"
            elif exact_status == "outside":
                disposition = "out-of-aoi-informational"
            else:
                disposition = "aoi-absent"
                terminal_reason = "aoi-loss-location-unknown"
        elif record["status"] == "excluded":
            if record["effective_role"] in {
                    "alternative", "alternate", "excursion"}:
                disposition = "variant-role-informational"
            elif exact_status == "outside":
                disposition = "unsafe-outside-exact-area"
            else:
                disposition = "unsafe-member-in-exact-area"
                terminal_reason = "unsafe-member-in-exact-area"
        elif exact_status == "intersects":
            disposition = "exact-area-render-member"
        elif exact_status == "outside":
            disposition = "out-of-area-informational"
        else:
            disposition = "member-location-unknown"
            terminal_reason = "member-location-unknown"
        record["render_disposition"] = disposition
        record["terminal_reason"] = terminal_reason


def _audit_resolution(audit: dict) -> tuple[list[str], dict | None]:
    members = audit["direct_way_members"]
    excluded = [record for record in members if record["status"] == "excluded"]
    terminal = [record for record in members if record.get("terminal_reason")]
    missing_way_ids = list(dict.fromkeys(
        record["way_id"] for record in members
        if record.get("raw_status") != "present"))
    status = audit.get("assembly_status")
    reasons = []
    if status != "removed-thru-hike":
        scope_statuses = audit.get("relation_scope_statuses") or {}
        for relation_status in scope_statuses.values():
            terminal_reason = relation_status.get("terminal_reason")
            if terminal_reason:
                reasons.append(terminal_reason)
        if not scope_statuses and audit["missing_relation_ids"]:
            reasons.append("missing-source-relation")
    reasons.extend(record["terminal_reason"] for record in terminal)
    if status == "entirely-filtered":
        reasons.append("entirely-filtered-route")
    elif status == "no-renderable-geometry":
        reasons.append("no-renderable-route-geometry")
    elif status not in {"emitted", "removed-thru-hike"}:
        reasons.append("invalid-assembly-status")
    reasons = list(dict.fromkeys(reasons))
    if not reasons:
        return reasons, None
    return reasons, {
        "relation_id": audit["relation_id"],
        "name": audit["name"],
        "assembly_status": status,
        "reasons": reasons,
        "excluded_way_ids": list(dict.fromkeys(
            record["way_id"] for record in excluded)),
        "terminal_way_ids": list(dict.fromkeys(
            record["way_id"] for record in terminal)),
        "missing_way_ids": missing_way_ids,
        "missing_relation_ids": audit["missing_relation_ids"],
    }


def _audit_has_root_specific_outside_gap(expected: dict,
                                         status: str) -> bool:
    included = [
        {"way_id": record["way_id"],
         "node_ids": record.get("source_node_ids") or []}
        for record in expected["direct_way_members"]
        if record["status"] == "included"
        and record.get("effective_role") in {"", "main"}
    ]
    outside_connectors = [
        {"way_id": record["way_id"],
         "node_ids": record.get("source_node_ids") or []}
        for record in expected["direct_way_members"]
        if record["status"] == "excluded"
        and record.get("effective_role") in {"", "main"}
        and record.get("exact_area_status") == "outside"
        and record.get("terminal_reason") is None
    ]
    return bool(
        status == "emitted" and outside_connectors
        and _source_component_count(included) > 1
        and _source_component_count([*included, *outside_connectors]) == 1)


def _authoritative_exact_area_root(expected: dict, boundary):
    """Build one root's exact-area linework and contributing way identity."""
    from shapely.geometry import LineString
    from shapely.ops import unary_union

    if boundary is None:
        return [], None
    way_ids = []
    lines = []
    for member in expected.get("direct_way_members", []):
        if (member.get("status") != "included"
                or member.get("effective_role") not in {"", "main"}
                or member.get("exact_area_status") != "intersects"
                or member.get("render_disposition") !=
                "exact-area-render-member"):
            continue
        way_id = member.get("way_id")
        coordinates = member.get("source_coordinates")
        if (way_id in way_ids or not isinstance(coordinates, list)
                or len(coordinates) < 2
                or any(_point(point) is None for point in coordinates)):
            continue
        way_ids.append(way_id)
        lines.append(LineString(coordinates))
    if not lines:
        return way_ids, None
    try:
        geometry = unary_union(lines).intersection(boundary)
    except Exception:  # noqa: BLE001 - malformed authority is already terminal
        return way_ids, None
    if geometry.is_empty or geometry.length <= 0:
        return way_ids, None
    return way_ids, geometry


def _authoritative_preclip_root(expected: dict):
    """Build complete eligible main-way linework without area selection."""
    from shapely.geometry import LineString
    from shapely.ops import unary_union

    way_ids = []
    lines = []
    for member in expected.get("direct_way_members", []):
        if (member.get("status") != "included"
                or member.get("effective_role") not in {"", "main"}):
            continue
        way_id = member.get("way_id")
        coordinates = member.get("source_coordinates")
        if (way_id in way_ids or not isinstance(coordinates, list)
                or len(coordinates) < 2
                or any(_point(point) is None for point in coordinates)):
            continue
        way_ids.append(way_id)
        lines.append(LineString(coordinates))
    if not lines:
        return way_ids, None
    try:
        geometry = unary_union(lines)
    except Exception:  # noqa: BLE001 - malformed authority fails closed later
        return way_ids, None
    if geometry.is_empty or geometry.length <= 0:
        return way_ids, None
    return way_ids, geometry


def _drop_descriptor_matches_root(descriptor: dict, root: dict,
                                  boundary) -> bool:
    required_way_ids = root["eligible_main_way_ids"]
    if not required_way_ids or not set(required_way_ids).issubset(
            descriptor["geometry_way_ids"]):
        return False
    evidence = descriptor["properties"].get("drop_evidence") or {}
    category = descriptor["category"]
    if category in _CURATION_REMOVAL_CATEGORIES:
        if evidence.get("kind") != "curation":
            return False
    elif category in _CLIP_REMOVAL_CATEGORIES:
        if evidence.get("kind") != category:
            return False
    elif category in _ABSORPTION_REMOVAL_CATEGORIES:
        if evidence.get("kind") != category:
            return False
        geometry = descriptor.get("geometry")
        exact_geometry = root.get("geometry")
        if geometry is None or exact_geometry is None:
            return False
        try:
            return exact_geometry.difference(geometry).is_empty
        except Exception:  # noqa: BLE001 - malformed evidence fails closed
            return False
    else:
        return False
    geometry = descriptor.get("geometry")
    if geometry is None or boundary is None:
        return False
    try:
        exact_geometry = geometry.intersection(boundary)
        if root["geometry"] is None:
            # Curation precedes clipping. A fully outside relation can
            # legitimately hit any independently reproducible curation rule;
            # outside-exact-area remains the clip-stage disposition.
            return exact_geometry.is_empty
        return root["geometry"].difference(exact_geometry).is_empty
    except Exception:  # noqa: BLE001 - malformed removal geometry fails closed
        return False


def _validate_relation_drop_descriptor(
        descriptor: dict, root_ids: list[int], root_records: dict[int, dict],
        authority: dict, raw_ways: dict[int, dict], boundary,
        output_descriptors: list[dict], errors: list[str]) -> None:
    """Bind one producer drop row to raw graph identity and exact geometry."""
    from shapely.geometry import LineString
    from shapely.ops import unary_union

    properties = descriptor["properties"]
    label = descriptor["label"]
    category = descriptor["category"]
    seen_relations = set()
    expected_relation_ids = []
    expected_direct = {}
    required_way_ids = []
    expected_member_way_ids = []
    for root_id in root_ids:
        root = root_records[root_id]
        for way_id in root["eligible_main_way_ids"]:
            if way_id not in required_way_ids:
                required_way_ids.append(way_id)
            if way_id not in expected_member_way_ids:
                expected_member_way_ids.append(way_id)
        for way_id in root["included_spur_way_ids"]:
            if way_id not in expected_member_way_ids:
                expected_member_way_ids.append(way_id)
        for relation_id in _graph_hierarchy(
                root_id, authority.get("relations", {}), seen_relations):
            expected_relation_ids.append(relation_id)
            expected_direct[relation_id] = list(
                authority["relations"][relation_id]["direct_way_ids"])
        if (descriptor["name_key"] != root["name_key"]
                and not _valid_promoted_hike_name(
                    properties, root_id, root["name"], authority)):
            errors.append(
                f"{label} name contradicts authority-derived root r{root_id}")

    member_ways = properties.get("member_ways")
    geometry_way_ids = properties.get("source_geometry_way_ids")
    rendered_way_ids = properties.get("rendered_source_way_ids")
    if (not _positive_int_list(member_ways)
            or len(member_ways) != len(set(member_ways))):
        errors.append(f"{label} member identity is invalid")
        member_ways = []
    if (not _positive_int_list(geometry_way_ids)
            or len(geometry_way_ids) != len(set(geometry_way_ids))):
        errors.append(f"{label} source geometry identity is invalid")
        geometry_way_ids = []
    if (not _nonnegative_int_list(rendered_way_ids)
            or len(rendered_way_ids) != len(set(rendered_way_ids))):
        errors.append(f"{label} rendered source identity is invalid")
        rendered_way_ids = []
    if member_ways != expected_member_way_ids:
        errors.append(
            f"{label} member ways contradict complete authoritative preclip "
            "identity")
    if geometry_way_ids != required_way_ids:
        errors.append(
            f"{label} source geometry way ids contradict complete authoritative "
            "preclip identity")
    expected_ckey = (
        "w" + "-".join(str(way_id) for way_id in sorted(member_ways))
        if member_ways else None)
    if properties.get("ckey") != expected_ckey:
        errors.append(f"{label} ckey contradicts member identity")
    if properties.get("root_relation_ids") != root_ids:
        errors.append(f"{label} root relation ids contradict dropped authority")
    if properties.get("relation_ids") != expected_relation_ids:
        errors.append(f"{label} relation ids contradict dropped authority")
    declared_direct = _normalize_direct_members(
        properties.get("direct_relation_way_ids"), label, errors)
    if declared_direct != expected_direct:
        errors.append(f"{label} direct relation membership contradicts authority")

    source_records = properties.get("source_ways")
    if not isinstance(source_records, list):
        errors.append(f"{label} source-way evidence is invalid")
        source_records = []
    source_ids = [record.get("way_id") for record in source_records
                  if isinstance(record, dict)]
    if source_ids != geometry_way_ids:
        errors.append(f"{label} source-way order contradicts geometry identity")
    expected_rendered = []
    source_lines = []
    for index, record in enumerate(source_records):
        record_label = f"{label}.source_ways[{index}]"
        if not isinstance(record, dict):
            errors.append(f"{record_label} is not an object")
            continue
        way_id = record.get("way_id")
        authority_way = authority.get("ways", {}).get(way_id)
        if not isinstance(authority_way, dict) \
                or authority_way.get("status") != "present":
            topology_way = raw_ways.get(way_id)
            authority_way = ({
                "status": "present",
                "node_ids": topology_way.get("node_ids"),
                "coordinates": topology_way.get("coordinates"),
                "missing_node_ids": topology_way.get("missing_node_ids", []),
                "tags": topology_way.get("tags"),
            } if isinstance(topology_way, dict) else None)
        if not isinstance(authority_way, dict):
            errors.append(f"{record_label} lacks raw source authority")
            continue
        expected_record = {
            "way_id": way_id,
            "node_ids": authority_way.get("node_ids"),
            "missing_node_ids": authority_way.get("missing_node_ids") or [],
            "coordinates": authority_way.get("coordinates"),
            "tags": member_safety.decisive_tags(
                authority_way.get("tags") or {}),
            "direct_relation_ids": [
                relation_id for relation_id in expected_relation_ids
                if way_id in expected_direct.get(relation_id, [])
            ],
        }
        for field, expected_value in expected_record.items():
            if record.get(field) != expected_value:
                errors.append(
                    f"{record_label} {field} contradicts raw authority")
        coordinates = authority_way.get("coordinates")
        if (not isinstance(coordinates, list) or len(coordinates) < 2
                or any(_point(point) is None for point in coordinates)):
            errors.append(f"{record_label} has incomplete raw geometry")
            continue
        line = LineString(coordinates)
        source_lines.append(line)
        renders = False
        if boundary is not None:
            try:
                clipped = line.intersection(boundary)
                renders = not clipped.is_empty and clipped.length > 0
            except Exception:  # noqa: BLE001
                pass
        if record.get("renders_in_area") is not renders:
            errors.append(f"{record_label} rendered-area flag is inconsistent")
        if renders:
            expected_rendered.append(way_id)
    if rendered_way_ids != expected_rendered:
        errors.append(f"{label} rendered source ways contradict exact area")
    if len(source_lines) != len(required_way_ids):
        errors.append(f"{label} lacks complete authoritative preclip geometry")
    elif descriptor.get("geometry") is not None:
        try:
            authoritative_preclip = unary_union(source_lines)
            expected_geometry = (
                authoritative_preclip.intersection(boundary)
                if category in _ABSORPTION_REMOVAL_CATEGORIES
                else authoritative_preclip)
            if not expected_geometry.equals(descriptor["geometry"]):
                errors.append(f"{label} geometry contradicts raw source ways")
        except Exception:  # noqa: BLE001
            errors.append(f"{label} geometry cannot be checked against authority")

    evidence = properties.get("drop_evidence")
    if not isinstance(evidence, dict):
        errors.append(f"{label} typed drop evidence is missing")
        return
    if category in _ABSORPTION_REMOVAL_CATEGORIES:
        survivor_claim = evidence.get("survivor")
        survivor_matches = []
        if isinstance(survivor_claim, dict):
            for output in output_descriptors:
                output_properties = output.get("properties") or {}
                if survivor_claim == {
                        "ckey": output_properties.get("ckey"),
                        "name": output_properties.get("name"),
                        "source": output_properties.get("source"),
                        "geometry_sha256": _publisher_json_sha256(
                            output.get("geometry_document") or {})}:
                    survivor_matches.append(output)
        if len(survivor_matches) != 1:
            errors.append(
                f"{label} absorption does not identify one unambiguous survivor")
            return
        survivor = survivor_matches[0]
        survivor_properties = survivor["properties"]
        survivor_way_ids = survivor_properties.get("source_geometry_way_ids")
        if (not _positive_int_list(survivor_way_ids)
                or len(survivor_way_ids) != len(set(survivor_way_ids))):
            errors.append(f"{label} survivor source geometry identity is invalid")
            return
        survivor_lines = []
        for way_id in survivor_way_ids:
            authority_way = authority.get("ways", {}).get(way_id)
            if not isinstance(authority_way, dict) \
                    or authority_way.get("status") != "present":
                topology_way = raw_ways.get(way_id)
                authority_way = ({
                    "status": "present",
                    "coordinates": topology_way.get("coordinates"),
                } if isinstance(topology_way, dict) else None)
            coordinates = ((authority_way or {}).get("coordinates")
                           if isinstance(authority_way, dict) else None)
            if (not isinstance(coordinates, list) or len(coordinates) < 2
                    or any(_point(point) is None for point in coordinates)):
                errors.append(
                    f"{label} survivor w{way_id} lacks complete raw geometry")
                continue
            survivor_lines.append(LineString(coordinates))
        if len(survivor_lines) != len(survivor_way_ids):
            return
        try:
            survivor_preclip = unary_union(survivor_lines)
            absorbed_exact = descriptor["geometry"]
            survivor_exact = survivor["geometry"]
            covered = absorbed_exact.difference(survivor_exact).is_empty
            equal = covered and survivor_exact.difference(
                absorbed_exact).is_empty
        except Exception:  # noqa: BLE001
            errors.append(f"{label} absorption geometry cannot be recomputed")
            return
        expected_evidence = {
            "kind": assembly_model.GEOMETRY_DUPLICATE_ABSORBED_CATEGORY,
            "reason": assembly_model.TERMINAL_ABSORPTION_EVIDENCE_REASON,
            "survivor": survivor_claim,
            "geometry_proof": {
                "method": "exact-area-linework-difference",
                "absorbed_covered_by_survivor": covered,
                "survivor_covered_by_absorbed": equal,
                "relationship": (
                    "equal" if equal else "covered-by-survivor"),
            },
        }
        if evidence != expected_evidence:
            errors.append(f"{label} absorption evidence is invalid")
        if properties.get("removed_reason") != \
                assembly_model.TERMINAL_GEOMETRY_DUPLICATE_ABSORBED_REASON:
            errors.append(f"{label} absorption reason is invalid")
        if not covered:
            errors.append(f"{label} survivor loses absorbed exact-area linework")
        for root_id in root_ids:
            root = root_records[root_id]
            preclip = root.get("preclip_geometry")
            try:
                if (preclip is None
                        or not preclip.difference(survivor_preclip).is_empty):
                    errors.append(
                        f"{label} survivor loses authoritative root r{root_id} "
                        "preclip linework")
                exact_geometry = root.get("geometry")
                if (exact_geometry is not None
                        and (survivor_exact is None or not exact_geometry.difference(
                            survivor_exact).is_empty)):
                    errors.append(
                        f"{label} survivor loses authoritative root r{root_id} "
                        "exact-area linework")
            except Exception:  # noqa: BLE001
                errors.append(
                    f"{label} survivor geometry cannot prove root r{root_id}")
        return
    if category in _CURATION_REMOVAL_CATEGORIES:
        expected_evidence = {
            "kind": "curation",
            "category": category,
            "min_length_mi": PILOT_MIN_LENGTH_MI,
        }
        if evidence != expected_evidence:
            errors.append(f"{label} curation evidence is invalid")
        miles = properties.get("length_mi")
        for root_id in root_ids:
            root_tags = authority.get("relations", {}).get(
                root_id, {}).get("tags") or {}
            expected_category = None
            expected_reason = None
            if _number(miles):
                expected_category, expected_reason = \
                    assembly_model._removal_verdict(
                        SimpleNamespace(
                            name=root_records[root_id]["name"], tags=root_tags,
                            source="relation", length_mi=float(miles)),
                        PILOT_MIN_LENGTH_MI, "dk")
            if (category != expected_category
                    or properties.get("removed_reason") != expected_reason):
                errors.append(
                    f"{label} curation category/reason is not reproducible "
                    f"for root r{root_id}")
        if category == "min-length":
            measured = _linework_miles(_geometry_lines_for_validation(
                descriptor["feature"]))
            if (not _number(miles) or float(miles) >= PILOT_MIN_LENGTH_MI
                    or round(measured, 3) != miles):
                errors.append(f"{label} min-length evidence is false")
        return
    expected_keys = {"kind", "area", "min_inside_mi", "inside_mi"}
    if (set(evidence) != expected_keys or evidence.get("kind") != category
            or evidence.get("area") != AREA_NAME
            or evidence.get("min_inside_mi") != PILOT_MIN_INSIDE_MI
            or not _number(evidence.get("inside_mi"))):
        errors.append(f"{label} exact-clip evidence is invalid")
        return
    try:
        clipped = descriptor["geometry"].intersection(boundary)
        measured_inside = _linework_miles([
            [[float(x), float(y)] for x, y in part.coords]
            for part in _shapely_line_parts(clipped)
        ])
    except Exception:  # noqa: BLE001
        errors.append(f"{label} exact-clip evidence cannot be recomputed")
        return
    if abs(float(evidence["inside_mi"]) - round(measured_inside, 12)) > 1e-12:
        errors.append(f"{label} exact-clip mileage is inconsistent")
    if category == "outside-exact-area":
        if measured_inside > 0:
            errors.append(f"{label} outside-area evidence is false")
    elif not 0 < measured_inside < PILOT_MIN_INSIDE_MI:
        errors.append(f"{label} min-inside evidence is false")


def _valid_promoted_hike_name(properties: dict, root_id: int,
                              root_name: str | None,
                              authority: dict) -> bool:
    """Identify a local-route promotion; sealed POI replay runs separately."""
    destinations = properties.get("destinations")
    if (properties.get("source") != "relation"
            or properties.get("kind") != "hike"
            or not isinstance(destinations, list) or len(destinations) != 1
            or not isinstance(destinations[0], str)
            or not destinations[0].strip()
            or properties.get("name") != assembly_model._hike_name(
                destinations[0])):
        return False
    tags = authority.get("relations", {}).get(root_id, {}).get("tags") or {}
    if str(tags.get("network", "")).strip().lower() in {"rwn", "nwn", "iwn"}:
        return False
    return assembly_model.classify_kind(root_name, tags) == "route"


def _independent_earth_distance_ft(left: tuple[float, float],
                                   right: tuple[float, float]) -> float:
    """Great-circle feet using the documented 3,958.7613-mile earth radius."""
    lon1, lat1 = left
    lon2, lat2 = right
    latitude1 = math.radians(lat1)
    latitude2 = math.radians(lat2)
    latitude_delta = math.radians(lat2 - lat1)
    longitude_delta = math.radians(lon2 - lon1)
    haversine = (
        math.sin(latitude_delta / 2.0) ** 2
        + math.cos(latitude1) * math.cos(latitude2)
        * math.sin(longitude_delta / 2.0) ** 2
    )
    miles = 2.0 * 3958.7613 * math.asin(
        min(1.0, math.sqrt(haversine)))
    return miles * 5280.0


def _independent_true_endpoints(lines: list[list[Any]]) -> list[tuple[float, float]]:
    """Derive degree-one vertices without using assembly-model helpers."""
    degree: dict[tuple[float, float], int] = {}
    segments: set[tuple[tuple[float, float], tuple[float, float]]] = set()
    for line in lines:
        points = [_point(value) for value in line]
        if any(point is None for point in points):
            continue
        for left, right in zip(points, points[1:]):
            if left == right:
                continue
            segment = tuple(sorted((left, right)))
            if segment in segments:
                continue
            segments.add(segment)
            degree[left] = degree.get(left, 0) + 1
            degree[right] = degree.get(right, 0) + 1
    return sorted(point for point, count in degree.items() if count == 1)


def _independent_root_endpoint_sources(
        root_id: int, root: dict, authority: dict) -> list[dict[str, Any]]:
    """Derive one root's pre-weld main-way endpoint identities."""
    source_lines = []
    source_nodes = []
    for way_id in root.get("eligible_main_way_ids") or []:
        way = authority.get("ways", {}).get(way_id)
        if not isinstance(way, dict):
            continue
        node_ids = way.get("node_ids")
        coordinates = way.get("coordinates")
        if (not isinstance(node_ids, list) or not isinstance(coordinates, list)
                or len(node_ids) != len(coordinates)):
            continue
        points = [_point(coordinate) for coordinate in coordinates]
        if len(points) < 2 or any(point is None for point in points):
            continue
        source_lines.append(points)
        source_nodes.append((way_id, list(zip(node_ids, points))))
    endpoint_coordinates = set(_independent_true_endpoints(source_lines))
    records = []
    seen = set()
    for way_id, node_values in source_nodes:
        for node_id, coordinate in node_values:
            identity = (coordinate, way_id, node_id)
            if coordinate not in endpoint_coordinates or identity in seen:
                continue
            seen.add(identity)
            records.append({
                "root_relation_id": root_id,
                "way_id": way_id,
                "node_id": node_id,
                "coordinate": coordinate,
            })
    return sorted(records, key=lambda value: (
        value["coordinate"], value["way_id"], value["node_id"]))


def _independent_candidate_promotion_endpoints(
        descriptor: dict, root_ids: list[int], roots: dict[int, dict],
        authority: dict) -> list[dict[str, Any]]:
    feature = descriptor.get("feature")
    final_lines = (_geometry_lines_for_validation(feature)
                   if isinstance(feature, dict) else [])
    final_endpoints = set(_independent_true_endpoints(final_lines))
    grouped: dict[tuple[float, float], list[dict[str, Any]]] = {}
    for root_id in root_ids:
        root = roots.get(root_id)
        if not isinstance(root, dict):
            continue
        for source in _independent_root_endpoint_sources(
                root_id, root, authority):
            coordinate = source["coordinate"]
            if coordinate not in final_endpoints:
                continue
            value = {
                "root_relation_id": root_id,
                "way_id": source["way_id"],
                "node_id": source["node_id"],
            }
            values = grouped.setdefault(coordinate, [])
            if value not in values:
                values.append(value)
    return [{
        "coordinate": coordinate,
        "sources": sorted(grouped[coordinate], key=lambda value: (
            value["root_relation_id"], value["way_id"], value["node_id"])),
    } for coordinate in sorted(grouped)]


def _independent_ambiguous_destination_record(
        poi: dict, endpoint: dict[str, Any], distance_ft: float,
        selection_rank: int) -> dict[str, Any]:
    return {
        "osm_node_id": poi["node_id"],
        "name": poi["name"],
        "selected_endpoint_coordinate": list(endpoint["coordinate"]),
        "distance_ft": round(distance_ft, 6),
        "selection_rank": selection_rank,
        "endpoint_sources": [dict(source) for source in endpoint["sources"]],
    }


def _independent_destination_proposal(
        descriptor: dict, root_ids: list[int], roots: dict[int, dict],
        authority: dict) -> dict[str, Any]:
    """Independently rank POIs with ambiguity fall-through for one candidate."""
    source_endpoints = _independent_candidate_promotion_endpoints(
        descriptor, root_ids, roots, authority)
    if not source_endpoints:
        return {
            "proposal": None,
            "reason": "no-authoritative-endpoints",
            "skipped_ambiguous_pois": [],
        }

    ranked = []
    for candidate in (authority.get("destination_pois") or {}).values():
        if candidate.get("name") is None:
            continue
        coordinate = _point(candidate.get("coordinate"))
        if coordinate is None:
            continue
        distances = [
            (_independent_earth_distance_ft(
                endpoint["coordinate"], coordinate), endpoint_index, endpoint)
            for endpoint_index, endpoint in enumerate(source_endpoints)
        ]
        distance_ft, endpoint_index, endpoint = min(
            distances, key=lambda value: (value[0], value[1]))
        if distance_ft <= assembly_model.SPUR_POI_REACH_FT:
            ranked.append((distance_ft, candidate["node_id"], endpoint_index,
                           endpoint, candidate))
    ranked.sort(key=lambda value: (value[0], value[1], value[2]))
    if not ranked:
        return {
            "proposal": None,
            "reason": "no-eligible-poi-in-reach",
            "skipped_ambiguous_pois": [],
        }

    skipped = []
    for selection_rank, (distance_ft, _poi_id, endpoint_index, endpoint,
                         candidate) in enumerate(ranked, 1):
        source_identities = {
            (source["root_relation_id"], source["way_id"], source["node_id"])
            for source in endpoint["sources"]
        }
        root_values = {identity[0] for identity in source_identities}
        if len(root_values) != 1 or len(source_identities) != 1:
            skipped.append(_independent_ambiguous_destination_record(
                candidate, endpoint, distance_ft, selection_rank))
            continue
        root_id, way_id, node_id = next(iter(source_identities))
        evidence = {
            "osm_node_id": candidate["node_id"],
            "name": candidate["name"],
            "tags": candidate["tags"],
            "eligibility_class": candidate["eligibility_class"],
            "coordinate": list(candidate["coordinate"]),
            "selected_endpoint_index": endpoint_index,
            "selected_endpoint_coordinate": list(endpoint["coordinate"]),
            "distance_ft": round(distance_ft, 6),
            "reach_limit_ft": assembly_model.SPUR_POI_REACH_FT,
            "selection_rank": selection_rank,
            "promotion_root_relation_id": root_id,
            "endpoint_source_way_id": way_id,
            "endpoint_source_node_id": node_id,
        }
        if skipped:
            evidence["skipped_ambiguous_pois"] = skipped
        return {
            "proposal": {
                "poi": candidate,
                "distance_ft": distance_ft,
                "endpoint_index": endpoint_index,
                "endpoint": endpoint,
                "evidence": evidence,
            },
            "reason": None,
            "skipped_ambiguous_pois": skipped,
        }
    return {
        "proposal": None,
        "reason": "ambiguous-endpoint-authority",
        "skipped_ambiguous_pois": skipped,
    }


def _validate_promoted_destination_evidence(
        descriptor: dict, root_ids: list[int], roots: dict[int, dict],
        authority: dict, errors: list[str]) -> None:
    """Replay one promotion from sealed POIs and pre-weld root endpoints."""
    properties = descriptor.get("properties") or {}
    label = descriptor.get("label", "candidate")
    evidence_values = properties.get("destination_evidence")
    if (not isinstance(evidence_values, list) or len(evidence_values) != 1
            or not isinstance(evidence_values[0], dict)):
        errors.append(f"{label} promoted destination evidence is missing or malformed")
        return
    evidence = evidence_values[0]
    required_keys = {
        "osm_node_id", "name", "tags", "eligibility_class", "coordinate",
        "selected_endpoint_coordinate", "distance_ft", "reach_limit_ft",
        "selection_rank", "promotion_root_relation_id",
        "endpoint_source_way_id", "endpoint_source_node_id",
    }
    allowed_keys = required_keys | {
        "selected_endpoint_index", "skipped_ambiguous_pois",
    }
    if (not required_keys.issubset(evidence)
            or not set(evidence).issubset(allowed_keys)):
        errors.append(f"{label} promoted destination evidence is missing or malformed")
        return
    promotion_root_id = evidence.get("promotion_root_relation_id")
    root = roots.get(promotion_root_id)
    if (not isinstance(promotion_root_id, int)
            or isinstance(promotion_root_id, bool)
            or promotion_root_id not in root_ids or not isinstance(root, dict)):
        errors.append(
            f"{label} promoted destination root lacks contributing graph authority")
        return
    if not _valid_promoted_hike_name(
            properties, promotion_root_id, root.get("name"), authority):
        errors.append(
            f"{label} promoted hike identity contradicts authority-derived "
            f"root r{promotion_root_id}")
        return

    poi_id = evidence.get("osm_node_id")
    trusted_pois = authority.get("destination_pois") or {}
    trusted = trusted_pois.get(poi_id)
    if trusted is None:
        errors.append(f"{label} promoted destination OSM node is not trusted")
        return
    trusted_identity = {
        "osm_node_id": trusted["node_id"],
        "name": trusted["name"],
        "tags": trusted["tags"],
        "eligibility_class": trusted["eligibility_class"],
        "coordinate": trusted["coordinate"],
    }
    if any(evidence.get(key) != value
           for key, value in trusted_identity.items()):
        errors.append(
            f"{label} promoted destination identity contradicts trusted AOI topology")
        return
    if (properties.get("destinations") != [trusted["name"]]
            or properties.get("name") != assembly_model._hike_name(
                trusted["name"])):
        errors.append(f"{label} promoted destination name evidence is inconsistent")

    source_endpoints = _independent_candidate_promotion_endpoints(
        descriptor, root_ids, roots, authority)
    declared_source = (
        promotion_root_id,
        evidence.get("endpoint_source_way_id"),
        evidence.get("endpoint_source_node_id"),
    )
    identity_matches = []
    for endpoint_index, endpoint in enumerate(source_endpoints):
        source_identities = {
            (source["root_relation_id"], source["way_id"], source["node_id"])
            for source in endpoint["sources"]
        }
        if declared_source in source_identities:
            identity_matches.append((endpoint_index, endpoint))
    if len(identity_matches) != 1:
        errors.append(
            f"{label} promoted destination root/source identity contradicts "
            "authoritative endpoint")
        return

    selected_endpoint_index, selected_endpoint = identity_matches[0]
    selected_coordinate = _point(evidence.get("selected_endpoint_coordinate"))
    if (selected_coordinate is None
            or selected_endpoint["coordinate"] != selected_coordinate):
        errors.append(
            f"{label} promoted destination selected endpoint is not an "
            "authoritative source endpoint")
        return
    endpoint_sources = selected_endpoint["sources"]
    endpoint_root_ids = {
        source["root_relation_id"] for source in endpoint_sources
    }
    if len(endpoint_root_ids) != 1:
        errors.append(
            f"{label} promoted destination endpoint has ambiguous root authority")
        return
    expected_sources = {
        (source["root_relation_id"], source["way_id"], source["node_id"])
        for source in endpoint_sources
    }
    if len(expected_sources) != 1 or declared_source not in expected_sources:
        errors.append(
            f"{label} promoted destination root/source identity contradicts "
            "authoritative endpoint")
        return
    if "selected_endpoint_index" in evidence:
        diagnostic_index = evidence["selected_endpoint_index"]
        if (not isinstance(diagnostic_index, int)
                or isinstance(diagnostic_index, bool)
                or diagnostic_index != selected_endpoint_index):
            errors.append(
                f"{label} promoted destination endpoint index diagnostic is "
                "inconsistent")

    replay = _independent_destination_proposal(
        descriptor, root_ids, roots, authority)
    expected = replay["proposal"]
    if expected is None:
        if replay["reason"] == "ambiguous-endpoint-authority":
            errors.append(
                f"{label} promoted destination endpoint has ambiguous root authority")
        else:
            errors.append(
                f"{label} promoted hike has no trusted destination within "
                "endpoint reach")
        return
    expected_evidence = expected["evidence"]
    expected_id = expected_evidence["osm_node_id"]
    expected_endpoint_index = expected["endpoint_index"]
    expected_endpoint = expected["endpoint"]["coordinate"]
    if poi_id != expected_id:
        errors.append(
            f"{label} promoted destination selection violates "
            "distance/OSM-ID rank")
    expected_endpoint_sources = {
        (source["root_relation_id"], source["way_id"], source["node_id"])
        for source in expected["endpoint"]["sources"]
    }
    if (len(expected_endpoint_sources) != 1
            or declared_source not in expected_endpoint_sources
            or selected_coordinate != expected_endpoint):
        errors.append(
            f"{label} promoted destination selected endpoint violates "
            "deterministic rank")
    if (evidence.get("reach_limit_ft") != assembly_model.SPUR_POI_REACH_FT
            or evidence.get("selection_rank") !=
            expected_evidence["selection_rank"]):
        errors.append(f"{label} promoted destination rank/reach evidence is invalid")
    if (evidence.get("skipped_ambiguous_pois", []) !=
            replay["skipped_ambiguous_pois"]):
        errors.append(
            f"{label} promoted destination ambiguity evidence is inconsistent")
    measured = evidence.get("distance_ft")
    if (not _number(measured)
            or float(measured) != expected_evidence["distance_ft"]):
        errors.append(
            f"{label} promoted destination measured distance is inconsistent")
    if expected_endpoint_index != selected_endpoint_index:
        errors.append(
            f"{label} promoted destination selected endpoint violates "
            "deterministic rank")


def _independent_promotion_source_kind(name: str | None,
                                       tags: dict[str, Any]) -> str:
    network = str(tags.get("network", "")).strip().lower()
    if network in {"rwn", "nwn", "iwn"}:
        return "route"
    if isinstance(name, str) and (
            "--" in name or re.search(r"\broute\b", name, re.IGNORECASE)):
        return "route"
    return "trail"


def _validate_exact_denmark_promotion_ledger(
        quality: dict, candidates: list[dict], roots: dict[int, dict],
        authority: dict, derived_roots_by_candidate: dict[int, list[int]],
        errors: list[str]) -> None:
    """Recompute every relation promotion and require a complete exact ledger."""
    root_order = authority.get("root_relation_ids") or []
    root_positions = {
        root_id: position for position, root_id in enumerate(root_order)
    }
    states = []
    proposals = {}
    for feature in sorted(
            (candidate for candidate in candidates
             if isinstance(candidate, dict)
             and isinstance(candidate.get("properties"), dict)),
            key=lambda candidate: candidate["properties"].get(
                "quality_candidate_index", -1)):
        properties = feature["properties"]
        if properties.get("source") != "relation":
            continue
        candidate_index = properties.get("quality_candidate_index")
        declared_root_ids = properties.get("root_relation_ids")
        if (not isinstance(candidate_index, int)
                or isinstance(candidate_index, bool)
                or not isinstance(declared_root_ids, list)
                or any(not isinstance(root_id, int)
                       or isinstance(root_id, bool)
                       for root_id in declared_root_ids)):
            continue
        root_ids = derived_roots_by_candidate.get(candidate_index, [])
        descriptor = {
            "feature": feature,
            "properties": properties,
            "label": f"relation candidate[{candidate_index}]",
        }
        ordered_roots = sorted(
            (root_id for root_id in root_ids if root_id in roots),
            key=lambda root_id: (
                root_positions.get(root_id, len(root_positions)), root_id),
        )
        identity = {
            "candidate_index": candidate_index,
            "candidate_ckey": properties.get("ckey"),
            "root_relation_ids": ordered_roots,
        }
        source_root_id = ordered_roots[0] if ordered_roots else None
        source_root = roots.get(source_root_id, {})
        source_tags = (authority.get("relations", {}).get(
            source_root_id, {}).get("tags") or {})
        source_name = source_root.get("name")
        source_kind = _independent_promotion_source_kind(
            source_name, source_tags)
        blocking_roots = []
        if len(ordered_roots) > 1:
            identity["identity_root_relation_id"] = source_root_id
            if properties.get("identity_root_relation_id") != source_root_id:
                errors.append(
                    f"relation candidate[{candidate_index}] "
                    "identity_root_relation_id diverges: "
                    f"expected {source_root_id!r}, got "
                    f"{properties.get('identity_root_relation_id')!r}")
            for field in (
                    "network", "operator", "sac_scale", "trail_visibility"):
                expected_value = source_tags.get(field, "")
                if properties.get(field) != expected_value:
                    errors.append(
                        f"relation candidate[{candidate_index}] identity field "
                        f"{field!r} diverges: expected {expected_value!r}, "
                        f"got {properties.get(field)!r}")
            for root_id in ordered_roots:
                root_tags = (authority.get("relations", {}).get(
                    root_id, {}).get("tags") or {})
                root_name = roots.get(root_id, {}).get("name")
                reason = None
                if str(root_tags.get("network", "")).strip().lower() in {
                        "rwn", "nwn", "iwn"}:
                    reason = "not-local-route"
                elif _independent_promotion_source_kind(
                        root_name, root_tags) != "route":
                    reason = "not-route-candidate"
                if reason is not None:
                    blocking_roots.append({
                        "root_relation_id": root_id,
                        "reason": reason,
                    })
        elif "identity_root_relation_id" in properties:
            errors.append(
                f"relation candidate[{candidate_index}] "
                "identity_root_relation_id diverges: expected absence, got "
                f"{properties.get('identity_root_relation_id')!r}")

        if blocking_roots:
            core = {
                "decision": "declined",
                "reason": (
                    "not-local-route"
                    if any(blocker["reason"] == "not-local-route"
                           for blocker in blocking_roots)
                    else "not-route-candidate"
                ),
                "blocking_roots": blocking_roots,
                "skipped_ambiguous_pois": [],
            }
        elif len(ordered_roots) <= 1 and str(
                source_tags.get("network", "")).strip().lower() in {
                    "rwn", "nwn", "iwn"}:
            core = {
                "decision": "declined",
                "reason": "not-local-route",
                "skipped_ambiguous_pois": [],
            }
        elif len(ordered_roots) <= 1 and source_kind != "route":
            core = {
                "decision": "declined",
                "reason": "not-route-candidate",
                "skipped_ambiguous_pois": [],
            }
        else:
            replay = _independent_destination_proposal(
                descriptor, ordered_roots, roots, authority)
            proposal = replay["proposal"]
            if proposal is None:
                core = {
                    "decision": "declined",
                    "reason": replay["reason"],
                    "skipped_ambiguous_pois": replay[
                        "skipped_ambiguous_pois"],
                }
            else:
                proposals[candidate_index] = proposal
                core = None
        states.append({
            "feature": feature,
            "properties": properties,
            "descriptor": descriptor,
            "identity": identity,
            "source_name": source_name,
            "source_name_key": source_root.get("name_key"),
            "source_kind": source_kind,
            "core": core,
        })

    ownership_groups: dict[str, list[tuple[int, dict]]] = {}
    for candidate_index, proposal in proposals.items():
        promoted_name = assembly_model._hike_name(proposal["poi"]["name"])
        ownership_groups.setdefault(
            _publisher_merge_key(promoted_name), []).append(
                (candidate_index, proposal))
    owners = set()
    for contenders in ownership_groups.values():
        owner_index, _proposal = min(contenders, key=lambda value: (
            value[1]["distance_ft"],
            value[1]["poi"]["node_id"],
            root_positions.get(
                value[1]["evidence"]["promotion_root_relation_id"],
                len(root_positions)),
            value[0],
        ))
        owners.add(owner_index)

    expected_decisions = []
    for state in states:
        properties = state["properties"]
        candidate_index = state["identity"]["candidate_index"]
        proposal = proposals.get(candidate_index)
        core = state["core"]
        if proposal is not None:
            if candidate_index in owners:
                evidence = proposal["evidence"]
                core = {
                    "decision": "promoted",
                    "poi_osm_node_id": evidence["osm_node_id"],
                    "promotion_root_relation_id": evidence[
                        "promotion_root_relation_id"],
                    "endpoint_source_way_id": evidence[
                        "endpoint_source_way_id"],
                    "endpoint_source_node_id": evidence[
                        "endpoint_source_node_id"],
                    "distance_ft": evidence["distance_ft"],
                    "selection_rank": evidence["selection_rank"],
                    "skipped_ambiguous_pois": proposal["evidence"].get(
                        "skipped_ambiguous_pois", []),
                }
                expected_evidence = dict(evidence)
                actual_values = properties.get("destination_evidence")
                evidence_matches = (
                    isinstance(actual_values, list)
                    and len(actual_values) == 1
                    and isinstance(actual_values[0], dict)
                )
                if evidence_matches:
                    actual_evidence = dict(actual_values[0])
                    if "selected_endpoint_index" not in actual_evidence:
                        expected_evidence.pop("selected_endpoint_index", None)
                    evidence_matches = actual_evidence == expected_evidence
                if (properties.get("kind") != "hike"
                        or properties.get("name") !=
                        assembly_model._hike_name(proposal["poi"]["name"])
                        or properties.get("destinations") != [
                            proposal["poi"]["name"]]
                        or not evidence_matches):
                    errors.append(
                        f"relation candidate[{candidate_index}] promotion output "
                        "contradicts independent decision")
            else:
                core = {
                    "decision": "declined",
                    "reason": "destination-already-claimed",
                    "skipped_ambiguous_pois": proposal["evidence"].get(
                        "skipped_ambiguous_pois", []),
                }
        if core is None:
            continue
        if core.get("decision") == "declined":
            declined_mismatch = False
            if properties.get("kind") != state["source_kind"]:
                declined_mismatch = True
                errors.append(
                    f"relation candidate[{candidate_index}] declined identity "
                    f"field 'kind' diverges: expected "
                    f"{state['source_kind']!r}, got "
                    f"{properties.get('kind')!r}")
            if properties.get("name") != state["source_name"]:
                declined_mismatch = True
                errors.append(
                    f"relation candidate[{candidate_index}] declined identity "
                    f"field 'name' diverges: expected "
                    f"{state['source_name']!r}, got "
                    f"{properties.get('name')!r}")
            actual_name_key = _publisher_merge_key(
                properties.get("name")
                if isinstance(properties.get("name"), str) else None)
            if actual_name_key != state["source_name_key"]:
                declined_mismatch = True
                errors.append(
                    f"relation candidate[{candidate_index}] declined identity "
                    f"field 'name_key' diverges: expected "
                    f"{state['source_name_key']!r}, got "
                    f"{actual_name_key!r}")
            if "destination_evidence" in properties:
                declined_mismatch = True
                errors.append(
                    f"relation candidate[{candidate_index}] declined identity "
                    "field 'destination_evidence' diverges: expected absence, "
                    f"got {properties.get('destination_evidence')!r}")
            if declined_mismatch:
                errors.append(
                    f"relation candidate[{candidate_index}] declined promotion "
                    "output contradicts independent decision")
        expected_decisions.append({**state["identity"], **core})

    actual_decisions = quality.get("promotion_decisions")
    if actual_decisions != expected_decisions:
        if not isinstance(actual_decisions, list):
            errors.append(
                "assembly promotion decision ledger diverges: expected list, "
                f"got {actual_decisions!r}")
        else:
            row_count = max(len(expected_decisions), len(actual_decisions))
            for position in range(row_count):
                expected_row = (expected_decisions[position]
                                if position < len(expected_decisions) else None)
                actual_row = (actual_decisions[position]
                              if position < len(actual_decisions) else None)
                if expected_row == actual_row:
                    continue
                candidate_index = (
                    expected_row.get("candidate_index")
                    if isinstance(expected_row, dict)
                    else actual_row.get("candidate_index")
                    if isinstance(actual_row, dict) else position
                )
                label = f"relation candidate[{candidate_index}]"
                if expected_row is None:
                    errors.append(
                        f"{label} promotion ledger row diverges: expected "
                        f"<missing>, got {actual_row!r}")
                    continue
                if actual_row is None:
                    errors.append(
                        f"{label} promotion ledger row diverges: expected "
                        f"{expected_row!r}, got <missing>")
                    continue
                if not isinstance(actual_row, dict):
                    errors.append(
                        f"{label} promotion ledger row diverges: expected "
                        f"{expected_row!r}, got {actual_row!r}")
                    continue
                for field in sorted(set(expected_row) | set(actual_row)):
                    expected_value = expected_row.get(field, "<missing>")
                    actual_value = actual_row.get(field, "<missing>")
                    if expected_value != actual_value:
                        errors.append(
                            f"{label} promotion ledger field {field!r} "
                            f"diverges: expected {expected_value!r}, got "
                            f"{actual_value!r}")
        errors.append(
            "assembly promotion decision ledger is incomplete or inconsistent")


def _validate_relation_member_audit(quality: dict, authority: dict,
                                    candidates: list[dict],
                                    removed_features: list[dict],
                                    raw_ways: dict[int, dict], boundary,
                                    errors: list[str]) -> dict[int, dict]:
    from shapely.geometry import shape

    audits = quality.get("relation_member_audit")
    if not isinstance(audits, list):
        errors.append("assembly relation-member audit is missing")
        authority["_candidate_claims"] = {}
        return {}
    if [audit.get("relation_id") for audit in audits
            if isinstance(audit, dict)] != authority["accepted_relation_ids"]:
        errors.append("assembly relation-member audit relation order is incomplete")
    unresolved = []
    outside_gap_relation_ids = set()
    emitted_roots = {}
    root_records = {}
    audit_by_root = {}
    for audit_index, audit in enumerate(audits):
        label = f"relation-member-audit[{audit_index}]"
        if not isinstance(audit, dict):
            errors.append(f"{label} is not an object")
            continue
        relation_id = audit.get("relation_id")
        if relation_id not in authority["accepted_relation_ids"]:
            errors.append(f"{label} lacks accepted graph authority")
            continue
        expected = _expected_relation_audit(
            relation_id, authority, boundary)
        expected_status = _derived_assembly_status(
            relation_id, expected, authority)
        fields_match = True
        for field, expected_value in expected.items():
            if audit.get(field) != expected_value:
                fields_match = False
                errors.append(f"{label} {field} contradicts relation graph")
        if audit.get("assembly_status") != expected_status:
            fields_match = False
            errors.append(
                f"{label} assembly status contradicts independently derived "
                f"{expected_status!r}")
        if _audit_has_root_specific_outside_gap(expected, expected_status):
            outside_gap_relation_ids.add(relation_id)
        reasons, record = _audit_resolution({
            **expected, "assembly_status": expected_status,
        })
        expected_review = "unresolved" if reasons else "accepted"
        if audit.get("review_status") != expected_review:
            fields_match = False
            errors.append(f"{label} review status is inconsistent")
        if audit.get("unresolved_reasons") != reasons:
            fields_match = False
            errors.append(f"{label} unresolved reasons are inconsistent")
        if record is not None:
            unresolved.append(record)
            errors.append(
                f"{label} contains unresolved member exclusions or evidence: "
                f"{reasons!r}")
        way_ids, geometry = _authoritative_exact_area_root(expected, boundary)
        preclip_way_ids, preclip_geometry = _authoritative_preclip_root(expected)
        display_name = _authoritative_root_display_name(
            relation_id, expected, authority)
        root_record = {
            "name": display_name,
            "name_key": _publisher_merge_key(display_name),
            "hierarchy": expected["relation_ids"],
            "direct": {
                int(key): list(value) for key, value in
                expected["direct_relation_way_ids"].items()
            },
            "eligible_main_way_ids": preclip_way_ids,
            "included_spur_way_ids": list(expected["included_spur_way_ids"]),
            "rendered_main_way_ids": way_ids,
            "preclip_geometry": preclip_geometry,
            "geometry": geometry,
            "expected_status": expected_status,
            "fields_match": fields_match,
        }
        root_records[relation_id] = root_record
        audit_by_root[relation_id] = audit
        if expected_status == "emitted":
            emitted_roots[relation_id] = root_record
            if not display_name:
                errors.append(
                    f"emitted relation root r{relation_id} has no authoritative "
                    "display name or named eligible main way")

    published_candidates = [
        candidate for candidate in candidates
        if (isinstance(candidate, dict)
            and isinstance(candidate.get("properties"), dict)
            and candidate["properties"].get("removed_category") not in
            _QUALITY_REMOVAL_CATEGORIES)
    ]
    output_descriptors = []
    for candidate_position, candidate in enumerate(published_candidates):
        properties = candidate.get("properties") if isinstance(candidate, dict) else None
        if not isinstance(properties, dict):
            continue
        try:
            candidate_geometry = shape(candidate.get("geometry"))
            if candidate_geometry.is_empty or candidate_geometry.length <= 0:
                candidate_geometry = None
        except Exception:  # noqa: BLE001
            candidate_geometry = None
        source_records = properties.get("source_ways")
        source_record_ids = {
            record.get("way_id") for record in source_records
            if isinstance(record, dict) and isinstance(record.get("way_id"), int)
        } if isinstance(source_records, list) else set()
        output_descriptors.append({
            "position": candidate_position,
            "properties": properties,
            "geometry": candidate_geometry,
            "geometry_document": candidate.get("geometry"),
            "way_ids": set(properties.get("member_ways") or [])
            | set(properties.get("source_geometry_way_ids") or [])
            | set(properties.get("rendered_source_way_ids") or [])
            | source_record_ids,
        })

    claims = {}
    identity_owners = {}
    descriptors = []
    for candidate_position, candidate in enumerate(published_candidates):
        properties = candidate.get("properties") if isinstance(candidate, dict) else None
        if not isinstance(properties, dict) or properties.get("source") != "relation":
            continue
        label = f"relation candidate[{candidate_position}]"
        candidate_index = properties.get("quality_candidate_index")
        name = properties.get("name")
        name_key = _publisher_merge_key(name if isinstance(name, str) else None)
        member_ways = properties.get("member_ways")
        geometry_way_ids = properties.get("source_geometry_way_ids")
        if (not isinstance(candidate_index, int)
                or isinstance(candidate_index, bool) or candidate_index < 0):
            errors.append(f"{label} candidate identity index is invalid")
            continue
        if (not _positive_int_list(member_ways)
                or len(member_ways) != len(set(member_ways))):
            errors.append(f"{label} member identity is invalid")
            member_ways = []
        expected_ckey = (
            "w" + "-".join(str(way_id) for way_id in sorted(member_ways))
            if member_ways else None)
        ckey = properties.get("ckey")
        if not name_key or ckey != expected_ckey:
            errors.append(f"{label} name/ckey binding is invalid")
        identity = (name_key, ckey)
        prior_owner = identity_owners.get(identity)
        if prior_owner is not None and prior_owner != candidate_index:
            errors.append(f"{label} name/ckey binding is ambiguous")
        identity_owners[identity] = candidate_index
        if (not _positive_int_list(geometry_way_ids)
                or len(geometry_way_ids) != len(set(geometry_way_ids))):
            errors.append(f"{label} source geometry identity is invalid")
            geometry_way_ids = []
        try:
            candidate_geometry = shape(candidate.get("geometry"))
            if candidate_geometry.is_empty or candidate_geometry.length <= 0:
                raise ValueError("empty candidate geometry")
        except Exception:  # noqa: BLE001 - final trail validation reports shape
            candidate_geometry = None
            errors.append(f"{label} geometry cannot bind relation authority")
        descriptors.append({
            "position": candidate_position,
            "index": candidate_index,
            "label": label,
            "feature": candidate,
            "properties": properties,
            "name": name,
            "name_key": name_key,
            "ckey": ckey,
            "member_ways": list(member_ways),
            "geometry_way_ids": list(geometry_way_ids),
            "geometry": candidate_geometry,
        })

    drop_descriptors = []
    for removed_position, feature in enumerate(removed_features):
        properties = feature.get("properties") if isinstance(feature, dict) else None
        if not isinstance(properties, dict) or properties.get("source") != "relation":
            continue
        category = properties.get("removed_category")
        if category not in (_CURATION_REMOVAL_CATEGORIES
                            | _CLIP_REMOVAL_CATEGORIES
                            | _ABSORPTION_REMOVAL_CATEGORIES):
            continue
        label = f"removed relation[{removed_position}]"
        geometry_way_ids = properties.get("source_geometry_way_ids")
        if not _positive_int_list(geometry_way_ids):
            geometry_way_ids = []
        try:
            removed_geometry = shape(feature.get("geometry"))
            if removed_geometry.is_empty or removed_geometry.length <= 0:
                raise ValueError("empty removal geometry")
        except Exception:  # noqa: BLE001
            removed_geometry = None
            errors.append(f"{label} geometry cannot bind dropped authority")
        name = properties.get("name")
        drop_descriptors.append({
            "position": removed_position,
            "label": label,
            "feature": feature,
            "properties": properties,
            "category": category,
            "name_key": _publisher_merge_key(
                name if isinstance(name, str) else None),
            "geometry_way_ids": set(geometry_way_ids),
            "geometry": removed_geometry,
        })

    matched_roots_by_candidate: dict[int, list[int]] = {}
    dropped_roots_by_position: dict[int, list[int]] = {}
    root_candidates: dict[int, int] = {}
    authority_root_order = authority.get("root_relation_ids", [])
    for root_id in authority_root_order:
        root = emitted_roots.get(root_id)
        if root is None:
            continue
        required_way_ids = set(root["eligible_main_way_ids"])
        match_way_ids = set(
            root["rendered_main_way_ids"] or root["eligible_main_way_ids"])
        matches = []
        if root["geometry"] is not None:
            for descriptor in descriptors:
                if (descriptor["geometry"] is None
                        or not match_way_ids.issubset(
                            descriptor["geometry_way_ids"])):
                    continue
                try:
                    covered = root["geometry"].difference(
                        descriptor["geometry"]).is_empty
                except Exception:  # noqa: BLE001 - malformed geometry fails closed
                    covered = False
                if covered:
                    matches.append(descriptor)
        drop_matches = [
            descriptor for descriptor in drop_descriptors
            if root_id in (descriptor["properties"].get(
                "root_relation_ids") or [])
            and _drop_descriptor_matches_root(descriptor, root, boundary)
        ]
        absorption_matches = [
            descriptor for descriptor in drop_matches
            if descriptor["category"] in _ABSORPTION_REMOVAL_CATEGORIES
        ]
        declared_matches = [
            descriptor for descriptor in matches
            if root_id in (descriptor["properties"].get(
                "root_relation_ids") or [])
        ]
        matched = None
        dropped = None
        if declared_matches:
            if len(declared_matches) != 1:
                errors.append(
                    f"emitted relation root r{root_id} matches multiple output "
                    "candidates")
                continue
            if drop_matches:
                errors.append(
                    f"emitted relation root r{root_id} has both output and drop "
                    "dispositions")
            matched = declared_matches[0]
        elif absorption_matches:
            if len(absorption_matches) != 1 or len(drop_matches) != 1:
                errors.append(
                    f"emitted relation root r{root_id} matches multiple producer "
                    "drop rows")
                continue
            dropped = absorption_matches[0]
        elif matches:
            if len(matches) != 1:
                errors.append(
                    f"emitted relation root r{root_id} matches multiple output "
                    "candidates")
                continue
            if drop_matches:
                errors.append(
                    f"emitted relation root r{root_id} has both output and drop "
                    "dispositions")
            matched = matches[0]
        elif not drop_matches:
            errors.append(
                f"emitted relation root r{root_id} has no output candidate or "
                "verified producer drop")
            continue
        elif len(drop_matches) != 1:
            errors.append(
                f"emitted relation root r{root_id} matches multiple producer "
                "drop rows")
            continue
        else:
            dropped = drop_matches[0]

        if matched is not None:
            if (matched["name_key"] != root["name_key"]
                    and not _valid_promoted_hike_name(
                        matched["properties"], root_id, root["name"],
                        authority)):
                errors.append(
                    f"{matched['label']} name contradicts authority-derived "
                    f"root r{root_id}")
            candidate_index = matched["index"]
            root_candidates[root_id] = candidate_index
            matched_roots_by_candidate.setdefault(candidate_index, []).append(root_id)
            continue

        dropped_roots_by_position.setdefault(
            dropped["position"], []).append(root_id)
        allowed_survivor_positions = set()
        if dropped["category"] in _ABSORPTION_REMOVAL_CATEGORIES:
            survivor_claim = (dropped["properties"].get("drop_evidence")
                              or {}).get("survivor")
            if isinstance(survivor_claim, dict):
                allowed_survivor_positions = {
                    output["position"] for output in output_descriptors
                    if survivor_claim == {
                        "ckey": output["properties"].get("ckey"),
                        "name": output["properties"].get("name"),
                        "source": output["properties"].get("source"),
                        "geometry_sha256": _publisher_json_sha256(
                            output.get("geometry_document") or {}),
                    }
                }
        for output in output_descriptors:
            if output["position"] in allowed_survivor_positions:
                continue
            way_overlap = required_way_ids.intersection(output["way_ids"])
            geometry_overlap = False
            if root["geometry"] is not None and output["geometry"] is not None:
                try:
                    geometry_overlap = root["geometry"].intersection(
                        output["geometry"]).length > 1e-12
                except Exception:  # noqa: BLE001
                    geometry_overlap = True
            if way_overlap or geometry_overlap:
                errors.append(
                    f"dropped relation root r{root_id} unexpectedly ships in "
                    f"output candidate[{output['position']}]")

    for descriptor in drop_descriptors:
        derived_roots = dropped_roots_by_position.get(descriptor["position"], [])
        if derived_roots:
            _validate_relation_drop_descriptor(
                descriptor, derived_roots, root_records, authority, raw_ways,
                boundary, output_descriptors, errors)
            continue
        declared_roots = descriptor["properties"].get("root_relation_ids") or []
        if any(root_id in emitted_roots for root_id in declared_roots):
            errors.append(
                f"{descriptor['label']} has no unambiguous emitted-root authority")

    for descriptor in descriptors:
        properties = descriptor["properties"]
        label = descriptor["label"]
        candidate_index = descriptor["index"]
        derived_roots = matched_roots_by_candidate.get(candidate_index, [])
        if not derived_roots:
            errors.append(f"{label} has no unambiguous emitted-root authority")
        seen_relations = set()
        expected_relation_ids = []
        expected_direct = {}
        required_way_ids = []
        for root_id in derived_roots:
            root = emitted_roots[root_id]
            for way_id in root["rendered_main_way_ids"]:
                if way_id not in required_way_ids:
                    required_way_ids.append(way_id)
            for relation_id in _graph_hierarchy(
                    root_id, authority.get("relations", {}), seen_relations):
                expected_relation_ids.append(relation_id)
                expected_direct[relation_id] = list(
                    authority["relations"][relation_id]["direct_way_ids"])
        declared_direct = _normalize_direct_members(
            properties.get("direct_relation_way_ids"), label, errors)
        if properties.get("root_relation_ids") != derived_roots:
            errors.append(
                f"{label} root relation ids contradict contributing graph roots")
        if properties.get("relation_ids") != expected_relation_ids:
            errors.append(
                f"{label} relation ids contradict authority-derived root order")
        if (declared_direct != expected_direct
                or not isinstance(declared_direct, dict)
                or list(declared_direct) != list(expected_direct)):
            errors.append(
                f"{label} direct relation membership contradicts independent graph")

        source_records = properties.get("source_ways")
        source_record_ids = [
            record.get("way_id") for record in source_records
            if isinstance(record, dict)
        ] if isinstance(source_records, list) else []
        required_fields = {
            "member ways": descriptor["member_ways"],
            "source geometry way ids": descriptor["geometry_way_ids"],
            "source-way records": source_record_ids,
            "rendered source way ids": properties.get(
                "rendered_source_way_ids") or [],
        }
        for field, values in required_fields.items():
            missing = [way_id for way_id in required_way_ids
                       if way_id not in values]
            if missing:
                errors.append(
                    f"{label} {field} omit authoritative exact-area ways: "
                    f"{missing!r}")

        way_direct = {
            way_id: [
                relation_id for relation_id in expected_relation_ids
                if way_id in expected_direct.get(relation_id, [])
            ]
            for way_id in descriptor["geometry_way_ids"]
        }
        if isinstance(source_records, list):
            for record in source_records:
                if not isinstance(record, dict):
                    continue
                way_id = record.get("way_id")
                if (isinstance(way_id, int)
                        and record.get("direct_relation_ids") !=
                        way_direct.get(way_id, [])):
                    errors.append(
                        f"{label} source-way direct membership is inconsistent")
        if properties.get("kind") == "hike":
            _validate_promoted_destination_evidence(
                descriptor, derived_roots, emitted_roots, authority, errors)
        elif "destination_evidence" in properties:
            errors.append(
                f"{label} destination evidence exists on a non-promoted candidate")
        claims[candidate_index] = {
            "name": descriptor["name"],
            "name_key": descriptor["name_key"],
            "ckey": descriptor["ckey"],
            "source_geometry_way_ids": descriptor["geometry_way_ids"],
            "required_authority_way_ids": required_way_ids,
            "root_relation_ids": derived_roots,
            "relation_ids": expected_relation_ids,
            "direct_relation_way_ids": expected_direct,
            "way_direct_relation_ids": way_direct,
        }

    for relation_id, root in root_records.items():
        if root["expected_status"] != "removed-thru-hike" \
                or root["geometry"] is None:
            continue
        for descriptor in descriptors:
            if relation_id in (descriptor["properties"].get(
                    "root_relation_ids") or []):
                errors.append(
                    f"removed thru-hike root r{relation_id} contributes an "
                    "output candidate")

    for relation_id, audit in audit_by_root.items():
        emitted = relation_id in root_candidates
        if emitted and audit.get("assembly_status") != "emitted":
            errors.append(
                f"relation audit r{relation_id} hides an emitted candidate root")
        if audit.get("emitted_in_exact_area") is not emitted:
            errors.append(
                f"relation audit r{relation_id} exact-area emission flag is inconsistent")
    for descriptor in descriptors:
        properties = descriptor["properties"]
        reasons = (properties.get("connectivity") or {}).get(
            "accepted_reasons") or []
        claims_outside_gap = "out-of-area-member-gap" in reasons
        claim = claims.get(descriptor["index"], {})
        derived_roots = claim.get("root_relation_ids") or []
        expected_outside_gap = bool(
            derived_roots
            and all(relation_id in outside_gap_relation_ids
                    for relation_id in derived_roots))
        if claims_outside_gap != expected_outside_gap:
            errors.append(
                f"{descriptor['label']} out-of-area gap evidence contradicts "
                "member audit")
    if quality.get("relation_member_unresolved") != unresolved:
        errors.append("assembly relation-member unresolved ledger is inconsistent")
    if quality.get("relation_member_unresolved_count") != len(unresolved):
        errors.append("assembly relation-member unresolved count is inconsistent")
    derived_roots_by_candidate = {
        candidate_index: list(root_ids)
        for candidate_index, root_ids in matched_roots_by_candidate.items()
    }
    for descriptor in drop_descriptors:
        candidate_index = descriptor["properties"].get(
            "quality_candidate_index")
        derived_roots = dropped_roots_by_position.get(
            descriptor["position"], [])
        if (isinstance(candidate_index, int)
                and not isinstance(candidate_index, bool)
                and derived_roots):
            derived_roots_by_candidate[candidate_index] = list(derived_roots)
    _validate_exact_denmark_promotion_ledger(
        quality, candidates, root_records, authority,
        derived_roots_by_candidate, errors)
    authority["_candidate_claims"] = claims
    return claims


def _candidate_source_evidence(feature: dict, boundary_feature: dict | None,
                               raw_ways: dict[int, dict],
                               relation_authority: dict,
                               label: str, errors: list[str]) -> dict[str, Any]:
    """Independently rebuild source topology, clipping, and walking identity."""
    from shapely.geometry import LineString, shape
    from shapely.ops import unary_union

    properties = feature.get("properties") or {}
    source_kind = properties.get("source")
    member_ways = properties.get("member_ways")
    root_relation_ids = properties.get("root_relation_ids", [])
    relation_ids = properties.get("relation_ids", [])
    root_identity_valid = source_kind != "relation"
    if source_kind == "relation":
        root_identity_valid = (
            _positive_int_list(root_relation_ids)
            and len(root_relation_ids) == len(set(root_relation_ids)))
        if not root_identity_valid:
            errors.append(f"{label} root relation ids are invalid")
    elif root_relation_ids not in ([], None):
        errors.append(f"{label} non-relation trail has root relation ids")
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
        authority_way = relation_authority.get("ways", {}).get(way_id)
        if (source_kind == "relation" and authority_way is not None
                and authority_way.get("status") == "present"):
            raw = {
                "coordinates": [tuple(point) for point in
                                authority_way.get("coordinates") or []],
                "tags": member_safety.decisive_tags(
                    authority_way.get("tags") or {}),
            }
        else:
            raw = raw_ways.get(way_id)
        raw_bound = False
        authority_identity_matches = True
        if (source_kind == "relation" and authority_way is not None
                and authority_way.get("status") == "present"):
            if (node_ids != authority_way.get("node_ids")
                    or coordinates != authority_way.get("coordinates")
                    or missing_node_ids != authority_way.get("missing_node_ids")):
                authority_identity_matches = False
                errors.append(
                    f"{record_label} contradicts raw authority node order w{way_id}")
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
            raw_bound = (authority_identity_matches and not missing_node_ids
                         and geometry_matches and tags_match)
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
    source_available_ids = set(raw_ways)
    if source_kind == "relation":
        source_available_ids.update(
            way_id for way_id, way in relation_authority.get("ways", {}).items()
            if way.get("status") == "present")
    raw_absent = set(geometry_way_ids) - source_available_ids
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
    connectivity = properties.get("connectivity")
    if not isinstance(connectivity, dict):
        errors.append(f"{label} lacks source connectivity evidence")
        connectivity = {}
    claimed_reasons = connectivity.get("accepted_reasons")
    outside_gap = (
        source_kind == "relation"
        and isinstance(claimed_reasons, list)
        and "out-of-area-member-gap" in claimed_reasons
    )
    if outside_gap and source_components <= 1:
        errors.append(f"{label} claims an unnecessary out-of-area member gap")
    source_complete_for_area = source_components == 1 or outside_gap
    boundary_split = (
        source_complete_for_area
        and postclip_components > 1
        and exact_clip
    )
    accepted = (
        root_identity_valid
        and not missing_way_ids
        and source_complete_for_area
        and postclip_components > 0
        and exact_clip
        and (postclip_components == 1 or boundary_split)
    )
    reasons = []
    if source_components == 1:
        reasons.append("shared-osm-node")
    if outside_gap:
        reasons.append("out-of-area-member-gap")
    if boundary_split:
        reasons.append("boundary-induced-split")
    if properties.get("boundary_induced_split") is not boundary_split:
        errors.append(f"{label} boundary-induced-split fact is inconsistent")

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

    source = source_kind
    relation_ids = properties.get("relation_ids")
    direct = _normalize_direct_members(
        properties.get("direct_relation_way_ids"), label, errors)
    confirmed_direct: dict[int, list[int]] = {}
    claim = None
    if source == "relation":
        candidate_index = properties.get("quality_candidate_index")
        claim = relation_authority.get("_candidate_claims", {}).get(
            candidate_index)
        if claim is None:
            root_identity_valid = False
            accepted = False
            errors.append(f"{label} lacks authority-derived candidate identity")
        else:
            expected_relation_ids = claim["relation_ids"]
            expected_roots = claim["root_relation_ids"]
            expected_direct = claim["direct_relation_way_ids"]
            relation_matches = relation_ids == expected_relation_ids
            roots_match = root_relation_ids == expected_roots
            direct_matches = (
                direct == expected_direct
                and isinstance(direct, dict)
                and list(direct) == list(expected_direct)
            )
            identity_matches = (
                properties.get("name") == claim["name"]
                and properties.get("ckey") == claim["ckey"]
                and geometry_way_ids == claim["source_geometry_way_ids"]
            )
            if not relation_matches:
                errors.append(
                    f"{label} relation ids contradict authority-derived root order")
            if not roots_match:
                errors.append(
                    f"{label} root relation ids contradict contributing graph roots")
            if not direct_matches:
                errors.append(
                    f"{label} direct relation membership contradicts independent graph")
            if not identity_matches:
                errors.append(f"{label} name/ckey authority binding is inconsistent")
            root_identity_valid = all((
                relation_matches, roots_match, direct_matches,
                identity_matches, bool(expected_roots),
            ))
            if root_identity_valid:
                confirmed_direct = expected_direct
            else:
                accepted = False
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
        expected_way_direct = (
            claim.get("way_direct_relation_ids", {}) if claim else {})
        for record in records:
            expected_memberships = expected_way_direct.get(record["way_id"], [])
            if record.get("direct_relation_ids") != expected_memberships:
                errors.append(f"{label} source-way direct membership is inconsistent")
            if (record.get("direct_relation_ids")
                    and not _relation_member_should_render(
                        record.get("tags") or {})):
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
        if category in _ABSORPTION_REMOVAL_CATEGORIES:
            # Relation-root identity, full preclip source records, terminal
            # survivor binding, and exact-area coverage are validated together
            # after graph-derived root claims are available.
            continue
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
    if quality.get("schema_version") != 7:
        errors.append("assembly Denmark quality schema_version must be 7")
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

    terminal_unresolved = quality.get("terminal_absorption_unresolved")
    terminal_unresolved_count = quality.get(
        "terminal_absorption_unresolved_count")
    if not isinstance(terminal_unresolved, list):
        errors.append("assembly terminal absorption unresolved ledger is invalid")
        terminal_unresolved = []
    if terminal_unresolved_count != len(terminal_unresolved):
        errors.append("assembly terminal absorption unresolved count is inconsistent")
    allowed_terminal_reasons = {
        "cycle", "divergent-targets", "missing-target", "clipped-target",
        "model-curation-removed-target", "quality-removed-target",
        "coverage-failure", "ambiguous-survivor",
    }
    candidate_record_keys = {
        "candidate_id", "population", "ckey", "name", "source",
        "root_relation_ids", "geometry_sha256", "quality_disposition",
        "removed_category", "removed_reason", "removal_stage",
        "successor_candidate_ids",
    }
    for record_index, record in enumerate(terminal_unresolved):
        label = f"terminal-absorption-unresolved[{record_index}]"
        if (not isinstance(record, dict) or set(record) != {
                "reason", "source_candidate", "target_candidates"}
                or record.get("reason") not in allowed_terminal_reasons
                or not isinstance(record.get("source_candidate"), dict)
                or set(record["source_candidate"]) != candidate_record_keys
                or not isinstance(record.get("target_candidates"), list)
                or not record["target_candidates"]
                or any(not isinstance(target, dict)
                       or set(target) != candidate_record_keys
                       for target in record["target_candidates"])):
            errors.append(f"{label} schema is invalid")
            continue
        source_record = record["source_candidate"]
        digest = source_record.get("geometry_sha256")
        if (source_record.get("population") != "published"
                or source_record.get("source") != "relation"
                or not _positive_int_list(
                    source_record.get("root_relation_ids"))
                or not (isinstance(
                    source_record.get("successor_candidate_ids"), list)
                    and all(isinstance(value, int)
                            and not isinstance(value, bool) and value >= 0
                            for value in source_record[
                                "successor_candidate_ids"]))
                or source_record.get("removal_stage") is not None
                or not re.fullmatch(r"[0-9a-f]{64}", str(digest or ""))):
            errors.append(f"{label} source identity is invalid")
        for target in record["target_candidates"]:
            target_digest = target.get("geometry_sha256")
            if (target.get("population") not in {
                    "published", "quality-blocked", "removed", "missing"}
                    or not (isinstance(
                        target.get("successor_candidate_ids"), list)
                        and all(isinstance(value, int)
                                and not isinstance(value, bool) and value >= 0
                                for value in target[
                                    "successor_candidate_ids"]))
                    or target.get("removal_stage") not in {
                        None, "model-curation", "exact-area-clip",
                        "denmark-quality"}
                    or (target["population"] == "missing"
                        and target_digest is not None)
                    or (target["population"] != "missing"
                        and not re.fullmatch(
                            r"[0-9a-f]{64}", str(target_digest or "")))):
                errors.append(f"{label} target identity is invalid")
        if record.get("reason") == "model-curation-removed-target" \
                and not any(
                    target.get("population") == "removed"
                    and target.get("removal_stage") == "model-curation"
                    and isinstance(target.get("removed_category"), str)
                    and bool(target.get("removed_category"))
                    and isinstance(target.get("removed_reason"), str)
                    and bool(target.get("removed_reason"))
                    for target in record["target_candidates"]):
            errors.append(f"{label} model-curation target disposition is invalid")
    if terminal_unresolved:
        errors.append(
            "assembly terminal relation absorption unresolved population is nonzero")

    boundary = None
    if boundary_feature is not None:
        try:
            from shapely.geometry import shape
            boundary = shape(boundary_feature["geometry"])
        except Exception:  # noqa: BLE001
            boundary = None
    _validate_relation_member_audit(
        quality, relation_authority, candidates, removed_features,
        raw_ways, boundary, errors)

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

    if quality.get("restored_road_policy") != \
            "diagnostic-only-member-safety-gated":
        errors.append("assembly restored-road diagnostic policy is invalid")
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
    aoi_scope = relation_authority.get("scopes", {}).get("aoi", {})
    graph_source = aoi_scope.get("source")
    expected_input = ({"sha256": graph_source.get("sha256"),
                       "bytes": graph_source.get("bytes")}
                      if isinstance(graph_source, dict) else None)
    if report.get("input_pbf") != expected_input:
        errors.append(
            "assembly input PBF identity disagrees with AOI relation scope")
    expected_authority = {
        "mode": "raw-primary-prefilter-witness-aoi-render-scope",
        "root_relation_ids": relation_authority.get("root_relation_ids"),
        "scopes": {
            scope: evidence.get("source")
            for scope, evidence in relation_authority.get("scopes", {}).items()
        },
    }
    if report.get("relation_authority") != expected_authority:
        errors.append("assembly three-scope relation authority record is invalid")
    if report.get("clip_applied") is not True:
        errors.append("assembly exact-area clip was not applied")
    if report.get("min_length_mi") != PILOT_MIN_LENGTH_MI:
        errors.append("assembly minimum trail length is not the pinned pilot value")
    if report.get("min_inside_mi") != PILOT_MIN_INSIDE_MI:
        errors.append("assembly inside-area floor is not the pinned pilot value")
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


def _app_trail_slug(name: str) -> str:
    return "-".join(
        part for part in re.split(r"[^a-z0-9]+", name.casefold()) if part
    )[:60]


def _app_canonical(trail_id: str) -> str:
    return re.sub(r"-\d{1,3}$", "", trail_id)


def _app_difficulty(miles: float, sac: Any, visibility: Any) -> str:
    sac_value = str(sac or "").strip()
    if sac_value and sac_value != "hiking":
        return "Hard"
    if miles > 4:
        return "Hard"
    if miles > 2 or str(visibility or "") == "intermediate":
        return "Moderate"
    return "Easy"


def _publisher_merge_key(name: str | None) -> str:
    normalized = unicodedata.normalize("NFC", name or "").casefold().strip()
    key = " ".join(normalized.replace("-", " ").split())
    if key.endswith(" trail"):
        key = key[:-6].rstrip()
    return key


def _sealed_exact_boundary(exact_document: Any, area_features: list,
                           assembly_report: Any, expected_relation_id: int,
                           errors: list[str]):
    """Verify one sealed exact relation geometry against assembly output."""
    from shapely.geometry import shape

    features = (exact_document.get("features")
                if isinstance(exact_document, dict) else None)
    if (not isinstance(exact_document, dict)
            or exact_document.get("type") != "FeatureCollection"
            or not isinstance(features, list) or len(features) != 1
            or not isinstance(features[0], dict)):
        errors.append("exact-area evidence must contain exactly one feature")
        return None, None
    feature = features[0]
    properties = feature.get("properties")
    if not isinstance(properties, dict):
        errors.append("exact-area evidence properties are invalid")
        return None, None
    expected_identity = {
        "name": AREA_NAME,
        "osm_type": "relation",
        "osm_id": expected_relation_id,
    }
    if any(properties.get(key) != value
           for key, value in expected_identity.items()):
        errors.append("exact-area evidence identity is invalid")
    try:
        boundary = shape(feature.get("geometry") or {})
    except Exception:  # noqa: BLE001
        boundary = None
    if (boundary is None or boundary.is_empty or not boundary.is_valid
            or boundary.geom_type not in {"Polygon", "MultiPolygon"}):
        errors.append("exact-area evidence geometry is invalid")
        return None, None
    geometry_sha256 = _publisher_json_sha256(boundary.__geo_interface__)
    if properties.get("geometry_sha256") != geometry_sha256:
        errors.append("exact-area evidence geometry hash is invalid")

    matching = []
    for area_feature in area_features:
        if not isinstance(area_feature, dict):
            continue
        area_properties = area_feature.get("properties") or {}
        if any(area_properties.get(key) != value
               for key, value in expected_identity.items()):
            continue
        try:
            area_geometry = shape(area_feature.get("geometry") or {})
        except Exception:  # noqa: BLE001
            continue
        if (_publisher_json_sha256(area_geometry.__geo_interface__)
                == geometry_sha256 and area_geometry.equals(boundary)):
            matching.append(area_feature)
    if len(matching) != 1:
        errors.append(
            "sealed exact area does not equal one assembly target relation")

    report_boundary = (assembly_report.get("boundary")
                       if isinstance(assembly_report, dict) else None)
    if (report_boundary != expected_identity
            or not isinstance(assembly_report, dict)
            or assembly_report.get("boundary_geometry_sha256") !=
            geometry_sha256):
        errors.append("assembly report does not bind the sealed exact geometry")
    evidence = {"enabled": True, **expected_identity,
                "geometry_sha256": geometry_sha256}
    return boundary, evidence


def _publisher_inside_miles(geometry, boundary) -> float:
    try:
        intersection = geometry.intersection(boundary)
    except Exception:  # noqa: BLE001
        return 0.0
    return _linework_miles([
        [[float(x), float(y)] for x, y in part.coords]
        for part in _shapely_line_parts(intersection)
    ])


def _publisher_clamped_feature(feature: dict, boundary) -> dict | None:
    """Independently replay publisher dangling-end clipping and remeasurement."""
    from shapely.geometry import LineString, MultiLineString, Point, shape
    from shapely.ops import substring

    try:
        geometry = shape(feature.get("geometry") or {})
    except Exception:  # noqa: BLE001
        return None
    components = (list(geometry.geoms)
                  if isinstance(geometry, MultiLineString) else [geometry])
    lines = []
    for component in components:
        if not isinstance(component, LineString) or component.length == 0:
            continue
        try:
            inside = component.intersection(boundary)
        except Exception:  # noqa: BLE001
            continue
        parts = _shapely_line_parts(inside)
        if not parts:
            continue
        distances = [
            component.project(Point(coordinate))
            for part in parts for coordinate in part.coords
        ]
        segment = substring(component, min(distances), max(distances))
        segments = (list(segment.geoms)
                    if isinstance(segment, MultiLineString) else [segment])
        for line in segments:
            if isinstance(line, LineString) and line.length > 0:
                lines.append([[float(x), float(y)] for x, y in line.coords])
    if not lines:
        return None
    miles = round(_linework_miles(lines), 3)
    properties = dict(feature.get("properties") or {})
    full = properties.get("length_mi")
    properties["length_mi"] = miles
    if _number(full) and float(full) - miles > 0.01:
        properties["full_length_mi"] = full
        properties["clipped"] = True
    return {
        **feature,
        "properties": properties,
        "geometry": {"type": "MultiLineString", "coordinates": lines},
    }


def _publisher_postclip_features(trail_features: list, boundary,
                                 errors: list[str]) -> list[dict]:
    """Replay one-area publisher selection against the sealed exact boundary."""
    from shapely.geometry import shape

    if boundary is None:
        return []
    clipped = []
    have = set()
    for feature in trail_features:
        if not isinstance(feature, dict):
            continue
        try:
            geometry = shape(feature.get("geometry") or {})
        except Exception:  # noqa: BLE001
            continue
        properties = feature.get("properties") or {}
        inside_miles = _publisher_inside_miles(geometry, boundary)
        if properties.get("kind") == "route":
            total = properties.get("length_mi") \
                or _publisher_inside_miles(geometry, geometry.envelope) or 0.0
            if not total or inside_miles / float(total) < 0.5:
                continue
            candidate = _publisher_clamped_feature(feature, boundary)
        else:
            candidate = _publisher_clamped_feature(feature, boundary)
            if candidate is None:
                continue
            clamped_miles = (candidate.get("properties") or {}).get(
                "length_mi") or 0.0
            if not (inside_miles >= 0.25
                    or (clamped_miles
                        and inside_miles >= 0.5 * float(clamped_miles))):
                continue
        if candidate is None:
            continue
        key = _publisher_merge_key(
            (candidate.get("properties") or {}).get("name") or "")
        if key and key in have:
            continue
        have.add(key)
        clipped.append(candidate)
    return clipped


def _publisher_json_sha256(value: Any) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _expected_postclip_evidence(trail_features: list, boundary,
                                postclip: list[dict]) -> dict:
    return {
        "assembly_sha256": _publisher_json_sha256(trail_features),
        "boundary_geometry_sha256": _publisher_json_sha256(
            boundary.__geo_interface__),
        "feature_count": len(postclip),
        "features": [{
            "key": (feature.get("properties") or {}).get("ckey"),
            "name": (feature.get("properties") or {}).get("name"),
            "geometry_sha256": _publisher_json_sha256(
                feature.get("geometry") or {}),
            "length_mi": (feature.get("properties") or {}).get("length_mi"),
        } for feature in postclip],
    }


def _expected_app_trail_miles(trail: dict) -> float:
    total = 0.0
    for segment in trail.get("segments") or []:
        for index in range(1, len(segment)):
            left, right = segment[index - 1], segment[index]
            if len(left) < 2 or len(right) < 2:
                continue
            total += _haversine_miles(
                (float(left[1]), float(left[0])),
                (float(right[1]), float(right[0])))
    return total


def _expected_degenerate_verdicts(trails: list[dict]) -> list[str]:
    owners: dict[tuple[float, float], set[int]] = {}
    for trail_index, trail in enumerate(trails):
        for segment in trail.get("segments") or []:
            for point in segment:
                if len(point) >= 2:
                    node = (round(point[0], 4), round(point[1], 4))
                    owners.setdefault(node, set()).add(trail_index)
    verdicts = []
    for trail_index, trail in enumerate(trails):
        if _expected_app_trail_miles(trail) >= 0.01:
            verdicts.append("")
            continue
        segments = [segment for segment in trail.get("segments") or []
                    if len(segment) >= 2]
        if not segments:
            verdicts.append("noseg")
            continue
        endpoints = (
            (round(segments[0][0][0], 4), round(segments[0][0][1], 4)),
            (round(segments[-1][-1][0], 4), round(segments[-1][-1][1], 4)),
        )
        shared = sum(
            1 for endpoint in endpoints
            if any(owner != trail_index for owner in owners.get(endpoint, set())))
        verdicts.append("" if shared >= 2 else
                        ("spur" if shared == 1 else "isolated"))
    return verdicts


def _finalize_expected_app_row(row: dict) -> tuple[dict, dict]:
    """Independently replay the sealed-empty Mols post-convert stages."""
    trails = list(row.get("trails") or [])
    verdicts = _expected_degenerate_verdicts(trails)
    kept = [trail for trail, verdict in zip(trails, verdicts) if not verdict]
    removed = ([{"id": trail.get("id"), "reason": verdict}
                for trail, verdict in zip(trails, verdicts) if verdict]
               if kept else [])
    if removed:
        row["trails"] = kept
        row["trail_count"] = len(kept)
        row["total_mi"] = round(sum(
            float(trail.get("distanceMi", 0)) for trail in kept), 1)
    evidence = {
        "nonhiking_sidecar_ids": [],
        "nonhiking_removed_ids": [],
        "nonhiking_would_empty": False,
        "degenerate_removed": removed,
        "prior_parking_present": False,
        "trail_count": row.get("trail_count"),
        "total_mi": row.get("total_mi"),
    }
    return row, evidence


def _expected_app_row(trail_features: list, boundary,
                      selected: dict, expected_relation_id: int,
                      errors: list[str], *,
                      postclip_features: list[dict] | None = None,
                      finalization_out: dict | None = None) -> dict:
    if postclip_features is None:
        postclip_features = _publisher_postclip_features(
            trail_features, boundary, errors)
    rows = []
    for feature_index, feature in enumerate(postclip_features):
        properties = feature.get("properties") or {}
        name = properties.get("name")
        lines = _geometry_lines_for_validation(feature)
        if ((properties.get("kind") or "trail") not in {
                "trail", "hike", "route"}):
            continue
        if not isinstance(name, str) or not name or not lines:
            continue
        miles = (float(properties.get("length_mi"))
                 if _number(properties.get("length_mi")) else 0.0)
        rows.append((feature_index, feature, name, lines, miles))
    rows.sort(key=lambda record: -record[4])
    slug_counts: dict[str, int] = {}
    used_canonical = set()
    app_trails = []
    min_lat = min_lon = float("inf")
    max_lat = max_lon = float("-inf")
    for _, feature, name, lines, miles in rows:
        properties = feature.get("properties") or {}
        base = _app_trail_slug(name)
        if not base:
            errors.append(f"cannot derive app trail id for {name!r}")
            continue
        seen = slug_counts.get(base, 0)
        slug_counts[base] = seen + 1
        trail_id = base if seen == 0 else f"{base}-{seen}"
        while _app_canonical(trail_id) in used_canonical:
            trail_id += "-x"
        used_canonical.add(_app_canonical(trail_id))
        segments = []
        for line in lines:
            points = []
            for point in line:
                parsed = _point(point)
                if parsed is None:
                    points = []
                    break
                lon, lat = parsed
                rounded = [round(lat, 6), round(lon, 6)]
                points.append(rounded)
                min_lat, max_lat = min(min_lat, rounded[0]), max(
                    max_lat, rounded[0])
                min_lon, max_lon = min(min_lon, rounded[1]), max(
                    max_lon, rounded[1])
            if len(points) >= 2:
                segments.append(points)
        if not segments:
            continue
        app_trails.append({
            "id": trail_id,
            "name": name,
            "distanceMi": round(miles, 2),
            "difficulty": _app_difficulty(
                miles, properties.get("sac_scale"),
                properties.get("trail_visibility")),
            "segments": segments,
        })
    index_row = selected.get("index_row") if isinstance(selected, dict) else None
    if not isinstance(index_row, list) or len(index_row) != 5:
        errors.append("cannot derive app metadata from selected discovery")
        index_row = [AREA_ID, AREA_NAME, STATE, 0.0, 0.0]
    bbox = ([round(min_lon, 6), round(min_lat, 6),
             round(max_lon, 6), round(max_lat, 6)] if app_trails else None)
    row = {
        "id": AREA_ID,
        "name": AREA_NAME,
        "state": STATE,
        "center_lat": index_row[3],
        "center_lon": index_row[4],
        "zoom": 13,
        "bbox": bbox,
        "trails": app_trails,
        "trail_count": len(app_trails),
        "total_mi": round(sum(
            trail["distanceMi"] for trail in app_trails), 1),
        "osm_relation_id": expected_relation_id,
    }
    row, finalization = _finalize_expected_app_row(row)
    if finalization_out is not None:
        finalization_out.update(finalization)
    return row


def _validate_preview(preview: Any, publish_report: Any,
                      trail_features: list, boundary,
                      exact_boundary_evidence: dict | None,
                      selected: dict, expected: int,
                      errors: list[str]) -> None:
    if not isinstance(preview, dict):
        errors.append("app preview must be an object")
        return
    if (preview.get("schema_version") != 1
            or preview.get("state") != STATE
            or preview.get("write_mode") != "dry-run-preview"
            or preview.get("canonical_write") is not False):
        errors.append("app preview safety metadata is invalid")
    expected_sealed_inputs = [{
        "area_id": AREA_ID,
        "nonhiking_sidecar_entry": {},
        "prior_output_exists": False,
        "prior_parking": None,
    }]
    if preview.get("sealed_inputs") != expected_sealed_inputs:
        errors.append("Mols publisher inputs are not sealed-empty and fresh")
    postclip_features = _publisher_postclip_features(
        trail_features, boundary, errors)
    finalization = {}
    expected_row = _expected_app_row(
        trail_features, boundary, selected, expected, errors,
        postclip_features=postclip_features,
        finalization_out=finalization)
    areas = preview.get("areas")
    if not isinstance(areas, list) or len(areas) != 1:
        errors.append("app preview must contain exactly one area row")
        return
    if areas[0] != expected_row:
        errors.append("app preview row contradicts independently converted trails")
    if "parking" in areas[0]:
        errors.append("Mols preview unexpectedly carries prior parking")
    if not isinstance(publish_report, dict):
        return
    if publish_report.get("exact_boundary") != exact_boundary_evidence:
        errors.append("publisher did not consume the sealed exact boundary")
    app_trails = expected_row["trails"]
    distance_sum = round(sum(
        float(trail["distanceMi"]) for trail in app_trails), 6)
    assembly_miles = round(sum(
        float((feature.get("properties") or {}).get("length_mi"))
        for feature in trail_features
        if _number((feature.get("properties") or {}).get("length_mi"))), 6)
    expected_reconciliation = {
        "enabled": True,
        "area_count": 1,
        "area_ids": [AREA_ID],
        "assembly_feature_count": len(trail_features),
        "assembly_property_miles": assembly_miles,
        "preview_distance_miles": distance_sum,
        "publisher_total_miles": expected_row["total_mi"],
        "postclip": (_expected_postclip_evidence(
            trail_features, boundary, postclip_features)
            if boundary is not None else None),
        "finalization": finalization,
        "trails": [{
            "id": row["id"], "name": row["name"],
            "distance_mi": row["distanceMi"],
            "assembly_length_mi": next((
                (feature.get("properties") or {}).get("length_mi")
                for feature in trail_features
                if (feature.get("properties") or {}).get("name") ==
                row["name"]
            ), None),
        } for row in app_trails],
    }
    if publish_report.get("preview") != expected_reconciliation:
        errors.append("publisher assembly-to-preview reconciliation is invalid")
    area_record = {
        "area_id": AREA_ID,
        "name": AREA_NAME,
        "osm_relation_id": expected,
        "trail_count": expected_row["trail_count"],
        "total_miles": expected_row["total_mi"],
    }
    if publish_report.get("validated_areas") != [area_record] \
            or publish_report.get("published_areas") != [area_record]:
        errors.append("publisher summary contradicts recomputed app row")


def _contains_geometry(value: Any) -> bool:
    if isinstance(value, dict):
        if any(key in value for key in ("geometry", "coordinates", "segments")):
            return True
        return any(_contains_geometry(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_geometry(item) for item in value)
    return False


def _golden_authority_lines(authority: dict, root_id: int,
                            errors: list[str]) -> list[list]:
    relations = authority.get("relations", {})
    seen_relations = set()
    seen_ways = set()
    lines = []

    def visit(relation_id: int) -> bool:
        if relation_id in seen_relations:
            return True
        seen_relations.add(relation_id)
        relation = relations.get(relation_id)
        if relation is None:
            return False
        direct = relation.get("direct_ways", {})
        for member in relation.get("members", []):
            role = str(member.get("role") or "").strip().casefold()
            if role not in {"", "main"}:
                continue
            if member.get("type") == "relation":
                if not visit(member.get("ref")):
                    return False
            elif member.get("type") == "way" \
                    and member.get("ref") not in seen_ways:
                way_id = member.get("ref")
                seen_ways.add(way_id)
                way = direct.get(way_id)
                coordinates = (way or {}).get("coordinates")
                if ((way or {}).get("status") != "present"
                        or not _visual_valid_line(coordinates)):
                    return False
                lines.append(coordinates)
        return True

    if not visit(root_id):
        errors.append("Bjergetapen raw authority geometry is incomplete")
        return []
    return lines


def _validate_golden(golden: Any, manifest: Any, authority: dict,
                     trails: list, errors: list[str]) -> None:
    if not isinstance(golden, dict) or golden.get("schema_version") != 1:
        errors.append("Mols golden record is invalid")
        return
    trail = golden.get("trail") or {}
    official = golden.get("official_fact") or {}
    measurement = golden.get("osm_measurement") or {}
    policy = golden.get("geometry_policy") or {}
    disposition = golden.get("identity_disposition") or {}
    if (golden.get("record_id") != "mols-bjerge-bjergetapen-8350111"
            or golden.get("purpose") !=
               "validation-only-identity-and-approximate-length"
            or trail.get("name") != "Mols Bjerge-stien Bjergetapen"
            or trail.get("osm_relation_id") != 8350111
            or official.get("publisher") != "VisitDenmark / VisitAarhus"
            or official.get("approximate_complete_stage_km") != 20
            or official.get("precision") != "approximate"
            or official.get("official_geometry_available") is not False
            or official.get("retrieved_on") != "2026-10-02"
            or not isinstance(official.get("retrieval_note"), str)
            or not official["retrieval_note"].strip()
            or not str(official.get("source_url") or "").startswith(
                "https://www.visitdenmark.com/")
            or policy.get("exclusive_source") != "OpenStreetMap"
            or policy.get("official_geometry_used") is not False
            or policy.get("competitor_data_used") is not False
            or policy.get("connector_geometry_invented") is not False
            or golden.get("acceptance_tolerance") is not None
            or not disposition.get("kiro")
            or disposition.get("human") != "not-yet-recorded"
            or golden.get("attribution") != ATTRIBUTION):
        errors.append("Mols golden identity/source policy contract is invalid")
    if _contains_geometry(golden):
        errors.append("validation-only official golden record contains geometry")

    relation = authority.get("relations", {}).get(8350111)
    if (relation is None
            or (relation.get("tags") or {}).get("name") !=
               "Mols Bjerge-stien Bjergetapen"):
        errors.append("raw authority lacks exact Bjergetapen relation 8350111")
        return
    matches = [
        feature for feature in trails
        if isinstance(feature, dict)
        and (feature.get("properties") or {}).get("name") ==
            "Mols Bjerge-stien Bjergetapen"
        and (feature.get("properties") or {}).get("source") == "relation"
        and 8350111 in ((feature.get("properties") or {}).get(
            "root_relation_ids") or [])
    ]
    if len(matches) != 1:
        errors.append(
            "assembled output lacks one directly relation-backed Bjergetapen")
        return
    preclip_lines = _golden_authority_lines(authority, 8350111, errors)
    exact_lines = _geometry_lines_for_validation(matches[0])
    if not preclip_lines or not exact_lines:
        errors.append("Bjergetapen current OSM measurement geometry is empty")
        return
    preclip_miles = round(_linework_miles(preclip_lines), 6)
    exact_miles = round(_linework_miles(exact_lines), 6)
    preclip_km = round(preclip_miles * 1.609344, 3)
    exact_km = round(exact_miles * 1.609344, 3)
    expected_measurement = {
        "source_sha": (manifest or {}).get("source_sha"),
        "raw_authority_sha256": (authority.get("source") or {}).get("sha256"),
        "preclip_miles": preclip_miles,
        "preclip_km": preclip_km,
        "preclip_delta_from_approximate_km": round(preclip_km - 20, 3),
        "exact_area_miles": exact_miles,
        "exact_area_km": exact_km,
        "exact_area_delta_from_approximate_km": round(exact_km - 20, 3),
        "disposition": "reported-for-human-review-no-numeric-pass-tolerance",
    }
    if measurement != expected_measurement:
        errors.append("Mols golden current OSM measurement binding is invalid")


def _visual_lines(geometry: dict):
    coordinates = geometry.get("coordinates")
    kind = geometry.get("type")
    if kind == "LineString" and isinstance(coordinates, list):
        yield coordinates
    elif kind in {"MultiLineString", "Polygon"} \
            and isinstance(coordinates, list):
        yield from coordinates
    elif kind == "MultiPolygon" and isinstance(coordinates, list):
        for polygon in coordinates:
            yield from polygon


def _visual_valid_line(line: Any) -> bool:
    return (isinstance(line, list) and len(line) >= 2
            and all(_point(point) is not None for point in line))


def _visual_projector(boundary: dict):
    lines = list(_visual_lines(boundary.get("geometry") or {}))
    points = [point for line in lines if _visual_valid_line(line)
              for point in line]
    if not points:
        raise ValueError("exact boundary has no visual extent")
    min_lon = min(float(point[0]) for point in points)
    min_lat = min(float(point[1]) for point in points)
    max_lon = max(float(point[0]) for point in points)
    max_lat = max(float(point[1]) for point in points)
    lon_scale = max(0.01, math.cos(math.radians((min_lat + max_lat) / 2)))
    span_x = max((max_lon - min_lon) * lon_scale, 1e-9)
    span_y = max(max_lat - min_lat, 1e-9)
    scale = min((1400 - 96) / span_x, (1000 - 96) / span_y)

    def project(point):
        return (
            48 + (float(point[0]) - min_lon) * lon_scale * scale,
            1000 - 48 - (float(point[1]) - min_lat) * scale,
        )

    return project


def _visual_path_data(line: list, project, *, close: bool) -> str:
    points = [project(point) for point in line]
    value = "M" + " L".join(f"{x:.3f},{y:.3f}" for x, y in points)
    return value + (" Z" if close else "")


def _expected_feature_visuals(document: dict, project,
                              class_name: str) -> list[dict[str, str]]:
    output = []
    for feature_index, feature in enumerate(document.get("features") or []):
        properties = feature.get("properties") or {}
        geometry = feature.get("geometry") or {}
        close = geometry.get("type") in {"Polygon", "MultiPolygon"}
        for part_index, line in enumerate(_visual_lines(geometry)):
            if not _visual_valid_line(line):
                continue
            attributes = {
                "class": class_name,
                "data-feature-index": str(feature_index),
                "data-part-index": str(part_index),
            }
            if feature.get("id"):
                attributes["data-osm-id"] = str(feature["id"])
            elif properties.get("osm_id"):
                prefix = ("r" if properties.get("osm_type") == "relation"
                          else "w")
                attributes["data-osm-id"] = f"{prefix}{properties['osm_id']}"
            if properties.get("ckey"):
                attributes["data-key"] = str(properties["ckey"])
            if properties.get("relation_ids"):
                attributes["data-relations"] = ",".join(
                    str(value) for value in properties["relation_ids"])
            if properties.get("member_ways"):
                attributes["data-ways"] = ",".join(
                    str(value) for value in properties["member_ways"])
            if properties.get("name"):
                attributes["data-name"] = str(properties["name"])
            attributes["d"] = _visual_path_data(line, project, close=close)
            output.append(attributes)
    return output


def _expected_visual_inventory(raw: dict, areas: dict, trails: dict,
                               removed: dict, authority: dict,
                               assembly: dict) -> tuple[dict[str, list[dict]], dict]:
    boundaries = [
        feature for feature in areas.get("features") or []
        if (feature.get("properties") or {}).get("name") == AREA_NAME
    ]
    if len(boundaries) != 1:
        raise ValueError("visual exact boundary is not unique")
    boundary = boundaries[0]
    project = _visual_projector(boundary)
    inventory = {
        "raw-osm-context": _expected_feature_visuals(
            raw, project, "raw-context"),
        "removed-output": _expected_feature_visuals(
            removed, project, "removed"),
        "emitted-output": _expected_feature_visuals(
            trails, project, "emitted"),
        "exact-boundary": _expected_feature_visuals(
            {"features": [boundary]}, project, "exact-boundary"),
    }
    authority_ways = {}
    for relation in authority.get("relations", {}).values():
        for way_id, way in relation.get("direct_ways", {}).items():
            if way.get("status") != "present":
                continue
            prior = authority_ways.get(way_id)
            if prior is not None and prior != way:
                raise ValueError(f"visual authority contradicts w{way_id}")
            authority_ways[way_id] = way
    safe_ids, unsafe_ids = set(), set()
    for audit in (assembly.get("quality") or {}).get(
            "relation_member_audit") or []:
        for member in audit.get("direct_way_members") or []:
            if member.get("status") == "included":
                safe_ids.add(member.get("way_id"))
            elif member.get("status") == "excluded":
                unsafe_ids.add(member.get("way_id"))
    for group, class_name, way_ids in (
            ("safe-relation-members", "safe-member", safe_ids),
            ("unsafe-relation-members", "unsafe-member", unsafe_ids)):
        records = []
        for way_id in sorted(way_ids):
            way = authority_ways.get(way_id)
            coordinates = (way or {}).get("coordinates")
            if not _visual_valid_line(coordinates):
                continue
            records.append({
                "class": class_name,
                "data-way": str(way_id),
                "d": _visual_path_data(coordinates, project, close=False),
            })
        inventory[group] = records
    markers = []
    for feature_index, feature in enumerate(trails.get("features") or []):
        if (feature.get("properties") or {}).get(
                "quality_disposition") not in {
                    "unresolved-relation-gap", "missing-source-way"}:
            continue
        marker_index = 0
        for line in _visual_lines(feature.get("geometry") or {}):
            if not _visual_valid_line(line):
                continue
            for endpoint_index, point in enumerate((line[0], line[-1])):
                x, y = project(point)
                markers.append({
                    "class": "unresolved-endpoint",
                    "data-feature-index": str(feature_index),
                    "data-marker-index": str(marker_index),
                    "data-endpoint": str(endpoint_index),
                    "cx": f"{x:.3f}", "cy": f"{y:.3f}", "r": "3.5",
                })
                marker_index += 1
    inventory["unresolved-endpoints"] = markers
    counts = {
        "raw_context_paths": len(inventory["raw-osm-context"]),
        "exact_boundary_paths": len(inventory["exact-boundary"]),
        "emitted_paths": len(inventory["emitted-output"]),
        "removed_paths": len(inventory["removed-output"]),
        "safe_relation_member_paths": len(
            inventory["safe-relation-members"]),
        "unsafe_relation_member_paths": len(
            inventory["unsafe-relation-members"]),
        "unresolved_endpoint_markers": len(
            inventory["unresolved-endpoints"]),
    }
    return inventory, counts


def _validate_visual(root: Path, review: Any, text: str, *,
                     raw_document: dict, areas: dict, trails: dict,
                     removed: dict, authority: dict,
                     authority_document: dict, assembly: dict,
                     errors: list[str]) -> None:
    visual_path = root / VISUAL_RELATIVE
    try:
        raw = visual_path.read_bytes()
    except OSError as error:
        errors.append(f"cannot read offline visual: {error}")
        return
    try:
        canonical_raw, canonical_review = canonical_visual.build_svg(
            raw=raw_document, areas=areas, trails=trails, removed=removed,
            authority=authority_document, assembly=assembly)
    except (KeyError, TypeError, ValueError) as error:
        errors.append(f"cannot render canonical offline visual: {error}")
        canonical_raw = None
        canonical_review = None
    if canonical_raw is not None and raw != canonical_raw:
        errors.append("offline visual bytes contradict canonical sealed-source render")
    if canonical_review is not None and review != canonical_review:
        errors.append("visual review record contradicts canonical sealed-source render")
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        errors.append("offline visual contains forbidden XML declarations")
        return
    content_without_namespace = text.replace(
        'xmlns="http://www.w3.org/2000/svg"', "")
    if ("http://" in content_without_namespace
            or "https://" in content_without_namespace
            or "@import" in content_without_namespace.casefold()
            or "url(" in content_without_namespace.casefold()):
        errors.append("offline visual contains external resource references")
    try:
        xml_root = ET.fromstring(raw)
    except ET.ParseError as error:
        errors.append(f"offline visual is invalid XML: {error}")
        return
    local = lambda tag: tag.rsplit("}", 1)[-1]
    if local(xml_root.tag) != "svg":
        errors.append("offline visual root is not SVG")
    forbidden_tags = {
        "script", "image", "foreignObject", "use", "iframe", "object",
        "embed", "audio", "video",
    }
    for element in xml_root.iter():
        tag = local(element.tag)
        if tag in forbidden_tags:
            errors.append(f"offline visual contains forbidden {tag} content")
        for attribute, value in element.attrib.items():
            attr = local(attribute).casefold()
            if attr == "href" or attr.startswith("on") or "url(" in value.casefold():
                errors.append("offline visual contains active/external content")
    try:
        inventory, expected_layers = _expected_visual_inventory(
            raw_document, areas, trails, removed, authority, assembly)
    except (TypeError, ValueError) as error:
        errors.append(f"cannot rebuild offline visual inventory: {error}")
        return
    groups = {}
    all_group_ids = []
    for element in xml_root.iter():
        if local(element.tag) != "g" or "id" not in element.attrib:
            continue
        group_id = element.attrib["id"]
        all_group_ids.append(group_id)
        if group_id in groups:
            errors.append(f"offline visual duplicates group id {group_id}")
        groups[group_id] = element
    if set(all_group_ids) != set(inventory):
        errors.append("offline visual group inventory is not exact")
    accounted_graphics = set()
    for group_id, expected_records in inventory.items():
        element = groups.get(group_id)
        if element is None:
            continue
        actual_records = []
        for child in list(element):
            child_tag = local(child.tag)
            expected_tag = ("circle" if group_id == "unresolved-endpoints"
                            else "path")
            if child_tag != expected_tag:
                errors.append(
                    f"offline visual group {group_id} has unexpected child")
                continue
            accounted_graphics.add(id(child))
            actual_records.append(dict(child.attrib))
        if actual_records != expected_records:
            errors.append(
                f"offline visual group {group_id} contradicts sealed sources")
    if any(local(element.tag) in {"path", "circle"}
           and id(element) not in accounted_graphics
           for element in xml_root.iter()):
        errors.append("offline visual contains unexpected unbound paths or markers")
    if not isinstance(review, dict):
        errors.append("visual review record is invalid")
        return
    layers = review.get("layers")
    if (review.get("schema_version") != 1
            or review.get("artifact") != VISUAL_RELATIVE
            or review.get("sha256") != hashlib.sha256(raw).hexdigest()
            or review.get("bytes") != len(raw)
            or review.get("geometry_source") != "OpenStreetMap"
            or review.get("external_network_resources") is not False
            or review.get("raster_or_external_tiles") is not False
            or review.get("kiro_disposition") !=
               "generated-and-structurally-validated"
            or review.get("human_disposition") != "not-yet-recorded"
            or review.get("attribution") != ATTRIBUTION
            or not isinstance(layers, dict)):
        errors.append("visual review record does not bind the offline SVG")
        return
    if layers != expected_layers:
        errors.append("offline visual layer counts contradict sealed sources")


def _signed_pedestrian_claims(assembly: Any, authority: dict,
                              boundary, errors: list[str]) -> set[int]:
    """Derive emitted signed-way claims from graph-bound member audits."""
    quality = assembly.get("quality") if isinstance(assembly, dict) else None
    audits = quality.get("relation_member_audit") if isinstance(quality, dict) else None
    if not isinstance(audits, list):
        errors.append("cannot derive signed pedestrian claims without audits")
        return set()
    claimed = set()
    for index, audit in enumerate(audits):
        if not isinstance(audit, dict):
            continue
        root_id = audit.get("relation_id")
        if root_id not in authority.get("accepted_relation_ids", []):
            continue
        expected = _expected_relation_audit(
            root_id, authority, boundary)
        if _derived_assembly_status(root_id, expected, authority) != "emitted":
            continue
        for member in expected["direct_way_members"]:
            if (member.get("status") == "included"
                    and member.get("effective_role") in {
                        "", "main", "approach", "connection"}):
                claimed.add(member["way_id"])
    return claimed


def _validate_maltgaarden(trails: list, removed: list,
                          raw_ways: dict[int, dict],
                          relation_claimed_way_ids: set[int],
                          errors: list[str]) -> None:
    """Enforce the Mols standalone class from canonical source topology."""
    class_ways = {
        way_id: way for way_id, way in raw_ways.items()
        if member_safety.standalone_pedestrian_area_candidate(
            way.get("tags") or {},
            closed=(len(way.get("node_ids") or []) >= 4
                    and way["node_ids"][0] == way["node_ids"][-1]),
            relation_claimed=way_id in relation_claimed_way_ids)
    }
    removal_by_way: dict[int, dict] = {}
    for index, feature in enumerate(removed):
        if not isinstance(feature, dict) or not isinstance(
                feature.get("properties"), dict):
            errors.append(f"removed[{index}] is not a structured feature")
            continue
        properties = feature["properties"]
        category = properties.get("removed_category")
        reason = properties.get("removed_reason")
        members = properties.get("member_ways")
        if (not isinstance(category, str) or not category
                or not isinstance(reason, str) or not reason.strip()
                or not _positive_int_list(members)
                or len(members) != len(set(members))):
            errors.append(f"removed[{index}] category row is structurally invalid")
            continue
        if category != "standalone-pedestrian-area":
            continue
        if (properties.get("source") != "name-stitch"
                or "exterior source-ring" not in reason):
            errors.append(
                f"removed[{index}] standalone pedestrian-area disposition is invalid")
        lines = _geometry_lines_for_validation(feature)
        if (len(members) != 1 or len(lines) != 1 or len(lines[0]) < 4
                or _point(lines[0][0]) != _point(lines[0][-1])):
            errors.append(
                f"removed[{index}] pedestrian-area exterior ring is invalid")
            continue
        way_id = members[0]
        if way_id in removal_by_way:
            errors.append(f"duplicate pedestrian-area removal for w{way_id}")
        removal_by_way[way_id] = feature
        raw = raw_ways.get(way_id)
        if way_id not in class_ways:
            errors.append(
                f"removed[{index}] is not a closed standalone pedestrian area")
        elif [_point(point) for point in lines[0]] not in [
                raw["coordinates"], list(reversed(raw["coordinates"]))]:
            errors.append(
                f"removed[{index}] exterior ring contradicts raw OSM w{way_id}")

    missing = sorted(set(class_ways) - set(removal_by_way))
    if missing:
        errors.append(
            f"closed standalone pedestrian-area exclusions are missing: {missing!r}")
    for index, feature in enumerate(trails):
        if not isinstance(feature, dict):
            continue
        properties = feature.get("properties") or {}
        if (properties.get("source") == "name-stitch"
                and set(properties.get("member_ways") or []).intersection(
                    class_ways)):
            errors.append(
                f"trail[{index}] synthesizes a standalone pedestrian-area centerline")


def _validate_artifact_seals(root: Path, manifest: Any,
                             relatives: list[str], errors: list[str]) -> None:
    if not isinstance(manifest, dict):
        return
    seals = manifest.get("artifacts")
    if not isinstance(seals, dict):
        errors.append("manifest artifact seals are missing")
        return
    if set(seals) != set(relatives):
        errors.append("manifest artifact seal inventory is incomplete")
    for relative in relatives:
        seal = seals.get(relative)
        path = root / relative
        if not isinstance(seal, dict):
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if (seal.get("bytes") != len(raw)
                or seal.get("sha256") != hashlib.sha256(raw).hexdigest()
                or not isinstance(seal.get("producer_stage"), str)
                or not seal["producer_stage"]
                or not isinstance(seal.get("source_scope"), str)
                or not seal["source_scope"]
                or not isinstance(seal.get("schema_version"), (int, str))):
            errors.append(f"manifest artifact seal is invalid: {relative}")
    expected_scopes = {
        RAW_RELATION_MEMBERS_RELATIVE: "raw-denmark",
        PREFILTER_RELATION_MEMBERS_RELATIVE: "prefiltered-denmark",
        AOI_RELATION_MEMBERS_RELATIVE: "aoi-render-scope",
        RAW_PBF_RELATIVE: "raw-denmark",
        PREFILTER_PBF_RELATIVE: "prefiltered-denmark",
        AOI_PBF_RELATIVE: "aoi-render-scope",
        RAW_WAY_TOPOLOGY_RELATIVE: "aoi-source-topology",
        EXACT_AREA_RELATIVE: "exact-area-geometry",
        RAW_SCOPE_RECEIPT_RELATIVE: "raw-scope-provenance",
        PREFILTER_SCOPE_RECEIPT_RELATIVE: "prefiltered-scope-provenance",
        AOI_SCOPE_RECEIPT_RELATIVE: "aoi-scope-provenance",
        PREFILTER_TRANSFORM_RECEIPT_RELATIVE:
            "prefilter-transformation-provenance",
        PREVIEW_RELATIVE: "runner-local-app-preview",
        GOLDEN_RELATIVE: "validation-only-no-geometry",
        VISUAL_RELATIVE: "offline-osm-visual",
        VISUAL_REVIEW_RELATIVE: "offline-osm-visual-review",
    }
    for relative, scope in expected_scopes.items():
        if isinstance(seals.get(relative), dict) \
                and seals[relative].get("source_scope") != scope:
            errors.append(f"manifest source scope is invalid: {relative}")


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
    scope_trust = _load_scope_trust(
        Path(args.scope_trust_file), root, errors)
    trusted_semantic_relatives = _validate_semantic_ledger_trust(
        root, scope_trust, errors)
    semantic_relatives = {
        *RELATION_LEDGER_RELATIVES_BY_SCOPE.values(),
        RAW_WAY_TOPOLOGY_RELATIVE,
    }
    if trusted_semantic_relatives != semantic_relatives:
        result = {
            "schema_version": 1,
            "stage": "final",
            "status": "failed",
            "area_id": AREA_ID,
            "expected_osm_relation_id": expected,
            "checked_files": [],
            "assembled_trail_count": 0,
            "errors": errors,
        }
        _write_json(Path(args.result_json), result)
        return 1
    expected_paths = {
        "raw": (root / RAW_RELATION_MEMBERS_RELATIVE).resolve(),
        "prefilter": (root / PREFILTER_RELATION_MEMBERS_RELATIVE).resolve(),
        "aoi": (root / AOI_RELATION_MEMBERS_RELATIVE).resolve(),
    }
    supplied_paths = {
        "raw": Path(args.raw_relation_members).resolve(),
        "prefilter": Path(args.prefilter_relation_members).resolve(),
        "aoi": Path(args.aoi_relation_members).resolve(),
    }
    for scope, expected_path in expected_paths.items():
        if supplied_paths[scope] != expected_path:
            errors.append(
                f"{scope} relation-members path must identify the QA package file")
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

    sealed_relatives = sorted([
        *[relative for label, relative in REQUIRED_JSON_FILES.items()
          if label != "manifest"],
        *REQUIRED_TEXT_FILES.values(),
        *PBF_RELATIVES_BY_SCOPE.values(),
        *([OPTIONAL_DROPPED_ROUTES] if optional_path.exists() else []),
    ])
    _validate_artifact_seals(
        root, documents.get("manifest"), sealed_relatives, errors)

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
    rendered_raw_ways = _raw_way_evidence(raw_features, errors)
    raw_destination_points = _raw_destination_point_evidence(
        raw_features, errors)
    raw_ways, aoi_topology_relations, destination_pois = \
        _way_topology_evidence(
            documents.get("raw_way_topology"), root, rendered_raw_ways, errors)
    _validate_destination_ledger_completeness(
        raw_destination_points, destination_pois, errors)
    relation_authority = _validate_relation_scopes(
        documents.get("raw_relation_members"),
        documents.get("prefilter_relation_members"),
        documents.get("aoi_relation_members"), raw_ways, errors)
    relation_authority["destination_pois"] = destination_pois
    scope_evidence = _validate_scope_pbf_sources(
        root, relation_authority, {
            "raw-denmark": documents.get("raw_scope_receipt"),
            "prefiltered-denmark": documents.get("prefilter_scope_receipt"),
            "aoi": documents.get("aoi_scope_receipt"),
        }, documents.get("prefilter_transform_receipt"),
        documents.get("manifest"), scope_trust, errors)
    trail_features = _feature_collection(documents.get("trails"), "trails", errors)
    removed_features = _feature_collection(documents.get("removed"), "removed", errors)
    ingest_dropped_features = _feature_collection(
        documents.get("ingest_dropped"), "ingest-dropped", errors)
    area_features = _feature_collection(documents.get("areas"), "areas", errors)
    exact_area_features = _feature_collection(
        documents.get("exact_area"), "exact-area", errors)
    boundary_feature = (exact_area_features[0]
                        if len(exact_area_features) == 1 else None)
    boundary = None
    exact_boundary_evidence = None
    if expected is not None:
        boundary, exact_boundary_evidence = _sealed_exact_boundary(
            documents.get("exact_area"), area_features,
            documents.get("assembly_report"), expected, errors)
    if boundary_feature is None:
        errors.append("exact-area GeoJSON must contain exactly one Mols boundary")
    if expected is not None:
        _validate_assembly(
            documents.get("assembly_report"), trail_features, expected,
            removed_features, boundary_feature, raw_ways,
            relation_authority, errors)
    _validate_aoi_content_scope(
        raw_ways, aoi_topology_relations, destination_pois,
        relation_authority, scope_evidence,
        boundary, trail_features, removed_features, errors)
    _validate_trails(
        trail_features, boundary_feature, raw_ways, relation_authority, errors)
    _validate_ingest_dropped(ingest_dropped_features, trail_features, errors)
    if not isinstance(documents.get("curation"), dict):
        errors.append("curation snapshot must be an object")
    if not isinstance(documents.get("curation_diff"), dict):
        errors.append("curation diff must be an object")

    if expected is not None:
        claimed_pedestrian_ways = _signed_pedestrian_claims(
            documents.get("assembly_report"), relation_authority,
            boundary, errors)
        _validate_maltgaarden(
            trail_features, removed_features, raw_ways,
            claimed_pedestrian_ways, errors)
        _validate_publication(documents.get("publish_report"), expected, errors)
        _validate_preview(
            documents.get("preview"), documents.get("publish_report"),
            trail_features, boundary, exact_boundary_evidence,
            documents.get("selected_discovery") or {}, expected, errors)
    _validate_golden(
        documents.get("golden"), documents.get("manifest"),
        relation_authority, trail_features, errors)
    _validate_visual(
        root, documents.get("visual_review"), texts.get("visual", ""),
        raw_document=documents.get("raw") or {},
        areas=documents.get("areas") or {},
        trails=documents.get("trails") or {},
        removed=documents.get("removed") or {},
        authority=relation_authority,
        authority_document=documents.get("raw_relation_members") or {},
        assembly=documents.get("assembly_report") or {},
        errors=errors)

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
            *PBF_RELATIVES_BY_SCOPE.values(),
            *([OPTIONAL_DROPPED_ROUTES] if optional_path.exists() else []),
        ]),
        "assembled_trail_count": len(trail_features),
        "unresolved_terminal_absorption_count": (
            ((documents.get("assembly_report") or {}).get("quality") or {}).get(
                "terminal_absorption_unresolved_count", 0)),
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
    final.add_argument("--scope-trust-file", required=True)
    final.add_argument("--raw-relation-members", required=True)
    final.add_argument("--prefilter-relation-members", required=True)
    final.add_argument("--aoi-relation-members", required=True)
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
