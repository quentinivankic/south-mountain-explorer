"""Offline controls for canonical AOI source-way topology export."""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

_HERE = Path(__file__).resolve().parent
_SPEC = importlib.util.spec_from_file_location(
    "export_way_topology_module", _HERE / "export_way_topology.py")
exporter = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(exporter)


def _tags(**values):
    return [SimpleNamespace(k=key, v=value) for key, value in values.items()]


class _FakeOsmium:
    class SimpleHandler:
        def apply_file(self, _path):
            if hasattr(self, "way"):
                self.way(SimpleNamespace(
                    id=10, tags=_tags(highway="pedestrian", name="Implicit"),
                    nodes=[SimpleNamespace(ref=value)
                           for value in (1, 2, 3, 1)]))
                self.way(SimpleNamespace(
                    id=11, tags=_tags(highway="pedestrian", area="no",
                                      name="Linear"),
                    nodes=[SimpleNamespace(ref=value)
                           for value in (1, 2, 3, 1)]))
                self.way(SimpleNamespace(
                    id=12, tags=_tags(leisure="park"),
                    nodes=[SimpleNamespace(ref=1), SimpleNamespace(ref=2)]))
                self.way(SimpleNamespace(
                    id=13, tags=_tags(highway="pedestrian", area="yes",
                                      name="Explicit"),
                    nodes=[SimpleNamespace(ref=value)
                           for value in (1, 2, 3, 1)]))
            if hasattr(self, "relation"):
                self.relation(SimpleNamespace(
                    id=700,
                    tags=_tags(type="route", route="hiking", name="Route"),
                    members=[SimpleNamespace(
                        type="w", ref=10, role="main")]))
                self.relation(SimpleNamespace(
                    id=7046785,
                    tags=_tags(type="boundary", boundary="national_park",
                               name="Nationalpark Mols Bjerge"),
                    members=[SimpleNamespace(
                        type="w", ref=12, role="outer")]))
            if hasattr(self, "node"):
                nodes = {
                    1: ((10.0, 56.0), {}),
                    2: ((10.1, 56.0), {}),
                    3: ((10.1, 56.1), {}),
                    90: ((10.2, 56.2), {
                        "natural": "peak", "name": "  Mo\u0308lle   Top  ",
                        "ele": "137",
                    }),
                    91: ((10.3, 56.2), {
                        "amenity": "shelter", "shelter_type": "basic_hut",
                    }),
                    92: ((10.4, 56.2), {
                        "waterway": "waterfall", "name": "Legacy Falls",
                    }),
                    93: ((10.5, 56.2), {
                        "tourism": "attraction", "name": "   ",
                        "name:da": " Udsigten ",
                    }),
                }
                for node_id, (point, tags) in nodes.items():
                    self.node(SimpleNamespace(
                        id=node_id, tags=_tags(**tags),
                        location=SimpleNamespace(lon=point[0], lat=point[1])))


