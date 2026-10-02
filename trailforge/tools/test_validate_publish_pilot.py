"""Pure-stdlib synthetic tests for the Mols Bjerge pilot validator."""
import copy
import importlib.util
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

_HERE = Path(__file__).resolve().parent
_SPEC = importlib.util.spec_from_file_location(
    "validate_publish_pilot_module", _HERE / "validate_publish_pilot.py")
validator = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(validator)

RELATION_ID = 7046785


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, str):
        path.write_text(value, encoding="utf-8")
    else:
        path.write_text(json.dumps(value), encoding="utf-8")


def _candidate(**changes):
    record = {
        "index_row": [
            validator.AREA_ID,
            validator.AREA_NAME,
            validator.STATE,
            56.2,
            10.6,
        ],
        "osm_relation_id": RELATION_ID,
    }
    record.update(changes)
    return record


def _discovery_report(candidates):
    return {
        "schema_version": 1,
        "requested_region_codes": ["DK"],
        "candidate_count": len(candidates),
        "candidates": candidates,
        "attribution": validator.ATTRIBUTION,
    }


class DiscoveryValidation(unittest.TestCase):
    def _run(self, report):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_path = root / "report.json"
            selected_path = root / "selected.json"
            index_path = root / "index.json"
            result_path = root / "result.json"
            if isinstance(report, str):
                report_path.write_text(report, encoding="utf-8")
            else:
                _write(report_path, report)
            rc = validator.main([
                "discovery",
                "--discovery-report", str(report_path),
                "--expected-osm-relation-id", str(RELATION_ID),
                "--selected-record-out", str(selected_path),
                "--index-out", str(index_path),
                "--result-json", str(result_path),
            ])
            return {
                "rc": rc,
                "result": json.loads(result_path.read_text()),
                "selected": json.loads(selected_path.read_text())
                if selected_path.exists() else None,
                "index": json.loads(index_path.read_text())
                if index_path.exists() else None,
            }

    def test_success_selects_only_mols_and_writes_one_row_index(self):
        other = {
            "index_row": ["nationalpark-thy-dk", "Nationalpark Thy",
                          "Denmark", 56.9, 8.4],
            "osm_relation_id": 7045682,
        }
        result = self._run(_discovery_report([other, _candidate()]))
        self.assertEqual(result["rc"], 0)
        self.assertEqual(result["result"]["status"], "ok")
        self.assertEqual(result["selected"]["index_row"][0], validator.AREA_ID)
        self.assertEqual(result["selected"]["osm_relation_id"], RELATION_ID)
        self.assertEqual(result["index"], [[
            validator.AREA_ID, validator.AREA_NAME, validator.STATE,
            56.2, 10.6, None, None, RELATION_ID,
        ]])

    def test_every_target_identity_failure_is_closed_and_diagnostic(self):
        wrong_name = _candidate(index_row=[
            validator.AREA_ID, "Mols Bjerge", validator.STATE, 56.2, 10.6])
        wrong_state = _candidate(index_row=[
            validator.AREA_ID, validator.AREA_NAME, "Arizona", 56.2, 10.6])
        cases = {
            "no-target": [],
            "duplicate-target": [_candidate(), _candidate()],
            "wrong-name": [wrong_name],
            "wrong-state": [wrong_state],
            "wrong-relation": [_candidate(osm_relation_id=1)],
            "missing-relation": [_candidate(osm_relation_id=None)],
        }
        for label, candidates in cases.items():
            with self.subTest(label=label):
                result = self._run(_discovery_report(candidates))
                self.assertEqual(result["rc"], 1)
                self.assertEqual(result["result"]["status"], "failed")
                self.assertTrue(result["result"]["errors"])
                self.assertIsNone(result["selected"])
                self.assertIsNone(result["index"])

    def test_malformed_json_still_writes_failed_result(self):
        result = self._run("{not-json")
        self.assertEqual(result["rc"], 1)
        self.assertEqual(result["result"]["status"], "failed")
        self.assertTrue(any("invalid discovery report" in error
                            for error in result["result"]["errors"]))


