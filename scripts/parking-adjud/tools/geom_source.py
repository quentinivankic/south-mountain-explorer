#!/usr/bin/env python3
"""Strict, stable source capture for shipped trail geometry JSON."""
from __future__ import annotations

import math
import os
import stat
from dataclasses import dataclass
from pathlib import Path

import dossier_output
import trusted_filesystem as trusted_fs


@dataclass(frozen=True)
class GeomDocument:
    path: Path
    bbox: tuple[float, float, float, float]
    document: dict


def _directory_identity(value: os.stat_result) -> tuple[int, int, int]:
    return value.st_dev, value.st_ino, value.st_mode


def _file_identity(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _regular_json_inventory(root: Path) -> tuple[
        tuple[int, int, int], tuple[tuple[str, tuple[int, ...]], ...]]:
    directory_fd = trusted_fs.open_directory_fd(root)
    try:
        directory_identity = _directory_identity(os.fstat(directory_fd))
        entries = []
        for name in sorted(os.listdir(directory_fd)):
            if not name.endswith(".json"):
                continue
            value = os.stat(
                name, dir_fd=directory_fd, follow_symlinks=False
            )
            if not stat.S_ISREG(value.st_mode):
                raise ValueError(
                    f"geometry path is not a regular no-follow file: {name}"
                )
            entries.append((name, _file_identity(value)))
        return directory_identity, tuple(entries)
    finally:
        os.close(directory_fd)


def _number(value: object, context: str) -> float:
    if (type(value) not in (int, float)
            or not math.isfinite(value)):
        raise ValueError(f"{context} must be a finite number")
    return float(value)


def _bbox(value: object, context: str, *, geographic: bool) -> tuple[
        float, float, float, float]:
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError(f"{context} must be a four-number array")
    west = _number(value[0], f"{context}[0]")
    south = _number(value[1], f"{context}[1]")
    east = _number(value[2], f"{context}[2]")
    north = _number(value[3], f"{context}[3]")
    if west > east or south > north:
        raise ValueError(f"{context} bounds are reversed")
    if geographic and not (
            -180.0 <= west <= 180.0
            and -180.0 <= east <= 180.0
            and -90.0 <= south <= 90.0
            and -90.0 <= north <= 90.0):
        raise ValueError(f"{context} is outside coordinate range")
    return west, south, east, north


def load_geom_inventory(geom_dir: str | Path) -> tuple[GeomDocument, ...]:
    """Strictly capture every sorted regular ``*.json`` geometry source."""
    root = Path(geom_dir).absolute()
    before = _regular_json_inventory(root)
    documents = []
    for name, _identity in before[1]:
        path = root / name
        try:
            document, _raw = dossier_output.read_strict_regular_json(
                path, f"geometry file {name}"
            )
        except OSError as error:
            raise ValueError(
                f"geometry file is not readable as one stable regular file: {name}"
            ) from error
        if not isinstance(document, dict):
            raise ValueError(f"geometry file {name} must contain an object")
        bbox = _bbox(document.get("bbox"), f"geometry file {name} bbox",
                     geographic=True)
        documents.append(GeomDocument(path, bbox, document))
    if _regular_json_inventory(root) != before:
        raise ValueError("geometry regular-file inventory changed during capture")
    return tuple(documents)


def document_for_slug(
        inventory: tuple[GeomDocument, ...], slug: str) -> GeomDocument:
    expected = f"{dossier_output.canonical_area(slug)}.json"
    matches = [entry for entry in inventory if entry.path.name == expected]
    if len(matches) != 1:
        raise FileNotFoundError(f"geometry source is missing: {expected}")
    return matches[0]


def _validate_relevant_trails(entry: GeomDocument) -> None:
    context = f"geometry file {entry.path.name}"
    trails = entry.document.get("trails")
    if not isinstance(trails, list):
        raise ValueError(f"{context} trails must be an array")
    for trail_index, trail in enumerate(trails):
        trail_context = f"{context} trail {trail_index}"
        if not isinstance(trail, dict):
            raise ValueError(f"{trail_context} must be an object")
        name = trail.get("name")
        if name is not None and not isinstance(name, str):
            raise ValueError(f"{trail_context} name must be a string or null")
        segments = trail.get("segments")
        if not isinstance(segments, list):
            raise ValueError(f"{trail_context} segments must be an array")
        for segment_index, segment in enumerate(segments):
            segment_context = f"{trail_context} segment {segment_index}"
            if not isinstance(segment, list):
                raise ValueError(f"{segment_context} must be an array")
            for coordinate_index, coordinate in enumerate(segment):
                coordinate_context = (
                    f"{segment_context} coordinate {coordinate_index}"
                )
                if not isinstance(coordinate, list) or len(coordinate) != 2:
                    raise ValueError(
                        f"{coordinate_context} must be a latitude/longitude pair"
                    )
                latitude = _number(
                    coordinate[0], f"{coordinate_context} latitude"
                )
                longitude = _number(
                    coordinate[1], f"{coordinate_context} longitude"
                )
                if not (-90.0 <= latitude <= 90.0):
                    raise ValueError(
                        f"{coordinate_context} latitude is outside coordinate range"
                    )
                if not (-180.0 <= longitude <= 180.0):
                    raise ValueError(
                        f"{coordinate_context} longitude is outside coordinate range"
                    )


def relevant_trail_documents(
        inventory: tuple[GeomDocument, ...],
        region_bbox: tuple[float, float, float, float] | list[float],
        ) -> tuple[GeomDocument, ...]:
    """Return intersecting documents after validating only their trail payloads."""
    region = _bbox(
        list(region_bbox), "geometry relevance bbox", geographic=False
    )
    relevant = []
    for entry in inventory:
        bbox = entry.bbox
        if (bbox[2] < region[0] or bbox[0] > region[2]
                or bbox[3] < region[1] or bbox[1] > region[3]):
            continue
        _validate_relevant_trails(entry)
        relevant.append(entry)
    return tuple(relevant)
