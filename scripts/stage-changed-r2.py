#!/usr/bin/env python3
"""Build and optionally apply a canonical R2 release contract.

``fetch-inventory`` seals account/endpoint/bucket identity, pagination closure,
every key/preimage, the protected set, denominator, digest, and anchor state.
``stage`` copies the complete local producer set into deterministic paths and
builds one exact changed-leaf-plus-four-global conditional manifest.
``apply-manifest`` accepts only the separately reviewed hash and emits a typed
terminal receipt.  The policy authority is :mod:`r2_release_contract`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

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
                "VersionId": head.get("VersionId"),
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


def stage_v2(args: argparse.Namespace) -> int:
    contract = _contract_module()
    inventory = _read_contract_json(args.inventory, "inventory receipt")
    contract.validate_inventory_receipt(inventory, expected_bucket=args.bucket)
    if args.stage_root.exists() or args.stage_root.is_symlink():
        raise contract.ManifestError(f"stage root must not already exist: {args.stage_root}")
    sources, local_rows, population = _derive_producer_population(args, inventory)
    manifest = contract.build_upload_manifest(
        inventory,
        local_rows,
        population,
        source_sha=args.source_sha,
        source_tree=args.source_tree,
        workflow_sha256=args.workflow_sha256,
        root_approval_ref=args.root_approval_ref,
        prior_anchor_ref=args.anchor_ref,
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
    """Fail-closed provider adapter with actual-identity and exact-content proof."""

    def __init__(self, *, endpoint_url: str, account_id: str, manifest_root: Path):
        self.endpoint_url = endpoint_url
        self.account_id = account_id
        self.manifest_root = manifest_root.resolve()

    def attest_identity(self) -> dict[str, Any]:
        return _contract_module().provider_identity_attestation(
            self.account_id, self.endpoint_url
        )

    @staticmethod
    def _request_id(document: dict[str, Any]) -> str:
        metadata = document.get("ResponseMetadata")
        candidate = metadata.get("RequestId") if isinstance(metadata, dict) else document.get("RequestId")
        if not isinstance(candidate, str) or not candidate:
            raise _contract_module().ManifestError(
                "provider response lacks a distinct request/transaction identity"
            )
        return candidate

    def _preflight_head(self, row: dict[str, Any]) -> dict[str, Any] | None:
        """Observe the exact current condition without performing a provider effect."""
        contract = _contract_module()
        completed = subprocess.run(
            [
                "aws", "s3api", "head-object", "--bucket", contract.BUCKET,
                "--key", row["key"], "--endpoint-url", self.endpoint_url,
                "--checksum-mode", "ENABLED", "--output", "json",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if completed.returncode != 0:
            # Absence is the only nonzero result that establishes IF_NONE_MATCH.
            # Authentication, timeout, transport, and ambiguous failures abort the
            # complete preflight before any write.
            if re.search(r"(?:\(404\)|\bNoSuchKey\b|\bNot Found\b)", completed.stderr):
                return None
            raise contract.ManifestError(
                "provider preflight could not determine exact current object state"
            )
        document = contract.strict_json_loads(
            completed.stdout, "provider release preflight HEAD"
        )
        if not isinstance(document, dict):
            raise contract.ManifestError("provider preflight HEAD is not an object")
        return {
            "etag": document.get("ETag"),
            "bytes": document.get("ContentLength"),
            "sha256": document.get("ChecksumSHA256"),
            "version_id": document.get("VersionId"),
        }

    def preflight_release(
        self, manifest: dict[str, Any], *, execution_id: str
    ) -> dict[str, Any]:
        """Read every condition first; this adapter remains effect-disabled.

        AWS CLI put-object JSON does not guarantee a provider RequestId in its
        success body. Because the contract requires that transaction identity,
        the capability is deliberately unproven and every row is ineligible.
        This prevents the former post-effect success-schema rejection.
        """
        contract = _contract_module()
        observed: list[dict[str, Any] | None] = []
        for row in manifest["objects"]:
            relative = Path(row["local_path"])
            source = (self.manifest_root / relative).resolve()
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or self.manifest_root not in source.parents
                or source.is_symlink()
                or not source.is_file()
                or source.stat().st_size != row["bytes"]
                or file_digest(source, "sha256") != row["sha256"]
            ):
                raise contract.ManifestError(
                    "provider preflight found changed or unsafe staged bytes"
                )
            observed.append(self._preflight_head(row))
        return contract.build_release_preflight(
            manifest,
            execution_id=execution_id,
            observed_preimages=observed,
            adapter_success_schema=contract.RELEASE_PROVIDER_RESULT_FIELDS,
            adapter_success_schema_capable=False,
        )

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
            or preflight_row.get("expected_row_sha256")
            != contract.canonical_sha256(row)
            or preflight_row.get("eligible") is not True
            or not isinstance(preflight_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", preflight_sha256)
        ):
            raise contract.ManifestError("provider effect is not bound to eligible preflight")
        relative = Path(row["local_path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise contract.ManifestError("manifest local path escapes its staging root")
        cursor = self.manifest_root
        for part in relative.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise contract.ManifestError("manifest local path contains a symlink")
        source = (self.manifest_root / relative).resolve()
        if self.manifest_root not in source.parents or not source.is_file():
            raise contract.ManifestError("manifest local object is missing or unsafe")
        # Existing-object replacement stays mechanically disabled: the current
        # CLI/provider contract exposes ETag conditionals but no atomic full
        # {version, ETag, bytes, SHA-256} compare-and-swap. Provider conformance
        # must prove such a primitive before this branch may be implemented.
        if row["conditional_mode"] == "IF_MATCH":
            raise contract.ManifestError(
                "live overwrite disabled: provider has no atomic full-preimage replace primitive"
            )
        if row["conditional_mode"] != "IF_NONE_MATCH" or row["remote_preimage"] is not None:
            raise contract.ManifestError("unsupported conditional mode/preimage")

        with tempfile.TemporaryFile(mode="w+b") as snapshot:
            digest = hashlib.sha256()
            byte_count = 0
            with source.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1 << 20), b""):
                    snapshot.write(chunk)
                    digest.update(chunk)
                    byte_count += len(chunk)
            snapshot.flush()
            os.fsync(snapshot.fileno())
            if byte_count != row["bytes"] or digest.hexdigest() != row["sha256"]:
                raise contract.ManifestError("staged object changed after manifest review")
            snapshot.seek(0)
            body_path = f"/dev/fd/{snapshot.fileno()}"
            completed = subprocess.run(
                [
                    "aws", "s3api", "put-object", "--bucket", contract.BUCKET,
                    "--key", row["key"], "--body", body_path,
                    "--endpoint-url", self.endpoint_url,
                    "--content-type", "application/json",
                    "--checksum-algorithm", "SHA256",
                    "--if-none-match", "*", "--output", "json",
                ],
                capture_output=True,
                text=True,
                check=True,
                timeout=180,
                pass_fds=(snapshot.fileno(),),
            )
        result = contract.strict_json_loads(completed.stdout, "conditional provider result")
        if not isinstance(result, dict):
            raise contract.ManifestError("conditional provider result is not an object")
        version_id = result.get("VersionId")
        if not isinstance(version_id, str) or not version_id:
            raise contract.ManifestError("provider did not return a stable version identity")
        transaction_id = self._request_id(result)
        verified = subprocess.run(
            [
                "aws", "s3api", "head-object", "--bucket", contract.BUCKET,
                "--key", row["key"], "--version-id", version_id,
                "--endpoint-url", self.endpoint_url, "--checksum-mode", "ENABLED",
                "--output", "json",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=180,
        )
        head = contract.strict_json_loads(verified.stdout, "provider write read-back")
        if not isinstance(head, dict):
            raise contract.ManifestError("provider write read-back is not an object")
        provider_checksum = contract.normalize_sha256_checksum(
            head.get("ChecksumSHA256"), "provider write read-back"
        )
        with tempfile.TemporaryDirectory(prefix="trekdex-r2-readback-") as directory:
            output = Path(directory) / "object"
            fetched = subprocess.run(
                [
                    "aws", "s3api", "get-object", "--bucket", contract.BUCKET,
                    "--key", row["key"], "--version-id", version_id,
                    "--endpoint-url", self.endpoint_url, "--checksum-mode", "ENABLED",
                    "--output", "json", str(output),
                ],
                capture_output=True,
                text=True,
                check=True,
                timeout=180,
            )
            fetched_document = contract.strict_json_loads(
                fetched.stdout or "{}", "provider exact-version GET"
            )
            if not isinstance(fetched_document, dict):
                raise contract.ManifestError("provider exact-version GET response is invalid")
            fetched_sha256 = file_digest(output, "sha256")
            fetched_bytes = output.stat().st_size
        if (
            head.get("VersionId") != version_id
            or fetched_bytes != row["bytes"]
            or fetched_sha256 != row["sha256"]
            or provider_checksum != row["sha256"]
        ):
            raise contract.ManifestError("provider exact-version bytes/checksum mismatch")
        return {
            "key": row["key"],
            "etag": head.get("ETag"),
            "bytes": fetched_bytes,
            "sha256": fetched_sha256,
            "version_id": version_id,
            "transaction_id": transaction_id,
            "attempt_id": attempt_id,
        }


def apply_manifest_v2(args: argparse.Namespace) -> int:
    contract = _contract_module()
    if args.terminal_receipt.exists() or args.terminal_receipt.is_symlink():
        raise contract.ManifestError(
            "existing terminal/journal requires explicit reconciliation; automatic retry is forbidden"
        )
    inventory = _read_contract_json(args.inventory, "inventory receipt")
    manifest = _read_contract_json(args.manifest, "upload manifest")
    contract.validate_upload_manifest(manifest, inventory_receipt=inventory)
    if manifest["manifest_sha256"] != args.approved_manifest_sha256:
        raise contract.ManifestError("apply refused: manifest hash is not the exact reviewed value")
    args.source_tree = manifest["source_tree"]
    _sources, local_rows, population = _derive_producer_population(args, inventory)
    if population != manifest["producer_population"]:
        raise contract.ManifestError("apply-time producer population differs from reviewed bytes")
    if {row["key"]: row for row in local_rows} != {
        row["key"]: row for row in population["objects"]
    }:
        raise contract.ManifestError("apply-time producer census is inconsistent")
    for row in population["objects"]:
        staged = args.manifest.parent / row["local_path"]
        if (
            staged.is_symlink() or not staged.is_file()
            or staged.stat().st_size != row["bytes"]
            or file_digest(staged, "sha256") != row["sha256"]
        ):
            raise contract.ManifestError("reviewed immutable stage differs from producer population")
    assertion_text = os.environ.get("R2_ROOT_APPROVAL_ASSERTION_JSON", "")
    approval_key = os.environ.get("R2_ROOT_APPROVAL_HMAC_KEY", "")
    assertion = contract.strict_json_loads(assertion_text, "root approval assertion")
    if not isinstance(assertion, dict):
        raise contract.ManifestError("root approval assertion is not an object")

    def state_probe(_boundary: str) -> bool:
        if subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip() != manifest["source_sha"]:
            return False
        if subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], text=True).strip() != manifest["source_tree"]:
            return False
        if subprocess.run(["git", "symbolic-ref", "-q", "HEAD"], capture_output=True).returncode == 0:
            return False
        if subprocess.check_output(["git", "status", "--porcelain=v1", "--untracked-files=all"], text=True):
            return False
        return file_digest(args.workflow, "sha256") == manifest["workflow_sha256"]

    writer = AwsConditionalWriter(
        endpoint_url=args.endpoint_url,
        account_id=os.environ.get("R2_ACCOUNT_ID", ""),
        manifest_root=args.manifest.parent,
    )
    receipt = contract.execute_release(
        manifest,
        writer,
        approval_assertion=assertion,
        approval_key=approval_key,
        state_probe=state_probe,
        checkpoint=lambda document: _write_contract_json(
            args.terminal_receipt, document, replace=True
        ),
    )
    _write_contract_json(args.terminal_receipt, receipt, replace=True)
    print(f"release terminal={receipt['terminal_state']} sha256={receipt['receipt_sha256']}")
    return 0 if receipt["terminal_state"] == contract.TerminalState.COMPLETE_SUCCESS.value else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

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
    stage.add_argument("--approved-manifest-sha256")

    apply_command = commands.add_parser("apply-manifest")
    apply_command.add_argument("--inventory", required=True, type=Path)
    apply_command.add_argument("--manifest", required=True, type=Path)
    apply_command.add_argument("--approved-manifest-sha256", required=True)
    apply_command.add_argument("--endpoint-url", required=True)
    apply_command.add_argument("--geom-dir", default=Path("public/areas/geom"), type=Path)
    apply_command.add_argument("--silhouettes-dir", default=Path("public/areas/silhouettes"), type=Path)
    apply_command.add_argument("--area-index", default=Path("public/areas/index.json"), type=Path)
    apply_command.add_argument("--global-object", required=True, action="append", default=[])
    apply_command.add_argument("--workflow", required=True, type=Path)
    apply_command.add_argument("--terminal-receipt", required=True, type=Path)
    args = parser.parse_args(argv)

    if args.command == "fetch-inventory":
        return fetch_inventory_v2(args)
    if args.command == "stage":
        return stage_v2(args)
    return apply_manifest_v2(args)


if __name__ == "__main__":
    raise SystemExit(main())
