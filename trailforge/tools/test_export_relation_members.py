"""Offline controls for three-scope relation authority export."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
from contextlib import redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

_HERE = Path(__file__).resolve().parent
_SPEC = importlib.util.spec_from_file_location(
    "export_relation_members_module", _HERE / "export_relation_members.py")
exporter = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(exporter)


def _tags(values):
    return [SimpleNamespace(k=key, v=value) for key, value in values]


def _member(member_type, ref, role=""):
    return SimpleNamespace(type=member_type, ref=ref, role=role)


def _relation(relation_id, tags, members):
    return SimpleNamespace(id=relation_id, tags=_tags(tags), members=members)


def _way(way_id, tags, nodes):
    return SimpleNamespace(
        id=way_id, tags=_tags(tags),
        nodes=[SimpleNamespace(ref=node_id) for node_id in nodes])


def _node(node_id, lon, lat):
    return SimpleNamespace(
        id=node_id, location=SimpleNamespace(lon=lon, lat=lat))


class _FakeOsmium:
    relations = []
    ways = []
    nodes = []

    class SimpleHandler:
        def apply_file(self, _path):
            if hasattr(self, "relation"):
                for relation in _FakeOsmium.relations:
                    self.relation(relation)
            if hasattr(self, "way"):
                for way in _FakeOsmium.ways:
                    self.way(way)
            if hasattr(self, "node"):
                for node in _FakeOsmium.nodes:
                    self.node(node)


def _records(reverse=False):
    relations = [
        _relation(20, [("name", "Route Twenty"), ("route", " Hiking "),
                       ("type", " Route ")], [
            _member("w", 3, ""),
            _member("r", 21, "connection"),
            _member("n", 100, "guidepost"),
        ]),
        # Deliberately not type=route: recursive hierarchy evidence must retain it.
        _relation(21, [("name", "Nested Segment")], [
            _member("way", 4, "alternative"),
            _member("r", 404, ""),
        ]),
        _relation(22, [("type", "route"), ("route", "foot")], [
            _member("way", 9, "main"),
        ]),
        _relation(23, [("type", "route"), ("route", "bicycle")], [
            _member("way", 99, ""),
        ]),
    ]
    ways = [
        _way(3, [("foot", "yes"), ("service", "alley"),
                 ("highway", "service")], [30, 31, 32]),
        _way(4, [("highway", "path")], [32, 33]),
    ]
    nodes = [
        _node(30, 10.30, 56.30),
        _node(31, 10.31, 56.31),
        _node(32, 10.32, 56.32),
        _node(33, 10.33, 56.33),
    ]
    if reverse:
        relations.reverse()
        ways.reverse()
        nodes.reverse()
        for value in relations + ways:
            value.tags.reverse()
    return relations, ways, nodes


class RelationMemberExporter(unittest.TestCase):
    def _run(self, *, reverse=False, scope="aoi", roots=None):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "mols.osm.pbf"
            output = root / "relation-members.json"
            source.write_bytes(b"synthetic-local-pbf")
            _FakeOsmium.relations, _FakeOsmium.ways, _FakeOsmium.nodes = \
                _records(reverse)
            argv = [
                "--in", str(source), "--scope", scope,
                "--out", str(output),
            ]
            if roots is not None:
                roots_path = root / "roots.json"
                roots_path.write_text(json.dumps({
                    "schema_version": 2,
                    "root_relation_ids": roots,
                }), encoding="utf-8")
                argv.extend(["--roots-from", str(roots_path)])
            rc = exporter.main(argv, osmium_module=_FakeOsmium)
            return rc, output.read_bytes() if output.exists() else None, \
                json.loads(output.read_text()) if output.exists() else None, \
                list(root.glob(".*.tmp"))

    def test_aoi_discovers_roots_and_preserves_recursive_order_and_nodes(self):
        rc, raw, graph, temporary = self._run()
        self.assertEqual(rc, 0)
        self.assertEqual(raw, exporter.canonical_json_bytes(graph))
        self.assertEqual(temporary, [])
        self.assertEqual(graph["schema_version"], 2)
        self.assertEqual(graph["scope"], "aoi")
        self.assertEqual(graph["source"]["artifact"],
                         exporter.SOURCE_ARTIFACTS["aoi"])
        self.assertEqual(graph["root_relation_ids"], [20, 22])
        self.assertEqual(graph["relation_count"], 3)
        self.assertEqual(graph["missing_relation_ids"], [404])
        relation = graph["relations"][0]
        self.assertEqual(relation["relation_id"], 20)
        self.assertEqual(relation["members"], [
            {"sequence": 0, "type": "way", "ref": 3, "role": ""},
            {"sequence": 1, "type": "relation", "ref": 21,
             "role": "connection"},
            {"sequence": 2, "type": "node", "ref": 100,
             "role": "guidepost"},
        ])
        self.assertEqual(relation["direct_ways"], [{
            "way_id": 3,
            "status": "present",
            "tags": {"foot": "yes", "highway": "service",
                     "service": "alley"},
            "node_ids": [30, 31, 32],
            "coordinates": [
                [10.3, 56.3], [10.31, 56.31], [10.32, 56.32],
            ],
            "missing_node_ids": [],
        }])
        self.assertEqual(graph["missing_direct_way_ids"], [9])
        self.assertEqual(graph["incomplete_direct_way_ids"], [])

    def test_full_scope_uses_only_aoi_selected_roots(self):
        rc, _, graph, _ = self._run(
            scope="raw-denmark", roots=[20])
        self.assertEqual(rc, 0)
        self.assertEqual(graph["root_relation_ids"], [20])
        self.assertEqual([row["relation_id"] for row in graph["relations"]],
                         [20, 21])
        self.assertNotIn(22, [row["relation_id"] for row in graph["relations"]])

    def test_incomplete_way_keeps_aligned_node_evidence(self):
        relations, ways, nodes = _records()
        del nodes[-1]
        _FakeOsmium.relations, _FakeOsmium.ways, _FakeOsmium.nodes = \
            relations, ways, nodes
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.pbf"
            source.write_bytes(b"pbf")
            output = root / "out.json"
            graph = exporter.export_relation_members(
                source, output, scope="aoi", osmium_module=_FakeOsmium)
        nested = graph["relations"][1]["direct_ways"][0]
        self.assertEqual(nested["status"], "incomplete")
        self.assertEqual(nested["node_ids"], [32, 33])
        self.assertEqual(nested["coordinates"], [[10.32, 56.32], None])
        self.assertEqual(nested["missing_node_ids"], [33])
        self.assertEqual(graph["incomplete_direct_way_ids"], [4])

    def test_output_is_stable_across_handler_and_tag_mapping_order(self):
        first = self._run(reverse=False)[1]
        second = self._run(reverse=True)[1]
        self.assertEqual(first, second)

    def test_creator_emits_exact_durable_ledger_identity(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "mols.osm.pbf"
            output = root / "relation-members.json"
            github_output = root / "github-output.txt"
            source.write_bytes(b"synthetic-local-pbf")
            _FakeOsmium.relations, _FakeOsmium.ways, _FakeOsmium.nodes = \
                _records()

            rc = exporter.main([
                "--in", str(source), "--scope", "aoi",
                "--out", str(output),
                "--github-output", str(github_output),
            ], osmium_module=_FakeOsmium)

            self.assertEqual(rc, 0)
            values = dict(line.split("=", 1) for line in
                          github_output.read_text().splitlines())
            raw = output.read_bytes()
            self.assertEqual(values, {
                "relation_ledger_sha256": hashlib.sha256(raw).hexdigest(),
                "relation_ledger_bytes": str(len(raw)),
            })

    def test_non_aoi_scope_requires_roots_ledger(self):
        rc, raw, graph, _ = self._run(scope="raw-denmark")
        self.assertEqual(rc, 2)
        self.assertIsNone(raw)
        self.assertIsNone(graph)

    def test_missing_pyosmium_fails_clearly_without_output(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "mols.osm.pbf"
            output = root / "relation-members.json"
            source.write_bytes(b"synthetic-local-pbf")
            stderr = io.StringIO()
            with (patch.object(
                    exporter, "_load_osmium",
                    side_effect=RuntimeError(
                        "pyosmium/osmium is required to export relation membership evidence")),
                  redirect_stderr(stderr)):
                rc = exporter.main([
                    "--in", str(source), "--scope", "aoi",
                    "--out", str(output),
                ])
            self.assertEqual(rc, 2)
            self.assertFalse(output.exists())
            self.assertIn("pyosmium/osmium is required", stderr.getvalue())

    def test_scaled_stream_retains_only_selected_relation_way_node_records(self):
        irrelevant_count = 5000
        _FakeOsmium.relations = [
            _relation(index + 1000, [("type", "route"), ("route", "bicycle")], [
                _member("way", index + 10000, "")])
            for index in range(irrelevant_count)
        ] + [
            _relation(20, [("type", "route"), ("route", "hiking")], [
                _member("relation", 21, "")]),
            _relation(21, [("name", "Child")], [
                _member("way", 3, "")]),
        ]
        _FakeOsmium.ways = [
            _way(index + 10000, [("highway", "service")], [
                index + 20000, index + 20001])
            for index in range(irrelevant_count)
        ] + [_way(3, [("highway", "path")], [30, 31])]
        _FakeOsmium.nodes = [
            _node(index + 20000, 0.0, 0.0)
            for index in range(irrelevant_count + 1)
        ] + [_node(30, 10.3, 56.3), _node(31, 10.4, 56.4)]
        with TemporaryDirectory() as tmp:
            source = Path(tmp) / "slice.osm.pbf"
            source.write_bytes(b"compact")
            relations, ways, roots, missing, metrics = \
                exporter.read_pbf_records(
                    source, root_relation_ids=[20],
                    osmium_module=_FakeOsmium)
        self.assertEqual(roots, [20])
        self.assertEqual(missing, [])
        self.assertEqual([row["relation_id"] for row in relations], [20, 21])
        self.assertEqual(set(ways), {3})
        self.assertEqual(metrics["relation_records_retained"], 2)
        self.assertEqual(metrics["way_records_retained"], 1)
        self.assertEqual(metrics["node_records_retained"], 2)
        self.assertGreaterEqual(
            max(record["seen"] for record in metrics["passes"]),
            irrelevant_count)
        self.assertLess(metrics["relation_records_retained"],
                        irrelevant_count // 100)

    def test_source_has_no_assembler_relation_resolution_dependency(self):
        source = (_HERE / "export_relation_members.py").read_text(encoding="utf-8")
        self.assertNotIn("import model", source)
        self.assertNotIn("resolve_route", source)
        self.assertNotIn("trailforge.assemble", source)


if __name__ == "__main__":
    unittest.main()
