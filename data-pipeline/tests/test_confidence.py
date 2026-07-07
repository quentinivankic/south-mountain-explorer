"""Tests for Bucket B flag emission (spec §4.2) + no-baked-score guard."""
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "build"))

import confidence as cf  # noqa: E402


def feat(osm_id, **props):
    props["osm_id"] = osm_id
    return {"type": "Feature", "geometry": {"type": "LineString", "coordinates": []},
            "properties": props}


class BucketB(unittest.TestCase):
    def test_matched_way_gets_source_and_whitelist(self):
        matches = {"1": {"matched": True, "source": "doc", "whitelist": True}}
        out = cf.apply_bucket_b(feat("1"), matches=matches, region_trust="high")
        p = out["properties"]
        self.assertTrue(p["authoritative_match"])
        self.assertEqual(p["matched_source"], "doc")
        self.assertTrue(p["in_official_whitelist"])
        self.assertEqual(p["region_trust"], "high")

    def test_unmatched_way_has_null_source(self):
        out = cf.apply_bucket_b(feat("2"), matches={}, region_trust="medium")
        p = out["properties"]
        self.assertFalse(p["authoritative_match"])
        self.assertIsNone(p["matched_source"])

    def test_low_trust_editor_flag(self):
        out = cf.apply_bucket_b(feat("3"), matches={}, region_trust="low",
                                low_trust_osm_ids={"3"})
        self.assertTrue(out["properties"]["low_trust_editor"])

    def test_leaked_score_field_is_stripped(self):
        # Defense in depth: a stray baked score must never reach the tiles.
        out = cf.apply_bucket_b(feat("4", confidence=88, band="high"),
                                matches={}, region_trust="high")
        for k in ("confidence", "band", "score"):
            self.assertNotIn(k, out["properties"])

    def test_bucket_a_signals_preserved(self):
        out = cf.apply_bucket_b(feat("5", informal="yes", sac_scale="alpine_hiking",
                                     access="private"),
                                matches={}, region_trust="high")
        p = out["properties"]
        self.assertEqual(p["informal"], "yes")
        self.assertEqual(p["sac_scale"], "alpine_hiking")
        self.assertEqual(p["access"], "private")

    def test_collection_roundtrip(self):
        fc = {"type": "FeatureCollection", "features": [feat("1"), feat("2")]}
        out = cf.apply_to_collection(fc, matches={"1": {"matched": True, "source": "doc"}},
                                     region_trust="high")
        self.assertEqual(len(out["features"]), 2)
        self.assertTrue(out["features"][0]["properties"]["authoritative_match"])


if __name__ == "__main__":
    unittest.main()
