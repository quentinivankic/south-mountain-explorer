"""Tests for the baked curation step (high+medium ship, low dropped). Gated on shapely."""
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "build"))

try:
    import shapely  # noqa: F401
    HAVE_SHAPELY = True
except ImportError:
    HAVE_SHAPELY = False

import curate as C  # noqa: E402


def _line(osm_id, coords, area_ids=None, **props):
    props["osm_id"] = osm_id
    props["area_ids"] = area_ids or []
    return {"type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": props}


def _poly(osm_id, name, rank):
    return {"type": "Feature",
            "geometry": {"type": "Polygon",
                         "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]},
            "properties": {"osm_id": osm_id, "name": name, "authority_rank": rank}}


def _fc(*feats):
    return {"type": "FeatureCollection", "features": list(feats)}


@unittest.skipUnless(HAVE_SHAPELY, "shapely not installed")
class Curate(unittest.TestCase):
    def test_keeps_high_and_medium_drops_low(self):
        trails = _fc(
            _line("named", [[0, 0], [0, 1]], has_name=True),                 # 70 high
            _line("park", [[0, 0], [0, 1]], area_ids=["P"],                   # 30+10+5 medium
                  in_official_whitelist=True, region_trust="high"),
            _line("bare", [[0, 0], [0, 1]]),                                  # 30 low
        )
        curated, _, counts = C.curate(trails, _fc(_poly("P", "Park", 35)))
        ids = {f["properties"]["osm_id"] for f in curated["features"]}
        self.assertEqual(ids, {"named", "park"})
        self.assertEqual(counts, {"high": 1, "medium": 1, "low": 1})

    def test_index_counts_only_curated_trails(self):
        # An area whose only trail is `low` drops out of the shipped index.
        trails = _fc(
            _line("t1", [[0, 0], [0, 1]], area_ids=["A"], has_name=True),     # high, in A
            _line("t2", [[0, 0], [0, 1]], area_ids=["B"]),                    # low, in B
        )
        _, index, _ = C.curate(trails, _fc(_poly("A", "Kept", 35), _poly("B", "Dropped", 35)))
        area_ids = {a["area_id"] for a in index["areas"]}
        self.assertEqual(area_ids, {"A"})            # B had only a low trail → gone
        self.assertEqual(index["areas"][0]["trail_count"], 1)

    def test_keep_override(self):
        trails = _fc(_line("bare", [[0, 0], [0, 1]]))                         # low
        curated, _, _ = C.curate(trails, _fc(), keep=("high", "medium", "low"))
        self.assertEqual(len(curated["features"]), 1)


if __name__ == "__main__":
    unittest.main()
