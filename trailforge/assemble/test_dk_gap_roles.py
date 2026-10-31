"""dk-v3 accept_gap (per-relation wide-gap acceptance) and accept_directional_roles
(forward/backward members of route=hiking relations), end to end through the real
assemble.py CLI on a synthetic OSM file. Every opt-in has its no-opt-in control,
fail-closed negatives and an unrelated-route control."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_dk_v3 import (AREA, AREA_RELATION, HAVE_DEPS, HERE, LAT0, LAT1, LON0,
                        LON1, Osm)

PATH = {"highway": "path"}


class RoleOsm(Osm):
    """Osm whose relation members can carry a role (way id -> role)."""

    def __init__(self):
        super().__init__()
        self.roles = {}

    def role_route(self, relation_id, name, way_ids, roles, route="hiking"):
        self.roles.update(roles)
        self.relations.append((relation_id, way_ids, {
            "type": "route", "route": route, "network": "lwn", "name": name}))

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
                       + "".join(f'<member type="way" ref="{w}" '
                                 f'role="{self.roles.get(w, "")}"/>' for w in way_ids)
                       + "".join(f'<tag k="{k}" v="{v}"/>' for k, v in tags.items())
                       + "</relation>")
        out.append("</osm>")
        return "\n".join(out)


def build_fixture():
    osm = RoleOsm()
    ring = [(LON0, LAT0), (LON1, LAT0), (LON1, LAT1), (LON0, LAT1), (LON0, LAT0)]
    boundary = osm.way(ring)
    osm.relations.append((AREA_RELATION, [boundary], {
        "type": "multipolygon", "natural": "wood", "name": AREA}))
    # Untouched control: one connected route, no gap, plain roles.
    osm.route(901, "Helrute", osm.chain(
        [[(9.01, 56.05), (9.04, 56.05)], [(9.04, 56.05), (9.09, 56.05)]], **PATH))
    # 25 m gap: already published by the 100 m default.
    first = osm.way([(9.02, 56.03), (9.03, 56.03)], **PATH)
    second = osm.way([(9.03035, 56.03), (9.04, 56.03)], **PATH)
    osm.route(903, "Smalt Hul", [first, second])
    # ~280 m gap.
    first = osm.way([(9.02, 56.04), (9.03, 56.04)], **PATH)
    second = osm.way([(9.0345, 56.04), (9.045, 56.04)], **PATH)
    osm.route(904, "Bredt Hul", [first, second])
    # ~700 m gap.
    first = osm.way([(9.02, 56.06), (9.03, 56.06)], **PATH)
    second = osm.way([(9.04125, 56.06), (9.05, 56.06)], **PATH)
    osm.route(907, "Meget Bredt Hul", [first, second])
    # ~1400 m gap: beyond the hard cap, never acceptable.
    first = osm.way([(9.02, 56.07), (9.03, 56.07)], **PATH)
    second = osm.way([(9.0525, 56.07), (9.06, 56.07)], **PATH)
    osm.route(909, "Ekstra Bredt Hul", [first, second])
    # Direction roles: one connected hiking route, 3 ways (A plain, B forward,
    # C backward). 0.02 deg lon each.
    a, b, c = osm.chain([[(9.02, 56.08), (9.04, 56.08)],
                         [(9.04, 56.08), (9.06, 56.08)],
                         [(9.06, 56.08), (9.08, 56.08)]], **PATH)
    osm.role_route(908, "Retningsrute", [a, b, c], {b: "forward", c: "backward"})
    # Same roles on a route=foot relation: stays rejected even when opted in.
    a, b = osm.chain([[(9.02, 56.09), (9.04, 56.09)],
                      [(9.04, 56.09), (9.06, 56.09)]], **PATH)
    osm.role_route(910, "Fodrute", [a, b], {b: "forward"}, route="foot")
    # An alternative role stays rejected even when opted in.
    a, b = osm.chain([[(9.02, 56.095), (9.04, 56.095)],
                      [(9.04, 56.095), (9.06, 56.095)]], **PATH)
    osm.role_route(911, "Variantrute", [a, b], {b: "alternative"})
    return osm.xml()


class GapRolesCase(unittest.TestCase):
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
        path.write_text(json.dumps(payload), encoding="utf-8")
        return str(path)

    def run_cli(self, *extra, rules="dk-v3"):
        key = (rules, extra)
        if key in self.runs:
            return self.runs[key]
        label = f"run-{len(self.runs)}"
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
        self.runs[key] = (result, trails, removed, quality, out)
        return self.runs[key]


def gap_entry(relation, limit, reason="reviewed: real route, source gap in OSM"):
    return {"relation": relation, "max_gap_m": limit, "reason": reason}


class AcceptGap(GapRolesCase):
    def test_default_still_drops_a_wide_gap_route(self):
        # Control: no opt-in, byte-for-byte the existing behavior.
        _, trails, removed, _, _ = self.run_cli()
        for name in ("Bredt Hul", "Meget Bredt Hul", "Ekstra Bredt Hul"):
            with self.subTest(name=name):
                self.assertNotIn(name, trails)
                self.assertEqual(removed[name]["removed_category"],
                                 "unresolved-relation-gap")
        self.assertIn("Smalt Hul", trails)

    def test_accepted_gap_publishes_connected_parts_without_synthesis(self):
        listed = self.json_file([gap_entry("r904", 400)])
        result, trails, _, quality, _ = self.run_cli("--dk-accept-gap", listed)
        self.assertEqual(result.returncode, 0, result.stderr)
        feature = trails["Bredt Hul"]
        connectivity = feature["properties"]["connectivity"]
        self.assertEqual(connectivity["status"], "accepted")
        self.assertEqual(connectivity["accepted_reasons"], ["accepted-gap"])
        self.assertEqual(connectivity["source_components"], 2)
        self.assertGreater(connectivity["widest_gap_m"], 100)
        self.assertLessEqual(connectivity["widest_gap_m"], 400)
        self.assertEqual(connectivity["bridge_limit_m"], 400)
        # Two separate parts; nothing is drawn across the gap.
        self.assertEqual(feature["geometry"]["type"], "MultiLineString")
        lines = feature["geometry"]["coordinates"]
        self.assertEqual(len(lines), 2)
        self.assertEqual(sorted(point for line in lines for point in
                                (line[0][0], line[-1][0])),
                         [9.02, 9.03, 9.0345, 9.045])
        # Miles are the sum of the real parts (no gap length).
        self.assertGreater(feature["properties"]["length_mi"], 0.7)
        self.assertEqual(feature["properties"]["dk_accept_gap"]["relation"], "r904")
        row = quality["dk_accept_gap"][0]
        self.assertEqual((row["relation"], row["parts"], row["max_gap_m"]),
                         ("r904", 2, 400.0))
        self.assertEqual(quality["dk_accept_gap_failures"], [])

    def test_miles_are_the_sum_of_the_parts(self):
        listed = self.json_file([gap_entry("r904", 400)])
        _, trails, _, _, _ = self.run_cli("--dk-accept-gap", listed)
        # 0.01 deg + 0.0105 deg of lon at 56.04N, nothing for the 280 m gap.
        import math
        metres = (0.01 + 0.0105) * 111320 * math.cos(math.radians(56.04))
        self.assertAlmostEqual(trails["Bredt Hul"]["properties"]["length_mi"],
                               metres / 1609.344, delta=0.02)

    def test_other_routes_are_unchanged_by_the_opt_in(self):
        _, base, _, _, _ = self.run_cli()
        listed = self.json_file([gap_entry("r904", 400)])
        _, opted, removed, _, _ = self.run_cli("--dk-accept-gap", listed)
        for name in set(base) - {"Bredt Hul"}:
            with self.subTest(name=name):
                self.assertEqual(base[name], opted[name])
        # A wide-gap route that is not listed is still dropped.
        self.assertNotIn("Meget Bredt Hul", opted)
        self.assertEqual(removed["Meget Bredt Hul"]["removed_category"],
                         "unresolved-relation-gap")

    def test_wider_gap_needs_its_own_larger_limit(self):
        listed = self.json_file([gap_entry("r907", 1000)])
        result, trails, _, quality, _ = self.run_cli("--dk-accept-gap", listed)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(trails["Meget Bredt Hul"]["geometry"]["coordinates"]), 2)
        self.assertGreater(quality["dk_accept_gap"][0]["widest_gap_m"], 650)

    def test_gap_above_the_entry_limit_fails_the_area(self):
        listed = self.json_file([gap_entry("r907", 500)])
        result, trails, removed, quality, _ = self.run_cli("--dk-accept-gap", listed)
        self.assertEqual(result.returncode, 2)
        self.assertIn("dk-accept-gap failures", result.stderr)
        self.assertNotIn("Meget Bredt Hul", trails)
        self.assertEqual(removed["Meget Bredt Hul"]["removed_category"],
                         "unresolved-relation-gap")
        self.assertIn("not accepted", quality["dk_accept_gap_failures"][0])

    def test_gap_above_the_hard_cap_is_rejected_at_validation(self):
        # The only way to bridge ~1400 m would be a limit above the cap.
        result = self.run_cli("--dk-accept-gap",
                              self.json_file([gap_entry("r909", 1001)]))[0]
        self.assertEqual(result.returncode, 2)
        self.assertIn("--dk-accept-gap is invalid", result.stderr)
        # At the cap itself the 1400 m route still fails closed.
        result, trails, _, quality, _ = self.run_cli(
            "--dk-accept-gap", self.json_file([gap_entry("r909", 1000)]))
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("Ekstra Bredt Hul", trails)
        self.assertIn("not accepted", quality["dk_accept_gap_failures"][0])

    def test_unknown_relation_fails_the_area(self):
        result, _, _, quality, _ = self.run_cli(
            "--dk-accept-gap", self.json_file([gap_entry("r99999", 500)]))
        self.assertEqual(result.returncode, 2)
        self.assertIn("0 matching candidates", quality["dk_accept_gap_failures"][0])

    def test_entry_for_a_route_with_no_wide_gap_fails_the_area(self):
        for relation, label in (("r901", "connected"), ("r903", "25 m gap")):
            with self.subTest(route=label):
                result, trails, _, quality, _ = self.run_cli(
                    "--dk-accept-gap", self.json_file([gap_entry(relation, 500)]))
                self.assertEqual(result.returncode, 2)
                self.assertIn("not needed", quality["dk_accept_gap_failures"][0])
                # Still published exactly as without the opt-in.
                self.assertIn("Helrute", trails)

    def test_accept_gap_is_dk_v3_exact_denmark_only(self):
        listed = self.json_file([gap_entry("r904", 400)])
        for rules in ("legacy", "dk-v2"):
            with self.subTest(rules=rules):
                result = self.run_cli("--dk-accept-gap", listed, rules=rules)[0]
                self.assertEqual(result.returncode, 2)
                self.assertIn("requires --dk-rules dk-v3", result.stderr)

    def test_invalid_entries_are_rejected(self):
        bad = [
            "not json",
            {"relation": "r904"},
            [gap_entry("904", 400)],
            [gap_entry("r904", 100)],
            [gap_entry("r904", 50)],
            [gap_entry("r904", 1000.5)],
            [gap_entry("r904", True)],
            [gap_entry("r904", "400")],
            [gap_entry("r904", 400, reason="  ")],
            [gap_entry("r904", 400), gap_entry("r904", 500)],
            [{**gap_entry("r904", 400), "extra": 1}],
        ]
        for payload in bad:
            with self.subTest(payload=str(payload)[:60]):
                path = self.dir / "bad.json"
                path.write_text(payload if isinstance(payload, str)
                                else json.dumps(payload), encoding="utf-8")
                result = subprocess.run(
                    [sys.executable, str(HERE / "assemble.py"),
                     "--in", str(self.dir / "missing.pbf"), "--out", "y.geojson",
                     "--region", "dk", "--require-exact-area",
                     "--dk-rules", "dk-v3", "--dk-accept-gap", str(path)],
                    capture_output=True, text=True)
                self.assertEqual(result.returncode, 2)
                self.assertIn("--dk-accept-gap is invalid", result.stderr)


class DirectionalRoles(GapRolesCase):
    @staticmethod
    def parts_length(feature):
        return sum(len(line) for line in feature["geometry"]["coordinates"])

    def test_default_rejects_forward_backward_members(self):
        # Control: today's behavior, the route is published at 1/3 of its length.
        result, trails, _, quality, _ = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        xs = [p[0] for line in trails["Retningsrute"]["geometry"]["coordinates"]
              for p in line]
        self.assertEqual((min(xs), max(xs)), (9.02, 9.04))
        audit = next(a for a in quality["relation_member_audit"]
                     if a["relation_id"] == 908)
        reasons = {m["way_id"]: m["exclusion_reason"]
                   for m in audit["direct_way_members"]}
        self.assertEqual(sorted(r for r in reasons.values() if r),
                         ["unsupported-role-backward", "unsupported-role-forward"])

    def test_opt_in_accepts_forward_backward_on_hiking_relations(self):
        result, trails, _, quality, _ = self.run_cli("--dk-accept-directional-roles")
        self.assertEqual(result.returncode, 0, result.stderr)
        xs = [p[0] for line in trails["Retningsrute"]["geometry"]["coordinates"]
              for p in line]
        self.assertEqual((min(xs), max(xs)), (9.02, 9.08))
        audit = next(a for a in quality["relation_member_audit"]
                     if a["relation_id"] == 908)
        self.assertTrue(all(m["status"] == "included"
                            for m in audit["direct_way_members"]))
        # The raw OSM role is kept; only the effective role is main-line.
        self.assertEqual(sorted(m["role"] for m in audit["direct_way_members"]),
                         ["", "backward", "forward"])
        self.assertEqual({m["effective_role"] for m in audit["direct_way_members"]},
                         {"main"})
        self.assertEqual(
            trails["Retningsrute"]["properties"]["connectivity"]["status"],
            "accepted")

    def test_opt_in_only_changes_the_directional_route(self):
        _, base, _, _, _ = self.run_cli()
        _, opted, _, _, _ = self.run_cli("--dk-accept-directional-roles")
        self.assertEqual(set(base), set(opted))
        for name in set(base) - {"Retningsrute"}:
            with self.subTest(name=name):
                self.assertEqual(base[name], opted[name])

    def test_non_hiking_relation_and_other_roles_stay_rejected(self):
        _, opted, _, _, _ = self.run_cli("--dk-accept-directional-roles")
        for name in ("Fodrute", "Variantrute"):
            with self.subTest(name=name):
                xs = [p[0] for line in opted[name]["geometry"]["coordinates"]
                      for p in line]
                self.assertEqual((min(xs), max(xs)), (9.02, 9.04))

    def test_flag_requires_dk_v3(self):
        for rules in ("legacy", "dk-v2"):
            with self.subTest(rules=rules):
                result = self.run_cli("--dk-accept-directional-roles",
                                      rules=rules)[0]
                self.assertEqual(result.returncode, 2)
                self.assertIn("--dk-accept-directional-roles requires",
                              result.stderr)

    def test_model_rejects_the_flag_outside_exact_denmark(self):
        import model
        with self.assertRaises(ValueError):
            model.assemble({}, {}, {}, [], region=None,
                           accept_directional_roles=True)

    def test_resolver_unit_behavior(self):
        import model
        relations = {1: {"tags": {"route": "hiking"}, "members": [
            ("w", 10, ""), ("w", 11, "forward"), ("w", 12, "backward"),
            ("w", 13, "approach"), ("w", 14, "alternative")]},
            2: {"tags": {"route": "foot"}, "members": [("w", 20, "forward")]},
            3: {"tags": {"route": "hiking"}, "members": [
                ("r", 1, "approach")]}}
        default = [e["effective_role"]
                   for e in model.resolve_route_member_entries(1, relations)]
        self.assertEqual(default, ["main", "forward", "backward", "approach",
                                   "alternative"])
        opted = [e["effective_role"] for e in model.resolve_route_member_entries(
            1, relations, accept_directional_roles=True)]
        self.assertEqual(opted, ["main", "main", "main", "approach", "alternative"])
        self.assertEqual([e["effective_role"] for e in
                          model.resolve_route_member_entries(
                              2, relations, accept_directional_roles=True)],
                         ["forward"])
        # An outer approach role still overrides nested roles.
        self.assertEqual({e["effective_role"] for e in
                          model.resolve_route_member_entries(
                              3, relations, accept_directional_roles=True)},
                         {"approach"})


if __name__ == "__main__":
    unittest.main()
