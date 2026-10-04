"""Focused structural and Denmark curation controls for exact-area output."""
import math
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from shapely.geometry import MultiPolygon, box
    HAVE_SHAPELY = True
except ImportError:
    HAVE_SHAPELY = False

import areas  # noqa: E402
import model as m  # noqa: E402
import quality  # noqa: E402

AREA = "Nationalpark Mols Bjerge"


def W(node_ids, **tags):
    return {"nodes": list(node_ids), "tags": tags}


def T(name, source, way_ids, lines, *, relation_id=None, tags=None):
    trail = m.Trail(name, source, list(way_ids), lines, tags or {}, [])
    if relation_id is not None:
        trail.relation_ids = [relation_id]
        trail.direct_relation_ways = {relation_id: list(way_ids)}
    return trail


def prepared(trails, ways):
    features = [trail.to_feature() for trail in trails]
    quality.prepare_feature_sources(features, trails, ways)
    for feature in features:
        feature["properties"]["area"] = AREA
    return features


def curate(trails, ways, nodes, *, region="dk", boundary=None):
    features = prepared(trails, ways)
    return quality.curate_exact_area(
        features,
        ways=ways,
        nodes=nodes,
        area_union=boundary if boundary is not None else box(-2, -2, 20, 20),
        area_name=AREA,
        region=region,
    )


