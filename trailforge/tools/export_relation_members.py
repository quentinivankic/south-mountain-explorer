#!/usr/bin/env python3
"""Export targeted route-relation authority from one archived OSM PBF slice.

The exporter is independent from TrailForge assembly. It discovers AOI roots in
one streaming pass when needed, then retains only the selected recursive
relation hierarchy. Separate filtered passes capture referenced ways and nodes.
Raw and prefilter runs reuse AOI roots through ``--roots-from``. Output is
canonical JSON written atomically and bound to the exact archived slice path.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Any

ATTRIBUTION = "© OpenStreetMap contributors"
ACCEPTED_ROUTE_VALUES = frozenset({"hiking", "foot", "walking", "running"})
SCOPES = frozenset({"aoi", "raw-denmark", "prefiltered-denmark"})
SOURCE_ARTIFACTS = {
    "raw-denmark": "data/source/mols-bjerge.raw.osm.pbf",
    "prefiltered-denmark": "data/source/mols-bjerge.prefilter.osm.pbf",
    "aoi": "data/aoi/mols-bjerge.osm.pbf",
}
_MEMBER_TYPES = {
    "n": "node", "node": "node",
    "w": "way", "way": "way",
    "r": "relation", "relation": "relation",
}


def _normalize(value: Any) -> str:
    return str(value or "").strip().casefold()


def _member_type(value: Any) -> str:
    normalized = _normalize(value)
    if normalized in _MEMBER_TYPES:
        return _MEMBER_TYPES[normalized]
    for suffix, member_type in ((".node", "node"), (".way", "way"),
                                (".relation", "relation")):
        if normalized.endswith(suffix):
            return member_type
    raise ValueError(f"unsupported OSM relation member type: {value!r}")


def _tags(values) -> dict[str, str]:
    return dict(sorted((str(tag.k), str(tag.v)) for tag in values))


def _sha256(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size


def _load_osmium():
    try:
        return importlib.import_module("osmium")
    except ImportError as error:
        raise RuntimeError(
            "pyosmium/osmium is required to export relation membership evidence"
        ) from error


def _is_hiking_route(tags: dict[str, str]) -> bool:
    return (_normalize(tags.get("type")) == "route"
            and _normalize(tags.get("route")) in ACCEPTED_ROUTE_VALUES)


def load_root_relation_ids(path: Path) -> list[int]:
    """Load the exact AOI-selected roots from an earlier canonical ledger."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError(f"roots ledger does not exist: {path}") from error
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid roots ledger {path}: {error}") from error
    roots = document.get("root_relation_ids")
    if roots is None and document.get("schema_version") == 1:
        roots = document.get("accepted_relation_ids")
    if (not isinstance(roots, list) or not roots
            or any(not isinstance(value, int) or isinstance(value, bool)
                   or value <= 0 for value in roots)
            or len(roots) != len(set(roots))
            or roots != sorted(roots)):
        raise ValueError("roots ledger has invalid root_relation_ids")
    return roots


def _relation_record(relation) -> dict:
    relation_id = int(relation.id)
    if relation_id <= 0:
        raise ValueError(f"invalid relation id: {relation_id!r}")
    members = []
    for sequence, member in enumerate(relation.members):
        members.append({
            "sequence": sequence,
            "type": _member_type(member.type),
            "ref": int(member.ref),
            "role": str(member.role or ""),
        })
    return {
        "relation_id": relation_id,
        "tags": _tags(relation.tags),
        "members": members,
    }


def _timed_metric(kind: str, started: float, *, seen: int,
                  retained: int) -> dict[str, Any]:
    return {
        "kind": kind,
        "seen": seen,
        "retained": retained,
        "seconds": round(time.monotonic() - started, 6),
    }


