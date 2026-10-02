#!/usr/bin/env python3
"""Fail-closed validation for the runner-local Mols Bjerge QA pilot."""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

AREA_ID = "nationalpark-mols-bjerge-dk"
AREA_NAME = "Nationalpark Mols Bjerge"
STATE = "Denmark"
ATTRIBUTION = "© OpenStreetMap contributors"
VISUAL_REVIEW_URL = "http://localhost:8000/viewer/?aoi=mols-bjerge"
_UNNAMED = re.compile(r"Unnamed \d+")

REQUIRED_JSON_FILES = {
    "manifest": "manifest.json",
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


def _point(value: Any) -> tuple[float, float] | None:
    if (not isinstance(value, list) or len(value) < 2
            or not _number(value[0]) or not _number(value[1])):
        return None
    return float(value[0]), float(value[1])


def _connected_lines(lines: Any) -> bool:
    if not isinstance(lines, list) or not lines:
        return False
    endpoints = []
    for line in lines:
        if not isinstance(line, list) or len(line) < 2:
            return False
        start, end = _point(line[0]), _point(line[-1])
        if start is None or end is None:
            return False
        endpoints.append({start, end})
    reached = {0}
    while True:
        joined = set().union(*(endpoints[index] for index in reached))
        additions = {
            index for index, ends in enumerate(endpoints)
            if index not in reached and joined.intersection(ends)
        }
        if not additions:
            break
        reached.update(additions)
    return len(reached) == len(endpoints)


def _validate_trails(features: list, errors: list[str]) -> None:
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
        if not isinstance(properties, dict) or not properties.get("area"):
            errors.append(f"trail[{index}] has no per-area assignment")
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
        if not _connected_lines(coordinates):
            errors.append(f"trail[{index}] has disconnected or invalid linework")


def _validate_assembly(report: Any, trail_count: int, expected: int,
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
    if report.get("assembled_trail_count") != trail_count:
        errors.append("assembly trail count disagrees with trails GeoJSON")
    if report.get("post_clip_trail_count") != trail_count:
        errors.append("assembly post-clip count disagrees with trails GeoJSON")
    before = report.get("pre_clip_trail_count")
    if not isinstance(before, int) or isinstance(before, bool) or before < trail_count:
        errors.append("assembly pre-clip count is invalid")


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
    trail_features = _feature_collection(documents.get("trails"), "trails", errors)
    _validate_trails(trail_features, errors)
    _feature_collection(documents.get("removed"), "removed", errors)
    _feature_collection(documents.get("ingest_dropped"), "ingest-dropped", errors)
    area_features = _feature_collection(documents.get("areas"), "areas", errors)
    exact_areas = [
        feature for feature in area_features
        if isinstance(feature, dict)
        and isinstance(feature.get("properties"), dict)
        and feature["properties"].get("name") == AREA_NAME
    ]
    if len(exact_areas) != 1:
        errors.append("areas GeoJSON must contain exactly one exact Mols boundary")
    if not isinstance(documents.get("curation"), dict):
        errors.append("curation snapshot must be an object")
    if not isinstance(documents.get("curation_diff"), dict):
        errors.append("curation diff must be an object")

    if expected is not None:
        _validate_assembly(
            documents.get("assembly_report"), len(trail_features), expected, errors)
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
