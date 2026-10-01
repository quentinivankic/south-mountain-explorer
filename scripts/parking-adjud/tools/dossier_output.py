#!/usr/bin/env python3
"""Lock-serialized output and generation-manifest handling for dossiers."""
from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

import trust_resolution as tr
import trusted_filesystem as trusted_fs

GENERATION_MANIFEST_VERSION = 1
GENERATION_MANIFEST_KIND = "parking-dossier-generation"
GENERATION_MANIFEST_ORIGINS = frozenset({
    "pipeline-v1",
    "legacy-bootstrap-v1",
})
GENERATION_ARTIFACT_KEYS = ("dossier", "serves", "context", "walk")

_AREA_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_DECIMAL_FID_RE = re.compile(r"(?:0|[1-9][0-9]*)")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_MANIFEST_KEYS = frozenset({
    "version",
    "kind",
    "area",
    "origin",
    "parent_manifest_sha256",
    "artifacts",
})
_ARTIFACT_KEYS = frozenset({"basename", "byte_length", "sha256"})
_SOURCE_GENERATION_KEYS = frozenset({
    "manifest_path", "manifest_sha256", "artifact_sha256",
})


def _canonical_area(area: object) -> str:
    if (not isinstance(area, str)
            or _AREA_RE.fullmatch(area) is None):
        raise ValueError("generation manifest area must be a canonical slug")
    return area


def _canonical_root(tmp: str | Path) -> Path:
    return Path(tmp).expanduser().resolve()


def _artifact_basenames(area: str) -> dict[str, str]:
    return {
        "dossier": f"{area}_dossier.json",
        "serves": f"{area}_serves2.json",
        "context": f"{area}_context.json",
        "walk": f"{area}_walk.json",
    }


def _generation_paths(tmp: str | Path, area: object) -> tuple[
        Path, dict[str, Path]]:
    canonical_area = _canonical_area(area)
    root = _canonical_root(tmp)
    basenames = _artifact_basenames(canonical_area)
    return (
        root / f"{canonical_area}_generation.json",
        {key: root / basenames[key] for key in GENERATION_ARTIFACT_KEYS},
    )


def generation_manifest_path(tmp: str | Path, area: object) -> Path:
    """Return the canonical manifest path for one area."""
    manifest, _artifacts = _generation_paths(tmp, area)
    return manifest


def _require_parent_hash(value: object) -> str | None:
    if value is None:
        return None
    if (not isinstance(value, str)
            or _SHA256_RE.fullmatch(value) is None):
        raise ValueError(
            "parent_manifest_sha256 must be null or a lowercase SHA-256"
        )
    return value


def _require_origin(value: object) -> str:
    if (not isinstance(value, str)
            or value not in GENERATION_MANIFEST_ORIGINS):
        raise ValueError("generation manifest origin is not supported")
    return value


def _require_artifact_bytes(
        values: Mapping[str, bytes]) -> dict[str, bytes]:
    if (not isinstance(values, Mapping)
            or set(values) != set(GENERATION_ARTIFACT_KEYS)):
        raise ValueError("generation artifacts must be the exact quartet")
    result = {}
    for key in GENERATION_ARTIFACT_KEYS:
        raw = values[key]
        if type(raw) is not bytes:
            raise ValueError(f"generation artifact {key} must be exact bytes")
        result[key] = bytes(raw)
    return result


def _require_artifact_hashes(
        values: Mapping[str, str]) -> dict[str, str]:
    if (not isinstance(values, Mapping)
            or set(values) != set(GENERATION_ARTIFACT_KEYS)):
        raise ValueError("expected artifact hashes must be the exact quartet")
    result = {}
    for key in GENERATION_ARTIFACT_KEYS:
        digest = values[key]
        if (not isinstance(digest, str)
                or _SHA256_RE.fullmatch(digest) is None):
            raise ValueError(
                f"expected artifact hash for {key} must be lowercase SHA-256"
            )
        result[key] = digest
    return result


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def captured_generation_document(capture: Mapping, key: str) -> object:
    """Return one mutable JSON copy from a trusted immutable capture."""
    if (not isinstance(capture, Mapping)
            or key not in GENERATION_ARTIFACT_KEYS
            or key not in capture):
        raise ValueError("generation capture document is unavailable")
    return _thaw_json(capture[key])


