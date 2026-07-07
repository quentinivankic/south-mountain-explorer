"""Tests for attribution generation (spec §2, §8 Attribution UI)."""
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sources"))
sys.path.insert(0, str(ROOT / "build"))

import validate_registry as vr  # noqa: E402
import attributions as at  # noqa: E402

REGISTRY = vr.load_registry(ROOT / "sources" / "registry.json")


class Attribution(unittest.TestCase):
    def test_osm_always_present(self):
        block = at.build_attribution(REGISTRY, ["nz_doc"], ["NZ"])
        self.assertIn(at.ALWAYS, block["attribution"])
        self.assertEqual(block["attribution"][0], at.ALWAYS)  # first

    def test_nz_pilot_strings(self):
        block = at.build_attribution(REGISTRY, ["osm", "nz_doc", "nz_linz"], ["NZ"])
        joined = " | ".join(block["attribution"])
        self.assertIn("OpenStreetMap", joined)
        self.assertIn("Department of Conservation", joined)
        self.assertIn("LINZ", joined)

    def test_country_override_triggers_only_when_present(self):
        # CDDA has an EE override; it should appear only if EE is in-region.
        with_ee = at.build_attribution(REGISTRY, ["cdda"], ["EE", "FR"])
        without_ee = at.build_attribution(REGISTRY, ["cdda"], ["FR"])
        self.assertTrue(any("Estonian" in s for s in with_ee["attribution"]))
        self.assertFalse(any("Estonian" in s for s in without_ee["attribution"]))

    def test_non_shippable_source_raises(self):
        # SERNANP failed the gate; it must never appear in attribution.
        with self.assertRaises(ValueError):
            at.build_attribution(REGISTRY, ["pe_sernanp"], ["PE"])

    def test_unknown_source_raises(self):
        with self.assertRaises(ValueError):
            at.build_attribution(REGISTRY, ["nope"], [])

    def test_no_duplicate_lines(self):
        block = at.build_attribution(REGISTRY, ["osm", "osm", "nz_doc"], ["NZ"])
        self.assertEqual(len(block["attribution"]), len(set(block["attribution"])))


if __name__ == "__main__":
    unittest.main()
