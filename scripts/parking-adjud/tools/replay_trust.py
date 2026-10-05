#!/usr/bin/env python3
"""Read-only replay of Trekdex parking verdicts through the shadow trust policy.

The command reads authoritative stores, the generated sidecar, calibration
ledger, and historical label file.  It never edits drafts, stores, sidecars,
geom, pools, workflows, or live data.  JSON goes to stdout by default.  An
explicit report path is allowed only below the dedicated ignored
`scripts/parking-adjud/reports/` tree.
"""
from __future__ import annotations

import argparse
import base64
import copy
import fcntl
import hashlib
import importlib.util
import json
import os
import stat
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parents[2]
SCRIPTS = ROOT / "scripts"
PARKING_ADJUD = SCRIPTS / "parking-adjud"
DATA = PARKING_ADJUD / "data"
WORK = PARKING_ADJUD / "work"
REPORTS = PARKING_ADJUD / "reports"
PUBLIC = ROOT / "public"
SIDECAR = PUBLIC / "areas" / "parking-verdicts.json"

for path in (str(TOOLS), str(SCRIPTS)):
    if path not in sys.path:
        sys.path.insert(0, path)

import trust_engine  # noqa: E402
import trust_resolution as tr  # noqa: E402
import trusted_filesystem as trusted_fs  # noqa: E402
import dossier_output  # noqa: E402
import review_evidence as review  # noqa: E402
import publication_objects as publication_objects  # noqa: E402
from judge_validation import (  # noqa: E402
    AUTHORITY_RECEIPT_VERSION,
    PATH_BOUND_AUTHORITY_RECEIPT_VERSION,
    LEGACY_AUTHORITY_RECEIPT_VERSION,
    LEGACY_PUBLICATION_ATTESTATION_VERSION,
    PATH_BOUND_PUBLICATION_ATTESTATION_VERSION,
    PUBLICATION_ATTESTATION_VERSION,
    authority_wrapper,
    authority_receipt_path,
    canonical_draft_files,
    current_authority,
    load_canonical_drafts,
    machine_decision_projection,
    validate_authority_receipt,
    validate_publication_attestation,
    validate_verdict_row,
)


