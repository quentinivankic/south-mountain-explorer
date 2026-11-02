#!/usr/bin/env python3
"""Tests for the Canada UPDATE mode (ca_publish.py replace-index / derive-update-areas /
build-update-candidates / rollback-decide-update / verify --mode update) and its two workflows.
Fixtures are built here; nothing reads the live registry."""
import copy
import re
import json
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ca_publish as ca  # noqa: E402
from test_ca_publish import _geom  # noqa: E402
from test_ca_publish_gates import _git  # noqa: E402
from test_ca_publish_sync import OLD_ROW, SHARED, _compact, _default, _make_repo  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OLD_SIL = {"b": [0, 0, 2, 2], "l": [[3, 4]]}
BUNDLE = "ios/SouthMountainExplorer/Resources/areas-index.json"
REG = "trailforge/golden/ca-areas.json"
EXP = "trailforge/golden/ca-areas-expected.json"


def _update_fixture(tmp_path, total, upd, *, row_changes=True):
    """Repo whose committed (HEAD) state is the OLD published state; the working tree already holds
    the NEW geoms/silhouettes for the `upd` positions. live/ = OLD R2 bytes. Old geoms of updated
    areas have 3 trails (t0..t2), the new ones 2 (t0, t1): ids of retained trails are kept."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    repo, live, ids = _make_repo(tmp_path, total)
    upd_ids = [ids[i] for i in upd]
    reg = ca.load_registry(repo / REG)
    bundle_path = repo / BUNDLE
    new_rows = json.loads(bundle_path.read_bytes())[1:]
    new_geoms = {a: (repo / f"public/areas/geom/{a}.json").read_bytes() for a in ids}
    new_sils = {a: (repo / f"public/areas/silhouettes/{a}.json").read_bytes() for a in ids}
    old_geoms, old_sils, old_rows = {}, {}, []
    for aid, row in zip(ids, new_rows):
        if aid in upd_ids:
            if row_changes:
                g = _geom(aid, trail_count=3, total_mi=4.0)
                old_rows.append([*row[:5], 3, 4.0, row[7]])
            else:
                g = _geom(aid, names=[f"{aid} Old A", f"{aid} Old B"])
                old_rows.append(list(row))
            old_geoms[aid], old_sils[aid] = _compact(g), _compact(OLD_SIL)
        else:
            old_geoms[aid], old_sils[aid] = new_geoms[aid], new_sils[aid]
            old_rows.append(list(row))
    for aid in ids:
        (repo / f"public/areas/geom/{aid}.json").write_bytes(old_geoms[aid])
        (repo / f"public/areas/silhouettes/{aid}.json").write_bytes(old_sils[aid])
    (repo / "public/areas/index.json").write_bytes(_default([OLD_ROW, *old_rows]))
    bundle_path.write_bytes(_compact([OLD_ROW, *old_rows]))
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "old published state")
    search = [["Old Trail", "old-area-co", "a", 1.0, "Easy"]]
    shapes = {"old-area-co/a": [0, 0, 255, 255]}
    for aid in ids:
        search += ca.expected_records("trail-search.json", aid, old_geoms[aid])
        shapes.update({r[0]: r[1] for r in ca.expected_records("trail-shapes.json", aid, old_geoms[aid])})
    (live / "index.json").write_bytes(_compact([OLD_ROW, *old_rows]))
    (live / "trail-search.json").write_bytes(_compact(search))
    (live / "trail-shapes.json").write_bytes(_compact(shapes))
    areas = tmp_path / "liveareas"
    (areas / "silhouettes").mkdir(parents=True)
    for aid in ids:
        (areas / f"{aid}.json").write_bytes(old_geoms[aid])
        (areas / "silhouettes" / f"{aid}.json").write_bytes(old_sils[aid])
    for aid in upd_ids:
        (repo / f"public/areas/geom/{aid}.json").write_bytes(new_geoms[aid])
        (repo / f"public/areas/silhouettes/{aid}.json").write_bytes(new_sils[aid])
    return {"repo": repo, "live": live, "areas": areas, "ids": ids, "upd": upd_ids, "reg": reg,
            "new_rows": new_rows, "old_rows": old_rows, "tmp": tmp_path}


def _replace_index(fx, work=None, sel_ids=None, rows=None, registry=None):
    repo, tmp_path = fx["repo"], fx["tmp"]
    work = work or tmp_path / "work"
    for aid, row in zip(fx["ids"], rows or fx["new_rows"]):
        if aid in fx["upd"]:
            (work / aid).mkdir(parents=True, exist_ok=True)
            (work / aid / "index.json").write_text(json.dumps([row]), encoding="utf-8")
    sel = tmp_path / "sel.txt"
    sel.write_text("\n".join(sel_ids or fx["upd"]) + "\n", encoding="utf-8")
    return ca.main(["replace-index", "--registry", str(registry or repo / REG),
                    "--selected", str(sel), "--rows-dir", str(work),
                    "--geom-dir", str(repo / "public/areas/geom"),
                    "--target", str(repo / "public/areas/index.json")])


def _finish(fx, *, stage=True):
    """The workflow's index steps: replace-index via the real CLI, then the regenerated bundle."""
    repo = fx["repo"]
    rc = _replace_index(fx)
    final_rows = json.loads((repo / "public/areas/index.json").read_bytes())
    (repo / BUNDLE).write_bytes(_compact(final_rows))
    if stage:
        _git(repo, "add", "public/areas", BUNDLE)
    return rc


def _derive(fx, out=None):
    out = out or fx["tmp"] / "areas.txt"
    rc = ca.main(["derive-update-areas", "--repo", str(fx["repo"]), "--live-index", str(fx["live"] / "index.json"),
                  "--live-areas-dir", str(fx["areas"]), "--out", str(out)])
    return rc, out


def _build_update(fx, out):
    return ca.main(["build-update-candidates", "--repo", str(fx["repo"]),
                    "--live-index", str(fx["live"] / "index.json"),
                    "--live-search", str(fx["live"] / "trail-search.json"),
                    "--live-shapes", str(fx["live"] / "trail-shapes.json"),
                    "--live-areas-dir", str(fx["areas"]), "--out-dir", str(out)])


UPDATE_CASES = [(1, [0]), (5, [0, 2, 4]), (45, [i for i in range(45) if i % 10 != 9][:40])]


