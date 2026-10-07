#!/usr/bin/env python3
"""Pure, closed ScenePack v1 imagery authority and bounded qRGB codec.

All authority is supplied as immutable bytes.  The module performs no path,
network, dynamic decoder, plugin, or system lookup. Original response bytes,
request/capture identity, fixed conversion, clean-source assertion, and
source-to-cell transforms are content addressed. Those bindings prove only
consistency; real-world origin comes solely from external repository-pinned
reviewed acquisition authority.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import struct
import zlib
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, localcontext
from typing import Iterable, Iterator, Mapping

CELL_WIDTH = 512
CELL_HEIGHT = 512
CELL_CHANNELS = 3
CELL_PIXEL_COUNT = CELL_WIDTH * CELL_HEIGHT
DECODED_RGB_LENGTH = CELL_PIXEL_COUNT * CELL_CHANNELS
GRID_SCHEME = "epsg3857-micrometre-grid-v1"
WEB_MERCATOR_LIMIT_UM = 20_037_508_342_789
MATRIX_PIXEL_UM = {
    "context-v1": 500_000,
    "detail-v1": 200_000,
    "confirm-v1": 125_000,
}
MAX_CELLS_PER_FRAME = 64
MAX_SOURCE_SIDE = 4096
MAX_SOURCE_PIXELS = 16 * 1024 * 1024
MAX_SOURCE_RESPONSE_BYTES = 48 * 1024 * 1024

QRGB_CODEC = "qrgb-zlib-v1"
QRGB_MAGIC = b"QRGBZV1\x00"
QRGB_COLORSPACE_SRGB = 1
QRGB_MODES = (0, 1, 2, 3)
QRGB_HEADER = struct.Struct(">8sHHBBBBII32s")
MAX_QRGB_PAYLOAD_BYTES = 1024 * 1024
MAX_QRGB_EXPANSION_RATIO = 1024
QRGB_ENCODER_VERSION = "qrgb-zlib-v1-reference-1"

SOURCE_KIND = "parking-offline-imagery-source"
SOURCE_MEDIA_TYPE = "application/x-rgb8"
SOURCE_DECODER = "raw-rgb8-exact-v1"
SOURCE_DECODER_VERSION = "imagery-objects-v5-1"
SOURCE_COLOR_PROFILE = "srgb-iec61966-2-1"
SOURCE_ORIENTATION = "top-left-row-major"
SOURCE_ALPHA = "none"
CLEAN_SOURCE_ASSERTION_KIND = "parking-clean-unoverlaid-source-assertion"
CLEAN_SOURCE_ASSERTION = "clean-unoverlaid-provider-imagery"
CLEAN_SOURCE_REVIEW_SCOPE = "requires-pinned-acquisition-approval"
SOURCE_TRANSFORM_KIND = "parking-source-to-cell-receipt"
SOURCE_TRANSFORM = "bilinear-rgb8-fixed-clamp-v1"
CRITICAL_MASK_RECIPE_KIND = "parking-critical-mask-recipe"
CRITICAL_MASK_DERIVATION = "reference-edge-dilate1-plus-border-v1"
QUALITY_RECEIPT_KIND = "parking-imagery-quality-receipt"
SEAM_RECEIPT_KIND = "parking-imagery-seam-quality-receipt"
IMAGERY_CATALOG_KIND = "parking-imagery-catalog"

QUALITY_POLICY = {
    "version": 2,
    "kind": "parking-imagery-quality-policy",
    "cell_width": CELL_WIDTH,
    "cell_height": CELL_HEIGHT,
    "color_space": "srgb-rgb8",
    "orientation": "top-left-row-major",
    "alpha": "none",
    "allowed_max_abs_channel_error": list(QRGB_MODES),
    "max_mean_abs_error_milli": 1500,
    # MSE <= 4.1 is a conservative integer form of the 42 dB gate.
    "max_squared_error_tenths_per_sample": 41,
    "min_rgb_psnr_millidb": 42_000,
    "ssim_window_side": 8,
    "min_mean_window_luma_ssim_ppm": 995_000,
    # A minimum-window floor prevents a tiny damaged region from disappearing
    # into the cell mean while retaining useful near-black qRGB candidates.
    "min_minimum_window_luma_ssim_ppm": 900_000,
    "edge_threshold_luma8": 24,
    "min_edge_direction_agreement_ppm": 999_000,
    "connected_component_neighborhood": "eight",
    "max_connected_edge_change_pixels": 2,
    "max_border_edge_change_pixels": 2,
    "max_connected_seam_edge_change_pixels": 2,
    "source_decoder": SOURCE_DECODER,
    "source_decoder_version": SOURCE_DECODER_VERSION,
    "source_color_profile": SOURCE_COLOR_PROFILE,
    "source_transform": SOURCE_TRANSFORM,
    "critical_mask_derivation": CRITICAL_MASK_DERIVATION,
}

BYTE_CATEGORIES = frozenset({
    "source-response-bytes",
    "source-descriptor",
    "clean-source-assertion",
    "source-transform-receipt",
    "acquisition-manifest",
    "reference-rgb",
    "critical-mask",
    "critical-mask-recipe",
    "cell-payload",
    "quality-receipt",
    "seam-quality-receipt",
    "imagery-catalog",
    "overlay-scene",
    "bitmap-font",
    "frame-recipe",
    "frame-fidelity-receipt",
    "frame-set",
    "packet-corpus",
    "prompt-recipe",
    "structured-authority",
    "proof-manifest",
})

_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_PROVIDER_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}\Z")
_SOURCE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_UTC_RE = re.compile(
    r"\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])"
    r"T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\dZ\Z"
)
FIXED_ONE = 1 << 16

MAX_CANONICAL_JSON_BYTES = 48 * 1024 * 1024
MAX_CANONICAL_JSON_DEPTH = 64
MAX_CANONICAL_JSON_NODES = 5_000_000
MAX_CANONICAL_JSON_CONTAINERS = 1_100_000
MAX_CANONICAL_JSON_CONTAINER_ENTRIES = 200_000
MAX_CANONICAL_JSON_TOTAL_CONTAINER_ENTRIES = 5_000_000
MAX_CANONICAL_JSON_STRING_BYTES = 16 * 1024 * 1024
MAX_CANONICAL_JSON_TOTAL_STRING_BYTES = 64 * 1024 * 1024
_JSON_RESOURCE_ERRORS = (RecursionError, MemoryError, OverflowError)
_JSON_HEX_BYTES = frozenset(b"0123456789abcdefABCDEF")


@dataclass(frozen=True)
class JsonStructure:
    """Deterministic pre-decode bounds for one immutable JSON document."""

    raw_bytes: int
    raw_sha256: str
    max_depth: int
    nodes: int
    containers: int
    container_entries: int
    string_expansion_bytes: int
    maximum_string_expansion_bytes: int


_JSON_WORK_BOUNDARIES = frozenset({
    "structural_scan",
    "json_decode",
    "canonical_serialization",
    "canonical_hash",
    "canonical_revalidation",
    "legacy_json_serialization",
})
_JSON_WORK_PHASE_RE = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")


@dataclass
class CanonicalJsonWorkAudit:
    """One fail-closed byte-operation ledger for every JSON work boundary."""

    reserved_byte_operations: int
    reservation_scope: str = "caller-provided"
    reservation_components: Mapping[str, int] = field(default_factory=dict)
    phase_components: Mapping[str, str] = field(default_factory=dict)
    executed_by_boundary: dict[str, int] = field(default_factory=dict)
    calls_by_boundary: dict[str, int] = field(default_factory=dict)
    _executed_by_phase: dict[str, dict[str, int]] = field(default_factory=dict)
    _calls_by_phase: dict[str, dict[str, int]] = field(default_factory=dict)
    _executed_by_component: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (type(self.reserved_byte_operations) is not int
                or self.reserved_byte_operations < 0
                or not isinstance(self.reservation_scope, str)
                or _JSON_WORK_PHASE_RE.fullmatch(self.reservation_scope) is None):
            raise ValueError("canonical JSON reservation is invalid")
        if (not isinstance(self.reservation_components, Mapping)
                or any(
                    not isinstance(name, str)
                    or _JSON_WORK_PHASE_RE.fullmatch(name) is None
                    or type(value) is not int or value < 0
                    for name, value in self.reservation_components.items()
                )
                or (self.reservation_components
                    and sum(self.reservation_components.values())
                    != self.reserved_byte_operations)):
            raise ValueError("canonical JSON reservation components do not close")
        if (not isinstance(self.phase_components, Mapping)
                or any(
                    not isinstance(phase, str)
                    or _JSON_WORK_PHASE_RE.fullmatch(phase) is None
                    or not isinstance(component, str)
                    or component not in self.reservation_components
                    for phase, component in self.phase_components.items()
                )):
            raise ValueError("canonical JSON phase components are invalid")
        self.reservation_components = dict(self.reservation_components)
        self.phase_components = dict(self.phase_components)

    @property
    def executed_byte_operations(self) -> int:
        return sum(self.executed_by_boundary.values())

    def charge(
            self, boundary: str, byte_operations: int,
            phase: str = "unscoped",
    ) -> None:
        if (boundary not in _JSON_WORK_BOUNDARIES
                or type(byte_operations) is not int or byte_operations < 0
                or not isinstance(phase, str)
                or _JSON_WORK_PHASE_RE.fullmatch(phase) is None):
            raise ValueError("invalid canonical JSON work charge")
        component = self.phase_components.get(phase)
        if self.reservation_components and component is None:
            raise ValueError(
                f"canonical JSON work phase {phase!r} has no reserved component"
            )
        if component is not None:
            component_attempted = (
                self._executed_by_component.get(component, 0) + byte_operations
            )
            component_limit = self.reservation_components[component]
            if component_attempted > component_limit:
                raise ValueError(
                    "canonical JSON executed byte-operation reservation "
                    f"exceeded for component {component}: "
                    f"{component_attempted} > {component_limit}"
                )
        attempted = self.executed_byte_operations + byte_operations
        if attempted > self.reserved_byte_operations:
            raise ValueError(
                "canonical JSON executed byte-operation reservation exceeded: "
                f"{attempted} > {self.reserved_byte_operations}"
            )
        if component is not None:
            self._executed_by_component[component] = component_attempted
        self.executed_by_boundary[boundary] = (
            self.executed_by_boundary.get(boundary, 0) + byte_operations
        )
        self.calls_by_boundary[boundary] = (
            self.calls_by_boundary.get(boundary, 0) + 1
        )
        phase_bytes = self._executed_by_phase.setdefault(phase, {})
        phase_calls = self._calls_by_phase.setdefault(phase, {})
        phase_bytes[boundary] = phase_bytes.get(boundary, 0) + byte_operations
        phase_calls[boundary] = phase_calls.get(boundary, 0) + 1

    def report(self) -> dict[str, object]:
        executed = self.executed_byte_operations
        phases = {}
        for phase in sorted(self._executed_by_phase):
            by_boundary = dict(sorted(self._executed_by_phase[phase].items()))
            phases[phase] = {
                "executed_byte_operations": sum(by_boundary.values()),
                "executed_by_boundary": by_boundary,
                "calls_by_boundary": dict(sorted(
                    self._calls_by_phase[phase].items()
                )),
            }
        components = {
            name: {
                "reserved_byte_operations": reservation,
                "executed_byte_operations": self._executed_by_component.get(
                    name, 0
                ),
                "headroom_byte_operations": (
                    reservation - self._executed_by_component.get(name, 0)
                ),
            }
            for name, reservation in sorted(
                self.reservation_components.items()
            )
        }
        return {
            "reservation_scope": self.reservation_scope,
            "reservation_components": dict(sorted(
                self.reservation_components.items()
            )),
            "phase_components": dict(sorted(self.phase_components.items())),
            "component_execution": components,
            "reservation_semantics": (
                "one scope-wide canonical-JSON hard ceiling and its named "
                "component ceilings are installed before the scope's first "
                "JSON boundary; unpartitioned nested phases reuse the ledger "
                "without resetting or rebasing, nested component maps are "
                "rejected rather than discarded, and each boundary refuses "
                "before either overrun"
            ),
            "execution_semantics": (
                "executed byte-operations are exact logical boundary charges: "
                "scan and decode input bytes, serialized and hashed JSON document "
                "bytes, and max(original, canonical) bytes for equality "
                "revalidation; binary RGB and qRGB hashes are excluded. When "
                "this snapshot is embedded in the benchmark's final report, "
                "that report-emitting serialization is deliberately excluded: "
                "including it would require the report to account for bytes "
                "whose audit totals are themselves serialized inside the report"
            ),
            "reserved_byte_operations": self.reserved_byte_operations,
            "executed_byte_operations": executed,
            "headroom_byte_operations": (
                self.reserved_byte_operations - executed
            ),
            "executed_by_boundary": dict(sorted(
                self.executed_by_boundary.items()
            )),
            "calls_by_boundary": dict(sorted(self.calls_by_boundary.items())),
            "phases": phases,
        }


_ACTIVE_CANONICAL_JSON_WORK_AUDIT: ContextVar[
    CanonicalJsonWorkAudit | None
] = ContextVar("scenepack_canonical_json_work_audit", default=None)
_ACTIVE_CANONICAL_JSON_WORK_PHASE: ContextVar[str] = ContextVar(
    "scenepack_canonical_json_work_phase", default="unscoped"
)


@contextmanager
def audit_canonical_json_work(
        reserved_byte_operations: int, *, scope: str = "caller-provided",
        components: Mapping[str, int] | None = None,
        phase_components: Mapping[str, str] | None = None,
) -> Iterator[CanonicalJsonWorkAudit]:
    """Reuse an active ledger only when no component map is requested.

    A nested component or phase map cannot install a tighter ledger without
    discarding or weakening parent accounting, so mapped nesting is rejected.
    """
    if (type(reserved_byte_operations) is not int
            or reserved_byte_operations < 0
            or not isinstance(scope, str)
            or _JSON_WORK_PHASE_RE.fullmatch(scope) is None):
        raise ValueError("canonical JSON reservation is invalid")
    if components is not None and (
            not isinstance(components, Mapping)
            or any(
                not isinstance(name, str)
                or _JSON_WORK_PHASE_RE.fullmatch(name) is None
                or type(value) is not int or value < 0
                for name, value in components.items()
            )
            or sum(components.values()) != reserved_byte_operations):
        raise ValueError("canonical JSON reservation components do not close")
    exact_components = {} if components is None else components
    if (phase_components is not None and (
            not isinstance(phase_components, Mapping)
            or any(
                not isinstance(phase, str)
                or _JSON_WORK_PHASE_RE.fullmatch(phase) is None
                or not isinstance(component, str)
                or component not in exact_components
                for phase, component in phase_components.items()
            ))):
        raise ValueError("canonical JSON phase components are invalid")
    existing = _ACTIVE_CANONICAL_JSON_WORK_AUDIT.get()
    if existing is not None:
        if components is not None or phase_components is not None:
            raise ValueError(
                "nested canonical JSON component maps cannot be installed "
                "over an active parent"
            )
        if reserved_byte_operations > existing.reserved_byte_operations:
            raise ValueError(
                "nested canonical JSON reservation exceeds its active parent"
            )
        yield existing
        return
    audit = CanonicalJsonWorkAudit(
        reserved_byte_operations,
        reservation_scope=scope,
        reservation_components=exact_components,
        phase_components=(
            {} if phase_components is None else phase_components
        ),
    )
    token = _ACTIVE_CANONICAL_JSON_WORK_AUDIT.set(audit)
    try:
        yield audit
    finally:
        _ACTIVE_CANONICAL_JSON_WORK_AUDIT.reset(token)


@contextmanager
def canonical_json_work_phase(phase: str) -> Iterator[None]:
    """Label work inside one shared audit without creating a nested ledger."""
    if (not isinstance(phase, str)
            or _JSON_WORK_PHASE_RE.fullmatch(phase) is None):
        raise ValueError("canonical JSON work phase is invalid")
    token = _ACTIVE_CANONICAL_JSON_WORK_PHASE.set(phase)
    try:
        yield
    finally:
        _ACTIVE_CANONICAL_JSON_WORK_PHASE.reset(token)


def charge_json_work(boundary: str, byte_operations: int) -> None:
    """Charge one boundary to the active scope and current named phase."""
    audit = _ACTIVE_CANONICAL_JSON_WORK_AUDIT.get()
    if audit is not None:
        audit.charge(
            boundary, byte_operations,
            _ACTIVE_CANONICAL_JSON_WORK_PHASE.get(),
        )


def charge_json_revalidation(original: bytes, canonical: bytes) -> None:
    """Charge one complete canonical-byte equality boundary."""
    charge_json_work(
        "canonical_revalidation", max(len(original), len(canonical))
    )


def inspect_json_structure(raw: bytes, label: str) -> JsonStructure:
    """Bound nesting and allocation proxies without recursive JSON decoding."""
    if not isinstance(raw, bytes):
        raise ValueError(f"{label} must be bytes")
    charge_json_work("structural_scan", len(raw))
    if len(raw) > MAX_CANONICAL_JSON_BYTES:
        raise ValueError(f"{label} JSON bytes exceed the structural scan cap")

    try:
        stack: list[list[int]] = []
        max_depth = 0
        nodes = 0
        containers = 0
        container_entries = 0
        string_expansion_bytes = 0
        maximum_string_expansion_bytes = 0
        index = 0

        def add_node() -> None:
            nonlocal nodes, container_entries
            nodes += 1
            if nodes > MAX_CANONICAL_JSON_NODES:
                raise ValueError(f"{label} JSON node expansion exceeds its cap")
            if stack:
                stack[-1][1] += 1
                container_entries += 1
                if stack[-1][1] > MAX_CANONICAL_JSON_CONTAINER_ENTRIES:
                    raise ValueError(
                        f"{label} JSON container expansion exceeds its cap"
                    )
                if (container_entries
                        > MAX_CANONICAL_JSON_TOTAL_CONTAINER_ENTRIES):
                    raise ValueError(
                        f"{label} aggregate JSON container expansion exceeds its cap"
                    )

        while index < len(raw):
            byte = raw[index]
            if byte in b" \t\r\n:":
                index += 1
                continue
            if byte == 34:  # '"'
                add_node()
                index += 1
                string_bytes = 0
                while True:
                    if index >= len(raw):
                        raise ValueError(f"{label} JSON string is unterminated")
                    byte = raw[index]
                    if byte == 34:
                        index += 1
                        break
                    if byte == 92:  # '\\'
                        index += 1
                        if index >= len(raw):
                            raise ValueError(f"{label} JSON escape is unterminated")
                        escape = raw[index]
                        if escape == 117:  # 'u'
                            digits = raw[index + 1:index + 5]
                            if (len(digits) != 4
                                    or any(value not in _JSON_HEX_BYTES
                                           for value in digits)):
                                raise ValueError(
                                    f"{label} JSON unicode escape is malformed"
                                )
                            string_bytes += 4
                            index += 5
                        elif escape in b'"\\/bfnrt':
                            string_bytes += 1
                            index += 1
                        else:
                            raise ValueError(f"{label} JSON escape is malformed")
                    else:
                        if byte < 32:
                            raise ValueError(
                                f"{label} JSON string contains a control byte"
                            )
                        # Four bytes per non-ASCII source byte conservatively
                        # bounds Python's widest in-memory Unicode storage.
                        string_bytes += 4 if byte >= 128 else 1
                        index += 1
                    if string_bytes > MAX_CANONICAL_JSON_STRING_BYTES:
                        raise ValueError(
                            f"{label} JSON string expansion exceeds its cap"
                        )
                string_expansion_bytes += string_bytes
                maximum_string_expansion_bytes = max(
                    maximum_string_expansion_bytes, string_bytes
                )
                if (string_expansion_bytes
                        > MAX_CANONICAL_JSON_TOTAL_STRING_BYTES):
                    raise ValueError(
                        f"{label} aggregate JSON string expansion exceeds its cap"
                    )
                continue
            if byte in (91, 123):  # '[', '{'
                add_node()
                containers += 1
                if containers > MAX_CANONICAL_JSON_CONTAINERS:
                    raise ValueError(
                        f"{label} JSON container count exceeds its cap"
                    )
                stack.append([byte, 0])
                max_depth = max(max_depth, len(stack))
                if max_depth > MAX_CANONICAL_JSON_DEPTH:
                    raise ValueError(
                        f"{label} JSON structural depth exceeds its cap"
                    )
                index += 1
                continue
            if byte in (93, 125):  # ']', '}'
                expected = 91 if byte == 93 else 123
                if not stack or stack[-1][0] != expected:
                    raise ValueError(f"{label} JSON containers are unbalanced")
                stack.pop()
                index += 1
                continue
            if byte == 44:  # ','
                index += 1
                continue

            add_node()
            while (index < len(raw)
                   and raw[index] not in b" \t\r\n,:[]{}"):
                index += 1

        if stack:
            raise ValueError(f"{label} JSON containers are unterminated")
        return JsonStructure(
            raw_bytes=len(raw),
            raw_sha256=sha256_canonical_bytes(raw),
            max_depth=max_depth,
            nodes=nodes,
            containers=containers,
            container_entries=container_entries,
            string_expansion_bytes=string_expansion_bytes,
            maximum_string_expansion_bytes=maximum_string_expansion_bytes,
        )
    except _JSON_RESOURCE_ERRORS as error:
        raise ValueError(
            f"{label} JSON structural scan exceeded resource limits"
        ) from error


def canonical_json(value: object) -> bytes:
    """Return the sole bounded canonical JSON representation for ScenePack."""
    try:
        raw = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except _JSON_RESOURCE_ERRORS as error:
        raise ValueError(
            "canonical JSON serialization exceeded resource limits"
        ) from error
    if len(raw) > MAX_CANONICAL_JSON_BYTES:
        raise ValueError("canonical JSON serialization exceeds its byte cap")
    charge_json_work("canonical_serialization", len(raw))
    return raw


def sha256_bytes(value: bytes) -> str:
    """Hash opaque or binary bytes; this is never a JSON-work charge."""
    return hashlib.sha256(value).hexdigest()


def sha256_canonical_bytes(value: bytes) -> str:
    """Hash serialized JSON document bytes and charge that exact hash pass."""
    if not isinstance(value, bytes):
        raise ValueError("canonical JSON hash input must be bytes")
    charge_json_work("canonical_hash", len(value))
    return sha256_bytes(value)


def sha256_json(value: object) -> str:
    return sha256_canonical_bytes(canonical_json(value))


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key {key!r}")
        value[key] = item
    return value


def parse_canonical_json(raw: bytes, label: str) -> object:
    """Parse structurally bounded, duplicate-free canonical ScenePack JSON."""
    inspect_json_structure(raw, label)
    charge_json_work("json_decode", len(raw))
    try:
        value = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, ValueError, *_JSON_RESOURCE_ERRORS) as error:
        raise ValueError(f"{label} is not strict JSON: {error}") from error
    canonical = canonical_json(value)
    charge_json_revalidation(raw, canonical)
    if canonical != raw:
        raise ValueError(f"{label} bytes are noncanonical")
    return value


def _canonical_document(value: object, label: str) -> tuple[dict, bytes]:
    if isinstance(value, bytes):
        parsed = parse_canonical_json(value, label)
        if not isinstance(parsed, dict):
            raise ValueError(f"{label} must be an object")
        return parsed, value
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object or canonical bytes")
    raw = canonical_json(value)
    return value, raw


def valid_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _closed(value: object, keys: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(f"{label} schema mismatch")
    return value


def _integer(value: object, label: str, minimum: int | None = None,
             maximum: int | None = None) -> int:
    if type(value) is not int:
        raise ValueError(f"{label} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{label} is below its minimum")
    if maximum is not None and value > maximum:
        raise ValueError(f"{label} exceeds its maximum")
    return value


def _sha256(value: object, label: str) -> str:
    if not valid_sha256(value):
        raise ValueError(f"{label} must be a lowercase SHA-256")
    return value


def _utc(value: object, label: str) -> str:
    if not isinstance(value, str) or _UTC_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be second-precision UTC")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as error:
        raise ValueError(f"{label} is not a real UTC timestamp") from error
    return value


QUALITY_POLICY_SHA256 = sha256_json(QUALITY_POLICY)


def matrix_cell_span_um(matrix: str) -> int:
    try:
        return MATRIX_PIXEL_UM[matrix] * CELL_WIDTH
    except (KeyError, TypeError) as error:
        raise ValueError("ScenePack matrix is unsupported") from error


def renderable_limit_um(matrix: str) -> int:
    """Return the largest symmetric limit tiled by complete grid cells."""
    span = matrix_cell_span_um(matrix)
    return (WEB_MERCATOR_LIMIT_UM // span) * span


def grid_cell_bounds(matrix: str, x: int, y: int) -> tuple[int, int, int, int]:
    """Return one canonical half-open complete cell on the signed grid."""
    _integer(x, "grid x")
    _integer(y, "grid y")
    span = matrix_cell_span_um(matrix)
    bounds = (x * span, y * span, (x + 1) * span, (y + 1) * span)
    limit = renderable_limit_um(matrix)
    if (bounds[0] < -limit or bounds[1] < -limit
            or bounds[2] > limit or bounds[3] > limit):
        raise ValueError(
            "grid cell is outside the complete-cell EPSG:3857 domain"
        )
    return bounds


def validate_viewport(
        viewport: object, matrix: str | None = None,
) -> tuple[int, int, int, int]:
    if (not isinstance(viewport, list) or len(viewport) != 4
            or any(type(value) is not int for value in viewport)):
        raise ValueError("viewport_um must contain four integers")
    x0, y0, x1, y1 = viewport
    if not (-WEB_MERCATOR_LIMIT_UM <= x0 < x1 <= WEB_MERCATOR_LIMIT_UM
            and -WEB_MERCATOR_LIMIT_UM <= y0 < y1 <= WEB_MERCATOR_LIMIT_UM):
        raise ValueError(
            "viewport_um is empty, wraps the dateline, or exceeds EPSG:3857"
        )
    if matrix is not None:
        limit = renderable_limit_um(matrix)
        if not (-limit <= x0 < x1 <= limit and -limit <= y0 < y1 <= limit):
            omitted = WEB_MERCATOR_LIMIT_UM - limit
            raise ValueError(
                f"viewport enters the unsupported terminal grid strip "
                f"({omitted} micrometres for {matrix})"
            )
    return x0, y0, x1, y1


def expected_cell_coordinates(
        matrix: str, viewport: object,
) -> tuple[tuple[int, int], ...]:
    """Return exact complete-cell coverage in top-to-bottom row order."""
    x0, y0, x1, y1 = validate_viewport(viewport, matrix)
    span = matrix_cell_span_um(matrix)
    x_start = x0 // span
    x_stop = (x1 - 1) // span
    y_start = y0 // span
    y_stop = (y1 - 1) // span
    coordinates = tuple(
        (x, y)
        for y in range(y_stop, y_start - 1, -1)
        for x in range(x_start, x_stop + 1)
    )
    if not coordinates or len(coordinates) > MAX_CELLS_PER_FRAME:
        raise ValueError("viewport requires an unsupported number of cells")
    for x, y in coordinates:
        grid_cell_bounds(matrix, x, y)
    return coordinates


def _rgb(value: object, label: str = "RGB cell") -> bytes:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise ValueError(f"{label} must be bytes")
    raw = bytes(value)
    if len(raw) != DECODED_RGB_LENGTH:
        raise ValueError(f"{label} must be exactly {DECODED_RGB_LENGTH} bytes")
    return raw


def quantize_rgb(reference_rgb: object, mode: int) -> bytes:
    """Apply the closed nearest-level quantizer with error bounded by mode."""
    raw = _rgb(reference_rgb)
    if type(mode) is not int or mode not in QRGB_MODES:
        raise ValueError("qRGB mode must be one of 0, 1, 2, or 3")
    if mode == 0:
        return raw
    step = 2 * mode + 1
    return bytes(((value + mode) // step) * step for value in raw)


@dataclass(frozen=True)
class DecodedQrgb:
    rgb: bytes
    mode: int
    payload_sha256: str
    payload_length: int


def _bounded_payload(source: bytes) -> bytes:
    if not isinstance(source, bytes):
        raise ValueError("qRGB payload must be immutable bytes")
    if len(source) > MAX_QRGB_PAYLOAD_BYTES:
        raise ValueError("qRGB payload exceeds its encoded-size cap")
    return source


def _parse_qrgb_header(payload: bytes) -> tuple[int, int, bytes]:
    if len(payload) < QRGB_HEADER.size:
        raise ValueError("qRGB payload is shorter than its fixed header")
    (magic, width, height, channels, color_space, mode, max_error,
     decoded_length, compressed_length, decoded_sha) = QRGB_HEADER.unpack_from(
         payload
     )
    if (magic != QRGB_MAGIC or width != CELL_WIDTH or height != CELL_HEIGHT
            or channels != CELL_CHANNELS
            or color_space != QRGB_COLORSPACE_SRGB
            or mode not in QRGB_MODES or max_error != mode
            or decoded_length != DECODED_RGB_LENGTH):
        raise ValueError("qRGB header schema or dimensions are unsupported")
    if compressed_length <= 0:
        raise ValueError("qRGB compressed stream is empty")
    if QRGB_HEADER.size + compressed_length != len(payload):
        raise ValueError("qRGB payload is truncated or has extra bytes")
    if decoded_length > compressed_length * MAX_QRGB_EXPANSION_RATIO:
        raise ValueError("qRGB expansion ratio exceeds its fixed cap")
    return mode, compressed_length, decoded_sha


def decode_qrgb_payload(source: bytes) -> DecodedQrgb:
    """Boundedly decode one fixed-size RGB cell and verify its pixel hash."""
    payload = _bounded_payload(source)
    mode, compressed_length, expected_sha = _parse_qrgb_header(payload)
    compressed = memoryview(payload)[QRGB_HEADER.size:]
    if len(compressed) != compressed_length:
        raise ValueError("qRGB compressed length mismatch")

    decoder = zlib.decompressobj(zlib.MAX_WBITS)
    decoded = bytearray()
    try:
        for offset in range(0, len(compressed), 64 * 1024):
            pending = bytes(compressed[offset:offset + 64 * 1024])
            while pending:
                before = len(pending)
                piece = decoder.decompress(
                    pending, DECODED_RGB_LENGTH - len(decoded) + 1
                )
                decoded.extend(piece)
                if len(decoded) > DECODED_RGB_LENGTH:
                    raise ValueError("qRGB stream exceeds decoded-length cap")
                pending = decoder.unconsumed_tail
                if pending and not piece and len(pending) == before:
                    raise ValueError("qRGB decoder made no bounded progress")
                if decoder.unused_data:
                    raise ValueError("qRGB zlib stream has trailing data")
        decoded.extend(decoder.flush(DECODED_RGB_LENGTH - len(decoded) + 1))
    except zlib.error as error:
        raise ValueError(f"qRGB zlib stream is invalid: {error}") from error
    if (not decoder.eof or decoder.unused_data or decoder.unconsumed_tail
            or len(decoded) != DECODED_RGB_LENGTH):
        raise ValueError("qRGB stream has wrong decoded length or termination")
    rgb = bytes(decoded)
    if hashlib.sha256(rgb).digest() != expected_sha:
        raise ValueError("qRGB decoded RGB SHA-256 mismatch")
    return DecodedQrgb(
        rgb=rgb,
        mode=mode,
        payload_sha256=sha256_bytes(payload),
        payload_length=len(payload),
    )


def decode_qrgb(source: bytes) -> bytes:
    return decode_qrgb_payload(source).rgb


def encode_qrgb(reference_rgb: object, mode: int) -> bytes:
    """Encode one immutable candidate; encoding reproducibility is not authority."""
    decoded = quantize_rgb(reference_rgb, mode)
    compressed = zlib.compress(decoded, level=9)
    header = QRGB_HEADER.pack(
        QRGB_MAGIC,
        CELL_WIDTH,
        CELL_HEIGHT,
        CELL_CHANNELS,
        QRGB_COLORSPACE_SRGB,
        mode,
        mode,
        len(decoded),
        len(compressed),
        hashlib.sha256(decoded).digest(),
    )
    payload = header + compressed
    if len(payload) > MAX_QRGB_PAYLOAD_BYTES:
        raise ValueError("encoded qRGB candidate exceeds its fixed cap")
    if decode_qrgb(payload) != decoded:
        raise AssertionError("qRGB encoder self-check failed")
    return payload


def _critical_mask(value: object) -> bytes:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise ValueError("critical mask must be bytes")
    mask = bytes(value)
    if len(mask) != CELL_PIXEL_COUNT or any(item not in (0, 1) for item in mask):
        raise ValueError("critical mask must contain one 0/1 byte per pixel")
    return mask


def _luminance(rgb: bytes) -> bytes:
    return bytes(
        (77 * rgb[offset] + 150 * rgb[offset + 1]
         + 29 * rgb[offset + 2] + 128) >> 8
        for offset in range(0, len(rgb), 3)
    )


def _pixel_luminance(rgb: bytes, index: int) -> int:
    offset = index * 3
    return (
        77 * rgb[offset] + 150 * rgb[offset + 1]
        + 29 * rgb[offset + 2] + 128
    ) >> 8


def _region_ssim_ppm(
        reference: bytes, candidate: bytes, width: int,
        x0: int, y0: int, x1: int, y1: int,
) -> int:
    count = (x1 - x0) * (y1 - y0)
    sum_ref = 0
    sum_candidate = 0
    sum_ref_sq = 0
    sum_candidate_sq = 0
    sum_cross = 0
    for row in range(y0, y1):
        offset = row * width + x0
        for index in range(offset, offset + (x1 - x0)):
            left = reference[index]
            right = candidate[index]
            sum_ref += left
            sum_candidate += right
            sum_ref_sq += left * left
            sum_candidate_sq += right * right
            sum_cross += left * right
    count_sq = count * count
    covariance = count * sum_cross - sum_ref * sum_candidate
    numerator_luma = 20_000 * sum_ref * sum_candidate + 65_025 * count_sq
    numerator_contrast = 20_000 * covariance + 585_225 * count_sq
    denominator_luma = (
        10_000 * (sum_ref * sum_ref + sum_candidate * sum_candidate)
        + 65_025 * count_sq
    )
    denominator_contrast = (
        10_000 * (
            count * sum_ref_sq - sum_ref * sum_ref
            + count * sum_candidate_sq - sum_candidate * sum_candidate
        )
        + 585_225 * count_sq
    )
    numerator = numerator_luma * numerator_contrast
    denominator = denominator_luma * denominator_contrast
    if numerator <= 0 or denominator <= 0:
        return 0
    return min(1_000_000, numerator * 1_000_000 // denominator)


def _windowed_luma_ssim(
        reference: bytes, candidate: bytes, width: int, height: int,
) -> tuple[int, int, int]:
    side = QUALITY_POLICY["ssim_window_side"]
    scores = []
    for y0 in range(0, height, side):
        for x0 in range(0, width, side):
            scores.append(_region_ssim_ppm(
                reference,
                candidate,
                width,
                x0,
                y0,
                min(width, x0 + side),
                min(height, y0 + side),
            ))
    if not scores:
        raise ValueError("quality raster has no SSIM windows")
    return len(scores), sum(scores) // len(scores), min(scores)


def _psnr_millidb(sum_squared_error: int, samples: int) -> int:
    if sum_squared_error == 0:
        return 999_999
    with localcontext() as context:
        context.prec = 50
        ratio = Decimal(255 * 255) * Decimal(samples) / Decimal(sum_squared_error)
        decibels = Decimal(10) * (ratio.ln() / Decimal(10).ln())
        return max(0, int(decibels * 1000))


def _edge_direction(
        luma: bytes, index: int, width: int, height: int,
) -> int:
    row, column = divmod(index, width)
    if width == 1:
        dx = 0
    elif column == 0:
        dx = luma[index + 1] - luma[index]
    elif column + 1 == width:
        dx = luma[index] - luma[index - 1]
    else:
        dx = luma[index + 1] - luma[index - 1]
    if height == 1:
        dy = 0
    elif row == 0:
        dy = luma[index + width] - luma[index]
    elif row + 1 == height:
        dy = luma[index] - luma[index - width]
    else:
        dy = luma[index + width] - luma[index - width]
    ax = abs(dx)
    ay = abs(dy)
    if max(ax, ay) < QUALITY_POLICY["edge_threshold_luma8"]:
        return 0
    if ax >= 2 * ay:
        return 1 if dx >= 0 else 2
    if ay >= 2 * ax:
        return 3 if dy >= 0 else 4
    if dx >= 0 and dy >= 0:
        return 5
    if dx < 0 <= dy:
        return 6
    if dx < 0 and dy < 0:
        return 7
    return 8


def _component_metrics(mask: bytearray, width: int, height: int) -> tuple[int, int]:
    pending = bytearray(mask)
    components = 0
    largest = 0
    for start in range(width * height):
        if not pending[start]:
            continue
        components += 1
        pending[start] = 0
        stack = [start]
        size = 0
        while stack:
            index = stack.pop()
            size += 1
            row, column = divmod(index, width)
            for delta_y in (-1, 0, 1):
                neighbour_y = row + delta_y
                if not 0 <= neighbour_y < height:
                    continue
                for delta_x in (-1, 0, 1):
                    if not delta_x and not delta_y:
                        continue
                    neighbour_x = column + delta_x
                    if not 0 <= neighbour_x < width:
                        continue
                    neighbour = neighbour_y * width + neighbour_x
                    if pending[neighbour]:
                        pending[neighbour] = 0
                        stack.append(neighbour)
        largest = max(largest, size)
    return components, largest


def _edge_metrics(
        reference_luma: bytes, candidate_luma: bytes, mask: bytes,
        width: int = CELL_WIDTH, height: int = CELL_HEIGHT,
) -> dict[str, int]:
    pixels = width * height
    if (len(reference_luma) != pixels or len(candidate_luma) != pixels
            or len(mask) != pixels):
        raise ValueError("edge metric raster dimensions mismatch")
    compared = 0
    matches = 0
    introduced = bytearray(pixels)
    removed = bytearray(pixels)
    direction_changed = bytearray(pixels)
    changed = bytearray(pixels)
    border_changes = 0
    for index in range(pixels):
        reference_direction = _edge_direction(reference_luma, index, width, height)
        candidate_direction = _edge_direction(candidate_luma, index, width, height)
        if reference_direction or candidate_direction or mask[index]:
            compared += 1
            if reference_direction == candidate_direction:
                matches += 1
        is_changed = False
        if candidate_direction and not reference_direction:
            introduced[index] = 1
            is_changed = True
        elif reference_direction and not candidate_direction:
            removed[index] = 1
            is_changed = True
        elif (reference_direction and candidate_direction
              and reference_direction != candidate_direction):
            direction_changed[index] = 1
            is_changed = True
        if is_changed:
            changed[index] = 1
            row, column = divmod(index, width)
            if row in (0, height - 1) or column in (0, width - 1):
                border_changes += 1
    agreement = 1_000_000 if not compared else matches * 1_000_000 // compared
    result = {
        "edge_domain_pixels": pixels,
        "edge_compared_pixels": compared,
        "edge_direction_matches": matches,
        "edge_direction_agreement_ppm": agreement,
        "introduced_edge_pixels": sum(introduced),
        "removed_edge_pixels": sum(removed),
        "direction_changed_edge_pixels": sum(direction_changed),
        "edge_change_pixels": sum(changed),
        "border_edge_change_pixels": border_changes,
    }
    for name, bitmap in (
        ("introduced", introduced),
        ("removed", removed),
        ("direction_changed", direction_changed),
    ):
        components, largest = _component_metrics(bitmap, width, height)
        result[f"{name}_edge_component_count"] = components
        result[f"max_connected_{name}_edge_pixels"] = largest
    components, largest = _component_metrics(changed, width, height)
    result["edge_change_component_count"] = components
    result["max_connected_edge_change_pixels"] = largest
    return result


QUALITY_METRIC_KEYS = {
    "max_abs_channel_error", "sum_abs_channel_error", "sum_squared_error",
    "psnr_millidb", "ssim_window_count", "mean_window_luma_ssim_ppm",
    "minimum_window_luma_ssim_ppm", "edge_domain_pixels",
    "edge_compared_pixels", "edge_direction_matches",
    "edge_direction_agreement_ppm", "introduced_edge_pixels",
    "removed_edge_pixels", "direction_changed_edge_pixels",
    "edge_change_pixels", "border_edge_change_pixels",
    "introduced_edge_component_count", "max_connected_introduced_edge_pixels",
    "removed_edge_component_count", "max_connected_removed_edge_pixels",
    "direction_changed_edge_component_count",
    "max_connected_direction_changed_edge_pixels",
    "edge_change_component_count", "max_connected_edge_change_pixels",
}


def _record_execution(
        counts: dict[str, int] | None, name: str, amount: int = 1,
) -> None:
    """Add one exact completed-work counter when replay requests accounting."""
    if counts is not None:
        counts[name] = counts.get(name, 0) + amount


def measure_raster_quality(
        reference_rgb: bytes, candidate_rgb: bytes, width: int, height: int,
        critical_mask: bytes | None = None, *,
        _execution_counts: dict[str, int] | None = None,
        _execution_scope: str | None = None,
) -> dict[str, int]:
    pixels = _integer(width, "quality width", 1) * _integer(
        height, "quality height", 1
    )
    expected = pixels * 3
    if (not isinstance(reference_rgb, bytes) or not isinstance(candidate_rgb, bytes)
            or len(reference_rgb) != expected or len(candidate_rgb) != expected):
        raise ValueError("quality RGB raster length mismatch")
    if critical_mask is None:
        mask = bytes(pixels)
    elif (not isinstance(critical_mask, bytes)
          or len(critical_mask) != pixels
          or any(value not in (0, 1) for value in critical_mask)):
        raise ValueError("quality critical mask dimensions or values mismatch")
    else:
        mask = critical_mask
    max_error = 0
    sum_abs = 0
    sum_squared = 0
    for left, right in zip(reference_rgb, candidate_rgb):
        difference = abs(left - right)
        max_error = max(max_error, difference)
        sum_abs += difference
        sum_squared += difference * difference
    reference_luma = _luminance(reference_rgb)
    candidate_luma = _luminance(candidate_rgb)
    window_count, mean_ssim, minimum_ssim = _windowed_luma_ssim(
        reference_luma, candidate_luma, width, height
    )
    edge_metrics = _edge_metrics(
        reference_luma, candidate_luma, mask, width, height
    )
    if _execution_scope is not None:
        if _execution_scope not in {"cell", "frame"}:
            raise ValueError("quality execution scope is unsupported")
        prefix = f"{_execution_scope}_quality"
        _record_execution(_execution_counts, f"{prefix}_scans")
        _record_execution(
            _execution_counts, f"{prefix}_channel_samples", expected
        )
        _record_execution(
            _execution_counts, f"{prefix}_windows", window_count
        )
        _record_execution(
            _execution_counts, f"{prefix}_edge_pixels_scanned", pixels
        )
    return {
        "max_abs_channel_error": max_error,
        "sum_abs_channel_error": sum_abs,
        "sum_squared_error": sum_squared,
        "psnr_millidb": _psnr_millidb(sum_squared, expected),
        "ssim_window_count": window_count,
        "mean_window_luma_ssim_ppm": mean_ssim,
        "minimum_window_luma_ssim_ppm": minimum_ssim,
        **edge_metrics,
    }


def validate_quality_metrics(
        metrics: Mapping[str, object], width: int, height: int,
        label: str = "quality metrics",
) -> Mapping[str, object]:
    """Validate bounded counters and exact internal metric accounting."""
    if not isinstance(metrics, Mapping) or set(metrics) != QUALITY_METRIC_KEYS:
        raise ValueError(f"{label} schema mismatch")
    pixels = _integer(width, f"{label} width", 1) * _integer(
        height, f"{label} height", 1
    )
    samples = pixels * 3
    bounds = {
        "max_abs_channel_error": 255,
        "sum_abs_channel_error": samples * 255,
        "sum_squared_error": samples * 255 * 255,
        "psnr_millidb": 999_999,
        "ssim_window_count": pixels,
        "mean_window_luma_ssim_ppm": 1_000_000,
        "minimum_window_luma_ssim_ppm": 1_000_000,
        "edge_domain_pixels": pixels,
        "edge_compared_pixels": pixels,
        "edge_direction_matches": pixels,
        "edge_direction_agreement_ppm": 1_000_000,
        "introduced_edge_pixels": pixels,
        "removed_edge_pixels": pixels,
        "direction_changed_edge_pixels": pixels,
        "edge_change_pixels": pixels,
        "border_edge_change_pixels": pixels,
        "introduced_edge_component_count": pixels,
        "max_connected_introduced_edge_pixels": pixels,
        "removed_edge_component_count": pixels,
        "max_connected_removed_edge_pixels": pixels,
        "direction_changed_edge_component_count": pixels,
        "max_connected_direction_changed_edge_pixels": pixels,
        "edge_change_component_count": pixels,
        "max_connected_edge_change_pixels": pixels,
    }
    for field, maximum in bounds.items():
        _integer(metrics.get(field), f"{label} {field}", 0, maximum)
    side = QUALITY_POLICY["ssim_window_side"]
    expected_windows = ((width + side - 1) // side) * (
        (height + side - 1) // side
    )
    if (metrics["ssim_window_count"] != expected_windows
            or metrics["edge_domain_pixels"] != pixels
            or metrics["edge_direction_matches"]
            > metrics["edge_compared_pixels"]
            or metrics["edge_change_pixels"] != (
                metrics["introduced_edge_pixels"]
                + metrics["removed_edge_pixels"]
                + metrics["direction_changed_edge_pixels"]
            )):
        raise ValueError(f"{label} raster or edge accounting is inconsistent")
    for name in ("introduced", "removed", "direction_changed"):
        total = metrics[f"{name}_edge_pixels"]
        components = metrics[f"{name}_edge_component_count"]
        largest = metrics[f"max_connected_{name}_edge_pixels"]
        if (components > total or largest > total
                or (total == 0) != (components == 0)
                or (total == 0) != (largest == 0)):
            raise ValueError(f"{label} {name} component accounting is inconsistent")
    if (metrics["edge_change_component_count"] > metrics["edge_change_pixels"]
            or metrics["max_connected_edge_change_pixels"]
            > metrics["edge_change_pixels"]
            or (metrics["edge_change_pixels"] == 0)
            != (metrics["edge_change_component_count"] == 0)
            or (metrics["edge_change_pixels"] == 0)
            != (metrics["max_connected_edge_change_pixels"] == 0)):
        raise ValueError(f"{label} combined component accounting is inconsistent")
    compared = metrics["edge_compared_pixels"]
    agreement = (
        1_000_000 if compared == 0
        else metrics["edge_direction_matches"] * 1_000_000 // compared
    )
    if metrics["edge_direction_agreement_ppm"] != agreement:
        raise ValueError(f"{label} edge agreement is inconsistent")
    return metrics


def quality_metrics_pass(
        metrics: Mapping[str, object], max_error: int, sample_count: int,
) -> bool:
    policy = QUALITY_POLICY
    compared = metrics["edge_compared_pixels"]
    matches = metrics["edge_direction_matches"]
    connected_cap = policy["max_connected_edge_change_pixels"]
    return bool(
        max_error in policy["allowed_max_abs_channel_error"]
        and metrics["max_abs_channel_error"] <= max_error
        and metrics["sum_abs_channel_error"] * 1000
        <= sample_count * policy["max_mean_abs_error_milli"]
        and metrics["sum_squared_error"] * 10
        <= sample_count * policy["max_squared_error_tenths_per_sample"]
        and metrics["psnr_millidb"] >= policy["min_rgb_psnr_millidb"]
        and metrics["mean_window_luma_ssim_ppm"]
        >= policy["min_mean_window_luma_ssim_ppm"]
        and metrics["minimum_window_luma_ssim_ppm"]
        >= policy["min_minimum_window_luma_ssim_ppm"]
        and (not compared or matches * 1_000_000
             >= compared * policy["min_edge_direction_agreement_ppm"])
        and metrics["max_connected_introduced_edge_pixels"] <= connected_cap
        and metrics["max_connected_removed_edge_pixels"] <= connected_cap
        and metrics["max_connected_direction_changed_edge_pixels"]
        <= connected_cap
        and metrics["max_connected_edge_change_pixels"] <= connected_cap
        and metrics["border_edge_change_pixels"]
        <= policy["max_border_edge_change_pixels"]
    )


_QUALITY_RECEIPT_KEYS = {
    "version", "kind", "policy_sha256", "encoder_version", "mode",
    "width", "height", "color_space", "orientation", "alpha",
    "reference_rgb_sha256", "candidate_rgb_sha256", "critical_mask_sha256",
    "pixel_count", "channel_sample_count", *QUALITY_METRIC_KEYS,
    "passed", "receipt_sha256",
}


def build_quality_receipt(
        reference_rgb: object, candidate_rgb: object, critical_mask: object,
        mode: int, *, _execution_counts: dict[str, int] | None = None,
) -> dict[str, object]:
    """Compute and self-hash all quality claims from exact supplied bytes."""
    reference = _rgb(reference_rgb, "quality reference RGB")
    candidate = _rgb(candidate_rgb, "quality candidate RGB")
    mask = _critical_mask(critical_mask)
    if type(mode) is not int or mode not in QRGB_MODES:
        raise ValueError("quality receipt mode is unsupported")
    metrics = measure_raster_quality(
        reference,
        candidate,
        CELL_WIDTH,
        CELL_HEIGHT,
        mask,
        _execution_counts=_execution_counts,
        _execution_scope="cell" if _execution_counts is not None else None,
    )
    body: dict[str, object] = {
        "version": 2,
        "kind": QUALITY_RECEIPT_KIND,
        "policy_sha256": QUALITY_POLICY_SHA256,
        "encoder_version": QRGB_ENCODER_VERSION,
        "mode": mode,
        "width": CELL_WIDTH,
        "height": CELL_HEIGHT,
        "color_space": "srgb-rgb8",
        "orientation": "top-left-row-major",
        "alpha": "none",
        "reference_rgb_sha256": sha256_bytes(reference),
        "candidate_rgb_sha256": sha256_bytes(candidate),
        "critical_mask_sha256": sha256_bytes(mask),
        "pixel_count": CELL_PIXEL_COUNT,
        "channel_sample_count": DECODED_RGB_LENGTH,
        **metrics,
        "passed": quality_metrics_pass(metrics, mode, DECODED_RGB_LENGTH),
    }
    body["receipt_sha256"] = sha256_json(body)
    return body


def validate_quality_receipt(
        value: object, *, reference_rgb: object | None = None,
        candidate_rgb: object | None = None, critical_mask: object | None = None,
        require_passed: bool = True,
        _execution_counts: dict[str, int] | None = None,
) -> dict[str, object]:
    receipt, _raw = _canonical_document(value, "quality receipt")
    _closed(receipt, _QUALITY_RECEIPT_KEYS, "quality receipt")
    if (receipt.get("version") != 2
            or receipt.get("kind") != QUALITY_RECEIPT_KIND
            or receipt.get("policy_sha256") != QUALITY_POLICY_SHA256
            or receipt.get("encoder_version") != QRGB_ENCODER_VERSION
            or receipt.get("width") != CELL_WIDTH
            or receipt.get("height") != CELL_HEIGHT
            or receipt.get("color_space") != "srgb-rgb8"
            or receipt.get("orientation") != "top-left-row-major"
            or receipt.get("alpha") != "none"):
        raise ValueError("quality receipt policy or raster identity mismatch")
    mode = _integer(receipt.get("mode"), "quality mode")
    if mode not in QRGB_MODES:
        raise ValueError("quality receipt mode is unsupported")
    for field in (
        "reference_rgb_sha256", "candidate_rgb_sha256",
        "critical_mask_sha256", "receipt_sha256",
    ):
        _sha256(receipt.get(field), f"quality receipt {field}")
    if (receipt.get("pixel_count") != CELL_PIXEL_COUNT
            or receipt.get("channel_sample_count") != DECODED_RGB_LENGTH):
        raise ValueError("quality receipt raster counts mismatch")
    validate_quality_metrics(
        {field: receipt[field] for field in QUALITY_METRIC_KEYS},
        CELL_WIDTH,
        CELL_HEIGHT,
        "quality receipt",
    )
    passed = quality_metrics_pass(receipt, mode, DECODED_RGB_LENGTH)
    if type(receipt.get("passed")) is not bool or receipt["passed"] != passed:
        raise ValueError("quality receipt pass result is inconsistent")
    body = dict(receipt)
    claimed = body.pop("receipt_sha256")
    if claimed != sha256_json(body):
        raise ValueError("quality receipt self-hash mismatch")
    supplied = (reference_rgb, candidate_rgb, critical_mask)
    if any(item is not None for item in supplied):
        if any(item is None for item in supplied):
            raise ValueError("quality receipt recomputation inputs are partial")
        expected = build_quality_receipt(
            reference_rgb,
            candidate_rgb,
            critical_mask,
            mode,
            _execution_counts=_execution_counts,
        )
        if receipt != expected:
            raise ValueError("quality receipt differs from recomputed metrics")
    if require_passed and not receipt["passed"]:
        raise ValueError("quality receipt did not pass the hashed policy")
    return receipt


_SOURCE_KEYS = {
    "version", "kind", "request", "request_identity_sha256",
    "retrieved_at", "response", "conversion", "bounds_um",
    "clean_source_assertion_ref",
}
_SOURCE_REQUEST_KEYS = {
    "provider", "product", "layer", "capture_id", "capture_version",
}
_SOURCE_RESPONSE_KEYS = {
    "media_type", "width", "height", "orientation", "alpha",
    "color_profile", "response_bytes_ref", "response_byte_length",
}
_SOURCE_CONVERSION_KEYS = {
    "decoder", "decoder_version", "output_media_type", "output_width",
    "output_height", "output_color_space", "output_orientation",
    "output_alpha", "output_color_profile", "rgb_bytes_ref",
    "rgb_byte_length",
}
_CLEAN_SOURCE_ASSERTION_KEYS = {
    "version", "kind", "request_identity_sha256", "response_bytes_ref",
    "rgb_bytes_ref", "assertion", "review_scope",
}


def _source_identity(value: object, label: str) -> str:
    if not isinstance(value, str) or _SOURCE_ID_RE.fullmatch(value) is None:
        raise ValueError(f"{label} is noncanonical")
    return value


def _expected_clean_source_assertion(
        descriptor: Mapping[str, object],
) -> dict[str, object]:
    response = descriptor["response"]
    conversion = descriptor["conversion"]
    return {
        "version": 1,
        "kind": CLEAN_SOURCE_ASSERTION_KIND,
        "request_identity_sha256": descriptor["request_identity_sha256"],
        "response_bytes_ref": response["response_bytes_ref"],
        "rgb_bytes_ref": conversion["rgb_bytes_ref"],
        "assertion": CLEAN_SOURCE_ASSERTION,
        "review_scope": CLEAN_SOURCE_REVIEW_SCOPE,
    }


def validate_source_descriptor(
        value: object, response_bytes: bytes | None = None,
        converted_rgb_bytes: bytes | None = None,
) -> dict[str, object]:
    """Validate complete offline response and fixed-conversion lineage.

    This proves byte consistency only. Real-world origin requires a separately
    pinned acquisition approval; candidate-supplied source metadata is never
    acquisition authority.
    """
    descriptor, _raw = _canonical_document(value, "offline source descriptor")
    _closed(descriptor, _SOURCE_KEYS, "offline source descriptor")
    if (descriptor.get("version") != 2 or descriptor.get("kind") != SOURCE_KIND):
        raise ValueError("offline source descriptor identity mismatch")

    request = _closed(
        descriptor.get("request"), _SOURCE_REQUEST_KEYS,
        "offline source request identity",
    )
    provider = request.get("provider")
    if not isinstance(provider, str) or _PROVIDER_RE.fullmatch(provider) is None:
        raise ValueError("offline source provider is noncanonical")
    for field_name in ("product", "layer", "capture_id", "capture_version"):
        _source_identity(
            request.get(field_name), f"offline source request {field_name}"
        )
    request_hash = sha256_json(request)
    if descriptor.get("request_identity_sha256") != request_hash:
        raise ValueError("offline source request identity hash mismatch")
    _utc(descriptor.get("retrieved_at"), "offline source retrieval time")

    response = _closed(
        descriptor.get("response"), _SOURCE_RESPONSE_KEYS,
        "offline source response",
    )
    width = _integer(response.get("width"), "offline source width", 1, MAX_SOURCE_SIDE)
    height = _integer(
        response.get("height"), "offline source height", 1, MAX_SOURCE_SIDE
    )
    if width * height > MAX_SOURCE_PIXELS:
        raise ValueError("offline source pixel count exceeds its cap")
    response_length = width * height * 3
    if (response.get("media_type") != SOURCE_MEDIA_TYPE
            or response.get("orientation") != SOURCE_ORIENTATION
            or response.get("alpha") != SOURCE_ALPHA
            or response.get("color_profile") != SOURCE_COLOR_PROFILE
            or response.get("response_byte_length") != response_length
            or response_length > MAX_SOURCE_RESPONSE_BYTES):
        raise ValueError("offline source response raster contract mismatch")
    response_ref = _sha256(
        response.get("response_bytes_ref"), "offline source response bytes"
    )

    conversion = _closed(
        descriptor.get("conversion"), _SOURCE_CONVERSION_KEYS,
        "offline source conversion",
    )
    if (conversion.get("decoder") != SOURCE_DECODER
            or conversion.get("decoder_version") != SOURCE_DECODER_VERSION
            or conversion.get("output_media_type") != SOURCE_MEDIA_TYPE
            or conversion.get("output_width") != width
            or conversion.get("output_height") != height
            or conversion.get("output_color_space") != "srgb-rgb8"
            or conversion.get("output_orientation") != SOURCE_ORIENTATION
            or conversion.get("output_alpha") != SOURCE_ALPHA
            or conversion.get("output_color_profile") != SOURCE_COLOR_PROFILE
            or conversion.get("rgb_byte_length") != response_length):
        raise ValueError("offline source fixed conversion contract mismatch")
    rgb_ref = _sha256(conversion.get("rgb_bytes_ref"), "offline source RGB bytes")
    if rgb_ref != response_ref:
        raise ValueError("raw RGB decoder output must exactly equal response bytes")

    bounds = validate_viewport(descriptor.get("bounds_um"))
    if list(bounds) != descriptor.get("bounds_um"):
        raise ValueError("offline source bounds are noncanonical")
    expected_assertion = _expected_clean_source_assertion(descriptor)
    if descriptor.get("clean_source_assertion_ref") != sha256_json(
            expected_assertion):
        raise ValueError("offline source clean-unoverlaid assertion binding mismatch")

    if response_bytes is not None:
        if (not isinstance(response_bytes, bytes)
                or len(response_bytes) != response_length
                or sha256_bytes(response_bytes) != response_ref):
            raise ValueError("offline source exact response bytes do not match descriptor")
    if converted_rgb_bytes is None and response_ref == rgb_ref:
        converted_rgb_bytes = response_bytes
    if converted_rgb_bytes is not None:
        if (not isinstance(converted_rgb_bytes, bytes)
                or len(converted_rgb_bytes) != response_length
                or sha256_bytes(converted_rgb_bytes) != rgb_ref):
            raise ValueError("offline source exact RGB bytes do not match conversion")
        if response_bytes is not None and converted_rgb_bytes != response_bytes:
            raise ValueError("raw RGB decoder did not preserve exact response bytes")
    return descriptor


def build_clean_source_assertion(
        source_descriptor: object,
) -> dict[str, object]:
    source = validate_source_descriptor(source_descriptor)
    return _expected_clean_source_assertion(source)


def _validate_clean_source_assertion_document(
        assertion: object, validated_source: Mapping[str, object],
) -> dict[str, object]:
    """Validate an assertion against one already validated immutable source."""
    exact = _closed(
        assertion, _CLEAN_SOURCE_ASSERTION_KEYS, "clean source assertion"
    )
    if exact != _expected_clean_source_assertion(validated_source):
        raise ValueError("clean source assertion differs from source lineage")
    return exact


def validate_clean_source_assertion(
        value: object, source_descriptor: object,
) -> dict[str, object]:
    assertion, _raw = _canonical_document(value, "clean source assertion")
    source = validate_source_descriptor(source_descriptor)
    return _validate_clean_source_assertion_document(assertion, source)


def build_source_descriptor(
        source_bytes: bytes, *, provider: str, product: str, layer: str,
        capture_id: str, capture_version: str, retrieved_at: str,
        width: int, height: int, bounds_um: list[int],
) -> dict[str, object]:
    request = {
        "provider": provider,
        "product": product,
        "layer": layer,
        "capture_id": capture_id,
        "capture_version": capture_version,
    }
    response_ref = sha256_bytes(source_bytes)
    descriptor: dict[str, object] = {
        "version": 2,
        "kind": SOURCE_KIND,
        "request": request,
        "request_identity_sha256": sha256_json(request),
        "retrieved_at": retrieved_at,
        "response": {
            "media_type": SOURCE_MEDIA_TYPE,
            "width": width,
            "height": height,
            "orientation": SOURCE_ORIENTATION,
            "alpha": SOURCE_ALPHA,
            "color_profile": SOURCE_COLOR_PROFILE,
            "response_bytes_ref": response_ref,
            "response_byte_length": len(source_bytes),
        },
        "conversion": {
            "decoder": SOURCE_DECODER,
            "decoder_version": SOURCE_DECODER_VERSION,
            "output_media_type": SOURCE_MEDIA_TYPE,
            "output_width": width,
            "output_height": height,
            "output_color_space": "srgb-rgb8",
            "output_orientation": SOURCE_ORIENTATION,
            "output_alpha": SOURCE_ALPHA,
            "output_color_profile": SOURCE_COLOR_PROFILE,
            "rgb_bytes_ref": response_ref,
            "rgb_byte_length": len(source_bytes),
        },
        "bounds_um": bounds_um,
        "clean_source_assertion_ref": "0" * 64,
    }
    descriptor["clean_source_assertion_ref"] = sha256_json(
        _expected_clean_source_assertion(descriptor)
    )
    return validate_source_descriptor(descriptor, source_bytes, source_bytes)


def _sample_coordinate(
        world_numerator: int, world_denominator: int, source_origin: int,
        source_span: int, source_pixels: int,
) -> tuple[int, int]:
    numerator = (
        2 * (world_numerator - source_origin * world_denominator) * source_pixels
        - world_denominator * source_span
    )
    denominator = 2 * world_denominator * source_span
    index, remainder = divmod(numerator, denominator)
    weight = (remainder * FIXED_ONE + denominator // 2) // denominator
    if weight == FIXED_ONE:
        return index + 1, 0
    return index, weight


def derive_reference_cell(
        source_descriptor: object, source_bytes: bytes,
        matrix: str, x: int, y: int, *,
        _execution_counts: dict[str, int] | None = None,
) -> bytes:
    """Deterministically transform a rooted raw source into one clean cell."""
    source = validate_source_descriptor(source_descriptor, source_bytes)
    return _derive_reference_cell_from_validated_source(
        source,
        source_bytes,
        matrix,
        x,
        y,
        _execution_counts=_execution_counts,
    )


def _derive_reference_cell_from_validated_source(
        source: Mapping[str, object], source_bytes: bytes,
        matrix: str, x: int, y: int, *,
        _execution_counts: dict[str, int] | None = None,
) -> bytes:
    destination = grid_cell_bounds(matrix, x, y)
    sx0, sy0, sx1, sy1 = source["bounds_um"]
    dx0, dy0, dx1, dy1 = destination
    if not (sx0 <= dx0 < dx1 <= sx1 and sy0 <= dy0 < dy1 <= sy1):
        raise ValueError("offline source does not contain the destination cell")
    conversion = source["conversion"]
    width = conversion["output_width"]
    height = conversion["output_height"]
    if (width == CELL_WIDTH and height == CELL_HEIGHT
            and tuple(source["bounds_um"]) == destination):
        _record_execution(
            _execution_counts, "source_transform_identity_copies"
        )
        _record_execution(
            _execution_counts, "source_transform_output_pixels",
            CELL_PIXEL_COUNT,
        )
        return bytes(source_bytes)

    x_denominator = 2 * CELL_WIDTH
    y_denominator = 2 * CELL_HEIGHT
    x_samples = []
    for column in range(CELL_WIDTH):
        world = dx0 * x_denominator + (2 * column + 1) * (dx1 - dx0)
        index, weight = _sample_coordinate(
            world, x_denominator, sx0, sx1 - sx0, width
        )
        x_samples.append((
            min(max(index, 0), width - 1),
            min(max(index + 1, 0), width - 1),
            weight,
        ))
    y_samples = []
    for row in range(CELL_HEIGHT):
        world = dy1 * y_denominator - (2 * row + 1) * (dy1 - dy0)
        # Source rows run top-to-bottom, hence the north-bound origin.
        index, weight = _sample_coordinate(
            sy1 * y_denominator - world,
            y_denominator,
            0,
            sy1 - sy0,
            height,
        )
        y_samples.append((
            min(max(index, 0), height - 1),
            min(max(index + 1, 0), height - 1),
            weight,
        ))
    result = bytearray(DECODED_RGB_LENGTH)
    fixed_squared = FIXED_ONE * FIXED_ONE
    output_offset = 0
    for top_y, bottom_y, weight_y in y_samples:
        inverse_y = FIXED_ONE - weight_y
        for left_x, right_x, weight_x in x_samples:
            inverse_x = FIXED_ONE - weight_x
            offsets = (
                (top_y * width + left_x) * 3,
                (top_y * width + right_x) * 3,
                (bottom_y * width + left_x) * 3,
                (bottom_y * width + right_x) * 3,
            )
            for channel in range(3):
                total = (
                    source_bytes[offsets[0] + channel] * inverse_x * inverse_y
                    + source_bytes[offsets[1] + channel] * weight_x * inverse_y
                    + source_bytes[offsets[2] + channel] * inverse_x * weight_y
                    + source_bytes[offsets[3] + channel] * weight_x * weight_y
                )
                result[output_offset] = (
                    total + fixed_squared // 2
                ) // fixed_squared
                output_offset += 1
    _record_execution(_execution_counts, "source_transform_bilinear_scans")
    _record_execution(
        _execution_counts, "source_transform_taps", CELL_PIXEL_COUNT * 4
    )
    _record_execution(
        _execution_counts, "source_transform_output_pixels", CELL_PIXEL_COUNT
    )
    return bytes(result)


_SOURCE_TRANSFORM_KEYS = {
    "version", "kind", "policy_sha256", "source_descriptor_ref",
    "source_response_bytes_ref", "source_rgb_bytes_ref",
    "clean_source_assertion_ref", "decoder", "decoder_version", "matrix",
    "x", "y", "destination_bounds_um", "resampler",
    "reference_rgb_sha256", "output_width", "output_height",
    "sample_tap_bound", "receipt_sha256",
}


def build_source_transform_receipt(
        source_descriptor: object, source_bytes: bytes,
        matrix: str, x: int, y: int, *,
        _execution_counts: dict[str, int] | None = None,
) -> tuple[bytes, dict[str, object]]:
    source = validate_source_descriptor(source_descriptor, source_bytes)
    return _build_source_transform_receipt_from_validated_source(
        source,
        source_bytes,
        matrix,
        x,
        y,
        _execution_counts=_execution_counts,
    )


def _build_source_transform_receipt_from_validated_source(
        source: Mapping[str, object], source_bytes: bytes,
        matrix: str, x: int, y: int, *,
        _execution_counts: dict[str, int] | None = None,
) -> tuple[bytes, dict[str, object]]:
    reference = _derive_reference_cell_from_validated_source(
        source,
        source_bytes,
        matrix,
        x,
        y,
        _execution_counts=_execution_counts,
    )
    body: dict[str, object] = {
        "version": 1,
        "kind": SOURCE_TRANSFORM_KIND,
        "policy_sha256": QUALITY_POLICY_SHA256,
        "source_descriptor_ref": sha256_json(source),
        "source_response_bytes_ref": source["response"]["response_bytes_ref"],
        "source_rgb_bytes_ref": source["conversion"]["rgb_bytes_ref"],
        "clean_source_assertion_ref": source["clean_source_assertion_ref"],
        "decoder": SOURCE_DECODER,
        "decoder_version": SOURCE_DECODER_VERSION,
        "matrix": matrix,
        "x": x,
        "y": y,
        "destination_bounds_um": list(grid_cell_bounds(matrix, x, y)),
        "resampler": SOURCE_TRANSFORM,
        "reference_rgb_sha256": sha256_bytes(reference),
        "output_width": CELL_WIDTH,
        "output_height": CELL_HEIGHT,
        "sample_tap_bound": CELL_PIXEL_COUNT * 4,
    }
    body["receipt_sha256"] = sha256_json(body)
    return reference, body


def _validate_source_transform_receipt_from_validated_source(
        receipt: object, source: Mapping[str, object], source_bytes: bytes,
        reference_rgb: bytes, *,
        _execution_counts: dict[str, int] | None = None,
) -> dict[str, object]:
    """Validate a transform against one prepared immutable source product."""
    exact = _closed(
        receipt, _SOURCE_TRANSFORM_KEYS, "source transform receipt"
    )
    if (exact.get("version") != 1
            or exact.get("kind") != SOURCE_TRANSFORM_KIND
            or exact.get("policy_sha256") != QUALITY_POLICY_SHA256
            or exact.get("source_descriptor_ref") != sha256_json(source)
            or exact.get("source_response_bytes_ref")
            != source["response"]["response_bytes_ref"]
            or exact.get("source_rgb_bytes_ref")
            != source["conversion"]["rgb_bytes_ref"]
            or exact.get("clean_source_assertion_ref")
            != source["clean_source_assertion_ref"]
            or exact.get("decoder") != SOURCE_DECODER
            or exact.get("decoder_version") != SOURCE_DECODER_VERSION
            or exact.get("resampler") != SOURCE_TRANSFORM
            or exact.get("output_width") != CELL_WIDTH
            or exact.get("output_height") != CELL_HEIGHT
            or exact.get("sample_tap_bound") != CELL_PIXEL_COUNT * 4):
        raise ValueError("source transform contract mismatch")
    matrix = exact.get("matrix")
    x = _integer(exact.get("x"), "source transform grid x")
    y = _integer(exact.get("y"), "source transform grid y")
    if (exact.get("destination_bounds_um")
            != list(grid_cell_bounds(matrix, x, y))):
        raise ValueError("source transform destination bounds mismatch")
    reference = _rgb(reference_rgb, "source transform reference RGB")
    if exact.get("reference_rgb_sha256") != sha256_bytes(reference):
        raise ValueError("source transform did not bind exact reference RGB")
    body = dict(exact)
    claimed = _sha256(body.pop("receipt_sha256"), "source transform self-hash")
    if claimed != sha256_json(body):
        raise ValueError("source transform receipt self-hash mismatch")
    expected_reference, expected = (
        _build_source_transform_receipt_from_validated_source(
            source,
            source_bytes,
            matrix,
            x,
            y,
            _execution_counts=_execution_counts,
        )
    )
    if expected_reference != reference or expected != exact:
        raise ValueError("source transform receipt differs from recomputation")
    return exact


def validate_source_transform_receipt(
        value: object, source_descriptor: object, source_bytes: bytes,
        reference_rgb: bytes, *,
        _execution_counts: dict[str, int] | None = None,
) -> dict[str, object]:
    receipt, _raw = _canonical_document(value, "source transform receipt")
    source = validate_source_descriptor(source_descriptor, source_bytes)
    return _validate_source_transform_receipt_from_validated_source(
        receipt,
        source,
        source_bytes,
        reference_rgb,
        _execution_counts=_execution_counts,
    )


_CRITICAL_MASK_RECIPE_KEYS = {
    "version", "kind", "policy_sha256", "reference_rgb_ref", "derivation",
}


def build_critical_mask_recipe(reference_rgb: object) -> dict[str, object]:
    reference = _rgb(reference_rgb, "critical-mask reference RGB")
    return {
        "version": 1,
        "kind": CRITICAL_MASK_RECIPE_KIND,
        "policy_sha256": QUALITY_POLICY_SHA256,
        "reference_rgb_ref": sha256_bytes(reference),
        "derivation": CRITICAL_MASK_DERIVATION,
    }


def _validate_critical_mask_recipe_document(
        recipe: object, reference_rgb: object,
) -> dict[str, object]:
    _closed(recipe, _CRITICAL_MASK_RECIPE_KEYS, "critical-mask recipe")
    reference = _rgb(reference_rgb, "critical-mask reference RGB")
    if (recipe.get("version") != 1
            or recipe.get("kind") != CRITICAL_MASK_RECIPE_KIND
            or recipe.get("policy_sha256") != QUALITY_POLICY_SHA256
            or recipe.get("reference_rgb_ref") != sha256_bytes(reference)
            or recipe.get("derivation") != CRITICAL_MASK_DERIVATION):
        raise ValueError("critical-mask derivation contract mismatch")
    return recipe


def validate_critical_mask_recipe(
        value: object, reference_rgb: object,
) -> dict[str, object]:
    recipe, _raw = _canonical_document(value, "critical-mask recipe")
    return _validate_critical_mask_recipe_document(recipe, reference_rgb)


def derive_critical_mask(reference_rgb: object) -> bytes:
    """Derive edge halos plus every cell border from exact clean pixels."""
    reference = _rgb(reference_rgb, "critical-mask reference RGB")
    luma = _luminance(reference)
    seed = bytearray(CELL_PIXEL_COUNT)
    for index in range(CELL_PIXEL_COUNT):
        row, column = divmod(index, CELL_WIDTH)
        if (row in (0, CELL_HEIGHT - 1) or column in (0, CELL_WIDTH - 1)
                or _edge_direction(luma, index, CELL_WIDTH, CELL_HEIGHT)):
            seed[index] = 1
    result = bytearray(seed)
    for index, value in enumerate(seed):
        if not value:
            continue
        row, column = divmod(index, CELL_WIDTH)
        for delta_y in (-1, 0, 1):
            y = row + delta_y
            if not 0 <= y < CELL_HEIGHT:
                continue
            for delta_x in (-1, 0, 1):
                x = column + delta_x
                if 0 <= x < CELL_WIDTH:
                    result[y * CELL_WIDTH + x] = 1
    return bytes(result)


def _validate_critical_mask_bytes(
        mask: object, recipe: object, reference: bytes,
) -> bytes:
    _validate_critical_mask_recipe_document(recipe, reference)
    exact = _critical_mask(mask)
    if exact != derive_critical_mask(reference):
        raise ValueError("critical mask differs from deterministic derivation")
    return exact


def validate_critical_mask(
        mask: object, recipe: object, reference_rgb: object,
) -> bytes:
    reference = _rgb(reference_rgb, "critical-mask reference RGB")
    recipe_document, _raw = _canonical_document(
        recipe, "critical-mask recipe"
    )
    return _validate_critical_mask_bytes(mask, recipe_document, reference)


_ACQUISITION_KEYS = {
    "source_descriptor_ref", "clean_source_assertion_ref",
    "source_transform_receipt_ref",
}
_GRID_KEYS = {"scheme", "matrix", "x", "y", "bounds_um", "width", "height"}
_ENCODING_KEYS = {
    "codec", "max_abs_channel_error", "payload_ref", "decoded_length",
    "decoded_rgb_sha256",
}
_CELL_KEYS = {
    "version", "kind", "grid", "acquisition", "encoding",
    "reference_rgb_ref", "critical_mask_ref", "critical_mask_recipe_ref",
    "quality_receipt_ref",
}


def _validate_acquisition(value: object) -> dict[str, object]:
    acquisition = _closed(value, _ACQUISITION_KEYS, "imagery acquisition")
    _sha256(acquisition.get("source_descriptor_ref"), "source descriptor reference")
    _sha256(
        acquisition.get("clean_source_assertion_ref"),
        "clean source assertion reference",
    )
    _sha256(
        acquisition.get("source_transform_receipt_ref"),
        "source transform receipt reference",
    )
    return acquisition


def validate_imagery_cell_document(value: object) -> dict[str, object]:
    cell = _closed(value, _CELL_KEYS, "imagery cell")
    if cell.get("version") != 2 or cell.get("kind") != "parking-imagery-cell":
        raise ValueError("imagery cell version or kind is unsupported")
    grid = _closed(cell.get("grid"), _GRID_KEYS, "imagery cell grid")
    if (grid.get("scheme") != GRID_SCHEME
            or grid.get("width") != CELL_WIDTH
            or grid.get("height") != CELL_HEIGHT):
        raise ValueError("imagery cell grid identity mismatch")
    matrix = grid.get("matrix")
    if matrix not in MATRIX_PIXEL_UM:
        raise ValueError("imagery cell matrix is unsupported")
    x = _integer(grid.get("x"), "imagery cell grid x")
    y = _integer(grid.get("y"), "imagery cell grid y")
    if grid.get("bounds_um") != list(grid_cell_bounds(matrix, x, y)):
        raise ValueError("imagery cell bounds are not canonical")
    _validate_acquisition(cell.get("acquisition"))
    encoding = _closed(cell.get("encoding"), _ENCODING_KEYS, "imagery encoding")
    if (encoding.get("codec") != QRGB_CODEC
            or encoding.get("decoded_length") != DECODED_RGB_LENGTH):
        raise ValueError("imagery cell encoding identity mismatch")
    mode = _integer(encoding.get("max_abs_channel_error"), "imagery error mode")
    if mode not in QRGB_MODES:
        raise ValueError("imagery cell error mode is unsupported")
    for field in ("payload_ref", "decoded_rgb_sha256"):
        _sha256(encoding.get(field), f"imagery encoding {field}")
    for field in (
        "reference_rgb_ref", "critical_mask_ref", "critical_mask_recipe_ref",
    ):
        _sha256(cell.get(field), f"imagery cell {field}")
    receipt_ref = cell.get("quality_receipt_ref")
    if mode == 0:
        if receipt_ref is not None:
            raise ValueError("lossless imagery cell must not claim a quality receipt")
    else:
        _sha256(receipt_ref, "imagery quality receipt reference")
    return cell


def imagery_cell_id(value: object) -> str:
    return sha256_json(validate_imagery_cell_document(value))


def _validate_prepared_imagery_cell(
        value: object, payload: bytes,
        quality_receipt: object | None = None, *,
        reference_rgb: object | None = None,
        critical_mask: object | None = None,
        critical_mask_recipe: object | None = None,
        decoded_qrgb: DecodedQrgb | None = None,
        _execution_counts: dict[str, int] | None = None,
) -> bytes:
    """Replay products for one previously validated immutable cell document."""
    if not isinstance(value, dict):
        raise ValueError("prepared imagery cell must be a validated object")
    cell = value
    if (reference_rgb is None or critical_mask is None
            or critical_mask_recipe is None):
        raise ValueError("imagery cell replay requires rooted reference and mask bytes")
    reference = _rgb(reference_rgb, "imagery reference RGB")
    recipe, recipe_raw = _canonical_document(
        critical_mask_recipe, "critical-mask recipe"
    )
    mask = _validate_critical_mask_bytes(
        critical_mask, recipe, reference
    )
    if (cell["reference_rgb_ref"] != sha256_bytes(reference)
            or cell["critical_mask_ref"] != sha256_bytes(mask)
            or cell["critical_mask_recipe_ref"] != sha256_canonical_bytes(recipe_raw)):
        raise ValueError("imagery cell reference or critical-mask binding mismatch")
    if decoded_qrgb is None:
        decoded = decode_qrgb_payload(payload)
    else:
        if not isinstance(decoded_qrgb, DecodedQrgb):
            raise ValueError("predecoded qRGB product has the wrong type")
        payload_bytes = _bounded_payload(payload)
        if (sha256_bytes(payload_bytes) != decoded_qrgb.payload_sha256
                or len(payload_bytes) != decoded_qrgb.payload_length):
            raise ValueError("predecoded qRGB product does not bind payload bytes")
        decoded = decoded_qrgb
    encoding = cell["encoding"]
    if (decoded.payload_sha256 != encoding["payload_ref"]
            or decoded.mode != encoding["max_abs_channel_error"]
            or sha256_bytes(decoded.rgb) != encoding["decoded_rgb_sha256"]):
        raise ValueError("imagery cell payload or decoded identity mismatch")
    if decoded.mode == 0:
        if quality_receipt is not None:
            raise ValueError("lossless imagery cell received an extra quality receipt")
        if decoded.rgb != reference:
            raise ValueError("lossless imagery cell differs from its reference RGB")
    else:
        if quality_receipt is None:
            raise ValueError("lossy imagery cell omitted its exact quality receipt")
        receipt, receipt_raw = _canonical_document(
            quality_receipt, "quality receipt"
        )
        validate_quality_receipt(
            receipt,
            reference_rgb=reference,
            candidate_rgb=decoded.rgb,
            critical_mask=mask,
            _execution_counts=_execution_counts,
        )
        if (sha256_canonical_bytes(receipt_raw) != cell["quality_receipt_ref"]
                or receipt["candidate_rgb_sha256"]
                != encoding["decoded_rgb_sha256"]
                or receipt["mode"] != decoded.mode):
            raise ValueError("imagery cell exact quality receipt binding mismatch")
    return decoded.rgb


def validate_imagery_cell(
        value: object, payload: bytes,
        quality_receipt: object | None = None, *,
        reference_rgb: object | None = None,
        critical_mask: object | None = None,
        critical_mask_recipe: object | None = None,
        decoded_qrgb: DecodedQrgb | None = None,
        _execution_counts: dict[str, int] | None = None,
) -> bytes:
    """Defensively validate and replay one complete imagery cell."""
    cell = validate_imagery_cell_document(value)
    return _validate_prepared_imagery_cell(
        cell,
        payload,
        quality_receipt,
        reference_rgb=reference_rgb,
        critical_mask=critical_mask,
        critical_mask_recipe=critical_mask_recipe,
        decoded_qrgb=decoded_qrgb,
        _execution_counts=_execution_counts,
    )


def build_imagery_cell(
        matrix: str, x: int, y: int, acquisition: object, payload: bytes,
        quality_receipt: object | None = None, *,
        reference_rgb: object | None = None,
        critical_mask: object | None = None,
        critical_mask_recipe: object | None = None,
) -> dict[str, object]:
    """Build a cell only from replayable reference and mask authority."""
    if (reference_rgb is None or critical_mask is None
            or critical_mask_recipe is None):
        raise ValueError("imagery cell build requires rooted reference and mask bytes")
    reference = _rgb(reference_rgb, "imagery reference RGB")
    recipe, recipe_raw = _canonical_document(
        critical_mask_recipe, "critical-mask recipe"
    )
    mask = _validate_critical_mask_bytes(
        critical_mask, recipe, reference
    )
    decoded = decode_qrgb_payload(payload)
    receipt_ref = None
    if decoded.mode == 0:
        if quality_receipt is not None:
            raise ValueError("lossless cell cannot bind a quality receipt")
        if decoded.rgb != reference:
            raise ValueError("lossless cell payload differs from reference RGB")
    else:
        if quality_receipt is None:
            raise ValueError("lossy cell requires an exact quality receipt")
        receipt, receipt_raw = _canonical_document(
            quality_receipt, "quality receipt"
        )
        validate_quality_receipt(
            receipt,
            reference_rgb=reference,
            candidate_rgb=decoded.rgb,
            critical_mask=mask,
        )
        receipt_ref = sha256_canonical_bytes(receipt_raw)
    document = {
        "version": 2,
        "kind": "parking-imagery-cell",
        "grid": {
            "scheme": GRID_SCHEME,
            "matrix": matrix,
            "x": x,
            "y": y,
            "bounds_um": list(grid_cell_bounds(matrix, x, y)),
            "width": CELL_WIDTH,
            "height": CELL_HEIGHT,
        },
        "acquisition": _validate_acquisition(acquisition),
        "encoding": {
            "codec": QRGB_CODEC,
            "max_abs_channel_error": decoded.mode,
            "payload_ref": decoded.payload_sha256,
            "decoded_length": len(decoded.rgb),
            "decoded_rgb_sha256": sha256_bytes(decoded.rgb),
        },
        "reference_rgb_ref": sha256_bytes(reference),
        "critical_mask_ref": sha256_bytes(mask),
        "critical_mask_recipe_ref": sha256_canonical_bytes(recipe_raw),
        "quality_receipt_ref": receipt_ref,
    }
    validated = validate_imagery_cell_document(document)
    # Exercise the same product replay used by the prepared benchmark path.
    _validate_prepared_imagery_cell(
        validated,
        payload,
        quality_receipt,
        reference_rgb=reference,
        critical_mask=mask,
        critical_mask_recipe=recipe,
    )
    return document


def _seam_direction(left: int, right: int) -> int:
    delta = right - left
    if abs(delta) < QUALITY_POLICY["edge_threshold_luma8"]:
        return 0
    return 1 if delta > 0 else 2


def _longest_true_run(values: list[bool]) -> tuple[int, int]:
    components = 0
    largest = 0
    current = 0
    for value in values:
        if value:
            if current == 0:
                components += 1
            current += 1
            largest = max(largest, current)
        else:
            current = 0
    return components, largest


_SEAM_RECEIPT_KEYS = {
    "version", "kind", "policy_sha256", "cell_set_sha256", "cell_count",
    "seam_count", "seam_sample_count", "edge_compared_samples",
    "edge_direction_matches", "edge_direction_agreement_ppm",
    "introduced_edge_samples", "removed_edge_samples",
    "direction_changed_edge_samples", "edge_change_samples",
    "edge_change_component_count", "max_connected_seam_edge_change_pixels",
    "passed", "receipt_sha256",
}


def seam_quality_metrics_pass(metrics: Mapping[str, object]) -> bool:
    compared = metrics["edge_compared_samples"]
    matches = metrics["edge_direction_matches"]
    return bool(
        (not compared or matches * 1_000_000
         >= compared * QUALITY_POLICY["min_edge_direction_agreement_ppm"])
        and metrics["max_connected_seam_edge_change_pixels"]
        <= QUALITY_POLICY["max_connected_seam_edge_change_pixels"]
    )


def _cell_set_identity(
        cells: Mapping[str, object], references: Mapping[str, bytes],
        candidates: Mapping[str, bytes], *, cells_validated: bool = False,
) -> str:
    rows = []
    for cell_id in sorted(cells):
        cell = (
            cells[cell_id] if cells_validated
            else validate_imagery_cell_document(cells[cell_id])
        )
        if not isinstance(cell, dict):
            raise ValueError("prepared seam cell must be a validated object")
        grid = cell["grid"]
        rows.append({
            "cell_id": cell_id,
            "matrix": grid["matrix"],
            "x": grid["x"],
            "y": grid["y"],
            "reference_rgb_sha256": sha256_bytes(references[cell_id]),
            "candidate_rgb_sha256": sha256_bytes(candidates[cell_id]),
        })
    return sha256_json(rows)


def build_seam_quality_receipt(
        cells: Mapping[str, object], reference_rgbs: Mapping[str, bytes],
        candidate_rgbs: Mapping[str, bytes], *,
        _execution_counts: dict[str, int] | None = None,
        _cells_validated: bool = False,
) -> dict[str, object]:
    if (not isinstance(cells, Mapping) or not cells
            or set(reference_rgbs) != set(cells)
            or set(candidate_rgbs) != set(cells)):
        raise ValueError("seam receipt cell maps are empty or incomplete")
    coordinates = {}
    rgb_products = {}
    for cell_id, raw_cell in cells.items():
        cell = (
            raw_cell if _cells_validated
            else validate_imagery_cell_document(raw_cell)
        )
        if not isinstance(cell, dict):
            raise ValueError("prepared seam cell must be a validated object")
        if not _cells_validated and imagery_cell_id(cell) != cell_id:
            raise ValueError("seam receipt cell ID is not content addressed")
        reference = _rgb(reference_rgbs[cell_id], "seam reference RGB")
        candidate = _rgb(candidate_rgbs[cell_id], "seam candidate RGB")
        if (sha256_bytes(reference) != cell["reference_rgb_ref"]
                or sha256_bytes(candidate)
                != cell["encoding"]["decoded_rgb_sha256"]):
            raise ValueError("seam receipt RGB identity mismatch")
        grid = cell["grid"]
        key = (grid["matrix"], grid["x"], grid["y"])
        if key in coordinates:
            raise ValueError("seam receipt has overlapping cells")
        coordinates[key] = cell_id
        rgb_products[cell_id] = (reference, candidate)

    seam_count = 0
    sample_count = 0
    compared = 0
    matches = 0
    introduced = 0
    removed = 0
    direction_changed = 0
    component_count = 0
    largest_component = 0
    for (matrix, x, y), first_id in sorted(coordinates.items()):
        for neighbour_key, orientation in (
            ((matrix, x + 1, y), "right"),
            ((matrix, x, y + 1), "top"),
        ):
            second_id = coordinates.get(neighbour_key)
            if second_id is None:
                continue
            seam_count += 1
            changes = []
            for offset in range(CELL_WIDTH):
                if orientation == "right":
                    first_index = offset * CELL_WIDTH + CELL_WIDTH - 1
                    second_index = offset * CELL_WIDTH
                else:
                    # The higher-y cell's bottom row meets the lower cell's top.
                    first_index = offset
                    second_index = (CELL_HEIGHT - 1) * CELL_WIDTH + offset
                ref_direction = _seam_direction(
                    _pixel_luminance(rgb_products[first_id][0], first_index),
                    _pixel_luminance(rgb_products[second_id][0], second_index),
                )
                candidate_direction = _seam_direction(
                    _pixel_luminance(rgb_products[first_id][1], first_index),
                    _pixel_luminance(rgb_products[second_id][1], second_index),
                )
                sample_count += 1
                if ref_direction or candidate_direction:
                    compared += 1
                    if ref_direction == candidate_direction:
                        matches += 1
                changed = ref_direction != candidate_direction
                changes.append(changed)
                if candidate_direction and not ref_direction:
                    introduced += 1
                elif ref_direction and not candidate_direction:
                    removed += 1
                elif changed:
                    direction_changed += 1
            components, largest = _longest_true_run(changes)
            component_count += components
            largest_component = max(largest_component, largest)
    _record_execution(_execution_counts, "seam_receipt_recomputations")
    _record_execution(_execution_counts, "seam_scans", seam_count)
    _record_execution(_execution_counts, "seam_samples", sample_count)
    agreement = 1_000_000 if not compared else matches * 1_000_000 // compared
    changed_total = introduced + removed + direction_changed
    seam_metrics = {
        "edge_compared_samples": compared,
        "edge_direction_matches": matches,
        "max_connected_seam_edge_change_pixels": largest_component,
    }
    passed = seam_quality_metrics_pass(seam_metrics)
    body: dict[str, object] = {
        "version": 1,
        "kind": SEAM_RECEIPT_KIND,
        "policy_sha256": QUALITY_POLICY_SHA256,
        "cell_set_sha256": _cell_set_identity(
            cells,
            reference_rgbs,
            candidate_rgbs,
            cells_validated=_cells_validated,
        ),
        "cell_count": len(cells),
        "seam_count": seam_count,
        "seam_sample_count": sample_count,
        "edge_compared_samples": compared,
        "edge_direction_matches": matches,
        "edge_direction_agreement_ppm": agreement,
        "introduced_edge_samples": introduced,
        "removed_edge_samples": removed,
        "direction_changed_edge_samples": direction_changed,
        "edge_change_samples": changed_total,
        "edge_change_component_count": component_count,
        "max_connected_seam_edge_change_pixels": largest_component,
        "passed": passed,
    }
    body["receipt_sha256"] = sha256_json(body)
    return body


def validate_seam_quality_receipt(
        value: object, *, cells: Mapping[str, object] | None = None,
        reference_rgbs: Mapping[str, bytes] | None = None,
        candidate_rgbs: Mapping[str, bytes] | None = None,
        require_passed: bool = True,
        _execution_counts: dict[str, int] | None = None,
        _cells_validated: bool = False,
) -> dict[str, object]:
    receipt, _raw = _canonical_document(value, "seam quality receipt")
    _closed(receipt, _SEAM_RECEIPT_KEYS, "seam quality receipt")
    if (receipt.get("version") != 1
            or receipt.get("kind") != SEAM_RECEIPT_KIND
            or receipt.get("policy_sha256") != QUALITY_POLICY_SHA256):
        raise ValueError("seam receipt policy mismatch")
    _sha256(receipt.get("cell_set_sha256"), "seam receipt cell set")
    for field in _SEAM_RECEIPT_KEYS - {
        "kind", "policy_sha256", "cell_set_sha256", "passed", "receipt_sha256",
    }:
        _integer(receipt.get(field), f"seam receipt {field}", 0)
    compared = receipt["edge_compared_samples"]
    matches = receipt["edge_direction_matches"]
    if (matches > compared
            or receipt["edge_change_samples"] != (
                receipt["introduced_edge_samples"]
                + receipt["removed_edge_samples"]
                + receipt["direction_changed_edge_samples"]
            )):
        raise ValueError("seam receipt accounting is inconsistent")
    agreement = 1_000_000 if not compared else matches * 1_000_000 // compared
    passed = seam_quality_metrics_pass(receipt)
    if (receipt["edge_direction_agreement_ppm"] != agreement
            or type(receipt.get("passed")) is not bool
            or receipt["passed"] != passed):
        raise ValueError("seam receipt result is inconsistent")
    body = dict(receipt)
    claimed = _sha256(body.pop("receipt_sha256"), "seam receipt self-hash")
    if claimed != sha256_json(body):
        raise ValueError("seam receipt self-hash mismatch")
    supplied = (cells, reference_rgbs, candidate_rgbs)
    if any(item is not None for item in supplied):
        if any(item is None for item in supplied):
            raise ValueError("seam receipt recomputation inputs are partial")
        if receipt != build_seam_quality_receipt(
                cells,
                reference_rgbs,
                candidate_rgbs,
                _execution_counts=_execution_counts,
                _cells_validated=_cells_validated):
            raise ValueError("seam receipt differs from recomputed metrics")
    if require_passed and not receipt["passed"]:
        raise ValueError("seam receipt did not pass the hashed policy")
    return receipt


_CATALOG_KEYS = {"version", "kind", "cells", "seam_quality_receipt_ref"}


def validate_imagery_catalog_document(value: object) -> dict[str, object]:
    catalog = _closed(value, _CATALOG_KEYS, "imagery catalog")
    if catalog.get("version") != 1 or catalog.get("kind") != IMAGERY_CATALOG_KIND:
        raise ValueError("imagery catalog version or kind mismatch")
    cells = catalog.get("cells")
    if not isinstance(cells, dict) or not cells or len(cells) > 100_000:
        raise ValueError("imagery catalog cell map is empty or oversized")
    for cell_id, raw_cell in cells.items():
        _sha256(cell_id, "imagery catalog cell ID")
        cell = validate_imagery_cell_document(raw_cell)
        if imagery_cell_id(cell) != cell_id:
            raise ValueError("imagery catalog cell is not content addressed")
    _sha256(catalog.get("seam_quality_receipt_ref"), "catalog seam receipt")
    return catalog


def build_imagery_catalog(
        cells: Mapping[str, object], seam_quality_receipt: object,
) -> dict[str, object]:
    receipt, receipt_raw = _canonical_document(
        seam_quality_receipt, "seam quality receipt"
    )
    validate_seam_quality_receipt(receipt)
    document = {
        "version": 1,
        "kind": IMAGERY_CATALOG_KIND,
        "cells": dict(cells),
        "seam_quality_receipt_ref": sha256_canonical_bytes(receipt_raw),
    }
    return validate_imagery_catalog_document(document)


def account_scenepack_bytes(records: Iterable[object]) -> dict[str, object]:
    """Deduplicate nonempty immutable objects and account closed categories."""
    unique: dict[str, dict[str, object]] = {}
    for raw in records:
        record = _closed(
            raw, {"sha256", "length", "category", "matrix"},
            "ScenePack byte record",
        )
        digest = _sha256(record.get("sha256"), "byte record SHA-256")
        _integer(record.get("length"), "byte record length", 1, 48 * 1024 * 1024)
        if record.get("category") not in BYTE_CATEGORIES:
            raise ValueError("ScenePack byte category is unsupported")
        matrix = record.get("matrix")
        if matrix is not None and matrix not in MATRIX_PIXEL_UM:
            raise ValueError("ScenePack byte record matrix is unsupported")
        normalized = dict(record)
        previous = unique.setdefault(digest, normalized)
        if previous != normalized:
            raise ValueError("one content hash has conflicting byte accounting")
    by_category = {category: 0 for category in sorted(BYTE_CATEGORIES)}
    by_matrix = {matrix: 0 for matrix in sorted(MATRIX_PIXEL_UM)}
    unattributed_matrix_bytes = 0
    for record in unique.values():
        by_category[record["category"]] += record["length"]
        if record["matrix"] is None:
            unattributed_matrix_bytes += record["length"]
        else:
            by_matrix[record["matrix"]] += record["length"]
    return {
        "total_bytes": sum(record["length"] for record in unique.values()),
        "object_count": len(unique),
        "largest_blob": max(
            (record["length"] for record in unique.values()), default=0
        ),
        "by_category": {
            key: value for key, value in by_category.items() if value
        },
        "by_matrix": {
            **{key: value for key, value in by_matrix.items() if value},
            "unattributed": unattributed_matrix_bytes,
        },
        "by_matrix_semantics": (
            "complete byte partition: matrix-named buckets contain records "
            "bound to that matrix; unattributed contains records with matrix null"
        ),
    }
