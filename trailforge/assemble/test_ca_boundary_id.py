"""Canada boundary-by-id opt-in (`--boundary-by-id`, alias of `--dk-boundary-by-id`, only with
`--region ca`), end to end through the real assemble.py CLI on a synthetic Canadian OSM file. Positive,
fail-closed negatives, and the no-opt-in / US / Denmark controls. No mocks."""
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
PATH = {"highway": "path"}
TWIN = "Highland Test National Park"     # a boundary relation AND a nature_reserve relation share it
SOLO = "Solo Test Provincial Park"       # unique name
REL_PARK, REL_TWIN, REL_SOLO = 8100, 8101, 8102


def ring(lon0, lat0, lon1, lat1):
    return [(lon0, lat0), (lon1, lat0), (lon1, lat1), (lon0, lat1), (lon0, lat0)]


def area_relation(osm, relation_id, box, **tags):
    way = osm.way(ring(*box))
    osm.relations.append((relation_id, [way], {"type": "multipolygon", **tags}))


def build_fixture():
    osm = Osm()
    # Twin: REL_PARK (boundary=national_park, lon -61.0..-60.9) and REL_TWIN (nature_reserve, lon -60.6..-60.5).
    area_relation(osm, REL_PARK, (-61.00, 46.00, -60.90, 46.10), boundary="national_park", name=TWIN)
    area_relation(osm, REL_TWIN, (-60.60, 46.00, -60.50, 46.10), leisure="nature_reserve", name=TWIN)
    osm.route(801, "Skyline Test Trail", [osm.way(
        [(-60.98, 46.05), (-60.95, 46.05), (-60.92, 46.05)], **PATH)])
    osm.route(802, "Twin Side Trail", [osm.way(
        [(-60.58, 46.05), (-60.55, 46.05), (-60.52, 46.05)], **PATH)])
    area_relation(osm, REL_SOLO, (-61.50, 46.00, -61.40, 46.10), boundary="protected_area", name=SOLO)
    osm.route(803, "Solo Test Trail", [osm.way(
        [(-61.48, 46.05), (-61.45, 46.05), (-61.42, 46.05)], **PATH)])
    return osm.xml()