@pytest.mark.parametrize("total,upd", UPDATE_CASES, ids=["N1", "N3", "N40"])
def test_update_splice_replaces_exactly_the_intended_records(tmp_path, total, upd):
    fx = _update_fixture(tmp_path, total, upd)
    assert _finish(fx) == 0
    rc, areas_file = _derive(fx)
    assert rc == 0 and areas_file.read_text().split() == sorted(fx["upd"])
    out = tmp_path / "out"
    parking = (fx["live"] / "parking.json").read_bytes()
    assert _build_update(fx, out) == 0
    plan = json.loads((out / "plan.json").read_text())
    n = len(fx["upd"])
    keys = [w["key"] for w in plan["writes"]]
    ids = sorted(fx["upd"])
    assert plan["schema"] == "trekdex-ca-r2-update-plan-v1"
    assert len(keys) == 2 * n + 3 and not any("parking" in k for k in keys)
    assert keys[:n] == [f"{i}.json" for i in ids] and keys[n:2 * n] == [f"silhouettes/{i}.json" for i in ids]
    assert keys[2 * n:] == ["trail-shapes.json", "trail-search.json", "index.json"]       # index last
    assert sorted(plan["old_objects"]) == sorted(keys[:2 * n])
    for key, meta in plan["old_objects"].items():
        assert meta["sha256"] == ca._sha((fx["areas"] / key).read_bytes())               # OLD bytes recorded
    assert (fx["live"] / "parking.json").read_bytes() == parking
    assert [r["area"] for r in plan["trail_id_retention"]] == ids
    assert all(r["retained"] == 2 and r["dropped"] == ["t2"] and r["added"] == [] for r in plan["trail_id_retention"])
    for key, as_dict, owner in (("trail-search.json", False, lambda r: r[1]),
                                ("trail-shapes.json", True, lambda r: r[0].split("/")[0])):
        old, new = (fx["live"] / key).read_bytes(), (out / key).read_bytes()
        old_p, new_p = ca._split_records(old, as_dict, "o"), ca._split_records(new, as_dict, "n")
        assert [raw for rec, raw in old_p if owner(rec) not in ids] == [raw for rec, raw in new_p if owner(rec) not in ids]
        seq = lambda pieces: [k for i, k in enumerate(owner(r) for r, _ in pieces) if i == 0 or k != owner(pieces[i - 1][0])]  # noqa: E731
        assert seq(old_p) == seq(new_p)                                                  # blocks keep their position
        assert sum(1 for r, _ in new_p if owner(r) in ids) == 2 * n
        assert sum(1 for r, _ in old_p if owner(r) in ids) == 3 * n
    old_idx, new_idx = json.loads((fx["live"] / "index.json").read_bytes()), json.loads((out / "index.json").read_bytes())
    assert [r[0] for r in new_idx] == [r[0] for r in old_idx] and new_idx[0] == OLD_ROW
    for o, nw in zip(old_idx, new_idx):
        assert (nw != o) == (nw[0] in ids)
    assert new_idx == json.loads((fx["repo"] / BUNDLE).read_bytes())
    for w in plan["writes"]:
        assert ca._sha((out / w["file"]).read_bytes()) == w["sha256"]


def test_update_derivation_cap_and_refusals(tmp_path):
    fx = _update_fixture(tmp_path, 41, list(range(41)))
    assert _finish(fx) == 1                                           # replace-index itself refuses 41
    (fx["repo"] / BUNDLE).write_bytes(_compact([OLD_ROW, *fx["new_rows"]]))
    assert _derive(fx)[0] == 1                                        # 41 > cap 40
    fx2 = _update_fixture(tmp_path / "b", 3, [1])
    assert _finish(fx2) == 0 and _derive(fx2)[0] == 0
    assert _derive(_update_fixture(tmp_path / "c", 2, []))[0] == 1    # nothing changed -> nothing to update
    bundle = fx2["repo"] / BUNDLE
    rows = json.loads(bundle.read_bytes())
    bundle.write_bytes(_compact(rows[:-1]))
    assert _derive(fx2)[0] == 1                                       # a removed row is not an update
    bundle.write_bytes(_compact([*rows, ["zzz-ca-ns", "New", "Canada", 45.0, -62.0, 2, 3.0, 5]]))
    assert _derive(fx2)[0] == 1                                       # an appended (new) area is not an update
    bundle.write_bytes(_compact([[*OLD_ROW[:1], "Renamed", *OLD_ROW[2:]], *rows[1:]]))
    assert _derive(fx2)[0] == 1                                       # a non-Canadian (US/DK) live row differs
    bundle.write_bytes(_compact(rows))
    (fx2["areas"] / f"{fx2['ids'][1]}.json").unlink()
    assert _derive(fx2)[0] == 1                                       # the 2N keys must exist


def test_update_refuses_non_canada_ids_and_unblessed_areas(tmp_path, capsys):
    fx = _update_fixture(tmp_path, 3, [0])
    assert _finish(fx) == 0
    # a US/DK id in the selection is not a registry id
    assert _replace_index(fx, sel_ids=["ghost-dk"]) == 1
    assert _replace_index(fx, sel_ids=["yosemite-ca"]) == 1
    # a Danish/US row changed in the bundle (the repo, not the registry, was touched): refused at derivation
    bundle = fx["repo"] / BUNDLE
    rows = json.loads(bundle.read_bytes())
    touched = copy.deepcopy(rows)
    touched[0][5] = 99                                                # the OLD (non-Canada) row
    bundle.write_bytes(_compact(touched))
    assert _derive(fx)[0] == 1 and "non-Canadian" in capsys.readouterr().err
    bundle.write_bytes(_compact(rows))
    assert _derive(fx)[0] == 0
    exp_path = fx["repo"] / EXP
    table = json.loads(exp_path.read_text())
    del table["areas"][fx["ids"][0]]
    exp_path.write_text(json.dumps(table), encoding="utf-8")
    assert _derive(fx)[0] == 1                                        # not blessed


def test_update_build_refuses_non_contiguous_blocks_and_unblessed_results(tmp_path):
    fx = _update_fixture(tmp_path, 3, [1])
    assert _finish(fx) == 0
    search_path = fx["live"] / "trail-search.json"
    original = search_path.read_bytes()
    rows = json.loads(original)
    aid = fx["upd"][0]
    mine = [r for r in rows if r[1] == aid]
    others = [r for r in rows if r[1] != aid]
    search_path.write_bytes(_compact([*others[:3], mine[0], *others[3:], *mine[1:]]))   # block split in two
    assert _build_update(fx, tmp_path / "a") == 1
    search_path.write_bytes(_compact([r for r in rows if r[1] != aid]))                  # no records at all
    assert _build_update(fx, tmp_path / "b") == 1
    search_path.write_bytes(original)
    assert _build_update(fx, tmp_path / "ok") == 0
    exp_path = fx["repo"] / EXP
    table = json.loads(exp_path.read_text())
    table["areas"][aid]["trail_count"] = 9
    exp_path.write_text(json.dumps(table), encoding="utf-8")
    assert _build_update(fx, tmp_path / "c") == 1                                        # must be the blessed result


# ------------------------------------------------ retained trail ids (new) ----

def _reid_new_geom(fx, aid):
    """The NEW geom (working tree) re-ids t1 -> u1 while keeping its name: a retained trail changed id."""
    path = fx["repo"] / f"public/areas/geom/{aid}.json"
    geom = json.loads(path.read_bytes())
    geom["trails"][1]["id"] = "u1"
    path.write_bytes(_compact(geom))


