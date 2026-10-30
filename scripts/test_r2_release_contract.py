#!/usr/bin/env python3
"""Offline hostile tests for the versioned R2 release/quarantine contract."""
from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import importlib.util
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from r2_release_contract import (  # noqa: E402
    BUCKET,
    AWS_CLI_ARCHIVE_SHA256,
    AWS_CLI_VERSION,
    CI_PRODUCER_GRAPH,
    CONTRACT_VERSION,
    GLOBAL_OBJECTS,
    INVENTORY_FAULT_NAMES,
    MAX_INVENTORY_PAGES,
    MOLS_AREA_ID,
    MOLS_FORWARD_KEYS,
    MOLS_MUTABLE_GLOBAL_KEYS,
    MOLS_OBJECT_KEYS,
    MOLS_REPOSITORY_PATHS,
    MOLS_SEMANTIC_GLOBAL_KEYS,
    PRODUCTION_PRODUCER_BOUNDARIES,
    ConditionalRequestRefused,
    InventoryError,
    KeyClass,
    ManifestError,
    TerminalState,
    audit_repository_contract,
    build_mols_global_delta,
    build_mols_index_delta,
    build_producer_population,
    build_release_preflight,
    build_rollback_manifest,
    build_scoped_release_preflight,
    build_quarantine_plan,
    build_upload_manifest as build_upload_manifest_contract,
    canonical_sha256,
    classify_key,
    derive_production_producer_graph,
    decode_mols_release_input_receipt,
    execute_quarantine as execute_quarantine_contract,
    execute_release as execute_release_contract,
    execute_scoped_release,
    fetch_complete_inventory,
    identity_ref,
    inventory_objects,
    local_object as local_object_contract,
    make_approval_assertion,
    make_mols_release_input_receipt,
    normalize_sha256_checksum,
    provider_identity_attestation,
    provider_tool_attestation,
    quarantine_key,
    reconcile_quarantine_write_ahead,
    reconcile_release_write_ahead,
    reconcile_scoped_release_write_ahead,
    release_boundaries,
    strict_json_loads,
    validate_inventory_receipt,
    validate_quarantine_plan,
    validate_quarantine_receipt,
    validate_release_preflight,
    validate_release_receipt,
    validate_rollback_manifest,
    validate_scoped_release_preflight,
    validate_scoped_release_receipt,
    validate_upload_manifest,
)

MINIMAL_SUBPROCESS_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/tmp",
    "LC_ALL": "C",
    "LANG": "C",
    "PYTHONDONTWRITEBYTECODE": "1",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_OPTIONAL_LOCKS": "0",
}

CONTRACT_TEST_SENTINEL = "RR2-ORIGINAL-CLAUSES-R2-V1"
SHA = "a" * 64
SHA2 = "b" * 64
COMMIT = "1" * 40
TREE = "2" * 40
APPROVAL = "sha256:" + "3" * 64
ANCHOR = "sha256:" + "4" * 64
ACCOUNT_ID = "0123456789abcdef0123456789abcdef"
ENDPOINT_URL = "https://0123456789abcdef0123456789abcdef.r2.example.invalid"
ACCOUNT = identity_ref("trekdex-r2-account-v1", ACCOUNT_ID)
ENDPOINT = identity_ref("trekdex-r2-endpoint-v1", ENDPOINT_URL)
WORKFLOW = "7" * 64
APPROVAL_KEY = "test-only-root-approval-key-32-bytes-minimum"


def etag(seed: str) -> str:
    return hashlib.md5(seed.encode(), usedforsecurity=False).hexdigest()


def remote(key: str, payload: bytes | None = None) -> dict[str, Any]:
    body = payload if payload is not None else key.encode()
    return {
        "Key": key,
        "ETag": f'"{hashlib.md5(body, usedforsecurity=False).hexdigest()}"',
        "Size": len(body),
        "ChecksumSHA256": hashlib.sha256(body).hexdigest(),
    }


def page(
    contents: list[dict[str, Any]],
    *,
    token: str | None = None,
    next_token: str | None = None,
    truncated: bool = False,
    bucket: str = BUCKET,
) -> dict[str, Any]:
    document: dict[str, Any] = {
        "Name": bucket,
        "IsTruncated": truncated,
        "KeyCount": len(contents),
        "Contents": contents,
    }
    if token is not None:
        document["ContinuationToken"] = token
    if next_token is not None:
        document["NextContinuationToken"] = next_token
    return document


class PageSource:
    def __init__(self, pages: dict[str | None, Any], fault: BaseException | None = None):
        self.pages = pages
        self.fault = fault
        self.calls: list[str | None] = []

    def __call__(self, token: str | None) -> Any:
        self.calls.append(token)
        if self.fault is not None:
            raise self.fault
        return copy.deepcopy(self.pages[token])


def receipt_for(
    keys: list[str] | None = None,
    *,
    area_registry: set[str] | None = None,
) -> dict[str, Any]:
    selected = keys or [*GLOBAL_OBJECTS, "alpha-trail-az.json", "silhouettes/alpha-trail-az.json"]
    if area_registry is None:
        area_registry = {
            key.removeprefix("silhouettes/").removesuffix(".json")
            for key in selected
            if key not in GLOBAL_OBJECTS and key.endswith(".json")
            and ("/" not in key or key.startswith("silhouettes/"))
        }
    midpoint = max(1, len(selected) // 2)
    pages = {
        None: page([remote(key) for key in selected[:midpoint]], next_token="opaque-2", truncated=True),
        "opaque-2": page([remote(key) for key in selected[midpoint:]], token="opaque-2"),
    }
    return fetch_complete_inventory(
        PageSource(pages),
        bucket=BUCKET,
        account_identity_ref=ACCOUNT,
        endpoint_identity_ref=ENDPOINT,
        authoritative_denominator=len(selected),
        authoritative_area_ids=area_registry,
        anchor_state="ASSERTED_PENDING_APPROVAL",
        anchor_ref=ANCHOR,
    )


def local_object(key: str, path: Path, **kwargs: Any) -> dict[str, Any]:
    area_id = key.removeprefix("silhouettes/").removesuffix(".json")
    registry = None if key in GLOBAL_OBJECTS else {area_id}
    return local_object_contract(
        key, path, authoritative_area_ids=registry, **kwargs
    )


def build_upload_manifest(
    inventory: dict[str, Any],
    local_rows: list[dict[str, Any]],
    **kwargs: Any,
) -> dict[str, Any]:
    registry = set(inventory["area_registry_ids"])
    published = {
        row["key"].removesuffix(".json")
        for row in local_rows
        if row["key"] not in GLOBAL_OBJECTS and not row["key"].startswith("silhouettes/")
    }
    population = build_producer_population(
        local_rows,
        source_tree=kwargs["source_tree"],
        authoritative_area_ids=registry,
        published_area_ids=published,
    )
    return build_upload_manifest_contract(
        inventory, local_rows, population, **kwargs
    )


def execute_release(manifest: dict[str, Any], writer: Any, **kwargs: Any) -> dict[str, Any]:
    kwargs.setdefault("approval_assertion", make_approval_assertion(
        manifest, operation="RELEASE", key=APPROVAL_KEY,
        authorization_nonce="release-test-approval",
    ))
    kwargs.setdefault("approval_key", APPROVAL_KEY)
    kwargs.setdefault("execution_id", "release-test-execution")
    return execute_release_contract(manifest, writer, **kwargs)


def execute_quarantine(plan: dict[str, Any], store: Any, **kwargs: Any) -> dict[str, Any]:
    kwargs.setdefault("approval_assertion", make_approval_assertion(
        plan, operation="QUARANTINE", key=APPROVAL_KEY,
        authorization_nonce="quarantine-test-approval",
    ))
    kwargs.setdefault("approval_key", APPROVAL_KEY)
    kwargs.setdefault("execution_id", "quarantine-test-execution")
    return execute_quarantine_contract(plan, store, **kwargs)


class FakeStore:
    """In-memory exact-current store whose call log exposes every effect."""

    def __init__(self, objects: dict[str, dict[str, Any]]):
        self.objects = copy.deepcopy(objects)
        self.calls: list[tuple[Any, ...]] = []
        self.fault: str | None = None

    def attest_identity(self) -> dict[str, Any]:
        return provider_identity_attestation(ACCOUNT_ID, ENDPOINT_URL)

    def head(self, key: str) -> dict[str, Any] | None:
        self.calls.append(("head", key))
        value = self.objects.get(key)
        return None if value is None else copy.deepcopy(value)

    def copy_no_clobber(
        self,
        source: str,
        destination: str,
        *,
        source_preimage: dict[str, Any],
        attempt_id: str,
    ) -> dict[str, Any]:
        self.calls.append((
            "copy_no_clobber", source, destination,
            copy.deepcopy(source_preimage), attempt_id,
        ))
        if self.fault == "copy":
            raise RuntimeError("copy fault")
        if destination in self.objects:
            raise RuntimeError("destination collision")
        current = self.objects.get(source)
        if current != source_preimage:
            raise RuntimeError("source preimage changed")
        copied = copy.deepcopy(current)
        if self.fault == "corrupt_copy":
            copied["sha256"] = "f" * 64
        self.objects[destination] = copied
        return {
            "transaction_id": "copy-tx-" + hashlib.sha256(
                destination.encode()
            ).hexdigest()[:16],
            "attempt_id": attempt_id,
        }

    def remove_after_exact_proof(
        self,
        source: str,
        *,
        source_preimage: dict[str, Any],
        attempt_id: str,
    ) -> dict[str, Any]:
        self.calls.append((
            "remove_after_exact_proof", source,
            copy.deepcopy(source_preimage), attempt_id,
        ))
        if self.fault == "remove":
            raise RuntimeError("remove fault")
        if self.objects.get(source) != source_preimage:
            raise RuntimeError("source preimage changed")
        del self.objects[source]
        return {
            "transaction_id": "remove-tx-" + hashlib.sha256(
                source.encode()
            ).hexdigest()[:16],
            "attempt_id": attempt_id,
        }

    def final_delete(self, _key: str) -> None:  # pragma: no cover
        self.calls.append(("FINAL_DELETE", _key))
        raise AssertionError("quarantine contract has no final deletion authority")


class FakeWriter:
    def __init__(
        self,
        fail_at: int | None = None,
        malformed_at: int | None = None,
        *,
        duplicate_transactions: bool = False,
        wrong_readback_at: int | None = None,
        wrong_identity: bool = False,
        refuse_preflight_at: int | None = None,
        malformed_success_schema: bool = False,
    ):
        self.calls: list[dict[str, Any]] = []
        self.preflight_calls: list[list[str]] = []
        self.fail_at = fail_at
        self.malformed_at = malformed_at
        self.duplicate_transactions = duplicate_transactions
        self.wrong_readback_at = wrong_readback_at
        self.wrong_identity = wrong_identity
        self.refuse_preflight_at = refuse_preflight_at
        self.malformed_success_schema = malformed_success_schema

    def attest_identity(self) -> dict[str, Any]:
        return provider_identity_attestation(
            "wrong-account" if self.wrong_identity else ACCOUNT_ID,
            ENDPOINT_URL,
        )

    def preflight_release(
        self, manifest: dict[str, Any], *, execution_id: str
    ) -> dict[str, Any]:
        self.preflight_calls.append([row["key"] for row in manifest["objects"]])
        observed = [
            copy.deepcopy(row["remote_preimage"])
            for row in manifest["objects"]
        ]
        if self.refuse_preflight_at is not None:
            row = manifest["objects"][self.refuse_preflight_at]
            observed[self.refuse_preflight_at] = (
                None
                if row["conditional_mode"] == "IF_MATCH"
                else {
                    "etag": etag("unexpected-existing"),
                    "bytes": 1,
                    "sha256": hashlib.sha256(b"x").hexdigest(),
                }
            )
        snapshot = build_release_preflight(
            manifest,
            execution_id=execution_id,
            observed_preimages=observed,
            adapter_success_schema=(
                ["attempt_id"] if self.malformed_success_schema
                else (
                    "attempt_id", "bytes", "etag", "key", "sha256",
                    "transaction_id",
                )
            ),
            adapter_success_schema_capable=True,
        )
        return snapshot

    def put_conditional(
        self,
        row: dict[str, Any],
        *,
        attempt_id: str,
        preflight_row: dict[str, Any],
        preflight_sha256: str,
    ) -> dict[str, Any]:
        if (
            preflight_row["key"] != row["key"]
            or preflight_row["expected_row_sha256"] != canonical_sha256(row)
            or preflight_row["eligible"] is not True
            or len(preflight_sha256) != 64
        ):
            raise AssertionError("effect was not bound to the complete eligible preflight")
        index = len(self.calls)
        self.calls.append(copy.deepcopy(row))
        if self.fail_at == index:
            raise RuntimeError("provider uncertainty")
        if self.malformed_at == index:
            return {"key": row["key"], "etag": etag("bad")}
        return {
            "key": row["key"],
            "etag": etag(row["key"]),
            "bytes": row["bytes"],
            "sha256": "0" * 64 if self.wrong_readback_at == index else row["sha256"],
            "transaction_id": (
                "tx-shared" if self.duplicate_transactions
                else "request-tx-" + hashlib.sha256(row["key"].encode()).hexdigest()[:20]
            ),
            "attempt_id": attempt_id,
        }


def _scoped_fixture(
    root: Path,
    *,
    existing_mols: bool = False,
    unchanged: bool = False,
    change_other_area: bool = False,
    change_parking: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], dict[str, bytes]]:
    alpha = "alpha-trail-az"
    old_mols_row = [MOLS_AREA_ID, "Old Mols", "Denmark", 56.2, 10.6, 1, 1.0, 7046785]
    new_mols_row = [MOLS_AREA_ID, "Nationalpark Mols Bjerge", "Denmark", 56.2, 10.6, 2, 3.75, 7046785]
    mols_trails = [
        {
            "id": "mols-one",
            "name": "Mols One",
            "distanceMi": 1.25,
            "difficulty": "Easy",
            "segments": [[[56.2, 10.6], [56.2, 10.7]]],
        },
        {
            "id": "mols-two",
            "name": "Mols Two",
            "distanceMi": 2.5,
            "difficulty": "Moderate",
            "segments": [[[56.1, 10.6], [56.2, 10.6]]],
        },
    ]
    new_mols_geom = json.dumps(
        {"id": MOLS_AREA_ID, "trails": mols_trails}, separators=(",", ":")
    ).encode()
    new_mols_silhouette = b'{"b":[10.4,56.08,10.9,56.35],"l":[0,0,255,255]}'
    alpha_search = [["Alpha Trail", alpha, "alpha", 1.0, "Easy"]]
    mols_search = [
        [
            trail["name"],
            MOLS_AREA_ID,
            trail["id"],
            trail["distanceMi"],
            trail["difficulty"],
        ]
        for trail in mols_trails
    ]
    alpha_shapes = {f"{alpha}/alpha": [0, 0, 255, 0]}
    mols_shapes = {
        f"{MOLS_AREA_ID}/mols-one": [0, 0, 255, 0],
        f"{MOLS_AREA_ID}/mols-two": [0, 0, 0, 255],
    }
    baseline_index_rows: list[list[Any]] = [[alpha]]
    baseline_search = copy.deepcopy(alpha_search)
    baseline_shapes = copy.deepcopy(alpha_shapes)
    baseline: dict[str, bytes] = {
        "index.json": b"",
        "parking.json": b'{"parking":[]}',
        "trail-search.json": b"",
        "trail-shapes.json": b"",
        f"{alpha}.json": b'{"id":"alpha-trail-az","trails":[]}',
        f"silhouettes/{alpha}.json": b'{"b":[0,0,1,1],"l":[0,0,255,255]}',
    }
    if existing_mols:
        baseline_index_rows.append(old_mols_row)
        baseline[MOLS_OBJECT_KEYS[0]] = b'{"id":"nationalpark-mols-bjerge-dk","trails":[{"id":"old","name":"Old","distanceMi":1.0,"difficulty":"Easy","segments":[[[56.2,10.6],[56.2,10.7]]]}]}'
        baseline[MOLS_OBJECT_KEYS[1]] = b'{"b":[10.4,56.08,10.9,56.35],"l":[0,0,1,1]}'
        baseline_search.append(["Old", MOLS_AREA_ID, "old", 1.0, "Easy"])
        baseline_shapes[f"{MOLS_AREA_ID}/old"] = [0, 0, 255, 0]
    if unchanged:
        if not existing_mols:
            raise AssertionError("unchanged scoped fixture requires an existing Mols publication")
        baseline_index_rows[-1] = new_mols_row
        baseline[MOLS_OBJECT_KEYS[0]] = new_mols_geom
        baseline[MOLS_OBJECT_KEYS[1]] = new_mols_silhouette
        baseline_search = [*alpha_search, *copy.deepcopy(mols_search)]
        baseline_shapes = {**alpha_shapes, **copy.deepcopy(mols_shapes)}
    baseline["index.json"] = json.dumps(
        baseline_index_rows, separators=(",", ":")
    ).encode()
    baseline["trail-search.json"] = json.dumps(
        baseline_search, separators=(",", ":")
    ).encode()
    baseline["trail-shapes.json"] = json.dumps(
        baseline_shapes, separators=(",", ":")
    ).encode()
    keys = sorted(baseline)
    source = PageSource({
        None: page([remote(key, baseline[key]) for key in keys])
    })
    inventory = fetch_complete_inventory(
        source,
        bucket=BUCKET,
        account_identity_ref=ACCOUNT,
        endpoint_identity_ref=ENDPOINT,
        authoritative_denominator=len(keys),
        authoritative_area_ids={alpha, MOLS_AREA_ID},
        anchor_state="ASSERTED_PENDING_APPROVAL",
        anchor_ref=ANCHOR,
    )
    candidate = copy.deepcopy(baseline)
    if not unchanged:
        candidate["index.json"] = json.dumps(
            [[alpha], new_mols_row], separators=(",", ":")
        ).encode()
        candidate[MOLS_OBJECT_KEYS[0]] = new_mols_geom
        candidate[MOLS_OBJECT_KEYS[1]] = new_mols_silhouette
        candidate["trail-search.json"] = json.dumps(
            [*alpha_search, *mols_search], separators=(",", ":")
        ).encode()
        candidate_shapes = {**alpha_shapes, **mols_shapes}
        candidate["trail-shapes.json"] = json.dumps(
            candidate_shapes, separators=(",", ":")
        ).encode()
    if change_other_area:
        candidate[f"{alpha}.json"] = b'{"id":"alpha-trail-az","trails":["forbidden"]}'
    if change_parking:
        candidate["parking.json"] = b'{"parking":["forbidden"]}'
    object_root = root / "objects"
    object_root.mkdir()
    local_rows: list[dict[str, Any]] = []
    for key in sorted(candidate):
        relative = Path("objects") / f"{key.encode('utf-8').hex()}.object"
        path = root / relative
        path.write_bytes(candidate[key])
        local_rows.append(local_object_contract(
            key,
            path,
            logical_path=str(relative),
            authoritative_area_ids={alpha, MOLS_AREA_ID},
        ))
    population = build_producer_population(
        local_rows,
        source_tree=TREE,
        authoritative_area_ids={alpha, MOLS_AREA_ID},
        published_area_ids={alpha, MOLS_AREA_ID},
    )
    semantic_deltas = []
    for key in MOLS_SEMANTIC_GLOBAL_KEYS:
        if candidate[key] == baseline[key]:
            continue
        semantic_deltas.append(
            build_mols_global_delta(
                key,
                baseline[key],
                candidate[key],
                candidate[MOLS_OBJECT_KEYS[0]],
                baseline_etag=hashlib.md5(
                    baseline[key], usedforsecurity=False
                ).hexdigest(),
            )
        )
    manifest = build_upload_manifest_contract(
        inventory,
        local_rows,
        population,
        source_sha=COMMIT,
        source_tree=TREE,
        workflow_sha256=WORKFLOW,
        root_approval_ref=APPROVAL,
        prior_anchor_ref=ANCHOR,
        publication_area_id=MOLS_AREA_ID,
        semantic_deltas=semantic_deltas,
    )
    return inventory, local_rows, manifest, baseline


