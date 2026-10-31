"""Way-backed Danish area boundary (`--expected-area-way-id`), end to end through
the real assemble.py CLI and its real argument parser.

A synthetic Danish OSM file holds a way-backed park, a relation-backed wood and two
name-collision cases, so every positive and negative case runs the actual parser
with the relation branch as the control. No hand-built args, no mocks.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
try:
    import osmium  # noqa: F401
    import shapely  # noqa: F401
    HAVE_DEPS = True
except ImportError:
    HAVE_DEPS = False
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from test_dk_v3 import Osm  # noqa: E402
WAY_AREA = "Vejskov"            # way-backed park, lon 9.00-9.10, lat 56.00-56.10
REL_AREA = "Relskov"            # relation-backed wood, lon 9.30-9.40
TWIN_AREA = "Dobbelt"           # same name on a way AND a relation
TWO_WAYS = "Tvilling"           # same name on two ways
RELATION_ID = 7000
def ring(lon0, lat0, lon1, lat1):
    return [(lon0, lat0), (lon1, lat0), (lon1, lat1), (lon0, lat1), (lon0, lat0)]
def build_fixture():
    """Returns (osm_xml, ids) where ids maps a label to its OSM id."""
    osm = Osm()
    ids = {}
    path = {"highway": "path"}
    ids["vejskov"] = osm.way(ring(9.00, 56.00, 9.10, 56.10),
                             leisure="nature_reserve", name=WAY_AREA)
    # Helrute: 0.09 deg inside, 0.04 deg beyond the east edge (dk-v3: whole route).
    osm.route(901, "Vejrute", osm.chain(
        [[(9.01, 56.05), (9.04, 56.05)], [(9.04, 56.05), (9.09, 56.05)],
         [(9.09, 56.05), (9.14, 56.05)]], **path))
    osm.route(902, "Lille Vejsti", [osm.way(
        [(9.02, 56.02), (9.03, 56.02), (9.04, 56.021)], **path)])
    # relation-backed wood somewhere else, with its own route
    wood = osm.way(ring(9.30, 56.00, 9.40, 56.10))
    osm.relations.append((RELATION_ID, [wood], {
        "type": "multipolygon", "natural": "wood", "name": REL_AREA}))
    osm.route(903, "Relskovsti", [osm.way(
        [(9.31, 56.05), (9.34, 56.05), (9.37, 56.05)], **path)])
    # a way and a relation sharing one name
    ids["dobbelt_way"] = osm.way(ring(9.50, 56.00, 9.55, 56.05),
                                 leisure="nature_reserve", name=TWIN_AREA)
    twin = osm.way(ring(9.60, 56.00, 9.65, 56.05))
    osm.relations.append((7001, [twin], {
        "type": "multipolygon", "natural": "wood", "name": TWIN_AREA}))
    # two ways sharing one name
    ids["tvilling_a"] = osm.way(ring(9.70, 56.00, 9.75, 56.05),
                                leisure="nature_reserve", name=TWO_WAYS)
    ids["tvilling_b"] = osm.way(ring(9.80, 56.00, 9.85, 56.05),
                                leisure="nature_reserve", name=TWO_WAYS)
    return osm.xml(), ids
class WayCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not HAVE_DEPS:
            raise unittest.SkipTest("pyosmium/shapely not installed")
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        cls.osm = cls.dir / "fixture.osm"
        xml, cls.ids = build_fixture()
        cls.osm.write_text(xml, encoding="utf-8")
        cls.way_id = cls.ids["vejskov"]
        cls.count = 0
    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()
    def run_cli(self, area, *flags, rules="dk-v3", region="dk"):
        """Real assemble.py CLI. `flags` are the boundary-id options under test."""
        type(self).count += 1
        label = f"run{self.count}"
        out = self.dir / f"{label}.trails.geojson"
        report = self.dir / f"{label}.report.json"
        result = subprocess.run(
            [sys.executable, str(HERE / "assemble.py"), "--in", str(self.osm),
             "--out", str(out), "--only-area", area, "--require-exact-area",
             *flags, "--min-length-mi", "0.1", "--per-area-merge",
             "--region", region, "--dk-rules", rules,
             "--report-json", str(report)],
            capture_output=True, text=True)
        trails = None
        if out.exists():
            trails = {f["properties"]["name"]: f for f in
                      json.loads(out.read_text())["features"]}
        data = json.loads(report.read_text()) if report.exists() else None
        return result, trails, data, out
class PositiveWay(WayCase):
    def test_way_backed_area_assembles_and_reports_the_way(self):
        for rules in ("dk-v3", "dk-v2"):
            with self.subTest(rules=rules):
                result, trails, report, out = self.run_cli(
                    WAY_AREA, "--expected-area-way-id", str(self.way_id), rules=rules)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(report["status"], "ok")
                self.assertEqual(report["boundary_kind"], "way")
                self.assertEqual(report["expected_area_way_id"], self.way_id)
                self.assertIsNone(report["expected_area_relation_id"])
                self.assertEqual(report["boundary"], {
                    "name": WAY_AREA, "osm_type": "way", "osm_id": self.way_id})
                self.assertEqual(report["boundary_match_count"], 1)
                self.assertIn("Lille Vejsti", trails)
                self.assertIn("Vejrute", trails)
                sealed = json.loads(out.with_name(
                    out.name.replace(".trails.geojson", ".exact-area.geojson")
                ).read_text())
                props = sealed["features"][0]["properties"]
                self.assertEqual((props["osm_type"], props["osm_id"]),
                                 ("way", self.way_id))
    def test_dk_v3_rules_apply_unchanged_to_a_way_backed_area(self):
        _, v3, _, _ = self.run_cli(
            WAY_AREA, "--expected-area-way-id", str(self.way_id), rules="dk-v3")
        _, v2, _, _ = self.run_cli(
            WAY_AREA, "--expected-area-way-id", str(self.way_id), rules="dk-v2")
        east = lambda f: max(p[0] for line in f["geometry"]["coordinates"] for p in line)
        self.assertGreater(east(v3["Vejrute"]), 9.139)            # whole route
        self.assertTrue(v3["Vejrute"]["properties"]["dk_whole_route"])
        self.assertLessEqual(east(v2["Vejrute"]), 9.1001)         # clipped control
        self.assertNotIn("dk_whole_route", v2["Vejrute"]["properties"])
class NegativeWay(WayCase):
    def fails(self, area, *flags, text, **kw):
        result, trails, report, out = self.run_cli(area, *flags, **kw)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn(text, result.stderr)
        return report
    def test_wrong_way_id_fails_closed_with_a_failed_report(self):
        report = self.fails(WAY_AREA, "--expected-area-way-id", str(self.way_id + 1),
                            text="does not match expected")
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["boundary_kind"], "way")
        self.assertIsNone(report["boundary"])
    def test_way_id_for_a_relation_backed_boundary_fails(self):
        self.fails(REL_AREA, "--expected-area-way-id", str(RELATION_ID),
                   text="came from 'relation', not a way")
    def test_relation_id_for_a_way_backed_boundary_fails(self):
        report = self.fails(WAY_AREA, "--expected-area-relation-id", str(self.way_id),
                            text="came from 'way', not a relation")
        self.assertNotIn("boundary_kind", report)
    def test_same_name_way_and_relation_is_a_collision_for_both_flags(self):
        self.fails(TWIN_AREA, "--expected-area-way-id", str(self.ids["dobbelt_way"]),
                   text="found 2")
        self.fails(TWIN_AREA, "--expected-area-relation-id", "7001", text="found 2")
    def test_two_same_name_ways_are_a_collision(self):
        self.fails(TWO_WAYS, "--expected-area-way-id", str(self.ids["tvilling_a"]),
                   text="found 2")
    def test_missing_boundary_fails(self):
        self.fails("Findes Ikke", "--expected-area-way-id", "5", text="found 0")
    def test_both_flags_are_mutually_exclusive(self):
        result, _, _, _ = self.run_cli(
            WAY_AREA, "--expected-area-way-id", str(self.way_id),
            "--expected-area-relation-id", "7000")
        self.assertEqual(result.returncode, 2)
        self.assertIn("mutually exclusive", result.stderr)
    def test_neither_flag_is_refused(self):
        result, _, _, _ = self.run_cli(WAY_AREA)
        self.assertEqual(result.returncode, 2)
        self.assertIn("requires --expected-area-relation-id", result.stderr)
    def test_way_flag_needs_exact_area_and_a_positive_id(self):
        result = subprocess.run(
            [sys.executable, str(HERE / "assemble.py"), "--in", str(self.osm),
             "--out", str(self.dir / "x.geojson"), "--region", "dk",
             "--dk-rules", "dk-v3", "--expected-area-way-id", "5"],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("--expected-area-way-id requires --require-exact-area",
                      result.stderr)
        result, _, _, _ = self.run_cli(WAY_AREA, "--expected-area-way-id", "0")
        self.assertEqual(result.returncode, 2)
        self.assertIn("must be positive", result.stderr)
    def test_way_flag_is_denmark_only(self):
        for rules, region in (("legacy", "dk"), ("dk-v3", "us")):
            with self.subTest(rules=rules, region=region):
                result, _, _, _ = self.run_cli(
                    WAY_AREA, "--expected-area-way-id", str(self.way_id),
                    rules=rules, region=region)
                self.assertEqual(result.returncode, 2)
                self.assertIn("limited to Denmark", result.stderr)
class RelationBranchUnchanged(WayCase):
    def test_relation_run_report_has_no_way_keys_and_the_old_identity(self):
        result, trails, report, _ = self.run_cli(
            REL_AREA, "--expected-area-relation-id", str(RELATION_ID))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(report["expected_area_relation_id"], RELATION_ID)
        self.assertEqual(report["boundary"], {
            "name": REL_AREA, "osm_type": "relation", "osm_id": RELATION_ID})
        self.assertNotIn("boundary_kind", report)
        self.assertNotIn("expected_area_way_id", report)
        self.assertIn("Relskovsti", trails)
    def test_relation_failure_report_keeps_its_old_keys(self):
        result, _, report, _ = self.run_cli(
            REL_AREA, "--expected-area-relation-id", "7999")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(report["status"], "failed")
        self.assertNotIn("boundary_kind", report)
        self.assertNotIn("expected_area_way_id", report)
if __name__ == "__main__":
    unittest.main()
