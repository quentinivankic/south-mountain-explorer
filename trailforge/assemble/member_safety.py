"""Shared deterministic safety policy for Denmark route-relation members.

This module is intentionally pure: it accepts OSM tag mappings and returns a
structured decision. The assembler and final pilot validator share this policy,
while source geometry and relation membership remain independently verified.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

TRAILISH_HIGHWAYS = frozenset({
    "path", "footway", "steps", "track", "bridleway", "via_ferrata",
    "cycleway", "pedestrian",
})
RESTORABLE_RELATION_HIGHWAYS = frozenset({
    "track", "residential", "service", "unclassified", "living_street",
    "pedestrian",
})
POSITIVE_FOOT_VALUES = frozenset({"yes", "designated", "permissive"})
# Canonical spellings after service-token space/hyphen/underscore folding.
FORBIDDEN_SERVICE_TOKENS = frozenset({
    "parking_aisle", "driveway", "drive_through", "drivethrough", "parking",
    "parking_space", "emergency_access", "bus",
})
# Every present highway=service token must be explicitly ordinary. A missing
# subtype remains compatible with the documented positive-foot policy.
ORDINARY_SERVICE_TOKENS = frozenset({"alley"})
ALWAYS_MOTORIZED_ACCESS_KEYS = (
    "4wd_only", "atv", "ohv", "snowmobile", "motorcycle",
)
DECISIVE_TAG_KEYS = (
    "name", "highway", "foot", "footway", "hiking", "trail", "route",
    "sac_scale", "trail_visibility", "designation", "network", "access",
    "indoor", "motor_vehicle", "motorcar", "atv", "ohv", "4wd_only",
    "snowmobile", "motorcycle", "bicycle", "tracktype", "surface", "lanes",
    "service", "piste:type", "mtb:type", "mtb:scale:imba", "oneway",
)

_BIKE_ONLY_NAME = re.compile(r"\bmtb\b|\bmountain\s*bike\b")
_BIKE_NAME_COMPOSITE = re.compile(r"/|\s+and\s+")
_BIKEPARK_NAME = re.compile(
    r"\b(down\s*hill|dh|slalom|flow|jump\s*line|pump\s*track|berm|freeride)\b"
)
_ROAD_LIKE_TRACK_NAME = re.compile(
    r"\b(road|street|avenue|boulevard|drive|lane|court|place|"
    r"canal|drain|ditch|highway|freeway|parkway|route)\b"
)
_GRID_ROAD_NAME = re.compile(
    r"^(?:[nsew]\.?\s+)?\d+\s+(?:north|south|east|west|n|s|e|w)\.?$"
)
# Preserve the two legacy branches separately: known agency forms classify by
# an unanchored numeric prefix, while unfamiliar short codes require a complete
# three-or-more-digit name so real GR20/E5-style trail names remain eligible.
_AGENCY_ROAD_CODE_PREFIX = re.compile(
    r"^(?:nf|fr|fsr|fs|usfs|blm|cr|nv)\b[-\s]?\d+"
)
_GENERIC_ROAD_CODE = re.compile(r"^[a-z]{1,4}[-\s]?\d{3,}[a-z]?$")
_SERVICE_TOKEN_SEPARATOR = re.compile(r"[\s_-]+")
_POSITIVE_LANE_COMPONENT = re.compile(r"[0-9]+")


def normalize(value: Any) -> str:
    """Normalize one OSM scalar before every policy decision."""
    return str(value or "").strip().casefold()


def _normalize_service_token(value: Any) -> str:
    """Fold equivalent service token separators to one canonical form."""
    return _SERVICE_TOKEN_SEPARATOR.sub("_", normalize(value))


def decisive_tags(tags: Mapping[str, Any]) -> dict[str, Any]:
    """Return raw decisive tags in a stable key order for audit evidence."""
    return {key: tags[key] for key in DECISIVE_TAG_KEYS if key in tags}


def service_tokens(tags: Mapping[str, Any]) -> tuple[str, ...]:
    """Return canonical semicolon-separated service tokens.

    An absent ``service`` tag is represented by an empty tuple. A present blank
    value or an empty component remains visible as ``""`` so callers fail it
    closed rather than treating it as absent. Case and runs of spaces, hyphens,
    or underscores normalize identically within each token.
    """
    if "service" not in tags:
        return ()
    return tuple(_normalize_service_token(token)
                 for token in str(tags.get("service") or "").split(";"))


def _strict_lane_components(value: Any) -> tuple[int, ...] | None:
    """Parse a nonempty semicolon lane list, or return ``None`` if unsafe."""
    normalized = normalize(value)
    if not normalized:
        return ()
    raw_components = tuple(
        component.strip() for component in normalized.split(";"))
    if any(not component or not _POSITIVE_LANE_COMPONENT.fullmatch(component)
           for component in raw_components):
        return None
    components = tuple(int(component) for component in raw_components)
    if any(component <= 0 for component in components):
        return None
    return components


@dataclass(frozen=True)
class MemberDecision:
    eligible: bool
    restored: bool
    reason: str
    service_tokens: tuple[str, ...] = ()
    road_like_track_kind: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "eligible": self.eligible,
            "restored": self.restored,
            "reason": self.reason,
            "service_tokens": list(self.service_tokens),
            "road_like_track_kind": self.road_like_track_kind,
        }


def bike_only_name(name: Any) -> bool:
    normalized = normalize(name)
    return bool(_BIKE_ONLY_NAME.search(normalized)
                and not _BIKE_NAME_COMPOSITE.search(normalized))


def nonhiking_reason(tags: Mapping[str, Any]) -> str | None:
    """Return the decisive non-hiking signal, or ``None``."""
    name = normalize(tags.get("name"))
    if "no hiking" in name:
        return "name-no-hiking"
    if bike_only_name(name):
        return "bike-only-name"
    if normalize(tags.get("foot")) == "no":
        return "foot-no"
    if normalize(tags.get("mtb:type")) in {"flow", "downhill"}:
        return "mtb-type"
    piste = normalize(tags.get("piste:type"))
    if piste and "hike" not in piste:
        return "non-hiking-piste"
    if normalize(tags.get("mtb:scale:imba")):
        if normalize(tags.get("oneway")) in {"yes", "1", "true"}:
            return "oneway-imba"
        if _BIKEPARK_NAME.search(name):
            return "bikepark-name-imba"
    return None


def motorized_reason(tags: Mapping[str, Any], *, include_road_motor: bool = True
                     ) -> str | None:
    keys = list(ALWAYS_MOTORIZED_ACCESS_KEYS)
    if include_road_motor:
        keys.extend(("motor_vehicle", "motorcar"))
    for key in keys:
        if normalize(tags.get(key)) in {"yes", "designated"}:
            return f"motorized-{key}"
    return None


def road_like_track_kind(tags: Mapping[str, Any], *,
                         ignore_motor_access: bool = False,
                         conservative_lanes: bool = True) -> str | None:
    """Classify a road-like track as ``tag``/``name``, otherwise ``None``.

    Denmark relation restoration uses conservative lane parsing. Legacy
    standalone ingest opts out so malformed historical values do not change
    disposition outside the Denmark-only widening.
    """
    if normalize(tags.get("highway")) != "track":
        return None
    if not ignore_motor_access:
        if normalize(tags.get("motor_vehicle")) in {"yes", "designated"}:
            return "tag"
        if normalize(tags.get("motorcar")) == "yes":
            return "tag"
    lanes = normalize(tags.get("lanes"))
    if conservative_lanes:
        components = _strict_lane_components(lanes)
        if lanes and (components is None
                      or any(component >= 2 for component in components)):
            return "tag"
    else:
        try:
            if int(lanes) >= 2:
                return "tag"
        except ValueError:
            pass
    name = normalize(tags.get("name"))
    if (_GRID_ROAD_NAME.fullmatch(name)
            or _AGENCY_ROAD_CODE_PREFIX.match(name)
            or _GENERIC_ROAD_CODE.fullmatch(name)
            or _ROAD_LIKE_TRACK_NAME.search(name)):
        return "name"
    return None


def standalone_member_decision(tags: Mapping[str, Any]) -> MemberDecision:
    """Apply the normalized legacy standalone relation-member policy."""
    highway = normalize(tags.get("highway"))
    if highway not in TRAILISH_HIGHWAYS:
        if highway.startswith("abandoned"):
            return MemberDecision(True, False, "standalone-abandoned")
        return MemberDecision(False, False, "highway-not-trailish")
    if highway == "footway" and normalize(tags.get("footway")) in {
            "sidewalk", "crossing"}:
        return MemberDecision(False, False, "footway-sidewalk-or-crossing")
    if normalize(tags.get("indoor")) == "yes":
        return MemberDecision(False, False, "indoor")
    if normalize(tags.get("trail")) == "no":
        return MemberDecision(False, False, "trail-no")
    road_kind = road_like_track_kind(tags, conservative_lanes=False)
    if road_kind is not None:
        return MemberDecision(False, False, f"road-like-track-{road_kind}",
                              road_like_track_kind=road_kind)
    motor_reason = motorized_reason(tags)
    if motor_reason is not None:
        return MemberDecision(False, False, motor_reason)
    nonhiking = nonhiking_reason(tags)
    if nonhiking is not None:
        return MemberDecision(False, False, nonhiking)
    return MemberDecision(True, False, "standalone-trail")


def _service_exclusion(tags: Mapping[str, Any]) -> tuple[str | None, tuple[str, ...]]:
    tokens = service_tokens(tags)
    if not tokens:
        return None, tokens
    if any(not token for token in tokens):
        return "service-blank-token", tokens
    forbidden = next((token for token in tokens
                      if token in FORBIDDEN_SERVICE_TOKENS), None)
    if forbidden is not None:
        return f"service-forbidden-{forbidden}", tokens
    if any(token not in ORDINARY_SERVICE_TOKENS for token in tokens):
        reason = ("service-unknown-token" if len(tokens) == 1
                  else "service-unknown-composite")
        return reason, tokens
    return None, tokens


def dk_relation_member_decision(tags: Mapping[str, Any]) -> MemberDecision:
    """Decide Denmark hiking-relation main-line eligibility with a reason."""
    foot = normalize(tags.get("foot"))
    explicit_foot = foot in POSITIVE_FOOT_VALUES
    if foot in {"no", "private"}:
        return MemberDecision(False, False, f"foot-{foot}")
    access = normalize(tags.get("access"))
    if access in {"no", "private"} and not explicit_foot:
        return MemberDecision(False, False, f"access-{access}")
    if normalize(tags.get("footway")) in {"sidewalk", "crossing"}:
        return MemberDecision(False, False, "footway-sidewalk-or-crossing")
    if normalize(tags.get("indoor")) == "yes":
        return MemberDecision(False, False, "indoor")
    if normalize(tags.get("trail")) == "no":
        return MemberDecision(False, False, "trail-no")
    nonhiking = nonhiking_reason(tags)
    if nonhiking is not None:
        return MemberDecision(False, False, nonhiking)
    motor_reason = motorized_reason(tags, include_road_motor=False)
    if motor_reason is not None:
        return MemberDecision(False, False, motor_reason)

    highway = normalize(tags.get("highway"))
    explicit_motor_access = any(
        normalize(tags.get(key)) in {"yes", "designated"}
        for key in ("motor_vehicle", "motorcar")
    )
    if explicit_motor_access and not explicit_foot:
        return MemberDecision(False, False, "motor-access-without-positive-foot")

    tokens: tuple[str, ...] = ()
    if highway == "service":
        if not explicit_foot:
            return MemberDecision(False, False, "service-without-positive-foot")
        service_error, tokens = _service_exclusion(tags)
        if service_error is not None:
            return MemberDecision(False, False, service_error,
                                  service_tokens=tokens)

    if highway == "track":
        road_kind = road_like_track_kind(
            tags, ignore_motor_access=explicit_motor_access,
            conservative_lanes=True)
        if road_kind is not None:
            return MemberDecision(
                False, False, f"road-like-track-{road_kind}",
                road_like_track_kind=road_kind)

    standalone = standalone_member_decision(tags)
    if standalone.eligible:
        return standalone
    if highway not in RESTORABLE_RELATION_HIGHWAYS:
        return MemberDecision(False, False, standalone.reason)
    if highway == "track":
        if explicit_foot and explicit_motor_access:
            return MemberDecision(True, True, "restored-shared-motor-track")
        return MemberDecision(False, False, standalone.reason)
    return MemberDecision(True, True, f"restored-{highway}",
                          service_tokens=tokens)
