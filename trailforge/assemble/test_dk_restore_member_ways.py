"""dk-v3 restore_member_ways: a reviewed per-relation opt-in that restores the
member ways of a signed route that the member-safety road gates dropped
(driveway and parking-aisle service ways, secondary crossings, sidewalks,
unclassified ways with a motor-access tag) and its way members with role
``route``. The restored road length is capped per relation (entry cap and a share
of the route); motorway, trunk and primary roads are never restored.
Layers: the pure member decision (one source mutation per gate), the model
assembly (opt-in scoped to the named relation, cap and share fail closed), the
validator and evidence, and the real assemble.py CLI on a synthetic OSM file.
Every opt-in has its no-opt-in control, fail-closed negatives and an
unrelated-route control. Nothing here touches non-Danish code paths other than
to prove they are unchanged or refused."""
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
REASON = "restored-opt-in-road"
PATH = {"highway": "path"}
SERVICE = {"highway": "service"}
DRIVEWAY = {**SERVICE, "service": "driveway"}
AISLE = {**SERVICE, "service": "parking_aisle"}
# (label, tags, default decision (eligible, restored, reason),
#  opt-in decision (eligible, restored, reason))
RESTORED = (True, True, REASON)
def refused(reason):
    return (False, False, reason)
def same(reason, restored=False):
    return (True, restored, reason)
MATRIX = (
    # the four restorable shapes
    ("driveway", dict(DRIVEWAY), refused("service-forbidden-driveway"), RESTORED),
    ("parking aisle", dict(AISLE),
     refused("service-forbidden-parking_aisle"), RESTORED),
    ("named driveway", {**DRIVEWAY, "name": "Vestergårdsvej"},
     refused("service-forbidden-driveway"), RESTORED),
    ("driveway with foot=yes", {**DRIVEWAY, "foot": "yes"},
     refused("service-forbidden-driveway"), RESTORED),
    ("driveway with motor_vehicle=yes", {**DRIVEWAY, "motor_vehicle": "yes"},
     refused("motor-access-without-positive-foot"), RESTORED),
    ("parking aisle with motorcar=designated",
     {**AISLE, "motorcar": "designated"},
     refused("motor-access-without-positive-foot"), RESTORED),
    ("driveway with access=private and foot=yes",
     {**DRIVEWAY, "access": "private", "foot": "yes"},
     refused("service-forbidden-driveway"), RESTORED),
    ("driveway token case and spaces", {**SERVICE, "service": " DRIVEWAY "},
     refused("service-forbidden-driveway"), RESTORED),
    ("driveway;parking_aisle", {**SERVICE, "service": "driveway;parking_aisle"},
     refused("service-forbidden-driveway"), RESTORED),
    ("secondary", {"highway": "secondary"}, refused("highway-not-trailish"),
     RESTORED),
    ("secondary named, lanes=2, oneway",
     {"highway": "secondary", "name": "Bredgade", "lanes": "2",
      "oneway": "yes"}, refused("highway-not-trailish"), RESTORED),
    ("secondary_link", {"highway": "secondary_link"},
     refused("highway-not-trailish"), RESTORED),
    ("tertiary_link", {"highway": "tertiary_link"},
     refused("highway-not-trailish"), RESTORED),
    ("sidewalk", {"highway": "footway", "footway": "sidewalk"},
     refused("footway-sidewalk"), RESTORED),
    ("unclassified with motor_vehicle=designated",
     {"highway": "unclassified", "motor_vehicle": "designated",
      "name": "Lystlundvej"},
     refused("motor-access-without-positive-foot"), RESTORED),
    ("unclassified with motorcar=yes",
     {"highway": "unclassified", "motorcar": "yes"},
     refused("motor-access-without-positive-foot"), RESTORED),
    ("living_street with motor_vehicle=yes",
     {"highway": "living_street", "motor_vehicle": "yes"},
     refused("motor-access-without-positive-foot"), RESTORED),
    # motorway, trunk and primary roads are never restored by the opt-in
    ("motorway", {"highway": "motorway"}, refused("highway-not-trailish"),
     refused("highway-not-trailish")),
    ("motorway_link", {"highway": "motorway_link"},
     refused("highway-not-trailish"), refused("highway-not-trailish")),
    ("trunk", {"highway": "trunk"}, refused("highway-not-trailish"),
     refused("highway-not-trailish")),
    ("trunk_link", {"highway": "trunk_link"}, refused("highway-not-trailish"),
     refused("highway-not-trailish")),
    ("primary_link", {"highway": "primary_link"},
     refused("highway-not-trailish"), refused("highway-not-trailish")),
    ("primary with foot=no", {"highway": "primary", "foot": "no"},
     refused("foot-no"), refused("foot-no")),
    ("primary with access=private", {"highway": "primary", "access": "private"},
     refused("access-private"), refused("access-private")),
    ("motorway with foot=yes", {"highway": "motorway", "foot": "yes"},
     refused("highway-not-trailish"), refused("highway-not-trailish")),
    # primary the default already restores stays exactly as it was
    ("primary the default restores", {"highway": "primary"},
     same("restored-primary", True), same("restored-primary", True)),
    ("residential the default restores", {"highway": "residential"},
     same("restored-residential", True), same("restored-residential", True)),
    ("plain service the default restores", dict(SERVICE),
     same("restored-service", True), same("restored-service", True)),
    # every hard prohibition still applies to an otherwise restorable shape
    ("driveway foot=no", {**DRIVEWAY, "foot": "no"}, refused("foot-no"),
     refused("foot-no")),
    ("driveway foot=private", {**DRIVEWAY, "foot": "private"},
     refused("foot-private"), refused("foot-private")),
    ("driveway access=private", {**DRIVEWAY, "access": "private"},
     refused("access-private"), refused("access-private")),
    ("driveway access=no", {**DRIVEWAY, "access": "no"}, refused("access-no"),
     refused("access-no")),
    ("secondary foot=no", {"highway": "secondary", "foot": "no"},
     refused("foot-no"), refused("foot-no")),
    ("sidewalk foot=no", {"highway": "footway", "footway": "sidewalk",
                          "foot": "no"}, refused("foot-no"), refused("foot-no")),
    ("sidewalk access=private", {"highway": "footway", "footway": "sidewalk",
                                 "access": "private"},
     refused("access-private"), refused("access-private")),
    ("sidewalk indoor", {"highway": "footway", "footway": "sidewalk",
                         "indoor": "yes"}, refused("footway-sidewalk"),
     refused("footway-sidewalk")),
    ("sidewalk trail=no", {"highway": "footway", "footway": "sidewalk",
                           "trail": "no"}, refused("footway-sidewalk"),
     refused("footway-sidewalk")),
    ("sidewalk mtb flow", {"highway": "footway", "footway": "sidewalk",
                           "mtb:type": "flow"}, refused("footway-sidewalk"),
     refused("footway-sidewalk")),
    ("sidewalk with atv", {"highway": "footway", "footway": "sidewalk",
                           "atv": "yes"}, refused("footway-sidewalk"),
     refused("footway-sidewalk")),
    ("driveway indoor", {**DRIVEWAY, "indoor": "yes"}, refused("indoor"),
     refused("indoor")),
    ("driveway trail=no", {**DRIVEWAY, "trail": "no"}, refused("trail-no"),
     refused("trail-no")),
    ("driveway mtb flow", {**DRIVEWAY, "mtb:type": "flow"},
     refused("mtb-type"), refused("mtb-type")),
    ("driveway downhill piste", {**DRIVEWAY, "piste:type": "downhill"},
     refused("non-hiking-piste"), refused("non-hiking-piste")),
    ("driveway no hiking name", {**DRIVEWAY, "name": "No hiking here"},
     refused("name-no-hiking"), refused("name-no-hiking")),
    ("driveway mtb name", {**DRIVEWAY, "name": "MTB Loop"},
     refused("bike-only-name"), refused("bike-only-name")),
    ("driveway atv", {**DRIVEWAY, "atv": "yes"}, refused("motorized-atv"),
     refused("motorized-atv")),
    ("driveway ohv", {**DRIVEWAY, "ohv": "designated"},
     refused("motorized-ohv"), refused("motorized-ohv")),
    ("driveway snowmobile", {**DRIVEWAY, "snowmobile": "yes"},
     refused("motorized-snowmobile"), refused("motorized-snowmobile")),
    ("driveway 4wd_only", {**DRIVEWAY, "4wd_only": "yes"},
     refused("motorized-4wd_only"), refused("motorized-4wd_only")),
    ("driveway motorcycle=yes", {**DRIVEWAY, "motorcycle": "yes"},
     refused("motorized-motorcycle"), refused("motorized-motorcycle")),
    ("unclassified atv", {"highway": "unclassified", "motorcar": "yes",
                          "atv": "yes"}, refused("motorized-atv"),
     refused("motorized-atv")),
    # shapes the opt-in does not cover
    ("alley without foot", {**SERVICE, "service": "alley"},
     refused("service-without-positive-foot"),
     refused("service-without-positive-foot")),
    ("emergency_access", {**SERVICE, "service": "emergency_access"},
     refused("service-forbidden-emergency_access"),
     refused("service-forbidden-emergency_access")),
    ("bus service", {**SERVICE, "service": "bus"},
     refused("service-forbidden-bus"), refused("service-forbidden-bus")),
    ("drive-through", {**SERVICE, "service": "drive-through"},
     refused("service-forbidden-drive_through"),
     refused("service-forbidden-drive_through")),
    ("driveway;alley composite", {**SERVICE, "service": "driveway;alley"},
     refused("service-forbidden-driveway"),
     refused("service-forbidden-driveway")),
    ("driveway;spur composite", {**SERVICE, "service": "driveway;spur"},
     refused("service-forbidden-driveway"),
     refused("service-forbidden-driveway")),
    ("unknown service subtype", {**SERVICE, "service": "spur"},
     refused("service-unknown-token"), refused("service-unknown-token")),
    ("blank service subtype", {**SERVICE, "service": ""},
     refused("service-blank-token"), refused("service-blank-token")),
    ("plain service with motor_vehicle=yes", {**SERVICE, "motor_vehicle": "yes"},
     refused("motor-access-without-positive-foot"),
     refused("motor-access-without-positive-foot")),
    ("secondary with motor_vehicle=yes",
     {"highway": "secondary", "motor_vehicle": "yes"},
     refused("motor-access-without-positive-foot"),
     refused("motor-access-without-positive-foot")),
    ("construction", {"highway": "construction", "foot": "yes"},
     refused("highway-not-trailish"), refused("highway-not-trailish")),
    ("no highway tag", {"name": "Nothing"}, refused("highway-not-trailish"),
     refused("highway-not-trailish")),
    ("footway that is not a sidewalk, indoor", {"highway": "footway",
                                              "indoor": "yes"},
     refused("indoor"), refused("indoor")),
    ("path foot=no", {**PATH, "foot": "no", "mtb:scale:imba": "2"},
     refused("foot-no"), refused("foot-no")),
    ("track motorcar=yes is the keep_motor_tracks case",
     {"highway": "track", "motorcar": "yes"},
     refused("motor-access-without-positive-foot"),
     refused("motor-access-without-positive-foot")),
    ("track with a road name", {"highway": "track", "name": "Forest Road"},
     refused("road-like-track-name"), refused("road-like-track-name")),
    ("unclassified with motorcar=no", {"highway": "unclassified",
                                       "motorcar": "no"},
     same("restored-unclassified", True), same("restored-unclassified", True)),
    ("plain path", dict(PATH), same("standalone-trail"),
     same("standalone-trail")),
    ("footway that is not a sidewalk", {"highway": "footway"},
     same("standalone-trail"), same("standalone-trail")),
    ("footway crossing the default restores",
     {"highway": "footway", "footway": "crossing"},
     same("restored-footway-crossing", True),
     same("restored-footway-crossing", True)),
)
# The shapes' own drop reasons, fed to the helper whatever the default decision
# was: each hard gate must hold for every reason on its own.
FORCED_REASONS = ("service-forbidden-driveway", "service-forbidden-parking_aisle",
                  "highway-not-trailish", "footway-sidewalk",
                  "motor-access-without-positive-foot", "standalone-trail")
