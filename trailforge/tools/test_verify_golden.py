"""Tests for the golden-suite validator (pure stdlib)."""
import json
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verify_golden as vg  # noqa: E402


def entry(**over):
    e = {"id": "x", "name": "X", "country": "US", "kind": "destination",
         "destination": {"lat": 1.0, "lon": 2.0},
         "osm_hint": "h", "coord_confidence": "high", "why": "w"}
    e.update(over)
    return e


class Validate(unittest.TestCase):
    def test_real_golden_file_is_valid(self):
        doc = json.loads((vg.GOLDEN).read_text(encoding="utf-8"))
        self.assertEqual(vg.validate(doc), [])
        # Devils Bridge — the founding regression — must always be present.
        self.assertIn("devils-bridge", {t["id"] for t in doc["trails"]})

    def test_destination_requires_coord(self):
        doc = {"version": 1, "trails": [entry(destination={"lat": 999, "lon": 0})]}
        self.assertTrue(any("destination" in e for e in vg.validate(doc)))

    def test_through_requires_two_endpoints(self):
        doc = {"version": 1, "trails": [entry(kind="through")]}
        self.assertTrue(any("endpoints" in e for e in vg.validate(doc)))

    def test_duplicate_ids_rejected(self):
        doc = {"version": 1, "trails": [entry(), entry()]}
        self.assertTrue(any("duplicate" in e for e in vg.validate(doc)))


if __name__ == "__main__":
    unittest.main()
