"""Tests for the trail↔area spatial join. Gated on shapely."""
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

import assign_areas as A  # noqa: E402


def _poly(osm_id, name, box, rank=30):
    x0, y0, x1, y1 = box
    return {"type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [[
                [x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]},
            "properties": {"osm_id": osm_id, "name": name, "authority_rank": rank}}


def _line(osm_id, coords):
    return {"type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {"osm_id": osm_id}}


def _fc(*feats):
    return {"type": "FeatureCollection", "features": list(feats)}


@unittest.skipUnless(HAVE_SHAPELY, "shapely not installed")
class AssignAreas(unittest.TestCase):
    AREAS = _fc(_poly("A", "Fiordland NP", (0, 0, 10, 10)),
                _poly("B", "Mt Aspiring NP", (20, 20, 30, 30)))

    def _run(self, *trails):
        return A.assign(_fc(*trails), self.AREAS)

    def test_trail_inside_area_assigned(self):
        out, _ = self._run(_line("t1", [[1, 1], [2, 2]]))
        self.assertEqual(out["features"][0]["properties"]["area_ids"], ["A"])

    def test_trail_outside_all_is_ungrouped(self):
        out, _ = self._run(_line("t3", [[50, 50], [51, 51]]))
        self.assertEqual(out["features"][0]["properties"]["area_ids"], [])

    def test_trail_crossing_two_areas_assigned_to_both(self):
        out, _ = self._run(_line("t4", [[9, 9], [21, 21]]))
        self.assertEqual(set(out["features"][0]["properties"]["area_ids"]), {"A", "B"})

    def test_areas_index_counts_and_ranks_by_prevalence(self):
        _, idx = self._run(_line("t1", [[1, 1], [2, 2]]),
                           _line("t2", [[3, 3], [4, 4]]),
                           _line("t5", [[25, 25], [26, 26]]))
        # A has 2 trails, B has 1 → A first.
        self.assertEqual(idx["areas"][0]["area_id"], "A")
        self.assertEqual(idx["areas"][0]["trail_count"], 2)
        self.assertEqual(idx["areas"][1]["trail_count"], 1)

    def test_no_areas_yields_empty_index(self):
        out, idx = A.assign(_fc(_line("t1", [[1, 1], [2, 2]])), _fc())
        self.assertEqual(out["features"][0]["properties"]["area_ids"], [])
        self.assertEqual(idx["areas"], [])


if __name__ == "__main__":
    unittest.main()
