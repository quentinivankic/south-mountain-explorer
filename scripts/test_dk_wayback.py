#!/usr/bin/env python3
"""Way-backed Danish areas (registry `way_id`): registry, prepare-areas, index row,
publish_areas identity, dk-run-area.sh gate. The assemble.py CLI side lives in
trailforge/assemble/test_dk_way_boundary.py."""
import copy
import importlib.util
import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dk_publish as dk  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "trailforge/golden/dk-areas.json"
MOLS = "nationalpark-mols-bjerge-dk"
WAY = "testskov-dk"
WAY_ID = 99000001   # synthetic: must not equal any real registry way_id
BOX = [12.50, 55.76, 12.60, 55.82]


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


publish_areas = _load("publish_areas_dk_wayback_tests", "trailforge/serve/publish_areas.py")


def _way_entry(**over):
    entry = {"id": WAY, "name": "Testskov", "state": "Denmark", "way_id": WAY_ID,
             "bbox": None, "center": None, "rules": "dk-v3", "tier": 3, "hold": None,
             "est_trails": 3, "est_mi": 5.0, "notes": "synthetic way-backed test entry"}
    entry.update(over)
    return entry


def _registry(tmp_path, entry=None, mutate=None):
    doc = json.loads(REGISTRY.read_text(encoding="utf-8"))
    doc["areas"].append(entry if entry is not None else _way_entry())
    if mutate:
        mutate(doc)
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


# ----------------------------------------------------------------- registry --
def test_registry_accepts_a_way_backed_entry_and_the_committed_registry_is_unchanged(tmp_path):
    reg = dk.load_registry(_registry(tmp_path))
    assert reg["by_id"][WAY]["way_id"] == WAY_ID and "relation_id" not in reg["by_id"][WAY]
    committed = dk.load_registry(REGISTRY)
    assert all(("way_id" in a) != ("relation_id" in a) for a in committed["areas"])   # exactly one boundary id


@pytest.mark.parametrize("entry", [
    _way_entry(relation_id=7046786),                  # both ids
    _way_entry(way_id=None),                          # way_id not a number
    _way_entry(way_id=0),
    _way_entry(way_id=True),
    _way_entry(way_id="99000001"),
    _way_entry(rules="legacy"),                       # way-backed needs a Danish rule set
    _way_entry(rules=None),
])
def test_registry_rejects_bad_way_entries(tmp_path, entry):
    with pytest.raises(dk.DkError):
        dk.load_registry(_registry(tmp_path, entry))


def test_registry_rejects_an_entry_with_neither_id(tmp_path):
    entry = _way_entry()
    del entry["way_id"]
    with pytest.raises(dk.DkError):
        dk.load_registry(_registry(tmp_path, entry))


def test_registry_rejects_a_repeated_way_id(tmp_path):
    def add_twin(doc):
        doc["areas"].append(_way_entry(id="twin-dk", name="Twin"))
    with pytest.raises(dk.DkError):
        dk.load_registry(_registry(tmp_path, mutate=add_twin))


def test_select_emits_w_and_r_getid_args(tmp_path):
    reg_path = _registry(tmp_path)
    out, gh = tmp_path / "sel.txt", tmp_path / "gh"
    assert dk.main(["select", "--registry", str(reg_path), "--areas", f"{MOLS},{WAY}",
                    "--mode", "qualify", "--chunk-size", "5", "--out", str(out),
                    "--github-output", str(gh)]) == 0
    assert "getid_args=r7046785 w99000001" in gh.read_text()


# ------------------------------------------------------------ prepare-areas --
WAY_OSM = """<?xml version='1.0' encoding='UTF-8'?>
<osm version="0.6">
<node id="1" lat="{s}" lon="{w}" version="1"/><node id="2" lat="{s}" lon="{e}" version="1"/>
<node id="3" lat="{n}" lon="{e}" version="1"/><node id="4" lat="{n}" lon="{w}" version="1"/>
<way id="{way}" version="1"><nd ref="1"/><nd ref="2"/><nd ref="3"/><nd ref="4"/>{close}
<tag k="leisure" v="nature_reserve"/><tag k="name" v="{name}"/></way>
</osm>
"""