class ScopedFakeWriter:
    """Lock-serialized in-memory writer with exact before/after identities."""

    def __init__(
        self,
        root: Path,
        manifest: dict[str, Any],
        baseline: dict[str, bytes],
        *,
        refuse_forward_at: int | None = None,
        malformed_forward_at: int | None = None,
        preflight_mismatch_at: int | None = None,
        fail_rollback_at: int | None = None,
    ):
        self.root = root
        self.manifest = manifest
        self.objects: dict[str, dict[str, Any]] = {}
        reviewed_preimages = {
            row["key"]: row["remote_preimage"]
            for row in manifest["objects"]
            if row["remote_preimage"] is not None
        }
        for key, payload in baseline.items():
            created = self._object(key, payload)
            if key in reviewed_preimages:
                created.update(reviewed_preimages[key])
            self.objects[key] = created
        self.forward_calls: list[str] = []
        self.rollback_calls: list[tuple[str, str]] = []
        self.refuse_forward_at = refuse_forward_at
        self.malformed_forward_at = malformed_forward_at
        self.preflight_mismatch_at = preflight_mismatch_at
        self.fail_rollback_at = fail_rollback_at

    @staticmethod
    def _object(
        key: str, payload: bytes, *, metadata: dict[str, str] | None = None
    ) -> dict[str, Any]:
        return {
            "key": key,
            "payload": payload,
            "etag": hashlib.md5(payload, usedforsecurity=False).hexdigest(),
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "metadata": copy.deepcopy(metadata),
        }

    @staticmethod
    def _identity(value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        return {field: value[field] for field in ("etag", "bytes", "sha256")}

    def attest_identity(self) -> dict[str, Any]:
        return provider_identity_attestation(ACCOUNT_ID, ENDPOINT_URL)

    def tool_attestation(self) -> dict[str, Any]:
        return provider_tool_attestation(
            version=AWS_CLI_VERSION,
            archive_sha256=AWS_CLI_ARCHIVE_SHA256,
        )

    def preflight_scoped_release(
        self, manifest: dict[str, Any], *, execution_id: str
    ) -> dict[str, Any]:
        rollback_root = self.root / "rollback"
        rollback_root.mkdir(exist_ok=True)
        observed: list[dict[str, Any] | None] = []
        baselines: list[dict[str, Any] | None] = []
        semantic_deltas: list[dict[str, Any]] = []
        index_delta = None
        population = {
            row["key"]: row for row in manifest["producer_population"]["objects"]
        }
        geom_row = population[MOLS_OBJECT_KEYS[0]]
        mols_geom = (self.root / geom_row["local_path"]).read_bytes()
        for index, row in enumerate(manifest["objects"]):
            current = self.objects.get(row["key"])
            identity = self._identity(current)
            if self.preflight_mismatch_at == index:
                identity = None if identity is not None else {
                    "etag": etag("unexpected"), "bytes": 1,
                    "sha256": hashlib.sha256(b"x").hexdigest(),
                }
            observed.append(copy.deepcopy(identity))
            if row["conditional_mode"] == "IF_NONE_MATCH" or current is None:
                baselines.append(None)
                continue
            relative = Path("rollback") / f"{row['key'].encode('utf-8').hex()}.object"
            path = self.root / relative
            path.write_bytes(current["payload"])
            baselines.append({
                "local_path": str(relative),
                "bytes": current["bytes"],
                "md5": current["etag"],
                "sha256": current["sha256"],
                "etag": current["etag"],
            })
            if row["key"] == "index.json":
                index_delta = build_mols_index_delta(
                    current["payload"], (self.root / row["local_path"]).read_bytes()
                )
            elif row["key"] in MOLS_SEMANTIC_GLOBAL_KEYS:
                semantic_deltas.append(
                    build_mols_global_delta(
                        row["key"],
                        current["payload"],
                        (self.root / row["local_path"]).read_bytes(),
                        mols_geom,
                        baseline_etag=current["etag"],
                    )
                )
        return build_scoped_release_preflight(
            manifest,
            execution_id=execution_id,
            observed_preimages=observed,
            baseline_objects=baselines,
            index_delta=index_delta,
            semantic_deltas=semantic_deltas,
        )

    @staticmethod
    def _write_metadata(
        row: dict[str, Any], manifest: dict[str, Any], attempt_id: str,
        *, condition_mode: str, preimage: dict[str, Any] | None,
    ) -> dict[str, str]:
        return {
            "trekdex-sha256": row["sha256"],
            "trekdex-source-sha": manifest["source_sha"],
            "trekdex-manifest-sha256": manifest["manifest_sha256"],
            "trekdex-attempt-id": attempt_id,
            "trekdex-preimage-ref": canonical_sha256(
                preimage if preimage is not None else {"state": "ABSENT"}
            ),
            "trekdex-condition-mode": condition_mode,
        }

    def put_conditional(
        self,
        row: dict[str, Any],
        *,
        attempt_id: str,
        preflight_row: dict[str, Any],
        preflight_sha256: str,
    ) -> dict[str, Any]:
        index = len(self.forward_calls)
        current = self._identity(self.objects.get(row["key"]))
        if current != row["remote_preimage"]:
            raise ConditionalRequestRefused("stale preimage")
        if self.refuse_forward_at == index:
            raise ConditionalRequestRefused("deterministic conditional refusal")
        self.forward_calls.append(row["key"])
        payload = (self.root / row["local_path"]).read_bytes()
        metadata = self._write_metadata(
            row, self.manifest, attempt_id,
            condition_mode=row["conditional_mode"],
            preimage=row["remote_preimage"],
        )
        written = self._object(row["key"], payload, metadata=metadata)
        self.objects[row["key"]] = written
        if self.malformed_forward_at == index:
            return {"key": row["key"], "etag": written["etag"]}
        return {
            "key": row["key"], "etag": written["etag"],
            "bytes": written["bytes"], "sha256": written["sha256"],
            "transaction_id": f"forward-tx-{index}",
            "attempt_id": attempt_id,
        }

    def _maybe_fail_rollback(self) -> None:
        if self.fail_rollback_at == len(self.rollback_calls):
            raise RuntimeError("indeterminate rollback outcome")

    def rollback_put_conditional(
        self,
        rollback_row: dict[str, Any],
        *,
        postimage: dict[str, Any],
        attempt_id: str,
        scoped_preflight_sha256: str,
    ) -> dict[str, Any]:
        self._maybe_fail_rollback()
        key = rollback_row["key"]
        self.rollback_calls.append(("RESTORE_IF_MATCH", key))
        if self._identity(self.objects.get(key)) != {
            field: postimage[field] for field in ("etag", "bytes", "sha256")
        }:
            raise RuntimeError("postimage changed")
        baseline = rollback_row["baseline_object"]
        payload = (self.root / baseline["local_path"]).read_bytes()
        metadata = self._write_metadata(
            baseline, self.manifest, attempt_id,
            condition_mode="IF_MATCH",
            preimage={field: postimage[field] for field in ("etag", "bytes", "sha256")},
        )
        restored = self._object(key, payload, metadata=metadata)
        self.objects[key] = restored
        return {
            "key": key, "etag": restored["etag"],
            "bytes": restored["bytes"], "sha256": restored["sha256"],
            "transaction_id": "restore-tx-" + str(len(self.rollback_calls)),
            "attempt_id": attempt_id,
        }

    def rollback_delete_created(
        self,
        rollback_row: dict[str, Any],
        *,
        postimage: dict[str, Any],
        attempt_id: str,
        scoped_preflight_sha256: str,
    ) -> dict[str, Any]:
        self._maybe_fail_rollback()
        key = rollback_row["key"]
        if key not in MOLS_OBJECT_KEYS or rollback_row["baseline_preimage"] is not None:
            raise AssertionError("delete escaped exact absent Mols scope")
        self.rollback_calls.append(("DELETE_CREATED_AFTER_EXACT_PROOF", key))
        if self._identity(self.objects.get(key)) != {
            field: postimage[field] for field in ("etag", "bytes", "sha256")
        }:
            raise RuntimeError("postimage changed")
        del self.objects[key]
        return {
            "attempt_id": attempt_id, "key": key,
            "postimage_sha256": rollback_row["expected_postimage"]["sha256"],
            "transaction_id": "delete-tx-" + str(len(self.rollback_calls)),
            "verified_absent": True,
        }

    def reconcile_pending_effect(
        self,
        pending: dict[str, Any],
        manifest: dict[str, Any],
        journal: dict[str, Any],
    ) -> dict[str, Any]:
        scoped_preflight = journal["scoped_preflight"]
        index = pending["index"]
        key = pending["key"]
        current_value = self.objects.get(key)
        current = self._identity(current_value)
        if pending["phase"] == "FORWARD":
            row = manifest["objects"][index]
            metadata = None if current_value is None else current_value.get("metadata")
            if (
                current is not None
                and current["bytes"] == row["bytes"]
                and current["sha256"] == row["sha256"]
                and isinstance(metadata, dict)
                and metadata.get("trekdex-attempt-id") == pending["attempt_id"]
                and metadata.get("trekdex-manifest-sha256") == manifest["manifest_sha256"]
            ):
                return {"state": "EFFECT_OBSERVED", "result": {
                    "key": key, "etag": current["etag"],
                    "bytes": current["bytes"], "sha256": current["sha256"],
                    "transaction_id": "reconciled-forward-" + str(index),
                    "attempt_id": pending["attempt_id"],
                }}
            if current == row["remote_preimage"]:
                return {"state": "EFFECT_NOT_OBSERVED", "result": None}
            return {"state": "AMBIGUOUS", "result": None}
        rollback_row = scoped_preflight["rollback_manifest"]["objects"][index]
        if pending["operation"] == "DELETE_CREATED_AFTER_EXACT_PROOF":
            if current is None:
                return {"state": "EFFECT_OBSERVED", "result": {
                    "attempt_id": pending["attempt_id"], "key": key,
                    "postimage_sha256": rollback_row["expected_postimage"]["sha256"],
                    "transaction_id": "reconciled-delete-" + str(index),
                    "verified_absent": True,
                }}
            return {"state": "EFFECT_NOT_OBSERVED", "result": None}
        baseline = rollback_row["baseline_object"]
        metadata = None if current_value is None else current_value.get("metadata")
        if (
            current is not None
            and current == {field: baseline[field] for field in ("etag", "bytes", "sha256")}
            and isinstance(metadata, dict)
            and metadata.get("trekdex-attempt-id") == pending["attempt_id"]
        ):
            return {"state": "EFFECT_OBSERVED", "result": {
                "key": key, "etag": current["etag"],
                "bytes": current["bytes"], "sha256": current["sha256"],
                "transaction_id": "reconciled-restore-" + str(index),
                "attempt_id": pending["attempt_id"],
            }}
        return {"state": "EFFECT_NOT_OBSERVED", "result": None}

    def verify_rollback(
        self,
        rollback_manifest: dict[str, Any],
        *,
        rollback_results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        absent: list[str] = []
        restored_keys: list[str] = []
        semantic_restored: list[str] = []
        index_restored = not any(
            row["key"] == "index.json" for row in rollback_manifest["objects"]
        )
        for row in rollback_manifest["objects"]:
            if row["rollback_operation"] == "DELETE_CREATED_AFTER_EXACT_PROOF":
                if row["key"] in self.objects:
                    raise RuntimeError("created key remains")
                absent.append(row["key"])
                continue
            baseline = row["baseline_object"]
            current = self.objects.get(row["key"])
            if current is None or any(
                current[field] != baseline[field]
                for field in ("etag", "bytes", "sha256")
            ):
                raise RuntimeError("baseline not restored")
            if row["key"] in MOLS_SEMANTIC_GLOBAL_KEYS:
                semantic_restored.append(row["key"])
            restored_keys.append(row["key"])
            if row["key"] == "index.json":
                index_restored = True
        receipt: dict[str, Any] = {
            "schema": "trekdex-r2-rollback-verification-v2",
            "rollback_manifest_sha256": rollback_manifest["rollback_manifest_sha256"],
            "all_rows_restored": True,
            "verified_absent_keys": absent,
            "verified_restored_keys": restored_keys,
            "semantic_global_baselines_restored": semantic_restored,
            "index_baseline_restored": index_restored,
            "non_target_index_entries_unchanged": True,
        }
        receipt["receipt_sha256"] = canonical_sha256(receipt)
        return receipt


def _execute_scoped(
    manifest: dict[str, Any], writer: ScopedFakeWriter, **kwargs: Any
) -> dict[str, Any]:
    kwargs.setdefault("approval_assertion", make_approval_assertion(
        manifest,
        operation="RELEASE",
        key=APPROVAL_KEY,
        authorization_nonce="scoped-mols-test-approval",
    ))
    kwargs.setdefault("approval_key", APPROVAL_KEY)
    kwargs.setdefault("execution_id", "scoped-mols-test-execution")
    return execute_scoped_release(manifest, writer, **kwargs)




def _workflow_mapping_keys(text: str, path: tuple[str, ...]) -> list[str]:
    """Parse direct mapping keys under one exact indentation path."""
    target_indent = 2 * (len(path) - 1)
    stack: list[str] = []
    collecting = False
    keys: list[str] = []
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if "\t" in raw[:indent] or indent % 2:
            raise AssertionError("workflow indentation is not canonical two-space YAML")
        stripped = raw.strip()
        match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_-]*):(?:\s*(.*))?", stripped)
        if not match:
            continue
        key, value = match.groups()
        depth = indent // 2
        stack = stack[:depth]
        if depth == len(stack):
            stack.append(key)
        if tuple(stack) == path:
            collecting = True
            continue
        if collecting and indent <= target_indent:
            break
        if collecting and indent == target_indent + 2:
            keys.append(key)
    if not collecting:
        raise AssertionError(f"workflow mapping path is absent: {path}")
    return keys


def _workflow_scalar(text: str, path: tuple[str, ...]) -> str:
    expected_indent = 2 * (len(path) - 1)
    parents = path[:-1]
    stack: list[str] = []
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        stripped = raw.strip()
        match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_-]*):\s*(.*?)\s*", stripped)
        if not match:
            continue
        key, value = match.groups()
        depth = indent // 2
        stack = stack[:depth]
        if depth == len(stack):
            stack.append(key)
        if indent == expected_indent and tuple(stack[:-1]) == parents and key == path[-1]:
            return value.strip('"\'')
    raise AssertionError(f"workflow scalar path is absent: {path}")


def _workflow_run_scalars(text: str) -> list[tuple[str, int, str]]:
    """Parse canonical step run scalars without evaluating GitHub expressions."""
    lines = text.splitlines()
    parsed: list[tuple[str, int, str]] = []
    for index, raw in enumerate(lines):
        match = re.fullmatch(r" {8}run:\s*(.*?)\s*", raw)
        if not match:
            continue
        value = match.group(1)
        name = "unnamed step"
        for prior in range(index - 1, -1, -1):
            if re.match(r"^      - ", lines[prior]):
                named = re.fullmatch(r"      - name:\s*(.+?)\s*", lines[prior])
                name = named.group(1) if named else lines[prior].strip()
                break
        if value in {"|", "|-", "|+", ">", ">-", ">+"}:
            body: list[str] = []
            cursor = index + 1
            while cursor < len(lines):
                candidate = lines[cursor]
                if candidate.strip() and len(candidate) - len(candidate.lstrip(" ")) <= 8:
                    break
                if candidate and not candidate.startswith(" " * 10):
                    raise AssertionError(
                        f"workflow run block at line {index + 1} has invalid indentation"
                    )
                body.append(candidate[10:] if candidate else "")
                cursor += 1
            if not body:
                raise AssertionError(
                    f"workflow run block at line {index + 1} is empty"
                )
            value = "\n".join(body)
        parsed.append((name, index + 1, value))
    if not parsed:
        raise AssertionError("workflow contains no parsed run scalars")
    return parsed


def _workflow_named_step(text: str, name: str) -> str:
    marker = f"      - name: {name}\n"
    start = text.find(marker)
    if start < 0:
        raise AssertionError(f"workflow step is absent: {name}")
    tail = text[start + len(marker):]
    match = re.search(r"(?m)^      - ", tail)
    return text[start:] if match is None else text[start:start + len(marker) + match.start()]


