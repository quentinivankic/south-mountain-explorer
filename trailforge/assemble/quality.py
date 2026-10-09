"""Exact-area structural and Denmark curation checks for assembled trails.

The core assembler stays geometry-source agnostic. This module uses source OSM
way/node identity plus the selected exact boundary to disposition the
runner-local Denmark pilot after clipping. It never creates connector geometry.
"""
from __future__ import annotations

from collections import Counter
import copy
from typing import Any

OVERLAP_THRESHOLD = 0.90

# Signed-route road miles and shares are evidence for manual review, never an
# arbitrary terminal threshold. Exact-area unsafe/missing/inconsistent members
# and unexplained topology own publication safety.

_FOOT_IDENTITY = {"yes", "designated", "permissive"}
_TRAIL_HIGHWAYS = {
    "path", "footway", "steps", "bridleway", "via_ferrata", "pedestrian",
}
_DECISIVE_TAGS = (
    "name", "highway", "foot", "footway", "hiking", "trail", "route",
    "sac_scale", "trail_visibility", "designation", "network", "access",
    "indoor", "motor_vehicle", "motorcar", "atv", "ohv", "4wd_only",
    "snowmobile", "motorcycle", "bicycle", "tracktype", "surface", "lanes",
    "service", "area", "piste:type", "mtb:type", "mtb:scale:imba", "oneway",
)
_QUALITY_REMOVAL_REASONS = {
    "disconnected-name-stitch": (
        "Same-name source ways are disconnected in OSM node topology, and the "
        "exact selected boundary did not create the split; not emitted as one "
        "checklist object."
    ),
    "dk-unqualified-road-track": (
        "Denmark-only rootless name stitch is composed entirely of "
        "highway=track ways with no explicit walking or trail identity."
    ),
    "nested-name-stitch-overlap": (
        "Low-authority rootless name stitch is at least 90% nested in a signed "
        "relation-backed object."
    ),
}


class _UF:
    def __init__(self, values=()):
        self.parent = {value: value for value in values}

    def find(self, value):
        self.parent.setdefault(value, value)
        while self.parent[value] != value:
            self.parent[value] = self.parent[self.parent[value]]
            value = self.parent[value]
        return value

    def union(self, left, right):
        left_root, right_root = self.find(left), self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root


def prepare_feature_sources(features: list[dict[str, Any]], trails: list,
                            ways: dict[int, dict]) -> None:
    """Attach temporary source identity needed by post-clip quality checks."""
    if len(features) != len(trails):
        raise ValueError("feature/trail count mismatch while preparing quality evidence")
    for feature, trail in zip(features, trails):
        geometry_ways = list(dict.fromkeys(
            getattr(trail, "geometry_ways", trail.member_ways)))
        relation_ids = list(dict.fromkeys(getattr(trail, "relation_ids", [])))
        root_relation_ids = list(dict.fromkeys(
            getattr(trail, "root_relation_ids", [])))
        direct = {
            int(relation_id): list(dict.fromkeys(member_ids))
            for relation_id, member_ids in
            getattr(trail, "direct_relation_ways", {}).items()
        }
        missing = [way_id for way_id in geometry_ways if way_id not in ways]
        restored = list(dict.fromkeys(
            getattr(trail, "restored_relation_ways", [])))
        if any(way_id not in geometry_ways for way_id in restored):
            raise ValueError("restored relation way is not rendered geometry")
        feature["properties"]["_quality_source"] = {
            "geometry_way_ids": geometry_ways,
            "relation_ids": relation_ids,
            "root_relation_ids": root_relation_ids,
            "direct_relation_way_ids": direct,
            "restored_relation_way_ids": restored,
            "missing_way_ids": missing,
        }


def finalize_dropped_feature_sources(
        features: list[dict[str, Any]], *, ways: dict[int, dict],
        nodes: dict[int, tuple[float, float]], area_union,
        area_name: str, min_length_mi: float) -> None:
    """Persist relation authority on curation and exact-clip drop rows."""
    for feature in features:
        properties = feature.get("properties") or {}
        source = properties.pop("_quality_source", None)
        if properties.get("source") != "relation" or not isinstance(source, dict):
            continue
        records, missing = _source_way_records(source, ways, nodes, area_union)
        properties.update({
            "area": area_name,
            "relation_ids": list(source.get("relation_ids") or []),
            "root_relation_ids": list(source.get("root_relation_ids") or []),
            "direct_relation_way_ids": {
                int(relation_id): list(member_ids)
                for relation_id, member_ids in
                (source.get("direct_relation_way_ids") or {}).items()
            },
            "source_geometry_way_ids": list(
                source.get("geometry_way_ids") or []),
            "restored_relation_way_ids": list(
                source.get("restored_relation_way_ids") or []),
            "source_ways": records,
            "rendered_source_way_ids": [
                record["way_id"] for record in records
                if record.get("renders_in_area") is True
            ],
        })
        if missing:
            properties["missing_source_way_ids"] = missing
        if not isinstance(properties.get("drop_evidence"), dict):
            properties["drop_evidence"] = {
                "kind": "curation",
                "category": properties.get("removed_category"),
                "min_length_mi": float(min_length_mi),
            }


def _geometry_lines(feature: dict[str, Any]) -> list[list[list[float]]]:
    geometry = feature.get("geometry") or {}
    coordinates = geometry.get("coordinates")
    if geometry.get("type") == "LineString":
        return [coordinates] if isinstance(coordinates, list) else []
    if geometry.get("type") == "MultiLineString" and isinstance(coordinates, list):
        return coordinates
    return []


