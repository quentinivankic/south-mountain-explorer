#!/usr/bin/env python3
"""Rooted ScenePack proof-size, replay, fidelity, and compression benchmark.

The immutable proof-v4 baseline is independently reconstructed from every leaf.
A proof-v5 candidate is accepted only as an exact content-addressed object DAG
under an explicitly supplied local root. Content consistency never establishes
real-world origin. A real achievement requires all three independent controls:
an exact repository-pinned acquisition approval, candidate-declared
``real-shadow`` provenance, and complete effective filesystem controls in the
candidate-admission phase. Every other replay reports only ``FIXTURE_ONLY`` and
no achieved ratio.
"""
from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import re
import stat
import struct
import sys
import zlib
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Mapping

TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import frame_renderer as renderer  # noqa: E402
import imagery_objects as imagery  # noqa: E402
import trusted_filesystem  # noqa: E402

DATA = TOOLS.parent / "data"
DEFAULT_REGISTRY = DATA / "co_verdicts_osm_publication_proofs.json"
ACQUISITION_APPROVAL_REGISTRY = (
    DATA / "parking-scenepack-acquisition-approvals-v1.json"
)
PINNED_ACQUISITION_APPROVAL_REGISTRY_BYTES = (
    b'{"approvals":[],"kind":"parking-scenepack-reviewed-acquisition-registry",'
    b'"version":1}'
)
PINNED_ACQUISITION_APPROVAL_REGISTRY_SHA256 = (
    "bb2a01f25097faef25fcedcb51155c69ed212a3bf1a9063ba7276ab381cf741c"
)

BEAR_CREEK_PROOF_SHA256 = (
    "87a4e0f3681d7e003b7aca61694c4385946ebb1f8095393947ebb0eb60cd18f1"
)
BEAR_CREEK_ADJUDICATIONS = 50
BEAR_CREEK_PNGS = 150
BEAR_CREEK_IMAGE_BYTES = 84_513_188
BEAR_CREEK_TOTAL_BYTES = 88_854_223
BEAR_CREEK_HARD_BYTES = 8_885_422
BEAR_CREEK_RUNG_BYTES = {
    "z1": 60_270_563,
    "z2": 16_111_609,
    "z3": 8_131_016,
}

