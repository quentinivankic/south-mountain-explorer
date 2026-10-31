"""dk-v3 trail-name prefix, end to end through the real assemble.py CLI.

A synthetic OSM file (one wood area plus signed hiking routes with generic and
non-generic names) is assembled with the actual argument parser. Every behavior
has its no-flag control; the trail id comes out of the real to_app_json.convert.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "serve"))

try:
    import osmium  # noqa: F401
    import shapely  # noqa: F401
    HAVE_DEPS = True
except ImportError:
    HAVE_DEPS = False

import dk_names  # noqa: E402

AREA = "Testskov"
AREA_RELATION = 500
LAT0, LAT1, LON0, LON1 = 56.00, 56.10, 9.00, 9.10
PREFIX = "Svanninge"
# (relation id, OSM name). Rows are 0.01 deg of latitude apart, each ~1.2 mi long.
GENERIC = [(901, "Grøn rute"), (902, "Gul"), (903, "Blå Rute"), (904, "Rute 3"),
           (905, "Tur 2 - orange")]
KEPT = [(911, "Kirkestien"), (912, "Testskov Rød"), (913, "Pamhule - rød rute")]


def build_fixture(rows):
    ring = [(LON0, LAT0), (LON1, LAT0), (LON1, LAT1), (LON0, LAT1), (LON0, LAT0)]
    nodes, ways, relations = [], [], []
    node_id = 1000

    def way(points):
        nonlocal node_id
        refs = []
        for lon, lat in points:
            node_id += 1
            nodes.append((node_id, lon, lat))
            refs.append(node_id)
        ways.append((2000 + len(ways) + 1, refs))
        return ways[-1][0]

    boundary = way(ring)
    relations.append((AREA_RELATION, [boundary], {
        "type": "multipolygon", "natural": "wood", "name": AREA}))
    for index, (rel, name) in enumerate(rows):
        lat = 56.01 + 0.007 * index
        w = way([(9.01, lat), (9.03, lat), (9.05, lat)])
        relations.append((rel, [w], {"type": "route", "route": "hiking",
                                     "network": "lwn", "name": name}))
    out = ["<?xml version='1.0' encoding='UTF-8'?>", '<osm version="0.6">']
    for nid, lon, lat in sorted(nodes):
        out.append(f'<node id="{nid}" lat="{lat}" lon="{lon}" version="1"/>')
    waytags = ' <tag k="highway" v="path"/>'
    for wid, refs in ways:
        tag = "" if wid == boundary else waytags
        out.append(f'<way id="{wid}" version="1">'
                   + "".join(f'<nd ref="{r}"/>' for r in refs) + tag + "</way>")
    for rid, members, tags in relations:
        out.append(f'<relation id="{rid}" version="1">'
                   + "".join(f'<member type="way" ref="{m}" role=""/>' for m in members)
                   + "".join(f'<tag k="{k}" v="{v}"/>' for k, v in tags.items())
                   + "</relation>")
    out.append("</osm>")
    return "\n".join(out)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not HAVE_DEPS:
            raise unittest.SkipTest("pyosmium/shapely not installed")
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        cls.osm = cls.dir / "fixture.osm"
        cls.osm.write_text(build_fixture(GENERIC + KEPT), encoding="utf-8")
        cls.runs = {}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def cli(self, rules, *extra, osm=None, tag=None):
        key = (rules, extra, str(osm))
        if key in self.runs:
            return self.runs[key]
        label = tag or f"run{len(self.runs)}"
        out = self.dir / f"{label}.trails.geojson"
        report = self.dir / f"{label}.report.json"
        result = subprocess.run(
            [sys.executable, str(HERE / "assemble.py"), "--in", str(osm or self.osm),
             "--out", str(out), "--only-area", AREA, "--require-exact-area",
             "--expected-area-relation-id", str(AREA_RELATION),
             "--min-length-mi", "0.1", "--per-area-merge", "--region", "dk",
             "--dk-rules", rules, "--report-json", str(report), *extra],
            capture_output=True, text=True)
        features = (json.loads(out.read_text())["features"] if out.exists() else None)
        rep = json.loads(report.read_text()) if report.exists() else None
        self.runs[key] = (result, features, rep, out)
        return self.runs[key]

    @staticmethod
    def names(features):
        return sorted(f["properties"]["name"] for f in features)


class Prefix(Base):
    def test_control_without_the_flag_has_osm_names_no_osm_name_and_no_report_block(self):
        result, features, report, _ = self.cli("dk-v3")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.names(features), sorted(n for _, n in GENERIC + KEPT))
        self.assertTrue(all("osm_name" not in f["properties"] for f in features))
        self.assertNotIn("name_prefix", report)

    def test_generic_names_get_the_prefix_and_keep_their_osm_name(self):
        result, features, report, _ = self.cli("dk-v3", "--dk-name-prefix", PREFIX)
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = sorted([f"{PREFIX} {n}" for _, n in GENERIC] + [n for _, n in KEPT])
        self.assertEqual(self.names(features), expected)
        for f in features:
            p = f["properties"]
            if p["name"].startswith(PREFIX + " "):
                self.assertEqual(f"{PREFIX} {p['osm_name']}", p["name"])
            else:
                self.assertNotIn("osm_name", p)
        block = report["name_prefix"]
        self.assertEqual(block["prefix"], PREFIX)
        self.assertEqual(sorted(r["osm_name"] for r in block["renamed"]),
                         sorted(n for _, n in GENERIC))
        self.assertEqual(block["unchanged"], len(KEPT))

    def test_names_with_a_place_or_a_non_colour_word_are_left_alone(self):
        _, features, _, _ = self.cli("dk-v3", "--dk-name-prefix", PREFIX)
        by_name = {f["properties"]["name"] for f in features}
        for _, name in KEPT:
            self.assertIn(name, by_name)

    def test_geometry_and_everything_but_the_name_is_identical_to_the_control(self):
        _, control, _, _ = self.cli("dk-v3")
        _, prefixed, _, _ = self.cli("dk-v3", "--dk-name-prefix", PREFIX)
        def strip(feature):
            props = {k: v for k, v in feature["properties"].items()
                     if k not in ("name", "osm_name")}
            return json.dumps({"p": props, "g": feature["geometry"]}, sort_keys=True)
        by_ckey = lambda fs: {f["properties"]["ckey"]: strip(f) for f in fs}  # noqa: E731
        self.assertEqual(by_ckey(control), by_ckey(prefixed))

    def test_trail_ids_do_not_change(self):
        import to_app_json
        _, control, _, _ = self.cli("dk-v3")
        _, prefixed, _, _ = self.cli("dk-v3", "--dk-name-prefix", PREFIX)
        def ids(features):
            row = to_app_json.convert({"features": features}, "testskov-dk", AREA,
                                      "Denmark", (56.05, 9.05), AREA_RELATION,
                                      {"trail", "hike", "route"})
            return {t["id"]: t["distanceMi"] for t in row["trails"]}, row
        control_ids, control_row = ids(control)
        prefixed_ids, prefixed_row = ids(prefixed)
        self.assertEqual(control_ids, prefixed_ids)
        self.assertIn("gul", prefixed_ids)
        self.assertIn("gr-n-rute", prefixed_ids)
        # The row's display names did change (control has none of the prefix).
        self.assertTrue(any(t["name"] == f"{PREFIX} Gul" for t in prefixed_row["trails"]))
        self.assertFalse(any(PREFIX in t["name"] for t in control_row["trails"]))

    def test_curated_exclude_selectors_keep_working_on_the_osm_name(self):
        exclude = self.dir / "exclude.json"
        exclude.write_text(json.dumps([{"id": "n:Gul", "name": "Gul", "reason": "test"}]),
                           encoding="utf-8")
        result, features, _, _ = self.cli(
            "dk-v3", "--dk-name-prefix", PREFIX, "--dk-curated-exclude", str(exclude),
            tag="excluded")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(f"{PREFIX} Gul", self.names(features))
        self.assertIn(f"{PREFIX} Grøn rute", self.names(features))

    def test_a_prefixed_name_equal_to_another_trail_fails_the_area(self):
        osm = self.dir / "collide.osm"
        osm.write_text(build_fixture([(901, "Gul"), (902, f"{PREFIX} Gul")]),
                       encoding="utf-8")
        result, _, report, _ = self.cli("dk-v3", "--dk-name-prefix", PREFIX,
                                        osm=osm, tag="collide")
        self.assertEqual(result.returncode, 2)
        self.assertIn("name-prefix collision", result.stderr)
        self.assertEqual(report["status"], "failed")
        # Control: without the flag the same file assembles cleanly.
        ok, features, _, _ = self.cli("dk-v3", osm=osm, tag="collide-control")
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(self.names(features), sorted(["Gul", f"{PREFIX} Gul"]))


class ParserGuards(unittest.TestCase):
    def parse(self, *extra, exact=True, rules="dk-v3", region="dk"):
        args = [sys.executable, str(HERE / "assemble.py"), "--in", "missing.pbf",
                "--out", "y.geojson", "--region", region, "--dk-rules", rules, *extra]
        if exact:
            args += ["--only-area", AREA, "--require-exact-area",
                     "--expected-area-relation-id", "1", "--report-json", "r.json"]
        return subprocess.run(args, capture_output=True, text=True)

    def test_empty_blank_padded_generic_and_overlong_prefixes_are_rejected(self):
        for bad in ("", "   ", " Svanninge", "Svanninge ", "Gul", "Rute 3", "x" * 41,
                    "Sv\tanninge", "Svanninge\n"):
            with self.subTest(prefix=bad):
                result = self.parse("--dk-name-prefix", bad)
                self.assertEqual(result.returncode, 2)
                self.assertIn("--dk-name-prefix is invalid", result.stderr)

    def test_prefix_requires_dk_v3(self):
        for rules in ("legacy", "dk-v2"):
            with self.subTest(rules=rules):
                result = self.parse("--dk-name-prefix", PREFIX, rules=rules)
                self.assertEqual(result.returncode, 2)
                self.assertIn("requires --dk-rules dk-v3", result.stderr)

    def test_prefix_is_limited_to_exact_denmark_assembly(self):
        result = self.parse("--dk-name-prefix", PREFIX, exact=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("limited to exact Denmark", result.stderr)
        result = self.parse("--dk-name-prefix", PREFIX, region="us")
        self.assertEqual(result.returncode, 2)

    def test_a_valid_prefix_passes_the_parser(self):
        result = self.parse("--dk-name-prefix", PREFIX)
        self.assertNotIn("--dk-name-prefix", result.stderr)


class GenericNames(unittest.TestCase):
    def test_generic_and_non_generic_names(self):
        for name in ("Grøn rute", "Grøn Rute", "Gul", "gul rute", "Blå Rute", "Brun rute",
                     "Lilla", "Orange", "Pink", "Lyseblå rute", "Sort", "Hvid", "Rød tur",
                     "Gul Tur 1", "Gul Rute", "Tur 2", "Rute 8", "Spor 3", "Rød sti",
                     "Syvende Spor", "Rute 14 - rød og 15 - Gul", "Blåt spor"):
            self.assertTrue(dk_names.is_generic_name(name), name)
        for name in ("Kirkestien", "Pamhule - rød rute", "Spor ved Tiset", "Svanninge Grøn rute",
                     "Kollerup Plantage Blå", "Rute", "Tur", "Sti", "Stien", "", None,
                     "Den Vilde Vej", "Rebild Bakker Ruten"):
            self.assertFalse(dk_names.is_generic_name(name), name)

    def test_apply_is_idempotent_on_already_prefixed_names(self):
        features = [{"properties": {"name": "Gul", "kind": "route", "length_mi": 1.0}}]
        dk_names.apply_name_prefix(features, PREFIX)
        again, failures = dk_names.apply_name_prefix(features, PREFIX)
        self.assertEqual(failures, [])
        self.assertEqual(features[0]["properties"]["name"], f"{PREFIX} Gul")
        self.assertEqual(features[0]["properties"]["osm_name"], "Gul")
        self.assertEqual(again["renamed"], [])


if __name__ == "__main__":
    unittest.main()
