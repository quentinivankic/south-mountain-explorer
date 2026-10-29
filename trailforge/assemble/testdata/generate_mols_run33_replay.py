#!/usr/bin/env python3
"""Build the deterministic, test-only Mols run-33 curation replay fixture."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
from typing import Any

AREA_NAME = "Nationalpark Mols Bjerge"
AREA_RELATION_ID = 7046785
ATTRIBUTION = "© OpenStreetMap contributors"
FIXTURE_NAME = "mols-run33-curation-replay.json"
EXPECTATIONS_NAME = "mols-run33-curation-replay.manifest.json"

_REQUIRED_SOURCE_FILES = {
    "README.md": "text",
    "manifest.json": "json",
    "reports/assembly.json": "json",
    "data/aoi/mols-bjerge.raw.geojson": "json",
    "data/aoi/mols-bjerge.trails.geojson": "json",
    "data/aoi/mols-bjerge.areas.geojson": "json",
}
_DECISIVE_TAGS = (
    "name", "highway", "foot", "footway", "hiking", "trail", "route",
    "sac_scale", "trail_visibility", "designation", "network", "access",
    "indoor", "motor_vehicle", "motorcar", "atv", "ohv", "4wd_only",
    "snowmobile", "motorcycle", "bicycle", "tracktype", "surface", "lanes",
    "service", "piste:type", "mtb:type", "mtb:scale:imba", "oneway",
)
_LIMITATIONS = [
    "Original OSM node IDs were absent; exact coordinate identity is used only "
    "for this archived replay.",
    "Route relation records and roles were absent; deterministic synthetic "
    "relation IDs are used.",
    "The artifact predates signed hiking relation road-member restoration.",
]
_SHA_RE = re.compile(r"[0-9a-f]{40}")
_WAY_ID_RE = re.compile(r"w([1-9][0-9]*)")


class FixtureError(ValueError):
    """The supplied artifact or expectations fail the replay contract."""


def canonical_json_bytes(value: Any) -> bytes:
    """Encode stable JSON independent of source object-key insertion order."""
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode("utf-8")


def canonical_json_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _load_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise FixtureError(f"missing {label}: {path}") from error
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise FixtureError(f"invalid {label}: {path}: {error}") from error


def _mapping(value: Any, label: str) -> dict:
    if not isinstance(value, dict):
        raise FixtureError(f"{label} must be an object")
    return value


def _feature_collection(value: Any, label: str) -> list[dict]:
    value = _mapping(value, label)
    features = value.get("features")
    if value.get("type") != "FeatureCollection" or not isinstance(features, list):
        raise FixtureError(f"{label} must be a GeoJSON FeatureCollection")
    if any(not isinstance(feature, dict) for feature in features):
        raise FixtureError(f"{label} contains a non-object feature")
    return features


def _coordinate(value: Any, label: str) -> list[float]:
    if (not isinstance(value, list) or len(value) < 2
            or isinstance(value[0], bool) or isinstance(value[1], bool)
            or not isinstance(value[0], (int, float))
            or not isinstance(value[1], (int, float))
            or not math.isfinite(float(value[0]))
            or not math.isfinite(float(value[1]))):
        raise FixtureError(f"{label} is not a finite coordinate")
    return [float(value[0]), float(value[1])]


def _load_expectations(path: Path) -> dict:
    expectations = _mapping(_load_json(path, "fixture manifest"),
                            "fixture manifest")
    if expectations.get("schema_version") != 1:
        raise FixtureError("fixture manifest schema_version must be 1")
    source_files = expectations.get("source_files")
    if not isinstance(source_files, dict) \
            or set(source_files) != set(_REQUIRED_SOURCE_FILES):
        raise FixtureError("fixture manifest source file identities are incomplete")
    return expectations


def _validate_identity(artifact_dir: Path, expectations: dict,
                       run_id: int, source_sha: str) -> dict[str, Any]:
    if not artifact_dir.is_dir():
        raise FixtureError(f"artifact directory does not exist: {artifact_dir}")
    if run_id != expectations.get("github_run_id"):
        raise FixtureError("GitHub run ID does not match the fixture manifest")
    if not _SHA_RE.fullmatch(source_sha) \
            or source_sha != expectations.get("source_sha"):
        raise FixtureError("source SHA does not match the fixture manifest")
    artifact_name = expectations.get("artifact_name")
    if (not isinstance(artifact_name, str)
            or artifact_name != f"mols-bjerge-qa-{source_sha}"
            or artifact_dir.name != artifact_name):
        raise FixtureError("artifact directory identity is invalid")
    if expectations.get("attribution") != ATTRIBUTION:
        raise FixtureError("fixture manifest attribution is invalid")
    if expectations.get("area_name") != AREA_NAME \
            or expectations.get("area_relation_id") != AREA_RELATION_ID:
        raise FixtureError("fixture manifest area identity is invalid")

    documents: dict[str, Any] = {}
    for relative, expected_format in _REQUIRED_SOURCE_FILES.items():
        identity = expectations["source_files"].get(relative)
        if (not isinstance(identity, dict)
                or identity.get("format") != expected_format
                or not re.fullmatch(r"[0-9a-f]{64}",
                                    str(identity.get("canonical_sha256") or ""))):
            raise FixtureError(f"invalid source identity for {relative}")
        path = artifact_dir / relative
        try:
            raw = path.read_bytes()
        except FileNotFoundError as error:
            raise FixtureError(f"missing required source file: {relative}") from error
        except OSError as error:
            raise FixtureError(f"cannot read required source file: {relative}") from error
        if expected_format == "json":
            try:
                value = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise FixtureError(f"invalid JSON source file: {relative}") from error
            digest = canonical_json_sha256(value)
            documents[relative] = value
        else:
            try:
                value = raw.decode("utf-8")
            except UnicodeDecodeError as error:
                raise FixtureError(f"invalid UTF-8 source file: {relative}") from error
            digest = hashlib.sha256(raw).hexdigest()
            documents[relative] = value
        if digest != identity["canonical_sha256"]:
            raise FixtureError(f"source file identity mismatch: {relative}")

    manifest = _mapping(documents["manifest.json"], "artifact manifest")
    if manifest.get("schema_version") != 1:
        raise FixtureError("artifact manifest schema_version must be 1")
    if manifest.get("source_sha") != source_sha:
        raise FixtureError("artifact manifest source SHA is invalid")
    if manifest.get("attribution") != ATTRIBUTION \
            or ATTRIBUTION not in documents["README.md"]:
        raise FixtureError("artifact attribution is invalid")
    inputs = _mapping(manifest.get("inputs"), "artifact manifest inputs")
    expected_inputs = {
        "extract": "europe/denmark",
        "state": "Denmark",
        "dry_run": "true",
        "no_routes": "false",
        "touch_report": "false",
        "elevation": "false",
        "region_code": "dk",
        "pilot_area_id": "nationalpark-mols-bjerge-dk",
        "pilot_bbox": "10.40,56.08,10.90,56.35",
        "expected_osm_relation_id": str(AREA_RELATION_ID),
    }
    if inputs != expected_inputs:
        raise FixtureError("artifact manifest inputs are not the run-33 pilot inputs")

    report = _mapping(documents["reports/assembly.json"], "assembly report")
    if (report.get("schema_version") != 1 or report.get("status") != "ok"
            or report.get("exact_area_required") is not True
            or report.get("area_query") != AREA_NAME
            or report.get("expected_area_relation_id") != AREA_RELATION_ID
            or report.get("boundary") != {
                "name": AREA_NAME,
                "osm_type": "relation",
                "osm_id": AREA_RELATION_ID,
            }
            or report.get("clip_applied") is not True):
        raise FixtureError("assembly report does not identify the exact run-33 area")
    return documents


def _projection_summary(fixture: dict) -> dict:
    return {
        "candidate_count": len(fixture["candidates"]),
        "coordinate_count": len(fixture["coordinates"]),
        "raw_way_count": len(fixture["ways"]),
        "missing_member_way_ids": fixture["missing_member_way_ids"],
    }


def build_fixture(documents: dict[str, Any], *, run_id: int,
                  source_sha: str, artifact_name: str) -> dict:
    """Project validated local artifact documents into the replay schema."""
    raw_features = _feature_collection(
        documents["data/aoi/mols-bjerge.raw.geojson"], "raw GeoJSON")
    trail_features = _feature_collection(
        documents["data/aoi/mols-bjerge.trails.geojson"], "trails GeoJSON")
    area_features = _feature_collection(
        documents["data/aoi/mols-bjerge.areas.geojson"], "areas GeoJSON")

    matching_areas = [
        feature for feature in area_features
        if (feature.get("properties") or {}).get("name") == AREA_NAME
    ]
    if len(matching_areas) != 1:
        raise FixtureError("areas GeoJSON must contain one exact Mols boundary")
    area_geometry = matching_areas[0].get("geometry")
    if (not isinstance(area_geometry, dict)
            or area_geometry.get("type") not in {"Polygon", "MultiPolygon"}
            or not isinstance(area_geometry.get("coordinates"), list)
            or not area_geometry["coordinates"]):
        raise FixtureError("exact Mols boundary geometry is invalid")

    candidate_rows = []
    used_way_ids: set[int] = set()
    for index, feature in enumerate(trail_features):
        properties = _mapping(feature.get("properties"),
                              f"trail[{index}].properties")
        name = properties.get("name")
        source = properties.get("source")
        member_ways = properties.get("member_ways")
        length_mi = properties.get("length_mi")
        if not isinstance(name, str) or not name.strip():
            raise FixtureError(f"trail[{index}] has no name")
        if source not in {"relation", "name-stitch"}:
            raise FixtureError(f"trail[{index}] has invalid assembly source")
        if (not isinstance(member_ways, list) or not member_ways
                or any(not isinstance(way_id, int) or isinstance(way_id, bool)
                       or way_id <= 0 for way_id in member_ways)):
            raise FixtureError(f"trail[{index}] has invalid member ways")
        if (not isinstance(length_mi, (int, float)) or isinstance(length_mi, bool)
                or not math.isfinite(float(length_mi)) or length_mi <= 0):
            raise FixtureError(f"trail[{index}] has invalid length")
        geometry = _mapping(feature.get("geometry"), f"trail[{index}].geometry")
        coordinates = geometry.get("coordinates")
        if geometry.get("type") == "LineString":
            lines = [coordinates]
        elif geometry.get("type") == "MultiLineString":
            lines = coordinates
        else:
            raise FixtureError(f"trail[{index}] geometry is not linework")
        if (not isinstance(lines, list) or not lines
                or any(not isinstance(line, list) or len(line) < 2 for line in lines)):
            raise FixtureError(f"trail[{index}] has invalid linework")
        used_way_ids.update(member_ways)
        candidate_rows.append((properties, member_ways, lines))

    raw_by_way_id = {}
    for feature in raw_features:
        match = _WAY_ID_RE.fullmatch(str(feature.get("id") or ""))
        if match is None:
            continue
        way_id = int(match.group(1))
        if way_id not in used_way_ids:
            continue
        if way_id in raw_by_way_id:
            raise FixtureError(f"raw GeoJSON duplicates w{way_id}")
        geometry = _mapping(feature.get("geometry"), f"raw w{way_id} geometry")
        coordinates = geometry.get("coordinates")
        if (geometry.get("type") != "LineString"
                or not isinstance(coordinates, list) or len(coordinates) < 2):
            raise FixtureError(f"raw w{way_id} is not valid linework")
        properties = _mapping(feature.get("properties"),
                              f"raw w{way_id} properties")
        raw_by_way_id[way_id] = (coordinates, properties)

    coordinates: list[list[float]] = []
    coordinate_indexes: dict[tuple[float, float], int] = {}

    def intern(value: Any, label: str) -> int:
        point = _coordinate(value, label)
        key = (point[0], point[1])
        index = coordinate_indexes.get(key)
        if index is None:
            coordinates.append(point)
            index = len(coordinates)
            coordinate_indexes[key] = index
        return index

    ways = []
    for way_id in sorted(raw_by_way_id):
        way_coordinates, properties = raw_by_way_id[way_id]
        ways.append({
            "way_id": way_id,
            "nodes": [
                intern(point, f"raw w{way_id} coordinate")
                for point in way_coordinates
            ],
            "tags": {
                key: properties[key] for key in _DECISIVE_TAGS
                if key in properties
            },
        })

    candidates = []
    for index, (properties, member_ways, lines) in enumerate(candidate_rows):
        candidate = {
            "name": properties["name"],
            "source": properties["source"],
            "member_ways": list(member_ways),
            "length_mi": properties["length_mi"],
            "lines": [
                [intern(point, f"trail[{index}] coordinate") for point in line]
                for line in lines
            ],
        }
        if properties.get("clipped") is True:
            candidate["clipped"] = True
        candidates.append(candidate)

    assembly_report = documents["reports/assembly.json"]
    if assembly_report.get("post_clip_trail_count") != len(candidates) \
            or assembly_report.get("assembled_trail_count") != len(candidates):
        raise FixtureError("assembly report trail counts disagree with source trails")

    return {
        "schema_version": 1,
        "description": (
            "Compact immutable test-only run-33 Denmark curation replay; "
            "not app output"
        ),
        "source": {
            "github_run_id": run_id,
            "source_sha": source_sha,
            "artifact_name": artifact_name,
            "attribution": ATTRIBUTION,
        },
        "limitations": list(_LIMITATIONS),
        "area": {
            "name": AREA_NAME,
            "geometry": copy.deepcopy(area_geometry),
        },
        "coordinates": coordinates,
        "ways": ways,
        "candidates": candidates,
        "missing_member_way_ids": sorted(used_way_ids - set(raw_by_way_id)),
    }


def generate_fixture(*, artifact_dir: Path, output: Path,
                     expectations_path: Path, run_id: int, source_sha: str,
                     update_manifest: bool = False) -> dict:
    """Validate local inputs, generate stable bytes, and atomically write them."""
    expectations = _load_expectations(expectations_path)
    documents = _validate_identity(artifact_dir, expectations, run_id, source_sha)
    fixture = build_fixture(
        documents, run_id=run_id, source_sha=source_sha,
        artifact_name=expectations["artifact_name"])
    payload = canonical_json_bytes(fixture)
    summary = _projection_summary(fixture)
    digest = hashlib.sha256(payload).hexdigest()

    if update_manifest:
        updated = copy.deepcopy(expectations)
        updated["projection"] = summary
        updated["output"] = {
            "file": FIXTURE_NAME,
            "sha256": digest,
            "bytes": len(payload),
        }
    else:
        if expectations.get("projection") != summary:
            raise FixtureError("generated replay projection summary changed")
        if expectations.get("output") != {
                "file": FIXTURE_NAME,
                "sha256": digest,
                "bytes": len(payload)}:
            raise FixtureError("generated replay hash or size changed")
        updated = None

    _atomic_write(output, payload)
    if updated is not None:
        _atomic_write(
            expectations_path,
            (json.dumps(updated, ensure_ascii=False, sort_keys=True, indent=2)
             + "\n").encode("utf-8"),
        )
    return {
        "sha256": digest,
        "bytes": len(payload),
        "projection": summary,
    }


def main(argv: list[str] | None = None) -> int:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--output", type=Path, default=here / FIXTURE_NAME)
    parser.add_argument("--expectations", type=Path,
                        default=here / EXPECTATIONS_NAME)
    parser.add_argument("--update-manifest", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = generate_fixture(
            artifact_dir=args.artifact_dir.resolve(),
            output=args.output.resolve(),
            expectations_path=args.expectations.resolve(),
            run_id=args.run_id,
            source_sha=args.source_sha,
            update_manifest=args.update_manifest,
        )
    except FixtureError as error:
        parser.exit(2, f"error: {error}\n")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
