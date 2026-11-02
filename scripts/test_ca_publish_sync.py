#!/usr/bin/env python3
"""Tests for ca_publish.py: boundary/prepare checks, N-area R2 candidates, rollback, the 2N+2 commit
allowlist, merge-index, the duplicate gate and the report. Fixtures are built here; nothing reads the
live registry except where stated."""
import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ca_publish as ca  # noqa: E402
from test_ca_publish import _entry, _geom, _registry  # noqa: E402

OLD_ROW = ["old-area-co", "Old Area", "Colorado", 39.0, -105.0, 1, 1.0, None]
SHARED = ("index.json", "trail-search.json", "trail-shapes.json")


def _compact(value):
    return json.dumps(value, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _default(value):
    return json.dumps(value, ensure_ascii=True).encode("utf-8")


# ------------------------------------------------------- boundary / prepare ----
OSM = """<?xml version='1.0' encoding='UTF-8'?>
<osm version="0.6" generator="t">
  <node id="1" lat="{s}" lon="{w}"/><node id="2" lat="{s}" lon="{e}"/>
  <node id="3" lat="{n}" lon="{e}"/><node id="4" lat="{n}" lon="{w}"/>
  <way id="10"><nd ref="1"/><nd ref="2"/><nd ref="3"/><nd ref="4"/><nd ref="1"/>{wtags}</way>
  <relation id="{rel}"><member type="way" ref="10" role="outer"/><tag k="type" v="{rtype}"/>
    <tag k="boundary" v="protected_area"/><tag k="name" v="{name}"/></relation>
</osm>
"""
BOX = (-63.0, 45.0, -62.5, 45.5)


def _osm(tmp_path, rel=1000, box=BOX, name="Area 0", rtype="multipolygon", wtags=""):
    path = tmp_path / f"b{rel}.osm"
    path.write_text(OSM.format(rel=rel, w=box[0], s=box[1], e=box[2], n=box[3], name=name, rtype=rtype,
                               wtags=wtags), encoding="utf-8")
    return path


def test_boundary_bbox_from_pbf_relation(tmp_path):
    path = _osm(tmp_path)
    got = ca.boundary_info(path, [1000, 999])
    assert got[1000]["bbox"] == pytest.approx(list(BOX), abs=1e-6) and got[1000]["count"] == 1
    assert got[999]["bbox"] is None


def _prep(tmp_path, entry, **osm):
    reg = ca.load_registry(_registry(tmp_path, [entry]))
    entry = reg["areas"][0]
    info = ca.boundary_info(_osm(tmp_path, entry["relation_id"], **osm), [entry["relation_id"]])[entry["relation_id"]]
    return entry, info


def test_prepare_checks_identity_type_name_size_and_country(tmp_path):
    entry, info = _prep(tmp_path, _entry(0, bbox=None, center=None))
    rec = ca.check_area_geometry(entry, info["bbox"], "qualify", info)
    assert rec["status"] == "ok" and rec["cross_check"] == "registry-relation"
    assert rec["proposed_bbox"] == [-63.05, 44.95, -62.45, 45.55] and rec["proposed_center"] == [45.2, -62.8]
    entry, info = _prep(tmp_path, _entry(0, bbox=None, center=None), name="Other Park")
    assert "does not match registry name" in ca.check_area_geometry(entry, info["bbox"], "qualify", info)["problems"][0]
    entry, info = _prep(tmp_path, _entry(0, bbox=None, center=None), rtype="route")
    assert "expected boundary/multipolygon" in ca.check_area_geometry(entry, info["bbox"], "qualify", info)["problems"][0]
    entry, info = _prep(tmp_path, _entry(0, bbox=None, center=None), box=(-63.0, 45.0, -62.999, 45.001))
    assert "implausible" in ca.check_area_geometry(entry, info["bbox"], "qualify", info)["problems"][0]
    entry, info = _prep(tmp_path, _entry(0, bbox=None, center=None), box=(10.0, 56.0, 10.5, 56.5))
    assert any("outside Canada" in p for p in ca.check_area_geometry(entry, info["bbox"], "qualify", info)["problems"])
    entry, _ = _prep(tmp_path, _entry(0, bbox=None, center=None))
    rec = ca.check_area_geometry(entry, None, "qualify", None)
    assert rec["status"] == "failed" and "not found" in rec["problems"][0]


def test_publish_needs_a_registry_bbox_and_margins_and_centre_are_enforced(tmp_path):
    entry, info = _prep(tmp_path, _entry(0, bbox=None, center=None))
    assert "registry bbox/center missing" in ca.check_area_geometry(entry, info["bbox"], "publish", info)["problems"][0]
    good = _entry(0, bbox=[-63.05, 44.95, -62.45, 45.55], center=[45.2, -62.8])
    entry, info = _prep(tmp_path, good)
    assert ca.check_area_geometry(entry, info["bbox"], "publish", info)["status"] == "ok"
    for bbox, center, why in (([-63.0, 45.0, -62.5, 45.5], [45.2, -62.8], "margins"),
                              ([-63.5, 44.0, -62.0, 46.0], [45.2, -62.8], "margins"),
                              ([-63.05, 44.95, -62.45, 45.55], [47.0, -62.8], "outside the boundary bbox")):
        entry, info = _prep(tmp_path, _entry(0, bbox=bbox, center=center))
        problems = ca.check_area_geometry(entry, info["bbox"], "publish", info)["problems"]
        assert problems and why in problems[0]


def test_way_backed_area_needs_a_unique_closed_named_way(tmp_path):
    entry = _entry(0, bbox=None, center=None)
    del entry["relation_id"]
    entry["way_id"] = 10
    reg = ca.load_registry(_registry(tmp_path, [entry]))
    entry = reg["areas"][0]
    path = _osm(tmp_path, wtags='<tag k="name" v="Area 0"/><tag k="leisure" v="park"/>')
    info = ca.way_boundary_info(path, [10, 11])[10]
    rec = ca.check_area_geometry(entry, info["bbox"], "qualify", info)
    assert rec["status"] == "ok" and rec["cross_check"] == "registry-way"
    assert ca.way_boundary_info(path, [11])[11]["tags"] is None
    assert "not in the PBF" in ca.way_problems(entry, ca.way_boundary_info(path, [11])[11])[0]


def test_prepare_areas_cli_writes_a_one_row_index_with_the_display_name(tmp_path):
    entry = _entry(0, bbox=None, center=None, display_name="Area Zero")
    reg = _registry(tmp_path, [entry])
    sel = tmp_path / "sel.txt"
    sel.write_text("area-000-ca-ns\n", encoding="utf-8")
    out = tmp_path / "run"
    argv = ["prepare-areas", "--registry", str(reg), "--selected", str(sel), "--bounds-pbf",
            str(_osm(tmp_path)), "--mode", "qualify", "--out-dir", str(out)]
    assert ca.main(argv) == 0
    row = json.loads((out / "area-000-ca-ns" / "index.json").read_text())
    assert row == [["area-000-ca-ns", "Area Zero", "Canada", 45.2, -62.8, None, None, 1000]]
    assert json.loads((out / "area-000-ca-ns" / "area.json").read_text())["bbox_arg"] == "-63.05,44.95,-62.45,45.55"
    assert ca.main(argv[:-3] + ["publish", "--out-dir", str(out)]) == 1      # null registry bbox refuses publish


def test_unique_boundary_check_fails_closed_on_same_name_or_contained_name(tmp_path, capsys):
    reg = _registry(tmp_path, [_entry(0)])
    path = _osm(tmp_path)
    assert ca.boundary_name_matches(path, "Area 0") == [("relation", 1000)]
    assert ca.main(["check-boundary-unique", "--registry", str(reg), "--area-id", "area-000-ca-ns",
                    "--pbf", str(path)]) == 0
    two = path.read_text().replace("</osm>", '<relation id="2000"><member type="way" ref="10" role="outer"/>'
                                   '<tag k="type" v="boundary"/><tag k="name" v="Area 0"/></relation></osm>')
    path.write_text(two, encoding="utf-8")
    assert ca.main(["check-boundary-unique", "--registry", str(reg), "--area-id", "area-000-ca-ns",
                    "--pbf", str(path)]) == 1
    assert "found [('relation', 1000), ('relation', 2000)]" in capsys.readouterr().err
    assert ca.boundary_name_matches(_osm(tmp_path, name="Big Area 0 Reserve"), "Area 0") == []
    assert ca.boundary_name_matches(_osm(tmp_path, name="Big Area 0 Reserve"), "Area 0", contains=True) == [
        ("relation", 1000)]


# ----------------------------------------------------- publish-row helpers ----
def test_index_for_publish_and_finish_publish_keep_the_display_name_and_counts(tmp_path):
    d = tmp_path
    (d / "area.json").write_text(json.dumps({"id": "a-ca-ns", "name": "Area", "display_name": "Area Shown"}))
    row = ["a-ca-ns", "Area Shown", "Canada", 45.2, -62.8, None, None, 7]
    (d / "index.json").write_text(json.dumps([row]))
    assert ca.main(["index-for-publish", "--index", str(d / "index.json"), "--area-json", str(d / "area.json"),
                    "--out", str(d / "pub.json")]) == 0
    pub = json.loads((d / "pub.json").read_text())
    assert pub[0][1] == "Area"
    pub[0][5], pub[0][6] = 3, 4.5                                  # what publish_areas writes back
    (d / "pub.json").write_text(json.dumps(pub))
    geom = {"id": "a-ca-ns", "name": "Area", "trail_count": 3, "total_mi": 4.5, "trails": []}
    (d / "geom.json").write_text(json.dumps(geom))
    assert ca.main(["finish-publish", "--index", str(d / "index.json"), "--publish-index", str(d / "pub.json"),
                    "--geom", str(d / "geom.json"), "--area-json", str(d / "area.json")]) == 0
    assert json.loads((d / "index.json").read_text()) == [["a-ca-ns", "Area Shown", "Canada", 45.2, -62.8, 3, 4.5, 7]]
    assert json.loads((d / "geom.json").read_text())["name"] == "Area Shown"
    pub[0][3] = 1.0                                                # identity drift is refused
    (d / "pub.json").write_text(json.dumps(pub))
    assert ca.main(["finish-publish", "--index", str(d / "index.json"), "--publish-index", str(d / "pub.json"),
                    "--geom", str(d / "geom.json"), "--area-json", str(d / "area.json")]) == 1


def test_check_new_row_expects_identity_centre_counts_and_trail_proximity():
    entry = _entry(0, display_name="Shown")
    geom = _geom("area-000-ca-ns")
    good = ["area-000-ca-ns", "Shown", "Canada", 45.2, -62.8, 2, 3.0, 1000]
    ca.check_new_row(good, entry, geom, "t")
    for bad in (["area-000-ca-ns", "Area 0", "Canada", 45.2, -62.8, 2, 3.0, 1000],     # not the display name
                ["area-000-ca-ns", "Shown", "Denmark", 45.2, -62.8, 2, 3.0, 1000],
                ["area-000-ca-ns", "Shown", "Canada", 45.3, -62.8, 2, 3.0, 1000],
                ["area-000-ca-ns", "Shown", "Canada", 45.2, -62.8, 3, 3.0, 1000],
                ["area-000-ca-ns", "Shown", "Canada", 45.2, -62.8, 2, 3.0]):         # missing relation id
        with pytest.raises(ca.CaError):
            ca.check_new_row(bad, entry, geom, "t")
    far = _entry(0, display_name="Shown", center=[46.9, -62.8])
    with pytest.raises(ca.CaError, match="0.1 deg"):
        ca.check_new_row(["area-000-ca-ns", "Shown", "Canada", 46.9, -62.8, 2, 3.0, 1000], far, geom, "t")
    way = {k: v for k, v in entry.items() if k != "relation_id"} | {"way_id": 9}
    ca.check_new_row(["area-000-ca-ns", "Shown", "Canada", 45.2, -62.8, 2, 3.0], way, geom, "t")


# -------------------------------------------------- N-area splice / sync ----
def _make_repo(tmp_path, n):
    """Fixture repo with n new Canadian areas + live R2 bytes. Returns (repo, live_dir, ids)."""
    entries = [_entry(i, display_name=None) for i in range(n)]
    for e in entries:
        del e["display_name"]
    repo, live = tmp_path / "repo", tmp_path / "live"
    for d in (repo / "trailforge/golden", repo / "public/areas/geom", repo / "public/areas/silhouettes",
              repo / "ios/SouthMountainExplorer/Resources", live):
        d.mkdir(parents=True, exist_ok=True)
    (repo / "trailforge/golden/ca-areas.json").write_text(
        _registry(tmp_path, entries).read_text(encoding="utf-8"), encoding="utf-8")
    ids = sorted(e["id"] for e in entries)
    expected = {"schema": ca.EXPECTED_SCHEMA, "areas": {}}
    rows = []
    for e in sorted(entries, key=lambda x: x["id"]):
        aid = e["id"]
        (repo / f"public/areas/geom/{aid}.json").write_bytes(_compact(_geom(aid)))
        (repo / f"public/areas/silhouettes/{aid}.json").write_bytes(_compact({"b": [0, 0, 1, 1], "l": [[1, 2]]}))
        rows.append([aid, e["name"], "Canada", 45.2, -62.8, 2, 3.0, e["relation_id"]])
        expected["areas"][aid] = {"trail_count": 2, "total_mi": 3.0, "total_mi_tolerance": 0.05,
                                  "required_names": [f"{aid} Trail 0"], "forbidden_names": []}
    (repo / "trailforge/golden/ca-areas-expected.json").write_text(json.dumps(expected), encoding="utf-8")
    (repo / "ios/SouthMountainExplorer/Resources/areas-index.json").write_bytes(_compact([OLD_ROW, *rows]))
    (live / "index.json").write_bytes(_compact([OLD_ROW]))
    (live / "trail-search.json").write_bytes(_compact([["Old Trail", "old-area-co", "a", 1.0, "Easy"]]))
    (live / "trail-shapes.json").write_bytes(_compact({"old-area-co/a": [0, 0, 255, 255]}))
    (live / "parking.json").write_bytes(b'{"parking":"untouched"}')
    return repo, live, ids


def _build(repo, live, out, extra=()):
    return ca.main(["build-candidates", "--repo", str(repo),
                    "--live-index", str(live / "index.json"), "--live-search", str(live / "trail-search.json"),
                    "--live-shapes", str(live / "trail-shapes.json"), "--out-dir", str(out), *extra])


@pytest.mark.parametrize("n", [1, 3, 40])
def test_splice_n_areas_onto_live_bytes(tmp_path, n):
    repo, live, ids = _make_repo(tmp_path, n)
    parking_before = (live / "parking.json").read_bytes()
    out = tmp_path / "out"
    assert _build(repo, live, out) == 0
    plan = json.loads((out / "plan.json").read_text())
    assert plan["areas"] == ids and plan["schema"] == "trekdex-ca-r2-plan-v1"
    keys = [w["key"] for w in plan["writes"]]
    assert len(keys) == 2 * n + 3
    assert keys[2 * n:] == ["trail-shapes.json", "trail-search.json", "index.json"]     # index last
    assert not any("parking" in k for k in keys)
    assert (live / "parking.json").read_bytes() == parking_before
    index = json.loads((out / "index.json").read_bytes())
    assert index[0] == OLD_ROW and [r[0] for r in index[1:]] == ids
    for w in plan["writes"]:
        assert ca._sha((out / w["file"]).read_bytes()) == w["sha256"]


def test_derive_areas_only_accepts_canada_registry_ids(tmp_path):
    repo, live, ids = _make_repo(tmp_path, 3)
    out = tmp_path / "areas.txt"
    assert ca.main(["derive-areas", "--repo", str(repo), "--live-index", str(live / "index.json"),
                    "--out", str(out)]) == 0
    assert out.read_text().split() == ids
    bundle = repo / "ios/SouthMountainExplorer/Resources/areas-index.json"
    rows = json.loads(bundle.read_bytes())
    for foreign in (["ghost-dk", "Ghost", "Denmark", 56.0, 9.0, 2, 3.0, 5],
                    ["ghost-ca", "Ghost", "California", 36.0, -120.0, 2, 3.0, 5],
                    ["zzz-ca-ns", "Ghost", "Canada", 45.0, -62.0, 2, 3.0, 5]):        # Canadian but not in registry
        bundle.write_bytes(_compact(rows + [foreign]))
        assert ca.main(["derive-areas", "--repo", str(repo), "--live-index", str(live / "index.json"),
                        "--out", str(out)]) == 1
    bundle.write_bytes(_compact(rows))
    out.write_text(ids[0] + "\n", encoding="utf-8")
    assert _build(repo, live, tmp_path / "o2", ["--areas-file", str(out)]) == 1


def test_refuses_existing_keys_unsorted_bundle_and_unblessed_areas(tmp_path):
    repo, live, ids = _make_repo(tmp_path, 3)
    (live / "trail-search.json").write_bytes(_compact(
        [["Old Trail", "old-area-co", "a", 1.0, "Easy"], ["X", ids[1], "t0", 1.5, "Easy"]]))
    assert _build(repo, live, tmp_path / "a") == 1
    (live / "trail-search.json").write_bytes(_compact([["Old Trail", "old-area-co", "a", 1.0, "Easy"]]))
    bundle = repo / "ios/SouthMountainExplorer/Resources/areas-index.json"
    rows = json.loads(bundle.read_bytes())
    bundle.write_bytes(_compact([rows[0], rows[2], rows[1], rows[3]]))
    assert _build(repo, live, tmp_path / "b") == 1
    bundle.write_bytes(_compact(rows))
    path = repo / "trailforge/golden/ca-areas-expected.json"
    table = json.loads(path.read_text())
    del table["areas"][ids[0]]
    path.write_text(json.dumps(table), encoding="utf-8")
    assert _build(repo, live, tmp_path / "c") == 1
    table["areas"][ids[0]] = {"trail_count": 9, "total_mi": 3.0, "total_mi_tolerance": 0.05,
                              "required_names": [], "forbidden_names": []}
    path.write_text(json.dumps(table), encoding="utf-8")
    assert _build(repo, live, tmp_path / "d") == 1


def _decide(tmp_path, capsys, state):
    repo, live, ids = _make_repo(tmp_path, 3)
    out = tmp_path / "out"
    assert _build(repo, live, out) == 0
    capsys.readouterr()
    cur = tmp_path / "cur"
    cur.mkdir()
    for key, what in state.items():
        data = {"cand": (out / key).read_bytes(), "base": (live / key).read_bytes(),
                "other": b'["third-party"]'}[what]
        (cur / key).write_bytes(data)
    code = ca.main(["rollback-decide", "--plan", str(out / "plan.json"), "--live-dir", str(cur)])
    cap = capsys.readouterr()
    return code, [line.split("\t") for line in cap.out.splitlines() if "\t" in line], cap.err, ids


def test_rollback_restores_candidates_skips_baseline_and_deletes_exactly_2n_keys(tmp_path, capsys):
    code, lines, _, ids = _decide(tmp_path, capsys, {k: "cand" for k in SHARED})
    assert code == 0 and [tuple(x) for x in lines[:3]] == [
        ("index.json", "restore"), ("trail-search.json", "restore"), ("trail-shapes.json", "restore")]
    deletes = [k for k, a in lines if a == "delete"]
    assert deletes == [f"{i}.json" for i in ids] + [f"silhouettes/{i}.json" for i in ids]
    assert not any(k in SHARED or "parking" in k for k in deletes)


def test_rollback_refuses_third_party_bytes(tmp_path, capsys):
    state = {"index.json": "other", "trail-search.json": "base", "trail-shapes.json": "cand"}
    code, lines, err, _ = _decide(tmp_path, capsys, state)
    assert code == 1 and lines == [] and "refusing" in err