def _load_builder():
    path = SCRIPTS / "build-parking-verdicts.py"
    spec = importlib.util.spec_from_file_location("parking_verdict_builder_shadow", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_json(path: Path, expected: type) -> object:
    value = json.loads(path.read_text())
    if not isinstance(value, expected):
        raise ValueError(f"{path} must contain a {expected.__name__}")
    return value


LEGACY_PUBLICATION_PROOF_REGISTRY_VERSION = 1
PUBLICATION_PROOF_REGISTRY_VERSION = 2
PUBLICATION_PROOF_REGISTRY_KIND = "parking-publication-proof-registry"
LEGACY_PUBLICATION_PROOF_VERSION = 1
REVIEW_PUBLICATION_PROOF_VERSION = 2
PATH_BOUND_PUBLICATION_PROOF_VERSION = 3
PUBLICATION_PROOF_VERSION = 4
PUBLICATION_PROOF_KIND = "parking-publication-proof"
PUBLICATION_PROOF_MANIFEST_MAX_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class _ProofReplaySummary:
    """Compact immutable state retained after one proof-v4 assignment replay."""

    state: str
    result: str | None
    selected_role: str | None
    reason: str
    model_families: tuple[str, ...]
    selected_decision_sha256: str | None


def _compact_terminal_value(item: dict, summary: _ProofReplaySummary) -> dict:
    value = {
        "item": item,
        "resolution": {
            "state": summary.state,
            "selected_role": summary.selected_role,
        },
    }
    if summary.selected_role in tr.ROLES:
        value[summary.selected_role] = {
            "decision_sha256": summary.selected_decision_sha256,
        }
    return value


def _update_pretty_object_digest(
        digest, member_count: int, key: str, value: object, serializer) -> int:
    """Hash one sorted member using the resolver's exact pretty-JSON framing."""
    encoded = serializer({key: value})
    if not encoded.startswith(b"{\n") or not encoded.endswith(b"\n}\n"):
        raise ValueError("packet source member encoding is noncanonical")
    digest.update(b"{\n" if member_count == 0 else b",\n")
    digest.update(encoded[2:-3])
    return member_count + 1


def _finish_pretty_object_digest(digest, member_count: int, serializer) -> str:
    if member_count:
        digest.update(b"\n}\n")
    else:
        digest.update(serializer({}))
    return digest.hexdigest()


def _publication_proof_compatibility(version: object) -> dict | None:
    """Return the closed historical proof/prepare/receipt version matrix."""
    if type(version) is not int:
        return None
    compatibility = {
        LEGACY_PUBLICATION_PROOF_VERSION: {
            "prepare_version": 2,
            "human_receipt_version": LEGACY_AUTHORITY_RECEIPT_VERSION,
            "attestation_version": LEGACY_PUBLICATION_ATTESTATION_VERSION,
            "human_reviews": False,
        },
        REVIEW_PUBLICATION_PROOF_VERSION: {
            "prepare_version": 2,
            "human_receipt_version": PATH_BOUND_AUTHORITY_RECEIPT_VERSION,
            "attestation_version": PATH_BOUND_PUBLICATION_ATTESTATION_VERSION,
            "human_reviews": True,
        },
        PATH_BOUND_PUBLICATION_PROOF_VERSION: {
            "prepare_version": 3,
            "human_receipt_version": PATH_BOUND_AUTHORITY_RECEIPT_VERSION,
            "attestation_version": PATH_BOUND_PUBLICATION_ATTESTATION_VERSION,
            "human_reviews": True,
        },
    }
    value = compatibility.get(version)
    return copy.deepcopy(value) if value is not None else None


def _parse_embedded_proof_prepare(
        raw: bytes, resolver, expected_version: int) -> dict:
    """Parse one embedded prepare at the exact version authorized by its proof."""
    if expected_version not in (
            resolver.LEGACY_PREPARE_VERSION,
            resolver.PATH_BOUND_PREPARE_VERSION,
            resolver.PREPARE_VERSION):
        raise ValueError("publication proof prepare version is unsupported")
    prepare = resolver.parse_prepare_bytes(
        raw,
        allow_legacy=(expected_version != resolver.PREPARE_VERSION),
    )
    if prepare.get("version") != expected_version:
        raise ValueError(
            "embedded publication proof prepare version does not match outer proof"
        )
    return prepare


def _publication_proof_path(data_dir: Path, store_name: str) -> Path:
    import _parking_verdict_source as source  # dependency leaf

    _store, proof, _floor = source.publication_artifact_filenames(store_name)
    return Path(data_dir) / proof


def _publication_floor_path(data_dir: Path, store_name: str) -> Path:
    import _parking_verdict_source as source  # dependency leaf

    _store, _proof, floor = source.publication_artifact_filenames(store_name)
    return Path(data_dir) / floor


def _empty_publication_registry() -> dict:
    return {
        "version": PUBLICATION_PROOF_REGISTRY_VERSION,
        "kind": PUBLICATION_PROOF_REGISTRY_KIND,
        "proofs": {},
    }


def _read_publication_registry_bytes(path: Path) -> bytes | None:
    try:
        return publication_objects.read_bounded_regular(
            path, publication_objects.REGISTRY_MAX_BYTES,
            "publication proof registry",
        )
    except FileNotFoundError:
        return None
    except ValueError as error:
        if "exceeds" in str(error):
            raise ValueError(
                f"{path} {publication_objects.registry_cap_message()}"
            ) from error
        raise


def _load_publication_proofs(data_dir: Path, store_name: str,
                             source_bytes: bytes | None = None) -> dict:
    path = _publication_proof_path(data_dir, store_name)
    if source_bytes is None:
        source_bytes = _read_publication_registry_bytes(path)
        if source_bytes is None:
            return publication_objects.PublicationProofRegistry(
                _empty_publication_registry(), data_dir
            )
    elif len(source_bytes) > publication_objects.REGISTRY_MAX_BYTES:
        raise ValueError(
            f"{path} {publication_objects.registry_cap_message()}"
        )
    value = json.loads(source_bytes)
    if not isinstance(value, dict) or type(value.get("version")) is not int:
        raise ValueError(f"{path} is not a publication proof registry")
    if value["version"] == LEGACY_PUBLICATION_PROOF_REGISTRY_VERSION:
        if set(value) != {"version", "proofs"} or not isinstance(value.get("proofs"), dict):
            raise ValueError(f"{path} is not a legacy publication proof registry")
        for proof_sha, proof in value["proofs"].items():
            if not isinstance(proof_sha, str) or not isinstance(proof, dict):
                raise ValueError(f"{path} has a malformed legacy publication proof")
            body = dict(proof)
            claimed = body.pop("proof_sha256", None)
            if claimed != proof_sha or tr.sha256_json(body) != proof_sha:
                raise ValueError(f"{path} has a legacy publication proof hash mismatch")
    elif value["version"] == PUBLICATION_PROOF_REGISTRY_VERSION:
        if (set(value) != {"version", "kind", "proofs"}
                or value.get("kind") != PUBLICATION_PROOF_REGISTRY_KIND
                or not isinstance(value.get("proofs"), dict)):
            raise ValueError(f"{path} is not a publication proof registry v2")
        for proof_sha, descriptor in value["proofs"].items():
            ref = publication_objects.validate_object_ref(descriptor)
            if ref.sha256 != proof_sha:
                raise ValueError(f"{path} manifest key/hash mismatch")
    else:
        raise ValueError(f"{path} publication proof registry version is unsupported")
    return publication_objects.PublicationProofRegistry(value, data_dir)


def _proof_decision_row(row: dict) -> dict:
    value = dict(row)
    for field in (
        "src", "judged", "lat", "lon", "rings", "name", "_key", "reason",
        "publication_attestation",
    ):
        value.pop(field, None)
    return value


def _reconstruct_proof_envelope_bytes(
        prepare: dict, item: dict, role: str,
        raw_bytes: bytes | None, packet_identity: dict,
) -> tuple[dict | None, list[str]]:
    assignment = (item.get("assignments") or {}).get(role)
    if not isinstance(assignment, dict):
        return None, [f"missing {role} assignment"]
    if raw_bytes is None:
        return None, []
    try:
        raw = json.loads(raw_bytes)
        if (not isinstance(raw, dict)
                or set(raw) != {"assignment_id", "decision", "external_evidence"}
                or raw.get("assignment_id") != assignment.get("assignment_id")):
            raise ValueError("sealed output schema/assignment mismatch")
        external = []
        for evidence in raw.get("external_evidence") or []:
            if (not isinstance(evidence, dict)
                    or set(evidence) != {"id", "supports"}
                    or evidence.get("id") not in prepare["external_evidence_catalog"]):
                raise ValueError("sealed output external evidence is unassigned")
            assigned = prepare["external_evidence_catalog"][evidence["id"]]
            external.append({
                key: copy.deepcopy(assigned[key])
                for key in (
                    "id", "sha256", "source_locator", "retrieved_at",
                    "source_updated_at", "frozen_path", "manifest_sha256",
                    "manifest_frozen_path", "metadata_sha256",
                    "metadata_frozen_path",
                )
            } | {"supports": copy.deepcopy(evidence["supports"])})
        envelope = tr.make_envelope(role, assignment, raw["decision"], external)

        def validate_plain(decision: dict) -> list[str]:
            return validate_verdict_row(
                decision, packet_identity, allow_override=False
            )[0]

        errors = tr.validate_envelope(
            envelope, role, packet_identity, validate_plain,
            expected_assignment=assignment,
            expected_packet_sha256=item.get("packet_sha256"),
            expected_version=tr.VERSION,
        )
        return envelope, errors
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        return None, [f"{role} sealed output invalid: {error}"]


def _reconstruct_proof_envelope(
        prepare: dict, item: dict, role: str,
        sealed_outputs: dict, packet_identity: dict,
) -> tuple[dict | None, list[str]]:
    assignment = (item.get("assignments") or {}).get(role)
    if not isinstance(assignment, dict):
        return None, [f"missing {role} assignment"]
    encoded = sealed_outputs.get(assignment.get("output_path"))
    if encoded is None:
        return _reconstruct_proof_envelope_bytes(
            prepare, item, role, None, packet_identity
        )
    try:
        raw_bytes = base64.b64decode(encoded, validate=True)
    except (TypeError, ValueError) as error:
        return None, [f"{role} sealed output invalid: {error}"]
    return _reconstruct_proof_envelope_bytes(
        prepare, item, role, raw_bytes, packet_identity
    )


def _validate_review_bundle(bundle: object, authority_receipt: dict,
                            wrapper: dict, row: dict, resolver, *,
                            expected_prepare_version: int) -> list[str]:
    """Replay exact review receipt, sheet, and frozen source bytes."""
    required = {
        "receipt_b64", "sheet_b64", "source_prepare_b64",
        "source_artifacts_b64",
    }
    if not isinstance(bundle, dict) or set(bundle) != required:
        return ["publication proof review bundle schema mismatch"]
    errors: list[str] = []
    try:
        receipt_bytes = base64.b64decode(bundle["receipt_b64"], validate=True)
        sheet_bytes = base64.b64decode(bundle["sheet_b64"], validate=True)
        prepare_bytes = base64.b64decode(
            bundle["source_prepare_b64"], validate=True
        )
        receipt = json.loads(receipt_bytes)
        prepare = _parse_embedded_proof_prepare(
            prepare_bytes, resolver, expected_prepare_version
        )
        artifacts_encoded = bundle["source_artifacts_b64"]
        if not isinstance(artifacts_encoded, dict):
            raise ValueError("source artifact map is not an object")
        source_artifacts = {
            relative: base64.b64decode(encoded, validate=True)
            for relative, encoded in artifacts_encoded.items()
            if isinstance(relative, str)
        }
        if len(source_artifacts) != len(artifacts_encoded):
            raise ValueError("source artifact map has a non-string path")
        receipt_errors = review.validate_review_receipt(receipt)
        if receipt_errors:
            raise ValueError(f"review receipt invalid: {receipt_errors}")
        if review.json_bytes(receipt) != receipt_bytes:
            raise ValueError("review receipt bytes are noncanonical")
        run_path = Path(prepare["run_root"]) / prepare["run_id"]
        paths = review.artifact_paths(
            prepare["tmp"], prepare["area"], prepare["run_id"]
        )
        consumed = []

        def read(relative: str) -> bytes:
            consumed.append(relative)
            if relative not in source_artifacts:
                raise ValueError(f"missing review source artifact {relative}")
            return source_artifacts[relative]

        expected_sheet, expected_receipt, expected_consumed = (
            review.build_review_artifacts(
                prepare, prepare_bytes, run_path, read,
                receipt["sample"]["requested"], paths["sheet"],
            )
        )
        if receipt != expected_receipt:
            raise ValueError("review receipt differs from reconstructed frozen run")
        if sheet_bytes != expected_sheet:
            raise ValueError("review sheet differs from reconstructed frozen run")
        if set(source_artifacts) != set(expected_consumed) or set(consumed) != set(expected_consumed):
            raise ValueError("review source artifact set is missing or has extras")
        selected = review.review_item(receipt, row["fid"])
        expected_bindings = {
            "review_receipt_sha256": receipt["receipt_sha256"],
            "review_receipt_path": str(paths["receipt"]),
            "review_sheet_sha256": receipt["sheet_sha256"],
            "review_sheet_path": str(paths["sheet"]),
            "review_item_sha256": selected["review_item_sha256"],
        }
        for field, expected in expected_bindings.items():
            if authority_receipt.get(field) != expected:
                errors.append(f"publication proof authority {field} mismatch")
        for field in (
            "review_receipt_sha256", "review_sheet_sha256",
            "review_item_sha256",
        ):
            if wrapper.get(field) != expected_bindings[field]:
                errors.append(f"publication proof wrapper {field} mismatch")
        if (authority_receipt.get("source_run_id") != prepare["run_id"]
                or authority_receipt.get("source_run_path") != str(run_path)
                or authority_receipt.get("source_prepare_sha256")
                != hashlib.sha256(prepare_bytes).hexdigest()
                or selected.get("packet_sha256")
                != authority_receipt.get("packet_sha256")
                or selected.get("primary_envelope_sha256")
                != authority_receipt.get("primary_envelope_sha256")):
            errors.append("publication proof review source authority mismatch")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        errors.append(f"publication proof review evidence invalid: {error}")
    return errors


def _load_manifest_object(
        manifest: dict, registry: dict, object_sha: str, *,
        max_bytes: int = 4 * 1024 * 1024) -> bytes:
    table = manifest.get("objects")
    descriptor = table.get(object_sha) if isinstance(table, dict) else None
    ref = publication_objects.validate_logical_object_ref(descriptor)
    if ref.sha256 != object_sha or ref.length > max_bytes:
        raise ValueError("proof object reference is missing, mismatched, or oversized")
    root = getattr(registry, "data_root", None)
    if root is None:
        raise ValueError("compact proof registry has no object-root context")
    stages = getattr(registry, "staged_objects", ())
    return b"".join(publication_objects.iter_object_bytes(
        root, ref, stages
    ))


def _load_proof_manifest(proof_sha: str, registry: dict) -> dict:
    descriptor = registry.get("proofs", {}).get(proof_sha)
    ref = publication_objects.validate_object_ref(descriptor)
    if ref.sha256 != proof_sha or ref.length > PUBLICATION_PROOF_MANIFEST_MAX_BYTES:
        raise ValueError("publication proof manifest descriptor is invalid")
    root = getattr(registry, "data_root", None)
    if root is None:
        raise ValueError("compact proof registry has no data-root context")
    stages = getattr(registry, "staged_objects", ())
    raw = b"".join(publication_objects.iter_object_bytes(root, ref, stages))
    try:
        import resolve_trust as resolver  # lazy: resolver imports replay
        import _parking_verdict_source as source
        manifest = source._strict_json_loads(raw, "publication proof manifest")
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError(f"publication proof manifest is invalid: {error}") from error
    if not isinstance(manifest, dict) or resolver._json_bytes(manifest) != raw:
        raise ValueError("publication proof manifest bytes are noncanonical")
    return manifest


def _validate_review_recipe_v2(
        review_sha: str, entry: object, manifest: dict,
        registry: dict, resolver) -> list[str]:
    required_entry = {
        "receipt_ref", "source_prepare_ref", "source_artifacts", "recipe",
    }
    required_recipe = {
        "version", "format_version", "renderer_version", "prepare_ref",
        "receipt_ref", "source_artifacts", "sheet_name",
        "expected_sheet_length", "expected_sheet_sha256",
    }
    if not isinstance(entry, dict) or set(entry) != required_entry:
        return ["publication proof review recipe entry schema mismatch"]
    recipe = entry.get("recipe")
    if (not isinstance(recipe, dict) or set(recipe) != required_recipe
            or recipe.get("version") != 2
            or recipe.get("format_version") != review.REVIEW_FORMAT_VERSION
            or recipe.get("renderer_version") != review.REVIEW_RENDERER_VERSION
            or recipe.get("prepare_ref") != entry.get("source_prepare_ref")
            or recipe.get("receipt_ref") != entry.get("receipt_ref")
            or recipe.get("source_artifacts") != entry.get("source_artifacts")
            or recipe.get("sheet_name") != review.REVIEW_SHEET_NAME):
        return ["publication proof review recipe schema mismatch"]
    errors = []
    try:
        prepare_bytes = _load_manifest_object(
            manifest, registry, entry["source_prepare_ref"]
        )
        prepare = resolver.parse_prepare_bytes(prepare_bytes)
        receipt_bytes = _load_manifest_object(
            manifest, registry, entry["receipt_ref"]
        )
        receipt = json.loads(receipt_bytes)
        receipt_errors = review.validate_review_receipt(receipt)
        if receipt_errors or review.json_bytes(receipt) != receipt_bytes:
            raise ValueError(f"review receipt invalid: {receipt_errors}")
        if (receipt.get("receipt_sha256") != review_sha
                or receipt.get("source_prepare_sha256")
                != hashlib.sha256(prepare_bytes).hexdigest()
                or receipt.get("sheet_length")
                != recipe.get("expected_sheet_length")
                or receipt.get("sheet_sha256")
                != recipe.get("expected_sheet_sha256")):
            raise ValueError("review receipt/recipe identity mismatch")
        source_map = entry.get("source_artifacts")
        if (not isinstance(source_map, dict)
                or set(source_map) != set(receipt.get("source_objects") or {})):
            raise ValueError("review recipe source closure mismatch")
        for relative, object_sha in source_map.items():
            descriptor = publication_objects.validate_logical_object_ref(
                manifest["objects"].get(object_sha)
            )
            expected = receipt["source_objects"][relative]
            if (descriptor.sha256 != expected["sha256"]
                    or descriptor.length != expected["length"]):
                raise ValueError(
                    f"review source descriptor mismatch for {relative}"
                )

        def read(relative: str) -> bytes:
            object_sha = source_map.get(relative)
            if object_sha is None:
                raise ValueError(f"missing review source {relative}")
            return _load_manifest_object(manifest, registry, object_sha)

        def iterate(relative: str):
            object_sha = source_map.get(relative)
            if object_sha is None:
                raise ValueError(f"missing review source {relative}")
            descriptor = manifest["objects"][object_sha]
            return publication_objects.iter_object_bytes(
                registry.data_root, descriptor,
                getattr(registry, "staged_objects", ()),
            )

        portable_run = Path("/portable-review") / prepare["run_id"]
        render, rebuilt_receipt, consumed = review.build_review_artifacts_v2(
            prepare, prepare_bytes, portable_run, read, iterate,
            receipt["sample"]["requested"],
        )
        if rebuilt_receipt != receipt or set(consumed) != set(source_map):
            raise ValueError("review recipe does not reconstruct its receipt")
        identity = review.review_html_identity_v2(render())
        if identity != (
                recipe["expected_sheet_length"],
                recipe["expected_sheet_sha256"]):
            raise ValueError("review recipe reconstructed sheet identity mismatch")
        if recipe["expected_sheet_sha256"] in manifest["objects"]:
            raise ValueError("review sheet must be a recipe, not a stored object")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        errors.append(f"publication proof review recipe invalid: {error}")
    return errors


def _validate_publication_proof_v4_row(
        row: dict, manifest: dict, registry: dict, resolver) -> list[str]:
    """Validate row-specific bindings after one full manifest replay."""
    try:
        attestation = row.get("publication_attestation")
        if (not isinstance(attestation, dict)
                or attestation.get("version") != PUBLICATION_ATTESTATION_VERSION):
            raise ValueError("proof v4 requires publication attestation v3")
        if (manifest.get("run_id") != attestation.get("resolver_run_id")
                or manifest.get("area") != row.get("area")):
            raise ValueError("proof v4 resolver identity mismatch")
        fid = str(row.get("fid"))
        final = manifest["final_rows"].get(fid)
        if final != _proof_decision_row(row):
            raise ValueError("proof v4 final decision/authority row mismatch")
        prepare_bytes = _load_manifest_object(
            manifest, registry, manifest["prepare_ref"]
        )
        prepare = resolver.parse_prepare_bytes(prepare_bytes)
        facility = (
            prepare.get("source", {}).get("publication", {})
            .get("facilities", {}).get(fid)
        )
        if not isinstance(facility, dict):
            raise ValueError("proof v4 has no bound publication facility")
        for field in ("osm", "lat", "lon", "rings", "name"):
            if row.get(field) != facility.get(field):
                raise ValueError(
                    f"proof v4 final {field} differs from bound facility"
                )
        binding = manifest["packet_bindings"].get(fid)
        if (not isinstance(binding, dict)
                or tr.sha256_json(binding)
                != attestation.get("packet_sha256")):
            raise ValueError("proof v4 packet binding mismatch")
        seal_bytes = _load_manifest_object(
            manifest, registry, manifest["output_seal_ref"]
        )
        seal = json.loads(seal_bytes)
        if (resolver._json_bytes(seal) != seal_bytes
                or seal.get("seal_sha256")
                != attestation.get("output_seal_sha256")):
            raise ValueError("proof v4 output seal mismatch")
        item = next(
            (value for value in prepare["items"]
             if value["fid"] == row.get("fid")),
            None,
        )
        if item is None:
            raise ValueError("proof v4 prepare has no matching item")
        receipt_bytes = _load_manifest_object(
            manifest, registry,
            manifest["chunk_receipts"][str(item["chunk"])],
        )
        receipt = json.loads(receipt_bytes)
        if (resolver._json_bytes(receipt) != receipt_bytes
                or tr.sha256_json(receipt)
                != attestation.get("terminal_receipt_sha256")):
            raise ValueError("proof v4 terminal receipt mismatch")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        return [f"publication proof v4 invalid: {error}"]
    return []


def _validate_publication_proof_v4(
        row: dict, proof_sha: str, manifest: dict, registry: dict) -> list[str]:
    required = {
        "version", "kind", "run_id", "area", "path_scheme", "objects",
        "object_closure_sha256", "prepare_ref", "output_seal_ref",
        "sealed_outputs", "rendered_prompts", "prompt_templates",
        "normative_documents", "chunk_receipts", "terminal_drafts",
        "terminal_checkpoints", "packet_objects", "packet_bindings",
        "final_rows", "human_authority", "human_reviews",
    }
    if set(manifest) != required:
        return ["publication proof v4 manifest schema mismatch"]
    errors = []
    try:
        import resolve_trust as resolver  # lazy: resolver imports replay
        if (manifest.get("version") != PUBLICATION_PROOF_VERSION
                or manifest.get("kind") != PUBLICATION_PROOF_KIND
                or manifest.get("path_scheme") != resolver.PREPARE_PATH_SCHEME):
            raise ValueError("proof v4 version/kind/path scheme mismatch")
        table = manifest.get("objects")
        if not isinstance(table, dict) or not table:
            raise ValueError("proof v4 object table is empty")
        logical = []
        for object_sha, descriptor in table.items():
            ref = publication_objects.validate_logical_object_ref(descriptor)
            if ref.sha256 != object_sha:
                raise ValueError("proof v4 object table key/hash mismatch")
            logical.append(ref)
        if publication_objects.object_closure_sha256(logical) != manifest.get(
                "object_closure_sha256"):
            raise ValueError("proof v4 object closure hash mismatch")
        publication_objects.verify_object_closure(
            registry.data_root, logical,
            getattr(registry, "staged_objects", ()),
        )

        referenced = {
            manifest["prepare_ref"], manifest["output_seal_ref"],
        }
        for field in (
            "sealed_outputs", "rendered_prompts", "prompt_templates",
            "normative_documents", "chunk_receipts", "terminal_drafts",
            "terminal_checkpoints", "packet_objects",
        ):
            values = manifest[field]
            if not isinstance(values, dict):
                raise ValueError(f"proof v4 {field} is not an object")
            referenced.update(value for value in values.values() if value is not None)
        packet_bindings = manifest.get("packet_bindings")
        final_rows = manifest.get("final_rows")
        human_authority = manifest.get("human_authority")
        human_reviews = manifest.get("human_reviews")
        if (not isinstance(packet_bindings, dict)
                or not isinstance(final_rows, dict)
                or not isinstance(human_authority, dict)
                or not isinstance(human_reviews, dict)):
            raise ValueError("proof v4 row/authority maps are malformed")
        for binding in packet_bindings.values():
            if not isinstance(binding, dict):
                raise ValueError("proof v4 packet binding is malformed")
            tile_hashes = binding.get("tile_sha256")
            if not isinstance(tile_hashes, dict):
                raise ValueError("proof v4 packet tile binding is malformed")
            referenced.update(tile_hashes.values())
        for entry in human_authority.values():
            if not isinstance(entry, dict) or set(entry) != {
                    "receipt_ref", "source_prepare_ref"}:
                raise ValueError("proof v4 human authority entry is malformed")
            referenced.update(entry.values())
        for entry in human_reviews.values():
            if not isinstance(entry, dict):
                raise ValueError("proof v4 human review entry is malformed")
            source_artifacts = entry.get("source_artifacts")
            if not isinstance(source_artifacts, dict):
                raise ValueError("proof v4 review source map is malformed")
            referenced.update((
                entry.get("receipt_ref"), entry.get("source_prepare_ref")
            ))
            referenced.update(source_artifacts.values())
        if referenced != set(table):
            missing = sorted(referenced - set(table))
            extra = sorted(set(table) - referenced)
            raise ValueError(
                f"proof v4 object table is not an exact closure: "
                f"missing={missing[:3]} extra={extra[:3]}"
            )

        prepare_bytes = _load_manifest_object(
            manifest, registry, manifest["prepare_ref"]
        )
        prepare = resolver.parse_prepare_bytes(prepare_bytes)
        attestation = row["publication_attestation"]
        if (not isinstance(attestation, dict)
                or attestation.get("version") != PUBLICATION_ATTESTATION_VERSION):
            raise ValueError("proof v4 requires publication attestation v3")
        if (prepare.get("run_id") != manifest.get("run_id")
                or prepare.get("area") != manifest.get("area")
                or manifest.get("run_id") != attestation.get("resolver_run_id")):
            raise ValueError("proof v4 resolver identity mismatch")
        prepared_fids = {str(item["fid"]) for item in prepare["items"]}
        for field in ("packet_bindings", "packet_objects", "final_rows"):
            if set(manifest[field]) != prepared_fids:
                raise ValueError(f"proof v4 {field} coverage mismatch")

        seal_bytes = _load_manifest_object(
            manifest, registry, manifest["output_seal_ref"]
        )
        seal = json.loads(seal_bytes)
        seal_body = dict(seal)
        seal_hash = seal_body.pop("seal_sha256", None)
        expected_output_paths = {
            assignment["output_path"]
            for item in prepare["items"]
            for assignment in item["assignments"].values()
        }
        if (resolver._json_bytes(seal) != seal_bytes
                or seal.get("version") != resolver.OUTPUT_SEAL_VERSION
                or seal.get("kind") != "parking-trust-output-seal"
                or seal.get("run_id") != prepare["run_id"]
                or seal_hash != tr.sha256_json(seal_body)
                or seal_hash != attestation.get("output_seal_sha256")
                or set(seal.get("outputs") or {}) != expected_output_paths
                or set(manifest["sealed_outputs"]) != expected_output_paths):
            raise ValueError("proof v4 output seal mismatch")
        expected_prompt_paths = {
            assignment["prompt_path"]
            for item in prepare["items"]
            for assignment in item["assignments"].values()
        }
        if (set(manifest["rendered_prompts"]) != expected_prompt_paths
                or set(manifest["prompt_templates"])
                != set(prepare["prompt_template_sha256"])
                or set(manifest["normative_documents"])
                != set(prepare["normative_document_sha256"])):
            raise ValueError("proof v4 prompt/rule object maps are not closed")
        for relative, expected_hash in seal["outputs"].items():
            object_sha = manifest["sealed_outputs"][relative]
            if expected_hash is None:
                if object_sha is not None:
                    raise ValueError("proof v4 absent sealed output has an object")
                continue
            if object_sha != expected_hash:
                raise ValueError(f"proof v4 sealed output mismatch for {relative}")
            descriptor = publication_objects.validate_logical_object_ref(
                table.get(object_sha)
            )
            if descriptor.sha256 != expected_hash:
                raise ValueError(f"proof v4 sealed output mismatch for {relative}")

        decoded_templates = {}
        for role, expected_hash in prepare["prompt_template_sha256"].items():
            raw = _load_manifest_object(
                manifest, registry, manifest["prompt_templates"][role]
            )
            decoded_templates[role] = raw
            if hashlib.sha256(raw).hexdigest() != expected_hash:
                raise ValueError(f"proof v4 {role} template hash mismatch")
        for name, expected_hash in prepare["normative_document_sha256"].items():
            raw = _load_manifest_object(
                manifest, registry, manifest["normative_documents"][name]
            )
            if hashlib.sha256(raw).hexdigest() != expected_hash:
                raise ValueError(f"proof v4 {name} rule hash mismatch")

        replay_summaries: dict[int, _ProofReplaySummary] = {}
        packet_source_digest = hashlib.sha256()
        packet_source_members = 0
        portable_run = Path("/portable-proof") / prepare["run_id"]
        for item in sorted(
                prepare["items"], key=lambda value: str(value["fid"])):
            fid = item["fid"]
            binding = packet_bindings[str(fid)]
            packet_raw = _load_manifest_object(
                manifest, registry, manifest["packet_objects"][str(fid)]
            )
            packet = json.loads(packet_raw)
            if resolver._json_bytes(packet) != packet_raw:
                raise ValueError(f"proof v4 fid {fid} packet bytes are noncanonical")
            payload = {key: copy.deepcopy(value) for key, value in packet.items()
                       if key != "tiles"}
            tile_hashes = binding.get("tile_sha256")
            if (not isinstance(tile_hashes, dict)
                    or set(tile_hashes) != {"z1", "z2", "z3"}):
                raise ValueError(f"proof v4 fid {fid} tile binding is malformed")
            if (payload != binding.get("packet")
                    or packet.get("tiles") != {
                        zoom: f"tiles/{tile_hashes[zoom]}.png"
                        for zoom in ("z1", "z2", "z3")
                    }
                    or tr.sha256_json({"packet": payload, "tile_sha256": tile_hashes})
                    != item["packet_sha256"]):
                raise ValueError(f"proof v4 fid {fid} packet binding mismatch")
            packet_source_members = _update_pretty_object_digest(
                packet_source_digest, packet_source_members, str(fid), packet,
                resolver._json_bytes,
            )
            for zoom, tile_sha in sorted(tile_hashes.items()):
                prefix = b""
                for tile_chunk in publication_objects.iter_object_bytes(
                        registry.data_root, manifest["objects"][tile_sha],
                        getattr(registry, "staged_objects", ())):
                    if len(prefix) < 8:
                        prefix += tile_chunk[:8 - len(prefix)]
                if prefix != b"\x89PNG\r\n\x1a\n":
                    raise ValueError(
                        f"proof v4 fid {fid} {zoom} object is not PNG data"
                    )
            for role, assignment in item["assignments"].items():
                prompt_raw = _load_manifest_object(
                    manifest, registry,
                    manifest["rendered_prompts"][assignment["prompt_path"]],
                )
                expected_prompt = resolver._prompt(
                    role, portable_run, assignment["assignment_id"],
                    portable_run / item["packet_path"],
                    portable_run / assignment["output_path"],
                    prepare["models"][role],
                    prepare["normative_document_sha256"],
                    template_bytes=decoded_templates[role], portable=True,
                )
                if (prompt_raw != expected_prompt
                        or hashlib.sha256(prompt_raw).hexdigest()
                        != assignment["prompt_sha256"]):
                    raise ValueError(f"proof v4 fid {fid} {role} prompt mismatch")
            replayed_outputs = {}
            output_errors = []
            for role in ("challenger", "arbiter"):
                assignment = item["assignments"][role]
                relative = assignment["output_path"]
                expected_hash = seal["outputs"][relative]
                raw_output = None
                if expected_hash is not None:
                    object_sha = manifest["sealed_outputs"][relative]
                    raw_output = _load_manifest_object(
                        manifest, registry, object_sha
                    )
                    if hashlib.sha256(raw_output).hexdigest() != expected_hash:
                        raise ValueError(
                            f"proof v4 sealed output mismatch for {relative}"
                        )
                envelope, role_errors = _reconstruct_proof_envelope_bytes(
                    prepare, item, role, raw_output, payload
                )
                replayed_outputs[role] = envelope
                output_errors.extend(role_errors)
            if output_errors:
                raise ValueError(
                    f"proof v4 fid {fid} output invalid: {output_errors}"
                )
            challenger = replayed_outputs["challenger"]
            arbiter = replayed_outputs["arbiter"]
            envelopes = {
                "primary": item["primary"],
                "challenger": challenger,
                "arbiter": arbiter,
            }
            resolution = tr.resolve(
                item["route"], item["primary"], challenger, arbiter
            )
            selected_role = resolution.get("selected_role")
            selected_envelope = (
                envelopes.get(selected_role)
                if selected_role in tr.ROLES else None
            )
            replay_summaries[fid] = _ProofReplaySummary(
                state=resolution["state"],
                result=resolution.get("result"),
                selected_role=selected_role,
                reason=resolution["reason"],
                model_families=tuple(sorted({
                    tr.family(envelope)
                    for envelope in envelopes.values()
                    if envelope is not None
                })),
                selected_decision_sha256=(
                    selected_envelope.get("decision_sha256")
                    if selected_envelope is not None else None
                ),
            )
            final_row = final_rows[str(fid)]

            def validate_plain(decision: dict) -> list[str]:
                return validate_verdict_row(
                    decision, payload, allow_override=False
                )[0]

            if resolution.get("state") == "resolved":
                selected_role = resolution.get("selected_role")
                machine_row, machine_errors = machine_decision_projection(
                    final_row
                )
                if (machine_errors or machine_row is None
                        or authority_wrapper(final_row) is not None):
                    raise ValueError(
                        f"proof v4 fid {fid} resolved row is not autonomous"
                    )
                persisted = machine_row.get("trust_resolution")
                persisted_errors = tr.validate_persisted_resolution(
                    machine_row, payload, validate_plain,
                    expected_packet_sha256=item["packet_sha256"],
                )
                if persisted_errors:
                    raise ValueError(
                        f"proof v4 fid {fid} persisted resolution invalid: "
                        f"{persisted_errors}"
                    )
                if (not isinstance(persisted, dict)
                        or persisted.get("run_id") != prepare["run_id"]
                        or persisted.get("route") != item["route"]
                        or persisted.get("result") != resolution.get("result")
                        or persisted.get("selected_role") != selected_role
                        or any(persisted.get(role) != envelopes[role]
                               for role in tr.ROLES)):
                    raise ValueError(
                        f"proof v4 fid {fid} persisted resolution differs "
                        "from sealed outputs"
                    )
            elif (resolution.get("state") == "preserved"
                    and item.get("route") == "PRESERVE_MACHINE_RESOLUTION"):
                machine_row, machine_errors = machine_decision_projection(
                    final_row
                )
                if machine_errors or machine_row is None:
                    raise ValueError(
                        f"proof v4 fid {fid} preserved machine row is invalid"
                    )
                persisted_errors = tr.validate_persisted_resolution(
                    machine_row, payload, validate_plain,
                    expected_packet_sha256=item["packet_sha256"],
                )
                persisted = machine_row.get("trust_resolution")
                if (persisted_errors or not isinstance(persisted, dict)
                        or not isinstance(persisted.get("primary"), dict)
                        or persisted["primary"].get("decision")
                        != item.get("primary", {}).get("decision")):
                    raise ValueError(
                        f"proof v4 fid {fid} preserved machine resolution "
                        "differs from its prepared primary"
                    )
            # The run-wide state below contains only immutable scalar summaries.
            # Release every decoded assignment envelope before loading the next
            # packet so output size cannot multiply replay retention.
            del (
                replayed_outputs, challenger, arbiter, envelopes, resolution,
                selected_envelope, envelope,
            )

        if _finish_pretty_object_digest(
                packet_source_digest, packet_source_members,
                resolver._json_bytes) != prepare["source"]["packets_file_sha256"]:
            raise ValueError("proof v4 portable packet source map mismatch")

        snapshot_counts = {
            "items": len(prepare["items"]),
            "pending_challenger": 0,
            "pending_arbiter": 0,
            "autonomous_resolved": 0,
            "preserved_authority": 0,
            "human_exceptions": 0,
            "refresh_required": 0,
            "invalid": 0,
        }
        snapshot_items = []
        for item in prepare["items"]:
            summary = replay_summaries[item["fid"]]
            state = summary.state
            if state == "resolved":
                snapshot_counts["autonomous_resolved"] += 1
            elif state == "preserved":
                snapshot_counts["preserved_authority"] += 1
            elif state == "pending":
                key = (
                    "pending_challenger"
                    if summary.reason == "challenger decision missing"
                    else "pending_arbiter"
                )
                snapshot_counts[key] += 1
            elif state == "human_exception":
                snapshot_counts["human_exceptions"] += 1
            elif state == "blocked":
                snapshot_counts["refresh_required"] += 1
            else:
                snapshot_counts["invalid"] += 1
            snapshot_items.append({
                "fid": item["fid"],
                "chunk": item["chunk"],
                "route": item["route"],
                "state": state,
                "result": summary.result,
                "selected_role": summary.selected_role,
                "reason": summary.reason,
                "errors": [],
                "model_families": list(summary.model_families),
            })
        snapshot_status = {
            "run_id": prepare["run_id"],
            "counts": snapshot_counts,
            "items": sorted(snapshot_items, key=lambda value: value["fid"]),
        }
        if (any(item["state"] not in ("resolved", "preserved")
                for item in snapshot_items)
                or resolver._resolution_snapshot_sha256(snapshot_status)
                != seal.get("ready_snapshot_sha256")):
            raise ValueError("proof v4 whole-run READY snapshot mismatch")

        expected_chunks = {str(source["chunk"])
                           for source in prepare["source"]["drafts"]}
        for field in ("chunk_receipts", "terminal_drafts", "terminal_checkpoints"):
            if set(manifest[field]) != expected_chunks:
                raise ValueError(f"proof v4 {field} chunk set mismatch")
        for source in prepare["source"]["drafts"]:
            chunk = str(source["chunk"])
            receipt_bytes = _load_manifest_object(
                manifest, registry, manifest["chunk_receipts"][chunk]
            )
            draft_bytes = _load_manifest_object(
                manifest, registry, manifest["terminal_drafts"][chunk]
            )
            checkpoint_bytes = _load_manifest_object(
                manifest, registry, manifest["terminal_checkpoints"][chunk]
            )
            receipt = json.loads(receipt_bytes)
            rows = json.loads(draft_bytes)
            checkpoint = json.loads(checkpoint_bytes)
            chunk_items = sorted(
                (item for item in prepare["items"]
                 if item["chunk"] == source["chunk"]),
                key=lambda value: value["row_index"],
            )
            expected_rows = [
                final_rows[str(item["fid"])] for item in chunk_items
            ]
            if rows != expected_rows:
                raise ValueError(f"proof v4 chunk {chunk} terminal rows mismatch")
            transaction_values = [
                _compact_terminal_value(
                    item, replay_summaries[item["fid"]]
                )
                for item in chunk_items
            ]
            expected_terminal = resolver._terminal_vector(transaction_values)
            expected_transaction_id = resolver._transaction_id(
                prepare, source, transaction_values
            )
            receipt_errors = resolver.validate_terminal_receipt(
                receipt, expected_terminal=expected_terminal
            )
            common_receipt_invalid = (
                receipt_errors
                or resolver._json_bytes(receipt) != receipt_bytes
                or receipt.get("run_id") != prepare["run_id"]
                or receipt.get("transaction_id") != expected_transaction_id
                or receipt.get("chunk") != source["chunk"]
                or receipt.get("output_seal_sha256") != seal_hash
            )
            if receipt.get("kind") == "parking-trust-receipt":
                resolution_hashes = []
                for terminal_row in rows:
                    machine_row, machine_errors = machine_decision_projection(
                        terminal_row
                    )
                    if machine_errors or machine_row is None:
                        terminal_invalid = True
                        break
                    resolution_hashes.append(tr.sha256_json(machine_row))
                else:
                    expected_checkpoint = copy.deepcopy(
                        source["checkpoint_value"]
                    )
                    expected_checkpoint.update({
                        "completed": [value["fid"] for value in rows],
                        "draft_sha256": receipt.get("after_sha256"),
                        "judge_row_sha256": source["judge_row_sha256"],
                        "resolution_row_sha256": resolution_hashes,
                    })
                    resolved_transactions_match = all(
                        replay_summaries[item["fid"]].state != "resolved"
                        or (final_rows[str(item["fid"])].get(
                            "trust_resolution") or {}).get("transaction_id")
                        == expected_transaction_id
                        for item in chunk_items
                    )
                    terminal_invalid = (
                        receipt.get("before_sha256") != source["file_sha256"]
                        or hashlib.sha256(draft_bytes).hexdigest()
                        != receipt.get("after_sha256")
                        or hashlib.sha256(checkpoint_bytes).hexdigest()
                        != receipt.get("checkpoint_after_sha256")
                        or checkpoint != expected_checkpoint
                        or not resolved_transactions_match
                    )
            elif receipt.get("kind") == "parking-trust-preservation-receipt":
                expected_routes_sha256 = tr.sha256_json({
                    str(item["fid"]): item["route"] for item in chunk_items
                })
                terminal_invalid = (
                    any(entry["state"] != "preserved"
                        for entry in expected_terminal)
                    or receipt.get("routes_sha256")
                    != expected_routes_sha256
                    or receipt.get("draft_sha256") != source["file_sha256"]
                    or receipt.get("checkpoint_sha256")
                    != source["checkpoint_sha256"]
                    or hashlib.sha256(draft_bytes).hexdigest()
                    != receipt.get("draft_sha256")
                    or hashlib.sha256(checkpoint_bytes).hexdigest()
                    != receipt.get("checkpoint_sha256")
                    or checkpoint != source["checkpoint_value"]
                )
            else:
                terminal_invalid = True
            matching_item = next(
                (item for item in prepare["items"]
                 if item["fid"] == row.get("fid")), None
            )
            if (matching_item is not None
                    and matching_item["chunk"] == source["chunk"]
                    and tr.sha256_json(receipt)
                    != attestation.get("terminal_receipt_sha256")):
                terminal_invalid = True
            if common_receipt_invalid or terminal_invalid:
                raise ValueError(
                    f"proof v4 chunk {chunk} terminal apply receipt mismatch"
                )

        expected_authority = set()
        expected_reviews = set()
        for final in final_rows.values():
            wrapped = authority_wrapper(final)
            if wrapped is None:
                continue
            receipt_sha = wrapped[1]["authority_receipt_sha256"]
            expected_authority.add(receipt_sha)
            authority_entry = manifest["human_authority"].get(receipt_sha)
            if not isinstance(authority_entry, dict):
                raise ValueError("proof v4 human authority receipt is missing")
            receipt_bytes = _load_manifest_object(
                manifest, registry, authority_entry["receipt_ref"]
            )
            receipt = json.loads(receipt_bytes)
            authority_prepare_bytes = _load_manifest_object(
                manifest, registry, authority_entry["source_prepare_ref"]
            )
            authority_prepare = resolver.parse_prepare_bytes(
                authority_prepare_bytes
            )
            if (resolver._json_bytes(receipt) != receipt_bytes
                    or hashlib.sha256(authority_prepare_bytes).hexdigest()
                    != receipt.get("source_prepare_sha256")
                    or authority_prepare.get("run_id")
                    != receipt.get("source_run_id")):
                raise ValueError("proof v4 authority source prepare mismatch")
            receipt_errors = validate_authority_receipt(
                final, Path("/portable-authority"), pending={receipt_sha: receipt}
            )
            if receipt_errors:
                raise ValueError(f"proof v4 authority receipt invalid: {receipt_errors}")
            review_sha = wrapped[1]["review_receipt_sha256"]
            review_entry = human_reviews.get(review_sha)
            prepared_item = next(
                (item for item in authority_prepare["items"]
                 if item["fid"] == final.get("fid")),
                None,
            )
            if (not isinstance(review_entry, dict)
                    or prepared_item is None
                    or review_entry.get("source_prepare_ref")
                    != authority_entry["source_prepare_ref"]):
                raise ValueError("proof v4 authority/review source binding mismatch")
            review_receipt = json.loads(_load_manifest_object(
                manifest, registry, review_entry["receipt_ref"]
            ))
            selected_review_item = review.review_item(
                review_receipt, final["fid"]
            )
            expected_primary_assignment = resolver.expected_primary_assignment(
                authority_prepare, prepared_item
            )
            if (receipt.get("primary_envelope_sha256")
                    != prepared_item["primary"].get("envelope_sha256")
                    or receipt.get("primary_assignment_sha256")
                    != tr.sha256_json(expected_primary_assignment)
                    or receipt.get("packet_sha256")
                    != prepared_item.get("packet_sha256")
                    or selected_review_item.get("prepared_item_sha256")
                    != tr.sha256_json(prepared_item)
                    or selected_review_item.get("packet_sha256")
                    != prepared_item.get("packet_sha256")
                    or selected_review_item.get("primary_envelope_sha256")
                    != prepared_item["primary"].get("envelope_sha256")
                    or selected_review_item.get("review_item_sha256")
                    != receipt.get("review_item_sha256")):
                raise ValueError("proof v4 authority/review item binding mismatch")
            expected_reviews.add(review_sha)
        if set(manifest["human_authority"]) != expected_authority:
            raise ValueError("proof v4 human authority set mismatch")
        if set(manifest["human_reviews"]) != expected_reviews:
            raise ValueError("proof v4 human review set mismatch")
        for review_sha, entry in manifest["human_reviews"].items():
            errors.extend(_validate_review_recipe_v2(
                review_sha, entry, manifest, registry, resolver
            ))

        errors.extend(
            _validate_publication_proof_v4_row(
                row, manifest, registry, resolver
            )
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        errors.append(f"publication proof v4 invalid: {error}")
    return errors


def _validate_publication_proof(row: dict, registry: dict) -> list[str]:
    attestation = row.get("publication_attestation")
    if not isinstance(attestation, dict):
        return ["publication proof requires a valid attestation"]
    proof_sha = attestation.get("publication_proof_sha256")
    if registry.get("version") == PUBLICATION_PROOF_REGISTRY_VERSION:
        if proof_sha not in registry.get("proofs", {}):
            return ["publication proof is absent from the canonical registry"]
        try:
            manifest = _load_proof_manifest(proof_sha, registry)
        except (KeyError, TypeError, ValueError, OSError) as error:
            return [f"publication proof manifest is unavailable: {error}"]
        return _validate_publication_proof_v4(
            row, proof_sha, manifest, registry
        )
    proof = registry.get("proofs", {}).get(proof_sha)
    if not isinstance(proof, dict):
        return ["publication proof is absent from the canonical registry"]
    common_required = {
        "version", "kind", "run_id", "prepare", "output_seal",
        "sealed_outputs", "rendered_prompts", "prompt_templates",
        "normative_documents", "chunk_receipts",
        "terminal_drafts", "terminal_checkpoints", "packet_bindings", "final_rows",
        "human_authority", "proof_sha256",
    }
    proof_version = proof.get("version")
    compatibility = _publication_proof_compatibility(proof_version)
    required = (
        common_required
        | ({"human_reviews"} if compatibility["human_reviews"] else set())
        if compatibility is not None else set()
    )
    if not required or set(proof) != required:
        return ["publication proof schema mismatch"]
    errors = []
    if attestation.get("version") != compatibility["attestation_version"]:
        errors.append("publication proof/attestation version mismatch")
    body = dict(proof)
    claimed_proof = body.pop("proof_sha256", None)
    if (proof.get("kind") != PUBLICATION_PROOF_KIND
            or claimed_proof != proof_sha
            or claimed_proof != tr.sha256_json(body)):
        errors.append("publication proof hash/identity mismatch")
    try:
        import resolve_trust as resolver  # lazy: resolver imports this replay module
        prepare = resolver.validate_prepare_document(
            proof["prepare"],
            allow_legacy=(
                compatibility["prepare_version"] != resolver.PREPARE_VERSION
            ),
        )
        expected_prepare_version = compatibility["prepare_version"]
        if prepare.get("version") != expected_prepare_version:
            errors.append("publication proof/prepare generation version mismatch")
        if resolver._run_identity(prepare) != prepare.get("run_id"):
            errors.append("publication proof prepare run identity mismatch")
        if (prepare.get("run_id") != proof.get("run_id")
                or proof.get("run_id") != attestation.get("resolver_run_id")):
            errors.append("publication proof resolver run mismatch")
        proof_generation = None
        if proof_version == PATH_BOUND_PUBLICATION_PROOF_VERSION:
            try:
                proof_generation = dossier_output.validate_source_generation(
                    prepare.get("source", {}).get("source_generation"),
                    prepare.get("area"),
                )
            except (TypeError, ValueError) as error:
                errors.append(
                    f"publication proof prepare source_generation invalid: {error}"
                )
        prepared_fids = {str(value["fid"]) for value in prepare.get("items", [])}
        if set(proof.get("packet_bindings") or {}) != prepared_fids:
            errors.append("publication proof packet binding set mismatch")
        if set(proof.get("final_rows") or {}) != prepared_fids:
            errors.append("publication proof final row set mismatch")
        expected_human_receipts = {
            wrapper[1].get("authority_receipt_sha256")
            for final in (proof.get("final_rows") or {}).values()
            for wrapper in [authority_wrapper(final)] if wrapper is not None
        }
        if set(proof.get("human_authority") or {}) != expected_human_receipts:
            errors.append("publication proof human authority set mismatch")
        if compatibility["human_reviews"]:
            expected_reviews = set()
            for final in (proof.get("final_rows") or {}).values():
                wrapper_value = authority_wrapper(final)
                if wrapper_value is None:
                    continue
                review_sha = wrapper_value[1].get("review_receipt_sha256")
                if (not isinstance(review_sha, str)
                        or len(review_sha) != 64):
                    errors.append(
                        "publication proof newly emitted human authority lacks review evidence"
                    )
                else:
                    expected_reviews.add(review_sha)
            if set(proof.get("human_reviews") or {}) != expected_reviews:
                errors.append("publication proof human review set mismatch")
        prompt_templates = proof.get("prompt_templates")
        normative_documents = proof.get("normative_documents")
        if (not isinstance(prompt_templates, dict)
                or set(prompt_templates) != set(prepare["prompt_template_sha256"])
                or not isinstance(normative_documents, dict)
                or set(normative_documents) != set(prepare["normative_document_sha256"])):
            errors.append("publication proof frozen prompt/rule set mismatch")
            prompt_templates = {}
            normative_documents = {}
        decoded_templates = {}
        for role, encoded in prompt_templates.items():
            try:
                decoded = base64.b64decode(encoded, validate=True)
            except (TypeError, ValueError):
                decoded = b""
            decoded_templates[role] = decoded
            if hashlib.sha256(decoded).hexdigest() != prepare["prompt_template_sha256"][role]:
                errors.append(f"publication proof frozen {role} template hash mismatch")
        for name, encoded in normative_documents.items():
            try:
                decoded = base64.b64decode(encoded, validate=True)
            except (TypeError, ValueError):
                decoded = b""
            if hashlib.sha256(decoded).hexdigest() != prepare["normative_document_sha256"][name]:
                errors.append(f"publication proof frozen {name} hash mismatch")
        rendered_prompts = proof.get("rendered_prompts")
        expected_prompt_paths = {
            assignment["prompt_path"]
            for prepared_item in prepare.get("items", [])
            for assignment in (prepared_item.get("assignments") or {}).values()
            if isinstance(assignment, dict) and isinstance(assignment.get("prompt_path"), str)
        }
        if not isinstance(rendered_prompts, dict) or set(rendered_prompts) != expected_prompt_paths:
            errors.append("publication proof rendered prompt set mismatch")
            rendered_prompts = {}
        for prepared_item in prepare.get("items", []):
            fid = prepared_item.get("fid")
            binding = (proof.get("packet_bindings") or {}).get(str(fid))
            if proof_generation is not None:
                try:
                    packet_generation = dossier_output.validate_source_generation(
                        (binding.get("packet") if isinstance(binding, dict) else {})
                        .get("source_generation"),
                        prepare["area"],
                    )
                except (TypeError, ValueError) as error:
                    errors.append(
                        f"publication proof fid {fid} source_generation invalid: {error}"
                    )
                else:
                    if packet_generation != proof_generation:
                        errors.append(
                            f"publication proof fid {fid} source_generation mismatch"
                        )
            packet_sha = tr.sha256_json(binding) if isinstance(binding, dict) else None
            tile_hashes = (binding or {}).get("tile_sha256") if isinstance(binding, dict) else {}
            expected_inputs = sorted(set(list((tile_hashes or {}).values()) + ([packet_sha] if packet_sha else [])))
            if (prepared_item.get("packet_sha256") != packet_sha
                    or prepared_item.get("input_evidence_sha256") != expected_inputs):
                errors.append(f"publication proof fid {fid} packet/input binding mismatch")
            for role in ("challenger", "arbiter"):
                assignment = (prepared_item.get("assignments") or {}).get(role)
                if not isinstance(assignment, dict):
                    errors.append(f"publication proof fid {fid} missing {role} assignment")
                    continue
                expected_id = resolver._assignment_id(
                    prepare["run_id"], fid, role, prepare["models"][role],
                    prepared_item.get("packet_sha256"),
                    prepare["prompt_template_sha256"][role],
                    prepare["normative_document_sha256"],
                    prepare["source"].get("source_generation"),
                )
                expected_static = {
                    "assignment_id": expected_id,
                    "role": role,
                    "model": prepare["models"][role],
                    "packet_sha256": prepared_item.get("packet_sha256"),
                    "external_evidence_catalog_sha256": tr.sha256_json(
                        prepare["external_evidence_catalog"]
                    ),
                    "normative_document_sha256": prepare["normative_document_sha256"],
                    "input_evidence_sha256": expected_inputs,
                    "prompt_path": f"prompts/fid-{fid:04d}.{role}.txt",
                    "output_path": f"inbox/fid-{fid:04d}.{role}.json",
                }
                if proof_generation is not None:
                    expected_static["source_generation"] = proof_generation
                for field, value in expected_static.items():
                    if assignment.get(field) != value:
                        errors.append(f"publication proof fid {fid} {role} {field} mismatch")
                prompt_encoded = rendered_prompts.get(assignment.get("prompt_path"))
                try:
                    prompt_bytes = base64.b64decode(prompt_encoded, validate=True)
                except (TypeError, ValueError):
                    prompt_bytes = None
                if (prompt_bytes is None
                        or hashlib.sha256(prompt_bytes).hexdigest()
                        != assignment.get("prompt_sha256")):
                    errors.append(f"publication proof fid {fid} {role} prompt hash mismatch")
                try:
                    proof_run_dir = Path(prepare["run_root"]) / prepare["run_id"]
                    expected_prompt = resolver._prompt(
                        role, proof_run_dir, expected_id,
                        proof_run_dir / prepared_item["packet_path"],
                        proof_run_dir / assignment["output_path"],
                        prepare["models"][role],
                        prepare["normative_document_sha256"],
                        template_bytes=decoded_templates.get(role, b""),
                    )
                    if prompt_bytes != expected_prompt:
                        errors.append(
                            f"publication proof fid {fid} {role} rendered prompt mismatch"
                        )
                except (KeyError, TypeError, ValueError) as error:
                    errors.append(
                        f"publication proof fid {fid} {role} prompt reconstruction failed: {error}"
                    )
        seal = proof["output_seal"]
        required_seal = {
            "version", "kind", "run_id", "outputs", "ready_snapshot_sha256",
            "seal_sha256",
        }
        expected_outputs = {
            assignment["output_path"]
            for prepared_item in prepare.get("items", [])
            for assignment in (prepared_item.get("assignments") or {}).values()
        }
        if (not isinstance(seal, dict) or set(seal) != required_seal
                or type(seal.get("version")) is not int
                or seal.get("version") != resolver.OUTPUT_SEAL_VERSION
                or seal.get("kind") != "parking-trust-output-seal"
                or set(seal.get("outputs") or {}) != expected_outputs):
            errors.append("publication proof output seal schema/assignment set mismatch")
        seal_body = dict(seal)
        seal_hash = seal_body.pop("seal_sha256", None)
        if (seal_hash != tr.sha256_json(seal_body)
                or seal_hash != attestation.get("output_seal_sha256")
                or seal.get("run_id") != prepare.get("run_id")):
            errors.append("publication proof output seal mismatch")
        sealed_outputs = proof["sealed_outputs"]
        if not isinstance(sealed_outputs, dict) or set(sealed_outputs) != set(seal.get("outputs") or {}):
            errors.append("publication proof sealed output set mismatch")
        else:
            for relative, expected_hash in seal["outputs"].items():
                encoded = sealed_outputs.get(relative)
                try:
                    raw_bytes = base64.b64decode(encoded, validate=True)
                except (TypeError, ValueError):
                    raw_bytes = None
                actual_hash = hashlib.sha256(raw_bytes).hexdigest() if raw_bytes is not None else None
                if actual_hash != expected_hash:
                    errors.append(f"publication proof sealed output mismatch for {relative}")
        snapshot_counts = {
            "items": len(prepare.get("items", [])), "pending_challenger": 0,
            "pending_arbiter": 0, "autonomous_resolved": 0,
            "preserved_authority": 0, "human_exceptions": 0,
            "refresh_required": 0, "invalid": 0,
        }
        snapshot_items = []
        expected_terminal_by_chunk = {}
        for prepared_item in prepare.get("items", []):
            fid = prepared_item["fid"]
            final = (proof.get("final_rows") or {}).get(str(fid)) or {}
            binding = (proof.get("packet_bindings") or {}).get(str(fid)) or {}
            packet_identity = binding.get("packet") if isinstance(binding, dict) else {}
            challenger, challenger_errors = _reconstruct_proof_envelope(
                prepare, prepared_item, "challenger", sealed_outputs, packet_identity
            )
            arbiter, arbiter_errors = _reconstruct_proof_envelope(
                prepare, prepared_item, "arbiter", sealed_outputs, packet_identity
            )
            errors.extend(
                f"publication proof fid {fid}: {error}"
                for error in challenger_errors + arbiter_errors
            )
            resolution = tr.resolve(
                prepared_item["route"], prepared_item["primary"], challenger, arbiter
            )
            selected_role = resolution.get("selected_role")
            selected_envelope = (
                {"primary": prepared_item["primary"], "challenger": challenger,
                 "arbiter": arbiter}.get(selected_role)
            )
            expected_terminal_by_chunk.setdefault(
                str(prepared_item["chunk"]), []
            ).append({
                "fid": fid,
                "state": resolution["state"],
                "selected_role": selected_role,
                "decision_sha256": (
                    selected_envelope.get("decision_sha256")
                    if isinstance(selected_envelope, dict) else None
                ),
            })
            if resolution["state"] == "resolved":
                snapshot_counts["autonomous_resolved"] += 1
            elif resolution["state"] == "preserved":
                snapshot_counts["preserved_authority"] += 1
            elif resolution["state"] == "pending":
                key = ("pending_challenger" if resolution["reason"] == "challenger decision missing"
                       else "pending_arbiter")
                snapshot_counts[key] += 1
            elif resolution["state"] == "human_exception":
                snapshot_counts["human_exceptions"] += 1
            elif resolution["state"] == "blocked":
                snapshot_counts["refresh_required"] += 1
            snapshot_items.append({
                "fid": fid, "chunk": prepared_item["chunk"],
                "route": prepared_item["route"], "state": resolution["state"],
                "result": resolution.get("result"),
                "selected_role": resolution.get("selected_role"),
                "reason": resolution["reason"], "errors": [],
                "model_families": sorted({
                    tr.family(envelope) for envelope in (
                        prepared_item["primary"], challenger, arbiter
                    ) if envelope is not None
                }),
            })
        snapshot_status = {
            "run_id": prepare.get("run_id"),
            "counts": snapshot_counts,
            "items": sorted(snapshot_items, key=lambda value: value["fid"]),
        }
        if (any(item["state"] not in ("resolved", "preserved") for item in snapshot_items)
                or resolver._resolution_snapshot_sha256(snapshot_status)
                != seal.get("ready_snapshot_sha256")):
            errors.append("publication proof whole-run READY snapshot mismatch")
        chunk_receipts = proof.get("chunk_receipts")
        expected_chunks = {str(source["chunk"]) for source in prepare["source"]["drafts"]}
        if not isinstance(chunk_receipts, dict) or set(chunk_receipts) != expected_chunks:
            errors.append("publication proof chunk receipt set mismatch")
            chunk_receipts = {}
        terminal_drafts = proof.get("terminal_drafts")
        terminal_checkpoints = proof.get("terminal_checkpoints")
        if (not isinstance(terminal_drafts, dict) or set(terminal_drafts) != expected_chunks
                or not isinstance(terminal_checkpoints, dict)
                or set(terminal_checkpoints) != expected_chunks):
            errors.append("publication proof terminal source set mismatch")
            terminal_drafts = {}
            terminal_checkpoints = {}
        for source in prepare["source"]["drafts"]:
            chunk_key = str(source["chunk"])
            receipt = chunk_receipts.get(chunk_key)
            try:
                terminal_draft_bytes = base64.b64decode(
                    terminal_drafts[chunk_key], validate=True
                )
                terminal_checkpoint_bytes = base64.b64decode(
                    terminal_checkpoints[chunk_key], validate=True
                )
                terminal_rows = json.loads(terminal_draft_bytes)
                terminal_checkpoint = json.loads(terminal_checkpoint_bytes)
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                terminal_draft_bytes = b""
                terminal_checkpoint_bytes = b""
                terminal_rows = None
                terminal_checkpoint = None
                errors.append(f"publication proof chunk {chunk_key} terminal bytes are invalid")
            if proof_generation is not None:
                try:
                    checkpoint_generation = dossier_output.validate_source_generation(
                        (terminal_checkpoint or {}).get("source_generation"),
                        prepare["area"],
                    )
                except (TypeError, ValueError) as error:
                    errors.append(
                        f"publication proof chunk {chunk_key} source_generation invalid: {error}"
                    )
                else:
                    if checkpoint_generation != proof_generation:
                        errors.append(
                            f"publication proof chunk {chunk_key} source_generation mismatch"
                        )
            if (not isinstance(terminal_rows, list)
                    or any(not isinstance(terminal_row, dict)
                           or type(terminal_row.get("fid")) is not int
                           or not 0 <= terminal_row["fid"] <= sys.maxsize
                           for terminal_row in terminal_rows)):
                errors.append(
                    f"publication proof chunk {chunk_key} terminal rows are malformed"
                )
                terminal_rows = []
            expected_rows = [
                (proof.get("final_rows") or {}).get(str(item["fid"]))
                for item in sorted(
                    (item for item in prepare["items"] if item["chunk"] == source["chunk"]),
                    key=lambda value: value["row_index"],
                )
            ]
            if terminal_rows != expected_rows:
                errors.append(f"publication proof chunk {chunk_key} terminal draft rows mismatch")
            terminal_vector = expected_terminal_by_chunk.get(chunk_key, [])
            transaction_id = tr.sha256_json({
                "run_id": prepare["run_id"],
                "chunk": source["chunk"],
                "before_sha256": source["file_sha256"],
                "selected": terminal_vector,
            })
            all_preserved = all(entry["state"] == "preserved" for entry in terminal_vector)
            receipt_errors = resolver.validate_terminal_receipt(
                receipt, expected_terminal=terminal_vector
            )
            if (receipt_errors
                    or receipt.get("run_id") != prepare["run_id"]
                    or receipt.get("transaction_id") != transaction_id
                    or receipt.get("output_seal_sha256") != seal_hash
                    or receipt.get("chunk") != source["chunk"]):
                errors.append(
                    f"publication proof chunk {chunk_key} receipt is noncanonical: "
                    f"{receipt_errors}"
                )
                continue
            if all_preserved:
                expected_routes = tr.sha256_json({
                    str(item["fid"]): item["route"] for item in prepare["items"]
                    if item["chunk"] == source["chunk"]
                })
                if (receipt.get("kind") != "parking-trust-preservation-receipt"
                        or receipt.get("draft_sha256") != source["file_sha256"]
                        or receipt.get("checkpoint_sha256") != source["checkpoint_sha256"]
                        or hashlib.sha256(terminal_draft_bytes).hexdigest()
                        != receipt.get("draft_sha256")
                        or hashlib.sha256(terminal_checkpoint_bytes).hexdigest()
                        != receipt.get("checkpoint_sha256")
                        or terminal_checkpoint != source["checkpoint_value"]
                        or receipt.get("routes_sha256") != expected_routes):
                    errors.append(f"publication proof chunk {chunk_key} preservation receipt mismatch")
            else:
                expected_applied = [
                    {"fid": entry["fid"], "decision_sha256": entry["decision_sha256"]}
                    for entry in terminal_vector if entry["state"] == "resolved"
                ]
                resolution_hashes = []
                for terminal_row in terminal_rows or []:
                    machine_row, machine_errors = machine_decision_projection(terminal_row)
                    if machine_errors or machine_row is None:
                        errors.append(
                            f"publication proof chunk {chunk_key} terminal machine projection invalid"
                        )
                        continue
                    resolution_hashes.append(tr.sha256_json(machine_row))
                expected_checkpoint = copy.deepcopy(source["checkpoint_value"])
                expected_checkpoint.update({
                    "completed": [row["fid"] for row in (terminal_rows or [])],
                    "draft_sha256": receipt.get("after_sha256"),
                    "judge_row_sha256": source["judge_row_sha256"],
                    "resolution_row_sha256": resolution_hashes,
                })
                if (receipt.get("kind") != "parking-trust-receipt"
                        or receipt.get("before_sha256") != source["file_sha256"]
                        or hashlib.sha256(terminal_draft_bytes).hexdigest()
                        != receipt.get("after_sha256")
                        or hashlib.sha256(terminal_checkpoint_bytes).hexdigest()
                        != receipt.get("checkpoint_after_sha256")
                        or not isinstance(terminal_checkpoint, dict)
                        or terminal_checkpoint != expected_checkpoint
                        or terminal_checkpoint.get("draft_sha256") != receipt.get("after_sha256")
                        or terminal_checkpoint.get("judge_row_sha256") != source["judge_row_sha256"]
                        or receipt.get("applied_fids") != expected_applied):
                    errors.append(f"publication proof chunk {chunk_key} apply receipt mismatch")
        item = next(
            (value for value in prepare.get("items", []) if value.get("fid") == row.get("fid")),
            None,
        )
        if item is None:
            errors.append("publication proof prepare has no matching item")
        else:
            final_row = (proof.get("final_rows") or {}).get(str(row.get("fid")))
            if final_row != _proof_decision_row(row):
                errors.append("publication proof final decision/authority row mismatch")
            receipt = (proof.get("chunk_receipts") or {}).get(str(item["chunk"]))
            valid_receipt_kind = receipt.get("kind") in (
                "parking-trust-receipt", "parking-trust-preservation-receipt"
            ) if isinstance(receipt, dict) else False
            if (not isinstance(receipt, dict)
                    or type(receipt.get("version")) is not int
                    or receipt.get("version") != 1
                    or not valid_receipt_kind
                    or tr.sha256_json(receipt) != attestation.get("terminal_receipt_sha256")
                    or receipt.get("run_id") != prepare.get("run_id")
                    or receipt.get("output_seal_sha256") != seal_hash
                    or receipt.get("chunk") != item.get("chunk")):
                errors.append("publication proof terminal receipt mismatch")
            terminal_entries = (
                receipt.get("terminal_fids")
                if isinstance(receipt, dict) else None
            )
            if not isinstance(terminal_entries, list):
                terminal_entries = []
            terminal_entry = next(
                (entry for entry in terminal_entries
                 if isinstance(entry, dict) and entry.get("fid") == row.get("fid")),
                None,
            )
            final_wrapper = final_row.get("trust_resolution") if isinstance(final_row, dict) else None
            expected_terminal = {
                "fid": row.get("fid"),
                "state": "preserved" if item.get("route") in (
                    "PRESERVE_HUMAN_AUTHORITY", "PRESERVE_MACHINE_RESOLUTION"
                ) else "resolved",
                "selected_role": None if item.get("route") in (
                    "PRESERVE_HUMAN_AUTHORITY", "PRESERVE_MACHINE_RESOLUTION"
                ) else (final_wrapper or {}).get("selected_role"),
                "decision_sha256": None if item.get("route") in (
                    "PRESERVE_HUMAN_AUTHORITY", "PRESERVE_MACHINE_RESOLUTION"
                ) else (final_wrapper or {}).get("selected_decision_sha256"),
            }
            if terminal_entry != expected_terminal:
                errors.append("publication proof row is absent from terminal receipt vector")
            facility = (prepare.get("source", {}).get("publication", {})
                        .get("facilities", {}).get(str(row.get("fid"))))
            if not isinstance(facility, dict):
                errors.append("publication proof has no bound facility")
            else:
                for field in ("osm", "lat", "lon", "rings", "name"):
                    if row.get(field) != facility.get(field):
                        errors.append(f"publication proof final {field} differs from bound facility")
        packet_binding = (proof.get("packet_bindings") or {}).get(str(row.get("fid")))
        if (not isinstance(packet_binding, dict)
                or tr.sha256_json(packet_binding) != attestation.get("packet_sha256")):
            errors.append("publication proof packet binding mismatch")
        authority = current_authority(row)
        if (authority is not None and authority[0] == "machine_v2"
                and isinstance(packet_binding, dict)):
            packet_identity = packet_binding.get("packet")
            if not isinstance(packet_identity, dict):
                errors.append("publication proof machine packet payload is malformed")
            else:
                def validate_plain(decision: dict) -> list[str]:
                    return validate_verdict_row(
                        decision, packet_identity, allow_override=False
                    )[0]
                expected_primary = resolver.expected_primary_assignment(prepare, item)
                primary_errors = tr.validate_envelope(
                    item.get("primary"), "primary", packet_identity, validate_plain,
                    expected_assignment=expected_primary,
                    expected_packet_sha256=item.get("packet_sha256"),
                    expected_version=tr.VERSION,
                )
                errors.extend(
                    f"publication proof primary: {error}" for error in primary_errors
                )
                wrapper = row.get("trust_resolution") or {}
                preserved_machine = item.get("route") == "PRESERVE_MACHINE_RESOLUTION"
                if preserved_machine:
                    prepared_primary_decision = (item.get("primary") or {}).get("decision") or {}
                    persisted_primary_decision = (wrapper.get("primary") or {}).get("decision") or {}
                    if tr.decision_content(persisted_primary_decision) != tr.decision_content(prepared_primary_decision):
                        errors.append(
                            "publication proof preserved machine primary decision differs from prepare"
                        )
                if (not preserved_machine
                        and wrapper.get("primary") != item.get("primary")):
                    errors.append("publication proof persisted primary differs from prepare")
                for role in ("challenger", "arbiter"):
                    assignment = (item.get("assignments") or {}).get(role)
                    if not isinstance(assignment, dict):
                        errors.append(f"publication proof missing {role} assignment")
                        continue
                    expected_assignment_id = resolver._assignment_id(
                        prepare["run_id"], item["fid"], role,
                        prepare["models"][role], item["packet_sha256"],
                        prepare["prompt_template_sha256"][role],
                        prepare["normative_document_sha256"],
                        prepare["source"].get("source_generation"),
                    )
                    if assignment.get("assignment_id") != expected_assignment_id:
                        errors.append(f"publication proof {role} assignment id mismatch")
                    persisted_envelope = wrapper.get(role)
                    reconstructed, envelope_errors = _reconstruct_proof_envelope(
                        prepare, item, role, sealed_outputs, packet_identity
                    )
                    errors.extend(
                        f"publication proof {role}: {error}" for error in envelope_errors
                    )
                    if reconstructed is None:
                        if persisted_envelope is not None and not preserved_machine:
                            errors.append(f"publication proof {role} envelope has no sealed output")
                        continue
                    if not preserved_machine and persisted_envelope != reconstructed:
                        errors.append(
                            f"publication proof persisted {role} envelope differs from sealed output"
                        )
                machine_errors = tr.validate_persisted_resolution(
                    row, packet_identity, validate_plain,
                    expected_packet_sha256=attestation.get("packet_sha256"),
                )
                errors.extend(
                    f"publication proof machine replay: {error}"
                    for error in machine_errors
                )
                if not preserved_machine:
                    receipt = (proof.get("chunk_receipts") or {}).get(
                        str(item["chunk"]) if item is not None else ""
                    )
                    selected = ((row.get("trust_resolution") or {})
                                .get("selected_decision_sha256"))
                    applied = receipt.get("applied_fids") if isinstance(receipt, dict) else None
                    if (not isinstance(applied, list)
                            or {"fid": row.get("fid"), "decision_sha256": selected} not in applied
                            or receipt.get("transaction_id")
                            != (row.get("trust_resolution") or {}).get("transaction_id")):
                        errors.append("publication proof machine row is absent from terminal receipt")
        bound = authority_wrapper(row)
        if bound is not None:
            receipt_sha = bound[1].get("authority_receipt_sha256")
            human = (proof.get("human_authority") or {}).get(receipt_sha)
            if not isinstance(human, dict) or set(human) != {"receipt", "source_prepare_b64"}:
                errors.append("publication proof human authority is missing")
            else:
                receipt = human["receipt"]
                expected_receipt_version = compatibility[
                    "human_receipt_version"
                ]
                if receipt.get("version") != expected_receipt_version:
                    errors.append(
                        "publication proof human authority receipt version mismatch"
                    )
                try:
                    source_bytes = base64.b64decode(
                        human["source_prepare_b64"], validate=True
                    )
                    source_prepare = _parse_embedded_proof_prepare(
                        source_bytes, resolver,
                        compatibility["prepare_version"],
                    )
                except (TypeError, ValueError, json.JSONDecodeError):
                    source_bytes = None
                    source_prepare = None
                if (not isinstance(source_prepare, dict)
                        or resolver._run_identity(source_prepare) != source_prepare.get("run_id")
                        or receipt.get("source_run_id") != source_prepare.get("run_id")
                        or (hashlib.sha256(source_bytes).hexdigest()
                            if source_bytes is not None else None)
                        != receipt.get("source_prepare_sha256")):
                    errors.append("publication proof human source prepare mismatch")
                if isinstance(source_prepare, dict):
                    source_item = next(
                        (value for value in source_prepare.get("items", [])
                         if value.get("fid") == row.get("fid")),
                        None,
                    )
                    if source_item is None:
                        errors.append("publication proof human source item is missing")
                    else:
                        primary = source_item.get("primary") or {}
                        if primary.get("envelope_sha256") != receipt.get("primary_envelope_sha256"):
                            errors.append("publication proof human primary envelope mismatch")
                        expected_assignment = resolver.expected_primary_assignment(
                            source_prepare, source_item
                        )
                        if tr.sha256_json(expected_assignment) != receipt.get("primary_assignment_sha256"):
                            errors.append("publication proof human primary assignment mismatch")
                        if source_item.get("packet_sha256") != receipt.get("packet_sha256"):
                            errors.append("publication proof human source packet mismatch")
                        source_decision = (
                            bound[1].get("from_decision")
                            if bound[0].startswith("override_v")
                            else _proof_decision_row(row)
                        )
                        if isinstance(source_decision, dict):
                            source_decision.pop("human_confirmation", None)
                        if tr.decision_projection(primary.get("decision") or {}) != tr.decision_projection(source_decision or {}):
                            errors.append("publication proof human source decision mismatch")
                        if isinstance(packet_binding, dict) and isinstance(packet_binding.get("packet"), dict):
                            source_packet = packet_binding["packet"]
                            def validate_source_plain(decision: dict) -> list[str]:
                                return validate_verdict_row(
                                    decision, source_packet, allow_override=False
                                )[0]
                            primary_errors = tr.validate_envelope(
                                primary, "primary", source_packet, validate_source_plain,
                                expected_assignment=expected_assignment,
                                expected_packet_sha256=receipt.get("packet_sha256"),
                                expected_version=tr.VERSION,
                            )
                            errors.extend(
                                f"publication proof human primary: {error}"
                                for error in primary_errors
                            )
                receipt_errors = validate_authority_receipt(
                    row, Path("."), pending={receipt_sha: receipt}
                )
                errors.extend(
                    f"publication proof {error}" for error in receipt_errors
                )
                if compatibility["human_reviews"]:
                    review_sha = bound[1].get("review_receipt_sha256")
                    review_bundle = (proof.get("human_reviews") or {}).get(
                        review_sha
                    )
                    errors.extend(_validate_review_bundle(
                        review_bundle, receipt, bound[1], row, resolver,
                        expected_prepare_version=compatibility[
                            "prepare_version"
                        ],
                    ))
                    if attestation.get("review_receipt_sha256") != review_sha:
                        errors.append(
                            "publication proof attestation review receipt mismatch"
                        )
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        errors.append(f"publication proof malformed: {error}")
    return errors


def _configured_store_specs(builder):
    import _parking_verdict_source as source  # dependency leaf

    key_kinds = getattr(builder, "STORE_KEY_KINDS", None)
    if not isinstance(key_kinds, dict):
        raise ValueError("parking verdict store key configuration is required")
    return source.configured_store_specs(builder.STORES, key_kinds)


def _configured_store_names(builder) -> tuple[str, ...]:
    return tuple(spec.filename for spec in _configured_store_specs(builder))


def _load_legacy_baseline(builder, specs, secure_reader):
    import _parking_verdict_source as source  # dependency leaf

    configured_path = getattr(builder, "LEGACY_ROW_BASELINE_PATH", None)
    configured_sha256 = getattr(builder, "LEGACY_ROW_BASELINE_SHA256", None)
    if configured_path is None:
        raise ValueError("configured legacy proofless-row baseline path is required")
    path = Path(configured_path)
    raw = secure_reader(path)
    return source.parse_legacy_baseline(raw, specs, configured_sha256)


def _publication_root_settings(builder, specs):
    import _parking_verdict_source as source  # dependency leaf

    configured_root = getattr(builder, "PUBLICATION_TRUST_ROOT_PATH", None)
    configured_pin = getattr(builder, "PUBLICATION_TRUST_ROOT_SHA256", None)
    configured_baseline = getattr(builder, "LEGACY_ROW_BASELINE_PATH", None)
    if configured_root is None:
        raise ValueError("configured publication trust root path is required")
    if configured_baseline is None:
        raise ValueError("configured legacy proofless-row baseline path is required")
    root_path = Path(configured_root).expanduser().resolve()
    baseline_path = Path(configured_baseline).expanduser().resolve()
    source.publication_artifact_filenames(root_path.name)
    source.publication_artifact_filenames(baseline_path.name)
    if root_path == baseline_path:
        raise ValueError("publication trust root collides with legacy baseline")
    if tuple(spec.filename for spec in specs) != tuple(dict.fromkeys(
            spec.filename for spec in specs)):
        raise ValueError("publication trust root store configuration is duplicated")
    return root_path, configured_pin, baseline_path


def publication_configuration_for_store(
        store_name: str, data_dir: Path | None = None):
    """Return the production builder configuration only for a rooted store."""
    builder_module = _load_builder()
    factory = getattr(builder_module, "_builder_configuration", None)
    builder = factory() if callable(factory) else builder_module
    specs = _configured_store_specs(builder)
    if store_name not in {spec.filename for spec in specs}:
        return None
    if data_dir is not None:
        configured_data = getattr(builder, "DATA_PATH", None)
        if (configured_data is None
                or Path(configured_data).expanduser().resolve()
                != Path(data_dir).expanduser().resolve()):
            return None
    _publication_root_settings(builder, specs)
    return builder


def load_pinned_publication_root(builder):
    """Read and parse the exact code-pinned root before any corpus semantics."""
    import _parking_verdict_source as source  # dependency leaf

    specs = _configured_store_specs(builder)
    root_path, root_pin, baseline_path = _publication_root_settings(
        builder, specs
    )
    raw = trusted_fs.read_regular_bytes(root_path)
    root = source.parse_publication_trust_root(
        raw, specs, root_pin, baseline_path.name
    )
    return specs, root_path, baseline_path, raw, root


def publication_resource_modes(data_dir: Path, builder,
                               target_store: str):
    """Return one globally sortable lock set for a terminal publication."""
    import merge_drafts as merger  # lazy: merge imports resolver/replay

    data_dir = Path(data_dir).expanduser().resolve()
    specs = _configured_store_specs(builder)
    configured_names = {spec.filename for spec in specs}
    if target_store not in configured_names:
        raise ValueError("terminal publication target is not a rooted configured store")
    root_path, _root_pin, baseline_path = _publication_root_settings(
        builder, specs
    )
    modes = [
        (root_path, fcntl.LOCK_EX),
        (data_dir / publication_objects.OBJECT_DIRECTORY, fcntl.LOCK_EX),
        (tr.dossier_resource_path(data_dir), fcntl.LOCK_SH),
        (baseline_path, fcntl.LOCK_SH),
    ]
    for spec in specs:
        store_path = data_dir / spec.filename
        proof_path = _publication_proof_path(data_dir, spec.filename)
        floor_path = _publication_floor_path(data_dir, spec.filename)
        journal = merger._publication_transaction_paths(
            store_path, proof_path, "locks", floor_path=floor_path
        )["journal"]
        mode = fcntl.LOCK_EX if spec.filename == target_store else fcntl.LOCK_SH
        modes.extend((
            (store_path, mode), (proof_path, mode), (floor_path, mode),
            (journal, mode),
        ))
    return modes


def validate_locked_publication_corpus(
        data_dir: Path, builder, resolver, *, root,
        target_store: str, target_before_bytes: dict[str, bytes | None],
        allow_target_journal: bool) -> dict:
    """Validate the complete rooted before-image while its global locks are held."""
    import _parking_verdict_source as source  # dependency leaf
    import merge_drafts as merger  # lazy: merge imports resolver/replay

    data_dir = Path(data_dir).expanduser().resolve()
    specs = _configured_store_specs(builder)
    _root_path, _root_pin, baseline_path = _publication_root_settings(
        builder, specs
    )
    if (target_store not in {spec.filename for spec in specs}
            or set(target_before_bytes) != {"store", "proof", "floor"}
            or not isinstance(target_before_bytes.get("store"), bytes)
            or not isinstance(target_before_bytes.get("floor"), bytes)
            or (target_before_bytes.get("proof") is not None
                and not isinstance(target_before_bytes.get("proof"), bytes))):
        raise ValueError("publication target before-image is malformed")
    if (target_before_bytes["proof"] is not None
            and len(target_before_bytes["proof"])
            > publication_objects.REGISTRY_MAX_BYTES):
        raise ValueError(
            publication_objects.registry_cap_message(
                "publication target proof registry"
            )
        )

    store_bytes = {}
    proof_bytes = {}
    floor_bytes = {}
    for spec in specs:
        store_path = data_dir / spec.filename
        proof_path = _publication_proof_path(data_dir, spec.filename)
        floor_path = _publication_floor_path(data_dir, spec.filename)
        journal = merger._publication_transaction_paths(
            store_path, proof_path, "corpus", floor_path=floor_path
        )["journal"]
        journal_present = os.path.lexists(journal)
        if spec.filename == target_store:
            if journal_present != allow_target_journal:
                raise ValueError(
                    f"{spec.filename}: publication journal state does not match "
                    "the independently reconstructed plan"
                )
            store_bytes[spec.filename] = bytes(target_before_bytes["store"])
            proof = target_before_bytes["proof"]
            proof_bytes[spec.filename] = None if proof is None else bytes(proof)
            floor_bytes[spec.filename] = bytes(target_before_bytes["floor"])
            continue
        if journal_present:
            raise ValueError(
                f"{spec.filename}: sibling publication journal blocks candidate creation"
            )
        store_bytes[spec.filename] = trusted_fs.read_regular_bytes(store_path)
        proof_bytes[spec.filename] = _read_publication_registry_bytes(proof_path)
        floor_bytes[spec.filename] = trusted_fs.read_regular_bytes(floor_path)

    baseline_bytes = trusted_fs.read_regular_bytes(baseline_path)
    dossier_bytes = _capture_dossier_snapshot(data_dir, resolver)
    source.validate_publication_trust_root_image(
        root, specs, store_bytes, proof_bytes, floor_bytes,
        baseline_path.name, baseline_bytes, dossier_bytes,
    )
    _validate_dossier_generation_snapshot(data_dir, resolver, dossier_bytes)

    # Root image validation deliberately precedes every semantic trust check.
    source.dossier_indexes_from_bytes(dossier_bytes)
    baseline = source.parse_legacy_baseline(
        baseline_bytes, specs,
        getattr(builder, "LEGACY_ROW_BASELINE_SHA256", None),
    )
    uses_legacy = False
    for spec in specs:
        try:
            values = json.loads(store_bytes[spec.filename])
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError(
                f"{spec.filename} rooted store is not valid JSON: {error}"
            ) from error
        registry = (
            _load_publication_proofs(
                data_dir, spec.filename, proof_bytes[spec.filename]
            )
            if proof_bytes[spec.filename] is not None
            else _empty_publication_registry()
        )
        uses_legacy = (
            validate_store_proof_image(spec, values, registry, baseline)
            or uses_legacy
        )
        floor = source.parse_publication_floor(
            floor_bytes[spec.filename], spec
        )
        validate_store_floor_image(spec, values, floor)
    if uses_legacy:
        baseline.require_exact_dossiers(dossier_bytes)
    return {
        "specs": specs,
        "baseline": baseline,
        "store_bytes": store_bytes,
        "proof_bytes": proof_bytes,
        "floor_bytes": floor_bytes,
        "baseline_path": baseline_path,
        "baseline_bytes": baseline_bytes,
        "dossier_bytes": dossier_bytes,
    }


def _file_identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        value.st_dev, value.st_ino, value.st_mode, value.st_size,
        value.st_mtime_ns,
    )


def _generation_manifest_inventory(
        data_dir: Path, resolver) -> dict[str, tuple[int, int, int, int, int]]:
    directory_fd = resolver._open_directory_fd(data_dir, create=False)
    try:
        result = {}
        for name in sorted(os.listdir(directory_fd)):
            if not name.endswith("_generation.json"):
                continue
            value = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if not stat.S_ISREG(value.st_mode):
                raise ValueError(
                    f"generation manifest is not a regular no-follow file: {name}"
                )
            result[name] = _file_identity(value)
        return result
    finally:
        os.close(directory_fd)


def _validate_dossier_generation_snapshot(
        data_dir: Path, resolver,
        dossier_bytes: dict[str, bytes]) -> frozenset[str]:
    """Require one exact verified quartet manifest per captured dossier."""
    areas = {}
    suffix = "_dossier.json"
    for name in sorted(dossier_bytes):
        if not name.endswith(suffix):
            raise ValueError(f"captured dossier has a noncanonical name: {name}")
        area = name[:-len(suffix)]
        try:
            dossier_output.canonical_area(area)
        except ValueError as error:
            raise ValueError(f"captured dossier has a noncanonical name: {name}") from error
        areas[area] = name

    before = _generation_manifest_inventory(data_dir, resolver)
    expected = {f"{area}_generation.json" for area in areas}
    if set(before) != expected:
        missing = sorted(expected - set(before))
        extra = sorted(set(before) - expected)
        raise ValueError(
            "generation manifest inventory does not exactly match dossiers: "
            f"missing={missing[:5]} extra={extra[:5]}"
        )

    source_names = set(expected)
    for area, dossier_name in sorted(areas.items()):
        captured = dossier_output.capture_generation_locked(data_dir, area)
        captured_dossier_sha256 = captured["source_generation"][
            "artifact_sha256"
        ]["dossier"]
        expected_dossier_sha256 = hashlib.sha256(
            dossier_bytes[dossier_name]
        ).hexdigest()
        if captured_dossier_sha256 != expected_dossier_sha256:
            raise ValueError(
                f"{area} generation dossier does not match the rooted dossier image"
            )
        source_names.update((
            f"{area}_dossier.json",
            f"{area}_serves2.json",
            f"{area}_context.json",
            f"{area}_walk.json",
        ))
    if _generation_manifest_inventory(data_dir, resolver) != before:
        raise ValueError("generation manifest inventory changed during validation")
    return frozenset(source_names)


def _capture_dossier_snapshot(data_dir: Path, resolver) -> dict[str, bytes]:
    """Capture one stable, no-follow dossier name/inode/byte image."""
    directory_fd = resolver._open_directory_fd(data_dir, create=False)
    try:
        def inventory() -> dict[str, tuple[int, int, int, int, int]]:
            result = {}
            for name in sorted(os.listdir(directory_fd)):
                if not name.endswith("_dossier.json"):
                    continue
                value = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                if not stat.S_ISREG(value.st_mode):
                    raise ValueError(f"dossier path is not a regular no-follow file: {name}")
                result[name] = _file_identity(value)
            return result

        before = inventory()
        captured = {}
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        for name, expected_identity in before.items():
            fd = os.open(name, flags, dir_fd=directory_fd)
            try:
                opened = os.fstat(fd)
                if (not stat.S_ISREG(opened.st_mode)
                        or _file_identity(opened) != expected_identity):
                    raise ValueError(f"dossier was replaced during capture: {name}")
                raw = resolver._read_fd_bytes(fd)
                after_read = os.fstat(fd)
                live = os.stat(
                    name, dir_fd=directory_fd, follow_symlinks=False
                )
                if (_file_identity(after_read) != expected_identity
                        or _file_identity(live) != expected_identity):
                    raise ValueError(f"dossier changed during capture: {name}")
                captured[name] = raw
            finally:
                os.close(fd)
        if inventory() != before:
            raise ValueError("dossier file set changed during capture")
        return captured
    finally:
        os.close(directory_fd)


def validate_store_proof_image(spec, values: dict, registry: dict,
                               baseline) -> bool:
    """Apply authority/proof/baseline policy; return whether legacy rows remain."""
    import _parking_verdict_source as source  # dependency leaf

    if not isinstance(values, dict):
        raise ValueError(f"{spec.filename} must contain an object")
    legacy_registry = (
        isinstance(registry, dict)
        and set(registry) == {"version", "proofs"}
        and registry.get("version") == LEGACY_PUBLICATION_PROOF_REGISTRY_VERSION
        and isinstance(registry.get("proofs"), dict)
    )
    compact_registry = (
        isinstance(registry, dict)
        and set(registry) == {"version", "kind", "proofs"}
        and registry.get("version") == PUBLICATION_PROOF_REGISTRY_VERSION
        and registry.get("kind") == PUBLICATION_PROOF_REGISTRY_KIND
        and isinstance(registry.get("proofs"), dict)
    )
    if not (legacy_registry or compact_registry):
        raise ValueError(f"{spec.filename} publication proof registry is malformed")
    referenced = set()
    uses_legacy = False
    validated_compact_proofs: dict[str, dict] = {}
    validated_compact_rows: set[tuple[str, str]] = set()
    for source_key, row in values.items():
        if not isinstance(row, dict):
            raise ValueError(f"{spec.filename}:{source_key} row must be an object")
        aliases = source.validate_store_source_key(spec, source_key, row)
        for alias in aliases:
            if alias in values and values[alias] != row:
                raise ValueError(
                    f"{spec.filename}:{source_key} alias {alias} does not share one exact row"
                )
        try:
            authority = current_authority(row)
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise ValueError(
                f"{spec.filename}:{source_key} has malformed current authority: {error}"
            ) from error
        if authority is None:
            uses_legacy = True
            if row.get("publication_attestation") is not None:
                raise ValueError(
                    f"{spec.filename}:{source_key} has a publication attestation "
                    "without current authority"
                )
            if baseline is None or not baseline.matches(
                    spec.filename, source_key, row):
                raise ValueError(
                    f"{spec.filename}:{source_key} is not an exact reviewed "
                    "legacy proofless-row baseline entry"
                )
            continue
        attestation_errors = validate_publication_attestation(row)
        if attestation_errors:
            raise ValueError(
                f"{spec.filename}:{source_key} invalid publication attestation: "
                + "; ".join(attestation_errors)
            )
        proof_sha = row["publication_attestation"][
            "publication_proof_sha256"
        ]
        row_cache_key = (proof_sha, tr.sha256_json(row))
        if compact_registry and row_cache_key in validated_compact_rows:
            proof_errors = []
        elif compact_registry and proof_sha in validated_compact_proofs:
            import resolve_trust as resolver  # lazy: resolver imports replay
            proof_errors = _validate_publication_proof_v4_row(
                row, validated_compact_proofs[proof_sha], registry, resolver
            )
        elif compact_registry:
            descriptor = registry["proofs"].get(proof_sha)
            if descriptor is None:
                proof_errors = [
                    "publication proof is absent from the canonical registry"
                ]
            else:
                try:
                    manifest = _load_proof_manifest(proof_sha, registry)
                except (KeyError, TypeError, ValueError, OSError) as error:
                    proof_errors = [
                        f"publication proof manifest is unavailable: {error}"
                    ]
                else:
                    proof_errors = _validate_publication_proof_v4(
                        row, proof_sha, manifest, registry
                    )
                    if not proof_errors:
                        validated_compact_proofs[proof_sha] = manifest
        else:
            proof_errors = _validate_publication_proof(row, registry)
        if proof_errors:
            raise ValueError(
                f"{spec.filename}:{source_key} invalid publication proof: "
                + "; ".join(proof_errors)
            )
        validated_compact_rows.add(row_cache_key)
        referenced.add(proof_sha)

    if baseline is not None:
        missing_legacy_keys = sorted(
            baseline.keys_for(spec.filename) - set(values)
        )
        if missing_legacy_keys:
            raise ValueError(
                f"{spec.filename} is missing reviewed legacy source key(s) "
                f"{missing_legacy_keys[:5]}"
            )
    registered = set(registry["proofs"])
    missing = sorted(referenced - registered)
    extra = sorted(registered - referenced)
    if missing or (legacy_registry and extra):
        raise ValueError(
            f"{spec.filename} publication proof registry does not contain the "
            f"required append-only proof set: missing={missing[:5]} "
            f"extra={extra[:5]}"
        )
    return uses_legacy


def _current_floor_entries(values: dict) -> dict[str, dict[str, str]]:
    import _parking_verdict_source as source  # dependency leaf

    result = {}
    for source_key, row in values.items():
        authority = current_authority(row)
        if authority is not None:
            result[source_key] = source.publication_floor_entry(row, authority)
    return result


def validate_store_floor_image(spec, values: dict, floor) -> None:
    """Require the floor key set to equal every currently proved source key."""
    current_entries = _current_floor_entries(values)
    current_keys = set(current_entries)
    floor_keys = set(floor.keys)
    if floor_keys != current_keys:
        missing = sorted(current_keys - floor_keys)
        downgraded = sorted(floor_keys - current_keys)
        raise ValueError(
            f"{spec.filename} publication floor/current key mismatch: "
            f"missing={missing[:5]} downgraded={downgraded[:5]}"
        )


def build_publication_floor_after_bytes(
        spec, before_floor_bytes: bytes, after_store_bytes: bytes) -> bytes:
    """Return append-only union(previous floor, every current after-image key)."""
    import _parking_verdict_source as source  # dependency leaf

    floor = source.parse_publication_floor(before_floor_bytes, spec)
    values = json.loads(after_store_bytes)
    if not isinstance(values, dict):
        raise ValueError("publication store after-image must be an object")
    current_entries = _current_floor_entries(values)
    downgraded = sorted(set(floor.keys) - set(current_entries))
    if downgraded:
        raise ValueError(
            f"{spec.filename} floor-marked key may not lose current authority: "
            f"{downgraded[:5]}"
        )
    document = source.extend_publication_floor(floor, current_entries)
    return source.publication_floor_json_bytes(document)


def validate_publication_floor_transition(
        spec, before_floor_bytes: bytes, after_floor_bytes: bytes) -> None:
    """Require every old floor entry to survive byte-for-byte in the after-image."""
    import _parking_verdict_source as source  # dependency leaf

    before = source.parse_publication_floor(before_floor_bytes, spec)
    after = source.parse_publication_floor(after_floor_bytes, spec)
    if before.store_name != after.store_name:
        raise ValueError("publication floor store identity changed")
    after_entries = after.entries_by_key
    for source_key, entry in before.entries_by_key.items():
        if after_entries.get(source_key) != entry:
            raise ValueError(
                f"{spec.filename}:{source_key} publication floor entry is not append-only"
            )


@contextmanager
def validated_store_snapshot(
        data_dir: Path, builder, *, output_path: Path | None = None,
        output_exclusive: bool = False,
        additional_resource_modes=()):
    """Yield a lease-scoped rooted snapshot under one sorted global lock set."""
    import _parking_verdict_source as source  # dependency leaf
    import merge_drafts as merger  # lazy: merge imports resolver/replay

    data_dir = Path(data_dir).resolve()
    additional_resource_modes = tuple(additional_resource_modes)
    specs = _configured_store_specs(builder)
    stores = tuple(spec.filename for spec in specs)
    root_path, root_pin, baseline_path = _publication_root_settings(
        builder, specs
    )
    dossier_resource = tr.dossier_resource_path(data_dir)

    paths = {}
    source_resources = [root_path, dossier_resource, baseline_path]
    for store in stores:
        store_path = data_dir / store
        proof_path = _publication_proof_path(data_dir, store)
        floor_path = _publication_floor_path(data_dir, store)
        transaction = merger._publication_transaction_paths(
            store_path, proof_path, "snapshot", floor_path=floor_path
        )
        journal = transaction["journal"]
        paths[store] = (store_path, proof_path, floor_path, journal)
        source_resources.extend((store_path, proof_path, floor_path, journal))

    resource_modes = [
        (resource, fcntl.LOCK_SH) for resource in source_resources
    ]
    resource_modes.extend(
        (Path(resource), mode)
        for resource, mode in additional_resource_modes
    )
    if output_path is not None:
        resource_modes.append((
            Path(output_path),
            fcntl.LOCK_EX if output_exclusive else fcntl.LOCK_SH,
        ))

    with trusted_fs.locked_resources(
            data_dir, resource_modes, tr.resource_lock_path) as lock_entries:
        if output_path is not None:
            canonical_output = Path(output_path).expanduser().resolve()
            canonical_inputs = {
                Path(resource).expanduser().resolve()
                for resource in source_resources
            }
            canonical_additional = {
                Path(resource).expanduser().resolve()
                for resource, _mode in additional_resource_modes
            }
            lock_paths = {lock_path for lock_path, _resource, _mode in lock_entries}
            if (canonical_output in canonical_inputs
                    or canonical_output in canonical_additional
                    or canonical_output in lock_paths):
                raise ValueError(
                    "parking verdict output collides with a source or lock path"
                )

        # A journal is a recovery boundary. Check every one before reading any
        # canonical target, including the root.
        for store in stores:
            journal = paths[store][3]
            if os.path.lexists(journal):
                raise ValueError(
                    f"{store}: live publication journal requires recovery before "
                    "sidecar build or replay"
                )

        # The exact external pin is checked before any mutable corpus bytes are
        # interpreted. All later image and semantic validation remains under
        # this same globally sorted lock lease.
        root_bytes = trusted_fs.read_regular_bytes(root_path)
        root = source.parse_publication_trust_root(
            root_bytes, specs, root_pin, baseline_path.name
        )
        store_bytes = {}
        proof_bytes = {}
        floor_bytes = {}
        for store in stores:
            store_path, proof_path, floor_path, _journal = paths[store]
            store_bytes[store] = merger.resolver._read_bytes_nofollow(store_path)
            proof_bytes[store] = _read_publication_registry_bytes(proof_path)
            floor_bytes[store] = merger.resolver._read_bytes_nofollow(floor_path)
        baseline_bytes = trusted_fs.read_regular_bytes(baseline_path)
        dossier_bytes = _capture_dossier_snapshot(data_dir, merger.resolver)

        source.validate_publication_trust_root_image(
            root, specs, store_bytes, proof_bytes, floor_bytes,
            baseline_path.name, baseline_bytes, dossier_bytes,
        )
        generation_source_names = _validate_dossier_generation_snapshot(
            data_dir, merger.resolver, dossier_bytes
        )
        if output_path is not None:
            canonical_output = Path(output_path).expanduser().resolve()
            if canonical_output in {
                (data_dir / name).resolve()
                for name in generation_source_names
            }:
                raise ValueError(
                    "parking verdict output collides with a generation source"
                )

        # Exact root-image validation deliberately precedes dossier, baseline,
        # proof, floor, and row semantics.
        source.dossier_indexes_from_bytes(dossier_bytes)
        baseline = source.parse_legacy_baseline(
            baseline_bytes, specs,
            getattr(builder, "LEGACY_ROW_BASELINE_SHA256", None),
        )
        uses_legacy = False
        for spec in specs:
            path = data_dir / spec.filename
            try:
                values = json.loads(store_bytes[spec.filename])
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                raise ValueError(f"{path} is not valid JSON: {error}") from error
            registry = (
                _load_publication_proofs(
                    data_dir, spec.filename, proof_bytes[spec.filename]
                )
                if proof_bytes[spec.filename] is not None
                else _empty_publication_registry()
            )
            uses_legacy = (
                validate_store_proof_image(spec, values, registry, baseline)
                or uses_legacy
            )
            floor = source.parse_publication_floor(
                floor_bytes[spec.filename], spec
            )
            validate_store_floor_image(spec, values, floor)

        if uses_legacy:
            baseline.require_exact_dossiers(dossier_bytes)

        snapshot = source._issue_validated_snapshot(
            data_dir, stores, store_bytes, proof_bytes, floor_bytes,
            dossier_bytes,
        )
        try:
            yield snapshot
        finally:
            source._revoke_validated_snapshot(snapshot)


def publication_store_policy(store_name: str):
    """Return the production key/baseline policy, or current-only OSM policy."""
    import _parking_verdict_source as source  # dependency leaf
    import merge_drafts as merger  # fully initialized when terminal publication calls

    builder = _load_builder()
    specs = _configured_store_specs(builder)
    for spec in specs:
        if spec.filename == store_name:
            baseline = _load_legacy_baseline(
                builder, specs, merger.resolver._read_bytes_nofollow
            )
            return spec, baseline
    return source.StoreSpec(store_name, source.OSM_KEY, None, None), None


def publication_baseline_path(store_name: str) -> Path | None:
    """Return the pinned baseline resource used by a configured store."""
    builder = _load_builder()
    if store_name not in _configured_store_names(builder):
        return None
    configured = getattr(builder, "LEGACY_ROW_BASELINE_PATH", None)
    return None if configured is None else Path(configured).resolve()


def validate_monotonic_publication_transition(
        store_name: str, before_bytes: bytes | None, after_bytes: bytes) -> None:
    """Forbid deletion or downgrade of any already-current source key."""
    if before_bytes is None:
        return
    before = json.loads(before_bytes)
    after = json.loads(after_bytes)
    if not isinstance(before, dict) or not isinstance(after, dict):
        raise ValueError("publication transition store images must be objects")
    for source_key, old_row in before.items():
        if not isinstance(old_row, dict):
            raise ValueError(f"{store_name}:{source_key} before-row is malformed")
        try:
            old_authority = current_authority(old_row)
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise ValueError(
                f"{store_name}:{source_key} before-authority is malformed: {error}"
            ) from error
        if old_authority is None:
            continue
        new_row = after.get(source_key)
        try:
            new_authority = (
                current_authority(new_row)
                if isinstance(new_row, dict) else None
            )
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise ValueError(
                f"{store_name}:{source_key} after-authority is malformed: {error}"
            ) from error
        if new_authority is None:
            raise ValueError(
                f"{store_name}:{source_key} current publication authority may not "
                "be removed or downgraded"
            )


@contextmanager
def _loaded_corpus_snapshot(
        data_dir: Path = DATA, sidecar_path: Path = SIDECAR, *,
        report_path: Path | None = None, additional_resource_modes=()):
    """Yield a loaded corpus while its complete sorted resource lease is held.

    Shared store, sidecar, and replay-input locks remain held through the
    caller's report construction and optional installation. Builder and replay
    parse only captured dossier bytes.
    """
    import merge_drafts as merger  # lazy: merge imports resolver/replay

    builder = _load_builder()
    data_dir = Path(data_dir)
    sidecar_path = Path(sidecar_path)
    resource_modes = list(additional_resource_modes)
    snapshot_output = sidecar_path
    output_exclusive = False
    if report_path is not None:
        snapshot_output = Path(report_path)
        output_exclusive = True
        resource_modes.append((sidecar_path, fcntl.LOCK_SH))
    with validated_store_snapshot(
            data_dir, builder, output_path=snapshot_output,
            output_exclusive=output_exclusive,
            additional_resource_modes=resource_modes) as snapshot:
        specs = _configured_store_specs(builder)
        store_names = tuple(spec.filename for spec in specs)
        store_documents = snapshot.documents_for(data_dir, store_names)
        store_bytes_by_name = snapshot.store_bytes_by_name
        dossier_bytes_by_name = snapshot.dossier_bytes_by_name
        by_fid, by_osm = snapshot.dossier_indexes_for(
            data_dir, store_names
        )
        document, notes, folded = builder._build_with_snapshot(
            str(data_dir), snapshot
        )
        if notes:
            raise ValueError(
                "source compiler rejected entries: " + " | ".join(notes)
            )
        canonical_bytes = builder.serialize(document).encode("utf-8")
        committed_bytes = merger.resolver._read_bytes_nofollow(sidecar_path)
        if committed_bytes != canonical_bytes:
            raise ValueError(
                f"{sidecar_path} is not byte-exact canonical output of the authoritative stores"
            )
        committed = json.loads(committed_bytes)
        if not isinstance(committed, dict):
            raise ValueError(f"{sidecar_path} must contain an object")

        claimed: dict[str, str] = {}
        signatures: dict[str, dict] = {}
        items: dict[str, dict] = {}
        source_rows = 0
        corpus_hash = hashlib.sha256()

        for spec in specs:
            store = spec.filename
            raw = store_bytes_by_name[store]
            corpus_hash.update(store.encode("utf-8") + b"\0" + raw + b"\0")
            values = store_documents[store]
            for source_key, row in values.items():
                if (not isinstance(row, dict)
                        or row.get("verdict") not in ("KEEP", "DROP", "REVIEW")):
                    continue
                source_rows += 1
                normalized = builder._normalize_store_record(
                    spec, source_key, row, by_fid, by_osm
                )
                aliases = list(normalized.aliases)
                published_attestation = None
                validated_receipt = None
                if current_authority(row) is not None:
                    published_attestation = row[
                        "publication_attestation"
                    ]["attestation_sha256"]
                    bound = authority_wrapper(row)
                    if bound is not None:
                        validated_receipt = bound[1]["authority_receipt_sha256"]
                holders = sorted({
                    claimed[alias] for alias in aliases if alias in claimed
                })
                duplicate = holders[0] if holders else None
                if duplicate is not None:
                    incompatible = [
                        holder for holder in holders
                        if signatures[holder] != normalized.fold_signature
                    ]
                    if incompatible:
                        raise ValueError(
                            f"{store}:{source_key} has different complete "
                            "decision/authority provenance from folded cluster "
                            f"holder(s) {incompatible}"
                        )
                    for holder in holders[1:]:
                        for alias, owner in list(claimed.items()):
                            if owner == holder:
                                claimed[alias] = duplicate
                        del items[holder]
                        del signatures[holder]
                    for alias in aliases:
                        claimed[alias] = duplicate
                    continue
                key = aliases[0]
                if key not in document["lots"]:
                    raise ValueError(
                        f"{store}:{source_key} compiled key {key} is absent from the sidecar"
                    )
                item = {
                    "key": key,
                    "source_key": source_key,
                    "store": store,
                    "area": row.get("area") or normalized.facility.get("_slug"),
                    "row": row,
                    "validated_authority_receipt_sha256": validated_receipt,
                    "published_authority_attestation_sha256": published_attestation,
                    "published": document["lots"][key],
                }
                items[key] = item
                signatures[key] = normalized.fold_signature
                for alias in aliases:
                    claimed[alias] = key

        if set(items) != set(document.get("lots") or {}):
            missing = sorted(set(document.get("lots") or {}) - set(items))
            extra = sorted(set(items) - set(document.get("lots") or {}))
            raise ValueError(
                f"source/sidecar identity mismatch: missing={missing[:5]} "
                f"extra={extra[:5]}"
            )
        if source_rows - len(items) != len(folded):
            raise ValueError(
                f"fold accounting mismatch: {source_rows} source - {len(items)} "
                f"unique != {len(folded)} folds"
            )
        for dossier_name in sorted(dossier_bytes_by_name):
            corpus_hash.update(
                b"dossier\0" + dossier_name.encode("utf-8") + b"\0"
                + dossier_bytes_by_name[dossier_name] + b"\0"
            )

        metadata = {
            "corpus_sha256": corpus_hash.hexdigest(),
            "source_rows": source_rows,
            "unique_clusters": len(items),
            "folded_duplicates": len(folded),
            "sidecar_sha256": hashlib.sha256(committed_bytes).hexdigest(),
        }
        loaded = (
            [items[key] for key in sorted(items)], metadata, document
        )
        yield loaded


def load_corpus(
        data_dir: Path = DATA,
        sidecar_path: Path = SIDECAR) -> tuple[list[dict], dict, dict]:
    """Load one canonical source row per builder-defined lot cluster."""
    with _loaded_corpus_snapshot(data_dir, sidecar_path) as loaded:
        return loaded


def load_work_area_locked(tmp: Path, slug: str, generation_capture,
                          packets_override: dict[int, dict] | None = None,
                          packet_source_bytes: bytes | None = None,
                          drafts_override: list[dict] | None = None,
                          draft_source_bytes: dict[str, bytes] | None = None) -> tuple[list[dict], dict, dict]:
    """Load work data while the caller holds inventory and area leases."""
    if tr.area_slug(slug) != slug:
        raise ValueError("work replay requires a canonical separator-free area slug")
    source_generation = dossier_output.portable_source_generation(
        generation_capture, slug
    )
    authority_journal = tr.authority_journal_path(tmp, slug)
    if authority_journal.exists():
        raise ValueError(
            "RECOVERY_REQUIRED: live human-authority journal blocks work replay"
        )
    packet_path = tmp / f"{slug}_packets.json"
    if packet_source_bytes is None:
        packet_source_bytes = packet_path.read_bytes()
    raw_packets = json.loads(packet_source_bytes)
    if not isinstance(raw_packets, dict):
        raise ValueError(f"{packet_path} must contain a dict")
    packets: dict[int, dict] = {}
    packet_generations = set()
    for key, packet in raw_packets.items():
        if not isinstance(packet, dict):
            raise ValueError(f"{packet_path}: packet {key!r} is not an object")
        fid = packet.get("fid")
        if type(fid) is not int or str(fid) != str(key):
            raise ValueError(f"{packet_path}: packet key/fid mismatch at {key!r}")
        if packet.get("area") != slug:
            raise ValueError(
                f"{packet_path}: packet {fid} area {packet.get('area')!r} does not match {slug!r}"
            )
        if fid in packets:
            raise ValueError(f"{packet_path}: duplicate fid {fid}")
        try:
            packet_generation = dossier_output.validate_source_generation(
                packet.get("source_generation"), slug
            )
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"{packet_path}: packet {fid} source_generation is invalid: {error}"
            ) from error
        packet_generations.add(tr.sha256_json(packet_generation))
        if packet_generation != source_generation:
            raise ValueError(
                f"{packet_path}: packet {fid} generation differs from current manifest"
            )
        packets[fid] = packet
    if not packets or len(packet_generations) != 1:
        raise ValueError(
            f"{packet_path}: packets must have exactly one source_generation"
        )
    if packets_override is not None:
        if set(packets_override) != set(packets):
            raise ValueError(f"{slug}: frozen packet override fid set differs from source")
        for fid, snapshot in packets_override.items():
            source_payload = {key: value for key, value in packets[fid].items() if key != "tiles"}
            snapshot_payload = {key: value for key, value in snapshot.items() if key != "tiles"}
            if source_payload != snapshot_payload:
                raise ValueError(f"{slug}: frozen packet override payload differs for fid {fid}")
        packets = copy.deepcopy(packets_override)
    drafts = (copy.deepcopy(drafts_override)
              if drafts_override is not None else load_canonical_drafts(tmp, slug))
    if drafts is None:
        raise ValueError(f"no canonical verdict drafts for {slug}")
    seen: set[int] = set()
    items = []
    corpus_hash = hashlib.sha256(packet_source_bytes)
    if draft_source_bytes is not None:
        for name, source_bytes in sorted(draft_source_bytes.items()):
            corpus_hash.update(name.encode("utf-8") + b"\0" + source_bytes)
    else:
        for draft_path in canonical_draft_files(tmp, slug):
            corpus_hash.update(draft_path.name.encode("utf-8") + b"\0" + draft_path.read_bytes())
    for row in drafts:
        fid = row.get("fid") if isinstance(row, dict) else None
        if type(fid) is not int or fid in seen:
            raise ValueError(f"{slug}: invalid or duplicate draft fid {fid!r}")
        packet = packets.get(fid)
        if packet is None:
            raise ValueError(f"{slug}: draft fid {fid} has no packet")
        forbidden_persistence = sorted(
            field for field in (
                "src", "judged", "lat", "lon", "rings", "name", "_key",
                "reason", "publication_attestation",
            ) if field in row
        )
        if forbidden_persistence:
            raise ValueError(
                f"{slug}: fid {fid}: draft contains host-owned persistence fields "
                f"{forbidden_persistence}"
            )
        identity_errors, _ = validate_verdict_row(row, packet=packet)
        identity_errors = [error for error in identity_errors if "does not match the current packet" in error]
        if identity_errors:
            raise ValueError(f"{slug}: fid {fid}: " + "; ".join(identity_errors))
        validated_receipt = None
        bound_authority = authority_wrapper(row)
        if bound_authority is not None:
            receipt_errors = validate_authority_receipt(row, tmp)
            if not receipt_errors:
                receipt_sha = bound_authority[1]["authority_receipt_sha256"]
                receipt = json.loads(
                    authority_receipt_path(tmp, slug, receipt_sha).read_text()
                )
                import merge_drafts as merger  # lazy: merge imports resolver/replay
                receipt_errors.extend(
                    merger._validate_receipt_source(receipt, row, packet)
                )
            if receipt_errors:
                raise ValueError(
                    f"{slug}: fid {fid}: invalid authority receipt: "
                    + "; ".join(receipt_errors)
                )
            validated_receipt = bound_authority[1]["authority_receipt_sha256"]
        osm = list(row.get("osm") or [])
        if not osm:
            raise ValueError(f"{slug}: fid {fid} has no OSM identity")
        key = osm[0]
        if any(key == item["key"] or set(osm) & set(item["row"].get("osm") or []) for item in items):
            raise ValueError(f"{slug}: duplicate OSM cluster in primary drafts at fid {fid}")
        seen.add(fid)
        published = {
            "verdict": row.get("verdict"),
            "area": slug,
            "src": "shadow-primary",
        }
        items.append({
            "key": key,
            "source_key": str(fid),
            "store": f"work:{slug}",
            "area": slug,
            "row": row,
            "packet": packet,
            "validated_authority_receipt_sha256": validated_receipt,
            "published": published,
        })
    missing = sorted(set(packets) - seen)
    if missing:
        raise ValueError(f"{slug}: primary drafts missing packet fids {missing}")
    document = {"version": 1, "lots": {item["key"]: item["published"] for item in items}}
    metadata = {
        "corpus_sha256": corpus_hash.hexdigest(),
        "source_rows": len(items),
        "unique_clusters": len(items),
        "folded_duplicates": 0,
        "sidecar_sha256": None,
        "work_area": slug,
    }
    if authority_journal.exists():
        raise ValueError(
            "RECOVERY_REQUIRED: human-authority recovery began during work replay"
        )
    return sorted(items, key=lambda item: item["key"]), metadata, document