def _prepare(tmp_path, *, way=WAY_ID, name="Testskov", close=True, box=BOX, disc=None):
    bounds = tmp_path / "bounds.osm"
    bounds.write_text(WAY_OSM.format(
        way=way, name=name, w=box[0], s=box[1], e=box[2], n=box[3],
        close='<nd ref="1"/>' if close else ""), encoding="utf-8")
    sel = tmp_path / "sel.txt"
    sel.write_text(f"{WAY}\n", encoding="utf-8")
    report = tmp_path / "disc.json"
    report.write_text(json.dumps({"candidates": disc or []}), encoding="utf-8")
    out = tmp_path / "out"
    rc = dk.main(["prepare-areas", "--selected", str(sel), "--bounds-pbf", str(bounds),
                  "--discovery-report", str(report), "--mode", "qualify", "--out-dir", str(out),
                  "--registry", str(_registry(tmp_path))])
    return rc, json.loads((out / WAY / "area.json").read_text()), out


def test_prepare_areas_writes_a_seven_field_row_and_way_identity(tmp_path):
    rc, area, out = _prepare(tmp_path)
    assert rc == 0 and area["status"] == "ok", area
    assert area["way_id"] == WAY_ID and "relation_id" not in area
    assert area["cross_check"] == "registry-way"
    row = json.loads((out / WAY / "index.json").read_text())
    assert row == [[WAY, "Testskov", "Denmark", 55.8, 12.6, None, None]]   # 7 long: no 8th element
    assert len(row[0]) == 7


def test_prepare_areas_ignores_a_misleading_discovery_candidate_for_a_way(tmp_path):
    far = {"index_row": [WAY, "Testskov", "Denmark", 57.9, 9.0], "osm_relation_id": 5}
    rc, area, _ = _prepare(tmp_path, disc=[far])
    assert rc == 0 and area["status"] == "ok" and area["cross_check"] == "registry-way"


@pytest.mark.parametrize("kwargs, text", [
    ({"way": 555}, "boundary way not found in the PBF"),                   # wrong way id
    ({"name": "Other"}, "does not match registry name"),
    ({"close": False}, "not a closed ring"),
    ({"box": [12.5, 55.76, 12.5001, 55.7601]}, "implausible"),
])
def test_prepare_areas_fails_closed_on_a_bad_way(tmp_path, kwargs, text):
    rc, area, out = _prepare(tmp_path, **kwargs)
    assert rc == 0 and area["status"] == "failed"
    assert any(text in p for p in area["problems"]), area["problems"]
    assert not (out / WAY / "index.json").exists()


def test_way_problems_rejects_a_duplicate_way_id():
    entry = _way_entry()
    info = {"bbox": list(BOX), "count": 2, "closed": True, "tags": {"name": "Testskov"}}
    assert any("not unique" in p for p in dk.way_problems(entry, info))
    assert dk.way_problems(entry, None)


# ----------------------------------------------------------------- index row --
def _geom(trails=2, mi=3.5):
    return {"trail_count": trails, "total_mi": mi,
            "trails": [{"name": f"T{i}", "segments": [[[55.79, 12.55], [55.79, 12.56]]]}
                       for i in range(trails)]}


WAY_ROW = [WAY, "Testskov", "Denmark", 55.8, 12.6, 2, 3.5]


def test_check_new_row_way_backed_needs_exactly_seven_fields():
    entry = _way_entry(center=[55.8, 12.6], bbox=[12.45, 55.71, 12.65, 55.87])
    dk.check_new_row(list(WAY_ROW), entry, _geom(), "t")
    for bad in (WAY_ROW + [None], WAY_ROW + [WAY_ID], WAY_ROW[:6]):
        with pytest.raises(dk.DkError):
            dk.check_new_row(list(bad), entry, _geom(), "t")


def test_check_new_row_relation_backed_still_needs_its_eighth_field():
    entry = dk.load_registry(REGISTRY)["by_id"][MOLS]
    ok = [MOLS, entry["name"], "Denmark", 56.2, 10.6, 2, 3.5, entry["relation_id"]]
    dk.check_new_row(list(ok), entry, _geom(), "t")
    for bad in (ok[:7], ok[:7] + [None], ok[:7] + [1]):
        with pytest.raises(dk.DkError):
            dk.check_new_row(list(bad), entry, _geom(), "t")


