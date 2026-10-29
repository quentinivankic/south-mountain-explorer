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
        publication_area_id=getattr(args, "publication_area_id", None),
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
    """Exact Mols publication adapter; all effects occur in one locked job.

    Request identity is the contract attempt ID. Result identity is derived only
    from required provider-returned ETag/checksum/version fields and independently
    verified HEAD/GET bytes. No debug output or credential value is retained.
    """

    _PUT_RESULT_FIELDS = {"ETag", "ChecksumSHA256", "VersionId"}
    _POST_METADATA_FIELDS = {
        "trekdex-sha256", "trekdex-source-sha", "trekdex-manifest-sha256",
        "trekdex-attempt-id", "trekdex-preimage-ref", "trekdex-condition-mode",
    }

    def _run_provider(
        self,
        command: list[str],
        context: str,
        *,
        timeout: int = 180,
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
        document = contract.strict_json_loads(completed.stdout, "provider HEAD")
        if not isinstance(document, dict):
            raise contract.ManifestError("provider HEAD is not an object")
        return document

    def _head_identity(
        self, key: str, *, missing_ok: bool = False
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        contract = _contract_module()
        document = self._head_document(key, missing_ok=missing_ok)
        if document is None:
            return None, None
        try:
            etag = contract._normalize_etag(document.get("ETag"), "provider HEAD")
            byte_count = document.get("ContentLength")
            version_id = document.get("VersionId")
            if (
                not isinstance(byte_count, int)
                or isinstance(byte_count, bool)
                or byte_count < 0
                or not isinstance(version_id, str)
                or not contract._SAFE_VERSION_RE.fullmatch(version_id)
            ):
                raise ValueError("identity fields are invalid")
        except (ValueError, contract.InventoryError) as error:
            raise contract.ManifestError("provider HEAD identity is malformed") from error
        checksum_value = document.get("ChecksumSHA256")
        checksum = None
        if checksum_value is not None:
            try:
                checksum = contract.normalize_sha256_checksum(
                    checksum_value, "provider HEAD"
                )
            except contract.InventoryError as error:
                raise contract.ManifestError("provider HEAD checksum is malformed") from error
        return {
            "etag": etag,
            "bytes": byte_count,
            "sha256": checksum,
            "version_id": version_id,
        }, document

    def _download_exact(
        self,
        key: str,
        *,
        etag: str,
        version_id: str,
        output: Path,
        context: str,
    ) -> dict[str, Any]:
        contract = _contract_module()
        if output.exists() or output.is_symlink():
            raise contract.ManifestError(f"{context} output already exists")
        completed = self._run_provider(
            [
                "aws", "s3api", "get-object", "--bucket", contract.BUCKET,
                "--key", key, "--version-id", version_id, "--if-match", etag,
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
    ) -> dict[str, Any]:
        contract = _contract_module()
        if condition_mode == "IF_MATCH":
            if (
                not isinstance(preimage, dict)
                or condition != ("--if-match", preimage.get("etag"))
                or set(preimage) != {"etag", "bytes", "sha256", "version_id"}
            ):
                raise contract.ManifestError("IF_MATCH request is not bound to the exact preimage")
        elif condition_mode == "IF_NONE_MATCH":
            if preimage is not None or condition != ("--if-none-match", "*"):
                raise contract.ManifestError("IF_NONE_MATCH request is not bound to reviewed absence")
        else:
            raise contract.ManifestError("conditional PUT mode is invalid")
        snapshot = self._snapshot_source(source_row)
        metadata = {
            "trekdex-sha256": source_row["sha256"],
            "trekdex-source-sha": source_sha,
            "trekdex-manifest-sha256": manifest_sha256,
            "trekdex-attempt-id": attempt_id,
            "trekdex-preimage-ref": contract.canonical_sha256(
                preimage if preimage is not None else {"state": "ABSENT"}
            ),
            "trekdex-condition-mode": condition_mode,
        }
        command = [
            "aws", "s3api", "put-object", "--bucket", contract.BUCKET,
            "--key", key, "--body", f"/dev/fd/{snapshot.fileno()}",
            "--endpoint-url", self.endpoint_url, "--content-type", "application/json",
            "--content-length", str(source_row["bytes"]),
            "--content-md5", base64.b64encode(bytes.fromhex(source_row["md5"])).decode("ascii"),
            "--checksum-algorithm", "SHA256",
            "--checksum-sha256", base64.b64encode(bytes.fromhex(source_row["sha256"])).decode("ascii"),
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
        document = contract.strict_json_loads(completed.stdout, "conditional PUT result")
        if not isinstance(document, dict) or set(document) != self._PUT_RESULT_FIELDS:
            raise contract.ManifestError("conditional PUT success schema is malformed or ambiguous")
        etag = contract._normalize_etag(document["ETag"], "conditional PUT result")
        checksum = contract.normalize_sha256_checksum(
            document["ChecksumSHA256"], "conditional PUT result"
        )
        version_id = document["VersionId"]
        if (
            etag != source_row["md5"]
            or checksum != source_row["sha256"]
            or not isinstance(version_id, str)
            or not contract._SAFE_VERSION_RE.fullmatch(version_id)
        ):
            raise contract.ManifestError("conditional PUT result identity mismatches reviewed bytes")
        verified = self._prove_postimage(
            key,
            expected={
                "etag": etag,
                "bytes": source_row["bytes"],
                "sha256": source_row["sha256"],
                "version_id": version_id,
            },
            metadata=metadata,
        )
        transaction_id = "r2-result-" + contract.canonical_sha256({
            "attempt_id": attempt_id,
            "etag": verified["etag"],
            "sha256": verified["sha256"],
            "version_id": verified["version_id"],
        })[:32]
        return {
            "key": key,
            "etag": verified["etag"],
            "bytes": verified["bytes"],
            "sha256": verified["sha256"],
            "version_id": verified["version_id"],
            "transaction_id": transaction_id,
            "attempt_id": attempt_id,
        }

    def _prove_postimage(
        self,
        key: str,
        *,
        expected: dict[str, Any],
        metadata: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        contract = _contract_module()
        observed, document = self._head_identity(key, missing_ok=True)
        if observed is None or document is None:
            raise contract.ManifestError("postimage HEAD is absent")
        for field in ("etag", "bytes", "version_id"):
            if observed[field] != expected[field]:
                raise contract.ManifestError(f"postimage HEAD {field} mismatch")
        provider_metadata = document.get("Metadata")
        metadata_hash = None
        if isinstance(provider_metadata, dict):
            metadata_hash = provider_metadata.get("trekdex-sha256")
        if metadata is not None:
            if not isinstance(provider_metadata, dict) or {
                str(key).lower(): str(value) for key, value in provider_metadata.items()
            } != metadata:
                raise contract.ManifestError("postimage HEAD metadata binding mismatch")
        if observed["sha256"] is not None and observed["sha256"] != expected["sha256"]:
            raise contract.ManifestError("postimage HEAD SHA-256 mismatch")
        if observed["sha256"] is None and metadata_hash != expected["sha256"]:
            # A missing HEAD checksum is not accepted on metadata alone. GET and
            # hash the exact version under the workflow lock as an extra proof.
            metadata_hash = None
        with tempfile.TemporaryDirectory(prefix="trekdex-r2-proof-") as directory:
            output = Path(directory) / "object"
            self._download_exact(
                key,
                etag=expected["etag"],
                version_id=expected["version_id"],
                output=output,
                context="exact postimage GET",
            )
            actual_sha256 = file_digest(output, "sha256")
            actual_bytes = output.stat().st_size
        if actual_sha256 != expected["sha256"] or actual_bytes != expected["bytes"]:
            raise contract.ManifestError("exact postimage GET bytes mismatch")
        return {
            "etag": expected["etag"],
            "bytes": actual_bytes,
            "sha256": actual_sha256,
            "version_id": expected["version_id"],
        }

    def preflight_scoped_release(
        self, manifest: dict[str, Any], *, execution_id: str
    ) -> dict[str, Any]:
        contract = _contract_module()
        contract.validate_upload_manifest(manifest)
        if manifest["publication_area_id"] != contract.MOLS_AREA_ID:
            raise contract.ManifestError("provider adapter accepts only exact Mols scope")
        self._active_manifest_sha256 = manifest["manifest_sha256"]
        self._active_source_sha = manifest["source_sha"]
        rollback_root = self.manifest_root / "rollback"
        rollback_root.mkdir(mode=0o700, exist_ok=False)
        observed: list[dict[str, Any] | None] = []
        baselines: list[dict[str, Any] | None] = []
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
            current, _document = self._head_identity(row["key"], missing_ok=True)
            observed.append(copy_identity := copy.deepcopy(current))
            if row["conditional_mode"] == "IF_NONE_MATCH":
                baselines.append(None)
                continue
            if copy_identity != row["remote_preimage"]:
                baselines.append(None)
                continue
            relative = Path("rollback") / f"{row['key'].encode('utf-8').hex()}.object"
            target = self.manifest_root / relative
            self._download_exact(
                row["key"],
                etag=copy_identity["etag"],
                version_id=copy_identity["version_id"],
                output=target,
                context="frozen pre-publish baseline GET",
            )
            baseline = {
                "local_path": str(relative),
                "bytes": target.stat().st_size,
                "md5": file_digest(target, "md5"),
                "sha256": file_digest(target, "sha256"),
                "etag": copy_identity["etag"],
                "version_id": copy_identity["version_id"],
            }
            baselines.append(baseline)
            if row["key"] == "index.json":
                index_delta = contract.build_mols_index_delta(
                    target.read_bytes(), source.read_bytes()
                )
        snapshot = contract.build_scoped_release_preflight(
            manifest,
            execution_id=execution_id,
            observed_preimages=observed,
            baseline_objects=baselines,
            index_delta=index_delta,
        )
        self._active_scoped_preflight_sha256 = snapshot["scoped_preflight_sha256"]
        self._active_release_preflight_sha256 = snapshot["release_preflight"]["preflight_sha256"]
        self._active_rollback_rows = {
            row["key"]: copy.deepcopy(row)
            for row in snapshot["rollback_manifest"]["objects"]
        }
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
            or not isinstance(preflight_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", preflight_sha256)
            or preflight_sha256 != getattr(self, "_active_release_preflight_sha256", None)
            or getattr(self, "_active_manifest_sha256", None) is None
            or getattr(self, "_active_source_sha", None) is None
        ):
            raise contract.ManifestError("provider effect is not bound to eligible scoped preflight")
        current, _document = self._head_identity(row["key"], missing_ok=True)
        expected = row["remote_preimage"]
        if current != expected:
            raise contract.ConditionalRequestRefused(
                "immediate pre-PUT object identity differs from reviewed preimage"
            )
        condition = (
            ("--if-match", expected["etag"])
            if row["conditional_mode"] == "IF_MATCH"
            else ("--if-none-match", "*")
        )
        return self._put_exact(
            key=row["key"],
            source_row=row,
            condition=condition,
            condition_mode=row["conditional_mode"],
            preimage=row["remote_preimage"],
            attempt_id=attempt_id,
            source_sha=self._active_source_sha,
            manifest_sha256=self._active_manifest_sha256,
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
        if (
            rollback_row.get("rollback_operation") != "RESTORE_IF_MATCH"
            or rollback_row.get("key") not in {*contract.MOLS_OBJECT_KEYS, "index.json"}
            or rollback_row.get("baseline_preimage") is None
            or scoped_preflight_sha256 != getattr(self, "_active_scoped_preflight_sha256", None)
            or rollback_row != getattr(self, "_active_rollback_rows", {}).get(rollback_row.get("key"))
        ):
            raise contract.ManifestError("rollback restore is outside exact Mols/index authority")
        self._prove_postimage(
            rollback_row["key"],
            expected={field: postimage[field] for field in ("etag", "bytes", "sha256", "version_id")},
        )
        baseline = copy.deepcopy(rollback_row["baseline_object"])
        baseline["key"] = rollback_row["key"]
        result = self._put_exact(
            key=rollback_row["key"],
            source_row=baseline,
            condition=("--if-match", postimage["etag"]),
            condition_mode="IF_MATCH",
            preimage={field: postimage[field] for field in ("etag", "bytes", "sha256", "version_id")},
            attempt_id=attempt_id,
            source_sha=self._active_source_sha,
            manifest_sha256=self._active_manifest_sha256,
        )
        if result["etag"] != rollback_row["baseline_preimage"]["etag"]:
            raise contract.ManifestError("index rollback did not restore baseline ETag")
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
            rollback_row.get("rollback_operation") != "DELETE_CREATED_IF_MATCH"
            or key not in contract.MOLS_OBJECT_KEYS
            or rollback_row.get("baseline_preimage") is not None
            or rollback_row.get("baseline_object") is not None
            or scoped_preflight_sha256 != getattr(self, "_active_scoped_preflight_sha256", None)
            or rollback_row != getattr(self, "_active_rollback_rows", {}).get(key)
        ):
            raise contract.ManifestError("rollback delete is outside exact absent Mols authority")
        self._prove_postimage(
            key,
            expected={field: postimage[field] for field in ("etag", "bytes", "sha256", "version_id")},
        )
        # Exactly one request. Any ambiguous/nonzero response is recorded by the
        # caller as indeterminate and is never retried blindly.
        completed = self._run_provider(
            [
                "aws", "s3api", "delete-object", "--bucket", contract.BUCKET,
                "--key", key, "--version-id", postimage["version_id"],
                "--if-match", postimage["etag"], "--endpoint-url", self.endpoint_url,
                "--output", "json",
            ],
            "single conditional Mols rollback delete",
        )
        if completed.returncode != 0:
            raise contract.ManifestError("conditional Mols delete outcome is indeterminate")
        document = contract.strict_json_loads(
            completed.stdout or "{}", "conditional Mols delete result"
        )
        allowed = {"DeleteMarker", "VersionId", "RequestCharged"}
        if not isinstance(document, dict) or not set(document).issubset(allowed):
            raise contract.ManifestError("conditional Mols delete success schema is ambiguous")
        if "VersionId" in document and document["VersionId"] != postimage["version_id"]:
            raise contract.ManifestError("conditional Mols delete returned a different version")
        after, _after_document = self._head_identity(key, missing_ok=True)
        if after is not None:
            raise contract.ManifestError("conditional Mols delete did not verify absence")
        transaction_id = "r2-delete-" + contract.canonical_sha256({
            "attempt_id": attempt_id,
            "key": key,
            "postimage": {field: postimage[field] for field in ("etag", "bytes", "sha256", "version_id")},
            "provider_result": document,
            "verified_absent": True,
        })[:32]
        return {
            "attempt_id": attempt_id,
            "key": key,
            "postimage_sha256": rollback_row["expected_postimage"]["sha256"],
            "transaction_id": transaction_id,
            "verified_absent": True,
        }

    def verify_rollback(
        self,
        rollback_manifest: dict[str, Any],
        *,
        rollback_results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        contract = _contract_module()
        # The contract validator proves this is the complete exact Mols plan.
        if rollback_manifest["publication_area_id"] != contract.MOLS_AREA_ID:
            raise contract.ManifestError("rollback verification scope is invalid")
        if len(rollback_results) != rollback_manifest["object_count"]:
            raise contract.ManifestError("rollback verification result population is incomplete")
        absent: list[str] = []
        index_restored = not any(
            row["key"] == "index.json" for row in rollback_manifest["objects"]
        )
        for row in rollback_manifest["objects"]:
            current, _document = self._head_identity(row["key"], missing_ok=True)
            if row["rollback_operation"] == "DELETE_CREATED_IF_MATCH":
                if current is not None:
                    raise contract.ManifestError("created Mols key remains after rollback")
                absent.append(row["key"])
                continue
            baseline = row["baseline_object"]
            if current is None or any(
                current[field] != baseline[field]
                for field in ("etag", "bytes", "sha256")
            ):
                raise contract.ManifestError("restored object differs from frozen baseline")
            baseline_path = self._safe_relative(self.manifest_root, baseline["local_path"])
            with tempfile.TemporaryDirectory(prefix="trekdex-r2-rollback-verify-") as directory:
                output = Path(directory) / "object"
                self._download_exact(
                    row["key"], etag=current["etag"], version_id=current["version_id"],
                    output=output, context="rollback baseline verification GET",
                )
                if output.read_bytes() != baseline_path.read_bytes():
                    raise contract.ManifestError("rollback bytes are not the exact frozen baseline")
            if row["key"] == "index.json":
                index_restored = True
        verification: dict[str, Any] = {
            "schema": "trekdex-r2-rollback-verification-v1",
            "rollback_manifest_sha256": rollback_manifest["rollback_manifest_sha256"],
            "all_rows_restored": True,
            "verified_absent_keys": absent,
            "index_baseline_restored": index_restored,
            "non_target_index_entries_unchanged": True,
        }
        verification["receipt_sha256"] = contract.canonical_sha256(verification)
        return verification


def apply_manifest_v2(args: argparse.Namespace) -> int:
    contract = _contract_module()
    if args.terminal_receipt.exists() or args.terminal_receipt.is_symlink():
        raise contract.ManifestError(
            "existing terminal/journal requires explicit reconciliation; automatic retry is forbidden"
        )
    inventory = _read_contract_json(args.inventory, "inventory receipt")
    manifest = _read_contract_json(args.manifest, "upload manifest")
    contract.validate_upload_manifest(manifest, inventory_receipt=inventory)
    if manifest["publication_area_id"] != args.publication_area_id:
        raise contract.ManifestError("apply publication area differs from reviewed manifest")
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
            capture_output=True,
            env=_READONLY_GIT_ENV,
        ).returncode == 0:
            return False
        if subprocess.check_output(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            text=True,
            env=_READONLY_GIT_ENV,
        ):
            return False
        return file_digest(args.workflow, "sha256") == manifest["workflow_sha256"]

    writer = MolsConditionalWriter(
        endpoint_url=args.endpoint_url,
        account_id=os.environ.get("R2_ACCOUNT_ID", ""),
        manifest_root=args.manifest.parent,
    )
    receipt = contract.execute_scoped_release(
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
    stage.add_argument("--publication-area-id")
    stage.add_argument("--approved-manifest-sha256")

    apply_command = commands.add_parser("apply-manifest")
    apply_command.add_argument("--inventory", required=True, type=Path)
    apply_command.add_argument("--manifest", required=True, type=Path)
    apply_command.add_argument("--approved-manifest-sha256", required=True)
    apply_command.add_argument("--publication-area-id", required=True)
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
