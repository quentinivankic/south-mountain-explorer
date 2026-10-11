#!/usr/bin/env python3
"""Generate the compact, test-only Mols run-35 root-cause replay."""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

RUN_ID = 37941658802
RUN_NUMBER = 35
SOURCE_SHA = "b1db2afb4fdb51191551fc372411c04a515c0869"
ARTIFACT_NAME = f"mols-bjerge-qa-{SOURCE_SHA}"
ATTRIBUTION = "© OpenStreetMap contributors"
ODBL = "Open Database License (ODbL) 1.0"
FIXTURE_NAME = "mols-run35-root-cause-replay.json"
MANIFEST_NAME = "mols-run35-root-cause-replay.manifest.json"
AREA_RELATION_ID = 7046785
SHARED_WAY_ID = 695939153

_SOURCE_FILES = (
    "manifest.json",
    "data/source/mols-bjerge.relation-members.raw.json",
    "reports/assembly.json",
    "data/aoi/mols-bjerge.trails.geojson",
    "data/aoi/mols-bjerge.removed.geojson",
    "data/aoi/mols-bjerge.exact-area.geojson",
)
_OCCURRENCE_COLUMNS = (
    "root_relation_id", "relation_id", "member_index", "way_id", "role",
    "effective_role",
)
_OCCURRENCE_CASES = {
    "bjergetapen": {
        "root_relation_id": 8350111,
        "name": "Mols Bjerge-stien Bjergetapen",
        "population": "trails",
        "member_count": 85,
        "unique_member_count": 82,
        "geometry_count": 82,
        "rendered_count": 82,
    },
    "djurslandstien": {
        "root_relation_id": 14190349,
        "name": "Djurslandstien",
        "population": "trails",
        "member_count": 373,
        "unique_member_count": 321,
        "geometry_count": 321,
        "rendered_count": 106,
    },
    "kystbefaestningsstien": {
        "root_relation_id": 19549642,
        "name": "Kystbefæstningsstien",
        "population": "removed",
        "member_count": 21,
        "unique_member_count": 17,
        "geometry_count": 17,
        "rendered_count": 0,
    },
    "nordsoevandreruten": {
        "root_relation_id": 2006949,
        "name": "Nordsøvandreruten",
        "population": "removed",
        "member_count": 3338,
        "unique_member_count": 3308,
        "geometry_count": 3308,
        "rendered_count": 75,
    },
}
_REMOVED_REVERSALS = {
    42: ("Kløverstier-Byruten", 26),
    43: ("Dråby Sporet. Gul rute", 8),
    44: ("Kystbefæstningsstien", 4),
}
_EXPECTED_RESIDUAL_TRAILS = (
    "Mols Bjerge-stien Ebeltoftetapen",
    "Mols Bjerge-stien Gåsehage-etapen",
    "Djurslandstien",
    "Langs Kysten",
    "Den gamle købstad",
)


class FixtureError(ValueError):
    pass


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode("utf-8")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _value_sha256(value: Any) -> str:
    return _sha256(canonical_json_bytes(value))


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


def _load_sources(artifact_dir: Path) -> tuple[dict[str, Any], dict[str, dict]]:
    documents = {}
    identities = {}
    for relative in _SOURCE_FILES:
        path = artifact_dir / relative
        try:
            raw = path.read_bytes()
        except OSError as error:
            raise FixtureError(
                f"cannot read required source {relative}: {error}") from error
        identities[relative] = {"bytes": len(raw), "sha256": _sha256(raw)}
        try:
            documents[relative] = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise FixtureError(f"invalid JSON source {relative}: {error}") from error
    return documents, identities


def _require_mapping(value: Any, label: str) -> dict:
    if not isinstance(value, dict):
        raise FixtureError(f"{label} must be an object")
    return value


