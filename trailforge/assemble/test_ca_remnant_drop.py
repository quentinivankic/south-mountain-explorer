"""Canada registry `remnant_drop` (publish_areas.py --ca-remnant-drop): drop the stray pieces of ONE named trail, only
when a piece is at most max_mi long (0.1 mi default, an entry may raise it up to 0.5) AND more than 5 m from the main
piece. Pure-function checks plus one input-mutation or constant-mutation test per gate. No mocks of the engine."""
import copy
import math
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ca_rules  # noqa: E402
import model  # noqa: E402

try:
    import shapely  # noqa: F401
    HAVE_SHAPELY = True
except ImportError:
    HAVE_SHAPELY = False

LAT = 50.0
LON0 = -100.0
M_LON = 111320.0 * math.cos(math.radians(LAT))      # metres per degree of longitude at LAT
M_LAT = 110540.0
SOURCE = "https://example.gc.ca/parks/ridge"
ENTRY = {"name": "Ridge Trail", "source": SOURCE, "reason": "stray remnant of the signed ridge hike"}


def piece(length_m, north_m=0.0, east_m=0.0):
    """A west-east line of `length_m` metres, `north_m` north of the main piece, starting `east_m` east of LON0."""
    lon = LON0 + east_m / M_LON
    return [(lon, LAT + north_m / M_LAT), (lon + length_m / M_LON, LAT + north_m / M_LAT)]


def miles(m):
    return m / 1609.344


MAIN = piece(2000.0)                                  # about 1.24 mi


def feature(name, lines, **props):
    base = {"name": name, "kind": "trail", "length_mi": 1.5, "id": name.lower().replace(" ", "-")}
    base.update(props)
    return {"type": "Feature", "properties": base,
            "geometry": {"type": "MultiLineString", "coordinates": [[list(p) for p in line] for line in lines]}}


