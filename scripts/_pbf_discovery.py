"""Deterministic, network-free candidate discovery from one local OSM PBF.

The module is deliberately independent from the live Overpass transport.  It
streams pyosmium areas, derives the Denmark scope and candidate centers from
assembled polygon bounds, and binds the result to the exact input bytes.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any

ATTRIBUTION = "© OpenStreetMap contributors"
PRODUCER = "scripts/seed-areas.py"
RECEIPT_KIND = "local-pbf-discovery-provenance"
PARSER = "pyosmium-area-v1"
_SHA256 = re.compile(r"[0-9a-f]{64}")


class SourcePbfError(RuntimeError):
    """The local PBF or its provenance failed a fail-closed check."""


def valid_sha256(value: str | None) -> bool:
    return bool(isinstance(value, str) and _SHA256.fullmatch(value))


def _hash_file(path: Path) -> tuple[dict[str, Any], tuple[int, int, int, int]]:
    try:
        before = path.stat()
        if not stat.S_ISREG(before.st_mode):
            raise SourcePbfError("source PBF must be a regular file")
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as source:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
        after = path.stat()
    except SourcePbfError:
        raise
    except OSError as error:
        raise SourcePbfError(f"source PBF is unreadable: {error}") from error
    before_fingerprint = (
        before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    after_fingerprint = (
        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if before_fingerprint != after_fingerprint or size != after.st_size:
        raise SourcePbfError("source PBF changed while it was being hashed")
    return ({"bytes": size, "sha256": digest.hexdigest()},
            after_fingerprint)


def _resolved_source(path: Path) -> Path:
    if path.is_symlink():
        raise SourcePbfError("source PBF must not be a symbolic link")
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise SourcePbfError(f"source PBF is unavailable: {error}") from error
    if not resolved.name.endswith(".osm.pbf"):
        raise SourcePbfError("source PBF path must end in .osm.pbf")
    return resolved


def _tags(values) -> dict[str, str]:
    return dict(sorted((str(tag.k), str(tag.v)) for tag in values))


def _candidate_tags(tags: dict[str, str]) -> bool:
    return (
        bool(str(tags.get("name") or "").strip())
        and (
            tags.get("boundary") in {"protected_area", "national_park"}
            or tags.get("leisure") == "nature_reserve"
        )
    )


def _scope_tags(tags: dict[str, str], region_code: str) -> bool:
    if region_code != "DK":
        return False
    return (
        tags.get("ISO3166-1") == "DK"
        and tags.get("boundary") == "administrative"
        and tags.get("admin_level") == "2"
    )


def _checked_bounds(geometry, label: str) -> tuple[float, float, float, float]:
    try:
        values = tuple(float(value) for value in geometry.bounds)
    except (AttributeError, TypeError, ValueError) as error:
        raise SourcePbfError(f"{label} has invalid geometry bounds") from error
    if (len(values) != 4 or not all(math.isfinite(value) for value in values)
            or values[0] > values[2] or values[1] > values[3]):
        raise SourcePbfError(f"{label} has invalid geometry bounds")
    return values


def _load_dependencies():
    try:
        osmium = importlib.import_module("osmium")
        shapely_wkb = importlib.import_module("shapely.wkb")
    except ImportError as error:
        raise SourcePbfError(
            "source-PBF discovery requires pyosmium and shapely") from error
    return osmium, shapely_wkb


def _read_pbf_areas(path: Path, region_code: str
                    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read candidate areas and one authoritative administrative scope."""
    osmium, shapely_wkb = _load_dependencies()
    factory = osmium.geom.WKBFactory()
    elements: list[dict[str, Any]] = []
    scopes: list[dict[str, Any]] = []
    identities: set[tuple[str, int]] = set()

    class AreaHandler(osmium.SimpleHandler):
        def area(self, area) -> None:
            tags = _tags(area.tags)
            candidate = _candidate_tags(tags)
            scope = _scope_tags(tags, region_code)
            if not candidate and not scope:
                return
            try:
                osm_id = int(area.orig_id())
                from_way = bool(area.from_way())
            except (AttributeError, TypeError, ValueError) as error:
                raise SourcePbfError("source PBF area identity is invalid") from error
            if osm_id <= 0:
                raise SourcePbfError("source PBF area identity is invalid")
            osm_type = "way" if from_way else "relation"
            identity = (osm_type, osm_id)
            if identity in identities:
                raise SourcePbfError(
                    f"source PBF repeats area identity {osm_type}/{osm_id}")
            identities.add(identity)
            try:
                geometry = shapely_wkb.loads(
                    factory.create_multipolygon(area), hex=True)
            except Exception as error:  # noqa: BLE001 - untrusted PBF boundary
                raise SourcePbfError(
                    f"source PBF area {osm_type}/{osm_id} is not assemblable") from error
            if getattr(geometry, "is_empty", True):
                raise SourcePbfError(
                    f"source PBF area {osm_type}/{osm_id} is empty")
            min_lon, min_lat, max_lon, max_lat = _checked_bounds(
                geometry, f"source PBF area {osm_type}/{osm_id}")
            if candidate:
                elements.append({
                    "type": osm_type,
                    "id": osm_id,
                    "tags": tags,
                    "center": {
                        "lat": (min_lat + max_lat) / 2.0,
                        "lon": (min_lon + max_lon) / 2.0,
                    },
                })
            if scope:
                if from_way:
                    raise SourcePbfError(
                        "Denmark administrative scope must be relation-backed")
                scopes.append({
                    "region_code": region_code,
                    "admin_boundary_osm_type": osm_type,
                    "admin_boundary_osm_id": osm_id,
                    "bbox": [min_lon, min_lat, max_lon, max_lat],
                })

    try:
        AreaHandler().apply_file(str(path), locations=True)
    except SourcePbfError:
        raise
    except Exception as error:  # noqa: BLE001 - malformed/unreadable PBF
        raise SourcePbfError("source PBF could not be parsed safely") from error

    if not scopes:
        raise SourcePbfError(
            f"source PBF does not contain the {region_code} administrative scope")
    scopes.sort(key=lambda value: (
        -((value["bbox"][2] - value["bbox"][0])
          * (value["bbox"][3] - value["bbox"][1])),
        value["admin_boundary_osm_id"],
    ))
    selected_scope = scopes[0]
    if len(scopes) > 1:
        first_area = ((selected_scope["bbox"][2] - selected_scope["bbox"][0])
                      * (selected_scope["bbox"][3] - selected_scope["bbox"][1]))
        second_area = ((scopes[1]["bbox"][2] - scopes[1]["bbox"][0])
                       * (scopes[1]["bbox"][3] - scopes[1]["bbox"][1]))
        if first_area == second_area:
            raise SourcePbfError(
                f"source PBF contains ambiguous {region_code} administrative scopes")
    elements.sort(key=lambda value: (
        value["type"], value["id"], value["tags"].get("name", "")))
    return elements, selected_scope