def _require_features(document: Any, label: str) -> list[dict]:
    document = _require_mapping(document, label)
    features = document.get("features")
    if document.get("type") != "FeatureCollection" or not isinstance(features, list):
        raise FixtureError(f"{label} must be a FeatureCollection")
    return features


def _raw_way_index(ledger: dict) -> dict[int, dict]:
    ways: dict[int, dict] = {}
    relations = ledger.get("relations")
    if not isinstance(relations, list):
        raise FixtureError("raw relation ledger relations must be a list")
    for relation in relations:
        if not isinstance(relation, dict):
            raise FixtureError("raw relation ledger contains a malformed relation")
        for record in relation.get("direct_ways") or []:
            if not isinstance(record, dict) or not isinstance(record.get("way_id"), int):
                raise FixtureError("raw relation ledger contains a malformed way")
            way_id = record["way_id"]
            prior = ways.setdefault(way_id, record)
            if prior != record:
                raise FixtureError(f"raw authority conflicts for way {way_id}")
    return ways


def _orientation(record: dict, raw: dict) -> str:
    node_ids = record.get("node_ids")
    coordinates = record.get("coordinates")
    raw_nodes = raw.get("node_ids")
    raw_coordinates = raw.get("coordinates")
    if node_ids == raw_nodes and coordinates == raw_coordinates:
        return "source"
    if (node_ids == list(reversed(raw_nodes or []))
            and coordinates == list(reversed(raw_coordinates or []))):
        return "reversed"
    return "tampered"


def _feature_by_root(features: list[dict], root_id: int, name: str) -> tuple[int, dict]:
    matches = []
    for index, feature in enumerate(features):
        properties = feature.get("properties") if isinstance(feature, dict) else None
        if (isinstance(properties, dict)
                and properties.get("root_relation_ids") == [root_id]
                and properties.get("name") == name):
            matches.append((index, feature))
    if len(matches) != 1:
        raise FixtureError(
            f"expected one {name!r} feature rooted at {root_id}; found {len(matches)}")
    return matches[0]


def _included_occurrences(audit: dict, root_id: int) -> list[list[Any]]:
    rows = []
    for member in audit.get("direct_way_members") or []:
        if member.get("status") != "included":
            continue
        row = {
            "root_relation_id": root_id,
            "relation_id": member.get("relation_id"),
            "member_index": member.get("member_index"),
            "way_id": member.get("way_id"),
            "role": member.get("role"),
            "effective_role": member.get("effective_role"),
        }
        if (not all(isinstance(row[key], int) and not isinstance(row[key], bool)
                    for key in ("root_relation_id", "relation_id", "member_index",
                                "way_id"))
                or not all(isinstance(row[key], str)
                           for key in ("role", "effective_role"))):
            raise FixtureError(f"root {root_id} has malformed included occurrence")
        rows.append([row[column] for column in _OCCURRENCE_COLUMNS])
    return rows