class Validate(unittest.TestCase):
    def test_valid_entries(self):
        self.assertEqual(ca_rules.validate_remnant_drop([ENTRY]), [ENTRY])
        self.assertTrue(ca_rules.validate_remnant_drop([{**ENTRY, "max_mi": 0.3}]))
        self.assertTrue(ca_rules.validate_remnant_drop([{**ENTRY, "max_mi": 0.5}]))
        self.assertTrue(ca_rules.validate_remnant_drop([{**ENTRY, "max_mi": 1 / 8}]))

    def test_malformed_entries_are_rejected(self):
        bad = [[], {}, "x", [{**ENTRY, "extra": 1}], [{**ENTRY, "source": "http://x.example/y"}],
               [{k: v for k, v in ENTRY.items() if k != "reason"}], [ENTRY, ENTRY], [{**ENTRY, "name": ""}],
               [{**ENTRY, "reason": "x" * 601}], [{**ENTRY, "min_dist_m": 1}]]
        for case in bad:
            with self.subTest(case=case), self.assertRaises(ValueError):
                ca_rules.validate_remnant_drop(case)

    def test_max_mi_is_a_raise_with_a_hard_cap(self):
        for value in (0.1, 0.05, 0, -1, 0.51, 1.0, "0.3", True, None, float("nan")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ca_rules.validate_remnant_drop([{**ENTRY, "max_mi": value}])


@unittest.skipUnless(HAVE_SHAPELY, "shapely not installed")
class Split(unittest.TestCase):
    def split(self, lines, max_mi=0.1):
        return ca_rules.split_stray_pieces(lines, max_mi)

    def test_a_small_far_piece_is_stray(self):
        out = self.split([MAIN, piece(100.0, north_m=800.0)])
        self.assertEqual(out["status"], "ok")
        self.assertEqual(len(out["stray"]), 1)
        self.assertEqual(out["stray"][0]["indices"], [1])
        self.assertAlmostEqual(out["stray"][0]["distance_m"], 800.0, delta=2.0)

    def test_single_piece_has_nothing_to_drop(self):
        self.assertEqual(self.split([MAIN])["stray"], [])
        self.assertEqual(self.split([])["stray"], [])

    def test_length_gate_default_and_raise(self):
        near_limit, over = piece(miles_to_m(0.09), north_m=800.0), piece(miles_to_m(0.11), north_m=800.0)
        self.assertEqual(len(self.split([MAIN, near_limit])["stray"]), 1)
        self.assertEqual(self.split([MAIN, over])["stray"], [])                       # 0.11 mi: over the default
        self.assertEqual(len(self.split([MAIN, over], max_mi=0.3)["stray"]), 1)       # mutation: the raise drops it
        self.assertEqual(self.split([MAIN, piece(miles_to_m(0.31), north_m=800.0)], max_mi=0.3)["stray"], [])

    def test_distance_gate_is_more_than_five_metres(self):
        self.assertEqual(self.split([MAIN, piece(100.0, north_m=4.0)])["stray"], [])  # 4 m: a gap, kept
        self.assertEqual(self.split([MAIN, piece(100.0, north_m=4.9)])["stray"], [])
        self.assertEqual(len(self.split([MAIN, piece(100.0, north_m=5.5)])["stray"]), 1)
        self.assertEqual(len(self.split([MAIN, piece(100.0, north_m=8.0)])["stray"]), 1)

    def test_distance_constant_mutation_flips_the_near_piece(self):
        near = [MAIN, piece(100.0, north_m=4.0)]
        self.assertEqual(self.split(near)["stray"], [])
        with mock.patch.object(ca_rules, "CA_REMNANT_MIN_DIST_M", 0.0):
            self.assertEqual(len(self.split(near)["stray"]), 1)

    def test_length_constant_mutation_flips_the_default(self):
        far = [MAIN, piece(miles_to_m(0.11), north_m=800.0)]
        self.assertEqual(self.split(far)["stray"], [])
        self.assertEqual(len(self.split(far, max_mi=0.2)["stray"]), 1)

    def test_pieces_that_touch_form_one_component(self):
        # a 60 m piece joined end to end to a second 60 m piece: one 120 m (0.075 mi) component: stray together
        a, b = piece(60.0, north_m=800.0), piece(60.0, north_m=800.0, east_m=60.0)
        out = self.split([MAIN, a, b])
        self.assertEqual([item["indices"] for item in out["stray"]], [[1, 2]])
        # the same two pieces 0.5 m apart are still joined (link tolerance 1 m)
        c = piece(60.0, north_m=800.0, east_m=60.5)
        self.assertEqual([item["indices"] for item in self.split([MAIN, a, c])["stray"]], [[1, 2]])

    def test_link_tolerance_mutation_splits_the_component(self):
        # two touching 100 m pieces are ONE 0.124 mi component: over the 0.1 mi default, so nothing is dropped
        a, b = piece(100.0, north_m=800.0), piece(100.0, north_m=800.0, east_m=100.0)
        self.assertEqual(self.split([MAIN, a, b])["stray"], [])
        # mutation: with no linking each 0.062 mi piece would be dropped on its own
        with mock.patch.object(ca_rules, "CA_REMNANT_LINK_M", -1.0):
            self.assertEqual(len(self.split([MAIN, a, b])["stray"]), 2)

    def test_main_piece_is_the_longest_component(self):
        small_main = piece(900.0)
        big_far = piece(3000.0, north_m=2000.0)
        out = self.split([small_main, big_far, piece(50.0, north_m=1000.0)], max_mi=0.5)
        # the 3000 m piece is main; the 900 m piece (0.56 mi) is over the cap, the 50 m piece is stray
        self.assertEqual([item["indices"] for item in out["stray"]], [[2]])

    def test_equal_longest_components_are_ambiguous(self):
        out = self.split([piece(1000.0), piece(1000.0, east_m=1500.0), piece(50.0, north_m=400.0)])
        self.assertEqual(out["status"], "ambiguous-main")
        self.assertEqual(out["stray"], [])

    def test_unequal_longest_components_are_not_ambiguous(self):
        out = self.split([piece(1000.0), piece(1001.0, east_m=1500.0), piece(50.0, north_m=400.0)])
        self.assertEqual(out["status"], "ok")
        self.assertEqual([item["indices"] for item in out["stray"]], [[2]])

    def test_far_piece_over_the_cap_is_never_dropped(self):
        far = piece(miles_to_m(0.6), north_m=900.0)
        self.assertEqual(self.split([MAIN, far], max_mi=ca_rules.CA_REMNANT_CAP_MI)["stray"], [])


def miles_to_m(mi):
    return mi * 1609.344


@unittest.skipUnless(HAVE_SHAPELY, "shapely not installed")
class Drop(unittest.TestCase):
    def setUp(self):
        self.stray = piece(100.0, north_m=800.0)
        self.target = feature("Ridge Trail", [MAIN, self.stray])
        self.other = feature("Other Trail", [piece(1500.0, north_m=3000.0), piece(80.0, north_m=4500.0)])
        self.poi = {"type": "Feature", "properties": {"name": "Ridge Trail", "kind": "poi"},
                    "geometry": {"type": "Point", "coordinates": [LON0, LAT]}}

    def run_drop(self, features, entries=None):
        return ca_rules.drop_stray_remnants(features, entries or [ENTRY])

    def test_only_the_named_trail_changes(self):
        features = [self.target, self.other, self.poi]
        before = copy.deepcopy(features)
        out, report, failures = self.run_drop(features)
        self.assertEqual(failures, [])
        self.assertEqual(features, before)                                  # input not mutated
        self.assertEqual(out[1], before[1])                                 # the unlisted trail is byte-identical
        self.assertEqual(out[2], before[2])
        new = out[0]
        self.assertEqual(len(new["geometry"]["coordinates"]), 1)
        self.assertEqual(new["geometry"]["coordinates"][0], [list(p) for p in MAIN])
        for key in ("id", "name", "kind"):
            self.assertEqual(new["properties"][key], before[0]["properties"][key])
        self.assertEqual(new["properties"]["length_mi"], round(model.line_mi(MAIN), 3))
        self.assertEqual(report[0]["length_mi_before"], 1.5)
        self.assertEqual(report[0]["length_mi_after"], new["properties"]["length_mi"])
        self.assertEqual(len(report[0]["dropped"]), 1)

    def test_unlisted_entries_do_nothing(self):
        out, report, failures = ca_rules.drop_stray_remnants([self.target], [])
        self.assertEqual((out, report, failures), ([self.target], [], []))

    def test_name_gates_fail_closed(self):
        _, _, failures = self.run_drop([self.other])                        # no such trail: stale entry
        self.assertTrue(failures and "stale entry" in failures[0])
        twin = copy.deepcopy(self.target)
        out, _, failures = self.run_drop([self.target, twin])               # two trails with that name
        self.assertTrue(failures and "ambiguous" in failures[0])
        self.assertEqual(out, [self.target, twin])
        _, _, failures = self.run_drop([self.poi])                          # a poi with the name is not a trail
        self.assertTrue(failures and "stale entry" in failures[0])

    def test_nothing_to_drop_fails_closed(self):
        clean = feature("Ridge Trail", [MAIN])
        _, _, failures = self.run_drop([clean])
        self.assertTrue(failures and "no stray piece" in failures[0])
        near = feature("Ridge Trail", [MAIN, piece(100.0, north_m=3.0)])
        _, _, failures = self.run_drop([near])
        self.assertTrue(failures)
        big = feature("Ridge Trail", [MAIN, piece(miles_to_m(0.2), north_m=800.0)])
        _, _, failures = self.run_drop([big])
        self.assertTrue(failures)
        out, report, failures = self.run_drop([big], [{**ENTRY, "max_mi": 0.3}])    # mutation: explicit raise
        self.assertEqual(failures, [])
        self.assertEqual(len(out[0]["geometry"]["coordinates"]), 1)
        self.assertEqual(report[0]["max_mi"], 0.3)

    def test_ambiguous_main_and_non_line_fail_closed(self):
        tie = feature("Ridge Trail", [piece(1000.0), piece(1000.0, east_m=1500.0), piece(50.0, north_m=400.0)])
        out, _, failures = self.run_drop([tie])
        self.assertTrue(failures and "ambiguous" in failures[0])
        self.assertEqual(out, [tie])
        point = {"type": "Feature", "properties": {"name": "Ridge Trail", "kind": "trail"},
                 "geometry": {"type": "Point", "coordinates": [LON0, LAT]}}
        _, _, failures = self.run_drop([point])
        self.assertTrue(failures and "not a line" in failures[0])

    def test_line_string_geometry_has_nothing_to_drop(self):
        single = {"type": "Feature", "properties": {"name": "Ridge Trail", "kind": "trail", "length_mi": 1.2},
                  "geometry": {"type": "LineString", "coordinates": [list(p) for p in MAIN]}}
        _, _, failures = self.run_drop([single])
        self.assertTrue(failures and "no stray piece" in failures[0])


if __name__ == "__main__":
    unittest.main()