def geometry_component_count(feature: dict[str, Any]) -> int:
    """Count rendered line components joined at an exact coordinate vertex."""
    lines = _geometry_lines(feature)
    if not lines or any(not isinstance(line, list) or len(line) < 2 for line in lines):
        return 0
    union = _UF(range(len(lines)))
    owner: dict[tuple[float, float], int] = {}
    for index, line in enumerate(lines):
        for coordinate in line:
            if not isinstance(coordinate, (list, tuple)) or len(coordinate) < 2:
                return 0
            point = (float(coordinate[0]), float(coordinate[1]))
            if point in owner:
                union.union(index, owner[point])
            else:
                owner[point] = index
    return len({union.find(index) for index in range(len(lines))})


def _source_way_records(source: dict[str, Any], ways: dict[int, dict],
                        nodes: dict[int, tuple[float, float]], area_union
                        ) -> tuple[list[dict], list[int]]:
    """Serialize complete source-node evidence for independent validation."""
    from shapely.geometry import LineString

    direct = {
        int(relation_id): set(member_ids)
        for relation_id, member_ids in
        (source.get("direct_relation_way_ids") or {}).items()
    }
    records = []
    missing = set(source.get("missing_way_ids") or [])
    for way_id in source.get("geometry_way_ids") or []:
        way = ways.get(way_id)
        source_node_ids = list((way or {}).get("nodes") or [])
        node_ids = [node_id for node_id in source_node_ids if node_id in nodes]
        missing_node_ids = [node_id for node_id in source_node_ids
                            if node_id not in nodes]
        # Match model.way_coords: incomplete extracts may omit individual nodes,
        # but a way remains serializable when at least two referenced nodes
        # resolve. It still carries unavailable-evidence status until complete.
        if way is None or len(node_ids) < 2:
            missing.add(way_id)
            continue
        if missing_node_ids:
            missing.add(way_id)
        coordinates = [nodes[node_id] for node_id in node_ids]
        try:
            intersection = LineString(coordinates).intersection(area_union)
            renders = not intersection.is_empty and intersection.length > 0
        except Exception:  # noqa: BLE001 - malformed source becomes missing evidence
            missing.add(way_id)
            continue
        tags = way.get("tags") or {}
        records.append({
            "way_id": way_id,
            "node_ids": node_ids,
            "missing_node_ids": missing_node_ids,
            "coordinates": [[float(lon), float(lat)] for lon, lat in coordinates],
            "tags": {key: tags[key] for key in _DECISIVE_TAGS if key in tags},
            "direct_relation_ids": [
                relation_id for relation_id, member_ids in direct.items()
                if way_id in member_ids
            ],
            "renders_in_area": renders,
        })
    return records, sorted(missing)


def _source_component_count(records: list[dict]) -> int:
    if not records:
        return 0
    way_ids = [record["way_id"] for record in records]
    union = _UF(way_ids)
    owner: dict[int, int] = {}
    for record in records:
        for node_id in record["node_ids"]:
            if node_id in owner:
                union.union(record["way_id"], owner[node_id])
            else:
                owner[node_id] = record["way_id"]
    return len({union.find(way_id) for way_id in way_ids})


def _exact_boundary_clip_matches(feature: dict[str, Any], records: list[dict],
                                 area_union) -> bool:
    """Prove rendered geometry is exactly the selected-boundary source clip."""
    from shapely.geometry import LineString, shape
    from shapely.ops import unary_union

    if not records:
        return False
    try:
        source_geometry = unary_union([
            LineString(record["coordinates"]) for record in records
        ])
        expected = source_geometry.intersection(area_union)
        rendered = shape(feature["geometry"])
        if expected.is_empty or rendered.is_empty:
            return False
        return expected.equals(rendered)
    except Exception:  # noqa: BLE001 - malformed geometry cannot prove a split
        return False


def _connectivity(feature: dict[str, Any], records: list[dict],
                  missing_way_ids: list[int], area_union,
                  outside_gap_explained: bool = False) -> dict:
    source_components = _source_component_count(records)
    postclip_components = geometry_component_count(feature)
    exact_clip = _exact_boundary_clip_matches(feature, records, area_union)
    source_complete_for_area = source_components == 1 or outside_gap_explained
    boundary_split = (
        source_complete_for_area
        and postclip_components > 1
        and exact_clip
    )
    accepted = (
        not missing_way_ids
        and source_complete_for_area
        and postclip_components > 0
        and exact_clip
        and (postclip_components == 1 or boundary_split)
    )
    reasons = []
    if source_components == 1:
        reasons.append("shared-osm-node")
    if outside_gap_explained:
        reasons.append("out-of-area-member-gap")
    if boundary_split:
        reasons.append("boundary-induced-split")
    status = "accepted" if accepted else "rejected"
    if missing_way_ids:
        status = "unavailable-evidence"
    return {
        "status": status,
        "accepted_reasons": reasons,
        "source_components": source_components,
        "postclip_components": postclip_components,
        "missing_way_ids": missing_way_ids,
        "exact_boundary_clip": exact_clip,
    }


def _line_parts(geometry) -> list:
    if geometry.is_empty:
        return []
    if geometry.geom_type == "LineString":
        return [geometry]
    if geometry.geom_type in {"MultiLineString", "GeometryCollection"}:
        return [part for item in geometry.geoms for part in _line_parts(item)]
    return []


def _linework_miles(lines: list) -> float:
    import model

    return sum(model.line_mi([
        (float(point[0]), float(point[1])) for point in line
    ]) for line in lines if len(line) >= 2)


def _clipped_record_miles(record: dict, area_union) -> float:
    """Measure one source way after exact-area clipping, without rounding."""
    from shapely.geometry import LineString

    clipped = LineString(record["coordinates"]).intersection(area_union)
    return _linework_miles([
        [[float(x), float(y)] for x, y in part.coords]
        for part in _line_parts(clipped)
    ])


