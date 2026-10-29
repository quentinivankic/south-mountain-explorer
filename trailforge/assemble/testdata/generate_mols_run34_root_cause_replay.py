#!/usr/bin/env python3
"""Generate the compact, test-only Mols run-34 root-cause replay."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

RUN_ID = 37213946834
SOURCE_SHA = "c2357749ed06dc1860c8ce469a926ed9f108ade8"
ARTIFACT_NAME = f"mols-bjerge-qa-{SOURCE_SHA}"
ATTRIBUTION = "© OpenStreetMap contributors"
ODBL = "Open Database License (ODbL) 1.0"
FIXTURE_NAME = "mols-run34-root-cause-replay.json"
MANIFEST_NAME = "mols-run34-root-cause-replay.manifest.json"
ROOT_IDS = (178382, 2006949, 2203180, 4603299, 14190349)
REMOVED_ROOT_IDS = frozenset({2006949, 2203180})

# Independently reconstructed from the archived source coordinates in the
# authoritative root-cause report. Values are evidence, not acceptance knobs.
_COUNTERFACTUAL_COMPONENTS = {
    "Molsruten": (2, 5, 1, 5, "boundary-induced-after-full-authority"),
    "Kaløstien": (1, 1, 1, 1, "resolved-by-default-foot"),
    "Dråby Sporet. Blå rute": (1, 2, 1, 2, "boundary-induced-after-default-foot"),
    "Djurslandstien": (2, 5, 1, 5, "boundary-induced-after-full-authority"),
    "Mols Bjerge-stien Ebeltoftetapen": (2, 2, 1, 1, "unsafe-in-area"),
    "Mols Bjerge-stien Kaløetapen": (1, 4, 1, 4, "boundary-induced-after-default-foot"),
    "Kløverstier-Naturruten": (1, 2, 1, 2, "boundary-induced-after-default-foot"),
    "Kløverstier-Landsbyruten": (1, 1, 1, 1, "resolved-by-default-foot"),
    "Mols Bjerge-stien Gåsehage-etapen": (2, 2, 1, 1, "unsafe-in-area"),
    "Langs Kysten": (3, 3, 3, 3, "unresolved-topology"),
    "Den gamle købstad": (2, 2, 2, 2, "unresolved-topology"),
}
_SOURCE_FILES = (
    "analysis-summary.json",
    "run35-root-cause.md",
    f"{ARTIFACT_NAME}/reports/assembly.json",
    f"{ARTIFACT_NAME}/data/aoi/mols-bjerge.trails.geojson",
    f"{ARTIFACT_NAME}/data/aoi/mols-bjerge.relation-members.json",
)


class FixtureError(ValueError):
    pass


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode("utf-8")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _atomic_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _load_sources(run_dir: Path) -> tuple[dict[str, Any], dict[str, dict]]:
    loaded = {}
    identities = {}
    for relative in _SOURCE_FILES:
        path = run_dir / relative
        try:
            raw = path.read_bytes()
        except OSError as error:
            raise FixtureError(f"cannot read required source {relative}: {error}") from error
        identities[relative] = {"bytes": len(raw), "sha256": _sha256(raw)}
        if path.suffix in {".json", ".geojson"}:
            try:
                loaded[relative] = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise FixtureError(f"invalid JSON source {relative}: {error}") from error
        else:
            loaded[relative] = raw.decode("utf-8")
    return loaded, identities


def _available_exclusions(audits: list[dict]) -> list[dict]:
    by_way: dict[int, dict] = {}
    for audit in audits:
        root_id = audit.get("relation_id")
        for member in audit.get("direct_way_members") or []:
            if (member.get("source_status") != "available"
                    or member.get("status") != "excluded"):
                continue
            way_id = member["way_id"]
            row = by_way.setdefault(way_id, {
                "way_id": way_id,
                "root_relation_ids": [],
                "effective_roles": [],
                "exclusion_reasons": [],
                "tags": member.get("tags") or {},
            })
            if row["tags"] != (member.get("tags") or {}):
                raise FixtureError(f"available exclusion w{way_id} has conflicting tags")
            if root_id not in row["root_relation_ids"]:
                row["root_relation_ids"].append(root_id)
            if member.get("effective_role") not in row["effective_roles"]:
                row["effective_roles"].append(member.get("effective_role"))
            if member.get("exclusion_reason") not in row["exclusion_reasons"]:
                row["exclusion_reasons"].append(member.get("exclusion_reason"))
    return [by_way[way_id] for way_id in sorted(by_way)]


def build_fixture(sources: dict[str, Any]) -> dict:
    summary = sources["analysis-summary.json"]
    assembly = sources[f"{ARTIFACT_NAME}/reports/assembly.json"]
    trails = sources[
        f"{ARTIFACT_NAME}/data/aoi/mols-bjerge.trails.geojson"]
    if (summary.get("run") or {}).get("id") != RUN_ID \
            or (summary.get("run") or {}).get("sha") != SOURCE_SHA:
        raise FixtureError("run summary identity is not run 34 at the pinned SHA")
    quality = assembly.get("quality")
    if not isinstance(quality, dict):
        raise FixtureError("assembly quality evidence is missing")
    audits = quality.get("relation_member_audit") or []
    audit_by_root = {audit.get("relation_id"): audit for audit in audits}
    if any(root_id not in audit_by_root for root_id in ROOT_IDS):
        raise FixtureError("five root-cause relation audits are incomplete")

    missing_roots = []
    for root_id in ROOT_IDS:
        audit = audit_by_root[root_id]
        missing = sorted({
            member["way_id"] for member in audit.get("direct_way_members") or []
            if member.get("source_status") == "missing"
        })
        missing_roots.append({
            "relation_id": root_id,
            "assembly_status": audit.get("assembly_status"),
            "missing_way_ids": missing,
            "missing_relation_ids": audit.get("missing_relation_ids") or [],
        })

    components = []
    for archived in summary["connectivity"]["relation_unresolved"]:
        name = archived["name"]
        if name not in _COUNTERFACTUAL_COMPONENTS:
            raise FixtureError(f"unexpected unresolved relation {name!r}")
        safe_source, safe_postclip, all_source, all_postclip, disposition = \
            _COUNTERFACTUAL_COMPONENTS[name]
        components.append({
            "name": name,
            "relation_ids": archived["relation_ids"],
            "run34_source_components": archived["source_components"],
            "run34_postclip_components": archived["postclip_components"],
            "safe_source_components": safe_source,
            "safe_postclip_components": safe_postclip,
            "all_main_source_components": all_source,
            "all_main_postclip_components": all_postclip,
            "expected_run35_disposition": disposition,
        })

    malt = next((feature for feature in trails.get("features") or []
                 if (feature.get("properties") or {}).get("name") == "Maltgården"),
                None)
    if malt is None:
        raise FixtureError("Maltgården evidence is missing")
    properties = malt["properties"]
    records = properties.get("source_ways") or []
    if len(records) != 1:
        raise FixtureError("Maltgården must have one source way")
    source = records[0]

    available = _available_exclusions(audits)
    tags_by_way = {row["way_id"]: row["tags"] for row in available}
    for audit in audits:
        for member in audit.get("direct_way_members") or []:
            if member.get("source_status") == "available":
                tags_by_way.setdefault(member["way_id"], member.get("tags") or {})
    road_rows = quality.get("restored_road_relations") or []
    road_way_ids = sorted({way_id for row in road_rows
                           for way_id in row.get("restored_way_ids") or []})

    return {
        "schema_version": 1,
        "description": (
            "Compact deterministic run-34 Denmark root-cause replay; "
            "test-only and never runtime/app data"
        ),
        "provenance": {
            "github_run_id": RUN_ID,
            "github_run_number": 34,
            "source_sha": SOURCE_SHA,
            "artifact_name": ARTIFACT_NAME,
            "attribution": ATTRIBUTION,
            "license": ODBL,
            "geometry_source": "OpenStreetMap",
            "use": "test-only-root-cause-regression",
        },
        "missing_root_audits": missing_roots,
        "available_exclusions": available,
        "component_rows": components,
        "maltgaarden": {
            "name": properties.get("name"),
            "way_id": source.get("way_id"),
            "source": properties.get("source"),
            "relation_ids": properties.get("relation_ids"),
            "length_mi": properties.get("length_mi"),
            "tags": source.get("tags"),
            "node_ids": source.get("node_ids"),
            "coordinates": source.get("coordinates"),
        },
        "restored_road_rows": road_rows,
        "restored_road_aggregate": quality.get("restored_road_aggregate"),
        "restored_way_tags": [
            {"way_id": way_id, "tags": tags_by_way.get(way_id, {})}
            for way_id in road_way_ids
        ],
    }


def generate(*, run_dir: Path, output: Path, manifest_path: Path,
             update_manifest: bool = False) -> dict:
    sources, identities = _load_sources(run_dir)
    fixture = build_fixture(sources)
    raw = canonical_json_bytes(fixture)
    projection = {
        "missing_root_audit_count": len(fixture["missing_root_audits"]),
        "available_exclusion_count": len(fixture["available_exclusions"]),
        "component_row_count": len(fixture["component_rows"]),
        "restored_road_row_count": len(fixture["restored_road_rows"]),
        "restored_way_tag_count": len(fixture["restored_way_tags"]),
    }
    expected = {
        "schema_version": 1,
        "source": {
            "github_run_id": RUN_ID,
            "source_sha": SOURCE_SHA,
            "artifact_name": ARTIFACT_NAME,
            "attribution": ATTRIBUTION,
            "license": ODBL,
        },
        "source_files": identities,
        "projection": projection,
        "output": {
            "file": FIXTURE_NAME,
            "bytes": len(raw),
            "sha256": _sha256(raw),
        },
    }
    if not update_manifest:
        try:
            actual = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise FixtureError(f"cannot load fixture manifest: {error}") from error
        if actual != expected:
            raise FixtureError("fixture manifest/source/output identity changed")
    _atomic_write(output, raw)
    if update_manifest:
        _atomic_write(
            manifest_path,
            (json.dumps(expected, ensure_ascii=False, sort_keys=True, indent=2)
             + "\n").encode("utf-8"),
        )
    return expected


def main(argv: list[str] | None = None) -> int:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--output", type=Path, default=here / FIXTURE_NAME)
    parser.add_argument("--manifest", type=Path, default=here / MANIFEST_NAME)
    parser.add_argument("--update-manifest", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = generate(
            run_dir=args.run_dir.resolve(), output=args.output.resolve(),
            manifest_path=args.manifest.resolve(),
            update_manifest=args.update_manifest)
    except FixtureError as error:
        parser.exit(2, f"error: {error}\n")
    print(json.dumps(result["output"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
