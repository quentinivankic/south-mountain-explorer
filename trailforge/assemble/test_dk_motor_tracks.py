"""dk-v3 keep_motor_tracks: a reviewed per-relation opt-in that keeps the member
tracks of a signed route tagged motorcar=yes or motor_vehicle=yes with no foot tag.

Layers: the pure member decision (and one source mutation per gate), the model
assembly (opt-in scoped to the named relation), and the real assemble.py CLI on a
synthetic OSM file. Every opt-in has its no-opt-in control, fail-closed negatives
and an unrelated-route control. Nothing here touches non-Danish code paths other
than to prove they are unchanged or refused."""
import json
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import member_safety as safety  # noqa: E402
import model  # noqa: E402
import quality  # noqa: E402
from test_dk_v3 import (AREA, AREA_RELATION, HAVE_DEPS, LAT0, LAT1,  # noqa: E402
                        LON0, LON1, Osm)

REASON = "restored-opt-in-motor-track"
PATH = {"highway": "path"}
TRACK = {"highway": "track"}

# (label, tags, default decision (eligible, restored, reason),
#  opt-in decision (eligible, restored, reason))
KEPT = (True, True, REASON)
DEFAULT_MOTOR = (False, False, "motor-access-without-positive-foot")
MATRIX = (
    ("motorcar=yes", {**TRACK, "motorcar": "yes"}, DEFAULT_MOTOR, KEPT),
    ("motor_vehicle=yes", {**TRACK, "motor_vehicle": "yes"}, DEFAULT_MOTOR, KEPT),
    ("both yes", {**TRACK, "motorcar": "yes", "motor_vehicle": "yes"},
     DEFAULT_MOTOR, KEPT),
    ("case and spaces", {**TRACK, "motorcar": " YES "},
     DEFAULT_MOTOR, KEPT),
    ("blank foot is no foot tag", {**TRACK, "motorcar": "yes", "foot": " "},
     DEFAULT_MOTOR, KEPT),
    ("named Danish road", {**TRACK, "motor_vehicle": "yes", "name": "Horneksvejen"},
     DEFAULT_MOTOR, KEPT),
    ("tracktype and surface", {**TRACK, "motorcar": "yes", "tracktype": "grade4",
                               "surface": "gravel"}, DEFAULT_MOTOR, KEPT),
    ("motorcycle=yes beside motorcar=yes", {**TRACK, "motorcar": "yes",
                                           "motorcycle": "yes"},
     (False, False, "motorized-motorcycle"), KEPT),
    ("motorcycle=yes beside motor_vehicle=yes", {**TRACK, "motor_vehicle": "yes",
                                                "motorcycle": "yes"},
     (False, False, "motorized-motorcycle"), KEPT),
    ("motorcycle=yes alone is no car permission",
     {**TRACK, "motorcycle": "yes"},
     (False, False, "motorized-motorcycle"),
     (False, False, "motorized-motorcycle")),
    ("motorcycle=no beside motorcar=yes", {**TRACK, "motorcar": "yes",
                                          "motorcycle": "no"},
     DEFAULT_MOTOR, DEFAULT_MOTOR),
    ("atv stays out beside motorcycle=yes", {**TRACK, "motorcar": "yes",
                                            "motorcycle": "yes", "atv": "yes"},
     (False, False, "motorized-atv"), (False, False, "motorized-atv")),
    ("ohv stays out beside motorcycle=yes", {**TRACK, "motor_vehicle": "yes",
                                            "motorcycle": "yes", "ohv": "yes"},
     (False, False, "motorized-ohv"), (False, False, "motorized-ohv")),
    ("motorcycle=designated stays out", {**TRACK, "motorcar": "yes",
                                        "motorcycle": "designated"},
     (False, False, "motorized-motorcycle"),
     (False, False, "motorized-motorcycle")),
    ("motor_vehicle=forestry + foot=yes needs no opt-in",
     {**TRACK, "motor_vehicle": "forestry", "foot": "yes"},
     (True, False, "standalone-trail"), (True, False, "standalone-trail")),
    ("foot=yes shared track is unchanged",
     {**TRACK, "motorcar": "yes", "foot": "yes"},
     (True, True, "restored-shared-motor-track"),
     (True, True, "restored-shared-motor-track")),
    ("plain track is unchanged", dict(TRACK),
     (True, False, "standalone-trail"), (True, False, "standalone-trail")),
    # gate: highway=track only
    ("path", {**PATH, "motorcar": "yes"}, DEFAULT_MOTOR, DEFAULT_MOTOR),
    ("unclassified", {"highway": "unclassified", "motorcar": "yes"},
     DEFAULT_MOTOR, DEFAULT_MOTOR),
    ("service", {"highway": "service", "motor_vehicle": "yes"},
     (False, False, "motor-access-without-positive-foot"),
     (False, False, "motor-access-without-positive-foot")),
    ("living_street", {"highway": "living_street", "motorcar": "yes"},
     DEFAULT_MOTOR, DEFAULT_MOTOR),
    # gate: no foot tag
    ("foot=no", {**TRACK, "motorcar": "yes", "foot": "no"},
     (False, False, "foot-no"), (False, False, "foot-no")),
    ("foot=private", {**TRACK, "motorcar": "yes", "foot": "private"},
     (False, False, "foot-private"), (False, False, "foot-private")),
    # gate: yes only, never designated or contradictory
    ("motorcar=designated", {**TRACK, "motorcar": "designated"},
     DEFAULT_MOTOR, DEFAULT_MOTOR),
    ("motor_vehicle=designated", {**TRACK, "motor_vehicle": "designated"},
     DEFAULT_MOTOR, DEFAULT_MOTOR),
    ("yes plus designated", {**TRACK, "motorcar": "yes",
                             "motor_vehicle": "designated"},
     DEFAULT_MOTOR, DEFAULT_MOTOR),
    ("motor_vehicle=yes, motorcar=no", {**TRACK, "motor_vehicle": "yes",
                                        "motorcar": "no"},
     DEFAULT_MOTOR, DEFAULT_MOTOR),
    # every other member-safety gate still applies
    ("access=private", {**TRACK, "motorcar": "yes", "access": "private"},
     (False, False, "access-private"), (False, False, "access-private")),
    ("access=no", {**TRACK, "motor_vehicle": "yes", "access": "no"},
     (False, False, "access-no"), (False, False, "access-no")),
    ("lanes=2", {**TRACK, "motorcar": "yes", "lanes": "2"},
     DEFAULT_MOTOR, (False, False, "road-like-track-tag")),
    ("malformed lanes", {**TRACK, "motorcar": "yes", "lanes": "two"},
     DEFAULT_MOTOR, (False, False, "road-like-track-tag")),
    ("English road name", {**TRACK, "motorcar": "yes", "name": "Forest Road"},
     DEFAULT_MOTOR, (False, False, "road-like-track-name")),
    ("agency road code", {**TRACK, "motorcar": "yes", "name": "FR 123"},
     DEFAULT_MOTOR, (False, False, "road-like-track-name")),
    ("atv=yes", {**TRACK, "motorcar": "yes", "atv": "yes"},
     (False, False, "motorized-atv"), (False, False, "motorized-atv")),
    ("motorcycle=designated", {**TRACK, "motor_vehicle": "yes",
                               "motorcycle": "designated"},
     (False, False, "motorized-motorcycle"),
     (False, False, "motorized-motorcycle")),
    ("snowmobile=yes", {**TRACK, "motorcar": "yes", "snowmobile": "yes"},
     (False, False, "motorized-snowmobile"),
     (False, False, "motorized-snowmobile")),
    ("indoor", {**TRACK, "motorcar": "yes", "indoor": "yes"},
     (False, False, "indoor"), (False, False, "indoor")),
    ("trail=no", {**TRACK, "motorcar": "yes", "trail": "no"},
     (False, False, "trail-no"), (False, False, "trail-no")),
    ("mtb flow", {**TRACK, "motorcar": "yes", "mtb:type": "flow"},
     (False, False, "mtb-type"), (False, False, "mtb-type")),
    ("downhill piste", {**TRACK, "motorcar": "yes", "piste:type": "downhill"},
     (False, False, "non-hiking-piste"), (False, False, "non-hiking-piste")),
    ("no hiking name", {**TRACK, "motorcar": "yes", "name": "No hiking here"},
     (False, False, "name-no-hiking"), (False, False, "name-no-hiking")),
    ("mtb name", {**TRACK, "motorcar": "yes", "name": "MTB Loop"},
     (False, False, "bike-only-name"), (False, False, "bike-only-name")),
)