def _restored_relation_measurements(
        feature: dict[str, Any], records: list[dict],
        restored_way_ids: list[int], direct: dict[int, list[int]], area_union
        ) -> list[tuple[dict, float, float,
                        dict[tuple[int, int], float], set[tuple[int, int]]]]:
    """Measure each relation only from its clipped direct source-way records."""
    records_by_id = {record["way_id"]: record for record in records}
    restored = set(restored_way_ids)
    properties = feature.get("properties") or {}
    relation_ids = list(dict.fromkeys(properties.get("relation_ids") or []))
    out = []
    for relation_id in relation_ids:
        relation_way_ids = []
        tuple_miles = {}
        restored_tuples = set()
        for way_id in dict.fromkeys(direct.get(relation_id, [])):
            record = records_by_id.get(way_id)
            if (record is None
                    or relation_id not in record.get("direct_relation_ids", [])):
                continue
            relation_way_ids.append(way_id)
            relation_way = (relation_id, way_id)
            tuple_miles[relation_way] = _clipped_record_miles(
                record, area_union)
            if way_id in restored:
                restored_tuples.add(relation_way)
        restored_way_ids_for_relation = [
            way_id for way_id in relation_way_ids
            if (relation_id, way_id) in restored_tuples
        ]
        total_miles = sum(tuple_miles.values())
        restored_miles = sum(
            tuple_miles[relation_way] for relation_way in restored_tuples)
        share = restored_miles / total_miles if total_miles > 0 else 0.0
        out.append(({
            "name": properties.get("name"),
            "key": properties.get("ckey"),
            "relation_id": relation_id,
            "relation_way_ids": relation_way_ids,
            "restored_way_ids": restored_way_ids_for_relation,
            "restored_ways": [
                {
                    "way_id": way_id,
                    "tags": dict(records_by_id[way_id].get("tags") or {}),
                }
                for way_id in restored_way_ids_for_relation
            ],
            "restored_way_count": len(restored_way_ids_for_relation),
            "restored_miles": round(restored_miles, 6),
            "total_relation_miles": round(total_miles, 6),
            "restored_share": round(share, 6),
        }, restored_miles, total_miles, tuple_miles, restored_tuples))
    return out


def walking_identity(source_ways: list[dict]) -> str:
    """Classify rendered source-way evidence for Denmark disposition."""
    if not source_ways:
        return "unknown"
    tags = [record.get("tags") or {} for record in source_ways]
    for record in tags:
        foot = str(record.get("foot", "")).strip().lower()
        if foot in _FOOT_IDENTITY:
            return "explicit"
        if str(record.get("trail", "")).strip().lower() in {"yes", "designated"}:
            return "explicit"
        if str(record.get("route", "")).strip().lower() in {
                "hiking", "foot", "walking", "running"}:
            return "explicit"
        if str(record.get("sac_scale", "")).strip():
            return "explicit"
        if str(record.get("trail_visibility", "")).strip():
            return "explicit"
        if str(record.get("hiking", "")).strip().lower() in _FOOT_IDENTITY:
            return "explicit"
        designation = str(record.get("designation", "")).strip().lower()
        if any(word in designation for word in ("foot", "path", "hiking", "walking")):
            return "explicit"
        if str(record.get("highway", "")).strip().lower() in _TRAIL_HIGHWAYS:
            return "explicit"
    if all(str(record.get("highway", "")).strip().lower() == "track"
           for record in tags):
        return "weak-track"
    return "other"


def overlap_ratio(left: dict[str, Any], right: dict[str, Any]) -> tuple[float, float, float]:
    """Return overlap share of (shorter, left, right) line geometry."""
    from shapely.geometry import shape

    left_geometry = shape(left["geometry"])
    right_geometry = shape(right["geometry"])
    left_length, right_length = left_geometry.length, right_geometry.length
    if left_length <= 0 or right_length <= 0:
        return 0.0, 0.0, 0.0
    try:
        overlap = left_geometry.intersection(right_geometry).length
    except Exception:  # noqa: BLE001 - invalid linework is not overlap evidence
        return 0.0, 0.0, 0.0
    left_share = min(1.0, overlap / left_length)
    right_share = min(1.0, overlap / right_length)
    shorter_share = min(1.0, overlap / min(left_length, right_length))
    return shorter_share, left_share, right_share


def _mark_removed(feature: dict[str, Any], category: str) -> dict[str, Any]:
    properties = feature["properties"]
    properties.pop("_quality_source", None)
    properties["quality_disposition"] = category
    properties["removed_category"] = category
    properties["removed_reason"] = _QUALITY_REMOVAL_REASONS[category]
    return feature


def _removed_record(state: dict, side: str) -> dict:
    properties = state["feature"]["properties"]
    return {
        "side": side,
        "candidate_index": state["index"],
        "key": properties.get("ckey"),
        "name": properties.get("name"),
        "category": state["removed_category"],
    }


def _pair_disposition(left: dict, right: dict) -> str:
    removed = [state for state in (left, right) if state["removed_category"]]
    if removed:
        categories = {state["removed_category"] for state in removed}
        if len(categories) == 1:
            return f"removed-{next(iter(categories))}"
        return "removed-multiple-candidates"

    left_properties = left["feature"]["properties"]
    right_properties = right["feature"]["properties"]
    left_relation = left_properties.get("source") == "relation"
    right_relation = right_properties.get("source") == "relation"
    if left_relation and right_relation:
        return "preserved-shared-signed-routes"
    if left_relation != right_relation:
        rootless = right if left_relation else left
        if rootless["identity"] == "explicit":
            return "preserved-explicit-walking-or-path"
        if rootless["identity"] == "other":
            return "preserved-other-needs-review"
        return "needs-review-missing-source-evidence"
    if "other" in {left["identity"], right["identity"]}:
        return "preserved-other-needs-review"
    return "preserved-nonweak-overlap"


