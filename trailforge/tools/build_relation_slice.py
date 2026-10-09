#!/usr/bin/env python3
"""Build a compact deterministic OSM root-closure PBF with osmium getid."""
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

_TOOLS_DIR = Path(__file__).resolve().parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))
from build_pbf_receipt import emit_github_outputs  # noqa: E402


def _load_roots(path: Path) -> list[int]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError(f"roots ledger does not exist: {path}") from error
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid roots ledger {path}: {error}") from error
    roots = document.get("root_relation_ids")
    if (not isinstance(roots, list) or not roots or roots != sorted(roots)
            or len(roots) != len(set(roots))
            or any(not isinstance(value, int) or isinstance(value, bool)
                   or value <= 0 for value in roots)):
        raise ValueError("roots ledger has invalid root_relation_ids")
    return roots


def osmium_command(source: Path, id_file: Path, output: Path,
                   executable: str = "osmium") -> list[str]:
    """Return the repository-pinned recursive getid command."""
    return [
        executable, "getid", "-r", "--id-file", str(id_file),
        str(source), "-o", str(output), "--overwrite",
    ]


def _identity(path: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
    return {"sha256": digest.hexdigest(), "bytes": size}


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _object_counts(text: str) -> dict[str, int]:
    counts = {}
    for label, key in (("nodes", "nodes"), ("ways", "ways"),
                       ("relations", "relations")):
        match = re.search(rf"Number of {label}:\s*([0-9]+)", text)
        if match is None:
            raise RuntimeError(f"osmium fileinfo omitted {label} count")
        counts[key] = int(match.group(1))
    return counts


def _run_metadata(runner: Callable[..., Any], command: list[str]) -> str:
    completed = runner(
        command, check=False, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(
            f"metadata command failed with exit {completed.returncode}: "
            f"{detail or 'no diagnostic'}")
    return (completed.stdout or completed.stderr or "").strip()


def _write_receipt(path: Path, receipt: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(receipt, output, ensure_ascii=False, sort_keys=True,
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


def build_slice(source: Path, roots_from: Path, output: Path, *,
                runner: Callable[..., Any] = subprocess.run,
                executable: str = "osmium", receipt: Path | None = None,
                scope: str | None = None, source_label: str | None = None,
                output_artifact: str | None = None,
                upstream_stage: str | None = None,
                github_output: Path | None = None) -> dict[str, Any]:
    if not source.is_file():
        raise FileNotFoundError(f"source PBF does not exist: {source}")
    if output.absolute() == source.absolute():
        raise ValueError("root-closure output must differ from its source PBF")
    receipt_fields = (scope, source_label, output_artifact, upstream_stage)
    if receipt is not None and any(not value for value in receipt_fields):
        raise ValueError("receipt requires scope, source/output labels, and stage")
    if receipt is None and any(value is not None for value in receipt_fields):
        raise ValueError("provenance fields require --receipt")
    if github_output is not None and receipt is None:
        raise ValueError("GitHub outputs require --receipt")
    roots = _load_roots(roots_from)
    output.parent.mkdir(parents=True, exist_ok=True)
    id_descriptor, id_name = tempfile.mkstemp(
        prefix=".mols-root-ids.", suffix=".txt", dir=output.parent)
    os.close(id_descriptor)
    slice_descriptor, slice_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp.pbf", dir=output.parent)
    os.close(slice_descriptor)
    os.unlink(slice_name)
    id_path = Path(id_name)
    temporary = Path(slice_name)
    try:
        id_path.write_text(
            "".join(f"r{relation_id}\n" for relation_id in roots),
            encoding="ascii")
        command = osmium_command(source, id_path, temporary, executable)
        try:
            completed = runner(
                command, check=False, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True)
        except FileNotFoundError as error:
            raise RuntimeError(
                f"required osmium executable is unavailable: {executable}") from error
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise RuntimeError(
                f"osmium root-closure extraction failed with exit "
                f"{completed.returncode}: {detail or 'no diagnostic'}")
        if not temporary.is_file() or temporary.stat().st_size <= 0:
            raise RuntimeError("osmium root-closure extraction produced no PBF bytes")
        os.replace(temporary, output)
        if receipt is not None:
            version = _run_metadata(runner, [executable, "--version"])
            parent_counts = _object_counts(_run_metadata(
                runner, [executable, "fileinfo", "-e", str(source)]))
            compact_counts = _object_counts(_run_metadata(
                runner, [executable, "fileinfo", "-e", str(output)]))
            receipt_document = {
                "schema_version": 1,
                "receipt_kind": "scope",
                "scope": scope,
                "canonical_source_path_label": source_label,
                "parent_pbf": {**_identity(source),
                               "object_counts": parent_counts},
                "root_relation_ids": roots,
                "root_set_sha256": _canonical_sha256(roots),
                "osmium": {
                    "version": version,
                    "command": command,
                },
                "compact_output": {
                    "artifact": output_artifact,
                    **_identity(output),
                    "object_counts": compact_counts,
                },
                "upstream_workflow_stage": upstream_stage,
            }
            _write_receipt(receipt, receipt_document)
            if github_output is not None:
                emit_github_outputs(
                    receipt_document, receipt, github_output)
    finally:
        try:
            id_path.unlink()
        except FileNotFoundError:
            pass
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return {"root_relation_ids": roots, **_identity(output)}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--in", dest="source", required=True, type=Path)
    parser.add_argument("--roots-from", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--scope", choices=("raw-denmark", "prefiltered-denmark"))
    parser.add_argument("--source-label")
    parser.add_argument("--output-artifact")
    parser.add_argument("--upstream-stage")
    parser.add_argument("--github-output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = build_slice(
            args.source, args.roots_from, args.out,
            receipt=args.receipt, scope=args.scope,
            source_label=args.source_label,
            output_artifact=args.output_artifact,
            upstream_stage=args.upstream_stage,
            github_output=args.github_output)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    print(json.dumps({"output": str(args.out), **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