def validate_source_generation(value: object, area: object) -> dict:
    """Validate one portable persisted generation binding and copy it."""
    canonical_area = _canonical_area(area)
    if not isinstance(value, dict) or set(value) != _SOURCE_GENERATION_KEYS:
        raise ValueError("source_generation does not have the exact schema")
    expected_manifest = f"{canonical_area}_generation.json"
    if value.get("manifest_path") != expected_manifest:
        raise ValueError(
            "source_generation manifest_path must be the canonical basename"
        )
    manifest_sha256 = value.get("manifest_sha256")
    if (not isinstance(manifest_sha256, str)
            or _SHA256_RE.fullmatch(manifest_sha256) is None):
        raise ValueError(
            "source_generation manifest_sha256 must be lowercase SHA-256"
        )
    try:
        artifact_sha256 = _require_artifact_hashes(value.get("artifact_sha256"))
    except (TypeError, ValueError) as error:
        raise ValueError(f"source_generation artifact_sha256 is invalid: {error}") from error
    return {
        "manifest_path": expected_manifest,
        "manifest_sha256": manifest_sha256,
        "artifact_sha256": artifact_sha256,
    }


def portable_source_generation(capture: Mapping, area: object) -> dict:
    """Project a trusted immutable capture to the strict portable boundary."""
    canonical_area = _canonical_area(area)
    if not isinstance(capture, Mapping):
        raise ValueError("generation capture must be a mapping")
    source = capture.get("source_generation", capture)
    if not isinstance(source, Mapping) or set(source) != _SOURCE_GENERATION_KEYS:
        raise ValueError("generation capture source_generation is malformed")
    raw_manifest_path = source.get("manifest_path")
    expected_manifest = f"{canonical_area}_generation.json"
    if (not isinstance(raw_manifest_path, str)
            or Path(raw_manifest_path).name != expected_manifest):
        raise ValueError("generation capture manifest path is noncanonical")
    projected = {
        "manifest_path": expected_manifest,
        "manifest_sha256": source.get("manifest_sha256"),
        "artifact_sha256": dict(source.get("artifact_sha256", {})),
    }
    return validate_source_generation(projected, canonical_area)


def build_generation_manifest(
        area: object, origin: object, parent_manifest_sha256: object,
        artifact_bytes: Mapping[str, bytes]) -> dict:
    """Build a deterministic v1 manifest from an exact artifact quartet."""
    canonical_area = _canonical_area(area)
    canonical_origin = _require_origin(origin)
    parent = _require_parent_hash(parent_manifest_sha256)
    if canonical_origin == "legacy-bootstrap-v1" and parent is not None:
        raise ValueError("legacy bootstrap generation must have a null parent")
    artifacts = _require_artifact_bytes(artifact_bytes)
    basenames = _artifact_basenames(canonical_area)
    document = {
        "version": GENERATION_MANIFEST_VERSION,
        "kind": GENERATION_MANIFEST_KIND,
        "area": canonical_area,
        "origin": canonical_origin,
        "parent_manifest_sha256": parent,
        "artifacts": {
            key: {
                "basename": basenames[key],
                "byte_length": len(artifacts[key]),
                "sha256": hashlib.sha256(artifacts[key]).hexdigest(),
            }
            for key in GENERATION_ARTIFACT_KEYS
        },
    }
    _validate_manifest_document(document, expected_area=canonical_area)
    return document


