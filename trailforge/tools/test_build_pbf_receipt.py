"""Offline provenance-receipt controls for existing osmium PBF stages."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

_HERE = Path(__file__).resolve().parent
_SPEC = importlib.util.spec_from_file_location(
    "build_pbf_receipt_module", _HERE / "build_pbf_receipt.py")
receipts = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(receipts)

_COUNTS = "Number of nodes: 10\nNumber of ways: 4\nNumber of relations: 2\n"


def _runner(command, **_kwargs):
    if command[1:] == ["--version"]:
        return SimpleNamespace(returncode=0, stdout="osmium version 1.16.0\n",
                               stderr="")
    return SimpleNamespace(returncode=0, stdout=_COUNTS, stderr="")


class PbfReceipt(unittest.TestCase):
    def test_scope_receipt_binds_parent_output_roots_command_and_counts(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent = root / "hiking.osm.pbf"
            output = root / "mols.osm.pbf"
            roots = root / "roots.json"
            receipt = root / "receipt.json"
            parent.write_bytes(b"prefilter-parent")
            output.write_bytes(b"aoi-compact")
            roots.write_text(json.dumps({"root_relation_ids": [700, 701]}),
                             encoding="utf-8")
            command = ["osmium", "extract", "--strategy=smart", "--bbox",
                       "10.40,56.08,10.90,56.35", "data/hiking.osm.pbf",
                       "-o", "data/aoi/mols-bjerge.osm.pbf", "--overwrite"]

            document = receipts.build_receipt(
                parent, output, receipt, kind="scope", scope="aoi",
                parent_label="data/hiking.osm.pbf",
                output_artifact="data/aoi/mols-bjerge.osm.pbf",
                upstream_stage="aoi-extract", command=command,
                roots_from=roots, aoi_name="mols-bjerge",
                aoi_bbox="10.40,56.08,10.90,56.35", runner=_runner)

            self.assertEqual(json.loads(receipt.read_text()), document)
            self.assertEqual(document["root_relation_ids"], [700, 701])
            self.assertEqual(document["osmium"]["command"], command)
            self.assertEqual(document["parent_pbf"]["object_counts"],
                             {"nodes": 10, "ways": 4, "relations": 2})
            self.assertEqual(document["compact_output"]["bytes"],
                             len(b"aoi-compact"))
            self.assertEqual(document["extraction"], {
                "name": "mols-bjerge",
                "bbox": "10.40,56.08,10.90,56.35",
                "root_bbox_sha256": receipts._canonical_sha256({
                    "bbox": "10.40,56.08,10.90,56.35",
                    "name": "mols-bjerge",
                    "root_relation_ids": [700, 701],
                }),
            })

    def test_scope_github_outputs_are_actual_durable_receipt_values(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent = root / "hiking.osm.pbf"
            output = root / "mols.osm.pbf"
            roots = root / "roots.json"
            receipt = root / "receipt.json"
            github_output = root / "github-output.txt"
            parent.write_bytes(b"prefilter-parent")
            output.write_bytes(b"aoi-compact")
            roots.write_text(json.dumps({"root_relation_ids": [700]}),
                             encoding="utf-8")
            command = [
                "osmium", "extract", "--strategy=smart", "--bbox",
                "10.40,56.08,10.90,56.35", "data/hiking.osm.pbf",
                "-o", "data/aoi/mols-bjerge.osm.pbf", "--overwrite",
            ]
            document = receipts.build_receipt(
                parent, output, receipt, kind="scope", scope="aoi",
                parent_label="data/hiking.osm.pbf",
                output_artifact="data/aoi/mols-bjerge.osm.pbf",
                upstream_stage="aoi-extract", command=command,
                roots_from=roots, aoi_name="mols-bjerge",
                aoi_bbox="10.40,56.08,10.90,56.35", runner=_runner)

            receipts.emit_github_outputs(document, receipt, github_output)

            values = dict(line.split("=", 1) for line in
                          github_output.read_text().splitlines())
            self.assertEqual(values["parent_sha256"],
                             document["parent_pbf"]["sha256"])
            self.assertEqual(values["slice_sha256"],
                             document["compact_output"]["sha256"])
            self.assertEqual(values["root_set_sha256"],
                             document["root_set_sha256"])
            self.assertEqual(values["root_bbox_sha256"],
                             document["extraction"]["root_bbox_sha256"])
            self.assertEqual(values["receipt_sha256"],
                             receipts._identity(receipt)["sha256"])
            self.assertEqual(int(values["receipt_bytes"]),
                             receipt.stat().st_size)

    def test_aoi_receipt_requires_explicit_name_and_bbox(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent = root / "hiking.osm.pbf"
            output = root / "mols.osm.pbf"
            roots = root / "roots.json"
            parent.write_bytes(b"prefilter-parent")
            output.write_bytes(b"aoi-compact")
            roots.write_text(json.dumps({"root_relation_ids": [700]}),
                             encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "name and bbox"):
                receipts.build_receipt(
                    parent, output, root / "receipt.json", kind="scope",
                    scope="aoi", parent_label="data/hiking.osm.pbf",
                    output_artifact="data/aoi/mols-bjerge.osm.pbf",
                    upstream_stage="aoi-extract",
                    command=["osmium", "extract"], roots_from=roots,
                    runner=_runner)

    def test_transformation_github_outputs_bind_input_output_and_receipt(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent = root / "raw.pbf"
            output = root / "hiking.pbf"
            receipt = root / "receipt.json"
            github_output = root / "github-output.txt"
            parent.write_bytes(b"raw-parent")
            output.write_bytes(b"filtered-parent")
            document = receipts.build_receipt(
                parent, output, receipt, kind="transformation",
                scope="prefiltered-denmark",
                parent_label="data/raw/denmark.osm.pbf",
                output_artifact="data/hiking.osm.pbf",
                upstream_stage="prefilter",
                command=["osmium", "tags-filter",
                         "data/raw/denmark.osm.pbf",
                         "-o", "data/hiking.osm.pbf", "--overwrite"],
                runner=_runner)

            receipts.emit_github_outputs(document, receipt, github_output)

            values = dict(line.split("=", 1) for line in
                          github_output.read_text().splitlines())
            self.assertEqual(values["input_sha256"],
                             document["parent_pbf"]["sha256"])
            self.assertEqual(values["output_sha256"],
                             document["output_pbf"]["sha256"])
            self.assertEqual(values["receipt_sha256"],
                             receipts._identity(receipt)["sha256"])

    def test_transformation_receipt_forbids_roots_and_path_relabeling(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent = root / "raw.pbf"
            output = root / "hiking.pbf"
            roots = root / "roots.json"
            parent.write_bytes(b"raw")
            output.write_bytes(b"filtered")
            roots.write_text(json.dumps({"root_relation_ids": [700]}),
                             encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "cannot carry"):
                receipts.build_receipt(
                    parent, output, root / "receipt.json",
                    kind="transformation", scope="prefiltered-denmark",
                    parent_label="data/raw/denmark.osm.pbf",
                    output_artifact="data/hiking.osm.pbf",
                    upstream_stage="prefilter", command=["osmium", "tags-filter"],
                    roots_from=roots, runner=_runner)
            with self.assertRaisesRegex(ValueError, "canonical relative"):
                receipts.build_receipt(
                    parent, output, root / "receipt.json",
                    kind="transformation", scope="prefiltered-denmark",
                    parent_label="../fake",
                    output_artifact="data/hiking.osm.pbf",
                    upstream_stage="prefilter", command=["osmium", "tags-filter"],
                    runner=_runner)


if __name__ == "__main__":
    unittest.main()
