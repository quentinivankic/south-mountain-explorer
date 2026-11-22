#!/usr/bin/env python3
"""Tests for the Canada leg_merges opt-in (scripts/ca_leg_merge.py, the registry validator and id-plan check in
ca_publish.py, the conditional step in ca-run-area.sh). Every gate has a mutation test: one valid fixture, one
change that breaks exactly that gate, and the refusal asserted."""
import copy
import json
import subprocess
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ca_leg_merge as lm  # noqa: E402
import ca_publish as ca  # noqa: E402
from test_ca_publish import _entry, _registry  # noqa: E402
shapely_geometry = pytest.importorskip("shapely.geometry")
ROOT = Path(__file__).resolve().parent.parent
SCRIPT = (ROOT / "scripts/ca-run-area.sh").read_text()
SOURCE = "https://parks.canada.ca/pn-np/sk/princealbert/activ/experiences/randonee-hiking/etat-sentiers-trail-conditions"
BOX = shapely_geometry.box(-107.0, 53.0, -106.0, 54.0)
LON = -106.5
LAT0 = 53.5
M_LAT = 1 / 110574.0  # degrees of latitude per metre
def seg(start_m, end_m):
    """A north-south line from start_m to end_m metres north of LAT0."""
    return [[LON, LAT0 + start_m * M_LAT], [LON, LAT0 + end_m * M_LAT]]
def feat(name, ways, lines, reason=None):
    return {"type": "Feature",
            "properties": {"name": name, "member_ways": list(ways), "length_mi": 1.0, "removed_reason": reason,
                           "ckey": "w" + "-".join(map(str, ways))},
            "geometry": {"type": "MultiLineString", "coordinates": lines}}
def legs3(gap_ab=0.0, gap_bc=0.0):
    """Three legs in a row: A 0-500 m, B after a gap, C after another gap."""
    a_end = 500.0
    b_start, b_end = a_end + gap_ab, a_end + gap_ab + 500.0
    c_start, c_end = b_end + gap_bc, b_end + gap_bc + 500.0
    return [feat("Leg A", [1], [seg(0, a_end)]), feat("Leg B", [2, 3], [seg(b_start, b_end)]),
            feat("Leg C", [4], [seg(c_start, c_end)])]
def entry(**over):
    base = {"name": "Whole Trail",
            "legs": [{"name": "Leg A", "way_ids": [1]}, {"name": "Leg B", "way_ids": [2, 3]},
                     {"name": "Leg C", "way_ids": [4]}],
            "source": SOURCE, "reason": "agency lists one trail", "new_id": "whole-trail",
            "old_ids": ["leg-a", "leg-b", "leg-c"]}
    base.update(over)
    return base
def other(name="Other Trail"):
    return feat(name, [99], [seg(5000, 5500)])
def build(entries=None, features=None, dropped=None, boundary=BOX):
    return lm.build(entries or [entry()], features if features is not None else legs3() + [other()],
                    dropped if dropped is not None else [], boundary, "Test Park")
def refused(match, **kw):
    with pytest.raises(lm.MergeError, match=match):
        build(**kw)
# ------------------------------------------------------------ validator --
def test_validator_accepts_a_well_formed_entry():
    assert lm.validate_leg_merges([entry()]) == [entry()]
def _drop(key):
    e = entry()
    del e[key]
    return e
def _legs(*specs):
    return [{"name": n, "way_ids": w} for n, w in specs]
