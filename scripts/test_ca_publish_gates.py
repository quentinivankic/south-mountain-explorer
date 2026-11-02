#!/usr/bin/env python3
"""Tests for ca_publish.py gates: the 2N+2 commit allowlist, merge-index, the cross-area duplicate
gate and the qualify report."""
import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ca_publish as ca  # noqa: E402
from test_ca_publish import _entry  # noqa: E402
from test_ca_publish_sync import OLD_ROW, _compact, _default, _make_repo  # noqa: E402


def _git(repo, *argv):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
    return subprocess.run(["git", *argv], cwd=repo, env=env, check=True, capture_output=True)


def _baseline_repo(tmp_path, n):
    """Git repo at the pre-publish state (OLD_ROW only) + the n areas' files kept aside."""
    repo, live, ids = _make_repo(tmp_path, n)
    new = tmp_path / "new"
    new.mkdir()
    bundle_path = repo / "ios/SouthMountainExplorer/Resources/areas-index.json"
    rows = json.loads(bundle_path.read_bytes())
    for aid in ids:
        for rel in (f"public/areas/geom/{aid}.json", f"public/areas/silhouettes/{aid}.json"):
            (new / f"{Path(rel).parent.name}-{Path(rel).name}").write_bytes((repo / rel).read_bytes())
            (repo / rel).unlink()
    bundle_path.write_bytes(_compact([OLD_ROW]))
    (repo / "public/areas/index.json").write_bytes(_default([OLD_ROW]))
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "baseline")
    return repo, ids, rows, new


def _apply_publish(repo, ids, rows, new, *, stage=True):
    for aid in ids:
        (repo / f"public/areas/geom/{aid}.json").write_bytes((new / f"geom-{aid}.json").read_bytes())
        (repo / f"public/areas/silhouettes/{aid}.json").write_bytes((new / f"silhouettes-{aid}.json").read_bytes())
    (repo / "public/areas/index.json").write_bytes(_default(rows))
    (repo / "ios/SouthMountainExplorer/Resources/areas-index.json").write_bytes(_compact(rows))
    if stage:
        _git(repo, "add", "public/areas", "ios/SouthMountainExplorer/Resources/areas-index.json")


def _verify(repo, ids, cmd="verify-staged"):
    return ca.main([cmd, "--repo", str(repo), "--areas", ",".join(ids)])


@pytest.mark.parametrize("n", [1, 3, 40])
def test_commit_allowlist_is_exactly_2n_plus_2(tmp_path, n):
    repo, ids, rows, new = _baseline_repo(tmp_path, n)
    _apply_publish(repo, ids, rows, new)
    assert len(ca.area_paths(ids)) == 2 * n + 2
    assert len(_git(repo, "diff", "--cached", "--name-only").stdout.decode().split()) == 2 * n + 2
    assert _verify(repo, ids) == 0
    _git(repo, "commit", "-q", "-m", "publish")
    assert _verify(repo, ids, "verify-commit") == 0


def test_staged_diff_refuses_extra_missing_modified_reordered_and_miscounted(tmp_path):
    repo, ids, rows, new = _baseline_repo(tmp_path, 3)
    _apply_publish(repo, ids, rows, new)
    (repo / "public/areas/geom/other-ca-ns.json").write_text("{}", encoding="utf-8")
    _git(repo, "add", "public/areas/geom/other-ca-ns.json")
    assert _verify(repo, ids) == 1                                  # extra path
    _git(repo, "rm", "-q", "-f", "public/areas/geom/other-ca-ns.json")
    assert _verify(repo, ids) == 0
    _git(repo, "rm", "-q", "--cached", f"public/areas/silhouettes/{ids[0]}.json")
    assert _verify(repo, ids) == 1                                  # missing path
    _git(repo, "add", f"public/areas/silhouettes/{ids[0]}.json")
    bad = copy.deepcopy(rows)
    bad[0][1] = "Changed live row"
    _apply_publish(repo, ids, bad, new)
    assert _verify(repo, ids) == 1                                  # an existing (US) row modified
    _apply_publish(repo, ids, [rows[0], rows[2], rows[1], rows[3]], new)
    assert _verify(repo, ids) == 1                                  # new rows not sorted
    wrong = copy.deepcopy(rows)
    wrong[1][5] = 99                                                # count differs from geom
    _apply_publish(repo, ids, wrong, new)
    assert _verify(repo, ids) == 1
    _apply_publish(repo, ids, rows, new)
    assert _verify(repo, ids) == 0
    (repo / "public/areas/index.json").write_bytes(json.dumps(rows, indent=1).encode("utf-8"))
    _git(repo, "add", "public/areas/index.json")
    assert _verify(repo, ids) == 1                                  # framing differs from producer