def decision_tuple(decision):
    return (decision.eligible, decision.restored, decision.reason)


def matrix_table(module):
    """Every matrix row, opt-in off then on, for one (possibly mutated) module."""
    out = []
    for _label, tags, _default, _opted in MATRIX:
        out.append(decision_tuple(module.dk_relation_member_decision(tags)))
        out.append(decision_tuple(module.dk_relation_member_decision(
            tags, keep_motor_track=True)))
        out.append(module.motor_track_opt_in_eligible(tags))
    return tuple(out)


class MemberDecisionMatrix(unittest.TestCase):
    def test_every_row_has_the_expected_default_and_opt_in_decision(self):
        for label, tags, default, opted in MATRIX:
            with self.subTest(row=label):
                self.assertEqual(decision_tuple(
                    safety.dk_relation_member_decision(tags)), default)
                self.assertEqual(decision_tuple(
                    safety.dk_relation_member_decision(
                        tags, keep_motor_track=True)), opted)

    def test_default_argument_is_off_and_equals_explicit_false(self):
        for label, tags, default, _opted in MATRIX:
            with self.subTest(row=label):
                self.assertEqual(
                    safety.dk_relation_member_decision(tags),
                    safety.dk_relation_member_decision(
                        tags, keep_motor_track=False))

    def test_model_default_path_is_the_shared_decision(self):
        for label, tags, default, _opted in MATRIX:
            with self.subTest(row=label):
                self.assertEqual(decision_tuple(
                    model._relation_member_decision(tags, "dk")), default)

    def test_non_denmark_member_decision_never_sees_the_opt_in(self):
        for region in (None, "us", "ca"):
            with self.subTest(region=region):
                decision = model._relation_member_decision(
                    {**TRACK, "motorcar": "yes"}, region)
                self.assertNotEqual(decision.reason, REASON)
                self.assertFalse(decision.restored)

    def test_opt_in_is_the_only_reason_that_reads_restored_opt_in(self):
        for label, tags, default, opted in MATRIX:
            if default[2] == REASON:
                self.fail(label)
        self.assertEqual(safety.MOTOR_TRACK_OPT_IN_REASON, REASON)

    def test_eligibility_helper_accepts_only_the_narrow_shape(self):
        # The shape only: the other member-safety gates are not its business.
        accepted = {label for label, tags, _d, _o in MATRIX
                    if safety.motor_track_opt_in_eligible(tags)}
        self.assertEqual(accepted, {
            "motorcar=yes", "motor_vehicle=yes", "both yes",
            "case and spaces", "blank foot is no foot tag",
            "named Danish road", "tracktype and surface",
            "access=private", "access=no", "lanes=2", "malformed lanes",
            "English road name", "agency road code", "atv=yes",
            "motorcycle=yes beside motorcar=yes",
            "motorcycle=yes beside motor_vehicle=yes",
            "atv stays out beside motorcycle=yes",
            "ohv stays out beside motorcycle=yes",
            "snowmobile=yes", "indoor", "trail=no",
            "mtb flow", "downhill piste", "no hiking name", "mtb name",
        })


