#!/usr/bin/env python3
"""Offline hostile tests for the versioned R2 release/quarantine contract."""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from r2_release_contract import (  # noqa: E402
    BUCKET,
    CI_PRODUCER_GRAPH,
    CONTRACT_VERSION,
    GLOBAL_OBJECTS,
    INVENTORY_FAULT_NAMES,
    MAX_INVENTORY_PAGES,
    PRODUCTION_PRODUCER_BOUNDARIES,
    InventoryError,
    KeyClass,
    ManifestError,
    TerminalState,
    audit_repository_contract,
    build_producer_population,
    build_release_preflight,
    build_quarantine_plan,
    build_upload_manifest as build_upload_manifest_contract,
    canonical_sha256,
    classify_key,
    derive_production_producer_graph,
    execute_quarantine as execute_quarantine_contract,
    execute_release as execute_release_contract,
    fetch_complete_inventory,
    identity_ref,
    inventory_objects,
    local_object as local_object_contract,
    make_approval_assertion,
    normalize_sha256_checksum,
    provider_identity_attestation,
    quarantine_key,
    reconcile_quarantine_write_ahead,
    reconcile_release_write_ahead,
    release_boundaries,
    strict_json_loads,
    validate_inventory_receipt,
    validate_quarantine_plan,
    validate_quarantine_receipt,
    validate_release_preflight,
    validate_release_receipt,
    validate_upload_manifest,
)

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
        "VersionId": "version-" + hashlib.sha256(key.encode()).hexdigest()[:12],
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
    """In-memory exact-version store whose call log exposes every effect."""

    def __init__(self, objects: dict[str, dict[str, Any]]):
        self.objects = copy.deepcopy(objects)
        self.calls: list[tuple[Any, ...]] = []
        self.fault: str | None = None

    def attest_identity(self) -> dict[str, Any]:
        return provider_identity_attestation(ACCOUNT_ID, ENDPOINT_URL)

    def head(self, key: str, *, version_id: str | None = None) -> dict[str, Any] | None:
        self.calls.append(("head", key, version_id))
        value = self.objects.get(key)
        if value is None or (version_id is not None and value["version_id"] != version_id):
            return None
        return copy.deepcopy(value)

    def copy_no_clobber(
        self,
        source: str,
        destination: str,
        *,
        source_preimage: dict[str, Any],
        attempt_id: str,
    ) -> dict[str, Any]:
        self.calls.append(("copy_no_clobber", source, destination, copy.deepcopy(source_preimage), attempt_id))
        if self.fault == "copy":
            raise RuntimeError("copy fault")
        if destination in self.objects:
            raise RuntimeError("destination collision")
        current = self.objects.get(source)
        if current != source_preimage:
            raise RuntimeError("source preimage changed")
        copied = copy.deepcopy(current)
        copied["version_id"] = "quarantine-" + hashlib.sha256(destination.encode()).hexdigest()[:12]
        if self.fault == "corrupt_copy":
            copied["sha256"] = "f" * 64
        self.objects[destination] = copied
        return {
            "transaction_id": "copy-tx-" + hashlib.sha256(destination.encode()).hexdigest()[:16],
            "version_id": copied["version_id"],
            "attempt_id": attempt_id,
        }

    def remove_if_match(
        self,
        source: str,
        *,
        source_preimage: dict[str, Any],
        attempt_id: str,
    ) -> dict[str, Any]:
        self.calls.append(("remove_if_match", source, copy.deepcopy(source_preimage), attempt_id))
        if self.fault == "remove":
            raise RuntimeError("remove fault")
        if self.objects.get(source) != source_preimage:
            raise RuntimeError("conditional source preimage changed")
        del self.objects[source]
        return {
            "transaction_id": "remove-tx-" + hashlib.sha256(source.encode()).hexdigest()[:16],
            "version_id": source_preimage["version_id"],
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
                    "version_id": "unexpected-existing-version",
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
                    "transaction_id", "version_id",
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
            "version_id": "written-version-" + str(index),
            "transaction_id": (
                "tx-shared" if self.duplicate_transactions
                else "request-tx-" + hashlib.sha256(row["key"].encode()).hexdigest()[:20]
            ),
            "attempt_id": attempt_id,
        }