def inspect_source_pbf(path: Path, expected_sha256: str, region_code: str
                       ) -> tuple[list[dict[str, Any]], dict[str, Any],
                                  dict[str, Any]]:
    """Verify, parse, then re-verify the exact source bytes."""
    if not valid_sha256(expected_sha256):
        raise SourcePbfError(
            "--source-pbf-sha256 must be 64 lowercase hexadecimal characters")
    resolved = _resolved_source(path)
    identity, fingerprint = _hash_file(resolved)
    if identity["bytes"] <= 0:
        raise SourcePbfError("source PBF must not be empty")
    if identity["sha256"] != expected_sha256:
        raise SourcePbfError("source PBF SHA-256 does not match the expected hash")
    elements, scope = _read_pbf_areas(resolved, region_code)
    closing_identity, closing_fingerprint = _hash_file(resolved)
    if closing_identity != identity or closing_fingerprint != fingerprint:
        raise SourcePbfError("source PBF changed while discovery was running")
    return elements, scope, {
        "resolved_path": str(resolved),
        "bytes": identity["bytes"],
        "sha256": identity["sha256"],
        "expected_sha256": expected_sha256,
    }


def byte_identity(path: Path) -> dict[str, Any]:
    identity, _ = _hash_file(path)
    return identity


def ensure_new_output(path: Path, label: str) -> None:
    if path.is_symlink() or path.exists():
        raise SourcePbfError(f"{label} target already exists or is unsafe")
    if not path.parent.is_dir() or path.parent.is_symlink():
        raise SourcePbfError(f"{label} parent is missing or unsafe")


def write_new_json(path: Path, document: dict[str, Any], label: str) -> None:
    """Create a durable JSON file atomically without replacing evidence."""
    ensure_new_output(path, label)
    payload = (
        json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    try:
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    except OSError as error:
        raise SourcePbfError(f"could not create {label}") from error
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as error:
            raise SourcePbfError(f"{label} target already exists") from error
        except OSError as error:
            raise SourcePbfError(f"could not publish {label}") from error
    except OSError as error:
        raise SourcePbfError(f"could not write {label}") from error
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def provenance_document(*, source: dict[str, Any], scope: dict[str, Any],
                        report_path: Path, candidate_occurrence_count: int,
                        candidate_count: int) -> dict[str, Any]:
    report_identity = byte_identity(report_path)
    return {
        "schema_version": 1,
        "receipt_kind": RECEIPT_KIND,
        "producer": PRODUCER,
        "parser": PARSER,
        "attribution": ATTRIBUTION,
        "requested_region_codes": ["DK"],
        "canonical_source_path_label": "data/raw/denmark.osm.pbf",
        "source_pbf": source,
        "scope": scope,
        "candidate_occurrence_count": candidate_occurrence_count,
        "candidate_count": candidate_count,
        "discovery_report": report_identity,
    }
