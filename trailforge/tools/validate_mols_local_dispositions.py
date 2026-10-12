#!/usr/bin/env python3
"""Validate snapshot-bound Mols local dispositions without changing output.

The ledger is an observational acceptance overlay.  It never enters assembly,
publishing, or final-validation inputs, so explicit omission decisions remain
visible while the accepted nine genuine producer errors stay byte-for-byte
unchanged.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import tempfile
from typing import Any

EXPECTED_LEDGER_SHA256 = (
    "e1ea024bf6a455abb3b6d73aed99ed4749797e3a94825f6bf5dc7ba9980e643f"
)
EXPECTED_HEAD = "a85cd00a894558f47bf5fb70e4ae88f475f3903a"
EXPECTED_WIP = "9ec7960e6f2ec490268c339b32d923e0ca7423b4e3299394e94bacb0b2d04980"
EXPECTED_FINAL_ERRORS = [
    "relation-member-audit[11] contains unresolved member exclusions or "
    "evidence: ['unsafe-member-in-exact-area']",
    "relation-member-audit[21] contains unresolved member exclusions or "
    "evidence: ['unsafe-member-in-exact-area']",
    "unresolved-relation-gap population is nonzero: 'Mols Bjerge-stien "
    "Ebeltoftetapen', 'Mols Bjerge-stien Gåsehage-etapen', "
    "'Djurslandstien', 'Langs Kysten', 'Den gamle købstad'",
    "assembly quality report contains unresolved failures",
    "trail[10] unresolved-relation-gap",
    "trail[17] unresolved-relation-gap",
    "trail[18] unresolved-relation-gap",
    "trail[23] unresolved-relation-gap",
    "trail[24] unresolved-relation-gap",
]
EXPECTED_OMISSIONS = {
    "D1": {
        "relation": 10507412,
        "name": "Mols Bjerge-stien Ebeltoftetapen",
        "index": 10,
        "miles": 13.252,
        "components": 2,
        "unsafe": [324442353],
    },
    "D2": {
        "relation": 13944294,
        "name": "Mols Bjerge-stien Gåsehage-etapen",
        "index": 17,
        "miles": 13.798,
        "components": 2,
        "unsafe": [532412676, 532412678, 273639315],
    },
    "D3": {
        "relation": 14190349,
        "name": "Djurslandstien",
        "index": 18,
        "miles": 24.114,
        "components": 5,
        "unsafe": [],
    },
    "D4": {
        "relation": 19113817,
        "name": "Langs Kysten",
        "index": 23,
        "miles": 2.073,
        "components": 3,
        "unsafe": [],
    },
    "D5": {
        "relation": 19113818,
        "name": "Den gamle købstad",
        "index": 24,
        "miles": 1.334,
        "components": 2,
        "unsafe": [],
    },
}


class DispositionError(ValueError):
    """The ledger or one of its bound source artifacts is not exact."""


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DispositionError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _strict_json(raw: bytes, label: str) -> Any:
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeError, json.JSONDecodeError, DispositionError) as error:
        raise DispositionError(f"invalid {label}: {error}") from error


def _expect(errors: list[str], condition: bool, message: str) -> None:
    if not condition:
        errors.append(message)


def _decision_map(document: dict, errors: list[str]) -> dict[str, dict]:
    decisions = document.get("decisions")
    if not isinstance(decisions, list):
        errors.append("decisions must be a list")
        return {}
    mapped: dict[str, dict] = {}
    for index, decision in enumerate(decisions):
        if not isinstance(decision, dict):
            errors.append(f"decision[{index}] must be an object")
            continue
        decision_id = decision.get("decision_id")
        if not isinstance(decision_id, str) or decision_id in mapped:
            errors.append(f"decision[{index}] has invalid or duplicate id")
            continue
        mapped[decision_id] = decision
    _expect(errors, list(mapped) == [f"D{index}" for index in range(1, 9)],
            "decision order/set must be exactly D1 through D8")
    return mapped


def validate_ledger_document(document: Any, fixture: Any) -> list[str]:
    """Validate decision semantics against the committed frozen replay."""
    errors: list[str] = []
    if not isinstance(document, dict):
        return ["ledger must be an object"]
    if not isinstance(fixture, dict):
        return ["fixture must be an object"]

    _expect(errors, document.get("schema_version") == 1,
            "ledger schema_version must be 1")
    _expect(errors, document.get("record_id") ==
            "mols-bjerge-local-dispositions-2026-10-11",
            "ledger record_id is wrong")
    scope = document.get("scope") or {}
    _expect(errors, scope.get("kind") ==
            "snapshot-bound-local-acceptance-only",
            "ledger must remain snapshot-bound and local-only")
    _expect(errors, scope.get("runtime_effect") == "none",
            "ledger must have no runtime effect")
    _expect(errors, scope.get("publication_authority") is False,
            "ledger must not grant publication authority")
    _expect(errors, scope.get("boundary_osm_relation_id") == 7046785,
            "boundary relation 7046785 must remain exact")

    snapshot = document.get("snapshot") or {}
    _expect(errors, snapshot.get("implementation_commit") == EXPECTED_HEAD,
            "implementation commit is wrong")
    _expect(errors, snapshot.get("accepted_whole_wip_sha256") == EXPECTED_WIP,
            "accepted whole-WIP identity is wrong")
    _expect(errors, snapshot.get("source_run_id") == 37941658802,
            "source run must remain frozen run 35")
    _expect(errors, snapshot.get("source_geometry_authority") ==
            "OpenStreetMap", "geometry authority must remain OpenStreetMap")

    policy = document.get("safety_policy") or {}
    expected_false = (
        "coordinate_snapping", "synthetic_connectors",
        "unsafe_way_restoration", "visitdenmark_geometry",
    )
    for field in expected_false:
        _expect(errors, policy.get(field) is False,
                f"safety policy {field} must remain false")
    _expect(errors, policy.get("coordinate_tolerance_m") is None,
            "coordinate tolerance must remain null")
    _expect(errors, policy.get("connectivity_authority") ==
            "exact-osm-node-id-only",
            "connectivity must use exact OSM node ids only")
    _expect(errors, policy.get("excluded_parking_aisle_way_ids") ==
            [324442353, 532412676, 532412678, 273639315],
            "the four unsafe parking aisles must remain excluded")
    for field in (
            "preserve_raw_order", "preserve_duplicate_occurrences",
            "preserve_relation_and_source_identity",
            "preserve_duplicate_aware_ckey", "preserve_provenance",
            "fail_closed"):
        _expect(errors, policy.get(field) is True,
                f"safety policy {field} must remain true")

    effects = document.get("effects") or {}
    for field in (
            "producer_output_changed", "raw_osm_changed",
            "assembly_quality_dispositions_changed",
            "final_validator_errors_changed", "expected_errors_suppressed",
            "expected_errors_relabelled", "expected_errors_waived",
            "expected_errors_deleted", "publication_behavior_changed"):
        _expect(errors, effects.get(field) is False,
                f"observational ledger cannot set {field}")

    fixture_messages = [
        row.get("message") for row in fixture.get("expected_residual_errors", [])
        if isinstance(row, dict)
    ]
    _expect(errors, document.get("preserved_genuine_errors") ==
            EXPECTED_FINAL_ERRORS,
            "ledger must preserve the exact ordered final nine errors")
    _expect(errors, Counter(fixture_messages) == Counter(EXPECTED_FINAL_ERRORS)
            and len(fixture_messages) == 9,
            "frozen fixture no longer contains the same exact nine errors")
    reconciliation = fixture.get(
        "archived_implementation_error_reconciliation") or {}
    partition = document.get("error_partition") or {}
    _expect(errors, reconciliation.get("total") == 1272,
            "frozen implementation/evidence partition must remain 1272")
    _expect(errors, partition == {
        "archived_implementation_or_evidence_errors": 1272,
        "corrected_implementation_or_evidence_errors": 0,
        "preserved_genuine_errors": 9,
        "equation": "9 = 0 implementation/evidence + the same 9 genuine",
    }, "ledger error partition is not exact")

    decisions = _decision_map(document, errors)
    residual_by_relation = {
        row["relation_ids"][0]: row
        for row in fixture.get("expected_residual_errors", [])
        if isinstance(row, dict)
        and row.get("category") == "unresolved-trail-gap"
        and isinstance(row.get("relation_ids"), list)
        and len(row["relation_ids"]) == 1
    }
    for decision_id, expected in EXPECTED_OMISSIONS.items():
        decision = decisions.get(decision_id) or {}
        trail = decision.get("trail") or {}
        residual = decision.get("genuine_residual") or {}
        frozen = residual_by_relation.get(expected["relation"]) or {}
        _expect(errors, decision.get("kind") == "relation-explicit-omission",
                f"{decision_id} kind is wrong")
        _expect(errors, decision.get("status") == "accepted",
                f"{decision_id} is not accepted")
        _expect(errors, decision.get("authority") ==
                "root-conductor-decision",
                f"{decision_id} authority is wrong")
        _expect(errors, decision.get("local_disposition") ==
                "explicitly-omitted-from-this-snapshot-local-acceptance",
                f"{decision_id} is not an explicit snapshot omission")
        _expect(errors, trail == {
            "name": expected["name"],
            "osm_relation_id": expected["relation"],
            "quality_candidate_index": expected["index"],
            "miles": expected["miles"],
        }, f"{decision_id} trail identity is wrong")
        _expect(errors, frozen.get("name") == expected["name"]
                and frozen.get("miles") == expected["miles"],
                f"{decision_id} no longer binds the frozen genuine residual")
        _expect(errors, residual.get("producer_quality_disposition") ==
                "unresolved-relation-gap",
                f"{decision_id} changed the producer disposition")
        _expect(errors, residual.get("source_components") ==
                expected["components"] and
                residual.get("postclip_components") == expected["components"],
                f"{decision_id} component counts are wrong")
        _expect(errors, residual.get("missing_way_ids") == [],
                f"{decision_id} must retain an empty missing-way set")
        _expect(errors, residual.get("excluded_way_ids", []) ==
                expected["unsafe"],
                f"{decision_id} unsafe-way set is wrong")
        for message in residual.get("preserved_error_messages", []):
            _expect(errors, message in EXPECTED_FINAL_ERRORS,
                    f"{decision_id} cites a non-genuine error")

    d5 = decisions.get("D5") or {}
    d5_residual = d5.get("genuine_residual") or {}
    pairs = d5_residual.get("diagnostic_only_endpoint_pairs") or []
    _expect(errors, pairs == [{
        "node_ids": [9919843269, 389083051], "distance_m": 0.385,
    }], "D5 must preserve the distinct 0.385 m node pair")
    _expect(errors, d5_residual.get("nodes_are_distinct") is True and
            d5_residual.get("coordinate_tolerance_m") is None,
            "D5 cannot snap or apply tolerance to distinct nodes")

    d6 = decisions.get("D6") or {}
    occurrence = (fixture.get("occurrence_cases") or {}).get(
        "bjergetapen") or {}
    d6_trail = d6.get("trail") or {}
    d6_identity = d6.get("identity") or {}
    member_ids = occurrence.get("member_way_ids") or []
    _expect(errors, d6.get("kind") == "trail-identity-acceptance"
            and d6.get("status") == "accepted",
            "D6 must accept Bjergetapen identity")
    _expect(errors, d6_trail.get("osm_relation_id") == 8350111
            and d6_trail.get("quality_candidate_index") == 7,
            "D6 must bind Bjergetapen relation 8350111/candidate 7")
    _expect(errors, occurrence.get("root_relation_id") == 8350111
            and occurrence.get("feature_index") == 7,
            "fixture Bjergetapen identity is wrong")
    _expect(errors, len(member_ids) == 85 and len(set(member_ids)) == 82,
            "fixture Bjergetapen must retain 85 occurrences/82 ways")
    _expect(errors, d6_identity.get("raw_way_occurrence_count") == 85
            and d6_identity.get("unique_raw_way_count") == 82,
            "D6 occurrence counts are wrong")
    _expect(errors, occurrence.get("ckey") ==
            "w" + "-".join(str(value) for value in sorted(member_ids)),
            "fixture Bjergetapen ckey lost duplicate multiplicity")
    _expect(errors, _sha256(occurrence.get("ckey", "").encode("utf-8")) ==
            d6_identity.get("ckey_sha256"),
            "D6 ckey hash is wrong")

    d7 = decisions.get("D7") or {}
    _expect(errors, d7.get("kind") == "visual-packet-acceptance"
            and d7.get("status") == "accepted",
            "D7 must accept the local visual packet")
    _expect(errors, d7.get("visual_redesign_authorized") is False
            and d7.get("publication_authority") is False,
            "D7 cannot authorize redesign or publication")

    d8 = decisions.get("D8") or {}
    _expect(errors, d8.get("kind") == "hosted-run-final-disposition"
            and d8.get("status") == "accepted",
            "D8 decision is wrong")
    _expect(errors, d8.get("local_disposition") ==
            "infrastructure-inconclusive-final-no-retry",
            "D8 must leave run 38181006310 final/inconclusive")
    _expect(errors, d8.get("run") == {
        "id": 38181006310,
        "number": 36,
        "attempt": 1,
        "head_sha": EXPECTED_HEAD,
        "status": "completed",
        "conclusion": "failure",
    }, "D8 run identity is wrong")
    for field in ("retry_authorized", "retry_performed",
                  "hosted_success_claimed", "publication_performed"):
        _expect(errors, d8.get(field) is False,
                f"D8 {field} must remain false")

    evidence = document.get("evidence_files")
    if not isinstance(evidence, list):
        errors.append("evidence_files must be a list")
    else:
        keys = [row.get("key") for row in evidence if isinstance(row, dict)]
        _expect(errors, len(evidence) == 17 and len(set(keys)) == 17,
                "evidence file inventory must have 17 unique keys")
        for row in evidence:
            if not isinstance(row, dict):
                errors.append("evidence file entry must be an object")
                continue
            relative = PurePosixPath(str(row.get("path", "")))
            _expect(errors, not relative.is_absolute()
                    and bool(relative.parts)
                    and all(part not in {"", ".", ".."}
                            for part in relative.parts),
                    f"unsafe evidence path: {row.get('path')!r}")
            _expect(errors, isinstance(row.get("bytes"), int)
                    and not isinstance(row.get("bytes"), bool)
                    and row["bytes"] >= 0,
                    f"invalid evidence size: {row.get('key')!r}")
            _expect(errors, isinstance(row.get("sha256"), str)
                    and len(row["sha256"]) == 64
                    and all(char in "0123456789abcdef"
                            for char in row["sha256"]),
                    f"invalid evidence hash: {row.get('key')!r}")
    return errors


def validate_ledger_bytes(raw: bytes, fixture: Any) -> tuple[Any, list[str]]:
    """Strictly parse, exact-hash, and semantically validate ledger bytes."""
    errors: list[str] = []
    try:
        document = _strict_json(raw, "local disposition ledger")
    except DispositionError as error:
        return None, [str(error)]
    _expect(errors, _sha256(raw) == EXPECTED_LEDGER_SHA256,
            "local disposition ledger exact-byte hash is wrong")
    errors.extend(validate_ledger_document(document, fixture))
    return document, errors


def _safe_archive_file(root: Path, relative: str) -> Path:
    rel = PurePosixPath(relative)
    if (rel.is_absolute() or not rel.parts
            or any(part in {"", ".", ".."} for part in rel.parts)):
        raise DispositionError(f"unsafe archive path: {relative!r}")
    if root.is_symlink() or not root.is_dir():
        raise DispositionError("archive root must be a real directory")
    candidate = root.joinpath(*rel.parts)
    current = root
    for part in rel.parts:
        current = current / part
        try:
            info = current.lstat()
        except OSError as error:
            raise DispositionError(
                f"missing archive input {relative}: {error}") from error
        if stat.S_ISLNK(info.st_mode):
            raise DispositionError(f"archive input uses symlink: {relative}")
    info = candidate.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise DispositionError(f"archive input is not regular: {relative}")
    return candidate


def _strict_bound_json(raw_by_key: dict[str, bytes], key: str) -> Any:
    raw = raw_by_key.get(key)
    if raw is None:
        raise DispositionError(f"missing bound JSON key: {key}")
    return _strict_json(raw, key)


def _ordered_unique(values: list[int]) -> list[int]:
    return list(dict.fromkeys(values))


def _validate_source_way_fidelity(feature: dict, relation: dict,
                                  label: str, errors: list[str]) -> None:
    direct = {
        row.get("way_id"): row for row in relation.get("direct_ways", [])
        if isinstance(row, dict)
    }
    source_rows = feature.get("properties", {}).get("source_ways", [])
    source_ids = feature.get("properties", {}).get(
        "source_geometry_way_ids", [])
    _expect(errors, [row.get("way_id") for row in source_rows] == source_ids,
            f"{label} source-way record order is wrong")
    for row in source_rows:
        raw = direct.get(row.get("way_id"))
        _expect(errors, raw is not None, f"{label} source way lacks raw record")
        if raw is None:
            continue
        for field in ("node_ids", "coordinates", "missing_node_ids"):
            _expect(errors, row.get(field) == raw.get(field),
                    f"{label} source way {row.get('way_id')} changed {field}")


def validate_archive(document: dict, archive_root: Path) -> tuple[
        list[str], list[dict]]:
    """Validate current immutable archive bytes and semantic bindings."""
    errors: list[str] = []
    identities: list[dict] = []
    raw_by_key: dict[str, bytes] = {}
    for row in document.get("evidence_files", []):
        key = row["key"]
        try:
            path = _safe_archive_file(archive_root, row["path"])
            raw = path.read_bytes()
        except (OSError, DispositionError) as error:
            errors.append(str(error))
            continue
        identity = {
            "key": key,
            "path": row["path"],
            "mode": format(path.lstat().st_mode, "o"),
            "bytes": len(raw),
            "sha256": _sha256(raw),
        }
        identities.append(identity)
        raw_by_key[key] = raw
        _expect(errors, identity["bytes"] == row["bytes"],
                f"bound evidence size changed: {key}")
        _expect(errors, identity["sha256"] == row["sha256"],
                f"bound evidence hash changed: {key}")

    try:
        final = _strict_bound_json(raw_by_key, "final_validation")
        assembly = _strict_bound_json(raw_by_key, "assembly_report")
        trails = _strict_bound_json(raw_by_key, "emitted_trails")
        authority = _strict_bound_json(raw_by_key, "raw_relation_ledger")
        terminal = _strict_bound_json(raw_by_key, "hosted_terminal_summary")
        visual_review = _strict_bound_json(
            raw_by_key, "visual_review_record")
        golden = _strict_bound_json(raw_by_key, "bjergetapen_golden_record")
        provider_branch = _strict_bound_json(
            raw_by_key, "provider_branch_receipt")
    except DispositionError as error:
        return [*errors, str(error)], identities

    _expect(errors, final.get("status") == "failed"
            and final.get("expected_osm_relation_id") == 7046785,
            "accepted final validation identity changed")
    _expect(errors, final.get("errors") == EXPECTED_FINAL_ERRORS,
            "accepted final validation no longer has the exact ordered nine")

    quality = assembly.get("quality") or {}
    _expect(errors, quality.get("schema_version") == 8,
            "assembly quality schema must remain 8")
    _expect(errors, quality.get("signed_relation_unresolved_count") == 5
            and quality.get("signed_relation_unresolved_miles") == 54.571,
            "assembly must retain five unresolved relations/54.571 miles")
    _expect(errors, quality.get("restored_road_aggregate") == {
        "restored_way_count": 450,
        "restored_miles": 33.411457,
        "total_relation_miles": 138.868205,
        "restored_share": 0.240598,
    }, "restored-road aggregate changed")

    decisions = {row["decision_id"]: row for row in document["decisions"]}
    unresolved = {
        row.get("relation_ids", [None])[0]: row
        for row in quality.get("signed_relation_unresolved", [])
        if isinstance(row, dict) and len(row.get("relation_ids", [])) == 1
    }
    audit_by_relation = {
        row.get("relation_id"): row
        for row in quality.get("relation_member_audit", [])
        if isinstance(row, dict)
    }
    unresolved_audits = {
        row.get("relation_id"): row
        for row in quality.get("relation_member_unresolved", [])
        if isinstance(row, dict)
    }
    features = trails.get("features") if isinstance(trails, dict) else None
    relations = authority.get("relations") if isinstance(authority, dict) else None
    if not isinstance(features, list) or not isinstance(relations, list):
        return [*errors, "trail or relation collection is invalid"], identities
    features_by_relation: dict[int, list[dict]] = {}
    for feature in features:
        for relation_id in (feature.get("properties") or {}).get(
                "relation_ids", []):
            features_by_relation.setdefault(relation_id, []).append(feature)
    relations_by_id = {
        row.get("relation_id"): row for row in relations if isinstance(row, dict)
    }

    for decision_id, expected in EXPECTED_OMISSIONS.items():
        decision = decisions[decision_id]
        identity = decision["snapshot_identity"]
        residual = decision["genuine_residual"]
        relation_id = expected["relation"]
        matches = features_by_relation.get(relation_id, [])
        _expect(errors, len(matches) == 1,
                f"{decision_id} must identify one emitted feature")
        relation = relations_by_id.get(relation_id)
        _expect(errors, isinstance(relation, dict),
                f"{decision_id} raw relation is missing")
        if len(matches) != 1 or not isinstance(relation, dict):
            continue
        feature = matches[0]
        properties = feature.get("properties") or {}
        _expect(errors, properties.get("name") == expected["name"]
                and properties.get("relation_ids") == [relation_id]
                and properties.get("root_relation_ids") == [relation_id]
                and properties.get("quality_candidate_index") ==
                expected["index"]
                and properties.get("length_mi") == expected["miles"],
                f"{decision_id} emitted identity changed")
        _expect(errors, properties.get("quality_disposition") ==
                "unresolved-relation-gap",
                f"{decision_id} genuine producer disposition was suppressed")
        connectivity = properties.get("connectivity") or {}
        _expect(errors, connectivity.get("status") == "rejected"
                and connectivity.get("source_components") ==
                expected["components"]
                and connectivity.get("postclip_components") ==
                expected["components"]
                and connectivity.get("missing_way_ids") == [],
                f"{decision_id} exact-node residual changed")
        _expect(errors, _sha256(_canonical(relation)) ==
                identity["relation_record_sha256"],
                f"{decision_id} raw relation hash changed")
        _expect(errors, _sha256(_canonical(feature)) ==
                identity["feature_sha256"],
                f"{decision_id} feature hash changed")
        _expect(errors, _sha256(_canonical(
                    properties.get("member_way_occurrences"))) ==
                identity["occurrence_sha256"],
                f"{decision_id} occurrence hash changed")
        _expect(errors, _sha256(properties.get("ckey", "").encode()) ==
                identity["ckey_sha256"],
                f"{decision_id} ckey hash changed")
        _expect(errors, _sha256(_canonical(
                    properties.get("source_geometry_way_ids"))) ==
                identity["source_geometry_way_ids_sha256"],
                f"{decision_id} source-way order changed")
        _expect(errors, _sha256(_canonical(
                    properties.get("rendered_source_way_ids"))) ==
                identity["rendered_source_way_ids_sha256"],
                f"{decision_id} rendered-way order changed")
        occurrences = properties.get("member_way_occurrences") or []
        source_ids = properties.get("source_geometry_way_ids") or []
        rendered_ids = properties.get("rendered_source_way_ids") or []
        _expect(errors, len(occurrences) == identity["occurrence_count"],
                f"{decision_id} occurrence count changed")
        _expect(errors, len(source_ids) == len(set(source_ids)) ==
                identity["unique_source_way_count"],
                f"{decision_id} source geometry lost uniqueness")
        _expect(errors, len(rendered_ids) == len(set(rendered_ids)) ==
                identity["unique_rendered_way_count"],
                f"{decision_id} rendered geometry lost uniqueness")
        occurrence_ids = [row.get("way_id") for row in occurrences]
        _expect(errors, properties.get("ckey") ==
                "w" + "-".join(str(value)
                                 for value in sorted(occurrence_ids)),
                f"{decision_id} ckey no longer preserves occurrence multiplicity")
        _validate_source_way_fidelity(feature, relation, decision_id, errors)
        row = unresolved.get(relation_id) or {}
        _expect(errors, row.get("name") == expected["name"]
                and row.get("miles") == expected["miles"]
                and row.get("source_components") == expected["components"]
                and row.get("postclip_components") == expected["components"]
                and row.get("missing_way_ids") == [],
                f"{decision_id} assembly unresolved row changed")
        audit = audit_by_relation.get(relation_id) or {}
        if expected["unsafe"]:
            unresolved_audit = unresolved_audits.get(relation_id) or {}
            _expect(errors, audit.get("review_status") == "unresolved"
                    and unresolved_audit.get("excluded_way_ids") ==
                    expected["unsafe"]
                    and unresolved_audit.get("terminal_way_ids") ==
                    expected["unsafe"],
                    f"{decision_id} unsafe audit changed")
            raw_ways = {
                row.get("way_id"): row
                for row in relation.get("direct_ways", [])
                if isinstance(row, dict)
            }
            for way_id in expected["unsafe"]:
                way = raw_ways.get(way_id) or {}
                _expect(errors, (way.get("tags") or {}).get("service") ==
                        "parking_aisle",
                        f"{decision_id} unsafe way {way_id} is not the aisle")
        else:
            _expect(errors, audit.get("review_status") == "accepted"
                    and relation_id not in unresolved_audits,
                    f"{decision_id} relation audit must remain accepted")

    d6 = decisions["D6"]
    d6_identity = d6["identity"]
    bjerge_rel = relations_by_id.get(8350111)
    bjerge_features = features_by_relation.get(8350111, [])
    _expect(errors, isinstance(bjerge_rel, dict)
            and len(bjerge_features) == 1,
            "D6 must bind one raw relation and emitted Bjergetapen")
    if isinstance(bjerge_rel, dict) and len(bjerge_features) == 1:
        feature = bjerge_features[0]
        properties = feature.get("properties") or {}
        members = [row for row in bjerge_rel.get("members", [])
                   if isinstance(row, dict) and row.get("type") == "way"]
        member_ids = [row.get("ref") for row in members]
        direct_ids = bjerge_rel.get("direct_way_ids") or []
        direct_rows = bjerge_rel.get("direct_ways") or []
        _expect(errors, len(member_ids) == 85 and len(set(member_ids)) == 82,
                "D6 raw occurrence counts changed")
        _expect(errors, direct_ids == _ordered_unique(member_ids)
                and [row.get("way_id") for row in direct_rows] == direct_ids,
                "D6 raw direct-way order changed")
        _expect(errors, properties.get("member_ways") == member_ids,
                "D6 emitted member order/duplicates changed")
        occurrences = properties.get("member_way_occurrences") or []
        _expect(errors, [row.get("way_id") for row in occurrences] == member_ids,
                "D6 occurrence order changed")
        _expect(errors, properties.get("ckey") ==
                "w" + "-".join(str(value) for value in sorted(member_ids)),
                "D6 ckey lost duplicate multiplicity")
        _expect(errors, _sha256(_canonical(bjerge_rel)) ==
                d6_identity["raw_relation_sha256"],
                "D6 raw relation hash changed")
        _expect(errors, _sha256(_canonical(bjerge_rel.get("members"))) ==
                d6_identity["raw_member_list_sha256"],
                "D6 raw member-list hash changed")
        for field, key in (
                ("member_ways", "member_ways_sha256"),
                ("member_way_occurrences", "occurrence_sha256"),
                ("source_geometry_way_ids", "source_way_ids_sha256"),
                ("rendered_source_way_ids", "rendered_way_ids_sha256"),
                ("rendered_way_lineage", "rendered_lineage_sha256")):
            _expect(errors, _sha256(_canonical(properties.get(field))) ==
                    d6_identity[key], f"D6 {field} hash changed")
        _expect(errors, _sha256(properties.get("ckey", "").encode()) ==
                d6_identity["ckey_sha256"], "D6 ckey hash changed")
        _expect(errors, _sha256(_canonical(feature)) ==
                d6_identity["feature_sha256"], "D6 feature hash changed")
        source_ids = properties.get("source_geometry_way_ids") or []
        rendered_ids = properties.get("rendered_source_way_ids") or []
        _expect(errors, len(source_ids) == len(set(source_ids)) == 82
                and len(rendered_ids) == len(set(rendered_ids)) == 82,
                "D6 source/render identity counts changed")
        lineage = properties.get("rendered_way_lineage") or []
        orientations = Counter(row.get("orientation") for row in lineage)
        chains = Counter(row.get("chain_index") for row in lineage)
        _expect(errors, orientations == Counter({"source": 51,
                                                  "reversed": 31}),
                "D6 render orientations changed")
        _expect(errors, [chains[index] for index in sorted(chains)] == [36, 46],
                "D6 render chains changed")
        connectivity = properties.get("connectivity") or {}
        _expect(errors, properties.get("quality_disposition") == "kept"
                and connectivity.get("status") == "accepted"
                and connectivity.get("source_components") == 1
                and connectivity.get("postclip_components") == 1
                and connectivity.get("missing_way_ids") == [],
                "D6 connectivity/kept identity changed")
        _validate_source_way_fidelity(feature, bjerge_rel, "D6", errors)

    _expect(errors, ((golden.get("identity_disposition") or {}).get("human") ==
                     "not-yet-recorded"),
            "source golden record was rewritten instead of overlaid")
    _expect(errors, visual_review.get("human_disposition") ==
            "not-yet-recorded",
            "source visual review was rewritten instead of overlaid")

    d8 = decisions["D8"]
    run = terminal.get("run") or {}
    dispatch = terminal.get("dispatch") or {}
    safety = terminal.get("safety_outcome") or {}
    jobs = {row.get("name"): row for row in terminal.get("jobs", [])
            if isinstance(row, dict)}
    _expect(errors, {
        "id": run.get("id"), "number": run.get("run_number"),
        "attempt": run.get("run_attempt"), "head_sha": run.get("head_sha"),
        "status": run.get("status"), "conclusion": run.get("conclusion"),
    } == d8["run"], "D8 terminal run evidence changed")
    _expect(errors, dispatch.get("attempt_count") == 1
            and dispatch.get("retry_performed") is False,
            "D8 dispatch/retry evidence changed")
    _expect(errors, (jobs.get("pilot") or {}).get("conclusion") == "failure"
            and (jobs.get("publish") or {}).get("conclusion") == "skipped",
            "D8 job conclusions changed")
    _expect(errors, safety == {
        "all_post_discovery_processing_steps": "skipped",
        "checkout_integrity_proof": "success",
        "discovery": "failure",
        "exact_input_validation": "success",
        "ordinary_publish_job": "skipped",
        "qa_artifact_upload": "success",
        "retry_or_rerun": False,
    }, "D8 fail-closed/no-retry safety evidence changed")
    _expect(errors, terminal.get("post_run_provider_branch_sha") ==
            EXPECTED_HEAD, "D8 provider branch receipt changed")
    _expect(errors, provider_branch.get("name") == "chat/denmark-trails"
            and (provider_branch.get("commit") or {}).get("sha") ==
            EXPECTED_HEAD, "accepted provider branch identity changed")
    return errors, identities


def _atomic_write(path: Path, value: dict) -> None:
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)
           + "\n").encode("utf-8")
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--archive-root", required=True, type=Path)
    parser.add_argument("--result-json", required=True, type=Path)
    args = parser.parse_args(argv)
    errors: list[str] = []
    document = None
    identities: list[dict] = []
    try:
        ledger_raw = args.ledger.read_bytes()
        fixture = _strict_json(args.fixture.read_bytes(), "replay fixture")
        document, ledger_errors = validate_ledger_bytes(ledger_raw, fixture)
        errors.extend(ledger_errors)
        if isinstance(document, dict):
            archive_errors, identities = validate_archive(
                document, args.archive_root)
            errors.extend(archive_errors)
    except (OSError, DispositionError) as error:
        errors.append(str(error))
    decisions = document.get("decisions", []) if isinstance(document, dict) else []
    result = {
        "schema_version": 1,
        "stage": "local-dispositions",
        "status": "failed" if errors else "ok",
        "ledger": {
            "path": str(args.ledger),
            "sha256": (_sha256(args.ledger.read_bytes())
                       if args.ledger.is_file() else None),
        },
        "implementation_commit": EXPECTED_HEAD,
        "decision_ids": [row.get("decision_id") for row in decisions
                         if isinstance(row, dict)],
        "decision_omitted_relation_ids": [
            row.get("trail", {}).get("osm_relation_id")
            for row in decisions[:5] if isinstance(row, dict)
        ],
        "bjergetapen_identity_relation_id": 8350111,
        "preserved_genuine_error_count": 9,
        "corrected_implementation_or_evidence_error_count": 0,
        "evidence_files": identities,
        "external_action_performed": False,
        "errors": errors,
    }
    try:
        _atomic_write(args.result_json, result)
    except OSError as error:
        parser.error(f"cannot write result: {error}")
    print(json.dumps({
        "status": result["status"],
        "result_json": str(args.result_json),
        "decision_omitted_relation_ids": result[
            "decision_omitted_relation_ids"],
        "preserved_genuine_error_count": 9,
        "errors": errors,
    }, ensure_ascii=False, sort_keys=True))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