TWO = _legs(("Leg A", [1]), ("Leg B", [2]))
@pytest.mark.parametrize("bad", [
    "not-a-list", [],
    [entry(), entry(legs=_legs(("X", [8]), ("Y", [9])), new_id="other", old_ids=["x"])],   # repeated entry name only
    [_drop("source")], [_drop("reason")], [_drop("new_id")], [_drop("old_ids")], [_drop("legs")], [_drop("name")],
    [entry(extra=1)],                                                   # unknown key
    [entry(source="http://parks.canada.ca/x")], [entry(source="https://parks.canada.ca/a b")],
    [entry(reason="  ")], [entry(name="")],
    [entry(legs=_legs(("Leg A", [1])), old_ids=["leg-a"])],            # one leg is not a merge
    [entry(legs=[{"name": f"L{i}", "way_ids": [i + 1]} for i in range(13)], old_ids=["x"])],
    [entry(legs=_legs(("Leg A", [1]), ("Leg A", [2])), old_ids=["a", "b"])],     # same leg name twice, other ways
    [entry(legs=_legs(("Leg A", [1]), ("Leg B", [1])), old_ids=["a", "b"])],     # shared way id
    [entry(legs=_legs(("Leg A", []), ("Leg B", [2])), old_ids=["a", "b"])],
    [entry(legs=_legs(("Leg A", [1, 1]), ("Leg B", [2])), old_ids=["a", "b"])],
    [entry(legs=_legs(("Leg A", [0]), ("Leg B", [2])), old_ids=["a", "b"])],
    [entry(legs=_legs(("Leg A", [True]), ("Leg B", [2])), old_ids=["a", "b"])],
    [entry(legs=_legs(("Leg A", "1"), ("Leg B", [2])), old_ids=["a", "b"])],
    [entry(legs=_legs(("", [1]), ("Leg B", [2])), old_ids=["a", "b"])],
    [entry(legs=_legs((7, [1]), ("Leg B", [2])), old_ids=["a", "b"])],
    [entry(legs=[{"name": "Leg A", "way_ids": [1], "x": 1}, {"name": "Leg B", "way_ids": [2]}], old_ids=["a", "b"])],
    [entry(legs=[{"name": "Leg A"}, {"name": "Leg B", "way_ids": [2]}], old_ids=["a", "b"])],
    [entry(legs=["Leg A", "Leg B"], old_ids=["a", "b"])],
    [entry(legs="Leg A")],
    [entry(max_gap_m=0)], [entry(max_gap_m=101)], [entry(max_gap_m=100.5)], [entry(max_gap_m=True)],
    [entry(max_gap_m="30")], [entry(max_gap_m=-5)],
    [entry(new_id="Whole Trail")], [entry(new_id="whole--trail")], [entry(new_id="")], [entry(new_id=7)],
    [entry(old_ids=[])], [entry(old_ids="leg-a")], [entry(old_ids=["leg-a", "leg-a"])], [entry(old_ids=["Leg A"])],
    [entry(old_ids=[1, 2])],
    [entry(old_ids=["a", "b", "c", "d"])],                              # more old ids than legs
    [entry(), entry(name="Other Whole", legs=_legs(("X", [8]), ("Y", [9])), new_id="whole-trail", old_ids=["x"])],
    [entry(), entry(name="Other Whole", legs=_legs(("X", [8]), ("Y", [9])), new_id="other", old_ids=["leg-a"])],
    [entry(), entry(name="Other Whole", legs=_legs(("X", [3]), ("Y", [9])), new_id="other", old_ids=["x"])],
])
def test_validator_mutation_every_bad_shape_is_refused(bad):
    with pytest.raises(lm.MergeError):
        lm.validate_leg_merges(bad)
def test_validator_allows_the_new_id_to_reuse_one_old_id_and_a_custom_gap():
    ok = entry(old_ids=["leg-a", "whole-trail"], max_gap_m=lm.MAX_GAP_M)
    assert lm.validate_leg_merges([ok]) == [ok]
# ------------------------------------------------- registry loader wiring --
def test_registry_loader_wiring(tmp_path):
    assert ca.load_registry(_registry(tmp_path, [_entry(0, leg_merges=[entry()])]))
    with pytest.raises(ca.CaError, match="leg_merges"):
        ca.load_registry(_registry(tmp_path, [_entry(0, leg_merges=[entry(extra=1)])]))
    way_backed = _entry(0, leg_merges=[entry()])
    del way_backed["relation_id"]
    way_backed["way_id"] = 5
    with pytest.raises(ca.CaError, match="relation-backed"):
        ca.load_registry(_registry(tmp_path, [way_backed]))