class TestKeyContract(unittest.TestCase):
    def test_version_and_exact_global_registry(self) -> None:
        self.assertEqual(CONTRACT_VERSION, "trekdex-r2-release-contract-v3")
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
            "malformed-preimage": PageSource({None: page([{**remote(GLOBAL_OBJECTS[0]), "ETag": "bad"}] + [remote(key) for key in GLOBAL_OBJECTS[1:]])}),
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
            self.assertNotEqual(copied["version_id"], row["source_preimage"]["version_id"])
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
                store.objects[destination] = {"etag": "collision", "bytes": 1, "sha256": SHA, "version_id": "collision"}
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
                    self.assertFalse(any(call[0] in {"copy_no_clobber", "remove_if_match"} for call in store.calls))

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
        self.assertIn('"--version-id"', cleanup_source)

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

    def test_quarantine_malformed_result_is_typed_and_full_version_race_preserves_newer(self) -> None:
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
                return {"transaction_id": None, "version_id": None, "attempt_id": attempt_id}

        malformed = execute_quarantine(plan, MalformedStore(preimages))
        self.assertEqual(malformed["terminal_state"], TerminalState.INDETERMINATE.value)
        validate_quarantine_receipt(malformed, plan)

        class NewerVersionStore(FakeStore):
            def copy_no_clobber(self, source: str, destination: str, *, source_preimage: dict[str, Any], attempt_id: str) -> dict[str, Any]:
                result = super().copy_no_clobber(
                    source, destination, source_preimage=source_preimage,
                    attempt_id=attempt_id,
                )
                newer = copy.deepcopy(self.objects[source])
                newer["version_id"] = "newer-current-version"
                newer["sha256"] = "d" * 64
                self.objects[source] = newer
                return result

        raced_store = NewerVersionStore(preimages)
        raced = execute_quarantine(plan, raced_store)
        self.assertEqual(raced["terminal_state"], TerminalState.INDETERMINATE.value)
        self.assertEqual(raced_store.objects["orphan-area-az.json"]["version_id"], "newer-current-version")
        self.assertFalse(any(call[0] == "remove_if_match" for call in raced_store.calls))

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
        self.assertEqual(journal["pending_effect"]["operation"], "COPY_EXACT_VERSION")
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
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
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
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
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
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
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
                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
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
                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
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
                "scripts/stage-changed-r2.py": "r2_release_contract",
                "scripts/cleanup-r2-orphans.py": "r2_release_contract",
                "scripts/r2_release_contract.py": "trekdex-r2-release-contract-v3",
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

    def test_apply_rederives_producer_population_and_disables_unproved_overwrite(self) -> None:
        files = self.files()
        stage = files["scripts/stage-changed-r2.py"]
        for marker in (
            "_derive_producer_population(args, inventory)",
            "apply-time producer population differs from reviewed bytes",
            "live overwrite disabled",
            '"get-object"',
            'pass_fds=(snapshot.fileno(),)',
        ):
            hostile = copy.deepcopy(files)
            hostile["scripts/stage-changed-r2.py"] = stage.replace(marker, "MUTATED-AWAY", 1)
            with self.subTest(marker=marker):
                self.assertIn("MUTATED-AWAY", hostile["scripts/stage-changed-r2.py"])
                self.assertNotEqual(hostile["scripts/stage-changed-r2.py"], stage)
        self.assertIn("_derive_producer_population(args, inventory)", stage)
        self.assertIn("live overwrite disabled", stage)

    def test_python_and_workflow_syntax_is_offline(self) -> None:
        for path in self.LEASED[4:]:
            completed = subprocess.run([sys.executable, "-m", "py_compile", str(ROOT / path)], capture_output=True, text=True)
            with self.subTest(path=path):
                self.assertEqual(completed.returncode, 0, completed.stderr)
        # GitHub expressions make generic YAML parsers unreliable; assert the
        # structural contract with a deterministic indentation/keys scan.
        for path in self.LEASED[:4]:
            text = (ROOT / path).read_text()
            self.assertRegex(text, r"(?m)^name: .+")
            self.assertRegex(text, r"(?m)^on:\s*$")
            self.assertRegex(text, r"(?m)^jobs:\s*$")
            self.assertNotIn("\t", text)


if __name__ == "__main__":
    print("R2 campaign: classification_hostile=21 classification_random=10000 quarantine_encoding=59704 inventory_faults=25 manifest_mutations=15 release_receipt_mutations=8 release_boundaries=20 provider_faults=26 quarantine_faults=8 quarantine_receipt_mutations=8 wiring_mutations=35 replay=100 consumer_smokes=5")
    unittest.main(verbosity=2)
