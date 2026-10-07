"""Golden and hostile tests for the bounded fixed-point ScenePack renderer."""
from __future__ import annotations

import base64
import builtins
import copy
import hashlib
import json
import os
import socket
import subprocess
import sys
import typing
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
TOOLS = HERE / "parking-adjud" / "tools"
FONT_PATH = HERE / "test-fixtures" / "parking-overlay-font-v1.json"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import frame_renderer as renderer  # noqa: E402
import imagery_objects as imagery  # noqa: E402

# Updated only when the frozen renderer contract intentionally changes.
EXPECTED_BASE_SHA256 = "32d3e4ce52258fd2c65d752700d6bdbc16fed2ed2fbc396346e43e42a40f2d08"
EXPECTED_FRAME_SHA256 = "6779dcb6b4e6dfec03b2707bb32f2fafc9affdab8a4ccb0f784b6df41bb309a8"


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _solid_rgb(color: tuple[int, int, int]) -> bytes:
    return bytes(color) * imagery.CELL_PIXEL_COUNT


def test_renderer_runtime_annotations_resolve_iterable():
    for function in (renderer._blend, renderer._paint_pixels, renderer._paint_path):
        hints = typing.get_type_hints(function)
        assert any(
            getattr(annotation, "__origin__", None) is not None
            and "Iterable" in str(annotation)
            for annotation in hints.values()
        )


