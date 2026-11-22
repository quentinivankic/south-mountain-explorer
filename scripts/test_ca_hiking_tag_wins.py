#!/usr/bin/env python3
"""Tests for the Canada hiking_tag_wins opt-in (scripts/ca_hiking_tag_wins.py, the registry validator in
ca_publish.py, the conditional step in ca-run-area.sh). Every gate has a mutation test: one valid fixture,
one change that breaks exactly that gate, and the refusal asserted. Negative tests use ski-only ways
(nordic/skitour/downhill pistes with no hiking evidence) and must never get in."""
import copy
import json
import subprocess
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ca_adopt_ways as aw  # noqa: E402
import ca_hiking_tag_wins as htw  # noqa: E402
import ca_publish as ca  # noqa: E402
from test_ca_publish import _entry, _registry  # noqa: E402
shapely_geometry = pytest.importorskip("shapely.geometry")
pytest.importorskip("osmium")
ROOT = Path(__file__).resolve().parent.parent
SCRIPT = (ROOT / "scripts/ca-run-area.sh").read_text()
SRC = "https://parks.canada.ca/pn-np/bc/glacier/activ/randonee-hiking/journee-day"
BOX = shapely_geometry.box(-116.0, 51.0, -115.9, 51.1)
def way(coords, nodes, **tags):
    return {"tags": {"highway": "path", **tags}, "nodes": nodes, "coords": coords}
def chain(**tags):
    """Three ways sharing nodes 2 and 3, all inside BOX, all hiking-tagged ski pistes."""
    return {
        1: way([(-115.95, 51.05), (-115.95, 51.055)], [1, 2], name="Great Glacier Trail", **{"piste:type": "skitour", **tags}),
        2: way([(-115.95, 51.055), (-115.95, 51.06)], [2, 3], name="Great Glacier Trail", **{"piste:type": "skitour", **tags}),
        3: way([(-115.95, 51.06), (-115.95, 51.065)], [3, 4], name="Great Glacier Trail", **{"piste:type": "skitour", **tags}),
    }
HIKING = {7000: {"tags": {"type": "route", "route": "hiking", "name": "Great Glacier Trail"}, "ways": [1, 2, 3]}}
def entry(**over):
    base = {"name": "Great Glacier Trail", "relation": "r7000", "source": SRC, "reason": "Parks Canada day hike"}
    base.update(over)
    return base
def way_entry(**over):
    e = entry(**{"way_ids": [1, 2, 3], **over})
    e.pop("relation", None)
    return e
