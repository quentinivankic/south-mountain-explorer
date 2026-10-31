#!/usr/bin/env python3
"""Versioned fail-closed contracts for Trekdex R2 release and quarantine.

This module is deliberately provider-agnostic. It owns the canonical key
classes, complete inventory receipt, reviewed conditional upload manifest,
Mols-scoped first-class rollback manifest, quarantine state machine, and typed
release terminals. Provider adapters are injected by command-line consumers
and replaced with fakes in CI.
"""
from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import hmac
import json
import math
import re
import secrets
import unicodedata
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

CONTRACT_VERSION = "trekdex-r2-release-contract-v5"
BUCKET = "trekdex-areas"
INVENTORY_SCHEMA = "trekdex-r2-complete-inventory-v3"
PRODUCER_POPULATION_SCHEMA = "trekdex-r2-producer-population-v1"
UPLOAD_MANIFEST_SCHEMA = "trekdex-r2-reviewed-upload-manifest-v5"
APPROVAL_ASSERTION_SCHEMA = "trekdex-r2-root-approval-assertion-v1"
PROVIDER_ATTESTATION_SCHEMA = "trekdex-r2-provider-identity-attestation-v1"
RELEASE_RECEIPT_SCHEMA = "trekdex-r2-release-terminal-v5"
RELEASE_PREFLIGHT_SCHEMA = "trekdex-r2-release-preflight-v1"
RELEASE_PROVIDER_RESULT_FIELDS = (
    "attempt_id", "bytes", "etag", "key", "sha256", "transaction_id",
)
SCOPED_PREFLIGHT_SCHEMA = "trekdex-r2-scoped-preflight-v2"
ROLLBACK_MANIFEST_SCHEMA = "trekdex-r2-rollback-manifest-v2"
SCOPED_RELEASE_RECEIPT_SCHEMA = "trekdex-r2-scoped-release-terminal-v3"
SCOPED_JOURNAL_AUTH_SCHEMA = "trekdex-r2-scoped-journal-auth-v1"
ROLLBACK_DELETE_RESULT_FIELDS = (
    "attempt_id", "key", "postimage_sha256", "transaction_id", "verified_absent",
)
MOLS_AREA_ID = "nationalpark-mols-bjerge-dk"
MOLS_AREA_NAME = "Nationalpark Mols Bjerge"
MOLS_EXTRACT = "europe/denmark"
MOLS_STATE = "Denmark"
MOLS_REGION_CODE = "dk"
MOLS_RELATION_ID = 7046785
MOLS_BBOX = "10.40,56.08,10.90,56.35"
MOLS_CENTER_LAT = 56.2
MOLS_CENTER_LON = 10.6
MOLS_RELEASE_INPUT_SCHEMA = "trekdex-mols-release-input-approval-v1"
MOLS_OBJECT_KEYS = (
    f"{MOLS_AREA_ID}.json",
    f"silhouettes/{MOLS_AREA_ID}.json",
)
MOLS_SEMANTIC_GLOBAL_KEYS = (
    "trail-search.json",
    "trail-shapes.json",
)
MOLS_MUTABLE_GLOBAL_KEYS = (
    "index.json",
    *MOLS_SEMANTIC_GLOBAL_KEYS,
)
MOLS_FORWARD_KEYS = (*MOLS_OBJECT_KEYS, *MOLS_MUTABLE_GLOBAL_KEYS)
MOLS_REPOSITORY_PATHS = (
    f"public/areas/geom/{MOLS_AREA_ID}.json",
    f"public/areas/silhouettes/{MOLS_AREA_ID}.json",
    "public/areas/index.json",
    "ios/SouthMountainExplorer/Resources/areas-index.json",
)
QUARANTINE_PLAN_SCHEMA = "trekdex-r2-quarantine-plan-v3"
QUARANTINE_RECEIPT_SCHEMA = "trekdex-r2-quarantine-terminal-v3"
CONTROL_PREFIX = "_trekdex-control/v2/"
QUARANTINE_PREFIX = CONTROL_PREFIX + "quarantine/"
MAX_INVENTORY_PAGES = 100_000
AWS_CLI_VERSION = "2.39.1"
AWS_CLI_ARCHIVE_SHA256 = "5b6c10e0a8ccdcf30c84bd643fea511f103d257a4787a7cef878691d63169804"
PROVIDER_TOOL_ATTESTATION_SCHEMA = "trekdex-r2-provider-tool-v1"

# Sorted order is normative and therefore also the protected-set framing.
GLOBAL_OBJECTS = (
    "index.json",
    "parking.json",
    "trail-search.json",
    "trail-shapes.json",
)

# Offline CI pins every source path that can create a released or review-candidate
# output but cannot safely execute in the no-network/no-provider job. The graph
# is compared in both directions with independently enumerated workflow
# invocation boundaries below. Any producer-only byte change therefore fails
# before output behavior can be reclassified.
CI_PRODUCER_GRAPH: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "trailforge/serve/publish_areas.py",
        "0e47a162dc54dab455e2e6f7233cf13c07e020604b0e70fb25339bdb766eb8bd",
        ("AREA_GEOM", "AREA_INDEX_INPUT"),
    ),
    (
        "scripts/build-trail-search-index.py",
        "30d3a1408aa6445b6dfcb7f3ca5b0323c0430d861963d255db5b4ac59a70a47c",
        ("trail-search.json",),
    ),
    (
        "scripts/build-parking-pool.py",
        "ad69d3d64c3dba260f7601782af3f66896339d1ad28c6b5bb5d7b8d58cfde856",
        ("parking.json",),
    ),
    (
        "scripts/build-trail-shapes.py",
        "660433edb1b157466bef8f844f0d7eb5a8c6d9e15b9a2b9f118e367ea9a2f1b5",
        ("trail-shapes.json",),
    ),
    (
        "scripts/merge-published-geom.py",
        "ade4c6f7e5e9d945f13221f34c7e85261dc19f2bc107f19f418badb8356dc4be",
        ("POST_FAN_IN_AREA_GEOM", "POST_FAN_IN_AREA_INDEX"),
    ),
    (
        "scripts/silhouettes-from-geom.py",
        "33dc1154989bbd1babf3d7b9f3c7b0d4f2529c56b90916ea25841ba573c6e862",
        ("POST_FAN_IN_AREA_SILHOUETTE",),
    ),
    (
        "scripts/filter-ios-bundle.py",
        "238a7c9cc7dbefa3e843465e36815bb7e94c8a7b366ab64772085cba4db609b3",
        ("POST_FAN_IN_IOS_AREA_INDEX", "POST_FAN_IN_IOS_ALIAS_MAP"),
    ),
    (
        ".github/workflows/trailforge-publish-us.yml",
        "4064b7525c05cda16d52b3b45e32a4a0f9a3c1199e6b596361487a8b512c1e8f",
        ("REVIEW_CANDIDATE_PATCH", "REVIEW_CANDIDATE_RECEIPT"),
    ),
)

# This is the production call graph authority, separate from the digest
# registry above. A boundary exists only when its exact invocation marker is
# present once in the named workflow. The audit compares the resulting source
# and capability sets with CI_PRODUCER_GRAPH in both directions.
PRODUCTION_PRODUCER_BOUNDARIES: tuple[
    tuple[str, str, str, tuple[str, ...]], ...
] = (
    (
        ".github/workflows/trailforge-publish-us.yml",
        "python3 serve/publish_areas.py",
        "trailforge/serve/publish_areas.py",
        ("AREA_GEOM", "AREA_INDEX_INPUT"),
    ),
    (
        ".github/workflows/sync-geom-to-r2.yml",
        'python3 scripts/build-trail-search-index.py --out "$TRAIL_SEARCH"',
        "scripts/build-trail-search-index.py",
        ("trail-search.json",),
    ),
    (
        ".github/workflows/sync-geom-to-r2.yml",
        'python3 scripts/build-parking-pool.py --out "$PARKING"',
        "scripts/build-parking-pool.py",
        ("parking.json",),
    ),
    (
        ".github/workflows/sync-geom-to-r2.yml",
        'python3 scripts/build-trail-shapes.py --out "$TRAIL_SHAPES"',
        "scripts/build-trail-shapes.py",
        ("trail-shapes.json",),
    ),
    (
        ".github/workflows/trailforge-publish-us.yml",
        "python3 scripts/merge-published-geom.py --artifacts-root artifacts",
        "scripts/merge-published-geom.py",
        ("POST_FAN_IN_AREA_GEOM", "POST_FAN_IN_AREA_INDEX"),
    ),
    (
        ".github/workflows/trailforge-publish-us.yml",
        "python3 scripts/silhouettes-from-geom.py",
        "scripts/silhouettes-from-geom.py",
        ("POST_FAN_IN_AREA_SILHOUETTE",),
    ),
    (
        ".github/workflows/trailforge-publish-us.yml",
        "python3 scripts/filter-ios-bundle.py",
        "scripts/filter-ios-bundle.py",
        ("POST_FAN_IN_IOS_AREA_INDEX", "POST_FAN_IN_IOS_ALIAS_MAP"),
    ),
    (
        ".github/workflows/trailforge-publish-us.yml",
        '"schema": "trekdex-whole-us-review-candidate-v3"',
        ".github/workflows/trailforge-publish-us.yml",
        ("REVIEW_CANDIDATE_PATCH", "REVIEW_CANDIDATE_RECEIPT"),
    ),
)

INVENTORY_FAULT_NAMES = (
    "access-denied",
    "subprocess-nonzero",
    "timeout",
    "empty-stdout",
    "malformed-json",
    "duplicate-json-key",
    "empty-page-with-continuation",
    "wrong-bucket",
    "wrong-account",
    "wrong-endpoint",
    "wrong-token",
    "repeated-token",
    "token-cycle",
    "truncation-mismatch",
    "terminal-token",
    "key-count-mismatch",
    "denominator-mismatch",
    "duplicate-object",
    "malformed-preimage",
    "missing-protected-key",
    "extra-unknown-key",
    "changed-receipt-after-review",
    "wrong-anchor",
    "stale-preimage",
    "conditional-mode-mismatch",
)

_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_SHA1_RE = re.compile(r"[0-9a-f]{40}")
_RECEIPT_ID_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?")
_SAFE_VERSION_RE = re.compile(r"[A-Za-z0-9._:+/=~-]{1,256}")


class InventoryError(RuntimeError):
    """Inventory bytes do not prove complete, canonical provider state."""


class ManifestError(RuntimeError):
    """A reviewed upload/quarantine manifest is incomplete or ambiguous."""


class ConditionalRequestRefused(RuntimeError):
    """The provider proved a conditional effect did not occur."""


class KeyClass(str, Enum):
    AREA_GEOM = "AREA_GEOM"
    AREA_SILHOUETTE = "AREA_SILHOUETTE"
    GLOBAL = "GLOBAL"
    QUARANTINE = "QUARANTINE"
    CONTROL = "CONTROL"
    UNKNOWN = "UNKNOWN"


class TerminalState(str, Enum):
    SAFE_ABORT = "SAFE_ABORT"
    COMPLETE_SUCCESS = "COMPLETE_SUCCESS"
    INDETERMINATE = "INDETERMINATE"


class ConditionalWriter(Protocol):
    def attest_identity(self) -> dict[str, Any]: ...

    def preflight_release(
        self, manifest: dict[str, Any], *, execution_id: str
    ) -> dict[str, Any]: ...

    def put_conditional(
        self,
        row: dict[str, Any],
        *,
        attempt_id: str,
        preflight_row: dict[str, Any],
        preflight_sha256: str,
    ) -> dict[str, Any]: ...


class QuarantineStore(Protocol):
    def attest_identity(self) -> dict[str, Any]: ...

    def head(self, key: str) -> dict[str, Any] | None: ...

    def copy_no_clobber(
        self,
        source: str,
        destination: str,
        *,
        source_preimage: dict[str, Any],
        attempt_id: str,
    ) -> dict[str, Any]: ...

    def remove_after_exact_proof(
        self,
        source: str,
        *,
        source_preimage: dict[str, Any],
        attempt_id: str,
    ) -> dict[str, Any]: ...


class ScopedConditionalWriter(ConditionalWriter, Protocol):
    def tool_attestation(self) -> dict[str, Any]: ...

    def preflight_scoped_release(
        self, manifest: dict[str, Any], *, execution_id: str
    ) -> dict[str, Any]: ...

    def rollback_put_conditional(
        self,
        rollback_row: dict[str, Any],
        *,
        postimage: dict[str, Any],
        attempt_id: str,
        scoped_preflight_sha256: str,
    ) -> dict[str, Any]: ...

    def rollback_delete_created(
        self,
        rollback_row: dict[str, Any],
        *,
        postimage: dict[str, Any],
        attempt_id: str,
        scoped_preflight_sha256: str,
    ) -> dict[str, Any]: ...

    def verify_rollback(
        self,
        rollback_manifest: dict[str, Any],
        *,
        forward_results: Sequence[dict[str, Any]],
        rollback_results: Sequence[dict[str, Any]],
    ) -> dict[str, Any]: ...

    def reconcile_pending_effect(
        self,
        pending: dict[str, Any],
        manifest: dict[str, Any],
        journal: dict[str, Any],
    ) -> dict[str, Any]: ...


def _canonical_bytes(document: Any) -> bytes:
    try:
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
    except (TypeError, ValueError) as error:
        raise ValueError(f"document is not canonical JSON: {error}") from error


def canonical_sha256(document: Any, *, omit: set[str] | None = None) -> str:
    """Hash canonical UTF-8 JSON, optionally omitting named top-level fields."""
    value = copy.deepcopy(document)
    if omit:
        if not isinstance(value, dict):
            raise TypeError("omit is valid only for a JSON object")
        for key in omit:
            value.pop(key, None)
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def strict_json_loads(text: str, context: str) -> Any:
    """Parse JSON while rejecting duplicate member names and non-finite values."""
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise InventoryError(f"{context} contains duplicate JSON key {key!r}")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise InventoryError(f"{context} contains non-finite number {value}")

    try:
        return json.loads(
            text,
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_constant,
        )
    except InventoryError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise InventoryError(f"{context} is not valid JSON: {error}") from error


def _is_plain_safe_text(value: Any, *, maximum: int = 512) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= maximum
        and value == unicodedata.normalize("NFC", value)
        and not any(unicodedata.category(char).startswith("C") for char in value)
    )


