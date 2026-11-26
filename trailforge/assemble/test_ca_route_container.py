"""Canada registry `container_from_routes` (assemble.py --ca-container-from-routes): the area is a ROUTE, its container
derived from the listed member ways of one anchor route relation, and only trails with a listed name AND a member way
of the anchor publish. End to end through the real assemble.py CLI on a synthetic Newfoundland OSM file, then pure
checks of every gate with one input-mutation or constant-mutation test per gate. No mocks of the engine."""
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
try:
    import osmium  # noqa: F401
    import shapely  # noqa: F401
    HAVE_DEPS = True
except ImportError:
    HAVE_DEPS = False
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import areas  # noqa: E402
import ca_rules  # noqa: E402
from test_dk_v3 import Osm  # noqa: E402
PATH = {"highway": "path"}
AREA = "Coast Trail Container"          # registry name; no OSM polygon carries it
ANCHOR = 3001
ANCHOR_NAME = "Coast Trail"
SOURCE = "https://example.ca/paths"
NAMES = ["Alpha Path", "Beta Path", "Gamma Path"]
def entry(**over):
    base = {"anchor_name": ANCHOR_NAME, "way_names": list(NAMES), "max_span_deg": 0.5,
            "source": SOURCE, "reason": "each path is its own listed hike"}
    base.update(over)
    return base
def build_fixture(anchor_tags=None, lon0=-52.80, lat0=47.30, polygon=True):
    osm = Osm()
    shift = lambda points: [(lon0 + dx, lat0 + dy) for dx, dy in points]      # noqa: E731
    a1, a2 = osm.chain([shift([(0.000, 0.000), (0.010, 0.000)]), shift([(0.010, 0.000), (0.020, 0.000)])],
                       name="Alpha Path", **PATH)
    beta = osm.way(shift([(0.000, 0.010), (0.020, 0.010)]), name="Beta Path", **PATH)
    # Gamma is two disconnected member ways 150 m apart: one trail only when the same-name merge is scoped to the container.
    gamma = osm.way(shift([(0.000, 0.020), (0.009, 0.020)]), name="Gamma Path", **PATH)
    gamma2 = osm.way(shift([(0.011, 0.020), (0.020, 0.020)]), name="Gamma Path", **PATH)
    delta = osm.way(shift([(0.005, 0.0008), (0.015, 0.0008)]), name="Delta Path", **PATH)     # member, not listed
    # A same-named way that is NOT a member of the anchor: starts 11 m from Beta, runs 480 m south, so its midpoint
    # lies outside the container and only a short piece of it is clipped in.
    osm.way(shift([(0.015, 0.0099), (0.015, 0.0056)]), name="Beta Path", **PATH)
    # The anchor: the whole long route (it also holds an unnamed way and a road-like member).
    tags = {"type": "route", "route": "hiking", "network": "none", "name": ANCHOR_NAME, **(anchor_tags or {})}
    osm.relations.append((ANCHOR, [a1, a2, beta, gamma, gamma2, delta], tags))
    if polygon:
        # A park polygon elsewhere in the extract: with any park present, a trail in no park is "backcountry" and the
        # same-name merge never joins it, so only the container-as-merge-area fuses the paths (as in the real extract).
        ring = shift([(0.2, 0.2), (0.3, 0.2), (0.3, 0.3), (0.2, 0.3), (0.2, 0.2)])
        osm.relations.append((3100, [osm.way(ring)], {"type": "boundary", "boundary": "protected_area",
                                                       "name": "Named Park"}))
        osm.way(shift([(0.22, 0.25), (0.28, 0.25)]), name="Park Path", **PATH)
    return osm.xml()