def test_verify_cli_is_publish_only(tmp_path):
    repo, ids, rows, new = _baseline_repo(tmp_path, 1)
    with pytest.raises(SystemExit):
        ca.main(["verify-staged", "--repo", str(repo), "--areas", ids[0], "--mode", "update"])


def test_merge_index_appends_sorted_rows_and_keeps_existing_bytes(tmp_path):
    repo, ids, rows, new = _baseline_repo(tmp_path, 3)
    work = tmp_path / "work"
    reg = repo / "trailforge/golden/ca-areas.json"
    for aid, row in zip(ids, rows[1:]):
        (work / aid).mkdir(parents=True)
        (work / aid / "index.json").write_text(json.dumps([row]), encoding="utf-8")
        (repo / f"public/areas/geom/{aid}.json").write_bytes((new / f"geom-{aid}.json").read_bytes())
    sel = tmp_path / "sel.txt"
    sel.write_text("\n".join(reversed(ids)) + "\n", encoding="utf-8")          # input order must not matter
    target = repo / "public/areas/index.json"
    before = target.read_bytes()
    argv = ["merge-index", "--registry", str(reg), "--selected", str(sel), "--rows-dir", str(work),
            "--geom-dir", str(repo / "public/areas/geom"), "--target", str(target)]
    assert ca.main(argv) == 0
    after = target.read_bytes()
    assert after.startswith(before[:-1] + b", ") and json.loads(after)[1:] == rows[1:]
    assert ca.main(argv) == 1                                                   # already in the master index


# ------------------------------------------------ cross-area duplicate gate ----
def _line(lat0, lon0, n=80, step=0.001):
    return [[round(lat0 + i * step, 5), round(lon0 + i * step / 2, 5)] for i in range(n)]


def _geom_with_line(aid, name, segment):
    return {"id": aid, "name": aid, "trail_count": 1, "total_mi": 1.0,
            "trails": [{"id": "t0", "name": name, "distanceMi": 1.0, "difficulty": "Easy", "segments": [segment]}]}


def _index_file(tmp_path, rows):
    path = tmp_path / "live-index.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    return path


def _dup_report(tmp_path, aid, trails, *, live_dir=None, live_rows=(), center=(45.2, -62.8)):
    """Write one area's report-area inputs and run the real `report-area` CLI."""
    d = tmp_path / aid
    d.mkdir()
    (d / "area.json").write_text(json.dumps({"id": aid, "name": aid, "province": "ns", "relation_id": 1,
                                             "center": list(center)}), encoding="utf-8")
    geom = {"id": aid, "name": aid, "trail_count": len(trails), "total_mi": float(len(trails)),
            "trails": [{"id": f"t{i}", "name": n, "distanceMi": 1.0, "difficulty": "Easy", "segments": [seg]}
                       for i, (n, seg) in enumerate(trails.items())]}
    (d / "geom.json").write_text(json.dumps(geom), encoding="utf-8")
    (d / "trails.geojson").write_text(json.dumps({"features": [
        {"type": "Feature", "geometry": None, "properties": {"name": n, "kind": "trail", "length_mi": 1.0}}
        for n in trails]}), encoding="utf-8")
    (d / "removed.geojson").write_text(json.dumps({"features": []}), encoding="utf-8")
    (d / "assembly.json").write_text(json.dumps({"pre_clip_trail_count": 2, "post_clip_trail_count": 1}), encoding="utf-8")
    argv = ["report-area", "--area-json", str(d / "area.json"), "--geom", str(d / "geom.json"),
            "--trails-geojson", str(d / "trails.geojson"), "--removed-geojson", str(d / "removed.geojson"),
            "--assembly-json", str(d / "assembly.json"), "--out", str(d / "report.json"),
            "--live-geom-dir", str(live_dir or tmp_path / "no-live"),
            "--live-index", str(_index_file(tmp_path, list(live_rows)))]
    assert ca.main(argv) == 0