def _require_digest(value: Any, context: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{context} must be one lowercase SHA-256")
    return value


def _require_git_identity(value: Any, context: str) -> str:
    if not isinstance(value, str) or not _SHA1_RE.fullmatch(value):
        raise ManifestError(f"{context} must be one full lowercase Git identity")
    return value


def _require_identity_ref(value: Any, context: str) -> str:
    if (
        not isinstance(value, str)
        or not value.startswith("sha256:")
        or not _SHA256_RE.fullmatch(value.removeprefix("sha256:"))
    ):
        raise ValueError(f"{context} must be a privacy-safe sha256 reference")
    return value


def identity_ref(domain: str, private_value: str) -> str:
    """Hash a high-entropy or synthetic value with domain separation.

    Runtime account and endpoint references must be supplied as precomputed
    privacy-safe references; low-entropy identifiers are not safe to publish as
    plain hashes because they can be enumerated offline.
    """
    if not _is_plain_safe_text(domain, maximum=64) or not isinstance(private_value, str):
        raise ValueError("identity reference inputs are invalid")
    framed = domain.encode("utf-8") + b"\0" + private_value.encode("utf-8")
    return "sha256:" + hashlib.sha256(framed).hexdigest()


def normalize_sha256_checksum(value: Any, context: str) -> str:
    """Normalize an independent provider SHA-256 (hex or RFC 4648 base64)."""
    if not isinstance(value, str):
        raise InventoryError(f"{context} independent SHA-256 is missing")
    if _SHA256_RE.fullmatch(value):
        return value
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as error:
        raise InventoryError(f"{context} independent SHA-256 is malformed") from error
    if len(decoded) != hashlib.sha256().digest_size:
        raise InventoryError(f"{context} independent SHA-256 has the wrong length")
    if base64.b64encode(decoded).decode("ascii") != value:
        raise InventoryError(f"{context} independent SHA-256 is not canonical base64")
    return decoded.hex()


def provider_identity_attestation(account_id: str, endpoint_url: str) -> dict[str, Any]:
    """Derive privacy-safe refs from the actual values used by an adapter."""
    if not _is_plain_safe_text(account_id, maximum=256):
        raise ValueError("actual provider account identity is unavailable or unsafe")
    if not _is_plain_safe_text(endpoint_url, maximum=1024):
        raise ValueError("actual provider endpoint identity is unavailable or unsafe")
    receipt: dict[str, Any] = {
        "schema": PROVIDER_ATTESTATION_SCHEMA,
        "account_identity_ref": identity_ref("trekdex-r2-account-v1", account_id),
        "endpoint_identity_ref": identity_ref("trekdex-r2-endpoint-v1", endpoint_url),
    }
    receipt["attestation_sha256"] = canonical_sha256(receipt)
    return receipt


def validate_provider_identity_attestation(
    attestation: Any,
    *,
    expected_account_identity_ref: str,
    expected_endpoint_identity_ref: str,
) -> dict[str, Any]:
    if not isinstance(attestation, dict) or set(attestation) != {
        "schema", "account_identity_ref", "endpoint_identity_ref", "attestation_sha256"
    }:
        raise ManifestError("provider identity attestation schema is not exact")
    if attestation["schema"] != PROVIDER_ATTESTATION_SCHEMA:
        raise ManifestError("provider identity attestation version mismatch")
    try:
        account_ref = _require_identity_ref(
            attestation["account_identity_ref"], "provider account identity"
        )
        endpoint_ref = _require_identity_ref(
            attestation["endpoint_identity_ref"], "provider endpoint identity"
        )
        digest = _require_digest(attestation["attestation_sha256"], "provider attestation")
    except ValueError as error:
        raise ManifestError(str(error)) from error
    if digest != canonical_sha256(attestation, omit={"attestation_sha256"}):
        raise ManifestError("provider identity attestation digest mismatch")
    if account_ref != expected_account_identity_ref or endpoint_ref != expected_endpoint_identity_ref:
        raise ManifestError("live provider identity differs from the reviewed identity")
    return copy.deepcopy(attestation)


def provider_tool_attestation(
    *, version: str, archive_sha256: str
) -> dict[str, Any]:
    if version != AWS_CLI_VERSION or archive_sha256 != AWS_CLI_ARCHIVE_SHA256:
        raise ManifestError("provider tool does not match the pinned AWS CLI identity")
    receipt: dict[str, Any] = {
        "schema": PROVIDER_TOOL_ATTESTATION_SCHEMA,
        "tool": "aws-cli",
        "version": version,
        "archive_sha256": archive_sha256,
    }
    receipt["attestation_sha256"] = canonical_sha256(receipt)
    return receipt


def validate_provider_tool_attestation(attestation: Any) -> dict[str, Any]:
    fields = {
        "schema", "tool", "version", "archive_sha256", "attestation_sha256"
    }
    if not isinstance(attestation, dict) or set(attestation) != fields:
        raise ManifestError("provider tool attestation schema is not exact")
    if (
        attestation["schema"] != PROVIDER_TOOL_ATTESTATION_SCHEMA
        or attestation["tool"] != "aws-cli"
        or attestation["version"] != AWS_CLI_VERSION
        or attestation["archive_sha256"] != AWS_CLI_ARCHIVE_SHA256
    ):
        raise ManifestError("provider tool attestation identity mismatch")
    try:
        digest = _require_digest(
            attestation["attestation_sha256"], "provider tool attestation"
        )
    except ValueError as error:
        raise ManifestError(str(error)) from error
    if digest != canonical_sha256(attestation, omit={"attestation_sha256"}):
        raise ManifestError("provider tool attestation digest mismatch")
    return copy.deepcopy(attestation)


def _approval_key_bytes(key: bytes | str) -> bytes:
    if isinstance(key, str):
        key = key.encode("utf-8")
    if not isinstance(key, bytes) or len(key) < 32:
        raise ManifestError("root approval verification key is unavailable or too short")
    return key


def _approval_artifact_bindings(artifact: Mapping[str, Any]) -> dict[str, Any]:
    required = (
        "source_sha", "source_tree", "workflow_sha256", "root_approval_ref",
        "account_identity_ref", "endpoint_identity_ref", "inventory_receipt_sha256",
        "inventory_anchor_ref", "authoritative_denominator", "area_registry_sha256",
    )
    missing = [field for field in required if field not in artifact]
    if missing:
        raise ManifestError(f"approved artifact lacks immutable bindings: {missing}")
    artifact_digest = artifact.get("manifest_sha256", artifact.get("plan_sha256"))
    _require_digest(artifact_digest, "approved artifact SHA-256")
    return {
        **{field: artifact[field] for field in required},
        "artifact_sha256": artifact_digest,
    }


def make_approval_assertion(
    artifact: Mapping[str, Any],
    *,
    operation: str,
    key: bytes | str,
    authorization_nonce: str,
) -> dict[str, Any]:
    """Create a synthetic assertion for offline tests or a trusted signer."""
    if operation not in {"RELEASE", "QUARANTINE"}:
        raise ManifestError("approval assertion operation is invalid")
    if not isinstance(authorization_nonce, str) or not _SAFE_VERSION_RE.fullmatch(authorization_nonce):
        raise ManifestError("approval assertion nonce is invalid")
    assertion: dict[str, Any] = {
        "schema": APPROVAL_ASSERTION_SCHEMA,
        "contract_version": CONTRACT_VERSION,
        "operation": operation,
        **_approval_artifact_bindings(artifact),
        "authorization_nonce": authorization_nonce,
    }
    assertion["signature_hmac_sha256"] = hmac.new(
        _approval_key_bytes(key), _canonical_bytes(assertion), hashlib.sha256
    ).hexdigest()
    return assertion


def validate_approval_assertion(
    assertion: Any,
    artifact: Mapping[str, Any],
    *,
    operation: str,
    key: bytes | str,
) -> str:
    fields = {
        "schema", "contract_version", "operation", "source_sha", "source_tree",
        "workflow_sha256", "root_approval_ref", "account_identity_ref",
        "endpoint_identity_ref", "inventory_receipt_sha256", "inventory_anchor_ref",
        "authoritative_denominator", "area_registry_sha256", "artifact_sha256",
        "authorization_nonce", "signature_hmac_sha256",
    }
    if not isinstance(assertion, dict) or set(assertion) != fields:
        raise ManifestError("root approval assertion schema is not exact")
    if (
        assertion["schema"] != APPROVAL_ASSERTION_SCHEMA
        or assertion["contract_version"] != CONTRACT_VERSION
        or assertion["operation"] != operation
    ):
        raise ManifestError("root approval assertion identity mismatch")
    expected = _approval_artifact_bindings(artifact)
    for field, value in expected.items():
        if assertion[field] != value:
            raise ManifestError(f"root approval assertion has wrong {field}")
    if not isinstance(assertion["authorization_nonce"], str) or not _SAFE_VERSION_RE.fullmatch(
        assertion["authorization_nonce"]
    ):
        raise ManifestError("root approval assertion nonce is invalid")
    signature = assertion["signature_hmac_sha256"]
    try:
        _require_digest(signature, "root approval signature")
    except ValueError as error:
        raise ManifestError(str(error)) from error
    unsigned = copy.deepcopy(assertion)
    unsigned.pop("signature_hmac_sha256")
    expected_signature = hmac.new(
        _approval_key_bytes(key), _canonical_bytes(unsigned), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature, expected_signature):
        raise ManifestError("root approval assertion signature mismatch")
    return canonical_sha256(assertion)


def _is_area_filename(filename: Any) -> bool:
    """Return whether a filename is one canonical jurisdiction-suffixed area key."""
    if not isinstance(filename, str) or "/" in filename or not filename.endswith(".json"):
        return False
    if filename != unicodedata.normalize("NFC", filename) or filename != filename.casefold():
        return False
    stem = filename.removesuffix(".json")
    if "-" not in stem:
        return False
    prefix, jurisdiction = stem.rsplit("-", 1)
    if not prefix or not re.fullmatch(r"[a-z]{2}", jurisdiction):
        return False
    # Historical source truncation can leave a double hyphen immediately
    # before the jurisdiction. It remains canonical and collision-free.
    return (
        not prefix.startswith("-")
        and any(char.isalnum() for char in prefix)
        and all(char.isalnum() or char == "-" for char in prefix)
    )


def make_mols_release_input_receipt(
    *,
    key: bytes | str,
    root_approval_ref: str,
    account_identity_ref: str,
    endpoint_identity_ref: str,
    inventory_anchor_ref: str,
    authoritative_denominator: int,
    workflow_source_sha: str,
    workflow_source_tree: str,
    authorization_nonce: str,
) -> dict[str, Any]:
    """Create one canonical signed handoff for the exact Mols publication."""
    for value, context in (
        (root_approval_ref, "root_approval_ref"),
        (account_identity_ref, "account_identity_ref"),
        (endpoint_identity_ref, "endpoint_identity_ref"),
        (inventory_anchor_ref, "inventory_anchor_ref"),
    ):
        try:
            _require_identity_ref(value, context)
        except ValueError as error:
            raise ManifestError(str(error)) from error
    if (
        not isinstance(authoritative_denominator, int)
        or isinstance(authoritative_denominator, bool)
        or authoritative_denominator < len(GLOBAL_OBJECTS)
    ):
        raise ManifestError("release-input authoritative denominator is invalid")
    workflow_source_sha = _require_git_identity(
        workflow_source_sha, "workflow_source_sha"
    )
    workflow_source_tree = _require_git_identity(
        workflow_source_tree, "workflow_source_tree"
    )
    if not isinstance(authorization_nonce, str) or not _SAFE_VERSION_RE.fullmatch(
        authorization_nonce
    ):
        raise ManifestError("release-input authorization nonce is invalid")
    receipt: dict[str, Any] = {
        "schema": MOLS_RELEASE_INPUT_SCHEMA,
        "contract_version": CONTRACT_VERSION,
        "extract": MOLS_EXTRACT,
        "state": MOLS_STATE,
        "region_code": MOLS_REGION_CODE,
        "area_id": MOLS_AREA_ID,
        "area_name": MOLS_AREA_NAME,
        "osm_relation_id": MOLS_RELATION_ID,
        "bbox": MOLS_BBOX,
        "center_lat": MOLS_CENTER_LAT,
        "center_lon": MOLS_CENTER_LON,
        "root_approval_ref": root_approval_ref,
        "account_identity_ref": account_identity_ref,
        "endpoint_identity_ref": endpoint_identity_ref,
        "inventory_anchor_ref": inventory_anchor_ref,
        "authoritative_denominator": authoritative_denominator,
        "workflow_source_sha": workflow_source_sha,
        "workflow_source_tree": workflow_source_tree,
        "authorization_nonce": authorization_nonce,
    }
    receipt["receipt_sha256"] = canonical_sha256(receipt)
    receipt["signature_hmac_sha256"] = hmac.new(
        _approval_key_bytes(key), _canonical_bytes(receipt), hashlib.sha256
    ).hexdigest()
    return validate_mols_release_input_receipt(receipt, key=key)


def validate_mols_release_input_receipt(
    receipt: Any, *, key: bytes | str
) -> dict[str, Any]:
    fields = {
        "schema", "contract_version", "extract", "state", "region_code",
        "area_id", "area_name", "osm_relation_id", "bbox", "center_lat", "center_lon",
        "root_approval_ref", "account_identity_ref", "endpoint_identity_ref",
        "inventory_anchor_ref", "authoritative_denominator",
        "workflow_source_sha", "workflow_source_tree",
        "authorization_nonce", "receipt_sha256", "signature_hmac_sha256",
    }
    if not isinstance(receipt, dict) or set(receipt) != fields:
        raise ManifestError("Mols release-input receipt schema is not exact")
    exact = {
        "schema": MOLS_RELEASE_INPUT_SCHEMA,
        "contract_version": CONTRACT_VERSION,
        "extract": MOLS_EXTRACT,
        "state": MOLS_STATE,
        "region_code": MOLS_REGION_CODE,
        "area_id": MOLS_AREA_ID,
        "area_name": MOLS_AREA_NAME,
        "osm_relation_id": MOLS_RELATION_ID,
        "bbox": MOLS_BBOX,
        "center_lat": MOLS_CENTER_LAT,
        "center_lon": MOLS_CENTER_LON,
    }
    for field, value in exact.items():
        if receipt[field] != value:
            raise ManifestError(f"Mols release-input receipt has wrong {field}")
    for field in (
        "root_approval_ref", "account_identity_ref", "endpoint_identity_ref",
        "inventory_anchor_ref",
    ):
        try:
            _require_identity_ref(receipt[field], field)
        except ValueError as error:
            raise ManifestError(str(error)) from error
    denominator = receipt["authoritative_denominator"]
    if (
        not isinstance(denominator, int)
        or isinstance(denominator, bool)
        or denominator < len(GLOBAL_OBJECTS)
    ):
        raise ManifestError("Mols release-input denominator is invalid")
    _require_git_identity(receipt["workflow_source_sha"], "workflow_source_sha")
    _require_git_identity(receipt["workflow_source_tree"], "workflow_source_tree")
    nonce = receipt["authorization_nonce"]
    if not isinstance(nonce, str) or not _SAFE_VERSION_RE.fullmatch(nonce):
        raise ManifestError("Mols release-input nonce is invalid")
    try:
        digest = _require_digest(receipt["receipt_sha256"], "release-input receipt")
        signature = _require_digest(
            receipt["signature_hmac_sha256"], "release-input signature"
        )
    except ValueError as error:
        raise ManifestError(str(error)) from error
    unsigned = copy.deepcopy(receipt)
    unsigned.pop("signature_hmac_sha256")
    expected_digest = canonical_sha256(unsigned, omit={"receipt_sha256"})
    if digest != expected_digest:
        raise ManifestError("Mols release-input receipt digest mismatch")
    expected_signature = hmac.new(
        _approval_key_bytes(key), _canonical_bytes(unsigned), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature, expected_signature):
        raise ManifestError("Mols release-input receipt signature mismatch")
    return copy.deepcopy(receipt)


def decode_mols_release_input_receipt(
    encoded: str, *, key: bytes | str
) -> dict[str, Any]:
    if not isinstance(encoded, str) or not encoded or any(char.isspace() for char in encoded):
        raise ManifestError("Mols release-input receipt base64 is missing or noncanonical")
    try:
        payload = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ManifestError("Mols release-input receipt base64 is malformed") from error
    if base64.b64encode(payload).decode("ascii") != encoded:
        raise ManifestError("Mols release-input receipt base64 is noncanonical")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ManifestError("Mols release-input receipt is not UTF-8") from error
    try:
        document = strict_json_loads(text, "Mols release-input receipt")
    except InventoryError as error:
        raise ManifestError(str(error)) from error
    if not isinstance(document, dict) or payload != _canonical_bytes(document):
        raise ManifestError("Mols release-input receipt bytes are not canonical JSON")
    return validate_mols_release_input_receipt(document, key=key)


def _canonical_area_ids(values: Any, context: str) -> tuple[str, ...]:
    if not isinstance(values, (set, frozenset, list, tuple)):
        raise ManifestError(f"{context} is not one explicit area registry")
    ordered = sorted(values)
    if len(ordered) != len(set(ordered)):
        raise ManifestError(f"{context} contains duplicate IDs")
    for area_id in ordered:
        if not isinstance(area_id, str) or not _is_area_filename(area_id + ".json"):
            raise ManifestError(f"{context} contains a noncanonical ID")
    return tuple(ordered)


def classify_key(
    key: Any,
    *,
    authoritative_area_ids: set[str] | frozenset[str] | None = None,
) -> KeyClass:
    """Classify a key; area shape is never authority without registry membership."""
    if not _is_plain_safe_text(key, maximum=1024):
        return KeyClass.UNKNOWN
    assert isinstance(key, str)
    if key != key.strip() or key != key.casefold() or "\\" in key:
        return KeyClass.UNKNOWN
    if key in GLOBAL_OBJECTS:
        return KeyClass.GLOBAL
    if key.startswith(QUARANTINE_PREFIX):
        remainder = key[len(QUARANTINE_PREFIX):]
        pieces = remainder.split("/", 1)
        if (
            len(pieces) == 2
            and _RECEIPT_ID_RE.fullmatch(pieces[0])
            and pieces[1]
            and "//" not in pieces[1]
            and "/../" not in f"/{pieces[1]}/"
        ):
            return KeyClass.QUARANTINE
        return KeyClass.UNKNOWN
    if key.startswith(CONTROL_PREFIX):
        remainder = key[len(CONTROL_PREFIX):]
        if (
            remainder
            and not remainder.startswith("/")
            and not remainder.endswith("/")
            and "//" not in remainder
            and "/../" not in f"/{remainder}/"
            and all(part not in {"", ".", ".."} for part in remainder.split("/"))
        ):
            return KeyClass.CONTROL
        return KeyClass.UNKNOWN
    filename = key
    candidate = KeyClass.AREA_GEOM
    if key.startswith("silhouettes/"):
        filename = key[len("silhouettes/"):]
        candidate = KeyClass.AREA_SILHOUETTE
    elif "/" in key:
        return KeyClass.UNKNOWN
    if not _is_area_filename(filename):
        return KeyClass.UNKNOWN
    if authoritative_area_ids is None:
        return KeyClass.UNKNOWN
    area_id = filename.removesuffix(".json")
    return candidate if area_id in authoritative_area_ids else KeyClass.UNKNOWN


def load_area_ids(path: Path | str, *, minimum: int = 1) -> set[str]:
    """Load one exact canonical area-ID set from a regular JSON index."""
    source = Path(path)
    if source.is_symlink() or not source.is_file():
        raise ManifestError(f"area index is not a regular file: {source}")
    document = strict_json_loads(source.read_text(encoding="utf-8"), "authoritative area index")
    if isinstance(document, dict):
        if set(document) != {"areas"}:
            raise ManifestError("area index object schema is not exact")
        rows = document["areas"]
    elif isinstance(document, list):
        rows = document
    else:
        raise ManifestError("area index is not an array")
    if not isinstance(rows, list):
        raise ManifestError("area index areas member is not an array")
    area_ids: set[str] = set()
    for number, row in enumerate(rows, start=1):
        area_id = (
            row[0] if isinstance(row, list) and row
            else row.get("id") if isinstance(row, dict) and set(row) >= {"id"}
            else None
        )
        if not isinstance(area_id, str) or not _is_area_filename(area_id + ".json"):
            raise ManifestError(f"area index row {number} has a noncanonical ID")
        if area_id in area_ids:
            raise ManifestError(f"area index repeats ID {area_id!r}")
        area_ids.add(area_id)
    if (
        not isinstance(minimum, int)
        or isinstance(minimum, bool)
        or minimum < 0
        or len(area_ids) < minimum
    ):
        raise ManifestError(f"area index is implausibly short: {len(area_ids)} < {minimum}")
    return area_ids


def _require_receipt_id(value: Any) -> str:
    if not isinstance(value, str) or not _RECEIPT_ID_RE.fullmatch(value):
        raise ValueError("receipt_id is not canonical")
    return value


def quarantine_key(
    receipt_id: str,
    source_key: str,
    *,
    authoritative_area_ids: set[str] | frozenset[str],
) -> str:
    receipt_id = _require_receipt_id(receipt_id)
    source_class = classify_key(source_key, authoritative_area_ids=authoritative_area_ids)
    if source_class not in {KeyClass.AREA_GEOM, KeyClass.AREA_SILHOUETTE}:
        raise ValueError("only exact area objects can enter quarantine")
    # Lowercase hexadecimal encoding is injective and prevents source
    # separators or Unicode bytes from creating an ambiguous control hierarchy.
    encoded_source = source_key.encode("utf-8").hex()
    result = f"{QUARANTINE_PREFIX}{receipt_id}/{encoded_source}"
    if len(result.encode("utf-8")) > 1024:
        raise ValueError("quarantine key exceeds the provider key-size contract")
    if classify_key(result) is not KeyClass.QUARANTINE:
        raise AssertionError("internal quarantine key is not classifiable")
    return result


def _normalize_etag(value: Any, context: str) -> str:
    if not isinstance(value, str):
        raise InventoryError(f"{context} ETag is not a string")
    normalized = value.strip()
    if len(normalized) >= 2 and normalized[0] == normalized[-1] == '"':
        normalized = normalized[1:-1]
    if (
        not _is_plain_safe_text(normalized, maximum=512)
        or normalized != normalized.strip()
        or any(char.isspace() for char in normalized)
        or '"' in normalized
    ):
        raise InventoryError(f"{context} ETag is empty or malformed")
    return normalized


def _normalize_preimage(item: Mapping[str, Any], context: str) -> dict[str, Any]:
    etag = _normalize_etag(item.get("ETag", item.get("etag")), context)
    byte_count = item.get("Size", item.get("bytes"))
    if not isinstance(byte_count, int) or isinstance(byte_count, bool) or byte_count < 0:
        raise InventoryError(f"{context} byte count is invalid")
    checksum = normalize_sha256_checksum(
        item.get("ChecksumSHA256", item.get("sha256")), f"{context} SHA-256"
    )
    return {"etag": etag, "bytes": byte_count, "sha256": checksum}


def _page_document(raw: Any, page_number: int) -> dict[str, Any]:
    context = f"inventory page {page_number}"
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise InventoryError(f"{context} is not UTF-8") from error
    if isinstance(raw, str):
        if not raw.strip():
            raise InventoryError(f"{context} stdout is empty")
        raw = strict_json_loads(raw, context)
    if not isinstance(raw, dict):
        raise InventoryError(f"{context} is not an object")
    return raw


def fetch_complete_inventory(
    list_page: Callable[[str | None], Any],
    *,
    bucket: str,
    account_identity_ref: str,
    endpoint_identity_ref: str,
    authoritative_denominator: int,
    authoritative_area_ids: set[str] | frozenset[str],
    anchor_state: str,
    anchor_ref: str,
) -> dict[str, Any]:
    """Fetch and seal every explicit page through a dependency-injected pager."""
    if bucket != BUCKET:
        raise InventoryError("inventory bucket is not the exact contract bucket")
    try:
        _require_identity_ref(account_identity_ref, "account_identity_ref")
        _require_identity_ref(endpoint_identity_ref, "endpoint_identity_ref")
        _require_identity_ref(anchor_ref, "anchor_ref")
    except ValueError as error:
        raise InventoryError(str(error)) from error
    if anchor_state != "ASSERTED_PENDING_APPROVAL":
        raise InventoryError("inventory anchor must remain pending immutable approval")
    if (
        not isinstance(authoritative_denominator, int)
        or isinstance(authoritative_denominator, bool)
        or authoritative_denominator < len(GLOBAL_OBJECTS)
    ):
        raise InventoryError("authoritative denominator is invalid")
    try:
        area_registry = _canonical_area_ids(authoritative_area_ids, "authoritative area registry")
    except ManifestError as error:
        raise InventoryError(str(error)) from error
    area_registry_set = frozenset(area_registry)
    if not area_registry:
        raise InventoryError("authoritative area registry is empty")

    request_token: str | None = None
    seen_tokens: set[str] = set()
    pages: list[dict[str, Any]] = []
    objects: dict[str, dict[str, Any]] = {}
    while True:
        if len(pages) >= MAX_INVENTORY_PAGES:
            raise InventoryError("inventory exceeded maximum page count")
        try:
            raw = list_page(request_token)
        except Exception as error:
            raise InventoryError(
                f"inventory page {len(pages) + 1} failed: {type(error).__name__}: {error}"
            ) from error
        page_number = len(pages) + 1
        document = _page_document(raw, page_number)
        if document.get("Name") != bucket:
            raise InventoryError(f"inventory page {page_number} names the wrong bucket")
        truncated = document.get("IsTruncated")
        if not isinstance(truncated, bool):
            raise InventoryError(f"inventory page {page_number} lacks boolean IsTruncated")
        contents = document.get("Contents", [])
        if not isinstance(contents, list):
            raise InventoryError(f"inventory page {page_number} Contents is not an array")
        count = document.get("KeyCount")
        if not isinstance(count, int) or isinstance(count, bool) or count != len(contents):
            raise InventoryError(f"inventory page {page_number} KeyCount mismatch")
        if truncated and not contents:
            raise InventoryError("an empty inventory page cannot continue")
        echoed = document.get("ContinuationToken")
        if request_token is None:
            if echoed not in (None, ""):
                raise InventoryError("first page unexpectedly echoes a continuation token")
        elif echoed != request_token:
            raise InventoryError(f"inventory page {page_number} continuation token mismatch")

        for offset, item in enumerate(contents, start=1):
            context = f"inventory page {page_number} object {offset}"
            if not isinstance(item, dict):
                raise InventoryError(f"{context} is not an object")
            key = item.get("Key")
            if not _is_plain_safe_text(key, maximum=1024):
                raise InventoryError(f"{context} key is empty or unsafe")
            assert isinstance(key, str)
            key_class = classify_key(key, authoritative_area_ids=area_registry_set)
            if key_class is KeyClass.UNKNOWN:
                raise InventoryError(f"{context} key is outside the versioned registry contract: {key!r}")
            if key in objects:
                raise InventoryError(f"inventory repeats key across pages: {key}")
            objects[key] = {
                "key": key,
                "key_class": key_class.value,
                "preimage": _normalize_preimage(item, context),
            }

        next_token = document.get("NextContinuationToken")
        page_row = {
            "number": page_number,
            "request_token_ref": (
                identity_ref("inventory-token", request_token) if request_token is not None else None
            ),
            "next_token_ref": None,
            "is_truncated": truncated,
            "object_count": len(contents),
        }
        if truncated:
            if not _is_plain_safe_text(next_token, maximum=4096):
                raise InventoryError(f"inventory page {page_number} lacks a valid next token")
            assert isinstance(next_token, str)
            if next_token == request_token or next_token in seen_tokens:
                raise InventoryError(f"inventory page {page_number} repeats a pagination token")
            seen_tokens.add(next_token)
            page_row["next_token_ref"] = identity_ref("inventory-token", next_token)
            request_token = next_token
            pages.append(page_row)
            continue
        if next_token not in (None, ""):
            raise InventoryError(f"terminal inventory page {page_number} has a next token")
        pages.append(page_row)
        break

    missing = sorted(set(GLOBAL_OBJECTS) - set(objects))
    if missing:
        raise InventoryError(f"complete inventory omits protected keys: {missing}")
    if len(objects) != authoritative_denominator:
        raise InventoryError(
            f"authoritative denominator mismatch: receipt={len(objects)} expected={authoritative_denominator}"
        )
    ordered = [objects[key] for key in sorted(objects)]
    class_counts = {
        key_class.value: sum(row["key_class"] == key_class.value for row in ordered)
        for key_class in KeyClass
        if key_class is not KeyClass.UNKNOWN
    }
    receipt: dict[str, Any] = {
        "schema": INVENTORY_SCHEMA,
        "contract_version": CONTRACT_VERSION,
        "bucket": bucket,
        "account_identity_ref": account_identity_ref,
        "endpoint_identity_ref": endpoint_identity_ref,
        "complete": True,
        "page_count": len(pages),
        "pages": pages,
        "authoritative_denominator": authoritative_denominator,
        "area_registry_count": len(area_registry),
        "area_registry_ids": list(area_registry),
        "area_registry_sha256": canonical_sha256(list(area_registry)),
        "object_count": len(ordered),
        "protected_keys": list(GLOBAL_OBJECTS),
        "class_counts": class_counts,
        "objects": ordered,
        "anchor_state": anchor_state,
        "anchor_ref": anchor_ref,
    }
    receipt["receipt_sha256"] = canonical_sha256(receipt)
    validate_inventory_receipt(receipt, expected_bucket=bucket)
    return receipt


def validate_inventory_receipt(
    receipt: Any,
    *,
    expected_bucket: str = BUCKET,
    expected_account_identity_ref: str | None = None,
    expected_endpoint_identity_ref: str | None = None,
    expected_anchor_ref: str | None = None,
) -> dict[str, dict[str, Any]]:
    if not isinstance(receipt, dict):
        raise InventoryError("inventory receipt is not an object")
    fields = {
        "schema", "contract_version", "bucket", "account_identity_ref",
        "endpoint_identity_ref", "complete", "page_count", "pages",
        "authoritative_denominator", "area_registry_count", "area_registry_ids",
        "area_registry_sha256", "object_count", "protected_keys",
        "class_counts", "objects", "anchor_state", "anchor_ref", "receipt_sha256",
    }
    if set(receipt) != fields:
        raise InventoryError("inventory receipt schema is not exact")
    if receipt["schema"] != INVENTORY_SCHEMA or receipt["contract_version"] != CONTRACT_VERSION:
        raise InventoryError("inventory receipt version mismatch")
    if receipt["bucket"] != expected_bucket or expected_bucket != BUCKET:
        raise InventoryError("inventory receipt bucket mismatch")
    try:
        account_ref = _require_identity_ref(receipt["account_identity_ref"], "account_identity_ref")
        endpoint_ref = _require_identity_ref(receipt["endpoint_identity_ref"], "endpoint_identity_ref")
        anchor_ref = _require_identity_ref(receipt["anchor_ref"], "anchor_ref")
        digest = _require_digest(receipt["receipt_sha256"], "receipt_sha256")
    except ValueError as error:
        raise InventoryError(str(error)) from error
    if expected_account_identity_ref is not None and account_ref != expected_account_identity_ref:
        raise InventoryError("inventory account identity mismatch")
    if expected_endpoint_identity_ref is not None and endpoint_ref != expected_endpoint_identity_ref:
        raise InventoryError("inventory endpoint identity mismatch")
    if expected_anchor_ref is not None and anchor_ref != expected_anchor_ref:
        raise InventoryError("inventory anchor reference mismatch")
    if receipt["anchor_state"] != "ASSERTED_PENDING_APPROVAL" or receipt["complete"] is not True:
        raise InventoryError("inventory receipt is not complete or honestly pending approval")
    if digest != canonical_sha256(receipt, omit={"receipt_sha256"}):
        raise InventoryError("inventory receipt digest mismatch")
    if receipt["protected_keys"] != list(GLOBAL_OBJECTS):
        raise InventoryError("inventory protected set mismatch")
    try:
        area_registry = _canonical_area_ids(receipt["area_registry_ids"], "inventory area registry")
    except ManifestError as error:
        raise InventoryError(str(error)) from error
    if (
        not area_registry
        or receipt["area_registry_count"] != len(area_registry)
        or receipt["area_registry_sha256"] != canonical_sha256(list(area_registry))
    ):
        raise InventoryError("inventory area-registry closure mismatch")
    area_registry_set = frozenset(area_registry)
    count_minimums = {
        "page_count": 1,
        "authoritative_denominator": len(GLOBAL_OBJECTS),
        "area_registry_count": 1,
        "object_count": len(GLOBAL_OBJECTS),
    }
    for field, minimum in count_minimums.items():
        value = receipt[field]
        if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
            raise InventoryError(f"inventory {field} is not an exact nonnegative integer")
    if (
        receipt["area_registry_count"] != len(area_registry)
        or receipt["area_registry_sha256"] != canonical_sha256(list(area_registry))
    ):
        raise InventoryError("inventory area-registry closure mismatch")
    pages = receipt["pages"]
    if (
        not isinstance(pages, list)
        or not pages
        or receipt["page_count"] != len(pages)
        or len(pages) > MAX_INVENTORY_PAGES
    ):
        raise InventoryError("inventory page closure mismatch")
    expected_request: str | None = None
    issued_token_refs: set[str] = set()
    consumed_token_refs: set[str] = set()
    page_total = 0
    for number, page_row in enumerate(pages, start=1):
        if not isinstance(page_row, dict) or set(page_row) != {
            "number", "request_token_ref", "next_token_ref", "is_truncated", "object_count"
        }:
            raise InventoryError("inventory page receipt schema is not exact")
        if (
            not isinstance(page_row["number"], int)
            or isinstance(page_row["number"], bool)
            or page_row["number"] != number
        ):
            raise InventoryError("inventory page number is not an exact sequential integer")
        request_ref = page_row["request_token_ref"]
        if request_ref != expected_request:
            raise InventoryError("inventory token chain is not closed")
        if request_ref is not None:
            try:
                request_ref = _require_identity_ref(request_ref, "request_token_ref")
            except ValueError as error:
                raise InventoryError(str(error)) from error
            if request_ref not in issued_token_refs or request_ref in consumed_token_refs:
                raise InventoryError("inventory request token is repeated or was never issued")
            consumed_token_refs.add(request_ref)
        truncated = page_row["is_truncated"]
        if not isinstance(truncated, bool):
            raise InventoryError("inventory page truncation flag is not an exact boolean")
        object_count = page_row["object_count"]
        if (
            not isinstance(object_count, int)
            or isinstance(object_count, bool)
            or object_count < 0
        ):
            raise InventoryError("inventory page object count is not an exact nonnegative integer")
        page_total += object_count
        next_ref = page_row["next_token_ref"]
        if truncated:
            if object_count == 0:
                raise InventoryError("an empty persisted inventory page cannot continue")
            try:
                next_ref = _require_identity_ref(next_ref, "next_token_ref")
            except ValueError as error:
                raise InventoryError(str(error)) from error
            if (
                next_ref == request_ref
                or next_ref in issued_token_refs
                or next_ref in consumed_token_refs
            ):
                raise InventoryError("inventory next token repeats, self-loops, or cycles")
            issued_token_refs.add(next_ref)
            expected_request = next_ref
        else:
            if number != len(pages) or next_ref is not None:
                raise InventoryError("inventory terminal token closure is invalid")
            expected_request = None
    if (
        pages[-1]["is_truncated"] is not False
        or pages[-1]["next_token_ref"] is not None
        or expected_request is not None
        or issued_token_refs != consumed_token_refs
    ):
        raise InventoryError("inventory pagination did not reach an exact consumed terminal")
    rows = receipt["objects"]
    if not isinstance(rows, list):
        raise InventoryError("inventory objects is not an array")
    if (
        receipt["object_count"] != len(rows)
        or receipt["authoritative_denominator"] != len(rows)
        or page_total != len(rows)
    ):
        raise InventoryError("inventory authoritative denominator mismatch")
    expected_count_fields = {
        member.value for member in KeyClass if member is not KeyClass.UNKNOWN
    }
    if (
        not isinstance(receipt["class_counts"], dict)
        or set(receipt["class_counts"]) != expected_count_fields
        or any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in receipt["class_counts"].values()
        )
    ):
        raise InventoryError("inventory class counts are not exact nonnegative integers")
    objects: dict[str, dict[str, Any]] = {}
    previous: str | None = None
    counts = {member.value: 0 for member in KeyClass if member is not KeyClass.UNKNOWN}
    for number, row in enumerate(rows, start=1):
        if not isinstance(row, dict) or set(row) != {"key", "key_class", "preimage"}:
            raise InventoryError(f"inventory object {number} schema is not exact")
        key = row["key"]
        if not isinstance(key, str) or (previous is not None and key <= previous):
            raise InventoryError("inventory keys are duplicate or not canonical-sorted")
        previous = key
        key_class = classify_key(key, authoritative_area_ids=area_registry_set)
        if key_class is KeyClass.UNKNOWN or row["key_class"] != key_class.value:
            raise InventoryError(f"inventory key class/registry mismatch for {key!r}")
        preimage = _normalize_preimage(row["preimage"], f"inventory object {number}")
        if row["preimage"] != preimage:
            raise InventoryError(f"inventory object {number} preimage is not canonical")
        objects[key] = preimage
        counts[key_class.value] += 1
    if set(GLOBAL_OBJECTS) - set(objects):
        raise InventoryError("inventory omits one or more protected objects")
    if receipt["class_counts"] != counts:
        raise InventoryError("inventory class-count closure mismatch")
    return objects


