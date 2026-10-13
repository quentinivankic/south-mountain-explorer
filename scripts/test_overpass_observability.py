#!/usr/bin/env python3
"""Pure offline tests for Overpass response and receipt observability."""
from __future__ import annotations

import errno
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import ssl
import stat
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _overpass_observability as obs  # noqa: E402


_QUERY_SHA = hashlib.sha256(b"query").hexdigest()


class _FakeClock:
    def __init__(self):
        self.wall = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
        self.monotonic = 1_000_000_000

    def utc_now(self):
        return self.wall

    def monotonic_ns(self):
        return self.monotonic

    def advance_ms(self, milliseconds):
        self.wall += timedelta(milliseconds=milliseconds)
        self.monotonic += milliseconds * 1_000_000

    def sleep(self, seconds):
        self.advance_ms(seconds * 1000)


def _receipt(root: Path, clock: _FakeClock | None = None) -> obs.OverpassReceipt:
    clock = clock or _FakeClock()
    return obs.OverpassReceipt.initialize(
        root / "receipt.json",
        ["DK"],
        utc_now=clock.utc_now,
        monotonic_ns=clock.monotonic_ns,
    )


def _begin(
    receipt: obs.OverpassReceipt,
    *,
    role: str = "candidate",
    outer: int = 1,
    ordinal: int = 1,
    host: str = "overpass-api.de",
) -> str:
    attempt_id = receipt.begin_attempt(
        role,
        "DK",
        outer,
        ordinal,
        host,
        _QUERY_SHA,
    )
    receipt.start_attempt_timing(attempt_id)
    return attempt_id


class ResponseClassification(unittest.TestCase):
    def test_valid_elements_are_counted_without_reordering(self):
        raw = (
            b'{"elements":[{"type":"relation","id":1},'
            b'{"type":"way","id":2},{"type":"node","id":3}]}'
        )
        parsed = obs.classify_response(raw)
        self.assertEqual(parsed.json_shape_verdict, "elements-list")
        self.assertEqual(
            (parsed.relation_count, parsed.way_count,
             parsed.other_element_count),
            (1, 1, 1),
        )
        self.assertEqual(
            [element["id"] for element in parsed.document["elements"]],
            [1, 2, 3],
        )

    def test_malformed_and_duplicate_json_are_distinct(self):
        malformed = obs.classify_response(b"<html>bad</html>")
        duplicate = obs.classify_response(
            b'{"outer":{"value":1,"value":2},"elements":[]}'
        )
        self.assertEqual(malformed.json_shape_verdict, "malformed-json")
        self.assertEqual(duplicate.json_shape_verdict, "duplicate-json-key")
        for parsed in (malformed, duplicate):
            self.assertEqual(
                (parsed.relation_count, parsed.way_count,
                 parsed.other_element_count),
                (None, None, None),
            )

    def test_each_non_object_top_level_is_rejected(self):
        for raw in (b"[]", b"null", b'"value"', b"1", b"true"):
            with self.subTest(raw=raw):
                self.assertEqual(
                    obs.classify_response(raw).json_shape_verdict,
                    "top-level-not-object",
                )

    def test_missing_and_non_list_elements_are_not_empty_success(self):
        self.assertEqual(
            obs.classify_response(b"{}").json_shape_verdict,
            "elements-missing",
        )
        for value in (None, {}, "", 1, True):
            with self.subTest(value=value):
                raw = json.dumps({"elements": value}).encode()
                self.assertEqual(
                    obs.classify_response(raw).json_shape_verdict,
                    "elements-not-list",
                )

    def test_scalar_element_rejects_the_entire_shape(self):
        parsed = obs.classify_response(
            b'{"elements":[{"type":"relation"},7]}'
        )
        self.assertEqual(parsed.json_shape_verdict,
                         "elements-entry-not-object")
        self.assertIsNone(parsed.relation_count)

    def test_nonstandard_json_constants_are_malformed(self):
        for constant in (b"NaN", b"Infinity", b"-Infinity"):
            with self.subTest(constant=constant):
                parsed = obs.classify_response(
                    b'{"elements":[],"value":' + constant + b"}")
                self.assertEqual(parsed.json_shape_verdict, "malformed-json")

    def test_empty_and_nodes_only_lists_have_measured_zero_primary_counts(self):
        for raw, other in (
            (b'{"elements":[]}', 0),
            (b'{"elements":[{"type":"node"}]}', 1),
        ):
            with self.subTest(raw=raw):
                parsed = obs.classify_response(raw)
                self.assertEqual(
                    (parsed.relation_count, parsed.way_count,
                     parsed.other_element_count),
                    (0, 0, other),
                )


