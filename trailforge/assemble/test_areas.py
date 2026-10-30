"""Tests for area-boundary filtering (trail↔area). Gated on shapely."""
import math
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

    def test_exact_clip_assigns_selected_area_to_every_survivor(self):
        union, _ = A.union_matching(self.AREAS, "south mountain")
        features = [
            _line([[1, 1], [2, 2]]),
            _line([[8, 5], [18, 5]]),
        ]
        kept = A.clip_features_to_area(
            features, union, area_name="South Mountain Park and Preserve")
        self.assertEqual(len(kept), 2)
        self.assertEqual(
            {feature["properties"]["area"] for feature in kept},
            {"South Mountain Park and Preserve"},
        )

    def test_exact_selector_is_case_sensitive_and_does_not_union_substrings(self):
        exact = A.select_exact(self.AREAS, "South Mountain Preserve")
        self.assertEqual([area["name"] for area in exact], ["South Mountain Preserve"])
        self.assertEqual(A.select_exact(self.AREAS, "south mountain preserve"), [])
        self.assertEqual(A.select_exact(self.AREAS, "South Mountain"), [])

    def test_exact_selector_returns_duplicates_for_caller_to_reject(self):
        duplicate = _area("South Mountain Preserve", (20, 0, 21, 1))
        exact = A.select_exact(
            [*self.AREAS, duplicate], "South Mountain Preserve")
        self.assertEqual(len(exact), 2)

    def test_sub_rounding_exact_cut_is_still_flagged_as_clipped(self):
        from shapely.geometry import MultiPolygon, box

        # The removed middle gap is below three-decimal mileage precision, so
        # display lengths match even though exact geometry is truly clipped.
        gap = 0.000001
        boundary = MultiPolygon([
            box(-0.001, -1, 0.005, 1),
            box(0.005 + gap, -1, 0.011, 1),
        ])
        feature = {
            "type": "Feature",
            "properties": {"name": "Micro Cut", "length_mi": 0.691},
            "geometry": {
                "type": "LineString",
                "coordinates": [[0.0, 0.0], [0.01, 0.0]],
            },
        }

        kept = A.clip_features_to_area([feature], boundary, min_inside_mi=0)

        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["properties"]["length_mi"], 0.691)
        self.assertEqual(kept[0]["properties"]["full_length_mi"], 0.691)
        self.assertTrue(kept[0]["properties"]["clipped"])
        self.assertEqual(len(kept[0]["geometry"]["coordinates"]), 2)

    def test_legacy_floor_uses_rounded_miles_but_exact_area_uses_raw_miles(self):
        from shapely.geometry import box

        miles_per_degree = math.pi * 3958.7613 / 180.0
        delta = 0.0496 / miles_per_degree
        boundary = box(-0.001, -1, delta + 0.001, 1)
        feature = {
            "type": "Feature",
            "properties": {"name": "Rounding Floor", "length_mi": 0.05},
            "geometry": {
                "type": "LineString",
                "coordinates": [[0, 0], [delta, 0]],
            },
        }

        legacy = A.clip_features_to_area(
            [feature], boundary, min_inside_mi=0.05, area_name=None)
        exact = A.clip_features_to_area(
            [feature], boundary, min_inside_mi=0.05,
            area_name="Nationalpark Mols Bjerge")

        self.assertEqual(len(legacy), 1)
        self.assertEqual(legacy[0]["properties"]["length_mi"], 0.05)
        self.assertNotIn("clipped", legacy[0]["properties"])
        self.assertEqual(exact, [])

        ordinary_delta = 0.051 / miles_per_degree
        ordinary = {**feature, "geometry": {
            "type": "LineString",
            "coordinates": [[0, 0], [ordinary_delta, 0]],
        }}
        ordinary_boundary = box(-0.001, -1, ordinary_delta + 0.001, 1)
        self.assertEqual(len(A.clip_features_to_area(
            [ordinary], ordinary_boundary, min_inside_mi=0.05)), 1)
        self.assertEqual(len(A.clip_features_to_area(
            [ordinary], ordinary_boundary, min_inside_mi=0.05,
            area_name="Nationalpark Mols Bjerge")), 1)

    def test_no_match_returns_none(self):
        union, names = A.union_matching(self.AREAS, "nonexistent park")
        self.assertIsNone(union)
        self.assertEqual(names, set())


class DenmarkExtraAreaTags(unittest.TestCase):
    """dk-v2 accepts woods/heaths/islands as exact areas; the legacy default does not."""
    OSM = """<?xml version='1.0' encoding='UTF-8'?>
<osm version="0.6">
<node id="1" lat="56.0" lon="9.0" version="1"/><node id="2" lat="56.0" lon="9.1" version="1"/>
<node id="3" lat="56.1" lon="9.1" version="1"/><node id="4" lat="56.1" lon="9.0" version="1"/>
<way id="10" version="1"><nd ref="1"/><nd ref="2"/><nd ref="3"/><nd ref="4"/><nd ref="1"/></way>
<relation id="500" version="1"><member type="way" ref="10" role="outer"/>
<tag k="type" v="multipolygon"/><tag k="natural" v="wood"/><tag k="name" v="Testskov"/></relation>
</osm>
"""

    def _pbf(self):
        import tempfile
        path = Path(tempfile.mkdtemp()) / "t.osm"
        path.write_text(self.OSM, encoding="utf-8")
        return str(path)

    @unittest.skipUnless(HAVE_SHAPELY, "shapely not installed")
    def test_wood_relation_only_found_with_the_dk_extra_tags(self):
        try:
            import osmium  # noqa: F401
        except ImportError:
            self.skipTest("pyosmium not installed")
        path = self._pbf()
        self.assertEqual(A.select_exact(A.assemble_areas(path), "Testskov"), [])
        found = A.select_exact(A.assemble_areas(path, extra_tags=A.DK_EXTRA_AREA_TAGS), "Testskov")
        self.assertEqual([(a["osm_type"], a["osm_id"]) for a in found], [("relation", 500)])

    def test_assemble_cli_helper_passes_extra_tags_only_for_dk_v2(self):
        import types
        import assemble as asm
        calls = []
        mod = types.SimpleNamespace(
            DK_EXTRA_AREA_TAGS={"natural": {"wood"}},
            assemble_areas=lambda *a, **k: calls.append((a, k)) or [])
        asm._assemble_areas(mod, types.SimpleNamespace(inp="x.pbf", dk_rules="legacy"))
        asm._assemble_areas(mod, types.SimpleNamespace(inp="x.pbf"))
        asm._assemble_areas(mod, types.SimpleNamespace(inp="x.pbf", dk_rules="dk-v2"))
        self.assertEqual(calls[0], (("x.pbf",), {}))
        self.assertEqual(calls[1], (("x.pbf",), {}))
        self.assertEqual(calls[2], (("x.pbf",), {"extra_tags": {"natural": {"wood"}}}))
if __name__ == "__main__":
    unittest.main()
