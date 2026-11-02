"""Canada engine opt-ins (`--ca-accept-gap`, `--ca-keep-superroute`, only with `--region ca`), end to
end through the real assemble.py CLI on synthetic Canadian OSM files. Positive cases, fail-closed
negatives, the no-opt-in controls, the gates, and pure-function checks. No mocks."""
import json
import math
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

import ca_rules  # noqa: E402

PATH = {"highway": "path"}
SKI = {"highway": "path", "piste:type": "skitour", "foot": "yes", "sac_scale": "hiking"}
LAT = 46.05
PARK = "Rules Test Provincial Park"
REL_PARK = 9000


class Fixture(Osm):
    """Osm builder that also writes relations holding relation members."""

    def tagged_chain(self, parts, lat=LAT):
        """parts: [(lon0, lon1, tags)] ways sharing end nodes along one latitude."""
        ids, previous = [], None
        for lon0, lon1, tags in parts:
            first = previous
            if first is None:
                self._node += 1
                self.nodes.append((self._node, lon0, lat))
                first = self._node
            self._node += 1
            self.nodes.append((self._node, lon1, lat))
            self._way += 1
            self.ways.append((self._way, [first, self._node], dict(tags)))
            ids.append(self._way)
            previous = self._node
        return ids

    def rel(self, relation_id, members, **tags):
        self.relations.append((relation_id, list(members), dict(tags)))

    def xml(self):
        out = ["<?xml version='1.0' encoding='UTF-8'?>", '<osm version="0.6">']
        for node_id, lon, lat in sorted(self.nodes):
            out.append(f'<node id="{node_id}" lat="{lat}" lon="{lon}" version="1"/>')
        for way_id, refs, tags in self.ways:
            out.append(f'<way id="{way_id}" version="1">'
                       + "".join(f'<nd ref="{ref}"/>' for ref in refs)
                       + "".join(f'<tag k="{k}" v="{v}"/>' for k, v in tags.items()) + "</way>")
        for relation_id, members, tags in self.relations:
            body = "".join(
                f'<member type="{m[0]}" ref="{m[1]}" role=""/>' if isinstance(m, tuple)
                else f'<member type="way" ref="{m}" role=""/>' for m in members)
            out.append(f'<relation id="{relation_id}" version="1">{body}'
                       + "".join(f'<tag k="{k}" v="{v}"/>' for k, v in tags.items()) + "</relation>")
        out.append("</osm>")
        return "\n".join(out)

    def park(self, box=(-61.00, 46.00, -60.80, 46.10)):
        lon0, lat0, lon1, lat1 = box
        ring = self.way([(lon0, lat0), (lon1, lat0), (lon1, lat1), (lon0, lat1), (lon0, lat0)])
        self.rel(REL_PARK, [ring], type="multipolygon", boundary="protected_area", name=PARK)


def route(osm, rid, name, members, network="lwn", **extra):
    osm.rel(rid, members, type="route", route="hiking", network=network, name=name, **extra)


def gap_fixture(gap_lon=0.004, extra_gap=False, piste_tags=SKI):
    """R1 'Ski Link Trail': walkable A, piste-tagged B, walkable C (B is the gap). R2 'Other Ski Trail'
    has the same shape but is never listed. Park spans lon -61.0..-60.8."""
    osm = Fixture()
    osm.park()
    x = -60.98
    a, b, c = osm.tagged_chain([(x, x + 0.010, PATH), (x + 0.010, x + 0.010 + gap_lon, {**piste_tags, "name": "Ski Link Trail"}),
                                (x + 0.010 + gap_lon, x + 0.020 + gap_lon, PATH)])
    members = [a, b, c]
    if extra_gap:                       # a second, real hole after C (no shared node)
        d = osm.way([(x + 0.030 + gap_lon, LAT), (x + 0.040 + gap_lon, LAT)], **PATH)
        members.append(d)
    route(osm, 9101, "Ski Link Trail", members)
    y = -60.90
    e, f, g = osm.tagged_chain([(y, y + 0.010, PATH), (y + 0.010, y + 0.014, SKI), (y + 0.014, y + 0.024, PATH)],
                               lat=46.07)
    route(osm, 9102, "Other Ski Trail", [e, f, g])
    return osm