def decision_tuple(decision):
    return (decision.eligible, decision.restored, decision.reason)
def matrix_table(module):
    """Every matrix row: default, opt-in on, opt-in on with the motor flag,
    plus the helper, for one (possibly mutated) member_safety module."""
    out = []
    for _label, tags, _default, _opted in MATRIX:
        out.append(decision_tuple(module.dk_relation_member_decision(tags)))
        out.append(decision_tuple(module.dk_relation_member_decision(
            tags, restore_roads=True)))
        out.append(decision_tuple(module.dk_relation_member_decision(
            tags, restore_roads=True, keep_motor_track=True)))
        base = module._dk_relation_member_decision(tags)
        out.append(module.road_restore_eligible(tags, base))
        for reason in FORCED_REASONS:
            out.append(module.road_restore_eligible(
                tags, module.MemberDecision(False, False, reason)))
            out.append(module.road_restore_eligible(
                tags, module.MemberDecision(True, False, reason)))
        out.append(module.dk_relation_member_decision(
            tags, restore_roads=True).service_tokens)
    return tuple(out)
class MemberDecisionMatrix(unittest.TestCase):
    def test_every_row_has_the_expected_default_and_opt_in_decision(self):
        for label, tags, default, opted in MATRIX:
            with self.subTest(row=label):
                self.assertEqual(decision_tuple(
                    safety.dk_relation_member_decision(tags)), default)
                self.assertEqual(decision_tuple(
                    safety.dk_relation_member_decision(
                        tags, restore_roads=True)), opted)
    def test_default_argument_is_off_and_equals_the_private_decision(self):
        for label, tags, default, _opted in MATRIX:
            with self.subTest(row=label):
                self.assertEqual(
                    safety.dk_relation_member_decision(tags),
                    safety.dk_relation_member_decision(
                        tags, restore_roads=False))
                self.assertEqual(
                    safety.dk_relation_member_decision(tags),
                    safety._dk_relation_member_decision(tags))
    def test_model_default_path_is_the_shared_decision(self):
        for label, tags, default, _opted in MATRIX:
            with self.subTest(row=label):
                self.assertEqual(decision_tuple(
                    model._relation_member_decision(tags, "dk")), default)
    def test_non_denmark_member_decision_never_sees_the_opt_in(self):
        for region in (None, "us", "ca"):
            for label, tags, _d, _o in MATRIX:
                with self.subTest(region=region, row=label):
                    decision = model._relation_member_decision(tags, region)
                    self.assertNotEqual(decision.reason, REASON)
    def test_only_the_opt_in_produces_the_restored_road_reason(self):
        for label, tags, default, opted in MATRIX:
            self.assertNotEqual(default[2], REASON, label)
        self.assertEqual(safety.ROAD_RESTORE_OPT_IN_REASON, REASON)
    def test_the_opt_in_only_ever_changes_a_refused_member(self):
        for label, tags, default, opted in MATRIX:
            with self.subTest(row=label):
                if default[0]:
                    self.assertEqual(default, opted)
                else:
                    self.assertTrue(opted == default or opted == RESTORED)
    def test_restored_rows_are_exactly_the_four_shapes(self):
        restored = {label for label, _t, _d, opted in MATRIX
                    if opted == RESTORED}
        self.assertEqual(restored, {
            "driveway", "parking aisle", "named driveway",
            "driveway with foot=yes", "driveway with motor_vehicle=yes",
            "parking aisle with motorcar=designated",
            "driveway with access=private and foot=yes",
            "driveway token case and spaces", "driveway;parking_aisle",
            "secondary", "secondary named, lanes=2, oneway", "secondary_link",
            "tertiary_link", "sidewalk",
            "unclassified with motor_vehicle=designated",
            "unclassified with motorcar=yes",
            "living_street with motor_vehicle=yes",
        })
    def test_motorway_trunk_and_primary_are_never_restored(self):
        # Whatever the tags beside the highway class are, the opt-in must not
        # turn a motorway, trunk or primary (or a link) from refused to kept.
        refused_classes = ("motorway", "motorway_link", "trunk", "trunk_link",
                           "primary_link")
        extras = ({}, {"foot": "yes"}, {"service": "driveway"},
                  {"footway": "sidewalk"}, {"motor_vehicle": "yes"},
                  {"motorcar": "designated"}, {"lanes": "1"},
                  {"name": "Bredgade"}, {"surface": "asphalt"})
        for highway in refused_classes:
            for extra in extras:
                with self.subTest(highway=highway, extra=extra):
                    tags = {"highway": highway, **extra}
                    default = safety.dk_relation_member_decision(tags)
                    opted = safety.dk_relation_member_decision(
                        tags, restore_roads=True)
                    self.assertEqual(opted, default)
                    self.assertNotEqual(opted.reason, REASON)
        for extra in ({"foot": "no"}, {"access": "private"}, {"access": "no"}):
            with self.subTest(primary=extra):
                tags = {"highway": "primary", **extra}
                self.assertFalse(safety.dk_relation_member_decision(
                    tags, restore_roads=True).eligible)
    def test_the_refusal_set_is_motorway_trunk_primary_and_links(self):
        self.assertEqual(safety.ROAD_RESTORE_REFUSED_HIGHWAYS, frozenset({
            "motorway", "motorway_link", "trunk", "trunk_link", "primary",
            "primary_link"}))
        self.assertTrue(safety.ROAD_RESTORE_REFUSED_HIGHWAYS.isdisjoint(
            safety.ROAD_RESTORE_CROSSING_HIGHWAYS
            | safety.ROAD_RESTORE_MOTOR_ACCESS_HIGHWAYS))
    def test_the_decision_keeps_the_service_tokens_of_a_restored_way(self):
        decision = safety.dk_relation_member_decision(
            {**SERVICE, "service": "driveway;parking_aisle"},
            restore_roads=True)
        self.assertEqual(decision.service_tokens,
                         ("driveway", "parking_aisle"))
        self.assertTrue(decision.restored)
    def test_the_two_opt_ins_are_independent_and_compose(self):
        track = {"highway": "track", "motorcar": "yes"}
        driveway = dict(DRIVEWAY)
        for tags, keep, restore, eligible in (
                (track, False, False, False), (track, True, False, True),
                (track, False, True, False), (track, True, True, True),
                (driveway, False, False, False), (driveway, True, False, False),
                (driveway, False, True, True), (driveway, True, True, True)):
            with self.subTest(tags=tags, keep=keep, restore=restore):
                self.assertEqual(safety.dk_relation_member_decision(
                    tags, keep_motor_track=keep,
                    restore_roads=restore).eligible, eligible)
