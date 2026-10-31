"""dk-v3 whole-route policy, end to end through the real assemble.py CLI.

A synthetic OSM file (one wood area plus signed hiking routes) is assembled with
the actual argument parser for dk-v2 and dk-v3, so every behavior is shown with
its dk-v2 control. No hand-built args, no mocks.
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
AREA = "Testskov"
AREA_RELATION = 500
# Area: lon 9.00-9.10, lat 56.00-56.10 (about 6.2 km x 11 km).
LAT0, LAT1, LON0, LON1 = 56.00, 56.10, 9.00, 9.10


class Osm:
    """Tiny OSM XML builder: ways of explicit lon/lat points, route relations."""

    def __init__(self):
        self.nodes, self.ways, self.relations = [], [], []
        self._node = 1000
        self._way = 2000

    def way(self, points, **tags):
        refs = []
        for lon, lat in points:
            self._node += 1
            self.nodes.append((self._node, lon, lat))
            refs.append(self._node)
        self._way += 1
        self.ways.append((self._way, refs, tags))
        return self._way

    def chain(self, points_list, **tags):
        """Ways that share end nodes: consecutive point lists joined at a node."""
        ids, previous = [], None
        for points in points_list:
            refs = []
            for index, (lon, lat) in enumerate(points):
                if index == 0 and previous is not None:
                    refs.append(previous)
                    continue
                self._node += 1
                self.nodes.append((self._node, lon, lat))
                refs.append(self._node)
            previous = refs[-1]
            self._way += 1
            self.ways.append((self._way, refs, dict(tags)))
            ids.append(self._way)
        return ids

    def route(self, relation_id, name, way_ids, network="lwn"):
        self.relations.append((relation_id, way_ids, {
            "type": "route", "route": "hiking", "network": network,
            "name": name}))

    def xml(self):
        out = ["<?xml version='1.0' encoding='UTF-8'?>", '<osm version="0.6">']
        for node_id, lon, lat in sorted(self.nodes):
            out.append(f'<node id="{node_id}" lat="{lat}" lon="{lon}" version="1"/>')
        for way_id, refs, tags in self.ways:
            out.append(f'<way id="{way_id}" version="1">'
                       + "".join(f'<nd ref="{ref}"/>' for ref in refs)
                       + "".join(f'<tag k="{k}" v="{v}"/>' for k, v in tags.items())
                       + "</way>")
        for relation_id, way_ids, tags in self.relations:
            out.append(f'<relation id="{relation_id}" version="1">'
                       + "".join(f'<member type="way" ref="{w}" role=""/>' for w in way_ids)
                       + "".join(f'<tag k="{k}" v="{v}"/>' for k, v in tags.items())
                       + "</relation>")
        out.append("</osm>")
        return "\n".join(out)


def build_fixture():
    osm = Osm()
    ring = [(LON0, LAT0), (LON1, LAT0), (LON1, LAT1), (LON0, LAT1), (LON0, LAT0)]
    boundary = osm.way(ring)
    osm.relations.append((AREA_RELATION, [boundary], {}))
    # The area relation needs multipolygon tags and an outer role.
    osm.relations[-1] = (AREA_RELATION, [boundary], {
        "type": "multipolygon", "natural": "wood", "name": AREA})
    path = {"highway": "path"}
    # Helrute: 0.09 deg inside, 0.04 deg beyond the east edge -> share 0.69.
    osm.route(901, "Helrute", osm.chain(
        [[(9.01, 56.05), (9.04, 56.05)], [(9.04, 56.05), (9.09, 56.05)],
         [(9.09, 56.05), (9.14, 56.05)]], **path))
    # Lille Hjem: only a 0.3 km corner inside a route that lies far outside.
    osm.route(902, "Lille Hjem", osm.chain(
        [[(9.095, 56.07), (9.15, 56.07)], [(9.15, 56.07), (9.30, 56.07)]],
        **path))
    # Smalt Hul: two pieces about 25 m apart, all inside the area.
    first = osm.way([(9.02, 56.03), (9.03, 56.03)], **path)
    second = osm.way([(9.03035, 56.03), (9.04, 56.03)], **path)
    osm.route(903, "Smalt Hul", [first, second])
    # Bredt Hul: two pieces about 330 m apart, all inside the area.
    first = osm.way([(9.02, 56.04), (9.03, 56.04)], **path)
    second = osm.way([(9.0345, 56.04), (9.045, 56.04)], **path)
    osm.route(904, "Bredt Hul", [first, second])
    # Lang Nationalrute: a regional (rwn) route, mostly inside the area.
    osm.route(905, "Lang Nationalrute", osm.chain(
        [[(9.01, 56.08), (9.06, 56.08)], [(9.06, 56.08), (9.12, 56.08)]],
        **path), network="rwn")
    # Udenfor Sti: entirely outside the area polygon.
    osm.route(906, "Udenfor Sti", [osm.way(
        [(9.20, 56.02), (9.21, 56.02), (9.22, 56.02)], **path)])
    return osm.xml()


class DkV3Case(unittest.TestCase):
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

    def run_cli(self, rules, *extra, tag=None):
        key = (rules, extra)
        if key in self.runs:
            return self.runs[key]
        label = tag or f"{rules}-{len(self.runs)}"
        out = self.dir / f"{label}.trails.geojson"
        report = self.dir / f"{label}.report.json"
        result = subprocess.run(
            [sys.executable, str(HERE / "assemble.py"), "--in", str(self.osm),
             "--out", str(out), "--only-area", AREA, "--require-exact-area",
             "--expected-area-relation-id", str(AREA_RELATION),
             "--min-length-mi", "0.1", "--per-area-merge", "--region", "dk",
             "--dk-rules", rules, "--report-json", str(report), *extra],
            capture_output=True, text=True)
        trails = removed = quality = None
        if out.exists():
            trails = {f["properties"]["name"]: f for f in
                      json.loads(out.read_text())["features"]}
            removed_path = out.with_name(out.name.replace(
                ".trails.geojson", ".removed.geojson"))
            removed = {f["properties"]["name"]: f["properties"] for f in
                       json.loads(removed_path.read_text())["features"]}
        if report.exists():
            quality = json.loads(report.read_text()).get("quality")
        self.runs[key] = (result, trails, removed, quality)
        return self.runs[key]

    @staticmethod
    def east_edge(feature):
        return max(point[0] for line in feature["geometry"]["coordinates"]
                   for point in line)

    @staticmethod
    def west_edge(feature):
        return min(point[0] for line in feature["geometry"]["coordinates"]
                   for point in line)


class WholeRoute(DkV3Case):
    def test_route_mostly_inside_is_published_whole_in_v3_and_clipped_in_v2(self):
        result, trails, _, quality = self.run_cli("dk-v3")
        self.assertEqual(result.returncode, 0, result.stderr)
        route = trails["Helrute"]
        self.assertGreater(self.east_edge(route), 9.139)
        self.assertTrue(route["properties"]["dk_whole_route"])
        self.assertNotIn("clipped", route["properties"])
        self.assertEqual(route["properties"]["connectivity"]["status"], "accepted")
        ledger = {row["name"]: row for row in quality["dk_v3_whole_routes"]}
        self.assertTrue(ledger["Helrute"]["published"])
        self.assertAlmostEqual(ledger["Helrute"]["share"], 0.69, delta=0.02)
        # Control: dk-v2 clips the same route at the polygon edge.
        result2, trails2, _, _ = self.run_cli("dk-v2")
        self.assertEqual(result2.returncode, 0, result2.stderr)
        clipped = trails2["Helrute"]
        self.assertLessEqual(self.east_edge(clipped), 9.1001)
        self.assertTrue(clipped["properties"]["clipped"])
        self.assertNotIn("dk_whole_route", clipped["properties"])

    def test_whole_route_geometry_is_only_real_member_way_coordinates(self):
        _, trails, _, _ = self.run_cli("dk-v3")
        points = {tuple(point) for line in trails["Helrute"]["geometry"]["coordinates"]
                  for point in line}
        expected = {(9.01, 56.05), (9.04, 56.05), (9.09, 56.05), (9.14, 56.05)}
        self.assertEqual(points, expected)

    def test_route_mostly_elsewhere_is_dropped_in_v3_but_left_as_a_sliver_in_v2(self):
        _, trails, removed, quality = self.run_cli("dk-v3")
        self.assertNotIn("Lille Hjem", trails)
        self.assertEqual(removed["Lille Hjem"]["removed_category"],
                         "dk-route-elsewhere")
        self.assertEqual(quality["removed_counts"].get("dk-route-elsewhere"), 1)
        _, trails2, _, _ = self.run_cli("dk-v2")
        self.assertIn("Lille Hjem", trails2)
        self.assertTrue(trails2["Lille Hjem"]["properties"]["clipped"])

    def test_regional_network_route_never_becomes_a_whole_route(self):
        for rules in ("dk-v2", "dk-v3"):
            with self.subTest(rules=rules):
                _, trails, removed, _ = self.run_cli(rules)
                self.assertNotIn("Lang Nationalrute", trails)
                self.assertEqual(removed["Lang Nationalrute"]["removed_category"],
                                 "dk-long-distance")


class BridgedGaps(DkV3Case):
    def test_small_gap_between_member_ways_is_published_in_v3_only(self):
        _, trails, _, quality = self.run_cli("dk-v3")
        feature = trails["Smalt Hul"]
        connectivity = feature["properties"]["connectivity"]
        self.assertEqual(connectivity["status"], "accepted")
        self.assertEqual(connectivity["accepted_reasons"], ["bridged-gap"])
        self.assertEqual(connectivity["source_components"], 2)
        self.assertLess(connectivity["widest_gap_m"], 100)
        # Nothing is drawn across the gap: both pieces keep their own ends.
        ends = sorted(point[0] for line in feature["geometry"]["coordinates"]
                      for point in (line[0], line[-1]))
        self.assertEqual(ends, [9.02, 9.03, 9.03035, 9.04])
        _, trails2, removed2, _ = self.run_cli("dk-v2")
        self.assertNotIn("Smalt Hul", trails2)
        self.assertEqual(removed2["Smalt Hul"]["removed_category"],
                         "unresolved-relation-gap")

    def test_wide_gap_is_still_dropped_in_v3(self):
        for rules in ("dk-v2", "dk-v3"):
            with self.subTest(rules=rules):
                _, trails, removed, _ = self.run_cli(rules)
                self.assertNotIn("Bredt Hul", trails)
                self.assertEqual(removed["Bredt Hul"]["removed_category"],
                                 "unresolved-relation-gap")
        _, _, removed, _ = self.run_cli("dk-v3")
        connectivity = removed["Bredt Hul"]["connectivity"]
        self.assertGreater(connectivity["widest_gap_m"], 100)
        self.assertEqual(connectivity["status"], "rejected")


class CuratedInclude(DkV3Case):
    def include_file(self, entries):
        path = self.dir / f"include-{len(list(self.dir.glob('include-*')))}.json"
        path.write_text(json.dumps(entries), encoding="utf-8")
        return str(path)

    def test_route_outside_the_polygon_is_published_only_when_listed(self):
        _, trails, removed, _ = self.run_cli("dk-v3")
        self.assertNotIn("Udenfor Sti", trails)
        self.assertEqual(removed["Udenfor Sti"]["removed_category"],
                         "outside-exact-area")
        listed = self.include_file([{
            "id": "r906", "name": "Udenfor Sti", "reason": "official guide route"}])
        result, trails, _, quality = self.run_cli(
            "dk-v3", "--dk-curated-include", listed, tag="include-ok")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Udenfor Sti", trails)
        self.assertGreater(self.west_edge(trails["Udenfor Sti"]), 9.19)
        self.assertEqual(quality["dk_curated_included"][0]["id"], "r906")
        self.assertEqual(quality["dk_curated_include_failures"], [])

    def test_stale_or_wrong_name_inclusion_fails_the_area(self):
        for entry in ({"id": "r999", "name": "Findes Ikke", "reason": "x"},
                      {"id": "r906", "name": "Andet Navn", "reason": "x"}):
            with self.subTest(entry=entry["id"], name=entry["name"]):
                result, _, _, quality = self.run_cli(
                    "dk-v3", "--dk-curated-include", self.include_file([entry]),
                    tag=f"include-bad-{entry['name'][:3]}")
                self.assertEqual(result.returncode, 2)
                self.assertIn("dk-curated-include failures", result.stderr)
                self.assertEqual(len(quality["dk_curated_include_failures"]), 1)

    def test_inclusion_does_not_override_a_structural_gap(self):
        listed = self.include_file([{
            "id": "r904", "name": "Bredt Hul", "reason": "x"}])
        result, trails, _, quality = self.run_cli(
            "dk-v3", "--dk-curated-include", listed, tag="include-gap")
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("Bredt Hul", trails)
        self.assertIn("unresolved-relation-gap",
                      quality["dk_curated_include_failures"][0])


class ParserGuards(unittest.TestCase):
    def run_parser(self, payload, *extra):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "include.json"
            path.write_text(payload, encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(HERE / "assemble.py"),
                 "--in", str(Path(tmp) / "missing.pbf"), "--out", "y.geojson",
                 "--region", "dk", "--dk-curated-include", str(path), *extra],
                capture_output=True, text=True)

    GOOD = '[{"id": "r906", "name": "A", "reason": "r"}]'

    def test_include_flag_requires_dk_v3(self):
        for rules in ("legacy", "dk-v2"):
            with self.subTest(rules=rules):
                result = self.run_parser(self.GOOD, "--dk-rules", rules)
                self.assertEqual(result.returncode, 2)
                self.assertIn("requires --dk-rules dk-v3", result.stderr)

    def test_malformed_include_file_is_rejected(self):
        for payload in ("not json", '{"id": 1}', '[{"id": "x"}]',
                        '[{"id": "n:A", "name": "B", "reason": "x"}]'):
            with self.subTest(payload=payload):
                result = self.run_parser(payload, "--dk-rules", "dk-v3")
                self.assertEqual(result.returncode, 2)
                self.assertIn("--dk-curated-include is invalid", result.stderr)

    def test_valid_include_file_passes_the_parser(self):
        result = self.run_parser(self.GOOD, "--dk-rules", "dk-v3")
        self.assertNotIn("is invalid", result.stderr)
        self.assertNotIn("requires --dk-rules", result.stderr)

    def test_dk_v3_is_an_accepted_rule_set_and_unknown_is_not(self):
        result = self.run_parser(self.GOOD, "--dk-rules", "dk-v4")
        self.assertIn("invalid choice", result.stderr)


if __name__ == "__main__":
    unittest.main()
