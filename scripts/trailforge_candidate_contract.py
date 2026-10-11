#!/usr/bin/env python3
"""Total, arrival-order-independent contract for the 51-lane US candidate.

Every lane is represented by one typed terminal.  Missing or malformed inputs
are synthesized as adverse evidence.  Only all 51 exact successes permit local
review-candidate construction; no state in this module can commit, push,
synchronize, or write to a provider.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import hmac
import json
import os
import re
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from r2_release_contract import KeyClass, classify_key

CONTRACT_VERSION = "trekdex-trailforge-candidate-contract-v3"
LANE_SCHEMA = "trekdex-trailforge-lane-terminal-v6"
FAN_IN_SCHEMA = "trekdex-trailforge-fan-in-terminal-v5"
INHERITED_LANE_SCHEMA = "trekdex-state-geom-lane-v5"
APPROVED_CANDIDATE_MANIFEST_SCHEMA = "trekdex-trailforge-approved-inputs-v2"
APPROVED_CANDIDATE_AUTHORITY_SCHEMA = "trekdex-approved-candidate-authority-v1"
CANDIDATE_CONFIG_SCHEMA = "trekdex-trailforge-candidate-config-v3"
ROOT_APPROVAL_ASSERTION_SCHEMA = "trekdex-trailforge-root-approval-assertion-v1"
ROOT_APPROVAL_OPERATION = "BUILD_REVIEW_CANDIDATE"
ROOT_APPROVAL_ASSERTION_ENV = "TRAILFORGE_ROOT_APPROVAL_ASSERTION_JSON"
ROOT_APPROVAL_KEY_ENV = "TRAILFORGE_ROOT_APPROVAL_HMAC_KEY"
ROOT_APPROVAL_KEY_ID_ENV = "TRAILFORGE_ROOT_APPROVAL_KEY_ID"
INHERITED_LANE_FIELDS = frozenset({
    "schema", "lane", "display_state", "source_sha", "source_tree",
    "workflow_sha256", "candidate_config_sha256", "run_id", "run_attempt",
    "toolchain_manifest_sha256", "input_manifest_sha256",
    "approved_input_set_sha256", "approved_input_population_sha256",
    "approved_geometry_population_sha256", "source_index_sha256",
    "authority_sha256", "expected_area_count", "expected_area_ids_sha256",
    "files", "receipt_sha256",
})

EXPECTED_LANES = tuple(sorted("""
alabama alaska arizona arkansas california colorado connecticut delaware
district-of-columbia florida georgia hawaii idaho illinois indiana iowa
kansas kentucky louisiana maine maryland massachusetts michigan minnesota
mississippi missouri montana nebraska nevada new-hampshire new-jersey
new-mexico new-york north-carolina north-dakota ohio oklahoma oregon
pennsylvania rhode-island south-carolina south-dakota tennessee texas utah
vermont virginia washington west-virginia wisconsin wyoming
""".split()))

LANE_AUTHORITY_SCHEMA = "trekdex-pinned-us-lane-authority-v1"

_LANE_AUTHORITY_CACHE: dict[bytes, dict[str, Any]] = {}
_APPROVED_AUTHORITY_CACHE: dict[tuple[bytes, str], dict[str, Any]] = {}
_CANDIDATE_CONFIG_CACHE: dict[tuple[bytes, str, str], dict[str, Any]] = {}


def derive_authoritative_lane_semantics(
    source_index: Path | str | Sequence[Any],
) -> dict[str, Any]:
    """Derive exact 51-lane display/area truth from pinned index bytes."""
    if isinstance(source_index, (str, Path)):
        path = Path(source_index)
        if path.is_symlink() or not path.is_file():
            raise ValueError("authoritative source index is missing or unsafe")
        raw = path.read_bytes()
        if not raw:
            raise ValueError("authoritative source index is empty")
        try:
            document = _strict_json(raw.decode("utf-8"), str(path))
        except UnicodeDecodeError as error:
            raise ValueError("authoritative source index is not UTF-8") from error
    else:
        document = copy.deepcopy(source_index)
        raw = _canonical_bytes(document)
    cached = _LANE_AUTHORITY_CACHE.get(raw)
    if cached is not None:
        return copy.deepcopy(cached)
    if not isinstance(document, list):
        raise ValueError("authoritative source index is not an array")

    area_ids_by_lane: dict[str, list[str]] = {lane: [] for lane in EXPECTED_LANES}
    display_by_lane: dict[str, str] = {}
    seen_area_ids: set[str] = set()
    for number, row in enumerate(document, start=1):
        if (
            not isinstance(row, list)
            or len(row) < 3
            or not isinstance(row[0], str)
            or not row[0]
            or not isinstance(row[2], str)
            or not row[2]
        ):
            raise ValueError(
                f"authoritative source index row {number} lacks exact id/state authority"
            )
        area_id = row[0]
        display_state = row[2]
        if area_id in seen_area_ids:
            raise ValueError(f"authoritative source index repeats area id: {area_id}")
        seen_area_ids.add(area_id)
        if (
            display_state != display_state.strip()
            or any(ord(character) < 32 for character in display_state)
            or any(not word for word in display_state.split(" "))
        ):
            raise ValueError(
                f"authoritative source index row {number} has a noncanonical state"
            )
        lane = "-".join(word.lower() for word in display_state.split(" "))
        if lane not in area_ids_by_lane:
            continue
        if (
            classify_key(area_id + ".json", authoritative_area_ids={area_id})
            is not KeyClass.AREA_GEOM
        ):
            raise ValueError(
                f"authoritative source index row {number} has an invalid area id"
            )
        previous_display = display_by_lane.setdefault(lane, display_state)
        if previous_display != display_state:
            raise ValueError(f"authoritative lane {lane} has conflicting display states")
        area_ids_by_lane[lane].append(area_id)

    lanes: dict[str, dict[str, Any]] = {}
    for lane in EXPECTED_LANES:
        area_ids = sorted(area_ids_by_lane[lane])
        if not area_ids or lane not in display_by_lane:
            raise ValueError(f"authoritative source index has no areas for lane {lane}")
        area_ids_sha256 = hashlib.sha256(
            ("\n".join(area_ids) + "\n").encode("utf-8")
        ).hexdigest()
        lanes[lane] = {
            "display_state": display_by_lane[lane],
            "area_count": len(area_ids),
            "area_ids_sha256": area_ids_sha256,
            "area_ids": area_ids,
        }
    authority: dict[str, Any] = {
        "schema": LANE_AUTHORITY_SCHEMA,
        "source_index_sha256": hashlib.sha256(raw).hexdigest(),
        "required_lane_count": len(EXPECTED_LANES),
        "lanes": lanes,
    }
    authority["authority_sha256"] = canonical_sha256(authority)
    _LANE_AUTHORITY_CACHE[raw] = copy.deepcopy(authority)
    return copy.deepcopy(authority)


def _lane_authority(authority: Any, lane: str) -> dict[str, Any]:
    if (
        not isinstance(authority, dict)
        or set(authority) != {
            "schema", "source_index_sha256", "required_lane_count", "lanes",
            "authority_sha256",
        }
        or authority["schema"] != LANE_AUTHORITY_SCHEMA
        or authority["required_lane_count"] != len(EXPECTED_LANES)
        or not isinstance(authority["lanes"], dict)
        or set(authority["lanes"]) != set(EXPECTED_LANES)
        or not isinstance(authority["source_index_sha256"], str)
        or not _SHA256_RE.fullmatch(authority["source_index_sha256"])
        or authority["authority_sha256"]
        != canonical_sha256(authority, omit={"authority_sha256"})
    ):
        raise ValueError("authoritative lane semantics are not exact")
    semantics = authority["lanes"].get(lane)
    if (
        not isinstance(semantics, dict)
        or set(semantics) != {
            "display_state", "area_count", "area_ids_sha256", "area_ids"
        }
        or not isinstance(semantics["display_state"], str)
        or not semantics["display_state"]
        or not isinstance(semantics["area_ids"], list)
        or not semantics["area_ids"]
        or semantics["area_ids"] != sorted(semantics["area_ids"])
        or len(semantics["area_ids"]) != len(set(semantics["area_ids"]))
        or semantics["area_count"] != len(semantics["area_ids"])
        or semantics["area_ids_sha256"]
        != hashlib.sha256(
            ("\n".join(semantics["area_ids"]) + "\n").encode("utf-8")
        ).hexdigest()
    ):
        raise ValueError(f"authoritative semantics for lane {lane} are invalid")
    return semantics


_SHA1_RE = re.compile(r"[0-9a-f]{40}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_REF_RE = re.compile(r"sha256:[0-9a-f]{64}")
_RUN_ID_RE = re.compile(r"[1-9][0-9]{0,19}")
_LANE_RE = re.compile(r"[a-z]+(?:-[a-z]+)*")
_APPROVAL_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")


class LaneOutcome(str, Enum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    MISSING = "MISSING"
    CORRUPT = "CORRUPT"
    CANCELLED = "CANCELLED"
    SKIPPED = "SKIPPED"
    STALE = "STALE"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"


class FanInState(str, Enum):
    COMPLETE_SUCCESS = "COMPLETE_SUCCESS"
    NON_PROMOTABLE = "NON_PROMOTABLE"
    PENDING_HOSTED_CANCELLATION = "PENDING_HOSTED_CANCELLATION"


def _canonical_bytes(document: Any) -> bytes:
    return (
        json.dumps(
            document,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def canonical_sha256(document: Any, *, omit: set[str] | None = None) -> str:
    value = copy.deepcopy(document)
    if omit:
        if not isinstance(value, dict):
            raise TypeError("omit requires an object")
        for key in omit:
            value.pop(key, None)
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _strict_json(text: str, context: str) -> Any:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"{context} contains duplicate JSON key {key!r}")
            result[key] = value
        return result
    try:
        return json.loads(text, object_pairs_hook=reject_duplicates)
    except ValueError:
        raise
    except Exception as error:
        raise ValueError(f"{context} is not valid JSON: {error}") from error


def _require_identity(value: Any, pattern: re.Pattern[str], context: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f"{context} has invalid immutable identity")
    return value


def _validate_file_rows(files: Any, *, require_nonempty: bool) -> list[dict[str, Any]]:
    if not isinstance(files, list) or (require_nonempty and not files):
        raise ValueError("lane file set is missing")
    previous: str | None = None
    normalized: list[dict[str, Any]] = []
    for number, row in enumerate(files, start=1):
        if not isinstance(row, dict) or set(row) != {"name", "bytes", "sha256"}:
            raise ValueError(f"lane file {number} schema is not exact")
        name = row["name"]
        if (
            not isinstance(name, str)
            or classify_key(name, authoritative_area_ids={Path(name).stem})
            is not KeyClass.AREA_GEOM
        ):
            raise ValueError(f"lane file {number} name is not an exact area-geom key")
        if previous is not None and name <= previous:
            raise ValueError("lane filenames are duplicate or not canonical-sorted")
        previous = name
        if not isinstance(row["bytes"], int) or isinstance(row["bytes"], bool) or row["bytes"] <= 0:
            raise ValueError(f"lane file {number} byte count is invalid")
        _require_identity(row["sha256"], _SHA256_RE, f"lane file {number} SHA-256")
        normalized.append(copy.deepcopy(row))
    return normalized


def derive_approved_candidate_manifest(
    approved_manifest: Path | str | bytes,
    *,
    authoritative_source_index: Path | str | Sequence[Any],
) -> dict[str, Any]:
    """Derive approval authority only from independently read pinned bytes."""
    if isinstance(approved_manifest, bytes):
        raw = bytes(approved_manifest)
        context = "approved candidate manifest bytes"
    else:
        path = Path(approved_manifest)
        if path.is_symlink() or not path.is_file():
            raise ValueError("approved candidate manifest is missing or unsafe")
        raw = path.read_bytes()
        context = str(path)
    if not raw:
        raise ValueError("approved candidate manifest is empty")
    lane_authority = derive_authoritative_lane_semantics(authoritative_source_index)
    cache_key = (raw, lane_authority["authority_sha256"])
    cached = _APPROVED_AUTHORITY_CACHE.get(cache_key)
    if cached is not None:
        return copy.deepcopy(cached)
    try:
        document = _strict_json(raw.decode("utf-8"), context)
    except UnicodeDecodeError as error:
        raise ValueError("approved candidate manifest is not UTF-8") from error
    if raw != _canonical_bytes(document):
        raise ValueError("approved candidate manifest bytes are not canonical JSON")
    if (
        not isinstance(document, dict)
        or set(document) != {"schema", "lanes"}
        or document["schema"] != APPROVED_CANDIDATE_MANIFEST_SCHEMA
        or not isinstance(document["lanes"], list)
        or len(document["lanes"]) != len(EXPECTED_LANES)
    ):
        raise ValueError("approved candidate manifest schema/population is not exact")
    normalized_lanes: dict[str, dict[str, Any]] = {}
    input_population: list[dict[str, Any]] = []
    geometry_population: list[dict[str, Any]] = []
    observed_lanes: list[str] = []
    for number, row in enumerate(document["lanes"], start=1):
        if not isinstance(row, dict) or set(row) != {
            "lane", "url", "bytes", "sha256", "generated_geometry"
        }:
            raise ValueError(f"approved candidate lane {number} schema is not exact")
        lane = row["lane"]
        if not isinstance(lane, str) or lane not in EXPECTED_LANES:
            raise ValueError(f"approved candidate lane {number} is unknown")
        observed_lanes.append(lane)
        if (
            not isinstance(row["url"], str)
            or not row["url"].startswith("https://")
            or any(ord(character) < 32 for character in row["url"])
            or not isinstance(row["bytes"], int)
            or isinstance(row["bytes"], bool)
            or row["bytes"] <= 0
        ):
            raise ValueError(f"approved candidate lane {lane} input identity is invalid")
        _require_identity(row["sha256"], _SHA256_RE, f"approved lane {lane} input")
        generated = _validate_file_rows(row["generated_geometry"], require_nonempty=True)
        semantics = _lane_authority(lane_authority, lane)
        if [Path(file_row["name"]).stem for file_row in generated] != semantics["area_ids"]:
            raise ValueError(
                f"approved lane {lane} generated geometry differs from pinned source population"
            )
        normalized = {
            "lane": lane,
            "url": row["url"],
            "bytes": row["bytes"],
            "sha256": row["sha256"],
            "generated_geometry": generated,
        }
        normalized_lanes[lane] = normalized
        input_population.append({
            "lane": lane,
            "url": row["url"],
            "bytes": row["bytes"],
            "sha256": row["sha256"],
        })
        geometry_population.append({"lane": lane, "files": generated})
    if observed_lanes != list(EXPECTED_LANES) or len(set(observed_lanes)) != len(EXPECTED_LANES):
        raise ValueError("approved candidate lane set/order is not the exact 51-lane population")
    authority: dict[str, Any] = {
        "schema": APPROVED_CANDIDATE_AUTHORITY_SCHEMA,
        "approved_input_set_sha256": hashlib.sha256(raw).hexdigest(),
        "approved_input_population_sha256": canonical_sha256(input_population),
        "approved_geometry_population_sha256": canonical_sha256(geometry_population),
        "lanes": normalized_lanes,
    }
    authority["authority_sha256"] = canonical_sha256(authority)
    _APPROVED_AUTHORITY_CACHE[cache_key] = copy.deepcopy(authority)
    return copy.deepcopy(authority)


def _approved_lane(
    authority: Mapping[str, Any], lane: str
) -> dict[str, Any]:
    fields = {
        "schema", "approved_input_set_sha256",
        "approved_input_population_sha256", "approved_geometry_population_sha256",
        "lanes", "authority_sha256",
    }
    if (
        not isinstance(authority, dict)
        or set(authority) != fields
        or authority["schema"] != APPROVED_CANDIDATE_AUTHORITY_SCHEMA
        or not isinstance(authority["lanes"], dict)
        or set(authority["lanes"]) != set(EXPECTED_LANES)
        or authority["authority_sha256"]
        != canonical_sha256(authority, omit={"authority_sha256"})
    ):
        raise ValueError("approved candidate authority is not exact")
    for field in (
        "approved_input_set_sha256", "approved_input_population_sha256",
        "approved_geometry_population_sha256",
    ):
        _require_identity(authority[field], _SHA256_RE, field)
    lane_row = authority["lanes"].get(lane)
    if not isinstance(lane_row, dict):
        raise ValueError(f"approved candidate authority lacks lane {lane}")
    return copy.deepcopy(lane_row)


def derive_candidate_config(
    candidate_config: Path | str | bytes,
    *,
    approved_candidate_manifest: Path | str | bytes,
    authoritative_source_index: Path | str | Sequence[Any],
) -> dict[str, Any]:
    """Read and validate the exact root-approved transaction configuration."""
    if isinstance(candidate_config, bytes):
        raw = bytes(candidate_config)
        context = "candidate configuration bytes"
    else:
        path = Path(candidate_config)
        if path.is_symlink() or not path.is_file():
            raise ValueError("candidate configuration is missing or unsafe")
        raw = path.read_bytes()
        context = str(path)
    if not raw:
        raise ValueError("candidate configuration is empty")
    try:
        document = _strict_json(raw.decode("utf-8"), context)
    except UnicodeDecodeError as error:
        raise ValueError("candidate configuration is not UTF-8") from error
    if raw != _canonical_bytes(document):
        raise ValueError("candidate configuration bytes are not canonical JSON")
    fields = {
        "schema", "source_sha", "source_tree", "workflow_sha256", "elevation",
        "no_routes", "toolchain_manifest_sha256", "approved_input_set_sha256",
        "approved_input_population_sha256", "approved_geometry_population_sha256",
        "artifact_service_ref", "source_index_sha256", "lane_authority_sha256",
    }
    if (
        not isinstance(document, dict)
        or set(document) != fields
        or document["schema"] != CANDIDATE_CONFIG_SCHEMA
        or not isinstance(document["elevation"], bool)
        or not isinstance(document["no_routes"], bool)
    ):
        raise ValueError("candidate configuration schema is not exact")
    _require_identity(document["source_sha"], _SHA1_RE, "candidate source_sha")
    _require_identity(document["source_tree"], _SHA1_RE, "candidate source_tree")
    for field in (
        "workflow_sha256", "toolchain_manifest_sha256", "approved_input_set_sha256",
        "approved_input_population_sha256", "approved_geometry_population_sha256",
        "source_index_sha256", "lane_authority_sha256",
    ):
        _require_identity(document[field], _SHA256_RE, field)
    _require_identity(document["artifact_service_ref"], _REF_RE, "artifact_service_ref")
    approval_authority = derive_approved_candidate_manifest(
        approved_candidate_manifest,
        authoritative_source_index=authoritative_source_index,
    )
    source_authority = derive_authoritative_lane_semantics(authoritative_source_index)
    cache_key = (
        raw,
        approval_authority["authority_sha256"],
        source_authority["authority_sha256"],
    )
    cached = _CANDIDATE_CONFIG_CACHE.get(cache_key)
    if cached is not None:
        return copy.deepcopy(cached)
    if (
        document["approved_input_set_sha256"]
        != approval_authority["approved_input_set_sha256"]
        or document["approved_input_population_sha256"]
        != approval_authority["approved_input_population_sha256"]
        or document["approved_geometry_population_sha256"]
        != approval_authority["approved_geometry_population_sha256"]
        or document["source_index_sha256"] != source_authority["source_index_sha256"]
        or document["lane_authority_sha256"] != source_authority["authority_sha256"]
    ):
        raise ValueError("candidate configuration differs from pinned manifest/source bytes")
    result = copy.deepcopy(document)
    result["candidate_config_sha256"] = hashlib.sha256(raw).hexdigest()
    _CANDIDATE_CONFIG_CACHE[cache_key] = copy.deepcopy(result)
    return copy.deepcopy(result)


_CANDIDATE_CONFIG_AUTHORITY_FIELDS = frozenset({
    "schema", "source_sha", "source_tree", "workflow_sha256", "elevation",
    "no_routes", "toolchain_manifest_sha256", "approved_input_set_sha256",
    "approved_input_population_sha256", "approved_geometry_population_sha256",
    "artifact_service_ref", "source_index_sha256", "lane_authority_sha256",
    "candidate_config_sha256",
})


def _root_approval_key_bytes(key: bytes | str | None) -> bytes:
    if isinstance(key, str):
        key = key.encode("utf-8")
    if not isinstance(key, bytes) or len(key) < 32:
        raise ValueError("root approval verification key is unavailable or too short")
    return bytes(key)


def _root_approval_token(value: Any, context: str) -> str:
    if not isinstance(value, str) or not _APPROVAL_TOKEN_RE.fullmatch(value):
        raise ValueError(f"{context} is invalid")
    return value


def _root_approval_bindings(
    config: Mapping[str, Any],
    *,
    root_approval_ref: str,
    run_id: str,
    run_attempt: int,
) -> dict[str, Any]:
    if not isinstance(config, dict) or set(config) != _CANDIDATE_CONFIG_AUTHORITY_FIELDS:
        raise ValueError("root approval candidate configuration is not exact")
    _require_identity(root_approval_ref, _REF_RE, "root approval ref")
    if root_approval_ref != "sha256:" + config["candidate_config_sha256"]:
        raise ValueError("root approval ref differs from exact candidate configuration")
    if not isinstance(run_id, str) or not _RUN_ID_RE.fullmatch(run_id):
        raise ValueError("root approval run ID is invalid")
    if (
        not isinstance(run_attempt, int)
        or isinstance(run_attempt, bool)
        or run_attempt < 1
    ):
        raise ValueError("root approval run attempt is invalid")
    return {
        "root_approval_ref": root_approval_ref,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "candidate_config": copy.deepcopy(config),
    }


def _root_approval_document(assertion: Any) -> dict[str, Any]:
    if isinstance(assertion, bytes):
        try:
            text = assertion.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("root approval assertion is not UTF-8") from error
        document = _strict_json(text, "root approval assertion")
    elif isinstance(assertion, str):
        if not assertion:
            raise ValueError("root approval assertion is unavailable")
        document = _strict_json(assertion, "root approval assertion")
    elif isinstance(assertion, Mapping):
        document = copy.deepcopy(dict(assertion))
    else:
        raise ValueError("root approval assertion is unavailable or malformed")
    if not isinstance(document, dict):
        raise ValueError("root approval assertion is not an object")
    return document


def make_root_approval_assertion(
    candidate_config: Path | str | bytes,
    *,
    approved_candidate_manifest: Path | str | bytes,
    authoritative_source_index: Path | str | Sequence[Any],
    root_approval_ref: str,
    run_id: str,
    run_attempt: int,
    key: bytes | str,
    authority_key_id: str,
    authorization_nonce: str,
) -> dict[str, Any]:
    """Create a synthetic assertion for offline tests or a trusted signer."""
    config = derive_candidate_config(
        candidate_config,
        approved_candidate_manifest=approved_candidate_manifest,
        authoritative_source_index=authoritative_source_index,
    )
    assertion: dict[str, Any] = {
        "schema": ROOT_APPROVAL_ASSERTION_SCHEMA,
        "contract_version": CONTRACT_VERSION,
        "operation": ROOT_APPROVAL_OPERATION,
        "authority_key_id": _root_approval_token(
            authority_key_id, "root approval authority key ID"
        ),
        "authorization_nonce": _root_approval_token(
            authorization_nonce, "root approval authorization nonce"
        ),
        **_root_approval_bindings(
            config,
            root_approval_ref=root_approval_ref,
            run_id=run_id,
            run_attempt=run_attempt,
        ),
    }
    assertion["signature_hmac_sha256"] = hmac.new(
        _root_approval_key_bytes(key), _canonical_bytes(assertion), hashlib.sha256
    ).hexdigest()
    return assertion


def validate_root_approval_assertion(
    assertion: Any,
    candidate_config: Path | str | bytes,
    *,
    approved_candidate_manifest: Path | str | bytes,
    authoritative_source_index: Path | str | Sequence[Any],
    root_approval_ref: str,
    run_id: str,
    run_attempt: int,
    key: bytes | str | None,
    expected_authority_key_id: str | None,
) -> str:
    """Authenticate one exact config for one trusted workflow run/attempt."""
    document = _root_approval_document(assertion)
    fields = {
        "schema", "contract_version", "operation", "authority_key_id",
        "authorization_nonce", "root_approval_ref", "run_id", "run_attempt",
        "candidate_config", "signature_hmac_sha256",
    }
    if set(document) != fields:
        raise ValueError("root approval assertion schema is not exact")
    if (
        document["schema"] != ROOT_APPROVAL_ASSERTION_SCHEMA
        or document["contract_version"] != CONTRACT_VERSION
        or document["operation"] != ROOT_APPROVAL_OPERATION
    ):
        raise ValueError("root approval assertion identity mismatch")
    expected_key_id = _root_approval_token(
        expected_authority_key_id, "expected root approval authority key ID"
    )
    if document["authority_key_id"] != expected_key_id:
        raise ValueError("root approval assertion authority key ID mismatch")
    _root_approval_token(
        document["authorization_nonce"], "root approval authorization nonce"
    )
    config = derive_candidate_config(
        candidate_config,
        approved_candidate_manifest=approved_candidate_manifest,
        authoritative_source_index=authoritative_source_index,
    )
    expected_bindings = _root_approval_bindings(
        config,
        root_approval_ref=root_approval_ref,
        run_id=run_id,
        run_attempt=run_attempt,
    )
    for field, expected in expected_bindings.items():
        if document[field] != expected:
            raise ValueError(f"root approval assertion has wrong {field}")
    signature = _require_identity(
        document["signature_hmac_sha256"], _SHA256_RE, "root approval signature"
    )
    unsigned = copy.deepcopy(document)
    unsigned.pop("signature_hmac_sha256")
    expected_signature = hmac.new(
        _root_approval_key_bytes(key), _canonical_bytes(unsigned), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature, expected_signature):
        raise ValueError("root approval assertion signature mismatch")
    return canonical_sha256(document)


def validate_root_approval_from_environment(
    candidate_config: Path | str | bytes,
    *,
    approved_candidate_manifest: Path | str | bytes,
    authoritative_source_index: Path | str | Sequence[Any],
    root_approval_ref: str,
    run_id: str,
    run_attempt: int,
) -> str:
    """Read verifier material only from non-dispatch workflow authority."""
    assertion = os.environ.get(ROOT_APPROVAL_ASSERTION_ENV)
    key = os.environ.get(ROOT_APPROVAL_KEY_ENV)
    key_id = os.environ.get(ROOT_APPROVAL_KEY_ID_ENV)
    if not assertion or not key or not key_id:
        raise ValueError("root approval repository authority is unavailable")
    return validate_root_approval_assertion(
        assertion,
        candidate_config,
        approved_candidate_manifest=approved_candidate_manifest,
        authoritative_source_index=authoritative_source_index,
        root_approval_ref=root_approval_ref,
        run_id=run_id,
        run_attempt=run_attempt,
        key=key,
        expected_authority_key_id=key_id,
    )


def lane_terminal(
    *,
    lane: str,
    outcome: LaneOutcome,
    source_sha: str,
    source_tree: str,
    workflow_sha256: str,
    root_approval_ref: str,
    run_id: str,
    run_attempt: int,
    candidate_config: Path | str | bytes,
    input_manifest_sha256: str,
    approved_candidate_manifest: Path | str | bytes,
    authoritative_source_index: Path | str | Sequence[Any],
    matrix_job_ref: str,
    retry_lineage_ref: str,
    artifact_id: str,
    artifact_payload_sha256: str,
    inherited_receipt_sha256: str,
    publisher_log_sha256: str,
    files: Sequence[dict[str, Any]],
    reason: str | None,
) -> dict[str, Any]:
    if not isinstance(outcome, LaneOutcome):
        raise ValueError("outcome must be a LaneOutcome")
    lane_authority = derive_authoritative_lane_semantics(authoritative_source_index)
    semantics = _lane_authority(lane_authority, lane)
    approval_authority = derive_approved_candidate_manifest(
        approved_candidate_manifest,
        authoritative_source_index=authoritative_source_index,
    )
    config = derive_candidate_config(
        candidate_config,
        approved_candidate_manifest=approved_candidate_manifest,
        authoritative_source_index=authoritative_source_index,
    )
    if (
        config["source_sha"] != source_sha
        or config["source_tree"] != source_tree
        or config["workflow_sha256"] != workflow_sha256
        or root_approval_ref != "sha256:" + config["candidate_config_sha256"]
    ):
        raise ValueError("lane identity differs from exact root-approved configuration")
    receipt: dict[str, Any] = {
        "schema": LANE_SCHEMA,
        "contract_version": CONTRACT_VERSION,
        "lane": lane,
        "display_state": semantics["display_state"],
        "outcome": outcome.value,
        "source_sha": source_sha,
        "source_tree": source_tree,
        "workflow_sha256": workflow_sha256,
        "root_approval_ref": root_approval_ref,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "candidate_config_sha256": config["candidate_config_sha256"],
        "toolchain_manifest_sha256": config["toolchain_manifest_sha256"],
        "input_manifest_sha256": input_manifest_sha256,
        "approved_input_set_sha256": approval_authority["approved_input_set_sha256"],
        "approved_input_population_sha256": approval_authority["approved_input_population_sha256"],
        "approved_geometry_population_sha256": approval_authority["approved_geometry_population_sha256"],
        "source_index_sha256": lane_authority["source_index_sha256"],
        "authority_sha256": lane_authority["authority_sha256"],
        "artifact_service_ref": config["artifact_service_ref"],
        "matrix_job_ref": matrix_job_ref,
        "retry_lineage_ref": retry_lineage_ref,
        "artifact_id": artifact_id,
        "artifact_payload_sha256": artifact_payload_sha256,
        "inherited_receipt_sha256": inherited_receipt_sha256,
        "publisher_log_sha256": publisher_log_sha256,
        "expected_area_count": semantics["area_count"],
        "expected_area_ids_sha256": semantics["area_ids_sha256"],
        "files": sorted((copy.deepcopy(row) for row in files), key=lambda row: row["name"]),
        "reason": reason,
        "promotable": False,
        "committed": False,
        "pushed": False,
        "synchronized": False,
        "provider_effect": False,
    }
    receipt["receipt_sha256"] = canonical_sha256(receipt)
    return validate_lane_terminal(
        receipt,
        candidate_config=candidate_config,
        approved_candidate_manifest=approved_candidate_manifest,
        authoritative_source_index=authoritative_source_index,
    )


def validate_lane_terminal(
    receipt: Any,
    *,
    candidate_config: Path | str | bytes,
    approved_candidate_manifest: Path | str | bytes,
    authoritative_source_index: Path | str | Sequence[Any],
    expected_lane: str | None = None,
    source_sha: str | None = None,
    source_tree: str | None = None,
    workflow_sha256: str | None = None,
    root_approval_ref: str | None = None,
    expected_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(receipt, dict):
        raise TypeError("lane terminal is not an object")
    fields = {
        "schema", "contract_version", "lane", "display_state", "outcome", "source_sha",
        "source_tree", "workflow_sha256", "root_approval_ref", "run_id",
        "run_attempt", "candidate_config_sha256", "toolchain_manifest_sha256",
        "input_manifest_sha256", "approved_input_set_sha256",
        "approved_input_population_sha256", "approved_geometry_population_sha256",
        "source_index_sha256", "authority_sha256", "artifact_service_ref",
        "matrix_job_ref", "retry_lineage_ref", "artifact_id",
        "artifact_payload_sha256", "inherited_receipt_sha256",
        "publisher_log_sha256", "expected_area_count", "expected_area_ids_sha256",
        "files", "reason", "promotable", "committed", "pushed", "synchronized",
        "provider_effect", "receipt_sha256",
    }
    if set(receipt) != fields:
        raise ValueError("lane terminal schema is not exact")
    if receipt["schema"] != LANE_SCHEMA or receipt["contract_version"] != CONTRACT_VERSION:
        raise ValueError("lane terminal version mismatch")
    lane = receipt["lane"]
    if not isinstance(lane, str) or not _LANE_RE.fullmatch(lane) or lane not in EXPECTED_LANES:
        raise ValueError("lane terminal names an unknown lane")
    if expected_lane is not None and lane != expected_lane:
        raise ValueError("lane terminal is in the wrong artifact slot")
    display_state = receipt["display_state"]
    if (
        not isinstance(display_state, str)
        or not display_state
        or display_state != display_state.strip()
        or any(ord(character) < 32 for character in display_state)
    ):
        raise ValueError("lane terminal display state is invalid")
    try:
        outcome = LaneOutcome(receipt["outcome"])
    except (TypeError, ValueError) as error:
        raise ValueError("lane terminal outcome is invalid") from error
    _require_identity(receipt["source_sha"], _SHA1_RE, "source_sha")
    _require_identity(receipt["source_tree"], _SHA1_RE, "source_tree")
    _require_identity(receipt["workflow_sha256"], _SHA256_RE, "workflow_sha256")
    _require_identity(receipt["root_approval_ref"], _REF_RE, "root_approval_ref")
    if receipt["root_approval_ref"] != "sha256:" + receipt["candidate_config_sha256"]:
        raise ValueError("root approval does not bind the exact candidate configuration")
    if not isinstance(receipt["run_id"], str) or not _RUN_ID_RE.fullmatch(receipt["run_id"]):
        raise ValueError("lane run ID is invalid")
    if (
        not isinstance(receipt["run_attempt"], int)
        or isinstance(receipt["run_attempt"], bool)
        or receipt["run_attempt"] < 1
    ):
        raise ValueError("lane run attempt is invalid")
    for field in (
        "candidate_config_sha256", "toolchain_manifest_sha256", "input_manifest_sha256",
        "approved_input_set_sha256", "approved_input_population_sha256",
        "approved_geometry_population_sha256", "source_index_sha256",
        "authority_sha256", "matrix_job_ref", "retry_lineage_ref",
        "artifact_payload_sha256", "inherited_receipt_sha256", "publisher_log_sha256",
    ):
        _require_identity(receipt[field], _SHA256_RE, field)
    _require_identity(receipt["artifact_service_ref"], _REF_RE, "artifact_service_ref")
    expected_artifact_id = f"lane-evidence-{lane}:{receipt['run_id']}:{receipt['run_attempt']}"
    if receipt["artifact_id"] != expected_artifact_id:
        raise ValueError("lane artifact identity is not exact for its run/attempt/slot")
    direct_expected = {
        "source_sha": source_sha,
        "source_tree": source_tree,
        "workflow_sha256": workflow_sha256,
        "root_approval_ref": root_approval_ref,
    }
    for field, value in direct_expected.items():
        if value is not None and receipt[field] != value:
            raise ValueError(f"lane {field} identity mismatch")

    lane_authority = derive_authoritative_lane_semantics(authoritative_source_index)
    semantics = _lane_authority(lane_authority, lane)
    approval_authority = derive_approved_candidate_manifest(
        approved_candidate_manifest,
        authoritative_source_index=authoritative_source_index,
    )
    config = derive_candidate_config(
        candidate_config,
        approved_candidate_manifest=approved_candidate_manifest,
        authoritative_source_index=authoritative_source_index,
    )
    approved_lane = _approved_lane(approval_authority, lane)
    immutable_expected = {
        "source_sha": config["source_sha"],
        "source_tree": config["source_tree"],
        "workflow_sha256": config["workflow_sha256"],
        "candidate_config_sha256": config["candidate_config_sha256"],
        "toolchain_manifest_sha256": config["toolchain_manifest_sha256"],
        "artifact_service_ref": config["artifact_service_ref"],
        "root_approval_ref": "sha256:" + config["candidate_config_sha256"],
        "approved_input_set_sha256": approval_authority["approved_input_set_sha256"],
        "approved_input_population_sha256": approval_authority["approved_input_population_sha256"],
        "approved_geometry_population_sha256": approval_authority["approved_geometry_population_sha256"],
        "source_index_sha256": lane_authority["source_index_sha256"],
        "authority_sha256": lane_authority["authority_sha256"],
        "display_state": semantics["display_state"],
        "expected_area_count": semantics["area_count"],
        "expected_area_ids_sha256": semantics["area_ids_sha256"],
    }
    for field, expected in immutable_expected.items():
        if receipt[field] != expected:
            raise ValueError(
                "lane terminal differs from pinned approval/source/geometry authority"
            )
    if expected_identity is not None:
        required_identity = {
            "run_id", "run_attempt", "candidate_config_sha256",
            "toolchain_manifest_sha256", "approved_input_set_sha256",
            "approved_input_population_sha256", "approved_geometry_population_sha256",
            "artifact_service_ref", "retry_lineage_ref",
        }
        if set(expected_identity) != required_identity:
            raise ValueError("expected candidate identity schema is not exact")
        for field in required_identity:
            if receipt[field] != expected_identity[field]:
                raise ValueError(f"lane {field} differs from expected candidate identity")
    if (
        not isinstance(receipt["expected_area_count"], int)
        or isinstance(receipt["expected_area_count"], bool)
        or receipt["expected_area_count"] < 0
    ):
        raise ValueError("lane expected area count is invalid")
    _require_identity(receipt["expected_area_ids_sha256"], _SHA256_RE, "expected area set")
    files = _validate_file_rows(receipt["files"], require_nonempty=outcome is LaneOutcome.SUCCESS)
    if outcome is LaneOutcome.SUCCESS:
        area_ids = [Path(row["name"]).stem for row in files]
        area_ids_digest = hashlib.sha256(("\n".join(area_ids) + "\n").encode()).hexdigest()
        if (
            receipt["expected_area_count"] != len(files)
            or receipt["expected_area_ids_sha256"] != area_ids_digest
            or area_ids != semantics["area_ids"]
            or files != approved_lane["generated_geometry"]
        ):
            raise ValueError(
                "successful lane geometry differs from root-approved pinned bytes"
            )
    if outcome is not LaneOutcome.SUCCESS and files:
        raise ValueError("adverse lane terminal cannot carry candidate files")
    if outcome is LaneOutcome.SUCCESS and receipt["reason"] is not None:
        raise ValueError("successful lane terminal cannot carry an adverse reason")
    if outcome is not LaneOutcome.SUCCESS and (
        not isinstance(receipt["reason"], str) or not receipt["reason"].strip()
    ):
        raise ValueError("adverse lane terminal lacks a typed reason")
    for field in ("promotable", "committed", "pushed", "synchronized", "provider_effect"):
        if receipt[field] is not False:
            raise ValueError(f"lane terminal illegally sets {field}")
    digest = _require_identity(receipt["receipt_sha256"], _SHA256_RE, "lane receipt")
    if digest != canonical_sha256(receipt, omit={"receipt_sha256"}):
        raise ValueError("lane terminal digest mismatch")
    normalized = copy.deepcopy(receipt)
    normalized["files"] = files
    return normalized


def _synthetic_lane(
    lane: str,
    outcome: LaneOutcome,
    reason: str,
    *,
    source_sha: str,
    source_tree: str,
    workflow_sha256: str,
    root_approval_ref: str,
    expected_identity: Mapping[str, Any],
    candidate_config: Path | str | bytes,
    approved_candidate_manifest: Path | str | bytes,
    authoritative_source_index: Path | str | Sequence[Any],
) -> dict[str, Any]:
    empty = hashlib.sha256(b"").hexdigest()
    return lane_terminal(
        lane=lane,
        outcome=outcome,
        source_sha=source_sha,
        source_tree=source_tree,
        workflow_sha256=workflow_sha256,
        root_approval_ref=root_approval_ref,
        run_id=expected_identity["run_id"],
        run_attempt=expected_identity["run_attempt"],
        candidate_config=candidate_config,
        input_manifest_sha256=empty,
        approved_candidate_manifest=approved_candidate_manifest,
        authoritative_source_index=authoritative_source_index,
        matrix_job_ref=hashlib.sha256(
            f"{expected_identity['run_id']}:{expected_identity['run_attempt']}:{lane}".encode()
        ).hexdigest(),
        retry_lineage_ref=expected_identity["retry_lineage_ref"],
        artifact_id=f"lane-evidence-{lane}:{expected_identity['run_id']}:{expected_identity['run_attempt']}",
        artifact_payload_sha256=empty,
        inherited_receipt_sha256=empty,
        publisher_log_sha256=empty,
        files=[],
        reason=reason,
    )


def _classify_invalid(
    receipt: Any,
    *,
    source_sha: str,
    source_tree: str,
    workflow_sha256: str,
    root_approval_ref: str,
    expected_identity: Mapping[str, Any],
) -> LaneOutcome:
    if not isinstance(receipt, dict):
        return LaneOutcome.CORRUPT
    if receipt.get("source_sha") != source_sha:
        return LaneOutcome.STALE
    if (
        receipt.get("source_tree") != source_tree
        or receipt.get("workflow_sha256") != workflow_sha256
        or receipt.get("root_approval_ref") != root_approval_ref
        or any(receipt.get(field) != value for field, value in expected_identity.items())
    ):
        return LaneOutcome.IDENTITY_MISMATCH
    return LaneOutcome.CORRUPT


def reduce_lane_terminals(
    terminals: Iterable[dict[str, Any]],
    *,
    source_sha: str,
    source_tree: str,
    workflow_sha256: str,
    root_approval_ref: str,
    run_id: str,
    run_attempt: int,
    candidate_config: Path | str | bytes,
    approved_candidate_manifest: Path | str | bytes,
    retry_lineage_ref: str,
    authoritative_source_index: Path | str | Sequence[Any],
    workflow_outcome: LaneOutcome | str = LaneOutcome.SUCCESS,
    collector_observed: bool = True,
    observed_inputs: Sequence[dict[str, str]] | None = None,
) -> dict[str, Any]:
    _require_identity(source_sha, _SHA1_RE, "source_sha")
    _require_identity(source_tree, _SHA1_RE, "source_tree")
    _require_identity(workflow_sha256, _SHA256_RE, "workflow_sha256")
    _require_identity(root_approval_ref, _REF_RE, "root_approval_ref")
    authoritative_lane_semantics = derive_authoritative_lane_semantics(
        authoritative_source_index
    )
    approval_authority = derive_approved_candidate_manifest(
        approved_candidate_manifest,
        authoritative_source_index=authoritative_source_index,
    )
    config = derive_candidate_config(
        candidate_config,
        approved_candidate_manifest=approved_candidate_manifest,
        authoritative_source_index=authoritative_source_index,
    )
    if (
        config["source_sha"] != source_sha
        or config["source_tree"] != source_tree
        or config["workflow_sha256"] != workflow_sha256
        or root_approval_ref != "sha256:" + config["candidate_config_sha256"]
    ):
        raise ValueError("fan-in identity differs from exact root-approved configuration")
    expected_identity: dict[str, Any] = {
        "run_id": run_id,
        "run_attempt": run_attempt,
        "candidate_config_sha256": config["candidate_config_sha256"],
        "toolchain_manifest_sha256": config["toolchain_manifest_sha256"],
        "approved_input_set_sha256": approval_authority["approved_input_set_sha256"],
        "approved_input_population_sha256": approval_authority["approved_input_population_sha256"],
        "approved_geometry_population_sha256": approval_authority["approved_geometry_population_sha256"],
        "artifact_service_ref": config["artifact_service_ref"],
        "retry_lineage_ref": retry_lineage_ref,
    }
    try:
        workflow_outcome = LaneOutcome(workflow_outcome)
    except (TypeError, ValueError) as error:
        raise ValueError("workflow outcome is invalid") from error

    supplied = list(terminals)
    derived_inputs = [
        {
            "slot": (
                terminal.get("lane")
                if isinstance(terminal, dict) and isinstance(terminal.get("lane"), str)
                else "UNEXPECTED"
            ),
            "terminal_sha256": canonical_sha256(terminal),
        }
        for terminal in supplied
    ]
    derived_inputs.sort(key=lambda row: (row["slot"], row["terminal_sha256"]))
    if observed_inputs is None:
        raw_inputs = [
            {
                "slot": row["slot"],
                "raw_terminal_sha256": row["terminal_sha256"],
                "terminal_sha256": row["terminal_sha256"],
            }
            for row in derived_inputs
        ]
    else:
        raw_inputs = [copy.deepcopy(row) for row in observed_inputs]
    for row in raw_inputs:
        if (
            not isinstance(row, dict)
            or set(row) != {"slot", "raw_terminal_sha256", "terminal_sha256"}
            or not isinstance(row["slot"], str)
            or not isinstance(row["raw_terminal_sha256"], str)
            or not _SHA256_RE.fullmatch(row["raw_terminal_sha256"])
            or not isinstance(row["terminal_sha256"], str)
            or not _SHA256_RE.fullmatch(row["terminal_sha256"])
        ):
            raise ValueError("observed input raw/canonical identity schema is not exact")
    raw_inputs.sort(
        key=lambda row: (
            row["slot"], row["raw_terminal_sha256"], row["terminal_sha256"]
        )
    )
    observed_canonical = sorted(
        (
            {"slot": row["slot"], "terminal_sha256": row["terminal_sha256"]}
            for row in raw_inputs
        ),
        key=lambda row: (row["slot"], row["terminal_sha256"]),
    )
    if observed_canonical != derived_inputs:
        raise ValueError("observed canonical identities do not match supplied terminal values")

    grouped: dict[str, list[dict[str, Any]]] = {lane: [] for lane in EXPECTED_LANES}
    for terminal in supplied:
        lane = terminal.get("lane") if isinstance(terminal, dict) else None
        if lane in grouped:
            grouped[lane].append(terminal)
    unexpected = sorted(
        row["raw_terminal_sha256"]
        for row in raw_inputs
        if row["slot"] not in EXPECTED_LANES
    )
    slot_input_counts = {lane: len(grouped[lane]) for lane in EXPECTED_LANES}

    lanes: list[dict[str, Any]] = []
    for lane in EXPECTED_LANES:
        candidates = grouped[lane]
        if not candidates:
            missing_outcome = (
                LaneOutcome.CANCELLED if workflow_outcome is LaneOutcome.CANCELLED
                else LaneOutcome.SKIPPED if workflow_outcome is LaneOutcome.SKIPPED
                else LaneOutcome.MISSING
            )
            lanes.append(_synthetic_lane(
                lane, missing_outcome,
                f"no terminal artifact observed; orchestration={workflow_outcome.value}",
                source_sha=source_sha, source_tree=source_tree,
                workflow_sha256=workflow_sha256, root_approval_ref=root_approval_ref,
                expected_identity=expected_identity,
                candidate_config=candidate_config,
                approved_candidate_manifest=approved_candidate_manifest,
                authoritative_source_index=authoritative_source_index,
            ))
            continue
        if len(candidates) != 1:
            lanes.append(_synthetic_lane(
                lane, LaneOutcome.CORRUPT,
                f"duplicate terminal artifacts observed: {len(candidates)}",
                source_sha=source_sha, source_tree=source_tree,
                workflow_sha256=workflow_sha256, root_approval_ref=root_approval_ref,
                expected_identity=expected_identity,
                candidate_config=candidate_config,
                approved_candidate_manifest=approved_candidate_manifest,
                authoritative_source_index=authoritative_source_index,
            ))
            continue
        candidate = candidates[0]
        try:
            lanes.append(validate_lane_terminal(
                candidate,
                candidate_config=candidate_config,
                approved_candidate_manifest=approved_candidate_manifest,
                authoritative_source_index=authoritative_source_index,
                expected_lane=lane, source_sha=source_sha,
                source_tree=source_tree, workflow_sha256=workflow_sha256,
                root_approval_ref=root_approval_ref,
                expected_identity=expected_identity,
            ))
        except (TypeError, ValueError) as error:
            outcome = _classify_invalid(
                candidate, source_sha=source_sha, source_tree=source_tree,
                workflow_sha256=workflow_sha256, root_approval_ref=root_approval_ref,
                expected_identity=expected_identity,
            )
            lanes.append(_synthetic_lane(
                lane, outcome, f"invalid terminal artifact: {type(error).__name__}",
                source_sha=source_sha, source_tree=source_tree,
                workflow_sha256=workflow_sha256, root_approval_ref=root_approval_ref,
                expected_identity=expected_identity,
                candidate_config=candidate_config,
                approved_candidate_manifest=approved_candidate_manifest,
                authoritative_source_index=authoritative_source_index,
            ))

    success_count = sum(row["outcome"] == LaneOutcome.SUCCESS.value for row in lanes)
    canonical_by_lane = {
        lane: canonical_sha256(lanes[index])
        for index, lane in enumerate(EXPECTED_LANES)
    }
    linked_inputs = []
    for row in raw_inputs:
        linked = copy.deepcopy(row)
        if row["slot"] in canonical_by_lane:
            linked["terminal_sha256"] = canonical_by_lane[row["slot"]]
        linked_inputs.append(linked)
    linked_inputs.sort(
        key=lambda row: (
            row["slot"], row["raw_terminal_sha256"], row["terminal_sha256"]
        )
    )
    workflow_adverse_count = 0 if workflow_outcome is LaneOutcome.SUCCESS else 1
    adverse_count = len(lanes) - success_count + len(unexpected) + workflow_adverse_count
    hosted_pending = not collector_observed and workflow_outcome is LaneOutcome.CANCELLED
    if hosted_pending:
        state = FanInState.PENDING_HOSTED_CANCELLATION
    elif (
        workflow_outcome is LaneOutcome.SUCCESS
        and adverse_count == 0
        and success_count == len(EXPECTED_LANES)
    ):
        state = FanInState.COMPLETE_SUCCESS
    else:
        state = FanInState.NON_PROMOTABLE
    provider_result_transaction = [
        {
            "lane": lane["lane"],
            "artifact_service_ref": lane["artifact_service_ref"],
            "artifact_id": lane["artifact_id"],
            "artifact_payload_sha256": lane["artifact_payload_sha256"],
            "input_manifest_sha256": lane["input_manifest_sha256"],
            "inherited_receipt_sha256": lane["inherited_receipt_sha256"],
            "publisher_log_sha256": lane["publisher_log_sha256"],
            "files": copy.deepcopy(lane["files"]),
        }
        for lane in lanes
    ]
    receipt: dict[str, Any] = {
        "schema": FAN_IN_SCHEMA,
        "contract_version": CONTRACT_VERSION,
        "fan_in_state": state.value,
        "source_sha": source_sha,
        "source_tree": source_tree,
        "workflow_sha256": workflow_sha256,
        "root_approval_ref": root_approval_ref,
        "source_index_sha256": authoritative_lane_semantics["source_index_sha256"],
        "authority_sha256": authoritative_lane_semantics["authority_sha256"],
        **expected_identity,
        "required_lane_count": len(EXPECTED_LANES),
        "observed_input_count": len(linked_inputs),
        "observed_inputs": linked_inputs,
        "observed_inputs_sha256": canonical_sha256(linked_inputs),
        "slot_input_counts": slot_input_counts,
        "success_count": success_count,
        "workflow_adverse_count": workflow_adverse_count,
        "adverse_count": adverse_count,
        "lanes": lanes,
        "provider_result_transaction_sha256": canonical_sha256(
            provider_result_transaction
        ),
        "unexpected_input_sha256": unexpected,
        "workflow_outcome": workflow_outcome.value,
        "collector_observed": collector_observed,
        "hosted_cancellation_proof_pending": hosted_pending,
        "review_candidate_allowed": state is FanInState.COMPLETE_SUCCESS,
        "all_or_none": True,
        "committed": False,
        "pushed": False,
        "synchronized": False,
        "provider_effect": False,
    }
    receipt["receipt_sha256"] = canonical_sha256(receipt)
    return receipt


def _validate_fan_in_receipt(
    receipt: Any,
    *,
    authoritative_source_index: Path | str | Sequence[Any],
    candidate_config: Path | str | bytes,
    approved_candidate_manifest: Path | str | bytes,
) -> dict[str, Any]:
    if not isinstance(receipt, dict):
        raise ValueError("fan-in receipt is not an object")
    required = {
        "schema", "contract_version", "fan_in_state", "source_sha", "source_tree",
        "workflow_sha256", "root_approval_ref", "source_index_sha256", "authority_sha256",
        "run_id", "run_attempt",
        "candidate_config_sha256", "toolchain_manifest_sha256",
        "approved_input_set_sha256", "approved_input_population_sha256",
        "approved_geometry_population_sha256", "artifact_service_ref",
        "retry_lineage_ref", "required_lane_count", "observed_input_count",
        "observed_inputs", "observed_inputs_sha256", "slot_input_counts",
        "success_count", "workflow_adverse_count", "adverse_count", "lanes",
        "provider_result_transaction_sha256", "unexpected_input_sha256",
        "workflow_outcome", "collector_observed",
        "hosted_cancellation_proof_pending", "review_candidate_allowed", "all_or_none",
        "committed", "pushed", "synchronized", "provider_effect", "receipt_sha256",
    }
    if set(receipt) != required:
        raise ValueError("fan-in receipt schema is not exact")
    if receipt["schema"] != FAN_IN_SCHEMA or receipt["contract_version"] != CONTRACT_VERSION:
        raise ValueError("fan-in receipt version mismatch")
    digest = _require_identity(receipt["receipt_sha256"], _SHA256_RE, "fan-in receipt")
    if digest != canonical_sha256(receipt, omit={"receipt_sha256"}):
        raise ValueError("fan-in receipt digest mismatch")
    authoritative_lane_semantics = derive_authoritative_lane_semantics(
        authoritative_source_index
    )
    approval_authority = derive_approved_candidate_manifest(
        approved_candidate_manifest,
        authoritative_source_index=authoritative_source_index,
    )
    config = derive_candidate_config(
        candidate_config,
        approved_candidate_manifest=approved_candidate_manifest,
        authoritative_source_index=authoritative_source_index,
    )
    if (
        receipt["source_sha"] != config["source_sha"]
        or receipt["source_tree"] != config["source_tree"]
        or receipt["workflow_sha256"] != config["workflow_sha256"]
        or receipt["candidate_config_sha256"] != config["candidate_config_sha256"]
        or receipt["toolchain_manifest_sha256"] != config["toolchain_manifest_sha256"]
        or receipt["artifact_service_ref"] != config["artifact_service_ref"]
        or receipt["root_approval_ref"]
        != "sha256:" + config["candidate_config_sha256"]
        or receipt["source_index_sha256"]
        != authoritative_lane_semantics["source_index_sha256"]
        or receipt["authority_sha256"]
        != authoritative_lane_semantics["authority_sha256"]
        or receipt["approved_input_set_sha256"]
        != approval_authority["approved_input_set_sha256"]
        or receipt["approved_input_population_sha256"]
        != approval_authority["approved_input_population_sha256"]
        or receipt["approved_geometry_population_sha256"]
        != approval_authority["approved_geometry_population_sha256"]
    ):
        raise ValueError(
            "fan-in receipt differs from pinned approval/source/geometry authority"
        )
    expected_identity = {
        field: receipt[field]
        for field in (
            "run_id", "run_attempt", "candidate_config_sha256",
            "toolchain_manifest_sha256", "approved_input_set_sha256",
            "approved_input_population_sha256", "approved_geometry_population_sha256",
            "artifact_service_ref", "retry_lineage_ref",
        )
    }
    if receipt["root_approval_ref"] != "sha256:" + receipt["candidate_config_sha256"]:
        raise ValueError("fan-in approval/config identity mismatch")
    lanes = receipt["lanes"]
    if (
        receipt["required_lane_count"] != len(EXPECTED_LANES)
        or not isinstance(lanes, list)
        or len(lanes) != len(EXPECTED_LANES)
    ):
        raise ValueError("fan-in receipt is not a fixed 51-slot record")
    validated_lanes: list[dict[str, Any]] = []
    for index, lane in enumerate(EXPECTED_LANES):
        validated_lanes.append(validate_lane_terminal(
            lanes[index],
            candidate_config=candidate_config,
            approved_candidate_manifest=approved_candidate_manifest,
            authoritative_source_index=authoritative_source_index,
            expected_lane=lane,
            source_sha=receipt["source_sha"], source_tree=receipt["source_tree"],
            workflow_sha256=receipt["workflow_sha256"],
            root_approval_ref=receipt["root_approval_ref"],
            expected_identity=expected_identity,
        ))
    expected_provider_result_transaction = [
        {
            "lane": lane["lane"],
            "artifact_service_ref": lane["artifact_service_ref"],
            "artifact_id": lane["artifact_id"],
            "artifact_payload_sha256": lane["artifact_payload_sha256"],
            "input_manifest_sha256": lane["input_manifest_sha256"],
            "inherited_receipt_sha256": lane["inherited_receipt_sha256"],
            "publisher_log_sha256": lane["publisher_log_sha256"],
            "files": copy.deepcopy(lane["files"]),
        }
        for lane in validated_lanes
    ]
    if receipt["provider_result_transaction_sha256"] != canonical_sha256(
        expected_provider_result_transaction
    ):
        raise ValueError("fan-in provider/result transaction digest mismatch")
    try:
        workflow_outcome = LaneOutcome(receipt["workflow_outcome"])
        state = FanInState(receipt["fan_in_state"])
    except (TypeError, ValueError) as error:
        raise ValueError("fan-in workflow outcome or terminal state is invalid") from error
    if not isinstance(receipt["collector_observed"], bool):
        raise ValueError("fan-in collector-observed fact is not boolean")
    raw_inputs = receipt["observed_inputs"]
    if not isinstance(raw_inputs, list):
        raise ValueError("fan-in observed inputs are not an array")
    normalized_inputs: list[dict[str, str]] = []
    for row in raw_inputs:
        if (
            not isinstance(row, dict)
            or set(row) != {
                "slot", "raw_terminal_sha256", "terminal_sha256"
            }
            or not isinstance(row["slot"], str)
            or not isinstance(row["raw_terminal_sha256"], str)
            or not _SHA256_RE.fullmatch(row["raw_terminal_sha256"])
            or not isinstance(row["terminal_sha256"], str)
            or not _SHA256_RE.fullmatch(row["terminal_sha256"])
        ):
            raise ValueError("fan-in observed input identity is invalid")
        normalized_inputs.append(copy.deepcopy(row))
    if normalized_inputs != sorted(
        normalized_inputs,
        key=lambda row: (
            row["slot"], row["raw_terminal_sha256"], row["terminal_sha256"]
        ),
    ):
        raise ValueError("fan-in observed inputs are not canonical-sorted")
    if (
        receipt["observed_input_count"] != len(normalized_inputs)
        or receipt["observed_inputs_sha256"] != canonical_sha256(normalized_inputs)
    ):
        raise ValueError("fan-in observed input count/digest is not independently derived")
    slot_counts = receipt["slot_input_counts"]
    if not isinstance(slot_counts, dict) or set(slot_counts) != set(EXPECTED_LANES):
        raise ValueError("fan-in slot multiplicity schema is not exact")
    derived_counts = {
        lane: sum(row["slot"] == lane for row in normalized_inputs)
        for lane in EXPECTED_LANES
    }
    if slot_counts != derived_counts:
        raise ValueError("fan-in slot multiplicity differs from raw input identities")
    for index, lane in enumerate(EXPECTED_LANES):
        linked_hash = canonical_sha256(validated_lanes[index])
        linked_rows = [row for row in normalized_inputs if row["slot"] == lane]
        if any(row["terminal_sha256"] != linked_hash for row in linked_rows):
            raise ValueError(
                "fan-in observed input does not match its canonical lane terminal"
            )
    unexpected = receipt["unexpected_input_sha256"]
    unexpected_rows = [
        row for row in normalized_inputs if row["slot"] not in EXPECTED_LANES
    ]
    expected_unexpected = sorted(
        row["raw_terminal_sha256"] for row in unexpected_rows
    )
    if unexpected != expected_unexpected:
        raise ValueError("fan-in unexpected-input closure is inconsistent")
    for index, lane in enumerate(EXPECTED_LANES):
        multiplicity = slot_counts[lane]
        outcome = validated_lanes[index]["outcome"]
        if multiplicity == 0 and outcome not in {
            LaneOutcome.MISSING.value, LaneOutcome.CANCELLED.value, LaneOutcome.SKIPPED.value
        }:
            raise ValueError("fan-in empty slot does not carry synthesized adverse evidence")
        if multiplicity > 1 and outcome != LaneOutcome.CORRUPT.value:
            raise ValueError("fan-in duplicate slot is not corrupt")
    success_count = sum(row["outcome"] == LaneOutcome.SUCCESS.value for row in validated_lanes)
    workflow_adverse_count = 0 if workflow_outcome is LaneOutcome.SUCCESS else 1
    adverse_count = len(validated_lanes) - success_count + len(unexpected) + workflow_adverse_count
    hosted_pending = not receipt["collector_observed"] and workflow_outcome is LaneOutcome.CANCELLED
    expected_state = (
        FanInState.PENDING_HOSTED_CANCELLATION if hosted_pending
        else FanInState.COMPLETE_SUCCESS
        if (
            workflow_outcome is LaneOutcome.SUCCESS
            and adverse_count == 0
            and success_count == len(EXPECTED_LANES)
        )
        else FanInState.NON_PROMOTABLE
    )
    expected = {
        "success_count": success_count,
        "workflow_adverse_count": workflow_adverse_count,
        "adverse_count": adverse_count,
        "hosted_cancellation_proof_pending": hosted_pending,
        "review_candidate_allowed": expected_state is FanInState.COMPLETE_SUCCESS,
    }
    for field, value in expected.items():
        if receipt[field] != value:
            raise ValueError(f"fan-in receipt has inconsistent {field}")
    if state is not expected_state:
        raise ValueError("fan-in receipt terminal state is not independently derived")
    for field in ("committed", "pushed", "synchronized", "provider_effect"):
        if receipt[field] is not False:
            raise ValueError(f"fan-in receipt illegally sets {field}")
    if receipt["all_or_none"] is not True:
        raise ValueError("fan-in receipt weakens all-or-none behavior")
    normalized = copy.deepcopy(receipt)
    normalized["lanes"] = validated_lanes
    normalized["observed_inputs"] = normalized_inputs
    return normalized


def build_review_candidate(
    fan_in_receipt: dict[str, Any],
    builder: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    authoritative_source_index: Path | str | Sequence[Any],
    candidate_config: Path | str | bytes,
    approved_candidate_manifest: Path | str | bytes,
    approval_assertion: Any = None,
    approval_key: bytes | str | None = None,
    expected_authority_key_id: str | None = None,
) -> dict[str, Any]:
    receipt = _validate_fan_in_receipt(
        fan_in_receipt,
        authoritative_source_index=authoritative_source_index,
        candidate_config=candidate_config,
        approved_candidate_manifest=approved_candidate_manifest,
    )
    if (
        receipt["fan_in_state"] != FanInState.COMPLETE_SUCCESS.value
        or receipt["review_candidate_allowed"] is not True
        or receipt["success_count"] != len(EXPECTED_LANES)
        or receipt["adverse_count"] != 0
    ):
        raise RuntimeError("fan-in is non-promotable; partial candidate construction is forbidden")
    validate_root_approval_assertion(
        approval_assertion,
        candidate_config,
        approved_candidate_manifest=approved_candidate_manifest,
        authoritative_source_index=authoritative_source_index,
        root_approval_ref=receipt["root_approval_ref"],
        run_id=receipt["run_id"],
        run_attempt=receipt["run_attempt"],
        key=approval_key,
        expected_authority_key_id=expected_authority_key_id,
    )
    return builder(copy.deepcopy(receipt))


def validate_workflow_contract(text: str) -> list[str]:
    errors: list[str] = []
    required = (
        "continue-on-error: true",
        "if: ${{ always() }}",
        "lane-evidence-${{ matrix.state }}",
        "trailforge_candidate_contract.py",
        "fan-in-terminal.json",
        "review_candidate_allowed",
        "whole-us-candidate-",
        "GITHUB_RUN_ID",
        "GITHUB_RUN_ATTEMPT",
        "CANDIDATE_CONFIG_SHA256",
        "TOOLCHAIN_MANIFEST_SHA256",
        "ARTIFACT_SERVICE_REF",
        "RETRY_LINEAGE_REF",
        "APPROVED_INPUT_SET_SHA256",
        "approved_input_manifest_b64",
        "approved_toolchain_manifest_b64",
        "artifact_service_ref",
        "Authenticate root approval before any candidate work",
        "validate_root_approval_assertion",
        "TRAILFORGE_ROOT_APPROVAL_ASSERTION_JSON",
        "TRAILFORGE_ROOT_APPROVAL_HMAC_KEY",
        "TRAILFORGE_ROOT_APPROVAL_KEY_ID",
        "runtime toolchain differs from root-approved exact manifest",
        "fan-in tool bytes differ from root-approved manifest",
        "input-manifest.json",
        "derive_authoritative_lane_semantics",
        "derive_approved_candidate_manifest",
        "trekdex-trailforge-candidate-config-v3",
        '"source_index_sha256": authority["source_index_sha256"]',
        '"lane_authority_sha256": authority["authority_sha256"]',
        '"approved_input_population_sha256": approval_authority["approved_input_population_sha256"]',
        '"approved_geometry_population_sha256": approval_authority["approved_geometry_population_sha256"]',
        '"schema": "trekdex-whole-us-review-candidate-v3"',
        "provider_result_transaction_sha256",
        "--source-index public/areas/index.json",
        "--candidate-config",
        "--approved-candidate-manifest",
        "validate_success_artifact_against_fan_in",
        "candidate_contract.build_review_candidate",
        "lane-evidence-${{ matrix.state }}-${{ github.run_id }}-${{ github.run_attempt }}",
        "pattern: lane-evidence-*-${{ github.run_id }}-${{ github.run_attempt }}",
    )
    for marker in required:
        if marker not in text:
            errors.append(f"missing workflow contract marker: {marker}")
    approval_step = text.find("      - name: Authenticate root approval before any candidate work")
    install_step = text.find("      - name: Install osmium, pyosmium, shapely, and Pillow")
    build_step = text.find("      - name: Build exact state candidate")
    if not (0 <= approval_step < install_step < build_step):
        errors.append("root approval authority is not authenticated before candidate work")
    authority_assignments = {
        "TRAILFORGE_ROOT_APPROVAL_ASSERTION_JSON: ${{ secrets.TRAILFORGE_ROOT_APPROVAL_ASSERTION_JSON }}": 4,
        "TRAILFORGE_ROOT_APPROVAL_HMAC_KEY: ${{ secrets.TRAILFORGE_ROOT_APPROVAL_HMAC_KEY }}": 4,
        "TRAILFORGE_ROOT_APPROVAL_KEY_ID: ${{ vars.TRAILFORGE_ROOT_APPROVAL_KEY_ID }}": 4,
    }
    for assignment, expected_count in authority_assignments.items():
        if text.count(assignment) != expected_count:
            errors.append(
                f"root approval authority assignment count differs for {assignment}"
            )
    if text.count("--source-index public/areas/index.json") != 2:
        errors.append("record-lane and fan-in must both use the pinned source index")
    if text.count("--candidate-config") != 2:
        errors.append("record-lane and fan-in must both consume exact candidate config bytes")
    if text.count("--approved-candidate-manifest") != 2:
        errors.append("record-lane and fan-in must both consume exact approved manifest bytes")
    if text.count("trekdex-trailforge-candidate-config-v3") != 3:
        errors.append(
            "pre-work authority, lane, and fan-in must share the candidate config"
        )
    if text.count('"source_index_sha256": authority["source_index_sha256"]') != 3:
        errors.append("lane/fan-in configs and inherited receipt must bind source-index bytes")
    if text.count('"lane_authority_sha256": authority["authority_sha256"]') != 2:
        errors.append("both candidate config constructors must bind lane semantics")
    if text.count("authoritative_source_index=Path(\"public/areas/index.json\")") != 8:
        errors.append(
            "all pre-work/lane/fan-in/artifact/candidate authority checks must use the pinned source index"
        )
    matrix = re.search(
        r"(?ms)^\s{6}matrix:\s*\n\s{8}state:\s*\n(?P<body>(?:\s{10}- [a-z-]+\s*\n)+)",
        text,
    )
    observed_lanes = tuple(re.findall(r"(?m)^\s{10}- ([a-z-]+)\s*$", matrix.group("body"))) if matrix else ()
    if observed_lanes != EXPECTED_LANES:
        errors.append("workflow matrix is not the exact ordered 51-lane contract")
    success_gate = "if: ${{ steps.fan_in.outputs.review_candidate_allowed == 'true' }}"
    if text.count(success_gate) != 6:
        errors.append("every one of the six candidate-producing/verification steps must carry the all-success gate")
    if text.count("if: ${{ always() }}") != 3:
        errors.append("typed lane/fan-in evidence does not have the exact three always-run gates")
    if text.count("continue-on-error: true") != 1:
        errors.append("lane failure capture must have exactly one continue-on-error seam")
    if text.count("if: ${{ always() && !cancelled() }}") != 1:
        errors.append("fan-in evidence collector is not scheduled exactly once for adverse aggregate outcomes")
    for forbidden in ("git commit", "git push", "aws ", "rclone ", "wrangler "):
        if forbidden in text:
            errors.append(f"forbidden release/provider action: {forbidden}")
    if 'lane_receipt["schema"] != "trekdex-state-geom-lane-v2"' in text:
        errors.append("downstream lane verifier reintroduces the stale v2 schema authority")
    if "needs.publish.result == 'success'" in text:
        errors.append("fan-in remains success-only and loses adverse evidence")
    if "if: ${{ always() && !cancelled() }}" not in text:
        errors.append("fan-in evidence collector is not scheduled for adverse aggregate outcomes")
    return errors


def _file_record(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"candidate file is not regular: {path}")
    payload = path.read_bytes()
    if not payload:
        raise ValueError(f"candidate file is empty: {path}")
    return {"name": path.name, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}


def _write_new_json(path: Path, document: dict[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        raise ValueError(f"output already exists: {path}")
    if not path.parent.is_dir() or path.parent.is_symlink():
        raise ValueError(f"output parent is missing or unsafe: {path.parent}")
    path.write_bytes(_canonical_bytes(document))


def _command_record_lane(args: argparse.Namespace) -> int:
    expected_path = Path(args.expected)
    outcome = LaneOutcome(args.outcome)
    authoritative_lane_semantics = derive_authoritative_lane_semantics(
        args.source_index
    )
    semantics = _lane_authority(authoritative_lane_semantics, args.lane)
    approval_authority = derive_approved_candidate_manifest(
        args.approved_candidate_manifest,
        authoritative_source_index=args.source_index,
    )
    config = derive_candidate_config(
        args.candidate_config,
        approved_candidate_manifest=args.approved_candidate_manifest,
        authoritative_source_index=args.source_index,
    )
    if (
        config["source_sha"] != args.source_sha
        or config["source_tree"] != args.source_tree
        or config["workflow_sha256"] != args.workflow_sha256
        or args.root_approval_ref != "sha256:" + config["candidate_config_sha256"]
    ):
        raise ValueError("record-lane identity differs from root-approved config bytes")
    validate_root_approval_from_environment(
        args.candidate_config,
        approved_candidate_manifest=args.approved_candidate_manifest,
        authoritative_source_index=args.source_index,
        root_approval_ref=args.root_approval_ref,
        run_id=args.run_id,
        run_attempt=args.run_attempt,
    )
    approved_lane = _approved_lane(approval_authority, args.lane)
    area_ids = semantics["area_ids"]
    if expected_path.is_file() and not expected_path.is_symlink():
        expected = _strict_json(expected_path.read_text(), "expected lane set")
        if (
            not isinstance(expected, dict)
            or set(expected) != {
                "schema", "state", "area_count", "area_ids_sha256", "area_ids"
            }
            or expected["schema"] != "trekdex-pinned-state-area-set-v1"
            or expected["state"] != semantics["display_state"]
            or expected["area_count"] != semantics["area_count"]
            or expected["area_ids_sha256"] != semantics["area_ids_sha256"]
            or expected["area_ids"] != semantics["area_ids"]
        ):
            raise ValueError("expected lane set differs from pinned source-index authority")
    elif outcome is LaneOutcome.SUCCESS:
        raise ValueError("successful lane is missing its exact expected-area contract")
    files: list[dict[str, Any]] = []
    empty = hashlib.sha256(b"").hexdigest()
    artifact_payload_sha256 = empty
    inherited_receipt_sha256 = empty
    publisher_log_sha256 = empty
    input_manifest_sha256 = empty
    if outcome is LaneOutcome.SUCCESS:
        root = Path(args.geom_dir)
        artifact_root = root.parent
        input_path = Path(args.input_manifest)
        log_path = Path(args.publisher_log)
        inherited_path = Path(args.inherited_receipt)
        if root.is_symlink() or not root.is_dir():
            raise ValueError("successful lane has no safe geom directory")
        geom_entries = sorted(root.iterdir(), key=lambda path: path.name)
        expected_names = {area_id + ".json" for area_id in area_ids} | {
            "trekdex-lane-receipt.json"
        }
        if (
            {path.name for path in geom_entries} != expected_names
            or any(path.is_symlink() or not path.is_file() for path in geom_entries)
            or inherited_path != root / "trekdex-lane-receipt.json"
        ):
            raise ValueError("successful lane geom payload shape is not authoritative-exact")
        for path in geom_entries:
            if path.name == "trekdex-lane-receipt.json":
                continue
            document = _strict_json(path.read_text(), str(path))
            if (
                not isinstance(document, dict)
                or document.get("id") != path.stem
                or document.get("state") != semantics["display_state"]
                or "trail_count" not in document
                or "total_mi" not in document
            ):
                raise ValueError("successful lane geom id/state semantics are not authoritative")
            files.append(_file_record(path))
        if (
            [Path(row["name"]).stem for row in files] != area_ids
            or files != approved_lane["generated_geometry"]
        ):
            raise ValueError("successful lane geometry differs from root-approved exact bytes")
        for path in (expected_path, input_path, log_path, inherited_path):
            if path.is_symlink() or not path.is_file() or not path.read_bytes():
                raise ValueError(f"successful lane evidence is missing or unsafe: {path}")
        input_manifest = _strict_json(input_path.read_text(), str(input_path))
        if (
            not isinstance(input_manifest, dict)
            or set(input_manifest) != {
                "schema", "lane", "source_sha", "run_id", "run_attempt",
                "approved_input_set_sha256", "approved_input_population_sha256",
                "approved_geometry_population_sha256", "url", "bytes", "sha256",
            }
            or input_manifest["schema"] != "trekdex-trailforge-input-v2"
            or input_manifest["lane"] != args.lane
            or input_manifest["source_sha"] != args.source_sha
            or input_manifest["run_id"] != args.run_id
            or input_manifest["run_attempt"] != args.run_attempt
            or input_manifest["approved_input_set_sha256"]
            != approval_authority["approved_input_set_sha256"]
            or input_manifest["approved_input_population_sha256"]
            != approval_authority["approved_input_population_sha256"]
            or input_manifest["approved_geometry_population_sha256"]
            != approval_authority["approved_geometry_population_sha256"]
            or any(
                input_manifest[field] != approved_lane[field]
                for field in ("url", "bytes", "sha256")
            )
        ):
            raise ValueError("lane input manifest differs from root-approved pinned input")
        inherited = _strict_json(inherited_path.read_text(), str(inherited_path))
        if (
            not isinstance(inherited, dict)
            or set(inherited) != INHERITED_LANE_FIELDS
            or inherited["schema"] != INHERITED_LANE_SCHEMA
            or inherited["lane"] != args.lane
            or inherited["display_state"] != semantics["display_state"]
            or inherited["source_sha"] != config["source_sha"]
            or inherited["source_tree"] != config["source_tree"]
            or inherited["workflow_sha256"] != config["workflow_sha256"]
            or inherited["candidate_config_sha256"]
            != config["candidate_config_sha256"]
            or inherited["run_id"] != args.run_id
            or inherited["run_attempt"] != args.run_attempt
            or inherited["toolchain_manifest_sha256"]
            != config["toolchain_manifest_sha256"]
            or inherited["approved_input_set_sha256"]
            != approval_authority["approved_input_set_sha256"]
            or inherited["approved_input_population_sha256"]
            != approval_authority["approved_input_population_sha256"]
            or inherited["approved_geometry_population_sha256"]
            != approval_authority["approved_geometry_population_sha256"]
            or inherited["source_index_sha256"]
            != authoritative_lane_semantics["source_index_sha256"]
            or inherited["authority_sha256"]
            != authoritative_lane_semantics["authority_sha256"]
            or inherited["expected_area_count"] != semantics["area_count"]
            or inherited["expected_area_ids_sha256"] != semantics["area_ids_sha256"]
            or inherited["files"] != files
            or inherited["receipt_sha256"]
            != canonical_sha256(inherited, omit={"receipt_sha256"})
        ):
            raise ValueError(
                "inherited lane receipt differs from pinned approval/source authority"
            )
        input_manifest_sha256 = hashlib.sha256(input_path.read_bytes()).hexdigest()
        inherited_receipt_sha256 = hashlib.sha256(inherited_path.read_bytes()).hexdigest()
        publisher_log_sha256 = hashlib.sha256(log_path.read_bytes()).hexdigest()
        payload_paths = [expected_path, input_path, log_path, *sorted(root.iterdir())]
        framed = bytearray()
        for path in sorted(payload_paths, key=lambda item: item.relative_to(artifact_root).as_posix()):
            if path.is_symlink() or not path.is_file():
                raise ValueError("artifact payload contains a non-regular entry")
            relative = path.relative_to(artifact_root).as_posix().encode("utf-8")
            payload = path.read_bytes()
            framed.extend(len(relative).to_bytes(4, "big"))
            framed.extend(relative)
            framed.extend(len(payload).to_bytes(8, "big"))
            framed.extend(payload)
        artifact_payload_sha256 = hashlib.sha256(framed).hexdigest()
    terminal = lane_terminal(
        lane=args.lane,
        outcome=outcome,
        source_sha=args.source_sha,
        source_tree=args.source_tree,
        workflow_sha256=args.workflow_sha256,
        root_approval_ref=args.root_approval_ref,
        run_id=args.run_id,
        run_attempt=args.run_attempt,
        candidate_config=args.candidate_config,
        input_manifest_sha256=input_manifest_sha256,
        approved_candidate_manifest=args.approved_candidate_manifest,
        authoritative_source_index=args.source_index,
        matrix_job_ref=args.matrix_job_ref,
        retry_lineage_ref=args.retry_lineage_ref,
        artifact_id=f"lane-evidence-{args.lane}:{args.run_id}:{args.run_attempt}",
        artifact_payload_sha256=artifact_payload_sha256,
        inherited_receipt_sha256=inherited_receipt_sha256,
        publisher_log_sha256=publisher_log_sha256,
        files=files,
        reason=None if outcome is LaneOutcome.SUCCESS else args.reason,
    )
    _write_new_json(Path(args.output), terminal)
    return 0


def _corrupt_placeholder(
    lane: str,
    args: argparse.Namespace,
    expected_identity: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "lane": lane,
        "source_sha": args.source_sha,
        "source_tree": args.source_tree,
        "workflow_sha256": args.workflow_sha256,
        "root_approval_ref": args.root_approval_ref,
        **copy.deepcopy(expected_identity),
    }


def _validate_success_artifact(
    artifact: Path,
    terminal: dict[str, Any],
    *,
    expected_lane: str,
    authoritative_lane_semantics: Mapping[str, Any],
    approved_candidate_authority: Mapping[str, Any],
) -> None:
    expected_names = {
        "geom", "expected-state-areas.json", "input-manifest.json",
        "publisher.log", "lane-evidence",
    }
    children = {entry.name: entry for entry in artifact.iterdir()}
    if set(children) != expected_names or any(entry.is_symlink() for entry in children.values()):
        raise ValueError("successful lane artifact payload shape is not exact")
    geom = children["geom"]
    evidence = children["lane-evidence"]
    expected_path = children["expected-state-areas.json"]
    input_path = children["input-manifest.json"]
    log_path = children["publisher.log"]
    if (
        not geom.is_dir() or not evidence.is_dir()
        or not expected_path.is_file() or not input_path.is_file() or not log_path.is_file()
    ):
        raise ValueError("successful lane artifact contains an unsafe entry type")
    if {entry.name for entry in evidence.iterdir()} != {"lane-terminal.json"}:
        raise ValueError("successful lane evidence directory is not exact")
    geom_entries = sorted(geom.iterdir(), key=lambda item: item.name)
    expected_geom_names = {row["name"] for row in terminal["files"]} | {
        "trekdex-lane-receipt.json"
    }
    if (
        {entry.name for entry in geom_entries} != expected_geom_names
        or any(entry.is_symlink() or not entry.is_file() for entry in geom_entries)
    ):
        raise ValueError("successful lane geom directory is not exact")
    authoritative = _lane_authority(authoritative_lane_semantics, expected_lane)
    approved_lane = _approved_lane(approved_candidate_authority, expected_lane)
    actual_files: list[dict[str, Any]] = []
    for path in geom_entries:
        if path.name == "trekdex-lane-receipt.json":
            continue
        document = _strict_json(path.read_text(), str(path))
        if (
            not isinstance(document, dict)
            or document.get("id") != path.stem
            or document.get("state") != authoritative["display_state"]
            or "trail_count" not in document
            or "total_mi" not in document
        ):
            raise ValueError("successful lane geom id/state semantics are not authoritative")
        actual_files.append(_file_record(path))
    if (
        actual_files != terminal["files"]
        or actual_files != approved_lane["generated_geometry"]
    ):
        raise ValueError("successful lane geom bytes differ from root-approved terminal bytes")
    expected = _strict_json(expected_path.read_text(), str(expected_path))
    area_ids = [Path(row["name"]).stem for row in actual_files]
    area_ids_sha256 = hashlib.sha256(("\n".join(area_ids) + "\n").encode()).hexdigest()
    if (
        not isinstance(expected, dict)
        or set(expected) != {"schema", "state", "area_count", "area_ids_sha256", "area_ids"}
        or expected["schema"] != "trekdex-pinned-state-area-set-v1"
        or expected["state"] != authoritative["display_state"]
        or expected["area_ids"] != authoritative["area_ids"]
        or expected["area_count"] != authoritative["area_count"]
        or expected["area_ids_sha256"] != authoritative["area_ids_sha256"]
        or area_ids != authoritative["area_ids"]
        or area_ids_sha256 != authoritative["area_ids_sha256"]
        or terminal["display_state"] != authoritative["display_state"]
        or terminal["source_index_sha256"]
        != authoritative_lane_semantics["source_index_sha256"]
        or terminal["authority_sha256"]
        != authoritative_lane_semantics["authority_sha256"]
        or terminal["expected_area_count"] != authoritative["area_count"]
        or terminal["expected_area_ids_sha256"] != authoritative["area_ids_sha256"]
    ):
        raise ValueError("successful lane expected-area payload is not authoritative")
    input_manifest = _strict_json(input_path.read_text(), str(input_path))
    if (
        not isinstance(input_manifest, dict)
        or set(input_manifest) != {
            "schema", "lane", "source_sha", "run_id", "run_attempt",
            "approved_input_set_sha256", "approved_input_population_sha256",
            "approved_geometry_population_sha256", "url", "bytes", "sha256",
        }
        or input_manifest["schema"] != "trekdex-trailforge-input-v2"
        or input_manifest["lane"] != expected_lane
        or input_manifest["source_sha"] != terminal["source_sha"]
        or input_manifest["run_id"] != terminal["run_id"]
        or input_manifest["run_attempt"] != terminal["run_attempt"]
        or input_manifest["approved_input_set_sha256"]
        != approved_candidate_authority["approved_input_set_sha256"]
        or input_manifest["approved_input_population_sha256"]
        != approved_candidate_authority["approved_input_population_sha256"]
        or input_manifest["approved_geometry_population_sha256"]
        != approved_candidate_authority["approved_geometry_population_sha256"]
        or any(
            input_manifest[field] != approved_lane[field]
            for field in ("url", "bytes", "sha256")
        )
        or hashlib.sha256(input_path.read_bytes()).hexdigest()
        != terminal["input_manifest_sha256"]
    ):
        raise ValueError("lane input manifest differs from root-approved pinned input")
    inherited_path = geom / "trekdex-lane-receipt.json"
    inherited = _strict_json(inherited_path.read_text(), str(inherited_path))
    if not isinstance(inherited, dict) or set(inherited) != INHERITED_LANE_FIELDS:
        raise ValueError("inherited lane receipt schema is not exact")
    inherited_digest = inherited.get("receipt_sha256")
    if (
        inherited["schema"] != INHERITED_LANE_SCHEMA
        or inherited["lane"] != expected_lane
        or inherited["display_state"] != expected["state"]
        or inherited["source_sha"] != terminal["source_sha"]
        or inherited["source_tree"] != terminal["source_tree"]
        or inherited["workflow_sha256"] != terminal["workflow_sha256"]
        or inherited["candidate_config_sha256"] != terminal["candidate_config_sha256"]
        or inherited["run_id"] != terminal["run_id"]
        or inherited["run_attempt"] != terminal["run_attempt"]
        or inherited["toolchain_manifest_sha256"] != terminal["toolchain_manifest_sha256"]
        or inherited["input_manifest_sha256"] != terminal["input_manifest_sha256"]
        or inherited["approved_input_set_sha256"]
        != terminal["approved_input_set_sha256"]
        or inherited["approved_input_population_sha256"]
        != terminal["approved_input_population_sha256"]
        or inherited["approved_geometry_population_sha256"]
        != terminal["approved_geometry_population_sha256"]
        or inherited["source_index_sha256"] != terminal["source_index_sha256"]
        or inherited["authority_sha256"] != terminal["authority_sha256"]
        or inherited["expected_area_count"] != terminal["expected_area_count"]
        or inherited["expected_area_ids_sha256"] != terminal["expected_area_ids_sha256"]
        or inherited["files"] != terminal["files"]
        or not isinstance(inherited_digest, str)
        or inherited_digest != canonical_sha256(inherited, omit={"receipt_sha256"})
        or hashlib.sha256(inherited_path.read_bytes()).hexdigest() != terminal["inherited_receipt_sha256"]
    ):
        raise ValueError("inherited lane receipt identity/content is invalid")
    log_payload = log_path.read_bytes()
    if (
        not log_payload
        or b"FAILED validation" in log_payload
        or hashlib.sha256(log_payload).hexdigest() != terminal["publisher_log_sha256"]
    ):
        raise ValueError("publisher log is empty, adverse, or unbound")
    payload_rows: list[tuple[str, bytes]] = []
    for path in [expected_path, input_path, log_path, *geom_entries]:
        relative = path.relative_to(artifact).as_posix()
        payload_rows.append((relative, path.read_bytes()))
    framed = bytearray()
    for relative, payload in sorted(payload_rows):
        name = relative.encode("utf-8")
        framed.extend(len(name).to_bytes(4, "big"))
        framed.extend(name)
        framed.extend(len(payload).to_bytes(8, "big"))
        framed.extend(payload)
    if hashlib.sha256(framed).hexdigest() != terminal["artifact_payload_sha256"]:
        raise ValueError("complete artifact payload digest mismatch")


def validate_success_artifact_against_fan_in(
    artifact: Path | str,
    fan_in_receipt: Any,
    *,
    expected_lane: str,
    authoritative_source_index: Path | str | Sequence[Any],
    candidate_config: Path | str | bytes,
    approved_candidate_manifest: Path | str | bytes,
) -> dict[str, Any]:
    """Revalidate one all-success artifact through pinned v5 authority."""
    if expected_lane not in EXPECTED_LANES:
        raise ValueError("downstream artifact names an unexpected lane")
    authoritative_lane_semantics = derive_authoritative_lane_semantics(
        authoritative_source_index
    )
    approved_candidate_authority = derive_approved_candidate_manifest(
        approved_candidate_manifest,
        authoritative_source_index=authoritative_source_index,
    )
    receipt = _validate_fan_in_receipt(
        fan_in_receipt,
        authoritative_source_index=authoritative_source_index,
        candidate_config=candidate_config,
        approved_candidate_manifest=approved_candidate_manifest,
    )
    if (
        receipt["fan_in_state"] != FanInState.COMPLETE_SUCCESS.value
        or receipt["review_candidate_allowed"] is not True
        or receipt["success_count"] != len(EXPECTED_LANES)
        or receipt["adverse_count"] != 0
    ):
        raise ValueError("downstream artifact validation requires exact complete success")
    matches = [row for row in receipt["lanes"] if row["lane"] == expected_lane]
    if len(matches) != 1 or matches[0]["outcome"] != LaneOutcome.SUCCESS.value:
        raise ValueError("downstream artifact has no unique successful lane terminal")
    terminal = matches[0]
    _validate_success_artifact(
        Path(artifact), terminal, expected_lane=expected_lane,
        authoritative_lane_semantics=authoritative_lane_semantics,
        approved_candidate_authority=approved_candidate_authority,
    )
    return copy.deepcopy(terminal)


def _command_fan_in(args: argparse.Namespace) -> int:
    root = Path(args.artifacts_root)
    authoritative_lane_semantics = derive_authoritative_lane_semantics(
        args.source_index
    )
    approval_authority = derive_approved_candidate_manifest(
        args.approved_candidate_manifest,
        authoritative_source_index=args.source_index,
    )
    config = derive_candidate_config(
        args.candidate_config,
        approved_candidate_manifest=args.approved_candidate_manifest,
        authoritative_source_index=args.source_index,
    )
    if (
        config["source_sha"] != args.source_sha
        or config["source_tree"] != args.source_tree
        or config["workflow_sha256"] != args.workflow_sha256
        or args.root_approval_ref != "sha256:" + config["candidate_config_sha256"]
    ):
        raise ValueError("fan-in identity differs from root-approved config bytes")
    validate_root_approval_from_environment(
        args.candidate_config,
        approved_candidate_manifest=args.approved_candidate_manifest,
        authoritative_source_index=args.source_index,
        root_approval_ref=args.root_approval_ref,
        run_id=args.run_id,
        run_attempt=args.run_attempt,
    )
    terminals: list[dict[str, Any]] = []
    observed_inputs: list[dict[str, str]] = []
    expected_identity = {
        "run_id": args.run_id,
        "run_attempt": args.run_attempt,
        "candidate_config_sha256": config["candidate_config_sha256"],
        "toolchain_manifest_sha256": config["toolchain_manifest_sha256"],
        "approved_input_set_sha256": approval_authority["approved_input_set_sha256"],
        "approved_input_population_sha256": approval_authority["approved_input_population_sha256"],
        "approved_geometry_population_sha256": approval_authority["approved_geometry_population_sha256"],
        "artifact_service_ref": config["artifact_service_ref"],
        "retry_lineage_ref": args.retry_lineage_ref,
    }
    if root.is_symlink() or not root.is_dir():
        entries: list[Path] = []
    else:
        entries = sorted(root.iterdir(), key=lambda item: item.name)
    suffix = f"-{args.run_id}-{args.run_attempt}"
    for artifact in entries:
        lane = (
            artifact.name.removeprefix("lane-evidence-").removesuffix(suffix)
            if artifact.name.startswith("lane-evidence-") and artifact.name.endswith(suffix)
            else "UNEXPECTED-" + hashlib.sha256(artifact.name.encode()).hexdigest()[:16]
        )
        terminal: dict[str, Any]
        raw_terminal_sha256: str
        if artifact.is_symlink() or not artifact.is_dir():
            terminal = _corrupt_placeholder(lane, args, expected_identity)
            raw_terminal_sha256 = canonical_sha256(terminal)
        else:
            path = artifact / "lane-evidence" / "lane-terminal.json"
            if path.is_symlink() or not path.is_file():
                terminal = _corrupt_placeholder(lane, args, expected_identity)
                raw_terminal_sha256 = canonical_sha256(terminal)
            else:
                raw_terminal = path.read_bytes()
                raw_terminal_sha256 = hashlib.sha256(raw_terminal).hexdigest()
                try:
                    loaded = _strict_json(raw_terminal.decode("utf-8"), str(path))
                    validated = validate_lane_terminal(
                        loaded,
                        candidate_config=args.candidate_config,
                        approved_candidate_manifest=args.approved_candidate_manifest,
                        authoritative_source_index=args.source_index,
                        expected_lane=lane,
                        source_sha=args.source_sha,
                        source_tree=args.source_tree,
                        workflow_sha256=args.workflow_sha256,
                        root_approval_ref=args.root_approval_ref,
                        expected_identity=expected_identity,
                    )
                    if validated["outcome"] == LaneOutcome.SUCCESS.value:
                        _validate_success_artifact(
                            artifact,
                            validated,
                            expected_lane=lane,
                            authoritative_lane_semantics=authoritative_lane_semantics,
                            approved_candidate_authority=approval_authority,
                        )
                    terminal = loaded
                except Exception:
                    terminal = _corrupt_placeholder(lane, args, expected_identity)
        terminals.append(terminal)
        observed_inputs.append({
            "slot": lane,
            "raw_terminal_sha256": raw_terminal_sha256,
            "terminal_sha256": canonical_sha256(terminal),
        })
    receipt = reduce_lane_terminals(
        terminals,
        source_sha=args.source_sha,
        source_tree=args.source_tree,
        workflow_sha256=args.workflow_sha256,
        root_approval_ref=args.root_approval_ref,
        run_id=args.run_id,
        run_attempt=args.run_attempt,
        candidate_config=args.candidate_config,
        approved_candidate_manifest=args.approved_candidate_manifest,
        retry_lineage_ref=args.retry_lineage_ref,
        authoritative_source_index=args.source_index,
        workflow_outcome=args.workflow_outcome,
        collector_observed=True,
        observed_inputs=observed_inputs,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    _write_new_json(output, receipt)
    if args.github_output:
        with Path(args.github_output).open("a", encoding="utf-8", newline="") as stream:
            stream.write(
                "review_candidate_allowed="
                + ("true" if receipt["review_candidate_allowed"] else "false")
                + "\n"
            )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    lane = commands.add_parser("record-lane")
    lane.add_argument("--lane", required=True, choices=EXPECTED_LANES)
    lane.add_argument("--outcome", required=True, choices=[item.value for item in LaneOutcome])
    lane.add_argument("--source-sha", required=True)
    lane.add_argument("--source-tree", required=True)
    lane.add_argument("--workflow-sha256", required=True)
    lane.add_argument("--root-approval-ref", required=True)
    lane.add_argument("--run-id", required=True)
    lane.add_argument("--run-attempt", required=True, type=int)
    lane.add_argument("--candidate-config", required=True, type=Path)
    lane.add_argument("--approved-candidate-manifest", required=True, type=Path)
    lane.add_argument("--matrix-job-ref", required=True)
    lane.add_argument("--retry-lineage-ref", required=True)
    lane.add_argument("--input-manifest", required=True)
    lane.add_argument("--publisher-log", required=True)
    lane.add_argument("--inherited-receipt", required=True)
    lane.add_argument("--expected", required=True)
    lane.add_argument("--source-index", required=True, type=Path)
    lane.add_argument("--geom-dir", required=True)
    lane.add_argument("--reason")
    lane.add_argument("--output", required=True)
    fan_in = commands.add_parser("fan-in")
    fan_in.add_argument("--artifacts-root", required=True)
    fan_in.add_argument("--source-index", required=True, type=Path)
    fan_in.add_argument("--source-sha", required=True)
    fan_in.add_argument("--source-tree", required=True)
    fan_in.add_argument("--workflow-sha256", required=True)
    fan_in.add_argument("--root-approval-ref", required=True)
    fan_in.add_argument("--run-id", required=True)
    fan_in.add_argument("--run-attempt", required=True, type=int)
    fan_in.add_argument("--candidate-config", required=True, type=Path)
    fan_in.add_argument("--approved-candidate-manifest", required=True, type=Path)
    fan_in.add_argument("--retry-lineage-ref", required=True)
    fan_in.add_argument("--workflow-outcome", required=True, choices=[item.value for item in LaneOutcome])
    fan_in.add_argument("--output", required=True)
    fan_in.add_argument("--github-output")
    validate = commands.add_parser("validate-workflow")
    validate.add_argument("path", type=Path)
    args = parser.parse_args(argv)
    if args.command == "record-lane":
        return _command_record_lane(args)
    if args.command == "fan-in":
        return _command_fan_in(args)
    errors = validate_workflow_contract(args.path.read_text())
    if errors:
        raise SystemExit("\n".join(errors))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
