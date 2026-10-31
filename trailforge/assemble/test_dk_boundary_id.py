"""dk-v3 boundary-by-id opt-ins (`--dk-boundary-by-id`, `--dk-container-by-id`), end to
end through the real assemble.py CLI on a synthetic Danish OSM file. Every opt-in has
its no-opt-in control, fail-closed negatives and an unrelated-area control. No mocks."""
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
TWIN = "Dobbeltskov"            # one relation AND one way carry this name
SOLO = "Enkeltskov"             # a unique name
CONTAINER = "Containerområde"   # registry name of a container-backed area
REL_TWIN, REL_SOLO, REL_UNNAMED, REL_PARK, REL_OTHER = 7100, 7101, 7102, 7103, 7104
def ring(lon0, lat0, lon1, lat1):
    return [(lon0, lat0), (lon1, lat0), (lon1, lat1), (lon0, lat1), (lon0, lat0)]
def build_fixture():
    osm = Osm()
    ids = {}
    def wood(relation_id, box, **tags):
        way = osm.way(ring(*box))
        osm.relations.append((relation_id, [way], {
            "type": "multipolygon", **tags}))
    # Twin name: a relation (lon 9.0-9.1) and a closed way (lon 9.3-9.4).
    wood(REL_TWIN, (9.00, 56.00, 9.10, 56.10), landuse="forest", name=TWIN)
    ids["twin_way"] = osm.way(ring(9.30, 56.00, 9.40, 56.10),
                              landuse="forest", name=TWIN)
    osm.route(901, "Relationsrute", [osm.way(
        [(9.02, 56.05), (9.05, 56.05), (9.08, 56.05)], **PATH)])
    osm.route(902, "Vejrute", [osm.way(
        [(9.32, 56.05), (9.35, 56.05), (9.38, 56.05)], **PATH)])
    # A unique name (control for "not needed").
    wood(REL_SOLO, (9.50, 56.00, 9.60, 56.10), landuse="forest", name=SOLO)
    osm.route(903, "Enkeltrute", [osm.way(
        [(9.52, 56.05), (9.55, 56.05), (9.58, 56.05)], **PATH)])
    # Unnamed forest relation (container) with an included route inside, an unlisted
    # route inside, and an included route outside the polygon.
    wood(REL_UNNAMED, (9.70, 56.00, 9.80, 56.10), landuse="forest")
    osm.route(904, "Valgt Rute", [osm.way(
        [(9.72, 56.05), (9.75, 56.05), (9.78, 56.05)], **PATH)])
    osm.route(905, "Ikke Valgt", [osm.way(
        [(9.72, 56.07), (9.75, 56.07), (9.78, 56.07)], **PATH)])
    osm.route(906, "Udenfor Rute", [osm.way(
        [(9.83, 56.03), (9.86, 56.03), (9.89, 56.03)], **PATH)])
    # A non-forest polygon (leisure=park) and an other-named forest.
    wood(REL_PARK, (9.90, 56.00, 10.00, 56.10), leisure="park")
    wood(REL_OTHER, (10.10, 56.00, 10.20, 56.10), landuse="forest",
         name="Et Andet Navn")
    osm.route(907, "Andenrute", [osm.way(
        [(10.12, 56.05), (10.15, 56.05), (10.18, 56.05)], **PATH)])
    # Unnamed forest WAY (way-backed container) with its own route.
    ids["unnamed_way"] = osm.way(ring(10.30, 56.00, 10.40, 56.10),
                                 landuse="forest")
    osm.route(908, "Vejcontainerrute", [osm.way(
        [(10.32, 56.05), (10.35, 56.05), (10.38, 56.05)], **PATH)])
    return osm.xml(), ids
def include(*pairs):
    return [{"id": rid, "name": name, "reason": "reviewed official walk"}
            for rid, name in pairs]
class BoundaryIdCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not HAVE_DEPS:
            raise unittest.SkipTest("pyosmium/shapely not installed")
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        cls.osm = cls.dir / "fixture.osm"
        xml, cls.ids = build_fixture()
        cls.osm.write_text(xml, encoding="utf-8")
        cls.count = 0
    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()
    def run_cli(self, area, boundary, *flags, rules="dk-v3", region="dk",
                listed=None):
        """boundary = ("relation"|"way", id). `listed` = include_trails entries."""
        type(self).count += 1
        label = f"run{self.count}"
        out = self.dir / f"{label}.trails.geojson"
        report = self.dir / f"{label}.report.json"
        kind, ident = boundary
        extra = []
        if listed is not None:
            path = self.dir / f"{label}.include.json"
            path.write_text(json.dumps(listed), encoding="utf-8")
            extra = ["--dk-curated-include", str(path)]
        result = subprocess.run(
            [sys.executable, str(HERE / "assemble.py"), "--in", str(self.osm),
             "--out", str(out), "--only-area", area, "--require-exact-area",
             f"--expected-area-{kind}-id", str(ident),
             "--min-length-mi", "0.1", "--per-area-merge",
             "--region", region, "--dk-rules", rules,
             "--report-json", str(report), *extra, *flags],
            capture_output=True, text=True)
        trails = removed = sealed = None
        if out.exists():
            trails = {f["properties"]["name"]: f for f in
                      json.loads(out.read_text())["features"]}
            removed = {f["properties"]["name"]: f["properties"] for f in
                       json.loads(out.with_name(out.name.replace(
                           ".trails.geojson", ".removed.geojson")).read_text()
                       )["features"]}
            sealed_path = out.with_name(out.name.replace(
                ".trails.geojson", ".exact-area.geojson"))
            if sealed_path.exists():
                sealed = json.loads(sealed_path.read_text())[
                    "features"][0]["properties"]
        data = json.loads(report.read_text()) if report.exists() else None
        return result, trails, removed, data, sealed