def _validate_manifest_document(
        value: object, *, expected_area: str | None = None) -> dict:
    if not isinstance(value, dict) or set(value) != _MANIFEST_KEYS:
        raise ValueError("generation manifest does not have the exact v1 schema")
    if (type(value["version"]) is not int
            or value["version"] != GENERATION_MANIFEST_VERSION):
        raise ValueError("generation manifest version must be exactly 1")
    if value["kind"] != GENERATION_MANIFEST_KIND:
        raise ValueError("generation manifest kind is not supported")
    area = _canonical_area(value["area"])
    if expected_area is not None and area != _canonical_area(expected_area):
        raise ValueError("generation manifest area does not match its path")
    origin = _require_origin(value["origin"])
    parent = _require_parent_hash(value["parent_manifest_sha256"])
    if origin == "legacy-bootstrap-v1" and parent is not None:
        raise ValueError("legacy bootstrap generation must have a null parent")

    artifacts = value["artifacts"]
    if (not isinstance(artifacts, dict)
            or set(artifacts) != set(GENERATION_ARTIFACT_KEYS)):
        raise ValueError("generation manifest artifacts must be the exact quartet")
    basenames = _artifact_basenames(area)
    for key in GENERATION_ARTIFACT_KEYS:
        entry = artifacts[key]
        if not isinstance(entry, dict) or set(entry) != _ARTIFACT_KEYS:
            raise ValueError(
                f"generation manifest {key} entry does not have the exact schema"
            )
        if entry["basename"] != basenames[key]:
            raise ValueError(
                f"generation manifest {key} basename is noncanonical"
            )
        length = entry["byte_length"]
        if type(length) is not int or length < 0:
            raise ValueError(
                f"generation manifest {key} byte_length must be nonnegative"
            )
        digest = entry["sha256"]
        if (not isinstance(digest, str)
                or _SHA256_RE.fullmatch(digest) is None):
            raise ValueError(
                f"generation manifest {key} sha256 must be lowercase SHA-256"
            )
    return value


def generation_manifest_json_bytes(document: dict) -> bytes:
    """Serialize the one canonical UTF-8 v1 manifest representation."""
    _validate_manifest_document(document)
    try:
        return tr.canonical_json(document) + b"\n"
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise ValueError(f"generation manifest cannot be canonicalized: {error}") from error


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key {key!r}")
        value[key] = item
    return value


def _reject_nonfinite(value: str):
    raise ValueError(f"nonfinite JSON number {value!r}")


def _require_finite_json(value: object) -> None:
    if (isinstance(value, float)
            and (value != value or value in (float("inf"), float("-inf")))):
        raise ValueError("nonfinite JSON number")
    if isinstance(value, dict):
        for item in value.values():
            _require_finite_json(item)
    elif isinstance(value, list):
        for item in value:
            _require_finite_json(item)


def _strict_json_loads(raw: bytes, context: str) -> object:
    if type(raw) is not bytes:
        raise ValueError(f"{context} must be exact bytes")
    try:
        parsed = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite,
        )
        _require_finite_json(parsed)
        return parsed
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError(f"{context} is not strict JSON: {error}") from error


def parse_generation_manifest(
        raw: bytes, *, expected_area: str | None = None) -> dict:
    """Parse only duplicate-free, finite, exact canonical v1 bytes."""
    parsed = _strict_json_loads(raw, "generation manifest")
    document = _validate_manifest_document(parsed, expected_area=expected_area)
    try:
        canonical = generation_manifest_json_bytes(document)
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise ValueError(f"generation manifest is not canonical: {error}") from error
    if raw != canonical:
        raise ValueError("generation manifest bytes are not canonical")
    return copy.deepcopy(document)


def _read_optional_regular_bytes(path: Path) -> bytes | None:
    try:
        return trusted_fs.read_regular_bytes(path)
    except FileNotFoundError:
        return None


