#!/usr/bin/env python3
"""Export canonical AOI way/relation topology and destination POI authority."""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile
from typing import Any
import unicodedata

ATTRIBUTION = "© OpenStreetMap contributors"
_DESTINATION_POI_CLASSES = (
    (("natural", "peak"), "natural=peak"),
    (("natural", "arch"), "natural=arch"),
    (("natural", "saddle"), "natural=saddle"),
    (("natural", "cliff"), "natural=cliff"),
    (("natural", "rock"), "natural=rock"),
    (("natural", "stone"), "natural=stone"),
    (("tourism", "viewpoint"), "tourism=viewpoint"),
    (("tourism", "attraction"), "tourism=attraction"),
    (("historic", "archaeological_site"), "historic=archaeological_site"),
    (("historic", "castle"), "historic=castle"),
    (("historic", "ruins"), "historic=ruins"),
    (("amenity", "shelter"), "amenity=shelter"),
    (("highway", "trailhead"), "highway=trailhead"),
)
_MEMBER_TYPES = {
    "n": "node", "node": "node",
    "w": "way", "way": "way",
    "r": "relation", "relation": "relation",
}


def _normalize(value: Any) -> str:
    return str(value or "").strip().casefold()


def _destination_eligibility_class(tags: dict[str, str]) -> str | None:
    normalized = {_normalize(key): _normalize(value)
                  for key, value in tags.items()}
    for (key, value), eligibility_class in _DESTINATION_POI_CLASSES:
        if normalized.get(key) == value:
            return eligibility_class
    return None


def _destination_display_name(tags: dict[str, str]) -> str | None:
    keys = ["name", *sorted(
        key for key in tags if key.startswith("name:"))]
    for key in keys:
        value = tags.get(key)
        if not isinstance(value, str):
            continue
        normalized = " ".join(unicodedata.normalize("NFC", value).split())
        if normalized:
            return normalized
    return None


def _member_type(value: Any) -> str:
    normalized = _normalize(value)
    if normalized in _MEMBER_TYPES:
        return _MEMBER_TYPES[normalized]
    for suffix, member_type in ((".node", "node"), (".way", "way"),
                                (".relation", "relation")):
        if normalized.endswith(suffix):
            return member_type
    raise ValueError(f"unsupported OSM relation member type: {value!r}")