def test_row_identity_kept_for_both_kinds():
    dk._row_identity_kept(list(WAY_ROW), list(WAY_ROW), WAY)
    dk._row_identity_kept([MOLS, "N", "Denmark", 1, 2, 3, 4.0, 7046785],
                          [MOLS, "N", "Denmark", 1, 2, 5, 6.0, 7046785], MOLS)
    for old, new in ((list(WAY_ROW), WAY_ROW + [None]), (WAY_ROW + [None], list(WAY_ROW)),
                     ([MOLS, "N", "Denmark", 1, 2, 3, 4.0, 7046785],
                      [MOLS, "N", "Denmark", 1, 2, 3, 4.0, 1])):
        with pytest.raises(dk.DkError):
            dk._row_identity_kept(old, new, "x")


def test_merge_index_appends_a_way_backed_seven_field_row(tmp_path):
    reg = _registry(tmp_path, _way_entry(center=[55.8, 12.6], bbox=[12.45, 55.71, 12.65, 55.87]))
    old = ["old-area-co", "Old Area", "Colorado", 39.0, -105.0, 1, 1.0, None]
    target = tmp_path / "index.json"
    target.write_bytes(json.dumps([old], ensure_ascii=True).encode("utf-8"))
    work, geoms = tmp_path / "rows", tmp_path / "geom"
    (work / WAY).mkdir(parents=True)
    geoms.mkdir()
    (work / WAY / "index.json").write_text(json.dumps([WAY_ROW]), encoding="utf-8")
    (geoms / f"{WAY}.json").write_text(json.dumps(_geom()), encoding="utf-8")
    sel = tmp_path / "sel.txt"
    sel.write_text(WAY + "\n", encoding="utf-8")
    argv = ["merge-index", "--registry", str(reg), "--selected", str(sel), "--rows-dir", str(work),
            "--geom-dir", str(geoms), "--target", str(target)]
    assert dk.main(argv) == 0
    assert json.loads(target.read_bytes()) == [old, WAY_ROW]
    # an 8-field row with a null relation is refused: old app builds cannot decode a null
    (work / WAY / "index.json").write_text(json.dumps([WAY_ROW + [None]]), encoding="utf-8")
    target.write_bytes(json.dumps([old], ensure_ascii=True).encode("utf-8"))
    assert dk.main(argv) == 1


# ------------------------------------------------------------- publish_areas --
def test_allowlist_pins_a_way_backed_area_to_a_null_relation(tmp_path):
    reg = _registry(tmp_path)
    allowed = publish_areas._exact_publish_allowlist(reg)
    assert allowed[WAY] == ("Testskov", None)
    assert allowed[MOLS] == (publish_areas._MOLS_AREA_NAME, publish_areas._MOLS_RELATION_ID)
    assert publish_areas._exact_way_id(WAY, reg) == WAY_ID
    assert publish_areas._exact_way_id(MOLS, reg) is None
    assert publish_areas._exact_way_id("unknown-dk", reg) is None


@pytest.mark.parametrize("entry", [
    _way_entry(relation_id=5), _way_entry(way_id=0), _way_entry(relation_id=None),
])
def test_allowlist_fails_closed_on_a_bad_way_entry(tmp_path, entry):
    with pytest.raises(ValueError):
        publish_areas._exact_publish_allowlist(_registry(tmp_path, entry))


def test_committed_registry_allowlist_is_unchanged_in_shape():
    reg = dk.load_registry(REGISTRY)
    assert publish_areas._exact_publish_allowlist() == {
        a["id"]: (a.get("display_name") or a["name"], a.get("relation_id")) for a in reg["areas"]}


def _exact_file(tmp_path, osm_type, osm_id, name="Testskov"):
    from shapely.geometry import Polygon, mapping
    geometry = mapping(Polygon([(12.5, 55.76), (12.6, 55.76), (12.6, 55.82), (12.5, 55.82)]))
    geometry = json.loads(json.dumps(geometry))
    path = tmp_path / "exact.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": [{
        "type": "Feature", "geometry": geometry,
        "properties": {"name": name, "osm_type": osm_type, "osm_id": osm_id,
                       "geometry_sha256": publish_areas._canonical_json_sha256(geometry)}}]}),
        encoding="utf-8")
    return path, geometry