def _audit_way_exact_area_status(record: dict, ways: dict, nodes: dict,
                                 area_union) -> str:
    """Classify raw source geometry against the exact boundary."""
    from shapely.geometry import LineString

    coordinates = record.get("source_coordinates")
    if not isinstance(coordinates, list) or len(coordinates) < 2 \
            or any(point is None for point in coordinates):
        way = ways.get(record.get("way_id")) or {}
        node_ids = way.get("nodes") or []
        if len(node_ids) < 2 or any(node_id not in nodes for node_id in node_ids):
            return "unknown"
        coordinates = [nodes[node_id] for node_id in node_ids]
        record["source_node_ids"] = list(node_ids)
        record["source_coordinates"] = [
            [float(lon), float(lat)] for lon, lat in coordinates
        ]
        record["source_missing_node_ids"] = []
    try:
        geometry = LineString(coordinates)
        clipped = geometry.intersection(area_union)
    except Exception:  # noqa: BLE001 - malformed authority fails closed
        return "unknown"
    return ("intersects" if not clipped.is_empty and clipped.length > 0
            else "outside")


def _relation_exact_area_status(relation_id: int, raw_scope: dict,
                                area_union) -> str:
    """Classify a relation's recursive canonical/main raw geometry."""
    from shapely.geometry import LineString

    relations = raw_scope.get("relations", {}) if raw_scope else {}
    seen: set[int] = set()

    def walk(current_id: int) -> list[str]:
        if current_id in seen:
            return []
        seen.add(current_id)
        relation = relations.get(current_id)
        if relation is None:
            return ["unknown"]
        statuses = []
        direct_ways = relation.get("direct_ways", {})
        for member in relation.get("members", []):
            role = str(member.get("role") or "").strip().casefold()
            if role not in {"", "main"}:
                continue
            if member.get("type") == "relation":
                statuses.extend(walk(member.get("ref")))
                continue
            if member.get("type") != "way":
                continue
            way = direct_ways.get(member.get("ref"))
            coordinates = (way or {}).get("coordinates")
            if ((way or {}).get("status") != "present"
                    or not isinstance(coordinates, list)
                    or len(coordinates) < 2
                    or any(point is None for point in coordinates)):
                statuses.append("unknown")
                continue
            try:
                clipped = LineString(coordinates).intersection(area_union)
            except Exception:  # noqa: BLE001
                statuses.append("unknown")
                continue
            statuses.append(
                "intersects" if not clipped.is_empty and clipped.length > 0
                else "outside")
        return statuses

    statuses = walk(relation_id)
    if "intersects" in statuses:
        return "intersects"
    if not statuses or "unknown" in statuses:
        return "unknown"
    return "outside"


def _classify_relation_audits(audits: list[dict], ways: dict, nodes: dict,
                              area_union,
                              relation_scope_evidence: dict | None) -> None:
    """Annotate every direct member and relation with scope/area disposition."""
    has_three_scopes = relation_scope_evidence is not None
    raw_scope = ((relation_scope_evidence or {}).get("raw-denmark") or {})
    for audit in audits:
        removed_root = audit.get("assembly_status") == "removed-thru-hike"
        for relation_key, status in (
                audit.get("relation_scope_statuses") or {}).items():
            try:
                relation_id = int(relation_key)
            except (TypeError, ValueError):
                relation_id = 0
            exact_status = _relation_exact_area_status(
                relation_id, raw_scope, area_union)
            status["exact_area_status"] = exact_status
            terminal_reason = None
            if removed_root:
                disposition = "removed-thru-hike-informational"
            elif status.get("raw_status") != "present":
                disposition = "missing-source-relation"
                terminal_reason = "missing-source-relation"
            elif status.get("prefilter_status") != "present":
                if exact_status == "outside":
                    disposition = "out-of-area-prefilter-relation-absence"
                elif exact_status == "intersects":
                    disposition = "prefilter-data-loss-relation"
                    terminal_reason = "prefilter-data-loss-relation"
                else:
                    disposition = "relation-location-unknown"
                    terminal_reason = "relation-location-unknown"
            elif status.get("aoi_status") != "present":
                if exact_status == "outside":
                    disposition = "out-of-aoi-relation-informational"
                elif exact_status == "intersects":
                    disposition = "aoi-relation-loss"
                    terminal_reason = "aoi-relation-loss"
                else:
                    disposition = "relation-location-unknown"
                    terminal_reason = "relation-location-unknown"
            else:
                disposition = "relation-present-in-aoi"
            status["render_disposition"] = disposition
            status["terminal_reason"] = terminal_reason
        for record in audit.get("direct_way_members") or []:
            raw_status = record.get("raw_status") or (
                "present" if record.get("source_status") == "available"
                else "missing")
            prefilter_status = record.get("prefilter_status") or (
                raw_status if not has_three_scopes else "missing")
            aoi_status = record.get("aoi_status") or (
                raw_status if not has_three_scopes else "missing")
            record["raw_status"] = raw_status
            record["prefilter_status"] = prefilter_status
            record["aoi_status"] = aoi_status
            exact_status = _audit_way_exact_area_status(
                record, ways, nodes, area_union)
            record["exact_area_status"] = exact_status
            terminal_reason = None
            if removed_root:
                disposition = "removed-thru-hike-informational"
            elif raw_status != "present":
                disposition = "raw-source-unavailable"
                terminal_reason = "raw-missing-relevant-member"
            elif prefilter_status != "present":
                disposition = "prefilter-data-loss"
                if exact_status != "outside":
                    terminal_reason = "prefilter-data-loss"
                else:
                    disposition = "out-of-area-prefilter-absence"
            elif aoi_status != "present":
                disposition = "aoi-absent"
                if exact_status == "intersects":
                    terminal_reason = "aoi-extraction-loss"
                    disposition = "aoi-extraction-loss"
                elif exact_status == "outside":
                    disposition = "out-of-aoi-informational"
                else:
                    terminal_reason = "aoi-loss-location-unknown"
            elif record.get("status") == "excluded":
                role = record.get("effective_role")
                if role in {"alternative", "alternate", "excursion"}:
                    disposition = "variant-role-informational"
                elif exact_status == "outside":
                    disposition = "unsafe-outside-exact-area"
                else:
                    disposition = "unsafe-member-in-exact-area"
                    terminal_reason = "unsafe-member-in-exact-area"
            elif exact_status == "intersects":
                disposition = "exact-area-render-member"
            elif exact_status == "outside":
                disposition = "out-of-area-informational"
            else:
                disposition = "member-location-unknown"
                terminal_reason = "member-location-unknown"
            record["render_disposition"] = disposition
            record["terminal_reason"] = terminal_reason