def test_registry_mutation_stale_rows_naming_a_merged_leg_are_refused(tmp_path):
    fix = {"osm_name": "Leg A", "name": "Leg Alpha", "source": SOURCE, "reason": "x"}
    keep = {"name": "Leg B", "source": SOURCE, "reason": "x"}
    drop = {"id": "n:Leg C", "name": "Leg C", "reason": "x"}
    for key, row in (("trail_name_fixes", fix), ("keep_trails", keep), ("exclude_trails", drop)):
        with pytest.raises(ca.CaError, match="still named by"):
            ca.load_registry(_registry(tmp_path, [_entry(0, leg_merges=[entry()], **{key: [row]})]))
    unrelated = {"id": "n:Something Else", "name": "Something Else", "reason": "x"}
    assert ca.load_registry(_registry(tmp_path, [_entry(0, leg_merges=[entry()], exclude_trails=[unrelated])]))
INTENDED = {
    "prince-albert-national-park-ca-sk": {"Grey Owl Trail": "grey-owl-trail"},
    "fred-henne-territorial-park-ca-nt": {"Prospector's Trail Loop": "prospector-s-trail-loop"},
    "south-chilcotin-mountains-provincial-park-ca-bc": {"Gun Creek Trail": "gun-creek-trail",
                                                        "Tyaughton Creek Trail": "tyaughton-creek-trail"},
    "riding-mountain-national-park-ca-mb": {"Reeve Ravine": "reeve-ravine"},
}
def test_live_registry_opts_in_exactly_the_intended_areas():
    reg = ca.load_registry()
    got = {a["id"]: {e["name"]: e["new_id"] for e in a["leg_merges"]} for a in reg["areas"] if "leg_merges" in a}
    assert got == INTENDED
    assert all(not a.get("hold") for a in reg["areas"] if "leg_merges" in a)
    assert all(a["state"] == "Canada" and "relation_id" in a for a in reg["areas"] if "leg_merges" in a)
def test_live_registry_pacific_rim_rainforest_loops_are_not_opted_in():
    area = ca.load_registry()["by_id"]["pacific-rim-national-park-reserve-of-canada-ca-bc"]
    assert "leg_merges" not in area      # Loop A and B sit 263 m apart (two loops, not one route)
def test_live_registry_id_plan_matches_the_repo_geoms_before_or_after_the_update():
    """Every planned merge is in the 'before' (all old ids live) or 'after' (new id live, legs gone) state."""
    reg = ca.load_registry()
    for aid, merges in INTENDED.items():
        live = ROOT / "public/areas/geom" / f"{aid}.json"
        live_ids = {t["id"] for t in json.loads(live.read_text())["trails"]}
        for e in reg["by_id"][aid]["leg_merges"]:
            before = set(e["old_ids"]) <= live_ids
            after = e["new_id"] in live_ids and not (set(e["old_ids"]) - {e["new_id"]}) & live_ids
            assert before or after, (aid, e["name"])
def test_live_registry_legs_are_not_named_by_stale_rows_and_ids_are_unique():
    reg = ca.load_registry()
    seen = set()
    for aid in INTENDED:
        for e in reg["by_id"][aid]["leg_merges"]:
            assert not ({e["new_id"], *e["old_ids"]} & seen)
            seen |= {e["new_id"], *e["old_ids"]}
# ------------------------------------------------------ geometry helpers --
def test_leg_gap_is_metres_between_geometries():
    a, b = lm._parts(legs3()[0]["geometry"]), lm._parts(legs3(gap_ab=100)[1]["geometry"])
    assert lm.leg_gap_m(a, b) == pytest.approx(100.0, abs=1.0)
    assert lm.leg_gap_m(a, lm._parts(legs3()[1]["geometry"])) == pytest.approx(0.0, abs=0.01)
def test_widest_join_gap_is_the_spanning_tree_bottleneck_not_the_end_to_end_distance():
    parts = [lm._parts(f["geometry"]) for f in legs3(gap_ab=20, gap_bc=40)]
    assert lm.widest_join_gap(parts) == pytest.approx(40.0, abs=1.0)
    assert lm.leg_gap_m(parts[0], parts[2]) > 500           # A and C are far apart; the chain still joins them
def test_unsupported_geometry_is_refused():
    with pytest.raises(lm.MergeError, match="unsupported"):
        lm._parts({"type": "Point", "coordinates": [0, 0]})