class EndpointAndExceptionSafety(unittest.TestCase):
    def test_endpoint_host_returns_only_the_configured_hostname(self):
        self.assertEqual(
            obs.endpoint_host("https://overpass-api.de/api/interpreter"),
            "overpass-api.de",
        )

    def test_endpoint_rejects_unsafe_or_ambiguous_urls(self):
        endpoints = (
            "http://overpass-api.de/api/interpreter",
            "https://user@overpass-api.de/api/interpreter",
            "https://overpass-api.de/api/interpreter?q=secret",
            "https://overpass-api.de/api/interpreter#fragment",
            "https://overpass-api.de:443/api/interpreter",
            "not-a-url",
        )
        for endpoint in endpoints:
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(obs.OverpassReceiptError):
                    obs.endpoint_host(endpoint)

    def test_exception_taxonomy_is_typed_and_message_free(self):
        cases = (
            (TimeoutError("token=SECRET"), "timeout", None),
            (urllib.error.URLError(socket.gaierror(
                socket.EAI_NONAME, "signed=query")), "dns",
             socket.EAI_NONAME),
            (urllib.error.URLError(ssl.SSLError("local/path")), "tls", None),
            (ConnectionResetError(errno.ECONNRESET, "credential"),
             "connection", errno.ECONNRESET),
            (OSError(errno.EIO, "raw-body"), "os", errno.EIO),
        )
        for error, category, numeric_errno in cases:
            with self.subTest(error_type=type(error).__name__):
                actual_category, error_type, actual_errno = \
                    obs.safe_exception(error)
                self.assertEqual(actual_category, category)
                self.assertIn(type(error).__name__, error_type)
                self.assertEqual(actual_errno, numeric_errno)
                serialized = json.dumps(
                    [actual_category, error_type, actual_errno])
                for forbidden in (
                        "SECRET", "signed=query", "local/path",
                        "credential", "raw-body"):
                    self.assertNotIn(forbidden, serialized)