def test_load_exact_boundary_accepts_a_way_only_when_allowed(tmp_path):
    path, _ = _exact_file(tmp_path, "way", WAY_ID)
    with pytest.raises(ValueError):
        publish_areas._load_exact_boundary(str(path))                 # default: relations only
    _, _, evidence = publish_areas._load_exact_boundary(str(path), allow_way=True)
    assert (evidence["osm_type"], evidence["osm_id"]) == ("way", WAY_ID)
    rel, _ = _exact_file(tmp_path, "relation", 7046785)
    assert publish_areas._load_exact_boundary(str(rel))[2]["osm_type"] == "relation"


def _publish(tmp_path, reg, exact_path, index_row):
    """Real publish_areas.main (dry-run preview) for the way-backed registry area."""
    work = tmp_path / "pub"
    root = tmp_path / "qa"
    (root / "reports").mkdir(parents=True)
    work.mkdir()
    (work / "published").mkdir()
    (work / "index.json").write_text(json.dumps([index_row]), encoding="utf-8")
    (work / "t.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [{
        "type": "Feature",
        "properties": {"name": "Vejsti", "kind": "trail", "source": "name-stitch", "ckey": "w1",
                       "length_mi": 1.0, "sac_scale": "", "trail_visibility": ""},
        "geometry": {"type": "MultiLineString", "coordinates": [[[12.52, 55.79], [12.56, 55.79]]]}}]}),
        encoding="utf-8")
    argv = ["--trails", str(work / "t.geojson"), "--hiking", str(work / "h.pbf"),
            "--index", str(work / "index.json"), "--out-dir", str(work / "published"),
            "--state", "Denmark", "--dry-run", "--no-boundary-fetch", "--no-elevation",
            "--only-area", WAY, "--exact-boundary", str(exact_path),
            "--preview-json", str(root / "reports" / "p.json"), "--preview-root", str(root),
            "--report-json", str(root / "reports" / "r.json")]
    stderr = io.StringIO()
    defaults = publish_areas._exact_publish_allowlist.__defaults__
    try:
        publish_areas._exact_publish_allowlist.__defaults__ = (reg,)
        publish_areas._exact_way_id.__defaults__ = (reg,)
        publish_areas._exact_osm_name.__defaults__ = (reg,)
        with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
            try:
                rc = publish_areas.main(argv)
            except SystemExit as exit_:
                rc = exit_.code
    finally:
        publish_areas._exact_publish_allowlist.__defaults__ = defaults
        publish_areas._exact_way_id.__defaults__ = (publish_areas._DK_REGISTRY,)
        publish_areas._exact_osm_name.__defaults__ = (publish_areas._DK_REGISTRY,)
    report = root / "reports" / "r.json"
    return rc, stderr.getvalue(), (json.loads(report.read_text()) if report.exists() else None)


def test_publish_areas_way_backed_exact_boundary_end_to_end(tmp_path):
    reg = _registry(tmp_path, _way_entry(center=[55.8, 12.6], bbox=[12.45, 55.71, 12.65, 55.87]))
    exact, _ = _exact_file(tmp_path, "way", WAY_ID)
    rc, err, report = _publish(tmp_path, reg, exact, [WAY, "Testskov", "Denmark", 55.8, 12.6, None, None])
    assert rc == 0, err
    published = report["published_areas"][0]
    assert published["osm_relation_id"] is None and published["osm_way_id"] == WAY_ID
    assert report["exact_boundary"]["osm_type"] == "way"


@pytest.mark.parametrize("osm_type, osm_id, name, row_tail", [
    ("way", WAY_ID + 1, "Testskov", []),               # wrong way id
    ("relation", WAY_ID, "Testskov", []),              # a relation for a way-backed area
    ("way", WAY_ID, "Other Name", []),
    ("way", WAY_ID, "Testskov", [WAY_ID]),             # index row must not carry a relation id
])
def test_publish_areas_refuses_a_wrong_way_boundary(tmp_path, osm_type, osm_id, name, row_tail):
    reg = _registry(tmp_path, _way_entry(center=[55.8, 12.6], bbox=[12.45, 55.71, 12.65, 55.87]))
    exact, _ = _exact_file(tmp_path, osm_type, osm_id, name)
    rc, _err, _ = _publish(tmp_path, reg, exact,
                           [WAY, "Testskov", "Denmark", 55.8, 12.6, None, None, *row_tail])
    assert rc == 2


# --------------------------------------------------------- dk-run-area.sh gate --
def _gate(tmp_path):
    script = (ROOT / "scripts/dk-run-area.sh").read_text(encoding="utf-8").split("\ncd trailforge\n")[0]
    gate = tmp_path / "gate.sh"
    gate.write_text(script, encoding="utf-8")
    return gate


