#!/usr/bin/env python3
"""Build a deterministic, read-only US parking adjudication census.

The census is intentionally separate from every publisher. It captures the
shipped US trail endpoint inventory, streams a parking PBF (through local
``osmium``) or GeoJSON-sequence fixture, resolves only evidence-backed OSM
identity edges, replays raw/current/effective service projections, and unions
that result with the current live parking pool. Output is written only to a
new explicit directory.
"""
from __future__ import annotations

import argparse
import bisect
import collections
import ctypes
import errno
import hashlib
import json
import math
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Iterator

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parents[2]
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import _parking_verdicts as parking_verdicts  # noqa: E402
from _seed_constants import (  # noqa: E402
    STATE_NAMES,
    US_JURISDICTION_CODES,
    code_from_slug,
)

SCHEMA_VERSION = 1
SCHEMA_ID = "trekdex.national-parking-census/v1"
PRODUCTION_BASELINE_SCHEMA = "trekdex.parking-production-baseline/v1"
PRODUCTION_BASELINE_SELF_HASH = "sha256(canonical-json-without-self_sha256)"
APPROVED_BASELINE_REGISTRY_SCHEMA = (
    "trekdex.approved-parking-production-baselines/v1"
)
APPROVED_BASELINE_REGISTRY_PATH = (
    _HERE.parent / "approved-production-baselines-v1.json"
)
APPROVED_BASELINE_REGISTRY_SHA256 = (
    "9ca2bc5c15939bac68fdf7b65bccb4f4960b90b3e86d317fe2b341df6a03b731"
)
FILTERED_PBF_ARTIFACT_SCHEMA = "trekdex.filtered-parking-pbf-artifact/v1"
FILTER_EXPRESSION = "nwr/amenity=parking"
CLOSED_NON_AREA_VALUES = frozenset({"no", "false", "0"})
PUBLICATION_RECEIPT_SCHEMA = "trekdex.parking-census-publication-receipt/v1"
ANONYMOUS_TRAIL_REF_SCHEME = (
    "atr1:sha256(canonical-area-and-trail-content-without-source-id)"
    "[:duplicate-ordinal-of-count]"
)
SHAPELY_REQUIRED_VERSION = "2.0.6"
EARTH_RADIUS_M = 6_371_000.0
CLUSTER_M = 40.0
POINT_POLYGON_CENTRE_M = 20.0
OVERLAP_RATIO = 0.98
NEAR_M = 805.0
NEAR_LIMIT = 3
FALLBACK_M = 5_000.0
FALLBACK_LIMIT = 2
LIVE_BBOX_CENTRE_M = 3.0
LIVE_POLYGON_EDGE_M = 10.0
LIVE_POINT_M = 20.0
DEFAULT_SHARD_SIZE = 1_000
MIN_SHARD_SIZE = 500
MAX_SHARD_SIZE = 2_000
ENDPOINT_GRID_DEGREES = math.degrees(FALLBACK_M / EARTH_RADIUS_M)
MAX_ENDPOINT_CANDIDATES_PER_FEATURE = 100_000
MAX_ENDPOINT_GRID_QUERY_CELLS = 500_000
MAX_COMPONENTS_PER_ENDPOINT = 100_000
MAX_TRAIL_CANDIDATE_COMPONENTS = 100_000
MAX_TRAILS_PER_ENDPOINT = 100_000
MAX_LIVE_TRAIL_ASSOCIATIONS = 1_000_000
MAX_BINDINGS_PER_COMPONENT = 1_000_000
TARGET_COMPONENT_INDEX_CELLS = 20_000
MAX_COMPONENT_INDEX_ASSOCIATIONS = 20_000_000
MAX_SPATIAL_CANDIDATES = 100_000
MAX_SPATIAL_PAIRS = 5_000_000
MAX_PARKING_IDENTITIES = 2_000_000
MAX_RETAINED_FEATURES = 2_000_000
MAX_WORK_UNITS = 2_000_000
MAX_STREAM_RECORD_BYTES = 16 * 1024 * 1024
OSMIUM_TIMEOUT_S = 3_600.0
OSMIUM_TERM_GRACE_S = 5.0
MIN_PBF_ARTIFACT_FREE_BYTES = 10 * 1024 * 1024 * 1024
MIN_NATIONAL_PBF_PARKING_IDENTITIES = 500_000
UNASSIGNED_OWNER = "__unassigned__"

HARD_NON_PUBLIC_ACCESS = frozenset({"private", "no", "customers"})
CURRENT_EXCLUDED_ACCESS = frozenset({"private", "no", "customers", "permit"})
CURRENT_EXCLUDED_PARKING = frozenset({
    "street_side", "lane", "on_kerb", "half_on_kerb", "on_street",
    "shoulder", "layby", "painted_area",
})
IDENTITY_CONFLICT_TAGS = ("name", "operator", "access", "parking")
PROJECTIONS = ("raw", "current", "effective")


class CensusError(ValueError):
    """A fail-closed census input or identity error."""


class ArtifactDestinationExists(CensusError):
    """An exclusive artifact promotion found an existing destination."""


