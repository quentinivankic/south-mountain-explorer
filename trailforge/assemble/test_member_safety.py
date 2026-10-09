"""Parity and policy controls for shared Denmark relation-member safety."""
from __future__ import annotations

import importlib.util
from itertools import product
from pathlib import Path
import sys
import unittest

_HERE = Path(__file__).resolve().parent
_TOOLS = _HERE.parent / "tools"
sys.path.insert(0, str(_HERE))
import member_safety as safety  # noqa: E402
import model  # noqa: E402

_SPEC = importlib.util.spec_from_file_location(
    "member_safety_validator_module", _TOOLS / "validate_publish_pilot.py")
validator = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(validator)


class SharedMemberSafety(unittest.TestCase):
    def assert_decision(self, tags, *, eligible, restored=False,
                        reason=None):
        decision = safety.dk_relation_member_decision(tags)
        self.assertEqual(decision.eligible, eligible, tags)
        self.assertEqual(decision.restored, restored, tags)
        if reason is not None:
            self.assertEqual(decision.reason, reason, tags)
        self.assertEqual(model._relation_member_decision(tags, "dk"), decision)
        self.assertEqual(validator._relation_member_decision(tags), decision)
        self.assertEqual(model._relation_member_walkable(tags, " DK "), eligible)
        self.assertEqual(validator._relation_member_should_render(tags), eligible)
        self.assertEqual(validator._is_expected_restored_member(tags), restored)

    def test_standalone_pedestrian_area_candidate_matrix(self):
        base = {"name": "Maltgården", "highway": "pedestrian"}
        self.assertTrue(safety.standalone_pedestrian_area_candidate(
            base, closed=True, relation_claimed=False))
        self.assertTrue(safety.standalone_pedestrian_area_candidate(
            {**base, "area": "yes", "surface": "sett", "lit": "yes"},
            closed=True, relation_claimed=False))
        for tags, closed, claimed in (
                ({"highway": "pedestrian", "area": "yes"}, True, False),
                ({**base, "area": "no"}, True, False),
                (base, False, False),
                (base, True, True)):
            with self.subTest(tags=tags, closed=closed, claimed=claimed):
                self.assertFalse(safety.standalone_pedestrian_area_candidate(
                    tags, closed=closed, relation_claimed=claimed))

    def test_service_tokens_use_one_fail_closed_allowlist(self):
        denied = {
            "driveway;parking_aisle": "service-forbidden-driveway",
            "parking_aisle;alley": "service-forbidden-parking_aisle",
            "  DriveWay ; PARKING_AISLE  ": "service-forbidden-driveway",
            "drive_through": "service-forbidden-drive_through",
            "drive-through": "service-forbidden-drive_through",
            "drive through": "service-forbidden-drive_through",
            "drivethrough": "service-forbidden-drivethrough",
            "parking": "service-forbidden-parking",
            "parking_space": "service-forbidden-parking_space",
            "parking-space": "service-forbidden-parking_space",
            "emergency-access": "service-forbidden-emergency_access",
            "emergency_access": "service-forbidden-emergency_access",
            "bus": "service-forbidden-bus",
            "unknown_form": "service-unknown-token",
            "service": "service-unknown-token",
            "alley;unknown_form": "service-unknown-composite",
            "alley;;alley": "service-blank-token",
            "   ": "service-blank-token",
        }
        for service, reason in denied.items():
            for foot in safety.POSITIVE_FOOT_VALUES:
                with self.subTest(service=service, foot=foot):
                    self.assert_decision(
                        {"highway": "service", "service": service,
                         "foot": foot},
                        eligible=False, reason=reason)
        for variants, canonical in (
            (("drive through", "drive-through", "drive_through",
              " DRIVE - THROUGH "), "drive_through"),
            (("parking space", "parking-space", "parking_space"),
             "parking_space"),
            (("emergency access", "emergency-access", "emergency_access"),
             "emergency_access"),
        ):
            for value in variants:
                with self.subTest(value=value):
                    self.assertEqual(
                        safety.service_tokens({"service": value}), (canonical,))
        for tags in (
            {"highway": "service", "foot": "yes"},
            {"highway": "service", "service": "alley", "foot": "designated"},
            {"highway": " SERVICE ", "service": " Alley ",
             "foot": " Permissive "},
            {"highway": "service", "service": "alley;ALLEY", "foot": "yes"},
        ):
            with self.subTest(tags=tags):
                self.assert_decision(tags, eligible=True, restored=True)

    def test_service_motor_access_requires_explicit_positive_foot(self):
        for highway in ("service", "unclassified", "living_street"):
            for motor_key in ("motor_vehicle", "motorcar"):
                for motor_value in ("yes", "designated"):
                    for foot in (None, "yes", "designated", "permissive"):
                        tags = {
                            "highway": highway,
                            motor_key: motor_value,
                        }
                        if foot is not None:
                            tags["foot"] = foot
                        with self.subTest(
                                highway=highway, motor_key=motor_key,
                                motor_value=motor_value, foot=foot):
                            if foot is None:
                                self.assert_decision(
                                    tags, eligible=False,
                                    reason="motor-access-without-positive-foot")
                            else:
                                self.assert_decision(
                                    tags, eligible=True, restored=True)

    def test_agency_road_codes_keep_legacy_prefix_semantics(self):
        names = (
            "CR 15 Trail", "FR 236 Cutoff", "BLM 9 Loop", "NV 8 Ridge",
            "CR 15", "FR 23",
        )
        for name in names:
            with self.subTest(name=name):
                tags = {"highway": "track", "name": name}
                self.assertEqual(
                    safety.road_like_track_kind(
                        tags, conservative_lanes=False),
                    "name")
                decision = safety.standalone_member_decision(tags)
                self.assertFalse(decision.eligible)
                self.assertEqual(decision.reason, "road-like-track-name")
                self.assert_decision(
                    {**tags, "foot": "yes"}, eligible=False,
                    reason="road-like-track-name")
        for name in ("NF-418C", "AB 123", "XY-123A"):
            with self.subTest(generic_code=name):
                self.assertEqual(safety.road_like_track_kind({
                    "highway": "track", "name": name,
                }), "name")
        for name in ("GR20", "E5", "AB 123 Trail"):
            with self.subTest(non_road_code=name):
                self.assertIsNone(safety.road_like_track_kind({
                    "highway": "track", "name": name,
                }))

    def test_conservative_lane_components_gate_denmark_restoration(self):
        for lanes in ("1", "01", "1;1", " 1 ; 01 "):
            with self.subTest(allowed=lanes):
                self.assert_decision({
                    "highway": "track", "lanes": lanes,
                    "motor_vehicle": "yes", "foot": "yes",
                }, eligible=True, restored=True)
        rejected = (
            "2", "03", "2;1", "1;2", "two", "2.5", "1;;1", ";1",
            "1;", "0", "1|1",
        )
        for lanes in rejected:
            with self.subTest(rejected=lanes):
                tags = {
                    "highway": "track", "lanes": lanes,
                    "motor_vehicle": "yes", "foot": "yes",
                }
                self.assert_decision(
                    tags, eligible=False, reason="road-like-track-tag")
                self.assertEqual(
                    safety.road_like_track_kind(
                        tags, ignore_motor_access=True,
                        conservative_lanes=True),
                    "tag")
        # The Denmark-only widening must not change historical standalone
        # disposition for malformed lane strings in other regions.
        for lanes in ("2;1", "1;2", "two", "2.5", "1;;1", "1|1"):
            with self.subTest(legacy=lanes):
                tags = {"highway": "track", "lanes": lanes}
                self.assertIsNone(safety.road_like_track_kind(
                    tags, conservative_lanes=False))
                self.assertTrue(safety.standalone_member_decision(tags).eligible)

    def test_mountainbike_spellings_and_normalization_are_identical(self):
        decisions = []
        for name in ("Mountainbike Trail", "Mountain Bike Trail",
                     " MOUNTAINBIKE TRAIL ", " mountain bike trail "):
            decision = safety.dk_relation_member_decision({
                "highway": " Path ", "name": name,
            })
            decisions.append(decision)
            self.assert_decision(
                {"highway": " Path ", "name": name},
                eligible=False, reason="bike-only-name")
        self.assertEqual(len(set(decisions)), 1)
        for name in ("Mountainbike/Hiking Trail",
                     "Foot Trail and Mountain Bike Trail"):
            self.assert_decision(
                {"highway": "path", "name": name}, eligible=True)

    def test_full_existing_decision_table(self):
        allowed = [
            ({"highway": "path"}, False),
            ({"highway": "footway"}, False),
            ({"highway": "steps"}, False),
            ({"highway": "track", "lanes": "1"}, False),
            ({"highway": "track", "name": "Provstskovvej",
              "tracktype": "grade3"}, False),
            ({"highway": "track", "name": "Provstskovvej",
              "foot": "yes"}, False),
            ({"highway": "track", "motor_vehicle": "yes",
              "foot": "yes"}, True),
            ({"highway": "track", "bicycle": "yes", "horse": "yes",
              "motorcar": "yes", "foot": "yes",
              "name": "Provstskovvej"}, True),
            ({"highway": "residential"}, True),
            ({"highway": "residential", "motor_vehicle": "yes"}, True),
            ({"highway": "primary"}, True),
            ({"highway": "tertiary"}, True),
            ({"highway": "unclassified"}, True),
            ({"highway": "living_street"}, True),
            ({"highway": "footway", "footway": "crossing"}, True),
            ({"highway": "pedestrian"}, False),
            ({"highway": "pedestrian", "motor_vehicle": "yes"}, True),
            ({"highway": "pedestrian", "motor_vehicle": "yes",
              "foot": "permissive"}, True),
            ({"highway": "service"}, True),
            ({"highway": "service", "foot": "destination"}, True),
            ({"highway": "service", "foot": "yes"}, True),
            ({"highway": "service", "service": "alley",
              "foot": "designated"}, True),
            ({"highway": "service", "access": "private",
              "foot": "permissive"}, True),
        ]
        denied = [
            {"highway": "motorway"},
            {"highway": "motorway_link"},
            {"highway": "trunk"},
            {"highway": "trunk_link"},
            {"highway": "construction"},
            {"highway": "raceway"},
            {"highway": "footway", "footway": "sidewalk"},
            {"highway": "path", "foot": "no"},
            {"highway": "path", "foot": "private"},
            {"highway": "path", "access": "private"},
            {"highway": "path", "trail": "no"},
            {"highway": "path", "indoor": "yes"},
            {"highway": "path", "piste:type": "downhill"},
            {"highway": "path", "name": "No Hiking"},
            {"highway": "path", "mtb:type": "flow"},
            {"highway": "path", "name": "Downhill Run",
             "mtb:scale:imba": "3"},
            {"highway": "track", "4wd_only": "yes", "foot": "yes"},
            {"highway": "track", "atv": "designated", "foot": "yes"},
            {"highway": "track", "motor_vehicle": "yes"},
            {"highway": "track", "motorcar": "yes"},
            {"highway": "track", "lanes": "2", "foot": "yes"},
            {"highway": "track", "lanes": " 03 ", "foot": "yes"},
            {"highway": " TRACK ", "name": " Synthetic Service Road ",
             "motor_vehicle": " YES ", "foot": " YES "},
            {"highway": "track", "name": "NF-418C", "foot": "yes"},
            {"highway": "track", "name": "3900 East", "foot": "yes"},
            {"highway": "service", "service": "alley"},
            {"highway": "service", "service": "drive-through",
             "foot": "yes"},
            {"highway": "service", "service": "emergency_access",
             "foot": "yes"},
            {"highway": "service", "service": "unknown", "foot": "yes"},
        ]
        for tags, restored in allowed:
            with self.subTest(allowed=tags):
                self.assert_decision(tags, eligible=True, restored=restored)
        for tags in denied:
            with self.subTest(denied=tags):
                self.assert_decision(tags, eligible=False)

    def test_property_matrix_has_exact_producer_validator_parity(self):
        highways = ("path", "track", "service", "residential",
                    "unclassified", "living_street", "pedestrian",
                    "footway", "motorway", "construction")
        names = ("", "Provstskovvej", "Synthetic Service Road", "NF-418C",
                 "CR 15 Trail", "FR 23", "3900 East", "Mountainbike Trail",
                 "Mountain Bike Trail")
        services = (None, "alley", "driveway", "parking_aisle;alley",
                    "driveway;parking_aisle", "alley;unknown_form",
                    "drive_through", "drivethrough", "parking",
                    "parking_space", "emergency-access", "bus", "unknown")
        feet = (None, "yes", "designated", "permissive", "no", "private",
                "destination")
        access_modes = (None, "private", "no")
        motor_modes = (None, ("motor_vehicle", "yes"),
                       ("motorcar", "yes"), ("atv", "designated"))
        lanes = (None, "1", "1;1", "2", "03", "2;1", "1;2", "two",
                 "2.5", "1;;1")
        checked = 0
        # Pair each dense safety dimension with representative values from all
        # other dimensions without constructing an unhelpful full cross product.
        rows = product(highways, names, services, feet,
                       access_modes, motor_modes)
        for index, (highway, name, service, foot, access, motor) in enumerate(rows):
            if index % 17:
                continue
            tags = {"highway": highway}
            for key, value in (("name", name), ("service", service),
                               ("foot", foot), ("access", access)):
                if value is not None and value != "":
                    tags[key] = value
            if motor is not None:
                tags[motor[0]] = motor[1]
            tags["lanes"] = lanes[index % len(lanes)] or ""
            shared = safety.dk_relation_member_decision(tags)
            self.assertEqual(model._relation_member_decision(tags, "dk"), shared)
            self.assertEqual(validator._relation_member_decision(tags), shared)
            checked += 1
        self.assertGreater(checked, 1000)

    def test_policy_constants_and_regexes_are_not_duplicated(self):
        model_source = (_HERE / "model.py").read_text(encoding="utf-8")
        validator_source = (_TOOLS / "validate_publish_pilot.py").read_text(
            encoding="utf-8")
        for duplicate in ("_DK_REJECTED_RELATION_SERVICE_TYPES",
                          "_REJECTED_RELATION_SERVICE_TYPES",
                          "_RESTORABLE_RELATION_HIGHWAYS",
                          "_BIKE_ONLY_NAME = re.compile",
                          "_ROAD_LIKE_TRACK_NAME = re.compile"):
            self.assertNotIn(duplicate, model_source)
            self.assertNotIn(duplicate, validator_source)


if __name__ == "__main__":
    unittest.main()