@unittest.skipUnless(HAVE_SHAPELY, "shapely not installed")
class NameStitchConnectivity(unittest.TestCase):
    def test_source_disconnected_stitch_is_removed_with_truthful_reason(self):
        ways = {
            1: W([1, 2], highway="footway", name="Synthetic Separated Path"),
            2: W([3, 4], highway="footway", name="Synthetic Separated Path"),
        }
        nodes = {1: (0, 0), 2: (0.001, 0), 3: (0.1, 0), 4: (0.101, 0)}
        trail = T("Synthetic Separated Path", "name-stitch", [1, 2],
                  [[nodes[1], nodes[2]], [nodes[3], nodes[4]]])

        kept, removed, report = curate([trail], ways, nodes)

        self.assertEqual(kept, [])
        self.assertEqual(report["removed_counts"], {"disconnected-name-stitch": 1})
        properties = removed[0]["properties"]
        self.assertEqual(properties["removed_category"], "disconnected-name-stitch")
        self.assertIn("disconnected in OSM node topology", properties["removed_reason"])
        self.assertEqual(properties["member_ways"], [1, 2])
        self.assertEqual([row["way_id"] for row in properties["source_ways"]], [1, 2])
        self.assertEqual(quality.geometry_component_count(removed[0]), 2)

    def test_shared_node_multipart_stitch_remains_one_object(self):
        ways = {
            1: W([1, 2], highway="path", name="Connected Trail"),
            2: W([2, 3], highway="footway", name="Connected Trail"),
        }
        nodes = {1: (0, 0), 2: (1, 0), 3: (2, 0)}
        trail = T("Connected Trail", "name-stitch", [1, 2],
                  [[nodes[1], nodes[2]], [nodes[2], nodes[3]]])

        kept, removed, _ = curate([trail], ways, nodes)

        self.assertEqual(len(kept), 1)
        self.assertEqual(removed, [])
        self.assertEqual(kept[0]["properties"]["connectivity"]["source_components"], 1)

    def test_connected_but_non_source_geometry_is_rejected(self):
        ways = {1: W([1, 2], highway="path", name="Source Bound Trail")}
        nodes = {1: (0, 0), 2: (2, 0)}
        trail = T("Source Bound Trail", "name-stitch", [1],
                  [[nodes[1], (1, 0)]])

        kept, removed, _ = curate([trail], ways, nodes)

        self.assertEqual(len(kept), 1)
        self.assertEqual(removed, [])
        self.assertEqual(kept[0]["properties"]["quality_disposition"],
                         "invalid-source-geometry")
        self.assertFalse(kept[0]["properties"]["connectivity"][
            "exact_boundary_clip"])

    def test_source_connected_boundary_split_is_preserved_and_proven(self):
        ways = {1: W([1, 2, 3], highway="path", name="Boundary Stitch")}
        nodes = {1: (0, 0), 2: (2, 0), 3: (4, 0)}
        trail = T("Boundary Stitch", "name-stitch", [1],
                  [[nodes[1], nodes[2], nodes[3]]])
        features = prepared([trail], ways)
        boundary = MultiPolygon([box(-0.1, -1, 1, 1), box(3, -1, 4.1, 1)])
        clipped = areas.clip_features_to_area(
            features, boundary, min_inside_mi=0, area_name=AREA)

        kept, removed, _ = quality.curate_exact_area(
            clipped, ways=ways, nodes=nodes, area_union=boundary,
            area_name=AREA, region="dk")

        self.assertEqual(len(kept), 1)
        self.assertEqual(removed, [])
        properties = kept[0]["properties"]
        self.assertEqual(properties["quality_disposition"], "boundary-induced-split")
        self.assertTrue(properties["boundary_induced_split"])
        self.assertTrue(properties["connectivity"]["exact_boundary_clip"])
        self.assertEqual(properties["connectivity"]["postclip_components"], 2)

    def test_boundary_split_other_keeps_additive_fact_and_review_disposition(self):
        ways = {1: W([1, 2, 3], highway="cycleway", name="Boundary Cycleway")}
        nodes = {1: (0, 0), 2: (2, 0), 3: (4, 0)}
        trail = T("Boundary Cycleway", "name-stitch", [1],
                  [[nodes[1], nodes[2], nodes[3]]])
        features = prepared([trail], ways)
        boundary = MultiPolygon([box(-0.1, -1, 1, 1), box(3, -1, 4.1, 1)])
        clipped = areas.clip_features_to_area(
            features, boundary, min_inside_mi=0, area_name=AREA)

        kept, removed, report = quality.curate_exact_area(
            clipped, ways=ways, nodes=nodes, area_union=boundary,
            area_name=AREA, region="dk")

        self.assertEqual(len(kept), 1)
        self.assertEqual(removed, [])
        properties = kept[0]["properties"]
        self.assertTrue(properties["boundary_induced_split"])
        self.assertEqual(properties["quality_disposition"],
                         "preserved-other-needs-review")
        self.assertEqual(report["validation_failures"], [
            "preserved-other-needs-review: 'Boundary Cycleway'",
        ])

    def test_disconnected_source_is_not_excused_by_clipped_flag(self):
        ways = {
            1: W([1, 2], highway="path", name="False Boundary Claim"),
            2: W([3, 4], highway="path", name="False Boundary Claim"),
        }
        nodes = {1: (0, 0), 2: (1, 0), 3: (3, 0), 4: (4, 0)}
        trail = T("False Boundary Claim", "name-stitch", [1, 2],
                  [[nodes[1], nodes[2]], [nodes[3], nodes[4]]])
        features = prepared([trail], ways)
        features[0]["properties"]["clipped"] = True

        kept, removed, _ = quality.curate_exact_area(
            features, ways=ways, nodes=nodes, area_union=box(-1, -1, 5, 1),
            area_name=AREA, region="dk")

        self.assertEqual(kept, [])
        self.assertEqual(removed[0]["properties"]["removed_category"],
                         "disconnected-name-stitch")


