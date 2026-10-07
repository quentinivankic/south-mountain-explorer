"""Hostile tests for rooted ScenePack imagery authority and qRGB."""
from __future__ import annotations

import copy
import hashlib
import sys
import zlib
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
TOOLS = HERE / "parking-adjud" / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import imagery_objects as imagery  # noqa: E402


def _pattern_rgb() -> bytes:
    rgb = bytearray(imagery.DECODED_RGB_LENGTH)
    for row in range(imagery.CELL_HEIGHT):
        for column in range(imagery.CELL_WIDTH):
            value = 50 if column < imagery.CELL_WIDTH // 2 else 200
            offset = (row * imagery.CELL_WIDTH + column) * 3
            rgb[offset:offset + 3] = bytes((value, value, value))
    return bytes(rgb)


@pytest.fixture(scope="module")
def authority_fixture():
    reference = _pattern_rgb()
    matrix = "detail-v1"
    bounds = list(imagery.grid_cell_bounds(matrix, 0, 0))
    source = imagery.build_source_descriptor(
        reference,
        provider="synthetic-fixture",
        product="fixture-rgb",
        layer="quality-authority",
        capture_id="authority-cell-0-0",
        capture_version="v1",
        retrieved_at="2026-10-05T12:00:00Z",
        width=imagery.CELL_WIDTH,
        height=imagery.CELL_HEIGHT,
        bounds_um=bounds,
    )
    transformed, transform = imagery.build_source_transform_receipt(
        source, reference, matrix, 0, 0
    )
    assert transformed == reference
    mask_recipe = imagery.build_critical_mask_recipe(reference)
    mask = imagery.derive_critical_mask(reference)
    candidate = imagery.quantize_rgb(reference, 1)
    receipt = imagery.build_quality_receipt(reference, candidate, mask, 1)
    assert receipt["passed"] is True
    assertion = imagery.build_clean_source_assertion(source)
    acquisition = {
        "source_descriptor_ref": imagery.sha256_json(source),
        "clean_source_assertion_ref": imagery.sha256_json(assertion),
        "source_transform_receipt_ref": imagery.sha256_json(transform),
    }
    return {
        "reference": reference,
        "candidate": candidate,
        "source": source,
        "transform": transform,
        "mask_recipe": mask_recipe,
        "mask": mask,
        "receipt": receipt,
        "acquisition": acquisition,
    }


def test_qrgb_all_modes_round_trip_with_exact_error_bounds(authority_fixture):
    reference = authority_fixture["reference"]
    for mode in imagery.QRGB_MODES:
        payload = imagery.encode_qrgb(reference, mode)
        decoded = imagery.decode_qrgb_payload(payload)
        assert decoded.rgb == imagery.quantize_rgb(reference, mode)
        assert decoded.mode == mode
        assert decoded.payload_sha256 == hashlib.sha256(payload).hexdigest()
        assert decoded.payload_length == len(payload)
        assert max(
            abs(left - right) for left, right in zip(reference, decoded.rgb)
        ) <= mode


def test_qrgb_rejects_header_hash_extra_tamper_bomb_and_oversize(
        authority_fixture, monkeypatch):
    reference = authority_fixture["reference"]
    payload = imagery.encode_qrgb(reference, 0)
    fields = list(imagery.QRGB_HEADER.unpack_from(payload))
    fields[1] -= 1
    wrong_width = imagery.QRGB_HEADER.pack(*fields) + payload[imagery.QRGB_HEADER.size:]
    with pytest.raises(ValueError, match="header"):
        imagery.decode_qrgb(wrong_width)

    fields = list(imagery.QRGB_HEADER.unpack_from(payload))
    fields[-1] = bytes([fields[-1][0] ^ 1]) + fields[-1][1:]
    wrong_hash = imagery.QRGB_HEADER.pack(*fields) + payload[imagery.QRGB_HEADER.size:]
    with pytest.raises(ValueError, match="SHA-256"):
        imagery.decode_qrgb(wrong_hash)
    with pytest.raises(ValueError, match="extra bytes"):
        imagery.decode_qrgb(payload + b"x")
    tampered = bytearray(payload)
    tampered[-4] ^= 0x40
    with pytest.raises(ValueError, match="zlib|SHA-256|termination"):
        imagery.decode_qrgb(bytes(tampered))

    oversized_rgb = b"\x00" * (imagery.DECODED_RGB_LENGTH + 1)
    compressed = zlib.compress(oversized_rgb, level=9)
    bomb = imagery.QRGB_HEADER.pack(
        imagery.QRGB_MAGIC,
        imagery.CELL_WIDTH,
        imagery.CELL_HEIGHT,
        imagery.CELL_CHANNELS,
        imagery.QRGB_COLORSPACE_SRGB,
        0,
        0,
        imagery.DECODED_RGB_LENGTH,
        len(compressed),
        hashlib.sha256(oversized_rgb[:imagery.DECODED_RGB_LENGTH]).digest(),
    ) + compressed
    with pytest.raises(ValueError, match="decoded-length cap|wrong decoded length"):
        imagery.decode_qrgb(bomb)

    monkeypatch.setattr(imagery, "MAX_QRGB_PAYLOAD_BYTES", 64)
    with pytest.raises(ValueError, match="encoded-size cap"):
        imagery.decode_qrgb(b"x" * 65)

    def endless_empty_chunks():
        while True:
            yield b""

    with pytest.raises(ValueError, match="immutable bytes"):
        imagery.decode_qrgb(endless_empty_chunks())


