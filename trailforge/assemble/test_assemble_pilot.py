"""Offline exact-boundary and structured-report tests for assemble.py."""
import copy
import hashlib
import importlib.util
import io
import json
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from types import SimpleNamespace
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
    def __init__(self, name="Mols Trail", *, source="name-stitch", kind="trail",
                 area="Nationalpark Mols Bjerge", member_ways=None,
                 coordinates=None, relation_ids=None):
        member_ways = list(member_ways or [10])
        coordinates = coordinates or [[[10.5, 56.15], [10.6, 56.2]]]
        self.member_ways = member_ways
        self.geometry_ways = list(member_ways)
        self.relation_ids = list(relation_ids or [])
        self.direct_relation_ways = {
            relation_id: list(member_ways) for relation_id in self.relation_ids
        }
        self._feature = {
            "type": "Feature",
            "properties": {
                "name": name,
                "kind": kind,
                "area": area,
                "source": source,
                "length_mi": 1.0,
                "member_ways": member_ways,
                "destinations": [],
                "welds": [],
                "network": "",
                "operator": "",
                "sac_scale": "",
                "trail_visibility": "",
                "removed_reason": None,
                "removed_category": None,
                "ckey": "w" + "-".join(str(way_id) for way_id in member_ways),
            },
            "geometry": {
                "type": "MultiLineString",
                "coordinates": coordinates,
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


class PbfRelationMemberLoading(unittest.TestCase):
    def test_direct_relation_way_without_highway_is_still_loaded_for_audit(self):
        relation = SimpleNamespace(
            id=700,
            tags=[SimpleNamespace(k="type", v="route"),
                  SimpleNamespace(k="route", v="hiking")],
            members=[SimpleNamespace(type="w", ref=10, role="")],
        )
        ways = [
            SimpleNamespace(
                id=10,
                tags=[SimpleNamespace(k="surface", v="ground")],
                nodes=[SimpleNamespace(ref=1), SimpleNamespace(ref=2)]),
            SimpleNamespace(
                id=20,
                tags=[SimpleNamespace(k="surface", v="ground")],
                nodes=[SimpleNamespace(ref=3), SimpleNamespace(ref=4)]),
        ]
        nodes = [
            SimpleNamespace(id=node_id, tags=[],
                            location=SimpleNamespace(lon=float(node_id), lat=0.0))
            for node_id in range(1, 5)
        ]

        class FakeHandler:
            def apply_file(self, _path):
                if hasattr(self, "relation"):
                    self.relation(relation)
                if hasattr(self, "way"):
                    for way in ways:
                        self.way(way)
                if hasattr(self, "node"):
                    for node in nodes:
                        self.node(node)

        with patch.dict(sys.modules, {
                "osmium": SimpleNamespace(SimpleHandler=FakeHandler)}):
            loaded_nodes, loaded_ways, loaded_relations, _ = \
                assemble.read_pbf("synthetic.osm.pbf")

        self.assertEqual(set(loaded_ways), {10})
        self.assertEqual(loaded_ways[10]["tags"], {"surface": "ground"})
        self.assertEqual(loaded_nodes, {1: (1.0, 0.0), 2: (2.0, 0.0)})
        self.assertEqual(loaded_relations[700]["members"], [("w", 10, "")])


@unittest.skipUnless(HAVE_SHAPELY, "shapely not installed")
class ExactPilotAssembly(unittest.TestCase):
    def _run(self, boundaries, *, nodes=None, ways=None, trails=None, extra_args=()):
        nodes = nodes if nodes is not None else {
            1: (10.5, 56.15),
            2: (10.6, 56.2),
        }
        ways = ways if ways is not None else {
            10: {"tags": {"highway": "path", "name": "Mols Trail"},
                 "nodes": [1, 2]},
        }
        trails = trails if trails is not None else [_Trail()]
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_pbf = root / "mols.osm.pbf"
            input_pbf.write_bytes(b"synthetic-runner-local-aoi-pbf")
            output = root / "mols-bjerge.trails.geojson"
            report = root / "assembly.json"
            argv = [
                "--in", str(input_pbf),
                "--out", str(output),
                "--only-area", "Nationalpark Mols Bjerge",
                "--require-exact-area",
                "--expected-area-relation-id", "7046785",
                "--report-json", str(report),
                *extra_args,
            ]
            stderr = io.StringIO()
            with (
                patch.object(assemble, "read_pbf", return_value=(nodes, ways, {}, [])),
                patch.object(areas, "assemble_areas", return_value=boundaries),
                patch.object(areas, "merge_areas", return_value=[]),
                patch.object(assemble.model, "assemble", return_value=trails) as build,
                redirect_stderr(stderr),
            ):
                rc = assemble.main(argv)
            payload = json.loads(report.read_text())
            output_payload = json.loads(output.read_text()) if output.exists() else None
            files = sorted(path.name for path in root.iterdir()
                           if path != input_pbf)
            return rc, payload, output_payload, files, build.call_count, stderr.getvalue()

    def _run_ingest_reconciliation(self, coordinates):
        nodes = {index: tuple(point)
                 for index, point in enumerate(coordinates, start=1)}
        ways = {
            10: {"tags": {"highway": "path", "name": "Signed Route"},
                 "nodes": list(nodes)},
        }
        trail = _Trail(
            "Signed Route", source="relation", kind="route", area=None,
            member_ways=[10], coordinates=[[list(point) for point in coordinates]],
            relation_ids=[700],
        )
        diagnostic = {
            "type": "Feature",
            "properties": {
                "name": "Source Diagnostic",
                "way_id": 10,
                "ckey": "w10",
                "highway": "track",
                "removed_category": "road-track-tag",
                "removed_reason": "Original standalone drop reason.",
                "retained_in_route": False,
                "relation_ids": [],
            },
            "geometry": {"type": "LineString", "coordinates": coordinates},
        }

        def build(_nodes, _ways, _relations, _pois, **kwargs):
            kwargs["collect_ingest_dropped"].append(copy.deepcopy(diagnostic))
            return [trail]

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_pbf = root / "mols.osm.pbf"
            input_pbf.write_bytes(b"synthetic-runner-local-aoi-pbf")
            output = root / "mols-bjerge.trails.geojson"
            report = root / "assembly.json"
            with (
                patch.object(assemble, "read_pbf",
                             return_value=(nodes, ways, {}, [])),
                patch.object(areas, "assemble_areas", return_value=[_boundary()]),
                patch.object(assemble.model, "assemble", side_effect=build),
            ):
                rc = assemble.main([
                    "--in", str(input_pbf),
                    "--out", str(output),
                    "--only-area", "Nationalpark Mols Bjerge",
                    "--require-exact-area",
                    "--expected-area-relation-id", "7046785",
                    "--report-json", str(report),
                    "--region", "dk",
                ])
            ingest = json.loads(
                (root / "mols-bjerge.ingest-dropped.geojson").read_text())
            trails = json.loads(output.read_text())
            return rc, trails, ingest

    def test_ingest_relation_fully_outside_remains_ordinary_drop(self):
        rc, trails, ingest = self._run_ingest_reconciliation(
            [[11.0, 56.15], [11.1, 56.15]])

        self.assertEqual(rc, 0)
        self.assertEqual(trails["features"], [])
        properties = ingest["features"][0]["properties"]
        self.assertFalse(properties["retained_in_route"])
        self.assertEqual(properties["relation_ids"], [])
        self.assertEqual(properties["removed_category"], "road-track-tag")
        self.assertEqual(properties["removed_reason"],
                         "Original standalone drop reason.")
        self.assertNotIn("standalone_drop_category", properties)

    def test_ingest_partially_clipped_relation_uses_surviving_binding(self):
        rc, trails, ingest = self._run_ingest_reconciliation(
            [[10.5, 56.15], [11.0, 56.15]])

        self.assertEqual(rc, 0)
        self.assertEqual(len(trails["features"]), 1)
        self.assertTrue(trails["features"][0]["properties"]["clipped"])
        properties = ingest["features"][0]["properties"]
        self.assertTrue(properties["retained_in_route"])
        self.assertEqual(properties["relation_ids"], [700])
        self.assertEqual(properties["standalone_drop_category"], "road-track-tag")
        self.assertEqual(properties["standalone_drop_reason"],
                         "Original standalone drop reason.")

    def test_ingest_inside_relation_uses_final_binding(self):
        rc, trails, ingest = self._run_ingest_reconciliation(
            [[10.5, 56.15], [10.6, 56.15]])

        self.assertEqual(rc, 0)
        self.assertEqual(len(trails["features"]), 1)
        properties = ingest["features"][0]["properties"]
        self.assertTrue(properties["retained_in_route"])
        self.assertEqual(properties["relation_ids"], [700])

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
        source_bytes = b"synthetic-runner-local-aoi-pbf"
        self.assertEqual(report["input_pbf"], {
            "sha256": hashlib.sha256(source_bytes).hexdigest(),
            "bytes": len(source_bytes),
        })
        self.assertTrue(report["clip_applied"])
        self.assertEqual(report["pre_clip_trail_count"], 1)
        self.assertEqual(report["post_clip_trail_count"], 1)
        self.assertEqual(report["coverage"]["raw_trailish_ways"], 1)
        self.assertEqual(report["coverage"]["named_trailish_ways"], 1)
        self.assertEqual(len(output["features"]), 1)
        self.assertIn("mols-bjerge.areas.geojson", files)
        self.assertIn("mols-bjerge.curation-diff.json", files)

    def test_exact_clip_assigns_area_to_run33_producer_patterns(self):
        nodes = {
            1: (10.50, 56.15), 2: (10.55, 56.16),
            3: (10.56, 56.17), 4: (10.60, 56.18),
            5: (10.61, 56.19), 6: (10.65, 56.20),
            7: (10.80, 56.21), 8: (11.00, 56.22),
        }
        ways = {
            way_id: {"tags": {"highway": "path", "name": name},
                     "nodes": node_ids}
            for way_id, name, node_ids in (
                (10, "Molsruten", [1, 2]),
                (11, "Midtervej", [3, 4]),
                (12, "Skolestien", [5, 6]),
                (13, "Boundary Trail", [7, 8]),
            )
        }
        trails = [
            _Trail("Molsruten", source="relation", kind="route", area=None,
                   member_ways=[10], coordinates=[[list(nodes[1]), list(nodes[2])]],
                   relation_ids=[100]),
            _Trail("Midtervej", source="relation", kind="trail", area=None,
                   member_ways=[11], coordinates=[[list(nodes[3]), list(nodes[4])]],
                   relation_ids=[101]),
            _Trail("Skolestien", area=None, member_ways=[12],
                   coordinates=[[list(nodes[5]), list(nodes[6])]]),
            _Trail("Boundary Trail", area=None, member_ways=[13],
                   coordinates=[[list(nodes[7]), list(nodes[8])]]),
        ]

        rc, report, output, _, _, _ = self._run(
            [_boundary()], nodes=nodes, ways=ways, trails=trails,
            extra_args=("--per-area-merge", "--region", "dk"))

        self.assertEqual(rc, 0)
        self.assertEqual(report["post_clip_trail_count"], 4)
        self.assertEqual(len(output["features"]), 4)
        self.assertEqual(
            {feature["properties"]["area"] for feature in output["features"]},
            {"Nationalpark Mols Bjerge"},
        )
        clipped = next(feature for feature in output["features"]
                       if feature["properties"]["name"] == "Boundary Trail")
        self.assertTrue(clipped["properties"]["clipped"])

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
