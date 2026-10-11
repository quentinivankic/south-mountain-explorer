#!/usr/bin/env python3
"""Plan or apply fail-closed R2 orphan quarantine.

The active policy lives only in :mod:`r2_release_contract`.  This consumer can
build a canonical plan or perform no-clobber copy -> exact verification ->
conditional source removal.  It has no final-deletion operation.  Provider
behavior remains a separately gated external proof; local tests inject fakes.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

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
    """Provider adapter that selects and removes only the reviewed version."""

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
            if missing_ok and any(token in completed.stderr for token in ("404", "NoSuchKey", "Not Found")):
                return None
            raise self.contract.ManifestError(f"{context} failed with status {completed.returncode}")
        document = self.contract.strict_json_loads(completed.stdout or "{}", context)
        if not isinstance(document, dict):
            raise self.contract.ManifestError(f"{context} did not return an object")
        return document

    def _request_id(self, document: dict[str, object], context: str) -> str:
        metadata = document.get("ResponseMetadata")
        candidate = metadata.get("RequestId") if isinstance(metadata, dict) else document.get("RequestId")
        if not isinstance(candidate, str) or not candidate:
            raise self.contract.ManifestError(f"{context} lacks distinct request identity")
        return candidate

    def head(self, key: str, *, version_id: str | None = None) -> dict[str, object] | None:
        command = [
            "aws", "s3api", "head-object", "--bucket", self.contract.BUCKET,
            "--key", key, "--endpoint-url", self.endpoint_url,
            "--checksum-mode", "ENABLED", "--output", "json",
        ]
        if version_id is not None:
            command += ["--version-id", version_id]
        document = self._run(
            command, "quarantine exact-version preimage read", missing_ok=True
        )
        if document is None:
            return None
        etag = document.get("ETag")
        if isinstance(etag, str) and len(etag) >= 2 and etag[0] == etag[-1] == '"':
            etag = etag[1:-1]
        return {
            "etag": etag.lower() if isinstance(etag, str) else etag,
            "bytes": document.get("ContentLength"),
            "sha256": self.contract.normalize_sha256_checksum(
                document.get("ChecksumSHA256"), "quarantine provider preimage"
            ),
            "version_id": document.get("VersionId"),
        }

    def copy_no_clobber(
        self,
        source: str,
        destination: str,
        *,
        source_preimage: dict[str, object],
        attempt_id: str,
    ) -> dict[str, object]:
        from urllib.parse import quote
        copy_source = quote(
            f"{self.contract.BUCKET}/{source}", safe="/"
        ) + "?versionId=" + quote(str(source_preimage["version_id"]), safe="")
        document = self._run(
            [
                "aws", "s3api", "copy-object", "--bucket", self.contract.BUCKET,
                "--copy-source", copy_source, "--key", destination,
                "--copy-source-if-match", str(source_preimage["etag"]),
                "--if-none-match", "*", "--checksum-algorithm", "SHA256",
                "--metadata-directive", "COPY", "--endpoint-url", self.endpoint_url,
                "--output", "json",
            ],
            "no-clobber exact-version quarantine copy",
        )
        assert document is not None
        version = document.get("VersionId")
        if not isinstance(version, str) or not version:
            raise self.contract.ManifestError("copy returned no stable destination version")
        return {
            "transaction_id": self._request_id(document, "copy result"),
            "version_id": version,
            "attempt_id": attempt_id,
        }

    def remove_if_match(
        self,
        source: str,
        *,
        source_preimage: dict[str, object],
        attempt_id: str,
    ) -> dict[str, object]:
        document = self._run(
            [
                "aws", "s3api", "delete-object", "--bucket", self.contract.BUCKET,
                "--key", source, "--version-id", str(source_preimage["version_id"]),
                "--if-match", str(source_preimage["etag"]),
                "--endpoint-url", self.endpoint_url, "--output", "json",
            ],
            "conditional exact-version source removal after verified quarantine copy",
        )
        assert document is not None
        returned_version = document.get("VersionId")
        if returned_version != source_preimage["version_id"]:
            raise self.contract.ManifestError("source removal returned the wrong version identity")
        return {
            "transaction_id": self._request_id(document, "source removal result"),
            "version_id": returned_version,
            "attempt_id": attempt_id,
        }


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