# Each mutant edits the module text (one or more exact replacements). Every
# mutant must change the matrix table; a surviving mutant fails the test (one
# per gate of the opt-in).
SAFETY_SOURCE = (HERE / "member_safety.py").read_text(encoding="utf-8")
WIDEN = ('ROAD_RESTORE_CROSSING_HIGHWAYS = frozenset({\n    "secondary", '
         '"secondary_link", "tertiary_link",\n})',
         'ROAD_RESTORE_CROSSING_HIGHWAYS = frozenset({\n    "secondary", '
         '"secondary_link", "tertiary_link", "motorway", "motorway_link", '
         '"trunk", "trunk_link", "primary", "primary_link",\n})')
NO_REFUSAL = ("    if highway in ROAD_RESTORE_REFUSED_HIGHWAYS:\n"
              "        return False\n", "")
MUTANTS = (
    ("helper: an eligible decision is left alone",
     (("    if decision.eligible:\n        return False\n", ""),)),
    ("helper: refusal set is the gate when the whitelist is widened",
     (WIDEN, NO_REFUSAL)),
    ("helper: foot=no/private is refused",
     (('    if foot in {"no", "private"}:\n        return False\n', ""),)),
    ("helper: access=no/private is refused",
     (('    if (normalize(tags.get("access")) in {"no", "private"}\n'
       '            and foot not in POSITIVE_FOOT_VALUES):\n'
       '        return False\n', ""),)),
    ("helper: access=private with foot=yes is not a prohibition",
     (("            and foot not in POSITIVE_FOOT_VALUES):\n",
       "            ):\n"),)),
    ("helper: indoor is refused",
     (('    if normalize(tags.get("indoor")) == "yes":\n        return False\n'
       '    if normalize(tags.get("trail"))', '    if normalize(tags.get("trail"))'),)),
    ("helper: trail=no is refused",
     (('    if normalize(tags.get("trail")) == "no":\n        return False\n'
       '    if nonhiking_reason(tags)', '    if nonhiking_reason(tags)'),)),
    ("helper: non-hiking tags are refused",
     (("    if nonhiking_reason(tags) is not None:\n        return False\n", ""),)),
    ("helper: atv/ohv/snowmobile/4wd_only/motorcycle are refused",
     (("    if motorized_reason(tags, include_road_motor=False) is not None:\n"
       "        return False\n", ""),)),
    ("service: a subtype is required",
     (("        if not tokens or not all(\n", "        if not all(\n"),)),
    ("service: every token must be restorable",
     (("        if not tokens or not all(\n", "        if not tokens or not any(\n"),)),
    ("service: only driveway and parking_aisle",
     (('ROAD_RESTORE_SERVICE_TOKENS = frozenset({"driveway", "parking_aisle"})',
       'ROAD_RESTORE_SERVICE_TOKENS = frozenset({"driveway", "parking_aisle", '
       '"alley", "emergency_access", "bus"})'),)),
    ("service: only the forbidden-subtype or motor-access reasons",
     (('        return (reason.startswith("service-forbidden-")\n'
       '                or reason == "motor-access-without-positive-foot")\n',
       "        return True\n"),)),
    ("service: motor-access reason is a restorable reason",
     (('                or reason == "motor-access-without-positive-foot")\n',
       "                )\n"),)),
    ("crossing: the reason is highway-not-trailish",
     (('        return reason == "highway-not-trailish"\n', "        return True\n"),)),
    ("crossing: secondary_link is covered",
     (('"secondary", "secondary_link", "tertiary_link",\n})',
       '"secondary", "tertiary_link",\n})'),)),
    ("crossing: tertiary_link is covered",
     (('"secondary", "secondary_link", "tertiary_link",\n})',
       '"secondary", "secondary_link",\n})'),)),
    ("sidewalk: the reason is footway-sidewalk",
     (('        return (reason == "footway-sidewalk"\n'
       '                and normalize(tags.get("footway")) == "sidewalk")\n',
       '        return normalize(tags.get("footway")) == "sidewalk"\n'),)),
    ("sidewalk: the way is a sidewalk",
     (('        return (reason == "footway-sidewalk"\n'
       '                and normalize(tags.get("footway")) == "sidewalk")\n',
       '        return reason == "footway-sidewalk"\n'),)),
    ("motor access: living_street is covered",
     (('ROAD_RESTORE_MOTOR_ACCESS_HIGHWAYS = frozenset({"unclassified", '
       '"living_street"})',
       'ROAD_RESTORE_MOTOR_ACCESS_HIGHWAYS = frozenset({"unclassified"})'),)),
    ("motor access: unclassified is covered",
     (('ROAD_RESTORE_MOTOR_ACCESS_HIGHWAYS = frozenset({"unclassified", '
       '"living_street"})',
       'ROAD_RESTORE_MOTOR_ACCESS_HIGHWAYS = frozenset({"living_street"})'),)),
    ("motor access: track is not covered here",
     (('ROAD_RESTORE_MOTOR_ACCESS_HIGHWAYS = frozenset({"unclassified", '
       '"living_street"})',
       'ROAD_RESTORE_MOTOR_ACCESS_HIGHWAYS = frozenset({"unclassified", '
       '"living_street", "track", "path"})'),)),
    ("motor access: the reason is motor-access-without-positive-foot",
     (('        return reason == "motor-access-without-positive-foot"\n    return False',
       '        return True\n    return False'),)),
    ("flag: default is off",
     (("keep_motor_track: bool = False,\n                                "
       "restore_roads: bool = False",
       "keep_motor_track: bool = False,\n                                "
       "restore_roads: bool = True"),)),
    ("flag: the opt-in needs the flag",
     (("    if restore_roads and road_restore_eligible(tags, decision):",
       "    if road_restore_eligible(tags, decision):"),)),
    ("flag: the opt-in is honored",
     (("    if restore_roads and road_restore_eligible(tags, decision):",
       "    if False and road_restore_eligible(tags, decision):"),)),
    ("flag: the motor flag still reaches the decision",
     (("    decision = _dk_relation_member_decision(\n        tags, "
       "keep_motor_track=keep_motor_track)",
       "    decision = _dk_relation_member_decision(\n        tags)"),)),
    ("restore: the restored flag is set",
     (("        return MemberDecision(True, True, ROAD_RESTORE_OPT_IN_REASON,",
       "        return MemberDecision(True, False, ROAD_RESTORE_OPT_IN_REASON,"),)),
    ("restore: the reason is the opt-in reason",
     (('ROAD_RESTORE_OPT_IN_REASON = "restored-opt-in-road"',
       'ROAD_RESTORE_OPT_IN_REASON = "restored-service"'),)),
    ("restore: the service tokens are kept",
     (("                              service_tokens=decision.service_tokens)\n"
       "    return decision",
       "                              )\n    return decision"),)),
)
def load_source(name, filename, source):
    mod = types.ModuleType(name)
    mod.__file__ = str(HERE / filename)
    sys.modules[name] = mod
    try:
        exec(compile(source, mod.__file__, "exec"), mod.__dict__)
    finally:
        del sys.modules[name]
    return mod
