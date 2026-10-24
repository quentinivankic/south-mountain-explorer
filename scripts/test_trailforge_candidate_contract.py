#!/usr/bin/env python3
"""Offline hostile tests for the 51-lane Trailforge candidate reducer."""
from __future__ import annotations

import copy
import hashlib
import itertools
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
sys.path.insert(0, str(ROOT / "scripts"))

from trailforge_candidate_contract import (  # noqa: E402
    CONTRACT_VERSION,
    EXPECTED_LANES,
    FanInState,
    INHERITED_LANE_SCHEMA,
    LaneOutcome,
    build_review_candidate as build_review_candidate_contract,
    canonical_sha256,
    derive_approved_candidate_manifest,
    derive_authoritative_lane_semantics,
    lane_terminal as lane_terminal_contract,
    make_root_approval_assertion,
    reduce_lane_terminals as reduce_lane_terminals_contract,
    validate_lane_terminal,
    validate_root_approval_assertion,
    validate_success_artifact_against_fan_in as validate_success_artifact_against_fan_in_contract,
    validate_workflow_contract,
)

CONTRACT_TEST_SENTINEL = "RR2-ORIGINAL-CLAUSES-TRAILFORGE-V1"
SOURCE = "1" * 40
TREE = "2" * 40
WORKFLOW = "3" * 64
RUN_ID = "123456789"
RUN_ATTEMPT = 2
TOOLCHAIN = "5" * 64
INPUT_MANIFEST = "6" * 64
ARTIFACT_SERVICE = "sha256:" + "7" * 64
RETRY_LINEAGE = "8" * 64
PAYLOAD = "9" * 64
INHERITED = "a" * 64
PUBLISHER_LOG = "b" * 64


def display_state_for_lane(lane: str) -> str:
    if lane == "district-of-columbia":
        return "District of Columbia"
    return " ".join(word.capitalize() for word in lane.split("-"))


def area_id_for_lane(lane: str) -> str:
    return lane + "-area-az"


TEST_SOURCE_INDEX: list[list[str]] = [
    [area_id_for_lane(lane), "Fixture Area", display_state_for_lane(lane)]
    for lane in EXPECTED_LANES
]
TEST_AUTHORITY = derive_authoritative_lane_semantics(TEST_SOURCE_INDEX)


