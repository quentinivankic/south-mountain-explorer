"""Offline controls for independent AOI relation-member evidence export."""
from __future__ import annotations

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
    return SimpleNamespace(
        id=relation_id, tags=_tags(tags), members=members)


def _way(way_id, tags):
    return SimpleNamespace(id=way_id, tags=_tags(tags))


class _FakeOsmium:
    relations = []
    ways = []

    class SimpleHandler:
        def apply_file(self, _path):
            if hasattr(self, "relation"):
                for relation in _FakeOsmium.relations:
                    self.relation(relation)
            if hasattr(self, "way"):
                for way in _FakeOsmium.ways:
                    self.way(way)


def _records(reverse=False):
    relations = [
        _relation(20, [("name", "Route Twenty"), ("route", " Hiking "),
                       ("type", " Route ")], [
            _member("w", 3, ""),
            _member("r", 21, "connection"),
            _member("n", 100, "guidepost"),
        ]),
        _relation(21, [("type", "route"), ("route", "bicycle")], [
            _member("way", 4, "alternative"),
        ]),
        _relation(22, [("type", "route"), ("route", "foot")], [
            _member("way", 9, "main"),
        ]),
        _relation(99, [("type", "boundary"), ("name", "Not a route")], []),
    ]
    ways = [
        _way(3, [("foot", "yes"), ("service", "alley"),
                 ("highway", "service")]),
        _way(4, [("highway", "path")]),
    ]
    if reverse:
        relations.reverse()
        ways.reverse()
        for value in relations + ways:
            value.tags.reverse()
    return relations, ways


class RelationMemberExporter(unittest.TestCase):
    def _run(self, *, reverse=False):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "mols.osm.pbf"
            output = root / "relation-members.json"
            source.write_bytes(b"synthetic-local-pbf")
            _FakeOsmium.relations, _FakeOsmium.ways = _records(reverse)
            rc = exporter.main([
                "--in", str(source), "--out", str(output),
            ], osmium_module=_FakeOsmium)
            return rc, output.read_bytes(), json.loads(output.read_text()), list(
                root.glob(".*.tmp"))

    def test_exports_all_route_relations_and_ordered_direct_members(self):
        rc, raw, graph, temporary = self._run()
        self.assertEqual(rc, 0)
        self.assertEqual(raw, exporter.canonical_json_bytes(graph))
        self.assertEqual(temporary, [])
        self.assertEqual(graph["relation_count"], 3)
        self.assertEqual(graph["accepted_relation_ids"], [20, 22])
        relation = graph["relations"][0]
        self.assertEqual(relation["relation_id"], 20)
        self.assertEqual(relation["direct_way_ids"], [3])
        self.assertEqual(relation["members"], [
            {"sequence": 0, "type": "way", "ref": 3, "role": ""},
            {"sequence": 1, "type": "relation", "ref": 21,
             "role": "connection"},
            {"sequence": 2, "type": "node", "ref": 100,
             "role": "guidepost"},
        ])
        self.assertEqual(relation["direct_ways"], [{
            "way_id": 3,
            "present": True,
            "tags": {"foot": "yes", "highway": "service",
                     "service": "alley"},
        }])
        self.assertEqual(graph["missing_direct_way_ids"], [9])
        self.assertEqual(graph["relations"][2]["direct_ways"], [{
            "way_id": 9, "present": False, "tags": None,
        }])

    def test_output_is_stable_across_handler_and_tag_mapping_order(self):
        first = self._run(reverse=False)[1]
        second = self._run(reverse=True)[1]
        self.assertEqual(first, second)

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
                    "--in", str(source), "--out", str(output),
                ])
            self.assertEqual(rc, 2)
            self.assertFalse(output.exists())
            self.assertIn("pyosmium/osmium is required", stderr.getvalue())

    def test_source_has_no_assembler_relation_resolution_dependency(self):
        source = (_HERE / "export_relation_members.py").read_text(encoding="utf-8")
        self.assertNotIn("import model", source)
        self.assertNotIn("resolve_route", source)
        self.assertNotIn("trailforge.assemble", source)


if __name__ == "__main__":
    unittest.main()