# --------------------------------------------------- gate 1: leg identity --
def test_valid_entry_merges_three_legs_into_one_whole_trail():
    trails, removed, evidence, merged = build()
    names = [f["properties"]["name"] for f in trails]
    assert names == ["Other Trail", "Whole Trail"] and removed == [] and merged == [trails[-1]]
    props = trails[-1]["properties"]
    assert (props["source"], props["kind"], props["area"]) == ("leg-merge", "trail", "Test Park")
    assert props["member_ways"] == [1, 2, 3, 4] and props["ckey"] == "merge-w1-w2-w3-w4"
    assert props["merged_legs"] == ["Leg A", "Leg B", "Leg C"] and props["merge_source"] == SOURCE
    assert props["removed_reason"] is None and props["length_mi"] == pytest.approx(1500 / 1609.344, abs=0.01)
    assert evidence[0]["new_id"] == "whole-trail" and evidence[0]["old_ids"] == ["leg-a", "leg-b", "leg-c"]
    assert evidence[0]["widest_gap_m"] == 0.0 and [leg["from"] for leg in evidence[0]["legs"]] == ["trails"] * 3
def test_merged_geometry_is_the_legs_own_lines_and_never_invents_a_point():
    trails, _, _, _ = build()
    lines = trails[-1]["geometry"]["coordinates"]
    assert len(lines) == 3 and all(len(line) == 2 for line in lines)
    assert sorted(map(tuple, (p for line in lines for p in line))) == sorted(
        map(tuple, (p for f in legs3() for line in f["geometry"]["coordinates"] for p in line)))
def test_inputs_are_not_mutated():
    features = legs3() + [other()]
    before = copy.deepcopy(features)
    build(features=features)
    assert features == before
@pytest.mark.parametrize("make", [
    lambda: dict(features=[f for f in legs3() if f["properties"]["name"] != "Leg C"] + [other()]),         # missing
    lambda: dict(features=[feat("Leg A", [1], [seg(0, 500)]), feat("Leg B", [2], [seg(500, 1000)]),
                           feat("Leg C", [4], [seg(1000, 1500)])]),                       # leg B way set differs
    lambda: dict(features=legs3() + [feat("Leg C", [4], [seg(2000, 2500)])]),              # two matches
    lambda: dict(features=[feat("Leg A", [1], [seg(0, 500)]), feat("Leg B", [2, 3], [seg(500, 1000)]),
                           feat("Leg C", [4, 5], [seg(1000, 1500)])]),                    # leg C has an extra way
])
def test_mutation_every_leg_must_match_exactly_one_feature_by_name_and_ways(make):
    refused("expected exactly 1", **make())
def test_mutation_right_ways_under_another_name_is_not_a_match():
    renamed = [feat("Leg A", [1], [seg(0, 500)]), feat("Leg B?", [2, 3], [seg(500, 1000)]),
               feat("Leg C", [4], [seg(1000, 1500)])]
    refused("matches 0 feature", features=renamed)
def test_mutation_a_leg_without_line_geometry_is_refused():
    bad = legs3()
    bad[1]["geometry"] = {"type": "MultiLineString", "coordinates": []}
    refused("no line geometry", features=bad + [other()])
def test_evidence_reports_the_widest_join_gap():
    assert build(features=legs3(gap_ab=20) + [other()])[2][0]["widest_gap_m"] == pytest.approx(20.0, abs=1.0)
def test_mutation_leg_name_alone_is_not_enough():
    renamed = [feat("Leg A", [1], [seg(0, 500)]), feat("Leg B", [7, 8], [seg(500, 1000)]),
               feat("Leg C", [4], [seg(1000, 1500)])]
    refused("matches 0 feature", features=renamed)
# ---------------------------------------- gate 2: dropped legs are size drops --
@pytest.mark.parametrize("reason", ["0.349 mi is under 0.6 mi", "Too short — 0.09 mi is below the 0.1 mi minimum (x)"])
def test_size_dropped_pieces_can_be_merged_back(reason):
    kept = legs3()[:2] + [other()]
    dropped = [dict(legs3()[2], properties={**legs3()[2]["properties"], "removed_reason": reason})]
    trails, removed, evidence, _ = build(features=kept, dropped=dropped)
    assert [f["properties"]["name"] for f in trails] == ["Other Trail", "Whole Trail"] and removed == []
    assert [leg["from"] for leg in evidence[0]["legs"]] == ["trails", "trails", "removed"]
