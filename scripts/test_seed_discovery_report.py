#!/usr/bin/env python3
"""Offline tests for seed-areas.py's opt-in discovery report."""
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import io
import json
import socket
import sys
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

_SCRIPT = Path(__file__).resolve().parent / "seed-areas.py"
_SPEC = importlib.util.spec_from_file_location("seed_areas_discovery_report", _SCRIPT)
sa = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sa)
import _overpass_observability as obs  # noqa: E402

_VALIDATOR = (
    Path(__file__).resolve().parent.parent
    / "trailforge" / "tools" / "validate_publish_pilot.py"
)
_VALIDATOR_SPEC = importlib.util.spec_from_file_location(
    "validate_publish_pilot_for_seed_receipt", _VALIDATOR)
validator = importlib.util.module_from_spec(_VALIDATOR_SPEC)
_VALIDATOR_SPEC.loader.exec_module(validator)


def _run(argv, elements, *, excludes=""):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        report = root / "discovery.json"
        include_path = root / "seeds-include.txt"
        exclude_path = root / "seeds-exclude.txt"
        index_path = root / "index.json"
        cache_path = root / "cache.json"
        include_path.write_text("", encoding="utf-8")
        exclude_path.write_text(excludes, encoding="utf-8")
        expanded = [str(report) if value == "REPORT" else value for value in argv]
        with (
            patch.object(sa, "SEED_INCLUDE", include_path),
            patch.object(sa, "SEED_EXCLUDE", exclude_path),
            patch.object(sa, "INDEX_PATH", index_path),
            patch.object(sa, "CACHE_PATH", cache_path),
            patch.object(sa, "fetch_overpass", return_value={"elements": elements}),
            patch.object(
                sa,
                "fetch_region_bbox",
                return_value=(54.0, 7.0, 58.0, 13.0),
            ),
            patch.object(sys, "argv", ["seed-areas.py", *expanded]),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            sa.main()
        payload = json.loads(report.read_text()) if report.exists() else None
        return payload, stdout.getvalue(), stderr.getvalue(), index_path.exists(), cache_path.exists()


def _element(osm_type, osm_id, name, *, lat=56.2, lon=10.6):
    return {
        "type": osm_type,
        "id": osm_id,
        "tags": {"name": name, "protect_class": "2"},
        "center": {"lat": lat, "lon": lon},
    }


def test_relation_id_survives_filter_exclude_and_slug_dedup_path():
    elements = [
        _element("relation", 7046785, "Nationalpark Mols Bjerge"),
        _element("relation", 9999999, "Nationalpark Mols Bjerge"),
        _element("relation", 1234, "Excluded Park"),
    ]
    payload, stdout, stderr, index_exists, cache_exists = _run(
        ["--dry-run", "--discovery-report", "REPORT", "DK"],
        elements,
        excludes="eXcLuDeD pArK\n",
    )

    assert payload == {
        "schema_version": 1,
        "requested_region_codes": ["DK"],
        "candidate_count": 1,
        "candidates": [{
            "index_row": [
                "nationalpark-mols-bjerge-dk",
                "Nationalpark Mols Bjerge",
                "Denmark",
                56.2,
                10.6,
            ],
            "osm_relation_id": 7046785,
        }],
        "attribution": "© OpenStreetMap contributors",
    }
    assert "Nationalpark Mols Bjerge" in stdout
    assert "3 candidates, 3 passed quality filter" in stderr
    assert not index_exists
    assert not cache_exists


def test_zero_candidates_write_explicit_empty_report():
    payload, stdout, _, index_exists, cache_exists = _run(
        ["--dry-run", "--discovery-report", "REPORT", "DK"],
        [],
    )

    assert payload["candidate_count"] == 0
    assert payload["candidates"] == []
    assert stdout == ""
    assert not index_exists
    assert not cache_exists


def test_way_candidate_has_no_relation_id():
    payload, _, _, _, _ = _run(
        ["--dry-run", "--discovery-report", "REPORT", "DK"],
        [_element("way", 222, "Nationalpark Mols Bjerge")],
    )

    assert payload["candidate_count"] == 1
    assert payload["candidates"][0]["osm_relation_id"] is None


def test_discovery_report_requires_dry_run():
    with TemporaryDirectory() as tmp:
        report = Path(tmp) / "discovery.json"
        stderr = io.StringIO()
        with (
            patch.object(sys, "argv", [
                "seed-areas.py", "--discovery-report", str(report), "DK"
            ]),
            redirect_stderr(stderr),
        ):
            try:
                sa.main()
            except SystemExit as error:
                assert error.code == 2
            else:
                raise AssertionError("expected argparse to reject a write-mode report")
        assert "--discovery-report requires --dry-run" in stderr.getvalue()
        assert not report.exists()


def test_omitting_report_preserves_console_only_dry_run():
    payload, stdout, _, index_exists, cache_exists = _run(
        ["--dry-run", "DK"],
        [_element("relation", 7046785, "Nationalpark Mols Bjerge")],
    )

    assert payload is None
    assert stdout.startswith("  Nationalpark Mols Bjerge")
    assert not index_exists
    assert not cache_exists


class _FakeResponse:
    def __init__(self, raw, status=200):
        self.raw = raw
        self.status = status
        self.read_count = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        self.read_count += 1
        if self.read_count != 1:
            raise AssertionError("response body was read more than once")
        return self.raw

    def getcode(self):
        return self.status


def _response(value):
    raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
    return _FakeResponse(raw), raw


def _http_error(status, body):
    return urllib.error.HTTPError(
        "https://user:password@example.invalid/private?token=SECRET",
        status,
        "server reason SECRET",
        {"Authorization": "SECRET"},
        io.BytesIO(body),
    )


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


def _run_with_receipt(argv, side_effects, *, includes="", excludes=""):
    stdout = io.StringIO()
    stderr = io.StringIO()
    clock = _FakeClock()
    effects = iter(side_effects)

    def timed_open(*_args, **_kwargs):
        clock.advance_ms(125)
        effect = next(effects)
        if isinstance(effect, BaseException):
            raise effect
        return effect

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        report = root / "discovery.json"
        receipt = root / "overpass-receipt.json"
        include_path = root / "seeds-include.txt"
        exclude_path = root / "seeds-exclude.txt"
        index_path = root / "index.json"
        cache_path = root / "cache.json"
        include_path.write_text(includes, encoding="utf-8")
        exclude_path.write_text(excludes, encoding="utf-8")
        expanded = [
            str(report) if value == "REPORT" else
            str(receipt) if value == "RECEIPT" else value
            for value in argv
        ]
        with (
            patch.object(sa, "SEED_INCLUDE", include_path),
            patch.object(sa, "SEED_EXCLUDE", exclude_path),
            patch.object(sa, "INDEX_PATH", index_path),
            patch.object(sa, "CACHE_PATH", cache_path),
            patch.object(
                sa.urllib.request, "urlopen", side_effect=timed_open
            ) as opener,
            patch.object(sa.time, "sleep", side_effect=clock.sleep) as sleeper,
            patch.object(obs, "_utc_now", side_effect=clock.utc_now),
            patch.object(
                obs, "_monotonic_ns", side_effect=clock.monotonic_ns),
            patch.object(sys, "argv", ["seed-areas.py", *expanded]),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            exit_code = 0
            try:
                sa.main()
            except SystemExit as error:
                exit_code = error.code
        return {
            "exit": exit_code,
            "report": json.loads(report.read_text()) if report.exists() else None,
            "receipt": json.loads(receipt.read_text())
            if receipt.exists() else None,
            "receipt_raw": receipt.read_text() if receipt.exists() else None,
            "stdout": stdout.getvalue(),
            "stderr": stderr.getvalue(),
            "index_exists": index_path.exists(),
            "cache_exists": cache_path.exists(),
            "opener_calls": opener.call_count,
            "sleep_values": [call.args[0] for call in sleeper.call_args_list],
        }


def _bbox_response():
    return _response({"elements": [{
        "type": "relation",
        "bounds": {
            "minlat": 54.0, "minlon": 7.0,
            "maxlat": 58.0, "maxlon": 13.0,
        },
    }]})[0]


def test_run36_shape_records_every_endpoint_and_valid_empty_fails_closed():
    empty_response, empty_raw = _response({"elements": []})
    http_body = b"raw server body token=SECRET"
    result = _run_with_receipt(
        ["--dry-run", "--discovery-report", "REPORT",
         "--overpass-receipt", "RECEIPT", "DK"],
        [
            TimeoutError("signed=query&token=SECRET"),
            _http_error(500, http_body),
            empty_response,
            _response({"elements": []})[0],
        ],
    )

    assert result["exit"] == 0
    assert result["opener_calls"] == 4
    assert result["sleep_values"] == [3, 3, 60]
    assert result["report"]["candidate_count"] == 0
    assert result["report"]["candidates"] == []
    receipt = result["receipt"]
    assert [attempt["endpoint_host"] for attempt in receipt["attempts"]] == [
        "overpass-api.de", "overpass.kumi.systems",
        "overpass-api.de", "overpass-api.de",
    ]
    assert [attempt["outer_attempt"] for attempt in receipt["attempts"]] == [
        1, 1, 2, 1,
    ]
    assert [attempt["outcome"] for attempt in receipt["attempts"]] == [
        "transport-error", "http-error",
        "ambiguous-empty-primary-response", "payload-accepted",
    ]
    assert [attempt["started_at"] for attempt in receipt["attempts"]] == [
        "2026-10-12T12:00:00.000Z",
        "2026-10-12T12:00:03.125Z",
        "2026-10-12T12:01:06.250Z",
        "2026-10-12T12:01:06.375Z",
    ]
    assert [attempt["ended_at"] for attempt in receipt["attempts"]] == [
        "2026-10-12T12:00:00.125Z",
        "2026-10-12T12:00:03.250Z",
        "2026-10-12T12:01:06.375Z",
        "2026-10-12T12:01:06.500Z",
    ]
    assert [attempt["duration_ms"] for attempt in receipt["attempts"]] == [
        125, 125, 125, 125,
    ]
    assert receipt["attempts"][0]["exception_category"] == "timeout"
    assert receipt["attempts"][1]["http_status"] == 500
    assert receipt["attempts"][1]["response_byte_count"] == len(http_body)
    assert receipt["attempts"][1]["response_sha256"] == hashlib.sha256(
        http_body).hexdigest()
    assert receipt["attempts"][2]["response_byte_count"] == len(empty_raw)
    assert receipt["attempts"][2]["response_sha256"] == hashlib.sha256(
        empty_raw).hexdigest()
    assert receipt["regions"] == [{
        "region_code": "DK",
        "candidate_attempt_id": "candidate-DK-o0002-e0001",
        "candidate_result": "ambiguous-empty-primary-response",
        "bbox_result": "no-usable-bbox",
        "report_candidate_count": 0,
    }]
    assert receipt["completion"] == {
        "state": "complete", "reason": "candidate-processing-complete"}
    consumer_errors = []
    validator._validate_overpass_receipt(
        receipt, result["report"], consumer_errors)
    assert consumer_errors == [
        "Overpass receipt records ambiguous empty primary response"]
    assert not result["index_exists"]
    assert not result["cache_exists"]
    for forbidden in (
            "SECRET", "signed=query", "example.invalid", "password",
            "Authorization", "server reason", "raw server body"):
        assert forbidden not in result["receipt_raw"]
        assert forbidden not in result["stderr"]


def test_missing_elements_uses_only_existing_failover_then_accepts_payload():
    invalid, invalid_raw = _response({})
    candidate, _ = _response({"elements": [
        _element("relation", 7046785, "Nationalpark Mols Bjerge"),
    ]})
    result = _run_with_receipt(
        ["--dry-run", "--discovery-report", "REPORT",
         "--overpass-receipt", "RECEIPT", "DK"],
        [invalid, candidate, _bbox_response()],
    )

    assert result["exit"] == 0
    assert result["opener_calls"] == 3
    assert result["sleep_values"] == [3]
    attempts = result["receipt"]["attempts"]
    assert attempts[0]["outcome"] == "invalid-json-shape"
    assert attempts[0]["json_shape_verdict"] == "elements-missing"
    assert attempts[0]["response_byte_count"] == len(invalid_raw)
    assert attempts[1]["outcome"] == "payload-accepted"
    assert attempts[1]["accepted_count"] == 1
    assert result["report"]["candidate_count"] == 1
    assert result["report"]["candidates"][0]["osm_relation_id"] == 7046785
    consumer_errors = []
    validator._validate_overpass_receipt(
        result["receipt"], result["report"], consumer_errors)
    assert consumer_errors == []


def test_returned_non_2xx_is_http_error_and_uses_existing_failover():
    non_2xx = _FakeResponse(b'{"elements":[{"type":"relation"}]}', 503)
    candidate, _ = _response({"elements": [
        _element("relation", 7046785, "Nationalpark Mols Bjerge"),
    ]})
    result = _run_with_receipt(
        ["--dry-run", "--discovery-report", "REPORT",
         "--overpass-receipt", "RECEIPT", "DK"],
        [non_2xx, candidate, _bbox_response()],
    )

    assert result["exit"] == 0
    assert result["opener_calls"] == 3
    attempt = result["receipt"]["attempts"][0]
    assert attempt["outcome"] == "http-error"
    assert attempt["http_status"] == 503
    assert attempt["json_shape_verdict"] == "not-evaluated-http-error"
    assert attempt["relation_count"] is None
    assert result["report"]["candidate_count"] == 1


def test_all_invalid_shapes_retain_six_rows_and_fail_without_report():
    malformed = _FakeResponse(b"<html>bad</html>")
    missing, _ = _response({})
    result = _run_with_receipt(
        ["--dry-run", "--discovery-report", "REPORT",
         "--overpass-receipt", "RECEIPT", "DK"],
        [malformed, missing, _FakeResponse(b"<html>bad</html>"),
         _response({})[0], _FakeResponse(b"<html>bad</html>"),
         _response({})[0]],
    )

    assert result["exit"] == 1
    assert result["opener_calls"] == 6
    assert result["report"] is None
    assert len(result["receipt"]["attempts"]) == 6
    assert result["receipt"]["regions"][0]["candidate_result"] == \
        "query-exhausted"
    assert result["receipt"]["completion"] == {
        "state": "failed", "reason": "candidate-query-exhausted"}
    assert "overpass-endpoints-exhausted role=candidate region=DK" in \
        result["stderr"]
    assert "<html>" not in result["stderr"]




def test_nonempty_all_rejected_payload_is_candidates_available_not_ambiguous():
    candidate, _ = _response({"elements": [{
        "type": "relation",
        "id": 1,
        "tags": {"name": "Noise"},
        "center": {"lat": 56.2, "lon": 10.6},
    }]})
    result = _run_with_receipt(
        ["--dry-run", "--discovery-report", "REPORT",
         "--overpass-receipt", "RECEIPT", "DK"],
        [candidate, _bbox_response()],
    )

    assert result["exit"] == 0
    assert result["report"]["candidate_count"] == 0
    attempt = result["receipt"]["attempts"][0]
    assert attempt["outcome"] == "payload-accepted"
    assert attempt["relation_count"] == 1
    assert attempt["accepted_count"] == 0
    assert attempt["rejection_breakdown"]["failed_quality_filter"] == 1
    assert result["receipt"]["regions"][0]["candidate_result"] == \
        "candidates-available"
    consumer_errors = []
    validator._validate_overpass_receipt(
        result["receipt"], result["report"], consumer_errors)
    assert consumer_errors == []
def test_every_candidate_occurrence_gets_one_terminal_disposition():
    elements = [
        {
            "type": "relation", "id": 1,
            "tags": {"name": "Unsafe Include", "protect_class": "2",
                     "access": "private"},
            "center": {"lat": 56.2, "lon": 10.6},
        },
        {
            "type": "relation", "id": 2,
            "tags": {"name": "Noise"},
            "center": {"lat": 56.2, "lon": 10.6},
        },
        {
            "type": "relation", "id": 3,
            "tags": {"name": "Missing Center", "protect_class": "2"},
        },
        {
            "type": "relation",
            "tags": {"name": "Missing ID", "protect_class": "2"},
            "center": {"lat": 56.2, "lon": 10.6},
        },
        _element("relation", 5, "Outside Park", lat=1.0, lon=1.0),
        _element("relation", 7046785, "Nationalpark Mols Bjerge"),
        _element("relation", 7046785, "Nationalpark Mols Bjerge"),
        _element("relation", 8, "Excluded Park"),
    ]
    candidate, _ = _response({"elements": elements})
    result = _run_with_receipt(
        ["--dry-run", "--discovery-report", "REPORT",
         "--overpass-receipt", "RECEIPT", "DK"],
        [candidate, _bbox_response()],
        includes="unsafe include\n",
        excludes="excluded park\n",
    )

    assert result["exit"] == 0
    attempt = result["receipt"]["attempts"][0]
    assert attempt["relation_count"] == 8
    assert attempt["way_count"] == 0
    assert attempt["accepted_count"] == 1
    assert attempt["rejection_breakdown"] == {
        "failed_included_safety_floor": 1,
        "failed_quality_filter": 1,
        "missing_center": 1,
        "missing_osm_id": 1,
        "outside_region_bbox": 1,
        "manual_exclude": 1,
        "duplicate_slug": 1,
    }
    assert attempt["relation_count"] + attempt["way_count"] == (
        attempt["accepted_count"]
        + sum(attempt["rejection_breakdown"].values())
    )
    assert result["report"]["candidate_count"] == 1
    assert result["report"]["candidates"][0]["osm_relation_id"] == 7046785


def test_bbox_pair_failure_is_explicit_but_preserves_existing_degradation():
    candidate, _ = _response({"elements": [
        _element("relation", 7046785, "Nationalpark Mols Bjerge"),
    ]})
    result = _run_with_receipt(
        ["--dry-run", "--discovery-report", "REPORT",
         "--overpass-receipt", "RECEIPT", "DK"],
        [candidate, TimeoutError("first"),
         urllib.error.URLError(socket.gaierror(-2, "second"))],
    )

    assert result["exit"] == 0
    assert result["opener_calls"] == 3
    assert result["report"]["candidate_count"] == 1
    region = result["receipt"]["regions"][0]
    assert region["bbox_result"] == "query-exhausted"
    bbox_attempts = result["receipt"]["attempts"][1:]
    assert [attempt["exception_category"] for attempt in bbox_attempts] == [
        "timeout", "dns"]


def test_receipt_cli_misuse_exits_two_before_any_opener_call():
    cases = (
        ["--dry-run", "--overpass-receipt", "RECEIPT", "DK"],
        ["--discovery-report", "REPORT",
         "--overpass-receipt", "RECEIPT", "DK"],
    )
    for argv in cases:
        result = _run_with_receipt(argv, [])
        assert result["exit"] == 2
        assert result["opener_calls"] == 0
        assert result["receipt"] is None


def test_unsafe_endpoint_is_rejected_before_opener_call():
    with TemporaryDirectory() as temporary:
        receipt = sa.OverpassReceipt.initialize(
            Path(temporary) / "receipt.json", ["DK"])
        with (
            patch.object(
                sa, "OVERPASS_ENDPOINTS",
                ["https://user:token@overpass-api.de/private"]),
            patch.object(sa.urllib.request, "urlopen") as opener,
        ):
            try:
                sa.fetch_overpass(
                    sa.overpass_query("DK"),
                    region_code="DK",
                    receipt=receipt,
                )
            except sa.OverpassReceiptError:
                pass
            else:
                raise AssertionError("unsafe endpoint should fail closed")
        opener.assert_not_called()
        assert receipt.document["attempts"] == []


def test_no_receipt_mode_does_not_consume_http_error_body():
    class _UnreadableBody:
        def read(self):
            raise AssertionError("HTTP error body should remain unread")

        def close(self):
            pass

    error = urllib.error.HTTPError(
        "https://example.invalid", 500, "server error", {},
        _UnreadableBody())
    valid, _ = _response({"elements": []})
    with (
        patch.object(
            sa.urllib.request, "urlopen", side_effect=[error, valid]
        ) as opener,
        patch.object(sa.time, "sleep") as sleeper,
    ):
        document = sa.fetch_overpass("query")
    assert document == {"elements": []}
    assert opener.call_count == 2
    sleeper.assert_called_once_with(3)


def test_receipt_checkpoint_failure_aborts_before_mirror_call():
    result = None
    original = sa.OverpassReceipt.finish_transport_error
    with patch.object(
            sa.OverpassReceipt,
            "finish_transport_error",
            side_effect=sa.ReceiptWriteError(
                "Overpass receipt checkpoint failed")):
        result = _run_with_receipt(
            ["--dry-run", "--discovery-report", "REPORT",
             "--overpass-receipt", "RECEIPT", "DK"],
            [TimeoutError("first endpoint")],
        )
    assert original is not None
    assert result["exit"] == 1
    assert result["opener_calls"] == 1
    assert result["sleep_values"] == []
    assert "overpass receipt error" in result["stderr"]


def test_receipt_invariant_failure_aborts_before_mirror_or_outer_retry():
    candidate, _ = _response({"elements": [
        _element("relation", 7046785, "Nationalpark Mols Bjerge"),
    ]})
    with patch.object(
            sa.OverpassReceipt,
            "finish_response",
            side_effect=sa.OverpassReceiptError(
                "candidate evaluation invariant failed")):
        result = _run_with_receipt(
            ["--dry-run", "--discovery-report", "REPORT",
             "--overpass-receipt", "RECEIPT", "DK"],
            [candidate],
        )
    assert result["exit"] == 1
    assert result["opener_calls"] == 1
    assert result["sleep_values"] == []
    assert "overpass receipt error" in result["stderr"]
