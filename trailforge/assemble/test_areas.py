"""Tests for area-boundary filtering (trail↔area). Gated on shapely."""
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    import shapely  # noqa: F401
    from shapely.geometry import Polygon, Point  # noqa: F401
    HAVE_SHAPELY = True
except ImportError:
    HAVE_SHAPELY = False

import areas as A  # noqa: E402


def _area(name, box):
    from shapely.geometry import box as sbox
    x0, y0, x1, y1 = box
    return {"name": name, "tags": {"name": name}, "geom": sbox(x0, y0, x1, y1)}


def _line(coords):
    return {"type": "Feature", "properties": {},
            "geometry": {"type": "LineString", "coordinates": coords}}


@unittest.skipUnless(HAVE_SHAPELY, "shapely not installed")
class AreaFilter(unittest.TestCase):
    # The real South Mountain shape: a relation named "…Park and Preserve"
    # plus member polygons named "…Preserve". "south mountain" must union
    # them; a nearby different park must NOT match.
    AREAS = [
        _area("South Mountain Park and Preserve", (0, 0, 10, 10)),
        _area("South Mountain Preserve", (10, 0, 12, 10)),   # adjacent member piece
        _area("Cesar Chavez Park", (50, 50, 55, 55)),        # unrelated
    ]

    def test_substring_unions_both_south_mountain_polys(self):
        union, names = A.union_matching(self.AREAS, "south mountain")
        self.assertEqual(names, {"South Mountain Park and Preserve", "South Mountain Preserve"})
        from shapely.geometry import Point
        self.assertTrue(union.covers(Point(11, 5)))   # inside the member piece
        self.assertFalse(union.covers(Point(52, 52)))  # Cesar Chavez, excluded

    def test_keeps_inside_drops_outside(self):
        union, _ = A.union_matching(self.AREAS, "south mountain")
        feats = [
            _line([[1, 1], [2, 2]]),        # inside the park
            _line([[11, 5], [11.5, 6]]),    # inside the member piece
            _line([[52, 52], [53, 53]]),    # in Cesar Chavez — out
            _line([[30, 30], [31, 31]]),    # nowhere near — out
        ]
        kept = A.clip_features_to_area(feats, union)
        self.assertEqual(len(kept), 2)

    def test_straddler_clipped_to_in_park_portion(self):
        # union spans x:0..12. A DC-Ray-style connector runs from inside the
        # park out to a road; clipping keeps only the in-park piece.
        union, _ = A.union_matching(self.AREAS, "south mountain")
        # full line is 10 deg (~688 mi); the in-park piece x:8->12 is 4 deg.
        straddler = {"type": "Feature",
                     "properties": {"name": "DC-Ray", "length_mi": 688.0},
                     "geometry": {"type": "LineString",
                                  "coordinates": [[8, 5], [18, 5]]}}
        kept = A.clip_features_to_area([straddler], union)
        self.assertEqual(len(kept), 1)
        coords = kept[0]["geometry"]["coordinates"]
        xs = [pt[0] for line in coords for pt in line]
        self.assertLessEqual(max(xs), 12.0 + 1e-9)   # nothing past the boundary
        self.assertGreaterEqual(min(xs), 8.0 - 1e-9)
        # clipped length recorded, original preserved, flagged.
        p = kept[0]["properties"]
        self.assertTrue(p["clipped"])
        self.assertEqual(p["full_length_mi"], 688.0)
        self.assertLess(p["length_mi"], 688.0)

    def test_fully_outside_clips_to_nothing(self):
        union, _ = A.union_matching(self.AREAS, "south mountain")
        outside = _line([[52, 52], [55, 55]])   # entirely in Cesar Chavez
        self.assertEqual(A.clip_features_to_area([outside], union), [])

    def test_boundary_sliver_dropped_by_floor(self):
        # A tiny in-park remnant is a sliver, not a trail — dropped by the floor.
        union, _ = A.union_matching(self.AREAS, "south mountain")
        tiny = _line([[1, 1], [1.0001, 1]])     # ~0.007 mi inside
        self.assertEqual(len(A.clip_features_to_area([tiny], union, min_inside_mi=0.05)), 0)
        self.assertEqual(len(A.clip_features_to_area([tiny], union, min_inside_mi=0.0)), 1)

    def test_no_match_returns_none(self):
        union, names = A.union_matching(self.AREAS, "nonexistent park")
        self.assertIsNone(union)
        self.assertEqual(names, set())


if __name__ == "__main__":
    unittest.main()