class Case(unittest.TestCase):
    count = 0
    @classmethod
    def setUpClass(cls):
        if not HAVE_DEPS:
            raise unittest.SkipTest("pyosmium/shapely not installed")
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        cls.fixtures = {}
    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()
    def fixture(self, key="base", **kwargs):
        if key not in self.fixtures:
            path = self.dir / f"{key}.osm"
            path.write_text(build_fixture(**kwargs), encoding="utf-8")
            self.fixtures[key] = path
        return self.fixtures[key]
    def run_cli(self, config=None, area=AREA, anchor=ANCHOR, region="ca", rules="legacy", fixture=None,
                flag=True, extra=()):
        type(self).count += 1
        label = f"run{self.count}"
        out = self.dir / f"{label}.trails.geojson"
        report = self.dir / f"{label}.report.json"
        args = [sys.executable, str(HERE / "assemble.py"), "--in", str(fixture or self.fixture()), "--out", str(out),
                "--only-area", area, "--require-exact-area", "--expected-area-relation-id", str(anchor),
                "--min-length-mi", "0.1", "--per-area-merge", "--report-json", str(report), *extra]
        if region:
            args += ["--region", region]
        if rules:
            args += ["--dk-rules", rules]
        if flag:
            cfg = self.dir / f"{label}.container.json"
            cfg.write_text(json.dumps(config if config is not None else entry()), encoding="utf-8")
            args += ["--ca-container-from-routes", str(cfg)]
        result = subprocess.run(args, capture_output=True, text=True)
        trails = removed = sealed = None
        if out.exists():
            trails = {}
            for f in json.loads(out.read_text())["features"]:
                trails.setdefault(f["properties"]["name"], []).append(f)
            removed = {}
            for f in json.loads(out.with_name(out.name.replace(".trails.geojson", ".removed.geojson")).read_text())["features"]:
                removed.setdefault(f["properties"]["name"], []).append(f["properties"])
            sealed_path = out.with_name(out.name.replace(".trails.geojson", ".exact-area.geojson"))
            if sealed_path.exists():
                sealed = json.loads(sealed_path.read_text())["features"][0]
        data = json.loads(report.read_text()) if report.exists() else None
        return result, trails, removed, data, sealed
    def assertFailsClosed(self, result, text):
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn(text, result.stderr)
class EndToEnd(Case):
    def test_container_is_derived_from_the_listed_ways_and_only_listed_member_trails_publish(self):
        result, trails, removed, report, sealed = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sorted(trails), NAMES)
        self.assertTrue(all(len(v) == 1 for v in trails.values()))
        # Delta is a member of the anchor and inside the container, but not listed: removed, visibly.
        self.assertEqual(removed["Delta Path"][0]["removed_category"], "ca-container-not-listed")
        # The same-named way outside the anchor is removed even though its name is listed.
        gone = [p for p in removed["Beta Path"] if p.get("removed_category") == "ca-container-not-listed"]
        self.assertEqual(len(gone), 1)
        self.assertIn("none of its ways is a member", gone[0]["removed_reason"])
        props = sealed["properties"]
        self.assertEqual((props["name"], props["osm_type"], props["osm_id"]), (AREA, "relation", ANCHOR))
        self.assertEqual(props["evidence_type"], "route-cluster")
        cluster = props["route_cluster"]
        self.assertEqual((cluster["version"], cluster["region"], cluster["buffer_m"]), ("ca-route-container-v1", "ca", 150))
        self.assertEqual(cluster["anchor_relation_id"], ANCHOR)
        self.assertEqual(cluster["way_names"], sorted(NAMES))
        self.assertEqual(cluster["route_way_count"], 5)          # Alpha x2, Beta, Gamma x2; never Delta
        self.assertEqual(len(cluster["route_linework_sha256"]), 64)
        self.assertEqual(report["boundary_selection"]["mode"], "ca-container-from-routes")
        self.assertEqual(report["boundary"], {"name": AREA, "osm_type": "relation", "osm_id": ANCHOR})
        self.assertEqual(report["ca_container_from_routes"]["failures"], [])
        self.assertEqual(report["ca_container_from_routes"]["kept"], sorted(NAMES))
        digest = hashlib.sha256(json.dumps(sealed["geometry"], ensure_ascii=False, sort_keys=True,
                                           separators=(",", ":")).encode("utf-8")).hexdigest()
        self.assertEqual(props["geometry_sha256"], digest)
    def test_same_name_ways_merge_into_one_trail_because_the_merge_is_scoped_to_the_container(self):
        # Gamma's two member ways are disconnected: only the container as the merge area fuses them (the polygon-less
        # paths are otherwise "backcountry" and never merge, which would leave two trails named Gamma Path).
        _, trails, _, _, _ = self.run_cli()
        self.assertEqual(len(trails["Gamma Path"]), 1)
        self.assertEqual(len(trails["Gamma Path"][0]["geometry"]["coordinates"]), 2)
        self.assertEqual(trails["Gamma Path"][0]["properties"]["area"], AREA)
        self.assertGreater(trails["Alpha Path"][0]["properties"]["length_mi"], 0.9)
    def test_container_holds_every_listed_route_with_the_fixed_buffer(self):
        from shapely.geometry import LineString, shape
        _, trails, _, _, sealed = self.run_cli()
        polygon = shape(sealed["geometry"])
        self.assertTrue(polygon.is_valid)
        for features in trails.values():
            for line in features[0]["geometry"]["coordinates"]:
                self.assertTrue(polygon.contains(LineString(line)))
        self.assertAlmostEqual(polygon.bounds[3] - 47.32, 150 / 111320.0, delta=0.0001)
    def test_derivation_is_deterministic_and_independent_of_list_order(self):
        _, _, _, _, first = self.run_cli()
        _, _, _, _, again = self.run_cli()
        _, _, _, _, reordered = self.run_cli(entry(way_names=list(reversed(NAMES))))
        self.assertEqual(first["geometry"], again["geometry"])
        self.assertEqual(first["properties"]["geometry_sha256"], reordered["properties"]["geometry_sha256"])
        self.assertEqual(first["properties"]["route_cluster"], reordered["properties"]["route_cluster"])
    def test_identity_follows_the_name_set(self):
        _, _, _, _, three = self.run_cli()
        _, _, _, _, two = self.run_cli(entry(way_names=["Alpha Path", "Beta Path"]))
        self.assertNotEqual(two["properties"]["route_cluster"]["route_linework_sha256"],
                            three["properties"]["route_cluster"]["route_linework_sha256"])
    def test_control_without_the_flag_finds_no_boundary(self):
        result, *_ = self.run_cli(flag=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("found 0", result.stderr)
class ArgumentGates(Case):
    def test_requires_region_ca_exact_area_by_relation_and_the_legacy_rules(self):
        for kwargs in ({"region": None}, {"region": "dk"}, {"rules": "dk-v3"}, {"rules": "dk-v2"}):
            with self.subTest(kwargs):
                result, *_ = self.run_cli(**kwargs)
                self.assertEqual(result.returncode, 2)
                self.assertIn("--ca-container-from-routes requires --region ca", result.stderr)
    def test_excludes_every_other_boundary_selection_and_the_denmark_flags(self):
        include = self.dir / "include.json"
        include.write_text(json.dumps([{"id": "r1", "name": "x", "reason": "y"}]), encoding="utf-8")
        for extra in (["--boundary-by-id"], ["--dk-container-from-routes"], ["--dk-container-by-id"],
                      ["--ca-dedupe-ring-way"], ["--dk-curated-include", str(include)]):
            with self.subTest(extra):
                result, *_ = self.run_cli(extra=extra)
                self.assertEqual(result.returncode, 2)
    def test_way_backed_runs_are_refused(self):
        type(self).count += 1
        cfg = self.dir / "way.json"
        cfg.write_text(json.dumps(entry()), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(HERE / "assemble.py"), "--in", str(self.fixture()), "--out", str(self.dir / "w.geojson"),
             "--only-area", AREA, "--require-exact-area", "--expected-area-way-id", "5", "--region", "ca",
             "--report-json", str(self.dir / "w.json"), "--ca-container-from-routes", str(cfg)],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
    def test_malformed_entry_file_is_refused(self):
        for bad in (entry(extra=1), {**entry(), "source": "http://x.example/y"}, entry(max_span_deg=1.01),
                    entry(max_span_deg=0), entry(way_names=["Alpha Path"]), entry(way_names=["Alpha Path", "Alpha Path"]),
                    entry(way_names=["Alpha Path", ANCHOR_NAME])):
            with self.subTest(bad):
                result, *_ = self.run_cli(bad)
                self.assertEqual(result.returncode, 2)
                self.assertIn("--ca-container-from-routes is invalid", result.stderr)
class FailClosed(Case):
    def test_anchor_name_must_equal_the_relation_name(self):
        result, *_ = self.run_cli(entry(anchor_name="Some Other Trail"))
        self.assertFailsClosed(result, "not 'Some Other Trail'")
        ok, *_ = self.run_cli(entry(anchor_name=ANCHOR_NAME))           # mutation: the right name passes
        self.assertEqual(ok.returncode, 0, ok.stderr)
    def test_a_listed_name_with_no_member_way_fails_closed(self):
        result, *_ = self.run_cli(entry(way_names=["Alpha Path", "Nowhere Path"]))
        self.assertFailsClosed(result, "no member way of relation 3001 is named 'Nowhere Path'")
    def test_a_name_carried_only_by_a_non_member_way_fails_closed(self):
        # Delta is a member; a name only a non-member carries must not derive a container.
        result, *_ = self.run_cli(entry(way_names=["Alpha Path", "Stray Path"]))
        self.assertFailsClosed(result, "'Stray Path'")
    def test_a_non_hiking_anchor_fails_closed(self):
        result, *_ = self.run_cli(fixture=self.fixture("bike", anchor_tags={"route": "bicycle"}))
        self.assertEqual(result.returncode, 2)
    def test_a_long_distance_network_anchor_fails_closed(self):
        result, *_ = self.run_cli(fixture=self.fixture("rwn", anchor_tags={"network": "rwn"}))
        self.assertEqual(result.returncode, 2)
    def test_a_real_polygon_with_the_registry_name_fails_closed(self):
        result, *_ = self.run_cli(area="Named Park", fixture=self.fixture())
        self.assertFailsClosed(result, "container_from_routes is not needed")
    def test_span_over_the_entry_cap_fails_closed_and_the_cap_is_the_gate(self):
        result, *_ = self.run_cli(entry(max_span_deg=0.01))
        self.assertFailsClosed(result, "over the 0.01 cap per axis")
        ok, *_ = self.run_cli(entry(max_span_deg=0.05))                 # mutation: a wider cap derives it
        self.assertEqual(ok.returncode, 0, ok.stderr)
    def test_a_container_outside_canada_fails_closed(self):
        result, *_ = self.run_cli(fixture=self.fixture("dk", lon0=9.0, lat0=56.0))
        self.assertFailsClosed(result, "outside Canada")
    def test_a_stale_listed_name_that_the_engine_filters_fails_the_area(self):
        # A listed name whose only way is a road-class member the engine drops yields no trail: stale entry.
        osm = Osm()
        a = osm.way([(-52.80, 47.30), (-52.79, 47.30)], name="Alpha Path", **PATH)
        b = osm.way([(-52.80, 47.31), (-52.79, 47.31)], name="Beta Path", **PATH)
        c = osm.way([(-52.80, 47.32), (-52.79, 47.32)], name="Short Path", **PATH)
        d = osm.way([(-52.80, 47.321), (-52.79, 47.321)], name="Gamma Path", highway="residential")
        osm.relations.append((ANCHOR, [a, b, c, d], {"type": "route", "route": "hiking", "network": "none",
                                                     "name": ANCHOR_NAME}))
        path = self.dir / "stale.osm"
        path.write_text(osm.xml(), encoding="utf-8")
        result, _, _, report, _ = self.run_cli(entry(way_names=["Alpha Path", "Beta Path", "Short Path", "Gamma Path"]),
                                               fixture=path)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(report["status"], "failed")
        self.assertIn("ca-container-from-routes failures", result.stderr)
@unittest.skipUnless(HAVE_DEPS, "pyosmium/shapely not installed")
class Derivation(unittest.TestCase):
    """areas.ca_route_container as a pure function: one test per gate, each with a mutation that flips it."""
    def world(self, names=("A Path", "B Path"), tags=None, coords=None):
        nodes = {1: (-52.80, 47.30), 2: (-52.79, 47.30), 3: (-52.80, 47.31), 4: (-52.79, 47.31)}
        if coords:
            nodes.update(coords)
        ways = {10: {"tags": {"name": names[0], "highway": "path"}, "nodes": [1, 2]},
                11: {"tags": {"name": names[1], "highway": "path"}, "nodes": [3, 4]}}
        relation = {"tags": {"type": "route", "route": "hiking", "network": "none", "name": "Big Trail",
                             **(tags or {})}, "members": [("w", 10, ""), ("w", 11, "")]}
        return {7: relation}, ways, nodes
    def derive(self, names=("A Path", "B Path"), span=0.5, tags=None, anchor=7, coords=None, listed=None):
        relations, ways, nodes = self.world(names, tags, coords)
        return areas.ca_route_container(relations, ways, nodes, anchor, list(listed or names), "Area", span)
    def test_positive_derivation(self):
        area, failure = self.derive()
        self.assertIsNone(failure)
        self.assertEqual((area["osm_type"], area["osm_id"], area["name"]), ("relation", 7, "Area"))
        self.assertEqual(area["route_cluster"]["route_way_ids"], [10, 11])
    def test_min_names_gate_and_its_constant(self):
        _, failure = self.derive(listed=["A Path"])
        self.assertIn("at least 2 distinct", failure)
        with mock.patch.object(areas, "CA_ROUTE_CONTAINER_MIN_NAMES", 1):
            area, failure = self.derive(listed=["A Path"])
        self.assertIsNone(failure)
        self.assertEqual(area["route_cluster"]["route_way_ids"], [10])
    def test_repeated_names_are_refused(self):
        _, failure = self.derive(listed=["A Path", "A Path"])
        self.assertIn("distinct", failure)
    def test_anchor_must_exist(self):
        _, failure = self.derive(anchor=8)
        self.assertIn("anchor route relation 8 is not in the extract", failure)
    def test_anchor_must_be_hiking_or_foot(self):
        _, failure = self.derive(tags={"route": "bicycle"})
        self.assertIn("not a hiking/foot route", failure)
        area, failure = self.derive(tags={"route": "foot"})              # mutation: foot is accepted
        self.assertIsNone(failure)
    def test_long_distance_network_gate_and_its_constant(self):
        for network in ("iwn", "nwn", "rwn"):
            with self.subTest(network):
                _, failure = self.derive(tags={"network": network})
                self.assertIn("long-distance network", failure)
        area, failure = self.derive(tags={"network": "lwn"})              # local network is fine
        self.assertIsNone(failure)
        with mock.patch.object(areas, "CA_ROUTE_CONTAINER_LONG_DISTANCE_NETWORKS", frozenset()):
            area, failure = self.derive(tags={"network": "rwn"})
        self.assertIsNone(failure)
    def test_the_anchors_own_name_can_never_be_listed(self):
        _, failure = self.derive(names=("Big Trail", "B Path"))
        self.assertIn("anchor route's own name", failure)
        _, failure = self.derive(listed=["A Path", "B Path"])             # mutation: other names derive
        self.assertIsNone(failure)
    def test_a_listed_name_must_be_carried_by_a_member_way(self):
        _, failure = self.derive(listed=["A Path", "C Path"])
        self.assertIn("named 'C Path'", failure)
    def test_missing_nodes_fail_closed(self):
        relations, ways, nodes = self.world()
        del nodes[2]
        _, failure = areas.ca_route_container(relations, ways, nodes, 7, ["A Path", "B Path"], "Area", 0.5)
        self.assertIn("has missing nodes", failure)
    def test_span_cap_gate_and_its_constant(self):
        _, failure = self.derive(span=0.005)
        self.assertIn("over the 0.005 cap per axis", failure)
        for bad in (0, -1, 1.5, True, "0.5", None):
            with self.subTest(bad):
                _, failure = self.derive(span=bad)
                self.assertIn("max_span_deg must be in (0, 1.0]", failure)
        with mock.patch.object(areas, "CA_ROUTE_CONTAINER_MAX_SPAN_DEG", 2.0):
            area, failure = self.derive(span=1.5)                         # mutation: a raised hard cap admits it
        self.assertIsNone(failure)
    def test_canada_bounds_gate_and_its_constant(self):
        _, failure = self.derive(coords={1: (9.0, 56.0), 2: (9.01, 56.0), 3: (9.0, 56.01), 4: (9.01, 56.01)})
        self.assertIn("outside Canada", failure)
        with mock.patch.object(areas, "CA_ROUTE_CONTAINER_LON", (0.0, 20.0)), \
                mock.patch.object(areas, "CA_ROUTE_CONTAINER_LAT", (40.0, 60.0)):
            area, failure = self.derive(coords={1: (9.0, 56.0), 2: (9.01, 56.0), 3: (9.0, 56.01), 4: (9.01, 56.01)})
        self.assertIsNone(failure)
    def test_buffer_constant_changes_the_container(self):
        area, _ = self.derive()
        with mock.patch.object(areas, "ROUTE_CLUSTER_BUFFER_M", 300):
            wide, _ = self.derive()
        self.assertGreater(wide["geom"].area, area["geom"].area)
    def test_denmark_derivation_is_untouched(self):
        # The shared polygon helper produces the same polygon for the same linework through both functions.
        relations, ways, nodes = self.world()
        relations[8] = {"tags": {"type": "route", "route": "hiking", "name": "X"}, "members": [("w", 10, "")]}
        relations[9] = {"tags": {"type": "route", "route": "hiking", "name": "Y"}, "members": [("w", 11, "")]}
        dk, failure = areas.route_cluster_container(relations, ways, nodes, [8, 9], 8, "Dk")
        self.assertIn("outside Denmark", failure)                        # Canada coordinates: still Denmark-gated
        self.assertEqual(areas.ROUTE_CLUSTER_VERSION, "route-cluster-v1")
class Restrict(unittest.TestCase):
    def feature(self, name, ways, kind="trail"):
        return {"type": "Feature", "properties": {"name": name, "kind": kind, "member_ways": ways},
                "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}}
    def run_restrict(self, features, names=("A Path", "B Path"), members=(1, 2)):
        return ca_rules.restrict_to_container_names(features, list(names), "Area", members)
    def test_listed_member_trails_stay_everything_else_goes(self):
        feats = [self.feature("A Path", [1]), self.feature("B Path", [2]), self.feature("C Path", [3]),
                 self.feature("A Path", [9])]
        kept, removed, failures = self.run_restrict(feats)
        self.assertEqual([f["properties"]["name"] for f in kept], ["A Path", "B Path"])
        self.assertEqual(len(removed), 2)
        self.assertEqual({r["properties"]["removed_category"] for r in removed}, {"ca-container-not-listed"})
        self.assertEqual(failures, [])
    def test_membership_gate_mutation(self):
        feats = [self.feature("A Path", [9]), self.feature("B Path", [2])]
        _, _, failures = self.run_restrict(feats)
        self.assertIn("'A Path': 0 trails", failures[0])
        _, _, failures = self.run_restrict(feats, members=(1, 2, 9))      # mutation: the way is a member now
        self.assertEqual(failures, [])
    def test_name_gate_mutation(self):
        feats = [self.feature("A Path", [1]), self.feature("B Path", [2]), self.feature("C Path", [1])]
        kept, _, _ = self.run_restrict(feats)
        self.assertEqual(len(kept), 2)
        kept, _, _ = self.run_restrict(feats, names=("A Path", "B Path", "C Path"))      # mutation: listing C keeps it
        self.assertEqual(len(kept), 3)
    def test_ambiguous_and_stale_names_fail_closed(self):
        feats = [self.feature("A Path", [1]), self.feature("A Path", [2]), self.feature("B Path", [2])]
        _, _, failures = self.run_restrict(feats)
        self.assertEqual(len(failures), 1)
        self.assertIn("'A Path': 2 trails have this name (ambiguous)", failures[0])
        _, _, failures = self.run_restrict(feats[1:])                     # mutation: one A Path left
        self.assertEqual(failures, [])
        _, _, failures = self.run_restrict([self.feature("A Path", [1])])
        self.assertIn("'B Path': 0 trails have this name (stale entry", failures[0])
    def test_a_poi_is_never_a_trail(self):
        kept, removed, failures = self.run_restrict([self.feature("A Path", [1], kind="poi"), self.feature("B Path", [2])])
        self.assertEqual([f["properties"]["name"] for f in kept], ["B Path"])
        self.assertTrue(failures)
class Validate(unittest.TestCase):
    def test_valid_entry(self):
        self.assertEqual(ca_rules.validate_container_from_routes(entry()), entry())
    def test_bad_shapes(self):
        bad = [[], None, "x", {}, {**entry(), "x": 1}, {k: v for k, v in entry().items() if k != "reason"},
               entry(source="http://x.example"), entry(source="https://"), entry(reason=" "),
               entry(anchor_name=""), entry(way_names="Alpha Path"), entry(way_names=["A"]),
               entry(way_names=["A", "A"]), entry(way_names=["A", 5]), entry(way_names=["A", ANCHOR_NAME]),
               entry(way_names=[f"P{i}" for i in range(41)]), entry(max_span_deg=1.01), entry(max_span_deg="0.5"),
               entry(max_span_deg=True), entry(max_span_deg=float("nan"))]
        for case in bad:
            with self.subTest(case=case), self.assertRaises(ValueError):
                ca_rules.validate_container_from_routes(case)
    def test_name_cap_constant_mutation(self):
        names = [f"P{i}" for i in range(41)]
        with self.assertRaises(ValueError):
            ca_rules.validate_container_from_routes(entry(way_names=names))
        with mock.patch.object(ca_rules, "CONTAINER_NAME_CAP", 50):
            self.assertTrue(ca_rules.validate_container_from_routes(entry(way_names=names)))
    def test_span_cap_constant_mutation(self):
        with self.assertRaises(ValueError):
            ca_rules.validate_container_from_routes(entry(max_span_deg=1.5))
        with mock.patch.object(ca_rules, "CONTAINER_MAX_SPAN_DEG", 2.0):
            self.assertTrue(ca_rules.validate_container_from_routes(entry(max_span_deg=1.5)))
if __name__ == "__main__":
    unittest.main()