class _QA:
    def __init__(self, root):
        self.root = root
        self.trail = {
            "type": "Feature",
            "properties": {
                "name": "Mols Trail",
                "area": validator.AREA_NAME,
                "kind": "trail",
            },
            "geometry": {
                "type": "MultiLineString",
                "coordinates": [[[10.5, 56.15], [10.6, 56.2]]],
            },
        }
        candidate = _candidate()
        selected = {
            "schema_version": 1,
            **candidate,
            "attribution": validator.ATTRIBUTION,
        }
        area_record = {
            "area_id": validator.AREA_ID,
            "name": validator.AREA_NAME,
            "osm_relation_id": RELATION_ID,
            "trail_count": 1,
            "total_miles": 1.0,
        }
        self.documents = {
            "manifest": {
                "schema_version": 1,
                "branch_ref": "chat/denmark-trails",
                "source_sha": "a" * 40,
                "inputs": validator._expected_inputs(RELATION_ID),
                "visual_review_url": validator.VISUAL_REVIEW_URL,
                "attribution": validator.ATTRIBUTION,
            },
            "raw": {
                "type": "FeatureCollection",
                "features": [{
                    "type": "Feature",
                    "properties": {"name": "raw way"},
                    "geometry": {"type": "LineString",
                                 "coordinates": [[10.5, 56.15], [10.6, 56.2]]},
                }],
            },
            "trails": {"type": "FeatureCollection", "features": [self.trail]},
            "removed": {"type": "FeatureCollection", "features": []},
            "ingest_dropped": {"type": "FeatureCollection", "features": []},
            "areas": {
                "type": "FeatureCollection",
                "features": [{
                    "type": "Feature",
                    "properties": {"name": validator.AREA_NAME},
                    "geometry": {"type": "Polygon", "coordinates": []},
                }],
            },
            "curation": {"w10": "kept"},
            "curation_diff": {
                "has_baseline": False,
                "new_removed": [],
                "new_kept": [],
                "reason_changed": [],
            },
            "selected_discovery": selected,
            "discovery_report": _discovery_report([candidate]),
            "assembly_report": {
                "schema_version": 1,
                "status": "ok",
                "failure": None,
                "exact_area_required": True,
                "area_query": validator.AREA_NAME,
                "expected_area_relation_id": RELATION_ID,
                "boundary_match_count": 1,
                "boundary_matches": [{
                    "name": validator.AREA_NAME,
                    "osm_type": "relation",
                    "osm_id": RELATION_ID,
                }],
                "boundary": {
                    "name": validator.AREA_NAME,
                    "osm_type": "relation",
                    "osm_id": RELATION_ID,
                },
                "clip_applied": True,
                "pre_clip_trail_count": 1,
                "post_clip_trail_count": 1,
                "assembled_trail_count": 1,
                "coverage": {
                    "raw_trailish_ways": 2,
                    "named_trailish_ways": 1,
                    "hiking_route_relations": 0,
                    "route_relations_total": 0,
                    "destination_pois": 0,
                },
            },
            "publish_report": {
                "schema_version": 1,
                "state": validator.STATE,
                "dry_run": True,
                "write_mode": "dry-run",
                "canonical_write": False,
                "index_area_count": 1,
                "validated_areas": [copy.deepcopy(area_record)],
                "published_areas": [copy.deepcopy(area_record)],
                "skipped_areas": [],
                "validation_failures": [],
                "routes": {"kept": 0, "dropped": 0},
            },
        }
        for label, relative in validator.REQUIRED_JSON_FILES.items():
            _write(root / relative, self.documents[label])
        _write(root / "README.md",
               f"Visual review: {validator.VISUAL_REVIEW_URL}\n\n"
               f"Attribution: {validator.ATTRIBUTION}\n")
        _write(root / "discovery/discovery.log", "discovery completed\n")
        _write(root / "reports/publish.log", "publisher completed\n")
        _write(root / "viewer/index.html", "<!doctype html><title>QA</title>\n")
        _write(root / "viewer/serve.py", "print('serve')\n")

    def rewrite(self, label):
        _write(self.root / validator.REQUIRED_JSON_FILES[label], self.documents[label])