def mutate(source, edits):
    for old, new in edits:
        if source.count(old) != 1:
            raise AssertionError(f"mutation target not unique ({source.count(old)}): "
                                 f"{old[:70]!r}")
        source = source.replace(old, new)
    return source
class OptInMutations(unittest.TestCase):
    def test_every_mutant_changes_the_matrix(self):
        baseline = matrix_table(safety)
        survivors = []
        for label, edits in MUTANTS:
            with self.subTest(mutant=label):
                mutant = load_source("member_safety_mutant", "member_safety.py",
                                     mutate(SAFETY_SOURCE, edits))
                try:
                    changed = matrix_table(mutant) != baseline
                except Exception:
                    changed = True
                if not changed:
                    survivors.append(label)
        self.assertEqual(survivors, [])
    def test_the_unmutated_copy_matches_the_real_module(self):
        copy = load_source("member_safety_mutant", "member_safety.py",
                           SAFETY_SOURCE)
        self.assertEqual(matrix_table(copy), matrix_table(safety))
    def test_the_refusal_holds_even_when_the_whitelist_is_widened(self):
        widened = load_source("member_safety_mutant", "member_safety.py",
                              mutate(SAFETY_SOURCE, (WIDEN,)))
        for highway in ("motorway", "motorway_link", "trunk", "trunk_link",
                        "primary", "primary_link"):
            for reason in FORCED_REASONS:
                with self.subTest(highway=highway, reason=reason):
                    self.assertFalse(widened.road_restore_eligible(
                        {"highway": highway},
                        widened.MemberDecision(False, False, reason)))
def way(nodes, **tags):
    return {"nodes": list(nodes), "tags": tags}
def run_model(ways, nodes, relations, **options):
    audits = []
    trails = model.assemble(
        nodes, ways, relations, [], region="dk",
        collect_relation_member_audit=audits, exact_denmark=True,
        defer_promotion=True, **options)
    return trails, audits
def route(name, members, relation_id, roles=None):
    roles = roles or {}
    return relation_id, {"tags": {"type": "route", "route": "hiking",
                                  "name": name, "network": "lwn"},
                         "members": [("w", ref, roles.get(ref, ""))
                                     for ref in members]}
class ModelScope(unittest.TestCase):
    """Two routes share one driveway (62 m); the opt-in names one of them."""
    def build(self, middle=None, roles=None):
        nodes = {1: (9.000, 56.0), 2: (9.010, 56.0), 3: (9.011, 56.0),
                 4: (9.021, 56.0), 5: (9.010, 56.01), 6: (9.010, 56.02)}
        ways = {
            10: way([1, 2], **PATH),
            11: way([2, 3], **(middle or {**DRIVEWAY, "name": "Skovvejen"})),
            12: way([3, 4], **PATH),
            13: way([2, 5], **PATH),
            14: way([5, 6], **PATH),
        }
        relations = dict([route("Rute Et", [10, 11, 12], 701, roles),
                          route("Rute To", [13, 14, 11], 702)])
        return ways, nodes, relations
    def test_default_drops_the_driveway_from_both_routes(self):
        trails, audits = run_model(*self.build())
        by_rel = {a["relation_id"]: a for a in audits}
        for rid in (701, 702):
            self.assertNotIn(11, by_rel[rid]["eligible_main_way_ids"])
            self.assertNotIn("dk_restore_member_ways", by_rel[rid])
        for trail in trails:
            self.assertEqual(trail.restored_relation_ways, [])
    def test_opt_in_restores_the_way_only_for_the_named_relation(self):
        trails, audits = run_model(
            *self.build(), dk_restore_member_ways={701: 500.0})
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
                         "service-forbidden-driveway")
        names = {t.name: t for t in trails}
        self.assertEqual(names["Rute Et"].restored_relation_ways, [11])
        self.assertEqual(names["Rute To"].restored_relation_ways, [])
        self.assertIn(11, names["Rute Et"].member_ways)
        self.assertNotIn(11, names["Rute To"].member_ways)
        self.assertNotIn("dk_restore_member_ways", by_rel[702])
    def test_evidence_names_the_restored_way_and_its_length(self):
        _, audits = run_model(*self.build(),
                              dk_restore_member_ways={701: 500.0})
        record = {a["relation_id"]: a for a in audits}[701][
            "dk_restore_member_ways"]
        self.assertEqual(record["status"], "restored")
        self.assertEqual(record["restored_way_ids"], [11])
        self.assertEqual(record["route_role_way_ids"], [])
        self.assertEqual(record["cap_m"], 500.0)
        self.assertEqual(record["max_share"], model.DK_RESTORE_MAX_SHARE)
        self.assertAlmostEqual(record["restored_m"], 62.2, delta=1.0)
        self.assertAlmostEqual(record["route_m"], 62.2 + 2 * 622.0,
                               delta=10.0)
        self.assertAlmostEqual(record["share"], 0.0476, delta=0.003)
        failures, evidence = quality.dk_restore_member_ways_evidence(
            [{"relation": "r701", "reason": "reviewed"}], audits)
        self.assertEqual(failures, [])
        self.assertEqual(evidence["r701"]["reason"], "reviewed")
        self.assertEqual(evidence["r701"]["restored_way_ids"], [11])
    def test_the_restored_length_is_capped_and_the_route_stays_as_default(self):
        default, _ = run_model(*self.build())
        trails, audits = run_model(*self.build(),
                                   dk_restore_member_ways={701: 50.0})
        audit = {a["relation_id"]: a for a in audits}[701]
        record = audit["dk_restore_member_ways"]
        self.assertEqual(record["status"], "cap-exceeded")
        self.assertGreater(record["restored_m"], 50.0)
        # Nothing is restored and nothing is truncated: the member list is the
        # default resolution.
        self.assertNotIn(11, audit["eligible_main_way_ids"])
        self.assertEqual([t.to_feature() for t in trails],
                         [t.to_feature() for t in default])
        failures, _ = quality.dk_restore_member_ways_evidence(
            [{"relation": "r701", "reason": "x"}], audits)
        self.assertEqual(len(failures), 1)
        self.assertIn("is over max_restored_m 50 m", failures[0])
    def test_the_cap_is_inclusive_at_the_measured_length(self):
        _, audits = run_model(*self.build(),
                              dk_restore_member_ways={701: 500.0})
        measured = {a["relation_id"]: a for a in audits}[701][
            "dk_restore_member_ways"]["restored_m"]
        for cap, status in ((measured + 0.2, "restored"),
                            (measured - 1.0, "cap-exceeded")):
            with self.subTest(cap=cap):
                _, audits = run_model(*self.build(),
                                      dk_restore_member_ways={701: cap})
                self.assertEqual({a["relation_id"]: a for a in audits}[701][
                    "dk_restore_member_ways"]["status"], status)
    def test_the_share_of_the_route_is_capped(self):
        # A 622 m driveway in a route of 622 + 622 m of path is 33 %: over the
        # 25 % limit however generous the entry cap is.
        nodes = {1: (9.000, 56.0), 2: (9.010, 56.0), 3: (9.020, 56.0),
                 4: (9.030, 56.0)}
        ways = {10: way([1, 2], **PATH), 11: way([2, 3], **DRIVEWAY),
                12: way([3, 4], **PATH)}
        relations = dict([route("Kort Rute", [10, 11, 12], 701)])
        default, _ = run_model(ways, nodes, relations)
        trails, audits = run_model(ways, nodes, relations,
                                   dk_restore_member_ways={701: 3000.0})
        audit = audits[0]["dk_restore_member_ways"]
        self.assertEqual(audit["status"], "share-exceeded")
        self.assertGreater(audit["share"], model.DK_RESTORE_MAX_SHARE)
        self.assertLess(audit["restored_m"], 3000.0)
        self.assertEqual([t.to_feature() for t in trails],
                         [t.to_feature() for t in default])
        failures, _ = quality.dk_restore_member_ways_evidence(
            [{"relation": "r701", "reason": "x"}], audits)
        self.assertIn("restored road share", failures[0])
    def test_a_way_with_missing_nodes_cannot_be_measured(self):
        ways, nodes, relations = self.build()
        del nodes[3]
        trails, audits = run_model(ways, nodes, relations,
                                   dk_restore_member_ways={701: 500.0})
        record = {a["relation_id"]: a for a in audits}[701][
            "dk_restore_member_ways"]
        self.assertEqual(record["status"], "unmeasurable")
        failures, _ = quality.dk_restore_member_ways_evidence(
            [{"relation": "r701", "reason": "x"}], audits)
        self.assertIn("cannot measure", failures[0])
