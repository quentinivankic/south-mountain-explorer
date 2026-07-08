"""Tests for route/segment dedup (NZ review fix #1). Pure stdlib."""
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "build"))

import dedup_trails as d  # noqa: E402


def seg(name, coords, osm_id, **p):
    props = {"osm_id": osm_id, **p}
    if name:
        props.update(name=name, has_name=True)
    return {"type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": props}


def fc(*feats):
    return {"type": "FeatureCollection", "features": list(feats)}


class Dedup(unittest.TestCase):
    def test_chained_route_collapses_to_one(self):
        out, stats = d.dedup(fc(
            seg("Te Araroa Trail", [[0, 0], [1, 1]], "w1"),
            seg("Te Araroa Trail", [[1, 1], [2, 2]], "w2"),
            seg("Te Araroa Trail", [[2, 2], [3, 3]], "w3"),
        ))
        self.assertEqual(len(out["features"]), 1)
        f = out["features"][0]
        self.assertEqual(f["geometry"]["type"], "MultiLineString")
        self.assertEqual(f["properties"]["segment_count"], 3)
        self.assertTrue(f["properties"]["has_name"])
        self.assertTrue(f["properties"]["osm_id"].startswith("route:"))
        self.assertEqual(stats["routes_merged"], 1)

    def test_same_name_disconnected_stays_separate(self):
        # Two "Ridge Track"s with no shared endpoint = distinct trails.
        out, _ = d.dedup(fc(
            seg("Ridge Track", [[10, 10], [10.1, 10.1]], "w5"),
            seg("Ridge Track", [[50, 50], [50.1, 50.1]], "w6"),
        ))
        self.assertEqual(len(out["features"]), 2)
        self.assertTrue(all(f["geometry"]["type"] == "LineString"
                            for f in out["features"]))

    def test_unnamed_passthrough_untouched(self):
        u1 = seg(None, [[0, 0], [0.1, 0]], "w7")
        out, _ = d.dedup(fc(u1, seg(None, [[5, 5], [5.1, 5]], "w8")))
        self.assertEqual(len(out["features"]), 2)
        self.assertIs(out["features"][0], u1)  # identical object, unmodified

    def test_single_named_segment_unchanged(self):
        s = seg("Solo Track", [[0, 0], [1, 1]], "w9")
        out, stats = d.dedup(fc(s))
        self.assertEqual(len(out["features"]), 1)
        self.assertNotIn("segment_count", out["features"][0]["properties"])
        self.assertEqual(stats["routes_merged"], 0)

    def test_length_mi_stamped_on_every_trail(self):
        # ~0 length (same point) → tiny; a 1° lat segment ≈ 69 mi.
        out, _ = d.dedup(fc(seg(None, [[0, 0], [0, 1]], "w1")))
        self.assertAlmostEqual(out["features"][0]["properties"]["length_mi"], 69.0, delta=1.0)

    def test_property_merge_operator_or_and_majority(self):
        out, _ = d.dedup(fc(
            seg("Kepler Track", [[0, 0], [1, 1]], "w1", access="no", informal="yes"),
            seg("Kepler Track", [[1, 1], [2, 2]], "w2", has_known_operator=True, access="no"),
            seg("Kepler Track", [[2, 2], [3, 3]], "w3", access="no"),
        ))
        p = out["features"][0]["properties"]
        self.assertTrue(p["has_known_operator"])          # OR-ed from one member
        self.assertEqual(p["access"], "no")               # majority
        self.assertNotIn("informal", p)                   # only 1/3 → not majority


if __name__ == "__main__":
    unittest.main()
