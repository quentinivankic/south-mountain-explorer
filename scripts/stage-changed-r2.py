#!/usr/bin/env python3
"""Build and optionally apply a canonical R2 release contract.

``fetch-inventory`` seals account/endpoint/bucket identity, pagination closure,
every key/preimage, the protected set, denominator, digest, and anchor state.
``stage`` copies the complete local producer set into deterministic paths and
builds one exact byte-different, Mols-scoped conditional manifest.
``apply-manifest`` accepts only the separately reviewed hash, snapshots a
first-class rollback manifest, and emits a typed terminal receipt. The policy
authority is :mod:`r2_release_contract`.
"""
from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, BinaryIO, Callable


_READONLY_GIT_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/tmp",
    "LC_ALL": "C",
    "LANG": "C",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_OPTIONAL_LOCKS": "0",
}


def file_digest(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dataset_files(path: Path) -> list[Path]:
    """Return regular JSON leaves, rejecting unsafe names and file types."""
    contract = _contract_module()
    if path.is_symlink() or not path.is_dir():
        raise contract.ManifestError(f"dataset is not a regular directory: {path}")
    files: list[Path] = []
    for entry in sorted(path.iterdir(), key=lambda item: item.name):
        if not entry.name.endswith(".json"):
            raise contract.ManifestError(f"dataset contains an unclassified output: {entry}")
        if entry.is_symlink() or not entry.is_file():
            raise contract.ManifestError(f"dataset JSON is not a regular file: {entry}")
        if any(char in entry.name for char in "\r\n\t\0"):
            raise contract.ManifestError(f"dataset JSON has an unsafe name: {entry.name!r}")
        files.append(entry)
    if not files:
        raise contract.ManifestError(f"dataset has no JSON files: {path}")
    return files


def _contract_module():
    import r2_release_contract as contract
    return contract


def _read_contract_json(path: Path, context: str) -> dict[str, Any]:
    contract = _contract_module()
    if path.is_symlink() or not path.is_file():
        raise contract.InventoryError(f"{context} is not a regular file: {path}")
    document = contract.strict_json_loads(path.read_text(encoding="utf-8"), context)
    if not isinstance(document, dict):
        raise contract.InventoryError(f"{context} is not an object")
    return document


def _write_contract_json(path: Path, document: dict[str, Any], *, replace: bool = False) -> None:
    contract = _contract_module()
    if path.is_symlink() or (path.exists() and not replace):
        raise contract.InventoryError(f"contract output already exists: {path}")
    if not path.parent.is_dir() or path.parent.is_symlink():
        raise contract.InventoryError(f"contract output parent is unsafe: {path.parent}")
    temporary = path.with_name(f".{path.name}.tmp")
    if temporary.exists() or temporary.is_symlink():
        raise contract.InventoryError(f"contract temporary path exists: {temporary}")
    payload = json.dumps(
        document, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ) + "\n"
    with temporary.open("x", encoding="utf-8", newline="") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _aws_inventory_pager(bucket: str, endpoint_url: str) -> Callable[[str | None], dict[str, Any]]:
    contract = _contract_module()

    def invoke(command: list[str], context: str) -> dict[str, Any]:
        try:
            completed = subprocess.run(
                command, capture_output=True, text=True, check=True, timeout=120
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise contract.InventoryError(f"{context} failed: {type(error).__name__}") from error
        document = contract.strict_json_loads(completed.stdout, context)
        if not isinstance(document, dict):
            raise contract.InventoryError(f"{context} did not return an object")
        return document

    def list_page(token: str | None) -> dict[str, Any]:
        command = [
            "aws", "s3api", "list-objects-v2", "--no-paginate", "--bucket", bucket,
            "--endpoint-url", endpoint_url, "--output", "json",
        ]
        if token is not None:
            command += ["--continuation-token", token]
        page = invoke(command, "inventory list page")
        contents = page.get("Contents", [])
        if not isinstance(contents, list):
            return page
        enriched: list[dict[str, Any]] = []
        for raw in contents:
            if not isinstance(raw, dict) or not isinstance(raw.get("Key"), str):
                enriched.append(raw)
                continue
            head = invoke(
                [
                    "aws", "s3api", "head-object", "--bucket", bucket, "--key", raw["Key"],
                    "--endpoint-url", endpoint_url, "--checksum-mode", "ENABLED", "--output", "json",
                ],
                "inventory object preimage",
            )
            checksum = contract.normalize_sha256_checksum(
                head.get("ChecksumSHA256"), "inventory object provider checksum"
            )
            enriched.append({
                **raw,
                "ETag": head.get("ETag", raw.get("ETag")),
                "Size": head.get("ContentLength", raw.get("Size")),
                "ChecksumSHA256": checksum,
            })
        page["Contents"] = enriched
        return page

    return list_page


def fetch_inventory_v2(args: argparse.Namespace) -> int:
    contract = _contract_module()
    account_id = os.environ.get("R2_ACCOUNT_ID", "")
    expected_endpoint = f"https://{account_id}.r2.cloudflarestorage.com"
    if args.endpoint_url != expected_endpoint:
        raise contract.InventoryError("provider endpoint is not derived from the actual account identity")
    attestation = contract.provider_identity_attestation(account_id, args.endpoint_url)
    contract.validate_provider_identity_attestation(
        attestation,
        expected_account_identity_ref=args.account_identity_ref,
        expected_endpoint_identity_ref=args.endpoint_identity_ref,
    )
    area_ids = contract.load_area_ids(args.area_index, minimum=1)
    receipt = contract.fetch_complete_inventory(
        _aws_inventory_pager(args.bucket, args.endpoint_url),
        bucket=args.bucket,
        account_identity_ref=args.account_identity_ref,
        endpoint_identity_ref=args.endpoint_identity_ref,
        authoritative_denominator=args.authoritative_denominator,
        authoritative_area_ids=area_ids,
        anchor_state="ASSERTED_PENDING_APPROVAL",
        anchor_ref=args.anchor_ref,
    )
    _write_contract_json(args.output, receipt)
    print(
        f"canonical inventory receipt: objects={receipt['object_count']} "
        f"pages={receipt['page_count']} sha256={receipt['receipt_sha256']} "
        "anchor_state=ASSERTED_PENDING_APPROVAL"
    )
    return 0


def _append_github_outputs(path: Path, values: dict[str, str]) -> None:
    contract = _contract_module()
    if path.is_symlink() or not path.is_file():
        raise contract.ManifestError("GitHub output path is missing or unsafe")
    for name, value in values.items():
        if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
            raise contract.ManifestError("GitHub output name is invalid")
        if not isinstance(value, str) or not value or any(char in value for char in "\r\n\0"):
            raise contract.ManifestError(f"GitHub output {name} is invalid")
    with path.open("a", encoding="utf-8", newline="") as stream:
        for name, value in values.items():
            stream.write(f"{name}={value}\n")
        stream.flush()
        os.fsync(stream.fileno())


def verify_release_input_v2(args: argparse.Namespace) -> int:
    """Verify one signed exact-Mols receipt before any publication effect."""
    contract = _contract_module()
    encoded = os.environ.get("MOLS_RELEASE_INPUT_RECEIPT_B64", "")
    if not encoded:
        if args.state == contract.MOLS_STATE or args.region_code == contract.MOLS_REGION_CODE:
            raise contract.ManifestError(
                "Denmark publication requires a signed Mols release-input receipt"
            )
        _append_github_outputs(args.github_output, {"release_mode": "generic"})
        print("generic state publication mode: no Mols release receipt supplied")
        return 0
    receipt = contract.decode_mols_release_input_receipt(
        encoded, key=os.environ.get("R2_ROOT_APPROVAL_HMAC_KEY", "")
    )
    expected_inputs = {
        "extract": args.extract,
        "state": args.state,
        "region_code": args.region_code,
        "bbox": args.bbox,
        "workflow_source_sha": args.workflow_source_sha,
        "workflow_source_tree": args.workflow_source_tree,
    }
    for field, actual in expected_inputs.items():
        if actual != receipt[field]:
            raise contract.ManifestError(
                f"workflow input {field} differs from the signed Mols release receipt"
            )
    if (
        args.dry_run != "false"
        or args.no_routes != "false"
        or args.touch_report != "false"
        or args.elevation != "false"
        or args.pilot_area_id
    ):
        raise contract.ManifestError(
            "signed Mols publication requires dry_run=false, no_routes=false, "
            "touch_report=false, elevation=false, and no read-only pilot selector"
        )
    publication_row = [
        receipt["area_id"], receipt["area_name"], receipt["state"],
        receipt["center_lat"], receipt["center_lon"], None, None,
        receipt["osm_relation_id"],
    ]
    _write_contract_json(args.mols_index_output, [publication_row])
    outputs = {
        "release_mode": "mols",
        "receipt_sha256": receipt["receipt_sha256"],
        "extract": receipt["extract"],
        "state": receipt["state"],
        "region_code": receipt["region_code"],
        "area_id": receipt["area_id"],
        "area_name": receipt["area_name"],
        "osm_relation_id": str(receipt["osm_relation_id"]),
        "bbox": receipt["bbox"],
        "center_lat": str(receipt["center_lat"]),
        "center_lon": str(receipt["center_lon"]),
        "publication_index_path": str(args.mols_index_output),
        "root_approval_ref": receipt["root_approval_ref"],
        "account_identity_ref": receipt["account_identity_ref"],
        "endpoint_identity_ref": receipt["endpoint_identity_ref"],
        "inventory_anchor_ref": receipt["inventory_anchor_ref"],
        "authoritative_denominator": str(receipt["authoritative_denominator"]),
        "workflow_source_sha": receipt["workflow_source_sha"],
        "workflow_source_tree": receipt["workflow_source_tree"],
    }
    _append_github_outputs(args.github_output, outputs)
    print(
        "verified signed exact Mols release-input receipt "
        f"sha256={receipt['receipt_sha256']}"
    )
    return 0


def _write_default_json_atomic(path: Path, document: Any) -> None:
    contract = _contract_module()
    if path.is_symlink() or not path.is_file() or path.parent.is_symlink():
        raise contract.ManifestError("canonical index target is missing or unsafe")
    temporary = path.with_name(f".{path.name}.tmp")
    if temporary.exists() or temporary.is_symlink():
        raise contract.ManifestError("canonical index temporary path already exists")
    payload = json.dumps(
        document, ensure_ascii=True, allow_nan=False
    ).encode("utf-8")
    with temporary.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _verified_release_input_digest(value: Any, context: str) -> str:
    contract = _contract_module()
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise contract.ManifestError(
            f"{context} lacks the verified release-input receipt digest"
        )
    return value


def merge_mols_index_v2(args: argparse.Namespace) -> int:
    """Merge one verified, published Mols row into the canonical master index."""
    contract = _contract_module()
    release_input_digest = _verified_release_input_digest(
        args.expected_receipt_sha256, "Mols index merge"
    )
    source = args.source.resolve()
    target = args.target.resolve()
    expected_target = (Path.cwd() / "public/areas/index.json").resolve()
    if (
        args.source.is_symlink() or not source.is_file()
        or args.target.is_symlink() or not target.is_file()
        or target != expected_target
        or source == target
    ):
        raise contract.ManifestError("Mols index merge paths are unsafe")
    try:
        source_rows = contract.strict_json_loads(
            source.read_text(encoding="utf-8"), "published one-row Mols index"
        )
        target_rows = contract.strict_json_loads(
            target.read_text(encoding="utf-8"), "canonical master index"
        )
    except contract.InventoryError as error:
        raise contract.ManifestError(str(error)) from error
    if not isinstance(source_rows, list) or len(source_rows) != 1:
        raise contract.ManifestError("published Mols index is not exactly one row")
    row = source_rows[0]
    if (
        not isinstance(row, list) or len(row) < 8
        or row[0] != contract.MOLS_AREA_ID
        or row[1] != contract.MOLS_AREA_NAME
        or row[2] != contract.MOLS_STATE
        or row[3] != contract.MOLS_CENTER_LAT
        or row[4] != contract.MOLS_CENTER_LON
        or not isinstance(row[5], int) or isinstance(row[5], bool) or row[5] <= 0
        or not isinstance(row[6], (int, float)) or isinstance(row[6], bool) or row[6] <= 0
        or row[7] != contract.MOLS_RELATION_ID
    ):
        raise contract.ManifestError("published Mols row differs from signed identity or has no trails")
    if not isinstance(target_rows, list) or any(
        not isinstance(item, list) or not item or not isinstance(item[0], str)
        for item in target_rows
    ):
        raise contract.ManifestError("canonical master index rows are invalid")
    ids = [item[0] for item in target_rows]
    if len(ids) != len(set(ids)):
        raise contract.ManifestError("canonical master index has duplicate IDs")
    before_other = [item for item in target_rows if item[0] != contract.MOLS_AREA_ID]
    existing = [item for item in target_rows if item[0] == contract.MOLS_AREA_ID]
    if len(existing) > 1 or existing == [row]:
        raise contract.ManifestError("canonical master index Mols row is duplicate or unchanged")
    if existing:
        candidate = [
            row if item[0] == contract.MOLS_AREA_ID else item
            for item in target_rows
        ]
    else:
        candidate = [*target_rows, row]
    if [item for item in candidate if item[0] != contract.MOLS_AREA_ID] != before_other:
        raise contract.ManifestError("Mols insertion would reorder non-Mols rows")
    _write_default_json_atomic(target, candidate)
    print(
        "merged exact verified Mols row into canonical master index "
        f"receipt={release_input_digest}"
    )
    return 0


def _git_bytes(repo: Path, *arguments: str, check: bool = True) -> bytes:
    contract = _contract_module()
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=repo,
            env=_READONLY_GIT_ENV,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise contract.ManifestError(
            f"staged-diff git probe failed: {type(error).__name__}"
        ) from error
    if check and completed.returncode != 0:
        raise contract.ManifestError("staged-diff git probe returned nonzero")
    return completed.stdout


def _strict_repository_index(
    payload: bytes, *, context: str, compact: bool
) -> list[list[Any]]:
    contract = _contract_module()
    try:
        document = contract.strict_json_loads(payload.decode("utf-8"), context)
    except (UnicodeDecodeError, contract.InventoryError) as error:
        raise contract.ManifestError(f"{context} is invalid: {error}") from error
    if not isinstance(document, list):
        raise contract.ManifestError(f"{context} is not an array")
    expected = json.dumps(
        document,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":") if compact else None,
    ).encode("utf-8")
    if payload != expected:
        raise contract.ManifestError(f"{context} byte framing differs from its producer")
    rows: list[list[Any]] = []
    seen: set[str] = set()
    for number, row in enumerate(document, start=1):
        if (
            not isinstance(row, list)
            or len(row) < 5
            or not isinstance(row[0], str)
            or row[0] in seen
        ):
            raise contract.ManifestError(f"{context} row {number} is invalid")
        seen.add(row[0])
        rows.append(row)
    return rows


def _verify_mols_index_transition(
    baseline: bytes, candidate: bytes, *, context: str, compact: bool
) -> dict[str, Any]:
    contract = _contract_module()
    before = _strict_repository_index(baseline, context=f"baseline {context}", compact=compact)
    after = _strict_repository_index(candidate, context=f"candidate {context}", compact=compact)
    before_target = [row for row in before if row[0] == contract.MOLS_AREA_ID]
    after_target = [row for row in after if row[0] == contract.MOLS_AREA_ID]
    before_other = [row for row in before if row[0] != contract.MOLS_AREA_ID]
    after_other = [row for row in after if row[0] != contract.MOLS_AREA_ID]
    if before_other != after_other:
        raise contract.ManifestError(f"{context} changes a non-Mols row or row order")
    if len(before_target) > 1 or len(after_target) != 1 or before_target == after_target:
        raise contract.ManifestError(f"{context} does not contain one genuinely changed Mols row")
    row = after_target[0]
    if (
        len(row) < 8
        or row[0] != contract.MOLS_AREA_ID
        or row[1] != contract.MOLS_AREA_NAME
        or row[2] != contract.MOLS_STATE
        or row[7] != contract.MOLS_RELATION_ID
    ):
        raise contract.ManifestError(f"{context} Mols row has the wrong exact identity")
    receipt: dict[str, Any] = {
        "context": context,
        "baseline_sha256": hashlib.sha256(baseline).hexdigest(),
        "candidate_sha256": hashlib.sha256(candidate).hexdigest(),
        "non_mols_rows_sha256": contract.canonical_sha256(before_other),
        "non_mols_rows_unchanged": True,
        "mols_row": row,
    }
    receipt["receipt_sha256"] = contract.canonical_sha256(receipt)
    return receipt


def verify_mols_staged_diff_v2(args: argparse.Namespace) -> int:
    """Refuse commit/push unless the staged tree is the exact Mols delta."""
    contract = _contract_module()
    release_input_digest = _verified_release_input_digest(
        args.expected_receipt_sha256, "staged diff"
    )
    repo = args.repo.resolve()
    if not (repo / ".git").is_dir() or repo.is_symlink():
        raise contract.ManifestError("staged-diff repository path is invalid")
    names = _git_bytes(
        repo, "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"
    ).decode("utf-8").split("\0")
    names = [name for name in names if name]
    deleted = _git_bytes(
        repo, "diff", "--cached", "--name-only", "--diff-filter=D", "-z"
    )
    if deleted:
        raise contract.ManifestError("Mols staged diff contains a deletion")
    if names != sorted(contract.MOLS_REPOSITORY_PATHS):
        raise contract.ManifestError(
            f"Mols staged diff is not the exact allowlist: {names}"
        )
    geom = contract.strict_json_loads(
        _git_bytes(repo, "show", f":{contract.MOLS_REPOSITORY_PATHS[0]}").decode("utf-8"),
        "staged Mols geom",
    )
    if not isinstance(geom, dict) or geom.get("id") != contract.MOLS_AREA_ID:
        raise contract.ManifestError("staged Mols geom has the wrong area identity")
    silhouette = contract.strict_json_loads(
        _git_bytes(repo, "show", f":{contract.MOLS_REPOSITORY_PATHS[1]}").decode("utf-8"),
        "staged Mols silhouette",
    )
    if (
        not isinstance(silhouette, dict)
        or set(silhouette) != {"b", "l"}
        or not isinstance(silhouette["b"], list)
        or len(silhouette["b"]) != 4
        or not isinstance(silhouette["l"], list)
        or not silhouette["l"]
    ):
        raise contract.ManifestError("staged Mols silhouette is malformed")
    transitions = []
    for path, compact in (
        ("public/areas/index.json", False),
        ("ios/SouthMountainExplorer/Resources/areas-index.json", True),
    ):
        baseline = _git_bytes(repo, "show", f"HEAD:{path}")
        candidate = _git_bytes(repo, "show", f":{path}")
        transitions.append(
            _verify_mols_index_transition(
                baseline, candidate, context=path, compact=compact
            )
        )
    summary: dict[str, Any] = {
        "schema": "trekdex-mols-staged-diff-v1",
        "release_input_receipt_sha256": release_input_digest,
        "allowed_paths": list(contract.MOLS_REPOSITORY_PATHS),
        "index_transitions": transitions,
    }
    summary["receipt_sha256"] = contract.canonical_sha256(summary)
    _write_contract_json(args.output, summary)
    print(
        "verified exact Mols staged diff before commit/push "
        f"sha256={summary['receipt_sha256']}"
    )
    return 0


def verify_mols_commit_v2(args: argparse.Namespace) -> int:
    """Recheck the committed Mols delta after hooks and before push."""
    contract = _contract_module()
    release_input_digest = _verified_release_input_digest(
        args.expected_receipt_sha256, "commit verification"
    )
    repo = args.repo.resolve()
    if not (repo / ".git").is_dir() or repo.is_symlink():
        raise contract.ManifestError("commit verification repository path is invalid")
    names = _git_bytes(
        repo, "diff", "--name-only", "--diff-filter=ACMR", "-z", "HEAD^", "HEAD"
    ).decode("utf-8").split("\0")
    names = [name for name in names if name]
    deleted = _git_bytes(
        repo, "diff", "--name-only", "--diff-filter=D", "-z", "HEAD^", "HEAD"
    )
    if deleted or names != sorted(contract.MOLS_REPOSITORY_PATHS):
        raise contract.ManifestError("committed Mols delta is not the exact allowlist")
    geom = contract.strict_json_loads(
        _git_bytes(repo, "show", f"HEAD:{contract.MOLS_REPOSITORY_PATHS[0]}").decode("utf-8"),
        "committed Mols geom",
    )
    if not isinstance(geom, dict) or geom.get("id") != contract.MOLS_AREA_ID:
        raise contract.ManifestError("committed Mols geom has the wrong identity")
    silhouette = contract.strict_json_loads(
        _git_bytes(repo, "show", f"HEAD:{contract.MOLS_REPOSITORY_PATHS[1]}").decode("utf-8"),
        "committed Mols silhouette",
    )
    if (
        not isinstance(silhouette, dict)
        or set(silhouette) != {"b", "l"}
        or not isinstance(silhouette.get("l"), list)
        or not silhouette["l"]
    ):
        raise contract.ManifestError("committed Mols silhouette is malformed")
    transitions = [
        _verify_mols_index_transition(
            _git_bytes(repo, "show", f"HEAD^:{path}"),
            _git_bytes(repo, "show", f"HEAD:{path}"),
            context=path,
            compact=compact,
        )
        for path, compact in (
            ("public/areas/index.json", False),
            ("ios/SouthMountainExplorer/Resources/areas-index.json", True),
        )
    ]
    summary: dict[str, Any] = {
        "schema": "trekdex-mols-commit-diff-v1",
        "release_input_receipt_sha256": release_input_digest,
        "commit_sha": _git_bytes(repo, "rev-parse", "HEAD").decode().strip(),
        "parent_sha": _git_bytes(repo, "rev-parse", "HEAD^").decode().strip(),
        "allowed_paths": list(contract.MOLS_REPOSITORY_PATHS),
        "index_transitions": transitions,
    }
    if not re.fullmatch(r"[0-9a-f]{40}", summary["commit_sha"]) or not re.fullmatch(
        r"[0-9a-f]{40}", summary["parent_sha"]
    ):
        raise contract.ManifestError("committed Mols Git identity is invalid")
    summary["receipt_sha256"] = contract.canonical_sha256(summary)
    _write_contract_json(args.output, summary)
    print(
        "verified exact Mols commit before push "
        f"sha256={summary['receipt_sha256']}"
    )
    return 0


def _parse_global_objects(values: list[str]) -> dict[str, Path]:
    contract = _contract_module()
    parsed: dict[str, Path] = {}
    for value in values:
        key, separator, path = value.partition("=")
        if not separator or key in parsed:
            raise contract.ManifestError("global object arguments must be unique KEY=PATH pairs")
        parsed[key] = Path(path)
    if set(parsed) != set(contract.GLOBAL_OBJECTS):
        raise contract.ManifestError("global object set must be exactly the four contract keys")
    return parsed


def _derive_producer_population(
    args: argparse.Namespace,
    inventory: dict[str, Any],
) -> tuple[list[tuple[str, Path, str]], list[dict[str, Any]], dict[str, Any]]:
    contract = _contract_module()
    globals_by_key = _parse_global_objects(args.global_object)
    registry = contract.inventory_area_ids(inventory)
    if registry != frozenset(contract.load_area_ids(args.area_index, minimum=1)):
        raise contract.ManifestError("local area registry differs from the reviewed inventory registry")
    published = frozenset(contract.load_area_ids(globals_by_key["index.json"], minimum=1))
    if not published.issubset(registry):
        raise contract.ManifestError("published index contains IDs outside the authoritative registry")
    geom_paths = {path.stem: path for path in dataset_files(args.geom_dir)}
    silhouette_paths = {path.stem: path for path in dataset_files(args.silhouettes_dir)}
    for area_id, path in [*geom_paths.items(), *silhouette_paths.items()]:
        key = path.name if path.parent == args.geom_dir else f"silhouettes/{path.name}"
        if contract.classify_key(key, authoritative_area_ids=registry) is contract.KeyClass.UNKNOWN:
            raise contract.ManifestError(f"local producer key is outside the release contract: {key!r}")
    if set(geom_paths) != set(published):
        missing = sorted(set(published) - set(geom_paths))
        extra = sorted(set(geom_paths) - set(published))
        raise contract.ManifestError(
            f"geom producer set differs from published index: missing={missing[:12]} extra={extra[:12]}"
        )
    if not set(published).issubset(silhouette_paths):
        missing = sorted(set(published) - set(silhouette_paths))
        raise contract.ManifestError(f"published silhouettes are missing: {missing[:12]}")
    sources: list[tuple[str, Path, str]] = []
    for area_id in sorted(published):
        geom_key = f"{area_id}.json"
        silhouette_key = f"silhouettes/{area_id}.json"
        sources.append((
            geom_key, geom_paths[area_id],
            f"objects/{geom_key.encode('utf-8').hex()}.object",
        ))
        sources.append((
            silhouette_key, silhouette_paths[area_id],
            f"objects/{silhouette_key.encode('utf-8').hex()}.object",
        ))
    for key in contract.GLOBAL_OBJECTS:
        sources.append((key, globals_by_key[key], f"objects/{key.encode('utf-8').hex()}.object"))
    local_rows = [
        contract.local_object(
            key, source, logical_path=logical, authoritative_area_ids=registry
        )
        for key, source, logical in sorted(sources)
    ]
    population = contract.build_producer_population(
        local_rows,
        source_tree=args.source_tree,
        authoritative_area_ids=registry,
        published_area_ids=published,
    )
    return sorted(sources), local_rows, population


def _copy_exact_source(source: Path, target: Path, expected: dict[str, Any]) -> None:
    contract = _contract_module()
    if source.is_symlink() or not source.is_file():
        raise contract.ManifestError(f"producer source is missing or unsafe: {source}")
    with source.open("rb") as reader, target.open("xb") as writer:
        digest = hashlib.sha256()
        byte_count = 0
        for chunk in iter(lambda: reader.read(1 << 20), b""):
            digest.update(chunk)
            byte_count += len(chunk)
            writer.write(chunk)
        writer.flush()
        os.fsync(writer.fileno())
    if byte_count != expected["bytes"] or digest.hexdigest() != expected["sha256"]:
        raise contract.ManifestError("producer source changed after population review")


def _build_scoped_semantic_deltas(
    args: argparse.Namespace,
    inventory: dict[str, Any],
    sources: list[tuple[str, Path, str]],
    local_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    contract = _contract_module()
    publication_area_id = getattr(args, "publication_area_id", None)
    if publication_area_id is None:
        return []
    if publication_area_id != contract.MOLS_AREA_ID:
        raise contract.ManifestError("semantic baseline reads are restricted to exact Mols scope")
    endpoint_url = getattr(args, "endpoint_url", None)
    aws_cli_version = getattr(args, "aws_cli_version", None)
    aws_cli_archive_sha256 = getattr(args, "aws_cli_archive_sha256", None)
    if not all(isinstance(value, str) and value for value in (
        endpoint_url, aws_cli_version, aws_cli_archive_sha256
    )):
        raise contract.ManifestError("scoped plan lacks pinned provider read identity")
    reader = MolsConditionalWriter(
        endpoint_url=endpoint_url,
        account_id=os.environ.get("R2_ACCOUNT_ID", ""),
        manifest_root=Path.cwd(),
        aws_cli_version=aws_cli_version,
        aws_cli_archive_sha256=aws_cli_archive_sha256,
    )
    contract.validate_provider_identity_attestation(
        reader.attest_identity(),
        expected_account_identity_ref=inventory["account_identity_ref"],
        expected_endpoint_identity_ref=inventory["endpoint_identity_ref"],
    )
    contract.validate_provider_tool_attestation(reader.tool_attestation())
    source_by_key = {key: source for key, source, _logical in sources}
    row_by_key = {row["key"]: row for row in local_rows}
    remote = contract.inventory_objects(inventory)
    geom_path = source_by_key.get(contract.MOLS_OBJECT_KEYS[0])
    if geom_path is None or geom_path.is_symlink() or not geom_path.is_file():
        raise contract.ManifestError("scoped plan lacks exact Mols geometry source")
    mols_geom = geom_path.read_bytes()
    deltas: list[dict[str, Any]] = []
    for key in contract.MOLS_SEMANTIC_GLOBAL_KEYS:
        local = row_by_key[key]
        preimage = remote[key]
        if (
            preimage["bytes"] == local["bytes"]
            and preimage["sha256"] == local["sha256"]
        ):
            continue
        baseline = reader.read_reviewed_baseline_bytes(key, preimage)
        candidate = source_by_key[key].read_bytes()
        deltas.append(
            contract.build_mols_global_delta(
                key,
                baseline,
                candidate,
                mols_geom,
                baseline_etag=preimage["etag"],
            )
        )
    return deltas


def stage_v2(args: argparse.Namespace) -> int:
    contract = _contract_module()
    inventory = _read_contract_json(args.inventory, "inventory receipt")
    contract.validate_inventory_receipt(inventory, expected_bucket=args.bucket)
    if args.stage_root.exists() or args.stage_root.is_symlink():
        raise contract.ManifestError(f"stage root must not already exist: {args.stage_root}")
    sources, local_rows, population = _derive_producer_population(args, inventory)
    semantic_deltas = _build_scoped_semantic_deltas(
        args, inventory, sources, local_rows
    )
    manifest = contract.build_upload_manifest(
        inventory,
        local_rows,
        population,
        source_sha=args.source_sha,
        source_tree=args.source_tree,
        workflow_sha256=args.workflow_sha256,
        root_approval_ref=args.root_approval_ref,
        prior_anchor_ref=args.anchor_ref,
        publication_area_id=getattr(args, "publication_area_id", None),
        semantic_deltas=semantic_deltas,
    )
    if args.approved_manifest_sha256 and manifest["manifest_sha256"] != args.approved_manifest_sha256:
        raise contract.ManifestError("computed manifest does not match the exact reviewed manifest")

    objects_root = args.stage_root / "objects"
    objects_root.mkdir(mode=0o700, parents=True)
    rows_by_key = {row["key"]: row for row in population["objects"]}
    for key, source, logical in sources:
        _copy_exact_source(source, args.stage_root / logical, rows_by_key[key])
    manifest_path = args.stage_root / "upload-manifest.json"
    _write_contract_json(manifest_path, manifest)
    print(
        f"exact conditional upload manifest: objects={manifest['object_count']} "
        f"producer_population={population['object_count']} "
        f"sha256={manifest['manifest_sha256']} path={manifest_path}"
    )
    return 0


class AwsConditionalWriter:
    """Provider identity base for the scoped conditional writer."""

    def __init__(self, *, endpoint_url: str, account_id: str, manifest_root: Path):
        self.endpoint_url = endpoint_url
        self.account_id = account_id
        self.manifest_root = manifest_root.resolve()

    def attest_identity(self) -> dict[str, Any]:
        return _contract_module().provider_identity_attestation(
            self.account_id, self.endpoint_url
        )



class MolsConditionalWriter(AwsConditionalWriter):
    """Version-free exact Mols adapter serialized by the workflow lock."""

    def __init__(
        self,
        *,
        endpoint_url: str,
        account_id: str,
        manifest_root: Path,
        aws_cli_version: str,
        aws_cli_archive_sha256: str,
    ):
        super().__init__(
            endpoint_url=endpoint_url,
            account_id=account_id,
            manifest_root=manifest_root,
        )
        self.aws_cli_version = aws_cli_version
        self.aws_cli_archive_sha256 = aws_cli_archive_sha256

    def tool_attestation(self) -> dict[str, Any]:
        contract = _contract_module()
        try:
            completed = subprocess.run(
                ["aws", "--version"],
                env={
                    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                    "HOME": "/tmp",
                    "LC_ALL": "C",
                    "LANG": "C",
                },
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise contract.ManifestError("pinned AWS CLI executable is unavailable") from error
        rendered = (completed.stdout + completed.stderr).strip()
        if completed.returncode != 0 or not rendered.startswith(
            f"aws-cli/{self.aws_cli_version} "
        ):
            raise contract.ManifestError("active AWS CLI does not match the pinned version")
        return contract.provider_tool_attestation(
            version=self.aws_cli_version,
            archive_sha256=self.aws_cli_archive_sha256,
        )

    def _run_provider(
        self, command: list[str], context: str, *, timeout: int = 180
    ) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                command, capture_output=True, text=True, timeout=timeout
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise _contract_module().ManifestError(
                f"{context} outcome is indeterminate: {type(error).__name__}"
            ) from error

    @staticmethod
    def _missing(completed: subprocess.CompletedProcess[str]) -> bool:
        return completed.returncode != 0 and re.search(
            r"(?:\(404\)|\bNoSuchKey\b|\bNot Found\b)", completed.stderr
        ) is not None

    @staticmethod
    def _precondition_refused(completed: subprocess.CompletedProcess[str]) -> bool:
        return completed.returncode != 0 and re.search(
            r"(?:\(412\)|\bPreconditionFailed\b|\bPrecondition Failed\b)",
            completed.stderr,
        ) is not None

    def _head_document(
        self, key: str, *, missing_ok: bool = False
    ) -> dict[str, Any] | None:
        contract = _contract_module()
        completed = self._run_provider(
            [
                "aws", "s3api", "head-object", "--bucket", contract.BUCKET,
                "--key", key, "--endpoint-url", self.endpoint_url,
                "--checksum-mode", "ENABLED", "--output", "json",
            ],
            "provider HEAD",
            timeout=120,
        )
        if completed.returncode != 0:
            if missing_ok and self._missing(completed):
                return None
            raise contract.ManifestError("provider HEAD did not prove exact object state")
        document = contract.strict_json_loads(completed.stdout or "{}", "provider HEAD")
        if not isinstance(document, dict):
            raise contract.ManifestError("provider HEAD is not an object")
        return document

    def _head_identity(
        self, key: str, *, missing_ok: bool = False
    ) -> tuple[dict[str, Any] | None, dict[str, str] | None]:
        contract = _contract_module()
        document = self._head_document(key, missing_ok=missing_ok)
        if document is None:
            return None, None
        try:
            etag = contract._normalize_etag(document.get("ETag"), "provider HEAD")
        except contract.InventoryError as error:
            raise contract.ManifestError("provider HEAD ETag is malformed") from error
        byte_count = document.get("ContentLength")
        if (
            not isinstance(byte_count, int)
            or isinstance(byte_count, bool)
            or byte_count < 0
        ):
            raise contract.ManifestError("provider HEAD byte count is malformed")
        checksum = None
        if document.get("ChecksumSHA256") is not None:
            try:
                checksum = contract.normalize_sha256_checksum(
                    document["ChecksumSHA256"], "provider HEAD"
                )
            except contract.InventoryError as error:
                raise contract.ManifestError("provider HEAD checksum is malformed") from error
        metadata_value = document.get("Metadata")
        metadata = None
        if metadata_value is not None:
            if not isinstance(metadata_value, dict) or any(
                not isinstance(name, str) or not isinstance(value, str)
                for name, value in metadata_value.items()
            ):
                raise contract.ManifestError("provider HEAD metadata is malformed")
            metadata = {
                name.lower(): value for name, value in metadata_value.items()
            }
        return {"etag": etag, "bytes": byte_count, "sha256": checksum}, metadata

    def _download_exact(
        self,
        key: str,
        *,
        etag: str,
        output: Path,
        context: str,
    ) -> dict[str, Any]:
        contract = _contract_module()
        if output.exists() or output.is_symlink():
            raise contract.ManifestError(f"{context} output already exists")
        completed = self._run_provider(
            [
                "aws", "s3api", "get-object", "--bucket", contract.BUCKET,
                "--key", key, "--if-match", etag,
                "--endpoint-url", self.endpoint_url, "--checksum-mode", "ENABLED",
                "--output", "json", str(output),
            ],
            context,
        )
        if completed.returncode != 0:
            raise contract.ManifestError(f"{context} did not return exact bytes")
        document = contract.strict_json_loads(completed.stdout or "{}", context)
        if not isinstance(document, dict) or not output.is_file() or output.is_symlink():
            raise contract.ManifestError(f"{context} response/output is invalid")
        return document

    def _read_current_exact(
        self, key: str, *, missing_ok: bool
    ) -> tuple[dict[str, Any] | None, dict[str, str] | None]:
        contract = _contract_module()
        head, metadata = self._head_identity(key, missing_ok=missing_ok)
        if head is None:
            return None, None
        with tempfile.TemporaryDirectory(prefix="trekdex-r2-read-") as directory:
            output = Path(directory) / "object"
            self._download_exact(
                key,
                etag=head["etag"],
                output=output,
                context="conditional exact object GET",
            )
            digest = file_digest(output, "sha256")
            byte_count = output.stat().st_size
        if byte_count != head["bytes"]:
            raise contract.ManifestError("provider HEAD/GET byte count mismatch")
        if head["sha256"] is not None and head["sha256"] != digest:
            raise contract.ManifestError("provider HEAD/GET SHA-256 mismatch")
        return {
            "etag": head["etag"], "bytes": byte_count, "sha256": digest
        }, metadata

    def read_reviewed_baseline_bytes(
        self, key: str, expected_preimage: dict[str, Any]
    ) -> bytes:
        contract = _contract_module()
        if key not in contract.MOLS_SEMANTIC_GLOBAL_KEYS:
            raise contract.ManifestError("reviewed baseline read exceeds Mols semantic globals")
        try:
            expected = contract._normalize_preimage(
                expected_preimage, f"{key} reviewed baseline"
            )
        except contract.InventoryError as error:
            raise contract.ManifestError("reviewed baseline identity is invalid") from error
        head, _metadata = self._head_identity(key, missing_ok=False)
        assert head is not None
        if head["etag"] != expected["etag"] or head["bytes"] != expected["bytes"]:
            raise contract.ConditionalRequestRefused(
                "reviewed semantic baseline changed before conditional GET"
            )
        with tempfile.TemporaryDirectory(prefix="trekdex-r2-semantic-baseline-") as directory:
            output = Path(directory) / "object"
            self._download_exact(
                key,
                etag=expected["etag"],
                output=output,
                context="reviewed semantic baseline GET",
            )
            payload = output.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        if (
            len(payload) != expected["bytes"]
            or digest != expected["sha256"]
            or (head["sha256"] is not None and head["sha256"] != digest)
        ):
            raise contract.ManifestError(
                "reviewed semantic baseline GET differs from inventory identity"
            )
        return payload

    @staticmethod
    def _safe_relative(root: Path, value: str) -> Path:
        contract = _contract_module()
        relative = Path(value)
        target = (root / relative).resolve()
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or root not in target.parents
            or target.is_symlink()
        ):
            raise contract.ManifestError("contract local path escapes its staging root")
        return target

    def _snapshot_source(self, row: dict[str, Any]) -> BinaryIO:
        contract = _contract_module()
        source = self._safe_relative(self.manifest_root, row["local_path"])
        if not source.is_file():
            raise contract.ManifestError("manifest local object is missing")
        snapshot = tempfile.TemporaryFile(mode="w+b")
        digest = hashlib.sha256()
        md5 = hashlib.md5(usedforsecurity=False)
        byte_count = 0
        try:
            with source.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1 << 20), b""):
                    snapshot.write(chunk)
                    digest.update(chunk)
                    md5.update(chunk)
                    byte_count += len(chunk)
            snapshot.flush()
            os.fsync(snapshot.fileno())
            if (
                byte_count != row["bytes"]
                or digest.hexdigest() != row["sha256"]
                or md5.hexdigest() != row["md5"]
            ):
                raise contract.ManifestError("staged object changed after manifest review")
            snapshot.seek(0)
            return snapshot
        except Exception:
            snapshot.close()
            raise

    @staticmethod
    def _metadata(
        *, source_row: dict[str, Any], source_sha: str,
        manifest_sha256: str, attempt_id: str,
        condition_mode: str, preimage: dict[str, Any] | None,
    ) -> dict[str, str]:
        contract = _contract_module()
        return {
            "trekdex-sha256": source_row["sha256"],
            "trekdex-source-sha": source_sha,
            "trekdex-manifest-sha256": manifest_sha256,
            "trekdex-attempt-id": attempt_id,
            "trekdex-preimage-ref": contract.canonical_sha256(
                preimage if preimage is not None else {"state": "ABSENT"}
            ),
            "trekdex-condition-mode": condition_mode,
        }

    def _put_exact(
        self,
        *,
        key: str,
        source_row: dict[str, Any],
        condition: tuple[str, str],
        condition_mode: str,
        preimage: dict[str, Any] | None,
        attempt_id: str,
        source_sha: str,
        manifest_sha256: str,
        transaction_phase: str,
        transaction_operation: str,
        transaction_index: int,
    ) -> dict[str, Any]:
        contract = _contract_module()
        if condition_mode == "IF_MATCH":
            if (
                not isinstance(preimage, dict)
                or set(preimage) != {"etag", "bytes", "sha256"}
                or condition != ("--if-match", preimage["etag"])
            ):
                raise contract.ManifestError("IF_MATCH request is not bound to exact preimage")
        elif condition_mode == "IF_NONE_MATCH":
            if preimage is not None or condition != ("--if-none-match", "*"):
                raise contract.ManifestError("IF_NONE_MATCH is not bound to reviewed absence")
        else:
            raise contract.ManifestError("conditional PUT mode is invalid")
        metadata = self._metadata(
            source_row=source_row,
            source_sha=source_sha,
            manifest_sha256=manifest_sha256,
            attempt_id=attempt_id,
            condition_mode=condition_mode,
            preimage=preimage,
        )
        snapshot = self._snapshot_source(source_row)
        command = [
            "aws", "s3api", "put-object", "--bucket", contract.BUCKET,
            "--key", key, "--body", f"/dev/fd/{snapshot.fileno()}",
            "--endpoint-url", self.endpoint_url, "--content-type", "application/json",
            "--content-length", str(source_row["bytes"]),
            "--content-md5", base64.b64encode(
                bytes.fromhex(source_row["md5"])
            ).decode("ascii"),
            "--checksum-algorithm", "SHA256",
            "--checksum-sha256", base64.b64encode(
                bytes.fromhex(source_row["sha256"])
            ).decode("ascii"),
            "--metadata", json.dumps(metadata, sort_keys=True, separators=(",", ":")),
            condition[0], condition[1], "--output", "json",
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=180,
                pass_fds=(snapshot.fileno(),),
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise contract.ManifestError(
                f"conditional PUT outcome is indeterminate: {type(error).__name__}"
            ) from error
        finally:
            snapshot.close()
        if completed.returncode != 0:
            if self._precondition_refused(completed):
                raise contract.ConditionalRequestRefused(
                    "provider atomically refused conditional PUT"
                )
            raise contract.ManifestError("conditional PUT outcome is indeterminate")
        try:
            document = contract.strict_json_loads(
                completed.stdout or "{}", "conditional PUT result"
            )
        except contract.InventoryError as error:
            raise contract.ManifestError("conditional PUT success body is ambiguous") from error
        if not isinstance(document, dict):
            raise contract.ManifestError("conditional PUT success body is not an object")
        try:
            response_etag = contract._normalize_etag(
                document.get("ETag"), "conditional PUT result"
            )
        except contract.InventoryError as error:
            raise contract.ManifestError("conditional PUT success lacks a nonempty ETag") from error
        verified, _verified_metadata = self._read_current_exact(key, missing_ok=False)
        assert verified is not None
        if (
            verified["etag"] != response_etag
            or verified["bytes"] != source_row["bytes"]
            or verified["sha256"] != source_row["sha256"]
        ):
            raise contract.ManifestError("conditional PUT post-write HEAD/GET mismatch")
        result = {
            "key": key,
            "etag": verified["etag"],
            "bytes": verified["bytes"],
            "sha256": verified["sha256"],
            "attempt_id": attempt_id,
        }
        result["transaction_id"] = contract.scoped_transaction_id(
            execution_id=self._active_execution_id,
            phase=transaction_phase,
            operation=transaction_operation,
            index=transaction_index,
            result=result,
        )
        return result

    def bind_scoped_preflight(self, snapshot: dict[str, Any]) -> None:
        contract = _contract_module()
        self._active_scoped_preflight_sha256 = snapshot["scoped_preflight_sha256"]
        self._active_execution_id = snapshot["execution_id"]
        self._active_release_preflight_sha256 = snapshot["release_preflight"]["preflight_sha256"]
        self._active_rollback_rows = {
            row["key"]: copy.deepcopy(row)
            for row in snapshot["rollback_manifest"]["objects"]
        }
        self._active_manifest_sha256 = snapshot["manifest_sha256"]
        self._active_source_sha = snapshot["rollback_manifest"]["source_sha"]

    def preflight_scoped_release(
        self, manifest: dict[str, Any], *, execution_id: str
    ) -> dict[str, Any]:
        contract = _contract_module()
        contract.validate_upload_manifest(manifest)
        if manifest["publication_area_id"] != contract.MOLS_AREA_ID:
            raise contract.ManifestError("provider adapter accepts only exact Mols scope")
        rollback_root = self.manifest_root / "rollback"
        rollback_root.mkdir(mode=0o700, exist_ok=False)
        population = {
            row["key"]: row for row in manifest["producer_population"]["objects"]
        }
        geom_row = population.get(contract.MOLS_OBJECT_KEYS[0])
        if geom_row is None:
            raise contract.ManifestError("scoped producer population omits Mols geometry")
        geom_source = self._safe_relative(self.manifest_root, geom_row["local_path"])
        if (
            not geom_source.is_file()
            or geom_source.stat().st_size != geom_row["bytes"]
            or file_digest(geom_source, "sha256") != geom_row["sha256"]
        ):
            raise contract.ManifestError("staged Mols geometry differs from producer population")
        mols_geom = geom_source.read_bytes()
        observed: list[dict[str, Any] | None] = []
        baselines: list[dict[str, Any] | None] = []
        semantic_deltas: list[dict[str, Any]] = []
        index_delta: dict[str, Any] | None = None
        for row in manifest["objects"]:
            source = self._safe_relative(self.manifest_root, row["local_path"])
            if (
                not source.is_file()
                or source.stat().st_size != row["bytes"]
                or file_digest(source, "sha256") != row["sha256"]
                or file_digest(source, "md5") != row["md5"]
            ):
                raise contract.ManifestError("provider preflight found changed staged bytes")
            current, _metadata = self._read_current_exact(row["key"], missing_ok=True)
            observed.append(copy.deepcopy(current))
            if row["conditional_mode"] == "IF_NONE_MATCH":
                baselines.append(None)
                continue
            if current != row["remote_preimage"]:
                baselines.append(None)
                continue
            relative = Path("rollback") / f"{row['key'].encode('utf-8').hex()}.object"
            target = self.manifest_root / relative
            self._download_exact(
                row["key"], etag=current["etag"], output=target,
                context="frozen pre-publish baseline GET",
            )
            baseline = {
                "local_path": str(relative),
                "bytes": target.stat().st_size,
                "md5": file_digest(target, "md5"),
                "sha256": file_digest(target, "sha256"),
                "etag": current["etag"],
            }
            baselines.append(baseline)
            if row["key"] == "index.json":
                index_delta = contract.build_mols_index_delta(
                    target.read_bytes(), source.read_bytes()
                )
            elif row["key"] in contract.MOLS_SEMANTIC_GLOBAL_KEYS:
                semantic_deltas.append(
                    contract.build_mols_global_delta(
                        row["key"],
                        target.read_bytes(),
                        source.read_bytes(),
                        mols_geom,
                        baseline_etag=current["etag"],
                    )
                )
        snapshot = contract.build_scoped_release_preflight(
            manifest,
            execution_id=execution_id,
            observed_preimages=observed,
            baseline_objects=baselines,
            index_delta=index_delta,
            semantic_deltas=semantic_deltas,
        )
        self.bind_scoped_preflight(snapshot)
        _write_contract_json(
            self.manifest_root / "rollback-manifest.json",
            snapshot["rollback_manifest"],
        )
        return snapshot

    def put_conditional(
        self,
        row: dict[str, Any],
        *,
        attempt_id: str,
        preflight_row: dict[str, Any],
        preflight_sha256: str,
    ) -> dict[str, Any]:
        contract = _contract_module()
        if (
            preflight_row.get("key") != row.get("key")
            or preflight_row.get("expected_row_sha256") != contract.canonical_sha256(row)
            or preflight_row.get("eligible") is not True
            or preflight_sha256 != getattr(self, "_active_release_preflight_sha256", None)
        ):
            raise contract.ManifestError("provider effect is not bound to eligible preflight")
        contract.validate_scoped_attempt_id(
            attempt_id,
            execution_id=self._active_execution_id,
            phase="FORWARD",
            index=preflight_row["index"],
        )
        current, _metadata = self._read_current_exact(row["key"], missing_ok=True)
        if current != row["remote_preimage"]:
            raise contract.ConditionalRequestRefused(
                "immediate pre-PUT identity differs from reviewed preimage"
            )
        condition = (
            ("--if-match", current["etag"])
            if row["conditional_mode"] == "IF_MATCH"
            else ("--if-none-match", "*")
        )
        return self._put_exact(
            key=row["key"], source_row=row, condition=condition,
            condition_mode=row["conditional_mode"], preimage=current,
            attempt_id=attempt_id, source_sha=self._active_source_sha,
            manifest_sha256=self._active_manifest_sha256,
            transaction_phase="FORWARD",
            transaction_operation="PUT_CONDITIONAL",
            transaction_index=preflight_row["index"],
        )

    def rollback_put_conditional(
        self,
        rollback_row: dict[str, Any],
        *,
        postimage: dict[str, Any],
        attempt_id: str,
        scoped_preflight_sha256: str,
    ) -> dict[str, Any]:
        contract = _contract_module()
        key = rollback_row.get("key")
        if (
            rollback_row.get("rollback_operation") != "RESTORE_IF_MATCH"
            or key not in contract.MOLS_FORWARD_KEYS
            or rollback_row.get("baseline_preimage") is None
            or scoped_preflight_sha256 != getattr(self, "_active_scoped_preflight_sha256", None)
            or rollback_row != getattr(self, "_active_rollback_rows", {}).get(key)
        ):
            raise contract.ManifestError("rollback restore exceeds exact Mols/index authority")
        contract.validate_scoped_attempt_id(
            attempt_id,
            execution_id=self._active_execution_id,
            phase="ROLLBACK",
            index=rollback_row["index"],
        )
        current, _metadata = self._read_current_exact(key, missing_ok=True)
        expected_post = {
            field: postimage[field] for field in ("etag", "bytes", "sha256")
        }
        if current != expected_post:
            raise contract.ConditionalRequestRefused("rollback postimage changed")
        baseline = copy.deepcopy(rollback_row["baseline_object"])
        baseline["key"] = key
        result = self._put_exact(
            key=key, source_row=baseline,
            condition=("--if-match", current["etag"]),
            condition_mode="IF_MATCH", preimage=current,
            attempt_id=attempt_id, source_sha=self._active_source_sha,
            manifest_sha256=self._active_manifest_sha256,
            transaction_phase="ROLLBACK",
            transaction_operation="RESTORE_IF_MATCH",
            transaction_index=rollback_row["index"],
        )
        if result["etag"] != rollback_row["baseline_preimage"]["etag"]:
            raise contract.ManifestError("rollback restore did not recover baseline ETag")
        return result

    def rollback_delete_created(
        self,
        rollback_row: dict[str, Any],
        *,
        postimage: dict[str, Any],
        attempt_id: str,
        scoped_preflight_sha256: str,
    ) -> dict[str, Any]:
        contract = _contract_module()
        key = rollback_row.get("key")
        if (
            rollback_row.get("rollback_operation") != "DELETE_CREATED_AFTER_EXACT_PROOF"
            or key not in contract.MOLS_OBJECT_KEYS
            or rollback_row.get("baseline_preimage") is not None
            or rollback_row.get("baseline_object") is not None
            or scoped_preflight_sha256 != getattr(self, "_active_scoped_preflight_sha256", None)
            or rollback_row != getattr(self, "_active_rollback_rows", {}).get(key)
        ):
            raise contract.ManifestError("rollback delete exceeds exact absent Mols authority")
        contract.validate_scoped_attempt_id(
            attempt_id,
            execution_id=self._active_execution_id,
            phase="ROLLBACK",
            index=rollback_row["index"],
        )
        expected_post = {
            field: postimage[field] for field in ("etag", "bytes", "sha256")
        }
        current, _metadata = self._read_current_exact(key, missing_ok=True)
        if current != expected_post:
            raise contract.ManifestError("rollback delete HEAD/GET identity mismatch")
        completed = self._run_provider(
            [
                "aws", "s3api", "delete-object", "--bucket", contract.BUCKET,
                "--key", key, "--endpoint-url", self.endpoint_url, "--output", "json",
            ],
            "single plain Mols rollback delete",
        )
        if completed.returncode != 0:
            raise contract.ManifestError("plain Mols delete outcome is indeterminate")
        try:
            provider_result = contract.strict_json_loads(
                completed.stdout or "{}", "plain Mols delete result"
            )
        except contract.InventoryError as error:
            raise contract.ManifestError("plain Mols delete success body is ambiguous") from error
        if not isinstance(provider_result, dict):
            raise contract.ManifestError("plain Mols delete success body is not an object")
        after = self._head_document(key, missing_ok=True)
        if after is not None:
            raise contract.ManifestError("plain Mols delete did not verify absence")
        result = {
            "attempt_id": attempt_id,
            "key": key,
            "postimage_sha256": rollback_row["expected_postimage"]["sha256"],
            "verified_absent": True,
        }
        result["transaction_id"] = contract.scoped_transaction_id(
            execution_id=self._active_execution_id,
            phase="ROLLBACK",
            operation="DELETE_CREATED_AFTER_EXACT_PROOF",
            index=rollback_row["index"],
            result=result,
        )
        return result

    @staticmethod
    def _metadata_matches(
        metadata: dict[str, str] | None, expected: dict[str, str]
    ) -> bool:
        return metadata is not None and all(
            metadata.get(name) == value for name, value in expected.items()
        )

    def reconcile_pending_effect(
        self,
        pending: dict[str, Any],
        manifest: dict[str, Any],
        journal: dict[str, Any],
    ) -> dict[str, Any]:
        contract = _contract_module()
        scoped_preflight = journal["scoped_preflight"]
        self.bind_scoped_preflight(scoped_preflight)
        index = pending["index"]
        key = pending["key"]
        contract.validate_scoped_attempt_id(
            pending["attempt_id"],
            execution_id=journal["execution_id"],
            phase=pending["phase"],
            index=index,
        )
        current, metadata = self._read_current_exact(key, missing_ok=True)
        if pending["phase"] == "FORWARD":
            row = manifest["objects"][index]
            expected_metadata = self._metadata(
                source_row=row, source_sha=manifest["source_sha"],
                manifest_sha256=manifest["manifest_sha256"],
                attempt_id=pending["attempt_id"],
                condition_mode=row["conditional_mode"],
                preimage=row["remote_preimage"],
            )
            if (
                current is not None
                and current["bytes"] == row["bytes"]
                and current["sha256"] == row["sha256"]
                and self._metadata_matches(metadata, expected_metadata)
            ):
                result = {
                    "key": key, "etag": current["etag"],
                    "bytes": current["bytes"], "sha256": current["sha256"],
                    "attempt_id": pending["attempt_id"],
                }
                result["transaction_id"] = contract.scoped_transaction_id(
                    execution_id=journal["execution_id"],
                    phase="FORWARD",
                    operation="PUT_CONDITIONAL",
                    index=index,
                    result=result,
                )
                return {"state": "EFFECT_OBSERVED", "result": result}
            if current == row["remote_preimage"]:
                return {"state": "EFFECT_NOT_OBSERVED", "result": None}
            return {"state": "AMBIGUOUS", "result": None}
        rollback_row = scoped_preflight["rollback_manifest"]["objects"][index]
        postimage = {
            field: journal["forward_results"][index][field]
            for field in ("etag", "bytes", "sha256")
        }
        if pending["operation"] == "DELETE_CREATED_AFTER_EXACT_PROOF":
            if current is None:
                result = {
                    "attempt_id": pending["attempt_id"], "key": key,
                    "postimage_sha256": rollback_row["expected_postimage"]["sha256"],
                    "verified_absent": True,
                }
                result["transaction_id"] = contract.scoped_transaction_id(
                    execution_id=journal["execution_id"],
                    phase="ROLLBACK",
                    operation="DELETE_CREATED_AFTER_EXACT_PROOF",
                    index=index,
                    result=result,
                )
                return {"state": "EFFECT_OBSERVED", "result": result}
            if current == postimage:
                return {"state": "EFFECT_NOT_OBSERVED", "result": None}
            return {"state": "AMBIGUOUS", "result": None}
        baseline = rollback_row["baseline_object"]
        expected_metadata = self._metadata(
            source_row=baseline,
            source_sha=manifest["source_sha"],
            manifest_sha256=manifest["manifest_sha256"],
            attempt_id=pending["attempt_id"],
            condition_mode="IF_MATCH",
            preimage=postimage,
        )
        if (
            current is not None
            and current["etag"] == baseline["etag"]
            and current["bytes"] == baseline["bytes"]
            and current["sha256"] == baseline["sha256"]
            and self._metadata_matches(metadata, expected_metadata)
        ):
            result = {
                "key": key, "etag": current["etag"],
                "bytes": current["bytes"], "sha256": current["sha256"],
                "attempt_id": pending["attempt_id"],
            }
            result["transaction_id"] = contract.scoped_transaction_id(
                execution_id=journal["execution_id"],
                phase="ROLLBACK",
                operation="RESTORE_IF_MATCH",
                index=index,
                result=result,
            )
            return {"state": "EFFECT_OBSERVED", "result": result}
        if current == postimage:
            return {"state": "EFFECT_NOT_OBSERVED", "result": None}
        return {"state": "AMBIGUOUS", "result": None}

    def verify_rollback(
        self,
        rollback_manifest: dict[str, Any],
        *,
        forward_results: list[dict[str, Any]],
        rollback_results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        contract = _contract_module()
        contract.validate_rollback_result_prefix(
            rollback_manifest,
            execution_id=self._active_execution_id,
            forward_results=forward_results,
            rollback_results=rollback_results,
            require_complete=True,
        )
        absent: list[str] = []
        restored_keys: list[str] = []
        semantic_restored: list[str] = []
        index_restored = not any(
            row["key"] == "index.json" for row in rollback_manifest["objects"]
        )
        for row in rollback_manifest["objects"]:
            current, _metadata = self._read_current_exact(row["key"], missing_ok=True)
            if row["rollback_operation"] == "DELETE_CREATED_AFTER_EXACT_PROOF":
                if current is not None:
                    raise contract.ManifestError("created Mols key remains after rollback")
                absent.append(row["key"])
                continue
            baseline = row["baseline_object"]
            if current != {
                field: baseline[field] for field in ("etag", "bytes", "sha256")
            }:
                raise contract.ManifestError("restored object differs from frozen baseline")
            baseline_path = self._safe_relative(
                self.manifest_root, baseline["local_path"]
            )
            with tempfile.TemporaryDirectory(prefix="trekdex-r2-rollback-verify-") as directory:
                output = Path(directory) / "object"
                self._download_exact(
                    row["key"], etag=current["etag"], output=output,
                    context="rollback baseline verification GET",
                )
                if output.read_bytes() != baseline_path.read_bytes():
                    raise contract.ManifestError("rollback bytes differ from frozen baseline")
            restored_keys.append(row["key"])
            if row["key"] in contract.MOLS_SEMANTIC_GLOBAL_KEYS:
                semantic_restored.append(row["key"])
            if row["key"] == "index.json":
                index_restored = True
        verification: dict[str, Any] = {
            "schema": "trekdex-r2-rollback-verification-v2",
            "rollback_manifest_sha256": rollback_manifest["rollback_manifest_sha256"],
            "all_rows_restored": True,
            "verified_absent_keys": absent,
            "verified_restored_keys": restored_keys,
            "semantic_global_baselines_restored": semantic_restored,
            "index_baseline_restored": index_restored,
            "non_target_index_entries_unchanged": True,
        }
        verification["receipt_sha256"] = contract.canonical_sha256(verification)
        return verification


def _validate_apply_material(
    args: argparse.Namespace,
) -> tuple[Any, dict[str, Any], dict[str, Any], dict[str, Any]]:
    contract = _contract_module()
    inventory = _read_contract_json(args.inventory, "inventory receipt")
    manifest = _read_contract_json(args.manifest, "upload manifest")
    contract.validate_upload_manifest(manifest, inventory_receipt=inventory)
    if manifest["publication_area_id"] != contract.MOLS_AREA_ID:
        raise contract.ManifestError("provider operation is restricted to exact Mols scope")
    if manifest["manifest_sha256"] != args.approved_manifest_sha256:
        raise contract.ManifestError("manifest is not the exact reviewed value")
    args.source_tree = manifest["source_tree"]
    _sources, local_rows, population = _derive_producer_population(args, inventory)
    if population != manifest["producer_population"]:
        raise contract.ManifestError("producer population differs from reviewed bytes")
    if {row["key"]: row for row in local_rows} != {
        row["key"]: row for row in population["objects"]
    }:
        raise contract.ManifestError("producer census is inconsistent")
    for row in population["objects"]:
        staged = args.manifest.parent / row["local_path"]
        if (
            staged.is_symlink() or not staged.is_file()
            or staged.stat().st_size != row["bytes"]
            or file_digest(staged, "sha256") != row["sha256"]
        ):
            raise contract.ManifestError("reviewed immutable stage differs from producer population")
    assertion = contract.strict_json_loads(
        os.environ.get("R2_ROOT_APPROVAL_ASSERTION_JSON", ""),
        "root approval assertion",
    )
    if not isinstance(assertion, dict):
        raise contract.ManifestError("root approval assertion is not an object")
    return contract, inventory, manifest, assertion


def _immutable_state_probe(
    manifest: dict[str, Any], workflow: Path, _boundary: str
) -> bool:
    if subprocess.check_output(
        ["git", "rev-parse", "HEAD"], text=True, env=_READONLY_GIT_ENV
    ).strip() != manifest["source_sha"]:
        return False
    if subprocess.check_output(
        ["git", "rev-parse", "HEAD^{tree}"], text=True, env=_READONLY_GIT_ENV
    ).strip() != manifest["source_tree"]:
        return False
    if subprocess.run(
        ["git", "symbolic-ref", "-q", "HEAD"],
        capture_output=True, env=_READONLY_GIT_ENV,
    ).returncode == 0:
        return False
    if subprocess.check_output(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        text=True, env=_READONLY_GIT_ENV,
    ):
        return False
    return file_digest(workflow, "sha256") == manifest["workflow_sha256"]


def _scoped_writer(args: argparse.Namespace) -> MolsConditionalWriter:
    return MolsConditionalWriter(
        endpoint_url=args.endpoint_url,
        account_id=os.environ.get("R2_ACCOUNT_ID", ""),
        manifest_root=args.manifest.parent,
        aws_cli_version=args.aws_cli_version,
        aws_cli_archive_sha256=args.aws_cli_archive_sha256,
    )


def apply_manifest_v2(args: argparse.Namespace) -> int:
    if args.terminal_receipt.exists() or args.terminal_receipt.is_symlink():
        raise _contract_module().ManifestError(
            "existing terminal/journal requires scoped reconciliation; automatic retry is forbidden"
        )
    contract, _inventory, manifest, assertion = _validate_apply_material(args)
    receipt = contract.execute_scoped_release(
        manifest,
        _scoped_writer(args),
        approval_assertion=assertion,
        approval_key=os.environ.get("R2_ROOT_APPROVAL_HMAC_KEY", ""),
        state_probe=lambda boundary: _immutable_state_probe(
            manifest, args.workflow, boundary
        ),
        checkpoint=lambda document: _write_contract_json(
            args.terminal_receipt, document, replace=True
        ),
    )
    _write_contract_json(args.terminal_receipt, receipt, replace=True)
    print(f"release terminal={receipt['terminal_state']} sha256={receipt['receipt_sha256']}")
    return 0 if receipt["terminal_state"] == contract.TerminalState.COMPLETE_SUCCESS.value else 1


def reconcile_manifest_v2(args: argparse.Namespace) -> int:
    contract, _inventory, manifest, assertion = _validate_apply_material(args)
    journal = _read_contract_json(args.journal, "scoped release journal")
    contract.validate_scoped_release_receipt(journal, manifest)
    if args.terminal_receipt.exists() or args.terminal_receipt.is_symlink():
        existing = _read_contract_json(
            args.terminal_receipt, "existing reconciliation terminal"
        )
        validated = contract.validate_scoped_release_receipt(existing, manifest)
        if (
            validated["reconciliation"] is not None
            and validated["reconciliation"]["source_journal_sha256"]
            == journal["receipt_sha256"]
        ):
            print(
                f"reconciliation already terminal={validated['terminal_state']} "
                f"sha256={validated['receipt_sha256']}"
            )
            return 0 if validated["terminal_state"] in {
                contract.TerminalState.COMPLETE_SUCCESS.value, "ROLLED_BACK"
            } else 1
        raise contract.ManifestError("reconciliation output exists for a different journal")
    receipt = contract.reconcile_scoped_release_write_ahead(
        journal,
        manifest,
        _scoped_writer(args),
        approval_assertion=assertion,
        approval_key=os.environ.get("R2_ROOT_APPROVAL_HMAC_KEY", ""),
        state_probe=lambda boundary: _immutable_state_probe(
            manifest, args.workflow, boundary
        ),
        checkpoint=lambda document: _write_contract_json(
            args.terminal_receipt, document, replace=True
        ),
    )
    _write_contract_json(args.terminal_receipt, receipt, replace=True)
    print(
        f"reconciliation terminal={receipt['terminal_state']} "
        f"sha256={receipt['receipt_sha256']}"
    )
    return 0 if receipt["terminal_state"] in {
        contract.TerminalState.COMPLETE_SUCCESS.value, "ROLLED_BACK"
    } else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    verify_input = commands.add_parser("verify-release-input")
    verify_input.add_argument("--extract", required=True)
    verify_input.add_argument("--state", required=True)
    verify_input.add_argument("--region-code", required=True)
    verify_input.add_argument("--bbox", required=True)
    verify_input.add_argument("--dry-run", required=True)
    verify_input.add_argument("--no-routes", required=True)
    verify_input.add_argument("--touch-report", required=True)
    verify_input.add_argument("--elevation", required=True)
    verify_input.add_argument("--pilot-area-id", required=True)
    verify_input.add_argument("--workflow-source-sha", required=True)
    verify_input.add_argument("--workflow-source-tree", required=True)
    verify_input.add_argument("--mols-index-output", required=True, type=Path)
    verify_input.add_argument("--github-output", required=True, type=Path)

    verify_diff = commands.add_parser("verify-mols-staged-diff")
    verify_diff.add_argument("--repo", required=True, type=Path)
    verify_diff.add_argument("--expected-receipt-sha256", required=True)
    verify_diff.add_argument("--output", required=True, type=Path)

    verify_commit = commands.add_parser("verify-mols-commit")
    verify_commit.add_argument("--repo", required=True, type=Path)
    verify_commit.add_argument("--expected-receipt-sha256", required=True)
    verify_commit.add_argument("--output", required=True, type=Path)

    merge_index = commands.add_parser("merge-mols-index")
    merge_index.add_argument("--source", required=True, type=Path)
    merge_index.add_argument("--target", required=True, type=Path)
    merge_index.add_argument("--expected-receipt-sha256", required=True)

    fetch = commands.add_parser("fetch-inventory")
    fetch.add_argument("--bucket", required=True)
    fetch.add_argument("--endpoint-url", required=True)
    fetch.add_argument("--account-identity-ref", required=True)
    fetch.add_argument("--endpoint-identity-ref", required=True)
    fetch.add_argument("--authoritative-denominator", required=True, type=int)
    fetch.add_argument("--area-index", required=True, type=Path)
    fetch.add_argument("--anchor-ref", required=True)
    fetch.add_argument("--output", required=True, type=Path)

    stage = commands.add_parser("stage")
    stage.add_argument("--inventory", required=True, type=Path)
    stage.add_argument("--bucket", required=True)
    stage.add_argument("--stage-root", required=True, type=Path)
    stage.add_argument("--geom-dir", default=Path("public/areas/geom"), type=Path)
    stage.add_argument("--silhouettes-dir", default=Path("public/areas/silhouettes"), type=Path)
    stage.add_argument("--area-index", default=Path("public/areas/index.json"), type=Path)
    stage.add_argument("--global-object", required=True, action="append", default=[])
    stage.add_argument("--source-sha", required=True)
    stage.add_argument("--source-tree", required=True)
    stage.add_argument("--workflow-sha256", required=True)
    stage.add_argument("--root-approval-ref", required=True)
    stage.add_argument("--anchor-ref", required=True)
    stage.add_argument("--publication-area-id")
    stage.add_argument("--endpoint-url")
    stage.add_argument("--aws-cli-version")
    stage.add_argument("--aws-cli-archive-sha256")
    stage.add_argument("--approved-manifest-sha256")

    apply_command = commands.add_parser("apply-manifest")
    apply_command.add_argument("--inventory", required=True, type=Path)
    apply_command.add_argument("--manifest", required=True, type=Path)
    apply_command.add_argument("--approved-manifest-sha256", required=True)
    apply_command.add_argument("--publication-area-id", required=True)
    apply_command.add_argument("--endpoint-url", required=True)
    apply_command.add_argument("--aws-cli-version", required=True)
    apply_command.add_argument("--aws-cli-archive-sha256", required=True)
    apply_command.add_argument("--geom-dir", default=Path("public/areas/geom"), type=Path)
    apply_command.add_argument("--silhouettes-dir", default=Path("public/areas/silhouettes"), type=Path)
    apply_command.add_argument("--area-index", default=Path("public/areas/index.json"), type=Path)
    apply_command.add_argument("--global-object", required=True, action="append", default=[])
    apply_command.add_argument("--workflow", required=True, type=Path)
    apply_command.add_argument("--terminal-receipt", required=True, type=Path)

    reconcile_command = commands.add_parser("reconcile-scoped")
    reconcile_command.add_argument("--inventory", required=True, type=Path)
    reconcile_command.add_argument("--manifest", required=True, type=Path)
    reconcile_command.add_argument("--journal", required=True, type=Path)
    reconcile_command.add_argument("--approved-manifest-sha256", required=True)
    reconcile_command.add_argument("--endpoint-url", required=True)
    reconcile_command.add_argument("--aws-cli-version", required=True)
    reconcile_command.add_argument("--aws-cli-archive-sha256", required=True)
    reconcile_command.add_argument("--geom-dir", default=Path("public/areas/geom"), type=Path)
    reconcile_command.add_argument("--silhouettes-dir", default=Path("public/areas/silhouettes"), type=Path)
    reconcile_command.add_argument("--area-index", default=Path("public/areas/index.json"), type=Path)
    reconcile_command.add_argument("--global-object", required=True, action="append", default=[])
    reconcile_command.add_argument("--workflow", required=True, type=Path)
    reconcile_command.add_argument("--terminal-receipt", required=True, type=Path)
    args = parser.parse_args(argv)

    if args.command == "verify-release-input":
        return verify_release_input_v2(args)
    if args.command == "verify-mols-staged-diff":
        return verify_mols_staged_diff_v2(args)
    if args.command == "verify-mols-commit":
        return verify_mols_commit_v2(args)
    if args.command == "merge-mols-index":
        return merge_mols_index_v2(args)
    if args.command == "fetch-inventory":
        return fetch_inventory_v2(args)
    if args.command == "stage":
        return stage_v2(args)
    if args.command == "reconcile-scoped":
        return reconcile_manifest_v2(args)
    return apply_manifest_v2(args)


if __name__ == "__main__":
    raise SystemExit(main())