def test_update_refuses_a_retained_trail_whose_id_changed(tmp_path, capsys):
    fx = _update_fixture(tmp_path, 3, [1])
    assert _finish(fx) == 0
    assert _build_update(fx, tmp_path / "ok") == 0
    aid = fx["upd"][0]
    _reid_new_geom(fx, aid)
    assert _build_update(fx, tmp_path / "bad") == 1
    err = capsys.readouterr().err
    assert "retained trail changed id" in err and "u1" in err
    assert not (tmp_path / "bad/plan.json").exists()                  # nothing written for a refused plan


def test_update_allows_plain_additions_and_removals_but_not_a_swapped_id(tmp_path):
    old = _compact(_geom("a-ca-ns", names=["Alpha", "Beta", "Gamma"]))
    kept = _geom("a-ca-ns", names=["Alpha", "Beta"])
    assert ca.trail_ids_retained("a-ca-ns", old, _compact(kept))["dropped"] == ["t2"]       # removal
    grown = _geom("a-ca-ns", names=["Alpha", "Beta", "Gamma", "Delta"])
    assert ca.trail_ids_retained("a-ca-ns", old, _compact(grown))["added"] == ["t3"]        # addition
    swapped = _geom("a-ca-ns", names=["Alpha", "Beta", "Gamma"])
    swapped["trails"][2]["id"] = "z9"
    with pytest.raises(ca.CaError, match="retained trail changed id"):
        ca.trail_ids_retained("a-ca-ns", old, _compact(swapped))
    # name-only change with the id kept is a rename, not an id change
    renamed = _geom("a-ca-ns", names=["Alpha", "Beta", "Gamma Loop"])
    assert ca.trail_ids_retained("a-ca-ns", old, _compact(renamed))["retained"] == 3


def test_staged_update_diff_refuses_a_changed_trail_id(tmp_path):
    fx = _update_fixture(tmp_path, 3, [1])
    _reid_new_geom(fx, fx["upd"][0])
    assert _finish(fx) == 0
    assert _verify_update(fx) == 1
    # restoring the id passes
    fx2 = _update_fixture(tmp_path / "ok", 3, [1])
    assert _finish(fx2) == 0 and _verify_update(fx2) == 0


# ------------------------------------------------------------ allow_rename ----

def _write_registry(fx, mutate):
    path = fx["repo"] / REG
    doc = json.loads(path.read_text())
    mutate(doc)
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def _rename_fixture(tmp_path):
    """Live row/geom carry `Area N`; the registry now wants display_name 'Area N Shown' via allow_rename."""
    fx = _update_fixture(tmp_path, 3, [1])
    aid = fx["upd"][0]
    shown = "Area One Shown"
    i = fx["ids"].index(aid)
    fx["new_rows"][i] = [fx["new_rows"][i][0], shown, *fx["new_rows"][i][2:]]
    return fx, aid, shown


def test_rename_needs_the_registry_opt_in_and_changes_only_the_name(tmp_path):
    fx, aid, shown = _rename_fixture(tmp_path)
    live_name = fx["old_rows"][fx["ids"].index(aid)][1]
    _write_registry(fx, lambda d: next(a for a in d["areas"] if a["id"] == aid).update(display_name=shown))
    before = (fx["repo"] / "public/areas/index.json").read_bytes()
    assert _replace_index(fx) == 1                                    # no opt-in: refused
    assert (fx["repo"] / "public/areas/index.json").read_bytes() == before
    _write_registry(fx, lambda d: next(a for a in d["areas"] if a["id"] == aid).update(
        allow_rename={"from": live_name, "reason": "local name"}))
    assert _replace_index(fx) == 0
    after = json.loads((fx["repo"] / "public/areas/index.json").read_bytes())
    old = json.loads(before)
    for o, n in zip(old, after):
        if o[0] == aid:
            assert n[1] == shown and (n[0], *n[2:5], n[7]) == (o[0], *o[2:5], o[7])
        else:
            assert n == o
    old_p = ca._split_records(before, False, "t", ", ")
    new_p = ca._split_records((fx["repo"] / "public/areas/index.json").read_bytes(), False, "t", ", ")
    assert [raw for (r, raw) in old_p if r[0] != aid] == [raw for (r, raw) in new_p if r[0] != aid]
    assert _replace_index(fx) == 0                                    # idempotent re-run


@pytest.mark.parametrize("bad", ["wrong-from", "nfd-from", "other-area", "state", "relation", "length", "id"])
def test_rename_refusals_leave_the_index_untouched(tmp_path, bad):
    fx, aid, shown = _rename_fixture(tmp_path)
    live_name = fx["old_rows"][fx["ids"].index(aid)][1]
    other = next(a for a in fx["ids"] if a != aid)
    i = fx["ids"].index(aid)
    row = list(fx["new_rows"][i])
    from_name = live_name
    target = aid
    if bad == "wrong-from":
        from_name = "Not the live name"
    elif bad == "nfd-from":
        from_name = "Are\u0301a One"                                  # NFD: the registry itself refuses it
    elif bad == "other-area":
        target = other                                                # opt-in sits on an area that is not renamed
    elif bad == "state":
        row[2] = "Denmark"
    elif bad == "relation":
        row[7] = 4242
    elif bad == "length":
        row = row[:7]
    elif bad == "id":
        row[0] = "area-999-ca-ns"
    fx["new_rows"][i] = row
    _write_registry(fx, lambda d: next(a for a in d["areas"] if a["id"] == target).update(
        display_name=shown, allow_rename={"from": from_name, "reason": "x"}))
    before = (fx["repo"] / "public/areas/index.json").read_bytes()
    assert _replace_index(fx) == 1
    assert (fx["repo"] / "public/areas/index.json").read_bytes() == before


@pytest.mark.parametrize("item", [{"from": "A"}, {"from": "A", "reason": "x", "extra": 1}, {"from": " ", "reason": "x"},
                                  {"from": "A", "reason": ""}, ["A"], "A", {"from": "Shown", "reason": "x"}])
def test_allow_rename_registry_shapes_are_validated(tmp_path, item):
    fx = _update_fixture(tmp_path, 2, [0])
    _write_registry(fx, lambda d: d["areas"][0].update(display_name="Shown", allow_rename=item))
    with pytest.raises(ca.CaError, match="allow_rename"):
        ca.load_registry(fx["repo"] / REG)
    _write_registry(fx, lambda d: d["areas"][0].update(display_name=None, allow_rename={"from": "A", "reason": "x"}))
    with pytest.raises(ca.CaError):
        ca.load_registry(fx["repo"] / REG)


