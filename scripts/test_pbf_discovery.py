"""Focused regressions for Denmark --source-pbf discovery."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from contextlib import redirect_stderr, redirect_stdout

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import _pbf_discovery as pbf  # noqa: E402

_SEED_SPEC = importlib.util.spec_from_file_location(
    "seed_areas_pbf_tests", _HERE / "seed-areas.py")
seed = importlib.util.module_from_spec(_SEED_SPEC)
_SEED_SPEC.loader.exec_module(seed)
_VALIDATOR_PATH = (
    _HERE.parent / "trailforge" / "tools" / "validate_publish_pilot.py")
_VALIDATOR_SPEC = importlib.util.spec_from_file_location(
    "validate_publish_pilot_pbf_tests", _VALIDATOR_PATH)
validator = importlib.util.module_from_spec(_VALIDATOR_SPEC)
_VALIDATOR_SPEC.loader.exec_module(validator)

RELATION_ID = 7046785
SOURCE_BYTES = b"synthetic deterministic OSM PBF fixture\n"
SCOPE = {
    "region_code": "DK",
    "admin_boundary_osm_type": "relation",
    "admin_boundary_osm_id": 50046,
    "bbox": [7.0, 54.0, 13.0, 58.0],
}


def _element(osm_type, osm_id, name, *, lat=56.2, lon=10.6):
    return {
        "type": osm_type,
        "id": osm_id,
        "tags": {"name": name, "protect_class": "2"},
        "center": {"lat": lat, "lon": lon},
    }


def _run_local(*, expected_sha=None, reader=None, extra=()):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        source = root / "denmark.osm.pbf"
        report = root / "report.json"
        provenance = root / "pbf-provenance.json"
        selected = root / "selected.json"
        index = root / "index.json"
        validation = root / "validation.json"
        include = root / "include.txt"
        exclude = root / "exclude.txt"
        cache = root / "cache.json"
        source.write_bytes(SOURCE_BYTES)
        include.write_text("", encoding="utf-8")
        exclude.write_text("", encoding="utf-8")
        digest = expected_sha or hashlib.sha256(SOURCE_BYTES).hexdigest()
        argv = [
            "seed-areas.py", "--dry-run", "DK",
            "--source-pbf", str(source),
            "--source-pbf-sha256", digest,
            "--discovery-report", str(report),
            "--pbf-provenance", str(provenance),
            *extra,
        ]
        default_reader = lambda *_args: (
            [_element("relation", RELATION_ID,
                      "Nationalpark Mols Bjerge")],
            dict(SCOPE),
        )
        with (
            patch.object(seed, "SEED_INCLUDE", include),
            patch.object(seed, "SEED_EXCLUDE", exclude),
            patch.object(seed, "INDEX_PATH", root / "canonical-index.json"),
            patch.object(seed, "CACHE_PATH", cache),
            patch.object(pbf, "_read_pbf_areas",
                         side_effect=reader or default_reader) as pbf_reader,
            patch.object(seed.urllib.request, "urlopen",
                         side_effect=AssertionError(
                             "network client must not be touched")) as opener,
            patch.object(sys, "argv", argv),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            exit_code = 0
            try:
                seed.main()
            except SystemExit as error:
                exit_code = int(error.code)

        report_value = json.loads(report.read_text()) if report.exists() else None
        provenance_value = (
            json.loads(provenance.read_text()) if provenance.exists() else None)
        validator_result = None
        validator_exit = None
        if report.exists() and provenance.exists():
            validator_exit = validator.main([
                "discovery",
                "--discovery-report", str(report),
                "--pbf-provenance", str(provenance),
                "--source-pbf", str(source),
                "--expected-osm-relation-id", str(RELATION_ID),
                "--selected-record-out", str(selected),
                "--index-out", str(index),
                "--result-json", str(validation),
            ])
            validator_result = json.loads(validation.read_text())
        return {
            "exit": exit_code,
            "stdout": stdout.getvalue(),
            "stderr": stderr.getvalue(),
            "report": report_value,
            "provenance": provenance_value,
            "reader_calls": pbf_reader.call_count,
            "network_calls": opener.call_count,
            "canonical_exists": (root / "canonical-index.json").exists(),
            "cache_exists": cache.exists(),
            "validator_exit": validator_exit,
            "validator_result": validator_result,
            "selected": json.loads(selected.read_text())
            if selected.exists() else None,
            "index": json.loads(index.read_text()) if index.exists() else None,
        }


class FakeAreaReaderTests(unittest.TestCase):
    def test_area_reader_selects_dk_scope_and_orders_candidates(self):
        class Tag:
            def __init__(self, key, value):
                self.k = key
                self.v = value

        class Area:
            def __init__(self, osm_id, tags, bounds, *, from_way=False):
                self._id = osm_id
                self._from_way = from_way
                self.tags = [Tag(key, value) for key, value in tags.items()]
                self.bounds = bounds

            def orig_id(self):
                return self._id

            def from_way(self):
                return self._from_way

        areas = [
            Area(22, {"name": "Nationalpark Thy", "protect_class": "2",
                      "boundary": "national_park"},
                 [8.0, 56.0, 9.0, 57.0], from_way=True),
            Area(RELATION_ID, {
                "name": "Nationalpark Mols Bjerge",
                "boundary": "national_park",
                "protect_class": "2",
            }, [10.4, 56.08, 10.9, 56.35]),
            Area(50046, {
                "name": "Danmark", "ISO3166-1": "DK",
                "boundary": "administrative", "admin_level": "2",
            }, [7.0, 54.0, 13.0, 58.0]),
        ]

        class Handler:
            def apply_file(self, filename, locations=False):
                self.filename = filename
                self.locations = locations
                for area in areas:
                    self.area(area)

        class Factory:
            def create_multipolygon(self, area):
                return area

        class Geometry:
            def __init__(self, bounds):
                self.bounds = bounds
                self.is_empty = False

        fake_osmium = SimpleNamespace(
            SimpleHandler=Handler,
            geom=SimpleNamespace(WKBFactory=lambda: Factory()),
        )
        fake_wkb = SimpleNamespace(
            loads=lambda area, hex=True: Geometry(area.bounds))
        with patch.object(pbf, "_load_dependencies",
                          return_value=(fake_osmium, fake_wkb)):
            elements, scope = pbf._read_pbf_areas(
                Path("synthetic.osm.pbf"), "DK")

        self.assertEqual(
            [(row["type"], row["id"]) for row in elements],
            [("relation", RELATION_ID), ("way", 22)],
        )
        self.assertEqual(elements[0]["center"], {
            "lat": 56.215, "lon": 10.65})
        self.assertEqual(scope, SCOPE)


class SourcePbfCliTests(unittest.TestCase):
    def test_end_to_end_local_discovery_and_validator_touch_no_network(self):
        result = _run_local()
        self.assertEqual(result["exit"], 0, result["stderr"])
        self.assertEqual(result["reader_calls"], 1)
        self.assertEqual(result["network_calls"], 0)
        self.assertFalse(result["canonical_exists"])
        self.assertFalse(result["cache_exists"])
        self.assertEqual(result["report"]["candidate_count"], 1)
        self.assertEqual(
            result["report"]["candidates"][0]["osm_relation_id"],
            RELATION_ID)
        provenance = result["provenance"]
        self.assertEqual(
            provenance["receipt_kind"],
            "local-pbf-discovery-provenance")
        self.assertEqual(provenance["scope"], SCOPE)
        self.assertEqual(
            provenance["source_pbf"]["sha256"],
            hashlib.sha256(SOURCE_BYTES).hexdigest())
        self.assertEqual(
            provenance["source_pbf"]["expected_sha256"],
            provenance["source_pbf"]["sha256"])
        self.assertEqual(result["validator_exit"], 0,
                         result["validator_result"])
        self.assertEqual(
            result["validator_result"]["evidence_kind"], "local-pbf")
        self.assertEqual(result["selected"]["osm_relation_id"], RELATION_ID)
        self.assertEqual(len(result["index"]), 1)

    def test_wrong_hash_fails_before_parse_or_network_and_writes_no_evidence(self):
        result = _run_local(expected_sha="0" * 64)
        self.assertEqual(result["exit"], 1)
        self.assertEqual(result["reader_calls"], 0)
        self.assertEqual(result["network_calls"], 0)
        self.assertIsNone(result["report"])
        self.assertIsNone(result["provenance"])
        self.assertIn("does not match the expected hash", result["stderr"])

    def test_wrong_scope_fails_closed_without_outputs(self):
        def wrong_scope(*_args):
            raise pbf.SourcePbfError(
                "source PBF does not contain the DK administrative scope")

        result = _run_local(reader=wrong_scope)
        self.assertEqual(result["exit"], 1)
        self.assertEqual(result["network_calls"], 0)
        self.assertIsNone(result["report"])
        self.assertIsNone(result["provenance"])
        self.assertIn("does not contain the DK", result["stderr"])

    def test_mid_discovery_tamper_is_detected_before_outputs(self):
        def tamper(path, _region):
            path.write_bytes(SOURCE_BYTES + b"tampered")
            return ([_element(
                "relation", RELATION_ID, "Nationalpark Mols Bjerge")],
                dict(SCOPE))

        result = _run_local(reader=tamper)
        self.assertEqual(result["exit"], 1)
        self.assertEqual(result["network_calls"], 0)
        self.assertIsNone(result["report"])
        self.assertIsNone(result["provenance"])
        self.assertIn("changed while discovery", result["stderr"])

    def test_accidental_overpass_call_is_stopped_before_urlopen(self):
        def accidental_network(*_args):
            seed.fetch_overpass("should never leave the process")
            raise AssertionError("unreachable")

        result = _run_local(reader=accidental_network)
        self.assertEqual(result["exit"], 1)
        self.assertEqual(result["network_calls"], 0)
        self.assertIsNone(result["report"])
        self.assertIsNone(result["provenance"])
        self.assertIn("network access is forbidden", result["stderr"])

    def test_cli_rejects_mixed_local_and_overpass_evidence(self):
        result = _run_local(extra=("--overpass-receipt", "receipt.json"))
        self.assertEqual(result["exit"], 2)
        self.assertEqual(result["reader_calls"], 0)
        self.assertEqual(result["network_calls"], 0)
        self.assertIn("mutually exclusive", result["stderr"])

    def test_missing_and_unreadable_source_files_fail_closed(self):
        with TemporaryDirectory() as temporary:
            missing = Path(temporary) / "missing.osm.pbf"
            with self.assertRaisesRegex(pbf.SourcePbfError, "unavailable"):
                pbf.inspect_source_pbf(missing, "0" * 64, "DK")

            unreadable = Path(temporary) / "unreadable.osm.pbf"
            unreadable.write_bytes(SOURCE_BYTES)
            with patch.object(
                    Path, "open", side_effect=PermissionError("denied")):
                with self.assertRaisesRegex(
                        pbf.SourcePbfError, "unreadable"):
                    pbf.inspect_source_pbf(
                        unreadable,
                        hashlib.sha256(SOURCE_BYTES).hexdigest(),
                        "DK",
                    )

    def test_existing_provenance_target_is_never_overwritten(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "provenance.json"
            path.write_text("preserve", encoding="utf-8")
            with self.assertRaisesRegex(
                    pbf.SourcePbfError, "already exists"):
                pbf.write_new_json(path, {"new": True}, "PBF provenance")
            self.assertEqual(path.read_text(), "preserve")


class ValidatorPbfProvenanceTests(unittest.TestCase):
    def _fixture(self, root):
        source = root / "denmark.osm.pbf"
        report = root / "report.json"
        source.write_bytes(SOURCE_BYTES)
        report.write_text(json.dumps({
            "schema_version": 1,
            "requested_region_codes": ["DK"],
            "candidate_count": 1,
            "candidates": [{
                "index_row": [
                    "nationalpark-mols-bjerge-dk",
                    "Nationalpark Mols Bjerge", "Denmark", 56.2, 10.6,
                ],
                "osm_relation_id": RELATION_ID,
            }],
            "attribution": validator.ATTRIBUTION,
        }), encoding="utf-8")
        provenance = {
            "schema_version": 1,
            "receipt_kind": "local-pbf-discovery-provenance",
            "producer": "scripts/seed-areas.py",
            "parser": "pyosmium-area-v1",
            "attribution": validator.ATTRIBUTION,
            "requested_region_codes": ["DK"],
            "canonical_source_path_label": "data/raw/denmark.osm.pbf",
            "source_pbf": {
                "resolved_path": str(source.resolve()),
                "bytes": len(SOURCE_BYTES),
                "sha256": hashlib.sha256(SOURCE_BYTES).hexdigest(),
                "expected_sha256": hashlib.sha256(SOURCE_BYTES).hexdigest(),
            },
            "scope": dict(SCOPE),
            "candidate_occurrence_count": 1,
            "candidate_count": 1,
            "discovery_report": {
                "bytes": len(report.read_bytes()),
                "sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
            },
        }
        return source, report, provenance

    def test_trusted_prefilter_identity_is_required_exactly(self):
        with TemporaryDirectory() as temporary:
            source, report, provenance = self._fixture(Path(temporary))
            document = json.loads(report.read_text())
            identity = {
                "sha256": hashlib.sha256(SOURCE_BYTES).hexdigest(),
                "bytes": len(SOURCE_BYTES),
            }
            errors = []
            result = validator._validate_pbf_provenance(
                provenance, document, report, source, errors,
                expected_source_identity=identity)
            self.assertEqual(errors, [])
            self.assertEqual(result, identity)

            errors = []
            validator._validate_pbf_provenance(
                provenance, document, report, source, errors,
                expected_source_identity={"sha256": "0" * 64,
                                          "bytes": len(SOURCE_BYTES)})
            self.assertIn(
                "local PBF provenance disagrees with trusted prefilter input",
                errors)

    def test_provenance_without_source_pbf_fails_closed(self):
        with TemporaryDirectory() as temporary:
            source, report, provenance = self._fixture(Path(temporary))
            document = json.loads(report.read_text())
            errors = []
            validator._validate_pbf_provenance(
                provenance, document, report, None, errors)
            self.assertIn(
                "local PBF provenance requires the source PBF", errors)

    def test_candidate_outside_sealed_scope_is_rejected(self):
        with TemporaryDirectory() as temporary:
            source, report, provenance = self._fixture(Path(temporary))
            document = json.loads(report.read_text())
            document["candidates"][0]["index_row"][3:5] = [1.0, 1.0]
            report.write_text(json.dumps(document), encoding="utf-8")
            provenance["discovery_report"] = {
                "bytes": len(report.read_bytes()),
                "sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
            }
            errors = []
            validator._validate_pbf_provenance(
                provenance, document, report, source, errors)
            self.assertIn(
                "candidate[0] lies outside the sealed PBF scope", errors)


_REAL_PBF_TOOLS = bool(
    shutil.which("osmium")
    and importlib.util.find_spec("osmium")
    and importlib.util.find_spec("shapely"))


@unittest.skipUnless(_REAL_PBF_TOOLS,
                     "real synthetic PBF test requires osmium/pyosmium/shapely")
class RealSyntheticPbfIntegration(unittest.TestCase):
    def test_real_synthetic_pbf_round_trip(self):
        osm = """<?xml version='1.0' encoding='UTF-8'?>
