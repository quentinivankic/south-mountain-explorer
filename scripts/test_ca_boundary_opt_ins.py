#!/usr/bin/env python3
"""Tests for the three per-area Canada boundary opt-ins (registry span_waiver, keep_park_boundary,
dedupe_ring_way). Each has a control showing the unflagged / non-Canada behavior is unchanged."""
import json
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ca_publish as ca  # noqa: E402
from test_ca_publish import _entry, _registry  # noqa: E402
from test_ca_publish_sync import _osm  # noqa: E402
WAIVER = {"max_deg": 4.5, "reason": "one big park polygon"}
WIDE = (-141.0, 60.0, -137.0, 61.3)                      # 4.0 x 1.3 degrees (Kluane-like)
def _check(entry, info):
    return ca.check_area_geometry(entry, info["bbox"], "qualify", info)
def _info(tmp_path, entry, **osm):
    path = _osm(tmp_path, entry["relation_id"], **osm)
    return ca.boundary_info(path, [entry["relation_id"]])[entry["relation_id"]]
# ------------------------------------------------------------- span_waiver --
def test_span_waiver_lifts_only_the_upper_limit_for_that_area(tmp_path):
    plain = _entry(0, bbox=None, center=None)
    waived = _entry(0, bbox=None, center=None, span_waiver=WAIVER)
    rec = _check(plain, _info(tmp_path, plain, box=WIDE, name="Area 0"))
    assert rec["status"] == "failed" and "implausible" in rec["problems"][0]      # control: unwaived
    rec = _check(waived, _info(tmp_path, waived, box=WIDE, name="Area 0"))
    assert rec["status"] == "ok" and rec["problems"] == []
    # past the waiver, under the lower limit, and outside Canada are still refused
    over = (-141.0, 60.0, -136.0, 61.0)                                            # 5.0 wide > 4.5
    assert "implausible" in _check(waived, _info(tmp_path, waived, box=over, name="Area 0"))["problems"][0]
    tiny = (-63.0, 45.0, -62.999, 45.001)
    assert "implausible" in _check(waived, _info(tmp_path, waived, box=tiny, name="Area 0"))["problems"][0]
    abroad = (10.0, 56.0, 13.9, 56.5)
    assert any("outside Canada" in p for p in _check(
        waived, _info(tmp_path, waived, box=abroad, name="Area 0"))["problems"])
def test_span_waiver_is_per_area_and_the_default_limit_is_unchanged():
    assert ca.REL_SPAN_MAX == 3.0
    assert ca._size_problems([-141.0, 60.0, -137.0, 61.3]) != []
    assert ca._size_problems([-141.0, 60.0, -137.0, 61.3], 4.5) == []
    assert ca._span_max({}) == 3.0 and ca._span_max({"span_waiver": WAIVER}) == 4.5
@pytest.mark.parametrize("bad", [
    {"max_deg": 3.0, "reason": "x"}, {"max_deg": 5.5, "reason": "x"}, {"max_deg": True, "reason": "x"},
    {"max_deg": "4", "reason": "x"}, {"max_deg": 4.0, "reason": " "}, {"max_deg": 4.0},
    {"max_deg": 4.0, "reason": "x", "extra": 1}, 4.0, [],
])
def test_span_waiver_registry_validation_rejects_bad_values(tmp_path, bad):
    with pytest.raises(ca.CaError, match="span_waiver"):
        ca.load_registry(_registry(tmp_path, [_entry(0, span_waiver=bad)]))
    assert ca.load_registry(_registry(tmp_path, [_entry(0, span_waiver=WAIVER)]))
# ------------------------------------------------------- keep_park_boundary --
def _park_info(tags):
    return {"tags": {"type": "multipolygon", "name": "Area 0", **tags}, "count": 1,
            "bbox": [-63.0, 45.0, -62.5, 45.5]}
