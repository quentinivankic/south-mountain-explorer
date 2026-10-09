#!/usr/bin/env python3
"""Assemble trail objects from an .osm.pbf → GeoJSON (SPEC.md §2).

Thin pyosmium reader around `model.assemble`. Three passes:
  pass 1: index route relations and their complete direct way references.
  pass 2: index POIs plus every trail/highway/direct-relation way's refs + tags.
  pass 3: resolve node coordinates for the referenced nodes.

pyosmium's default handler doesn't expose way-node coordinates without a
location cache, so we read node locations with a NodeLocationsForWays-style
second pass (apply_file with locations=True) — memory bounded by the AOI /
hiking subset, never the planet.

Usage:
    python3 assemble.py --in data/aoi/sedona.osm.pbf --out data/aoi/sedona.trails.geojson
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import model  # noqa: E402


_RELATION_SOURCE_ARTIFACTS = {
    "raw-denmark": "data/source/mols-bjerge.raw.osm.pbf",
    "prefiltered-denmark": "data/source/mols-bjerge.prefilter.osm.pbf",
    "aoi": "data/aoi/mols-bjerge.osm.pbf",
}


def _poi_kind(tags, *, exact_denmark: bool = False) -> bool:
    values = set()
    for tag in tags:
        if hasattr(tag, "k") and hasattr(tag, "v"):
            key, value = tag.k, tag.v
        else:
            key, value = tag
        values.add((str(key), str(value)))
    signatures = (
        {signature for signature, _class in model.DK_DESTINATION_POI_CLASSES}
        if exact_denmark else model.DESTINATION_POIS)
    return any(signature in values for signature in signatures)


def read_pbf(path: str, *, exact_denmark: bool = False):
    import osmium

    nodes: dict[int, tuple[float, float]] = {}
    ways: dict[int, dict] = {}
    relations: dict[int, dict] = {}
    pois: list[dict] = []
    want_nodes: set[int] = set()
    direct_relation_way_ids: set[int] = set()

    class RelationPass(osmium.SimpleHandler):
        def relation(self, relation):
            tags = {tag.k: tag.v for tag in relation.tags}
            if model.member_safety.normalize(tags.get("type")) != "route":
                return
            members = [(member.type, member.ref, member.role)
                       for member in relation.members]
            relations[relation.id] = {"tags": tags, "members": members}
            direct_relation_way_ids.update(
                ref for member_type, ref, _role in members
                if member_type == "w")

    RelationPass().apply_file(path)

    class FeaturePass(osmium.SimpleHandler):
        def way(self, way):
            tags = {tag.k: tag.v for tag in way.tags}
            if not (model._is_trailish(tags) or "highway" in tags
                    or way.id in direct_relation_way_ids):
                return
            node_ids = [node.ref for node in way.nodes]
            if len(node_ids) < 2:
                return
            ways[way.id] = {"tags": tags, "nodes": node_ids}
            want_nodes.update(node_ids)

        def node(self, node):
            tags = {tag.k: tag.v for tag in node.tags}
            if tags and _poi_kind(
                    node.tags, exact_denmark=exact_denmark):
                pois.append({"id": node.id,
                             "coord": (node.location.lon, node.location.lat),
                             "tags": tags,
                             "name": tags.get("name")})

    FeaturePass().apply_file(path)

    # Final pass: resolve coordinates for the referenced way nodes.
    class NodePass(osmium.SimpleHandler):
        def node(self, node):
            if node.id in want_nodes:
                nodes[node.id] = (node.location.lon, node.location.lat)

    NodePass().apply_file(path)
    return nodes, ways, relations, pois


def _boundary_record(area: dict) -> dict:
    return {
        "name": area.get("name"),
        "osm_type": area.get("osm_type"),
        "osm_id": area.get("osm_id"),
    }


def _write_report(path: str | None, report: dict) -> None:
    if not path:
        return
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _pbf_identity(path: str) -> dict:
    source = Path(path)
    digest = hashlib.sha256()
    size = 0
    with source.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
    return {"sha256": digest.hexdigest(), "bytes": size}


def _geometry_sha256(geometry: dict) -> str:
    payload = json.dumps(
        geometry, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


_TERMINAL_CANDIDATE_ID = "_terminal_absorption_candidate_id"
_TERMINAL_REMOVAL_STAGE = "_terminal_absorption_removal_stage"
_TERMINAL_SUCCESSOR_IDS = "_terminal_absorption_successor_candidate_ids"


def _terminal_successor_candidate_ids(trail) -> list[int]:
    lineage = []
    seen = set()
    successor = getattr(trail, "_terminal_successor", None)
    while successor is not None and id(successor) not in seen:
        seen.add(id(successor))
        candidate_id = getattr(
            successor, "_terminal_absorption_candidate_id", None)
        if (isinstance(candidate_id, int)
                and not isinstance(candidate_id, bool)):
            lineage.append(candidate_id)
        successor = getattr(successor, "_terminal_successor", None)
    return lineage


def _mark_terminal_absorption_candidates(
        features: list[dict], trails: list, *,
        curation_removed_features: list[dict] | None = None,
        curation_removed_trails: list | None = None) -> dict[int, list[int]]:
    targets = model.terminal_relation_absorption_targets(trails)
    if len(features) != len(trails):
        raise ValueError("terminal feature/trail count mismatch")
    for feature, trail in zip(features, trails):
        candidate_id = getattr(
            trail, "_terminal_absorption_candidate_id", None)
        if not isinstance(candidate_id, int) or isinstance(candidate_id, bool):
            raise ValueError("terminal candidate lacks stable pre-curation id")
        properties = feature["properties"]
        properties[_TERMINAL_CANDIDATE_ID] = candidate_id
        properties[_TERMINAL_SUCCESSOR_IDS] = \
            _terminal_successor_candidate_ids(trail)
    removed_features = curation_removed_features or []
    removed_trails = curation_removed_trails or []
    if len(removed_features) != len(removed_trails):
        raise ValueError("terminal removed feature/trail count mismatch")
    for feature, trail in zip(removed_features, removed_trails):
        candidate_id = getattr(
            trail, "_terminal_absorption_candidate_id", None)
        if not isinstance(candidate_id, int) or isinstance(candidate_id, bool):
            continue
        properties = feature["properties"]
        properties[_TERMINAL_CANDIDATE_ID] = candidate_id
        properties[_TERMINAL_REMOVAL_STAGE] = "model-curation"
        properties[_TERMINAL_SUCCESSOR_IDS] = \
            _terminal_successor_candidate_ids(trail)
    return targets


def _promote_exact_denmark_postclip(
        features: list[dict], trails: list, pois: list[dict], *,
        authoritative_root_order: list[int]
        ) -> tuple[list[dict], list, list[dict]]:
    """Finalize exact-Denmark geometry, then decide promotion exactly once.

    Stable candidate IDs bind each clipped feature back to its model object.
    Same-name/area coalescing completes before POI ranking, so promotion
    evidence is measured against immutable final candidate linework. Later
    stages may disposition a candidate but never append geometry to it.
    """
    trails_by_id = {}
    preclip_lines = {}
    for trail in trails:
        candidate_id = getattr(
            trail, "_terminal_absorption_candidate_id", None)
        if not isinstance(candidate_id, int) or isinstance(candidate_id, bool):
            raise ValueError("postclip promotion candidate lacks stable id")
        if candidate_id in trails_by_id:
            raise ValueError("postclip promotion candidate id is not unique")
        trails_by_id[candidate_id] = trail
        preclip_lines[candidate_id] = [
            list(line) for line in getattr(trail, "lines", [])
        ]

    live = []
    feature_by_id = {}
    for feature in features:
        properties = feature.get("properties") or {}
        candidate_id = properties.get(_TERMINAL_CANDIDATE_ID)
        trail = trails_by_id.get(candidate_id)
        if trail is None or candidate_id in feature_by_id:
            raise ValueError("postclip promotion feature lacks model authority")
        live.append(trail)
        feature_by_id[candidate_id] = feature

    for trail in live:
        candidate_id = trail._terminal_absorption_candidate_id
        geometry = feature_by_id[candidate_id].get("geometry") or {}
        coordinates = geometry.get("coordinates")
        if geometry.get("type") != "MultiLineString" \
                or not isinstance(coordinates, list):
            raise ValueError("postclip promotion geometry is malformed")
        trail.lines = [
            [(float(point[0]), float(point[1])) for point in line]
            for line in coordinates
        ]

    # This is the final geometry/identity fusion boundary. Nothing after this
    # call may append linework to a promoted candidate.
    coalesced = model.coalesce_by_area(live)
    final_by_object = {id(trail): trail for trail in coalesced}
    grouped_ids = {
        trail._terminal_absorption_candidate_id: [] for trail in coalesced
    }
    for trail in live:
        source_id = trail._terminal_absorption_candidate_id
        final = trail
        seen = set()
        while id(final) not in final_by_object:
            if id(final) in seen or final._terminal_successor is None:
                raise ValueError("prepromotion coalescing successor is unresolved")
            seen.add(id(final))
            final = final._terminal_successor
        grouped_ids[final._terminal_absorption_candidate_id].append(source_id)

    promoted = model.promote_hikes(
        coalesced, pois, defer_relation_absorption=True, exact_denmark=True,
        authoritative_root_order=authoritative_root_order)
    promotion_decisions = []
    for candidate_index, trail in enumerate(promoted):
        if trail.source != "relation":
            continue
        decision = getattr(trail, "_promotion_decision", None)
        if not isinstance(decision, dict):
            raise ValueError("relation candidate lacks promotion decision")
        identity = {
            "candidate_index": candidate_index,
            "candidate_ckey": model._trail_ckey(trail),
            "root_relation_ids": list(dict.fromkeys(trail.root_relation_ids)),
        }
        identity_root_id = getattr(
            trail, "_identity_root_relation_id", None)
        if identity_root_id is not None:
            identity["identity_root_relation_id"] = identity_root_id
        promotion_decisions.append({**identity, **decision})

    out = []
    for trail in promoted:
        candidate_id = trail._terminal_absorption_candidate_id
        feature = feature_by_id[candidate_id]
        properties = feature["properties"]
        area = properties.get("area")
        rendered = trail.to_feature()
        properties.update(rendered["properties"])
        properties["area"] = area
        identity_root_id = getattr(
            trail, "_identity_root_relation_id", None)
        if identity_root_id is None:
            properties.pop("identity_root_relation_id", None)
        else:
            properties["identity_root_relation_id"] = identity_root_id
        quality_source = properties.get("_quality_source")
        if isinstance(quality_source, dict):
            quality_source["root_relation_ids"] = list(
                trail.root_relation_ids)
        properties[_TERMINAL_CANDIDATE_ID] = candidate_id
        properties[_TERMINAL_SUCCESSOR_IDS] = \
            _terminal_successor_candidate_ids(trail)
        if "destination_evidence" not in rendered["properties"]:
            properties.pop("destination_evidence", None)

        source_ids = grouped_ids[candidate_id]
        if any((feature_by_id[source_id].get("properties") or {}).get(
                "clipped") for source_id in source_ids):
            properties["clipped"] = True
            properties["full_length_mi"] = round(sum(
                model.line_mi(line)
                for source_id in source_ids
                for line in preclip_lines[source_id]
            ), 3)
        else:
            properties.pop("clipped", None)
            properties.pop("full_length_mi", None)
        feature["geometry"] = rendered["geometry"]
        out.append(feature)
    return out, promoted, promotion_decisions


def _terminal_linework_coverage(absorbed: dict, survivor: dict
                                 ) -> tuple[bool, bool]:
    from shapely.geometry import shape

    try:
        absorbed_geometry = shape(absorbed["geometry"])
        survivor_geometry = shape(survivor["geometry"])
        covered = absorbed_geometry.difference(survivor_geometry).is_empty
        equal = covered and survivor_geometry.difference(
            absorbed_geometry).is_empty
    except Exception:  # noqa: BLE001 - unprovable candidates remain published
        return False, False
    return covered, equal


def _terminal_candidate_record(candidate_id: int, feature: dict | None,
                               population: str) -> dict[str, Any]:
    properties = (feature or {}).get("properties") or {}
    geometry = (feature or {}).get("geometry")
    return {
        "candidate_id": candidate_id,
        "population": population,
        "ckey": properties.get("ckey"),
        "name": properties.get("name"),
        "source": properties.get("source"),
        "root_relation_ids": properties.get("root_relation_ids") or [],
        "geometry_sha256": (
            _geometry_sha256(geometry) if isinstance(geometry, dict) else None),
        "quality_disposition": properties.get("quality_disposition"),
        "removed_category": properties.get("removed_category"),
        "removed_reason": properties.get("removed_reason"),
        "removal_stage": properties.get(_TERMINAL_REMOVAL_STAGE),
        "successor_candidate_ids": properties.get(
            _TERMINAL_SUCCESSOR_IDS) or [],
    }


def _resolve_terminal_relation_absorptions(
        features: list[dict], targets: dict[int, list[int]], *,
        removed_targets: list[dict] | None = None
        ) -> tuple[list[dict], list[dict], list[dict]]:
    """Resolve deferred relation loss against the actual final population.

    Target chains collapse to one live survivor. Every unresolved live source
    is returned with a typed reason and complete source/target identity.
    """
    published_by_id: dict[int, list[dict]] = {}
    for feature in features:
        candidate_id = (feature.get("properties") or {}).get(
            _TERMINAL_CANDIDATE_ID)
        if isinstance(candidate_id, int) and not isinstance(candidate_id, bool):
            published_by_id.setdefault(candidate_id, []).append(feature)
    removed_by_id: dict[int, list[dict]] = {}
    for feature in removed_targets or []:
        candidate_id = (feature.get("properties") or {}).get(
            _TERMINAL_CANDIDATE_ID)
        if isinstance(candidate_id, int) and not isinstance(candidate_id, bool):
            removed_by_id.setdefault(candidate_id, []).append(feature)
    by_id = {candidate_id: values[0]
             for candidate_id, values in published_by_id.items()}
    blocked_dispositions = {
        "invalid-source-geometry", "missing-source-way",
        "unresolved-relation-gap",
    }
    blocked_published_ids = {
        candidate_id for candidate_id, feature in by_id.items()
        if (feature.get("properties") or {}).get("quality_disposition") in
        blocked_dispositions
    }
    edges = {
        candidate_id: list(dict.fromkeys(target_ids))
        for candidate_id, target_ids in targets.items()
        if candidate_id in by_id and candidate_id not in blocked_published_ids
        and (by_id[candidate_id].get("properties") or {}).get("source") ==
        "relation"
    }

    cycle_nodes: set[int] = set()
    state: dict[int, int] = {}
    stack: list[int] = []
    stack_positions: dict[int, int] = {}

    def find_cycles(candidate_id: int) -> None:
        state[candidate_id] = 1
        stack_positions[candidate_id] = len(stack)
        stack.append(candidate_id)
        for target_id in edges.get(candidate_id, []):
            if target_id not in edges or len(published_by_id.get(target_id, [])) != 1:
                continue
            if state.get(target_id, 0) == 0:
                find_cycles(target_id)
            elif state.get(target_id) == 1:
                cycle_nodes.update(stack[stack_positions[target_id]:])
        stack.pop()
        stack_positions.pop(candidate_id, None)
        state[candidate_id] = 2

    for candidate_id in edges:
        if state.get(candidate_id, 0) == 0:
            find_cycles(candidate_id)

    resolved: dict[int, int] = {}
    unresolved_reasons: dict[int, str] = {}

    def resolve(candidate_id: int) -> int | None:
        if candidate_id in resolved:
            return resolved[candidate_id]
        if candidate_id in unresolved_reasons:
            return None
        if len(published_by_id.get(candidate_id, [])) != 1:
            unresolved_reasons[candidate_id] = "ambiguous-survivor"
            return None
        if candidate_id in cycle_nodes:
            unresolved_reasons[candidate_id] = "cycle"
            return None
        terminal_targets = []
        for target_id in edges.get(candidate_id, []):
            if target_id == candidate_id:
                unresolved_reasons[candidate_id] = "cycle"
                return None
            if target_id in blocked_published_ids:
                unresolved_reasons[candidate_id] = "quality-removed-target"
                return None
            if (target_id in published_by_id and target_id in removed_by_id) \
                    or len(published_by_id.get(target_id, [])) > 1:
                unresolved_reasons[candidate_id] = "ambiguous-survivor"
                return None
            if target_id in removed_by_id:
                target_properties = [
                    feature.get("properties") or {}
                    for feature in removed_by_id[target_id]
                ]
                categories = {
                    properties.get("removed_category")
                    for properties in target_properties
                }
                stages = {
                    properties.get(_TERMINAL_REMOVAL_STAGE)
                    for properties in target_properties
                }
                unresolved_reasons[candidate_id] = (
                    "model-curation-removed-target"
                    if "model-curation" in stages
                    else "clipped-target" if categories & {
                        "min-inside-mi", "outside-exact-area"}
                    else "quality-removed-target")
                return None
            if target_id not in by_id:
                unresolved_reasons[candidate_id] = "missing-target"
                return None
            terminal_id = resolve(target_id) if target_id in edges else target_id
            if terminal_id is None:
                unresolved_reasons[candidate_id] = "ambiguous-survivor"
                return None
            terminal_targets.append(terminal_id)
        unique_targets = set(terminal_targets)
        if len(unique_targets) != 1:
            unresolved_reasons[candidate_id] = "divergent-targets"
            return None
        survivor_id = next(iter(unique_targets))
        covered, _equal = _terminal_linework_coverage(
            by_id[candidate_id], by_id[survivor_id])
        if not covered:
            unresolved_reasons[candidate_id] = "coverage-failure"
            return None
        resolved[candidate_id] = survivor_id
        return survivor_id

    for candidate_id in edges:
        resolve(candidate_id)

    def target_records(candidate_id: int) -> list[dict[str, Any]]:
        records = []
        for target_id in edges.get(candidate_id, []):
            published = published_by_id.get(target_id, [])
            removed = removed_by_id.get(target_id, [])
            records.extend(_terminal_candidate_record(
                target_id, feature,
                "quality-blocked" if target_id in blocked_published_ids
                else "published") for feature in published)
            records.extend(_terminal_candidate_record(
                target_id, feature, "removed") for feature in removed)
            if not published and not removed:
                records.append(_terminal_candidate_record(
                    target_id, None, "missing"))
        return records

    unresolved = [{
        "reason": unresolved_reasons[candidate_id],
        "source_candidate": _terminal_candidate_record(
            candidate_id, by_id.get(candidate_id), "published"),
        "target_candidates": target_records(candidate_id),
    } for candidate_id in sorted(unresolved_reasons) if candidate_id in edges]

    kept = []
    absorbed = []
    for feature in features:
        properties = feature.get("properties") or {}
        candidate_id = properties.get(_TERMINAL_CANDIDATE_ID)
        survivor_id = resolved.get(candidate_id, candidate_id)
        if survivor_id == candidate_id or survivor_id not in by_id:
            kept.append(feature)
            continue
        survivor = by_id[survivor_id]
        covered, equal = _terminal_linework_coverage(feature, survivor)
        if not covered:
            kept.append(feature)
            continue
        survivor_properties = survivor.get("properties") or {}
        properties.update({
            "quality_disposition": model.GEOMETRY_DUPLICATE_ABSORBED_CATEGORY,
            "removed_category": model.GEOMETRY_DUPLICATE_ABSORBED_CATEGORY,
            "removed_reason":
                model.TERMINAL_GEOMETRY_DUPLICATE_ABSORBED_REASON,
            "drop_evidence": {
                "kind": model.GEOMETRY_DUPLICATE_ABSORBED_CATEGORY,
                "reason": model.TERMINAL_ABSORPTION_EVIDENCE_REASON,
                "survivor": {
                    "ckey": survivor_properties.get("ckey"),
                    "name": survivor_properties.get("name"),
                    "source": survivor_properties.get("source"),
                    "geometry_sha256": _geometry_sha256(
                        survivor.get("geometry") or {}),
                },
                "geometry_proof": {
                    "method": "exact-area-linework-difference",
                    "absorbed_covered_by_survivor": True,
                    "survivor_covered_by_absorbed": equal,
                    "relationship": (
                        "equal" if equal else "covered-by-survivor"),
                },
            },
        })
        absorbed.append(feature)
    return kept, absorbed, unresolved


def _clear_terminal_absorption_metadata(*populations: list[dict]) -> None:
    for population in populations:
        for feature in population:
            properties = feature.get("properties") or {}
            properties.pop(_TERMINAL_CANDIDATE_ID, None)
            properties.pop(_TERMINAL_REMOVAL_STAGE, None)
            properties.pop(_TERMINAL_SUCCESSOR_IDS, None)


def _curation_snapshot(features: list[dict], removed: list[dict], *,
                       published_wins: bool = False) -> dict:
    """Return one disposition per key, preserving legacy precedence by default."""
    populations = ((removed, features) if published_wins
                   else (features, removed))
    snapshot = {}
    for population in populations:
        for feature in population:
            properties = feature.get("properties") or {}
            key = properties.get("ckey")
            if key:
                snapshot[key] = (
                    "kept" if population is features
                    else properties.get("removed_category") or "removed")
    return snapshot


def _load_relation_ledger(path: str, expected_scope: str) -> dict[str, Any]:
    """Load and minimally validate one exporter-owned authority ledger."""
    source_path = Path(path)
    try:
        document = json.loads(source_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot load {expected_scope} relation ledger: {error}") from error
    if not isinstance(document, dict) or document.get("schema_version") != 2:
        raise ValueError(f"{expected_scope} relation ledger schema_version must be 2")
    if document.get("scope") != expected_scope:
        raise ValueError(f"relation ledger scope must be {expected_scope!r}")
    if (document.get("attribution") != "© OpenStreetMap contributors"
            or document.get("accepted_route_values") != [
                "foot", "hiking", "running", "walking"]):
        raise ValueError(f"{expected_scope} relation ledger provenance is invalid")
    roots = document.get("root_relation_ids")
    if (not isinstance(roots, list) or not roots or roots != sorted(roots)
            or len(roots) != len(set(roots))
            or any(not isinstance(value, int) or isinstance(value, bool)
                   or value <= 0 for value in roots)):
        raise ValueError(f"{expected_scope} root_relation_ids are invalid")
    source = document.get("source")
    source_sha = source.get("sha256") if isinstance(source, dict) else None
    if (not isinstance(source, dict)
            or source.get("kind") != "runner-local-osm-pbf"
            or source.get("artifact") != _RELATION_SOURCE_ARTIFACTS[
                expected_scope]
            or not isinstance(source_sha, str)
            or len(source_sha) != 64
            or any(character not in "0123456789abcdef"
                   for character in source_sha)
            or not isinstance(source.get("bytes"), int)
            or isinstance(source.get("bytes"), bool)
            or source["bytes"] <= 0):
        raise ValueError(f"{expected_scope} source identity is invalid")
    values = document.get("relations")
    if not isinstance(values, list):
        raise ValueError(f"{expected_scope} relations must be a list")
    relations: dict[int, dict] = {}
    ways: dict[int, dict] = {}
    for relation in values:
        if not isinstance(relation, dict):
            raise ValueError(f"{expected_scope} relation record is invalid")
        relation_id = relation.get("relation_id")
        members = relation.get("members")
        tags = relation.get("tags")
        if (not isinstance(relation_id, int) or isinstance(relation_id, bool)
                or relation_id <= 0 or relation_id in relations
                or not isinstance(tags, dict)
                or not all(isinstance(key, str) and isinstance(value, str)
                           for key, value in tags.items())
                or not isinstance(members, list)):
            raise ValueError(f"{expected_scope} relation identity is invalid")
        derived_accepted = (
            model.member_safety.normalize(tags.get("type")) == "route"
            and model.member_safety.normalize(tags.get("route")) in
            model.HIKING_ROUTE_KINDS)
        if relation.get("accepted_hiking_route") is not derived_accepted:
            raise ValueError(
                f"{expected_scope} relation {relation_id} route flag is invalid")
        normalized_members = []
        for sequence, member in enumerate(members):
            if (not isinstance(member, dict)
                    or member.get("sequence") != sequence
                    or member.get("type") not in {"node", "way", "relation"}
                    or not isinstance(member.get("ref"), int)
                    or isinstance(member.get("ref"), bool)
                    or member["ref"] <= 0
                    or not isinstance(member.get("role"), str)):
                raise ValueError(
                    f"{expected_scope} relation {relation_id} member is invalid")
            normalized_members.append({
                "sequence": sequence,
                "type": member["type"],
                "ref": member["ref"],
                "role": member["role"],
            })
        expected_direct = list(dict.fromkeys(
            member["ref"] for member in normalized_members
            if member["type"] == "way"))
        direct_values = relation.get("direct_ways")
        if (relation.get("direct_way_ids") != expected_direct
                or not isinstance(direct_values, list)):
            raise ValueError(
                f"{expected_scope} relation {relation_id} direct ways are invalid")
        direct_by_id = {}
        for direct in direct_values:
            if not isinstance(direct, dict):
                raise ValueError(f"{expected_scope} direct way record is invalid")
            way_id = direct.get("way_id")
            status = direct.get("status")
            if (not isinstance(way_id, int) or isinstance(way_id, bool)
                    or way_id <= 0 or way_id in direct_by_id
                    or status not in {"present", "incomplete", "missing"}):
                raise ValueError(f"{expected_scope} direct way identity is invalid")
            if status == "missing":
                if any(direct.get(key) is not None for key in (
                        "tags", "node_ids", "coordinates", "missing_node_ids")):
                    raise ValueError(
                        f"{expected_scope} missing way w{way_id} carries source data")
            else:
                node_ids = direct.get("node_ids")
                coordinates = direct.get("coordinates")
                missing_nodes = direct.get("missing_node_ids")
                direct_tags = direct.get("tags")
                if (not isinstance(direct_tags, dict)
                        or not all(isinstance(key, str) and isinstance(value, str)
                                   for key, value in direct_tags.items())
                        or not isinstance(node_ids, list)
                        or any(not isinstance(node_id, int)
                               or isinstance(node_id, bool) or node_id <= 0
                               for node_id in node_ids)
                        or not isinstance(coordinates, list)
                        or len(coordinates) != len(node_ids)
                        or not isinstance(missing_nodes, list)
                        or any(not isinstance(node_id, int)
                               or isinstance(node_id, bool) or node_id <= 0
                               for node_id in missing_nodes)
                        or any(
                            point is not None and (
                                not isinstance(point, list) or len(point) < 2
                                or not all(isinstance(value, (int, float))
                                           and not isinstance(value, bool)
                                           and math.isfinite(float(value))
                                           for value in point[:2]))
                            for point in coordinates)):
                    raise ValueError(
                        f"{expected_scope} way w{way_id} geometry evidence is invalid")
                expected_missing_nodes = [
                    node_id for node_id, point in zip(node_ids, coordinates)
                    if point is None
                ]
                if missing_nodes != expected_missing_nodes:
                    raise ValueError(
                        f"{expected_scope} way w{way_id} missing nodes disagree")
                if status == "present" and (
                        len(node_ids) < 2 or missing_nodes
                        or any(point is None for point in coordinates)):
                    raise ValueError(
                        f"{expected_scope} way w{way_id} present status is invalid")
            direct_by_id[way_id] = direct
            prior = ways.get(way_id)
            if prior is not None and prior != direct:
                raise ValueError(
                    f"{expected_scope} way w{way_id} evidence is inconsistent")
            ways[way_id] = direct
        if list(direct_by_id) != expected_direct:
            raise ValueError(
                f"{expected_scope} relation {relation_id} direct way order is invalid")
        relations[relation_id] = {
            "relation_id": relation_id,
            "tags": tags,
            "members": normalized_members,
            "direct_way_ids": expected_direct,
            "direct_ways": direct_by_id,
        }
    for root_id in roots:
        relation = relations.get(root_id)
        if relation is not None and not (
                model.member_safety.normalize(relation["tags"].get("type")) ==
                "route"
                and model.member_safety.normalize(
                    relation["tags"].get("route")) in model.HIKING_ROUTE_KINDS):
            raise ValueError(
                f"{expected_scope} selected root r{root_id} is not a hiking route")
    if document.get("relation_count") != len(relations):
        raise ValueError(f"{expected_scope} relation_count is inconsistent")
    expected_missing_relations = sorted({
        member["ref"]
        for relation in relations.values()
        for member in relation["members"]
        if member["type"] == "relation" and member["ref"] not in relations
    })
    expected_missing_ways = sorted(
        way_id for way_id, way in ways.items()
        if way["status"] == "missing")
    expected_incomplete_ways = sorted(
        way_id for way_id, way in ways.items()
        if way["status"] == "incomplete")
    if (document.get("missing_relation_ids") != expected_missing_relations
            or document.get("missing_direct_way_ids") != expected_missing_ways
            or document.get("incomplete_direct_way_ids") !=
            expected_incomplete_ways):
        raise ValueError(f"{expected_scope} missing-object ledgers are inconsistent")
    return {
        "document": document,
        "scope": expected_scope,
        "source": source,
        "root_relation_ids": roots,
        "relations": relations,
        "ways": ways,
    }


def _same_way_transform(raw: dict, witness: dict) -> bool:
    """Compare source identity while allowing an incomplete AOI node cache."""
    if raw["status"] == "missing" or witness["status"] == "missing":
        return raw["status"] == witness["status"]
    if raw.get("tags") != witness.get("tags") \
            or raw.get("node_ids") != witness.get("node_ids"):
        return False
    raw_coordinates = raw.get("coordinates") or []
    witness_coordinates = witness.get("coordinates") or []
    return all(witness_point is None or witness_point == raw_point
               for raw_point, witness_point in zip(
                   raw_coordinates, witness_coordinates))


def _load_relation_scopes(raw_path: str, prefilter_path: str, aoi_path: str,
                          input_pbf: dict[str, Any]) -> dict[str, Any]:
    scopes = {
        "raw-denmark": _load_relation_ledger(raw_path, "raw-denmark"),
        "prefiltered-denmark": _load_relation_ledger(
            prefilter_path, "prefiltered-denmark"),
        "aoi": _load_relation_ledger(aoi_path, "aoi"),
    }
    roots = scopes["raw-denmark"]["root_relation_ids"]
    if any(scope["root_relation_ids"] != roots for scope in scopes.values()):
        raise ValueError("relation authority ledgers select different AOI roots")
    if scopes["aoi"]["source"] != {
            "kind": "runner-local-osm-pbf",
            "artifact": _RELATION_SOURCE_ARTIFACTS["aoi"],
            **input_pbf}:
        raise ValueError("AOI relation ledger source disagrees with assembly PBF")

    raw = scopes["raw-denmark"]
    missing_raw_roots = sorted(
        set(roots) - set(raw["relations"]))
    if missing_raw_roots:
        raise ValueError(
            f"raw Denmark is missing AOI-selected roots {missing_raw_roots!r}")
    for witness_name in ("prefiltered-denmark", "aoi"):
        witness = scopes[witness_name]
        for relation_id, relation in witness["relations"].items():
            raw_relation = raw["relations"].get(relation_id)
            if raw_relation is None:
                raise ValueError(
                    f"{witness_name} relation r{relation_id} is absent from raw Denmark")
            if (relation["tags"] != raw_relation["tags"]
                    or relation["members"] != raw_relation["members"]):
                raise ValueError(
                    f"{witness_name} relation r{relation_id} contradicts raw Denmark")
        for way_id, way in witness["ways"].items():
            raw_way = raw["ways"].get(way_id)
            if raw_way is None or (way["status"] != "missing"
                                   and raw_way["status"] == "missing"):
                raise ValueError(
                    f"{witness_name} way w{way_id} is absent from raw Denmark")
            if (way["status"] != "missing" and raw_way["status"] != "missing"
                    and not _same_way_transform(raw_way, way)):
                raise ValueError(
                    f"{witness_name} way w{way_id} contradicts raw Denmark")
    return scopes


def _augment_with_raw_authority(nodes: dict, ways: dict, relations: dict,
                                scopes: dict[str, Any]) -> None:
    """Supply target hierarchy geometry from raw authority without invention."""
    raw = scopes["raw-denmark"]
    for node_way in raw["ways"].values():
        if node_way["status"] != "present":
            continue
        node_ids = node_way["node_ids"]
        coordinates = node_way["coordinates"]
        for node_id, point in zip(node_ids, coordinates):
            coordinate = (float(point[0]), float(point[1]))
            prior = nodes.get(node_id)
            if prior is not None and prior != coordinate:
                raise ValueError(
                    f"raw Denmark node n{node_id} contradicts AOI coordinates")
            nodes[node_id] = coordinate
        way_id = node_way["way_id"]
        raw_way = {"tags": dict(node_way["tags"]), "nodes": list(node_ids)}
        prior_way = ways.get(way_id)
        if prior_way is not None and prior_way != raw_way:
            raise ValueError(f"raw Denmark way w{way_id} contradicts AOI PBF")
        ways[way_id] = raw_way
    for relation_id, relation in raw["relations"].items():
        raw_relation = {
            "tags": dict(relation["tags"]),
            "members": [
                ({"node": "n", "way": "w", "relation": "r"}[member["type"]],
                 member["ref"], member["role"])
                for member in relation["members"]
            ],
        }
        prior = relations.get(relation_id)
        if prior is not None and prior != raw_relation:
            raise ValueError(
                f"raw Denmark relation r{relation_id} contradicts AOI PBF")
        relations[relation_id] = raw_relation


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Assemble trail objects from OSM PBF")
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--only-area", dest="only_area",
                    help="keep only trails inside area(s) whose name contains this "
                         "(case-insensitive; unions all matches). Boundaries are "
                         "assembled from the same --in PBF.")
    ap.add_argument("--require-exact-area", action="store_true",
                    help="require exactly one case-sensitive --only-area boundary "
                         "from the expected relation; never fall back to unclipped")
    ap.add_argument("--expected-area-relation-id", type=int,
                    help="relation id required by --require-exact-area")
    ap.add_argument("--report-json",
                    help="write versioned machine-readable assembly evidence")
    ap.add_argument(
        "--relation-authority",
        help="targeted raw-Denmark relation authority ledger")
    ap.add_argument(
        "--prefilter-authority",
        help="targeted full-prefilter transformation witness ledger")
    ap.add_argument(
        "--aoi-relation-members",
        help="targeted AOI render-scope relation ledger")
    ap.add_argument("--min-length-mi", dest="min_length_mi", type=float, default=0.0,
                    help="drop assembled trails shorter than this (miles); 0 = keep all")
    ap.add_argument("--min-inside-mi", dest="min_inside_mi", type=float, default=0.05,
                    help="with --only-area, drop a clipped trail whose in-park "
                         "remnant is shorter than this (miles); guards against "
                         "boundary slivers (default 0.05)")
    ap.add_argument("--per-area-merge", dest="per_area_merge", action="store_true",
                    help="scope same-name merge to within each park boundary. For "
                         "unclipped region/state runs, so same-named trails in "
                         "different parks don't fuse into one scattered object.")
    ap.add_argument("--region",
                    help="2-letter state code (e.g. vt, nm) — enables region-scoped "
                         "thru-hike drops whose bare name collides with unrelated "
                         "local trails elsewhere (Vermont's 'Long Trail', NM's "
                         "'Skyline Trail'). Match what the publisher ships.")
    args = ap.parse_args(argv)

    authority_paths = (
        args.relation_authority,
        args.prefilter_authority,
        args.aoi_relation_members,
    )
    if any(authority_paths) and not all(authority_paths):
        ap.error("relation authority requires raw, prefilter, and AOI ledgers")
    if all(authority_paths) and (
            not args.require_exact_area
            or model.member_safety.normalize(args.region) != "dk"):
        ap.error("three-scope relation authority is limited to exact Denmark assembly")

    if args.require_exact_area:
        if not args.only_area:
            ap.error("--require-exact-area requires --only-area")
        if args.expected_area_relation_id is None:
            ap.error("--require-exact-area requires --expected-area-relation-id")
        if args.expected_area_relation_id <= 0:
            ap.error("--expected-area-relation-id must be positive")
        if not args.report_json:
            ap.error("--require-exact-area requires --report-json")
    elif args.expected_area_relation_id is not None:
        ap.error("--expected-area-relation-id requires --require-exact-area")

    input_pbf = _pbf_identity(args.inp) if args.require_exact_area else None
    exact_denmark = (
        args.require_exact_area
        and model.member_safety.normalize(args.region) == "dk")
    nodes, ways, relations, pois = read_pbf(
        args.inp, exact_denmark=exact_denmark)
    # Raw authority may add out-of-AOI relation members below. Preserve the
    # rendered AOI way universe before augmentation so no standalone trail,
    # removal, or spur can be invented from country-scope evidence.
    aoi_way_ids = frozenset(ways)
    coverage = model.coverage_stats(ways, relations, pois)
    relation_scopes = None
    if all(authority_paths):
        try:
            relation_scopes = _load_relation_scopes(
                args.relation_authority, args.prefilter_authority,
                args.aoi_relation_members, input_pbf)
            _augment_with_raw_authority(nodes, ways, relations, relation_scopes)
        except ValueError as error:
            _write_report(args.report_json, {
                "schema_version": 1,
                "status": "failed",
                "failure": f"relation authority validation failed: {error}",
                "exact_area_required": True,
                "input_pbf": input_pbf,
                "clip_applied": False,
            })
            print(f"ERROR: relation authority validation failed: {error}",
                  file=sys.stderr)
            return 2
    print(f"read: {len(ways):,} ways, {len(relations):,} route relations, "
          f"{len(pois):,} POIs, {len(nodes):,} nodes", file=sys.stderr)

    area_objs = None
    exact_area = None
    boundary_matches: list[dict] = []
    if args.require_exact_area:
        import areas as areamod
        area_objs = areamod.assemble_areas(args.inp)
        exact_matches = areamod.select_exact(area_objs, args.only_area)
        boundary_matches = [_boundary_record(area) for area in exact_matches]
        failure = None
        if len(exact_matches) != 1:
            failure = (f"expected exactly one boundary named {args.only_area!r}; "
                       f"found {len(exact_matches)}")
        elif exact_matches[0].get("osm_type") != "relation":
            failure = (f"boundary {args.only_area!r} came from "
                       f"{exact_matches[0].get('osm_type')!r}, not a relation")
        elif int(exact_matches[0].get("osm_id") or 0) != args.expected_area_relation_id:
            failure = (f"boundary relation id {exact_matches[0].get('osm_id')!r} "
                       f"does not match expected {args.expected_area_relation_id}")
        if failure:
            _write_report(args.report_json, {
                "schema_version": 1,
                "status": "failed",
                "failure": failure,
                "exact_area_required": True,
                "area_query": args.only_area,
                "expected_area_relation_id": args.expected_area_relation_id,
                "input_pbf": input_pbf,
                "boundary_match_count": len(exact_matches),
                "boundary_matches": boundary_matches,
                "boundary": None,
                "clip_applied": False,
                "pre_clip_trail_count": None,
                "post_clip_trail_count": None,
                "assembled_trail_count": None,
                "coverage": coverage,
            })
            print(f"ERROR: exact-area validation failed: {failure}", file=sys.stderr)
            return 2
        exact_area = exact_matches[0]

    areas_arg = None
    if args.per_area_merge:
        import areas as areamod
        areas_arg = areamod.merge_areas(args.inp)
        print(f"per-area merge: scoping same-name merge to {len(areas_arg):,} "
              f"park areas", file=sys.stderr)

    removed: list = []
    ingest_dropped: list = []
    relation_member_audit: list[dict] = []
    trails = model.assemble(nodes, ways, relations, pois,
                            min_length_mi=args.min_length_mi, areas=areas_arg,
                            collect_removed=removed, region=args.region,
                            collect_ingest_dropped=ingest_dropped,
                            collect_relation_member_audit=relation_member_audit,
                            relation_scope_evidence=relation_scopes,
                            standalone_way_ids=aoi_way_ids,
                            defer_relation_absorption=exact_denmark,
                            exact_denmark=exact_denmark,
                            defer_promotion=exact_denmark)
    features = [t.to_feature() for t in trails]
    curation_removed_features = [t.to_feature() for t in removed]
    terminal_absorption_targets = (
        _mark_terminal_absorption_candidates(
            features, trails,
            curation_removed_features=curation_removed_features,
            curation_removed_trails=removed)
        if exact_denmark else {})
    clip_removed: list[dict] = []
    pre_clip_count = len(features)
    clip_applied = False
    quality_report = None
    quality_removed: list[dict] = []
    promotion_decisions: list[dict] = []
    terminal_absorption_unresolved: list[dict] = []
    qualitymod = None
    if exact_denmark:
        import quality as qualitymod
        qualitymod.prepare_feature_sources(features, trails, ways)
        qualitymod.prepare_feature_sources(
            curation_removed_features, removed, ways)

    area_note = ""
    if args.only_area:
        import areas as areamod
        if exact_area is not None:
            union = exact_area["geom"]
            names = {exact_area["name"]}
            selected_area_name = exact_area["name"]
        else:
            if area_objs is None:
                area_objs = areamod.assemble_areas(args.inp)
            union, names = areamod.union_matching(area_objs, args.only_area)
            selected_area_name = None
        if union is None:
            print(f"WARNING: no area matched '{args.only_area}' in {args.inp} "
                  f"— leaving trails unclipped", file=sys.stderr)
        else:
            before = len(features)
            features = areamod.clip_features_to_area(
                features, union, min_inside_mi=args.min_inside_mi,
                area_name=selected_area_name,
                collect_dropped=(clip_removed if qualitymod is not None else None))
            clip_applied = True
            if qualitymod is not None:
                features, trails, promotion_decisions = \
                    _promote_exact_denmark_postclip(
                        features, trails, pois,
                        authoritative_root_order=(
                            relation_scopes["raw-denmark"][
                                "root_relation_ids"]
                            if relation_scopes is not None else []))
                qualitymod.prepare_feature_sources(features, trails, ways)
                # Promotion can add exact-geometry absorption edges. Rebuild
                # the graph before quality and terminal survivor resolution.
                terminal_absorption_targets = \
                    model.terminal_relation_absorption_targets(trails)
                qualitymod.finalize_dropped_feature_sources(
                    curation_removed_features, ways=ways, nodes=nodes,
                    area_union=union, area_name=selected_area_name,
                    min_length_mi=args.min_length_mi)
                qualitymod.finalize_dropped_feature_sources(
                    clip_removed, ways=ways, nodes=nodes,
                    area_union=union, area_name=selected_area_name,
                    min_length_mi=args.min_length_mi)
                features, quality_removed, quality_report = qualitymod.curate_exact_area(
                    features, ways=ways, nodes=nodes, area_union=union,
                    area_name=selected_area_name, region=args.region,
                    relation_member_audit=relation_member_audit,
                    relation_scope_evidence=relation_scopes,
                    promotion_decisions=promotion_decisions)
                for removed_feature in clip_removed:
                    (removed_feature.get("properties") or {}).setdefault(
                        _TERMINAL_REMOVAL_STAGE, "exact-area-clip")
                for removed_feature in quality_removed:
                    (removed_feature.get("properties") or {}).setdefault(
                        _TERMINAL_REMOVAL_STAGE, "denmark-quality")
                features, terminal_absorbed, \
                    terminal_absorption_unresolved = \
                    _resolve_terminal_relation_absorptions(
                        features, terminal_absorption_targets,
                        removed_targets=[
                            *curation_removed_features,
                            *clip_removed,
                            *quality_removed,
                        ])
                if terminal_absorbed:
                    quality_removed.extend(terminal_absorbed)
                qualitymod.reconcile_terminal_absorptions(
                    quality_report, features, quality_removed, union,
                    terminal_absorption_unresolved)
            clipped = sum(1 for f in features if f["properties"].get("clipped"))
            matched = ", ".join(sorted(n for n in names if n)) or "(unnamed)"
            area_note = (f"; clipped to '{args.only_area}' [{matched}]: "
                         f"{before} -> {len(features)} ({clipped} trimmed at boundary)")

    if exact_denmark:
        _clear_terminal_absorption_metadata(
            features, curation_removed_features, clip_removed, quality_removed)

    terminal_absorption_failure = None
    if exact_denmark and terminal_absorption_unresolved:
        terminal_absorption_failure = (
            "unresolved terminal relation absorptions: "
            f"{len(terminal_absorption_unresolved)}")
        if isinstance(quality_report, dict):
            quality_report.setdefault("validation_failures", []).append(
                terminal_absorption_failure)
        print(f"ERROR: {terminal_absorption_failure}", file=sys.stderr)

    # Retained-route ingest truth depends on the final diagnostic-published
    # population, after exact clipping and Denmark quality disposition.
    model.reconcile_ingest_dropped(ingest_dropped, features, args.region)

    fc = {"type": "FeatureCollection", "features": features,
          "coverage": coverage}
    Path(args.out).write_text(json.dumps(fc), encoding="utf-8")

    # Sidecar base: strip '.trails.geojson' (or '.geojson') down to the stem so
    # the sidecars land next to the trails output (vermont.trails.geojson ->
    # vermont.removed.geojson / vermont.areas.geojson).
    base = Path(args.out).with_suffix("")          # strip .geojson
    if base.suffix == ".trails":
        base = base.with_suffix("")

    # Sidecar 1: the trails curation dropped, each carrying a plain-language
    # `removed_reason`, so the QA viewer can show/hide them and explain WHY.
    # Kept separate from the trails output so publish + the app never see them.
    removed_path = base.with_name(base.name + ".removed.geojson")
    removed_fc = {
        "type": "FeatureCollection",
        "features": [
            *curation_removed_features, *clip_removed, *quality_removed,
        ],
    }
    removed_path.write_text(json.dumps(removed_fc), encoding="utf-8")

    # Sidecar 1a: NAMED ways a TAG gate filtered out before assembly (foot=no,
    # ski piste, road-like track, motor vehicle). Each carries a category +
    # reason so the viewer's 'ingest-filtered' layer makes a named trail
    # wrongly eaten by a tag rule visible instead of silently gone.
    ingest_path = base.with_name(base.name + ".ingest-dropped.geojson")
    ingest_path.write_text(
        json.dumps({"type": "FeatureCollection", "features": ingest_dropped}),
        encoding="utf-8")
    print(f"ingest-filtered (named, tag rules): {len(ingest_dropped)} -> {ingest_path}",
          file=sys.stderr)

    # Sidecar 1b: curation snapshot + run-to-run DIFF. Maps each trail's stable
    # `ckey` (its sorted member OSM ways — unchanged when only a rule is tuned)
    # to its verdict this run ('kept' or the removal category). If a prior
    # snapshot sits beside it (an earlier assemble of this AOI in this session),
    # emit a diff so the viewer can show ONLY what moved — newly removed, newly
    # kept, or shifted between removal reasons — instead of re-reviewing the
    # whole area. (data/ is scratch: between sessions the first run has no
    # baseline and is a full review, which is correct.)
    snap_path = base.with_name(base.name + ".curation.json")
    diff_path = base.with_name(base.name + ".curation-diff.json")
    new_snap = _curation_snapshot(
        features, removed_fc["features"], published_wins=exact_denmark)
    has_baseline = snap_path.exists()
    diff = {"has_baseline": has_baseline, "new_removed": [], "new_kept": [],
            "reason_changed": []}
    if has_baseline:
        try:
            old_snap = json.loads(snap_path.read_text())
        except Exception:
            old_snap = {}
        name_sources = (
            removed_fc["features"] + features if exact_denmark
            else features + removed_fc["features"])
        name_by_key = {f["properties"].get("ckey"): f["properties"].get("name")
                       for f in name_sources}
        for k, cat in new_snap.items():
            old = old_snap.get(k)
            if old is None or old == cat:
                continue                       # brand-new way, or unchanged
            entry = {"ckey": k, "name": name_by_key.get(k)}
            if old == "kept":
                diff["new_removed"].append({**entry, "category": cat})
            elif cat == "kept":
                diff["new_kept"].append({**entry, "was": old})
            else:
                diff["reason_changed"].append({**entry, "from": old, "to": cat})
    diff_path.write_text(json.dumps(diff), encoding="utf-8")
    snap_path.write_text(json.dumps(new_snap), encoding="utf-8")
    if has_baseline:
        print(f"curation diff vs last run: {len(diff['new_removed'])} newly removed, "
              f"{len(diff['new_kept'])} newly kept, "
              f"{len(diff['reason_changed'])} reason-changed -> {diff_path}",
              file=sys.stderr)
    else:
        print(f"curation snapshot written (no baseline — full review) -> {snap_path}",
              file=sys.stderr)

    # Sidecar 2: the park-area polygons the merge/clip scope to. The viewer
    # draws them (green fills) AND its "Only trails in an area" + clip toggles
    # test trail vertices against them — WITHOUT this file every trail reads as
    # "not in an area" and the toggle hides them all. Assembled from the same
    # AOI PBF (already the source for --only-area / --per-area-merge).
    areas_path = base.with_name(base.name + ".areas.geojson")
    try:
        import areas as areamod
        from shapely.geometry import mapping as shp_mapping
        if area_objs is None:
            area_objs = areamod.assemble_areas(args.inp)
        def area_properties(area):
            properties = {"name": area.get("name")}
            if args.require_exact_area:
                properties.update({
                    "osm_type": area.get("osm_type"),
                    "osm_id": area.get("osm_id"),
                })
            return properties

        area_feats = [{"type": "Feature",
                       "properties": area_properties(a),
                       "geometry": shp_mapping(a["geom"])}
                      for a in area_objs if a.get("geom") is not None]
    except Exception as e:
        area_feats = []
        print(f"note: could not export park areas ({e})", file=sys.stderr)
    areas_path.write_text(
        json.dumps({"type": "FeatureCollection", "features": area_feats}),
        encoding="utf-8")
    print(f"exported {len(area_feats):,} park areas -> {areas_path}", file=sys.stderr)

    # The exact pilot boundary is a one-feature evidence artifact. It preserves
    # Polygon/MultiPolygon structure and interior rings verbatim so the dry-run
    # publisher cannot independently union look-alike neighbouring areas.
    exact_area_path = base.with_name(base.name + ".exact-area.geojson")
    exact_area_geometry = None
    if exact_area is not None:
        from shapely.geometry import mapping as shp_mapping
        exact_area_geometry = shp_mapping(exact_area["geom"])
        exact_area_feature = {
            "type": "Feature",
            "properties": {
                **_boundary_record(exact_area),
                "geometry_sha256": _geometry_sha256(exact_area_geometry),
            },
            "geometry": exact_area_geometry,
        }
        exact_area_path.write_text(json.dumps({
            "type": "FeatureCollection", "features": [exact_area_feature],
        }), encoding="utf-8")
        print(f"exported sealed exact area -> {exact_area_path}", file=sys.stderr)

    report_boundary = (_boundary_record(exact_area) if exact_area is not None else None)
    _write_report(args.report_json, {
        "schema_version": 1,
        "status": "failed" if terminal_absorption_failure else "ok",
        "failure": terminal_absorption_failure,
        "exact_area_required": bool(args.require_exact_area),
        "area_query": args.only_area,
        "expected_area_relation_id": args.expected_area_relation_id,
        "input_pbf": input_pbf,
        "relation_authority": ({
            "mode": "raw-primary-prefilter-witness-aoi-render-scope",
            "root_relation_ids": relation_scopes["raw-denmark"][
                "root_relation_ids"],
            "scopes": {
                scope: evidence["source"]
                for scope, evidence in relation_scopes.items()
            },
        } if relation_scopes is not None else None),
        "boundary_match_count": len(boundary_matches),
        "boundary_matches": boundary_matches,
        "boundary": report_boundary,
        "boundary_geometry_sha256": (
            _geometry_sha256(exact_area_geometry)
            if exact_area_geometry is not None else None),
        "clip_applied": clip_applied,
        "min_length_mi": args.min_length_mi,
        "min_inside_mi": args.min_inside_mi,
        "pre_clip_trail_count": pre_clip_count,
        "post_clip_trail_count": len(features),
        "assembled_trail_count": len(features),
        "coverage": coverage,
        "quality": quality_report,
    })
    welded = sum(1 for f in features if f["properties"].get("welds"))
    from_rel = sum(1 for f in features if f["properties"].get("source") == "relation")
    print(f"assembled {len(features):,} trails "
          f"({from_rel:,} from relations, {welded:,} with welded spurs){area_note} -> {args.out}",
          file=sys.stderr)
    print(
        f"removed {len(curation_removed_features) + len(clip_removed) + len(quality_removed):,} "
        f"trails (curation/exact clip) -> {removed_path}",
        file=sys.stderr)
    return 2 if terminal_absorption_failure else 0


if __name__ == "__main__":
    raise SystemExit(main())