def read_pbf_records(path: Path, *, root_relation_ids: list[int] | None = None,
                     osmium_module=None
                     ) -> tuple[list[dict], dict[int, dict], list[int],
                                list[int], dict[str, Any]]:
    """Stream only the selected recursive hierarchy, ways, and node locations.

    No pass stores an irrelevant relation member list. Recursion may require
    another pass when a referenced child sorts before its parent, but each pass
    retains only newly reached relation records. The workflow supplies compact
    root-closure slices, so repeated relation passes never rescan country-scale
    inputs.
    """
    osmium_module = osmium_module or _load_osmium()
    pass_metrics: list[dict[str, Any]] = []

    if root_relation_ids is None:
        discovered_roots: set[int] = set()
        seen_ids: set[int] = set()
        seen_count = 0
        started = time.monotonic()

        class RootDiscoveryPass(osmium_module.SimpleHandler):
            def relation(self, relation):
                nonlocal seen_count
                seen_count += 1
                relation_id = int(relation.id)
                if relation_id <= 0 or relation_id in seen_ids:
                    raise ValueError(
                        f"invalid or duplicate relation id: {relation_id!r}")
                seen_ids.add(relation_id)
                if _is_hiking_route(_tags(relation.tags)):
                    discovered_roots.add(relation_id)

        RootDiscoveryPass().apply_file(str(path))
        roots = sorted(discovered_roots)
        pass_metrics.append(_timed_metric(
            "relation-root-discovery", started, seen=seen_count,
            retained=len(roots)))
    else:
        roots = list(root_relation_ids)
    if not roots:
        raise ValueError("no selected hiking route roots were found")

    relations: dict[int, dict] = {}
    wanted_relation_ids = set(roots)
    pass_index = 0
    while True:
        pass_index += 1
        retained_this_pass = 0
        seen_count = 0
        seen_this_pass: set[int] = set()
        started = time.monotonic()

        class RelationClosurePass(osmium_module.SimpleHandler):
            def relation(self, relation):
                nonlocal retained_this_pass, seen_count
                seen_count += 1
                relation_id = int(relation.id)
                if relation_id in seen_this_pass:
                    raise ValueError(
                        f"invalid or duplicate relation id: {relation_id!r}")
                seen_this_pass.add(relation_id)
                if (relation_id not in wanted_relation_ids
                        or relation_id in relations):
                    return
                record = _relation_record(relation)
                relations[relation_id] = record
                retained_this_pass += 1
                wanted_relation_ids.update(
                    member["ref"] for member in record["members"]
                    if member["type"] == "relation")

        RelationClosurePass().apply_file(str(path))
        pass_metrics.append(_timed_metric(
            f"relation-closure-{pass_index}", started, seen=seen_count,
            retained=retained_this_pass))
        if retained_this_pass == 0:
            break

    missing_relation_ids = sorted(wanted_relation_ids - set(relations))
    target_relations = [relations[relation_id]
                        for relation_id in sorted(relations)]
    wanted_way_ids = {
        member["ref"]
        for relation in target_relations
        for member in relation["members"]
        if member["type"] == "way"
    }
    ways: dict[int, dict] = {}
    wanted_node_ids: set[int] = set()
    seen_way_count = 0
    retained_way_count = 0
    seen_way_ids: set[int] = set()
    started = time.monotonic()

    class WayPass(osmium_module.SimpleHandler):
        def way(self, way):
            nonlocal seen_way_count, retained_way_count
            seen_way_count += 1
            way_id = int(way.id)
            if way_id not in wanted_way_ids:
                return
            if way_id in seen_way_ids:
                raise ValueError(f"duplicate target way id: {way_id}")
            seen_way_ids.add(way_id)
            node_ids = [int(node.ref) for node in way.nodes]
            ways[way_id] = {
                "tags": _tags(way.tags),
                "node_ids": node_ids,
            }
            wanted_node_ids.update(node_ids)
            retained_way_count += 1

    WayPass().apply_file(str(path))
    pass_metrics.append(_timed_metric(
        "target-ways", started, seen=seen_way_count,
        retained=retained_way_count))

    node_coordinates: dict[int, list[float]] = {}
    seen_node_count = 0
    retained_node_count = 0
    started = time.monotonic()

    class NodePass(osmium_module.SimpleHandler):
        def node(self, node):
            nonlocal seen_node_count, retained_node_count
            seen_node_count += 1
            node_id = int(node.id)
            if node_id not in wanted_node_ids:
                return
            try:
                coordinates = [float(node.location.lon), float(node.location.lat)]
            except (AttributeError, RuntimeError, TypeError, ValueError):
                return
            prior = node_coordinates.get(node_id)
            if prior is not None and prior != coordinates:
                raise ValueError(
                    f"target node {node_id} has conflicting coordinates")
            if prior is None:
                retained_node_count += 1
            node_coordinates[node_id] = coordinates

    NodePass().apply_file(str(path))
    pass_metrics.append(_timed_metric(
        "target-nodes", started, seen=seen_node_count,
        retained=retained_node_count))
    for way in ways.values():
        way["coordinates"] = [
            node_coordinates.get(node_id) for node_id in way["node_ids"]
        ]
        way["missing_node_ids"] = [
            node_id for node_id in way["node_ids"]
            if node_id not in node_coordinates
        ]
    metrics = {
        "passes": pass_metrics,
        "relation_records_retained": len(relations),
        "way_records_retained": len(ways),
        "node_records_retained": len(node_coordinates),
    }
    return target_relations, ways, roots, missing_relation_ids, metrics