class ReceiptCheckpoints(unittest.TestCase):
    def test_initialization_is_canonical_private_and_in_progress(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            receipt = _receipt(root)
            raw = receipt.path.read_bytes()
            self.assertEqual(
                raw,
                (json.dumps(receipt.document, ensure_ascii=False,
                            sort_keys=True, indent=2) + "\n").encode(),
            )
            self.assertEqual(
                stat.S_IMODE(receipt.path.stat().st_mode), 0o600)
            self.assertEqual(
                receipt.document["completion"],
                {"state": "in-progress", "reason": "running"},
            )

    def test_candidate_empty_and_bbox_empty_have_distinct_outcomes(self):
        with TemporaryDirectory() as temporary:
            receipt = _receipt(Path(temporary))
            raw = b'{"elements":[]}'
            parsed = obs.classify_response(raw)
            candidate_id = _begin(receipt, outer=2)
            candidate_outcome = receipt.finish_response(
                candidate_id, http_status=200, raw=raw, parsed=parsed)
            receipt.record_candidate_local_evaluation(
                "DK", 0, {key: 0 for key in obs.REJECTION_KEYS})
            bbox_id = _begin(
                receipt, role="bbox", ordinal=1)
            bbox_outcome = receipt.finish_response(
                bbox_id, http_status=200, raw=raw, parsed=parsed)
            receipt.record_bbox_result("DK", "no-usable-bbox")
            receipt.finalize_candidate_output(
                "DK", accepted_count=0, manual_exclude=0,
                duplicate_slug=0)
            receipt.finish("complete", "candidate-processing-complete")

            self.assertEqual(candidate_outcome,
                             "ambiguous-empty-primary-response")
            self.assertEqual(bbox_outcome, "payload-accepted")
            document = json.loads(receipt.path.read_text())
            candidate, bbox = document["attempts"]
            self.assertEqual(candidate["accepted_count"], 0)
            self.assertTrue(all(
                count == 0
                for count in candidate["rejection_breakdown"].values()))
            self.assertIsNone(bbox["accepted_count"])
            self.assertEqual(bbox["parent_candidate_outer_attempt"], 2)
            self.assertEqual(document["regions"][0]["bbox_result"],
                             "no-usable-bbox")

    def test_every_terminal_outcome_closes_exact_timing(self):
        cases = (
            "transport-error", "http-error", "malformed-json",
            "invalid-json-shape", "ambiguous-empty-primary-response",
            "payload-accepted",
        )
        for expected_outcome in cases:
            with self.subTest(outcome=expected_outcome):
                with TemporaryDirectory() as temporary:
                    clock = _FakeClock()
                    receipt = _receipt(Path(temporary), clock)
                    attempt_id = _begin(receipt)
                    clock.advance_ms(125)
                    if expected_outcome == "transport-error":
                        receipt.finish_transport_error(
                            attempt_id, TimeoutError("private message"))
                    elif expected_outcome == "http-error":
                        error = urllib.error.HTTPError(
                            "", 500, "", {}, None)
                        receipt.finish_http_error(
                            attempt_id, error, b"server bytes")
                    else:
                        raw_by_outcome = {
                            "malformed-json": b"<html>",
                            "invalid-json-shape": b"{}",
                            "ambiguous-empty-primary-response":
                                b'{"elements":[]}',
                            "payload-accepted":
                                b'{"elements":[{"type":"relation"}]}',
                        }
                        raw = raw_by_outcome[expected_outcome]
                        actual = receipt.finish_response(
                            attempt_id,
                            http_status=200,
                            raw=raw,
                            parsed=obs.classify_response(raw),
                        )
                        self.assertEqual(actual, expected_outcome)
                    attempt = receipt.document["attempts"][0]
                    self.assertEqual(
                        attempt["started_at"], "2026-10-12T12:00:00.000Z")
                    self.assertEqual(
                        attempt["ended_at"], "2026-10-12T12:00:00.125Z")
                    self.assertEqual(attempt["duration_ms"], 125)
                    self.assertEqual(attempt["outcome"], expected_outcome)

    def test_timing_closes_before_checkpoint_and_later_invariant_failures(self):
        with TemporaryDirectory() as temporary:
            clock = _FakeClock()
            receipt = _receipt(Path(temporary), clock)
            attempt_id = _begin(receipt)
            clock.advance_ms(25)
            with patch.object(
                    receipt, "_checkpoint",
                    side_effect=obs.ReceiptWriteError("checkpoint failed")):
                with self.assertRaises(obs.ReceiptWriteError):
                    receipt.finish_transport_error(
                        attempt_id, TimeoutError("private message"))
            attempt = receipt.document["attempts"][0]
            self.assertEqual(attempt["ended_at"],
                             "2026-10-12T12:00:00.025Z")
            self.assertEqual(attempt["duration_ms"], 25)

        with TemporaryDirectory() as temporary:
            clock = _FakeClock()
            receipt = _receipt(Path(temporary), clock)
            raw = b'{"elements":[{"type":"relation"}]}'
            attempt_id = _begin(receipt)
            clock.advance_ms(40)
            receipt.finish_response(
                attempt_id, http_status=200, raw=raw,
                parsed=obs.classify_response(raw))
            with self.assertRaisesRegex(
                    obs.OverpassReceiptError, "count mismatch"):
                receipt.record_candidate_local_evaluation(
                    "DK", 0, {key: 0 for key in obs.REJECTION_KEYS})
            attempt = receipt.document["attempts"][0]
            self.assertEqual(attempt["ended_at"],
                             "2026-10-12T12:00:00.040Z")
            self.assertEqual(attempt["duration_ms"], 40)

    def test_http_error_hashes_body_without_persisting_it(self):
        with TemporaryDirectory() as temporary:
            receipt = _receipt(Path(temporary))
            attempt_id = _begin(receipt)
            body = b"token=SECRET and raw response"
            error = urllib.error.HTTPError(
                "https://user:password@example.invalid/private?token=x",
                500,
                "reason phrase",
                {"Authorization": "secret"},
                io.BytesIO(body),
            )
            receipt.finish_http_error(attempt_id, error, body)
            raw = receipt.path.read_text()
            attempt = receipt.document["attempts"][0]
            self.assertEqual(attempt["outcome"], "http-error")
            self.assertEqual(attempt["http_status"], 500)
            self.assertEqual(attempt["response_byte_count"], len(body))
            self.assertEqual(attempt["response_sha256"],
                             hashlib.sha256(body).hexdigest())
            for forbidden in (
                    "SECRET", "password", "Authorization",
                    "reason phrase", "example.invalid", "private"):
                self.assertNotIn(forbidden, raw)

    def test_transport_receipt_never_stringifies_exception(self):
        with TemporaryDirectory() as temporary:
            receipt = _receipt(Path(temporary))
            attempt_id = _begin(receipt)
            receipt.finish_transport_error(
                attempt_id,
                TimeoutError("token=SECRET /Users/person/private"),
            )
            raw = receipt.path.read_text()
            self.assertNotIn("SECRET", raw)
            self.assertNotIn("/Users/person", raw)
            self.assertEqual(
                receipt.document["attempts"][0]["exception_category"],
                "timeout",
            )

    def test_http_body_read_failure_is_typed_without_message_or_body(self):
        with TemporaryDirectory() as temporary:
            receipt = _receipt(Path(temporary))
            attempt_id = _begin(receipt)
            error = urllib.error.HTTPError("", 500, "", {}, None)
            receipt.finish_http_error(
                attempt_id,
                error,
                None,
                body_read_error=TimeoutError(
                    "token=SECRET /Users/person/private"),
            )
            raw = receipt.path.read_text()
            attempt = receipt.document["attempts"][0]
            self.assertEqual(attempt["outcome"], "http-error")
            self.assertEqual(attempt["exception_category"], "timeout")
            self.assertIn("TimeoutError", attempt["exception_type"])
            self.assertIsNone(attempt["response_byte_count"])
            self.assertIsNone(attempt["response_sha256"])
            self.assertNotIn("SECRET", raw)
            self.assertNotIn("/Users/person", raw)

    def test_existing_target_and_symlink_parent_fail_before_checkpoint(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "receipt.json"
            target.write_text("old")
            with self.assertRaisesRegex(
                    obs.OverpassReceiptError, "already exists"):
                obs.OverpassReceipt.initialize(target, ["DK"])
            real = root / "real"
            real.mkdir()
            linked = root / "linked"
            linked.symlink_to(real, target_is_directory=True)
            with self.assertRaisesRegex(
                    obs.OverpassReceiptError, "parent is unsafe"):
                obs.OverpassReceipt.initialize(
                    linked / "receipt.json", ["DK"])
            self.assertFalse((real / "receipt.json").exists())

    def test_unsafe_region_or_host_metadata_is_never_checkpointed(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(
                    obs.OverpassReceiptError, "region codes"):
                obs.OverpassReceipt.initialize(
                    root / "bad.json", ["../../private"])
            self.assertFalse((root / "bad.json").exists())

            receipt = _receipt(root)
            before = receipt.path.read_bytes()
            with self.assertRaisesRegex(
                    obs.OverpassReceiptError, "attempt identity"):
                _begin(
                    receipt,
                    host="https://user:token@example.invalid/private",
                )
            self.assertEqual(receipt.path.read_bytes(), before)

    def test_checkpoint_failure_propagates_and_leaves_prior_json(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            receipt = _receipt(root)
            before = receipt.path.read_bytes()
            with patch.object(obs.os, "replace", side_effect=OSError("path")):
                with self.assertRaises(obs.ReceiptWriteError):
                    _begin(receipt)
            self.assertEqual(receipt.path.read_bytes(), before)
            self.assertEqual(
                list(root.glob(f".{receipt.path.name}.*.tmp")), [])

    def test_same_events_produce_identical_bytes_in_fresh_directories(self):
        def run(root: Path) -> bytes:
            receipt = _receipt(root)
            candidate_raw = b'{"elements":[{"type":"relation"}]}'
            candidate = obs.classify_response(candidate_raw)
            candidate_id = _begin(receipt)
            receipt.finish_response(
                candidate_id, http_status=200,
                raw=candidate_raw, parsed=candidate)
            receipt.record_candidate_local_evaluation(
                "DK", 1, {key: 0 for key in obs.REJECTION_KEYS})
            bbox_raw = b'{"elements":[]}'
            bbox_id = _begin(
                receipt, role="bbox")
            receipt.finish_response(
                bbox_id, http_status=200, raw=bbox_raw,
                parsed=obs.classify_response(bbox_raw))
            receipt.record_bbox_result("DK", "no-usable-bbox")
            receipt.finalize_candidate_output(
                "DK", accepted_count=1, manual_exclude=0,
                duplicate_slug=0)
            receipt.finish("complete", "candidate-processing-complete")
            return receipt.path.read_bytes()

        with TemporaryDirectory() as first, TemporaryDirectory() as second:
            self.assertEqual(run(Path(first)), run(Path(second)))

    def test_interrupted_attempt_retains_valid_incomplete_json(self):
        with TemporaryDirectory() as temporary:
            receipt = _receipt(Path(temporary))
            _begin(receipt)
            document = json.loads(receipt.path.read_text())
            self.assertEqual(document["completion"]["state"], "in-progress")
            attempt = document["attempts"][0]
            self.assertEqual(attempt["started_at"],
                             "2026-10-12T12:00:00.000Z")
            self.assertIsNone(attempt["ended_at"])
            self.assertIsNone(attempt["duration_ms"])
            self.assertIsNone(attempt["outcome"])

    def test_outcome_checkpoint_failure_retains_closed_timing_on_disk(self):
        with TemporaryDirectory() as temporary:
            clock = _FakeClock()
            receipt = _receipt(Path(temporary), clock)
            attempt_id = _begin(receipt)
            clock.advance_ms(75)
            original_checkpoint = receipt._checkpoint
            calls = 0

            def checkpoint_then_fail():
                nonlocal calls
                calls += 1
                if calls == 1:
                    original_checkpoint()
                    return
                raise obs.ReceiptWriteError("checkpoint failed")

            with patch.object(
                    receipt, "_checkpoint", side_effect=checkpoint_then_fail):
                with self.assertRaises(obs.ReceiptWriteError):
                    receipt.finish_transport_error(
                        attempt_id, TimeoutError("private message"))
            durable = json.loads(receipt.path.read_text())
            attempt = durable["attempts"][0]
            self.assertEqual(attempt["started_at"],
                             "2026-10-12T12:00:00.000Z")
            self.assertEqual(attempt["ended_at"],
                             "2026-10-12T12:00:00.075Z")
            self.assertEqual(attempt["duration_ms"], 75)
            self.assertIsNone(attempt["outcome"])

    def test_count_equation_rejects_missing_terminal_dispositions(self):
        with TemporaryDirectory() as temporary:
            receipt = _receipt(Path(temporary))
            raw = b'{"elements":[{"type":"relation"},{"type":"way"}]}'
            attempt_id = _begin(receipt)
            receipt.finish_response(
                attempt_id, http_status=200, raw=raw,
                parsed=obs.classify_response(raw))
            with self.assertRaisesRegex(
                    obs.OverpassReceiptError, "count mismatch"):
                receipt.record_candidate_local_evaluation(
                    "DK", 1, {key: 0 for key in obs.REJECTION_KEYS})


if __name__ == "__main__":
    unittest.main()
