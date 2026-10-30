#!/usr/bin/env python3
"""Simple (unsigned) Mols Bjerge publish helpers.

Two jobs, both read-only with respect to R2:

  prepare-input  Used by trailforge-publish.yml when mols_simple_publish=true.
                 Validates the dispatch inputs against the fixed Mols identity
                 and writes the one-row publication index plus the step outputs
                 the signed-receipt path would have produced. No HMAC, no
                 receipt, no R2.

  build-candidates  Used by sync-mols-to-r2.yml. Given the LIVE R2 bytes for
                 index.json / trail-search.json / trail-shapes.json, builds the
                 exact candidate bytes to upload by SPLICING the Mols records
                 onto the live bytes (non-Mols bytes are preserved verbatim, so
                 platform float drift in a rebuild cannot touch other areas).
                 Refuses unless the write set is exactly the five Mols keys.
                 parking.json is never read or written.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import r2_release_contract as contract  # noqa: E402

AREA = contract.MOLS_AREA_ID
GEOM_REPO = Path(contract.MOLS_REPOSITORY_PATHS[0])
SIL_REPO = Path(contract.MOLS_REPOSITORY_PATHS[1])
BUNDLE_REPO = Path(contract.MOLS_REPOSITORY_PATHS[3])
# R2 key -> candidate file name inside --out-dir
WRITE_KEYS = {
    f"{AREA}.json": "geom.json",
    f"silhouettes/{AREA}.json": "silhouette.json",
    "index.json": "index.json",
    "trail-search.json": "trail-search.json",
    "trail-shapes.json": "trail-shapes.json",
}


class SimpleError(Exception):
    pass


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def prepare_input(args: argparse.Namespace) -> int:
    expected = {
        "extract": contract.MOLS_EXTRACT,
        "state": contract.MOLS_STATE,
        "region_code": contract.MOLS_REGION_CODE,
        "bbox": contract.MOLS_BBOX,
    }
    actual = {
        "extract": args.extract, "state": args.state,
        "region_code": args.region_code, "bbox": args.bbox,
    }
    if actual != expected:
        raise SimpleError(f"inputs differ from the fixed Mols identity: {actual}")
    if (args.dry_run, args.no_routes, args.touch_report, args.elevation) != (
        "false", "false", "false", "false"
    ) or args.pilot_area_id:
        raise SimpleError("simple Mols publish needs dry_run/no_routes/touch_report/elevation=false and no pilot id")
    row = [AREA, contract.MOLS_AREA_NAME, contract.MOLS_STATE,
           contract.MOLS_CENTER_LAT, contract.MOLS_CENTER_LON, None, None,
           contract.MOLS_RELATION_ID]
    args.mols_index_output.write_text(json.dumps([row], ensure_ascii=True), encoding="utf-8")
    digest = contract.canonical_sha256({
        "schema": "trekdex-mols-simple-publish-input-v1",
        "identity": expected,
        "workflow_source_sha": args.workflow_source_sha,
    })
    outputs = {
        "release_mode": "mols",
        "receipt_sha256": digest,
        "area_id": AREA,
        "publication_index_path": str(args.mols_index_output),
    }
    with args.github_output.open("a", encoding="utf-8") as stream:
        for name, value in outputs.items():
            stream.write(f"{name}={value}\n")
    print(f"simple (unsigned) Mols publish input accepted digest={digest}")
    return 0


def _splice(live: bytes, records: list[Any], opener: bytes, closer: bytes, as_dict: bool) -> bytes:
    if not live.startswith(opener) or not live.endswith(closer) or len(live) < 3:
        raise SimpleError("live object has unexpected framing")
    parts = []
    for rec in records:
        if as_dict:
            parts.append(json.dumps({rec[0]: rec[1]}, separators=(",", ":"))[1:-1])
        else:
            parts.append(json.dumps(rec, separators=(",", ":"), ensure_ascii=True))
    return live[:-1] + b"," + ",".join(parts).encode("utf-8") + closer


def build_candidates(args: argparse.Namespace) -> int:
    repo = args.repo
    live = {k: Path(v).read_bytes() for k, v in (
        ("index.json", args.live_index),
        ("trail-search.json", args.live_search),
        ("trail-shapes.json", args.live_shapes),
    )}
    geom = (repo / GEOM_REPO).read_bytes()
    silhouette = (repo / SIL_REPO).read_bytes()
    bundle = (repo / BUNDLE_REPO).read_bytes()
    contract.strict_json_loads(silhouette.decode("utf-8"), "Mols silhouette")

    # index.json: repo bundle must equal live rows + exactly one new Mols row.
    live_rows = json.loads(live["index.json"])
    new_rows = json.loads(bundle)
    if any(r[0] == AREA for r in live_rows):
        raise SimpleError("live index.json already contains Mols; refusing to overwrite")
    if new_rows[:-1] != live_rows or len(new_rows) != len(live_rows) + 1 or new_rows[-1][0] != AREA:
        raise SimpleError("repo bundle index is not live index + one trailing Mols row")
    if json.dumps(new_rows, separators=(",", ":"), ensure_ascii=True).encode("utf-8") != bundle:
        raise SimpleError("repo bundle index framing is not the compact producer framing")

    # trail-search / trail-shapes: splice Mols records onto live bytes.
    candidates: dict[str, bytes] = {"index.json": bundle}
    for key, opener, closer, as_dict in (
        ("trail-search.json", b"[", b"]", False),
        ("trail-shapes.json", b"{", b"}", True),
    ):
        records, _ids = contract._expected_mols_global_records(key, geom)
        after = _splice(live[key], records, opener, closer, as_dict)
        # Proof: only exact Mols additions; every non-Mols record identical.
        contract.build_mols_global_delta(
            key, live[key], after, geom, baseline_etag=args.etag[key]
        )
        candidates[key] = after
    candidates[f"{AREA}.json"] = geom
    candidates[f"silhouettes/{AREA}.json"] = silhouette
    if set(candidates) != set(WRITE_KEYS):
        raise SimpleError("write set is not exactly the five Mols keys")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    plan = {"area": AREA, "writes": []}
    for key, name in WRITE_KEYS.items():
        (args.out_dir / name).write_bytes(candidates[key])
        plan["writes"].append({"key": key, "file": name, "bytes": len(candidates[key]),
                               "sha256": _sha(candidates[key])})
    for key, data in live.items():
        plan.setdefault("baseline", {})[key] = {"bytes": len(data), "sha256": _sha(data)}
    (args.out_dir / "plan.json").write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(plan, indent=2))
    return 0


SHARED_KEYS = ("index.json", "trail-search.json", "trail-shapes.json")


def rollback_actions(plan: dict[str, Any], live_dir: Path) -> dict[str, str]:
    """Per shared file: 'restore' if live is the applied candidate, 'skip' if already
    baseline. Anything else is a third party's publish: refuse."""
    cand = {w["key"]: w["sha256"] for w in plan["writes"]}
    actions: dict[str, str] = {}
    for key in SHARED_KEYS:
        live = _sha((live_dir / key).read_bytes())
        base = plan["baseline"][key]["sha256"]
        if live == cand[key] and live != base:
            actions[key] = "restore"
        elif live == base:
            actions[key] = "skip"
        else:
            raise SimpleError(f"live {key} is neither the applied candidate nor the baseline; refusing to roll back over it")
    return actions