def test_replace_index_refuses_new_areas_and_identity_changes(tmp_path):
    fx = _update_fixture(tmp_path, 3, [1])
    aid = fx["upd"][0]
    i = fx["ids"].index(aid)
    row = fx["new_rows"][i]
    renamed = list(row)
    renamed[1] = "Another"
    fx["new_rows"][i] = renamed
    assert _replace_index(fx) == 1                                    # name change without opt-in
    fx["new_rows"][i] = row
    before = (fx["repo"] / "public/areas/index.json").read_bytes()
    assert _replace_index(fx, sel_ids=["ghost-ca-ns"]) == 1 and (fx["repo"] / "public/areas/index.json").read_bytes() == before
    assert _replace_index(fx) == 0 and _replace_index(fx) == 0        # idempotent (row already replaced)
    fx["new_rows"][i] = [*row[:5], 99, *row[6:]]
    assert _replace_index(fx) == 1                                    # count differs from the geom


# ------------------------------------------------- update rollback table ------

def _update_plan(tmp_path, capsys):
    fx = _update_fixture(tmp_path, 3, [0, 2])
    assert _finish(fx) == 0
    out = tmp_path / "out"
    assert _build_update(fx, out) == 0
    capsys.readouterr()
    return fx, out


def _live_state(fx, out, tmp_path, spec):
    plan = json.loads((out / "plan.json").read_text())
    cur = tmp_path / "cur"
    for w in plan["writes"]:
        key = w["key"]
        what = spec.get(key, spec["*"])
        old = (fx["live"] / key).read_bytes() if key in SHARED else (fx["areas"] / key).read_bytes()
        data = {"cand": (out / w["file"]).read_bytes(), "old": old, "other": b'{"third":"party"}'}[what]
        (cur / key).parent.mkdir(parents=True, exist_ok=True)
        (cur / key).write_bytes(data)
    return plan, cur


def _decide_update(fx, out, tmp_path, capsys, spec):
    _, cur = _live_state(fx, out, tmp_path, spec)
    capsys.readouterr()
    code = ca.main(["rollback-decide-update", "--plan", str(out / "plan.json"), "--live-dir", str(cur)])
    cap = capsys.readouterr()
    return code, [line.split("\t") for line in cap.out.splitlines() if "\t" in line], cap.err


def test_update_rollback_full_apply_restores_every_overwritten_object(tmp_path, capsys):
    fx, out = _update_plan(tmp_path, capsys)
    code, lines, _ = _decide_update(fx, out, tmp_path, capsys, {"*": "cand"})
    assert code == 0 and len(lines) == 2 * 2 + 3
    assert [k for k, _ in lines[:3]] == ["index.json", "trail-search.json", "trail-shapes.json"]   # index first
    assert all(a == "restore" for _, a in lines)
    assert not any("parking" in k for k, _ in lines) and not any(a == "delete" for _, a in lines)


def test_update_rollback_partial_apply_restores_only_candidate_objects(tmp_path, capsys):
    fx, out = _update_plan(tmp_path, capsys)
    first = fx["upd"][0]
    code, lines, _ = _decide_update(fx, out, tmp_path, capsys,
                                    {"*": "old", f"{first}.json": "cand", "trail-shapes.json": "cand"})
    got = dict(lines)
    assert code == 0 and got[f"{first}.json"] == "restore" and got["trail-shapes.json"] == "restore"
    assert got["index.json"] == "skip" and got[f"silhouettes/{first}.json"] == "skip"


def test_update_rollback_nothing_applied_skips_everything(tmp_path, capsys):
    fx, out = _update_plan(tmp_path, capsys)
    code, lines, _ = _decide_update(fx, out, tmp_path, capsys, {"*": "old"})
    assert code == 0 and {a for _, a in lines} == {"skip"}


@pytest.mark.parametrize("bad", ["index.json", "trail-search.json", "trail-shapes.json", "AREA_GEOM", "AREA_SIL"])
def test_update_rollback_refuses_third_party_modified_object(tmp_path, capsys, bad):
    fx, out = _update_plan(tmp_path, capsys)
    key = {"AREA_GEOM": f"{fx['upd'][1]}.json", "AREA_SIL": f"silhouettes/{fx['upd'][1]}.json"}.get(bad, bad)
    code, lines, err = _decide_update(fx, out, tmp_path, capsys, {"*": "cand", key: "other"})
    assert code == 1 and lines == [] and "refusing" in err


def test_update_rollback_refuses_a_missing_object_and_an_append_plan(tmp_path, capsys):
    fx, out = _update_plan(tmp_path, capsys)
    _, cur = _live_state(fx, out, tmp_path, {"*": "cand"})
    (cur / f"{fx['upd'][0]}.json").unlink()
    assert ca.main(["rollback-decide-update", "--plan", str(out / "plan.json"), "--live-dir", str(cur)]) == 1
    plan = json.loads((out / "plan.json").read_text())
    plan["schema"] = "trekdex-ca-r2-plan-v1"
    (out / "plan2.json").write_text(json.dumps(plan))
    assert ca.main(["rollback-decide-update", "--plan", str(out / "plan2.json"), "--live-dir", str(cur)]) == 1


# ---------------------------- publish side: in-place rows + 2N+2 allowlist ----

def _verify_update(fx, cmd="verify-staged"):
    return ca.main([cmd, "--repo", str(fx["repo"]), "--areas", ",".join(fx["upd"]), "--mode", "update"])


@pytest.mark.parametrize("total,upd", UPDATE_CASES, ids=["N1", "N3", "N40"])
def test_update_allowlist_is_exactly_2n_plus_2_and_rows_are_replaced_in_place(tmp_path, total, upd):
    fx = _update_fixture(tmp_path, total, upd)
    before = (fx["repo"] / "public/areas/index.json").read_bytes()
    assert _finish(fx) == 0
    names = _git(fx["repo"], "diff", "--cached", "--name-only").stdout.decode().split()
    assert len(names) == 2 * len(upd) + 2 == len(ca.area_paths(fx["upd"]))
    assert _verify_update(fx) == 0
    old_p = ca._split_records(before, False, "t", sep=", ")
    new_p = ca._split_records((fx["repo"] / "public/areas/index.json").read_bytes(), False, "t", sep=", ")
    assert [r[0] for r, _ in old_p] == [r[0] for r, _ in new_p]                         # row order preserved
    for (orow, oraw), (nrow, nraw) in zip(old_p, new_p):
        assert (nraw == oraw) == (orow[0] not in fx["upd"])
    _git(fx["repo"], "commit", "-q", "-m", "update")
    assert _verify_update(fx, "verify-commit") == 0


def test_update_allowlist_accepts_a_geom_only_change_when_the_row_is_unchanged(tmp_path):
    fx = _update_fixture(tmp_path, 2, [0], row_changes=False)
    assert _finish(fx) == 0
    names = _git(fx["repo"], "diff", "--cached", "--name-only").stdout.decode().split()
    assert len(names) == 2 and not any("index" in n for n in names)
    assert _verify_update(fx) == 0