def test_offline_source_is_exact_rooted_fixed_and_replayed(authority_fixture):
    fixture = authority_fixture
    source = fixture["source"]
    reference = fixture["reference"]
    assert source["conversion"]["decoder"] == imagery.SOURCE_DECODER
    assert source["conversion"]["decoder_version"] == imagery.SOURCE_DECODER_VERSION
    assert source["response"]["orientation"] == imagery.SOURCE_ORIENTATION
    assert source["conversion"]["output_color_space"] == "srgb-rgb8"
    assert source["request"] == {
        "provider": "synthetic-fixture",
        "product": "fixture-rgb",
        "layer": "quality-authority",
        "capture_id": "authority-cell-0-0",
        "capture_version": "v1",
    }
    assertion = imagery.build_clean_source_assertion(source)
    assert imagery.validate_clean_source_assertion(assertion, source) == assertion
    assert imagery.validate_source_descriptor(source, reference, reference) == source
    assert fixture["transform"]["sample_tap_bound"] == (
        imagery.CELL_PIXEL_COUNT * 4
    )
    identity_execution = {}
    assert imagery.validate_source_transform_receipt(
        fixture["transform"],
        source,
        reference,
        reference,
        _execution_counts=identity_execution,
    ) == fixture["transform"]
    assert identity_execution == {
        "source_transform_identity_copies": 1,
        "source_transform_output_pixels": imagery.CELL_PIXEL_COUNT,
    }

    changed = bytearray(reference)
    changed[0] ^= 1
    with pytest.raises(ValueError, match="exact response bytes"):
        imagery.validate_source_descriptor(source, bytes(changed))
    with pytest.raises(ValueError, match="schema"):
        imagery.validate_source_descriptor(source | {"url": "https://invalid"})
    with pytest.raises(ValueError, match="contract"):
        imagery.validate_source_transform_receipt(
            fixture["transform"] | {"decoder": "dynamic"},
            source,
            reference,
            reference,
        )

    bounds = list(imagery.grid_cell_bounds("detail-v1", 2, 3))
    corners = bytes((255, 0, 0, 0, 255, 0, 0, 0, 255, 255, 255, 0))
    small_source = imagery.build_source_descriptor(
        corners,
        provider="synthetic-fixture",
        product="fixture-rgb",
        layer="transform-corners",
        capture_id="corner-cell-2-3",
        capture_version="v1",
        retrieved_at="2026-10-05T12:00:00Z",
        width=2,
        height=2,
        bounds_um=bounds,
    )
    transformed, transform = imagery.build_source_transform_receipt(
        small_source, corners, "detail-v1", 2, 3
    )
    assert transformed[:3] == b"\xff\x00\x00"
    assert transformed[(imagery.CELL_WIDTH - 1) * 3:imagery.CELL_WIDTH * 3] == b"\x00\xff\x00"
    bottom_left = (imagery.CELL_HEIGHT - 1) * imagery.CELL_WIDTH * 3
    assert transformed[bottom_left:bottom_left + 3] == b"\x00\x00\xff"
    bilinear_execution = {}
    assert imagery.validate_source_transform_receipt(
        transform,
        small_source,
        corners,
        transformed,
        _execution_counts=bilinear_execution,
    ) == transform
    assert bilinear_execution == {
        "source_transform_bilinear_scans": 1,
        "source_transform_output_pixels": imagery.CELL_PIXEL_COUNT,
        "source_transform_taps": imagery.CELL_PIXEL_COUNT * 4,
    }


