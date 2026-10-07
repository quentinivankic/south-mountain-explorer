"""Exact rooted-closure and non-achievement gates for proof-v5 scaling."""
from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import resource
import shutil
import stat
import struct
import sys
import time
import zlib
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
TOOLS = HERE / "parking-adjud" / "tools"
FONT_PATH = HERE / "test-fixtures" / "parking-overlay-font-v1.json"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import benchmark_evidence_scaling as benchmark  # noqa: E402
import frame_renderer as renderer  # noqa: E402
import imagery_objects as imagery  # noqa: E402


FULL_RESOLUTION_ENV = "PARKING_FULL_RESOLUTION_BENCHMARK"
FULL_RESOLUTION_ISOLATION_ENV = "PARKING_BENCHMARK_ISOLATED_HOST"
FULL_RESOLUTION_HARNESS_TIMEOUT_ENV = (
    "PARKING_BENCHMARK_HARNESS_TIMEOUT_SECONDS"
)
FULL_RESOLUTION_LOCK_PATH = Path(
    "/tmp/trekdex-proof-v5-full-resolution.lock"
)
FULL_RESOLUTION_PEAK_RSS_BUDGET_BYTES = 256 * 1024 * 1024
FULL_RESOLUTION_OBSERVATIONAL_EXPECTATION_SECONDS = 20 * 60
PRIOR_CONTENDED_ATTEMPT = {
    "classification": "contended-timed-out-no-receipt",
    "external_timeout_seconds": 30 * 60,
    "accepted_receipt": False,
    "included_in_clean_measurement": False,
}


def _benchmark_refusal(classification: str, error: str) -> None:
    pytest.fail(json.dumps({
        "status": "FAIL",
        "run_classification": classification,
        "accepted_receipt": False,
        "error": error,
    }, sort_keys=True), pytrace=False)