@pytest.mark.parametrize("reason", ["portage route ('portage')", "registry exclude: x", "winter-only", "", None,
                                    "thru-hike: spans 3 areas"])
def test_mutation_pieces_dropped_for_other_reasons_stay_dropped(reason):
    kept = legs3()[:2] + [other()]
    dropped = [dict(legs3()[2], properties={**legs3()[2]["properties"], "removed_reason": reason})]
    refused("not for being short", features=kept, dropped=dropped)
def test_other_dropped_features_are_left_in_the_removed_list():
    kept = legs3() + [other()]
    stray = feat("Stray", [77], [seg(9000, 9100)], reason="0.1 mi is under 0.6 mi")
    _, removed, _, _ = build(features=kept, dropped=[stray])
    assert removed == [stray]
# ------------------------------------------------ gate 3: gap tolerance --
@pytest.mark.parametrize("gap", [0, 1, 29.0])
def test_gap_within_default_tolerance_merges(gap):
    assert build(features=legs3(gap_ab=gap) + [other()])[3]
def test_mutation_gap_beyond_default_tolerance_is_rejected():
    refused("gap, wider than the 30 m tolerance", features=legs3(gap_ab=60) + [other()])
def test_mutation_gap_between_the_second_and_third_leg_is_rejected_too():
    refused("wider than", features=legs3(gap_bc=250) + [other()])
def test_per_entry_tolerance_can_be_widened_but_is_still_a_limit():
    assert build([entry(max_gap_m=100)], legs3(gap_ab=80) + [other()])[3]
    with pytest.raises(lm.MergeError, match="wider than the 100 m"):
        build([entry(max_gap_m=100)], legs3(gap_ab=140) + [other()])
def test_chain_joined_through_a_middle_leg_is_one_route():
    assert build(features=legs3(gap_ab=20, gap_bc=25) + [other()])[3]
def test_mutation_a_single_disjoint_leg_rejects_the_whole_merge():
    far = [feat("Leg A", [1], [seg(0, 500)]), feat("Leg B", [2, 3], [seg(500, 1000)]),
           feat("Leg C", [4], [seg(4000, 4500)])]
    refused("wider than", features=far + [other()])
def test_real_rainforest_loops_pacific_rim_are_rejected_for_their_263_m_gap():
    """Real endpoints of Rainforest Trail Loop A and B (Pacific Rim, ways 110353817.../694390409...)."""
    a = feat("Rainforest Trail Loop A", [110353817, 1349871675], [[[-125.6813338, 49.0426115], [-125.6812, 49.0435]]])
    b = feat("Rainforest Trail Loop B", [694390409, 1530947444], [[[-125.6817996, 49.0402511], [-125.6819, 49.0394]]])
    e = entry(name="Rainforest Trail", legs=[{"name": a["properties"]["name"], "way_ids": [110353817, 1349871675]},
                                            {"name": b["properties"]["name"], "way_ids": [694390409, 1530947444]}],
              old_ids=["rainforest-trail-loop-a", "rainforest-trail-loop-b"], new_id="rainforest-trail")
    box = shapely_geometry.box(-126.0, 48.0, -125.0, 50.0)
    with pytest.raises(lm.MergeError, match=r"leave a 26\d\.\d m gap"):
        lm.build([e], [a, b], [], box, "Pacific Rim")
def test_real_upper_tyaughton_is_rejected_for_its_1047_m_gap():
    mid = feat("Mid Tyaughton Creek Trail", [1], [[[-123.079508, 51.10554], [-123.07, 51.1]]])
    upper = feat("Upper Tyaughton Creek Trail", [2], [[[-123.0892257, 51.1127408], [-123.1, 51.12]]])
    e = entry(name="Tyaughton Creek Trail", legs=[{"name": mid["properties"]["name"], "way_ids": [1]},
                                                  {"name": upper["properties"]["name"], "way_ids": [2]}],
              old_ids=["mid"], new_id="tyaughton-creek-trail")
    with pytest.raises(lm.MergeError, match=r"leave a 10\d\d\.\d m gap"):
        lm.build([e], [mid, upper], [], shapely_geometry.box(-124, 50, -122, 52), "South Chilcotin")
