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

    def test_umbrella_route_keeps_local_named_members(self):
        # A route relation whose member has its OWN name must not erase that
        # trail — the Maricopa Trail absorbing "Bursera Canyon" bug.
        rels = {1: {"tags": {"type": "route", "route": "hiking", "name": "Maricopa Trail"},
                    "members": [("w", 10, ""), ("w", 11, "")]}}
        ways = {10: W([1, 2], highway="path", name="Maricopa Trail"),
                11: W([2, 3], highway="path", name="Bursera Canyon")}
        nodes = {1: (0, 0), 2: (0, 1), 3: (0, 2)}
        names = sorted(t.name for t in m.assemble(nodes, ways, rels, []))
        self.assertIn("Bursera Canyon", names)   # local trail preserved as itself
        self.assertIn("Maricopa Trail", names)   # umbrella route still emitted

    def test_relation_excludes_road_like_track_member(self):
        # A vehicle/access-road track that's a route member isn't drawn as trail.
        rels = {1: {"tags": {"type": "route", "route": "hiking", "name": "Loop"},
                    "members": [("w", 10, ""), ("w", 11, "")]}}
        ways = {10: W([1, 2], highway="path"),
                11: W([2, 3], highway="track", motor_vehicle="yes", name="Service Road")}
        nodes = {1: (0, 0), 2: (0, 1), 3: (0, 2)}
        t = [x for x in m.assemble(nodes, ways, rels, []) if x.source == "relation"][0]
        self.assertNotIn(11, t.member_ways)

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

    def test_same_name_connected_merge_disconnected_split(self):
        # Connectivity is the join key. Two CONNECTED "Ridge Trail" ways stitch
        # into one object; two DISCONNECTED ones stay separate (no blob).
        ways = {1: W([1, 2], highway="path", name="Ridge Trail"),
                2: W([2, 3], highway="path", name="Ridge Trail")}     # touch at node 2
        nodes = {1: (0, 0), 2: (0, 1), 3: (0, 2)}
        self.assertEqual(len(m.assemble(nodes, ways, {}, [])), 1)
        ways2 = {1: W([1, 2], highway="path", name="Ridge Trail"),
                 2: W([8, 9], highway="path", name="Ridge Trail")}    # far apart
        nodes2 = {1: (0, 0), 2: (0, 1), 8: (5, 5), 9: (5, 6)}
        self.assertEqual(len(m.assemble(nodes2, ways2, {}, [])), 2)

    def test_merge_key_folds_trail_suffix_and_hyphens(self):
        self.assertEqual(m.merge_key("Alta"), m.merge_key("Alta Trail"))
        self.assertEqual(m.merge_key("Ma-Ha-Tuak"), m.merge_key("Ma Ha Tuak"))
        self.assertEqual(m.merge_key("Desert Classic"), m.merge_key("Desert Classic Trail"))
        # distinct trails stay distinct
        self.assertNotEqual(m.merge_key("Alta"), m.merge_key("West Alta"))
        self.assertNotEqual(m.merge_key("Mormon Trail"), m.merge_key("Mormon Loop Trail"))

    def test_alta_variant_names_merge(self):
        # "Alta" + "Alta Trail" fold to one merge_key AND touch (node 2), so
        # they fuse; "West Alta" is a distinct name and stays separate.
        ways = {1: W([1, 2], highway="path", name="Alta"),
                2: W([2, 3], highway="path", name="Alta Trail"),   # touches Alta at node 2
                3: W([20, 21], highway="path", name="West Alta")}
        nodes = {1: (0, 0), 2: (0, 1), 3: (0, 2), 20: (9, 9), 21: (9, 10)}
        names = sorted(t.name for t in m.assemble(nodes, ways, {}, []))
        self.assertEqual(len(names), 2)             # Alta(+Trail) merged, West Alta separate
        self.assertIn("West Alta", names)

    def test_closed_named_trail_dropped(self):
        ways = {1: W([1, 2], highway="path", name="CLOSED - old Pyramid Trail"),
                2: W([3, 4], highway="path", name="Pyramid Trail")}
        nodes = {1: (0, 0), 2: (0, 1), 3: (0, 2), 4: (0, 3)}
        names = {t.name for t in m.assemble(nodes, ways, {}, [])}
        self.assertNotIn("CLOSED - old Pyramid Trail", names)
        self.assertIn("Pyramid Trail", names)

    def test_min_length_drops_short_trails(self):
        ways = {1: W([1, 2], highway="path", name="Stub"),        # ~tiny
                2: W([3, 4], highway="path", name="Long Trail")}  # ~1 deg lat = 69mi
        nodes = {1: (0, 0), 2: (0, 0.0003), 3: (0, 0), 4: (0, 1)}
        names = {t.name for t in m.assemble(nodes, ways, {}, [], min_length_mi=0.1)}
        self.assertNotIn("Stub", names)
        self.assertIn("Long Trail", names)
        # with no threshold, the stub survives
        self.assertIn("Stub", {t.name for t in m.assemble(nodes, ways, {}, [])})

    def test_generic_named_trails_dropped_unless_relation(self):
        # A standalone way named just "Trail" has no identity -> dropped.
        # A same-generic-named ROUTE RELATION is spared (human curation).
        rels = {1: {"tags": {"type": "route", "route": "hiking", "name": "Trail"},
                    "members": [("w", 30, "")]}}
        ways = {10: W([1, 2], highway="path", name="Trail"),            # generic standalone
                20: W([3, 4], highway="path", name="Nature Trail"),     # generic standalone
                30: W([5, 6], highway="path"),                          # relation member
                40: W([7, 8], highway="path", name="Desert Classic Trail")}  # real name
        nodes = {1: (0, 0), 2: (0, 1), 3: (2, 0), 4: (2, 1),
                 5: (4, 0), 6: (4, 1), 7: (6, 0), 8: (6, 1)}
        names = {t.name for t in m.assemble(nodes, ways, rels, [])}
        self.assertNotIn("Nature Trail", names)          # generic standalone dropped
        self.assertIn("Desert Classic Trail", names)     # real name kept
        self.assertIn("Trail", names)                    # generic BUT from a relation -> kept

    def test_relation_and_standalone_same_name_merge(self):
        # National Trail: some ways in the route relation, some standalone —
        # the standalone CONNECTS to the relation (shares node 2), so they fuse.
        rels = {1: {"tags": {"type": "route", "route": "hiking", "name": "National Trail"},
                    "members": [("w", 10, "")]}}
        ways = {10: W([1, 2], highway="path", name="National Trail"),   # relation member
                20: W([2, 3], highway="path", name="National Trail")}   # standalone, touches
        nodes = {1: (0, 0), 2: (0, 1), 3: (0, 2)}
        nat = [t for t in m.assemble(nodes, ways, rels, []) if t.name == "National Trail"]
        self.assertEqual(len(nat), 1)               # one National Trail, not two
        self.assertEqual(nat[0].source, "relation")  # relation metadata wins
        self.assertEqual(set(nat[0].member_ways), {10, 20})

    # --- spread-gated, area-scoped merge (SPEC §6b) ---
    _AREAS = [
        {"name": "ParkA", "bbox": (0, 0, 2, 2),
         "rings": [[(0, 0), (2, 0), (2, 2), (0, 2), (0, 0)]]},
        {"name": "ParkB", "bbox": (10, 10, 12, 12),
         "rings": [[(10, 10), (12, 10), (12, 12), (10, 12), (10, 10)]]},
    ]

    def test_compact_disconnected_same_name_fuse(self):
        # The Pima Canyon Loop case: a small loop whose same-named pieces are
        # split by short shared-tread segments -> ONE object. Spread is well
        # under the cap, so the group fuses even without a shared vertex.
        a = m.Trail("Loop Trail", "name-stitch", [1], [[(33.360, -111.980), (33.365, -111.975)]], {}, [])
        b = m.Trail("Loop Trail", "name-stitch", [2], [[(33.370, -111.970), (33.372, -111.965)]], {}, [])
        self.assertEqual(len(m.merge_same_name([a, b])), 1)

    def test_sprawling_same_name_splits_into_components(self):
        # The Bonneville Shoreline case: same name spread far past the cap ->
        # split into connected components, NOT one scattered blob.
        a = m.Trail("Trail", "name-stitch", [1], [[(0, 0), (1, 0)]], {}, [])
        b = m.Trail("Trail", "name-stitch", [2], [[(9, 9), (10, 9)]], {}, [])  # ~800 mi away
        self.assertEqual(len(m.merge_same_name([a, b])), 2)

    def test_sprawling_but_connected_stays_one(self):
        # A long trail spanning past the cap but genuinely connected end-to-end
        # is ONE component -> stays one object.
        a = m.Trail("Ridge Trail", "name-stitch", [1], [[(0, 0), (1, 0)]], {}, [])
        b = m.Trail("Ridge Trail", "name-stitch", [2], [[(1, 0), (2, 0)]], {}, [])  # touches a
        self.assertEqual(len(m.merge_same_name([a, b])), 1)

    def test_area_scoping_keeps_nearby_parks_separate(self):
        # Two compact same-name trails straddling adjacent parks: spread is under
        # the cap so without scoping they'd wrongly fuse; area_of keeps them apart.
        areas = [{"name": "ParkA", "bbox": (0, 0, 1, 1),
                  "rings": [[(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)]]},
                 {"name": "ParkB", "bbox": (1, 0, 2, 1),
                  "rings": [[(1, 0), (2, 0), (2, 1), (1, 1), (1, 0)]]}]
        area_of = m.make_area_of(areas)
        a = m.Trail("Ridge Trail", "name-stitch", [1], [[(0.90, 0.5), (0.95, 0.5)]], {}, [])  # ParkA
        b = m.Trail("Ridge Trail", "name-stitch", [2], [[(1.05, 0.5), (1.10, 0.5)]], {}, [])  # ParkB
        self.assertEqual(len(m.merge_same_name([a, b], area_of=area_of)), 2)

    def test_different_names_never_fuse(self):
        a = m.Trail("Trail", "name-stitch", [4], [[(0.0, 0.0), (0.01, 0.01)]], {}, [])
        y = m.Trail("Other Trail", "name-stitch", [5], [[(0.01, 0.01), (0.02, 0.02)]], {}, [])
        self.assertEqual(len(m.merge_same_name([a, y])), 2)

    def test_relation_gap_preserved_and_connected_standalone_merges(self):
        # National Trail: a route relation with a real internal gap + a
        # standalone same-name piece that TOUCHES one relation part -> one
        # object, the relation's internal gap kept.
        rel = m.Trail("National Trail", "relation", [1, 2],
                      [[(1, 1), (2, 1)], [(8, 1), (9, 1)]], {"network": "lwn"}, [])  # gap
        standalone = m.Trail("National Trail", "name-stitch", [3],
                             [[(2, 1), (5, 1)]], {}, [])   # shares (2,1) with rel part 1
        out = m.merge_same_name([rel, standalone])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].source, "relation")   # relation wins
        self.assertEqual(len(out[0].lines), 3)         # relation's 2 parts (gap kept) + standalone

    def test_area_of_assigns_park_and_backcountry_none(self):
        area_of = m.make_area_of(self._AREAS)
        inside = m.Trail("X", "name-stitch", [1], [[(0.5, 0.5), (1, 1)]], {}, [])
        outside = m.Trail("Y", "name-stitch", [2], [[(5, 5), (6, 6)]], {}, [])
        self.assertEqual(area_of(inside), "ParkA")
        self.assertIsNone(area_of(outside))


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


