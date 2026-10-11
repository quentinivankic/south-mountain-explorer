"""Immutable regression contract for the run-35 root-cause evidence."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
import copy
from datetime import datetime, timezone
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

_HERE = Path(__file__).resolve().parent
_TESTDATA = _HERE / "testdata"
_FIXTURE_PATH = _TESTDATA / "mols-run35-root-cause-replay.json"
_MANIFEST_PATH = _TESTDATA / "mols-run35-root-cause-replay.manifest.json"
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent / "tools"))
sys.path.insert(0, str(_HERE.parent / "serve"))
import assemble as assembly_pipeline  # noqa: E402
import areas as area_module  # noqa: E402
import model as assembly_model  # noqa: E402
import publish_areas as publish_producer  # noqa: E402
import build_mols_golden as golden_builder  # noqa: E402
import build_mols_pilot_visual as visual_builder  # noqa: E402
import validate_publish_pilot as validator  # noqa: E402

try:
    from shapely.geometry import LineString, shape
    from shapely.ops import unary_union
    HAVE_SHAPELY = True
except ImportError:
    HAVE_SHAPELY = False


def _load():
    return (
        json.loads(_FIXTURE_PATH.read_text(encoding="utf-8")),
        json.loads(_MANIFEST_PATH.read_text(encoding="utf-8")),
    )


def _value_sha256(value):
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")) + "\n").encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _occurrence_dicts(case):
    columns = case["occurrence_columns"]
    return [dict(zip(columns, row)) for row in case["member_way_occurrences"]]


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value) -> None:
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _byte_identity(path: Path) -> dict:
    raw = path.read_bytes()
    return {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}




















def _refresh_run35_visual(root: Path) -> None:
    raw_svg, review = visual_builder.build_svg(
        raw=_read_json(root / "data/aoi/mols-bjerge.raw.geojson"),
        areas=_read_json(root / "data/aoi/mols-bjerge.areas.geojson"),
        trails=_read_json(root / "data/aoi/mols-bjerge.trails.geojson"),
        removed=_read_json(root / "data/aoi/mols-bjerge.removed.geojson"),
        authority=_read_json(root / validator.RAW_RELATION_MEMBERS_RELATIVE),
        assembly=_read_json(root / "reports/assembly.json"),
    )
    (root / validator.VISUAL_RELATIVE).write_bytes(raw_svg)
    (root / validator.VISUAL_REVIEW_RELATIVE).write_text(
        json.dumps(review, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )




def _write_run35_scope_trust(root: Path, output: Path) -> Path:
    scopes = {}
    rows = {
        "raw-denmark": (
            validator.RAW_SCOPE_RECEIPT_RELATIVE,
            validator.RAW_PBF_RELATIVE,
            validator.RAW_RELATION_MEMBERS_RELATIVE,
        ),
        "prefiltered-denmark": (
            validator.PREFILTER_SCOPE_RECEIPT_RELATIVE,
            validator.PREFILTER_PBF_RELATIVE,
            validator.PREFILTER_RELATION_MEMBERS_RELATIVE,
        ),
        "aoi": (
            validator.AOI_SCOPE_RECEIPT_RELATIVE,
            validator.AOI_PBF_RELATIVE,
            validator.AOI_RELATION_MEMBERS_RELATIVE,
        ),
    }
    for scope, (receipt_relative, slice_relative, ledger_relative) in rows.items():
        receipt = _read_json(root / receipt_relative)
        record = {
            "parent": {key: receipt["parent_pbf"][key]
                       for key in ("sha256", "bytes")},
            "slice": _byte_identity(root / slice_relative),
            "receipt": _byte_identity(root / receipt_relative),
            "relation_ledger": _byte_identity(root / ledger_relative),
            "root_set_sha256": receipt["root_set_sha256"],
        }
        if scope == "aoi":
            record["root_bbox_sha256"] = receipt["extraction"][
                "root_bbox_sha256"]
            record["way_topology"] = _byte_identity(
                root / validator.RAW_WAY_TOPOLOGY_RELATIVE)
        scopes[scope] = record
    transform = _read_json(
        root / validator.PREFILTER_TRANSFORM_RECEIPT_RELATIVE)
    trust = {
        "schema_version": 1,
        "pilot": {
            "name": validator.PILOT_AOI_NAME, "bbox": validator.PILOT_BBOX,
        },
        "scopes": scopes,
        "prefilter_transformation": {
            "input": {key: transform["parent_pbf"][key]
                      for key in ("sha256", "bytes")},
            "output": {key: transform["output_pbf"][key]
                       for key in ("sha256", "bytes")},
            "receipt": _byte_identity(
                root / validator.PREFILTER_TRANSFORM_RECEIPT_RELATIVE),
        },
    }
    _write_json(output, trust)
    return output




_RUN36_SEALED_RELATIVES = (
    "README.md",
    "data/aoi/mols-bjerge.areas.geojson",
    "data/aoi/mols-bjerge.curation-diff.json",
    "data/aoi/mols-bjerge.curation.json",
    "data/aoi/mols-bjerge.exact-area.geojson",
    "data/aoi/mols-bjerge.ingest-dropped.geojson",
    "data/aoi/mols-bjerge.osm.pbf",
    "data/aoi/mols-bjerge.raw.geojson",
    "data/aoi/mols-bjerge.relation-members.aoi.json",
    "data/aoi/mols-bjerge.removed.geojson",
    "data/aoi/mols-bjerge.scope.aoi.json",
    "data/aoi/mols-bjerge.trails.geojson",
    "data/aoi/mols-bjerge.way-topology.json",
    "data/source/mols-bjerge.prefilter-transformation.json",
    "data/source/mols-bjerge.prefilter.osm.pbf",
    "data/source/mols-bjerge.raw.osm.pbf",
    "data/source/mols-bjerge.relation-members.prefilter.json",
    "data/source/mols-bjerge.relation-members.raw.json",
    "data/source/mols-bjerge.scope.prefilter.json",
    "data/source/mols-bjerge.scope.raw.json",
    "discovery/discovery.log",
    "discovery/report.json",
    "discovery/selected.json",
    "discovery/validation.json",
    "discovery/validation.log",
    "golden/mols-bjerge.json",
    "reports/app-preview.json",
    "reports/assembly.json",
    "reports/assembly.log",
    "reports/input-validation.log",
    "reports/publish.json",
    "reports/publish.log",
    "viewer/index.html",
    "viewer/serve.py",
    "visual/mols-bjerge.svg",
    "visual/review.json",
)

_RUN35_REGENERATED_EARLY_RELATIVES = frozenset({
    "discovery/validation.json",
    "discovery/validation.log",
    "reports/assembly.log",
    "reports/input-validation.log",
})
_RUN35_ORIGINAL_SEALED_RELATIVES = tuple(
    relative for relative in _RUN36_SEALED_RELATIVES
    if relative not in _RUN35_REGENERATED_EARLY_RELATIVES
)


class ArchiveInputError(ValueError):
    """The archived run is not byte-identical to its pinned authority."""


def _safe_archive_file(archive: Path, relative: str) -> Path:
    candidate_relative = Path(relative)
    if (candidate_relative.is_absolute() or not candidate_relative.parts
            or any(part in {"", ".", ".."}
                   for part in candidate_relative.parts)):
        raise ArchiveInputError(f"unsafe archived input path: {relative!r}")
    candidate = archive / candidate_relative
    current = archive
    for part in candidate_relative.parts:
        current = current / part
        if current.is_symlink():
            raise ArchiveInputError(
                f"archived input must not be a symlink: {relative}")
    if not candidate.is_file():
        raise ArchiveInputError(f"archived input is missing: {relative}")
    try:
        candidate.resolve(strict=True).relative_to(archive.resolve(strict=True))
    except (FileNotFoundError, ValueError) as error:
        raise ArchiveInputError(
            f"archived input escapes its root: {relative}") from error
    return candidate


def _verify_manifest_bound_archive(
        archive: Path, expected_manifest_identity: dict,
        expected_artifact_relatives: tuple[str, ...]) -> dict:
    """Authenticate the original manifest, then every declared input byte."""
    if not archive.is_dir() or archive.is_symlink():
        raise ArchiveInputError(f"archive root is missing or unsafe: {archive}")
    manifest_path = _safe_archive_file(archive, "manifest.json")
    actual_manifest_identity = _byte_identity(manifest_path)
    if actual_manifest_identity != expected_manifest_identity:
        raise ArchiveInputError(
            "original archived manifest identity changed: "
            f"expected {expected_manifest_identity}, "
            f"observed {actual_manifest_identity}")
    try:
        manifest = _read_json(manifest_path)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ArchiveInputError(
            "original archived manifest is not valid JSON") from error
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ArchiveInputError(
            "original archived manifest has no artifact authority")
    expected = set(expected_artifact_relatives)
    declared = set(artifacts)
    missing = sorted(expected - declared)
    undeclared = sorted(declared - expected)
    if missing or undeclared:
        raise ArchiveInputError(
            "original archived artifact inventory differs: "
            f"missing={missing}, undeclared={undeclared}")

    identities = {}
    for relative in sorted(expected, key=lambda value: value.encode("utf-8")):
        record = artifacts[relative]
        expected_identity = {
            "sha256": record.get("sha256"), "bytes": record.get("bytes"),
        }
        if (not isinstance(expected_identity["bytes"], int)
                or not isinstance(expected_identity["sha256"], str)
                or len(expected_identity["sha256"]) != 64):
            raise ArchiveInputError(
                f"invalid manifest identity for archived input: {relative}")
        actual_identity = _byte_identity(_safe_archive_file(
            archive, relative))
        if actual_identity != expected_identity:
            raise ArchiveInputError(
                f"archived input identity changed: {relative}; "
                f"expected {expected_identity}, observed {actual_identity}")
        identities[relative] = actual_identity
    return {
        "manifest": manifest,
        "manifest_identity": actual_manifest_identity,
        "artifacts": identities,
    }


def _verify_original_run35_archive(archive: Path) -> dict:
    fixture, fixture_manifest = _load()
    source_files = fixture_manifest.get("source_files")
    if set(source_files or {}) != {
            "manifest.json",
            "data/source/mols-bjerge.relation-members.raw.json",
            "reports/assembly.json",
            "data/aoi/mols-bjerge.trails.geojson",
            "data/aoi/mols-bjerge.removed.geojson",
            "data/aoi/mols-bjerge.exact-area.geojson"}:
        raise ArchiveInputError("checked fixture does not pin exactly six sources")
    verified = _verify_manifest_bound_archive(
        archive,
        source_files["manifest.json"],
        _RUN35_ORIGINAL_SEALED_RELATIVES,
    )
    manifest = verified["manifest"]
    expected_inputs = {
        "extract": "europe/denmark",
        "state": "Denmark",
        "dry_run": "true",
        "no_routes": "false",
        "touch_report": "false",
        "elevation": "false",
        "region_code": "dk",
        "pilot_area_id": "nationalpark-mols-bjerge-dk",
        "pilot_bbox": "10.40,56.08,10.90,56.35",
        "expected_osm_relation_id": "7046785",
    }
    if (manifest.get("schema_version") != 1
            or manifest.get("branch_ref") != "chat/denmark-trails"
            or manifest.get("source_sha") != fixture["provenance"]["source_sha"]
            or manifest.get("inputs") != expected_inputs):
        raise ArchiveInputError("original archived manifest identity is invalid")
    for relative, expected_identity in source_files.items():
        actual_identity = (verified["manifest_identity"]
                           if relative == "manifest.json"
                           else verified["artifacts"].get(relative))
        if actual_identity != expected_identity:
            raise ArchiveInputError(
                f"six-source fixture authority mismatch: {relative}")
    return verified


def _copy_verified_archive(
        archive: Path, package: Path, verified: dict) -> None:
    """Copy only authenticated manifest-declared inputs, never archive extras."""
    package.mkdir(parents=True, exist_ok=False)
    copy_relatives = ("manifest.json", *sorted(
        verified["artifacts"], key=lambda value: value.encode("utf-8")))
    for relative in copy_relatives:
        source = _safe_archive_file(archive, relative)
        destination = package / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        expected = (verified["manifest_identity"]
                    if relative == "manifest.json"
                    else verified["artifacts"][relative])
        if _byte_identity(destination) != expected:
            raise ArchiveInputError(
                f"verified copy identity changed: {relative}")


def _regenerate_run35_early_evidence(root: Path) -> None:
    """Regenerate four run-35-unsealed files instead of blessing their bytes."""
    manifest = _read_json(root / "manifest.json")
    inputs = manifest["inputs"]
    if (inputs["dry_run"] != "true" or inputs["state"] != "Denmark"
            or inputs["region_code"] != "dk"
            or inputs["expected_osm_relation_id"] != "7046785"):
        raise ArchiveInputError("replay input contract is not fail-closed")
    (root / "reports/input-validation.log").write_text(
        "Validated read-only Mols Bjerge pilot inputs.\n", encoding="utf-8")
    output = StringIO()
    with redirect_stdout(output), redirect_stderr(output):
        exit_code = validator.main([
            "discovery",
            "--discovery-report", str(root / "discovery/report.json"),
            "--expected-osm-relation-id", "7046785",
            "--selected-record-out", str(root / "discovery/selected.json"),
            "--index-out", str(root.parent / "one-row-index.json"),
            "--result-json", str(root / "discovery/validation.json"),
        ])
    (root / "discovery/validation.log").write_text(
        output.getvalue(), encoding="utf-8")
    if exit_code != 0:
        raise ArchiveInputError(
            f"regenerated discovery validation failed: {exit_code}")


def _run35_pbf_projection(root: Path) -> tuple[dict, dict, dict, list, dict]:
    """Project the archived PBF-owned topology without reimplementing assembly."""
    topology = _read_json(root / validator.RAW_WAY_TOPOLOGY_RELATIVE)
    nodes = {}
    ways = {}
    for record in topology["ways"]:
        node_ids = list(record["node_ids"])
        coordinates = list(record["coordinates"])
        ways[record["way_id"]] = {
            "nodes": node_ids,
            "tags": dict(record["tags"]),
        }
        for node_id, coordinate in zip(node_ids, coordinates):
            if coordinate is not None:
                nodes[node_id] = (
                    float(coordinate[0]), float(coordinate[1]))
    relations = {}
    for record in topology["relations"]:
        tags = dict(record["tags"])
        if assembly_model.member_safety.normalize(tags.get("type")) != "route":
            continue
        relations[record["relation_id"]] = {
            "tags": tags,
            "members": [
                ({"node": "n", "way": "w", "relation": "r"}[
                    member["type"]], member["ref"], member["role"])
                for member in record["members"]
            ],
        }
    pois = [{
        "id": record["node_id"],
        "coord": tuple(record["coordinate"]),
        "tags": dict(record["tags"]),
        "name": record.get("name"),
    } for record in topology["destination_pois"]]
    return nodes, ways, relations, pois, topology


def _run35_area_projections(root: Path, topology: dict) -> tuple[list, list]:
    """Project the archived PBF-owned areas for the two parser interfaces."""
    relation_tags = {
        record["relation_id"]: record["tags"]
        for record in topology["relations"]
    }
    way_tags = {
        record["way_id"]: record["tags"] for record in topology["ways"]
    }
    assembled = []
    merge_areas = []
    for feature in _read_json(
            root / "data/aoi/mols-bjerge.areas.geojson")["features"]:
        properties = feature["properties"]
        geometry = shape(feature["geometry"])
        source_tags = (relation_tags if properties["osm_type"] == "relation"
                       else way_tags)
        tags = dict(source_tags.get(properties["osm_id"], {}))
        area = {
            "name": properties.get("name"),
            "tags": tags,
            "geom": geometry,
            "osm_id": properties["osm_id"],
            "osm_type": properties["osm_type"],
        }
        assembled.append(area)
        if not area["name"] or not area_module._is_park_area(tags):
            continue
        polygons = (list(geometry.geoms)
                    if geometry.geom_type == "MultiPolygon" else [geometry])
        rings = [
            [(float(x), float(y)) for x, y in polygon.exterior.coords]
            for polygon in polygons if polygon.geom_type == "Polygon"
        ]
        if rings:
            merge_areas.append({
                "name": area["name"],
                "bbox": geometry.bounds,
                "rings": rings,
                "osm_id": area["osm_id"],
                "osm_type": area["osm_type"],
            })
    return assembled, merge_areas


def _source_maps_preserve_authority(
        original: tuple[dict, dict, dict, list], actual: tuple,
        scopes: dict) -> bool:
    original_nodes, original_ways, original_relations, original_pois = original
    nodes, ways, relations, pois = actual[:4]
    aoi_unchanged = (
        all(nodes.get(key) == value for key, value in original_nodes.items())
        and all(ways.get(key) == value for key, value in original_ways.items())
        and all(relations.get(key) == value
                for key, value in original_relations.items())
        and pois == original_pois
    )
    raw_unchanged = all(
        ways.get(way_id) == {
            "nodes": record["node_ids"], "tags": record["tags"],
        }
        for way_id, record in scopes["raw-denmark"]["ways"].items()
        if record["status"] == "present"
    )
    return aoi_unchanged and raw_unchanged


def _run_production_assembly(root: Path) -> dict:
    projection = _run35_pbf_projection(root)
    original = copy.deepcopy(projection[:4])
    assembled_areas, merge_areas = _run35_area_projections(
        root, projection[4])
    generated = (
        "data/aoi/mols-bjerge.trails.geojson",
        "data/aoi/mols-bjerge.removed.geojson",
        "data/aoi/mols-bjerge.ingest-dropped.geojson",
        "data/aoi/mols-bjerge.curation.json",
        "data/aoi/mols-bjerge.curation-diff.json",
        "data/aoi/mols-bjerge.areas.geojson",
        "data/aoi/mols-bjerge.exact-area.geojson",
        "reports/assembly.json",
        "reports/assembly.log",
    )
    for relative in generated:
        try:
            (root / relative).unlink()
        except FileNotFoundError:
            pass
    output = StringIO()
    arguments = [
        "--in", str(root / validator.AOI_PBF_RELATIVE),
        "--out", str(root / "data/aoi/mols-bjerge.trails.geojson"),
        "--only-area", validator.AREA_NAME,
        "--require-exact-area",
        "--expected-area-relation-id", "7046785",
        "--relation-authority",
        str(root / validator.RAW_RELATION_MEMBERS_RELATIVE),
        "--prefilter-authority",
        str(root / validator.PREFILTER_RELATION_MEMBERS_RELATIVE),
        "--aoi-relation-members",
        str(root / validator.AOI_RELATION_MEMBERS_RELATIVE),
        "--min-length-mi", "0.1",
        "--per-area-merge",
        "--region", "dk",
        "--report-json", str(root / "reports/assembly.json"),
    ]
    with (patch.object(assembly_pipeline, "read_pbf",
                       return_value=projection[:4]),
          patch.object(area_module, "assemble_areas",
                       return_value=assembled_areas),
          patch.object(area_module, "merge_areas",
                       return_value=merge_areas),
          redirect_stdout(output), redirect_stderr(output)):
        exit_code = assembly_pipeline.main(arguments)
    (root / "reports/assembly.log").write_text(
        output.getvalue(), encoding="utf-8")
    scopes = assembly_pipeline._load_relation_scopes(
        str(root / validator.RAW_RELATION_MEMBERS_RELATIVE),
        str(root / validator.PREFILTER_RELATION_MEMBERS_RELATIVE),
        str(root / validator.AOI_RELATION_MEMBERS_RELATIVE),
        _byte_identity(root / validator.AOI_PBF_RELATIVE),
    )
    return {
        "exit": exit_code,
        "source_maps_preserve_authority": _source_maps_preserve_authority(
            original, projection, scopes),
        "input_counts": {
            "nodes": len(original[0]),
            "ways": len(original[1]),
            "relations": len(original[2]),
            "pois": len(original[3]),
            "areas": len(assembled_areas),
            "merge_areas": len(merge_areas),
        },
    }


def _run_production_publisher(root: Path) -> int:
    selected = _read_json(root / "discovery/selected.json")
    index_path = root.parent / "one-row-index.json"
    _write_json(index_path, [[
        *selected["index_row"], None, None, selected["osm_relation_id"],
    ]])
    output_dir = root.parent / "publish-output"
    output_dir.mkdir()
    output = StringIO()
    with redirect_stdout(output), redirect_stderr(output):
        exit_code = publish_producer.main([
            "--trails", str(root / "data/aoi/mols-bjerge.trails.geojson"),
            "--hiking", str(root / validator.AOI_PBF_RELATIVE),
            "--state", validator.STATE,
            "--index", str(index_path),
            "--out-dir", str(output_dir),
            "--dry-run", "--no-boundary-fetch", "--no-elevation",
            "--exact-boundary", str(root / validator.EXACT_AREA_RELATIVE),
            "--preview-root", str(root),
            "--preview-json", str(root / validator.PREVIEW_RELATIVE),
            "--report-json",
            str(root / validator.REQUIRED_JSON_FILES["publish_report"]),
        ])
    (root / "reports/publish.log").write_text(
        output.getvalue(), encoding="utf-8")
    return exit_code


def _run_production_golden(root: Path) -> int:
    return golden_builder.main([
        "--template", str(_HERE.parent / "golden/mols-bjerge.json"),
        "--relation-authority",
        str(root / validator.RAW_RELATION_MEMBERS_RELATIVE),
        "--trails", str(root / "data/aoi/mols-bjerge.trails.geojson"),
        "--source-sha", "b1db2afb4fdb51191551fc372411c04a515c0869",
        "--out", str(root / validator.GOLDEN_RELATIVE),
    ])


def _reseal_run35_explicit(root: Path) -> None:
    if len(_RUN36_SEALED_RELATIVES) != 36 \
            or len(set(_RUN36_SEALED_RELATIVES)) != 36:
        raise AssertionError("run-36 seal inventory must contain 36 unique files")
    manifest_path = root / "manifest.json"
    manifest = _read_json(manifest_path)
    previous = manifest["artifacts"]
    source_scopes = {
        validator.RAW_RELATION_MEMBERS_RELATIVE: "raw-denmark",
        validator.PREFILTER_RELATION_MEMBERS_RELATIVE:
            "prefiltered-denmark",
        validator.AOI_RELATION_MEMBERS_RELATIVE: "aoi-render-scope",
        validator.RAW_PBF_RELATIVE: "raw-denmark",
        validator.PREFILTER_PBF_RELATIVE: "prefiltered-denmark",
        validator.AOI_PBF_RELATIVE: "aoi-render-scope",
        validator.RAW_WAY_TOPOLOGY_RELATIVE: "aoi-source-topology",
        validator.EXACT_AREA_RELATIVE: "exact-area-geometry",
        validator.RAW_SCOPE_RECEIPT_RELATIVE: "raw-scope-provenance",
        validator.PREFILTER_SCOPE_RECEIPT_RELATIVE:
            "prefiltered-scope-provenance",
        validator.AOI_SCOPE_RECEIPT_RELATIVE: "aoi-scope-provenance",
        validator.PREFILTER_TRANSFORM_RECEIPT_RELATIVE:
            "prefilter-transformation-provenance",
        validator.PREVIEW_RELATIVE: "runner-local-app-preview",
        validator.GOLDEN_RELATIVE: "validation-only-no-geometry",
        validator.VISUAL_RELATIVE: "offline-osm-visual",
        validator.VISUAL_REVIEW_RELATIVE:
            "offline-osm-visual-review",
    }
    manifest["artifacts"] = {
        relative: {
            **_byte_identity(root / relative),
            "producer_stage": previous.get(relative, {}).get(
                "producer_stage", "production-replay"),
            "source_scope": previous.get(relative, {}).get(
                "source_scope",
                source_scopes.get(relative, "runner-local-qa")),
            "schema_version": previous.get(relative, {}).get(
                "schema_version", "text"),
        }
        for relative in _RUN36_SEALED_RELATIVES
    }
    _write_json(manifest_path, manifest)


def _build_run35_production_package(
        archive: Path, package: Path) -> tuple[dict, Path, dict, dict]:
    verified = _verify_original_run35_archive(archive)
    _copy_verified_archive(archive, package, verified)
    _regenerate_run35_early_evidence(package)
    assembly_result = _run_production_assembly(package)
    publisher_exit = _run_production_publisher(package)
    golden_exit = _run_production_golden(package)
    _refresh_run35_visual(package)
    _reseal_run35_explicit(package)
    trust = _write_run35_scope_trust(
        package, package.parent / "scope-trust.json")
    report = _read_json(package / "reports/assembly.json")["quality"]
    producer = {
        "assembly_exit": assembly_result["exit"],
        "publisher_exit": publisher_exit,
        "golden_exit": golden_exit,
        "source_maps_preserve_authority": assembly_result[
            "source_maps_preserve_authority"],
        "input_counts": assembly_result["input_counts"],
    }
    return report, trust, producer, verified


class MolsRun35RootCauseReplay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture, cls.manifest = _load()
        cls.raw_by_id = {
            record["way_id"]: record
            for record in cls.fixture["raw_way_records"]
        }

    def test_fixture_is_canonical_manifest_bound_and_odbl_attributed(self):
        raw = _FIXTURE_PATH.read_bytes()
        canonical = (json.dumps(
            self.fixture, ensure_ascii=False, sort_keys=True,
            separators=(",", ":")) + "\n").encode("utf-8")
        self.assertEqual(raw, canonical)
        self.assertEqual(len(raw), self.manifest["output"]["bytes"])
        self.assertEqual(hashlib.sha256(raw).hexdigest(),
                         self.manifest["output"]["sha256"])
        provenance = self.fixture["provenance"]
        self.assertEqual(provenance["github_run_id"], 37941658802)
        self.assertEqual(provenance["github_run_number"], 35)
        self.assertEqual(
            provenance["source_sha"],
            "b1db2afb4fdb51191551fc372411c04a515c0869")
        self.assertEqual(provenance["attribution"],
                         "© OpenStreetMap contributors")
        self.assertEqual(provenance["license"],
                         "Open Database License (ODbL) 1.0")
        self.assertEqual(set(self.manifest["source_files"]), {
            "manifest.json",
            "data/source/mols-bjerge.relation-members.raw.json",
            "reports/assembly.json",
            "data/aoi/mols-bjerge.trails.geojson",
            "data/aoi/mols-bjerge.removed.geojson",
            "data/aoi/mols-bjerge.exact-area.geojson",
        })

    def test_all_591_emitted_and_26_8_4_removed_reversals_replay_exactly(self):
        replay = self.fixture["orientation_replay"]
        emitted = replay["emitted_reversals"]
        self.assertEqual(replay["emitted_relation_candidate_count"], 26)
        self.assertEqual(len(emitted), 591)
        self.assertEqual(len({row["way_id"] for row in emitted}), 418)
        self.assertEqual(
            len(replay["emitted_unique_reversed_way_ids"]), 418)
        self.assertEqual(replay["removed_reversal_counts"], {
            "42": 26, "43": 8, "44": 4,
        })
        self.assertEqual(len(replay["removed_reversals"]), 38)
        self.assertEqual(replay["aoi_only_records"], [{
            "candidate_index": 3,
            "source_record_index": 15,
            "way_id": 37917643,
        }])

        for row in [*emitted, *replay["removed_reversals"]]:
            raw = self.raw_by_id[row["way_id"]]
            self.assertEqual(
                row["serialized_node_ids_sha256"],
                _value_sha256(list(reversed(raw["node_ids"]))))
            self.assertEqual(
                row["serialized_coordinates_sha256"],
                _value_sha256(list(reversed(raw["coordinates"]))))
            self.assertNotEqual(
                row["serialized_node_ids_sha256"],
                _value_sha256(raw["node_ids"]))

    def test_occurrence_multisets_and_unique_geometry_domains_are_exact(self):
        cases = self.fixture["occurrence_cases"]
        expected = {
            "bjergetapen": (85, 82, 82, 82),
            "djurslandstien": (373, 321, 321, 106),
            "kystbefaestningsstien": (21, 17, 17, 0),
            "nordsoevandreruten": (3338, 3308, 3308, 75),
        }
        for key, counts in expected.items():
            with self.subTest(key=key):
                case = cases[key]
                occurrences = _occurrence_dicts(case)
                member_ids = case["member_way_ids"]
                self.assertEqual([row["way_id"] for row in occurrences],
                                 member_ids)
                self.assertEqual((
                    len(member_ids), len(set(member_ids)),
                    len(case["source_geometry_way_ids"]),
                    len(case["rendered_source_way_ids"]),
                ), counts)
                self.assertEqual(
                    len(case["source_geometry_way_ids"]),
                    len(set(case["source_geometry_way_ids"])))
                self.assertEqual(
                    len(case["rendered_source_way_ids"]),
                    len(set(case["rendered_source_way_ids"])))
                self.assertEqual(
                    case["ckey"],
                    "w" + "-".join(str(value) for value in sorted(member_ids)))
                errors = []
                self.assertEqual(
                    validator._normalize_member_way_occurrences(
                        occurrences, key, errors), occurrences)
                self.assertEqual(errors, [])

        repeated = {
            key: {row["way_id"]: row["positions"]
                  for row in case["repeated_way_positions"]}
            for key, case in cases.items()
        }
        self.assertEqual(repeated["bjergetapen"], {
            185426000: [37, 38],
            1165157102: [0, 3],
            1268368329: [1, 2],
        })
        self.assertEqual(repeated["djurslandstien"][1226327837], [127, 179])
        self.assertEqual(repeated["djurslandstien"][33969088], [153, 154])
        self.assertEqual(repeated["djurslandstien"][166601006], [329, 330])
        self.assertEqual(len(repeated["djurslandstien"]), 52)
        self.assertEqual(len(repeated["kystbefaestningsstien"]), 4)
        self.assertEqual(len(repeated["nordsoevandreruten"]), 30)

    def test_occurrence_deletion_invention_role_and_ckey_attacks_fail(self):
        case = self.fixture["occurrence_cases"]["bjergetapen"]
        authority = _occurrence_dicts(case)
        attacks = {
            "deleted": authority[:-1],
            "invented": [*authority, {**authority[-1], "member_index": 999}],
            "role": [{**authority[0], "effective_role": "connection"},
                     *authority[1:]],
        }
        for label, attack in attacks.items():
            with self.subTest(label=label):
                self.assertNotEqual(attack, authority)
        deleted_members = case["member_way_ids"][:-1]
        invented_members = [*case["member_way_ids"], 999999999]
        for member_ids in (deleted_members, invented_members):
            self.assertNotEqual(
                "w" + "-".join(str(value) for value in sorted(member_ids)),
                case["ckey"])
        geometry = case["source_geometry_way_ids"]
        self.assertNotEqual(geometry[:-1], geometry)

    def test_shared_way_is_root_authorized_but_dropped_only_attack_fails(self):
        shared = self.fixture["shared_root_way"]
        self.assertEqual(shared, {
            "way_id": 695939153,
            "surviving_root_relation_id": 11350070,
            "dropped_root_relation_id": 11350071,
            "surviving_candidate_index": 12,
            "dropped_feature_index": 42,
            "surviving_direct_authorization": True,
            "dropped_direct_authorization": True,
            "surviving_rendered": False,
            "dropped_rendered": False,
        })
        dropped = {"eligible_main_way_ids": [695939153], "geometry": None}
        output = {"way_ids": {695939153}, "geometry": None}
        authorized = [{
            "eligible_main_way_ids": [695939153], "geometry": None,
        }]
        self.assertEqual(
            validator._dropped_root_output_contamination(
                dropped, output, authorized), ([], False))
        self.assertEqual(
            validator._dropped_root_output_contamination(
                dropped, output, []), ([695939153], False))

    @unittest.skipUnless(HAVE_SHAPELY, "shapely not installed")
    def test_root_scoped_geometry_subtracts_shared_line_but_not_dropped_only_line(self):
        shared_line = LineString([(0.0, 0.0), (1.0, 0.0)])
        dropped_only = LineString([(1.0, 0.0), (2.0, 0.0)])
        dropped = {
            "eligible_main_way_ids": [695939153, 999],
            "geometry": unary_union([shared_line, dropped_only]),
        }
        authorized = [{
            "eligible_main_way_ids": [695939153],
            "geometry": shared_line,
        }]
        shared_output = {
            "way_ids": {695939153},
            "geometry": shared_line,
        }
        contaminated_output = {
            "way_ids": {695939153, 999},
            "geometry": unary_union([shared_line, dropped_only]),
        }

        self.assertEqual(
            validator._dropped_root_output_contamination(
                dropped, shared_output, authorized), ([], False))
        self.assertEqual(
            validator._dropped_root_output_contamination(
                dropped, contaminated_output, authorized), ([999], True))

    @unittest.skipUnless(HAVE_SHAPELY, "shapely not installed")
    def test_all_27_restored_rows_and_exact_aggregate_replay_from_raw(self):
        replay = self.fixture["restored_replay"]
        boundary = shape(self.fixture["exact_area"]["geometry"])
        measurements = []
        for candidate in replay["candidates"]:
            direct = {
                int(relation_id): list(way_ids)
                for relation_id, way_ids in
                candidate["direct_relation_way_ids"].items()
            }
            source_records = []
            for way_id in candidate["source_geometry_way_ids"]:
                raw = self.raw_by_id.get(way_id)
                if raw is None:
                    continue
                source_records.append({
                    **raw,
                    "direct_relation_ids": [
                        relation_id for relation_id, way_ids in direct.items()
                        if way_id in way_ids
                    ],
                    "_raw_bound": True,
                })
            feature = {
                "properties": {
                    "name": candidate["name"],
                    "ckey": candidate["ckey"],
                    "relation_ids": candidate["relation_ids"],
                },
                "geometry": candidate["geometry"],
            }
            measurements.extend(validator._restored_measurements(
                feature, source_records,
                candidate["restored_relation_way_ids"], direct, boundary))

        rows = [measurement[0] for measurement in measurements]
        self.assertEqual(len(rows), 27)
        self.assertEqual(rows, replay["expected_rows"])
        tuple_miles = {}
        restored_tuples = set()
        for _row, _restored, _total, relation_miles, restored in measurements:
            for relation_way, miles in relation_miles.items():
                prior = tuple_miles.setdefault(relation_way, miles)
                self.assertEqual(prior, miles)
            restored_tuples.update(restored)
        restored_miles = sum(tuple_miles[key] for key in restored_tuples)
        total_miles = sum(tuple_miles.values())
        aggregate = {
            "restored_way_count": len(restored_tuples),
            "restored_miles": round(restored_miles, 6),
            "total_relation_miles": round(total_miles, 6),
            "restored_share": round(restored_miles / total_miles, 6),
        }
        self.assertEqual(aggregate, {
            "restored_way_count": 450,
            "restored_miles": 33.411457,
            "total_relation_miles": 138.868205,
            "restored_share": 0.240598,
        })
        self.assertEqual(aggregate, replay["expected_aggregate"])

    def test_frozen_archived_projection_observes_1272_plus_nine(self):
        reconciliation = self.fixture[
            "archived_implementation_error_reconciliation"]
        categories = Counter({
            key: value["final_error_entries"]
            for key, value in reconciliation.items()
            if isinstance(value, dict) and "final_error_entries" in value
        })
        residual = [
            row["message"] for row in self.fixture["expected_residual_errors"]
        ]

        self.assertEqual(categories, Counter({
            "mutable_orientation": 1258,
            "occurrence_identity": 11,
            "dropped_root_contamination": 1,
            "restored_road_downstream": 2,
        }))
        self.assertEqual(sum(categories.values()), 1272)
        self.assertEqual(reconciliation["total"], 1272)
        self.assertEqual(len(residual), 9)
        self.assertEqual(sum(categories.values()) + len(residual), 1281)
        self.assertEqual(len(set(residual)), 9)

    def test_archived_reconciliation_and_residual_inventory_are_exact(self):
        reconciliation = self.fixture[
            "archived_implementation_error_reconciliation"]
        self.assertEqual(reconciliation["mutable_orientation"], {
            "emitted_reversals": 591,
            "emitted_unique_ways": 418,
            "emitted_validation_passes": 2,
            "removed_reversals": 38,
            "removed_fields_per_reversal": 2,
            "final_error_entries": 1258,
        })
        self.assertEqual(reconciliation["occurrence_identity"][
            "final_error_entries"], 11)
        self.assertEqual(reconciliation["dropped_root_contamination"][
            "final_error_entries"], 1)
        self.assertEqual(reconciliation["restored_road_downstream"][
            "final_error_entries"], 2)
        self.assertEqual(reconciliation["total"], 1272)

        residual = self.fixture["expected_residual_errors"]
        self.assertEqual(Counter(row["category"] for row in residual), Counter({
            "unsafe-in-area-relation-audit": 2,
            "unresolved-trail-gap": 5,
            "nonzero-unresolved-population": 1,
            "assembly-quality-aggregate": 1,
        }))
        self.assertEqual(
            [row["relation_id"] for row in residual[:2]],
            [10507412, 13944294])
        self.assertEqual([row["name"] for row in residual[2:7]], [
            "Mols Bjerge-stien Ebeltoftetapen",
            "Mols Bjerge-stien Gåsehage-etapen",
            "Djurslandstien",
            "Langs Kysten",
            "Den gamle købstad",
        ])
        self.assertEqual(
            (residual[7]["category"], residual[7]["count"],
             residual[7]["miles"]),
            ("nonzero-unresolved-population", 5, 54.571))
        self.assertEqual({row["message"] for row in residual}, {
            "relation-member-audit[11] contains unresolved member exclusions "
            "or evidence: ['unsafe-member-in-exact-area']",
            "relation-member-audit[21] contains unresolved member exclusions "
            "or evidence: ['unsafe-member-in-exact-area']",
            "trail[10] unresolved-relation-gap",
            "trail[17] unresolved-relation-gap",
            "trail[18] unresolved-relation-gap",
            "trail[23] unresolved-relation-gap",
            "trail[24] unresolved-relation-gap",
            "unresolved-relation-gap population is nonzero: 'Mols Bjerge-stien "
            "Ebeltoftetapen', 'Mols Bjerge-stien Gåsehage-etapen', "
            "'Djurslandstien', 'Langs Kysten', 'Den gamle købstad'",
            "assembly quality report contains unresolved failures",
        })
        self.assertEqual(len(residual), 9)

    def test_archive_verification_and_copy_contract_is_hermetic(self):
        relatives = (
            "data/aoi/mols-bjerge.areas.geojson",
            "data/aoi/mols-bjerge.way-topology.json",
        )
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "archive"
            archive.mkdir()
            contents = {
                relatives[0]: b'{"type":"FeatureCollection"}\n',
                relatives[1]: b'{"schema_version":3,"ways":[]}\n',
            }
            artifacts = {}
            for relative, raw in contents.items():
                path = archive / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
                artifacts[relative] = _byte_identity(path)
            _write_json(archive / "manifest.json", {
                "schema_version": 1, "artifacts": artifacts,
            })
            manifest_identity = _byte_identity(archive / "manifest.json")

            verified = _verify_manifest_bound_archive(
                archive, manifest_identity, relatives)
            destination = root / "verified-copy"
            _copy_verified_archive(archive, destination, verified)
            self.assertEqual(
                {path.relative_to(destination).as_posix()
                 for path in destination.rglob("*") if path.is_file()},
                {"manifest.json", *relatives})

            for relative in relatives:
                with self.subTest(tampered=relative):
                    path = archive / relative
                    original = path.read_bytes()
                    path.write_bytes(original + b"tampered")
                    with self.assertRaisesRegex(
                            ArchiveInputError,
                            "archived input identity changed"):
                        _verify_manifest_bound_archive(
                            archive, manifest_identity, relatives)
                    path.write_bytes(original)

            with self.assertRaisesRegex(
                    ArchiveInputError, "artifact inventory differs"):
                _verify_manifest_bound_archive(
                    archive, manifest_identity,
                    (*relatives, "validation/final.json"))

    def test_replay_fails_before_copy_or_reseal_when_preflight_fails(self):
        with (patch(
                f"{__name__}._verify_original_run35_archive",
                side_effect=ArchiveInputError("preflight rejected")),
              patch(f"{__name__}._copy_verified_archive") as copier,
              patch(f"{__name__}._reseal_run35_explicit") as resealer,
              TemporaryDirectory() as temporary):
            with self.assertRaisesRegex(
                    ArchiveInputError, "preflight rejected"):
                _build_run35_production_package(
                    Path(temporary) / "archive",
                    Path(temporary) / "package")
            copier.assert_not_called()
            resealer.assert_not_called()

    def test_replay_inventory_regenerates_only_four_unsealed_inputs(self):
        self.assertEqual(len(_RUN36_SEALED_RELATIVES), 36)
        self.assertEqual(len(_RUN35_ORIGINAL_SEALED_RELATIVES), 32)
        self.assertEqual(_RUN35_REGENERATED_EARLY_RELATIVES, {
            "discovery/validation.json",
            "discovery/validation.log",
            "reports/assembly.log",
            "reports/input-validation.log",
        })
        self.assertTrue(
            _RUN35_REGENERATED_EARLY_RELATIVES.isdisjoint(
                _RUN35_ORIGINAL_SEALED_RELATIVES))
        self.assertNotIn(
            "validation/final.json", _RUN35_ORIGINAL_SEALED_RELATIVES)

    def test_fixture_is_not_referenced_by_runtime_modules(self):
        needle = _FIXTURE_PATH.name
        runtime_files = [
            _HERE / "assemble.py", _HERE / "model.py", _HERE / "quality.py",
            _HERE.parent / "serve" / "publish_areas.py",
            _HERE.parent / "tools" / "validate_publish_pilot.py",
        ]
        self.assertTrue(all(
            needle not in path.read_text(encoding="utf-8")
            for path in runtime_files
        ))


def _recursive_file_identities(root: Path, excluded: set[str]) -> list[dict]:
    identities = []
    for path in sorted(
            (value for value in root.rglob("*") if value.is_file()),
            key=lambda value: value.relative_to(root).as_posix().encode("utf-8")):
        relative = path.relative_to(root).as_posix()
        if relative in excluded:
            continue
        identities.append({"path": relative, **_byte_identity(path)})
    return identities


def _write_seal_verification(package: Path, output: Path) -> dict:
    manifest = _read_json(package / "manifest.json")
    artifacts = manifest.get("artifacts", {})
    rows = []
    for relative in sorted(artifacts, key=lambda value: value.encode("utf-8")):
        actual = _byte_identity(package / relative)
        expected = {
            "sha256": artifacts[relative]["sha256"],
            "bytes": artifacts[relative]["bytes"],
        }
        rows.append({
            "path": relative,
            "expected": expected,
            "actual": actual,
            "matches": actual == expected,
        })
    verification = {
        "schema_version": 1,
        "prevalidation_seal_count": len(rows),
        "all_prevalidation_seals_match": all(
            row["matches"] for row in rows),
        "prevalidation_artifacts": rows,
        "post_seal_outputs": [
            "manifest.json",
            "validation/final.json",
            "validation/final.log",
        ],
    }
    _write_json(output, verification)
    if len(rows) != 36 or not verification["all_prevalidation_seals_match"]:
        raise AssertionError("run-36 prevalidation seals are incomplete")
    return verification


def _run_original_manifest_tamper_controls(
        archive: Path, verified: dict) -> dict:
    results = []
    targets = (
        "data/aoi/mols-bjerge.way-topology.json",
        "data/aoi/mols-bjerge.areas.geojson",
    )
    with TemporaryDirectory(prefix="trekdex-run35-tamper-controls-") as temporary:
        mirror = Path(temporary) / "archive"
        _copy_verified_archive(archive, mirror, verified)
        for relative in targets:
            target = mirror / relative
            target.write_bytes(target.read_bytes() + b"tampered")
            try:
                _verify_original_run35_archive(mirror)
            except ArchiveInputError as error:
                results.append({
                    "path": relative,
                    "rejected_before_copy_or_reseal": True,
                    "error": str(error),
                })
            else:
                raise AssertionError(
                    f"tampered archived input was accepted: {relative}")
            shutil.copy2(archive / relative, target)
            if _byte_identity(target) != verified["artifacts"][relative]:
                raise AssertionError(
                    f"tamper-control restoration failed: {relative}")
    return {
        "schema_version": 1,
        "phase": "before-production-copy-consumption-and-reseal",
        "controls": results,
    }


def _run_retained_integration_replay(
        archive: Path, evidence_dir: Path) -> int:
    if not HAVE_SHAPELY:
        raise RuntimeError("the explicit run-35 integration gate requires shapely")
    evidence_dir.mkdir(parents=True, exist_ok=False)
    started = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    driver_source = Path(__file__).read_bytes()
    (evidence_dir / "driver-source.py").write_bytes(driver_source)
    fixture, fixture_manifest = _load()
    frozen_projection = {
        "schema_version": 1,
        "fixture": _byte_identity(_FIXTURE_PATH),
        "fixture_manifest": _byte_identity(_MANIFEST_PATH),
        "archived_status": "failed",
        "archived_total_errors": 1281,
        "archived_implementation_error_count": 1272,
        "archived_implementation_error_reconciliation": fixture[
            "archived_implementation_error_reconciliation"],
        "archived_genuine_errors": fixture["expected_residual_errors"],
    }
    _write_json(evidence_dir / "frozen-run35-projection.json",
                frozen_projection)

    initial_verified = _verify_original_run35_archive(archive)
    tamper_controls = _run_original_manifest_tamper_controls(
        archive, initial_verified)
    _write_json(evidence_dir / "original-manifest-tamper-controls.json",
                tamper_controls)
    temporary_replay = TemporaryDirectory(
        prefix="trekdex-run36-retained-replay-")
    replay_root = Path(temporary_replay.name)
    package = replay_root / "qa"
    report, trust, producer, before = _build_run35_production_package(
        archive, package)
    if before != initial_verified:
        raise AssertionError("archive changed between preflight and replay")
    shutil.copy2(archive / "manifest.json",
                 evidence_dir / "original-run35-manifest.json")
    if _byte_identity(evidence_dir / "original-run35-manifest.json") != before[
            "manifest_identity"]:
        raise ArchiveInputError("retained original manifest copy changed")
    _write_json(evidence_dir / "archive-input-identities-before.json", before)

    result_path = package / "validation/final.json"
    result_path.parent.mkdir(parents=True, exist_ok=True)
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        validator_exit = validator.main([
            "final",
            "--qa-dir", str(package),
            "--scope-trust-file", str(trust),
            "--raw-relation-members",
            str(package / validator.RAW_RELATION_MEMBERS_RELATIVE),
            "--prefilter-relation-members",
            str(package / validator.PREFILTER_RELATION_MEMBERS_RELATIVE),
            "--aoi-relation-members",
            str(package / validator.AOI_RELATION_MEMBERS_RELATIVE),
            "--expected-osm-relation-id", "7046785",
            "--result-json", str(result_path),
        ])
    producer_logs = evidence_dir / "producer-logs"
    producer_logs.mkdir()
    (producer_logs / "final-validator.stdout.log").write_text(
        stdout.getvalue(), encoding="utf-8")
    (producer_logs / "final-validator.stderr.log").write_text(
        stderr.getvalue(), encoding="utf-8")
    (package / "validation/final.log").write_text(
        stdout.getvalue() + stderr.getvalue(), encoding="utf-8")

    result = _read_json(result_path)
    expected_errors = [
        row["message"] for row in fixture["expected_residual_errors"]
    ]
    expected_aggregate = {
        "restored_way_count": 450,
        "restored_miles": 33.411457,
        "total_relation_miles": 138.868205,
        "restored_share": 0.240598,
    }
    expected_counts = {
        "nodes": 86177,
        "ways": 7440,
        "relations": 33,
        "pois": 647,
        "areas": 1646,
        "merge_areas": 6,
    }
    implementation_fragments = (
        "raw authority node order", "member way occurrences contradict",
        "rendered way lineage", "dropped relation root",
        "restored-road relation ledger", "restored-road aggregate",
        "artifact seal", "schema_version must be 8",
    )
    implementation_errors = [
        error for error in result["errors"]
        if any(fragment in error for fragment in implementation_fragments)
    ]
    publish = _read_json(package / "reports/publish.json")
    preview = _read_json(package / "reports/app-preview.json")

    if validator_exit != 1 or result.get("status") != "failed":
        raise AssertionError(
            f"corrected validator must fail only on genuine gates: {validator_exit}")
    if Counter(result["errors"]) != Counter(expected_errors):
        raise AssertionError("corrected final errors differ from frozen exact nine")
    if implementation_errors:
        raise AssertionError(
            f"implementation/evidence errors remain: {implementation_errors}")
    if {key: producer[key] for key in (
            "assembly_exit", "publisher_exit", "golden_exit")} != {
                "assembly_exit": 0, "publisher_exit": 0, "golden_exit": 0}:
        raise AssertionError(f"producer failed: {producer}")
    if not producer["source_maps_preserve_authority"]:
        raise AssertionError("production replay changed raw source authority")
    if producer["input_counts"] != expected_counts:
        raise AssertionError("production replay input projection changed")
    expected_rows = fixture["restored_replay"]["expected_rows"]
    actual_rows = report["restored_road_relations"]
    row_differences = []
    if len(actual_rows) != 27 or len(expected_rows) != 27:
        raise AssertionError("production restored-road row count changed")
    for index, (actual_row, expected_row) in enumerate(
            zip(actual_rows, expected_rows)):
        differences = {
            key: {"expected": expected_row.get(key),
                  "actual": actual_row.get(key)}
            for key in sorted(set(actual_row) | set(expected_row))
            if actual_row.get(key) != expected_row.get(key)
        }
        if differences:
            row_differences.append({
                "index": index,
                "relation_id": actual_row.get("relation_id"),
                "differences": differences,
            })
    if ({row["relation_id"] for row in row_differences}
            != {177276, 178383}
            or any(set(row["differences"]) != {"key"}
                   for row in row_differences)):
        raise AssertionError(
            f"production restored-road rows changed: {row_differences}")
    trails = _read_json(
        package / "data/aoi/mols-bjerge.trails.geojson")["features"]
    midtervej = [
        feature["properties"] for feature in trails
        if feature.get("properties", {}).get("name") == "Midtervej"
    ]
    if len(midtervej) != 1:
        raise AssertionError("production Midtervej identity is ambiguous")
    expected_midtervej_ckey = "w" + "-".join(
        str(value) for value in sorted(midtervej[0]["member_ways"]))
    if (midtervej[0]["ckey"] != expected_midtervej_ckey
            or {actual_rows[index]["key"] for index in (2, 3)}
            != {expected_midtervej_ckey}):
        raise AssertionError("production Midtervej ckey lost multiplicity")
    _write_json(evidence_dir / "restored-row-comparison.json", {
        "schema_version": 1,
        "ordered_row_count": 27,
        "all_non_ckey_fields_exact": True,
        "differences": row_differences,
        "justification": (
            "The frozen run-35 rows used a unique-way Midtervej key. The "
            "corrected schema must preserve five cross-root duplicate member "
            "occurrences plus the AOI same-name way, so relation rows 177276 "
            "and 178383 intentionally carry the production candidate's "
            "duplicate-preserving member_ways ckey."
        ),
        "midtervej_member_way_count": len(midtervej[0]["member_ways"]),
        "midtervej_unique_member_way_count": len(set(
            midtervej[0]["member_ways"])),
        "midtervej_ckey": expected_midtervej_ckey,
        "expected_rows": expected_rows,
        "actual_rows": actual_rows,
    })
    if report["restored_road_aggregate"] != expected_aggregate:
        raise AssertionError("production restored-road aggregate changed")
    if (report["signed_relation_unresolved_count"] != 5
            or report["relation_member_unresolved_count"] != 2):
        raise AssertionError("genuine unresolved population changed")
    if (publish.get("dry_run") is not True
            or publish.get("canonical_write") is not False
            or preview.get("canonical_write") is not False):
        raise AssertionError("production replay publication was not fail-closed")

    seal = _write_seal_verification(
        package, evidence_dir / "seal-verification.json")
    after = _verify_original_run35_archive(archive)
    _write_json(evidence_dir / "archive-input-identities-after.json", after)
    if before != after:
        raise AssertionError("archived run-35 inputs changed during replay")

    retained_package = evidence_dir / "qa"
    shutil.copytree(package, retained_package, copy_function=shutil.copy2)
    for relative in ("scope-trust.json", "one-row-index.json"):
        shutil.copy2(replay_root / relative, evidence_dir / relative)
    if (replay_root / "publish-output").is_dir():
        shutil.copytree(
            replay_root / "publish-output", evidence_dir / "publish-output",
            copy_function=shutil.copy2)
    source_package_files = _recursive_file_identities(package, set())
    retained_package_files = _recursive_file_identities(
        retained_package, set())
    if source_package_files != retained_package_files:
        raise AssertionError("retained QA package copy changed")
    temporary_replay.cleanup()

    summary = {
        "schema_version": 1,
        "archived_partition": {
            "total": 1281, "implementation_or_evidence": 1272,
            "genuine": 9,
        },
        "corrected_partition": {
            "total": len(result["errors"]),
            "implementation_or_evidence": len(implementation_errors),
            "genuine": len(result["errors"]),
        },
        "validator_exit": validator_exit,
        "producer": producer,
        "restored_road_row_count": len(report["restored_road_relations"]),
        "restored_road_non_ckey_fields_exact": True,
        "restored_road_required_ckey_migration_count": len(row_differences),
        "restored_road_aggregate": report["restored_road_aggregate"],
        "dry_run": publish["dry_run"],
        "canonical_write": publish["canonical_write"],
        "archive_unchanged": True,
        "prevalidation_seal_count": seal["prevalidation_seal_count"],
        "all_prevalidation_seals_match": seal[
            "all_prevalidation_seals_match"],
        "fixture_output_identity": fixture_manifest["output"],
    }
    _write_json(evidence_dir / "retained-output-summary.json", summary)
    ended = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    environment_names = (
        "ALL_PROXY", "GIT_OPTIONAL_LOCKS", "HTTP_PROXY", "HTTPS_PROXY",
        "LC_ALL", "NO_PROXY", "PIP_NO_INDEX", "PYTHONHASHSEED",
        "PYTHONDONTWRITEBYTECODE", "PYTHONNOUSERSITE", "PYTEST_ADDOPTS",
        "TMPDIR", "TREKDEX_RUN35_ARCHIVE", "TZ", "XDG_CACHE_HOME",
    )
    _write_json(evidence_dir / "driver.json", {
        "schema_version": 1,
        "argv": sys.argv,
        "cwd": os.getcwd(),
        "environment": {
            name: os.environ[name] for name in environment_names
            if name in os.environ
        },
        "python": {"executable": sys.executable, "version": sys.version},
        "driver": {
            "path": str(Path(__file__).resolve()),
            "bytes": len(driver_source),
            "sha256": hashlib.sha256(driver_source).hexdigest(),
            "retained_copy": "driver-source.py",
        },
        "started_at_utc": started,
        "ended_at_utc": ended,
        "exit": 0,
        "expected_validator_exit": 1,
    })
    identities = _recursive_file_identities(
        evidence_dir, {"evidence-manifest.json"})
    _write_json(evidence_dir / "evidence-manifest.json", {
        "schema_version": 1,
        "file_count": len(identities),
        "files": identities,
    })
    return 0


def _integration_cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Run the explicit manifest-bound run-35 replay gate")
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--evidence-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    return _run_retained_integration_replay(
        args.archive.resolve(), args.evidence_dir)


if __name__ == "__main__":
    if sys.argv[1:2] == ["integration-replay"]:
        raise SystemExit(_integration_cli(sys.argv[2:]))
    unittest.main()