def load_work_area(tmp: Path, slug: str,
                   packets_override: dict[int, dict] | None = None,
                   packet_source_bytes: bytes | None = None,
                   drafts_override: list[dict] | None = None,
                   draft_source_bytes: dict[str, bytes] | None = None) -> tuple[list[dict], dict, dict]:
    """Load a work area under inventory-shared then area-shared locks."""
    inventory_resource = tr.dossier_resource_path(tmp)
    with trusted_fs.locked_resources(
            tmp, [(inventory_resource, fcntl.LOCK_SH)],
            tr.resource_lock_path):
        area_lock_path = tr.area_lock_path(tmp, slug)
        with trusted_fs.open_lock_file(area_lock_path) as area_lock:
            fcntl.flock(area_lock.fileno(), fcntl.LOCK_SH)
            generation_capture = dossier_output.capture_generation_locked(
                tmp, slug
            )
            return load_work_area_locked(
                tmp, slug, generation_capture,
                packets_override=packets_override,
                packet_source_bytes=packet_source_bytes,
                drafts_override=drafts_override,
                draft_source_bytes=draft_source_bytes,
            )


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _lexical_absolute(path: Path) -> Path:
    expanded = Path(path).expanduser()
    if ".." in expanded.parts:
        raise ValueError("--out may not contain traversal components")
    return Path(os.path.abspath(os.fspath(expanded)))