def trail(name="Other Trail", member_ways=(99,)):
    return {"type": "Feature", "properties": {"name": name, "member_ways": list(member_ways)},
            "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}}
def build(entries=None, ways=None, relations=None, features=None, boundary=BOX, excluded=frozenset()):
    return htw.build(entries or [entry()], relations if relations is not None else HIKING,
                     ways if ways is not None else chain(), features if features is not None else [trail()],
                     boundary, "Test Park", excluded)
def names(result):
    return [f["properties"]["name"] for f in result[0]]
def refused(match, **kw):
    with pytest.raises(htw.HikingTagWinsError, match=match):
        build(**kw)
# ------------------------------------------------ gate 1: opt-in shape (validator) --
def test_validator_accepts_way_and_relation_forms():
    items = [entry(), way_entry(name="Other Hike", max_gap_m=50)]
    items[1]["way_ids"] = [11, 12]
    assert htw.validate_hiking_tag_wins(items) is items
@pytest.mark.parametrize("bad", [
    None, [], "x", {}, [entry(extra=1)], [{"name": "A"}], [entry(source="http://x.y")], [entry(reason=" ")],
    [entry(name="Unnamed path")], [entry(name=" padded ")],
    [entry(way_ids=[1])],                                    # both way_ids and relation
    [{k: v for k, v in entry().items() if k != "relation"}],  # neither
    [entry(relation="7000")], [entry(relation="r0")], [entry(relation="r-5")], [entry(relation=7000)],
    [way_entry(way_ids=[])], [way_entry(way_ids=[1, 1])], [way_entry(way_ids=[True])], [way_entry(way_ids=list(range(1, 62)))],
    [way_entry(max_gap_m=0)], [way_entry(max_gap_m=101)],
    [entry(), entry(name="Other")],                          # same relation twice
    [entry(), entry(relation="r7001")],                      # same name twice
    [way_entry(), way_entry(name="Other", way_ids=[3, 4])],  # shared way id
])
def test_validator_mutation_every_bad_shape_is_refused(bad):
    with pytest.raises(htw.HikingTagWinsError):
        htw.validate_hiking_tag_wins(bad)
def test_registry_loader_wiring(tmp_path):
    good = _entry(0, hiking_tag_wins=[entry()])
    assert ca.load_registry(_registry(tmp_path, [good]))
    with pytest.raises(ca.CaError, match="hiking_tag_wins"):
        ca.load_registry(_registry(tmp_path, [_entry(0, hiking_tag_wins=[entry(extra=1)])]))
    with pytest.raises(ca.CaError, match="relation-backed"):
        way_backed = _entry(0, hiking_tag_wins=[entry()])
        del way_backed["relation_id"]
        way_backed["way_id"] = 5
        ca.load_registry(_registry(tmp_path, [way_backed]))
    adopt = {"name": "Johnson Loop", "way_ids": [1, 9], "source": SRC, "reason": "listed"}
    with pytest.raises(ca.CaError, match="both list way ids"):
        ca.load_registry(_registry(tmp_path, [_entry(0, adopt_ways=[adopt], hiking_tag_wins=[way_entry()])]))
def test_live_registry_loads_and_intended_areas_are_opted_in():
    reg = ca.load_registry()
    got = {a["id"]: [e["name"] for e in a["hiking_tag_wins"]] for a in reg["areas"] if "hiking_tag_wins" in a}
    assert got == {
        "garibaldi-provincial-park-ca-bc": ["Garibaldi Lake Trail", "Elfin Lakes Trail"],
        "glacier-national-park-ca-bc": ["Great Glacier Trail", "Meeting of the Waters Trail", "Avalanche Crest Trail"],
        "jasper-national-park-ca-ab": ["Bald Hills Trail"],
        "banff-national-park-ca-ab": ["Healy Pass Trail"],
        "e-c-manning-provincial-park-ca-bc": ["Frosty Mountain"],
    }
    # Garibaldi stays held (Black Tusk and Panorama Ridge are still stubs); every other opted-in area is live.
    assert [a["id"] for a in reg["areas"] if "hiking_tag_wins" in a and a.get("hold")] == ["garibaldi-provincial-park-ca-bc"]
    assert all(a["id"].endswith(("-ca-bc", "-ca-ab")) for a in reg["areas"] if "hiking_tag_wins" in a)
    flagged = {(a["id"], e["name"]): len(e["way_ids"]) for a in reg["areas"] for e in a.get("hiking_tag_wins", [])
               if "named_sac_evidence" in e}
    assert flagged == {("garibaldi-provincial-park-ca-bc", "Garibaldi Lake Trail"): 13,
                       ("garibaldi-provincial-park-ca-bc", "Elfin Lakes Trail"): 13}
    assert all(e["named_sac_evidence"] is True for a in reg["areas"] for e in a.get("hiking_tag_wins", [])
               if "named_sac_evidence" in e)
# ------------------------------------------------ gate 2: piste tag present, skitour/nordic only --
def test_valid_relation_and_way_forms_build_one_feature():
    for item in (entry(), way_entry()):
        new, evidence, replaced = build([item])
        assert [f["properties"]["name"] for f in new] == ["Great Glacier Trail"]
        props = new[0]["properties"]
        assert props["source"] == "hiking-tag-wins" and props["ckey"] == "htw-w1-w2-w3"
        assert props["member_ways"] == [1, 2, 3] and not replaced
        assert evidence[0]["hiking_relations"] == [7000]
def test_mutation_way_form_needs_a_piste_tag():
    ways = chain()
    del ways[2]["tags"]["piste:type"]
    refused("no piste:type", entries=[way_entry()], ways=ways)
    build([entry()], ways=ways)            # control: relation form allows a plain member next to piste ways
@pytest.mark.parametrize("piste", ["downhill", "sled", "alpine", "snow_park", "nordic;fatbike", "skitour;downhill", "hike", ""])
def test_mutation_other_piste_values_stay_out(piste):
    ways = chain()
    ways[2]["tags"]["piste:type"] = piste
    refused("piste:type", ways=ways)
@pytest.mark.parametrize("piste", ["skitour", "nordic", "nordic;skitour", "Nordic"])
def test_piste_values_that_may_be_overridden(piste):
    ways = chain()
    ways[2]["tags"]["piste:type"] = piste
    assert names(build(ways=ways)) == ["Great Glacier Trail"]
def test_relation_form_needs_one_piste_way():
    ways = chain()
    for w in ways.values():
        del w["tags"]["piste:type"]
    refused("no piste-tagged member", ways=ways)
# ------------------------------------------------ gate 3: hiking evidence (ski-only ways never get in) --
def test_ski_only_ways_are_refused_in_way_form():
    ways = chain()
    refused("not in a route=hiking relation", entries=[way_entry()], ways=ways, relations={})
def test_ski_only_bare_piste_ways_with_foot_yes_stay_out():
    # Minnesing / Lac Louise shape: nordic piste, foot=yes, no hiking relation, no foot=designated.
    ways = chain(foot="yes")
    refused("not in a route=hiking relation", entries=[way_entry()], ways=ways, relations={})
@pytest.mark.parametrize("route", ["ski", "piste", "foot", "walking", "bicycle", "mtb", ""])
def test_mutation_only_a_route_hiking_relation_counts(route):
    relations = {7000: {"tags": {"type": "route", "route": route}, "ways": [1, 2, 3]}}
    refused("not in a route=hiking relation", entries=[way_entry()], relations=relations)
def test_mutation_relation_must_be_type_route():
    relations = {7000: {"tags": {"type": "multipolygon", "route": "hiking"}, "ways": [1, 2, 3]}}
    refused("not in a route=hiking relation", entries=[way_entry()], relations=relations)
    build([way_entry()], relations=HIKING)   # control
def test_foot_designated_is_evidence_without_a_relation():
    assert names(build([way_entry()], ways=chain(foot="designated"), relations={})) == ["Great Glacier Trail"]
def test_ski_relation_does_not_lend_evidence_to_a_listed_way():
    relations = {7000: {"tags": {"type": "route", "route": "piste", "piste:type": "nordic"}, "ways": [1, 2, 3]}}
    refused("a ski-only way stays out", entries=[way_entry()], relations=relations)
def test_name_connected_evidence_only_inside_the_entry():
    # Healy Pass shape: one way is in the hiking relation, the others have foot=yes and the same name.
    ways = chain(foot="yes")
    relations = {7000: {"tags": {"type": "route", "route": "hiking"}, "ways": [1]}}
    assert names(build([way_entry()], ways=ways, relations=relations)) == ["Great Glacier Trail"]
    ways[3]["tags"]["name"] = "Some Ski Loop"            # mutation: different name, still foot=yes
    refused("way 3: not in a route=hiking", entries=[way_entry()], ways=ways, relations=relations)
    ways = chain()                                       # mutation: same name but no foot tag
    refused("way 2: not in a route=hiking", entries=[way_entry()], ways=ways, relations=relations)
    ways = chain(foot="yes")                             # mutation: nobody in the entry has evidence
    refused("not in a route=hiking", entries=[way_entry()], ways=ways, relations={})
# ------------------------------------------------ gate 4: highway=track needs a positive foot tag --
def test_mutation_track_needs_foot_yes_or_designated():
    ways = chain()
    ways[2]["tags"]["highway"] = "track"
    refused("highway=track needs foot", ways=ways)
    ways[2]["tags"]["foot"] = "yes"
    assert names(build(ways=ways)) == ["Great Glacier Trail"]
    ways[2]["tags"]["foot"] = "permissive"
    refused("highway=track needs foot", ways=ways)
@pytest.mark.parametrize("key", ["motor_vehicle", "motorcar", "vehicle"])
def test_mutation_track_with_motor_access_stays_out(key):
    ways = chain()
    ways[2]["tags"].update(highway="track", foot="yes", **{key: "yes"})
    refused("highway=track with", ways=ways)
# ------------------------------------------------ gate 5: the adopt_ways way checks still apply --
@pytest.mark.parametrize("tags,why", [
    ({"highway": "cycleway"}, "highway='cycleway'"), ({"highway": "service"}, "highway='service'"),
    ({"highway": "unclassified"}, "highway='unclassified'"), ({"highway": None}, "highway=None"),
    ({"access": "private"}, "access=private"), ({"access": "no"}, "access=no"), ({"foot": "no"}, "foot=no"),
    ({"mtb:scale": "2"}, "mtb:scale"),
])
def test_mutation_unwalkable_ways_stay_out(tags, why):
    ways = chain()
    ways[2]["tags"].update(tags)
    with pytest.raises(htw.HikingTagWinsError, match=why):
        build(ways=ways)
def test_adopt_ways_default_is_unchanged_and_still_refuses_a_track():
    ways = {1: way([(-115.95, 51.05), (-115.95, 51.055)], [1, 2], highway="track", foot="yes")}
    item = {"name": "T", "way_ids": [1], "source": SRC, "reason": "x"}
    with pytest.raises(aw.AdoptError, match="highway='track'"):
        aw.build_features([item], ways, [], BOX, "Test Park")
    new, _ = aw.build_features([item], ways, [], BOX, "Test Park", frozenset(), aw.ALLOWED_HIGHWAY | {"track"})
    assert len(new) == 1
# ------------------------------------------------ gate 6: relation form --
def test_relation_must_be_in_the_extract_and_be_route_hiking():
    refused("is not in the extract", entries=[entry(relation="r9")])
    relations = {7000: {"tags": {"type": "route", "route": "ski"}, "ways": [1, 2, 3]}}
    refused("not type=route route=hiking", relations=relations)
    relations = {7000: {"tags": {"route": "hiking"}, "ways": [1, 2, 3]}}
    refused("not type=route route=hiking", relations=relations)
def test_relation_members_outside_the_extract_are_skipped_not_invented():
    relations = {7000: {"tags": {"type": "route", "route": "hiking"}, "ways": [1, 2, 3, 99]}}
    new, evidence, _ = build(relations=relations)
    assert new[0]["properties"]["member_ways"] == [1, 2, 3]
def test_relation_whole_route_includes_plain_members():
    ways = chain()
    ways[4] = way([(-115.95, 51.065), (-115.95, 51.07)], [4, 5], name="Great Glacier Trail")
    relations = {7000: {"tags": {"type": "route", "route": "hiking"}, "ways": [1, 2, 3, 4]}}
    new, _, _ = build(ways=ways, relations=relations)
    assert new[0]["properties"]["member_ways"] == [1, 2, 3, 4]
def test_relation_plain_member_must_still_be_walkable():
    ways = chain()
    ways[4] = way([(-115.95, 51.065), (-115.95, 51.07)], [4, 5], highway="service")
    relations = {7000: {"tags": {"type": "route", "route": "hiking"}, "ways": [1, 2, 3, 4]}}
    refused("highway='service'", ways=ways, relations=relations)
def test_way_owned_by_another_assembled_trail_stays_with_it():
    owner = trail("Maligne Pass Trail", member_ways=(3, 77))
    new, evidence, _ = build(features=[trail(), owner])
    assert new[0]["properties"]["member_ways"] == [1, 2]
    assert evidence[0]["left_with_other_trails"] == [3]
    assert owner["properties"]["member_ways"] == [3, 77]
# ------------------------------------------------ gate 7: same-name trails --
def test_same_name_trail_from_the_routes_own_ways_is_replaced():
    partial = trail("Great Glacier Trail", member_ways=(3,))
    new, evidence, replaced = build(features=[trail(), partial])
    assert replaced == [partial] and evidence[0]["replaced_partial_trails"] == ["Great Glacier Trail"]
    assert new[0]["properties"]["member_ways"] == [1, 2, 3]
def test_mutation_same_name_trail_with_other_ways_is_a_clash():
    refused("already has this name", features=[trail("Great Glacier Trail", member_ways=(3, 99))])
    refused("already has this name", features=[trail("great glacier trail", member_ways=(99,))])
def test_mutation_same_name_trail_without_member_ways_is_a_clash():
    refused("already has this name", features=[trail("Great Glacier Trail", member_ways=())])
def test_mutation_other_names_never_replaced():
    other = trail("Another Trail", member_ways=(3,))
    refused("already in an assembled trail", features=[other], entries=[way_entry()])
def test_way_form_replaces_a_partial_listed_in_full():
    ways = chain()
    ways[9] = way([(-115.95, 51.065), (-115.95, 51.07)], [4, 5], highway="path", name="Great Glacier Trail",
                  **{"piste:type": "hike"})
    partial = trail("Great Glacier Trail", member_ways=(9,))
    item = way_entry()
    item["way_ids"] = [1, 2, 3, 9]
    new, _, replaced = build([item], ways=ways, features=[partial])
    assert replaced == [partial] and new[0]["properties"]["member_ways"] == [1, 2, 3, 9]
    item["way_ids"] = [1, 2, 3]                       # mutation: partial not listed in full -> clash
    refused("already has this name", entries=[item], ways=ways, features=[partial])
def test_excluded_trail_does_not_clash():
    owner = trail("Great Glacier Trail", member_ways=(3, 99))
    new, _, _ = build(features=[owner], excluded=frozenset({"Great Glacier Trail"}))
    assert names((new,)) == ["Great Glacier Trail"]
# ------------------------------------------------ gate 8: chaining and boundary (adopt_ways checks) --
def test_mutation_ways_must_chain():
    ways = chain()
    ways[3] = way([(-115.95, 51.08), (-115.95, 51.085)], [8, 9], name="Great Glacier Trail", **{"piste:type": "skitour"})
    refused("do not chain", ways=ways)
    assert names(build([entry(max_gap_m=3000)], ways=ways)) == ["Great Glacier Trail"]
def test_mutation_must_be_inside_the_area():
    refused("inside the boundary", boundary=shapely_geometry.box(-114.0, 51.0, -113.9, 51.1))
    refused("exact-area boundary", boundary=None)
def test_mutation_missing_way_in_way_form():
    refused("missing from the extract", entries=[way_entry(way_ids=[1, 2, 3, 77])])
def test_mutation_too_many_ways():
    ways = {i: way([(-115.95, 51.0 + i * 1e-4), (-115.95, 51.0 + (i + 1) * 1e-4)], [i, i + 1], name="G",
                   **{"piste:type": "skitour"}) for i in range(1, 62)}
    relations = {7000: {"tags": {"type": "route", "route": "hiking"}, "ways": list(ways)}}
    refused("more than 60", ways=ways, relations=relations)
# ------------------------------------------------ gate 9: named_sac_evidence (Garibaldi Lake / Elfin Lakes) --
SAC = {"foot": "yes", "sac_scale": "hiking"}
def sac_chain(**tags):
    """Way 1 is the plain hiking way (the assembled stub, no piste tag); ways 2 and 3 are skitour-tagged ways
    of the same trail with foot=yes and a hiking sac_scale. No hiking relation exists."""
    ways = chain(**{**SAC, **tags})
    del ways[1]["tags"]["piste:type"]
    return ways
def sac_stub():
    return trail("Great Glacier Trail", member_ways=(1,))
def sac_entry(**over):
    return way_entry(**{"named_sac_evidence": True, **over})
def sac_build(ways=None, entries=None, features=None, **kw):
    return build(entries or [sac_entry()], ways=ways if ways is not None else sac_chain(), relations={},
                 features=features if features is not None else [trail(), sac_stub()], **kw)
def sac_refused(match, ways=None, entries=None, features=None):
    with pytest.raises(htw.HikingTagWinsError, match=match):
        sac_build(ways=ways, entries=entries, features=features)
def test_named_sac_evidence_keeps_foot_yes_sac_scale_piste_ways_named_like_a_plain_way():
    new, evidence, replaced = sac_build()
    assert [f["properties"]["name"] for f in new] == ["Great Glacier Trail"]
    assert new[0]["properties"]["member_ways"] == [1, 2, 3] and len(replaced) == 1
    assert evidence[0]["named_sac_evidence_ways"] == [2, 3] and evidence[0]["hiking_relations"] == []
@pytest.mark.parametrize("sac", ["hiking", "mountain_hiking", "demanding_mountain_hiking"])
def test_named_sac_evidence_accepts_the_hiking_sac_scales(sac):
    ways = sac_chain(sac_scale=sac)
    ways[1]["tags"]["sac_scale"] = sac
    assert names(sac_build(ways=ways)) == ["Great Glacier Trail"]
def test_mutation_without_the_flag_the_same_ways_are_refused():
    with pytest.raises(htw.HikingTagWinsError, match="not in a route=hiking relation"):
        build([way_entry()], ways=sac_chain(), relations={}, features=[trail(), sac_stub()])
    assert names(sac_build()) == ["Great Glacier Trail"]          # control: flag on
@pytest.mark.parametrize("sac", [None, "", "none", "alpine_hiking", "demanding_alpine_hiking", "difficult_alpine_hiking",
                                 "ski", "Hiking"])
def test_mutation_needs_a_hiking_sac_scale(sac):
    ways = sac_chain()
    for i in (2, 3):
        if sac is None:
            del ways[i]["tags"]["sac_scale"]
        else:
            ways[i]["tags"]["sac_scale"] = sac
    sac_refused("not in a route=hiking relation", ways=ways)
@pytest.mark.parametrize("foot", [None, "no", "permissive", "private", "customers", "Yes"])
def test_mutation_needs_foot_yes(foot):
    ways = sac_chain()
    if foot is None:
        del ways[3]["tags"]["foot"]
    else:
        ways[3]["tags"]["foot"] = foot
    sac_refused("way 3: not in a route=hiking", ways=ways)
def test_mutation_needs_the_exact_name_of_a_plain_way_of_the_entry():
    for other in ("Great Glacier Trail ", "great glacier trail", "Great Glacier Trail Loop", "", None):
        ways = sac_chain()
        if other is None:
            del ways[3]["tags"]["name"]
        else:
            ways[3]["tags"]["name"] = other
        with pytest.raises(htw.HikingTagWinsError):
            sac_build(ways=ways)
def test_mutation_ski_only_trail_has_no_plain_anchor_and_stays_out():
    # Every way of the trail is a skitour way with foot=yes and sac_scale: nothing non-piste to name-match.
    ways = chain(**SAC)
    sac_refused("not in a route=hiking relation", ways=ways, features=[trail()])
    nordic = chain(**{**SAC, "piste:type": "nordic"})
    sac_refused("not in a route=hiking relation", ways=nordic, features=[trail()])
def test_mutation_the_anchor_must_not_carry_a_piste_tag():
    ways = sac_chain()
    ways[1]["tags"]["piste:type"] = "skitour"
    ways[1]["tags"].pop("name")                               # piste-tagged, unnamed, is no anchor either
    sac_refused("way 2: not in a route=hiking", ways=ways, features=[trail()])
    ways = sac_chain()
    ways[1]["tags"]["piste:type"] = "downhill"
    sac_refused("piste:type=downhill", ways=ways, features=[trail()])
def test_mutation_an_anchor_in_another_entry_does_not_count():
    ways = sac_chain()
    ways[4] = way([(-115.95, 51.065), (-115.95, 51.07)], [4, 5], name="Other Hike", **SAC)
    ways[5] = way([(-115.95, 51.07), (-115.95, 51.075)], [5, 6], name="Other Hike", **{**SAC, "piste:type": "skitour"})
    ways[6] = way([(-115.95, 51.08), (-115.95, 51.085)], [8, 9], name="Great Glacier Trail", **{**SAC, "piste:type": "skitour"})
    first = sac_entry(way_ids=[1, 2, 3])
    second = sac_entry(name="Other Hike", way_ids=[4, 5, 6])
    stubs = [trail(), sac_stub(), trail("Other Hike", member_ways=(4,))]
    refused("way 6: not in a route=hiking", entries=[first, second], ways=ways, relations={}, features=stubs)
    second["way_ids"] = [4, 5]                                  # control: the entry that owns an anchor passes
    assert names(build([first, second], ways=ways, relations={}, features=stubs)) == [
        "Great Glacier Trail", "Other Hike"]
@pytest.mark.parametrize("piste", ["downhill", "sled", "alpine", "snow_park", "nordic;fatbike", "skitour;downhill", "hike", ""])
def test_mutation_other_piste_values_stay_out_with_the_flag(piste):
    ways = sac_chain()
    ways[3]["tags"]["piste:type"] = piste
    sac_refused("piste:type", ways=ways)
def test_flag_keeps_the_track_gate():
    ways = sac_chain()
    ways[3]["tags"]["highway"] = "track"
    assert names(sac_build(ways=ways)) == ["Great Glacier Trail"]      # foot=yes track, no motor tag
    for key in ("motor_vehicle", "motorcar", "vehicle"):
        bad = sac_chain()
        bad[3]["tags"].update(highway="track", **{key: "yes"})
        sac_refused("highway=track with", ways=bad)
    nofoot = sac_chain()
    nofoot[3]["tags"].update(highway="track", foot="permissive")
    sac_refused("highway=track needs foot", ways=nofoot)
@pytest.mark.parametrize("tags,why", [
    ({"highway": "cycleway"}, "highway='cycleway'"), ({"highway": "service"}, "highway='service'"),
    ({"highway": "unclassified"}, "highway='unclassified'"), ({"access": "private"}, "access=private"),
    ({"mtb:scale": "2"}, "mtb:scale"), ({"mtb:scale": "5"}, "mtb:scale"), ({"mtb:scale": ""}, "mtb:scale"),
    ({"mtb:scale": "1;2"}, "mtb:scale"), ({"mtb:scale:uphill": "3"}, "never-matches"),
])
def test_flag_keeps_the_unwalkable_way_checks(tags, why):
    ways = sac_chain()
    ways[3]["tags"].update(tags)
    if why == "never-matches":                                   # a different tag key is not tolerated or checked
        assert names(sac_build(ways=ways)) == ["Great Glacier Trail"]
    else:
        with pytest.raises(htw.HikingTagWinsError, match=why):
            sac_build(ways=ways)
def test_flag_tolerates_access_no_only_with_foot_and_a_hiking_sac_scale():
    ways = sac_chain(access="no")                                 # footbridge: foot=yes overrides access=no
    new, evidence, _ = sac_build(ways=ways)
    assert names((new,)) == ["Great Glacier Trail"] and evidence[0]["tolerated_access_or_mtb_ways"] == [1, 2, 3]
    ways[3]["tags"]["foot"] = "designated"
    assert names(sac_build(ways=ways)) == ["Great Glacier Trail"]
    for key, bad in (("foot", "permissive"), ("sac_scale", "alpine_hiking"), ("sac_scale", None)):
        mutated = sac_chain(access="no")
        if bad is None:
            del mutated[1]["tags"][key]
        else:
            mutated[1]["tags"][key] = bad
        with pytest.raises(htw.HikingTagWinsError, match="access=no"):
            sac_build(ways=mutated)
    no_foot = sac_chain(access="no")
    del no_foot[1]["tags"]["foot"]
    with pytest.raises(htw.HikingTagWinsError, match="access=no"):
        sac_build(ways=no_foot)
    plain = chain(access="no")                                    # control: no flag, the adopt rule is unchanged
    with pytest.raises(htw.HikingTagWinsError, match="access=no"):
        build([way_entry()], ways=plain)
    unflagged = sac_chain(access="no")                            # same tags as a flagged entry, flag absent
    with pytest.raises(htw.HikingTagWinsError, match="access=no"):
        build([way_entry()], ways=unflagged, relations=HIKING, features=[trail(), sac_stub()])
    with pytest.raises(htw.HikingTagWinsError, match="mtb:scale"):
        build([way_entry()], ways=sac_chain(**{"mtb:scale": "1"}), relations=HIKING, features=[trail(), sac_stub()])
def test_flag_tolerates_easy_mtb_scale_only_with_foot_and_a_hiking_sac_scale():
    for scale in ("0", "1"):
        ways = sac_chain(**{"mtb:scale": scale})
        new, evidence, _ = sac_build(ways=ways)
        assert names((new,)) == ["Great Glacier Trail"] and evidence[0]["tolerated_access_or_mtb_ways"] == [1, 2, 3]
    for scale in ("2", "3", "0+", "x"):
        with pytest.raises(htw.HikingTagWinsError, match="mtb:scale"):
            sac_build(ways=sac_chain(**{"mtb:scale": scale}))
    for key in ("foot", "sac_scale"):
        ways = sac_chain(**{"mtb:scale": "1"})
        del ways[1]["tags"][key]
        with pytest.raises(htw.HikingTagWinsError, match="mtb:scale"):
            sac_build(ways=ways)
    with pytest.raises(htw.HikingTagWinsError, match="mtb:scale"):    # control: no flag
        build([way_entry()], ways=chain(**{"mtb:scale": "1"}), relations=HIKING)
@pytest.mark.parametrize("truthy", ["true", "yes", 1, [True], False, None])
def test_mutation_build_acts_only_on_the_literal_true(truthy):
    sac_refused("not in a route=hiking", entries=[sac_entry(named_sac_evidence=truthy)])
def test_flag_in_relation_form_adds_nothing():
    relations = {7000: {"tags": {"type": "route", "route": "hiking"}, "ways": [1, 2, 3]}}
    ways = sac_chain(access="no")
    with pytest.raises(htw.HikingTagWinsError, match="access=no"):
        build([entry(named_sac_evidence=True)], ways=ways, relations=relations, features=[trail(), sac_stub()])
def test_flag_does_not_touch_the_input_ways_or_other_entries():
    ways = sac_chain(access="no", **{"mtb:scale": "1"})
    before = copy.deepcopy(ways)
    sac_build(ways=ways)
    assert ways == before
def test_flag_still_needs_chaining_boundary_and_name_checks():
    ways = sac_chain()
    ways[3] = way([(-115.95, 51.08), (-115.95, 51.085)], [8, 9], name="Great Glacier Trail", **{**SAC, "piste:type": "skitour"})
    sac_refused("do not chain", ways=ways)
    with pytest.raises(htw.HikingTagWinsError, match="inside the boundary"):
        sac_build(boundary=shapely_geometry.box(-114.0, 51.0, -113.9, 51.1))
    # a same-name trail holding other ways is not the entry's shorter copy: its way 1 is not replaced, so the
    # plain way has no assembled stub behind it and the entry is refused
    sac_refused("way 1: no piste:type", features=[trail("Great Glacier Trail", member_ways=(1, 99))])
def test_flag_does_not_replace_a_trail_with_another_name():
    other = trail("Another Trail", member_ways=(1,))
    with pytest.raises(htw.HikingTagWinsError, match="way 1: no piste:type"):   # another trail's way is not ours
        sac_build(features=[other])
@pytest.mark.parametrize("bad", [False, None, "true", 1, "yes", []])
def test_validator_mutation_flag_must_be_exactly_true(bad):
    with pytest.raises(htw.HikingTagWinsError, match="named_sac_evidence"):
        htw.validate_hiking_tag_wins([sac_entry(named_sac_evidence=bad)])
def test_validator_mutation_flag_is_way_ids_only():
    with pytest.raises(htw.HikingTagWinsError, match="way_ids only"):
        htw.validate_hiking_tag_wins([entry(named_sac_evidence=True)])
    assert htw.validate_hiking_tag_wins([sac_entry()])
def test_registry_loader_accepts_and_rejects_the_flag(tmp_path):
    assert ca.load_registry(_registry(tmp_path, [_entry(0, hiking_tag_wins=[sac_entry()])]))
    with pytest.raises(ca.CaError, match="named_sac_evidence"):
        ca.load_registry(_registry(tmp_path, [_entry(0, hiking_tag_wins=[entry(named_sac_evidence=True)])]))
def test_cli_named_sac_evidence_on_a_real_extract(tmp_path):
    plain = {"highway": "path", "name": "Great Glacier Trail", **SAC}
    osm = _osm(_way_xml(1, [1, 2], plain) + _way_xml(2, [2, 3], {**plain, "piste:type": "skitour"}))
    stub = trail("Great Glacier Trail", member_ways=(1,))
    run, trails, report = _cli(tmp_path, [sac_entry(way_ids=[1, 2])], osm=osm, features=[trail(), stub])
    assert run.returncode == 0, run.stderr
    assert _names(trails) == ["Other Trail", "Great Glacier Trail"]
    assert json.loads(report.read_text())["kept"][0]["named_sac_evidence_ways"] == [2]
    ski_only = _osm(_way_xml(1, [1, 2], {**plain, "piste:type": "nordic"}) + _way_xml(2, [2, 3], {**plain, "piste:type": "nordic"}))
    (tmp_path / "ski").mkdir()
    run, trails, report = _cli(tmp_path / "ski", [sac_entry(way_ids=[1, 2])], osm=ski_only)
    assert run.returncode == 2 and "ski-only" in run.stderr and _names(trails) == ["Other Trail"] and not report.exists()
    (tmp_path / "noflag").mkdir()
    run, trails, _ = _cli(tmp_path / "noflag", [way_entry(way_ids=[1, 2])], osm=osm, features=[trail(), stub])
    assert run.returncode == 2 and _names(trails) == ["Other Trail", "Great Glacier Trail"] and "SAFETY BLOCK" in run.stderr
def test_flagged_opt_in_is_confined_to_one_area_and_the_two_trails():
    reg = json.loads((ROOT / "trailforge/golden/ca-areas.json").read_text())
    users = [(a["id"], e["name"]) for a in reg["areas"] for e in a.get("hiking_tag_wins", []) if "named_sac_evidence" in e]
    assert users == [("garibaldi-provincial-park-ca-bc", "Garibaldi Lake Trail"),
                     ("garibaldi-provincial-park-ca-bc", "Elfin Lakes Trail")]
    assert "named_sac_evidence" not in SCRIPT                      # the shell step only passes the registry through
# ------------------------------------------------ CLI on a real (XML) extract --
def _osm(ways, relations=""):
    nodes = "".join(f'<node id="{i}" version="1" lat="{51.05 + 0.005 * (i - 1)}" lon="-115.95"/>' for i in range(1, 8))
    return f'<?xml version="1.0"?><osm version="0.6">{nodes}{ways}{relations}</osm>'
def _way_xml(wid, nodes, tags):
    nds = "".join(f'<nd ref="{n}"/>' for n in nodes)
    tg = "".join(f'<tag k="{k}" v="{v}"/>' for k, v in tags.items())
    return f'<way id="{wid}" version="1">{nds}{tg}</way>'
PISTE = {"highway": "path", "name": "Great Glacier Trail", "piste:type": "skitour"}
def _rel_xml(route="hiking", members=(1, 2)):
    mem = "".join(f'<member type="way" ref="{m}" role=""/>' for m in members)
    return (f'<relation id="7000" version="1">{mem}<tag k="type" v="route"/><tag k="route" v="{route}"/>'
            f'<tag k="name" v="Great Glacier Trail"/></relation>')
def _cli(tmp_path, hiking_tag_wins, osm=None, with_boundary=True, features=None):
    pbf = tmp_path / "a.osm"
    pbf.write_text(osm or _osm(_way_xml(1, [1, 2], PISTE) + _way_xml(2, [2, 3], PISTE), _rel_xml()))
    registry = tmp_path / "reg.json"
    area = {"id": "t-ca-bc", "name": "Test Park"}
    if hiking_tag_wins is not None:
        area["hiking_tag_wins"] = hiking_tag_wins
    registry.write_text(json.dumps({"areas": [area]}))
    trails = tmp_path / "t.trails.geojson"
    trails.write_text(json.dumps({"type": "FeatureCollection", "features": features or [trail()]}))
    if with_boundary:
        (tmp_path / "t.exact-area.geojson").write_text(json.dumps(
            {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {},
                                                         "geometry": shapely_geometry.mapping(BOX)}]}))
    report = tmp_path / "r.json"
    run = subprocess.run([sys.executable, str(Path(htw.__file__)), "--registry", str(registry), "--area-id", "t-ca-bc",
                          "--pbf", str(pbf), "--trails", str(trails), "--report", str(report)],
                         capture_output=True, text=True)
    return run, trails, report
