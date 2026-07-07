"""Tests for the on-device scoring reference (spec §4.3).

These pin the default weights to the documented heuristic so a future
weight change is a deliberate, reviewed edit. They also serve as the
conformance suite the Swift authoring-build port must match.
"""
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "build"))

import scoring_reference as sr  # noqa: E402

W = sr.load_weights(ROOT / "build" / "weights.default.json")
NOW = datetime(2026, 7, 7, tzinfo=timezone.utc)


class Scoring(unittest.TestCase):
    def test_official_maintained_trail_is_high(self):
        # authoritative_match + operator + name + whitelist + high region:
        # 50 +20 +20 +10 +10 +10 = 120 -> clamp 100 -> high.
        props = {
            "authoritative_match": True, "has_known_operator": True,
            "has_name": True, "in_official_whitelist": True,
            "region_trust": "high", "highway": "path", "sac_scale": "hiking",
        }
        s, b = sr.score_and_band(props, W, as_of=NOW)
        self.assertEqual(s, 100.0)
        self.assertEqual(b, "high")

    def test_informal_abandoned_is_low(self):
        # base 50, informal -35, abandoned -50 -> clamp 0 -> low.
        props = {"informal": "yes", "lifecycle": "abandoned", "highway": "path"}
        s, b = sr.score_and_band(props, W, as_of=NOW)
        self.assertEqual(s, 0.0)
        self.assertEqual(b, "low")

    def test_access_restricted_penalty(self):
        # base 50, access=private -40 = 10 -> low.
        props = {"access": "private", "highway": "footway"}
        s, b = sr.score_and_band(props, W, as_of=NOW)
        self.assertEqual(s, 10.0)
        self.assertEqual(b, "low")

    def test_bare_named_path_is_medium(self):
        # base 50 + has_name 10 = 60 -> medium.
        s, b = sr.score_and_band({"has_name": True, "highway": "path"}, W, as_of=NOW)
        self.assertEqual(s, 60.0)
        self.assertEqual(b, "medium")

    def test_sac_scale_threshold_at_demanding_mountain_hiking(self):
        self.assertFalse(sr.active_signals({"sac_scale": "mountain_hiking"})["sac_scale_t4_plus"])
        self.assertTrue(
            sr.active_signals({"sac_scale": "demanding_mountain_hiking"})["sac_scale_t4_plus"])
        self.assertTrue(sr.active_signals({"sac_scale": "alpine_hiking"})["sac_scale_t4_plus"])

    def test_edit_recency_fires_within_30_days(self):
        recent = (NOW - timedelta(days=5)).isoformat()
        old = (NOW - timedelta(days=200)).isoformat()
        self.assertTrue(
            sr.active_signals({"osm_timestamp": recent}, as_of=NOW)["recently_edited_or_low_trust"])
        self.assertFalse(
            sr.active_signals({"osm_timestamp": old}, as_of=NOW)["recently_edited_or_low_trust"])

    def test_low_trust_editor_fires_regardless_of_recency(self):
        old = (NOW - timedelta(days=200)).isoformat()
        self.assertTrue(sr.active_signals(
            {"osm_timestamp": old, "low_trust_editor": True},
            as_of=NOW)["recently_edited_or_low_trust"])

    def test_clamped_to_0_100(self):
        self.assertLessEqual(sr.score({"informal": "yes", "lifecycle": "abandoned",
                                       "access": "no", "trail_visibility": "no"}, W), 100)
        self.assertGreaterEqual(sr.score({}, W), 0)

    def test_string_and_bool_signals_equivalent(self):
        self.assertEqual(sr.active_signals({"informal": "yes"})["informal"],
                         sr.active_signals({"informal": True})["informal"])

    def test_band_cutoffs(self):
        self.assertEqual(sr.band(70, W), "high")
        self.assertEqual(sr.band(69.9, W), "medium")
        self.assertEqual(sr.band(40, W), "medium")
        self.assertEqual(sr.band(39.9, W), "low")


if __name__ == "__main__":
    unittest.main()