class BoundaryById(BoundaryIdCase):
    FLAG = "--dk-boundary-by-id"
    def test_control_without_the_flag_still_refuses_a_duplicated_name(self):
        result, trails, _, report, _ = self.run_cli(
            TWIN, ("relation", REL_TWIN))
        self.assertEqual(result.returncode, 2)
        self.assertIn("expected exactly one boundary named", result.stderr)
        self.assertEqual(report["boundary_match_count"], 2)
        self.assertNotIn("boundary_selection", report)
    def test_flag_picks_the_relation_by_id(self):
        result, trails, _, report, sealed = self.run_cli(
            TWIN, ("relation", REL_TWIN), self.FLAG)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(report["boundary"], {
            "name": TWIN, "osm_type": "relation", "osm_id": REL_TWIN})
        self.assertEqual(report["boundary_match_count"], 2)   # honest: two by name
        self.assertEqual(report["boundary_selection"], {
            "mode": "boundary-by-id", "osm_type": "relation",
            "osm_id": REL_TWIN, "osm_name": TWIN})
        self.assertEqual((sealed["osm_type"], sealed["osm_id"], sealed["name"]),
                         ("relation", REL_TWIN, TWIN))
        self.assertNotIn("container_by_id", sealed)
        self.assertEqual(sorted(trails), ["Relationsrute"])   # the twin way's route is outside
    def test_flag_picks_the_way_by_id(self):
        result, trails, _, report, sealed = self.run_cli(
            TWIN, ("way", self.ids["twin_way"]), self.FLAG)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(report["boundary_kind"], "way")
        self.assertEqual((sealed["osm_type"], sealed["osm_id"]),
                         ("way", self.ids["twin_way"]))
        self.assertEqual(sorted(trails), ["Vejrute"])
    def test_the_id_must_be_one_of_the_same_name_boundaries(self):
        for label, boundary in (
                ("other-name relation", ("relation", REL_SOLO)),
                ("missing relation", ("relation", 7999)),
                ("wrong kind", ("way", REL_TWIN)),
                ("way that is not a twin", ("way", self.ids["unnamed_way"]))):
            with self.subTest(label):
                result, trails, _, report, _ = self.run_cli(
                    TWIN, boundary, self.FLAG)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("exact-area validation failed", result.stderr)
                self.assertIn("is " + boundary[0], result.stderr)
                self.assertEqual(report["status"], "failed")
    def test_the_name_must_still_match(self):
        result, _, _, report, _ = self.run_cli(
            "Ukendt Skov", ("relation", REL_TWIN), self.FLAG)
        self.assertEqual(result.returncode, 2)
        self.assertIn("no boundary is named", result.stderr)
    def test_an_unneeded_flag_fails_closed(self):
        result, _, _, report, _ = self.run_cli(
            SOLO, ("relation", REL_SOLO), self.FLAG)
        self.assertEqual(result.returncode, 2)
        self.assertIn("boundary_by_id is not needed", result.stderr)
    def test_unique_name_without_the_flag_is_unchanged(self):
        result, trails, _, report, sealed = self.run_cli(
            SOLO, ("relation", REL_SOLO))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sorted(trails), ["Enkeltrute"])
        self.assertNotIn("boundary_selection", report)
        self.assertEqual(sorted(sealed), [
            "geometry_sha256", "name", "osm_id", "osm_type"])
    def test_flag_is_limited_to_dk_v3_exact_denmark(self):
        for kwargs in ({"rules": "dk-v2"}, {"rules": "legacy"},
                       {"region": "us"}):
            with self.subTest(**kwargs):
                result, *_ = self.run_cli(
                    TWIN, ("relation", REL_TWIN), self.FLAG, **kwargs)
                self.assertEqual(result.returncode, 2)
                self.assertIn(
                    "--dk-boundary-by-id requires --dk-rules dk-v3",
                    result.stderr)
    def test_both_flags_are_mutually_exclusive(self):
        result, *_ = self.run_cli(
            TWIN, ("relation", REL_TWIN), self.FLAG, "--dk-container-by-id",
            listed=include(("r901", "Relationsrute")))
        self.assertEqual(result.returncode, 2)
        self.assertIn("mutually exclusive", result.stderr)