def _names(trails):
    return [f["properties"]["name"] for f in json.loads(trails.read_text())["features"]]
def test_cli_relation_form_appends_and_reports(tmp_path):
    run, trails, report = _cli(tmp_path, [entry()])
    assert run.returncode == 0, run.stderr
    assert _names(trails) == ["Other Trail", "Great Glacier Trail"]
    kept = json.loads(report.read_text())["kept"][0]
    assert kept["way_ids"] == [1, 2] and kept["hiking_relations"] == [7000]
def test_cli_way_form_with_foot_designated_and_no_relation(tmp_path):
    osm = _osm(_way_xml(1, [1, 2], {**PISTE, "foot": "designated"}) + _way_xml(2, [2, 3], {**PISTE, "foot": "designated"}))
    run, trails, _ = _cli(tmp_path, [way_entry(way_ids=[1, 2])], osm=osm)
    assert run.returncode == 0, run.stderr
    assert _names(trails)[-1] == "Great Glacier Trail"
def test_cli_ski_only_extract_is_refused_and_leaves_trails_untouched(tmp_path):
    osm = _osm(_way_xml(1, [1, 2], PISTE) + _way_xml(2, [2, 3], PISTE))
    run, trails, report = _cli(tmp_path, [way_entry(way_ids=[1, 2])], osm=osm)
    assert run.returncode == 2 and "SAFETY BLOCK" in run.stderr and "ski-only" in run.stderr
    assert _names(trails) == ["Other Trail"] and not report.exists()