# Each mutant edits the module text once. Every mutant must change the matrix
# table; a surviving mutant fails the test (one per gate of the opt-in).
SAFETY_SOURCE = (HERE / "member_safety.py").read_text(encoding="utf-8")
MUTANTS = (
    ("gate: highway must be a track",
     '    if normalize(tags.get("highway")) != "track":\n        return False\n',
     ''),
    ("gate: any foot tag blocks the opt-in",
     '    if normalize(tags.get("foot")):\n        return False\n',
     ''),
    ("gate: a motor tag is required",
     '"yes" in values and values <= {"yes", ""}',
     'values <= {"yes", ""}'),
    ("gate: designated and no stay out",
     '"yes" in values and values <= {"yes", ""}',
     '"yes" in values'),
    ("gate: a non-yes value stays out (yes only)",
     '"yes" in values and values <= {"yes", ""}',
     'bool(values - {""})'),
    ("gate: the motor_vehicle key is read",
     'for key in ("motor_vehicle", "motorcar")}',
     'for key in ("motorcar",)}'),
    ("gate: the motorcar key is read",
     'for key in ("motor_vehicle", "motorcar")}',
     'for key in ("motor_vehicle",)}'),
    ("gate: motorcycle must be yes or blank",
     '    if normalize(tags.get("motorcycle")) not in {"", "yes"}:\n        return False\n',
     ''),
    ("gate: only a matching motorcycle=yes is accepted",
     'not in {"", "yes"}:\n        return False', 'not in {"", "yes", "designated"}:\n        return False'),
    ("restore: a matching motorcycle=yes is the same permission",
     '        motor_tags = {key: value for key, value in tags.items()\n                      if key != "motorcycle"}',
     '        motor_tags = tags'),
    ("restore: only motorcycle is set aside",
     'if key != "motorcycle"}', 'if key not in ("motorcycle", "atv", "ohv")}'),
    ("restore: motorcycle is set aside only under the opt-in",
     'if opt_in_motor_track and normalize(tags.get("motorcycle")) == "yes":',
     'if normalize(tags.get("motorcycle")) == "yes":'),
    ("flag: default is off",
     "keep_motor_track: bool = False,\n                                restore_roads: bool = False",
     "keep_motor_track: bool = True,\n                                restore_roads: bool = False"),
    ("flag: the opt-in needs the flag",
     "opt_in_motor_track = (keep_motor_track\n                          and motor_track_opt_in_eligible(tags))",
     "opt_in_motor_track = motor_track_opt_in_eligible(tags)"),
    ("flag: the opt-in is honored",
     "            and highway not in motor_default_classes\n            and not opt_in_motor_track):",
     "            and highway not in motor_default_classes):"),
    ("restore: the kept member is returned",
     "        if opt_in_motor_track:\n            return MemberDecision(True, True, MOTOR_TRACK_OPT_IN_REASON)\n",
     ""),
    ("restore: road-like tracks are checked first",
     '    if highway == "track":\n        road_kind = road_like_track_kind(',
     '    if highway == "track":\n        if opt_in_motor_track:\n            return MemberDecision(True, True, MOTOR_TRACK_OPT_IN_REASON)\n        road_kind = road_like_track_kind('),
    ("restore: a kept member reads as restored",
     "return MemberDecision(True, True, MOTOR_TRACK_OPT_IN_REASON)",
     "return MemberDecision(True, False, MOTOR_TRACK_OPT_IN_REASON)"),
    ("restore: the reason string",
     'MOTOR_TRACK_OPT_IN_REASON = "restored-opt-in-motor-track"',
     'MOTOR_TRACK_OPT_IN_REASON = "restored-motor-track"'),
    ("order: the foot=no gate stays before the opt-in",
     '    if foot in {"no", "private"}:\n        return MemberDecision(False, False, f"foot-{foot}")\n',
     ''),
    ("order: the access gate stays before the opt-in",
     '    if access in {"no", "private"} and not explicit_foot:\n        return MemberDecision(False, False, f"access-{access}")\n',
     ''),
)


