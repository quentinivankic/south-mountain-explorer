"""Tests for OSM staging (spec §3.1–3.2)."""
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "build"))

import stage_osm as st  # noqa: E402


def line(**tags):
    return {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
            "properties": tags}


def poly(**tags):
    return {"type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]},
            "properties": tags}


class TrailStaging(unittest.TestCase):
    def test_selects_trail_highways(self):
        fc = {"features": [line(highway="path", **{"@id": "1"}),
                           line(highway="motorway", **{"@id": "2"}),
                           line(highway="footway", **{"@id": "3"})]}
        trails, _ = st.stage(fc)
        ids = {f["properties"]["osm_id"] for f in trails["features"]}
        self.assertEqual(ids, {"1", "3"})

    def test_abandoned_highway_kept_with_lifecycle(self):
        fc = {"features": [line(**{"abandoned:highway": "path", "@id": "9"})]}
        trails, _ = st.stage(fc)
        self.assertEqual(len(trails["features"]), 1)
        self.assertEqual(trails["features"][0]["properties"]["lifecycle"], "abandoned")

    def test_bucket_a_normalization(self):
        p = st.normalize_trail(
            {"highway": "path", "name": "Kepler Track", "operator": "DOC",
             "SAC_scale": None, "sac_scale": "Mountain_Hiking", "access": "Private",
             "informal": "Yes", "tiger:reviewed": "no"}, "5")
        self.assertTrue(p["has_name"])
        self.assertTrue(p["has_known_operator"])
        self.assertEqual(p["sac_scale"], "mountain_hiking")  # lowercased
        self.assertEqual(p["access"], "private")
        self.assertEqual(p["informal"], "yes")
        self.assertTrue(p["tiger_unreviewed"])

    def test_disused_lifecycle(self):
        p = st.normalize_trail({"highway": "track", "disused": "yes"}, "1")
        self.assertEqual(p["lifecycle"], "disused")

    def test_no_score_field_emitted(self):
        p = st.normalize_trail({"highway": "path"}, "1")
        for k in ("confidence", "score", "band"):
            self.assertNotIn(k, p)

    def test_vehicle_or_utility_road_detection(self):
        # named-like-a-road track
        self.assertTrue(st.normalize_trail(
            {"highway": "track", "name": "Irrigation Canal"}, "1")["vehicle_or_utility_road"])
        # motor-access track
        self.assertTrue(st.normalize_trail(
            {"highway": "track", "motor_vehicle": "yes"}, "2")["vehicle_or_utility_road"])
        # a plain footpath is not a road
        self.assertFalse(st.normalize_trail(
            {"highway": "path", "name": "Coastal Walk"}, "3")["vehicle_or_utility_road"])
        # a plain named track is fine
        self.assertFalse(st.normalize_trail(
            {"highway": "track", "name": "Kepler Track"}, "4")["vehicle_or_utility_road"])

    def test_route_relation_signals_from_index(self):
        route = {"in_route": True, "network": "NWN",
                 "route_name": "Te Araroa", "route_operator": "Te Araroa Trust"}
        p = st.normalize_trail({"highway": "path"}, "w1", route)
        self.assertTrue(p["in_route_relation"])
        self.assertEqual(p["network"], "nwn")           # lowercased
        self.assertTrue(p["has_known_operator"])        # inherited from route
        # no route -> signals off
        q = st.normalize_trail({"highway": "path"}, "w2")
        self.assertFalse(q["in_route_relation"])
        self.assertEqual(q["network"], "")

    def test_stage_joins_route_index_by_id(self):
        fc = {"features": [line(highway="path", **{"@id": "w5"})]}
        trails, _ = st.stage(fc, {"w5": {"in_route": True, "network": "iwn"}})
        self.assertTrue(trails["features"][0]["properties"]["in_route_relation"])


class AreaStaging(unittest.TestCase):
    def test_protected_area_highest_osm_rank(self):
        _, areas = st.stage({"features": [poly(boundary="protected_area", name="Fiordland NP")]})
        self.assertEqual(len(areas["features"]), 1)
        self.assertEqual(areas["features"][0]["properties"]["authority_rank"], 35)

    def test_protected_area_with_wdpa_xref_ranks_higher(self):
        a = st.normalize_area({"boundary": "protected_area", "ref:whon": "1234"}, "1")
        self.assertEqual(a["authority_rank"], 40)

    def test_forest_lowest_rank(self):
        a = st.normalize_area({"landuse": "forest"}, "1")
        self.assertEqual(a["authority_rank"], 10)

    def test_non_area_polygon_skipped(self):
        _, areas = st.stage({"features": [poly(building="yes")]})
        self.assertEqual(areas["features"], [])

    def test_trail_line_not_counted_as_area(self):
        _, areas = st.stage({"features": [line(highway="path")]})
        self.assertEqual(areas["features"], [])


if __name__ == "__main__":
    unittest.main()
