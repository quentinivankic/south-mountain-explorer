"""Canada ring-way dedupe (`--ca-dedupe-ring-way`, registry `dedupe_ring_way`), end to end through the
real assemble.py CLI on a synthetic Canadian OSM file: a boundary relation and its own tagged outer ring
way carry the identical name (Cypress Hills Centre Block). Positive, fail-closed negatives, and the
no-flag / non-Canada / unrelated-twin controls. No mocks."""
import json
import re
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
RING_PARK = "Ringed Test Park"      # relation + its own outer ring way share this name
LONE_PARK = "Lone Test Park"        # relation + an UNRELATED same-name closed way
PLAIN_PARK = "Plain Test Park"      # unique name
REL_RING, REL_LONE, REL_PLAIN = 8200, 8201, 8202
def ring(lon0, lat0, lon1, lat1):
    return [(lon0, lat0), (lon1, lat0), (lon1, lat1), (lon0, lat1), (lon0, lat0)]
def build_fixture():
    osm = Osm()
    outer = osm.way(ring(-61.00, 46.00, -60.90, 46.10), boundary="protected_area", name=RING_PARK)
    osm.relations.append((REL_RING, [outer], {"type": "multipolygon", "leisure": "nature_reserve",
                                              "name": RING_PARK}))
    osm.route(811, "Ring Test Trail", [osm.way([(-60.98, 46.05), (-60.95, 46.05), (-60.92, 46.05)], **PATH)])
    own = osm.way(ring(-60.60, 46.00, -60.50, 46.10))
    osm.relations.append((REL_LONE, [own], {"type": "multipolygon", "leisure": "nature_reserve",
                                            "name": LONE_PARK}))
    # same name, closed, tagged, but NOT a member of REL_LONE
    osm.way(ring(-60.60, 46.00, -60.50, 46.10), boundary="protected_area", name=LONE_PARK)
    osm.route(812, "Lone Test Trail", [osm.way([(-60.58, 46.05), (-60.55, 46.05), (-60.52, 46.05)], **PATH)])
    plain = osm.way(ring(-61.50, 46.00, -61.40, 46.10))
    osm.relations.append((REL_PLAIN, [plain], {"type": "multipolygon", "boundary": "protected_area",
                                               "name": PLAIN_PARK}))
    osm.route(813, "Plain Test Trail", [osm.way([(-61.48, 46.05), (-61.45, 46.05), (-61.42, 46.05)], **PATH)])
    xml = osm.xml()
    # the shared builder writes empty member roles; the area assembler needs `outer`
    return re.sub(r'(<relation id="82\d\d" version="1">.*?</relation>)',
                  lambda m: m.group(1).replace('role=""', 'role="outer"'), xml, flags=re.S)
class CaRingWay(unittest.TestCase):
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
    def run_cli(self, area, ident, *flags, region="ca", rules=None, kind="relation"):
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
    def test_control_without_the_flag_sees_two_boundaries_and_refuses(self):
        for region in ("ca", None, "us"):
            with self.subTest(region=region):
                result, trails, report = self.run_cli(RING_PARK, REL_RING, region=region)
                self.assertEqual(result.returncode, 2)
                self.assertIn("expected exactly one boundary named", result.stderr)
                self.assertEqual(report["boundary_match_count"], 2)
                self.assertIsNone(trails)
    def test_flag_dedupes_the_relation_and_its_own_outer_ring_way(self):
        result, trails, report = self.run_cli(RING_PARK, REL_RING, "--ca-dedupe-ring-way")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(trails, ["Ring Test Trail"])
        self.assertEqual(report["boundary"], {"name": RING_PARK, "osm_type": "relation", "osm_id": REL_RING})
        self.assertEqual(report["boundary_match_count"], 2)      # evidence keeps the raw count
        self.assertEqual(report["ca_ring_way_dedupe"], {"relation": REL_RING, "dropped_ring_ways": [2001]})
    def test_an_unrelated_same_name_way_is_not_deduped(self):
        result, trails, report = self.run_cli(LONE_PARK, REL_LONE, "--ca-dedupe-ring-way")
        self.assertEqual(result.returncode, 2)
        self.assertIn("dedupe_ring_way is not needed", result.stderr)
        self.assertIsNone(trails)
        self.assertEqual(report["status"], "failed")
    def test_an_unneeded_flag_fails_closed(self):
        result, trails, _ = self.run_cli(PLAIN_PARK, REL_PLAIN, "--ca-dedupe-ring-way")
        self.assertEqual(result.returncode, 2)
        self.assertIn("dedupe_ring_way is not needed", result.stderr)
        self.assertIsNone(trails)
    def test_unique_name_without_the_flag_is_unchanged(self):
        result, trails, report = self.run_cli(PLAIN_PARK, REL_PLAIN)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(trails, ["Plain Test Trail"])
        self.assertNotIn("ca_ring_way_dedupe", report)
    def test_the_relation_id_must_still_match(self):
        result, trails, _ = self.run_cli(RING_PARK, REL_LONE, "--ca-dedupe-ring-way")
        self.assertEqual(result.returncode, 2)
        self.assertIsNone(trails)
    def test_flag_is_unreachable_outside_region_ca(self):
        for region in ("us", "dk", "co", None):
            with self.subTest(region=region):
                result, trails, _ = self.run_cli(RING_PARK, REL_RING, "--ca-dedupe-ring-way", region=region)
                self.assertEqual(result.returncode, 2)
                self.assertIn("--ca-dedupe-ring-way requires --region ca", result.stderr)
                self.assertIsNone(trails)
    def test_flag_needs_legacy_rules_a_relation_id_and_excludes_boundary_by_id(self):
        for label, args, kwargs in (
                ("dk-v3 rules", (RING_PARK, REL_RING, "--ca-dedupe-ring-way"), {"rules": "dk-v3"}),
                ("way id", (RING_PARK, 2001, "--ca-dedupe-ring-way"), {"kind": "way"}),
                ("boundary-by-id", (RING_PARK, REL_RING, "--ca-dedupe-ring-way", "--boundary-by-id"), {})):
            with self.subTest(label):
                result, trails, _ = self.run_cli(*args, **kwargs)
                self.assertEqual(result.returncode, 2)
                self.assertIn("--ca-dedupe-ring-way requires --region ca", result.stderr)
                self.assertIsNone(trails)
    def test_flag_without_exact_area_is_refused(self):
        result = subprocess.run(
            [sys.executable, str(HERE / "assemble.py"), "--in", str(self.osm),
             "--out", str(self.dir / "x.geojson"), "--only-area", RING_PARK, "--region", "ca",
             "--ca-dedupe-ring-way"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("--ca-dedupe-ring-way requires --region ca", result.stderr)
if __name__ == "__main__":
    unittest.main()