def load_source(name, filename, source):
    """Exec one (possibly mutated) module text as a fresh module. A dataclass
    needs its module registered while the class body runs."""
    mod = types.ModuleType(name)
    mod.__file__ = str(HERE / filename)
    sys.modules[name] = mod
    try:
        exec(compile(source, mod.__file__, "exec"), mod.__dict__)
    finally:
        del sys.modules[name]
    return mod


def load_mutant(source):
    return load_source("member_safety_mutant", "member_safety.py", source)


class OptInMutations(unittest.TestCase):
    def test_every_mutant_changes_the_matrix(self):
        baseline = matrix_table(safety)
        survivors = []
        for label, old, new in MUTANTS:
            with self.subTest(mutant=label):
                self.assertEqual(SAFETY_SOURCE.count(old), 1, label)
                mutant = load_mutant(SAFETY_SOURCE.replace(old, new))
                try:
                    changed = matrix_table(mutant) != baseline
                except Exception:
                    changed = True
                if not changed:
                    survivors.append(label)
        self.assertEqual(survivors, [])

    def test_the_unmutated_copy_matches_the_real_module(self):
        self.assertEqual(matrix_table(load_mutant(SAFETY_SOURCE)),
                         matrix_table(safety))


def way(nodes, **tags):
    return {"nodes": list(nodes), "tags": tags}


def run_model(ways, nodes, relations, **options):
    audits = []
    trails = model.assemble(
        nodes, ways, relations, [], region="dk",
        collect_relation_member_audit=audits, exact_denmark=True,
        defer_promotion=True, **options)
    return trails, audits


def route(name, members, relation_id):
    return relation_id, {"tags": {"type": "route", "route": "hiking",
                                  "name": name, "network": "lwn"},
                         "members": [("w", ref, "") for ref in members]}


class ModelScope(unittest.TestCase):
    """Two routes share the same motor track; the opt-in names one of them."""

    def build(self):
        nodes = {1: (9.00, 56.0), 2: (9.01, 56.0), 3: (9.02, 56.0),
                 4: (9.03, 56.0), 5: (9.01, 56.01), 6: (9.01, 56.02)}
        ways = {
            10: way([1, 2], **PATH),
            11: way([2, 3], **TRACK, motorcar="yes", name="Skovvejen"),
            12: way([3, 4], **PATH),
            13: way([2, 5], **PATH),
            14: way([5, 6], **PATH),
        }
        relations = dict([route("Rute Et", [10, 11, 12], 701),
                          route("Rute To", [13, 14, 11], 702)])
        return ways, nodes, relations

    def test_default_drops_the_motor_track_from_both_routes(self):
        trails, audits = run_model(*self.build())
        by_rel = {a["relation_id"]: a for a in audits}
        for rid in (701, 702):
            self.assertEqual(by_rel[rid]["eligible_main_way_ids"].count(11), 0)
        for trail in trails:
            self.assertEqual(trail.restored_relation_ways, [])

    def test_opt_in_keeps_the_track_only_for_the_named_relation(self):
        trails, audits = run_model(*self.build(),
                                   dk_keep_motor_tracks=frozenset({701}))
        by_rel = {a["relation_id"]: a for a in audits}
        self.assertIn(11, by_rel[701]["eligible_main_way_ids"])
        self.assertNotIn(11, by_rel[702]["eligible_main_way_ids"])
        record = next(r for r in by_rel[701]["direct_way_members"]
                      if r["way_id"] == 11)
        self.assertEqual(record["status"], "included")
        self.assertEqual(record["decision"]["reason"], REASON)
        self.assertTrue(record["decision"]["restored"])
        other = next(r for r in by_rel[702]["direct_way_members"]
                     if r["way_id"] == 11)
        self.assertEqual(other["status"], "excluded")
        self.assertEqual(other["exclusion_reason"],
                         "motor-access-without-positive-foot")
        names = {t.name: t for t in trails}
        self.assertEqual(names["Rute Et"].restored_relation_ways, [11])
        self.assertEqual(names["Rute To"].restored_relation_ways, [])
        self.assertIn(11, names["Rute Et"].member_ways)
        self.assertNotIn(11, names["Rute To"].member_ways)

    def test_audit_evidence_names_the_restored_way(self):
        _, audits = run_model(*self.build(),
                              dk_keep_motor_tracks=frozenset({701}))
        failures, evidence = quality.dk_keep_motor_tracks_evidence(
            [{"relation": "r701", "reason": "reviewed"}], audits)
        self.assertEqual(failures, [])
        self.assertEqual(evidence, {"r701": {"reason": "reviewed",
                                             "restored_way_ids": [11]}})

    def test_evidence_fails_closed_for_a_stale_or_absent_relation(self):
        _, audits = run_model(*self.build(),
                              dk_keep_motor_tracks=frozenset({701}))
        failures, evidence = quality.dk_keep_motor_tracks_evidence(
            [{"relation": "r702", "reason": "x"},
             {"relation": "r999", "reason": "y"}], audits)
        self.assertEqual(len(failures), 2)
        self.assertIn("r702: no member way needs the opt-in", failures[0])
        self.assertIn("r999: relation was not assembled", failures[1])
        self.assertEqual(evidence["r702"]["restored_way_ids"], [])
        self.assertNotIn("r999", evidence)

    def test_opt_in_is_refused_outside_exact_denmark(self):
        ways, nodes, relations = self.build()
        for kwargs in ({"region": "dk"}, {"region": "ca"}, {"region": None}):
            with self.subTest(**kwargs):
                with self.assertRaisesRegex(ValueError, "exact Denmark"):
                    model.assemble(nodes, ways, relations, [],
                                   dk_keep_motor_tracks=frozenset({701}),
                                   **kwargs)

    def test_an_empty_opt_in_equals_none(self):
        base = run_model(*self.build())
        for empty in (None, frozenset()):
            with self.subTest(empty=empty):
                trails, audits = run_model(*self.build(),
                                           dk_keep_motor_tracks=empty)
                self.assertEqual([t.to_feature() for t in trails],
                                 [t.to_feature() for t in base[0]])
                self.assertEqual(audits, base[1])

    def test_non_denmark_assembly_is_byte_identical_with_the_new_code(self):
        # Same inputs, region None: the legacy path never reads the opt-in.
        ways, nodes, relations = self.build()
        first = model.assemble(nodes, ways, relations, [], region=None)
        second = model.assemble(nodes, ways, relations, [], region=None,
                                dk_keep_motor_tracks=None)
        self.assertEqual([t.to_feature() for t in first],
                         [t.to_feature() for t in second])


