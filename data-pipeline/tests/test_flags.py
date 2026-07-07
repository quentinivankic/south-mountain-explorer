"""Tests for QA flag derivation (spec §5) + inclusion guard (spec §7.1)."""
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "conflation"))
sys.path.insert(0, str(ROOT / "qa"))

import flags as fl  # noqa: E402
import assert_inclusion as ai  # noqa: E402


class Flags(unittest.TestCase):
    def test_phantom_candidate_inside_and_unmatched(self):
        # The safety-critical rule: unmatched way inside an official boundary.
        m = {"matched": False, "inside_official_boundary": True}
        self.assertIn("phantom_candidate", fl.flags_for_osm_way(m))

    def test_no_phantom_when_matched(self):
        m = {"matched": True, "inside_official_boundary": True}
        self.assertNotIn("phantom_candidate", fl.flags_for_osm_way(m))

    def test_no_phantom_when_outside_boundary(self):
        m = {"matched": False, "inside_official_boundary": False}
        self.assertNotIn("phantom_candidate", fl.flags_for_osm_way(m))

    def test_name_mismatch(self):
        m = {"matched": True, "osm_name": "Kepler Track", "auth_name": "Routeburn Track"}
        self.assertIn("name_mismatch", fl.flags_for_osm_way(m))

    def test_name_match_no_flag(self):
        m = {"matched": True, "osm_name": "Kepler Track", "auth_name": "kepler track"}
        self.assertNotIn("name_mismatch", fl.flags_for_osm_way(m))

    def test_operator_mismatch(self):
        m = {"matched": True, "osm_operator": "DOC", "auth_operator": "Council"}
        self.assertIn("operator_mismatch", fl.flags_for_osm_way(m))

    def test_coverage_gap(self):
        self.assertEqual(fl.flags_for_authoritative_way({"matched": False}), ["coverage_gap"])
        self.assertEqual(fl.flags_for_authoritative_way({"matched": True}), [])

    def test_summarize_counts(self):
        osm = [{"matched": False, "inside_official_boundary": True},
               {"matched": True, "osm_name": "A", "auth_name": "B"}]
        auth = [{"matched": False}, {"matched": True}]
        s = fl.summarize(osm, auth)
        self.assertEqual(s["phantom_candidate"], 1)
        self.assertEqual(s["coverage_gap"], 1)
        self.assertEqual(s["name_mismatch"], 1)
        self.assertEqual(s["matched_pairs"], 1)


class InclusionGuard(unittest.TestCase):
    def _fc(self, feats):
        return {"type": "FeatureCollection",
                "features": [{"properties": p} for p in feats]}

    def test_risky_trails_present_passes(self):
        fc = self._fc([{"informal": "yes"}, {"access": "no"},
                       {"lifecycle": "abandoned"}, {"highway": "path"}])
        stats = ai.audit(fc)
        self.assertEqual(ai.check(stats), [])
        self.assertEqual(stats["informal"], 1)
        self.assertEqual(stats["abandoned_or_disused"], 1)

    def test_zero_risky_fails_unless_allowed(self):
        fc = self._fc([{"highway": "path"}, {"highway": "footway"}])
        stats = ai.audit(fc)
        self.assertTrue(ai.check(stats, expect_risky=True))       # fails
        self.assertEqual(ai.check(stats, expect_risky=False), [])  # allowed

    def test_empty_output_fails(self):
        self.assertTrue(ai.check(ai.audit(self._fc([])), expect_risky=False))

    def test_leaked_score_field_fails(self):
        fc = self._fc([{"informal": "yes", "confidence": 90}])
        self.assertTrue(any("baked score" in f for f in ai.check(ai.audit(fc))))


if __name__ == "__main__":
    unittest.main()
