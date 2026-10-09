"""Offline tests for publish_areas.py's opt-in structured report."""
import argparse
import importlib.util
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
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
             include_preview=False, validate_result=None,
             exact_geometry=None, prior_row=None, nonhiking=None):
        with TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            work = temp_root / "work"
            root = temp_root / "qa"
            work.mkdir()
            root.mkdir()
            index_path = work / "index.json"
            trails_path = work / "mols.trails.geojson"
            output_dir = work / "published"
            report_path = root / "reports" / "publish.json"
            preview_path = root / "reports" / "app-preview.json"
            report_path.parent.mkdir(parents=True)
            index_path.write_text(json.dumps(index), encoding="utf-8")
            trails_path.write_text(json.dumps(trails), encoding="utf-8")
            output_dir.mkdir()
            if prior_row is not None:
                (output_dir / f"{_AREA_ID}.json").write_text(
                    json.dumps(prior_row), encoding="utf-8")
            original_index = index_path.read_bytes()
            argv = [
                "--trails", str(trails_path),
                "--hiking", str(work / "mols.osm.pbf"),
                "--index", str(index_path),
                "--out-dir", str(output_dir),
                "--state", "Denmark",
                "--dry-run",
                "--no-boundary-fetch",
                "--no-elevation",
            ]
            if include_report:
                argv.extend(["--report-json", str(report_path)])
            if include_preview:
                argv.extend([
                    "--preview-json", str(preview_path),
                    "--preview-root", str(root),
                ])
            if exact_geometry is not None:
                exact_path = root / "exact-area.geojson"
                exact_path.write_text(json.dumps({
                    "type": "FeatureCollection",
                    "features": [{
                        "type": "Feature",
                        "properties": {
                            "name": _AREA_NAME,
                            "osm_type": "relation",
                            "osm_id": _RELATION_ID,
                            "geometry_sha256": publish._canonical_json_sha256(
                                exact_geometry),
                        },
                        "geometry": exact_geometry,
                    }],
                }), encoding="utf-8")
                argv.extend(["--exact-boundary", str(exact_path)])
            stdout = io.StringIO()
            stderr = io.StringIO()
            validation_patch = (
                patch.object(publish, "validate", return_value=validate_result)
                if validate_result is not None else patch.object(
                    publish, "validate", wraps=publish.validate)
            )
            with (
                patch.object(publish.areamod, "merge_areas", return_value=boundaries),
                patch.object(
                    publish, "_NONHIKING",
                    publish._NONHIKING if nonhiking is None else nonhiking),
                validation_patch,
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                rc = publish.main(argv)
            report = json.loads(report_path.read_text()) if report_path.exists() else None
            preview = (json.loads(preview_path.read_text())
                       if preview_path.exists() else None)
            return {
                "rc": rc,
                "report": report,
                "preview": preview,
                "preview_exists": preview_path.exists(),
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

    def test_explicit_preview_archives_exact_validated_app_row_only(self):
        result = self._run(
            _index(), _trails(), _boundary(), include_preview=True)
        preview = result["preview"]
        report = result["report"]

        self.assertEqual(result["rc"], 0)
        self.assertEqual(preview["schema_version"], 1)
        self.assertEqual(preview["write_mode"], "dry-run-preview")
        self.assertFalse(preview["canonical_write"])
        self.assertEqual(len(preview["areas"]), 1)
        row = preview["areas"][0]
        self.assertEqual(row["id"], _AREA_ID)
        self.assertEqual(row["trail_count"], 1)
        self.assertEqual(row["trails"][0]["id"], "mols-trail")
        # Publisher recomputes/clamps source geometry before app conversion;
        # the preview preserves that exact app-visible distance rather than the
        # input's synthetic 1.0-mile property.
        self.assertEqual(row["trails"][0]["distanceMi"], 5.17)
        self.assertEqual(
            row["trails"][0]["segments"],
            [[[56.15, 10.5], [56.2, 10.6]]],
        )
        self.assertEqual(report["preview"], {
            "enabled": True,
            "area_count": 1,
            "area_ids": [_AREA_ID],
            "assembly_feature_count": 1,
            "assembly_property_miles": 1.0,
            "preview_distance_miles": 5.17,
            "publisher_total_miles": 5.2,
            "postclip": report["preview"]["postclip"],
            "finalization": {
                "nonhiking_sidecar_ids": [],
                "nonhiking_removed_ids": [],
                "nonhiking_would_empty": False,
                "degenerate_removed": [],
                "prior_parking_present": False,
                "trail_count": 1,
                "total_mi": 5.2,
            },
            "trails": [{
                "id": "mols-trail", "name": "Mols Trail",
                "distance_mi": 5.17, "assembly_length_mi": 1.0,
            }],
        })
        self.assertEqual(result["published_files"], [])
        self.assertTrue(result["index_unchanged"])

    def test_omitting_preview_preserves_no_app_row_output(self):
        result = self._run(_index(), _trails(), _boundary())
        self.assertFalse(result["preview_exists"])
        self.assertEqual(result["report"]["preview"], {
            "enabled": False,
            "area_count": 0,
            "area_ids": [],
            "assembly_feature_count": 1,
            "assembly_property_miles": 1.0,
            "preview_distance_miles": 0,
            "publisher_total_miles": None,
            "postclip": None,
            "finalization": None,
            "trails": [],
        })

    def test_exact_boundary_preserves_hole_ignores_neighbor_and_prunes_remnant(self):
        outer = [
            [10.4, 56.1], [10.8, 56.1], [10.8, 56.35],
            [10.4, 56.35], [10.4, 56.1],
        ]
        hole = [
            [10.55, 56.18], [10.65, 56.18], [10.65, 56.28],
            [10.55, 56.28], [10.55, 56.18],
        ]
        exact = {"type": "Polygon", "coordinates": [outer, hole]}
        lines = {
            "Kept Trail": [[10.45, 56.15], [10.50, 56.15]],
            "Hole Trail": [[10.57, 56.22], [10.62, 56.22]],
            "Mols Neighbor Trail": [[10.82, 56.15], [10.88, 56.15]],
            "Tiny Remnant": [[10.79995, 56.32], [10.90, 56.32]],
        }
        trails = {"type": "FeatureCollection", "features": []}
        for index, (name, coordinates) in enumerate(lines.items()):
            trails["features"].append({
                "type": "Feature",
                "properties": {
                    "name": name, "kind": "trail", "source": "name-stitch",
                    "ckey": f"w{index + 1}", "length_mi": 4.0,
                    "sac_scale": "", "trail_visibility": "",
                },
                "geometry": {"type": "MultiLineString",
                             "coordinates": [coordinates]},
            })
        neighbor = [{
            "name": f"{_AREA_NAME} Neighbor",
            "rings": [[
                (10.81, 56.1), (10.91, 56.1), (10.91, 56.3),
                (10.81, 56.3), (10.81, 56.1),
            ]],
            "osm_id": 999,
            "osm_type": "relation",
        }]

        result = self._run(
            _index(), trails, neighbor, include_preview=True,
            exact_geometry=exact)

        self.assertEqual(result["rc"], 0)
        row = result["preview"]["areas"][0]
        self.assertEqual([trail["name"] for trail in row["trails"]],
                         ["Kept Trail"])
        finalization = result["report"]["preview"]["finalization"]
        self.assertEqual(finalization["degenerate_removed"], [{
            "id": "tiny-remnant", "reason": "isolated",
        }])
        self.assertEqual(result["report"]["preview"]["postclip"][
            "feature_count"], 2)
        self.assertEqual(result["report"]["exact_boundary"][
            "geometry_sha256"], publish._canonical_json_sha256(exact))

    def test_exact_boundary_requires_empty_sidecar_and_fresh_parking(self):
        exact = {"type": "Polygon", "coordinates": [[
            [10.4, 56.1], [10.9, 56.1], [10.9, 56.35],
            [10.4, 56.35], [10.4, 56.1],
        ]]}
        with self.assertRaises(SystemExit):
            self._run(
                _index(), _trails(), _boundary(), include_preview=True,
                exact_geometry=exact,
                nonhiking={_AREA_ID: {"mols-trail": "drop"}})
        with self.assertRaises(SystemExit):
            self._run(
                _index(), _trails(), _boundary(), include_preview=True,
                exact_geometry=exact,
                prior_row={"parking": [{"id": "sealed-only"}]})

    def test_generic_finalizer_preserves_prior_parking_semantics(self):
        parking = [{"id": "existing"}]
        result = self._run(
            _index(), _trails(), _boundary(), include_preview=True,
            prior_row={"parking": parking})
        self.assertEqual(result["preview"]["areas"][0]["parking"], parking)
        self.assertTrue(result["report"]["preview"]["finalization"][
            "prior_parking_present"])

    def _assert_preview_path_rejected(self, root, target, *, index=None,
                                      trails=None, hiking=None, output=None,
                                      report=None, preview_root=None):
        preview_root = Path(preview_root or root)
        work = root.parent / f".{root.name}-preview-guard-work"
        argv = [
            "--trails", str(trails or (work / "trails.geojson")),
            "--hiking", str(hiking or (work / "hiking.osm.pbf")),
            "--index", str(index or (work / "index.json")),
            "--out-dir", str(output or (work / "output")),
            "--dry-run",
            "--preview-root", str(preview_root),
            "--preview-json", str(target),
        ]
        if report is not None:
            argv.extend(["--report-json", str(report)])
        with self.assertRaises(SystemExit) as raised:
            publish.main(argv)
        self.assertEqual(raised.exception.code, 2)

    def _preview_path(self, root, target, *, index, trails, hiking,
                      output, report=None):
        args = SimpleNamespace(
            preview_json=str(target), preview_root=str(root), dry_run=True,
            index=str(index), trails=str(trails), hiking=str(hiking),
            out_dir=str(output), report_json=(str(report) if report else None),
        )
        return publish._preview_output_path(args, argparse.ArgumentParser())

    def test_preview_refuses_missing_root_absolute_escape_and_dotdot_alias(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            reports = root / "reports"
            reports.mkdir()
            with self.assertRaises(SystemExit) as raised:
                publish.main([
                    "--trails", "unused.geojson",
                    "--hiking", "unused.osm.pbf",
                    "--out-dir", str(root / "output"),
                    "--dry-run",
                    "--preview-json", str(reports / "preview.json"),
                ])
            self.assertEqual(raised.exception.code, 2)
            self._assert_preview_path_rejected(
                root, root.parent / "escape.json")
            self._assert_preview_path_rejected(
                root, f"{reports}/../escape.json")
            self._assert_preview_path_rejected(root, root)

    def test_preview_refuses_symlink_components_and_target(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            actual = root / "actual"
            actual.mkdir()
            target_file = actual / "target.json"
            target_file.write_text("preserve", encoding="utf-8")
            target_link = root / "target-link.json"
            target_link.symlink_to(target_file)
            directory_link = root / "directory-link"
            directory_link.symlink_to(actual, target_is_directory=True)
            self._assert_preview_path_rejected(root, target_link)
            self._assert_preview_path_rejected(
                root, directory_link / "preview.json")
            self.assertEqual(target_file.read_text(), "preserve")

    def test_preview_refuses_every_input_and_output_collision(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "output"
            output.mkdir()
            paths = {
                "index": root / "index.json",
                "trails": root / "trails.geojson",
                "hiking": root / "hiking.osm.pbf",
                "report": root / "publish.json",
            }
            for collision, target in paths.items():
                with self.subTest(collision=collision):
                    kwargs = {collision: target}
                    self._assert_preview_path_rejected(
                        root, target, output=output, **kwargs)
            self._assert_preview_path_rejected(
                root, output, output=output)
            self._assert_preview_path_rejected(
                root, output / "preview.json", output=output)
            trails = root / "mols.trails.geojson"
            self._assert_preview_path_rejected(
                root, root / "mols.dropped-routes.geojson",
                trails=trails, output=output)

    def test_preview_accepts_only_distinct_target_under_runner_temp(self):
        with TemporaryDirectory() as tmp:
            runner = Path(tmp) / "runner-temp"
            qa = runner / "qa"
            work = runner / "work"
            reports = qa / "reports"
            runner.mkdir()
            reports.mkdir(parents=True)
            work.mkdir()
            target = reports / "preview.json"
            with patch.dict(os.environ, {"RUNNER_TEMP": str(runner)}):
                resolved = self._preview_path(
                    qa, target, index=work / "index.json",
                    trails=work / "trails.geojson",
                    hiking=work / "hiking.osm.pbf",
                    output=work / "output", report=reports / "publish.json")
            self.assertEqual(resolved, target.resolve())

    def test_preview_root_rejects_trusted_root_repo_home_traversal_and_symlink(self):
        with TemporaryDirectory() as tmp:
            runner = Path(tmp) / "runner-temp"
            qa = runner / "qa"
            work = runner / "work"
            reports = qa / "reports"
            runner.mkdir()
            reports.mkdir(parents=True)
            work.mkdir()
            root_link = runner / "qa-link"
            root_link.symlink_to(qa, target_is_directory=True)
            roots = (
                runner,
                publish._REPO_ROOT,
                Path.home(),
                str(qa / ".." / "qa"),
                root_link,
            )
            with patch.dict(os.environ, {"RUNNER_TEMP": str(runner)}):
                for root in roots:
                    with self.subTest(root=root), self.assertRaises(SystemExit):
                        self._preview_path(
                            root, Path(root) / "preview.json",
                            index=work / "index.json",
                            trails=work / "trails.geojson",
                            hiking=work / "hiking.osm.pbf",
                            output=work / "output")

    def test_preview_rejects_input_inode_and_output_directory_aliases(self):
        with TemporaryDirectory() as tmp:
            runner = Path(tmp) / "runner-temp"
            qa = runner / "qa"
            work = runner / "work"
            reports = qa / "reports"
            runner.mkdir()
            reports.mkdir(parents=True)
            work.mkdir()
            target = reports / "preview.json"
            target.write_text("preserve", encoding="utf-8")
            input_link = work / "index-link.json"
            input_link.symlink_to(target)
            hard_link = work / "hiking-hardlink.pbf"
            os.link(target, hard_link)
            output_link = work / "output-link"
            output_link.symlink_to(reports, target_is_directory=True)
            common = {
                "trails": work / "trails.geojson",
                "hiking": work / "hiking.osm.pbf",
                "output": work / "output",
            }
            with patch.dict(os.environ, {"RUNNER_TEMP": str(runner)}):
                with self.assertRaises(SystemExit):
                    self._preview_path(
                        qa, target, index=input_link, **common)
                with self.assertRaises(SystemExit):
                    self._preview_path(
                        qa, target, index=work / "index.json",
                        trails=common["trails"], hiking=hard_link,
                        output=common["output"])
                with self.assertRaises(SystemExit):
                    self._preview_path(
                        qa, target, index=work / "index.json",
                        trails=common["trails"], hiking=common["hiking"],
                        output=output_link)
            self.assertEqual(target.read_text(), "preserve")

    def test_preview_refuses_canonical_public_and_ios_resource_targets(self):
        for target in (
                publish._CANONICAL_PUBLIC_ROOT / "preview.json",
                publish._CANONICAL_IOS_RESOURCES / "preview.json"):
            with self.subTest(target=target):
                self._assert_preview_path_rejected(
                    publish._REPO_ROOT, target,
                    output=publish._REPO_ROOT / "runner-output")
                self.assertFalse(target.exists())

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