def _audit_record_component_count(records: list[dict]) -> int:
    by_way = {}
    for record in records:
        way_id = record.get("way_id")
        node_ids = record.get("source_node_ids")
        if (not isinstance(way_id, int) or not isinstance(node_ids, list)
                or len(node_ids) < 2):
            continue
        prior = by_way.get(way_id)
        current = set(node_ids)
        if prior is not None and prior != current:
            return 0
        by_way[way_id] = current
    if not by_way:
        return 0
    union = _UF(by_way)
    owner = {}
    for way_id, node_ids in by_way.items():
        for node_id in node_ids:
            if node_id in owner:
                union.union(way_id, owner[node_id])
            else:
                owner[node_id] = way_id
    return len({union.find(way_id) for way_id in by_way})


def _relation_outside_gap_evidence(audits: list[dict]) -> set[int]:
    """Roots whose exact source disconnection is closed only outside the area."""
    accepted = set()
    for audit in audits:
        if audit.get("assembly_status") != "emitted":
            continue
        members = audit.get("direct_way_members") or []
        included = [
            record for record in members
            if record.get("status") == "included"
            and record.get("effective_role") in {"", "main"}
        ]
        outside_connectors = [
            record for record in members
            if record.get("status") == "excluded"
            and record.get("effective_role") in {"", "main"}
            and record.get("exact_area_status") == "outside"
            and record.get("terminal_reason") is None
        ]
        if (outside_connectors
                and _audit_record_component_count(included) > 1
                and _audit_record_component_count(
                    [*included, *outside_connectors]) == 1):
            accepted.add(audit["relation_id"])
    return accepted