@unittest.skipUnless(HAVE_SHAPELY, "shapely not installed")
class RelationConnectivity(unittest.TestCase):
    def test_boundary_induced_split_keeps_signed_relation_without_connector(self):
        ways = {1: W([1, 2, 3], highway="path")}
        nodes = {1: (0, 0), 2: (2, 0), 3: (4, 0)}
        trail = T("Boundary Route", "relation", [1],
                  [[nodes[1], nodes[2], nodes[3]]], relation_id=700)
        original = [list(line) for line in trail.lines]
        features = prepared([trail], ways)
        boundary = MultiPolygon([box(-0.1, -1, 1, 1), box(3, -1, 4.1, 1)])
        clipped = areas.clip_features_to_area(
            features, boundary, min_inside_mi=0, area_name=AREA)

        kept, removed, report = quality.curate_exact_area(
            clipped, ways=ways, nodes=nodes, area_union=boundary,
            area_name=AREA, region="dk")

        self.assertEqual(len(kept), 1)
        self.assertEqual(removed, [])
        self.assertEqual(report["signed_relation_unresolved_count"], 0)
        evidence = kept[0]["properties"]["connectivity"]
        self.assertEqual(evidence["source_components"], 1)
        self.assertEqual(evidence["postclip_components"], 2)
        self.assertIn("boundary-induced-split", evidence["accepted_reasons"])
        self.assertEqual(kept[0]["properties"]["quality_disposition"],
                         "boundary-induced-split")
        self.assertTrue(kept[0]["properties"]["boundary_induced_split"])
        self.assertNotEqual(kept[0]["geometry"]["coordinates"],
                            [[[x, y] for x, y in line] for line in original])
        self.assertEqual(len(kept[0]["geometry"]["coordinates"]), 2)

    def test_small_coordinate_gap_with_distinct_nodes_stays_unresolved(self):
        nodes = {1: (0, 0), 2: (1, 0), 3: (1.000001, 0), 4: (2, 0)}
        ways = {1: W([1, 2], highway="path"), 2: W([3, 4], highway="path")}
        trail = T("Distinct Node Gap", "relation", [1, 2],
                  [[nodes[1], nodes[2]], [nodes[3], nodes[4]]], relation_id=701)
        original = [[[x, y] for x, y in line] for line in trail.lines]

        kept, removed, report = curate([trail], ways, nodes)

        self.assertEqual(len(kept), 1)
        self.assertEqual(removed, [])
        self.assertEqual(kept[0]["geometry"]["coordinates"], original)
        self.assertEqual(kept[0]["properties"]["quality_disposition"],
                         "unresolved-relation-gap")
        self.assertEqual(report["signed_relation_unresolved_count"], 1)
        self.assertEqual(report["signed_relation_unresolved"][0]["name"],
                         "Distinct Node Gap")
        self.assertTrue(any(error.startswith("unresolved-relation-gap:")
                            for error in report["validation_failures"]))
        self.assertNotIn("relation_endpoint_tolerance_m", report)

    def test_shared_node_t_junction_is_connected(self):
        nodes = {1: (0, 0), 2: (1, 0), 3: (2, 0), 4: (1, 1)}
        ways = {1: W([1, 2, 3], highway="path"), 2: W([2, 4], highway="path")}
        trail = T("T Route", "relation", [1, 2],
                  [[nodes[1], nodes[2], nodes[3]], [nodes[2], nodes[4]]],
                  relation_id=703)

        kept, removed, report = curate([trail], ways, nodes)

        self.assertEqual(len(kept), 1)
        self.assertEqual(removed, [])
        self.assertEqual(report["signed_relation_unresolved_count"], 0)
        self.assertEqual(kept[0]["properties"]["connectivity"]["source_components"], 1)
        self.assertEqual(quality.geometry_component_count(kept[0]), 1)

    def test_coordinate_crossing_with_distinct_node_ids_is_unresolved(self):
        nodes = {1: (-1, 0), 2: (1, 0), 3: (0, -1), 4: (0, 1)}
        ways = {1: W([1, 2], highway="path"), 2: W([3, 4], highway="path")}
        trail = T("Crossing Route", "relation", [1, 2],
                  [[nodes[1], nodes[2]], [nodes[3], nodes[4]]], relation_id=704)

        kept, removed, report = curate([trail], ways, nodes)

        self.assertEqual(len(kept), 1)
        self.assertEqual(removed, [])
        self.assertEqual(report["signed_relation_unresolved_count"], 1)
        self.assertEqual(kept[0]["properties"]["connectivity"]["source_components"], 2)

    def test_signed_hiking_relation_retains_walkable_road_member_only_in_route(self):
        nodes = {1: (0, 0), 2: (0.01, 0),
                 3: (0.0105, 0), 4: (0.02, 0)}
        ways = {
            10: W([1, 2], highway="path"),
            11: W([2, 3], highway="residential", name="Synthetic Road"),
            12: W([3, 4], highway="footway"),
        }
        relations = {
            900: {
                "tags": {"type": "route", "route": "hiking", "name": "Signed Route"},
                "members": [("w", 10, ""), ("w", 11, ""), ("w", 12, "")],
            },
        }

        trails = m.assemble(nodes, ways, relations, [], region="dk")

        self.assertEqual(len(trails), 1)
        self.assertEqual(trails[0].source, "relation")
        self.assertEqual(trails[0].member_ways, [10, 11, 12])
        self.assertEqual(trails[0].direct_relation_ways, {900: [10, 11, 12]})
        self.assertFalse(m._is_trailish(ways[11]["tags"]))
        self.assertEqual(m.assemble(nodes, {11: ways[11]}, {}, []), [])
        kept, removed, report = curate(trails, ways, nodes)
        self.assertEqual(len(kept), 1)
        self.assertEqual(removed, [])
        self.assertEqual(report["signed_relation_unresolved_count"], 0)
        restored = report["restored_road_relations"][0]
        self.assertEqual(restored["restored_way_ids"], [11])
        self.assertEqual(restored["restored_way_count"], 1)
        self.assertLess(restored["restored_share"], 0.10)
        self.assertLess(restored["restored_miles"], 1.0)
        self.assertEqual(report["restored_road_aggregate"]["restored_way_count"], 1)
        self.assertEqual(report["validation_failures"], [])

    def test_explicit_pedestrian_restriction_excludes_member_and_surfaces_gap(self):
        for restricted_tags in (
                {"foot": "no"}, {"foot": "private"}, {"access": "private"},
                {"service": "parking_aisle", "foot": "yes"},
                {"service": "driveway", "foot": "yes"},
                {"service": "drive-through", "foot": "yes"},
                {"service": "emergency_access", "foot": "yes"}):
            with self.subTest(restricted_tags=restricted_tags):
                nodes = {1: (0, 0), 2: (1, 0), 3: (2, 0), 4: (3, 0)}
                ways = {
                    10: W([1, 2], highway="path"),
                    11: W([2, 3], highway="service", **restricted_tags),
                    12: W([3, 4], highway="path"),
                }
                relations = {901: {
                    "tags": {"type": "route", "route": "hiking", "name": "Restricted"},
                    "members": [("w", 10, ""), ("w", 11, ""), ("w", 12, "")],
                }}
                trail = m.assemble(nodes, ways, relations, [], region="dk")[0]
                self.assertEqual(trail.member_ways, [10, 12])
                kept, removed, report = curate([trail], ways, nodes)
                self.assertEqual(len(kept), 1)
                self.assertEqual(removed, [])
                self.assertEqual(report["signed_relation_unresolved_count"], 1)

    def _curate_road_fraction(self, road_miles, total_miles):
        miles_per_degree = math.pi * 3958.7613 / 180.0
        start_road = (total_miles - road_miles) / 2.0 / miles_per_degree
        end_road = start_road + road_miles / miles_per_degree
        end = total_miles / miles_per_degree
        nodes = {
            1: (0.0, 0.0), 2: (start_road, 0.0),
            3: (end_road, 0.0), 4: (end, 0.0),
        }
        ways = {
            10: W([1, 2], highway="path"),
            11: W([2, 3], highway="residential"),
            12: W([3, 4], highway="path"),
        }
        relations = {902: {
            "tags": {"type": "route", "route": "hiking",
                     "name": "Threshold Route"},
            "members": [("w", 10, ""), ("w", 11, ""), ("w", 12, "")],
        }}
        trails = m.assemble(nodes, ways, relations, [], region="dk")
        return curate(trails, ways, nodes)

    def test_restored_road_share_threshold_below_equal_and_above(self):
        cases = ((0.099, False), (0.100, False), (0.101, True))
        for share, rejected in cases:
            with self.subTest(share=share):
                kept, removed, report = self._curate_road_fraction(share, 1.0)
                self.assertEqual(len(kept), 1)
                self.assertEqual(removed, [])
                row = report["restored_road_relations"][0]
                self.assertAlmostEqual(row["restored_share"], share, places=6)
                failures = [message for message in report["validation_failures"]
                            if "restored-road-share-limit" in message]
                self.assertEqual(bool(failures), rejected)
                aggregate_failures = [
                    message for message in report["validation_failures"]
                    if "restored-road-aggregate-share-limit" in message]
                self.assertEqual(bool(aggregate_failures), rejected)

    def test_restored_road_mile_threshold_below_equal_and_above(self):
        cases = ((0.999, False), (1.000, False), (1.001, True))
        for road_miles, rejected in cases:
            with self.subTest(road_miles=road_miles):
                _, _, report = self._curate_road_fraction(road_miles, 20.0)
                row = report["restored_road_relations"][0]
                self.assertAlmostEqual(row["restored_miles"], road_miles, places=6)
                failures = [message for message in report["validation_failures"]
                            if "restored-road-mile-limit" in message]
                self.assertEqual(bool(failures), rejected)
                self.assertFalse(any("restored-road-share-limit" in message
                                     for message in report["validation_failures"]))

    def test_relation_only_denominator_ignores_absorbed_same_name_path(self):
        miles_per_degree = math.pi * 3958.7613 / 180.0
        road = 0.02 / miles_per_degree
        relation_path = 0.09 / miles_per_degree
        standalone_path = 1.0 / miles_per_degree
        nodes = {
            1: (0.0, 0.0),
            2: (relation_path, 0.0),
            3: (relation_path + road, 0.0),
            4: (relation_path + road + standalone_path, 0.0),
        }
        ways = {
            10: W([1, 2], highway="path"),
            11: W([2, 3], highway="residential"),
            12: W([3, 4], highway="path", name="Signed Route"),
        }
        relations = {700: {
            "tags": {"type": "route", "route": "hiking", "name": "Signed Route"},
            "members": [("w", 10, ""), ("w", 11, "")],
        }}

        trails = m.assemble(nodes, ways, relations, [], region="dk")
        self.assertEqual(len(trails), 1)
        self.assertEqual(trails[0].member_ways, [10, 11, 12])
        kept, removed, report = curate(trails, ways, nodes)

        self.assertEqual(len(kept), 1)
        self.assertEqual(removed, [])
        measurement = report["restored_road_relations"][0]
        self.assertEqual(measurement["relation_id"], 700)
        self.assertEqual(measurement["relation_way_ids"], [10, 11])
        self.assertAlmostEqual(measurement["total_relation_miles"], 0.11, places=5)
        self.assertAlmostEqual(measurement["restored_share"], 0.02 / 0.11,
                               places=5)
        self.assertTrue(any("restored-road-share-limit" in failure
                            for failure in report["validation_failures"]))

    def test_multi_relation_measurements_use_unique_relation_way_tuples(self):
        nodes = {1: (0, 0), 2: (0.01, 0), 3: (0.011, 0), 4: (0.021, 0)}
        ways = {
            10: W([1, 2], highway="path"),
            11: W([2, 3], highway="residential"),
            12: W([3, 4], highway="path"),
        }
        trail = T("Coalesced Relations", "relation", [10, 11, 12],
                  [[nodes[1], nodes[2], nodes[3], nodes[4]]])
        trail.relation_ids = [700, 701]
        trail.direct_relation_ways = {700: [10, 11], 701: [11, 12]}
        trail.restored_relation_ways = [11]

        _, _, report = curate([trail], ways, nodes)

        measurements = report["restored_road_relations"]
        self.assertEqual([row["relation_id"] for row in measurements], [700, 701])
        self.assertEqual([row["relation_way_ids"] for row in measurements],
                         [[10, 11], [11, 12]])
        self.assertEqual(report["restored_road_aggregate"]["restored_way_count"], 2)
        expected_total = (m.line_mi([nodes[1], nodes[2]])
                          + 2 * m.line_mi([nodes[2], nodes[3]])
                          + m.line_mi([nodes[3], nodes[4]]))
        self.assertAlmostEqual(
            report["restored_road_aggregate"]["total_relation_miles"],
            expected_total, places=6)

    def test_relation_denominator_uses_exact_clipped_source_way_miles(self):
        nodes = {1: (-1, 0), 2: (0.5, 0), 3: (0.6, 0)}
        ways = {
            10: W([1, 2], highway="path"),
            11: W([2, 3], highway="residential"),
        }
        trail = T("Clipped Relation", "relation", [10, 11],
                  [[nodes[1], nodes[2], nodes[3]]], relation_id=700)
        trail.restored_relation_ways = [11]
        features = prepared([trail], ways)
        boundary = box(0, -1, 1, 1)
        clipped = areas.clip_features_to_area(
            features, boundary, min_inside_mi=0, area_name=AREA)

        _, _, report = quality.curate_exact_area(
            clipped, ways=ways, nodes=nodes, area_union=boundary,
            area_name=AREA, region="dk")

        measurement = report["restored_road_relations"][0]
        expected = (m.line_mi([(0, 0), nodes[2]])
                    + m.line_mi([nodes[2], nodes[3]]))
        self.assertAlmostEqual(measurement["total_relation_miles"], expected,
                               places=6)
        self.assertLess(measurement["total_relation_miles"],
                        m.line_mi([nodes[1], nodes[2], nodes[3]]))

    def test_missing_source_way_is_kept_as_unavailable_evidence(self):
        nodes = {1: (0, 0), 2: (1, 0)}
        trail = T("Missing Source", "name-stitch", [99],
                  [[nodes[1], nodes[2]]])

        kept, removed, report = curate([trail], {}, nodes)

        self.assertEqual(len(kept), 1)
        self.assertEqual(removed, [])
        properties = kept[0]["properties"]
        self.assertEqual(properties["quality_disposition"], "missing-source-way")
        self.assertEqual(properties["connectivity"]["status"],
                         "unavailable-evidence")
        self.assertEqual(properties["connectivity"]["missing_way_ids"], [99])
        self.assertTrue(any("missing-source-way unavailable evidence" in message
                            for message in report["validation_failures"]))

    def test_missing_source_node_matches_assembler_tolerance_but_fails_closed(self):
        ways = {99: W([1, 2, 3], highway="path", name="Partial Source")}
        nodes = {1: (0, 0), 3: (1, 0)}
        trail = T("Partial Source", "name-stitch", [99],
                  [[nodes[1], nodes[3]]])

        kept, removed, report = curate([trail], ways, nodes)

        self.assertEqual(len(kept), 1)
        self.assertEqual(removed, [])
        properties = kept[0]["properties"]
        self.assertEqual(properties["quality_disposition"], "missing-source-way")
        self.assertEqual(properties["source_ways"][0]["node_ids"], [1, 3])
        self.assertEqual(properties["source_ways"][0]["missing_node_ids"], [2])
        self.assertEqual(properties["connectivity"]["status"],
                         "unavailable-evidence")
        self.assertEqual(properties["connectivity"]["missing_way_ids"], [99])
        self.assertTrue(any("missing-source-way unavailable evidence" in message
                            for message in report["validation_failures"]))

    def test_nested_relations_keep_true_direct_membership(self):
        nodes = {1: (0, 0), 2: (1, 0), 3: (2, 0)}
        ways = {
            11: W([1, 2], highway="path"),
            12: W([2, 3], highway="path"),
        }
        relations = {
            700: {
                "tags": {"type": "route", "route": "hiking", "name": "Parent"},
                "members": [("r", 701, "")],
            },
            701: {
                # A nested relation need not itself be a separately emitted
                # hiking route; its direct identity still belongs in the
                # parent's source evidence.
                "tags": {},
                "members": [("w", 11, ""), ("w", 12, "")],
            },
        }

        trails = m.assemble(nodes, ways, relations, [], region="dk")
        parent = next(trail for trail in trails if trail.name == "Parent")

        self.assertEqual(parent.relation_ids, [700, 701])
        self.assertEqual(parent.direct_relation_ways, {700: [], 701: [11, 12]})
        self.assertNotIn(11, parent.direct_relation_ways[700])