REGISTRY_MAX_BYTES = 1024 * 1024
MANIFEST_MAX_BYTES = 4 * 1024 * 1024
LEAF_MAX_BYTES = 48 * 1024 * 1024
MAX_CANDIDATE_OBJECTS = 100_000
MAX_CANDIDATE_CLOSURE_BYTES = 128 * 1024 * 1024
MAX_REPLAY_FRAMES = 150
MAX_REPLAY_RECIPES = 150
MAX_REPLAY_CELLS = 192
MAX_REPLAY_OBJECT_READS = MAX_CANDIDATE_OBJECTS + MAX_REPLAY_FRAMES + 1
MAX_REPLAY_OBJECT_READ_BYTES = 256 * 1024 * 1024
MAX_REPLAY_CANONICAL_JSON_BYTES = 64 * 1024 * 1024
MAX_REPLAY_CANONICAL_JSON_OPERATIONS = 192 * 1024 * 1024
# A proof-v4 legacy reader performs three raw-input passes per document
# (structural scan, structure hash, and decode), plus four bounded generated or
# equality passes (canonical serialization/revalidation and legacy-pretty
# serialization/revalidation). Across the registry and manifest those generated
# passes reserve 8 * MAX_CANONICAL_JSON_BYTES. The manifest content hash adds
# MANIFEST_MAX_BYTES, and the normalized object-table serialization/hash pair
# adds the remaining 2 * MAX_CANONICAL_JSON_BYTES. These terms deliberately
# reserve the structural maximum, not today's small Bear Creek byte counts.
MAX_PROOF_V4_CANONICAL_JSON_OPERATIONS = (
    3 * (REGISTRY_MAX_BYTES + MANIFEST_MAX_BYTES)
    + MANIFEST_MAX_BYTES
    + 10 * imagery.MAX_CANONICAL_JSON_BYTES
)
# A CLI descriptor is canonical-only: content-bound scan/decode plus one
# bounded serialization/revalidation pair.
MAX_CLI_DESCRIPTOR_CANONICAL_JSON_OPERATIONS = (
    3 * MANIFEST_MAX_BYTES + 2 * imagery.MAX_CANONICAL_JSON_BYTES
)
CANDIDATE_SCOPE_CANONICAL_JSON_RESERVATIONS = {
    "candidate-evaluation": MAX_REPLAY_CANONICAL_JSON_OPERATIONS,
    "approval-baseline-rederivation": MAX_PROOF_V4_CANONICAL_JSON_OPERATIONS,
}
CANDIDATE_SCOPE_CANONICAL_JSON_PHASE_COMPONENTS = {
    "candidate-load": "candidate-evaluation",
    "candidate-replay": "candidate-evaluation",
    "acquisition-approval": "candidate-evaluation",
    "approval-baseline-rederivation": "approval-baseline-rederivation",
}
MAX_CANDIDATE_SCOPE_CANONICAL_JSON_OPERATIONS = sum(
    CANDIDATE_SCOPE_CANONICAL_JSON_RESERVATIONS.values()
)
FULL_SCOPE_CANONICAL_JSON_RESERVATIONS = {
    "initial-proof-v4-baseline": MAX_PROOF_V4_CANONICAL_JSON_OPERATIONS,
    "candidate-descriptor": MAX_CLI_DESCRIPTOR_CANONICAL_JSON_OPERATIONS,
    **CANDIDATE_SCOPE_CANONICAL_JSON_RESERVATIONS,
}
FULL_SCOPE_CANONICAL_JSON_PHASE_COMPONENTS = {
    "initial-proof-v4-baseline": "initial-proof-v4-baseline",
    "candidate-descriptor": "candidate-descriptor",
    **CANDIDATE_SCOPE_CANONICAL_JSON_PHASE_COMPONENTS,
}
MAX_FULL_SCOPE_CANONICAL_JSON_OPERATIONS = sum(
    FULL_SCOPE_CANONICAL_JSON_RESERVATIONS.values()
)
MAX_REPLAY_CANONICAL_JSON_NODES = 8_000_000
MAX_REPLAY_CANONICAL_JSON_CONTAINERS = 2_000_000
MAX_REPLAY_CANONICAL_JSON_CONTAINER_ENTRIES = 8_000_000
MAX_REPLAY_CANONICAL_JSON_STRING_BYTES = 64 * 1024 * 1024
MAX_REPLAY_SCENES = MAX_REPLAY_FRAMES
MAX_REPLAY_SCENE_POINTS = renderer.MAX_SCENE_POINTS
MAX_REPLAY_SCENE_PATHS = (
    renderer.MAX_TRAILS + renderer.MAX_LOTS * renderer.MAX_RINGS_PER_LOT
)
MAX_REPLAY_SCENE_SEGMENTS = MAX_REPLAY_SCENE_POINTS + MAX_REPLAY_SCENE_PATHS
MAX_REPLAY_SCENE_LOTS = renderer.MAX_LOTS
MAX_REPLAY_SCENE_JSON_BYTES = LEAF_MAX_BYTES
MAX_REPLAY_SCENE_PREPARED_BYTES = 192 * 1024 * 1024
MAX_REPLAY_SCENE_CANONICALIZATION_BYTES = LEAF_MAX_BYTES
MAX_REPLAY_SCENE_PREPARATION_OPERATIONS = 128 * 1024 * 1024
SCENE_PREPARED_BASE_BYTES = 4096
SCENE_PREPARED_JSON_NODE_BYTES = 64
SCENE_PREPARED_JSON_CONTAINER_BYTES = 64
SCENE_PREPARED_JSON_ENTRY_BYTES = 16
SCENE_PREPARED_POINT_BYTES = 64
SCENE_PREPARED_PATH_BYTES = 96
SCENE_PREPARED_SEGMENT_BYTES = 16
SCENE_PREPARED_LOT_BYTES = 128
MAX_REPLAY_SOURCE_RESPONSE_BYTES = 128 * 1024 * 1024
MAX_REPLAY_SOURCE_RESPONSE_PIXELS = 64 * 1024 * 1024
MAX_REPLAY_SOURCE_TRANSFORM_INPUT_PIXELS = 128 * 1024 * 1024
MAX_REPLAY_SOURCE_TRANSFORM_TAPS = 256 * 1024 * 1024
MAX_REPLAY_QRGB_DECODE_BYTES = 192 * imagery.DECODED_RGB_LENGTH
MAX_REPLAY_QUALITY_WINDOWS = (
    MAX_REPLAY_FRAMES
    * ((renderer.MAX_OUTPUT_SIDE + imagery.QUALITY_POLICY["ssim_window_side"] - 1)
       // imagery.QUALITY_POLICY["ssim_window_side"]) ** 2
    + MAX_REPLAY_CELLS
    * ((imagery.CELL_WIDTH + imagery.QUALITY_POLICY["ssim_window_side"] - 1)
       // imagery.QUALITY_POLICY["ssim_window_side"])
    * ((imagery.CELL_HEIGHT + imagery.QUALITY_POLICY["ssim_window_side"] - 1)
       // imagery.QUALITY_POLICY["ssim_window_side"])
)
MAX_REPLAY_QUALITY_EDGE_SCANS = 256 * 1024 * 1024
MAX_REPLAY_SEAM_LUMA_WORK = 8 * 1024 * 1024
MAX_REPLAY_PNG_COMPRESSED_BYTES = 96 * 1024 * 1024
MAX_REPLAY_PNG_DECODE_BYTES = 512 * 1024 * 1024
MAX_REPLAY_OUTPUT_PIXELS = 150 * 1024 * 1024
MAX_REPLAY_CLIP_SEGMENTS = 10_000_000
MAX_REPLAY_FRACTION_OPERATIONS = 160_000_000
MAX_REPLAY_RETAINED_RAW_BYTES = MAX_CANDIDATE_CLOSURE_BYTES
MAX_REPLAY_NON_SCENE_PREPARED_BYTES = 192 * 1024 * 1024
MAX_REPLAY_METADATA_PREPARED_BYTES = 64 * 1024 * 1024
MAX_REPLAY_RETAINED_RGB_BYTES = 256 * 1024 * 1024
MAX_REPLAY_WORKING_MEMORY_BYTES = 224 * 1024 * 1024
MAX_REPLAY_PEAK_RETAINED_BYTES = 256 * 1024 * 1024
MAX_REPLAY_TOTAL_OPERATIONS = 25_000_000_000
NON_SCENE_PREPARED_BASE_BYTES = 1024
NON_SCENE_PREPARED_RAW_BYTE_MULTIPLIER = 2
NON_SCENE_PREPARED_JSON_NODE_BYTES = 96
NON_SCENE_PREPARED_JSON_CONTAINER_BYTES = 96
NON_SCENE_PREPARED_JSON_ENTRY_BYTES = 32
NON_SCENE_PREPARED_STRING_BYTES = 2
BASELINE_METADATA_OBJECT_BYTES = 512
BASELINE_METADATA_FRAME_BYTES = 1024
BASELINE_METADATA_DECISION_BYTES = 1024
RUNTIME_METADATA_OBJECT_BYTES = 512
RUNTIME_METADATA_FRAME_BYTES = 1024
RUNTIME_METADATA_CELL_BYTES = 1024
RUNTIME_METADATA_SOURCE_BYTES = 2048
CELL_VALIDATION_WORKING_BYTES = 192 * imagery.CELL_PIXEL_COUNT
FRAME_WORKING_BYTES_PER_PIXEL = 192
READ_SIZE = 1024 * 1024
PINNED_REVIEWED_ACL_EQUIVALENT: str | None = None
REAL_ACQUISITION_PROVENANCE_KIND = "real-shadow"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_PNG_SIDE = 2048
MAX_PNG_PIXELS = MAX_PNG_SIDE * MAX_PNG_SIDE
PNG_ALPHA_BACKGROUND = (255, 255, 255)
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_LABEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._:-]{0,127}\Z")

CANDIDATE_CATEGORIES = imagery.BYTE_CATEGORIES
CANDIDATE_MATRICES = frozenset(imagery.MATRIX_PIXEL_UM)
CANDIDATE_OBJECT_DIRECTORY = "sha256"
REQUIRED_CANDIDATE_CATEGORIES = frozenset({
    "source-response-bytes",
    "source-descriptor",
    "clean-source-assertion",
    "source-transform-receipt",
    "acquisition-manifest",
    "critical-mask",
    "critical-mask-recipe",
    "cell-payload",
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
})
JSON_OBJECT_CATEGORIES = frozenset({
    "source-descriptor", "clean-source-assertion",
    "source-transform-receipt", "acquisition-manifest",
    "critical-mask-recipe", "quality-receipt", "seam-quality-receipt",
    "imagery-catalog", "overlay-scene", "bitmap-font", "frame-recipe",
    "frame-fidelity-receipt", "frame-set", "packet-corpus",
    "prompt-recipe", "structured-authority",
})


def _canonical_json(value: object) -> bytes:
    return imagery.canonical_json(value)


def _pretty_json(value: object) -> bytes:
    raw = (json.dumps(
        value, ensure_ascii=False, sort_keys=True, indent=1, allow_nan=False,
    ) + "\n").encode("utf-8")
    if len(raw) > imagery.MAX_CANONICAL_JSON_BYTES:
        raise ValueError("legacy JSON serialization exceeds its byte cap")
    imagery.charge_json_work("legacy_json_serialization", len(raw))
    return raw


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key {key!r}")
        value[key] = item
    return value


def _strict_json(
        raw: bytes, label: str, *, allow_legacy_pretty: bool = False,
        structure: imagery.JsonStructure | None = None,
) -> object:
    if structure is None:
        imagery.inspect_json_structure(raw, label)
    elif (not isinstance(structure, imagery.JsonStructure)
          or structure.raw_bytes != len(raw)
          or structure.raw_sha256 != imagery.sha256_canonical_bytes(raw)):
        raise ValueError(f"{label} precomputed JSON structure does not bind its bytes")
    imagery.charge_json_work("json_decode", len(raw))
    try:
        value = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except (
            UnicodeDecodeError, ValueError, RecursionError, MemoryError,
            OverflowError,
    ) as error:
        raise ValueError(f"{label} is not strict JSON: {error}") from error
    try:
        canonical = _canonical_json(value)
        imagery.charge_json_revalidation(raw, canonical)
        if raw == canonical:
            return value
        if allow_legacy_pretty:
            pretty = _pretty_json(value)
            imagery.charge_json_revalidation(raw, pretty)
            if raw == pretty:
                return value
    except (RecursionError, MemoryError, OverflowError) as error:
        raise ValueError(
            f"{label} canonicalization exceeded resource limits"
        ) from error
    raise ValueError(f"{label} bytes are noncanonical")


def _valid_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _closed(value: object, keys: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(f"{label} schema mismatch")
    return value


def _sha256(value: object, label: str) -> str:
    if not _valid_sha256(value):
        raise ValueError(f"{label} must be a lowercase SHA-256")
    return value


def _integer(value: object, label: str, minimum: int = 0,
             maximum: int | None = None) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{label} must be an integer >= {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{label} exceeds its cap")
    return value


def _positive_ratio(value: object) -> int:
    if type(value) is not int or value < 1:
        raise ValueError("required ratio must be a positive integer")
    return value


def _cli_ratio(value: object) -> int:
    if not isinstance(value, str) or re.fullmatch(r"[0-9]+", value) is None:
        raise ValueError("--require-ratio must be an integer >= 1")
    return _positive_ratio(int(value))


def _non_scene_prepared_bound(
        raw_length: int, structure: imagery.JsonStructure,
) -> int:
    """Conservatively bound one retained parsed non-scene JSON document."""
    return (
        NON_SCENE_PREPARED_BASE_BYTES
        + raw_length * NON_SCENE_PREPARED_RAW_BYTE_MULTIPLIER
        + structure.nodes * NON_SCENE_PREPARED_JSON_NODE_BYTES
        + structure.containers * NON_SCENE_PREPARED_JSON_CONTAINER_BYTES
        + structure.container_entries * NON_SCENE_PREPARED_JSON_ENTRY_BYTES
        + structure.string_expansion_bytes * NON_SCENE_PREPARED_STRING_BYTES
    )


REPLAY_LIMITS = {
    "object_reads": MAX_REPLAY_OBJECT_READS,
    "object_read_bytes": MAX_REPLAY_OBJECT_READ_BYTES,
    "canonical_json_bytes": MAX_REPLAY_CANONICAL_JSON_BYTES,
    "canonical_json_operations": MAX_REPLAY_CANONICAL_JSON_OPERATIONS,
    "canonical_json_nodes": MAX_REPLAY_CANONICAL_JSON_NODES,
    "canonical_json_containers": MAX_REPLAY_CANONICAL_JSON_CONTAINERS,
    "canonical_json_container_entries": (
        MAX_REPLAY_CANONICAL_JSON_CONTAINER_ENTRIES
    ),
    "canonical_json_string_bytes": MAX_REPLAY_CANONICAL_JSON_STRING_BYTES,
    "canonical_json_max_depth": imagery.MAX_CANONICAL_JSON_DEPTH,
    "scenes": MAX_REPLAY_SCENES,
    "scene_points": MAX_REPLAY_SCENE_POINTS,
    "scene_paths": MAX_REPLAY_SCENE_PATHS,
    "scene_segments": MAX_REPLAY_SCENE_SEGMENTS,
    "scene_lots": MAX_REPLAY_SCENE_LOTS,
    "scene_json_bytes": MAX_REPLAY_SCENE_JSON_BYTES,
    "scene_prepared_bytes": MAX_REPLAY_SCENE_PREPARED_BYTES,
    "scene_canonicalization_bytes": MAX_REPLAY_SCENE_CANONICALIZATION_BYTES,
    "scene_preparation_operations": MAX_REPLAY_SCENE_PREPARATION_OPERATIONS,
    "source_response_bytes": MAX_REPLAY_SOURCE_RESPONSE_BYTES,
    "source_response_pixels": MAX_REPLAY_SOURCE_RESPONSE_PIXELS,
    "source_transform_input_pixels": MAX_REPLAY_SOURCE_TRANSFORM_INPUT_PIXELS,
    "source_transform_taps": MAX_REPLAY_SOURCE_TRANSFORM_TAPS,
    "qrgb_decode_bytes": MAX_REPLAY_QRGB_DECODE_BYTES,
    "quality_windows": MAX_REPLAY_QUALITY_WINDOWS,
    "quality_edge_scans": MAX_REPLAY_QUALITY_EDGE_SCANS,
    "seam_cells": MAX_REPLAY_CELLS,
    "seam_luma_work": MAX_REPLAY_SEAM_LUMA_WORK,
    "png_compressed_bytes": MAX_REPLAY_PNG_COMPRESSED_BYTES,
    "png_decode_bytes": MAX_REPLAY_PNG_DECODE_BYTES,
    "frames": MAX_REPLAY_FRAMES,
    "cells": MAX_REPLAY_CELLS,
    "recipes": MAX_REPLAY_RECIPES,
    "output_pixels": MAX_REPLAY_OUTPUT_PIXELS,
    "clip_segments": MAX_REPLAY_CLIP_SEGMENTS,
    "fraction_operations": MAX_REPLAY_FRACTION_OPERATIONS,
    "retained_raw_bytes": MAX_REPLAY_RETAINED_RAW_BYTES,
    "non_scene_prepared_bytes": MAX_REPLAY_NON_SCENE_PREPARED_BYTES,
    "metadata_prepared_bytes": MAX_REPLAY_METADATA_PREPARED_BYTES,
    "retained_rgb_bytes": MAX_REPLAY_RETAINED_RGB_BYTES,
    "working_memory_bytes": MAX_REPLAY_WORKING_MEMORY_BYTES,
    "peak_retained_bytes": MAX_REPLAY_PEAK_RETAINED_BYTES,
    "render_operation_budget": MAX_REPLAY_TOTAL_OPERATIONS,
    "total_operations_planned": MAX_REPLAY_TOTAL_OPERATIONS,
}


@dataclass
class ReplayBudget:
    """Whole-closure deterministic resource accounting and rejection limits."""

    usage: dict[str, int] = field(default_factory=dict)

    def reserve(self, name: str, amount: int) -> None:
        if name not in REPLAY_LIMITS or type(amount) is not int or amount < 0:
            raise ValueError(f"invalid replay budget reservation {name!r}")
        value = self.usage.get(name, 0) + amount
        if value > REPLAY_LIMITS[name]:
            raise ValueError(
                f"replay {name} budget exceeded: {value} > {REPLAY_LIMITS[name]}"
            )
        self.usage[name] = value

    def observe_max(self, name: str, amount: int) -> None:
        if name not in REPLAY_LIMITS or type(amount) is not int or amount < 0:
            raise ValueError(f"invalid replay budget observation {name!r}")
        if amount > REPLAY_LIMITS[name]:
            raise ValueError(
                f"replay {name} budget exceeded: {amount} > {REPLAY_LIMITS[name]}"
            )
        self.usage[name] = max(self.usage.get(name, 0), amount)

    def reserve_json_structure(self, structure: imagery.JsonStructure) -> None:
        self.reserve("canonical_json_nodes", structure.nodes)
        self.reserve("canonical_json_containers", structure.containers)
        self.reserve(
            "canonical_json_container_entries", structure.container_entries
        )
        self.reserve(
            "canonical_json_string_bytes", structure.string_expansion_bytes
        )
        self.observe_max("canonical_json_max_depth", structure.max_depth)

    def report(
            self, json_work: imagery.CanonicalJsonWorkAudit | None = None,
    ) -> dict[str, object]:
        canonical_json_bytes = self.usage.get("canonical_json_bytes", 0)
        candidate_reservation = self.usage.get(
            "canonical_json_operations", 0
        )
        execution = None if json_work is None else json_work.report()
        if (execution is not None
                and execution["reserved_byte_operations"]
                < candidate_reservation):
            raise ValueError(
                "canonical JSON execution audit does not match its preflight "
                "reservation"
            )
        candidate_component = (
            None if execution is None else execution[
                "component_execution"
            ].get("candidate-evaluation")
        )
        if execution is not None and candidate_component is None:
            raise ValueError(
                "canonical JSON audit omits the candidate-evaluation component"
            )
        if (candidate_component is not None
                and candidate_component["reserved_byte_operations"]
                != candidate_reservation):
            raise ValueError(
                "canonical JSON candidate component does not match its "
                "preflight reservation"
            )
        scope_reservation = (
            candidate_reservation if execution is None
            else execution["reserved_byte_operations"]
        )
        return {
            "planned": dict(sorted(self.usage.items())),
            "limits": dict(sorted(REPLAY_LIMITS.items())),
            "semantics": (
                "planned values are conservative preflight reservations or "
                "bounds, never claims of executed work; canonical_json_bytes "
                "is the declared rooted candidate JSON input size, the "
                "candidate ceiling is reserved before candidate parsing, and "
                "the enclosing scope also reserves a separate proof-v4-sized "
                "approval re-derivation component plus any CLI baseline or "
                "descriptor components"
            ),
            "operation_equations": {
                "canonical_json_operations": {
                    "declared_rooted_json_bytes": canonical_json_bytes,
                    "candidate_reserved_byte_operations": candidate_reservation,
                    "candidate_executed_byte_operations": (
                        None if candidate_component is None
                        else candidate_component["executed_byte_operations"]
                    ),
                    "candidate_reservation_closed": (
                        None if candidate_component is None
                        else candidate_component["executed_byte_operations"]
                        <= candidate_reservation
                    ),
                    "reserved_byte_operations": scope_reservation,
                    "executed_byte_operations": (
                        None if execution is None
                        else execution["executed_byte_operations"]
                    ),
                    "headroom_byte_operations": (
                        None if execution is None
                        else execution["headroom_byte_operations"]
                    ),
                    "reservation_closed": (
                        None if execution is None
                        else execution["executed_byte_operations"]
                        <= scope_reservation
                    ),
                    "candidate_reservation_within_scope": (
                        candidate_reservation <= scope_reservation
                    ),
                    "reservation_equation": (
                        "candidate_reserved_byte_operations = "
                        "MAX_REPLAY_CANONICAL_JSON_OPERATIONS; every candidate "
                        "scope separately reserves "
                        "MAX_PROOF_V4_CANONICAL_JSON_OPERATIONS for approval "
                        "baseline re-derivation, and a CLI scope also reserves "
                        "its initial baseline and descriptor components"
                    ),
                    "execution_audit": execution,
                },
            },
        }


def _acl_platform() -> str:
    return sys.platform


def _acl_platform_requirement(platform: str) -> str:
    if platform == "darwin":
        return (
            "Darwin descriptor ACL policy via "
            "trusted_filesystem.require_trivial_acl_fd "
            "(acl_get_fd_np/acl_free)"
        )
    if platform.startswith("linux"):
        return (
            "Linux descriptor ACL policy via "
            "trusted_filesystem.require_trivial_acl_fd (fgetxattr), plus "
            "descriptor xattr enumeration rejecting unrecognized ACL backends"
        )
    return f"no reviewed descriptor ACL policy for platform {platform}"


@dataclass
class _FilesystemControlAudit:
    phase: str
    governs_real_achievement: bool
    acl_platform: str = field(default_factory=lambda: _acl_platform())
    acl_inspection_attempts: int = 0
    acl_inspections_completed: int = 0
    acl_unavailable_reasons: dict[str, int] = field(default_factory=dict)

    def record_acl_completed(self) -> None:
        self.acl_inspection_attempts += 1
        self.acl_inspections_completed += 1

    def record_acl_unavailable(self, reason: str) -> None:
        self.acl_inspection_attempts += 1
        self.acl_unavailable_reasons[reason] = (
            self.acl_unavailable_reasons.get(reason, 0) + 1
        )

    def report(self) -> dict[str, object]:
        unavailable = (
            self.acl_inspection_attempts - self.acl_inspections_completed
        )
        acl_complete = (
            self.acl_inspection_attempts > 0 and unavailable == 0
        )
        configured_override = PINNED_REVIEWED_ACL_EQUIVALENT
        configured_display = (
            configured_override
            if configured_override is None or isinstance(configured_override, str)
            else f"invalid-type:{type(configured_override).__name__}"
        )
        # No string override is admission authority. A typed, reviewed registry
        # must exist before equivalent support can be introduced.
        complete = acl_complete
        acl_requirement = _acl_platform_requirement(self.acl_platform)
        acl_status = (
            "available-enforced" if acl_complete else "unavailable-degraded"
        )
        candidate_scope = (
            "effective-user-owned exact-mode 0700 candidate root/hash "
            "directories and 0600 single-link regular object files"
            if self.governs_real_achievement else "not-exercised-in-this-phase"
        )
        candidate_ancestors = (
            "no-follow directory traversal plus final candidate-root identity "
            "revalidation; ancestor owner, mode, and ACL are not admission "
            "controls"
            if self.governs_real_achievement else "not-exercised-in-this-phase"
        )
        return {
            "phase": self.phase,
            "governs_real_achievement": self.governs_real_achievement,
            "status": "complete" if complete else "degraded",
            "complete_for_real_achievement": complete,
            "required_controls": {
                "content_hash_and_exact_length": "enforced",
                "candidate_root_and_object_tree": candidate_scope,
                "candidate_root_ancestors": candidate_ancestors,
                "trust_anchor_files": (
                    "effective-user-owned non-group/world-writable "
                    "single-link regular files"
                ),
                "trust_anchor_ancestors": (
                    "named components below the filesystem root are "
                    "root/effective-user-owned, not group- or world-writable "
                    "no-follow directories, except writable root-owned sticky "
                    "directories; the filesystem root is identity-only; all "
                    "descriptor identities are stable across read; ancestor "
                    "ACLs are not controls because every trust-anchor payload "
                    "is independently content pinned"
                ),
                "stable_no_follow_descriptor_reads": "enforced",
                "acl_inspection": f"{acl_requirement}; {acl_status}",
            },
            "acl_inspection": {
                "platform": self.acl_platform,
                "policy": "trusted_filesystem.require_trivial_acl_fd",
                "required_platform_capability": acl_requirement,
                "status": acl_status,
                "attempts": self.acl_inspection_attempts,
                "completed": self.acl_inspections_completed,
                "unavailable": unavailable,
                "unavailable_reasons": dict(sorted(
                    self.acl_unavailable_reasons.items()
                )),
            },
            "reviewed_acl_equivalent": {
                "support": "not-implemented",
                "shipped_value": None,
                "observed_runtime_value": configured_display,
                "configured_but_rejected": configured_override is not None,
                "accepted_for_real_achievement": False,
            },
        }


_ACTIVE_FILESYSTEM_CONTROL_AUDIT: ContextVar[
    _FilesystemControlAudit | None
] = ContextVar("proof_v5_filesystem_control_audit", default=None)


def _exact_cache_execution_closure(
        cell_count: int, unique_payload_count: int,
        execution_counts: Mapping[str, int],
) -> dict[str, object]:
    """Require cached work to close against its exact semantic cardinality."""
    expected = {
        "source_transform_derivations": cell_count,
        "qrgb_decodes": unique_payload_count,
    }
    executed = {
        "source_transform_derivations": (
            execution_counts.get("source_transform_identity_copies", 0)
            + execution_counts.get("source_transform_bilinear_scans", 0)
        ),
        "qrgb_decodes": execution_counts.get("qrgb_decodes", 0),
    }
    omissions = {
        name: expected[name] - executed[name] for name in expected
    }
    if any(value != 0 for value in omissions.values()):
        raise ValueError(
            f"candidate cached replay omitted exact work: {omissions}"
        )
    return {
        "expected": expected,
        "executed": executed,
        "omissions": omissions,
        "equations": {
            "source_transform_derivations": (
                "source_transform_identity_copies + "
                "source_transform_bilinear_scans == cell_count"
            ),
            "qrgb_decodes": "qrgb_decodes == unique_payload_refs",
        },
    }


def _trust_anchor_ancestor_mode_allowed(owner_uid: int, mode: int) -> bool:
    """Allow owner writes, but no group/world writes except root sticky dirs."""
    return not mode & 0o022 or bool(mode & stat.S_ISVTX and owner_uid == 0)


def _bounded_regular(path: Path, maximum: int, label: str) -> bytes:
    """Read one identity-stable trust anchor through no-follow descriptors.

    The candidate root/object tree uses a different sealed policy: its root and
    hash directories must be effective-user-owned mode 0700 and each object
    must be an effective-user-owned mode-0600 single-link regular file. This
    trust-anchor reader permits normal read-only checkout bits. Its file must
    be effective-user-owned, single-link, and not group/world writable. Every
    named ancestor below the filesystem root must be root- or
    effective-user-owned and not group- or world-writable, except a writable
    root-owned sticky directory (for example a platform temp parent). The
    filesystem root itself is identity-revalidated only. Named ancestor ACLs
    are not authority controls: every trust-anchor document or leaf is content
    pinned, while ancestor type, ownership, mode, no-follow traversal, and
    identity revalidation prevent an undetected path substitution during read.
    The final trust-anchor file and every sealed candidate directory/file reuse
    ``trusted_filesystem.require_trivial_acl_fd`` on Darwin and Linux. Linux
    additionally requires descriptor xattr enumeration so any ACL-bearing
    backend outside that tracked policy is rejected. An unsupported or
    incomplete platform capability makes the governing candidate-admission
    phase unable to admit a real ``PASS``.

    Content authority remains separate: the acquisition registry is byte- and
    SHA-256-pinned, proof-v4 manifests/leaves are hash-pinned, and the CLI
    descriptor remains stable untrusted input.
    """
    exact_path = Path(path)
    if (type(maximum) is not int or maximum < 0
            or not exact_path.is_absolute() or exact_path == Path("/")
            or ".." in exact_path.parts):
        raise ValueError(f"{label} path or size bound is invalid")

    directory_flags = (
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    )
    file_flags = (
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    directory_descriptors: list[int] = []
    lineage: list[tuple[int, str, int, tuple[int, ...]]] = []
    descriptor = None
    root_descriptor = os.open("/", directory_flags)
    directory_descriptors.append(root_descriptor)
    root_identity = _directory_identity(os.fstat(root_descriptor))
    try:
        parent_descriptor = root_descriptor
        for component in exact_path.parts[1:-1]:
            entry = os.stat(
                component, dir_fd=parent_descriptor, follow_symlinks=False
            )
            if not stat.S_ISDIR(entry.st_mode):
                raise ValueError(f"{label} has a symlinked directory ancestor")
            child_descriptor = os.open(
                component, directory_flags, dir_fd=parent_descriptor
            )
            child = os.fstat(child_descriptor)
            identity = _directory_identity(child)
            if identity != _directory_identity(entry):
                os.close(child_descriptor)
                raise ValueError(f"{label} directory lineage changed while opened")
            if child.st_uid not in {0, os.geteuid()}:
                os.close(child_descriptor)
                raise ValueError(f"{label} has a foreign-owned directory ancestor")
            mode = stat.S_IMODE(child.st_mode)
            if not _trust_anchor_ancestor_mode_allowed(child.st_uid, mode):
                os.close(child_descriptor)
                raise ValueError(
                    f"{label} has a group- or world-writable directory ancestor"
                )
            lineage.append((
                parent_descriptor, component, child_descriptor, identity,
            ))
            directory_descriptors.append(child_descriptor)
            parent_descriptor = child_descriptor

        name = exact_path.name
        entry_before = os.stat(
            name, dir_fd=parent_descriptor, follow_symlinks=False
        )
        if not stat.S_ISREG(entry_before.st_mode):
            raise ValueError(f"{label} is symlinked or not regular")
        descriptor = os.open(name, file_flags, dir_fd=parent_descriptor)
        before = os.fstat(descriptor)
        identity = _stat_identity(before)
        if identity != _stat_identity(entry_before):
            raise ValueError(f"{label} changed while it was opened")
        if before.st_uid != os.geteuid():
            raise ValueError(f"{label} is not owned by the effective user")
        if stat.S_IMODE(before.st_mode) & 0o022:
            raise ValueError(f"{label} is group- or world-writable")
        if before.st_nlink != 1:
            raise ValueError(f"{label} is not a single-link regular file")
        _inspect_trivial_acl(descriptor, label)
        if before.st_size > maximum:
            raise ValueError(f"{label} exceeds its cap")

        chunks = []
        length = 0
        while True:
            chunk = os.read(descriptor, min(READ_SIZE, maximum - length + 1))
            if not chunk:
                break
            chunks.append(chunk)
            length += len(chunk)
            if length > maximum:
                raise ValueError(f"{label} exceeds its cap")

        after = os.fstat(descriptor)
        entry_after = os.stat(
            name, dir_fd=parent_descriptor, follow_symlinks=False
        )
        if (_stat_identity(after) != identity
                or _stat_identity(entry_after) != identity
                or _directory_identity(os.fstat(root_descriptor))
                != root_identity):
            raise ValueError(f"{label} changed while it was read")
        for parent, component, child, expected in lineage:
            live_entry = os.stat(
                component, dir_fd=parent, follow_symlinks=False
            )
            if (_directory_identity(live_entry) != expected
                    or _directory_identity(os.fstat(child)) != expected):
                raise ValueError(f"{label} directory lineage changed while read")
        return b"".join(chunks)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        for directory_descriptor in reversed(directory_descriptors):
            os.close(directory_descriptor)


def _canonical_object_path(digest: str) -> str:
    return f"publication-proof-objects/sha256/{digest[:2]}/{digest[2:]}"


@dataclass(frozen=True)
class LeafRef:
    path: str
    length: int
    sha256: str


@dataclass(frozen=True)
class LogicalRef:
    length: int
    sha256: str
    leaves: tuple[LeafRef, ...]


@dataclass(frozen=True)
class BaselineMeasurement:
    proof_sha256: str
    manifest_length: int
    adjudications: int
    frame_count: int
    image_bytes: int
    total_bytes: int
    logical_objects: int
    leaf_objects: int
    largest_blob: int
    by_rung: Mapping[str, int]
    frame_sources: Mapping[str, str]
    frame_dimensions: Mapping[str, tuple[int, int]]
    decisions: Mapping[str, object]
    object_root: Path | None = field(default=None, repr=False, compare=False)
    object_refs: Mapping[str, LogicalRef] = field(
        default_factory=dict, repr=False, compare=False
    )
    registry_path: Path | None = field(default=None, repr=False, compare=False)

    def report(self, require_ratio: int = 10) -> dict[str, object]:
        require_ratio = _positive_ratio(require_ratio)
        gate = self.total_bytes // require_ratio
        if self.proof_sha256 == BEAR_CREEK_PROOF_SHA256 and require_ratio == 10:
            gate = BEAR_CREEK_HARD_BYTES
        return {
            "proof_sha256": self.proof_sha256,
            "adjudications": self.adjudications,
            "png_frames": self.frame_count,
            "image_bytes": self.image_bytes,
            "other_bytes_including_manifest": self.total_bytes - self.image_bytes,
            "total_closure_bytes": self.total_bytes,
            "logical_objects": self.logical_objects,
            "leaf_objects": self.leaf_objects,
            "blob_count_including_manifest": self.leaf_objects + 1,
            "largest_blob": self.largest_blob,
            "by_rung": dict(self.by_rung),
            "required_ratio": require_ratio,
            "candidate_byte_gate": gate,
            "required_savings_bytes": self.total_bytes - gate,
            "real_resolution_validation_plan": renderer.real_resolution_budget_plan(
                self.frame_count, 1024, 1024
            ),
        }


def _leaf_ref(value: object) -> LeafRef:
    descriptor = _closed(value, {"path", "length", "sha256"}, "leaf descriptor")
    digest = _sha256(descriptor.get("sha256"), "leaf SHA-256")
    length = _integer(descriptor.get("length"), "leaf length", 0, LEAF_MAX_BYTES)
    path = descriptor.get("path")
    if (not isinstance(path, str) or path != _canonical_object_path(digest)
            or PurePosixPath(path).is_absolute()
            or ".." in PurePosixPath(path).parts):
        raise ValueError("leaf object path is noncanonical")
    return LeafRef(path=path, length=length, sha256=digest)


def _logical_ref(value: object) -> LogicalRef:
    descriptor = _closed(value, {"length", "sha256", "leaves"}, "logical descriptor")
    length = _integer(descriptor.get("length"), "logical length")
    digest = _sha256(descriptor.get("sha256"), "logical SHA-256")
    raw_leaves = descriptor.get("leaves")
    if not isinstance(raw_leaves, list) or not raw_leaves:
        raise ValueError("logical object has no leaves")
    leaves = tuple(_leaf_ref(leaf) for leaf in raw_leaves)
    expected_count = max(1, (length + LEAF_MAX_BYTES - 1) // LEAF_MAX_BYTES)
    if len(leaves) != expected_count:
        raise ValueError("logical leaf count is noncanonical")
    remaining = length
    for leaf in leaves:
        expected = min(LEAF_MAX_BYTES, remaining)
        if length == 0:
            expected = 0
        if leaf.length != expected:
            raise ValueError("logical leaf length is noncanonical")
        remaining -= leaf.length
    if remaining:
        raise ValueError("logical leaves do not cover the exact length")
    return LogicalRef(length=length, sha256=digest, leaves=leaves)


def _object_path(root: Path, leaf: LeafRef) -> Path:
    path = root.joinpath(*PurePosixPath(leaf.path).parts)
    try:
        path.relative_to(root)
    except ValueError as error:
        raise ValueError("leaf escaped its object root") from error
    return path


def _read_logical(root: Path, value: LogicalRef) -> bytes:
    # Proof-v4 logical leaves are opaque mixed evidence (PNG, prompts, JSON,
    # receipts). Without a declared media category they are integrity-hashed as
    # bytes, never guessed into the canonical-JSON ledger.
    result = bytearray()
    digest = hashlib.sha256()
    for leaf in value.leaves:
        raw = _bounded_regular(
            _object_path(root, leaf), leaf.length, f"proof leaf {leaf.sha256}"
        )
        if len(raw) != leaf.length or imagery.sha256_bytes(raw) != leaf.sha256:
            raise ValueError(f"proof leaf {leaf.sha256} length or hash mismatch")
        result.extend(raw)
        digest.update(raw)
    if len(result) != value.length or digest.hexdigest() != value.sha256:
        raise ValueError("logical object reconstructed length or SHA-256 mismatch")
    return bytes(result)


def _verify_leaf(root: Path, leaf: LeafRef) -> bytes:
    raw = _bounded_regular(_object_path(root, leaf), leaf.length, f"proof leaf {leaf.sha256}")
    if len(raw) != leaf.length or imagery.sha256_bytes(raw) != leaf.sha256:
        raise ValueError(f"proof leaf {leaf.sha256} length or hash mismatch")
    return raw[:32]


def _manifest_references(manifest: Mapping[str, object]) -> set[str]:
    references = {manifest["prepare_ref"], manifest["output_seal_ref"]}
    for field_name in (
        "sealed_outputs", "rendered_prompts", "prompt_templates",
        "normative_documents", "chunk_receipts", "terminal_drafts",
        "terminal_checkpoints", "packet_objects",
    ):
        values = manifest[field_name]
        if not isinstance(values, dict):
            raise ValueError(f"proof v4 {field_name} is not an object")
        references.update(value for value in values.values() if value is not None)
    bindings = manifest["packet_bindings"]
    if not isinstance(bindings, dict):
        raise ValueError("proof v4 packet bindings are malformed")
    for binding in bindings.values():
        if not isinstance(binding, dict) or not isinstance(binding.get("tile_sha256"), dict):
            raise ValueError("proof v4 tile binding is malformed")
        references.update(binding["tile_sha256"].values())
    authority = manifest["human_authority"]
    reviews = manifest["human_reviews"]
    if not isinstance(authority, dict) or not isinstance(reviews, dict):
        raise ValueError("proof v4 human authority maps are malformed")
    for entry in authority.values():
        if not isinstance(entry, dict):
            raise ValueError("proof v4 authority entry is malformed")
        references.update(entry.values())
    for entry in reviews.values():
        if not isinstance(entry, dict) or not isinstance(entry.get("source_artifacts"), dict):
            raise ValueError("proof v4 review entry is malformed")
        references.update((entry.get("receipt_ref"), entry.get("source_prepare_ref")))
        references.update(entry["source_artifacts"].values())
    if None in references or not all(_valid_sha256(value) for value in references):
        raise ValueError("proof v4 contains an invalid object reference")
    return references


def _decision_projection(row: object) -> dict[str, object]:
    if not isinstance(row, dict):
        raise ValueError("proof final row is malformed")
    axes = {}
    for name in ("exists", "public", "serves"):
        axis = row.get(name)
        if not isinstance(axis, dict):
            raise ValueError(f"proof final row {name} axis is malformed")
        axes[name] = axis.get("call")
    result = {
        "verdict": row.get("verdict"),
        "confidence": row.get("confidence"),
        **axes,
    }
    if (result["verdict"] not in {"KEEP", "DROP", "REVIEW"}
            or result["confidence"] not in {"certain", "strong", "leaning"}
            or result["exists"] not in {"yes", "no", "unclear", "n/a"}
            or result["public"] not in {"yes", "no", "unclear", "n/a"}
            or result["serves"] not in {"yes", "no", "unclear", "n/a"}):
        raise ValueError("proof final row has an unsupported decision tuple")
    return result


def measure_proof_v4(
        registry_path: Path = DEFAULT_REGISTRY,
        proof_sha256: str = BEAR_CREEK_PROOF_SHA256,
) -> BaselineMeasurement:
    """Independently verify and measure one compact proof-v4 closure."""
    proof_sha256 = _sha256(proof_sha256, "proof SHA-256")
    registry_path = Path(registry_path).absolute()
    registry_raw = _bounded_regular(
        registry_path, REGISTRY_MAX_BYTES, "publication proof registry"
    )
    registry = _strict_json(
        registry_raw, "publication proof registry", allow_legacy_pretty=True
    )
    if (not isinstance(registry, dict)
            or set(registry) != {"version", "kind", "proofs"}
            or registry.get("version") != 2
            or registry.get("kind") != "parking-publication-proof-registry"
            or not isinstance(registry.get("proofs"), dict)):
        raise ValueError("publication proof registry v2 schema mismatch")
    manifest_leaf = _leaf_ref(registry["proofs"].get(proof_sha256))
    if manifest_leaf.sha256 != proof_sha256 or manifest_leaf.length > MANIFEST_MAX_BYTES:
        raise ValueError("proof manifest registry descriptor mismatch")
    manifest_raw = _bounded_regular(
        _object_path(registry_path.parent, manifest_leaf),
        manifest_leaf.length,
        "proof manifest",
    )
    if (len(manifest_raw) != manifest_leaf.length
            or imagery.sha256_canonical_bytes(manifest_raw) != proof_sha256):
        raise ValueError("proof manifest length or hash mismatch")
    manifest = _strict_json(
        manifest_raw, "proof manifest", allow_legacy_pretty=True
    )
    required = {
        "version", "kind", "run_id", "area", "path_scheme", "objects",
        "object_closure_sha256", "prepare_ref", "output_seal_ref",
        "sealed_outputs", "rendered_prompts", "prompt_templates",
        "normative_documents", "chunk_receipts", "terminal_drafts",
        "terminal_checkpoints", "packet_objects", "packet_bindings",
        "final_rows", "human_authority", "human_reviews",
    }
    if (not isinstance(manifest, dict) or set(manifest) != required
            or manifest.get("version") != 4
            or manifest.get("kind") != "parking-publication-proof"):
        raise ValueError("proof v4 manifest schema mismatch")
    table = manifest.get("objects")
    if not isinstance(table, dict) or not table:
        raise ValueError("proof v4 object table is empty")
    logical: dict[str, LogicalRef] = {}
    normalized_table = {}
    leaves: dict[str, LeafRef] = {}
    for object_sha, descriptor in table.items():
        ref = _logical_ref(descriptor)
        if ref.sha256 != object_sha:
            raise ValueError("logical object table key/hash mismatch")
        logical[object_sha] = ref
        normalized_table[object_sha] = descriptor
        for leaf in ref.leaves:
            previous = leaves.setdefault(leaf.sha256, leaf)
            if previous != leaf:
                raise ValueError("proof closure has conflicting leaf descriptors")
    closure_hash = imagery.sha256_json(normalized_table)
    if closure_hash != manifest.get("object_closure_sha256"):
        raise ValueError("proof v4 object closure hash mismatch")
    references = _manifest_references(manifest)
    if references != set(logical):
        missing = sorted(references - set(logical))
        extra = sorted(set(logical) - references)
        raise ValueError(
            f"proof v4 is not an exact closure: missing={missing[:3]} extra={extra[:3]}"
        )

    prefixes = {
        digest: _verify_leaf(registry_path.parent, leaf)
        for digest, leaf in sorted(leaves.items())
    }
    for ref in logical.values():
        if len(ref.leaves) == 1 and ref.leaves[0].sha256 == ref.sha256:
            continue
        _read_logical(registry_path.parent, ref)

    final_rows = manifest["final_rows"]
    bindings = manifest["packet_bindings"]
    packet_objects = manifest["packet_objects"]
    if (not isinstance(final_rows, dict) or not isinstance(bindings, dict)
            or not isinstance(packet_objects, dict)
            or set(final_rows) != set(bindings)
            or set(final_rows) != set(packet_objects)):
        raise ValueError("proof v4 adjudication coverage mismatch")
    frame_sources = {}
    frame_dimensions = {}
    by_rung = {"z1": 0, "z2": 0, "z3": 0}
    image_hashes = set()
    for fid in sorted(bindings, key=lambda value: int(value)):
        binding = bindings[fid]
        tiles = binding.get("tile_sha256") if isinstance(binding, dict) else None
        if not isinstance(tiles, dict) or set(tiles) != set(by_rung):
            raise ValueError(f"proof v4 fid {fid} does not bind all three rungs")
        for rung in sorted(by_rung):
            digest = _sha256(tiles[rung], f"fid {fid} {rung} PNG SHA-256")
            ref = logical.get(digest)
            if ref is None:
                raise ValueError("proof v4 frame is absent from its closure")
            prefix = prefixes[ref.leaves[0].sha256]
            width = int.from_bytes(prefix[16:20], "big")
            height = int.from_bytes(prefix[20:24], "big")
            if (prefix[:8] != PNG_SIGNATURE or prefix[12:16] != b"IHDR"
                    or width != 1024 or height != 1024):
                raise ValueError("proof v4 frame is not a 1024x1024 PNG")
            frame_key = f"{fid}/{rung}"
            frame_sources[frame_key] = digest
            frame_dimensions[frame_key] = (width, height)
            image_hashes.add(digest)
            by_rung[rung] += ref.length
    if len(image_hashes) != len(frame_sources):
        raise ValueError("proof v4 reuses a PNG object across required frames")
    decisions = {
        fid: _decision_projection(final_rows[fid])
        for fid in sorted(final_rows, key=lambda value: int(value))
    }
    measurement = BaselineMeasurement(
        proof_sha256=proof_sha256,
        manifest_length=manifest_leaf.length,
        adjudications=len(final_rows),
        frame_count=len(frame_sources),
        image_bytes=sum(logical[digest].length for digest in image_hashes),
        total_bytes=manifest_leaf.length + sum(leaf.length for leaf in leaves.values()),
        logical_objects=len(logical),
        leaf_objects=len(leaves),
        largest_blob=max(
            [manifest_leaf.length] + [leaf.length for leaf in leaves.values()]
        ),
        by_rung=by_rung,
        frame_sources=frame_sources,
        frame_dimensions=frame_dimensions,
        decisions=decisions,
        object_root=registry_path.parent,
        object_refs=logical,
        registry_path=registry_path,
    )
    if proof_sha256 == BEAR_CREEK_PROOF_SHA256:
        expected = (
            BEAR_CREEK_ADJUDICATIONS,
            BEAR_CREEK_PNGS,
            BEAR_CREEK_IMAGE_BYTES,
            BEAR_CREEK_TOTAL_BYTES,
            BEAR_CREEK_RUNG_BYTES,
        )
        actual = (
            measurement.adjudications,
            measurement.frame_count,
            measurement.image_bytes,
            measurement.total_bytes,
            dict(measurement.by_rung),
        )
        if actual != expected:
            raise ValueError(
                f"Bear Creek baseline drifted: expected={expected!r} actual={actual!r}"
            )
    _validate_baseline_frame_identities(measurement)
    return measurement


def candidate_object_path(root: Path, digest: str) -> Path:
    """Return the canonical candidate object path under a supplied root."""
    exact = _sha256(digest, "candidate object SHA-256")
    return Path(root) / CANDIDATE_OBJECT_DIRECTORY / exact[:2] / exact[2:]


def _stat_identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_gid,
        value.st_size, value.st_mtime_ns, value.st_ctime_ns, value.st_nlink,
    )


def _directory_identity(value: os.stat_result) -> tuple[int, ...]:
    """Return stable directory identity fields unaffected by sibling churn."""
    return (
        value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_gid,
    )


def _acl_xattr_name(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "strict")
    if isinstance(value, str):
        return value
    raise ValueError("descriptor ACL enumeration returned a non-text xattr name")


def _is_acl_bearing_xattr(name: str) -> bool:
    return any(
        component == "acl" or component.endswith("acl")
        for component in re.split(r"[._-]", name.casefold())
    )


def _inspect_trivial_acl(descriptor: int, label: str) -> bool:
    """Apply the tracked Darwin/Linux descriptor ACL policy, failing closed.

    Linux also enumerates descriptor xattrs and rejects every ACL-bearing name
    not already rejected by the tracked POSIX policy, including NFSv4 and
    RichACL representations. Unsupported or incomplete capabilities are
    recorded as degraded rather than treated as ACL-trivial.
    """
    audit = _ACTIVE_FILESYSTEM_CONTROL_AUDIT.get()
    platform = audit.acl_platform if audit is not None else _acl_platform()

    def unavailable(reason: str) -> bool:
        if audit is not None:
            audit.record_acl_unavailable(reason)
        return False

    if platform != "darwin" and not platform.startswith("linux"):
        return unavailable(f"unsupported-platform:{platform}")
    try:
        value = os.fstat(descriptor)
        trusted_filesystem.require_trivial_acl_fd(
            descriptor,
            label,
            is_directory=stat.S_ISDIR(value.st_mode),
        )
    except ValueError:
        raise
    except TypeError:
        return unavailable("trusted-filesystem-policy-incompatible")
    except (AttributeError, NotImplementedError, OSError) as error:
        error_name = (
            errno.errorcode.get(error.errno, "UNKNOWN")
            if isinstance(error, OSError) else type(error).__name__
        )
        return unavailable(
            f"trusted-filesystem-policy-unavailable:{error_name}"
        )

    if platform.startswith("linux"):
        inspector = getattr(os, "listxattr", None)
        if not callable(inspector):
            return unavailable("descriptor-listxattr-missing")
        try:
            attributes = inspector(descriptor)
        except TypeError:
            return unavailable("descriptor-listxattr-unsupported")
        except (AttributeError, NotImplementedError, OSError):
            return unavailable("descriptor-listxattr-unavailable")
        try:
            acl_attributes = sorted(
                name for name in map(_acl_xattr_name, attributes)
                if _is_acl_bearing_xattr(name)
            )
        except (UnicodeDecodeError, ValueError):
            return unavailable("descriptor-listxattr-malformed")
        if acl_attributes:
            raise ValueError(
                f"{label} has a nontrivial or unrecognized ACL backend: "
                f"{acl_attributes}"
            )

    if audit is not None:
        audit.record_acl_completed()
    return True


def _require_sealed_stat(
        value: os.stat_result, label: str, expected_mode: int,
        *, directory: bool,
) -> None:
    expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected_type(value.st_mode):
        raise ValueError(f"{label} has the wrong filesystem type")
    if value.st_uid != os.geteuid():
        raise ValueError(f"{label} is not owned by the current effective user")
    mode = stat.S_IMODE(value.st_mode)
    if mode != expected_mode or mode & 0o022:
        raise ValueError(
            f"{label} mode {mode:04o} is not sealed as {expected_mode:04o}"
        )


def _open_directory_component(
        parent_fd: int, name: str, label: str, *, sealed: bool,
) -> tuple[int, tuple[int, ...]]:
    flags = (
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    )
    entry = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    descriptor = os.open(name, flags, dir_fd=parent_fd)
    try:
        value = os.fstat(descriptor)
        if (not stat.S_ISDIR(value.st_mode)
                or _directory_identity(entry) != _directory_identity(value)):
            raise ValueError(f"{label} changed while it was opened")
        if sealed:
            _require_sealed_stat(value, label, 0o700, directory=True)
            _inspect_trivial_acl(descriptor, label)
        return descriptor, _directory_identity(value)
    except Exception:
        os.close(descriptor)
        raise


def _open_candidate_root(root: Path) -> tuple[int, tuple[int, ...]]:
    """Open a sealed candidate root through identity-stable no-follow ancestors.

    Only the final candidate root is owner/mode/ACL sealed here. Earlier path
    components establish directory type and no-follow traversal; their owner,
    mode, and ACL are intentionally not candidate admission controls. Reopening
    after each object read must still resolve to the same final root identity.
    """
    path = Path(root)
    parts = path.parts[1:] if path.is_absolute() else ()
    if not path.is_absolute() or ".." in path.parts or not parts:
        raise ValueError(
            "candidate object root must be an absolute normalized path with "
            "a traversable component"
        )
    flags = (
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    )
    descriptor = os.open("/", flags)
    try:
        for index, component in enumerate(parts):
            child, identity = _open_directory_component(
                descriptor,
                component,
                "candidate object root" if index + 1 == len(parts)
                else "candidate root path component",
                sealed=index + 1 == len(parts),
            )
            os.close(descriptor)
            descriptor = child
        return descriptor, identity
    except Exception:
        os.close(descriptor)
        raise


def _read_candidate_object(
        root: Path, digest: str, expected_length: int, label: str, *,
        json_document: bool = False,
) -> bytes:
    """Read one exact object from the fully sealed candidate object tree.

    The final root, object directory, and hash-prefix directory are sealed mode
    0700; the single-link object is sealed mode 0600. All are effective-user
    owned, no-follow, and ACL-inspected when capability exists. Candidate
    directories are revalidated with device/inode/type/owner identity fields
    that deliberately ignore benign sibling-entry churn; object files retain
    strict size/time/link metadata identity. Ancestors above the candidate root
    follow ``_open_candidate_root``'s identity/no-follow-only policy.
    """
    _sha256(digest, f"{label} digest")
    length = _integer(expected_length, f"{label} length", 1, LEAF_MAX_BYTES)
    root_fd, root_identity = _open_candidate_root(Path(root))
    directory_fd = None
    prefix_fd = None
    descriptor = None
    try:
        directory_fd, directory_identity = _open_directory_component(
            root_fd, CANDIDATE_OBJECT_DIRECTORY,
            "candidate object directory", sealed=True,
        )
        prefix_fd, prefix_identity = _open_directory_component(
            directory_fd, digest[:2],
            "candidate hash-prefix directory", sealed=True,
        )
        flags = (
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        entry_before = os.stat(
            digest[2:], dir_fd=prefix_fd, follow_symlinks=False
        )
        descriptor = os.open(digest[2:], flags, dir_fd=prefix_fd)
        before = os.fstat(descriptor)
        identity = _stat_identity(before)
        if identity != _stat_identity(entry_before):
            raise ValueError(f"{label} changed while it was opened")
        _require_sealed_stat(before, label, 0o600, directory=False)
        _inspect_trivial_acl(descriptor, label)
        if before.st_nlink != 1 or before.st_size != length:
            raise ValueError(
                f"{label} is not a single-link regular file of exact declared length"
            )
        chunks = []
        read_length = 0
        while read_length < length:
            chunk = os.read(descriptor, min(READ_SIZE, length - read_length))
            if not chunk:
                raise ValueError(f"{label} ended before its declared length")
            chunks.append(chunk)
            read_length += len(chunk)
        if os.read(descriptor, 1):
            raise ValueError(f"{label} exceeds its declared length")
        after = os.fstat(descriptor)
        entry_after = os.stat(
            digest[2:], dir_fd=prefix_fd, follow_symlinks=False
        )
        raw = b"".join(chunks)
        if (_stat_identity(after) != identity
                or _stat_identity(entry_after) != identity
                or _directory_identity(os.fstat(prefix_fd)) != prefix_identity
                or _directory_identity(os.fstat(directory_fd))
                != directory_identity
                or _directory_identity(os.fstat(root_fd)) != root_identity
                or (
                    imagery.sha256_canonical_bytes(raw)
                    if json_document else imagery.sha256_bytes(raw)
                ) != digest):
            raise ValueError(f"{label} changed or failed its exact SHA-256")
        live_prefix = os.stat(
            digest[:2], dir_fd=directory_fd, follow_symlinks=False
        )
        live_directory = os.stat(
            CANDIDATE_OBJECT_DIRECTORY, dir_fd=root_fd, follow_symlinks=False
        )
        if (_directory_identity(live_prefix) != prefix_identity
                or _directory_identity(live_directory) != directory_identity):
            raise ValueError(f"{label} directory lineage changed while read")
        reopened_fd, reopened_identity = _open_candidate_root(Path(root))
        os.close(reopened_fd)
        if reopened_identity != root_identity:
            raise ValueError("candidate object root was substituted while read")
        return raw
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if prefix_fd is not None:
            os.close(prefix_fd)
        if directory_fd is not None:
            os.close(directory_fd)
        os.close(root_fd)


def decode_png_rgb(raw: bytes) -> tuple[int, int, bytes]:
    """Decode a bounded noninterlaced 8-bit PNG with a frozen sample contract."""
    if not isinstance(raw, bytes) or not raw.startswith(PNG_SIGNATURE):
        raise ValueError("baseline frame is not a PNG")
    offset = len(PNG_SIGNATURE)
    ihdr = None
    idat = bytearray()
    palette = None
    transparency = None
    saw_iend = False
    saw_idat = False
    idat_ended = False
    while offset < len(raw):
        if offset + 12 > len(raw):
            raise ValueError("PNG chunk is truncated")
        length = int.from_bytes(raw[offset:offset + 4], "big")
        chunk_type = raw[offset + 4:offset + 8]
        end = offset + 12 + length
        if end > len(raw):
            raise ValueError("PNG chunk length exceeds input")
        data = raw[offset + 8:offset + 8 + length]
        expected_crc = int.from_bytes(raw[offset + 8 + length:end], "big")
        if zlib.crc32(chunk_type + data) & 0xFFFFFFFF != expected_crc:
            raise ValueError("PNG chunk CRC mismatch")
        if ihdr is None and chunk_type != b"IHDR":
            raise ValueError("PNG IHDR is not first")
        if saw_idat and chunk_type not in {b"IDAT", b"IEND"}:
            idat_ended = True
        if chunk_type == b"IHDR":
            if ihdr is not None or length != 13:
                raise ValueError("PNG has malformed duplicate IHDR")
            ihdr = struct.unpack(">IIBBBBB", data)
        elif chunk_type == b"PLTE":
            if palette is not None:
                raise ValueError("PNG has duplicate PLTE")
            if saw_idat or transparency is not None:
                raise ValueError("PNG PLTE must precede tRNS and IDAT")
            if ihdr[3] in {0, 4}:
                raise ValueError("PNG color type forbids PLTE")
            palette = bytes(data)
        elif chunk_type == b"tRNS":
            if transparency is not None:
                raise ValueError("PNG has duplicate tRNS")
            if saw_idat:
                raise ValueError("PNG tRNS must precede IDAT")
            if ihdr[3] in {4, 6}:
                raise ValueError("PNG alpha color type forbids tRNS")
            if ihdr[3] == 3 and palette is None:
                raise ValueError("indexed PNG tRNS must follow PLTE")
            transparency = bytes(data)
        elif chunk_type == b"IDAT":
            if idat_ended:
                raise ValueError("PNG IDAT chunks must be contiguous")
            saw_idat = True
            if len(idat) + len(data) > LEAF_MAX_BYTES:
                raise ValueError("PNG compressed stream exceeds cap")
            idat.extend(data)
        elif chunk_type == b"IEND":
            if length or saw_iend:
                raise ValueError("PNG IEND is malformed")
            saw_iend = True
            offset = end
            break
        elif chunk_type[:1].isupper():
            raise ValueError(f"unsupported critical PNG chunk {chunk_type!r}")
        offset = end
    if not saw_iend or offset != len(raw) or ihdr is None or not saw_idat:
        raise ValueError("PNG termination or image data is incomplete")
    width, height, bit_depth, color_type, compression, filter_method, interlace = ihdr
    if (not 1 <= width <= MAX_PNG_SIDE or not 1 <= height <= MAX_PNG_SIDE
            or width * height > MAX_PNG_PIXELS or bit_depth != 8
            or compression != 0 or filter_method != 0 or interlace != 0
            or color_type not in {0, 2, 3, 4, 6}):
        raise ValueError("PNG raster contract is unsupported")
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]
    row_bytes = width * channels
    expected_length = height * (row_bytes + 1)
    decoder = zlib.decompressobj(zlib.MAX_WBITS)
    decoded = bytearray()
    try:
        for compressed_offset in range(0, len(idat), 64 * 1024):
            pending = bytes(idat[compressed_offset:compressed_offset + 64 * 1024])
            while pending:
                before = len(pending)
                piece = decoder.decompress(
                    pending, expected_length - len(decoded) + 1
                )
                decoded.extend(piece)
                if len(decoded) > expected_length:
                    raise ValueError("PNG decoded stream exceeds its exact cap")
                pending = decoder.unconsumed_tail
                if pending and not piece and len(pending) == before:
                    raise ValueError("PNG decoder made no bounded progress")
                if decoder.unused_data:
                    raise ValueError("PNG zlib stream has trailing data")
        decoded.extend(decoder.flush(expected_length - len(decoded) + 1))
    except zlib.error as error:
        raise ValueError(f"PNG zlib stream is invalid: {error}") from error
    if (not decoder.eof or decoder.unused_data or decoder.unconsumed_tail
            or len(decoded) != expected_length):
        raise ValueError("PNG decoded stream has wrong bounded length")
    decoded = bytes(decoded)
    if color_type == 0 and transparency is not None:
        if len(transparency) != 2 or int.from_bytes(transparency, "big") > 255:
            raise ValueError("grayscale PNG transparency key is malformed")
        transparent_gray = int.from_bytes(transparency, "big")
    else:
        transparent_gray = None
    if color_type == 2 and transparency is not None:
        if len(transparency) != 6:
            raise ValueError("RGB PNG transparency key is malformed")
        transparent_rgb = tuple(
            int.from_bytes(transparency[index:index + 2], "big")
            for index in range(0, 6, 2)
        )
        if any(channel > 255 for channel in transparent_rgb):
            raise ValueError("RGB PNG transparency key exceeds 8-bit samples")
    else:
        transparent_rgb = None
    if color_type in {4, 6} and transparency is not None:
        raise ValueError("alpha PNG must not also contain tRNS")
    if (palette is not None
            and (not palette or len(palette) % 3 or len(palette) > 256 * 3)):
        raise ValueError("PNG palette is malformed")
    if color_type == 3:
        if palette is None:
            raise ValueError("indexed PNG palette is missing")
        if transparency is not None and len(transparency) > len(palette) // 3:
            raise ValueError("indexed PNG transparency table is oversized")
        alphas = transparency or b""
    else:
        alphas = b""

    rows = []
    prior = bytearray(row_bytes)
    position = 0
    for _row in range(height):
        filter_type = decoded[position]
        position += 1
        current = bytearray(decoded[position:position + row_bytes])
        position += row_bytes
        if filter_type == 1:
            for index in range(channels, row_bytes):
                current[index] = (current[index] + current[index - channels]) & 0xFF
        elif filter_type == 2:
            for index in range(row_bytes):
                current[index] = (current[index] + prior[index]) & 0xFF
        elif filter_type == 3:
            for index in range(row_bytes):
                left = current[index - channels] if index >= channels else 0
                current[index] = (current[index] + (left + prior[index]) // 2) & 0xFF
        elif filter_type == 4:
            for index in range(row_bytes):
                left = current[index - channels] if index >= channels else 0
                up = prior[index]
                upper_left = prior[index - channels] if index >= channels else 0
                estimate = left + up - upper_left
                left_distance = abs(estimate - left)
                up_distance = abs(estimate - up)
                upper_left_distance = abs(estimate - upper_left)
                predictor = (
                    left if left_distance <= up_distance
                    and left_distance <= upper_left_distance
                    else up if up_distance <= upper_left_distance else upper_left
                )
                current[index] = (current[index] + predictor) & 0xFF
        elif filter_type != 0:
            raise ValueError("PNG uses an unsupported row filter")
        rows.append(bytes(current))
        prior = current

    def composite(channel: int, alpha: int, background: int) -> int:
        return (
            channel * alpha + background * (255 - alpha) + 127
        ) // 255

    rgb = bytearray(width * height * 3)
    output = 0
    for row in rows:
        for column in range(width):
            index = column * channels
            alpha = 255
            if color_type == 0:
                red = green = blue = row[index]
                if transparent_gray is not None and red == transparent_gray:
                    alpha = 0
            elif color_type == 2:
                red, green, blue = row[index:index + 3]
                if transparent_rgb is not None and (red, green, blue) == transparent_rgb:
                    alpha = 0
            elif color_type == 3:
                palette_index = row[index]
                palette_offset = palette_index * 3
                if palette_offset + 3 > len(palette):
                    raise ValueError("indexed PNG references missing palette entry")
                red, green, blue = palette[palette_offset:palette_offset + 3]
                alpha = alphas[palette_index] if palette_index < len(alphas) else 255
            elif color_type == 4:
                red = green = blue = row[index]
                alpha = row[index + 1]
            else:
                red, green, blue, alpha = row[index:index + 4]
            if alpha != 255:
                red = composite(red, alpha, PNG_ALPHA_BACKGROUND[0])
                green = composite(green, alpha, PNG_ALPHA_BACKGROUND[1])
                blue = composite(blue, alpha, PNG_ALPHA_BACKGROUND[2])
            rgb[output:output + 3] = bytes((red, green, blue))
            output += 3
    return width, height, bytes(rgb)


def _baseline_frame_rgb(
        baseline: BaselineMeasurement, frame_key: str,
) -> tuple[int, int, bytes]:
    if baseline.object_root is None:
        raise ValueError("baseline does not expose a rooted frame closure")
    digest = baseline.frame_sources[frame_key]
    ref = baseline.object_refs.get(digest)
    if ref is None:
        raise ValueError("baseline frame object is unavailable")
    return decode_png_rgb(_read_logical(baseline.object_root, ref))


_APPROVAL_REGISTRY_KEYS = {"version", "kind", "approvals"}
_APPROVAL_ENTRY_KEYS = {
    "candidate_proof_manifest_ref", "baseline_proof_sha256",
    "acquisition_manifest_ref", "source_set_sha256", "review_id",
}


def _load_acquisition_approval_registry() -> tuple[dict[str, object], ...]:
    raw = _bounded_regular(
        ACQUISITION_APPROVAL_REGISTRY,
        len(PINNED_ACQUISITION_APPROVAL_REGISTRY_BYTES),
        "repository-pinned acquisition approval registry",
    )
    if (raw != PINNED_ACQUISITION_APPROVAL_REGISTRY_BYTES
            or imagery.sha256_canonical_bytes(raw)
            != PINNED_ACQUISITION_APPROVAL_REGISTRY_SHA256):
        raise ValueError("repository-pinned acquisition approval registry drifted")
    registry = _closed(
        _strict_json(raw, "repository-pinned acquisition approval registry"),
        _APPROVAL_REGISTRY_KEYS,
        "repository-pinned acquisition approval registry",
    )
    if (registry.get("version") != 1
            or registry.get("kind")
            != "parking-scenepack-reviewed-acquisition-registry"):
        raise ValueError("acquisition approval registry identity mismatch")
    approvals = registry.get("approvals")
    if not isinstance(approvals, list):
        raise ValueError("acquisition approval registry entries are malformed")
    normalized = []
    previous = None
    for raw_entry in approvals:
        entry = _closed(
            raw_entry, _APPROVAL_ENTRY_KEYS, "acquisition approval entry"
        )
        for field_name in (
            "candidate_proof_manifest_ref", "baseline_proof_sha256",
            "acquisition_manifest_ref", "source_set_sha256",
        ):
            _sha256(entry.get(field_name), f"acquisition approval {field_name}")
        review_id = entry.get("review_id")
        if not isinstance(review_id, str) or _LABEL_RE.fullmatch(review_id) is None:
            raise ValueError("acquisition approval review ID is noncanonical")
        key = entry["candidate_proof_manifest_ref"]
        if previous is not None and key <= previous:
            raise ValueError("acquisition approvals are not uniquely sorted")
        previous = key
        normalized.append(dict(entry))
    return tuple(normalized)


def _validate_baseline_frame_identities(baseline: BaselineMeasurement) -> None:
    decision_fids = set()
    for fid in baseline.decisions:
        if (not isinstance(fid, str) or not fid.isdecimal()
                or str(int(fid)) != fid):
            raise ValueError("baseline decision FID is noncanonical")
        decision_fids.add(int(fid))
    parsed = {renderer.parse_frame_key(key) for key in baseline.frame_sources}
    if len(parsed) != len(baseline.frame_sources):
        raise ValueError("baseline frame identities are not one-to-one")
    if any(fid not in decision_fids for fid, _rung in parsed):
        raise ValueError("baseline frame FID lacks a structured decision")
    if set(baseline.frame_dimensions) != set(baseline.frame_sources):
        raise ValueError("baseline frame dimensions do not cover every frame")
    for dimensions in baseline.frame_dimensions.values():
        if (not isinstance(dimensions, tuple) or len(dimensions) != 2
                or any(type(side) is not int or not 1 <= side <= MAX_PNG_SIDE
                       for side in dimensions)
                or dimensions[0] * dimensions[1] > MAX_PNG_PIXELS):
            raise ValueError("baseline frame dimensions are malformed")
    if baseline.proof_sha256 == BEAR_CREEK_PROOF_SHA256:
        expected = {
            (fid, rung)
            for fid in decision_fids
            for rung in ("z1", "z2", "z3")
        }
        if (len(decision_fids) != BEAR_CREEK_ADJUDICATIONS
                or parsed != expected
                or len(parsed) != BEAR_CREEK_PNGS
                or set(baseline.frame_dimensions.values()) != {(1024, 1024)}):
            raise ValueError(
                "Bear Creek frame identities are not 50 decisions by 3 "
                "1024x1024 rungs"
            )


_ROOT_KEYS = {
    "acquisition_manifest_ref", "imagery_catalog_ref", "overlay_scene_refs",
    "bitmap_font_ref", "frame_set_ref", "packet_corpus_ref",
    "prompt_recipe_ref", "structured_authority_ref",
}
_MANIFEST_KEYS = {
    "version", "kind", "baseline_proof_sha256", "roots", "objects",
    "object_closure_sha256", "warm_cache_refs",
}
_RECORD_KEYS = {"length", "category", "matrix"}
_DESCRIPTOR_KEYS = {
    "version", "kind", "baseline_proof_sha256", "provenance",
    "proof_manifest_ref", "proof_manifest_length",
}
_PROVENANCE_KEYS = {"kind", "label"}
_FRAME_SET_KEYS = {"version", "kind", "baseline_proof_sha256", "frames"}
_FRAME_ENTRY_KEYS = {
    "fid", "rung", "recipe_ref", "source_png_sha256",
    "rendered_rgb_sha256", "fidelity_receipt_ref",
}
_PROMPT_KEYS = {
    "version", "kind", "policy_id", "required_rungs", "decision_fields",
}
_PACKET_CORPUS_KEYS = {
    "version", "kind", "baseline_proof_sha256", "prompt_recipe_ref", "packets",
}
_PACKET_KEYS = {"fid", "frame_keys"}
_AUTHORITY_KEYS = {
    "version", "kind", "baseline_proof_sha256", "packet_corpus_ref",
    "prompt_recipe_ref", "decisions",
}
_ACQUISITION_MANIFEST_KEYS = {
    "version", "kind", "baseline_proof_sha256", "candidate_roots",
    "sources", "source_set_sha256",
}
_ACQUISITION_ROOT_BINDING_KEYS = _ROOT_KEYS - {"acquisition_manifest_ref"}
_ACQUISITION_SOURCE_KEYS = {
    "source_descriptor_ref", "response_bytes_ref", "rgb_bytes_ref",
    "clean_source_assertion_ref",
}


@dataclass(frozen=True)
class CandidateClosure:
    descriptor: Mapping[str, object]
    provenance_kind: str
    manifest_ref: str
    manifest: Mapping[str, object]
    manifest_raw: bytes
    records: Mapping[str, Mapping[str, object]]
    raw_objects: Mapping[str, bytes]
    json_structures: Mapping[str, imagery.JsonStructure]
    accounting: Mapping[str, object]


def _load_candidate_closure(
        baseline: BaselineMeasurement, candidate: object, object_root: Path,
        budget: ReplayBudget, byte_gate: int,
) -> CandidateClosure:
    if "canonical_json_operations" not in budget.usage:
        # Reserve the entire unchanged hard ceiling before the first candidate
        # JSON boundary. The shared execution audit refuses any overrun.
        budget.reserve(
            "canonical_json_operations",
            MAX_REPLAY_CANONICAL_JSON_OPERATIONS,
        )
    descriptor = _closed(candidate, _DESCRIPTOR_KEYS, "candidate benchmark")
    if (descriptor.get("version") != 2
            or descriptor.get("kind") != "parking-evidence-scaling-candidate"
            or descriptor.get("baseline_proof_sha256") != baseline.proof_sha256):
        raise ValueError("candidate benchmark identity mismatch")
    provenance = _closed(
        descriptor.get("provenance"), _PROVENANCE_KEYS, "candidate provenance"
    )
    provenance_kind = provenance.get("kind")
    label = provenance.get("label")
    if provenance_kind not in {
            REAL_ACQUISITION_PROVENANCE_KIND, "synthetic-fixture"}:
        raise ValueError("candidate provenance kind is unsupported")
    if not isinstance(label, str) or _LABEL_RE.fullmatch(label) is None:
        raise ValueError("candidate provenance label is noncanonical")
    manifest_ref = _sha256(
        descriptor.get("proof_manifest_ref"), "candidate proof manifest"
    )
    manifest_length = _integer(
        descriptor.get("proof_manifest_length"), "candidate proof manifest length",
        1, MANIFEST_MAX_BYTES,
    )
    budget.reserve("object_reads", 1)
    budget.reserve("object_read_bytes", manifest_length)
    budget.reserve("canonical_json_bytes", manifest_length)
    manifest_raw = _read_candidate_object(
        Path(object_root),
        manifest_ref,
        manifest_length,
        "candidate proof manifest",
        json_document=True,
    )
    manifest_structure = imagery.inspect_json_structure(
        manifest_raw, "candidate proof manifest"
    )
    budget.reserve_json_structure(manifest_structure)
    manifest_prepared_bytes = _non_scene_prepared_bound(
        manifest_length, manifest_structure
    )
    # The retained parse proxy is reserved before this first candidate
    # json.loads, not merely before the remaining closure is parsed.
    budget.reserve("non_scene_prepared_bytes", manifest_prepared_bytes)
    manifest = _strict_json(
        manifest_raw,
        "candidate proof manifest",
        structure=manifest_structure,
    )
    _closed(manifest, _MANIFEST_KEYS, "candidate proof manifest")
    if (manifest.get("version") != 1
            or manifest.get("kind") != "parking-scenepack-proof"
            or manifest.get("baseline_proof_sha256") != baseline.proof_sha256):
        raise ValueError("candidate proof manifest identity mismatch")
    roots = _closed(manifest.get("roots"), _ROOT_KEYS, "candidate proof roots")
    for field_name in _ROOT_KEYS - {"overlay_scene_refs"}:
        _sha256(roots.get(field_name), f"candidate root {field_name}")
    scenes = roots.get("overlay_scene_refs")
    if (not isinstance(scenes, list) or not scenes
            or scenes != sorted(set(scenes))
            or any(not _valid_sha256(value) for value in scenes)):
        raise ValueError("candidate overlay scene roots are noncanonical")
    table = manifest.get("objects")
    if (not isinstance(table, dict) or not table
            or len(table) > MAX_CANDIDATE_OBJECTS):
        raise ValueError("candidate object table is empty or oversized")
    records = {}
    declared_total = manifest_length
    for digest, raw_record in table.items():
        _sha256(digest, "candidate object-table key")
        record = _closed(raw_record, _RECORD_KEYS, "candidate object record")
        length = _integer(
            record.get("length"), "candidate object length", 1, LEAF_MAX_BYTES
        )
        category = record.get("category")
        matrix = record.get("matrix")
        if category not in CANDIDATE_CATEGORIES or category == "proof-manifest":
            raise ValueError("candidate object category is unsupported in its table")
        if matrix is not None and matrix not in CANDIDATE_MATRICES:
            raise ValueError("candidate object matrix is unsupported")
        records[digest] = dict(record)
        declared_total += length
        if declared_total > MAX_CANDIDATE_CLOSURE_BYTES:
            raise ValueError("candidate declared closure exceeds its verification cap")
    if declared_total > byte_gate:
        raise ValueError(
            f"candidate is {declared_total} bytes; gate is {byte_gate}"
        )
    if manifest_ref in records:
        raise ValueError("candidate manifest is recursively listed in its own table")
    if manifest.get("object_closure_sha256") != imagery.sha256_json(records):
        raise ValueError("candidate object table closure hash mismatch")
    warm_refs = manifest.get("warm_cache_refs")
    if (not isinstance(warm_refs, list) or warm_refs != sorted(set(warm_refs))
            or any(not _valid_sha256(value) for value in warm_refs)
            or not set(warm_refs).issubset(records)):
        raise ValueError("candidate warm-cache references are noncanonical")
    # The complete raw closure remains live through replay. Reserve it before
    # reading any leaf so declared amplification cannot outrun retention gates.
    budget.reserve("retained_raw_bytes", declared_total)
    budget.reserve("object_reads", len(records))
    budget.reserve(
        "object_read_bytes", sum(record["length"] for record in records.values())
    )
    budget.reserve(
        "canonical_json_bytes",
        sum(
            record["length"] for record in records.values()
            if record["category"] in JSON_OBJECT_CATEGORIES
        ),
    )
    raw_objects = {
        digest: _read_candidate_object(
            Path(object_root),
            digest,
            record["length"],
            f"candidate {record['category']} {digest}",
            json_document=record["category"] in JSON_OBJECT_CATEGORIES,
        )
        for digest, record in sorted(records.items())
    }
    json_structures: dict[str, imagery.JsonStructure] = {}
    for digest, record in sorted(records.items()):
        if record["category"] in JSON_OBJECT_CATEGORIES:
            structure = imagery.inspect_json_structure(
                raw_objects[digest],
                f"candidate {record['category']} {digest}",
            )
            json_structures[digest] = structure
            budget.reserve_json_structure(structure)
    object_prepared_bytes = sum(
        _non_scene_prepared_bound(records[digest]["length"], structure)
        for digest, structure in json_structures.items()
        if records[digest]["category"] != "overlay-scene"
    )
    # Every remaining non-scene document can remain parsed through graph
    # closure. Their aggregate proxy is reserved before graph.json parses one.
    budget.reserve("non_scene_prepared_bytes", object_prepared_bytes)
    non_scene_prepared_bytes = (
        manifest_prepared_bytes + object_prepared_bytes
    )
    byte_records = [
        {
            "sha256": digest,
            "length": record["length"],
            "category": record["category"],
            "matrix": record["matrix"],
        }
        for digest, record in records.items()
    ]
    byte_records.append({
        "sha256": manifest_ref,
        "length": manifest_length,
        "category": "proof-manifest",
        "matrix": None,
    })
    accounting = imagery.account_scenepack_bytes(byte_records)
    if accounting["total_bytes"] != declared_total:
        raise ValueError("candidate exact byte accounting failed to close")
    categories = set(accounting["by_category"])
    missing_categories = REQUIRED_CANDIDATE_CATEGORIES - categories
    if missing_categories:
        raise ValueError(
            f"candidate closure omits required categories {sorted(missing_categories)}"
        )
    warm_reused = sum(records[digest]["length"] for digest in warm_refs)
    accounting = dict(accounting)
    accounting.update({
        "cold_total_bytes": accounting["total_bytes"],
        "candidate_declared_warm_reused_bytes": warm_reused,
        "candidate_declared_warm_incremental_bytes": (
            accounting["total_bytes"] - warm_reused
        ),
    })
    return CandidateClosure(
        descriptor=descriptor,
        provenance_kind=provenance_kind,
        manifest_ref=manifest_ref,
        manifest=manifest,
        manifest_raw=manifest_raw,
        records=records,
        raw_objects=raw_objects,
        json_structures=json_structures,
        accounting=accounting,
    )


class _ClosureGraph:
    def __init__(self, closure: CandidateClosure):
        self.closure = closure
        self.reachable: set[str] = set()
        self.parsed: dict[str, object] = {}

    def require_matrix(
            self, digest: str, matrix: str, label: str, *, exact: bool = False,
    ) -> None:
        recorded = self.closure.records[digest]["matrix"]
        allowed = {matrix} if exact else {None, matrix}
        if recorded not in allowed:
            raise ValueError(
                f"{label} matrix accounting {recorded!r} does not match {matrix!r}"
            )

    def require(
            self, digest: str, categories: str | set[str], label: str,
    ) -> bytes:
        exact = _sha256(digest, label)
        allowed = {categories} if isinstance(categories, str) else categories
        record = self.closure.records.get(exact)
        if record is None:
            raise ValueError(f"{label} is absent from the candidate object table")
        if record["category"] not in allowed:
            raise ValueError(
                f"{label} has category {record['category']!r}; expected {sorted(allowed)}"
            )
        self.reachable.add(exact)
        return self.closure.raw_objects[exact]

    def json(
            self, digest: str, categories: str | set[str], label: str,
    ) -> dict:
        raw = self.require(digest, categories, label)
        if digest not in self.parsed:
            value = _strict_json(
                raw,
                label,
                structure=self.closure.json_structures[digest],
            )
            if not isinstance(value, dict):
                raise ValueError(f"{label} must be a canonical JSON object")
            self.parsed[digest] = value
        return self.parsed[digest]

    def close(self) -> None:
        expected = set(self.closure.records)
        if self.reachable != expected:
            missing = sorted(expected - self.reachable)
            extras = sorted(self.reachable - expected)
            raise ValueError(
                f"candidate manifest is not an exact reachable closure: "
                f"missing={missing[:3]} extras={extras[:3]}"
            )


def _validate_prompt_recipe(
        graph: _ClosureGraph, digest: str,
) -> dict[str, object]:
    prompt = _closed(
        graph.json(digest, "prompt-recipe", "candidate prompt recipe"),
        _PROMPT_KEYS,
        "candidate prompt recipe",
    )
    if (prompt.get("version") != 1
            or prompt.get("kind") != "parking-proof-v5-prompt-recipe"
            or prompt.get("policy_id") != "parking-proof-v5-shadow-v1"
            or prompt.get("required_rungs") != ["z1", "z2", "z3"]
            or prompt.get("decision_fields")
            != ["verdict", "confidence", "exists", "public", "serves"]):
        raise ValueError("candidate prompt recipe contract mismatch")
    return prompt


def _validate_packet_corpus(
        graph: _ClosureGraph, digest: str, baseline: BaselineMeasurement,
        prompt_ref: str,
) -> dict[str, object]:
    corpus = _closed(
        graph.json(digest, "packet-corpus", "candidate packet corpus"),
        _PACKET_CORPUS_KEYS,
        "candidate packet corpus",
    )
    if (corpus.get("version") != 1
            or corpus.get("kind") != "parking-proof-v5-packet-corpus"
            or corpus.get("baseline_proof_sha256") != baseline.proof_sha256
            or corpus.get("prompt_recipe_ref") != prompt_ref):
        raise ValueError("candidate packet corpus identity mismatch")
    graph.require(prompt_ref, "prompt-recipe", "packet prompt recipe")
    expected: dict[str, list[str]] = {}
    for frame_key in baseline.frame_sources:
        fid, _rung = renderer.parse_frame_key(frame_key)
        expected.setdefault(str(fid), []).append(frame_key)
    expected = {key: sorted(value) for key, value in expected.items()}
    packets = corpus.get("packets")
    if not isinstance(packets, dict) or set(packets) != set(expected):
        raise ValueError("candidate packet corpus fid coverage mismatch")
    for fid, raw_packet in packets.items():
        packet = _closed(raw_packet, _PACKET_KEYS, f"candidate packet {fid}")
        if (packet.get("fid") != int(fid)
                or packet.get("frame_keys") != expected[fid]
                or any(
                    renderer.parse_frame_key(frame_key)[0] != int(fid)
                    for frame_key in packet["frame_keys"]
                )):
            raise ValueError(f"candidate packet {fid} frame/FID coverage mismatch")
    return corpus


def _validate_structured_authority(
        graph: _ClosureGraph, digest: str, baseline: BaselineMeasurement,
        packet_ref: str, prompt_ref: str,
) -> dict[str, object]:
    authority = _closed(
        graph.json(digest, "structured-authority", "candidate authority"),
        _AUTHORITY_KEYS,
        "candidate authority",
    )
    if (authority.get("version") != 1
            or authority.get("kind") != "parking-proof-v5-structured-authority"
            or authority.get("baseline_proof_sha256") != baseline.proof_sha256
            or authority.get("packet_corpus_ref") != packet_ref
            or authority.get("prompt_recipe_ref") != prompt_ref):
        raise ValueError("candidate structured authority identity mismatch")
    graph.require(packet_ref, "packet-corpus", "authority packet corpus")
    graph.require(prompt_ref, "prompt-recipe", "authority prompt recipe")
    expected_decisions = {
        fid: {"fid": int(fid), **decision}
        for fid, decision in baseline.decisions.items()
    }
    if authority.get("decisions") != expected_decisions:
        raise ValueError("candidate replayed decision, axis, or confidence parity failed")
    return authority


def _validate_acquisition_root_bindings(
        graph: _ClosureGraph, digest: str, baseline: BaselineMeasurement,
        roots: Mapping[str, object],
) -> dict[str, object]:
    manifest = _closed(
        graph.json(
            digest, "acquisition-manifest", "candidate acquisition manifest"
        ),
        _ACQUISITION_MANIFEST_KEYS,
        "candidate acquisition manifest",
    )
    if (manifest.get("version") != 1
            or manifest.get("kind")
            != "parking-scenepack-acquisition-manifest"
            or manifest.get("baseline_proof_sha256") != baseline.proof_sha256):
        raise ValueError("candidate acquisition manifest identity mismatch")
    bindings = _closed(
        manifest.get("candidate_roots"),
        _ACQUISITION_ROOT_BINDING_KEYS,
        "candidate acquisition root bindings",
    )
    expected_bindings = {
        key: roots[key] for key in sorted(_ACQUISITION_ROOT_BINDING_KEYS)
    }
    if bindings != expected_bindings:
        raise ValueError("candidate acquisition manifest root binding mismatch")
    return manifest


def _validate_acquisition_manifest(
        graph: _ClosureGraph, digest: str, baseline: BaselineMeasurement,
        roots: Mapping[str, object], expected_source_refs: set[str], *,
        prepared_manifest: Mapping[str, object] | None = None,
) -> tuple[dict[str, object], dict[str, dict[str, object]]]:
    manifest = (
        _validate_acquisition_root_bindings(graph, digest, baseline, roots)
        if prepared_manifest is None else prepared_manifest
    )
    sources = manifest.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("candidate acquisition source lineage is empty")
    normalized = []
    products: dict[str, dict[str, object]] = {}
    previous = None
    for raw_entry in sources:
        entry = _closed(
            raw_entry, _ACQUISITION_SOURCE_KEYS,
            "candidate acquisition source entry",
        )
        for field_name in _ACQUISITION_SOURCE_KEYS:
            _sha256(entry.get(field_name), f"acquisition source {field_name}")
        descriptor_ref = entry["source_descriptor_ref"]
        if previous is not None and descriptor_ref <= previous:
            raise ValueError("candidate acquisition sources are not uniquely sorted")
        previous = descriptor_ref
        descriptor = graph.json(
            descriptor_ref,
            "source-descriptor",
            "acquisition source descriptor",
        )
        response_ref = entry["response_bytes_ref"]
        response = graph.require(
            response_ref, "source-response-bytes",
            "acquisition original response bytes",
        )
        rgb_ref = entry["rgb_bytes_ref"]
        if rgb_ref != response_ref:
            raise ValueError(
                "raw source RGB reference must equal validated response bytes"
            )
        rgb = graph.require(
            rgb_ref, "source-response-bytes",
            "acquisition converted RGB bytes",
        )
        assertion_ref = entry["clean_source_assertion_ref"]
        assertion = graph.json(
            assertion_ref,
            "clean-source-assertion",
            "acquisition clean-source assertion",
        )
        descriptor = imagery.validate_source_descriptor(
            descriptor, response, rgb
        )
        imagery._validate_clean_source_assertion_document(
            assertion, descriptor
        )
        if (descriptor["response"]["response_bytes_ref"] != response_ref
                or descriptor["conversion"]["rgb_bytes_ref"] != rgb_ref
                or descriptor["clean_source_assertion_ref"] != assertion_ref):
            raise ValueError("acquisition source entry does not bind its lineage")
        normalized.append(dict(entry))
        products[descriptor_ref] = {
            "descriptor": descriptor,
            "response": response,
            "rgb": rgb,
            "assertion": assertion,
        }
    if set(products) != expected_source_refs:
        raise ValueError("acquisition manifest source set differs from cell lineage")
    if manifest.get("source_set_sha256") != imagery.sha256_json(normalized):
        raise ValueError("candidate acquisition source-set hash mismatch")
    return manifest, products


def _approved_baseline_identity(
        baseline: BaselineMeasurement,
) -> dict[str, object]:
    """Return every authority-bearing proof-v4 measurement field by name."""
    return {
        "proof_sha256": baseline.proof_sha256,
        "manifest_length": baseline.manifest_length,
        "adjudications": baseline.adjudications,
        "frame_count": baseline.frame_count,
        "image_bytes": baseline.image_bytes,
        "total_bytes": baseline.total_bytes,
        "logical_objects": baseline.logical_objects,
        "leaf_objects": baseline.leaf_objects,
        "largest_blob": baseline.largest_blob,
        "by_rung": dict(baseline.by_rung),
        "frame_sources": dict(baseline.frame_sources),
        "frame_dimensions": dict(baseline.frame_dimensions),
        "decisions": dict(baseline.decisions),
        "object_refs": dict(baseline.object_refs),
    }


def _rederive_approved_baseline(
        baseline: BaselineMeasurement,
) -> BaselineMeasurement:
    """Re-read the exact proof-v4 registry; caller object state is not authority."""
    if baseline.registry_path is None:
        raise ValueError(
            "approved acquisition requires a re-derived proof-v4 baseline registry"
        )
    try:
        with imagery.canonical_json_work_phase(
                "approval-baseline-rederivation"):
            measured = measure_proof_v4(
                baseline.registry_path, baseline.proof_sha256
            )
    except (
            OSError, ValueError, AssertionError, AttributeError, TypeError,
            KeyError, RecursionError, MemoryError, OverflowError,
    ) as error:
        raise ValueError(
            "approved acquisition baseline re-derivation failed"
        ) from error
    if _approved_baseline_identity(measured) != _approved_baseline_identity(
            baseline):
        raise ValueError(
            "approved acquisition baseline differs from its re-derived proof-v4"
        )
    return measured


def _matching_acquisition_approval(
        closure: CandidateClosure, baseline: BaselineMeasurement,
        acquisition_manifest: Mapping[str, object],
        approvals: tuple[dict[str, object], ...],
) -> dict[str, object] | None:
    if closure.provenance_kind != REAL_ACQUISITION_PROVENANCE_KIND:
        return None
    target = {
        "candidate_proof_manifest_ref": closure.manifest_ref,
        "baseline_proof_sha256": baseline.proof_sha256,
        "acquisition_manifest_ref": closure.manifest["roots"][
            "acquisition_manifest_ref"
        ],
        "source_set_sha256": acquisition_manifest["source_set_sha256"],
    }
    for entry in approvals:
        if all(entry[key] == value for key, value in target.items()):
            _rederive_approved_baseline(baseline)
            return entry
    return None


def _replay_candidate(
        baseline: BaselineMeasurement, closure: CandidateClosure,
        budget: ReplayBudget,
) -> tuple[dict[str, object], Mapping[str, object]]:
    """Validate identities and budgets before decoding, then replay exactly."""
    _validate_baseline_frame_identities(baseline)
    graph = _ClosureGraph(closure)
    roots = closure.manifest["roots"]
    font_ref = roots["bitmap_font_ref"]
    font_document = graph.json(
        font_ref, "bitmap-font", "candidate bitmap font"
    )
    font = renderer._parse_bitmap_font_document(font_document, font_ref)

    frame_set_ref = roots["frame_set_ref"]
    frame_set = _closed(
        graph.json(frame_set_ref, "frame-set", "candidate frame set"),
        _FRAME_SET_KEYS,
        "candidate frame set",
    )
    if (frame_set.get("version") != 1
            or frame_set.get("kind") != "parking-proof-v5-frame-set"
            or frame_set.get("baseline_proof_sha256") != baseline.proof_sha256):
        raise ValueError("candidate frame-set identity mismatch")
    frames = frame_set.get("frames")
    if not isinstance(frames, dict) or set(frames) != set(baseline.frame_sources):
        raise ValueError("candidate frame coverage omits or adds a required frame")
    budget.reserve("frames", len(frames))

    frame_infos: dict[str, dict[str, object]] = {}
    referenced_cell_ids: set[str] = set()
    used_scene_refs: set[str] = set()
    recipe_refs = set()
    fidelity_refs = set()
    output_pixels = 0
    render_operation_budget = 0
    for frame_key in sorted(frames):
        fid, rung = renderer.parse_frame_key(frame_key)
        entry = _closed(
            frames[frame_key], _FRAME_ENTRY_KEYS, f"candidate frame {frame_key}"
        )
        if entry.get("fid") != fid or entry.get("rung") != rung:
            raise ValueError(
                f"candidate frame {frame_key} entry FID/rung identity mismatch"
            )
        recipe_ref = _sha256(entry.get("recipe_ref"), "candidate frame recipe")
        fidelity_ref = _sha256(
            entry.get("fidelity_receipt_ref"), "candidate fidelity receipt"
        )
        source_hash = baseline.frame_sources[frame_key]
        if entry.get("source_png_sha256") != source_hash:
            raise ValueError(f"candidate frame {frame_key} changed its source evidence")
        _sha256(entry.get("rendered_rgb_sha256"), "candidate rendered RGB")
        recipe = graph.json(recipe_ref, "frame-recipe", "candidate frame recipe")
        recipe_matrix = recipe.get("matrix")
        if recipe_matrix not in CANDIDATE_MATRICES:
            raise ValueError("candidate frame recipe matrix is unsupported")
        graph.require_matrix(
            recipe_ref, recipe_matrix, "candidate frame recipe", exact=True
        )
        renderer.validate_frame_recipe_envelope(recipe, font)
        if recipe.get("fid") != fid or recipe.get("rung") != rung:
            raise ValueError(
                f"candidate frame {frame_key} recipe FID/rung identity mismatch"
            )
        baseline_width, baseline_height = baseline.frame_dimensions[frame_key]
        if (recipe["output"]["width"] != baseline_width
                or recipe["output"]["height"] != baseline_height):
            raise ValueError(
                "candidate frame dimensions differ from pinned baseline"
            )
        scene_ref = recipe["overlay_scene_ref"]
        used_scene_refs.add(scene_ref)
        recipe_refs.add(recipe_ref)
        fidelity_refs.add(fidelity_ref)
        referenced_cell_ids.update(recipe["cells"])
        pixels = recipe["output"]["width"] * recipe["output"]["height"]
        output_pixels += pixels
        render_operation_budget += max(
            renderer.MIN_RENDER_OPERATION_BUDGET,
            pixels * renderer.RENDER_OPERATIONS_PER_PIXEL,
        )
        frame_infos[frame_key] = {
            "fid": fid,
            "rung": rung,
            "entry": entry,
            "recipe_ref": recipe_ref,
            "fidelity_ref": fidelity_ref,
            "recipe": recipe,
            "scene_ref": scene_ref,
        }
    if len(recipe_refs) != len(frames) or len(fidelity_refs) != len(frames):
        raise ValueError("frame recipes and fidelity receipts must be one-to-one")
    budget.reserve("recipes", len(recipe_refs))
    budget.reserve("output_pixels", output_pixels)
    budget.reserve("render_operation_budget", render_operation_budget)

    acquisition_header = _validate_acquisition_root_bindings(
        graph, roots["acquisition_manifest_ref"], baseline, roots
    )
    rooted_scene_refs = roots["overlay_scene_refs"]
    exact_used_scene_refs = sorted(used_scene_refs)
    acquisition_scene_refs = acquisition_header["candidate_roots"][
        "overlay_scene_refs"
    ]
    if (rooted_scene_refs != exact_used_scene_refs
            or acquisition_scene_refs != exact_used_scene_refs):
        frame_unused = sorted(set(rooted_scene_refs) - used_scene_refs)
        unrooted = sorted(used_scene_refs - set(rooted_scene_refs))
        raise ValueError(
            "candidate overlay scene roots must equal the exact frame-used "
            f"scene set: frame-unused={frame_unused[:3]} unrooted={unrooted[:3]}"
        )
    budget.reserve("scenes", len(exact_used_scene_refs))

    scene_raws = {
        scene_ref: graph.require(
            scene_ref, "overlay-scene", "candidate overlay scene"
        )
        for scene_ref in exact_used_scene_refs
    }
    scene_json_bytes = sum(len(raw) for raw in scene_raws.values())
    budget.reserve("scene_json_bytes", scene_json_bytes)
    budget.reserve("scene_canonicalization_bytes", scene_json_bytes)

    scene_structures = {
        scene_ref: closure.json_structures[scene_ref]
        for scene_ref in exact_used_scene_refs
    }
    scene_prepared_bytes = sum(
        SCENE_PREPARED_BASE_BYTES
        + len(scene_raws[scene_ref])
        + structure.nodes * SCENE_PREPARED_JSON_NODE_BYTES
        + structure.containers * SCENE_PREPARED_JSON_CONTAINER_BYTES
        + structure.container_entries * SCENE_PREPARED_JSON_ENTRY_BYTES
        + structure.string_expansion_bytes
        for scene_ref, structure in scene_structures.items()
    )
    scene_preparation_operations = sum(
        len(scene_raws[scene_ref]) * 2
        + structure.nodes * 2
        + structure.container_entries
        for scene_ref, structure in scene_structures.items()
    )
    # Reserve parsed-object retention and lexical/canonical work before json.loads.
    budget.reserve("scene_prepared_bytes", scene_prepared_bytes)
    budget.reserve(
        "scene_preparation_operations", scene_preparation_operations
    )

    validated_scenes: dict[
        str, tuple[dict[str, object], renderer.OverlaySceneMetrics]
    ] = {}
    scene_points = 0
    scene_paths = 0
    scene_segments = 0
    scene_lots = 0
    semantic_prepared_bytes = 0
    for scene_ref in exact_used_scene_refs:
        scene, metrics = renderer.validate_overlay_scene_metrics(
            graph.json(scene_ref, "overlay-scene", "candidate overlay scene")
        )
        if scene["font_ref"] != font_ref:
            raise ValueError("candidate overlay scene does not use the rooted font")
        validated_scenes[scene_ref] = (scene, metrics)
        scene_points += metrics.point_count
        scene_paths += metrics.path_count
        scene_segments += metrics.segment_count
        scene_lots += metrics.lot_count
        semantic_prepared_bytes += (
            metrics.point_count * SCENE_PREPARED_POINT_BYTES
            + metrics.path_count * SCENE_PREPARED_PATH_BYTES
            + metrics.segment_count * SCENE_PREPARED_SEGMENT_BYTES
            + metrics.lot_count * SCENE_PREPARED_LOT_BYTES
        )
    semantic_preparation_operations = (
        scene_points * 8
        + scene_paths * 4
        + scene_segments * 4
        + scene_lots * 4
    )
    budget.reserve("scene_points", scene_points)
    budget.reserve("scene_paths", scene_paths)
    budget.reserve("scene_segments", scene_segments)
    budget.reserve("scene_lots", scene_lots)
    budget.reserve("scene_prepared_bytes", semantic_prepared_bytes)
    budget.reserve(
        "scene_preparation_operations", semantic_preparation_operations
    )
    scene_prepared_bytes += semantic_prepared_bytes
    scene_preparation_operations += semantic_preparation_operations

    prepared_scenes = {
        scene_ref: renderer._prepare_validated_overlay_scene(
            scene, scene_ref, font, metrics
        )
        for scene_ref, (scene, metrics) in validated_scenes.items()
    }
    clip_segments = 0
    for info in frame_infos.values():
        prepared = prepared_scenes[info["scene_ref"]]
        info["prepared"] = prepared
        clip_segments += renderer.frame_clip_segment_bound(prepared) * 2
    budget.reserve("clip_segments", clip_segments)
    budget.reserve("fraction_operations", clip_segments * 24)

    catalog_ref = roots["imagery_catalog_ref"]
    catalog_document = graph.json(
        catalog_ref, "imagery-catalog", "candidate imagery catalog"
    )
    declared_cells = catalog_document.get("cells")
    if not isinstance(declared_cells, dict) or not declared_cells:
        raise ValueError("imagery catalog cell map is empty or malformed")
    # The replay cap is checked before content-addressing any individual cell.
    budget.reserve("cells", len(declared_cells))
    budget.reserve("seam_cells", len(declared_cells))
    catalog = imagery.validate_imagery_catalog_document(catalog_document)
    cells = catalog["cells"]
    if set(cells) != referenced_cell_ids:
        extras = sorted(set(cells) - referenced_cell_ids)
        missing = sorted(referenced_cell_ids - set(cells))
        raise ValueError(
            "candidate imagery catalog must equal the exact frame cell union: "
            f"frame-unused={extras[:3]} missing={missing[:3]}"
        )
    for frame_key, info in frame_infos.items():
        recipe = info["recipe"]
        frame_cells = {cell_id: cells[cell_id] for cell_id in recipe["cells"]}
        renderer._validate_frame_recipe_prepared(
            recipe,
            frame_cells,
            info["prepared"],
            envelope_validated=True,
            cells_validated=True,
        )

    expected_source_refs = {
        cell["acquisition"]["source_descriptor_ref"] for cell in cells.values()
    }
    acquisition_manifest, source_products = _validate_acquisition_manifest(
        graph,
        roots["acquisition_manifest_ref"],
        baseline,
        roots,
        expected_source_refs,
        prepared_manifest=acquisition_header,
    )

    unique_payload_refs = {
        cell["encoding"]["payload_ref"] for cell in cells.values()
    }
    decoded_rgb_retention_bytes = (
        len(unique_payload_refs) * imagery.DECODED_RGB_LENGTH
    )
    budget.reserve("qrgb_decode_bytes", decoded_rgb_retention_bytes)
    # decoded_cache is keyed by payload reference and owns one independently
    # allocated DecodedQrgb.rgb value per key. Equal decoded content therefore
    # does not deduplicate retained allocation.
    budget.reserve("retained_rgb_bytes", decoded_rgb_retention_bytes)

    source_response_bytes = 0
    source_response_pixels = 0
    seen_responses = set()
    for product in source_products.values():
        source = product["descriptor"]
        response = source["response"]
        response_key = (
            response["response_bytes_ref"], response["width"], response["height"]
        )
        if response_key not in seen_responses:
            seen_responses.add(response_key)
            source_response_bytes += response["response_byte_length"]
            source_response_pixels += response["width"] * response["height"]
    transform_input_pixels = sum(
        source_products[cell["acquisition"]["source_descriptor_ref"]][
            "descriptor"
        ]["response"]["width"]
        * source_products[cell["acquisition"]["source_descriptor_ref"]][
            "descriptor"
        ]["response"]["height"]
        for cell in cells.values()
    )
    budget.reserve("source_response_bytes", source_response_bytes)
    budget.reserve("source_response_pixels", source_response_pixels)
    budget.reserve("source_transform_input_pixels", transform_input_pixels)
    budget.reserve(
        "source_transform_taps", len(cells) * imagery.CELL_PIXEL_COUNT * 4
    )

    lossy_count = sum(
        cell["quality_receipt_ref"] is not None for cell in cells.values()
    )
    cell_windows = lossy_count * (
        (imagery.CELL_WIDTH + imagery.QUALITY_POLICY["ssim_window_side"] - 1)
        // imagery.QUALITY_POLICY["ssim_window_side"]
    ) * (
        (imagery.CELL_HEIGHT + imagery.QUALITY_POLICY["ssim_window_side"] - 1)
        // imagery.QUALITY_POLICY["ssim_window_side"]
    )
    frame_windows = sum(
        ((info["recipe"]["output"]["width"]
          + imagery.QUALITY_POLICY["ssim_window_side"] - 1)
         // imagery.QUALITY_POLICY["ssim_window_side"])
        * ((info["recipe"]["output"]["height"]
            + imagery.QUALITY_POLICY["ssim_window_side"] - 1)
           // imagery.QUALITY_POLICY["ssim_window_side"])
        for info in frame_infos.values()
    )
    quality_edge_scans = (
        (len(cells) + lossy_count) * imagery.CELL_PIXEL_COUNT + output_pixels
    )
    budget.reserve("quality_windows", cell_windows + frame_windows)
    budget.reserve("quality_edge_scans", quality_edge_scans)

    coordinates = {
        (cell["grid"]["matrix"], cell["grid"]["x"], cell["grid"]["y"])
        for cell in cells.values()
    }
    seam_count = sum(
        1
        for matrix, x, y in coordinates
        for neighbour in ((matrix, x + 1, y), (matrix, x, y + 1))
        if neighbour in coordinates
    )
    seam_luma_work = seam_count * imagery.CELL_WIDTH * 4
    budget.reserve("seam_luma_work", seam_luma_work)

    png_compressed_bytes = 0
    maximum_png_compressed_bytes = 0
    for frame_key in frames:
        ref = baseline.object_refs.get(baseline.frame_sources[frame_key])
        if ref is None:
            raise ValueError("baseline frame object is unavailable")
        png_compressed_bytes += ref.length
        maximum_png_compressed_bytes = max(
            maximum_png_compressed_bytes, ref.length
        )
    budget.reserve("object_reads", len(frames))
    budget.reserve("object_read_bytes", png_compressed_bytes)
    budget.reserve("png_compressed_bytes", png_compressed_bytes)
    baseline_decode_pixels = sum(
        width * height for width, height in baseline.frame_dimensions.values()
    )
    budget.reserve("png_decode_bytes", baseline_decode_pixels * 3)

    metadata_prepared_bytes = (
        4096
        + baseline.logical_objects * BASELINE_METADATA_OBJECT_BYTES
        + baseline.leaf_objects * BASELINE_METADATA_OBJECT_BYTES
        + baseline.frame_count * BASELINE_METADATA_FRAME_BYTES
        + baseline.adjudications * BASELINE_METADATA_DECISION_BYTES
        + len(closure.records) * RUNTIME_METADATA_OBJECT_BYTES
        + len(frame_infos) * RUNTIME_METADATA_FRAME_BYTES
        + len(cells) * RUNTIME_METADATA_CELL_BYTES
        + len(source_products) * RUNTIME_METADATA_SOURCE_BYTES
    )
    budget.reserve("metadata_prepared_bytes", metadata_prepared_bytes)
    maximum_frame_pixels = max(
        width * height for width, height in baseline.frame_dimensions.values()
    )
    frame_working_bytes = (
        maximum_frame_pixels * FRAME_WORKING_BYTES_PER_PIXEL
        + maximum_png_compressed_bytes * 2
    )
    qrgb_decode_working_bytes = 2 * imagery.DECODED_RGB_LENGTH
    working_memory_bytes = max(
        CELL_VALIDATION_WORKING_BYTES,
        frame_working_bytes,
        qrgb_decode_working_bytes,
    )
    budget.reserve("working_memory_bytes", working_memory_bytes)
    peak_retained_bytes = (
        budget.usage["retained_raw_bytes"]
        + budget.usage["non_scene_prepared_bytes"]
        + budget.usage["metadata_prepared_bytes"]
        + budget.usage["scene_prepared_bytes"]
        + budget.usage["retained_rgb_bytes"]
        + working_memory_bytes
    )
    budget.reserve("peak_retained_bytes", peak_retained_bytes)
    retention_preflight = {
        "candidate_raw_bytes": budget.usage["retained_raw_bytes"],
        "non_scene_prepared_bytes": budget.usage[
            "non_scene_prepared_bytes"
        ],
        "metadata_prepared_bytes": budget.usage[
            "metadata_prepared_bytes"
        ],
        "scene_prepared_bytes": budget.usage["scene_prepared_bytes"],
        "retained_cell_rgb_allocations": len(unique_payload_refs),
        "retained_cell_rgb_bytes": budget.usage["retained_rgb_bytes"],
        "qrgb_decode_temporary_bytes": qrgb_decode_working_bytes,
        "working_memory_bytes": working_memory_bytes,
        "maximum_baseline_png_bytes": maximum_png_compressed_bytes,
        "peak_retained_bytes": peak_retained_bytes,
        "semantics": (
            "conservative simultaneous raw, parsed JSON, baseline/runtime "
            "metadata, prepared scene, one independently allocated decoded "
            "RGB value per retained payload reference, and one streamed frame "
            "or cell-decode working set; equal decoded RGB hashes do not merge "
            "payload-keyed cache allocations"
        ),
    }

    canonical_json_operations = budget.usage["canonical_json_operations"]
    non_render_operations = (
        budget.usage["object_read_bytes"]
        + canonical_json_operations
        + source_response_pixels
        + transform_input_pixels
        + budget.usage["source_transform_taps"]
        + budget.usage["qrgb_decode_bytes"]
        + (cell_windows + frame_windows) * 64
        + quality_edge_scans
        + seam_luma_work
        + scene_preparation_operations
        + baseline_decode_pixels * 3
    )
    budget.reserve(
        "total_operations_planned",
        non_render_operations + render_operation_budget,
    )

    execution_counts: dict[str, int] = {}
    candidate_rgbs: dict[str, bytes] = {}
    reference_rgbs: dict[str, bytes] = {}
    decoded_cache: dict[str, imagery.DecodedQrgb] = {}
    transform_cache: dict[str, tuple[str, str, str]] = {}
    lossless_cells = 0
    lossy_cells = 0
    replayed_quality_receipts = 0
    nonzero_cell_quality_products = 0
    for cell_id, cell in cells.items():
        matrix = cell["grid"]["matrix"]
        payload_ref = cell["encoding"]["payload_ref"]
        payload = graph.require(payload_ref, "cell-payload", "cell payload")
        graph.require_matrix(payload_ref, matrix, "cell payload", exact=True)
        if payload_ref not in decoded_cache:
            decoded_cache[payload_ref] = imagery.decode_qrgb_payload(payload)
            execution_counts["qrgb_decodes"] = (
                execution_counts.get("qrgb_decodes", 0) + 1
            )
            execution_counts["qrgb_decoded_rgb_bytes"] = (
                execution_counts.get("qrgb_decoded_rgb_bytes", 0)
                + imagery.DECODED_RGB_LENGTH
            )

        acquisition = cell["acquisition"]
        source_descriptor_ref = acquisition["source_descriptor_ref"]
        product = source_products.get(source_descriptor_ref)
        if product is None:
            raise ValueError(
                "cell source descriptor is absent from acquisition manifest"
            )
        source_descriptor = product["descriptor"]
        response_ref = source_descriptor["response"]["response_bytes_ref"]

        reference_ref = cell["reference_rgb_ref"]
        reference_category = (
            "source-response-bytes"
            if reference_ref == response_ref else "reference-rgb"
        )
        reference = graph.require(
            reference_ref, reference_category, "cell reference RGB"
        )
        graph.require_matrix(reference_ref, matrix, "cell reference RGB")
        if len(reference) != imagery.DECODED_RGB_LENGTH:
            raise ValueError("cell reference RGB has wrong exact length")
        mask_ref = cell["critical_mask_ref"]
        mask = graph.require(mask_ref, "critical-mask", "cell critical mask")
        graph.require_matrix(mask_ref, matrix, "cell critical mask")
        mask_recipe_ref = cell["critical_mask_recipe_ref"]
        mask_recipe = graph.json(
            mask_recipe_ref,
            "critical-mask-recipe",
            "cell critical-mask recipe",
        )
        graph.require_matrix(mask_recipe_ref, matrix, "cell critical-mask recipe")

        if acquisition["clean_source_assertion_ref"] != source_descriptor[
                "clean_source_assertion_ref"]:
            raise ValueError("cell clean-source assertion binding mismatch")
        graph.require_matrix(
            source_descriptor_ref, matrix, "cell source descriptor"
        )
        rgb_ref = source_descriptor["conversion"]["rgb_bytes_ref"]
        graph.require_matrix(response_ref, matrix, "cell source response bytes")
        graph.require_matrix(rgb_ref, matrix, "cell source RGB bytes")
        transform_ref = acquisition["source_transform_receipt_ref"]
        graph.require_matrix(
            transform_ref, matrix, "cell source transform", exact=True
        )
        transform = graph.json(
            transform_ref,
            "source-transform-receipt",
            "cell source transform",
        )
        transform_identity = (
            source_descriptor_ref, reference_ref, transform_ref,
        )
        previous_transform = transform_cache.get(transform_ref)
        if previous_transform is None:
            imagery._validate_source_transform_receipt_from_validated_source(
                transform,
                source_descriptor,
                product["rgb"],
                reference,
                _execution_counts=execution_counts,
            )
            transform_cache[transform_ref] = transform_identity
        elif previous_transform != transform_identity:
            raise ValueError("reused source transform has conflicting identity")
        if (transform["source_descriptor_ref"] != source_descriptor_ref
                or transform["reference_rgb_sha256"] != reference_ref
                or transform["matrix"] != cell["grid"]["matrix"]
                or transform["x"] != cell["grid"]["x"]
                or transform["y"] != cell["grid"]["y"]):
            raise ValueError("cell source transform does not bind its cell")

        receipt_ref = cell["quality_receipt_ref"]
        if receipt_ref is None:
            receipt = None
            lossless_cells += 1
        else:
            receipt = graph.json(
                receipt_ref, "quality-receipt", "cell quality receipt"
            )
            graph.require_matrix(
                receipt_ref, matrix, "cell quality receipt", exact=True
            )
            lossy_cells += 1
            replayed_quality_receipts += 1
        candidate_rgb = imagery._validate_prepared_imagery_cell(
            cell,
            payload,
            receipt,
            reference_rgb=reference,
            critical_mask=mask,
            critical_mask_recipe=mask_recipe,
            decoded_qrgb=decoded_cache[payload_ref],
            _execution_counts=execution_counts,
        )
        if candidate_rgb != reference:
            nonzero_cell_quality_products += 1
        reference_rgbs[cell_id] = reference
        candidate_rgbs[cell_id] = candidate_rgb

    cache_execution_closure = _exact_cache_execution_closure(
        len(cells), len(unique_payload_refs), execution_counts
    )

    seam_ref = catalog["seam_quality_receipt_ref"]
    seam = graph.json(
        seam_ref, "seam-quality-receipt", "catalog seam quality receipt"
    )
    imagery.validate_seam_quality_receipt(
        seam,
        cells=cells,
        reference_rgbs=reference_rgbs,
        candidate_rgbs=candidate_rgbs,
        _execution_counts=execution_counts,
        _cells_validated=True,
    )

    render_operations = 0
    maximum_frame_operations = 0
    aggregate_counters: dict[str, int] = {}
    fidelity_receipts = 0
    nonzero_frame_fidelity_products = 0
    baseline_png_hashes: set[str] = set()
    candidate_rgb_hashes: set[str] = set()
    for frame_key in sorted(frame_infos):
        info = frame_infos[frame_key]
        entry = info["entry"]
        recipe = info["recipe"]
        frame_cells = {cell_id: cells[cell_id] for cell_id in recipe["cells"]}
        frame_max_cell_mode = max(
            cell["encoding"]["max_abs_channel_error"]
            for cell in frame_cells.values()
        )
        frame_candidates = {
            cell_id: candidate_rgbs[cell_id] for cell_id in recipe["cells"]
        }
        frame_references = {
            cell_id: reference_rgbs[cell_id] for cell_id in recipe["cells"]
        }
        work = renderer.RenderWork()
        reference_frame, candidate_frame = renderer._render_validated_frame_pair(
            recipe,
            frame_cells,
            frame_candidates,
            frame_references,
            info["prepared"],
            work=work,
            verify_output=True,
            identities_validated=True,
        )
        candidate_hash = imagery.sha256_bytes(candidate_frame)
        if (candidate_hash != entry["rendered_rgb_sha256"]
                or candidate_hash != recipe["output"]["rgb_sha256"]):
            raise ValueError("candidate frame actual RGB hash binding failed")
        source_png_sha256 = entry["source_png_sha256"]
        baseline_png_hashes.add(source_png_sha256)
        width, height, baseline_rgb = _baseline_frame_rgb(baseline, frame_key)
        execution_counts["png_decodes"] = (
            execution_counts.get("png_decodes", 0) + 1
        )
        execution_counts["png_decode_pixels"] = (
            execution_counts.get("png_decode_pixels", 0) + width * height
        )
        execution_counts["png_decoded_rgb_bytes"] = (
            execution_counts.get("png_decoded_rgb_bytes", 0)
            + width * height * 3
        )
        baseline_ref = baseline.object_refs[source_png_sha256]
        execution_counts["png_compressed_bytes_read"] = (
            execution_counts.get("png_compressed_bytes_read", 0)
            + baseline_ref.length
        )
        if (width != recipe["output"]["width"]
                or height != recipe["output"]["height"]):
            raise ValueError("candidate frame dimensions differ from baseline")
        if reference_frame != baseline_rgb:
            raise ValueError(
                "candidate clean-source reconstruction does not exactly match baseline RGB"
            )
        recomputed_metrics = imagery.measure_raster_quality(
            baseline_rgb,
            candidate_frame,
            width,
            height,
            _execution_counts=execution_counts,
            _execution_scope="frame",
        )
        candidate_rgb_hashes.add(candidate_hash)
        if recomputed_metrics["max_abs_channel_error"] > 0:
            nonzero_frame_fidelity_products += 1
        fidelity = graph.json(
            info["fidelity_ref"],
            "frame-fidelity-receipt",
            "frame fidelity receipt",
        )
        renderer.validate_frame_fidelity_receipt(
            fidelity,
            frame_key=frame_key,
            recipe_ref=info["recipe_ref"],
            source_png_sha256=entry["source_png_sha256"],
            reference_rgb=baseline_rgb,
            candidate_rgb=candidate_frame,
            width=width,
            height=height,
            max_cell_mode=frame_max_cell_mode,
            _recomputed_metrics=recomputed_metrics,
        )
        fidelity_receipts += 1
        report = work.report()
        render_operations += report["operations"]
        maximum_frame_operations = max(
            maximum_frame_operations, report["operations"]
        )
        for name, count in report["counters"].items():
            aggregate_counters[name] = aggregate_counters.get(name, 0) + count
        execution_counts["renderer_frame_pairs"] = (
            execution_counts.get("renderer_frame_pairs", 0) + 1
        )
        # Baseline PNG RGB and both frame products are intentionally streamed.
        # No cross-frame cache may retain or suppress a required decode/scan.
        del baseline_rgb, reference_frame, candidate_frame, recomputed_metrics

    if aggregate_counters.get("fraction_operations", 0) > budget.usage[
            "fraction_operations"]:
        raise ValueError("actual Fraction work exceeded its prevalidated bound")
    execution_counts["renderer_operations"] = render_operations
    execution_counts["renderer_fraction_operations"] = aggregate_counters.get(
        "fraction_operations", 0
    )
    expected_quality_work = {
        "cell_full_raster_scans": lossy_count,
        "seam_edge_scans": seam_count,
        "frame_full_raster_scans": len(frames),
    }
    executed_quality_work = {
        "cell_full_raster_scans": execution_counts.get(
            "cell_quality_scans", 0
        ),
        "seam_edge_scans": execution_counts.get("seam_scans", 0),
        "frame_full_raster_scans": execution_counts.get(
            "frame_quality_scans", 0
        ),
    }
    quality_omissions = {
        name: expected - executed_quality_work[name]
        for name, expected in expected_quality_work.items()
    }
    if any(value != 0 for value in quality_omissions.values()):
        raise ValueError(
            f"candidate quality replay omitted exact work: {quality_omissions}"
        )

    prompt_ref = roots["prompt_recipe_ref"]
    _validate_prompt_recipe(graph, prompt_ref)
    packet_ref = roots["packet_corpus_ref"]
    _validate_packet_corpus(graph, packet_ref, baseline, prompt_ref)
    authority_ref = roots["structured_authority_ref"]
    authority = _validate_structured_authority(
        graph, authority_ref, baseline, packet_ref, prompt_ref
    )
    graph.close()
    return ({
        "frame_parity": len(frames),
        "decision_parity": len(authority["decisions"]),
        "frame_cell_references": sum(
            len(info["recipe"]["cells"]) for info in frame_infos.values()
        ),
        "replayed_seams": seam_count,
        "lossless_cells": lossless_cells,
        "lossy_cells": lossy_cells,
        "replayed_quality_receipts": replayed_quality_receipts,
        "replayed_seam_receipts": 1,
        "replayed_frame_fidelity_receipts": fidelity_receipts,
        "quality_coverage": {
            "expected": expected_quality_work,
            "executed": executed_quality_work,
            "omissions": quality_omissions,
            "unit_threshold_coverage": "separate-focused-tests-not-this-replay",
            "aggregate_discrimination": {
                "nonzero_cell_quality_products": (
                    nonzero_cell_quality_products
                ),
                "nonzero_frame_fidelity_products": (
                    nonzero_frame_fidelity_products
                ),
            },
        },
        "prepared_product_reuse": {
            "candidate_json_documents": len(closure.json_structures) + 1,
            "precomputed_json_structures_reused": len(graph.parsed) + 1,
            "catalog_validated_cells": len(cells),
            "frame_recipe_envelope_validations": len(frame_infos),
            "frame_recipe_binding_validations": len(frame_infos),
            "prepared_source_products": len(source_products),
            "prepared_overlay_scenes": len(prepared_scenes),
            "semantics": (
                "each candidate JSON document receives one structural scan and "
                "one decode; immutable catalog cells, source products, scenes, "
                "and frame bindings are then reused. Deliberate semantic "
                "self-hash and receipt recomputations remain charged as "
                "canonical serialization/hash work"
            ),
        },
        "executed_work": {
            "semantics": (
                "exact completed deterministic counters; renderer operations "
                "include its Fraction counter, which is also published "
                "separately and must not be summed again"
            ),
            "counters": dict(sorted(execution_counts.items())),
            "cache_closure": cache_execution_closure,
        },
        "retention_preflight": retention_preflight,
        "scene_usage": {
            "rooted_scene_refs": list(rooted_scene_refs),
            "used_scene_refs": exact_used_scene_refs,
            "scene_count": len(exact_used_scene_refs),
            "point_count": scene_points,
            "path_count": scene_paths,
            "segment_count": scene_segments,
            "lot_count": scene_lots,
            "raw_json_bytes": scene_json_bytes,
            "prepared_memory_bound_bytes": scene_prepared_bytes,
            "canonicalization_bytes_processed": scene_json_bytes,
            "preparation_operation_bound": scene_preparation_operations,
        },
        "render_work": {
            "operations_executed": render_operations,
            "operation_bound_planned": render_operation_budget,
            "maximum_frame_operations_executed": maximum_frame_operations,
            "distinct_baseline_png_hashes": len(baseline_png_hashes),
            "distinct_candidate_rgb_products": len(candidate_rgb_hashes),
            "png_decodes_executed": execution_counts.get("png_decodes", 0),
            "full_raster_fidelity_scans_executed": execution_counts.get(
                "frame_quality_scans", 0
            ),
            "counters": dict(sorted(aggregate_counters.items())),
        },
    }, acquisition_manifest)


def _require_fresh_candidate_json_components(
        audit: imagery.CanonicalJsonWorkAudit,
) -> None:
    """Require one untouched, exactly partitioned candidate audit allocation."""
    components = audit.reservation_components
    phase_components = audit.phase_components
    expected_components = CANDIDATE_SCOPE_CANONICAL_JSON_RESERVATIONS
    expected_phases = CANDIDATE_SCOPE_CANONICAL_JSON_PHASE_COMPONENTS
    owned_components = set(expected_components)
    owned_phase_components = {
        phase: component for phase, component in phase_components.items()
        if component in owned_components
    }
    report = audit.report()
    execution = report["component_execution"]
    phases = report["phases"]
    if (any(components.get(name) != reservation
            for name, reservation in expected_components.items())
            or owned_phase_components != expected_phases
            or any(
                execution[name]["executed_byte_operations"] != 0
                for name in expected_components
                if name in execution
            )
            or any(
                sum(phases.get(phase, {}).get(
                    "calls_by_boundary", {}
                ).values()) != 0
                for phase in expected_phases
            )):
        raise ValueError(
            "active canonical JSON audit lacks fresh exact candidate and "
            "approval-baseline component reservations, phase mappings, or "
            "zero-call state"
        )


def evaluate_candidate(
        baseline: BaselineMeasurement, candidate: object,
        object_root: Path | None = None, require_ratio: int = 10,
) -> dict[str, object]:
    """Hash, close, replay, render, and evaluate one candidate object DAG."""
    require_ratio = _positive_ratio(require_ratio)
    if object_root is None:
        raise ValueError("candidate evaluation requires a supplied local object root")
    json_work = imagery._ACTIVE_CANONICAL_JSON_WORK_AUDIT.get()
    json_token = None
    if json_work is None:
        json_work = imagery.CanonicalJsonWorkAudit(
            MAX_CANDIDATE_SCOPE_CANONICAL_JSON_OPERATIONS,
            reservation_scope="candidate-api",
            reservation_components=(
                CANDIDATE_SCOPE_CANONICAL_JSON_RESERVATIONS
            ),
            phase_components=(
                CANDIDATE_SCOPE_CANONICAL_JSON_PHASE_COMPONENTS
            ),
        )
        json_token = imagery._ACTIVE_CANONICAL_JSON_WORK_AUDIT.set(json_work)
    else:
        _require_fresh_candidate_json_components(json_work)
    audit = _FilesystemControlAudit(
        phase="candidate-admission", governs_real_achievement=True
    )
    token = _ACTIVE_FILESYSTEM_CONTROL_AUDIT.set(audit)
    try:
        byte_gate = baseline.total_bytes // require_ratio
        if (baseline.proof_sha256 == BEAR_CREEK_PROOF_SHA256
                and require_ratio == 10):
            byte_gate = BEAR_CREEK_HARD_BYTES
        budget = ReplayBudget()
        budget.reserve(
            "canonical_json_operations",
            MAX_REPLAY_CANONICAL_JSON_OPERATIONS,
        )
        with imagery.canonical_json_work_phase("candidate-load"):
            closure = _load_candidate_closure(
                baseline, candidate, Path(object_root), budget, byte_gate
            )
        with imagery.canonical_json_work_phase("candidate-replay"):
            replay, acquisition_manifest = _replay_candidate(
                baseline, closure, budget
            )
        with imagery.canonical_json_work_phase("acquisition-approval"):
            approvals = _load_acquisition_approval_registry()
            approval = _matching_acquisition_approval(
                closure, baseline, acquisition_manifest, approvals
            )
        approval_entry_count = len(approvals)
        filesystem_controls = audit.report()
        registry_matched = approval is not None
        admitted = (
            registry_matched
            and filesystem_controls["complete_for_real_achievement"]
        )
        result = {
            "status": "PASS" if admitted else "FIXTURE_ONLY",
            "baseline_proof_sha256": baseline.proof_sha256,
            "required_ratio": require_ratio,
            "byte_gate": byte_gate,
            "candidate": dict(closure.accounting),
            **replay,
            "acquisition_approval": {
                "registry_sha256": PINNED_ACQUISITION_APPROVAL_REGISTRY_SHA256,
                "approved_entry_count": approval_entry_count,
                "matched": registry_matched,
                "admitted_for_real_achievement": admitted,
                "candidate_declared_provenance": closure.provenance_kind,
                "required_declared_provenance": (
                    REAL_ACQUISITION_PROVENANCE_KIND
                ),
                "acquisition_manifest_ref": closure.manifest["roots"][
                    "acquisition_manifest_ref"
                ],
                "source_set_sha256": acquisition_manifest["source_set_sha256"],
            },
            "filesystem_controls": filesystem_controls,
            "replay_budget": budget.report(json_work),
            "real_resolution_validation_plan": (
                renderer.real_resolution_budget_plan(
                    baseline.frame_count, 1024, 1024
                )
            ),
        }
        if admitted:
            result["achieved_ratio_milli"] = (
                baseline.total_bytes * 1000
                // closure.accounting["cold_total_bytes"]
            )
            result["acquisition_approval"]["review_id"] = approval[
                "review_id"
            ]
        elif registry_matched:
            result["non_achievement_reason"] = (
                "a repository-pinned reviewed acquisition approval matched, "
                "but required filesystem controls are incomplete; a reviewed "
                "descriptor-bound Darwin/Linux ACL policy, including rejection "
                "of unrecognized Linux ACL backends, and complete inspection "
                "are required for a real compression achievement"
            )
        else:
            result["non_achievement_reason"] = (
                "no repository-pinned reviewed acquisition approval matches "
                "this candidate manifest, declared real-acquisition "
                "provenance, and complete source lineage; content consistency "
                "alone proves no real-world origin or compression achievement"
            )
        return result
    finally:
        if json_token is not None:
            imagery._ACTIVE_CANONICAL_JSON_WORK_AUDIT.reset(json_token)
        _ACTIVE_FILESYSTEM_CONTROL_AUDIT.reset(token)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument(
        "--baseline-proof", default=BEAR_CREEK_PROOF_SHA256,
        help="compact proof-v4 manifest SHA-256",
    )
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--object-root", type=Path)
    parser.add_argument("--require-ratio", default="10")
    return parser


def _whole_run_filesystem_controls(
        cli_controls: Mapping[str, object],
        candidate_controls: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Aggregate distinct phase audits without changing the PASS governor."""
    cli_phase = str(cli_controls["phase"])
    phases = {cli_phase: dict(cli_controls)}
    if candidate_controls is not None:
        candidate_phase = str(candidate_controls["phase"])
        if candidate_phase in phases:
            raise ValueError(
                f"duplicate filesystem control phase {candidate_phase!r}"
            )
        phases[candidate_phase] = dict(candidate_controls)
    whole_run_complete = all(
        phase["status"] == "complete" for phase in phases.values()
    )
    return {
        "phase": "whole-run",
        "status": "complete" if whole_run_complete else "degraded",
        "whole_run_complete": whole_run_complete,
        "real_achievement_governing_phase": (
            None if candidate_controls is None
            else candidate_controls["phase"]
        ),
        "real_achievement_controls_complete": (
            None if candidate_controls is None
            else candidate_controls["complete_for_real_achievement"]
        ),
        "semantics": (
            "whole_run_complete aggregates every emitted phase; only the "
            "candidate-admission phase governs PASS because an approved "
            "candidate re-derives all admission evidence inside that phase"
        ),
        "phases": phases,
    }


def main(argv: list[str] | None = None) -> int:
    """Own one top-level full-run ledger; refuse execution under a parent."""
    if imagery._ACTIVE_CANONICAL_JSON_WORK_AUDIT.get() is not None:
        sys.stdout.write(
            '{\n "error": "benchmark main cannot run inside an active '
            'canonical JSON audit; it owns the full-run ledger",\n '
            '"status": "FAIL"\n}\n'
        )
        return 2
    args = _parser().parse_args(argv)
    audit = _FilesystemControlAudit(
        phase="cli-baseline-and-descriptor", governs_real_achievement=False
    )
    token = _ACTIVE_FILESYSTEM_CONTROL_AUDIT.set(audit)
    json_work = imagery.CanonicalJsonWorkAudit(
        MAX_FULL_SCOPE_CANONICAL_JSON_OPERATIONS,
        reservation_scope="cli-full-run",
        reservation_components=FULL_SCOPE_CANONICAL_JSON_RESERVATIONS,
        phase_components=FULL_SCOPE_CANONICAL_JSON_PHASE_COMPONENTS,
    )
    json_token = imagery._ACTIVE_CANONICAL_JSON_WORK_AUDIT.set(json_work)
    output: dict[str, object]
    exit_code = 0
    try:
        require_ratio = _cli_ratio(args.require_ratio)
        with imagery.canonical_json_work_phase("initial-proof-v4-baseline"):
            baseline = measure_proof_v4(args.registry, args.baseline_proof)
        report: dict[str, object] = {
            "status": "BASELINE_ONLY" if args.candidate is None else "PENDING",
            "baseline": baseline.report(require_ratio),
        }
        if args.candidate is not None:
            if args.object_root is None:
                raise ValueError("--object-root is required with --candidate")
            raw = _bounded_regular(
                args.candidate.absolute(), MANIFEST_MAX_BYTES,
                "candidate benchmark descriptor",
            )
            with imagery.canonical_json_work_phase("candidate-descriptor"):
                candidate = _strict_json(
                    raw, "candidate benchmark descriptor"
                )
            report["candidate_gate"] = evaluate_candidate(
                baseline, candidate, args.object_root, require_ratio
            )
            report["status"] = report["candidate_gate"]["status"]
        cli_controls = audit.report()
        candidate_controls = (
            None if args.candidate is None
            else report["candidate_gate"]["filesystem_controls"]
        )
        report["filesystem_controls"] = _whole_run_filesystem_controls(
            cli_controls, candidate_controls
        )
        report["canonical_json_work"] = json_work.report()
        output = report
    except (
            OSError, ValueError, AssertionError, AttributeError, TypeError,
            KeyError, NameError, RecursionError, MemoryError, OverflowError,
    ) as error:
        output = {"status": "FAIL", "error": str(error)}
        exit_code = 2
    finally:
        imagery._ACTIVE_CANONICAL_JSON_WORK_AUDIT.reset(json_token)
        _ACTIVE_FILESYSTEM_CONTROL_AUDIT.reset(token)
    print(_pretty_json(output).decode(), end="")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