def build_graph(relations: list[dict], ways: dict[int, dict], *,
                source_sha256: str, source_bytes: int, scope: str,
                root_relation_ids: list[int],
                missing_relation_ids: list[int] | None = None) -> dict:
    """Build one deterministic, target-only relation authority ledger."""
    if scope not in SCOPES:
        raise ValueError(f"invalid evidence scope: {scope!r}")
    if (not root_relation_ids or root_relation_ids != sorted(root_relation_ids)
            or len(root_relation_ids) != len(set(root_relation_ids))
            or any(not isinstance(value, int) or isinstance(value, bool)
                   or value <= 0 for value in root_relation_ids)):
        raise ValueError("root relation ids must be sorted unique positive integers")

    output_relations = []
    seen_relation_ids = set()
    for relation in sorted(relations, key=lambda record: record["relation_id"]):
        relation_id = int(relation["relation_id"])
        if relation_id <= 0 or relation_id in seen_relation_ids:
            raise ValueError(f"invalid or duplicate relation id: {relation_id!r}")
        seen_relation_ids.add(relation_id)
        tags = dict(sorted(relation["tags"].items()))
        members = []
        for sequence, member in enumerate(relation["members"]):
            if member.get("sequence") != sequence:
                raise ValueError(
                    f"relation {relation_id} member sequence is not contiguous")
            ref = int(member["ref"])
            if ref <= 0:
                raise ValueError(f"relation {relation_id} has invalid member ref")
            members.append({
                "sequence": sequence,
                "type": _member_type(member["type"]),
                "ref": ref,
                "role": str(member.get("role") or ""),
            })
        direct_way_ids = list(dict.fromkeys(
            member["ref"] for member in members if member["type"] == "way"
        ))
        direct_ways = []
        for way_id in direct_way_ids:
            source = ways.get(way_id)
            if source is None:
                direct_ways.append({
                    "way_id": way_id,
                    "status": "missing",
                    "tags": None,
                    "node_ids": None,
                    "coordinates": None,
                    "missing_node_ids": None,
                })
                continue
            node_ids = list(source.get("node_ids") or [])
            coordinates = list(source.get("coordinates") or [])
            missing_nodes = list(source.get("missing_node_ids") or [])
            status = ("present" if not missing_nodes and len(node_ids) >= 2
                      else "incomplete")
            direct_ways.append({
                "way_id": way_id,
                "status": status,
                "tags": dict(sorted((source.get("tags") or {}).items())),
                "node_ids": node_ids,
                "coordinates": coordinates,
                "missing_node_ids": missing_nodes,
            })
        output_relations.append({
            "relation_id": relation_id,
            "accepted_hiking_route": _is_hiking_route(tags),
            "tags": tags,
            "members": members,
            "direct_way_ids": direct_way_ids,
            "direct_ways": direct_ways,
        })

    missing_relations = sorted(set(missing_relation_ids or []))
    missing_ways = sorted({
        direct["way_id"]
        for relation in output_relations
        for direct in relation["direct_ways"]
        if direct["status"] == "missing"
    })
    incomplete_ways = sorted({
        direct["way_id"]
        for relation in output_relations
        for direct in relation["direct_ways"]
        if direct["status"] == "incomplete"
    })
    return {
        "schema_version": 2,
        "scope": scope,
        "source": {
            "kind": "runner-local-osm-pbf",
            "artifact": SOURCE_ARTIFACTS[scope],
            "sha256": source_sha256,
            "bytes": source_bytes,
        },
        "attribution": ATTRIBUTION,
        "accepted_route_values": sorted(ACCEPTED_ROUTE_VALUES),
        "root_relation_ids": root_relation_ids,
        "relation_count": len(output_relations),
        "missing_relation_ids": missing_relations,
        "missing_direct_way_ids": missing_ways,
        "incomplete_direct_way_ids": incomplete_ways,
        "relations": output_relations,
    }


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode("utf-8")


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _emit_github_identity(path: Path, github_output: Path | None) -> None:
    """Publish the durable ledger identity from the creator step."""
    if github_output is None:
        return
    sha256, size = _sha256(path)
    with github_output.open("a", encoding="utf-8") as output:
        output.write(f"relation_ledger_sha256={sha256}\n")
        output.write(f"relation_ledger_bytes={size}\n")