@unittest.skipUnless(HAVE_SHAPELY, "shapely not installed")
class DenmarkRoadTrackCuration(unittest.TestCase):
    def _single(self, name, tags, *, source="name-stitch", relation_id=None,
                region="dk"):
        ways = {1: W([1, 2], name=name, **tags)}
        nodes = {1: (0, 0), 2: (1, 0)}
        trail = T(name, source, [1], [[nodes[1], nodes[2]]],
                  relation_id=relation_id,
                  tags={"type": "route", "route": "hiking"}
                  if relation_id else None)
        return curate([trail], ways, nodes, region=region)

    def test_non_suffix_and_vej_pure_tracks_are_removed(self):
        for name in ("Synthetic Farm Track", "Synthetic Skovvej"):
            with self.subTest(name=name):
                kept, removed, _ = self._single(name, {"highway": "track"})
                self.assertEqual(kept, [])
                self.assertEqual(removed[0]["properties"]["removed_category"],
                                 "dk-unqualified-road-track")

    def test_only_actual_walking_foot_values_are_explicit(self):
        for foot in ("yes", "designated", "permissive"):
            with self.subTest(foot=foot):
                kept, removed, _ = self._single(
                    "Synthetic Explicit Track", {"highway": "track", "foot": foot})
                self.assertEqual(len(kept), 1)
                self.assertEqual(removed, [])
                self.assertEqual(kept[0]["properties"]["walking_identity"], "explicit")

    def test_access_style_foot_values_do_not_grant_walking_identity(self):
        for foot in ("destination", "permit", "customers", "official"):
            with self.subTest(foot=foot):
                kept, removed, _ = self._single(
                    "Synthetic Access Track", {"highway": "track", "foot": foot})
                self.assertEqual(kept, [])
                self.assertEqual(removed[0]["properties"]["walking_identity"],
                                 "weak-track")
                self.assertEqual(removed[0]["properties"]["removed_category"],
                                 "dk-unqualified-road-track")

    def test_relation_backed_midtervej_and_haervejen_are_preserved(self):
        nodes = {1: (0, 0), 2: (1, 0), 3: (2, 0), 4: (3, 0)}
        ways = {
            1: W([1, 2], highway="track", name="Midtervej"),
            2: W([3, 4], highway="track", name="Hærvejen"),
        }
        relations = {
            800: {
                "tags": {"type": "route", "route": "hiking",
                         "name": "Midtervej", "network": "lwn"},
                "members": [("w", 1, "")],
            },
            801: {
                "tags": {"type": "route", "route": "hiking",
                         "name": "Hærvejen", "network": "nwn"},
                "members": [("w", 2, "")],
            },
        }
        trails = m.assemble(nodes, ways, relations, [], region="dk")
        kept, removed, report = curate(trails, ways, nodes)
        self.assertEqual({feature["properties"]["name"] for feature in kept},
                         {"Midtervej", "Hærvejen"})
        self.assertEqual(removed, [])
        self.assertEqual(report["validation_failures"], [])

    def test_arizona_and_california_controls_are_unchanged(self):
        for region in ("az", "ca"):
            with self.subTest(region=region):
                kept, removed, report = self._single(
                    "Ordinary Track", {"highway": "track"}, region=region)
                self.assertEqual(len(kept), 1)
                self.assertEqual(removed, [])
                self.assertFalse(report["enabled"])
                self.assertNotIn("walking_identity", kept[0]["properties"])