@pytest.fixture(scope="module")
def frame_fixture():
    font_bytes = FONT_PATH.read_bytes()
    font = renderer.parse_bitmap_font(font_bytes)
    matrix = "detail-v1"
    span = imagery.matrix_cell_span_um(matrix)
    colors = {
        (0, 1): (220, 20, 20),
        (1, 1): (20, 220, 20),
        (0, 0): (20, 20, 220),
        (1, 0): (220, 220, 20),
    }
    cells = {}
    payloads = {}
    references = {}
    masks = {}
    mask_recipes = {}
    source_objects = {}
    transform_objects = {}
    for coordinate in ((0, 1), (1, 1), (0, 0), (1, 0)):
        rgb = _solid_rgb(colors[coordinate])
        bounds = list(imagery.grid_cell_bounds(matrix, *coordinate))
        source = imagery.build_source_descriptor(
            rgb,
            provider="synthetic-fixture",
            product="fixture-rgb",
            layer="renderer-cells",
            capture_id=f"cell-{coordinate[0]}-{coordinate[1]}",
            capture_version="v1",
            retrieved_at="2026-10-05T12:00:00Z",
            width=imagery.CELL_WIDTH,
            height=imagery.CELL_HEIGHT,
            bounds_um=bounds,
        )
        reference, transform = imagery.build_source_transform_receipt(
            source, rgb, matrix, *coordinate
        )
        mask_recipe = imagery.build_critical_mask_recipe(reference)
        mask = imagery.derive_critical_mask(reference)
        payload = imagery.encode_qrgb(reference, 0)
        assertion = imagery.build_clean_source_assertion(source)
        acquisition = {
            "source_descriptor_ref": imagery.sha256_json(source),
            "clean_source_assertion_ref": imagery.sha256_json(assertion),
            "source_transform_receipt_ref": imagery.sha256_json(transform),
        }
        cell = imagery.build_imagery_cell(
            matrix,
            coordinate[0],
            coordinate[1],
            acquisition,
            payload,
            reference_rgb=reference,
            critical_mask=mask,
            critical_mask_recipe=mask_recipe,
        )
        cell_id = imagery.imagery_cell_id(cell)
        cells[cell_id] = cell
        payloads[cell["encoding"]["payload_ref"]] = payload
        references[cell["reference_rgb_ref"]] = reference
        masks[cell["critical_mask_ref"]] = mask
        mask_recipes[cell["critical_mask_recipe_ref"]] = imagery.canonical_json(
            mask_recipe
        )
        source_objects[imagery.sha256_json(source)] = source
        transform_objects[imagery.sha256_json(transform)] = transform

    extent = 40_000_000
    scene = {
        "version": 2,
        "kind": renderer.OVERLAY_SCENE_KIND,
        "source": {
            "dossier_sha256": _digest("dossier"),
            "geom_sha256": _digest("geom"),
            "generation_sha256": _digest("generation"),
        },
        "font_ref": font.sha256,
        "style": copy.deepcopy(renderer.OVERLAY_STYLE),
        "trails": [[
            [span - 30_000_000, span],
            [span + 30_000_000, span],
        ]],
        "lots": [
            {
                "fid": 1,
                "rings_um": [],
                "point_um": [span - 20_000_000, span + 20_000_000],
            },
            {
                "fid": 2,
                "rings_um": [[
                    [span - 20_000_000, span],
                    [span + 20_000_000, span],
                    [span + 20_000_000, span - 20_000_000],
                ]],
                "point_um": None,
            },
        ],
    }
    recipe = {
        "version": 2,
        "kind": renderer.FRAME_RECIPE_KIND,
        "fid": 2,
        "rung": "z2",
        "viewport_um": [
            span - extent, span - extent, span + extent, span + extent,
        ],
        "output": {"width": 96, "height": 96, "rgb_sha256": "0" * 64},
        "matrix": matrix,
        "cells": list(cells),
        "resampler": renderer.FRAME_RESAMPLER,
        "overlay_scene_ref": renderer.overlay_scene_sha256(scene),
        "style": renderer.OVERLAY_STYLE_ID,
        "header": "#2 Z2",
    }
    decoded = {
        cell_id: imagery.decode_qrgb(payloads[cell["encoding"]["payload_ref"]])
        for cell_id, cell in cells.items()
    }
    references_by_cell = {
        cell_id: references[cell["reference_rgb_ref"]]
        for cell_id, cell in cells.items()
    }
    work = renderer.RenderWork()
    base = renderer.reconstruct_base_rgb(
        recipe, cells, decoded, scene, font_bytes, work=work
    )
    rendered = renderer.render_frame_rgb(
        recipe,
        cells,
        payloads,
        {},
        scene,
        font_bytes,
        reference_rgbs=references,
        critical_masks=masks,
        critical_mask_recipes=mask_recipes,
        verify_output=False,
    )
    recipe["output"]["rgb_sha256"] = hashlib.sha256(rendered).hexdigest()
    return {
        "font_bytes": font_bytes,
        "font": font,
        "cells": cells,
        "payloads": payloads,
        "references": references,
        "references_by_cell": references_by_cell,
        "masks": masks,
        "mask_recipes": mask_recipes,
        "source_objects": source_objects,
        "transform_objects": transform_objects,
        "scene": scene,
        "recipe": recipe,
        "decoded": decoded,
        "base": base,
        "rendered": rendered,
    }


def _render(fixture, recipe=None, scene=None, verify_output=True, work=None):
    return renderer.render_frame_rgb(
        recipe or fixture["recipe"],
        fixture["cells"],
        fixture["payloads"],
        {},
        scene or fixture["scene"],
        fixture["font_bytes"],
        reference_rgbs=fixture["references"],
        critical_masks=fixture["masks"],
        critical_mask_recipes=fixture["mask_recipes"],
        verify_output=verify_output,
        work=work,
    )


def test_frame_key_has_one_canonical_fid_rung_grammar():
    assert renderer.parse_frame_key("0/z1") == (0, "z1")
    assert renderer.parse_frame_key("42/z3") == (42, "z3")
    for value in (
        "01/z2", "1/Z2", "1/z4", "1/z2/extra", "-1/z2", "+1/z2",
        "1//z2", "1z2", 1,
    ):
        with pytest.raises(ValueError, match="canonical FID/rung"):
            renderer.parse_frame_key(value)


def test_fixed_point_clamp_seam_has_pinned_base_and_frame_hash(frame_fixture):
    fixture = frame_fixture
    assert renderer.FRAME_RESAMPLER == "bilinear-rgb8-fixed-clamp-v1"
    assert hashlib.sha256(fixture["base"]).hexdigest() == EXPECTED_BASE_SHA256
    assert hashlib.sha256(fixture["rendered"]).hexdigest() == EXPECTED_FRAME_SHA256
    assert fixture["recipe"]["output"]["rgb_sha256"] == EXPECTED_FRAME_SHA256
    assert _render(fixture) == fixture["rendered"]


