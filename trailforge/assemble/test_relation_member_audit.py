"""Producer controls for complete Denmark relation-member audit evidence."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import model  # noqa: E402
import quality  # noqa: E402

try:
    from shapely.geometry import box
    HAVE_SHAPELY = True
except ImportError:
    HAVE_SHAPELY = False

_AREA = "Nationalpark Mols Bjerge"


def _way(nodes, **tags):
    return {"nodes": list(nodes), "tags": tags}


def _assemble(ways, nodes, members, *, relation_id=700):
    audits = []
    relations = {relation_id: {
        "tags": {"type": "route", "route": "hiking",
                 "name": "Audited Route"},
        "members": members,
    }}
    trails = model.assemble(
        nodes, ways, relations, [], region="dk",
        collect_relation_member_audit=audits)
    return trails, audits


def _curate(trails, audits, ways, nodes):
    features = [trail.to_feature() for trail in trails]
    quality.prepare_feature_sources(features, trails, ways)
    for feature in features:
        feature["properties"]["area"] = _AREA
    return quality.curate_exact_area(
        features, ways=ways, nodes=nodes,
        area_union=box(-1, -1, 10, 1), area_name=_AREA, region="dk",
        relation_member_audit=audits)


@unittest.skipUnless(HAVE_SHAPELY, "shapely not installed")
class RelationMemberAudit(unittest.TestCase):
    def test_truncated_emitted_route_records_exclusion_and_fails(self):
        nodes = {1: (0, 0), 2: (1, 0), 3: (2, 0)}
        ways = {
            10: _way([1, 2], highway="path"),
            11: _way([2, 3], highway="path", access="private"),
        }
        trails, audits = _assemble(
            ways, nodes, [("w", 10, ""), ("w", 11, "")])
        self.assertEqual(len(trails), 1)
        self.assertEqual(trails[0].member_ways, [10])
        self.assertEqual(trails[0].direct_relation_ways, {700: [10, 11]})
        kept, removed, report = _curate(trails, audits, ways, nodes)
        self.assertEqual(len(kept), 1)
        self.assertEqual(removed, [])
        member = report["relation_member_audit"][0]["direct_way_members"][1]
        self.assertEqual(member["way_id"], 11)
        self.assertEqual(member["tags"], {
            "highway": "path", "access": "private",
        })
        self.assertEqual(member["role"], "")
        self.assertEqual(member["status"], "excluded")
        self.assertEqual(member["exclusion_reason"], "access-private")
        self.assertEqual(report["relation_member_unresolved_count"], 1)
        self.assertIn("unsafe-member-in-exact-area",
                      report["relation_member_unresolved"][0]["reasons"])
        self.assertEqual(
            report["relation_member_unresolved"][0]["terminal_way_ids"], [11])
        self.assertTrue(report["validation_failures"])

    def test_entirely_filtered_route_cannot_disappear(self):
        nodes = {1: (0, 0), 2: (1, 0)}
        ways = {10: _way([1, 2], highway="path", foot="private")}
        trails, audits = _assemble(ways, nodes, [("w", 10, "main")])
        self.assertEqual(trails, [])
        self.assertEqual(audits[0]["assembly_status"], "entirely-filtered")
        kept, removed, report = _curate(trails, audits, ways, nodes)
        self.assertEqual(kept, [])
        self.assertEqual(removed, [])
        self.assertEqual(report["relation_member_unresolved"], [{
            "relation_id": 700,
            "name": "Audited Route",
            "assembly_status": "entirely-filtered",
            "reasons": ["unsafe-member-in-exact-area",
                        "entirely-filtered-route"],
            "excluded_way_ids": [10],
            "terminal_way_ids": [10],
            "missing_way_ids": [],
            "missing_relation_ids": [],
        }])
        self.assertTrue(report["validation_failures"])

    def test_missing_direct_way_is_unavailable_and_fails_closed(self):
        nodes = {1: (0, 0), 2: (1, 0)}
        ways = {10: _way([1, 2], highway="path")}
        trails, audits = _assemble(
            ways, nodes, [("w", 10, ""), ("w", 99, "")])
        self.assertEqual(trails[0].direct_relation_ways, {700: [10, 99]})
        _, _, report = _curate(trails, audits, ways, nodes)
        missing = report["relation_member_audit"][0]["direct_way_members"][1]
        self.assertEqual(missing["source_status"], "missing")
        self.assertEqual(missing["status"], "excluded")
        self.assertEqual(missing["exclusion_reason"], "missing-source-way")
        self.assertEqual(
            report["relation_member_unresolved"][0]["missing_way_ids"], [99])
        self.assertTrue(report["validation_failures"])

    def test_full_safe_membership_is_accepted(self):
        nodes = {1: (0, 0), 2: (1, 0), 3: (2, 0)}
        ways = {
            10: _way([1, 2], highway="path"),
            11: _way([2, 3], highway="footway"),
        }
        trails, audits = _assemble(
            ways, nodes, [("w", 10, ""), ("w", 11, "main")])
        _, _, report = _curate(trails, audits, ways, nodes)
        self.assertEqual(report["relation_member_unresolved_count"], 0)
        self.assertEqual(report["relation_member_unresolved"], [])
        self.assertEqual(report["relation_member_audit"][0]["review_status"],
                         "accepted")
        self.assertEqual(report["validation_failures"], [])

    def test_removed_thru_hike_exclusion_is_visible_but_informational(self):
        nodes = {1: (0, 0), 2: (1, 0), 3: (2, 0)}
        ways = {
            10: _way([1, 2], highway="path", name="Signed Superroute"),
            11: _way([2, 3], highway="path", access="private"),
        }
        relations = {
            700: {
                "tags": {"type": "route", "route": "hiking",
                         "name": "Signed Superroute"},
                "members": [("r", 701, "")],
            },
            701: {
                "tags": {"type": "route", "route": "hiking",
                         "name": "Signed Superroute"},
                "members": [("w", 10, ""), ("w", 11, "")],
            },
        }
        audits = []
        trails = model.assemble(
            nodes, ways, relations, [], region="dk",
            collect_relation_member_audit=audits)
        self.assertEqual(trails, [])
        self.assertEqual(
            [audit["assembly_status"] for audit in audits],
            ["removed-thru-hike", "removed-thru-hike"])

        kept, removed, report = _curate(trails, audits, ways, nodes)
        self.assertEqual(kept, [])
        self.assertEqual(removed, [])
        self.assertEqual(report["relation_member_unresolved_count"], 0)
        self.assertEqual(report["relation_member_unresolved"], [])
        for audit in report["relation_member_audit"]:
            self.assertEqual(audit["review_status"], "accepted")
            self.assertEqual(audit["unresolved_reasons"], [])
            excluded = [record for record in audit["direct_way_members"]
                        if record["status"] == "excluded"]
            self.assertEqual(excluded[0]["render_disposition"],
                             "removed-thru-hike-informational")
            self.assertIsNone(excluded[0]["terminal_reason"])
        self.assertEqual(report["validation_failures"], [])

    def test_three_scope_exact_area_decision_matrix(self):
        inside = [[0.0, 0.0], [1.0, 0.0]]
        outside = [[5.0, 0.0], [6.0, 0.0]]
        cases = (
            ("removed-raw-missing", "removed-thru-hike", "missing",
             "missing", "missing", None, "main", None,
             "removed-thru-hike-informational"),
            ("relevant-raw-missing", "emitted", "missing", "missing",
             "missing", None, "main", "raw-missing-relevant-member",
             "raw-source-unavailable"),
            ("prefilter-loss-inside", "emitted", "present", "missing",
             "missing", inside, "main", "prefilter-data-loss",
             "prefilter-data-loss"),
            ("prefilter-absence-outside", "emitted", "present", "missing",
             "missing", outside, "main", None,
             "out-of-area-prefilter-absence"),
            ("aoi-loss-inside", "emitted", "present", "present", "missing",
             inside, "main", "aoi-extraction-loss", "aoi-extraction-loss"),
            ("aoi-absence-outside", "emitted", "present", "present", "missing",
             outside, "main", None, "out-of-aoi-informational"),
            ("unsafe-inside", "emitted", "present", "present", "present",
             inside, "main", "unsafe-member-in-exact-area",
             "unsafe-member-in-exact-area"),
            ("unsafe-outside", "emitted", "present", "present", "present",
             outside, "main", None, "unsafe-outside-exact-area"),
            ("variant-inside", "emitted", "present", "present", "present",
             inside, "excursion", None, "variant-role-informational"),
        )
        for (label, assembly_status, raw_status, prefilter_status, aoi_status,
             coordinates, role, terminal, disposition) in cases:
            with self.subTest(label=label):
                excluded = label.startswith("unsafe") or label.startswith("variant")
                audit = {
                    "relation_id": 700,
                    "name": "Scoped Route",
                    "tags": {"type": "route", "route": "hiking"},
                    "relation_ids": [700],
                    "relation_scope_statuses": {
                        "700": {"raw_status": "present",
                                "prefilter_status": "present",
                                "aoi_status": "present"}},
                    "direct_relation_way_ids": {700: [11]},
                    "missing_relation_ids": [],
                    "direct_way_members": [{
                        "relation_id": 700,
                        "member_index": 0,
                        "way_id": 11,
                        "role": "" if role == "main" else role,
                        "effective_role": role,
                        "source_status": (
                            "available" if raw_status == "present" else "missing"),
                        "raw_status": raw_status,
                        "prefilter_status": prefilter_status,
                        "aoi_status": aoi_status,
                        "source_node_ids": [1, 2] if coordinates else None,
                        "source_coordinates": coordinates,
                        "source_missing_node_ids": [] if coordinates else None,
                        "tags": ({"highway": "service", "service": "parking_aisle"}
                                 if excluded else {"highway": "path"}),
                        "status": "excluded" if excluded else "included",
                        "exclusion_reason": (
                            "service-forbidden-parking_aisle" if excluded else None),
                        "decision": {
                            "eligible": not excluded,
                            "restored": False,
                            "reason": ("service-forbidden-parking_aisle"
                                       if excluded else "standalone-trail"),
                            "service_tokens": (["parking_aisle"]
                                               if excluded else []),
                            "road_like_track_kind": None,
                        },
                    }],
                    "eligible_main_way_ids": [] if excluded else [11],
                    "included_spur_way_ids": [],
                    "assembly_status": assembly_status,
                }
                _, _, report = quality.curate_exact_area(
                    [], ways={}, nodes={}, area_union=box(-1, -1, 2, 1),
                    area_name=_AREA, region="dk",
                    relation_member_audit=[audit],
                    relation_scope_evidence={"three": "scopes"})
                result = report["relation_member_audit"][0][
                    "direct_way_members"][0]
                self.assertEqual(result["terminal_reason"], terminal)
                self.assertEqual(result["render_disposition"], disposition)
                self.assertEqual(
                    report["relation_member_unresolved_count"],
                    1 if terminal else 0)

    def test_three_scope_assembly_audits_only_aoi_selected_roots(self):
        nodes = {1: (0, 0), 2: (1, 0)}
        ways = {10: _way([1, 2], highway="path")}
        relations = {
            700: {
                "tags": {"type": "route", "route": "hiking",
                         "name": "Selected Parent"},
                "members": [("r", 701, "")],
            },
            701: {
                "tags": {"type": "route", "route": "hiking",
                         "name": "Raw-only Child"},
                "members": [("w", 10, "")],
            },
        }
        way_evidence = {
            "way_id": 10, "status": "present",
            "tags": {"highway": "path"}, "node_ids": [1, 2],
            "coordinates": [[0, 0], [1, 0]], "missing_node_ids": [],
        }
        relation_evidence = {
            700: {"direct_ways": {}},
            701: {"direct_ways": {10: way_evidence}},
        }
        scopes = {
            scope: {
                "root_relation_ids": [700],
                "relations": relation_evidence,
            }
            for scope in ("raw-denmark", "prefiltered-denmark", "aoi")
        }
        audits = []

        model.assemble(
            nodes, ways, relations, [], region="dk",
            collect_relation_member_audit=audits,
            relation_scope_evidence=scopes)

        self.assertEqual([audit["relation_id"] for audit in audits], [700])
        self.assertEqual(audits[0]["relation_ids"], [700, 701])
        self.assertEqual(audits[0]["direct_relation_way_ids"], {
            700: [], 701: [10],
        })

    def test_aoi_absent_child_relation_uses_its_recursive_raw_geometry(self):
        cases = (
            ("outside", "outside", [[5.0, 0.0], [6.0, 0.0]], "present",
             None, "out-of-aoi-relation-informational"),
            ("inside", "intersects", [[0.0, 0.0], [1.0, 0.0]], "present",
             "aoi-relation-loss", "aoi-relation-loss"),
            ("unknown", "unknown", [None, None], "incomplete",
             "relation-location-unknown", "relation-location-unknown"),
        )
        for (label, exact_status, coordinates, way_status, terminal,
             disposition) in cases:
            with self.subTest(label=label):
                way = {
                    "way_id": 11,
                    "status": way_status,
                    "tags": {"highway": "path"},
                    "node_ids": [1, 2],
                    "coordinates": coordinates,
                    "missing_node_ids": ([1, 2]
                                         if way_status == "incomplete" else []),
                }
                raw_relations = {
                    700: {
                        "members": [{
                            "sequence": 0, "type": "relation",
                            "ref": 701, "role": "",
                        }],
                        "direct_ways": {},
                    },
                    701: {
                        "members": [{
                            "sequence": 0, "type": "way",
                            "ref": 11, "role": "main",
                        }],
                        "direct_ways": {11: way},
                    },
                }
                scopes = {
                    "raw-denmark": {"relations": raw_relations},
                    "prefiltered-denmark": {"relations": raw_relations},
                    "aoi": {"relations": {700: raw_relations[700]}},
                }
                audit = {
                    "relation_id": 700,
                    "name": "Parent",
                    "tags": {"type": "route", "route": "hiking"},
                    "relation_ids": [700, 701],
                    "relation_scope_statuses": {
                        "700": {"raw_status": "present",
                                "prefilter_status": "present",
                                "aoi_status": "present"},
                        "701": {"raw_status": "present",
                                "prefilter_status": "present",
                                "aoi_status": "missing"},
                    },
                    "direct_relation_way_ids": {700: [], 701: [11]},
                    "missing_relation_ids": [],
                    "direct_way_members": [],
                    "eligible_main_way_ids": [11],
                    "included_spur_way_ids": [],
                    "assembly_status": "emitted",
                }
                _, _, report = quality.curate_exact_area(
                    [], ways={}, nodes={}, area_union=box(-1, -1, 2, 1),
                    area_name=_AREA, region="dk",
                    relation_member_audit=[audit],
                    relation_scope_evidence=scopes)
                child = report["relation_member_audit"][0][
                    "relation_scope_statuses"]["701"]
                self.assertEqual(child["exact_area_status"], exact_status)
                self.assertEqual(child["terminal_reason"], terminal)
                self.assertEqual(child["render_disposition"], disposition)
                self.assertEqual(
                    report["relation_member_unresolved_count"],
                    0 if terminal is None else 1)

    def test_outside_gap_evidence_is_bound_only_to_the_audited_root(self):
        base_members = [
            {"way_id": 10, "status": "included", "effective_role": "main",
             "source_node_ids": [1, 2]},
            {"way_id": 11, "status": "included", "effective_role": "main",
             "source_node_ids": [3, 4]},
            {"way_id": 12, "status": "excluded", "effective_role": "main",
             "source_node_ids": [2, 3], "exact_area_status": "outside",
             "terminal_reason": None},
        ]
        for proof_root, other_root in ((700, 701), (701, 700)):
            with self.subTest(proof_root=proof_root):
                audit = {
                    "relation_id": proof_root,
                    "relation_ids": [700, 701],
                    "assembly_status": "emitted",
                    "direct_way_members": base_members,
                }
                accepted = quality._relation_outside_gap_evidence([audit])
                self.assertEqual(accepted, {proof_root})
                self.assertNotIn(other_root, accepted)

    def test_composite_service_decisions_flow_through_real_producer(self):
        cases = (
            ("driveway;parking_aisle", False,
             "service-forbidden-driveway"),
            ("parking_aisle;alley", False,
             "service-forbidden-parking_aisle"),
            (" DRIVEWAY ; parking_aisle ", False,
             "service-forbidden-driveway"),
            ("drive_through", False, "service-forbidden-drive_through"),
            ("drivethrough", False, "service-forbidden-drivethrough"),
            ("parking", False, "service-forbidden-parking"),
            ("parking_space", False, "service-forbidden-parking_space"),
            ("emergency-access", False,
             "service-forbidden-emergency_access"),
            ("bus", False, "service-forbidden-bus"),
            ("unknown", False, "service-unknown-token"),
            ("alley;unknown", False, "service-unknown-composite"),
            (" ALLEY ", True, None),
        )
        for service, included, reason in cases:
            with self.subTest(service=service):
                nodes = {1: (0, 0), 2: (1, 0), 3: (2, 0), 4: (3, 0)}
                ways = {
                    10: _way([1, 2], highway="path"),
                    11: _way([2, 3], highway="service", service=service,
                             foot=" YES "),
                    12: _way([3, 4], highway="path"),
                }
                trails, audits = _assemble(
                    ways, nodes,
                    [("w", 10, ""), ("w", 11, ""), ("w", 12, "")])
                self.assertEqual(11 in trails[0].member_ways, included)
                record = audits[0]["direct_way_members"][1]
                self.assertEqual(record["status"] == "included", included)
                self.assertEqual(record["exclusion_reason"], reason)
                if included:
                    self.assertEqual(trails[0].restored_relation_ways, [11])
                else:
                    self.assertEqual(trails[0].restored_relation_ways, [])


if __name__ == "__main__":
    unittest.main()