def super_fixture(outside=None, parent=False, network="lwn", kid_network="lwn", name="Highland Ring Trail",
                  kid_name=None, alt_name=None, lone=False, park_east=-60.80):
    """S = r9201 'Highland Ring Trail' holds three child relations (each its own ways); r9210 is an
    independent trail. Optional outside way, a larger parent route, and long-distance variants."""
    osm = Fixture()
    osm.park((-61.00, 46.00, park_east, 46.10))
    kids = []
    for i in range(3):
        lon = -60.97 + i * 0.03
        ways = osm.tagged_chain([(lon, lon + 0.012, PATH), (lon + 0.012, lon + 0.024, PATH)])
        if outside is not None and i == 2:
            ways += osm.tagged_chain([(lon + 0.024, lon + 0.024 + outside, PATH)])
        route(osm, 9211 + i, kid_name if (kid_name and i == 0) else f"Highland Ring Trail ({i + 1})", ways,
              network=kid_network if i == 0 else "lwn")
        kids.append(("r", 9211 + i))
    tags = {"alt_name": alt_name} if alt_name else {}
    route(osm, 9201, name, [] if lone else kids, network=network, **tags)
    if lone:                              # not a super-relation: it holds ways only
        osm.relations[-1] = (9201, osm.tagged_chain([(-60.90, -60.88, PATH)], lat=46.08), osm.relations[-1][2])
    if parent:
        route(osm, 9300, "Larger Provincial Route", [("r", 9201)])
    ind = osm.tagged_chain([(-60.90, -60.88, PATH)], lat=46.09)
    route(osm, 9210, "Lone Ridge Trail", ind)
    return osm


def mi(coords_lines):
    total = 0.0
    for line in coords_lines:
        for (lo0, la0), (lo1, la1) in zip(line, line[1:]):
            dx = math.radians(lo1 - lo0) * math.cos(math.radians((la0 + la1) / 2))
            dy = math.radians(la1 - la0)
            total += 6371000 * math.hypot(dx, dy)
    return total / 1609.344


class Base(unittest.TestCase):
    count = 0

    @classmethod
    def setUpClass(cls):
        if not HAVE_DEPS:
            raise unittest.SkipTest("pyosmium/shapely not installed")
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_cli(self, fixture, rule_flags=(), *, region="ca", rules=None, exact=True, way=False, name=PARK):
        Base.count += 1
        label = f"run{Base.count}"
        osm = self.dir / f"{label}.osm"
        osm.write_text(fixture.xml(), encoding="utf-8")
        out = self.dir / f"{label}.trails.geojson"
        report = self.dir / f"{label}.report.json"
        cmd = [sys.executable, str(HERE / "assemble.py"), "--in", str(osm), "--out", str(out),
               "--only-area", name, "--min-length-mi", "0.1", "--per-area-merge", "--report-json", str(report)]
        if exact:
            cmd += ["--require-exact-area", "--expected-area-way-id" if way else "--expected-area-relation-id",
                    str(REL_PARK)]
        if region:
            cmd += ["--region", region]
        if rules:
            cmd += ["--dk-rules", rules]
        for flag, entries in rule_flags:
            path = self.dir / f"{label}.{flag.strip('-')}.json"
            path.write_text(entries if isinstance(entries, str) else json.dumps(entries), encoding="utf-8")
            cmd += [flag, str(path)]
        result = subprocess.run(cmd, capture_output=True, text=True)
        features = (json.loads(out.read_text())["features"] if out.exists() else None)
        data = json.loads(report.read_text()) if report.exists() else None
        removed_path = out.with_name(f"{label}.removed.geojson")
        removed = json.loads(removed_path.read_text())["features"] if removed_path.exists() else []
        dropped_path = out.with_name(f"{label}.ingest-dropped.geojson")
        dropped = json.loads(dropped_path.read_text())["features"] if dropped_path.exists() else []
        return result, features, data, removed, dropped

    @staticmethod
    def by_name(features):
        return {f["properties"]["name"]: f for f in features}


def gap_entry(relation="r9101", max_gap_m=400, reason="reviewed: signed hiking route, ski-tour tags on foot=yes ways"):
    return {"relation": relation, "max_gap_m": max_gap_m, "reason": reason}