# -------------------------------------------------- gate 4: inside boundary --
def test_boundary_inside_share_is_enforced_at_80_percent():
    box = shapely_geometry.box(-107.0, LAT0 - 0.01, -106.0, LAT0 + 1500 * 0.85 * M_LAT)
    assert build(boundary=box)[3]                                       # 85% inside: merges
    small = shapely_geometry.box(-107.0, LAT0 - 0.01, -106.0, LAT0 + 1500 * 0.75 * M_LAT)
    refused("inside the boundary", boundary=small)                      # 75% inside: refused
def test_mutation_without_an_exact_area_boundary_is_refused():
    refused("no exact-area boundary", boundary=None)
def test_mutation_boundary_that_misses_the_trail_is_refused():
    refused("inside the boundary", boundary=shapely_geometry.box(10, 10, 11, 11))
# ------------------------------------------------------ gate 5: name clash --
def test_mutation_new_name_of_a_staying_trail_is_refused():
    refused("already has this name", features=legs3() + [other("Whole Trail")])
def test_new_name_may_equal_one_of_its_own_legs():
    e = entry(name="Leg A", old_ids=["leg-a", "leg-b"])
    trails, _, _, _ = build([e])
    assert [f["properties"]["name"] for f in trails if f["properties"]["name"] == "Leg A"] == ["Leg A"]
    assert "Leg B" not in [f["properties"]["name"] for f in trails]
def test_mutation_new_name_clash_is_case_and_accent_insensitive():
    refused("already has this name", features=legs3() + [other("WHOLE TRAIL")])
# ------------------------------------------------------ multiple entries --
def test_two_entries_merge_independently_and_keep_other_trails():
    f = legs3() + [feat("X1", [11], [seg(8000, 8300)]), feat("X2", [12], [seg(8300, 8600)]), other()]
    e2 = entry(name="Second Whole", legs=[{"name": "X1", "way_ids": [11]}, {"name": "X2", "way_ids": [12]}],
               new_id="second-whole", old_ids=["x1", "x2"])
    trails, _, evidence, merged = build([entry(), e2], f)
    assert [x["properties"]["name"] for x in trails] == ["Other Trail", "Whole Trail", "Second Whole"]
    assert len(merged) == 2 and [e["new_id"] for e in evidence] == ["whole-trail", "second-whole"]
# ----------------------------------------------------------------- the CLI --
def _cli(tmp_path, merges, features=None, dropped=None, with_boundary=True):
    registry = tmp_path / "reg.json"
    area = {"id": "t-ca-sk", "name": "Test Park"}
    if merges is not None:
        area["leg_merges"] = merges
    registry.write_text(json.dumps({"areas": [area]}))
    trails = tmp_path / "t.trails.geojson"
    removed = tmp_path / "removed.geojson"
    trails.write_text(json.dumps({"type": "FeatureCollection", "features": features or legs3() + [other()]}))
    removed.write_text(json.dumps({"type": "FeatureCollection", "features": dropped or []}))
    if with_boundary:
        (tmp_path / "t.exact-area.geojson").write_text(json.dumps(
            {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {},
                                                         "geometry": shapely_geometry.mapping(BOX)}]}))
    report = tmp_path / "r.json"
    run = subprocess.run([sys.executable, str(Path(lm.__file__)), "--registry", str(registry), "--area-id", "t-ca-sk",
                          "--trails", str(trails), "--removed", str(removed), "--report", str(report)],
                         capture_output=True, text=True)
    return run, trails, removed, report
def _names(path):
    return [f["properties"]["name"] for f in json.loads(path.read_text())["features"]]
def test_cli_merges_rewrites_both_files_and_reports(tmp_path):
    stray = feat("Stray", [77], [seg(9000, 9100)], reason="0.1 mi is under 0.6 mi")
    short = dict(legs3()[2], properties={**legs3()[2]["properties"], "removed_reason": "0.3 mi is under 0.6 mi"})
    run, trails, removed, report = _cli(tmp_path, [entry()], features=legs3()[:2] + [other()], dropped=[short, stray])
    assert run.returncode == 0, run.stderr
    assert _names(trails) == ["Other Trail", "Whole Trail"] and _names(removed) == ["Stray"]
    doc = json.loads(report.read_text())
    assert doc["area"] == "t-ca-sk" and doc["merged"][0]["new_id"] == "whole-trail"