def test_keep_park_boundary_needs_leisure_park_with_an_operator():
    flagged = _entry(0, keep_park_boundary=True)
    assert ca.relation_problems(flagged, _park_info({"leisure": "park", "operator": "Capital Regional District"})) == []
    for label, tags in (("no operator", {"leisure": "park"}), ("blank operator", {"leisure": "park", "operator": " "}),
                        ("not a park", {"leisure": "nature_reserve", "operator": "CRD"}),
                        ("protected area", {"boundary": "protected_area", "operator": "CRD"})):
        problems = ca.relation_problems(flagged, _park_info(tags))
        assert any("keep_park_boundary" in p for p in problems), label
    # control: an unflagged entry is not subject to the park check at all
    assert ca.relation_problems(_entry(0), _park_info({"leisure": "park"})) == []
def test_keep_park_boundary_registry_validation(tmp_path):
    assert ca.load_registry(_registry(tmp_path, [_entry(0, keep_park_boundary=True)]))
    for bad in (False, "true", 1, None):
        with pytest.raises(ca.CaError, match="keep_park_boundary must be true"):
            ca.load_registry(_registry(tmp_path, [_entry(0, keep_park_boundary=bad)]))
    way = _entry(0, bbox=None, center=None, keep_park_boundary=True)
    del way["relation_id"]
    way["way_id"] = 10
    with pytest.raises(ca.CaError, match="relation-backed"):
        ca.load_registry(_registry(tmp_path, [way]))
def test_select_emits_park_args_only_for_flagged_selected_areas(tmp_path):
    reg = _registry(tmp_path, [_entry(1), _entry(2, keep_park_boundary=True), _entry(3, keep_park_boundary=True)])
    index = tmp_path / "index.json"
    index.write_text("[]", encoding="utf-8")
    def run(areas):
        gh = tmp_path / f"gh-{areas}.txt"
        assert ca.main(["select", "--registry", str(reg), "--index", str(index), "--areas", areas,
                        "--province", "ns", "--mode", "qualify", "--out", str(tmp_path / "sel.txt"),
                        "--github-output", str(gh)]) == 0
        return gh.read_text()
    assert "park_args=r1002 r1003\n" in run("area-001-ca-ns,area-002-ca-ns,area-003-ca-ns")
    assert "park_args=r1002\n" in run("area-002-ca-ns")
    control = run("area-001-ca-ns")                       # nothing flagged: output bytes as before
    assert "park_args" not in control and control.endswith("getid_args=r1001\n")
# ---------------------------------------------------------- dedupe_ring_way --
RING_OSM_TAIL = ('<relation id="1000"><member type="way" ref="10" role="outer"/><tag k="type" v="multipolygon"/>'
                 '<tag k="leisure" v="nature_reserve"/><tag k="name" v="Area 0"/></relation></osm>')
def _ring_pbf(tmp_path, ring_tags, extra=""):
    """Boundary relation 1000 (name Area 0) whose outer ring way 10 carries `ring_tags`."""
    path = _osm(tmp_path, name="Area 0", wtags=ring_tags)
    text = path.read_text()
    text = text[:text.index("<relation")] + extra + RING_OSM_TAIL
    path.write_text(text, encoding="utf-8")
    return path
def _unique(reg, path):
    return ca.main(["check-boundary-unique", "--registry", str(reg), "--area-id", "area-000-ca-ns",
                    "--pbf", str(path)])
def test_dedupe_ring_way_check_requires_a_same_name_outer_ring_way(tmp_path, capsys):
    path = _ring_pbf(tmp_path, '<tag k="boundary" v="protected_area"/><tag k="name" v="Area 0"/>')
    assert ca.ring_way_twins(path, "Area 0", 1000) == [10]
    assert ca.boundary_name_matches(path, "Area 0") == [("relation", 1000)]       # ring ways are ignored here
    assert _unique(_registry(tmp_path, [_entry(0, dedupe_ring_way=True)]), path) == 0
    assert "deduped" in capsys.readouterr().out
    # control: the plain row on the same file still passes the unique check (no flag, no change)
    assert _unique(_registry(tmp_path, [_entry(0)]), path) == 0
    assert "deduped" not in capsys.readouterr().out
