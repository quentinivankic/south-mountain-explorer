"""dk-v3 route-cluster container (`--dk-container-from-routes`), end to end through the real
assemble.py CLI on a synthetic Danish OSM file. Positive derivation, determinism, every
fail-closed negative, and the controls (no flag, real polygon path). No mocks."""
import hashlib
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
NAME = "Klyngeskov"               # registry name; no OSM polygon carries it
ANCHOR = 1001
def build_fixture():
    osm = Osm()
    # The cluster: two listed hiking routes ~700 m apart, one unlisted hiking route, one
    # bicycle route, and a route 0.3 degrees away (over the size cap).
    osm.route(1001, "Skovrunden", [osm.way(
        [(9.000, 56.050), (9.010, 56.050), (9.020, 56.050)], **PATH)])
    osm.route(1002, "Engstien", [osm.way(
        [(9.000, 56.056), (9.010, 56.058), (9.020, 56.056)], **PATH)])
    osm.route(1003, "Ikke Valgt", [osm.way(
        [(9.005, 56.0508), (9.015, 56.0508)], **PATH)])
    osm.relations.append((1004, [osm.way(
        [(9.005, 56.054), (9.015, 56.054)], **PATH)],
        {"type": "route", "route": "bicycle", "network": "lcn", "name": "Cykelrute"}))
    osm.route(1005, "Langt Vaek", [osm.way(
        [(9.300, 56.050), (9.310, 56.050)], **PATH)])
    # A pair outside Denmark (lat 50): the size is fine, the place is not.
    osm.route(1101, "Syd Et", [osm.way([(9.0, 50.00), (9.01, 50.00)], **PATH)])
    osm.route(1102, "Syd To", [osm.way([(9.0, 50.003), (9.01, 50.003)], **PATH)])
    # A real named polygon holding two routes: this area belongs on the polygon path.
    ring = [(9.50, 56.00), (9.60, 56.00), (9.60, 56.10), (9.50, 56.10), (9.50, 56.00)]
    osm.relations.append((1200, [osm.way(ring)], {
        "type": "multipolygon", "landuse": "forest", "name": "Navngivet Skov"}))
    osm.route(1011, "Skov Et", [osm.way([(9.52, 56.05), (9.55, 56.05), (9.58, 56.05)], **PATH)])
    osm.route(1012, "Skov To", [osm.way([(9.52, 56.07), (9.55, 56.07), (9.58, 56.07)], **PATH)])
    # A hiking route whose member way is not in the file (incomplete geometry).
    osm.relations.append((1020, [99999], {
        "type": "route", "route": "hiking", "network": "lwn", "name": "Hul"}))
    return osm.xml()
def include(*pairs):
    return [{"id": f"r{rid}", "name": name, "reason": "reviewed official walk"}
            for rid, name in pairs]
