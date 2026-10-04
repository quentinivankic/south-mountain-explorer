#!/usr/bin/env python3
"""Export independent route-relation membership evidence from a local OSM PBF.

The exporter intentionally does not import TrailForge's assembler or relation
resolution. It performs two pyosmium passes over one runner-local AOI PBF and
writes canonical JSON atomically.
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
from typing import Any

ATTRIBUTION = "© OpenStreetMap contributors"
ACCEPTED_ROUTE_VALUES = frozenset({"hiking", "foot", "walking", "running"})
_MEMBER_TYPES = {"n": "node", "node": "node",
                 "w": "way", "way": "way",
                 "r": "relation", "relation": "relation"}


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


def read_pbf_records(path: Path, osmium_module=None) -> tuple[list[dict], dict[int, dict]]:
    """Read route relations and their direct way tags in independent passes."""
    osmium_module = osmium_module or _load_osmium()
    relations: list[dict] = []

    class RelationPass(osmium_module.SimpleHandler):
        def relation(self, relation):
            tags = _tags(relation.tags)
            if _normalize(tags.get("type")) != "route":
                return
            members = []
            for sequence, member in enumerate(relation.members):
                members.append({
                    "sequence": sequence,
                    "type": _member_type(member.type),
                    "ref": int(member.ref),
                    "role": str(member.role or ""),
                })
            relations.append({
                "relation_id": int(relation.id),
                "tags": tags,
                "members": members,
            })

    RelationPass().apply_file(str(path))
    wanted_way_ids = {
        member["ref"]
        for relation in relations
        for member in relation["members"]
        if member["type"] == "way"
    }
    ways: dict[int, dict] = {}

    class WayPass(osmium_module.SimpleHandler):
        def way(self, way):
            way_id = int(way.id)
            if way_id in wanted_way_ids:
                ways[way_id] = _tags(way.tags)

    WayPass().apply_file(str(path))
    return relations, ways


def build_graph(relations: list[dict], ways: dict[int, dict], *,
                source_sha256: str, source_bytes: int) -> dict:
    """Build the deterministic machine-readable relation graph."""
    output_relations = []
    accepted_ids = []
    seen_relation_ids = set()
    for relation in sorted(relations, key=lambda record: record["relation_id"]):
        relation_id = int(relation["relation_id"])
        if relation_id <= 0 or relation_id in seen_relation_ids:
            raise ValueError(f"invalid or duplicate route relation id: {relation_id!r}")
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
            present = way_id in ways
            direct_ways.append({
                "way_id": way_id,
                "present": present,
                "tags": dict(sorted(ways[way_id].items())) if present else None,
            })
        accepted = (_normalize(tags.get("type")) == "route"
                    and _normalize(tags.get("route")) in ACCEPTED_ROUTE_VALUES)
        if accepted:
            accepted_ids.append(relation_id)
        output_relations.append({
            "relation_id": relation_id,
            "accepted_hiking_route": accepted,
            "tags": tags,
            "members": members,
            "direct_way_ids": direct_way_ids,
            "direct_ways": direct_ways,
        })
    by_id = {relation["relation_id"]: relation for relation in output_relations}
    relevant_relation_ids = set()

    def include_hierarchy(relation_id):
        if relation_id in relevant_relation_ids or relation_id not in by_id:
            return
        relevant_relation_ids.add(relation_id)
        for member in by_id[relation_id]["members"]:
            if member["type"] == "relation":
                include_hierarchy(member["ref"])

    for relation_id in accepted_ids:
        include_hierarchy(relation_id)
    missing_way_ids = sorted({
        direct["way_id"]
        for relation_id in relevant_relation_ids
        for direct in by_id[relation_id]["direct_ways"]
        if not direct["present"]
    })
    return {
        "schema_version": 1,
        "source": {
            "kind": "runner-local-osm-pbf",
            "sha256": source_sha256,
            "bytes": source_bytes,
        },
        "attribution": ATTRIBUTION,
        "accepted_route_values": sorted(ACCEPTED_ROUTE_VALUES),
        "relation_count": len(output_relations),
        "accepted_relation_ids": accepted_ids,
        "missing_direct_way_ids": missing_way_ids,
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


def export_relation_members(input_path: Path, output_path: Path,
                            osmium_module=None) -> dict:
    if not input_path.is_file():
        raise FileNotFoundError(f"local AOI PBF does not exist: {input_path}")
    source_sha256, source_bytes = _sha256(input_path)
    relations, ways = read_pbf_records(input_path, osmium_module=osmium_module)
    graph = build_graph(
        relations, ways, source_sha256=source_sha256,
        source_bytes=source_bytes)
    _atomic_write(output_path, canonical_json_bytes(graph))
    return graph


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--in", dest="input_path", required=True,
                        help="runner-local AOI .osm.pbf")
    parser.add_argument("--out", dest="output_path", required=True,
                        help="canonical relation-members JSON output")
    return parser


def main(argv=None, *, osmium_module=None) -> int:
    args = _parser().parse_args(argv)
    try:
        graph = export_relation_members(
            Path(args.input_path), Path(args.output_path),
            osmium_module=osmium_module)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    print(
        f"exported {len(graph['accepted_relation_ids'])} accepted hiking routes "
        f"from {graph['relation_count']} route relations -> {args.output_path}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
