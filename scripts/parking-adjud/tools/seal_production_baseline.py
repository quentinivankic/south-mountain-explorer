#!/usr/bin/env python3
"""Seal an unapproved, versioned production-baseline rotation candidate."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import national_census as census

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parents[2]


def seal_candidate(
        *, bundle_path: str | Path, geom_dir: str | Path,
        live_pool_path: str | Path, verdicts_path: str | Path,
        predecessor_path: str | Path, output_path: str | Path) -> dict:
    """Write one canonical successor without changing the approval registry."""
    output = Path(output_path).absolute()
    if os.path.lexists(output):
        raise census.CensusError(f"baseline candidate output already exists: {output}")
    if not output.parent.is_dir():
        raise census.CensusError(
            f"baseline candidate parent does not exist: {output.parent}"
        )
    predecessor = census._load_production_baseline(
        predecessor_path, require_approved=True
    )
    approvals, registry_binding = census._load_approved_baseline_registry()
    if predecessor.approval != approvals[-1]:
        raise census.CensusError(
            "baseline rotation predecessor must be the latest reviewed approval"
        )
    scope = census.capture_scope(bundle_path, geom_dir)
    live = census._load_live_pool(live_pool_path)
    verdicts = census._load_verdicts(verdicts_path)
    version = predecessor.document["version"] + 1
    document = census.production_baseline_candidate_document(
        scope,
        live,
        verdicts,
        version=version,
        predecessor_self_sha256=predecessor.self_sha256,
    )
    raw = census._canonical_bytes(document)
    census._verify_inventory(scope.geom_inventory)
    source_bindings = (
        predecessor.binding, predecessor.registry_binding, registry_binding,
        scope.bundle_binding, *scope.geom_bindings,
        live.binding, verdicts.binding,
    )
    for binding in source_bindings:
        if binding is not None:
            census._verify_binding(binding)
    census._exclusive_write(output, raw)
    census._verify_staged_file(output, raw)
    census._fsync_directory(output.parent)
    census._verify_inventory(scope.geom_inventory)
    for binding in source_bindings:
        if binding is not None:
            census._verify_binding(binding)
    candidate = census._load_production_baseline(output, require_approved=False)
    if candidate.document != document:
        raise AssertionError("sealed baseline candidate changed during verification")
    return {
        "path": str(output),
        "version": version,
        "name": document["name"],
        "predecessor_self_sha256": predecessor.self_sha256,
        "self_sha256": document["self_sha256"],
        "canonical_file_sha256": candidate.binding.sha256,
        "approved": False,
        "approval_registry_sha256": registry_binding.sha256,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bundle",
        default=_ROOT / "ios" / "SouthMountainExplorer" / "Resources" / "areas-index.json",
    )
    parser.add_argument("--geom-dir", default=_ROOT / "public" / "areas" / "geom")
    parser.add_argument("--live-pool", required=True, metavar="JSON")
    parser.add_argument(
        "--verdicts", default=census.parking_verdicts.DEFAULT_PATH, metavar="JSON"
    )
    parser.add_argument("--predecessor", required=True, metavar="APPROVED_JSON")
    parser.add_argument("--output", required=True, metavar="NEW_JSON")
    return parser


def main(argv=None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        result = seal_candidate(
            bundle_path=arguments.bundle,
            geom_dir=arguments.geom_dir,
            live_pool_path=arguments.live_pool,
            verdicts_path=arguments.verdicts,
            predecessor_path=arguments.predecessor,
            output_path=arguments.output,
        )
    except (census.CensusError, OSError, subprocess.SubprocessError) as error:
        print(f"seal_production_baseline: error: {error}", file=sys.stderr)
        return 2
    print(
        "sealed unapproved baseline candidate "
        f"{result['name']}\n"
        f"self_sha256={result['self_sha256']}\n"
        f"canonical_file_sha256={result['canonical_file_sha256']}\n"
        f"predecessor_self_sha256={result['predecessor_self_sha256']}\n"
        "approval unchanged: add the digest pair and predecessor to the reviewed "
        "registry, recompute its self-hash, and update the pinned registry file "
        "digest in national_census.py in one reviewed change"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