def _read_stable_quartet_under_lock(
        root: Path, area: str) -> tuple[bytes | None, dict[str, bytes]]:
    """Capture exact bytes while the caller holds the inventory lock."""
    manifest_path, artifact_paths = _generation_paths(root, area)
    manifest_before = _read_optional_regular_bytes(manifest_path)
    artifacts = {
        key: trusted_fs.read_regular_bytes(artifact_paths[key])
        for key in GENERATION_ARTIFACT_KEYS
    }
    manifest_after = _read_optional_regular_bytes(manifest_path)
    if manifest_before != manifest_after:
        raise ValueError("generation manifest changed while capturing artifacts")
    return manifest_before, artifacts


def _json_values_equal(left: object, right: object) -> bool:
    try:
        return tr.canonical_json(left) == tr.canonical_json(right)
    except (TypeError, ValueError, UnicodeEncodeError):
        return False


def _parse_and_validate_quartet(
        area: str, artifacts: Mapping[str, bytes]) -> dict[str, object]:
    documents = {
        key: _strict_json_loads(artifacts[key], f"{key} artifact")
        for key in GENERATION_ARTIFACT_KEYS
    }
    dossier = documents["dossier"]
    if not isinstance(dossier, dict):
        raise ValueError("dossier artifact must be an object")
    if dossier.get("slug") != area:
        raise ValueError("dossier slug does not exactly match the generation area")
    facilities = dossier.get("facilities")
    if not isinstance(facilities, list):
        raise ValueError("dossier facilities must be an array")

    fids: set[int] = set()
    facilities_by_fid: dict[int, dict] = {}
    for index, facility in enumerate(facilities):
        if not isinstance(facility, dict):
            raise ValueError(f"dossier facility {index} must be an object")
        fid = facility.get("fid")
        if type(fid) is not int or fid < 0:
            raise ValueError(
                f"dossier facility {index} fid must be a nonnegative integer"
            )
        if fid in fids:
            raise ValueError(f"dossier fid {fid} is duplicated")
        fids.add(fid)
        facilities_by_fid[fid] = facility

    decimal_fids = {str(fid) for fid in fids}
    for key in ("serves", "context", "walk"):
        sidecar = documents[key]
        if not isinstance(sidecar, dict):
            raise ValueError(f"{key} artifact must be an object")
        if any(_DECIMAL_FID_RE.fullmatch(fid) is None for fid in sidecar):
            raise ValueError(f"{key} artifact has a noncanonical decimal fid")
        if set(sidecar) != decimal_fids:
            raise ValueError(
                f"{key} artifact fid set does not exactly match the dossier"
            )

    walk = documents["walk"]
    assert isinstance(walk, dict)
    for fid, facility in facilities_by_fid.items():
        if ("walk" in facility
                and not _json_values_equal(facility["walk"], walk[str(fid)])):
            raise ValueError(
                f"dossier facility {fid} embedded walk does not exactly match "
                "the walk artifact"
            )
    return documents


def _verify_manifest_artifacts(
        manifest: dict, artifacts: Mapping[str, bytes]) -> None:
    for key in GENERATION_ARTIFACT_KEYS:
        raw = artifacts[key]
        entry = manifest["artifacts"][key]
        if len(raw) != entry["byte_length"]:
            raise ValueError(
                f"{key} artifact byte length does not match generation manifest"
            )
        digest = hashlib.sha256(raw).hexdigest()
        if digest != entry["sha256"]:
            raise ValueError(
                f"{key} artifact hash does not match generation manifest"
            )


def _freeze_json(value: object) -> object:
    if isinstance(value, dict):
        return MappingProxyType({
            key: _freeze_json(item) for key, item in value.items()
        })
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _capture_generation_under_lock(root: Path, area: str) -> Mapping:
    manifest_path, _artifact_paths = _generation_paths(root, area)
    manifest_raw, artifacts = _read_stable_quartet_under_lock(root, area)
    if manifest_raw is None:
        raise FileNotFoundError(f"generation manifest is missing: {manifest_path}")
    manifest = parse_generation_manifest(manifest_raw, expected_area=area)
    _verify_manifest_artifacts(manifest, artifacts)
    documents = _parse_and_validate_quartet(area, artifacts)
    artifact_sha256 = MappingProxyType({
        key: hashlib.sha256(artifacts[key]).hexdigest()
        for key in GENERATION_ARTIFACT_KEYS
    })
    source_generation = MappingProxyType({
        "manifest_path": str(manifest_path),
        "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
        "artifact_sha256": artifact_sha256,
    })
    return MappingProxyType({
        key: _freeze_json(documents[key])
        for key in GENERATION_ARTIFACT_KEYS
    } | {"source_generation": source_generation})


