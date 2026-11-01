"""Legacy-rules curated exclusion (`--dk-curated-exclude` with `--dk-rules legacy`, registry
`legacy_exclude_trails`), end to end through the real assemble.py CLI on a synthetic Danish OSM
file. Control run without the flag, fail-closed negatives, byte-identity of the kept trails."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from test_dk_boundary_id import BoundaryIdCase, HAVE_DEPS, PATH, ring  # noqa: E402
from test_dk_v3 import Osm  # noqa: E402
AREA = "Legacyskov"
REL = 8100
def entry(ident, name, reason="reviewed: not a hiking destination"):
    return {"id": ident, "name": name, "reason": reason}
def build_fixture():
    osm = Osm()
    way = osm.way(ring(9.00, 56.00, 9.10, 56.10))
    osm.relations.append((REL, [way], {
        "type": "multipolygon", "landuse": "forest", "name": AREA}))
    for number, name, lat in ((801, "Naturruten", 56.02), (802, "Motionsruten", 56.05),
                              (803, "Boernestien", 56.08)):
        osm.route(number, name, [osm.way(
            [(9.02, lat), (9.05, lat), (9.08, lat)], **PATH)])
    return osm.xml()
class LegacyCuratedExclude(BoundaryIdCase):
    @classmethod
    def setUpClass(cls):
        if not HAVE_DEPS:
            raise unittest.SkipTest("pyosmium/shapely not installed")
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        cls.osm = cls.dir / "fixture.osm"
        cls.osm.write_text(build_fixture(), encoding="utf-8")
        cls.count = 0
    def run_legacy(self, items, **kwargs):
        flags = []
        if items is not None:
            path = self.dir / f"exclude{self.count}.json"
            path.write_text(json.dumps(items), encoding="utf-8")
            flags = ["--dk-curated-exclude", str(path)]
        return self.run_cli(AREA, ("relation", REL), *flags, rules="legacy", **kwargs)
    def test_control_without_the_flag_keeps_every_route(self):
        result, trails, removed, report, _ = self.run_legacy(None)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sorted(trails), ["Boernestien", "Motionsruten", "Naturruten"])
        self.assertNotIn("dk_curated_excluded", report["quality"])
        self.assertNotIn("dk_rules", report["quality"])
        type(self).control = trails
    def test_named_exclusion_removes_exactly_the_listed_trails(self):
        _, control, *_ = self.run_legacy(None)
        result, trails, removed, report, _ = self.run_legacy(
            [entry("n:Motionsruten", "Motionsruten"),
             entry("n*:Boernestien", "Boernestien")])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sorted(trails), ["Naturruten"])
        # the kept trail is byte-identical to the run without the flag
        self.assertEqual(json.dumps(trails["Naturruten"], sort_keys=True),
                         json.dumps(control["Naturruten"], sort_keys=True))
        for name in ("Motionsruten", "Boernestien"):
            self.assertEqual(removed[name]["removed_category"], "dk-curated-exclude")
            self.assertEqual(removed[name]["curated_exclude_reason"],
                             "reviewed: not a hiking destination")
        quality = report["quality"]
        self.assertEqual(quality["dk_curated_exclude_failures"], [])
        self.assertEqual(sorted(e["name"] for e in quality["dk_curated_excluded"]),
                         ["Boernestien", "Motionsruten"])
    def test_stale_exclusion_fails_the_area_closed(self):
        result, *_ = self.run_legacy([entry("n:Findes Ikke", "Findes Ikke")])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("dk-curated-exclude failures", result.stderr)
    def test_exclusion_whose_name_disagrees_fails_closed(self):
        result, *_ = self.run_legacy([entry("r801", "Andet Navn")])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("dk-curated-exclude failures", result.stderr)
    def test_flag_is_limited_to_exact_denmark_for_legacy(self):
        for kwargs in ({"region": "us"},):
            with self.subTest(**kwargs):
                result, *_ = self.run_legacy([entry("n:Motionsruten", "Motionsruten")], **kwargs)
                self.assertEqual(result.returncode, 2)
                self.assertIn("limited to exact Denmark assembly", result.stderr)
if __name__ == "__main__":
    unittest.main()
