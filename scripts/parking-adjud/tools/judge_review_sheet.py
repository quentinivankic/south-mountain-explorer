#!/usr/bin/env python3
"""Create canonical review evidence from one frozen authority run.

The sheet and its self-hashed receipt are installed only under
``$PADJ_TMP/.review-artifacts/<area>/<run-id>/``. They are deterministic,
owner-only, single-link files and never read live packets, drafts, dossiers,
ladder tiles, maps, or prior loose review output.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import resolve_trust as resolver  # noqa: E402
import review_evidence as review  # noqa: E402
import trust_resolution as tr  # noqa: E402

PADJ_TMP = os.environ.get("PADJ_TMP") or os.path.join(_HERE, "..", "work")


def _assert_no_authority_journal(tmp: str | Path, slug: str) -> None:
    journal = tr.authority_journal_path(tmp, slug)
    if journal.exists():
        raise SystemExit(
            "RECOVERY_REQUIRED: live human-authority journal blocks "
            "review-sheet snapshot/output"
        )


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slug")
    parser.add_argument(
        "--authority-run", required=True,
        help="canonical absolute path to the current-schema frozen prepare run",
    )
    parser.add_argument(
        "--sample", type=int, default=20,
        help="deterministic KEEP sample size bound into the receipt (default 20)",
    )
    parser.add_argument(
        "--open", action="store_true",
        help="open the canonical local sheet after releasing the area lock",
    )
    return parser


def _authority_run(raw: str) -> Path:
    path = Path(raw)
    if (not path.is_absolute() or str(path) != str(path.resolve())):
        raise ValueError("--authority-run must be a canonical absolute path")
    return path


def _preflight_stream_target(
        path: Path, expected_length: int, expected_sha256: str,
        tmp: Path, area: str, run_id: str) -> bool:
    paths = review.artifact_paths(tmp, area, run_id)
    if path != paths["sheet"]:
        raise ValueError("review sheet path is noncanonical")
    try:
        observed = review.review_html_identity_v2(
            resolver.trusted_fs.iter_verified_regular_chunks(
                path, require_owner=True, required_mode=0o600,
                expected_length=expected_length,
                expected_sha256=expected_sha256,
            )
        )
    except FileNotFoundError:
        return False
    except ValueError as error:
        raise ValueError(
            f"existing immutable review artifact has different bytes: {path}: "
            f"{error}"
        ) from error
    if observed != (expected_length, expected_sha256):
        raise ValueError(
            f"existing immutable review artifact has different bytes: {path}"
        )
    return True


def _preflight_bytes_target(path: Path, expected: bytes, tmp: Path,
                            area: str, run_id: str) -> None:
    try:
        current = resolver._review_file_bytes(path, tmp, area, run_id)
    except FileNotFoundError:
        return
    if current != expected:
        raise ValueError(
            f"existing immutable review artifact has different bytes: {path}"
        )


def _main_under_area_lock(args: argparse.Namespace) -> tuple[Path, Path, dict]:
    """Build and install one journal-free frozen review evidence pair."""
    tmp = Path(PADJ_TMP).expanduser().resolve()
    _assert_no_authority_journal(tmp, args.slug)
    run_path = _authority_run(args.authority_run)
    prepare_path = resolver._trusted_artifact_path(
        run_path / "prepare.json", run_path
    )
    prepare = resolver.load_prepare_document(
        prepare_path, expected_area=args.slug
    )
    if resolver.runtime_tmp(run_path, prepare) != tmp:
        raise ValueError("authority run targets a different PADJ_TMP")
    if run_path.name != prepare["run_id"]:
        raise ValueError("authority run directory does not match its run id")
    paths = review.artifact_paths(tmp, args.slug, prepare["run_id"])
    prepare_bytes = resolver._read_bytes_nofollow(prepare_path)
    observed: set[str] = set()

    def read_frozen(relative: str) -> bytes:
        artifact = resolver._trusted_artifact_path(run_path / relative, run_path)
        observed.add(relative)
        return resolver._read_bytes_nofollow(artifact)

    def iter_frozen(relative: str):
        artifact = resolver._trusted_artifact_path(run_path / relative, run_path)
        observed.add(relative)
        return resolver.trusted_fs.iter_verified_regular_chunks(
            artifact, require_owner=True, required_mode=0o600
        )

    render, receipt, consumed = review.build_review_artifacts_v2(
        prepare, prepare_bytes, run_path, read_frozen, iter_frozen,
        args.sample,
    )
    if observed != set(consumed):
        raise ValueError("review source artifact set is not closed")
    receipt_bytes = review.json_bytes(receipt)
    sheet_length = receipt["sheet_length"]
    sheet_sha256 = receipt["sheet_sha256"]

    # Reject every hostile existing target before creating either member. A
    # crash after the sheet but before the receipt leaves no usable authority;
    # an exact retry safely completes the pair without rotating the sheet.
    sheet_exists = _preflight_stream_target(
        paths["sheet"], sheet_length, sheet_sha256,
        tmp, args.slug, prepare["run_id"],
    )
    _preflight_bytes_target(
        paths["receipt"], receipt_bytes, tmp, args.slug, prepare["run_id"]
    )
    _assert_no_authority_journal(tmp, args.slug)
    if not sheet_exists:
        directory_fd = resolver._open_review_artifact_directory(
            tmp, args.slug, prepare["run_id"], create=True
        )
        os.close(directory_fd)
        resolver.trusted_fs.atomic_write_stream(
            paths["sheet"], render(), expected_length=sheet_length,
            expected_sha256=sheet_sha256, mode=0o600,
        )
    resolver._write_review_artifact(
        paths["receipt"], receipt_bytes, tmp, args.slug, prepare["run_id"]
    )
    runtime_context = {
        "version": review.REVIEW_RUNTIME_VERSION,
        "kind": review.REVIEW_RUNTIME_KIND,
        "area": args.slug,
        "source_run_id": prepare["run_id"],
        "source_run_path": str(run_path),
    }
    resolver.trusted_fs.write_idempotent_bytes(
        review.review_runtime_path(paths, run_path),
        review.json_bytes(runtime_context),
    )
    installed_sheet = review.review_html_identity_v2(
        resolver.trusted_fs.iter_verified_regular_chunks(
            paths["sheet"], require_owner=True, required_mode=0o600,
            expected_length=sheet_length, expected_sha256=sheet_sha256,
        )
    )
    if (installed_sheet != (sheet_length, sheet_sha256)
            or resolver._review_file_bytes(
                paths["receipt"], tmp, args.slug, prepare["run_id"]
            ) != receipt_bytes):
        raise ValueError("installed review evidence failed exact-byte verification")
    _assert_no_authority_journal(tmp, args.slug)
    return paths["sheet"], paths["receipt"], receipt


def main(argv=None) -> int:
    args = _argument_parser().parse_args(argv)
    if tr.area_slug(args.slug) != args.slug:
        raise SystemExit("review sheet requires a canonical area slug")
    if args.sample < 0:
        raise SystemExit("--sample must be nonnegative")
    tmp = Path(PADJ_TMP).expanduser().resolve()
    try:
        _authority_run(args.authority_run)
        lock_path = tr.area_lock_path(tmp, args.slug)
        with resolver._open_lock_file(lock_path) as area_lock:
            fcntl.flock(area_lock.fileno(), fcntl.LOCK_EX)
            _assert_no_authority_journal(tmp, args.slug)
            sheet_path, receipt_path, receipt = _main_under_area_lock(args)
            _assert_no_authority_journal(tmp, args.slug)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise SystemExit(str(error)) from error

    print(f"review sheet: {sheet_path}")
    print(f"review receipt: {receipt_path}")
    print(
        f"review receipt sha256: {receipt['receipt_sha256']} "
        f"({len(receipt['items'])} items, "
        f"{len(receipt['sample']['selected_fids'])} sampled KEEPs)"
    )
    if args.open:
        subprocess.run(["open", str(sheet_path)], check=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