def _aggregate(tmp_path, capsys):
    out = tmp_path / "q"
    reg = tmp_path / "reg.json"
    reg.write_text(json.dumps({"schema": ca.REGISTRY_SCHEMA, "extract_base": ca.EXTRACT_BASE, "state": "Canada",
                               "areas": [_entry(1)]}), encoding="utf-8")
    rc = ca.main(["qualify-aggregate", "--reports-dir", str(tmp_path), "--out-dir", str(out), "--registry", str(reg)])
    return rc, capsys.readouterr().err, out


def test_identical_geometry_in_two_selected_areas_fails_closed_even_with_different_names(tmp_path, capsys):
    _dup_report(tmp_path, "a-ca-ns", {"Rute Et": _line(45.2, -62.6)})
    _dup_report(tmp_path, "b-ca-ns", {"Rute To": list(reversed(_line(45.2, -62.6)))})
    rc, err, out = _aggregate(tmp_path, capsys)
    assert rc == 1 and "a-ca-ns 'Rute Et' duplicates area b-ca-ns 'Rute To' (identical geometry)" in err
    conflicts = json.loads((out / "duplicate-conflicts.json").read_text())["conflicts"]
    assert [(c["area"], c["other_area"], c["other_is_live"]) for c in conflicts] == [("a-ca-ns", "b-ca-ns", False)]
    assert "## Cross-area duplicates" in (out / "qualify-report.md").read_text()


def test_same_name_over_the_same_ground_fails_but_same_name_elsewhere_passes(tmp_path, capsys):
    _dup_report(tmp_path, "a-ca-ns", {"Lakeshore": _line(45.2, -62.6), "Ridge": _line(45.3, -62.7)})
    _dup_report(tmp_path, "b-ca-ns", {"Lakeshore": _line(45.2, -62.6)[:50] + _line(46.0, -61.0, 30),
                                      "Ridge": _line(45.9, -61.5)})
    rc, err, _ = _aggregate(tmp_path, capsys)
    assert rc == 1 and "'Lakeshore' duplicates area b-ca-ns 'Lakeshore' (same name over the same ground)" in err
    assert "'Ridge'" not in err


def test_different_names_over_overlapping_ground_are_not_duplicates(tmp_path, capsys):
    _dup_report(tmp_path, "a-ca-ns", {"Rute Et": _line(45.2, -62.6)})
    _dup_report(tmp_path, "b-ca-ns", {"Rute To": _line(45.2, -62.6)[:50] + _line(46.0, -61.0, 30)})
    rc, err, out = _aggregate(tmp_path, capsys)
    assert rc == 0 and err == ""
    assert json.loads((out / "duplicate-conflicts.json").read_text()) == {"conflicts": []}


def test_selected_area_duplicating_a_nearby_live_area_fails_closed_naming_it(tmp_path, capsys):
    live = tmp_path / "live"
    live.mkdir()
    (live / "old-park-me.json").write_text(json.dumps(_geom_with_line("old-park-me", "Kingfisher", _line(45.2, -62.6))))
    (live / "far-park-wa.json").write_text(json.dumps(_geom_with_line("far-park-wa", "Kingfisher", _line(45.2, -62.6))))
    (live / "new-ca-ns.json").write_text(json.dumps(_geom_with_line("new-ca-ns", "Selfcopy", _line(45.2, -62.6))))
    rows = [["old-park-me", "Old", "Maine", 45.0, -67.5, 1, 1.0, 1],           # within 3 deg: compared
            ["far-park-wa", "Far", "Washington", 47.5, -121.0, 1, 1.0, 2],       # far away: never read
            ["new-ca-ns", "New", "Canada", 45.2, -62.8, 1, 1.0, 3]]               # itself: skipped
    assert ca.nearby_live_ids(_index_file(tmp_path, rows), "new-ca-ns", [45.2, -62.8]) == []
    rows[0][4] = -60.5
    assert ca.nearby_live_ids(_index_file(tmp_path, rows), "new-ca-ns", [45.2, -62.8]) == ["old-park-me"]
    _dup_report(tmp_path, "new-ca-ns", {"Kingfisher": _line(45.2, -62.6)}, live_dir=live, live_rows=rows)
    rep = json.loads((tmp_path / "new-ca-ns" / "report.json").read_text())
    assert [(c["other_area"], c["other_trail"]) for c in rep["live_duplicates"]] == [("old-park-me", "Kingfisher")]
    rc, err, _ = _aggregate(tmp_path, capsys)
    assert rc == 1 and "new-ca-ns 'Kingfisher' duplicates live area old-park-me 'Kingfisher'" in err