class RouteRole(unittest.TestCase):
    """A way member with role ``route`` is a main-line member only under the
    opt-in; every other role resolves as before."""
    def build(self, role):
        nodes = {1: (9.000, 56.0), 2: (9.010, 56.0), 3: (9.020, 56.0),
                 4: (9.030, 56.0)}
        ways = {10: way([1, 2], **PATH), 11: way([2, 3], **PATH),
                12: way([3, 4], **PATH)}
        relations = dict([route("Rolle Rute", [10, 11, 12], 701, {11: role}),
                          route("Anden Rute", [10, 11, 12], 702, {11: role})])
        return ways, nodes, relations
    def test_default_drops_the_route_role_member(self):
        _, audits = run_model(*self.build("route"))
        for audit in audits:
            self.assertNotIn(11, audit["eligible_main_way_ids"])
            record = next(r for r in audit["direct_way_members"]
                          if r["way_id"] == 11)
            self.assertEqual(record["exclusion_reason"], "unsupported-role-route")
    def test_opt_in_keeps_it_for_the_named_relation_only(self):
        _, audits = run_model(*self.build("route"),
                              dk_restore_member_ways={701: 100.0})
        by_rel = {a["relation_id"]: a for a in audits}
        self.assertIn(11, by_rel[701]["eligible_main_way_ids"])
        self.assertNotIn(11, by_rel[702]["eligible_main_way_ids"])
        record = by_rel[701]["dk_restore_member_ways"]
        self.assertEqual(record["status"], "restored")
        self.assertEqual(record["route_role_way_ids"], [11])
        self.assertEqual(record["restored_way_ids"], [])
        failures, _ = quality.dk_restore_member_ways_evidence(
            [{"relation": "r701", "reason": "x"}], audits)
        self.assertEqual(failures, [])
    def test_other_roles_are_untouched_by_the_opt_in(self):
        for role in ("alternative", "excursion", "forward", "backward",
                     "approach", "connection", "routes", "stage"):
            with self.subTest(role=role):
                _, plain = run_model(*self.build(role))
                _, opted = run_model(*self.build(role),
                                     dk_restore_member_ways={701: 100.0})
                before = {a["relation_id"]: a for a in plain}[701]
                after = {a["relation_id"]: a for a in opted}[701]
                self.assertEqual(before["eligible_main_way_ids"],
                                 after["eligible_main_way_ids"])
                self.assertEqual(before["included_spur_way_ids"],
                                 after["included_spur_way_ids"])
                self.assertEqual(
                    after["dk_restore_member_ways"]["route_role_way_ids"], [])
    def test_a_nested_route_relation_keeps_the_role_rule(self):
        nodes = {1: (9.000, 56.0), 2: (9.010, 56.0), 3: (9.020, 56.0),
                 4: (9.030, 56.0)}
        ways = {10: way([1, 2], **PATH), 11: way([2, 3], **PATH),
                12: way([3, 4], **PATH)}
        child = {"tags": {"type": "route", "route": "hiking", "name": "Barn"},
                 "members": [("w", 10, ""), ("w", 11, "route"), ("w", 12, "")]}
        parent = {"tags": {"type": "route", "route": "hiking",
                           "name": "Forælder", "network": "lwn"},
                  "members": [("r", 801, "")]}
        relations = {701: parent, 801: child}
        _, plain = run_model(ways, nodes, relations)
        _, opted = run_model(ways, nodes, relations,
                             dk_restore_member_ways={701: 100.0})
        before = {a["relation_id"]: a for a in plain}[701]
        after = {a["relation_id"]: a for a in opted}[701]
        self.assertNotIn(11, before["eligible_main_way_ids"])
        self.assertIn(11, after["eligible_main_way_ids"])
        self.assertEqual(after["dk_restore_member_ways"]["route_role_way_ids"],
                         [11])
    def test_the_role_is_matched_after_normalizing_case_and_spaces(self):
        _, audits = run_model(*self.build(" Route "),
                              dk_restore_member_ways={701: 100.0})
        self.assertEqual({a["relation_id"]: a for a in audits}[701][
            "dk_restore_member_ways"]["route_role_way_ids"], [11])
    def test_the_route_role_way_still_meets_every_member_gate(self):
        ways, nodes, relations = self.build("route")
        ways[11] = way([2, 3], highway="path", foot="no")
        _, audits = run_model(ways, nodes, relations,
                              dk_restore_member_ways={701: 100.0})
        audit = {a["relation_id"]: a for a in audits}[701]
        self.assertNotIn(11, audit["eligible_main_way_ids"])
        record = next(r for r in audit["direct_way_members"]
                      if r["way_id"] == 11)
        self.assertEqual(record["exclusion_reason"], "foot-no")