def test_dedupe_ring_way_is_stale_without_a_twin_and_ignores_other_names_and_other_relations(tmp_path, capsys):
    reg = _registry(tmp_path, [_entry(0, dedupe_ring_way=True)])
    untagged = _ring_pbf(tmp_path, "")
    assert ca.ring_way_twins(untagged, "Area 0", 1000) == []
    assert _unique(reg, untagged) == 1
    assert "dedupe_ring_way is not needed" in capsys.readouterr().err
    renamed = _ring_pbf(tmp_path, '<tag k="boundary" v="protected_area"/><tag k="name" v="Other Name"/>')
    assert ca.ring_way_twins(renamed, "Area 0", 1000) == []
    twin = _ring_pbf(tmp_path, '<tag k="boundary" v="protected_area"/><tag k="name" v="Area 0"/>')
    assert ca.ring_way_twins(twin, "Area 0", 999) == []                            # not this relation's ring
def test_dedupe_ring_way_does_not_hide_an_unrelated_same_name_boundary(tmp_path, capsys):
    extra = ('<node id="21" lat="46" lon="-60"/><node id="22" lat="46" lon="-59.9"/><node id="23" lat="46.1" lon="-59.9"/>'
             '<way id="30"><nd ref="21"/><nd ref="22"/><nd ref="23"/><nd ref="21"/>'
             '<tag k="boundary" v="protected_area"/><tag k="name" v="Area 0"/></way>')
    path = _ring_pbf(tmp_path, '<tag k="boundary" v="protected_area"/><tag k="name" v="Area 0"/>', extra)
    assert _unique(_registry(tmp_path, [_entry(0, dedupe_ring_way=True)]), path) == 1
    assert "found [('relation', 1000), ('way', 30)]" in capsys.readouterr().err
def test_dedupe_ring_way_registry_validation(tmp_path):
    assert ca.load_registry(_registry(tmp_path, [_entry(0, dedupe_ring_way=True)]))
    for bad in (False, "true", 1, None):
        with pytest.raises(ca.CaError, match="dedupe_ring_way must be true"):
            ca.load_registry(_registry(tmp_path, [_entry(0, dedupe_ring_way=bad)]))
    with pytest.raises(ca.CaError, match="mutually exclusive"):
        ca.load_registry(_registry(tmp_path, [_entry(0, dedupe_ring_way=True, boundary_by_id=True)]))
    way = _entry(0, bbox=None, center=None, dedupe_ring_way=True)
    del way["relation_id"]
    way["way_id"] = 10
    with pytest.raises(ca.CaError, match="relation-backed"):
        ca.load_registry(_registry(tmp_path, [way]))
# ------------------------------------------------- committed registry + shell --
def test_committed_registry_opts_in_exactly_the_three_target_areas():
    reg = ca.load_registry()
    flagged = {k: sorted(a["id"] for a in reg["areas"] if k in a)
               for k in ("span_waiver", "keep_park_boundary", "dedupe_ring_way")}
    assert flagged == {
        "span_waiver": ["kluane-national-park-and-reserve-ca-yt"],
        "keep_park_boundary": ["east-sooke-regional-park-ca-bc"],
        "dedupe_ring_way": ["cypress-hills-interprovincial-park-ca-sk"]}
def test_shell_passes_the_ring_way_flag_only_for_flagged_relation_backed_areas():
    text = (Path(ca.__file__).resolve().parent / "ca-run-area.sh").read_text()
    assert 'DEDUPE_RING_WAY="$(jq' in text and '"--ca-dedupe-ring-way"' in text
    assert text.count("--ca-dedupe-ring-way") == 1
    assert "dedupe_ring_way must be true on a relation-backed area without boundary_by_id" in text
