"""dk-v3 allow_classA_name: a reviewed per-relation exemption from the NAME rule of
dk-non-hiking (løberute, ...), for the class-A ruling (Quentin 2026-11-24).
Layers: the validator, the real assemble.py CLI on a synthetic OSM file (no opt-in
control, listed relation kept, not-listed løberute still dropped, stale entry fails
closed, scope refusals) and source mutations of the gate that must change the
outcome. Nothing here touches non-Danish code paths other than to prove they are
refused."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import quality  # noqa: E402
from test_dk_v3 import (AREA, AREA_RELATION, HAVE_DEPS, LAT0, LAT1,  # noqa: E402
                        LON0, LON1, Osm)
PATH = {"highway": "path"}
ROUTES = {
    801: "Vestskoven Rød løberute",     # listed in the opt-in tests
    802: "Vestskoven Blå løberute",     # a løberute that is NOT listed
    803: "Ren Rute",                    # normal control
    804: "Skov Hjertesti",              # name rule does not touch it
}
def build_fixture():
    osm = Osm()
    ring = [(LON0, LAT0), (LON1, LAT0), (LON1, LAT1), (LON0, LAT1), (LON0, LAT0)]
    boundary = osm.way(ring)
    osm.relations.append((AREA_RELATION, [boundary], {
        "type": "multipolygon", "natural": "wood", "name": AREA}))
    for index, (relation_id, name) in enumerate(ROUTES.items()):
        lat = 56.01 + 0.005 * index
        ways = osm.chain([[(9.01, lat), (9.03, lat)], [(9.03, lat), (9.05, lat)],
                          [(9.05, lat), (9.07, lat)]], **PATH)
        osm.route(relation_id, name, ways)
    return osm.xml()
def validate(entries):
    return quality.validate_dk_allow_classa_name(entries)
class ValidatorShape(unittest.TestCase):
    def test_a_good_list_is_normalized(self):
        self.assertEqual(validate([{"relation": "r801", "reason": " why "}]),
                         [{"relation": "r801", "reason": "why"}])
    def test_bad_lists_are_rejected(self):
        for bad in ("x", None, [], [{"relation": "r801"}],
                    [{"relation": "801", "reason": "x"}],
                    [{"relation": "r801", "reason": " "}],
                    [{"relation": "r801", "reason": "x", "extra": 1}],
                    [{"relation": "r801", "reason": "x"},
                     {"relation": "r801", "reason": "y"}]):
            with self.subTest(bad=str(bad)[:50]):
                with self.assertRaises(ValueError):
                    validate(bad)
    def test_the_quality_gate_refuses_other_rule_sets(self):
        for rules in ("legacy", "dk-v2"):
            with self.assertRaises(ValueError):
                quality.curate_exact_area(
                    [], ways={}, nodes={}, area_union=None, area_name="x",
                    region="dk", dk_rules=rules,
                    dk_allow_classa_name=[{"relation": "r801", "reason": "x"}])
class CliCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not HAVE_DEPS:
            raise unittest.SkipTest("pyosmium/shapely not installed")
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        cls.osm = cls.dir / "fixture.osm"
        cls.osm.write_text(build_fixture(), encoding="utf-8")
        cls.runs = {}
    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()
    def json_file(self, payload):
        path = self.dir / f"opt-{len(list(self.dir.glob('opt-*')))}.json"
        path.write_text(payload if isinstance(payload, str)
                        else json.dumps(payload), encoding="utf-8")
        return str(path)
    def entry(self, *relations):
        return self.json_file([{"relation": f"r{r}", "reason": "reviewed"}
                               for r in relations])
    def run_cli(self, *extra, rules="dk-v3", region="dk", exact=True, root=None):
        key = (rules, region, exact, extra, str(root))
        if key in self.runs:
            return self.runs[key]
        label = f"run-{len(self.runs)}"
        out = self.dir / f"{label}.trails.geojson"
        report = self.dir / f"{label}.report.json"
        command = [sys.executable, str((root or HERE) / "assemble.py"),
                   "--in", str(self.osm), "--out", str(out),
                   "--only-area", AREA, "--min-length-mi", "0.1",
                   "--per-area-merge", "--region", region, "--dk-rules", rules]
        if exact:
            command += ["--require-exact-area", "--expected-area-relation-id",
                        str(AREA_RELATION), "--report-json", str(report)]
        result = subprocess.run(command + list(extra), capture_output=True, text=True)
        trails = removed = report_doc = None
        if out.exists():
            trails = {f["properties"]["name"]: f for f in
                      json.loads(out.read_text())["features"]}
            removed_path = out.with_name(out.name.replace(
                ".trails.geojson", ".removed.geojson"))
            removed = {f["properties"]["name"]: f["properties"] for f in
                       json.loads(removed_path.read_text())["features"]}
        if report.exists():
            report_doc = json.loads(report.read_text())
        self.runs[key] = (result, trails, removed, report_doc)
        return self.runs[key]
class AllowClassAName(CliCase):
    def test_default_drops_every_loberute_by_name(self):
        result, trails, removed, report = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in ("Vestskoven Rød løberute", "Vestskoven Blå løberute"):
            self.assertNotIn(name, trails)
            self.assertEqual(removed[name]["removed_category"], "dk-non-hiking")
        self.assertIn("Ren Rute", trails)
        self.assertIn("Skov Hjertesti", trails)
        self.assertNotIn("dk_allow_classa_name", report)
    def test_listed_relation_is_kept_and_an_unlisted_loberute_still_drops(self):
        result, trails, removed, report = self.run_cli(
            "--dk-allow-classa-name", self.entry(801))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Vestskoven Rød løberute", trails)
        # mutation guard: the exemption is per relation id, not per name pattern
        self.assertNotIn("Vestskoven Blå løberute", trails)
        self.assertEqual(removed["Vestskoven Blå løberute"]["removed_category"],
                         "dk-non-hiking")
        self.assertEqual(report["status"], "ok")
    def test_every_other_trail_is_exactly_as_without_the_opt_in(self):
        _, base, _, _ = self.run_cli()
        _, opted, _, _ = self.run_cli("--dk-allow-classa-name", self.entry(801))
        for name in ("Ren Rute", "Skov Hjertesti"):
            self.assertEqual(json.dumps(base[name], sort_keys=True),
                             json.dumps(opted[name], sort_keys=True))
    def test_an_unneeded_entry_fails_closed(self):
        # r803 has no løberute name: the entry saves nothing, so it is stale
        result, _, _, report = self.run_cli(
            "--dk-allow-classa-name", self.entry(803))
        self.assertEqual(result.returncode, 2)
        self.assertIn("allow_classA_name r803", result.stderr + json.dumps(report))
    def test_an_unknown_relation_fails_closed(self):
        result, _, _, report = self.run_cli(
            "--dk-allow-classa-name", self.entry(99999))
        self.assertEqual(result.returncode, 2)
    def test_a_stale_entry_does_not_hide_a_good_one_but_fails_the_area(self):
        result, trails, _, _ = self.run_cli(
            "--dk-allow-classa-name", self.entry(801, 803))
        self.assertEqual(result.returncode, 2)
        self.assertIn("allow_classA_name r803", result.stderr)
    def test_the_opt_in_is_dk_v3_exact_denmark_only(self):
        listed = self.entry(801)
        for rules in ("legacy", "dk-v2"):
            with self.subTest(rules=rules):
                result = self.run_cli("--dk-allow-classa-name", listed,
                                      rules=rules)[0]
                self.assertEqual(result.returncode, 2)
                self.assertIn("requires --dk-rules dk-v3", result.stderr)
        result = self.run_cli("--dk-allow-classa-name", listed, exact=False)[0]
        self.assertEqual(result.returncode, 2)
        self.assertIn("limited to exact Denmark assembly", result.stderr)
        for region in ("ca", "us"):
            with self.subTest(region=region):
                result = self.run_cli("--dk-allow-classa-name", listed,
                                      region=region)[0]
                self.assertEqual(result.returncode, 2)
                self.assertIn("limited to exact Denmark assembly", result.stderr)
    def test_invalid_files_are_rejected_before_any_work(self):
        for payload in ("not json", {"relation": "r801"}, [],
                        [{"relation": "801", "reason": "x"}],
                        [{"relation": "r801", "reason": " "}]):
            with self.subTest(payload=str(payload)[:40]):
                result = subprocess.run(
                    [sys.executable, str(HERE / "assemble.py"),
                     "--in", str(self.dir / "missing.pbf"), "--out", "y.geojson",
                     "--region", "dk", "--require-exact-area",
                     "--dk-rules", "dk-v3",
                     "--dk-allow-classa-name", self.json_file(payload)],
                    capture_output=True, text=True)
                self.assertEqual(result.returncode, 2)
                self.assertIn("--dk-allow-classa-name is invalid", result.stderr)
class SourceMutations(CliCase):
    """Each mutant of the gate must change the outcome of the CLI runs above."""
    SOURCE = (HERE / "quality.py").read_text(encoding="utf-8")
    MUTANTS = (
        ("exempt every name-rule hit (ignore the relation ids)",
         "                    if hit:\n                        classa_used |= hit",
         "                    if True:\n                        classa_used |= hit"),
        ("exempt on any non-empty opt-in regardless of the match",
         "if rule == \"non-hiking-name\" and classa_allowed:\n                    # Registry",
         "if rule == \"non-hiking-name\" and classa_allowed:\n                    classa_used |= classa_allowed\n                    rule = None\n                    # Registry"),
        ("mark every listed relation used",
         "classa_used |= hit",
         "classa_used |= classa_allowed"),
        ("drop the stale-entry failure",
         "    for relation_id in sorted(classa_allowed - classa_used):",
         "    for relation_id in sorted(set()):"),
    )
    def outcome(self, root):
        listed = self.entry(801)
        stale = self.entry(801, 803)
        r1 = self.run_cli("--dk-allow-classa-name", listed, root=root)
        r2 = self.run_cli("--dk-allow-classa-name", stale, root=root)
        r3 = self.run_cli(root=root)
        return (sorted((r1[1] or {}).keys()), r1[0].returncode,
                r2[0].returncode, sorted((r3[1] or {}).keys()))
    def test_every_mutant_changes_the_outcome(self):
        baseline = self.outcome(HERE)
        self.assertEqual(baseline[1:3], (0, 2))
        survivors = []
        for label, old, new in self.MUTANTS:
            with self.subTest(mutant=label):
                self.assertEqual(self.SOURCE.count(old), 1, label)
                copy = self.dir / ("mut-" + str(len(list(self.dir.glob("mut-*")))))
                shutil.copytree(HERE, copy, ignore=shutil.ignore_patterns(
                    "__pycache__", "*.pbf"))
                (copy / "quality.py").write_text(
                    self.SOURCE.replace(old, new), encoding="utf-8")
                if self.outcome(copy) == baseline:
                    survivors.append(label)
        self.assertEqual(survivors, [])
if __name__ == "__main__":
    unittest.main()