class CaBoundaryId(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not HAVE_DEPS:
            raise unittest.SkipTest("pyosmium/shapely not installed")
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        cls.osm = cls.dir / "fixture.osm"
        cls.osm.write_text(build_fixture(), encoding="utf-8")
        cls.count = 0

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_cli(self, area, kind, ident, *flags, region="ca", rules=None):
        type(self).count += 1
        label = f"run{self.count}"
        out = self.dir / f"{label}.trails.geojson"
        report = self.dir / f"{label}.report.json"
        cmd = [sys.executable, str(HERE / "assemble.py"), "--in", str(self.osm), "--out", str(out),
               "--only-area", area, "--require-exact-area", f"--expected-area-{kind}-id", str(ident),
               "--min-length-mi", "0.1", "--per-area-merge", "--report-json", str(report)]
        if region:
            cmd += ["--region", region]
        if rules:
            cmd += ["--dk-rules", rules]
        result = subprocess.run(cmd + list(flags), capture_output=True, text=True)
        trails = None
        if out.exists():
            trails = sorted(f["properties"]["name"] for f in json.loads(out.read_text())["features"])
        data = json.loads(report.read_text()) if report.exists() else None
        return result, trails, data

    def test_control_without_the_flag_refuses_a_duplicated_name(self):
        result, trails, report = self.run_cli(TWIN, "relation", REL_PARK)
        self.assertEqual(result.returncode, 2)
        self.assertIn("expected exactly one boundary named", result.stderr)
        self.assertEqual(report["boundary_match_count"], 2)
        self.assertNotIn("boundary_selection", report)

    def test_control_with_region_ca_but_no_flag_also_refuses(self):
        result, *_ = self.run_cli(TWIN, "relation", REL_TWIN, region="ca")
        self.assertEqual(result.returncode, 2)

    def test_flag_picks_each_twin_by_relation_id(self):
        for ident, want in ((REL_PARK, ["Skyline Test Trail"]), (REL_TWIN, ["Twin Side Trail"])):
            with self.subTest(ident):
                result, trails, report = self.run_cli(TWIN, "relation", ident, "--boundary-by-id")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(trails, want)
                self.assertEqual(report["boundary"], {"name": TWIN, "osm_type": "relation", "osm_id": ident})
                self.assertEqual(report["boundary_match_count"], 2)
                self.assertEqual(report["boundary_selection"], {
                    "mode": "boundary-by-id", "osm_type": "relation", "osm_id": ident, "osm_name": TWIN})

    def test_the_id_must_be_one_of_the_same_name_boundaries(self):
        for label, kind, ident in (("other-name relation", "relation", REL_SOLO),
                                   ("missing relation", "relation", 8999),
                                   ("relation id as way", "way", REL_PARK)):
            with self.subTest(label):
                result, trails, report = self.run_cli(
                    TWIN, "relation" if kind == "relation" else "way", ident, "--boundary-by-id")
                self.assertNotEqual(result.returncode, 0)
                self.assertIsNone(trails)

    def test_other_name_relation_is_refused_by_the_id_check_not_just_the_gate(self):
        result, _, report = self.run_cli(TWIN, "relation", REL_SOLO, "--boundary-by-id")
        self.assertEqual(result.returncode, 2)
        self.assertIn("exact-area validation failed", result.stderr)
        self.assertIn(f"is relation {REL_SOLO}", result.stderr)
        self.assertEqual(report["status"], "failed")

    def test_the_name_must_still_match(self):
        result, _, report = self.run_cli("Unknown Test Park", "relation", REL_PARK, "--boundary-by-id")
        self.assertEqual(result.returncode, 2)
        self.assertIn("no boundary is named", result.stderr)

    def test_an_unneeded_flag_fails_closed(self):
        result, trails, _ = self.run_cli(SOLO, "relation", REL_SOLO, "--boundary-by-id")
        self.assertEqual(result.returncode, 2)
        self.assertIn("boundary_by_id is not needed", result.stderr)
        self.assertIsNone(trails)

    def test_unique_name_without_the_flag_is_unchanged(self):
        result, trails, report = self.run_cli(SOLO, "relation", REL_SOLO)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(trails, ["Solo Test Trail"])
        self.assertNotIn("boundary_selection", report)

    def test_flag_is_unreachable_outside_region_ca(self):
        for region in ("us", "co", "vt", None):
            with self.subTest(region=region):
                result, trails, _ = self.run_cli(TWIN, "relation", REL_PARK, "--boundary-by-id", region=region)
                self.assertEqual(result.returncode, 2)
                self.assertIn("requires --dk-rules dk-v3 and exact Denmark assembly", result.stderr)
                self.assertIsNone(trails)

    def test_flag_with_region_ca_needs_legacy_rules_and_a_relation_id(self):
        result, *_ = self.run_cli(TWIN, "relation", REL_PARK, "--boundary-by-id", rules="dk-v3")
        self.assertEqual(result.returncode, 2)
        self.assertIn("requires exact-area assembly by relation id with the legacy rules", result.stderr)
        result, *_ = self.run_cli(TWIN, "way", 2001, "--boundary-by-id")
        self.assertEqual(result.returncode, 2)
        self.assertIn("requires exact-area assembly by relation id with the legacy rules", result.stderr)

    def test_flag_without_exact_area_is_refused(self):
        result = subprocess.run(
            [sys.executable, str(HERE / "assemble.py"), "--in", str(self.osm),
             "--out", str(self.dir / "x.geojson"), "--only-area", TWIN, "--region", "ca",
             "--boundary-by-id"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("requires exact-area assembly by relation id", result.stderr)

    def test_other_dk_opt_ins_stay_denmark_only_under_region_ca(self):
        for flag in ("--dk-container-by-id", "--dk-container-from-routes"):
            with self.subTest(flag):
                result, *_ = self.run_cli(TWIN, "relation", REL_PARK, flag)
                self.assertEqual(result.returncode, 2)
                self.assertIn(f"{flag} requires --dk-rules dk-v3 and exact Denmark", result.stderr)

    def test_denmark_flag_spelling_is_still_dk_v3_only_and_alias_is_the_same_flag(self):
        result, *_ = self.run_cli(TWIN, "relation", REL_PARK, "--dk-boundary-by-id", region="dk", rules="legacy")
        self.assertEqual(result.returncode, 2)
        self.assertIn("--dk-boundary-by-id requires --dk-rules dk-v3", result.stderr)
        result, *_ = self.run_cli(TWIN, "relation", REL_PARK, "--dk-boundary-by-id")
        self.assertEqual(result.returncode, 0, result.stderr)   # region ca: the old spelling is the same flag


if __name__ == "__main__":
    unittest.main()
