"""Offline tests for publish_areas.py's opt-in structured report."""
import importlib.util
import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

try:
    import shapely  # noqa: F401
    HAVE_SHAPELY = True
except ImportError:
    HAVE_SHAPELY = False

_HERE = Path(__file__).resolve().parent
_SPEC = importlib.util.spec_from_file_location(
    "publish_areas_report_module", _HERE / "publish_areas.py")
publish = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(publish)

_AREA_ID = "nationalpark-mols-bjerge-dk"
_AREA_NAME = "Nationalpark Mols Bjerge"
_RELATION_ID = 7046785


def _index():
    return [[_AREA_ID, _AREA_NAME, "Denmark", 56.2, 10.6, None, None, _RELATION_ID]]


def _trails():
    return {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {
                "name": "Mols Trail",
                "kind": "trail",
                "source": "name-stitch",
                "length_mi": 1.0,
                "sac_scale": "",
                "trail_visibility": "",
            },
            "geometry": {
                "type": "MultiLineString",
                "coordinates": [[[10.5, 56.15], [10.6, 56.2]]],
            },
        }],
    }


def _boundary():
    return [{
        "name": _AREA_NAME,
        "bbox": (10.4, 56.08, 10.9, 56.35),
        "rings": [[
            (10.4, 56.08), (10.9, 56.08), (10.9, 56.35),
            (10.4, 56.35), (10.4, 56.08),
        ]],
        "osm_id": _RELATION_ID,
        "osm_type": "relation",
    }]


@unittest.skipUnless(HAVE_SHAPELY, "shapely not installed")
class PublishReport(unittest.TestCase):
    def _run(self, index, trails, boundaries, *, include_report=True,
             validate_result=None):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            index_path = root / "index.json"
            trails_path = root / "mols.trails.geojson"
            output_dir = root / "published"
            report_path = root / "reports" / "publish.json"
            index_path.write_text(json.dumps(index), encoding="utf-8")
            trails_path.write_text(json.dumps(trails), encoding="utf-8")
            output_dir.mkdir()
            original_index = index_path.read_bytes()
            argv = [
                "--trails", str(trails_path),
                "--hiking", str(root / "mols.osm.pbf"),
                "--index", str(index_path),
                "--out-dir", str(output_dir),
                "--state", "Denmark",
                "--dry-run",
                "--no-boundary-fetch",
                "--no-elevation",
            ]
            if include_report:
                argv.extend(["--report-json", str(report_path)])
            stdout = io.StringIO()
            stderr = io.StringIO()
            validation_patch = (
                patch.object(publish, "validate", return_value=validate_result)
                if validate_result is not None else patch.object(
                    publish, "validate", wraps=publish.validate)
            )
            with (
                patch.object(publish.areamod, "merge_areas", return_value=boundaries),
                validation_patch,
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                rc = publish.main(argv)
            report = json.loads(report_path.read_text()) if report_path.exists() else None
            return {
                "rc": rc,
                "report": report,
                "report_exists": report_path.exists(),
                "index_unchanged": index_path.read_bytes() == original_index,
                "published_files": sorted(path.name for path in output_dir.iterdir()),
                "stdout": stdout.getvalue(),
                "stderr": stderr.getvalue(),
            }

    def test_zero_index_writes_explicit_noop_report(self):
        result = self._run([], {"type": "FeatureCollection", "features": []}, [])
        report = result["report"]
        self.assertEqual(result["rc"], 0)
        self.assertEqual(report["index_area_count"], 0)
        self.assertEqual(report["validated_areas"], [])
        self.assertEqual(report["published_areas"], [])
        self.assertEqual(report["skipped_areas"], [])
        self.assertEqual(report["validation_failures"], [])
        self.assertTrue(report["dry_run"])
        self.assertFalse(report["canonical_write"])

    def test_one_validated_dry_run_record_carries_exact_identity_and_counts(self):
        result = self._run(_index(), _trails(), _boundary())
        report = result["report"]
        expected = {
            "area_id": _AREA_ID,
            "name": _AREA_NAME,
            "osm_relation_id": _RELATION_ID,
            "trail_count": 1,
            "total_miles": report["published_areas"][0]["total_miles"],
        }
        self.assertEqual(result["rc"], 0)
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["state"], "Denmark")
        self.assertEqual(report["write_mode"], "dry-run")
        self.assertEqual(report["index_area_count"], 1)
        self.assertEqual(report["validated_areas"], [expected])
        self.assertEqual(report["published_areas"], [expected])
        self.assertEqual(report["routes"], {"kept": 0, "dropped": 0})
        self.assertEqual(result["published_files"], [])
        self.assertTrue(result["index_unchanged"])

    def test_skip_reason_is_structured(self):
        result = self._run(_index(), _trails(), [])
        report = result["report"]
        self.assertEqual(report["validated_areas"], [])
        self.assertEqual(report["published_areas"], [])
        self.assertEqual(report["skipped_areas"], [{
            "area_id": _AREA_ID,
            "reason": "no boundary in PBF",
        }])
        self.assertEqual(report["validation_failures"], [])

    def test_validation_failure_problems_are_structured(self):
        result = self._run(
            _index(), _trails(), _boundary(), validate_result=["forced failure"])
        report = result["report"]
        self.assertEqual(report["validated_areas"], [])
        self.assertEqual(report["published_areas"], [])
        self.assertEqual(report["skipped_areas"], [])
        self.assertEqual(report["validation_failures"], [{
            "area_id": _AREA_ID,
            "problems": ["forced failure"],
        }])

    def test_omitting_report_preserves_dry_run_behavior_and_creates_no_report(self):
        result = self._run(
            _index(), _trails(), _boundary(), include_report=False)
        self.assertEqual(result["rc"], 0)
        self.assertFalse(result["report_exists"])
        self.assertTrue(result["index_unchanged"])
        self.assertEqual(result["published_files"], [])
        self.assertIn("=== published 1 areas (dry-run, nothing written) ===",
                      result["stdout"])


if __name__ == "__main__":
    unittest.main()
