"""Shell contract tests for default and provenance-enabled AOI extraction."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import textwrap
import unittest

_HERE = Path(__file__).resolve().parent
_AOI_SCRIPT = _HERE.parent / "extract" / "aoi.sh"


def _write_executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)


class AoiScriptReceipt(unittest.TestCase):
    def _environment(self, root: Path, *, pilot: bool = True,
                     fail_extract: bool = False,
                     pyosmium_available: bool = True):
        binary = root / "bin"
        binary.mkdir()
        osmium_log = root / "osmium.jsonl"
        python_log = root / "python.jsonl"
        fake_osmium = binary / "osmium"
        _write_executable(fake_osmium, textwrap.dedent(f"""\
            #!{sys.executable}
            import json
            import os
            from pathlib import Path
            import sys

            with Path(os.environ["FAKE_OSMIUM_LOG"]).open("a", encoding="utf-8") as out:
                out.write(json.dumps(sys.argv[1:], separators=(",", ":")) + "\\n")
            args = sys.argv[1:]
            if args and args[0] == "extract":
                if os.environ.get("FAKE_EXTRACT_FAIL") == "1":
                    raise SystemExit(9)
                output = Path(args[args.index("-o") + 1])
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(b"executed-aoi-pbf")
            elif args and args[0] == "export":
                output = Path(args[args.index("-o") + 1])
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text('{{"type":"FeatureCollection","features":[]}}\\n',
                                  encoding="utf-8")
            elif args == ["--version"]:
                print("osmium version synthetic")
            elif args[:2] == ["fileinfo", "-e"]:
                print("Number of nodes: 10")
                print("Number of ways: 3")
                print("Number of relations: 2")
            else:
                raise SystemExit(11)
            """))
        fake_python = binary / "python3"
        _write_executable(fake_python, textwrap.dedent(f"""\
            #!{sys.executable}
            import hashlib
            import json
            import os
            from pathlib import Path
            import sys

            with Path(os.environ["FAKE_PYTHON_LOG"]).open("a", encoding="utf-8") as out:
                out.write(json.dumps(sys.argv[1:], separators=(",", ":")) + "\\n")
            if sys.argv[1:3] == ["-c", "import osmium"]:
                raise SystemExit(0 if os.environ.get("FAKE_PYOSMIUM") == "1" else 1)
            target = sys.argv[1] if len(sys.argv) > 1 else ""
            if target.endswith("export_relation_members.py"):
                output = Path(sys.argv[sys.argv.index("--out") + 1])
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(json.dumps({{
                    "schema_version": 2,
                    "root_relation_ids": [700],
                }}) + "\\n", encoding="utf-8")
                if "--github-output" in sys.argv:
                    raw = output.read_bytes()
                    github_output = Path(
                        sys.argv[sys.argv.index("--github-output") + 1])
                    with github_output.open("a", encoding="utf-8") as stream:
                        stream.write(
                            f"relation_ledger_sha256={{hashlib.sha256(raw).hexdigest()}}\\n")
                        stream.write(f"relation_ledger_bytes={{len(raw)}}\\n")
                raise SystemExit(0)
            if target.endswith("export_way_topology.py"):
                output = Path(sys.argv[sys.argv.index("--out") + 1])
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(json.dumps({{
                    "schema_version": 3,
                    "way_count": 0,
                    "ways": [],
                    "relation_count": 0,
                    "relations": [],
                    "destination_poi_count": 0,
                    "destination_pois": [],
                }}) + "\\n", encoding="utf-8")
                if "--github-output" in sys.argv:
                    raw = output.read_bytes()
                    github_output = Path(
                        sys.argv[sys.argv.index("--github-output") + 1])
                    with github_output.open("a", encoding="utf-8") as stream:
                        stream.write(
                            f"way_topology_sha256={{hashlib.sha256(raw).hexdigest()}}\\n")
                        stream.write(f"way_topology_bytes={{len(raw)}}\\n")
                raise SystemExit(0)
            os.execv({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]])
            """))
        environment = dict(os.environ)
        environment.update({
            "PATH": f"{binary}{os.pathsep}{environment.get('PATH', '')}",
            "FAKE_OSMIUM_LOG": str(osmium_log),
            "FAKE_PYTHON_LOG": str(python_log),
            "FAKE_PYOSMIUM": "1" if pyosmium_available else "0",
            "HIKING": "data/hiking.osm.pbf",
            "NAME": "mols-bjerge",
            "BBOX": "10.40,56.08,10.90,56.35",
        })
        if pilot:
            environment.update({
                "AOI_RECEIPT": str(root / "qa" / "mols.scope.aoi.json"),
                "AOI_RELATION_MEMBERS": str(root / "qa" / "roots.json"),
                "AOI_WAY_TOPOLOGY": str(
                    root / "trailforge" / "data" / "aoi" /
                    "mols-bjerge.way-topology.json"),
                "AOI_GITHUB_OUTPUT": str(root / "github-output.txt"),
                "AOI_PARENT_LABEL": "data/hiking.osm.pbf",
                "AOI_OUTPUT_ARTIFACT": "data/aoi/mols-bjerge.osm.pbf",
            })
        if fail_extract:
            environment["FAKE_EXTRACT_FAIL"] = "1"
        return environment, osmium_log, python_log

    def _worktree(self, root: Path) -> Path:
        work = root / "trailforge"
        (work / "data").mkdir(parents=True)
        (work / "data" / "hiking.osm.pbf").write_bytes(b"prefilter-parent")
        return work

    def test_default_no_env_preserves_original_export_without_python(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = self._worktree(root)
            environment, osmium_log, python_log = self._environment(
                root, pilot=False, pyosmium_available=False)

            completed = subprocess.run(
                ["bash", str(_AOI_SCRIPT)], cwd=work, env=environment,
                check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True)

            self.assertEqual(completed.returncode, 0, completed.stderr)
            calls = [json.loads(line) for line in
                     osmium_log.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([call[0] for call in calls], ["extract", "export"])
            export = calls[1]
            self.assertIn("--geometry-types=linestring,point", export)
            self.assertNotIn("--geometry-types=linestring,polygon,point", export)
            self.assertFalse(python_log.exists())
            self.assertFalse(
                (work / "data/aoi/mols-bjerge.way-topology.json").exists())

    def test_partial_provenance_env_fails_before_extraction(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = self._worktree(root)
            environment, osmium_log, _ = self._environment(root)
            environment.pop("AOI_WAY_TOPOLOGY")

            completed = subprocess.run(
                ["bash", str(_AOI_SCRIPT)], cwd=work, env=environment,
                check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True)

            self.assertEqual(completed.returncode, 2)
            self.assertIn("requires AOI_RECEIPT", completed.stderr)
            self.assertFalse(osmium_log.exists())
            self.assertFalse((work / "data/aoi/mols-bjerge.osm.pbf").exists())

    def test_missing_provenance_tool_fails_before_extraction(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = self._worktree(root)
            environment, osmium_log, _ = self._environment(root)
            isolated = root / "isolated" / "extract"
            isolated.mkdir(parents=True)
            copied_script = isolated / "aoi.sh"
            copied_script.write_bytes(_AOI_SCRIPT.read_bytes())

            completed = subprocess.run(
                ["bash", str(copied_script)], cwd=work, env=environment,
                check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True)

            self.assertEqual(completed.returncode, 2)
            self.assertIn("requires", completed.stderr)
            self.assertIn("export_relation_members.py", completed.stderr)
            self.assertFalse(osmium_log.exists())
            self.assertFalse((work / "data/aoi/mols-bjerge.osm.pbf").exists())

    def test_missing_pyosmium_fails_before_extraction(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = self._worktree(root)
            environment, osmium_log, _ = self._environment(
                root, pyosmium_available=False)

            completed = subprocess.run(
                ["bash", str(_AOI_SCRIPT)], cwd=work, env=environment,
                check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True)

            self.assertEqual(completed.returncode, 2)
            self.assertIn("requires pyosmium before extraction", completed.stderr)
            self.assertFalse(osmium_log.exists())
            self.assertFalse(Path(environment["AOI_RECEIPT"]).exists())
            self.assertFalse((work / "data/aoi/mols-bjerge.osm.pbf").exists())

    def test_receipt_argv_byte_equals_the_executed_extract_command(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = self._worktree(root)
            environment, osmium_log, python_log = self._environment(root)

            completed = subprocess.run(
                ["bash", str(_AOI_SCRIPT)], cwd=work, env=environment,
                check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True)

            self.assertEqual(completed.returncode, 0, completed.stderr)
            calls = [json.loads(line) for line in
                     osmium_log.read_text(encoding="utf-8").splitlines()]
            executed = calls[0]
            export = next(call for call in calls if call[0] == "export")
            self.assertIn("--geometry-types=linestring,polygon,point", export)
            receipt = json.loads(Path(environment["AOI_RECEIPT"]).read_text())
            self.assertEqual(receipt["osmium"]["command"],
                             ["osmium", *executed])
            self.assertEqual(receipt["extraction"]["name"], "mols-bjerge")
            self.assertEqual(receipt["extraction"]["bbox"],
                             "10.40,56.08,10.90,56.35")
            outputs = dict(line.split("=", 1) for line in
                           Path(environment["AOI_GITHUB_OUTPUT"])
                           .read_text().splitlines())
            self.assertEqual(outputs["slice_sha256"],
                             receipt["compact_output"]["sha256"])
            self.assertEqual(outputs["root_set_sha256"],
                             receipt["root_set_sha256"])
            relation_raw = Path(environment["AOI_RELATION_MEMBERS"]).read_bytes()
            topology_raw = Path(environment["AOI_WAY_TOPOLOGY"]).read_bytes()
            self.assertEqual(
                outputs["relation_ledger_sha256"],
                hashlib.sha256(relation_raw).hexdigest())
            self.assertEqual(outputs["relation_ledger_bytes"],
                             str(len(relation_raw)))
            self.assertEqual(
                outputs["way_topology_sha256"],
                hashlib.sha256(topology_raw).hexdigest())
            self.assertEqual(outputs["way_topology_bytes"],
                             str(len(topology_raw)))
            self.assertTrue(Path(environment["AOI_WAY_TOPOLOGY"]).is_file())
            python_calls = [json.loads(line) for line in
                            python_log.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(sum(
                bool(call and call[0].endswith("export_way_topology.py"))
                for call in python_calls), 1)

    def test_failed_extract_writes_no_receipt_or_trust_outputs(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = self._worktree(root)
            environment, _, _ = self._environment(root, fail_extract=True)

            completed = subprocess.run(
                ["bash", str(_AOI_SCRIPT)], cwd=work, env=environment,
                check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True)

            self.assertEqual(completed.returncode, 9)
            self.assertFalse(Path(environment["AOI_RECEIPT"]).exists())
            self.assertFalse(Path(environment["AOI_GITHUB_OUTPUT"]).exists())
            self.assertFalse(Path(environment["AOI_WAY_TOPOLOGY"]).exists())


if __name__ == "__main__":
    unittest.main()