class ValidatorShape(unittest.TestCase):
    GOOD = {"relation": "r12787516", "reason": "reviewed: signed walk"}

    def test_a_good_list_is_normalized(self):
        out = quality.validate_dk_keep_motor_tracks(
            [{**self.GOOD, "reason": "  reviewed  "}])
        self.assertEqual(out, [{"relation": "r12787516", "reason": "reviewed"}])

    def test_bad_lists_are_rejected(self):
        bad = [
            None, {}, "r1", [], [None], ["r1"],
            [{"relation": "r1"}],
            [{"reason": "x"}],
            [{**self.GOOD, "extra": 1}],
            [{**self.GOOD, "relation": "12787516"}],
            [{**self.GOOD, "relation": "r0"}],
            [{**self.GOOD, "relation": "r01"}],
            [{**self.GOOD, "relation": "w12787516"}],
            [{**self.GOOD, "relation": 12787516}],
            [{**self.GOOD, "reason": " "}],
            [{**self.GOOD, "reason": None}],
            [{**self.GOOD, "reason": 5}],
            [self.GOOD, dict(self.GOOD)],
        ]
        for value in bad:
            with self.subTest(value=str(value)[:50]):
                with self.assertRaises(ValueError):
                    quality.validate_dk_keep_motor_tracks(value)

    def test_two_distinct_relations_are_allowed(self):
        out = quality.validate_dk_keep_motor_tracks(
            [self.GOOD, {**self.GOOD, "relation": "r2"}])
        self.assertEqual([e["relation"] for e in out],
                         ["r12787516", "r2"])


QUALITY_SOURCE = (HERE / "quality.py").read_text(encoding="utf-8")
QUALITY_MUTANTS = (
    ("validator: list required",
     'if not isinstance(entries, list) or not entries:\n        raise ValueError("keep_motor_tracks must be a non-empty list")',
     'if not isinstance(entries, list):\n        raise ValueError("keep_motor_tracks must be a non-empty list")'),
    ("validator: exact keys",
     "or set(entry) != DK_KEEP_MOTOR_TRACKS_KEYS)",
     "or not set(entry) >= DK_KEEP_MOTOR_TRACKS_KEYS)"),
    ("validator: relation id pattern",
     "DK_ACCEPT_GAP_ID.fullmatch(relation)):\n            raise ValueError(\n                f\"keep_motor_tracks relation is invalid",
     "DK_ACCEPT_GAP_ID.match(relation + 'x')):\n            raise ValueError(\n                f\"keep_motor_tracks relation is invalid"),
    ("validator: reason required",
     'if not isinstance(entry["reason"], str) or not entry["reason"].strip():\n            raise ValueError(f"keep_motor_tracks {relation}: reason is required")',
     'pass'),
    ("validator: no repeats",
     'if relation in seen:\n            raise ValueError(f"keep_motor_tracks repeats relation {relation!r}")',
     'pass'),
    ("evidence: absent relation fails",
     'failures.append(f"{relation}: relation was not assembled in this area")\n            continue',
     'continue'),
    ("evidence: stale entry fails",
     'if not restored:\n            failures.append(',
     'if False:\n            failures.append('),
    ("evidence: only included members count",
     'for record in audit.get("direct_way_members") or []\n            if record.get("status") == "included"',
     'for record in audit.get("direct_way_members") or []\n            if True'),
    ("evidence: only the opt-in reason counts",
     '== "restored-opt-in-motor-track"))',
     '!= "x"))'),
)