TWO = include((1001, "Skovrunden"), (1002, "Engstien"))
class RouteClusterCase(unittest.TestCase):
    count = 0

    @classmethod
    def setUpClass(cls):
        if not HAVE_DEPS:
            raise unittest.SkipTest("pyosmium/shapely not installed")
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        cls.osm = cls.dir / "fixture.osm"
        cls.osm.write_text(build_fixture(), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_cli(self, area=NAME, anchor=ANCHOR, listed=TWO, flags=("--dk-container-from-routes",),
                rules="dk-v3", region="dk", way_anchor=None):
        type(self).count += 1
        label = f"run{self.count}"
        out = self.dir / f"{label}.trails.geojson"
        report = self.dir / f"{label}.report.json"
        extra = []
        if listed is not None:
            path = self.dir / f"{label}.include.json"
            path.write_text(json.dumps(listed), encoding="utf-8")
            extra = ["--dk-curated-include", str(path)]
        boundary = (["--expected-area-way-id", str(way_anchor)] if way_anchor
                    else ["--expected-area-relation-id", str(anchor)])
        result = subprocess.run(
            [sys.executable, str(HERE / "assemble.py"), "--in", str(self.osm),
             "--out", str(out), "--only-area", area, "--require-exact-area", *boundary,
             "--min-length-mi", "0.1", "--per-area-merge",
             "--region", region, "--dk-rules", rules,
             "--report-json", str(report), *extra, *flags],
            capture_output=True, text=True)
        trails = removed = sealed = None
        if out.exists():
            trails = {f["properties"]["name"]: f for f in json.loads(out.read_text())["features"]}
            removed = {f["properties"]["name"]: f["properties"] for f in json.loads(
                out.with_name(out.name.replace(".trails.geojson", ".removed.geojson")).read_text()
            )["features"]}
            sealed_path = out.with_name(out.name.replace(".trails.geojson", ".exact-area.geojson"))
            if sealed_path.exists():
                sealed = json.loads(sealed_path.read_text())["features"][0]
        data = json.loads(report.read_text()) if report.exists() else None
        return result, trails, removed, data, sealed

    def assertFailsClosed(self, result, report, text):
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn(text, result.stderr)
        if report is not None:
            self.assertEqual(report["status"], "failed")
class RouteClusterPositive(RouteClusterCase):
    def test_container_is_derived_from_the_listed_routes_and_sealed_as_route_cluster(self):
        result, trails, removed, report, sealed = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sorted(trails), ["Engstien", "Skovrunden"])
        # Nothing is synthesized: the unlisted route is removed, visibly.
        self.assertEqual(removed["Ikke Valgt"]["removed_category"], "dk-container-only")
        props = sealed["properties"]
        self.assertEqual((props["name"], props["osm_type"], props["osm_id"]),
                         (NAME, "relation", ANCHOR))
        self.assertEqual(props["evidence_type"], "route-cluster")
        cluster = props["route_cluster"]
        self.assertEqual(cluster["route_relation_ids"], [1001, 1002])
        self.assertEqual(cluster["anchor_relation_id"], ANCHOR)
        self.assertEqual(cluster["buffer_m"], 150)
        self.assertEqual(cluster["version"], "route-cluster-v1")
        self.assertEqual(cluster["route_way_count"], 2)
        self.assertEqual(len(cluster["route_linework_sha256"]), 64)
        self.assertEqual(report["boundary"], {"name": NAME, "osm_type": "relation", "osm_id": ANCHOR})
        self.assertEqual(report["boundary_match_count"], 0)   # honest: no polygon carries the name
        self.assertEqual(report["boundary_selection"]["mode"], "container-from-routes")
        self.assertEqual(report["boundary_selection"]["route_relation_ids"], [1001, 1002])
        # The hash in the sealed feature is the hash of the sealed geometry.
        digest = hashlib.sha256(json.dumps(
            sealed["geometry"], ensure_ascii=False, sort_keys=True,
            separators=(",", ":")).encode("utf-8")).hexdigest()
        self.assertEqual(props["geometry_sha256"], digest)
        self.assertEqual(report["boundary_geometry_sha256"], digest)
        self.assertEqual([i["id"] for i in report["quality"]["dk_curated_included"]],
                         ["r1001", "r1002"])

    def test_container_holds_every_route_with_the_fixed_buffer(self):
        from shapely.geometry import LineString, shape
        _, trails, _, _, sealed = self.run_cli()
        polygon = shape(sealed["geometry"])
        self.assertTrue(polygon.is_valid)
        for feature in trails.values():
            for line in feature["geometry"]["coordinates"]:
                self.assertTrue(polygon.contains(LineString(line)))
        # 150 m is about 0.00135 degrees of latitude beyond the northernmost route vertex.
        north = polygon.bounds[3] - 56.058
        self.assertAlmostEqual(north, 150 / 111320.0, delta=0.0001)
        south = 56.050 - polygon.bounds[1]
        self.assertAlmostEqual(south, 150 / 111320.0, delta=0.0001)

    def test_derivation_is_deterministic_and_independent_of_include_order(self):
        _, _, _, _, first = self.run_cli()
        _, _, _, _, again = self.run_cli()
        _, _, _, _, reordered = self.run_cli(listed=list(reversed(TWO)))
        self.assertEqual(first["properties"], again["properties"])
        self.assertEqual(first["geometry"], again["geometry"])
        self.assertEqual(first["properties"]["geometry_sha256"],
                         reordered["properties"]["geometry_sha256"])
        self.assertEqual(first["properties"]["route_cluster"],
                         reordered["properties"]["route_cluster"])

    def test_identity_follows_the_route_set(self):
        # A different route set is a different container (the evidence cannot drift silently).
        _, _, _, _, two = self.run_cli()
        _, _, _, _, three = self.run_cli(listed=include(
            (1001, "Skovrunden"), (1002, "Engstien"), (1003, "Ikke Valgt")))
        self.assertNotEqual(two["properties"]["route_cluster"]["route_linework_sha256"],
                            three["properties"]["route_cluster"]["route_linework_sha256"])
        self.assertEqual(three["properties"]["route_cluster"]["route_relation_ids"],
                         [1001, 1002, 1003])