def test_subject_overlay_is_after_trail_and_header_is_not_clipped(frame_fixture):
    fixture = frame_fixture
    rendered = fixture["rendered"]
    width = fixture["recipe"]["output"]["width"]
    center_offset = (48 * width + 48) * 3
    assert tuple(rendered[center_offset:center_offset + 3]) == (255, 30, 30)
    capacity = renderer.header_character_capacity(width, fixture["font"])
    assert capacity == 7
    assert len(fixture["recipe"]["header"]) <= capacity


def test_renderer_is_identical_in_another_process_and_working_root(
        frame_fixture, tmp_path):
    fixture = frame_fixture
    bundle = {
        "recipe": fixture["recipe"],
        "cells": fixture["cells"],
        "payloads": {
            key: base64.b64encode(value).decode()
            for key, value in fixture["payloads"].items()
        },
        "references": {
            key: base64.b64encode(value).decode()
            for key, value in fixture["references"].items()
        },
        "masks": {
            key: base64.b64encode(value).decode()
            for key, value in fixture["masks"].items()
        },
        "mask_recipes": {
            key: base64.b64encode(value).decode()
            for key, value in fixture["mask_recipes"].items()
        },
        "scene": fixture["scene"],
        "font": base64.b64encode(fixture["font_bytes"]).decode(),
    }
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(json.dumps(bundle, sort_keys=True))
    script = """
import base64, hashlib, json, sys
from pathlib import Path
import frame_renderer
bundle = json.loads(Path(sys.argv[1]).read_text())
decode = lambda values: {key: base64.b64decode(value) for key, value in values.items()}
rgb = frame_renderer.render_frame_rgb(
    bundle['recipe'], bundle['cells'], decode(bundle['payloads']), {},
    bundle['scene'], base64.b64decode(bundle['font']),
    reference_rgbs=decode(bundle['references']),
    critical_masks=decode(bundle['masks']),
    critical_mask_recipes=decode(bundle['mask_recipes']))
print(hashlib.sha256(rgb).hexdigest())
"""
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(TOOLS)
    completed = subprocess.run(
        [sys.executable, "-c", script, str(bundle_path)],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.stdout.strip() == EXPECTED_FRAME_SHA256


def test_renderer_performs_no_lookup_after_bytes_are_supplied(
        frame_fixture, monkeypatch):
    fixture = frame_fixture

    def forbidden(*_args, **_kwargs):
        raise AssertionError("external lookup attempted")

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    assert hashlib.sha256(_render(fixture)).hexdigest() == EXPECTED_FRAME_SHA256


def test_exact_cell_coverage_rejects_missing_extra_order_and_overlap(frame_fixture):
    fixture = frame_fixture
    recipe = fixture["recipe"]
    cells = fixture["cells"]
    prepared = renderer.prepare_overlay_scene(
        fixture["scene"], fixture["font_bytes"]
    )
    missing = dict(cells)
    missing.pop(next(iter(missing)))
    with pytest.raises(ValueError, match="missing or unreferenced"):
        renderer._validate_frame_recipe_prepared(recipe, missing, prepared)
    extra_id = "f" * 64
    with pytest.raises(ValueError, match="missing or unreferenced"):
        renderer._validate_frame_recipe_prepared(
            recipe, cells | {extra_id: next(iter(cells.values()))}, prepared
        )
    reordered = copy.deepcopy(recipe)
    reordered["cells"] = list(reversed(reordered["cells"]))
    with pytest.raises(ValueError, match="complete-cell coverage"):
        renderer._validate_frame_recipe_prepared(reordered, cells, prepared)
    overlapping = copy.deepcopy(cells)
    first_id, second_id = list(overlapping)[:2]
    overlapping[second_id]["grid"] = copy.deepcopy(overlapping[first_id]["grid"])
    with pytest.raises(ValueError, match="content addressed|coverage"):
        renderer._validate_frame_recipe_prepared(recipe, overlapping, prepared)


def test_recipe_rejects_dynamic_schema_caps_and_terminal_strips(frame_fixture):
    fixture = frame_fixture
    prepared = renderer.prepare_overlay_scene(
        fixture["scene"], fixture["font_bytes"]
    )
    dynamic = copy.deepcopy(fixture["recipe"])
    dynamic["matrix"] = "context-v1"
    with pytest.raises(ValueError, match="rung and matrix"):
        renderer._validate_frame_recipe_prepared(dynamic, fixture["cells"], prepared)
    extra = copy.deepcopy(fixture["recipe"])
    extra["decoder"] = "system-default"
    with pytest.raises(ValueError, match="schema"):
        renderer._validate_frame_recipe_prepared(extra, fixture["cells"], prepared)
    oversized = copy.deepcopy(fixture["recipe"])
    oversized["output"]["width"] = renderer.MAX_OUTPUT_SIDE + 1
    with pytest.raises(ValueError, match="exceeds"):
        renderer._validate_frame_recipe_prepared(oversized, fixture["cells"], prepared)
    terminal = copy.deepcopy(fixture["recipe"])
    limit = imagery.renderable_limit_um("detail-v1")
    terminal["viewport_um"] = [limit, 0, limit + 1, 1]
    with pytest.raises(ValueError, match="terminal grid strip"):
        renderer._validate_frame_recipe_prepared(terminal, fixture["cells"], prepared)


def test_overlay_scene_is_closed_frozen_and_rejects_malformed_geometry(
        frame_fixture):
    scene = frame_fixture["scene"]
    assert renderer.validate_overlay_scene(scene) == scene
    changed_style = copy.deepcopy(scene)
    changed_style["style"]["trail"]["width_px"] = 3
    with pytest.raises(ValueError, match="frozen"):
        renderer.validate_overlay_scene(changed_style)
    float_geometry = copy.deepcopy(scene)
    float_geometry["trails"][0][0][0] = 1.5
    with pytest.raises(ValueError, match="integer"):
        renderer.validate_overlay_scene(float_geometry)
    closed_ring = copy.deepcopy(scene)
    closed_ring["lots"][1]["rings_um"][0].append(
        copy.deepcopy(closed_ring["lots"][1]["rings_um"][0][0])
    )
    with pytest.raises(ValueError, match="omit the repeated closing"):
        renderer.validate_overlay_scene(closed_ring)


def test_payload_font_scene_output_and_authority_tamper_fail_closed(frame_fixture):
    fixture = frame_fixture
    wrong_output = copy.deepcopy(fixture["recipe"])
    wrong_output["output"]["rgb_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="RGB SHA-256 mismatch"):
        _render(fixture, recipe=wrong_output)
    payload_ref = next(iter(fixture["payloads"]))
    tampered_payloads = dict(fixture["payloads"])
    tampered_payloads[payload_ref] += b"x"
    with pytest.raises(ValueError, match="extra bytes"):
        renderer.render_frame_rgb(
            fixture["recipe"], fixture["cells"], tampered_payloads, {},
            fixture["scene"], fixture["font_bytes"],
            reference_rgbs=fixture["references"],
            critical_masks=fixture["masks"],
            critical_mask_recipes=fixture["mask_recipes"],
        )
    with pytest.raises(ValueError, match="rooted reference and mask"):
        renderer.render_frame_rgb(
            fixture["recipe"], fixture["cells"], fixture["payloads"], {},
            fixture["scene"], fixture["font_bytes"],
        )
    changed_scene = copy.deepcopy(fixture["scene"])
    changed_scene["trails"] = []
    with pytest.raises(ValueError, match="scene reference"):
        _render(fixture, scene=changed_scene)


def test_font_and_header_are_validated_before_render_or_hash(frame_fixture):
    fixture = frame_fixture
    font_bytes = fixture["font_bytes"]
    assert renderer.parse_bitmap_font(font_bytes).sha256 == hashlib.sha256(
        font_bytes
    ).hexdigest()
    with pytest.raises(ValueError, match="duplicate"):
        renderer.parse_bitmap_font(
            b'{"kind":"parking-bitmap-font","kind":"x"}'
        )
    noncanonical = json.dumps(json.loads(font_bytes), indent=1).encode()
    with pytest.raises(ValueError, match="noncanonical"):
        renderer.parse_bitmap_font(noncanonical)

    unsupported = copy.deepcopy(fixture["recipe"])
    unsupported["header"] = "#2 €"
    with pytest.raises(ValueError, match="unsupported font codepoint"):
        renderer.validate_frame_recipe(
            unsupported, fixture["cells"], fixture["scene"], font_bytes
        )
    clipped = copy.deepcopy(fixture["recipe"])
    clipped["header"] = "A" * 8
    with pytest.raises(ValueError, match="width capacity 7"):
        renderer.validate_frame_recipe(
            clipped, fixture["cells"], fixture["scene"], font_bytes
        )
    vertically_clipped = copy.deepcopy(fixture["recipe"])
    vertically_clipped["output"]["height"] = 20
    with pytest.raises(ValueError, match="vertical extent"):
        renderer.validate_frame_recipe(
            vertically_clipped, fixture["cells"], fixture["scene"], font_bytes
        )


def _recipe_for_scene(fixture, scene, viewport=None, width=96, header="#2 Z2"):
    recipe = copy.deepcopy(fixture["recipe"])
    recipe["overlay_scene_ref"] = renderer.overlay_scene_sha256(scene)
    recipe["output"] = {"width": width, "height": width, "rgb_sha256": "0" * 64}
    recipe["header"] = header
    if viewport is not None:
        recipe["viewport_um"] = viewport
    return recipe


def test_domain_edge_segments_are_preclipped_and_distance_independent(frame_fixture):
    fixture = frame_fixture
    scene = copy.deepcopy(fixture["scene"])
    limit = imagery.WEB_MERCATOR_LIMIT_UM
    scene["trails"] = [[[-limit, -limit], [limit, limit]]]
    span = imagery.matrix_cell_span_um("detail-v1")
    scene["lots"] = [{
        "fid": 2,
        "rings_um": [],
        "point_um": [span, span],
    }]
    recipe = _recipe_for_scene(fixture, scene)
    prepared = renderer.prepare_overlay_scene(scene, fixture["font_bytes"])
    work = renderer.RenderWork()
    _reference, candidate = renderer.render_prepared_frame_pair(
        recipe,
        fixture["cells"],
        fixture["decoded"],
        fixture["references_by_cell"],
        prepared,
        work=work,
        verify_output=False,
    )
    assert len(candidate) == 96 * 96 * 3
    report = work.report()
    assert report["operations"] <= report["max_operations"]
    assert report["counters"]["line_steps"] <= 8 * 96
    assert report["counters"]["segments_rasterized"] == 4


def test_huge_subject_radius_is_output_clipped_before_enumeration(frame_fixture):
    fixture = frame_fixture
    cell = next(iter(fixture["cells"].values()))
    x0, y0, _x1, _y1 = cell["grid"]["bounds_um"]
    viewport = [x0 + 1_000_000, y0 + 1_000_000,
                x0 + 1_000_001, y0 + 1_000_001]
    scene = copy.deepcopy(fixture["scene"])
    scene["trails"] = []
    scene["lots"] = [{
        "fid": 2,
        "rings_um": [],
        "point_um": [x0 + 1_000_000, y0 + 1_000_000],
    }]
    one_cell_id = next(
        cell_id for cell_id, value in fixture["cells"].items()
        if value["grid"]["x"] == cell["grid"]["x"]
        and value["grid"]["y"] == cell["grid"]["y"]
    )
    recipe = _recipe_for_scene(
        fixture, scene, viewport=viewport, width=64, header="#2"
    )
    recipe["cells"] = [one_cell_id]
    prepared = renderer.prepare_overlay_scene(scene, fixture["font_bytes"])
    work = renderer.RenderWork()
    renderer.render_prepared_frame_pair(
        recipe,
        {one_cell_id: fixture["cells"][one_cell_id]},
        {one_cell_id: fixture["decoded"][one_cell_id]},
        {one_cell_id: fixture["references_by_cell"][one_cell_id]},
        prepared,
        work=work,
        verify_output=False,
    )
    report = work.report()
    assert report["operations"] <= report["max_operations"]
    assert report["counters"].get("circle_tests", 0) <= 64 * 64 * 2


def test_tiny_output_rejects_scene_over_explicit_point_budget(frame_fixture):
    fixture = frame_fixture
    scene = copy.deepcopy(fixture["scene"])
    limit = imagery.WEB_MERCATOR_LIMIT_UM
    scene["trails"] = [[
        [-limit + index, -limit] for index in range(9000)
    ]]
    recipe = _recipe_for_scene(fixture, scene, width=32, header="#")
    prepared = renderer.prepare_overlay_scene(scene, fixture["font_bytes"])
    with pytest.raises(ValueError, match="point budget"):
        renderer.render_prepared_frame_pair(
            recipe,
            fixture["cells"],
            fixture["decoded"],
            fixture["references_by_cell"],
            prepared,
            verify_output=False,
        )


def test_frame_fidelity_receipt_is_recomputed_from_actual_rgb(frame_fixture):
    fixture = frame_fixture
    recipe_ref = imagery.sha256_json(fixture["recipe"])
    source = _digest("source-png")
    receipt = renderer.build_frame_fidelity_receipt(
        "2/z2",
        recipe_ref,
        source,
        fixture["rendered"],
        fixture["rendered"],
        96,
        96,
        0,
    )
    assert receipt["max_cell_mode"] == 0
    assert receipt["passed"] is True
    assert renderer.validate_frame_fidelity_receipt(
        imagery.canonical_json(receipt),
        frame_key="2/z2",
        recipe_ref=recipe_ref,
        source_png_sha256=source,
        reference_rgb=fixture["rendered"],
        candidate_rgb=fixture["rendered"],
        width=96,
        height=96,
        max_cell_mode=0,
    ) == receipt
    damaged = bytearray(fixture["rendered"])
    damaged[0:30] = b"\x00" * 30
    with pytest.raises(ValueError, match="differs from recomputation"):
        renderer.validate_frame_fidelity_receipt(
            receipt,
            frame_key="2/z2",
            recipe_ref=recipe_ref,
            source_png_sha256=source,
            reference_rgb=fixture["rendered"],
            candidate_rgb=bytes(damaged),
            width=96,
            height=96,
            max_cell_mode=0,
        )


def test_frame_fidelity_ceiling_is_derived_from_max_actual_cell_mode():
    metrics = imagery.measure_raster_quality(
        b"\x00\x00\x00", b"\x00\x00\x00", 1, 1
    )
    lossless_drift = dict(metrics)
    lossless_drift["max_abs_channel_error"] = 1
    assert not renderer.frame_fidelity_metrics_pass(
        lossless_drift, 0, 1, 1
    )

    metrics["max_abs_channel_error"] = 3
    metrics["minimum_window_luma_ssim_ppm"] = (
        imagery.QUALITY_POLICY["min_minimum_window_luma_ssim_ppm"]
    )
    assert renderer.frame_fidelity_metrics_pass(metrics, 3, 1, 1)
    metrics["minimum_window_luma_ssim_ppm"] -= 1
    assert not renderer.frame_fidelity_metrics_pass(metrics, 3, 1, 1)


def test_real_resolution_plan_is_deterministic_and_does_not_claim_timing():
    plan = renderer.real_resolution_budget_plan()
    assert plan["frame_count"] == 150
    assert plan["pixels_per_frame"] == 1_048_576
    assert plan["total_output_pixels"] == 157_286_400
    assert plan["max_operations_per_frame"] == 134_217_728
    assert not any("second" in key or "time" in key for key in plan)