def capture_generation_under_lock(
        tmp: str | Path, area: object) -> Mapping:
    """Capture one generation; the caller must hold its inventory lock."""
    canonical_area = _canonical_area(area)
    return _capture_generation_under_lock(
        _canonical_root(tmp), canonical_area
    )


# A concise alias for callers whose lease is already established.
capture_generation_locked = capture_generation_under_lock


def capture_generation(tmp: str | Path, area: object) -> Mapping:
    """Capture and verify one immutable generation under a shared lock."""
    canonical_area = _canonical_area(area)
    root = _canonical_root(tmp)
    resource = tr.dossier_resource_path(root)
    with trusted_fs.locked_resources(
            root, [(resource, fcntl.LOCK_SH)], tr.resource_lock_path):
        return _capture_generation_under_lock(root, canonical_area)


def verify_generation(tmp: str | Path, area: object) -> Mapping:
    """Verify and return one immutable current generation."""
    return capture_generation(tmp, area)


def _validate_expected_artifact_hashes(
        artifacts: Mapping[str, bytes],
        expected_artifact_sha256: Mapping[str, str] | None) -> None:
    if expected_artifact_sha256 is None:
        return
    expected = _require_artifact_hashes(expected_artifact_sha256)
    actual = {
        key: hashlib.sha256(artifacts[key]).hexdigest()
        for key in GENERATION_ARTIFACT_KEYS
    }
    if actual != expected:
        raise ValueError("captured artifact hashes do not match expected hashes")


def _revalidate_before_install(
        root: Path, area: str, manifest_before: bytes | None,
        artifacts_before: Mapping[str, bytes]) -> None:
    manifest_after, artifacts_after = _read_stable_quartet_under_lock(root, area)
    if manifest_after != manifest_before:
        raise ValueError("generation parent changed before manifest installation")
    if dict(artifacts_after) != dict(artifacts_before):
        raise ValueError("generation artifact quartet changed before manifest installation")


def commit_generation(
        tmp: str | Path, area: object,
        parent_manifest_sha256: object,
        *, expected_artifact_sha256: Mapping[str, str] | None = None) -> Mapping:
    """Commit a pipeline manifest with parent-hash compare-and-swap."""
    canonical_area = _canonical_area(area)
    parent = _require_parent_hash(parent_manifest_sha256)
    root = _canonical_root(tmp)
    manifest_path, _artifact_paths = _generation_paths(root, canonical_area)
    resource = tr.dossier_resource_path(root)
    with trusted_fs.locked_resources(
            root, [(resource, fcntl.LOCK_EX)], tr.resource_lock_path):
        manifest_before, artifacts = _read_stable_quartet_under_lock(
            root, canonical_area
        )
        if manifest_before is not None:
            parse_generation_manifest(
                manifest_before, expected_area=canonical_area
            )
        _validate_expected_artifact_hashes(
            artifacts, expected_artifact_sha256
        )
        _parse_and_validate_quartet(canonical_area, artifacts)
        candidate = generation_manifest_json_bytes(build_generation_manifest(
            canonical_area, "pipeline-v1", parent, artifacts
        ))

        if manifest_before == candidate:
            return _capture_generation_under_lock(root, canonical_area)

        current_sha256 = (
            None if manifest_before is None
            else hashlib.sha256(manifest_before).hexdigest()
        )
        if current_sha256 != parent:
            raise ValueError(
                "stale parent_manifest_sha256; generation manifest CAS failed"
            )

        _revalidate_before_install(
            root, canonical_area, manifest_before, artifacts
        )
        trusted_fs.atomic_write_bytes(manifest_path, candidate)
        return _capture_generation_under_lock(root, canonical_area)


