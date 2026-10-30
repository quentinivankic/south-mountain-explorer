#!/usr/bin/env python3
"""Plan or apply fail-closed R2 orphan quarantine.

The active policy lives only in :mod:`r2_release_contract`.  This consumer can
build a canonical plan or perform a no-clobber copy and exact verification.
Real source removal remains fail-closed until a separately approved documented
atomic primitive exists; local contract tests inject fakes for state-machine
coverage. It has no final-deletion operation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path



def file_digest(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


DEFAULT_INDEX = Path(__file__).resolve().parent.parent / "ios" / "SouthMountainExplorer" / "Resources" / "areas-index.json"
DEFAULT_REGISTRY = Path(__file__).resolve().parent.parent / "public" / "areas" / "index.json"
MIN_INDEX = 1000
MAX_FRAC = 0.60


def _contract_module():
    import r2_release_contract as contract
    return contract


def _read_contract_json(path: Path, context: str) -> object:
    contract = _contract_module()
    if path.is_symlink() or not path.is_file():
        raise contract.InventoryError(f"{context} is not a regular file: {path}")
    return contract.strict_json_loads(path.read_text(encoding="utf-8"), context)


def _write_contract_json(path: Path, document: dict[str, object], *, replace: bool = False) -> None:
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


def _strict_index_ids(path: Path) -> set[str]:
    return _contract_module().load_area_ids(path, minimum=MIN_INDEX)


def plan_quarantine(args: argparse.Namespace) -> int:
    contract = _contract_module()
    inventory = _read_contract_json(args.inventory, "inventory receipt")
    contract.validate_inventory_receipt(inventory, expected_bucket=contract.BUCKET)
    registry = contract.load_area_ids(args.registry, minimum=MIN_INDEX)
    if registry != set(inventory["area_registry_ids"]):
        raise contract.ManifestError("local area registry differs from inventory receipt")
    active_ids = _strict_index_ids(args.index)
    plan = contract.build_quarantine_plan(
        inventory,
        active_area_ids=active_ids,
        source_sha=args.source_sha,
        source_tree=args.source_tree,
        workflow_sha256=args.workflow_sha256,
        root_approval_ref=args.root_approval_ref,
        receipt_id=args.receipt_id,
    )
    area_objects = sum(
        inventory["class_counts"][name]
        for name in (contract.KeyClass.AREA_GEOM.value, contract.KeyClass.AREA_SILHOUETTE.value)
    )
    fraction = plan["object_count"] / max(1, area_objects)
    if fraction > MAX_FRAC:
        raise contract.ManifestError(
            f"quarantine plan is implausibly large: {fraction:.1%} > {MAX_FRAC:.0%}"
        )
    if args.approved_quarantine_plan_sha256 and plan["plan_sha256"] != args.approved_quarantine_plan_sha256:
        raise contract.ManifestError("computed quarantine plan differs from the reviewed hash")
    _write_contract_json(args.output, plan)
    print(
        f"quarantine plan only: objects={plan['object_count']} "
        f"sha256={plan['plan_sha256']} final_deletion_authorized=false"
    )
    return 0


class AwsQuarantineStore:
    """Version-free adapter: exact current copy, verify, then single-key remove."""

    def __init__(self, endpoint_url: str, account_id: str):
        self.endpoint_url = endpoint_url
        self.account_id = account_id
        self.contract = _contract_module()

    def attest_identity(self) -> dict[str, object]:
        return self.contract.provider_identity_attestation(
            self.account_id, self.endpoint_url
        )

    def _run(
        self, command: list[str], context: str, *, missing_ok: bool = False
    ) -> dict[str, object] | None:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=180)
        if completed.returncode != 0:
            if missing_ok and any(
                token in completed.stderr for token in ("404", "NoSuchKey", "Not Found")
            ):
                return None
            raise self.contract.ManifestError(
                f"{context} failed with status {completed.returncode}"
            )
        document = self.contract.strict_json_loads(completed.stdout or "{}", context)
        if not isinstance(document, dict):
            raise self.contract.ManifestError(f"{context} did not return an object")
        return document

    def _download(self, key: str, etag: str, output: Path) -> None:
        document = self._run(
            [
                "aws", "s3api", "get-object", "--bucket", self.contract.BUCKET,
                "--key", key, "--if-match", etag,
                "--endpoint-url", self.endpoint_url, "--checksum-mode", "ENABLED",
                "--output", "json", str(output),
            ],
            "quarantine exact current GET",
        )
        if document is None or not output.is_file() or output.is_symlink():
            raise self.contract.ManifestError("quarantine GET did not produce exact bytes")

    def head(self, key: str) -> dict[str, object] | None:
        document = self._run(
            [
                "aws", "s3api", "head-object", "--bucket", self.contract.BUCKET,
                "--key", key, "--endpoint-url", self.endpoint_url,
                "--checksum-mode", "ENABLED", "--output", "json",
            ],
            "quarantine current preimage HEAD", missing_ok=True,
        )
        if document is None:
            return None
        etag = self.contract._normalize_etag(
            document.get("ETag"), "quarantine current preimage"
        )
        byte_count = document.get("ContentLength")
        if not isinstance(byte_count, int) or isinstance(byte_count, bool) or byte_count < 0:
            raise self.contract.ManifestError("quarantine HEAD byte count is invalid")
        with tempfile.TemporaryDirectory(prefix="trekdex-quarantine-read-") as directory:
            output = Path(directory) / "object"
            self._download(key, etag, output)
            sha256 = file_digest(output, "sha256")
            actual_bytes = output.stat().st_size
        if actual_bytes != byte_count:
            raise self.contract.ManifestError("quarantine HEAD/GET byte count mismatch")
        checksum_value = document.get("ChecksumSHA256")
        if checksum_value is not None and self.contract.normalize_sha256_checksum(
            checksum_value, "quarantine current preimage"
        ) != sha256:
            raise self.contract.ManifestError("quarantine HEAD/GET SHA-256 mismatch")
        return {"etag": etag, "bytes": actual_bytes, "sha256": sha256}

    def copy_no_clobber(
        self,
        source: str,
        destination: str,
        *,
        source_preimage: dict[str, object],
        attempt_id: str,
    ) -> dict[str, object]:
        from urllib.parse import quote
        if self.head(source) != source_preimage or self.head(destination) is not None:
            raise self.contract.ConditionalRequestRefused(
                "quarantine copy precondition changed"
            )
        copy_source = quote(f"{self.contract.BUCKET}/{source}", safe="/")
        document = self._run(
            [
                "aws", "s3api", "copy-object", "--bucket", self.contract.BUCKET,
                "--copy-source", copy_source, "--key", destination,
                "--copy-source-if-match", str(source_preimage["etag"]),
                "--if-none-match", "*", "--checksum-algorithm", "SHA256",
                "--metadata-directive", "COPY", "--endpoint-url", self.endpoint_url,
                "--output", "json",
            ],
            "no-clobber current-object quarantine copy",
        )
        assert document is not None
        if self.head(destination) != source_preimage:
            raise self.contract.ManifestError("quarantine copy readback mismatch")
        return {
            "transaction_id": "r2-copy-" + self.contract.canonical_sha256({
                "attempt_id": attempt_id, "source": source,
                "destination": destination, "provider_result": document,
            })[:32],
            "attempt_id": attempt_id,
        }

    def remove_after_exact_proof(
        self,
        source: str,
        *,
        source_preimage: dict[str, object],
        attempt_id: str,
    ) -> dict[str, object]:
        if self.head(source) != source_preimage:
            raise self.contract.ConditionalRequestRefused(
                "quarantine source changed before removal"
            )
        raise self.contract.ManifestError(
            "quarantine source removal is disabled: no separately approved "
            "documented atomic primitive is configured"
        )


def apply_quarantine(args: argparse.Namespace) -> int:
    contract = _contract_module()
    if args.terminal_receipt.exists() or args.terminal_receipt.is_symlink():
        raise contract.ManifestError(
            "existing terminal/journal requires explicit reconciliation; automatic retry is forbidden"
        )
    inventory = _read_contract_json(args.inventory, "inventory receipt")
    plan = _read_contract_json(args.plan, "quarantine plan")
    contract.validate_quarantine_plan(plan, inventory_receipt=inventory)
    if plan["plan_sha256"] != args.approved_quarantine_plan_sha256:
        raise contract.ManifestError("apply refused: plan hash is not the exact reviewed value")
    assertion = contract.strict_json_loads(
        os.environ.get("R2_ROOT_APPROVAL_ASSERTION_JSON", ""),
        "root approval assertion",
    )
    if not isinstance(assertion, dict):
        raise contract.ManifestError("root approval assertion is not an object")
    receipt = contract.execute_quarantine(
        plan,
        AwsQuarantineStore(
            args.endpoint_url, os.environ.get("R2_ACCOUNT_ID", "")
        ),
        approval_assertion=assertion,
        approval_key=os.environ.get("R2_ROOT_APPROVAL_HMAC_KEY", ""),
        checkpoint=lambda document: _write_contract_json(
            args.terminal_receipt, document, replace=True
        ),
    )
    _write_contract_json(args.terminal_receipt, receipt, replace=True)
    print(
        f"quarantine terminal={receipt['terminal_state']} "
        f"sha256={receipt['receipt_sha256']} final_deletion_authorized=false"
    )
    return 0 if receipt["terminal_state"] == contract.TerminalState.COMPLETE_SUCCESS.value else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan")
    plan.add_argument("--inventory", required=True, type=Path)
    plan.add_argument("--index", default=DEFAULT_INDEX, type=Path)
    plan.add_argument("--registry", default=DEFAULT_REGISTRY, type=Path)
    plan.add_argument("--source-sha", required=True)
    plan.add_argument("--source-tree", required=True)
    plan.add_argument("--workflow-sha256", required=True)
    plan.add_argument("--root-approval-ref", required=True)
    plan.add_argument("--receipt-id", required=True)
    plan.add_argument("--output", required=True, type=Path)
    plan.add_argument("--approved-quarantine-plan-sha256")
    apply_command = commands.add_parser("apply-quarantine")
    apply_command.add_argument("--inventory", required=True, type=Path)
    apply_command.add_argument("--plan", required=True, type=Path)
    apply_command.add_argument("--approved-quarantine-plan-sha256", required=True)
    apply_command.add_argument("--endpoint-url", required=True)
    apply_command.add_argument("--terminal-receipt", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.command == "plan":
        return plan_quarantine(args)
    return apply_quarantine(args)


if __name__ == "__main__":
    raise SystemExit(main())