class Coverage(unittest.TestCase):
    def test_coverage_stats_counts_by_kind(self):
        ways = {1: W([1, 2], highway="path", name="A"),
                2: W([2, 3], highway="steps"),
                3: W([3, 4], highway="footway", footway="sidewalk"),  # excluded
                4: W([4, 5], highway="residential")}                  # not trailish
        rels = {10: {"tags": {"type": "route", "route": "hiking"}, "members": []},
                11: {"tags": {"type": "route", "route": "bicycle"}, "members": []}}
        pois = [{"id": 9, "coord": (0, 0), "tags": {"natural": "arch"}, "name": "X"}]
        c = m.coverage_stats(ways, rels, pois)
        self.assertEqual(c["raw_trailish_ways"], 2)       # path + steps
        self.assertEqual(c["named_trailish_ways"], 1)     # only "A"
        self.assertEqual(c["hiking_route_relations"], 1)  # bicycle excluded
        self.assertEqual(c["route_relations_total"], 2)
        self.assertEqual(c["destination_pois"], 1)


class Classification(unittest.TestCase):
    def test_sidewalk_and_indoor_excluded(self):
        self.assertFalse(m._is_trailish({"highway": "footway", "footway": "sidewalk"}))
        self.assertFalse(m._is_trailish({"highway": "path", "indoor": "yes"}))
        self.assertFalse(m._is_trailish({"highway": "path", "trail": "no"}))
        self.assertTrue(m._is_trailish({"highway": "steps"}))
        self.assertTrue(m._is_trailish({"highway": "via_ferrata"}))

    def test_road_like_track_demoted(self):
        # access/utility roads tagged as track are not trails (the parking road).
        self.assertFalse(m._is_trailish({"highway": "track", "motor_vehicle": "yes"}))
        self.assertFalse(m._is_trailish({"highway": "track", "name": "Dobbins Lookout Road"}))
        self.assertFalse(m._is_trailish({"highway": "track", "name": "Irrigation Canal"}))
        # a 2+-lane track is a drivable road (the sand road named after the park).
        self.assertFalse(m._is_trailish({"highway": "track", "lanes": "2",
                                         "name": "Phoenix South Mountain Park"}))
        # numeric grid-address road names (Utah/AZ grids) are roads, not trails.
        self.assertFalse(m._is_trailish({"highway": "track", "name": "3900 East"}))
        self.assertFalse(m._is_trailish({"highway": "track", "name": "400 South"}))
        self.assertFalse(m._is_trailish({"highway": "track", "name": "N 400 W"}))
        # Forest Service / BLM / agency road codes — dirt vehicle roads.
        for n in ("NF-418C", "NF-761", "BLM 1048", "FR 236", "FS 6005", "Fr 301",
                  "NV-9040V", "N9234", "H1290"):   # incl. unfamiliar prefixes (3+ digits)
            self.assertFalse(m._is_trailish({"highway": "track", "name": n}), n)
        # ...but short numbered TRAIL codes (1-2 digits) are NOT road codes.
        self.assertTrue(m._is_trailish({"highway": "track", "name": "GR 20"}), "GR20")
        self.assertTrue(m._is_trailish({"highway": "path", "name": "E5"}), "E5")
        # rural grid-street / lane / place names tagged as track.
        for n in ("7th Street", "54th Street North", "Holley Lane",
                  "Middle Place", "Easy Street", "West Orchard Lane"):
            self.assertFalse(m._is_trailish({"highway": "track", "name": n}), n)
        # ...but a numbered *trail* (no grid direction pattern) stays.
        self.assertTrue(m._is_trailish({"highway": "track", "name": "Trail 100"}))
        self.assertTrue(m._is_trailish({"highway": "path", "name": "East Rim Trail"}))
        # legit trails that merely CONTAIN a road-ish word stay: path-typed, or
        # word-boundary spares them (relation-named trails keep their names too).
        self.assertTrue(m._is_trailish({"highway": "path", "name": "Old Country Road Trail"}))
        self.assertTrue(m._is_trailish({"highway": "path", "name": "Prescott Circle Trail"}))
        self.assertTrue(m._is_trailish({"highway": "footway", "name": "Sun Circle Trail"}))
        self.assertTrue(m._is_trailish({"highway": "track", "name": "Broadway Trail"}))  # not "road"
        self.assertTrue(m._is_trailish({"highway": "track", "name": "Creek 5 Loop"}))    # not "CR 5"
        # a plain named track (a real trail) stays.
        self.assertTrue(m._is_trailish({"highway": "track", "name": "Desert Classic Trail"}))
        self.assertTrue(m._is_trailish({"highway": "track"}))

    def test_road_code_name_dropped_regardless_of_highway(self):
        # forest-road / OSM-ref codes that ride on path/footway or route
        # relations (so the track-scoped filter never sees them) — from the
        # AZ statewide diff (Tonto/Kaibab/Apache-Sitgreaves).
        for n in ("[FR 1098]", "[FR 374] **4WD**", "[FS 9601A]  **4WD**",
                  ";NF-246C", "NF-D1857", "NF-W17", "MT-2026 - FDR 2026",
                  "U2259", "U72B", "U2271A", "PST012", "212E", "300V1",
                  "8170D", "237B OHV", "09149T", "#744", "933b", "F R 8080",
                  "FR8375A", "Forest Rt 85", "Forest Service Road 420",
                  "T4417", "U S F 3347", "8080"):
            self.assertTrue(m.is_road_code_name(n), n)
        # real trails that merely carry a number are KEPT (digit-gated + word-aware)
        for n in ("Aerie #168", "Calloway Trail 33", "See Canyon Trail #184",
                  "Little Saddle Mountain Trail #244", "Trail #2090",
                  "32nd St Connector", "35th Ave Access Trail", "Alta Trail",
                  "National Trail", "DC-Ray Connector", "Pure O"):
            self.assertFalse(m.is_road_code_name(n), n)

    def test_offtrail_name_drops_worded_agency_roads_and_features(self):
        # from the Idaho/Washington audit — worded agency roads is_road_code_name
        # lets through, plus freeway ramps / airport concourses / parking lots.
        for n in ("National Forest Development Road 005",
                  "National Forest Development Road 113 Trail",
                  "Caribou National Forest Road 155", "National Forest Development 626 Road",
                  "Natl Forrest Develop Rd 2798-A", "Forest Service Road 420",
                  "East Fsr 1562A", "Conjector Mine Rd FS-1017", "NF-65 (abandoned)",
                  "IDL 43D", "Bia 37", "Bureau of Indian Affairs Road 115",
                  "Ramp 23", "Soundside Ramp 52", "Lower Off-Ramp",
                  "Concourse A (Main Access Road)", "Proposed Parking lot"):
            self.assertTrue(m.is_offtrail_name(n), n)
        # false alarms — real trails that must survive (bare road names are kept
        # on purpose per the audit decision).
        for n in ("Alligator Road", "Fire Road", "Service Road", "Grassy Gap Fire Road",
                  "Yellow Brick Road", "Thunder Road", "Old Cole Mill Rd Trail",
                  "Roanoke Canal Trail", "Historic Columbia River Highway State Trail",
                  "Golden Gate Trail", "Ramparts Trail", "Parking Lot Connector Trail",
                  "Driveway Butte Trail"):
            self.assertFalse(m.is_offtrail_name(n), n)

    def test_motorized_ways_are_not_trailish(self):
        # ATV/OHV/4WD/snowmobile designations => drop (tag-based, so a foot-only
        # path merely NAMED 'Jeep Trail' with no such tag is kept).
        self.assertFalse(m._is_trailish({"highway": "track", "atv": "yes"}))
        self.assertFalse(m._is_trailish({"highway": "path", "ohv": "yes"}))
        self.assertFalse(m._is_trailish({"highway": "track", "4wd_only": "yes"}))
        self.assertFalse(m._is_trailish({"highway": "path", "snowmobile": "designated"}))
        self.assertFalse(m._is_trailish({"highway": "path", "motor_vehicle": "designated"}))
        # foot-only path (no motor tag) stays trailish at the WAY level; the
        # name-based motorized filter runs later in curation.
        self.assertTrue(m._is_trailish({"highway": "path", "name": "Huckleberry Trail"}))

    def test_motorized_name_dropped_in_curation(self):
        # US mappers name these without atv/ohv tags, so the NAME filter catches
        # them (from the Idaho audit — all genuine vehicle routes).
        for n in ("Basalt Jeep Trail", "Pine Creek South ATV Trail",
                  "Bobtail Spur OHV Trail", "George Gulch 4WD Trail",
                  "Eccles Road Snowmobile Trail", "Deer Creek Trail (OHV Section) #158",
                  "Lewis and Clark Jeep Trail", "Jeep Trail", "ATV TR 1550"):
            self.assertTrue(m.is_motorized_name(n), n)
        # normal hiking names untouched
        for n in ("Deer Creek Trail", "Huckleberry Trail", "Angels Landing Trail",
                  "West Rim Trail", "Casner Canyon Trail"):
            self.assertFalse(m.is_motorized_name(n), n)

    def test_removal_reason_names_the_rule_that_fired(self):
        # QA viewer relies on removal_reason mirroring curation's predicate
        # order; each removed trail must carry a non-empty plain-language reason.
        line = [(0.0, 0.0), (0.1, 0.0)]  # ~6.9 mi, well over any min-length
        cases = {
            "CLOSED - Old Pyramid Trail": "closed",
            "FR 231": "code",
            "Forest Service Road 420": "road",
            "Basalt Jeep Trail": "motor",
            "Trail": "generic",
        }
        for name, needle in cases.items():
            t = m.Trail(name, "name-stitch", [1], [list(line)], {}, [])
            reason = m.removal_reason(t)
            self.assertIsNotNone(reason, name)
            self.assertIn(needle, reason.lower(), (name, reason))
        # a real named trail is kept (reason is None)
        keep = m.Trail("Angels Landing Trail", "name-stitch", [1], [list(line)], {}, [])
        self.assertIsNone(m.removal_reason(keep))
        # a route relation spares an otherwise-generic name
        rel = m.Trail("Trail", "relation", [1], [list(line)], {}, [])
        self.assertIsNone(m.removal_reason(rel))

    def test_min_length_removal_reason(self):
        stub = m.Trail("Tiny Connector Path", "name-stitch", [1],
                       [[(0.0, 0.0), (0.0001, 0.0)]], {}, [])  # ~40 ft
        reason = m.removal_reason(stub, min_length_mi=0.1)
        self.assertIsNotNone(reason)
        self.assertIn("short", reason.lower())

    def test_grid_address_name_dropped(self):
        # Utah section-line grid — rural farm-road lattice, never a trail.
        for n in ("North 3325 West", "West 6000 North", "North 4000 West",
                  "South 100 East", "N 3600 W", "west 600 north"):
            self.assertTrue(m.is_grid_address_name(n), n)
        # real trails and near-miss names survive
        for n in ("Bonneville Shoreline Trail", "North Rim Trail",
                  "West Fork Trail", "North 40", "Highway 6", "3600 West Trail"):
            self.assertFalse(m.is_grid_address_name(n), n)

    def test_dedupe_ref_vs_name_duplicate(self):
        # a route relation and a name-stitch over the SAME ways under names
        # that normalize differently — the Casner Canyon #11 == Casner Canyon
        # Trail case. Geometry (shared ways) matches -> keep the worded name.
        line = [(-111.703, 34.889), (-111.718, 34.891), (-111.733, 34.893)]
        a = m.Trail("Casner Canyon Trail", "relation", [10, 11, 12], [list(line)], {}, [])
        b = m.Trail("Casner Canyon #11", "name-stitch", [10, 11, 12], [list(line)], {}, [])
        a.area = b.area = "Coconino National Forest"
        out = m.dedupe_duplicate_trails([a, b])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].name, "Casner Canyon Trail")

    def test_dedupe_matches_identical_geometry_without_shared_ways(self):
        # duplicate mappings (different way ids, same traced path) still fold
        line = [(-111.703, 34.889), (-111.733, 34.893)]
        a = m.Trail("Foo Trail", "relation", [30], [list(line)], {}, [])
        b = m.Trail("Foo #7", "name-stitch", [99], [list(line)], {}, [])
        a.area = b.area = "X"
        out = m.dedupe_duplicate_trails([a, b])
        self.assertEqual([t.name for t in out], ["Foo Trail"])

    def test_dedupe_keeps_distinct_trails_sharing_a_base_name(self):
        # same base name, DIFFERENT geometry (different canyon segments) ->
        # both survive; the ref is not a merge signal, geometry is.
        c = m.Trail("Bear Canyon #29", "name-stitch", [20],
                    [[(-110.0, 32.0), (-110.01, 32.01)]], {}, [])
        d = m.Trail("Bear Canyon #31", "name-stitch", [21],
                    [[(-110.5, 32.5), (-110.51, 32.51)]], {}, [])
        c.area = d.area = "Coronado National Forest"
        self.assertEqual(len(m.dedupe_duplicate_trails([c, d])), 2)

    def test_dedupe_keeps_the_descriptive_name_not_the_generic_one(self):
        # a duplicate pair must keep the DISTINCTIVE name, never a bare
        # 'Trail <n>' — and must prefer fuller spelling over a typo/abbrev.
        line = [(-112.45, 34.55), (-112.46, 34.56), (-112.47, 34.57)]
        for good, bad in [("Granite Mountain Trail #261", "Trail 261"),
                          ("Wilson Mountain Spur B #10B", "Willson Mtn Spur B"),
                          ("Raspberry Ridge Trail #228", "Rasberry Ridge Trail #228")]:
            a = m.Trail(good, "name-stitch", [1], [list(line)], {}, [])
            b = m.Trail(bad, "relation", [1], [list(line)], {}, [])
            a.area = b.area = "Prescott National Forest"
            out = m.dedupe_duplicate_trails([a, b])
            self.assertEqual([t.name for t in out], [good], f"{good!r} vs {bad!r}")

    def test_dedupe_keeps_distinct_trails_sharing_endpoints(self):
        # two different routes between the same trailhead and peak: SAME
        # endpoints, similar length, DIFFERENT path between -> both survive.
        # Endpoint coincidence alone must not trigger a merge (the over-removal
        # guard); only near-total coordinate overlap counts.
        a = m.Trail("Cathedral Rock Trail", "relation", [1],
                    [[(-111.80, 34.82), (-111.81, 34.83), (-111.82, 34.84)]], {}, [])
        b = m.Trail("Templeton Trail", "name-stitch", [2],
                    [[(-111.80, 34.82), (-111.79, 34.83), (-111.82, 34.84)]], {}, [])
        a.area = b.area = "Coconino National Forest"
        self.assertEqual(len(m.dedupe_duplicate_trails([a, b])), 2)

    def test_dedupe_is_area_scoped(self):
        # identical name+geometry in DIFFERENT areas are not each other's dupes
        line = [(-111.703, 34.889), (-111.733, 34.893)]
        a = m.Trail("Ridge Trail", "relation", [1], [list(line)], {}, [])
        b = m.Trail("Ridge Trail", "relation", [1], [list(line)], {}, [])
        a.area, b.area = "Park A", "Park B"
        self.assertEqual(len(m.dedupe_duplicate_trails([a, b])), 2)

    def test_promote_hike_renames_local_route_from_destination(self):
        # a local route ending at a named destination POI -> canonical hike
        t = m.Trail("Angels Landing Trail--West Rim Trail", "relation", [1],
                    [[(0.0, 0.0), (0.001, 0.001)]], {}, [])
        pois = [{"name": "Angels Landing", "coord": (0.001, 0.001),
                 "tags": {"natural": "peak"}}]
        m.promote_hikes([t], pois)
        self.assertTrue(t.hike)
        self.assertEqual(t.name, "Angels Landing Trail")
        self.assertEqual(t.to_feature()["properties"]["kind"], "hike")

    def test_promote_hike_absorbs_covered_fragment(self):
        # the composite route promotes to the hike; the shorter same-named
        # physical spur (kind=trail) is absorbed so it isn't listed twice.
        hike = m.Trail("Angels Landing Trail--West Rim Trail", "relation", [1],
                       [[(0.0, 0.0), (0.001, 0.001)]], {}, [])
        spur = m.Trail("Angels Landing Trail", "name-stitch", [2],
                       [[(0.0008, 0.0008), (0.001, 0.001)]], {}, [])
        pois = [{"name": "Angels Landing", "coord": (0.001, 0.001),
                 "tags": {"natural": "peak"}}]
        out = m.promote_hikes([hike, spur], pois)
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0].hike)
        self.assertEqual(out[0].name, "Angels Landing Trail")

    def test_promote_hike_skips_thru_routes_and_plain_trails(self):
        poi = [{"name": "Peak", "coord": (0.001, 0.001), "tags": {"natural": "peak"}}]
        line = [[(0.0, 0.0), (0.001, 0.001)]]
        # a regional thru-route reaching the POI is NOT one hike -> stays a route
        thru = m.Trail("Hayduke Trail #13", "relation", [1], line, {"network": "rwn"}, [])
        # a plain named trail reaching the POI is a trail, not promoted
        trail = m.Trail("Summit Trail", "name-stitch", [2], line, {}, [])
        m.promote_hikes([thru, trail], poi)
        self.assertFalse(thru.hike)
        self.assertFalse(trail.hike)
        self.assertEqual(thru.to_feature()["properties"]["kind"], "route")
        self.assertEqual(trail.to_feature()["properties"]["kind"], "trail")

    def test_promote_hike_needs_a_named_destination_in_reach(self):
        # a route that ends nowhere near a destination POI is left alone
        t = m.Trail("Some Loop Route", "relation", [1],
                    [[(0.0, 0.0), (0.001, 0.001)]], {}, [])
        far = [{"name": "Far Peak", "coord": (5.0, 5.0), "tags": {"natural": "peak"}}]
        m.promote_hikes([t], far)
        self.assertFalse(t.hike)

    def test_classify_kind_route_vs_trail(self):
        # thru-routes by network grade
        self.assertEqual(m.classify_kind("Hayduke Trail #13", {"network": "rwn"}), "route")
        self.assertEqual(m.classify_kind("Some Way", {"network": "nwn"}), "route")
        self.assertEqual(m.classify_kind("Some Way", {"network": "IWN"}), "route")
        # composite '--' name (a route over two named trails)
        self.assertEqual(
            m.classify_kind("Angels Landing Trail--West Rim Trail", {}), "route")
        # explicitly a 'Route' in the name
        self.assertEqual(
            m.classify_kind("Zion Narrows Top-Down Hiking Route", {}), "route")
        # named trails stay trails — incl. a route relation named for one trail
        self.assertEqual(m.classify_kind("West Rim Trail", {"network": "lwn"}), "trail")
        self.assertEqual(m.classify_kind("Angels Landing Trail", {}), "trail")
        self.assertEqual(m.classify_kind("Ma-Ha-Tuak Trail", {}), "trail")  # single hyphen
        self.assertEqual(m.classify_kind(None, {}), "trail")


if __name__ == "__main__":
    unittest.main()