class WayTopologyExport(unittest.TestCase):
    def test_closedness_comes_from_node_ids_and_area_tag_is_preserved(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "mols.osm.pbf"
            output = root / "mols.way-topology.json"
            source.write_bytes(b"synthetic-aoi")
            document = exporter.export_way_topology(
                source, output,
                source_artifact="data/aoi/mols-bjerge.osm.pbf",
                osmium_module=_FakeOsmium)

            self.assertEqual(output.read_bytes(),
                             exporter.canonical_json_bytes(document))
            self.assertEqual(document["schema_version"], 3)
            self.assertEqual(document["way_count"], 4)
            self.assertEqual(document["relation_count"], 2)
            self.assertEqual(document["destination_poi_count"], 3)
            self.assertEqual(document["incomplete_way_ids"], [])
            self.assertEqual(document["destination_pois"], [{
                "node_id": 90,
                "tags": {"ele": "137", "name": "  Mo\u0308lle   Top  ",
                         "natural": "peak"},
                "name": "Mölle Top",
                "coordinate": [10.2, 56.2],
                "eligibility_class": "natural=peak",
            }, {
                "node_id": 91,
                "tags": {"amenity": "shelter", "shelter_type": "basic_hut"},
                "name": None,
                "coordinate": [10.3, 56.2],
                "eligibility_class": "amenity=shelter",
            }, {
                "node_id": 93,
                "tags": {"name": "   ", "name:da": " Udsigten ",
                         "tourism": "attraction"},
                "name": "Udsigten",
                "coordinate": [10.5, 56.2],
                "eligibility_class": "tourism=attraction",
            }])
            route, boundary = document["relations"]
            self.assertEqual(route, {
                "relation_id": 700,
                "tags": {"name": "Route", "route": "hiking",
                         "type": "route"},
                "members": [{"sequence": 0, "type": "way", "ref": 10,
                             "role": "main"}],
            })
            self.assertEqual(boundary["relation_id"], 7046785)
            self.assertEqual(boundary["tags"]["boundary"], "national_park")
            self.assertEqual(boundary["members"], [{
                "sequence": 0, "type": "way", "ref": 12, "role": "outer",
            }])
            implicit, linear, area, explicit = document["ways"]
            self.assertEqual(implicit["node_ids"], [1, 2, 3, 1])
            self.assertNotIn("area", implicit["tags"])
            self.assertEqual(linear["tags"]["area"], "no")
            self.assertEqual(area["tags"], {"leisure": "park"})
            self.assertEqual(explicit["tags"]["area"], "yes")
            self.assertEqual(implicit["coordinates"][0],
                             implicit["coordinates"][-1])

    def test_destination_class_policy_is_exact_and_excludes_legacy_only_nodes(self):
        expected = {
            ("natural", "peak"): "natural=peak",
            ("natural", "arch"): "natural=arch",
            ("natural", "saddle"): "natural=saddle",
            ("natural", "cliff"): "natural=cliff",
            ("natural", "rock"): "natural=rock",
            ("natural", "stone"): "natural=stone",
            ("tourism", "viewpoint"): "tourism=viewpoint",
            ("tourism", "attraction"): "tourism=attraction",
            ("historic", "archaeological_site"):
                "historic=archaeological_site",
            ("historic", "castle"): "historic=castle",
            ("historic", "ruins"): "historic=ruins",
            ("amenity", "shelter"): "amenity=shelter",
            ("highway", "trailhead"): "highway=trailhead",
        }
        self.assertEqual(
            {signature: exporter._destination_eligibility_class(
                {signature[0]: signature[1]}) for signature in expected},
            expected)
        for tags in (
                {"natural": "volcano"}, {"natural": "hot_spring"},
                {"waterway": "waterfall"}, {"tourism": "alpine_hut"},
                {"mountain_pass": "yes"}, {"amenity": "parking"}):
            with self.subTest(tags=tags):
                self.assertIsNone(exporter._destination_eligibility_class(tags))

    def test_creator_emits_exact_durable_topology_identity(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "mols.osm.pbf"
            output = root / "mols.way-topology.json"
            github_output = root / "github-output.txt"
            source.write_bytes(b"synthetic-aoi")

            rc = exporter.main([
                "--in", str(source), "--out", str(output),
                "--source-artifact", "data/aoi/mols-bjerge.osm.pbf",
                "--github-output", str(github_output),
            ], osmium_module=_FakeOsmium)

            self.assertEqual(rc, 0)
            raw = output.read_bytes()
            values = dict(line.split("=", 1) for line in
                          github_output.read_text().splitlines())
            self.assertEqual(values, {
                "way_topology_sha256": hashlib.sha256(raw).hexdigest(),
                "way_topology_bytes": str(len(raw)),
            })

    def test_missing_dependency_fails_without_output(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "mols.osm.pbf"
            output = root / "out.json"
            source.write_bytes(b"pbf")
            with patch.object(
                    exporter, "_load_osmium",
                    side_effect=RuntimeError("pyosmium unavailable")):
                self.assertEqual(exporter.main([
                    "--in", str(source), "--out", str(output),
                    "--source-artifact", "data/aoi/mols-bjerge.osm.pbf",
                ]), 2)
            self.assertFalse(output.exists())

    def test_invalid_source_label_fails_before_read(self):
        with TemporaryDirectory() as tmp:
            source = Path(tmp) / "mols.osm.pbf"
            source.write_bytes(b"pbf")
            with self.assertRaisesRegex(ValueError, "canonical relative"):
                exporter.export_way_topology(
                    source, Path(tmp) / "out.json", source_artifact="../fake",
                    osmium_module=_FakeOsmium)


if __name__ == "__main__":
    unittest.main()