@contextmanager
def _isolated_benchmark_lock(lock_path: Path = FULL_RESOLUTION_LOCK_PATH):
    if os.environ.get(FULL_RESOLUTION_ISOLATION_ENV) != "1":
        _benchmark_refusal(
            "precondition-refused-no-receipt",
            f"{FULL_RESOLUTION_ISOLATION_ENV}=1 is required",
        )
    raw_timeout = os.environ.get(FULL_RESOLUTION_HARNESS_TIMEOUT_ENV, "")
    if not raw_timeout.isdecimal():
        _benchmark_refusal(
            "precondition-refused-no-receipt",
            f"{FULL_RESOLUTION_HARNESS_TIMEOUT_ENV} must be an integer",
        )
    harness_timeout = int(raw_timeout)
    if harness_timeout <= FULL_RESOLUTION_OBSERVATIONAL_EXPECTATION_SECONDS:
        _benchmark_refusal(
            "precondition-refused-no-receipt",
            "harness timeout must exceed the observational expectation",
        )

    flags = (
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as error:
        _benchmark_refusal(
            "lock-unavailable-refused-no-receipt",
            f"benchmark lock could not be opened: {error}",
        )
        return
    try:
        value = os.fstat(descriptor)
        if (not stat.S_ISREG(value.st_mode) or value.st_uid != os.geteuid()
                or stat.S_IMODE(value.st_mode) != 0o600
                or value.st_nlink != 1):
            _benchmark_refusal(
                "precondition-refused-no-receipt",
                "benchmark lock is not a single-link owner-only file",
            )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            _benchmark_refusal(
                "contended-refused-no-receipt",
                "another full-resolution benchmark owns the host lock",
            )
        yield {
            "isolated_host_assertion": {
                "environment": FULL_RESOLUTION_ISOLATION_ENV,
                "value": "1",
            },
            "exclusive_host_lock": {
                "path": str(lock_path),
                "acquired": True,
            },
            "declared_harness_timeout_seconds": harness_timeout,
            "observational_expectation_seconds": (
                FULL_RESOLUTION_OBSERVATIONAL_EXPECTATION_SECONDS
            ),
        }
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _peak_rss_bytes() -> int:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak if sys.platform == "darwin" else peak * 1024


def _hash(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return (
        len(data).to_bytes(4, "big") + kind + data
        + (zlib.crc32(kind + data) & 0xFFFFFFFF).to_bytes(4, "big")
    )


def _encode_rgb_png(
        width: int, height: int, rgb: bytes, *, identity: str | None = None,
) -> bytes:
    assert len(rgb) == width * height * 3
    scanlines = b"".join(
        b"\x00" + rgb[row * width * 3:(row + 1) * width * 3]
        for row in range(height)
    )
    identity_chunk = (
        b"" if identity is None
        else _png_chunk(b"tEXt", b"frame-key\x00" + identity.encode("ascii"))
    )
    return (
        benchmark.PNG_SIGNATURE
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + identity_chunk
        + _png_chunk(b"IDAT", zlib.compress(scanlines, 9))
        + _png_chunk(b"IEND", b"")
    )


def _reencode_qrgb(payload: bytes, level: int) -> bytes:
    """Return another valid zlib representation of the same qRGB product."""
    fields = list(imagery.QRGB_HEADER.unpack_from(payload))
    decoded = imagery.decode_qrgb(payload)
    compressed = zlib.compress(decoded, level)
    fields[8] = len(compressed)
    result = imagery.QRGB_HEADER.pack(*fields) + compressed
    assert imagery.decode_qrgb(result) == decoded
    return result


def _paint_header_text_variant(
        framed_background: bytes, width: int, height: int, text: str,
        font: renderer.BitmapFont,
) -> bytes:
    """Paint only text over a frame already rendered with blank header glyphs."""
    result = bytearray(framed_background)
    style = renderer.OVERLAY_STYLE["header"]
    scale = style["font_scale"]
    cursor_x = style["x_px"]
    origin_y = style["y_px"]
    color = style["text_rgba"]
    for character in text:
        rows = font.rows(character)
        for row_index, bits in enumerate(rows):
            for column in range(font.width):
                if bits & (1 << (font.width - 1 - column)):
                    for offset_y in range(scale):
                        for offset_x in range(scale):
                            renderer._blend(
                                result,
                                width,
                                height,
                                cursor_x + column * scale + offset_x,
                                origin_y + row_index * scale + offset_y,
                                color,
                            )
        cursor_x += font.advance * scale
    return bytes(result)


def _write_candidate_object(root: Path, raw: bytes) -> str:
    digest = hashlib.sha256(raw).hexdigest()
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o700)
    object_directory = root / benchmark.CANDIDATE_OBJECT_DIRECTORY
    object_directory.mkdir(exist_ok=True)
    object_directory.chmod(0o700)
    path = benchmark.candidate_object_path(root, digest)
    path.parent.mkdir(exist_ok=True)
    path.parent.chmod(0o700)
    if not path.exists():
        path.write_bytes(raw)
    path.chmod(0o600)
    return digest


def _write_baseline_object(root: Path, raw: bytes) -> tuple[str, benchmark.LogicalRef]:
    digest = hashlib.sha256(raw).hexdigest()
    relative = benchmark._canonical_object_path(digest)
    path = root.joinpath(*relative.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    leaf = benchmark.LeafRef(relative, len(raw), digest)
    return digest, benchmark.LogicalRef(len(raw), digest, (leaf,))


def _add_object(
        root: Path, records: dict[str, dict[str, object]], raw: bytes,
        category: str, matrix: str | None = None,
) -> str:
    digest = _write_candidate_object(root, raw)
    record = {"length": len(raw), "category": category, "matrix": matrix}
    prior = records.setdefault(digest, record)
    if prior != record:
        raise AssertionError("test fixture reused bytes under conflicting categories")
    return digest


def _build_rooted_fixture(
        tmp_path: Path, *, forged_frame_hash: bool = False,
        changed_decision: bool = False,
        provider: str = "synthetic-fixture",
        provenance_kind: str = "synthetic-fixture",
        capture_id: str = "fixture-cell-0-0",
        non_identity_source: bool = False,
) -> tuple[benchmark.BaselineMeasurement, dict[str, object], Path, dict[str, object]]:
    object_root = tmp_path / "candidate-objects"
    object_root.mkdir(parents=True)
    records: dict[str, dict[str, object]] = {}
    font_bytes = FONT_PATH.read_bytes()
    font_ref = _add_object(object_root, records, font_bytes, "bitmap-font")
    font = renderer.parse_bitmap_font(font_bytes)

    matrix = "detail-v1"
    reference = bytes((50, 50, 50)) * imagery.CELL_PIXEL_COUNT
    bounds = list(imagery.grid_cell_bounds(matrix, 0, 0))
    if non_identity_source:
        source_width = imagery.CELL_WIDTH + 1
        source_height = imagery.CELL_HEIGHT + 1
        source_bytes = bytes((50, 50, 50)) * (
            source_width * source_height
        )
        source_bounds = [bounds[0], bounds[1], bounds[2] + 1, bounds[3] + 1]
    else:
        source_width = imagery.CELL_WIDTH
        source_height = imagery.CELL_HEIGHT
        source_bytes = reference
        source_bounds = bounds
    source = imagery.build_source_descriptor(
        source_bytes,
        provider=provider,
        product="fixture-rgb",
        layer="proof-v5-cell",
        capture_id=capture_id,
        capture_version="v1",
        retrieved_at="2026-10-05T12:00:00Z",
        width=source_width,
        height=source_height,
        bounds_um=source_bounds,
    )
    source_bytes_ref = _add_object(
        object_root, records, source_bytes, "source-response-bytes", matrix
    )
    assert source_bytes_ref == source["response"]["response_bytes_ref"]
    assert source_bytes_ref == source["conversion"]["rgb_bytes_ref"]
    reference_ref = imagery.sha256_bytes(reference)
    if reference_ref != source_bytes_ref:
        assert _add_object(
            object_root, records, reference, "reference-rgb", matrix
        ) == reference_ref
    source_raw = imagery.canonical_json(source)
    source_ref = _add_object(
        object_root, records, source_raw, "source-descriptor", matrix
    )
    clean_assertion = imagery.build_clean_source_assertion(source)
    clean_assertion_ref = _add_object(
        object_root,
        records,
        imagery.canonical_json(clean_assertion),
        "clean-source-assertion",
        matrix,
    )
    clean, transform = imagery.build_source_transform_receipt(
        source, source_bytes, matrix, 0, 0
    )
    assert clean == reference
    transform_ref = _add_object(
        object_root,
        records,
        imagery.canonical_json(transform),
        "source-transform-receipt",
        matrix,
    )
    mask_recipe = imagery.build_critical_mask_recipe(reference)
    mask_recipe_ref = _add_object(
        object_root,
        records,
        imagery.canonical_json(mask_recipe),
        "critical-mask-recipe",
        matrix,
    )
    mask = imagery.derive_critical_mask(reference)
    mask_ref = _add_object(object_root, records, mask, "critical-mask", matrix)
    candidate_rgb = imagery.quantize_rgb(reference, 1)
    quality = imagery.build_quality_receipt(reference, candidate_rgb, mask, 1)
    assert quality["passed"] is True
    quality_raw = imagery.canonical_json(quality)
    quality_ref = _add_object(
        object_root, records, quality_raw, "quality-receipt", matrix
    )
    payload = imagery.encode_qrgb(reference, 1)
    payload_ref = _add_object(
        object_root, records, payload, "cell-payload", matrix
    )
    acquisition = {
        "source_descriptor_ref": source_ref,
        "clean_source_assertion_ref": clean_assertion_ref,
        "source_transform_receipt_ref": transform_ref,
    }
    cell = imagery.build_imagery_cell(
        matrix,
        0,
        0,
        acquisition,
        payload,
        quality_raw,
        reference_rgb=reference,
        critical_mask=mask,
        critical_mask_recipe=imagery.canonical_json(mask_recipe),
    )
    assert cell["encoding"]["payload_ref"] == payload_ref
    assert cell["quality_receipt_ref"] == quality_ref
    assert cell["critical_mask_ref"] == mask_ref
    assert cell["critical_mask_recipe_ref"] == mask_recipe_ref
    cell_id = imagery.imagery_cell_id(cell)
    cells = {cell_id: cell}
    references = {cell_id: reference}
    candidates = {cell_id: candidate_rgb}
    seam = imagery.build_seam_quality_receipt(cells, references, candidates)
    seam_ref = _add_object(
        object_root,
        records,
        imagery.canonical_json(seam),
        "seam-quality-receipt",
    )
    catalog = imagery.build_imagery_catalog(cells, seam)
    assert catalog["seam_quality_receipt_ref"] == seam_ref
    catalog_ref = _add_object(
        object_root,
        records,
        imagery.canonical_json(catalog),
        "imagery-catalog",
    )

    span = imagery.matrix_cell_span_um(matrix)
    scene = {
        "version": 2,
        "kind": renderer.OVERLAY_SCENE_KIND,
        "source": {
            "dossier_sha256": _hash("dossier"),
            "geom_sha256": _hash("geom"),
            "generation_sha256": _hash("generation"),
        },
        "font_ref": font_ref,
        "style": copy.deepcopy(renderer.OVERLAY_STYLE),
        "trails": [[[10_000_000, 40_000_000], [90_000_000, 60_000_000]]],
        "lots": [
            {
                "fid": 1,
                "rings_um": [],
                "point_um": [span // 2, span // 2],
            },
            {
                "fid": 2,
                "rings_um": [],
                "point_um": [span // 2, span // 2],
            },
        ],
    }
    assert font.sha256 == font_ref
    scene_raw = imagery.canonical_json(scene)
    scene_ref = _add_object(object_root, records, scene_raw, "overlay-scene")
    viewport = [20_000_000, 20_000_000, 84_000_000, 84_000_000]
    recipe = {
        "version": 2,
        "kind": renderer.FRAME_RECIPE_KIND,
        "fid": 1,
        "rung": "z2",
        "viewport_um": viewport,
        "output": {"width": 64, "height": 64, "rgb_sha256": "0" * 64},
        "matrix": matrix,
        "cells": [cell_id],
        "resampler": renderer.FRAME_RESAMPLER,
        "overlay_scene_ref": scene_ref,
        "style": renderer.OVERLAY_STYLE_ID,
        "header": "#1Z2",
    }
    prepared = renderer.prepare_overlay_scene(scene, font_bytes)
    reference_frame, rendered_frame = renderer.render_prepared_frame_pair(
        recipe,
        cells,
        candidates,
        references,
        prepared,
        verify_output=False,
    )
    recipe["output"]["rgb_sha256"] = hashlib.sha256(rendered_frame).hexdigest()
    recipe_raw = imagery.canonical_json(recipe)
    assert renderer.frame_recipe_sha256(
        recipe, cells, scene, font_bytes
    ) == hashlib.sha256(recipe_raw).hexdigest()
    recipe_ref = _add_object(
        object_root, records, recipe_raw, "frame-recipe", matrix
    )

    baseline_png = _encode_rgb_png(64, 64, reference_frame)
    baseline_root = tmp_path / "baseline-objects"
    baseline_root.mkdir(parents=True)
    source_png_ref, source_logical = _write_baseline_object(
        baseline_root, baseline_png
    )
    proof_sha = _hash("synthetic baseline proof")
    decision = {
        "verdict": "KEEP",
        "confidence": "strong",
        "exists": "yes",
        "public": "yes",
        "serves": "yes",
    }
    baseline = benchmark.BaselineMeasurement(
        proof_sha256=proof_sha,
        manifest_length=1000,
        adjudications=1,
        frame_count=1,
        image_bytes=len(baseline_png),
        total_bytes=100_000_000,
        logical_objects=1,
        leaf_objects=1,
        largest_blob=len(baseline_png),
        by_rung={"z1": 0, "z2": len(baseline_png), "z3": 0},
        frame_sources={"1/z2": source_png_ref},
        frame_dimensions={"1/z2": (64, 64)},
        decisions={"1": decision},
        object_root=baseline_root,
        object_refs={source_png_ref: source_logical},
    )
    fidelity = renderer.build_frame_fidelity_receipt(
        "1/z2",
        recipe_ref,
        source_png_ref,
        reference_frame,
        rendered_frame,
        64,
        64,
        1,
    )
    assert fidelity["passed"] is True
    fidelity_ref = _add_object(
        object_root,
        records,
        imagery.canonical_json(fidelity),
        "frame-fidelity-receipt",
    )
    frame_set = {
        "version": 1,
        "kind": "parking-proof-v5-frame-set",
        "baseline_proof_sha256": proof_sha,
        "frames": {
            "1/z2": {
                "fid": 1,
                "rung": "z2",
                "recipe_ref": recipe_ref,
                "source_png_sha256": source_png_ref,
                "rendered_rgb_sha256": (
                    "f" * 64 if forged_frame_hash
                    else hashlib.sha256(rendered_frame).hexdigest()
                ),
                "fidelity_receipt_ref": fidelity_ref,
            },
        },
    }
    frame_set_ref = _add_object(
        object_root, records, imagery.canonical_json(frame_set), "frame-set"
    )
    prompt = {
        "version": 1,
        "kind": "parking-proof-v5-prompt-recipe",
        "policy_id": "parking-proof-v5-shadow-v1",
        "required_rungs": ["z1", "z2", "z3"],
        "decision_fields": [
            "verdict", "confidence", "exists", "public", "serves",
        ],
    }
    prompt_ref = _add_object(
        object_root, records, imagery.canonical_json(prompt), "prompt-recipe"
    )
    packet_corpus = {
        "version": 1,
        "kind": "parking-proof-v5-packet-corpus",
        "baseline_proof_sha256": proof_sha,
        "prompt_recipe_ref": prompt_ref,
        "packets": {"1": {"fid": 1, "frame_keys": ["1/z2"]}},
    }
    packet_ref = _add_object(
        object_root,
        records,
        imagery.canonical_json(packet_corpus),
        "packet-corpus",
    )
    authority_decision = {"fid": 1, **copy.deepcopy(decision)}
    if changed_decision:
        authority_decision["confidence"] = "leaning"
    authority = {
        "version": 1,
        "kind": "parking-proof-v5-structured-authority",
        "baseline_proof_sha256": proof_sha,
        "packet_corpus_ref": packet_ref,
        "prompt_recipe_ref": prompt_ref,
        "decisions": {"1": authority_decision},
    }
    authority_ref = _add_object(
        object_root,
        records,
        imagery.canonical_json(authority),
        "structured-authority",
    )
    candidate_roots = {
        "imagery_catalog_ref": catalog_ref,
        "overlay_scene_refs": [scene_ref],
        "bitmap_font_ref": font_ref,
        "frame_set_ref": frame_set_ref,
        "packet_corpus_ref": packet_ref,
        "prompt_recipe_ref": prompt_ref,
        "structured_authority_ref": authority_ref,
    }
    acquisition_sources = [{
        "source_descriptor_ref": source_ref,
        "response_bytes_ref": source_bytes_ref,
        "rgb_bytes_ref": source_bytes_ref,
        "clean_source_assertion_ref": clean_assertion_ref,
    }]
    acquisition_manifest = {
        "version": 1,
        "kind": "parking-scenepack-acquisition-manifest",
        "baseline_proof_sha256": proof_sha,
        "candidate_roots": copy.deepcopy(candidate_roots),
        "sources": acquisition_sources,
        "source_set_sha256": imagery.sha256_json(acquisition_sources),
    }
    acquisition_manifest_ref = _add_object(
        object_root,
        records,
        imagery.canonical_json(acquisition_manifest),
        "acquisition-manifest",
    )
    manifest = {
        "version": 1,
        "kind": "parking-scenepack-proof",
        "baseline_proof_sha256": proof_sha,
        "roots": {
            "acquisition_manifest_ref": acquisition_manifest_ref,
            **candidate_roots,
        },
        "objects": dict(sorted(records.items())),
        "object_closure_sha256": hashlib.sha256(
            imagery.canonical_json(dict(sorted(records.items())))
        ).hexdigest(),
        "warm_cache_refs": [source_bytes_ref],
    }
    manifest_raw = imagery.canonical_json(manifest)
    manifest_ref = _write_candidate_object(object_root, manifest_raw)
    descriptor = {
        "version": 2,
        "kind": "parking-evidence-scaling-candidate",
        "baseline_proof_sha256": proof_sha,
        "provenance": {
            "kind": provenance_kind,
            "label": "one-frame rooted contract fixture",
        },
        "proof_manifest_ref": manifest_ref,
        "proof_manifest_length": len(manifest_raw),
    }
    details = {
        "manifest": manifest,
        "manifest_raw": manifest_raw,
        "manifest_ref": manifest_ref,
        "records": records,
        "rendered_frame": rendered_frame,
        "reference_frame": reference_frame,
        "quality_ref": quality_ref,
        "source_ref": source_ref,
        "source_bytes_ref": source_bytes_ref,
        "reference_ref": reference_ref,
        "clean_assertion_ref": clean_assertion_ref,
        "acquisition_manifest": acquisition_manifest,
        "acquisition_manifest_ref": acquisition_manifest_ref,
        "cell_id": cell_id,
        "catalog_ref": catalog_ref,
        "recipe_ref": recipe_ref,
        "scene": scene,
        "scene_ref": scene_ref,
        "frame_set_ref": frame_set_ref,
        "frame_set": frame_set,
        "recipe": recipe,
        "fidelity": fidelity,
        "fidelity_ref": fidelity_ref,
        "packet_corpus": packet_corpus,
        "packet_ref": packet_ref,
        "authority": authority,
        "authority_ref": authority_ref,
        "catalog": catalog,
        "cell": cell,
    }
    return baseline, descriptor, object_root, details


def _build_150_frame_fixture(
        tmp_path: Path, *, output_side: int = 32,
        representative: bool = False,
        equivalent_payload_pair: bool = False,
) -> tuple[
        benchmark.BaselineMeasurement, dict[str, object], Path,
        dict[str, object],
]:
    object_root = tmp_path / "candidate-objects"
    object_root.mkdir(parents=True)
    records: dict[str, dict[str, object]] = {}
    font_bytes = FONT_PATH.read_bytes()
    font_ref = _add_object(object_root, records, font_bytes, "bitmap-font")

    cells = {}
    references = {}
    candidates = {}
    acquisition_sources = []
    cell_by_rung: dict[str, list[str]] = {}
    for rung, matrix, quick_value, full_value in (
        ("z1", "context-v1", 40, 42),
        ("z2", "detail-v1", 50, 51),
        ("z3", "confirm-v1", 60, 60),
    ):
        cell_by_rung[rung] = []
        x_values = (
            (0, 1)
            if representative or (equivalent_payload_pair and rung == "z2")
            else (0,)
        )
        for x in x_values:
            value = full_value if representative else quick_value
            if not (equivalent_payload_pair and rung == "z2"):
                value += x * 3
            reference_buffer = bytearray(
                bytes((value, value, value)) * imagery.CELL_PIXEL_COUNT
            )
            if representative and rung == "z2" and x == 0:
                # A bounded patch quantizes 50 -> 51, producing a nonzero,
                # passing full-resolution cell and frame fidelity delta.
                for row in range(384, 448):
                    for column in range(192, 320):
                        offset = (row * imagery.CELL_WIDTH + column) * 3
                        reference_buffer[offset:offset + 3] = bytes((50, 50, 50))
            reference = bytes(reference_buffer)
            destination_bounds = list(
                imagery.grid_cell_bounds(matrix, x, 0)
            )
            non_aligned = representative and rung == "z1" and x == 0
            if non_aligned:
                source_width = imagery.CELL_WIDTH + 1
                source_height = imagery.CELL_HEIGHT + 1
                source_bytes = bytes((value, value, value)) * (
                    source_width * source_height
                )
                source_bounds = [
                    destination_bounds[0],
                    destination_bounds[1],
                    destination_bounds[2] + 1,
                    destination_bounds[3] + 1,
                ]
            else:
                source_width = imagery.CELL_WIDTH
                source_height = imagery.CELL_HEIGHT
                source_bytes = reference
                source_bounds = destination_bounds
            source = imagery.build_source_descriptor(
                source_bytes,
                provider="synthetic-fixture",
                product="bounded-aggregate-rgb",
                layer=rung,
                capture_id=f"aggregate-{rung}-{x}",
                capture_version="v1",
                retrieved_at="2026-10-05T12:00:00Z",
                width=source_width,
                height=source_height,
                bounds_um=source_bounds,
            )
            response_ref = _add_object(
                object_root,
                records,
                source_bytes,
                "source-response-bytes",
                matrix,
            )
            reference_ref = imagery.sha256_bytes(reference)
            if reference_ref != response_ref:
                assert _add_object(
                    object_root,
                    records,
                    reference,
                    "reference-rgb",
                    matrix,
                ) == reference_ref
            source_ref = _add_object(
                object_root,
                records,
                imagery.canonical_json(source),
                "source-descriptor",
                matrix,
            )
            assertion = imagery.build_clean_source_assertion(source)
            assertion_ref = _add_object(
                object_root,
                records,
                imagery.canonical_json(assertion),
                "clean-source-assertion",
                matrix,
            )
            transformed, transform = imagery.build_source_transform_receipt(
                source, source_bytes, matrix, x, 0
            )
            assert transformed == reference
            transform_ref = _add_object(
                object_root,
                records,
                imagery.canonical_json(transform),
                "source-transform-receipt",
                matrix,
            )
            mask_recipe = imagery.build_critical_mask_recipe(reference)
            mask_recipe_ref = _add_object(
                object_root,
                records,
                imagery.canonical_json(mask_recipe),
                "critical-mask-recipe",
                matrix,
            )
            mask = imagery.derive_critical_mask(reference)
            mask_ref = _add_object(
                object_root, records, mask, "critical-mask", None
            )
            mode = 1 if representative else 0
            candidate_rgb = imagery.quantize_rgb(reference, mode)
            quality_raw = None
            if representative:
                quality = imagery.build_quality_receipt(
                    reference, candidate_rgb, mask, mode
                )
                quality_raw = imagery.canonical_json(quality)
                _add_object(
                    object_root, records, quality_raw, "quality-receipt", matrix
                )
            payload = imagery.encode_qrgb(reference, mode)
            if equivalent_payload_pair and rung == "z2" and x == 1:
                payload = _reencode_qrgb(payload, 1)
            payload_ref = _add_object(
                object_root, records, payload, "cell-payload", matrix
            )
            cell = imagery.build_imagery_cell(
                matrix,
                x,
                0,
                {
                    "source_descriptor_ref": source_ref,
                    "clean_source_assertion_ref": assertion_ref,
                    "source_transform_receipt_ref": transform_ref,
                },
                payload,
                quality_raw,
                reference_rgb=reference,
                critical_mask=mask,
                critical_mask_recipe=mask_recipe,
            )
            assert cell["encoding"]["payload_ref"] == payload_ref
            assert cell["critical_mask_ref"] == mask_ref
            assert cell["critical_mask_recipe_ref"] == mask_recipe_ref
            cell_id = imagery.imagery_cell_id(cell)
            cells[cell_id] = cell
            references[cell_id] = reference
            candidates[cell_id] = candidate_rgb
            cell_by_rung[rung].append(cell_id)
            acquisition_sources.append({
                "source_descriptor_ref": source_ref,
                "response_bytes_ref": response_ref,
                "rgb_bytes_ref": response_ref,
                "clean_source_assertion_ref": assertion_ref,
            })

    seam = imagery.build_seam_quality_receipt(cells, references, candidates)
    seam_ref = _add_object(
        object_root,
        records,
        imagery.canonical_json(seam),
        "seam-quality-receipt",
    )
    catalog = imagery.build_imagery_catalog(cells, seam)
    assert catalog["seam_quality_receipt_ref"] == seam_ref
    catalog_ref = _add_object(
        object_root,
        records,
        imagery.canonical_json(catalog),
        "imagery-catalog",
    )

    point = [30_000_000, 30_000_000]
    if representative:
        ring = [
            [28_000_000, 28_000_000],
            [32_000_000, 28_000_000],
            [32_000_000, 32_000_000],
            [28_000_000, 32_000_000],
        ]
        trails = [
            [
                [-imagery.WEB_MERCATOR_LIMIT_UM, 16_000_000],
                [imagery.WEB_MERCATOR_LIMIT_UM, 16_000_000],
            ],
            [
                [0, 8_000_000],
                [64_000_000, 40_000_000],
                [160_000_000, 8_000_000],
            ],
        ]
        lots = [
            {"fid": fid, "rings_um": [copy.deepcopy(ring)], "point_um": None}
            for fid in range(1, 51)
        ]
    else:
        trails = []
        lots = [
            {"fid": fid, "rings_um": [], "point_um": point}
            for fid in range(1, 51)
        ]
    scene = {
        "version": 2,
        "kind": renderer.OVERLAY_SCENE_KIND,
        "source": {
            "dossier_sha256": _hash("aggregate-dossier"),
            "geom_sha256": _hash("aggregate-geom"),
            "generation_sha256": _hash("aggregate-generation"),
        },
        "font_ref": font_ref,
        "style": copy.deepcopy(renderer.OVERLAY_STYLE),
        "trails": trails,
        "lots": lots,
    }
    scene_ref = _add_object(
        object_root, records, imagery.canonical_json(scene), "overlay-scene"
    )
    prepared = renderer.prepare_overlay_scene(scene, font_bytes)
    viewport_by_rung = {
        rung: (
            [0, 0, 2 * imagery.matrix_cell_span_um(matrix),
             imagery.matrix_cell_span_um(matrix)]
            if (representative
                or (equivalent_payload_pair and rung == "z2"))
            else [20_000_000, 20_000_000, 40_000_000, 40_000_000]
        )
        for rung, matrix in renderer.RUNG_MATRIX.items()
    }

    baseline_root = tmp_path / "baseline-objects"
    baseline_root.mkdir(parents=True)
    baseline_frames: dict[str, str] = {}
    baseline_refs: dict[str, benchmark.LogicalRef] = {}
    baseline_bytes_by_rung = {rung: 0 for rung in renderer.RUNG_MATRIX}
    template_frames: dict[str, tuple[bytes, bytes]] = {}
    quick_fidelity_metrics: dict[str, dict[str, int]] = {}
    for rung, matrix in renderer.RUNG_MATRIX.items():
        cell_ids = cell_by_rung[rung]
        template_header = "   " if representative else "#"
        if representative:
            assert all(
                not any(prepared.font.rows(char)) for char in template_header
            )
        template = {
            "version": 2,
            "kind": renderer.FRAME_RECIPE_KIND,
            "fid": 1,
            "rung": rung,
            "viewport_um": viewport_by_rung[rung],
            "output": {
                "width": output_side,
                "height": output_side,
                "rgb_sha256": "0" * 64,
            },
            "matrix": matrix,
            "cells": cell_ids,
            "resampler": renderer.FRAME_RESAMPLER,
            "overlay_scene_ref": scene_ref,
            "style": renderer.OVERLAY_STYLE_ID,
            "header": template_header,
        }
        reference_frame, candidate_frame = renderer.render_prepared_frame_pair(
            template,
            {cell_id: cells[cell_id] for cell_id in cell_ids},
            {cell_id: candidates[cell_id] for cell_id in cell_ids},
            {cell_id: references[cell_id] for cell_id in cell_ids},
            prepared,
            verify_output=False,
        )
        template_frames[rung] = (reference_frame, candidate_frame)
        if not representative:
            png = _encode_rgb_png(output_side, output_side, reference_frame)
            digest, logical = _write_baseline_object(baseline_root, png)
            baseline_frames[rung] = digest
            baseline_refs[digest] = logical
            quick_fidelity_metrics[rung] = imagery.measure_raster_quality(
                reference_frame, candidate_frame, output_side, output_side
            )

    proof_sha = _hash("bounded aggregate baseline proof")
    frames = {}
    frame_sources = {}
    candidate_product_hashes = set()
    for fid in range(1, 51):
        for rung, matrix in renderer.RUNG_MATRIX.items():
            cell_ids = cell_by_rung[rung]
            frame_key = f"{fid}/{rung}"
            if representative:
                header = f"{fid:02d}{rung[-1]}"
                reference_frame = _paint_header_text_variant(
                    template_frames[rung][0],
                    output_side,
                    output_side,
                    header,
                    prepared.font,
                )
                rendered = _paint_header_text_variant(
                    template_frames[rung][1],
                    output_side,
                    output_side,
                    header,
                    prepared.font,
                )
                png = _encode_rgb_png(
                    output_side,
                    output_side,
                    reference_frame,
                    identity=frame_key,
                )
                source_ref, logical = _write_baseline_object(
                    baseline_root, png
                )
                baseline_refs[source_ref] = logical
                fidelity_metrics = imagery.measure_raster_quality(
                    reference_frame, rendered, output_side, output_side
                )
            else:
                header = "#"
                reference_frame, rendered = template_frames[rung]
                source_ref = baseline_frames[rung]
                logical = baseline_refs[source_ref]
                fidelity_metrics = quick_fidelity_metrics[rung]
            baseline_bytes_by_rung[rung] += logical.length
            rendered_hash = hashlib.sha256(rendered).hexdigest()
            candidate_product_hashes.add(rendered_hash)
            recipe = {
                "version": 2,
                "kind": renderer.FRAME_RECIPE_KIND,
                "fid": fid,
                "rung": rung,
                "viewport_um": viewport_by_rung[rung],
                "output": {
                    "width": output_side,
                    "height": output_side,
                    "rgb_sha256": rendered_hash,
                },
                "matrix": matrix,
                "cells": cell_ids,
                "resampler": renderer.FRAME_RESAMPLER,
                "overlay_scene_ref": scene_ref,
                "style": renderer.OVERLAY_STYLE_ID,
                "header": header,
            }
            recipe_ref = _add_object(
                object_root,
                records,
                imagery.canonical_json(recipe),
                "frame-recipe",
                matrix,
            )
            fidelity = renderer._build_frame_fidelity_receipt_from_metrics(
                frame_key,
                recipe_ref,
                source_ref,
                reference_frame,
                rendered,
                output_side,
                output_side,
                max(
                    cells[cell_id]["encoding"]["max_abs_channel_error"]
                    for cell_id in cell_ids
                ),
                fidelity_metrics,
            )
            assert fidelity["passed"] is True
            fidelity_ref = _add_object(
                object_root,
                records,
                imagery.canonical_json(fidelity),
                "frame-fidelity-receipt",
            )
            frames[frame_key] = {
                "fid": fid,
                "rung": rung,
                "recipe_ref": recipe_ref,
                "source_png_sha256": source_ref,
                "rendered_rgb_sha256": rendered_hash,
                "fidelity_receipt_ref": fidelity_ref,
            }
            frame_sources[frame_key] = source_ref
            if representative:
                del reference_frame, rendered, png, fidelity_metrics

    if representative:
        assert len(set(frame_sources.values())) == 150
        assert len(candidate_product_hashes) == 150

    frame_set = {
        "version": 1,
        "kind": "parking-proof-v5-frame-set",
        "baseline_proof_sha256": proof_sha,
        "frames": frames,
    }
    frame_set_ref = _add_object(
        object_root, records, imagery.canonical_json(frame_set), "frame-set"
    )
    prompt = {
        "version": 1,
        "kind": "parking-proof-v5-prompt-recipe",
        "policy_id": "parking-proof-v5-shadow-v1",
        "required_rungs": ["z1", "z2", "z3"],
        "decision_fields": [
            "verdict", "confidence", "exists", "public", "serves",
        ],
    }
    prompt_ref = _add_object(
        object_root, records, imagery.canonical_json(prompt), "prompt-recipe"
    )
    packets = {
        str(fid): {
            "fid": fid,
            "frame_keys": [f"{fid}/z1", f"{fid}/z2", f"{fid}/z3"],
        }
        for fid in range(1, 51)
    }
    packet_corpus = {
        "version": 1,
        "kind": "parking-proof-v5-packet-corpus",
        "baseline_proof_sha256": proof_sha,
        "prompt_recipe_ref": prompt_ref,
        "packets": packets,
    }
    packet_ref = _add_object(
        object_root,
        records,
        imagery.canonical_json(packet_corpus),
        "packet-corpus",
    )
    decision = {
        "verdict": "KEEP",
        "confidence": "strong",
        "exists": "yes",
        "public": "yes",
        "serves": "yes",
    }
    baseline_decisions = {str(fid): copy.deepcopy(decision) for fid in range(1, 51)}
    authority = {
        "version": 1,
        "kind": "parking-proof-v5-structured-authority",
        "baseline_proof_sha256": proof_sha,
        "packet_corpus_ref": packet_ref,
        "prompt_recipe_ref": prompt_ref,
        "decisions": {
            str(fid): {"fid": fid, **copy.deepcopy(decision)}
            for fid in range(1, 51)
        },
    }
    authority_ref = _add_object(
        object_root,
        records,
        imagery.canonical_json(authority),
        "structured-authority",
    )
    candidate_roots = {
        "imagery_catalog_ref": catalog_ref,
        "overlay_scene_refs": [scene_ref],
        "bitmap_font_ref": font_ref,
        "frame_set_ref": frame_set_ref,
        "packet_corpus_ref": packet_ref,
        "prompt_recipe_ref": prompt_ref,
        "structured_authority_ref": authority_ref,
    }
    acquisition_sources.sort(key=lambda item: item["source_descriptor_ref"])
    acquisition_manifest = {
        "version": 1,
        "kind": "parking-scenepack-acquisition-manifest",
        "baseline_proof_sha256": proof_sha,
        "candidate_roots": copy.deepcopy(candidate_roots),
        "sources": acquisition_sources,
        "source_set_sha256": imagery.sha256_json(acquisition_sources),
    }
    acquisition_ref = _add_object(
        object_root,
        records,
        imagery.canonical_json(acquisition_manifest),
        "acquisition-manifest",
    )
    manifest = {
        "version": 1,
        "kind": "parking-scenepack-proof",
        "baseline_proof_sha256": proof_sha,
        "roots": {"acquisition_manifest_ref": acquisition_ref, **candidate_roots},
        "objects": dict(sorted(records.items())),
        "object_closure_sha256": hashlib.sha256(
            imagery.canonical_json(dict(sorted(records.items())))
        ).hexdigest(),
        "warm_cache_refs": [],
    }
    manifest_raw = imagery.canonical_json(manifest)
    manifest_ref = _write_candidate_object(object_root, manifest_raw)
    descriptor = {
        "version": 2,
        "kind": "parking-evidence-scaling-candidate",
        "baseline_proof_sha256": proof_sha,
        "provenance": {
            "kind": "synthetic-fixture",
            "label": (
                "representative synthetic 50 by 3 full-resolution control"
                if representative
                else "bounded synthetic 50 by 3 aggregate fixture"
            ),
        },
        "proof_manifest_ref": manifest_ref,
        "proof_manifest_length": len(manifest_raw),
    }
    baseline = benchmark.BaselineMeasurement(
        proof_sha256=proof_sha,
        manifest_length=1000,
        adjudications=50,
        frame_count=150,
        image_bytes=sum(ref.length for ref in baseline_refs.values()),
        total_bytes=100_000_000,
        logical_objects=len(baseline_refs),
        leaf_objects=len(baseline_refs),
        largest_blob=max(ref.length for ref in baseline_refs.values()),
        by_rung=baseline_bytes_by_rung,
        frame_sources=frame_sources,
        frame_dimensions={
            frame_key: (output_side, output_side) for frame_key in frame_sources
        },
        decisions=baseline_decisions,
        object_root=baseline_root,
        object_refs=baseline_refs,
    )
    details = {
        "cells": cells,
        "references": references,
        "candidates": candidates,
        "manifest": manifest,
    }
    return baseline, descriptor, object_root, details


@pytest.fixture(scope="module")
def bear_baseline():
    return benchmark.measure_proof_v4(
        benchmark.DEFAULT_REGISTRY,
        benchmark.BEAR_CREEK_PROOF_SHA256,
    )


def test_bear_creek_proof_v4_exact_rooted_baseline(bear_baseline):
    baseline = bear_baseline
    assert baseline.proof_sha256 == benchmark.BEAR_CREEK_PROOF_SHA256
    assert baseline.adjudications == 50
    assert baseline.frame_count == 150
    assert set(baseline.frame_sources) == {
        f"{fid}/{rung}"
        for fid in baseline.decisions
        for rung in ("z1", "z2", "z3")
    }
    assert len({
        renderer.parse_frame_key(key) for key in baseline.frame_sources
    }) == 150
    assert set(baseline.frame_dimensions) == set(baseline.frame_sources)
    assert set(baseline.frame_dimensions.values()) == {(1024, 1024)}
    assert baseline.image_bytes == 84_513_188
    assert baseline.by_rung == {
        "z1": 60_270_563,
        "z2": 16_111_609,
        "z3": 8_131_016,
    }
    assert baseline.total_bytes == 88_854_223
    assert baseline.manifest_length == 784_805
    assert baseline.logical_objects == 394
    assert baseline.leaf_objects == 394
    assert baseline.registry_path == benchmark.DEFAULT_REGISTRY.absolute()
    report = baseline.report()
    assert report["blob_count_including_manifest"] == 395
    assert report["other_bytes_including_manifest"] == 4_341_035
    assert report["candidate_byte_gate"] == 8_885_422
    assert report["required_savings_bytes"] == 79_968_801
    assert report["real_resolution_validation_plan"]["total_output_pixels"] == 157_286_400


@pytest.mark.parametrize("invalid_ratio", [0, -1, 1.0, True, "1"])
def test_baseline_report_and_candidate_api_require_positive_integer_ratio(
        bear_baseline, invalid_ratio):
    with pytest.raises(ValueError, match="positive integer"):
        bear_baseline.report(invalid_ratio)
    with pytest.raises(ValueError, match="positive integer"):
        benchmark.evaluate_candidate(
            bear_baseline, {}, require_ratio=invalid_ratio
        )


def test_full_closure_50_by_3_aggregate_replay_is_bounded_and_nonachievement(
        tmp_path, monkeypatch):
    baseline, descriptor, root, _details = _build_150_frame_fixture(tmp_path)
    original_baseline_frame = benchmark._baseline_frame_rgb
    original_quality = imagery.measure_raster_quality
    observed = {
        "decode_calls": 0,
        "frame_quality_calls": 0,
        "live_baseline_products": 0,
        "maximum_live_baseline_products": 0,
    }

    class StreamedBaseline(bytes):
        def __new__(cls, value):
            instance = super().__new__(cls, value)
            observed["live_baseline_products"] += 1
            observed["maximum_live_baseline_products"] = max(
                observed["maximum_live_baseline_products"],
                observed["live_baseline_products"],
            )
            return instance

        def __del__(self):
            observed["live_baseline_products"] -= 1

    def counted_baseline(*args, **kwargs):
        width, height, rgb = original_baseline_frame(*args, **kwargs)
        observed["decode_calls"] += 1
        return width, height, StreamedBaseline(rgb)

    def counted_quality(*args, **kwargs):
        if kwargs.get("_execution_scope") == "frame":
            observed["frame_quality_calls"] += 1
        return original_quality(*args, **kwargs)

    monkeypatch.setattr(benchmark, "_baseline_frame_rgb", counted_baseline)
    monkeypatch.setattr(imagery, "measure_raster_quality", counted_quality)
    result = benchmark.evaluate_candidate(baseline, descriptor, root)
    assert result["status"] == "FIXTURE_ONLY"
    assert result["frame_parity"] == 150
    assert result["decision_parity"] == 50
    assert result["replayed_frame_fidelity_receipts"] == 150
    assert result["acquisition_approval"]["matched"] is False
    assert "achieved_ratio_milli" not in result
    planned = result["replay_budget"]["planned"]
    limits = result["replay_budget"]["limits"]
    executed = result["executed_work"]["counters"]
    assert planned["frames"] == 150
    assert planned["recipes"] == 150
    assert planned["cells"] == 3
    assert planned["output_pixels"] == 150 * 32 * 32
    assert planned["qrgb_decode_bytes"] == 3 * imagery.DECODED_RGB_LENGTH
    assert planned["retained_rgb_bytes"] == 3 * imagery.DECODED_RGB_LENGTH
    assert planned["total_operations_planned"] <= limits[
        "total_operations_planned"
    ]
    assert executed["renderer_operations"] == result["render_work"][
        "operations_executed"
    ]
    assert executed["png_decodes"] == 150
    assert executed["frame_quality_scans"] == 150
    assert executed["source_transform_identity_copies"] == 3
    assert executed.get("source_transform_bilinear_scans", 0) == 0
    assert executed["qrgb_decodes"] == 3
    assert result["executed_work"]["cache_closure"] == {
        "expected": {
            "source_transform_derivations": 3,
            "qrgb_decodes": 3,
        },
        "executed": {
            "source_transform_derivations": 3,
            "qrgb_decodes": 3,
        },
        "omissions": {
            "source_transform_derivations": 0,
            "qrgb_decodes": 0,
        },
        "equations": {
            "source_transform_derivations": (
                "source_transform_identity_copies + "
                "source_transform_bilinear_scans == cell_count"
            ),
            "qrgb_decodes": "qrgb_decodes == unique_payload_refs",
        },
    }
    assert result["render_work"]["distinct_baseline_png_hashes"] == 3
    assert result["quality_coverage"]["omissions"] == {
        "cell_full_raster_scans": 0,
        "seam_edge_scans": 0,
        "frame_full_raster_scans": 0,
    }
    assert observed["decode_calls"] == 150
    assert observed["frame_quality_calls"] == 150
    assert observed["live_baseline_products"] == 0
    assert observed["maximum_live_baseline_products"] == 1


def test_payload_keyed_retention_counts_distinct_zlib_encodings_and_exact_peak(
        tmp_path):
    baseline, descriptor, root, details = _build_150_frame_fixture(
        tmp_path,
        equivalent_payload_pair=True,
    )
    z2_cells = [
        cell for cell in details["cells"].values()
        if cell["grid"]["matrix"] == "detail-v1"
    ]
    assert len(z2_cells) == 2
    payload_refs = {
        cell["encoding"]["payload_ref"] for cell in z2_cells
    }
    decoded_refs = {
        cell["encoding"]["decoded_rgb_sha256"] for cell in z2_cells
    }
    assert len(payload_refs) == 2
    assert len(decoded_refs) == 1
    payloads = [
        benchmark.candidate_object_path(root, payload_ref).read_bytes()
        for payload_ref in sorted(payload_refs)
    ]
    assert payloads[0] != payloads[1]
    assert {imagery.decode_qrgb(payload) for payload in payloads} == {
        details["references"][imagery.imagery_cell_id(z2_cells[0])]
    }

    result = benchmark.evaluate_candidate(baseline, descriptor, root)
    planned = result["replay_budget"]["planned"]
    retention = result["retention_preflight"]
    expected_allocations = 4
    expected_rgb_bytes = expected_allocations * imagery.DECODED_RGB_LENGTH
    assert planned["qrgb_decode_bytes"] == expected_rgb_bytes
    assert planned["retained_rgb_bytes"] == expected_rgb_bytes
    assert retention["retained_cell_rgb_allocations"] == expected_allocations
    assert retention["retained_cell_rgb_bytes"] == expected_rgb_bytes
    assert retention["qrgb_decode_temporary_bytes"] == (
        2 * imagery.DECODED_RGB_LENGTH
    )
    expected_peak = sum(
        retention[name] for name in (
            "candidate_raw_bytes",
            "non_scene_prepared_bytes",
            "metadata_prepared_bytes",
            "scene_prepared_bytes",
            "retained_cell_rgb_bytes",
            "working_memory_bytes",
        )
    )
    assert retention["peak_retained_bytes"] == expected_peak
    assert planned["peak_retained_bytes"] == expected_peak
    assert result["executed_work"]["counters"]["qrgb_decodes"] == 4


def test_150_distinct_product_lane_shape_replays_at_small_resolution(tmp_path):
    baseline, descriptor, root, _details = _build_150_frame_fixture(
        tmp_path, output_side=64, representative=True
    )
    assert len(set(baseline.frame_sources.values())) == 150
    result = benchmark.evaluate_candidate(
        baseline, descriptor, root, require_ratio=1
    )
    render_work = result["render_work"]
    executed = result["executed_work"]["counters"]
    assert render_work["distinct_baseline_png_hashes"] == 150
    assert render_work["distinct_candidate_rgb_products"] == 150
    assert render_work["png_decodes_executed"] == 150
    assert render_work["full_raster_fidelity_scans_executed"] == 150
    assert executed["source_transform_identity_copies"] == 5
    assert executed["source_transform_bilinear_scans"] == 1
    assert executed["source_transform_taps"] == 1_048_576
    assert executed["qrgb_decodes"] == 6
    assert result["executed_work"]["cache_closure"]["expected"] == {
        "source_transform_derivations": 6,
        "qrgb_decodes": 6,
    }
    assert result["executed_work"]["cache_closure"]["executed"] == {
        "source_transform_derivations": 6,
        "qrgb_decodes": 6,
    }
    assert result["executed_work"]["cache_closure"]["omissions"] == {
        "source_transform_derivations": 0,
        "qrgb_decodes": 0,
    }
    assert result["quality_coverage"]["aggregate_discrimination"][
        "nonzero_cell_quality_products"
    ] == 1
    assert result["quality_coverage"]["aggregate_discrimination"][
        "nonzero_frame_fidelity_products"
    ] == 50


def test_isolated_benchmark_precondition_classifies_refusal_and_contention(
        tmp_path, monkeypatch):
    lock_path = tmp_path / "full-resolution.lock"
    monkeypatch.delenv(FULL_RESOLUTION_ISOLATION_ENV, raising=False)
    monkeypatch.setenv(FULL_RESOLUTION_HARNESS_TIMEOUT_ENV, "1800")
    with pytest.raises(
            pytest.fail.Exception, match="precondition-refused-no-receipt"):
        with _isolated_benchmark_lock(lock_path):
            pass

    monkeypatch.setenv(FULL_RESOLUTION_ISOLATION_ENV, "1")
    with _isolated_benchmark_lock(lock_path) as isolation:
        assert isolation["exclusive_host_lock"]["acquired"] is True
        with pytest.raises(
                pytest.fail.Exception, match="contended-refused-no-receipt"):
            with _isolated_benchmark_lock(lock_path):
                pass


def test_isolated_benchmark_lock_open_failure_is_no_receipt(
        tmp_path, monkeypatch):
    monkeypatch.setenv(FULL_RESOLUTION_ISOLATION_ENV, "1")
    monkeypatch.setenv(FULL_RESOLUTION_HARNESS_TIMEOUT_ENV, "1800")

    def unavailable(*_args, **_kwargs):
        raise PermissionError("simulated unusable lock")

    monkeypatch.setattr(os, "open", unavailable)
    with pytest.raises(
            pytest.fail.Exception,
            match="lock-unavailable-refused-no-receipt"):
        with _isolated_benchmark_lock(tmp_path / "unusable.lock"):
            pass


@pytest.mark.skipif(
    os.environ.get(FULL_RESOLUTION_ENV) != "1",
    reason=(
        "explicit isolated slow lane: PARKING_FULL_RESOLUTION_BENCHMARK=1 "
        "PARKING_BENCHMARK_ISOLATED_HOST=1 "
        "PARKING_BENCHMARK_HARNESS_TIMEOUT_SECONDS=1800 "
        "python3 -m pytest -q -s scripts/test_parking_evidence_scaling.py::"
        "test_full_resolution_150_frame_replay_is_measured_under_isolation"
    ),
)
def test_full_resolution_150_frame_replay_is_measured_under_isolation(tmp_path):
    with _isolated_benchmark_lock() as isolation:
        fixture_started = time.perf_counter()
        baseline, descriptor, root, _details = _build_150_frame_fixture(
            tmp_path, output_side=1024, representative=True
        )
        fixture_seconds = time.perf_counter() - fixture_started
        replay_started = time.perf_counter()
        result = benchmark.evaluate_candidate(
            baseline, descriptor, root, require_ratio=1
        )
        replay_seconds = time.perf_counter() - replay_started
        peak_rss_bytes = _peak_rss_bytes()

    assert baseline.proof_sha256 != benchmark.BEAR_CREEK_PROOF_SHA256
    assert descriptor["provenance"]["kind"] == "synthetic-fixture"
    assert result["status"] == "FIXTURE_ONLY"
    assert result["required_ratio"] == 1
    assert "achieved_ratio_milli" not in result
    assert result["frame_parity"] == 150
    assert result["decision_parity"] == 50
    assert result["frame_cell_references"] == 300
    assert result["replayed_seams"] == 3
    quality = result["quality_coverage"]
    assert quality["expected"] == quality["executed"]
    assert quality["omissions"] == {
        "cell_full_raster_scans": 0,
        "seam_edge_scans": 0,
        "frame_full_raster_scans": 0,
    }
    assert quality["unit_threshold_coverage"] == (
        "separate-focused-tests-not-this-replay"
    )
    assert quality["aggregate_discrimination"][
        "nonzero_cell_quality_products"
    ] == 1
    assert quality["aggregate_discrimination"][
        "nonzero_frame_fidelity_products"
    ] == 50
    assert result["lossless_cells"] == 0
    assert result["lossy_cells"] == 6
    assert result["replayed_quality_receipts"] == 6
    assert result["replayed_seam_receipts"] == 1
    assert result["replayed_frame_fidelity_receipts"] == 150

    scene = result["scene_usage"]
    assert scene["rooted_scene_refs"] == scene["used_scene_refs"]
    assert scene["scene_count"] == 1
    assert scene["point_count"] == 205
    assert scene["path_count"] == 52
    assert scene["segment_count"] == 203
    assert scene["lot_count"] == 50
    assert scene["raw_json_bytes"] == 7_168
    assert scene["canonicalization_bytes_processed"] == 7_168
    assert scene["prepared_memory_bound_bytes"] == 153_934
    assert scene["preparation_operation_bound"] == 20_546

    planned = result["replay_budget"]["planned"]
    limits = result["replay_budget"]["limits"]
    executed = result["executed_work"]["counters"]
    assert planned["frames"] == 150
    assert planned["recipes"] == 150
    assert planned["cells"] == 6
    assert planned["seam_cells"] == 6
    assert planned["seam_luma_work"] == 3 * imagery.CELL_WIDTH * 4
    assert planned["quality_windows"] == 2_482_176
    assert limits["quality_windows"] == 3_244_032
    assert planned["output_pixels"] == 150 * 1024 * 1024
    assert planned["render_operation_budget"] == 20_132_659_200
    assert planned["clip_segments"] == 61_800
    assert planned["fraction_operations"] == 1_483_200
    assert planned["source_transform_taps"] == (
        6 * imagery.CELL_PIXEL_COUNT * 4
    )
    assert planned["total_operations_planned"] <= limits[
        "total_operations_planned"
    ]
    assert executed["renderer_operations"] == result["render_work"][
        "operations_executed"
    ]
    assert executed["renderer_operations"] <= planned[
        "render_operation_budget"
    ]
    assert executed["renderer_fraction_operations"] == result[
        "render_work"
    ]["counters"]["fraction_operations"]
    assert executed["png_decodes"] == 150
    assert executed["png_decode_pixels"] == 150 * 1024 * 1024
    assert executed["frame_quality_scans"] == 150
    assert executed["frame_quality_windows"] == 150 * 128 * 128
    assert executed["frame_quality_edge_pixels_scanned"] == (
        150 * 1024 * 1024
    )
    assert executed["cell_quality_scans"] == 6
    assert executed["seam_scans"] == 3
    assert executed["source_transform_identity_copies"] == 5
    assert executed["source_transform_bilinear_scans"] == 1
    assert executed["source_transform_taps"] == 1_048_576
    assert executed["qrgb_decodes"] == 6
    assert result["executed_work"]["cache_closure"]["expected"] == {
        "source_transform_derivations": 6,
        "qrgb_decodes": 6,
    }
    assert result["executed_work"]["cache_closure"]["executed"] == {
        "source_transform_derivations": 6,
        "qrgb_decodes": 6,
    }
    assert result["executed_work"]["cache_closure"]["omissions"] == {
        "source_transform_derivations": 0,
        "qrgb_decodes": 0,
    }
    assert result["render_work"]["distinct_baseline_png_hashes"] == 150
    assert result["render_work"]["distinct_candidate_rgb_products"] == 150
    assert result["render_work"]["png_decodes_executed"] == 150
    assert result["render_work"][
        "full_raster_fidelity_scans_executed"
    ] == 150
    assert result["render_work"]["counters"]["segments_rasterized"] > 0
    assert result["render_work"]["counters"]["clip_boundary_tests"] > 0
    retention = result["retention_preflight"]
    assert retention["peak_retained_bytes"] == planned["peak_retained_bytes"]
    assert retention["peak_retained_bytes"] <= limits["peak_retained_bytes"]
    assert retention["candidate_raw_bytes"] == planned["retained_raw_bytes"]
    assert retention["non_scene_prepared_bytes"] == planned[
        "non_scene_prepared_bytes"
    ]
    assert retention["metadata_prepared_bytes"] == planned[
        "metadata_prepared_bytes"
    ]
    assert peak_rss_bytes <= FULL_RESOLUTION_PEAK_RSS_BUDGET_BYTES
    assert isolation["declared_harness_timeout_seconds"] > isolation[
        "observational_expectation_seconds"
    ]

    receipt = {
        "kind": "synthetic-full-resolution-replay-measurement",
        "frame_count": result["frame_parity"],
        "decision_count": result["decision_parity"],
        "frame_cell_references": result["frame_cell_references"],
        "unique_cell_count": planned["cells"],
        "seam_count": result["replayed_seams"],
        "output_side": 1024,
        "run_classification": "clean-isolated-completed",
        "benchmark_precondition": isolation,
        "timing_observation": {
            "semantics": (
                "observational only; excluded from deterministic proof "
                "soundness and measured after fixture construction"
            ),
            "deterministic_soundness_gate": False,
            "fixture_seconds": round(fixture_seconds, 6),
            "replay_seconds": round(replay_seconds, 6),
        },
        "independent_contended_attempt": PRIOR_CONTENDED_ATTEMPT,
        "peak_rss_bytes": peak_rss_bytes,
        "peak_rss_budget_bytes": FULL_RESOLUTION_PEAK_RSS_BUDGET_BYTES,
        "planned_operations": {
            "render_bound": planned["render_operation_budget"],
            "total_bound": planned["total_operations_planned"],
            "total_limit": limits["total_operations_planned"],
        },
        "executed_work": result["executed_work"],
        "distinct_baseline_png_hashes": result["render_work"][
            "distinct_baseline_png_hashes"
        ],
        "distinct_candidate_rgb_products": result["render_work"][
            "distinct_candidate_rgb_products"
        ],
        "quality_coverage": quality,
        "retention_preflight": retention,
        "scene_usage": scene,
        "real_bear_creek_acquisition": False,
        "real_10x_achievement": False,
    }
    print(json.dumps(receipt, sort_keys=True))


def test_rooted_synthetic_candidate_is_replayed_but_never_an_achievement(tmp_path):
    baseline, descriptor, root, details = _build_rooted_fixture(tmp_path)
    result = benchmark.evaluate_candidate(baseline, descriptor, root, 10)
    assert result["status"] == "FIXTURE_ONLY"
    assert "achieved_ratio_milli" not in result
    assert "content consistency alone" in result["non_achievement_reason"]
    assert result["acquisition_approval"]["matched"] is False
    assert result["acquisition_approval"]["approved_entry_count"] == 0
    assert result["frame_parity"] == 1
    assert result["decision_parity"] == 1
    assert result["lossless_cells"] == 0
    assert result["lossy_cells"] == 1
    assert result["replayed_quality_receipts"] == 1
    assert result["replayed_seam_receipts"] == 1
    assert result["replayed_frame_fidelity_receipts"] == 1
    assert result["quality_coverage"]["expected"] == result[
        "quality_coverage"
    ]["executed"]
    assert all(
        value == 0 for value in result["quality_coverage"]["omissions"].values()
    )
    assert result["candidate"]["cold_total_bytes"] == result["candidate"][
        "total_bytes"
    ]
    assert result["candidate"][
        "candidate_declared_warm_reused_bytes"
    ] == imagery.DECODED_RGB_LENGTH
    assert "warm_reused_bytes" not in result["candidate"]
    assert result["render_work"]["operations_executed"] > 0
    planned = result["replay_budget"]["planned"]
    limits = result["replay_budget"]["limits"]
    for required in (
        "object_reads", "canonical_json_bytes", "canonical_json_operations",
        "source_response_pixels",
        "source_transform_taps", "qrgb_decode_bytes", "quality_windows",
        "quality_edge_scans", "seam_cells", "seam_luma_work",
        "png_decode_bytes", "frames", "cells", "recipes",
        "fraction_operations", "retained_raw_bytes",
        "non_scene_prepared_bytes", "metadata_prepared_bytes",
        "retained_rgb_bytes",
        "working_memory_bytes", "peak_retained_bytes",
        "total_operations_planned",
        "canonical_json_nodes", "canonical_json_containers",
        "canonical_json_container_entries", "canonical_json_string_bytes",
        "canonical_json_max_depth", "scenes", "scene_points",
        "scene_paths", "scene_segments", "scene_lots", "scene_json_bytes",
        "scene_prepared_bytes", "scene_canonicalization_bytes",
        "scene_preparation_operations",
    ):
        assert required in planned
        assert planned[required] <= limits[required]
    json_operations = result["replay_budget"]["operation_equations"][
        "canonical_json_operations"
    ]
    assert planned["canonical_json_operations"] == (
        benchmark.MAX_REPLAY_CANONICAL_JSON_OPERATIONS
    )
    assert json_operations["declared_rooted_json_bytes"] == planned[
        "canonical_json_bytes"
    ]
    assert json_operations["reserved_byte_operations"] == (
        benchmark.MAX_CANDIDATE_SCOPE_CANONICAL_JSON_OPERATIONS
    )
    assert json_operations["candidate_reserved_byte_operations"] == planned[
        "canonical_json_operations"
    ]
    assert 0 < json_operations["executed_byte_operations"] <= (
        json_operations["reserved_byte_operations"]
    )
    assert json_operations["executed_byte_operations"] > (
        planned["canonical_json_bytes"] * 3
    )
    assert json_operations["reservation_closed"] is True
    assert json_operations["headroom_byte_operations"] == (
        json_operations["reserved_byte_operations"]
        - json_operations["executed_byte_operations"]
    )
    json_audit = json_operations["execution_audit"]
    assert json_audit["reserved_byte_operations"] == (
        benchmark.MAX_CANDIDATE_SCOPE_CANONICAL_JSON_OPERATIONS
    )
    assert json_audit["executed_byte_operations"] == json_operations[
        "executed_byte_operations"
    ]
    assert set(json_audit["executed_by_boundary"]) == {
        "canonical_hash",
        "canonical_revalidation",
        "canonical_serialization",
        "json_decode",
        "structural_scan",
    }
    assert all(
        value > 0 for value in json_audit["executed_by_boundary"].values()
    )
    assert json_audit["reservation_scope"] == "candidate-api"
    assert json_audit["reservation_components"] == (
        benchmark.CANDIDATE_SCOPE_CANONICAL_JSON_RESERVATIONS
    )
    assert json_audit["phase_components"] == (
        benchmark.CANDIDATE_SCOPE_CANONICAL_JSON_PHASE_COMPONENTS
    )
    candidate_execution = json_audit["component_execution"][
        "candidate-evaluation"
    ]
    assert candidate_execution == {
        "reserved_byte_operations": (
            benchmark.MAX_REPLAY_CANONICAL_JSON_OPERATIONS
        ),
        "executed_byte_operations": json_audit["executed_byte_operations"],
        "headroom_byte_operations": (
            benchmark.MAX_REPLAY_CANONICAL_JSON_OPERATIONS
            - json_audit["executed_byte_operations"]
        ),
    }
    assert json_audit["component_execution"][
        "approval-baseline-rederivation"
    ] == {
        "reserved_byte_operations": (
            benchmark.MAX_PROOF_V4_CANONICAL_JSON_OPERATIONS
        ),
        "executed_byte_operations": 0,
        "headroom_byte_operations": (
            benchmark.MAX_PROOF_V4_CANONICAL_JSON_OPERATIONS
        ),
    }
    json_records = {
        digest: record for digest, record in details["records"].items()
        if record["category"] in benchmark.JSON_OBJECT_CATEGORIES
    }
    json_record_bytes = sum(
        record["length"] for record in json_records.values()
    )
    json_document_count = len(json_records) + 1
    phases = json_audit["phases"]
    assert phases["candidate-load"]["calls_by_boundary"] == {
        "canonical_hash": 2 * json_document_count + 2,
        "canonical_revalidation": 1,
        "canonical_serialization": 2,
        "json_decode": 1,
        "structural_scan": json_document_count,
    }
    assert phases["candidate-load"]["executed_by_boundary"] == {
        "canonical_hash": (
            3 * len(details["manifest_raw"])
            + 2 * json_record_bytes
            + len(imagery.canonical_json(details["records"]))
        ),
        "canonical_revalidation": len(details["manifest_raw"]),
        "canonical_serialization": (
            len(details["manifest_raw"])
            + len(imagery.canonical_json(details["records"]))
        ),
        "json_decode": len(details["manifest_raw"]),
        "structural_scan": len(details["manifest_raw"]) + json_record_bytes,
    }
    assert phases["candidate-replay"]["calls_by_boundary"] == {
        "canonical_hash": 17 + len(json_records),
        "canonical_revalidation": len(json_records),
        "canonical_serialization": 37,
        "json_decode": len(json_records),
    }
    assert phases["candidate-replay"]["executed_by_boundary"][
        "json_decode"
    ] == json_record_bytes
    assert phases["candidate-replay"]["executed_by_boundary"][
        "canonical_revalidation"
    ] == json_record_bytes
    assert "structural_scan" not in phases["candidate-replay"][
        "calls_by_boundary"
    ]
    assert phases["acquisition-approval"]["calls_by_boundary"] == {
        "canonical_hash": 2,
        "canonical_revalidation": 1,
        "canonical_serialization": 1,
        "json_decode": 1,
        "structural_scan": 1,
    }
    assert set(phases) == {
        "candidate-load", "candidate-replay", "acquisition-approval",
    }
    for boundary, total in json_audit["executed_by_boundary"].items():
        assert total == sum(
            phase["executed_by_boundary"].get(boundary, 0)
            for phase in phases.values()
        )
    for boundary, total in json_audit["calls_by_boundary"].items():
        assert total == sum(
            phase["calls_by_boundary"].get(boundary, 0)
            for phase in phases.values()
        )
    reuse = result["prepared_product_reuse"]
    assert reuse["candidate_json_documents"] == json_document_count
    assert reuse["precomputed_json_structures_reused"] == json_document_count
    assert reuse["catalog_validated_cells"] == 1
    assert reuse["frame_recipe_envelope_validations"] == 1
    assert reuse["frame_recipe_binding_validations"] == 1
    assert reuse["prepared_source_products"] == 1
    assert reuse["prepared_overlay_scenes"] == 1
    scene_usage = result["scene_usage"]
    assert scene_usage["rooted_scene_refs"] == scene_usage["used_scene_refs"]
    assert scene_usage["scene_count"] == planned["scenes"] == 1
    assert scene_usage["point_count"] == planned["scene_points"]
    assert scene_usage["path_count"] == planned["scene_paths"]
    assert scene_usage["segment_count"] == planned["scene_segments"]
    assert scene_usage["raw_json_bytes"] == planned["scene_json_bytes"]
    assert scene_usage["prepared_memory_bound_bytes"] == planned[
        "scene_prepared_bytes"
    ]


def test_quality_omission_is_derived_from_executed_scans(tmp_path, monkeypatch):
    baseline, descriptor, root, _details = _build_rooted_fixture(tmp_path)
    original = imagery.measure_raster_quality

    def uncounted_frame_scan(*args, **kwargs):
        if kwargs.get("_execution_scope") == "frame":
            kwargs = dict(kwargs)
            kwargs["_execution_counts"] = None
            kwargs["_execution_scope"] = None
        return original(*args, **kwargs)

    monkeypatch.setattr(imagery, "measure_raster_quality", uncounted_frame_scan)
    with pytest.raises(ValueError, match="quality replay omitted exact work"):
        benchmark.evaluate_candidate(baseline, descriptor, root)


@pytest.mark.parametrize("missing_counter", [
    "source_transform_identity_copies",
    "qrgb_decodes",
])
def test_cache_execution_closure_rejects_under_execution(missing_counter):
    counters = {
        "source_transform_identity_copies": 2,
        "source_transform_bilinear_scans": 1,
        "qrgb_decodes": 2,
    }
    counters[missing_counter] -= 1
    with pytest.raises(ValueError, match="cached replay omitted exact work"):
        benchmark._exact_cache_execution_closure(3, 2, counters)


def test_exact_rooted_byte_boundary_passes_and_one_byte_over_fails(tmp_path):
    baseline, descriptor, root, details = _build_rooted_fixture(tmp_path)
    candidate_bytes = len(details["manifest_raw"]) + sum(
        record["length"] for record in details["records"].values()
    )
    exact_baseline = replace(baseline, total_bytes=candidate_bytes * 10)
    exact = benchmark.evaluate_candidate(exact_baseline, descriptor, root, 10)
    assert exact["candidate"]["total_bytes"] == candidate_bytes
    assert exact["byte_gate"] == candidate_bytes
    assert exact["status"] == "FIXTURE_ONLY"
    assert "achieved_ratio_milli" not in exact

    one_byte_short = replace(
        baseline, total_bytes=candidate_bytes * 10 - 1
    )
    with pytest.raises(
            ValueError,
            match=rf"candidate is {candidate_bytes} bytes; gate is {candidate_bytes - 1}"):
        benchmark.evaluate_candidate(one_byte_short, descriptor, root, 10)


def test_evaluation_rejects_no_root_and_declaration_only_v1(tmp_path):
    baseline, descriptor, root, _details = _build_rooted_fixture(tmp_path)
    with pytest.raises(ValueError, match="supplied local object root"):
        benchmark.evaluate_candidate(baseline, descriptor)
    old_declaration = {
        "version": 1,
        "kind": "parking-evidence-scaling-candidate",
        "baseline_proof_sha256": baseline.proof_sha256,
        "objects": [],
        "frames": {},
        "decisions": {},
        "quality": {},
        "warm_cache_refs": [],
    }
    with pytest.raises(ValueError, match="schema mismatch"):
        benchmark.evaluate_candidate(baseline, old_declaration, root)


def test_forged_frame_hash_and_changed_decision_are_replayed_not_trusted(tmp_path):
    baseline, descriptor, root, _details = _build_rooted_fixture(
        tmp_path / "frame", forged_frame_hash=True
    )
    with pytest.raises(ValueError, match="actual RGB hash binding"):
        benchmark.evaluate_candidate(baseline, descriptor, root)
    baseline, descriptor, root, _details = _build_rooted_fixture(
        tmp_path / "decision", changed_decision=True
    )
    with pytest.raises(ValueError, match="decision, axis, or confidence parity"):
        benchmark.evaluate_candidate(baseline, descriptor, root)


def _rewrite_manifest(
        root: Path, descriptor: dict[str, object], manifest: dict[str, object],
) -> dict[str, object]:
    rewritten = copy.deepcopy(manifest)
    rewritten["objects"] = dict(sorted(rewritten["objects"].items()))
    rewritten["object_closure_sha256"] = hashlib.sha256(
        imagery.canonical_json(rewritten["objects"])
    ).hexdigest()
    raw = imagery.canonical_json(rewritten)
    digest = _write_candidate_object(root, raw)
    result = copy.deepcopy(descriptor)
    result["proof_manifest_ref"] = digest
    result["proof_manifest_length"] = len(raw)
    return result


def _replace_root_document(
        root: Path, manifest: dict[str, object], field: str,
        document: dict[str, object], category: str,
        matrix: str | None = None,
) -> dict[str, object]:
    rewritten = copy.deepcopy(manifest)
    records = rewritten["objects"]
    old_ref = rewritten["roots"][field]
    records.pop(old_ref)
    new_ref = _add_object(
        root, records, imagery.canonical_json(document), category, matrix
    )
    rewritten["roots"][field] = new_ref

    old_acquisition_ref = rewritten["roots"]["acquisition_manifest_ref"]
    acquisition_raw = benchmark.candidate_object_path(
        root, old_acquisition_ref
    ).read_bytes()
    acquisition = imagery.parse_canonical_json(
        acquisition_raw, "fixture acquisition manifest"
    )
    records.pop(old_acquisition_ref)
    acquisition["candidate_roots"][field] = new_ref
    new_acquisition_ref = _add_object(
        root,
        records,
        imagery.canonical_json(acquisition),
        "acquisition-manifest",
    )
    rewritten["roots"]["acquisition_manifest_ref"] = new_acquisition_ref
    return rewritten


def _replace_recipe(
        root: Path, manifest: dict[str, object], details: dict[str, object],
        recipe: dict[str, object],
) -> dict[str, object]:
    rewritten = copy.deepcopy(manifest)
    records = rewritten["objects"]
    records.pop(details["recipe_ref"])
    recipe_ref = _add_object(
        root,
        records,
        imagery.canonical_json(recipe),
        "frame-recipe",
        recipe["matrix"],
    )
    frame_set = copy.deepcopy(details["frame_set"])
    frame_set["frames"]["1/z2"]["recipe_ref"] = recipe_ref
    return _replace_root_document(
        root, rewritten, "frame_set_ref", frame_set, "frame-set"
    )


def _replace_fidelity(
        root: Path, manifest: dict[str, object], details: dict[str, object],
        fidelity: dict[str, object],
) -> dict[str, object]:
    rewritten = copy.deepcopy(manifest)
    records = rewritten["objects"]
    records.pop(details["fidelity_ref"])
    fidelity_ref = _add_object(
        root,
        records,
        imagery.canonical_json(fidelity),
        "frame-fidelity-receipt",
    )
    frame_set = copy.deepcopy(details["frame_set"])
    frame_set["frames"]["1/z2"]["fidelity_receipt_ref"] = fidelity_ref
    return _replace_root_document(
        root, rewritten, "frame_set_ref", frame_set, "frame-set"
    )


def test_recipe_dimensions_reject_against_baseline_before_decode_or_budget(
        tmp_path, monkeypatch):
    baseline, descriptor, root, details = _build_rooted_fixture(tmp_path)
    recipe = copy.deepcopy(details["recipe"])
    recipe["output"]["height"] -= 1
    manifest = _replace_recipe(root, details["manifest"], details, recipe)
    candidate = _rewrite_manifest(root, descriptor, manifest)
    original_reserve = benchmark.ReplayBudget.reserve

    def guarded_reserve(self, name, amount):
        if name in {"png_decode_bytes", "working_memory_bytes"}:
            raise AssertionError("dimension mismatch reached frame memory budget")
        return original_reserve(self, name, amount)

    def forbidden_decode(*_args, **_kwargs):
        raise AssertionError("dimension mismatch reached baseline PNG decode")

    monkeypatch.setattr(benchmark.ReplayBudget, "reserve", guarded_reserve)
    monkeypatch.setattr(benchmark, "_baseline_frame_rgb", forbidden_decode)
    with pytest.raises(ValueError, match="differ from pinned baseline"):
        benchmark.evaluate_candidate(baseline, candidate, root)


def test_closure_rejects_unreachable_extra_missing_and_zero_length(tmp_path):
    baseline, descriptor, root, details = _build_rooted_fixture(tmp_path / "extra")
    extra_raw = b"unreachable but correctly hashed bytes"
    extra_ref = _write_candidate_object(root, extra_raw)
    manifest = copy.deepcopy(details["manifest"])
    manifest["objects"][extra_ref] = {
        "length": len(extra_raw),
        "category": "source-response-bytes",
        "matrix": "detail-v1",
    }
    extra_descriptor = _rewrite_manifest(root, descriptor, manifest)
    with pytest.raises(ValueError, match="exact reachable closure") as error:
        benchmark.evaluate_candidate(baseline, extra_descriptor, root)
    assert f"missing=['{extra_ref}'] extras=[]" in str(error.value)

    baseline, descriptor, root, details = _build_rooted_fixture(tmp_path / "zero")
    manifest = copy.deepcopy(details["manifest"])
    any_ref = next(iter(manifest["objects"]))
    manifest["objects"][any_ref]["length"] = 0
    zero_descriptor = _rewrite_manifest(root, descriptor, manifest)
    with pytest.raises(ValueError, match="integer >= 1"):
        benchmark.evaluate_candidate(baseline, zero_descriptor, root)

    baseline, descriptor, root, details = _build_rooted_fixture(tmp_path / "matrix")
    manifest = copy.deepcopy(details["manifest"])
    payload_ref = next(
        digest for digest, record in manifest["objects"].items()
        if record["category"] == "cell-payload"
    )
    manifest["objects"][payload_ref]["matrix"] = "context-v1"
    matrix_descriptor = _rewrite_manifest(root, descriptor, manifest)
    with pytest.raises(ValueError, match="matrix accounting"):
        benchmark.evaluate_candidate(baseline, matrix_descriptor, root)

    baseline, descriptor, root, details = _build_rooted_fixture(tmp_path / "missing")
    missing = copy.deepcopy(descriptor)
    missing["proof_manifest_ref"] = "f" * 64
    with pytest.raises(FileNotFoundError):
        benchmark.evaluate_candidate(baseline, missing, root)


def test_hash_length_symlink_and_hardlink_reads_fail_closed(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    raw = b"exact rooted bytes"
    digest = _write_candidate_object(root, raw)
    assert benchmark._read_candidate_object(root, digest, len(raw), "fixture") == raw
    with pytest.raises(ValueError, match="exact declared length"):
        benchmark._read_candidate_object(root, digest, len(raw) + 1, "fixture")

    linked_root = tmp_path / "linked"
    linked_root.mkdir()
    linked_root.chmod(0o700)
    target = tmp_path / "outside"
    target.write_bytes(raw)
    linked_path = benchmark.candidate_object_path(linked_root, digest)
    linked_path.parent.mkdir(parents=True)
    (linked_root / benchmark.CANDIDATE_OBJECT_DIRECTORY).chmod(0o700)
    linked_path.parent.chmod(0o700)
    linked_path.symlink_to(target)
    with pytest.raises(OSError):
        benchmark._read_candidate_object(linked_root, digest, len(raw), "fixture")

    hard_root = tmp_path / "hard"
    hard_root.mkdir()
    _write_candidate_object(hard_root, raw)
    hard_path = benchmark.candidate_object_path(hard_root, digest)
    os.link(hard_path, tmp_path / "hard-alias")
    with pytest.raises(ValueError, match="single-link"):
        benchmark._read_candidate_object(hard_root, digest, len(raw), "fixture")


def test_candidate_directory_sibling_churn_does_not_change_identity(
        tmp_path, monkeypatch):
    root = tmp_path / "churn-root"
    raw = b"a" * (benchmark.READ_SIZE + 32)
    digest = _write_candidate_object(root, raw)
    object_path = benchmark.candidate_object_path(root, digest)
    sibling = object_path.parent / ("f" * 62)
    if sibling == object_path:
        sibling = object_path.parent / ("e" * 62)
    real_read = benchmark.os.read
    changed = False

    def churning_read(descriptor, amount):
        nonlocal changed
        chunk = real_read(descriptor, amount)
        if chunk and not changed:
            changed = True
            sibling.write_bytes(b"benign sibling")
            sibling.chmod(0o600)
        return chunk

    monkeypatch.setattr(benchmark.os, "read", churning_read)
    assert benchmark._read_candidate_object(
        root, digest, len(raw), "directory-churn fixture"
    ) == raw
    assert changed is True


def test_trust_anchor_reader_rejects_symlinked_ancestor_mode_and_substitution(
        tmp_path, monkeypatch):
    actual = tmp_path / "actual"
    actual.mkdir()
    anchor = actual / "anchor.json"
    raw = b"a" * (benchmark.READ_SIZE + 32)
    anchor.write_bytes(raw)
    anchor.chmod(0o644)
    assert benchmark._bounded_regular(
        anchor, len(raw), "tracked anchor"
    ) == raw

    root_identity = (os.stat("/").st_dev, os.stat("/").st_ino)
    full_identity = benchmark._stat_identity

    def reject_full_root_identity(value):
        if stat.S_ISDIR(value.st_mode) and (value.st_dev, value.st_ino) == (
                root_identity):
            raise AssertionError("filesystem root used churn-sensitive identity")
        return full_identity(value)

    monkeypatch.setattr(
        benchmark, "_stat_identity", reject_full_root_identity
    )
    assert benchmark._bounded_regular(
        anchor, len(raw), "tracked anchor"
    ) == raw

    linked = tmp_path / "linked"
    linked.symlink_to(actual, target_is_directory=True)
    with pytest.raises((OSError, ValueError), match="symlink|Not a directory"):
        benchmark._bounded_regular(
            linked / anchor.name, len(raw), "tracked anchor"
        )

    anchor.chmod(0o664)
    with pytest.raises(ValueError, match="group- or world-writable"):
        benchmark._bounded_regular(anchor, len(raw), "tracked anchor")
    anchor.chmod(0o644)

    real_read = benchmark.os.read
    replaced = False

    def substituting_read(descriptor, amount):
        nonlocal replaced
        chunk = real_read(descriptor, amount)
        if chunk and not replaced:
            replaced = True
            anchor.rename(actual / "displaced-anchor.json")
            anchor.write_bytes(b"b" * len(raw))
            anchor.chmod(0o644)
        return chunk

    monkeypatch.setattr(benchmark.os, "read", substituting_read)
    with pytest.raises(ValueError, match="changed while it was read"):
        benchmark._bounded_regular(anchor, len(raw), "tracked anchor")


@pytest.mark.parametrize("acl_name", [
    "system.nfs4_acl",
    "system.richacl",
])
def test_linux_acl_inspection_rejects_unrecognized_acl_backends(
        tmp_path, monkeypatch, acl_name):
    root = tmp_path / "acl-candidate"
    raw = b"acl-bearing candidate object"
    digest = _write_candidate_object(root, raw)
    path = benchmark.candidate_object_path(root, digest)
    target_identity = (path.stat().st_dev, path.stat().st_ino)

    monkeypatch.setattr(benchmark.sys, "platform", "linux")
    monkeypatch.setattr(
        benchmark.trusted_filesystem,
        "require_trivial_acl_fd",
        lambda *_args, **_kwargs: None,
    )

    def listxattr(descriptor):
        value = os.fstat(descriptor)
        if (value.st_dev, value.st_ino) == target_identity:
            return [acl_name]
        return []

    monkeypatch.setattr(benchmark.os, "listxattr", listxattr, raising=False)
    with pytest.raises(ValueError, match="unrecognized ACL backend"):
        benchmark._read_candidate_object(
            root, digest, len(raw), "ACL-bearing candidate"
        )


def test_acl_inspection_reuses_tracked_darwin_and_linux_policy(monkeypatch):
    for platform in ("darwin", "linux"):
        calls = []
        monkeypatch.setattr(benchmark.sys, "platform", platform)
        monkeypatch.setattr(
            benchmark.trusted_filesystem,
            "require_trivial_acl_fd",
            lambda *args, **kwargs: calls.append((args, kwargs)),
        )
        monkeypatch.setattr(
            benchmark.os, "listxattr", lambda _descriptor: [], raising=False
        )
        audit = benchmark._FilesystemControlAudit(
            phase="candidate-admission", governs_real_achievement=True
        )
        token = benchmark._ACTIVE_FILESYSTEM_CONTROL_AUDIT.set(audit)
        try:
            assert benchmark._inspect_trivial_acl(0, "simulated object") is True
        finally:
            benchmark._ACTIVE_FILESYSTEM_CONTROL_AUDIT.reset(token)
        assert len(calls) == 1
        controls = audit.report()
        assert controls["status"] == "complete"
        assert controls["acl_inspection"]["platform"] == platform
        assert controls["acl_inspection"]["policy"] == (
            "trusted_filesystem.require_trivial_acl_fd"
        )
        assert platform.capitalize() in controls["acl_inspection"][
            "required_platform_capability"
        ]


def test_unavailable_acl_inspection_is_reported_for_fixture_replay(
        tmp_path, monkeypatch):
    monkeypatch.setattr(benchmark.sys, "platform", "linux")
    monkeypatch.setattr(
        benchmark.trusted_filesystem,
        "require_trivial_acl_fd",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.delattr(benchmark.os, "listxattr", raising=False)
    baseline, descriptor, root, _details = _build_rooted_fixture(tmp_path)
    result = benchmark.evaluate_candidate(baseline, descriptor, root)
    controls = result["filesystem_controls"]
    assert result["status"] == "FIXTURE_ONLY"
    assert controls["status"] == "degraded"
    assert controls["complete_for_real_achievement"] is False
    assert controls["phase"] == "candidate-admission"
    assert controls["governs_real_achievement"] is True
    assert controls["required_controls"] == {
        "content_hash_and_exact_length": "enforced",
        "candidate_root_and_object_tree": (
            "effective-user-owned exact-mode 0700 candidate root/hash "
            "directories and 0600 single-link regular object files"
        ),
        "candidate_root_ancestors": (
            "no-follow directory traversal plus final candidate-root identity "
            "revalidation; ancestor owner, mode, and ACL are not admission "
            "controls"
        ),
        "trust_anchor_files": (
            "effective-user-owned non-group/world-writable single-link "
            "regular files"
        ),
        "trust_anchor_ancestors": (
            "named components below the filesystem root are "
            "root/effective-user-owned, not group- or world-writable "
            "no-follow directories, except writable root-owned sticky "
            "directories; the filesystem root is identity-only; all "
            "descriptor identities are stable across read; ancestor ACLs are "
            "not controls because every trust-anchor payload is independently "
            "content pinned"
        ),
        "stable_no_follow_descriptor_reads": "enforced",
        "acl_inspection": (
            "Linux descriptor ACL policy via "
            "trusted_filesystem.require_trivial_acl_fd (fgetxattr), plus "
            "descriptor xattr enumeration rejecting unrecognized ACL backends; "
            "unavailable-degraded"
        ),
    }
    reader_contract = benchmark._bounded_regular.__doc__
    assert reader_contract is not None
    assert "named ancestor below the filesystem root" in reader_contract
    assert "filesystem root itself is identity-revalidated only" in reader_contract
    assert "Every\n    ancestor must" not in reader_contract
    acl = controls["acl_inspection"]
    assert acl["platform"] == "linux"
    assert acl["policy"] == "trusted_filesystem.require_trivial_acl_fd"
    assert acl["required_platform_capability"] == (
        "Linux descriptor ACL policy via "
        "trusted_filesystem.require_trivial_acl_fd (fgetxattr), plus "
        "descriptor xattr enumeration rejecting unrecognized ACL backends"
    )
    assert acl["status"] == "unavailable-degraded"
    assert acl["attempts"] > 0
    assert acl["completed"] == 0
    assert acl["unavailable"] == acl["attempts"]
    assert acl["unavailable_reasons"] == {
        "descriptor-listxattr-missing": acl["attempts"],
    }
    assert controls["reviewed_acl_equivalent"] == {
        "support": "not-implemented",
        "shipped_value": None,
        "observed_runtime_value": None,
        "configured_but_rejected": False,
        "accepted_for_real_achievement": False,
    }


def test_acl_equivalent_is_shipped_none_and_unrecognized_values_never_admit(
        monkeypatch):
    assert benchmark.PINNED_REVIEWED_ACL_EQUIVALENT is None
    monkeypatch.setattr(benchmark.sys, "platform", "linux")
    monkeypatch.setattr(
        benchmark.trusted_filesystem,
        "require_trivial_acl_fd",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.delattr(benchmark.os, "listxattr", raising=False)
    for configured in ("", "unreviewed-label", object()):
        monkeypatch.setattr(
            benchmark, "PINNED_REVIEWED_ACL_EQUIVALENT", configured
        )
        audit = benchmark._FilesystemControlAudit(
            phase="candidate-admission", governs_real_achievement=True
        )
        token = benchmark._ACTIVE_FILESYSTEM_CONTROL_AUDIT.set(audit)
        try:
            assert benchmark._inspect_trivial_acl(0, "simulated object") is False
        finally:
            benchmark._ACTIVE_FILESYSTEM_CONTROL_AUDIT.reset(token)
        controls = audit.report()
        assert controls["complete_for_real_achievement"] is False
        assert controls["reviewed_acl_equivalent"][
            "configured_but_rejected"
        ] is True
        assert controls["reviewed_acl_equivalent"][
            "accepted_for_real_achievement"
        ] is False


@pytest.mark.parametrize(
    "error_type,reason",
    [
        (TypeError, "descriptor-listxattr-unsupported"),
        (OSError, "descriptor-listxattr-unavailable"),
    ],
)
def test_descriptor_acl_unavailability_reasons_are_audited(
        monkeypatch, error_type, reason):
    def unavailable(_descriptor):
        raise error_type("simulated descriptor ACL limitation")

    monkeypatch.setattr(benchmark.sys, "platform", "linux")
    monkeypatch.setattr(
        benchmark.trusted_filesystem,
        "require_trivial_acl_fd",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(benchmark.os, "listxattr", unavailable, raising=False)
    audit = benchmark._FilesystemControlAudit(
        phase="candidate-admission", governs_real_achievement=True
    )
    token = benchmark._ACTIVE_FILESYSTEM_CONTROL_AUDIT.set(audit)
    try:
        assert benchmark._inspect_trivial_acl(0, "simulated object") is False
    finally:
        benchmark._ACTIVE_FILESYSTEM_CONTROL_AUDIT.reset(token)
    controls = audit.report()
    assert controls["status"] == "degraded"
    assert controls["complete_for_real_achievement"] is False
    assert controls["acl_inspection"]["unavailable_reasons"] == {reason: 1}


def test_exact_object_bytes_tamper_is_rejected_before_replay(tmp_path):
    baseline, descriptor, root, details = _build_rooted_fixture(tmp_path)
    copy_root = tmp_path / "copy"
    shutil.copytree(root, copy_root)
    quality_path = benchmark.candidate_object_path(copy_root, details["quality_ref"])
    raw = bytearray(quality_path.read_bytes())
    raw[-1] ^= 1
    quality_path.write_bytes(raw)
    with pytest.raises(ValueError, match="exact SHA-256"):
        benchmark.evaluate_candidate(baseline, descriptor, copy_root)


def test_synthetic_cannot_be_relabelled_as_real_shadow(tmp_path):
    baseline, descriptor, root, _details = _build_rooted_fixture(tmp_path)
    relabelled = copy.deepcopy(descriptor)
    relabelled["provenance"]["kind"] = "real-shadow"
    rooted_baseline = baseline
    result = benchmark.evaluate_candidate(rooted_baseline, relabelled, root)
    assert result["status"] == "FIXTURE_ONLY"
    assert result["acquisition_approval"]["matched"] is False
    assert "achieved_ratio_milli" not in result


def test_unapproved_relabelled_provider_and_v4_repackaging_never_achieve(
        tmp_path):
    for name, provider, capture_id in (
        ("provider", "naip", "invented-real-capture"),
        ("v4", "naip", "proof-v4-png-pixels-repacked-as-cells"),
    ):
        baseline, descriptor, root, _details = _build_rooted_fixture(
            tmp_path / name,
            provider=provider,
            provenance_kind="real-shadow",
            capture_id=capture_id,
        )
        result = benchmark.evaluate_candidate(
            baseline, descriptor, root
        )
        assert result["status"] == "FIXTURE_ONLY"
        assert result["acquisition_approval"]["matched"] is False
        assert "achieved_ratio_milli" not in result


def test_matching_approval_requires_declared_real_acquisition_provenance(
        tmp_path, monkeypatch):
    baseline, descriptor, root, details = _build_rooted_fixture(tmp_path)
    approval = {
        "candidate_proof_manifest_ref": details["manifest_ref"],
        "baseline_proof_sha256": baseline.proof_sha256,
        "acquisition_manifest_ref": details["acquisition_manifest_ref"],
        "source_set_sha256": details["acquisition_manifest"][
            "source_set_sha256"
        ],
        "review_id": "code-pinned-test-approval",
    }
    monkeypatch.setattr(
        benchmark, "_load_acquisition_approval_registry", lambda: (approval,)
    )
    result = benchmark.evaluate_candidate(baseline, descriptor, root)
    assert descriptor["provenance"]["kind"] == "synthetic-fixture"
    assert result["status"] == "FIXTURE_ONLY"
    assert result["acquisition_approval"]["matched"] is False
    assert result["acquisition_approval"]["required_declared_provenance"] == (
        benchmark.REAL_ACQUISITION_PROVENANCE_KIND
    )
    assert "achieved_ratio_milli" not in result


def test_approval_registry_is_closed_pinned_empty_and_not_caller_selectable(
        tmp_path):
    raw = benchmark.ACQUISITION_APPROVAL_REGISTRY.read_bytes()
    assert raw == benchmark.PINNED_ACQUISITION_APPROVAL_REGISTRY_BYTES
    assert hashlib.sha256(raw).hexdigest() == (
        benchmark.PINNED_ACQUISITION_APPROVAL_REGISTRY_SHA256
    )
    assert benchmark._load_acquisition_approval_registry() == ()
    baseline, descriptor, root, _details = _build_rooted_fixture(tmp_path)
    supplied = copy.deepcopy(descriptor)
    supplied["approval_registry"] = {"approvals": ["caller-selected"]}
    with pytest.raises(ValueError, match="schema mismatch"):
        benchmark.evaluate_candidate(baseline, supplied, root)


def test_matching_approval_refuses_baseline_without_registry_path(
        tmp_path, monkeypatch):
    baseline, descriptor, root, details = _build_rooted_fixture(
        tmp_path, provenance_kind=benchmark.REAL_ACQUISITION_PROVENANCE_KIND
    )
    assert baseline.registry_path is None
    with pytest.raises(TypeError):
        replace(baseline, verified_rooted=True)
    approval = {
        "candidate_proof_manifest_ref": details["manifest_ref"],
        "baseline_proof_sha256": baseline.proof_sha256,
        "acquisition_manifest_ref": details["acquisition_manifest_ref"],
        "source_set_sha256": details["acquisition_manifest"][
            "source_set_sha256"
        ],
        "review_id": "code-pinned-test-approval",
    }
    monkeypatch.setattr(
        benchmark, "_load_acquisition_approval_registry", lambda: (approval,)
    )
    with pytest.raises(ValueError, match="re-derived proof-v4 baseline"):
        benchmark.evaluate_candidate(baseline, descriptor, root)


def test_test_only_positive_approval_executes_pass_and_cold_ratio(
        bear_baseline, monkeypatch):
    manifest_ref = _hash("test-only approved candidate manifest")
    acquisition_ref = _hash("test-only approved acquisition manifest")
    source_set_ref = _hash("test-only approved source set")
    cold_total_bytes = 8_000_000
    closure = benchmark.CandidateClosure(
        descriptor={},
        provenance_kind="real-shadow",
        manifest_ref=manifest_ref,
        manifest={"roots": {"acquisition_manifest_ref": acquisition_ref}},
        manifest_raw=b"{}",
        records={},
        raw_objects={},
        json_structures={},
        accounting={
            "total_bytes": cold_total_bytes,
            "cold_total_bytes": cold_total_bytes,
            "candidate_declared_warm_reused_bytes": cold_total_bytes - 1,
            "candidate_declared_warm_incremental_bytes": 1,
        },
    )
    acquisition_manifest = {"source_set_sha256": source_set_ref}
    approval = {
        "candidate_proof_manifest_ref": manifest_ref,
        "baseline_proof_sha256": bear_baseline.proof_sha256,
        "acquisition_manifest_ref": acquisition_ref,
        "source_set_sha256": source_set_ref,
        "review_id": "code-pinned-test-only-approval",
    }
    monkeypatch.setattr(benchmark.sys, "platform", "linux")
    monkeypatch.setattr(
        benchmark.trusted_filesystem,
        "require_trivial_acl_fd",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        benchmark.os, "listxattr", lambda _descriptor: [], raising=False
    )

    def isolated_closure_stub(
            baseline, candidate, object_root, budget, byte_gate):
        assert baseline is bear_baseline
        assert candidate == {"test_only": True}
        assert object_root == Path("/test-only-isolated-object-root")
        assert byte_gate == benchmark.BEAR_CREEK_HARD_BYTES
        assert budget.usage == {
            "canonical_json_operations": (
                benchmark.MAX_REPLAY_CANONICAL_JSON_OPERATIONS
            ),
        }
        benchmark._inspect_trivial_acl(
            0, "fully simulated test-only filesystem controls"
        )
        return closure

    def isolated_replay_stub(baseline, supplied_closure, budget):
        assert baseline is bear_baseline
        assert supplied_closure is closure
        assert budget.usage == {
            "canonical_json_operations": (
                benchmark.MAX_REPLAY_CANONICAL_JSON_OPERATIONS
            ),
        }
        return {"frame_parity": 150, "decision_parity": 50}, acquisition_manifest

    monkeypatch.setattr(
        benchmark, "_load_candidate_closure", isolated_closure_stub
    )
    monkeypatch.setattr(benchmark, "_replay_candidate", isolated_replay_stub)
    monkeypatch.setattr(
        benchmark, "_load_acquisition_approval_registry", lambda: (approval,)
    )
    result = benchmark.evaluate_candidate(
        bear_baseline,
        {"test_only": True},
        Path("/test-only-isolated-object-root"),
        require_ratio=10,
    )
    assert result["status"] == "PASS"
    assert result["acquisition_approval"]["matched"] is True
    assert result["acquisition_approval"]["review_id"] == (
        "code-pinned-test-only-approval"
    )
    assert result["candidate"]["cold_total_bytes"] == cold_total_bytes
    assert result["achieved_ratio_milli"] == (
        bear_baseline.total_bytes * 1000 // cold_total_bytes
    )
    assert result["achieved_ratio_milli"] != (
        bear_baseline.total_bytes * 1000
        // result["candidate"]["candidate_declared_warm_incremental_bytes"]
    )
    assert result["filesystem_controls"]["status"] == "complete"
    assert result["filesystem_controls"][
        "complete_for_real_achievement"
    ] is True
    assert result["acquisition_approval"][
        "admitted_for_real_achievement"
    ] is True
    approval_audit = result["replay_budget"]["operation_equations"][
        "canonical_json_operations"
    ]["execution_audit"]
    assert approval_audit["reservation_scope"] == "candidate-api"
    assert approval_audit["reserved_byte_operations"] == (
        benchmark.MAX_CANDIDATE_SCOPE_CANONICAL_JSON_OPERATIONS
    )
    assert approval_audit["reserved_byte_operations"] == sum(
        approval_audit["reservation_components"].values()
    )
    approval_phase = approval_audit["phases"][
        "approval-baseline-rederivation"
    ]
    assert approval_phase["calls_by_boundary"]["canonical_hash"] == 4
    approval_component = approval_audit["component_execution"][
        "approval-baseline-rederivation"
    ]
    assert approval_component["reserved_byte_operations"] == (
        benchmark.MAX_PROOF_V4_CANONICAL_JSON_OPERATIONS
    )
    assert approval_component["executed_byte_operations"] == (
        approval_phase["executed_byte_operations"]
    )
    assert approval_component["headroom_byte_operations"] >= 0
    assert approval_audit["component_execution"]["candidate-evaluation"][
        "reserved_byte_operations"
    ] == benchmark.MAX_REPLAY_CANONICAL_JSON_OPERATIONS

    monkeypatch.delattr(benchmark.os, "listxattr", raising=False)
    refused = benchmark.evaluate_candidate(
        bear_baseline,
        {"test_only": True},
        Path("/test-only-isolated-object-root"),
        require_ratio=10,
    )
    assert refused["status"] == "FIXTURE_ONLY"
    assert refused["acquisition_approval"]["matched"] is True
    assert refused["acquisition_approval"][
        "admitted_for_real_achievement"
    ] is False
    assert refused["filesystem_controls"]["status"] == "degraded"
    assert "achieved_ratio_milli" not in refused
    assert "filesystem controls are incomplete" in refused[
        "non_achievement_reason"
    ]


def test_local_synthetic_approval_passes_real_loader_and_replay(
        tmp_path, monkeypatch):
    baseline, descriptor, root, details = _build_rooted_fixture(
        tmp_path / "candidate",
        provenance_kind=benchmark.REAL_ACQUISITION_PROVENANCE_KIND,
    )
    baseline_registry = tmp_path / "synthetic-proof-v4-registry.json"
    baseline_registry.write_bytes(b"{}")
    baseline_registry.chmod(0o600)
    baseline = replace(baseline, registry_path=baseline_registry)
    approval = {
        "candidate_proof_manifest_ref": details["manifest_ref"],
        "baseline_proof_sha256": baseline.proof_sha256,
        "acquisition_manifest_ref": details["acquisition_manifest_ref"],
        "source_set_sha256": details["acquisition_manifest"][
            "source_set_sha256"
        ],
        "review_id": "local-synthetic-integration-approval",
    }
    registry_raw = imagery.canonical_json({
        "approvals": [approval],
        "kind": "parking-scenepack-reviewed-acquisition-registry",
        "version": 1,
    })
    local_registry = tmp_path / "local-acquisition-approvals.json"
    local_registry.write_bytes(registry_raw)
    local_registry.chmod(0o600)
    monkeypatch.setattr(
        benchmark, "ACQUISITION_APPROVAL_REGISTRY", local_registry
    )
    monkeypatch.setattr(
        benchmark, "PINNED_ACQUISITION_APPROVAL_REGISTRY_BYTES", registry_raw
    )
    monkeypatch.setattr(
        benchmark,
        "PINNED_ACQUISITION_APPROVAL_REGISTRY_SHA256",
        hashlib.sha256(registry_raw).hexdigest(),
    )
    monkeypatch.setattr(benchmark, "_acl_platform", lambda: "darwin")
    monkeypatch.setattr(
        benchmark.trusted_filesystem,
        "require_trivial_acl_fd",
        lambda *_args, **_kwargs: None,
    )
    rederivations = []

    def remeasure(registry_path, proof_sha256):
        rederivations.append((registry_path, proof_sha256))
        return baseline

    monkeypatch.setattr(benchmark, "measure_proof_v4", remeasure)
    result = benchmark.evaluate_candidate(baseline, descriptor, root)
    assert result["status"] == "PASS"
    assert result["frame_parity"] == 1
    assert result["decision_parity"] == 1
    assert result["replayed_frame_fidelity_receipts"] == 1
    assert result["acquisition_approval"]["matched"] is True
    assert result["acquisition_approval"][
        "admitted_for_real_achievement"
    ] is True
    assert result["acquisition_approval"]["review_id"] == approval["review_id"]
    assert result["achieved_ratio_milli"] == (
        baseline.total_bytes * 1000
        // result["candidate"]["cold_total_bytes"]
    )
    assert rederivations == [(baseline_registry, baseline.proof_sha256)]
    assert benchmark.ACQUISITION_APPROVAL_REGISTRY == local_registry
    assert registry_raw != (
        b'{"approvals":[],"kind":"parking-scenepack-reviewed-acquisition-registry",'
        b'"version":1}'
    )


_APPROVED_BASELINE_AUTHORITY_FIELDS = (
    "proof_sha256",
    "manifest_length",
    "adjudications",
    "frame_count",
    "image_bytes",
    "total_bytes",
    "logical_objects",
    "leaf_objects",
    "largest_blob",
    "by_rung",
    "frame_sources",
    "frame_dimensions",
    "decisions",
    "object_refs",
)


def _mutate_approved_baseline_field(
        baseline: benchmark.BaselineMeasurement, field_name: str,
) -> benchmark.BaselineMeasurement:
    if field_name == "proof_sha256":
        value = _hash("mutated approved baseline proof")
    elif field_name in {
            "manifest_length", "adjudications", "frame_count", "image_bytes",
            "total_bytes", "logical_objects", "leaf_objects", "largest_blob",
    }:
        value = getattr(baseline, field_name) + 1
    elif field_name == "by_rung":
        value = dict(baseline.by_rung)
        value["z1"] += 1
    elif field_name == "frame_sources":
        value = dict(baseline.frame_sources)
        value[next(iter(value))] = _hash("mutated approved baseline frame")
    elif field_name == "frame_dimensions":
        value = dict(baseline.frame_dimensions)
        value[next(iter(value))] = (1023, 1024)
    elif field_name == "decisions":
        value = copy.deepcopy(baseline.decisions)
        value[next(iter(value))] = {"mutated": True}
    elif field_name == "object_refs":
        value = dict(baseline.object_refs)
        value.pop(next(iter(value)))
    else:
        raise AssertionError(f"unhandled authority field {field_name}")
    return replace(baseline, **{field_name: value})


def test_approval_mutation_fields_equal_actual_identity_keys(bear_baseline):
    assert set(_APPROVED_BASELINE_AUTHORITY_FIELDS) == set(
        benchmark._approved_baseline_identity(bear_baseline)
    )


@pytest.mark.parametrize("field_name", _APPROVED_BASELINE_AUTHORITY_FIELDS)
def test_every_rederived_baseline_authority_field_mutation_refuses_approval(
        bear_baseline, monkeypatch, field_name):
    mutated = _mutate_approved_baseline_field(bear_baseline, field_name)
    monkeypatch.setattr(
        benchmark, "measure_proof_v4", lambda *_args: bear_baseline
    )
    with pytest.raises(
            ValueError, match="differs from its re-derived proof-v4"):
        benchmark._rederive_approved_baseline(mutated)


def test_frame_entry_rung_and_recipe_fid_swaps_fail_before_pixel_replay(
        tmp_path, monkeypatch):
    baseline, descriptor, root, details = _build_rooted_fixture(tmp_path / "rung")
    frame_set = copy.deepcopy(details["frame_set"])
    frame_set["frames"]["1/z2"]["rung"] = "z1"
    manifest = _replace_root_document(
        root, details["manifest"], "frame_set_ref", frame_set, "frame-set"
    )
    swapped = _rewrite_manifest(root, descriptor, manifest)
    with pytest.raises(ValueError, match="entry FID/rung identity mismatch"):
        benchmark.evaluate_candidate(baseline, swapped, root)

    baseline, descriptor, root, details = _build_rooted_fixture(tmp_path / "fid")
    recipe = copy.deepcopy(details["recipe"])
    recipe["fid"] = 2
    manifest = _replace_recipe(root, details["manifest"], details, recipe)
    swapped = _rewrite_manifest(root, descriptor, manifest)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("identity swap reached qRGB decode")

    monkeypatch.setattr(imagery, "decode_qrgb_payload", forbidden)
    with pytest.raises(ValueError, match="recipe FID/rung identity mismatch"):
        benchmark.evaluate_candidate(baseline, swapped, root)


def test_remaining_frame_identity_chain_fields_fail_closed(tmp_path):
    baseline, descriptor, root, details = _build_rooted_fixture(
        tmp_path / "source"
    )
    frame_set = copy.deepcopy(details["frame_set"])
    frame_set["frames"]["1/z2"]["source_png_sha256"] = "f" * 64
    manifest = _replace_root_document(
        root, details["manifest"], "frame_set_ref", frame_set, "frame-set"
    )
    candidate = _rewrite_manifest(root, descriptor, manifest)
    with pytest.raises(ValueError, match="changed its source evidence"):
        benchmark.evaluate_candidate(baseline, candidate, root)

    baseline, descriptor, root, details = _build_rooted_fixture(
        tmp_path / "fidelity"
    )
    fidelity = copy.deepcopy(details["fidelity"])
    fidelity["fid"] = 2
    manifest = _replace_fidelity(root, details["manifest"], details, fidelity)
    candidate = _rewrite_manifest(root, descriptor, manifest)
    with pytest.raises(ValueError, match="fidelity FID/rung identity mismatch"):
        benchmark.evaluate_candidate(baseline, candidate, root)

    baseline, descriptor, root, details = _build_rooted_fixture(
        tmp_path / "packet"
    )
    packet = copy.deepcopy(details["packet_corpus"])
    packet["packets"]["1"]["fid"] = 2
    manifest = _replace_root_document(
        root, details["manifest"], "packet_corpus_ref", packet,
        "packet-corpus",
    )
    candidate = _rewrite_manifest(root, descriptor, manifest)
    with pytest.raises(ValueError, match="frame/FID coverage mismatch"):
        benchmark.evaluate_candidate(baseline, candidate, root)

    baseline, descriptor, root, details = _build_rooted_fixture(
        tmp_path / "authority"
    )
    authority = copy.deepcopy(details["authority"])
    authority["decisions"]["1"]["fid"] = 2
    manifest = _replace_root_document(
        root, details["manifest"], "structured_authority_ref", authority,
        "structured-authority",
    )
    candidate = _rewrite_manifest(root, descriptor, manifest)
    with pytest.raises(ValueError, match="decision, axis, or confidence parity"):
        benchmark.evaluate_candidate(baseline, candidate, root)


def test_unused_catalog_cell_is_rejected_before_any_decode(tmp_path, monkeypatch):
    baseline, descriptor, root, details = _build_rooted_fixture(tmp_path)
    extra_cell = copy.deepcopy(details["cell"])
    extra_cell["grid"]["x"] = 1
    extra_cell["grid"]["bounds_um"] = list(
        imagery.grid_cell_bounds("detail-v1", 1, 0)
    )
    extra_id = imagery.imagery_cell_id(extra_cell)
    catalog = copy.deepcopy(details["catalog"])
    catalog["cells"][extra_id] = extra_cell
    manifest = _replace_root_document(
        root, details["manifest"], "imagery_catalog_ref", catalog,
        "imagery-catalog",
    )
    candidate = _rewrite_manifest(root, descriptor, manifest)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("unused catalog cell reached qRGB decode")

    monkeypatch.setattr(imagery, "decode_qrgb_payload", forbidden)
    with pytest.raises(ValueError, match="exact frame cell union"):
        benchmark.evaluate_candidate(baseline, candidate, root)


def test_catalog_cell_cap_preflights_before_per_cell_validation(
        tmp_path, monkeypatch):
    baseline, descriptor, root, details = _build_rooted_fixture(tmp_path)
    catalog = {
        "version": 1,
        "kind": imagery.IMAGERY_CATALOG_KIND,
        "cells": {
            _hash(f"oversized-cell-{index}"): {}
            for index in range(benchmark.MAX_REPLAY_CELLS + 1)
        },
        "seam_quality_receipt_ref": details["catalog"][
            "seam_quality_receipt_ref"
        ],
    }
    manifest = _replace_root_document(
        root,
        details["manifest"],
        "imagery_catalog_ref",
        catalog,
        "imagery-catalog",
    )
    candidate = _rewrite_manifest(root, descriptor, manifest)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("oversized catalog reached per-cell validation")

    monkeypatch.setattr(
        imagery, "validate_imagery_catalog_document", forbidden
    )
    with pytest.raises(ValueError, match="cells budget exceeded"):
        benchmark.evaluate_candidate(baseline, candidate, root)


def test_unused_rooted_huge_scene_is_rejected_before_scene_preparation(
        tmp_path, monkeypatch):
    baseline, descriptor, root, details = _build_rooted_fixture(tmp_path)
    manifest = copy.deepcopy(details["manifest"])
    records = manifest["objects"]
    huge_scene = copy.deepcopy(details["scene"])
    huge_scene["source"]["generation_sha256"] = _hash("unused huge scene")
    huge_scene["trails"] = [[
        [index, 0] for index in range(renderer.MAX_POINTS_PER_PATH)
    ]]
    huge_ref = _add_object(
        root, records, imagery.canonical_json(huge_scene), "overlay-scene"
    )
    rooted_refs = sorted([details["scene_ref"], huge_ref])
    manifest["roots"]["overlay_scene_refs"] = rooted_refs

    records.pop(details["acquisition_manifest_ref"])
    acquisition = copy.deepcopy(details["acquisition_manifest"])
    acquisition["candidate_roots"]["overlay_scene_refs"] = rooted_refs
    acquisition_ref = _add_object(
        root,
        records,
        imagery.canonical_json(acquisition),
        "acquisition-manifest",
    )
    manifest["roots"]["acquisition_manifest_ref"] = acquisition_ref
    candidate = _rewrite_manifest(root, descriptor, manifest)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("frame-unused scene reached validation or preparation")

    monkeypatch.setattr(renderer, "validate_overlay_scene_metrics", forbidden)
    monkeypatch.setattr(renderer, "_prepare_validated_overlay_scene", forbidden)
    with pytest.raises(ValueError, match="exact frame-used scene set"):
        benchmark.evaluate_candidate(baseline, candidate, root)


@pytest.mark.parametrize("budget_name", [
    "scenes",
    "scene_points",
    "scene_paths",
    "scene_segments",
    "scene_lots",
    "scene_json_bytes",
    "scene_prepared_bytes",
    "scene_canonicalization_bytes",
    "scene_preparation_operations",
])
def test_every_scene_budget_rejects_before_prepared_retention(
        tmp_path, monkeypatch, budget_name):
    baseline, descriptor, root, _details = _build_rooted_fixture(tmp_path)
    monkeypatch.setitem(benchmark.REPLAY_LIMITS, budget_name, 0)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("over-budget scene reached prepared retention")

    monkeypatch.setattr(renderer, "_prepare_validated_overlay_scene", forbidden)
    with pytest.raises(ValueError, match=rf"{budget_name} budget exceeded"):
        benchmark.evaluate_candidate(baseline, descriptor, root)


def test_compressed_amplification_is_rejected_before_decode(tmp_path, monkeypatch):
    baseline, descriptor, root, _details = _build_rooted_fixture(tmp_path)
    monkeypatch.setitem(
        benchmark.REPLAY_LIMITS,
        "retained_rgb_bytes",
        imagery.DECODED_RGB_LENGTH - 1,
    )

    def forbidden(*_args, **_kwargs):
        raise AssertionError("over-budget compressed payload was decoded")

    monkeypatch.setattr(imagery, "decode_qrgb_payload", forbidden)
    with pytest.raises(ValueError, match="retained_rgb_bytes budget exceeded"):
        benchmark.evaluate_candidate(baseline, descriptor, root)


def test_raw_retention_rejects_before_candidate_leaf_reads(tmp_path, monkeypatch):
    baseline, descriptor, root, _details = _build_rooted_fixture(tmp_path)
    original_read = benchmark._read_candidate_object
    labels = []

    def counted_read(*args, **kwargs):
        labels.append(args[3])
        return original_read(*args, **kwargs)

    monkeypatch.setattr(benchmark, "_read_candidate_object", counted_read)
    monkeypatch.setitem(benchmark.REPLAY_LIMITS, "retained_raw_bytes", 0)
    with pytest.raises(ValueError, match="retained_raw_bytes budget exceeded"):
        benchmark.evaluate_candidate(baseline, descriptor, root)
    assert labels == ["candidate proof manifest"]


def test_manifest_retention_is_reserved_before_first_candidate_json_load(
        tmp_path, monkeypatch):
    baseline, descriptor, root, details = _build_rooted_fixture(tmp_path)
    budget = benchmark.ReplayBudget()
    structure = imagery.inspect_json_structure(
        details["manifest_raw"], "fixture candidate manifest"
    )
    expected = benchmark._non_scene_prepared_bound(
        len(details["manifest_raw"]), structure
    )
    original = benchmark._strict_json
    observed = False

    def checked(raw, label, **kwargs):
        nonlocal observed
        if label == "candidate proof manifest":
            observed = True
            assert budget.usage["non_scene_prepared_bytes"] == expected
        return original(raw, label, **kwargs)

    monkeypatch.setattr(benchmark, "_strict_json", checked)
    benchmark._load_candidate_closure(
        baseline, descriptor, root, budget, baseline.total_bytes
    )
    assert observed is True


def test_active_parent_requires_fresh_exact_candidate_components(
        bear_baseline, monkeypatch):
    expected_components = dict(
        benchmark.CANDIDATE_SCOPE_CANONICAL_JSON_RESERVATIONS
    )
    expected_phases = dict(
        benchmark.CANDIDATE_SCOPE_CANONICAL_JSON_PHASE_COMPONENTS
    )
    parents = [
        imagery.CanonicalJsonWorkAudit(
            benchmark.MAX_CANDIDATE_SCOPE_CANONICAL_JSON_OPERATIONS + 1,
            reservation_scope="unpartitioned-parent",
        ),
        imagery.CanonicalJsonWorkAudit(
            benchmark.MAX_REPLAY_CANONICAL_JSON_OPERATIONS,
            reservation_scope="missing-approval-component",
            reservation_components={
                "candidate-evaluation": (
                    benchmark.MAX_REPLAY_CANONICAL_JSON_OPERATIONS
                ),
            },
            phase_components={
                phase: "candidate-evaluation" for phase in (
                    "candidate-load", "candidate-replay",
                    "acquisition-approval",
                    "approval-baseline-rederivation",
                )
            },
        ),
    ]
    undersized = dict(expected_components)
    undersized["candidate-evaluation"] -= 1
    parents.append(imagery.CanonicalJsonWorkAudit(
        sum(undersized.values()),
        reservation_scope="undersized-candidate-component",
        reservation_components=undersized,
        phase_components=expected_phases,
    ))
    for phase in expected_phases:
        wrong_phases = dict(expected_phases)
        wrong_phases[phase] = (
            "approval-baseline-rederivation"
            if expected_phases[phase] == "candidate-evaluation"
            else "candidate-evaluation"
        )
        parents.append(imagery.CanonicalJsonWorkAudit(
            benchmark.MAX_CANDIDATE_SCOPE_CANONICAL_JSON_OPERATIONS,
            reservation_scope=f"wrong-{phase}",
            reservation_components=expected_components,
            phase_components=wrong_phases,
        ))
    consumed = imagery.CanonicalJsonWorkAudit(
        benchmark.MAX_CANDIDATE_SCOPE_CANONICAL_JSON_OPERATIONS,
        reservation_scope="consumed-candidate-component",
        reservation_components=expected_components,
        phase_components=expected_phases,
    )
    consumed.charge("json_decode", 1, "candidate-load")
    parents.append(consumed)
    zero_byte_call = imagery.CanonicalJsonWorkAudit(
        benchmark.MAX_CANDIDATE_SCOPE_CANONICAL_JSON_OPERATIONS,
        reservation_scope="zero-byte-call-in-candidate-component",
        reservation_components=expected_components,
        phase_components=expected_phases,
    )
    zero_byte_call.charge("canonical_hash", 0, "candidate-load")
    assert zero_byte_call.executed_byte_operations == 0
    assert zero_byte_call.report()["phases"]["candidate-load"][
        "calls_by_boundary"
    ] == {"canonical_hash": 1}
    parents.append(zero_byte_call)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("invalid parent reached candidate loading")

    monkeypatch.setattr(benchmark, "_load_candidate_closure", forbidden)
    for parent in parents:
        before = parent.report()
        token = imagery._ACTIVE_CANONICAL_JSON_WORK_AUDIT.set(parent)
        try:
            with pytest.raises(
                    ValueError, match="fresh exact candidate and approval"):
                benchmark.evaluate_candidate(
                    bear_baseline, {}, Path("/unused-candidate-root")
                )
        finally:
            imagery._ACTIVE_CANONICAL_JSON_WORK_AUDIT.reset(token)
        assert parent.report() == before


def test_canonical_json_execution_cannot_exceed_preflight_reservation(
        tmp_path, monkeypatch):
    baseline, descriptor, root, details = _build_rooted_fixture(tmp_path)
    candidate_limit = len(details["manifest_raw"]) - 1
    monkeypatch.setattr(
        benchmark, "MAX_REPLAY_CANONICAL_JSON_OPERATIONS", candidate_limit
    )
    monkeypatch.setitem(
        benchmark.CANDIDATE_SCOPE_CANONICAL_JSON_RESERVATIONS,
        "candidate-evaluation",
        candidate_limit,
    )
    monkeypatch.setattr(
        benchmark,
        "MAX_CANDIDATE_SCOPE_CANONICAL_JSON_OPERATIONS",
        candidate_limit + benchmark.MAX_PROOF_V4_CANONICAL_JSON_OPERATIONS,
    )

    def forbidden(*_args, **_kwargs):
        raise AssertionError("over-budget canonical JSON reached replay")

    monkeypatch.setattr(benchmark, "_replay_candidate", forbidden)
    with pytest.raises(
            ValueError, match="executed byte-operation reservation exceeded"):
        benchmark.evaluate_candidate(baseline, descriptor, root)

    unrelated_reservation = 10 * len(details["manifest_raw"])
    approval_reservation = benchmark.MAX_PROOF_V4_CANONICAL_JSON_OPERATIONS
    outer = imagery.CanonicalJsonWorkAudit(
        candidate_limit + approval_reservation + unrelated_reservation,
        reservation_scope="cli-full-run",
        reservation_components={
            "candidate-evaluation": candidate_limit,
            "approval-baseline-rederivation": approval_reservation,
            "initial-proof-v4-baseline": unrelated_reservation,
        },
        phase_components={
            "candidate-load": "candidate-evaluation",
            "candidate-replay": "candidate-evaluation",
            "acquisition-approval": "candidate-evaluation",
            "approval-baseline-rederivation": (
                "approval-baseline-rederivation"
            ),
        },
    )
    token = imagery._ACTIVE_CANONICAL_JSON_WORK_AUDIT.set(outer)
    try:
        with pytest.raises(
                ValueError, match="component candidate-evaluation"):
            benchmark.evaluate_candidate(baseline, descriptor, root)
    finally:
        imagery._ACTIVE_CANONICAL_JSON_WORK_AUDIT.reset(token)
    assert outer.executed_byte_operations == 0


def test_canonical_json_report_rejects_mismatched_audit_reservation():
    budget = benchmark.ReplayBudget()
    budget.reserve(
        "canonical_json_operations",
        benchmark.MAX_REPLAY_CANONICAL_JSON_OPERATIONS,
    )
    audit = imagery.CanonicalJsonWorkAudit(
        benchmark.MAX_REPLAY_CANONICAL_JSON_OPERATIONS - 1
    )
    with pytest.raises(ValueError, match="does not match its preflight"):
        budget.report(audit)


def test_non_scene_prepared_retention_rejects_before_replay(
        tmp_path, monkeypatch):
    baseline, descriptor, root, _details = _build_rooted_fixture(tmp_path)
    monkeypatch.setitem(
        benchmark.REPLAY_LIMITS, "non_scene_prepared_bytes", 0
    )

    def forbidden(*_args, **_kwargs):
        raise AssertionError("unbudgeted parsed JSON reached replay")

    monkeypatch.setattr(benchmark, "_replay_candidate", forbidden)
    with pytest.raises(
            ValueError, match="non_scene_prepared_bytes budget exceeded"):
        benchmark.evaluate_candidate(baseline, descriptor, root)


def test_peak_retention_rejects_before_rgb_decode(tmp_path, monkeypatch):
    baseline, descriptor, root, _details = _build_rooted_fixture(tmp_path)
    monkeypatch.setitem(benchmark.REPLAY_LIMITS, "peak_retained_bytes", 0)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("over-budget retention reached qRGB decode")

    monkeypatch.setattr(imagery, "decode_qrgb_payload", forbidden)
    with pytest.raises(ValueError, match="peak_retained_bytes budget exceeded"):
        benchmark.evaluate_candidate(baseline, descriptor, root)


@pytest.mark.parametrize("budget_name", [
    "canonical_json_nodes",
    "canonical_json_containers",
    "canonical_json_container_entries",
    "canonical_json_string_bytes",
    "canonical_json_max_depth",
])
def test_aggregate_json_expansion_budgets_reject_before_replay(
        tmp_path, monkeypatch, budget_name):
    baseline, descriptor, root, _details = _build_rooted_fixture(tmp_path)
    monkeypatch.setitem(benchmark.REPLAY_LIMITS, budget_name, 0)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("over-budget JSON expansion reached replay")

    monkeypatch.setattr(benchmark, "_replay_candidate", forbidden)
    with pytest.raises(ValueError, match=rf"{budget_name} budget exceeded"):
        benchmark.evaluate_candidate(baseline, descriptor, root)


def test_source_lineage_manifest_tamper_fails_closed(tmp_path):
    baseline, descriptor, root, details = _build_rooted_fixture(tmp_path)
    acquisition = copy.deepcopy(details["acquisition_manifest"])
    acquisition["source_set_sha256"] = "f" * 64
    manifest = copy.deepcopy(details["manifest"])
    records = manifest["objects"]
    records.pop(details["acquisition_manifest_ref"])
    new_ref = _add_object(
        root, records, imagery.canonical_json(acquisition),
        "acquisition-manifest",
    )
    manifest["roots"]["acquisition_manifest_ref"] = new_ref
    candidate = _rewrite_manifest(root, descriptor, manifest)
    with pytest.raises(ValueError, match="source-set hash mismatch"):
        benchmark.evaluate_candidate(baseline, candidate, root)


def test_reference_rgb_category_is_bound_to_validated_source_identity(
        tmp_path):
    assert "source-rgb-bytes" not in imagery.BYTE_CATEGORIES
    baseline, descriptor, root, details = _build_rooted_fixture(
        tmp_path, non_identity_source=True
    )
    assert details["reference_ref"] != details["source_bytes_ref"]
    assert details["manifest"]["objects"][details["reference_ref"]][
        "category"
    ] == "reference-rgb"

    manifest = copy.deepcopy(details["manifest"])
    manifest["objects"][details["reference_ref"]]["category"] = (
        "source-response-bytes"
    )
    mislabeled = _rewrite_manifest(root, descriptor, manifest)
    with pytest.raises(
            ValueError,
            match="has category 'source-response-bytes'.*reference-rgb",
    ):
        benchmark.evaluate_candidate(baseline, mislabeled, root)


def test_candidate_root_and_hash_directories_require_owner_only_modes(tmp_path):
    baseline, descriptor, root, _details = _build_rooted_fixture(tmp_path / "sealed")
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(tmp_path / "sealed", target_is_directory=True)
    with pytest.raises(OSError):
        benchmark.evaluate_candidate(
            baseline, descriptor, linked_parent / "candidate-objects"
        )
    root.chmod(0o750)
    with pytest.raises(ValueError, match="mode 0750"):
        benchmark.evaluate_candidate(baseline, descriptor, root)
    root.chmod(0o700)
    object_directory = root / benchmark.CANDIDATE_OBJECT_DIRECTORY
    object_directory.chmod(0o770)
    with pytest.raises(ValueError, match="mode 0770"):
        benchmark.evaluate_candidate(baseline, descriptor, root)


def test_sticky_trust_anchor_exemption_and_candidate_ancestor_policy(
        tmp_path):
    assert benchmark._trust_anchor_ancestor_mode_allowed(0, 0o1777) is True
    assert benchmark._trust_anchor_ancestor_mode_allowed(0, 0o0777) is False
    assert benchmark._trust_anchor_ancestor_mode_allowed(os.geteuid(), 0o0755)
    assert not benchmark._trust_anchor_ancestor_mode_allowed(
        os.geteuid(), 0o1777
    )

    writable_ancestor = tmp_path / "writable-candidate-ancestor"
    writable_ancestor.mkdir()
    writable_ancestor.chmod(0o777)
    stable_identity = benchmark._directory_identity(writable_ancestor.stat())
    (writable_ancestor / "unrelated-sibling").mkdir()
    assert benchmark._directory_identity(
        writable_ancestor.stat()
    ) == stable_identity
    root = writable_ancestor / "sealed-root"
    raw = b"candidate bytes below a writable ancestor"
    digest = _write_candidate_object(root, raw)
    assert benchmark._read_candidate_object(
        root, digest, len(raw), "ancestor-policy fixture"
    ) == raw


def test_byte_gate_rejects_after_manifest_before_candidate_leaf_reads(
        tmp_path, monkeypatch):
    baseline, descriptor, root, _details = _build_rooted_fixture(tmp_path)
    baseline = replace(baseline, total_bytes=1)
    original_read = benchmark._read_candidate_object
    labels = []

    def counted_read(*args, **kwargs):
        labels.append(args[3])
        return original_read(*args, **kwargs)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("over-gate candidate reached frame replay")

    monkeypatch.setattr(benchmark, "_read_candidate_object", counted_read)
    monkeypatch.setattr(benchmark, "_replay_candidate", forbidden)
    with pytest.raises(ValueError, match="gate is 0"):
        benchmark.evaluate_candidate(baseline, descriptor, root, 10)
    assert labels == ["candidate proof manifest"]


def test_png_decoder_round_trip_composites_alpha_and_rejects_trailing():
    rgb = bytes(range(12))
    png = _encode_rgb_png(2, 2, rgb)
    assert benchmark.decode_png_rgb(png) == (2, 2, rgb)
    with pytest.raises(ValueError, match="termination"):
        benchmark.decode_png_rgb(png + b"x")

    scanline = b"\x00" + bytes((1, 2, 3, 0))
    rgba = (
        benchmark.PNG_SIGNATURE
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
        + _png_chunk(b"IDAT", zlib.compress(scanline))
        + _png_chunk(b"IEND", b"")
    )
    assert benchmark.decode_png_rgb(rgba) == (1, 1, b"\xff\xff\xff")

    transparent_rgb = (
        benchmark.PNG_SIGNATURE
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + _png_chunk(b"tRNS", b"\x00\xfd\x00\xfd\x00\xfd")
        + _png_chunk(b"IDAT", zlib.compress(b"\x00\xfd\xfd\xfd"))
        + _png_chunk(b"IEND", b"")
    )
    assert benchmark.decode_png_rgb(transparent_rgb) == (
        1, 1, b"\xff\xff\xff"
    )

    bomb = (
        benchmark.PNG_SIGNATURE
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + _png_chunk(b"IDAT", zlib.compress(b"\x00" * 10_000))
        + _png_chunk(b"IEND", b"")
    )
    with pytest.raises(ValueError, match="exact cap|wrong bounded length"):
        benchmark.decode_png_rgb(bomb)


@pytest.mark.parametrize(
    "case,error",
    [
        ("grayscale-plte", "forbids PLTE"),
        ("grayscale-alpha-plte", "forbids PLTE"),
        ("duplicate-plte", "duplicate PLTE"),
        ("duplicate-trns", "duplicate tRNS"),
        ("rgba-trns", "forbids tRNS"),
        ("split-idat", "must be contiguous"),
    ],
)
def test_png_decoder_rejects_forbidden_duplicate_and_split_chunks(case, error):
    color_type = {
        "grayscale-plte": 0,
        "grayscale-alpha-plte": 4,
        "rgba-trns": 6,
    }.get(case, 2)
    channels = {0: 1, 2: 3, 4: 2, 6: 4}[color_type]
    compressed = zlib.compress(b"\x00" + bytes(range(1, channels + 1)))
    chunks = []
    if case in {"grayscale-plte", "grayscale-alpha-plte"}:
        chunks.append(_png_chunk(b"PLTE", b"\x01\x02\x03"))
    elif case == "duplicate-plte":
        chunks.extend([
            _png_chunk(b"PLTE", b"\x01\x02\x03"),
            _png_chunk(b"PLTE", b"\x04\x05\x06"),
        ])
    elif case == "duplicate-trns":
        chunks.extend([
            _png_chunk(b"tRNS", b"\x00\x01\x00\x02\x00\x03"),
            _png_chunk(b"tRNS", b"\x00\x04\x00\x05\x00\x06"),
        ])
    elif case == "rgba-trns":
        chunks.append(_png_chunk(b"tRNS", b"\x00\x01"))
    if case == "split-idat":
        midpoint = len(compressed) // 2
        chunks.extend([
            _png_chunk(b"IDAT", compressed[:midpoint]),
            _png_chunk(b"tEXt", b"key\x00value"),
            _png_chunk(b"IDAT", compressed[midpoint:]),
        ])
    else:
        chunks.append(_png_chunk(b"IDAT", compressed))
    png = (
        benchmark.PNG_SIGNATURE
        + _png_chunk(
            b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, color_type, 0, 0, 0)
        )
        + b"".join(chunks)
        + _png_chunk(b"IEND", b"")
    )
    with pytest.raises(ValueError, match=error):
        benchmark.decode_png_rgb(png)


def test_png_alpha_composites_against_each_background_channel(monkeypatch):
    monkeypatch.setattr(benchmark, "PNG_ALPHA_BACKGROUND", (10, 20, 30))
    rgba = (
        benchmark.PNG_SIGNATURE
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
        + _png_chunk(b"IDAT", zlib.compress(b"\x00\x01\x02\x03\x00"))
        + _png_chunk(b"IEND", b"")
    )
    assert benchmark.decode_png_rgb(rgba) == (1, 1, bytes((10, 20, 30)))


def test_prepared_json_structure_is_bound_to_content_digest():
    original = b'{"value":"aa"}'
    changed = b'{"value":"bb"}'
    structure = imagery.inspect_json_structure(original, "prepared original")
    assert structure.raw_sha256 == hashlib.sha256(original).hexdigest()
    assert not hasattr(structure, "raw_identity")
    assert benchmark._strict_json(
        original, "prepared original", structure=structure
    ) == {"value": "aa"}
    with pytest.raises(ValueError, match="does not bind its bytes"):
        benchmark._strict_json(
            changed, "prepared changed", structure=structure
        )


def test_malformed_non_delimiter_token_is_rejected_by_strict_parser():
    raw = b'{"value":@}'
    structure = imagery.inspect_json_structure(raw, "malformed token")
    assert structure.nodes > 0
    with pytest.raises(ValueError, match="strict JSON"):
        imagery.parse_canonical_json(raw, "malformed token")


@pytest.mark.parametrize("axis_value", [None, "yes", [], 1, True])
def test_proof_v4_decision_projection_rejects_wrong_axis_types(axis_value):
    row = {
        "verdict": "KEEP",
        "confidence": "strong",
        "exists": {"call": "yes"},
        "public": {"call": "yes"},
        "serves": {"call": "yes"},
    }
    row["exists"] = axis_value
    with pytest.raises(ValueError, match="exists axis is malformed"):
        benchmark._decision_projection(row)


def test_cli_refuses_active_parent_before_parsing_or_charging(
        monkeypatch, capsys):
    parent = imagery.CanonicalJsonWorkAudit(
        1, reservation_scope="embedding-parent"
    )
    before = parent.report()

    def forbidden():
        raise AssertionError("embedded main reached argument parsing")

    monkeypatch.setattr(benchmark, "_parser", forbidden)
    token = imagery._ACTIVE_CANONICAL_JSON_WORK_AUDIT.set(parent)
    try:
        assert benchmark.main(["--candidate", "unused"]) == 2
        assert imagery._ACTIVE_CANONICAL_JSON_WORK_AUDIT.get() is parent
    finally:
        imagery._ACTIVE_CANONICAL_JSON_WORK_AUDIT.reset(token)
    assert parent.report() == before
    assert json.loads(capsys.readouterr().out) == {
        "error": (
            "benchmark main cannot run inside an active canonical JSON audit; "
            "it owns the full-run ledger"
        ),
        "status": "FAIL",
    }


@pytest.mark.parametrize("raw_ratio", ["0", "-2", "1.5", "ten"])
def test_cli_invalid_ratio_is_structured_fail_before_baseline(
        monkeypatch, capsys, raw_ratio):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("invalid ratio reached baseline measurement")

    monkeypatch.setattr(benchmark, "measure_proof_v4", forbidden)
    assert benchmark.main(["--require-ratio", raw_ratio]) == 2
    report = json.loads(capsys.readouterr().out)
    assert set(report) == {"status", "error"}
    assert report["status"] == "FAIL"
    assert "ratio" in report["error"]


@pytest.mark.parametrize("object_root", ["/", "//"])
def test_cli_root_without_traversable_component_is_structured_fail(
        tmp_path, monkeypatch, capsys, object_root):
    baseline, descriptor, _root, _details = _build_rooted_fixture(tmp_path)
    descriptor_path = tmp_path / "candidate.json"
    descriptor_path.write_bytes(imagery.canonical_json(descriptor))
    descriptor_path.chmod(0o600)
    monkeypatch.setattr(
        benchmark, "measure_proof_v4", lambda *_args, **_kwargs: baseline
    )
    monkeypatch.setattr(benchmark, "_acl_platform", lambda: "darwin")
    monkeypatch.setattr(
        benchmark.trusted_filesystem,
        "require_trivial_acl_fd",
        lambda *_args, **_kwargs: None,
    )
    assert benchmark.main([
        "--candidate", str(descriptor_path),
        "--object-root", object_root,
    ]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "FAIL"
    assert "traversable component" in report["error"]
    assert "local variable" not in report["error"]


def test_cli_baseline_reports_unavailable_acl_control(
        bear_baseline, tmp_path, monkeypatch, capsys):
    real_measure = benchmark.measure_proof_v4
    monkeypatch.setattr(benchmark.sys, "platform", "linux")
    monkeypatch.setattr(
        benchmark.trusted_filesystem,
        "require_trivial_acl_fd",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.delattr(benchmark.os, "listxattr", raising=False)

    def measured(*_args, **_kwargs):
        benchmark._inspect_trivial_acl(0, "simulated baseline trust anchor")
        return bear_baseline

    monkeypatch.setattr(benchmark, "measure_proof_v4", measured)
    assert benchmark.main([]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "BASELINE_ONLY"
    controls = report["filesystem_controls"]
    assert controls["phase"] == "whole-run"
    assert controls["status"] == "degraded"
    assert controls["whole_run_complete"] is False
    assert controls["real_achievement_governing_phase"] is None
    assert controls["real_achievement_controls_complete"] is None
    phase = controls["phases"]["cli-baseline-and-descriptor"]
    assert phase["phase"] == "cli-baseline-and-descriptor"
    assert phase["governs_real_achievement"] is False
    assert phase["complete_for_real_achievement"] is False
    assert phase["acl_inspection"] == {
        "platform": "linux",
        "policy": "trusted_filesystem.require_trivial_acl_fd",
        "required_platform_capability": (
            "Linux descriptor ACL policy via "
            "trusted_filesystem.require_trivial_acl_fd (fgetxattr), plus "
            "descriptor xattr enumeration rejecting unrecognized ACL backends"
        ),
        "status": "unavailable-degraded",
        "attempts": 1,
        "completed": 0,
        "unavailable": 1,
        "unavailable_reasons": {"descriptor-listxattr-missing": 1},
    }
    baseline_only_json = report["canonical_json_work"]
    assert baseline_only_json["reservation_scope"] == "cli-full-run"
    assert baseline_only_json["reserved_byte_operations"] == (
        benchmark.MAX_FULL_SCOPE_CANONICAL_JSON_OPERATIONS
    )
    assert baseline_only_json["reservation_components"] == (
        benchmark.FULL_SCOPE_CANONICAL_JSON_RESERVATIONS
    )
    assert baseline_only_json["phase_components"] == (
        benchmark.FULL_SCOPE_CANONICAL_JSON_PHASE_COMPONENTS
    )

    synthetic, descriptor, object_root, _details = _build_rooted_fixture(
        tmp_path / "full-cli"
    )
    descriptor_raw = imagery.canonical_json(descriptor)
    descriptor_path = tmp_path / "candidate-descriptor.json"
    descriptor_path.write_bytes(descriptor_raw)
    descriptor_path.chmod(0o644)

    def measured_full_scope(*args, **kwargs):
        measured_baseline = real_measure(*args, **kwargs)
        assert measured_baseline.proof_sha256 == bear_baseline.proof_sha256
        return synthetic

    monkeypatch.setattr(benchmark, "measure_proof_v4", measured_full_scope)
    assert benchmark.main([
        "--candidate", str(descriptor_path),
        "--object-root", str(object_root),
    ]) == 0
    full_report = json.loads(capsys.readouterr().out)
    assert full_report["status"] == "FIXTURE_ONLY"
    full_audit = full_report["canonical_json_work"]
    candidate_audit = full_report["candidate_gate"]["replay_budget"][
        "operation_equations"
    ]["canonical_json_operations"]["execution_audit"]
    assert candidate_audit == full_audit
    assert full_audit["reservation_scope"] == "cli-full-run"
    assert full_audit["reserved_byte_operations"] == sum(
        full_audit["reservation_components"].values()
    ) == benchmark.MAX_FULL_SCOPE_CANONICAL_JSON_OPERATIONS
    assert "report-emitting serialization is deliberately excluded" in (
        full_audit["execution_semantics"]
    )
    assert full_report["candidate_gate"]["replay_budget"][
        "operation_equations"
    ]["canonical_json_operations"][
        "candidate_reserved_byte_operations"
    ] == benchmark.MAX_REPLAY_CANONICAL_JSON_OPERATIONS

    phases = full_audit["phases"]
    descriptor_phase = phases["candidate-descriptor"]
    assert descriptor_phase["calls_by_boundary"] == {
        "canonical_hash": 1,
        "canonical_revalidation": 1,
        "canonical_serialization": 1,
        "json_decode": 1,
        "structural_scan": 1,
    }
    assert descriptor_phase["executed_by_boundary"] == {
        "canonical_hash": len(descriptor_raw),
        "canonical_revalidation": len(descriptor_raw),
        "canonical_serialization": len(descriptor_raw),
        "json_decode": len(descriptor_raw),
        "structural_scan": len(descriptor_raw),
    }

    registry_raw = benchmark.DEFAULT_REGISTRY.read_bytes()
    registry_document = json.loads(registry_raw)
    manifest_descriptor = registry_document["proofs"][
        benchmark.BEAR_CREEK_PROOF_SHA256
    ]
    manifest_raw = (
        benchmark.DEFAULT_REGISTRY.parent / manifest_descriptor["path"]
    ).read_bytes()
    manifest_document = json.loads(manifest_raw)
    canonical_registry = imagery.canonical_json(registry_document)
    canonical_manifest = imagery.canonical_json(manifest_document)
    canonical_table = imagery.canonical_json(manifest_document["objects"])
    baseline_phase = phases["initial-proof-v4-baseline"]
    assert baseline_phase["calls_by_boundary"] == {
        "canonical_hash": 4,
        "canonical_revalidation": 4,
        "canonical_serialization": 3,
        "json_decode": 2,
        "legacy_json_serialization": 2,
        "structural_scan": 2,
    }
    assert baseline_phase["executed_by_boundary"] == {
        "canonical_hash": (
            len(registry_raw) + 2 * len(manifest_raw) + len(canonical_table)
        ),
        "canonical_revalidation": 2 * (
            len(registry_raw) + len(manifest_raw)
        ),
        "canonical_serialization": (
            len(canonical_registry) + len(canonical_manifest)
            + len(canonical_table)
        ),
        "json_decode": len(registry_raw) + len(manifest_raw),
        "legacy_json_serialization": (
            len(registry_raw) + len(manifest_raw)
        ),
        "structural_scan": len(registry_raw) + len(manifest_raw),
    }
    assert set(phases) == {
        "initial-proof-v4-baseline", "candidate-descriptor",
        "candidate-load", "candidate-replay", "acquisition-approval",
    }
    components = full_audit["component_execution"]
    assert components["initial-proof-v4-baseline"][
        "executed_byte_operations"
    ] == baseline_phase["executed_byte_operations"]
    assert components["candidate-descriptor"][
        "executed_byte_operations"
    ] == descriptor_phase["executed_byte_operations"]
    assert components["candidate-evaluation"][
        "executed_byte_operations"
    ] == sum(
        phases[name]["executed_byte_operations"] for name in (
            "candidate-load", "candidate-replay", "acquisition-approval",
        )
    )
    assert components["candidate-evaluation"][
        "reserved_byte_operations"
    ] == benchmark.MAX_REPLAY_CANONICAL_JSON_OPERATIONS
    assert components["approval-baseline-rederivation"] == {
        "reserved_byte_operations": (
            benchmark.MAX_PROOF_V4_CANONICAL_JSON_OPERATIONS
        ),
        "executed_byte_operations": 0,
        "headroom_byte_operations": (
            benchmark.MAX_PROOF_V4_CANONICAL_JSON_OPERATIONS
        ),
    }
    for boundary, total in full_audit["executed_by_boundary"].items():
        assert total == sum(
            value["executed_by_boundary"].get(boundary, 0)
            for value in phases.values()
        )
    for boundary, total in full_audit["calls_by_boundary"].items():
        assert total == sum(
            value["calls_by_boundary"].get(boundary, 0)
            for value in phases.values()
        )


def test_whole_run_filesystem_verdict_labels_both_phases_and_pass_governor():
    cli = benchmark._FilesystemControlAudit(
        phase="cli-baseline-and-descriptor", governs_real_achievement=False
    ).report()
    candidate_audit = benchmark._FilesystemControlAudit(
        phase="candidate-admission", governs_real_achievement=True
    )
    candidate_audit.record_acl_completed()
    candidate = candidate_audit.report()
    controls = benchmark._whole_run_filesystem_controls(cli, candidate)
    assert controls["status"] == "degraded"
    assert controls["whole_run_complete"] is False
    assert controls["real_achievement_governing_phase"] == (
        "candidate-admission"
    )
    assert controls["real_achievement_controls_complete"] is True
    assert set(controls["phases"]) == {
        "cli-baseline-and-descriptor", "candidate-admission",
    }
    duplicate = dict(candidate)
    duplicate["phase"] = cli["phase"]
    with pytest.raises(ValueError, match="duplicate filesystem control phase"):
        benchmark._whole_run_filesystem_controls(cli, duplicate)


def test_cli_normalizes_malformed_proof_v4_decision_row(monkeypatch, capsys):
    def malformed_baseline(*_args, **_kwargs):
        return benchmark._decision_projection({
            "verdict": "KEEP",
            "confidence": "strong",
            "exists": "yes",
            "public": {"call": "yes"},
            "serves": {"call": "yes"},
        })

    monkeypatch.setattr(benchmark, "measure_proof_v4", malformed_baseline)
    assert benchmark.main([]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "FAIL"
    assert "exists axis is malformed" in report["error"]


def test_candidate_json_has_one_canonical_form():
    assert benchmark._strict_json(b'{"kind":"x","version":1}', "candidate") == {
        "kind": "x", "version": 1,
    }
    with pytest.raises(ValueError, match="duplicate"):
        benchmark._strict_json(b'{"version":1,"version":1}', "candidate")
    with pytest.raises(ValueError, match="noncanonical"):
        benchmark._strict_json(b'{"version": 1}', "candidate")


@pytest.mark.parametrize("reader", ["benchmark", "imagery", "renderer"])
def test_every_json_reader_rejects_depth_before_json_load(monkeypatch, reader):
    depth = imagery.MAX_CANONICAL_JSON_DEPTH + 1
    raw = b"[" * depth + b"0" + b"]" * depth

    def forbidden(*_args, **_kwargs):
        raise AssertionError("over-depth JSON reached json.loads")

    monkeypatch.setattr(json, "loads", forbidden)
    parse = {
        "benchmark": lambda: benchmark._strict_json(raw, "benchmark fixture"),
        "imagery": lambda: imagery.parse_canonical_json(raw, "imagery fixture"),
        "renderer": lambda: renderer.parse_bitmap_font(raw),
    }[reader]
    with pytest.raises(ValueError, match="structural depth"):
        parse()


@pytest.mark.parametrize("reader", ["benchmark", "imagery", "renderer"])
@pytest.mark.parametrize("error_type", [RecursionError, MemoryError, OverflowError])
def test_every_json_reader_normalizes_decoder_resource_failures(
        monkeypatch, reader, error_type):
    raw = FONT_PATH.read_bytes() if reader == "renderer" else b"{}"

    def fail(*_args, **_kwargs):
        raise error_type("simulated decoder exhaustion")

    monkeypatch.setattr(json, "loads", fail)
    parse = {
        "benchmark": lambda: benchmark._strict_json(raw, "benchmark fixture"),
        "imagery": lambda: imagery.parse_canonical_json(raw, "imagery fixture"),
        "renderer": lambda: renderer.parse_bitmap_font(raw),
    }[reader]
    with pytest.raises(ValueError, match="strict JSON"):
        parse()


def test_cli_returns_structured_fail_for_over_depth_candidate(
        tmp_path, monkeypatch, capsys):
    baseline = benchmark.BaselineMeasurement(
        proof_sha256=_hash("CLI structural baseline"),
        manifest_length=1,
        adjudications=1,
        frame_count=1,
        image_bytes=1,
        total_bytes=10,
        logical_objects=1,
        leaf_objects=1,
        largest_blob=1,
        by_rung={"z1": 1, "z2": 0, "z3": 0},
        frame_sources={},
        frame_dimensions={},
        decisions={},
    )
    monkeypatch.setattr(benchmark, "measure_proof_v4", lambda *_args: baseline)
    depth = imagery.MAX_CANONICAL_JSON_DEPTH + 1
    candidate_path = tmp_path / "deep-candidate.json"
    candidate_path.write_bytes(b"[" * depth + b"0" + b"]" * depth)
    object_root = tmp_path / "objects"
    object_root.mkdir()

    assert benchmark.main([
        "--candidate", str(candidate_path),
        "--object-root", str(object_root),
    ]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "FAIL"
    assert "structural depth" in report["error"]


@pytest.mark.parametrize("error_type", [
    AssertionError,
    AttributeError,
    TypeError,
    KeyError,
    NameError,
    UnboundLocalError,
    RecursionError,
    MemoryError,
    OverflowError,
])
def test_cli_normalizes_internal_failures_to_structured_fail(
        monkeypatch, capsys, error_type):
    failure = error_type("simulated CLI failure")

    def fail(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(benchmark, "measure_proof_v4", fail)
    assert benchmark.main([]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report == {
        "status": "FAIL",
        "error": str(failure),
    }