def test_source_lineage_tamper_fails_every_identity_and_conversion_field(
        authority_fixture):
    source = authority_fixture["source"]
    response = authority_fixture["reference"]
    mutations = []
    for field, value in (
        ("media_type", "image/png"),
        ("width", imagery.CELL_WIDTH - 1),
        ("orientation", "bottom-left-row-major"),
        ("alpha", "straight"),
        ("color_profile", "display-p3"),
    ):
        changed = copy.deepcopy(source)
        changed["response"][field] = value
        mutations.append(changed)
    for field, value in (
        ("decoder", "system-default"),
        ("decoder_version", "latest"),
        ("output_color_profile", "display-p3"),
        ("output_orientation", "bottom-left-row-major"),
        ("output_alpha", "straight"),
    ):
        changed = copy.deepcopy(source)
        changed["conversion"][field] = value
        mutations.append(changed)
    changed_capture = copy.deepcopy(source)
    changed_capture["request"]["capture_version"] = "v2"
    mutations.append(changed_capture)
    for changed in mutations:
        with pytest.raises(ValueError):
            imagery.validate_source_descriptor(changed, response, response)

    forged_assertion = imagery.build_clean_source_assertion(source)
    forged_assertion["assertion"] = "caller-says-clean"
    with pytest.raises(ValueError, match="differs from source lineage"):
        imagery.validate_clean_source_assertion(forged_assertion, source)


def test_critical_mask_is_deterministic_and_covers_edges_borders(authority_fixture):
    fixture = authority_fixture
    mask = imagery.validate_critical_mask(
        fixture["mask"], fixture["mask_recipe"], fixture["reference"]
    )
    assert all(mask[column] for column in range(imagery.CELL_WIDTH))
    assert all(
        mask[(imagery.CELL_HEIGHT - 1) * imagery.CELL_WIDTH + column]
        for column in range(imagery.CELL_WIDTH)
    )
    center = imagery.CELL_WIDTH // 2
    assert mask[100 * imagery.CELL_WIDTH + center]
    changed = bytearray(mask)
    changed[100 * imagery.CELL_WIDTH + center] = 0
    with pytest.raises(ValueError, match="derivation"):
        imagery.validate_critical_mask(
            bytes(changed), fixture["mask_recipe"], fixture["reference"]
        )


def test_quality_receipt_uses_local_windows_full_edges_and_recomputation(
        authority_fixture):
    fixture = authority_fixture
    receipt = fixture["receipt"]
    assert receipt["ssim_window_count"] == 4096
    assert receipt["edge_domain_pixels"] == imagery.CELL_PIXEL_COUNT
    assert receipt["mean_window_luma_ssim_ppm"] >= 995_000
    assert receipt["minimum_window_luma_ssim_ppm"] >= 900_000
    assert imagery.validate_quality_receipt(
        receipt,
        reference_rgb=fixture["reference"],
        candidate_rgb=fixture["candidate"],
        critical_mask=fixture["mask"],
    ) == receipt

    forged = copy.deepcopy(receipt)
    forged["sum_abs_channel_error"] = 0
    forged["receipt_sha256"] = imagery.sha256_json({
        key: value for key, value in forged.items() if key != "receipt_sha256"
    })
    with pytest.raises(ValueError, match="recomputed metrics"):
        imagery.validate_quality_receipt(
            forged,
            reference_rgb=fixture["reference"],
            candidate_rgb=fixture["candidate"],
            critical_mask=fixture["mask"],
        )

    oversized = copy.deepcopy(receipt)
    oversized["edge_compared_pixels"] = imagery.CELL_PIXEL_COUNT + 1
    oversized["receipt_sha256"] = imagery.sha256_json({
        key: value for key, value in oversized.items() if key != "receipt_sha256"
    })
    with pytest.raises(ValueError, match="exceeds its maximum"):
        imagery.validate_quality_receipt(oversized)


def _passing_metrics() -> dict[str, int]:
    metrics = {
        "max_abs_channel_error": 3,
        "sum_abs_channel_error": 0,
        "sum_squared_error": 0,
        "psnr_millidb": 999_999,
        "ssim_window_count": 4096,
        "mean_window_luma_ssim_ppm": 1_000_000,
        "minimum_window_luma_ssim_ppm": 1_000_000,
        "edge_domain_pixels": imagery.CELL_PIXEL_COUNT,
        "edge_compared_pixels": 10_000,
        "edge_direction_matches": 10_000,
        "edge_direction_agreement_ppm": 1_000_000,
        "introduced_edge_pixels": 2,
        "removed_edge_pixels": 0,
        "direction_changed_edge_pixels": 0,
        "edge_change_pixels": 2,
        "border_edge_change_pixels": 0,
        "introduced_edge_component_count": 1,
        "max_connected_introduced_edge_pixels": 2,
        "removed_edge_component_count": 0,
        "max_connected_removed_edge_pixels": 0,
        "direction_changed_edge_component_count": 0,
        "max_connected_direction_changed_edge_pixels": 0,
        "edge_change_component_count": 1,
        "max_connected_edge_change_pixels": 2,
    }
    return metrics