def keep_entry(relation="r9201", reason="reviewed: single-park named trail wholly inside the park"):
    return {"relation": relation, "reason": reason}


GAP = "--ca-accept-gap"
KEEP = "--ca-keep-superroute"


class AcceptGap(Base):
    def test_control_without_the_flag_drops_the_ski_tagged_ways(self):
        for region in ("ca", None):
            with self.subTest(region=region):
                result, features, report, _, dropped = self.run_cli(gap_fixture(), region=region)
                self.assertEqual(result.returncode, 0, result.stderr)
                trail = self.by_name(features)["Ski Link Trail"]
                self.assertEqual(len(trail["geometry"]["coordinates"]), 2)          # two parts
                self.assertEqual(len(trail["properties"]["member_ways"]), 2)
                self.assertNotIn("ca_rules", report)
                self.assertFalse(any(d["properties"].get("restored_by_accept_gap") for d in dropped))

    def test_flag_restores_the_members_and_publishes_one_connected_route(self):
        result, features, report, _, dropped = self.run_cli(gap_fixture(), [(GAP, [gap_entry()])])
        self.assertEqual(result.returncode, 0, result.stderr)
        by = self.by_name(features)
        trail = by["Ski Link Trail"]
        self.assertEqual(len(trail["properties"]["member_ways"]), 3)
        self.assertEqual(len(trail["geometry"]["coordinates"]), 1)
        self.assertAlmostEqual(trail["properties"]["length_mi"], mi(trail["geometry"]["coordinates"]), places=2)
        rec = report["ca_rules"]["accept_gap"][0]
        self.assertEqual((rec["status"], rec["restored_count"], rec["pieces_before"], rec["pieces_after"]),
                         ("accepted", 1, 2, 1))
        self.assertGreater(rec["widest_gap_before_m"], 250)
        self.assertLess(rec["widest_gap_before_m"], 350)
        self.assertEqual(rec["widest_gap_after_m"], 0.0)
        self.assertEqual(report["ca_rules"]["failures"], [])
        self.assertEqual(sum(1 for d in dropped if d["properties"].get("restored_by_accept_gap")), 1)

    def test_only_the_listed_relation_changes(self):
        _, base, *_ = self.run_cli(gap_fixture())
        _, opt, *_ = self.run_cli(gap_fixture(), [(GAP, [gap_entry()])])
        other_base, other_opt = self.by_name(base)["Other Ski Trail"], self.by_name(opt)["Other Ski Trail"]
        self.assertEqual(other_base["geometry"], other_opt["geometry"])
        self.assertEqual(other_base["properties"], other_opt["properties"])
        self.assertEqual(len(other_opt["geometry"]["coordinates"]), 2)

    def test_restore_adds_no_synthesized_geometry(self):
        _, features, *_ = self.run_cli(gap_fixture(), [(GAP, [gap_entry()])])
        trail = self.by_name(features)["Ski Link Trail"]
        lon = sorted({round(c[0], 6) for line in trail["geometry"]["coordinates"] for c in line})
        self.assertEqual(lon, [-60.98, -60.97, -60.966, -60.956])          # fixture vertices only

    def test_over_the_limit_fails_closed(self):
        result, features, report, *_ = self.run_cli(gap_fixture(), [(GAP, [gap_entry(max_gap_m=200)])])
        self.assertEqual(result.returncode, 2)
        self.assertIn("over max_gap_m", result.stderr)
        self.assertEqual(report["status"], "failed")
        # a failed entry restores nothing: the trails file still holds the default two parts
        self.assertEqual(len(self.by_name(features)["Ski Link Trail"]["geometry"]["coordinates"]), 2)

    def test_exactly_at_the_limit_is_accepted(self):
        _, _, report, *_ = self.run_cli(gap_fixture(), [(GAP, [gap_entry(max_gap_m=1000)])])
        gap = report["ca_rules"]["accept_gap"][0]["widest_gap_before_m"]
        result, *_ = self.run_cli(gap_fixture(), [(GAP, [gap_entry(max_gap_m=gap)])])
        self.assertEqual(result.returncode, 0, result.stderr)
        result, *_ = self.run_cli(gap_fixture(), [(GAP, [gap_entry(max_gap_m=gap - 1)])])
        self.assertEqual(result.returncode, 2)

    def test_hard_cap_is_not_raised_by_the_registry(self):
        for cap in (1001, 1e9):
            with self.subTest(cap):
                result, *_ = self.run_cli(gap_fixture(0.020), [(GAP, [gap_entry(max_gap_m=cap)])])
                self.assertEqual(result.returncode, 2)
                self.assertIn("--ca-accept-gap is invalid", result.stderr)
        # a real 1400 m gap under the cap of 1000 still fails closed
        result, *_ = self.run_cli(gap_fixture(0.018), [(GAP, [gap_entry(max_gap_m=1000)])])
        self.assertEqual(result.returncode, 2)
        self.assertIn("over max_gap_m", result.stderr)

    def test_unknown_relation_fails_closed(self):
        result, *_ = self.run_cli(gap_fixture(), [(GAP, [gap_entry("r4242")])])
        self.assertEqual(result.returncode, 2)
        self.assertIn("not an emitted hiking route relation", result.stderr)

    def test_unneeded_entry_no_ski_tagged_member_fails_closed(self):
        result, *_ = self.run_cli(gap_fixture(piste_tags=PATH), [(GAP, [gap_entry()])])
        self.assertEqual(result.returncode, 2)
        self.assertIn("no member way is dropped for a ski-piste tag alone", result.stderr)

    def test_unneeded_entry_when_the_walkable_members_are_not_split_fails_closed(self):
        osm = Fixture()
        osm.park()
        a, b = osm.tagged_chain([(-60.98, -60.97, PATH), (-60.97, -60.96, SKI)])
        route(osm, 9101, "Ski Link Trail", [a, b])
        result, *_ = self.run_cli(osm, [(GAP, [gap_entry()])])
        self.assertEqual(result.returncode, 2)
        self.assertIn("not split", result.stderr)

    def test_a_remaining_gap_after_the_restore_fails_closed(self):
        result, *_ = self.run_cli(gap_fixture(extra_gap=True), [(GAP, [gap_entry(max_gap_m=400)])])
        self.assertEqual(result.returncode, 2)
        self.assertIn("remains after the restore", result.stderr)
        # a limit that covers both gaps accepts it; the far piece stays a separate part
        result, features, report, *_ = self.run_cli(gap_fixture(extra_gap=True), [(GAP, [gap_entry(max_gap_m=900)])])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.by_name(features)["Ski Link Trail"]["geometry"]["coordinates"]), 2)
        self.assertEqual(report["ca_rules"]["accept_gap"][0]["pieces_after"], 2)

    def test_other_drop_reasons_are_never_restored(self):
        for label, tags in (("foot=no", {**SKI, "foot": "no"}),
                            ("no hiking evidence", {"highway": "path", "piste:type": "skitour"}),
                            ("motor vehicle", {**SKI, "motor_vehicle": "yes"}),
                            ("mtb flow", {**SKI, "mtb:type": "flow"})):
            with self.subTest(label):
                result, *_ = self.run_cli(gap_fixture(piste_tags=tags), [(GAP, [gap_entry()])])
                self.assertEqual(result.returncode, 2)
                self.assertIn("no member way is dropped for a ski-piste tag alone", result.stderr)

    def test_a_member_way_missing_from_the_extract_fails_closed(self):
        osm = gap_fixture()
        index = [i for i, r in enumerate(osm.relations) if r[0] == 9101][0]
        rid, members, tags = osm.relations[index]
        osm.relations[index] = (rid, members + [987654321], tags)
        result, *_ = self.run_cli(osm, [(GAP, [gap_entry()])])
        self.assertEqual(result.returncode, 2)
        self.assertIn("member ways missing from the extract: [987654321]", result.stderr)
        result, features, *_ = self.run_cli(osm)                              # control: no flag, default untouched
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.by_name(features)["Ski Link Trail"]["geometry"]["coordinates"]), 2)

    def test_a_restored_way_removed_by_a_later_rule_fails_closed(self):
        # The whole route is below --min-length-mi after the restore is impossible here, so use a route
        # whose restored way ends up outside the park: the clip drops the trail's share.
        osm = Fixture()
        osm.park((-61.00, 46.00, -60.97, 46.10))                     # narrow park: route mostly outside
        a, b, c = osm.tagged_chain([(-60.90, -60.89, PATH), (-60.89, -60.885, SKI), (-60.885, -60.87, PATH)])
        route(osm, 9101, "Ski Link Trail", [a, b, c])
        result, *_ = self.run_cli(osm, [(GAP, [gap_entry()])])
        self.assertEqual(result.returncode, 2)
        self.assertRegex(result.stderr, "not in any published trail|not an emitted")

    def test_malformed_entries_are_rejected(self):
        good = gap_entry()
        bad = [
            "not json", {"relation": "r9101"}, [{**good, "extra": 1}], [{**good, "relation": "9101"}],
            [{**good, "relation": "r0"}], [{**good, "max_gap_m": 0}], [{**good, "max_gap_m": -5}],
            [{**good, "max_gap_m": True}], [{**good, "max_gap_m": "300"}], [{**good, "max_gap_m": float("nan")}],
            [{**good, "reason": ""}], [{**good, "reason": "  "}], [good, good], [{k: v for k, v in good.items() if k != "reason"}],
        ]
        for entries in bad:
            with self.subTest(entries=str(entries)[:60]):
                payload = entries if isinstance(entries, str) else json.dumps(entries, allow_nan=True)
                result, *_ = self.run_cli(gap_fixture(), [(GAP, payload)])
                self.assertEqual(result.returncode, 2)
                self.assertIn("--ca-accept-gap is invalid", result.stderr)

    def test_the_flag_is_unreachable_outside_exact_canada_with_legacy_rules(self):
        entries = [(GAP, [gap_entry()])]
        cases = (("no region", dict(region=None)), ("us", dict(region="us")), ("dk", dict(region="dk")),
                 ("co", dict(region="co")), ("no exact area", dict(exact=False)),
                 ("dk-v3 rules", dict(rules="dk-v3")), ("dk-v2 rules", dict(rules="dk-v2")))
        for label, kwargs in cases:
            with self.subTest(label):
                result, features, *_ = self.run_cli(gap_fixture(), entries, **kwargs)
                self.assertEqual(result.returncode, 2)
                self.assertIn("--ca-accept-gap requires --region ca", result.stderr)
                self.assertIsNone(features)

    def test_model_refuses_the_rule_outside_region_ca(self):
        import model
        for region in (None, "us", "dk", "co"):
            with self.subTest(region):
                with self.assertRaises(ValueError):
                    model.assemble({}, {}, {}, [], region=region, ca_accept_gap={9101: gap_entry()})
                with self.assertRaises(ValueError):
                    model.assemble({}, {}, {}, [], region=region, ca_keep_superroute=[keep_entry()])
        self.assertEqual(model.assemble({}, {}, {}, [], region="ca"), [])        # no opt-in: untouched