class FeatureRejection(ValueError):
    """One explicitly accounted malformed parking feature."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code


@dataclass(frozen=True)
class FileSignature:
    device: int
    inode: int
    mode: int
    uid: int
    gid: int
    links: int
    size: int
    mtime_ns: int
    ctime_ns: int


@dataclass(frozen=True)
class SourceBinding:
    path: Path = field(compare=False, repr=False)
    logical_path: str
    sha256: str
    size_bytes: int
    signature: FileSignature = field(compare=False, repr=False)

    def portable(self) -> dict:
        return {
            "path": self.logical_path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True)
class DirectoryInventory:
    path: Path = field(compare=False, repr=False)
    signature: tuple[int, int, int]
    entries: tuple[tuple[str, FileSignature], ...]


@dataclass(frozen=True)
class Endpoint:
    latitude: float
    longitude: float


@dataclass(frozen=True)
class Trail:
    area: str
    jurisdiction: str
    trail_id: str
    name: str | None
    endpoint_ids: tuple[int, ...]

    @property
    def key(self) -> tuple[str, str]:
        return self.area, self.trail_id


@dataclass
class ScopeCapture:
    bundle_binding: SourceBinding
    geom_inventory: DirectoryInventory
    geom_bindings: tuple[SourceBinding, ...]
    areas: tuple[str, ...]
    jurisdictions: tuple[str, ...]
    excluded_non_us_areas: int
    trails: tuple[Trail, ...]
    endpoints: tuple[Endpoint, ...]
    endpoint_occurrences: int
    trails_without_source_id: int
    anonymous_trail_refs: tuple[str, ...]
    trails_without_endpoints: int


@dataclass
class ParkingFeature:
    alias: str
    source_form: str
    geometry: dict
    latitude: float
    longitude: float
    tags: dict[str, str]
    near_endpoint_ids: tuple[int, ...]
    relation_members: tuple[str, ...] = ()

    @property
    def geometry_type(self) -> str:
        return self.geometry["type"]


@dataclass
class Component:
    candidate_id: str
    aliases: tuple[str, ...]
    members: tuple[ParkingFeature, ...]
    near_endpoint_ids: tuple[int, ...]
    verdict_entry: dict | None = None
    live_pins: list[dict] = field(default_factory=list)
    bindings: list[dict] = field(default_factory=list)
    projection_eligibility: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ServiceTrailGraph:
    trail: Trail
    scored: tuple[tuple[float, int], ...]


@dataclass(frozen=True)
class StaticServiceBinding:
    area: str
    jurisdiction: str
    trail_id: str
    trail_name: str | None
    distance_m: float
    fallback: bool
    projections: tuple[str, ...]


@dataclass(frozen=True)
class StaticProjectionSummary:
    selected_component_indices: tuple[int, ...]
    trail_selections: int
    near_trails: int
    fallback_trails: int


@dataclass(frozen=True)
class ServiceGraph:
    component_ids: tuple[str, ...]
    endpoint_components: tuple[tuple[int, tuple[int, ...]], ...]
    trails: tuple[ServiceTrailGraph, ...]
    static_bindings: tuple[tuple[StaticServiceBinding, ...], ...]
    raw_summary: StaticProjectionSummary
    current_summary: StaticProjectionSummary
    endpoint_component_associations: int
    max_components_per_endpoint: int
    trail_candidate_clusters_total: int
    max_trail_candidate_clusters: int
    association_rows_built: int
    association_rows_sorted: int
    new_distance_computations: int
    reused_distance_evaluations: int
    distance_cache_entries: int
    static_selection_rows: int


@dataclass
class ParkingStreamResult:
    features: tuple[ParkingFeature, ...]
    binding: SourceBinding
    metadata: dict
    counters: dict
    relation_memberships: dict[str, tuple[str, ...]]
    seen_authority_aliases: tuple[str, ...]
    inventory: dict = field(default_factory=dict)
    osmium_binding: SourceBinding | None = None
    osmium: dict | None = None
    filtered_binding: SourceBinding | None = None
    filtered_manifest_binding: SourceBinding | None = None


@dataclass(frozen=True)
class ProductionBaseline:
    binding: SourceBinding
    self_sha256: str
    document: dict
    registry_binding: SourceBinding | None = field(
        default=None, compare=False, repr=False
    )
    approval: dict | None = field(default=None, compare=False, repr=False)


@dataclass
class LiveCapture:
    pins: tuple[dict, ...]
    binding: SourceBinding


@dataclass
class VerdictCapture:
    verdicts: parking_verdicts.Verdicts
    binding: SourceBinding


@dataclass(frozen=True)
class OsmiumTool:
    binding: SourceBinding
    version: str


@dataclass(frozen=True)
class FilteredArtifact:
    path: Path = field(compare=False, repr=False)
    document: dict
    filtered_binding: SourceBinding
    manifest_binding: SourceBinding


@dataclass(frozen=True)
class VerdictLedger:
    assignments: dict[str, dict]
    matched_keys: tuple[str, ...]
    tombstone_entries: tuple[dict, ...]
    claim_edges: int
    ambiguous_edges: int
    reserved_authoritative_keys: tuple[str, ...] = ()
    reserved_unselected_keys: tuple[str, ...] = ()
    seen_authority_aliases: tuple[str, ...] = ()
    seen_authority_keys: tuple[str, ...] = ()


def _canonical_bytes(value: object) -> bytes:
    return (json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ) + "\n").encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_hash(value: object) -> str:
    return _sha256(_canonical_bytes(value))


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_nonfinite(token: str):
    raise ValueError(f"non-finite JSON number {token}")


def _strict_json_bytes(data: bytes, label: str) -> object:
    try:
        return json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise CensusError(f"{label} is not strict JSON: {error}") from error


def _signature(value: os.stat_result) -> FileSignature:
    return FileSignature(
        value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_gid,
        value.st_nlink, value.st_size, value.st_mtime_ns, value.st_ctime_ns,
    )


def _capture_file_bytes(
        path: str | Path, logical_path: str | None = None, *,
        role: str = "input") -> tuple[bytes, SourceBinding]:
    candidate = Path(path).absolute()
    display_label = logical_path or f"{role} input"
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(candidate, flags)
    except OSError as error:
        raise CensusError(f"cannot securely open {display_label}: {error}") from error
    try:
        before = _signature(os.fstat(fd))
        entry_before = _signature(os.stat(candidate, follow_symlinks=False))
        if not stat.S_ISREG(before.mode) or before != entry_before:
            raise CensusError(f"{display_label} is not one stable regular file")
        hasher = hashlib.sha256()
        chunks = []
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
            chunks.append(chunk)
        after = _signature(os.fstat(fd))
        entry_after = _signature(os.stat(candidate, follow_symlinks=False))
        if not (before == after == entry_before == entry_after):
            raise CensusError(f"{display_label} changed during capture")
        data = b"".join(chunks)
        if len(data) != before.size:
            raise CensusError(f"{display_label} size changed during capture")
        digest = hasher.hexdigest()
        return data, SourceBinding(
            candidate,
            logical_path or _portable_label(candidate, digest, role),
            digest,
            len(data),
            before,
        )
    finally:
        os.close(fd)


def _capture_file_hash(
        path: str | Path, logical_path: str | None = None, *,
        role: str = "input") -> SourceBinding:
    """Hash a potentially huge source without retaining its bytes."""
    candidate = Path(path).absolute()
    display_label = logical_path or f"{role} input"
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(candidate, flags)
    except OSError as error:
        raise CensusError(f"cannot securely open {display_label}: {error}") from error
    try:
        before = _signature(os.fstat(fd))
        entry_before = _signature(os.stat(candidate, follow_symlinks=False))
        if not stat.S_ISREG(before.mode) or before != entry_before:
            raise CensusError(f"{display_label} is not one stable regular file")
        hasher = hashlib.sha256()
        size = 0
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
            size += len(chunk)
        after = _signature(os.fstat(fd))
        entry_after = _signature(os.stat(candidate, follow_symlinks=False))
        if not (before == after == entry_before == entry_after) or size != before.size:
            raise CensusError(f"{display_label} changed during capture")
        digest = hasher.hexdigest()
        return SourceBinding(
            candidate,
            logical_path or _portable_label(candidate, digest, role),
            digest,
            size,
            before,
        )
    finally:
        os.close(fd)


def _verify_binding(binding: SourceBinding) -> None:
    try:
        current = _signature(os.stat(binding.path, follow_symlinks=False))
    except OSError as error:
        raise CensusError(
            f"source disappeared after capture: {binding.logical_path}"
        ) from error
    if current != binding.signature or not stat.S_ISREG(current.mode):
        raise CensusError(f"source changed after capture: {binding.logical_path}")


def _directory_inventory(path: str | Path) -> DirectoryInventory:
    root = Path(path).absolute()
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(root, flags)
    except OSError as error:
        raise CensusError(f"cannot securely open geometry directory: {error}") from error
    try:
        root_stat = os.fstat(fd)
        entries = []
        for name in sorted(os.listdir(fd)):
            if not name.endswith(".json"):
                continue
            value = os.stat(name, dir_fd=fd, follow_symlinks=False)
            signature = _signature(value)
            if not stat.S_ISREG(signature.mode):
                raise CensusError(
                    f"geometry entry is not a regular no-follow file: {name}"
                )
            entries.append((name, signature))
        return DirectoryInventory(
            root,
            (root_stat.st_dev, root_stat.st_ino, root_stat.st_mode),
            tuple(entries),
        )
    finally:
        os.close(fd)


def _verify_inventory(inventory: DirectoryInventory) -> None:
    current = _directory_inventory(inventory.path)
    if current.signature != inventory.signature or current.entries != inventory.entries:
        raise CensusError("geometry input inventory or file identity changed")


def _portable_label(path: Path, digest: str | None = None,
                    role: str = "input") -> str:
    resolved = path.absolute().resolve()
    try:
        return resolved.relative_to(_ROOT).as_posix()
    except ValueError:
        if digest is None:
            raise CensusError(
                f"external {role} needs a content digest for portable provenance"
            )
        return f"external/{role}-sha256-{digest}"


def _portable_directory_label(
        path: Path, bindings: Iterable[SourceBinding], role: str) -> str:
    resolved = path.absolute().resolve()
    try:
        return resolved.relative_to(_ROOT).as_posix()
    except ValueError:
        digest = _canonical_hash([
            binding.sha256 for binding in sorted(
                bindings, key=lambda value: (value.logical_path, value.sha256)
            )
        ])
        return f"external/{role}-set-sha256-{digest}"


def _finite_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CensusError(f"{label} must be a nonboolean number")
    number = float(value)
    if not math.isfinite(number):
        raise CensusError(f"{label} must be finite")
    return 0.0 if number == 0 else number


def _coordinate(value: object, label: str) -> tuple[float, float]:
    if not isinstance(value, list) or len(value) < 2:
        raise CensusError(f"{label} must be a coordinate pair")
    longitude = _finite_number(value[0], f"{label} longitude")
    latitude = _finite_number(value[1], f"{label} latitude")
    if not -180.0 <= longitude <= 180.0 or not -90.0 <= latitude <= 90.0:
        raise CensusError(f"{label} is outside geographic coordinate range")
    return longitude, latitude


def _trail_coordinate(value: object, label: str) -> tuple[float, float]:
    if not isinstance(value, list) or len(value) != 2:
        raise CensusError(f"{label} must be [latitude, longitude]")
    latitude = _finite_number(value[0], f"{label} latitude")
    longitude = _finite_number(value[1], f"{label} longitude")
    if not -90.0 <= latitude <= 90.0 or not -180.0 <= longitude <= 180.0:
        raise CensusError(f"{label} is outside geographic coordinate range")
    return latitude, longitude


def _anonymous_trail_refs(slug: str, trail_rows: list) -> dict[int, str]:
    """Derive stable refs for trails whose source id is absent.

    The digest binds the area and complete canonical trail content except the
    absent source-id field. Identical anonymous rows receive a stable multiset
    of ordinal suffixes; because their bound content is identical, reordering
    those rows cannot change the resulting content/ref pairs.
    """
    groups: dict[bytes, list[int]] = collections.defaultdict(list)
    digests: dict[bytes, str] = {}
    digest_payloads: dict[str, bytes] = {}
    for index, row in enumerate(trail_rows):
        if not isinstance(row, dict):
            continue
        source_id = row.get("id")
        if isinstance(source_id, str) and source_id:
            continue
        content = {key: value for key, value in row.items() if key != "id"}
        payload = _canonical_bytes({
            "area": slug,
            "scheme": "atr1",
            "trail": content,
        })
        digest = _sha256(payload)
        previous = digest_payloads.setdefault(digest, payload)
        if previous != payload:
            raise CensusError(
                f"geometry {slug} anonymous trail content hash collision"
            )
        groups[payload].append(index)
        digests[payload] = digest
    result = {}
    for payload in sorted(groups):
        indices = groups[payload]
        base = f"atr1:{digests[payload]}"
        count = len(indices)
        for ordinal, index in enumerate(indices, start=1):
            result[index] = (
                base if count == 1 else f"{base}:{ordinal}-of-{count}"
            )
    return result


def _area_slugs_sha256(areas: Iterable[str]) -> str:
    return _sha256(("\n".join(sorted(areas)) + "\n").encode("utf-8"))


def _geometry_authority_sha256(bindings: Iterable[SourceBinding]) -> str:
    return _canonical_hash([binding.sha256 for binding in bindings])


def capture_scope(bundle_path: str | Path, geom_dir: str | Path) -> ScopeCapture:
    """Capture the exact shipped US area set and only trail endpoints.

    Each geometry file is read and hashed once, then its parsed document is
    discarded before the next file. The national geometry corpus is never
    retained as a parsed whole.
    """
    bundle_candidate = Path(bundle_path).absolute()
    bundle_raw, bundle_binding = _capture_file_bytes(
        bundle_candidate, role="areas-index"
    )
    bundle = _strict_json_bytes(bundle_raw, "shipped areas index")
    if not isinstance(bundle, list):
        raise CensusError("shipped areas index root must be an array")

    all_slugs = []
    seen_slugs: set[str] = set()
    us_rows: dict[str, tuple[str, list]] = {}
    excluded = 0
    for index, row in enumerate(bundle):
        if not isinstance(row, list) or not row or not isinstance(row[0], str) or not row[0]:
            raise CensusError(f"areas index row {index} has no canonical slug")
        slug = row[0]
        if slug in seen_slugs:
            raise CensusError(f"areas index duplicates slug {slug!r}")
        seen_slugs.add(slug)
        all_slugs.append(slug)
        jurisdiction = code_from_slug(slug)
        if jurisdiction is None:
            raise CensusError(
                f"area {slug!r} has no recognized canonical jurisdiction suffix"
            )
        if jurisdiction not in US_JURISDICTION_CODES:
            if jurisdiction not in STATE_NAMES:
                raise CensusError(f"area {slug!r} has unknown jurisdiction {jurisdiction!r}")
            excluded += 1
            continue
        if len(row) < 3 or row[2] != STATE_NAMES[jurisdiction]:
            raise CensusError(
                f"area {slug!r} label does not match canonical {jurisdiction} name"
            )
        us_rows[slug] = (jurisdiction, row)

    jurisdictions = frozenset(code for code, _row in us_rows.values())
    if jurisdictions != US_JURISDICTION_CODES:
        missing = sorted(US_JURISDICTION_CODES - jurisdictions)
        extra = sorted(jurisdictions - US_JURISDICTION_CODES)
        raise CensusError(
            f"shipped US scope is not exactly 50 states plus DC; missing={missing}, extra={extra}"
        )

    inventory = _directory_inventory(geom_dir)
    geom_names = {name[:-5] for name, _signature_value in inventory.entries}
    bundle_names = set(all_slugs)
    if geom_names != bundle_names:
        missing = sorted(bundle_names - geom_names)
        extra = sorted(geom_names - bundle_names)
        raise CensusError(
            "bundle/geometry correspondence failed: "
            f"missing={missing[:10]}, extra={extra[:10]}"
        )

    endpoints: list[Endpoint] = []
    endpoint_by_coordinate: dict[tuple[float, float], int] = {}
    trails: list[Trail] = []
    geom_bindings: list[SourceBinding] = []
    endpoint_occurrences = 0
    trails_without_source_id = 0
    anonymous_trail_refs: list[str] = []
    trails_without_endpoints = 0

    for slug in sorted(us_rows):
        jurisdiction = us_rows[slug][0]
        path = inventory.path / f"{slug}.json"
        raw, binding = _capture_file_bytes(path, role="geometry")
        geom_bindings.append(binding)
        document = _strict_json_bytes(raw, f"geometry {slug}")
        if not isinstance(document, dict) or document.get("id") != slug:
            raise CensusError(f"geometry {slug} does not bind its filename id")
        if document.get("state") != STATE_NAMES[jurisdiction]:
            raise CensusError(f"geometry {slug} has the wrong jurisdiction label")
        trail_rows = document.get("trails")
        if not isinstance(trail_rows, list):
            raise CensusError(f"geometry {slug} trails must be an array")
        derived_anonymous_refs = _anonymous_trail_refs(slug, trail_rows)
        seen_trail_ids: set[str] = set()
        for trail_index, trail_row in enumerate(trail_rows):
            label = f"geometry {slug} trail {trail_index}"
            if not isinstance(trail_row, dict):
                raise CensusError(f"{label} must be an object")
            source_trail_id = trail_row.get("id")
            if source_trail_id is None or source_trail_id == "":
                trail_id = derived_anonymous_refs[trail_index]
                trails_without_source_id += 1
                anonymous_trail_refs.append(trail_id)
            elif isinstance(source_trail_id, str):
                trail_id = source_trail_id
            else:
                raise CensusError(f"{label} source trail id must be a string or absent")
            if trail_id in seen_trail_ids:
                raise CensusError(f"geometry {slug} duplicates trail ref {trail_id!r}")
            seen_trail_ids.add(trail_id)
            name = trail_row.get("name")
            if name is not None and not isinstance(name, str):
                raise CensusError(f"{label} name must be a string or null")
            segments = trail_row.get("segments")
            if not isinstance(segments, list):
                raise CensusError(f"{label} segments must be an array")
            trail_endpoint_ids: list[int] = []
            for segment_index, segment in enumerate(segments):
                segment_label = f"{label} segment {segment_index}"
                if not isinstance(segment, list):
                    raise CensusError(f"{segment_label} must be an array")
                validated = [
                    _trail_coordinate(point, f"{segment_label} point {point_index}")
                    for point_index, point in enumerate(segment)
                ]
                if not validated:
                    continue
                for latitude, longitude in (validated[0], validated[-1]):
                    endpoint_occurrences += 1
                    key = (latitude, longitude)
                    endpoint_id = endpoint_by_coordinate.get(key)
                    if endpoint_id is None:
                        endpoint_id = len(endpoints)
                        endpoint_by_coordinate[key] = endpoint_id
                        endpoints.append(Endpoint(latitude, longitude))
                    trail_endpoint_ids.append(endpoint_id)
            unique_ids = tuple(dict.fromkeys(trail_endpoint_ids))
            if not unique_ids:
                trails_without_endpoints += 1
            trails.append(Trail(
                slug, jurisdiction, trail_id, name, unique_ids,
            ))
        del document, raw

    trail_keys = [trail.key for trail in trails]
    if len(trail_keys) != len(set(trail_keys)):
        raise CensusError("shipped scope contains duplicate final trail refs")
    if len(anonymous_trail_refs) != len(set(anonymous_trail_refs)):
        raise CensusError("anonymous trail refs are not globally unique")
    _verify_inventory(inventory)
    _verify_binding(bundle_binding)
    return ScopeCapture(
        bundle_binding=bundle_binding,
        geom_inventory=inventory,
        geom_bindings=tuple(geom_bindings),
        areas=tuple(sorted(us_rows)),
        jurisdictions=tuple(sorted(jurisdictions)),
        excluded_non_us_areas=excluded,
        trails=tuple(trails),
        endpoints=tuple(endpoints),
        endpoint_occurrences=endpoint_occurrences,
        trails_without_source_id=trails_without_source_id,
        anonymous_trail_refs=tuple(sorted(anonymous_trail_refs)),
        trails_without_endpoints=trails_without_endpoints,
    )


def _wrap_longitude(longitude: float) -> float:
    wrapped = (longitude + 180.0) % 360.0 - 180.0
    return 180.0 if wrapped == -180.0 and longitude > 0 else wrapped


def _longitude_delta(first: float, second: float) -> float:
    return (second - first + 180.0) % 360.0 - 180.0


def haversine_m(
        latitude1: float, longitude1: float,
        latitude2: float, longitude2: float) -> float:
    p = math.radians
    d_latitude = p(latitude2 - latitude1)
    d_longitude = p(_longitude_delta(longitude1, longitude2))
    value = (
        math.sin(d_latitude / 2.0) ** 2
        + math.cos(p(latitude1)) * math.cos(p(latitude2))
        * math.sin(d_longitude / 2.0) ** 2
    )
    return EARTH_RADIUS_M * 2.0 * math.asin(min(1.0, math.sqrt(value)))


def _bearing_radians(
        latitude1: float, longitude1: float,
        latitude2: float, longitude2: float) -> float:
    first = math.radians(latitude1)
    second = math.radians(latitude2)
    delta = math.radians(_longitude_delta(longitude1, longitude2))
    return math.atan2(
        math.sin(delta) * math.cos(second),
        math.cos(first) * math.sin(second)
        - math.sin(first) * math.cos(second) * math.cos(delta),
    )


def _segment_distance_m(
        latitude: float, longitude: float,
        first: tuple[float, float], second: tuple[float, float]) -> float:
    first_latitude, first_longitude = first
    second_latitude, second_longitude = second
    length = haversine_m(
        first_latitude, first_longitude, second_latitude, second_longitude
    )
    if length <= 1e-9:
        return haversine_m(latitude, longitude, first_latitude, first_longitude)
    d13 = haversine_m(
        first_latitude, first_longitude, latitude, longitude
    ) / EARTH_RADIUS_M
    theta13 = _bearing_radians(
        first_latitude, first_longitude, latitude, longitude
    )
    theta12 = _bearing_radians(
        first_latitude, first_longitude, second_latitude, second_longitude
    )
    cross = math.asin(max(-1.0, min(1.0, math.sin(d13) * math.sin(theta13 - theta12))))
    along = math.atan2(
        math.sin(d13) * math.cos(theta13 - theta12), math.cos(d13)
    ) * EARTH_RADIUS_M
    if 0.0 <= along <= length:
        return abs(cross) * EARTH_RADIUS_M
    return min(
        haversine_m(latitude, longitude, first_latitude, first_longitude),
        haversine_m(latitude, longitude, second_latitude, second_longitude),
    )


def _minimal_longitude_arc(longitudes: Iterable[float]) -> tuple[float, float]:
    values = sorted({longitude % 360.0 for longitude in longitudes})
    if not values:
        raise CensusError("geometry has no longitude")
    if len(values) == 1:
        return values[0], 0.0
    gaps = [
        ((values[(index + 1) % len(values)] - values[index]) % 360.0, index)
        for index in range(len(values))
    ]
    largest_gap, index = max(gaps)
    start = values[(index + 1) % len(values)]
    return start, 360.0 - largest_gap


def _unwrapped_longitude(longitude: float, anchor: float) -> float:
    return anchor + _longitude_delta(anchor, longitude)


def _iter_polygons(geometry: dict) -> Iterator[list[list[list[float]]]]:
    geometry_type = geometry["type"]
    if geometry_type == "Polygon":
        yield geometry["coordinates"]
    elif geometry_type == "MultiPolygon":
        yield from geometry["coordinates"]


def _iter_lines(geometry: dict) -> Iterator[list[list[float]]]:
    geometry_type = geometry["type"]
    if geometry_type == "LineString":
        yield geometry["coordinates"]
    elif geometry_type in ("Polygon", "MultiPolygon"):
        for polygon in _iter_polygons(geometry):
            yield from polygon


def _iter_vertices(geometry: dict) -> Iterator[tuple[float, float]]:
    if geometry["type"] == "Point":
        longitude, latitude = geometry["coordinates"]
        yield latitude, longitude
        return
    for line in _iter_lines(geometry):
        for longitude, latitude in line:
            yield latitude, longitude


def _point_in_ring(
        latitude: float, longitude: float, ring: list[list[float]]) -> bool:
    inside = False
    x = 0.0
    for index in range(len(ring) - 1):
        longitude1, latitude1 = ring[index]
        longitude2, latitude2 = ring[index + 1]
        x1 = _longitude_delta(longitude, longitude1)
        x2 = _longitude_delta(longitude, longitude2)
        if (latitude1 > latitude) != (latitude2 > latitude):
            crossing = x1 + (x2 - x1) * (latitude - latitude1) / (latitude2 - latitude1)
            if x < crossing:
                inside = not inside
    return inside


def point_in_geometry(latitude: float, longitude: float, geometry: dict) -> bool:
    for polygon in _iter_polygons(geometry):
        if not polygon or not _point_in_ring(latitude, longitude, polygon[0]):
            continue
        if any(_point_in_ring(latitude, longitude, hole) for hole in polygon[1:]):
            continue
        return True
    return False


def geometry_distance_to_point_m(
        geometry: dict, latitude: float, longitude: float) -> float:
    if geometry["type"] == "Point":
        point_longitude, point_latitude = geometry["coordinates"]
        return haversine_m(latitude, longitude, point_latitude, point_longitude)
    if geometry["type"] in ("Polygon", "MultiPolygon") and point_in_geometry(
            latitude, longitude, geometry):
        return 0.0
    best = math.inf
    for line in _iter_lines(geometry):
        for first, second in zip(line, line[1:]):
            best = min(best, _segment_distance_m(
                latitude,
                longitude,
                (first[1], first[0]),
                (second[1], second[0]),
            ))
    return best


def _line_segments(geometry: dict) -> Iterator[
        tuple[tuple[float, float], tuple[float, float]]]:
    for line in _iter_lines(geometry):
        for first, second in zip(line, line[1:]):
            yield (first[1], first[0]), (second[1], second[0])


def _orientation(a, b, c, anchor: float) -> float:
    ax, ay = _unwrapped_longitude(a[1], anchor), a[0]
    bx, by = _unwrapped_longitude(b[1], anchor), b[0]
    cx, cy = _unwrapped_longitude(c[1], anchor), c[0]
    return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)


def _segments_intersect(a, b, c, d) -> bool:
    anchor = a[1]
    first = _orientation(a, b, c, anchor)
    second = _orientation(a, b, d, anchor)
    third = _orientation(c, d, a, anchor)
    fourth = _orientation(c, d, b, anchor)
    epsilon = 1e-12
    return (
        ((first > epsilon and second < -epsilon) or (first < -epsilon and second > epsilon))
        and ((third > epsilon and fourth < -epsilon) or (third < -epsilon and fourth > epsilon))
    )


def geometry_distance_m(first: dict, second: dict) -> float:
    if first["type"] == "Point":
        longitude, latitude = first["coordinates"]
        return geometry_distance_to_point_m(second, latitude, longitude)
    if second["type"] == "Point":
        longitude, latitude = second["coordinates"]
        return geometry_distance_to_point_m(first, latitude, longitude)
    first_vertices = list(_iter_vertices(first))
    second_vertices = list(_iter_vertices(second))
    if first["type"] in ("Polygon", "MultiPolygon") and any(
            point_in_geometry(latitude, longitude, first)
            for latitude, longitude in second_vertices):
        return 0.0
    if second["type"] in ("Polygon", "MultiPolygon") and any(
            point_in_geometry(latitude, longitude, second)
            for latitude, longitude in first_vertices):
        return 0.0
    first_segments = list(_line_segments(first))
    second_segments = list(_line_segments(second))
    if any(_segments_intersect(a, b, c, d)
           for a, b in first_segments for c, d in second_segments):
        return 0.0
    best = math.inf
    for latitude, longitude in first_vertices:
        best = min(best, geometry_distance_to_point_m(second, latitude, longitude))
    for latitude, longitude in second_vertices:
        best = min(best, geometry_distance_to_point_m(first, latitude, longitude))
    return best


def _normalize_line(value: object, label: str, minimum: int) -> list[list[float]]:
    if not isinstance(value, list) or len(value) < minimum:
        raise FeatureRejection("malformed_geometry", f"{label} has too few coordinates")
    normalized = []
    for index, point in enumerate(value):
        try:
            longitude, latitude = _coordinate(point, f"{label}[{index}]")
        except CensusError as error:
            raise FeatureRejection("malformed_geometry", str(error)) from error
        normalized.append([longitude, latitude])
    return normalized


def _ring_twice_area(ring: list[list[float]]) -> float:
    anchor = ring[0][0]
    points = [(_unwrapped_longitude(point[0], anchor), point[1]) for point in ring]
    return abs(sum(
        first[0] * second[1] - second[0] * first[1]
        for first, second in zip(points, points[1:])
    ))


def _ring_self_intersects(ring: list[list[float]]) -> bool:
    anchor = ring[0][0]
    points = [
        (_unwrapped_longitude(point[0], anchor), point[1])
        for point in ring
    ]

    def orientation(first, second, third) -> float:
        return (
            (second[0] - first[0]) * (third[1] - first[1])
            - (second[1] - first[1]) * (third[0] - first[0])
        )

    def on_segment(first, second, point) -> bool:
        epsilon = 1e-12
        return (
            min(first[0], second[0]) - epsilon <= point[0]
            <= max(first[0], second[0]) + epsilon
            and min(first[1], second[1]) - epsilon <= point[1]
            <= max(first[1], second[1]) + epsilon
        )

    def intersects(first, second, third, fourth) -> bool:
        epsilon = 1e-12
        values = (
            orientation(first, second, third),
            orientation(first, second, fourth),
            orientation(third, fourth, first),
            orientation(third, fourth, second),
        )
        if ((values[0] > epsilon and values[1] < -epsilon)
                or (values[0] < -epsilon and values[1] > epsilon)):
            if ((values[2] > epsilon and values[3] < -epsilon)
                    or (values[2] < -epsilon and values[3] > epsilon)):
                return True
        return (
            (abs(values[0]) <= epsilon and on_segment(first, second, third))
            or (abs(values[1]) <= epsilon and on_segment(first, second, fourth))
            or (abs(values[2]) <= epsilon and on_segment(third, fourth, first))
            or (abs(values[3]) <= epsilon and on_segment(third, fourth, second))
        )

    segment_count = len(points) - 1
    for first_index in range(segment_count):
        for second_index in range(first_index + 1, segment_count):
            if second_index == first_index + 1:
                continue
            if first_index == 0 and second_index == segment_count - 1:
                continue
            if intersects(
                    points[first_index], points[first_index + 1],
                    points[second_index], points[second_index + 1]):
                return True
    return False


def _normalize_ring(value: object, label: str) -> list[list[float]]:
    ring = _normalize_line(value, label, 4)
    if ring[0] != ring[-1]:
        raise FeatureRejection("malformed_geometry", f"{label} is not explicitly closed")
    if len({tuple(point) for point in ring[:-1]}) < 3 or _ring_twice_area(ring) <= 1e-15:
        raise FeatureRejection("malformed_geometry", f"{label} is degenerate")
    if _ring_self_intersects(ring):
        raise FeatureRejection(
            "malformed_geometry", f"{label} is topologically self-intersecting"
        )
    return ring


def _require_shapely():
    try:
        import shapely
        from shapely.geometry import shape
        from shapely.validation import explain_validity
    except ImportError as error:
        raise CensusError(
            f"parking census requires Shapely=={SHAPELY_REQUIRED_VERSION}; "
            "install scripts/parking-adjud/requirements.txt"
        ) from error
    version = getattr(shapely, "__version__", None)
    if version != SHAPELY_REQUIRED_VERSION:
        raise CensusError(
            f"parking census requires Shapely=={SHAPELY_REQUIRED_VERSION}; "
            f"found {version or 'unknown'}"
        )
    return shape, explain_validity


def _geometry_engine_manifest() -> dict:
    _require_shapely()
    return {
        "engine": "Shapely",
        "version": SHAPELY_REQUIRED_VERSION,
        "requirement": f"Shapely=={SHAPELY_REQUIRED_VERSION}",
        "validity": "complete Polygon/MultiPolygon topology",
    }


def _validate_polygon_topology(geometry: dict) -> None:
    shape, explain_validity = _require_shapely()
    anchor = next(_iter_vertices(geometry))[1]
    try:
        value = shape(_unwrap_geometry(geometry, anchor))
    except Exception as error:
        raise FeatureRejection(
            "malformed_geometry", f"polygon topology could not be constructed: {error}"
        ) from error
    if value.is_empty or value.geom_type != geometry["type"] or not value.is_valid:
        detail = explain_validity(value)
        raise FeatureRejection(
            "malformed_geometry", f"invalid complete polygon topology: {detail}"
        )


def normalize_geometry(value: object) -> dict:
    if not isinstance(value, dict) or not isinstance(value.get("type"), str):
        raise FeatureRejection("malformed_geometry", "geometry is not an object with a type")
    geometry_type = value["type"]
    coordinates = value.get("coordinates")
    if geometry_type == "Point":
        try:
            longitude, latitude = _coordinate(coordinates, "point")
        except CensusError as error:
            raise FeatureRejection("malformed_geometry", str(error)) from error
        return {"type": "Point", "coordinates": [longitude, latitude]}
    if geometry_type == "LineString":
        return {
            "type": "LineString",
            "coordinates": _normalize_line(coordinates, "line", 2),
        }
    if geometry_type == "Polygon":
        if not isinstance(coordinates, list) or not coordinates:
            raise FeatureRejection("malformed_geometry", "polygon has no rings")
        normalized = {
            "type": "Polygon",
            "coordinates": [
                _normalize_ring(ring, f"polygon ring {index}")
                for index, ring in enumerate(coordinates)
            ],
        }
        _validate_polygon_topology(normalized)
        return normalized
    if geometry_type == "MultiPolygon":
        if not isinstance(coordinates, list) or not coordinates:
            raise FeatureRejection("malformed_geometry", "multipolygon has no polygons")
        polygons = []
        for polygon_index, polygon in enumerate(coordinates):
            if not isinstance(polygon, list) or not polygon:
                raise FeatureRejection(
                    "malformed_geometry", f"multipolygon {polygon_index} has no rings"
                )
            polygons.append([
                _normalize_ring(ring, f"multipolygon {polygon_index} ring {ring_index}")
                for ring_index, ring in enumerate(polygon)
            ])
        normalized = {"type": "MultiPolygon", "coordinates": polygons}
        _validate_polygon_topology(normalized)
        return normalized
    raise FeatureRejection(
        "unsupported_geometry", f"unsupported geometry type {geometry_type!r}"
    )


def _ring_centroid(ring: list[list[float]]) -> tuple[float, float, float]:
    anchor = ring[0][0]
    points = [(_unwrapped_longitude(point[0], anchor), point[1]) for point in ring]
    cross_sum = 0.0
    x_sum = 0.0
    y_sum = 0.0
    for first, second in zip(points, points[1:]):
        cross = first[0] * second[1] - second[0] * first[1]
        cross_sum += cross
        x_sum += (first[0] + second[0]) * cross
        y_sum += (first[1] + second[1]) * cross
    if abs(cross_sum) <= 1e-15:
        longitudes = [point[0] for point in ring[:-1]]
        latitudes = [point[1] for point in ring[:-1]]
        return sum(latitudes) / len(latitudes), _wrap_longitude(
            sum(longitudes) / len(longitudes)
        ), 0.0
    longitude = x_sum / (3.0 * cross_sum)
    latitude = y_sum / (3.0 * cross_sum)
    return latitude, _wrap_longitude(longitude), abs(cross_sum)


def geometry_representative(geometry: dict) -> tuple[float, float]:
    if geometry["type"] == "Point":
        longitude, latitude = geometry["coordinates"]
        return latitude, longitude
    if geometry["type"] == "LineString":
        vertices = geometry["coordinates"]
        start, span = _minimal_longitude_arc(point[0] for point in vertices)
        return (
            (min(point[1] for point in vertices) + max(point[1] for point in vertices)) / 2.0,
            _wrap_longitude(start + span / 2.0),
        )
    weighted = []
    for polygon in _iter_polygons(geometry):
        latitude, longitude, weight = _ring_centroid(polygon[0])
        weighted.append((latitude, longitude, weight))
    if not weighted or sum(item[2] for item in weighted) <= 1e-15:
        vertices = list(_iter_vertices(geometry))
        return vertices[0]
    anchor = weighted[0][1]
    total = sum(item[2] for item in weighted)
    return (
        sum(item[0] * item[2] for item in weighted) / total,
        _wrap_longitude(sum(
            _unwrapped_longitude(item[1], anchor) * item[2] for item in weighted
        ) / total),
    )


def geometry_bounds(geometry: dict) -> tuple[float, float, float, float]:
    vertices = list(_iter_vertices(geometry))
    latitudes = [point[0] for point in vertices]
    start, span = _minimal_longitude_arc(point[1] for point in vertices)
    return min(latitudes), max(latitudes), start, span


def geometry_bbox_center(geometry: dict) -> tuple[float, float]:
    minimum_latitude, maximum_latitude, start, span = geometry_bounds(geometry)
    return (
        (minimum_latitude + maximum_latitude) / 2.0,
        _wrap_longitude(start + span / 2.0),
    )


class EndpointGrid:
    """Dateline-safe geographic cell index for immutable endpoint ids."""

    def __init__(
            self, endpoints: tuple[Endpoint, ...],
            cell_degrees=ENDPOINT_GRID_DEGREES):
        self.endpoints = endpoints
        self.cell = cell_degrees
        self.longitude_cells = round(360.0 / cell_degrees)
        self.latitude_cells = round(180.0 / cell_degrees)
        self.rows: dict[int, dict[int, list[int]]] = {}
        for endpoint_id, endpoint in enumerate(endpoints):
            latitude_cell = self._latitude_cell(endpoint.latitude)
            longitude_cell = self._longitude_cell(endpoint.longitude)
            self.rows.setdefault(latitude_cell, {}).setdefault(
                longitude_cell, []
            ).append(endpoint_id)

    def _latitude_cell(self, latitude: float) -> int:
        return max(0, min(
            self.latitude_cells - 1,
            int(math.floor((latitude + 90.0) / self.cell)),
        ))

    def _longitude_cell(self, longitude: float) -> int:
        return int(math.floor((longitude % 360.0) / self.cell)) % self.longitude_cells

    @staticmethod
    def _longitude_pad(radius_m: float, maximum_abs_latitude: float) -> float:
        cosine = math.cos(math.radians(min(90.0, maximum_abs_latitude)))
        if cosine <= 1e-8:
            return 180.0
        return min(180.0, math.degrees(radius_m / EARTH_RADIUS_M) / cosine)

    def candidate_ids(
            self, geometry: dict, radius_m: float, *,
            limit: int | None = None,
            context: str = "geometry") -> set[int]:
        minimum_latitude, maximum_latitude, start, span = geometry_bounds(geometry)
        latitude_pad = math.degrees(radius_m / EARTH_RADIUS_M)
        south = max(-90.0, minimum_latitude - latitude_pad)
        north = min(90.0, maximum_latitude + latitude_pad)
        maximum_abs = max(abs(south), abs(north))
        longitude_pad = self._longitude_pad(radius_m, maximum_abs)
        expanded_span = span + 2.0 * longitude_pad
        expanded_start = (start - longitude_pad) % 360.0
        first_latitude_cell = self._latitude_cell(south)
        last_latitude_cell = self._latitude_cell(north)
        longitude_cell_count = (
            self.longitude_cells if expanded_span >= 360.0
            else int(math.ceil(expanded_span / self.cell)) + 3
        )
        query_cells = (
            last_latitude_cell - first_latitude_cell + 1
        ) * longitude_cell_count
        if query_cells > MAX_ENDPOINT_GRID_QUERY_CELLS:
            raise CensusError(
                f"{context} spans {query_cells:,} endpoint-grid cells; "
                f"limit is {MAX_ENDPOINT_GRID_QUERY_CELLS:,}"
            )
        result: set[int] = set()

        def add(ids: Iterable[int]) -> None:
            result.update(ids)
            if limit is not None and len(result) > limit:
                raise CensusError(
                    f"{context} has more than {limit:,} coarse endpoint "
                    "candidates"
                )

        for latitude_cell in range(first_latitude_cell, last_latitude_cell + 1):
            row = self.rows.get(latitude_cell)
            if not row:
                continue
            if expanded_span >= 360.0:
                for ids in row.values():
                    add(ids)
                continue
            first = int(math.floor(expanded_start / self.cell)) - 1
            count = int(math.ceil(expanded_span / self.cell)) + 3
            for offset in range(count):
                ids = row.get((first + offset) % self.longitude_cells)
                if ids:
                    add(ids)
        return result

    def within_candidates(
            self, geometry: dict, candidate_ids: Iterable[int],
            radius_m: float) -> tuple[int, ...]:
        return tuple(sorted(
            endpoint_id
            for endpoint_id in candidate_ids
            if geometry_distance_to_point_m(
                geometry,
                self.endpoints[endpoint_id].latitude,
                self.endpoints[endpoint_id].longitude,
            ) <= radius_m
        ))

    def within_geometry(
            self, geometry: dict, radius_m: float, *,
            limit: int | None = None,
            context: str = "geometry") -> tuple[int, ...]:
        return self.within_candidates(
            geometry,
            self.candidate_ids(
                geometry, radius_m, limit=limit, context=context
            ),
            radius_m,
        )

    def within_point(
            self, latitude: float, longitude: float, radius_m: float, *,
            limit: int | None = None,
            context: str = "point") -> tuple[int, ...]:
        geometry = {"type": "Point", "coordinates": [longitude, latitude]}
        return self.within_geometry(
            geometry, radius_m, limit=limit, context=context
        )


def canonical_osm_alias(value: object) -> tuple[str, str]:
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise FeatureRejection("missing_identity", "feature id is not canonical")
    text = str(value).strip()
    source_form = text
    area = re.fullmatch(r"a(\d+)", text)
    if area:
        encoded = int(area.group(1))
        if encoded <= 0:
            raise FeatureRejection("missing_identity", "osmium area id is not positive")
        if encoded % 2 == 0:
            return f"way/{encoded // 2}", source_form
        return f"relation/{(encoded - 1) // 2}", source_form
    match = re.fullmatch(r"(n|w|r)(\d+)", text)
    if match:
        kind = {"n": "node", "w": "way", "r": "relation"}[match.group(1)]
        identifier = int(match.group(2))
        if identifier <= 0:
            raise FeatureRejection("missing_identity", "OSM id is not positive")
        return f"{kind}/{identifier}", source_form
    match = re.fullmatch(r"(node|way|relation)/(\d+)", text)
    if match and int(match.group(2)) > 0:
        return f"{match.group(1)}/{int(match.group(2))}", source_form
    raise FeatureRejection("missing_identity", f"unsupported OSM identity {text!r}")


def _normalized_tags(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        raise FeatureRejection("malformed_tags", "feature properties are not an object")
    tags = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise FeatureRejection("malformed_tags", "OSM tags must map strings to strings")
        tags[key] = item
    return dict(sorted(tags.items()))


def _normalize_relation_members(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise FeatureRejection(
            "malformed_relation_members", "parking_members must be an array"
        )
    aliases = []
    for raw_alias in value:
        alias, _source = canonical_osm_alias(raw_alias)
        aliases.append(alias)
    return tuple(sorted(set(aliases)))


def _associate_feature_endpoints(
        alias: str, geometry: dict, endpoint_grid: EndpointGrid,
        metrics: collections.Counter | None = None) -> tuple[int, ...]:
    coarse_ids = endpoint_grid.candidate_ids(
        geometry,
        FALLBACK_M,
        limit=MAX_ENDPOINT_CANDIDATES_PER_FEATURE,
        context=f"parking feature {alias}",
    )
    if len(coarse_ids) > MAX_ENDPOINT_CANDIDATES_PER_FEATURE:
        raise CensusError(
            f"parking feature {alias} has {len(coarse_ids):,} coarse endpoint "
            f"associations; limit is {MAX_ENDPOINT_CANDIDATES_PER_FEATURE:,}"
        )
    if metrics is not None:
        metrics["coarse_endpoint_candidates_total"] += len(coarse_ids)
        metrics["exact_endpoint_distance_checks"] += len(coarse_ids)
        metrics["max_coarse_endpoint_candidates"] = max(
            metrics["max_coarse_endpoint_candidates"], len(coarse_ids)
        )
        if not coarse_ids:
            metrics["coarse_outside_fallback_envelope"] += 1
    near_endpoint_ids = endpoint_grid.within_candidates(
        geometry, coarse_ids, FALLBACK_M
    )
    if metrics is not None:
        metrics["endpoint_associations_total"] += len(near_endpoint_ids)
        metrics["max_endpoint_associations"] = max(
            metrics["max_endpoint_associations"], len(near_endpoint_ids)
        )
    return near_endpoint_ids


def normalize_feature(
        record: object, endpoint_grid: EndpointGrid,
        relation_memberships: dict[str, tuple[str, ...]],
        metrics: collections.Counter | None = None, *,
        associate_endpoints: bool = True) -> ParkingFeature | None:
    if not isinstance(record, dict) or record.get("type") != "Feature":
        raise FeatureRejection("malformed_feature", "record is not a GeoJSON Feature")
    properties = record.get("properties")
    if not isinstance(properties, dict):
        raise FeatureRejection("malformed_tags", "feature properties are not an object")
    amenity = properties.get("amenity")
    if amenity is None or (isinstance(amenity, str) and amenity != "parking"):
        return None
    tags = _normalized_tags(properties)
    if tags.get("amenity") != "parking":
        raise FeatureRejection(
            "malformed_tags", "potential parking amenity value is not a string"
        )
    alias, source_form = canonical_osm_alias(
        record.get("id", tags.get("@id"))
    )
    geometry = normalize_geometry(record.get("geometry"))
    latitude, longitude = geometry_representative(geometry)
    near_endpoint_ids = (
        _associate_feature_endpoints(alias, geometry, endpoint_grid, metrics)
        if associate_endpoints else ()
    )
    members = set(relation_memberships.get(alias, ()))
    members.update(_normalize_relation_members(record.get("parking_members")))
    return ParkingFeature(
        alias=alias,
        source_form=source_form,
        geometry=geometry,
        latitude=latitude,
        longitude=longitude,
        tags=tags,
        near_endpoint_ids=near_endpoint_ids,
        relation_members=tuple(sorted(members)),
    )


def _declared_non_area(tags: dict[str, str]) -> bool:
    return _normalized_declared_area(tags.get("area")) in CLOSED_NON_AREA_VALUES


def _geometry_rank(geometry_type: str, tags: dict[str, str]) -> int:
    declared_non_area = _declared_non_area(tags)
    if declared_non_area:
        # osmium 1.16 can emit both a LineString and a MultiPolygon for closed
        # ways tagged area=false/0. The source explicitly says this is not an
        # area, so preserve the line geometry rather than the derived area copy.
        return {
            "Point": 0, "Polygon": 1, "MultiPolygon": 1, "LineString": 2,
        }[geometry_type]
    return {
        "Point": 0, "LineString": 1, "Polygon": 2, "MultiPolygon": 2,
    }[geometry_type]


def _merge_duplicate_feature(
        previous: ParkingFeature, current: ParkingFeature) -> ParkingFeature:
    if previous.tags != current.tags:
        raise CensusError(
            f"canonical alias {previous.alias} has conflicting export tags"
        )
    if previous.alias != current.alias:
        raise AssertionError("duplicate merge requires one canonical alias")
    previous_rank = _geometry_rank(previous.geometry_type, previous.tags)
    current_rank = _geometry_rank(current.geometry_type, current.tags)
    if previous_rank == current_rank and previous.geometry != current.geometry:
        raise CensusError(
            f"canonical alias {previous.alias} has conflicting export geometry"
        )
    winner = current if current_rank > previous_rank else previous
    near = tuple(sorted(set(previous.near_endpoint_ids) | set(current.near_endpoint_ids)))
    members = tuple(sorted(set(previous.relation_members) | set(current.relation_members)))
    return ParkingFeature(
        alias=winner.alias,
        source_form=winner.source_form,
        geometry=winner.geometry,
        latitude=winner.latitude,
        longitude=winner.longitude,
        tags=winner.tags,
        near_endpoint_ids=near,
        relation_members=members,
    )


class FeatureAccumulator:
    def __init__(
            self, endpoint_grid: EndpointGrid,
            relation_memberships: dict[str, tuple[str, ...]],
            sidecar_aliases: Iterable[str] = ()):
        self.endpoint_grid = endpoint_grid
        self.relation_memberships = relation_memberships
        self.sidecar_alias_allow_set = frozenset(sidecar_aliases)
        self.seen_authority_aliases: set[str] = set()
        self.features: dict[str, ParkingFeature] = {}
        self.exported_forms: dict[str, set[str]] = collections.defaultdict(set)
        self.exported_source_forms: dict[str, set[str]] = collections.defaultdict(set)
        self.counters = collections.Counter()
        self.rejections = collections.Counter()
        self.metadata: dict = {}

    def consume_line(self, raw_line: bytes) -> None:
        self.counters["records_total"] += 1
        if len(raw_line) > MAX_STREAM_RECORD_BYTES:
            self.rejections["oversized_record"] += 1
            return
        payload = raw_line.strip().lstrip(b"\x1e")
        if not payload:
            self.counters["blank_records"] += 1
            return
        try:
            record = json.loads(
                payload.decode("utf-8"),
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_nonfinite,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            self.rejections["invalid_json"] += 1
            return
        if isinstance(record, dict) and set(record) == {"_meta"}:
            meta = record["_meta"]
            if not isinstance(meta, dict):
                self.rejections["malformed_meta"] += 1
                return
            normalized = {
                key: meta[key]
                for key in ("timestamp", "sequence", "stamp")
                if isinstance(meta.get(key), (str, int))
                and not isinstance(meta.get(key), bool)
            }
            if self.metadata and normalized != self.metadata:
                raise CensusError("GeoJSON sequence has conflicting metadata records")
            self.metadata = normalized
            self.counters["metadata_records"] += 1
            return
        try:
            feature = normalize_feature(
                record, self.endpoint_grid, self.relation_memberships,
                associate_endpoints=False,
            )
        except FeatureRejection as error:
            self.rejections[error.code] += 1
            return
        if feature is None:
            self.counters["ignored_nonparking"] += 1
            return
        self.counters["parking_features"] += 1
        if feature.alias in self.sidecar_alias_allow_set:
            self.seen_authority_aliases.add(feature.alias)
        if (feature.alias not in self.exported_forms
                and len(self.exported_forms) >= MAX_PARKING_IDENTITIES):
            raise CensusError(
                f"parking identity inventory exceeds {MAX_PARKING_IDENTITIES:,}; "
                f"latest identity={feature.alias}"
            )
        self.exported_forms[feature.alias].add(feature.geometry_type)
        self.exported_source_forms[feature.alias].add(feature.source_form)
        if (feature.alias.startswith("way/")
                and _declared_non_area(feature.tags)
                and feature.geometry_type in ("Polygon", "MultiPolygon")):
            # osmium 1.16 can emit a derived area copy alongside the source
            # line for area=false/0. Reconcile that form, but never let it
            # establish endpoint proximity or canonical candidate geometry.
            self.counters["ignored_non_area_area_copies"] += 1
            return
        feature.near_endpoint_ids = _associate_feature_endpoints(
            feature.alias, feature.geometry, self.endpoint_grid, self.counters
        )
        if not feature.near_endpoint_ids:
            self.counters["outside_fallback_envelope"] += 1
            return
        previous = self.features.get(feature.alias)
        if previous is not None:
            self.counters["canonical_export_duplicates"] += 1
            feature = _merge_duplicate_feature(previous, feature)
        elif len(self.features) >= MAX_RETAINED_FEATURES:
            raise CensusError(
                f"retained parking features exceed {MAX_RETAINED_FEATURES:,}; "
                f"latest identity={feature.alias}"
            )
        self.features[feature.alias] = feature

    def final_counters(self) -> dict:
        names = (
            "records_total", "blank_records", "metadata_records",
            "ignored_nonparking", "parking_features",
            "outside_fallback_envelope", "canonical_export_duplicates",
            "ignored_non_area_area_copies",
            "coarse_endpoint_candidates_total",
            "coarse_outside_fallback_envelope",
            "exact_endpoint_distance_checks", "endpoint_associations_total",
            "max_coarse_endpoint_candidates", "max_endpoint_associations",
        )
        values = {name: self.counters[name] for name in names}
        values["retained_canonical_features"] = len(self.features)
        values["seen_authority_aliases"] = len(self.seen_authority_aliases)
        values["rejected_total"] = sum(self.rejections.values())
        values["rejections"] = dict(sorted(self.rejections.items()))
        values["record_equation"] = {
            "records_total": values["records_total"],
            "classified_records": (
                values["blank_records"] + values["metadata_records"]
                + values["ignored_nonparking"] + values["parking_features"]
                + values["rejected_total"]
            ),
        }
        values["parking_equation"] = {
            "parking_features": values["parking_features"],
            "reconciled_parking_records": (
                values["outside_fallback_envelope"]
                + values["retained_canonical_features"]
                + values["canonical_export_duplicates"]
                + values["ignored_non_area_area_copies"]
            ),
        }
        if values["record_equation"]["records_total"] != values["record_equation"][
                "classified_records"]:
            raise CensusError("parking stream record accounting did not close")
        if values["parking_equation"]["parking_features"] != values[
                "parking_equation"]["reconciled_parking_records"]:
            raise CensusError("parking stream envelope accounting did not close")
        return dict(sorted(values.items()))

    def export_inventory(self) -> dict[str, dict]:
        return {
            alias: {
                "geometry_types": sorted(self.exported_forms[alias]),
                "source_forms": sorted(self.exported_source_forms[alias]),
            }
            for alias in sorted(self.exported_forms)
        }

    def release_export_inventory(self) -> None:
        """Release export-only form maps after exact reconciliation."""
        self.exported_forms.clear()
        self.exported_source_forms.clear()


def _stream_fixture(
        path: Path, endpoint_grid: EndpointGrid,
        sidecar_aliases: Iterable[str]) -> ParkingStreamResult:
    candidate = path.absolute()
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(candidate, flags)
    except OSError as error:
        raise CensusError(f"cannot securely open parking fixture: {error}") from error
    accumulator = FeatureAccumulator(endpoint_grid, {}, sidecar_aliases)
    hasher = hashlib.sha256()
    try:
        before = _signature(os.fstat(fd))
        entry_before = _signature(os.stat(candidate, follow_symlinks=False))
        if not stat.S_ISREG(before.mode) or before != entry_before:
            raise CensusError("parking fixture is not one stable regular file")
        with os.fdopen(os.dup(fd), "rb") as stream:
            for raw_line in stream:
                hasher.update(raw_line)
                accumulator.consume_line(raw_line)
        after = _signature(os.fstat(fd))
        entry_after = _signature(os.stat(candidate, follow_symlinks=False))
        if not (before == after == entry_before == entry_after):
            raise CensusError("parking fixture changed during capture")
        digest = hasher.hexdigest()
        binding = SourceBinding(
            candidate,
            _portable_label(candidate, digest, "parking-fixture"),
            digest,
            before.size,
            before,
        )
    finally:
        os.close(fd)
    return ParkingStreamResult(
        tuple(accumulator.features[key] for key in sorted(accumulator.features)),
        binding,
        accumulator.metadata,
        accumulator.final_counters(),
        {},
        tuple(sorted(accumulator.seen_authority_aliases)),
    )


def _opl_field(line: str, prefix: str) -> str | None:
    for token in line.split(" "):
        if token.startswith(prefix):
            return token[len(prefix):]
    return None


def _opl_tags(value: str | None) -> dict[str, str]:
    tags = {}
    if not value:
        return tags
    for pair in value.split(","):
        if "=" not in pair:
            continue
        key, item = pair.split("=", 1)
        tags[urllib.parse.unquote(key)] = urllib.parse.unquote(item)
    return tags


def _signal_process_group(process: subprocess.Popen, signal_number: int) -> None:
    process_id = getattr(process, "pid", None)
    if process_id is None:
        action = process.terminate if signal_number == signal.SIGTERM else process.kill
        try:
            action()
        except ProcessLookupError:
            pass
        return
    try:
        os.killpg(process_id, signal_number)
    except ProcessLookupError:
        pass
    except PermissionError:
        if process.poll() is None:
            action = (
                process.terminate
                if signal_number == signal.SIGTERM else process.kill
            )
            action()


def _process_group_exists(process: subprocess.Popen) -> bool:
    process_id = getattr(process, "pid", None)
    if process_id is None:
        return process.poll() is None
    try:
        os.killpg(process_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return process.poll() is None
    return True


def _terminate_and_reap(
        process: subprocess.Popen,
        grace_seconds: float | None = None) -> None:
    """Terminate an isolated osmium process group and always reap its leader."""
    if grace_seconds is None:
        grace_seconds = OSMIUM_TERM_GRACE_S
    _signal_process_group(process, signal.SIGTERM)
    deadline = time.monotonic() + grace_seconds
    while _process_group_exists(process) and time.monotonic() < deadline:
        try:
            process.wait(timeout=min(0.05, max(0.001, deadline - time.monotonic())))
        except subprocess.TimeoutExpired:
            pass
        if process.poll() is not None and _process_group_exists(process):
            time.sleep(0.01)
    if _process_group_exists(process):
        _signal_process_group(process, signal.SIGKILL)
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        _signal_process_group(process, signal.SIGKILL)
        try:
            process.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired as final_error:
            raise CensusError("osmium process-group leader could not be reaped") from final_error


def _validated_osmium_timeout(value: float | None = None) -> float:
    timeout = OSMIUM_TIMEOUT_S if value is None else value
    if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not math.isfinite(float(timeout)) or float(timeout) <= 0):
        raise CensusError("osmium timeout seconds must be positive and finite")
    return float(timeout)


def _run_osmium(
        command: list[str], label: str,
        timeout_s: float | None = None) -> subprocess.CompletedProcess:
    timeout = _validated_osmium_timeout(timeout_s)
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=False,
        start_new_session=True,
    )
    try:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            _terminate_and_reap(process)
            try:
                stdout, stderr = process.communicate(timeout=OSMIUM_TERM_GRACE_S)
            except subprocess.TimeoutExpired:
                stdout, stderr = "", ""
            raise CensusError(
                f"{label} timed out after {timeout:g} seconds; "
                "process group terminated and reaped"
            ) from error
    except BaseException:
        if process.poll() is None or _process_group_exists(process):
            _terminate_and_reap(process)
        raise
    finally:
        if process.poll() is not None:
            process.wait()
    completed = subprocess.CompletedProcess(
        command, process.returncode, stdout, stderr
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()[-2000:]
        raise CensusError(
            f"{label} failed ({completed.returncode}): {detail}"
        )
    return completed


def _resolve_osmium(
        executable: str | Path | None = None, *,
        expected_sha256: str | None = None,
        require_explicit_absolute: bool = False,
        timeout_s: float | None = None) -> OsmiumTool:
    if require_explicit_absolute and executable is None:
        raise CensusError("authoritative PBF mode requires explicit --osmium")
    raw_path = str(executable) if executable is not None else shutil.which("osmium")
    if not raw_path:
        raise CensusError("osmium executable was not found")
    expanded = Path(raw_path).expanduser()
    if require_explicit_absolute and not expanded.is_absolute():
        raise CensusError("authoritative --osmium path must be absolute")
    path = expanded.resolve()
    if not path.is_absolute() or not os.access(path, os.X_OK):
        raise CensusError("resolved osmium executable is not executable")
    if expected_sha256 is not None and re.fullmatch(
            r"[0-9a-f]{64}", expected_sha256) is None:
        raise CensusError("expected osmium SHA-256 must be 64 lowercase hex characters")
    binding = _capture_file_hash(path, role="osmium-executable")
    if expected_sha256 is not None and binding.sha256 != expected_sha256:
        raise CensusError(
            "osmium executable SHA-256 mismatch: "
            f"expected {expected_sha256}, observed {binding.sha256}"
        )
    completed = _run_osmium(
        [str(path), "--version"], "osmium version", timeout_s=timeout_s
    )
    version = completed.stdout.strip()
    if not version:
        raise CensusError("osmium version command returned no version")
    _verify_binding(binding)
    return OsmiumTool(binding, version)


def _stream_osmium_lines(
        command: list[str], label: str,
        consume_line: Callable[[bytes], None], *,
        timeout_s: float | None = None) -> None:
    timeout = _validated_osmium_timeout(timeout_s)
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=errors,
            shell=False,
            start_new_session=True,
        )
        assert process.stdout is not None
        timed_out = threading.Event()
        finished = threading.Event()
        termination_lock = threading.Lock()

        def terminate_group() -> None:
            with termination_lock:
                _terminate_and_reap(process)

        def watchdog() -> None:
            if not finished.wait(timeout):
                timed_out.set()
                terminate_group()

        watcher = threading.Thread(
            target=watchdog, name="osmium-timeout-watchdog", daemon=True
        )
        watcher.start()
        completed_normally = False
        try:
            for raw_line in process.stdout:
                consume_line(raw_line)
            try:
                return_code = process.wait(timeout=OSMIUM_TERM_GRACE_S)
            except subprocess.TimeoutExpired as error:
                terminate_group()
                raise CensusError(f"{label} did not reap after output closed") from error
            if timed_out.is_set():
                raise CensusError(
                    f"{label} timed out after {timeout:g} seconds; "
                    "process group terminated and reaped"
                )
            if return_code != 0:
                errors.seek(0)
                detail = errors.read().decode("utf-8", errors="replace")[-2000:]
                raise CensusError(
                    f"{label} failed ({return_code}): {detail.strip()}"
                )
            completed_normally = True
        finally:
            finished.set()
            process.stdout.close()
            if not completed_normally:
                terminate_group()
            else:
                process.wait()
            watcher.join(timeout=OSMIUM_TERM_GRACE_S + 1.0)
            if watcher.is_alive():
                raise CensusError(f"{label} timeout watchdog did not finish")


def _normalized_declared_area(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise CensusError("parking inventory declared area must be a string or null")
    return value.strip().casefold() or None


def _pbf_inventory(
        path: Path, tool: OsmiumTool, *, source_side: bool = False,
        timeout_s: float | None = None,
) -> tuple[dict[str, dict], dict[str, tuple[str, ...]]]:
    """Inventory tagged parking objects from independent osmium OPL."""
    identities: dict[str, dict] = {}
    memberships: dict[str, tuple[str, ...]] = {}

    def consume(raw_line: bytes) -> None:
        if len(raw_line) > MAX_STREAM_RECORD_BYTES:
            raise CensusError(
                "filtered osmium inventory emitted an oversized OPL record"
            )
        try:
            line = raw_line.decode("utf-8").rstrip("\n")
        except UnicodeDecodeError as error:
            raise CensusError("filtered osmium inventory is not UTF-8 OPL") from error
        identifier = line.split(" ", 1)[0]
        if not identifier or identifier[0] not in "nwr":
            return
        tags = _opl_tags(_opl_field(line, "T"))
        if tags.get("amenity") != "parking":
            return
        try:
            alias, _source = canonical_osm_alias(identifier)
        except FeatureRejection as error:
            raise CensusError(
                f"filtered parking inventory has invalid identity {identifier!r}"
            ) from error
        if alias in identities:
            raise CensusError(f"filtered parking inventory duplicates {alias}")
        if len(identities) >= MAX_PARKING_IDENTITIES:
            raise CensusError(
                f"filtered parking inventory exceeds {MAX_PARKING_IDENTITIES:,}; "
                f"latest identity={alias}"
            )
        kind = alias.split("/", 1)[0]
        refs = [
            value for value in (_opl_field(line, "N") or "").split(",")
            if value
        ]
        identities[alias] = {
            "source_type": kind,
            "closed_way": kind == "way" and len(refs) >= 4 and refs[0] == refs[-1],
            "declared_area": _normalized_declared_area(tags.get("area")),
        }
        if kind != "relation":
            return
        members = []
        for member in (_opl_field(line, "M") or "").split(","):
            raw_alias = member.split("@", 1)[0]
            if not raw_alias:
                continue
            try:
                member_alias, _source = canonical_osm_alias(raw_alias)
            except FeatureRejection as error:
                raise CensusError(
                    f"parking relation {alias} has invalid member {raw_alias!r}"
                ) from error
            members.append(member_alias)
        memberships[alias] = tuple(sorted(set(members)))

    if source_side:
        command = [
            str(tool.binding.path), "tags-filter", "-R", "-f", "opl",
            str(path), "nwr/amenity=parking",
        ]
        label = "osmium input PBF parking inventory"
    else:
        command = [str(tool.binding.path), "cat", str(path), "-f", "opl"]
        label = "osmium filtered parking inventory"
    _stream_osmium_lines(
        command, label, consume, timeout_s=timeout_s
    )
    return dict(sorted(identities.items())), dict(sorted(memberships.items()))


def _inventory_rows(inventory: dict[str, dict]) -> list[list]:
    return [
        [
            alias,
            inventory[alias]["source_type"],
            inventory[alias]["closed_way"],
            _normalized_declared_area(inventory[alias].get("declared_area")),
        ]
        for alias in sorted(inventory)
    ]


def _inventory_forms(inventory: dict[str, dict]) -> dict[str, int]:
    return dict(sorted(collections.Counter(
        value["source_type"] for value in inventory.values()
    ).items()))


def _membership_rows(memberships: dict[str, tuple[str, ...]]) -> list[list]:
    return [
        [relation, list(memberships[relation])]
        for relation in sorted(memberships)
    ]


def _reconcile_filtered_inventory(
        input_inventory: dict[str, dict], input_memberships: dict[str, tuple[str, ...]],
        filtered_inventory: dict[str, dict],
        filtered_memberships: dict[str, tuple[str, ...]]) -> dict:
    missing = sorted(set(input_inventory) - set(filtered_inventory))
    extra = sorted(set(filtered_inventory) - set(input_inventory))
    changed = sorted(
        alias for alias in set(input_inventory) & set(filtered_inventory)
        if input_inventory[alias] != filtered_inventory[alias]
    )
    if missing or extra or changed:
        raise CensusError(
            "osmium tags-filter parking identity reconciliation failed: "
            f"missing={missing[:10]}, extra={extra[:10]}, changed={changed[:10]}"
        )
    if input_memberships != filtered_memberships:
        missing_relations = sorted(set(input_memberships) - set(filtered_memberships))
        extra_relations = sorted(set(filtered_memberships) - set(input_memberships))
        changed_relations = sorted(
            relation for relation in set(input_memberships) & set(filtered_memberships)
            if input_memberships[relation] != filtered_memberships[relation]
        )
        raise CensusError(
            "osmium tags-filter parking membership reconciliation failed: "
            f"missing={missing_relations[:10]}, extra={extra_relations[:10]}, "
            f"changed={changed_relations[:10]}"
        )
    input_identity_hash = _canonical_hash(_inventory_rows(input_inventory))
    filtered_identity_hash = _canonical_hash(_inventory_rows(filtered_inventory))
    input_membership_hash = _canonical_hash(_membership_rows(input_memberships))
    filtered_membership_hash = _canonical_hash(
        _membership_rows(filtered_memberships)
    )
    return {
        "input_parking_identities": len(input_inventory),
        "filtered_parking_identities": len(filtered_inventory),
        "input_forms": _inventory_forms(input_inventory),
        "filtered_forms": _inventory_forms(filtered_inventory),
        "input_identity_sha256": input_identity_hash,
        "filtered_identity_sha256": filtered_identity_hash,
        "input_relation_memberships": len(input_memberships),
        "filtered_relation_memberships": len(filtered_memberships),
        "input_relation_memberships_sha256": input_membership_hash,
        "filtered_relation_memberships_sha256": filtered_membership_hash,
        "exact_filter_identity_reconciliation": True,
        "exact_filter_membership_reconciliation": True,
    }


def _pbf_metadata(
        path: Path, tool: OsmiumTool, *, require_replication: bool,
        timeout_s: float | None = None) -> dict:
    fields = {
        "timestamp": "header.option.timestamp",
        "replication_timestamp": "header.option.osmosis_replication_timestamp",
        "sequence": "header.option.osmosis_replication_sequence_number",
    }
    result = {}
    for name, key in fields.items():
        completed = _run_osmium(
            [str(tool.binding.path), "fileinfo", "-g", key, str(path)],
            f"osmium fileinfo {name}",
            timeout_s=timeout_s,
        )
        value = completed.stdout.strip()
        optional_replication = name in {"replication_timestamp", "sequence"}
        if not value and (require_replication or not optional_replication):
            raise CensusError(f"osmium fileinfo {name} returned no value")
        result[name] = value or None
    result["replication_headers_required"] = require_replication
    result["replication_headers_present"] = all(
        result[name] is not None for name in ("replication_timestamp", "sequence")
    )
    return result


def _reconcile_pbf_export(
        source_inventory: dict[str, dict], exported_inventory: dict[str, dict],
        *, enforce_national_floor: bool) -> dict:
    source_aliases = set(source_inventory)
    exported_aliases = set(exported_inventory)
    missing = sorted(source_aliases - exported_aliases)
    extra = sorted(exported_aliases - source_aliases)
    if missing or extra:
        raise CensusError(
            "PBF parking export identity reconciliation failed: "
            f"missing={missing[:10]}, extra={extra[:10]}"
        )
    unsupported = []
    source_forms = collections.Counter()
    export_forms = collections.Counter()
    for alias in sorted(source_inventory):
        source_type = source_inventory[alias]["source_type"]
        source_forms[source_type] += 1
        geometry_types = set(exported_inventory[alias]["geometry_types"])
        for geometry_type in geometry_types:
            export_forms[geometry_type] += 1
        allowed = {
            "node": {"Point"},
            "way": {"LineString", "Polygon", "MultiPolygon"},
            "relation": {"Polygon", "MultiPolygon"},
        }[source_type]
        if not geometry_types or not geometry_types.issubset(allowed):
            unsupported.append((alias, sorted(geometry_types)))
            continue
        if source_type == "way":
            closed_way = source_inventory[alias]["closed_way"]
            declared_non_area = _normalized_declared_area(
                source_inventory[alias].get("declared_area")
            ) in CLOSED_NON_AREA_VALUES
            if not closed_way or declared_non_area:
                required_forms = {"LineString"}
            else:
                required_forms = {"Polygon", "MultiPolygon"}
            if geometry_types.isdisjoint(required_forms):
                unsupported.append((alias, sorted(geometry_types)))
    if unsupported:
        raise CensusError(
            "PBF parking export has unsupported or under-answered forms: "
            f"{unsupported[:10]}"
        )
    identity_count = len(source_inventory)
    if enforce_national_floor and identity_count <= MIN_NATIONAL_PBF_PARKING_IDENTITIES:
        raise CensusError(
            f"national PBF parking inventory has {identity_count:,} identities; "
            f"must exceed {MIN_NATIONAL_PBF_PARKING_IDENTITIES:,}"
        )
    identity_rows = _inventory_rows(source_inventory)
    export_rows = [
        [alias, exported_inventory[alias]["geometry_types"],
         exported_inventory[alias]["source_forms"]]
        for alias in sorted(exported_inventory)
    ]
    return {
        "source_parking_identities": identity_count,
        "input_parking_identities": identity_count,
        "exported_parking_identities": len(exported_inventory),
        "source_forms": dict(sorted(source_forms.items())),
        "input_forms": dict(sorted(source_forms.items())),
        "export_geometry_forms": dict(sorted(export_forms.items())),
        "way_geometry_policy": {
            "open": "LineString-required",
            "closed_default": "Polygon-or-MultiPolygon-required",
            "closed_non_area": "LineString-required-area-copies-ignored-before-endpoint-association",
            "closed_non_area_values": sorted(CLOSED_NON_AREA_VALUES),
        },
        "identity_sha256": _canonical_hash(identity_rows),
        "input_identity_sha256": _canonical_hash(identity_rows),
        "export_inventory_sha256": _canonical_hash(export_rows),
        "exact_identity_reconciliation": True,
        "national_floor_enforced": enforce_national_floor,
        "national_floor_exclusive_minimum": (
            MIN_NATIONAL_PBF_PARKING_IDENTITIES
            if enforce_national_floor else None
        ),
    }


def _operator_artifact_directory(path: str | Path | None) -> Path:
    if path is None:
        raise CensusError("PBF mode requires an operator-owned artifact directory")
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        raise CensusError("PBF artifact directory must be absolute")
    candidate = Path(os.path.abspath(str(candidate)))
    try:
        value = os.stat(candidate, follow_symlinks=False)
    except OSError as error:
        raise CensusError(f"cannot inspect PBF artifact directory: {error}") from error
    if not stat.S_ISDIR(value.st_mode) or value.st_uid != os.geteuid():
        raise CensusError("PBF artifact directory must be an operator-owned directory")
    if stat.S_IMODE(value.st_mode) != 0o700:
        raise CensusError("PBF artifact directory mode must be exactly 0700")
    return candidate


def _assert_artifact_free_space(path: Path, minimum_free_bytes: int) -> int:
    if (isinstance(minimum_free_bytes, bool)
            or not isinstance(minimum_free_bytes, int)
            or minimum_free_bytes < 0):
        raise CensusError("minimum PBF artifact free bytes must be nonnegative")
    free = shutil.disk_usage(path).free
    if free < minimum_free_bytes:
        raise CensusError(
            f"PBF artifact directory has {free:,} free bytes; requires at least "
            f"{minimum_free_bytes:,}"
        )
    return free


def _self_hashed_document(payload: dict) -> dict:
    document = dict(payload)
    document["self_sha256"] = _canonical_hash(document)
    return document


def _filtered_pbf_policy() -> dict:
    return {
        "expression": FILTER_EXPRESSION,
        "input_inventory_command": "tags-filter -R -f opl",
        "filtered_inventory_command": "cat -f opl",
        "filter_command": "tags-filter INPUT nwr/amenity=parking -o OUTPUT",
        "export_command": (
            "export INPUT -f geojsonseq "
            "--geometry-types=point,linestring,polygon "
            "--add-unique-id=type_id"
        ),
        "closed_way_non_area_policy": (
            "LineString-required-area-copies-ignored-before-endpoint-association"
        ),
        "closed_way_non_area_values": sorted(CLOSED_NON_AREA_VALUES),
    }


def _source_inventory_provenance(
        inventory: dict[str, dict],
        memberships: dict[str, tuple[str, ...]]) -> dict:
    return {
        "input_parking_identities": len(inventory),
        "input_forms": _inventory_forms(inventory),
        "input_identity_sha256": _canonical_hash(_inventory_rows(inventory)),
        "input_relation_memberships": len(memberships),
        "input_relation_memberships_sha256": _canonical_hash(
            _membership_rows(memberships)
        ),
    }


def _artifact_inventory(inventory: dict) -> dict:
    run_policy_keys = {
        "national_floor_enforced", "national_floor_exclusive_minimum",
        "artifact_minimum_free_bytes",
    }
    return {
        key: value for key, value in inventory.items()
        if key not in run_policy_keys
    }


def _filtered_artifact_payload(
        source: SourceBinding, tool: OsmiumTool,
        filtered: SourceBinding, inventory: dict) -> dict:
    return {
        "schema": FILTERED_PBF_ARTIFACT_SCHEMA,
        "self_hash_algorithm": PRODUCTION_BASELINE_SELF_HASH,
        "source_pbf": {
            "sha256": source.sha256,
            "size_bytes": source.size_bytes,
        },
        "osmium": {
            "sha256": tool.binding.sha256,
            "size_bytes": tool.binding.size_bytes,
            "version": tool.version,
        },
        "filter": _filtered_pbf_policy(),
        "filtered_pbf": {
            "sha256": filtered.sha256,
            "size_bytes": filtered.size_bytes,
        },
        "inventory": _artifact_inventory(inventory),
    }


def _require_private_artifact_directory(path: Path) -> FileSignature:
    try:
        value = os.stat(path, follow_symlinks=False)
    except OSError as error:
        raise CensusError(
            f"cannot inspect existing filtered artifact {path}: {error}"
        ) from error
    signature = _signature(value)
    if (not stat.S_ISDIR(signature.mode)
            or signature.uid != os.geteuid()
            or stat.S_IMODE(signature.mode) != 0o700):
        raise CensusError(
            f"existing filtered artifact {path} must be an operator-owned "
            "no-follow directory with mode 0700"
        )
    return signature


def _require_private_artifact_file(
        binding: SourceBinding, artifact_path: Path) -> None:
    signature = binding.signature
    if (not stat.S_ISREG(signature.mode)
            or signature.uid != os.geteuid()
            or stat.S_IMODE(signature.mode) != 0o600
            or signature.links != 1):
        raise CensusError(
            f"existing filtered artifact file {artifact_path} must be an "
            "operator-owned no-follow regular file with mode 0600 and one link"
        )


def _read_filtered_artifact_manifest(
        artifact_path: Path, *,
        require_current_filter_policy: bool = True,
) -> tuple[dict, SourceBinding]:
    before = _require_private_artifact_directory(artifact_path)
    try:
        entries = sorted(os.listdir(artifact_path))
    except OSError as error:
        raise CensusError(
            f"cannot list existing filtered artifact {artifact_path}: {error}"
        ) from error
    if entries != ["artifact-manifest.json", "parking.pbf"]:
        raise CensusError(
            f"existing filtered artifact {artifact_path} has unexpected entries "
            f"{entries}"
        )
    raw, manifest_binding = _capture_file_bytes(
        artifact_path / "artifact-manifest.json",
        role="filtered-parking-pbf-manifest",
    )
    _require_private_artifact_file(
        manifest_binding, artifact_path / "artifact-manifest.json"
    )
    document = _strict_json_bytes(raw, "filtered PBF artifact manifest")
    root = _exact_keys(document, {
        "schema", "self_hash_algorithm", "source_pbf", "osmium", "filter",
        "filtered_pbf", "inventory", "self_sha256",
    }, "filtered PBF artifact manifest")
    if raw != _canonical_bytes(root):
        raise CensusError(
            f"existing filtered artifact {artifact_path} manifest is not "
            "canonical JSON"
        )
    if root["schema"] != FILTERED_PBF_ARTIFACT_SCHEMA:
        raise CensusError(
            f"existing filtered artifact {artifact_path} schema is unsupported"
        )
    if root["self_hash_algorithm"] != PRODUCTION_BASELINE_SELF_HASH:
        raise CensusError(
            f"existing filtered artifact {artifact_path} self-hash policy is wrong"
        )
    supplied_self = _baseline_hash(
        root["self_sha256"], "filtered PBF artifact self_sha256"
    )
    payload = dict(root)
    payload.pop("self_sha256")
    if supplied_self != _canonical_hash(payload):
        raise CensusError(
            f"existing filtered artifact {artifact_path} self-hash mismatch"
        )
    source = _exact_keys(
        root["source_pbf"], {"sha256", "size_bytes"},
        "filtered PBF artifact source",
    )
    osmium = _exact_keys(
        root["osmium"], {"sha256", "size_bytes", "version"},
        "filtered PBF artifact osmium",
    )
    filtered = _exact_keys(
        root["filtered_pbf"], {"sha256", "size_bytes"},
        "filtered PBF artifact bytes",
    )
    for label, value in (
            ("source sha256", source["sha256"]),
            ("osmium sha256", osmium["sha256"]),
            ("filtered sha256", filtered["sha256"])):
        _baseline_hash(value, f"filtered PBF artifact {label}")
    for label, value in (
            ("source size_bytes", source["size_bytes"]),
            ("osmium size_bytes", osmium["size_bytes"]),
            ("filtered size_bytes", filtered["size_bytes"])):
        _baseline_count(value, f"filtered PBF artifact {label}")
    if not isinstance(osmium["version"], str) or not osmium["version"]:
        raise CensusError(
            f"existing filtered artifact {artifact_path} has no osmium version"
        )
    if (require_current_filter_policy
            and root["filter"] != _filtered_pbf_policy()):
        raise CensusError(
            f"existing filtered artifact {artifact_path} filter provenance "
            "does not match current policy"
        )
    if not isinstance(root["inventory"], dict):
        raise CensusError(
            f"existing filtered artifact {artifact_path} inventory is not an object"
        )
    expected_name = f"filtered-pbf-sha256-{filtered['sha256']}"
    if artifact_path.name != expected_name:
        raise CensusError(
            f"existing filtered artifact {artifact_path} name does not match "
            "its filtered digest"
        )
    try:
        parking_stat = _signature(os.stat(
            artifact_path / "parking.pbf", follow_symlinks=False
        ))
    except OSError as error:
        raise CensusError(
            f"cannot inspect existing filtered artifact {artifact_path}/parking.pbf"
        ) from error
    if (not stat.S_ISREG(parking_stat.mode)
            or parking_stat.uid != os.geteuid()
            or stat.S_IMODE(parking_stat.mode) != 0o600
            or parking_stat.links != 1):
        raise CensusError(
            f"existing filtered artifact file {artifact_path}/parking.pbf must "
            "be an operator-owned no-follow regular file with mode 0600 and one link"
        )
    after = _require_private_artifact_directory(artifact_path)
    if before != after:
        raise CensusError(
            f"existing filtered artifact {artifact_path} changed during inspection"
        )
    return root, manifest_binding


def _verify_filtered_artifact(
        artifact_path: Path, *,
        expected_document: dict | None = None) -> FilteredArtifact:
    document, manifest_binding = _read_filtered_artifact_manifest(artifact_path)
    filtered_binding = _capture_file_hash(
        artifact_path / "parking.pbf", role="filtered-parking-pbf"
    )
    _require_private_artifact_file(
        filtered_binding, artifact_path / "parking.pbf"
    )
    expected_filtered = document["filtered_pbf"]
    if (filtered_binding.sha256 != expected_filtered["sha256"]
            or filtered_binding.size_bytes != expected_filtered["size_bytes"]):
        raise CensusError(
            f"existing filtered artifact {artifact_path} byte hash/length mismatch"
        )
    if expected_document is not None and document != expected_document:
        raise CensusError(
            f"existing filtered artifact {artifact_path} has the same filtered "
            "digest but mismatched source/osmium/filter/inventory provenance"
        )
    _verify_binding(manifest_binding)
    _verify_binding(filtered_binding)
    return FilteredArtifact(
        artifact_path, document, filtered_binding, manifest_binding
    )


def _artifact_matches_source(
        document: dict, source: SourceBinding, tool: OsmiumTool,
        input_inventory: dict[str, dict],
        input_memberships: dict[str, tuple[str, ...]]) -> bool:
    if document["filter"] != _filtered_pbf_policy():
        return False
    if document["source_pbf"] != {
            "sha256": source.sha256, "size_bytes": source.size_bytes}:
        return False
    if document["osmium"] != {
            "sha256": tool.binding.sha256,
            "size_bytes": tool.binding.size_bytes,
            "version": tool.version,
    }:
        return False
    expected_inventory = _source_inventory_provenance(
        input_inventory, input_memberships
    )
    return all(
        document["inventory"].get(key) == value
        for key, value in expected_inventory.items()
    )


def _find_reusable_filtered_artifact(
        artifact_root: Path, source: SourceBinding, tool: OsmiumTool,
        input_inventory: dict[str, dict],
        input_memberships: dict[str, tuple[str, ...]]) -> FilteredArtifact | None:
    matches = []
    for name in sorted(os.listdir(artifact_root)):
        if re.fullmatch(r"filtered-pbf-sha256-[0-9a-f]{64}", name) is None:
            continue
        artifact_path = artifact_root / name
        document, _manifest_binding = _read_filtered_artifact_manifest(
            artifact_path, require_current_filter_policy=False
        )
        if _artifact_matches_source(
                document, source, tool, input_inventory, input_memberships):
            matches.append(artifact_path)
    if len(matches) > 1:
        raise CensusError(
            "multiple existing filtered artifacts claim identical exact "
            "source/osmium/filter/inventory provenance: "
            + ", ".join(str(path) for path in matches)
        )
    if not matches:
        return None
    return _verify_filtered_artifact(matches[0])


def _process_filtered_pbf(
        filtered: SourceBinding, endpoint_grid: EndpointGrid,
        tool: OsmiumTool, input_inventory: dict[str, dict],
        input_memberships: dict[str, tuple[str, ...]],
        sidecar_aliases: Iterable[str], *,
        enforce_national_floor: bool,
        minimum_artifact_free_bytes: int,
        timeout_s: float) -> tuple[FeatureAccumulator, dict, dict]:
    filtered_inventory, filtered_memberships = _pbf_inventory(
        filtered.path, tool, timeout_s=timeout_s
    )
    filter_reconciliation = _reconcile_filtered_inventory(
        input_inventory, input_memberships,
        filtered_inventory, filtered_memberships,
    )
    del filtered_inventory, filtered_memberships
    accumulator = FeatureAccumulator(
        endpoint_grid, input_memberships, sidecar_aliases
    )
    _stream_osmium_lines(
        [
            str(tool.binding.path), "export", str(filtered.path),
            "-f", "geojsonseq",
            "--geometry-types=point,linestring,polygon",
            "--add-unique-id=type_id",
        ],
        "osmium filtered parking export",
        accumulator.consume_line,
        timeout_s=timeout_s,
    )
    counters = accumulator.final_counters()
    if counters["rejected_total"]:
        raise CensusError(
            "PBF parking export rejected malformed records before identity "
            f"reconciliation: {counters['rejections']}"
        )
    exported_inventory = accumulator.export_inventory()
    try:
        export_reconciliation = _reconcile_pbf_export(
            input_inventory,
            exported_inventory,
            enforce_national_floor=enforce_national_floor,
        )
    finally:
        accumulator.release_export_inventory()
        del exported_inventory
    inventory = {**filter_reconciliation, **export_reconciliation}
    inventory["parking_relation_memberships"] = len(input_memberships)
    inventory["relation_memberships_sha256"] = _canonical_hash(
        _membership_rows(input_memberships)
    )
    inventory["filtered_pbf_sha256"] = filtered.sha256
    inventory["filtered_pbf_size_bytes"] = filtered.size_bytes
    inventory["artifact_minimum_free_bytes"] = minimum_artifact_free_bytes
    return accumulator, counters, inventory


def _stream_pbf(
        path: Path, endpoint_grid: EndpointGrid, *,
        sidecar_aliases: Iterable[str] = (),
        artifact_dir: str | Path | None,
        enforce_national_floor: bool = True,
        osmium_executable: str | Path | None = None,
        expected_osmium_sha256: str | None = None,
        authoritative: bool = False,
        minimum_artifact_free_bytes: int = MIN_PBF_ARTIFACT_FREE_BYTES,
        osmium_timeout_s: float | None = None,
) -> ParkingStreamResult:
    timeout = _validated_osmium_timeout(osmium_timeout_s)
    artifact_root = _operator_artifact_directory(artifact_dir)
    tool = _resolve_osmium(
        osmium_executable,
        expected_sha256=expected_osmium_sha256,
        require_explicit_absolute=authoritative,
        timeout_s=timeout,
    )
    binding = _capture_file_hash(path, role="parking-pbf")
    metadata = _pbf_metadata(
        path, tool, require_replication=authoritative, timeout_s=timeout
    )
    input_inventory, input_memberships = _pbf_inventory(
        path, tool, source_side=True, timeout_s=timeout
    )
    reusable = _find_reusable_filtered_artifact(
        artifact_root, binding, tool, input_inventory, input_memberships
    )
    if reusable is not None:
        accumulator, counters, inventory = _process_filtered_pbf(
            reusable.filtered_binding,
            endpoint_grid,
            tool,
            input_inventory,
            input_memberships,
            sidecar_aliases,
            enforce_national_floor=enforce_national_floor,
            minimum_artifact_free_bytes=minimum_artifact_free_bytes,
            timeout_s=timeout,
        )
        expected_document = _self_hashed_document(_filtered_artifact_payload(
            binding, tool, reusable.filtered_binding, inventory
        ))
        if reusable.document != expected_document:
            raise CensusError(
                f"existing filtered artifact {reusable.path} has mismatched "
                "source/osmium/filter/inventory provenance after exact "
                "reconciliation"
            )
        filtered_binding = reusable.filtered_binding
        filtered_manifest_binding = reusable.manifest_binding
    else:
        _assert_artifact_free_space(
            artifact_root, minimum_artifact_free_bytes
        )
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        stage = Path(tempfile.mkdtemp(
            prefix=f".parking-filter-stage-{timestamp}-", dir=artifact_root
        ))
        os.chmod(stage, 0o700)
        promoted = False
        destination = None
        try:
            filtered = stage / "parking.pbf"
            _run_osmium([
                str(tool.binding.path), "tags-filter", str(path),
                FILTER_EXPRESSION, "-o", str(filtered),
            ], "osmium parking tag filter", timeout_s=timeout)
            _fsync_existing_file(filtered)
            _verify_binding(binding)
            filtered_stage_binding = _capture_file_hash(
                filtered, role="filtered-parking-pbf"
            )
            _require_private_artifact_file(filtered_stage_binding, filtered)
            accumulator, counters, inventory = _process_filtered_pbf(
                filtered_stage_binding,
                endpoint_grid,
                tool,
                input_inventory,
                input_memberships,
                sidecar_aliases,
                enforce_national_floor=enforce_national_floor,
                minimum_artifact_free_bytes=minimum_artifact_free_bytes,
                timeout_s=timeout,
            )
            artifact_document = _self_hashed_document(
                _filtered_artifact_payload(
                    binding, tool, filtered_stage_binding, inventory
                )
            )
            artifact_bytes = _canonical_bytes(artifact_document)
            artifact_path = stage / "artifact-manifest.json"
            _exclusive_write(artifact_path, artifact_bytes)
            _verify_staged_file(artifact_path, artifact_bytes)
            _fsync_directory(stage)
            destination = artifact_root / (
                f"filtered-pbf-sha256-{filtered_stage_binding.sha256}"
            )
            try:
                _rename_stage_exclusive(stage, destination)
            except ArtifactDestinationExists:
                installed = _verify_filtered_artifact(
                    destination, expected_document=artifact_document
                )
            else:
                promoted = True
                installed = _verify_filtered_artifact(
                    destination, expected_document=artifact_document
                )
            _fsync_directory(artifact_root)
            filtered_binding = installed.filtered_binding
            filtered_manifest_binding = installed.manifest_binding
        except Exception as error:
            if promoted and destination is not None:
                raise CensusError(
                    "filtered PBF was content-address installed but durability or "
                    f"verification failed; preserve and recover/archive "
                    f"{destination.name}: {error}"
                ) from error
            raise CensusError(
                "PBF processing failed; diagnostic stage preserved for operator "
                f"archive as {stage.name}: {error}"
            ) from error
    del input_inventory
    _verify_binding(binding)
    _verify_binding(tool.binding)
    _verify_binding(filtered_binding)
    _verify_binding(filtered_manifest_binding)
    return ParkingStreamResult(
        tuple(accumulator.features[key] for key in sorted(accumulator.features)),
        binding,
        metadata,
        counters,
        input_memberships,
        tuple(sorted(accumulator.seen_authority_aliases)),
        inventory,
        tool.binding,
        {
            "version": tool.version,
            "expected_sha256": expected_osmium_sha256,
            "digest_authorized": (
                expected_osmium_sha256 is not None
                and expected_osmium_sha256 == tool.binding.sha256
            ),
        },
        filtered_binding,
        filtered_manifest_binding,
    )


def stream_parking_source(
        path: str | Path, kind: str, endpoint_grid: EndpointGrid, *,
        sidecar_aliases: Iterable[str] = (),
        pbf_artifact_dir: str | Path | None = None,
        enforce_national_pbf_floor: bool = True,
        osmium_executable: str | Path | None = None,
        expected_osmium_sha256: str | None = None,
        authoritative: bool = False,
        minimum_artifact_free_bytes: int = MIN_PBF_ARTIFACT_FREE_BYTES,
        osmium_timeout_s: float | None = None,
) -> ParkingStreamResult:
    candidate = Path(path).absolute()
    if kind == "geojsonseq":
        return _stream_fixture(candidate, endpoint_grid, sidecar_aliases)
    if kind == "pbf":
        return _stream_pbf(
            candidate,
            endpoint_grid,
            sidecar_aliases=sidecar_aliases,
            artifact_dir=pbf_artifact_dir,
            enforce_national_floor=enforce_national_pbf_floor,
            osmium_executable=osmium_executable,
            expected_osmium_sha256=expected_osmium_sha256,
            authoritative=authoritative,
            minimum_artifact_free_bytes=minimum_artifact_free_bytes,
            osmium_timeout_s=osmium_timeout_s,
        )
    raise CensusError(f"unsupported parking source kind {kind!r}")


class UnionFind:
    def __init__(self, size: int, sidecar_keys: list[set[str]] | None = None):
        self.parent = list(range(size))
        self.sidecar_keys = sidecar_keys or [set() for _ in range(size)]

    def find(self, value: int) -> int:
        root = value
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[value] != value:
            next_value = self.parent[value]
            self.parent[value] = root
            value = next_value
        return root

    def union(self, first: int, second: int, reason: str,
              reject_sidecar_bridge: bool = True) -> bool:
        first_root = self.find(first)
        second_root = self.find(second)
        if first_root == second_root:
            return False
        combined = self.sidecar_keys[first_root] | self.sidecar_keys[second_root]
        if reject_sidecar_bridge and len(combined) > 1:
            raise CensusError(
                f"{reason} would bridge distinct verdict clusters {sorted(combined)}"
            )
        if first_root > second_root:
            first_root, second_root = second_root, first_root
        self.parent[second_root] = first_root
        self.sidecar_keys[first_root] = combined
        return True


def _feature_conflicts(first: ParkingFeature, second: ParkingFeature) -> bool:
    for key in IDENTITY_CONFLICT_TAGS:
        first_value = (first.tags.get(key) or "").strip().casefold()
        second_value = (second.tags.get(key) or "").strip().casefold()
        if first_value and second_value and first_value != second_value:
            return True
    return False


def _rectangle_bounds(geometry: dict) -> tuple[float, float, float, float] | None:
    if geometry["type"] != "Polygon" or len(geometry["coordinates"]) != 1:
        return None
    ring = geometry["coordinates"][0]
    longitudes = {_unwrapped_longitude(point[0], ring[0][0]) for point in ring[:-1]}
    latitudes = {point[1] for point in ring[:-1]}
    if len(longitudes) != 2 or len(latitudes) != 2:
        return None
    if any(
            _unwrapped_longitude(point[0], ring[0][0]) not in longitudes
            or point[1] not in latitudes
            for point in ring[:-1]):
        return None
    return min(longitudes), min(latitudes), max(longitudes), max(latitudes)


def _unwrap_geometry(geometry: dict, anchor: float) -> dict:
    def point(value):
        return [_unwrapped_longitude(value[0], anchor), value[1]]
    if geometry["type"] == "Point":
        coordinates = point(geometry["coordinates"])
    elif geometry["type"] == "LineString":
        coordinates = [point(value) for value in geometry["coordinates"]]
    elif geometry["type"] == "Polygon":
        coordinates = [
            [point(value) for value in ring]
            for ring in geometry["coordinates"]
        ]
    else:
        coordinates = [[
            [point(value) for value in ring]
            for ring in polygon
        ] for polygon in geometry["coordinates"]]
    return {"type": geometry["type"], "coordinates": coordinates}


def polygon_overlap_ratio(first: dict, second: dict) -> float:
    """Intersection area divided by the smaller polygon area.

    Axis-aligned rectangles stay pure-stdlib for focused tests. General
    production footprints use the repository's normal Shapely dependency only
    when their bounding boxes actually intersect.
    """
    first_rectangle = _rectangle_bounds(first)
    second_rectangle = _rectangle_bounds(second)
    if first_rectangle is not None and second_rectangle is not None:
        anchor = first["coordinates"][0][0][0]
        second_ring = second["coordinates"][0]
        second_rectangle = (
            min(_unwrapped_longitude(point[0], anchor) for point in second_ring[:-1]),
            min(point[1] for point in second_ring[:-1]),
            max(_unwrapped_longitude(point[0], anchor) for point in second_ring[:-1]),
            max(point[1] for point in second_ring[:-1]),
        )
        first_ring = first["coordinates"][0]
        first_rectangle = (
            min(_unwrapped_longitude(point[0], anchor) for point in first_ring[:-1]),
            min(point[1] for point in first_ring[:-1]),
            max(_unwrapped_longitude(point[0], anchor) for point in first_ring[:-1]),
            max(point[1] for point in first_ring[:-1]),
        )
        west = max(first_rectangle[0], second_rectangle[0])
        south = max(first_rectangle[1], second_rectangle[1])
        east = min(first_rectangle[2], second_rectangle[2])
        north = min(first_rectangle[3], second_rectangle[3])
        intersection = max(0.0, east - west) * max(0.0, north - south)
        first_area = (first_rectangle[2] - first_rectangle[0]) * (
            first_rectangle[3] - first_rectangle[1]
        )
        second_area = (second_rectangle[2] - second_rectangle[0]) * (
            second_rectangle[3] - second_rectangle[1]
        )
        return intersection / min(first_area, second_area)
    shape, _explain_validity = _require_shapely()
    anchor = next(_iter_vertices(first))[1]
    first_shape = shape(_unwrap_geometry(first, anchor))
    second_shape = shape(_unwrap_geometry(second, anchor))
    if not first_shape.is_valid or not second_shape.is_valid:
        raise CensusError("parking polygon is topologically invalid")
    smaller = min(first_shape.area, second_shape.area)
    if smaller <= 0:
        raise CensusError("parking polygon has zero area")
    return first_shape.intersection(second_shape).area / smaller


def _overlapping_polygon_pairs(features: tuple[ParkingFeature, ...]) -> Iterator[tuple[int, int]]:
    records = []
    for index, feature in enumerate(features):
        if feature.geometry_type not in ("Polygon", "MultiPolygon"):
            continue
        minimum_latitude, maximum_latitude, start, span = geometry_bounds(feature.geometry)
        for shift in (-360.0, 0.0, 360.0):
            records.append((start + shift, start + span + shift,
                            minimum_latitude, maximum_latitude, index))
    records.sort()
    active = []
    yielded = set()
    for west, east, south, north, index in records:
        active = [record for record in active if record[1] >= west]
        for _other_west, other_east, other_south, other_north, other_index in active:
            if index == other_index or other_north < south or north < other_south:
                continue
            pair = tuple(sorted((index, other_index)))
            if pair not in yielded:
                yielded.add(pair)
                if len(yielded) > MAX_SPATIAL_PAIRS:
                    raise CensusError(
                        "polygon overlap candidate pairs exceed the "
                        f"{MAX_SPATIAL_PAIRS:,}-pair resource limit; "
                        f"latest aliases={features[pair[0]].alias},"
                        f"{features[pair[1]].alias}"
                    )
                yield pair
        active.append((west, east, south, north, index))


class SpherePointGrid:
    def __init__(self, radius_m: float):
        self.cell = 2.0 * math.sin(radius_m / EARTH_RADIUS_M / 2.0)
        self.rows: dict[tuple[int, int, int], list[int]] = {}

    @staticmethod
    def _xyz(latitude: float, longitude: float) -> tuple[float, float, float]:
        latitude_radians = math.radians(latitude)
        longitude_radians = math.radians(longitude)
        cosine = math.cos(latitude_radians)
        return (
            cosine * math.cos(longitude_radians),
            cosine * math.sin(longitude_radians),
            math.sin(latitude_radians),
        )

    def _cell(self, latitude: float, longitude: float) -> tuple[int, int, int]:
        return tuple(
            math.floor(value / self.cell)
            for value in self._xyz(latitude, longitude)
        )

    def add(self, index: int, latitude: float, longitude: float) -> None:
        self.rows.setdefault(self._cell(latitude, longitude), []).append(index)

    def neighbours(self, latitude: float, longitude: float) -> Iterator[int]:
        cell = self._cell(latitude, longitude)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    yield from self.rows.get(
                        (cell[0] + dx, cell[1] + dy, cell[2] + dz), ()
                    )


def candidate_id_for_aliases(aliases: Iterable[str]) -> str:
    ordered = tuple(sorted(aliases))
    if not ordered:
        raise CensusError("OSM candidate identity needs at least one alias")
    return "pc1:" + hashlib.sha256("\0".join(ordered).encode("utf-8")).hexdigest()


def _sidecar_alias_owners(
        verdicts: parking_verdicts.Verdicts) -> dict[str, str]:
    owners = {}
    for key, entry in verdicts.entries.items():
        for alias in entry.get("osm") or (key,):
            previous = owners.setdefault(alias, key)
            if previous != key:
                raise CensusError(
                    f"sidecar OSM alias {alias!r} maps to conflicting entries "
                    f"{previous!r} and {key!r}"
                )
    return owners


def build_components(
        features: tuple[ParkingFeature, ...],
        verdicts: parking_verdicts.Verdicts) -> tuple[tuple[Component, ...], dict]:
    """Resolve only canonical must-link identity edges.

    Forty-metre proximity is deliberately absent here; it is computed later as
    a review-only grouping over census candidates.
    """
    by_alias = {feature.alias: index for index, feature in enumerate(features)}
    alias_to_sidecar = _sidecar_alias_owners(verdicts)
    labels = [
        {alias_to_sidecar[feature.alias]} if feature.alias in alias_to_sidecar else set()
        for feature in features
    ]
    union = UnionFind(len(features), labels)
    edge_counts = collections.Counter()

    by_sidecar: dict[str, list[int]] = collections.defaultdict(list)
    for index, keys in enumerate(labels):
        for key in keys:
            by_sidecar[key].append(index)
    for key in sorted(by_sidecar):
        indices = sorted(by_sidecar[key])
        for index in indices[1:]:
            if union.union(indices[0], index, "one verdict alias cluster"):
                edge_counts["verdict_alias_cluster"] += 1

    for relation_index, relation in enumerate(features):
        if not relation.alias.startswith("relation/"):
            continue
        for member_alias in relation.relation_members:
            member_index = by_alias.get(member_alias)
            if member_index is None:
                continue
            member = features[member_index]
            if member.tags.get("amenity") != "parking":
                continue
            if union.union(
                    relation_index, member_index,
                    f"parking relation/member edge {relation.alias}->{member.alias}"):
                edge_counts["parking_relation_member"] += 1

    for first_index, second_index in _overlapping_polygon_pairs(features):
        first = features[first_index]
        second = features[second_index]
        if polygon_overlap_ratio(first.geometry, second.geometry) >= OVERLAP_RATIO:
            if union.union(
                    first_index, second_index,
                    f"98-percent polygon overlap {first.alias}<->{second.alias}"):
                edge_counts["polygon_overlap"] += 1

    polygon_centres = SpherePointGrid(POINT_POLYGON_CENTRE_M)
    polygon_indices = []
    for index, feature in enumerate(features):
        if feature.geometry_type in ("Polygon", "MultiPolygon"):
            latitude, longitude = geometry_bbox_center(feature.geometry)
            polygon_centres.add(index, latitude, longitude)
            polygon_indices.append(index)
    for point_index, point in enumerate(features):
        if point.geometry_type != "Point":
            continue
        seen = set()
        for polygon_index in polygon_centres.neighbours(point.latitude, point.longitude):
            if polygon_index in seen:
                continue
            seen.add(polygon_index)
            polygon = features[polygon_index]
            centre_latitude, centre_longitude = geometry_bbox_center(polygon.geometry)
            if haversine_m(
                    point.latitude, point.longitude,
                    centre_latitude, centre_longitude) > POINT_POLYGON_CENTRE_M:
                continue
            if not point_in_geometry(point.latitude, point.longitude, polygon.geometry):
                continue
            if _feature_conflicts(point, polygon):
                continue
            if union.union(
                    point_index, polygon_index,
                    f"unambiguous point-in-polygon {point.alias}<->{polygon.alias}"):
                edge_counts["point_inside_polygon"] += 1

    groups: dict[int, list[ParkingFeature]] = collections.defaultdict(list)
    for index, feature in enumerate(features):
        groups[union.find(index)].append(feature)
    components = []
    for members in groups.values():
        ordered = tuple(sorted(members, key=lambda feature: feature.alias))
        aliases = tuple(feature.alias for feature in ordered)
        candidate_id = candidate_id_for_aliases(aliases)
        near_endpoint_ids = tuple(sorted({
            endpoint_id
            for member in ordered
            for endpoint_id in member.near_endpoint_ids
        }))
        components.append(Component(candidate_id, aliases, ordered, near_endpoint_ids))
    components.sort(key=lambda component: component.candidate_id)
    return tuple(components), dict(sorted(edge_counts.items()))


def component_distance_to_point_m(
        component: Component, latitude: float, longitude: float) -> float:
    return min(
        geometry_distance_to_point_m(member.geometry, latitude, longitude)
        for member in component.members
    )


def component_distance_m(first: Component, second: Component) -> float:
    return min(
        geometry_distance_m(first_member.geometry, second_member.geometry)
        for first_member in first.members
        for second_member in second.members
    )


def component_trail_distance_m(
        component: Component, trail: Trail,
        endpoints: tuple[Endpoint, ...]) -> float:
    return min(
        component_distance_to_point_m(
            component,
            endpoints[endpoint_id].latitude,
            endpoints[endpoint_id].longitude,
        )
        for endpoint_id in trail.endpoint_ids
    )


def live_candidate_id(pin: dict) -> str:
    return "pl1:" + hashlib.sha256(pin["live_id"].encode("utf-8")).hexdigest()


def tombstone_id(entry: dict) -> str:
    return "pt1:" + hashlib.sha256(entry["_key"].encode("utf-8")).hexdigest()


def _claim_score(claim: dict) -> tuple[int, int, float]:
    return (
        0 if claim["kind"] == "osm" else 1,
        int(claim["rank"]),
        float(claim["distance_m"]),
    )


def _component_verdict_claims(
        component: Component,
        verdicts: parking_verdicts.Verdicts) -> dict[str, tuple[tuple, dict]]:
    best: dict[str, tuple[tuple, dict]] = {}
    authoritative_keys = set()
    for member in component.members:
        if member.geometry_type in ("Polygon", "MultiPolygon"):
            latitude, longitude = geometry_bbox_center(member.geometry)
        else:
            latitude, longitude = member.latitude, member.longitude
        for claim in verdicts.claims({
                "osm": member.alias, "lat": latitude, "lon": longitude}):
            key = claim["entry"]["_key"]
            score = _claim_score(claim)
            if claim["kind"] == "osm":
                authoritative_keys.add(key)
            previous = best.get(key)
            if previous is None or score < previous[0]:
                best[key] = (score, claim["entry"])
    if len(authoritative_keys) > 1:
        raise CensusError(
            f"candidate {component.candidate_id} aliases match conflicting verdicts "
            f"{sorted(authoritative_keys)}"
        )
    for pin in component.live_pins:
        for claim in verdicts.claims(pin):
            key = claim["entry"]["_key"]
            score = _claim_score(claim)
            previous = best.get(key)
            if previous is None or score < previous[0]:
                best[key] = (score, claim["entry"])
    return best


def _live_verdict_claims(
        pin: dict, verdicts: parking_verdicts.Verdicts,
) -> dict[str, tuple[tuple, dict]]:
    best = {}
    for claim in verdicts.claims(pin):
        key = claim["entry"]["_key"]
        score = _claim_score(claim)
        previous = best.get(key)
        if previous is None or score < previous[0]:
            best[key] = (score, claim["entry"])
    return best


def reconcile_verdict_ledger(
        components: tuple[Component, ...], synthetic_live: list[dict],
        verdicts: parking_verdicts.Verdicts, *,
        component_ids: set[str] | None = None,
        seen_authority_aliases: Iterable[str] = ()) -> VerdictLedger:
    """Assign verdicts one-to-one after globally reserving exact OSM aliases."""
    alias_owners = _sidecar_alias_owners(verdicts)
    seen_aliases = tuple(sorted(set(seen_authority_aliases)))
    unknown_seen_aliases = sorted(set(seen_aliases) - set(alias_owners))
    if unknown_seen_aliases:
        raise CensusError(
            "parking export reported authority aliases absent from the sidecar: "
            f"{unknown_seen_aliases[:10]}"
        )
    seen_keys = tuple(sorted({alias_owners[alias] for alias in seen_aliases}))
    all_component_claims: dict[str, dict[str, tuple[tuple, dict]]] = {}
    authoritative_reservations: dict[str, str | None] = {
        key: None for key in seen_keys
    }
    for component in components:
        component.verdict_entry = None
        claims = _component_verdict_claims(component, verdicts)
        all_component_claims[component.candidate_id] = claims
        for key, (score, _entry) in claims.items():
            if score[0] != 0:
                continue
            previous = authoritative_reservations.get(key)
            if (key in authoritative_reservations
                    and previous is not None
                    and previous != component.candidate_id):
                raise CensusError(
                    f"verdict {key} has exact OSM aliases in distinct candidates "
                    f"{previous} and {component.candidate_id}"
                )
            authoritative_reservations[key] = component.candidate_id

    component_by_id = {
        component.candidate_id: component
        for component in components
        if component_ids is None or component.candidate_id in component_ids
    }
    candidates = {
        component_id: all_component_claims[component_id]
        for component_id in component_by_id
    }
    pin_by_id = {}
    for pin in synthetic_live:
        pin.pop("verdict_entry", None)
        candidate_id = live_candidate_id(pin)
        if candidate_id in candidates or candidate_id in pin_by_id:
            raise CensusError(f"duplicate current candidate id {candidate_id}")
        pin_by_id[candidate_id] = pin
        candidates[candidate_id] = _live_verdict_claims(pin, verdicts)

    edges_by_candidate: dict[str, dict[str, tuple[tuple, dict]]] = {}
    edges_by_key: dict[str, dict[str, tuple[tuple, dict]]] = collections.defaultdict(dict)
    for candidate_id, claims in candidates.items():
        eligible = {}
        for key, edge in claims.items():
            reserved_for = authoritative_reservations.get(key)
            if (key in authoritative_reservations
                    and reserved_for != candidate_id):
                continue
            eligible[key] = edge
            edges_by_key[key][candidate_id] = edge
        if eligible:
            edges_by_candidate[candidate_id] = eligible

    selected_component_ids = set(component_by_id)
    reserved_unselected = {
        key for key, candidate_id in authoritative_reservations.items()
        if candidate_id not in selected_component_ids
    }
    assignments: dict[str, dict] = {}
    remaining_candidates = set(edges_by_candidate)
    remaining_keys = set(verdicts.entries) - reserved_unselected
    while True:
        candidate_best: dict[str, tuple[str, tuple]] = {}
        for candidate_id in sorted(remaining_candidates):
            options = [
                (score, key)
                for key, (score, _entry) in edges_by_candidate[candidate_id].items()
                if key in remaining_keys
            ]
            if not options:
                continue
            best_score = min(score for score, _key in options)
            best_keys = sorted(key for score, key in options if score == best_score)
            if len(best_keys) == 1:
                candidate_best[candidate_id] = (best_keys[0], best_score)
        verdict_best: dict[str, tuple[str, tuple]] = {}
        for key in sorted(remaining_keys):
            options = [
                (score, candidate_id)
                for candidate_id, (score, _entry) in edges_by_key.get(key, {}).items()
                if candidate_id in remaining_candidates
            ]
            if not options:
                continue
            best_score = min(score for score, _candidate_id in options)
            best_candidates = sorted(
                candidate_id for score, candidate_id in options
                if score == best_score
            )
            if len(best_candidates) == 1:
                verdict_best[key] = (best_candidates[0], best_score)
        accepted = [
            (candidate_id, key)
            for candidate_id, (key, score) in candidate_best.items()
            if verdict_best.get(key) == (candidate_id, score)
        ]
        if not accepted:
            break
        for candidate_id, key in sorted(accepted):
            assignments[candidate_id] = edges_by_candidate[candidate_id][key][1]
            remaining_candidates.remove(candidate_id)
            remaining_keys.remove(key)

    for candidate_id, entry in assignments.items():
        if candidate_id in component_by_id:
            component_by_id[candidate_id].verdict_entry = entry
        else:
            pin_by_id[candidate_id]["verdict_entry"] = entry
    matched_keys = tuple(sorted(entry["_key"] for entry in assignments.values()))
    if len(matched_keys) != len(set(matched_keys)):
        raise AssertionError("verdict ledger reused one verdict key")
    tombstones = tuple(
        verdicts.entries[key] for key in sorted(set(verdicts.entries) - set(matched_keys))
    )
    if len(matched_keys) + len(tombstones) != len(verdicts.entries):
        raise AssertionError("verdict ledger did not reconcile the sidecar")
    return VerdictLedger(
        assignments=assignments,
        matched_keys=matched_keys,
        tombstone_entries=tombstones,
        claim_edges=sum(len(values) for values in edges_by_candidate.values()),
        ambiguous_edges=sum(
            candidate_id in remaining_candidates and key in remaining_keys
            for candidate_id, values in edges_by_candidate.items()
            for key in values
        ),
        reserved_authoritative_keys=tuple(sorted(authoritative_reservations)),
        reserved_unselected_keys=tuple(sorted(reserved_unselected)),
        seen_authority_aliases=seen_aliases,
        seen_authority_keys=seen_keys,
    )


def match_component_verdicts(
        components: tuple[Component, ...],
        verdicts: parking_verdicts.Verdicts) -> VerdictLedger:
    """Compatibility entry point for census-only component reconciliation."""
    return reconcile_verdict_ledger(components, [], verdicts)


def _access_summary(component: Component) -> dict:
    values = [
        (member.tags.get("access") or "").strip().casefold()
        for member in component.members
    ]
    nonempty = sorted(set(value for value in values if value))
    missing = sum(not value for value in values)
    if not nonempty:
        classification = "unspecified"
    elif (all(value in HARD_NON_PUBLIC_ACCESS for value in nonempty)
          and missing == 0):
        classification = "hard_non_public"
    elif len(nonempty) > 1 or missing or any(
            value not in HARD_NON_PUBLIC_ACCESS | {"yes", "public", "permissive", "permit"}
            for value in nonempty):
        classification = "mixed_or_ambiguous"
    elif nonempty == ["permit"]:
        classification = "permit_restricted"
    else:
        classification = "public"
    return {
        "classification": classification,
        "values": nonempty,
        "missing_member_count": missing,
        "hard_non_public": any(value in HARD_NON_PUBLIC_ACCESS for value in nonempty),
    }


def _projection_eligibility(component: Component) -> dict:
    access_values = sorted({
        (member.tags.get("access") or "").strip().casefold()
        for member in component.members
        if (member.tags.get("access") or "").strip()
    })
    parking_values = sorted({
        (member.tags.get("parking") or "").strip().casefold()
        for member in component.members
        if (member.tags.get("parking") or "").strip()
    })
    current_reasons = []
    current_reasons.extend(
        f"access={value}" for value in access_values
        if value in CURRENT_EXCLUDED_ACCESS
    )
    current_reasons.extend(
        f"parking={value}" for value in parking_values
        if value in CURRENT_EXCLUDED_PARKING
    )
    verdict = component.verdict_entry.get("verdict") if component.verdict_entry else None
    effective_reasons = list(current_reasons)
    if verdict == "DROP":
        effective_reasons.append("verdict=DROP")
    return {
        "raw": {"eligible": True, "exclusions": []},
        "current": {"eligible": not current_reasons, "exclusions": current_reasons},
        "effective": {
            "eligible": not effective_reasons,
            "exclusions": effective_reasons,
        },
    }


def _select_service_rows(
        scored: tuple[tuple[float, int], ...],
        eligible: Callable[[int], bool],
) -> tuple[tuple[tuple[float, int], ...], bool, int]:
    near = []
    fallback = []
    visited = 0
    for item in scored:
        distance_m, component_index = item
        visited += 1
        if distance_m > FALLBACK_M or (near and distance_m > NEAR_M):
            break
        if not eligible(component_index):
            continue
        if distance_m <= NEAR_M:
            near.append(item)
            if len(near) == NEAR_LIMIT:
                break
        else:
            fallback.append(item)
            if len(fallback) == FALLBACK_LIMIT:
                break
    if near:
        return tuple(near), False, visited
    return tuple(fallback), bool(fallback), visited


def build_service_graph(
        trails: tuple[Trail, ...], endpoints: tuple[Endpoint, ...],
        components: tuple[Component, ...], *,
        distance_cache: dict[tuple[tuple[str, str], str], float] | None = None,
) -> ServiceGraph:
    """Build immutable endpoint/component/distance rows exactly once."""
    if distance_cache is None:
        distance_cache = {}
    by_endpoint: dict[int, list[int]] = collections.defaultdict(list)
    endpoint_component_associations = 0
    for component_index, component in enumerate(components):
        for endpoint_id in component.near_endpoint_ids:
            values = by_endpoint[endpoint_id]
            values.append(component_index)
            endpoint_component_associations += 1
            if len(values) > MAX_COMPONENTS_PER_ENDPOINT:
                raise CensusError(
                    f"endpoint {endpoint_id} has {len(values):,} candidate clusters; "
                    f"limit is {MAX_COMPONENTS_PER_ENDPOINT:,}"
                )
    endpoint_components = tuple(
        (endpoint_id, tuple(sorted(values)))
        for endpoint_id, values in sorted(by_endpoint.items())
    )
    current_eligible = tuple(
        _projection_eligibility(component)["current"]["eligible"]
        for component in components
    )
    trail_graphs = []
    static_binding_maps: list[dict[tuple[str, str], dict]] = [
        {} for _component in components
    ]
    static_selected = {
        "raw": set(), "current": set(),
    }
    static_counts = {
        "raw": collections.Counter(), "current": collections.Counter(),
    }
    max_trail_candidates = 0
    association_rows = 0
    new_distance_computations = 0
    reused_distance_evaluations = 0
    static_selection_rows = 0
    for trail in sorted(trails, key=lambda item: item.key):
        if not trail.endpoint_ids:
            continue
        candidate_index_set = set()
        for endpoint_id in trail.endpoint_ids:
            for component_index in by_endpoint.get(endpoint_id, ()):
                candidate_index_set.add(component_index)
                if len(candidate_index_set) > MAX_TRAIL_CANDIDATE_COMPONENTS:
                    raise CensusError(
                        f"trail {trail.area}/{trail.trail_id} has more than "
                        f"{MAX_TRAIL_CANDIDATE_COMPONENTS:,} candidate clusters"
                    )
        scored = []
        for component_index in candidate_index_set:
            component = components[component_index]
            cache_key = (trail.key, component.candidate_id)
            if cache_key in distance_cache:
                distance_m = distance_cache[cache_key]
                reused_distance_evaluations += 1
            else:
                distance_m = component_trail_distance_m(
                    component, trail, endpoints
                )
                distance_cache[cache_key] = distance_m
                new_distance_computations += 1
            scored.append((distance_m, component_index))
        scored.sort(
            key=lambda item: (item[0], components[item[1]].candidate_id)
        )
        scored_rows = tuple(scored)
        raw_selection, raw_fallback, _ = _select_service_rows(
            scored_rows, lambda _index: True
        )
        current_selection, current_fallback, _ = _select_service_rows(
            scored_rows, lambda index: current_eligible[index]
        )
        trail_graphs.append(ServiceTrailGraph(trail, scored_rows))
        for projection, selection, fallback in (
                ("raw", raw_selection, raw_fallback),
                ("current", current_selection, current_fallback)):
            if selection:
                static_counts[projection]["trail_selections"] += 1
                static_counts[projection][
                    "fallback_trails" if fallback else "near_trails"
                ] += 1
            for distance_m, component_index in selection:
                static_selected[projection].add(component_index)
                binding = static_binding_maps[component_index].get(trail.key)
                if binding is None:
                    if len(static_binding_maps[component_index]) >= (
                            MAX_BINDINGS_PER_COMPONENT):
                        raise CensusError(
                            f"candidate {components[component_index].candidate_id} "
                            f"exceeds {MAX_BINDINGS_PER_COMPONENT:,} service bindings"
                        )
                    binding = {
                        "area": trail.area,
                        "jurisdiction": trail.jurisdiction,
                        "trail_id": trail.trail_id,
                        "trail_name": trail.name,
                        "distance_m": round(distance_m, 3),
                        "fallback": fallback,
                        "projections": [],
                    }
                    static_binding_maps[component_index][trail.key] = binding
                elif binding["fallback"] != fallback:
                    raise AssertionError(
                        "one static trail/component binding changed near/fallback "
                        "class between projections"
                    )
                binding["projections"].append(projection)
        association_rows += len(scored_rows)
        static_selection_rows += len(raw_selection) + len(current_selection)
        max_trail_candidates = max(max_trail_candidates, len(scored_rows))
    static_bindings = tuple(
        tuple(
            StaticServiceBinding(
                binding["area"], binding["jurisdiction"],
                binding["trail_id"], binding["trail_name"],
                binding["distance_m"], binding["fallback"],
                tuple(binding["projections"]),
            )
            for binding in sorted(
                values.values(),
                key=lambda binding: (
                    binding["distance_m"], binding["jurisdiction"],
                    binding["area"], binding["trail_id"],
                ),
            )
        )
        for values in static_binding_maps
    )

    def summary(projection: str) -> StaticProjectionSummary:
        return StaticProjectionSummary(
            tuple(sorted(static_selected[projection])),
            static_counts[projection]["trail_selections"],
            static_counts[projection]["near_trails"],
            static_counts[projection]["fallback_trails"],
        )

    return ServiceGraph(
        tuple(component.candidate_id for component in components),
        endpoint_components,
        tuple(trail_graphs),
        static_bindings,
        summary("raw"),
        summary("current"),
        endpoint_component_associations,
        max((len(values) for values in by_endpoint.values()), default=0),
        association_rows,
        max_trail_candidates,
        association_rows,
        association_rows,
        new_distance_computations,
        reused_distance_evaluations,
        len(distance_cache),
        static_selection_rows,
    )


def evaluate_service_graph(
        graph: ServiceGraph,
        components: tuple[Component, ...]) -> tuple[set[str], dict]:
    """Recompute only verdict-dependent effective selection and bindings."""
    if graph.component_ids != tuple(
            component.candidate_id for component in components):
        raise CensusError("service graph component identity changed")
    for component in components:
        component.projection_eligibility = _projection_eligibility(component)
        component.bindings = []
    binding_maps: dict[str, dict[tuple[str, str], dict]] = (
        collections.defaultdict(dict)
    )
    for component_index, rows in enumerate(graph.static_bindings):
        candidate_id = components[component_index].candidate_id
        for row in rows:
            binding_maps[candidate_id][(row.area, row.trail_id)] = {
                "area": row.area,
                "jurisdiction": row.jurisdiction,
                "trail_id": row.trail_id,
                "trail_name": row.trail_name,
                "distance_m": row.distance_m,
                "fallback": row.fallback,
                "projections": list(row.projections),
            }
    selected_ids = {
        components[index].candidate_id
        for index in (
            set(graph.raw_summary.selected_component_indices)
            | set(graph.current_summary.selected_component_indices)
        )
    }
    counters = {}
    for projection, summary in (
            ("raw", graph.raw_summary),
            ("current", graph.current_summary)):
        counters[projection] = {
            "selected_candidates": {
                components[index].candidate_id
                for index in summary.selected_component_indices
            },
            "trail_selections": summary.trail_selections,
            "near_trails": summary.near_trails,
            "fallback_trails": summary.fallback_trails,
        }
    counters["effective"] = {
        "selected_candidates": set(), "trail_selections": 0,
        "near_trails": 0, "fallback_trails": 0,
    }
    effective_rows_visited = 0

    def record_selection(
            trail: Trail, projection: str,
            selected: tuple[tuple[float, int], ...], fallback: bool) -> None:
        if selected:
            counters[projection]["trail_selections"] += 1
            counters[projection][
                "fallback_trails" if fallback else "near_trails"
            ] += 1
        for distance_m, component_index in selected:
            component = components[component_index]
            selected_ids.add(component.candidate_id)
            counters[projection]["selected_candidates"].add(
                component.candidate_id
            )
            key = trail.key
            binding = binding_maps[component.candidate_id].get(key)
            if binding is None:
                if len(binding_maps[component.candidate_id]) >= MAX_BINDINGS_PER_COMPONENT:
                    raise CensusError(
                        f"candidate {component.candidate_id} exceeds "
                        f"{MAX_BINDINGS_PER_COMPONENT:,} service bindings"
                    )
                binding = {
                    "area": trail.area,
                    "jurisdiction": trail.jurisdiction,
                    "trail_id": trail.trail_id,
                    "trail_name": trail.name,
                    "distance_m": round(distance_m, 3),
                    "fallback": fallback,
                    "projections": [],
                }
                binding_maps[component.candidate_id][key] = binding
            elif binding["fallback"] != fallback:
                raise AssertionError(
                    "one trail/component binding changed near/fallback class "
                    "between projections"
                )
            binding["projections"].append(projection)

    for trail_graph in graph.trails:
        effective_selection, effective_fallback, visited = _select_service_rows(
            trail_graph.scored,
            lambda index: components[index].projection_eligibility[
                "effective"
            ]["eligible"],
        )
        effective_rows_visited += visited
        record_selection(
            trail_graph.trail, "effective",
            effective_selection, effective_fallback,
        )
    max_bindings = 0
    for component in components:
        component.bindings = sorted(
            binding_maps.get(component.candidate_id, {}).values(),
            key=lambda binding: (
                binding["distance_m"], binding["jurisdiction"],
                binding["area"], binding["trail_id"],
            ),
        )
        max_bindings = max(max_bindings, len(component.bindings))
    portable_counters = {}
    for projection in PROJECTIONS:
        portable_counters[projection] = {
            "selected_candidates": len(counters[projection]["selected_candidates"]),
            "trail_selections": counters[projection]["trail_selections"],
            "near_trails": counters[projection]["near_trails"],
            "fallback_trails": counters[projection]["fallback_trails"],
        }
    portable_counters["associations"] = {
        "endpoint_component_associations": graph.endpoint_component_associations,
        "max_components_per_endpoint": graph.max_components_per_endpoint,
        "trail_candidate_clusters_total": graph.trail_candidate_clusters_total,
        "max_trail_candidate_clusters": graph.max_trail_candidate_clusters,
        "exact_trail_distance_evaluations": graph.association_rows_built,
        "new_exact_trail_distance_computations": graph.new_distance_computations,
        "reused_trail_distance_evaluations": graph.reused_distance_evaluations,
        "distance_cache_entries": graph.distance_cache_entries,
        "service_graph_builds": 1,
        "service_graph_association_rows_built": graph.association_rows_built,
        "service_graph_association_rows_sorted": graph.association_rows_sorted,
        "raw_current_selection_precomputations": 1,
        "static_selection_rows": graph.static_selection_rows,
        "static_binding_rows_precomputed": sum(
            len(rows) for rows in graph.static_bindings
        ),
        "effective_association_rows_visited": effective_rows_visited,
        "association_rows_rebuilt_this_evaluation": 0,
        "association_rows_resorted_this_evaluation": 0,
        "max_bindings_per_candidate": max_bindings,
        "endpoint_associations_truncated": 0,
    }
    return selected_ids, portable_counters


def service_projections(
        trails: tuple[Trail, ...], endpoints: tuple[Endpoint, ...],
        components: tuple[Component, ...], *,
        distance_cache: dict[tuple[tuple[str, str], str], float] | None = None,
        service_graph: ServiceGraph | None = None,
) -> tuple[set[str], dict]:
    graph = service_graph or build_service_graph(
        trails, endpoints, components, distance_cache=distance_cache
    )
    return evaluate_service_graph(graph, components)


def _close_candidate_verdict_service(
        components: tuple[Component, ...], synthetic_live: list[dict],
        verdicts: parking_verdicts.Verdicts,
        service_pass: Callable[[], tuple[set[str], dict]], *,
        seen_authority_aliases: Iterable[str] = (),
        max_rounds: int | None = None,
) -> tuple[set[str], dict, VerdictLedger]:
    """Close effective selection over one caller-precomputed service graph."""
    for component in components:
        component.verdict_entry = None
    selected_ids, projection_counts = service_pass()
    final_component_ids = set(selected_ids) | {
        component.candidate_id for component in components if component.live_pins
    }
    round_limit = (
        min(len(components) + 2, 100) if max_rounds is None else max_rounds
    )
    if round_limit <= 0:
        raise CensusError("candidate/verdict service closure needs a positive round limit")
    seen_candidate_sets = set()
    for round_index in range(1, round_limit + 1):
        signature = tuple(sorted(final_component_ids))
        if signature in seen_candidate_sets:
            raise CensusError(
                "candidate/verdict service closure entered a cycle; "
                f"candidate_count={len(signature):,}, round={round_index}"
            )
        seen_candidate_sets.add(signature)
        ledger = reconcile_verdict_ledger(
            components, synthetic_live, verdicts,
            component_ids=final_component_ids,
            seen_authority_aliases=seen_authority_aliases,
        )
        selected_ids, projection_counts = service_pass()
        next_component_ids = set(selected_ids) | {
            component.candidate_id for component in components if component.live_pins
        }
        if next_component_ids == final_component_ids:
            associations = projection_counts.get("associations", {})
            projection_counts["closure"] = {
                "rounds": round_index,
                "candidate_sets_seen": len(seen_candidate_sets),
                "service_evaluations": round_index + 1,
                "distance_cache_entries": associations.get(
                    "distance_cache_entries", 0
                ),
                "service_graph_builds": associations.get(
                    "service_graph_builds", 0
                ),
                "association_rows_built_once": associations.get(
                    "service_graph_association_rows_built", 0
                ),
                "association_rows_sorted_once": associations.get(
                    "service_graph_association_rows_sorted", 0
                ),
                "association_rows_rebuilt_in_rounds": 0,
                "association_rows_resorted_in_rounds": 0,
                "raw_current_selection_precomputations": associations.get(
                    "raw_current_selection_precomputations", 0
                ),
                "static_binding_rows_precomputed": associations.get(
                    "static_binding_rows_precomputed", 0
                ),
                "converged": True,
            }
            return final_component_ids, projection_counts, ledger
        final_component_ids = next_component_ids
    raise CensusError(
        "candidate/verdict service closure did not converge within "
        f"{round_limit} rounds"
    )


def _load_live_pool(path: str | Path) -> LiveCapture:
    candidate = Path(path).absolute()
    raw, binding = _capture_file_bytes(candidate, role="live-pool")
    document = _strict_json_bytes(raw, "live parking pool")
    if not isinstance(document, list):
        raise CensusError("live parking pool root must be an array")
    normalized = []
    for index, row in enumerate(document):
        if not isinstance(row, list) or len(row) < 2:
            raise CensusError(f"live parking row {index} must be a positional array")
        latitude = _finite_number(row[0], f"live parking row {index} latitude")
        longitude = _finite_number(row[1], f"live parking row {index} longitude")
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise CensusError(f"live parking row {index} coordinate is out of range")
        pin = {"lat": latitude, "lon": longitude}
        fields = ("name", "source")
        for offset, key in enumerate(fields, start=2):
            if len(row) > offset and row[offset] is not None:
                if not isinstance(row[offset], str):
                    raise CensusError(f"live parking row {index} {key} must be a string/null")
                pin[key] = row[offset]
        if len(row) > 4 and row[4] is not None:
            if row[4] not in (0, 1, False, True):
                raise CensusError(f"live parking row {index} trailhead flag is invalid")
            pin["trailhead"] = bool(row[4])
        if len(row) > 5 and row[5] is not None:
            if row[5] not in (0, 1, False, True):
                raise CensusError(f"live parking row {index} fee flag is invalid")
            pin["fee"] = bool(row[5])
        normalized.append(pin)
    normalized.sort(key=lambda pin: _canonical_bytes(pin))
    occurrences = collections.Counter()
    pins = []
    for pin in normalized:
        base = _canonical_hash(pin)
        occurrence = occurrences[base]
        occurrences[base] += 1
        value = dict(pin)
        value["live_id"] = "live-" + hashlib.sha256(
            f"{base}#{occurrence}".encode()
        ).hexdigest()
        pins.append(value)
    return LiveCapture(tuple(pins), binding)


def _load_verdicts(path: str | Path) -> VerdictCapture:
    candidate = Path(path).absolute()
    raw, binding = _capture_file_bytes(candidate, role="verdict-sidecar")
    try:
        verdicts = parking_verdicts.strict_verdicts_bytes(
            raw, "national census verdict sidecar"
        )
    except (TypeError, ValueError) as error:
        raise CensusError(str(error)) from error
    return VerdictCapture(verdicts, binding)


def _exact_keys(value: object, expected: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != expected:
        actual = sorted(value) if isinstance(value, dict) else type(value).__name__
        raise CensusError(
            f"{label} must have exact keys {sorted(expected)}; found {actual}"
        )
    return value


def _baseline_hash(value: object, label: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise CensusError(f"{label} must be one lowercase SHA-256")
    return value


def _baseline_count(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CensusError(f"{label} must be a nonnegative integer")
    return value


def _load_approved_baseline_registry() -> tuple[tuple[dict, ...], SourceBinding]:
    raw, binding = _capture_file_bytes(
        APPROVED_BASELINE_REGISTRY_PATH,
        role="approved-production-baseline-registry",
    )
    if binding.sha256 != APPROVED_BASELINE_REGISTRY_SHA256:
        raise CensusError(
            "reviewed approved baseline registry digest mismatch: "
            f"expected {APPROVED_BASELINE_REGISTRY_SHA256}, "
            f"found {binding.sha256}"
        )
    document = _strict_json_bytes(raw, "approved production baseline registry")
    root = _exact_keys(document, {
        "schema", "version", "baselines", "self_hash_algorithm", "self_sha256",
    }, "approved production baseline registry")
    if raw != _canonical_bytes(root):
        raise CensusError("approved production baseline registry is not canonical JSON")
    if (root["schema"] != APPROVED_BASELINE_REGISTRY_SCHEMA
            or isinstance(root["version"], bool) or root["version"] != 1):
        raise CensusError("approved production baseline registry schema is unsupported")
    if root["self_hash_algorithm"] != PRODUCTION_BASELINE_SELF_HASH:
        raise CensusError("approved production baseline registry hash policy is wrong")
    supplied_self = _baseline_hash(
        root["self_sha256"], "approved baseline registry self_sha256"
    )
    payload = dict(root)
    payload.pop("self_sha256")
    if supplied_self != _canonical_hash(payload):
        raise CensusError("approved production baseline registry self-hash mismatch")
    if not isinstance(root["baselines"], list) or not root["baselines"]:
        raise CensusError("approved production baseline registry must not be empty")
    entries = []
    previous = None
    for index, value in enumerate(root["baselines"], start=1):
        entry = _exact_keys(value, {
            "version", "name", "self_sha256", "canonical_file_sha256",
            "predecessor_self_sha256",
        }, f"approved production baseline registry entry {index}")
        if (isinstance(entry["version"], bool)
                or not isinstance(entry["version"], int)
                or entry["version"] != index):
            raise CensusError(
                "approved production baseline registry versions must be "
                "contiguous and ordered from 1"
            )
        if entry["name"] != f"production-baseline-v{index}":
            raise CensusError("approved production baseline registry name/version mismatch")
        _baseline_hash(
            entry["self_sha256"],
            f"approved production baseline v{index} self_sha256",
        )
        _baseline_hash(
            entry["canonical_file_sha256"],
            f"approved production baseline v{index} canonical_file_sha256",
        )
        expected_predecessor = previous["self_sha256"] if previous else None
        if entry["predecessor_self_sha256"] != expected_predecessor:
            raise CensusError(
                f"approved production baseline v{index} predecessor lineage is wrong"
            )
        entries.append(dict(entry))
        previous = entry
    if len({entry["self_sha256"] for entry in entries}) != len(entries):
        raise CensusError("approved production baseline registry repeats a digest")
    _verify_binding(binding)
    return tuple(entries), binding


def _load_production_baseline(
        path: str | Path, *, require_approved: bool = False) -> ProductionBaseline:
    raw, binding = _capture_file_bytes(path, role="production-baseline")
    document = _strict_json_bytes(raw, "production parking baseline")
    if not isinstance(document, dict):
        raise CensusError("production parking baseline must be an object")
    version = document.get("version")
    if (isinstance(version, bool) or not isinstance(version, int)
            or version <= 0):
        raise CensusError("production parking baseline version must be positive")
    expected_keys = {
        "schema", "version", "name", "bundle", "geometry", "scope",
        "live_pool", "verdicts", "self_hash_algorithm", "self_sha256",
    }
    if version > 1:
        expected_keys.add("predecessor_self_sha256")
    root = _exact_keys(document, expected_keys, "production parking baseline")
    if raw != _canonical_bytes(root):
        raise CensusError("production parking baseline is not canonical JSON")
    if root["schema"] != PRODUCTION_BASELINE_SCHEMA:
        raise CensusError("production parking baseline schema is unsupported")
    if root["name"] != f"production-baseline-v{version}":
        raise CensusError("production parking baseline name/version mismatch")
    predecessor = root.get("predecessor_self_sha256")
    if version == 1:
        predecessor = None
    else:
        _baseline_hash(
            predecessor, "production parking baseline predecessor_self_sha256"
        )
    if root["self_hash_algorithm"] != PRODUCTION_BASELINE_SELF_HASH:
        raise CensusError("production parking baseline self-hash algorithm is wrong")
    supplied_self_hash = _baseline_hash(
        root["self_sha256"], "production parking baseline self_sha256"
    )
    payload = dict(root)
    payload.pop("self_sha256")
    expected_self_hash = _canonical_hash(payload)
    if supplied_self_hash != expected_self_hash:
        raise CensusError(
            "production parking baseline self-hash mismatch: "
            f"expected {expected_self_hash}, found {supplied_self_hash}"
        )
    if predecessor == supplied_self_hash:
        raise CensusError("production parking baseline cannot supersede itself")
    bundle = _exact_keys(root["bundle"], {"sha256"}, "baseline bundle")
    geometry = _exact_keys(
        root["geometry"], {"authority_sha256"}, "baseline geometry"
    )
    scope = _exact_keys(root["scope"], {
        "areas", "jurisdictions", "trails", "trails_without_source_id",
        "endpoint_occurrences", "unique_endpoints", "area_slugs_sha256",
    }, "baseline scope")
    live_pool = _exact_keys(
        root["live_pool"], {"rows", "size_bytes", "sha256"},
        "baseline live_pool",
    )
    verdicts = _exact_keys(root["verdicts"], {
        "rows", "keep", "drop", "review", "sha256",
    }, "baseline verdicts")
    _baseline_hash(bundle["sha256"], "baseline bundle sha256")
    _baseline_hash(
        geometry["authority_sha256"], "baseline geometry authority_sha256"
    )
    _baseline_hash(scope["area_slugs_sha256"], "baseline area_slugs_sha256")
    _baseline_hash(live_pool["sha256"], "baseline live_pool sha256")
    _baseline_hash(verdicts["sha256"], "baseline verdicts sha256")
    for key, value in scope.items():
        if key != "area_slugs_sha256":
            _baseline_count(value, f"baseline scope {key}")
    for key in ("rows", "size_bytes"):
        _baseline_count(live_pool[key], f"baseline live_pool {key}")
    for key in ("rows", "keep", "drop", "review"):
        _baseline_count(verdicts[key], f"baseline verdicts {key}")
    if verdicts["rows"] != verdicts["keep"] + verdicts["drop"] + verdicts["review"]:
        raise CensusError("baseline verdict counts do not reconcile")
    registry_binding = None
    approval = None
    if require_approved:
        approvals, registry_binding = _load_approved_baseline_registry()
        approval = next((
            entry for entry in approvals
            if entry["self_sha256"] == supplied_self_hash
        ), None)
        if approval is None:
            raise CensusError(
                "production baseline is internally valid but is not in the "
                "reviewed approved baseline registry"
            )
        expected_approval = {
            "version": version,
            "name": root["name"],
            "self_sha256": supplied_self_hash,
            "canonical_file_sha256": binding.sha256,
            "predecessor_self_sha256": predecessor,
        }
        if approval != expected_approval:
            raise CensusError(
                "production baseline does not match its reviewed registry entry"
            )
        _verify_binding(registry_binding)
    _verify_binding(binding)
    return ProductionBaseline(
        binding, supplied_self_hash, root, registry_binding, approval
    )


def production_baseline_candidate_document(
        scope: ScopeCapture, live: LiveCapture, verdicts: VerdictCapture, *,
        version: int, predecessor_self_sha256: str) -> dict:
    if (isinstance(version, bool) or not isinstance(version, int)
            or version <= 1):
        raise CensusError("rotated production baseline version must be at least 2")
    predecessor = _baseline_hash(
        predecessor_self_sha256, "candidate predecessor_self_sha256"
    )
    verdict_counts = collections.Counter(
        entry["verdict"] for entry in verdicts.verdicts.entries.values()
    )
    payload = {
        "schema": PRODUCTION_BASELINE_SCHEMA,
        "version": version,
        "name": f"production-baseline-v{version}",
        "predecessor_self_sha256": predecessor,
        "bundle": {"sha256": scope.bundle_binding.sha256},
        "geometry": {
            "authority_sha256": _geometry_authority_sha256(scope.geom_bindings)
        },
        "scope": {
            "areas": len(scope.areas),
            "jurisdictions": len(scope.jurisdictions),
            "trails": len(scope.trails),
            "trails_without_source_id": scope.trails_without_source_id,
            "endpoint_occurrences": scope.endpoint_occurrences,
            "unique_endpoints": len(scope.endpoints),
            "area_slugs_sha256": _area_slugs_sha256(scope.areas),
        },
        "live_pool": {
            "rows": len(live.pins),
            "size_bytes": live.binding.size_bytes,
            "sha256": live.binding.sha256,
        },
        "verdicts": {
            "rows": len(verdicts.verdicts.entries),
            "keep": verdict_counts["KEEP"],
            "drop": verdict_counts["DROP"],
            "review": verdict_counts["REVIEW"],
            "sha256": verdicts.binding.sha256,
        },
        "self_hash_algorithm": PRODUCTION_BASELINE_SELF_HASH,
    }
    return _self_hashed_document(payload)


def _validate_production_baseline(
        baseline: ProductionBaseline, scope: ScopeCapture,
        live: LiveCapture, verdicts: VerdictCapture) -> None:
    document = baseline.document
    scope_expected = document["scope"]
    actual_scope = {
        "areas": len(scope.areas),
        "jurisdictions": len(scope.jurisdictions),
        "trails": len(scope.trails),
        "trails_without_source_id": scope.trails_without_source_id,
        "endpoint_occurrences": scope.endpoint_occurrences,
        "unique_endpoints": len(scope.endpoints),
        "area_slugs_sha256": _area_slugs_sha256(scope.areas),
    }
    actual_verdicts = collections.Counter(
        entry["verdict"] for entry in verdicts.verdicts.entries.values()
    )
    checks = {
        "bundle.sha256": (
            scope.bundle_binding.sha256, document["bundle"]["sha256"]
        ),
        "geometry.authority_sha256": (
            _geometry_authority_sha256(scope.geom_bindings),
            document["geometry"]["authority_sha256"],
        ),
        **{
            f"scope.{key}": (actual_scope[key], expected)
            for key, expected in scope_expected.items()
        },
        "live_pool.rows": (len(live.pins), document["live_pool"]["rows"]),
        "live_pool.size_bytes": (
            live.binding.size_bytes, document["live_pool"]["size_bytes"]
        ),
        "live_pool.sha256": (
            live.binding.sha256, document["live_pool"]["sha256"]
        ),
        "verdicts.rows": (
            len(verdicts.verdicts.entries), document["verdicts"]["rows"]
        ),
        "verdicts.keep": (actual_verdicts["KEEP"], document["verdicts"]["keep"]),
        "verdicts.drop": (actual_verdicts["DROP"], document["verdicts"]["drop"]),
        "verdicts.review": (
            actual_verdicts["REVIEW"], document["verdicts"]["review"]
        ),
        "verdicts.sha256": (
            verdicts.binding.sha256, document["verdicts"]["sha256"]
        ),
    }
    mismatches = [
        f"{name}: observed={observed!r}, baseline={expected!r}"
        for name, (observed, expected) in checks.items()
        if observed != expected
    ]
    if mismatches:
        raise CensusError(
            "production baseline mismatch; authoritative census refused: "
            + "; ".join(mismatches)
        )
    for binding in (
            baseline.binding, baseline.registry_binding,
            scope.bundle_binding, *scope.geom_bindings,
            live.binding, verdicts.binding):
        if binding is not None:
            _verify_binding(binding)


class ComponentPointIndex:
    """Exact multi-resolution point index for component geometry claims."""

    def __init__(self, components: tuple[Component, ...], radius_m: float):
        if not math.isfinite(radius_m) or radius_m <= 0:
            raise CensusError("component spatial index radius must be positive")
        self.components = components
        self.radius_m = radius_m
        self.cell_degrees = max(
            math.degrees(radius_m / EARTH_RADIUS_M), 1e-9
        )
        self.grids: dict[int, EndpointGrid] = {}
        self.rows: dict[tuple[int, int, int], set[int]] = (
            collections.defaultdict(set)
        )
        active_levels = set()
        associations = 0
        max_geometry_cells = 0
        for component_index, component in enumerate(components):
            for member in component.members:
                level = 0
                while True:
                    grid = self._grid(level)
                    window = self._cell_window(member.geometry, radius_m, grid)
                    if (window[-1] <= TARGET_COMPONENT_INDEX_CELLS
                            or grid.cell >= 180.0):
                        break
                    level += 1
                cells = set(self._cells(window, grid))
                if not cells:
                    raise AssertionError("component index produced no geometry cells")
                active_levels.add(level)
                max_geometry_cells = max(max_geometry_cells, len(cells))
                for latitude_cell, longitude_cell in cells:
                    key = (level, latitude_cell, longitude_cell)
                    values = self.rows[key]
                    if component_index not in values:
                        values.add(component_index)
                        associations += 1
                        if associations > MAX_COMPONENT_INDEX_ASSOCIATIONS:
                            raise CensusError(
                                "component spatial index exceeds the total "
                                f"{MAX_COMPONENT_INDEX_ASSOCIATIONS:,}-association "
                                f"limit at {member.alias}"
                            )
        self.active_levels = tuple(sorted(active_levels))
        self.association_count = associations
        self.max_geometry_cells = max_geometry_cells

    def _grid(self, level: int) -> EndpointGrid:
        grid = self.grids.get(level)
        if grid is None:
            cell_degrees = min(180.0, self.cell_degrees * (2 ** level))
            grid = EndpointGrid((), cell_degrees)
            self.grids[level] = grid
        return grid

    @staticmethod
    def _cell_window(
            geometry: dict, radius_m: float,
            grid: EndpointGrid) -> tuple[int, int, int, int, int]:
        minimum_latitude, maximum_latitude, start, span = geometry_bounds(geometry)
        latitude_pad = math.degrees(radius_m / EARTH_RADIUS_M)
        south = max(-90.0, minimum_latitude - latitude_pad)
        north = min(90.0, maximum_latitude + latitude_pad)
        longitude_pad = EndpointGrid._longitude_pad(
            radius_m, max(abs(south), abs(north))
        )
        expanded_span = min(360.0, span + 2 * longitude_pad)
        expanded_start = (start - longitude_pad) % 360.0
        first_latitude = grid._latitude_cell(south)
        last_latitude = grid._latitude_cell(north)
        longitude_count = (
            grid.longitude_cells if expanded_span >= 360.0 else
            min(
                grid.longitude_cells,
                int(math.ceil(expanded_span / grid.cell)) + 3,
            )
        )
        first_longitude = int(math.floor(expanded_start / grid.cell)) - 1
        total = (last_latitude - first_latitude + 1) * longitude_count
        return (
            first_latitude, last_latitude, first_longitude,
            longitude_count, total,
        )

    @staticmethod
    def _cells(
            window: tuple[int, int, int, int, int],
            grid: EndpointGrid) -> Iterator[tuple[int, int]]:
        first_latitude, last_latitude, first_longitude, longitude_count, _ = window
        for latitude_cell in range(first_latitude, last_latitude + 1):
            for offset in range(longitude_count):
                yield (
                    latitude_cell,
                    (first_longitude + offset) % grid.longitude_cells,
                )

    def candidates(self, latitude: float, longitude: float) -> set[int]:
        result = set()
        for level in self.active_levels:
            grid = self.grids[level]
            key = (
                level,
                grid._latitude_cell(latitude),
                grid._longitude_cell(longitude),
            )
            result.update(self.rows.get(key, ()))
            if len(result) > MAX_SPATIAL_CANDIDATES:
                raise CensusError(
                    "multi-resolution spatial query exceeds the total "
                    f"{MAX_SPATIAL_CANDIDATES:,}-candidate limit"
                )
        return result


def _live_component_claim(
        component: Component, latitude: float,
        longitude: float) -> tuple[int, float] | None:
    claims = []
    for member in component.members:
        if member.geometry_type in ("Polygon", "MultiPolygon"):
            centre_latitude, centre_longitude = geometry_bbox_center(member.geometry)
            centre_distance = haversine_m(
                latitude, longitude, centre_latitude, centre_longitude
            )
            if centre_distance <= LIVE_BBOX_CENTRE_M:
                claims.append((0, centre_distance))
                continue
            if point_in_geometry(latitude, longitude, member.geometry):
                claims.append((1, 0.0))
                continue
            edge_distance = geometry_distance_to_point_m(
                member.geometry, latitude, longitude
            )
            if edge_distance <= LIVE_POLYGON_EDGE_M:
                claims.append((2, edge_distance))
        elif member.geometry_type == "Point":
            distance = geometry_distance_to_point_m(
                member.geometry, latitude, longitude
            )
            if distance <= LIVE_POINT_M:
                claims.append((3, distance))
    return min(claims) if claims else None


def attach_live_pins(
        components: tuple[Component, ...], pins: tuple[dict, ...],
        verdicts: parking_verdicts.Verdicts | None = None,
        diagnostics: dict | None = None) -> list[dict]:
    del verdicts  # Verdict identity is assigned once by the global ledger.
    index = ComponentPointIndex(components, LIVE_POINT_M)
    counters = collections.Counter()
    synthetic = []
    for pin in pins:
        indexed = index.candidates(pin["lat"], pin["lon"])
        counters["max_index_candidates"] = max(
            counters["max_index_candidates"], len(indexed)
        )
        if len(indexed) > MAX_SPATIAL_CANDIDATES:
            raise CensusError(
                f"live pin {pin['live_id']} has {len(indexed):,} spatial "
                f"candidates; limit is {MAX_SPATIAL_CANDIDATES:,}"
            )
        claims = []
        for component_index in indexed:
            score = _live_component_claim(
                components[component_index], pin["lat"], pin["lon"]
            )
            if score is not None:
                claims.append((score, components[component_index].candidate_id,
                               component_index))
        claims.sort(key=lambda item: (item[0], item[1]))
        counters["claim_edges"] += len(claims)
        best_rank = claims[0][0][0] if claims else None
        best = [claim for claim in claims if claim[0][0] == best_rank]
        if len(best) != 1:
            value = dict(pin)
            value["component_match"] = "ambiguous" if best else "unmatched"
            if claims:
                value["component_candidates"] = [
                    candidate_id for _score, candidate_id, _index in claims
                ]
                value["component_claims"] = [
                    {
                        "candidate_id": candidate_id,
                        "rank": score[0],
                        "distance_m": round(score[1], 3),
                    }
                    for score, candidate_id, _index in claims
                ]
            synthetic.append(value)
            counters[value["component_match"]] += 1
            continue
        component = components[best[0][2]]
        component.live_pins.append(dict(pin))
        counters["attached"] += 1
    if diagnostics is not None:
        diagnostics.update({
            "pins": len(pins),
            "attached": counters["attached"],
            "unmatched": counters["unmatched"],
            "ambiguous": counters["ambiguous"],
            "claim_edges": counters["claim_edges"],
            "max_index_candidates": counters["max_index_candidates"],
            "index_levels": list(index.active_levels),
            "index_associations": index.association_count,
            "max_geometry_index_cells": index.max_geometry_cells,
            "identity_max_m": LIVE_POINT_M,
            "possible_duplicate_m": CLUSTER_M,
        })
    return synthetic


def _candidate_pairs_by_bounds(
        components: tuple[Component, ...],
        radius_m: float) -> Iterator[tuple[int, int]]:
    """Yield conservative component pairs with a dateline-safe sweep line."""
    if not math.isfinite(radius_m) or radius_m <= 0:
        raise CensusError("possible-duplicate radius must be positive")
    records = []
    latitude_pad = math.degrees(radius_m / EARTH_RADIUS_M)
    for component_index, component in enumerate(components):
        for member in component.members:
            minimum_latitude, maximum_latitude, start, span = geometry_bounds(
                member.geometry
            )
            south = max(-90.0, minimum_latitude - latitude_pad)
            north = min(90.0, maximum_latitude + latitude_pad)
            longitude_pad = EndpointGrid._longitude_pad(
                radius_m, max(abs(south), abs(north))
            )
            west = start - longitude_pad
            east = start + span + longitude_pad
            if east - west >= 360.0:
                west, east = -180.0, 540.0
            for shift in (-360.0, 0.0, 360.0):
                records.append((
                    west + shift, east + shift, south, north,
                    component.candidate_id, member.alias, component_index,
                ))
    records.sort()
    active = []
    yielded = set()
    for record in records:
        west, east, south, north, _candidate_id, _alias, component_index = record
        active = [value for value in active if value[1] >= west]
        for other in active:
            (_other_west, _other_east, other_south, other_north,
             _other_candidate_id, _other_alias, other_index) = other
            if (component_index == other_index
                    or other_north < south or north < other_south):
                continue
            pair = tuple(sorted((component_index, other_index)))
            if pair in yielded:
                continue
            yielded.add(pair)
            if len(yielded) > MAX_SPATIAL_PAIRS:
                raise CensusError(
                    "possible-duplicate sweep exceeds the total "
                    f"{MAX_SPATIAL_PAIRS:,}-pair resource limit; latest="
                    f"{components[pair[0]].candidate_id},"
                    f"{components[pair[1]].candidate_id}"
                )
            yield pair
        active.append(record)


def proximity_review_groups(
        components: tuple[Component, ...]) -> tuple[dict[str, dict], int]:
    if not components:
        return {}, 0
    union = UnionFind(len(components))
    edge_count = 0
    for first, second in _candidate_pairs_by_bounds(components, CLUSTER_M):
        if component_distance_m(components[first], components[second]) <= CLUSTER_M:
            if union.union(first, second, "review proximity", reject_sidecar_bridge=False):
                edge_count += 1
    groups: dict[int, list[str]] = collections.defaultdict(list)
    for index, component in enumerate(components):
        groups[union.find(index)].append(component.candidate_id)
    by_candidate = {}
    for candidate_ids in groups.values():
        if len(candidate_ids) < 2:
            continue
        ordered = sorted(candidate_ids)
        group_id = "near-" + hashlib.sha256("\n".join(ordered).encode()).hexdigest()
        value = {"group_id": group_id, "candidate_ids": ordered}
        for candidate_id in ordered:
            by_candidate[candidate_id] = value
    return by_candidate, edge_count


def _verdict_projection(entry: dict | None) -> dict:
    if entry is None:
        return {"status": "UNMATCHED", "effect": "UNRESOLVED", "key": None}
    verdict = entry["verdict"]
    return {
        "status": verdict,
        "effect": {"KEEP": "ADD", "DROP": "REMOVE", "REVIEW": "HOLD"}[verdict],
        "key": entry["_key"],
    }


def _routing(kind: str, verdict: dict, access: dict) -> dict:
    if verdict["status"] == "REVIEW" or access["classification"] == "mixed_or_ambiguous":
        return {"bucket": "review_hold", "rank": 2}
    if verdict["status"] in ("KEEP", "DROP"):
        return {"bucket": "closed_trusted_decision", "rank": 3}
    if kind == "live_only":
        return {"bucket": "live_unmatched", "rank": 0}
    return {"bucket": "unmatched", "rank": 1}


def _member_output(member: ParkingFeature) -> dict:
    return {
        "osm": member.alias,
        "geometry": member.geometry,
        "representative": {
            "lat": round(member.latitude, 7),
            "lon": round(member.longitude, 7),
        },
        "tags": member.tags,
    }


def _service_output(bindings: list[dict]) -> dict:
    areas = sorted({binding["area"] for binding in bindings})
    jurisdictions = sorted({binding["jurisdiction"] for binding in bindings})
    return {
        "areas": areas,
        "jurisdictions": jurisdictions,
        "bindings": bindings,
        "nearest_binding": bindings[0] if bindings else None,
    }


def _owner(
        bindings: list[dict], verdict_entry: dict | None,
        shipped_areas: frozenset[str]) -> dict:
    if bindings:
        return {
            "jurisdiction": bindings[0]["jurisdiction"],
            "area": bindings[0]["area"],
        }
    area = verdict_entry.get("area") if verdict_entry is not None else None
    jurisdiction = code_from_slug(area) if isinstance(area, str) else None
    if jurisdiction in US_JURISDICTION_CODES and area in shipped_areas:
        return {"jurisdiction": jurisdiction, "area": area}
    return {"jurisdiction": UNASSIGNED_OWNER, "area": UNASSIGNED_OWNER}


def _tombstone_area_status(
        verdict_entry: dict, shipped_areas: frozenset[str]) -> str:
    area = verdict_entry.get("area")
    if not isinstance(area, str) or not area.strip():
        return "area_less"
    if area in shipped_areas:
        return "shipped"
    return "retired"


def _unspecified_access() -> dict:
    return {
        "classification": "unspecified",
        "values": [],
        "missing_member_count": 1,
        "hard_non_public": False,
    }


def _nearest_live_binding(
        pin: dict, endpoint_grid: EndpointGrid,
        trails_by_endpoint: dict[int, list[Trail]]) -> dict | None:
    best = None
    association_count = 0
    endpoint_ids = endpoint_grid.within_point(
        pin["lat"],
        pin["lon"],
        FALLBACK_M,
        limit=MAX_ENDPOINT_CANDIDATES_PER_FEATURE,
        context=f"live pin {pin['live_id']}",
    )
    for endpoint_id in endpoint_ids:
        endpoint = endpoint_grid.endpoints[endpoint_id]
        distance = haversine_m(
            pin["lat"], pin["lon"], endpoint.latitude, endpoint.longitude
        )
        for trail in trails_by_endpoint.get(endpoint_id, ()):
            association_count += 1
            if association_count > MAX_LIVE_TRAIL_ASSOCIATIONS:
                raise CensusError(
                    f"live pin {pin['live_id']} exceeds "
                    f"{MAX_LIVE_TRAIL_ASSOCIATIONS:,} exact trail associations"
                )
            value = (distance, trail.jurisdiction, trail.area, trail.trail_id, trail)
            if best is None or value[:-1] < best[:-1]:
                best = value
    if best is None:
        return None
    distance, _jurisdiction, _area, _trail_id, trail = best
    return {
        "area": trail.area,
        "jurisdiction": trail.jurisdiction,
        "trail_id": trail.trail_id,
        "trail_name": trail.name,
        "distance_m": round(distance, 3),
        "fallback": distance > NEAR_M,
        "projections": [],
    }


def build_work_units(
        selected_ids: set[str], components: tuple[Component, ...],
        synthetic_live: list[dict], scope: ScopeCapture,
        endpoint_grid: EndpointGrid,
        ledger: VerdictLedger) -> tuple[list[dict], dict]:
    selected_components = tuple(
        component for component in components
        if component.candidate_id in selected_ids or component.live_pins
    )
    shipped_areas = frozenset(scope.areas)
    current_candidate_ids = {
        component.candidate_id for component in selected_components
    } | {live_candidate_id(pin) for pin in synthetic_live}
    if set(ledger.assignments) - current_candidate_ids:
        raise AssertionError("verdict ledger contains a non-final candidate")
    proximity, proximity_edges = proximity_review_groups(selected_components)
    units = []
    for component in selected_components:
        access = _access_summary(component)
        verdict = _verdict_projection(component.verdict_entry)
        service = _service_output(component.bindings)
        selection_projections = sorted({
            projection
            for binding in component.bindings
            for projection in binding["projections"]
        }, key=PROJECTIONS.index)
        unit = {
            "candidate_id": component.candidate_id,
            "kind": "osm",
            "aliases": list(component.aliases),
            "members": [_member_output(member) for member in component.members],
            "live_pins": sorted(component.live_pins, key=lambda pin: pin["live_id"]),
            "access": access,
            "projection_eligibility": component.projection_eligibility,
            "selection_projections": selection_projections,
            "service": service,
            "owner": _owner(component.bindings, component.verdict_entry, shipped_areas),
            "verdict": verdict,
            "routing": _routing("osm", verdict, access),
            "possible_duplicate_group": proximity.get(component.candidate_id),
        }
        units.append(unit)

    trails_by_endpoint: dict[int, list[Trail]] = collections.defaultdict(list)
    for trail in scope.trails:
        for endpoint_id in trail.endpoint_ids:
            values = trails_by_endpoint[endpoint_id]
            values.append(trail)
            if len(values) > MAX_TRAILS_PER_ENDPOINT:
                raise CensusError(
                    f"endpoint {endpoint_id} exceeds {MAX_TRAILS_PER_ENDPOINT:,} "
                    "trail references"
                )
    for pin in synthetic_live:
        entry = pin.get("verdict_entry")
        verdict = _verdict_projection(entry)
        nearest = _nearest_live_binding(pin, endpoint_grid, trails_by_endpoint)
        bindings = [nearest] if nearest else []
        service = _service_output(bindings)
        access = _unspecified_access()
        public_pin = {
            key: value for key, value in pin.items()
            if key != "verdict_entry"
        }
        units.append({
            "candidate_id": live_candidate_id(pin),
            "kind": "live_only",
            "aliases": [],
            "members": [],
            "live_pins": [public_pin],
            "access": access,
            "projection_eligibility": {
                projection: {"eligible": False, "exclusions": ["live-only"]}
                for projection in PROJECTIONS
            },
            "selection_projections": [],
            "service": service,
            "owner": _owner(bindings, entry, shipped_areas),
            "verdict": verdict,
            "routing": _routing("live_only", verdict, access),
            "possible_duplicate_group": None,
        })

    for entry in ledger.tombstone_entries:
        verdict = _verdict_projection(entry)
        access = _unspecified_access()
        prior = {
            key: entry[key]
            for key in (
                "name", "area", "lat", "lon", "osm", "reason",
                "prior", "confidence", "judged",
            )
            if key in entry
        }
        units.append({
            "candidate_id": tombstone_id(entry),
            "kind": "verdict_tombstone",
            "aliases": [],
            "prior_verdict": prior,
            "prior_area_status": _tombstone_area_status(entry, shipped_areas),
            "members": [],
            "live_pins": [],
            "access": access,
            "projection_eligibility": {
                projection: {
                    "eligible": False,
                    "exclusions": ["unmatched-prior-verdict"],
                }
                for projection in PROJECTIONS
            },
            "selection_projections": [],
            "service": _service_output([]),
            "owner": _owner([], entry, shipped_areas),
            "verdict": verdict,
            "routing": _routing("verdict_tombstone", verdict, access),
            "possible_duplicate_group": None,
        })

    if len(units) > MAX_WORK_UNITS:
        raise CensusError(
            f"census has {len(units):,} work units; in-memory limit is "
            f"{MAX_WORK_UNITS:,}"
        )

    def sort_key(unit: dict):
        jurisdiction = unit["owner"]["jurisdiction"]
        return unit["routing"]["rank"], jurisdiction, unit["candidate_id"]

    units.sort(key=sort_key)
    return units, {
        "possible_duplicate_edges": proximity_edges,
        "possible_duplicate_groups": len({
            value["group_id"] for value in proximity.values()
        }),
        "indexed_candidate_clusters": len(selected_components),
    }


def _fsync_existing_file(path: Path) -> None:
    """Seal one generated artifact file to 0600 without following links."""
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(path, flags)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.geteuid() or before.st_nlink != 1):
            raise CensusError(
                f"artifact {path.name} is not an operator-owned single-link file"
            )
        os.fchmod(fd, 0o600)
        os.fsync(fd)
        after = os.fstat(fd)
        entry = os.stat(path, follow_symlinks=False)
        stable_fields = lambda value: (
            value.st_dev, value.st_ino, value.st_uid, value.st_gid,
            value.st_nlink, value.st_size, value.st_mtime_ns,
        )
        if (stable_fields(before) != stable_fields(after)
                or _signature(after) != _signature(entry)
                or stat.S_IMODE(after.st_mode) != 0o600):
            raise CensusError(
                f"artifact {path.name} changed while it was sealed"
            )
    finally:
        os.close(fd)


def _exclusive_write(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(path, flags, 0o600)
    try:
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("output write made no progress")
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)


def _verify_staged_file(path: Path, expected: bytes) -> None:
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(path, flags)
    try:
        before = _signature(os.fstat(fd))
        if not stat.S_ISREG(before.mode) or before.size != len(expected):
            raise CensusError(f"staged file {path.name} has the wrong type or size")
        chunks = []
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = _signature(os.fstat(fd))
        data = b"".join(chunks)
        if before != after or data != expected:
            raise CensusError(f"staged file {path.name} failed read-back verification")
    finally:
        os.close(fd)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _raise_exclusive_rename_error(error_number: int, destination: Path) -> None:
    if error_number in (errno.EEXIST, errno.ENOTEMPTY):
        raise ArtifactDestinationExists(
            f"output destination appeared before promotion: {destination.name}"
        )
    unsupported = {
        errno.EINVAL,
        getattr(errno, "ENOSYS", -1),
        getattr(errno, "ENOTSUP", -1),
        getattr(errno, "EOPNOTSUPP", -1),
    }
    if error_number in unsupported:
        raise CensusError(
            "platform/filesystem lacks a supported exclusive atomic "
            "directory-rename primitive"
        )
    raise OSError(error_number, os.strerror(error_number), str(destination))


def _rename_stage_exclusive(stage: Path, destination: Path) -> None:
    """Atomically promote one directory only while destination is absent."""
    libc = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(stage)
    destination_bytes = os.fsencode(destination)
    if sys.platform == "darwin" and hasattr(libc, "renamex_np"):
        renamex_np = libc.renamex_np
        renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        renamex_np.restype = ctypes.c_int
        result = renamex_np(source_bytes, destination_bytes, 0x00000004)
    elif hasattr(libc, "renameat2"):
        renameat2 = libc.renameat2
        renameat2.argtypes = [
            ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
            ctypes.c_char_p, ctypes.c_uint,
        ]
        renameat2.restype = ctypes.c_int
        result = renameat2(-100, source_bytes, -100, destination_bytes, 0x1)
    else:
        raise CensusError(
            "platform lacks an exclusive atomic directory-rename primitive"
        )
    if result != 0:
        _raise_exclusive_rename_error(ctypes.get_errno(), destination)


def write_outputs(
        output_dir: str | Path, work_units: list[dict], manifest: dict,
        shard_size: int, verify_sources: Callable[[], None]) -> dict:
    if not MIN_SHARD_SIZE <= shard_size <= MAX_SHARD_SIZE:
        raise CensusError(
            f"shard size must be in [{MIN_SHARD_SIZE}, {MAX_SHARD_SIZE}]"
        )
    destination = Path(output_dir).absolute()
    if os.path.lexists(destination):
        raise CensusError(f"output destination already exists: {destination}")
    if not destination.parent.is_dir():
        raise CensusError(f"output parent does not exist: {destination.parent}")
    _assert_manifest_closure(manifest, work_units)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stage = Path(tempfile.mkdtemp(
        prefix=f".{destination.name}.stage-{timestamp}-", dir=destination.parent
    ))
    os.chmod(stage, 0o700)
    promoted = False
    try:
        shards = []
        total_bytes = 0
        for shard_index, start in enumerate(range(0, len(work_units), shard_size)):
            rows = work_units[start:start + shard_size]
            data = b"".join(_canonical_bytes(row) for row in rows)
            name = f"work-units-{shard_index:05d}.jsonl"
            path = stage / name
            _exclusive_write(path, data)
            _verify_staged_file(path, data)
            descriptor = {
                "path": name,
                "length": len(rows),
                "size_bytes": len(data),
                "sha256": _sha256(data),
            }
            shards.append(descriptor)
            total_bytes += len(data)
        manifest = dict(manifest)
        manifest["shards"] = shards
        manifest["counts"] = dict(manifest["counts"])
        manifest["counts"]["shards"] = len(shards)
        manifest["counts"]["output_jsonl_bytes"] = total_bytes
        manifest["publication"] = {
            "mode": "same-filesystem-exclusive-atomic-directory-rename",
            "staged_files_fsynced_and_read_back": True,
            "source_revalidation_before_promotion": True,
            "parent_directory_fsync_required_after_promotion": True,
            "durability_success_record": "publication-receipt.json",
            "failed_stages_preserved_for_operator_archive": True,
            "automatic_stage_deletion": False,
        }
        if sum(shard["length"] for shard in shards) != len(work_units):
            raise AssertionError("shard row counts do not reconcile")
        if sum(shard["size_bytes"] for shard in shards) != total_bytes:
            raise AssertionError("shard byte counts do not reconcile")
        _assert_manifest_closure(manifest, work_units)
        manifest["root_hash_algorithm"] = (
            "sha256(canonical-manifest-without-root_sha256)"
        )
        manifest["root_sha256"] = _canonical_hash(manifest)
        manifest_bytes = _canonical_bytes(manifest)
        manifest_path = stage / "manifest.json"
        _exclusive_write(manifest_path, manifest_bytes)
        _verify_staged_file(manifest_path, manifest_bytes)
        _fsync_directory(stage)
        verify_sources()
        _rename_stage_exclusive(stage, destination)
        promoted = True
        _fsync_directory(destination.parent)
        receipt_payload = {
            "schema": PUBLICATION_RECEIPT_SCHEMA,
            "self_hash_algorithm": PRODUCTION_BASELINE_SELF_HASH,
            "manifest_root_sha256": manifest["root_sha256"],
            "manifest_sha256": _sha256(manifest_bytes),
            "promotion_parent_directory_fsync_completed": True,
        }
        receipt = _self_hashed_document(receipt_payload)
        receipt_bytes = _canonical_bytes(receipt)
        receipt_path = destination / "publication-receipt.json"
        _exclusive_write(receipt_path, receipt_bytes)
        _verify_staged_file(receipt_path, receipt_bytes)
        _fsync_directory(destination)
        return {
            "manifest": manifest,
            "manifest_bytes": len(manifest_bytes),
            "jsonl_bytes": total_bytes,
            "publication_receipt": receipt,
            "publication_receipt_bytes": len(receipt_bytes),
            "durability_confirmed": True,
            "total_bytes": total_bytes + len(manifest_bytes) + len(receipt_bytes),
        }
    except Exception as error:
        if isinstance(error, CensusError) and str(error).startswith(
                "output destination already exists"):
            raise
        if promoted:
            raise CensusError(
                f"output was promoted to {destination.name}, but no durable "
                "publication success may be claimed; verify in place and recover "
                "the publication receipt or move the directory to an operator-owned "
                f"Archive before retrying: {error}"
            ) from error
        raise CensusError(
            f"output publication failed; diagnostic stage {stage.name} preserved. "
            "Move it to an operator-owned Archive after diagnosis; code never "
            f"deletes or prunes stages: {error}"
        ) from error


def _source_manifest(
        scope: ScopeCapture, parking: ParkingStreamResult,
        live: LiveCapture, verdicts: VerdictCapture, parking_kind: str,
        baseline: ProductionBaseline | None) -> dict:
    geom_files = [
        {**binding.portable(), "area": area}
        for area, binding in zip(scope.areas, scope.geom_bindings)
    ]
    geometry_authority_sha256 = _geometry_authority_sha256(
        scope.geom_bindings
    )
    seen_authority_aliases = tuple(parking.seen_authority_aliases)
    if seen_authority_aliases != tuple(sorted(set(seen_authority_aliases))):
        raise AssertionError("seen authority aliases are not sorted and unique")
    if not set(seen_authority_aliases).issubset(
            verdicts.verdicts.osm_aliases):
        raise AssertionError("seen authority aliases are not a sidecar intersection")
    if parking.counters["seen_authority_aliases"] != len(
            seen_authority_aliases):
        raise AssertionError("parking stream seen-authority count did not reconcile")
    parking_source = {
        **parking.binding.portable(),
        "kind": parking_kind,
        "osm_metadata": parking.metadata,
        "inventory": parking.inventory,
        "seen_authority_aliases": {
            "count": len(seen_authority_aliases),
            "sha256": _canonical_hash(list(seen_authority_aliases)),
        },
    }
    if parking.osmium_binding is not None and parking.osmium is not None:
        parking_source["osmium"] = {
            **parking.osmium_binding.portable(),
            **parking.osmium,
        }
    if parking.filtered_binding is not None:
        parking_source["filtered_pbf"] = parking.filtered_binding.portable()
    if parking.filtered_manifest_binding is not None:
        parking_source["filtered_artifact_manifest"] = (
            parking.filtered_manifest_binding.portable()
        )
    sources = {
        "bundle": scope.bundle_binding.portable(),
        "geometry": {
            "directory": _portable_directory_label(
                scope.geom_inventory.path, scope.geom_bindings, "geometry"
            ),
            "files": geom_files,
            "portable_inventory_sha256": _canonical_hash(geom_files),
            "authority_sha256": geometry_authority_sha256,
            "area_slugs_sha256": _area_slugs_sha256(scope.areas),
        },
        "parking": parking_source,
        "live_pool": live.binding.portable(),
        "verdicts": verdicts.binding.portable(),
    }
    if baseline is not None:
        if baseline.registry_binding is None or baseline.approval is None:
            raise AssertionError("authoritative baseline has no reviewed approval")
        sources["production_baseline"] = {
            **baseline.binding.portable(),
            "schema": PRODUCTION_BASELINE_SCHEMA,
            "version": baseline.document["version"],
            "name": baseline.document["name"],
            "predecessor_self_sha256": baseline.document.get(
                "predecessor_self_sha256"
            ),
            "self_sha256": baseline.self_sha256,
        }
        sources["production_baseline_registry"] = {
            **baseline.registry_binding.portable(),
            "schema": APPROVED_BASELINE_REGISTRY_SCHEMA,
        }
    return sources


def _count_manifest(
        scope: ScopeCapture, parking: ParkingStreamResult,
        components: tuple[Component, ...], work_units: list[dict],
        live: LiveCapture, synthetic_live: list[dict],
        ledger: VerdictLedger) -> tuple[dict, dict, dict]:
    candidates = [
        unit for unit in work_units if unit["kind"] != "verdict_tombstone"
    ]
    tombstones = [
        unit for unit in work_units if unit["kind"] == "verdict_tombstone"
    ]
    matched = len(ledger.matched_keys)
    exact_remaining = len(candidates) - matched
    if exact_remaining < 0:
        raise AssertionError("matched verdicts exceed current candidate clusters")
    counts = {
        "areas": len(scope.areas),
        "jurisdictions": len(scope.jurisdictions),
        "excluded_non_us_areas": scope.excluded_non_us_areas,
        "trails": len(scope.trails),
        "trails_without_source_id": scope.trails_without_source_id,
        "anonymous_trail_refs": len(scope.anonymous_trail_refs),
        "trails_without_endpoints": scope.trails_without_endpoints,
        "endpoint_occurrences": scope.endpoint_occurrences,
        "unique_endpoints": len(scope.endpoints),
        "retained_osm_features": len(parking.features),
        "canonical_components": len(components),
        "live_pins": len(live.pins),
        "live_only_candidate_clusters": len(synthetic_live),
        "current_candidate_clusters": len(candidates),
        "uniquely_matched_verdict_clusters": matched,
        "exact_remaining_denominator": exact_remaining,
        "exact_remaining": exact_remaining,
        "verdict_tombstones": len(tombstones),
        "sidecar_verdict_clusters": matched + len(tombstones),
        "gross_work_units": len(work_units),
        "work_units": len(work_units),
    }
    routing = collections.Counter(unit["routing"]["bucket"] for unit in work_units)
    for bucket in (
            "live_unmatched", "unmatched", "review_hold",
            "closed_trusted_decision"):
        counts[f"routing_{bucket}"] = routing[bucket]
    verdict_counts = collections.Counter(
        unit["verdict"]["status"] for unit in work_units
    )
    candidate_verdict_counts = collections.Counter(
        unit["verdict"]["status"] for unit in candidates
    )
    tombstone_verdict_counts = collections.Counter(
        unit["verdict"]["status"] for unit in tombstones
    )
    for verdict in ("UNMATCHED", "KEEP", "DROP", "REVIEW"):
        normalized = verdict.lower()
        counts[f"verdict_{normalized}"] = verdict_counts[verdict]
        counts[f"candidate_verdict_{normalized}"] = candidate_verdict_counts[verdict]
        counts[f"tombstone_verdict_{normalized}"] = tombstone_verdict_counts[verdict]
    counts["binary_verdict_candidate_clusters"] = (
        candidate_verdict_counts["KEEP"] + candidate_verdict_counts["DROP"]
    )
    counts["review_verdict_candidate_clusters"] = candidate_verdict_counts["REVIEW"]
    counts["open_review_candidate_clusters"] = sum(
        unit["routing"]["bucket"] != "closed_trusted_decision"
        for unit in candidates
    )
    counts["tombstone_binary_verdict_clusters"] = (
        tombstone_verdict_counts["KEEP"] + tombstone_verdict_counts["DROP"]
    )
    counts["tombstone_review_verdict_clusters"] = tombstone_verdict_counts["REVIEW"]

    owner_codes = list(scope.jurisdictions) + [UNASSIGNED_OWNER]
    owners = {
        code: {
            "areas": (
                sum(code_from_slug(area) == code for area in scope.areas)
                if code != UNASSIGNED_OWNER else 0
            ),
            "candidate_clusters": 0,
            "tombstones": 0,
            "total_work_units": 0,
            "unmatched_candidates": 0,
            "review_holds": 0,
            "binary_verdict_candidates": 0,
            "open_review_candidates": 0,
            "candidate_verdict_keep": 0,
            "candidate_verdict_drop": 0,
            "candidate_verdict_review": 0,
            "candidate_verdict_unmatched": 0,
            "tombstone_verdict_keep": 0,
            "tombstone_verdict_drop": 0,
            "tombstone_verdict_review": 0,
        }
        for code in owner_codes
    }
    referenced_service = {
        code: {"candidate_clusters": 0, "area_references": 0}
        for code in scope.jurisdictions
    }
    scope_area_set = frozenset(scope.areas)
    for unit in work_units:
        owner = unit.get("owner")
        if not isinstance(owner, dict):
            raise AssertionError(f"work unit {unit['candidate_id']} has no owner")
        jurisdiction = owner.get("jurisdiction")
        area = owner.get("area")
        if jurisdiction not in owners:
            raise AssertionError(
                f"work unit {unit['candidate_id']} has invalid owner {jurisdiction!r}"
            )
        if jurisdiction == UNASSIGNED_OWNER:
            if area != UNASSIGNED_OWNER:
                raise AssertionError("unassigned owner must use unassigned area sentinel")
        elif code_from_slug(area) != jurisdiction or area not in scope_area_set:
            raise AssertionError(
                f"owner area {area!r} is not a shipped area for {jurisdiction}"
            )
        values = owners[jurisdiction]
        values["total_work_units"] += 1
        verdict_status = unit["verdict"]["status"]
        verdict_suffix = verdict_status.lower()
        if unit["kind"] == "verdict_tombstone":
            values["tombstones"] += 1
            if verdict_status in ("KEEP", "DROP", "REVIEW"):
                values[f"tombstone_verdict_{verdict_suffix}"] += 1
        else:
            values["candidate_clusters"] += 1
            values["unmatched_candidates"] += verdict_status == "UNMATCHED"
            values[f"candidate_verdict_{verdict_suffix}"] += 1
            values["binary_verdict_candidates"] += verdict_status in ("KEEP", "DROP")
            values["open_review_candidates"] += (
                unit["routing"]["bucket"] != "closed_trusted_decision"
            )
        values["review_holds"] += unit["routing"]["bucket"] == "review_hold"
        if unit["kind"] != "verdict_tombstone":
            for code in unit["service"]["jurisdictions"]:
                referenced_service[code]["candidate_clusters"] += 1
            for area_name in unit["service"]["areas"]:
                code = code_from_slug(area_name)
                if code not in referenced_service:
                    raise AssertionError(
                        f"service area {area_name!r} has no US jurisdiction"
                    )
                referenced_service[code]["area_references"] += 1

    owner_candidate_total = sum(
        values["candidate_clusters"] for values in owners.values()
    )
    owner_tombstone_total = sum(values["tombstones"] for values in owners.values())
    owner_total = sum(values["total_work_units"] for values in owners.values())
    routing_total = sum(routing.values())
    verdict_total = sum(verdict_counts.values())
    if owner_candidate_total != len(candidates):
        raise AssertionError("owner candidate totals do not reconcile")
    if owner_tombstone_total != len(tombstones):
        raise AssertionError("owner tombstone totals do not reconcile")
    if owner_total != len(work_units):
        raise AssertionError("owner work-unit totals do not reconcile")
    if routing_total != len(work_units):
        raise AssertionError("routing buckets do not reconcile")
    if verdict_total != len(work_units):
        raise AssertionError("verdict statuses do not reconcile")
    matched_candidate_total = sum(
        unit["verdict"]["status"] != "UNMATCHED" for unit in candidates
    )
    if matched_candidate_total != matched:
        raise AssertionError("matched candidate verdict totals do not reconcile")
    if len(tombstones) != len(ledger.tombstone_entries):
        raise AssertionError("verdict tombstone totals do not reconcile")
    owner_metric_expectations = {
        "binary_verdict_candidates": counts["binary_verdict_candidate_clusters"],
        "open_review_candidates": counts["open_review_candidate_clusters"],
        **{
            f"candidate_verdict_{verdict.lower()}": candidate_verdict_counts[verdict]
            for verdict in ("UNMATCHED", "KEEP", "DROP", "REVIEW")
        },
        **{
            f"tombstone_verdict_{verdict.lower()}": tombstone_verdict_counts[verdict]
            for verdict in ("KEEP", "DROP", "REVIEW")
        },
    }
    for metric, expected in owner_metric_expectations.items():
        observed = sum(values[metric] for values in owners.values())
        if observed != expected:
            raise AssertionError(f"owner {metric} totals do not reconcile")
    counts["unassigned_work_units"] = owners[UNASSIGNED_OWNER][
        "total_work_units"
    ]
    counts["area_less_tombstones"] = sum(
        unit["owner"]["jurisdiction"] == UNASSIGNED_OWNER
        and unit.get("prior_area_status") == "area_less"
        for unit in tombstones
    )
    counts["retired_area_tombstones"] = sum(
        unit["owner"]["jurisdiction"] == UNASSIGNED_OWNER
        and unit.get("prior_area_status") == "retired"
        for unit in tombstones
    )
    counts["unassigned_candidate_clusters"] = sum(
        unit["owner"]["jurisdiction"] == UNASSIGNED_OWNER
        for unit in candidates
    )
    if counts["unassigned_work_units"] != (
            counts["area_less_tombstones"]
            + counts["retired_area_tombstones"]
            + counts["unassigned_candidate_clusters"]):
        raise AssertionError("unassigned work-unit categories did not reconcile")
    counts["equations"] = {
        "exact_remaining_denominator": (
            "current_candidate_clusters - uniquely_matched_verdict_clusters"
        ),
        "exact_remaining": (
            "current_candidate_clusters - uniquely_matched_verdict_clusters"
        ),
        "gross": "current_candidate_clusters + verdict_tombstones",
        "verdict_ledger": (
            "uniquely_matched_verdict_clusters + verdict_tombstones"
        ),
        "owners": "sum(owner.total_work_units)",
        "routing": "sum(routing buckets)",
        "candidate_verdict_split": (
            "candidate_unmatched + candidate_keep + candidate_drop + candidate_review"
        ),
        "unassigned_split": (
            "area_less_tombstones + retired_area_tombstones + "
            "unassigned_candidate_clusters"
        ),
        "open_review": "candidate routing bucket is not closed_trusted_decision",
    }
    counts["equation_values"] = {
        "exact_remaining_denominator": [
            len(candidates), matched, exact_remaining
        ],
        "exact_remaining": [len(candidates), matched, exact_remaining],
        "gross": [len(candidates), len(tombstones), len(work_units)],
        "verdict_ledger": [
            matched, len(tombstones), matched + len(tombstones)
        ],
        "owners": [owner_total, len(work_units)],
        "routing": [routing_total, len(work_units)],
        "candidate_verdict_split": [
            candidate_verdict_counts["UNMATCHED"],
            candidate_verdict_counts["KEEP"],
            candidate_verdict_counts["DROP"],
            candidate_verdict_counts["REVIEW"],
            len(candidates),
        ],
        "unassigned_split": [
            counts["area_less_tombstones"],
            counts["retired_area_tombstones"],
            counts["unassigned_candidate_clusters"],
            counts["unassigned_work_units"],
        ],
        "open_review": [counts["open_review_candidate_clusters"]],
    }
    return counts, owners, referenced_service


def _assert_stream_complete(parking: ParkingStreamResult) -> None:
    if parking.counters["rejected_total"]:
        raise CensusError(
            "parking stream rejected records of unknown or malformed relevance; "
            f"complete census refused: {parking.counters['rejections']}"
        )


def _deterministic_run_id(sources: dict, algorithm: dict) -> str:
    authority = {
        "bundle": sources["bundle"]["sha256"],
        "geometry": sources["geometry"]["authority_sha256"],
        "parking_kind": sources["parking"]["kind"],
        "parking": sources["parking"]["sha256"],
        "live_pool": sources["live_pool"]["sha256"],
        "verdicts": sources["verdicts"]["sha256"],
        "production_baseline": (
            sources.get("production_baseline", {}).get("sha256")
        ),
        "production_baseline_self": (
            sources.get("production_baseline", {}).get("self_sha256")
        ),
        "production_baseline_registry": (
            sources.get("production_baseline_registry", {}).get("sha256")
        ),
        "filtered_pbf": (
            sources["parking"].get("filtered_pbf", {}).get("sha256")
        ),
        "filtered_artifact_manifest": (
            sources["parking"].get(
                "filtered_artifact_manifest", {}
            ).get("sha256")
        ),
        "parking_inventory": _canonical_hash(
            sources["parking"].get("inventory", {})
        ),
        "seen_authority_aliases": sources["parking"].get(
            "seen_authority_aliases"
        ),
        "osmium": (
            sources["parking"].get("osmium", {}).get("sha256")
        ),
        "osmium_version": (
            sources["parking"].get("osmium", {}).get("version")
        ),
    }
    return "pcr1:" + _canonical_hash({
        "schema": SCHEMA_ID,
        "authority": authority,
        "policy": algorithm,
    })


def _assert_manifest_closure(manifest: dict, work_units: list[dict]) -> None:
    if manifest.get("kind") != "national_parking_census":
        raise AssertionError("manifest kind is not explicit")
    if manifest.get("schema") != SCHEMA_ID:
        raise AssertionError("manifest schema is not explicit")
    authorization = manifest.get("production_authorization")
    if not isinstance(authorization, dict):
        raise AssertionError("manifest has no production authorization mode")
    authoritative = authorization.get("authoritative") is True
    expected_status = "complete" if authoritative else "non_authoritative"
    if manifest.get("status") != expected_status:
        raise AssertionError("manifest status does not match production authorization")
    if authoritative:
        if manifest["sources"]["parking"].get("kind") != "pbf":
            raise AssertionError("only PBF mode can be production authoritative")
        if "production_baseline" not in manifest["sources"]:
            raise AssertionError("authoritative manifest has no production baseline")
        if "production_baseline_registry" not in manifest["sources"]:
            raise AssertionError("authoritative manifest has no approved baseline registry")
        if authorization.get("baseline_self_sha256") != manifest[
                "sources"]["production_baseline"]["self_sha256"]:
            raise AssertionError("production authorization baseline binding is wrong")
    elif ("production_baseline" in manifest["sources"]
          or "production_baseline_registry" in manifest["sources"]):
        raise AssertionError("non-authoritative manifest cannot claim a baseline")
    if manifest.get("run_id") != _deterministic_run_id(
            manifest["sources"], manifest["algorithm"]):
        raise AssertionError("manifest run_id does not bind authority and policy")
    counts = manifest["counts"]
    candidates = counts["current_candidate_clusters"]
    matched = counts["uniquely_matched_verdict_clusters"]
    tombstones = counts["verdict_tombstones"]
    gross = counts["gross_work_units"]
    if counts["exact_remaining_denominator"] != candidates - matched:
        raise AssertionError("exact remaining denominator equation did not close")
    if counts["exact_remaining"] != candidates - matched:
        raise AssertionError("exact remaining equation did not close")
    if gross != candidates + tombstones or gross != len(work_units):
        raise AssertionError("gross work-unit equation did not close")
    if counts["sidecar_verdict_clusters"] != matched + tombstones:
        raise AssertionError("verdict ledger equation did not close")
    if counts["unassigned_work_units"] != (
            counts["area_less_tombstones"]
            + counts["retired_area_tombstones"]
            + counts["unassigned_candidate_clusters"]):
        raise AssertionError("unassigned category equation did not close")
    candidate_units = [
        unit for unit in work_units if unit["kind"] != "verdict_tombstone"
    ]
    tombstone_units = [
        unit for unit in work_units if unit["kind"] == "verdict_tombstone"
    ]
    if len(candidate_units) != candidates or len(tombstone_units) != tombstones:
        raise AssertionError("work-unit kind counts do not reconcile")
    actual_unassigned_candidates = sum(
        unit["owner"]["jurisdiction"] == UNASSIGNED_OWNER
        for unit in candidate_units
    )
    actual_area_less_tombstones = sum(
        unit["owner"]["jurisdiction"] == UNASSIGNED_OWNER
        and unit.get("prior_area_status") == "area_less"
        for unit in tombstone_units
    )
    actual_retired_area_tombstones = sum(
        unit["owner"]["jurisdiction"] == UNASSIGNED_OWNER
        and unit.get("prior_area_status") == "retired"
        for unit in tombstone_units
    )
    if counts["unassigned_candidate_clusters"] != actual_unassigned_candidates:
        raise AssertionError("unassigned candidate count does not reconcile")
    if counts["area_less_tombstones"] != actual_area_less_tombstones:
        raise AssertionError("area-less tombstone count does not reconcile")
    if counts["retired_area_tombstones"] != actual_retired_area_tombstones:
        raise AssertionError("retired-area tombstone count does not reconcile")
    matched_units = sum(
        unit["verdict"]["status"] != "UNMATCHED" for unit in candidate_units
    )
    if matched_units != matched:
        raise AssertionError("matched verdict work units do not reconcile")
    ledger = manifest["verdict_ledger"]
    if (ledger["matched"] != matched or ledger["tombstones"] != tombstones
            or ledger["sidecar_rows"] != matched + tombstones):
        raise AssertionError("manifest verdict ledger counts do not reconcile")
    seen_authority = manifest["sources"]["parking"].get(
        "seen_authority_aliases"
    )
    if (not isinstance(seen_authority, dict)
            or set(seen_authority) != {"count", "sha256"}
            or isinstance(seen_authority["count"], bool)
            or not isinstance(seen_authority["count"], int)
            or seen_authority["count"] < 0
            or not isinstance(seen_authority["sha256"], str)
            or re.fullmatch(r"[0-9a-f]{64}", seen_authority["sha256"])
            is None):
        raise AssertionError("seen authority alias evidence is malformed")
    if seen_authority["count"] != manifest["parking_stream"][
            "seen_authority_aliases"]:
        raise AssertionError("parking stream seen-authority count does not reconcile")
    if (seen_authority["count"] != ledger["seen_authority_aliases"]
            or seen_authority["sha256"]
            != ledger["seen_authority_aliases_sha256"]):
        raise AssertionError("verdict ledger seen-authority evidence does not reconcile")
    if (ledger["seen_authority_keys"] > ledger["seen_authority_aliases"]
            or ledger["seen_authority_keys"]
            != ledger["reserved_authoritative"]):
        raise AssertionError("seen exact authority keys were not globally reserved")
    identifiers = [unit["candidate_id"] for unit in work_units]
    if len(identifiers) != len(set(identifiers)):
        raise AssertionError("work-unit identifiers are not unique")
    for unit in work_units:
        if unit["kind"] == "osm":
            expected = candidate_id_for_aliases(unit["aliases"])
            if unit["candidate_id"] != expected:
                raise AssertionError("OSM candidate id is not canonical pc1")
        if unit["kind"] == "verdict_tombstone":
            status = unit.get("prior_area_status")
            if status not in {"shipped", "retired", "area_less"}:
                raise AssertionError("verdict tombstone area status is invalid")
            if (status == "retired"
                    and unit["owner"]["jurisdiction"] != UNASSIGNED_OWNER):
                raise AssertionError("retired-area tombstone must be unassigned")
        owner = unit.get("owner", {})
        if owner.get("jurisdiction") is None or owner.get("area") is None:
            raise AssertionError("work unit has no deterministic owner")
    owner_codes = set(manifest["owners"])
    expected_owner_codes = set(US_JURISDICTION_CODES) | {UNASSIGNED_OWNER}
    if owner_codes != expected_owner_codes:
        raise AssertionError(
            "manifest owners are not exactly all 51 US codes plus sentinel"
        )
    routing_counts = collections.Counter(
        unit["routing"]["bucket"] for unit in work_units
    )
    for bucket in (
            "live_unmatched", "unmatched", "review_hold",
            "closed_trusted_decision"):
        if counts[f"routing_{bucket}"] != routing_counts[bucket]:
            raise AssertionError(f"routing bucket {bucket} does not reconcile")
    verdict_counts = collections.Counter(
        unit["verdict"]["status"] for unit in work_units
    )
    candidate_verdict_counts = collections.Counter(
        unit["verdict"]["status"] for unit in candidate_units
    )
    tombstone_verdict_counts = collections.Counter(
        unit["verdict"]["status"] for unit in tombstone_units
    )
    for verdict in ("UNMATCHED", "KEEP", "DROP", "REVIEW"):
        suffix = verdict.lower()
        if counts[f"verdict_{suffix}"] != verdict_counts[verdict]:
            raise AssertionError(f"verdict bucket {verdict} does not reconcile")
        if counts[f"candidate_verdict_{suffix}"] != candidate_verdict_counts[verdict]:
            raise AssertionError(
                f"candidate verdict bucket {verdict} does not reconcile"
            )
        if counts[f"tombstone_verdict_{suffix}"] != tombstone_verdict_counts[verdict]:
            raise AssertionError(
                f"tombstone verdict bucket {verdict} does not reconcile"
            )
    if counts["binary_verdict_candidate_clusters"] != (
            candidate_verdict_counts["KEEP"] + candidate_verdict_counts["DROP"]):
        raise AssertionError("binary candidate verdict total does not reconcile")
    if counts["review_verdict_candidate_clusters"] != candidate_verdict_counts["REVIEW"]:
        raise AssertionError("review candidate verdict total does not reconcile")
    if counts["open_review_candidate_clusters"] != sum(
            unit["routing"]["bucket"] != "closed_trusted_decision"
            for unit in candidate_units):
        raise AssertionError("open-review candidate total does not reconcile")
    owner_counts = collections.Counter(
        unit["owner"]["jurisdiction"] for unit in work_units
    )
    for code, row in manifest["owners"].items():
        if row["total_work_units"] != owner_counts[code]:
            raise AssertionError(f"owner bucket {code} does not reconcile")
    owner_total = sum(
        row["total_work_units"] for row in manifest["owners"].values()
    )
    if owner_total != gross:
        raise AssertionError("manifest owner equation did not close")
    if "parent_directory_fsynced_after_promotion" in manifest.get(
            "publication", {}):
        raise AssertionError("manifest predeclares an unperformed durability outcome")
    source_sections = ["bundle", "live_pool", "verdicts"]
    if "production_baseline" in manifest["sources"]:
        source_sections.extend([
            "production_baseline", "production_baseline_registry"
        ])
    for section in source_sections:
        if Path(manifest["sources"][section]["path"]).is_absolute():
            raise AssertionError("manifest contains an absolute authority path")
    geometry = manifest["sources"]["geometry"]
    geometry_files = geometry["files"]
    if Path(geometry["directory"]).is_absolute() or any(
            Path(row["path"]).is_absolute() for row in geometry_files):
        raise AssertionError("manifest contains an absolute geometry path")
    geometry_areas = [row.get("area") for row in geometry_files]
    if (len(geometry_areas) != len(set(geometry_areas))
            or any(not isinstance(area, str) or not area for area in geometry_areas)):
        raise AssertionError("geometry provenance does not bind one unique area per file")
    parking_source = manifest["sources"]["parking"]
    if Path(parking_source["path"]).is_absolute():
        raise AssertionError("manifest contains an absolute parking path")
    for section in ("osmium", "filtered_pbf", "filtered_artifact_manifest"):
        if section in parking_source and Path(
                parking_source[section]["path"]).is_absolute():
            raise AssertionError(f"manifest contains an absolute {section} path")


def run_census(
        *, bundle_path: str | Path, geom_dir: str | Path,
        parking_path: str | Path, parking_kind: str,
        live_pool_path: str | Path, verdicts_path: str | Path,
        output_dir: str | Path, shard_size: int = DEFAULT_SHARD_SIZE,
        authoritative: bool = False,
        production_baseline_path: str | Path | None = None,
        pbf_artifact_dir: str | Path | None = None,
        osmium_executable: str | Path | None = None,
        expected_osmium_sha256: str | None = None,
        enforce_national_pbf_floor: bool | None = None,
        minimum_artifact_free_bytes: int = MIN_PBF_ARTIFACT_FREE_BYTES,
        osmium_timeout_s: float | None = None,
) -> dict:
    destination = Path(output_dir).absolute()
    if not isinstance(authoritative, bool):
        raise CensusError("authoritative mode must be a boolean")
    if (enforce_national_pbf_floor is not None
            and not isinstance(enforce_national_pbf_floor, bool)):
        raise CensusError("national PBF floor mode must be a boolean or null")
    if os.path.lexists(destination):
        raise CensusError(f"output destination already exists: {destination}")
    if not MIN_SHARD_SIZE <= shard_size <= MAX_SHARD_SIZE:
        raise CensusError(
            f"shard size must be in [{MIN_SHARD_SIZE}, {MAX_SHARD_SIZE}]"
        )
    if authoritative:
        if parking_kind != "pbf":
            raise CensusError("only PBF mode can request authoritative completion")
        if production_baseline_path is None:
            raise CensusError("authoritative PBF mode requires a production baseline")
        if osmium_executable is None or expected_osmium_sha256 is None:
            raise CensusError(
                "authoritative PBF mode requires absolute --osmium and expected SHA-256"
            )
        if enforce_national_pbf_floor is False:
            raise CensusError("authoritative PBF mode cannot disable the national floor")
        enforce_floor = True
    else:
        if production_baseline_path is not None:
            raise CensusError(
                "a production baseline may only be used with authoritative PBF mode"
            )
        enforce_floor = bool(enforce_national_pbf_floor)
    if parking_kind == "pbf" and pbf_artifact_dir is None:
        raise CensusError("PBF mode requires an operator-owned artifact directory")
    if parking_kind != "pbf" and any(value is not None for value in (
            pbf_artifact_dir, osmium_executable, expected_osmium_sha256,
            osmium_timeout_s)):
        raise CensusError("PBF artifact and osmium options require PBF mode")
    effective_osmium_timeout = _validated_osmium_timeout(osmium_timeout_s)

    geometry_engine = _geometry_engine_manifest()
    scope = capture_scope(bundle_path, geom_dir)
    endpoint_grid = EndpointGrid(scope.endpoints)
    verdict_capture = _load_verdicts(verdicts_path)
    live = _load_live_pool(live_pool_path)
    baseline = None
    if authoritative:
        baseline = _load_production_baseline(
            production_baseline_path, require_approved=True
        )
        _validate_production_baseline(
            baseline, scope, live, verdict_capture
        )
    parking = stream_parking_source(
        parking_path,
        parking_kind,
        endpoint_grid,
        sidecar_aliases=verdict_capture.verdicts.osm_aliases,
        pbf_artifact_dir=pbf_artifact_dir,
        enforce_national_pbf_floor=enforce_floor,
        osmium_executable=osmium_executable,
        expected_osmium_sha256=expected_osmium_sha256,
        authoritative=authoritative,
        minimum_artifact_free_bytes=minimum_artifact_free_bytes,
        osmium_timeout_s=effective_osmium_timeout,
    )
    _assert_stream_complete(parking)
    components, identity_edges = build_components(
        parking.features, verdict_capture.verdicts
    )
    live_identity = {}
    synthetic_live = attach_live_pins(
        components, live.pins, verdict_capture.verdicts, live_identity
    )
    service_graph = build_service_graph(
        scope.trails, scope.endpoints, components
    )

    def service_pass() -> tuple[set[str], dict]:
        return evaluate_service_graph(service_graph, components)

    final_component_ids, projection_counts, ledger = (
        _close_candidate_verdict_service(
            components, synthetic_live, verdict_capture.verdicts, service_pass,
            seen_authority_aliases=parking.seen_authority_aliases,
        )
    )
    if ledger.seen_authority_aliases != parking.seen_authority_aliases:
        raise AssertionError("verdict ledger changed the seen authority aliases")

    work_units, proximity_counts = build_work_units(
        final_component_ids, components, synthetic_live, scope, endpoint_grid,
        ledger,
    )
    counts, owners, referenced_service = _count_manifest(
        scope, parking, components, work_units, live, synthetic_live, ledger
    )
    sources = _source_manifest(
        scope, parking, live, verdict_capture, parking_kind, baseline
    )
    algorithm = {
        "mode": "authoritative-production" if authoritative else "non-authoritative",
        "anonymous_trail_ref": ANONYMOUS_TRAIL_REF_SCHEME,
        "geometry_topology": geometry_engine,
        "canonical_must_link": [
            "identical canonical OSM alias/export duplicate",
            "aliases in one verdict sidecar cluster",
            "tagged parking relation to tagged parking member",
            f"polygon overlap >= {OVERLAP_RATIO}",
            "parking point inside polygon within 20 m of bbox centre without tag conflict",
        ],
        "candidate_id": "pc1:sha256(NUL-joined-sorted-canonical-OSM-aliases)",
        "verdict_assignment": (
            "global-exact-OSM-alias-reservation-across-complete-parking-export-"
            "then-selected-global-one-to-one-ranked-position"
        ),
        "proximity_review_m": CLUSTER_M,
        "near_m": NEAR_M,
        "near_limit": NEAR_LIMIT,
        "fallback_m": FALLBACK_M,
        "fallback_limit": FALLBACK_LIMIT,
        "endpoint_grid_cell_degrees": ENDPOINT_GRID_DEGREES,
        "component_index_cell_policy": (
            "multi-resolution-power-of-two; query-every-active-level; "
            "exact-geometry-decides"
        ),
        "live_identity": [
            f"polygon bbox centre <= {LIVE_BBOX_CENTRE_M:g} m",
            "point inside polygon footprint",
            f"polygon edge <= {LIVE_POLYGON_EDGE_M:g} m",
            f"point member <= {LIVE_POINT_M:g} m",
        ],
        "hard_non_public_access": sorted(HARD_NON_PUBLIC_ACCESS),
        "current_excluded_access": sorted(CURRENT_EXCLUDED_ACCESS),
        "current_excluded_parking": sorted(CURRENT_EXCLUDED_PARKING),
        "projections": list(PROJECTIONS),
        "shard_size": shard_size,
        "resource_policy": {
            "endpoint_associations": "exact-and-untruncated",
            "max_endpoint_candidates_per_feature": MAX_ENDPOINT_CANDIDATES_PER_FEATURE,
            "max_endpoint_grid_query_cells": MAX_ENDPOINT_GRID_QUERY_CELLS,
            "max_components_per_endpoint": MAX_COMPONENTS_PER_ENDPOINT,
            "max_trail_candidate_components": MAX_TRAIL_CANDIDATE_COMPONENTS,
            "max_trails_per_endpoint": MAX_TRAILS_PER_ENDPOINT,
            "max_live_trail_associations": MAX_LIVE_TRAIL_ASSOCIATIONS,
            "max_bindings_per_component": MAX_BINDINGS_PER_COMPONENT,
            "component_index_target_cells_per_geometry": (
                TARGET_COMPONENT_INDEX_CELLS
            ),
            "max_component_index_associations": (
                MAX_COMPONENT_INDEX_ASSOCIATIONS
            ),
            "max_spatial_candidates_per_query": MAX_SPATIAL_CANDIDATES,
            "max_spatial_pairs": MAX_SPATIAL_PAIRS,
            "max_parking_identities": MAX_PARKING_IDENTITIES,
            "max_retained_features": MAX_RETAINED_FEATURES,
            "max_work_units": MAX_WORK_UNITS,
            "max_stream_record_bytes": MAX_STREAM_RECORD_BYTES,
            "national_pbf_parking_identity_floor_exclusive": (
                MIN_NATIONAL_PBF_PARKING_IDENTITIES
            ),
            "national_pbf_floor_enforced": enforce_floor,
            "osmium_timeout_seconds": effective_osmium_timeout,
            "osmium_term_grace_seconds": OSMIUM_TERM_GRACE_S,
            "minimum_pbf_artifact_free_bytes": minimum_artifact_free_bytes,
            "failed_stages": "preserve-for-operator-Archive; never-auto-delete",
            "memory_model": (
                "filtered inventories released after reconciliation; export-form "
                "maps released before census assembly; only the sidecar-bounded "
                "seen-authority-alias intersection survives capture; full SQLite "
                "spill remains future work; peak RSS is an external homelab gate "
                "and is not included in authority bytes"
            ),
        },
    }
    status = "complete" if authoritative else "non_authoritative"
    manifest = {
        "kind": "national_parking_census",
        "schema": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "production_authorization": {
            "authoritative": authoritative,
            "production_complete": authoritative,
            "baseline_self_sha256": baseline.self_sha256 if baseline else None,
            "national_pbf_floor_enforced": enforce_floor,
            "non_authoritative_reason": (
                None if authoritative else
                "fixture/subnational runs cannot claim production completion"
            ),
        },
        "run_id": _deterministic_run_id(sources, algorithm),
        "scope": {
            "country": "US",
            "jurisdictions": list(scope.jurisdictions),
            "areas": len(scope.areas),
            "area_slugs_sha256": _area_slugs_sha256(scope.areas),
            "anonymous_trail_ref_scheme": ANONYMOUS_TRAIL_REF_SCHEME,
            "anonymous_trail_refs_sha256": _canonical_hash(
                list(scope.anonymous_trail_refs)
            ),
        },
        "algorithm": algorithm,
        "sources": sources,
        "counts": counts,
        "parking_stream": parking.counters,
        "identity_edges": identity_edges,
        "verdict_ledger": {
            "claim_edges": ledger.claim_edges,
            "ambiguous_edges": ledger.ambiguous_edges,
            "matched": len(ledger.matched_keys),
            "tombstones": len(ledger.tombstone_entries),
            "sidecar_rows": len(verdict_capture.verdicts.entries),
            "reserved_authoritative": len(ledger.reserved_authoritative_keys),
            "reserved_unselected": len(ledger.reserved_unselected_keys),
            "seen_authority_aliases": len(ledger.seen_authority_aliases),
            "seen_authority_aliases_sha256": _canonical_hash(
                list(ledger.seen_authority_aliases)
            ),
            "seen_authority_keys": len(ledger.seen_authority_keys),
            "one_to_one": True,
        },
        "live_identity": live_identity,
        "projection_counts": projection_counts,
        "proximity_review": proximity_counts,
        "owners": owners,
        "referenced_service": referenced_service,
    }
    _assert_manifest_closure(manifest, work_units)

    bindings = [
        scope.bundle_binding, *scope.geom_bindings,
        parking.binding, live.binding, verdict_capture.binding,
    ]
    for optional_binding in (
            parking.osmium_binding, parking.filtered_binding,
            parking.filtered_manifest_binding,
            baseline.binding if baseline is not None else None,
            baseline.registry_binding if baseline is not None else None):
        if optional_binding is not None:
            bindings.append(optional_binding)

    def verify_sources() -> None:
        _verify_inventory(scope.geom_inventory)
        for binding in bindings:
            _verify_binding(binding)
        if baseline is not None:
            _validate_production_baseline(
                baseline, scope, live, verdict_capture
            )

    result = write_outputs(
        destination, work_units, manifest, shard_size, verify_sources
    )
    result["work_units"] = work_units
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--bundle",
        default=_ROOT / "ios" / "SouthMountainExplorer" / "Resources" / "areas-index.json",
    )
    parser.add_argument("--geom-dir", default=_ROOT / "public" / "areas" / "geom")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--parking-pbf", metavar="PATH")
    source.add_argument("--parking-geojsonseq", metavar="PATH")
    parser.add_argument(
        "--authoritative", action="store_true",
        help="claim production completion only after all baseline/PBF gates",
    )
    parser.add_argument("--production-baseline", metavar="JSON")
    parser.add_argument("--pbf-artifact-dir", metavar="ABSOLUTE_DIRECTORY")
    parser.add_argument("--osmium", metavar="ABSOLUTE_EXECUTABLE")
    parser.add_argument(
        "--expected-osmium-sha256", "--osmium-sha256",
        dest="expected_osmium_sha256", metavar="SHA256",
    )
    parser.add_argument(
        "--osmium-timeout-seconds", type=float, metavar="SECONDS",
        help=f"positive finite timeout per osmium command (default: {OSMIUM_TIMEOUT_S:g})",
    )
    parser.add_argument(
        "--enforce-national-pbf-floor", action="store_true", default=None,
        help="enforce the parking-identity floor in non-authoritative rehearsal",
    )
    parser.add_argument("--live-pool", required=True, metavar="JSON")
    parser.add_argument(
        "--verdicts", default=parking_verdicts.DEFAULT_PATH, metavar="JSON"
    )
    parser.add_argument("--output-dir", required=True, metavar="NEW_DIRECTORY")
    parser.add_argument("--shard-size", type=int, default=DEFAULT_SHARD_SIZE)
    return parser


def main(argv=None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    parking_kind = "pbf" if arguments.parking_pbf else "geojsonseq"
    parking_path = arguments.parking_pbf or arguments.parking_geojsonseq
    try:
        result = run_census(
            bundle_path=arguments.bundle,
            geom_dir=arguments.geom_dir,
            parking_path=parking_path,
            parking_kind=parking_kind,
            live_pool_path=arguments.live_pool,
            verdicts_path=arguments.verdicts,
            output_dir=arguments.output_dir,
            shard_size=arguments.shard_size,
            authoritative=arguments.authoritative,
            production_baseline_path=arguments.production_baseline,
            pbf_artifact_dir=arguments.pbf_artifact_dir,
            osmium_executable=arguments.osmium,
            expected_osmium_sha256=arguments.expected_osmium_sha256,
            enforce_national_pbf_floor=arguments.enforce_national_pbf_floor,
            osmium_timeout_s=arguments.osmium_timeout_seconds,
        )
    except (CensusError, OSError, subprocess.SubprocessError) as error:
        print(f"national_census: error: {error}", file=sys.stderr)
        return 2
    manifest = result["manifest"]
    print(
        f"parking census ({manifest['status']}): "
        f"{manifest['counts']['areas']:,} areas, "
        f"{manifest['counts']['trails']:,} trails, "
        f"{manifest['counts']['work_units']:,} work units, "
        f"{result['total_bytes']:,} bytes, "
        f"root {manifest['root_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