def test_exact_connected_edge_threshold_two_passes_three_fails():
    metrics = _passing_metrics()
    assert imagery.quality_metrics_pass(
        metrics, 3, imagery.DECODED_RGB_LENGTH
    )
    metrics["introduced_edge_pixels"] = 3
    metrics["edge_change_pixels"] = 3
    metrics["max_connected_introduced_edge_pixels"] = 3
    metrics["max_connected_edge_change_pixels"] = 3
    assert not imagery.quality_metrics_pass(
        metrics, 3, imagery.DECODED_RGB_LENGTH
    )


@pytest.mark.parametrize(
    "field,exact_value,one_over_value",
    [
        ("max_abs_channel_error", 3, 4),
        (
            "sum_abs_channel_error",
            imagery.DECODED_RGB_LENGTH
            * imagery.QUALITY_POLICY["max_mean_abs_error_milli"] // 1000,
            imagery.DECODED_RGB_LENGTH
            * imagery.QUALITY_POLICY["max_mean_abs_error_milli"] // 1000 + 1,
        ),
        (
            "sum_squared_error",
            imagery.DECODED_RGB_LENGTH
            * imagery.QUALITY_POLICY["max_squared_error_tenths_per_sample"] // 10,
            imagery.DECODED_RGB_LENGTH
            * imagery.QUALITY_POLICY["max_squared_error_tenths_per_sample"] // 10 + 1,
        ),
        ("psnr_millidb", 42_000, 41_999),
        ("mean_window_luma_ssim_ppm", 995_000, 994_999),
        ("minimum_window_luma_ssim_ppm", 900_000, 899_999),
        ("max_connected_introduced_edge_pixels", 2, 3),
        ("max_connected_removed_edge_pixels", 2, 3),
        ("max_connected_direction_changed_edge_pixels", 2, 3),
        ("max_connected_edge_change_pixels", 2, 3),
        ("border_edge_change_pixels", 2, 3),
    ],
)
def test_every_cell_quality_threshold_passes_exactly_and_fails_one_unit_over(
        field, exact_value, one_over_value):
    metrics = _passing_metrics()
    metrics[field] = exact_value
    assert imagery.quality_metrics_pass(
        metrics, 3, imagery.DECODED_RGB_LENGTH
    ), field
    metrics[field] = one_over_value
    assert not imagery.quality_metrics_pass(
        metrics, 3, imagery.DECODED_RGB_LENGTH
    ), field


def test_direction_agreement_uses_exact_cross_multiplication_at_rounding_edge():
    metrics = _passing_metrics()
    metrics["edge_compared_pixels"] = 1001
    metrics["edge_direction_matches"] = 1000
    metrics["edge_direction_agreement_ppm"] = 999_000
    assert imagery.quality_metrics_pass(
        metrics, 3, imagery.DECODED_RGB_LENGTH
    )
    imagery.validate_quality_metrics(metrics, imagery.CELL_WIDTH, imagery.CELL_HEIGHT)
    metrics["edge_direction_matches"] = 999
    metrics["edge_direction_agreement_ppm"] = 998_001
    assert not imagery.quality_metrics_pass(
        metrics, 3, imagery.DECODED_RGB_LENGTH
    )
    imagery.validate_quality_metrics(metrics, imagery.CELL_WIDTH, imagery.CELL_HEIGHT)


@pytest.mark.parametrize(
    "field,exact_value,one_over_value",
    [
        ("edge_direction_matches", 1000, 999),
        ("max_connected_seam_edge_change_pixels", 2, 3),
    ],
)
def test_every_seam_quality_threshold_passes_exactly_and_fails_one_unit_over(
        field, exact_value, one_over_value):
    metrics = {
        "edge_compared_samples": 1001,
        "edge_direction_matches": 1000,
        "max_connected_seam_edge_change_pixels": 2,
    }
    metrics[field] = exact_value
    assert imagery.seam_quality_metrics_pass(metrics), field
    metrics[field] = one_over_value
    assert not imagery.seam_quality_metrics_pass(metrics), field


