#!/usr/bin/env python3
"""Tests for the simple (unsigned) Mols publish helper and its workflow wiring."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import r2_release_contract as contract  # noqa: E402
import simple_mols_publish as smp  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
AREA = contract.MOLS_AREA_ID
ROW = [AREA, contract.MOLS_AREA_NAME, "Denmark", 56.2, 10.6, 1, 2.5, 7046785]
OLD_ROW = ["old-area-co", "Old Area", "Colorado", 39.0, -105.0, 1, 1.0, None]


def _dumps(value):
    return json.dumps(value, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _repo(tmp_path, bundle_rows):
    repo = tmp_path / "repo"
    for rel in contract.MOLS_REPOSITORY_PATHS:
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
    geom = {"id": AREA, "trails": [
        {"id": "t1", "name": "Molsruten", "distanceMi": 1.5, "difficulty": "Hard",
         "segments": [[[56.2, 10.6], [56.21, 10.62], [56.22, 10.6]]]}]}
    (repo / contract.MOLS_REPOSITORY_PATHS[0]).write_bytes(_dumps(geom))
    (repo / contract.MOLS_REPOSITORY_PATHS[1]).write_bytes(_dumps({"b": [0, 0, 1, 1], "l": [[1, 2]]}))
    (repo / contract.MOLS_REPOSITORY_PATHS[3]).write_bytes(_dumps(bundle_rows))
    return repo


def _live(tmp_path):
    live = tmp_path / "live"
    live.mkdir()
    (live / "index.json").write_bytes(_dumps([OLD_ROW]))
    (live / "search.json").write_bytes(_dumps([["Old Trail", "old-area-co", "a", 1.0, "Easy"]]))
    (live / "shapes.json").write_bytes(_dumps({"old-area-co/a": [0, 0, 255, 255]}))
    return live


def _run(repo, live, out):
    return smp.main([
        "build-candidates", "--repo", str(repo),
        "--live-index", str(live / "index.json"), "--live-search", str(live / "search.json"),
        "--live-shapes", str(live / "shapes.json"),
        "--etag-index", "a" * 32, "--etag-search", "b" * 32, "--etag-shapes", "c" * 32 + "-2",
        "--out-dir", str(out)])


def test_candidates_preserve_live_bytes_and_write_exactly_five_keys(tmp_path):
    repo, live, out = _repo(tmp_path, [OLD_ROW, ROW]), _live(tmp_path), tmp_path / "out"
    assert _run(repo, live, out) == 0
    plan = json.loads((out / "plan.json").read_text())
    assert [w["key"] for w in plan["writes"]] == [
        f"{AREA}.json", f"silhouettes/{AREA}.json", "index.json",
        "trail-search.json", "trail-shapes.json"]
    assert not any("parking" in w["key"] for w in plan["writes"])
    for name, closer in (("search.json", b"]"), ("shapes.json", b"}")):
        old = (live / name).read_bytes()
        new = (out / ("trail-" + name)).read_bytes()
        assert new.startswith(old[:-1] + b",") and new.endswith(closer)
    assert json.loads((out / "trail-search.json").read_bytes())[-1][1] == AREA
    assert f"{AREA}/t1" in json.loads((out / "trail-shapes.json").read_bytes())


def test_refuses_when_bundle_is_not_live_plus_mols_row(tmp_path):
    changed = ["old-area-co", "Renamed", "Colorado", 39.0, -105.0, 1, 1.0, None]
    repo, live = _repo(tmp_path, [changed, ROW]), _live(tmp_path)
    assert _run(repo, live, tmp_path / "out") == 1


def test_refuses_when_live_already_has_mols(tmp_path):
    repo, live = _repo(tmp_path, [OLD_ROW, ROW]), _live(tmp_path)
    (live / "index.json").write_bytes(_dumps([OLD_ROW, ROW]))
    assert _run(repo, live, tmp_path / "out") == 1


def test_prepare_input_accepts_only_fixed_identity(tmp_path):
    base = ["prepare-input", "--extract", contract.MOLS_EXTRACT, "--state", "Denmark",
            "--region-code", "dk", "--bbox", contract.MOLS_BBOX, "--dry-run", "false",
            "--no-routes", "false", "--touch-report", "false", "--elevation", "false",
            "--pilot-area-id", "", "--workflow-source-sha", "a" * 40,
            "--mols-index-output", str(tmp_path / "row.json"),
            "--github-output", str(tmp_path / "out.txt")]
    assert smp.main(base) == 0
    assert json.loads((tmp_path / "row.json").read_text())[0][0] == AREA
    assert "release_mode=mols" in (tmp_path / "out.txt").read_text()
    bad = list(base)
    bad[bad.index("--bbox") + 1] = "1,2,3,4"
    assert smp.main(bad) == 1


def test_workflow_wiring():
    sync = (ROOT / ".github/workflows/sync-mols-to-r2.yml").read_text()
    assert "workflow_dispatch:" in sync
    for trigger in ("push:", "schedule:", "pull_request:"):
        assert trigger not in sync
    assert "environment:" not in sync and "HMAC" not in sync
    assert "parking.json" in sync and "put cand/parking" not in sync
    publish = (ROOT / ".github/workflows/trailforge-publish.yml").read_text()
    assert "mols_simple_publish:" in publish
    assert "default: false" in publish.split("mols_simple_publish:")[1].split("concurrency:")[0]
    assert 'test "$RAW_MOLS_SIMPLE" = "false"' in publish  # receipt and simple are exclusive