class ContainerById(BoundaryIdCase):
    FLAG = "--dk-container-by-id"
    LISTED = include(("r904", "Valgt Rute"), ("r906", "Udenfor Rute"))
    def test_control_without_the_flag_finds_no_boundary_by_that_name(self):
        result, *_ = self.run_cli(
            CONTAINER, ("relation", REL_UNNAMED), listed=self.LISTED)
        self.assertEqual(result.returncode, 2)
        self.assertIn("found 0", result.stderr)
    def test_unnamed_forest_is_the_container_and_trails_come_from_the_list(self):
        result, trails, removed, report, sealed = self.run_cli(
            CONTAINER, ("relation", REL_UNNAMED), self.FLAG,
            listed=self.LISTED)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sorted(trails), ["Udenfor Rute", "Valgt Rute"])
        # The unlisted route inside the polygon is dropped, visibly.
        self.assertEqual(removed["Ikke Valgt"]["removed_category"],
                         "dk-container-only")
        self.assertEqual(report["boundary"], {
            "name": CONTAINER, "osm_type": "relation", "osm_id": REL_UNNAMED})
        self.assertEqual(report["boundary_selection"], {
            "mode": "container-by-id", "osm_type": "relation",
            "osm_id": REL_UNNAMED, "osm_name": None})
        self.assertEqual(sealed["name"], CONTAINER)
        self.assertIs(sealed["container_by_id"], True)
        self.assertIsNone(sealed["osm_name"])
        # The included route that lies outside the polygon is whole (real geometry).
        east = max(p[0] for line in trails["Udenfor Rute"]["geometry"][
            "coordinates"] for p in line)
        self.assertGreater(east, 9.88)
        self.assertEqual([i["id"] for i in report["quality"][
            "dk_curated_included"]], ["r904", "r906"])
    def test_without_include_only_the_listed_routes_would_not_exist_elsewhere(self):
        # Control for the include-only rule: the same polygon through the plain
        # relation path (named like the registry) keeps the unlisted route.
        result, trails, _, report, _ = self.run_cli(
            "Et Andet Navn", ("relation", REL_OTHER))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sorted(trails), ["Andenrute"])
    def test_other_named_forest_can_be_the_container(self):
        result, trails, _, report, sealed = self.run_cli(
            "Anden Plantage", ("relation", REL_OTHER), self.FLAG,
            listed=include(("r907", "Andenrute")))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sorted(trails), ["Andenrute"])
        self.assertEqual(sealed["name"], "Anden Plantage")
        self.assertEqual(sealed["osm_name"], "Et Andet Navn")
    def test_unnamed_way_can_be_the_container(self):
        result, trails, _, report, sealed = self.run_cli(
            CONTAINER, ("way", self.ids["unnamed_way"]), self.FLAG,
            listed=include(("r908", "Vejcontainerrute")))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sorted(trails), ["Vejcontainerrute"])
        self.assertEqual((sealed["osm_type"], sealed["osm_id"]),
                         ("way", self.ids["unnamed_way"]))
        self.assertEqual(report["boundary_kind"], "way")
    def test_only_a_forest_like_polygon_is_accepted(self):
        result, _, _, report, _ = self.run_cli(
            CONTAINER, ("relation", REL_PARK), self.FLAG, listed=self.LISTED)
        self.assertEqual(result.returncode, 2)
        self.assertIn("not a forest, wood, nature reserve", result.stderr)
        self.assertEqual(report["status"], "failed")
    def test_a_missing_or_wrong_kind_container_fails_closed(self):
        for label, boundary in (("missing", ("relation", 7999)),
                                ("wrong kind", ("way", REL_UNNAMED))):
            with self.subTest(label):
                result, trails, _, report, _ = self.run_cli(
                    CONTAINER, boundary, self.FLAG, listed=self.LISTED)
                self.assertEqual(result.returncode, 2)
                self.assertIn("is not exactly one polygon", result.stderr)
    def test_a_stale_include_still_fails_the_area(self):
        result, *_ = self.run_cli(
            CONTAINER, ("relation", REL_UNNAMED), self.FLAG,
            listed=include(("r904", "Valgt Rute"), ("r999", "Findes Ikke")))
        self.assertEqual(result.returncode, 2)
        self.assertIn("dk-curated-include failures", result.stderr)
    def test_the_container_needs_an_include_list(self):
        result, *_ = self.run_cli(
            CONTAINER, ("relation", REL_UNNAMED), self.FLAG)
        self.assertEqual(result.returncode, 2)
        self.assertIn("requires --dk-curated-include", result.stderr)
    def test_flag_is_limited_to_dk_v3_exact_denmark(self):
        for kwargs in ({"rules": "dk-v2"}, {"region": "us"}):
            with self.subTest(**kwargs):
                result, *_ = self.run_cli(
                    CONTAINER, ("relation", REL_UNNAMED), self.FLAG,
                    listed=self.LISTED, **kwargs)
                self.assertEqual(result.returncode, 2)
                self.assertIn("--dk-container-by-id requires", result.stderr)
if __name__ == "__main__":
    unittest.main()
