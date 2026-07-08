"""Tests for golden evaluation logic (pure, no OSM data)."""
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import golden_eval as ge  # noqa: E402


def feat(name, coords, length_mi=None):
    p = {"name": name}
    if length_mi is not None:
        p["length_mi"] = length_mi
    return {"type": "Feature", "properties": p,
            "geometry": {"type": "MultiLineString", "coordinates": [coords]}}


def fc(*fs):
    return {"type": "FeatureCollection", "features": list(fs)}


DEST = {"id": "db", "name": "Devils Bridge Trail", "kind": "destination",
        "destination": {"lon": -111.8153, "lat": 34.9008},
        "reach_tolerance_ft": 150, "expected_one_way_mi": 1.0}


class Destination(unittest.TestCase):
    def test_pass_when_trail_reaches(self):
        f = feat("Devils Bridge Trail",
                 [[-111.820, 34.899], [-111.8153, 34.9008]], length_mi=1.0)
        r = ge.evaluate(DEST, fc(f))
        self.assertTrue(r["passed"], r["reasons"])
        self.assertEqual(r["fragments"], 1)

    def test_fail_when_short(self):
        # ends ~800 ft away — the old bug
        f = feat("Devils Bridge Trail",
                 [[-111.820, 34.899], [-111.8175, 34.9006]], length_mi=0.8)
        r = ge.evaluate(DEST, fc(f))
        self.assertFalse(r["passed"])
        self.assertIn("no trail within", r["reasons"][0])

    def test_length_out_of_tolerance_fails(self):
        f = feat("Devils Bridge Trail",
                 [[-111.820, 34.899], [-111.8153, 34.9008]], length_mi=5.0)
        r = ge.evaluate(DEST, fc(f))
        self.assertFalse(r["passed"])
        self.assertTrue(any("length" in x for x in r["reasons"]))


class Through(unittest.TestCase):
    ENTRY = {"id": "whw", "name": "West Highland Way", "kind": "through",
             "endpoints": [{"lon": -4.317, "lat": 55.942},
                           {"lon": -5.105, "lat": 56.820}],
             "endpoint_tolerance_mi": 1.0}

    def test_single_trail_connecting_both_passes(self):
        f = feat("West Highland Way",
                 [[-4.317, 55.942], [-4.7, 56.3], [-5.105, 56.820]], length_mi=96)
        r = ge.evaluate(self.ENTRY, fc(f))
        self.assertTrue(r["passed"], r["reasons"])

    def test_fragmented_route_fails(self):
        # two halves, neither reaches both endpoints
        f1 = feat("WHW", [[-4.317, 55.942], [-4.7, 56.3]], length_mi=48)
        f2 = feat("WHW", [[-4.7, 56.3], [-5.105, 56.820]], length_mi=48)
        r = ge.evaluate(self.ENTRY, fc(f1, f2))
        self.assertFalse(r["passed"])


class Diagnosis(unittest.TestCase):
    def _fc(self, feats, coverage):
        return {"type": "FeatureCollection", "features": feats, "coverage": coverage}

    def test_missing_data_when_few_ways(self):
        r = ge.evaluate(DEST, self._fc([], {"raw_trailish_ways": 2,
                        "hiking_route_relations": 0, "destination_pois": 0}))
        self.assertTrue(r["diagnosis"].startswith("missing-data"))

    def test_assembly_gap_when_close_but_short(self):
        # a trail ends ~800 ft from the arch, ways are plentiful → our gap
        f = feat("Devils Bridge Trail",
                 [[-111.820, 34.899], [-111.8175, 34.9006]], length_mi=0.9)
        r = ge.evaluate(DEST, self._fc([f], {"raw_trailish_ways": 120,
                        "hiking_route_relations": 1, "destination_pois": 3}))
        self.assertFalse(r["passed"])
        self.assertTrue(r["diagnosis"].startswith("assembly-gap"), r["diagnosis"])

    def test_missing_poi(self):
        f = feat("Devils Bridge Trail",
                 [[-111.820, 34.899], [-111.8175, 34.9006]], length_mi=0.9)
        r = ge.evaluate(DEST, self._fc([f], {"raw_trailish_ways": 120,
                        "hiking_route_relations": 1, "destination_pois": 0}))
        self.assertTrue(r["diagnosis"].startswith("missing-poi"), r["diagnosis"])

    def test_through_no_relation(self):
        entry = {"id": "x", "name": "Long", "kind": "through",
                 "endpoints": [{"lon": 0, "lat": 0}, {"lon": 1, "lat": 1}]}
        r = ge.evaluate(entry, self._fc([], {"raw_trailish_ways": 300,
                        "hiking_route_relations": 0, "destination_pois": 5}))
        self.assertTrue(r["diagnosis"].startswith("no-route-relation"), r["diagnosis"])

    def test_pass_diagnosis_is_ok(self):
        f = feat("Devils Bridge Trail",
                 [[-111.820, 34.899], [-111.8153, 34.9008]], length_mi=1.0)
        r = ge.evaluate(DEST, self._fc([f], {"raw_trailish_ways": 120,
                        "hiking_route_relations": 1, "destination_pois": 3}))
        self.assertTrue(r["passed"])
        self.assertEqual(r["diagnosis"], "ok")


class RealGolden(unittest.TestCase):
    def test_summarize_runs_on_real_golden_ids(self):
        import json
        g = json.loads((Path(__file__).resolve().parents[1]
                        / "golden" / "golden.json").read_text())
        # empty FC → everything fails, but evaluate must not crash on any entry
        results = [ge.evaluate(e, {"features": []}, g["defaults"]) for e in g["trails"]]
        self.assertEqual(len(results), 20)
        self.assertIn("0/20 passed", ge.summarize(results))


if __name__ == "__main__":
    unittest.main()
