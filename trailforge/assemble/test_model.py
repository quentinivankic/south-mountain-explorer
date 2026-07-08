"""Tests for the trail-assembly algorithm (pure Python, no OSM data).

The founding case is `test_devils_bridge_*`: a named path + an unnamed
staircase spur to an arch POI must assemble into ONE trail that reaches the
arch — the exact failure Systems 1/2 shipped.
"""
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import model as m  # noqa: E402


def W(nodes, **tags):
    return {"tags": tags, "nodes": list(nodes)}


class Geometry(unittest.TestCase):
    def test_haversine_known_distance(self):
        # ~1 deg lat near equator ~= 69 mi
        self.assertAlmostEqual(m.haversine_mi((0, 0), (0, 1)), 69.0, delta=0.5)

    def test_norm_name_keeps_non_latin(self):
        self.assertEqual(m.norm_name("  Mount  Fuji "), "mount fuji")
        self.assertEqual(m.norm_name("富士山"), "富士山")

    def test_order_ways_joins_shared_endpoints(self):
        ways = {1: W([1, 2]), 2: W([2, 3]), 3: W([3, 4])}
        nodes = {1: (0, 0), 2: (0, 1), 3: (0, 2), 4: (0, 3)}
        chains = m.order_ways([3, 1, 2], ways)
        self.assertEqual(len(chains), 1)          # one connected chain
        lines = m.chains_to_multiline(chains, ways, nodes)
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0][0], (0, 0))
        self.assertEqual(lines[0][-1], (0, 3))


class RelationsFirst(unittest.TestCase):
    def test_superrelation_resolves_recursively(self):
        rels = {
            100: {"tags": {"type": "route", "route": "hiking", "name": "Big Trail"},
                  "members": [("r", 101, ""), ("r", 102, "")]},
            101: {"tags": {}, "members": [("w", 1, "")]},
            102: {"tags": {}, "members": [("w", 2, "")]},
        }
        ways = {1: W([1, 2], highway="path"), 2: W([2, 3], highway="path")}
        nodes = {1: (0, 0), 2: (0, 1), 3: (0, 2)}
        trails = m.assemble(nodes, ways, rels, [])
        self.assertEqual(len(trails), 1)
        self.assertEqual(trails[0].name, "Big Trail")
        self.assertEqual(trails[0].source, "relation")
        self.assertEqual(set(trails[0].member_ways), {1, 2})

    def test_member_roles_main_vs_approach(self):
        rels = {200: {"tags": {"type": "route", "route": "hiking", "name": "Peak Route"},
                      "members": [("w", 1, "main"), ("w", 2, "approach")]}}
        ways = {1: W([1, 2], highway="path"), 2: W([2, 3], highway="path")}
        nodes = {1: (0, 0), 2: (0, 1), 3: (0, 2)}
        t = m.assemble(nodes, ways, rels, [])[0]
        # main line is way1 only (~69 mi); approach way2 is a member but not main length
        self.assertAlmostEqual(t.length_mi, 69.0, delta=0.5)
        self.assertIn(2, t.member_ways)


class NameStitch(unittest.TestCase):
    def test_stitches_across_highway_type_boundary(self):
        # path -> steps -> path, same name, connected: ONE trail.
        ways = {1: W([1, 2], highway="path", name="Ridge Trail"),
                2: W([2, 3], highway="steps", name="Ridge Trail"),
                3: W([3, 4], highway="path", name="Ridge Trail")}
        nodes = {1: (0, 0), 2: (0, 1), 3: (0, 2), 4: (0, 3)}
        trails = m.assemble(nodes, ways, {}, [])
        self.assertEqual(len(trails), 1)
        self.assertEqual(set(trails[0].member_ways), {1, 2, 3})

    def test_same_name_disconnected_stays_separate(self):
        ways = {1: W([1, 2], highway="path", name="Ridge Trail"),
                2: W([8, 9], highway="path", name="Ridge Trail")}
        nodes = {1: (0, 0), 2: (0, 1), 8: (5, 5), 9: (5, 6)}
        self.assertEqual(len(m.assemble(nodes, ways, {}, [])), 2)


class SpurAttach(unittest.TestCase):
    def test_devils_bridge_reaches_the_arch(self):
        # Named path ends at n3; an UNNAMED steps spur n3->n4 climbs to the
        # arch POI at n4. The two must become one trail reaching the arch.
        arch = (-111.8153, 34.9008)
        ways = {
            1: W([1, 2, 3], highway="path", name="Devils Bridge Trail"),
            2: W([3, 4], highway="steps"),          # the missing staircase
        }
        nodes = {1: (-111.820, 34.899), 2: (-111.818, 34.900),
                 3: (-111.817, 34.9005), 4: arch}
        pois = [{"id": 9, "coord": arch, "tags": {"natural": "arch"},
                 "name": "Devils Bridge"}]
        trails = m.assemble(nodes, ways, {}, pois)
        self.assertEqual(len(trails), 1)
        t = trails[0]
        self.assertEqual(t.name, "Devils Bridge Trail")
        self.assertIn(2, t.member_ways)             # steps welded on
        self.assertEqual(t.destinations, ["Devils Bridge"])
        # geometry actually reaches the arch node
        reached = min(m.haversine_mi(p, arch) for line in t.lines for p in line)
        self.assertLess(reached * 5280, 5)          # within 5 ft

    def test_spur_without_poi_is_not_welded(self):
        ways = {1: W([1, 2, 3], highway="path", name="Some Trail"),
                2: W([3, 4], highway="steps")}       # goes nowhere special
        nodes = {1: (0, 0), 2: (0, 0.001), 3: (0, 0.002), 4: (0, 0.003)}
        trails = m.assemble(nodes, ways, {}, [])      # no POIs
        named = [t for t in trails if t.name == "Some Trail"][0]
        self.assertNotIn(2, named.member_ways)        # unnamed steps not welded

    def test_long_way_not_welded_as_spur(self):
        # An unnamed ~0.7 mi way (> SPUR_MAX_MI) reaching a POI is a trail
        # in its own right, not a payoff spur to be swallowed.
        poi = (-111.8046, 34.9005)
        ways = {1: W([1, 2], highway="path", name="Main"),
                2: W([2, 3], highway="path")}
        nodes = {1: (-111.85, 34.90), 2: (-111.817, 34.9005), 3: poi}
        pois = [{"id": 9, "coord": poi, "tags": {"natural": "arch"}, "name": "X"}]
        t = [t for t in m.assemble(nodes, ways, {}, pois) if t.name == "Main"][0]
        self.assertNotIn(2, t.member_ways)            # too long to be a payoff spur


class Classification(unittest.TestCase):
    def test_sidewalk_and_indoor_excluded(self):
        self.assertFalse(m._is_trailish({"highway": "footway", "footway": "sidewalk"}))
        self.assertFalse(m._is_trailish({"highway": "path", "indoor": "yes"}))
        self.assertFalse(m._is_trailish({"highway": "path", "trail": "no"}))
        self.assertTrue(m._is_trailish({"highway": "steps"}))
        self.assertTrue(m._is_trailish({"highway": "via_ferrata"}))


if __name__ == "__main__":
    unittest.main()