def curate_exact_area(features: list[dict[str, Any]], *, ways: dict[int, dict],
                       nodes: dict[int, tuple[float, float]], area_union,
                       area_name: str, region: str | None,
                       relation_member_audit: list[dict] | None = None,
                       relation_scope_evidence: dict | None = None,
                       promotion_decisions: list[dict] | None = None
                       ) -> tuple[list, list, dict]:
    """Disposition exact-area candidates and return (kept, removed, evidence).

    Outside Denmark this is a no-op. In Denmark, signed relations are never
    deleted for a source gap: unresolved routes remain in diagnostic output and
    make terminal validation fail closed as ``unresolved-relation-gap``.
    """
    normalized_region = (region or "").strip().lower()
    if normalized_region != "dk":
        for feature in features:
            feature.get("properties", {}).pop("_quality_source", None)
        return features, [], {
            "enabled": False,
            "region": normalized_region or None,
            "area": area_name,
            "validation_failures": [],
        }

    relation_audits = copy.deepcopy(relation_member_audit or [])
    _classify_relation_audits(
        relation_audits, ways, nodes, area_union, relation_scope_evidence)
    outside_gap_relation_ids = _relation_outside_gap_evidence(relation_audits)

    states = []
    validation_failures: list[str] = []
    unresolved_relations = []
    restored_road_relations = []
    aggregate_relation_way_miles: dict[tuple[int, int], float] = {}
    aggregate_restored_relation_ways: set[tuple[int, int]] = set()
    for index, feature in enumerate(features):
        properties = feature.get("properties") or {}
        source = properties.get("_quality_source") or {}
        relation_ids = list(source.get("relation_ids") or [])
        root_relation_ids = list(source.get("root_relation_ids") or [])
        direct = {
            int(relation_id): list(member_ids)
            for relation_id, member_ids in
            (source.get("direct_relation_way_ids") or {}).items()
        }
        restored_way_ids = list(source.get("restored_relation_way_ids") or [])
        geometry_way_ids = list(source.get("geometry_way_ids") or [])
        direct_way_ids = {
            way_id for member_ids in direct.values() for way_id in member_ids
        }
        invalid_restored = (
            len(restored_way_ids) != len(set(restored_way_ids))
            or not set(restored_way_ids).issubset(set(geometry_way_ids))
            or not set(restored_way_ids).issubset(direct_way_ids)
        )
        if invalid_restored:
            validation_failures.append(
                f"invalid restored-road identity: {properties.get('name')!r}")
            restored_way_ids = []
        records, missing = _source_way_records(source, ways, nodes, area_union)
        rendered_records = [record for record in records if record["renders_in_area"]]
        outside_gap_explained = bool(
            root_relation_ids
            and all(relation_id in outside_gap_relation_ids
                    for relation_id in root_relation_ids))
        connectivity = _connectivity(
            feature, records, missing, area_union,
            outside_gap_explained=outside_gap_explained)
        identity = walking_identity(rendered_records)

        properties.update({
            "quality_candidate_index": index,
            "relation_ids": relation_ids,
            "root_relation_ids": root_relation_ids,
            "direct_relation_way_ids": direct,
            "source_geometry_way_ids": geometry_way_ids,
            "restored_relation_way_ids": restored_way_ids,
            "source_ways": records,
            "rendered_source_way_ids": [
                record["way_id"] for record in rendered_records
            ],
            "connectivity": connectivity,
            "walking_identity": identity,
            "boundary_induced_split": (
                "boundary-induced-split" in connectivity["accepted_reasons"]
            ),
        })
        source_kind = properties.get("source")
        removed_category = None
        if properties["boundary_induced_split"]:
            # This is source-agnostic: both signed relations and name stitches
            # can become multipart only because the exact boundary cut them.
            properties["quality_disposition"] = "boundary-induced-split"

        if source_kind not in {"relation", "name-stitch"}:
            validation_failures.append(
                f"{properties.get('name')!r} has unknown assembly source {source_kind!r}")
        elif missing:
            properties["quality_disposition"] = "missing-source-way"
            validation_failures.append(
                f"missing-source-way unavailable evidence: "
                f"{properties.get('name')!r} {missing!r}")
            if source_kind == "relation":
                unresolved_relations.append({
                    "name": properties.get("name"),
                    "key": properties.get("ckey"),
                    "relation_ids": relation_ids,
                    "miles": properties.get("length_mi", 0),
                    "source_components": connectivity["source_components"],
                    "postclip_components": connectivity["postclip_components"],
                    "missing_way_ids": connectivity["missing_way_ids"],
                })
        elif source_kind == "name-stitch" and connectivity["status"] != "accepted":
            if connectivity["source_components"] != 1:
                removed_category = "disconnected-name-stitch"
            else:
                properties["quality_disposition"] = "invalid-source-geometry"
                validation_failures.append(
                    f"invalid-source-geometry: {properties.get('name')!r}")
        elif source_kind == "relation" and connectivity["status"] != "accepted":
            properties["quality_disposition"] = "unresolved-relation-gap"
            unresolved = {
                "name": properties.get("name"),
                "key": properties.get("ckey"),
                "relation_ids": relation_ids,
                "miles": properties.get("length_mi", 0),
                "source_components": connectivity["source_components"],
                "postclip_components": connectivity["postclip_components"],
                "missing_way_ids": connectivity["missing_way_ids"],
            }
            unresolved_relations.append(unresolved)
            validation_failures.append(
                f"unresolved-relation-gap: {properties.get('name')!r}")
        elif source_kind == "relation" and not relation_ids:
            validation_failures.append(
                f"{properties.get('name')!r} is relation-backed but has no relation id")

        if source_kind == "relation":
            unavailable_restored = sorted(
                set(restored_way_ids) - {record["way_id"] for record in records})
            if unavailable_restored:
                validation_failures.append(
                    f"missing-source-way unavailable restored-road evidence: "
                    f"{properties.get('name')!r} {unavailable_restored!r}")
            measurements = _restored_relation_measurements(
                feature, records, restored_way_ids, direct, area_union)
            for (measurement, restored_miles, relation_miles,
                 relation_way_miles, restored_relation_ways) in measurements:
                restored_road_relations.append(measurement)
                for relation_way, miles in relation_way_miles.items():
                    previous = aggregate_relation_way_miles.get(relation_way)
                    if previous is not None and abs(previous - miles) > 1e-12:
                        validation_failures.append(
                            "inconsistent restored-road relation-way evidence: "
                            f"{relation_way!r}")
                    aggregate_relation_way_miles.setdefault(relation_way, miles)
                aggregate_restored_relation_ways.update(restored_relation_ways)

        if source_kind == "name-stitch" and identity == "unknown" \
                and removed_category is None:
            validation_failures.append(
                f"{properties.get('name')!r} has no rendered source-way evidence")
        if source_kind == "name-stitch" and identity == "other" \
                and removed_category is None:
            properties["quality_disposition"] = "preserved-other-needs-review"
            validation_failures.append(
                f"preserved-other-needs-review: {properties.get('name')!r}")

        states.append({
            "index": index,
            "feature": feature,
            "identity": identity,
            "removed_category": removed_category,
        })

    # Preserve a complete relation-level ledger even when no trail feature was
    # emitted. Scope/area dispositions were attached before feature topology so
    # the same evidence can explain non-blocking out-of-area source gaps.
    emitted_relation_ids = {
        relation_id
        for state in states
        if state["feature"].get("properties", {}).get("source") == "relation"
        for relation_id in state["feature"].get("properties", {}).get(
            "relation_ids", [])
    }
    relation_member_unresolved = []
    for audit_index, audit in enumerate(relation_audits):
        relation_id = audit.get("relation_id")
        members = audit.get("direct_way_members")
        if not isinstance(members, list):
            members = []
            validation_failures.append(
                f"invalid relation-member audit: index {audit_index}")
        excluded = [record for record in members
                    if isinstance(record, dict)
                    and record.get("status") == "excluded"]
        terminal_members = [record for record in members
                            if isinstance(record, dict)
                            and record.get("terminal_reason")]
        missing_way_ids = list(dict.fromkeys(
            record.get("way_id") for record in members
            if isinstance(record, dict)
            and record.get("raw_status") != "present"
            and isinstance(record.get("way_id"), int)
        ))
        terminal_way_ids = list(dict.fromkeys(
            record.get("way_id") for record in terminal_members
            if isinstance(record.get("way_id"), int)
        ))
        missing_relation_ids = audit.get("missing_relation_ids") or []
        assembly_status = audit.get("assembly_status")
        unresolved_reasons = []
        relation_statuses = audit.get("relation_scope_statuses") or {}
        if assembly_status != "removed-thru-hike":
            for status in relation_statuses.values():
                terminal_reason = status.get("terminal_reason")
                if terminal_reason:
                    unresolved_reasons.append(terminal_reason)
            if not relation_statuses and missing_relation_ids:
                unresolved_reasons.append("missing-source-relation")
        unresolved_reasons.extend(
            record["terminal_reason"] for record in terminal_members)
        if assembly_status == "entirely-filtered" \
                and assembly_status != "removed-thru-hike":
            unresolved_reasons.append("entirely-filtered-route")
        elif assembly_status == "no-renderable-geometry":
            unresolved_reasons.append("no-renderable-route-geometry")
        elif assembly_status not in {"emitted", "removed-thru-hike"}:
            unresolved_reasons.append("invalid-assembly-status")
        unresolved_reasons = list(dict.fromkeys(unresolved_reasons))
        audit["emitted_in_exact_area"] = relation_id in emitted_relation_ids
        audit["review_status"] = (
            "unresolved" if unresolved_reasons else "accepted")
        audit["unresolved_reasons"] = unresolved_reasons
        if unresolved_reasons:
            unresolved = {
                "relation_id": relation_id,
                "name": audit.get("name"),
                "assembly_status": assembly_status,
                "reasons": unresolved_reasons,
                "excluded_way_ids": list(dict.fromkeys(
                    record.get("way_id") for record in excluded
                    if isinstance(record.get("way_id"), int)
                )),
                "terminal_way_ids": terminal_way_ids,
                "missing_way_ids": missing_way_ids,
                "missing_relation_ids": missing_relation_ids,
            }
            relation_member_unresolved.append(unresolved)
            validation_failures.append(
                "relation-member-audit unresolved: "
                f"relation {relation_id!r} {unresolved_reasons!r}")

    # Measure the complete postclip population before applying any quality
    # removal. Nested decisions use this frozen pair set; the final ledger is
    # annotated only after every candidate has a disposition.
    overlap_pairs = []
    for left_index, left in enumerate(states):
        for right in states[left_index + 1:]:
            ratio, left_share, right_share = overlap_ratio(
                left["feature"], right["feature"])
            if ratio + 1e-12 < OVERLAP_THRESHOLD:
                continue
            overlap_pairs.append((left, right, ratio, left_share, right_share))
            left_relation = left["feature"]["properties"].get("source") == "relation"
            right_relation = right["feature"]["properties"].get("source") == "relation"
            if left_relation == right_relation:
                continue
            rootless = right if left_relation else left
            rootless_share = right_share if left_relation else left_share
            if (rootless["removed_category"] is None
                    and rootless["feature"]["properties"].get(
                        "quality_disposition") not in {
                            "invalid-source-geometry", "missing-source-way"}
                    and rootless["identity"] == "weak-track"
                    and rootless_share + 1e-12 >= OVERLAP_THRESHOLD):
                rootless["removed_category"] = "nested-name-stitch-overlap"

    for state in states:
        properties = state["feature"]["properties"]
        if (state["removed_category"] is None
                and properties.get("quality_disposition") not in {
                    "invalid-source-geometry", "missing-source-way"}
                and properties.get("source") == "name-stitch"
                and state["identity"] == "weak-track"):
            state["removed_category"] = "dk-unqualified-road-track"

    overlap_evidence = []
    remaining_overlaps = []
    for left, right, ratio, left_share, right_share in overlap_pairs:
        left_properties = left["feature"]["properties"]
        right_properties = right["feature"]["properties"]
        removed_candidates = []
        if left["removed_category"]:
            removed_candidates.append(_removed_record(left, "left"))
        if right["removed_category"]:
            removed_candidates.append(_removed_record(right, "right"))
        record = {
            "candidate_indexes": [left["index"], right["index"]],
            "names": [left_properties.get("name"), right_properties.get("name")],
            "keys": [left_properties.get("ckey"), right_properties.get("ckey")],
            "sources": [left_properties.get("source"), right_properties.get("source")],
            "relation_ids": [left_properties.get("relation_ids", []),
                             right_properties.get("relation_ids", [])],
            "shorter_overlap_ratio": round(ratio, 6),
            "left_overlap_ratio": round(left_share, 6),
            "right_overlap_ratio": round(right_share, 6),
            "final_dispositions": [
                left["removed_category"] or left_properties.get(
                    "quality_disposition", "kept"),
                right["removed_category"] or right_properties.get(
                    "quality_disposition", "kept"),
            ],
            "disposition": _pair_disposition(left, right),
            "removed_candidates": removed_candidates,
        }
        overlap_evidence.append(record)
        if not removed_candidates:
            remaining_overlaps.append(record)
            if record["disposition"] == "preserved-other-needs-review":
                validation_failures.append(
                    f"preserved-other-needs-review overlap: {record['names']!r}")
            elif record["disposition"] == "needs-review-missing-source-evidence":
                validation_failures.append(
                    f"unhandled rootless overlap: {record['names']!r}")

    kept = []
    removed = []
    for state in states:
        feature = state["feature"]
        category = state["removed_category"]
        if category:
            removed.append(_mark_removed(feature, category))
        else:
            feature["properties"].pop("_quality_source", None)
            feature["properties"].setdefault("quality_disposition", "kept")
            kept.append(feature)

    counts = Counter(
        feature["properties"].get("removed_category") for feature in removed)
    unresolved_miles = round(sum(
        float(record["miles"]) for record in unresolved_relations
        if isinstance(record.get("miles"), (int, float))
    ), 3)
    aggregate_relation_miles = sum(aggregate_relation_way_miles.values())
    aggregate_restored_miles = sum(
        aggregate_relation_way_miles[relation_way]
        for relation_way in aggregate_restored_relation_ways
        if relation_way in aggregate_relation_way_miles
    )
    aggregate_restored_count = len(aggregate_restored_relation_ways)
    aggregate_share = (
        aggregate_restored_miles / aggregate_relation_miles
        if aggregate_relation_miles > 0 else 0.0
    )
    restored_road_aggregate = {
        "restored_way_count": aggregate_restored_count,
        "restored_miles": round(aggregate_restored_miles, 6),
        "total_relation_miles": round(aggregate_relation_miles, 6),
        "restored_share": round(aggregate_share, 6),
    }
    return kept, removed, {
        "schema_version": 7,
        "enabled": True,
        "region": "dk",
        "area": area_name,
        "connectivity_authority": "shared-osm-node-or-exact-boundary-clip",
        "overlap_threshold": OVERLAP_THRESHOLD,
        "candidate_count": len(features),
        "kept_count": len(kept),
        "removed_count": len(removed),
        "removed_counts": dict(sorted(counts.items())),
        "signed_relation_unresolved_count": len(unresolved_relations),
        "signed_relation_unresolved_miles": unresolved_miles,
        "signed_relation_unresolved": unresolved_relations,
        "relation_member_audit": relation_audits,
        "relation_member_unresolved_count": len(relation_member_unresolved),
        "relation_member_unresolved": relation_member_unresolved,
        "restored_road_policy": "diagnostic-only-member-safety-gated",
        "restored_road_relations": restored_road_relations,
        "restored_road_aggregate": restored_road_aggregate,
        "overlap_evidence": overlap_evidence,
        "remaining_overlaps": remaining_overlaps,
        "terminal_absorption_unresolved_count": 0,
        "terminal_absorption_unresolved": [],
        "promotion_decisions": copy.deepcopy(promotion_decisions or []),
        "validation_failures": list(dict.fromkeys(validation_failures)),
    }


