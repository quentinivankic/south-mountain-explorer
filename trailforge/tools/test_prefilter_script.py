"""Shell and destination-policy contracts for the hiking prefilter."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory
import textwrap
import unittest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import export_way_topology as topology_exporter  # noqa: E402
import validate_publish_pilot as validator  # noqa: E402

_PREFILTER_SCRIPT = _HERE.parent / "extract" / "prefilter.sh"

_HEAD_COMPATIBLE_NODE_SELECTORS = {
    ("natural", "peak"),
    ("natural", "arch"),
    ("natural", "saddle"),
    ("natural", "cliff"),
    ("natural", "rock"),
    ("natural", "stone"),
    ("waterway", "waterfall"),
    ("tourism", "viewpoint"),
    ("tourism", "alpine_hut"),
    ("tourism", "wilderness_hut"),
    ("mountain_pass", "yes"),
    ("highway", "trailhead"),
}
_DK_ONLY_RAW_VIEWER_SELECTORS = {
    ("tourism", "attraction"),
    ("historic", "archaeological_site"),
    ("historic", "castle"),
    ("historic", "ruins"),
    ("amenity", "shelter"),
}
_HEAD_COMPATIBLE_NODE_ARGUMENTS = [
    "n/natural=peak,arch,saddle,cliff,rock,stone",
    "n/waterway=waterfall",
    "n/tourism=viewpoint,alpine_hut,wilderness_hut",
    "n/mountain_pass=yes",
    "n/highway=trailhead",
]
_DK_ONLY_RAW_VIEWER_ARGUMENTS = [
    "n/tourism=attraction",
    "n/historic=archaeological_site,castle,ruins",
    "n/amenity=shelter",
]


def _write_executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)


def _node_selectors(command: list[str]) -> list[tuple[str, str]]:
    selectors = []
    for argument in command:
        if not argument.startswith("n/") or "=" not in argument:
            continue
        key, values = argument[2:].split("=", 1)
        selectors.extend((key, value) for value in values.split(","))
    return selectors


def _script_node_arguments() -> list[str]:
    return re.findall(
        r"^\s+(n/[^\s#]+)\s*$",
        _PREFILTER_SCRIPT.read_text(encoding="utf-8"), re.MULTILINE)


def _script_node_selectors() -> list[tuple[str, str]]:
    return _node_selectors(_script_node_arguments())


class PrefilterScriptReceipt(unittest.TestCase):
    def _environment(self, root: Path, *, fail: bool = False):
        binary = root / "bin"
        binary.mkdir()
        log = root / "osmium.jsonl"
        _write_executable(binary / "osmium", textwrap.dedent(f"""\
            #!{sys.executable}
            import json
            import os
            from pathlib import Path
            import sys

            with Path(os.environ["FAKE_OSMIUM_LOG"]).open(
                    "a", encoding="utf-8") as output:
                output.write(json.dumps(
                    sys.argv[1:], separators=(",", ":")) + "\\n")
            arguments = sys.argv[1:]
            if arguments and arguments[0] == "tags-filter":
                if os.environ.get("FAKE_PREFILTER_FAIL") == "1":
                    raise SystemExit(9)
                output = Path(arguments[arguments.index("-o") + 1])
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(b"filtered-denmark-pbf")
            elif arguments == ["--version"]:
                print("osmium version synthetic")
            elif arguments[:2] == ["fileinfo", "-e"]:
                if arguments[-1].endswith("denmark.osm.pbf"):
                    counts = (100, 40, 8)
                else:
                    counts = (60, 20, 4)
                print(f"Number of nodes: {{counts[0]}}")
                print(f"Number of ways: {{counts[1]}}")
                print(f"Number of relations: {{counts[2]}}")
                print("Bounding box: (10,56,11,57)")
            else:
                raise SystemExit(11)
            """))
        environment = dict(os.environ)
        environment.update({
            "PATH": f"{binary}{os.pathsep}{environment.get('PATH', '')}",
            "FAKE_OSMIUM_LOG": str(log),
            "RECEIPT_GITHUB_OUTPUT": str(root / "github-output.txt"),
        })
        if fail:
            environment["FAKE_PREFILTER_FAIL"] = "1"
        return environment, log

    @staticmethod
    def _worktree(root: Path) -> Path:
        work = root / "trailforge"
        (work / "data" / "raw").mkdir(parents=True)
        (work / "data" / "raw" / "denmark.osm.pbf").write_bytes(
            b"raw-denmark-pbf")
        return work

    def test_executed_argv_and_receipt_match_validator_pin_byte_for_byte(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = self._worktree(root)
            environment, log = self._environment(root)

            completed = subprocess.run([
                "bash", str(_PREFILTER_SCRIPT),
                "data/raw/denmark.osm.pbf", "data/hiking.osm.pbf",
                "reports/prefilter-transformation.json",
            ], cwd=work, env=environment, check=False,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

            self.assertEqual(completed.returncode, 0, completed.stderr)
            calls = [json.loads(line) for line in
                     log.read_text(encoding="utf-8").splitlines()]
            executed = ["osmium", *calls[0]]
            pinned = validator._pinned_prefilter_command()
            self.assertEqual(executed, pinned)
            self.assertEqual(
                json.dumps(executed, separators=(",", ":")).encode(),
                json.dumps(pinned, separators=(",", ":")).encode())
            self.assertNotIn("-t", executed)
            self.assertEqual(_node_selectors(executed),
                             _node_selectors(pinned))

            receipt_path = work / "reports/prefilter-transformation.json"
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(receipt["osmium"]["command"], executed)
            self.assertEqual(receipt["parent_pbf"], {
                "sha256": hashlib.sha256(b"raw-denmark-pbf").hexdigest(),
                "bytes": len(b"raw-denmark-pbf"),
                "object_counts": {"nodes": 100, "ways": 40, "relations": 8},
            })
            self.assertEqual(receipt["output_pbf"], {
                "artifact": "data/hiking.osm.pbf",
                "sha256": hashlib.sha256(b"filtered-denmark-pbf").hexdigest(),
                "bytes": len(b"filtered-denmark-pbf"),
                "object_counts": {"nodes": 60, "ways": 20, "relations": 4},
            })
            outputs = dict(
                line.split("=", 1) for line in
                Path(environment["RECEIPT_GITHUB_OUTPUT"])
                .read_text(encoding="utf-8").splitlines())
            self.assertEqual(outputs["input_sha256"],
                             receipt["parent_pbf"]["sha256"])
            self.assertEqual(outputs["input_bytes"],
                             str(receipt["parent_pbf"]["bytes"]))
            self.assertEqual(outputs["output_sha256"],
                             receipt["output_pbf"]["sha256"])
            self.assertEqual(outputs["output_bytes"],
                             str(receipt["output_pbf"]["bytes"]))

    def test_failed_prefilter_writes_no_receipt_or_trust_output(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = self._worktree(root)
            environment, _log = self._environment(root, fail=True)

            completed = subprocess.run([
                "bash", str(_PREFILTER_SCRIPT),
                "data/raw/denmark.osm.pbf", "data/hiking.osm.pbf",
                "reports/prefilter-transformation.json",
            ], cwd=work, env=environment, check=False,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

            self.assertEqual(completed.returncode, 9)
            self.assertFalse((work / "data/hiking.osm.pbf").exists())
            self.assertFalse(
                (work / "reports/prefilter-transformation.json").exists())
            self.assertFalse(
                Path(environment["RECEIPT_GITHUB_OUTPUT"]).exists())


class DestinationPolicyParity(unittest.TestCase):
    def test_prefilter_covers_equal_independent_exact_denmark_policies(self):
        producer = dict(validator.assembly_model.DK_DESTINATION_POI_CLASSES)
        exporter = dict(topology_exporter._DESTINATION_POI_CLASSES)
        final_validator = dict(validator._DESTINATION_POI_CLASSES)
        self.assertEqual(producer, exporter)
        self.assertEqual(exporter, final_validator)

        selectors = set(_script_node_selectors())
        self.assertEqual(
            selectors,
            set(_node_selectors(validator._pinned_prefilter_command())))
        self.assertTrue(set(producer).issubset(selectors))
        self.assertEqual(
            selectors,
            _HEAD_COMPATIBLE_NODE_SELECTORS | _DK_ONLY_RAW_VIEWER_SELECTORS)

    def test_non_denmark_selector_inputs_match_head_and_exclude_new_legacy_pois(self):
        selectors = set(_script_node_selectors())
        self.assertEqual(
            selectors,
            set(_node_selectors(validator._pinned_prefilter_command())))
        self.assertEqual(
            _script_node_arguments(),
            _HEAD_COMPATIBLE_NODE_ARGUMENTS +
            _DK_ONLY_RAW_VIEWER_ARGUMENTS)
        self.assertEqual(
            selectors - _DK_ONLY_RAW_VIEWER_SELECTORS,
            _HEAD_COMPATIBLE_NODE_SELECTORS)
        self.assertTrue(_DK_ONLY_RAW_VIEWER_SELECTORS.isdisjoint(
            validator.assembly_model.DESTINATION_POIS))
        self.assertTrue({
            ("waterway", "waterfall"),
            ("tourism", "alpine_hut"),
            ("tourism", "wilderness_hut"),
            ("mountain_pass", "yes"),
        }.issubset(selectors))
        self.assertTrue({
            ("natural", "volcano"), ("natural", "hot_spring"),
        }.isdisjoint(selectors))


if __name__ == "__main__":
    unittest.main()
