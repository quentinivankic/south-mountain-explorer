"""Offline command-generation controls for compact relation PBF slices."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
import unittest

_HERE = Path(__file__).resolve().parent
_SPEC = importlib.util.spec_from_file_location(
    "build_relation_slice_module", _HERE / "build_relation_slice.py")
slicer = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(slicer)
_EXPORT_SPEC = importlib.util.spec_from_file_location(
    "relation_slice_exporter_module", _HERE / "export_relation_members.py")
exporter = importlib.util.module_from_spec(_EXPORT_SPEC)
_EXPORT_SPEC.loader.exec_module(exporter)


class RelationSliceBuilder(unittest.TestCase):
    def _paths(self, root: Path):
        source = root / "denmark.osm.pbf"
        roots = root / "roots.json"
        output = root / "qa" / "mols.raw.osm.pbf"
        source.write_bytes(b"country-pbf")
        roots.write_text(json.dumps({
            "schema_version": 2,
            "root_relation_ids": [20, 22],
        }), encoding="utf-8")
        return source, roots, output

    def test_uses_pinned_recursive_getid_pattern_and_atomic_output(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, roots, output = self._paths(root)
            calls = []

            def fake_run(command, **kwargs):
                calls.append((command, kwargs))
                id_file = Path(command[command.index("--id-file") + 1])
                self.assertEqual(id_file.read_text(), "r20\nr22\n")
                temporary = Path(command[command.index("-o") + 1])
                temporary.write_bytes(b"compact-root-closure")
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            result = slicer.build_slice(
                source, roots, output, runner=fake_run)

            command, kwargs = calls[0]
            self.assertEqual(command[:4], [
                "osmium", "getid", "-r", "--id-file"])
            self.assertNotIn("-t", command)
            self.assertNotIn("--remove-tags", command)
            self.assertEqual(command[-3:], [
                "-o", str(Path(command[-2])), "--overwrite"])
            self.assertEqual(Path(command[5]), source)
            self.assertFalse(Path(command[4]).exists())
            self.assertTrue(kwargs["text"])
            self.assertEqual(output.read_bytes(), b"compact-root-closure")
            self.assertEqual(result["root_relation_ids"], [20, 22])
            self.assertEqual(result["bytes"], len(b"compact-root-closure"))
            self.assertEqual(list(output.parent.glob(".*.tmp.pbf")), [])

    def test_direct_member_tags_survive_fake_slice_and_feed_export_ledger(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "denmark.osm.pbf"
            roots = root / "roots.json"
            output = root / "mols.raw.osm.pbf"
            ledger = root / "mols.relation-members.raw.json"
            document = {
                "relations": [{
                    "id": 20,
                    "tags": {"type": "route", "route": "hiking"},
                    "members": [{"type": "way", "ref": 3, "role": ""}],
                }],
                "ways": [{
                    "id": 3,
                    "tags": {"highway": "service", "service": "alley",
                             "foot": "yes", "name": "Tagged Member"},
                    "nodes": [30, 31],
                }],
                "nodes": [{"id": 30, "lon": 10.3, "lat": 56.3},
                          {"id": 31, "lon": 10.31, "lat": 56.31}],
            }
            source.write_text(json.dumps(document), encoding="utf-8")
            roots.write_text(json.dumps({"root_relation_ids": [20]}),
                             encoding="utf-8")

            def fake_getid(command, **_kwargs):
                self.assertNotIn("-t", command)
                self.assertNotIn("--remove-tags", command)
                Path(command[command.index("-o") + 1]).write_bytes(
                    source.read_bytes())
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            slicer.build_slice(source, roots, output, runner=fake_getid)

            class FakeOsmium:
                class SimpleHandler:
                    def apply_file(self, path):
                        sliced = json.loads(Path(path).read_text())
                        if hasattr(self, "relation"):
                            for row in sliced["relations"]:
                                self.relation(SimpleNamespace(
                                    id=row["id"],
                                    tags=[SimpleNamespace(k=key, v=value)
                                          for key, value in row["tags"].items()],
                                    members=[SimpleNamespace(
                                        type=member["type"], ref=member["ref"],
                                        role=member["role"])
                                        for member in row["members"]]))
                        if hasattr(self, "way"):
                            for row in sliced["ways"]:
                                self.way(SimpleNamespace(
                                    id=row["id"],
                                    tags=[SimpleNamespace(k=key, v=value)
                                          for key, value in row["tags"].items()],
                                    nodes=[SimpleNamespace(ref=node_id)
                                           for node_id in row["nodes"]]))
                        if hasattr(self, "node"):
                            for row in sliced["nodes"]:
                                self.node(SimpleNamespace(
                                    id=row["id"], location=SimpleNamespace(
                                        lon=row["lon"], lat=row["lat"])))

            graph = exporter.export_relation_members(
                output, ledger, scope="raw-denmark", roots_from=roots,
                osmium_module=FakeOsmium)
            direct = graph["relations"][0]["direct_ways"][0]
            self.assertEqual(direct["status"], "present")
            self.assertEqual(direct["tags"], document["ways"][0]["tags"])

    def test_receipt_records_actual_command_parent_roots_version_and_counts(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, roots, output = self._paths(root)
            receipt = root / "qa" / "raw-receipt.json"
            github_output = root / "github-output.txt"
            calls = []

            def fake_run(command, **_kwargs):
                calls.append(command)
                if command[1:] == ["--version"]:
                    return SimpleNamespace(
                        returncode=0, stdout="osmium version 1.16.0\n",
                        stderr="")
                if command[1:3] == ["fileinfo", "-e"]:
                    return SimpleNamespace(
                        returncode=0,
                        stdout=("Number of nodes: 8\nNumber of ways: 4\n"
                                "Number of relations: 2\n"), stderr="")
                Path(command[command.index("-o") + 1]).write_bytes(b"compact")
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            slicer.build_slice(
                source, roots, output, runner=fake_run, receipt=receipt,
                scope="raw-denmark",
                source_label="data/raw/denmark.osm.pbf",
                output_artifact="data/source/mols-bjerge.raw.osm.pbf",
                upstream_stage="relation-root-closure-extract",
                github_output=github_output)

            document = json.loads(receipt.read_text())
            self.assertEqual(document["scope"], "raw-denmark")
            self.assertEqual(document["root_relation_ids"], [20, 22])
            self.assertEqual(document["osmium"]["command"], calls[0])
            self.assertNotIn("-t", document["osmium"]["command"])
            self.assertEqual(document["parent_pbf"]["bytes"],
                             len(b"country-pbf"))
            self.assertEqual(document["compact_output"]["bytes"],
                             len(b"compact"))
            self.assertEqual(document["compact_output"]["object_counts"],
                             {"nodes": 8, "ways": 4, "relations": 2})
            outputs = dict(line.split("=", 1) for line in
                           github_output.read_text().splitlines())
            self.assertEqual(outputs["parent_sha256"],
                             document["parent_pbf"]["sha256"])
            self.assertEqual(outputs["slice_sha256"],
                             document["compact_output"]["sha256"])
            self.assertEqual(outputs["root_set_sha256"],
                             document["root_set_sha256"])
            self.assertEqual(int(outputs["receipt_bytes"]),
                             receipt.stat().st_size)

    def test_missing_osmium_fails_clearly_without_output(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, roots, output = self._paths(root)

            def missing(_command, **_kwargs):
                raise FileNotFoundError("osmium")

            with self.assertRaisesRegex(RuntimeError, "osmium executable"):
                slicer.build_slice(source, roots, output, runner=missing)
            self.assertFalse(output.exists())

    def test_failed_or_empty_command_never_replaces_existing_output(self):
        for mode in ("failed", "empty"):
            with self.subTest(mode=mode), TemporaryDirectory() as tmp:
                root = Path(tmp)
                source, roots, output = self._paths(root)
                output.parent.mkdir(parents=True)
                output.write_bytes(b"preserve")

                def fake_run(command, **_kwargs):
                    if mode == "empty":
                        Path(command[command.index("-o") + 1]).write_bytes(b"")
                        return SimpleNamespace(
                            returncode=0, stdout="", stderr="")
                    return SimpleNamespace(
                        returncode=7, stdout="", stderr="synthetic failure")

                with self.assertRaises(RuntimeError):
                    slicer.build_slice(source, roots, output, runner=fake_run)
                self.assertEqual(output.read_bytes(), b"preserve")

    def test_invalid_roots_and_source_output_collision_fail_before_command(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, roots, _ = self._paths(root)
            roots.write_text(json.dumps({"root_relation_ids": [22, 20]}),
                             encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "root_relation_ids"):
                slicer.build_slice(source, roots, root / "out.pbf")
            roots.write_text(json.dumps({"root_relation_ids": [20]}),
                             encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "must differ"):
                slicer.build_slice(source, roots, source)


if __name__ == "__main__":
    unittest.main()