def _occurrence_case(key: str, specification: dict, audits: dict[int, dict],
                     trails: list[dict], removed: list[dict]) -> dict:
    root_id = specification["root_relation_id"]
    population = trails if specification["population"] == "trails" else removed
    feature_index, feature = _feature_by_root(
        population, root_id, specification["name"])
    properties = _require_mapping(feature.get("properties"), f"{key} properties")
    member_way_ids = properties.get("member_ways")
    geometry_way_ids = properties.get("source_geometry_way_ids")
    rendered_way_ids = properties.get("rendered_source_way_ids")
    if not all(isinstance(value, list) for value in (
            member_way_ids, geometry_way_ids, rendered_way_ids)):
        raise FixtureError(f"{key} occurrence vectors are malformed")
    occurrences = _included_occurrences(audits[root_id], root_id)
    occurrence_way_ids = [row[3] for row in occurrences]
    expected = (
        len(member_way_ids), len(set(member_way_ids)), len(geometry_way_ids),
        len(rendered_way_ids),
    )
    required = (
        specification["member_count"], specification["unique_member_count"],
        specification["geometry_count"], specification["rendered_count"],
    )
    if expected != required or occurrence_way_ids != member_way_ids:
        raise FixtureError(
            f"{key} occurrence characterization changed: {expected!r} != {required!r}")
    if len(geometry_way_ids) != len(set(geometry_way_ids)):
        raise FixtureError(f"{key} geometry identity is not unique")
    if len(rendered_way_ids) != len(set(rendered_way_ids)):
        raise FixtureError(f"{key} rendered identity is not unique")
    positions: dict[int, list[int]] = defaultdict(list)
    for index, way_id in enumerate(member_way_ids):
        positions[way_id].append(index)
    repeated = [
        {"way_id": way_id, "positions": value}
        for way_id, value in sorted(positions.items()) if len(value) > 1
    ]
    return {
        "name": specification["name"],
        "population": specification["population"],
        "feature_index": feature_index,
        "root_relation_id": root_id,
        "occurrence_columns": list(_OCCURRENCE_COLUMNS),
        "member_way_occurrences": occurrences,
        "member_way_ids": member_way_ids,
        "member_way_ids_sha256": _value_sha256(member_way_ids),
        "source_geometry_way_ids": geometry_way_ids,
        "source_geometry_way_ids_sha256": _value_sha256(geometry_way_ids),
        "rendered_source_way_ids": rendered_way_ids,
        "rendered_source_way_ids_sha256": _value_sha256(rendered_way_ids),
        "repeated_way_positions": repeated,
        "ckey": properties.get("ckey"),
    }


def _residual_errors(quality: dict, trails: list[dict]) -> list[dict]:
    residual = []
    audit_indexes = {
        row.get("relation_id"): index
        for index, row in enumerate(quality.get("relation_member_audit") or [])
        if isinstance(row, dict)
    }
    unresolved_audits = quality.get("relation_member_unresolved") or []
    for row in unresolved_audits:
        relation_id = row.get("relation_id")
        reasons = row.get("reasons")
        residual.append({
            "category": "unsafe-in-area-relation-audit",
            "relation_id": relation_id,
            "reasons": reasons,
            "message": (
                f"relation-member-audit[{audit_indexes[relation_id]}] contains "
                f"unresolved member exclusions or evidence: {reasons!r}"
            ),
        })
    unresolved_trails = quality.get("signed_relation_unresolved") or []
    trail_indexes = {
        (feature.get("properties") or {}).get("name"): index
        for index, feature in enumerate(trails)
        if isinstance(feature, dict)
    }
    for row in unresolved_trails:
        name = row.get("name")
        residual.append({
            "category": "unresolved-trail-gap",
            "name": name,
            "relation_ids": row.get("relation_ids"),
            "miles": row.get("miles"),
            "message": f"trail[{trail_indexes[name]}] unresolved-relation-gap",
        })
    unresolved_names = [row.get("name") for row in unresolved_trails]
    residual.append({
        "category": "nonzero-unresolved-population",
        "count": quality.get("signed_relation_unresolved_count"),
        "miles": quality.get("signed_relation_unresolved_miles"),
        "message": (
            "unresolved-relation-gap population is nonzero: "
            + ", ".join(repr(name) for name in unresolved_names)
        ),
    })
    residual.append({
        "category": "assembly-quality-aggregate",
        "validation_failures": quality.get("validation_failures"),
        "message": "assembly quality report contains unresolved failures",
    })
    if len(residual) != 9:
        raise FixtureError(f"expected exactly nine residual gates; found {len(residual)}")
    if [row.get("relation_id") for row in residual[:2]] != [10507412, 13944294]:
        raise FixtureError("unsafe residual relation set changed")
    if tuple(row.get("name") for row in residual[2:7]) != _EXPECTED_RESIDUAL_TRAILS:
        raise FixtureError("unresolved residual trail set changed")
    if (residual[7].get("count"), residual[7].get("miles")) != (5, 54.571):
        raise FixtureError("unresolved residual aggregate changed")
    if len(residual[8]["validation_failures"] or []) != 7:
        raise FixtureError("assembly quality residual aggregate changed")
    return residual