class TestMolsScopedPublication(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.inventory, self.locals, self.manifest, self.baseline = _scoped_fixture(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_first_publication_is_two_creates_then_three_conditional_global_replaces(self) -> None:
        self.assertEqual([row["key"] for row in self.manifest["objects"]], list(MOLS_FORWARD_KEYS))
        self.assertEqual(
            [row["conditional_mode"] for row in self.manifest["objects"]],
            ["IF_NONE_MATCH", "IF_NONE_MATCH", "IF_MATCH", "IF_MATCH", "IF_MATCH"],
        )
        self.assertNotIn(
            "parking.json", {row["key"] for row in self.manifest["objects"]}
        )
        self.assertEqual(
            {row["key"] for row in self.manifest["objects"]} & set(GLOBAL_OBJECTS),
            set(MOLS_MUTABLE_GLOBAL_KEYS),
        )
        writer = ScopedFakeWriter(self.root, self.manifest, self.baseline)
        receipt = _execute_scoped(self.manifest, writer)
        self.assertEqual(receipt["terminal_state"], "COMPLETE_SUCCESS")
        self.assertEqual(writer.forward_calls, list(MOLS_FORWARD_KEYS))
        self.assertEqual(writer.rollback_calls, [])
        self.assertEqual(
            receipt["provider_tool_attestation"]["version"], AWS_CLI_VERSION
        )
        self.assertEqual(
            receipt["provider_tool_attestation"]["archive_sha256"],
            AWS_CLI_ARCHIVE_SHA256,
        )
        validate_scoped_release_receipt(receipt, self.manifest)

    def test_existing_mols_semantic_globals_are_exact_replace_and_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _inventory, _locals, manifest, baseline = _scoped_fixture(
                root, existing_mols=True
            )
            self.assertEqual(
                [proof["transition_kind"] for proof in manifest["semantic_deltas"]],
                ["REPLACE", "REPLACE"],
            )
            for proof in manifest["semantic_deltas"]:
                self.assertEqual(proof["baseline_mols_record_count"], 1)
                self.assertEqual(proof["candidate_mols_record_count"], 2)
                self.assertEqual(
                    proof["candidate_mols_record_ids"], ["mols-one", "mols-two"]
                )
            writer = ScopedFakeWriter(root, manifest, baseline)
            boundary = "AFTER_WRITE:4:trail-shapes.json"
            receipt = _execute_scoped(
                manifest, writer, state_probe=lambda value: value != boundary
            )
            self.assertEqual(receipt["terminal_state"], "ROLLED_BACK")
            for key in MOLS_SEMANTIC_GLOBAL_KEYS:
                self.assertEqual(writer.objects[key]["payload"], baseline[key])

    def test_existing_mols_objects_and_index_use_exact_if_match(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _inventory, _locals, manifest, baseline = _scoped_fixture(
                root, existing_mols=True
            )
            self.assertTrue(manifest["objects"])
            self.assertTrue(all(row["conditional_mode"] == "IF_MATCH" for row in manifest["objects"]))
            writer = ScopedFakeWriter(root, manifest, baseline)
            receipt = _execute_scoped(manifest, writer)
            self.assertEqual(receipt["terminal_state"], "COMPLETE_SUCCESS")
            self.assertEqual(writer.forward_calls, list(MOLS_FORWARD_KEYS))

    def test_existing_mols_rollback_restores_all_five_exact_baselines(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _inventory, _locals, manifest, baseline = _scoped_fixture(
                root, existing_mols=True
            )
            writer = ScopedFakeWriter(root, manifest, baseline)
            boundary = "AFTER_WRITE:4:trail-shapes.json"
            receipt = _execute_scoped(
                manifest, writer,
                state_probe=lambda value: value != boundary,
            )
            self.assertEqual(receipt["terminal_state"], "ROLLED_BACK")
            self.assertEqual(
                writer.rollback_calls,
                [
                    ("RESTORE_IF_MATCH", "trail-shapes.json"),
                    ("RESTORE_IF_MATCH", "trail-search.json"),
                    ("RESTORE_IF_MATCH", "index.json"),
                    ("RESTORE_IF_MATCH", MOLS_OBJECT_KEYS[1]),
                    ("RESTORE_IF_MATCH", MOLS_OBJECT_KEYS[0]),
                ],
            )
            for key in MOLS_FORWARD_KEYS:
                self.assertEqual(writer.objects[key]["payload"], baseline[key])

    def test_byte_identical_population_is_an_exact_zero_write_repeat(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _inventory, _locals, manifest, baseline = _scoped_fixture(
                root, existing_mols=True, unchanged=True
            )
            self.assertEqual(manifest["objects"], [])
            self.assertEqual(manifest["object_count"], 0)
            writer = ScopedFakeWriter(root, manifest, baseline)
            receipt = _execute_scoped(manifest, writer)
            self.assertEqual(receipt["terminal_state"], "COMPLETE_SUCCESS")
            self.assertEqual(writer.forward_calls, [])
            self.assertEqual(writer.rollback_calls, [])

    def test_stale_missing_wrong_etag_hash_or_bytes_on_each_global_aborts_before_first_effect(self) -> None:
        for global_key in MOLS_MUTABLE_GLOBAL_KEYS:
            for field, value in (
                ("etag", etag("wrong")),
                ("sha256", "0" * 64),
                ("bytes", 999),
            ):
                with self.subTest(key=global_key, field=field):
                    writer = ScopedFakeWriter(self.root, self.manifest, self.baseline)
                    writer.objects[global_key][field] = value
                    receipt = _execute_scoped(self.manifest, writer)
                    self.assertEqual(receipt["terminal_state"], "SAFE_ABORT")
                    self.assertEqual(writer.forward_calls, [])
                    self.assertEqual(writer.rollback_calls, [])
            with self.subTest(key=global_key, field="missing"):
                writer = ScopedFakeWriter(self.root, self.manifest, self.baseline)
                del writer.objects[global_key]
                receipt = _execute_scoped(self.manifest, writer)
                self.assertEqual(receipt["terminal_state"], "SAFE_ABORT")
                self.assertEqual(writer.forward_calls, [])
                self.assertEqual(writer.rollback_calls, [])

    def test_mixed_eligible_and_ineligible_rows_abort_as_one_preflight(self) -> None:
        for index in range(self.manifest["object_count"]):
            writer = ScopedFakeWriter(
                self.root, self.manifest, self.baseline,
                preflight_mismatch_at=index,
            )
            receipt = _execute_scoped(self.manifest, writer)
            with self.subTest(index=index):
                self.assertEqual(receipt["terminal_state"], "SAFE_ABORT")
                self.assertEqual(writer.forward_calls, [])
                self.assertEqual(writer.rollback_calls, [])

    def test_nonzero_partial_scoped_plan_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _inventory, _locals, _manifest, baseline = _scoped_fixture(
                root, existing_mols=True, unchanged=True
            )
            # Start from the exact published bytes, then change only the Mols
            # geometry. A new scoped publication must never derive a one-key
            # plan; it is exactly five keys or an exact zero-write repeat.
            candidate = copy.deepcopy(baseline)
            candidate[MOLS_OBJECT_KEYS[0]] = b'{"id":"nationalpark-mols-bjerge-dk","trails":[{"id":"changed","name":"Changed","distanceMi":1.0,"difficulty":"Easy","segments":[[[56.2,10.6],[56.2,10.7]]]}]}'
            inventory = fetch_complete_inventory(
                PageSource({None: page([
                    remote(key, baseline[key]) for key in sorted(baseline)
                ])}),
                bucket=BUCKET,
                account_identity_ref=ACCOUNT,
                endpoint_identity_ref=ENDPOINT,
                authoritative_denominator=len(baseline),
                authoritative_area_ids={"alpha-trail-az", MOLS_AREA_ID},
                anchor_state="ASSERTED_PENDING_APPROVAL",
                anchor_ref=ANCHOR,
            )
            local_rows = []
            for key in sorted(candidate):
                path = root / f"partial-{key.encode('utf-8').hex()}"
                path.write_bytes(candidate[key])
                local_rows.append(local_object_contract(
                    key,
                    path,
                    logical_path=f"objects/{key.encode('utf-8').hex()}.object",
                    authoritative_area_ids={"alpha-trail-az", MOLS_AREA_ID},
                ))
            population = build_producer_population(
                local_rows,
                source_tree=TREE,
                authoritative_area_ids={"alpha-trail-az", MOLS_AREA_ID},
                published_area_ids={"alpha-trail-az", MOLS_AREA_ID},
            )
            with self.assertRaisesRegex(ManifestError, "five-key or zero-write"):
                build_upload_manifest_contract(
                    inventory,
                    local_rows,
                    population,
                    source_sha=COMMIT,
                    source_tree=TREE,
                    workflow_sha256=WORKFLOW,
                    root_approval_ref=APPROVAL,
                    prior_anchor_ref=ANCHOR,
                    publication_area_id=MOLS_AREA_ID,
                    semantic_deltas=[],
                )

    def test_partial_global_forward_refusal_restores_search_index_and_created_keys(self) -> None:
        writer = ScopedFakeWriter(
            self.root, self.manifest, self.baseline, refuse_forward_at=4
        )
        receipt = _execute_scoped(self.manifest, writer)
        self.assertEqual(receipt["terminal_state"], "ROLLED_BACK")
        self.assertEqual(writer.forward_calls, list(MOLS_FORWARD_KEYS[:4]))
        self.assertEqual(
            writer.rollback_calls,
            [
                ("RESTORE_IF_MATCH", "trail-search.json"),
                ("RESTORE_IF_MATCH", "index.json"),
                ("DELETE_CREATED_AFTER_EXACT_PROOF", MOLS_OBJECT_KEYS[1]),
                ("DELETE_CREATED_AFTER_EXACT_PROOF", MOLS_OBJECT_KEYS[0]),
            ],
        )
        for key in MOLS_MUTABLE_GLOBAL_KEYS:
            self.assertEqual(writer.objects[key]["payload"], self.baseline[key])
        self.assertNotIn(MOLS_OBJECT_KEYS[0], writer.objects)
        self.assertNotIn(MOLS_OBJECT_KEYS[1], writer.objects)
        validate_scoped_release_receipt(receipt, self.manifest)

    def test_partial_forward_refusal_rolls_back_only_known_effects(self) -> None:
        writer = ScopedFakeWriter(
            self.root, self.manifest, self.baseline, refuse_forward_at=1
        )
        receipt = _execute_scoped(self.manifest, writer)
        self.assertEqual(receipt["terminal_state"], "ROLLED_BACK")
        self.assertEqual(writer.forward_calls, [MOLS_OBJECT_KEYS[0]])
        self.assertEqual(
            writer.rollback_calls,
            [("DELETE_CREATED_AFTER_EXACT_PROOF", MOLS_OBJECT_KEYS[0])],
        )
        self.assertNotIn(MOLS_OBJECT_KEYS[0], writer.objects)
        self.assertNotIn(MOLS_OBJECT_KEYS[1], writer.objects)
        validate_scoped_release_receipt(receipt, self.manifest)

    def test_exact_rollback_restores_index_then_deletes_each_created_key_once(self) -> None:
        writer = ScopedFakeWriter(self.root, self.manifest, self.baseline)
        boundary = "AFTER_WRITE:4:trail-shapes.json"
        receipt = _execute_scoped(
            self.manifest,
            writer,
            state_probe=lambda value: value != boundary,
        )
        self.assertEqual(receipt["terminal_state"], "ROLLED_BACK")
        self.assertEqual(writer.forward_calls, list(MOLS_FORWARD_KEYS))
        self.assertEqual(
            writer.rollback_calls,
            [
                ("RESTORE_IF_MATCH", "trail-shapes.json"),
                ("RESTORE_IF_MATCH", "trail-search.json"),
                ("RESTORE_IF_MATCH", "index.json"),
                ("DELETE_CREATED_AFTER_EXACT_PROOF", MOLS_OBJECT_KEYS[1]),
                ("DELETE_CREATED_AFTER_EXACT_PROOF", MOLS_OBJECT_KEYS[0]),
            ],
        )
        self.assertEqual(len(writer.rollback_calls), len(set(writer.rollback_calls)))
        self.assertEqual(writer.objects["index.json"]["payload"], self.baseline["index.json"])
        self.assertNotIn(MOLS_OBJECT_KEYS[0], writer.objects)
        self.assertNotIn(MOLS_OBJECT_KEYS[1], writer.objects)
        verification = receipt["rollback_verification"]
        self.assertTrue(verification["index_baseline_restored"])
        self.assertEqual(
            verification["semantic_global_baselines_restored"],
            list(MOLS_SEMANTIC_GLOBAL_KEYS),
        )
        self.assertEqual(
            verification["verified_restored_keys"],
            list(MOLS_MUTABLE_GLOBAL_KEYS),
        )
        self.assertTrue(verification["non_target_index_entries_unchanged"])

    def test_partial_rollback_is_indeterminate_and_never_retries(self) -> None:
        writer = ScopedFakeWriter(
            self.root, self.manifest, self.baseline, fail_rollback_at=0
        )
        boundary = "AFTER_WRITE:4:trail-shapes.json"
        receipt = _execute_scoped(
            self.manifest,
            writer,
            state_probe=lambda value: value != boundary,
        )
        self.assertEqual(receipt["terminal_state"], "INDETERMINATE")
        self.assertEqual(receipt["rollback_attempted"], 1)
        self.assertEqual(receipt["rollback_observed"], 0)
        self.assertEqual(writer.rollback_calls, [])
        self.assertEqual(receipt["pending_effect"]["operation"], "RESTORE_IF_MATCH")

    def test_malformed_or_ambiguous_provider_success_is_indeterminate(self) -> None:
        writer = ScopedFakeWriter(
            self.root, self.manifest, self.baseline, malformed_forward_at=0
        )
        receipt = _execute_scoped(self.manifest, writer)
        self.assertEqual(receipt["terminal_state"], "INDETERMINATE")
        self.assertEqual(writer.forward_calls, [MOLS_OBJECT_KEYS[0]])
        self.assertEqual(writer.rollback_calls, [])
        self.assertEqual(receipt["pending_effect"]["operation"], "PUT_CONDITIONAL")

    def test_manifest_source_probe_mismatch_has_zero_effect(self) -> None:
        writer = ScopedFakeWriter(self.root, self.manifest, self.baseline)
        receipt = _execute_scoped(
            self.manifest,
            writer,
            state_probe=lambda boundary: boundary != "AFTER_DETACHED_CHECKOUT",
        )
        self.assertEqual(receipt["terminal_state"], "SAFE_ABORT")
        self.assertEqual(writer.forward_calls, [])

    def test_non_mols_object_parking_or_other_global_change_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ManifestError, "out-of-scope"):
                _scoped_fixture(Path(directory), change_other_area=True)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ManifestError, "out-of-scope"):
                _scoped_fixture(Path(directory), change_parking=True)
        hostile = copy.deepcopy(self.manifest)
        hostile["objects"][0]["key"] = "parking.json"
        hostile["objects"][0]["key_class"] = KeyClass.GLOBAL.value
        hostile["manifest_sha256"] = canonical_sha256(
            hostile, omit={"manifest_sha256"}
        )
        with self.assertRaises(ManifestError):
            validate_upload_manifest(hostile, inventory_receipt=self.inventory)

    def test_exact_round_a_hash_reference_derives_only_the_five_key_plan(self) -> None:
        reference_path = ROOT / "scripts/fixtures/mols-round-a-five-key-reference-v1.json"
        reference = json.loads(reference_path.read_text(encoding="utf-8"))
        self.assertEqual(reference["schema"], "trekdex-mols-round-a-five-key-reference-v1")
        self.assertEqual(reference["exact_main"], "aaace72ebb40f0af4eb877b60c3cfd9730c7c944")
        self.assertEqual(reference["exact_tree"], "e4e959a2d394afafd19355d47686475184f061c1")
        self.assertEqual(reference["area_id"], MOLS_AREA_ID)
        self.assertEqual(reference["relation_id"], 7046785)
        self.assertEqual(reference["trail_count"], 26)
        self.assertEqual(reference["expected_write_set"], list(MOLS_FORWARD_KEYS))
        objects = reference["objects"]
        changed = [
            key for key, value in objects.items()
            if value["baseline"] is None
            or value["baseline"]["bytes"] != value["candidate"]["bytes"]
            or value["baseline"]["sha256"] != value["candidate"]["sha256"]
        ]
        self.assertEqual(changed, list(MOLS_FORWARD_KEYS))
        self.assertNotIn("parking.json", changed)
        self.assertTrue(objects["parking.json"]["byte_identical"])
        self.assertEqual(
            objects["parking.json"]["baseline"]["sha256"],
            objects["parking.json"]["candidate"]["sha256"],
        )
        self.assertEqual(reference["index_delta"]["baseline_row_count"], 9074)
        self.assertEqual(reference["index_delta"]["candidate_row_count"], 9075)
        self.assertEqual(reference["index_delta"]["candidate_target"][0], MOLS_AREA_ID)
        self.assertEqual(reference["index_delta"]["candidate_target"][5], 26)
        shared_ids = reference["semantic_deltas"]["trail-search.json"]["candidate_mols_record_ids"]
        self.assertEqual(len(shared_ids), 26)
        self.assertEqual(len(shared_ids), len(set(shared_ids)))
        self.assertEqual(canonical_sha256(shared_ids), "9ea7049c2b0b60f857c1af95b8e31961432d1057107630fe9d5ab537442e7e71")
        for key, semantic_kind in (
            ("trail-search.json", "ORDERED_SEARCH_RECORDS"),
            ("trail-shapes.json", "ORDERED_SHAPE_RECORDS"),
        ):
            summary = reference["semantic_deltas"][key]
            receipt = {
                "schema": "trekdex-r2-mols-global-delta-v1",
                "area_id": MOLS_AREA_ID,
                "key": key,
                "semantic_kind": semantic_kind,
                "baseline_identity": objects[key]["baseline"],
                "candidate_identity": objects[key]["candidate"],
                "mols_geom_identity": objects[MOLS_OBJECT_KEYS[0]]["candidate"],
                "baseline_record_count": summary["baseline_record_count"],
                "candidate_record_count": summary["candidate_record_count"],
                "non_mols_record_count": summary["baseline_record_count"],
                "transition_kind": summary["transition_kind"],
                "baseline_mols_record_count": summary["baseline_mols_record_count"],
                "baseline_mols_records_sha256": summary["baseline_mols_records_sha256"],
                "candidate_mols_record_count": summary["candidate_mols_record_count"],
                "candidate_mols_record_ids": shared_ids,
                "candidate_mols_record_ids_sha256": summary["candidate_mols_record_ids_sha256"],
                "non_mols_records_sha256": summary["non_mols_records_sha256"],
                "candidate_mols_records_sha256": summary["candidate_mols_records_sha256"],
                "non_mols_identity_and_content_preserved": True,
                "only_expected_mols_records_present": True,
            }
            receipt["receipt_sha256"] = canonical_sha256(receipt)
            with self.subTest(key=key):
                self.assertEqual(summary["baseline_record_count"], 92437)
                self.assertEqual(summary["candidate_record_count"], 92463)
                self.assertEqual(summary["transition_kind"], "ADD")
                self.assertEqual(summary["baseline_mols_record_count"], 0)
                self.assertEqual(summary["candidate_mols_record_count"], 26)
                self.assertEqual(
                    receipt["receipt_sha256"], summary["semantic_receipt_sha256"]
                )

    def test_semantic_preflight_binds_exact_baselines_and_deterministic_mols_additions(self) -> None:
        writer = ScopedFakeWriter(self.root, self.manifest, self.baseline)
        preflight = writer.preflight_scoped_release(
            self.manifest, execution_id="semantic-proof"
        )
        rollback_rows = {
            row["key"]: row for row in preflight["rollback_manifest"]["objects"]
        }
        for key in MOLS_SEMANTIC_GLOBAL_KEYS:
            proof = rollback_rows[key]["semantic_delta"]
            reviewed = next(
                value for value in self.manifest["semantic_deltas"]
                if value["key"] == key
            )
            forward = next(row for row in self.manifest["objects"] if row["key"] == key)
            with self.subTest(key=key):
                self.assertEqual(proof, reviewed)
                self.assertEqual(proof["baseline_identity"], forward["remote_preimage"])
                self.assertEqual(proof["candidate_identity"], {
                    "bytes": forward["bytes"], "sha256": forward["sha256"]
                })
                self.assertEqual(proof["transition_kind"], "ADD")
                self.assertEqual(proof["baseline_mols_record_count"], 0)
                self.assertEqual(proof["candidate_mols_record_count"], 2)
                self.assertEqual(proof["candidate_mols_record_ids"], ["mols-one", "mols-two"])
                self.assertTrue(proof["non_mols_identity_and_content_preserved"])
                self.assertTrue(proof["only_expected_mols_records_present"])
        for key in (*MOLS_OBJECT_KEYS, "index.json"):
            self.assertIsNone(rollback_rows[key]["semantic_delta"])

    def test_trail_search_rejects_non_mols_mutation_removal_reorder_and_extra_records(self) -> None:
        geom_row = next(
            row for row in self.manifest["producer_population"]["objects"]
            if row["key"] == MOLS_OBJECT_KEYS[0]
        )
        geom = (self.root / geom_row["local_path"]).read_bytes()
        candidate_row = next(
            row for row in self.manifest["objects"]
            if row["key"] == "trail-search.json"
        )
        candidate = json.loads((self.root / candidate_row["local_path"]).read_bytes())
        mols = [row for row in candidate if row[1] == MOLS_AREA_ID]
        baseline = [
            ["Alpha", "alpha-trail-az", "alpha", 1.0, "Easy"],
            ["Beta", "beta-trail-ut", "beta", 2.0, "Moderate"],
        ]
        cases: dict[str, list[list[Any]]] = {
            "modified": [["Changed", *baseline[0][1:]], baseline[1], *mols],
            "removed": [baseline[1], *mols],
            "reordered": [baseline[1], baseline[0], *mols],
            "extra-non-mols": [*baseline, ["Gamma", "gamma-trail-co", "gamma", 3.0, "Hard"], *mols],
            "extra-mols": [*baseline, *mols, ["Extra", MOLS_AREA_ID, "extra", 1.0, "Easy"]],
        }
        before = json.dumps(baseline, separators=(",", ":")).encode()
        for name, records in cases.items():
            after = json.dumps(records, separators=(",", ":")).encode()
            with self.subTest(case=name), self.assertRaises(ManifestError):
                build_mols_global_delta(
                    "trail-search.json", before, after, geom,
                    baseline_etag=etag("trail-search-baseline"),
                )

    def test_trail_shapes_rejects_non_mols_mutation_removal_reorder_and_extra_records(self) -> None:
        geom_row = next(
            row for row in self.manifest["producer_population"]["objects"]
            if row["key"] == MOLS_OBJECT_KEYS[0]
        )
        geom = (self.root / geom_row["local_path"]).read_bytes()
        candidate_row = next(
            row for row in self.manifest["objects"]
            if row["key"] == "trail-shapes.json"
        )
        candidate = json.loads((self.root / candidate_row["local_path"]).read_bytes())
        target_prefix = f"{MOLS_AREA_ID}/"
        mols = {key: value for key, value in candidate.items() if key.startswith(target_prefix)}
        baseline = {
            "alpha-trail-az/alpha": [0, 0, 255, 0],
            "beta-trail-ut/beta": [0, 0, 0, 255],
        }
        cases: dict[str, dict[str, list[int]]] = {
            "modified": {"alpha-trail-az/alpha": [1, 0, 255, 0], "beta-trail-ut/beta": baseline["beta-trail-ut/beta"], **mols},
            "removed": {"beta-trail-ut/beta": baseline["beta-trail-ut/beta"], **mols},
            "reordered": {"beta-trail-ut/beta": baseline["beta-trail-ut/beta"], "alpha-trail-az/alpha": baseline["alpha-trail-az/alpha"], **mols},
            "extra-non-mols": {**baseline, "gamma-trail-co/gamma": [0, 0, 255, 255], **mols},
            "extra-mols": {**baseline, **mols, f"{MOLS_AREA_ID}/extra": [0, 0, 255, 255]},
        }
        before = json.dumps(baseline, separators=(",", ":")).encode()
        for name, records in cases.items():
            after = json.dumps(records, separators=(",", ":")).encode()
            with self.subTest(case=name), self.assertRaises(ManifestError):
                build_mols_global_delta(
                    "trail-shapes.json", before, after, geom,
                    baseline_etag=etag("trail-shapes-baseline"),
                )

    def test_manifest_rejects_missing_or_reordered_semantic_proofs(self) -> None:
        missing = copy.deepcopy(self.manifest)
        missing["semantic_deltas"] = []
        missing["manifest_sha256"] = canonical_sha256(
            missing, omit={"manifest_sha256"}
        )
        with self.assertRaises(ManifestError):
            validate_upload_manifest(missing, inventory_receipt=self.inventory)
        reordered = copy.deepcopy(self.manifest)
        reordered["semantic_deltas"].reverse()
        reordered["manifest_sha256"] = canonical_sha256(
            reordered, omit={"manifest_sha256"}
        )
        with self.assertRaises(ManifestError):
            validate_upload_manifest(reordered, inventory_receipt=self.inventory)

    def test_semantic_globals_reject_duplicate_identity_noncanonical_and_nonfinite_values(self) -> None:
        geom_row = next(
            row for row in self.manifest["producer_population"]["objects"]
            if row["key"] == MOLS_OBJECT_KEYS[0]
        )
        geom = (self.root / geom_row["local_path"]).read_bytes()
        search_before = b'[["Alpha","alpha-trail-az","alpha",1.0,"Easy"]]'
        search_target = json.loads(next(
            (self.root / row["local_path"]).read_bytes()
            for row in self.manifest["objects"] if row["key"] == "trail-search.json"
        ))[1:]
        hostile_search = (
            b'[["Alpha","alpha-trail-az","alpha",1.0,"Easy"],'
            b'["Duplicate","alpha-trail-az","alpha",2.0,"Hard"]]'
        )
        with self.assertRaisesRegex(ManifestError, "duplicate trail identity"):
            build_mols_global_delta(
                "trail-search.json", hostile_search,
                json.dumps([
                    ["Alpha", "alpha-trail-az", "alpha", 1.0, "Easy"],
                    ["Duplicate", "alpha-trail-az", "alpha", 2.0, "Hard"],
                    *search_target,
                ], separators=(",", ":")).encode(),
                geom, baseline_etag=etag("duplicate-search"),
            )
        noncanonical_after = json.dumps(
            [["Alpha", "alpha-trail-az", "alpha", 1.0, "Easy"], *search_target],
            indent=2,
        ).encode()
        with self.assertRaisesRegex(ManifestError, "canonical producer byte framing"):
            build_mols_global_delta(
                "trail-search.json", search_before, noncanonical_after, geom,
                baseline_etag=etag("noncanonical-search"),
            )
        nonfinite_geom = json.loads(geom)
        nonfinite_geom["trails"][0]["segments"][0][0][0] = float("inf")
        with self.assertRaisesRegex(ManifestError, "non-finite number"):
            build_mols_global_delta(
                "trail-shapes.json",
                b'{"alpha-trail-az/alpha":[0,0,255,0]}',
                next(
                    (self.root / row["local_path"]).read_bytes()
                    for row in self.manifest["objects"] if row["key"] == "trail-shapes.json"
                ),
                json.dumps(nonfinite_geom, allow_nan=True, separators=(",", ":")).encode(),
                baseline_etag=etag("nonfinite-shape"),
            )

    def test_semantic_proof_mutation_is_rejected_even_with_recomputed_outer_digests(self) -> None:
        writer = ScopedFakeWriter(self.root, self.manifest, self.baseline)
        preflight = writer.preflight_scoped_release(
            self.manifest, execution_id="semantic-mutation"
        )
        hostile = copy.deepcopy(preflight)
        row = next(
            item for item in hostile["rollback_manifest"]["objects"]
            if item["key"] == "trail-search.json"
        )
        row["semantic_delta"]["only_expected_mols_records_present"] = False
        row["semantic_delta"]["receipt_sha256"] = canonical_sha256(
            row["semantic_delta"], omit={"receipt_sha256"}
        )
        hostile["rollback_manifest"]["rollback_manifest_sha256"] = canonical_sha256(
            hostile["rollback_manifest"], omit={"rollback_manifest_sha256"}
        )
        hostile["scoped_preflight_sha256"] = canonical_sha256(
            hostile, omit={"scoped_preflight_sha256"}
        )
        with self.assertRaises(ManifestError):
            validate_scoped_release_preflight(
                hostile, self.manifest, execution_id="semantic-mutation"
            )

    def test_non_mols_index_entry_mutation_is_rejected(self) -> None:
        before = b'[["alpha-trail-az","original"]]'
        after = (
            '[["alpha-trail-az","changed"],["nationalpark-mols-bjerge-dk","Mols"]]'
        ).encode()
        with self.assertRaisesRegex(ManifestError, "non-Mols"):
            build_mols_index_delta(before, after)
        reformatted = (
            '[["alpha-trail-az", "original"], '
            '["nationalpark-mols-bjerge-dk", "Mols"]]'
        ).encode()
        with self.assertRaisesRegex(ManifestError, "canonical producer byte framing"):
            build_mols_index_delta(before, reformatted)

    def test_rollback_delete_scope_refuses_global_prefix_and_other_area(self) -> None:
        writer = ScopedFakeWriter(self.root, self.manifest, self.baseline)
        preflight = writer.preflight_scoped_release(
            self.manifest, execution_id="scope-refusal"
        )
        plan = preflight["rollback_manifest"]
        validate_rollback_manifest(plan, self.manifest)
        for forbidden in (
            "index.json", "parking.json", "trail-search.json", "trail-shapes.json",
            "silhouettes/", "other-area-dk.json", "*",
        ):
            hostile = copy.deepcopy(plan)
            delete_row = next(
                row for row in hostile["objects"]
                if row["rollback_operation"] == "DELETE_CREATED_AFTER_EXACT_PROOF"
            )
            delete_row["key"] = forbidden
            hostile["rollback_manifest_sha256"] = canonical_sha256(
                hostile, omit={"rollback_manifest_sha256"}
            )
            with self.subTest(key=forbidden):
                with self.assertRaises(ManifestError):
                    validate_rollback_manifest(hostile, self.manifest)

    def test_repeat_after_success_derives_zero_changed_keys(self) -> None:
        writer = ScopedFakeWriter(self.root, self.manifest, self.baseline)
        first = _execute_scoped(self.manifest, writer)
        self.assertEqual(first["terminal_state"], "COMPLETE_SUCCESS")
        payloads = {key: value["payload"] for key, value in writer.objects.items()}
        repeated_inventory = fetch_complete_inventory(
            PageSource({None: page([remote(key, payloads[key]) for key in sorted(payloads)])}),
            bucket=BUCKET,
            account_identity_ref=ACCOUNT,
            endpoint_identity_ref=ENDPOINT,
            authoritative_denominator=len(payloads),
            authoritative_area_ids={"alpha-trail-az", MOLS_AREA_ID},
            anchor_state="ASSERTED_PENDING_APPROVAL",
            anchor_ref=ANCHOR,
        )
        population = build_producer_population(
            self.locals,
            source_tree=TREE,
            authoritative_area_ids={"alpha-trail-az", MOLS_AREA_ID},
            published_area_ids={"alpha-trail-az", MOLS_AREA_ID},
        )
        repeated = build_upload_manifest_contract(
            repeated_inventory,
            self.locals,
            population,
            source_sha=COMMIT,
            source_tree=TREE,
            workflow_sha256=WORKFLOW,
            root_approval_ref=APPROVAL,
            prior_anchor_ref=ANCHOR,
            publication_area_id=MOLS_AREA_ID,
        )
        self.assertEqual(repeated["object_count"], 0)
        self.assertEqual(repeated["objects"], [])

    def test_scoped_receipt_mutations_fail_closed(self) -> None:
        receipt = _execute_scoped(
            self.manifest,
            ScopedFakeWriter(self.root, self.manifest, self.baseline),
        )
        for name, mutation in (
            ("source", lambda d: d.__setitem__("source_sha", "9" * 40)),
            ("delete-authority", lambda d: d["scoped_preflight"]["rollback_manifest"].__setitem__("delete_allowlist", ["*"])),
            ("effect-count", lambda d: d.__setitem__("forward_observed", 0)),
            ("tool-version", lambda d: d["provider_tool_attestation"].__setitem__("version", "2.39.0")),
            ("forbidden-push", lambda d: d.__setitem__("pushed", True)),
        ):
            hostile = copy.deepcopy(receipt)
            mutation(hostile)
            if name == "delete-authority":
                hostile["scoped_preflight"]["scoped_preflight_sha256"] = canonical_sha256(
                    hostile["scoped_preflight"], omit={"scoped_preflight_sha256"}
                )
            hostile["receipt_sha256"] = canonical_sha256(
                hostile, omit={"receipt_sha256"}
            )
            with self.subTest(name=name):
                with self.assertRaises(ManifestError):
                    validate_scoped_release_receipt(hostile, self.manifest)


class TestMolsReleaseInputAndStagedDiff(unittest.TestCase):
    def setUp(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "stage_changed_r2_release_input", SCRIPTS / "stage-changed-r2.py"
        )
        assert spec and spec.loader
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.receipt = make_mols_release_input_receipt(
            key=APPROVAL_KEY,
            root_approval_ref=APPROVAL,
            account_identity_ref=ACCOUNT,
            endpoint_identity_ref=ENDPOINT,
            inventory_anchor_ref=ANCHOR,
            authoritative_denominator=12345,
            workflow_source_sha=COMMIT,
            workflow_source_tree=TREE,
            authorization_nonce="mols-release-input-test",
        )
        payload = (
            json.dumps(
                self.receipt,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode()
        self.encoded = base64.b64encode(payload).decode("ascii")

    def test_signed_receipt_is_canonical_and_every_exact_tuple_mutation_fails(self) -> None:
        self.assertEqual(
            decode_mols_release_input_receipt(self.encoded, key=APPROVAL_KEY),
            self.receipt,
        )
        exact_fields = (
            "extract", "state", "region_code", "area_id", "area_name",
            "osm_relation_id", "bbox", "center_lat", "center_lon",
            "root_approval_ref",
            "account_identity_ref", "endpoint_identity_ref",
            "inventory_anchor_ref", "authoritative_denominator",
            "workflow_source_sha", "workflow_source_tree",
        )
        for field in exact_fields:
            hostile = copy.deepcopy(self.receipt)
            hostile[field] = 999 if field in {"osm_relation_id", "authoritative_denominator"} else "wrong"
            payload = (
                json.dumps(hostile, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode()
            with self.subTest(field=field), self.assertRaises(ManifestError):
                decode_mols_release_input_receipt(
                    base64.b64encode(payload).decode("ascii"), key=APPROVAL_KEY
                )

    def test_verify_input_emits_only_verified_receipt_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "github-output"
            mols_index = Path(directory) / "mols-index.json"
            output.write_text("")
            args = argparse.Namespace(
                extract="europe/denmark", state="Denmark", region_code="dk",
                bbox="10.40,56.08,10.90,56.35", dry_run="false",
                no_routes="false", touch_report="false", elevation="false",
                pilot_area_id="", workflow_source_sha=COMMIT,
                workflow_source_tree=TREE, mols_index_output=mols_index,
                github_output=output,
            )
            with mock.patch.dict(os.environ, {
                "MOLS_RELEASE_INPUT_RECEIPT_B64": self.encoded,
                "R2_ROOT_APPROVAL_HMAC_KEY": APPROVAL_KEY,
            }, clear=True):
                self.assertEqual(self.module.verify_release_input_v2(args), 0)
            values = dict(
                line.split("=", 1) for line in output.read_text().splitlines()
            )
            self.assertEqual(values["release_mode"], "mols")
            self.assertEqual(values["area_id"], MOLS_AREA_ID)
            self.assertEqual(values["root_approval_ref"], APPROVAL)
            self.assertEqual(values["account_identity_ref"], ACCOUNT)
            self.assertEqual(values["endpoint_identity_ref"], ENDPOINT)
            self.assertEqual(values["inventory_anchor_ref"], ANCHOR)
            self.assertEqual(values["authoritative_denominator"], "12345")
            self.assertEqual(values["workflow_source_sha"], COMMIT)
            self.assertEqual(values["workflow_source_tree"], TREE)
            self.assertEqual(values["publication_index_path"], str(mols_index))
            self.assertEqual(
                strict_json_loads(mols_index.read_text(), "Mols seed index"),
                [[
                    MOLS_AREA_ID, "Nationalpark Mols Bjerge", "Denmark",
                    56.2, 10.6, None, None, 7046785,
                ]],
            )
            self.assertEqual(values["receipt_sha256"], self.receipt["receipt_sha256"])

    def test_wrong_workflow_tuple_fails_before_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            for field, value in (
                ("extract", "north-america/us/arizona"),
                ("state", "Arizona"),
                ("region_code", ""),
                ("bbox", "0,0,1,1"),
                ("dry_run", "true"),
                ("workflow_source_sha", "9" * 40),
                ("workflow_source_tree", "8" * 40),
            ):
                output = Path(directory) / f"output-{field}"
                output.write_text("")
                values = {
                    "extract": "europe/denmark", "state": "Denmark",
                    "region_code": "dk", "bbox": "10.40,56.08,10.90,56.35",
                    "dry_run": "false", "no_routes": "false",
                    "touch_report": "false", "elevation": "false",
                    "pilot_area_id": "", "workflow_source_sha": COMMIT,
                    "workflow_source_tree": TREE,
                    "mols_index_output": Path(directory) / f"mols-{field}.json",
                    "github_output": output,
                }
                values[field] = value
                with self.subTest(field=field), mock.patch.dict(os.environ, {
                    "MOLS_RELEASE_INPUT_RECEIPT_B64": self.encoded,
                    "R2_ROOT_APPROVAL_HMAC_KEY": APPROVAL_KEY,
                }, clear=True), self.assertRaises(ManifestError):
                    self.module.verify_release_input_v2(argparse.Namespace(**values))
                self.assertEqual(output.read_text(), "")

    def test_exact_staged_diff_accepts_only_two_mols_objects_and_two_indexes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            output = root / "staged-receipt.json"
            alpha = ["alpha-trail-az", "Alpha", "Arizona", 0, 0, 1, 1.0, 1]
            mols = [MOLS_AREA_ID, "Nationalpark Mols Bjerge", "Denmark", 56.2, 10.6, 12, 31.4, 7046785]
            public_before = json.dumps([alpha]).encode()
            public_after = json.dumps([alpha, mols]).encode()
            ios_before = json.dumps([alpha], separators=(",", ":")).encode()
            ios_after = json.dumps([alpha, mols], separators=(",", ":")).encode()
            staged = {
                f":public/areas/geom/{MOLS_AREA_ID}.json": json.dumps({"id": MOLS_AREA_ID}).encode(),
                f":public/areas/silhouettes/{MOLS_AREA_ID}.json": json.dumps({"b": [0, 0, 1, 1], "l": [{"d": "e", "p": [[0, 0], [1, 1]]}]}).encode(),
                ":public/areas/index.json": public_after,
                ":ios/SouthMountainExplorer/Resources/areas-index.json": ios_after,
                "HEAD:public/areas/index.json": public_before,
                "HEAD:ios/SouthMountainExplorer/Resources/areas-index.json": ios_before,
            }

            def git_bytes(_repo: Path, *arguments: str, **_kwargs: Any) -> bytes:
                if arguments[:4] == ("diff", "--cached", "--name-only", "--diff-filter=ACMR"):
                    return ("\0".join(sorted((
                        f"public/areas/geom/{MOLS_AREA_ID}.json",
                        f"public/areas/silhouettes/{MOLS_AREA_ID}.json",
                        "public/areas/index.json",
                        "ios/SouthMountainExplorer/Resources/areas-index.json",
                    ))) + "\0").encode()
                if arguments[:4] == ("diff", "--cached", "--name-only", "--diff-filter=D"):
                    return b""
                if arguments[0] == "show":
                    return staged[arguments[1]]
                raise AssertionError(arguments)

            args = argparse.Namespace(
                repo=root,
                expected_receipt_sha256=self.receipt["receipt_sha256"],
                output=output,
            )
            with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(
                self.module, "_git_bytes", side_effect=git_bytes
            ):
                self.assertEqual(self.module.verify_mols_staged_diff_v2(args), 0)
            result = strict_json_loads(output.read_text(), "staged diff result")
            self.assertEqual(
                set(result["allowed_paths"]),
                {
                    f"public/areas/geom/{MOLS_AREA_ID}.json",
                    f"public/areas/silhouettes/{MOLS_AREA_ID}.json",
                    "public/areas/index.json",
                    "ios/SouthMountainExplorer/Resources/areas-index.json",
                },
            )
            self.assertTrue(all(
                item["non_mols_rows_unchanged"]
                for item in result["index_transitions"]
            ))

    def test_signed_one_row_merge_changes_only_mols_master_index_row(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "public/areas/index.json"
            target.parent.mkdir(parents=True)
            alpha = ["alpha-trail-az", "Alpha", "Arizona", 0, 0]
            omega = ["omega-trail-wy", "Omega", "Wyoming", 1, 1]
            target.write_text(json.dumps([alpha, omega]))
            source = root / "mols-index.json"
            mols = [
                MOLS_AREA_ID, "Nationalpark Mols Bjerge", "Denmark",
                56.2, 10.6, 12, 31.4, 7046785,
            ]
            source.write_text(json.dumps([mols]))
            with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(
                self.module.Path, "cwd", return_value=root
            ):
                self.assertEqual(self.module.merge_mols_index_v2(
                    argparse.Namespace(
                        source=source, target=target,
                        expected_receipt_sha256=self.receipt["receipt_sha256"],
                    )
                ), 0)
            merged = json.loads(target.read_text())
            self.assertEqual(
                [row for row in merged if row[0] != MOLS_AREA_ID],
                [alpha, omega],
            )
            self.assertEqual([row for row in merged if row[0] == MOLS_AREA_ID], [mols])

    def test_exact_committed_diff_is_revalidated_before_push(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            output = root / "commit-receipt.json"
            alpha = ["alpha-trail-az", "Alpha", "Arizona", 0, 0, 1, 1.0, 1]
            mols = [MOLS_AREA_ID, "Nationalpark Mols Bjerge", "Denmark", 56.2, 10.6, 12, 31.4, 7046785]
            documents = {
                f"HEAD:{MOLS_REPOSITORY_PATHS[0]}": json.dumps({"id": MOLS_AREA_ID}).encode(),
                f"HEAD:{MOLS_REPOSITORY_PATHS[1]}": json.dumps({"b": [0, 0, 1, 1], "l": [{}]}).encode(),
                "HEAD^:public/areas/index.json": json.dumps([alpha]).encode(),
                "HEAD:public/areas/index.json": json.dumps([alpha, mols]).encode(),
                "HEAD^:ios/SouthMountainExplorer/Resources/areas-index.json": json.dumps([alpha], separators=(",", ":")).encode(),
                "HEAD:ios/SouthMountainExplorer/Resources/areas-index.json": json.dumps([alpha, mols], separators=(",", ":")).encode(),
            }

            def git_bytes(_repo: Path, *arguments: str, **_kwargs: Any) -> bytes:
                if arguments[0] == "diff" and "--diff-filter=ACMR" in arguments:
                    return ("\0".join(sorted(MOLS_REPOSITORY_PATHS)) + "\0").encode()
                if arguments[0] == "diff" and "--diff-filter=D" in arguments:
                    return b""
                if arguments[0] == "show":
                    return documents[arguments[1]]
                if arguments == ("rev-parse", "HEAD"):
                    return ("1" * 40 + "\n").encode()
                if arguments == ("rev-parse", "HEAD^"):
                    return ("2" * 40 + "\n").encode()
                raise AssertionError(arguments)

            with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(
                self.module, "_git_bytes", side_effect=git_bytes
            ):
                self.assertEqual(self.module.verify_mols_commit_v2(
                    argparse.Namespace(
                        repo=root,
                        expected_receipt_sha256=self.receipt["receipt_sha256"],
                        output=output,
                    )
                ), 0)
            result = strict_json_loads(output.read_text(), "commit diff result")
            self.assertEqual(result["commit_sha"], "1" * 40)
            self.assertEqual(result["parent_sha"], "2" * 40)

    def test_extra_staged_path_is_refused_before_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            output = root / "must-not-exist.json"
            names = sorted((*MOLS_REPOSITORY_PATHS, "public/areas/geom/other-area-dk.json"))
            with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(
                self.module, "_git_bytes",
                return_value=("\0".join(names) + "\0").encode(),
            ), self.assertRaises(ManifestError):
                self.module.verify_mols_staged_diff_v2(argparse.Namespace(
                    repo=root,
                    expected_receipt_sha256=self.receipt["receipt_sha256"],
                    output=output,
                ))
            self.assertFalse(output.exists())


class TestScopedReconciliation(unittest.TestCase):
    class ProcessDeath(BaseException):
        pass

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        _inventory, _locals, self.manifest, self.baseline = _scoped_fixture(self.root)
        self.approval = make_approval_assertion(
            self.manifest,
            operation="RELEASE",
            key=APPROVAL_KEY,
            authorization_nonce="scoped-reconcile-test",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _reconcile(
        self,
        journal: dict[str, Any],
        writer: ScopedFakeWriter,
        **kwargs: Any,
    ) -> dict[str, Any]:
        return reconcile_scoped_release_write_ahead(
            journal,
            self.manifest,
            writer,
            approval_assertion=self.approval,
            approval_key=APPROVAL_KEY,
            **kwargs,
        )

    def test_process_death_after_forward_success_resumes_once_and_repeat_is_idempotent(self) -> None:
        checkpoints: list[dict[str, Any]] = []
        parent = self

        class CrashAfterForward(ScopedFakeWriter):
            crashed = False

            def put_conditional(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
                result = super().put_conditional(*args, **kwargs)
                if not self.crashed:
                    self.crashed = True
                    raise parent.ProcessDeath("after forward provider success")
                return result

        writer = CrashAfterForward(self.root, self.manifest, self.baseline)
        with self.assertRaises(self.ProcessDeath):
            _execute_scoped(
                self.manifest, writer,
                approval_assertion=self.approval,
                checkpoint=lambda document: checkpoints.append(copy.deepcopy(document)),
            )
        journal = checkpoints[-1]
        self.assertEqual(journal["checkpoint_kind"], "WRITE_AHEAD")
        self.assertEqual(journal["pending_effect"]["phase"], "FORWARD")
        terminal = self._reconcile(journal, writer)
        self.assertEqual(terminal["terminal_state"], "COMPLETE_SUCCESS")
        self.assertEqual(terminal["reconciliation"]["observation_state"], "EFFECT_OBSERVED")
        self.assertTrue(terminal["reconciliation"]["resumed"])
        self.assertEqual(writer.forward_calls, list(MOLS_FORWARD_KEYS))
        calls_before_repeat = (
            list(writer.forward_calls), list(writer.rollback_calls)
        )
        repeated = self._reconcile(terminal, writer)
        self.assertEqual(repeated, terminal)
        self.assertEqual(
            (writer.forward_calls, writer.rollback_calls), calls_before_repeat
        )

    def test_process_death_after_trail_search_success_resumes_shape_once(self) -> None:
        checkpoints: list[dict[str, Any]] = []
        parent = self

        class CrashAfterSearch(ScopedFakeWriter):
            def put_conditional(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
                result = super().put_conditional(*args, **kwargs)
                if len(self.forward_calls) == 4:
                    raise parent.ProcessDeath("after trail-search provider success")
                return result

        writer = CrashAfterSearch(self.root, self.manifest, self.baseline)
        with self.assertRaises(self.ProcessDeath):
            _execute_scoped(
                self.manifest,
                writer,
                approval_assertion=self.approval,
                checkpoint=lambda document: checkpoints.append(copy.deepcopy(document)),
            )
        journal = checkpoints[-1]
        self.assertEqual(journal["pending_effect"]["key"], "trail-search.json")
        self.assertEqual(journal["forward_observed"], 3)
        terminal = self._reconcile(journal, writer)
        self.assertEqual(terminal["terminal_state"], "COMPLETE_SUCCESS")
        self.assertEqual(writer.forward_calls, list(MOLS_FORWARD_KEYS))
        calls_before_repeat = list(writer.forward_calls)
        self.assertEqual(self._reconcile(terminal, writer), terminal)
        self.assertEqual(writer.forward_calls, calls_before_repeat)

    def test_timeout_after_forward_effect_reconciles_without_replay(self) -> None:
        class TimeoutAfterEffect(ScopedFakeWriter):
            timed_out = False

            def put_conditional(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
                result = super().put_conditional(*args, **kwargs)
                if not self.timed_out:
                    self.timed_out = True
                    raise RuntimeError("simulated timeout after committed effect")
                return result

        writer = TimeoutAfterEffect(self.root, self.manifest, self.baseline)
        terminal_pending = _execute_scoped(
            self.manifest, writer, approval_assertion=self.approval
        )
        self.assertEqual(terminal_pending["terminal_state"], "INDETERMINATE")
        self.assertEqual(terminal_pending["checkpoint_kind"], "TERMINAL")
        self.assertIsNotNone(terminal_pending["pending_effect"])
        reconciled = self._reconcile(terminal_pending, writer)
        self.assertEqual(reconciled["terminal_state"], "COMPLETE_SUCCESS")
        self.assertEqual(writer.forward_calls, list(MOLS_FORWARD_KEYS))

    def test_process_death_after_rollback_success_resumes_once_and_repeat_is_idempotent(self) -> None:
        checkpoints: list[dict[str, Any]] = []
        parent = self

        class CrashAfterRollback(ScopedFakeWriter):
            crashed = False

            def rollback_put_conditional(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
                result = super().rollback_put_conditional(*args, **kwargs)
                if not self.crashed:
                    self.crashed = True
                    raise parent.ProcessDeath("after rollback provider success")
                return result

        writer = CrashAfterRollback(self.root, self.manifest, self.baseline)
        boundary = "AFTER_WRITE:4:trail-shapes.json"
        with self.assertRaises(self.ProcessDeath):
            _execute_scoped(
                self.manifest, writer,
                approval_assertion=self.approval,
                state_probe=lambda value: value != boundary,
                checkpoint=lambda document: checkpoints.append(copy.deepcopy(document)),
            )
        journal = checkpoints[-1]
        self.assertEqual(journal["pending_effect"]["phase"], "ROLLBACK")
        self.assertEqual(journal["pending_effect"]["operation"], "RESTORE_IF_MATCH")
        terminal = self._reconcile(journal, writer)
        self.assertEqual(terminal["terminal_state"], "ROLLED_BACK")
        self.assertEqual(
            writer.rollback_calls,
            [
                ("RESTORE_IF_MATCH", "trail-shapes.json"),
                ("RESTORE_IF_MATCH", "trail-search.json"),
                ("RESTORE_IF_MATCH", "index.json"),
                ("DELETE_CREATED_AFTER_EXACT_PROOF", MOLS_OBJECT_KEYS[1]),
                ("DELETE_CREATED_AFTER_EXACT_PROOF", MOLS_OBJECT_KEYS[0]),
            ],
        )
        repeated = self._reconcile(terminal, writer)
        self.assertEqual(repeated, terminal)
        self.assertEqual(len(writer.rollback_calls), 5)

    def test_unobserved_pending_delete_halts_for_operator_without_retry(self) -> None:
        checkpoints: list[dict[str, Any]] = []
        parent = self

        class CrashBeforeDelete(ScopedFakeWriter):
            def rollback_delete_created(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
                raise parent.ProcessDeath("before delete provider call")

        writer = CrashBeforeDelete(self.root, self.manifest, self.baseline)
        boundary = "AFTER_WRITE:4:trail-shapes.json"
        with self.assertRaises(self.ProcessDeath):
            _execute_scoped(
                self.manifest, writer,
                approval_assertion=self.approval,
                state_probe=lambda value: value != boundary,
                checkpoint=lambda document: checkpoints.append(copy.deepcopy(document)),
            )
        journal = checkpoints[-1]
        self.assertEqual(journal["pending_effect"]["operation"], "DELETE_CREATED_AFTER_EXACT_PROOF")
        rollback_calls = list(writer.rollback_calls)
        terminal = self._reconcile(journal, writer)
        self.assertEqual(terminal["terminal_state"], "INDETERMINATE")
        self.assertEqual(terminal["error_boundary"], "RECONCILED_OPERATOR_REVIEW")
        self.assertEqual(
            terminal["reconciliation"]["observation_state"],
            "EFFECT_NOT_OBSERVED",
        )
        self.assertFalse(terminal["reconciliation"]["resumed"])
        self.assertEqual(writer.rollback_calls, rollback_calls)
        self.assertEqual(self._reconcile(terminal, writer), terminal)


class TestMolsProviderAdapter(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.inventory, self.locals, self.manifest, self.baseline = _scoped_fixture(self.root)
        fake = ScopedFakeWriter(self.root, self.manifest, self.baseline)
        self.preflight = fake.preflight_scoped_release(
            self.manifest, execution_id="adapter-contract-test"
        )
        spec = importlib.util.spec_from_file_location(
            "stage_changed_r2_adapter_test", SCRIPTS / "stage-changed-r2.py"
        )
        assert spec and spec.loader
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.adapter = self.module.MolsConditionalWriter(
            endpoint_url=ENDPOINT_URL,
            account_id=ACCOUNT_ID,
            manifest_root=self.root,
            aws_cli_version=AWS_CLI_VERSION,
            aws_cli_archive_sha256=AWS_CLI_ARCHIVE_SHA256,
        )
        self.adapter._active_source_sha = COMMIT
        self.adapter._active_manifest_sha256 = self.manifest["manifest_sha256"]
        self.adapter._active_scoped_preflight_sha256 = self.preflight["scoped_preflight_sha256"]
        self.adapter._active_release_preflight_sha256 = self.preflight["release_preflight"]["preflight_sha256"]
        self.adapter._active_rollback_rows = {
            row["key"]: copy.deepcopy(row)
            for row in self.preflight["rollback_manifest"]["objects"]
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _postimage(self, key: str) -> tuple[bytes, dict[str, Any]]:
        row = next(row for row in self.manifest["objects"] if row["key"] == key)
        payload = (self.root / row["local_path"]).read_bytes()
        return payload, {
            "key": key,
            "etag": hashlib.md5(payload, usedforsecurity=False).hexdigest(),
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "transaction_id": "forward-provider-result",
            "attempt_id": "forward-attempt",
        }

    @staticmethod
    def _completed(command: list[str], *, stdout: str = "{}", stderr: str = "", code: int = 0) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, code, stdout=stdout, stderr=stderr)

    @staticmethod
    def _head_json(
        identity: dict[str, Any], *, checksum: bool = True,
        metadata: dict[str, str] | None = None,
    ) -> str:
        document: dict[str, Any] = {
            "ETag": '"' + identity["etag"] + '"',
            "ContentLength": identity["bytes"],
        }
        if checksum:
            document["ChecksumSHA256"] = identity["sha256"]
        if metadata is not None:
            document["Metadata"] = metadata
        return json.dumps(document)

    def _delete_row(self) -> dict[str, Any]:
        return next(
            row for row in self.preflight["rollback_manifest"]["objects"]
            if row["rollback_operation"] == "DELETE_CREATED_AFTER_EXACT_PROOF"
        )

    def test_tool_attestation_checks_active_version_and_archive_pin(self) -> None:
        good = subprocess.CompletedProcess(
            ["aws", "--version"], 0,
            stdout="", stderr="aws-cli/2.39.1 Python/3.13 Linux/x86_64\n",
        )
        with mock.patch.object(self.module.subprocess, "run", return_value=good):
            attestation = self.adapter.tool_attestation()
        self.assertEqual(attestation["version"], AWS_CLI_VERSION)
        self.assertEqual(attestation["archive_sha256"], AWS_CLI_ARCHIVE_SHA256)
        bad = subprocess.CompletedProcess(
            ["aws", "--version"], 0,
            stdout="aws-cli/2.39.0 Python/3.13 Linux/x86_64\n", stderr="",
        )
        with mock.patch.object(self.module.subprocess, "run", return_value=bad), self.assertRaises(ManifestError):
            self.adapter.tool_attestation()

    def test_real_preflight_freezes_index_baseline_and_standalone_rollback_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _inventory, _locals, manifest, baseline = _scoped_fixture(root)
            adapter = self.module.MolsConditionalWriter(
                endpoint_url=ENDPOINT_URL,
                account_id=ACCOUNT_ID,
                manifest_root=root,
                aws_cli_version=AWS_CLI_VERSION,
                aws_cli_archive_sha256=AWS_CLI_ARCHIVE_SHA256,
            )
            identities = {
                row["key"]: copy.deepcopy(row["remote_preimage"])
                for row in manifest["objects"]
            }

            def head(key: str, *, missing_ok: bool = False) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
                identity = identities[key]
                return copy.deepcopy(identity), {} if identity is not None else None

            def download(
                key: str,
                *,
                etag: str,
                output: Path,
                context: str,
            ) -> dict[str, Any]:
                output.write_bytes(baseline[key])
                return {}

            with mock.patch.object(adapter, "_head_identity", side_effect=head), mock.patch.object(
                adapter, "_download_exact", side_effect=download
            ):
                scoped = adapter.preflight_scoped_release(
                    manifest, execution_id="real-adapter-preflight"
                )
            validate_scoped_release_preflight(
                scoped, manifest, execution_id="real-adapter-preflight"
            )
            standalone = strict_json_loads(
                (root / "rollback-manifest.json").read_text(),
                "standalone rollback manifest",
            )
            self.assertEqual(standalone, scoped["rollback_manifest"])
            index_row = next(
                row for row in standalone["objects"] if row["key"] == "index.json"
            )
            self.assertEqual(
                (root / index_row["baseline_object"]["local_path"]).read_bytes(),
                baseline["index.json"],
            )
            self.assertTrue(standalone["index_delta"]["non_target_rows_equal"])

    def test_forward_create_uses_if_none_match_and_request_bound_identities(self) -> None:
        row = self.manifest["objects"][0]
        self.assertEqual(row["conditional_mode"], "IF_NONE_MATCH")
        payload = (self.root / row["local_path"]).read_bytes()
        written = {
            "etag": row["md5"],
            "bytes": row["bytes"],
            "sha256": row["sha256"],
        }
        head_count = 0
        commands: list[list[str]] = []
        request_metadata: dict[str, str] = {}

        def run(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
            nonlocal head_count, request_metadata
            commands.append(command)
            operation = command[2]
            if operation == "head-object":
                head_count += 1
                if head_count == 1:
                    return self._completed(command, code=1, stderr="404 Not Found")
                return self._completed(
                    command,
                    stdout=self._head_json(written, metadata=request_metadata),
                )
            if operation == "put-object":
                request_metadata = json.loads(command[command.index("--metadata") + 1])
                return self._completed(command, stdout=json.dumps({
                    "ETag": '"' + written["etag"] + '"',
                    "UnexpectedFutureField": {"ignored": True},
                }))
            if operation == "get-object":
                Path(command[-1]).write_bytes(payload)
                return self._completed(command)
            raise AssertionError(command)

        with mock.patch.object(self.module.subprocess, "run", side_effect=run):
            result = self.adapter.put_conditional(
                row,
                attempt_id="forward-create-attempt",
                preflight_row=self.preflight["release_preflight"]["rows"][0],
                preflight_sha256=self.preflight["release_preflight"]["preflight_sha256"],
            )
        put = next(command for command in commands if command[2] == "put-object")
        self.assertEqual(put[put.index("--if-none-match") + 1], "*")
        self.assertNotIn("--if-match", put)
        self.assertEqual(request_metadata["trekdex-sha256"], row["sha256"])
        self.assertEqual(request_metadata["trekdex-source-sha"], COMMIT)
        self.assertEqual(
            request_metadata["trekdex-manifest-sha256"], self.manifest["manifest_sha256"]
        )
        self.assertEqual(request_metadata["trekdex-condition-mode"], "IF_NONE_MATCH")
        self.assertEqual(
            request_metadata["trekdex-preimage-ref"], canonical_sha256({"state": "ABSENT"})
        )
        self.assertEqual(result["sha256"], written["sha256"])

    def test_put_response_variants_fail_closed_without_schema_assumptions(self) -> None:
        row = self.manifest["objects"][0]
        preflight_row = self.preflight["release_preflight"]["rows"][0]
        cases = (
            ("missing-etag", 0, "{}", "", ManifestError),
            ("precondition", 1, "", "An error occurred (412): PreconditionFailed", ConditionalRequestRefused),
            ("not-found", 1, "", "An error occurred (404): Not Found", ManifestError),
            ("server", 1, "", "An error occurred (500): InternalError", ManifestError),
        )
        for name, code, stdout, stderr, expected_error in cases:
            operations: list[str] = []

            def run(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
                operation = command[2]
                operations.append(operation)
                if operation == "head-object":
                    return self._completed(command, code=1, stderr="404 Not Found")
                if operation == "put-object":
                    return self._completed(
                        command, code=code, stdout=stdout, stderr=stderr
                    )
                raise AssertionError(command)

            with self.subTest(case=name), mock.patch.object(
                self.module.subprocess, "run", side_effect=run
            ):
                with self.assertRaises(expected_error):
                    self.adapter.put_conditional(
                        row,
                        attempt_id=f"variant-{name}",
                        preflight_row=preflight_row,
                        preflight_sha256=self.preflight["release_preflight"]["preflight_sha256"],
                    )
                self.assertEqual(operations, ["head-object", "put-object"])

    def test_plan_builds_exact_reviewed_semantic_deltas_before_manifest(self) -> None:
        sources = [
            (row["key"], self.root / row["local_path"], row["local_path"])
            for row in self.locals
        ]

        class BaselineReader:
            def __init__(inner_self) -> None:
                inner_self.reads: list[tuple[str, dict[str, Any]]] = []

            def attest_identity(inner_self) -> dict[str, Any]:
                return provider_identity_attestation(ACCOUNT_ID, ENDPOINT_URL)

            def tool_attestation(inner_self) -> dict[str, Any]:
                return provider_tool_attestation(
                    version=AWS_CLI_VERSION,
                    archive_sha256=AWS_CLI_ARCHIVE_SHA256,
                )

            def read_reviewed_baseline_bytes(
                inner_self, key: str, preimage: dict[str, Any]
            ) -> bytes:
                inner_self.reads.append((key, copy.deepcopy(preimage)))
                return self.baseline[key]

        reader = BaselineReader()
        args = argparse.Namespace(
            publication_area_id=MOLS_AREA_ID,
            endpoint_url=ENDPOINT_URL,
            aws_cli_version=AWS_CLI_VERSION,
            aws_cli_archive_sha256=AWS_CLI_ARCHIVE_SHA256,
        )
        with mock.patch.object(
            self.module, "MolsConditionalWriter", return_value=reader
        ) as constructor, mock.patch.dict(
            self.module.os.environ, {"R2_ACCOUNT_ID": ACCOUNT_ID}, clear=False
        ):
            deltas = self.module._build_scoped_semantic_deltas(
                args, self.inventory, sources, self.locals
            )
        self.assertEqual(deltas, self.manifest["semantic_deltas"])
        self.assertEqual(
            [key for key, _preimage in reader.reads],
            list(MOLS_SEMANTIC_GLOBAL_KEYS),
        )
        self.assertEqual(
            [preimage for _key, preimage in reader.reads],
            [
                next(
                    row["remote_preimage"] for row in self.manifest["objects"]
                    if row["key"] == key
                )
                for key in MOLS_SEMANTIC_GLOBAL_KEYS
            ],
        )
        constructor.assert_called_once_with(
            endpoint_url=ENDPOINT_URL,
            account_id=ACCOUNT_ID,
            manifest_root=Path.cwd(),
            aws_cli_version=AWS_CLI_VERSION,
            aws_cli_archive_sha256=AWS_CLI_ARCHIVE_SHA256,
        )

    def test_plan_semantic_baseline_reads_are_if_match_and_stale_safe(self) -> None:
        for key in MOLS_SEMANTIC_GLOBAL_KEYS:
            row = next(row for row in self.manifest["objects"] if row["key"] == key)
            payload = self.baseline[key]
            operations: list[list[str]] = []

            def run(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
                operations.append(command)
                operation = command[2]
                if operation == "head-object":
                    return self._completed(
                        command, stdout=self._head_json(row["remote_preimage"])
                    )
                if operation == "get-object":
                    Path(command[-1]).write_bytes(payload)
                    return self._completed(command)
                raise AssertionError(command)

            with self.subTest(key=key, case="exact"), mock.patch.object(
                self.module.subprocess, "run", side_effect=run
            ):
                self.assertEqual(
                    self.adapter.read_reviewed_baseline_bytes(
                        key, row["remote_preimage"]
                    ),
                    payload,
                )
                self.assertEqual(
                    [command[2] for command in operations],
                    ["head-object", "get-object"],
                )
                get = operations[1]
                self.assertEqual(
                    get[get.index("--if-match") + 1], row["remote_preimage"]["etag"]
                )

            stale = copy.deepcopy(row["remote_preimage"])
            stale["etag"] = etag(f"stale-{key}")
            operations = []

            def stale_run(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
                operations.append(command)
                if command[2] == "head-object":
                    return self._completed(command, stdout=self._head_json(stale))
                raise AssertionError("stale baseline must fail before GET")

            with self.subTest(key=key, case="stale"), mock.patch.object(
                self.module.subprocess, "run", side_effect=stale_run
            ), self.assertRaises(ConditionalRequestRefused):
                self.adapter.read_reviewed_baseline_bytes(
                    key, row["remote_preimage"]
                )
            self.assertEqual([command[2] for command in operations], ["head-object"])

    def test_each_mutable_global_uses_if_match_and_full_preimage_binding(self) -> None:
        for key in MOLS_MUTABLE_GLOBAL_KEYS:
            row = next(row for row in self.manifest["objects"] if row["key"] == key)
            self.assertEqual(row["conditional_mode"], "IF_MATCH")
            payload = (self.root / row["local_path"]).read_bytes()
            get_payloads = [self.baseline[key], payload]
            written = {
                "etag": row["md5"], "bytes": row["bytes"], "sha256": row["sha256"],
            }
            head_count = 0
            commands: list[list[str]] = []
            request_metadata: dict[str, str] = {}

            def run(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
                nonlocal head_count, request_metadata
                commands.append(command)
                operation = command[2]
                if operation == "head-object":
                    head_count += 1
                    if head_count == 1:
                        return self._completed(
                            command, stdout=self._head_json(row["remote_preimage"])
                        )
                    return self._completed(
                        command, stdout=self._head_json(written, metadata=request_metadata)
                    )
                if operation == "put-object":
                    request_metadata = json.loads(command[command.index("--metadata") + 1])
                    return self._completed(command, stdout=json.dumps({
                        "ETag": '"' + written["etag"] + '"',
                        "UnexpectedFutureField": {"ignored": True},
                    }))
                if operation == "get-object":
                    Path(command[-1]).write_bytes(get_payloads.pop(0))
                    return self._completed(command)
                raise AssertionError(command)

            preflight_row = next(
                item for item in self.preflight["release_preflight"]["rows"]
                if item["key"] == key
            )
            with self.subTest(key=key), mock.patch.object(
                self.module.subprocess, "run", side_effect=run
            ):
                result = self.adapter.put_conditional(
                    row,
                    attempt_id=f"forward-{key}-if-match",
                    preflight_row=preflight_row,
                    preflight_sha256=self.preflight["release_preflight"]["preflight_sha256"],
                )
                put = next(command for command in commands if command[2] == "put-object")
                self.assertEqual(
                    put[put.index("--if-match") + 1], row["remote_preimage"]["etag"]
                )
                self.assertNotIn("--if-none-match", put)
                self.assertEqual(
                    request_metadata["trekdex-preimage-ref"],
                    canonical_sha256(row["remote_preimage"]),
                )
                self.assertEqual(request_metadata["trekdex-condition-mode"], "IF_MATCH")
                self.assertEqual(result["sha256"], row["sha256"])

    def test_delete_heads_exact_postimage_once_deletes_once_and_verifies_404(self) -> None:
        row = self._delete_row()
        payload, postimage = self._postimage(row["key"])
        commands: list[list[str]] = []
        head_count = 0

        def run(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
            nonlocal head_count
            commands.append(command)
            operation = command[2]
            if operation == "head-object":
                head_count += 1
                if head_count == 1:
                    return self._completed(command, stdout=self._head_json(postimage))
                return self._completed(command, code=1, stderr="An error occurred (404): Not Found")
            if operation == "get-object":
                Path(command[-1]).write_bytes(payload)
                return self._completed(command)
            if operation == "delete-object":
                return self._completed(command)
            raise AssertionError(command)

        with mock.patch.object(self.module.subprocess, "run", side_effect=run):
            result = self.adapter.rollback_delete_created(
                row,
                postimage=postimage,
                attempt_id="rollback-delete-test",
                scoped_preflight_sha256=self.preflight["scoped_preflight_sha256"],
            )
        deletes = [command for command in commands if command[2] == "delete-object"]
        self.assertEqual(len(deletes), 1)
        delete = deletes[0]
        self.assertEqual(delete[delete.index("--key") + 1], row["key"])
        self.assertEqual(
            delete,
            [
                "aws", "s3api", "delete-object", "--bucket", BUCKET,
                "--key", row["key"], "--endpoint-url", ENDPOINT_URL,
                "--output", "json",
            ],
        )
        self.assertTrue(result["verified_absent"])

    def test_forged_rollback_row_or_preflight_digest_has_zero_provider_calls(self) -> None:
        row = self._delete_row()
        _payload, postimage = self._postimage(row["key"])
        for name, candidate, digest in (
            ("row", {**row, "expected_postimage": {**row["expected_postimage"], "sha256": "0" * 64}}, self.preflight["scoped_preflight_sha256"]),
            ("preflight", row, "0" * 64),
        ):
            with self.subTest(name=name), mock.patch.object(
                self.module.subprocess, "run"
            ) as run:
                with self.assertRaises(ManifestError):
                    self.adapter.rollback_delete_created(
                        candidate,
                        postimage=postimage,
                        attempt_id="forged-rollback",
                        scoped_preflight_sha256=digest,
                    )
                run.assert_not_called()

    def test_head_mismatch_missing_hash_404_and_ambiguity_never_delete(self) -> None:
        row = self._delete_row()
        payload, postimage = self._postimage(row["key"])
        cases = ("etag-mismatch", "missing-hash", "missing", "ambiguous")
        for case in cases:
            commands: list[list[str]] = []

            def run(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
                commands.append(command)
                operation = command[2]
                if operation == "head-object":
                    if case == "missing":
                        return self._completed(command, code=1, stderr="An error occurred (404): Not Found")
                    if case == "ambiguous":
                        return self._completed(command, code=1, stderr="transport failure")
                    identity = copy.deepcopy(postimage)
                    if case == "etag-mismatch":
                        identity["etag"] = etag("different")
                    return self._completed(
                        command,
                        stdout=self._head_json(identity, checksum=case != "missing-hash"),
                    )
                if operation == "get-object":
                    # Missing HEAD hash must fall back to an exact GET. This
                    # simulated ambiguous GET proves deletion stays disabled.
                    return self._completed(command, code=1, stderr="ambiguous GET failure")
                raise AssertionError("delete must not be reached")

            with self.subTest(case=case), mock.patch.object(
                self.module.subprocess, "run", side_effect=run
            ):
                with self.assertRaises(ManifestError):
                    self.adapter.rollback_delete_created(
                        row,
                        postimage=postimage,
                        attempt_id="rollback-no-delete",
                        scoped_preflight_sha256=self.preflight["scoped_preflight_sha256"],
                    )
                self.assertFalse(any(command[2] == "delete-object" for command in commands))

    def test_missing_head_checksum_uses_exact_get_before_delete(self) -> None:
        row = self._delete_row()
        payload, postimage = self._postimage(row["key"])
        operations: list[str] = []
        head_count = 0

        def run(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
            nonlocal head_count
            operation = command[2]
            operations.append(operation)
            if operation == "head-object":
                head_count += 1
                if head_count == 1:
                    return self._completed(command, stdout=self._head_json(postimage, checksum=False))
                return self._completed(command, code=1, stderr="404 Not Found")
            if operation == "get-object":
                Path(command[-1]).write_bytes(payload)
                return self._completed(command)
            if operation == "delete-object":
                return self._completed(command)
            raise AssertionError(command)

        with mock.patch.object(self.module.subprocess, "run", side_effect=run):
            self.adapter.rollback_delete_created(
                row,
                postimage=postimage,
                attempt_id="rollback-get-fallback",
                scoped_preflight_sha256=self.preflight["scoped_preflight_sha256"],
            )
        self.assertEqual(operations, ["head-object", "get-object", "delete-object", "head-object"])

    def test_indeterminate_delete_is_attempted_once_and_never_retried(self) -> None:
        row = self._delete_row()
        payload, postimage = self._postimage(row["key"])
        delete_calls = 0

        def run(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
            nonlocal delete_calls
            operation = command[2]
            if operation == "head-object":
                return self._completed(command, stdout=self._head_json(postimage))
            if operation == "get-object":
                Path(command[-1]).write_bytes(payload)
                return self._completed(command)
            if operation == "delete-object":
                delete_calls += 1
                raise subprocess.TimeoutExpired(command, 180)
            raise AssertionError(command)

        with mock.patch.object(self.module.subprocess, "run", side_effect=run):
            with self.assertRaisesRegex(ManifestError, "indeterminate"):
                self.adapter.rollback_delete_created(
                    row,
                    postimage=postimage,
                    attempt_id="rollback-timeout",
                    scoped_preflight_sha256=self.preflight["scoped_preflight_sha256"],
                )
        self.assertEqual(delete_calls, 1)

    def test_each_mutable_global_restore_is_if_match_on_exact_post_publish_identity(self) -> None:
        for key in MOLS_MUTABLE_GLOBAL_KEYS:
            row = next(
                row for row in self.preflight["rollback_manifest"]["objects"]
                if row["key"] == key
            )
            candidate, postimage = self._postimage(key)
            baseline = (self.root / row["baseline_object"]["local_path"]).read_bytes()
            restored = {
                "etag": row["baseline_preimage"]["etag"],
                "bytes": len(baseline),
                "sha256": hashlib.sha256(baseline).hexdigest(),
            }
            attempt_id = f"rollback-{key}-if-match"
            metadata = {
                "trekdex-sha256": restored["sha256"],
                "trekdex-source-sha": COMMIT,
                "trekdex-manifest-sha256": self.manifest["manifest_sha256"],
                "trekdex-attempt-id": attempt_id,
                "trekdex-preimage-ref": canonical_sha256({
                    field: postimage[field] for field in ("etag", "bytes", "sha256")
                }),
                "trekdex-condition-mode": "IF_MATCH",
            }
            head_documents = [
                self._head_json(postimage),
                self._head_json(restored, metadata=metadata),
            ]
            get_payloads = [candidate, baseline]
            commands: list[list[str]] = []

            def run(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
                commands.append(command)
                operation = command[2]
                if operation == "head-object":
                    return self._completed(command, stdout=head_documents.pop(0))
                if operation == "get-object":
                    Path(command[-1]).write_bytes(get_payloads.pop(0))
                    return self._completed(command)
                if operation == "put-object":
                    return self._completed(command, stdout=json.dumps({
                        "ETag": '"' + restored["etag"] + '"',
                    }))
                raise AssertionError(command)

            with self.subTest(key=key), mock.patch.object(
                self.module.subprocess, "run", side_effect=run
            ):
                result = self.adapter.rollback_put_conditional(
                    row,
                    postimage=postimage,
                    attempt_id=attempt_id,
                    scoped_preflight_sha256=self.preflight["scoped_preflight_sha256"],
                )
                puts = [command for command in commands if command[2] == "put-object"]
                self.assertEqual(len(puts), 1)
                put = puts[0]
                self.assertEqual(put[put.index("--key") + 1], key)
                self.assertEqual(put[put.index("--if-match") + 1], postimage["etag"])
                self.assertNotIn("--if-none-match", put)
                self.assertEqual(result["etag"], row["baseline_preimage"]["etag"])


class TestKeyContract(unittest.TestCase):
    def test_version_and_exact_global_registry(self) -> None:
        self.assertEqual(CONTRACT_VERSION, "trekdex-r2-release-contract-v5")
        self.assertEqual(
            tuple(GLOBAL_OBJECTS),
            ("index.json", "parking.json", "trail-search.json", "trail-shapes.json"),
        )
        self.assertEqual(len(set(GLOBAL_OBJECTS)), 4)
        for key in GLOBAL_OBJECTS:
            self.assertEqual(classify_key(key), KeyClass.GLOBAL)

    def test_total_exclusive_hostile_classification(self) -> None:
            registry = {"alpha-trail-az", "café-az"}
            quarantine = quarantine_key(
                "receipt-001", "alpha-trail-az.json",
                authoritative_area_ids=registry,
            )
            expected = {
                "alpha-trail-az.json": KeyClass.AREA_GEOM,
                "silhouettes/alpha-trail-az.json": KeyClass.AREA_SILHOUETTE,
                "index.json": KeyClass.GLOBAL,
                quarantine: KeyClass.QUARANTINE,
                "_trekdex-control/v2/receipts/x.json": KeyClass.CONTROL,
                "future-global.json": KeyClass.UNKNOWN,
                "catalog-us.json": KeyClass.UNKNOWN,
                "silhouettes/catalog-us.json": KeyClass.UNKNOWN,
                "silhouettes/index.json": KeyClass.UNKNOWN,
                "../alpha-trail-az.json": KeyClass.UNKNOWN,
                "Alpha-Trail-AZ.json": KeyClass.UNKNOWN,
                "alpha_trail_az.json": KeyClass.UNKNOWN,
                "alpha-trail-az.json/child": KeyClass.UNKNOWN,
                "alpha-trail-az.json\n": KeyClass.UNKNOWN,
                "": KeyClass.UNKNOWN,
                "\u0000": KeyClass.UNKNOWN,
                "cafe\u0301-az.json": KeyClass.UNKNOWN,
                "café-az.json": KeyClass.AREA_GEOM,
            }
            for key, key_class in expected.items():
                with self.subTest(key=repr(key)):
                    self.assertEqual(
                        classify_key(key, authoritative_area_ids=registry), key_class
                    )
            self.assertEqual(
                classify_key("alpha-trail-az.json"), KeyClass.UNKNOWN,
                "filename shape alone must never authorize an area key",
            )
            self.assertEqual(
                classify_key("catalog-us.json", authoritative_area_ids={"catalog-us"}),
                KeyClass.AREA_GEOM,
                "only explicit registry membership may classify an area-shaped key",
            )

    def test_quarantine_encoding_is_injective_for_authoritative_ids(self) -> None:
        index = json.loads((ROOT / "public/areas/index.json").read_text())
        source_keys = [
            key
            for row in index
            for key in (f"{row[0]}.json", f"silhouettes/{row[0]}.json")
        ]
        registry = {row[0] for row in index}
        destinations = [
            quarantine_key("receipt-001", key, authoritative_area_ids=registry)
            for key in source_keys
        ]
        self.assertEqual(len(destinations), len(set(destinations)))
        self.assertTrue(all(classify_key(key) is KeyClass.QUARANTINE for key in destinations))

    def test_property_campaign_is_deterministic_and_unknown_never_area(self) -> None:
        rng = random.Random(0xC001)
        alphabet = "abcdefghijklmnopqrstuvwxyz0123456789-_/..ABCDEFGHIJKLMNOPQRSTUVWXYZ\n\t"
        totals = {member: 0 for member in KeyClass}
        for _ in range(10_000):
            key = "".join(rng.choice(alphabet) for _ in range(rng.randrange(0, 45)))
            one = classify_key(key)
            two = classify_key(key)
            self.assertIs(one, two)
            self.assertIn(one, KeyClass)
            totals[one] += 1
            if one is KeyClass.UNKNOWN:
                self.assertNotIn(one, {KeyClass.AREA_GEOM, KeyClass.AREA_SILHOUETTE})
        self.assertEqual(sum(totals.values()), 10_000)

    def test_every_current_index_area_has_contract_key(self) -> None:
        index = json.loads((ROOT / "public/areas/index.json").read_text())
        ids = [row[0] for row in index]
        self.assertGreater(len(ids), 1_000)
        bad = [
            area_id for area_id in ids
            if classify_key(f"{area_id}.json", authoritative_area_ids=set(ids))
            is not KeyClass.AREA_GEOM
        ]
        self.assertEqual(bad, [])


class TestInventoryContract(unittest.TestCase):
    def test_complete_canonical_receipt_binds_identity_tokens_objects_and_anchor(self) -> None:
        receipt = receipt_for()
        validated = validate_inventory_receipt(
            receipt,
            expected_bucket=BUCKET,
            expected_account_identity_ref=ACCOUNT,
            expected_endpoint_identity_ref=ENDPOINT,
            expected_anchor_ref=ANCHOR,
        )
        self.assertEqual(len(validated), receipt["authoritative_denominator"])
        self.assertEqual(receipt["protected_keys"], list(GLOBAL_OBJECTS))
        self.assertEqual(receipt["page_count"], 2)
        self.assertNotIn("opaque-2", json.dumps(receipt))
        self.assertRegex(receipt["pages"][0]["next_token_ref"], r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(receipt["receipt_sha256"], canonical_sha256(receipt, omit={"receipt_sha256"}))
        self.assertEqual(receipt, strict_json_loads(json.dumps(receipt), "round trip"))

    def test_persisted_receipt_rejects_empty_repeated_cyclic_and_typed_page_evidence(self) -> None:
        base = receipt_for()
        token_a = identity_ref("inventory-token", "round34-token-a")
        token_b = identity_ref("inventory-token", "round34-token-b")

        def row(number: int, request: str | None, following: str | None,
                truncated: bool, count: int) -> dict[str, Any]:
            return {
                "number": number,
                "request_token_ref": request,
                "next_token_ref": following,
                "is_truncated": truncated,
                "object_count": count,
            }

        def seal(pages: list[dict[str, Any]]) -> dict[str, Any]:
            receipt = copy.deepcopy(base)
            receipt["pages"] = pages
            receipt["page_count"] = len(pages)
            receipt["receipt_sha256"] = canonical_sha256(
                receipt, omit={"receipt_sha256"}
            )
            return receipt

        valid_pages = [
            row(1, None, token_a, True, 2),
            row(2, token_a, token_b, True, 1),
            row(3, token_b, None, False, 3),
        ]
        self.assertEqual(len(validate_inventory_receipt(seal(valid_pages))), 6)

        empty_continuation = [
            row(1, None, token_a, True, 2),
            row(2, token_a, token_b, True, 0),
            row(3, token_b, None, False, 4),
        ]
        self_loop = [
            row(1, None, token_a, True, 2),
            row(2, token_a, token_a, True, 1),
            row(3, token_a, None, False, 3),
        ]
        cycle = [
            row(1, None, token_a, True, 1),
            row(2, token_a, token_b, True, 1),
            row(3, token_b, token_a, True, 1),
            row(4, token_a, None, False, 3),
        ]
        terminal_truncation = [
            row(1, None, token_a, True, 2),
            row(2, token_a, token_b, True, 4),
        ]
        mutations: list[tuple[str, dict[str, Any]]] = [
            ("empty-continuation", seal(empty_continuation)),
            ("self-loop-and-repeated-request", seal(self_loop)),
            ("token-cycle", seal(cycle)),
            ("terminal-truncation-unconsumed-token", seal(terminal_truncation)),
        ]
        for name, page_index, field, value in (
            ("integer-truncation", 0, "is_truncated", 1),
            ("boolean-page-count", 1, "object_count", True),
            ("boolean-page-number", 0, "number", True),
        ):
            pages = copy.deepcopy(valid_pages)
            pages[page_index][field] = value
            mutations.append((name, seal(pages)))

        one_page = seal([row(1, None, None, False, 6)])
        one_page["page_count"] = True
        one_page["receipt_sha256"] = canonical_sha256(
            one_page, omit={"receipt_sha256"}
        )
        mutations.append(("boolean-receipt-page-count", one_page))

        area_count = seal(copy.deepcopy(valid_pages))
        area_count["area_registry_count"] = True
        area_count["receipt_sha256"] = canonical_sha256(
            area_count, omit={"receipt_sha256"}
        )
        mutations.append(("boolean-area-registry-count", area_count))

        class_count = seal(copy.deepcopy(valid_pages))
        class_count["class_counts"][KeyClass.AREA_GEOM.value] = True
        class_count["receipt_sha256"] = canonical_sha256(
            class_count, omit={"receipt_sha256"}
        )
        mutations.append(("boolean-class-count", class_count))

        for name, hostile in mutations:
            with self.subTest(name=name):
                with self.assertRaises(InventoryError):
                    validate_inventory_receipt(hostile, expected_bucket=BUCKET)

        over_limit = copy.deepcopy(base)
        over_limit["pages"] = [{}] * (MAX_INVENTORY_PAGES + 1)
        over_limit["page_count"] = len(over_limit["pages"])
        over_limit["receipt_sha256"] = canonical_sha256(
            over_limit, omit={"receipt_sha256"}
        )
        with self.assertRaisesRegex(InventoryError, "page closure"):
            validate_inventory_receipt(over_limit, expected_bucket=BUCKET)

    def test_synthetic_identity_refs_are_domain_separated(self) -> None:
        self.assertEqual(identity_ref("account", "private-value"), identity_ref("account", "private-value"))
        self.assertNotEqual(identity_ref("account", "private-value"), identity_ref("endpoint", "private-value"))
        self.assertNotIn("private-value", identity_ref("account", "private-value"))

    def test_duplicate_json_keys_are_rejected(self) -> None:
        with self.assertRaisesRegex(InventoryError, "duplicate JSON key"):
            strict_json_loads('{"a":1,"a":2}', "hostile")

    def test_named_fault_matrix_is_complete_and_zero_effect(self) -> None:
        required = {
            "access-denied", "subprocess-nonzero", "timeout", "empty-stdout",
            "malformed-json", "duplicate-json-key", "empty-page-with-continuation",
            "wrong-bucket", "wrong-account", "wrong-endpoint", "wrong-token",
            "repeated-token", "token-cycle", "truncation-mismatch", "terminal-token",
            "key-count-mismatch", "denominator-mismatch", "duplicate-object",
            "malformed-preimage", "missing-protected-key", "extra-unknown-key",
            "changed-receipt-after-review", "wrong-anchor", "stale-preimage",
            "conditional-mode-mismatch",
        }
        self.assertEqual(set(INVENTORY_FAULT_NAMES), required)

        base = {None: page([remote(key) for key in GLOBAL_OBJECTS])}
        sources: dict[str, PageSource] = {
            "access-denied": PageSource(base, PermissionError("denied")),
            "subprocess-nonzero": PageSource(base, subprocess.CalledProcessError(2, ["fake"])),
            "timeout": PageSource(base, subprocess.TimeoutExpired(["fake"], 1)),
            "empty-stdout": PageSource({None: ""}),
            "malformed-json": PageSource({None: "{"}),
            "duplicate-json-key": PageSource({None: '{"Name":"trekdex-areas","Name":"other"}'}),
            "empty-page-with-continuation": PageSource({None: page([], next_token="next", truncated=True), "next": page([remote(key) for key in GLOBAL_OBJECTS], token="next")}),
            "wrong-bucket": PageSource({None: page([remote(key) for key in GLOBAL_OBJECTS], bucket="other")}),
            "wrong-token": PageSource({None: page([remote(key) for key in GLOBAL_OBJECTS[:2]], next_token="next", truncated=True), "next": page([remote(key) for key in GLOBAL_OBJECTS[2:]], token="wrong")}),
            "repeated-token": PageSource({None: page([remote(key) for key in GLOBAL_OBJECTS[:2]], next_token="next", truncated=True), "next": page([remote(key) for key in GLOBAL_OBJECTS[2:]], token="next", next_token="next", truncated=True)}),
            "token-cycle": PageSource({None: page([remote(key) for key in GLOBAL_OBJECTS[:1]], next_token="a", truncated=True), "a": page([remote(GLOBAL_OBJECTS[1])], token="a", next_token="b", truncated=True), "b": page([remote(key) for key in GLOBAL_OBJECTS[2:]], token="b", next_token="a", truncated=True)}),
            "truncation-mismatch": PageSource({None: page([remote(key) for key in GLOBAL_OBJECTS], truncated=True)}),
            "terminal-token": PageSource({None: page([remote(key) for key in GLOBAL_OBJECTS], next_token="unexpected")}),
            "key-count-mismatch": PageSource({None: {**page([remote(key) for key in GLOBAL_OBJECTS]), "KeyCount": 99}}),
            "duplicate-object": PageSource({None: page([remote(key) for key in GLOBAL_OBJECTS] + [remote(GLOBAL_OBJECTS[0])])}),
            "malformed-preimage": PageSource({None: page([{**remote(GLOBAL_OBJECTS[0]), "ETag": "bad etag"}] + [remote(key) for key in GLOBAL_OBJECTS[1:]])}),
            "missing-protected-key": PageSource({None: page([remote(key) for key in GLOBAL_OBJECTS[:-1]])}),
            "extra-unknown-key": PageSource({None: page([remote(key) for key in GLOBAL_OBJECTS] + [remote("future-global.json")])}),
        }
        for name, source in sources.items():
            with self.subTest(name=name):
                with self.assertRaises(InventoryError):
                    fetch_complete_inventory(
                        source,
                        bucket=BUCKET,
                        account_identity_ref=ACCOUNT,
                        endpoint_identity_ref=ENDPOINT,
                        authoritative_denominator=len(GLOBAL_OBJECTS),
                        authoritative_area_ids={"alpha-trail-az"},
                        anchor_state="ASSERTED_PENDING_APPROVAL",
                        anchor_ref=ANCHOR,
                    )
                self.assertLessEqual(len(source.calls), 3)

        receipt = receipt_for()
        mutations = {
            "wrong-account": ("account_identity_ref", "sha256:" + "0" * 64),
            "wrong-endpoint": ("endpoint_identity_ref", "sha256:" + "0" * 64),
            "denominator-mismatch": ("authoritative_denominator", receipt["authoritative_denominator"] + 1),
            "changed-receipt-after-review": ("receipt_sha256", "0" * 64),
            "wrong-anchor": ("anchor_ref", "sha256:" + "0" * 64),
        }
        for name, (field, value) in mutations.items():
            hostile = copy.deepcopy(receipt)
            hostile[field] = value
            with self.subTest(name=name):
                with self.assertRaises(InventoryError):
                    validate_inventory_receipt(hostile, expected_bucket=BUCKET)

    def test_future_area_shaped_global_never_enters_inventory_or_quarantine(self) -> None:
        keys = [*GLOBAL_OBJECTS, "catalog-us.json"]
        with self.assertRaises(InventoryError):
            receipt_for(keys, area_registry={"alpha-trail-az"})
        self.assertEqual(
            classify_key("catalog-us.json", authoritative_area_ids={"alpha-trail-az"}),
            KeyClass.UNKNOWN,
        )

    def test_receipt_is_byte_deterministic(self) -> None:
        first = receipt_for()
        for _ in range(50):
            self.assertEqual(receipt_for(), first)
            self.assertEqual(canonical_sha256(receipt_for()), canonical_sha256(first))


class TestManifestAndRelease(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.files: dict[str, Path] = {}
        keys = [*GLOBAL_OBJECTS, "alpha-trail-az.json", "silhouettes/alpha-trail-az.json"]
        for key in keys:
            path = self.root / key.replace("/", "--")
            path.write_bytes(("local:" + key).encode())
            self.files[key] = path
        self.receipt = receipt_for(keys)
        self.locals = [local_object(key, self.files[key]) for key in keys]
        self.manifest = build_upload_manifest(
            self.receipt,
            self.locals,
            source_sha=COMMIT,
            source_tree=TREE,
            workflow_sha256=WORKFLOW,
            root_approval_ref=APPROVAL,
            prior_anchor_ref=ANCHOR,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_manifest_is_complete_exact_canonical_and_condition_bound(self) -> None:
        validated = validate_upload_manifest(self.manifest, inventory_receipt=self.receipt)
        rows = validated["objects"]
        self.assertEqual([row["key"] for row in rows], sorted(self.files))
        self.assertEqual({row["key"] for row in rows if row["key_class"] == "GLOBAL"}, set(GLOBAL_OBJECTS))
        self.assertEqual(validated["object_count"], 6)
        population = validated["producer_population"]
        self.assertEqual(population["object_count"], 6)
        self.assertEqual(
            [row["key"] for row in population["objects"]],
            sorted(self.files),
        )
        self.assertEqual(
            validated["producer_population_sha256"], population["receipt_sha256"]
        )
        self.assertTrue(all(row["conditional_mode"] == "IF_MATCH" for row in rows))
        self.assertTrue(all(row["remote_preimage"] for row in rows))
        self.assertEqual(validated["manifest_sha256"], canonical_sha256(validated, omit={"manifest_sha256"}))

    def test_leaf_skip_requires_exact_sha256_and_bytes_not_etag_md5(self) -> None:
        key = "alpha-trail-az.json"
        local = next(row for row in self.locals if row["key"] == key)

        collision = copy.deepcopy(self.receipt)
        remote_row = next(row for row in collision["objects"] if row["key"] == key)
        remote_row["preimage"]["etag"] = local["md5"]
        remote_row["preimage"]["sha256"] = "0" * 64
        remote_row["preimage"]["bytes"] = local["bytes"]
        collision["receipt_sha256"] = canonical_sha256(collision, omit={"receipt_sha256"})
        collision_manifest = build_upload_manifest(
            collision, self.locals, source_sha=COMMIT, source_tree=TREE,
            workflow_sha256=WORKFLOW, root_approval_ref=APPROVAL,
            prior_anchor_ref=ANCHOR,
        )
        self.assertIn(key, {row["key"] for row in collision_manifest["objects"]})

        exact = copy.deepcopy(self.receipt)
        remote_row = next(row for row in exact["objects"] if row["key"] == key)
        remote_row["preimage"]["etag"] = etag("multipart-or-unrelated-etag")
        remote_row["preimage"]["sha256"] = local["sha256"]
        remote_row["preimage"]["bytes"] = local["bytes"]
        exact["receipt_sha256"] = canonical_sha256(exact, omit={"receipt_sha256"})
        exact_manifest = build_upload_manifest(
            exact, self.locals, source_sha=COMMIT, source_tree=TREE,
            workflow_sha256=WORKFLOW, root_approval_ref=APPROVAL,
            prior_anchor_ref=ANCHOR,
        )
        self.assertNotIn(key, {row["key"] for row in exact_manifest["objects"]})

    def test_new_leaf_uses_if_none_match_but_globals_are_never_optional(self) -> None:
            keys = [*GLOBAL_OBJECTS, "alpha-trail-az.json", "silhouettes/alpha-trail-az.json"]
            receipt = receipt_for(keys, area_registry={"alpha-trail-az", "new-area-ut"})
            new_geom = self.root / "new-area-ut.json"
            new_silhouette = self.root / "new-area-ut-silhouette.json"
            new_geom.write_text('{"id":"new-area-ut"}\n')
            new_silhouette.write_text('{"id":"new-area-ut"}\n')
            locals_ = self.locals + [
                local_object("new-area-ut.json", new_geom),
                local_object("silhouettes/new-area-ut.json", new_silhouette),
            ]
            manifest = build_upload_manifest(
                receipt, locals_, source_sha=COMMIT, source_tree=TREE,
                workflow_sha256=WORKFLOW, root_approval_ref=APPROVAL,
                prior_anchor_ref=ANCHOR,
            )
            rows = {row["key"]: row for row in manifest["objects"]}
            for key in ("new-area-ut.json", "silhouettes/new-area-ut.json"):
                self.assertEqual(rows[key]["conditional_mode"], "IF_NONE_MATCH")
                self.assertIsNone(rows[key]["remote_preimage"])
            self.assertEqual(set(GLOBAL_OBJECTS), {key for key in GLOBAL_OBJECTS if key in rows})

    def test_manifest_mutations_fail_closed(self) -> None:
        mutations: list[tuple[str, Any]] = []
        for field, value in (
            ("source_sha", "A" * 40), ("source_tree", "2" * 39),
            ("workflow_sha256", "f" * 63), ("root_approval_ref", "mutable-main"),
            ("prior_anchor_ref", ""), ("manifest_sha256", "0" * 64),
        ):
            def mutate(document: dict[str, Any], f: str = field, v: Any = value) -> None:
                document[f] = v
            mutations.append((field, mutate))

        def remove_changed_leaf(document: dict[str, Any]) -> None:
            document["objects"] = [
                row for row in document["objects"] if row["key"] != "alpha-trail-az.json"
            ]
            document["object_count"] = len(document["objects"])

        def remove_producer(document: dict[str, Any]) -> None:
            population = document["producer_population"]
            population["objects"] = [
                row for row in population["objects"]
                if row["key"] != "alpha-trail-az.json"
            ]
            population["object_count"] = len(population["objects"])
            population["object_keys_sha256"] = canonical_sha256(
                [row["key"] for row in population["objects"]]
            )
            population["objects_sha256"] = canonical_sha256(population["objects"])
            population["receipt_sha256"] = canonical_sha256(
                population, omit={"receipt_sha256"}
            )
            document["producer_population_sha256"] = population["receipt_sha256"]
            document["objects"] = [
                row for row in document["objects"]
                if row["key"] != "alpha-trail-az.json"
            ]
            document["object_count"] = len(document["objects"])

        def duplicate_producer_path(document: dict[str, Any]) -> None:
            population = document["producer_population"]
            population["objects"][1]["local_path"] = population["objects"][0]["local_path"]
            population["objects_sha256"] = canonical_sha256(population["objects"])
            population["receipt_sha256"] = canonical_sha256(
                population, omit={"receipt_sha256"}
            )
            document["producer_population_sha256"] = population["receipt_sha256"]

        mutations.extend([
            ("missing-changed-leaf", remove_changed_leaf),
            ("missing-producer", remove_producer),
            ("duplicate-producer-path", duplicate_producer_path),
            ("duplicate-row", lambda d: d["objects"].append(copy.deepcopy(d["objects"][0]))),
            ("wrong-order", lambda d: d["objects"].reverse()),
            ("wrong-bytes", lambda d: d["objects"][0].__setitem__("bytes", 0)),
            ("wrong-local-hash", lambda d: d["objects"][0].__setitem__("sha256", "0" * 64)),
            ("wrong-preimage", lambda d: d["objects"][0]["remote_preimage"].__setitem__("etag", etag("stale"))),
            ("conditional-mode-mismatch", lambda d: d["objects"][0].__setitem__("conditional_mode", "IF_NONE_MATCH")),
        ])
        for name, mutation in mutations:
            hostile = copy.deepcopy(self.manifest)
            mutation(hostile)
            if name != "manifest_sha256":
                hostile["manifest_sha256"] = canonical_sha256(hostile, omit={"manifest_sha256"})
            with self.subTest(name=name):
                with self.assertRaises(ManifestError):
                    validate_upload_manifest(hostile, inventory_receipt=self.receipt)

    def test_split_index_and_joint_population_omission_fail(self) -> None:
        population = copy.deepcopy(self.manifest["producer_population"])
        population["published_area_ids"] = []
        population["published_area_count"] = 0
        population["published_area_ids_sha256"] = canonical_sha256([])
        population["objects"] = [
            row for row in population["objects"] if row["key"] in GLOBAL_OBJECTS
        ]
        population["object_count"] = len(population["objects"])
        population["object_keys_sha256"] = canonical_sha256(
            [row["key"] for row in population["objects"]]
        )
        population["objects_sha256"] = canonical_sha256(population["objects"])
        population["receipt_sha256"] = canonical_sha256(
            population, omit={"receipt_sha256"}
        )
        forged = copy.deepcopy(self.manifest)
        forged["producer_population"] = population
        forged["producer_population_sha256"] = population["receipt_sha256"]
        forged["objects"] = [row for row in forged["objects"] if row["key"] in GLOBAL_OBJECTS]
        forged["object_count"] = len(forged["objects"])
        forged["manifest_sha256"] = canonical_sha256(forged, omit={"manifest_sha256"})
        with self.assertRaises(ManifestError):
            validate_upload_manifest(forged, inventory_receipt=self.receipt)

    def test_release_complete_success_binds_every_result_and_transaction(self) -> None:
        writer = FakeWriter()
        checkpoints: list[dict[str, Any]] = []
        receipt = execute_release(self.manifest, writer, checkpoint=lambda d: checkpoints.append(copy.deepcopy(d)))
        self.assertEqual(receipt["terminal_state"], TerminalState.COMPLETE_SUCCESS.value)
        self.assertEqual(len(writer.calls), self.manifest["object_count"])
        self.assertEqual(len(receipt["provider_results"]), self.manifest["object_count"])
        self.assertEqual(len(receipt["provider_transaction_ids"]), self.manifest["object_count"])
        self.assertEqual(receipt["manifest_sha256"], self.manifest["manifest_sha256"])
        self.assertEqual(receipt["source_sha"], COMMIT)
        self.assertEqual(receipt["source_tree"], TREE)
        self.assertEqual(receipt["workflow_sha256"], WORKFLOW)
        self.assertEqual(receipt["root_approval_ref"], APPROVAL)
        self.assertFalse(receipt["committed"])
        self.assertFalse(receipt["pushed"])
        self.assertFalse(receipt["synchronization_dispatched"])
        self.assertEqual(receipt["receipt_sha256"], canonical_sha256(receipt, omit={"receipt_sha256"}))
        self.assertEqual(len(writer.preflight_calls), 1)
        self.assertEqual(
            writer.preflight_calls[0],
            [row["key"] for row in self.manifest["objects"]],
        )
        self.assertEqual(
            receipt["release_preflight_sha256"],
            receipt["release_preflight"]["preflight_sha256"],
        )
        self.assertTrue(all(row["eligible"] for row in receipt["release_preflight"]["rows"]))
        self.assertGreaterEqual(len(checkpoints), len(writer.calls))

    def test_r38_h_002_later_determinable_refusal_prevents_first_effect(self) -> None:
        new_area = "aaa-area-aa"
        inventory = receipt_for(
            [*GLOBAL_OBJECTS, "alpha-trail-az.json", "silhouettes/alpha-trail-az.json"],
            area_registry={"alpha-trail-az", new_area},
        )
        new_geom = self.root / f"{new_area}.json"
        new_silhouette = self.root / f"{new_area}-silhouette.json"
        new_geom.write_text('{"id":"aaa-area-aa"}\n')
        new_silhouette.write_text('{"id":"aaa-area-aa"}\n')
        local_rows = [
            *self.locals,
            local_object(f"{new_area}.json", new_geom),
            local_object(f"silhouettes/{new_area}.json", new_silhouette),
        ]
        manifest = build_upload_manifest(
            inventory,
            local_rows,
            source_sha=COMMIT,
            source_tree=TREE,
            workflow_sha256=WORKFLOW,
            root_approval_ref=APPROVAL,
            prior_anchor_ref=ANCHOR,
        )
        self.assertEqual(manifest["objects"][0]["conditional_mode"], "IF_NONE_MATCH")
        later_index = next(
            index
            for index, row in enumerate(manifest["objects"])
            if index > 0 and row["conditional_mode"] == "IF_MATCH"
        )
        writer = FakeWriter(refuse_preflight_at=later_index)
        receipt = execute_release(manifest, writer)
        self.assertEqual(receipt["terminal_state"], TerminalState.SAFE_ABORT.value)
        self.assertEqual(receipt["error_boundary"], "PREFLIGHT_REFUSAL")
        self.assertEqual(receipt["attempted_effects"], 0)
        self.assertEqual(receipt["observed_effects"], 0)
        self.assertEqual(writer.calls, [])
        self.assertEqual(len(writer.preflight_calls), 1)
        self.assertEqual(len(writer.preflight_calls[0]), manifest["object_count"])
        self.assertTrue(receipt["release_preflight"]["rows"][0]["eligible"])
        self.assertFalse(receipt["release_preflight"]["rows"][later_index]["eligible"])

    def test_r38_h_002_success_schema_is_validated_before_any_effect(self) -> None:
        writer = FakeWriter(malformed_success_schema=True)
        receipt = execute_release(self.manifest, writer)
        self.assertEqual(receipt["terminal_state"], TerminalState.SAFE_ABORT.value)
        self.assertEqual(receipt["error_boundary"], "COMPLETE_PREFLIGHT")
        self.assertEqual(receipt["attempted_effects"], 0)
        self.assertEqual(writer.calls, [])
        self.assertIsNone(receipt["release_preflight"])

    def test_release_preflight_snapshot_mutations_fail_closed(self) -> None:
        receipt = execute_release(self.manifest, FakeWriter())
        snapshot = receipt["release_preflight"]
        self.assertEqual(
            validate_release_preflight(
                snapshot, self.manifest, execution_id=receipt["execution_id"]
            ),
            snapshot,
        )
        mutations = (
            ("missing-row", lambda d: d["rows"].pop()),
            ("reordered", lambda d: d["rows"].reverse()),
            ("condition", lambda d: d["rows"][0].__setitem__("observed_preimage", None)),
            ("schema", lambda d: d.__setitem__("adapter_success_schema", ["key"])),
            ("capability", lambda d: d.__setitem__("adapter_success_schema_capable", False)),
            ("manifest", lambda d: d.__setitem__("manifest_sha256", "0" * 64)),
        )
        for name, mutation in mutations:
            hostile = copy.deepcopy(snapshot)
            mutation(hostile)
            hostile["preflight_sha256"] = canonical_sha256(
                hostile, omit={"preflight_sha256"}
            )
            with self.subTest(name=name):
                with self.assertRaises(ManifestError):
                    validate_release_preflight(
                        hostile, self.manifest, execution_id=receipt["execution_id"]
                    )

    def test_release_terminal_receipt_mutations_fail_closed(self) -> None:
        receipt = execute_release(self.manifest, FakeWriter())
        self.assertEqual(validate_release_receipt(receipt, self.manifest), receipt)
        mutations: list[tuple[str, Any]] = [
            ("terminal-state", lambda d: d.__setitem__("terminal_state", "SAFE_ABORT")),
            ("attempted-count", lambda d: d.__setitem__("attempted_effects", d["attempted_effects"] - 1)),
            ("observed-count", lambda d: d.__setitem__("observed_effects", 0)),
            ("source", lambda d: d.__setitem__("source_sha", "9" * 40)),
            ("transaction-list", lambda d: d["provider_transaction_ids"].reverse()),
            ("error-boundary", lambda d: d.__setitem__("error_boundary", "forged")),
            ("forbidden-push", lambda d: d.__setitem__("pushed", True)),
        ]

        def wrong_readback(document: dict[str, Any]) -> None:
            document["provider_results"][0]["sha256"] = "0" * 64
            document["provider_results_sha256"] = canonical_sha256(document["provider_results"])

        mutations.append(("readback", wrong_readback))
        for name, mutation in mutations:
            hostile = copy.deepcopy(receipt)
            mutation(hostile)
            hostile["receipt_sha256"] = canonical_sha256(hostile, omit={"receipt_sha256"})
            with self.subTest(name=name):
                with self.assertRaises(ManifestError):
                    validate_release_receipt(hostile, self.manifest)

    def test_every_prewrite_and_race_boundary_is_typed_and_never_retried(self) -> None:
        boundaries = release_boundaries(self.manifest)
        self.assertGreater(len(boundaries), self.manifest["object_count"] * 2)
        for boundary in boundaries:
            writer = FakeWriter()
            receipt = execute_release(
                self.manifest,
                writer,
                state_probe=lambda observed, target=boundary: observed != target,
            )
            with self.subTest(boundary=boundary):
                expected = TerminalState.SAFE_ABORT if not writer.calls else TerminalState.INDETERMINATE
                self.assertEqual(receipt["terminal_state"], expected.value)
                self.assertLessEqual(len(writer.calls), self.manifest["object_count"])
                self.assertEqual(len({row["key"] for row in writer.calls}), len(writer.calls))
                self.assertNotEqual(receipt["terminal_state"], TerminalState.COMPLETE_SUCCESS.value)

    def test_provider_failure_malformed_result_and_checkpoint_crash_are_indeterminate(self) -> None:
        for fail_at in range(self.manifest["object_count"]):
            writer = FakeWriter(fail_at=fail_at)
            receipt = execute_release(self.manifest, writer)
            with self.subTest(kind="provider", at=fail_at):
                self.assertEqual(receipt["terminal_state"], TerminalState.INDETERMINATE.value)
                self.assertEqual(len(writer.calls), fail_at + 1)
        for malformed_at in range(self.manifest["object_count"]):
            writer = FakeWriter(malformed_at=malformed_at)
            receipt = execute_release(self.manifest, writer)
            with self.subTest(kind="malformed", at=malformed_at):
                self.assertEqual(receipt["terminal_state"], TerminalState.INDETERMINATE.value)
                self.assertEqual(len(writer.calls), malformed_at + 1)
        for readback_at in range(self.manifest["object_count"]):
            writer = FakeWriter(wrong_readback_at=readback_at)
            receipt = execute_release(self.manifest, writer)
            with self.subTest(kind="readback", at=readback_at):
                self.assertEqual(receipt["terminal_state"], TerminalState.INDETERMINATE.value)
                self.assertEqual(len(writer.calls), readback_at + 1)
        for crash_after in range(self.manifest["object_count"] + 1):
            count = 0
            def checkpoint(_document: dict[str, Any], target: int = crash_after) -> None:
                nonlocal count
                count += 1
                if count == target + 1:
                    raise OSError("crash boundary")
            writer = FakeWriter()
            receipt = execute_release(self.manifest, writer, checkpoint=checkpoint)
            with self.subTest(kind="checkpoint", at=crash_after):
                self.assertNotEqual(receipt["terminal_state"], TerminalState.COMPLETE_SUCCESS.value)

    def test_duplicate_provider_transaction_identity_is_indeterminate(self) -> None:
        writer = FakeWriter(duplicate_transactions=True)
        receipt = execute_release(self.manifest, writer)
        self.assertEqual(receipt["terminal_state"], TerminalState.INDETERMINATE.value)
        self.assertEqual(len(writer.calls), 2)
        self.assertEqual(len(receipt["provider_results"]), 1)

    def test_release_replay_is_byte_deterministic(self) -> None:
        first = execute_release(self.manifest, FakeWriter())
        encoded = json.dumps(first, sort_keys=True, separators=(",", ":"))
        for _ in range(100):
            self.assertEqual(json.dumps(execute_release(self.manifest, FakeWriter()), sort_keys=True, separators=(",", ":")), encoded)


class TestQuarantine(unittest.TestCase):
    def setUp(self) -> None:
        keys = [*GLOBAL_OBJECTS, "kept-area-az.json", "orphan-area-az.json", "silhouettes/orphan-area-az.json"]
        self.inventory = receipt_for(keys)
        self.plan = build_quarantine_plan(
            self.inventory,
            active_area_ids={"kept-area-az"},
            source_sha=COMMIT,
            source_tree=TREE,
            workflow_sha256=WORKFLOW,
            root_approval_ref=APPROVAL,
            receipt_id="receipt-001",
        )
        self.preimages = {
            record["key"]: copy.deepcopy(record["preimage"])
            for record in self.inventory["objects"]
        }

    def test_empty_plan_requires_canonical_receipt_id_at_build_and_validation(self) -> None:
        inventory = receipt_for(
            list(GLOBAL_OBJECTS), area_registry={"alpha-trail-az"}
        )
        plan = build_quarantine_plan(
            inventory,
            active_area_ids={"alpha-trail-az"},
            source_sha=COMMIT,
            source_tree=TREE,
            workflow_sha256=WORKFLOW,
            root_approval_ref=APPROVAL,
            receipt_id="round34-empty-plan",
        )
        self.assertEqual(plan["object_count"], 0)
        self.assertEqual(validate_quarantine_plan(plan, inventory_receipt=inventory), plan)

        with self.assertRaisesRegex(ValueError, "receipt_id is not canonical"):
            build_quarantine_plan(
                inventory,
                active_area_ids={"alpha-trail-az"},
                source_sha=COMMIT,
                source_tree=TREE,
                workflow_sha256=WORKFLOW,
                root_approval_ref=APPROVAL,
                receipt_id="../NOT-CANONICAL",
            )

        hostile = copy.deepcopy(plan)
        hostile["receipt_id"] = "../NOT-CANONICAL"
        hostile["plan_sha256"] = canonical_sha256(
            hostile, omit={"plan_sha256"}
        )
        with self.assertRaisesRegex(ManifestError, "receipt_id is not canonical"):
            validate_quarantine_plan(hostile, inventory_receipt=inventory)

    def test_no_clobber_copy_verify_conditional_remove_and_no_final_delete(self) -> None:
        before_globals = {key: copy.deepcopy(self.preimages[key]) for key in GLOBAL_OBJECTS}
        store = FakeStore(self.preimages)
        receipt = execute_quarantine(self.plan, store)
        self.assertEqual(receipt["terminal_state"], TerminalState.COMPLETE_SUCCESS.value)
        for row in self.plan["objects"]:
            self.assertNotIn(row["source_key"], store.objects)
            copied = store.objects[row["quarantine_key"]]
            self.assertEqual(
                {field: copied[field] for field in ("etag", "bytes", "sha256")},
                {field: row["source_preimage"][field] for field in ("etag", "bytes", "sha256")},
            )
        self.assertTrue(all(row["quarantine_preimage"] for row in receipt["objects"]))
        self.assertEqual({key: store.objects[key] for key in GLOBAL_OBJECTS}, before_globals)
        self.assertFalse(any(call[0] == "FINAL_DELETE" for call in store.calls))
        self.assertFalse(receipt["final_deletion_authorized"])

    def test_quarantine_terminal_receipt_mutations_fail_closed(self) -> None:
        receipt = execute_quarantine(self.plan, FakeStore(self.preimages))
        self.assertEqual(validate_quarantine_receipt(receipt, self.plan), receipt)
        mutations: list[tuple[str, Any]] = [
            ("terminal-state", lambda d: d.__setitem__("terminal_state", "SAFE_ABORT")),
            ("plan", lambda d: d.__setitem__("plan_sha256", "0" * 64)),
            ("attempted", lambda d: d.__setitem__("attempted_effects", 0)),
            ("observed", lambda d: d.__setitem__("observed_effects", 0)),
            ("source-removed", lambda d: d["objects"][0].__setitem__("source_removed", False)),
            ("final-delete", lambda d: d.__setitem__("final_deletion_authorized", True)),
        ]

        def wrong_copy(document: dict[str, Any]) -> None:
            document["objects"][0]["quarantine_preimage"]["sha256"] = "0" * 64

        def duplicate_transaction(document: dict[str, Any]) -> None:
            document["objects"][1]["copy_transaction_id"] = document["objects"][0]["copy_transaction_id"]

        mutations.extend((("copy-bytes", wrong_copy), ("duplicate-transaction", duplicate_transaction)))
        for name, mutation in mutations:
            hostile = copy.deepcopy(receipt)
            mutation(hostile)
            hostile["receipt_sha256"] = canonical_sha256(hostile, omit={"receipt_sha256"})
            with self.subTest(name=name):
                with self.assertRaises(ManifestError):
                    validate_quarantine_receipt(hostile, self.plan)

    def test_faults_are_safe_abort_or_indeterminate_and_never_clobber(self) -> None:
        scenarios = ("copy", "corrupt_copy", "remove", "checkpoint", "collision", "stale", "changed_protected")
        for scenario in scenarios:
            store = FakeStore(self.preimages)
            checkpoint = None
            if scenario in {"copy", "corrupt_copy", "remove"}:
                store.fault = scenario
            elif scenario == "collision":
                destination = self.plan["objects"][0]["quarantine_key"]
                store.objects[destination] = {"etag": "collision", "bytes": 1, "sha256": SHA}
            elif scenario == "stale":
                store.objects[self.plan["objects"][0]["source_key"]]["etag"] = etag("stale")
            elif scenario == "changed_protected":
                store.objects["parking.json"]["sha256"] = "0" * 64
            elif scenario == "checkpoint":
                checkpoint = lambda _document: (_ for _ in ()).throw(OSError("durability fault"))
            receipt = execute_quarantine(self.plan, store, checkpoint=checkpoint)
            with self.subTest(scenario=scenario):
                self.assertIn(receipt["terminal_state"], {TerminalState.SAFE_ABORT.value, TerminalState.INDETERMINATE.value})
                self.assertNotEqual(receipt["terminal_state"], TerminalState.COMPLETE_SUCCESS.value)
                self.assertFalse(receipt["final_deletion_authorized"])
                self.assertFalse(any(call[0] == "FINAL_DELETE" for call in store.calls))
                if scenario == "changed_protected":
                    self.assertFalse(any(call[0] in {"copy_no_clobber", "remove_after_exact_proof"} for call in store.calls))

    def test_partial_batch_is_indeterminate_and_repeat_never_clobbers(self) -> None:
        class SecondCopyFault(FakeStore):
            def copy_no_clobber(
                self, source: str, destination: str, *,
                source_preimage: dict[str, Any], attempt_id: str,
            ) -> dict[str, Any]:
                if sum(call[0] == "copy_no_clobber" for call in self.calls) == 1:
                    self.fault = "copy"
                return super().copy_no_clobber(
                    source, destination, source_preimage=source_preimage,
                    attempt_id=attempt_id,
                )
        store = SecondCopyFault(self.preimages)
        receipt = execute_quarantine(self.plan, store)
        self.assertEqual(receipt["terminal_state"], TerminalState.INDETERMINATE.value)
        before_repeat = copy.deepcopy(store.objects)
        repeated = execute_quarantine(self.plan, store)
        self.assertNotEqual(repeated["terminal_state"], TerminalState.COMPLETE_SUCCESS.value)
        for key, value in before_repeat.items():
            if key.startswith("_trekdex-control/v2/quarantine/"):
                self.assertEqual(store.objects[key], value)


class HardCrash(BaseException):
    pass


class TestRound29Corrections(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        keys = [*GLOBAL_OBJECTS, "alpha-trail-az.json", "silhouettes/alpha-trail-az.json"]
        self.inventory = receipt_for(keys)
        self.locals = []
        for key in keys:
            path = root / key.replace("/", "--")
            path.write_bytes(("round29:" + key).encode())
            self.locals.append(local_object(key, path))
        self.manifest = build_upload_manifest(
            self.inventory, self.locals, source_sha=COMMIT, source_tree=TREE,
            workflow_sha256=WORKFLOW, root_approval_ref=APPROVAL,
            prior_anchor_ref=ANCHOR,
        )
        self.approval = make_approval_assertion(
            self.manifest, operation="RELEASE", key=APPROVAL_KEY,
            authorization_nonce="round29-release",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_signed_approval_and_actual_provider_identity_precede_every_effect(self) -> None:
        forged = copy.deepcopy(self.approval)
        forged["artifact_sha256"] = "0" * 64
        writer = FakeWriter()
        with self.assertRaises(ManifestError):
            execute_release_contract(
                self.manifest, writer, approval_assertion=forged,
                approval_key=APPROVAL_KEY, execution_id="approval-forgery",
            )
        self.assertEqual(writer.calls, [])

        wrong_provider = FakeWriter(wrong_identity=True)
        with self.assertRaises(ManifestError):
            execute_release_contract(
                self.manifest, wrong_provider, approval_assertion=self.approval,
                approval_key=APPROVAL_KEY, execution_id="provider-rebinding",
            )
        self.assertEqual(wrong_provider.calls, [])

    def test_provider_checksum_normalization_rejects_metadata_substitution(self) -> None:
        payload = b"independent-provider-content"
        digest = hashlib.sha256(payload).digest()
        import base64
        encoded = base64.b64encode(digest).decode()
        self.assertEqual(normalize_sha256_checksum(encoded, "provider"), digest.hex())
        with self.assertRaises(InventoryError):
            normalize_sha256_checksum("caller-metadata-claim", "provider")
        stage_source = (SCRIPTS / "stage-changed-r2.py").read_text()
        cleanup_source = (SCRIPTS / "cleanup-r2-orphans.py").read_text()
        self.assertNotIn('metadata.get("sha256")', stage_source)
        self.assertNotIn('metadata.get("sha256")', cleanup_source)
        self.assertIn('"get-object"', stage_source)
        self.assertIn('"get-object"', cleanup_source)
        self.assertNotIn('"--version-' + 'id"', cleanup_source)

    def test_release_hard_crash_leaves_exact_write_ahead_and_reconciles_without_retry(self) -> None:
        journals: list[dict[str, Any]] = []

        class CrashingWriter(FakeWriter):
            def put_conditional(
                self,
                row: dict[str, Any],
                *,
                attempt_id: str,
                preflight_row: dict[str, Any],
                preflight_sha256: str,
            ) -> dict[str, Any]:
                self.calls.append(copy.deepcopy(row))
                raise HardCrash("simulated process death after provider invocation")

        writer = CrashingWriter()
        with self.assertRaises(HardCrash):
            execute_release_contract(
                self.manifest, writer, approval_assertion=self.approval,
                approval_key=APPROVAL_KEY, execution_id="hard-crash-release",
                checkpoint=lambda document: journals.append(copy.deepcopy(document)),
            )
        self.assertEqual(len(writer.calls), 1)
        journal = journals[-1]
        self.assertEqual(journal["checkpoint_kind"], "WRITE_AHEAD")
        self.assertEqual(journal["pending_effect"]["index"], 0)
        pending = journal["pending_effect"]
        preflight = journal["release_preflight"]
        evidence = FakeWriter().put_conditional(
            self.manifest["objects"][0],
            attempt_id=pending["attempt_id"],
            preflight_row=preflight["rows"][0],
            preflight_sha256=preflight["preflight_sha256"],
        )
        reconciled = reconcile_release_write_ahead(journal, self.manifest, evidence)
        self.assertEqual(reconciled["terminal_state"], TerminalState.INDETERMINATE.value)
        self.assertEqual(reconciled["error_boundary"], "RECONCILED_NO_AUTOMATIC_RETRY")
        self.assertIsNone(reconciled["pending_effect"])

    def test_manifest_approval_mismatch_creates_zero_stage_bytes(self) -> None:
        root = Path(self.temp.name)
        geom = root / "geom"
        silhouettes = root / "silhouettes"
        geom.mkdir()
        silhouettes.mkdir()
        (geom / "alpha-trail-az.json").write_text('{}\n')
        (silhouettes / "alpha-trail-az.json").write_text('{}\n')
        inventory_path = root / "inventory.json"
        inventory_path.write_text(json.dumps(self.inventory) + "\n")
        area_index = root / "area-index.json"
        area_index.write_text('[["alpha-trail-az"]]\n')
        globals_by_key: dict[str, Path] = {}
        for key in GLOBAL_OBJECTS:
            path = root / ("preflight-" + key)
            path.write_text('[["alpha-trail-az"]]\n' if key == "index.json" else '{}\n')
            globals_by_key[key] = path
        spec = importlib.util.spec_from_file_location(
            "stage_changed_r2_round29", SCRIPTS / "stage-changed-r2.py"
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        stage_root = root / "must-not-exist"
        args = argparse.Namespace(
            inventory=inventory_path, bucket=BUCKET, stage_root=stage_root,
            geom_dir=geom, silhouettes_dir=silhouettes, area_index=area_index,
            global_object=[f"{key}={globals_by_key[key]}" for key in GLOBAL_OBJECTS],
            source_sha=COMMIT, source_tree=TREE, workflow_sha256=WORKFLOW,
            root_approval_ref=APPROVAL, anchor_ref=ANCHOR,
            approved_manifest_sha256="0" * 64,
        )
        with self.assertRaises(ManifestError):
            module.stage_v2(args)
        self.assertFalse(stage_root.exists())

    def test_quarantine_malformed_result_is_typed_and_current_object_race_preserves_newer(self) -> None:
        keys = [*GLOBAL_OBJECTS, "kept-area-az.json", "orphan-area-az.json"]
        inventory = receipt_for(keys)
        plan = build_quarantine_plan(
            inventory, active_area_ids={"kept-area-az"}, source_sha=COMMIT,
            source_tree=TREE, workflow_sha256=WORKFLOW,
            root_approval_ref=APPROVAL, receipt_id="round29-race",
        )
        preimages = {row["key"]: copy.deepcopy(row["preimage"]) for row in inventory["objects"]}

        class MalformedStore(FakeStore):
            def copy_no_clobber(self, source: str, destination: str, *, source_preimage: dict[str, Any], attempt_id: str) -> dict[str, Any]:
                super().copy_no_clobber(
                    source, destination, source_preimage=source_preimage,
                    attempt_id=attempt_id,
                )
                return {"transaction_id": None, "attempt_id": attempt_id}

        malformed = execute_quarantine(plan, MalformedStore(preimages))
        self.assertEqual(malformed["terminal_state"], TerminalState.INDETERMINATE.value)
        validate_quarantine_receipt(malformed, plan)

        class NewerCurrentStore(FakeStore):
            def copy_no_clobber(self, source: str, destination: str, *, source_preimage: dict[str, Any], attempt_id: str) -> dict[str, Any]:
                result = super().copy_no_clobber(
                    source, destination, source_preimage=source_preimage,
                    attempt_id=attempt_id,
                )
                newer = copy.deepcopy(self.objects[source])
                newer["etag"] = etag("newer-current")
                newer["bytes"] += 1
                newer["sha256"] = "d" * 64
                self.objects[source] = newer
                return result

        raced_store = NewerCurrentStore(preimages)
        raced = execute_quarantine(plan, raced_store)
        self.assertEqual(raced["terminal_state"], TerminalState.INDETERMINATE.value)
        self.assertEqual(raced_store.objects["orphan-area-az.json"]["sha256"], "d" * 64)
        self.assertFalse(any(call[0] == "remove_after_exact_proof" for call in raced_store.calls))

    def test_quarantine_hard_crash_journal_precedes_copy(self) -> None:
        keys = [*GLOBAL_OBJECTS, "kept-area-az.json", "orphan-area-az.json"]
        inventory = receipt_for(keys)
        plan = build_quarantine_plan(
            inventory, active_area_ids={"kept-area-az"}, source_sha=COMMIT,
            source_tree=TREE, workflow_sha256=WORKFLOW,
            root_approval_ref=APPROVAL, receipt_id="round29-crash",
        )
        preimages = {row["key"]: copy.deepcopy(row["preimage"]) for row in inventory["objects"]}
        journals: list[dict[str, Any]] = []

        class CrashingStore(FakeStore):
            def copy_no_clobber(self, source: str, destination: str, *, source_preimage: dict[str, Any], attempt_id: str) -> dict[str, Any]:
                self.calls.append(("copy_no_clobber", source, destination, attempt_id))
                raise HardCrash("simulated process death during copy")

        with self.assertRaises(HardCrash):
            execute_quarantine(
                plan, CrashingStore(preimages),
                checkpoint=lambda document: journals.append(copy.deepcopy(document)),
            )
        journal = journals[-1]
        self.assertEqual(journal["checkpoint_kind"], "WRITE_AHEAD")
        self.assertEqual(journal["pending_effect"]["operation"], "COPY_IF_MATCH")
        validate_quarantine_receipt(journal, plan)


class TestOfflineConsumers(unittest.TestCase):
    def test_stage_consumer_builds_exact_manifest_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory = receipt_for()
            inventory_path = root / "inventory.json"
            inventory_path.write_text(json.dumps(inventory, sort_keys=True, separators=(",", ":")) + "\n")
            geom = root / "geom"
            silhouettes = root / "silhouettes"
            geom.mkdir()
            silhouettes.mkdir()
            (geom / "alpha-trail-az.json").write_text('{"id":"alpha-trail-az"}\n')
            (silhouettes / "alpha-trail-az.json").write_text('{"id":"alpha-trail-az"}\n')
            globals_by_key: dict[str, Path] = {}
            for key in GLOBAL_OBJECTS:
                path = root / ("global-" + key)
                path.write_text('{"key":' + json.dumps(key) + '}\n')
                globals_by_key[key] = path
            globals_by_key["index.json"].write_text('[["alpha-trail-az"]]\n')
            stage_root = root / "stage"
            area_index = root / "area-index.json"
            area_index.write_text('[["alpha-trail-az"]]\n')
            command = [
                sys.executable, str(SCRIPTS / "stage-changed-r2.py"), "stage",
                "--inventory", str(inventory_path), "--bucket", BUCKET,
                "--stage-root", str(stage_root), "--geom-dir", str(geom),
                "--silhouettes-dir", str(silhouettes), "--area-index", str(area_index),
                "--source-sha", COMMIT,
                "--source-tree", TREE, "--workflow-sha256", WORKFLOW,
                "--root-approval-ref", APPROVAL, "--anchor-ref", ANCHOR,
            ]
            for key in GLOBAL_OBJECTS:
                command += ["--global-object", f"{key}={globals_by_key[key]}"]
            completed = subprocess.run(
                command,
                cwd=ROOT,
                env=MINIMAL_SUBPROCESS_ENV,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            manifest = strict_json_loads((stage_root / "upload-manifest.json").read_text(), "staged manifest")
            validate_upload_manifest(manifest, inventory_receipt=inventory)
            self.assertEqual(manifest["object_count"], 6)
            self.assertTrue(all(not Path(row["local_path"]).is_absolute() for row in manifest["objects"]))

    def test_stage_consumer_rejects_future_key_before_staging(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory = receipt_for()
            inventory_path = root / "inventory.json"
            inventory_path.write_text(json.dumps(inventory, sort_keys=True, separators=(",", ":")) + "\n")
            geom = root / "geom"
            silhouettes = root / "silhouettes"
            geom.mkdir()
            silhouettes.mkdir()
            (geom / "alpha-trail-az.json").write_text('{}\n')
            (geom / "future-global.json").write_text('{}\n')
            (silhouettes / "alpha-trail-az.json").write_text('{}\n')
            globals_by_key = {}
            for key in GLOBAL_OBJECTS:
                path = root / ("global-" + key)
                path.write_text('{}\n')
                globals_by_key[key] = path
            globals_by_key["index.json"].write_text('[["alpha-trail-az"]]\n')
            stage_root = root / "must-not-exist"
            area_index = root / "area-index.json"
            area_index.write_text('[["alpha-trail-az"]]\n')
            command = [
                sys.executable, str(SCRIPTS / "stage-changed-r2.py"), "stage",
                "--inventory", str(inventory_path), "--bucket", BUCKET,
                "--stage-root", str(stage_root), "--geom-dir", str(geom),
                "--silhouettes-dir", str(silhouettes), "--area-index", str(area_index),
                "--source-sha", COMMIT,
                "--source-tree", TREE, "--workflow-sha256", WORKFLOW,
                "--root-approval-ref", APPROVAL, "--anchor-ref", ANCHOR,
            ]
            for key in GLOBAL_OBJECTS:
                command += ["--global-object", f"{key}={globals_by_key[key]}"]
            completed = subprocess.run(
                command, cwd=ROOT,
                env=MINIMAL_SUBPROCESS_ENV,
                capture_output=True, text=True,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("outside the release contract", completed.stderr)
            self.assertFalse(stage_root.exists())

    def test_stage_consumer_rejects_area_shaped_global_not_in_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory = receipt_for()
            inventory_path = root / "inventory.json"
            inventory_path.write_text(json.dumps(inventory, sort_keys=True, separators=(",", ":")) + "\n")
            geom = root / "geom"
            silhouettes = root / "silhouettes"
            geom.mkdir()
            silhouettes.mkdir()
            (geom / "alpha-trail-az.json").write_text('{}\n')
            (geom / "future-global-az.json").write_text('{}\n')
            (silhouettes / "alpha-trail-az.json").write_text('{}\n')
            area_index = root / "area-index.json"
            area_index.write_text('[["alpha-trail-az"]]\n')
            globals_by_key = {}
            for key in GLOBAL_OBJECTS:
                path = root / ("global-" + key)
                path.write_text('{}\n')
                globals_by_key[key] = path
            globals_by_key["index.json"].write_text('[["alpha-trail-az"]]\n')
            stage_root = root / "must-not-exist"
            command = [
                sys.executable, str(SCRIPTS / "stage-changed-r2.py"), "stage",
                "--inventory", str(inventory_path), "--bucket", BUCKET,
                "--stage-root", str(stage_root), "--geom-dir", str(geom),
                "--silhouettes-dir", str(silhouettes), "--area-index", str(area_index),
                "--source-sha", COMMIT, "--source-tree", TREE,
                "--workflow-sha256", WORKFLOW, "--root-approval-ref", APPROVAL,
                "--anchor-ref", ANCHOR,
            ]
            for key in GLOBAL_OBJECTS:
                command += ["--global-object", f"{key}={globals_by_key[key]}"]
            completed = subprocess.run(
                command, cwd=ROOT,
                env=MINIMAL_SUBPROCESS_ENV,
                capture_output=True, text=True,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("outside the release contract", completed.stderr)
            self.assertFalse(stage_root.exists())

    def test_cleanup_consumer_builds_quarantine_plan_without_network(self) -> None:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                keys = [
                    *GLOBAL_OBJECTS, "kept-area-az.json", "kept-two-az.json",
                    "kept-three-az.json", "orphan-area-az.json",
                    "silhouettes/orphan-area-az.json",
                ]
                active_ids = {"kept-area-az", "kept-two-az", "kept-three-az"} | {
                    f"area-{number:04d}-az" for number in range(997)
                }
                registry_ids = active_ids | {"orphan-area-az"}
                inventory = receipt_for(keys, area_registry=registry_ids)
                inventory_path = root / "inventory.json"
                inventory_path.write_text(json.dumps(inventory, sort_keys=True, separators=(",", ":")) + "\n")
                index_path = root / "active-index.json"
                registry_path = root / "registry-index.json"
                index_path.write_text(json.dumps([[area_id] for area_id in sorted(active_ids)], separators=(",", ":")) + "\n")
                registry_path.write_text(json.dumps([[area_id] for area_id in sorted(registry_ids)], separators=(",", ":")) + "\n")
                plan_path = root / "quarantine-plan.json"
                completed = subprocess.run(
                    [
                        sys.executable, str(SCRIPTS / "cleanup-r2-orphans.py"), "plan",
                        "--inventory", str(inventory_path), "--index", str(index_path),
                        "--registry", str(registry_path), "--source-sha", COMMIT,
                        "--source-tree", TREE, "--workflow-sha256", WORKFLOW,
                        "--root-approval-ref", APPROVAL, "--receipt-id", "receipt-001",
                        "--output", str(plan_path),
                    ],
                    cwd=ROOT,
                    env=MINIMAL_SUBPROCESS_ENV,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                plan = strict_json_loads(plan_path.read_text(), "quarantine plan")
                self.assertEqual(plan["object_count"], 2)
                self.assertFalse(plan["final_deletion_authorized"])
                self.assertEqual(
                    {row["source_key"] for row in plan["objects"]},
                    {"orphan-area-az.json", "silhouettes/orphan-area-az.json"},
                )


    def test_cleanup_consumer_rejects_wrong_index_before_plan(self) -> None:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                registry_ids = {"orphan-area-az"} | {
                    f"area-{number:04d}-az" for number in range(999)
                }
                inventory = receipt_for(
                    [*GLOBAL_OBJECTS, "orphan-area-az.json"],
                    area_registry=registry_ids,
                )
                inventory_path = root / "inventory.json"
                inventory_path.write_text(json.dumps(inventory, sort_keys=True, separators=(",", ":")) + "\n")
                index_path = root / "short-index.json"
                index_path.write_text('[["only-area-az"]]\n')
                registry_path = root / "registry-index.json"
                registry_path.write_text(json.dumps([[area_id] for area_id in sorted(registry_ids)]) + "\n")
                output = root / "must-not-exist.json"
                completed = subprocess.run(
                    [
                        sys.executable, str(SCRIPTS / "cleanup-r2-orphans.py"), "plan",
                        "--inventory", str(inventory_path), "--index", str(index_path),
                        "--registry", str(registry_path), "--source-sha", COMMIT,
                        "--source-tree", TREE, "--workflow-sha256", WORKFLOW,
                        "--root-approval-ref", APPROVAL, "--receipt-id", "receipt-001",
                        "--output", str(output),
                    ],
                    cwd=ROOT,
                    env=MINIMAL_SUBPROCESS_ENV,
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn("implausibly short", completed.stderr)
                self.assertFalse(output.exists())


class TestRepositoryWiring(unittest.TestCase):
    LEASED = (
        ".github/workflows/sync-geom-to-r2.yml",
        ".github/workflows/cleanup-r2-orphans.yml",
        ".github/workflows/trailforge-publish-us.yml",
        ".github/workflows/release-contracts-ci.yml",
        ".github/workflows/trailforge-publish.yml",
        "scripts/stage-changed-r2.py",
        "scripts/cleanup-r2-orphans.py",
        "scripts/r2_release_contract.py",
        "scripts/trailforge_candidate_contract.py",
        "scripts/test_r2_release_contract.py",
        "scripts/test_trailforge_candidate_contract.py",
    )
    PRODUCERS = tuple(path for path, _digest, _capabilities in CI_PRODUCER_GRAPH)

    def files(self) -> dict[str, str]:
        def read_exact_text(path: str) -> str:
            with (ROOT / path).open(
                "r", encoding="utf-8", errors="strict", newline=""
            ) as stream:
                return stream.read()

        return {
            path: read_exact_text(path)
            for path in (*self.LEASED, *self.PRODUCERS)
        }

    def test_all_ten_leased_consumers_and_ci_seams_are_required(self) -> None:
        files = self.files()
        self.assertEqual(audit_repository_contract(files), [])
        for path in self.LEASED:
            hostile = copy.deepcopy(files)
            marker = {
                ".github/workflows/sync-geom-to-r2.yml": "approved_manifest_sha256",
                ".github/workflows/cleanup-r2-orphans.yml": "approved_quarantine_plan_sha256",
                ".github/workflows/trailforge-publish-us.yml": "trailforge_candidate_contract.py",
                ".github/workflows/release-contracts-ci.yml": "scripts/test_trailforge_candidate_contract.py",
                ".github/workflows/trailforge-publish.yml": "mols_release_input_receipt_b64",
                "scripts/stage-changed-r2.py": "r2_release_contract",
                "scripts/cleanup-r2-orphans.py": "r2_release_contract",
                "scripts/r2_release_contract.py": "trekdex-r2-release-contract-v5",
                "scripts/trailforge_candidate_contract.py": "trekdex-trailforge-candidate-contract-v3",
                "scripts/test_r2_release_contract.py": CONTRACT_TEST_SENTINEL,
                "scripts/test_trailforge_candidate_contract.py": "RR2-ORIGINAL-CLAUSES-TRAILFORGE-V1",
            }[path]
            self.assertIn(marker, hostile[path])
            hostile[path] = hostile[path].replace(marker, "MUTATED-AWAY")
            with self.subTest(path=path):
                self.assertTrue(any(path in error for error in audit_repository_contract(hostile)))

    def test_complete_real_producer_graph_rejects_every_producer_only_mutation(self) -> None:
        files = self.files()
        registry_graph = tuple(sorted(
            (path, capabilities)
            for path, _digest, capabilities in CI_PRODUCER_GRAPH
        ))
        production_graph = derive_production_producer_graph(files)
        self.assertEqual(production_graph, registry_graph)
        self.assertEqual(len(registry_graph), 8)
        self.assertEqual(
            set(self.PRODUCERS),
            {
                "trailforge/serve/publish_areas.py",
                "scripts/build-trail-search-index.py",
                "scripts/build-parking-pool.py",
                "scripts/build-trail-shapes.py",
                "scripts/merge-published-geom.py",
                "scripts/silhouettes-from-geom.py",
                "scripts/filter-ios-bundle.py",
                ".github/workflows/trailforge-publish-us.yml",
            },
        )
        expected_capabilities = {
            "AREA_GEOM", "AREA_INDEX_INPUT", "parking.json",
            "trail-search.json", "trail-shapes.json",
            "POST_FAN_IN_AREA_GEOM", "POST_FAN_IN_AREA_INDEX",
            "POST_FAN_IN_AREA_SILHOUETTE", "POST_FAN_IN_IOS_AREA_INDEX",
            "POST_FAN_IN_IOS_ALIAS_MAP", "REVIEW_CANDIDATE_PATCH",
            "REVIEW_CANDIDATE_RECEIPT",
        }
        self.assertEqual(
            {
                capability
                for _path, _digest, outputs in CI_PRODUCER_GRAPH
                for capability in outputs
            },
            expected_capabilities,
        )
        ci = files[".github/workflows/release-contracts-ci.yml"]
        self.assertIn("python3 scripts/test_r2_release_contract.py", ci)
        for path in self.PRODUCERS:
            hostile = copy.deepcopy(files)
            if path.endswith(".py"):
                hostile[path] += (
                    '\n# R38-H-001 hostile output-key mutation.\n'
                    'open("catalog-us.json", "w").write("{}")\n'
                )
            else:
                hostile[path] = hostile[path].replace(
                    'Path("candidate/candidate-receipt.json")',
                    'Path("candidate/catalog-us.json")',
                    1,
                )
            errors = audit_repository_contract(hostile)
            with self.subTest(path=path, mutation="output-key-behavior"):
                self.assertTrue(
                    any(
                        path in error and "producer bytes differ" in error
                        for error in errors
                    ),
                    errors,
                )

            missing = copy.deepcopy(files)
            del missing[path]
            errors = audit_repository_contract(missing)
            with self.subTest(path=path, mutation="missing-node"):
                self.assertTrue(any(path in error and "missing" in error for error in errors))

        for workflow_path, marker, source_path, _capabilities in PRODUCTION_PRODUCER_BOUNDARIES:
            missing_boundary = copy.deepcopy(files)
            missing_boundary[workflow_path] = missing_boundary[workflow_path].replace(
                marker, "MUTATED-PRODUCTION-BOUNDARY", 1
            )
            errors = audit_repository_contract(missing_boundary)
            with self.subTest(source=source_path, mutation="missing-production-boundary"):
                self.assertTrue(any("production boundary" in error for error in errors), errors)

            duplicate_boundary = copy.deepcopy(files)
            duplicate_boundary[workflow_path] += "\n" + marker + "\n"
            errors = audit_repository_contract(duplicate_boundary)
            with self.subTest(source=source_path, mutation="duplicate-production-boundary"):
                self.assertTrue(any("production boundary" in error for error in errors), errors)

    def test_immutable_identity_and_cleanliness_seams_are_required(self) -> None:
        files = self.files()
        markers = {
            ".github/workflows/sync-geom-to-r2.yml": (
                "git symbolic-ref -q HEAD", "--untracked-files=all", "GITHUB_SHA",
                "sha256sum .github/workflows/sync-geom-to-r2.yml",
                "account_identity_ref", "endpoint_identity_ref",
            ),
            ".github/workflows/cleanup-r2-orphans.yml": (
                "git symbolic-ref -q HEAD", "--untracked-files=all", "GITHUB_SHA",
                "account_identity_ref", "endpoint_identity_ref",
            ),
            ".github/workflows/trailforge-publish-us.yml": (
                "git symbolic-ref -q HEAD", "--untracked-files=all", "GITHUB_SHA",
                "sha256sum .github/workflows/trailforge-publish-us.yml",
            ),
        }
        for path, required in markers.items():
            for marker in required:
                hostile = copy.deepcopy(files)
                hostile[path] = hostile[path].replace(marker, "MUTATED-AWAY")
                with self.subTest(path=path, marker=marker):
                    self.assertTrue(any(path in error for error in audit_repository_contract(hostile)))

    def test_workflows_have_no_mutable_git_tail_recursive_upload_or_final_delete(self) -> None:
        files = self.files()
        controlled = "\n".join(files[path] for path in self.LEASED[:4])
        forbidden = ("git push", "git rebase", "git commit", "aws s3 sync", "delete-objects", "aws s3 rm")
        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token, controlled)
        self.assertIn("plan_only", files[".github/workflows/sync-geom-to-r2.yml"])
        self.assertIn("plan_only", files[".github/workflows/cleanup-r2-orphans.yml"])

    def test_every_repository_change_triggers_ci_without_path_bypass(self) -> None:
            files = self.files()
            ci_path = ".github/workflows/release-contracts-ci.yml"
            ci = files[ci_path]
            self.assertNotIn("paths:", ci)
            self.assertNotIn("paths-ignore:", ci)
            for event in ("pull_request: {}", "push: {}", "workflow_dispatch: {}"):
                hostile = copy.deepcopy(files)
                hostile[ci_path] = hostile[ci_path].replace(event, "MUTATED-EVENT", 1)
                with self.subTest(event=event):
                    self.assertTrue(any(ci_path in error for error in audit_repository_contract(hostile)))
            producer_only_filter = copy.deepcopy(files)
            producer_only_filter[ci_path] = producer_only_filter[ci_path].replace(
                "pull_request: {}",
                'pull_request:\n    paths:\n      - "scripts/r2_release_contract.py"',
            )
            errors = audit_repository_contract(producer_only_filter)
            self.assertTrue(any("bypass safety CI" in error for error in errors))

    def test_apply_rederives_population_and_uses_scoped_conditional_rollback(self) -> None:
        files = self.files()
        stage = files["scripts/stage-changed-r2.py"]
        for marker in (
            "_derive_producer_population(args, inventory)",
            "_build_scoped_semantic_deltas(",
            "read_reviewed_baseline_bytes",
            "semantic_deltas=semantic_deltas",
            "producer population differs from reviewed bytes",
            "class MolsConditionalWriter",
            "execute_scoped_release",
            "rollback_delete_created",
            '"--if-match"',
            '"--if-none-match"',
            '"trekdex-preimage-ref"',
            '"rollback-manifest.json"',
            'pass_fds=(snapshot.fileno(),)',
        ):
            hostile = copy.deepcopy(files)
            hostile["scripts/stage-changed-r2.py"] = stage.replace(marker, "MUTATED-AWAY", 1)
            with self.subTest(marker=marker):
                self.assertIn("MUTATED-AWAY", hostile["scripts/stage-changed-r2.py"])
                self.assertNotEqual(hostile["scripts/stage-changed-r2.py"], stage)
        self.assertNotIn("live overwrite disabled", stage)
        self.assertNotIn("adapter_success_schema_capable=False", stage)

    def test_publish_to_sync_passes_exact_required_plan_only_inputs(self) -> None:
        publish = (ROOT / ".github/workflows/trailforge-publish.yml").read_text()
        sync = (ROOT / ".github/workflows/sync-geom-to-r2.yml").read_text()
        self.assertEqual(_workflow_mapping_keys(publish, ("on",)), ["workflow_dispatch"])
        self.assertEqual(_workflow_mapping_keys(sync, ("on",)), ["workflow_dispatch"])
        sync_inputs = set(_workflow_mapping_keys(sync, ("on", "workflow_dispatch", "inputs")))
        self.assertEqual(sync_inputs, {
            "source_sha", "source_tree", "root_approval_ref",
            "account_identity_ref", "endpoint_identity_ref", "inventory_anchor_ref",
            "authoritative_denominator", "plan_only", "approved_manifest_sha256",
            "reconcile_run_id",
        })
        dispatch_match = re.search(
            r"gh workflow run sync-geom-to-r2\.yml --ref main \\\n(?P<body>(?:\s+-f [^\n]+\\?\n?)+)",
            publish,
        )
        self.assertIsNotNone(dispatch_match)
        body = dispatch_match.group("body")
        passed = set(re.findall(r"-f ([a-z_]+)=", body))
        self.assertEqual(
            passed,
            sync_inputs - {"approved_manifest_sha256", "reconcile_run_id"},
        )
        self.assertIn('-f plan_only=true', body)
        self.assertNotIn("approved_manifest_sha256", body)
        self.assertIn('test "$GITHUB_REF_TYPE" = "branch"', publish)
        self.assertIn('test "$GITHUB_REF_NAME" = "main"', publish)
        self.assertIn('SOURCE_SHA="$(git rev-parse HEAD)"', publish)
        self.assertIn('SOURCE_TREE="$(git rev-parse \'HEAD^{tree}\')"', publish)
        self.assertIn('source_sha=$SOURCE_SHA', publish)
        self.assertIn('source_tree=$SOURCE_TREE', publish)
        self.assertIn("PUBLISHED: ${{ steps.published_commit.outputs.published }}", publish)
        self.assertIn('if [ "$PUBLISHED" != "true" ] || [ "$RELEASE_MODE" != "mols" ]', publish)
        for output in (
            "root_approval_ref", "account_identity_ref", "endpoint_identity_ref",
            "inventory_anchor_ref", "authoritative_denominator",
        ):
            self.assertIn(f"steps.release_input.outputs.{output}", publish)
            self.assertNotIn(f"${{{{ inputs.{output} }}}}", publish)
        verify_offset = publish.index("verify-release-input")
        install_offset = publish.index("Install osmium")
        commit_offset = publish.index("git commit -m")
        push_offset = publish.index("            git push\n          else")
        guard_offset = publish.index("verify-mols-staged-diff")
        commit_guard_offset = publish.index("verify-mols-commit")
        self.assertLess(verify_offset, install_offset)
        self.assertLess(guard_offset, commit_offset)
        self.assertLess(commit_offset, commit_guard_offset)
        self.assertLess(commit_guard_offset, push_offset)
        self.assertIn('test "$(git rev-parse origin/main)" = "$(git rev-parse HEAD^)"', publish)
        self.assertIn('--only-area "$VERIFIED_AREA_ID"', publish)
        self.assertIn("separate reviewed", publish)
        self.assertIn('AWS_CLI_VERSION: "2.39.1"', sync)
        self.assertIn("5b6c10e0a8ccdcf30c84bd643fea511f103d257a4787a7cef878691d63169804", sync)
        self.assertIn('--endpoint-url "$ENDPOINT"', sync)
        self.assertIn('--aws-cli-version "$AWS_CLI_VERSION"', sync)
        self.assertIn('--aws-cli-archive-sha256 "$AWS_CLI_ARCHIVE_SHA256"', sync)
        self.assertIn("sha256sum --check --strict", sync)
        self.assertIn("reconcile-scoped", sync)
        self.assertEqual(_workflow_scalar(sync, ("concurrency", "group")), "sync-geom-to-r2")
        self.assertNotRegex(publish, r"(?m)^\s*push:\s*$")
        self.assertNotRegex(sync, r"(?m)^\s*push:\s*$")
        self.assertNotRegex(publish, r"(?m)^\s*pull_request:\s*$")
        self.assertNotRegex(sync, r"(?m)^\s*pull_request:\s*$")

    def test_secret_bearing_workflow_run_scalars_use_validated_env_only(self) -> None:
        workflows = {
            path: (ROOT / ".github/workflows" / path).read_text(encoding="utf-8")
            for path in ("trailforge-publish.yml", "sync-geom-to-r2.yml")
        }
        forbidden = re.compile(
            r"(?:^|[^A-Za-z0-9_])inputs\.|github\.event(?:\.|$)|"
            r"steps\.[A-Za-z0-9_-]+\.outputs(?:\.|\[|$)"
        )
        parsed: dict[str, dict[str, str]] = {}
        for path, text in workflows.items():
            runs = _workflow_run_scalars(text)
            self.assertEqual(
                len(runs),
                len(re.findall(r"(?m)^\s+run:\s*", text)),
                f"unparsed run scalar in {path}",
            )
            parsed[path] = {name: script for name, _line, script in runs}
            for name, line, script in runs:
                syntax = subprocess.run(
                    ["/bin/bash", "-n"],
                    input=script,
                    env=MINIMAL_SUBPROCESS_ENV,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                with self.subTest(path=path, step=name, line=line, check="bash-syntax"):
                    self.assertEqual(syntax.returncode, 0, syntax.stderr)
                for expression in re.findall(r"\$\{\{\s*(.*?)\s*\}\}", script):
                    with self.subTest(path=path, step=name, line=line):
                        self.assertIsNone(
                            forbidden.search(expression),
                            f"unsafe workflow expression in run scalar: {expression}",
                        )

        publish = workflows["trailforge-publish.yml"]
        shape_name = "Validate publication input shape before secrets"
        verify_name = "Verify canonical Mols release input after shape validation"
        shape_step = _workflow_named_step(publish, shape_name)
        verify_step = _workflow_named_step(publish, verify_name)
        self.assertNotIn("secrets.", shape_step)
        self.assertNotIn("${{ inputs.", publish.replace(shape_step, ""))
        self.assertEqual(publish.count("R2_ROOT_APPROVAL_HMAC_KEY: ${{ secrets.R2_ROOT_APPROVAL_HMAC_KEY }}"), 1)
        self.assertIn("steps.input_shape.outputs.mols_release_input_receipt_b64", verify_step)
        self.assertNotRegex(verify_step, r"\$\{\{\s*(?:inputs\.|github\.event)")
        self.assertLess(publish.index(shape_name), publish.index("actions/checkout@v4"))
        self.assertLess(publish.index("actions/checkout@v4"), publish.index(verify_name))
        self.assertIn("persist-credentials: false", publish[:publish.index(verify_name)])
        for later_name in (
            "Merge signed Mols row into canonical master index",
            "Commit published data and seal pushed identity",
        ):
            later = _workflow_named_step(publish, later_name)
            self.assertNotIn("R2_ROOT_APPROVAL_HMAC_KEY", later)
            self.assertNotIn("MOLS_RELEASE_INPUT_RECEIPT_B64", later)

        sync = workflows["sync-geom-to-r2.yml"]
        sync_shape_name = "Validate sync input shape before secrets"
        sync_shape = _workflow_named_step(sync, sync_shape_name)
        provider = _workflow_named_step(
            sync,
            "Build canonical plan and optionally apply the exact reviewed manifest",
        )
        self.assertNotIn("secrets.", sync_shape)
        self.assertNotIn("${{ inputs.", sync.replace(sync_shape, ""))
        self.assertLess(sync.index(sync_shape_name), sync.index("actions/checkout@v4"))
        self.assertNotIn("${{ inputs.", provider)
        self.assertIn("steps.input_shape.outputs.source_sha", provider)
        self.assertIn("steps.input_shape.outputs.approved_manifest_sha256", provider)

        payload = 'europe/denmark"; printf injected > "$MARKER"; #'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "marker"
            output = root / "github-output"
            output.write_text("", encoding="utf-8")
            environment = {
                **MINIMAL_SUBPROCESS_ENV,
                "GITHUB_OUTPUT": str(output),
                "GITHUB_REF_TYPE": "branch",
                "GITHUB_REF_NAME": "main",
                "MARKER": str(marker),
                "RAW_EXTRACT": payload,
                "RAW_STATE": "Denmark",
                "RAW_DRY_RUN": "false",
                "RAW_NO_ROUTES": "false",
                "RAW_TOUCH_REPORT": "false",
                "RAW_ELEVATION": "false",
                "RAW_REGION_CODE": "dk",
                "RAW_PILOT_AREA_ID": "",
                "RAW_PILOT_BBOX": "10.40,56.08,10.90,56.35",
                "RAW_MOLS_RELEASE_INPUT_RECEIPT_B64": "e30K",
            }
            completed = subprocess.run(
                ["/bin/bash", "-c", parsed["trailforge-publish.yml"][shape_name]],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertFalse(marker.exists())
            self.assertEqual(output.read_text(encoding="utf-8"), "")

            generic_output = root / "generic-output"
            generic_output.write_text("", encoding="utf-8")
            generic_environment = {
                **environment,
                "GITHUB_OUTPUT": str(generic_output),
                "RAW_EXTRACT": "north-america/us/arizona",
                "RAW_STATE": "Arizona",
                "RAW_DRY_RUN": "true",
                "RAW_NO_ROUTES": "false",
                "RAW_TOUCH_REPORT": "false",
                "RAW_ELEVATION": "true",
                "RAW_REGION_CODE": "",
                "RAW_PILOT_BBOX": "",
                "RAW_MOLS_RELEASE_INPUT_RECEIPT_B64": "",
            }
            generic = subprocess.run(
                ["/bin/bash", "-c", parsed["trailforge-publish.yml"][shape_name]],
                cwd=ROOT,
                env=generic_environment,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(generic.returncode, 0, generic.stderr)
            generic_values = dict(
                line.split("=", 1)
                for line in generic_output.read_text(encoding="utf-8").splitlines()
            )
            self.assertEqual(generic_values["release_mode"], "generic")
            self.assertEqual(generic_values["extract"], "north-america/us/arizona")
            self.assertEqual(generic_values["state"], "Arizona")

            mols_output = root / "mols-output"
            mols_output.write_text("", encoding="utf-8")
            mols_environment = {
                **environment,
                "GITHUB_OUTPUT": str(mols_output),
                "RAW_EXTRACT": "europe/denmark",
            }
            mols = subprocess.run(
                ["/bin/bash", "-c", parsed["trailforge-publish.yml"][shape_name]],
                cwd=ROOT,
                env=mols_environment,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(mols.returncode, 0, mols.stderr)
            mols_values = dict(
                line.split("=", 1)
                for line in mols_output.read_text(encoding="utf-8").splitlines()
            )
            self.assertEqual(mols_values["release_mode"], "mols")
            self.assertEqual(mols_values["mols_release_input_receipt_b64"], "e30K")

            sync_output = root / "sync-output"
            sync_output.write_text("", encoding="utf-8")
            sync_environment = {
                **MINIMAL_SUBPROCESS_ENV,
                "GITHUB_OUTPUT": str(sync_output),
                "RAW_SOURCE_SHA": COMMIT,
                "RAW_SOURCE_TREE": TREE,
                "RAW_ROOT_APPROVAL_REF": APPROVAL,
                "RAW_ACCOUNT_IDENTITY_REF": ACCOUNT,
                "RAW_ENDPOINT_IDENTITY_REF": ENDPOINT,
                "RAW_INVENTORY_ANCHOR_REF": ANCHOR,
                "RAW_AUTHORITATIVE_DENOMINATOR": "12345",
                "RAW_PLAN_ONLY": "true",
                "RAW_APPROVED_MANIFEST_SHA256": "",
                "RAW_RECONCILE_RUN_ID": "",
            }
            sync_shape_run = subprocess.run(
                ["/bin/bash", "-c", parsed["sync-geom-to-r2.yml"][sync_shape_name]],
                cwd=ROOT,
                env=sync_environment,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(sync_shape_run.returncode, 0, sync_shape_run.stderr)
            sync_values = dict(
                line.split("=", 1)
                for line in sync_output.read_text(encoding="utf-8").splitlines()
            )
            self.assertEqual(sync_values["source_sha"], COMMIT)
            self.assertEqual(sync_values["plan_only"], "true")

    def test_every_r2_writer_and_credential_holder_shares_sync_lock(self) -> None:
        workflow_root = ROOT / ".github/workflows"
        workflows = {
            path.name: path.read_text()
            for path in sorted(workflow_root.glob("*.yml"))
        }
        write_credential_tokens = (
            "secrets.R2_ACCESS_KEY_ID", "secrets.R2_SECRET_ACCESS_KEY",
        )
        write_credential_holders = {
            name for name, text in workflows.items()
            if any(token in text for token in write_credential_tokens)
        }
        self.assertEqual(
            write_credential_holders,
            {"sync-geom-to-r2.yml", "cleanup-r2-orphans.yml"},
        )
        for name in sorted(write_credential_holders):
            with self.subTest(workflow=name):
                self.assertEqual(
                    _workflow_scalar(workflows[name], ("concurrency", "group")),
                    "sync-geom-to-r2",
                )
                self.assertEqual(
                    _workflow_mapping_keys(workflows[name], ("on",)),
                    ["workflow_dispatch"],
                )

        direct_writers: set[str] = set()
        for path in sorted((ROOT / "scripts").glob("*.py")):
            if path.name.startswith("test_"):
                continue
            text = path.read_text()
            if re.search(
                r'"aws"\s*,\s*"s3api"\s*,\s*"(?:put|copy|delete)-object"',
                text,
            ):
                direct_writers.add(path.name)
        self.assertEqual(
            direct_writers,
            {"stage-changed-r2.py", "cleanup-r2-orphans.py"},
        )
        self.assertIn("scripts/stage-changed-r2.py", workflows["sync-geom-to-r2.yml"])
        self.assertIn("scripts/cleanup-r2-orphans.py", workflows["cleanup-r2-orphans.yml"])

        dispatchers = {
            name for name, text in workflows.items()
            if "gh workflow run sync-geom-to-r2.yml" in text
        }
        self.assertEqual(dispatchers, {
            "build-trail-index.yml", "trailforge-elevation-us.yml",
            "trailforge-parking.yml", "trailforge-publish-batch.yml",
            "trailforge-publish.yml",
        })
        for name in sorted(dispatchers):
            text = workflows[name]
            with self.subTest(dispatcher=name):
                self.assertFalse(any(token in text for token in write_credential_tokens))
                self.assertNotRegex(text, r'"(?:put|copy|delete)-object"')

    def test_r2_paths_are_version_free_and_deletes_are_plain_single_key(self) -> None:
        paths = (
            "scripts/r2_release_contract.py",
            "scripts/stage-changed-r2.py",
            "scripts/cleanup-r2-orphans.py",
            ".github/workflows/sync-geom-to-r2.yml",
        )
        combined = "\n".join((ROOT / path).read_text() for path in paths)
        for token in (
            "Version" + "Id", "version_" + "id",
            "version-" + "id", "?version" + "Id",
        ):
            self.assertNotIn(token, combined)
        stage = (ROOT / "scripts/stage-changed-r2.py").read_text()
        delete_commands = re.findall(
            r'\[\s*"aws",\s*"s3api",\s*"delete-object"(?P<body>.*?)\]',
            stage,
            re.S,
        )
        self.assertEqual(len(delete_commands), 1)
        self.assertNotIn('"--if-match"', delete_commands[0])
        self.assertNotIn('"delete-objects"', delete_commands[0])
        cleanup = (ROOT / "scripts/cleanup-r2-orphans.py").read_text()
        self.assertNotRegex(
            cleanup,
            r'"aws"\s*,\s*"s3api"\s*,\s*"delete-object"',
        )
        self.assertIn("quarantine source removal is disabled", cleanup)

    def test_python_and_workflow_syntax_is_offline(self) -> None:
        for path in (item for item in self.LEASED if item.endswith(".py")):
            completed = subprocess.run(
                [sys.executable, "-m", "py_compile", str(ROOT / path)],
                env=MINIMAL_SUBPROCESS_ENV,
                capture_output=True,
                text=True,
            )
            with self.subTest(path=path):
                self.assertEqual(completed.returncode, 0, completed.stderr)
        # GitHub expressions make generic YAML parsers unreliable; assert the
        # structural contract with a deterministic indentation/keys scan.
        for path in (item for item in self.LEASED if item.endswith((".yml", ".yaml"))):
            text = (ROOT / path).read_text()
            self.assertRegex(text, r"(?m)^name: .+")
            self.assertRegex(text, r"(?m)^on:\s*$")
            self.assertRegex(text, r"(?m)^jobs:\s*$")
            self.assertNotIn("\t", text)


if __name__ == "__main__":
    print("R2 campaign: exact-five-key scoped Mols, semantic globals, IF_MATCH replace/restore, rollback/reconciliation, provider adapter, workflow wiring, and Round-A hash replay")
    unittest.main(verbosity=2)
