"""Offline exact-boundary and structured-report tests for assemble.py."""
import copy
import importlib.util
import io
import json
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

try:
    from shapely.geometry import box
    HAVE_SHAPELY = True
except ImportError:
    HAVE_SHAPELY = False

_HERE = Path(__file__).resolve().parent
_SPEC = importlib.util.spec_from_file_location(
    "assemble_pilot_module", _HERE / "assemble.py")
assemble = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(assemble)
import areas  # noqa: E402


class _Trail:
    def __init__(self, name="Mols Trail"):
        self._feature = {
            "type": "Feature",
            "properties": {
                "name": name,
                "kind": "trail",
                "area": "Nationalpark Mols Bjerge",
                "source": "name-stitch",
                "length_mi": 1.0,
                "member_ways": [10],
                "destinations": [],
                "welds": [],
                "network": "",
                "operator": "",
                "sac_scale": "",
                "trail_visibility": "",
                "removed_reason": None,
                "removed_category": None,
                "ckey": "w10",
            },
            "geometry": {
                "type": "MultiLineString",
                "coordinates": [[[10.5, 56.15], [10.6, 56.2]]],
            },
        }

    def to_feature(self):
        return copy.deepcopy(self._feature)


def _boundary(name="Nationalpark Mols Bjerge", osm_type="relation", osm_id=7046785):
    return {
        "name": name,
        "tags": {"name": name, "boundary": "national_park"},
        "geom": box(10.4, 56.08, 10.9, 56.35),
        "osm_type": osm_type,
        "osm_id": osm_id,
    }


@unittest.skipUnless(HAVE_SHAPELY, "shapely not installed")
class ExactPilotAssembly(unittest.TestCase):
    def _run(self, boundaries, *, ways=None, extra_args=()):
        ways = ways if ways is not None else {
            10: {"tags": {"highway": "path", "name": "Mols Trail"},
                 "nodes": [1, 2]},
        }
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "mols-bjerge.trails.geojson"
            report = root / "assembly.json"
            argv = [
                "--in", str(root / "mols.osm.pbf"),
                "--out", str(output),
                "--only-area", "Nationalpark Mols Bjerge",
                "--require-exact-area",
                "--expected-area-relation-id", "7046785",
                "--report-json", str(report),
                *extra_args,
            ]
            stderr = io.StringIO()
            with (
                patch.object(assemble, "read_pbf", return_value=({}, ways, {}, [])),
                patch.object(areas, "assemble_areas", return_value=boundaries),
                patch.object(areas, "merge_areas", return_value=[]),
                patch.object(assemble.model, "assemble", return_value=[_Trail()]) as build,
                redirect_stderr(stderr),
            ):
                rc = assemble.main(argv)
            payload = json.loads(report.read_text())
            output_payload = json.loads(output.read_text()) if output.exists() else None
            files = sorted(path.name for path in root.iterdir())
            return rc, payload, output_payload, files, build.call_count, stderr.getvalue()

    def test_exact_relation_succeeds_and_reports_clip_and_coverage(self):
        rc, report, output, files, calls, _ = self._run(
            [_boundary()], extra_args=("--per-area-merge", "--region", "dk"))

        self.assertEqual(rc, 0)
        self.assertEqual(calls, 1)
        self.assertEqual(report["status"], "ok")
        self.assertTrue(report["exact_area_required"])
        self.assertEqual(report["boundary_match_count"], 1)
        self.assertEqual(report["boundary"], {
            "name": "Nationalpark Mols Bjerge",
            "osm_type": "relation",
            "osm_id": 7046785,
        })
        self.assertTrue(report["clip_applied"])
        self.assertEqual(report["pre_clip_trail_count"], 1)
        self.assertEqual(report["post_clip_trail_count"], 1)
        self.assertEqual(report["coverage"]["raw_trailish_ways"], 1)
        self.assertEqual(report["coverage"]["named_trailish_ways"], 1)
        self.assertEqual(len(output["features"]), 1)
        self.assertIn("mols-bjerge.areas.geojson", files)
        self.assertIn("mols-bjerge.curation-diff.json", files)

    def test_zero_coverage_counters_are_reported_faithfully(self):
        rc, report, _, _, _, _ = self._run([_boundary()], ways={})
        self.assertEqual(rc, 0)
        self.assertEqual(report["coverage"]["raw_trailish_ways"], 0)
        self.assertEqual(report["coverage"]["named_trailish_ways"], 0)

    def test_missing_duplicate_wrong_name_way_and_wrong_relation_fail_closed(self):
        cases = {
            "missing": [],
            "duplicate": [_boundary(), _boundary()],
            "wrong-name": [_boundary(name="nationalpark mols bjerge")],
            "way-source": [_boundary(osm_type="way")],
            "wrong-relation": [_boundary(osm_id=1)],
        }
        for label, boundaries in cases.items():
            with self.subTest(label=label):
                rc, report, output, files, calls, stderr = self._run(boundaries)
                self.assertEqual(rc, 2)
                self.assertEqual(calls, 0)
                self.assertEqual(report["status"], "failed")
                self.assertFalse(report["clip_applied"])
                self.assertIsNone(output)
                self.assertEqual(files, ["assembly.json"])
                self.assertIn("exact-area validation failed", stderr)

    def test_legacy_substring_union_path_still_clips_case_insensitively(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "legacy.trails.geojson"
            boundaries = [
                _boundary(),
                _boundary(name="Mols Bjerge Annex", osm_id=99),
            ]
            with (
                patch.object(assemble, "read_pbf", return_value=({}, {}, {}, [])),
                patch.object(areas, "assemble_areas", return_value=boundaries),
                patch.object(assemble.model, "assemble", return_value=[_Trail()]),
                patch.object(areas, "union_matching", wraps=areas.union_matching) as union,
            ):
                rc = assemble.main([
                    "--in", str(root / "legacy.osm.pbf"),
                    "--out", str(output),
                    "--only-area", "mols bjerge",
                ])
            payload = json.loads(output.read_text())
            self.assertEqual(rc, 0)
            self.assertEqual(len(payload["features"]), 1)
            union.assert_called_once()
            self.assertEqual(union.call_args.args[1], "mols bjerge")

    def test_legacy_missing_substring_match_still_warns_and_leaves_output_unclipped(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "legacy.trails.geojson"
            stderr = io.StringIO()
            with (
                patch.object(assemble, "read_pbf", return_value=({}, {}, {}, [])),
                patch.object(areas, "assemble_areas", return_value=[]),
                patch.object(assemble.model, "assemble", return_value=[_Trail()]),
                redirect_stderr(stderr),
            ):
                rc = assemble.main([
                    "--in", str(root / "legacy.osm.pbf"),
                    "--out", str(output),
                    "--only-area", "missing park",
                ])
            payload = json.loads(output.read_text())
            self.assertEqual(rc, 0)
            self.assertEqual(len(payload["features"]), 1)
            self.assertIn("leaving trails unclipped", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