def bootstrap_generation(tmp: str | Path, area: object) -> Mapping:
    """Bind an existing quartet without rewriting any artifact bytes."""
    canonical_area = _canonical_area(area)
    root = _canonical_root(tmp)
    manifest_path, _artifact_paths = _generation_paths(root, canonical_area)
    resource = tr.dossier_resource_path(root)
    with trusted_fs.locked_resources(
            root, [(resource, fcntl.LOCK_EX)], tr.resource_lock_path):
        manifest_before, artifacts = _read_stable_quartet_under_lock(
            root, canonical_area
        )
        if manifest_before is not None:
            parse_generation_manifest(
                manifest_before, expected_area=canonical_area
            )
        _parse_and_validate_quartet(canonical_area, artifacts)
        candidate = generation_manifest_json_bytes(build_generation_manifest(
            canonical_area, "legacy-bootstrap-v1", None, artifacts
        ))

        if manifest_before == candidate:
            return _capture_generation_under_lock(root, canonical_area)
        if manifest_before is not None:
            raise ValueError(
                "existing generation manifest differs from bootstrap image"
            )

        _revalidate_before_install(root, canonical_area, None, artifacts)
        trusted_fs.atomic_write_bytes(manifest_path, candidate)
        return _capture_generation_under_lock(root, canonical_area)


# Descriptive aliases for callers that name the persisted object explicitly.
commit_generation_manifest = commit_generation
bootstrap_generation_manifest = bootstrap_generation
verify_generation_manifest = verify_generation


def canonical_area(area: object) -> str:
    """Return one validated canonical area slug."""
    return _canonical_area(area)


def strict_json_loads(raw: bytes, context: str) -> object:
    """Parse duplicate-free finite UTF-8 JSON from exact bytes."""
    return _strict_json_loads(raw, context)


def read_strict_regular_json(
        path: str | Path, context: str) -> tuple[object, bytes]:
    """Read one stable no-follow regular file and strictly parse its JSON."""
    raw = trusted_fs.read_regular_bytes(path)
    return _strict_json_loads(raw, context), raw


def _producer_dossier_fids(area: str, dossier: object) -> dict[int, dict]:
    if not isinstance(dossier, dict):
        raise ValueError("producer dossier must be an object")
    if dossier.get("slug") != area:
        raise ValueError("producer dossier slug does not match its path")
    facilities = dossier.get("facilities")
    if not isinstance(facilities, list):
        raise ValueError("producer dossier facilities must be an array")
    result = {}
    for index, facility in enumerate(facilities):
        if not isinstance(facility, dict):
            raise ValueError(f"producer dossier facility {index} must be an object")
        fid = facility.get("fid")
        if type(fid) is not int or fid < 0:
            raise ValueError(
                f"producer dossier facility {index} fid must be a nonnegative integer"
            )
        if fid in result:
            raise ValueError(f"producer dossier fid {fid} is duplicated")
        result[fid] = facility
    return result


