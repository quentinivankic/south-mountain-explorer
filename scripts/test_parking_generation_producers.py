"""Offline producer and generation-driver integrity tests."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import types
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
TOOLS = HERE / "parking-adjud" / "tools"
for path in (str(HERE), str(TOOLS)):
    if path not in sys.path:
        sys.path.insert(0, path)

import dossier_output  # noqa: E402
import geom_source  # noqa: E402


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, allow_nan=False))


def _valid_geom(bbox=None):
    return {
        "bbox": bbox or [-105.6, 39.9, -105.4, 40.1],
        "trails": [{
            "name": "Test Trail",
            "segments": [[[40.0, -105.5], [40.01, -105.49]]],
        }],
    }


def test_geom_inventory_rejects_duplicate_keys(tmp_path):
    (tmp_path / "duplicate.json").write_bytes(
        b'{"bbox":[-1,-1,1,1],"bbox":[-1,-1,1,1],"trails":[]}'
    )
    with pytest.raises(ValueError, match="duplicate JSON key"):
        geom_source.load_geom_inventory(tmp_path)


def test_geom_inventory_rejects_unreadable_regular_file(tmp_path, monkeypatch):
    path = tmp_path / "unreadable.json"
    _write_json(path, _valid_geom())
    real_read = dossier_output.read_strict_regular_json

    def unreadable(candidate, context):
        if Path(candidate).name == path.name:
            raise PermissionError("simulated unreadable geometry")
        return real_read(candidate, context)

    monkeypatch.setattr(
        dossier_output, "read_strict_regular_json", unreadable
    )
    with pytest.raises(ValueError, match="not readable"):
        geom_source.load_geom_inventory(tmp_path)


@pytest.mark.parametrize("bbox", [
    [-105.6, 39.9, -105.4],
    [-105.4, 39.9, -105.6, 40.1],
    [-181.0, 39.9, -105.4, 40.1],
])
def test_geom_inventory_rejects_bad_bbox(tmp_path, bbox):
    _write_json(tmp_path / "bad-bbox.json", _valid_geom(bbox))
    with pytest.raises(ValueError, match="bbox"):
        geom_source.load_geom_inventory(tmp_path)


@pytest.mark.parametrize("trails, message", [
    ({"not": "an array"}, "trails must be an array"),
    ([{"name": "Trail", "segments": {}}], "segments must be an array"),
    ([{"name": "Trail", "segments": [[[91.0, 0.0]]]}], "outside coordinate range"),
])
def test_relevant_geom_rejects_malformed_trails(
        tmp_path, trails, message):
    document = _valid_geom()
    document["trails"] = trails
    _write_json(tmp_path / "relevant.json", document)
    inventory = geom_source.load_geom_inventory(tmp_path)
    with pytest.raises(ValueError, match=message):
        geom_source.relevant_trail_documents(
            inventory, (-105.7, 39.8, -105.3, 40.2)
        )


def test_geom_inventory_rejects_nonfinite_coordinate(tmp_path):
    (tmp_path / "nonfinite.json").write_bytes(
        b'{"bbox":[-105.6,39.9,-105.4,40.1],"trails":'
        b'[{"segments":[[[NaN,-105.5]]]}]}'
    )
    with pytest.raises(ValueError, match="nonfinite JSON number"):
        geom_source.load_geom_inventory(tmp_path)


def test_disjoint_geom_ignores_deep_malformed_payload_and_inventory_is_sorted(
        tmp_path):
    disjoint = {
        "bbox": [10.0, 10.0, 11.0, 11.0],
        "trails": {"deep": "malformed but irrelevant"},
    }
    _write_json(tmp_path / "z-disjoint.json", disjoint)
    _write_json(tmp_path / "a-relevant.json", _valid_geom())

    inventory = geom_source.load_geom_inventory(tmp_path)
    assert [entry.path.name for entry in inventory] == [
        "a-relevant.json", "z-disjoint.json",
    ]
    relevant = geom_source.relevant_trail_documents(
        inventory, (-105.7, 39.8, -105.3, 40.2)
    )
    assert [entry.path.name for entry in relevant] == ["a-relevant.json"]


def _load_dossier_with_fake_geometry(monkeypatch):
    fake_osmium = types.ModuleType("osmium")

    class SimpleHandler:
        def __init__(self):
            pass

    class WKBFactory:
        def create_multipolygon(self, _area):
            raise AssertionError("factory must be replaced by the test")

    fake_osmium.SimpleHandler = SimpleHandler
    fake_osmium.geom = types.SimpleNamespace(WKBFactory=WKBFactory)

    fake_shapely = types.ModuleType("shapely")
    fake_shapely.__path__ = []
    fake_wkb = types.ModuleType("shapely.wkb")
    fake_wkb.loads = lambda *_args, **_kwargs: None
    fake_geometry = types.ModuleType("shapely.geometry")
    fake_geometry.Point = object
    fake_geometry.Polygon = object
    fake_strtree = types.ModuleType("shapely.strtree")
    fake_strtree.STRtree = object
    fake_shapely.wkb = fake_wkb

    monkeypatch.setitem(sys.modules, "osmium", fake_osmium)
    monkeypatch.setitem(sys.modules, "shapely", fake_shapely)
    monkeypatch.setitem(sys.modules, "shapely.wkb", fake_wkb)
    monkeypatch.setitem(sys.modules, "shapely.geometry", fake_geometry)
    monkeypatch.setitem(sys.modules, "shapely.strtree", fake_strtree)

    module_name = "parking_dossier_producer_tests"
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(
        module_name, TOOLS / "dossier.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class _Area:
    def __init__(self, ident, amenity="parking", from_way=True):
        self.ident = ident
        self.tags = [types.SimpleNamespace(k="amenity", v=amenity)]
        self._from_way = from_way

    def from_way(self):
        return self._from_way

    def orig_id(self):
        return self.ident


def test_parking_multipolygon_failures_accumulate_but_nonparking_is_irrelevant(
        monkeypatch):
    dossier = _load_dossier_with_fake_geometry(monkeypatch)
    handler = dossier.ParkH((-180.0, -90.0, 180.0, 90.0))
    calls = []

    class FailingFactory:
        def create_multipolygon(self, area):
            calls.append(area.ident)
            raise RuntimeError("nondeterministic library detail")

    handler.wkb = FailingFactory()
    handler.area(_Area(99, amenity="school"))
    handler.area(_Area(9, from_way=True))
    handler.area(_Area(2, from_way=False))

    assert calls == [9, 2]
    assert handler.lots == []
    with pytest.raises(ValueError, match="parking area geometry failures") as caught:
        handler.raise_for_errors()
    message = str(caught.value)
    assert "relation/2: WKB multipolygon conversion failed" in message
    assert "way/9: WKB multipolygon conversion failed" in message
    assert message.index("relation/2") < message.index("way/9")
    assert "nondeterministic library detail" not in message


def _quartet_documents(slug="test-area"):
    old_walk = {"walk_m": None, "conn": "no route", "trail": None}
    return {
        "dossier": {
            "slug": slug,
            "bbox": [-105.6, 39.9, -105.4, 40.1],
            "facilities": [{
                "fid": 0,
                "lat": 40.0,
                "lon": -105.5,
                "walk": copy.deepcopy(old_walk),
            }],
        },
        "serves": {"0": {"served": False}},
        "context": {"0": {"category": "NEUTRAL"}},
        "walk": {"0": old_walk},
    }


def _write_quartet(root: Path, slug="test-area"):
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    documents = _quartet_documents(slug)
    for key, suffix in (
            ("dossier", "dossier"),
            ("serves", "serves2"),
            ("context", "context"),
            ("walk", "walk")):
        _write_json(root / f"{slug}_{suffix}.json", documents[key])
    capture = dossier_output.bootstrap_generation(root, slug)
    return documents, capture


def test_producer_input_dossier_is_strict_json(tmp_path):
    root = tmp_path / "strict-dossier"
    root.mkdir(mode=0o700)
    (root / "test-area_dossier.json").write_bytes(
        b'{"slug":"test-area","slug":"test-area","facilities":[]}'
    )
    with pytest.raises(ValueError, match="duplicate JSON key"):
        dossier_output.capture_producer_dossier(root, "test-area")


def test_context_producer_rechecks_exact_dossier_before_install(tmp_path):
    root = tmp_path / "stale-context"
    documents, capture = _write_quartet(root)
    manifest = dossier_output.generation_manifest_path(root, "test-area")
    manifest_before = manifest.read_bytes()
    context_path = root / "test-area_context.json"
    context_before = context_path.read_bytes()
    dossier, source_bytes, source_sha256 = (
        dossier_output.capture_producer_dossier(root, "test-area")
    )
    changed = copy.deepcopy(documents["dossier"])
    changed["facilities"][0]["lat"] = 40.01
    _write_json(root / "test-area_dossier.json", changed)

    with pytest.raises(ValueError, match="input dossier changed"):
        dossier_output.write_context_output(
            root, "test-area",
            source_dossier_bytes=source_bytes,
            source_dossier_sha256=source_sha256,
            dossier=dossier,
            context={"0": {"category": "SUPPORT"}},
        )
    assert context_path.read_bytes() == context_before
    assert manifest.read_bytes() == manifest_before
    assert capture["source_generation"]["manifest_sha256"] == hashlib.sha256(
        manifest_before
    ).hexdigest()


def test_walk_second_atomic_failure_preserves_manifest_and_fails_closed(
        tmp_path, monkeypatch):
    root = tmp_path / "walk-atomic-failure"
    _documents, _capture = _write_quartet(root)
    manifest = dossier_output.generation_manifest_path(root, "test-area")
    manifest_before = manifest.read_bytes()
    dossier_path = root / "test-area_dossier.json"
    dossier_before = dossier_path.read_bytes()
    walk_path = root / "test-area_walk.json"
    walk_before = walk_path.read_bytes()
    dossier, source_bytes, source_sha256 = (
        dossier_output.capture_producer_dossier(root, "test-area")
    )
    new_walk = {
        "0": {"walk_m": 12, "conn": "", "trail": "Test Trail"}
    }
    dossier["facilities"][0]["walk"] = copy.deepcopy(new_walk["0"])
    real_replace = dossier_output.trusted_fs.os.replace

    def fail_second_replace(source_name, target_name, **kwargs):
        if target_name == dossier_path.name:
            raise OSError("simulated second replacement failure")
        return real_replace(source_name, target_name, **kwargs)

    monkeypatch.setattr(
        dossier_output.trusted_fs.os, "replace", fail_second_replace
    )
    with pytest.raises(OSError, match="second replacement failure"):
        dossier_output.write_walk_outputs(
            root, "test-area",
            source_dossier_bytes=source_bytes,
            source_dossier_sha256=source_sha256,
            dossier=dossier,
            walk=new_walk,
        )

    assert manifest.read_bytes() == manifest_before
    assert dossier_path.read_bytes() == dossier_before
    assert walk_path.read_bytes() != walk_before
    assert not list(root.glob(".*.tmp-*"))
    with pytest.raises(ValueError, match="walk artifact"):
        dossier_output.verify_generation(root, "test-area")


_DOSSIER_PRODUCER = r'''import json, os, sys
root = os.environ["PADJ_TMP"]
slug = sys.argv[1]
dossier = {
    "slug": slug,
    "bbox": [-105.6, 39.9, -105.4, 40.1],
    "facilities": [{"fid": 0, "lat": 40.0, "lon": -105.5}],
}
with open(os.path.join(root, f"{slug}_dossier.json"), "w") as handle:
    json.dump(dossier, handle)
with open(os.path.join(root, f"{slug}_serves2.json"), "w") as handle:
    json.dump({"0": {"served": False}}, handle)
print("other dossier output" if os.environ.get("DOSSIER_NO_STATUS") else "context: fake complete")
sys.exit(int(os.environ.get("DOSSIER_EXIT", "0")))
'''

_FOOT_PRODUCER = r'''import json, os, sys
root = os.environ["PADJ_TMP"]
slug = sys.argv[1]
dossier_path = os.path.join(root, f"{slug}_dossier.json")
with open(dossier_path) as handle:
    dossier = json.load(handle)
walk = {"0": {"walk_m": None, "conn": "no route", "trail": None}}
dossier["facilities"][0]["walk"] = walk["0"]
with open(os.path.join(root, f"{slug}_walk.json"), "w") as handle:
    json.dump(walk, handle)
with open(dossier_path, "w") as handle:
    json.dump(dossier, handle)
print("walk joined into dossier: 0/1 routed")
'''

_CONTEXT_PRODUCER = r'''import json, os, pathlib, sys
root = pathlib.Path(os.environ["PADJ_TMP"])
slug = sys.argv[1]
context_path = root / f"{slug}_context.json"
context = {"0": {"category": "NEUTRAL"}}
if not os.environ.get("MISSING_CONTEXT"):
    if os.environ.get("SYMLINK_CONTEXT"):
        target = root / f"{slug}_context-target.json"
        target.write_text(json.dumps(context))
        context_path.symlink_to(target.name)
    else:
        context_path.write_text(json.dumps(context))
if os.environ.get("STALE_PARENT"):
    tools = pathlib.Path(os.environ["PADJ_MANIFEST_TOOL"]).parent
    sys.path.insert(0, str(tools))
    import dossier_output
    dossier_output.commit_generation(
        root, slug, os.environ["INITIAL_PARENT"]
    )
    context["0"]["race"] = "after concurrent commit"
    context_path.write_text(json.dumps(context))
print(f"{slug}: {{'NEUTRAL': 1}}")
'''


def _driver_case(tmp_path: Path, *, initial=False, **behavior):
    root = tmp_path / "driver-work"
    root.mkdir(mode=0o700)
    ctx = tmp_path / "ctx"
    ctx.mkdir(mode=0o700)
    slug = "test-area"
    (ctx / f"{slug}_ctx.osm.pbf").write_bytes(b"offline fixture")
    fake_tools = tmp_path / "fake-tools"
    fake_tools.mkdir(mode=0o700)
    dossier_tool = fake_tools / "dossier.py"
    foot_tool = fake_tools / "foot.py"
    context_tool = fake_tools / "context.py"
    dossier_tool.write_text(_DOSSIER_PRODUCER)
    foot_tool.write_text(_FOOT_PRODUCER)
    context_tool.write_text(_CONTEXT_PRODUCER)

    initial_parent = None
    if initial:
        _documents, capture = _write_quartet(root, slug)
        initial_parent = capture["source_generation"]["manifest_sha256"]

    env = os.environ.copy()
    env.update({
        "PADJ_TMP": str(root),
        "PADJ_CTX": str(ctx),
        "PADJ_PYTHON": sys.executable,
        "PADJ_DOSSIER_TOOL": str(dossier_tool),
        "PADJ_FOOT_TOOL": str(foot_tool),
        "PADJ_CONTEXT_TOOL": str(context_tool),
        "PADJ_MANIFEST_TOOL": str(TOOLS / "dossier_output.py"),
    })
    if initial_parent is not None:
        env["INITIAL_PARENT"] = initial_parent
    env.update({key: str(value) for key, value in behavior.items()})
    result = subprocess.run(
        ["/bin/bash", str(TOOLS / "run_co.sh"), slug],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    return root, slug, result


def test_run_co_preserves_nonzero_producer_status_after_expected_text(tmp_path):
    _root, _slug, result = _driver_case(tmp_path, DOSSIER_EXIT=9)
    assert result.returncode == 9
    assert "context: fake complete" in result.stdout
    assert "DONE generation=" not in result.stdout


def test_run_co_requires_matching_producer_status_line(tmp_path):
    _root, _slug, result = _driver_case(tmp_path, DOSSIER_NO_STATUS=1)
    assert result.returncode != 0
    assert "DONE generation=" not in result.stdout


@pytest.mark.parametrize("behavior", [
    {"MISSING_CONTEXT": 1},
    {"SYMLINK_CONTEXT": 1},
])
def test_run_co_rejects_missing_or_linked_quartet_member(tmp_path, behavior):
    _root, _slug, result = _driver_case(tmp_path, **behavior)
    assert result.returncode != 0
    assert "DONE generation=" not in result.stdout


def test_run_co_rejects_stale_parent_after_concurrent_generation(tmp_path):
    _root, _slug, result = _driver_case(
        tmp_path, initial=True, STALE_PARENT=1
    )
    assert result.returncode != 0
    assert "stale parent_manifest_sha256" in result.stderr
    assert "DONE generation=" not in result.stdout


def test_run_co_success_prints_exact_verified_generation_hash(tmp_path):
    root, slug, result = _driver_case(tmp_path)
    assert result.returncode == 0, result.stderr
    matches = re.findall(
        r"^DONE generation=([0-9a-f]{64})$", result.stdout,
        flags=re.MULTILINE,
    )
    assert len(matches) == 1
    manifest_path = dossier_output.generation_manifest_path(root, slug)
    exact_sha256 = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    verified = dossier_output.verify_generation(root, slug)
    assert matches[0] == exact_sha256
    assert matches[0] == verified["source_generation"]["manifest_sha256"]