@unittest.skipUnless(HAVE_SHAPELY, "shapely not installed")
class CompleteOverlapLedger(unittest.TestCase):
    def test_structurally_removed_candidate_is_still_in_complete_ledger(self):
        nodes = {
            1: (0, 0), 2: (10, 0),
            3: (1, 0), 4: (2, 0), 5: (8, 0), 6: (9, 0),
        }
        ways = {
            10: W([1, 2], highway="path"),
            20: W([3, 4], highway="track", name="Synthetic Split Track"),
            21: W([5, 6], highway="track", name="Synthetic Split Track"),
        }
        trails = [
            T("Signed Route", "relation", [10], [[nodes[1], nodes[2]]],
              relation_id=900),
            T("Synthetic Split Track", "name-stitch", [20, 21],
              [[nodes[3], nodes[4]], [nodes[5], nodes[6]]]),
        ]

        kept, removed, report = curate(trails, ways, nodes)

        self.assertEqual({feature["properties"]["name"] for feature in kept},
                         {"Signed Route"})
        self.assertEqual(removed[0]["properties"]["removed_category"],
                         "disconnected-name-stitch")
        self.assertEqual(len(report["overlap_evidence"]), 1)
        record = report["overlap_evidence"][0]
        self.assertEqual(record["left_overlap_ratio"], 0.2)
        self.assertEqual(record["right_overlap_ratio"], 1.0)
        self.assertEqual(record["disposition"],
                         "removed-disconnected-name-stitch")
        self.assertEqual(record["removed_candidates"], [{
            "side": "right",
            "candidate_index": 1,
            "key": "w20-21",
            "name": "Synthetic Split Track",
            "category": "disconnected-name-stitch",
        }])
        self.assertEqual(report["remaining_overlaps"], [])

    def test_weak_nested_synthetic_tracks_drop_and_explicit_path_stays(self):
        nodes = {
            1: (0, 0), 2: (10, 0), 3: (0, 0), 4: (10, 0),
            5: (1, 0), 6: (3, 0), 7: (4, 0), 8: (6, 0),
            9: (7, 0), 10: (9, 0),
        }
        ways = {
            100: W([1, 2], highway="path"),
            101: W([3, 4], highway="path"),
            200: W([5, 6], highway="track", name="Synthetic Weak Track A"),
            201: W([7, 8], highway="track", name="Synthetic Weak Track B"),
            202: W([9, 10], highway="path", name="Synthetic Explicit Path"),
        }
        trails = [
            T("Signed Route A", "relation", [100], [[nodes[1], nodes[2]]],
              relation_id=900),
            T("Signed Route B", "relation", [101], [[nodes[3], nodes[4]]],
              relation_id=901),
            T("Synthetic Weak Track A", "name-stitch", [200], [[nodes[5], nodes[6]]]),
            T("Synthetic Weak Track B", "name-stitch", [201], [[nodes[7], nodes[8]]]),
            T("Synthetic Explicit Path", "name-stitch", [202], [[nodes[9], nodes[10]]]),
        ]

        kept, removed, report = curate(trails, ways, nodes)

        self.assertEqual({feature["properties"]["name"] for feature in kept},
                         {"Signed Route A", "Signed Route B", "Synthetic Explicit Path"})
        self.assertEqual(
            {feature["properties"]["name"] for feature in removed},
            {"Synthetic Weak Track A", "Synthetic Weak Track B"},
        )
        explicit_rows = [
            row for row in report["overlap_evidence"]
            if "Synthetic Explicit Path" in row["names"]
        ]
        self.assertTrue(explicit_rows)
        self.assertTrue(all(row["disposition"] ==
                            "preserved-explicit-walking-or-path"
                            for row in explicit_rows))
        self.assertEqual(report["validation_failures"], [])

    def test_relation_backed_all_track_overlap_is_preserved_without_weak_failure(self):
        nodes = {1: (0, 0), 2: (10, 0), 3: (0, 0), 4: (10, 0)}
        ways = {
            1: W([1, 2], highway="track", name="Midtervej"),
            2: W([3, 4], highway="track", name="Hærvejen"),
        }
        trails = [
            T("Midtervej", "relation", [1], [[nodes[1], nodes[2]]], relation_id=1),
            T("Hærvejen", "relation", [2], [[nodes[3], nodes[4]]], relation_id=2),
        ]

        kept, removed, report = curate(trails, ways, nodes)

        self.assertEqual(len(kept), 2)
        self.assertEqual(removed, [])
        self.assertEqual(report["validation_failures"], [])
        self.assertEqual(report["overlap_evidence"][0]["disposition"],
                         "preserved-shared-signed-routes")

    def test_cycleway_overlap_is_other_and_fails_closed_for_review(self):
        nodes = {1: (0, 0), 2: (10, 0), 3: (1, 0), 4: (9, 0)}
        ways = {
            1: W([1, 2], highway="path"),
            2: W([3, 4], highway="cycleway", name="Synthetic Cycleway"),
        }
        trails = [
            T("Signed Route", "relation", [1], [[nodes[1], nodes[2]]], relation_id=1),
            T("Synthetic Cycleway", "name-stitch", [2], [[nodes[3], nodes[4]]]),
        ]

        kept, removed, report = curate(trails, ways, nodes)

        self.assertEqual(len(kept), 2)
        self.assertEqual(removed, [])
        cycleway = next(feature for feature in kept
                        if feature["properties"]["name"] == "Synthetic Cycleway")
        self.assertEqual(cycleway["properties"]["walking_identity"], "other")
        self.assertEqual(cycleway["properties"]["quality_disposition"],
                         "preserved-other-needs-review")
        self.assertEqual(report["overlap_evidence"][0]["disposition"],
                         "preserved-other-needs-review")
        self.assertTrue(any("preserved-other-needs-review" in error
                            for error in report["validation_failures"]))


if __name__ == "__main__":
    unittest.main()