class ModelGates(unittest.TestCase):
    def test_the_opt_in_is_refused_outside_exact_denmark(self):
        ways, nodes, relations = ModelScope().build()
        for kwargs in ({"region": "dk"}, {"region": "ca"}, {"region": None}):
            with self.subTest(**kwargs):
                with self.assertRaisesRegex(ValueError, "exact Denmark"):
                    model.assemble(nodes, ways, relations, [],
                                   dk_restore_member_ways={701: 100.0},
                                   **kwargs)
    def test_an_empty_opt_in_equals_none(self):
        base = run_model(*ModelScope().build())
        for empty in (None, {}):
            with self.subTest(empty=empty):
                trails, audits = run_model(*ModelScope().build(),
                                           dk_restore_member_ways=empty)
                self.assertEqual([t.to_feature() for t in trails],
                                 [t.to_feature() for t in base[0]])
                self.assertEqual(audits, base[1])
    def test_non_denmark_assembly_is_identical_with_the_new_code(self):
        ways, nodes, relations = ModelScope().build()
        first = model.assemble(nodes, ways, relations, [], region=None)
        second = model.assemble(nodes, ways, relations, [], region=None,
                                dk_restore_member_ways=None)
        self.assertEqual([t.to_feature() for t in first],
                         [t.to_feature() for t in second])
    def test_route_role_resolution_is_off_by_default(self):
        _, relations = route("R", [1, 2, 3], 9, {2: "route"})
        relations = {9: relations}
        default = model.resolve_route_member_entries(9, relations)
        opted = model.resolve_route_member_entries(
            9, relations, accept_route_role=True)
        self.assertEqual([e["effective_role"] for e in default],
                         ["main", "route", "main"])
        self.assertEqual([e["effective_role"] for e in opted],
                         ["main", "main", "main"])
        self.assertEqual([e["role"] for e in opted], ["", "route", ""])
    def test_an_approach_ancestor_still_overrides_the_route_role(self):
        relations = {
            1: {"tags": {"type": "route", "route": "hiking"},
                "members": [("r", 2, "approach")]},
            2: {"tags": {"type": "route", "route": "hiking"},
                "members": [("w", 7, "route")]},
        }
        entries = model.resolve_route_member_entries(
            1, relations, accept_route_role=True)
        self.assertEqual([e["effective_role"] for e in entries], ["approach"])
