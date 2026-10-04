"""Synthetic controls for the deterministic run-33 replay generator."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

_TESTDATA = Path(__file__).resolve().parent / "testdata"
sys.path.insert(0, str(_TESTDATA))
import generate_mols_run33_replay as generator  # noqa: E402

_RUN_ID = 37036001231
_SOURCE_SHA = "a" * 40
_ARTIFACT_NAME = f"mols-bjerge-qa-{_SOURCE_SHA}"


def _reverse_mapping_order(value):
    if isinstance(value, dict):
        return {
            key: _reverse_mapping_order(value[key])
            for key in reversed(list(value))
        }
    if isinstance(value, list):
        return [_reverse_mapping_order(item) for item in value]
    return value


def _documents():
    coordinates = [[10.5, 56.15], [10.55, 56.15], [10.6, 56.15]]
    return {
        "README.md": f"Synthetic local fixture\n\n{generator.ATTRIBUTION}\n",
        "manifest.json": {
            "schema_version": 1,
            "branch_ref": "chat/denmark-trails",
            "source_sha": _SOURCE_SHA,
            "inputs": {
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
            },
            "attribution": generator.ATTRIBUTION,
        },
        "reports/assembly.json": {
            "schema_version": 1,
            "status": "ok",
            "exact_area_required": True,
            "area_query": generator.AREA_NAME,
            "expected_area_relation_id": generator.AREA_RELATION_ID,
            "boundary": {
                "name": generator.AREA_NAME,
                "osm_type": "relation",
                "osm_id": generator.AREA_RELATION_ID,
            },
            "clip_applied": True,
            "post_clip_trail_count": 1,
            "assembled_trail_count": 1,
        },
        "data/aoi/mols-bjerge.raw.geojson": {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": "w10",
                    "properties": {"surface": "ground", "highway": "path"},
                    "geometry": {
                        "type": "LineString",
                        "coordinates": coordinates[:2],
                    },
                },
                {
                    "type": "Feature",
                    "id": "w11",
                    "properties": {
                        "foot": "yes", "service": "alley", "highway": "service",
                    },
                    "geometry": {
                        "type": "LineString",
                        "coordinates": coordinates[1:],
                    },
                },
            ],
        },
        "data/aoi/mols-bjerge.trails.geojson": {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {
                    "name": "Synthetic Route",
                    "source": "relation",
                    "member_ways": [10, 11],
                    "length_mi": 1.0,
                    "clipped": True,
                },
                "geometry": {
                    "type": "MultiLineString",
                    "coordinates": [coordinates],
                },
            }],
        },
        "data/aoi/mols-bjerge.areas.geojson": {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {"name": generator.AREA_NAME},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [10.4, 56.1], [10.7, 56.1], [10.7, 56.2],
                        [10.4, 56.2], [10.4, 56.1],
                    ]],
                },
            }],
        },
    }


def _write_artifact(parent: Path, documents: dict, *, reverse_keys=False,
                    omit=None) -> Path:
    artifact = parent / _ARTIFACT_NAME
    for relative, value in documents.items():
        if relative == omit:
            continue
        path = artifact / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if relative.endswith(".md"):
            path.write_text(value, encoding="utf-8")
        else:
            if reverse_keys:
                value = _reverse_mapping_order(value)
            path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
    return artifact


def _expectations(documents: dict) -> dict:
    source_files = {}
    for relative, expected_format in generator._REQUIRED_SOURCE_FILES.items():
        value = documents[relative]
        if expected_format == "json":
            digest = generator.canonical_json_sha256(value)
        else:
            digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
        source_files[relative] = {
            "format": expected_format,
            "canonical_sha256": digest,
        }
    fixture = generator.build_fixture(
        documents, run_id=_RUN_ID, source_sha=_SOURCE_SHA,
        artifact_name=_ARTIFACT_NAME)
    payload = generator.canonical_json_bytes(fixture)
    return {
        "schema_version": 1,
        "github_run_id": _RUN_ID,
        "source_sha": _SOURCE_SHA,
        "artifact_name": _ARTIFACT_NAME,
        "attribution": generator.ATTRIBUTION,
        "area_name": generator.AREA_NAME,
        "area_relation_id": generator.AREA_RELATION_ID,
        "source_files": source_files,
        "projection": generator._projection_summary(fixture),
        "output": {
            "file": generator.FIXTURE_NAME,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "bytes": len(payload),
        },
    }


def _write_expectations(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


class ReplayGenerator(unittest.TestCase):
    def test_stable_bytes_ignore_source_json_key_order(self):
        documents = _documents()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = _write_artifact(root / "first", documents)
            second = _write_artifact(
                root / "second", documents, reverse_keys=True)
            expectations_path = root / "expectations.json"
            _write_expectations(expectations_path, _expectations(documents))
            first_output = root / "first.json"
            second_output = root / "second.json"

            generator.generate_fixture(
                artifact_dir=first, output=first_output,
                expectations_path=expectations_path, run_id=_RUN_ID,
                source_sha=_SOURCE_SHA)
            generator.generate_fixture(
                artifact_dir=second, output=second_output,
                expectations_path=expectations_path, run_id=_RUN_ID,
                source_sha=_SOURCE_SHA)

            self.assertEqual(first_output.read_bytes(), second_output.read_bytes())
            self.assertEqual(json.loads(first_output.read_text())["schema_version"], 1)
            self.assertEqual(list(root.glob(".*.tmp")), [])

    def test_rejects_wrong_run_sha_and_missing_source_file(self):
        documents = _documents()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = _write_artifact(root / "valid", documents)
            missing = _write_artifact(
                root / "missing", documents, omit="reports/assembly.json")
            expectations_path = root / "expectations.json"
            _write_expectations(expectations_path, _expectations(documents))
            base = {
                "artifact_dir": artifact,
                "output": root / "out.json",
                "expectations_path": expectations_path,
                "run_id": _RUN_ID,
                "source_sha": _SOURCE_SHA,
            }

            cases = [
                {**base, "run_id": _RUN_ID + 1},
                {**base, "source_sha": "b" * 40},
                {**base, "artifact_dir": missing},
            ]
            for arguments in cases:
                with self.subTest(arguments=arguments):
                    with self.assertRaises(generator.FixtureError):
                        generator.generate_fixture(**arguments)

    def test_rejects_changed_expected_file_identity(self):
        documents = _documents()
        changed = copy.deepcopy(documents)
        changed["data/aoi/mols-bjerge.raw.geojson"]["features"][0][
            "properties"]["surface"] = "paved"
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = _write_artifact(root, changed)
            expectations_path = root / "expectations.json"
            _write_expectations(expectations_path, _expectations(documents))

            with self.assertRaisesRegex(generator.FixtureError,
                                        "source file identity mismatch"):
                generator.generate_fixture(
                    artifact_dir=artifact, output=root / "out.json",
                    expectations_path=expectations_path, run_id=_RUN_ID,
                    source_sha=_SOURCE_SHA)

    def test_update_manifest_then_standard_hash_check_passes(self):
        documents = _documents()
        expectations = _expectations(documents)
        expectations["projection"] = {}
        expectations["output"] = {}
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = _write_artifact(root, documents)
            expectations_path = root / "expectations.json"
            _write_expectations(expectations_path, expectations)
            output = root / generator.FIXTURE_NAME

            generated = generator.generate_fixture(
                artifact_dir=artifact, output=output,
                expectations_path=expectations_path, run_id=_RUN_ID,
                source_sha=_SOURCE_SHA, update_manifest=True)
            updated = json.loads(expectations_path.read_text())
            raw = output.read_bytes()

            self.assertEqual(updated["output"]["sha256"],
                             hashlib.sha256(raw).hexdigest())
            self.assertEqual(updated["output"]["bytes"], len(raw))
            self.assertEqual(updated["projection"], generated["projection"])
            self.assertEqual(json.loads(raw)["schema_version"], 1)
            generator.generate_fixture(
                artifact_dir=artifact, output=root / "verified.json",
                expectations_path=expectations_path, run_id=_RUN_ID,
                source_sha=_SOURCE_SHA)


if __name__ == "__main__":
    unittest.main()