def test_cli_gap_rejection_leaves_both_files_untouched(tmp_path):
    run, trails, removed, report = _cli(tmp_path, [entry()], features=legs3(gap_ab=500) + [other()])
    assert run.returncode == 2 and "SAFETY BLOCK" in run.stderr and "gap" in run.stderr
    assert _names(trails) == ["Leg A", "Leg B", "Leg C", "Other Trail"] and _names(removed) == [] and not report.exists()
def test_cli_without_boundary_file_fails_closed(tmp_path):
    run, trails, _, report = _cli(tmp_path, [entry()], with_boundary=False)
    assert run.returncode == 2 and "exact-area" in run.stderr and "Whole Trail" not in _names(trails)
    assert not report.exists()
def test_cli_without_registry_entry_fails(tmp_path):
    run, _, _, _ = _cli(tmp_path, None)
    assert run.returncode == 2 and "no leg_merges" in run.stderr
def test_cli_invalid_registry_entry_fails_and_leaves_trails_untouched(tmp_path):
    run, trails, _, _ = _cli(tmp_path, [entry(extra=1)])
    assert run.returncode == 2 and "Whole Trail" not in _names(trails)
def test_cli_unknown_leg_fails_closed(tmp_path):
    e = entry(legs=[{"name": "Leg A", "way_ids": [1]}, {"name": "Nope", "way_ids": [9]}], old_ids=["a"])
    run, trails, _, _ = _cli(tmp_path, [e])
    assert run.returncode == 2 and "expected exactly 1" in run.stderr and "Leg A" in _names(trails)
# ------------------------------------------------------------ the id plan --
def geom(*pairs):
    return {"trails": [{"id": i, "name": n} for i, n in pairs]}
PLAN = {"name": "Whole Trail", "new_id": "whole-trail", "old_ids": ["leg-a", "leg-b", "leg-c"]}
def _live(tmp_path, ids):
    path = tmp_path / "t-ca-sk.json"
    path.write_text(json.dumps(geom(*[(i, i) for i in ids])))
    return path
def test_id_plan_before_state_passes(tmp_path):
    ca.apply_leg_merge_id_plan("t", geom(("whole-trail", "Whole Trail"), ("o", "Other")), [PLAN],
                               _live(tmp_path, ["leg-a", "leg-b", "leg-c", "o"]))
def test_id_plan_after_state_is_idempotent(tmp_path):
    ca.apply_leg_merge_id_plan("t", geom(("whole-trail", "Whole Trail")), [PLAN], _live(tmp_path, ["whole-trail"]))
def test_id_plan_without_a_live_geom_passes(tmp_path):
    ca.apply_leg_merge_id_plan("t", geom(("whole-trail", "Whole Trail")), [PLAN], tmp_path / "missing.json")
def test_id_plan_new_id_may_be_one_of_the_old_ids(tmp_path):
    plan = {"name": "Whole Trail", "new_id": "leg-a", "old_ids": ["leg-a", "leg-b"]}
    ca.apply_leg_merge_id_plan("t", geom(("leg-a", "Whole Trail")), [plan], _live(tmp_path, ["leg-a", "leg-b"]))
    ca.apply_leg_merge_id_plan("t", geom(("leg-a", "Whole Trail")), [plan], _live(tmp_path, ["leg-a"]))
def test_id_plan_mutation_merged_trail_with_another_id_is_refused(tmp_path):
    with pytest.raises(ca.CaError, match="must publish as id 'whole-trail'"):
        ca.apply_leg_merge_id_plan("t", geom(("whole-trail-2", "Whole Trail")), [PLAN],
                                   _live(tmp_path, ["leg-a", "leg-b", "leg-c"]))
def test_id_plan_mutation_missing_merged_trail_is_refused(tmp_path):
    with pytest.raises(ca.CaError, match="must publish as id"):
        ca.apply_leg_merge_id_plan("t", geom(("o", "Other")), [PLAN], _live(tmp_path, ["leg-a", "leg-b", "leg-c"]))
def test_id_plan_mutation_an_old_leg_id_still_published_is_refused(tmp_path):
    with pytest.raises(ca.CaError, match="old leg ids still published"):
        ca.apply_leg_merge_id_plan("t", geom(("whole-trail", "Whole Trail"), ("leg-b", "Leg B")), [PLAN],
                                   _live(tmp_path, ["leg-a", "leg-b", "leg-c"]))
