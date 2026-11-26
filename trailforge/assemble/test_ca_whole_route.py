"""Canada registry `whole_route` (`--ca-whole-route`): publish a listed trail with its complete geometry when the
exact area holds at least 75% of it and every listed rival area holds a strictly smaller share. End to end through
the real assemble.py CLI on synthetic Canadian OSM files, plus pure-function checks and one input-mutation (or
constant-mutation) test per gate. No mocks of the engine."""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_ca_rules as base  # noqa: E402
import ca_rules  # noqa: E402

Base, Fixture, PATH, LAT, PARK, REL_PARK = base.Base, base.Fixture, base.PATH, base.LAT, base.PARK, base.REL_PARK
FLAG = "--ca-whole-route"
RIVAL = "Rival Test Provincial Park"
REL_RIVAL = 9500
EDGE = -60.80                      # park east edge; the rival park starts there
WEST = -60.99
NAME = "Long Ridge Trail"
SOURCE = "https://example.gc.ca/parks/long-ridge"


def whole_fixture(share=0.8, rival=True, twin=False):
    """Park lon -61.00..-60.80; the trail starts at WEST and runs east past the edge so that `share` of its
    length is inside the park. The rest lies in the rival park (lon -60.80..-60.40) when `rival`."""
    osm = Fixture()
    osm.park()
    if rival:
        ring = osm.way([(EDGE, 46.00), (-60.40, 46.00), (-60.40, 46.10), (EDGE, 46.10), (EDGE, 46.00)])
        osm.rel(REL_RIVAL, [ring], type="multipolygon", boundary="protected_area", name=RIVAL)
    inside_deg = EDGE - WEST
    end = WEST + inside_deg / share
    osm.tagged_chain([(WEST, end, {**PATH, "name": NAME})])
    # a second, wholly inside trail that no entry lists (must stay byte-identical with and without the flag)
    osm.tagged_chain([(-60.97, -60.94, {**PATH, "name": "Short Inside Trail"})], lat=46.08)
    if twin:                                  # same name, elsewhere in the park: the merge key stays distinct
        osm.tagged_chain([(-60.97, -60.94, {**PATH, "name": NAME})], lat=46.02)
    return osm


def entry(name=NAME, rivals=None, **extra):
    row = {"name": name, "source": SOURCE, "reason": "reviewed: agency-listed signed route that runs past the polygon"}
    if rivals is not None:
        row["rivals"] = rivals
    row.update(extra)
    return row


RIVAL_ROW = {"osm": f"r{REL_RIVAL}", "name": RIVAL}


def trails(features):
    return {f["properties"]["name"]: f for f in features}