class KeepSuperroute(Base):
    def test_control_without_the_flag_drops_the_super_relation_and_its_children(self):
        for region in ("ca", None):
            with self.subTest(region=region):
                result, features, report, removed, _ = self.run_cli(super_fixture(), region=region)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(sorted(self.by_name(features)), ["Lone Ridge Trail"])
                cats = {f["properties"]["name"]: f["properties"]["removed_category"] for f in removed}
                self.assertEqual(cats["Highland Ring Trail"], "thru-hike")
                self.assertEqual(sum(1 for c in cats.values() if c == "thru-hike"), 4)
                self.assertNotIn("ca_rules", report)

    def test_flag_keeps_the_super_relation_as_one_trail_and_children_stay_dropped(self):
        result, features, report, removed, _ = self.run_cli(super_fixture(), [(KEEP, [keep_entry()])])
        self.assertEqual(result.returncode, 0, result.stderr)
        by = self.by_name(features)
        self.assertEqual(sorted(by), ["Highland Ring Trail", "Lone Ridge Trail"])
        trail = by["Highland Ring Trail"]
        self.assertEqual(len(trail["properties"]["member_ways"]), 6)         # 3 kids x 2 ways, each once
        self.assertAlmostEqual(trail["properties"]["length_mi"], mi(trail["geometry"]["coordinates"]), places=2)
        kids = {f["properties"]["name"]: f["properties"] for f in removed}
        self.assertEqual(sorted(kids), ["Highland Ring Trail (1)", "Highland Ring Trail (2)",
                                        "Highland Ring Trail (3)"])
        for props in kids.values():
            self.assertEqual(props["removed_category"], "thru-hike")
            self.assertIn("kept whole (registry keep_superroute)", props["removed_reason"])
        rec = report["ca_rules"]["keep_superroute"][0]
        self.assertEqual((rec["status"], rec["outside_deg"], rec["children"]), ("accepted", 0.0, [9211, 9212, 9213]))

    def test_the_independent_trail_is_unchanged(self):
        _, base, *_ = self.run_cli(super_fixture())
        _, opt, *_ = self.run_cli(super_fixture(), [(KEEP, [keep_entry()])])
        self.assertEqual(self.by_name(base)["Lone Ridge Trail"], self.by_name(opt)["Lone Ridge Trail"])

    def test_an_unlisted_super_relation_is_still_dropped(self):
        osm = super_fixture()
        route(osm, 9400, "Second Ring Trail", [("r", 9211)])           # another super-relation over a child
        result, features, _, removed, _ = self.run_cli(osm, [(KEEP, [keep_entry()])])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Second Ring Trail", self.by_name(features))
        reasons = {f["properties"]["name"]: f["properties"]["removed_reason"] for f in removed}
        self.assertIn("long-distance thru-hike route", reasons["Second Ring Trail"])

    def test_a_part_outside_the_polygon_fails_closed_a_hair_over_the_edge_does_not(self):
        # The third child ends at lon -60.886; the park's east edge is set right there.
        edge = -60.886
        ok = super_fixture(outside=0.0001, park_east=edge)                 # ~8 m of line past the edge
        result, features, report, *_ = self.run_cli(ok, [(KEEP, [keep_entry()])])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Highland Ring Trail", self.by_name(features))
        self.assertEqual(report["ca_rules"]["keep_superroute"][0]["outside_deg"], 0.0)
        for overhang in (0.002, 0.2):                                      # ~150 m and ~15 km
            with self.subTest(overhang):
                bad = super_fixture(outside=overhang, park_east=edge)
                result, _, report, *_ = self.run_cli(bad, [(KEEP, [keep_entry()])])
                self.assertEqual(result.returncode, 2)
                self.assertIn("outside the area polygon", result.stderr)
                self.assertEqual(report["status"], "failed")

    def test_control_the_same_overhang_without_the_flag_is_simply_dropped(self):
        result, features, *_ = self.run_cli(super_fixture(outside=0.2, park_east=-60.886))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sorted(self.by_name(features)), ["Lone Ridge Trail"])

    def test_outside_length_tolerance_boundaries(self):
        from shapely.geometry import box
        area = box(0, 0, 1, 1)
        inside = [[(0.1, 0.5), (0.9, 0.5)]]
        self.assertEqual(ca_rules.outside_length_deg(inside, area), 0.0)
        hug = [[(0.5, 1.0001), (0.6, 1.0001)]]                 # 0.0001 deg over the edge (about 11 m)
        self.assertEqual(ca_rules.outside_length_deg(hug, area), 0.0)
        out = [[(0.5, 1.0), (0.5, 1.01)]]                      # a 0.01 deg spur past the edge (about 1.1 km)
        self.assertGreater(ca_rules.outside_length_deg(out, area), 0.0)

    def test_a_child_of_a_larger_route_fails_closed(self):
        result, features, *_ = self.run_cli(super_fixture(parent=True), [(KEEP, [keep_entry()])])
        self.assertEqual(result.returncode, 2)
        self.assertIn("child of larger route relation(s) [9300]", result.stderr)
        self.assertNotIn("Highland Ring Trail", self.by_name(features))       # a failed plan keeps nothing

    def test_long_distance_networks_fail_closed(self):
        for network in ("nwn", "iwn", "rwn"):
            with self.subTest(f"super {network}"):
                result, features, *_ = self.run_cli(super_fixture(network=network), [(KEEP, [keep_entry()])])
                self.assertEqual(result.returncode, 2)
                self.assertIn(f"long-distance network ({network})", result.stderr)
                self.assertNotIn("Highland Ring Trail", self.by_name(features))
            with self.subTest(f"child {network}"):
                result, *_ = self.run_cli(super_fixture(kid_network=network), [(KEEP, [keep_entry()])])
                self.assertEqual(result.returncode, 2)
                self.assertIn(f"long-distance network ({network})", result.stderr)

    def test_recognised_long_distance_names_fail_closed(self):
        names = ("Trans Canada Trail", "The Great Trail", "Sentier Transcanadien", "Bruce Trail", "Rideau Trail",
                 "Great Divide Trail", "International Appalachian Trail", "Eastern Continental Trail")
        for label in names:
            with self.subTest(f"name {label}"):
                result, *_ = self.run_cli(super_fixture(name=label), [(KEEP, [keep_entry()])])
                self.assertEqual(result.returncode, 2)
                self.assertIn("recognised long-distance trail", result.stderr)
            with self.subTest(f"alt_name {label}"):
                result, *_ = self.run_cli(super_fixture(alt_name=label), [(KEEP, [keep_entry()])])
                self.assertEqual(result.returncode, 2)
                self.assertIn("recognised long-distance trail", result.stderr)
            with self.subTest(f"child {label}"):
                result, *_ = self.run_cli(super_fixture(kid_name=label), [(KEEP, [keep_entry()])])
                self.assertEqual(result.returncode, 2)
                self.assertIn("recognised long-distance trail", result.stderr)

    def test_a_famous_thru_hike_name_fails_closed(self):
        self.assertIsNone(ca_rules.long_distance_name({"name": "Hayduke Trail"}))     # only the shared list knows it
        result, *_ = self.run_cli(super_fixture(name="Hayduke Trail"), [(KEEP, [keep_entry()])])
        self.assertEqual(result.returncode, 2)
        self.assertIn("famous thru-hike by name", result.stderr)
        result, *_ = self.run_cli(super_fixture(name="Pacific Crest Trail"), [(KEEP, [keep_entry()])])
        self.assertEqual(result.returncode, 2)
        self.assertIn("recognised long-distance trail", result.stderr)

    def test_not_a_super_relation_and_unknown_relation_fail_closed(self):
        result, *_ = self.run_cli(super_fixture(lone=True), [(KEEP, [keep_entry()])])
        self.assertEqual(result.returncode, 2)
        self.assertIn("not a super-relation", result.stderr)
        result, *_ = self.run_cli(super_fixture(), [(KEEP, [keep_entry("r4242")])])
        self.assertEqual(result.returncode, 2)
        self.assertIn("not a hiking route relation", result.stderr)

    def test_a_super_relation_removed_by_the_clip_fails_closed(self):
        osm = super_fixture()
        osm.relations = [r for r in osm.relations if r[0] != REL_PARK]
        osm.park((-61.00, 46.00, -60.99, 46.01))                         # park far from the trails
        result, *_ = self.run_cli(osm, [(KEEP, [keep_entry()])])
        self.assertEqual(result.returncode, 2)
        self.assertRegex(result.stderr, "outside the area polygon|not in the published population")

    def test_a_kept_trail_removed_by_a_later_rule_fails_closed(self):
        osm = Fixture()
        osm.park()
        kids = []
        for i in range(2):                                   # two ~30 m children: inside the park, under 0.1 mi
            lon = -60.97 + i * 0.01
            route(osm, 9211 + i, f"Tiny Ring Trail ({i + 1})", osm.tagged_chain([(lon, lon + 0.0004, PATH)]))
            kids.append(("r", 9211 + i))
        route(osm, 9201, "Tiny Ring Trail", kids)
        result, features, report, removed, _ = self.run_cli(osm, [(KEEP, [keep_entry()])])
        self.assertEqual(result.returncode, 2)
        self.assertIn("not in the published population", result.stderr)
        self.assertNotIn("outside the area polygon", result.stderr)
        self.assertEqual(report["ca_rules"]["keep_superroute"][0]["outside_deg"], 0.0)

    def test_malformed_entries_are_rejected(self):
        good = keep_entry()
        bad = ["not json", {"relation": "r9201"}, [{**good, "extra": 1}], [{**good, "max_gap_m": 100}],
               [{**good, "relation": "9201"}], [{**good, "relation": "r-1"}], [{**good, "reason": ""}],
               [{**good, "reason": 5}], [good, good], [{"relation": "r9201"}]]
        for entries in bad:
            with self.subTest(entries=str(entries)[:60]):
                payload = entries if isinstance(entries, str) else json.dumps(entries)
                result, *_ = self.run_cli(super_fixture(), [(KEEP, payload)])
                self.assertEqual(result.returncode, 2)
                self.assertIn("--ca-keep-superroute is invalid", result.stderr)

    def test_the_flag_is_unreachable_outside_exact_canada_with_legacy_rules(self):
        entries = [(KEEP, [keep_entry()])]
        cases = (("no region", dict(region=None)), ("us", dict(region="us")), ("dk", dict(region="dk")),
                 ("no exact area", dict(exact=False)), ("dk-v3 rules", dict(rules="dk-v3")),
                 ("way-backed area", dict(way=True)))
        for label, kwargs in cases:
            with self.subTest(label):
                result, features, *_ = self.run_cli(super_fixture(), entries, **kwargs)
                self.assertEqual(result.returncode, 2)
                self.assertIn("--ca-keep-superroute requires --region ca", result.stderr)
                self.assertIsNone(features)

    def test_both_rules_can_run_together(self):
        osm = gap_fixture()
        sup = super_fixture()
        osm.nodes += [(n + 50000, lon, lat) for n, lon, lat in sup.nodes]
        osm.ways += [(w + 50000, [n + 50000 for n in refs], tags) for w, refs, tags in sup.ways]
        for rid, members, tags in sup.relations:
            if rid == REL_PARK:
                continue
            osm.relations.append((rid, [(m[0], m[1]) if isinstance(m, tuple) else m + 50000 for m in members], tags))
        result, features, report, *_ = self.run_cli(
            osm, [(GAP, [gap_entry()]), (KEEP, [keep_entry()])])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Highland Ring Trail", self.by_name(features))
        self.assertEqual(len(self.by_name(features)["Ski Link Trail"]["geometry"]["coordinates"]), 1)
        self.assertEqual(report["ca_rules"]["failures"], [])


