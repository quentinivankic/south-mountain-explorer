#!/usr/bin/env python3
"""Closed deterministic ScenePack v1 frame renderer.

The renderer consumes only supplied rooted cells, scene bytes, and font bytes.
Raster work is preclipped, spatially subset, deterministically counted, and
rejected when an output-derived budget would be exceeded.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Iterable, Mapping

import imagery_objects as imagery

FRAME_RECIPE_KIND = "parking-frame-recipe"
OVERLAY_SCENE_KIND = "parking-overlay-scene"
FRAME_FIDELITY_RECEIPT_KIND = "parking-frame-fidelity-receipt"
BASELINE_PNG_DECODER = "png-rgb8-noninterlaced-white-composite-v1"
FRAME_RESAMPLER = "bilinear-rgb8-fixed-clamp-v1"
OVERLAY_STYLE_ID = "parking-overlay-v2"
MAX_OUTPUT_SIDE = 1024
MAX_OUTPUT_PIXELS = MAX_OUTPUT_SIDE * MAX_OUTPUT_SIDE
MAX_TRAILS = 4096
MAX_LOTS = 4096
MAX_RINGS_PER_LOT = 32
MAX_POINTS_PER_PATH = 65_536
MAX_SCENE_POINTS = 1_000_000
MIN_RENDER_OPERATION_BUDGET = 100_000
RENDER_OPERATIONS_PER_PIXEL = 128
MIN_SCENE_POINT_BUDGET = 4096
SCENE_POINTS_PER_PIXEL = 8
FIXED_ONE = 1 << 16

RUNG_MATRIX = {
    "z1": "context-v1",
    "z2": "detail-v1",
    "z3": "confirm-v1",
}
_FRAME_KEY_RE = re.compile(r"(0|[1-9][0-9]*)/(z[123])\Z")


def parse_frame_key(frame_key: object) -> tuple[int, str]:
    """Parse the sole canonical proof frame identity grammar."""
    if not isinstance(frame_key, str):
        raise ValueError("frame key must be a canonical FID/rung string")
    match = _FRAME_KEY_RE.fullmatch(frame_key)
    if match is None:
        raise ValueError("frame key must be a canonical FID/rung string")
    fid = int(match.group(1))
    if fid > (1 << 63) - 1:
        raise ValueError("frame key FID exceeds its fixed range")
    return fid, match.group(2)


OVERLAY_STYLE = {
    "id": OVERLAY_STYLE_ID,
    "layer_order": [
        "trail-casing", "trail", "other-lot", "subject-lot", "header",
    ],
    "clipping": "preclipped-output-plus-stroke-halo-v1",
    "alpha": "source-over-rgb8-fixed-v1",
    "trail_casing": {"rgba": [10, 10, 10, 170], "width_px": 8},
    "trail": {"rgba": [255, 230, 0, 255], "width_px": 4},
    "other_lot": {
        "rgba": [255, 150, 0, 255],
        "width_px": 3,
        "point_radius_px": 6,
    },
    "subject_lot": {
        "rgba": [255, 30, 30, 255],
        "width_px": 5,
        "point_radius_um": 20_000_000,
    },
    "header": {
        "background_rgba": [0, 0, 0, 170],
        "text_rgba": [255, 235, 120, 255],
        "height_px": 32,
        "x_px": 8,
        "y_px": 7,
        "font_scale": 2,
    },
}

_SCENE_KEYS = {
    "version", "kind", "source", "font_ref", "style", "trails", "lots",
}
_SOURCE_KEYS = {"dossier_sha256", "geom_sha256", "generation_sha256"}
_LOT_KEYS = {"fid", "rings_um", "point_um"}
_RECIPE_KEYS = {
    "version", "kind", "fid", "rung", "viewport_um", "output", "matrix",
    "cells", "resampler", "overlay_scene_ref", "style", "header",
}
_OUTPUT_KEYS = {"width", "height", "rgb_sha256"}
_FONT_KEYS = {
    "version", "kind", "width", "height", "advance", "glyphs", "aliases",
}
_SHA256_CHARACTERS = frozenset("0123456789abcdef")


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
    if (not isinstance(value, str) or len(value) != 64
            or not set(value).issubset(_SHA256_CHARACTERS)):
        raise ValueError(f"{label} must be a lowercase SHA-256")
    return value


def _point(value: object, label: str) -> tuple[int, int]:
    if (not isinstance(value, list) or len(value) != 2
            or any(type(coordinate) is not int for coordinate in value)):
        raise ValueError(f"{label} must contain two integer micrometre coordinates")
    x, y = value
    limit = imagery.WEB_MERCATOR_LIMIT_UM
    if not (-limit <= x <= limit and -limit <= y <= limit):
        raise ValueError(f"{label} is outside EPSG:3857")
    return x, y


def _path(value: object, label: str, minimum: int) -> tuple[tuple[int, int], ...]:
    if (not isinstance(value, list)
            or not minimum <= len(value) <= MAX_POINTS_PER_PATH):
        raise ValueError(f"{label} point count is invalid")
    points = tuple(_point(point, label) for point in value)
    if len(set(points)) < minimum:
        raise ValueError(f"{label} has insufficient distinct points")
    return points


@dataclass(frozen=True)
class OverlaySceneMetrics:
    point_count: int
    path_count: int
    segment_count: int
    lot_count: int


def validate_overlay_scene_metrics(
        value: object,
) -> tuple[dict[str, object], OverlaySceneMetrics]:
    scene = _closed(value, _SCENE_KEYS, "overlay scene")
    if scene.get("version") != 2 or scene.get("kind") != OVERLAY_SCENE_KIND:
        raise ValueError("overlay scene version or kind is unsupported")
    source = _closed(scene.get("source"), _SOURCE_KEYS, "overlay scene source")
    for field_name in sorted(_SOURCE_KEYS):
        _sha256(source.get(field_name), f"overlay source {field_name}")
    _sha256(scene.get("font_ref"), "overlay font reference")
    if scene.get("style") != OVERLAY_STYLE:
        raise ValueError("overlay scene style is not the frozen v2 style")

    trails = scene.get("trails")
    if not isinstance(trails, list) or len(trails) > MAX_TRAILS:
        raise ValueError("overlay scene trail count exceeds its cap")
    point_count = 0
    path_count = 0
    segment_count = 0
    for index, trail in enumerate(trails):
        points = _path(trail, f"trail {index}", 2)
        point_count += len(points)
        path_count += 1
        segment_count += len(points) - 1

    lots = scene.get("lots")
    if not isinstance(lots, list) or not lots or len(lots) > MAX_LOTS:
        raise ValueError("overlay scene lot count is invalid")
    previous_fid = -1
    for index, raw_lot in enumerate(lots):
        lot = _closed(raw_lot, _LOT_KEYS, f"overlay lot {index}")
        fid = _integer(lot.get("fid"), f"overlay lot {index} fid", 0)
        if fid <= previous_fid:
            raise ValueError("overlay lots must have unique ascending fids")
        previous_fid = fid
        rings = lot.get("rings_um")
        point = lot.get("point_um")
        if not isinstance(rings, list) or len(rings) > MAX_RINGS_PER_LOT:
            raise ValueError("overlay lot ring vector is invalid")
        if rings and point is not None:
            raise ValueError("overlay polygon lot must not also have a point")
        if not rings and point is None:
            raise ValueError("overlay lot must have rings or one point")
        for ring_index, ring in enumerate(rings):
            points = _path(ring, f"overlay lot {fid} ring {ring_index}", 3)
            if points[0] == points[-1]:
                raise ValueError("overlay rings omit the repeated closing point")
            point_count += len(points)
            path_count += 1
            segment_count += len(points)
        if point is not None:
            _point(point, f"overlay lot {fid} point")
            point_count += 1
    if point_count > MAX_SCENE_POINTS:
        raise ValueError("overlay scene point count exceeds its cap")
    return scene, OverlaySceneMetrics(
        point_count=point_count,
        path_count=path_count,
        segment_count=segment_count,
        lot_count=len(lots),
    )


def _validate_overlay_scene_with_count(
        value: object,
) -> tuple[dict[str, object], int]:
    scene, metrics = validate_overlay_scene_metrics(value)
    return scene, metrics.point_count


def validate_overlay_scene(value: object) -> dict[str, object]:
    return _validate_overlay_scene_with_count(value)[0]


def overlay_scene_sha256(value: object) -> str:
    return imagery.sha256_json(validate_overlay_scene(value))


@dataclass(frozen=True)
class BitmapFont:
    width: int
    height: int
    advance: int
    glyphs: Mapping[int, tuple[int, ...]]
    aliases: Mapping[int, int]
    sha256: str

    def rows(self, character: str) -> tuple[int, ...]:
        codepoint = ord(character)
        codepoint = self.aliases.get(codepoint, codepoint)
        try:
            return self.glyphs[codepoint]
        except KeyError as error:
            raise ValueError(
                f"header contains unsupported font codepoint U+{ord(character):04X}"
            ) from error


def _parse_bitmap_font_document(
        value: object, document_sha256: str,
) -> BitmapFont:
    """Prepare one already parsed, content-addressed bitmap font document."""
    if not imagery.valid_sha256(document_sha256):
        raise ValueError("bitmap font document hash is invalid")
    font = _closed(value, _FONT_KEYS, "bitmap font")
    if font.get("version") != 1 or font.get("kind") != "parking-bitmap-font":
        raise ValueError("bitmap font version or kind is unsupported")
    width = _integer(font.get("width"), "bitmap font width", 1, 8)
    height = _integer(font.get("height"), "bitmap font height", 1, 16)
    advance = _integer(font.get("advance"), "bitmap font advance", width, 16)
    raw_glyphs = font.get("glyphs")
    if not isinstance(raw_glyphs, dict) or not 1 <= len(raw_glyphs) <= 128:
        raise ValueError("bitmap font glyph table is invalid")
    glyphs: dict[int, tuple[int, ...]] = {}
    for key, rows in raw_glyphs.items():
        if (not isinstance(key, str) or not key.isdecimal()
                or str(int(key)) != key):
            raise ValueError("bitmap font glyph key is noncanonical")
        codepoint = int(key)
        if (not isinstance(rows, list) or len(rows) != height
                or any(type(row) is not int or not 0 <= row < 1 << width
                       for row in rows)):
            raise ValueError("bitmap font glyph rows are invalid")
        glyphs[codepoint] = tuple(rows)
    required = {32, 35, 40, 41, 45, 46, 47, 61, 63, 183}
    required.update(range(48, 58))
    required.update(range(65, 91))
    if not required.issubset(glyphs):
        raise ValueError("bitmap font lacks a required header glyph")

    raw_aliases = font.get("aliases")
    if not isinstance(raw_aliases, dict) or len(raw_aliases) > 64:
        raise ValueError("bitmap font alias table is invalid")
    aliases: dict[int, int] = {}
    for key, target in raw_aliases.items():
        if (not isinstance(key, str) or not key.isdecimal()
                or str(int(key)) != key or type(target) is not int
                or target not in glyphs):
            raise ValueError("bitmap font alias is invalid")
        aliases[int(key)] = target
    if aliases != {codepoint: codepoint - 32 for codepoint in range(97, 123)}:
        raise ValueError("bitmap font lowercase alias policy mismatch")
    return BitmapFont(
        width=width,
        height=height,
        advance=advance,
        glyphs=glyphs,
        aliases=aliases,
        sha256=document_sha256,
    )


def parse_bitmap_font(raw: bytes) -> BitmapFont:
    """Defensively parse the sole canonical bounded bitmap font bytes."""
    if not isinstance(raw, bytes) or not raw or len(raw) > 64 * 1024:
        raise ValueError("bitmap font bytes are empty or oversized")
    value = imagery.parse_canonical_json(raw, "bitmap font")
    return _parse_bitmap_font_document(
        value, imagery.sha256_canonical_bytes(raw)
    )


@dataclass(frozen=True)
class PreparedOverlay:
    scene: Mapping[str, object]
    scene_sha256: str
    font: BitmapFont
    point_count: int


def _prepare_validated_overlay_scene(
        scene: Mapping[str, object], scene_sha256: str, font: BitmapFont,
        metrics: OverlaySceneMetrics,
) -> PreparedOverlay:
    """Create the retained view only after the caller reserves aggregate work."""
    _sha256(scene_sha256, "validated overlay scene reference")
    if scene["font_ref"] != font.sha256:
        raise ValueError("overlay scene does not bind the supplied bitmap font")
    return PreparedOverlay(
        scene=scene,
        scene_sha256=scene_sha256,
        font=font,
        point_count=metrics.point_count,
    )


def prepare_overlay_scene(
        overlay_scene: object, font_bytes: bytes,
) -> PreparedOverlay:
    scene, metrics = validate_overlay_scene_metrics(overlay_scene)
    font = parse_bitmap_font(font_bytes)
    return _prepare_validated_overlay_scene(
        scene, imagery.sha256_json(scene), font, metrics
    )


def frame_clip_segment_bound(prepared: PreparedOverlay) -> int:
    """Return the exact maximum clipped path segments for one frame."""
    trail_segments = sum(
        max(0, len(trail) - 1) for trail in prepared.scene["trails"]
    ) * 2
    ring_segments = sum(
        len(ring)
        for lot in prepared.scene["lots"]
        for ring in lot["rings_um"]
    )
    return trail_segments + ring_segments


def header_character_capacity(output_width: int, font: BitmapFont) -> int:
    """Return exact non-clipping capacity for the frozen header geometry."""
    width = _integer(output_width, "header output width", 1, MAX_OUTPUT_SIDE)
    style = OVERLAY_STYLE["header"]
    scale = style["font_scale"]
    available = width - style["x_px"]
    glyph_width = font.width * scale
    if available < glyph_width:
        return 0
    return 1 + (available - glyph_width) // (font.advance * scale)


def validate_frame_recipe_envelope(
        value: object, font: BitmapFont,
) -> dict[str, object]:
    """Validate all recipe structure before resolving or preparing its scene."""
    if not isinstance(font, BitmapFont):
        raise ValueError("frame recipe requires a parsed bitmap font")
    recipe = _closed(value, _RECIPE_KEYS, "frame recipe")
    if recipe.get("version") != 2 or recipe.get("kind") != FRAME_RECIPE_KIND:
        raise ValueError("frame recipe version or kind is unsupported")
    _integer(recipe.get("fid"), "frame fid", 0)
    rung = recipe.get("rung")
    matrix = recipe.get("matrix")
    if rung not in RUNG_MATRIX or matrix != RUNG_MATRIX[rung]:
        raise ValueError("frame rung and matrix are inconsistent")
    imagery.validate_viewport(recipe.get("viewport_um"), matrix)
    output = _closed(recipe.get("output"), _OUTPUT_KEYS, "frame output")
    width = _integer(output.get("width"), "frame width", 1, MAX_OUTPUT_SIDE)
    height = _integer(output.get("height"), "frame height", 1, MAX_OUTPUT_SIDE)
    if width * height > MAX_OUTPUT_PIXELS:
        raise ValueError("frame output pixel count exceeds its cap")
    _sha256(output.get("rgb_sha256"), "frame output RGB hash")
    if (recipe.get("resampler") != FRAME_RESAMPLER
            or recipe.get("style") != OVERLAY_STYLE_ID):
        raise ValueError("frame renderer or style is unsupported")
    _sha256(recipe.get("overlay_scene_ref"), "frame overlay scene reference")
    header = recipe.get("header")
    capacity = header_character_capacity(width, font)
    vertical_extent = (
        OVERLAY_STYLE["header"]["y_px"]
        + font.height * OVERLAY_STYLE["header"]["font_scale"]
    )
    if (not isinstance(header, str) or not header or len(header) > capacity
            or height < vertical_extent
            or any(ord(character) < 32 or ord(character) == 127
                   for character in header)):
        raise ValueError(
            f"frame header is empty, controlled, or exceeds width capacity "
            f"{capacity} or vertical extent {vertical_extent}"
        )
    for character in header:
        font.rows(character)

    cell_ids = recipe.get("cells")
    if (not isinstance(cell_ids, list) or not cell_ids
            or len(cell_ids) > imagery.MAX_CELLS_PER_FRAME
            or len(set(cell_ids)) != len(cell_ids)
            or any(not imagery.valid_sha256(cell_id) for cell_id in cell_ids)):
        raise ValueError("frame cell list is invalid")
    return recipe


def _validate_frame_recipe_structure_prepared(
        value: object, prepared: PreparedOverlay, *,
        envelope_validated: bool = False,
) -> dict[str, object]:
    recipe = (
        value if envelope_validated
        else validate_frame_recipe_envelope(value, prepared.font)
    )
    if not isinstance(recipe, dict):
        raise ValueError("prepared frame recipe must be a validated object")
    if recipe.get("overlay_scene_ref") != prepared.scene_sha256:
        raise ValueError("frame overlay scene reference mismatch")
    if not any(lot["fid"] == recipe["fid"] for lot in prepared.scene["lots"]):
        raise ValueError("frame subject fid is absent from its overlay scene")
    return recipe


def validate_frame_recipe_structure(
        value: object, prepared: PreparedOverlay,
) -> dict[str, object]:
    """Validate a complete recipe before any imagery catalog is decoded."""
    return _validate_frame_recipe_structure_prepared(value, prepared)


def _validate_frame_recipe_prepared(
        value: object, cells: Mapping[str, object], prepared: PreparedOverlay, *,
        envelope_validated: bool = False, cells_validated: bool = False,
) -> dict[str, object]:
    recipe = _validate_frame_recipe_structure_prepared(
        value, prepared, envelope_validated=envelope_validated
    )
    cell_ids = recipe["cells"]
    if not isinstance(cells, Mapping) or set(cells) != set(cell_ids):
        raise ValueError("frame cell catalog has missing or unreferenced extras")
    matrix = recipe["matrix"]
    expected_coordinates = imagery.expected_cell_coordinates(
        matrix, list(recipe["viewport_um"])
    )
    actual_coordinates = []
    for cell_id in cell_ids:
        cell = (
            cells[cell_id] if cells_validated
            else imagery.validate_imagery_cell_document(cells[cell_id])
        )
        if not isinstance(cell, dict):
            raise ValueError("prepared frame cell must be a validated object")
        if not cells_validated and imagery.imagery_cell_id(cell) != cell_id:
            raise ValueError("frame cell ID is not content addressed")
        grid = cell["grid"]
        if grid["matrix"] != matrix:
            raise ValueError("frame mixes cells from different matrices")
        actual_coordinates.append((grid["x"], grid["y"]))
    if tuple(actual_coordinates) != expected_coordinates:
        raise ValueError("frame cells do not provide exact complete-cell coverage")
    return recipe


def validate_frame_recipe(
        value: object, cells: Mapping[str, object], overlay_scene: object,
        font: BitmapFont | bytes | None = None,
) -> dict[str, object]:
    if font is None:
        raise ValueError("frame validation requires the exact bound bitmap font")
    font_bytes: bytes
    if isinstance(font, BitmapFont):
        scene, point_count = _validate_overlay_scene_with_count(overlay_scene)
        if font.sha256 != scene["font_ref"]:
            raise ValueError("overlay scene does not bind the supplied bitmap font")
        prepared = PreparedOverlay(
            scene, imagery.sha256_json(scene), font, point_count
        )
    else:
        font_bytes = font
        prepared = prepare_overlay_scene(overlay_scene, font_bytes)
    return _validate_frame_recipe_prepared(value, cells, prepared)


def frame_recipe_sha256(
        value: object, cells: Mapping[str, object], overlay_scene: object,
        font: BitmapFont | bytes,
) -> str:
    """Validate every bound glyph and geometry before content addressing."""
    recipe = validate_frame_recipe(value, cells, overlay_scene, font)
    return imagery.sha256_json(recipe)


@dataclass
class RenderWork:
    """Deterministic renderer counters and output-derived rejection budgets."""
    max_operations: int = 0
    max_scene_points: int = 0
    operations: int = 0
    counters: dict[str, int] = field(default_factory=dict)

    def configure(self, width: int, height: int) -> None:
        pixels = width * height
        derived_operations = max(
            MIN_RENDER_OPERATION_BUDGET,
            pixels * RENDER_OPERATIONS_PER_PIXEL,
        )
        derived_scene = max(
            MIN_SCENE_POINT_BUDGET,
            pixels * SCENE_POINTS_PER_PIXEL,
        )
        if self.max_operations == 0:
            self.max_operations = derived_operations
        elif self.max_operations > derived_operations:
            raise ValueError("render operation budget exceeds the normative cap")
        if self.max_scene_points == 0:
            self.max_scene_points = derived_scene
        elif self.max_scene_points > derived_scene:
            raise ValueError("render scene budget exceeds the normative cap")

    def charge(self, name: str, amount: int = 1) -> None:
        if type(amount) is not int or amount < 0:
            raise ValueError("render operation charge is invalid")
        self.counters[name] = self.counters.get(name, 0) + amount
        self.operations += amount
        if self.operations > self.max_operations:
            raise ValueError(
                f"render operation budget exceeded: {self.operations} > "
                f"{self.max_operations}"
            )

    def report(self) -> dict[str, object]:
        return {
            "operations": self.operations,
            "max_operations": self.max_operations,
            "max_scene_points": self.max_scene_points,
            "counters": dict(sorted(self.counters.items())),
        }


def _source_coordinate(
        world_numerator: int, world_denominator: int, pixel_um: int,
) -> tuple[int, int]:
    numerator = 2 * world_numerator - world_denominator * pixel_um
    denominator = 2 * world_denominator * pixel_um
    index, remainder = divmod(numerator, denominator)
    weight = (remainder * FIXED_ONE + denominator // 2) // denominator
    if weight == FIXED_ONE:
        return index + 1, 0
    return index, weight


def _cell_sample(
        decoded_by_coordinate: Mapping[tuple[int, int], bytes],
        pixel_x: int, pixel_y: int,
) -> tuple[int, int, int]:
    cell_x, local_x = divmod(pixel_x, imagery.CELL_WIDTH)
    cell_y, local_y = divmod(pixel_y, imagery.CELL_HEIGHT)
    try:
        rgb = decoded_by_coordinate[(cell_x, cell_y)]
    except KeyError as error:
        raise ValueError("bilinear clamp escaped complete cell coverage") from error
    row = imagery.CELL_HEIGHT - 1 - local_y
    offset = (row * imagery.CELL_WIDTH + local_x) * 3
    return rgb[offset], rgb[offset + 1], rgb[offset + 2]


def _reconstruct_prepared(
        frame: Mapping[str, object], cells: Mapping[str, object],
        rgb_cells: Mapping[str, bytes], identity_field: str,
        work: RenderWork, *, identities_validated: bool = False,
) -> bytes:
    if not isinstance(rgb_cells, Mapping) or set(rgb_cells) != set(frame["cells"]):
        raise ValueError("frame RGB cell map has missing or unreferenced extras")
    decoded_by_coordinate = {}
    coordinates = []
    for cell_id in frame["cells"]:
        rgb = rgb_cells[cell_id]
        if not isinstance(rgb, bytes) or len(rgb) != imagery.DECODED_RGB_LENGTH:
            raise ValueError("frame cell has wrong RGB length")
        cell = cells[cell_id]
        expected_hash = (
            cell["encoding"]["decoded_rgb_sha256"]
            if identity_field == "decoded"
            else cell["reference_rgb_ref"]
        )
        if (not identities_validated
                and imagery.sha256_bytes(rgb) != expected_hash):
            raise ValueError("frame cell RGB hash mismatch")
        grid = cell["grid"]
        coordinate = (grid["x"], grid["y"])
        decoded_by_coordinate[coordinate] = rgb
        coordinates.append(coordinate)

    minimum_x = min(x for x, _y in coordinates) * imagery.CELL_WIDTH
    maximum_x = (max(x for x, _y in coordinates) + 1) * imagery.CELL_WIDTH - 1
    minimum_y = min(y for _x, y in coordinates) * imagery.CELL_HEIGHT
    maximum_y = (max(y for _x, y in coordinates) + 1) * imagery.CELL_HEIGHT - 1
    x0, y0, x1, y1 = frame["viewport_um"]
    width = frame["output"]["width"]
    height = frame["output"]["height"]
    pixel_um = imagery.MATRIX_PIXEL_UM[frame["matrix"]]
    x_denominator = 2 * width
    y_denominator = 2 * height
    x_samples = []
    for column in range(width):
        world = x0 * x_denominator + (2 * column + 1) * (x1 - x0)
        source, weight = _source_coordinate(world, x_denominator, pixel_um)
        x_samples.append((
            min(max(source, minimum_x), maximum_x),
            min(max(source + 1, minimum_x), maximum_x),
            weight,
        ))
    y_samples = []
    for row in range(height):
        world = y1 * y_denominator - (2 * row + 1) * (y1 - y0)
        source, weight = _source_coordinate(world, y_denominator, pixel_um)
        y_samples.append((
            min(max(source, minimum_y), maximum_y),
            min(max(source + 1, minimum_y), maximum_y),
            weight,
        ))

    work.charge("resample_taps", width * height * 4)
    result = bytearray(width * height * 3)
    fixed_squared = FIXED_ONE * FIXED_ONE
    offset = 0
    for bottom_y, top_y, weight_y in y_samples:
        inverse_y = FIXED_ONE - weight_y
        for left_x, right_x, weight_x in x_samples:
            inverse_x = FIXED_ONE - weight_x
            bottom_left = _cell_sample(decoded_by_coordinate, left_x, bottom_y)
            bottom_right = _cell_sample(decoded_by_coordinate, right_x, bottom_y)
            top_left = _cell_sample(decoded_by_coordinate, left_x, top_y)
            top_right = _cell_sample(decoded_by_coordinate, right_x, top_y)
            for channel in range(3):
                total = (
                    bottom_left[channel] * inverse_x * inverse_y
                    + bottom_right[channel] * weight_x * inverse_y
                    + top_left[channel] * inverse_x * weight_y
                    + top_right[channel] * weight_x * weight_y
                )
                result[offset] = (total + fixed_squared // 2) // fixed_squared
                offset += 1
    return bytes(result)


def reconstruct_base_rgb(
        recipe: object, cells: Mapping[str, object],
        decoded_cells: Mapping[str, bytes], overlay_scene: object,
        font: BitmapFont | bytes | None = None, *,
        reference: bool = False, work: RenderWork | None = None,
) -> bytes:
    """Reconstruct with the normative border clamp; no apron is consulted."""
    if font is None:
        raise ValueError("base reconstruction requires the exact bound bitmap font")
    if isinstance(font, bytes):
        prepared = prepare_overlay_scene(overlay_scene, font)
    else:
        scene, point_count = _validate_overlay_scene_with_count(overlay_scene)
        prepared = PreparedOverlay(
            scene, imagery.sha256_json(scene), font, point_count
        )
    frame = _validate_frame_recipe_prepared(recipe, cells, prepared)
    counter = work or RenderWork()
    counter.configure(frame["output"]["width"], frame["output"]["height"])
    return _reconstruct_prepared(
        frame, cells, decoded_cells, "reference" if reference else "decoded",
        counter,
    )


def _blend(rgb: bytearray, width: int, height: int, x: int, y: int,
           color: Iterable[int]) -> None:
    if not (0 <= x < width and 0 <= y < height):
        return
    red, green, blue, alpha = color
    offset = (y * width + x) * 3
    inverse = 255 - alpha
    rgb[offset] = (red * alpha + rgb[offset] * inverse + 127) // 255
    rgb[offset + 1] = (green * alpha + rgb[offset + 1] * inverse + 127) // 255
    rgb[offset + 2] = (blue * alpha + rgb[offset + 2] * inverse + 127) // 255


def _round_fraction(value: Fraction) -> int:
    quotient, remainder = divmod(value.numerator, value.denominator)
    return quotient + (1 if remainder * 2 >= value.denominator else 0)


def _clip_segment(
        start: tuple[int, int], stop: tuple[int, int],
        minimum_x: int, minimum_y: int, maximum_x: int, maximum_y: int,
        work: RenderWork,
) -> tuple[tuple[int, int], tuple[int, int]] | None:
    """Liang-Barsky clip before Bresenham, using exact rational bounds."""
    x0, y0 = start
    x1, y1 = stop
    dx = x1 - x0
    dy = y1 - y0
    work.charge("fraction_operations", 2)
    lower = Fraction(0)
    upper = Fraction(1)
    for p, q in (
        (-dx, x0 - minimum_x),
        (dx, maximum_x - x0),
        (-dy, y0 - minimum_y),
        (dy, maximum_y - y0),
    ):
        work.charge("clip_boundary_tests")
        if p == 0:
            if q < 0:
                return None
            continue
        work.charge("fraction_operations", 4)
        ratio = Fraction(q, p)
        if p < 0:
            if ratio > upper:
                return None
            lower = max(lower, ratio)
        else:
            if ratio < lower:
                return None
            upper = min(upper, ratio)
    work.charge("fraction_operations", 6)
    clipped_start = (
        _round_fraction(Fraction(x0) + lower * dx),
        _round_fraction(Fraction(y0) + lower * dy),
    )
    clipped_stop = (
        _round_fraction(Fraction(x0) + upper * dx),
        _round_fraction(Fraction(y0) + upper * dy),
    )
    return clipped_start, clipped_stop


def _line_pixels(start: tuple[int, int], stop: tuple[int, int]):
    x0, y0 = start
    x1, y1 = stop
    delta_x = abs(x1 - x0)
    step_x = 1 if x0 < x1 else -1
    delta_y = -abs(y1 - y0)
    step_y = 1 if y0 < y1 else -1
    error = delta_x + delta_y
    while True:
        yield x0, y0
        if x0 == x1 and y0 == y1:
            break
        doubled = 2 * error
        if doubled >= delta_y:
            error += delta_y
            x0 += step_x
        if doubled <= delta_x:
            error += delta_x
            y0 += step_y


def _wide_segment_pixels(
        start: tuple[int, int], stop: tuple[int, int], width_px: int,
        width: int, height: int, painted: set[tuple[int, int]],
        work: RenderWork,
) -> None:
    radius = (width_px + 1) // 2
    clipped = _clip_segment(
        start,
        stop,
        -radius,
        -radius,
        width - 1 + radius,
        height - 1 + radius,
        work,
    )
    if clipped is None:
        return
    work.charge("segments_rasterized")
    for x, y in _line_pixels(*clipped):
        work.charge("line_steps")
        for delta_y in range(-radius, radius + 1):
            for delta_x in range(-radius, radius + 1):
                work.charge("brush_tests")
                if (4 * (delta_x * delta_x + delta_y * delta_y)
                        <= width_px * width_px):
                    painted_x = x + delta_x
                    painted_y = y + delta_y
                    if 0 <= painted_x < width and 0 <= painted_y < height:
                        painted.add((painted_x, painted_y))


def _circle_outline_pixels(
        center: tuple[int, int], radius: int, width_px: int,
        width: int, height: int, work: RenderWork,
) -> set[tuple[int, int]]:
    center_x, center_y = center
    outer = max(0, radius + (width_px + 1) // 2)
    inner = max(0, radius - width_px // 2)
    minimum_x = max(0, center_x - outer)
    maximum_x = min(width - 1, center_x + outer)
    minimum_y = max(0, center_y - outer)
    maximum_y = min(height - 1, center_y + outer)
    if minimum_x > maximum_x or minimum_y > maximum_y:
        return set()
    nearest_x = min(max(center_x, 0), width - 1)
    nearest_y = min(max(center_y, 0), height - 1)
    minimum_distance = (
        (nearest_x - center_x) ** 2 + (nearest_y - center_y) ** 2
    )
    maximum_distance = max(
        (corner_x - center_x) ** 2 + (corner_y - center_y) ** 2
        for corner_x in (0, width - 1)
        for corner_y in (0, height - 1)
    )
    if outer * outer < minimum_distance or inner * inner > maximum_distance:
        return set()
    result = set()
    for y in range(minimum_y, maximum_y + 1):
        for x in range(minimum_x, maximum_x + 1):
            work.charge("circle_tests")
            distance = (x - center_x) ** 2 + (y - center_y) ** 2
            if inner * inner <= distance <= outer * outer:
                result.add((x, y))
    return result


def _world_to_pixel(
        point: object, viewport: list[int], width: int, height: int,
) -> tuple[int, int]:
    if (isinstance(point, tuple) and len(point) == 2
            and all(type(coordinate) is int for coordinate in point)):
        x, y = point
    else:
        x, y = _point(point, "overlay coordinate")
    x0, y0, x1, y1 = viewport
    pixel_x = ((x - x0) * width + (x1 - x0) // 2) // (x1 - x0)
    pixel_y = ((y1 - y) * height + (y1 - y0) // 2) // (y1 - y0)
    return pixel_x, pixel_y


def _segment_intersects_world_halo(
        start: tuple[int, int], stop: tuple[int, int], viewport: list[int],
        width: int, height: int, halo_px: int,
) -> bool:
    x0, y0, x1, y1 = viewport
    halo_x = ((x1 - x0) * halo_px + width - 1) // width
    halo_y = ((y1 - y0) * halo_px + height - 1) // height
    minimum_x = min(start[0], stop[0])
    maximum_x = max(start[0], stop[0])
    minimum_y = min(start[1], stop[1])
    maximum_y = max(start[1], stop[1])
    return not (
        maximum_x < x0 - halo_x or minimum_x > x1 + halo_x
        or maximum_y < y0 - halo_y or minimum_y > y1 + halo_y
    )


def _paint_pixels(
        rgb: bytearray, width: int, height: int,
        pixels: Iterable[tuple[int, int]], color: Iterable[int],
        work: RenderWork,
) -> None:
    ordered = sorted(pixels, key=lambda item: (item[1], item[0]))
    work.charge("blend_operations", len(ordered))
    for x, y in ordered:
        _blend(rgb, width, height, x, y, color)


def _paint_path(
        rgb: bytearray, width: int, height: int, path: Iterable[object],
        viewport: list[int], color: Iterable[int], width_px: int,
        work: RenderWork, close: bool = False,
) -> None:
    raw_points = tuple(_point(point, "overlay path coordinate") for point in path)
    pairs = list(zip(raw_points, raw_points[1:]))
    if close:
        pairs.append((raw_points[-1], raw_points[0]))
    painted: set[tuple[int, int]] = set()
    halo = (width_px + 1) // 2
    for start, stop in pairs:
        work.charge("segment_tests")
        if not _segment_intersects_world_halo(
                start, stop, viewport, width, height, halo):
            continue
        _wide_segment_pixels(
            _world_to_pixel(start, viewport, width, height),
            _world_to_pixel(stop, viewport, width, height),
            width_px,
            width,
            height,
            painted,
            work,
        )
    _paint_pixels(rgb, width, height, painted, color, work)


def _paint_lot(
        rgb: bytearray, width: int, height: int, lot: Mapping[str, object],
        viewport: list[int], style: Mapping[str, object], subject: bool,
        work: RenderWork,
) -> None:
    color = style["rgba"]
    width_px = style["width_px"]
    if lot["rings_um"]:
        for ring in lot["rings_um"]:
            _paint_path(
                rgb, width, height, ring, viewport, color, width_px, work,
                close=True,
            )
        return
    center = _world_to_pixel(lot["point_um"], viewport, width, height)
    if subject:
        x0, y0, x1, y1 = viewport
        radius = max(1, (
            style["point_radius_um"] * min(width, height)
            + max(x1 - x0, y1 - y0) // 2
        ) // max(x1 - x0, y1 - y0))
    else:
        radius = style["point_radius_px"]
    _paint_pixels(
        rgb,
        width,
        height,
        _circle_outline_pixels(center, radius, width_px, width, height, work),
        color,
        work,
    )


def _paint_header(
        rgb: bytearray, width: int, height: int, text: str, font: BitmapFont,
        work: RenderWork,
) -> None:
    style = OVERLAY_STYLE["header"]
    header_height = min(height, style["height_px"])
    background = style["background_rgba"]
    work.charge("header_background_pixels", width * header_height)
    work.charge("blend_operations", width * header_height)
    for y in range(header_height):
        for x in range(width):
            _blend(rgb, width, height, x, y, background)
    scale = style["font_scale"]
    cursor_x = style["x_px"]
    origin_y = style["y_px"]
    if origin_y + font.height * scale > height:
        raise ValueError("validated header would be vertically clipped")
    color = style["text_rgba"]
    for character in text:
        rows = font.rows(character)
        for row_index, bits in enumerate(rows):
            for column in range(font.width):
                if bits & (1 << (font.width - 1 - column)):
                    for offset_y in range(scale):
                        for offset_x in range(scale):
                            work.charge("header_glyph_pixels")
                            work.charge("blend_operations")
                            _blend(
                                rgb,
                                width,
                                height,
                                cursor_x + column * scale + offset_x,
                                origin_y + row_index * scale + offset_y,
                                color,
                            )
        cursor_x += font.advance * scale
    if cursor_x - font.advance * scale + font.width * scale > width:
        raise AssertionError("validated header was silently clipped")


def _apply_overlay_prepared(
        base_rgb: bytes, frame: Mapping[str, object],
        prepared: PreparedOverlay, work: RenderWork,
) -> bytes:
    width = frame["output"]["width"]
    height = frame["output"]["height"]
    if len(base_rgb) != width * height * 3:
        raise ValueError("base frame RGB length mismatch")
    if prepared.point_count > work.max_scene_points:
        raise ValueError(
            f"overlay scene exceeds frame point budget: {prepared.point_count} > "
            f"{work.max_scene_points}"
        )
    work.charge("scene_points_examined", prepared.point_count)
    viewport = frame["viewport_um"]
    result = bytearray(base_rgb)
    for trail in prepared.scene["trails"]:
        _paint_path(
            result,
            width,
            height,
            trail,
            viewport,
            OVERLAY_STYLE["trail_casing"]["rgba"],
            OVERLAY_STYLE["trail_casing"]["width_px"],
            work,
        )
    for trail in prepared.scene["trails"]:
        _paint_path(
            result,
            width,
            height,
            trail,
            viewport,
            OVERLAY_STYLE["trail"]["rgba"],
            OVERLAY_STYLE["trail"]["width_px"],
            work,
        )
    subject = None
    for lot in prepared.scene["lots"]:
        if lot["fid"] == frame["fid"]:
            subject = lot
        else:
            _paint_lot(
                result,
                width,
                height,
                lot,
                viewport,
                OVERLAY_STYLE["other_lot"],
                False,
                work,
            )
    if subject is None:
        raise ValueError("overlay scene has no subject lot")
    _paint_lot(
        result,
        width,
        height,
        subject,
        viewport,
        OVERLAY_STYLE["subject_lot"],
        True,
        work,
    )
    _paint_header(result, width, height, frame["header"], prepared.font, work)
    return bytes(result)


def _render_validated_frame_pair(
        frame: Mapping[str, object], cells: Mapping[str, object],
        candidate_rgbs: Mapping[str, bytes], reference_rgbs: Mapping[str, bytes],
        prepared: PreparedOverlay, *, work: RenderWork | None = None,
        verify_output: bool = True, identities_validated: bool = False,
) -> tuple[bytes, bytes]:
    """Render one already validated immutable frame/cell/scene product."""
    counter = work or RenderWork()
    width = frame["output"]["width"]
    height = frame["output"]["height"]
    counter.configure(width, height)
    reference_base = _reconstruct_prepared(
        frame,
        cells,
        reference_rgbs,
        "reference",
        counter,
        identities_validated=identities_validated,
    )
    candidate_base = _reconstruct_prepared(
        frame,
        cells,
        candidate_rgbs,
        "decoded",
        counter,
        identities_validated=identities_validated,
    )
    reference_frame = _apply_overlay_prepared(
        reference_base, frame, prepared, counter
    )
    candidate_frame = _apply_overlay_prepared(
        candidate_base, frame, prepared, counter
    )
    if verify_output:
        actual = imagery.sha256_bytes(candidate_frame)
        if actual != frame["output"]["rgb_sha256"]:
            raise ValueError("reconstructed frame RGB SHA-256 mismatch")
    return reference_frame, candidate_frame


def render_prepared_frame_pair(
        recipe: object, cells: Mapping[str, object],
        candidate_rgbs: Mapping[str, bytes], reference_rgbs: Mapping[str, bytes],
        prepared: PreparedOverlay, *, work: RenderWork | None = None,
        verify_output: bool = True,
) -> tuple[bytes, bytes]:
    """Defensively validate and render clean-reference/candidate frames."""
    frame = _validate_frame_recipe_prepared(recipe, cells, prepared)
    return _render_validated_frame_pair(
        frame,
        cells,
        candidate_rgbs,
        reference_rgbs,
        prepared,
        work=work,
        verify_output=verify_output,
    )


def render_frame_rgb(
        recipe: object, cells: Mapping[str, object],
        payloads: Mapping[str, bytes],
        quality_receipts: Mapping[str, object], overlay_scene: object,
        font_bytes: bytes, *, reference_rgbs: Mapping[str, bytes] | None = None,
        critical_masks: Mapping[str, bytes] | None = None,
        critical_mask_recipes: Mapping[str, object] | None = None,
        verify_output: bool = True, work: RenderWork | None = None,
) -> bytes:
    """Replay cell authority, render one frame, and verify its exact RGB hash."""
    prepared = prepare_overlay_scene(overlay_scene, font_bytes)
    frame = _validate_frame_recipe_prepared(recipe, cells, prepared)
    if reference_rgbs is None or critical_masks is None or critical_mask_recipes is None:
        raise ValueError("frame replay requires rooted reference and mask objects")
    required_payloads = {
        cells[cell_id]["encoding"]["payload_ref"] for cell_id in frame["cells"]
    }
    required_receipts = {
        cells[cell_id]["quality_receipt_ref"] for cell_id in frame["cells"]
        if cells[cell_id]["quality_receipt_ref"] is not None
    }
    required_references = {
        cells[cell_id]["reference_rgb_ref"] for cell_id in frame["cells"]
    }
    required_masks = {
        cells[cell_id]["critical_mask_ref"] for cell_id in frame["cells"]
    }
    required_mask_recipes = {
        cells[cell_id]["critical_mask_recipe_ref"] for cell_id in frame["cells"]
    }
    maps = (
        (payloads, required_payloads, "payload"),
        (quality_receipts, required_receipts, "quality"),
        (reference_rgbs, required_references, "reference RGB"),
        (critical_masks, required_masks, "critical mask"),
        (critical_mask_recipes, required_mask_recipes, "critical-mask recipe"),
    )
    for supplied, required, label in maps:
        if not isinstance(supplied, Mapping) or set(supplied) != required:
            raise ValueError(f"frame {label} map has missing or unreferenced extras")

    decoded = {}
    references_by_cell = {}
    for cell_id in frame["cells"]:
        cell = cells[cell_id]
        payload_ref = cell["encoding"]["payload_ref"]
        receipt_ref = cell["quality_receipt_ref"]
        reference_ref = cell["reference_rgb_ref"]
        mask_ref = cell["critical_mask_ref"]
        mask_recipe_ref = cell["critical_mask_recipe_ref"]
        receipt = None if receipt_ref is None else quality_receipts[receipt_ref]
        reference = reference_rgbs[reference_ref]
        decoded[cell_id] = imagery.validate_imagery_cell(
            cell,
            payloads[payload_ref],
            receipt,
            reference_rgb=reference,
            critical_mask=critical_masks[mask_ref],
            critical_mask_recipe=critical_mask_recipes[mask_recipe_ref],
        )
        references_by_cell[cell_id] = reference
    _reference, candidate = render_prepared_frame_pair(
        frame,
        cells,
        decoded,
        references_by_cell,
        prepared,
        work=work,
        verify_output=verify_output,
    )
    return candidate


_FRAME_FIDELITY_KEYS = {
    "version", "kind", "policy_sha256", "baseline_png_decoder", "frame_key",
    "fid", "rung", "recipe_ref", "source_png_sha256", "width", "height",
    "max_cell_mode", "reference_rgb_sha256", "candidate_rgb_sha256",
    "metrics", "passed", "receipt_sha256",
}


def frame_fidelity_metrics_pass(
        metrics: Mapping[str, object], max_cell_mode: int,
        width: int, height: int,
) -> bool:
    mode = _integer(max_cell_mode, "frame fidelity max cell mode")
    if mode not in imagery.QRGB_MODES:
        raise ValueError("frame fidelity max cell mode is unsupported")
    return imagery.quality_metrics_pass(
        metrics, mode, width * height * 3
    )


def _build_frame_fidelity_receipt_from_metrics(
        frame_key: str, recipe_ref: str, source_png_sha256: str,
        reference_rgb: bytes, candidate_rgb: bytes, width: int, height: int,
        max_cell_mode: int, metrics: Mapping[str, object],
) -> dict[str, object]:
    fid, rung = parse_frame_key(frame_key)
    _sha256(recipe_ref, "frame fidelity recipe reference")
    _sha256(source_png_sha256, "frame fidelity source PNG")
    pixels = _integer(width, "frame fidelity width", 1, MAX_OUTPUT_SIDE) * _integer(
        height, "frame fidelity height", 1, MAX_OUTPUT_SIDE
    )
    mode = _integer(max_cell_mode, "frame fidelity max cell mode")
    if mode not in imagery.QRGB_MODES:
        raise ValueError("frame fidelity max cell mode is unsupported")
    if (not isinstance(reference_rgb, bytes)
            or not isinstance(candidate_rgb, bytes)
            or len(reference_rgb) != pixels * 3
            or len(candidate_rgb) != pixels * 3):
        raise ValueError("frame fidelity RGB raster length mismatch")
    validated_metrics = dict(imagery.validate_quality_metrics(
        metrics, width, height, "frame fidelity metrics"
    ))
    body: dict[str, object] = {
        "version": 1,
        "kind": FRAME_FIDELITY_RECEIPT_KIND,
        "policy_sha256": imagery.QUALITY_POLICY_SHA256,
        "baseline_png_decoder": BASELINE_PNG_DECODER,
        "frame_key": frame_key,
        "fid": fid,
        "rung": rung,
        "recipe_ref": recipe_ref,
        "source_png_sha256": source_png_sha256,
        "width": width,
        "height": height,
        "max_cell_mode": mode,
        "reference_rgb_sha256": imagery.sha256_bytes(reference_rgb),
        "candidate_rgb_sha256": imagery.sha256_bytes(candidate_rgb),
        "metrics": validated_metrics,
        "passed": frame_fidelity_metrics_pass(
            validated_metrics, mode, width, height
        ),
    }
    body["receipt_sha256"] = imagery.sha256_json(body)
    return body


def build_frame_fidelity_receipt(
        frame_key: str, recipe_ref: str, source_png_sha256: str,
        reference_rgb: bytes, candidate_rgb: bytes, width: int, height: int,
        max_cell_mode: int,
) -> dict[str, object]:
    metrics = imagery.measure_raster_quality(
        reference_rgb, candidate_rgb, width, height
    )
    return _build_frame_fidelity_receipt_from_metrics(
        frame_key,
        recipe_ref,
        source_png_sha256,
        reference_rgb,
        candidate_rgb,
        width,
        height,
        max_cell_mode,
        metrics,
    )


def validate_frame_fidelity_receipt(
        value: object, *, frame_key: str | None = None,
        recipe_ref: str | None = None, source_png_sha256: str | None = None,
        reference_rgb: bytes | None = None, candidate_rgb: bytes | None = None,
        width: int | None = None, height: int | None = None,
        max_cell_mode: int | None = None, require_passed: bool = True,
        _recomputed_metrics: Mapping[str, object] | None = None,
) -> dict[str, object]:
    receipt, _raw = imagery._canonical_document(value, "frame fidelity receipt")
    _closed(receipt, _FRAME_FIDELITY_KEYS, "frame fidelity receipt")
    if (receipt.get("version") != 1
            or receipt.get("kind") != FRAME_FIDELITY_RECEIPT_KIND
            or receipt.get("policy_sha256") != imagery.QUALITY_POLICY_SHA256
            or receipt.get("baseline_png_decoder") != BASELINE_PNG_DECODER):
        raise ValueError("frame fidelity policy or PNG decoder mismatch")
    parsed_fid, parsed_rung = parse_frame_key(receipt.get("frame_key"))
    if (receipt.get("fid") != parsed_fid
            or receipt.get("rung") != parsed_rung):
        raise ValueError("frame fidelity FID/rung identity mismatch")
    for field_name in (
        "recipe_ref", "source_png_sha256", "reference_rgb_sha256",
        "candidate_rgb_sha256", "receipt_sha256",
    ):
        _sha256(receipt.get(field_name), f"frame fidelity {field_name}")
    _integer(receipt.get("width"), "frame fidelity width", 1, MAX_OUTPUT_SIDE)
    _integer(receipt.get("height"), "frame fidelity height", 1, MAX_OUTPUT_SIDE)
    receipt_mode = _integer(
        receipt.get("max_cell_mode"), "frame fidelity max cell mode"
    )
    if receipt_mode not in imagery.QRGB_MODES:
        raise ValueError("frame fidelity max cell mode is unsupported")
    metrics = _closed(
        receipt.get("metrics"), imagery.QUALITY_METRIC_KEYS,
        "frame fidelity metrics",
    )
    imagery.validate_quality_metrics(
        metrics, receipt["width"], receipt["height"], "frame fidelity metrics"
    )
    passed = frame_fidelity_metrics_pass(
        metrics, receipt_mode, receipt["width"], receipt["height"]
    )
    if type(receipt.get("passed")) is not bool or receipt["passed"] != passed:
        raise ValueError("frame fidelity pass result is inconsistent")
    body = dict(receipt)
    claimed = body.pop("receipt_sha256")
    if claimed != imagery.sha256_json(body):
        raise ValueError("frame fidelity receipt self-hash mismatch")
    supplied = (
        frame_key,
        recipe_ref,
        source_png_sha256,
        reference_rgb,
        candidate_rgb,
        width,
        height,
        max_cell_mode,
    )
    if any(item is not None for item in supplied):
        if any(item is None for item in supplied):
            raise ValueError("frame fidelity recomputation inputs are partial")
        if _recomputed_metrics is None:
            expected = build_frame_fidelity_receipt(
                frame_key,
                recipe_ref,
                source_png_sha256,
                reference_rgb,
                candidate_rgb,
                width,
                height,
                max_cell_mode,
            )
        else:
            expected = _build_frame_fidelity_receipt_from_metrics(
                frame_key,
                recipe_ref,
                source_png_sha256,
                reference_rgb,
                candidate_rgb,
                width,
                height,
                max_cell_mode,
                _recomputed_metrics,
            )
        if receipt != expected:
            raise ValueError("frame fidelity receipt differs from recomputation")
    if require_passed and not receipt["passed"]:
        raise ValueError("frame fidelity receipt did not pass the hashed policy")
    return receipt


def real_resolution_budget_plan(
        frame_count: int = 150, width: int = 1024, height: int = 1024,
) -> dict[str, int]:
    """Return deterministic limits for a real-resolution validation run."""
    frames = _integer(frame_count, "budget frame count", 1)
    side_width = _integer(width, "budget width", 1, MAX_OUTPUT_SIDE)
    side_height = _integer(height, "budget height", 1, MAX_OUTPUT_SIDE)
    pixels = side_width * side_height
    if pixels > MAX_OUTPUT_PIXELS:
        raise ValueError("budget frame exceeds output pixel cap")
    return {
        "frame_count": frames,
        "pixels_per_frame": pixels,
        "total_output_pixels": frames * pixels,
        "max_operations_per_frame": max(
            MIN_RENDER_OPERATION_BUDGET,
            pixels * RENDER_OPERATIONS_PER_PIXEL,
        ),
        "max_operations_all_frames": frames * max(
            MIN_RENDER_OPERATION_BUDGET,
            pixels * RENDER_OPERATIONS_PER_PIXEL,
        ),
        "max_scene_points_per_frame": max(
            MIN_SCENE_POINT_BUDGET,
            pixels * SCENE_POINTS_PER_PIXEL,
        ),
        "candidate_and_reference_rgb_bytes_per_frame": pixels * 3 * 2,
    }