def build_fixture(documents: dict[str, Any]) -> dict:
    manifest = _require_mapping(documents["manifest.json"], "manifest")
    ledger = _require_mapping(
        documents["data/source/mols-bjerge.relation-members.raw.json"],
        "raw relation ledger")
    assembly = _require_mapping(documents["reports/assembly.json"], "assembly")
    trails = _require_features(
        documents["data/aoi/mols-bjerge.trails.geojson"], "trails")
    removed = _require_features(
        documents["data/aoi/mols-bjerge.removed.geojson"], "removed")
    exact_area = _require_features(
        documents["data/aoi/mols-bjerge.exact-area.geojson"], "exact area")
    if (manifest.get("source_sha") != SOURCE_SHA
            or manifest.get("branch_ref") != "chat/denmark-trails"
            or manifest.get("attribution") != ATTRIBUTION):
        raise FixtureError("artifact manifest identity is not pinned run 35")
    if ledger.get("attribution") != ATTRIBUTION:
        raise FixtureError("raw relation ledger attribution is invalid")
    if (assembly.get("status") != "ok" or assembly.get("clip_applied") is not True
            or (assembly.get("boundary") or {}).get("osm_id") != AREA_RELATION_ID):
        raise FixtureError("assembly is not the successful exact Mols run")
    if len(exact_area) != 1:
        raise FixtureError("exact-area source must contain one boundary")
    boundary_feature = exact_area[0]
    boundary_properties = boundary_feature.get("properties") or {}
    if boundary_properties.get("osm_id") != AREA_RELATION_ID:
        raise FixtureError("exact-area relation identity changed")

    quality = _require_mapping(assembly.get("quality"), "assembly quality")
    audits_list = quality.get("relation_member_audit")
    if not isinstance(audits_list, list):
        raise FixtureError("relation-member audits are missing")
    audits = {audit.get("relation_id"): audit for audit in audits_list
              if isinstance(audit, dict)}
    if any(specification["root_relation_id"] not in audits
           for specification in _OCCURRENCE_CASES.values()):
        raise FixtureError("root-cause relation audits are incomplete")

    raw_ways = _raw_way_index(ledger)
    evidence_tags: dict[int, dict] = {}
    emitted_reversals = []
    emitted_relation_count = 0
    relation_candidate_records = []
    aoi_only_records = []
    required_raw_way_ids = set()
    for candidate_index, feature in enumerate(trails):
        properties = feature.get("properties") or {}
        if properties.get("source") != "relation":
            continue
        emitted_relation_count += 1
        records = properties.get("source_ways") or []
        for source_record_index, record in enumerate(records):
            way_id = record.get("way_id") if isinstance(record, dict) else None
            raw = raw_ways.get(way_id)
            if raw is None:
                aoi_only_records.append({
                    "candidate_index": candidate_index,
                    "source_record_index": source_record_index,
                    "way_id": way_id,
                })
                continue
            orientation = _orientation(record, raw)
            if orientation == "tampered":
                raise FixtureError(
                    f"trail[{candidate_index}] source record {source_record_index} "
                    f"is neither raw nor exactly reversed")
            required_raw_way_ids.add(way_id)
            tags = record.get("tags") or {}
            prior_tags = evidence_tags.setdefault(way_id, tags)
            if prior_tags != tags:
                raise FixtureError(f"source evidence tags conflict for way {way_id}")
            if orientation == "reversed":
                emitted_reversals.append({
                    "candidate_index": candidate_index,
                    "candidate_name": properties.get("name"),
                    "source_record_index": source_record_index,
                    "way_id": way_id,
                    "serialized_node_ids_sha256": _value_sha256(
                        record.get("node_ids")),
                    "serialized_coordinates_sha256": _value_sha256(
                        record.get("coordinates")),
                })
        relation_candidate_records.append({
            "candidate_index": candidate_index,
            "name": properties.get("name"),
            "ckey": properties.get("ckey"),
            "relation_ids": properties.get("relation_ids") or [],
            "root_relation_ids": properties.get("root_relation_ids") or [],
            "direct_relation_way_ids": properties.get(
                "direct_relation_way_ids") or {},
            "source_geometry_way_ids": properties.get(
                "source_geometry_way_ids") or [],
            "restored_relation_way_ids": properties.get(
                "restored_relation_way_ids") or [],
            "geometry": feature.get("geometry"),
        })
    if (emitted_relation_count != 26 or len(emitted_reversals) != 591
            or len({row["way_id"] for row in emitted_reversals}) != 418
            or aoi_only_records != [{
                "candidate_index": 3,
                "source_record_index": 15,
                "way_id": 37917643,
            }]):
        raise FixtureError("emitted source-orientation characterization changed")

    removed_reversals = []
    removed_breakdown = {}
    for removed_index, (name, expected_count) in _REMOVED_REVERSALS.items():
        feature = removed[removed_index]
        properties = feature.get("properties") or {}
        if properties.get("name") != name:
            raise FixtureError(f"removed[{removed_index}] identity changed")
        rows = []
        for source_record_index, record in enumerate(
                properties.get("source_ways") or []):
            way_id = record.get("way_id") if isinstance(record, dict) else None
            raw = raw_ways.get(way_id)
            if raw is None:
                raise FixtureError(
                    f"removed[{removed_index}] way {way_id} lacks raw authority")
            orientation = _orientation(record, raw)
            if orientation == "tampered":
                raise FixtureError(
                    f"removed[{removed_index}] source record is tampered")
            required_raw_way_ids.add(way_id)
            tags = record.get("tags") or {}
            prior_tags = evidence_tags.setdefault(way_id, tags)
            if prior_tags != tags:
                raise FixtureError(f"source evidence tags conflict for way {way_id}")
            if orientation == "reversed":
                row = {
                    "removed_index": removed_index,
                    "removed_name": name,
                    "source_record_index": source_record_index,
                    "way_id": way_id,
                    "serialized_node_ids_sha256": _value_sha256(
                        record.get("node_ids")),
                    "serialized_coordinates_sha256": _value_sha256(
                        record.get("coordinates")),
                }
                rows.append(row)
                removed_reversals.append(row)
        if len(rows) != expected_count:
            raise FixtureError(
                f"removed[{removed_index}] reversal count changed: {len(rows)}")
        removed_breakdown[str(removed_index)] = len(rows)
    if len(removed_reversals) != 38 or removed_breakdown != {
            "42": 26, "43": 8, "44": 4}:
        raise FixtureError("removed source-orientation characterization changed")

    occurrence_cases = {
        key: _occurrence_case(key, specification, audits, trails, removed)
        for key, specification in _OCCURRENCE_CASES.items()
    }

    surviving_index, surviving = _feature_by_root(
        trails, 11350070, "Kløverstier-Landsbyruten")
    dropped_index, dropped = _feature_by_root(
        removed, 11350071, "Kløverstier-Byruten")
    shared_raw = raw_ways.get(SHARED_WAY_ID)
    if shared_raw is None:
        raise FixtureError("shared way lacks raw authority")
    required_raw_way_ids.add(SHARED_WAY_ID)
    for feature in (surviving, dropped):
        source = next((record for record in
                       (feature.get("properties") or {}).get("source_ways") or []
                       if record.get("way_id") == SHARED_WAY_ID), None)
        if source is None:
            raise FixtureError("shared way is missing from one root descriptor")
        evidence_tags.setdefault(SHARED_WAY_ID, source.get("tags") or {})
    surviving_properties = surviving["properties"]
    dropped_properties = dropped["properties"]
    shared_way = {
        "way_id": SHARED_WAY_ID,
        "surviving_root_relation_id": 11350070,
        "dropped_root_relation_id": 11350071,
        "surviving_candidate_index": surviving_index,
        "dropped_feature_index": dropped_index,
        "surviving_direct_authorization": (
            SHARED_WAY_ID in (surviving_properties.get(
                "direct_relation_way_ids") or {}).get("11350070", [])),
        "dropped_direct_authorization": (
            SHARED_WAY_ID in (dropped_properties.get(
                "direct_relation_way_ids") or {}).get("11350071", [])),
        "surviving_rendered": SHARED_WAY_ID in (
            surviving_properties.get("rendered_source_way_ids") or []),
        "dropped_rendered": SHARED_WAY_ID in (
            dropped_properties.get("rendered_source_way_ids") or []),
    }
    if shared_way != {
            "way_id": SHARED_WAY_ID,
            "surviving_root_relation_id": 11350070,
            "dropped_root_relation_id": 11350071,
            "surviving_candidate_index": 12,
            "dropped_feature_index": 42,
            "surviving_direct_authorization": True,
            "dropped_direct_authorization": True,
            "surviving_rendered": False,
            "dropped_rendered": False}:
        raise FixtureError("shared-root contamination characterization changed")

    for candidate in relation_candidate_records:
        for way_id in candidate["source_geometry_way_ids"]:
            if way_id in raw_ways:
                required_raw_way_ids.add(way_id)
                feature = trails[candidate["candidate_index"]]
                source = next((record for record in
                               feature["properties"].get("source_ways") or []
                               if record.get("way_id") == way_id), None)
                if source is not None:
                    prior_tags = evidence_tags.setdefault(
                        way_id, source.get("tags") or {})
                    if prior_tags != (source.get("tags") or {}):
                        raise FixtureError(
                            f"source evidence tags conflict for way {way_id}")

    raw_way_records = []
    for way_id in sorted(required_raw_way_ids):
        raw = raw_ways[way_id]
        if raw.get("status") != "present":
            raise FixtureError(f"required raw way {way_id} is unavailable")
        raw_way_records.append({
            "way_id": way_id,
            "node_ids": raw.get("node_ids"),
            "coordinates": raw.get("coordinates"),
            "missing_node_ids": raw.get("missing_node_ids") or [],
            "tags": evidence_tags.get(way_id, {}),
        })

    expected_aggregate = {
        "restored_way_count": 450,
        "restored_miles": 33.411457,
        "total_relation_miles": 138.868205,
        "restored_share": 0.240598,
    }
    restored_rows = quality.get("restored_road_relations")
    if (not isinstance(restored_rows, list) or len(restored_rows) != 27
            or quality.get("restored_road_aggregate") != expected_aggregate):
        raise FixtureError("restored-road characterization changed")

    initial_errors = {
        "mutable_orientation": {
            "emitted_reversals": 591,
            "emitted_unique_ways": 418,
            "emitted_validation_passes": 2,
            "removed_reversals": 38,
            "removed_fields_per_reversal": 2,
            "final_error_entries": 1258,
        },
        "occurrence_identity": {
            "final_error_entries": 11,
        },
        "dropped_root_contamination": {
            "final_error_entries": 1,
        },
        "restored_road_downstream": {
            "final_error_entries": 2,
        },
        "total": 1272,
    }
    if (591 * 2 + 38 * 2 != 1258
            or 1258 + 11 + 1 + 2 != initial_errors["total"]):
        raise FixtureError("implementation-error reconciliation is inconsistent")

    return {
        "schema_version": 1,
        "description": (
            "Compact deterministic run-35 Denmark source-evidence replay; "
            "test-only and never runtime/app data"
        ),
        "provenance": {
            "github_run_id": RUN_ID,
            "github_run_number": RUN_NUMBER,
            "source_sha": SOURCE_SHA,
            "artifact_name": ARTIFACT_NAME,
            "attribution": ATTRIBUTION,
            "license": ODBL,
            "geometry_source": "OpenStreetMap",
            "use": "test-only-root-cause-regression",
        },
        "exact_area": boundary_feature,
        "raw_way_records": raw_way_records,
        "orientation_replay": {
            "emitted_relation_candidate_count": emitted_relation_count,
            "emitted_reversals": emitted_reversals,
            "emitted_unique_reversed_way_ids": sorted({
                row["way_id"] for row in emitted_reversals}),
            "aoi_only_records": aoi_only_records,
            "removed_reversals": removed_reversals,
            "removed_reversal_counts": removed_breakdown,
        },
        "occurrence_cases": occurrence_cases,
        "shared_root_way": shared_way,
        "restored_replay": {
            "candidates": relation_candidate_records,
            "expected_rows": restored_rows,
            "expected_aggregate": expected_aggregate,
        },
        "archived_implementation_error_reconciliation": initial_errors,
        "expected_residual_errors": _residual_errors(quality, trails),
    }