class Vocabulary(unittest.TestCase):
    def test_every_ca_trail_filter_long_distance_phrase_is_recognised_here_too(self):
        sys.path.insert(0, str(HERE.parent.parent / "scripts"))
        import ca_trail_filter
        for sample in ("Trans Canada Trail", "TransCanada Trail", "The Great Trail", "Sentier Trans-Canadien",
                       "Sentier transcanadien", "Bruce Trail", "Rideau Trail", "Great Divide Trail",
                       "Sentier national", "Sentier international des Appalaches", "International Appalachian Trail",
                       "Waterfront Trail", "Confederation Trail"):
            with self.subTest(sample):
                self.assertTrue(ca_trail_filter.LONG_DISTANCE.search(ca_trail_filter.fold(sample)), sample)
                self.assertIsNotNone(ca_rules.long_distance_name({"name": sample}), sample)

    def test_ordinary_park_trails_are_not_long_distance(self):
        for sample in ("Western Uplands Backpacking Trail", "Highland Backpacking Trail", "Joffre Lakes Trail",
                       "West Coast Trail", "Fundy Footpath", "Sentier des Chutes"):
            with self.subTest(sample):
                self.assertIsNone(ca_rules.long_distance_name({"name": sample}))

    def test_validators_cover_the_cap_and_keys(self):
        self.assertEqual(ca_rules.CA_ACCEPT_GAP_CAP_M, 1000.0)
        self.assertEqual(ca_rules.validate_accept_gap([gap_entry(max_gap_m=1000)])[0]["max_gap_m"], 1000.0)
        for bad in (1000.5, 0, -1, math.inf, math.nan, None):
            with self.subTest(bad):
                with self.assertRaises(ValueError):
                    ca_rules.validate_accept_gap([gap_entry(max_gap_m=bad)])
        self.assertEqual(ca_rules.validate_keep_superroute([keep_entry()])[0]["relation"], "r9201")
        with self.assertRaises(ValueError):
            ca_rules.validate_keep_superroute([{"relation": "r1", "reason": "x", "max_gap_m": 1}])


if __name__ == "__main__":
    unittest.main()