class RouteClusterNegatives(RouteClusterCase):
    def test_control_without_the_flag_finds_no_boundary(self):
        result, *_ = self.run_cli(flags=())
        self.assertEqual(result.returncode, 2)
        self.assertIn("found 0", result.stderr)

    def test_at_least_two_routes(self):
        result, _, _, report, _ = self.run_cli(listed=include((1001, "Skovrunden")))
        self.assertFailsClosed(result, report, "at least 2 distinct include_trails routes")

    def test_duplicate_routes_are_refused(self):
        result, *_ = self.run_cli(listed=include((1001, "Skovrunden"), (1001, "Skovrunden")))
        self.assertEqual(result.returncode, 2)
        self.assertIn("repeats id", result.stderr)

    def test_anchor_must_be_an_included_route(self):
        result, _, _, report, _ = self.run_cli(anchor=1003)
        self.assertFailsClosed(result, report, "anchor relation 1003 is not an included route")

    def test_a_missing_route_fails_closed(self):
        result, _, _, report, _ = self.run_cli(listed=include((1001, "Skovrunden"), (7777, "Findes ikke")))
        self.assertFailsClosed(result, report, "route relation 7777 is not in the extract")

    def test_a_non_hiking_route_fails_closed(self):
        result, _, _, report, _ = self.run_cli(listed=include((1001, "Skovrunden"), (1004, "Cykelrute")))
        self.assertFailsClosed(result, report, "route relation 1004 is route='bicycle'")

    def test_incomplete_geometry_fails_closed(self):
        result, _, _, report, _ = self.run_cli(listed=include((1001, "Skovrunden"), (1020, "Hul")))
        self.assertFailsClosed(result, report, "member way 99999")

    def test_cluster_size_cap(self):
        result, _, _, report, _ = self.run_cli(listed=include((1001, "Skovrunden"), (1005, "Langt Vaek")))
        self.assertFailsClosed(result, report, "over the 0.12 cap per axis")

    def test_cluster_must_be_inside_denmark(self):
        result, _, _, report, _ = self.run_cli(anchor=1101, listed=include((1101, "Syd Et"), (1102, "Syd To")))
        self.assertFailsClosed(result, report, "is outside Denmark")

    def test_a_real_polygon_with_the_name_belongs_on_the_polygon_path(self):
        result, _, _, report, _ = self.run_cli(
            area="Navngivet Skov", anchor=1011, listed=include((1011, "Skov Et"), (1012, "Skov To")))
        self.assertFailsClosed(result, report, "container_from_routes is not needed")
        # Control: the same area on the normal polygon path publishes.
        result, trails, *_ = self.run_cli(area="Navngivet Skov", anchor=1200, listed=None, flags=())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sorted(trails), ["Skov Et", "Skov To"])

    def test_only_relation_includes_and_a_relation_anchor(self):
        result, *_ = self.run_cli(listed=[
            *TWO, {"id": "n:Navn", "name": "Navn", "reason": "x"}])
        self.assertEqual(result.returncode, 2)
        self.assertIn("accepts only r<id> routes", result.stderr)
        result, *_ = self.run_cli(way_anchor=2001)
        self.assertEqual(result.returncode, 2)
        self.assertIn("requires --expected-area-relation-id", result.stderr)

    def test_the_include_list_is_required(self):
        result, *_ = self.run_cli(listed=None)
        self.assertEqual(result.returncode, 2)
        self.assertIn("requires --dk-curated-include", result.stderr)

    def test_limited_to_dk_v3_exact_denmark(self):
        for kwargs in ({"rules": "dk-v2"}, {"rules": "legacy"}, {"region": "us"}):
            with self.subTest(**kwargs):
                result, *_ = self.run_cli(**kwargs)
                self.assertEqual(result.returncode, 2)
                self.assertIn("--dk-container-from-routes requires --dk-rules dk-v3", result.stderr)

    def test_exclusive_with_the_other_boundary_opt_ins(self):
        for other in ("--dk-boundary-by-id", "--dk-container-by-id"):
            with self.subTest(other):
                result, *_ = self.run_cli(flags=("--dk-container-from-routes", other))
                self.assertEqual(result.returncode, 2)
                self.assertIn("mutually exclusive", result.stderr)
class RouteClusterRounding(unittest.TestCase):
    """Two routes ~300 m apart make the 150 m buffers nearly tangent; rounding the ring to
    6 decimals then self-intersects (found by search; the unrounded buffer is valid). The
    derivation must repair that deterministically and still cover both routes."""
    LINES = (((9.00049257335851, 56.0), (9.01049257335851, 55.999989385620346)),
             ((9.00123800213736, 56.002676729069535), (9.01049257335851, 56.00284453163715)))

    def derive(self):
        if not HAVE_DEPS:
            self.skipTest("pyosmium/shapely not installed")
        import areas
        nodes, ways, relations = {}, {}, {}
        for index, line in enumerate(self.LINES, start=1):
            ids = [index * 10, index * 10 + 1]
            nodes.update(dict(zip(ids, line)))
            ways[index] = {"tags": {}, "nodes": ids}
            relations[100 + index] = {"tags": {"type": "route", "route": "hiking"},
                                      "members": [("w", index, "")]}
        return areas.route_cluster_container(relations, ways, nodes, [101, 102], 101, "Tangent")

    def test_rounding_pinch_is_repaired_and_still_covers_the_routes(self):
        from shapely.geometry import LineString
        area, failure = self.derive()
        self.assertIsNone(failure)
        self.assertTrue(area["geom"].is_valid)
        for line in self.LINES:
            self.assertTrue(area["geom"].covers(LineString(line)))
        again, _ = self.derive()
        self.assertEqual(area["geom"].wkt, again["geom"].wkt)
        self.assertEqual(area["route_cluster"], again["route_cluster"])
if __name__ == "__main__":
    unittest.main()
