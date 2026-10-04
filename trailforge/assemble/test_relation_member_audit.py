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
        self.assertIn("excluded-direct-member",
                      report["relation_member_unresolved"][0]["reasons"])
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
            "reasons": ["entirely-filtered-route", "excluded-direct-member"],
            "excluded_way_ids": [10],
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

    def test_removed_thru_hike_exclusion_fails_without_published_geometry(self):
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
        self.assertEqual(report["relation_member_unresolved_count"], 2)
        self.assertEqual(
            [row["relation_id"]
             for row in report["relation_member_unresolved"]],
            [700, 701])
        for audit, unresolved in zip(
                report["relation_member_audit"],
                report["relation_member_unresolved"]):
            self.assertEqual(audit["review_status"], "unresolved")
            self.assertEqual(
                audit["unresolved_reasons"], ["excluded-direct-member"])
            self.assertEqual(unresolved["assembly_status"],
                             "removed-thru-hike")
            self.assertEqual(unresolved["excluded_way_ids"], [11])
            self.assertEqual(unresolved["reasons"],
                             ["excluded-direct-member"])
        self.assertEqual(len(report["validation_failures"]), 2)

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
