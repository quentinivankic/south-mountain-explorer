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


SHARED = ("index.json", "trail-search.json", "trail-shapes.json")
BASE_FILE = {"index.json": "index.json", "trail-search.json": "search.json",
             "trail-shapes.json": "shapes.json"}


def _rollback(tmp_path, capsys, state):
    """state: key -> 'cand' | 'base' | 'other'. Returns (exit code, {key: action}, stderr)."""
    repo, live, out = _repo(tmp_path, [OLD_ROW, ROW]), _live(tmp_path), tmp_path / "out"
    assert _run(repo, live, out) == 0
    capsys.readouterr()
    cur = tmp_path / "cur"
    cur.mkdir()
    for key, what in state.items():
        if what == "cand":
            data = (out / key).read_bytes()
        elif what == "base":
            data = (live / BASE_FILE[key]).read_bytes()
        else:
            data = b'["third-party"]'
        (cur / key).write_bytes(data)
    code = smp.main(["rollback-decide", "--plan", str(out / "plan.json"), "--live-dir", str(cur)])
    cap = capsys.readouterr()
    actions = dict(line.split("\t") for line in cap.out.splitlines() if "\t" in line)
    return code, actions, cap.err


def test_rollback_fully_applied_restores_all_three(tmp_path, capsys):
    code, actions, _ = _rollback(tmp_path, capsys, {k: "cand" for k in SHARED})
    assert code == 0 and actions == {k: "restore" for k in SHARED}


def test_rollback_partial_apply_restores_only_candidate_files(tmp_path, capsys):
    state = {"index.json": "base", "trail-search.json": "base", "trail-shapes.json": "cand"}
    code, actions, _ = _rollback(tmp_path, capsys, state)
    assert code == 0
    assert actions == {"index.json": "skip", "trail-search.json": "skip", "trail-shapes.json": "restore"}


def test_rollback_nothing_applied_skips_all(tmp_path, capsys):
    code, actions, _ = _rollback(tmp_path, capsys, {k: "base" for k in SHARED})
    assert code == 0 and actions == {k: "skip" for k in SHARED}


@pytest.mark.parametrize("bad", SHARED)
def test_rollback_refuses_third_party_modified_file(tmp_path, capsys, bad):
    state = {"index.json": "cand", "trail-search.json": "base", "trail-shapes.json": "cand"}
    state[bad] = "other"
    code, actions, err = _rollback(tmp_path, capsys, state)
    assert code == 1 and actions == {} and "refusing" in err


def test_workflow_rollback_and_partial_apply_wiring():
    sync = (ROOT / ".github/workflows/sync-mols-to-r2.yml").read_text()
    assert "rollback-decide" in sync
    assert "PARTIAL APPLY: dispatch mode=rollback with rollback_run_id=$GITHUB_RUN_ID" in sync
    assert "trap - EXIT" in sync


def _geom(names, total_mi):
    return {"id": AREA, "trail_count": len(names), "total_mi": total_mi,
            "trails": [{"name": n} for n in names]}


def _assert_exact(tmp_path, row):
    path = tmp_path / "geom.json"
    path.write_text(json.dumps(row), encoding="utf-8")
    return smp.main(["assert-exact", "--geom", str(path)])


def _good_names():
    return ["Molsruten", "Kaløstien"] + [f"Trail {i}" for i in range(24)]


def test_assert_exact_accepts_only_the_reviewed_result(tmp_path):
    assert _assert_exact(tmp_path, _geom(_good_names(), 85.4)) == 0
    assert _assert_exact(tmp_path, _geom(_good_names(), 85.44)) == 0
    assert _assert_exact(tmp_path, _geom(_good_names(), 85.5)) == 1
    assert _assert_exact(tmp_path, _geom(_good_names()[:-1], 85.4)) == 1
    assert _assert_exact(tmp_path, _geom(_good_names() + ["Extra"], 85.4)) == 1
    missing = [n for n in _good_names() if n != "Kaløstien"] + ["Other"]
    assert _assert_exact(tmp_path, _geom(missing, 85.4)) == 1
    bad = _good_names()[:-1] + ["Gravlevstien"]
    assert _assert_exact(tmp_path, _geom(bad, 85.4)) == 1


def test_assert_exact_rejects_the_66_trail_statewide_result(tmp_path):
    # Shape of the generic statewide result: 66 trails / 127.2 mi, junk present.
    names = ["Enhøjvej", "Lyngevej"] + [f"Trail {i}" for i in range(64)]
    assert _assert_exact(tmp_path, _geom(names, 127.2)) == 1


def test_non_dry_mols_simple_path_uses_the_exact_assembly_and_publish_flags():
    publish = (ROOT / ".github/workflows/trailforge-publish.yml").read_text()
    job = publish.split("\n  pilot:\n")[0]
    exact = job.split("- name: Exact Mols assembly", 1)[1].split("- name: Assemble (statewide", 1)[0]
    for needle in ("--only-area \"$AREA_NAME\"", "--require-exact-area",
                   "--expected-area-relation-id \"$EXPECTED_RELATION_ID\"",
                   "--relation-authority", "--prefilter-authority",
                   "--aoi-relation-members", "validate_publish_pilot.py discovery",
                   'EXPECTED_RELATION_ID: "7046785"'):
        assert needle in exact, needle
    generic = job.split("- name: Assemble (statewide", 1)[1]
    assert "if: ${{ steps.input_shape.outputs.mols_simple != 'true' }}" in generic.split("env:")[0]
    publish_step = generic.split("- name: Publish areas", 1)[1].split("- name: Refuse unless", 1)[0]
    assert 'if [ "$MOLS_SIMPLE" = "true" ]; then' in publish_step
    for needle in ("--no-boundary-fetch", "--no-elevation",
                   "--exact-boundary data/aoi/mols-bjerge.exact-area.geojson",
                   "data/aoi/mols-bjerge.trails.geojson", "data/aoi/mols-bjerge.osm.pbf"):
        assert needle in publish_step, needle
    # Fail-closed assertion runs after publish and before the index merge / commit.
    order = [job.index(m) for m in ("- name: Publish areas", "simple_mols_publish.py assert-exact",
                                    "- name: Merge signed Mols row", "- name: Commit published data")]
    assert order == sorted(order)