def reconcile_terminal_absorptions(report: dict, kept: list[dict],
                                    removed: list[dict], area_union,
                                    unresolved: list[dict]) -> None:
    """Reconcile quality evidence after terminal relation absorption."""
    candidates = [*kept, *removed]
    counts = Counter(
        feature.get("properties", {}).get("removed_category")
        for feature in removed)
    counts.pop(None, None)
    report.update({
        "candidate_count": len(candidates),
        "kept_count": len(kept),
        "removed_count": len(removed),
        "removed_counts": dict(sorted(counts.items())),
        "terminal_absorption_unresolved_count": len(unresolved),
        "terminal_absorption_unresolved": unresolved,
    })

    emitted_relation_ids = {
        relation_id
        for feature in kept
        if feature.get("properties", {}).get("source") == "relation"
        for relation_id in feature.get("properties", {}).get(
            "relation_ids", [])
    }
    for audit in report.get("relation_member_audit") or []:
        if isinstance(audit, dict):
            audit["emitted_in_exact_area"] = (
                audit.get("relation_id") in emitted_relation_ids)

    restored_road_relations = []
    aggregate_relation_way_miles: dict[tuple[int, int], float] = {}
    aggregate_restored_relation_ways: set[tuple[int, int]] = set()
    for feature in kept:
        properties = feature.get("properties") or {}
        if properties.get("source") != "relation":
            continue
        measurements = _restored_relation_measurements(
            feature, properties.get("source_ways") or [],
            properties.get("restored_relation_way_ids") or [],
            {
                int(relation_id): list(member_ids)
                for relation_id, member_ids in
                (properties.get("direct_relation_way_ids") or {}).items()
            }, area_union)
        for (measurement, _restored_miles, _relation_miles,
             relation_way_miles, restored_relation_ways) in measurements:
            restored_road_relations.append(measurement)
            for relation_way, miles in relation_way_miles.items():
                aggregate_relation_way_miles.setdefault(relation_way, miles)
            aggregate_restored_relation_ways.update(restored_relation_ways)
    aggregate_relation_miles = sum(aggregate_relation_way_miles.values())
    aggregate_restored_miles = sum(
        aggregate_relation_way_miles[relation_way]
        for relation_way in aggregate_restored_relation_ways
        if relation_way in aggregate_relation_way_miles)
    aggregate_share = (
        aggregate_restored_miles / aggregate_relation_miles
        if aggregate_relation_miles > 0 else 0.0)
    report["restored_road_relations"] = restored_road_relations
    report["restored_road_aggregate"] = {
        "restored_way_count": len(aggregate_restored_relation_ways),
        "restored_miles": round(aggregate_restored_miles, 6),
        "total_relation_miles": round(aggregate_relation_miles, 6),
        "restored_share": round(aggregate_share, 6),
    }

    indexed = {
        feature.get("properties", {}).get("quality_candidate_index"): feature
        for feature in candidates
    }
    evidence = report.get("overlap_evidence") or []
    remaining = []
    for record in evidence:
        indexes = record.get("candidate_indexes") if isinstance(record, dict) else None
        if (not isinstance(indexes, list) or len(indexes) != 2
                or indexes[0] not in indexed or indexes[1] not in indexed):
            continue
        states = []
        for index in indexes:
            feature = indexed[index]
            properties = feature.get("properties") or {}
            states.append({
                "index": index,
                "feature": feature,
                "identity": properties.get("walking_identity"),
                "removed_category": properties.get("removed_category"),
            })
        left, right = states
        record["final_dispositions"] = [
            state["removed_category"]
            or state["feature"].get("properties", {}).get(
                "quality_disposition", "kept")
            for state in states
        ]
        record["removed_candidates"] = [
            _removed_record(state, side)
            for state, side in ((left, "left"), (right, "right"))
            if state["removed_category"]
        ]
        record["disposition"] = _pair_disposition(left, right)
        if not record["removed_candidates"]:
            remaining.append(record)
    report["remaining_overlaps"] = remaining