def _producer_json_bytes(value: object, *, indent: int | None = None) -> bytes:
    try:
        return json.dumps(
            value, indent=indent, ensure_ascii=True, allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise ValueError(f"producer output is not finite JSON: {error}") from error


def capture_producer_dossier(
        tmp: str | Path, area: object) -> tuple[dict, bytes, str]:
    """Capture strict dossier bytes under the exclusive inventory lock."""
    canonical_area = _canonical_area(area)
    root = _canonical_root(tmp)
    dossier_path = root / f"{canonical_area}_dossier.json"
    resource = tr.dossier_resource_path(root)
    with trusted_fs.locked_resources(
            root, [(resource, fcntl.LOCK_EX)], tr.resource_lock_path):
        dossier, raw = read_strict_regular_json(
            dossier_path, f"{canonical_area} dossier"
        )
        _producer_dossier_fids(canonical_area, dossier)
    assert isinstance(dossier, dict)
    return copy.deepcopy(dossier), raw, hashlib.sha256(raw).hexdigest()


def _require_unchanged_producer_dossier(
        path: Path, expected_bytes: bytes, expected_sha256: str) -> None:
    if type(expected_bytes) is not bytes:
        raise ValueError("captured dossier must be exact bytes")
    if (not isinstance(expected_sha256, str)
            or _SHA256_RE.fullmatch(expected_sha256) is None
            or hashlib.sha256(expected_bytes).hexdigest() != expected_sha256):
        raise ValueError("captured dossier hash is malformed or mismatched")
    current = trusted_fs.read_regular_bytes(path)
    current_sha256 = hashlib.sha256(current).hexdigest()
    if current != expected_bytes or current_sha256 != expected_sha256:
        raise ValueError("input dossier changed before producer output installation")


def _validate_producer_sidecar_fids(
        name: str, value: object, dossier_by_fid: Mapping[int, dict]) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"producer {name} output must be an object")
    if any(not isinstance(fid, str) or _DECIMAL_FID_RE.fullmatch(fid) is None
           for fid in value):
        raise ValueError(f"producer {name} output has a noncanonical decimal fid")
    expected = {str(fid) for fid in dossier_by_fid}
    if set(value) != expected:
        raise ValueError(
            f"producer {name} fid set does not exactly match the dossier"
        )
    return value


def write_walk_outputs(
        tmp: str | Path, area: object, *, source_dossier_bytes: bytes,
        source_dossier_sha256: str, dossier: dict, walk: dict) -> None:
    """Install walk then dossier iff the exact captured dossier is current."""
    canonical_area = _canonical_area(area)
    root = _canonical_root(tmp)
    dossier_by_fid = _producer_dossier_fids(canonical_area, dossier)
    walk = _validate_producer_sidecar_fids("walk", walk, dossier_by_fid)
    for fid, facility in dossier_by_fid.items():
        if ("walk" not in facility
                or not _json_values_equal(facility["walk"], walk[str(fid)])):
            raise ValueError(
                f"producer dossier facility {fid} walk does not match walk output"
            )
    walk_bytes = _producer_json_bytes(walk)
    dossier_bytes = _producer_json_bytes(dossier)
    dossier_path = root / f"{canonical_area}_dossier.json"
    walk_path = root / f"{canonical_area}_walk.json"
    resource = tr.dossier_resource_path(root)
    with trusted_fs.locked_resources(
            root, [(resource, fcntl.LOCK_EX)], tr.resource_lock_path):
        _require_unchanged_producer_dossier(
            dossier_path, source_dossier_bytes, source_dossier_sha256
        )
        trusted_fs.atomic_write_bytes(walk_path, walk_bytes)
        trusted_fs.atomic_write_bytes(dossier_path, dossier_bytes)


def write_context_output(
        tmp: str | Path, area: object, *, source_dossier_bytes: bytes,
        source_dossier_sha256: str, dossier: dict, context: dict) -> None:
    """Install context iff the exact captured dossier is still current."""
    canonical_area = _canonical_area(area)
    root = _canonical_root(tmp)
    dossier_by_fid = _producer_dossier_fids(canonical_area, dossier)
    context = _validate_producer_sidecar_fids(
        "context", context, dossier_by_fid
    )
    context_bytes = _producer_json_bytes(context, indent=0)
    dossier_path = root / f"{canonical_area}_dossier.json"
    context_path = root / f"{canonical_area}_context.json"
    resource = tr.dossier_resource_path(root)
    with trusted_fs.locked_resources(
            root, [(resource, fcntl.LOCK_EX)], tr.resource_lock_path):
        _require_unchanged_producer_dossier(
            dossier_path, source_dossier_bytes, source_dossier_sha256
        )
        trusted_fs.atomic_write_bytes(context_path, context_bytes)


