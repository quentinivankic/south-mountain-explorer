"""Tests for scripts/dk_name_unnamed_loops.py (registry name_unnamed_loops, Denmark only, M4 NAMING).
Source mutants for every gate are in test_dk_name_unnamed_loops_mutants.py."""
import copy
import json
import os
import subprocess
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parent))
import dk_name_unnamed_loops as naming  # noqa: E402
import dk_publish as dk  # noqa: E402
ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "dk_name_unnamed_loops.py"
REGISTRY = ROOT / "trailforge/golden/dk-areas.json"
RUN_AREA = ROOT / "scripts/dk-run-area.sh"
SRC = "https://naturstyrelsen.dk/find-et-naturomraade/naturguider/midt-og-vestjylland/fanoe/praktisk"
RED = {"type": "route", "route": "hiking", "network": "lwn", "roundtrip": "yes", "osmc:symbol": "red:brown:red_dot"}
YELLOW = {**RED, "osmc:symbol": "red:brown:yellow_dot"}
def entry(rel="r101", name="Pælebjerg Rød rute", km=(1, 2), **extra):
    return {"relation": rel, "name": name, "source": SRC, "listed_km": list(km), "reason": "reviewed", **extra}
def feature(rid, name, mi, **props):
    return {"type": "Feature", "geometry": {"type": "MultiLineString", "coordinates": [[[8.4, 55.38], [8.41, 55.39]]]},
            "properties": {"name": name, "length_mi": mi, "source": "relation", "root_relation_ids": [rid],
                           "relation_ids": [rid], **props}}
def trails():
    """Two unnamed loops (one opted in below), one named relation trail, one stitched trail."""
    return {"type": "FeatureCollection", "features": [
        feature(101, "Mågekolonievej", 1.1), feature(102, None, 1.4), feature(103, "Fanø Hjertesti", 2.5),
        {**feature(104, "Kirkevej", 0.9), "properties": {"name": "Kirkevej", "length_mi": 0.9, "source": "name-stitch"}}]}
def relations():
    return {101: dict(RED), 102: dict(YELLOW), 103: {**RED, "name": "Fanø Hjertesti"}}
def names(fc):
    return [f["properties"]["name"] for f in fc["features"]]
def test_names_the_listed_loop_and_leaves_every_other_trail_untouched():
    fc = trails()
    before = copy.deepcopy(fc)
    rows = naming.apply_entries(fc, relations(), [entry()])
    assert names(fc) == ["Pælebjerg Rød rute", None, "Fanø Hjertesti", "Kirkevej"]
    for old, new in list(zip(before["features"], fc["features"]))[1:]:
        assert old == new                                            # the non-opted unnamed loop stays unnamed
    assert "osm_name" not in fc["features"][0]["properties"]
    assert rows == [{"relation": "r101", "from": "Mågekolonievej", "name": "Pælebjerg Rød rute", "source": SRC,
                     "listed_km": [1, 2], "length_km": 1.77}]
def test_an_empty_list_of_loops_is_never_a_no_op_input():
    with pytest.raises(naming.NamingError):
        naming.apply_entries(trails(), relations(), [])
def test_the_yellow_dot_loop_takes_the_yellow_word():
    fc = trails()
    naming.apply_entries(fc, relations(), [entry("r102", "Pælebjerg Gul rute", (1, 2))])
    assert names(fc)[1] == "Pælebjerg Gul rute"
def test_ids_derive_from_the_new_name_and_do_not_collide():
    sys.path.insert(0, str(ROOT / "trailforge" / "serve"))
    sys.path.insert(0, str(ROOT / "trailforge" / "assemble"))
    import to_app_json
    fc = trails()
    naming.apply_entries(fc, relations(), [entry(), entry("r102", "Pælebjerg Gul rute")])
    row = to_app_json.convert(fc, "x-dk", "X", "Denmark", (55.0, 8.0), 1, {"trail"})
    ids = [t["id"] for t in row["trails"]]
    assert len(set(ids)) == len(ids) and row["trail_count"] == 4
    assert {t["name"] for t in row["trails"]} >= {"Pælebjerg Rød rute", "Pælebjerg Gul rute"}
def _fails(fc, rels, entries, match):
    snapshot = json.dumps(fc)
    with pytest.raises(naming.NamingError, match=match):
        naming.apply_entries(fc, rels, entries)
    assert json.dumps(fc) == snapshot                                   # fails closed, nothing renamed