class FinalValidation(unittest.TestCase):
    def _run(self, mutate=None, *, remove=None, malformed=None, optional=None):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            qa = _QA(root)
            if mutate:
                mutate(qa)
            if remove:
                (root / remove).unlink()
            if malformed:
                (root / malformed).write_text("{not-json", encoding="utf-8")
            if optional is not None:
                _write(root / validator.OPTIONAL_DROPPED_ROUTES, optional)
            result_path = root / "validation/final.json"
            rc = validator.main([
                "final",
                "--qa-dir", str(root),
                "--expected-osm-relation-id", str(RELATION_ID),
                "--result-json", str(result_path),
            ])
            return rc, json.loads(result_path.read_text())

    def assert_rejected(self, mutate=None, **kwargs):
        rc, result = self._run(mutate, **kwargs)
        self.assertEqual(rc, 1)
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["errors"])

    def test_complete_fixture_passes(self):
        rc, result = self._run()
        self.assertEqual(rc, 0)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["assembled_trail_count"], 1)
        self.assertEqual(result["errors"], [])

    def test_optional_dropped_routes_is_parsed_when_present(self):
        rc, result = self._run(optional={
            "type": "FeatureCollection", "features": []})
        self.assertEqual(rc, 0)
        self.assertIn(validator.OPTIONAL_DROPPED_ROUTES, result["checked_files"])
        self.assert_rejected(optional={"not": "geojson"})

    def test_exact_clip_and_coverage_failures_are_independent(self):
        def no_clip(qa):
            qa.documents["assembly_report"]["clip_applied"] = False
            qa.rewrite("assembly_report")

        def zero_raw(qa):
            qa.documents["assembly_report"]["coverage"]["raw_trailish_ways"] = 0
            qa.rewrite("assembly_report")

        def zero_named(qa):
            qa.documents["assembly_report"]["coverage"]["named_trailish_ways"] = 0
            qa.rewrite("assembly_report")

        def wrong_relation(qa):
            qa.documents["assembly_report"]["boundary"]["osm_id"] = 1
            qa.rewrite("assembly_report")

        for label, mutate in {
            "no-exact-clip": no_clip,
            "zero-raw": zero_raw,
            "zero-named": zero_named,
            "wrong-boundary-relation": wrong_relation,
        }.items():
            with self.subTest(label=label):
                self.assert_rejected(mutate)

    def test_zero_unnamed_and_disconnected_trails_are_rejected(self):
        def zero(qa):
            qa.documents["trails"]["features"] = []
            report = qa.documents["assembly_report"]
            report["pre_clip_trail_count"] = 0
            report["post_clip_trail_count"] = 0
            report["assembled_trail_count"] = 0
            qa.rewrite("trails")
            qa.rewrite("assembly_report")

        def unnamed(qa):
            qa.documents["trails"]["features"][0]["properties"]["name"] = "Unnamed 123"
            qa.rewrite("trails")

        def disconnected(qa):
            qa.documents["trails"]["features"][0]["geometry"]["coordinates"] = [
                [[10.5, 56.15], [10.6, 56.2]],
                [[10.7, 56.25], [10.8, 56.3]],
            ]
            qa.rewrite("trails")

        for label, mutate in {
            "zero": zero,
            "unnamed": unnamed,
            "disconnected": disconnected,
        }.items():
            with self.subTest(label=label):
                self.assert_rejected(mutate)

    def test_publisher_cardinality_identity_skip_and_failure_are_rejected(self):
        def zero_index(qa):
            report = qa.documents["publish_report"]
            report["index_area_count"] = 0
            report["validated_areas"] = []
            report["published_areas"] = []
            qa.rewrite("publish_report")

        def zero_result(qa):
            report = qa.documents["publish_report"]
            report["validated_areas"] = []
            report["published_areas"] = []
            qa.rewrite("publish_report")

        def multiple_result(qa):
            report = qa.documents["publish_report"]
            report["validated_areas"] *= 2
            report["published_areas"] *= 2
            qa.rewrite("publish_report")

        def wrong_result(qa):
            report = qa.documents["publish_report"]
            report["validated_areas"][0]["area_id"] = "wrong-dk"
            report["published_areas"][0]["area_id"] = "wrong-dk"
            qa.rewrite("publish_report")

        def skipped(qa):
            qa.documents["publish_report"]["skipped_areas"] = [
                {"area_id": validator.AREA_ID, "reason": "no trails"}]
            qa.rewrite("publish_report")

        def failed(qa):
            qa.documents["publish_report"]["validation_failures"] = [
                {"area_id": validator.AREA_ID, "problems": ["no trails"]}]
            qa.rewrite("publish_report")

        for label, mutate in {
            "zero-index": zero_index,
            "zero": zero_result,
            "multiple": multiple_result,
            "wrong": wrong_result,
            "skip": skipped,
            "validation-failure": failed,
        }.items():
            with self.subTest(label=label):
                self.assert_rejected(mutate)

    def test_non_dry_run_and_canonical_write_are_rejected(self):
        def non_dry_run(qa):
            report = qa.documents["publish_report"]
            report["dry_run"] = False
            report["write_mode"] = "canonical"
            qa.rewrite("publish_report")

        def canonical_write(qa):
            qa.documents["publish_report"]["canonical_write"] = True
            qa.rewrite("publish_report")

        self.assert_rejected(non_dry_run)
        self.assert_rejected(canonical_write)

    def test_malformed_json_is_rejected_with_a_written_result(self):
        self.assert_rejected(malformed="reports/assembly.json")

    def test_each_mandatory_artifact_is_required(self):
        required = [
            *validator.REQUIRED_JSON_FILES.values(),
            *validator.REQUIRED_TEXT_FILES.values(),
        ]
        for relative in required:
            with self.subTest(relative=relative):
                self.assert_rejected(remove=relative)


if __name__ == "__main__":
    unittest.main()