def _projection(fixture: dict) -> dict:
    orientation = fixture["orientation_replay"]
    occurrence_cases = fixture["occurrence_cases"]
    return {
        "raw_way_record_count": len(fixture["raw_way_records"]),
        "emitted_reversal_count": len(orientation["emitted_reversals"]),
        "emitted_unique_reversed_way_count": len(
            orientation["emitted_unique_reversed_way_ids"]),
        "removed_reversal_count": len(orientation["removed_reversals"]),
        "occurrence_counts": {
            key: len(case["member_way_occurrences"])
            for key, case in occurrence_cases.items()
        },
        "restored_candidate_count": len(fixture["restored_replay"]["candidates"]),
        "restored_row_count": len(fixture["restored_replay"]["expected_rows"]),
        "residual_error_count": len(fixture["expected_residual_errors"]),
    }


def generate(*, artifact_dir: Path, output: Path, manifest_path: Path,
             update_manifest: bool = False) -> dict:
    if artifact_dir.name != ARTIFACT_NAME:
        raise FixtureError(
            f"artifact directory must be named {ARTIFACT_NAME!r}")
    documents, identities = _load_sources(artifact_dir)
    fixture = build_fixture(documents)
    raw = canonical_json_bytes(fixture)
    expected = {
        "schema_version": 1,
        "source": {
            "github_run_id": RUN_ID,
            "github_run_number": RUN_NUMBER,
            "source_sha": SOURCE_SHA,
            "artifact_name": ARTIFACT_NAME,
            "attribution": ATTRIBUTION,
            "license": ODBL,
        },
        "source_files": identities,
        "projection": _projection(fixture),
        "output": {
            "file": FIXTURE_NAME,
            "bytes": len(raw),
            "sha256": _sha256(raw),
        },
    }
    if not update_manifest:
        try:
            actual = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise FixtureError(f"cannot load fixture manifest: {error}") from error
        if actual != expected:
            raise FixtureError("fixture manifest/source/output identity changed")
    _atomic_write(output, raw)
    if update_manifest:
        _atomic_write(
            manifest_path,
            (json.dumps(expected, ensure_ascii=False, sort_keys=True, indent=2)
             + "\n").encode("utf-8"),
        )
    return expected


def main(argv: list[str] | None = None) -> int:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", required=True, type=Path)
    parser.add_argument("--output", type=Path, default=here / FIXTURE_NAME)
    parser.add_argument("--manifest", type=Path, default=here / MANIFEST_NAME)
    parser.add_argument("--update-manifest", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = generate(
            artifact_dir=args.artifact_dir.resolve(), output=args.output.resolve(),
            manifest_path=args.manifest.resolve(),
            update_manifest=args.update_manifest)
    except FixtureError as error:
        parser.exit(2, f"error: {error}\n")
    print(json.dumps(result["output"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