def test_refuses_a_relation_that_is_not_in_the_authority_file():
    _fails(trails(), {102: relations()[102]}, [entry()], "not found")
@pytest.mark.parametrize("tag", [{"name": "Rød"}, {"name:da": "Rød"}, {"official_name": "x"}, {"alt_name": "x"},
                                 {"short_name": "x"}, {"loc_name": "x"}, {"old_name": "x"}])
def test_refuses_a_relation_that_already_has_a_name(tag):
    _fails(trails(), {**relations(), 101: {**RED, **tag}}, [entry()], "already has a name")
@pytest.mark.parametrize("tags,match", [
    ({"route": "bicycle"}, "not a hiking"), ({"type": "multipolygon"}, "not a hiking"),
    ({"osmc:symbol": ""}, "not signed"), ({"osmc:symbol": " "}, "not signed"),
    ({"roundtrip": "no"}, "not a loop"), ({"roundtrip": ""}, "not a loop"),
    ({"osmc:symbol": "red:brown:yellow_dot"}, "colour word"), ({"osmc:symbol": "red:brown:cyan_dot"}, "colour word"),
])
def test_refuses_a_relation_that_is_not_an_unnamed_signed_loop_of_that_colour(tags, match):
    _fails(trails(), {**relations(), 101: {**RED, **tags}}, [entry()], match)
def test_a_missing_tag_is_refused_too():
    for key, match in (("osmc:symbol", "not signed"), ("roundtrip", "not a loop"), ("route", "not a hiking")):
        rel = dict(RED)
        del rel[key]
        _fails(trails(), {**relations(), 101: rel}, [entry()], match)
@pytest.mark.parametrize("symbol,word,ok", [
    ("red:white", "Rød", True), ("red:white", "Gul", False), ("blue:white:blue_bar", "Blå", True),
    ("red:white:blue_bar", "Rød", False), ("yellow:white:yellow_dot:black:x", "Gul", True),
    ("green:white:green_circle", "Grøn", True), ("orange:white:orange_dot", "Orange", True),
])
def test_waymark_colour_is_the_foreground_colour_else_the_way_colour(symbol, word, ok):
    fc, rels = trails(), {**relations(), 101: {**RED, "osmc:symbol": symbol}}
    if ok:
        naming.apply_entries(fc, rels, [entry(name=f"Pælebjerg {word} rute")])
        assert names(fc)[0] == f"Pælebjerg {word} rute"
    else:
        _fails(fc, rels, [entry(name=f"Pælebjerg {word} rute")], "colour word")
def test_refuses_when_the_relation_is_not_exactly_one_assembled_trail():
    fc = trails()
    fc["features"].append(feature(101, "Mågekolonievej 2", 1.1))
    _fails(fc, relations(), [entry()], "matches 2")
    fc = trails()
    fc["features"][0]["properties"]["root_relation_ids"] = [101, 105]            # merged with another relation
    _fails(fc, relations(), [entry()], "matches 0")
    fc = trails()
    fc["features"][0]["properties"]["source"] = "name-stitch"
    _fails(fc, relations(), [entry()], "matches 0")
    fc = trails()
    del fc["features"][0]
    _fails(fc, relations(), [entry()], "matches 0")
@pytest.mark.parametrize("mi,ok", [
    (0.46, False), (0.47, True), (1.1, True), (1.55, True), (1.56, False), (3.0, False), (0.05, False)])
def test_the_loop_length_must_match_the_listed_range_within_the_tolerance(mi, ok):
    fc = trails()
    fc["features"][0]["properties"]["length_mi"] = mi                       # 0.75 km (0.466 mi) .. 2.5 km (1.553 mi) is accepted
    if ok:
        naming.apply_entries(fc, relations(), [entry()])
        assert names(fc)[0] == "Pælebjerg Rød rute"
    else:
        _fails(fc, relations(), [entry()], "not the listed loop")
def test_a_stub_is_not_named_after_a_longer_listed_loop():
    fc = trails()
    fc["features"][1]["properties"]["length_mi"] = 0.486                    # the 0.78 km yellow stub
    _fails(fc, relations(), [entry("r102", "Fuglekøje Gul rute", (5.5, 5.5))], "not the listed loop")