def inventory_objects(receipt: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return validate_inventory_receipt(receipt)


def inventory_area_ids(receipt: dict[str, Any]) -> frozenset[str]:
    validate_inventory_receipt(receipt)
    return frozenset(receipt["area_registry_ids"])


def _file_digest(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def local_object(
    key: str,
    path: Path | str,
    *,
    logical_path: str | None = None,
    authoritative_area_ids: set[str] | frozenset[str] | None = None,
) -> dict[str, Any]:
    key_class = classify_key(key, authoritative_area_ids=authoritative_area_ids)
    if key_class not in {KeyClass.AREA_GEOM, KeyClass.AREA_SILHOUETTE, KeyClass.GLOBAL}:
        raise ManifestError(f"local producer key is outside the release contract: {key!r}")
    source = Path(path)
    if source.is_symlink() or not source.is_file():
        raise ManifestError(f"local producer object is not a regular file: {source}")
    size = source.stat().st_size
    if size <= 0:
        raise ManifestError(f"local producer object is empty: {source}")
    return {
        "key": key,
        "key_class": key_class.value,
        "local_path": logical_path if logical_path is not None else str(source),
        "bytes": size,
        "md5": _file_digest(source, "md5"),
        "sha256": _file_digest(source, "sha256"),
    }


def build_upload_manifest(
    inventory_receipt: dict[str, Any],
    local_objects: Sequence[dict[str, Any]],
    producer_population: dict[str, Any],
    *,
    source_sha: str,
    source_tree: str,
    workflow_sha256: str,
    root_approval_ref: str,
    prior_anchor_ref: str,
    publication_area_id: str | None = None,
    semantic_deltas: Sequence[dict[str, Any]] = (),
) -> dict[str, Any]:
    remote = validate_inventory_receipt(inventory_receipt)
    area_registry = inventory_area_ids(inventory_receipt)
    source_sha = _require_git_identity(source_sha, "source_sha")
    source_tree = _require_git_identity(source_tree, "source_tree")
    try:
        workflow_sha256 = _require_digest(workflow_sha256, "workflow_sha256")
        root_approval_ref = _require_identity_ref(root_approval_ref, "root_approval_ref")
        prior_anchor_ref = _require_identity_ref(prior_anchor_ref, "prior_anchor_ref")
    except ValueError as error:
        raise ManifestError(str(error)) from error
    if prior_anchor_ref != inventory_receipt["anchor_ref"]:
        raise ManifestError("manifest prior anchor does not match inventory receipt")
    if publication_area_id is not None:
        if publication_area_id != MOLS_AREA_ID:
            raise ManifestError("scoped publication is authorized only for exact Mols area")
        if publication_area_id not in area_registry:
            raise ManifestError("scoped publication area is absent from the authoritative registry")
    population = validate_producer_population(
        producer_population, inventory_receipt=inventory_receipt
    )
    if population["source_tree"] != source_tree:
        raise ManifestError("producer population names a different source tree")
    by_key = _validate_producer_rows(
        sorted((copy.deepcopy(row) for row in local_objects), key=lambda row: row.get("key", "")),
        authoritative_area_ids=area_registry,
    )
    expected_by_key = {row["key"]: row for row in population["objects"]}
    if by_key != expected_by_key:
        raise ManifestError("local producer rows differ from the independent population receipt")
    rows: list[dict[str, Any]] = []
    for key in sorted(by_key):
        local = by_key[key]
        preimage = remote.get(key)
        byte_identical = (
            preimage is not None
            and preimage["sha256"] == local["sha256"]
            and preimage["bytes"] == local["bytes"]
        )
        if byte_identical and (
            publication_area_id is not None
            or classify_key(key, authoritative_area_ids=area_registry) is not KeyClass.GLOBAL
        ):
            continue
        rows.append({
            **local,
            "conditional_mode": "IF_MATCH" if preimage is not None else "IF_NONE_MATCH",
            "remote_preimage": copy.deepcopy(preimage),
        })
    if publication_area_id is not None:
        changed_keys = {row["key"] for row in rows}
        forbidden = changed_keys - set(MOLS_FORWARD_KEYS)
        if forbidden:
            raise ManifestError(
                f"scoped Mols publication found out-of-scope changed keys: {sorted(forbidden)[:12]}"
            )
        if rows and changed_keys != set(MOLS_FORWARD_KEYS):
            missing = sorted(set(MOLS_FORWARD_KEYS) - changed_keys)
            raise ManifestError(
                f"scoped Mols publication must be exact five-key or zero-write: missing={missing}"
            )
        for row in rows:
            if row["conditional_mode"] == "IF_NONE_MATCH" and row["key"] not in MOLS_OBJECT_KEYS:
                raise ManifestError("only the exact Mols geom and silhouette may be created")
        if any(row["conditional_mode"] == "IF_NONE_MATCH" for row in rows) and "index.json" not in changed_keys:
            raise ManifestError("first-time Mols objects require the canonical index change")
        order = {key: index for index, key in enumerate(MOLS_FORWARD_KEYS)}
        rows.sort(key=lambda row: order[row["key"]])
    manifest: dict[str, Any] = {
        "schema": UPLOAD_MANIFEST_SCHEMA,
        "contract_version": CONTRACT_VERSION,
        "bucket": BUCKET,
        "publication_area_id": publication_area_id,
        "source_sha": source_sha,
        "source_tree": source_tree,
        "workflow_sha256": workflow_sha256,
        "root_approval_ref": root_approval_ref,
        "account_identity_ref": inventory_receipt["account_identity_ref"],
        "endpoint_identity_ref": inventory_receipt["endpoint_identity_ref"],
        "prior_anchor_ref": prior_anchor_ref,
        "inventory_anchor_ref": inventory_receipt["anchor_ref"],
        "inventory_receipt_sha256": inventory_receipt["receipt_sha256"],
        "authoritative_denominator": inventory_receipt["authoritative_denominator"],
        "area_registry_sha256": inventory_receipt["area_registry_sha256"],
        "producer_population": copy.deepcopy(population),
        "producer_population_sha256": population["receipt_sha256"],
        "semantic_deltas": copy.deepcopy(list(semantic_deltas)),
        "object_count": len(rows),
        "objects": rows,
        "all_or_none": True,
        "recursive_discovery": False,
    }
    manifest["manifest_sha256"] = canonical_sha256(manifest)
    validate_upload_manifest(manifest, inventory_receipt=inventory_receipt)
    return manifest


def _validate_producer_rows(
    rows: Any,
    *,
    authoritative_area_ids: set[str] | frozenset[str],
) -> dict[str, dict[str, Any]]:
    if not isinstance(rows, list) or not rows:
        raise ManifestError("producer population is empty or invalid")
    producers: dict[str, dict[str, Any]] = {}
    local_paths: set[str] = set()
    previous: str | None = None
    for number, row in enumerate(rows, start=1):
        if not isinstance(row, dict) or set(row) != {
            "key", "key_class", "local_path", "bytes", "md5", "sha256"
        }:
            raise ManifestError(f"producer object {number} schema is not exact")
        key = row["key"]
        key_class = classify_key(key, authoritative_area_ids=authoritative_area_ids)
        if (
            not isinstance(key, str)
            or (previous is not None and key <= previous)
            or key_class not in {KeyClass.AREA_GEOM, KeyClass.AREA_SILHOUETTE, KeyClass.GLOBAL}
            or row["key_class"] != key_class.value
        ):
            raise ManifestError("producer keys are duplicate, unsorted, unclassified, or misclassified")
        previous = key
        local_path = row["local_path"]
        if (
            not isinstance(local_path, str)
            or not local_path
            or local_path in local_paths
            or not isinstance(row["bytes"], int)
            or isinstance(row["bytes"], bool)
            or row["bytes"] <= 0
            or not isinstance(row["md5"], str)
            or not re.fullmatch(r"[0-9a-f]{32}", row["md5"])
            or not isinstance(row["sha256"], str)
            or not _SHA256_RE.fullmatch(row["sha256"])
        ):
            raise ManifestError(f"producer object {number} local identity is invalid")
        local_paths.add(local_path)
        producers[key] = copy.deepcopy(row)
    if {
        key for key in producers
        if classify_key(key, authoritative_area_ids=authoritative_area_ids) is KeyClass.GLOBAL
    } != set(GLOBAL_OBJECTS):
        raise ManifestError("producer population does not contain exactly the four global objects")
    return producers


def build_producer_population(
    local_objects: Sequence[dict[str, Any]],
    *,
    source_tree: str,
    authoritative_area_ids: set[str] | frozenset[str],
    published_area_ids: set[str] | frozenset[str],
) -> dict[str, Any]:
    source_tree = _require_git_identity(source_tree, "producer source_tree")
    registry = _canonical_area_ids(authoritative_area_ids, "producer area registry")
    published = _canonical_area_ids(published_area_ids, "published area registry")
    registry_set = frozenset(registry)
    if not set(published).issubset(registry_set):
        raise ManifestError("published area set is outside the authoritative area registry")
    rows = sorted((copy.deepcopy(row) for row in local_objects), key=lambda row: row.get("key", ""))
    producers = _validate_producer_rows(rows, authoritative_area_ids=registry_set)
    expected_keys = set(GLOBAL_OBJECTS)
    expected_keys.update(f"{area_id}.json" for area_id in published)
    expected_keys.update(f"silhouettes/{area_id}.json" for area_id in published)
    if set(producers) != expected_keys:
        missing = sorted(expected_keys - set(producers))
        extra = sorted(set(producers) - expected_keys)
        raise ManifestError(
            f"producer population differs from published index: missing={missing[:12]} extra={extra[:12]}"
        )
    receipt: dict[str, Any] = {
        "schema": PRODUCER_POPULATION_SCHEMA,
        "contract_version": CONTRACT_VERSION,
        "source_tree": source_tree,
        "area_registry_count": len(registry),
        "area_registry_ids": list(registry),
        "area_registry_sha256": canonical_sha256(list(registry)),
        "published_area_count": len(published),
        "published_area_ids": list(published),
        "published_area_ids_sha256": canonical_sha256(list(published)),
        "object_count": len(rows),
        "object_keys_sha256": canonical_sha256(sorted(producers)),
        "objects": rows,
        "objects_sha256": canonical_sha256(rows),
    }
    receipt["receipt_sha256"] = canonical_sha256(receipt)
    return validate_producer_population(receipt)


def validate_producer_population(
    receipt: Any,
    *,
    inventory_receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    fields = {
        "schema", "contract_version", "source_tree", "area_registry_count",
        "area_registry_ids", "area_registry_sha256", "published_area_count",
        "published_area_ids", "published_area_ids_sha256", "object_count",
        "object_keys_sha256", "objects", "objects_sha256", "receipt_sha256",
    }
    if not isinstance(receipt, dict) or set(receipt) != fields:
        raise ManifestError("producer population receipt schema is not exact")
    if receipt["schema"] != PRODUCER_POPULATION_SCHEMA or receipt["contract_version"] != CONTRACT_VERSION:
        raise ManifestError("producer population receipt version mismatch")
    _require_git_identity(receipt["source_tree"], "producer source_tree")
    registry = _canonical_area_ids(receipt["area_registry_ids"], "producer area registry")
    published = _canonical_area_ids(receipt["published_area_ids"], "published area registry")
    if (
        not registry
        or not published
        or receipt["area_registry_count"] != len(registry)
        or receipt["area_registry_sha256"] != canonical_sha256(list(registry))
        or receipt["published_area_count"] != len(published)
        or receipt["published_area_ids_sha256"] != canonical_sha256(list(published))
        or not set(published).issubset(set(registry))
    ):
        raise ManifestError("producer registry/index closure mismatch")
    registry_set = frozenset(registry)
    producers = _validate_producer_rows(
        receipt["objects"], authoritative_area_ids=registry_set
    )
    expected_keys = set(GLOBAL_OBJECTS)
    expected_keys.update(f"{area_id}.json" for area_id in published)
    expected_keys.update(f"silhouettes/{area_id}.json" for area_id in published)
    if set(producers) != expected_keys:
        raise ManifestError("producer population is jointly omissible or has extra outputs")
    if (
        receipt["object_count"] != len(producers)
        or receipt["object_keys_sha256"] != canonical_sha256(sorted(producers))
        or receipt["objects_sha256"] != canonical_sha256(receipt["objects"])
        or receipt["receipt_sha256"] != canonical_sha256(receipt, omit={"receipt_sha256"})
    ):
        raise ManifestError("producer population count/digest closure mismatch")
    if inventory_receipt is not None:
        validate_inventory_receipt(inventory_receipt)
        if (
            receipt["area_registry_ids"] != inventory_receipt["area_registry_ids"]
            or receipt["area_registry_sha256"] != inventory_receipt["area_registry_sha256"]
        ):
            raise ManifestError("producer population uses a different authoritative area registry")
    return copy.deepcopy(receipt)


def validate_upload_manifest(
    manifest: Any,
    *,
    inventory_receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        raise ManifestError("upload manifest is not an object")
    fields = {
        "schema", "contract_version", "bucket", "publication_area_id", "source_sha", "source_tree",
        "workflow_sha256", "root_approval_ref", "account_identity_ref",
        "endpoint_identity_ref", "prior_anchor_ref", "inventory_anchor_ref",
        "inventory_receipt_sha256", "authoritative_denominator",
        "area_registry_sha256", "producer_population",
        "producer_population_sha256", "semantic_deltas", "object_count", "objects", "all_or_none",
        "recursive_discovery", "manifest_sha256",
    }
    if set(manifest) != fields:
        raise ManifestError("upload manifest schema is not exact")
    if (
        manifest["schema"] != UPLOAD_MANIFEST_SCHEMA
        or manifest["contract_version"] != CONTRACT_VERSION
        or manifest["bucket"] != BUCKET
    ):
        raise ManifestError("upload manifest identity mismatch")
    publication_area_id = manifest["publication_area_id"]
    if publication_area_id is not None and publication_area_id != MOLS_AREA_ID:
        raise ManifestError("scoped publication is authorized only for exact Mols area")
    _require_git_identity(manifest["source_sha"], "source_sha")
    _require_git_identity(manifest["source_tree"], "source_tree")
    try:
        _require_digest(manifest["workflow_sha256"], "workflow_sha256")
        _require_identity_ref(manifest["root_approval_ref"], "root_approval_ref")
        _require_identity_ref(manifest["account_identity_ref"], "account_identity_ref")
        _require_identity_ref(manifest["endpoint_identity_ref"], "endpoint_identity_ref")
        _require_identity_ref(manifest["prior_anchor_ref"], "prior_anchor_ref")
        _require_identity_ref(manifest["inventory_anchor_ref"], "inventory_anchor_ref")
        _require_digest(manifest["inventory_receipt_sha256"], "inventory_receipt_sha256")
        _require_digest(manifest["area_registry_sha256"], "area_registry_sha256")
        _require_digest(manifest["producer_population_sha256"], "producer_population_sha256")
        digest = _require_digest(manifest["manifest_sha256"], "manifest_sha256")
    except ValueError as error:
        raise ManifestError(str(error)) from error
    if digest != canonical_sha256(manifest, omit={"manifest_sha256"}):
        raise ManifestError("upload manifest digest mismatch")
    if manifest["all_or_none"] is not True or manifest["recursive_discovery"] is not False:
        raise ManifestError("upload manifest weakens exact all-or-none behavior")
    population = validate_producer_population(
        manifest["producer_population"], inventory_receipt=inventory_receipt
    )
    if (
        manifest["producer_population_sha256"] != population["receipt_sha256"]
        or manifest["source_tree"] != population["source_tree"]
        or manifest["area_registry_sha256"] != population["area_registry_sha256"]
    ):
        raise ManifestError("upload manifest producer-population binding mismatch")
    registry = frozenset(population["area_registry_ids"])
    producers = {row["key"]: row for row in population["objects"]}
    rows = manifest["objects"]
    if (
        not isinstance(rows, list)
        or not isinstance(manifest["object_count"], int)
        or isinstance(manifest["object_count"], bool)
        or manifest["object_count"] != len(rows)
    ):
        raise ManifestError("upload manifest object count mismatch")
    remote = validate_inventory_receipt(inventory_receipt) if inventory_receipt is not None else None
    if inventory_receipt is not None:
        expected_bindings = {
            "inventory_receipt_sha256": inventory_receipt["receipt_sha256"],
            "prior_anchor_ref": inventory_receipt["anchor_ref"],
            "inventory_anchor_ref": inventory_receipt["anchor_ref"],
            "account_identity_ref": inventory_receipt["account_identity_ref"],
            "endpoint_identity_ref": inventory_receipt["endpoint_identity_ref"],
            "authoritative_denominator": inventory_receipt["authoritative_denominator"],
            "area_registry_sha256": inventory_receipt["area_registry_sha256"],
        }
        for field, value in expected_bindings.items():
            if manifest[field] != value:
                raise ManifestError(f"upload manifest has wrong {field}")
    keys_in_order = [row.get("key") for row in rows if isinstance(row, dict)]
    if publication_area_id is None:
        if keys_in_order != sorted(keys_in_order):
            raise ManifestError("upload manifest keys are not canonical-sorted")
    else:
        expected_scoped_order = [key for key in MOLS_FORWARD_KEYS if key in keys_in_order]
        if keys_in_order != expected_scoped_order or len(keys_in_order) != len(set(keys_in_order)):
            raise ManifestError("scoped Mols manifest order/scope is not canonical")
    upload_keys: set[str] = set()
    globals_seen: set[str] = set()
    for number, row in enumerate(rows, start=1):
        if not isinstance(row, dict) or set(row) != {
            "key", "key_class", "local_path", "bytes", "md5", "sha256",
            "conditional_mode", "remote_preimage",
        }:
            raise ManifestError(f"upload manifest object {number} schema is not exact")
        key = row["key"]
        if not isinstance(key, str) or key in upload_keys:
            raise ManifestError("upload manifest keys are duplicate or invalid")
        upload_keys.add(key)
        key_class = classify_key(key, authoritative_area_ids=registry)
        if key_class not in {KeyClass.AREA_GEOM, KeyClass.AREA_SILHOUETTE, KeyClass.GLOBAL}:
            raise ManifestError(f"upload manifest contains unregistered key {key!r}")
        if row["key_class"] != key_class.value:
            raise ManifestError(f"upload manifest key class mismatch for {key!r}")
        if key_class is KeyClass.GLOBAL:
            globals_seen.add(key)
        producer = producers.get(key)
        if producer is None or any(
            row[field] != producer[field]
            for field in ("key", "key_class", "local_path", "bytes", "md5", "sha256")
        ):
            raise ManifestError(f"upload manifest object {number} is not bound to its producer record")
        if row["key"] in MOLS_SEMANTIC_GLOBAL_KEYS and row["conditional_mode"] != "IF_MATCH":
            raise ManifestError("Mols semantic globals require exact IF_MATCH baselines")
        if row["conditional_mode"] == "IF_MATCH":
            if row["remote_preimage"] is None:
                raise ManifestError("IF_MATCH row has no remote preimage")
            preimage = _normalize_preimage(row["remote_preimage"], f"manifest object {number}")
            if preimage != row["remote_preimage"]:
                raise ManifestError("manifest remote preimage is not canonical")
            if remote is not None and remote.get(key) != preimage:
                raise ManifestError("manifest remote preimage is stale")
        elif row["conditional_mode"] == "IF_NONE_MATCH":
            if row["remote_preimage"] is not None:
                raise ManifestError("IF_NONE_MATCH row unexpectedly has a preimage")
            if publication_area_id is not None and key not in MOLS_OBJECT_KEYS:
                raise ManifestError("scoped create is limited to the exact two Mols objects")
            if remote is not None and key in remote:
                raise ManifestError("IF_NONE_MATCH row already exists in inventory")
        else:
            raise ManifestError("upload manifest conditional mode is invalid")
    if remote is not None:
        expected_upload_keys = {
            key
            for key, producer in producers.items()
            if (
                key not in remote
                or remote[key]["sha256"] != producer["sha256"]
                or remote[key]["bytes"] != producer["bytes"]
                or (
                    publication_area_id is None
                    and classify_key(key, authoritative_area_ids=registry) is KeyClass.GLOBAL
                )
            )
        }
        if publication_area_id is not None:
            forbidden = expected_upload_keys - set(MOLS_FORWARD_KEYS)
            if forbidden:
                raise ManifestError(
                    f"scoped Mols publication has out-of-scope byte differences: {sorted(forbidden)[:12]}"
                )
        if upload_keys != expected_upload_keys:
            raise ManifestError("upload manifest is not the exact independently derived upload set")
    semantic_values = manifest["semantic_deltas"]
    if not isinstance(semantic_values, list):
        raise ManifestError("upload manifest semantic proof population is invalid")
    if publication_area_id is None:
        if globals_seen != set(GLOBAL_OBJECTS):
            raise ManifestError("upload manifest does not contain all four global objects")
        if semantic_values:
            raise ManifestError("generic upload manifest carries Mols-only semantic proofs")
    else:
        if globals_seen - set(MOLS_MUTABLE_GLOBAL_KEYS):
            raise ManifestError(
                "scoped Mols publication may change only index, trail-search, and trail-shapes"
            )
        expected_semantic_keys = [
            key for key in MOLS_SEMANTIC_GLOBAL_KEYS if key in upload_keys
        ]
        if [
            value.get("key") if isinstance(value, dict) else None
            for value in semantic_values
        ] != expected_semantic_keys:
            raise ManifestError("scoped Mols semantic proof order/scope is not exact")
        validated_semantics = [
            _validate_mols_global_delta(value, manifest)
            for value in semantic_values
        ]
        if validated_semantics != semantic_values:
            raise ManifestError("scoped Mols semantic proofs are noncanonical")
    return copy.deepcopy(manifest)


RELEASE_PREWRITE_BOUNDARIES = (
    "BEFORE_CHECKOUT",
    "AFTER_DETACHED_CHECKOUT",
    "BEFORE_CANDIDATE_CREATION",
    "AFTER_CANDIDATE_CREATION",
    "LEGACY_PRE_PUSH_BOUNDARY_PROHIBITED",
    "BEFORE_SYNCHRONIZATION",
    "PREWRITE",
)


def release_boundaries(manifest: dict[str, Any]) -> tuple[str, ...]:
    validate_upload_manifest(manifest)
    values = list(RELEASE_PREWRITE_BOUNDARIES)
    for index, row in enumerate(manifest["objects"]):
        values.append(f"BEFORE_WRITE:{index}:{row['key']}")
        values.append(f"AFTER_WRITE:{index}:{row['key']}")
    values.append("BEFORE_TERMINAL")
    return tuple(values)


def build_release_preflight(
    manifest: dict[str, Any],
    *,
    execution_id: str,
    observed_preimages: Sequence[dict[str, Any] | None],
    adapter_success_schema: Sequence[str],
    adapter_success_schema_capable: bool,
) -> dict[str, Any]:
    """Build one complete pre-effect snapshot from independently observed state."""
    manifest = validate_upload_manifest(manifest)
    if not isinstance(execution_id, str) or not _SAFE_VERSION_RE.fullmatch(execution_id):
        raise ManifestError("release execution identity is invalid")
    if list(adapter_success_schema) != list(RELEASE_PROVIDER_RESULT_FIELDS):
        raise ManifestError("adapter success-result schema is not exact")
    if not isinstance(adapter_success_schema_capable, bool):
        raise ManifestError("adapter success-schema capability is not boolean")
    if len(observed_preimages) != manifest["object_count"]:
        raise ManifestError("release preflight did not inspect every manifest row")
    rows: list[dict[str, Any]] = []
    for index, (manifest_row, observed_value) in enumerate(
        zip(manifest["objects"], observed_preimages)
    ):
        observed = (
            None
            if observed_value is None
            else _normalize_preimage(observed_value, f"preflight row {index}")
        )
        condition_matches = (
            observed == manifest_row["remote_preimage"]
            if manifest_row["conditional_mode"] == "IF_MATCH"
            else observed is None
        )
        eligible = adapter_success_schema_capable and condition_matches
        refusal = None
        if not adapter_success_schema_capable:
            refusal = "ADAPTER_SUCCESS_SCHEMA_UNPROVEN"
        elif not condition_matches:
            refusal = "PRECONDITION_REFUSED"
        rows.append({
            "index": index,
            "key": manifest_row["key"],
            "conditional_mode": manifest_row["conditional_mode"],
            "expected_row_sha256": canonical_sha256(manifest_row),
            "remote_preimage": copy.deepcopy(manifest_row["remote_preimage"]),
            "observed_preimage": copy.deepcopy(observed),
            "eligible": eligible,
            "refusal": refusal,
        })
    snapshot: dict[str, Any] = {
        "schema": RELEASE_PREFLIGHT_SCHEMA,
        "contract_version": CONTRACT_VERSION,
        "execution_id": execution_id,
        "manifest_sha256": manifest["manifest_sha256"],
        "adapter_success_schema": list(adapter_success_schema),
        "adapter_success_schema_capable": adapter_success_schema_capable,
        "all_rows_checked": True,
        "row_count": len(rows),
        "rows": rows,
    }
    snapshot["preflight_sha256"] = canonical_sha256(snapshot)
    return validate_release_preflight(snapshot, manifest, execution_id=execution_id)


def validate_release_preflight(
    snapshot: Any,
    manifest: dict[str, Any],
    *,
    execution_id: str,
) -> dict[str, Any]:
    """Validate that capability and every condition were frozen before effects."""
    manifest = validate_upload_manifest(manifest)
    fields = {
        "schema", "contract_version", "execution_id", "manifest_sha256",
        "adapter_success_schema", "adapter_success_schema_capable",
        "all_rows_checked", "row_count", "rows", "preflight_sha256",
    }
    if not isinstance(snapshot, dict) or set(snapshot) != fields:
        raise ManifestError("release preflight snapshot schema is not exact")
    if (
        snapshot["schema"] != RELEASE_PREFLIGHT_SCHEMA
        or snapshot["contract_version"] != CONTRACT_VERSION
        or snapshot["execution_id"] != execution_id
        or snapshot["manifest_sha256"] != manifest["manifest_sha256"]
        or snapshot["adapter_success_schema"] != list(RELEASE_PROVIDER_RESULT_FIELDS)
        or not isinstance(snapshot["adapter_success_schema_capable"], bool)
        or snapshot["all_rows_checked"] is not True
        or snapshot["row_count"] != manifest["object_count"]
        or snapshot["preflight_sha256"]
        != canonical_sha256(snapshot, omit={"preflight_sha256"})
    ):
        raise ManifestError("release preflight identity/capability closure mismatch")
    rows = snapshot["rows"]
    if not isinstance(rows, list) or len(rows) != manifest["object_count"]:
        raise ManifestError("release preflight row population is incomplete")
    normalized_rows: list[dict[str, Any]] = []
    for index, (row, manifest_row) in enumerate(
        zip(rows, manifest["objects"])
    ):
        expected_fields = {
            "index", "key", "conditional_mode", "expected_row_sha256",
            "remote_preimage", "observed_preimage", "eligible", "refusal",
        }
        if not isinstance(row, dict) or set(row) != expected_fields:
            raise ManifestError(f"release preflight row {index} schema is not exact")
        observed = (
            None
            if row["observed_preimage"] is None
            else _normalize_preimage(
                row["observed_preimage"], f"preflight row {index} observed state"
            )
        )
        if (
            row["index"] != index
            or row["key"] != manifest_row["key"]
            or row["conditional_mode"] != manifest_row["conditional_mode"]
            or row["expected_row_sha256"] != canonical_sha256(manifest_row)
            or row["remote_preimage"] != manifest_row["remote_preimage"]
            or observed != row["observed_preimage"]
            or not isinstance(row["eligible"], bool)
        ):
            raise ManifestError(f"release preflight row {index} is not transaction-bound")
        condition_matches = (
            observed == manifest_row["remote_preimage"]
            if manifest_row["conditional_mode"] == "IF_MATCH"
            else observed is None
        )
        expected_eligible = (
            snapshot["adapter_success_schema_capable"] and condition_matches
        )
        expected_refusal = None
        if not snapshot["adapter_success_schema_capable"]:
            expected_refusal = "ADAPTER_SUCCESS_SCHEMA_UNPROVEN"
        elif not condition_matches:
            expected_refusal = "PRECONDITION_REFUSED"
        if row["eligible"] is not expected_eligible or row["refusal"] != expected_refusal:
            raise ManifestError(f"release preflight row {index} eligibility is not derived")
        normalized_rows.append(copy.deepcopy(row))
    normalized = copy.deepcopy(snapshot)
    normalized["rows"] = normalized_rows
    return normalized


def _valid_provider_result(
    result: Any,
    row: Mapping[str, Any],
    *,
    attempt_id: str,
) -> dict[str, Any]:
    if not isinstance(result, dict) or set(result) != set(RELEASE_PROVIDER_RESULT_FIELDS):
        raise ManifestError("provider result schema is not exact")
    key = row["key"]
    if result["key"] != key or result["attempt_id"] != attempt_id:
        raise ManifestError("provider result key/attempt identity mismatch")
    etag = _normalize_etag(result["etag"], "provider result")
    if result["bytes"] != row["bytes"] or result["sha256"] != row["sha256"]:
        raise ManifestError("provider result does not independently verify the reviewed bytes")
    for field in ("transaction_id", "attempt_id"):
        if not isinstance(result[field], str) or not _SAFE_VERSION_RE.fullmatch(result[field]):
            raise ManifestError(f"provider result {field} is missing or unsafe")
    return {
        "key": key,
        "etag": etag,
        "bytes": result["bytes"],
        "sha256": result["sha256"],
        "transaction_id": result["transaction_id"],
        "attempt_id": attempt_id,
    }


def _release_terminal(
    manifest: dict[str, Any],
    state: TerminalState,
    results: list[dict[str, Any]],
    *,
    attempted: int,
    observed: int,
    boundary: str | None,
    error: BaseException | None,
    checkpoint_kind: str,
    execution_id: str,
    approval_assertion_sha256: str,
    provider_attestation: dict[str, Any],
    release_preflight: dict[str, Any] | None,
    pending_effect: dict[str, Any] | None,
) -> dict[str, Any]:
    receipt: dict[str, Any] = {
        "schema": RELEASE_RECEIPT_SCHEMA,
        "contract_version": CONTRACT_VERSION,
        "terminal_state": state.value,
        "checkpoint_kind": checkpoint_kind,
        "execution_id": execution_id,
        "approval_assertion_sha256": approval_assertion_sha256,
        "provider_identity_attestation": copy.deepcopy(provider_attestation),
        "release_preflight": copy.deepcopy(release_preflight),
        "release_preflight_sha256": (
            release_preflight["preflight_sha256"]
            if release_preflight is not None else None
        ),
        "bucket": BUCKET,
        "source_sha": manifest["source_sha"],
        "source_tree": manifest["source_tree"],
        "workflow_sha256": manifest["workflow_sha256"],
        "root_approval_ref": manifest["root_approval_ref"],
        "prior_anchor_ref": manifest["prior_anchor_ref"],
        "manifest_sha256": manifest["manifest_sha256"],
        "inventory_receipt_sha256": manifest["inventory_receipt_sha256"],
        "manifest_object_count": manifest["object_count"],
        "attempted_effects": attempted,
        "observed_effects": observed,
        "provider_results": copy.deepcopy(results),
        "provider_results_sha256": canonical_sha256(results),
        "provider_transaction_ids": [row["transaction_id"] for row in results],
        "pending_effect": copy.deepcopy(pending_effect),
        "error_boundary": boundary,
        "error_type": type(error).__name__ if error is not None else None,
        "committed": False,
        "pushed": False,
        "synchronization_dispatched": False,
    }
    receipt["receipt_sha256"] = canonical_sha256(receipt)
    validate_release_receipt(receipt, manifest)
    return receipt


def scoped_attempt_id(
    execution_id: str,
    *,
    phase: str,
    index: int,
    reconciled: bool,
) -> str:
    if not isinstance(execution_id, str) or not _SAFE_VERSION_RE.fullmatch(execution_id):
        raise ManifestError("scoped attempt execution identity is invalid")
    if phase not in {"FORWARD", "ROLLBACK"}:
        raise ManifestError("scoped attempt phase is invalid")
    if not isinstance(index, int) or isinstance(index, bool) or index < 0:
        raise ManifestError("scoped attempt index is invalid")
    operation = "put" if phase == "FORWARD" else "rollback"
    infix = "reconcile-" if reconciled else ""
    return f"{execution_id}-{infix}{operation}-{index}"


def _permitted_scoped_attempt_ids(
    execution_id: str, *, phase: str, index: int
) -> frozenset[str]:
    return frozenset({
        scoped_attempt_id(
            execution_id, phase=phase, index=index, reconciled=False
        ),
        scoped_attempt_id(
            execution_id, phase=phase, index=index, reconciled=True
        ),
    })


def validate_scoped_attempt_id(
    attempt_id: Any,
    *,
    execution_id: str,
    phase: str,
    index: int,
) -> str:
    if attempt_id not in _permitted_scoped_attempt_ids(
        execution_id, phase=phase, index=index
    ):
        raise ManifestError("scoped attempt is foreign to its execution")
    return attempt_id


def scoped_transaction_id(
    *,
    execution_id: str,
    phase: str,
    operation: str,
    index: int,
    result: Mapping[str, Any],
) -> str:
    permitted_operations = {
        "FORWARD": {"PUT_CONDITIONAL"},
        "ROLLBACK": {"RESTORE_IF_MATCH", "DELETE_CREATED_AFTER_EXACT_PROOF"},
    }
    if operation not in permitted_operations.get(phase, set()):
        raise ManifestError("scoped transaction phase/operation is invalid")
    if not isinstance(result, Mapping):
        raise ManifestError("scoped transaction result is invalid")
    attempt_id = validate_scoped_attempt_id(
        result.get("attempt_id"),
        execution_id=execution_id,
        phase=phase,
        index=index,
    )
    if operation == "DELETE_CREATED_AFTER_EXACT_PROOF":
        identity_fields = {
            "attempt_id", "key", "postimage_sha256", "verified_absent"
        }
    else:
        identity_fields = {"attempt_id", "bytes", "etag", "key", "sha256"}
    if not identity_fields.issubset(result):
        raise ManifestError("scoped transaction result identity is incomplete")
    identity = {
        field: copy.deepcopy(result[field]) for field in sorted(identity_fields)
    }
    document = {
        "schema": "trekdex-r2-scoped-transaction-v1",
        "execution_id": execution_id,
        "phase": phase,
        "operation": operation,
        "index": index,
        "identity": identity,
    }
    return "r2-scoped-" + canonical_sha256(document)[:40]


def scoped_journal_authentication(
    receipt: Mapping[str, Any], *, key: bytes | str
) -> str:
    if not isinstance(receipt, Mapping):
        raise ManifestError("scoped journal is not an object")
    payload = copy.deepcopy(dict(receipt))
    payload.pop("journal_authentication", None)
    payload.pop("receipt_sha256", None)
    envelope = {
        "schema": SCOPED_JOURNAL_AUTH_SCHEMA,
        "execution_id": payload.get("execution_id"),
        "manifest_sha256": payload.get("manifest_sha256"),
        "checkpoint": payload,
    }
    try:
        key_bytes = _approval_key_bytes(key)
        message = b"trekdex-r2-scoped-journal-auth-v1\0" + _canonical_bytes(envelope)
    except (TypeError, ValueError) as error:
        raise ManifestError(
            f"scoped journal authentication input is invalid: {error}"
        ) from error
    return hmac.new(key_bytes, message, hashlib.sha256).hexdigest()


def validate_scoped_journal_authentication(
    receipt: Mapping[str, Any], *, key: bytes | str
) -> str:
    observed = (
        receipt.get("journal_authentication")
        if isinstance(receipt, Mapping)
        else None
    )
    try:
        observed = _require_digest(observed, "journal_authentication")
    except ValueError as error:
        raise ManifestError(str(error)) from error
    expected = scoped_journal_authentication(receipt, key=key)
    if not hmac.compare_digest(observed, expected):
        raise ManifestError("scoped journal authentication mismatch")
    return observed


def validate_release_receipt(
    receipt: Any,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    manifest = validate_upload_manifest(manifest)
    if not isinstance(receipt, dict):
        raise ManifestError("release receipt is not an object")
    fields = {
        "schema", "contract_version", "terminal_state", "checkpoint_kind",
        "execution_id", "approval_assertion_sha256", "provider_identity_attestation",
        "release_preflight", "release_preflight_sha256", "bucket", "source_sha",
        "source_tree", "workflow_sha256", "root_approval_ref", "prior_anchor_ref",
        "manifest_sha256", "inventory_receipt_sha256", "manifest_object_count",
        "attempted_effects", "observed_effects", "provider_results",
        "provider_results_sha256", "provider_transaction_ids", "pending_effect",
        "error_boundary", "error_type", "committed", "pushed",
        "synchronization_dispatched", "receipt_sha256",
    }
    if set(receipt) != fields:
        raise ManifestError("release receipt schema is not exact")
    if (
        receipt["schema"] != RELEASE_RECEIPT_SCHEMA
        or receipt["contract_version"] != CONTRACT_VERSION
        or receipt["bucket"] != BUCKET
    ):
        raise ManifestError("release receipt identity mismatch")
    for field in (
        "source_sha", "source_tree", "workflow_sha256", "root_approval_ref",
        "prior_anchor_ref", "manifest_sha256", "inventory_receipt_sha256",
    ):
        if receipt[field] != manifest[field]:
            raise ManifestError(f"release receipt has wrong {field}")
    execution_id = receipt["execution_id"]
    if not isinstance(execution_id, str) or not _SAFE_VERSION_RE.fullmatch(execution_id):
        raise ManifestError("release execution identity is invalid")
    try:
        state = TerminalState(receipt["terminal_state"])
        digest = _require_digest(receipt["receipt_sha256"], "receipt_sha256")
        _require_digest(
            receipt["approval_assertion_sha256"], "approval_assertion_sha256"
        )
        results_digest = _require_digest(
            receipt["provider_results_sha256"], "provider_results_sha256"
        )
    except (TypeError, ValueError) as error:
        raise ManifestError("release receipt state/digest is invalid") from error
    if digest != canonical_sha256(receipt, omit={"receipt_sha256"}):
        raise ManifestError("release receipt digest mismatch")
    attestation = validate_provider_identity_attestation(
        receipt["provider_identity_attestation"],
        expected_account_identity_ref=manifest["account_identity_ref"],
        expected_endpoint_identity_ref=manifest["endpoint_identity_ref"],
    )
    if attestation != receipt["provider_identity_attestation"]:
        raise ManifestError("release provider attestation is noncanonical")

    preflight = receipt["release_preflight"]
    if preflight is None:
        if receipt["release_preflight_sha256"] is not None:
            raise ManifestError("release receipt has a digest without a preflight snapshot")
        validated_preflight = None
    else:
        validated_preflight = validate_release_preflight(
            preflight, manifest, execution_id=execution_id
        )
        if receipt["release_preflight_sha256"] != validated_preflight["preflight_sha256"]:
            raise ManifestError("release receipt preflight digest mismatch")

    results = receipt["provider_results"]
    if not isinstance(results, list) or results_digest != canonical_sha256(results):
        raise ManifestError("release provider-result closure mismatch")
    attempted = receipt["attempted_effects"]
    observed = receipt["observed_effects"]
    if (
        not isinstance(attempted, int) or isinstance(attempted, bool)
        or not isinstance(observed, int) or isinstance(observed, bool)
        or observed != len(results)
        or receipt["manifest_object_count"] != manifest["object_count"]
    ):
        raise ManifestError("release effect counts are inconsistent")
    if attempted and (
        validated_preflight is None
        or not all(row["eligible"] for row in validated_preflight["rows"])
    ):
        raise ManifestError("release effect occurred without an eligible complete preflight")
    normalized_results = [
        _valid_provider_result(
            result,
            manifest["objects"][index],
            attempt_id=result.get("attempt_id") if isinstance(result, dict) else "",
        )
        for index, result in enumerate(results)
    ]
    if normalized_results != results:
        raise ManifestError("release provider results are noncanonical")
    transaction_ids = [result["transaction_id"] for result in results]
    attempt_ids = [result["attempt_id"] for result in results]
    if (
        receipt["provider_transaction_ids"] != transaction_ids
        or len(transaction_ids) != len(set(transaction_ids))
        or len(attempt_ids) != len(set(attempt_ids))
    ):
        raise ManifestError("release transaction/attempt identity closure mismatch")
    pending = receipt["pending_effect"]
    if pending is None:
        if attempted != observed:
            raise ManifestError("release count has no pending effect")
    else:
        if not isinstance(pending, dict) or set(pending) != {
            "index", "key", "operation", "attempt_id", "expected_row_sha256",
            "remote_preimage", "preflight_row_sha256", "preflight_snapshot_sha256",
        }:
            raise ManifestError("release pending-effect schema is not exact")
        index = pending["index"]
        expected_preflight_row = (
            validated_preflight["rows"][index]
            if validated_preflight is not None
            and isinstance(index, int) and not isinstance(index, bool)
            and 0 <= index < manifest["object_count"]
            else None
        )
        if (
            expected_preflight_row is None
            or index != len(results)
            or pending["key"] != manifest["objects"][index]["key"]
            or pending["operation"] != "PUT_CONDITIONAL"
            or pending["expected_row_sha256"] != canonical_sha256(manifest["objects"][index])
            or pending["remote_preimage"] != manifest["objects"][index]["remote_preimage"]
            or pending["preflight_row_sha256"] != canonical_sha256(expected_preflight_row)
            or pending["preflight_snapshot_sha256"] != validated_preflight["preflight_sha256"]
            or expected_preflight_row["eligible"] is not True
            or not isinstance(pending["attempt_id"], str)
            or not _SAFE_VERSION_RE.fullmatch(pending["attempt_id"])
            or pending["attempt_id"] in attempt_ids
            or attempted != observed + 1
        ):
            raise ManifestError("release pending effect is not the exact preflight-bound operation")
    if receipt["checkpoint_kind"] not in {"WRITE_AHEAD", "PROGRESS", "TERMINAL"}:
        raise ManifestError("release checkpoint kind is invalid")
    if receipt["checkpoint_kind"] == "WRITE_AHEAD" and (
        state is not TerminalState.INDETERMINATE or pending is None
    ):
        raise ManifestError("release write-ahead checkpoint is not visibly indeterminate")
    if receipt["checkpoint_kind"] == "PROGRESS" and (
        state is not TerminalState.INDETERMINATE or pending is not None
    ):
        raise ManifestError("release progress checkpoint is malformed")
    for field in ("committed", "pushed", "synchronization_dispatched"):
        if receipt[field] is not False:
            raise ManifestError(f"release receipt illegally sets {field}")
    if state is TerminalState.COMPLETE_SUCCESS:
        if (
            validated_preflight is None
            or not all(row["eligible"] for row in validated_preflight["rows"])
            or receipt["checkpoint_kind"] != "TERMINAL"
            or pending is not None
            or attempted != manifest["object_count"]
            or observed != manifest["object_count"]
            or receipt["error_boundary"] is not None
            or receipt["error_type"] is not None
        ):
            raise ManifestError("COMPLETE_SUCCESS release receipt is incomplete")
    elif state is TerminalState.SAFE_ABORT:
        if (
            receipt["checkpoint_kind"] != "TERMINAL" or pending is not None
            or attempted != 0 or observed != 0
            or not receipt["error_boundary"] or not receipt["error_type"]
        ):
            raise ManifestError("SAFE_ABORT release receipt records an effect or lacks cause")
    elif not receipt["error_boundary"] or not receipt["error_type"]:
        raise ManifestError("INDETERMINATE release receipt lacks its uncertainty boundary")
    return copy.deepcopy(receipt)


def execute_release(
    manifest: dict[str, Any],
    writer: ConditionalWriter,
    *,
    approval_assertion: dict[str, Any],
    approval_key: bytes | str,
    execution_id: str | None = None,
    state_probe: Callable[[str], bool] | None = None,
    checkpoint: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Preflight every row before the first effect; reconciliation never retries."""
    manifest = validate_upload_manifest(manifest)
    approval_digest = validate_approval_assertion(
        approval_assertion, manifest, operation="RELEASE", key=approval_key
    )
    attestation = validate_provider_identity_attestation(
        writer.attest_identity(),
        expected_account_identity_ref=manifest["account_identity_ref"],
        expected_endpoint_identity_ref=manifest["endpoint_identity_ref"],
    )
    execution_id = execution_id or ("release-" + secrets.token_hex(16))
    if not isinstance(execution_id, str) or not _SAFE_VERSION_RE.fullmatch(execution_id):
        raise ManifestError("release execution identity is invalid")
    probe = state_probe or (lambda _boundary: True)
    persist = checkpoint or (lambda _document: None)
    results: list[dict[str, Any]] = []
    preflight: dict[str, Any] | None = None
    attempted = 0
    observed = 0

    def receipt(
        state: TerminalState,
        boundary: str | None,
        error: BaseException | None,
        *,
        kind: str,
        pending: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return _release_terminal(
            manifest, state, results, attempted=attempted, observed=observed,
            boundary=boundary, error=error, checkpoint_kind=kind,
            execution_id=execution_id, approval_assertion_sha256=approval_digest,
            provider_attestation=attestation, release_preflight=preflight,
            pending_effect=pending,
        )

    def terminal(
        boundary: str,
        error: BaseException,
        pending: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        state = TerminalState.SAFE_ABORT if attempted == 0 else TerminalState.INDETERMINATE
        document = receipt(state, boundary, error, kind="TERMINAL", pending=pending)
        try:
            persist(document)
        except Exception:
            pass
        return document

    for prewrite_boundary in RELEASE_PREWRITE_BOUNDARIES:
        if not probe(prewrite_boundary):
            return terminal(prewrite_boundary, RuntimeError("immutable state probe failed"))
    try:
        candidate_preflight = writer.preflight_release(
            copy.deepcopy(manifest), execution_id=execution_id
        )
        preflight = validate_release_preflight(
            candidate_preflight, manifest, execution_id=execution_id
        )
    except Exception as error:
        preflight = None
        return terminal("COMPLETE_PREFLIGHT", error)
    if not all(row["eligible"] for row in preflight["rows"]):
        return terminal(
            "PREFLIGHT_REFUSAL",
            RuntimeError("at least one manifest row is determinably ineligible"),
        )

    for index, row in enumerate(manifest["objects"]):
        before = f"BEFORE_WRITE:{index}:{row['key']}"
        if not probe(before):
            return terminal(before, RuntimeError("immutable state probe failed"))
        attempt_id = f"{execution_id}-put-{index}"
        preflight_row = preflight["rows"][index]
        pending = {
            "index": index,
            "key": row["key"],
            "operation": "PUT_CONDITIONAL",
            "attempt_id": attempt_id,
            "expected_row_sha256": canonical_sha256(row),
            "remote_preimage": copy.deepcopy(row["remote_preimage"]),
            "preflight_row_sha256": canonical_sha256(preflight_row),
            "preflight_snapshot_sha256": preflight["preflight_sha256"],
        }
        attempted += 1
        write_ahead = receipt(
            TerminalState.INDETERMINATE,
            before,
            RuntimeError("provider effect pending reconciliation"),
            kind="WRITE_AHEAD",
            pending=pending,
        )
        try:
            persist(write_ahead)
        except Exception as error:
            attempted -= 1
            return terminal(f"WRITE_AHEAD:{index}", error)
        try:
            raw_result = writer.put_conditional(
                copy.deepcopy(row),
                attempt_id=attempt_id,
                preflight_row=copy.deepcopy(preflight_row),
                preflight_sha256=preflight["preflight_sha256"],
            )
            result = _valid_provider_result(raw_result, row, attempt_id=attempt_id)
            if any(previous["transaction_id"] == result["transaction_id"] for previous in results):
                raise ManifestError("provider transaction identity is duplicated")
            results.append(result)
            observed += 1
        except Exception as error:
            unresolved = pending if attempted == observed + 1 else None
            return terminal(before, error, unresolved)
        after = f"AFTER_WRITE:{index}:{row['key']}"
        if not probe(after):
            return terminal(after, RuntimeError("immutable state probe failed"))
        progress = receipt(
            TerminalState.INDETERMINATE,
            after,
            RuntimeError("release in progress"),
            kind="PROGRESS",
            pending=None,
        )
        try:
            persist(progress)
        except Exception as error:
            return terminal(f"PROGRESS_CHECKPOINT:{index}", error)
    if not probe("BEFORE_TERMINAL"):
        return terminal("BEFORE_TERMINAL", RuntimeError("immutable state probe failed"))
    complete = receipt(
        TerminalState.COMPLETE_SUCCESS, None, None, kind="TERMINAL", pending=None
    )
    try:
        persist(complete)
    except Exception as error:
        return terminal("TERMINAL_RECEIPT", error)
    return complete



def _strict_producer_json(payload: bytes, context: str) -> Any:
    """Parse one producer object and require its exact compact byte framing."""
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ManifestError(f"{context} is not UTF-8") from error
    try:
        document = strict_json_loads(text, context)
    except InventoryError as error:
        raise ManifestError(str(error)) from error
    try:
        canonical = json.dumps(
            document,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ManifestError(f"{context} is not canonical producer JSON") from error
    if payload != canonical:
        raise ManifestError(f"{context} is not in canonical producer byte framing")
    return document


def _strict_mols_geom_trails(payload: bytes) -> list[dict[str, Any]]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ManifestError("candidate Mols geometry is not UTF-8") from error
    try:
        document = strict_json_loads(text, "candidate Mols geometry")
    except InventoryError as error:
        raise ManifestError(str(error)) from error
    trails = document.get("trails") if isinstance(document, dict) else None
    if document.get("id") != MOLS_AREA_ID or not isinstance(trails, list) or not trails:
        raise ManifestError("candidate Mols geometry identity/trails are invalid")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for number, trail in enumerate(trails, start=1):
        if not isinstance(trail, dict):
            raise ManifestError(f"candidate Mols trail {number} is not an object")
        trail_id = trail.get("id")
        if not _is_plain_safe_text(trail_id, maximum=512) or trail_id in seen:
            raise ManifestError(f"candidate Mols trail {number} identity is invalid or duplicate")
        seen.add(trail_id)
        normalized.append(copy.deepcopy(trail))
    return normalized


def _is_safe_display_text(value: Any, *, maximum: int = 1024) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= maximum
        and value == unicodedata.normalize("NFC", value)
        and not any(unicodedata.category(char) in {"Cc", "Cs"} for char in value)
    )


def _mols_shape(segment: Any) -> list[int] | None:
    """Mirror the pinned trail-shapes producer for one [lat, lon] segment."""
    if not isinstance(segment, list) or len(segment) < 2:
        return None
    points: list[tuple[float, float]] = []
    for point in segment:
        if (
            not isinstance(point, list)
            or len(point) != 2
            or any(
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
                for value in point
            )
        ):
            raise ManifestError("candidate Mols trail segment has an invalid point")
        points.append((point[0], point[1]))

    def perpendicular(
        point: tuple[float, float],
        start: tuple[float, float],
        end: tuple[float, float],
    ) -> float:
        if start == end:
            return math.dist(point, start)
        (x0, y0), (x1, y1), (x2, y2) = point, start, end
        numerator = abs((y2 - y1) * x0 - (x2 - x1) * y0 + x2 * y1 - y2 * x1)
        denominator = math.hypot(y2 - y1, x2 - x1)
        return numerator / denominator if denominator else 0.0

    def simplify(
        values: list[tuple[float, float]], epsilon: float
    ) -> list[tuple[float, float]]:
        if len(values) < 3:
            return values
        maximum, split = 0.0, 0
        for index in range(1, len(values) - 1):
            distance = perpendicular(values[index], values[0], values[-1])
            if distance > maximum:
                maximum, split = distance, index
        if maximum > epsilon:
            return simplify(values[: split + 1], epsilon)[:-1] + simplify(
                values[split:], epsilon
            )
        return [values[0], values[-1]]

    center_latitude = sum(point[0] for point in points) / len(points)
    scale = math.cos(math.radians(center_latitude))
    projected = [(point[1] * scale, point[0]) for point in points]
    xs = [point[0] for point in projected]
    ys = [point[1] for point in projected]
    diagonal = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
    simplified = simplify(projected, diagonal * 0.02)
    if len(simplified) > 40:
        simplified = simplify(projected, diagonal * 0.04)
    minimum_x, minimum_y = min(xs), min(ys)
    span = max(max(xs) - minimum_x, max(ys) - minimum_y, 1e-9)
    flattened: list[int] = []
    for x, y in simplified:
        flattened.extend(
            (round((x - minimum_x) / span * 255), round((y - minimum_y) / span * 255))
        )
    return flattened


def _strict_trail_search_records(payload: bytes, context: str) -> list[list[Any]]:
    document = _strict_producer_json(payload, context)
    if not isinstance(document, list):
        raise ManifestError(f"{context} is not an array")
    records: list[list[Any]] = []
    identities: set[tuple[str, str]] = set()
    for number, row in enumerate(document, start=1):
        if (
            not isinstance(row, list)
            or len(row) != 5
            or not _is_safe_display_text(row[0], maximum=1024)
            or not _is_plain_safe_text(row[1], maximum=256)
            or not _is_plain_safe_text(row[2], maximum=512)
            or (
                row[3] is not None
                and (
                    not isinstance(row[3], (int, float))
                    or isinstance(row[3], bool)
                    or not math.isfinite(row[3])
                    or row[3] < 0
                )
            )
            or (
                row[4] is not None
                and not _is_safe_display_text(row[4], maximum=128)
            )
        ):
            raise ManifestError(f"{context} record {number} is invalid")
        identity = (row[1], row[2])
        if identity in identities:
            raise ManifestError(f"{context} has duplicate trail identity {identity!r}")
        identities.add(identity)
        records.append(copy.deepcopy(row))
    return records


def _strict_trail_shape_records(payload: bytes, context: str) -> list[list[Any]]:
    document = _strict_producer_json(payload, context)
    if not isinstance(document, dict):
        raise ManifestError(f"{context} is not an object")
    records: list[list[Any]] = []
    for number, (identity, shape) in enumerate(document.items(), start=1):
        if (
            not _is_plain_safe_text(identity, maximum=1024)
            or "/" not in identity
            or identity.startswith("/")
            or identity.endswith("/")
            or not isinstance(shape, list)
            or len(shape) < 4
            or len(shape) % 2
            or any(
                not isinstance(value, int)
                or isinstance(value, bool)
                or not 0 <= value <= 255
                for value in shape
            )
        ):
            raise ManifestError(f"{context} record {number} is invalid")
        records.append([identity, copy.deepcopy(shape)])
    return records


def _expected_mols_global_records(
    key: str, mols_geom: bytes
) -> tuple[list[list[Any]], list[str]]:
    trails = _strict_mols_geom_trails(mols_geom)
    records: list[list[Any]] = []
    trail_ids: list[str] = []
    for trail in trails:
        trail_id = trail["id"]
        if key == "trail-search.json":
            name = trail.get("name")
            if not _is_safe_display_text(name, maximum=1024):
                raise ManifestError("candidate Mols search trail has no qualified name")
            distance = trail.get("distanceMi")
            difficulty = trail.get("difficulty")
            record = [name, MOLS_AREA_ID, trail_id, distance, difficulty]
            _strict_trail_search_records(
                json.dumps([record], ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("utf-8"),
                "expected Mols trail-search record",
            )
        elif key == "trail-shapes.json":
            segments = trail.get("segments") or []
            if not isinstance(segments, list) or not segments:
                raise ManifestError("candidate Mols shape trail has no qualified segment")
            if any(not isinstance(segment, list) for segment in segments):
                raise ManifestError("candidate Mols shape trail segments are invalid")
            shape = _mols_shape(max(segments, key=len))
            if shape is None:
                raise ManifestError("candidate Mols shape trail has no drawable segment")
            record = [f"{MOLS_AREA_ID}/{trail_id}", shape]
            _strict_trail_shape_records(
                json.dumps({record[0]: record[1]}, separators=(",", ":")).encode("utf-8"),
                "expected Mols trail-shapes record",
            )
        else:
            raise ManifestError("Mols semantic delta key is outside the exact global allowlist")
        records.append(record)
        trail_ids.append(trail_id)
    return records, trail_ids


def build_mols_global_delta(
    key: str,
    before: bytes,
    after: bytes,
    mols_geom: bytes,
    *,
    baseline_etag: str,
) -> dict[str, Any]:
    """Prove one global changes only by deterministic Mols record additions."""
    if key not in MOLS_SEMANTIC_GLOBAL_KEYS:
        raise ManifestError("Mols semantic delta key is outside the exact global allowlist")
    try:
        normalized_etag = _normalize_etag(baseline_etag, f"{key} baseline")
    except InventoryError as error:
        raise ManifestError(f"{key} baseline ETag is invalid") from error
    if key == "trail-search.json":
        baseline_records = _strict_trail_search_records(before, "baseline trail-search")
        candidate_records = _strict_trail_search_records(after, "candidate trail-search")
        target = lambda row: row[1] == MOLS_AREA_ID
        semantic_kind = "ORDERED_SEARCH_RECORDS"
    else:
        baseline_records = _strict_trail_shape_records(before, "baseline trail-shapes")
        candidate_records = _strict_trail_shape_records(after, "candidate trail-shapes")
        target_prefix = f"{MOLS_AREA_ID}/"
        target = lambda row: row[0].startswith(target_prefix)
        semantic_kind = "ORDERED_SHAPE_RECORDS"
    baseline_target = [row for row in baseline_records if target(row)]
    baseline_other = [row for row in baseline_records if not target(row)]
    candidate_target = [row for row in candidate_records if target(row)]
    candidate_other = [row for row in candidate_records if not target(row)]
    expected_target, expected_ids = _expected_mols_global_records(key, mols_geom)
    if candidate_other != baseline_other:
        raise ManifestError(f"candidate {key} modifies, removes, adds, or reorders a non-Mols record")
    if candidate_target != expected_target:
        raise ManifestError(f"candidate {key} is not the exact deterministic Mols addition")
    if len(candidate_target) != len(_strict_mols_geom_trails(mols_geom)):
        raise ManifestError(f"candidate {key} does not qualify every Mols trail exactly once")
    receipt: dict[str, Any] = {
        "schema": "trekdex-r2-mols-global-delta-v1",
        "area_id": MOLS_AREA_ID,
        "key": key,
        "semantic_kind": semantic_kind,
        "baseline_identity": {
            "etag": normalized_etag,
            "bytes": len(before),
            "sha256": hashlib.sha256(before).hexdigest(),
        },
        "candidate_identity": {
            "bytes": len(after),
            "sha256": hashlib.sha256(after).hexdigest(),
        },
        "mols_geom_identity": {
            "bytes": len(mols_geom),
            "sha256": hashlib.sha256(mols_geom).hexdigest(),
        },
        "baseline_record_count": len(baseline_records),
        "candidate_record_count": len(candidate_records),
        "non_mols_record_count": len(baseline_other),
        "transition_kind": "ADD" if not baseline_target else "REPLACE",
        "baseline_mols_record_count": len(baseline_target),
        "baseline_mols_records_sha256": canonical_sha256(baseline_target),
        "candidate_mols_record_count": len(candidate_target),
        "candidate_mols_record_ids": expected_ids,
        "candidate_mols_record_ids_sha256": canonical_sha256(expected_ids),
        "non_mols_records_sha256": canonical_sha256(baseline_other),
        "candidate_mols_records_sha256": canonical_sha256(candidate_target),
        "non_mols_identity_and_content_preserved": True,
        "only_expected_mols_records_present": True,
    }
    receipt["receipt_sha256"] = canonical_sha256(receipt)
    return receipt


def _validate_mols_global_delta(
    receipt: Any,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    fields = {
        "schema", "area_id", "key", "semantic_kind", "baseline_identity",
        "candidate_identity", "mols_geom_identity", "baseline_record_count",
        "candidate_record_count", "non_mols_record_count", "transition_kind",
        "baseline_mols_record_count", "baseline_mols_records_sha256",
        "candidate_mols_record_count", "candidate_mols_record_ids",
        "candidate_mols_record_ids_sha256", "non_mols_records_sha256",
        "candidate_mols_records_sha256", "non_mols_identity_and_content_preserved",
        "only_expected_mols_records_present", "receipt_sha256",
    }
    if not isinstance(receipt, dict) or set(receipt) != fields:
        raise ManifestError("Mols global semantic delta schema is not exact")
    key = receipt["key"]
    expected_kind = {
        "trail-search.json": "ORDERED_SEARCH_RECORDS",
        "trail-shapes.json": "ORDERED_SHAPE_RECORDS",
    }.get(key)
    if (
        receipt["schema"] != "trekdex-r2-mols-global-delta-v1"
        or receipt["area_id"] != MOLS_AREA_ID
        or expected_kind is None
        or receipt["semantic_kind"] != expected_kind
        or receipt["non_mols_identity_and_content_preserved"] is not True
        or receipt["only_expected_mols_records_present"] is not True
        or receipt["transition_kind"] not in {"ADD", "REPLACE"}
    ):
        raise ManifestError("Mols global semantic delta identity/proof is invalid")
    rows = {row["key"]: row for row in manifest["objects"]}
    row = rows.get(key)
    population = {
        item["key"]: item for item in manifest["producer_population"]["objects"]
    }
    geom = population.get(MOLS_OBJECT_KEYS[0])
    if row is None or geom is None or row["conditional_mode"] != "IF_MATCH":
        raise ManifestError("Mols global semantic delta is not bound to an IF_MATCH row")
    if receipt["baseline_identity"] != row["remote_preimage"]:
        raise ManifestError("Mols global semantic delta baseline differs from manifest preimage")
    if receipt["candidate_identity"] != {
        "bytes": row["bytes"], "sha256": row["sha256"]
    }:
        raise ManifestError("Mols global semantic delta candidate differs from manifest row")
    if receipt["mols_geom_identity"] != {
        "bytes": geom["bytes"], "sha256": geom["sha256"]
    }:
        raise ManifestError("Mols global semantic delta uses a different Mols geometry")
    counts = (
        receipt["baseline_record_count"],
        receipt["candidate_record_count"],
        receipt["non_mols_record_count"],
        receipt["baseline_mols_record_count"],
        receipt["candidate_mols_record_count"],
    )
    if (
        any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in counts)
        or receipt["candidate_mols_record_count"] <= 0
        or receipt["baseline_record_count"]
        != receipt["non_mols_record_count"] + receipt["baseline_mols_record_count"]
        or receipt["candidate_record_count"]
        != receipt["non_mols_record_count"] + receipt["candidate_mols_record_count"]
        or (receipt["transition_kind"] == "ADD" and receipt["baseline_mols_record_count"] != 0)
        or (receipt["transition_kind"] == "REPLACE" and receipt["baseline_mols_record_count"] <= 0)
    ):
        raise ManifestError("Mols global semantic delta record counts are inconsistent")
    ids = receipt["candidate_mols_record_ids"]
    if (
        not isinstance(ids, list)
        or len(ids) != receipt["candidate_mols_record_count"]
        or len(ids) != len(set(ids))
        or any(not _is_plain_safe_text(value, maximum=512) for value in ids)
    ):
        raise ManifestError("Mols global semantic delta record identities are invalid")
    for field in (
        "baseline_mols_records_sha256", "candidate_mols_record_ids_sha256",
        "non_mols_records_sha256", "candidate_mols_records_sha256",
        "receipt_sha256",
    ):
        try:
            _require_digest(receipt[field], field)
        except ValueError as error:
            raise ManifestError(str(error)) from error
    if receipt["candidate_mols_record_ids_sha256"] != canonical_sha256(ids):
        raise ManifestError("Mols global semantic delta record identity digest mismatch")
    if receipt["receipt_sha256"] != canonical_sha256(
        receipt, omit={"receipt_sha256"}
    ):
        raise ManifestError("Mols global semantic delta digest mismatch")
    return copy.deepcopy(receipt)

def _strict_index_rows(payload: bytes, context: str) -> list[list[Any]]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ManifestError(f"{context} is not UTF-8") from error
    try:
        document = strict_json_loads(text, context)
    except InventoryError as error:
        raise ManifestError(str(error)) from error
    if not isinstance(document, list):
        raise ManifestError(f"{context} is not an array")
    canonical = json.dumps(
        document,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if payload != canonical:
        raise ManifestError(f"{context} is not in canonical producer byte framing")
    rows: list[list[Any]] = []
    seen: set[str] = set()
    for number, row in enumerate(document, start=1):
        if (
            not isinstance(row, list)
            or not row
            or not _is_plain_safe_text(row[0], maximum=256)
            or row[0] in seen
        ):
            raise ManifestError(f"{context} row {number} has an invalid or duplicate area ID")
        seen.add(row[0])
        rows.append(copy.deepcopy(row))
    return rows


def build_mols_index_delta(before: bytes, after: bytes) -> dict[str, Any]:
    """Prove that the canonical index changes only the exact Mols row."""
    before_rows = _strict_index_rows(before, "baseline canonical index")
    after_rows = _strict_index_rows(after, "candidate canonical index")
    before_target = [row for row in before_rows if row[0] == MOLS_AREA_ID]
    after_target = [row for row in after_rows if row[0] == MOLS_AREA_ID]
    before_other = [row for row in before_rows if row[0] != MOLS_AREA_ID]
    after_other = [row for row in after_rows if row[0] != MOLS_AREA_ID]
    if len(before_target) > 1 or len(after_target) != 1:
        raise ManifestError("canonical index does not contain exactly one candidate Mols row")
    if before_other != after_other:
        raise ManifestError("canonical index changes a non-Mols entry or its order")
    if before_target == after_target:
        raise ManifestError("canonical index Mols row is byte-semantically unchanged")
    receipt: dict[str, Any] = {
        "schema": "trekdex-r2-mols-index-delta-v1",
        "area_id": MOLS_AREA_ID,
        "baseline_row_count": len(before_rows),
        "candidate_row_count": len(after_rows),
        "baseline_target": before_target[0] if before_target else None,
        "candidate_target": after_target[0],
        "non_target_rows_sha256": canonical_sha256(before_other),
        "non_target_rows_equal": True,
    }
    receipt["receipt_sha256"] = canonical_sha256(receipt)
    return receipt


def _validate_mols_index_delta(receipt: Any) -> dict[str, Any]:
    fields = {
        "schema", "area_id", "baseline_row_count", "candidate_row_count",
        "baseline_target", "candidate_target", "non_target_rows_sha256",
        "non_target_rows_equal", "receipt_sha256",
    }
    if not isinstance(receipt, dict) or set(receipt) != fields:
        raise ManifestError("Mols index delta schema is not exact")
    if (
        receipt["schema"] != "trekdex-r2-mols-index-delta-v1"
        or receipt["area_id"] != MOLS_AREA_ID
        or receipt["non_target_rows_equal"] is not True
        or not isinstance(receipt["baseline_row_count"], int)
        or isinstance(receipt["baseline_row_count"], bool)
        or not isinstance(receipt["candidate_row_count"], int)
        or isinstance(receipt["candidate_row_count"], bool)
        or receipt["baseline_row_count"] < 0
        or receipt["candidate_row_count"] < 1
        or receipt["candidate_row_count"] - receipt["baseline_row_count"] not in {0, 1}
    ):
        raise ManifestError("Mols index delta identity/count is invalid")
    try:
        _require_digest(receipt["non_target_rows_sha256"], "non_target_rows_sha256")
        digest = _require_digest(receipt["receipt_sha256"], "receipt_sha256")
    except ValueError as error:
        raise ManifestError(str(error)) from error
    if digest != canonical_sha256(receipt, omit={"receipt_sha256"}):
        raise ManifestError("Mols index delta digest mismatch")
    baseline_target = receipt["baseline_target"]
    candidate_target = receipt["candidate_target"]
    if (
        baseline_target is not None
        and (not isinstance(baseline_target, list) or not baseline_target or baseline_target[0] != MOLS_AREA_ID)
    ):
        raise ManifestError("Mols baseline index row is invalid")
    if not isinstance(candidate_target, list) or not candidate_target or candidate_target[0] != MOLS_AREA_ID:
        raise ManifestError("Mols candidate index row is invalid")
    if baseline_target == candidate_target:
        raise ManifestError("Mols index delta does not change the target row")
    return copy.deepcopy(receipt)


def build_rollback_manifest(
    manifest: dict[str, Any],
    *,
    baseline_objects: Sequence[dict[str, Any] | None],
    index_delta: dict[str, Any] | None,
    semantic_deltas: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    manifest = validate_upload_manifest(manifest)
    if manifest["publication_area_id"] != MOLS_AREA_ID:
        raise ManifestError("rollback manifest requires exact Mols publication scope")
    if len(baseline_objects) != manifest["object_count"]:
        raise ManifestError("rollback baseline population is incomplete")
    semantic_by_key: dict[str, dict[str, Any]] = {}
    for value in semantic_deltas:
        validated_delta = _validate_mols_global_delta(value, manifest)
        key = validated_delta["key"]
        if key in semantic_by_key:
            raise ManifestError("rollback manifest repeats a Mols global semantic proof")
        semantic_by_key[key] = validated_delta
    expected_semantic_keys = {
        row["key"] for row in manifest["objects"]
        if row["key"] in MOLS_SEMANTIC_GLOBAL_KEYS
    }
    if set(semantic_by_key) != expected_semantic_keys:
        raise ManifestError("rollback manifest semantic proof population is incomplete or extra")
    if list(semantic_by_key.values()) != manifest["semantic_deltas"]:
        raise ManifestError("apply-time Mols semantic proofs differ from reviewed manifest")
    rows: list[dict[str, Any]] = []
    for index, (forward, baseline) in enumerate(zip(manifest["objects"], baseline_objects)):
        if forward["conditional_mode"] == "IF_MATCH":
            fields = {"local_path", "bytes", "md5", "sha256", "etag"}
            if not isinstance(baseline, dict) or set(baseline) != fields:
                raise ManifestError("IF_MATCH rollback row lacks exact frozen baseline bytes")
            relative = Path(baseline["local_path"])
            if relative.is_absolute() or ".." in relative.parts or not relative.parts:
                raise ManifestError("rollback baseline path is unsafe")
            remote = forward["remote_preimage"]
            if (
                baseline["bytes"] != remote["bytes"]
                or baseline["sha256"] != remote["sha256"]
                or baseline["etag"] != remote["etag"]
                or not isinstance(baseline["md5"], str)
                or not re.fullmatch(r"[0-9a-f]{32}", baseline["md5"])
            ):
                raise ManifestError("frozen rollback baseline differs from exact remote preimage")
            operation = "RESTORE_IF_MATCH"
        else:
            if baseline is not None or forward["key"] not in MOLS_OBJECT_KEYS:
                raise ManifestError("absent-baseline rollback delete is outside exact Mols scope")
            operation = "DELETE_CREATED_AFTER_EXACT_PROOF"
        rows.append({
            "index": index,
            "key": forward["key"],
            "key_class": forward["key_class"],
            "forward_mode": forward["conditional_mode"],
            "forward_row_sha256": canonical_sha256(forward),
            "baseline_preimage": copy.deepcopy(forward["remote_preimage"]),
            "baseline_object": copy.deepcopy(baseline),
            "expected_postimage": {
                "bytes": forward["bytes"],
                "md5": forward["md5"],
                "sha256": forward["sha256"],
            },
            "semantic_delta": copy.deepcopy(semantic_by_key.get(forward["key"])),
            "rollback_operation": operation,
        })
    has_index = any(row["key"] == "index.json" for row in rows)
    if has_index != (index_delta is not None):
        raise ManifestError("rollback index delta is missing or unexpected")
    if index_delta is not None:
        index_delta = _validate_mols_index_delta(index_delta)
    plan: dict[str, Any] = {
        "schema": ROLLBACK_MANIFEST_SCHEMA,
        "contract_version": CONTRACT_VERSION,
        "bucket": BUCKET,
        "publication_area_id": MOLS_AREA_ID,
        "source_sha": manifest["source_sha"],
        "source_tree": manifest["source_tree"],
        "approved_manifest_sha256": manifest["manifest_sha256"],
        "delete_allowlist": list(MOLS_OBJECT_KEYS),
        "object_count": len(rows),
        "objects": rows,
        "index_delta": copy.deepcopy(index_delta),
        "reverse_order_required": True,
    }
    plan["rollback_manifest_sha256"] = canonical_sha256(plan)
    return validate_rollback_manifest(plan, manifest)


def validate_rollback_manifest(plan: Any, manifest: dict[str, Any]) -> dict[str, Any]:
    manifest = validate_upload_manifest(manifest)
    fields = {
        "schema", "contract_version", "bucket", "publication_area_id",
        "source_sha", "source_tree", "approved_manifest_sha256",
        "delete_allowlist", "object_count", "objects", "index_delta",
        "reverse_order_required", "rollback_manifest_sha256",
    }
    if not isinstance(plan, dict) or set(plan) != fields:
        raise ManifestError("rollback manifest schema is not exact")
    if (
        plan["schema"] != ROLLBACK_MANIFEST_SCHEMA
        or plan["contract_version"] != CONTRACT_VERSION
        or plan["bucket"] != BUCKET
        or plan["publication_area_id"] != MOLS_AREA_ID
        or manifest["publication_area_id"] != MOLS_AREA_ID
        or plan["source_sha"] != manifest["source_sha"]
        or plan["source_tree"] != manifest["source_tree"]
        or plan["approved_manifest_sha256"] != manifest["manifest_sha256"]
        or plan["delete_allowlist"] != list(MOLS_OBJECT_KEYS)
        or plan["reverse_order_required"] is not True
    ):
        raise ManifestError("rollback manifest identity/scope binding mismatch")
    try:
        digest = _require_digest(
            plan["rollback_manifest_sha256"], "rollback_manifest_sha256"
        )
    except ValueError as error:
        raise ManifestError(str(error)) from error
    if digest != canonical_sha256(plan, omit={"rollback_manifest_sha256"}):
        raise ManifestError("rollback manifest digest mismatch")
    rows = plan["objects"]
    if not isinstance(rows, list) or plan["object_count"] != len(rows) or len(rows) != manifest["object_count"]:
        raise ManifestError("rollback manifest population mismatch")
    delete_keys: set[str] = set()
    for index, (row, forward) in enumerate(zip(rows, manifest["objects"])):
        expected_fields = {
            "index", "key", "key_class", "forward_mode", "forward_row_sha256",
            "baseline_preimage", "baseline_object", "expected_postimage",
            "semantic_delta", "rollback_operation",
        }
        if not isinstance(row, dict) or set(row) != expected_fields:
            raise ManifestError(f"rollback row {index} schema is not exact")
        if (
            row["index"] != index
            or row["key"] != forward["key"]
            or row["key_class"] != forward["key_class"]
            or row["forward_mode"] != forward["conditional_mode"]
            or row["forward_row_sha256"] != canonical_sha256(forward)
            or row["baseline_preimage"] != forward["remote_preimage"]
            or row["expected_postimage"] != {
                "bytes": forward["bytes"], "md5": forward["md5"], "sha256": forward["sha256"]
            }
        ):
            raise ManifestError("rollback row is not bound to its forward row")
        if row["key"] in MOLS_SEMANTIC_GLOBAL_KEYS:
            semantic_delta = _validate_mols_global_delta(row["semantic_delta"], manifest)
            reviewed_semantics = {
                value["key"]: value for value in manifest["semantic_deltas"]
            }
            if (
                semantic_delta["key"] != row["key"]
                or semantic_delta != reviewed_semantics.get(row["key"])
            ):
                raise ManifestError("rollback row carries the wrong Mols global semantic proof")
        elif row["semantic_delta"] is not None:
            raise ManifestError("rollback row carries an unexpected Mols global semantic proof")
        if row["forward_mode"] == "IF_NONE_MATCH":
            if (
                row["rollback_operation"] != "DELETE_CREATED_AFTER_EXACT_PROOF"
                or row["baseline_object"] is not None
                or row["baseline_preimage"] is not None
                or row["key"] not in MOLS_OBJECT_KEYS
            ):
                raise ManifestError("rollback delete row exceeds exact absent Mols authority")
            delete_keys.add(row["key"])
        else:
            baseline = row["baseline_object"]
            if row["rollback_operation"] != "RESTORE_IF_MATCH" or not isinstance(baseline, dict):
                raise ManifestError("rollback restore row is incomplete")
            if set(baseline) != {"local_path", "bytes", "md5", "sha256", "etag"}:
                raise ManifestError("rollback restore baseline schema is not exact")
            relative = Path(baseline["local_path"])
            if relative.is_absolute() or ".." in relative.parts or not relative.parts:
                raise ManifestError("rollback restore baseline path is unsafe")
            if any(
                baseline[field] != row["baseline_preimage"][field]
                for field in ("bytes", "sha256", "etag")
            ):
                raise ManifestError("rollback restore baseline identity mismatch")
    if not delete_keys.issubset(set(MOLS_OBJECT_KEYS)):
        raise ManifestError("rollback delete scope is not the exact Mols allowlist")
    has_index = any(row["key"] == "index.json" for row in rows)
    if has_index:
        _validate_mols_index_delta(plan["index_delta"])
    elif plan["index_delta"] is not None:
        raise ManifestError("rollback manifest has an index proof without an index write")
    return copy.deepcopy(plan)


def build_scoped_release_preflight(
    manifest: dict[str, Any],
    *,
    execution_id: str,
    observed_preimages: Sequence[dict[str, Any] | None],
    baseline_objects: Sequence[dict[str, Any] | None],
    index_delta: dict[str, Any] | None,
    semantic_deltas: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    manifest = validate_upload_manifest(manifest)
    if manifest["publication_area_id"] != MOLS_AREA_ID:
        raise ManifestError("scoped preflight requires exact Mols publication")
    release = build_release_preflight(
        manifest,
        execution_id=execution_id,
        observed_preimages=observed_preimages,
        adapter_success_schema=RELEASE_PROVIDER_RESULT_FIELDS,
        adapter_success_schema_capable=True,
    )
    rollback = build_rollback_manifest(
        manifest,
        baseline_objects=baseline_objects,
        index_delta=index_delta,
        semantic_deltas=semantic_deltas,
    )
    snapshot: dict[str, Any] = {
        "schema": SCOPED_PREFLIGHT_SCHEMA,
        "contract_version": CONTRACT_VERSION,
        "execution_id": execution_id,
        "manifest_sha256": manifest["manifest_sha256"],
        "release_preflight": release,
        "rollback_manifest": rollback,
    }
    snapshot["scoped_preflight_sha256"] = canonical_sha256(snapshot)
    return validate_scoped_release_preflight(snapshot, manifest, execution_id=execution_id)


def validate_scoped_release_preflight(
    snapshot: Any, manifest: dict[str, Any], *, execution_id: str
) -> dict[str, Any]:
    fields = {
        "schema", "contract_version", "execution_id", "manifest_sha256",
        "release_preflight", "rollback_manifest", "scoped_preflight_sha256",
    }
    if not isinstance(snapshot, dict) or set(snapshot) != fields:
        raise ManifestError("scoped release preflight schema is not exact")
    if (
        snapshot["schema"] != SCOPED_PREFLIGHT_SCHEMA
        or snapshot["contract_version"] != CONTRACT_VERSION
        or snapshot["execution_id"] != execution_id
        or snapshot["manifest_sha256"] != manifest["manifest_sha256"]
    ):
        raise ManifestError("scoped release preflight identity mismatch")
    try:
        digest = _require_digest(
            snapshot["scoped_preflight_sha256"], "scoped_preflight_sha256"
        )
    except ValueError as error:
        raise ManifestError(str(error)) from error
    if digest != canonical_sha256(snapshot, omit={"scoped_preflight_sha256"}):
        raise ManifestError("scoped release preflight digest mismatch")
    validate_release_preflight(
        snapshot["release_preflight"], manifest, execution_id=execution_id
    )
    validate_rollback_manifest(snapshot["rollback_manifest"], manifest)
    return copy.deepcopy(snapshot)


def _valid_rollback_delete_result(
    result: Any, rollback_row: Mapping[str, Any], *, attempt_id: str
) -> dict[str, Any]:
    if not isinstance(result, dict) or set(result) != set(ROLLBACK_DELETE_RESULT_FIELDS):
        raise ManifestError("rollback delete result schema is not exact")
    if (
        rollback_row["rollback_operation"] != "DELETE_CREATED_AFTER_EXACT_PROOF"
        or rollback_row["key"] not in MOLS_OBJECT_KEYS
        or rollback_row["baseline_preimage"] is not None
        or result["key"] != rollback_row["key"]
        or result["attempt_id"] != attempt_id
        or result["postimage_sha256"] != rollback_row["expected_postimage"]["sha256"]
        or result["verified_absent"] is not True
    ):
        raise ManifestError("rollback delete result exceeds or mismatches exact authority")
    if not isinstance(result["transaction_id"], str) or not _SAFE_VERSION_RE.fullmatch(result["transaction_id"]):
        raise ManifestError("rollback delete result identity is missing or unsafe")
    return copy.deepcopy(result)



def validate_rollback_result_prefix(
    rollback_manifest: Mapping[str, Any],
    *,
    execution_id: str,
    forward_results: Sequence[dict[str, Any]],
    rollback_results: Sequence[dict[str, Any]],
    require_complete: bool = False,
) -> tuple[int, ...]:
    """Bind rollback evidence to the exact known forward prefix in reverse order."""
    if not isinstance(require_complete, bool):
        raise ManifestError("rollback prefix completeness flag is not boolean")
    if not isinstance(execution_id, str) or not _SAFE_VERSION_RE.fullmatch(execution_id):
        raise ManifestError("rollback prefix execution identity is invalid")
    if (
        not isinstance(rollback_manifest, dict)
        or rollback_manifest.get("publication_area_id") != MOLS_AREA_ID
        or not isinstance(rollback_manifest.get("objects"), list)
        or not isinstance(rollback_manifest.get("object_count"), int)
        or isinstance(rollback_manifest.get("object_count"), bool)
        or rollback_manifest["object_count"] != len(rollback_manifest["objects"])
    ):
        raise ManifestError("rollback prefix manifest population/scope is invalid")
    if not isinstance(forward_results, (list, tuple)) or not isinstance(
        rollback_results, (list, tuple)
    ):
        raise ManifestError("rollback prefix result populations are invalid")
    rows = rollback_manifest["objects"]
    if len(forward_results) > len(rows) or len(rollback_results) > len(forward_results):
        raise ManifestError("rollback results exceed the known forward-effect prefix")
    if require_complete and (
        not forward_results or len(rollback_results) != len(forward_results)
    ):
        raise ManifestError("rollback verification has not reversed every known forward effect")

    normalized_forward: list[dict[str, Any]] = []
    for index, (result, row) in enumerate(zip(forward_results, rows)):
        if not isinstance(row, dict) or row.get("index") != index:
            raise ManifestError("rollback prefix manifest rows are not canonical")
        attempt_id = result.get("attempt_id", "") if isinstance(result, dict) else ""
        if attempt_id not in _permitted_scoped_attempt_ids(
            execution_id, phase="FORWARD", index=index
        ):
            raise ManifestError("known forward attempt is foreign to its execution")
        normalized = _valid_provider_result(
            result,
            {"key": row.get("key"), **(row.get("expected_postimage") or {})},
            attempt_id=attempt_id,
        )
        expected_transaction = scoped_transaction_id(
            execution_id=execution_id,
            phase="FORWARD",
            operation="PUT_CONDITIONAL",
            index=index,
            result=normalized,
        )
        if normalized != result or result["transaction_id"] != expected_transaction:
            raise ManifestError("known forward result identity/order is invalid")
        normalized_forward.append(normalized)

    expected_indices = tuple(reversed(range(len(forward_results))))[: len(rollback_results)]
    normalized_rollback: list[dict[str, Any]] = []
    for result, index in zip(rollback_results, expected_indices):
        row = rows[index]
        attempt_id = result.get("attempt_id", "") if isinstance(result, dict) else ""
        if attempt_id not in _permitted_scoped_attempt_ids(
            execution_id, phase="ROLLBACK", index=index
        ):
            raise ManifestError("rollback attempt is foreign to its execution")
        if row.get("rollback_operation") == "DELETE_CREATED_AFTER_EXACT_PROOF":
            normalized = _valid_rollback_delete_result(
                result, row, attempt_id=attempt_id
            )
        elif row.get("rollback_operation") == "RESTORE_IF_MATCH":
            baseline = row.get("baseline_object")
            if not isinstance(baseline, dict):
                raise ManifestError("rollback restore lacks its frozen baseline")
            normalized = _valid_provider_result(
                result,
                {"key": row.get("key"), **baseline},
                attempt_id=attempt_id,
            )
            preimage = row.get("baseline_preimage")
            if not isinstance(preimage, dict) or normalized["etag"] != preimage.get("etag"):
                raise ManifestError("rollback restore did not recover the baseline ETag")
        else:
            raise ManifestError("rollback result names an invalid operation")
        expected_transaction = scoped_transaction_id(
            execution_id=execution_id,
            phase="ROLLBACK",
            operation=row["rollback_operation"],
            index=index,
            result=normalized,
        )
        if normalized != result or result["transaction_id"] != expected_transaction:
            raise ManifestError("rollback result is not execution-bound and canonical")
        normalized_rollback.append(normalized)

    forward_transactions = [result["transaction_id"] for result in normalized_forward]
    rollback_transactions = [result["transaction_id"] for result in normalized_rollback]
    if (
        len(forward_transactions) != len(set(forward_transactions))
        or len(rollback_transactions) != len(set(rollback_transactions))
        or set(forward_transactions) & set(rollback_transactions)
    ):
        raise ManifestError("rollback prefix transaction identities repeat")
    return expected_indices

def _valid_rollback_verification(
    verification: Any, rollback_manifest: dict[str, Any]
) -> dict[str, Any]:
    fields = {
        "schema", "rollback_manifest_sha256", "all_rows_restored",
        "verified_absent_keys", "verified_restored_keys",
        "semantic_global_baselines_restored", "index_baseline_restored",
        "non_target_index_entries_unchanged", "receipt_sha256",
    }
    if not isinstance(verification, dict) or set(verification) != fields:
        raise ManifestError("rollback verification schema is not exact")
    expected_absent = [
        row["key"] for row in rollback_manifest["objects"]
        if row["rollback_operation"] == "DELETE_CREATED_AFTER_EXACT_PROOF"
    ]
    expected_restored = [
        row["key"] for row in rollback_manifest["objects"]
        if row["rollback_operation"] == "RESTORE_IF_MATCH"
    ]
    expected_semantic = [
        key for key in MOLS_SEMANTIC_GLOBAL_KEYS if key in expected_restored
    ]
    if (
        verification["schema"] != "trekdex-r2-rollback-verification-v2"
        or verification["rollback_manifest_sha256"] != rollback_manifest["rollback_manifest_sha256"]
        or verification["all_rows_restored"] is not True
        or verification["verified_absent_keys"] != expected_absent
        or verification["verified_restored_keys"] != expected_restored
        or verification["semantic_global_baselines_restored"] != expected_semantic
        or verification["index_baseline_restored"] is not True
        or verification["non_target_index_entries_unchanged"] is not True
    ):
        raise ManifestError("rollback verification does not prove exact baseline restoration")
    try:
        digest = _require_digest(verification["receipt_sha256"], "receipt_sha256")
    except ValueError as error:
        raise ManifestError(str(error)) from error
    if digest != canonical_sha256(verification, omit={"receipt_sha256"}):
        raise ManifestError("rollback verification digest mismatch")
    return copy.deepcopy(verification)


def _validate_scoped_reconciliation(value: Any) -> dict[str, Any]:
    fields = {
        "schema", "source_journal_sha256", "pending_phase", "pending_operation",
        "pending_key", "observation_state", "resumed", "receipt_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ManifestError("scoped reconciliation schema is not exact")
    if (
        value["schema"] != "trekdex-r2-scoped-reconciliation-v1"
        or value["pending_phase"] not in {"FORWARD", "ROLLBACK"}
        or value["pending_operation"] not in {
            "PUT_CONDITIONAL", "RESTORE_IF_MATCH", "DELETE_CREATED_AFTER_EXACT_PROOF"
        }
        or not _is_plain_safe_text(value["pending_key"], maximum=1024)
        or value["observation_state"] not in {
            "EFFECT_OBSERVED", "EFFECT_NOT_OBSERVED", "AMBIGUOUS"
        }
        or not isinstance(value["resumed"], bool)
        or (value["observation_state"] != "EFFECT_OBSERVED" and value["resumed"])
    ):
        raise ManifestError("scoped reconciliation state is invalid")
    try:
        _require_digest(value["source_journal_sha256"], "source_journal_sha256")
        digest = _require_digest(value["receipt_sha256"], "reconciliation receipt")
    except ValueError as error:
        raise ManifestError(str(error)) from error
    if digest != canonical_sha256(value, omit={"receipt_sha256"}):
        raise ManifestError("scoped reconciliation digest mismatch")
    return copy.deepcopy(value)


def _scoped_release_terminal(
    manifest: dict[str, Any],
    *,
    terminal_state: str,
    checkpoint_kind: str,
    execution_id: str,
    approval_assertion_sha256: str,
    provider_attestation: dict[str, Any],
    provider_tool_attestation: dict[str, Any],
    scoped_preflight: dict[str, Any] | None,
    forward_attempted: int,
    forward_results: Sequence[dict[str, Any]],
    rollback_attempted: int,
    rollback_results: Sequence[dict[str, Any]],
    pending_effect: dict[str, Any] | None,
    rollback_verification: dict[str, Any] | None,
    error_boundary: str | None,
    error: BaseException | None,
    reconciliation: dict[str, Any] | None,
    journal_key: bytes | str,
) -> dict[str, Any]:
    receipt: dict[str, Any] = {
        "schema": SCOPED_RELEASE_RECEIPT_SCHEMA,
        "contract_version": CONTRACT_VERSION,
        "terminal_state": terminal_state,
        "checkpoint_kind": checkpoint_kind,
        "execution_id": execution_id,
        "approval_assertion_sha256": approval_assertion_sha256,
        "provider_identity_attestation": copy.deepcopy(provider_attestation),
        "provider_tool_attestation": copy.deepcopy(provider_tool_attestation),
        "bucket": BUCKET,
        "publication_area_id": manifest["publication_area_id"],
        "source_sha": manifest["source_sha"],
        "source_tree": manifest["source_tree"],
        "manifest_sha256": manifest["manifest_sha256"],
        "scoped_preflight": copy.deepcopy(scoped_preflight),
        "scoped_preflight_sha256": (
            scoped_preflight["scoped_preflight_sha256"] if scoped_preflight is not None else None
        ),
        "rollback_manifest_sha256": (
            scoped_preflight["rollback_manifest"]["rollback_manifest_sha256"]
            if scoped_preflight is not None else None
        ),
        "forward_attempted": forward_attempted,
        "forward_observed": len(forward_results),
        "forward_results": copy.deepcopy(list(forward_results)),
        "forward_results_sha256": canonical_sha256(list(forward_results)),
        "rollback_attempted": rollback_attempted,
        "rollback_observed": len(rollback_results),
        "rollback_results": copy.deepcopy(list(rollback_results)),
        "rollback_results_sha256": canonical_sha256(list(rollback_results)),
        "pending_effect": copy.deepcopy(pending_effect),
        "rollback_verification": copy.deepcopy(rollback_verification),
        "reconciliation": copy.deepcopy(reconciliation),
        "error_boundary": error_boundary,
        "error_type": type(error).__name__ if error is not None else None,
        "committed": False,
        "pushed": False,
        "synchronization_dispatched": False,
        "journal_authentication": None,
    }
    receipt["journal_authentication"] = scoped_journal_authentication(
        receipt, key=journal_key
    )
    receipt["receipt_sha256"] = canonical_sha256(receipt)
    validate_scoped_release_receipt(receipt, manifest)
    return receipt


def validate_scoped_release_receipt(
    receipt: Any, manifest: dict[str, Any]
) -> dict[str, Any]:
    manifest = validate_upload_manifest(manifest)
    fields = {
        "schema", "contract_version", "terminal_state", "checkpoint_kind",
        "execution_id", "approval_assertion_sha256", "provider_identity_attestation",
        "provider_tool_attestation", "bucket", "publication_area_id", "source_sha", "source_tree",
        "manifest_sha256", "scoped_preflight", "scoped_preflight_sha256",
        "rollback_manifest_sha256", "forward_attempted", "forward_observed",
        "forward_results", "forward_results_sha256", "rollback_attempted",
        "rollback_observed", "rollback_results", "rollback_results_sha256",
        "pending_effect", "rollback_verification", "reconciliation",
        "error_boundary", "error_type", "journal_authentication",
        "committed", "pushed", "synchronization_dispatched", "receipt_sha256",
    }
    if not isinstance(receipt, dict) or set(receipt) != fields:
        raise ManifestError("scoped release receipt schema is not exact")
    if (
        receipt["schema"] != SCOPED_RELEASE_RECEIPT_SCHEMA
        or receipt["contract_version"] != CONTRACT_VERSION
        or receipt["bucket"] != BUCKET
        or receipt["publication_area_id"] != MOLS_AREA_ID
        or receipt["publication_area_id"] != manifest["publication_area_id"]
        or receipt["source_sha"] != manifest["source_sha"]
        or receipt["source_tree"] != manifest["source_tree"]
        or receipt["manifest_sha256"] != manifest["manifest_sha256"]
    ):
        raise ManifestError("scoped release receipt identity mismatch")
    for field in ("committed", "pushed", "synchronization_dispatched"):
        if receipt[field] is not False:
            raise ManifestError(f"scoped release receipt illegally sets {field}")
    try:
        _require_digest(receipt["approval_assertion_sha256"], "approval_assertion_sha256")
        _require_digest(receipt["forward_results_sha256"], "forward_results_sha256")
        _require_digest(receipt["rollback_results_sha256"], "rollback_results_sha256")
        _require_digest(receipt["journal_authentication"], "journal_authentication")
        digest = _require_digest(receipt["receipt_sha256"], "receipt_sha256")
    except ValueError as error:
        raise ManifestError(str(error)) from error
    if digest != canonical_sha256(receipt, omit={"receipt_sha256"}):
        raise ManifestError("scoped release receipt digest mismatch")
    validate_provider_identity_attestation(
        receipt["provider_identity_attestation"],
        expected_account_identity_ref=manifest["account_identity_ref"],
        expected_endpoint_identity_ref=manifest["endpoint_identity_ref"],
    )
    validate_provider_tool_attestation(receipt["provider_tool_attestation"])
    preflight = receipt["scoped_preflight"]
    reconciliation = receipt["reconciliation"]
    if reconciliation is not None:
        reconciliation = _validate_scoped_reconciliation(reconciliation)
        if reconciliation["pending_key"] not in set(MOLS_FORWARD_KEYS):
            raise ManifestError("scoped reconciliation key is outside exact Mols scope")
    if preflight is None:
        if receipt["scoped_preflight_sha256"] is not None or receipt["rollback_manifest_sha256"] is not None:
            raise ManifestError("scoped receipt has preflight digests without preflight")
        rollback_manifest = None
    else:
        validate_scoped_release_preflight(preflight, manifest, execution_id=receipt["execution_id"])
        rollback_manifest = preflight["rollback_manifest"]
        if (
            receipt["scoped_preflight_sha256"] != preflight["scoped_preflight_sha256"]
            or receipt["rollback_manifest_sha256"] != rollback_manifest["rollback_manifest_sha256"]
        ):
            raise ManifestError("scoped receipt preflight/rollback digest mismatch")
    forward = receipt["forward_results"]
    rollback = receipt["rollback_results"]
    if (
        not isinstance(forward, list)
        or receipt["forward_observed"] != len(forward)
        or receipt["forward_results_sha256"] != canonical_sha256(forward)
        or not isinstance(rollback, list)
        or receipt["rollback_observed"] != len(rollback)
        or receipt["rollback_results_sha256"] != canonical_sha256(rollback)
    ):
        raise ManifestError("scoped release result population/digest mismatch")
    for field in ("forward_attempted", "forward_observed", "rollback_attempted", "rollback_observed"):
        if not isinstance(receipt[field], int) or isinstance(receipt[field], bool) or receipt[field] < 0:
            raise ManifestError("scoped release effect count is invalid")
    if rollback_manifest is not None:
        validate_rollback_result_prefix(
            rollback_manifest,
            execution_id=receipt["execution_id"],
            forward_results=forward,
            rollback_results=rollback,
        )
    elif forward or rollback:
        raise ManifestError("scoped effect results have no rollback manifest")
    if not (
        len(forward) <= receipt["forward_attempted"] <= len(forward) + 1
        and len(rollback) <= receipt["rollback_attempted"] <= len(rollback) + 1
    ):
        raise ManifestError("scoped release attempted/observed counts are not closed")
    pending = receipt["pending_effect"]
    if pending is not None:
        pending_fields = {
            "phase", "index", "key", "operation", "attempt_id",
            "expected_identity_sha256",
        }
        if not isinstance(pending, dict) or set(pending) != pending_fields:
            raise ManifestError("scoped pending-effect schema is not exact")
        try:
            _require_digest(pending["expected_identity_sha256"], "pending identity")
        except ValueError as error:
            raise ManifestError(str(error)) from error
        if not isinstance(pending["attempt_id"], str) or not _SAFE_VERSION_RE.fullmatch(pending["attempt_id"]):
            raise ManifestError("scoped pending attempt identity is invalid")
        index = pending["index"]
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < manifest["object_count"]:
            raise ManifestError("scoped pending index is invalid")
        expected_pending_attempt = scoped_attempt_id(
            receipt["execution_id"],
            phase=pending["phase"],
            index=index,
            reconciled=reconciliation is not None,
        )
        if pending["attempt_id"] != expected_pending_attempt:
            raise ManifestError("scoped pending attempt is foreign to its execution")
        if pending["phase"] == "FORWARD":
            expected = manifest["objects"][index]
            if (
                index != len(forward)
                or pending["key"] != expected["key"]
                or pending["operation"] != "PUT_CONDITIONAL"
                or pending["expected_identity_sha256"] != canonical_sha256(expected)
            ):
                raise ManifestError("scoped pending forward effect is not exact")
        elif pending["phase"] == "ROLLBACK":
            if rollback_manifest is None or index >= len(forward):
                raise ManifestError("scoped pending rollback has no known forward effect")
            expected = rollback_manifest["objects"][index]
            if (
                index != len(forward) - len(rollback) - 1
                or pending["key"] != expected["key"]
                or pending["operation"] != expected["rollback_operation"]
                or pending["expected_identity_sha256"] != canonical_sha256(forward[index])
            ):
                raise ManifestError("scoped pending rollback effect is not exact")
        else:
            raise ManifestError("scoped pending phase is invalid")
    state = receipt["terminal_state"]
    if state not in {"SAFE_ABORT", "COMPLETE_SUCCESS", "ROLLED_BACK", "INDETERMINATE"}:
        raise ManifestError("scoped release terminal state is invalid")
    if receipt["checkpoint_kind"] not in {"WRITE_AHEAD", "PROGRESS", "TERMINAL"}:
        raise ManifestError("scoped release checkpoint kind is invalid")
    if receipt["checkpoint_kind"] == "WRITE_AHEAD" and pending is None:
        raise ManifestError("scoped write-ahead checkpoint lacks its pending effect")
    if receipt["checkpoint_kind"] == "PROGRESS" and pending is not None:
        raise ManifestError("scoped progress checkpoint retains a pending effect")
    if state == "COMPLETE_SUCCESS":
        if (
            preflight is None
            or receipt["checkpoint_kind"] != "TERMINAL"
            or len(forward) != manifest["object_count"]
            or receipt["forward_attempted"] != len(forward)
            or receipt["rollback_attempted"] != 0
            or rollback
            or receipt["rollback_verification"] is not None
            or receipt["pending_effect"] is not None
            or receipt["error_boundary"] is not None
            or receipt["error_type"] is not None
        ):
            raise ManifestError("scoped COMPLETE_SUCCESS receipt is incomplete")
    elif state == "ROLLED_BACK":
        if (
            rollback_manifest is None
            or receipt["checkpoint_kind"] != "TERMINAL"
            or len(rollback) != len(forward)
            or receipt["forward_attempted"] != len(forward)
            or receipt["rollback_attempted"] != len(rollback)
            or receipt["pending_effect"] is not None
            or receipt["rollback_verification"] is None
            or not receipt["error_boundary"]
            or not receipt["error_type"]
        ):
            raise ManifestError("scoped ROLLED_BACK receipt is incomplete")
        _valid_rollback_verification(receipt["rollback_verification"], rollback_manifest)
    elif state == "SAFE_ABORT":
        if (
            receipt["checkpoint_kind"] != "TERMINAL"
            or forward
            or rollback
            or receipt["forward_attempted"] != 0
            or receipt["rollback_attempted"] != 0
            or receipt["rollback_verification"] is not None
            or pending is not None
            or not receipt["error_boundary"]
            or not receipt["error_type"]
        ):
            raise ManifestError("scoped SAFE_ABORT receipt records an effect or lacks cause")
    elif (
        not receipt["error_boundary"]
        or not receipt["error_type"]
        or receipt["rollback_verification"] is not None
    ):
        raise ManifestError("scoped INDETERMINATE receipt lacks cause or claims verification")
    if reconciliation is not None:
        if state in {"COMPLETE_SUCCESS", "ROLLED_BACK"}:
            if (
                reconciliation["observation_state"] != "EFFECT_OBSERVED"
                or reconciliation["resumed"] is not True
            ):
                raise ManifestError("completed reconciliation did not observe and resume")
        elif state == "SAFE_ABORT":
            raise ManifestError("SAFE_ABORT cannot carry a reconciliation record")
    return copy.deepcopy(receipt)


def validate_scoped_reconciliation_terminal(
    journal: Any,
    terminal: Any,
    manifest: dict[str, Any],
    *,
    approval_assertion: dict[str, Any],
    approval_key: bytes | str,
) -> dict[str, Any]:
    """Authenticate and bind an idempotent reconciliation terminal to its journal."""
    manifest = validate_upload_manifest(manifest)
    validated_journal = validate_scoped_release_receipt(journal, manifest)
    validated_terminal = validate_scoped_release_receipt(terminal, manifest)
    for label, document in (
        ("source journal", validated_journal),
        ("existing terminal", validated_terminal),
    ):
        try:
            validate_scoped_journal_authentication(document, key=approval_key)
        except ManifestError as error:
            raise ManifestError(f"{label} authentication failed: {error}") from error

    approval_digest = validate_approval_assertion(
        approval_assertion,
        manifest,
        operation="RELEASE",
        key=approval_key,
    )
    if any(
        document["approval_assertion_sha256"] != approval_digest
        for document in (validated_journal, validated_terminal)
    ):
        raise ManifestError(
            "scoped reconciliation approval differs from journal or terminal"
        )
    if (
        validated_journal["reconciliation"] is not None
        or validated_journal["checkpoint_kind"]
        not in {"WRITE_AHEAD", "TERMINAL"}
        or validated_journal["pending_effect"] is None
    ):
        raise ManifestError(
            "idempotent reconciliation requires the original pending source journal"
        )
    reconciliation = validated_terminal["reconciliation"]
    if (
        validated_terminal["checkpoint_kind"] != "TERMINAL"
        or reconciliation is None
    ):
        raise ManifestError(
            "existing reconciliation output is not an exact terminal receipt"
        )

    identity_fields = (
        "execution_id",
        "approval_assertion_sha256",
        "provider_identity_attestation",
        "provider_tool_attestation",
        "scoped_preflight",
        "scoped_preflight_sha256",
        "rollback_manifest_sha256",
    )
    if any(
        validated_terminal[field] != validated_journal[field]
        for field in identity_fields
    ):
        raise ManifestError(
            "existing reconciliation terminal identity differs from source journal"
        )
    if reconciliation["source_journal_sha256"] != validated_journal["receipt_sha256"]:
        raise ManifestError(
            "existing reconciliation terminal is bound to a different source journal"
        )

    pending = validated_journal["pending_effect"]
    expected_reconciliation = {
        "pending_phase": pending["phase"],
        "pending_operation": pending["operation"],
        "pending_key": pending["key"],
    }
    if any(
        reconciliation[field] != expected
        for field, expected in expected_reconciliation.items()
    ):
        raise ManifestError(
            "existing reconciliation terminal pending identity differs from source journal"
        )

    for field in ("forward_results", "rollback_results"):
        journal_results = validated_journal[field]
        terminal_results = validated_terminal[field]
        if (
            len(terminal_results) < len(journal_results)
            or terminal_results[:len(journal_results)] != journal_results
        ):
            raise ManifestError(
                f"existing reconciliation terminal rewrites {field} history"
            )
        journal_identities = [
            (result["attempt_id"], result["transaction_id"])
            for result in journal_results
        ]
        terminal_identities = [
            (result["attempt_id"], result["transaction_id"])
            for result in terminal_results[:len(journal_results)]
        ]
        if terminal_identities != journal_identities:
            raise ManifestError(
                f"existing reconciliation terminal rewrites {field} identities"
            )

    if reconciliation["resumed"] is not True:
        if any(
            validated_terminal[field] != validated_journal[field]
            for field in ("forward_results", "rollback_results")
        ):
            raise ManifestError(
                "non-resumed reconciliation terminal invents provider results"
            )
    else:
        if pending["phase"] == "FORWARD":
            results = validated_terminal["forward_results"]
            position = pending["index"]
            phase = "FORWARD"
        else:
            results = validated_terminal["rollback_results"]
            position = len(validated_journal["rollback_results"])
            phase = "ROLLBACK"
        if position >= len(results):
            raise ManifestError(
                "resumed reconciliation terminal lacks the pending effect result"
            )
        result = results[position]
        if result["attempt_id"] != pending["attempt_id"]:
            raise ManifestError(
                "resumed reconciliation terminal has a foreign pending attempt"
            )
        expected_transaction_id = scoped_transaction_id(
            execution_id=validated_journal["execution_id"],
            phase=phase,
            operation=pending["operation"],
            index=pending["index"],
            result=result,
        )
        if result["transaction_id"] != expected_transaction_id:
            raise ManifestError(
                "resumed reconciliation terminal has a foreign pending transaction"
            )
    return copy.deepcopy(validated_terminal)


def execute_scoped_release(
    manifest: dict[str, Any],
    writer: ScopedConditionalWriter,
    *,
    approval_assertion: dict[str, Any],
    approval_key: bytes | str,
    execution_id: str | None = None,
    state_probe: Callable[[str], bool] | None = None,
    checkpoint: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Apply the exact five-key Mols plan and roll back known partial effects under one lock."""
    manifest = validate_upload_manifest(manifest)
    if manifest["publication_area_id"] != MOLS_AREA_ID:
        raise ManifestError("scoped release requires exact Mols publication scope")
    approval_digest = validate_approval_assertion(
        approval_assertion, manifest, operation="RELEASE", key=approval_key
    )
    attestation = validate_provider_identity_attestation(
        writer.attest_identity(),
        expected_account_identity_ref=manifest["account_identity_ref"],
        expected_endpoint_identity_ref=manifest["endpoint_identity_ref"],
    )
    tool_attestation = validate_provider_tool_attestation(writer.tool_attestation())
    execution_id = execution_id or ("mols-release-" + secrets.token_hex(16))
    if not isinstance(execution_id, str) or not _SAFE_VERSION_RE.fullmatch(execution_id):
        raise ManifestError("scoped release execution identity is invalid")
    probe = state_probe or (lambda _boundary: True)
    persist = checkpoint or (lambda _document: None)
    preflight: dict[str, Any] | None = None
    forward: list[dict[str, Any]] = []
    rollback: list[dict[str, Any]] = []
    forward_attempted = 0
    rollback_attempted = 0

    def make_receipt(
        state: str,
        boundary: str | None,
        error: BaseException | None,
        *,
        kind: str = "TERMINAL",
        pending: dict[str, Any] | None = None,
        verification: dict[str, Any] | None = None,
        reconciliation_record: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return _scoped_release_terminal(
            manifest,
            terminal_state=state,
            checkpoint_kind=kind,
            execution_id=execution_id,
            approval_assertion_sha256=approval_digest,
            provider_attestation=attestation,
            provider_tool_attestation=tool_attestation,
            scoped_preflight=preflight,
            forward_attempted=forward_attempted,
            forward_results=forward,
            rollback_attempted=rollback_attempted,
            rollback_results=rollback,
            pending_effect=pending,
            rollback_verification=verification,
            error_boundary=boundary,
            error=error,
            reconciliation=reconciliation_record,
            journal_key=approval_key,
        )

    def persist_terminal(document: dict[str, Any]) -> dict[str, Any]:
        try:
            persist(document)
        except Exception:
            pass
        return document

    for boundary in RELEASE_PREWRITE_BOUNDARIES:
        if not probe(boundary):
            return persist_terminal(make_receipt("SAFE_ABORT", boundary, RuntimeError("immutable state probe failed")))
    try:
        candidate = writer.preflight_scoped_release(copy.deepcopy(manifest), execution_id=execution_id)
        preflight = validate_scoped_release_preflight(candidate, manifest, execution_id=execution_id)
    except Exception as error:
        preflight = None
        return persist_terminal(make_receipt("SAFE_ABORT", "COMPLETE_SCOPED_PREFLIGHT", error))
    release_preflight = preflight["release_preflight"]
    if not all(row["eligible"] for row in release_preflight["rows"]):
        return persist_terminal(make_receipt("SAFE_ABORT", "PREFLIGHT_REFUSAL", RuntimeError("at least one row is ineligible")))

    def rollback_known(cause_boundary: str, cause: BaseException) -> dict[str, Any]:
        nonlocal rollback_attempted
        rollback_manifest = preflight["rollback_manifest"]
        for forward_index in reversed(range(len(forward))):
            rollback_row = rollback_manifest["objects"][forward_index]
            postimage = forward[forward_index]
            operation = rollback_row["rollback_operation"]
            attempt_id = scoped_attempt_id(
                execution_id,
                phase="ROLLBACK",
                index=forward_index,
                reconciled=False,
            )
            pending = {
                "phase": "ROLLBACK",
                "index": forward_index,
                "key": rollback_row["key"],
                "operation": operation,
                "attempt_id": attempt_id,
                "expected_identity_sha256": canonical_sha256(postimage),
            }
            write_ahead = make_receipt(
                "INDETERMINATE", f"BEFORE_{operation}:{forward_index}",
                RuntimeError("rollback effect pending reconciliation"),
                kind="WRITE_AHEAD", pending=pending,
            )
            try:
                persist(write_ahead)
            except Exception as error:
                return persist_terminal(make_receipt("INDETERMINATE", "ROLLBACK_WRITE_AHEAD", error))
            rollback_attempted += 1
            try:
                if operation == "DELETE_CREATED_AFTER_EXACT_PROOF":
                    raw = writer.rollback_delete_created(
                        copy.deepcopy(rollback_row), postimage=copy.deepcopy(postimage),
                        attempt_id=attempt_id,
                        scoped_preflight_sha256=preflight["scoped_preflight_sha256"],
                    )
                    result = _valid_rollback_delete_result(raw, rollback_row, attempt_id=attempt_id)
                else:
                    raw = writer.rollback_put_conditional(
                        copy.deepcopy(rollback_row), postimage=copy.deepcopy(postimage),
                        attempt_id=attempt_id,
                        scoped_preflight_sha256=preflight["scoped_preflight_sha256"],
                    )
                    result = _valid_provider_result(
                        raw, {"key": rollback_row["key"], **rollback_row["baseline_object"]},
                        attempt_id=attempt_id,
                    )
                    if result["etag"] != rollback_row["baseline_preimage"]["etag"]:
                        raise ManifestError("rollback restore returned a non-baseline ETag")
                rollback.append(result)
            except Exception as error:
                return persist_terminal(make_receipt(
                    "INDETERMINATE", f"{operation}:{forward_index}", error, pending=pending
                ))
            try:
                persist(make_receipt(
                    "INDETERMINATE", f"AFTER_{operation}:{forward_index}",
                    RuntimeError("rollback in progress"), kind="PROGRESS"
                ))
            except Exception as error:
                return persist_terminal(make_receipt("INDETERMINATE", "ROLLBACK_PROGRESS_CHECKPOINT", error))
        try:
            verification = _valid_rollback_verification(
                writer.verify_rollback(
                    copy.deepcopy(rollback_manifest),
                    forward_results=copy.deepcopy(forward),
                    rollback_results=copy.deepcopy(rollback),
                ),
                rollback_manifest,
            )
        except Exception as error:
            return persist_terminal(make_receipt("INDETERMINATE", "VERIFY_ROLLBACK_BASELINE", error))
        return persist_terminal(make_receipt(
            "ROLLED_BACK", cause_boundary, cause, verification=verification
        ))

    for index, row in enumerate(manifest["objects"]):
        boundary = f"BEFORE_WRITE:{index}:{row['key']}"
        if not probe(boundary):
            return rollback_known(boundary, RuntimeError("immutable state probe failed")) if forward else persist_terminal(make_receipt("SAFE_ABORT", boundary, RuntimeError("immutable state probe failed")))
        attempt_id = scoped_attempt_id(
            execution_id,
            phase="FORWARD",
            index=index,
            reconciled=False,
        )
        pending = {
            "phase": "FORWARD",
            "index": index,
            "key": row["key"],
            "operation": "PUT_CONDITIONAL",
            "attempt_id": attempt_id,
            "expected_identity_sha256": canonical_sha256(row),
        }
        try:
            persist(make_receipt(
                "INDETERMINATE", boundary, RuntimeError("forward effect pending reconciliation"),
                kind="WRITE_AHEAD", pending=pending,
            ))
        except Exception as error:
            return rollback_known("FORWARD_WRITE_AHEAD", error) if forward else persist_terminal(make_receipt("SAFE_ABORT", "FORWARD_WRITE_AHEAD", error))
        forward_attempted += 1
        try:
            raw = writer.put_conditional(
                copy.deepcopy(row), attempt_id=attempt_id,
                preflight_row=copy.deepcopy(release_preflight["rows"][index]),
                preflight_sha256=release_preflight["preflight_sha256"],
            )
            result = _valid_provider_result(raw, row, attempt_id=attempt_id)
            if any(existing["transaction_id"] == result["transaction_id"] for existing in forward):
                raise ManifestError("scoped provider transaction identity is duplicated")
            forward.append(result)
        except ConditionalRequestRefused as error:
            forward_attempted -= 1
            return rollback_known(boundary, error) if forward else persist_terminal(make_receipt("SAFE_ABORT", boundary, error))
        except Exception as error:
            return persist_terminal(make_receipt("INDETERMINATE", boundary, error, pending=pending))
        after = f"AFTER_WRITE:{index}:{row['key']}"
        if not probe(after):
            return rollback_known(after, RuntimeError("immutable state probe failed"))
        try:
            persist(make_receipt(
                "INDETERMINATE", after, RuntimeError("scoped release in progress"), kind="PROGRESS"
            ))
        except Exception as error:
            return persist_terminal(make_receipt("INDETERMINATE", "FORWARD_PROGRESS_CHECKPOINT", error))
    if not probe("BEFORE_TERMINAL"):
        return rollback_known("BEFORE_TERMINAL", RuntimeError("immutable state probe failed"))
    return persist_terminal(make_receipt("COMPLETE_SUCCESS", None, None))


def reconcile_scoped_release_write_ahead(
    journal: dict[str, Any],
    manifest: dict[str, Any],
    writer: ScopedConditionalWriter,
    *,
    approval_assertion: dict[str, Any],
    approval_key: bytes | str,
    state_probe: Callable[[str], bool] | None = None,
    checkpoint: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Re-observe one scoped pending effect and resume at most once."""
    manifest = validate_upload_manifest(manifest)
    validated = validate_scoped_release_receipt(journal, manifest)
    approval_digest = validate_approval_assertion(
        approval_assertion, manifest, operation="RELEASE", key=approval_key
    )
    if approval_digest != validated["approval_assertion_sha256"]:
        raise ManifestError("scoped reconciliation approval differs from journal")
    validate_scoped_journal_authentication(validated, key=approval_key)
    if validated["reconciliation"] is not None:
        if validated["checkpoint_kind"] == "TERMINAL":
            return copy.deepcopy(validated)
        raise ManifestError(
            "scoped reconciliation already resumed once; operator review is required"
        )
    if (
        validated["checkpoint_kind"] not in {"WRITE_AHEAD", "TERMINAL"}
        or validated["pending_effect"] is None
    ):
        raise ManifestError("scoped reconciliation requires an exact pending-effect journal")
    attestation = validate_provider_identity_attestation(
        writer.attest_identity(),
        expected_account_identity_ref=manifest["account_identity_ref"],
        expected_endpoint_identity_ref=manifest["endpoint_identity_ref"],
    )
    tool_attestation = validate_provider_tool_attestation(writer.tool_attestation())
    if (
        attestation != validated["provider_identity_attestation"]
        or tool_attestation != validated["provider_tool_attestation"]
    ):
        raise ManifestError("scoped reconciliation provider/tool differs from journal")
    preflight = validated["scoped_preflight"]
    if preflight is None:
        raise ManifestError("scoped reconciliation journal lacks preflight")
    pending = validated["pending_effect"]
    probe = state_probe or (lambda _boundary: True)
    persist = checkpoint or (lambda _document: None)
    forward = copy.deepcopy(validated["forward_results"])
    rollback = copy.deepcopy(validated["rollback_results"])
    forward_attempted = len(forward)
    rollback_attempted = len(rollback)
    source_journal_sha256 = validated["receipt_sha256"]

    def reconciliation_record(state: str, *, resumed: bool) -> dict[str, Any]:
        record: dict[str, Any] = {
            "schema": "trekdex-r2-scoped-reconciliation-v1",
            "source_journal_sha256": source_journal_sha256,
            "pending_phase": pending["phase"],
            "pending_operation": pending["operation"],
            "pending_key": pending["key"],
            "observation_state": state,
            "resumed": resumed,
        }
        record["receipt_sha256"] = canonical_sha256(record)
        return _validate_scoped_reconciliation(record)

    def make_receipt(
        state: str,
        boundary: str | None,
        error: BaseException | None,
        *,
        kind: str = "TERMINAL",
        pending_effect: dict[str, Any] | None = None,
        verification: dict[str, Any] | None = None,
        observation_state: str = "EFFECT_OBSERVED",
        resumed: bool = True,
    ) -> dict[str, Any]:
        return _scoped_release_terminal(
            manifest,
            terminal_state=state,
            checkpoint_kind=kind,
            execution_id=validated["execution_id"],
            approval_assertion_sha256=approval_digest,
            provider_attestation=attestation,
            provider_tool_attestation=tool_attestation,
            scoped_preflight=preflight,
            forward_attempted=forward_attempted,
            forward_results=forward,
            rollback_attempted=rollback_attempted,
            rollback_results=rollback,
            pending_effect=pending_effect,
            rollback_verification=verification,
            error_boundary=boundary,
            error=error,
            reconciliation=reconciliation_record(
                observation_state, resumed=resumed
            ),
            journal_key=approval_key,
        )

    def save_terminal(document: dict[str, Any]) -> dict[str, Any]:
        try:
            persist(document)
        except Exception:
            pass
        return document

    try:
        observation = writer.reconcile_pending_effect(
            copy.deepcopy(pending), copy.deepcopy(manifest), copy.deepcopy(validated)
        )
    except Exception:
        observation = {"state": "AMBIGUOUS", "result": None}
    if (
        not isinstance(observation, dict)
        or set(observation) != {"state", "result"}
        or observation["state"] not in {
            "EFFECT_OBSERVED", "EFFECT_NOT_OBSERVED", "AMBIGUOUS"
        }
    ):
        observation = {"state": "AMBIGUOUS", "result": None}
    observation_state = observation["state"]
    if observation_state != "EFFECT_OBSERVED":
        return save_terminal(make_receipt(
            "INDETERMINATE",
            "RECONCILED_OPERATOR_REVIEW",
            RuntimeError("pending effect was not proven; no retry is permitted"),
            observation_state=observation_state,
            resumed=False,
        ))

    index = pending["index"]
    raw_result = observation["result"]
    if pending["phase"] == "FORWARD":
        if index != len(forward):
            raise ManifestError("reconciled forward index is not next")
        result = _valid_provider_result(
            raw_result, manifest["objects"][index],
            attempt_id=pending["attempt_id"],
        )
        forward.append(result)
        forward_attempted = len(forward)
    else:
        rollback_row = preflight["rollback_manifest"]["objects"][index]
        if index != len(forward) - len(rollback) - 1:
            raise ManifestError("reconciled rollback index is not next")
        if rollback_row["rollback_operation"] == "DELETE_CREATED_AFTER_EXACT_PROOF":
            result = _valid_rollback_delete_result(
                raw_result, rollback_row, attempt_id=pending["attempt_id"]
            )
        else:
            result = _valid_provider_result(
                raw_result, {"key": rollback_row["key"], **rollback_row["baseline_object"]},
                attempt_id=pending["attempt_id"],
            )
            if result["etag"] != rollback_row["baseline_preimage"]["etag"]:
                raise ManifestError("reconciled restore does not match baseline ETag")
        rollback.append(result)
        rollback_attempted = len(rollback)

    def continue_rollback(cause_boundary: str, cause: BaseException) -> dict[str, Any]:
        nonlocal rollback_attempted
        rollback_manifest = preflight["rollback_manifest"]
        while len(rollback) < len(forward):
            rollback_index = len(forward) - len(rollback) - 1
            row = rollback_manifest["objects"][rollback_index]
            postimage = forward[rollback_index]
            operation = row["rollback_operation"]
            attempt_id = scoped_attempt_id(
                validated["execution_id"],
                phase="ROLLBACK",
                index=rollback_index,
                reconciled=True,
            )
            next_pending = {
                "phase": "ROLLBACK",
                "index": rollback_index,
                "key": row["key"],
                "operation": operation,
                "attempt_id": attempt_id,
                "expected_identity_sha256": canonical_sha256(postimage),
            }
            write_ahead = make_receipt(
                "INDETERMINATE", f"BEFORE_{operation}:{rollback_index}",
                RuntimeError("reconciled rollback effect pending"),
                kind="WRITE_AHEAD", pending_effect=next_pending,
            )
            try:
                persist(write_ahead)
            except Exception as error:
                return save_terminal(make_receipt(
                    "INDETERMINATE", "RECONCILE_ROLLBACK_WRITE_AHEAD", error
                ))
            rollback_attempted += 1
            try:
                if operation == "DELETE_CREATED_AFTER_EXACT_PROOF":
                    raw = writer.rollback_delete_created(
                        copy.deepcopy(row), postimage=copy.deepcopy(postimage),
                        attempt_id=attempt_id,
                        scoped_preflight_sha256=preflight["scoped_preflight_sha256"],
                    )
                    item = _valid_rollback_delete_result(
                        raw, row, attempt_id=attempt_id
                    )
                else:
                    raw = writer.rollback_put_conditional(
                        copy.deepcopy(row), postimage=copy.deepcopy(postimage),
                        attempt_id=attempt_id,
                        scoped_preflight_sha256=preflight["scoped_preflight_sha256"],
                    )
                    item = _valid_provider_result(
                        raw, {"key": row["key"], **row["baseline_object"]},
                        attempt_id=attempt_id,
                    )
                    if item["etag"] != row["baseline_preimage"]["etag"]:
                        raise ManifestError("reconciled rollback ETag mismatch")
                rollback.append(item)
            except Exception as error:
                return save_terminal(make_receipt(
                    "INDETERMINATE", f"{operation}:{rollback_index}", error,
                    pending_effect=next_pending,
                ))
            try:
                persist(make_receipt(
                    "INDETERMINATE", f"AFTER_{operation}:{rollback_index}",
                    RuntimeError("reconciled rollback in progress"), kind="PROGRESS",
                ))
            except Exception as error:
                return save_terminal(make_receipt(
                    "INDETERMINATE", "RECONCILE_ROLLBACK_PROGRESS", error
                ))
        try:
            verification = _valid_rollback_verification(
                writer.verify_rollback(
                    copy.deepcopy(rollback_manifest),
                    forward_results=copy.deepcopy(forward),
                    rollback_results=copy.deepcopy(rollback),
                ),
                rollback_manifest,
            )
        except Exception as error:
            return save_terminal(make_receipt(
                "INDETERMINATE", "RECONCILE_VERIFY_ROLLBACK", error
            ))
        return save_terminal(make_receipt(
            "ROLLED_BACK", cause_boundary, cause, verification=verification
        ))

    if pending["phase"] == "ROLLBACK":
        return continue_rollback(
            "RECONCILED_ROLLBACK_COMPLETE",
            RuntimeError("original forward sequence required rollback"),
        )

    release_preflight = preflight["release_preflight"]
    for forward_index in range(len(forward), manifest["object_count"]):
        row = manifest["objects"][forward_index]
        boundary = f"BEFORE_WRITE:{forward_index}:{row['key']}"
        if not probe(boundary):
            return continue_rollback(
                boundary, RuntimeError("immutable state probe failed")
            )
        attempt_id = scoped_attempt_id(
            validated["execution_id"],
            phase="FORWARD",
            index=forward_index,
            reconciled=True,
        )
        next_pending = {
            "phase": "FORWARD",
            "index": forward_index,
            "key": row["key"],
            "operation": "PUT_CONDITIONAL",
            "attempt_id": attempt_id,
            "expected_identity_sha256": canonical_sha256(row),
        }
        write_ahead = make_receipt(
            "INDETERMINATE", boundary,
            RuntimeError("reconciled forward effect pending"),
            kind="WRITE_AHEAD", pending_effect=next_pending,
        )
        try:
            persist(write_ahead)
        except Exception as error:
            return save_terminal(make_receipt(
                "INDETERMINATE", "RECONCILE_FORWARD_WRITE_AHEAD", error
            ))
        forward_attempted += 1
        try:
            raw = writer.put_conditional(
                copy.deepcopy(row), attempt_id=attempt_id,
                preflight_row=copy.deepcopy(release_preflight["rows"][forward_index]),
                preflight_sha256=release_preflight["preflight_sha256"],
            )
            item = _valid_provider_result(raw, row, attempt_id=attempt_id)
            forward.append(item)
        except ConditionalRequestRefused as error:
            forward_attempted -= 1
            return continue_rollback(boundary, error)
        except Exception as error:
            return save_terminal(make_receipt(
                "INDETERMINATE", boundary, error, pending_effect=next_pending
            ))
        after = f"AFTER_WRITE:{forward_index}:{row['key']}"
        if not probe(after):
            return continue_rollback(after, RuntimeError("immutable state probe failed"))
        try:
            persist(make_receipt(
                "INDETERMINATE", after,
                RuntimeError("reconciled release in progress"), kind="PROGRESS",
            ))
        except Exception as error:
            return save_terminal(make_receipt(
                "INDETERMINATE", "RECONCILE_FORWARD_PROGRESS", error
            ))
    if not probe("BEFORE_TERMINAL"):
        return continue_rollback(
            "BEFORE_TERMINAL", RuntimeError("immutable state probe failed")
        )
    return save_terminal(make_receipt("COMPLETE_SUCCESS", None, None))


def reconcile_release_write_ahead(
    journal: dict[str, Any],
    manifest: dict[str, Any],
    provider_result: dict[str, Any],
) -> dict[str, Any]:
    """Record exact post-restart evidence without retrying or continuing effects."""
    validated = validate_release_receipt(journal, manifest)
    if validated["checkpoint_kind"] != "WRITE_AHEAD" or validated["pending_effect"] is None:
        raise ManifestError("release reconciliation requires a write-ahead checkpoint")
    pending = validated["pending_effect"]
    row = manifest["objects"][pending["index"]]
    result = _valid_provider_result(
        provider_result, row, attempt_id=pending["attempt_id"]
    )
    results = [*validated["provider_results"], result]
    return _release_terminal(
        manifest,
        TerminalState.INDETERMINATE,
        results,
        attempted=len(results),
        observed=len(results),
        boundary="RECONCILED_NO_AUTOMATIC_RETRY",
        error=RuntimeError("explicit operator review required after reconciliation"),
        checkpoint_kind="TERMINAL",
        execution_id=validated["execution_id"],
        approval_assertion_sha256=validated["approval_assertion_sha256"],
        provider_attestation=validated["provider_identity_attestation"],
        release_preflight=validated["release_preflight"],
        pending_effect=None,
    )


def _area_slug(key: str) -> str:
    if key.startswith("silhouettes/"):
        key = key[len("silhouettes/"):]
    return key.removesuffix(".json")


def build_quarantine_plan(
    inventory_receipt: dict[str, Any],
    *,
    active_area_ids: set[str] | frozenset[str],
    source_sha: str,
    source_tree: str,
    workflow_sha256: str,
    root_approval_ref: str,
    receipt_id: str,
) -> dict[str, Any]:
    receipt_id = _require_receipt_id(receipt_id)
    remote = validate_inventory_receipt(inventory_receipt)
    registry = inventory_area_ids(inventory_receipt)
    active = _canonical_area_ids(active_area_ids, "active area registry")
    if not set(active).issubset(registry):
        raise ManifestError("active area registry is outside the inventory area registry")
    source_sha = _require_git_identity(source_sha, "source_sha")
    source_tree = _require_git_identity(source_tree, "source_tree")
    try:
        workflow_sha256 = _require_digest(workflow_sha256, "workflow_sha256")
        root_approval_ref = _require_identity_ref(root_approval_ref, "root_approval_ref")
    except ValueError as error:
        raise ManifestError(str(error)) from error
    rows = []
    for key in sorted(remote):
        key_class = classify_key(key, authoritative_area_ids=registry)
        if key_class not in {KeyClass.AREA_GEOM, KeyClass.AREA_SILHOUETTE}:
            continue
        if _area_slug(key) in active_area_ids:
            continue
        rows.append({
            "source_key": key,
            "source_key_class": key_class.value,
            "source_preimage": copy.deepcopy(remote[key]),
            "quarantine_key": quarantine_key(
                receipt_id, key, authoritative_area_ids=registry
            ),
        })
    protected = [
        {"key": key, "preimage": copy.deepcopy(remote[key])}
        for key in GLOBAL_OBJECTS
    ]
    plan: dict[str, Any] = {
        "schema": QUARANTINE_PLAN_SCHEMA,
        "contract_version": CONTRACT_VERSION,
        "bucket": BUCKET,
        "source_sha": source_sha,
        "source_tree": source_tree,
        "workflow_sha256": workflow_sha256,
        "root_approval_ref": root_approval_ref,
        "account_identity_ref": inventory_receipt["account_identity_ref"],
        "endpoint_identity_ref": inventory_receipt["endpoint_identity_ref"],
        "inventory_receipt_sha256": inventory_receipt["receipt_sha256"],
        "inventory_anchor_ref": inventory_receipt["anchor_ref"],
        "authoritative_denominator": inventory_receipt["authoritative_denominator"],
        "area_registry_sha256": inventory_receipt["area_registry_sha256"],
        "area_registry_ids": list(inventory_receipt["area_registry_ids"]),
        "receipt_id": receipt_id,
        "active_area_count": len(active),
        "active_area_ids": list(active),
        "active_area_ids_sha256": canonical_sha256(list(active)),
        "protected_preimages": protected,
        "object_count": len(rows),
        "objects": rows,
        "operation": "NO_CLOBBER_COPY_VERIFY_CURRENT_REMOVE",
        "final_deletion_authorized": False,
    }
    plan["plan_sha256"] = canonical_sha256(plan)
    validate_quarantine_plan(plan, inventory_receipt=inventory_receipt)
    return plan


def _validate_quarantine_plan(
    plan: Any,
    *,
    inventory_receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(plan, dict):
        raise ManifestError("quarantine plan is not an object")
    fields = {
        "schema", "contract_version", "bucket", "source_sha", "source_tree",
        "workflow_sha256", "root_approval_ref", "account_identity_ref",
        "endpoint_identity_ref", "inventory_receipt_sha256", "inventory_anchor_ref",
        "authoritative_denominator", "area_registry_sha256", "area_registry_ids",
        "receipt_id", "active_area_count", "active_area_ids",
        "active_area_ids_sha256", "protected_preimages", "object_count", "objects",
        "operation", "final_deletion_authorized", "plan_sha256",
    }
    if set(plan) != fields:
        raise ManifestError("quarantine plan schema is not exact")
    if (
        plan["schema"] != QUARANTINE_PLAN_SCHEMA
        or plan["contract_version"] != CONTRACT_VERSION
        or plan["bucket"] != BUCKET
        or plan["operation"] != "NO_CLOBBER_COPY_VERIFY_CURRENT_REMOVE"
        or plan["final_deletion_authorized"] is not False
    ):
        raise ManifestError("quarantine plan identity or operation is invalid")
    _require_git_identity(plan["source_sha"], "source_sha")
    _require_git_identity(plan["source_tree"], "source_tree")
    try:
        _require_digest(plan["workflow_sha256"], "workflow_sha256")
        _require_identity_ref(plan["root_approval_ref"], "root_approval_ref")
        _require_identity_ref(plan["account_identity_ref"], "account_identity_ref")
        _require_identity_ref(plan["endpoint_identity_ref"], "endpoint_identity_ref")
        _require_digest(plan["inventory_receipt_sha256"], "inventory_receipt_sha256")
        _require_identity_ref(plan["inventory_anchor_ref"], "inventory_anchor_ref")
        _require_digest(plan["area_registry_sha256"], "area_registry_sha256")
        _require_digest(plan["active_area_ids_sha256"], "active_area_ids_sha256")
        receipt_id = _require_receipt_id(plan["receipt_id"])
        digest = _require_digest(plan["plan_sha256"], "plan_sha256")
    except ValueError as error:
        raise ManifestError(str(error)) from error
    if digest != canonical_sha256(plan, omit={"plan_sha256"}):
        raise ManifestError("quarantine plan digest mismatch")
    registry = _canonical_area_ids(plan["area_registry_ids"], "quarantine area registry")
    active = _canonical_area_ids(plan["active_area_ids"], "quarantine active registry")
    registry_set = frozenset(registry)
    if (
        plan["area_registry_sha256"] != canonical_sha256(list(registry))
        or plan["active_area_count"] != len(active)
        or plan["active_area_ids_sha256"] != canonical_sha256(list(active))
        or not set(active).issubset(registry_set)
    ):
        raise ManifestError("quarantine registry/active-set closure mismatch")
    protected = plan["protected_preimages"]
    if not isinstance(protected, list) or [row.get("key") for row in protected] != list(GLOBAL_OBJECTS):
        raise ManifestError("quarantine protected preimage set mismatch")
    for number, row in enumerate(protected, start=1):
        if not isinstance(row, dict) or set(row) != {"key", "preimage"}:
            raise ManifestError("quarantine protected preimage schema is invalid")
        if row["preimage"] != _normalize_preimage(row["preimage"], f"protected {number}"):
            raise ManifestError("quarantine protected preimage is noncanonical")
    rows = plan["objects"]
    if not isinstance(rows, list) or plan["object_count"] != len(rows):
        raise ManifestError("quarantine object count mismatch")
    previous: str | None = None
    for number, row in enumerate(rows, start=1):
        if not isinstance(row, dict) or set(row) != {
            "source_key", "source_key_class", "source_preimage", "quarantine_key"
        }:
            raise ManifestError(f"quarantine row {number} schema is not exact")
        source = row["source_key"]
        source_class = classify_key(source, authoritative_area_ids=registry_set)
        if source_class not in {KeyClass.AREA_GEOM, KeyClass.AREA_SILHOUETTE}:
            raise ManifestError("quarantine source is not a registered area object")
        if row["source_key_class"] != source_class.value:
            raise ManifestError("quarantine source class mismatch")
        if previous is not None and source <= previous:
            raise ManifestError("quarantine sources are duplicate or unsorted")
        previous = source
        if _area_slug(source) in active:
            raise ManifestError("quarantine plan selects an active area")
        if row["source_preimage"] != _normalize_preimage(row["source_preimage"], f"quarantine row {number}"):
            raise ManifestError("quarantine source preimage is noncanonical")
        if row["quarantine_key"] != quarantine_key(
            receipt_id, source, authoritative_area_ids=registry_set
        ):
            raise ManifestError("quarantine destination is not canonical")
    if inventory_receipt is not None:
        remote = validate_inventory_receipt(inventory_receipt)
        expected_bindings = {
            "inventory_receipt_sha256": inventory_receipt["receipt_sha256"],
            "inventory_anchor_ref": inventory_receipt["anchor_ref"],
            "account_identity_ref": inventory_receipt["account_identity_ref"],
            "endpoint_identity_ref": inventory_receipt["endpoint_identity_ref"],
            "authoritative_denominator": inventory_receipt["authoritative_denominator"],
            "area_registry_sha256": inventory_receipt["area_registry_sha256"],
            "area_registry_ids": inventory_receipt["area_registry_ids"],
        }
        for field, value in expected_bindings.items():
            if plan[field] != value:
                raise ManifestError(f"quarantine plan has wrong {field}")
        expected_sources = [
            key for key in sorted(remote)
            if classify_key(key, authoritative_area_ids=registry_set)
            in {KeyClass.AREA_GEOM, KeyClass.AREA_SILHOUETTE}
            and _area_slug(key) not in active
        ]
        if [row["source_key"] for row in rows] != expected_sources:
            raise ManifestError("quarantine plan omits or adds a derived source")
        for row in rows:
            if remote[row["source_key"]] != row["source_preimage"]:
                raise ManifestError("quarantine plan source preimage differs from inventory")
    return copy.deepcopy(plan)


def validate_quarantine_plan(
    plan: Any,
    *,
    inventory_receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _validate_quarantine_plan(plan, inventory_receipt=inventory_receipt)


def _same_object_bytes(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return all(left.get(field) == right.get(field) for field in ("etag", "bytes", "sha256"))


def _quarantine_terminal(
    plan: dict[str, Any],
    state: TerminalState,
    rows: list[dict[str, Any]],
    *,
    attempted: int,
    observed: int,
    boundary: str | None,
    error: BaseException | None,
    checkpoint_kind: str,
    execution_id: str,
    approval_assertion_sha256: str,
    provider_attestation: dict[str, Any],
    pending_effect: dict[str, Any] | None,
) -> dict[str, Any]:
    receipt: dict[str, Any] = {
        "schema": QUARANTINE_RECEIPT_SCHEMA,
        "contract_version": CONTRACT_VERSION,
        "terminal_state": state.value,
        "checkpoint_kind": checkpoint_kind,
        "execution_id": execution_id,
        "approval_assertion_sha256": approval_assertion_sha256,
        "provider_identity_attestation": copy.deepcopy(provider_attestation),
        "bucket": BUCKET,
        "plan_sha256": plan["plan_sha256"],
        "inventory_receipt_sha256": plan["inventory_receipt_sha256"],
        "inventory_anchor_ref": plan["inventory_anchor_ref"],
        "root_approval_ref": plan["root_approval_ref"],
        "object_count": plan["object_count"],
        "attempted_effects": attempted,
        "observed_effects": observed,
        "objects": copy.deepcopy(rows),
        "pending_effect": copy.deepcopy(pending_effect),
        "error_boundary": boundary,
        "error_type": type(error).__name__ if error is not None else None,
        "final_deletion_authorized": False,
    }
    receipt["receipt_sha256"] = canonical_sha256(receipt)
    validate_quarantine_receipt(receipt, plan)
    return receipt


def validate_quarantine_receipt(
    receipt: Any,
    plan: dict[str, Any],
) -> dict[str, Any]:
    plan = _validate_quarantine_plan(plan)
    fields = {
        "schema", "contract_version", "terminal_state", "checkpoint_kind",
        "execution_id", "approval_assertion_sha256", "provider_identity_attestation",
        "bucket", "plan_sha256", "inventory_receipt_sha256", "inventory_anchor_ref",
        "root_approval_ref", "object_count", "attempted_effects", "observed_effects",
        "objects", "pending_effect", "error_boundary", "error_type",
        "final_deletion_authorized", "receipt_sha256",
    }
    if not isinstance(receipt, dict) or set(receipt) != fields:
        raise ManifestError("quarantine receipt schema is not exact")
    if (
        receipt["schema"] != QUARANTINE_RECEIPT_SCHEMA
        or receipt["contract_version"] != CONTRACT_VERSION
        or receipt["bucket"] != BUCKET
    ):
        raise ManifestError("quarantine receipt identity mismatch")
    for field in (
        "plan_sha256", "inventory_receipt_sha256", "inventory_anchor_ref",
        "root_approval_ref", "object_count",
    ):
        expected = plan["object_count"] if field == "object_count" else plan[field]
        if receipt[field] != expected:
            raise ManifestError(f"quarantine receipt has wrong {field}")
    if not isinstance(receipt["execution_id"], str) or not _SAFE_VERSION_RE.fullmatch(receipt["execution_id"]):
        raise ManifestError("quarantine execution identity is invalid")
    try:
        state = TerminalState(receipt["terminal_state"])
        digest = _require_digest(receipt["receipt_sha256"], "receipt_sha256")
        _require_digest(receipt["approval_assertion_sha256"], "approval_assertion_sha256")
    except (TypeError, ValueError) as error:
        raise ManifestError("quarantine receipt state/digest is invalid") from error
    if digest != canonical_sha256(receipt, omit={"receipt_sha256"}):
        raise ManifestError("quarantine receipt digest mismatch")
    validate_provider_identity_attestation(
        receipt["provider_identity_attestation"],
        expected_account_identity_ref=plan["account_identity_ref"],
        expected_endpoint_identity_ref=plan["endpoint_identity_ref"],
    )
    outcomes = receipt["objects"]
    if not isinstance(outcomes, list) or len(outcomes) > plan["object_count"]:
        raise ManifestError("quarantine receipt object closure is invalid")
    transactions: list[str] = []
    observed_from_rows = 0
    for index, outcome in enumerate(outcomes):
        expected_fields = {
            "source_key", "quarantine_key", "copy_transaction_id",
            "remove_transaction_id", "quarantine_preimage",
            "copy_verified", "source_removed",
        }
        if not isinstance(outcome, dict) or set(outcome) != expected_fields:
            raise ManifestError(f"quarantine receipt row {index + 1} schema is not exact")
        planned = plan["objects"][index]
        if (
            outcome["source_key"] != planned["source_key"]
            or outcome["quarantine_key"] != planned["quarantine_key"]
            or not isinstance(outcome["copy_verified"], bool)
            or not isinstance(outcome["source_removed"], bool)
        ):
            raise ManifestError("quarantine receipt row is not bound to its plan")
        copy_tx = outcome["copy_transaction_id"]
        remove_tx = outcome["remove_transaction_id"]
        for name, value in (("copy", copy_tx), ("remove", remove_tx)):
            if value is not None and (
                not isinstance(value, str) or not _SAFE_VERSION_RE.fullmatch(value)
            ):
                raise ManifestError(f"quarantine {name} transaction identity is invalid")
        if copy_tx is not None:
            transactions.append(copy_tx)
            observed_from_rows += 1
        if remove_tx is not None:
            transactions.append(remove_tx)
            observed_from_rows += 1
        if outcome["copy_verified"]:
            if copy_tx is None or outcome["quarantine_preimage"] is None:
                raise ManifestError("verified quarantine copy lacks identity/preimage")
            destination = _normalize_preimage(
                outcome["quarantine_preimage"], f"quarantine receipt row {index + 1}"
            )
            if (
                destination != outcome["quarantine_preimage"]
                or not _same_object_bytes(destination, planned["source_preimage"])
            ):
                raise ManifestError("quarantine copy is not byte-exact")
        elif outcome["quarantine_preimage"] is not None or copy_tx is not None:
            raise ManifestError("unverified quarantine copy carries provider identity")
        if outcome["source_removed"] and (
            not outcome["copy_verified"] or remove_tx is None
        ):
            raise ManifestError("source removal is not downstream of verified copy")
        if not outcome["source_removed"] and remove_tx is not None:
            raise ManifestError("unverified source removal carries provider identity")
    if len(transactions) != len(set(transactions)):
        raise ManifestError("quarantine transaction identities are duplicated")
    attempted = receipt["attempted_effects"]
    observed = receipt["observed_effects"]
    if (
        not isinstance(attempted, int) or isinstance(attempted, bool)
        or not isinstance(observed, int) or isinstance(observed, bool)
        or observed != observed_from_rows
    ):
        raise ManifestError("quarantine effect counts are inconsistent")
    pending = receipt["pending_effect"]
    if pending is None:
        if attempted != observed:
            raise ManifestError("quarantine count has no pending effect")
    else:
        pending_fields = {
            "index", "operation", "source_key", "quarantine_key", "attempt_id",
            "reviewed_preimage",
        }
        if not isinstance(pending, dict) or set(pending) != pending_fields:
            raise ManifestError("quarantine pending-effect schema is not exact")
        index = pending["index"]
        if (
            not isinstance(index, int) or isinstance(index, bool)
            or not 0 <= index < plan["object_count"]
            or pending["source_key"] != plan["objects"][index]["source_key"]
            or pending["quarantine_key"] != plan["objects"][index]["quarantine_key"]
            or pending["reviewed_preimage"] != plan["objects"][index]["source_preimage"]
            or pending["operation"] not in {"COPY_IF_MATCH", "REMOVE_AFTER_EXACT_PROOF"}
            or not isinstance(pending["attempt_id"], str)
            or not _SAFE_VERSION_RE.fullmatch(pending["attempt_id"])
            or attempted != observed + 1
            or index >= len(outcomes)
        ):
            raise ManifestError("quarantine pending effect is not exact")
        outcome = outcomes[index]
        if pending["operation"] == "COPY_IF_MATCH" and outcome["copy_verified"]:
            raise ManifestError("quarantine copy intent follows an observed copy")
        if pending["operation"] == "REMOVE_AFTER_EXACT_PROOF" and (
            not outcome["copy_verified"] or outcome["source_removed"]
        ):
            raise ManifestError("quarantine removal intent violates ordering")
    if receipt["checkpoint_kind"] not in {"WRITE_AHEAD", "PROGRESS", "TERMINAL"}:
        raise ManifestError("quarantine checkpoint kind is invalid")
    if receipt["checkpoint_kind"] == "WRITE_AHEAD" and (
        state is not TerminalState.INDETERMINATE or pending is None
    ):
        raise ManifestError("quarantine write-ahead checkpoint is malformed")
    if receipt["checkpoint_kind"] == "PROGRESS" and (
        state is not TerminalState.INDETERMINATE or pending is not None
    ):
        raise ManifestError("quarantine progress checkpoint is malformed")
    if receipt["final_deletion_authorized"] is not False:
        raise ManifestError("quarantine receipt claims final-deletion authority")
    if state is TerminalState.COMPLETE_SUCCESS:
        if (
            receipt["checkpoint_kind"] != "TERMINAL" or pending is not None
            or len(outcomes) != plan["object_count"]
            or attempted != 2 * plan["object_count"] or observed != attempted
            or any(not row["copy_verified"] or not row["source_removed"] for row in outcomes)
            or receipt["error_boundary"] is not None or receipt["error_type"] is not None
        ):
            raise ManifestError("COMPLETE_SUCCESS quarantine receipt is incomplete")
    elif state is TerminalState.SAFE_ABORT:
        if (
            receipt["checkpoint_kind"] != "TERMINAL" or pending is not None
            or attempted != 0 or observed != 0
            or not receipt["error_boundary"] or not receipt["error_type"]
        ):
            raise ManifestError("SAFE_ABORT quarantine receipt records an effect or lacks cause")
    elif not receipt["error_boundary"] or not receipt["error_type"]:
        raise ManifestError("INDETERMINATE quarantine receipt lacks uncertainty boundary")
    return copy.deepcopy(receipt)


def execute_quarantine(
    plan: dict[str, Any],
    store: QuarantineStore,
    *,
    approval_assertion: dict[str, Any],
    approval_key: bytes | str,
    execution_id: str | None = None,
    checkpoint: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Copy exact current bytes, verify, then remove after one exact proof."""
    plan = _validate_quarantine_plan(plan)
    approval_digest = validate_approval_assertion(
        approval_assertion, plan, operation="QUARANTINE", key=approval_key
    )
    attestation = validate_provider_identity_attestation(
        store.attest_identity(),
        expected_account_identity_ref=plan["account_identity_ref"],
        expected_endpoint_identity_ref=plan["endpoint_identity_ref"],
    )
    execution_id = execution_id or ("quarantine-" + secrets.token_hex(16))
    if not isinstance(execution_id, str) or not _SAFE_VERSION_RE.fullmatch(execution_id):
        raise ManifestError("quarantine execution identity is invalid")
    persist = checkpoint or (lambda _document: None)
    outcomes: list[dict[str, Any]] = []
    attempted = 0
    observed = 0

    def receipt(
        state: TerminalState,
        boundary: str | None,
        error: BaseException | None,
        *,
        kind: str,
        pending: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return _quarantine_terminal(
            plan, state, outcomes, attempted=attempted, observed=observed,
            boundary=boundary, error=error, checkpoint_kind=kind,
            execution_id=execution_id, approval_assertion_sha256=approval_digest,
            provider_attestation=attestation, pending_effect=pending,
        )

    def terminal(
        boundary: str,
        error: BaseException,
        pending: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        state = TerminalState.SAFE_ABORT if attempted == 0 else TerminalState.INDETERMINATE
        document = receipt(state, boundary, error, kind="TERMINAL", pending=pending)
        try:
            persist(document)
        except Exception:
            pass
        return document

    protected_before: dict[str, dict[str, Any]] = {}
    try:
        for row in plan["protected_preimages"]:
            actual = store.head(row["key"])
            if actual != row["preimage"]:
                raise ManifestError(f"protected object changed before quarantine: {row['key']}")
            protected_before[row["key"]] = copy.deepcopy(actual)
    except Exception as error:
        return terminal("PROTECTED_PRECHECK", error)

    for index, row in enumerate(plan["objects"]):
        source = row["source_key"]
        destination = row["quarantine_key"]
        expected = row["source_preimage"]
        outcome = {
            "source_key": source,
            "quarantine_key": destination,
            "copy_transaction_id": None,
            "remove_transaction_id": None,
            "quarantine_preimage": None,
            "copy_verified": False,
            "source_removed": False,
        }
        outcomes.append(outcome)
        pending: dict[str, Any] | None = None
        try:
            if store.head(source) != expected:
                raise ManifestError("source reviewed preimage is stale or absent")
            if store.head(destination) is not None:
                raise ManifestError("quarantine destination already exists")
            copy_attempt = f"{execution_id}-copy-{index}"
            pending = {
                "index": index,
                "operation": "COPY_IF_MATCH",
                "source_key": source,
                "quarantine_key": destination,
                "attempt_id": copy_attempt,
                "reviewed_preimage": copy.deepcopy(expected),
            }
            attempted += 1
            try:
                persist(receipt(
                    TerminalState.INDETERMINATE, f"BEFORE_COPY:{index}",
                    RuntimeError("copy pending reconciliation"),
                    kind="WRITE_AHEAD", pending=pending,
                ))
            except Exception as error:
                attempted -= 1
                return terminal(f"COPY_WRITE_AHEAD:{index}", error)
            copied = store.copy_no_clobber(
                source, destination, source_preimage=copy.deepcopy(expected),
                attempt_id=copy_attempt,
            )
            if not isinstance(copied, dict) or set(copied) != {
                "transaction_id", "attempt_id"
            }:
                raise ManifestError("copy result schema is not exact")
            copy_tx = copied["transaction_id"]
            if (
                copied["attempt_id"] != copy_attempt
                or not isinstance(copy_tx, str)
                or not _SAFE_VERSION_RE.fullmatch(copy_tx)
            ):
                raise ManifestError("copy result identity is invalid")
            destination_preimage = store.head(destination)
            if destination_preimage is None:
                raise ManifestError("quarantine copy is absent after provider success")
            destination_preimage = _normalize_preimage(
                destination_preimage, f"quarantine destination {index}"
            )
            if not _same_object_bytes(destination_preimage, expected):
                raise ManifestError("quarantine copy failed exact content verification")
            prior_transactions = {
                value for prior in outcomes for value in (
                    prior["copy_transaction_id"], prior["remove_transaction_id"]
                ) if value is not None
            }
            if copy_tx in prior_transactions:
                raise ManifestError("copy transaction identity is duplicated")
            outcome["copy_transaction_id"] = copy_tx
            outcome["quarantine_preimage"] = destination_preimage
            outcome["copy_verified"] = True
            observed += 1
            persist(receipt(
                TerminalState.INDETERMINATE, f"COPY_VERIFIED:{index}",
                RuntimeError("quarantine in progress"),
                kind="PROGRESS", pending=None,
            ))

            if store.head(source) != expected:
                raise ManifestError("source changed after copy")
            remove_attempt = f"{execution_id}-remove-{index}"
            pending = {
                "index": index,
                "operation": "REMOVE_AFTER_EXACT_PROOF",
                "source_key": source,
                "quarantine_key": destination,
                "attempt_id": remove_attempt,
                "reviewed_preimage": copy.deepcopy(expected),
            }
            attempted += 1
            try:
                persist(receipt(
                    TerminalState.INDETERMINATE, f"BEFORE_REMOVE:{index}",
                    RuntimeError("removal pending reconciliation"),
                    kind="WRITE_AHEAD", pending=pending,
                ))
            except Exception as error:
                attempted -= 1
                return terminal(f"REMOVE_WRITE_AHEAD:{index}", error)
            removed = store.remove_after_exact_proof(
                source, source_preimage=copy.deepcopy(expected),
                attempt_id=remove_attempt,
            )
            if not isinstance(removed, dict) or set(removed) != {
                "transaction_id", "attempt_id"
            }:
                raise ManifestError("source-removal result schema is not exact")
            remove_tx = removed["transaction_id"]
            prior_transactions = {
                value for prior in outcomes for value in (
                    prior["copy_transaction_id"], prior["remove_transaction_id"]
                ) if value is not None
            }
            if (
                removed["attempt_id"] != remove_attempt
                or not isinstance(remove_tx, str)
                or not _SAFE_VERSION_RE.fullmatch(remove_tx)
                or remove_tx in prior_transactions
            ):
                raise ManifestError("source-removal result identity is invalid")
            if store.head(source) is not None:
                raise ManifestError("source remains after removal")
            outcome["remove_transaction_id"] = remove_tx
            outcome["source_removed"] = True
            observed += 1
            persist(receipt(
                TerminalState.INDETERMINATE, f"SOURCE_REMOVED:{index}",
                RuntimeError("quarantine in progress"),
                kind="PROGRESS", pending=None,
            ))
        except Exception as error:
            unresolved = pending if attempted == observed + 1 else None
            return terminal(f"OBJECT:{index}", error, unresolved)

    try:
        for key, before in protected_before.items():
            if store.head(key) != before:
                raise ManifestError(f"protected object changed during quarantine: {key}")
    except Exception as error:
        return terminal("PROTECTED_POSTCHECK", error)
    complete = receipt(
        TerminalState.COMPLETE_SUCCESS, None, None, kind="TERMINAL", pending=None
    )
    try:
        persist(complete)
    except Exception as error:
        return terminal("TERMINAL_RECEIPT", error)
    return complete


def reconcile_quarantine_write_ahead(
    journal: dict[str, Any],
    plan: dict[str, Any],
    *,
    provider_result: dict[str, Any],
    observed_preimage: dict[str, Any] | None,
) -> dict[str, Any]:
    """Seal exact post-restart evidence without retrying quarantine effects."""
    validated = validate_quarantine_receipt(journal, plan)
    if validated["checkpoint_kind"] != "WRITE_AHEAD" or validated["pending_effect"] is None:
        raise ManifestError("quarantine reconciliation requires a write-ahead checkpoint")
    pending = validated["pending_effect"]
    if not isinstance(provider_result, dict) or set(provider_result) != {
        "transaction_id", "attempt_id"
    }:
        raise ManifestError("quarantine reconciliation result schema is not exact")
    if provider_result["attempt_id"] != pending["attempt_id"]:
        raise ManifestError("quarantine reconciliation attempt identity mismatch")
    for field in ("transaction_id", "attempt_id"):
        if not isinstance(provider_result[field], str) or not _SAFE_VERSION_RE.fullmatch(provider_result[field]):
            raise ManifestError(f"quarantine reconciliation {field} is invalid")
    outcomes = copy.deepcopy(validated["objects"])
    outcome = outcomes[pending["index"]]
    if pending["operation"] == "COPY_IF_MATCH":
        if observed_preimage is None:
            raise ManifestError("copy reconciliation lacks destination preimage")
        normalized = _normalize_preimage(
            observed_preimage, "reconciled quarantine destination"
        )
        if not _same_object_bytes(normalized, pending["reviewed_preimage"]):
            raise ManifestError("reconciled copy is not exact")
        outcome["copy_transaction_id"] = provider_result["transaction_id"]
        outcome["quarantine_preimage"] = normalized
        outcome["copy_verified"] = True
    else:
        if observed_preimage is not None:
            raise ManifestError("removal reconciliation found source present")
        outcome["remove_transaction_id"] = provider_result["transaction_id"]
        outcome["source_removed"] = True
    observed = validated["observed_effects"] + 1
    return _quarantine_terminal(
        plan,
        TerminalState.INDETERMINATE,
        outcomes,
        attempted=observed,
        observed=observed,
        boundary="RECONCILED_NO_AUTOMATIC_RETRY",
        error=RuntimeError("explicit operator review required after reconciliation"),
        checkpoint_kind="TERMINAL",
        execution_id=validated["execution_id"],
        approval_assertion_sha256=validated["approval_assertion_sha256"],
        provider_attestation=validated["provider_identity_attestation"],
        pending_effect=None,
    )


_LEASED_MARKERS: Mapping[str, tuple[str, ...]] = {
    ".github/workflows/sync-geom-to-r2.yml": (
        "approved_manifest_sha256", "plan_only", "stage-changed-r2.py", "apply-manifest",
        "git symbolic-ref -q HEAD", "--untracked-files=all", "GITHUB_SHA",
        "sha256sum .github/workflows/sync-geom-to-r2.yml",
        "account_identity_ref", "endpoint_identity_ref", "ACCOUNT_IDENTITY_REF", "ENDPOINT_IDENTITY_REF",
        "environment: trekdex-r2-release", "R2_ROOT_APPROVAL_ASSERTION_JSON",
        "R2_ROOT_APPROVAL_HMAC_KEY", "--area-index", "--global-object",
        'python3 scripts/build-trail-search-index.py --out "$TRAIL_SEARCH"',
        'python3 scripts/build-parking-pool.py --out "$PARKING"',
        'python3 scripts/build-trail-shapes.py --out "$TRAIL_SHAPES"',
        "PUBLICATION_AREA_ID", "--publication-area-id",
        "AWS_CLI_VERSION", "AWS_CLI_ARCHIVE_SHA256", "sha256sum --check --strict",
        "Validate sync input shape before secrets",
        "steps.input_shape.outputs.source_sha",
        "steps.input_shape.outputs.approved_manifest_sha256",
        "reconcile_run_id", "reconcile-scoped",
        "group: sync-geom-to-r2", "stage/rollback-manifest.json", "stage/rollback/**",
        "stage/objects/747261696c2d7365617263682e6a736f6e.object",
        'cp -R "$RECOVERY_ROOT/stage" "$STAGE_ROOT"',
    ),
    ".github/workflows/cleanup-r2-orphans.yml": (
        "approved_quarantine_plan_sha256", "plan_only", "cleanup-r2-orphans.py", "apply-quarantine",
        "git symbolic-ref -q HEAD", "--untracked-files=all", "GITHUB_SHA",
        "account_identity_ref", "endpoint_identity_ref", "ACCOUNT_IDENTITY_REF", "ENDPOINT_IDENTITY_REF",
        "environment: trekdex-r2-release", "R2_ROOT_APPROVAL_ASSERTION_JSON",
        "R2_ROOT_APPROVAL_HMAC_KEY", "--registry", "--inventory \"$INVENTORY\"",
        "group: sync-geom-to-r2",
    ),
    ".github/workflows/trailforge-publish.yml": (
        "mols_release_input_receipt_b64", "verify-release-input",
        "Validate publication input shape before secrets",
        "steps.input_shape.outputs.mols_release_input_receipt_b64",
        "persist-credentials: false",
        "mols-release-index.json", "merge-mols-index",
        "R2_ROOT_APPROVAL_HMAC_KEY", "--only-area \"$VERIFIED_AREA_ID\"",
        "verify-mols-staged-diff", "verify-mols-commit",
        "RELEASE_INPUT_RECEIPT_SHA256",
        "steps.release_input.outputs.root_approval_ref",
        "steps.release_input.outputs.account_identity_ref",
        "steps.release_input.outputs.endpoint_identity_ref",
        "steps.release_input.outputs.inventory_anchor_ref",
        "steps.release_input.outputs.authoritative_denominator",
        "steps.published_commit.outputs.source_sha",
        "steps.published_commit.outputs.source_tree", "-f plan_only=true",
    ),
    ".github/workflows/trailforge-publish-us.yml": (
        "trailforge_candidate_contract.py", "fan-in-terminal.json", "review_candidate_allowed",
        "git symbolic-ref -q HEAD", "--untracked-files=all", "GITHUB_SHA",
        "sha256sum .github/workflows/trailforge-publish-us.yml", "root_approval_ref",
        "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "CANDIDATE_CONFIG_SHA256",
        "TOOLCHAIN_MANIFEST_SHA256", "APPROVED_INPUT_SET_SHA256",
        "approved_input_manifest_b64", "approved_toolchain_manifest_b64",
        "artifact_service_ref", "runtime toolchain differs from root-approved exact manifest",
        "fan-in tool bytes differ from root-approved manifest",
        "input-manifest.json", "ARTIFACT_SERVICE_REF", "python3 serve/publish_areas.py",
    ),
    ".github/workflows/release-contracts-ci.yml": (
        "scripts/test_r2_release_contract.py", "scripts/reproduce_r2_repair_failure.py",
        "scripts/test_publish_workflow_pilot.py", "scripts/test_publish_workflow_region.py",
        "scripts/test_filter_bundle_dk.py", "trailforge/serve/test_publish_report.py",
        "scripts/test_trailforge_candidate_contract.py",
        "trailforge/serve/publish_areas.py", "scripts/build-trail-search-index.py",
        "scripts/build-parking-pool.py", "scripts/build-trail-shapes.py",
        "scripts/merge-published-geom.py", "scripts/silhouettes-from-geom.py",
        "scripts/filter-ios-bundle.py",
    ),
    "scripts/stage-changed-r2.py": (
        "r2_release_contract", "apply-manifest", "execute_scoped_release",
        "verify-release-input", "merge-mols-index", "verify-mols-staged-diff", "verify-mols-commit",
        "reconcile-scoped",
        "reconcile_scoped_release_write_ahead", "rollback_delete_created",
        "validate_rollback_result_prefix", "validate_scoped_attempt_id",
        "scoped_transaction_id",
        "DELETE_CREATED_AFTER_EXACT_PROOF", '"delete-object"',
        '"--if-match"', '"--if-none-match"', "--publication-area-id",
        "read_reviewed_baseline_bytes", "_build_scoped_semantic_deltas",
        "_validate_recovery_material", "_validate_recovery_stage",
        "_sealed_recovery_bytes",
        "semantic_deltas=semantic_deltas",
        "aws_cli_version", "aws_cli_archive_sha256",
    ),
    "scripts/cleanup-r2-orphans.py": ("r2_release_contract", "apply-quarantine"),
    "scripts/r2_release_contract.py": (
        CONTRACT_VERSION,
        "validate_rollback_result_prefix",
        "forward_results=copy.deepcopy(forward)",
        "validate_scoped_attempt_id",
        "scoped_transaction_id",
        "scoped_journal_authentication",
        "validate_scoped_journal_authentication(validated, key=approval_key)",
    ),
    "scripts/trailforge_candidate_contract.py": ("trekdex-trailforge-candidate-contract-v3",),
    "scripts/test_r2_release_contract.py": (
        "RR2-ORIGINAL-CLAUSES-R2-V1",
        "test_secret_bearing_workflow_run_scalars_use_validated_env_only",
        "test_fake_reviewer_probe_rejects_invented_prior_identities_without_calls",
        "test_authenticated_terminal_rejects_permitted_form_rewrite_without_calls",
        "test_pending_recovery_fake_and_production_parity_campaign",
        "test_recovery_workflow_cli_reaches_each_shared_pending_effect",
        "test_every_key_forward_rollback_state_has_fake_production_parity",
        "test_recovery_branch_never_restages_and_uploads_exact_candidates",
        "def _metadata_matches(",
    ),
    "scripts/test_trailforge_candidate_contract.py": ("RR2-ORIGINAL-CLAUSES-TRAILFORGE-V1",),
}


def derive_production_producer_graph(
    files: Mapping[str, str],
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Derive all current output/candidate producers from real workflow seams."""
    observed: list[tuple[str, tuple[str, ...]]] = []
    for workflow_path, marker, source_path, capabilities in PRODUCTION_PRODUCER_BOUNDARIES:
        workflow = files.get(workflow_path, "")
        count = workflow.count(marker)
        if count == 1:
            observed.append((source_path, capabilities))
        elif count > 1:
            # Preserve the duplicate in the derived graph so exact-set/uniqueness
            # validation cannot silently collapse an ambiguous production seam.
            observed.extend((source_path, capabilities) for _ in range(count))
    return tuple(sorted(observed))


def audit_repository_contract(files: Mapping[str, str]) -> list[str]:
    """Static fail-closed audit used by no-secret CI and hostile mutations."""
    errors: list[str] = []
    producer_pins = {path: digest for path, digest, _capabilities in CI_PRODUCER_GRAPH}
    registry_graph = tuple(sorted(
        (path, capabilities)
        for path, _digest, capabilities in CI_PRODUCER_GRAPH
    ))
    if (
        len(CI_PRODUCER_GRAPH) != len(PRODUCTION_PRODUCER_BOUNDARIES)
        or len(producer_pins) != len(CI_PRODUCER_GRAPH)
        or len(registry_graph) != len(set(registry_graph))
    ):
        errors.append("producer graph: registry nodes are missing, duplicated, or extra")
    production_graph = derive_production_producer_graph(files)
    for workflow_path, marker, source_path, _capabilities in PRODUCTION_PRODUCER_BOUNDARIES:
        count = files.get(workflow_path, "").count(marker)
        if count != 1:
            errors.append(
                f"producer graph: {source_path} production boundary occurs {count} times"
            )
    if production_graph != registry_graph:
        registry_set = set(registry_graph)
        production_set = set(production_graph)
        for source_path, capabilities in sorted(registry_set - production_set):
            errors.append(
                f"producer graph: registry-only node {source_path} {capabilities}"
            )
        for source_path, capabilities in sorted(production_set - registry_set):
            errors.append(
                f"producer graph: production-only node {source_path} {capabilities}"
            )
        if len(production_graph) != len(set(production_graph)):
            errors.append("producer graph: production boundaries are duplicated")
    expected_paths = set(_LEASED_MARKERS) | set(producer_pins)
    if set(files) != expected_paths:
        for missing in sorted(expected_paths - set(files)):
            errors.append(f"{missing}: audited path is missing")
        for extra in sorted(set(files) - expected_paths):
            errors.append(f"{extra}: path is outside the exact audited producer/consumer graph")
    for path, markers in _LEASED_MARKERS.items():
        text = files.get(path, "")
        for marker in markers:
            if marker not in text:
                errors.append(f"{path}: required contract seam missing: {marker}")
    for path, expected_digest in producer_pins.items():
        text = files.get(path)
        if not isinstance(text, str):
            continue
        observed_digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if observed_digest != expected_digest:
            errors.append(
                f"{path}: producer bytes differ from reviewed graph identity "
                f"{expected_digest}; observed {observed_digest}"
            )
    workflow_text = "\n".join(
        files.get(path, "") for path in (
            ".github/workflows/sync-geom-to-r2.yml",
            ".github/workflows/cleanup-r2-orphans.yml",
            ".github/workflows/trailforge-publish-us.yml",
            ".github/workflows/release-contracts-ci.yml",
        )
    )
    for forbidden in ("git push", "git rebase", "git commit", "aws s3 sync", "delete-objects", "aws s3 rm"):
        if forbidden in workflow_text:
            errors.append(f"workflow: forbidden mutable/recursive/final operation: {forbidden}")
    cleanup_workflow = files.get(".github/workflows/cleanup-r2-orphans.yml", "")
    if "apply_quarantine" not in cleanup_workflow:
        errors.append(".github/workflows/cleanup-r2-orphans.yml: quarantine apply gate missing")
    ci = files.get(".github/workflows/release-contracts-ci.yml", "")
    if "paths:" in ci or "paths-ignore:" in ci:
        errors.append(".github/workflows/release-contracts-ci.yml: restrictive trigger filter can bypass safety CI")
    for event in ("pull_request: {}", "push: {}", "workflow_dispatch: {}"):
        if event not in ci:
            errors.append(f".github/workflows/release-contracts-ci.yml: missing unfiltered event {event}")
    if "secrets." in ci or "aws " in ci or "curl " in ci:
        errors.append(".github/workflows/release-contracts-ci.yml: CI is not no-secret/no-network")
    for path in (
        ".github/workflows/sync-geom-to-r2.yml",
        ".github/workflows/cleanup-r2-orphans.yml",
        ".github/workflows/trailforge-publish-us.yml",
    ):
        if re.search(r"(?m)(?:^|\s)aws\s", files.get(path, "")):
            errors.append(f"{path}: workflow bypasses the injected contract provider adapter")
    sync = files.get(".github/workflows/sync-geom-to-r2.yml", "")
    if sync.count("--global-object") != 2 * len(GLOBAL_OBJECTS):
        errors.append(
            ".github/workflows/sync-geom-to-r2.yml: fresh stage/apply must each name four globals"
        )
    for key in GLOBAL_OBJECTS:
        if sync.count(f'--global-object "{key}=') != 2:
            errors.append(
                f".github/workflows/sync-geom-to-r2.yml: missing fresh double-census global {key}"
            )
    return errors