def rollback_decide(args: argparse.Namespace) -> int:
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    for key, action in rollback_actions(plan, args.live_dir).items():
        print(f"{key}\t{action}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    a = sub.add_parser("prepare-input")
    for name in ("extract", "state", "region-code", "bbox", "dry-run", "no-routes",
                 "touch-report", "elevation", "pilot-area-id", "workflow-source-sha"):
        a.add_argument(f"--{name}", required=True)
    a.add_argument("--mols-index-output", required=True, type=Path)
    a.add_argument("--github-output", required=True, type=Path)
    b = sub.add_parser("build-candidates")
    b.add_argument("--repo", type=Path, default=Path("."))
    b.add_argument("--live-index", required=True, type=Path)
    b.add_argument("--live-search", required=True, type=Path)
    b.add_argument("--live-shapes", required=True, type=Path)
    b.add_argument("--etag-index", required=True)
    b.add_argument("--etag-search", required=True)
    b.add_argument("--etag-shapes", required=True)
    b.add_argument("--out-dir", required=True, type=Path)
    c = sub.add_parser("rollback-decide")
    c.add_argument("--plan", required=True, type=Path)
    c.add_argument("--live-dir", required=True, type=Path)
    args = p.parse_args(argv)
    try:
        if args.command == "prepare-input":
            return prepare_input(args)
        if args.command == "rollback-decide":
            return rollback_decide(args)
        args.etag = {"index.json": args.etag_index, "trail-search.json": args.etag_search,
                     "trail-shapes.json": args.etag_shapes}
        return build_candidates(args)
    except (SimpleError, contract.ManifestError, contract.InventoryError, OSError, ValueError) as error:
        print(f"REFUSED: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