def quality_table(module):
    out = []
    good = {"relation": "r5", "reason": "why"}
    for value in (None, [], [good], [good, good], [{"relation": "r5"}],
                  [{**good, "relation": "5"}], [{**good, "reason": " "}],
                  [{**good, "extra": 1}], [good, {**good, "relation": "r6"}]):
        try:
            out.append(module.validate_dk_keep_motor_tracks(value))
        except ValueError as error:
            out.append(("error", str(error)))
    audits = [
        {"relation_id": 5, "direct_way_members": [
            {"way_id": 1, "status": "included",
             "decision": {"reason": REASON}},
            {"way_id": 2, "status": "excluded",
             "decision": {"reason": REASON}},
            {"way_id": 3, "status": "included",
             "decision": {"reason": "standalone-trail"}}]},
        {"relation_id": 6, "direct_way_members": [
            {"way_id": 4, "status": "included",
             "decision": {"reason": "standalone-trail"}}]},
    ]
    for entries in ([good], [{**good, "relation": "r6"}],
                    [{**good, "relation": "r7"}]):
        out.append(module.dk_keep_motor_tracks_evidence(entries, audits))
    return out


class QualityMutations(unittest.TestCase):
    def test_every_mutant_changes_the_table(self):
        def load(source):
            return load_source("quality_mutant", "quality.py", source)
        baseline = quality_table(quality)
        self.assertEqual(quality_table(load(QUALITY_SOURCE)), baseline)
        survivors = []
        for label, old, new in QUALITY_MUTANTS:
            with self.subTest(mutant=label):
                self.assertEqual(QUALITY_SOURCE.count(old), 1, label)
                try:
                    changed = quality_table(
                        load(QUALITY_SOURCE.replace(old, new))) != baseline
                except Exception:
                    changed = True
                if not changed:
                    survivors.append(label)
        self.assertEqual(survivors, [])


def build_fixture():
    osm = Osm()
    ring = [(LON0, LAT0), (LON1, LAT0), (LON1, LAT1), (LON0, LAT1),
            (LON0, LAT0)]
    boundary = osm.way(ring)
    osm.relations.append((AREA_RELATION, [boundary], {
        "type": "multipolygon", "natural": "wood", "name": AREA}))

    def line(index, middle_tags, name="Skovvejen"):
        """Path, one middle way with the given tags, path: 0.02 deg each."""
        lat = 56.01 + 0.005 * index
        pieces = [[(9.01, lat), (9.03, lat)], [(9.03, lat), (9.05, lat)],
                  [(9.05, lat), (9.07, lat)]]
        first, _, last = osm.chain(pieces, **PATH)
        # The chain gives all three the same tags; replace the middle way.
        middle = osm.ways[-2]
        osm.ways[-2] = (middle[0], middle[1], dict(middle_tags))
        return [first, middle[0], last]

    cases = {
        701: ("Bil Rute", {**TRACK, "motorcar": "yes", "name": "Skovvejen"}),
        702: ("Motor Rute", {**TRACK, "motor_vehicle": "yes",
                             "name": "Horneksvejen"}),
        703: ("Fodforbud Rute", {**TRACK, "motorcar": "yes", "foot": "no"}),
        704: ("Skiltet Rute", {**TRACK, "motor_vehicle": "designated"}),
        705: ("Servicevej Rute", {"highway": "service", "motorcar": "yes"}),
        706: ("Fodtilladt Rute", {**TRACK, "motorcar": "yes", "foot": "yes"}),
        707: ("Vejnavn Rute", {**TRACK, "motorcar": "yes",
                               "name": "Forest Road"}),
        708: ("Bredt Spor Rute", {**TRACK, "motor_vehicle": "yes",
                                  "lanes": "2"}),
        709: ("Privat Rute", {**TRACK, "motorcar": "yes",
                              "access": "private"}),
        710: ("Ren Rute", dict(PATH)),
        711: ("Skovvej Uklassificeret", {"highway": "unclassified",
                                         "motor_vehicle": "yes"}),
        712: ("Motorcykel Rute", {**TRACK, "motorcar": "yes",
                                  "motorcycle": "yes", "name": "Sømærkevej"}),
        713: ("Terrænkøretøj Rute", {**TRACK, "motorcar": "yes",
                                     "motorcycle": "yes", "atv": "yes"}),
    }
    for index, (relation_id, (name, tags)) in enumerate(cases.items()):
        osm.route(relation_id, name, line(index, tags))
    return osm.xml()


class CliCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not HAVE_DEPS:
            raise unittest.SkipTest("pyosmium/shapely not installed")
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        cls.osm = cls.dir / "fixture.osm"
        cls.osm.write_text(build_fixture(), encoding="utf-8")
        cls.runs = {}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def json_file(self, payload):
        path = self.dir / f"opt-{len(list(self.dir.glob('opt-*')))}.json"
        path.write_text(payload if isinstance(payload, str)
                        else json.dumps(payload), encoding="utf-8")
        return str(path)

    def entry(self, *relations):
        return self.json_file([
            {"relation": f"r{r}", "reason": "reviewed: signed walk"}
            for r in relations])

    def run_cli(self, *extra, rules="dk-v3", region="dk", exact=True):
        key = (rules, region, exact, extra)
        if key in self.runs:
            return self.runs[key]
        label = f"run-{len(self.runs)}"
        out = self.dir / f"{label}.trails.geojson"
        report = self.dir / f"{label}.report.json"
        command = [sys.executable, str(HERE / "assemble.py"),
                   "--in", str(self.osm), "--out", str(out),
                   "--only-area", AREA, "--min-length-mi", "0.1",
                   "--per-area-merge", "--region", region,
                   "--dk-rules", rules]
        if exact:
            command += ["--require-exact-area", "--expected-area-relation-id",
                        str(AREA_RELATION), "--report-json", str(report)]
        result = subprocess.run(command + list(extra), capture_output=True,
                                text=True)
        trails = removed = report_doc = None
        if out.exists():
            trails = {f["properties"]["name"]: f for f in
                      json.loads(out.read_text())["features"]}
            removed_path = out.with_name(out.name.replace(
                ".trails.geojson", ".removed.geojson"))
            removed = {f["properties"]["name"]: f["properties"] for f in
                       json.loads(removed_path.read_text())["features"]}
        if report.exists():
            report_doc = json.loads(report.read_text())
        self.runs[key] = (result, trails, removed, report_doc, out)
        return self.runs[key]

    @staticmethod
    def span(feature):
        geometry = feature["geometry"]
        lines = (geometry["coordinates"] if geometry["type"] == "MultiLineString"
                 else [geometry["coordinates"]])
        xs = [point[0] for line in lines for point in line]
        return round(min(xs), 4), round(max(xs), 4)