def test_update_staged_diff_refusals(tmp_path):
    fx = _update_fixture(tmp_path, 4, [1, 2])
    assert _finish(fx) == 0
    repo = fx["repo"]
    assert _verify_update(fx) == 0
    (repo / "public/areas/geom/other-ca-ns.json").write_text("{}", encoding="utf-8")
    _git(repo, "add", "public/areas/geom/other-ca-ns.json")
    assert _verify_update(fx) == 1                                    # extra path
    _git(repo, "rm", "-q", "-f", "public/areas/geom/other-ca-ns.json")
    by = [a for a in fx["ids"] if a not in fx["upd"]][0]
    (repo / f"public/areas/geom/{by}.json").write_bytes(b'{"id":"x"}')
    _git(repo, "add", f"public/areas/geom/{by}.json")
    assert _verify_update(fx) == 1                                    # a bystander geom modified
    _git(repo, "checkout", "-q", "HEAD", "--", f"public/areas/geom/{by}.json")
    assert _verify_update(fx) == 0
    _git(repo, "checkout", "-q", "HEAD", "--", f"public/areas/geom/{fx['upd'][0]}.json")
    assert _verify_update(fx) == 1                                    # an updated geom left unchanged
    _git(repo, "add", "-A", "public/areas")
    _git(repo, "rm", "-q", "-f", f"public/areas/silhouettes/{fx['upd'][1]}.json")
    assert _verify_update(fx) == 1                                    # deletion


def test_update_staged_diff_refuses_index_tampering_including_us_and_dk_rows(tmp_path):
    fx = _update_fixture(tmp_path, 4, [1, 2])
    assert _finish(fx) == 0
    repo = fx["repo"]
    index = repo / "public/areas/index.json"
    rows = json.loads(index.read_bytes())
    by = [i for i, r in enumerate(rows) if r[0] in fx["ids"] and r[0] not in fx["upd"]][0]

    def put(new_rows, *, bundle=True):
        index.write_bytes(_default(new_rows))
        if bundle:
            (repo / BUNDLE).write_bytes(_compact(new_rows))
        _git(repo, "add", "public/areas", BUNDLE)
    changed = copy.deepcopy(rows)
    changed[by][1] = "Renamed bystander"
    put(changed)
    assert _verify_update(fx) == 1                                    # a non-updated Canadian row changed
    changed = copy.deepcopy(rows)
    changed[0][1] = "Renamed US row"
    put(changed)
    assert _verify_update(fx) == 1                                    # a US/DK row touched
    swapped = copy.deepcopy(rows)
    swapped[1], swapped[2] = swapped[2], swapped[1]
    put(swapped)
    assert _verify_update(fx) == 1                                    # order changed
    put(rows + [["extra-ca-ns", "Extra", "Canada", 45.0, -62.0, 2, 3.0, 5]])
    assert _verify_update(fx) == 1                                    # a row was appended
    wrong = copy.deepcopy(rows)
    wrong[fx["ids"].index(fx["upd"][0]) + 1][5] = 99
    put(wrong)
    assert _verify_update(fx) == 1                                    # count differs from the geom
    ident = copy.deepcopy(rows)
    ident[fx["ids"].index(fx["upd"][0]) + 1][1] = "Another name"
    put(ident)
    assert _verify_update(fx) == 1                                    # identity changed without opt-in
    put(rows, bundle=False)
    assert _verify_update(fx) == 1                                    # only one of the two index files changed
    put(rows)
    assert _verify_update(fx) == 0
    index.write_bytes(json.dumps(rows, indent=1).encode("utf-8"))
    _git(repo, "add", "public/areas/index.json")
    assert _verify_update(fx) == 1                                    # producer framing differs


def test_update_select_refuses_new_areas_batches_and_unblessed(tmp_path, capsys):
    fx = _update_fixture(tmp_path, 3, [0])
    reg = fx["repo"] / REG
    index = fx["repo"] / "public/areas/index.json"
    live = fx["ids"][:2]
    index.write_bytes(_default([OLD_ROW, *[r for r in fx["old_rows"] if r[0] in live]]))

    def select(areas, mode):
        return ca.main(["select", "--registry", str(reg), "--index", str(index), "--expected", str(fx["repo"] / EXP),
                        "--areas", areas, "--province", "ns", "--mode", mode, "--out", str(tmp_path / "s.txt")])
    assert select(",".join(live), "update") == 0
    assert (tmp_path / "s.txt").read_text().split() == live
    assert select(fx["ids"][2], "update") == 1                        # not published yet
    assert "not in public/areas/index.json" in capsys.readouterr().err
    assert select("batch=1", "update") == 1                           # explicit ids only
    assert select(live[0], "publish") == 1                            # publish still refuses existing areas
    assert select(fx["ids"][2], "publish") == 0                       # ...and still accepts new ones
    assert select(f"{live[0]},{live[0]}", "update") == 1              # duplicate id
    assert select("ghost-ca-ns", "update") == 1                       # non-registry id
    assert select("a-ca-pe", "update") == 1                           # other province
    path = fx["repo"] / EXP
    table = json.loads(path.read_text())
    del table["areas"][live[1]]
    path.write_text(json.dumps(table), encoding="utf-8")
    assert select(live[1], "update") == 1                             # must be blessed


# ------------------------------------------------------------ update workflows -----
# The sync workflow's real shell steps run under /bin/bash (3.2 on macOS, as on the agent machine) against a
# fake R2 (`aws`) and a fake artifact store (`gh`): plan-update -> apply-update -> rollback-update end to end,
# including a lost ETag race, a mid-write failure (partial apply) and a third party's bytes at rollback.
import hashlib  # noqa: E402
import os  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import stat  # noqa: E402

SYNC = (ROOT / ".github/workflows/sync-ca-to-r2.yml").read_text()
PUBLISH = (ROOT / ".github/workflows/trailforge-publish-ca.yml").read_text()