def test_cli_ski_route_relation_extract_is_refused(tmp_path):
    osm = _osm(_way_xml(1, [1, 2], PISTE) + _way_xml(2, [2, 3], PISTE), _rel_xml(route="piste"))
    run, trails, report = _cli(tmp_path, [way_entry(way_ids=[1, 2])], osm=osm)
    assert run.returncode == 2 and _names(trails) == ["Other Trail"] and not report.exists()
    run, _, _ = _cli(tmp_path, [entry()], osm=osm)
    assert run.returncode == 2 and "route=hiking" in run.stderr
def test_cli_replaces_partial_copy_and_nothing_else(tmp_path):
    partial = trail("Great Glacier Trail", member_ways=(1,))
    run, trails, _ = _cli(tmp_path, [entry()], features=[trail(), partial])
    assert run.returncode == 0, run.stderr
    feats = json.loads(trails.read_text())["features"]
    assert [f["properties"]["name"] for f in feats] == ["Other Trail", "Great Glacier Trail"]
    assert feats[1]["properties"]["source"] == "hiking-tag-wins"
def test_cli_without_boundary_file_fails(tmp_path):
    run, _, _ = _cli(tmp_path, [entry()], with_boundary=False)
    assert run.returncode == 2 and "exact-area" in run.stderr
