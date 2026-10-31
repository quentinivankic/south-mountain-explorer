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
        trail.root_relation_ids = [relation_id]
        trail.direct_relation_ways = {relation_id: list(way_ids)}
    return trail


def prepared(trails, ways):
    features = [trail.to_feature() for trail in trails]
    quality.prepare_feature_sources(features, trails, ways)
    for feature in features:
        feature["properties"]["area"] = AREA
    return features


def curate(trails, ways, nodes, *, region="dk", boundary=None,
           dk_rules="legacy"):
    features = prepared(trails, ways)
    return quality.curate_exact_area(
        features,
        ways=ways,
        nodes=nodes,
        area_union=boundary if boundary is not None else box(-2, -2, 20, 20),
        area_name=AREA,
        region=region,
        dk_rules=dk_rules,
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

    def test_out_of_area_unsafe_member_explains_source_gap(self):
        nodes = {
            1: (0, 0), 2: (3, 0), 3: (7, 0), 4: (10, 0),
        }
        ways = {
            10: W([1, 2], highway="path"),
            11: W([2, 3], highway="service", service="parking_aisle"),
            12: W([3, 4], highway="path"),
        }
        relations = {700: {
            "tags": {"type": "route", "route": "hiking",
                     "name": "Boundary Excursion"},
            "members": [("w", 10, ""), ("w", 11, ""), ("w", 12, "")],
        }}
        audits = []
        trails = m.assemble(
            nodes, ways, relations, [], region="dk",
            collect_relation_member_audit=audits)
        features = prepared(trails, ways)
        boundary = MultiPolygon([
            box(-0.5, -1, 1, 1), box(9, -1, 10.5, 1),
        ])
        clipped = areas.clip_features_to_area(
            features, boundary, min_inside_mi=0, area_name=AREA)

        kept, removed, report = quality.curate_exact_area(
            clipped, ways=ways, nodes=nodes, area_union=boundary,
            area_name=AREA, region="dk", relation_member_audit=audits)

        self.assertEqual(len(kept), 1)
        self.assertEqual(removed, [])
        connectivity = kept[0]["properties"]["connectivity"]
        self.assertEqual(connectivity["source_components"], 2)
        self.assertEqual(connectivity["postclip_components"], 2)
        self.assertEqual(connectivity["accepted_reasons"], [
            "out-of-area-member-gap", "boundary-induced-split",
        ])
        excluded = report["relation_member_audit"][0]["direct_way_members"][1]
        self.assertEqual(excluded["exact_area_status"], "outside")
        self.assertEqual(excluded["render_disposition"],
                         "unsafe-outside-exact-area")
        self.assertIsNone(excluded["terminal_reason"])
        self.assertEqual(report["relation_member_unresolved_count"], 0)
        self.assertEqual(report["signed_relation_unresolved_count"], 0)

    def test_fused_roots_require_outside_gap_proof_for_every_root(self):
        nodes = {
            1: (10.000, 56.0), 2: (10.005, 56.0),
            3: (10.010, 56.0), 4: (10.015, 56.0),
            5: (10.000, 56.01), 6: (10.005, 56.01),
            7: (10.010, 56.01), 8: (10.015, 56.01),
        }
        boundary = MultiPolygon([
            box(9.999, 55.999, 10.005, 56.011),
            box(10.010, 55.999, 10.016, 56.011),
        ])

        def run(second_proof):
            ways = {
                10: W([1, 2], highway="path"),
                11: W([2, 3], highway="service", service="parking_aisle"),
                12: W([3, 4], highway="path"),
                20: W([5, 6], highway="path"),
                22: W([7, 8], highway="path"),
            }
            if second_proof:
                ways[21] = W(
                    [6, 7], highway="service", service="parking_aisle")
            relations = {
                700: {
                    "tags": {"type": "route", "route": "hiking",
                             "name": "Fused Route"},
                    "members": [("w", 10, ""), ("w", 11, ""),
                                ("w", 12, "")],
                },
                701: {
                    "tags": {"type": "route", "route": "hiking",
                             "name": "Fused Route"},
                    "members": ([("w", 20, ""), ("w", 21, ""),
                                 ("w", 22, "")] if second_proof else
                                [("w", 20, ""), ("w", 22, "")]),
                },
            }
            audits = []
            trails = m.assemble(
                nodes, ways, relations, [], region="dk",
                collect_relation_member_audit=audits)
            self.assertEqual(len(trails), 1)
            self.assertEqual(trails[0].root_relation_ids, [700, 701])
            features = prepared(trails, ways)
            clipped = areas.clip_features_to_area(
                features, boundary, min_inside_mi=0, area_name=AREA)
            return quality.curate_exact_area(
                clipped, ways=ways, nodes=nodes, area_union=boundary,
                area_name=AREA, region="dk", relation_member_audit=audits)

        failed, failed_removed, failed_report = run(False)
        self.assertEqual(failed, [])
        self.assertEqual(len(failed_removed), 1)
        failed_properties = failed_removed[0]["properties"]
        self.assertEqual(failed_properties["connectivity"]["status"],
                         "rejected")
        self.assertEqual(failed_properties["removed_category"],
                         "unresolved-relation-gap")
        self.assertEqual(failed_properties["drop_evidence"], {
            "kind": "denmark-quality",
            "category": "unresolved-relation-gap",
            "reason": "unresolved-source-connectivity",
        })
        self.assertEqual(failed_report["signed_relation_unresolved_count"], 1)
        self.assertEqual(failed_report["validation_failures"], [])

        passed, removed, passed_report = run(True)
        self.assertEqual(removed, [])
        self.assertEqual(passed[0]["properties"]["connectivity"]["status"],
                         "accepted")
        self.assertIn(
            "out-of-area-member-gap",
            passed[0]["properties"]["connectivity"]["accepted_reasons"])
        self.assertEqual(passed_report["signed_relation_unresolved_count"], 0)

    def test_small_coordinate_gap_with_distinct_nodes_stays_unresolved(self):
        nodes = {1: (0, 0), 2: (1, 0), 3: (1.000001, 0), 4: (2, 0)}
        ways = {1: W([1, 2], highway="path"), 2: W([3, 4], highway="path")}
        trail = T("Distinct Node Gap", "relation", [1, 2],
                  [[nodes[1], nodes[2]], [nodes[3], nodes[4]]], relation_id=701)
        original = [[[x, y] for x, y in line] for line in trail.lines]

        kept, removed, report = curate([trail], ways, nodes)

        self.assertEqual(kept, [])
        self.assertEqual(len(removed), 1)
        self.assertEqual(removed[0]["geometry"]["coordinates"], original)
        self.assertEqual(removed[0]["properties"]["removed_category"],
                         "unresolved-relation-gap")
        self.assertEqual(report["signed_relation_unresolved_count"], 1)
        self.assertEqual(report["signed_relation_unresolved"][0]["name"],
                         "Distinct Node Gap")
        self.assertEqual(report["validation_failures"], [])
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

        self.assertEqual(kept, [])
        self.assertEqual(len(removed), 1)
        self.assertEqual(
            removed[0]["properties"]["removed_category"],
            "unresolved-relation-gap")
        self.assertEqual(report["signed_relation_unresolved_count"], 1)
        self.assertEqual(
            removed[0]["properties"]["connectivity"]["source_components"], 2)

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
        self.assertEqual(restored["restored_ways"], [{
            "way_id": 11,
            "tags": {"highway": "residential", "name": "Synthetic Road"},
        }])
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
                audits = []
                trail = m.assemble(
                    nodes, ways, relations, [], region="dk",
                    collect_relation_member_audit=audits)[0]
                self.assertEqual(trail.member_ways, [10, 12])
                kept, removed, report = quality.curate_exact_area(
                    prepared([trail], ways), ways=ways, nodes=nodes,
                    area_union=box(-2, -2, 20, 20), area_name=AREA,
                    region="dk", relation_member_audit=audits)
                self.assertEqual(kept, [])
                self.assertEqual(len(removed), 1)
                self.assertEqual(
                    removed[0]["properties"]["removed_category"],
                    "unresolved-relation-gap")
                self.assertEqual(report["signed_relation_unresolved_count"], 1)
                self.assertEqual(report["relation_member_unresolved_count"], 1)
                self.assertIn(
                    "unsafe-member-in-exact-area",
                    report["relation_member_unresolved"][0]["reasons"])
                self.assertEqual(report["validation_failures"], [])

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

    def test_restored_road_share_is_diagnostic_without_threshold(self):
        for share in (0.099, 0.100, 0.101):
            with self.subTest(share=share):
                kept, removed, report = self._curate_road_fraction(share, 1.0)
                self.assertEqual(len(kept), 1)
                self.assertEqual(removed, [])
                row = report["restored_road_relations"][0]
                self.assertAlmostEqual(row["restored_share"], share, places=6)
                self.assertFalse(any("restored-road" in message and "limit" in message
                                     for message in report["validation_failures"]))

    def test_restored_road_miles_are_diagnostic_without_threshold(self):
        for road_miles in (0.999, 1.000, 1.001):
            with self.subTest(road_miles=road_miles):
                _, _, report = self._curate_road_fraction(road_miles, 20.0)
                row = report["restored_road_relations"][0]
                self.assertAlmostEqual(row["restored_miles"], road_miles, places=6)
                self.assertFalse(any("restored-road" in message and "limit" in message
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
        self.assertFalse(any("restored-road" in failure and "limit" in failure
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
        trail.root_relation_ids = [700, 701]
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

    def test_raw_source_order_and_local_reversed_lineage_are_separate(self):
        nodes = {1: (0, 0), 2: (1, 0), 3: (2, 0)}
        ways = {
            10: W([1, 2], highway="path"),
            11: W([3, 2], highway="footway"),
        }
        relations = {700: {
            "tags": {"type": "route", "route": "hiking", "name": "Route"},
            "members": [("w", 10, ""), ("w", 11, "")],
        }}
        raw_before = {way_id: list(way["nodes"])
                      for way_id, way in ways.items()}
        trails = m.assemble(nodes, ways, relations, [], region="dk")

        kept, removed, report = curate(trails, ways, nodes)

        self.assertEqual(removed, [])
        self.assertEqual(report["schema_version"], 8)
        self.assertEqual(
            {way_id: way["nodes"] for way_id, way in ways.items()}, raw_before)
        properties = kept[0]["properties"]
        self.assertEqual(properties["source_evidence_schema_version"], 2)
        self.assertEqual(properties["rendered_way_lineage_version"], 1)
        self.assertEqual(
            [record["node_ids"] for record in properties["source_ways"]],
            [[1, 2], [3, 2]])
        lineage = {record["way_id"]: record
                   for record in properties["rendered_way_lineage"]}
        self.assertEqual(lineage[10]["orientation"], "source")
        self.assertEqual(lineage[11]["orientation"], "reversed")
        self.assertEqual(kept[0]["geometry"]["coordinates"], [[
            [0.0, 0.0], [1.0, 0.0], [2.0, 0.0],
        ]])


@unittest.skipUnless(HAVE_SHAPELY, "shapely not installed")
class DroppedRootWayContamination(unittest.TestCase):
    """Dropped unresolved roots quarantine every publishable way alias."""

    @staticmethod
    def _add_chain(ways, nodes, way_ids, name, *, y, start_x=0.0):
        node_id = max(nodes, default=0) + 1
        for offset, way_id in enumerate(way_ids):
            left = node_id + offset
            right = left + 1
            nodes.setdefault(left, (start_x + offset, y))
            nodes[right] = (start_x + offset + 1, y)
            ways[way_id] = W([left, right], highway="path", name=name)

    @staticmethod
    def _lines(way_ids, ways, nodes):
        return [[nodes[node_id] for node_id in ways[way_id]["nodes"]]
                for way_id in way_ids]

    def test_frozen_five_root_overlaps_are_excluded_and_controls_ship(self):
        ways = {}
        nodes = {}
        candidate_specs = [
            ("Adelgade", [33969099, 759409895], 0.0),
            ("Krogryggevej", [135631196, 900001], 1.0),
            ("Eriks Vej", [38002217, 38002218, 760016202], 2.0),
            ("Rugårdsvej", [128896399, 900002, 1158192100], 3.0),
        ]
        for name, way_ids, y in candidate_specs:
            self._add_chain(ways, nodes, way_ids, name, y=y)
        self._add_chain(
            ways, nodes, [900003], "Unrelated Positive", y=4.0)
        self._add_chain(
            ways, nodes, [900004], "Adelgade", y=5.0)
        self._add_chain(
            ways, nodes, [920001], "Root 105 gap", y=10.0)
        self._add_chain(
            ways, nodes, [920002], "Root 141 gap", y=11.0)
        self._add_chain(
            ways, nodes, [920003], "Root 191 gap", y=12.0)

        root_specs = [
            (10507412, "Mols Bjerge-stien Ebeltoftetapen",
             [759409895, 920001]),
            (14190349, "Djurslandstien",
             [135631196, 38002217, 38002218, 760016202,
              128896399, 1158192100, 920002]),
            (19113818, "Den gamle købstad",
             [33969099, 759409895, 920003]),
        ]
        trails = [
            T(name, "relation", way_ids,
              self._lines(way_ids, ways, nodes), relation_id=root_id,
              tags={"type": "route", "route": "hiking"})
            for root_id, name, way_ids in root_specs
        ]
        trails.extend(
            T(name, "name-stitch", way_ids,
              self._lines(way_ids, ways, nodes))
            for name, way_ids, _y in candidate_specs
        )
        trails.extend([
            T("Unrelated Positive", "name-stitch", [900003],
              self._lines([900003], ways, nodes)),
            T("Adelgade", "name-stitch", [900004],
              self._lines([900004], ways, nodes)),
        ])

        kept, removed, report = curate(trails, ways, nodes)

        self.assertEqual(
            [(row["properties"]["name"], row["properties"]["member_ways"])
             for row in kept],
            [("Unrelated Positive", [900003]), ("Adelgade", [900004])],
        )
        self.assertEqual(
            {row["properties"]["root_relation_ids"][0] for row in removed},
            {10507412, 14190349, 19113818},
        )
        self.assertTrue(all(
            row["properties"]["removed_category"] ==
            "unresolved-relation-gap" for row in removed))
        self.assertEqual(report["dropped_root_contamination_count"], 4)
        overlaps = {
            (root["root_relation_id"], tuple(root["shared_way_ids"]))
            for exclusion in report["dropped_root_contamination"]
            for root in exclusion["dropped_roots"]
        }
        self.assertEqual(overlaps, {
            (10507412, (759409895,)),
            (14190349, (135631196,)),
            (14190349, (38002217, 38002218, 760016202)),
            (14190349, (128896399, 1158192100)),
            (19113818, (33969099, 759409895)),
        })
        self.assertEqual(report["candidate_count"], 5)
        indexes = [
            row["properties"]["quality_candidate_index"]
            for row in [*kept, *removed]
        ]
        self.assertEqual(sorted(indexes), list(range(5)))

    def test_name_stitch_fusion_cannot_hide_one_shared_way(self):
        ways = {}
        nodes = {}
        self._add_chain(
            ways, nodes, [759409895, 900010], "Fused Alias", y=0.0)
        self._add_chain(
            ways, nodes, [920010], "Root gap", y=4.0)
        shared = T(
            "Fused Alias", "name-stitch", [759409895],
            self._lines([759409895], ways, nodes))
        extension = T(
            "Fused Alias", "name-stitch", [900010],
            self._lines([900010], ways, nodes))
        fused = m.merge_same_name([shared, extension])
        self.assertEqual(len(fused), 1)
        self.assertEqual(fused[0].geometry_ways, [759409895, 900010])
        root = T(
            "Dropped Root", "relation", [759409895, 920010],
            self._lines([759409895, 920010], ways, nodes),
            relation_id=10507412,
            tags={"type": "route", "route": "hiking"})

        kept, removed, report = curate([root, fused[0]], ways, nodes)

        self.assertEqual(kept, [])
        self.assertEqual(len(removed), 1)
        self.assertEqual(report["dropped_root_contamination_count"], 1)
        self.assertEqual(
            report["dropped_root_contamination"][0]["dropped_roots"],
            [{"root_relation_id": 10507412,
              "shared_way_ids": [759409895]}],
        )

    def test_name_stitch_chain_from_assembly_is_quarantined(self):
        nodes = {
            1: (0.0, 0.0), 2: (1.0, 0.0), 3: (2.0, 0.0),
            4: (0.0, 4.0), 5: (1.0, 4.0),
        }
        ways = {
            10: W([1, 2], highway="path", name="Alias Chain"),
            11: W([2, 3], highway="path", name="Alias Chain"),
            12: W([4, 5], highway="path"),
        }
        relations = {700: {
            "tags": {"type": "route", "route": "hiking",
                     "name": "Dropped Route"},
            "members": [("w", 10, ""), ("w", 12, "")],
        }}
        trails = m.assemble(nodes, ways, relations, [], region="dk")
        relation = next(trail for trail in trails if trail.source == "relation")
        stitch = next(trail for trail in trails if trail.source == "name-stitch")
        self.assertEqual(relation.root_relation_ids, [700])
        self.assertEqual(stitch.geometry_ways, [10, 11])

        kept, removed, report = curate(trails, ways, nodes)

        self.assertEqual(kept, [])
        self.assertEqual(len(removed), 1)
        self.assertEqual(removed[0]["properties"]["root_relation_ids"], [700])
        self.assertEqual(report["dropped_root_contamination_count"], 1)
        self.assertEqual(report["dropped_root_contamination"][0]["name"],
                         "Alias Chain")
        self.assertEqual(
            report["dropped_root_contamination"][0]["dropped_roots"],
            [{"root_relation_id": 700, "shared_way_ids": [10]}],
        )

    def test_boundary_split_cannot_hide_one_shared_way(self):
        ways = {
            135631196: W([1, 2, 3], highway="path", name="Krogryggevej"),
            920020: W([4, 5], highway="path", name="Root gap"),
        }
        nodes = {
            1: (0.0, 0.0), 2: (2.0, 0.0), 3: (4.0, 0.0),
            4: (0.0, 4.0), 5: (1.0, 4.0),
        }
        root = T(
            "Djurslandstien", "relation", [135631196, 920020],
            self._lines([135631196, 920020], ways, nodes),
            relation_id=14190349,
            tags={"type": "route", "route": "hiking"})
        stitch = T(
            "Krogryggevej", "name-stitch", [135631196],
            self._lines([135631196], ways, nodes))
        features = prepared([root, stitch], ways)
        boundary = MultiPolygon([
            box(-0.1, -1.0, 1.0, 1.0),
            box(3.0, -1.0, 4.1, 1.0),
            box(-0.1, 3.0, 1.1, 5.0),
        ])
        clipped = areas.clip_features_to_area(
            features, boundary, min_inside_mi=0, area_name=AREA)

        kept, removed, report = quality.curate_exact_area(
            clipped, ways=ways, nodes=nodes, area_union=boundary,
            area_name=AREA, region="dk")

        self.assertEqual(kept, [])
        self.assertEqual(len(removed), 1)
        self.assertEqual(report["dropped_root_contamination_count"], 1)
        exclusion = report["dropped_root_contamination"][0]
        self.assertTrue(exclusion["boundary_induced_split"])
        self.assertEqual(exclusion["dropped_roots"], [{
            "root_relation_id": 14190349,
            "shared_way_ids": [135631196],
        }])


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
class DenmarkRoadNameRules(unittest.TestCase):
    """dk-v2 `dk-road-name`: street/forest-road names on road-class ways."""
    def _stitch(self, name, tags, rules="dk-v2", n_ways=1):
        ways = {i + 1: W([2 * i + 1, 2 * i + 2], name=name, **tags)
                for i in range(n_ways)}
        nodes = {}
        for i in range(n_ways):
            nodes[2 * i + 1] = (i, 0)
            nodes[2 * i + 2] = (i + 1, 0)
        trail = T(name, "name-stitch", list(ways),
                  [[nodes[2 * i + 1], nodes[2 * i + 2]] for i in range(n_ways)])
        return curate([trail], ways, nodes, dk_rules=rules)
    def _removed_as_road_name(self, name, tags, rules="dk-v2"):
        kept, removed, report = self._stitch(name, tags, rules)
        return (kept == [] and len(removed) == 1
                and removed[0]["properties"]["removed_category"] == "dk-road-name")
    def test_forest_road_names_on_designated_tracks_are_removed(self):
        track = {"highway": "track", "foot": "designated",
                 "motor_vehicle": "forestry", "tracktype": "grade2"}
        for name in ("Tovej", "Trevej", "Femvej", "Seksvej", "Sandportalle",
                     "Skomagergade", "Strandvej", "Nordre Sanatorievej",
                     "Juvre Strandvej", "Klatmøllevej", "Strandvejen", "Alleen",
                     "Frederik Madsens Allé", "Bjergegade", "Kastaniealle'"):
            with self.subTest(name=name):
                self.assertTrue(self._removed_as_road_name(name, track))
    def test_street_names_on_plain_paths_are_removed(self):
        for name, tags in (
                ("Jagtvej", {"highway": "path"}),
                ("Rosenvej", {"highway": "footway", "surface": "ground"}),
                ("Førby Hedevej", {"highway": "path", "surface": "dirt"}),
                ("Linders Alle", {"highway": "path"}),
                ("Hovedgaden", {"highway": "pedestrian"}),
                ("Bangs Vej", {"highway": "path"}),
                ("Kristoffervejen", {"highway": "path"}),
                ("Strandzugang", {"highway": "path"})):
            with self.subTest(name=name):
                self.assertTrue(self._removed_as_road_name(name, tags))
    def test_report_lists_each_removal_with_its_rule(self):
        _, _, report = self._stitch("Tovej", {"highway": "track", "foot": "yes"})
        self.assertEqual(report["dk_rules"], "dk-v2")
        self.assertEqual(report["dk_road_name_removed"],
                         [{"name": "Tovej", "rule": "road-class-way",
                           "miles": report["dk_road_name_removed"][0]["miles"]}])
        self.assertEqual(report["removed_counts"], {"dk-road-name": 1})
    def test_legacy_rules_keep_every_road_name_and_report_is_unchanged(self):
        for name, tags in (
                ("Tovej", {"highway": "track", "foot": "designated"}),
                ("Jagtvej", {"highway": "path"}),
                ("Strandzugang", {"highway": "path"})):
            with self.subTest(name=name):
                kept, removed, report = self._stitch(name, tags, rules="legacy")
                self.assertEqual(len(kept), 1)
                self.assertEqual(removed, [])
                self.assertNotIn("dk_rules", report)
                self.assertNotIn("dk_road_name_removed", report)
    def test_unknown_rule_set_fails_closed(self):
        with self.assertRaises(ValueError):
            self._stitch("Tovej", {"highway": "track", "foot": "yes"}, rules="v3")
    def test_real_trail_names_and_hiking_tags_are_never_removed(self):
        track = {"highway": "track", "foot": "designated"}
        for name, tags in (
                ("Ridevejen", track), ("Oksevejen", track),
                ("Hærvej Sti", track), ("Kirkevej Sti", track),
                ("Strandvej Rute", track),
                ("Naturstien Skovvej", dict(track, sac_scale="hiking")),
                ("Pilgrimsrute Vestsjælland", track),
                ("Skovvej", dict(track, trail_visibility="good")),
                ("Skovvej", dict(track, hiking="yes")),
                ("Skovvej", dict(track, route="hiking")),
                ("Den Vilde Vej", {"highway": "path"}),
                ("Hulvejen", {"highway": "path"}),
                ("Kongevejen", {"highway": "path"}),
                ("Oldtidsvejen", {"highway": "path"}),
                ("Kanalstien", {"highway": "footway"}),
                ("Det Hvide Spor", {"highway": "path"})):
            with self.subTest(name=name, tags=tags):
                kept, removed, _ = self._stitch(name, tags)
                self.assertEqual(len(kept), 1)
                self.assertEqual(removed, [])
    def test_hiking_relation_backed_road_names_are_never_removed(self):
        nodes = {1: (0, 0), 2: (1, 0)}
        ways = {1: W([1, 2], highway="tertiary", name="Klosterrisvej")}
        relations = {900: {
            "tags": {"type": "route", "route": "hiking", "name": "Klosterrisvej"},
            "members": [("w", 1, "")]}}
        trails = m.assemble(nodes, ways, relations, [], region="dk")
        kept, removed, _ = curate(trails, ways, nodes, dk_rules="dk-v2")
        self.assertEqual([f["properties"]["name"] for f in kept],
                         ["Klosterrisvej"])
        self.assertEqual(removed, [])
    def test_relation_with_in_park_gap_is_still_dropped_under_both_rule_sets(self):
        # Long-distance routes are NOT exempt from the gap gate: a listed-style
        # name with a gap inside the park keeps the existing fail-closed result.
        nodes = {1: (0, 0), 2: (1, 0), 3: (3, 0), 4: (4, 0)}
        ways = {1: W([1, 2], highway="path"), 2: W([3, 4], highway="path")}
        relations = {901: {
            "tags": {"type": "route", "route": "hiking", "name": "Vestkyststien"},
            "members": [("w", 1, ""), ("w", 2, "")]}}
        for rules in ("legacy", "dk-v2"):
            with self.subTest(rules=rules):
                trails = m.assemble(nodes, ways, relations, [], region="dk")
                kept, removed, report = curate(
                    trails, ways, nodes, dk_rules=rules)
                self.assertEqual(kept, [])
                self.assertEqual(
                    [f["properties"]["removed_category"] for f in removed],
                    ["unresolved-relation-gap"])


@unittest.skipUnless(HAVE_SHAPELY, "shapely not installed")
class DenmarkV2QualityRules(unittest.TestCase):
    """dk-v2 only: dk-mtb-trail, dk-non-hiking, dk-long-distance, dk-fragment
    and the extended dk-road-name words (-vejen, -linie, Ride)."""
    def _stitch(self, name, way_tags, rules="dk-v2", n_ways=1, step=0.01,
                relation_tags=None, props=None):
        ways = {i + 1: W([i + 1, i + 2], name=name, **way_tags)
                for i in range(n_ways)}
        nodes = {i + 1: (i * step, 0) for i in range(n_ways + 1)}
        lines = [[nodes[i + 1], nodes[i + 2]] for i in range(n_ways)]
        if relation_tags is None:
            trails = [T(name, "name-stitch", list(ways), lines)]
        else:
            relations = {900: {"tags": dict(relation_tags, type="route",
                                            route="hiking", name=name),
                               "members": [("w", i, "") for i in ways]}}
            trails = m.assemble(nodes, ways, relations, [], region="dk")
        features = prepared(trails, ways)
        for feature in features:
            feature["properties"].update(props or {})
        return quality.curate_exact_area(
            features, ways=ways, nodes=nodes, area_union=box(-2, -2, 20, 20),
            area_name=AREA, region="dk", dk_rules=rules)
    def _category(self, name, way_tags, **kwargs):
        kept, removed, _ = self._stitch(name, way_tags, **kwargs)
        if kept:
            return None
        return removed[0]["properties"]["removed_category"]
    # -- dk-mtb-trail
    def test_mtb_tagged_paths_are_removed_with_the_new_category(self):
        for name, tags in (
                ("Ordinary Name", {"highway": "path", "mtb:scale": "1"}),
                ("Zero Scale", {"highway": "path", "mtb:scale": "0", "foot": "yes"}),
                ("Flow", {"highway": "path", "oneway": "yes"}),
                ("Bike Only", {"highway": "path", "bicycle": "designated"}),
                ("Cycle Lane", {"highway": "cycleway"}),
                ("Dance Floor", {"highway": "path", "mtb:scale:imba": "2"})):
            with self.subTest(name=name):
                self.assertEqual(self._category(name, tags), "dk-mtb-trail")
    def test_mtb_name_needs_an_mtb_tagged_way(self):
        self.assertEqual(self._category(
            "Evil Autobahn", {"highway": "path", "mtb:scale": "2",
                              "trail_visibility": "good"}), "dk-mtb-trail")
        self.assertIsNone(self._category("Evil Autobahn", {"highway": "path"}))
    def test_shared_use_trails_with_hiking_identity_are_kept(self):
        for tags in (
                {"highway": "path", "mtb:scale": "1", "foot": "designated"},
                {"highway": "path", "mtb:scale": "1", "sac_scale": "hiking"},
                {"highway": "path", "mtb:scale": "1", "trail_visibility": "good"},
                {"highway": "path", "bicycle": "designated", "foot": "yes"},
                {"highway": "path", "bicycle": "designated", "foot": "designated"}):
            with self.subTest(tags=tags):
                self.assertIsNone(self._category("Skovstien Syd", tags))
    def test_hiking_route_relation_members_are_never_mtb_removed(self):
        kept, removed, _ = self._stitch(
            "Tour Rute", {"highway": "path", "mtb:scale": "1"},
            relation_tags={"network": "lwn"})
        self.assertEqual([f["properties"]["name"] for f in kept], ["Tour Rute"])
        self.assertEqual(removed, [])
    def test_mtb_rules_do_not_touch_legacy(self):
        kept, removed, report = self._stitch(
            "Flow", {"highway": "path", "mtb:scale": "1"}, rules="legacy")
        self.assertEqual(len(kept), 1)
        self.assertNotIn("dk_quality_removed", report)
    # -- dk-road-name extensions
    def test_forest_ride_words_are_removed_unless_trail_like(self):
        for name, tags in (
                ("Hjortevejen", {"highway": "path"}),
                ("Jydelinie", {"highway": "path"}),
                ("Lumsåslinjen", {"highway": "path"}),
                ("Femvejen", {"highway": "track", "foot": "yes"}),
                ("Langelinie", {"highway": "track", "foot": "yes"}),
                ("Boulevarden", {"highway": "track", "foot": "yes"}),
                ("Nordre Ride", {"highway": "track", "foot": "yes"}),
                ("Lyngmose linie", {"highway": "track", "foot": "yes"})):
            with self.subTest(name=name):
                self.assertEqual(self._category(name, tags, step=0.01),
                                 "dk-road-name")
        for name, tags in (
                ("Den Vilde Vej", {"highway": "path"}),
                ("Kirkestien", {"highway": "path"}),
                ("Hulvejen", {"highway": "path"}),
                ("Skovvejen Sti", {"highway": "path"}),
                ("Hjortevejen", {"highway": "path", "sac_scale": "hiking"}),
                ("Jydelinie", {"highway": "path", "trail_visibility": "good"})):
            with self.subTest(name=name, tags=tags):
                self.assertIsNone(self._category(name, tags))
    # -- dk-non-hiking
    def test_bridle_and_event_routes_are_non_hiking(self):
        self.assertEqual(self._category(
            "Kronprinsessens Ridesti", {"highway": "path", "horse": "yes"}),
            "dk-non-hiking")
        self.assertEqual(self._category(
            "Petersens passage", {"highway": "bridleway", "horse": "designated"}),
            "dk-non-hiking")
        self.assertEqual(self._category(
            "Power trackbane", {"highway": "path", "foot": "yes"}),
            "dk-non-hiking")
        self.assertEqual(self._category(
            "Margueritløbet - Hedehusene - lang", {"highway": "path"},
            relation_tags={"network": "lwn"}), "dk-non-hiking")
        self.assertIsNone(self._category(
            "Skovsti", {"highway": "bridleway", "horse": "designated",
                        "foot": "designated"}))
        self.assertIsNone(self._category("Skovløbersporet", {"highway": "path"}))
    # -- dk-long-distance
    def test_long_distance_legs_are_dropped_by_network_or_whole_name(self):
        leg = {"clipped": True, "full_length_mi": 500.0}
        for name, net in (("Hærvejen", "nwn"), ("Fjordstien Vandresti", "rwn"),
                          ("Sønderskovvej", "iwn"), ("Etape 10b - Gudenåstien", "rwn"),
                          ("Hærvejen", "lwn"), ("Camønoen", "")):
            with self.subTest(name=name, net=net):
                self.assertEqual(self._category(
                    name, {"highway": "path"}, n_ways=2, step=0.5,
                    relation_tags={"network": net} if net else {},
                    props=leg), "dk-long-distance")
    def test_local_walks_named_like_a_route_are_listed_not_dropped(self):
        kept, removed, report = self._stitch(
            "Hærvejen rundt", {"highway": "path"}, n_ways=2, step=0.5,
            relation_tags={"network": "lwn"},
            props={"clipped": True, "full_length_mi": 500.0})
        self.assertEqual([f["properties"]["name"] for f in kept], ["Hærvejen rundt"])
        self.assertEqual(removed, [])
        self.assertEqual([r["name"] for r in report["dk_long_distance_ambiguous"]],
                         ["Hærvejen rundt"])
    def test_whole_regional_route_inside_the_area_is_kept(self):
        kept, removed, _ = self._stitch(
            "Kystruten Mors", {"highway": "path"}, n_ways=2, step=0.5,
            relation_tags={"network": "rwn"})
        self.assertEqual(len(kept), 1)
        self.assertEqual(removed, [])
    # -- dk-fragment
    def test_short_rootless_fragments_go_but_short_relation_trails_stay(self):
        self.assertEqual(self._category("Lille Stump", {"highway": "path"},
                                        step=0.002), "dk-fragment")
        self.assertIsNone(self._category("Lang Nok", {"highway": "path"},
                                         step=0.01))
        kept, removed, _ = self._stitch(
            "Kort Rute", {"highway": "path"}, step=0.002,
            relation_tags={"network": "lwn"})
        self.assertEqual(len(kept), 1)
        self.assertEqual(removed, [])
        kept, _, report = self._stitch("Lille Stump", {"highway": "path"},
                                       step=0.002, rules="legacy")
        self.assertEqual(len(kept), 1)

class DenmarkRulesCli(unittest.TestCase):
    """The real argument parser must accept --dk-rules (dk-run-area.sh passes it)."""
    def _run(self, *extra):
        import subprocess
        return subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parent / "assemble.py"),
             "--in", "x.pbf", "--out", "y.geojson", *extra],
            capture_output=True, text=True)
    def test_flag_is_accepted_by_the_real_parser(self):
        for rules in ("legacy", "dk-v2"):
            with self.subTest(rules=rules):
                result = self._run("--region", "dk", "--dk-rules", rules)
                self.assertNotIn("unrecognized arguments", result.stderr)
                self.assertNotIn("invalid choice", result.stderr)
    def test_unknown_rule_set_is_rejected_by_the_parser(self):
        result = self._run("--dk-rules", "v3")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid choice", result.stderr)
@unittest.skipUnless(HAVE_SHAPELY, "shapely not installed")
class DenmarkCuratedExclude(unittest.TestCase):
    """dk-v2 `dk-curated-exclude`: registry exclude_trails, fail closed."""
    def _two_trails(self, exclude, rules="dk-v2"):
        ways = {1: W([1, 2], highway="path", name="Skovstien"),
                2: W([3, 4], highway="path", name="Engstien")}
        nodes = {1: (0, 0), 2: (0.01, 0), 3: (0, 0.5), 4: (0.01, 0.5)}
        relations = {900: {"tags": {"type": "route", "route": "hiking",
                                    "name": "Skovstien", "network": "lwn"},
                           "members": [("w", 1, "")]}}
        trails = m.assemble(nodes, ways, relations, [], region="dk")
        features = prepared(trails, ways)
        return quality.curate_exact_area(
            features, ways=ways, nodes=nodes, area_union=box(-2, -2, 20, 20),
            area_name=AREA, region="dk", dk_rules=rules, dk_exclude=exclude)
    def test_relation_and_name_entries_remove_exactly_the_listed_trail(self):
        for entry, other in (
                ({"id": "r900", "name": "Skovstien", "reason": "test reason"},
                 "Engstien"),
                ({"id": "n:Engstien", "name": "Engstien", "reason": "why"},
                 "Skovstien")):
            with self.subTest(entry=entry["id"]):
                kept, removed, report = self._two_trails([entry])
                self.assertEqual([k["properties"]["name"] for k in kept], [other])
                props = removed[0]["properties"]
                self.assertEqual(props["removed_category"], "dk-curated-exclude")
                self.assertEqual(props["curated_exclude_reason"], entry["reason"])
                self.assertEqual(report["removed_counts"],
                                 {"dk-curated-exclude": 1})
                self.assertEqual(report["dk_curated_exclude_failures"], [])
                self.assertEqual(report["validation_failures"], [])
                self.assertEqual(report["dk_curated_excluded"][0]["reason"],
                                 entry["reason"])
                self.assertEqual(report["dk_curated_excluded"][0]["name"],
                                 entry["name"])
    def test_stale_id_fails_closed(self):
        for entry in ({"id": "r12345", "name": "Skovstien", "reason": "x"},
                      {"id": "n:Findes Ikke", "name": "Findes Ikke", "reason": "x"}):
            kept, removed, report = self._two_trails([entry])
            self.assertEqual(len(kept), 2)
            self.assertEqual(removed, [])
            self.assertEqual(len(report["dk_curated_exclude_failures"]), 1)
            self.assertTrue(any("dk-curated-exclude" in item
                                for item in report["validation_failures"]))
    def test_name_must_match_the_relation_trail(self):
        _, _, report = self._two_trails(
            [{"id": "r900", "name": "Andet Navn", "reason": "x"}])
        self.assertEqual(len(report["dk_curated_exclude_failures"]), 1)
    def test_trail_already_removed_by_an_automatic_rule_is_stale(self):
        # "Hjortevejen" on a plain path is dk-road-name; listing it is a stale entry.
        ways = {1: W([1, 2], highway="path", name="Hjortevejen")}
        nodes = {1: (0, 0), 2: (0.01, 0)}
        features = prepared(
            [T("Hjortevejen", "name-stitch", [1], [[nodes[1], nodes[2]]])], ways)
        kept, removed, report = quality.curate_exact_area(
            features, ways=ways, nodes=nodes, area_union=box(-2, -2, 20, 20),
            area_name=AREA, region="dk", dk_rules="dk-v2",
            dk_exclude=[{"id": "n:Hjortevejen", "name": "Hjortevejen",
                         "reason": "x"}])
        self.assertEqual(removed[0]["properties"]["removed_category"],
                         "dk-road-name")
        self.assertEqual(len(report["dk_curated_exclude_failures"]), 1)
    def test_malformed_entries_and_legacy_rules_are_rejected(self):
        good = {"id": "r900", "name": "Skovstien", "reason": "x"}
        for bad in ([{**good, "id": "900"}], [{**good, "reason": " "}],
                    [{**good, "extra": 1}], [good, good], "r900",
                    [{"id": "n:A", "name": "B", "reason": "x"}]):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    self._two_trails(bad)
        with self.assertRaises(ValueError):
            self._two_trails([good], rules="legacy")
    def test_no_exclusions_leaves_dk_v2_output_unchanged(self):
        kept, removed, report = self._two_trails([])
        self.assertEqual(len(kept), 2)
        self.assertEqual(report["dk_curated_excluded"], [])
@unittest.skipUnless(HAVE_SHAPELY, "shapely not installed")
class DenmarkCuratedExcludeAll(unittest.TestCase):
    """`n*:<name>` opt-in: every kept trail with that exact name; n: stays exactly-1."""
    def _stitches(self, exclude, count=2):
        ways, nodes, trails = {}, {}, []
        for i in range(count):
            ways[i + 1] = W([2 * i + 1, 2 * i + 2], highway="path", name="Aaro")
            nodes[2 * i + 1], nodes[2 * i + 2] = (0, 0.5 * i), (0.01, 0.5 * i)
        ways[99] = W([97, 98], highway="path", name="Engstien")
        nodes[97], nodes[98] = (0, 9), (0.01, 9)
        relations = {900: {"tags": {"type": "route", "route": "hiking",
                                    "name": "Engstien", "network": "lwn"},
                           "members": [("w", 99, "")]}}
        trails = m.assemble(nodes, ways, relations, [], region="dk")
        features = prepared(trails, ways)
        return quality.curate_exact_area(
            features, ways=ways, nodes=nodes, area_union=box(-2, -2, 20, 20),
            area_name=AREA, region="dk", dk_rules="dk-v3", dk_exclude=exclude)
    def test_multi_selector_removes_every_same_named_trail_only(self):
        entry = {"id": "n*:Aaro", "name": "Aaro", "reason": "stitched twice"}
        kept, removed, report = self._stitches([entry])
        self.assertEqual([k["properties"]["name"] for k in kept], ["Engstien"])
        self.assertGreaterEqual(len(removed), 2)
        self.assertEqual(report["dk_curated_exclude_failures"], [])
        self.assertTrue(all(r["properties"]["removed_category"] == "dk-curated-exclude"
                            for r in removed))
        self.assertEqual({e["name"] for e in report["dk_curated_excluded"]}, {"Aaro"})
    def test_multi_selector_zero_match_fails_closed(self):
        _, removed, report = self._stitches(
            [{"id": "n*:Findes Ikke", "name": "Findes Ikke", "reason": "x"}])
        self.assertEqual(removed, [])
        self.assertEqual(len(report["dk_curated_exclude_failures"]), 1)
    def test_exact_selector_still_demands_exactly_one(self):
        kept, removed, report = self._stitches(
            [{"id": "n:Aaro", "name": "Aaro", "reason": "x"}])
        self.assertEqual(removed, [])
        self.assertEqual(len(report["dk_curated_exclude_failures"]), 1)
        self.assertIn("expected exactly 1", report["dk_curated_exclude_failures"][0])
    def test_exact_selector_single_match_unchanged(self):
        kept, removed, report = self._stitches(
            [{"id": "n:Engstien", "name": "Engstien", "reason": "x"}])
        self.assertEqual(sorted(k["properties"]["name"] for k in kept), ["Aaro", "Aaro"])
        self.assertEqual(len(removed), 1)
    def test_validation(self):
        good = {"id": "n*:Aaro", "name": "Aaro", "reason": "x"}
        self.assertEqual(quality.validate_dk_exclude([good])[0]["id"], "n*:Aaro")
        for bad in ([{**good, "name": "Other"}], [{**good, "id": "n*:"}]):
            with self.assertRaises(ValueError):
                quality.validate_dk_exclude(bad)
        with self.assertRaises(ValueError):  # exclude-only: never valid as an include
            quality.validate_dk_exclude([good], "include")
class DenmarkCuratedExcludeCli(unittest.TestCase):
    """The real argument parser validates --dk-curated-exclude before reading any PBF."""
    def _run(self, payload, *extra):
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ex.json"
            path.write_text(payload, encoding="utf-8")
            return subprocess.run(
                [sys.executable,
                 str(Path(__file__).resolve().parent / "assemble.py"),
                 "--in", str(Path(tmp) / "missing.pbf"), "--out", "y.geojson",
                 "--region", "dk", "--dk-curated-exclude", str(path), *extra],
                capture_output=True, text=True)
    GOOD = '[{"id": "r900", "name": "A", "reason": "r"}]'
    def test_legacy_rules_reject_the_flag(self):
        result = self._run(self.GOOD)
        self.assertEqual(result.returncode, 2)
        self.assertIn("requires --dk-rules dk-v2", result.stderr)
    def test_malformed_file_is_rejected(self):
        for payload in ("not json", '{"id": 1}', '[{"id": "x"}]'):
            with self.subTest(payload=payload):
                result = self._run(payload, "--dk-rules", "dk-v2")
                self.assertEqual(result.returncode, 2)
                self.assertIn("--dk-curated-exclude is invalid", result.stderr)
    def test_valid_file_passes_the_parser(self):
        result = self._run(self.GOOD, "--dk-rules", "dk-v2")
        self.assertNotIn("is invalid", result.stderr)
        self.assertNotIn("requires --dk-rules", result.stderr)
        self.assertNotIn("unrecognized arguments", result.stderr)


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