def write_generated_outputs(tmp: str | Path, slug: str,
                            dossier: dict, serves: dict) -> None:
    """Install one dossier/serves generation under its inventory lock."""
    canonical_area = _canonical_area(slug)
    root = _canonical_root(tmp)
    _producer_dossier_fids(canonical_area, dossier)
    if not isinstance(serves, dict):
        raise ValueError("producer serves output must be an object")
    dossier_bytes = _producer_json_bytes(dossier)
    serves_bytes = _producer_json_bytes(serves, indent=0)
    dossier_path = root / f"{canonical_area}_dossier.json"
    serves_path = root / f"{canonical_area}_serves2.json"
    resource = tr.dossier_resource_path(root)
    with trusted_fs.locked_resources(
            root, [(resource, fcntl.LOCK_EX)], tr.resource_lock_path):
        trusted_fs.atomic_write_bytes(dossier_path, dossier_bytes)
        trusted_fs.atomic_write_bytes(serves_path, serves_bytes)


def current_parent_manifest_sha256(
        tmp: str | Path, area: object) -> str | None:
    """Verify the current generation and return its manifest hash, or null."""
    canonical_area = _canonical_area(area)
    root = _canonical_root(tmp)
    manifest_path, _artifact_paths = _generation_paths(root, canonical_area)
    resource = tr.dossier_resource_path(root)
    with trusted_fs.locked_resources(
            root, [(resource, fcntl.LOCK_SH)], tr.resource_lock_path):
        if _read_optional_regular_bytes(manifest_path) is None:
            return None
        captured = _capture_generation_under_lock(root, canonical_area)
        return captured["source_generation"]["manifest_sha256"]


def _padj_tmp() -> Path:
    configured = os.environ.get("PADJ_TMP")
    if configured:
        return _canonical_root(configured)
    return (Path(__file__).resolve().parent.parent / "work").resolve()


def _source_generation_json(capture: Mapping) -> str:
    dossier = capture.get("dossier")
    area = dossier.get("slug") if isinstance(dossier, Mapping) else None
    source = portable_source_generation(capture, area)
    return json.dumps(
        source, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _parent_argument(value: str | None) -> str | None:
    if value in (None, "null"):
        return None
    return _require_parent_hash(value)


def main(argv: list[str] | None = None) -> int:
    """Run manifest parent capture, commit, bootstrap, or verification."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    bootstrap = commands.add_parser(
        "bootstrap", aliases=["bootstrap-generation"],
        help="create deterministic legacy manifests for existing quartets",
    )
    bootstrap.add_argument("area", nargs="+")

    verify = commands.add_parser(
        "verify", aliases=["verify-generation"],
        help="verify existing generation manifests and quartets",
    )
    verify.add_argument("area", nargs="+")

    parent = commands.add_parser(
        "parent-manifest-sha256", aliases=["current-parent"],
        help="verify and print the current manifest hash, or null",
    )
    parent.add_argument("area")

    commit = commands.add_parser(
        "commit", aliases=["commit-generation"],
        help="commit a pipeline manifest with parent-hash CAS",
    )
    commit.add_argument("area")
    commit.add_argument(
        "--parent-manifest-sha256", default=None,
        help="expected current manifest hash, or omit/use 'null' for none",
    )

    args = parser.parse_args(argv)
    root = _padj_tmp()
    captures = []
    if args.command in ("bootstrap", "bootstrap-generation"):
        captures = [bootstrap_generation(root, area) for area in args.area]
    elif args.command in ("verify", "verify-generation"):
        captures = [verify_generation(root, area) for area in args.area]
    elif args.command in ("parent-manifest-sha256", "current-parent"):
        value = current_parent_manifest_sha256(root, args.area)
        print("null" if value is None else value)
        return 0
    else:
        captures = [commit_generation(
            root, args.area, _parent_argument(args.parent_manifest_sha256)
        )]
    for capture in captures:
        print(_source_generation_json(capture))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