def test_id_plan_mutation_stale_plan_against_the_live_ids_is_refused(tmp_path):
    with pytest.raises(ca.CaError, match="id plan is stale"):
        ca.apply_leg_merge_id_plan("t", geom(("whole-trail", "Whole Trail")), [PLAN], _live(tmp_path, ["leg-a", "x"]))
def test_id_plan_runs_in_finish_publish(tmp_path):
    registry = _registry(tmp_path, [_entry(0, leg_merges=[entry()])])
    area_id = "area-000-ca-ns"
    area = {"id": area_id, "name": "Area 0"}
    (tmp_path / "area.json").write_text(json.dumps(area))
    (tmp_path / "index.json").write_text(json.dumps([[area_id, "Area 0", "Canada", 1, 1, 0, 0, 1000]]))
    (tmp_path / "pub.json").write_text(json.dumps([[area_id, "Area 0", "Canada", 1, 1, 1, 1.0, 1000]]))
    bad = {"id": area_id, "name": "Area 0", "trails": [{"id": "wrong-id", "name": "Whole Trail"}]}
    (tmp_path / "geom.json").write_text(json.dumps(bad))
    live_dir = tmp_path / "live"
    live_dir.mkdir()
    ns = type("A", (), dict(index=tmp_path / "index.json", publish_index=tmp_path / "pub.json",
                            area_json=tmp_path / "area.json", geom=tmp_path / "geom.json", registry=registry,
                            live_geom_dir=live_dir))()
    with pytest.raises(ca.CaError, match="must publish as id 'whole-trail'"):
        ca.finish_publish_cmd(ns)
# ------------------------------------------------------- shell + isolation --
def test_run_area_step_is_conditional_and_ordered():
    step = SCRIPT.index("ca_leg_merge.py")
    assert 'if [ -n "$LM_COUNT" ]; then' in SCRIPT[:step]
    assert 'has("leg_merges")' in SCRIPT[:step]
    assert (SCRIPT.index("assemble.py") < SCRIPT.index("# Optional adopt_ways") < SCRIPT.index("# Optional way_name_overrides")
            < step < SCRIPT.index("ca_trail_filter.py --registry"))
    assert 'SAFETY BLOCK: leg_merges must be a non-empty list on a relation-backed area' in SCRIPT
    assert '--removed "$A/removed.geojson"' in SCRIPT[step:SCRIPT.index("ca_trail_filter.py --registry")]
def test_absent_key_means_no_step():
    block = SCRIPT[SCRIPT.index("# Optional leg_merges"):SCRIPT.index("# Canadian vocabulary")]
    assert block.count("python3 ../scripts/ca_leg_merge.py") == 1
    guard = block[:block.index("python3 ../scripts/ca_leg_merge.py")]
    assert '[ "$LM_COUNT" = "0" ] || [ -z "$RELATION_ID" ]' in guard and "exit 2" in guard
def test_canada_only_no_shared_engine_file_mentions_it():
    for rel in ("trailforge/assemble", "trailforge/extract", "trailforge/serve", ".github/workflows"):
        for path in (ROOT / rel).rglob("*"):
            if path.is_file() and path.suffix in {".py", ".sh", ".yml", ".yaml"}:
                text = path.read_text(encoding="utf-8", errors="ignore")
                assert "leg_merges" not in text and "ca_leg_merge" not in text, path
    importers = {p.name for p in (ROOT / "scripts").glob("*.py") if "ca_leg_merge" in p.read_text(encoding="utf-8")}
    assert importers <= {"ca_publish.py", "ca_leg_merge.py", "test_ca_leg_merge.py"}
def test_no_us_or_denmark_code_reads_the_key():
    for name in ("dk_publish.py", "publish_areas.py"):
        for path in ROOT.rglob(name):
            if ".git" not in path.parts:
                assert "leg_merges" not in path.read_text(encoding="utf-8")
def test_us_and_denmark_registries_do_not_carry_the_key():
    for name in ("dk-areas.json", "golden.json"):
        text = (ROOT / "trailforge/golden" / name).read_text(encoding="utf-8")
        assert "leg_merges" not in text