FAKE_AWS = r'''#!/usr/bin/env python3
import hashlib, json, os, shutil, sys
store = os.environ["FAKE_R2"]
log = open(os.environ["FAKE_LOG"], "a")
argv = sys.argv[1:]
log.write(" ".join(argv) + "\n"); log.flush()
valued = {"--bucket", "--key", "--endpoint-url", "--query", "--output", "--body", "--content-type",
          "--cache-control", "--if-match", "--prefix"}
flags, pos, i = {}, [], 2
while i < len(argv):
    if argv[i] in valued:
        flags[argv[i]] = argv[i + 1]; i += 2
    else:
        pos.append(argv[i]); i += 1
op = argv[1]
key = flags.get("--key")
path = os.path.join(store, "obj", key) if key else None
meta_path = os.path.join(store, "meta", key.replace("/", "__") + ".json") if key else None
def die(code, msg):
    sys.stderr.write(msg + "\n"); sys.exit(code)
def meta():
    if not os.path.isfile(path):
        die(254, "An error occurred (NoSuchKey) when calling the %s operation: The specified key does not exist." % op)
    return json.load(open(meta_path))
if argv[0] != "s3api" or op not in ("get-object", "head-object", "put-object"):
    die(99, "fake aws: unsupported call " + " ".join(argv))
if op in ("get-object", "head-object"):
    m = meta()
    if op == "get-object":
        shutil.copyfile(path, pos[0])
    if flags.get("--query") == "ETag":
        print('"%s"' % m["etag"])
    elif op == "head-object":
        out = {"ContentType": m["ct"], "ETag": '"%s"' % m["etag"]}
        if m["cc"] is not None:
            out["CacheControl"] = m["cc"]
        print(json.dumps(out))
elif op == "put-object":
    race = os.environ.get("FAKE_RACE_ON_PUT")
    if race and race == key and os.path.isfile(path):             # a third party wins the race just before this write
        data = b"third-party-" + key.encode()
        open(path, "wb").write(data)
        json.dump({"etag": hashlib.md5(data).hexdigest(), "ct": "application/json", "cc": None}, open(meta_path, "w"))
    if "--if-match" in flags:
        if not os.path.isfile(path) or json.load(open(meta_path))["etag"] != flags["--if-match"]:
            die(254, "An error occurred (PreconditionFailed) when calling the PutObject operation: At least one of the pre-conditions you specified did not hold")
    elif os.path.isfile(path):
        die(98, "fake aws: unconditional overwrite of " + key)
    data = open(flags["--body"], "rb").read()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "wb").write(data)
    json.dump({"etag": hashlib.md5(data).hexdigest(), "ct": flags.get("--content-type"), "cc": flags.get("--cache-control")},
              open(meta_path, "w"))
'''
FAKE_GH = r'''#!/usr/bin/env python3
import os, shutil, sys
a = sys.argv[1:]
assert a[:2] == ["run", "download"], a
name, dest = a[a.index("-n") + 1], a[a.index("-D") + 1]
src = os.path.join(os.environ["FAKE_ARTIFACTS"], name)
if not os.path.isdir(src):
    sys.stderr.write("no artifact " + name + "\n"); sys.exit(1)
shutil.copytree(src, dest, dirs_exist_ok=True)
'''


def _put(store, key, data, ct="application/json", cc="public, max-age=86400"):
    (store / "obj" / key).parent.mkdir(parents=True, exist_ok=True)
    (store / "meta").mkdir(parents=True, exist_ok=True)
    (store / "obj" / key).write_bytes(data)
    (store / "meta" / (key.replace("/", "__") + ".json")).write_text(
        json.dumps({"etag": hashlib.md5(data).hexdigest(), "ct": ct, "cc": cc}))


def _step(name_prefix):
    yaml = pytest.importorskip("yaml")
    steps = next(iter(yaml.safe_load(SYNC)["jobs"].values()))["steps"]
    return next(s for s in steps if s.get("name", "").startswith(name_prefix))["run"]


class Sim:
    """A fake R2 seeded with the fixture's live objects, plus runner dirs that execute the real workflow steps."""

    def __init__(self, tmp_path, capsys):
        self.tmp = tmp_path
        self.fx, _ = _update_plan(tmp_path / "fx", capsys)
        self.fx_repo = self.fx["repo"]
        self.store = tmp_path / "r2"
        self.log = tmp_path / "aws.log"
        self.log.write_text("")
        self.artifacts = tmp_path / "artifacts"
        self.artifacts.mkdir()
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        for name, text in (("aws", FAKE_AWS), ("gh", FAKE_GH)):
            (bin_dir / name).write_text(text)
            (bin_dir / name).chmod((bin_dir / name).stat().st_mode | stat.S_IEXEC)
        self.bin = bin_dir
        live = self.fx["live"]
        for key in SHARED:
            _put(self.store, key, (live / key).read_bytes(), cc="public, max-age=300")
        _put(self.store, "parking.json", (live / "parking.json").read_bytes(), cc="public, max-age=300")
        for aid in self.fx["ids"]:
            _put(self.store, f"{aid}.json", (self.fx["areas"] / f"{aid}.json").read_bytes())
            _put(self.store, f"silhouettes/{aid}.json", (self.fx["areas"] / "silhouettes" / f"{aid}.json").read_bytes())
        a0, a1 = self.fx["upd"]
        # headers that differ from the workflow constants must come back exactly after a rollback
        _put(self.store, f"silhouettes/{a0}.json", (self.fx["areas"] / "silhouettes" / f"{a0}.json").read_bytes(),
             cc="public, max-age=999")
        _put(self.store, f"{a1}.json", (self.fx["areas"] / f"{a1}.json").read_bytes(), cc=None)
        self.initial = self.state()
        self.run_n = 0

    def state(self):
        out = {}
        for p in sorted((self.store / "obj").rglob("*")):
            if p.is_file():
                key = p.relative_to(self.store / "obj").as_posix()
                meta = json.loads((self.store / "meta" / (key.replace("/", "__") + ".json")).read_text())
                out[key] = (p.read_bytes(), meta["ct"], meta["cc"])
        return out

    def runner(self):
        self.run_n += 1
        d = self.tmp / f"runner{self.run_n}"
        shutil.copytree(self.fx_repo, d, ignore=shutil.ignore_patterns(".git"))
        (d / "scripts").symlink_to(ROOT / "scripts")
        return d

    def sh(self, runner, prefix, mode, run_id, rollback_run_id="", env=None):
        e = {**os.environ, "PATH": f"{self.bin}:{Path(sys.executable).parent}:{os.environ['PATH']}",
             "FAKE_R2": str(self.store), "FAKE_LOG": str(self.log), "FAKE_ARTIFACTS": str(self.artifacts),
             "BUCKET": "trekdex-areas", "ENDPOINT": "https://fake.invalid", "MODE": mode,
             "ROLLBACK_RUN_ID": rollback_run_id, "GITHUB_RUN_ID": str(run_id),
             "GITHUB_REPOSITORY": "o/r", "GH_TOKEN": "x", **(env or {})}
        return subprocess.run(["/bin/bash", "-e", "-c", _step(prefix)], cwd=runner, env=e, capture_output=True, text=True)

    def plan(self, mode, run_id):
        r = self.runner()
        snap = self.sh(r, "Snapshot live R2", mode, run_id)
        assert snap.returncode == 0, snap.stderr
        return r, self.sh(r, "Update plan", mode, run_id)

    def upload(self, runner, run_id):
        dest = self.artifacts / f"ca-r2-update-snapshot-{run_id}"
        for d in ("snap", "cand"):
            shutil.copytree(runner / d, dest / d)

    def puts(self):
        return [ln for ln in self.log.read_text().splitlines() if ln.startswith("s3api put-object")]