def test_refuses_a_name_that_equals_another_trail_in_any_case():
    for other in ("Pælebjerg Rød rute", "pælebjerg rød rute", "PÆLEBJERG RØD RUTE"):
        fc = trails()
        fc["features"][2]["properties"]["name"] = other
        _fails(fc, relations(), [entry()], "equals another trail")
def test_two_entries_either_both_apply_or_nothing_does():
    fc = trails()
    fc["features"][1]["properties"]["length_mi"] = 9.0
    _fails(fc, relations(), [entry(), entry("r102", "Pælebjerg Gul rute")], "not the listed loop")
@pytest.mark.parametrize("bad", [
    [], None, "x", {}, [None], [{}], [entry(), entry()], [entry(), entry("r102")],
    [{k: v for k, v in entry().items() if k != "source"}],                  # an entry without a source URL
    [{k: v for k, v in entry().items() if k != "listed_km"}], [{k: v for k, v in entry().items() if k != "reason"}],
    [{**entry(), "extra": 1}], [{**entry(), "source": ""}], [{**entry(), "source": " "}],
    [{**entry(), "source": None}], [{**entry(), "source": 5}], [{**entry(), "reason": " "}],
    [{**entry(), "relation": "101"}], [{**entry(), "relation": "w101"}], [{**entry(), "relation": "r0"}],
    [{**entry(), "relation": "r01"}], [{**entry(), "relation": 101}],
    [entry(name="Pælebjerg")], [entry(name="Rød rute")], [entry(name=" Rød rute")], [entry(name="X Rød rute")],
    [entry(name="Pælebjerg rød rute")], [entry(name="Pælebjerg Rød Rute")], [entry(name="Pælebjerg Cyan rute")],
    [entry(name="Pælebjerg Rød rute ")], [entry(name="Pe\u0301lebjerg Rød rute")], [entry(name="Pælebjerg\u0007 Rød rute")],
    [entry(name="P" * 120 + " Rød rute")],
    [entry(km=(2, 1))], [entry(km=(0, 1))], [entry(km=(-1, 1))], [entry(km=(1, 61))], [{**entry(), "listed_km": [1]}],
    [{**entry(), "listed_km": [1, 2, 3]}], [{**entry(), "listed_km": ["1", 2]}], [{**entry(), "listed_km": [True, 2]}],
    [{**entry(), "listed_km": 2}], [{**entry(), "listed_km": [float("nan"), 2]}],
])
def test_validate_entries_rejects(bad):
    with pytest.raises(naming.NamingError):
        naming.validate_entries(bad)
@pytest.mark.parametrize("url", [
    "http://naturstyrelsen.dk/fanoe/praktisk", "ftp://naturstyrelsen.dk/x", "naturstyrelsen.dk/x",
    "https://www.alltrails.com/trail/denmark/x", "https://www.komoot.com/de-de/guide/207606",
    "https://esmark.de/wanderweg-fano/", "https://outdooractive.com/x/1", "https://www.visitor-blog.dk/x",
    "https://naturstyrelsen.dk.evil.example/x", "https://evilnaturstyrelsen.dk/x", "https://nst.dk.evil.example/x",
    "https://naturstyrelsen.dk@evil.example/x", "https://user:pw@naturstyrelsen.dk/x",
    "https://naturstyrelsen.dk:8443/x", "https://naturstyrelsen.dk", "https://naturstyrelsen.dk/",
    "https://naturstyrelsen.dk/a b", "https://", "https:///x", "https://kommune.dk/x", "https://visit.dk/x",
    "https://visitfanoe.org/x", "https://notvadehavskysten.dk/x", "https://mykommune.dk/x",
    "https://www.esbjergkommune.dk/x",
])
def test_check_source_refuses_non_agency_or_malformed_urls(url):
    with pytest.raises(naming.NamingError):
        naming.check_source(url, "t")
@pytest.mark.parametrize("url", [
    SRC, "https://www.naturstyrelsen.dk/x", "https://de.naturstyrelsen.dk/naturerlebnisse/natur-guide/fanoe/praktisch",
    "https://nst.dk/x", "https://naturstyrelsen.dk:443/x", "https://visitfanoe.dk/da/skovlegepladsen/",
    "https://www.visitdenmark.com/denmark/plan-your-trip/x", "https://www.vadehavskysten.com/hiking-fano",
    "https://fanoe.dk/x", "https://vadehavskysten.de/x",
])
def test_check_source_accepts_agency_pages(url):
    naming.check_source(url, "t")