def test_every_live_canadian_area_is_compared_wherever_it_is(tmp_path):
    rows = [["a-ca-bc", "A", "Canada", 50.0, -120.0, 1, 1.0, 1], ["b-ca-ns", "B", "Canada", 45.0, -62.0, 1, 1.0, 2]]
    assert ca.nearby_live_ids(_index_file(tmp_path, rows), "c-ca-pe", [46.4, -63.4]) == ["a-ca-bc", "b-ca-ns"]


def test_report_area_lists_removed_categories_curated_exclusions_and_forbidden_names(tmp_path):
    aid = "x-ca-ns"
    d = tmp_path / aid
    d.mkdir()
    (d / "area.json").write_text(json.dumps({"id": aid, "name": "X", "province": "ns", "relation_id": 1,
                                             "center": [45.2, -62.8]}), encoding="utf-8")
    (d / "geom.json").write_text(json.dumps({"id": aid, "name": "X", "trail_count": 2, "total_mi": 5.0, "trails": [
        {"id": "a", "name": "Ridge Trail", "segments": [[[45.2, -62.8], [45.21, -62.79]]]},
        {"id": "b", "name": "Lake Loop", "segments": [[[45.3, -62.7], [45.31, -62.69]]]}]}), encoding="utf-8")
    (d / "trails.geojson").write_text(json.dumps({"features": []}), encoding="utf-8")
    (d / "removed.geojson").write_text(json.dumps({"features": [
        {"type": "Feature", "geometry": None, "properties": {"name": "Trans Canada Trail", "length_mi": 9.0,
                                                             "removed_category": "ca-long-distance"}},
        {"type": "Feature", "geometry": None, "properties": {"name": "Spur", "length_mi": 1.0,
                                                             "removed_category": "ca-curated-exclude",
                                                             "curated_exclude_id": "n:Spur",
                                                             "curated_exclude_reason": "duplicate of Ridge"}},
        {"type": "Feature", "geometry": None, "properties": {"name": "Noise", "length_mi": 0.1,
                                                             "removed_category": "min-length"}}]}), encoding="utf-8")
    (d / "assembly.json").write_text(json.dumps({"pre_clip_trail_count": 5, "post_clip_trail_count": 3}), encoding="utf-8")
    assert ca.main(["report-area", "--area-json", str(d / "area.json"), "--geom", str(d / "geom.json"),
                    "--trails-geojson", str(d / "trails.geojson"), "--removed-geojson", str(d / "removed.geojson"),
                    "--assembly-json", str(d / "assembly.json"), "--out", str(d / "report.json"),
                    "--live-geom-dir", str(tmp_path / "none"), "--live-index", str(_index_file(tmp_path, []))]) == 0
    rec = json.loads((d / "report.json").read_text())
    assert rec["removed_category_counts"] == {"ca-curated-exclude": 1, "ca-long-distance": 1, "min-length": 1}
    assert rec["curated_excluded"] == [{"id": "n:Spur", "name": "Spur", "length_mi": 1.0, "reason": "duplicate of Ridge"}]
    assert rec["proposed_expected"]["forbidden_names"] == ["Spur", "Trans Canada Trail"]
    assert rec["proposed_expected"]["trail_count"] == 2 and rec["province"] == "ns"