def _identity(path: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    return {"sha256": digest.hexdigest(), "bytes": size}


def _load_osmium():
    try:
        return importlib.import_module("osmium")
    except ImportError as error:
        raise RuntimeError(
            "pyosmium/osmium is required to export AOI way topology") from error


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


def export_way_topology(input_path: Path, output_path: Path, *,
                        source_artifact: str,
                        github_output: Path | None = None,
                        osmium_module=None) -> dict[str, Any]:
    if not input_path.is_file():
        raise FileNotFoundError(f"local PBF does not exist: {input_path}")
    if not source_artifact or source_artifact.startswith("/") \
            or ".." in Path(source_artifact).parts:
        raise ValueError("source artifact must be a canonical relative path")
    osmium_module = osmium_module or _load_osmium()
    ways: dict[int, dict[str, Any]] = {}
    relations: dict[int, dict[str, Any]] = {}
    destination_nodes: dict[int, dict[str, Any]] = {}
    wanted_nodes: set[int] = set()

    class TopologyPass(osmium_module.SimpleHandler):
        def node(self, node):
            tags = dict(sorted((str(tag.k), str(tag.v)) for tag in node.tags))
            eligibility_class = _destination_eligibility_class(tags)
            if eligibility_class is None:
                return
            node_id = int(node.id)
            if node_id <= 0 or node_id in destination_nodes:
                raise ValueError(
                    f"invalid or duplicate destination node id: {node_id}")
            try:
                coordinate = [
                    float(node.location.lon), float(node.location.lat),
                ]
            except (AttributeError, RuntimeError, TypeError, ValueError) as error:
                raise ValueError(
                    f"destination node n{node_id} has invalid coordinates") \
                    from error
            if (not all(math.isfinite(value) for value in coordinate)
                    or not -180 <= coordinate[0] <= 180
                    or not -90 <= coordinate[1] <= 90):
                raise ValueError(
                    f"destination node n{node_id} has invalid coordinates")
            destination_nodes[node_id] = {
                "node_id": node_id,
                "tags": tags,
                "name": _destination_display_name(tags),
                "coordinate": coordinate,
                "eligibility_class": eligibility_class,
            }

        def way(self, way):
            tags = dict(sorted((str(tag.k), str(tag.v)) for tag in way.tags))
            way_id = int(way.id)
            if way_id <= 0 or way_id in ways:
                raise ValueError(f"invalid or duplicate source way id: {way_id}")
            node_ids = [int(node.ref) for node in way.nodes]
            if len(node_ids) < 2 or any(node_id <= 0 for node_id in node_ids):
                raise ValueError(f"source way w{way_id} has invalid topology")
            ways[way_id] = {"way_id": way_id, "tags": tags,
                            "node_ids": node_ids}
            wanted_nodes.update(node_ids)

        def relation(self, relation):
            relation_id = int(relation.id)
            if relation_id <= 0 or relation_id in relations:
                raise ValueError(
                    f"invalid or duplicate source relation id: {relation_id}")
            members = []
            for sequence, member in enumerate(relation.members):
                ref = int(member.ref)
                if ref <= 0:
                    raise ValueError(
                        f"source relation r{relation_id} has invalid member ref")
                members.append({
                    "sequence": sequence,
                    "type": _member_type(member.type),
                    "ref": ref,
                    "role": str(member.role or ""),
                })
            relations[relation_id] = {
                "relation_id": relation_id,
                "tags": dict(sorted(
                    (str(tag.k), str(tag.v)) for tag in relation.tags)),
                "members": members,
            }

    TopologyPass().apply_file(str(input_path))
    coordinates: dict[int, list[float]] = {}

    class NodePass(osmium_module.SimpleHandler):
        def node(self, node):
            node_id = int(node.id)
            if node_id not in wanted_nodes:
                return
            try:
                point = [float(node.location.lon), float(node.location.lat)]
            except (AttributeError, RuntimeError, TypeError, ValueError):
                return
            prior = coordinates.get(node_id)
            if prior is not None and prior != point:
                raise ValueError(f"node n{node_id} has conflicting coordinates")
            coordinates[node_id] = point

    NodePass().apply_file(str(input_path))
    records = []
    incomplete = []
    for way_id in sorted(ways):
        record = ways[way_id]
        missing = [node_id for node_id in record["node_ids"]
                   if node_id not in coordinates]
        if missing:
            incomplete.append(way_id)
        records.append({
            **record,
            "coordinates": [coordinates.get(node_id)
                            for node_id in record["node_ids"]],
            "missing_node_ids": missing,
        })
    document = {
        "schema_version": 3,
        "source": {
            "kind": "runner-local-osm-pbf",
            "artifact": source_artifact,
            **_identity(input_path),
        },
        "attribution": ATTRIBUTION,
        "way_count": len(records),
        "incomplete_way_ids": incomplete,
        "ways": records,
        "relation_count": len(relations),
        "relations": [relations[relation_id]
                      for relation_id in sorted(relations)],
        "destination_poi_count": len(destination_nodes),
        "destination_pois": [destination_nodes[node_id]
                             for node_id in sorted(destination_nodes)],
    }
    _atomic_write(output_path, canonical_json_bytes(document))
    if github_output is not None:
        identity = _identity(output_path)
        with github_output.open("a", encoding="utf-8") as output:
            output.write(f"way_topology_sha256={identity['sha256']}\n")
            output.write(f"way_topology_bytes={identity['bytes']}\n")
    return document


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--in", dest="input_path", required=True, type=Path)
    parser.add_argument("--out", dest="output_path", required=True, type=Path)
    parser.add_argument("--source-artifact", required=True)
    parser.add_argument("--github-output", type=Path,
                        help="append creator-owned topology identity outputs")
    return parser


def main(argv=None, *, osmium_module=None) -> int:
    args = _parser().parse_args(argv)
    try:
        document = export_way_topology(
            args.input_path, args.output_path,
            source_artifact=args.source_artifact,
            github_output=args.github_output,
            osmium_module=osmium_module)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    print(
        f"exported {document['way_count']} AOI source ways, "
        f"{document['relation_count']} AOI relations, and "
        f"{document['destination_poi_count']} destination POIs -> "
        f"{args.output_path}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