def test_edge_metrics_catch_introduced_removed_rotated_border_and_mask_boundary():
    width = height = 32
    empty = [0] * (width * height)
    vertical = []
    horizontal = []
    for row in range(height):
        for column in range(width):
            vertical.append(0 if column < width // 2 else 40)
            horizontal.append(0 if row < height // 2 else 40)
    mask = bytes(width * height)

    introduced = imagery._edge_metrics(empty, vertical, mask, width, height)
    assert introduced["introduced_edge_pixels"] > 3
    assert introduced["max_connected_introduced_edge_pixels"] > 3
    removed = imagery._edge_metrics(vertical, empty, mask, width, height)
    assert removed["removed_edge_pixels"] > 3
    assert removed["max_connected_removed_edge_pixels"] > 3
    rotated = imagery._edge_metrics(vertical, horizontal, mask, width, height)
    assert rotated["direction_changed_edge_pixels"] > 3
    assert rotated["max_connected_edge_change_pixels"] > 3

    border = [0] * (width * height)
    for row in range(height):
        border[row * width] = 40
    border_metrics = imagery._edge_metrics(empty, border, mask, width, height)
    assert border_metrics["border_edge_change_pixels"] > 0

    outside_mask = bytearray(width * height)
    outside_mask[0] = 1
    masked_metrics = imagery._edge_metrics(
        empty, vertical, bytes(outside_mask), width, height
    )
    assert masked_metrics["introduced_edge_pixels"] == introduced["introduced_edge_pixels"]


def test_cell_replay_recomputes_exact_serialized_lossy_receipt(authority_fixture):
    fixture = authority_fixture
    payload = imagery.encode_qrgb(fixture["reference"], 1)
    cell = imagery.build_imagery_cell(
        "detail-v1",
        0,
        0,
        fixture["acquisition"],
        payload,
        fixture["receipt"],
        reference_rgb=fixture["reference"],
        critical_mask=fixture["mask"],
        critical_mask_recipe=fixture["mask_recipe"],
    )
    assert cell["quality_receipt_ref"] == imagery.sha256_canonical_bytes(
        imagery.canonical_json(fixture["receipt"])
    )
    assert imagery.validate_imagery_cell(
        cell,
        payload,
        imagery.canonical_json(fixture["receipt"]),
        reference_rgb=fixture["reference"],
        critical_mask=fixture["mask"],
        critical_mask_recipe=imagery.canonical_json(fixture["mask_recipe"]),
    ) == fixture["candidate"]

    with pytest.raises(ValueError, match="requires rooted reference"):
        imagery.validate_imagery_cell(cell, payload, fixture["receipt"])
    forged = copy.deepcopy(fixture["receipt"])
    forged["reference_rgb_sha256"] = "f" * 64
    forged["receipt_sha256"] = imagery.sha256_json({
        key: value for key, value in forged.items() if key != "receipt_sha256"
    })
    forged_cell = copy.deepcopy(cell)
    forged_cell["quality_receipt_ref"] = imagery.sha256_json(forged)
    with pytest.raises(ValueError, match="recomputed metrics|differs"):
        imagery.validate_imagery_cell(
            forged_cell,
            payload,
            forged,
            reference_rgb=fixture["reference"],
            critical_mask=fixture["mask"],
            critical_mask_recipe=fixture["mask_recipe"],
        )


def test_lossless_cell_still_requires_source_mask_authority(authority_fixture):
    fixture = authority_fixture
    payload = imagery.encode_qrgb(fixture["reference"], 0)
    cell = imagery.build_imagery_cell(
        "detail-v1",
        0,
        0,
        fixture["acquisition"],
        payload,
        reference_rgb=fixture["reference"],
        critical_mask=fixture["mask"],
        critical_mask_recipe=fixture["mask_recipe"],
    )
    assert imagery.validate_imagery_cell(
        cell,
        payload,
        reference_rgb=fixture["reference"],
        critical_mask=fixture["mask"],
        critical_mask_recipe=fixture["mask_recipe"],
    ) == fixture["reference"]
    with pytest.raises(ValueError, match="requires rooted reference"):
        imagery.build_imagery_cell(
            "detail-v1", 0, 0, fixture["acquisition"], payload
        )


def test_grid_rejects_both_terminal_strips_and_accepts_exact_limits():
    for matrix in sorted(imagery.MATRIX_PIXEL_UM):
        span = imagery.matrix_cell_span_um(matrix)
        limit = imagery.renderable_limit_um(matrix)
        assert imagery.grid_cell_bounds(matrix, 0, 0) == (0, 0, span, span)
        assert imagery.expected_cell_coordinates(
            matrix, [limit - 1, 0, limit, 1]
        ) == ((limit // span - 1, 0),)
        assert imagery.expected_cell_coordinates(
            matrix, [-limit, 0, -limit + 1, 1]
        ) == ((-limit // span, 0),)
        with pytest.raises(ValueError, match="terminal grid strip"):
            imagery.expected_cell_coordinates(
                matrix, [limit, 0, limit + 1, 1]
            )
        with pytest.raises(ValueError, match="terminal grid strip"):
            imagery.expected_cell_coordinates(
                matrix, [-limit - 1, 0, -limit, 1]
            )
    mercator = imagery.WEB_MERCATOR_LIMIT_UM
    with pytest.raises(ValueError, match="wraps the dateline"):
        imagery.validate_viewport([mercator - 1, 0, -mercator + 1, 1])


def _seam_cell(
        x: int, reference: bytes, candidate: bytes,
) -> tuple[str, dict[str, object]]:
    cell = {
        "version": 2,
        "kind": "parking-imagery-cell",
        "grid": {
            "scheme": imagery.GRID_SCHEME,
            "matrix": "detail-v1",
            "x": x,
            "y": 0,
            "bounds_um": list(imagery.grid_cell_bounds("detail-v1", x, 0)),
            "width": imagery.CELL_WIDTH,
            "height": imagery.CELL_HEIGHT,
        },
        "acquisition": {
            "source_descriptor_ref": hashlib.sha256(f"source{x}".encode()).hexdigest(),
            "clean_source_assertion_ref": hashlib.sha256(
                f"assertion{x}".encode()
            ).hexdigest(),
            "source_transform_receipt_ref": hashlib.sha256(f"transform{x}".encode()).hexdigest(),
        },
        "encoding": {
            "codec": imagery.QRGB_CODEC,
            "max_abs_channel_error": 1,
            "payload_ref": hashlib.sha256(f"payload{x}".encode()).hexdigest(),
            "decoded_length": imagery.DECODED_RGB_LENGTH,
            "decoded_rgb_sha256": imagery.sha256_bytes(candidate),
        },
        "reference_rgb_ref": imagery.sha256_bytes(reference),
        "critical_mask_ref": hashlib.sha256(f"mask{x}".encode()).hexdigest(),
        "critical_mask_recipe_ref": hashlib.sha256(f"recipe{x}".encode()).hexdigest(),
        "quality_receipt_ref": hashlib.sha256(f"quality{x}".encode()).hexdigest(),
    }
    return imagery.imagery_cell_id(cell), cell


def test_seam_receipt_replays_and_catches_local_boundary_damage():
    left_ref = bytes((20, 20, 20)) * imagery.CELL_PIXEL_COUNT
    right_ref = bytes((50, 50, 50)) * imagery.CELL_PIXEL_COUNT
    left_candidate = left_ref
    right_candidate = bytearray(right_ref)
    for row in range(12):
        offset = (row * imagery.CELL_WIDTH) * 3
        right_candidate[offset:offset + 3] = bytes((20, 20, 20))
    first_id, first = _seam_cell(0, left_ref, left_candidate)
    second_id, second = _seam_cell(1, right_ref, bytes(right_candidate))
    cells = {first_id: first, second_id: second}
    references = {first_id: left_ref, second_id: right_ref}
    candidates = {first_id: left_candidate, second_id: bytes(right_candidate)}
    receipt = imagery.build_seam_quality_receipt(cells, references, candidates)
    assert receipt["removed_edge_samples"] == 12
    assert receipt["max_connected_seam_edge_change_pixels"] == 12
    assert receipt["passed"] is False
    assert imagery.validate_seam_quality_receipt(
        receipt,
        cells=cells,
        reference_rgbs=references,
        candidate_rgbs=candidates,
        require_passed=False,
    ) == receipt
    with pytest.raises(ValueError, match="did not pass"):
        imagery.validate_seam_quality_receipt(receipt)


def test_canonical_json_and_accounting_reject_ambiguity_and_zero_length():
    raw = b'{"kind":"x","version":1}'
    assert imagery.parse_canonical_json(raw, "fixture") == {
        "kind": "x", "version": 1,
    }
    with pytest.raises(ValueError, match="duplicate"):
        imagery.parse_canonical_json(b'{"kind":"x","kind":"y"}', "fixture")
    with pytest.raises(ValueError, match="noncanonical"):
        imagery.parse_canonical_json(b'{"version": 1,"kind":"x"}', "fixture")

    records = [{
        "sha256": "a" * 64,
        "length": 10,
        "category": "cell-payload",
        "matrix": "detail-v1",
    }]
    assert imagery.account_scenepack_bytes(records + [copy.deepcopy(records[0])])[
        "total_bytes"
    ] == 10
    partitioned = imagery.account_scenepack_bytes(records + [{
        "sha256": "b" * 64,
        "length": 7,
        "category": "frame-set",
        "matrix": None,
    }])
    assert partitioned["by_matrix"] == {
        "detail-v1": 10,
        "unattributed": 7,
    }
    assert sum(partitioned["by_matrix"].values()) == partitioned["total_bytes"]
    assert partitioned["by_matrix_semantics"] == (
        "complete byte partition: matrix-named buckets contain records bound "
        "to that matrix; unattributed contains records with matrix null"
    )
    with pytest.raises(ValueError, match="minimum"):
        imagery.account_scenepack_bytes([records[0] | {"length": 0}])
    with pytest.raises(ValueError, match="conflicting"):
        imagery.account_scenepack_bytes([
            records[0], records[0] | {"category": "frame-recipe"},
        ])


def test_canonical_json_work_audit_counts_every_boundary_and_refuses_overrun():
    raw = b'{"kind":"x","version":1}'
    with imagery.audit_canonical_json_work(len(raw) * 7) as audit:
        parsed = imagery.parse_canonical_json(raw, "audited fixture")
        assert imagery.sha256_json(parsed) == hashlib.sha256(raw).hexdigest()
    report = audit.report()
    assert report["reserved_byte_operations"] == len(raw) * 7
    assert report["executed_byte_operations"] == len(raw) * 7
    assert report["headroom_byte_operations"] == 0
    assert report["executed_by_boundary"] == {
        "canonical_hash": len(raw) * 2,
        "canonical_revalidation": len(raw),
        "canonical_serialization": len(raw) * 2,
        "json_decode": len(raw),
        "structural_scan": len(raw),
    }
    assert report["calls_by_boundary"] == {
        "canonical_hash": 2,
        "canonical_revalidation": 1,
        "canonical_serialization": 2,
        "json_decode": 1,
        "structural_scan": 1,
    }

    with pytest.raises(
            ValueError, match="executed byte-operation reservation exceeded"):
        with imagery.audit_canonical_json_work(len(raw) - 1):
            imagery.inspect_json_structure(raw, "over-budget fixture")

    with imagery.audit_canonical_json_work(
            len(raw) * 2, scope="outer-test") as outer:
        with imagery.canonical_json_work_phase("outer-phase"):
            imagery.sha256_canonical_bytes(raw)
        with imagery.audit_canonical_json_work(0) as nested:
            assert nested is outer
            assert nested.reserved_byte_operations == len(raw) * 2
            with imagery.canonical_json_work_phase("nested-phase"):
                imagery.sha256_canonical_bytes(raw)
        assert imagery._ACTIVE_CANONICAL_JSON_WORK_AUDIT.get() is outer
    nested_report = outer.report()
    assert nested_report["reservation_scope"] == "outer-test"
    assert nested_report["executed_byte_operations"] == len(raw) * 2
    assert nested_report["phases"] == {
        "nested-phase": {
            "executed_byte_operations": len(raw),
            "executed_by_boundary": {"canonical_hash": len(raw)},
            "calls_by_boundary": {"canonical_hash": 1},
        },
        "outer-phase": {
            "executed_byte_operations": len(raw),
            "executed_by_boundary": {"canonical_hash": len(raw)},
            "calls_by_boundary": {"canonical_hash": 1},
        },
    }

    rgb = b"\x00\x01\x02" * 4
    with imagery.audit_canonical_json_work(0) as binary_audit:
        imagery.sha256_bytes(rgb)
        payload = imagery.encode_qrgb(rgb * (imagery.DECODED_RGB_LENGTH // len(rgb)), 0)
        imagery.decode_qrgb_payload(payload)
    assert binary_audit.report()["executed_byte_operations"] == 0


def test_audit_rejection_branches_are_fail_closed():
    mapped = imagery.CanonicalJsonWorkAudit(
        2,
        reservation_scope="mapped-test",
        reservation_components={"mapped-component": 2},
        phase_components={"mapped-phase": "mapped-component"},
    )
    before = mapped.report()
    with pytest.raises(ValueError, match="has no reserved component"):
        mapped.charge("json_decode", 1, "unmapped-phase")
    assert mapped.report() == before

    with pytest.raises(ValueError, match="components do not close"):
        with imagery.audit_canonical_json_work(
                2, components={"short-component": 1}):
            pass
    with pytest.raises(ValueError, match="phase components are invalid"):
        with imagery.audit_canonical_json_work(
                2,
                components={"mapped-component": 2},
                phase_components={"mapped-phase": "missing-component"}):
            pass

    with imagery.audit_canonical_json_work(
            2,
            scope="outer-component-test",
            components={"mapped-component": 2},
            phase_components={"mapped-phase": "mapped-component"},
    ) as outer:
        outer_before = outer.report()
        with pytest.raises(
                ValueError, match="component maps cannot be installed"):
            with imagery.audit_canonical_json_work(
                    2,
                    components={"mapped-component": 2},
                    phase_components={
                        "mapped-phase": "mapped-component",
                    },
            ):
                pass
        with pytest.raises(
                ValueError, match="reservation exceeds its active parent"):
            with imagery.audit_canonical_json_work(3):
                pass
        assert outer.report() == outer_before


def test_json_structure_scan_is_escape_aware_and_accepts_exact_limits():
    escaped = imagery.canonical_json({
        "value": '[{\"nested-looking\":\"]}\\\\"',
    })
    structure = imagery.inspect_json_structure(escaped, "escaped fixture")
    assert structure.max_depth == 1
    assert structure.containers == 1
    assert imagery.parse_canonical_json(escaped, "escaped fixture") == {
        "value": '[{\"nested-looking\":\"]}\\\\"',
    }

    depth = imagery.MAX_CANONICAL_JSON_DEPTH
    near_depth = b"[" * depth + b"0" + b"]" * depth
    parsed = imagery.parse_canonical_json(near_depth, "near-depth fixture")
    for _index in range(depth):
        assert isinstance(parsed, list) and len(parsed) == 1
        parsed = parsed[0]
    assert parsed == 0

    entries = imagery.MAX_CANONICAL_JSON_CONTAINER_ENTRIES
    near_entries = b"[" + b",".join([b"0"] * entries) + b"]"
    structure = imagery.inspect_json_structure(
        near_entries, "near-container fixture"
    )
    assert structure.container_entries == entries
    assert len(imagery.parse_canonical_json(
        near_entries, "near-container fixture"
    )) == entries


def test_json_structure_scan_rejects_deep_arrays_objects_and_expansion(
        monkeypatch):
    depth = imagery.MAX_CANONICAL_JSON_DEPTH + 1
    deep_array = b"[" * depth + b"0" + b"]" * depth
    deep_object = b'{"a":' * depth + b"0" + b"}" * depth

    def forbidden(*_args, **_kwargs):
        raise AssertionError("structurally rejected JSON reached json.loads")

    monkeypatch.setattr(imagery.json, "loads", forbidden)
    for raw in (deep_array, deep_object):
        with pytest.raises(ValueError, match="structural depth"):
            imagery.parse_canonical_json(raw, "deep fixture")

    monkeypatch.setattr(imagery, "MAX_CANONICAL_JSON_NODES", 8)
    assert imagery.inspect_json_structure(
        b"[0,0,0,0,0,0,0]", "near-node fixture"
    ).nodes == 8
    with pytest.raises(ValueError, match="node expansion"):
        imagery.inspect_json_structure(
            b"[0,0,0,0,0,0,0,0]", "node expansion fixture"
        )

    monkeypatch.setattr(imagery, "MAX_CANONICAL_JSON_STRING_BYTES", 8)
    assert imagery.inspect_json_structure(
        b'{"k":"12345678"}', "near-string fixture"
    ).maximum_string_expansion_bytes == 8
    with pytest.raises(ValueError, match="string expansion"):
        imagery.inspect_json_structure(
            b'{"k":"123456789"}', "string expansion fixture"
        )


@pytest.mark.parametrize("error_type", [RecursionError, MemoryError, OverflowError])
def test_canonical_json_normalizes_serializer_resource_failures(
        monkeypatch, error_type):
    def fail(*_args, **_kwargs):
        raise error_type("simulated serializer exhaustion")

    monkeypatch.setattr(imagery.json, "dumps", fail)
    with pytest.raises(ValueError, match="serialization exceeded resource limits"):
        imagery.canonical_json({"bounded": True})