def _path_views(path: Path) -> tuple[Path, Path]:
    lexical = _lexical_absolute(path)
    return lexical, lexical.resolve(strict=False)


def _overlaps_directory(path: Path, directory: Path) -> bool:
    path_views = _path_views(path)
    directory_views = _path_views(directory)
    return any(
        (_is_within(path_view, directory_view)
         or _is_within(directory_view, path_view))
        for path_view in path_views
        for directory_view in directory_views
    )


def _same_path(first: Path, second: Path) -> bool:
    return any(
        first_view == second_view
        for first_view in _path_views(first)
        for second_view in _path_views(second)
    )


def _reject_symlink_components(path: Path) -> None:
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        try:
            value = os.lstat(current)
        except FileNotFoundError:
            break
        if stat.S_ISLNK(value.st_mode):
            raise ValueError(f"--out may not use symlinked path components: {current}")


def validate_report_path(
        path: Path, *, data_dir: Path | None = None,
        sidecar_path: Path | None = None, tmp_dir: Path | None = None,
        ledger_path: Path | None = None,
        groundtruth_path: Path | None = None) -> Path:
    """Return one lexical, symlink-free target below the report-only root."""
    target = _lexical_absolute(path)
    report_root = _lexical_absolute(REPORTS)
    if target.suffix.lower() != ".json":
        raise ValueError("--out must end in .json")
    try:
        relative = target.relative_to(report_root)
    except ValueError as error:
        raise ValueError(
            "--out is allowed only under scripts/parking-adjud/reports/"
        ) from error
    if not relative.parts:
        raise ValueError("--out must be a strict descendant of the report root")
    for component in relative.parts:
        lowered = component.lower()
        if (component.startswith(".")
                or lowered in {"lock", "locks"}
                or lowered.endswith(".lock")
                or ".lock." in lowered):
            raise ValueError("--out may not use dot or lock namespaces")

    _reject_symlink_components(report_root)
    _reject_symlink_components(target)
    resolved_root = report_root.resolve(strict=False)
    resolved_target = target.resolve(strict=False)
    if (resolved_target == resolved_root
            or not _is_within(resolved_target, resolved_root)):
        raise ValueError(
            "--out resolved path must remain below scripts/parking-adjud/reports/"
        )
    try:
        target_stat = os.lstat(target)
    except FileNotFoundError:
        target_stat = None
    if target_stat is not None and not stat.S_ISREG(target_stat.st_mode):
        raise ValueError("--out existing target must be a regular file")

    protected_directories = [
        (WORK, "work"),
        (DATA, "data"),
        (PUBLIC, "public"),
        (Path(data_dir) if data_dir is not None else DATA, "--data-dir"),
    ]
    if tmp_dir is not None:
        protected_directories.append((Path(tmp_dir), "--tmp"))
    for directory, label in protected_directories:
        if _overlaps_directory(target, directory):
            raise ValueError(f"--out may not be within {label}")

    protected_files = [
        Path(sidecar_path) if sidecar_path is not None else SIDECAR,
        Path(ledger_path) if ledger_path is not None else DATA / "calibration.json",
        (Path(groundtruth_path) if groundtruth_path is not None
         else DATA / "groundtruth.json"),
    ]
    if any(_same_path(target, protected) for protected in protected_files):
        raise ValueError("--out collides with a replay input")
    return target


