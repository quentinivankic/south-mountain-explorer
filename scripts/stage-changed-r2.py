#!/usr/bin/env python3
"""Fetch and consume an authoritative R2 inventory for exact reconciliation.

``fetch-inventory`` performs explicit low-level pagination and writes an atomic
complete-inventory envelope only after every page validates. ``stage`` accepts
only that envelope and requires every local geom and silhouette key to be
present. A missing local key is an incomplete inventory or a bootstrap/new-key
request; both are refused here and require a separate reviewed mechanism.

Changed files are copied into a fresh stage root and recorded in
``upload-manifest.tsv`` with dataset, basename, MD5, and SHA-256. The workflow
uploads only those exact manifest rows.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

INVENTORY_SCHEMA = "trekdex-r2-complete-inventory-v1"
MAX_INVENTORY_PAGES = 100_000
_ETAG_RE = re.compile(r"[0-9a-fA-F]{32}(?:-[1-9][0-9]*)?")


class InventoryError(RuntimeError):
    """The remote inventory is not authoritative enough to allow staging."""


def _strict_json(text: str, context: str) -> Any:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        document: dict[str, Any] = {}
        for key, value in pairs:
            if key in document:
                raise InventoryError(f"{context} contains duplicate JSON key {key!r}")
            document[key] = value
        return document

    try:
        return json.loads(text, object_pairs_hook=reject_duplicates)
    except InventoryError:
        raise
    except (TypeError, ValueError) as error:
        raise InventoryError(f"{context} is not valid JSON: {error}") from error


def _safe_inventory_key(key: Any, context: str) -> str:
    if not isinstance(key, str) or not key or any(char in key for char in "\r\n\t\0"):
        raise InventoryError(f"{context} has an unsafe or empty key")
    return key


def _normalized_etag(value: Any, context: str) -> str:
    if not isinstance(value, str):
        raise InventoryError(f"{context} has a non-string ETag")
    etag = value.strip()
    if len(etag) >= 2 and etag[0] == etag[-1] == '"':
        etag = etag[1:-1]
    if not _ETAG_RE.fullmatch(etag):
        raise InventoryError(f"{context} has an invalid ETag")
    return etag.lower()


def _atomic_write_json(path: Path, document: dict[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        raise InventoryError(f"inventory output must not already exist: {path}")
    if not path.parent.is_dir() or path.parent.is_symlink():
        raise InventoryError(f"inventory output parent is not a regular directory: {path.parent}")
    temporary = path.with_name(f".{path.name}.tmp")
    if temporary.exists() or temporary.is_symlink():
        raise InventoryError(f"inventory temporary path already exists: {temporary}")
    payload = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    with temporary.open("x", encoding="utf-8", newline="") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def fetch_inventory(
    bucket: str,
    endpoint_url: str,
    output: Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, str]:
    """Fetch every page explicitly and publish a complete envelope atomically."""
    if not bucket or any(char in bucket for char in "\r\n\t\0"):
        raise InventoryError("bucket name is empty or unsafe")
    if not endpoint_url:
        raise InventoryError("endpoint URL is empty")

    objects: dict[str, str] = {}
    continuation_token: str | None = None
    seen_tokens: set[str] = set()
    page_count = 0

    while True:
        if page_count >= MAX_INVENTORY_PAGES:
            raise InventoryError("inventory exceeded the maximum page count")
        command = [
            "aws", "s3api", "list-objects-v2", "--no-paginate",
            "--bucket", bucket, "--endpoint-url", endpoint_url, "--output", "json",
        ]
        if continuation_token is not None:
            command += ["--continuation-token", continuation_token]
        completed = runner(command, capture_output=True, text=True, check=True)
        page_number = page_count + 1
        if not completed.stdout.strip():
            raise InventoryError(f"inventory page {page_number} is empty")
        page = _strict_json(completed.stdout, f"inventory page {page_number}")
        if not isinstance(page, dict):
            raise InventoryError(f"inventory page {page_number} is not an object")
        if page.get("Name") != bucket:
            raise InventoryError(f"inventory page {page_number} names the wrong bucket")
        truncated = page.get("IsTruncated")
        if not isinstance(truncated, bool):
            raise InventoryError(f"inventory page {page_number} lacks boolean IsTruncated")
        contents = page.get("Contents", [])
        if not isinstance(contents, list):
            raise InventoryError(f"inventory page {page_number} Contents is not an array")
        key_count = page.get("KeyCount")
        if not isinstance(key_count, int) or isinstance(key_count, bool) or key_count != len(contents):
            raise InventoryError(f"inventory page {page_number} KeyCount mismatch")

        echoed_token = page.get("ContinuationToken")
        if continuation_token is None:
            if echoed_token not in (None, ""):
                raise InventoryError("first inventory page unexpectedly echoes a continuation token")
        elif echoed_token != continuation_token:
            raise InventoryError(f"inventory page {page_number} continuation token mismatch")

        for offset, item in enumerate(contents, start=1):
            context = f"inventory page {page_number} object {offset}"
            if not isinstance(item, dict):
                raise InventoryError(f"{context} is not an object")
            key = _safe_inventory_key(item.get("Key"), context)
            etag = _normalized_etag(item.get("ETag"), context)
            if key in objects:
                raise InventoryError(f"inventory repeats key across pages: {key}")
            objects[key] = etag

        page_count = page_number
        next_token = page.get("NextContinuationToken")
        if truncated:
            if (not isinstance(next_token, str) or not next_token or
                    any(char in next_token for char in "\r\n\t\0")):
                raise InventoryError(f"inventory page {page_number} lacks a valid next token")
            if next_token == continuation_token or next_token in seen_tokens:
                raise InventoryError(f"inventory page {page_number} repeats a pagination token")
            seen_tokens.add(next_token)
            continuation_token = next_token
            continue
        if next_token not in (None, ""):
            raise InventoryError(f"terminal inventory page {page_number} has a next token")
        break

    ordered = [{"key": key, "etag": objects[key]} for key in sorted(objects)]
    envelope = {
        "schema": INVENTORY_SCHEMA,
        "bucket": bucket,
        "complete": True,
        "page_count": page_count,
        "object_count": len(ordered),
        "objects": ordered,
    }
    _atomic_write_json(output, envelope)
    return objects


def load_inventory(path: Path, bucket: str) -> dict[str, str]:
    """Load only a canonical complete-inventory envelope."""
    if path.is_symlink() or not path.is_file():
        raise InventoryError(f"inventory is not a regular file: {path}")
    if path.stat().st_size == 0:
        raise InventoryError(f"inventory is empty: {path}")
    envelope = _strict_json(path.read_text(encoding="utf-8"), "inventory envelope")
    if not isinstance(envelope, dict) or set(envelope) != {
        "schema", "bucket", "complete", "page_count", "object_count", "objects"
    }:
        raise InventoryError("inventory envelope schema is not exact")
    if envelope["schema"] != INVENTORY_SCHEMA or envelope["bucket"] != bucket:
        raise InventoryError("inventory envelope identity mismatch")
    if envelope["complete"] is not True:
        raise InventoryError("inventory envelope is not complete")
    page_count = envelope["page_count"]
    if not isinstance(page_count, int) or isinstance(page_count, bool) or page_count < 1:
        raise InventoryError("inventory envelope page_count is invalid")
    records = envelope["objects"]
    if not isinstance(records, list):
        raise InventoryError("inventory envelope objects is not an array")
    if (not isinstance(envelope["object_count"], int) or
            isinstance(envelope["object_count"], bool) or
            envelope["object_count"] != len(records)):
        raise InventoryError("inventory envelope object_count mismatch")

    etags: dict[str, str] = {}
    previous_key: str | None = None
    for number, record in enumerate(records, start=1):
        context = f"inventory envelope object {number}"
        if not isinstance(record, dict) or set(record) != {"key", "etag"}:
            raise InventoryError(f"{context} schema is not exact")
        key = _safe_inventory_key(record["key"], context)
        etag = _normalized_etag(record["etag"], context)
        if previous_key is not None and key <= previous_key:
            raise InventoryError("inventory envelope keys are duplicate or not canonical-sorted")
        previous_key = key
        etags[key] = etag
    return etags


def file_digest(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dataset_files(path: Path) -> list[Path]:
    """Return regular JSON leaves, rejecting unsafe names and file types."""
    if path.is_symlink() or not path.is_dir():
        raise InventoryError(f"dataset is not a regular directory: {path}")
    files: list[Path] = []
    for entry in sorted(path.iterdir(), key=lambda item: item.name):
        if not entry.name.endswith(".json"):
            continue
        if entry.is_symlink() or not entry.is_file():
            raise InventoryError(f"dataset JSON is not a regular file: {entry}")
        if any(char in entry.name for char in "\r\n\t\0"):
            raise InventoryError(f"dataset JSON has an unsafe name: {entry.name!r}")
        files.append(entry)
    if not files:
        raise InventoryError(f"dataset has no JSON files: {path}")
    return files


def plan_changes(
    etags: dict[str, str],
    plans: tuple[tuple[str, Path, str], ...],
) -> list[tuple[str, Path, str, str]]:
    """Require complete local-key coverage, then compute exact changed records."""
    datasets: list[tuple[str, list[Path], str]] = []
    for dataset, source, prefix in plans:
        files = dataset_files(source)
        missing = sorted(prefix + path.name for path in files if prefix + path.name not in etags)
        if missing:
            preview = ", ".join(missing[:5])
            raise InventoryError(
                f"authoritative inventory is missing {len(missing)}/{len(files)} local "
                f"{dataset} keys ({preview}); reconcile forbids bootstrap/new keys"
            )
        datasets.append((dataset, files, prefix))

    changes: list[tuple[str, Path, str, str]] = []
    for dataset, files, prefix in datasets:
        dataset_changes = 0
        for path in files:
            local_md5 = file_digest(path, "md5")
            if etags[prefix + path.name] != local_md5:
                changes.append((dataset, path, local_md5, file_digest(path, "sha256")))
                dataset_changes += 1
        print(f"{files[0].parent}: {dataset_changes} changed of {len(files)}; "
              f"authoritative inventory coverage {len(files)}/{len(files)}")
    return changes


def stage_changes(changes: list[tuple[str, Path, str, str]], stage_root: Path) -> Path:
    """Copy the approved exact set and atomically publish its local manifest."""
    if stage_root.exists() or stage_root.is_symlink():
        raise InventoryError(f"stage root must not already exist: {stage_root}")
    stage_root.mkdir(mode=0o700, parents=True)
    manifest_lines: list[str] = []
    for dataset, source, expected_md5, expected_sha256 in changes:
        target_dir = stage_root / dataset
        target_dir.mkdir(mode=0o700, exist_ok=True)
        target = target_dir / source.name
        shutil.copyfile(source, target)
        if file_digest(target, "md5") != expected_md5:
            raise InventoryError(f"staged MD5 mismatch: {source.name}")
        if file_digest(target, "sha256") != expected_sha256:
            raise InventoryError(f"staged SHA-256 mismatch: {source.name}")
        manifest_lines.append(
            f"{dataset}\t{source.name}\t{expected_md5}\t{expected_sha256}\n"
        )

    temporary = stage_root / ".upload-manifest.tsv.tmp"
    manifest = stage_root / "upload-manifest.tsv"
    with temporary.open("x", encoding="utf-8", newline="") as stream:
        stream.writelines(manifest_lines)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    fetch = commands.add_parser("fetch-inventory")
    fetch.add_argument("--bucket", required=True)
    fetch.add_argument("--endpoint-url", required=True)
    fetch.add_argument("--output", required=True, type=Path)

    stage = commands.add_parser("stage")
    stage.add_argument("--inventory", required=True, type=Path)
    stage.add_argument("--bucket", required=True)
    stage.add_argument("--stage-root", required=True, type=Path)
    stage.add_argument("--geom-dir", default=Path("public/areas/geom"), type=Path)
    stage.add_argument("--silhouettes-dir", default=Path("public/areas/silhouettes"), type=Path)
    args = parser.parse_args(argv)

    if args.command == "fetch-inventory":
        objects = fetch_inventory(args.bucket, args.endpoint_url, args.output)
        print(f"authoritative inventory complete: {len(objects)} objects -> {args.output}")
        return 0

    etags = load_inventory(args.inventory, args.bucket)
    plans = (
        ("geom", args.geom_dir, ""),
        ("silhouettes", args.silhouettes_dir, "silhouettes/"),
    )
    changes = plan_changes(etags, plans)
    manifest = stage_changes(changes, args.stage_root)
    print(f"exact upload set: {len(changes)} object(s); manifest: {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
