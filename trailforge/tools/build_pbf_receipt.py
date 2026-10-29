#!/usr/bin/env python3
"""Seal an already executed osmium PBF transformation with byte provenance."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any, Callable


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


def _canonical_sha256(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _counts(text: str) -> dict[str, int]:
    result = {}
    for key in ("nodes", "ways", "relations"):
        match = re.search(rf"Number of {key}:\s*([0-9]+)", text)
        if match is None:
            raise RuntimeError(f"osmium fileinfo omitted {key} count")
        result[key] = int(match.group(1))
    return result


def _capture(runner: Callable[..., Any], command: list[str]) -> str:
    completed = runner(command, check=False, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, text=True)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(
            f"metadata command failed with exit {completed.returncode}: "
            f"{detail or 'no diagnostic'}")
    return (completed.stdout or completed.stderr or "").strip()


def _load_roots(path: Path) -> list[int]:
    document = json.loads(path.read_text(encoding="utf-8"))
    roots = document.get("root_relation_ids")
    if (not isinstance(roots, list) or not roots or roots != sorted(roots)
            or len(roots) != len(set(roots))
            or any(not isinstance(value, int) or isinstance(value, bool)
                   or value <= 0 for value in roots)):
        raise ValueError("roots ledger has invalid root_relation_ids")
    return roots


def _write(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(document, output, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def emit_github_outputs(document: dict[str, Any], receipt: Path,
                        github_output: Path) -> None:
    """Append stage-owned trust values after the receipt is durable."""
    receipt_identity = _identity(receipt)
    kind = document.get("receipt_kind")
    if kind == "scope":
        compact = document.get("compact_output")
        parent = document.get("parent_pbf")
        if not isinstance(compact, dict) or not isinstance(parent, dict):
            raise ValueError("scope receipt identities are incomplete")
        values = {
            "parent_sha256": parent.get("sha256"),
            "parent_bytes": parent.get("bytes"),
            "slice_sha256": compact.get("sha256"),
            "slice_bytes": compact.get("bytes"),
            "receipt_sha256": receipt_identity["sha256"],
            "receipt_bytes": receipt_identity["bytes"],
            "root_set_sha256": document.get("root_set_sha256"),
        }
        extraction = document.get("extraction")
        if isinstance(extraction, dict):
            values.update({
                "aoi_name": extraction.get("name"),
                "aoi_bbox": extraction.get("bbox"),
                "root_bbox_sha256": extraction.get("root_bbox_sha256"),
            })
    elif kind == "transformation":
        parent = document.get("parent_pbf")
        output = document.get("output_pbf")
        if not isinstance(parent, dict) or not isinstance(output, dict):
            raise ValueError("transformation receipt identities are incomplete")
        values = {
            "input_sha256": parent.get("sha256"),
            "input_bytes": parent.get("bytes"),
            "output_sha256": output.get("sha256"),
            "output_bytes": output.get("bytes"),
            "receipt_sha256": receipt_identity["sha256"],
            "receipt_bytes": receipt_identity["bytes"],
        }
    else:
        raise ValueError("receipt kind is invalid for GitHub outputs")
    if (any(not re.fullmatch(r"[a-z0-9_]+", key) for key in values)
            or any(value is None or "\n" in str(value) or "\r" in str(value)
                   for value in values.values())):
        raise ValueError("GitHub receipt outputs are invalid")
    with github_output.open("a", encoding="utf-8") as output:
        for key, value in values.items():
            output.write(f"{key}={value}\n")


def build_receipt(parent: Path, output: Path, receipt: Path, *,
                  kind: str, scope: str, parent_label: str,
                  output_artifact: str, upstream_stage: str,
                  command: list[str], roots_from: Path | None = None,
                  aoi_name: str | None = None,
                  aoi_bbox: str | None = None,
                  runner: Callable[..., Any] = subprocess.run,
                  executable: str = "osmium") -> dict[str, Any]:
    if kind not in {"scope", "transformation"}:
        raise ValueError("receipt kind must be scope or transformation")
    if not parent.is_file() or not output.is_file():
        raise FileNotFoundError("receipt input and output PBFs must exist")
    if (not parent_label or parent_label.startswith("/")
            or ".." in Path(parent_label).parts
            or not output_artifact or output_artifact.startswith("/")
            or ".." in Path(output_artifact).parts):
        raise ValueError("receipt path labels must be canonical relative paths")
    if (not isinstance(command, list) or not command
            or not all(isinstance(value, str) and value for value in command)):
        raise ValueError("receipt command must be a nonempty string list")
    roots = _load_roots(roots_from) if roots_from is not None else None
    if kind == "scope" and roots is None:
        raise ValueError("scope receipt requires --roots-from")
    if kind == "transformation" and roots is not None:
        raise ValueError("transformation receipt cannot carry selected roots")
    is_aoi_scope = kind == "scope" and scope == "aoi"
    if is_aoi_scope and (not aoi_name or not aoi_bbox):
        raise ValueError("AOI scope receipt requires name and bbox")
    if not is_aoi_scope and (aoi_name is not None or aoi_bbox is not None):
        raise ValueError("AOI name/bbox are valid only for an AOI scope receipt")
    version = _capture(runner, [executable, "--version"])
    parent_counts = _counts(_capture(
        runner, [executable, "fileinfo", "-e", str(parent)]))
    output_counts = _counts(_capture(
        runner, [executable, "fileinfo", "-e", str(output)]))
    document = {
        "schema_version": 1,
        "receipt_kind": kind,
        "scope": scope,
        "canonical_source_path_label": parent_label,
        "parent_pbf": {**_identity(parent), "object_counts": parent_counts},
        "osmium": {"version": version, "command": command},
        ("compact_output" if kind == "scope" else "output_pbf"): {
            "artifact": output_artifact,
            **_identity(output),
            "object_counts": output_counts,
        },
        "upstream_workflow_stage": upstream_stage,
    }
    if roots is not None:
        document["root_relation_ids"] = roots
        document["root_set_sha256"] = _canonical_sha256(roots)
    if is_aoi_scope:
        document["extraction"] = {
            "name": aoi_name,
            "bbox": aoi_bbox,
            "root_bbox_sha256": _canonical_sha256({
                "bbox": aoi_bbox,
                "name": aoi_name,
                "root_relation_ids": roots,
            }),
        }
    _write(receipt, document)
    return document


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=("scope", "transformation"),
                        required=True)
    parser.add_argument("--scope", required=True)
    parser.add_argument("--parent", required=True, type=Path)
    parser.add_argument("--parent-label", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--output-artifact", required=True)
    parser.add_argument("--upstream-stage", required=True)
    parser.add_argument("--command-json", required=True)
    parser.add_argument("--roots-from", type=Path)
    parser.add_argument("--aoi-name")
    parser.add_argument("--aoi-bbox")
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("--github-output", type=Path)
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        command = json.loads(args.command_json)
        document = build_receipt(
            args.parent, args.output, args.receipt, kind=args.kind,
            scope=args.scope, parent_label=args.parent_label,
            output_artifact=args.output_artifact,
            upstream_stage=args.upstream_stage, command=command,
            roots_from=args.roots_from, aoi_name=args.aoi_name,
            aoi_bbox=args.aoi_bbox)
        if args.github_output is not None:
            emit_github_outputs(document, args.receipt, args.github_output)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    print(f"sealed {args.kind} PBF receipt -> {args.receipt}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