def export_relation_members(input_path: Path, output_path: Path, *, scope: str,
                            roots_from: Path | None = None,
                            github_output: Path | None = None,
                            osmium_module=None,
                            metrics_out: dict[str, Any] | None = None) -> dict:
    if not input_path.is_file():
        raise FileNotFoundError(f"local PBF does not exist: {input_path}")
    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {sorted(SCOPES)!r}")
    roots = load_root_relation_ids(roots_from) if roots_from is not None else None
    if scope != "aoi" and roots is None:
        raise ValueError(f"scope {scope!r} requires --roots-from")
    source_sha256, source_bytes = _sha256(input_path)
    relations, ways, selected_roots, missing_relations, metrics = \
        read_pbf_records(
            input_path, root_relation_ids=roots, osmium_module=osmium_module)
    graph = build_graph(
        relations, ways, source_sha256=source_sha256,
        source_bytes=source_bytes, scope=scope,
        root_relation_ids=selected_roots,
        missing_relation_ids=missing_relations)
    _atomic_write(output_path, canonical_json_bytes(graph))
    _emit_github_identity(output_path, github_output)
    if metrics_out is not None:
        metrics_out.update(metrics)
    return graph


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--in", dest="input_path", required=True,
                        help="archived runner-local .osm.pbf slice")
    parser.add_argument("--scope", choices=sorted(SCOPES), required=True)
    parser.add_argument("--roots-from",
                        help="AOI ledger whose root_relation_ids select targets")
    parser.add_argument("--out", dest="output_path", required=True,
                        help="canonical relation-members JSON output")
    parser.add_argument("--github-output", type=Path,
                        help="append creator-owned ledger identity outputs")
    return parser


def main(argv=None, *, osmium_module=None) -> int:
    args = _parser().parse_args(argv)
    metrics: dict[str, Any] = {}
    try:
        graph = export_relation_members(
            Path(args.input_path), Path(args.output_path), scope=args.scope,
            roots_from=Path(args.roots_from) if args.roots_from else None,
            github_output=args.github_output,
            osmium_module=osmium_module, metrics_out=metrics)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    for record in metrics.get("passes", []):
        print(
            "export-pass "
            f"kind={record['kind']} seen={record['seen']} "
            f"retained={record['retained']} seconds={record['seconds']:.6f}",
            file=sys.stderr,
        )
    print(
        f"exported {len(graph['root_relation_ids'])} selected hiking roots "
        f"and {graph['relation_count']} hierarchy relations from "
        f"{graph['scope']} -> {args.output_path}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