class ValidatorShape(unittest.TestCase):
    GOOD = {"relation": "r12787516", "max_restored_m": 300,
            "reason": "reviewed: signed walk"}
    def test_a_good_list_is_normalized(self):
        out = quality.validate_dk_restore_member_ways(
            [{**self.GOOD, "reason": "  reviewed  "}])
        self.assertEqual(out, [{"relation": "r12787516",
                                "max_restored_m": 300.0,
                                "reason": "reviewed"}])
    def test_the_cap_is_bounded_by_the_hard_limit(self):
        self.assertEqual(quality.DK_RESTORE_CAP_M, 3000.0)
        for cap in (0.5, 1, 3000, 3000.0):
            with self.subTest(cap=cap):
                quality.validate_dk_restore_member_ways(
                    [{**self.GOOD, "max_restored_m": cap}])
        for cap in (0, -1, 3000.1, 5000, float("inf"), float("nan"), True,
                    "300", None, [300]):
            with self.subTest(cap=cap):
                with self.assertRaises(ValueError):
                    quality.validate_dk_restore_member_ways(
                        [{**self.GOOD, "max_restored_m": cap}])
    def test_bad_lists_are_rejected(self):
        bad = [
            None, {}, "r1", [], [None], ["r1"],
            [{"relation": "r1", "reason": "x"}],
            [{"relation": "r1", "max_restored_m": 5}],
            [{"max_restored_m": 5, "reason": "x"}],
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
                    quality.validate_dk_restore_member_ways(value)
    def test_two_distinct_relations_are_allowed(self):
        out = quality.validate_dk_restore_member_ways(
            [self.GOOD, {**self.GOOD, "relation": "r2"}])
        self.assertEqual([e["relation"] for e in out], ["r12787516", "r2"])
class EvidenceShape(unittest.TestCase):
    def audit(self, relation_id, **record):
        return {"relation_id": relation_id, "dk_restore_member_ways": {
            "status": "restored", "cap_m": 100.0, "max_share": 0.25,
            "restored_way_ids": [1], "route_role_way_ids": [],
            "restored_m": 50.0, "route_m": 500.0, "share": 0.1, **record}}
    def test_each_failure_names_the_relation_and_the_reason(self):
        audits = [
            self.audit(1), self.audit(2, status="cap-exceeded"),
            self.audit(3, status="share-exceeded", share=0.4),
            self.audit(4, status="unmeasurable"),
            self.audit(5, restored_way_ids=[]),
            self.audit(6, restored_way_ids=[], route_role_way_ids=[9]),
            {"relation_id": 7, "direct_way_members": []},
        ]
        entries = [{"relation": f"r{n}", "reason": "x"} for n in range(1, 9)]
        failures, evidence = quality.dk_restore_member_ways_evidence(
            entries, audits)
        self.assertEqual([f.split(":")[0] for f in failures],
                         ["r2", "r3", "r4", "r5", "r7", "r8"])
        self.assertIn("over max_restored_m", failures[0])
        self.assertIn("restored road share 0.4", failures[1])
        self.assertIn("cannot measure", failures[2])
        self.assertIn("stale entry", failures[3])
        self.assertIn("no restore evidence", failures[4])
        self.assertIn("not assembled", failures[5])
        self.assertEqual(sorted(evidence), ["r1", "r2", "r3", "r4", "r5", "r6"])
        self.assertEqual(evidence["r1"]["reason"], "x")
CLI_CASES = (
    # relation, name, middle-way tags, middle width in degrees of longitude
    (801, "Kørevej Rute", {**DRIVEWAY, "name": "Skovvejen"}, 0.005),
    (802, "Parkeringsrute", dict(AISLE), 0.005),
    (803, "Landevej Rute", {"highway": "secondary", "name": "Bredgade"}, 0.005),
    (804, "Fortov Rute", {"highway": "footway", "footway": "sidewalk"}, 0.005),
    (805, "Lystlund Rute", {"highway": "unclassified",
                            "motor_vehicle": "designated",
                            "name": "Lystlundvej"}, 0.005),
    (806, "Motorvej Rute", {"highway": "motorway"}, 0.005),
    (807, "Hovedvej Rute", {"highway": "primary", "foot": "no"}, 0.005),
    (808, "Fodforbud Rute", {**DRIVEWAY, "foot": "no"}, 0.005),
    (809, "Privat Rute", {**DRIVEWAY, "access": "private"}, 0.005),
    (810, "Ren Rute", dict(PATH), 0.005),
    (811, "Rolle Rute", dict(PATH), 0.005),
    (812, "Lang Rute", dict(DRIVEWAY), 0.02),
    (813, "Bilspor Rute", {"highway": "track", "motorcar": "yes"}, 0.005),
    (814, "Trunk Rute", {"highway": "trunk"}, 0.005),
)
ROLE_RELATION = 811
def build_fixture():
    osm = Osm()
    ring = [(LON0, LAT0), (LON1, LAT0), (LON1, LAT1), (LON0, LAT1),
            (LON0, LAT0)]
    boundary = osm.way(ring)
    osm.relations.append((AREA_RELATION, [boundary], {
        "type": "multipolygon", "natural": "wood", "name": AREA}))
    role_way = None
    for index, (relation_id, name, tags, width) in enumerate(CLI_CASES):
        lat = 56.01 + 0.005 * index
        mid_end = 9.05 + width
        pieces = [[(9.01, lat), (9.05, lat)], [(9.05, lat), (mid_end, lat)],
                  [(mid_end, lat), (mid_end + 0.04, lat)]]
        first, middle, last = osm.chain(pieces, **PATH)
        # The chain gives all three the same tags; replace the middle way.
        position = next(i for i, w in enumerate(osm.ways) if w[0] == middle)
        osm.ways[position] = (middle, osm.ways[position][1], dict(tags))
        osm.route(relation_id, name, [first, middle, last])
        if relation_id == ROLE_RELATION:
            role_way = middle
    xml = osm.xml()
    return xml.replace(f'<member type="way" ref="{role_way}" role=""/>',
                       f'<member type="way" ref="{role_way}" role="route"/>')
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
    def entry(self, *relations, cap=600):
        return self.json_file([
            {"relation": f"r{r}", "max_restored_m": cap,
             "reason": "reviewed: signed walk"} for r in relations])
    def motor(self, *relations):
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
    @staticmethod
    def whole(width):
        return 9.01, round(9.05 + width + 0.04, 4)
HOLES = ("Kørevej Rute", "Parkeringsrute", "Landevej Rute", "Fortov Rute",
         "Lystlund Rute", "Motorvej Rute", "Hovedvej Rute", "Fodforbud Rute",
         "Privat Rute", "Rolle Rute", "Lang Rute", "Bilspor Rute", "Trunk Rute")
RESTORABLE = (801, 802, 803, 804, 805, 811)
class RestoreMemberWays(CliCase):
    def test_default_withdraws_every_route_with_a_dropped_member(self):
        # Control: no opt-in, today's behavior. The dropped member leaves a hole
        # wider than the 100 m bridge limit, so the relation-gap gate withdraws it.
        result, trails, removed, report, _ = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.span(trails["Ren Rute"]), self.whole(0.005))
        for name in HOLES:
            with self.subTest(name=name):
                self.assertNotIn(name, trails)
                self.assertEqual(removed[name]["removed_category"],
                                 "unresolved-relation-gap")
        self.assertNotIn("dk_restore_member_ways", report)
    def test_opt_in_publishes_the_whole_route_for_each_shape(self):
        listed = self.entry(*RESTORABLE)
        result, trails, _, report, _ = self.run_cli(
            "--dk-restore-member-ways", listed)
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in ("Kørevej Rute", "Parkeringsrute", "Landevej Rute",
                     "Fortov Rute", "Lystlund Rute", "Rolle Rute"):
            with self.subTest(name=name):
                self.assertEqual(self.span(trails[name]), self.whole(0.005))
                self.assertEqual(
                    len(trails[name]["geometry"]["coordinates"]), 1)
        evidence = report["dk_restore_member_ways"]
        self.assertEqual(set(evidence), {f"r{r}" for r in RESTORABLE})
        for relation in (801, 802, 803, 804, 805):
            record = evidence[f"r{relation}"]
            self.assertEqual(len(record["restored_way_ids"]), 1)
            self.assertEqual(record["status"], "restored")
            self.assertEqual(record["cap_m"], 600.0)
            self.assertGreater(record["restored_m"], 300.0)
        self.assertEqual(evidence["r811"]["restored_way_ids"], [])
        self.assertEqual(len(evidence["r811"]["route_role_way_ids"]), 1)
        self.assertEqual(report["status"], "ok")
    def test_opt_in_is_per_relation(self):
        _, base, _, _, _ = self.run_cli()
        result, opted, _, _, _ = self.run_cli(
            "--dk-restore-member-ways", self.entry(801))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.span(opted["Kørevej Rute"]), self.whole(0.005))
        # Every other route is exactly as without the opt-in (r802 has the same
        # kind of road but is not named).
        for name in set(base) - {"Kørevej Rute"}:
            with self.subTest(name=name):
                self.assertEqual(base[name], opted[name])
        self.assertEqual(set(opted), set(base) | {"Kørevej Rute"})
    def test_motorway_trunk_and_primary_routes_stay_withdrawn(self):
        listed = self.entry(806, 807, 814)
        result, trails, removed, report, _ = self.run_cli(
            "--dk-restore-member-ways", listed)
        # Each entry is stale (nothing the opt-in may restore), so the area
        # fails closed, and the roads are not in the published routes.
        self.assertEqual(result.returncode, 2)
        self.assertIn("dk-restore-member-ways failures", result.stderr)
        for relation in (806, 807, 814):
            self.assertIn(f"r{relation}: no member way needs the opt-in",
                          report["failure"])
        for name in ("Motorvej Rute", "Hovedvej Rute", "Trunk Rute"):
            with self.subTest(name=name):
                self.assertNotIn(name, trails)
                self.assertEqual(removed[name]["removed_category"],
                                 "unresolved-relation-gap")
    def test_routes_that_fail_a_member_gate_stay_withdrawn(self):
        listed = self.entry(808, 809, 813)
        result, trails, removed, report, _ = self.run_cli(
            "--dk-restore-member-ways", listed)
        self.assertEqual(result.returncode, 2)
        for relation in (808, 809, 813):
            self.assertIn(f"r{relation}: no member way needs the opt-in",
                          report["failure"])
        for name in ("Fodforbud Rute", "Privat Rute", "Bilspor Rute"):
            with self.subTest(name=name):
                self.assertNotIn(name, trails)
                self.assertEqual(removed[name]["removed_category"],
                                 "unresolved-relation-gap")
    def test_a_route_without_dropped_members_fails_closed(self):
        result, _, _, report, _ = self.run_cli(
            "--dk-restore-member-ways", self.entry(810))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(report["status"], "failed")
        self.assertIn("r810: no member way needs the opt-in", report["failure"])
    def test_an_unknown_relation_fails_closed(self):
        result, _, _, report, _ = self.run_cli(
            "--dk-restore-member-ways", self.entry(99999))
        self.assertEqual(result.returncode, 2)
        self.assertIn("r99999: relation was not assembled in this area",
                      report["failure"])
    def test_a_stale_entry_does_not_hide_a_good_one(self):
        result, trails, _, report, _ = self.run_cli(
            "--dk-restore-member-ways", self.entry(801, 810))
        self.assertEqual(result.returncode, 2)
        self.assertIn("r810", report["failure"])
        self.assertNotIn("r801:", report["failure"])
        self.assertEqual(self.span(trails["Kørevej Rute"]), self.whole(0.005))
    def test_the_cap_fails_closed_and_leaves_the_route_withdrawn(self):
        # r812 restores ~1243 m of driveway: a 1200 m cap refuses it, 1300 m
        # accepts it (the route is 20 % driveway, under the share limit).
        result, trails, removed, report, _ = self.run_cli(
            "--dk-restore-member-ways", self.entry(812, cap=1200))
        self.assertEqual(result.returncode, 2)
        self.assertIn("r812: restored road length", report["failure"])
        self.assertIn("is over max_restored_m 1200 m", report["failure"])
        self.assertNotIn("Lang Rute", trails)
        self.assertEqual(removed["Lang Rute"]["removed_category"],
                         "unresolved-relation-gap")
        evidence = report["dk_restore_member_ways"]["r812"]
        self.assertEqual(evidence["status"], "cap-exceeded")
        self.assertGreater(evidence["restored_m"], 1200.0)
        self.assertEqual(evidence["cap_m"], 1200.0)
        result, trails, _, report, _ = self.run_cli(
            "--dk-restore-member-ways", self.entry(812, cap=1300))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.span(trails["Lang Rute"]), self.whole(0.02))
        self.assertEqual(report["dk_restore_member_ways"]["r812"]["status"],
                         "restored")
    def test_the_two_opt_ins_compose(self):
        result, trails, _, report, _ = self.run_cli(
            "--dk-restore-member-ways", self.entry(801),
            "--dk-keep-motor-tracks", self.motor(813))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.span(trails["Kørevej Rute"]), self.whole(0.005))
        self.assertEqual(self.span(trails["Bilspor Rute"]), self.whole(0.005))
        self.assertEqual(set(report["dk_restore_member_ways"]), {"r801"})
        self.assertEqual(set(report["dk_keep_motor_tracks"]), {"r813"})
    def test_the_opt_in_is_dk_v3_exact_denmark_only(self):
        listed = self.entry(801)
        for rules in ("legacy", "dk-v2"):
            with self.subTest(rules=rules):
                result = self.run_cli("--dk-restore-member-ways", listed,
                                      rules=rules)[0]
                self.assertEqual(result.returncode, 2)
                self.assertIn("requires --dk-rules dk-v3", result.stderr)
        result = self.run_cli("--dk-restore-member-ways", listed,
                              exact=False)[0]
        self.assertEqual(result.returncode, 2)
        self.assertIn("limited to exact Denmark assembly", result.stderr)
        for region in ("ca", "us"):
            with self.subTest(region=region):
                result = self.run_cli("--dk-restore-member-ways", listed,
                                      region=region, rules="dk-v3")[0]
                self.assertEqual(result.returncode, 2)
                self.assertIn("limited to exact Denmark assembly",
                              result.stderr)
    def test_invalid_files_are_rejected_before_any_work(self):
        good = {"relation": "r801", "max_restored_m": 100, "reason": "x"}
        bad = ["not json", {"relation": "r801"}, [], [{"relation": "r801"}],
               [{**good, "relation": "801"}], [{**good, "reason": " "}],
               [good, dict(good)], [{**good, "extra": 1}],
               [{**good, "max_restored_m": 0}],
               [{**good, "max_restored_m": 3001}],
               [{**good, "max_restored_m": "100"}]]
        for payload in bad:
            with self.subTest(payload=str(payload)[:60]):
                path = self.json_file(payload)
                result = subprocess.run(
                    [sys.executable, str(HERE / "assemble.py"),
                     "--in", str(self.dir / "missing.pbf"), "--out", "y.geojson",
                     "--region", "dk", "--require-exact-area",
                     "--dk-rules", "dk-v3", "--dk-restore-member-ways", path],
                    capture_output=True, text=True)
                self.assertEqual(result.returncode, 2)
                self.assertIn("--dk-restore-member-ways is invalid",
                              result.stderr)
    def test_no_flag_leaves_the_report_without_the_key(self):
        _, _, _, report, _ = self.run_cli()
        self.assertNotIn("dk_restore_member_ways", report)