def _install_report_locked(target: Path, text: str) -> None:
    if not isinstance(text, str):
        raise TypeError("report text must be a string")
    trusted_fs.atomic_write_bytes(target, text.encode("utf-8"))


def write_report(path: Path, text: str) -> Path:
    """Safely install a standalone report under its canonical EX lock."""
    target = validate_report_path(path)
    with trusted_fs.locked_resources(
            REPORTS, ((target, fcntl.LOCK_EX),),
            tr.resource_lock_path):
        _install_report_locked(target, text)
    return target


def _build_report_text(items, ledger, groundtruth, sidecar, corpus,
                       *, include_items: bool) -> tuple[dict, str]:
    report = trust_engine.build_report(
        items, ledger, groundtruth, sidecar, corpus,
        include_items=include_items,
    )
    text = json.dumps(
        report, indent=2, sort_keys=True, ensure_ascii=False
    ) + "\n"
    return report, text


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", type=Path, default=DATA)
    parser.add_argument("--sidecar", type=Path, default=SIDECAR)
    parser.add_argument("--work-area", metavar="SLUG",
                        help="route one completed primary draft from --tmp instead of committed stores")
    parser.add_argument("--tmp", type=Path,
                        help="packet/draft directory for --work-area")
    parser.add_argument("--ledger", type=Path, default=DATA / "calibration.json")
    parser.add_argument("--groundtruth", type=Path, default=DATA / "groundtruth.json")
    parser.add_argument("--format", choices=("json", "summary"), default="json")
    parser.add_argument("--include-items", action="store_true",
                        help="include one machine route record per canonical lot")
    parser.add_argument("--out", type=Path,
                        help="atomically write JSON under the ignored reports/ root")
    args = parser.parse_args(argv)

    try:
        if bool(args.work_area) != bool(args.tmp):
            raise ValueError("--work-area and --tmp must be supplied together")
        report_target = (
            validate_report_path(
                args.out, data_dir=args.data_dir, sidecar_path=args.sidecar,
                tmp_dir=args.tmp, ledger_path=args.ledger,
                groundtruth_path=args.groundtruth,
            )
            if args.out is not None else None
        )

        if args.work_area:
            global_resource_modes = [
                (args.ledger, fcntl.LOCK_SH),
                (tr.dossier_resource_path(args.tmp), fcntl.LOCK_SH),
            ]
            if report_target is not None:
                global_resource_modes.append((report_target, fcntl.LOCK_EX))
            with trusted_fs.locked_resources(
                    PARKING_ADJUD, global_resource_modes,
                    tr.resource_lock_path):
                import resolve_trust as resolver  # lazy: resolver imports replay

                lock_path = tr.area_lock_path(args.tmp, args.work_area)
                with resolver._open_lock_file(lock_path) as area_lock:
                    fcntl.flock(area_lock.fileno(), fcntl.LOCK_SH)
                    try:
                        ledger = _load_json(args.ledger, dict)
                        generation_capture = dossier_output.capture_generation_locked(
                            args.tmp, args.work_area
                        )
                        items, corpus, sidecar = load_work_area_locked(
                            args.tmp, args.work_area, generation_capture
                        )
                        report, text = _build_report_text(
                            items, ledger, {}, sidecar, corpus,
                            include_items=True,
                        )
                        if report_target is not None:
                            _install_report_locked(report_target, text)
                    finally:
                        fcntl.flock(area_lock.fileno(), fcntl.LOCK_UN)
        else:
            replay_input_modes = [
                (args.ledger, fcntl.LOCK_SH),
                (args.groundtruth, fcntl.LOCK_SH),
            ]
            with _loaded_corpus_snapshot(
                    args.data_dir, args.sidecar,
                    report_path=report_target,
                    additional_resource_modes=replay_input_modes) as loaded:
                items, corpus, sidecar = loaded
                ledger = _load_json(args.ledger, dict)
                groundtruth = _load_json(args.groundtruth, dict)
                report, text = _build_report_text(
                    items, ledger, groundtruth, sidecar, corpus,
                    include_items=args.include_items,
                )
                if report_target is not None:
                    _install_report_locked(report_target, text)

        if report_target is not None:
            print(f"wrote {report_target}")
        if args.format == "summary":
            print(trust_engine.summary_text(report))
        elif report_target is None:
            sys.stdout.write(text)
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"trust replay failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
