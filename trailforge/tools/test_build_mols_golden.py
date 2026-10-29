"""Offline controls for current-source Mols golden binding."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import unittest

_HERE = Path(__file__).resolve().parent
_SPEC = importlib.util.spec_from_file_location(
    "build_mols_golden_module", _HERE / "build_mols_golden.py")
golden = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(golden)


class MolsGoldenBuilder(unittest.TestCase):
    def setUp(self):
        self.template = json.loads(
            (_HERE.parent / "golden" / "mols-bjerge.json").read_text())
        self.coordinates = [[10.5, 56.2], [10.6, 56.2]]
        self.authority = {
            "source": {"sha256": "b" * 64},
            "relations": [{
                "relation_id": golden.RELATION_ID,
                "tags": {"name": golden.TRAIL_NAME},
                "members": [{
                    "sequence": 0, "type": "way", "ref": 10,
                    "role": "main",
                }],
                "direct_ways": [{
                    "way_id": 10, "status": "present",
                    "coordinates": self.coordinates,
                }],
            }],
        }
        self.trails = {"features": [{
            "type": "Feature",
            "properties": {
                "name": golden.TRAIL_NAME,
                "source": "relation",
                "relation_ids": [golden.RELATION_ID],
                "root_relation_ids": [golden.RELATION_ID],
                "source_ways": [{
                    "way_id": 10,
                    "coordinates": self.coordinates,
                }],
            },
            "geometry": {
                "type": "MultiLineString",
                "coordinates": [self.coordinates],
            },
        }]}

    def test_builds_current_preclip_and_exact_area_measurements(self):
        record = golden.build_record(
            self.template, self.authority, self.trails, "a" * 40)
        measurement = record["osm_measurement"]
        self.assertEqual(measurement["source_sha"], "a" * 40)
        self.assertEqual(measurement["raw_authority_sha256"], "b" * 64)
        self.assertGreater(measurement["preclip_miles"], 0)
        self.assertEqual(measurement["preclip_miles"],
                         measurement["exact_area_miles"])
        self.assertEqual(
            measurement["disposition"],
            "reported-for-human-review-no-numeric-pass-tolerance")
        self.assertIsNone(record["acceptance_tolerance"])

    def test_missing_relation_trail_wrong_name_and_stale_sha_fail(self):
        cases = []
        authority = copy.deepcopy(self.authority)
        authority["relations"] = []
        cases.append((authority, self.trails, "a" * 40))
        authority = copy.deepcopy(self.authority)
        authority["relations"][0]["tags"]["name"] = "Wrong"
        cases.append((authority, self.trails, "a" * 40))
        trails = copy.deepcopy(self.trails)
        trails["features"] = []
        cases.append((self.authority, trails, "a" * 40))
        cases.append((self.authority, self.trails, "short"))
        for authority, trails, source_sha in cases:
            with self.subTest(authority=authority, trails=len(trails["features"]),
                              source_sha=source_sha):
                with self.assertRaises(ValueError):
                    golden.build_record(
                        self.template, authority, trails, source_sha)


if __name__ == "__main__":
    unittest.main()
