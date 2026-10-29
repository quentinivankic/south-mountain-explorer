"""Redacted, crash-consistent observability for Overpass discovery calls.

The receipt deliberately persists only allowlisted metadata. It never stores a
query, response body, endpoint URL, header, or exception message.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import errno as errno_module
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import ssl
import tempfile
import time
from typing import Any, Callable
import urllib.error
import urllib.parse


REJECTION_KEYS = (
    "failed_included_safety_floor",
    "failed_quality_filter",
    "missing_center",
    "missing_osm_id",
    "outside_region_bbox",
    "manual_exclude",
    "duplicate_slug",
)

# Attempt wall timestamps are canonical UTC with exactly millisecond precision.
# Monotonic duration is authoritative; wall elapsed time may differ by at most
# this bounded clock/rounding tolerance. The request timeout is 300 seconds, so
# 15 minutes is a conservative fail-closed ceiling rather than a load policy.
MAX_ATTEMPT_DURATION_MS = 15 * 60 * 1000
ATTEMPT_CLOCK_TOLERANCE_MS = 100


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _monotonic_ns() -> int:
    return time.monotonic_ns()


def _format_utc_timestamp(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise OverpassReceiptError("invalid Overpass attempt clock")
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


class OverpassReceiptError(RuntimeError):
    """Base error for receipt construction and invariant failures."""


class ReceiptWriteError(OverpassReceiptError):
    """A receipt checkpoint could not be durably written."""


class DuplicateJsonKeyError(ValueError):
    """Strict JSON parsing found a duplicate object key."""


@dataclass(frozen=True)
class ParsedOverpassResponse:
    """Strict response classification without retaining raw response bytes."""

    document: Any | None
    json_shape_verdict: str
    relation_count: int | None
    way_count: int | None
    other_element_count: int | None
    exception_type: str | None = None


def _qualified_type(error: BaseException) -> str:
    error_type = type(error)
    return f"{error_type.__module__}.{error_type.__qualname__}"


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise DuplicateJsonKeyError("duplicate JSON object key")
        document[key] = value
    return document


def endpoint_host(endpoint: str) -> str:
    """Return only a validated HTTPS endpoint hostname."""
    try:
        parsed = urllib.parse.urlsplit(endpoint)
        port = parsed.port
    except (TypeError, ValueError) as error:
        raise OverpassReceiptError("invalid Overpass endpoint") from None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or port is not None
    ):
        raise OverpassReceiptError("invalid Overpass endpoint")
    return parsed.hostname.lower()


def _reject_json_constant(value: str) -> None:
    raise json.JSONDecodeError("invalid JSON constant", value, 0)


def classify_response(raw: bytes) -> ParsedOverpassResponse:
    """Parse bytes strictly and classify only the top-level Overpass shape."""
    try:
        document = json.loads(
            raw,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except DuplicateJsonKeyError as error:
        return ParsedOverpassResponse(
            None, "duplicate-json-key", None, None, None,
            _qualified_type(error),
        )
    except (UnicodeError, json.JSONDecodeError) as error:
        return ParsedOverpassResponse(
            None, "malformed-json", None, None, None,
            _qualified_type(error),
        )
    if not isinstance(document, dict):
        return ParsedOverpassResponse(
            document, "top-level-not-object", None, None, None)
    if "elements" not in document:
        return ParsedOverpassResponse(
            document, "elements-missing", None, None, None)
    elements = document["elements"]
    if not isinstance(elements, list):
        return ParsedOverpassResponse(
            document, "elements-not-list", None, None, None)
    if any(not isinstance(element, dict) for element in elements):
        return ParsedOverpassResponse(
            document, "elements-entry-not-object", None, None, None)
    relation_count = sum(
        element.get("type") == "relation" for element in elements)
    way_count = sum(element.get("type") == "way" for element in elements)
    other_count = len(elements) - relation_count - way_count
    return ParsedOverpassResponse(
        document,
        "elements-list",
        relation_count,
        way_count,
        other_count,
    )


def safe_exception(error: BaseException) -> tuple[str, str, int | None]:
    """Return stable typed exception metadata without stringifying the error."""
    reason: BaseException | object = error
    if isinstance(error, urllib.error.URLError) and not isinstance(
            error, urllib.error.HTTPError):
        reason = error.reason

    numeric_errno = getattr(reason, "errno", None)
    if not isinstance(numeric_errno, int) or isinstance(numeric_errno, bool):
        numeric_errno = getattr(error, "errno", None)
    if not isinstance(numeric_errno, int) or isinstance(numeric_errno, bool):
        numeric_errno = None

    if isinstance(error, urllib.error.HTTPError):
        category = "http"
    elif isinstance(reason, (TimeoutError, socket.timeout)):
        category = "timeout"
    elif isinstance(reason, socket.gaierror):
        category = "dns"
    elif isinstance(reason, ssl.SSLError):
        category = "tls"
    elif isinstance(reason, ConnectionError) or numeric_errno in {
            errno_module.ECONNABORTED,
            errno_module.ECONNREFUSED,
            errno_module.ECONNRESET,
            errno_module.EHOSTUNREACH,
            errno_module.ENETUNREACH,
            errno_module.EPIPE,
    }:
        category = "connection"
    elif isinstance(error, urllib.error.URLError):
        category = "url"
    elif isinstance(reason, OSError):
        category = "os"
    else:
        category = "other"
    return category, _qualified_type(error), numeric_errno


def _zero_rejections() -> dict[str, int]:
    return {key: 0 for key in REJECTION_KEYS}


class OverpassReceipt:
    """In-memory schema-v1 receipt with an atomic checkpoint after each event."""

    def __init__(
        self,
        path: Path,
        document: dict[str, Any],
        *,
        utc_now: Callable[[], datetime] | None = None,
        monotonic_ns: Callable[[], int] | None = None,
    ):
        self.path = path
        self.document = document
        self._utc_now = utc_now or _utc_now
        self._monotonic_ns = monotonic_ns or _monotonic_ns
        self._attempt_starts: dict[str, tuple[datetime, int]] = {}

    @classmethod
    def initialize(
        cls,
        path: str | os.PathLike[str],
        requested_region_codes: list[str],
        *,
        utc_now: Callable[[], datetime] | None = None,
        monotonic_ns: Callable[[], int] | None = None,
    ) -> "OverpassReceipt":
        receipt_path = Path(path)
        parent = receipt_path.parent
        if (
            not requested_region_codes
            or len(set(requested_region_codes)) != len(requested_region_codes)
            or any(
                not isinstance(code, str)
                or not re.fullmatch(r"[A-Z]{2}(?:-[A-Z0-9]{1,3})?", code)
                for code in requested_region_codes
            )
        ):
            raise OverpassReceiptError("invalid requested region codes")
        if os.path.lexists(os.fspath(receipt_path)):
            raise OverpassReceiptError("Overpass receipt target already exists")
        if parent.is_symlink() or not parent.is_dir():
            raise OverpassReceiptError("Overpass receipt parent is unsafe")
        document = {
            "schema_version": 1,
            "receipt_kind": "overpass-query-observability",
            "producer": "scripts/seed-areas.py",
            "attribution": "© OpenStreetMap contributors",
            "requested_region_codes": list(requested_region_codes),
            "attempts": [],
            "regions": [
                {
                    "region_code": code,
                    "candidate_attempt_id": None,
                    "candidate_result": "not-run",
                    "bbox_result": "not-run",
                    "report_candidate_count": None,
                }
                for code in requested_region_codes
            ],
            "completion": {"state": "in-progress", "reason": "running"},
        }
        receipt = cls(
            receipt_path,
            document,
            utc_now=utc_now,
            monotonic_ns=monotonic_ns,
        )
        receipt._checkpoint()
        return receipt

    @property
    def completion_state(self) -> str:
        return str(self.document["completion"]["state"])

    def _checkpoint(self) -> None:
        payload = (
            json.dumps(
                self.document,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
            + "\n"
        ).encode("utf-8")
        descriptor = -1
        temporary: str | None = None
        try:
            descriptor, temporary = tempfile.mkstemp(
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                dir=self.path.parent,
            )
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                descriptor = -1
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
            temporary = None
            self._fsync_parent()
        except (OSError, UnicodeError, TypeError, ValueError):
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
            raise ReceiptWriteError("Overpass receipt checkpoint failed") from None

    def _fsync_parent(self) -> None:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        try:
            descriptor = os.open(self.path.parent, flags)
        except OSError as error:
            if error.errno in {
                    errno_module.EINVAL, errno_module.ENOTSUP,
                    getattr(errno_module, "EOPNOTSUPP", errno_module.ENOTSUP)}:
                return
            raise
        try:
            try:
                os.fsync(descriptor)
            except OSError as error:
                if error.errno not in {
                        errno_module.EINVAL, errno_module.ENOTSUP,
                        getattr(errno_module, "EOPNOTSUPP", errno_module.ENOTSUP)}:
                    raise
        finally:
            os.close(descriptor)

    def _require_in_progress(self) -> None:
        if self.completion_state != "in-progress":
            raise OverpassReceiptError("Overpass receipt is already terminal")

    def _region(self, region_code: str) -> dict[str, Any]:
        for region in self.document["regions"]:
            if region["region_code"] == region_code:
                return region
        raise OverpassReceiptError("unknown Overpass receipt region")

    def _attempt(self, attempt_id: str) -> dict[str, Any]:
        for attempt in self.document["attempts"]:
            if attempt["attempt_id"] == attempt_id:
                return attempt
        raise OverpassReceiptError("unknown Overpass receipt attempt")

    def candidate_outer_attempt(self, region_code: str) -> int:
        region = self._region(region_code)
        attempt_id = region["candidate_attempt_id"]
        if not isinstance(attempt_id, str):
            raise OverpassReceiptError("candidate attempt is unavailable")
        outer_attempt = self._attempt(attempt_id)["outer_attempt"]
        if not isinstance(outer_attempt, int) or isinstance(outer_attempt, bool):
            raise OverpassReceiptError("candidate attempt context is invalid")
        return outer_attempt

    def begin_attempt(
        self,
        query_role: str,
        region_code: str,
        outer_attempt: int,
        endpoint_ordinal: int,
        endpoint_host: str,
        query_sha256: str,
    ) -> str:
        self._require_in_progress()
        self._region(region_code)
        if query_role not in {"candidate", "bbox"}:
            raise OverpassReceiptError("invalid Overpass query role")
        parent_candidate_outer_attempt = (
            self.candidate_outer_attempt(region_code)
            if query_role == "bbox" else None
        )
        if (
            not isinstance(outer_attempt, int)
            or isinstance(outer_attempt, bool)
            or outer_attempt <= 0
            or not isinstance(endpoint_ordinal, int)
            or isinstance(endpoint_ordinal, bool)
            or endpoint_ordinal <= 0
            or not isinstance(endpoint_host, str)
            or not re.fullmatch(
                r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?",
                endpoint_host,
            )
            or ".." in endpoint_host
            or len(query_sha256) != 64
            or any(character not in "0123456789abcdef" for character in query_sha256)
        ):
            raise OverpassReceiptError("invalid Overpass attempt identity")
        attempt_id = (
            f"{query_role}-{region_code}-o{outer_attempt:04d}-"
            f"e{endpoint_ordinal:04d}"
        )
        if any(
                attempt["attempt_id"] == attempt_id
                for attempt in self.document["attempts"]):
            raise OverpassReceiptError("duplicate Overpass attempt identity")
        record = {
            "attempt_id": attempt_id,
            "sequence": len(self.document["attempts"]) + 1,
            "query_role": query_role,
            "region_code": region_code,
            "outer_attempt": outer_attempt,
            "parent_candidate_outer_attempt": parent_candidate_outer_attempt,
            "endpoint_ordinal": endpoint_ordinal,
            "endpoint_host": endpoint_host,
            "request_method": "POST",
            "query_sha256": query_sha256,
            "started_at": None,
            "ended_at": None,
            "duration_ms": None,
            "outcome": None,
            "http_status": None,
            "exception_category": None,
            "exception_type": None,
            "exception_errno": None,
            "response_byte_count": None,
            "response_sha256": None,
            "json_shape_verdict": "not-observed",
            "relation_count": None,
            "way_count": None,
            "other_element_count": None,
            "accepted_count": None,
            "rejection_breakdown": None,
        }
        self.document["attempts"].append(record)
        self._checkpoint()
        return attempt_id

    def _open_attempt(self, attempt_id: str) -> dict[str, Any]:
        self._require_in_progress()
        attempt = self._attempt(attempt_id)
        if attempt["outcome"] is not None:
            raise OverpassReceiptError("Overpass attempt is already complete")
        return attempt

    def start_attempt_timing(self, attempt_id: str) -> None:
        """Start the clocks immediately before the endpoint I/O begins."""
        attempt = self._open_attempt(attempt_id)
        if (attempt_id in self._attempt_starts
                or any(attempt[field] is not None for field in (
                    "started_at", "ended_at", "duration_ms"))):
            raise OverpassReceiptError("Overpass attempt timing already started")
        provisional_at = self._utc_now()
        attempt["started_at"] = _format_utc_timestamp(provisional_at)
        self._checkpoint()

        started_at = self._utc_now()
        started_monotonic_ns = self._monotonic_ns()
        if (not isinstance(started_monotonic_ns, int)
                or isinstance(started_monotonic_ns, bool)
                or started_monotonic_ns < 0):
            raise OverpassReceiptError("invalid Overpass attempt clock")
        attempt["started_at"] = _format_utc_timestamp(started_at)
        self._attempt_starts[attempt_id] = (
            started_at.astimezone(timezone.utc), started_monotonic_ns)

    def close_attempt_timing(self, attempt_id: str) -> None:
        """Close timing once, before response parsing or outcome checkpointing."""
        attempt = self._open_attempt(attempt_id)
        if all(attempt[field] is not None for field in (
                "started_at", "ended_at", "duration_ms")):
            return
        start = self._attempt_starts.get(attempt_id)
        if start is None or attempt["started_at"] is None:
            raise OverpassReceiptError("Overpass attempt timing was not started")
        ended_monotonic_ns = self._monotonic_ns()
        ended_at = self._utc_now()
        if (not isinstance(ended_monotonic_ns, int)
                or isinstance(ended_monotonic_ns, bool)
                or ended_monotonic_ns < start[1]):
            raise OverpassReceiptError("invalid Overpass attempt clock")
        ended_at_utc = (
            ended_at.astimezone(timezone.utc)
            if isinstance(ended_at, datetime) and ended_at.tzinfo is not None
            else None
        )
        if ended_at_utc is None or ended_at_utc < start[0]:
            raise OverpassReceiptError("invalid Overpass attempt clock")
        duration_ms = (ended_monotonic_ns - start[1]) // 1_000_000
        wall_duration_ms = int(
            (ended_at_utc - start[0]).total_seconds() * 1000)
        if (duration_ms > MAX_ATTEMPT_DURATION_MS
                or abs(wall_duration_ms - duration_ms)
                > ATTEMPT_CLOCK_TOLERANCE_MS):
            raise OverpassReceiptError("invalid Overpass attempt duration")
        attempt.update({
            "ended_at": _format_utc_timestamp(ended_at_utc),
            "duration_ms": duration_ms,
        })
        del self._attempt_starts[attempt_id]
        self._checkpoint()

    def finish_transport_error(
            self, attempt_id: str, error: BaseException) -> None:
        self.close_attempt_timing(attempt_id)
        attempt = self._open_attempt(attempt_id)
        category, error_type, numeric_errno = safe_exception(error)
        attempt.update({
            "outcome": "transport-error",
            "exception_category": category,
            "exception_type": error_type,
            "exception_errno": numeric_errno,
            "json_shape_verdict": "not-observed",
        })
        self._checkpoint()

    def finish_http_error(
        self,
        attempt_id: str,
        error: urllib.error.HTTPError,
        response_body: bytes | None,
        *,
        body_read_error: BaseException | None = None,
    ) -> None:
        self.close_attempt_timing(attempt_id)
        attempt = self._open_attempt(attempt_id)
        diagnostic_error = body_read_error or error
        category, error_type, numeric_errno = safe_exception(diagnostic_error)
        response_byte_count = None
        response_sha256 = None
        if response_body is not None:
            response_byte_count = len(response_body)
            response_sha256 = hashlib.sha256(response_body).hexdigest()
        attempt.update({
            "outcome": "http-error",
            "http_status": int(error.code),
            "exception_category": category,
            "exception_type": error_type,
            "exception_errno": numeric_errno,
            "response_byte_count": response_byte_count,
            "response_sha256": response_sha256,
            "json_shape_verdict": "not-evaluated-http-error",
        })
        self._checkpoint()

    def finish_response(
        self,
        attempt_id: str,
        *,
        http_status: int,
        raw: bytes,
        parsed: ParsedOverpassResponse,
    ) -> str:
        self.close_attempt_timing(attempt_id)
        attempt = self._open_attempt(attempt_id)
        verdict = parsed.json_shape_verdict
        if verdict in {"malformed-json", "duplicate-json-key"}:
            outcome = "malformed-json"
        elif verdict != "elements-list":
            outcome = "invalid-json-shape"
        elif (
            attempt["query_role"] == "candidate"
            and parsed.relation_count == 0
            and parsed.way_count == 0
        ):
            outcome = "ambiguous-empty-primary-response"
        else:
            outcome = "payload-accepted"
        attempt.update({
            "outcome": outcome,
            "http_status": int(http_status),
            "response_byte_count": len(raw),
            "response_sha256": hashlib.sha256(raw).hexdigest(),
            "json_shape_verdict": verdict,
            "relation_count": parsed.relation_count,
            "way_count": parsed.way_count,
            "other_element_count": parsed.other_element_count,
        })
        if outcome == "malformed-json":
            attempt["exception_category"] = "json"
            attempt["exception_type"] = parsed.exception_type
        if outcome == "ambiguous-empty-primary-response":
            attempt["accepted_count"] = 0
            attempt["rejection_breakdown"] = _zero_rejections()
        if attempt["query_role"] == "candidate" and outcome in {
                "payload-accepted", "ambiguous-empty-primary-response"}:
            region = self._region(attempt["region_code"])
            region["candidate_attempt_id"] = attempt_id
            region["candidate_result"] = (
                "ambiguous-empty-primary-response"
                if outcome == "ambiguous-empty-primary-response"
                else "candidates-available"
            )
        self._checkpoint()
        return outcome

    def record_bbox_result(self, region_code: str, result: str) -> None:
        self._require_in_progress()
        if result not in {
                "usable-bbox", "no-usable-bbox", "query-exhausted"}:
            raise OverpassReceiptError("invalid Overpass bbox result")
        self._region(region_code)["bbox_result"] = result
        self._checkpoint()

    def record_candidate_query_exhausted(self, region_code: str) -> None:
        self._require_in_progress()
        region = self._region(region_code)
        region["candidate_attempt_id"] = None
        region["candidate_result"] = "query-exhausted"
        region["report_candidate_count"] = None
        self._checkpoint()

    def record_candidate_local_evaluation(
        self,
        region_code: str,
        accepted_count: int,
        rejection_breakdown: dict[str, int],
    ) -> None:
        self._require_in_progress()
        region = self._region(region_code)
        attempt_id = region["candidate_attempt_id"]
        if not isinstance(attempt_id, str):
            raise OverpassReceiptError("candidate attempt is unavailable")
        attempt = self._attempt(attempt_id)
        if (
            set(rejection_breakdown) != set(REJECTION_KEYS)
            or not isinstance(accepted_count, int)
            or isinstance(accepted_count, bool)
            or accepted_count < 0
            or any(
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
                for value in rejection_breakdown.values()
            )
        ):
            raise OverpassReceiptError("invalid candidate evaluation")
        relation_count = attempt["relation_count"]
        way_count = attempt["way_count"]
        if (
            not isinstance(relation_count, int)
            or not isinstance(way_count, int)
            or relation_count + way_count
            != accepted_count + sum(rejection_breakdown.values())
        ):
            raise OverpassReceiptError("candidate evaluation count mismatch")
        if attempt["outcome"] == "ambiguous-empty-primary-response" and (
                accepted_count != 0 or any(rejection_breakdown.values())):
            raise OverpassReceiptError("ambiguous candidate evaluation mismatch")
        attempt["accepted_count"] = accepted_count
        attempt["rejection_breakdown"] = dict(rejection_breakdown)
        self._checkpoint()

    def finalize_candidate_output(
        self,
        region_code: str,
        *,
        accepted_count: int,
        manual_exclude: int,
        duplicate_slug: int,
    ) -> None:
        self._require_in_progress()
        region = self._region(region_code)
        attempt_id = region["candidate_attempt_id"]
        if not isinstance(attempt_id, str):
            raise OverpassReceiptError("candidate attempt is unavailable")
        attempt = self._attempt(attempt_id)
        rejection_breakdown = attempt["rejection_breakdown"]
        if (
            not isinstance(rejection_breakdown, dict)
            or any(
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
                for value in (accepted_count, manual_exclude, duplicate_slug)
            )
            or attempt["accepted_count"]
            != accepted_count + manual_exclude + duplicate_slug
        ):
            raise OverpassReceiptError("candidate output count mismatch")
        rejection_breakdown["manual_exclude"] += manual_exclude
        rejection_breakdown["duplicate_slug"] += duplicate_slug
        attempt["accepted_count"] = accepted_count
        if (
            attempt["relation_count"] + attempt["way_count"]
            != accepted_count + sum(rejection_breakdown.values())
        ):
            raise OverpassReceiptError("candidate output equation mismatch")
        region["report_candidate_count"] = accepted_count
        self._checkpoint()

    def finish(self, state: str, reason: str) -> None:
        self._require_in_progress()
        allowed = {
            ("complete", "candidate-processing-complete"),
            ("failed", "candidate-query-exhausted"),
            ("failed", "internal-error"),
        }
        if (state, reason) not in allowed:
            raise OverpassReceiptError("invalid Overpass completion state")
        self.document["completion"] = {"state": state, "reason": reason}
        self._checkpoint()
