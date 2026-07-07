"""Tests for OSM<->authoritative conflation geometry (spec §5.1).

Gated on shapely: the light licensing-gate CI step runs the unit suite
BEFORE the geo toolchain installs, so these skip there and run wherever
shapely is present (locally, and any geo-enabled runner).
"""
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "conflation"))

try:
    import shapely  # noqa: F401
    from shapely.geometry import MultiLineString, LineString
    HAVE_SHAPELY = True
except ImportError:
    HAVE_SHAPELY = False

import match  # noqa: E402


def _osm_line(osm_id, coords, **props):
    return {"features": [{"geometry": {"type": "LineString", "coordinates": coords},
                          "properties": {"osm_id": osm_id, **props}}]}


@unittest.skipUnless(HAVE_SHAPELY, "shapely not installed")
class MetricLength(unittest.TestCase):
    def test_multipart_geometry_sums_parts(self):
        # Two ~111 m segments → ~222 m. A MultiLineString has no .coords;
        # the old code raised NotImplementedError here (the field crash).
        mls = MultiLineString([[(0, 0), (0.001, 0)], [(0.002, 0), (0.003, 0)]])
        self.assertAlmostEqual(match._to_metric_len(mls, 0.0), 222.6, delta=1.0)

    def test_empty_and_nonlinear_are_zero(self):
        self.assertEqual(match._to_metric_len(LineString(), 0.0), 0.0)


@unittest.skipUnless(HAVE_SHAPELY, "shapely not installed")
class Conflation(unittest.TestCase):
    def test_multilinestring_intersection_matches_without_crashing(self):
        # A single DOC feature whose buffer meets the OSM way in two
        # disjoint stretches → intersection is a MultiLineString. This is
        # the exact geometry that crashed the first real NZ run.
        osm = _osm_line("w1", [[0, 0], [0.01, 0]], name="Kepler")
        auth = {"features": [{"geometry": {"type": "MultiLineString", "coordinates": [
            [[0.0, 0.00005], [0.004, 0.00005]],
            [[0.006, 0.00005], [0.01, 0.00005]]]},
            "properties": {"name": "DOC Track", "operator": "DOC"}}]}
        res = match.match_region(osm, auth, None, source_id="doc", min_overlap=0.6)
        m = res["osm"]["w1"]
        self.assertTrue(m["matched"])
        self.assertEqual(m["source"], "doc")
        self.assertEqual(m["auth_name"], "DOC Track")

    def test_distant_trail_does_not_match(self):
        auth = {"features": [{"geometry": {"type": "LineString",
                "coordinates": [[0, 0], [0.01, 0]]}, "properties": {"name": "DOC"}}]}
        res = match.match_region(_osm_line("w2", [[5, 5], [5.01, 5]]), auth,
                                 None, source_id="doc")
        self.assertFalse(res["osm"]["w2"]["matched"])

    def test_inside_official_boundary_sets_whitelist(self):
        osm = _osm_line("w3", [[0.001, 0.001], [0.002, 0.001]])
        boundaries = {"features": [{"geometry": {"type": "Polygon", "coordinates": [
            [[0, 0], [0.01, 0], [0.01, 0.01], [0, 0.01], [0, 0]]]},
            "properties": {"name": "Fiordland NP"}}]}
        res = match.match_region(osm, {"features": []}, boundaries, source_id="doc")
        m = res["osm"]["w3"]
        self.assertTrue(m["inside_official_boundary"])
        self.assertTrue(m["whitelist"])


if __name__ == "__main__":
    unittest.main()
