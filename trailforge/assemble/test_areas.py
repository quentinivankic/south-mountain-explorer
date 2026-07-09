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
        kept = A.filter_features_inside(feats, union)
        self.assertEqual(len(kept), 2)

    def test_straddler_kept_when_majority_inside(self):
        # A connector leaving the park (union spans x:0..12): DC-Ray-style.
        union, _ = A.union_matching(self.AREAS, "south mountain")
        # 6 of 10 units inside (x 6->12), 4 outside (12->16) -> 60% -> kept.
        majority = _line([[6, 5], [16, 5]])
        # 2 of 10 inside (x 10->12) -> 20% -> dropped.
        minority = _line([[10, 5], [20, 5]])
        kept = A.filter_features_inside([majority, minority], union)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["geometry"]["coordinates"][0], [6, 5])

    def test_no_match_returns_none(self):
        union, names = A.union_matching(self.AREAS, "nonexistent park")
        self.assertIsNone(union)
        self.assertEqual(names, set())


if __name__ == "__main__":
    unittest.main()
