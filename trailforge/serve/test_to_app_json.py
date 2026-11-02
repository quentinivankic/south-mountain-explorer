#!/usr/bin/env python3
"""Tests for the published area row's boundary id fields."""
import copy
import importlib.util
import unittest
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "to_app_json", Path(__file__).resolve().parent / "to_app_json.py")
conv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(conv)

FC = {"features": [{
    "geometry": {"type": "MultiLineString",
                 "coordinates": [[[-112.0, 33.0], [-112.001, 33.001]]]},
    "properties": {"name": "Test Trail", "kind": "trail", "length_mi": 0.1},
}]}


def _row(osm_rel=None, osm_way=None):
    return conv.convert(FC, "test-area-az", "Test Area", "Arizona",
                        (33.0, -112.0), osm_rel, {"trail", "hike", "route"},
                        osm_way=osm_way)


def test_a_way_sourced_area_records_its_way_id():
    # 6,151 of 9,060 shipped areas had NO boundary id, so the containment gate
    # could not run for them and their parking fell back to proximity alone.
    row = _row(osm_way=1226630002)
    assert row["osm_way_id"] == 1226630002
    assert row["osm_relation_id"] is None


def test_a_way_id_never_lands_in_the_relation_field():
    # The app computes an Overpass area id as osmId + 3_600_000_000, the
    # RELATION-only offset. A way id in that field points it at a different
    # polygon entirely, which is why seed-areas.py dropped it rather than
    # storing it there.
    row = _row(osm_way=1226630002)
    assert row["osm_relation_id"] != 1226630002


def test_a_relation_wins_and_no_way_id_is_written():
    # A relation id is strictly better: the app can use it AND so can the gate.
    row = _row(osm_rel=14195507, osm_way=1226630002)
    assert row["osm_relation_id"] == 14195507
    assert "osm_way_id" not in row


def test_an_area_with_neither_id_is_unchanged():
    row = _row()
    assert row["osm_relation_id"] is None
    assert "osm_way_id" not in row


def test_exact_denmark_qa_evidence_is_not_published_to_app_rows():
    fc = copy.deepcopy(FC)
    fc["features"][0]["properties"].update({
        "kind": "hike",
        "destination_evidence": [{"osm_node_id": 900}],
        "quality_candidate_index": 7,
        "root_relation_ids": [700, 701],
        "identity_root_relation_id": 700,
    })

    trail = conv.convert(
        fc, "test-area-dk", "Test Area", "Denmark", (33.0, -112.0),
        7046785, {"trail", "hike", "route"})["trails"][0]

    self_contained_fields = {
        "id", "name", "distanceMi", "difficulty", "segments",
    }
    assert set(trail) == self_contained_fields
    assert "destination_evidence" not in trail
    assert "quality_candidate_index" not in trail
    assert "root_relation_ids" not in trail
    assert "identity_root_relation_id" not in trail


class QaEvidenceOmission(unittest.TestCase):
    def test_exact_denmark_qa_evidence_is_not_published(self):
        test_exact_denmark_qa_evidence_is_not_published_to_app_rows()


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
# --- Canada accent fold (region=ca only) -------------------------------------
def _ids(name, area_id, state):
    fc = {"features": [{
        "geometry": {"type": "MultiLineString",
                     "coordinates": [[[-70.0, 47.0], [-70.001, 47.001]]]},
        "properties": {"name": name, "kind": "trail", "length_mi": 1.0}}]}
    row = conv.convert(fc, area_id, "Area", state, (47.0, -70.0), None,
                       {"trail", "hike", "route"})
    return [t["id"] for t in row["trails"]]
FRENCH = {
    "Sentier de la Vallée": "sentier-de-la-vallee",
    "La Rivière-Cachée": "la-riviere-cachee",
    "La Taïga": "la-taiga",
    "Grand Brûlé": "grand-brule",
    "Les Éclaireurs": "les-eclaireurs",
    "Là-Haut": "la-haut",
    "Pâtis": "patis",
    "Noël": "noel",
    "Cœur": "coeur",
    "Œuvre": "oeuvre",
    "Où": "ou",
    "Lÿs": "lys",
    "Île Verte": "ile-verte",
    "Mary Schäffer Loop": "mary-schaffer-loop",
}
def test_canada_trail_ids_fold_french_accents():
    for name, want in FRENCH.items():
        assert _ids(name, "x-ca-qc", "Canada") == [want], name
def test_canada_fold_handles_decomposed_input():
    # NFD "e" + combining acute must fold the same as precomposed
    assert _ids("Rivie\u0300re", "x-ca-qc", "Canada") == ["riviere"]
def test_non_canada_ids_are_unchanged_by_the_fold():
    for name in FRENCH:
        for state, aid in (("Arizona", "x-az"), ("Denmark", "x-dk"),
                           ("Vermont", "x-vt")):
            assert _ids(name, aid, state) == [conv.trail_slug(name)], (name, state)
    assert conv.trail_slug("La Rivière") == "la-rivi-re"
    assert conv.trail_slug("Cœur") == "c-ur"
def test_live_canadian_areas_keep_their_legacy_ids():
    assert _ids("Brûlé Trail", "awenda-provincial-park-ca-on", "Canada") == ["br-l-trail"]
    assert _ids("Mary Schäffer Loop", "jasper-national-park-ca-ab", "Canada") == ["mary-sch-ffer-loop"]
    assert _ids("Brûlé Trail", "other-park-ca-on", "Canada") == ["brule-trail"]
def test_ascii_canadian_names_are_unchanged():
    for name in ("Bald Hill", "Steve’s Trail", "Grey Owl 2"):
        assert _ids(name, "x-ca-mb", "Canada") == [conv.trail_slug(name)]