@pytest.fixture
def sim(tmp_path, capsys):
    return Sim(tmp_path, capsys)


def _apply(sim, run_id=1001, env=None, mutate=None):
    r, plan = sim.plan("apply-update", run_id)
    assert plan.returncode == 0, plan.stderr
    sim.upload(r, run_id)
    if mutate:
        mutate()
    return r, sim.sh(r, "Apply update", "apply-update", run_id, env=env)


def test_e2e_plan_apply_rollback_restores_exact_bytes_and_headers(sim):
    r, plan = sim.plan("plan-update", 1000)
    assert plan.returncode == 0, plan.stderr
    assert sim.state() == sim.initial and sim.puts() == []             # plan writes nothing
    cand = json.loads((r / "cand/plan.json").read_text())
    assert cand["schema"] == "trekdex-ca-r2-update-plan-v1"
    hdr = dict((ln.split("\t")[0], ln.split("\t")[1:]) for ln in (r / "snap/update-headers.tsv").read_text().splitlines())
    a0, a1 = sim.fx["upd"]
    assert hdr[f"silhouettes/{a0}.json"] == ["application/json", "public, max-age=999"]
    assert hdr[f"{a1}.json"] == ["application/json", ""]                # no cache-control on that object
    r, applied = _apply(sim)
    assert applied.returncode == 0, applied.stderr
    after = sim.state()
    for w in cand["writes"]:
        assert after[w["key"]][0] == (r / "cand" / w["file"]).read_bytes()
    untouched = {k for k in after if k not in {w["key"] for w in cand["writes"]}}
    assert untouched and all(after[k] == sim.initial[k] for k in untouched)       # incl. parking.json and bystander areas
    assert after["parking.json"] == sim.initial["parking.json"]
    puts = sim.puts()
    assert len(puts) == 2 * 2 + 3 and all("--if-match" in p for p in puts)         # every write is conditional
    assert [p.split("--key ")[1].split()[0] for p in puts][-3:] == ["trail-shapes.json", "trail-search.json", "index.json"]
    assert not any("parking.json" in p for p in puts)
    # rollback from a fresh runner (fresh snapshot of the now-live state)
    r2 = sim.runner()
    assert sim.sh(r2, "Snapshot live R2", "rollback-update", 2002, "1001").returncode == 0
    rb = sim.sh(r2, "Rollback update", "rollback-update", 2002, "1001")
    assert rb.returncode == 0, rb.stderr + rb.stdout
    assert sim.state() == sim.initial                                     # exact bytes AND content-type/cache-control
    assert "delete-object" not in sim.log.read_text()
    assert "update rollback verified" in rb.stdout


def test_apply_aborts_before_any_write_when_a_shared_file_changed_after_the_plan(sim):
    def race():
        _put(sim.store, "index.json", b"[]", cc="public, max-age=300")
    r, out = _apply(sim, mutate=race)
    assert out.returncode != 0 and "index.json changed since snapshot; aborting before any write" in out.stdout
    assert sim.puts() == []


def test_apply_aborts_before_any_write_when_an_area_object_or_parking_changed(sim):
    a0 = sim.fx["upd"][0]
    r, out = _apply(sim, mutate=lambda: _put(sim.store, f"{a0}.json", b"{}"))
    assert out.returncode != 0 and f"{a0}.json changed since snapshot" in out.stdout and sim.puts() == []
    _put(sim.store, f"{a0}.json", sim.initial[f"{a0}.json"][0])
    r, out = _apply(sim, run_id=1002, mutate=lambda: _put(sim.store, "parking.json", b"{}"))
    assert out.returncode != 0 and "parking.json changed since snapshot" in out.stdout and sim.puts() == []


def test_etag_race_mid_write_fails_closed_then_rollback_restores_everything(sim):
    a1 = sim.fx["upd"][1]
    r, out = _apply(sim, env={"FAKE_RACE_ON_PUT": f"silhouettes/{a1}.json"})
    assert out.returncode != 0 and "PreconditionFailed" in out.stderr
    assert "PARTIAL APPLY: dispatch mode=rollback-update with rollback_run_id=1001" in out.stdout
    assert any("index.json" in p for p in sim.puts()) is False         # index is written last: never reached
    assert sim.state()["index.json"] == sim.initial["index.json"]
    # the third party's bytes at the raced key must make the rollback REFUSE (it never overwrites unknown bytes)
    r2 = sim.runner()
    assert sim.sh(r2, "Snapshot live R2", "rollback-update", 2002, "1001").returncode == 0
    before = sim.state()
    n = len(sim.puts())
    rb = sim.sh(r2, "Rollback update", "rollback-update", 2002, "1001")
    assert rb.returncode != 0 and "refusing" in rb.stderr
    assert sim.state() == before and len(sim.puts()) == n              # refused before writing anything
    # once the raced object is back to its old bytes the partial apply rolls back cleanly
    _put(sim.store, f"silhouettes/{a1}.json", sim.initial[f"silhouettes/{a1}.json"][0],
         ct=sim.initial[f"silhouettes/{a1}.json"][1], cc=sim.initial[f"silhouettes/{a1}.json"][2])
    r3 = sim.runner()
    assert sim.sh(r3, "Snapshot live R2", "rollback-update", 3003, "1001").returncode == 0
    rb = sim.sh(r3, "Rollback update", "rollback-update", 3003, "1001")
    assert rb.returncode == 0, rb.stderr + rb.stdout
    assert sim.state() == sim.initial


def test_rollback_refuses_third_party_bytes_and_writes_nothing(sim):
    r, out = _apply(sim)
    assert out.returncode == 0, out.stderr
    a0 = sim.fx["upd"][0]
    _put(sim.store, f"silhouettes/{a0}.json", b'{"b":[9,9,9,9],"l":[[9,9]]}')
    r2 = sim.runner()
    assert sim.sh(r2, "Snapshot live R2", "rollback-update", 2002, "1001").returncode == 0
    n, before = len(sim.puts()), sim.state()
    rb = sim.sh(r2, "Rollback update", "rollback-update", 2002, "1001")
    assert rb.returncode != 0 and "refusing to roll back over it" in rb.stderr
    assert len(sim.puts()) == n and sim.state() == before


def test_plan_update_fails_when_a_live_area_object_is_missing(sim):
    a = sim.fx["ids"][1]
    (sim.store / "obj" / f"{a}.json").unlink()
    r, plan = sim.plan("plan-update", 1000)
    assert plan.returncode != 0 and "NoSuchKey" in plan.stderr
    assert sim.puts() == []