class KeepMotorTracks(CliCase):
    def test_default_truncates_every_motor_track_route(self):
        # Control: no opt-in, today's behavior.
        result, trails, removed, _, _ = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.span(trails["Ren Rute"]), (9.01, 9.07))
        # The dropped motor track leaves a hole, so the route is withdrawn by
        # the relation-gap gate: the Endelave / Læsø / Bulbjerg symptom.
        for name in ("Bil Rute", "Motor Rute", "Fodforbud Rute",
                     "Skiltet Rute", "Servicevej Rute", "Vejnavn Rute",
                     "Bredt Spor Rute", "Privat Rute",
                     "Skovvej Uklassificeret", "Motorcykel Rute",
                     "Terrænkøretøj Rute"):
            with self.subTest(name=name):
                self.assertNotIn(name, trails)
                self.assertEqual(removed[name]["removed_category"],
                                 "unresolved-relation-gap")

    def test_opt_in_publishes_the_whole_route_for_both_tags(self):
        listed = self.entry(701, 702, 712)
        result, trails, _, report, _ = self.run_cli(
            "--dk-keep-motor-tracks", listed)
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in ("Bil Rute", "Motor Rute", "Motorcykel Rute"):
            with self.subTest(name=name):
                self.assertEqual(self.span(trails[name]), (9.01, 9.07))
                self.assertEqual(
                    len(trails[name]["geometry"]["coordinates"]), 1)
        evidence = report["dk_keep_motor_tracks"]
        self.assertEqual(set(evidence), {"r701", "r702", "r712"})
        self.assertEqual(len(evidence["r701"]["restored_way_ids"]), 1)
        self.assertEqual(report["status"], "ok")

    def test_opt_in_is_per_relation(self):
        _, base, _, _, _ = self.run_cli()
        listed = self.entry(701)
        result, opted, _, _, _ = self.run_cli("--dk-keep-motor-tracks", listed)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.span(opted["Bil Rute"]), (9.01, 9.07))
        # Every other route is exactly as without the opt-in (r702 has the same
        # kind of track but is not named).
        for name in set(base) - {"Bil Rute"}:
            with self.subTest(name=name):
                self.assertEqual(base[name], opted[name])
        self.assertEqual(set(opted), set(base) | {"Bil Rute"})

    def test_routes_that_fail_a_member_gate_stay_out_or_truncated(self):
        _, base, _, _, _ = self.run_cli()
        listed = self.entry(701, 703, 704, 705, 707, 708, 709, 711, 713)
        result, opted, _, report, _ = self.run_cli(
            "--dk-keep-motor-tracks", listed)
        # r703.. r711 each need no opt-in or are refused by their own gate, so
        # the entries for them are stale and the area fails closed.
        self.assertEqual(result.returncode, 2)
        self.assertIn("dk-keep-motor-tracks failures", result.stderr)
        stale = report["failure"]
        for relation in (703, 704, 705, 707, 708, 709, 711, 713):
            with self.subTest(relation=relation):
                self.assertIn(f"r{relation}: no member way needs the opt-in",
                              stale)
        self.assertNotIn("r701:", stale)
        # Whatever the failed run wrote, the refused routes are still withdrawn
        # exactly as without the opt-in, and the named one is whole.
        _, _, removed, _, _ = self.run_cli(
            "--dk-keep-motor-tracks", listed)
        for name in ("Fodforbud Rute", "Skiltet Rute", "Servicevej Rute",
                     "Vejnavn Rute", "Bredt Spor Rute", "Privat Rute",
                     "Skovvej Uklassificeret", "Terrænkøretøj Rute"):
            with self.subTest(name=name):
                self.assertNotIn(name, opted)
                self.assertEqual(removed[name]["removed_category"],
                                 "unresolved-relation-gap")
        self.assertEqual(self.span(opted["Bil Rute"]), (9.01, 9.07))

    def test_a_route_with_foot_yes_needs_no_opt_in(self):
        _, base, _, _, _ = self.run_cli()
        self.assertEqual(self.span(base["Fodtilladt Rute"]), (9.01, 9.07))
        result, _, _, report, _ = self.run_cli(
            "--dk-keep-motor-tracks", self.entry(706))
        self.assertEqual(result.returncode, 2)
        self.assertIn("r706: no member way needs the opt-in", report["failure"])

    def test_a_route_without_motor_tracks_fails_closed(self):
        result, _, _, report, _ = self.run_cli(
            "--dk-keep-motor-tracks", self.entry(710))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(report["status"], "failed")
        self.assertIn("r710: no member way needs the opt-in", report["failure"])

    def test_an_unknown_relation_fails_closed(self):
        result, _, _, report, _ = self.run_cli(
            "--dk-keep-motor-tracks", self.entry(99999))
        self.assertEqual(result.returncode, 2)
        self.assertIn("r99999: relation was not assembled in this area",
                      report["failure"])

    def test_a_stale_entry_does_not_hide_a_good_one(self):
        result, trails, _, report, _ = self.run_cli(
            "--dk-keep-motor-tracks", self.entry(701, 710))
        self.assertEqual(result.returncode, 2)
        self.assertIn("r710", report["failure"])
        self.assertNotIn("r701:", report["failure"])
        self.assertEqual(self.span(trails["Bil Rute"]), (9.01, 9.07))

    def test_the_opt_in_is_dk_v3_exact_denmark_only(self):
        listed = self.entry(701)
        for rules in ("legacy", "dk-v2"):
            with self.subTest(rules=rules):
                result = self.run_cli("--dk-keep-motor-tracks", listed,
                                      rules=rules)[0]
                self.assertEqual(result.returncode, 2)
                self.assertIn("requires --dk-rules dk-v3", result.stderr)
        result = self.run_cli("--dk-keep-motor-tracks", listed,
                              exact=False)[0]
        self.assertEqual(result.returncode, 2)
        self.assertIn("limited to exact Denmark assembly", result.stderr)
        for region in ("ca", "us"):
            with self.subTest(region=region):
                result = self.run_cli("--dk-keep-motor-tracks", listed,
                                      region=region, rules="dk-v3")[0]
                self.assertEqual(result.returncode, 2)
                self.assertIn("limited to exact Denmark assembly",
                              result.stderr)

    def test_invalid_files_are_rejected_before_any_work(self):
        bad = ["not json", {"relation": "r701"}, [], [{"relation": "r701"}],
               [{"relation": "701", "reason": "x"}],
               [{"relation": "r701", "reason": " "}],
               [{"relation": "r701", "reason": "x"},
                {"relation": "r701", "reason": "y"}],
               [{"relation": "r701", "reason": "x", "extra": 1}]]
        for payload in bad:
            with self.subTest(payload=str(payload)[:60]):
                path = self.json_file(payload)
                result = subprocess.run(
                    [sys.executable, str(HERE / "assemble.py"),
                     "--in", str(self.dir / "missing.pbf"), "--out", "y.geojson",
                     "--region", "dk", "--require-exact-area",
                     "--dk-rules", "dk-v3", "--dk-keep-motor-tracks", path],
                    capture_output=True, text=True)
                self.assertEqual(result.returncode, 2)
                self.assertIn("--dk-keep-motor-tracks is invalid",
                              result.stderr)

    def test_no_flag_leaves_the_report_without_the_key(self):
        _, _, _, report, _ = self.run_cli()
        self.assertNotIn("dk_keep_motor_tracks", report)


if __name__ == "__main__":
    unittest.main()