<osm version='0.6' generator='test'>
  <node id='1' lat='54.0' lon='7.0'/><node id='2' lat='54.0' lon='13.0'/>
  <node id='3' lat='58.0' lon='13.0'/><node id='4' lat='58.0' lon='7.0'/>
  <node id='11' lat='56.08' lon='10.4'/><node id='12' lat='56.08' lon='10.9'/>
  <node id='13' lat='56.35' lon='10.9'/><node id='14' lat='56.35' lon='10.4'/>
  <way id='100'><nd ref='1'/><nd ref='2'/><nd ref='3'/><nd ref='4'/><nd ref='1'/></way>
  <way id='101'><nd ref='11'/><nd ref='12'/><nd ref='13'/><nd ref='14'/><nd ref='11'/></way>
  <relation id='50046'>
    <member type='way' ref='100' role='outer'/>
    <tag k='type' v='boundary'/><tag k='boundary' v='administrative'/>
    <tag k='admin_level' v='2'/><tag k='ISO3166-1' v='DK'/>
    <tag k='name' v='Danmark'/>
  </relation>
  <relation id='7046785'>
    <member type='way' ref='101' role='outer'/>
    <tag k='type' v='multipolygon'/><tag k='boundary' v='national_park'/>
    <tag k='protect_class' v='2'/><tag k='name' v='Nationalpark Mols Bjerge'/>
  </relation>
</osm>
"""
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            xml = root / "fixture.osm"
            source = root / "denmark.osm.pbf"
            xml.write_text(osm, encoding="utf-8")
            completed = subprocess.run(
                [shutil.which("osmium"), "cat", str(xml), "-o", str(source)],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env={"PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"},
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            elements, scope, identity = pbf.inspect_source_pbf(
                source, digest, "DK")
            self.assertEqual(scope["admin_boundary_osm_id"], 50046)
            self.assertEqual(
                [row["id"] for row in elements if row["type"] == "relation"],
                [RELATION_ID])
            self.assertEqual(identity["sha256"], digest)


if __name__ == "__main__":
    unittest.main()