def geom_body(lane: str) -> bytes:
    return (
        json.dumps({
            "id": area_id_for_lane(lane),
            "state": display_state_for_lane(lane),
            "trail_count": 1,
            "total_mi": 1.0,
        }, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()


APPROVED_CANDIDATE_DOCUMENT = {
    "schema": "trekdex-trailforge-approved-inputs-v2",
    "lanes": [
        {
            "lane": lane,
            "url": f"https://example.invalid/{lane}.osm.pbf",
            "bytes": 1,
            "sha256": "c" * 64,
            "generated_geometry": [{
                "name": area_id_for_lane(lane) + ".json",
                "bytes": len(geom_body(lane)),
                "sha256": hashlib.sha256(geom_body(lane)).hexdigest(),
            }],
        }
        for lane in EXPECTED_LANES
    ],
}
APPROVED_CANDIDATE_MANIFEST = (
    json.dumps(APPROVED_CANDIDATE_DOCUMENT, sort_keys=True, separators=(",", ":"))
    + "\n"
).encode()
APPROVAL_AUTHORITY = derive_approved_candidate_manifest(
    APPROVED_CANDIDATE_MANIFEST,
    authoritative_source_index=TEST_SOURCE_INDEX,
)
APPROVED_INPUT_SET = APPROVAL_AUTHORITY["approved_input_set_sha256"]
APPROVED_INPUT_POPULATION = APPROVAL_AUTHORITY["approved_input_population_sha256"]
APPROVED_GEOMETRY_POPULATION = APPROVAL_AUTHORITY["approved_geometry_population_sha256"]
CONFIG_DOCUMENT = {
    "schema": "trekdex-trailforge-candidate-config-v3",
    "source_sha": SOURCE,
    "source_tree": TREE,
    "workflow_sha256": WORKFLOW,
    "elevation": True,
    "no_routes": True,
    "toolchain_manifest_sha256": TOOLCHAIN,
    "approved_input_set_sha256": APPROVED_INPUT_SET,
    "approved_input_population_sha256": APPROVED_INPUT_POPULATION,
    "approved_geometry_population_sha256": APPROVED_GEOMETRY_POPULATION,
    "artifact_service_ref": ARTIFACT_SERVICE,
    "source_index_sha256": TEST_AUTHORITY["source_index_sha256"],
    "lane_authority_sha256": TEST_AUTHORITY["authority_sha256"],
}
CANDIDATE_CONFIG = (
    json.dumps(CONFIG_DOCUMENT, sort_keys=True, separators=(",", ":")) + "\n"
).encode()
CONFIG = hashlib.sha256(CANDIDATE_CONFIG).hexdigest()
APPROVAL = "sha256:" + CONFIG

SYNTHETIC_APPROVAL_KEY = b"round43-synthetic-root-approval-key-v1"
SYNTHETIC_ROTATED_KEY = b"round43-synthetic-root-approval-key-v2"
SYNTHETIC_APPROVAL_KEY_ID = "round43-synthetic-key-v1"
SYNTHETIC_APPROVAL_NONCE = "round43-approved-config-v1"
ROOT_APPROVAL_ASSERTION = make_root_approval_assertion(
    CANDIDATE_CONFIG,
    approved_candidate_manifest=APPROVED_CANDIDATE_MANIFEST,
    authoritative_source_index=TEST_SOURCE_INDEX,
    root_approval_ref=APPROVAL,
    run_id=RUN_ID,
    run_attempt=RUN_ATTEMPT,
    key=SYNTHETIC_APPROVAL_KEY,
    authority_key_id=SYNTHETIC_APPROVAL_KEY_ID,
    authorization_nonce=SYNTHETIC_APPROVAL_NONCE,
)


def root_approval_env(
    assertion: Any = ROOT_APPROVAL_ASSERTION,
    *,
    key: bytes = SYNTHETIC_APPROVAL_KEY,
    key_id: str = SYNTHETIC_APPROVAL_KEY_ID,
) -> dict[str, str]:
    return {
        "TRAILFORGE_ROOT_APPROVAL_ASSERTION_JSON": json.dumps(
            assertion, sort_keys=True, separators=(",", ":")
        ),
        "TRAILFORGE_ROOT_APPROVAL_HMAC_KEY": key.decode("ascii"),
        "TRAILFORGE_ROOT_APPROVAL_KEY_ID": key_id,
    }


def build_review_candidate(
    receipt: dict[str, Any],
    builder: Any,
    *,
    approval_assertion: Any = ROOT_APPROVAL_ASSERTION,
    approval_key: bytes | str | None = SYNTHETIC_APPROVAL_KEY,
    expected_authority_key_id: str | None = SYNTHETIC_APPROVAL_KEY_ID,
) -> dict[str, Any]:
    return build_review_candidate_contract(
        receipt,
        builder,
        authoritative_source_index=TEST_SOURCE_INDEX,
        candidate_config=CANDIDATE_CONFIG,
        approved_candidate_manifest=APPROVED_CANDIDATE_MANIFEST,
        approval_assertion=approval_assertion,
        approval_key=approval_key,
        expected_authority_key_id=expected_authority_key_id,
    )


def validate_success_artifact_against_fan_in(
    artifact: Path | str,
    receipt: dict[str, Any],
    *,
    expected_lane: str,
) -> dict[str, Any]:
    return validate_success_artifact_against_fan_in_contract(
        artifact,
        receipt,
        expected_lane=expected_lane,
        authoritative_source_index=TEST_SOURCE_INDEX,
        candidate_config=CANDIDATE_CONFIG,
        approved_candidate_manifest=APPROVED_CANDIDATE_MANIFEST,
    )


def lane_terminal(**kwargs: Any) -> dict[str, Any]:
    lane = kwargs["lane"]
    kwargs.setdefault("run_id", RUN_ID)
    kwargs.setdefault("run_attempt", RUN_ATTEMPT)
    kwargs.setdefault("candidate_config", CANDIDATE_CONFIG)
    kwargs.setdefault("input_manifest_sha256", INPUT_MANIFEST)
    kwargs.setdefault("approved_candidate_manifest", APPROVED_CANDIDATE_MANIFEST)
    kwargs.setdefault("authoritative_source_index", TEST_SOURCE_INDEX)
    kwargs.setdefault(
        "matrix_job_ref",
        hashlib.sha256(f"{RUN_ID}:{RUN_ATTEMPT}:{lane}".encode()).hexdigest(),
    )
    kwargs.setdefault("retry_lineage_ref", RETRY_LINEAGE)
    kwargs.setdefault("artifact_id", f"lane-evidence-{lane}:{RUN_ID}:{RUN_ATTEMPT}")
    kwargs.setdefault("artifact_payload_sha256", PAYLOAD)
    kwargs.setdefault("inherited_receipt_sha256", INHERITED)
    kwargs.setdefault("publisher_log_sha256", PUBLISHER_LOG)
    return lane_terminal_contract(**kwargs)


def validate_terminal(receipt: Any, **kwargs: Any) -> dict[str, Any]:
    kwargs.setdefault("candidate_config", CANDIDATE_CONFIG)
    kwargs.setdefault("approved_candidate_manifest", APPROVED_CANDIDATE_MANIFEST)
    kwargs.setdefault("authoritative_source_index", TEST_SOURCE_INDEX)
    return validate_lane_terminal(receipt, **kwargs)


def reduce_lane_terminals(terminals: Any, **kwargs: Any) -> dict[str, Any]:
    kwargs.setdefault("run_id", RUN_ID)
    kwargs.setdefault("run_attempt", RUN_ATTEMPT)
    kwargs.setdefault("candidate_config", CANDIDATE_CONFIG)
    kwargs.setdefault("approved_candidate_manifest", APPROVED_CANDIDATE_MANIFEST)
    kwargs.setdefault("retry_lineage_ref", RETRY_LINEAGE)
    kwargs.setdefault("authoritative_source_index", TEST_SOURCE_INDEX)
    return reduce_lane_terminals_contract(terminals, **kwargs)


def success(lane: str) -> dict[str, Any]:
    area_id = area_id_for_lane(lane)
    body = geom_body(lane)
    return lane_terminal(
        lane=lane,
        outcome=LaneOutcome.SUCCESS,
        source_sha=SOURCE,
        source_tree=TREE,
        workflow_sha256=WORKFLOW,
        root_approval_ref=APPROVAL,
        files=[{
            "name": area_id + ".json",
            "bytes": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
        }],
        reason=None,
    )


def adverse(lane: str, outcome: LaneOutcome) -> dict[str, Any]:
    return lane_terminal(
        lane=lane,
        outcome=outcome,
        source_sha=SOURCE,
        source_tree=TREE,
        workflow_sha256=WORKFLOW,
        root_approval_ref=APPROVAL,
        files=[],
        reason="typed-" + outcome.value.lower(),
    )


class EffectSpy:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def build(self, receipt: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("build", copy.deepcopy(receipt)))
        return {"candidate_sha256": canonical_sha256(receipt)}

    def commit(self) -> None:  # pragma: no cover
        self.calls.append(("commit", None))

    def push(self) -> None:  # pragma: no cover
        self.calls.append(("push", None))

    def synchronize(self) -> None:  # pragma: no cover
        self.calls.append(("synchronize", None))

    def provider_write(self) -> None:  # pragma: no cover
        self.calls.append(("provider_write", None))


class TestRootApprovalAuthority(unittest.TestCase):
    def validate(
        self,
        assertion: Any = ROOT_APPROVAL_ASSERTION,
        *,
        key: bytes | str | None = SYNTHETIC_APPROVAL_KEY,
        key_id: str | None = SYNTHETIC_APPROVAL_KEY_ID,
        run_id: str = RUN_ID,
        run_attempt: int = RUN_ATTEMPT,
    ) -> str:
        return validate_root_approval_assertion(
            assertion,
            CANDIDATE_CONFIG,
            approved_candidate_manifest=APPROVED_CANDIDATE_MANIFEST,
            authoritative_source_index=TEST_SOURCE_INDEX,
            root_approval_ref=APPROVAL,
            run_id=run_id,
            run_attempt=run_attempt,
            key=key,
            expected_authority_key_id=key_id,
        )

    def test_valid_synthetic_root_approval_succeeds(self) -> None:
        self.assertEqual(
            self.validate(),
            canonical_sha256(ROOT_APPROVAL_ASSERTION),
        )
        self.assertNotIn(
            SYNTHETIC_APPROVAL_KEY.decode("ascii"),
            json.dumps(ROOT_APPROVAL_ASSERTION, sort_keys=True),
        )

    def test_missing_or_malformed_authority_fails_closed(self) -> None:
        cases: tuple[tuple[str, Any, bytes | str | None, str | None], ...] = (
            ("missing-assertion", None, SYNTHETIC_APPROVAL_KEY, SYNTHETIC_APPROVAL_KEY_ID),
            ("empty-assertion", "", SYNTHETIC_APPROVAL_KEY, SYNTHETIC_APPROVAL_KEY_ID),
            ("malformed-json", "{", SYNTHETIC_APPROVAL_KEY, SYNTHETIC_APPROVAL_KEY_ID),
            ("duplicate-json-key", '{"schema":"x","schema":"y"}', SYNTHETIC_APPROVAL_KEY, SYNTHETIC_APPROVAL_KEY_ID),
            ("wrong-schema", {}, SYNTHETIC_APPROVAL_KEY, SYNTHETIC_APPROVAL_KEY_ID),
            ("missing-key", ROOT_APPROVAL_ASSERTION, None, SYNTHETIC_APPROVAL_KEY_ID),
            ("short-key", ROOT_APPROVAL_ASSERTION, b"short", SYNTHETIC_APPROVAL_KEY_ID),
            ("missing-key-id", ROOT_APPROVAL_ASSERTION, SYNTHETIC_APPROVAL_KEY, None),
        )
        for name, assertion, key, key_id in cases:
            with self.subTest(name=name):
                with self.assertRaises((TypeError, ValueError)):
                    self.validate(assertion, key=key, key_id=key_id)

    def test_mismatched_signature_and_rotated_key_fail_closed(self) -> None:
        mismatched = copy.deepcopy(ROOT_APPROVAL_ASSERTION)
        mismatched["signature_hmac_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "signature mismatch"):
            self.validate(mismatched)
        with self.assertRaisesRegex(ValueError, "signature mismatch"):
            self.validate(key=SYNTHETIC_ROTATED_KEY)
        with self.assertRaisesRegex(ValueError, "key ID mismatch"):
            self.validate(key_id="round43-synthetic-key-v2")

    def test_every_bound_config_field_and_ref_tamper_fails_closed(self) -> None:
        for field, value in ROOT_APPROVAL_ASSERTION["candidate_config"].items():
            hostile = copy.deepcopy(ROOT_APPROVAL_ASSERTION)
            if isinstance(value, bool):
                replacement: Any = not value
            elif isinstance(value, str) and value.startswith("sha256:"):
                replacement = "sha256:" + "f" * 64
            elif isinstance(value, str) and len(value) == 40:
                replacement = "f" * 40
            elif isinstance(value, str) and len(value) == 64:
                replacement = "f" * 64
            else:
                replacement = str(value) + "-tampered"
            hostile["candidate_config"][field] = replacement
            with self.subTest(field=field):
                with self.assertRaisesRegex(ValueError, "candidate_config"):
                    self.validate(hostile)
        hostile_ref = copy.deepcopy(ROOT_APPROVAL_ASSERTION)
        hostile_ref["root_approval_ref"] = "sha256:" + "f" * 64
        with self.assertRaisesRegex(ValueError, "root_approval_ref"):
            self.validate(hostile_ref)

    def test_assertion_replay_across_run_or_attempt_fails_closed(self) -> None:
        for name, run_id, run_attempt in (
            ("different-run", "987654321", RUN_ATTEMPT),
            ("rerun-attempt", RUN_ID, RUN_ATTEMPT + 1),
        ):
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, "run_id|run_attempt"):
                    self.validate(run_id=run_id, run_attempt=run_attempt)

    def test_complete_success_cannot_build_without_authority(self) -> None:
        receipt = reduce_lane_terminals(
            [success(lane) for lane in EXPECTED_LANES],
            source_sha=SOURCE,
            source_tree=TREE,
            workflow_sha256=WORKFLOW,
            root_approval_ref=APPROVAL,
        )
        spy = EffectSpy()
        with self.assertRaisesRegex(ValueError, "authority|assertion"):
            build_review_candidate(
                receipt,
                spy.build,
                approval_assertion=None,
                approval_key=None,
                expected_authority_key_id=None,
            )
        self.assertEqual(spy.calls, [])


class TestLaneContract(unittest.TestCase):
    def test_exact_lane_set_and_outcome_enum(self) -> None:
        self.assertEqual(CONTRACT_VERSION, "trekdex-trailforge-candidate-contract-v3")
        self.assertEqual(len(EXPECTED_LANES), 51)
        self.assertEqual(len(set(EXPECTED_LANES)), 51)
        self.assertEqual(tuple(sorted(EXPECTED_LANES)), EXPECTED_LANES)
        self.assertEqual(
            {member.value for member in LaneOutcome},
            {"SUCCESS", "FAILED", "MISSING", "CORRUPT", "CANCELLED", "SKIPPED", "STALE", "IDENTITY_MISMATCH"},
        )

    def test_success_and_every_adverse_receipt_are_typed(self) -> None:
        lane = EXPECTED_LANES[0]
        good = validate_terminal(success(lane), expected_lane=lane, source_sha=SOURCE, source_tree=TREE, workflow_sha256=WORKFLOW, root_approval_ref=APPROVAL)
        self.assertEqual(good["outcome"], LaneOutcome.SUCCESS.value)
        self.assertFalse(good["promotable"])
        for outcome in LaneOutcome:
            if outcome is LaneOutcome.SUCCESS:
                continue
            receipt = validate_terminal(adverse(lane, outcome), expected_lane=lane, source_sha=SOURCE, source_tree=TREE, workflow_sha256=WORKFLOW, root_approval_ref=APPROVAL)
            with self.subTest(outcome=outcome.value):
                self.assertEqual(receipt["outcome"], outcome.value)
                self.assertEqual(receipt["files"], [])
                self.assertFalse(receipt["committed"])
                self.assertFalse(receipt["pushed"])
                self.assertFalse(receipt["synchronized"])
                self.assertFalse(receipt["provider_effect"])

    def test_canonical_unicode_area_filename_is_supported(self) -> None:
        lane = EXPECTED_LANES[0]
        area_id = "haleakalā-national-park-hi"
        body = b"unicode-area"
        source_index = copy.deepcopy(TEST_SOURCE_INDEX)
        source_index[0][0] = area_id
        source_authority = derive_authoritative_lane_semantics(source_index)
        approved_document = copy.deepcopy(APPROVED_CANDIDATE_DOCUMENT)
        approved_document["lanes"][0]["generated_geometry"] = [{
            "name": area_id + ".json",
            "bytes": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
        }]
        approved_manifest = (
            json.dumps(
                approved_document,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode()
        approval_authority = derive_approved_candidate_manifest(
            approved_manifest, authoritative_source_index=source_index
        )
        config_document = copy.deepcopy(CONFIG_DOCUMENT)
        config_document.update({
            "approved_input_set_sha256": approval_authority["approved_input_set_sha256"],
            "approved_input_population_sha256": approval_authority["approved_input_population_sha256"],
            "approved_geometry_population_sha256": approval_authority["approved_geometry_population_sha256"],
            "source_index_sha256": source_authority["source_index_sha256"],
            "lane_authority_sha256": source_authority["authority_sha256"],
        })
        candidate_config = (
            json.dumps(config_document, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode()
        approval = "sha256:" + hashlib.sha256(candidate_config).hexdigest()
        receipt = lane_terminal(
            lane=lane,
            outcome=LaneOutcome.SUCCESS,
            source_sha=SOURCE,
            source_tree=TREE,
            workflow_sha256=WORKFLOW,
            root_approval_ref=approval,
            candidate_config=candidate_config,
            approved_candidate_manifest=approved_manifest,
            authoritative_source_index=source_index,
            files=[{
                "name": area_id + ".json",
                "bytes": len(body),
                "sha256": hashlib.sha256(body).hexdigest(),
            }],
            reason=None,
        )
        self.assertEqual(
            validate_terminal(
                receipt,
                candidate_config=candidate_config,
                approved_candidate_manifest=approved_manifest,
                authoritative_source_index=source_index,
            )["files"][0]["name"],
            area_id + ".json",
        )

    def test_receipt_mutations_fail(self) -> None:
        lane = EXPECTED_LANES[0]
        base = success(lane)
        mutations = [
            ("lane", lambda d: d.__setitem__("lane", "unknown")),
            ("display-state", lambda d: d.__setitem__("display_state", "")),
            ("source-index", lambda d: d.__setitem__("source_index_sha256", "0" * 64)),
            ("authority", lambda d: d.__setitem__("authority_sha256", "0" * 64)),
            ("outcome", lambda d: d.__setitem__("outcome", "BOGUS")),
            ("source", lambda d: d.__setitem__("source_sha", "A" * 40)),
            ("tree", lambda d: d.__setitem__("source_tree", "2" * 39)),
            ("workflow", lambda d: d.__setitem__("workflow_sha256", "3" * 63)),
            ("approval", lambda d: d.__setitem__("root_approval_ref", "main")),
            ("file-hash", lambda d: d["files"][0].__setitem__("sha256", "0" * 64)),
            ("duplicate-file", lambda d: d["files"].append(copy.deepcopy(d["files"][0]))),
            ("side-effect", lambda d: d.__setitem__("pushed", True)),
            ("digest", lambda d: d.__setitem__("receipt_sha256", "0" * 64)),
        ]
        for name, mutation in mutations:
            hostile = copy.deepcopy(base)
            mutation(hostile)
            with self.subTest(name=name):
                with self.assertRaises((TypeError, ValueError)):
                    validate_terminal(hostile, expected_lane=lane, source_sha=SOURCE, source_tree=TREE, workflow_sha256=WORKFLOW, root_approval_ref=APPROVAL)


class TestFixedReducer(unittest.TestCase):
    def all_success(self) -> list[dict[str, Any]]:
        return [success(lane) for lane in EXPECTED_LANES]

    def reduce(self, terminals: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        return reduce_lane_terminals(
            terminals,
            source_sha=SOURCE,
            source_tree=TREE,
            workflow_sha256=WORKFLOW,
            root_approval_ref=APPROVAL,
            **kwargs,
        )

    def test_all_success_is_review_only_complete_success(self) -> None:
        receipt = self.reduce(self.all_success())
        self.assertEqual(receipt["fan_in_state"], FanInState.COMPLETE_SUCCESS.value)
        self.assertTrue(receipt["review_candidate_allowed"])
        self.assertEqual(receipt["success_count"], 51)
        self.assertEqual(receipt["workflow_adverse_count"], 0)
        self.assertEqual(receipt["adverse_count"], 0)
        self.assertFalse(receipt["committed"])
        self.assertFalse(receipt["pushed"])
        self.assertFalse(receipt["synchronized"])
        self.assertFalse(receipt["provider_effect"])
        self.assertEqual(receipt["receipt_sha256"], canonical_sha256(receipt, omit={"receipt_sha256"}))

    def test_r35_m_002_lane_wrong_display_state_is_nonpromotable(self) -> None:
        terminals = self.all_success()
        terminals[0]["display_state"] = "Coherently Wrong Display State"
        terminals[0]["receipt_sha256"] = canonical_sha256(
            terminals[0], omit={"receipt_sha256"}
        )
        receipt = self.reduce(terminals)
        self.assertEqual(receipt["fan_in_state"], FanInState.NON_PROMOTABLE.value)
        self.assertFalse(receipt["review_candidate_allowed"])
        self.assertEqual(receipt["lanes"][0]["outcome"], LaneOutcome.CORRUPT.value)

        good = self.reduce(self.all_success())
        substituted_index = copy.deepcopy(TEST_SOURCE_INDEX)
        substituted_index[0][0] = "substituted-area-al"
        called: list[dict[str, Any]] = []
        with self.assertRaisesRegex(ValueError, "pinned source population|source-index authority"):
            build_review_candidate_contract(
                good,
                lambda value: called.append(value) or {},
                authoritative_source_index=substituted_index,
                candidate_config=CANDIDATE_CONFIG,
                approved_candidate_manifest=APPROVED_CANDIDATE_MANIFEST,
            )
        self.assertEqual(called, [])

    def test_every_adverse_outcome_in_every_lane_position_is_nonpromotable(self) -> None:
        baseline = self.all_success()
        probes = 0
        for position, lane in enumerate(EXPECTED_LANES):
            for outcome in LaneOutcome:
                if outcome is LaneOutcome.SUCCESS:
                    continue
                terminals = copy.deepcopy(baseline)
                terminals[position] = adverse(lane, outcome)
                receipt = self.reduce(terminals)
                probes += 1
                with self.subTest(position=position, outcome=outcome.value):
                    self.assertEqual(receipt["fan_in_state"], FanInState.NON_PROMOTABLE.value)
                    self.assertFalse(receipt["review_candidate_allowed"])
                    self.assertEqual(receipt["adverse_count"], 1)
                    self.assertEqual(receipt["lanes"][position]["outcome"], outcome.value)
                    self.assertFalse(any(receipt[name] for name in ("committed", "pushed", "synchronized", "provider_effect")))
        self.assertEqual(probes, 51 * 7)

    def test_missing_extra_duplicate_cross_lane_and_identity_faults(self) -> None:
        all_success = self.all_success()
        cases: list[tuple[str, list[dict[str, Any]]]] = [
            ("missing", all_success[:-1]),
            ("duplicate", all_success + [copy.deepcopy(all_success[0])]),
            ("extra", all_success + [{**copy.deepcopy(all_success[0]), "lane": "extra-lane"}]),
        ]
        cross = copy.deepcopy(all_success)
        cross[0]["lane"] = EXPECTED_LANES[1]
        cases.append(("cross-lane", cross))
        stale = copy.deepcopy(all_success)
        stale[0]["source_sha"] = "9" * 40
        cases.append(("stale-source", stale))
        corrupt = copy.deepcopy(all_success)
        corrupt[0]["receipt_sha256"] = "0" * 64
        cases.append(("corrupt", corrupt))
        for name, terminals in cases:
            receipt = self.reduce(terminals)
            with self.subTest(name=name):
                self.assertEqual(receipt["fan_in_state"], FanInState.NON_PROMOTABLE.value)
                self.assertFalse(receipt["review_candidate_allowed"])
                self.assertGreater(receipt["adverse_count"], 0)

    def test_arrival_order_independent_for_all_success_and_simultaneous_faults(self) -> None:
        rng = random.Random(0xC004)
        baseline = self.all_success()
        expected = self.reduce(baseline)
        expected_digest = expected["receipt_sha256"]
        for _ in range(256):
            shuffled = copy.deepcopy(baseline)
            rng.shuffle(shuffled)
            self.assertEqual(self.reduce(shuffled)["receipt_sha256"], expected_digest)

        adverse_set = copy.deepcopy(baseline)
        adverse_set[3] = adverse(EXPECTED_LANES[3], LaneOutcome.CANCELLED)
        adverse_set[17] = adverse(EXPECTED_LANES[17], LaneOutcome.CORRUPT)
        adverse_set[49] = adverse(EXPECTED_LANES[49], LaneOutcome.IDENTITY_MISMATCH)
        expected_adverse = self.reduce(adverse_set)["receipt_sha256"]
        for _ in range(256):
            shuffled = copy.deepcopy(adverse_set)
            rng.shuffle(shuffled)
            self.assertEqual(self.reduce(shuffled)["receipt_sha256"], expected_adverse)

    def test_exhaustive_zero_to_five_lane_raw_permutations(self) -> None:
        total = 0
        for count in range(6):
            subset = [success(lane) for lane in EXPECTED_LANES[:count]]
            expected = self.reduce(subset)["receipt_sha256"]
            for order in itertools.permutations(subset):
                total += 1
                self.assertEqual(self.reduce(list(order))["receipt_sha256"], expected)
        self.assertEqual(total, 154)

    def test_adjacent_independent_swaps_preserve_result(self) -> None:
        events = self.all_success()
        baseline = self.reduce(events)["receipt_sha256"]
        for index in range(len(events) - 1):
            swapped = copy.deepcopy(events)
            swapped[index], swapped[index + 1] = swapped[index + 1], swapped[index]
            with self.subTest(index=index):
                self.assertEqual(self.reduce(swapped)["receipt_sha256"], baseline)

    def test_all_success_artifacts_cannot_override_adverse_workflow_outcome(self) -> None:
        for outcome in LaneOutcome:
            if outcome is LaneOutcome.SUCCESS:
                continue
            receipt = self.reduce(self.all_success(), workflow_outcome=outcome)
            with self.subTest(outcome=outcome.value):
                self.assertEqual(receipt["fan_in_state"], FanInState.NON_PROMOTABLE.value)
                self.assertFalse(receipt["review_candidate_allowed"])
                self.assertEqual(receipt["success_count"], 51)
                self.assertEqual(receipt["workflow_adverse_count"], 1)
                self.assertEqual(receipt["adverse_count"], 1)

    def test_missing_slots_preserve_cancelled_and_skipped_orchestration_facts(self) -> None:
        for outcome in (LaneOutcome.CANCELLED, LaneOutcome.SKIPPED):
            receipt = self.reduce([], workflow_outcome=outcome)
            with self.subTest(outcome=outcome.value):
                self.assertEqual({row["outcome"] for row in receipt["lanes"]}, {outcome.value})
                self.assertEqual(receipt["fan_in_state"], FanInState.NON_PROMOTABLE.value)
                self.assertEqual(receipt["adverse_count"], 52)

    def test_whole_workflow_cancellation_is_visibly_pending(self) -> None:
        receipt = self.reduce([], workflow_outcome=LaneOutcome.CANCELLED, collector_observed=False)
        self.assertEqual(receipt["fan_in_state"], FanInState.PENDING_HOSTED_CANCELLATION.value)
        self.assertTrue(receipt["hosted_cancellation_proof_pending"])
        self.assertFalse(receipt["review_candidate_allowed"])
        self.assertEqual(receipt["success_count"], 0)

    def test_no_partial_release_even_with_fifty_successes(self) -> None:
        for missing_position in range(51):
            terminals = [terminal for index, terminal in enumerate(self.all_success()) if index != missing_position]
            receipt = self.reduce(terminals)
            with self.subTest(missing_position=missing_position):
                self.assertFalse(receipt["review_candidate_allowed"])
                self.assertEqual(receipt["fan_in_state"], FanInState.NON_PROMOTABLE.value)
                self.assertEqual(receipt["success_count"], 50)

    def test_adverse_reducer_has_zero_candidate_commit_push_sync_provider_calls(self) -> None:
        for outcome in LaneOutcome:
            if outcome is LaneOutcome.SUCCESS:
                continue
            terminals = self.all_success()
            terminals[0] = adverse(EXPECTED_LANES[0], outcome)
            receipt = self.reduce(terminals)
            spy = EffectSpy()
            with self.subTest(outcome=outcome.value):
                with self.assertRaises(RuntimeError):
                    build_review_candidate(receipt, spy.build)
                self.assertEqual(spy.calls, [])

    def test_success_calls_only_local_review_candidate_builder(self) -> None:
        receipt = self.reduce(self.all_success())
        spy = EffectSpy()
        result = build_review_candidate(receipt, spy.build)
        self.assertEqual(result["candidate_sha256"], canonical_sha256(receipt))
        self.assertEqual([call[0] for call in spy.calls], ["build"])

    def test_r38_h_003_coherent_manifest_and_approval_ref_rewrite_is_unauthorized(self) -> None:
        for kind in ("input", "geometry"):
            approved = copy.deepcopy(APPROVED_CANDIDATE_DOCUMENT)
            if kind == "input":
                approved["lanes"][0]["sha256"] = "e" * 64
            else:
                approved["lanes"][0]["generated_geometry"][0]["sha256"] = "e" * 64
            rewritten_manifest = (
                json.dumps(approved, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode()
            rewritten_authority = derive_approved_candidate_manifest(
                rewritten_manifest,
                authoritative_source_index=TEST_SOURCE_INDEX,
            )
            rewritten_config_document = copy.deepcopy(CONFIG_DOCUMENT)
            rewritten_config_document.update({
                "approved_input_set_sha256": rewritten_authority["approved_input_set_sha256"],
                "approved_input_population_sha256": rewritten_authority["approved_input_population_sha256"],
                "approved_geometry_population_sha256": rewritten_authority["approved_geometry_population_sha256"],
            })
            rewritten_config = (
                json.dumps(
                    rewritten_config_document,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode()
            rewritten_ref = "sha256:" + hashlib.sha256(rewritten_config).hexdigest()
            self.assertNotEqual(rewritten_manifest, APPROVED_CANDIDATE_MANIFEST)
            self.assertNotEqual(rewritten_ref, APPROVAL)

            terminals = []
            for lane in EXPECTED_LANES:
                terminals.append(lane_terminal_contract(
                    lane=lane,
                    outcome=LaneOutcome.SUCCESS,
                    source_sha=SOURCE,
                    source_tree=TREE,
                    workflow_sha256=WORKFLOW,
                    root_approval_ref=rewritten_ref,
                    run_id=RUN_ID,
                    run_attempt=RUN_ATTEMPT,
                    candidate_config=rewritten_config,
                    input_manifest_sha256=INPUT_MANIFEST,
                    approved_candidate_manifest=rewritten_manifest,
                    authoritative_source_index=TEST_SOURCE_INDEX,
                    matrix_job_ref=hashlib.sha256(
                        f"{RUN_ID}:{RUN_ATTEMPT}:{lane}".encode()
                    ).hexdigest(),
                    retry_lineage_ref=RETRY_LINEAGE,
                    artifact_id=f"lane-evidence-{lane}:{RUN_ID}:{RUN_ATTEMPT}",
                    artifact_payload_sha256=PAYLOAD,
                    inherited_receipt_sha256=INHERITED,
                    publisher_log_sha256=PUBLISHER_LOG,
                    files=rewritten_authority["lanes"][lane]["generated_geometry"],
                    reason=None,
                ))
            coherently_rewritten = reduce_lane_terminals_contract(
                terminals,
                source_sha=SOURCE,
                source_tree=TREE,
                workflow_sha256=WORKFLOW,
                root_approval_ref=rewritten_ref,
                run_id=RUN_ID,
                run_attempt=RUN_ATTEMPT,
                candidate_config=rewritten_config,
                approved_candidate_manifest=rewritten_manifest,
                retry_lineage_ref=RETRY_LINEAGE,
                authoritative_source_index=TEST_SOURCE_INDEX,
            )
            self.assertEqual(
                coherently_rewritten["fan_in_state"],
                FanInState.COMPLETE_SUCCESS.value,
            )
            spy = EffectSpy()
            with self.subTest(kind=kind):
                with self.assertRaisesRegex(
                    ValueError, "root approval assertion has wrong"
                ):
                    build_review_candidate_contract(
                        coherently_rewritten,
                        spy.build,
                        authoritative_source_index=TEST_SOURCE_INDEX,
                        candidate_config=rewritten_config,
                        approved_candidate_manifest=rewritten_manifest,
                        approval_assertion=ROOT_APPROVAL_ASSERTION,
                        approval_key=SYNTHETIC_APPROVAL_KEY,
                        expected_authority_key_id=SYNTHETIC_APPROVAL_KEY_ID,
                    )
                self.assertEqual(spy.calls, [])

    def test_provider_result_transaction_mutation_blocks_candidate(self) -> None:
        forged = self.reduce(self.all_success())
        forged["provider_result_transaction_sha256"] = "0" * 64
        forged["receipt_sha256"] = canonical_sha256(
            forged, omit={"receipt_sha256"}
        )
        called: list[dict[str, Any]] = []
        with self.assertRaisesRegex(ValueError, "provider/result transaction"):
            build_review_candidate(forged, lambda value: called.append(value) or {})
        self.assertEqual(called, [])

    def test_recomputed_digest_cannot_hide_inconsistent_fan_in_semantics(self) -> None:
        base = self.reduce(self.all_success())
        mutations = []

        def adverse_lane(document: dict[str, Any]) -> None:
            document["lanes"][0] = adverse(EXPECTED_LANES[0], LaneOutcome.CANCELLED)

        mutations.append(("adverse-lane", adverse_lane))
        mutations.append(("workflow-cancelled", lambda d: d.__setitem__("workflow_outcome", "CANCELLED")))
        mutations.append(("false-success-count", lambda d: d.__setitem__("success_count", 50)))
        mutations.append(("false-adverse-count", lambda d: d.__setitem__("adverse_count", 1)))
        mutations.append(("false-pending", lambda d: d.__setitem__("hosted_cancellation_proof_pending", True)))
        for name, mutation in mutations:
            forged = copy.deepcopy(base)
            mutation(forged)
            forged["receipt_sha256"] = canonical_sha256(forged, omit={"receipt_sha256"})
            called = []
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    build_review_candidate(forged, lambda receipt: called.append(receipt) or {})
                self.assertEqual(called, [])

    def test_r35_l_001_rehashed_observed_terminal_digest_blocks_candidate(self) -> None:
        receipt = self.reduce(self.all_success())
        for name, mutate_raw in (("link-only", False), ("raw-and-link", True)):
            forged = copy.deepcopy(receipt)
            forged["observed_inputs"][0]["terminal_sha256"] = "0" * 64
            if mutate_raw:
                forged["observed_inputs"][0]["raw_terminal_sha256"] = "0" * 64
            forged["observed_inputs"].sort(
                key=lambda row: (
                    row["slot"], row["raw_terminal_sha256"], row["terminal_sha256"]
                )
            )
            forged["observed_inputs_sha256"] = canonical_sha256(
                forged["observed_inputs"]
            )
            forged["receipt_sha256"] = canonical_sha256(
                forged, omit={"receipt_sha256"}
            )
            called: list[dict[str, Any]] = []
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, "canonical lane terminal"):
                    build_review_candidate(
                        forged, lambda value: called.append(value) or {}
                    )
                self.assertEqual(called, [])

    def test_replay_determinism(self) -> None:
        baseline = self.all_success()
        first = json.dumps(self.reduce(baseline), sort_keys=True, separators=(",", ":"))
        for _ in range(100):
            self.assertEqual(json.dumps(self.reduce(copy.deepcopy(baseline)), sort_keys=True, separators=(",", ":")), first)


def write_success_artifact_set(artifacts: Path) -> None:
    artifacts.parent.joinpath("source-index.json").write_text(
        json.dumps(TEST_SOURCE_INDEX, sort_keys=True, separators=(",", ":")) + "\n"
    )
    artifacts.parent.joinpath("approved-candidate-manifest.json").write_bytes(
        APPROVED_CANDIDATE_MANIFEST
    )
    artifacts.parent.joinpath("candidate-config.json").write_bytes(CANDIDATE_CONFIG)
    for lane in EXPECTED_LANES:
        artifact = artifacts / f"lane-evidence-{lane}-{RUN_ID}-{RUN_ATTEMPT}"
        geom = artifact / "geom"
        evidence = artifact / "lane-evidence"
        geom.mkdir(parents=True)
        evidence.mkdir()
        terminal = success(lane)
        file_row = terminal["files"][0]
        geom.joinpath(file_row["name"]).write_bytes(geom_body(lane))
        area_id = Path(file_row["name"]).stem
        expected_path = artifact / "expected-state-areas.json"
        expected_path.write_text(
            json.dumps({
                "schema": "trekdex-pinned-state-area-set-v1",
                "state": display_state_for_lane(lane),
                "area_count": 1,
                "area_ids_sha256": terminal["expected_area_ids_sha256"],
                "area_ids": [area_id],
            }, sort_keys=True, separators=(",", ":")) + "\n"
        )
        input_path = artifact / "input-manifest.json"
        input_path.write_text(json.dumps({
            "schema": "trekdex-trailforge-input-v2",
            "lane": lane,
            "source_sha": SOURCE,
            "run_id": RUN_ID,
            "run_attempt": RUN_ATTEMPT,
            "approved_input_set_sha256": APPROVED_INPUT_SET,
            "approved_input_population_sha256": APPROVED_INPUT_POPULATION,
            "approved_geometry_population_sha256": APPROVED_GEOMETRY_POPULATION,
            "url": f"https://example.invalid/{lane}.osm.pbf",
            "bytes": 1,
            "sha256": "c" * 64,
        }, sort_keys=True, separators=(",", ":")) + "\n")
        log_path = artifact / "publisher.log"
        log_path.write_text("publisher succeeded\n")
        inherited_path = geom / "trekdex-lane-receipt.json"
        inherited = {
            "schema": INHERITED_LANE_SCHEMA,
            "lane": lane,
            "display_state": display_state_for_lane(lane),
            "source_sha": SOURCE,
            "source_tree": TREE,
            "workflow_sha256": WORKFLOW,
            "candidate_config_sha256": CONFIG,
            "run_id": RUN_ID,
            "run_attempt": RUN_ATTEMPT,
            "toolchain_manifest_sha256": TOOLCHAIN,
            "input_manifest_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
            "approved_input_set_sha256": APPROVED_INPUT_SET,
            "approved_input_population_sha256": APPROVED_INPUT_POPULATION,
            "approved_geometry_population_sha256": APPROVED_GEOMETRY_POPULATION,
            "source_index_sha256": TEST_AUTHORITY["source_index_sha256"],
            "authority_sha256": TEST_AUTHORITY["authority_sha256"],
            "expected_area_count": 1,
            "expected_area_ids_sha256": terminal["expected_area_ids_sha256"],
            "files": terminal["files"],
        }
        inherited["receipt_sha256"] = canonical_sha256(inherited)
        inherited_path.write_text(
            json.dumps(inherited, sort_keys=True, separators=(",", ":")) + "\n"
        )
        payload_paths = [expected_path, input_path, log_path, *sorted(geom.iterdir())]
        framed = bytearray()
        for path in sorted(payload_paths, key=lambda item: item.relative_to(artifact).as_posix()):
            relative = path.relative_to(artifact).as_posix().encode()
            payload = path.read_bytes()
            framed.extend(len(relative).to_bytes(4, "big"))
            framed.extend(relative)
            framed.extend(len(payload).to_bytes(8, "big"))
            framed.extend(payload)
        terminal["input_manifest_sha256"] = hashlib.sha256(input_path.read_bytes()).hexdigest()
        terminal["inherited_receipt_sha256"] = hashlib.sha256(inherited_path.read_bytes()).hexdigest()
        terminal["publisher_log_sha256"] = hashlib.sha256(log_path.read_bytes()).hexdigest()
        terminal["artifact_payload_sha256"] = hashlib.sha256(framed).hexdigest()
        terminal["receipt_sha256"] = canonical_sha256(terminal, omit={"receipt_sha256"})
        evidence.joinpath("lane-terminal.json").write_text(
            json.dumps(terminal, sort_keys=True, separators=(",", ":")) + "\n"
        )


class TestRound29TrailforgeCorrections(unittest.TestCase):
    def reduce(self, terminals: list[dict[str, Any]]) -> dict[str, Any]:
        return reduce_lane_terminals(
            terminals,
            source_sha=SOURCE,
            source_tree=TREE,
            workflow_sha256=WORKFLOW,
            root_approval_ref=APPROVAL,
        )

    def fan_in(self, artifacts: Path, output: Path) -> dict[str, Any]:
        source_index = artifacts.parent / "source-index.json"
        source_index.write_text(
            json.dumps(TEST_SOURCE_INDEX, sort_keys=True, separators=(",", ":")) + "\n"
        )
        approved_manifest = artifacts.parent / "approved-candidate-manifest.json"
        approved_manifest.write_bytes(APPROVED_CANDIDATE_MANIFEST)
        candidate_config = artifacts.parent / "candidate-config.json"
        candidate_config.write_bytes(CANDIDATE_CONFIG)
        completed = subprocess.run(
            [
                sys.executable, str(ROOT / "scripts/trailforge_candidate_contract.py"),
                "fan-in", "--artifacts-root", str(artifacts),
                "--source-index", str(source_index),
                "--source-sha", SOURCE, "--source-tree", TREE,
                "--workflow-sha256", WORKFLOW, "--root-approval-ref", APPROVAL,
                "--run-id", RUN_ID, "--run-attempt", str(RUN_ATTEMPT),
                "--candidate-config", str(candidate_config),
                "--approved-candidate-manifest", str(approved_manifest),
                "--retry-lineage-ref", RETRY_LINEAGE,
                "--workflow-outcome", "SUCCESS", "--output", str(output),
            ],
            cwd=ROOT,
            env={
                **os.environ,
                **root_approval_env(),
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(output.read_text())

    def test_same_source_stale_attempt_and_behavior_identities_are_nonpromotable(self) -> None:
        fields = {
            "run_id": "987654321",
            "run_attempt": RUN_ATTEMPT + 1,
            "candidate_config_sha256": "c" * 64,
            "toolchain_manifest_sha256": "d" * 64,
            "approved_input_set_sha256": "e" * 64,
            "artifact_service_ref": "sha256:" + "f" * 64,
            "retry_lineage_ref": "0" * 64,
        }
        for field, value in fields.items():
            terminals = [success(lane) for lane in EXPECTED_LANES]
            terminals[0][field] = value
            if field == "candidate_config_sha256":
                terminals[0]["root_approval_ref"] = "sha256:" + value
            if field in {"run_id", "run_attempt"}:
                terminals[0]["artifact_id"] = (
                    f"lane-evidence-{EXPECTED_LANES[0]}:"
                    f"{terminals[0]['run_id']}:{terminals[0]['run_attempt']}"
                )
            terminals[0]["receipt_sha256"] = canonical_sha256(
                terminals[0], omit={"receipt_sha256"}
            )
            receipt = self.reduce(terminals)
            with self.subTest(field=field):
                self.assertEqual(receipt["fan_in_state"], FanInState.NON_PROMOTABLE.value)
                self.assertEqual(
                    receipt["lanes"][0]["outcome"], LaneOutcome.IDENTITY_MISMATCH.value
                )

    def test_approved_input_population_mismatch_is_nonpromotable(self) -> None:
        terminals = [success(lane) for lane in EXPECTED_LANES]
        terminals[0]["approved_input_set_sha256"] = "c" * 64
        terminals[0]["receipt_sha256"] = canonical_sha256(
            terminals[0], omit={"receipt_sha256"}
        )
        receipt = self.reduce(terminals)
        self.assertEqual(receipt["fan_in_state"], FanInState.NON_PROMOTABLE.value)
        self.assertEqual(
            receipt["lanes"][0]["outcome"], LaneOutcome.IDENTITY_MISMATCH.value
        )

    def test_observed_input_count_cannot_be_recomputed_away(self) -> None:
        receipt = self.reduce([success(lane) for lane in EXPECTED_LANES])
        forged = copy.deepcopy(receipt)
        forged["observed_input_count"] = 0
        forged["receipt_sha256"] = canonical_sha256(forged, omit={"receipt_sha256"})
        called: list[dict[str, Any]] = []
        with self.assertRaises(ValueError):
            build_review_candidate(forged, lambda value: called.append(value) or {})
        self.assertEqual(called, [])

    def test_payload_receipt_log_and_input_mutations_each_block_fan_in(self) -> None:
        mutations = (
            ("publisher-log", lambda artifact: (artifact / "publisher.log").write_text("changed\n")),
            ("inherited-receipt", lambda artifact: (artifact / "geom" / "trekdex-lane-receipt.json").write_text('{}\n')),
            ("input-manifest", lambda artifact: (artifact / "input-manifest.json").write_text('{}\n')),
            ("extra-root", lambda artifact: (artifact / "unexpected.json").write_text('{}\n')),
        )
        for name, mutation in mutations:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                artifacts = root / "artifacts"
                write_success_artifact_set(artifacts)
                first = artifacts / f"lane-evidence-{EXPECTED_LANES[0]}-{RUN_ID}-{RUN_ATTEMPT}"
                mutation(first)
                receipt = self.fan_in(artifacts, root / "fan-in.json")
                with self.subTest(name=name):
                    self.assertEqual(receipt["fan_in_state"], FanInState.NON_PROMOTABLE.value)
                    self.assertFalse(receipt["review_candidate_allowed"])

    def test_r38_l_001_exact_terminal_bytes_are_separate_and_candidate_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = root / "artifacts"
            write_success_artifact_set(artifacts)
            baseline = self.fan_in(artifacts, root / "baseline-fan-in.json")
            lane = EXPECTED_LANES[0]
            terminal_path = artifacts / (
                f"lane-evidence-{lane}-{RUN_ID}-{RUN_ATTEMPT}"
            ) / "lane-evidence" / "lane-terminal.json"
            document = json.loads(terminal_path.read_text())
            canonical_identity = canonical_sha256(document)
            alternate_bytes = (
                json.dumps(document, ensure_ascii=False, indent=2) + "\n"
            ).encode()
            terminal_path.write_bytes(alternate_bytes)
            variant = self.fan_in(artifacts, root / "variant-fan-in.json")
            self.assertEqual(variant["fan_in_state"], FanInState.COMPLETE_SUCCESS.value)
            observed = next(
                row for row in variant["observed_inputs"] if row["slot"] == lane
            )
            self.assertEqual(
                observed["raw_terminal_sha256"],
                hashlib.sha256(alternate_bytes).hexdigest(),
            )
            self.assertEqual(observed["terminal_sha256"], canonical_identity)
            self.assertNotEqual(
                observed["raw_terminal_sha256"], observed["terminal_sha256"]
            )
            self.assertNotEqual(
                baseline["observed_inputs_sha256"], variant["observed_inputs_sha256"]
            )
            baseline_candidate = build_review_candidate(
                baseline, lambda value: {"sha256": canonical_sha256(value)}
            )
            variant_candidate = build_review_candidate(
                variant, lambda value: {"sha256": canonical_sha256(value)}
            )
            self.assertNotEqual(
                baseline_candidate["sha256"], variant_candidate["sha256"]
            )

    def test_v5_record_passes_fan_in_and_shared_downstream_consumer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = root / "artifacts"
            write_success_artifact_set(artifacts)
            receipt = self.fan_in(artifacts, root / "fan-in.json")
            self.assertEqual(receipt["fan_in_state"], FanInState.COMPLETE_SUCCESS.value)
            for lane in EXPECTED_LANES:
                artifact = artifacts / f"lane-evidence-{lane}-{RUN_ID}-{RUN_ATTEMPT}"
                terminal = validate_success_artifact_against_fan_in(
                    artifact, receipt, expected_lane=lane
                )
                self.assertEqual(terminal["lane"], lane)
                inherited = json.loads(
                    artifact.joinpath(
                        "geom", "trekdex-lane-receipt.json"
                    ).read_text()
                )
                self.assertEqual(inherited["schema"], INHERITED_LANE_SCHEMA)

            first_lane = EXPECTED_LANES[0]
            first_artifact = artifacts / (
                f"lane-evidence-{first_lane}-{RUN_ID}-{RUN_ATTEMPT}"
            )
            inherited_path = first_artifact / "geom" / "trekdex-lane-receipt.json"
            stale = json.loads(inherited_path.read_text())
            stale["schema"] = "trekdex-state-geom-lane-v2"
            stale["receipt_sha256"] = canonical_sha256(
                stale, omit={"receipt_sha256"}
            )
            inherited_path.write_text(
                json.dumps(stale, sort_keys=True, separators=(",", ":")) + "\n"
            )
            with self.assertRaisesRegex(ValueError, "inherited lane receipt"):
                validate_success_artifact_against_fan_in(
                    first_artifact, receipt, expected_lane=first_lane
                )

    def test_r35_m_002_rehashed_wrong_display_artifact_blocks_fan_in(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = root / "artifacts"
            write_success_artifact_set(artifacts)
            lane = EXPECTED_LANES[0]
            artifact = artifacts / f"lane-evidence-{lane}-{RUN_ID}-{RUN_ATTEMPT}"
            wrong = "Coherently Wrong Display State"

            expected_path = artifact / "expected-state-areas.json"
            expected = json.loads(expected_path.read_text())
            expected["state"] = wrong
            expected_path.write_text(
                json.dumps(expected, sort_keys=True, separators=(",", ":")) + "\n"
            )

            geom_path = next(
                path for path in sorted((artifact / "geom").iterdir())
                if path.name != "trekdex-lane-receipt.json"
            )
            geom_document = json.loads(geom_path.read_text())
            geom_document["state"] = wrong
            geom_path.write_text(
                json.dumps(geom_document, sort_keys=True, separators=(",", ":")) + "\n"
            )
            file_row = {
                "name": geom_path.name,
                "bytes": len(geom_path.read_bytes()),
                "sha256": hashlib.sha256(geom_path.read_bytes()).hexdigest(),
            }

            inherited_path = artifact / "geom" / "trekdex-lane-receipt.json"
            inherited = json.loads(inherited_path.read_text())
            inherited["display_state"] = wrong
            inherited["files"] = [file_row]
            inherited["receipt_sha256"] = canonical_sha256(
                inherited, omit={"receipt_sha256"}
            )
            inherited_path.write_text(
                json.dumps(inherited, sort_keys=True, separators=(",", ":")) + "\n"
            )

            terminal_path = artifact / "lane-evidence" / "lane-terminal.json"
            terminal = json.loads(terminal_path.read_text())
            terminal["display_state"] = wrong
            terminal["files"] = [file_row]
            terminal["inherited_receipt_sha256"] = hashlib.sha256(
                inherited_path.read_bytes()
            ).hexdigest()
            payload_paths = [
                expected_path,
                artifact / "input-manifest.json",
                artifact / "publisher.log",
                *sorted((artifact / "geom").iterdir()),
            ]
            framed = bytearray()
            for path in sorted(
                payload_paths, key=lambda item: item.relative_to(artifact).as_posix()
            ):
                relative = path.relative_to(artifact).as_posix().encode()
                payload = path.read_bytes()
                framed.extend(len(relative).to_bytes(4, "big"))
                framed.extend(relative)
                framed.extend(len(payload).to_bytes(8, "big"))
                framed.extend(payload)
            terminal["artifact_payload_sha256"] = hashlib.sha256(framed).hexdigest()
            terminal["receipt_sha256"] = canonical_sha256(
                terminal, omit={"receipt_sha256"}
            )
            terminal_path.write_text(
                json.dumps(terminal, sort_keys=True, separators=(",", ":")) + "\n"
            )

            receipt = self.fan_in(artifacts, root / "fan-in.json")
            self.assertEqual(receipt["fan_in_state"], FanInState.NON_PROMOTABLE.value)
            self.assertFalse(receipt["review_candidate_allowed"])
            observed = next(
                row for row in receipt["observed_inputs"] if row["slot"] == lane
            )
            lane_terminal = next(
                row for row in receipt["lanes"] if row["lane"] == lane
            )
            self.assertEqual(
                observed["terminal_sha256"], canonical_sha256(lane_terminal)
            )
            self.assertNotEqual(
                observed["raw_terminal_sha256"], observed["terminal_sha256"]
            )
            called: list[dict[str, Any]] = []
            with self.assertRaisesRegex(RuntimeError, "non-promotable"):
                build_review_candidate(
                    receipt, lambda value: called.append(value) or {}
                )
            self.assertEqual(called, [])

    def test_stale_attempt_directory_and_cyclic_slot_swap_block_fan_in(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = root / "artifacts"
            write_success_artifact_set(artifacts)
            lane = EXPECTED_LANES[0]
            current = artifacts / f"lane-evidence-{lane}-{RUN_ID}-{RUN_ATTEMPT}"
            current.rename(artifacts / f"lane-evidence-{lane}-{RUN_ID}-{RUN_ATTEMPT - 1}")
            receipt = self.fan_in(artifacts, root / "stale.json")
            self.assertEqual(receipt["fan_in_state"], FanInState.NON_PROMOTABLE.value)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = root / "artifacts"
            write_success_artifact_set(artifacts)
            first, second = EXPECTED_LANES[:2]
            one = artifacts / f"lane-evidence-{first}-{RUN_ID}-{RUN_ATTEMPT}" / "lane-evidence" / "lane-terminal.json"
            two = artifacts / f"lane-evidence-{second}-{RUN_ID}-{RUN_ATTEMPT}" / "lane-evidence" / "lane-terminal.json"
            one_bytes, two_bytes = one.read_bytes(), two.read_bytes()
            one.write_bytes(two_bytes)
            two.write_bytes(one_bytes)
            receipt = self.fan_in(artifacts, root / "swapped.json")
            self.assertEqual(receipt["fan_in_state"], FanInState.NON_PROMOTABLE.value)
            self.assertFalse(receipt["review_candidate_allowed"])


class TestOfflineCLI(unittest.TestCase):
    def test_record_lane_cli_emits_valid_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lane = EXPECTED_LANES[0]
            area_id = area_id_for_lane(lane)
            source_index = root / "source-index.json"
            source_index.write_text(
                json.dumps(TEST_SOURCE_INDEX, sort_keys=True, separators=(",", ":")) + "\n"
            )
            approved_manifest = root / "approved-candidate-manifest.json"
            approved_manifest.write_bytes(APPROVED_CANDIDATE_MANIFEST)
            candidate_config = root / "candidate-config.json"
            candidate_config.write_bytes(CANDIDATE_CONFIG)
            expected = root / "expected.json"
            expected.write_text(json.dumps({
                "schema": "trekdex-pinned-state-area-set-v1",
                "state": display_state_for_lane(lane),
                "area_count": 1,
                "area_ids": [area_id],
                "area_ids_sha256": hashlib.sha256((area_id + "\n").encode()).hexdigest(),
            }, sort_keys=True, separators=(",", ":")) + "\n")
            geom = root / "geom"
            geom.mkdir()
            geom_path = geom / f"{area_id}.json"
            geom_path.write_bytes(geom_body(lane))
            output = root / "lane-terminal.json"
            input_manifest = root / "input-manifest.json"
            publisher_log = root / "publisher.log"
            inherited = geom / "trekdex-lane-receipt.json"
            input_manifest.write_text(json.dumps({
                "schema": "trekdex-trailforge-input-v2",
                "lane": lane,
                "source_sha": SOURCE,
                "run_id": RUN_ID,
                "run_attempt": RUN_ATTEMPT,
                "approved_input_set_sha256": APPROVED_INPUT_SET,
                "approved_input_population_sha256": APPROVED_INPUT_POPULATION,
                "approved_geometry_population_sha256": APPROVED_GEOMETRY_POPULATION,
                "url": f"https://example.invalid/{lane}.osm.pbf",
                "bytes": 1,
                "sha256": "c" * 64,
            }, sort_keys=True, separators=(",", ":")) + "\n")
            publisher_log.write_text("publisher succeeded\n")
            file_row = {
                "name": geom_path.name,
                "bytes": len(geom_path.read_bytes()),
                "sha256": hashlib.sha256(geom_path.read_bytes()).hexdigest(),
            }
            inherited_document = {
                "schema": INHERITED_LANE_SCHEMA,
                "lane": lane,
                "display_state": display_state_for_lane(lane),
                "source_sha": SOURCE,
                "source_tree": TREE,
                "workflow_sha256": WORKFLOW,
                "candidate_config_sha256": CONFIG,
                "run_id": RUN_ID,
                "run_attempt": RUN_ATTEMPT,
                "toolchain_manifest_sha256": TOOLCHAIN,
                "input_manifest_sha256": hashlib.sha256(
                    input_manifest.read_bytes()
                ).hexdigest(),
                "approved_input_set_sha256": APPROVED_INPUT_SET,
                "approved_input_population_sha256": APPROVED_INPUT_POPULATION,
                "approved_geometry_population_sha256": APPROVED_GEOMETRY_POPULATION,
                "source_index_sha256": TEST_AUTHORITY["source_index_sha256"],
                "authority_sha256": TEST_AUTHORITY["authority_sha256"],
                "expected_area_count": 1,
                "expected_area_ids_sha256": hashlib.sha256(
                    (area_id + "\n").encode()
                ).hexdigest(),
                "files": [file_row],
            }
            inherited_document["receipt_sha256"] = canonical_sha256(inherited_document)
            inherited.write_text(
                json.dumps(inherited_document, sort_keys=True, separators=(",", ":")) + "\n"
            )
            completed = subprocess.run(
                [
                    sys.executable, str(ROOT / "scripts/trailforge_candidate_contract.py"),
                    "record-lane", "--lane", lane, "--outcome", "SUCCESS",
                    "--source-sha", SOURCE, "--source-tree", TREE,
                    "--workflow-sha256", WORKFLOW,
                    "--root-approval-ref", APPROVAL,
                    "--run-id", RUN_ID, "--run-attempt", str(RUN_ATTEMPT),
                    "--candidate-config", str(candidate_config),
                    "--approved-candidate-manifest", str(approved_manifest),
                    "--matrix-job-ref", hashlib.sha256(
                        f"{RUN_ID}:{RUN_ATTEMPT}:{lane}".encode()
                    ).hexdigest(),
                    "--retry-lineage-ref", RETRY_LINEAGE,
                    "--input-manifest", str(input_manifest),
                    "--publisher-log", str(publisher_log),
                    "--inherited-receipt", str(inherited),
                    "--expected", str(expected),
                    "--source-index", str(source_index),
                    "--geom-dir", str(geom),
                    "--output", str(output),
                ],
                cwd=ROOT,
                env={
                **os.environ,
                **root_approval_env(),
                "PYTHONDONTWRITEBYTECODE": "1",
            },
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            receipt = json.loads(output.read_text())
            validate_terminal(
                receipt, expected_lane=lane, source_sha=SOURCE,
                source_tree=TREE, workflow_sha256=WORKFLOW,
                root_approval_ref=APPROVAL,
            )

    def test_fan_in_cli_validates_exact_downloaded_artifact_set(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = root / "artifacts"
            write_success_artifact_set(artifacts)
            output = root / "candidate" / "fan-in-terminal.json"
            github_output = root / "github-output"
            completed = subprocess.run(
                [
                    sys.executable, str(ROOT / "scripts/trailforge_candidate_contract.py"),
                    "fan-in", "--artifacts-root", str(artifacts),
                    "--source-index", str(root / "source-index.json"),
                    "--source-sha", SOURCE, "--source-tree", TREE,
                    "--workflow-sha256", WORKFLOW,
                    "--root-approval-ref", APPROVAL,
                    "--run-id", RUN_ID, "--run-attempt", str(RUN_ATTEMPT),
                    "--candidate-config", str(root / "candidate-config.json"),
                    "--approved-candidate-manifest", str(root / "approved-candidate-manifest.json"),
                    "--retry-lineage-ref", RETRY_LINEAGE,
                    "--workflow-outcome", "SUCCESS", "--output", str(output),
                    "--github-output", str(github_output),
                ],
                cwd=ROOT,
                env={
                **os.environ,
                **root_approval_env(),
                "PYTHONDONTWRITEBYTECODE": "1",
            },
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            receipt = json.loads(output.read_text())
            self.assertEqual(receipt["fan_in_state"], FanInState.COMPLETE_SUCCESS.value)
            self.assertTrue(receipt["review_candidate_allowed"])
            self.assertEqual(github_output.read_text(), "review_candidate_allowed=true\n")


    def test_fan_in_cli_marks_extra_success_payload_corrupt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = root / "artifacts"
            write_success_artifact_set(artifacts)
            artifacts.joinpath(
                f"lane-evidence-{EXPECTED_LANES[0]}-{RUN_ID}-{RUN_ATTEMPT}", "unexpected.json"
            ).write_text('{"trail_count":1}\n')
            output = root / "candidate" / "fan-in-terminal.json"
            completed = subprocess.run(
                [
                    sys.executable, str(ROOT / "scripts/trailforge_candidate_contract.py"),
                    "fan-in", "--artifacts-root", str(artifacts),
                    "--source-index", str(root / "source-index.json"),
                    "--source-sha", SOURCE, "--source-tree", TREE,
                    "--workflow-sha256", WORKFLOW,
                    "--root-approval-ref", APPROVAL,
                    "--run-id", RUN_ID, "--run-attempt", str(RUN_ATTEMPT),
                    "--candidate-config", str(root / "candidate-config.json"),
                    "--approved-candidate-manifest", str(root / "approved-candidate-manifest.json"),
                    "--retry-lineage-ref", RETRY_LINEAGE,
                    "--workflow-outcome", "SUCCESS", "--output", str(output),
                ],
                cwd=ROOT,
                env={
                **os.environ,
                **root_approval_env(),
                "PYTHONDONTWRITEBYTECODE": "1",
            },
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            receipt = json.loads(output.read_text())
            self.assertEqual(receipt["fan_in_state"], FanInState.NON_PROMOTABLE.value)
            self.assertFalse(receipt["review_candidate_allowed"])
            self.assertEqual(receipt["lanes"][0]["outcome"], LaneOutcome.CORRUPT.value)


class TestWorkflowWiring(unittest.TestCase):
    def test_static_workflow_contract(self) -> None:
        path = ROOT / ".github/workflows/trailforge-publish-us.yml"
        text = path.read_text()
        self.assertEqual(validate_workflow_contract(text), [])
        contract_text = (ROOT / "scripts/trailforge_candidate_contract.py").read_text()
        self.assertIn("hmac.compare_digest", contract_text)
        self.assertLess(
            text.index("Authenticate root approval before any candidate work"),
            text.index("Install osmium, pyosmium, shapely, and Pillow"),
        )
        required = (
            "continue-on-error: true",
            "if: ${{ always() }}",
            "lane-evidence-${{ matrix.state }}",
            "trailforge_candidate_contract.py",
            "fan-in-terminal.json",
            "review_candidate_allowed",
        )
        for marker in required:
            with self.subTest(marker=marker):
                self.assertIn(marker, text)
        for forbidden in ("git commit", "git push", "aws ", "rclone ", "wrangler "):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text)
        stale_consumer = 'lane_receipt["schema"] != "trekdex-state-geom-lane-v2"'
        self.assertNotIn(stale_consumer, text)
        self.assertTrue(
            any(
                "stale v2 schema" in error
                for error in validate_workflow_contract(text + "\n" + stale_consumer)
            )
        )

    def test_each_workflow_seam_mutation_is_detected(self) -> None:
        text = (ROOT / ".github/workflows/trailforge-publish-us.yml").read_text()
        markers = (
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
        )
        for marker in markers:
            hostile = text.replace(marker, "MUTATED")
            with self.subTest(marker=marker):
                self.assertTrue(validate_workflow_contract(hostile))

    def test_module_and_test_compile_offline(self) -> None:
        for path in (
            ROOT / "scripts/trailforge_candidate_contract.py",
            ROOT / "scripts/test_trailforge_candidate_contract.py",
        ):
            completed = subprocess.run([sys.executable, "-m", "py_compile", str(path)], capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)


    def test_single_gate_and_matrix_mutations_are_detected(self) -> None:
        text = (ROOT / ".github/workflows/trailforge-publish-us.yml").read_text()
        success_gate = "if: ${{ steps.fan_in.outputs.review_candidate_allowed == 'true' }}"
        self.assertEqual(text.count(success_gate), 6)
        self.assertTrue(validate_workflow_contract(text.replace(success_gate, "if: ${{ true }}", 1)))
        self.assertTrue(validate_workflow_contract(text.replace("          - alabama\n", "          - extra-lane\n", 1)))


if __name__ == "__main__":
    print("Trailforge campaign: adverse_positions=357 receipt_mutations=10 fan_in_semantic_mutations=5 aggregate_outcomes=7 orchestration_synthesis=2 partial_positions=51 zero_effect_outcomes=7 raw_permutations=154 metamorphic_schedules=512 adjacent_swaps=50 workflow_mutations=9 replay=100 cli_smokes=3")
    unittest.main(verbosity=2)