class Cli(Base):
    def test_control_without_the_flag_clips_the_trail_at_the_polygon(self):
        for region in ("ca", None):
            with self.subTest(region=region):
                result, features, report, *_ = self.run_cli(whole_fixture(), region=region)
                self.assertEqual(result.returncode, 0, result.stderr)
                trail = trails(features)[NAME]["properties"]
                self.assertTrue(trail["clipped"])
                self.assertNotIn("ca_whole_route", trail)
                self.assertLess(trail["length_mi"], trail["full_length_mi"])
                self.assertNotIn("ca_whole_route", report)

    def test_flag_publishes_the_complete_route_when_the_area_holds_at_least_75_percent(self):
        _, control, *_ = self.run_cli(whole_fixture())
        result, features, report, *_ = self.run_cli(whole_fixture(), [(FLAG, [entry(rivals=[RIVAL_ROW])])])
        self.assertEqual(result.returncode, 0, result.stderr)
        by = trails(features)
        props = by[NAME]["properties"]
        full = trails(control)[NAME]["properties"]["full_length_mi"]
        self.assertTrue(props["ca_whole_route"])
        self.assertNotIn("clipped", props)
        self.assertNotIn("full_length_mi", props)
        self.assertAlmostEqual(props["length_mi"], full, places=3)
        self.assertEqual(props["ca_route_share"], 0.8)
        lons = [c[0] for line in by[NAME]["geometry"]["coordinates"] for c in line]
        self.assertGreater(max(lons), EDGE)                              # real source coordinates past the edge
        self.assertEqual(report["status"], "ok")
        rec = report["ca_whole_route"]["decisions"][0]
        self.assertEqual((rec["name"], rec["share"], rec["source"]), (NAME, 0.8, SOURCE))
        self.assertEqual(rec["rivals"][0]["osm"], f"r{REL_RIVAL}")
        self.assertAlmostEqual(rec["rivals"][0]["share"], 0.2, places=3)
        self.assertEqual(report["ca_whole_route"]["failures"], [])

    def test_unlisted_trails_are_byte_identical_with_and_without_the_flag(self):
        _, control, *_ = self.run_cli(whole_fixture())
        _, opt, *_ = self.run_cli(whole_fixture(), [(FLAG, [entry(rivals=[RIVAL_ROW])])])
        self.assertEqual(trails(control)["Short Inside Trail"], trails(opt)["Short Inside Trail"])
        self.assertEqual(sorted(trails(control)), sorted(trails(opt)))

    # --- gate: the 75% floor (input mutation) ---------------------------------------------------------
    def test_gate_share_floor(self):
        for share, ok in ((0.80, True), (0.76, True), (0.74, False), (0.60, False), (0.51, False)):
            with self.subTest(share=share):
                result, features, report, *_ = self.run_cli(whole_fixture(share=share), [(FLAG, [entry()])])
                if ok:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertTrue(trails(features)[NAME]["properties"]["ca_whole_route"])
                else:
                    self.assertEqual(result.returncode, 2)
                    self.assertIn("ownership floor", result.stderr)
                    self.assertEqual(report["status"], "failed")

    def test_the_floor_cannot_be_lowered_by_an_entry(self):
        for extra in ({"min_share": 0.5}, {"share": 0.5}, {"floor": 0.1}):
            with self.subTest(extra=extra):
                result, features, *_ = self.run_cli(whole_fixture(share=0.6), [(FLAG, [entry(**extra)])])
                self.assertEqual(result.returncode, 2)
                self.assertIn("--ca-whole-route is invalid", result.stderr)
                self.assertIsNone(features)

    # --- gate: stale / unneeded entry (input mutation) -------------------------------------------------
    def test_gate_stale_name(self):
        result, _, report, *_ = self.run_cli(whole_fixture(), [(FLAG, [entry(name="No Such Trail")])])
        self.assertEqual(result.returncode, 2)
        self.assertIn("no assembled trail has this name", result.stderr)
        self.assertEqual(report["status"], "failed")

    def test_gate_unclipped_route_is_a_stale_entry(self):
        osm = Fixture()
        osm.park()
        osm.tagged_chain([(-60.97, -60.90, {**PATH, "name": NAME})])        # wholly inside
        result, _, report, *_ = self.run_cli(osm, [(FLAG, [entry()])])
        self.assertEqual(result.returncode, 2)
        self.assertIn("not clipped by the polygon", result.stderr)
        # mutation: the same entry on a route that does leave the polygon passes
        result, features, *_ = self.run_cli(whole_fixture(), [(FLAG, [entry()])])
        self.assertEqual(result.returncode, 0, result.stderr)

    # --- gate: ownership (one trail, one area) ---------------------------------------------------------
    def test_gate_rival_with_an_equal_or_larger_share_owns_the_trail(self):
        # share 0.5: the rival holds the other half (0.5) -> not strictly smaller; the 75% floor also refuses.
        result, _, _, *_ = self.run_cli(whole_fixture(share=0.5), [(FLAG, [entry(rivals=[RIVAL_ROW])])])
        self.assertEqual(result.returncode, 2)
        # overlapping rival polygon holding the same ground as this area: equal share refuses although the floor passes
        osm = whole_fixture(share=0.8, rival=False)
        ring = osm.way([(-61.00, 46.00), (-60.80, 46.00), (-60.80, 46.10), (-61.00, 46.10), (-61.00, 46.00)])
        osm.rel(REL_RIVAL, [ring], type="multipolygon", boundary="protected_area", name=RIVAL)
        result, _, report, *_ = self.run_cli(osm, [(FLAG, [entry(rivals=[RIVAL_ROW])])])
        self.assertEqual(result.returncode, 2)
        self.assertIn("the larger share owns the trail", result.stderr)
        # mutation: without the rival listed the same input passes (the gate is what refused it)
        result, features, *_ = self.run_cli(osm, [(FLAG, [entry()])])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(trails(features)[NAME]["properties"]["ca_whole_route"])

    def test_gate_rival_polygon_must_be_found_in_the_extract(self):
        result, _, _, *_ = self.run_cli(whole_fixture(rival=False), [(FLAG, [entry(rivals=[RIVAL_ROW])])])
        self.assertEqual(result.returncode, 2)
        self.assertIn("ownership cannot be proved", result.stderr)
        result, *_ = self.run_cli(whole_fixture(rival=False), [(FLAG, [entry()])])           # mutation: no rival listed
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_gate_a_rival_cannot_be_the_area_itself(self):
        result, _, _, *_ = self.run_cli(whole_fixture(), [(FLAG, [entry(rivals=[{"osm": f"r{REL_PARK}",
                                                                                   "name": PARK}])])])
        self.assertEqual(result.returncode, 2)
        self.assertIn("a rival cannot be the area itself", result.stderr)

    # --- gate: Canada-only, exact-area, legacy rules ---------------------------------------------------
    def test_the_flag_is_unreachable_outside_exact_canada_with_legacy_rules(self):
        cases = (("no region", dict(region=None)), ("us", dict(region="us")), ("dk", dict(region="dk")),
                 ("co", dict(region="co")),
                 ("dk-v3 rules", dict(rules="dk-v3")), ("dk-v2 rules", dict(rules="dk-v2")),
                 ("way-backed", dict(way=True)))
        for label, kwargs in cases:
            with self.subTest(label):
                result, features, *_ = self.run_cli(whole_fixture(), [(FLAG, [entry()])], **kwargs)
                self.assertEqual(result.returncode, 2)
                self.assertIn("--ca-whole-route requires --region ca", result.stderr)
                self.assertIsNone(features)

    def test_way_backed_lane_without_an_exact_area_is_accepted(self):
        # A way-backed Canadian area runs with --region ca and no --require-exact-area (its unique same-name
        # polygon is the clip). The decision uses that same polygon; a way-built rival is found by "w<id>".
        osm = whole_fixture(rival=False)
        ring = osm.way([(EDGE, 46.00), (-60.40, 46.00), (-60.40, 46.10), (EDGE, 46.10), (EDGE, 46.00)])
        osm.rel(REL_RIVAL, [ring], type="multipolygon", boundary="protected_area", name=RIVAL)
        rival = {"osm": f"w{ring}", "name": RIVAL}
        result, features, report, *_ = self.run_cli(osm, [(FLAG, [entry(rivals=[rival])])], exact=False)
        self.assertEqual(result.returncode, 2)                  # a relation-built polygon is not "w<ring>"
        self.assertIn("ownership cannot be proved", result.stderr)
        result, features, report, *_ = self.run_cli(osm, [(FLAG, [entry(rivals=[RIVAL_ROW])])], exact=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(trails(features)[NAME]["properties"]["ca_whole_route"])
        self.assertEqual(report["ca_whole_route"]["decisions"][0]["rivals"][0]["osm"], f"r{REL_RIVAL}")
        result, features, *_ = self.run_cli(osm, [(FLAG, [entry()])], exact=False, region=None)
        self.assertEqual(result.returncode, 2)                  # still needs --region ca
        _, control, *_ = self.run_cli(osm, exact=False)
        self.assertTrue(trails(control)[NAME]["properties"]["clipped"])

    def test_malformed_entries_are_rejected(self):
        good = entry(rivals=[RIVAL_ROW])
        bad = [
            "not json", {"name": NAME}, [], [{**good, "extra": 1}], [{**good, "name": ""}], [{**good, "name": 5}],
            [{**good, "source": "http://example.gc.ca/x"}], [{**good, "source": "ftp://example.gc.ca/x"}],
            [{**good, "source": "not a url"}], [{**good, "reason": "  "}], [good, good],
            [{k: v for k, v in good.items() if k != "reason"}], [{**good, "rivals": []}],
            [{**good, "rivals": [{"osm": "r0", "name": RIVAL}]}],
            [{**good, "rivals": [{"osm": 9500, "name": RIVAL}]}],
            [{**good, "rivals": [{"osm": "9500", "name": RIVAL}]}],
            [{**good, "rivals": [{"osm": "n9500", "name": RIVAL}]}],
            [{**good, "rivals": [{"osm": f"r{REL_RIVAL}"}]}],
            [{**good, "rivals": [RIVAL_ROW, RIVAL_ROW]}],
            [{**good, "rivals": [{**RIVAL_ROW, "extra": 1}]}],
            [{**good, "name": "Caf\u0065\u0301 Trail"}],                      # NFD text
        ]
        for entries in bad:
            with self.subTest(entries=str(entries)[:70]):
                payload = entries if isinstance(entries, str) else json.dumps(entries)
                result, features, *_ = self.run_cli(whole_fixture(), [(FLAG, payload)])
                self.assertEqual(result.returncode, 2)
                self.assertIn("--ca-whole-route is invalid", result.stderr)
                self.assertIsNone(features)


def feature(name, coords, kind="trail"):
    return {"type": "Feature", "properties": {"name": name, "kind": kind},
            "geometry": {"type": "LineString", "coordinates": coords}}


@unittest.skipUnless(base.HAVE_DEPS, "pyosmium/shapely not installed")
class Pure(unittest.TestCase):
    """Decision function on synthetic features: one gate per test, each with a weakened-gate mutation."""
    def setUp(self):
        from shapely.geometry import box
        self.area = box(0.0, 0.0, 1.0, 1.0)
        self.rival = {"osm_type": "relation", "osm_id": 77, "name": "Rival", "geom": box(1.0, 0.0, 2.0, 1.0)}
        self.row = entry(rivals=[{"osm": "r77", "name": "Rival"}])

    def run_decide(self, features, entries=None, areas=None, exact="r5", min_inside=0.05):
        return ca_rules.whole_route_decisions(features, self.area, entries or [self.row],
                                              [self.rival] if areas is None else areas, exact,
                                              min_inside_mi=min_inside)

    def line(self, east, name=NAME, kind="trail"):
        return feature(name, [[0.1, 0.5], [east, 0.5]], kind)

    def test_pass_records_every_number(self):
        decisions, failures = self.run_decide([self.line(1.3)])
        self.assertEqual(failures, [])
        record = decisions[0]
        self.assertAlmostEqual(record["share"], 0.9 / 1.2, places=3)
        self.assertAlmostEqual(record["full_mi"], record["inside_mi"] + record["outside_mi"], places=2)
        self.assertAlmostEqual(record["rivals"][0]["share"], 0.3 / 1.2, places=3)

    def test_floor_mutation(self):
        features = [self.line(1.6)]                       # 0.9 / 1.5 = 60% inside
        decisions, failures = self.run_decide(features)
        self.assertEqual((decisions, len(failures)), ({}, 1))
        with mock.patch.object(ca_rules, "CA_WHOLE_ROUTE_MIN_SHARE", 0.0):
            decisions, failures = self.run_decide(features)
        self.assertEqual((list(decisions), failures), ([0], []))     # the weakened gate would publish it
        # exactly on the floor passes, a hair under refuses
        self.assertEqual(self.run_decide([self.line(1.0 + 0.9 / 0.75 - 0.9)])[1], [])
        self.assertTrue(self.run_decide([self.line(1.0 + 0.9 / 0.74 - 0.9)])[1])

    def test_unclipped_mutation(self):
        features = [self.line(0.9)]
        self.assertIn("not clipped", self.run_decide(features)[1][0])
        with mock.patch.object(ca_rules, "CA_WHOLE_ROUTE_MIN_OUTSIDE_MI", -1.0):
            self.assertEqual(self.run_decide(features)[1], [])

    def test_missing_and_ambiguous_names(self):
        self.assertIn("no assembled trail", self.run_decide([self.line(1.3, name="Other")])[1][0])
        self.assertIn("2 assembled trails", self.run_decide([self.line(1.3), self.line(1.2)])[1][0])
        self.assertEqual(self.run_decide([self.line(1.3), self.line(1.2, name="Other")])[1], [])

    def test_a_poi_with_the_name_is_not_a_trail(self):
        self.assertIn("no assembled trail", self.run_decide([self.line(1.3, kind="poi")])[1][0])

    def test_rival_gates(self):
        features = [self.line(1.3)]
        overlapping = {**self.rival, "geom": self.area}                      # equal share
        failures = self.run_decide(features, areas=[overlapping])[1]
        self.assertIn("larger share owns", failures[0])
        self.assertIn("not exactly one polygon", self.run_decide(features, areas=[])[1][0])
        twice = [self.rival, dict(self.rival)]
        self.assertIn("not exactly one polygon", self.run_decide(features, areas=twice)[1][0])
        way = {**self.rival, "osm_type": "way"}                              # a way with the same number is not "r77"
        wayrow = entry(rivals=[{"osm": "w77", "name": "Rival"}])
        self.assertEqual(self.run_decide(features, entries=[wayrow], areas=[way])[1], [])      # but "w77" is
        self.assertIn("not exactly one polygon", self.run_decide(features, areas=[way])[1][0])
        self.assertIn("rival cannot be the area itself", self.run_decide(features, exact="r77")[1][0])
        # mutation: the same inputs without a listed rival pass
        self.assertEqual(self.run_decide(features, entries=[entry()], areas=[overlapping])[1], [])

    def test_min_inside_floor(self):
        features = [feature(NAME, [[0.5, 0.5], [1.1, 0.5]])]                  # 0.5 deg inside, 0.1 outside (83%)
        self.assertIn("ownership floor", self.run_decide(features, min_inside=60.0)[1][0])
        self.assertEqual(self.run_decide(features, min_inside=0.05)[1], [])

    def test_validate_is_exact(self):
        self.assertEqual(ca_rules.validate_whole_route([self.row]), [self.row])
        with self.assertRaises(ValueError):
            ca_rules.validate_whole_route([])
        with self.assertRaises(ValueError):
            ca_rules.validate_whole_route({"name": NAME})


if __name__ == "__main__":
    unittest.main()