def test_cli_without_registry_entry_fails(tmp_path):
    run, _, _ = _cli(tmp_path, None)
    assert run.returncode == 2 and "no hiking_tag_wins" in run.stderr
def test_cli_invalid_registry_entry_fails(tmp_path):
    run, trails, _ = _cli(tmp_path, [entry(extra=1)])
    assert run.returncode == 2 and _names(trails) == ["Other Trail"]
# ------------------------------------------------ shell step and isolation --
def test_run_area_step_is_conditional_and_ordered():
    step = SCRIPT.index("ca_hiking_tag_wins.py")
    assert 'if [ -n "$HTW_COUNT" ]; then' in SCRIPT[:step]
    assert 'has("hiking_tag_wins")' in SCRIPT[:step]
    assert SCRIPT.index("assemble.py") < SCRIPT.index("ca_adopt_ways.py") < step < SCRIPT.index("ca_trail_filter.py --registry")
    assert 'SAFETY BLOCK: hiking_tag_wins must be a non-empty list on a relation-backed area' in SCRIPT
def test_absent_key_means_no_step_and_unchanged_adopt_default():
    block = SCRIPT[SCRIPT.index("# Optional hiking_tag_wins"):SCRIPT.index("# Canadian vocabulary")]
    assert block.count("python3 ../scripts/ca_hiking_tag_wins.py") == 1
    guard = block[:block.index("python3 ../scripts/ca_hiking_tag_wins.py")]
    assert '[ "$HTW_COUNT" = "0" ] || [ -z "$RELATION_ID" ]' in guard and "exit 2" in guard
    assert "ADOPT" not in block
def test_canada_only_no_shared_engine_file_mentions_it():
    for rel in ("trailforge/assemble", "trailforge/extract", "trailforge/serve", ".github/workflows"):
        for path in (ROOT / rel).rglob("*"):
            if path.is_file() and path.suffix in {".py", ".sh", ".yml", ".yaml"}:
                assert "hiking_tag_wins" not in path.read_text(encoding="utf-8", errors="ignore"), path
    importers = {p.name for p in (ROOT / "scripts").glob("*.py") if "ca_hiking_tag_wins" in p.read_text(encoding="utf-8")}
    assert importers <= {"ca_publish.py", "test_ca_hiking_tag_wins.py"}
def test_no_us_or_denmark_registry_reads_the_key():
    for name in ("dk_publish.py", "publish_areas.py"):
        for path in ROOT.rglob(name):
            if ".git" not in path.parts:
                assert "hiking_tag_wins" not in path.read_text(encoding="utf-8")