QUALITY_SOURCE = (HERE / "quality.py").read_text(encoding="utf-8")
QUALITY_MUTANTS = (
    ("validator: list required",
     (('if not isinstance(entries, list) or not entries:\n        raise '
       'ValueError("restore_member_ways must be a non-empty list")',
       'if not isinstance(entries, list):\n        raise '
       'ValueError("restore_member_ways must be a non-empty list")'),)),
    ("validator: exact keys",
     (("or set(entry) != DK_RESTORE_MEMBER_WAYS_KEYS)",
       "or not set(entry) >= DK_RESTORE_MEMBER_WAYS_KEYS)"),)),
    ("validator: keys are relation, max_restored_m, reason",
     (('DK_RESTORE_MEMBER_WAYS_KEYS = {"relation", "max_restored_m", "reason"}',
       'DK_RESTORE_MEMBER_WAYS_KEYS = {"relation", "reason"}'),)),
    ("validator: relation id pattern",
     (("DK_ACCEPT_GAP_ID.fullmatch(relation)):\n            raise ValueError(\n"
       "                f\"restore_member_ways relation is invalid",
       "DK_ACCEPT_GAP_ID.match(relation + 'x')):\n            raise ValueError(\n"
       "                f\"restore_member_ways relation is invalid"),)),
    ("validator: cap is a number",
     (("if (isinstance(cap, bool) or not isinstance(cap, (int, float))\n"
       "                or not math.isfinite(cap) or not 0 < cap <= DK_RESTORE_CAP_M):",
       "if (not isinstance(cap, (int, float))\n"
       "                or not math.isfinite(cap) or not 0 < cap <= DK_RESTORE_CAP_M):"),)),
    ("validator: cap is positive",
     (("or not 0 < cap <= DK_RESTORE_CAP_M):", "or not cap <= DK_RESTORE_CAP_M):"),)),
    ("validator: cap is hard-limited",
     (("or not 0 < cap <= DK_RESTORE_CAP_M):", "or not 0 < cap):"),)),
    ("validator: the hard limit value",
     (("DK_RESTORE_CAP_M = 3000.0", "DK_RESTORE_CAP_M = 5000.0"),)),
    ("validator: reason required",
     (('if not isinstance(entry["reason"], str) or not entry["reason"].strip():\n'
       '            raise ValueError(\n                f"restore_member_ways {relation}: reason is required")',
       'pass'),)),
    ("validator: no repeats",
     (('if relation in seen:\n            raise ValueError(\n'
       '                f"restore_member_ways repeats relation {relation!r}")',
       'pass'),)),
    ("evidence: absent relation fails",
     (('f"(restore_member_ways)")\n            continue', ')\n            continue'),)),
    ("evidence: missing record fails",
     (('        if not isinstance(record, dict):\n            failures.append('
       'f"{relation}: no restore evidence (stale entry)")\n            continue',
       '        if not isinstance(record, dict):\n            continue'),)),
    ("evidence: cap exceeded fails",
     (('        if status == "cap-exceeded":', '        if False:'),)),
    ("evidence: share exceeded fails",
     (('        elif status == "share-exceeded":', '        elif False:'),)),
    ("evidence: unmeasurable fails",
     (('        elif status == "unmeasurable":', '        elif False:'),)),
    ("evidence: stale entry fails",
     (('        elif not record.get("restored_way_ids") and not record.get(\n'
       '                "route_role_way_ids"):', '        elif False:'),)),
    ("evidence: a route-role restore is not stale",
     (('        elif not record.get("restored_way_ids") and not record.get(\n'
       '                "route_role_way_ids"):',
       '        elif not record.get("restored_way_ids"):'),)),
    ("evidence: a road restore is not stale",
     (('        elif not record.get("restored_way_ids") and not record.get(\n'
       '                "route_role_way_ids"):',
       '        elif not record.get("route_role_way_ids"):'),)),
    ("evidence: the entry reason is recorded",
     (('evidence[relation] = {"reason": entry["reason"], **record}',
       'evidence[relation] = {**record}'),)),
)
def quality_table(module):
    out = []
    good = {"relation": "r5", "max_restored_m": 100, "reason": "why"}
    for value in (None, [], [good], [good, good], [{"relation": "r5"}],
                  [{**good, "relation": "5"}], [{**good, "reason": " "}],
                  [{**good, "extra": 1}], [good, {**good, "relation": "r6"}],
                  [{**good, "max_restored_m": 0}], [{**good, "max_restored_m": -5}],
                  [{**good, "max_restored_m": 3000}],
                  [{**good, "max_restored_m": 3001}],
                  [{**good, "max_restored_m": 4000}],
                  [{**good, "max_restored_m": True}],
                  [{**good, "max_restored_m": "100"}],
                  [{"relation": "r5", "reason": "why"}]):
        try:
            out.append(module.validate_dk_restore_member_ways(value))
        except ValueError as error:
            out.append(("error", str(error)))
    def audit(relation_id, **record):
        return {"relation_id": relation_id, "dk_restore_member_ways": {
            "status": "restored", "cap_m": 100.0, "max_share": 0.25,
            "restored_way_ids": [1], "route_role_way_ids": [],
            "restored_m": 50.0, "route_m": 500.0, "share": 0.1, **record}}
    audits = [audit(1), audit(2, status="cap-exceeded"),
              audit(3, status="share-exceeded", share=0.4),
              audit(4, status="unmeasurable"),
              audit(5, restored_way_ids=[]),
              audit(6, restored_way_ids=[], route_role_way_ids=[9]),
              audit(7, route_role_way_ids=[9]),
              {"relation_id": 8, "direct_way_members": []}]
    for relation in range(1, 10):
        out.append(module.dk_restore_member_ways_evidence(
            [{"relation": f"r{relation}", "reason": "x"}], audits))
    return out
class QualityMutations(unittest.TestCase):
    def test_every_mutant_changes_the_table(self):
        def load(source):
            return load_source("quality_mutant", "quality.py", source)
        baseline = quality_table(quality)
        self.assertEqual(quality_table(load(QUALITY_SOURCE)), baseline)
        survivors = []
        for label, edits in QUALITY_MUTANTS:
            with self.subTest(mutant=label):
                try:
                    changed = quality_table(
                        load(mutate(QUALITY_SOURCE, edits))) != baseline
                except AssertionError:
                    raise
                except Exception:
                    changed = True
                if not changed:
                    survivors.append(label)
        self.assertEqual(survivors, [])
if __name__ == "__main__":
    unittest.main()