def _run_gate(tmp_path, area_json, **env):
    gate = _gate(tmp_path)
    w = tmp_path / "w"
    (w / "nationalpark-thy-dk").mkdir(parents=True, exist_ok=True)
    (w / "discovery-report.json").write_text(json.dumps({"candidates": []}), encoding="utf-8")
    (w / "nationalpark-thy-dk" / "area.json").write_text(json.dumps(area_json), encoding="utf-8")
    base = {k: v for k, v in os.environ.items() if k not in ("RELATION_ID", "WAY_ID")}
    return subprocess.run(["bash", str(gate)], cwd=ROOT, capture_output=True, text=True, env={
        **base, "AREA_ID": "nationalpark-thy-dk", "AREA_NAME": "Nationalpark Thy",
        "BBOX": "8.05,56.65,8.75,57.15", "W": str(w), **env})


def test_run_area_script_demands_exactly_one_boundary_id(tmp_path):
    ok_area = {"id": "nationalpark-thy-dk", "status": "ok", "cross_check": "registry-way", "way_id": 77}
    both = _run_gate(tmp_path, ok_area, RELATION_ID="7045682", WAY_ID="77")
    neither = _run_gate(tmp_path, ok_area)
    assert both.returncode == 2 and "exactly one of RELATION_ID and WAY_ID" in both.stderr
    assert neither.returncode == 2 and "exactly one of RELATION_ID and WAY_ID" in neither.stderr
    assert _run_gate(tmp_path, ok_area, WAY_ID="abc").returncode != 0
    assert _run_gate(tmp_path, ok_area, WAY_ID="0").returncode != 0


def test_run_area_script_way_gate_needs_the_registry_way_cross_check(tmp_path):
    good = _run_gate(tmp_path, {"id": "nationalpark-thy-dk", "status": "ok",
                                "cross_check": "registry-way", "way_id": 77}, WAY_ID="77")
    assert good.returncode == 0, good.stderr
    for bad in ({"id": "nationalpark-thy-dk", "status": "ok", "cross_check": "registry-relation", "way_id": 77},
                {"id": "nationalpark-thy-dk", "status": "ok", "cross_check": "registry-way", "way_id": 78},
                {"id": "nationalpark-thy-dk", "status": "failed", "cross_check": "registry-way", "way_id": 77}, {}):
        assert _run_gate(tmp_path, bad, WAY_ID="77").returncode != 0, bad


def test_run_area_script_relation_gate_is_unchanged(tmp_path):
    ok = _run_gate(tmp_path, {"id": "nationalpark-thy-dk", "status": "ok",
                              "cross_check": "registry-relation"}, RELATION_ID="7045682")
    assert ok.returncode == 0, ok.stderr
    script = (ROOT / "scripts/dk-run-area.sh").read_text(encoding="utf-8")
    assert '--expected-area-relation-id "$RELATION_ID"' in script
    assert '--expected-area-way-id "$WAY_ID"' in script
    assert "${BOUNDARY_ARGS[@]}" in script.split("assemble/assemble.py", 1)[1]
    assert subprocess.run(["bash", "-n", str(ROOT / "scripts/dk-run-area.sh")]).returncode == 0


def test_workflow_passes_the_way_id_to_the_run_script():
    text = (ROOT / ".github/workflows/trailforge-publish-dk.yml").read_text(encoding="utf-8")
    assert "WAY=\"$(jq -r '.way_id // empty'" in text and 'WAY_ID="$WAY"' in text
    assert "REL=\"$(jq -r '.relation_id // empty'" in text and 'RELATION_ID="$REL"' in text


def test_workflow_adds_selected_way_boundaries_only_when_a_way_area_is_selected():
    text = (ROOT / ".github/workflows/trailforge-publish-dk.yml").read_text(encoding="utf-8")
    step = text.split("- name: Download + prefilter Denmark (once)", 1)[1].split("- name: Discovery", 1)[0]
    assert "GETID_ARGS: ${{ steps.select.outputs.getid_args }}" in step
    assert "grep -E '^w[1-9][0-9]*$'" in step
    assert 'if [ -n "$WAY_IDS" ]; then' in step
    assert step.index("prefilter.sh") < step.index("osmium getid -r") < step.index("osmium merge")