def test_plan_update_refuses_a_changed_trail_id_before_any_artifact(sim):
    # the repo's NEW geom re-ids a retained trail: the plan step must fail closed (and nothing is written)
    aid = sim.fx["upd"][0]
    original = (sim.fx_repo / f"public/areas/geom/{aid}.json").read_bytes()
    _reid_new_geom(sim.fx, aid)
    r, plan = sim.plan("plan-update", 1000)
    assert plan.returncode != 0 and "retained trail changed id" in plan.stderr
    assert not (r / "cand/plan.json").exists() and sim.puts() == []
    (sim.fx_repo / f"public/areas/geom/{aid}.json").write_bytes(original)


# ------------------------------------------ static workflow contracts -------

def test_sync_ca_update_modes_contract():
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(SYNC)
    assert doc["concurrency"] == {"group": "sync-geom-to-r2", "cancel-in-progress": False}
    on = doc.get("on") or doc.get(True)
    assert list(on) == ["workflow_dispatch"]
    inputs = on["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"mode", "rollback_run_id"}                  # the area list is never an input
    assert inputs["mode"]["options"] == ["plan", "apply", "rollback", "plan-update", "apply-update", "rollback-update"]
    steps = {s["name"]: s for s in next(iter(doc["jobs"].values()))["steps"] if "name" in s}
    cond = {n: s.get("if", "") for n, s in steps.items()}
    guards = SYNC.split("- name: Guards", 1)[1].split("- name: Snapshot", 1)[0]
    assert "plan|apply|rollback|plan-update|apply-update|rollback-update" in guards
    assert 'if [ "$MODE" = "rollback" ] || [ "$MODE" = "rollback-update" ]' in guards
    assert 'test "$GITHUB_REF_NAME" = "main"' in guards and 'test "$(git rev-parse origin/main)" = "$(git rev-parse HEAD)"' in guards
    # the append path conditions are exactly the old modes: update modes never run them
    assert cond["Plan (area list from the committed index diff; build + prove candidates)"] == \
        "${{ inputs.mode == 'plan' || inputs.mode == 'apply' }}"
    assert cond["Apply (write 2N+3 keys in order, read each back)"] == "${{ inputs.mode == 'apply' }}"
    assert cond["Rollback (restore shared files, delete exactly the 2N area keys)"] == "${{ inputs.mode == 'rollback' }}"
    update_plan = SYNC.split("- name: Update plan", 1)[1].split("\n      - name:", 1)[0]
    apply_update = SYNC.split("- name: Apply update", 1)[1].split("\n      - name:", 1)[0]
    rollback_update = SYNC.split("- name: Rollback update", 1)[1]
    assert "inputs.mode == 'plan-update' || inputs.mode == 'apply-update'" in cond[
        "Update plan (live Canadian objects, repo-vs-live area set, candidates, proofs)"]
    assert "get-object" in update_plan and "derive-update-areas" in update_plan and "already exists" not in update_plan
    assert "build-update-candidates" in update_plan and "live-ca-ids" in update_plan
    assert (SYNC.index("- name: Update plan") < SYNC.index("ca-r2-update-snapshot-${{ github.run_id }}")
            < SYNC.index("- name: Apply update"))
    assert "--if-match" in apply_update and 'put "cand/$file" "$key" "$cc" "$(base "$key")"' in apply_update
    assert apply_update.index("changed since snapshot") < apply_update.index('put "cand/$file"')
    assert "PARTIAL APPLY: dispatch mode=rollback-update with rollback_run_id=$GITHUB_RUN_ID" in apply_update
    assert "trap - EXIT" in apply_update and "READBACK MISMATCH" in apply_update and "put-object" in apply_update
    assert 'test "$(cur parking.json)" = "$(base parking.json)"' in apply_update and "cand/parking" not in apply_update
    assert "delete-object" not in update_plan + apply_update + rollback_update      # an update never removes objects
    assert "--if-match" in rollback_update and "rollback-decide-update" in rollback_update
    assert "ROLLBACK READBACK MISMATCH" in rollback_update and "ROLLBACK HEADER MISMATCH" in rollback_update
    assert "update-headers.tsv" in update_plan and "update-headers.tsv" in rollback_update and "parking_after" in rollback_update
    assert "|| true" not in rollback_update and "2>&1" not in rollback_update
    assert not re.search(r"--key\s+parking\.json[^\n]*--body|put[^\n]*parking", SYNC)
    # the write call is byte-identical to the reviewed Denmark conditional write
    dk = (ROOT / ".github/workflows/sync-dk-to-r2.yml").read_text()
    call = ('aws s3api put-object --bucket "$BUCKET" --key "$2" --body "$1" \\\n'
            '              --content-type "application/json" --cache-control "$3" --if-match "$4" \\\n'
            '              --endpoint-url "$ENDPOINT" > /dev/null')
    assert call in dk and call in apply_update
    assert sorted(set(re.findall(r"secrets\.([A-Z0-9_]+)", SYNC))) == ["R2_ACCESS_KEY_ID", "R2_ACCOUNT_ID", "R2_SECRET_ACCESS_KEY"]


def test_publish_ca_update_mode_is_main_only_explicit_ids_and_publish_unchanged():
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(PUBLISH)
    inputs = (doc.get("on") or doc.get(True))["workflow_dispatch"]["inputs"]
    assert inputs["mode"]["options"] == ["qualify", "publish", "update"] and inputs["mode"]["default"] == "qualify"
    assert doc["concurrency"] == {"group": "trailforge-publish", "cancel-in-progress": False}
    validate = PUBLISH.split("- name: Validate input shape before checkout", 1)[1].split("- uses:", 1)[0]
    assert "(qualify|publish|update)" in validate
    assert '[[ "$RAW_AREAS" != batch=* ]]' in validate                  # update takes explicit ids only
    assert 'if [ "$RAW_MODE" != "qualify" ]' in validate and 'test "$GITHUB_REF_NAME" = "main"' in validate
    install = PUBLISH.split("- name: Install blessed geoms", 1)[1].split("\n      - name:", 1)[0]
    assert 'test ! -e "public/areas/geom/$ID.json"' in install and "merge-index" in install
    upd = PUBLISH.split("- name: Install updated geoms", 1)[1].split("\n      - name:", 1)[0]
    assert 'test -f "public/areas/geom/$ID.json"' in upd and "replace-index" in upd and "merge-index" not in upd
    assert "if: ${{ inputs.mode == 'update' }}" in upd
    commit = PUBLISH.split("- name: Commit published data", 1)[1].split("\n      - name:", 1)[0]
    assert "inputs.mode == 'publish' || inputs.mode == 'update'" in commit and '--mode "$MODE"' in commit
    assert commit.count("verify-staged") == 1 and commit.count("verify-commit") == 1
    assert 'PREP_MODE="$MODE"; [ "$MODE" = "update" ] && PREP_MODE=publish' in PUBLISH     # strict bbox checks
    assert 'if [ "$MODE" != "qualify" ]; then\n              python3 scripts/ca_publish.py assert-expected' in PUBLISH
    for forbidden in ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "aws s3", "s3api", "--force", "gh workflow run"):
        assert forbidden not in PUBLISH
