#!/usr/bin/env python3
"""Prepare, inspect, and atomically apply autonomous parking resolutions.

`prepare` writes only an ignored resolver run directory. `status` is read-only.
`apply` is a dry-run unless the literal `--apply` is supplied, and even then it
changes exactly one canonical draft chunk plus its checkpoint. Stores, sidecars,
geom, pools, workflows, and live data remain separate explicit operations.
"""
from __future__ import annotations

import argparse
import copy
import datetime as _dt
import errno
import fcntl
import hashlib
import json
import math
import os
import re
import secrets
import stat
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DATA = ROOT / "scripts" / "parking-adjud" / "data"
DEFAULT_TMP = ROOT / "scripts" / "parking-adjud" / "work"
for value in (str(HERE), str(ROOT / "scripts")):
    if value not in sys.path:
        sys.path.insert(0, value)

import dossier_output  # noqa: E402
import judge_packets  # noqa: E402
from judge_validation import (  # noqa: E402
    canonical_draft_files,
    original_judge_projection,
    validate_authority_receipt,
    validate_verdict_row,
)
import replay_trust  # noqa: E402
import review_evidence as review  # noqa: E402
import trusted_filesystem as trusted_fs  # noqa: E402
import trust_engine  # noqa: E402
import trust_resolution as tr  # noqa: E402

LEGACY_PREPARE_VERSION = 2
PREPARE_VERSION = 3
OUTPUT_SEAL_VERSION = 1
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_TOKEN_RE = re.compile(r"\{([A-Z0-9_]+)\}")
_MODEL_ROLES = ("primary", "challenger", "arbiter")
_AGENT_ROLES = ("challenger", "arbiter")
_NORMATIVE_PATHS = {
    "judge_protocol.md": HERE / "judge_protocol.md",
    "judge_lessons.md": HERE / "judge_lessons.md",
}
_PROMPT_PATHS = {
    "primary": HERE / "judge_agent_prompt.md",
    "challenger": HERE / "trust_challenger_prompt.md",
    "arbiter": HERE / "trust_arbiter_prompt.md",
}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(value: object) -> bytes:
    return (json.dumps(
        value, indent=1, ensure_ascii=False, sort_keys=True, allow_nan=False
    ) + "\n").encode("utf-8")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key {key!r}")
        value[key] = item
    return value


def parse_prepare_bytes(raw: bytes, expected_area: str | None = None, *,
                        allow_legacy: bool = False) -> dict:
    """Strictly parse and validate one canonical prepare.json byte image."""
    if not isinstance(raw, bytes):
        raise ValueError("prepare.json input must be bytes")
    try:
        parsed = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, ValueError) as error:
        raise ValueError(f"prepare.json is not valid strict JSON: {error}") from error
    document = validate_prepare_document(
        parsed, expected_area=expected_area, allow_legacy=allow_legacy
    )
    if raw != _json_bytes(document):
        raise ValueError("prepare.json bytes are noncanonical")
    return document


def _open_directory_fd(path: Path, create: bool = False) -> int:
    return trusted_fs.open_directory_fd(
        path, create=create, create_mode=0o700
    )


def _require_trusted_parent_fd(fd: int, path: Path) -> os.stat_result:
    """Enforce the cooperative same-UID boundary for a canonical parent."""
    return trusted_fs.require_trusted_directory_fd(fd, path)


def _read_bytes_nofollow(path: Path) -> bytes:
    parent_fd = _open_directory_fd(path.parent, create=False)
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(path.name, flags, dir_fd=parent_fd)
        except OSError as error:
            if error.errno == errno.ELOOP:
                raise ValueError(
                    f"no-follow read rejected linked file: {path}"
                ) from error
            raise
        try:
            file_stat = os.fstat(fd)
            trusted_fs.require_trivial_acl_fd(fd, path)
            if not stat.S_ISREG(file_stat.st_mode):
                raise ValueError(f"not a regular file: {path}")
            chunks = []
            while True:
                chunk = os.read(fd, 1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
            trusted_fs.require_trivial_acl_fd(fd, path)
            return b"".join(chunks)
        finally:
            os.close(fd)
    finally:
        os.close(parent_fd)


def _fsync_dir(path: Path) -> None:
    directory = _open_directory_fd(path, create=False)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _atomic_bytes(path: Path, data: bytes) -> None:
    trusted_fs.atomic_write_bytes(path, data)


def _open_lock_file(path: Path):
    return trusted_fs.open_lock_file(path)


def _read_fd_bytes(fd: int) -> bytes:
    os.lseek(fd, 0, os.SEEK_SET)
    chunks = []
    while True:
        chunk = os.read(fd, 1024 * 1024)
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks)


def _require_verified_replace_support() -> None:
    import errno
    import inspect

    trusted_fs._require_acl_platform_support()
    missing_flags = [
        name for name in ("O_DIRECTORY", "O_NOFOLLOW")
        if not hasattr(os, name)
    ]
    supports_dir_fd = getattr(os, "supports_dir_fd", frozenset())
    supported_dir_fd_names = {
        getattr(function, "__name__", "") for function in supports_dir_fd
    }
    missing_dir_fd = [
        name for name in ("open", "mkdir", "stat", "unlink")
        if name not in supported_dir_fd_names
    ]
    supported_nofollow_names = {
        getattr(function, "__name__", "")
        for function in getattr(os, "supports_follow_symlinks", frozenset())
    }
    supports_nofollow_stat = "stat" in supported_nofollow_names
    try:
        replace_parameters = inspect.signature(os.replace).parameters
        replace_has_kwargs = any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in replace_parameters.values()
        )
        replace_has_dir_fd = replace_has_kwargs or all(
            name in replace_parameters
            for name in ("src_dir_fd", "dst_dir_fd")
        )
    except (TypeError, ValueError):
        replace_has_dir_fd = False
    if (os.name != "posix" or missing_flags or missing_dir_fd
            or not supports_nofollow_stat or not replace_has_dir_fd):
        details = []
        if os.name != "posix":
            details.append("POSIX filesystem semantics")
        if missing_flags:
            details.append("flags " + ", ".join(missing_flags))
        if missing_dir_fd:
            details.append("dir_fd for " + ", ".join(missing_dir_fd))
        if not supports_nofollow_stat:
            details.append("no-follow stat")
        if not replace_has_dir_fd:
            details.append("descriptor-relative replace")
        raise OSError(
            errno.ENOTSUP,
            "verified replacement requires " + "; ".join(details),
        )


def _remove_owned_private_entry(parent_fd: int, name: str,
                                owned_stat: os.stat_result) -> None:
    try:
        entry_stat = os.stat(
            name, dir_fd=parent_fd, follow_symlinks=False
        )
    except FileNotFoundError:
        return
    if ((entry_stat.st_dev, entry_stat.st_ino)
            != (owned_stat.st_dev, owned_stat.st_ino)):
        return
    os.unlink(name, dir_fd=parent_fd)


def _replace_verified_nofollow(source: Path, target: Path,
                               expected_sha256: str, *,
                               retire_trusted_source: bool = False) -> None:
    """Install captured source bytes through a private target-directory inode.

    The target parent is a trusted-directory boundary: it must be owned by the
    current euid and not group/world writable. Portable POSIX replace cannot
    bind a later rename to an open fd if a malicious same-UID actor discovers
    and swaps the unpredictable private basename between its final stat and
    the rename. Every same-UID process in that directory must therefore obey
    the canonical lock. Source stages are intentionally retained.
    ``retire_trusted_source`` is only for a lock-serialized source namespace
    that shares this trust boundary.
    """
    _require_verified_replace_support()
    source_parent = _open_directory_fd(source.parent, create=False)
    target_parent = None
    source_fd = None
    private_fd = None
    target_fd = None
    private_name = None
    private_stat = None
    renamed = False
    cloexec = getattr(os, "O_CLOEXEC", 0)
    try:
        source_flags = os.O_RDONLY | os.O_NOFOLLOW | cloexec
        source_fd = os.open(source.name, source_flags, dir_fd=source_parent)
        source_stat = os.fstat(source_fd)
        if not stat.S_ISREG(source_stat.st_mode):
            raise ValueError(f"verified replacement source is not regular: {source}")
        source_bytes = _read_fd_bytes(source_fd)
        if _sha(source_bytes) != expected_sha256:
            raise ValueError(f"verified replacement source hash is invalid: {source}")

        # No target-directory entry is created until the pinned source bytes pass.
        target_parent = _open_directory_fd(target.parent, create=True)
        _require_trusted_parent_fd(target_parent, target.parent)
        try:
            existing_target_fd = os.open(
                target.name, source_flags, dir_fd=target_parent
            )
        except FileNotFoundError:
            existing_target_fd = None
        if existing_target_fd is not None:
            try:
                trusted_fs.require_trivial_acl_fd(existing_target_fd, target)
            finally:
                os.close(existing_target_fd)
        private_flags = (
            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | cloexec
        )
        for _ in range(128):
            candidate = f".verified-replace-{secrets.token_hex(24)}"
            try:
                private_fd = os.open(
                    candidate, private_flags, 0o600, dir_fd=target_parent
                )
            except FileExistsError:
                continue
            private_name = candidate
            private_stat = os.fstat(private_fd)
            break
        else:
            raise FileExistsError(
                "could not allocate a private verified-replacement entry"
            )

        trusted_fs.require_trivial_acl_fd(private_fd, target)
        view = memoryview(source_bytes)
        while view:
            written = os.write(private_fd, view)
            if written <= 0:
                raise OSError("verified replacement write made no progress")
            view = view[written:]
        os.fsync(private_fd)

        private_stat = os.fstat(private_fd)
        trusted_fs.require_trivial_acl_fd(private_fd, target)
        private_bytes = _read_fd_bytes(private_fd)
        entry_stat = os.stat(
            private_name, dir_fd=target_parent, follow_symlinks=False
        )
        private_identity = (private_stat.st_dev, private_stat.st_ino)
        if (not stat.S_ISREG(private_stat.st_mode)
                or private_stat.st_uid != os.geteuid()
                or stat.S_IMODE(private_stat.st_mode) != 0o600
                or private_stat.st_nlink != 1
                or private_stat.st_size != len(source_bytes)
                or len(private_bytes) != len(source_bytes)
                or _sha(private_bytes) != expected_sha256
                or (entry_stat.st_dev, entry_stat.st_ino) != private_identity
                or not stat.S_ISREG(entry_stat.st_mode)
                or entry_stat.st_uid != os.geteuid()
                or stat.S_IMODE(entry_stat.st_mode) != 0o600
                or entry_stat.st_nlink != 1
                or entry_stat.st_size != len(source_bytes)):
            raise ValueError(
                f"private verified replacement is not fd-owned: {target}"
            )

        os.replace(
            private_name, target.name,
            src_dir_fd=target_parent, dst_dir_fd=target_parent,
        )
        renamed = True

        target_fd = os.open(target.name, source_flags, dir_fd=target_parent)
        installed_private_stat = os.fstat(private_fd)
        target_stat = os.fstat(target_fd)
        trusted_fs.require_trivial_acl_fd(private_fd, target)
        trusted_fs.require_trivial_acl_fd(target_fd, target)
        target_entry_stat = os.stat(
            target.name, dir_fd=target_parent, follow_symlinks=False
        )
        target_bytes = _read_fd_bytes(target_fd)
        if (not stat.S_ISREG(installed_private_stat.st_mode)
                or installed_private_stat.st_uid != os.geteuid()
                or stat.S_IMODE(installed_private_stat.st_mode) != 0o600
                or installed_private_stat.st_nlink != 1
                or installed_private_stat.st_size != len(source_bytes)
                or (installed_private_stat.st_dev, installed_private_stat.st_ino)
                != private_identity
                or not stat.S_ISREG(target_stat.st_mode)
                or target_stat.st_uid != os.geteuid()
                or stat.S_IMODE(target_stat.st_mode) != 0o600
                or target_stat.st_nlink != 1
                or target_stat.st_size != len(source_bytes)
                or (target_stat.st_dev, target_stat.st_ino) != private_identity
                or not stat.S_ISREG(target_entry_stat.st_mode)
                or target_entry_stat.st_uid != os.geteuid()
                or stat.S_IMODE(target_entry_stat.st_mode) != 0o600
                or target_entry_stat.st_nlink != 1
                or target_entry_stat.st_size != len(source_bytes)
                or (target_entry_stat.st_dev, target_entry_stat.st_ino)
                != private_identity
                or len(target_bytes) != len(source_bytes)
                or _sha(target_bytes) != expected_sha256):
            raise ValueError(
                f"replacement target is not the verified private inode: {target}"
            )
        os.fsync(target_parent)
        if retire_trusted_source:
            _require_trusted_parent_fd(source_parent, source.parent)
            source_entry_stat = os.stat(
                source.name, dir_fd=source_parent, follow_symlinks=False
            )
            if (not stat.S_ISREG(source_entry_stat.st_mode)
                    or (source_entry_stat.st_dev, source_entry_stat.st_ino)
                    != (source_stat.st_dev, source_stat.st_ino)):
                raise ValueError(
                    f"trusted replacement source changed before retirement: {source}"
                )
            os.unlink(source.name, dir_fd=source_parent)
            os.fsync(source_parent)
    finally:
        try:
            if (not renamed and target_parent is not None
                    and private_name is not None and private_stat is not None):
                _remove_owned_private_entry(
                    target_parent, private_name, private_stat
                )
        finally:
            if target_fd is not None:
                os.close(target_fd)
            if private_fd is not None:
                os.close(private_fd)
            if source_fd is not None:
                os.close(source_fd)
            if target_parent is not None:
                os.close(target_parent)
            os.close(source_parent)


def _replace_nofollow(source: Path, target: Path) -> None:
    """Move one lock-serialized entry across trusted cooperative parents."""
    source_parent = _open_directory_fd(source.parent, create=False)
    target_parent = _open_directory_fd(target.parent, create=True)
    try:
        _require_trusted_parent_fd(source_parent, source.parent)
        _require_trusted_parent_fd(target_parent, target.parent)
        source_fd = os.open(
            source.name,
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            dir_fd=source_parent,
        )
        try:
            trusted_fs.require_trivial_acl_fd(source_fd, source)
        finally:
            os.close(source_fd)
        try:
            existing_target_fd = os.open(
                target.name,
                os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                dir_fd=target_parent,
            )
        except FileNotFoundError:
            existing_target_fd = None
        if existing_target_fd is not None:
            try:
                trusted_fs.require_trivial_acl_fd(existing_target_fd, target)
            finally:
                os.close(existing_target_fd)
        os.replace(
            source.name, target.name,
            src_dir_fd=source_parent, dst_dir_fd=target_parent,
        )
        os.fsync(source_parent)
        if target_parent != source_parent:
            os.fsync(target_parent)
    finally:
        os.close(source_parent)
        os.close(target_parent)


def _atomic_json(path: Path, value: object) -> None:
    _atomic_bytes(path, _json_bytes(value))


def _load_json(path: Path, expected: type = dict):
    value = json.loads(_read_bytes_nofollow(path))
    if not isinstance(value, expected):
        raise ValueError(f"{path} must contain a {expected.__name__}")
    return value


def _write_idempotent(path: Path, data: bytes) -> None:
    parent_fd = _open_directory_fd(path.parent, create=True)
    try:
        _require_trusted_parent_fd(parent_fd, path.parent)
    finally:
        os.close(parent_fd)
    try:
        existing = _read_bytes_nofollow(path)
    except FileNotFoundError:
        _atomic_bytes(path, data)
        return
    if existing != data:
        raise ValueError(f"prepared artifact drifted: {path}")


def _open_review_artifact_directory(tmp: Path, area: str, run_id: str,
                                    create: bool = False) -> int:
    """Open the canonical review namespace through owned 0700 directories."""
    paths = review.artifact_paths(tmp, area, run_id)
    root = paths["root"]
    directory = paths["directory"]
    root_fd = _open_directory_fd(root, create=False)
    fd = root_fd
    try:
        _require_trusted_parent_fd(root_fd, root)
        relative = directory.relative_to(root)
        flags = (os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                 | getattr(os, "O_CLOEXEC", 0))
        opened = root
        for part in relative.parts:
            next_fd = None
            child = opened / part
            try:
                try:
                    next_fd = os.open(part, flags, dir_fd=fd)
                except FileNotFoundError:
                    if not create:
                        raise
                    next_fd = trusted_fs.create_trusted_directory_fd(
                        fd, opened, part, create_mode=0o700
                    )
                value = os.fstat(next_fd)
                trusted_fs.require_trivial_acl_fd(
                    next_fd, child, is_directory=True
                )
                if (not stat.S_ISDIR(value.st_mode)
                        or value.st_uid != os.geteuid()
                        or stat.S_IMODE(value.st_mode) != 0o700):
                    raise ValueError(
                        f"review artifact parent is not an owner-only "
                        f"directory: {child}"
                    )
            except BaseException:
                if next_fd is not None:
                    try:
                        os.close(next_fd)
                    except BaseException:
                        pass
                raise
            previous_fd = fd
            fd = next_fd
            if previous_fd != root_fd:
                os.close(previous_fd)
            opened = child
        if fd == root_fd:
            raise ValueError("review artifact namespace has no private parent")
        _require_trusted_parent_fd(fd, directory)
    except BaseException:
        if fd != root_fd:
            try:
                os.close(fd)
            except BaseException:
                pass
        try:
            os.close(root_fd)
        except BaseException:
            pass
        raise
    try:
        os.close(root_fd)
    except BaseException:
        try:
            os.close(fd)
        except BaseException:
            pass
        raise
    return fd


def _review_file_bytes(path: Path, tmp: Path, area: str, run_id: str) -> bytes:
    paths = review.artifact_paths(tmp, area, run_id)
    if path != paths["directory"] / path.name or path.name not in (
            review.REVIEW_SHEET_NAME, review.REVIEW_RECEIPT_NAME):
        raise ValueError("review artifact path is noncanonical")
    parent_fd = _open_review_artifact_directory(tmp, area, run_id, create=False)
    fd = None
    try:
        flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        fd = os.open(path.name, flags, dir_fd=parent_fd)
        before = os.fstat(fd)
        trusted_fs.require_trivial_acl_fd(fd, path)
        entry_before = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        data = _read_fd_bytes(fd)
        after = os.fstat(fd)
        trusted_fs.require_trivial_acl_fd(fd, path)
        entry_after = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        identity = (before.st_dev, before.st_ino)
        for value in (before, after, entry_before, entry_after):
            if (not stat.S_ISREG(value.st_mode)
                    or value.st_uid != os.geteuid()
                    or stat.S_IMODE(value.st_mode) != 0o600
                    or value.st_nlink != 1
                    or (value.st_dev, value.st_ino) != identity):
                raise ValueError(
                    f"review artifact is not an immutable owner-only single-link file: {path}"
                )
        if before.st_size != len(data) or after.st_size != len(data):
            raise ValueError(f"review artifact changed while reading: {path}")
        return data
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent_fd)


def _write_review_artifact(path: Path, data: bytes, tmp: Path,
                           area: str, run_id: str) -> None:
    """Atomically install one immutable review file, or accept exact bytes."""
    paths = review.artifact_paths(tmp, area, run_id)
    if path != paths["directory"] / path.name or path.name not in (
            review.REVIEW_SHEET_NAME, review.REVIEW_RECEIPT_NAME):
        raise ValueError("review artifact path is noncanonical")
    try:
        existing = _review_file_bytes(path, tmp, area, run_id)
    except FileNotFoundError:
        existing = None
    if existing is not None:
        if existing != data:
            raise ValueError(
                f"existing immutable review artifact has different bytes: {path}"
            )
        return

    parent_fd = _open_review_artifact_directory(
        tmp, area, run_id, create=True
    )
    private_fd = None
    private_name = None
    private_stat = None
    renamed = False
    try:
        flags = (os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                 | getattr(os, "O_CLOEXEC", 0))
        for _ in range(128):
            candidate = f".{path.name}.install-{secrets.token_hex(24)}"
            try:
                private_fd = os.open(
                    candidate, flags, 0o600, dir_fd=parent_fd
                )
            except FileExistsError:
                continue
            private_name = candidate
            private_stat = os.fstat(private_fd)
            break
        else:
            raise FileExistsError(
                "could not allocate a private review-artifact entry"
            )

        trusted_fs.require_trivial_acl_fd(private_fd, path)
        os.fchmod(private_fd, 0o600)
        view = memoryview(data)
        while view:
            written = os.write(private_fd, view)
            if written <= 0:
                raise OSError("review artifact write made no progress")
            view = view[written:]
        os.fsync(private_fd)

        after = os.fstat(private_fd)
        trusted_fs.require_trivial_acl_fd(private_fd, path)
        entry = os.stat(
            private_name, dir_fd=parent_fd, follow_symlinks=False
        )
        identity = (private_stat.st_dev, private_stat.st_ino)
        if (not stat.S_ISREG(private_stat.st_mode)
                or private_stat.st_uid != os.geteuid()
                or stat.S_IMODE(private_stat.st_mode) != 0o600
                or private_stat.st_nlink != 1):
            raise ValueError(
                f"private review artifact is not an owned inode: {path}"
            )
        for value in (after, entry):
            if (not stat.S_ISREG(value.st_mode)
                    or value.st_uid != os.geteuid()
                    or stat.S_IMODE(value.st_mode) != 0o600
                    or value.st_nlink != 1
                    or value.st_size != len(data)
                    or (value.st_dev, value.st_ino) != identity):
                raise ValueError(
                    f"private review artifact is not an owned inode: {path}"
                )
        if _read_fd_bytes(private_fd) != data:
            raise ValueError(f"private review artifact bytes changed: {path}")

        try:
            os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ValueError(
                f"review artifact target appeared before install: {path}"
            )
        os.replace(
            private_name, path.name,
            src_dir_fd=parent_fd, dst_dir_fd=parent_fd,
        )
        renamed = True

        installed = os.stat(
            path.name, dir_fd=parent_fd, follow_symlinks=False
        )
        final_stat = os.fstat(private_fd)
        trusted_fs.require_trivial_acl_fd(private_fd, path)
        if (not stat.S_ISREG(installed.st_mode)
                or installed.st_uid != os.geteuid()
                or stat.S_IMODE(installed.st_mode) != 0o600
                or installed.st_nlink != 1
                or installed.st_size != len(data)
                or (installed.st_dev, installed.st_ino) != identity
                or (final_stat.st_dev, final_stat.st_ino) != identity
                or final_stat.st_nlink != 1
                or final_stat.st_size != len(data)
                or _read_fd_bytes(private_fd) != data):
            raise ValueError(
                f"installed review artifact is not the verified private inode: {path}"
            )
        os.fsync(parent_fd)
    finally:
        if (not renamed and private_name is not None
                and private_stat is not None):
            _remove_owned_private_entry(
                parent_fd, private_name, private_stat
            )
        if private_fd is not None:
            os.close(private_fd)
        os.close(parent_fd)


def _parse_model(value: str) -> dict:
    parts = value.split(":")
    if len(parts) not in (2, 3, 4):
        raise ValueError("model wants ID:FAMILY[:PROVIDER[:BUILD]]")
    parts += ["kiro", "unspecified"][len(parts) - 2:]
    return tr.model_config(parts[0], parts[1], parts[2], parts[3])


def _render_bytes(data: bytes, name: str, bindings: dict[str, str]) -> bytes:
    text = data.decode("utf-8")
    tokens = _TOKEN_RE.findall(text)
    if sorted(tokens) != sorted(bindings):
        raise ValueError(f"{name} placeholders {sorted(tokens)} != {sorted(bindings)}")
    for token, value in bindings.items():
        text = text.replace("{" + token + "}", value)
    if _TOKEN_RE.search(text):
        raise ValueError(f"{name} retains an unresolved placeholder")
    return text.encode("utf-8")


def _render(path: Path, bindings: dict[str, str]) -> bytes:
    return _render_bytes(_read_bytes_nofollow(path), path.name, bindings)


def _normative_document_hashes() -> dict[str, str]:
    return {name: _sha(path.read_bytes()) for name, path in _NORMATIVE_PATHS.items()}


def _prompt_template_hashes() -> dict[str, str]:
    return {role: _sha(path.read_bytes()) for role, path in _PROMPT_PATHS.items()}


def _valid_sha(value: object) -> bool:
    return isinstance(value, str) and _HASH_RE.fullmatch(value) is not None


def _valid_hash_list(value: object) -> bool:
    return (
        isinstance(value, list)
        and all(_valid_sha(item) for item in value)
        and value == sorted(set(value))
    )


def _valid_hash_vector(value: object) -> bool:
    return isinstance(value, list) and bool(value) and all(_valid_sha(item) for item in value)


def _coordinate_value(value: object, minimum: float, maximum: float,
                      message: str) -> float:
    if type(value) is int:
        if value < minimum or value > maximum:
            raise ValueError(message)
        return float(value)
    if type(value) is float:
        if not math.isfinite(value) or value < minimum or value > maximum:
            raise ValueError(message)
        return value
    raise ValueError(message)


def _parse_packet_map_bytes(path: Path, source_bytes: bytes, slug: str,
                            expected_source_generation: dict) -> dict[int, dict]:
    raw = json.loads(source_bytes)
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must contain a dict")
    expected = dossier_output.validate_source_generation(
        expected_source_generation, slug
    )
    packets = {}
    packet_generations = set()
    for key, packet in raw.items():
        if not isinstance(packet, dict) or type(packet.get("fid")) is not int:
            raise ValueError(f"{path}: malformed packet {key!r}")
        if str(packet["fid"]) != str(key) or packet.get("area") != slug:
            raise ValueError(f"{path}: packet key/fid/area mismatch at {key!r}")
        if packet["fid"] in packets:
            raise ValueError(f"{path}: duplicate packet fid {packet['fid']}")
        try:
            packet_generation = dossier_output.validate_source_generation(
                packet.get("source_generation"), slug
            )
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"{path}: packet {key!r} source_generation is invalid: {error}"
            ) from error
        packet_generations.add(tr.sha256_json(packet_generation))
        if packet_generation != expected:
            raise ValueError(
                f"{path}: packet {key!r} generation differs from current capture"
            )
        packets[packet["fid"]] = packet
    if not packets or len(packet_generations) != 1:
        raise ValueError(f"{path}: packets must have exactly one source_generation")
    return packets


def _packet_map(tmp: Path, slug: str,
                expected_source_generation: dict) -> tuple[Path, dict[int, dict], bytes]:
    if tr.area_slug(slug) != slug:
        raise ValueError("packet map requires a canonical separator-free area slug")
    path = _trusted_artifact_path(
        tmp.resolve() / f"{slug}_packets.json", tmp.resolve()
    )
    source_bytes = _read_bytes_nofollow(path)
    packets = _parse_packet_map_bytes(
        path, source_bytes, slug, expected_source_generation
    )
    return path, packets, source_bytes


def _source_chunks(tmp: Path, slug: str, packets: dict[int, dict]) -> list[dict]:
    if tr.authority_journal_path(tmp, slug).exists():
        raise ValueError(
            "RECOVERY_REQUIRED: live human-authority journal blocks resolver source work"
        )
    files = canonical_draft_files(tmp, slug)
    if not files:
        raise ValueError(f"no canonical drafts for {slug}")
    numbered = re.compile(rf"^{re.escape(slug)}_verdict_draft_(\d{{2}})\.json$")
    chunks = []
    for fallback_index, path in enumerate(files):
        match = numbered.fullmatch(path.name)
        if match is None and len(files) != 1:
            raise ValueError("resolver requires numbered drafts when an area has multiple chunks")
        index = int(match.group(1)) if match else fallback_index
        path = _trusted_artifact_path(path, tmp.resolve())
        draft_bytes = _read_bytes_nofollow(path)
        rows = json.loads(draft_bytes)
        if not isinstance(rows, list):
            raise ValueError(f"{path} must contain a list")
        chunk_packets = []
        for row in rows:
            fid = row.get("fid") if isinstance(row, dict) else None
            if fid not in packets:
                raise ValueError(f"{path.name}: unknown fid {fid!r}")
            chunk_packets.append(packets[fid])
        state = judge_packets.inspect_rows(chunk_packets, rows, path.name)
        state["file_sha256"] = _sha(draft_bytes)
        authority_errors = [
            f"fid {row.get('fid')}: {error}"
            for row in rows if isinstance(row, dict)
            for error in validate_authority_receipt(row, tmp)
        ]
        if authority_errors:
            raise ValueError(
                f"{path.name} has invalid human authority: {authority_errors}"
            )
        if state["status"] != "complete":
            raise ValueError(f"{path.name} is not a complete valid chunk: {state['errors']}")
        continuation = tmp / f"{slug}_verdict_continue_{index:02d}.json"
        if continuation.exists():
            raise ValueError(f"{continuation.name} is still live; finish its transaction first")
        checkpoint = _trusted_artifact_path(
            tmp.resolve() / f"{slug}_checkpoint_{index:02d}.json", tmp.resolve()
        )
        checkpoint_bytes = _read_bytes_nofollow(checkpoint)
        manifest = json.loads(checkpoint_bytes)
        if not isinstance(manifest, dict):
            raise ValueError(f"{checkpoint} must contain an object")
        decision_input, missing = judge_packets.decision_fingerprint(chunk_packets)
        if missing or not decision_input:
            raise ValueError(f"chunk {index:02d} missing decision inputs: {missing}")
        manifest_errors = judge_packets.validate_manifest(
            manifest, slug, index, chunk_packets, decision_input, state
        )
        if manifest_errors:
            raise ValueError(f"chunk {index:02d} checkpoint invalid: {manifest_errors}")
        if manifest.get("draft_sha256") != state["file_sha256"]:
            raise ValueError(
                f"chunk {index:02d} checkpoint draft hash does not match canonical draft bytes"
            )
        chunks.append({
            "chunk": index,
            "draft": path,
            "draft_bytes": draft_bytes,
            "checkpoint": checkpoint,
            "checkpoint_bytes": checkpoint_bytes,
            "rows": rows,
            "packets": chunk_packets,
            "state": state,
            "manifest": manifest,
            "decision_input_sha256": decision_input,
        })
    return chunks


def _assignment_id(run_id: str, fid: int, role: str, model: dict,
                   packet_sha: str, template_sha: str,
                   normative_hashes: dict[str, str],
                   source_generation: dict | None = None) -> str:
    identity = {
        "run_id": run_id, "fid": fid, "role": role, "model": model,
        "packet_sha256": packet_sha, "template_sha256": template_sha,
        "normative_document_sha256": normative_hashes,
    }
    if source_generation is not None:
        identity["source_generation"] = source_generation
    return tr.sha256_json(identity)


def _prompt(role: str, run_dir: Path, assignment_id: str, packet_path: Path,
            output_path: Path, model: dict,
            normative_hashes: dict[str, str],
            template_bytes: bytes | None = None) -> bytes:
    template = HERE / f"trust_{role}_prompt.md"
    bindings = {
        "ASSIGNMENT_ID": assignment_id,
        "PACKET_PATH": str(packet_path.resolve()),
        "OUTPUT_PATH": str(output_path.resolve()),
        "MODEL_ID": model["id"],
        "MODEL_FAMILY": model["family"],
        "PROTOCOL_PATH": str((run_dir / "rules" / "judge_protocol.md").resolve()),
        "PROTOCOL_SHA256": normative_hashes["judge_protocol.md"],
        "LESSONS_PATH": str((run_dir / "rules" / "judge_lessons.md").resolve()),
        "LESSONS_SHA256": normative_hashes["judge_lessons.md"],
    }
    if role == "arbiter":
        bindings["EXTERNAL_CATALOG_PATH"] = str(
            (run_dir / "evidence" / "catalog.json").resolve()
        )
    source_bytes = template_bytes if template_bytes is not None else _read_bytes_nofollow(template)
    return _render_bytes(source_bytes, template.name, bindings)


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _trusted_artifact_path(path: Path, root: Path) -> Path:
    trusted_root = root.resolve()
    candidate = path.absolute()
    try:
        relative = candidate.relative_to(trusted_root)
    except ValueError as error:
        raise ValueError(f"artifact path escapes trusted root: {path}") from error
    current = trusted_root
    for part in relative.parts[:-1]:
        current = current / part
        if current.exists() and current.is_symlink():
            raise ValueError(
                f"artifact parent is a symlink (no-follow boundary): {current}"
            )
    if candidate.exists() and candidate.is_symlink():
        raise ValueError(
            f"artifact path is a symlink (no-follow boundary): {candidate}"
        )
    if candidate.exists() and not _is_within(candidate, trusted_root):
        raise ValueError(f"artifact resolves outside trusted root: {candidate}")
    return candidate


def _file_signature(value: os.stat_result) -> dict[str, int]:
    """Return every metadata field that binds one stable source entry."""
    return {
        "dev": value.st_dev,
        "inode": value.st_ino,
        "mode": value.st_mode,
        "nlink": value.st_nlink,
        "size": value.st_size,
        "mtime_ns": value.st_mtime_ns,
        "ctime_ns": value.st_ctime_ns,
    }


def _capture_stable_regular(path: Path, label: str) -> bytes:
    """Read one no-follow source exactly once with fd/entry stability checks."""
    parent_fd = _open_directory_fd(path.parent, create=False)
    fd = None
    try:
        flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        fd = os.open(path.name, flags, dir_fd=parent_fd)
        fd_before = _file_signature(os.fstat(fd))
        entry_before = _file_signature(os.stat(
            path.name, dir_fd=parent_fd, follow_symlinks=False
        ))
        if (not stat.S_ISREG(fd_before["mode"])
                or not stat.S_ISREG(entry_before["mode"])
                or (fd_before["dev"], fd_before["inode"])
                != (entry_before["dev"], entry_before["inode"])):
            raise ValueError(f"{label} is not one stable regular file: {path}")
        data = _read_fd_bytes(fd)
        fd_after = _file_signature(os.fstat(fd))
        try:
            entry_after = _file_signature(os.stat(
                path.name, dir_fd=parent_fd, follow_symlinks=False
            ))
        except FileNotFoundError as error:
            raise ValueError(f"{label} disappeared during secure capture: {path}") from error
        if not (fd_before == entry_before == fd_after == entry_after):
            raise ValueError(f"{label} changed during secure capture: {path}")
        if len(data) != fd_after["size"]:
            raise ValueError(f"{label} size changed during secure capture: {path}")
        return data
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent_fd)


def _json_object_from_capture(data: bytes, path: Path, label: str) -> dict:
    try:
        value = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as error:
        raise ValueError(f"{label} is not valid JSON: {path}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain an object: {path}")
    return value


def _external_source_path(raw: object, root: Path, field: str,
                          evidence_id: str) -> Path:
    if not isinstance(raw, str):
        raise ValueError(
            f"external source {evidence_id} {field} must be an absolute string"
        )
    candidate = Path(raw)
    if (not candidate.is_absolute() or str(candidate) != raw
            or ".." in candidate.parts):
        raise ValueError(
            f"external source {evidence_id} {field} is noncanonical"
        )
    try:
        relative = candidate.relative_to(root)
    except ValueError as error:
        raise ValueError(
            f"external source {evidence_id} {field} escapes the evidence root"
        ) from error
    if not relative.parts:
        raise ValueError(
            f"external source {evidence_id} {field} does not name a file"
        )
    return candidate


def _capture_tile_bytes(root_fd: int, name: str, fid: object, zoom: str) -> bytes:
    """Read one canonical tile once while binding its fd to its live entry."""
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(name, flags, dir_fd=root_fd)
    except OSError as error:
        raise ValueError(f"fid {fid}: cannot open {zoom} tile: {error}") from error
    try:
        fd_before = _file_signature(os.fstat(fd))
        try:
            entry_before = _file_signature(os.stat(
                name, dir_fd=root_fd, follow_symlinks=False
            ))
        except OSError as error:
            raise ValueError(
                f"fid {fid}: {zoom} tile canonical entry cannot be captured: {error}"
            ) from error
        if (not stat.S_ISREG(fd_before["mode"])
                or not stat.S_ISREG(entry_before["mode"])):
            raise ValueError(f"fid {fid}: {zoom} tile is not a regular file")
        if (fd_before["dev"], fd_before["inode"]) != (
                entry_before["dev"], entry_before["inode"]):
            raise ValueError(f"fid {fid}: {zoom} tile changed while it was opened")

        data = _read_fd_bytes(fd)
        fd_after = _file_signature(os.fstat(fd))
        try:
            entry_after = _file_signature(os.stat(
                name, dir_fd=root_fd, follow_symlinks=False
            ))
        except FileNotFoundError as error:
            raise ValueError(
                f"fid {fid}: {zoom} tile disappeared during secure capture"
            ) from error
        except OSError as error:
            raise ValueError(
                f"fid {fid}: {zoom} tile canonical entry changed during secure capture: {error}"
            ) from error
        if (not stat.S_ISREG(fd_after["mode"])
                or not stat.S_ISREG(entry_after["mode"])):
            raise ValueError(
                f"fid {fid}: {zoom} tile stopped being a regular file during secure capture"
            )
        if not (fd_before == fd_after == entry_before == entry_after):
            raise ValueError(f"fid {fid}: {zoom} tile changed during secure capture")
        if len(data) != fd_after["size"]:
            raise ValueError(f"fid {fid}: {zoom} tile size changed during secure capture")
        return data
    finally:
        os.close(fd)


def _capture_packet(packet: dict, tmp: Path, slug: str) -> tuple[dict, dict[str, str], dict[str, bytes]]:
    tiles = packet.get("tiles")
    if not isinstance(tiles, dict):
        raise ValueError(f"fid {packet.get('fid')}: tiles is not an object")
    ladder_root = tmp.resolve() / f"{slug}_ladder"
    root_flags = (os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                  | getattr(os, "O_CLOEXEC", 0))
    try:
        root_fd = os.open(ladder_root, root_flags)
    except OSError as error:
        raise ValueError(f"cannot open trusted ladder directory {ladder_root}: {error}") from error
    payload = {key: copy.deepcopy(value) for key, value in packet.items() if key != "tiles"}
    hashes = {}
    captured = {}
    try:
        for zoom in ("z1", "z2", "z3"):
            raw_path = tiles.get(zoom)
            if not isinstance(raw_path, str):
                raise ValueError(f"fid {packet.get('fid')}: missing {zoom} tile")
            source_path = Path(raw_path)
            if (not source_path.is_absolute()
                    or source_path.parent.absolute() != ladder_root
                    or source_path.suffix.casefold() != ".png"):
                raise ValueError(f"fid {packet.get('fid')}: {zoom} tile is outside canonical ladder")
            data = _capture_tile_bytes(
                root_fd, source_path.name, packet.get("fid"), zoom
            )
            if not data.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError(f"fid {packet.get('fid')}: {zoom} tile is not PNG data")
            captured[zoom] = data
            hashes[zoom] = _sha(data)
    finally:
        os.close(root_fd)
    return payload, hashes, captured


def _capture_packets(tmp: Path, slug: str, packets: dict[int, dict]) -> tuple[dict, dict]:
    captures = {}
    identities = {}
    for fid in sorted(packets):
        payload, tile_hashes, tile_bytes = _capture_packet(packets[fid], tmp, slug)
        packet_sha = tr.sha256_json({"packet": payload, "tile_sha256": tile_hashes})
        captures[fid] = (payload, tile_hashes, tile_bytes, packet_sha)
        identities[str(fid)] = {"packet_sha256": packet_sha, "tile_sha256": tile_hashes}
    capture_id = tr.sha256_json(identities)
    capture_root = tmp.resolve() / ".trust-resolver-captures" / capture_id
    snapshot_packets = {}
    for fid, (payload, tile_hashes, tile_bytes, _packet_sha) in captures.items():
        snapshot = copy.deepcopy(payload)
        snapshot["tiles"] = {}
        for zoom in ("z1", "z2", "z3"):
            path = _trusted_artifact_path(
                capture_root / "tiles" / f"{tile_hashes[zoom]}.png", tmp.resolve()
            )
            _write_idempotent(path, tile_bytes[zoom])
            snapshot["tiles"][zoom] = str(path)
        snapshot_packets[fid] = snapshot
    return captures, snapshot_packets


def _safe_run_root(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    allowed = ROOT / "scripts" / "parking-adjud" / "work"
    if _is_within(resolved, ROOT) and not _is_within(resolved, allowed):
        raise ValueError("resolver run directories inside the repository must be under ignored parking-adjud/work")
    return resolved


def _current_trust_report(tmp: Path, slug: str,
                          generation_capture,
                          packets: dict[int, dict] | None = None,
                          packet_source_bytes: bytes | None = None,
                          chunks: list[dict] | None = None) -> tuple[list[dict], dict]:
    drafts_override = (
        [copy.deepcopy(row) for chunk in chunks for row in chunk["rows"]]
        if chunks is not None else None
    )
    draft_source_bytes = (
        {chunk["draft"].name: chunk["draft_bytes"] for chunk in chunks}
        if chunks is not None else None
    )
    items, corpus, sidecar = replay_trust.load_work_area_locked(
        tmp, slug, generation_capture,
        packets_override=packets,
        packet_source_bytes=packet_source_bytes,
        drafts_override=drafts_override,
        draft_source_bytes=draft_source_bytes,
    )
    ledger = _load_json(DATA / "calibration.json")
    report = trust_engine.build_report(items, ledger, {}, sidecar, corpus, include_items=True)
    return report["items"], report


def _host_external_evidence_catalog(
        tmp: Path, slug: str,
        ) -> tuple[dict[str, dict], dict[str, object]]:
    """Capture each host evidence file once and return private byte buffers."""
    tmp_root = tmp.resolve()
    manifest_path = tmp_root / f"{slug}_external" / "manifest.json"
    try:
        manifest_bytes = _capture_stable_regular(
            manifest_path, "external evidence manifest"
        )
    except FileNotFoundError:
        return {}, {"manifest": None, "sources": {}}
    except OSError as error:
        raise ValueError(
            f"cannot securely capture external evidence manifest {manifest_path}: {error}"
        ) from error
    manifest = _json_object_from_capture(
        manifest_bytes, manifest_path, "external evidence manifest"
    )
    fetched_at = manifest.get("fetched_at")
    if (manifest.get("kind") != "trekdex-frozen-bulk-evidence"
            or manifest.get("area") != slug
            or tr._utc_timestamp(fetched_at) is None
            or not isinstance(manifest.get("sources"), list)):
        raise ValueError(f"{manifest_path}: invalid frozen evidence manifest")
    root = manifest_path.parent
    manifest_sha = _sha(manifest_bytes)
    catalog = {}
    seen_hashes = set()
    captured_by_path: dict[Path, bytes] = {manifest_path: manifest_bytes}
    captured_sources: dict[str, dict[str, bytes]] = {}
    required = {
        "id", "path", "sha256", "query_endpoint", "item_metadata_path",
        "item_metadata_sha256",
    }

    def capture_once(path: Path, label: str) -> bytes:
        if path not in captured_by_path:
            try:
                captured_by_path[path] = _capture_stable_regular(path, label)
            except (OSError, ValueError) as error:
                raise ValueError(f"{manifest_path}: {label} is invalid: {error}") from error
        return captured_by_path[path]

    for index, source in enumerate(manifest["sources"]):
        if not isinstance(source, dict) or not required.issubset(source):
            raise ValueError(f"{manifest_path}: source {index} is malformed")
        evidence_id = source.get("id")
        evidence_hash = source.get("sha256")
        if (not isinstance(evidence_id, str)
                or tr.identity_key(evidence_id) != evidence_id
                or not _valid_sha(evidence_hash)):
            raise ValueError(f"{manifest_path}: source {index} identity is invalid")
        if evidence_id in catalog or evidence_hash in seen_hashes:
            raise ValueError(f"{manifest_path}: duplicate source id or hash")
        seen_hashes.add(evidence_hash)
        artifact = _external_source_path(
            source.get("path"), root, "path", evidence_id
        )
        metadata_path = _external_source_path(
            source.get("item_metadata_path"), root,
            "item_metadata_path", evidence_id,
        )
        metadata_hash = source.get("item_metadata_sha256")
        if not _valid_sha(metadata_hash):
            raise ValueError(
                f"{manifest_path}: source {evidence_id} metadata hash is invalid"
            )
        artifact_bytes = capture_once(
            artifact, f"source {evidence_id} artifact"
        )
        if _sha(artifact_bytes) != evidence_hash:
            raise ValueError(f"{manifest_path}: source {evidence_id} bytes are invalid")
        metadata_bytes = capture_once(
            metadata_path, f"source {evidence_id} metadata"
        )
        if _sha(metadata_bytes) != metadata_hash:
            raise ValueError(f"{manifest_path}: source {evidence_id} metadata is invalid")
        locator = source.get("query_endpoint")
        if not tr._source_locator(locator):
            raise ValueError(f"{manifest_path}: source {evidence_id} locator is invalid")
        metadata = _json_object_from_capture(
            metadata_bytes, metadata_path, f"source {evidence_id} metadata"
        )
        modified = metadata.get("modified")
        if modified is None:
            source_updated_at = None
        elif type(modified) is int and modified >= 0:
            try:
                source_updated = (
                    _dt.datetime(1970, 1, 1, tzinfo=_dt.timezone.utc)
                    + _dt.timedelta(milliseconds=modified)
                )
            except (OverflowError, OSError, ValueError) as error:
                raise ValueError(
                    f"{manifest_path}: source {evidence_id} modified time is invalid"
                ) from error
            if source_updated > tr._utc_timestamp(fetched_at):
                raise ValueError(
                    f"{manifest_path}: source {evidence_id} modified after retrieval"
                )
            source_updated_at = source_updated.isoformat()
        else:
            raise ValueError(f"{manifest_path}: source {evidence_id} modified time is invalid")
        catalog[evidence_id] = {
            "id": evidence_id,
            "path": str(artifact),
            "sha256": evidence_hash,
            "source_locator": locator,
            "retrieved_at": fetched_at,
            "source_updated_at": source_updated_at,
            "manifest_sha256": manifest_sha,
            "manifest_path": str(manifest_path),
            "metadata_sha256": metadata_hash,
            "metadata_path": str(metadata_path),
        }
        captured_sources[evidence_id] = {
            "artifact": artifact_bytes,
            "metadata": metadata_bytes,
        }
    return catalog, {
        "manifest": manifest_bytes,
        "sources": captured_sources,
    }


def _bound_external_evidence_catalog(source_catalog: dict[str, dict]) -> dict[str, dict]:
    return {
        evidence_id: {
            "id": evidence_id,
            "sha256": source["sha256"],
            "source_locator": source["source_locator"],
            "retrieved_at": source["retrieved_at"],
            "source_updated_at": source["source_updated_at"],
            "frozen_path": f"evidence/{source['sha256']}.bin",
            "manifest_sha256": source["manifest_sha256"],
            "manifest_frozen_path": f"evidence/{source['manifest_sha256']}.manifest.json",
            "metadata_sha256": source["metadata_sha256"],
            "metadata_frozen_path": f"evidence/{source['metadata_sha256']}.metadata.json",
        }
        for evidence_id, source in sorted(source_catalog.items())
    }


def _publication_source(tmp: Path, slug: str, packets: dict[int, dict],
                        chunks: list[dict], generation_capture) -> dict:
    tmp_root = tmp.resolve()
    dossier_path = tmp_root / f"{slug}_dossier.json"
    public_set_path = _trusted_artifact_path(
        tmp_root / f"{slug}_pub.txt", tmp_root
    )
    dossier = dossier_output.captured_generation_document(
        generation_capture, "dossier"
    )
    source_generation = dossier_output.portable_source_generation(
        generation_capture, slug
    )
    if not isinstance(dossier, dict):
        raise ValueError(f"{dossier_path}: dossier must be an object")
    public_set_bytes = _read_bytes_nofollow(public_set_path)
    facilities = dossier.get("facilities")
    if dossier.get("slug") != slug or not isinstance(facilities, list):
        raise ValueError(f"{dossier_path}: invalid publication dossier")
    by_fid = {}
    for facility in facilities:
        if not isinstance(facility, dict) or type(facility.get("fid")) is not int:
            raise ValueError(f"{dossier_path}: malformed facility")
        fid = facility["fid"]
        if fid in by_fid:
            raise ValueError(f"{dossier_path}: duplicate fid {fid}")
        by_fid[fid] = facility
    try:
        public_fids = [
            int(value) for value in public_set_bytes.decode("utf-8").split(",")
            if value.strip()
        ]
    except (OSError, ValueError) as error:
        raise ValueError(f"{public_set_path}: invalid judge set: {error}") from error
    draft_rows = [row for chunk in chunks for row in chunk["rows"]]
    draft_fids = [row["fid"] for row in draft_rows]
    if public_fids != draft_fids:
        raise ValueError("publication judge set does not exactly match canonical draft order")
    normalized = {}
    for row in draft_rows:
        fid = row["fid"]
        packet = packets[fid]
        facility = by_fid.get(fid)
        if facility is None:
            raise ValueError(f"{dossier_path}: missing publication facility {fid}")
        for field in ("osm", "prior"):
            if facility.get(field) != packet.get(field):
                raise ValueError(f"{dossier_path}: fid {fid} {field} differs from packet")
        if row.get("osm") != packet.get("osm") or row.get("prior") != packet.get("prior"):
            raise ValueError(f"fid {fid}: draft identity differs from packet")
        latitude = _coordinate_value(
            facility.get("lat"), -90.0, 90.0,
            f"{dossier_path}: fid {fid} latitude is out of range",
        )
        longitude = _coordinate_value(
            facility.get("lon"), -180.0, 180.0,
            f"{dossier_path}: fid {fid} longitude is out of range",
        )
        raw_rings = facility.get("rings")
        raw_ring = facility.get("ring")
        if "rings" in facility and not isinstance(raw_rings, list):
            raise ValueError(f"{dossier_path}: fid {fid} rings must be a list")
        if "ring" in facility and not isinstance(raw_ring, list):
            raise ValueError(f"{dossier_path}: fid {fid} ring must be a list")
        rings = raw_rings or ([raw_ring] if raw_ring else [])
        normalized_rings = []
        for ring in rings:
            if not isinstance(ring, list):
                raise ValueError(f"{dossier_path}: fid {fid} ring is invalid")
            normalized_ring = []
            for point in ring:
                if (not isinstance(point, list) or len(point) != 2):
                    raise ValueError(f"{dossier_path}: fid {fid} ring point is invalid")
                point_lat = _coordinate_value(
                    point[0], -90.0, 90.0,
                    f"{dossier_path}: fid {fid} ring point is out of range",
                )
                point_lon = _coordinate_value(
                    point[1], -180.0, 180.0,
                    f"{dossier_path}: fid {fid} ring point is out of range",
                )
                normalized_ring.append([round(point_lat, 6), round(point_lon, 6)])
            if normalized_ring:
                normalized_rings.append(normalized_ring)
        tags_union = facility.get("tags_union")
        if "tags_union" not in facility:
            tags_union = {}
        if not isinstance(tags_union, dict):
            raise ValueError(f"{dossier_path}: fid {fid} tags_union must be an object")
        name = tags_union.get("name")
        if name is not None and not isinstance(name, str):
            raise ValueError(f"{dossier_path}: fid {fid} tags_union.name is invalid")
        normalized[str(fid)] = {
            "fid": fid,
            "osm": list(packet["osm"]),
            "prior": packet["prior"],
            "lat": round(latitude, 6),
            "lon": round(longitude, 6),
            "rings": normalized_rings,
            "name": name,
        }
    return {
        "dossier_path": dossier_path.name,
        "dossier_sha256": source_generation["artifact_sha256"]["dossier"],
        "public_set_path": public_set_path.name,
        "public_set_sha256": _sha(public_set_bytes),
        "judge_fids": public_fids,
        "facilities": normalized,
    }


def _prepare_under_area_lock(slug: str, tmp: Path,
                             models: dict[str, dict], generation_capture,
                             run_root: Path | None = None) -> Path:
    source_generation = dossier_output.portable_source_generation(
        generation_capture, slug
    )
    packet_path, source_packets, packet_source_bytes = _packet_map(
        tmp, slug, source_generation
    )
    packet_captures, packets = _capture_packets(tmp, slug, source_packets)
    chunks = _source_chunks(tmp, slug, packets)
    routes, replay = _current_trust_report(
        tmp, slug, generation_capture, packets, packet_source_bytes, chunks
    )
    route_by_fid = {int(item["source_key"]): item for item in routes}
    publication = _publication_source(
        tmp, slug, packets, chunks, generation_capture
    )
    source = {
        "source_generation": source_generation,
        "publication": publication,
        "packets_path": packet_path.name,
        "packets_frozen_path": "source/packets.json",
        "packets_file_sha256": _sha(packet_source_bytes),
        "replay_sha256": tr.sha256_json(replay),
        "drafts": [{
            "chunk": chunk["chunk"],
            "path": chunk["draft"].name,
            "frozen_path": f"source/draft-{chunk['chunk']:02d}.json",
            "file_sha256": _sha(chunk["draft_bytes"]),
            "checkpoint_path": chunk["checkpoint"].name,
            "checkpoint_frozen_path": f"source/checkpoint-{chunk['chunk']:02d}.json",
            "checkpoint_sha256": _sha(chunk["checkpoint_bytes"]),
            "checkpoint_value": copy.deepcopy(chunk["manifest"]),
            "decision_input_sha256": chunk["decision_input_sha256"],
            "judge_row_sha256": chunk["state"]["row_sha256"],
            "resolution_row_sha256": chunk["state"]["resolution_sha256"],
        } for chunk in chunks],
    }
    normative_bytes = {
        name: _read_bytes_nofollow(path) for name, path in _NORMATIVE_PATHS.items()
    }
    template_bytes = {
        role: _read_bytes_nofollow(path) for role, path in _PROMPT_PATHS.items()
    }
    normative_hashes = {name: _sha(data) for name, data in normative_bytes.items()}
    template_hashes = {role: _sha(data) for role, data in template_bytes.items()}
    host_external_catalog, captured_external = (
        _host_external_evidence_catalog(tmp, slug)
    )
    external_catalog = _bound_external_evidence_catalog(host_external_catalog)
    external_catalog_sha = tr.sha256_json(external_catalog)
    chunk_by_fid = {}
    for chunk in chunks:
        for row_index, row in enumerate(chunk["rows"]):
            fid = row["fid"]
            if fid in chunk_by_fid:
                raise ValueError(f"fid {fid} appears in more than one source row")
            chunk_by_fid[fid] = (chunk, row_index, row)
    if set(chunk_by_fid) != set(route_by_fid):
        raise ValueError("prepared source rows do not match current trust routes")

    identity_items = []
    for fid in sorted(route_by_fid):
        chunk, row_index, row = chunk_by_fid[fid]
        packet = packets[fid]
        packet_payload, tile_hashes, tile_bytes, packet_sha = packet_captures[fid]
        input_evidence_hashes = sorted(set(
            list(tile_hashes.values()) + [packet_sha]
        ))
        primary_row, primary_errors = original_judge_projection(row)
        if primary_row is None or primary_errors:
            raise ValueError(f"fid {fid}: cannot recover immutable primary: {primary_errors}")
        assignments = {}
        for role in _AGENT_ROLES:
            assignments[role] = {
                "role": role,
                "model": models[role],
                "packet_sha256": packet_sha,
                "external_evidence_catalog_sha256": external_catalog_sha,
                "source_generation": source_generation,
                "input_evidence_sha256": input_evidence_hashes,
                "prompt_path": f"prompts/fid-{fid:04d}.{role}.txt",
                "output_path": f"inbox/fid-{fid:04d}.{role}.json",
            }
        identity_items.append({
            "fid": fid,
            "chunk": chunk["chunk"],
            "row_index": row_index,
            "route": route_by_fid[fid]["route"],
            "packet_path": f"packets/fid-{fid:04d}.json",
            "packet_sha256": packet_sha,
            "input_evidence_sha256": input_evidence_hashes,
            "primary": {"decision": primary_row},
            "assignments": assignments,
        })
    base = _safe_run_root(run_root or (tmp / "trust-resolver" / slug))
    identity_document = {
        "version": PREPARE_VERSION,
        "policy_id": tr.POLICY_ID,
        "area": slug,
        "tmp": str(tmp.resolve()),
        "run_root": str(base),
        "models": models,
        "external_evidence_catalog": external_catalog,
        "normative_document_sha256": normative_hashes,
        "prompt_template_sha256": template_hashes,
        "source": source,
        "items": identity_items,
    }
    run_id = _run_identity(identity_document)
    run_dir = base / run_id

    chunk_by_fid = {}
    for chunk in chunks:
        for row_index, row in enumerate(chunk["rows"]):
            chunk_by_fid[row["fid"]] = (chunk, row_index, row)
    items = []
    generated: list[tuple[Path, bytes]] = [
        (run_dir / source["packets_frozen_path"], packet_source_bytes),
    ]
    for chunk, source_entry in zip(chunks, source["drafts"]):
        generated.append((run_dir / source_entry["frozen_path"], chunk["draft_bytes"]))
        generated.append((
            run_dir / source_entry["checkpoint_frozen_path"],
            chunk["checkpoint_bytes"],
        ))
    for role, data in template_bytes.items():
        generated.append((run_dir / "templates" / f"{role}.md", data))
    for name, data in normative_bytes.items():
        generated.append((run_dir / "rules" / name, data))
    evidence_artifacts: dict[Path, bytes] = {}

    def add_captured_evidence(relative: str, data: bytes,
                              expected_sha256: str) -> None:
        if _sha(data) != expected_sha256:
            raise ValueError(
                f"captured external evidence changed before run materialization: {relative}"
            )
        output = run_dir / relative
        previous = evidence_artifacts.setdefault(output, data)
        if previous != data:
            raise ValueError(
                f"captured external evidence destination conflicts: {relative}"
            )

    manifest_buffer = captured_external.get("manifest")
    captured_sources = captured_external.get("sources")
    if not isinstance(captured_sources, dict):
        raise ValueError("private external evidence capture map is malformed")
    for evidence_id in host_external_catalog:
        bound = external_catalog[evidence_id]
        source_buffers = captured_sources.get(evidence_id)
        if (not isinstance(source_buffers, dict)
                or not isinstance(source_buffers.get("artifact"), bytes)
                or not isinstance(source_buffers.get("metadata"), bytes)
                or not isinstance(manifest_buffer, bytes)):
            raise ValueError(
                f"private external evidence capture is incomplete: {evidence_id}"
            )
        add_captured_evidence(
            bound["frozen_path"], source_buffers["artifact"], bound["sha256"]
        )
        add_captured_evidence(
            bound["manifest_frozen_path"], manifest_buffer,
            bound["manifest_sha256"],
        )
        add_captured_evidence(
            bound["metadata_frozen_path"], source_buffers["metadata"],
            bound["metadata_sha256"],
        )
    evidence_artifacts[run_dir / "evidence" / "catalog.json"] = _json_bytes(
        external_catalog
    )
    generated.extend(sorted(evidence_artifacts.items(), key=lambda item: str(item[0])))
    for fid in sorted(route_by_fid):
        chunk, row_index, row = chunk_by_fid[fid]
        packet = packets[fid]
        packet_payload, tile_hashes, tile_bytes, packet_sha = packet_captures[fid]
        frozen_tiles = {}
        for zoom in ("z1", "z2", "z3"):
            tile_out = run_dir / "tiles" / f"{tile_hashes[zoom]}.png"
            frozen_tiles[zoom] = str(tile_out.resolve())
            generated.append((tile_out, tile_bytes[zoom]))
        frozen_packet = copy.deepcopy(packet_payload)
        frozen_packet["tiles"] = frozen_tiles
        packet_bytes = _json_bytes(frozen_packet)
        input_evidence_hashes = sorted(set(
            list(tile_hashes.values()) + [packet_sha]
        ))
        packet_out = run_dir / "packets" / f"fid-{fid:04d}.json"
        generated.append((packet_out, packet_bytes))
        primary_assignment = {
            "assignment_id": _assignment_id(
                run_id, fid, "primary", models["primary"], packet_sha,
                template_hashes["primary"], normative_hashes,
                source_generation,
            ),
            "role": "primary",
            "model": models["primary"],
            "packet_sha256": packet_sha,
            "prompt_sha256": template_hashes["primary"],
            "external_evidence_catalog_sha256": external_catalog_sha,
            "normative_document_sha256": normative_hashes,
            "source_generation": source_generation,
            "input_evidence_sha256": input_evidence_hashes,
        }
        primary_row, primary_errors = original_judge_projection(row)
        if primary_row is None or primary_errors:
            raise ValueError(f"fid {fid}: cannot recover immutable primary: {primary_errors}")
        primary = tr.make_envelope("primary", primary_assignment, primary_row)
        assignments = {}
        for role in _AGENT_ROLES:
            assignment_id = _assignment_id(
                run_id, fid, role, models[role], packet_sha,
                template_hashes[role], normative_hashes,
                source_generation,
            )
            output = run_dir / "inbox" / f"fid-{fid:04d}.{role}.json"
            prompt_path = run_dir / "prompts" / f"fid-{fid:04d}.{role}.txt"
            prompt = _prompt(
                role, run_dir, assignment_id, packet_out, output, models[role],
                normative_hashes, template_bytes=template_bytes[role],
            )
            assignment = {
                "assignment_id": assignment_id,
                "role": role,
                "model": models[role],
                "packet_sha256": packet_sha,
                "prompt_sha256": _sha(prompt),
                "external_evidence_catalog_sha256": external_catalog_sha,
                "normative_document_sha256": normative_hashes,
                "source_generation": source_generation,
                "input_evidence_sha256": input_evidence_hashes,
                "prompt_path": str(prompt_path.relative_to(run_dir)),
                "output_path": str(output.relative_to(run_dir)),
            }
            assignments[role] = assignment
            generated.append((prompt_path, prompt))
        items.append({
            "fid": fid,
            "chunk": chunk["chunk"],
            "row_index": row_index,
            "route": route_by_fid[fid]["route"],
            "packet_path": str(packet_out.relative_to(run_dir)),
            "packet_sha256": packet_sha,
            "input_evidence_sha256": input_evidence_hashes,
            "primary": primary,
            "assignments": assignments,
        })
    document = {
        "version": PREPARE_VERSION,
        "kind": "parking-trust-prepare",
        "policy_id": tr.POLICY_ID,
        "run_id": run_id,
        "area": slug,
        "tmp": str(tmp.resolve()),
        "run_root": str(base),
        "models": models,
        "external_evidence_catalog": external_catalog,
        "normative_document_sha256": normative_hashes,
        "prompt_template_sha256": template_hashes,
        "source": source,
        "items": items,
    }
    generated.append((run_dir / "prepare.json", _json_bytes(document)))
    for path, data in generated:
        _trusted_artifact_path(path, run_dir)
        _write_idempotent(path, data)
    completed = load_prepare_document(
        run_dir / "prepare.json", expected_area=slug
    )
    if completed != document:
        raise ValueError("completed prepare run differs from generated identity")
    return run_dir


def prepare(slug: str, tmp: Path, models: dict[str, dict],
            run_root: Path | None = None) -> Path:
    """Capture a resolver run under inventory-shared then area-exclusive locks."""
    inventory_resource = tr.dossier_resource_path(tmp)
    with trusted_fs.locked_resources(
            tmp, [(inventory_resource, fcntl.LOCK_SH)],
            tr.resource_lock_path):
        lock_path = tr.area_lock_path(tmp, slug)
        with _open_lock_file(lock_path) as area_lock:
            # Both leases cover source capture, materialization, and completed
            # run reload. Dossier producers therefore cannot rotate generation.
            fcntl.flock(area_lock.fileno(), fcntl.LOCK_EX)
            journal = tr.authority_journal_path(tmp, slug)
            if journal.exists():
                raise ValueError(
                    "RECOVERY_REQUIRED: live human-authority journal blocks "
                    "resolver prepare"
                )
            generation_capture = dossier_output.capture_generation_locked(
                tmp, slug
            )
            run = _prepare_under_area_lock(
                slug, tmp, models, generation_capture, run_root
            )
            if journal.exists():
                raise ValueError(
                    "RECOVERY_REQUIRED: human-authority recovery began during "
                    "resolver prepare"
                )
            return run


def _identity_item(item: dict) -> dict:
    assignments = item["assignments"]
    return {
        "fid": item["fid"],
        "chunk": item["chunk"],
        "row_index": item["row_index"],
        "route": item["route"],
        "packet_path": item["packet_path"],
        "packet_sha256": item["packet_sha256"],
        "input_evidence_sha256": item["input_evidence_sha256"],
        "primary_decision": copy.deepcopy(item["primary"]["decision"]),
        "assignments": {
            role: {
                "role": assignments[role]["role"],
                "model": assignments[role]["model"],
                "packet_sha256": assignments[role]["packet_sha256"],
                "external_evidence_catalog_sha256": assignments[role]["external_evidence_catalog_sha256"],
                **({"source_generation": assignments[role]["source_generation"]}
                   if "source_generation" in assignments[role] else {}),
                "input_evidence_sha256": assignments[role]["input_evidence_sha256"],
                "prompt_path": assignments[role]["prompt_path"],
                "output_path": assignments[role]["output_path"],
            }
            for role in _AGENT_ROLES
        },
    }


def _run_identity(document: dict) -> str:
    return tr.sha256_json({
        "version": document["version"],
        "policy_id": document["policy_id"],
        "area": document["area"],
        "tmp": document["tmp"],
        "run_root": document["run_root"],
        "source": document["source"],
        "models": document["models"],
        "external_evidence_catalog": document["external_evidence_catalog"],
        "normative_document_sha256": document["normative_document_sha256"],
        "prompt_template_sha256": document["prompt_template_sha256"],
        "items": [_identity_item(item) for item in document["items"]],
    })


def expected_primary_assignment(document: dict, item: dict) -> dict:
    source_generation = document["source"].get("source_generation")
    value = {
        "assignment_id": _assignment_id(
            document["run_id"], item["fid"], "primary",
            document["models"]["primary"], item["packet_sha256"],
            document["prompt_template_sha256"]["primary"],
            document["normative_document_sha256"], source_generation,
        ),
        "role": "primary",
        "model": document["models"]["primary"],
        "packet_sha256": item["packet_sha256"],
        "prompt_sha256": document["prompt_template_sha256"]["primary"],
        "external_evidence_catalog_sha256": tr.sha256_json(
            document["external_evidence_catalog"]
        ),
        "normative_document_sha256": document["normative_document_sha256"],
        "input_evidence_sha256": item["input_evidence_sha256"],
    }
    if source_generation is not None:
        value["source_generation"] = source_generation
    return value


def validate_prepare_document(document: object, expected_area: str | None = None,
                              *, allow_legacy: bool = False) -> dict:
    required = {
        "version", "kind", "policy_id", "run_id", "area", "tmp", "run_root",
        "models", "external_evidence_catalog", "normative_document_sha256",
        "prompt_template_sha256", "source", "items",
    }
    if not isinstance(document, dict) or set(document) != required:
        raise ValueError("prepare.json schema mismatch")
    version = document.get("version")
    legacy = allow_legacy and type(version) is int and version == LEGACY_PREPARE_VERSION
    current = type(version) is int and version == PREPARE_VERSION
    if (not (current or legacy)
            or document.get("kind") != "parking-trust-prepare"
            or document.get("policy_id") != tr.POLICY_ID):
        raise ValueError("prepare.json version/kind/policy mismatch")
    if not _valid_sha(document.get("run_id")):
        raise ValueError("prepare.json run_id is invalid")
    area = document.get("area")
    if not isinstance(area, str) or tr.area_slug(area) != area:
        raise ValueError("prepare.json area is noncanonical")
    if expected_area is not None and area != expected_area:
        raise ValueError("prepare.json area mismatch")
    tmp_value = document.get("tmp")
    if (not isinstance(tmp_value, str) or not Path(tmp_value).is_absolute()
            or str(Path(tmp_value).resolve()) != tmp_value):
        raise ValueError("prepare.json tmp path is noncanonical")
    run_root_value = document.get("run_root")
    if (not isinstance(run_root_value, str) or not Path(run_root_value).is_absolute()
            or str(_safe_run_root(Path(run_root_value))) != run_root_value):
        raise ValueError("prepare.json run_root path is noncanonical")

    models = document.get("models")
    if not isinstance(models, dict) or set(models) != set(_MODEL_ROLES):
        raise ValueError("prepare.json models schema mismatch")
    for role in _MODEL_ROLES:
        model = models.get(role)
        if not isinstance(model, dict) or set(model) != {"id", "family", "provider", "build"}:
            raise ValueError(f"prepare.json {role} model schema mismatch")
        for field in ("id", "family", "provider", "build"):
            value = model.get(field)
            if not isinstance(value, str) or tr.identity_key(value) != value:
                raise ValueError(f"prepare.json {role} model.{field} is noncanonical")
    if len({models[role]["id"] for role in _MODEL_ROLES}) != len(_MODEL_ROLES):
        raise ValueError("prepare.json model ids must be distinct across roles")

    external_catalog = document.get("external_evidence_catalog")
    if not isinstance(external_catalog, dict):
        raise ValueError("prepare.json external evidence catalog is malformed")
    required_catalog_entry = {
        "id", "sha256", "source_locator", "retrieved_at", "source_updated_at",
        "frozen_path", "manifest_sha256", "manifest_frozen_path",
        "metadata_sha256", "metadata_frozen_path",
    }
    seen_catalog_hashes = set()
    for evidence_id, source in external_catalog.items():
        if (not isinstance(evidence_id, str)
                or tr.identity_key(evidence_id) != evidence_id
                or not isinstance(source, dict)
                or set(source) != required_catalog_entry
                or source.get("id") != evidence_id):
            raise ValueError("prepare.json external evidence catalog entry is malformed")
        for field in ("sha256", "manifest_sha256", "metadata_sha256"):
            if not _valid_sha(source.get(field)):
                raise ValueError(f"prepare.json external evidence {evidence_id} {field} is invalid")
        if source["sha256"] in seen_catalog_hashes:
            raise ValueError("prepare.json external evidence catalog repeats content")
        seen_catalog_hashes.add(source["sha256"])
        if (source.get("frozen_path") != f"evidence/{source['sha256']}.bin"
                or source.get("manifest_frozen_path")
                != f"evidence/{source['manifest_sha256']}.manifest.json"
                or source.get("metadata_frozen_path")
                != f"evidence/{source['metadata_sha256']}.metadata.json"):
            raise ValueError(f"prepare.json external evidence {evidence_id} path is noncanonical")
        if not tr._source_locator(source.get("source_locator")):
            raise ValueError(f"prepare.json external evidence {evidence_id} locator is invalid")
        retrieved = tr._utc_timestamp(source.get("retrieved_at"))
        updated_raw = source.get("source_updated_at")
        updated = None if updated_raw is None else tr._utc_timestamp(updated_raw)
        if (retrieved is None or (updated_raw is not None and updated is None)
                or (updated is not None and updated > retrieved)):
            raise ValueError(f"prepare.json external evidence {evidence_id} timestamps are invalid")

    normative_hashes = document.get("normative_document_sha256")
    if (not isinstance(normative_hashes, dict)
            or set(normative_hashes) != set(tr.NORMATIVE_DOCUMENT_NAMES)
            or any(not _valid_sha(normative_hashes.get(name))
                   for name in tr.NORMATIVE_DOCUMENT_NAMES)):
        raise ValueError("prepare.json normative document hashes are malformed")
    template_hashes = document.get("prompt_template_sha256")
    if (not isinstance(template_hashes, dict)
            or set(template_hashes) != set(_MODEL_ROLES)
            or any(not _valid_sha(template_hashes.get(role)) for role in _MODEL_ROLES)):
        raise ValueError("prepare.json prompt template hashes are malformed")

    source = document.get("source")
    items = document.get("items")
    if not isinstance(items, list) or not items or not isinstance(source, dict):
        raise ValueError("prepare.json source/items are malformed")
    required_source = {
        "drafts", "packets_file_sha256", "packets_path",
        "packets_frozen_path", "replay_sha256",
        "publication",
    }
    if current:
        required_source.add("source_generation")
    if set(source) != required_source or not isinstance(source.get("drafts"), list):
        raise ValueError("prepare.json source schema mismatch")
    source_generation = None
    if current:
        source_generation = dossier_output.validate_source_generation(
            source.get("source_generation"), area
        )
    for field in ("packets_file_sha256", "replay_sha256"):
        if not _valid_sha(source.get(field)):
            raise ValueError(f"prepare.json source {field} is invalid")
    if (source.get("packets_path") != f"{area}_packets.json"
            or source.get("packets_frozen_path") != "source/packets.json"):
        raise ValueError("prepare.json source packet paths are noncanonical")
    publication = source.get("publication")
    required_publication = {
        "dossier_path", "dossier_sha256", "public_set_path", "public_set_sha256",
        "judge_fids", "facilities",
    }
    if not isinstance(publication, dict) or set(publication) != required_publication:
        raise ValueError("prepare.json publication source schema mismatch")
    if (publication.get("dossier_path") != f"{area}_dossier.json"
            or publication.get("public_set_path") != f"{area}_pub.txt"
            or not _valid_sha(publication.get("dossier_sha256"))
            or not _valid_sha(publication.get("public_set_sha256"))):
        raise ValueError("prepare.json publication source identity is invalid")
    if (source_generation is not None
            and publication["dossier_sha256"]
            != source_generation["artifact_sha256"]["dossier"]):
        raise ValueError(
            "prepare.json publication dossier hash differs from source_generation"
        )
    judge_fids = publication.get("judge_fids")
    facilities = publication.get("facilities")
    if (not isinstance(judge_fids, list)
            or any(type(fid) is not int for fid in judge_fids)
            or len(judge_fids) != len(set(judge_fids))
            or not isinstance(facilities, dict)
            or set(facilities) != {str(fid) for fid in judge_fids}):
        raise ValueError("prepare.json publication fid set is invalid")
    required_facility = {"fid", "osm", "prior", "lat", "lon", "rings", "name"}
    for fid in judge_fids:
        facility = facilities[str(fid)]
        if (not isinstance(facility, dict) or set(facility) != required_facility
                or facility.get("fid") != fid
                or not isinstance(facility.get("osm"), list)
                or not facility["osm"]
                or any(not isinstance(value, str) or not value for value in facility["osm"])
                or facility.get("prior") not in ("surveyed", "bare")
                or not isinstance(facility.get("rings"), list)
                or facility.get("name") is not None
                and not isinstance(facility.get("name"), str)):
            raise ValueError(f"prepare.json publication facility {fid} is invalid")
        _coordinate_value(
            facility.get("lat"), -90.0, 90.0,
            f"prepare.json publication facility {fid} coordinates are invalid",
        )
        _coordinate_value(
            facility.get("lon"), -180.0, 180.0,
            f"prepare.json publication facility {fid} coordinates are invalid",
        )
        for ring in facility["rings"]:
            if not isinstance(ring, list):
                raise ValueError(
                    f"prepare.json publication facility {fid} ring is invalid"
                )
            for point in ring:
                if not isinstance(point, list) or len(point) != 2:
                    raise ValueError(f"prepare.json publication facility {fid} ring is invalid")
                _coordinate_value(
                    point[0], -90.0, 90.0,
                    f"prepare.json publication facility {fid} ring is invalid",
                )
                _coordinate_value(
                    point[1], -180.0, 180.0,
                    f"prepare.json publication facility {fid} ring is invalid",
                )
    required_draft = {
        "checkpoint_path", "checkpoint_frozen_path", "checkpoint_sha256",
        "checkpoint_value", "chunk", "decision_input_sha256", "file_sha256",
        "frozen_path", "judge_row_sha256", "path", "resolution_row_sha256",
    }
    chunks = []
    for draft in source["drafts"]:
        if not isinstance(draft, dict) or set(draft) != required_draft:
            raise ValueError("prepare.json source draft schema mismatch")
        chunk = draft.get("chunk")
        if not isinstance(chunk, int) or isinstance(chunk, bool) or chunk < 0:
            raise ValueError("prepare.json source draft chunk is invalid")
        chunks.append(chunk)
        for field in (
            "checkpoint_sha256", "decision_input_sha256", "file_sha256",
        ):
            if not _valid_sha(draft.get(field)):
                raise ValueError(f"prepare.json source draft {field} is invalid")
        for field in ("judge_row_sha256", "resolution_row_sha256"):
            if not _valid_hash_vector(draft.get(field)):
                raise ValueError(f"prepare.json source draft {field} is invalid")
        if not isinstance(draft.get("checkpoint_value"), dict):
            raise ValueError("prepare.json source checkpoint_value is malformed")
        if current:
            checkpoint_generation = dossier_output.validate_source_generation(
                draft["checkpoint_value"].get("source_generation"), area
            )
            if checkpoint_generation != source_generation:
                raise ValueError(
                    "prepare.json checkpoint source_generation mismatch"
                )
        expected_draft = f"{area}_verdict_draft_{chunk:02d}.json"
        single_draft = f"{area}_verdict_draft.json"
        if draft.get("path") not in ({single_draft} if len(source["drafts"]) == 1
                                     else set()) | {expected_draft}:
            raise ValueError("prepare.json source draft path is noncanonical")
        if draft.get("checkpoint_path") != f"{area}_checkpoint_{chunk:02d}.json":
            raise ValueError("prepare.json source checkpoint path is noncanonical")
        if (draft.get("frozen_path") != f"source/draft-{chunk:02d}.json"
                or draft.get("checkpoint_frozen_path")
                != f"source/checkpoint-{chunk:02d}.json"):
            raise ValueError("prepare.json frozen source path is noncanonical")
    if sorted(chunks) != list(range(len(chunks))) or len(chunks) != len(set(chunks)):
        raise ValueError("prepare.json source chunks are not contiguous and unique")

    required_item = {
        "fid", "chunk", "row_index", "route", "packet_path", "packet_sha256",
        "input_evidence_sha256", "primary", "assignments",
    }
    required_assignment = {
        "assignment_id", "role", "model", "packet_sha256", "prompt_sha256",
        "external_evidence_catalog_sha256", "normative_document_sha256",
        "input_evidence_sha256", "prompt_path",
        "output_path",
    }
    if current:
        required_assignment.add("source_generation")
    seen_fids = set()
    seen_coordinates = set()
    row_indexes: dict[int, list[int]] = {chunk: [] for chunk in chunks}
    for item in items:
        if not isinstance(item, dict) or set(item) != required_item:
            raise ValueError("prepare.json item schema mismatch")
        fid = item.get("fid")
        chunk = item.get("chunk")
        row_index = item.get("row_index")
        if not isinstance(fid, int) or isinstance(fid, bool) or fid < 0:
            raise ValueError("prepare.json item fid is invalid")
        if fid in seen_fids:
            raise ValueError("prepare.json contains duplicate fids")
        seen_fids.add(fid)
        if chunk not in row_indexes or not isinstance(row_index, int) or isinstance(row_index, bool) or row_index < 0:
            raise ValueError(f"prepare.json fid {fid} chunk/row_index is invalid")
        coordinate = (chunk, row_index)
        if coordinate in seen_coordinates:
            raise ValueError("prepare.json contains duplicate chunk/row coordinates")
        seen_coordinates.add(coordinate)
        row_indexes[chunk].append(row_index)
        if item.get("route") not in tr.ROUTES:
            raise ValueError(f"prepare.json fid {fid} route is invalid")
        if item.get("packet_path") != f"packets/fid-{fid:04d}.json":
            raise ValueError(f"prepare.json fid {fid} packet path is noncanonical")
        if not _valid_sha(item.get("packet_sha256")):
            raise ValueError(f"prepare.json fid {fid} packet hash is invalid")
        if not _valid_hash_list(item.get("input_evidence_sha256")):
            raise ValueError(f"prepare.json fid {fid} input evidence hashes are invalid")
        primary = item.get("primary")
        if not isinstance(primary, dict) or not isinstance(primary.get("decision"), dict):
            raise ValueError(f"prepare.json fid {fid} primary envelope is malformed")
        assignments = item.get("assignments")
        if not isinstance(assignments, dict) or set(assignments) != set(_AGENT_ROLES):
            raise ValueError(f"prepare.json fid {fid} assignments schema mismatch")
        for role in _AGENT_ROLES:
            assignment = assignments.get(role)
            if not isinstance(assignment, dict) or set(assignment) != required_assignment:
                raise ValueError(f"prepare.json fid {fid} {role} assignment schema mismatch")
            if assignment.get("role") != role:
                raise ValueError(f"prepare.json fid {fid} {role} assignment role mismatch")
            if assignment.get("model") != models[role]:
                raise ValueError(f"prepare.json fid {fid} {role} assignment model mismatch")
            if assignment.get("packet_sha256") != item["packet_sha256"]:
                raise ValueError(f"prepare.json fid {fid} {role} assignment packet mismatch")
            if assignment.get("external_evidence_catalog_sha256") != tr.sha256_json(external_catalog):
                raise ValueError(f"prepare.json fid {fid} {role} external catalog mismatch")
            if assignment.get("input_evidence_sha256") != item["input_evidence_sha256"]:
                raise ValueError(f"prepare.json fid {fid} {role} assignment evidence mismatch")
            if assignment.get("normative_document_sha256") != normative_hashes:
                raise ValueError(f"prepare.json fid {fid} {role} normative hashes mismatch")
            if current and assignment.get("source_generation") != source_generation:
                raise ValueError(
                    f"prepare.json fid {fid} {role} source_generation mismatch"
                )
            for field in ("assignment_id", "prompt_sha256"):
                if not _valid_sha(assignment.get(field)):
                    raise ValueError(f"prepare.json fid {fid} {role} {field} is invalid")
            if assignment.get("prompt_path") != f"prompts/fid-{fid:04d}.{role}.txt":
                raise ValueError(f"prepare.json fid {fid} {role} prompt path is noncanonical")
            if assignment.get("output_path") != f"inbox/fid-{fid:04d}.{role}.json":
                raise ValueError(f"prepare.json fid {fid} {role} output path is noncanonical")
    if [item["fid"] for item in items] != judge_fids:
        raise ValueError("prepare.json item order differs from publication judge set")
    for item in items:
        facility = facilities[str(item["fid"])]
        primary_decision = item["primary"]["decision"]
        for field in ("fid", "osm", "prior"):
            if primary_decision.get(field) != facility.get(field):
                raise ValueError(
                    f"prepare.json fid {item['fid']} primary differs from publication {field}"
                )
    draft_by_chunk = {draft["chunk"]: draft for draft in source["drafts"]}
    items_by_chunk = {
        chunk: sorted(
            (item for item in items if item["chunk"] == chunk),
            key=lambda item: item["row_index"],
        )
        for chunk in chunks
    }
    for chunk, indexes in row_indexes.items():
        if sorted(indexes) != list(range(len(indexes))):
            raise ValueError(f"prepare.json chunk {chunk:02d} row indexes are not contiguous")
        source_draft = draft_by_chunk[chunk]
        expected_rows = len(indexes)
        if (len(source_draft["judge_row_sha256"]) != expected_rows
                or len(source_draft["resolution_row_sha256"]) != expected_rows):
            raise ValueError(
                f"prepare.json chunk {chunk:02d} decision vectors do not match item count"
            )
        if current:
            checkpoint_packets = [{
                "fid": item["fid"],
                "area": area,
                "source_generation": copy.deepcopy(source_generation),
            } for item in items_by_chunk[chunk]]
            checkpoint = source_draft["checkpoint_value"]
            checkpoint_errors = judge_packets.validate_manifest_static(
                checkpoint, area, chunk, checkpoint_packets,
                source_draft["decision_input_sha256"],
            )
            if checkpoint_errors:
                raise ValueError(
                    f"prepare.json chunk {chunk:02d} checkpoint invalid: "
                    f"{checkpoint_errors}"
                )
            expected_fids = [item["fid"] for item in items_by_chunk[chunk]]
            if (checkpoint.get("completed") != expected_fids
                    or checkpoint.get("draft_sha256")
                    != source_draft["file_sha256"]
                    or checkpoint.get("judge_row_sha256")
                    != source_draft["judge_row_sha256"]
                    or checkpoint.get("resolution_row_sha256")
                    != source_draft["resolution_row_sha256"]):
                raise ValueError(
                    f"prepare.json chunk {chunk:02d} checkpoint differs from "
                    "its bound source vectors"
                )

    if _run_identity(document) != document.get("run_id"):
        raise ValueError("prepare.json content does not match its run_id")
    for item in items:
        fid = item["fid"]
        expected_primary = expected_primary_assignment(document, item)
        primary = item["primary"]
        decision = primary["decision"]
        packet_identity = {
            key: decision.get(key) for key in ("fid", "area", "osm", "prior")
        }

        def validate_plain(value: dict) -> list[str]:
            return validate_verdict_row(value, packet_identity, allow_override=False)[0]

        primary_errors = tr.validate_envelope(
            primary, "primary", packet_identity, validate_plain,
            expected_assignment=expected_primary,
            expected_packet_sha256=item["packet_sha256"],
            expected_version=tr.VERSION,
        )
        if primary_errors:
            raise ValueError(f"prepare.json fid {fid} primary invalid: {primary_errors}")
        for role in _AGENT_ROLES:
            assignment = item["assignments"][role]
            expected_id = _assignment_id(
                document["run_id"], fid, role, models[role], item["packet_sha256"],
                template_hashes[role], normative_hashes, source_generation,
            )
            if assignment["assignment_id"] != expected_id:
                raise ValueError(f"prepare.json fid {fid} {role} assignment id mismatch")
    return document


def load_prepare_document(path: Path, expected_area: str | None = None, *,
                          allow_legacy: bool = False) -> dict:
    prepare_bytes = _read_bytes_nofollow(path)
    document = parse_prepare_bytes(
        prepare_bytes, expected_area=expected_area, allow_legacy=allow_legacy
    )
    run_dir = path.parent.resolve()
    if run_dir.name != document["run_id"]:
        raise ValueError("prepare.json parent directory does not match run_id")
    if str(run_dir.parent) != document["run_root"]:
        raise ValueError("prepare.json parent root does not match run_root")
    source = document["source"]
    frozen_packets = _trusted_artifact_path(
        run_dir / source["packets_frozen_path"], run_dir
    )
    frozen_packet_bytes = _read_bytes_nofollow(frozen_packets)
    if (not frozen_packets.is_file()
            or _sha(frozen_packet_bytes) != source["packets_file_sha256"]):
        raise ValueError("prepare.json frozen packet source changed")
    source_packets = None
    if document["version"] == PREPARE_VERSION:
        source_packets = _parse_packet_map_bytes(
            frozen_packets, frozen_packet_bytes, document["area"],
            source["source_generation"],
        )
        expected_packet_fids = {item["fid"] for item in document["items"]}
        if set(source_packets) != expected_packet_fids:
            raise ValueError(
                "prepare.json frozen packet source fid set differs from items"
            )
    for source_draft in source["drafts"]:
        frozen_draft = _trusted_artifact_path(
            run_dir / source_draft["frozen_path"], run_dir
        )
        frozen_checkpoint = _trusted_artifact_path(
            run_dir / source_draft["checkpoint_frozen_path"], run_dir
        )
        draft_bytes = _read_bytes_nofollow(frozen_draft)
        checkpoint_bytes = _read_bytes_nofollow(frozen_checkpoint)
        if (not frozen_draft.is_file()
                or _sha(draft_bytes) != source_draft["file_sha256"]
                or not frozen_checkpoint.is_file()
                or _sha(checkpoint_bytes) != source_draft["checkpoint_sha256"]):
            raise ValueError(
                f"prepare.json chunk {source_draft['chunk']:02d} frozen source changed"
            )
        if document["version"] == PREPARE_VERSION:
            try:
                checkpoint_value = json.loads(
                    checkpoint_bytes, object_pairs_hook=_reject_duplicate_keys
                )
            except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
                raise ValueError(
                    f"prepare.json chunk {source_draft['chunk']:02d} "
                    f"frozen checkpoint is invalid: {error}"
                ) from error
            if (not isinstance(checkpoint_value, dict)
                    or checkpoint_value != source_draft["checkpoint_value"]):
                raise ValueError(
                    f"prepare.json chunk {source_draft['chunk']:02d} frozen "
                    "checkpoint differs from checkpoint_value"
                )
    for role, expected_hash in document["prompt_template_sha256"].items():
        frozen_template = _trusted_artifact_path(
            run_dir / "templates" / f"{role}.md", run_dir
        )
        if (not frozen_template.is_file()
                or _sha(_read_bytes_nofollow(frozen_template)) != expected_hash):
            raise ValueError(f"prepare.json frozen {role} template changed")
    for name, expected_hash in document["normative_document_sha256"].items():
        frozen_rule = _trusted_artifact_path(run_dir / "rules" / name, run_dir)
        if (not frozen_rule.is_file()
                or _sha(_read_bytes_nofollow(frozen_rule)) != expected_hash):
            raise ValueError(f"prepare.json frozen normative document {name} changed")
    catalog_path = _trusted_artifact_path(
        run_dir / "evidence" / "catalog.json", run_dir
    )
    try:
        catalog_bytes = _capture_stable_regular(
            catalog_path, "prepared external evidence catalog"
        )
    except (OSError, ValueError) as error:
        raise ValueError(
            f"prepare.json host external evidence catalog artifact changed: {error}"
        ) from error
    if catalog_bytes != _json_bytes(document["external_evidence_catalog"]):
        raise ValueError("prepare.json host external evidence catalog artifact changed")
    for evidence_id, source in document["external_evidence_catalog"].items():
        for path_field, hash_field in (
            ("frozen_path", "sha256"),
            ("manifest_frozen_path", "manifest_sha256"),
            ("metadata_frozen_path", "metadata_sha256"),
        ):
            artifact = _trusted_artifact_path(
                run_dir / source[path_field], run_dir
            )
            try:
                artifact_bytes = _capture_stable_regular(
                    artifact,
                    f"prepared external evidence {evidence_id} {path_field}",
                )
            except (OSError, ValueError) as error:
                raise ValueError(
                    f"prepare.json external evidence {evidence_id} "
                    f"{path_field} bytes changed: {error}"
                ) from error
            if (not _is_within(artifact, run_dir)
                    or _sha(artifact_bytes) != source[hash_field]):
                raise ValueError(
                    f"prepare.json external evidence {evidence_id} {path_field} bytes changed"
                )
    for item in document["items"]:
        packet_path = _trusted_artifact_path(
            run_dir / item["packet_path"], run_dir
        )
        try:
            packet = _load_json(packet_path)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            raise ValueError(
                f"prepare.json fid {item['fid']} frozen packet is unavailable: {error}"
            ) from error
        source_packet = (
            source_packets[item["fid"]]
            if source_packets is not None else packet
        )
        artifact_errors = _verify_prepared_item(
            run_dir, document, item, source_packet
        )
        if artifact_errors:
            raise ValueError(
                f"prepare.json fid {item['fid']} artifacts invalid: {artifact_errors}"
            )
    return document


def _load_prepare(run_dir: Path) -> dict:
    document = load_prepare_document(run_dir / "prepare.json")
    if document.get("policy_id") != tr.POLICY_ID or document.get("run_id") != run_dir.name:
        raise ValueError("prepare.json policy/run identity mismatch")
    return document


def load_review_receipt(review_receipt_path: Path, authority_run: Path,
                        expected_area: str, expected_fid: int | None = None) -> dict:
    """Rebuild and validate one canonical immutable review artifact chain."""
    run_path = authority_run
    if (not run_path.is_absolute() or str(run_path) != str(run_path.resolve())):
        raise ValueError("authority run path must be canonical and absolute")
    prepare = load_prepare_document(
        run_path / "prepare.json", expected_area=expected_area
    )
    tmp = Path(prepare["tmp"])
    expected_paths = review.artifact_paths(tmp, expected_area, prepare["run_id"])
    if (not review_receipt_path.is_absolute()
            or review_receipt_path != expected_paths["receipt"]
            or str(review_receipt_path) != str(review_receipt_path.resolve())):
        raise ValueError(
            "--review-receipt must be the canonical receipt path for the authority run"
        )
    receipt_bytes = _review_file_bytes(
        expected_paths["receipt"], tmp, expected_area, prepare["run_id"]
    )
    try:
        receipt = json.loads(receipt_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("review receipt is not valid JSON") from error
    receipt_errors = review.validate_review_receipt(receipt)
    if receipt_errors:
        raise ValueError(f"invalid review receipt: {receipt_errors}")
    if review.json_bytes(receipt) != receipt_bytes:
        raise ValueError("review receipt bytes are noncanonical")
    sheet_bytes = _review_file_bytes(
        expected_paths["sheet"], tmp, expected_area, prepare["run_id"]
    )
    prepare_path = _trusted_artifact_path(run_path / "prepare.json", run_path)
    prepare_bytes = _read_bytes_nofollow(prepare_path)
    source_artifacts: dict[str, bytes] = {}

    def capture(relative: str) -> bytes:
        artifact = _trusted_artifact_path(run_path / relative, run_path)
        raw = _read_bytes_nofollow(artifact)
        previous = source_artifacts.setdefault(relative, raw)
        if previous != raw:
            raise ValueError(f"frozen review source changed while reading: {relative}")
        return raw

    expected_sheet, expected_receipt, consumed = review.build_review_artifacts(
        prepare, prepare_bytes, run_path, capture,
        receipt["sample"]["requested"], expected_paths["sheet"],
    )
    if receipt != expected_receipt:
        raise ValueError("review receipt does not match the exact frozen authority run")
    if sheet_bytes != expected_sheet:
        raise ValueError("review sheet does not match the exact frozen authority run")
    if set(source_artifacts) != set(consumed):
        raise ValueError("review source artifact capture is not closed")
    selected_item = None
    if expected_fid is not None:
        selected_item = review.review_item(receipt, expected_fid)
    return {
        "prepare": prepare,
        "prepare_bytes": prepare_bytes,
        "receipt": receipt,
        "receipt_bytes": receipt_bytes,
        "receipt_path": expected_paths["receipt"],
        "sheet_bytes": sheet_bytes,
        "sheet_path": expected_paths["sheet"],
        "item": selected_item,
        "source_artifacts": source_artifacts,
    }


def _verify_external(raw: object, catalog: dict[str, dict], run_dir: Path) -> list[dict]:
    if not isinstance(raw, list):
        raise ValueError("external_evidence must be a list")
    result = []
    seen_ids = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict) or set(item) != {"id", "supports"}:
            raise ValueError(
                f"external_evidence[{index}] must contain only id,supports"
            )
        evidence_id = item.get("id")
        if not isinstance(evidence_id, str) or evidence_id not in catalog:
            raise ValueError(
                f"external_evidence[{index}].id is not host-assigned"
            )
        if evidence_id in seen_ids:
            raise ValueError(f"external_evidence repeats host-assigned id {evidence_id}")
        seen_ids.add(evidence_id)
        assigned = catalog[evidence_id]
        for path_field, hash_field in (
            ("frozen_path", "sha256"),
            ("manifest_frozen_path", "manifest_sha256"),
            ("metadata_frozen_path", "metadata_sha256"),
        ):
            path = _trusted_artifact_path(
                run_dir / assigned[path_field], run_dir
            )
            try:
                frozen_bytes = _capture_stable_regular(
                    path, f"host-frozen external evidence {path_field}"
                )
            except (OSError, ValueError) as error:
                raise ValueError(
                    f"external_evidence[{index}] host-frozen {path_field} "
                    f"changed: {error}"
                ) from error
            if (not _is_within(path, run_dir)
                    or _sha(frozen_bytes) != assigned[hash_field]):
                raise ValueError(
                    f"external_evidence[{index}] host-frozen {path_field} changed"
                )
        supports = item.get("supports")
        if not isinstance(supports, list) or not supports:
            raise ValueError(f"external_evidence[{index}].supports must be non-empty")
        seen_supports = set()
        for support_index, support in enumerate(supports):
            if not isinstance(support, dict) or set(support) != {"axis", "call", "claim"}:
                raise ValueError(
                    f"external_evidence[{index}].supports[{support_index}] is malformed"
                )
            axis, call, claim = support["axis"], support["call"], support["claim"]
            if axis not in ("exists", "public", "serves"):
                raise ValueError(f"external_evidence[{index}] support axis is {axis!r}")
            if call not in ("yes", "no", "unclear", "n/a"):
                raise ValueError(f"external_evidence[{index}] support call is {call!r}")
            if not isinstance(claim, str) or not claim.strip():
                raise ValueError(f"external_evidence[{index}] support claim is empty")
            support_key = (axis, call)
            if support_key in seen_supports:
                raise ValueError(
                    f"external_evidence[{index}] repeats support {support_key}"
                )
            seen_supports.add(support_key)
        result.append({
            key: copy.deepcopy(assigned[key])
            for key in (
                "id", "sha256", "source_locator", "retrieved_at",
                "source_updated_at", "frozen_path", "manifest_sha256",
                "manifest_frozen_path", "metadata_sha256",
                "metadata_frozen_path",
            )
        } | {"supports": copy.deepcopy(supports)})
    return result


def _expected_output_paths(prepare_doc: dict) -> list[str]:
    return sorted(
        item["assignments"][role]["output_path"]
        for item in prepare_doc["items"]
        for role in _AGENT_ROLES
    )


def _sealed_output_path(run_dir: Path, relative_output: str) -> Path:
    if Path(relative_output).name != relative_output.split("/")[-1]:
        raise ValueError("sealed output name is noncanonical")
    return _trusted_artifact_path(
        run_dir / "sealed-inbox" / Path(relative_output).name, run_dir
    )


def _resolution_snapshot_sha256(status: dict) -> str:
    return tr.sha256_json({
        "run_id": status.get("run_id"),
        "counts": status.get("counts"),
        "items": status.get("items"),
    })


def _load_output_seal(run_dir: Path, prepare_doc: dict) -> dict | None:
    path = _trusted_artifact_path(run_dir / "output-seal.json", run_dir)
    if not path.exists():
        return None
    seal = _load_json(path)
    required = {
        "version", "kind", "run_id", "outputs",
        "ready_snapshot_sha256", "seal_sha256",
    }
    if set(seal) != required:
        raise ValueError("output seal schema mismatch")
    if (type(seal.get("version")) is not int
            or seal.get("version") != OUTPUT_SEAL_VERSION
            or seal.get("kind") != "parking-trust-output-seal"
            or seal.get("run_id") != prepare_doc["run_id"]):
        raise ValueError("output seal identity mismatch")
    if not _valid_sha(seal.get("ready_snapshot_sha256")):
        raise ValueError("output seal ready snapshot hash is invalid")
    outputs = seal.get("outputs")
    expected_paths = _expected_output_paths(prepare_doc)
    if not isinstance(outputs, dict) or sorted(outputs) != expected_paths:
        raise ValueError("output seal assignment set mismatch")
    body = dict(seal)
    claimed_hash = body.pop("seal_sha256", None)
    if not _valid_sha(claimed_hash) or claimed_hash != tr.sha256_json(body):
        raise ValueError("output seal hash mismatch")
    for relative, expected_hash in outputs.items():
        sealed_path = _sealed_output_path(run_dir, relative)
        if expected_hash is None:
            if sealed_path.exists():
                raise ValueError(f"sealed output unexpectedly exists for {relative}")
            continue
        if not _valid_sha(expected_hash):
            raise ValueError(f"output seal hash is invalid for {relative}")
        if not sealed_path.is_file() or _sha(sealed_path.read_bytes()) != expected_hash:
            raise ValueError(f"sealed output bytes changed for {relative}")
    return seal


def _seal_outputs(run_dir: Path, prepare_doc: dict, ready_status: dict) -> dict:
    existing = _load_output_seal(run_dir, prepare_doc)
    if existing is not None:
        if existing["ready_snapshot_sha256"] != _resolution_snapshot_sha256(ready_status):
            raise ValueError("output seal ready snapshot differs from current resolution")
        return existing
    captured: dict[str, bytes | None] = {}
    outputs = {}
    for relative in _expected_output_paths(prepare_doc):
        live_path = _trusted_artifact_path(run_dir / relative, run_dir)
        data = live_path.read_bytes() if live_path.is_file() else None
        captured[relative] = data
        outputs[relative] = _sha(data) if data is not None else None
    for relative, data in captured.items():
        if data is not None:
            _write_idempotent(_sealed_output_path(run_dir, relative), data)
    for relative, data in captured.items():
        live_path = _trusted_artifact_path(run_dir / relative, run_dir)
        current = live_path.read_bytes() if live_path.is_file() else None
        if current != data:
            raise ValueError(f"live output changed while sealing {relative}")
    body = {
        "version": OUTPUT_SEAL_VERSION,
        "kind": "parking-trust-output-seal",
        "run_id": prepare_doc["run_id"],
        "ready_snapshot_sha256": _resolution_snapshot_sha256(ready_status),
        "outputs": outputs,
    }
    seal = {**body, "seal_sha256": tr.sha256_json(body)}
    seal_path = _trusted_artifact_path(run_dir / "output-seal.json", run_dir)
    _write_idempotent(seal_path, _json_bytes(seal))
    return _load_output_seal(run_dir, prepare_doc)


def _load_agent_envelope(run_dir: Path, item: dict, role: str,
                         packet: dict, external_catalog: dict[str, dict],
                         output_seal: dict | None = None) -> tuple[dict | None, list[str]]:
    assignment = item["assignments"][role]
    expected_output = f"inbox/fid-{item['fid']:04d}.{role}.json"
    if assignment.get("output_path") != expected_output:
        return None, [f"{role} output path is noncanonical"]
    if output_seal is None:
        path = _trusted_artifact_path(run_dir / expected_output, run_dir)
    else:
        sealed_hash = output_seal["outputs"].get(expected_output)
        if sealed_hash is None:
            return None, []
        path = _sealed_output_path(run_dir, expected_output)
    if not _is_within(path, run_dir):
        return None, [f"{role} output path escapes run directory"]
    if not path.exists():
        return None, []
    try:
        raw = _load_json(path)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return None, [f"{role} output invalid: {error}"]
    if set(raw) != {"assignment_id", "decision", "external_evidence"}:
        return None, [f"{role} output has unexpected keys"]
    if raw.get("assignment_id") != assignment["assignment_id"]:
        return None, [f"{role} assignment_id mismatch"]
    if not isinstance(raw.get("decision"), dict):
        return None, [f"{role} decision is not an object"]
    try:
        external = _verify_external(
            raw.get("external_evidence"), external_catalog, run_dir
        )
        envelope = tr.make_envelope(role, assignment, raw["decision"], external)
    except (KeyError, TypeError, ValueError) as error:
        return None, [f"{role} output invalid: {error}"]

    def validate_plain(decision: dict) -> list[str]:
        return validate_verdict_row(decision, packet, allow_override=False)[0]

    errors = tr.validate_envelope(
        envelope, role, packet, validate_plain,
        expected_assignment=assignment,
        expected_packet_sha256=item["packet_sha256"],
        expected_version=tr.VERSION,
    )
    return envelope, errors


def _source_draft_state(prepare_doc: dict, source: dict, run_dir: Path) -> tuple[str, list[str]]:
    tmp_root = Path(prepare_doc["tmp"]).resolve()
    draft = _trusted_artifact_path(tmp_root / source["path"], tmp_root)
    before = source["file_sha256"]
    journal = _trusted_artifact_path(
        run_dir / "transactions" / f"chunk-{source['chunk']:02d}.json", run_dir
    )
    if journal.exists():
        return "recovery", []
    receipt = _trusted_artifact_path(
        run_dir / "receipts" / f"chunk-{source['chunk']:02d}.json", run_dir
    )
    current = _sha(draft.read_bytes()) if draft.exists() else None
    if receipt.exists():
        return "receipt", []
    if current != before:
        return "changed", [f"{draft.name} changed after prepare"]
    checkpoint = _trusted_artifact_path(
        tmp_root / source["checkpoint_path"], tmp_root
    )
    if not checkpoint.exists() or _sha(checkpoint.read_bytes()) != source["checkpoint_sha256"]:
        return "changed", [f"{checkpoint.name} changed after prepare"]
    return "source", []


def _verify_prepared_item(run_dir: Path, prepare_doc: dict, item: dict,
                          packet: dict) -> list[str]:
    errors = []
    expected_packet_rel = f"packets/fid-{item['fid']:04d}.json"
    if item.get("packet_path") != expected_packet_rel:
        errors.append(f"fid {item['fid']}: packet path is noncanonical")
    packet_path = _trusted_artifact_path(
        run_dir / expected_packet_rel, run_dir
    )
    if not _is_within(packet_path, run_dir):
        return errors + [f"fid {item['fid']}: packet path escapes run directory"]
    try:
        frozen_packet = _load_json(packet_path)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return [f"fid {item.get('fid')}: frozen packet invalid: {error}"]
    source_payload = {key: copy.deepcopy(value) for key, value in packet.items() if key != "tiles"}
    frozen_payload = {
        key: copy.deepcopy(value) for key, value in frozen_packet.items() if key != "tiles"
    }
    if frozen_payload != source_payload:
        errors.append(f"fid {item['fid']}: frozen packet payload differs from current packet")
    try:
        packet_hash = tr.packet_sha256(frozen_packet)
        _payload, tile_hashes = tr.packet_components(frozen_packet)
        for zoom, tile_hash in tile_hashes.items():
            expected_tile = str((run_dir / "tiles" / f"{tile_hash}.png").resolve())
            if frozen_packet["tiles"].get(zoom) != expected_tile:
                errors.append(f"fid {item['fid']}: frozen {zoom} tile path is noncanonical")
        input_evidence_hashes = sorted(set(
            list(tile_hashes.values()) + [packet_hash]
        ))
    except ValueError as error:
        errors.append(str(error))
        packet_hash = None
        input_evidence_hashes = []
    if item.get("packet_sha256") != packet_hash:
        errors.append(f"fid {item['fid']}: packet hash mismatch")
    primary = item.get("primary") or {}
    expected_primary = expected_primary_assignment(prepare_doc, item)
    for field in (
        "assignment_id", "role", "model", "packet_sha256", "prompt_sha256",
        "external_evidence_catalog_sha256", "normative_document_sha256",
        "input_evidence_sha256",
    ):
        if primary.get(field) != expected_primary[field]:
            errors.append(f"fid {item['fid']}: primary {field} mismatch")
    if item.get("input_evidence_sha256") != input_evidence_hashes:
        errors.append(f"fid {item['fid']}: item input evidence hashes mismatch")
    for role in _AGENT_ROLES:
        assignment = (item.get("assignments") or {}).get(role)
        if not isinstance(assignment, dict):
            errors.append(f"fid {item['fid']}: missing {role} assignment")
            continue
        template_sha = prepare_doc["prompt_template_sha256"][role]
        expected_id = _assignment_id(
            prepare_doc["run_id"], item["fid"], role,
            prepare_doc["models"][role], item["packet_sha256"], template_sha,
            prepare_doc["normative_document_sha256"],
            prepare_doc["source"].get("source_generation"),
        )
        if assignment.get("assignment_id") != expected_id:
            errors.append(f"fid {item['fid']}: {role} assignment id mismatch")
        if assignment.get("model") != prepare_doc["models"][role]:
            errors.append(f"fid {item['fid']}: {role} model differs from prepare config")
        if assignment.get("external_evidence_catalog_sha256") != tr.sha256_json(
                prepare_doc["external_evidence_catalog"]):
            errors.append(f"fid {item['fid']}: {role} external catalog hash mismatch")
        if assignment.get("normative_document_sha256") != prepare_doc["normative_document_sha256"]:
            errors.append(f"fid {item['fid']}: {role} normative document hashes mismatch")
        if (prepare_doc["version"] == PREPARE_VERSION
                and assignment.get("source_generation")
                != prepare_doc["source"]["source_generation"]):
            errors.append(f"fid {item['fid']}: {role} source generation mismatch")
        if assignment.get("input_evidence_sha256") != input_evidence_hashes:
            errors.append(f"fid {item['fid']}: {role} input evidence hashes mismatch")
        expected_prompt_rel = f"prompts/fid-{item['fid']:04d}.{role}.txt"
        expected_output_rel = f"inbox/fid-{item['fid']:04d}.{role}.json"
        if assignment.get("prompt_path") != expected_prompt_rel:
            errors.append(f"fid {item['fid']}: {role} prompt path is noncanonical")
        if assignment.get("output_path") != expected_output_rel:
            errors.append(f"fid {item['fid']}: {role} output path is noncanonical")
        prompt_path = _trusted_artifact_path(
            run_dir / expected_prompt_rel, run_dir
        )
        output_path = _trusted_artifact_path(
            run_dir / expected_output_rel, run_dir
        )
        if not _is_within(prompt_path, run_dir) or not _is_within(output_path, run_dir):
            errors.append(f"fid {item['fid']}: {role} assignment escapes run directory")
            continue
        try:
            frozen_template = _read_bytes_nofollow(
                run_dir / "templates" / f"{role}.md"
            )
            expected_prompt = _prompt(
                role, run_dir, expected_id, packet_path, output_path,
                prepare_doc["models"][role],
                prepare_doc["normative_document_sha256"],
                template_bytes=frozen_template,
            )
            actual_prompt = prompt_path.read_bytes()
            if actual_prompt != expected_prompt or _sha(actual_prompt) != assignment.get("prompt_sha256"):
                errors.append(f"fid {item['fid']}: {role} prompt bytes/hash mismatch")
        except (OSError, ValueError) as error:
            errors.append(f"fid {item['fid']}: {role} prompt invalid: {error}")
    return errors


def require_current_source_generation(prepare_doc: dict,
                                      generation_capture) -> dict:
    """Require a caller-held live capture to equal a current prepare binding."""
    area = prepare_doc.get("area") if isinstance(prepare_doc, dict) else None
    prepared = dossier_output.validate_source_generation(
        (prepare_doc.get("source") or {}).get("source_generation"), area
    )
    current = dossier_output.portable_source_generation(
        generation_capture, area
    )
    if current != prepared:
        raise ValueError("source generation changed after prepare")
    return current


def evaluate_locked(run_dir: Path, generation_capture) -> tuple[dict, dict]:
    """Evaluate while the caller holds dossier-inventory and area leases."""
    prepare_doc = _load_prepare(run_dir)
    prepared_generation = dossier_output.validate_source_generation(
        prepare_doc["source"].get("source_generation"), prepare_doc["area"]
    )
    current_generation = dossier_output.portable_source_generation(
        generation_capture, prepare_doc["area"]
    )
    generation_errors = []
    if current_generation != prepared_generation:
        generation_errors.append("source generation changed after prepare")
    if tr.authority_journal_path(
            prepare_doc["tmp"], prepare_doc["area"]).exists():
        counts = {
            "items": len(prepare_doc["items"]),
            "pending_challenger": 0,
            "pending_arbiter": 0,
            "autonomous_resolved": 0,
            "preserved_authority": 0,
            "human_exceptions": 0,
            "refresh_required": 0,
            "invalid": 0,
        }
        return {
            "version": 1,
            "kind": "parking-trust-status",
            "run_id": prepare_doc["run_id"],
            "area": prepare_doc["area"],
            "state": "RECOVERY_REQUIRED",
            "read_only": True,
            "output_seal_sha256": None,
            "counts": counts,
            "chunks": [
                {"chunk": source["chunk"], "source_state": "recovery"}
                for source in prepare_doc["source"]["drafts"]
            ],
            "items": [],
            "errors": [
                "live human-authority journal blocks resolver status"
            ],
        }, {}
    seal_errors = []
    try:
        output_seal = _load_output_seal(run_dir, prepare_doc)
    except (OSError, ValueError, json.JSONDecodeError, TypeError) as error:
        seal_errors.append(f"output seal invalid: {error}")
        output_seal = {
            "outputs": {path: None for path in _expected_output_paths(prepare_doc)},
            "seal_sha256": None,
        }
    tmp = Path(prepare_doc["tmp"])
    packet_path, packets, packet_source_bytes = _packet_map(
        tmp, prepare_doc["area"], prepared_generation
    )
    errors = list(generation_errors) + list(seal_errors)
    if _sha(packet_source_bytes) != prepare_doc["source"]["packets_file_sha256"]:
        errors.append("source packets file changed after prepare")
    chunk_states = {}
    for source in prepare_doc["source"]["drafts"]:
        state, state_errors = _source_draft_state(prepare_doc, source, run_dir)
        chunk_states[source["chunk"]] = state
        errors.extend(state_errors)
    internal = {}
    public_items = []
    counts = {
        "items": len(prepare_doc["items"]), "pending_challenger": 0,
        "pending_arbiter": 0, "autonomous_resolved": 0,
        "preserved_authority": 0, "human_exceptions": 0,
        "refresh_required": 0, "invalid": 0,
    }
    for item in prepare_doc["items"]:
        fid = item["fid"]
        source_packet = packets[fid]
        packet = _load_json(run_dir / item["packet_path"])
        primary = item["primary"]

        def validate_plain(decision: dict) -> list[str]:
            return validate_verdict_row(decision, packet, allow_override=False)[0]

        item_errors = _verify_prepared_item(
            run_dir, prepare_doc, item, source_packet
        )
        item_errors.extend(tr.validate_envelope(
            primary, "primary", packet, validate_plain,
            expected_assignment=expected_primary_assignment(prepare_doc, item),
            expected_packet_sha256=item["packet_sha256"],
            expected_version=tr.VERSION,
        ))
        challenger, challenger_errors = _load_agent_envelope(
            run_dir, item, "challenger", packet,
            prepare_doc["external_evidence_catalog"], output_seal,
        )
        arbiter, arbiter_errors = _load_agent_envelope(
            run_dir, item, "arbiter", packet,
            prepare_doc["external_evidence_catalog"], output_seal,
        )
        item_errors.extend(challenger_errors + arbiter_errors)
        if item_errors:
            resolution = {"state": "invalid", "result": None, "selected_role": None,
                          "reason": "; ".join(item_errors)}
            counts["invalid"] += 1
        else:
            resolution = tr.resolve(item["route"], primary, challenger, arbiter)
            if resolution["state"] == "pending":
                if resolution["reason"] == "challenger decision missing":
                    counts["pending_challenger"] += 1
                else:
                    counts["pending_arbiter"] += 1
            elif resolution["state"] == "resolved":
                counts["autonomous_resolved"] += 1
            elif resolution["state"] == "preserved":
                counts["preserved_authority"] += 1
            elif resolution["state"] == "human_exception":
                counts["human_exceptions"] += 1
            elif resolution["state"] == "blocked":
                counts["refresh_required"] += 1
        internal[fid] = {
            "item": item, "packet": packet, "primary": primary,
            "challenger": challenger, "arbiter": arbiter,
            "resolution": resolution, "errors": item_errors,
        }
        public_items.append({
            "fid": fid, "chunk": item["chunk"], "route": item["route"],
            "state": resolution["state"], "result": resolution.get("result"),
            "selected_role": resolution.get("selected_role"),
            "reason": resolution["reason"], "errors": item_errors,
            "model_families": sorted({tr.family(env) for env in (primary, challenger, arbiter)
                                      if env is not None}),
        })
    for source in prepare_doc["source"]["drafts"]:
        if chunk_states.get(source["chunk"]) != "receipt":
            continue
        try:
            values = _chunk_values(internal, source["chunk"])
            transaction_id = _transaction_id(prepare_doc, source, values)
            paths = _artifact_paths(run_dir, prepare_doc, source, transaction_id)
            if _sha(_read_bytes_nofollow(paths["target"])) == source["file_sha256"]:
                receipt_before = _read_bytes_nofollow(paths["target"])
            elif (paths["backup"].exists()
                  and _sha(_read_bytes_nofollow(paths["backup"])) == source["file_sha256"]):
                receipt_before = _read_bytes_nofollow(paths["backup"])
            else:
                raise ValueError("receipt has no exact prepared source or backup")
            plan = _build_plan(
                run_dir, prepare_doc, source, internal, receipt_before
            )
            if plan.get("state") == "PRESERVED":
                _validate_exact_artifact(paths["receipt"], plan["receipt"], "preservation receipt")
                if (_sha(_read_bytes_nofollow(paths["target"])) != source["file_sha256"]
                        or _sha(_read_bytes_nofollow(paths["checkpoint"])) != source["checkpoint_sha256"]):
                    raise ValueError("preservation receipt source hashes changed")
                chunk_states[source["chunk"]] = "preserved"
            elif plan.get("state") == "READY":
                _validate_exact_artifact(paths["receipt"], plan["receipt"], "apply receipt")
                if (_read_bytes_nofollow(paths["target"]) != plan["after_bytes"]
                        or _sha(_read_bytes_nofollow(paths["checkpoint"]))
                        != plan["receipt"]["checkpoint_after_sha256"]):
                    raise ValueError("receipt draft/checkpoint differs from canonical plan")
                chunk_states[source["chunk"]] = "applied"
            else:
                raise ValueError("receipt does not correspond to a terminal canonical plan")
        except (OSError, ValueError, json.JSONDecodeError) as error:
            chunk_states[source["chunk"]] = "recovery"
            errors.append(f"chunk {source['chunk']:02d} receipt invalid: {error}")

    if errors or any(value == "recovery" for value in chunk_states.values()):
        state = "RECOVERY_REQUIRED"
    elif counts["invalid"] or counts["human_exceptions"] or counts["refresh_required"]:
        state = "BLOCKED"
    elif counts["pending_challenger"] or counts["pending_arbiter"]:
        state = "PENDING"
    elif all(value in ("applied", "preserved") for value in chunk_states.values()):
        state = "APPLIED"
    else:
        state = "READY"
    status = {
        "version": 1,
        "kind": "parking-trust-status",
        "run_id": prepare_doc["run_id"],
        "area": prepare_doc["area"],
        "state": state,
        "read_only": True,
        "output_seal_sha256": output_seal.get("seal_sha256") if output_seal else None,
        "counts": counts,
        "chunks": [{"chunk": key, "source_state": value}
                   for key, value in sorted(chunk_states.items())],
        "items": sorted(public_items, key=lambda value: value["fid"]),
        "errors": errors,
    }
    return status, internal


def evaluate(run_dir: Path) -> tuple[dict, dict]:
    """Evaluate current authority under global-before-area shared locks."""
    prepare_doc = _load_prepare(run_dir)
    tmp = Path(prepare_doc["tmp"])
    inventory_resource = tr.dossier_resource_path(tmp)
    with trusted_fs.locked_resources(
            tmp, [(inventory_resource, fcntl.LOCK_SH)],
            tr.resource_lock_path):
        area_lock_path = tr.area_lock_path(tmp, prepare_doc["area"])
        with _open_lock_file(area_lock_path) as area_lock:
            fcntl.flock(area_lock.fileno(), fcntl.LOCK_SH)
            generation_capture = dossier_output.capture_generation_locked(
                tmp, prepare_doc["area"]
            )
            return evaluate_locked(run_dir, generation_capture)


def _manifest_after(source: dict, new_state: dict) -> dict:
    value = copy.deepcopy(source["checkpoint_value"])
    value.update({
        "completed": new_state["completed"],
        "draft_sha256": new_state["file_sha256"],
        "judge_row_sha256": new_state["row_sha256"],
        "resolution_row_sha256": new_state["resolution_sha256"],
    })
    return value


def _chunk_values(internal: dict, chunk_index: int) -> list[dict]:
    return sorted(
        (value for value in internal.values() if value["item"]["chunk"] == chunk_index),
        key=lambda value: value["item"]["row_index"],
    )


def _terminal_vector(values: list[dict]) -> list[dict]:
    vector = []
    for value in values:
        role = value["resolution"].get("selected_role")
        envelope = value.get(role) if role in tr.ROLES else None
        vector.append({
            "fid": value["item"]["fid"],
            "state": value["resolution"]["state"],
            "selected_role": role,
            "decision_sha256": envelope.get("decision_sha256") if envelope else None,
        })
    return vector


def validate_terminal_receipt(receipt: object,
                              expected_terminal: list[dict] | None = None) -> list[str]:
    """Return structural errors without indexing or sorting untrusted values."""
    if not isinstance(receipt, dict):
        return ["terminal receipt schema mismatch"]
    kind = receipt.get("kind")
    preservation = kind == "parking-trust-preservation-receipt"
    applying = kind == "parking-trust-receipt"
    required = ({
        "version", "kind", "run_id", "transaction_id",
        "output_seal_sha256", "chunk", "draft_sha256",
        "checkpoint_sha256", "terminal_fids", "routes_sha256",
    } if preservation else {
        "version", "kind", "transaction_id", "run_id",
        "output_seal_sha256", "chunk", "before_sha256", "after_sha256",
        "checkpoint_after_sha256", "terminal_fids", "applied_fids",
    } if applying else set())
    if not required or set(receipt) != required:
        return ["terminal receipt schema mismatch"]

    errors: list[str] = []
    if type(receipt.get("version")) is not int or receipt.get("version") != 1:
        errors.append("terminal receipt version is invalid")
    if (type(receipt.get("chunk")) is not int
            or not 0 <= receipt["chunk"] <= sys.maxsize):
        errors.append("terminal receipt chunk is invalid")
    hash_fields = (
        ("run_id", "transaction_id", "output_seal_sha256",
         "draft_sha256", "checkpoint_sha256", "routes_sha256")
        if preservation else
        ("run_id", "transaction_id", "output_seal_sha256",
         "before_sha256", "after_sha256", "checkpoint_after_sha256")
    )
    for field in hash_fields:
        if not _valid_sha(receipt.get(field)):
            errors.append(f"terminal receipt {field} is invalid")

    terminal = receipt.get("terminal_fids")
    valid_terminal: list[dict] = []
    if not isinstance(terminal, list):
        errors.append("terminal receipt fid vector is malformed")
    else:
        for entry in terminal:
            if (not isinstance(entry, dict)
                    or set(entry) != {
                        "fid", "state", "selected_role", "decision_sha256"
                    }):
                errors.append("terminal receipt fid entry schema mismatch")
                continue
            fid = entry.get("fid")
            state = entry.get("state")
            role = entry.get("selected_role")
            decision_sha = entry.get("decision_sha256")
            if type(fid) is not int or not 0 <= fid <= sys.maxsize:
                errors.append("terminal receipt fid is invalid")
                continue
            if state == "resolved":
                if role not in tr.ROLES or not _valid_sha(decision_sha):
                    errors.append("terminal receipt resolved entry is invalid")
                    continue
            elif state == "preserved":
                if role is not None or decision_sha is not None:
                    errors.append("terminal receipt preserved entry is invalid")
                    continue
            else:
                errors.append("terminal receipt state is invalid")
                continue
            valid_terminal.append(entry)
        valid_fids = [entry["fid"] for entry in valid_terminal]
        if (len(valid_terminal) != len(terminal)
                or valid_fids != sorted(set(valid_fids))):
            errors.append("terminal receipt fid vector is not sorted and unique")
    if expected_terminal is not None and terminal != expected_terminal:
        errors.append("terminal receipt fid vector differs from the prepared transaction")

    if applying:
        applied = receipt.get("applied_fids")
        valid_applied: list[dict] = []
        if not isinstance(applied, list):
            errors.append("terminal receipt applied fid vector is malformed")
        else:
            for entry in applied:
                if (not isinstance(entry, dict)
                        or set(entry) != {"fid", "decision_sha256"}):
                    errors.append("terminal receipt applied fid entry schema mismatch")
                    continue
                fid = entry.get("fid")
                if (type(fid) is not int or not 0 <= fid <= sys.maxsize
                        or not _valid_sha(entry.get("decision_sha256"))):
                    errors.append("terminal receipt applied fid entry is invalid")
                    continue
                valid_applied.append(entry)
            applied_fids = [entry["fid"] for entry in valid_applied]
            if (len(valid_applied) != len(applied)
                    or applied_fids != sorted(set(applied_fids))):
                errors.append(
                    "terminal receipt applied fid vector is not sorted and unique"
                )
            expected_applied = [
                {"fid": entry["fid"],
                 "decision_sha256": entry["decision_sha256"]}
                for entry in valid_terminal if entry["state"] == "resolved"
            ]
            if applied != expected_applied:
                errors.append(
                    "terminal receipt applied fid vector differs from terminal decisions"
                )
    return errors


def _transaction_id(prepare_doc: dict, source: dict, values: list[dict]) -> str:
    selected = _terminal_vector(values)
    return tr.sha256_json({
        "run_id": prepare_doc["run_id"],
        "chunk": source["chunk"],
        "before_sha256": source["file_sha256"],
        "selected": selected,
    })


def _artifact_paths(run_dir: Path, prepare_doc: dict, source: dict,
                    transaction_id: str) -> dict[str, Path]:
    for field in ("path", "checkpoint_path"):
        if Path(source[field]).name != source[field]:
            raise ValueError(f"prepared source {field} is noncanonical")
    tmp_root = Path(prepare_doc["tmp"]).resolve()
    target = _trusted_artifact_path(tmp_root / source["path"], tmp_root)
    checkpoint = _trusted_artifact_path(
        tmp_root / source["checkpoint_path"], tmp_root
    )
    chunk = source["chunk"]
    paths = {
        "target": target,
        "checkpoint": checkpoint,
        "backup": run_dir / "backups" / f"{target.name}.{source['file_sha256'][:12]}.json",
        "stage": run_dir / "staged" / f"{target.name}.{transaction_id}.json",
        "journal": run_dir / "transactions" / f"chunk-{chunk:02d}.json",
        "receipt": run_dir / "receipts" / f"chunk-{chunk:02d}.json",
        "archive": run_dir / "Archive" / "transactions" / f"chunk-{chunk:02d}.{transaction_id}.json",
    }
    for name in ("backup", "stage", "journal", "receipt", "archive"):
        paths[name] = _trusted_artifact_path(paths[name], run_dir)
    return paths


def _build_plan(run_dir: Path, prepare_doc: dict, source: dict, internal: dict,
                before_bytes: bytes) -> dict:
    if _sha(before_bytes) != source["file_sha256"]:
        raise ValueError("resolver backup/source does not match prepared draft")
    values = _chunk_values(internal, source["chunk"])
    output_seal = _load_output_seal(run_dir, prepare_doc)
    output_seal_sha256 = (
        output_seal.get("seal_sha256") if output_seal is not None else None
    )
    blockers = [value for value in values
                if value["resolution"]["state"] not in ("resolved", "preserved")]
    if blockers:
        return {"state": "BLOCKED", "fids": [value["item"]["fid"] for value in blockers]}
    rows = json.loads(before_bytes)
    if not isinstance(rows, list):
        raise ValueError("prepared canonical draft is not a JSON list")
    transaction_id = _transaction_id(prepare_doc, source, values)
    selected_hashes = []
    for value in values:
        item = value["item"]
        if value["resolution"]["state"] == "preserved":
            continue
        envelopes = {role: value[role] for role in tr.ROLES}
        resolved = tr.make_resolved_row(
            rows[item["row_index"]], item["route"], value["resolution"], envelopes,
            prepare_doc["run_id"], transaction_id,
        )
        row_errors, _ = validate_verdict_row(resolved, value["packet"])
        if row_errors:
            raise ValueError(f"fid {item['fid']} resolved row invalid: {row_errors}")
        rows[item["row_index"]] = resolved
        selected_hashes.append({
            "fid": item["fid"],
            "decision_sha256": resolved["trust_resolution"]["selected_decision_sha256"],
        })
    if not selected_hashes:
        paths = _artifact_paths(run_dir, prepare_doc, source, transaction_id)
        receipt = {
            "version": 1,
            "kind": "parking-trust-preservation-receipt",
            "run_id": prepare_doc["run_id"],
            "transaction_id": transaction_id,
            "output_seal_sha256": output_seal_sha256,
            "chunk": source["chunk"],
            "draft_sha256": source["file_sha256"],
            "checkpoint_sha256": source["checkpoint_sha256"],
            "terminal_fids": _terminal_vector(values),
            "routes_sha256": tr.sha256_json({
                str(value["item"]["fid"]): value["item"]["route"] for value in values
            }),
        }
        return {
            "state": "PRESERVED", "chunk": source["chunk"], "applied_fids": [],
            "transaction_id": transaction_id, "receipt": receipt, "paths": paths,
        }
    after_bytes = (json.dumps(rows, indent=1, ensure_ascii=False) + "\n").encode("utf-8")
    frozen_by_fid = {
        value["item"]["fid"]: value["packet"] for value in values
    }
    chunk_packets = [frozen_by_fid[row["fid"]] for row in rows]
    new_state = judge_packets.inspect_rows(chunk_packets, rows, source["path"])
    if new_state["status"] != "complete":
        raise ValueError(f"resolved chunk does not validate: {new_state['errors']}")
    if new_state["row_sha256"] != source["judge_row_sha256"]:
        raise ValueError("resolver changed the immutable primary decision vector")
    new_state["file_sha256"] = _sha(after_bytes)
    checkpoint_after = _manifest_after(source, new_state)
    paths = _artifact_paths(run_dir, prepare_doc, source, transaction_id)
    receipt = {
        "version": 1,
        "kind": "parking-trust-receipt",
        "transaction_id": transaction_id,
        "run_id": prepare_doc["run_id"],
        "output_seal_sha256": output_seal_sha256,
        "chunk": source["chunk"],
        "before_sha256": source["file_sha256"],
        "after_sha256": _sha(after_bytes),
        "checkpoint_after_sha256": _sha(_json_bytes(checkpoint_after)),
        "terminal_fids": _terminal_vector(values),
        "applied_fids": selected_hashes,
    }
    journal = {
        **receipt,
        "version": 1,
        "kind": "parking-trust-transaction",
        "target": str(paths["target"]),
        "checkpoint": str(paths["checkpoint"]),
        "stage": str(paths["stage"]),
        "backup": str(paths["backup"]),
        "receipt": str(paths["receipt"]),
        "journal": str(paths["journal"]),
        "archive": str(paths["archive"]),
        "checkpoint_before_sha256": source["checkpoint_sha256"],
        "checkpoint_after": checkpoint_after,
    }
    return {
        "state": "READY",
        "run_id": prepare_doc["run_id"],
        "transaction_id": transaction_id,
        "chunk": source["chunk"],
        "target": str(paths["target"]),
        "before_sha256": source["file_sha256"],
        "after_sha256": _sha(after_bytes),
        "applied_fids": selected_hashes,
        "before_bytes": before_bytes,
        "after_bytes": after_bytes,
        "checkpoint_after": checkpoint_after,
        "receipt": receipt,
        "journal": journal,
        "paths": paths,
    }


def _validate_exact_artifact(path: Path, expected: dict, kind: str) -> dict:
    value = _load_json(path)
    if expected.get("kind") in (
            "parking-trust-receipt",
            "parking-trust-preservation-receipt"):
        receipt_errors = validate_terminal_receipt(
            value, expected_terminal=expected.get("terminal_fids")
        )
        if receipt_errors:
            raise ValueError(f"{kind} is malformed: {receipt_errors}")
    if value != expected:
        raise ValueError(f"{kind} does not match the prepared canonical transaction")
    return value


def _finish_transaction(plan: dict, crash_after_replace: bool = False) -> None:
    paths = plan["paths"]
    _validate_exact_artifact(paths["journal"], plan["journal"], "transaction journal")
    target = paths["target"]
    current = _sha(_read_bytes_nofollow(target))
    if current == plan["before_sha256"]:
        if not paths["stage"].exists() or _read_bytes_nofollow(paths["stage"]) != plan["after_bytes"]:
            raise ValueError("staged resolver bytes are missing or changed")
        if _sha(_read_bytes_nofollow(paths["checkpoint"])) != plan["journal"]["checkpoint_before_sha256"]:
            raise ValueError("checkpoint changed before resolver replacement")
        _replace_verified_nofollow(paths["stage"], target, plan["after_sha256"])
        if crash_after_replace:
            raise RuntimeError("simulated crash after canonical draft replacement")
    elif current != plan["after_sha256"]:
        raise ValueError("canonical draft matches neither resolver transaction hash")
    if _read_bytes_nofollow(target) != plan["after_bytes"]:
        raise ValueError("canonical draft content differs from the recomputed plan")
    checkpoint_hash = _sha(_read_bytes_nofollow(paths["checkpoint"]))
    if checkpoint_hash == plan["journal"]["checkpoint_before_sha256"]:
        _atomic_json(paths["checkpoint"], plan["checkpoint_after"])
    elif checkpoint_hash != plan["receipt"]["checkpoint_after_sha256"]:
        raise ValueError("checkpoint matches neither resolver transaction hash")
    if (_read_bytes_nofollow(target) != plan["after_bytes"]
            or _sha(_read_bytes_nofollow(paths["checkpoint"]))
            != plan["receipt"]["checkpoint_after_sha256"]):
        raise ValueError("resolver target/checkpoint changed before receipt")
    _atomic_json(paths["receipt"], plan["receipt"])
    if (_read_bytes_nofollow(target) != plan["after_bytes"]
            or _sha(_read_bytes_nofollow(paths["checkpoint"]))
            != plan["receipt"]["checkpoint_after_sha256"]):
        raise ValueError("resolver target/checkpoint changed before journal archival")
    _replace_nofollow(paths["journal"], paths["archive"])


def _verify_current_routes(run_dir: Path, prepare_doc: dict,
                           generation_capture) -> None:
    tmp = Path(prepare_doc["tmp"])
    packet_source_bytes = _read_bytes_nofollow(
        run_dir / prepare_doc["source"]["packets_frozen_path"]
    )
    frozen_packets = {
        item["fid"]: _load_json(run_dir / item["packet_path"])
        for item in prepare_doc["items"]
    }
    source_chunks = []
    for source in prepare_doc["source"]["drafts"]:
        path = run_dir / source["frozen_path"]
        source_bytes = _read_bytes_nofollow(path)
        rows = json.loads(source_bytes)
        logical_path = tmp / source["path"]
        source_chunks.append({
            "draft": logical_path, "draft_bytes": source_bytes, "rows": rows
        })
    current_routes, current_replay = _current_trust_report(
        tmp, prepare_doc["area"], generation_capture,
        frozen_packets, packet_source_bytes, source_chunks,
    )
    if tr.sha256_json(current_replay) != prepare_doc["source"]["replay_sha256"]:
        raise ValueError("trust replay/authority routes changed after prepare")
    expected = {item["fid"]: item["route"] for item in prepare_doc["items"]}
    actual = {int(item["source_key"]): item["route"] for item in current_routes}
    if actual != expected:
        raise ValueError("per-fid trust routes changed after prepare")


def _global_apply_block(status: dict) -> dict | None:
    if status.get("state") == "READY":
        return None
    unresolved = [
        item["fid"] for item in status.get("items", [])
        if item.get("state") not in ("resolved", "preserved")
    ]
    errors = list(status.get("errors") or [])
    if not errors:
        errors.append(
            f"whole resolver run is {status.get('state')}; every chunk must be ready"
        )
    return {
        "state": status.get("state") or "BLOCKED",
        "errors": errors,
        "fids": unresolved,
    }


def _apply_chunk_under_lock(run_dir: Path, chunk_index: int,
                            generation_capture, apply: bool = False,
                            crash_after_replace: bool = False) -> dict:
    prepare_doc = _load_prepare(run_dir)
    require_current_source_generation(prepare_doc, generation_capture)
    authority_journal = tr.authority_journal_path(
        prepare_doc["tmp"], prepare_doc["area"]
    )
    if authority_journal.exists():
        return {
            "state": "RECOVERY_REQUIRED",
            "errors": [
                "live human-authority journal blocks resolver work/apply"
            ],
        }
    source = next((row for row in prepare_doc["source"]["drafts"]
                   if row["chunk"] == chunk_index), None)
    if source is None:
        raise ValueError(f"unknown chunk {chunk_index:02d}")

    live_journal_path = _trusted_artifact_path(
        run_dir / "transactions" / f"chunk-{chunk_index:02d}.json", run_dir
    )
    status, internal = evaluate_locked(run_dir, generation_capture)
    if live_journal_path.exists():
        try:
            recovery_seal = _load_output_seal(run_dir, prepare_doc)
        except (OSError, ValueError, json.JSONDecodeError, TypeError) as error:
            return {"state": "RECOVERY_REQUIRED", "errors": [
                f"live journal has no valid output seal: {error}"
            ]}
        if recovery_seal is None:
            return {"state": "RECOVERY_REQUIRED", "errors": [
                "live journal cannot recover without a verified whole-run output seal"
            ]}
        if any(item.get("state") not in ("resolved", "preserved")
               for item in status.get("items", [])):
            return {"state": "RECOVERY_REQUIRED", "errors": [
                "live journal cannot recover while any sealed sibling is unresolved"
            ]}
        if (recovery_seal["ready_snapshot_sha256"]
                != _resolution_snapshot_sha256(status)):
            return {"state": "RECOVERY_REQUIRED", "errors": [
                "live journal resolution differs from its sealed whole-run READY snapshot"
            ]}
    if (apply and status.get("state") == "READY"
            and not live_journal_path.exists()
            and status.get("output_seal_sha256") is None):
        seal = _seal_outputs(run_dir, prepare_doc, status)
        status, internal = evaluate_locked(run_dir, generation_capture)
        if (status.get("state") != "READY"
                or seal["ready_snapshot_sha256"] != _resolution_snapshot_sha256(status)):
            return _global_apply_block(status) or {
                "state": "BLOCKED", "errors": ["sealed output set is not ready"]
            }
    values = _chunk_values(internal, chunk_index)
    transaction_id = _transaction_id(prepare_doc, source, values)
    paths = _artifact_paths(run_dir, prepare_doc, source, transaction_id)
    current_hash = _sha(_read_bytes_nofollow(paths["target"]))
    if current_hash == source["file_sha256"]:
        before_bytes = _read_bytes_nofollow(paths["target"])
    elif paths["backup"].exists() and _sha(_read_bytes_nofollow(paths["backup"])) == source["file_sha256"]:
        before_bytes = _read_bytes_nofollow(paths["backup"])
    else:
        raise ValueError("cannot recover the exact prepared canonical draft bytes")
    plan = _build_plan(run_dir, prepare_doc, source, internal, before_bytes)
    if live_journal_path.exists() and plan.get("state") not in ("READY", "PRESERVED"):
        return {"state": "RECOVERY_REQUIRED", "errors": [
            "live transaction journal exists but current inbox cannot reconstruct its plan"
        ]}
    if plan.get("state") == "PRESERVED":
        receipt_path = plan["paths"]["receipt"]
        if plan["paths"]["journal"].exists():
            return {"state": "RECOVERY_REQUIRED", "errors": [
                "preserved-only chunk has an unexpected live transaction journal"
            ]}
        if receipt_path.exists():
            receipt = _validate_exact_artifact(
                receipt_path, plan["receipt"], "preservation receipt"
            )
            if (_sha(_read_bytes_nofollow(plan["paths"]["target"])) != source["file_sha256"]
                    or _sha(_read_bytes_nofollow(plan["paths"]["checkpoint"])) != source["checkpoint_sha256"]):
                raise ValueError("preservation receipt source hashes changed")
            return {"state": "PRESERVED", "receipt": receipt}
        global_block = _global_apply_block(status)
        if global_block is not None:
            return global_block
        if not apply:
            return {key: value for key, value in plan.items()
                    if key not in ("receipt", "paths")}
        _verify_current_routes(run_dir, prepare_doc, generation_capture)
        if (_sha(_read_bytes_nofollow(plan["paths"]["target"])) != source["file_sha256"]
                or _sha(_read_bytes_nofollow(plan["paths"]["checkpoint"])) != source["checkpoint_sha256"]):
            raise ValueError("preserved source changed before receipt")
        _atomic_json(receipt_path, plan["receipt"])
        return {"state": "PRESERVED", "receipt": plan["receipt"]}
    if plan.get("state") != "READY":
        return plan

    if paths["journal"].exists():
        try:
            _validate_exact_artifact(paths["journal"], plan["journal"], "transaction journal")
        except ValueError as error:
            return {"state": "RECOVERY_REQUIRED", "errors": [str(error)]}
        if not apply:
            return {"state": "RECOVERY_REQUIRED", "transaction_id": transaction_id}
        if _sha(_read_bytes_nofollow(paths["target"])) == source["file_sha256"]:
            _verify_current_routes(run_dir, prepare_doc, generation_capture)
        _finish_transaction(plan)
        return {"state": "APPLIED", "receipt": _load_json(paths["receipt"])}

    if paths["receipt"].exists():
        receipt = _validate_exact_artifact(paths["receipt"], plan["receipt"], "apply receipt")
        if (_read_bytes_nofollow(paths["target"]) != plan["after_bytes"]
                or _sha(_read_bytes_nofollow(paths["checkpoint"])) != receipt["checkpoint_after_sha256"]):
            raise ValueError("applied receipt does not match canonical draft/checkpoint")
        return {"state": "APPLIED", "receipt": receipt}

    global_block = _global_apply_block(status)
    if global_block is not None:
        return global_block
    source_state = next(
        (row["source_state"] for row in status["chunks"] if row["chunk"] == chunk_index),
        None,
    )
    if source_state != "source":
        return {"state": "BLOCKED", "errors": [f"chunk source state is {source_state}"]}
    _verify_current_routes(run_dir, prepare_doc, generation_capture)
    public_plan = {key: value for key, value in plan.items()
                   if key not in ("before_bytes", "after_bytes", "checkpoint_after",
                                  "receipt", "journal", "paths")}
    if not apply:
        return public_plan

    _write_idempotent(paths["backup"], before_bytes)
    _write_idempotent(paths["stage"], plan["after_bytes"])
    if _sha(_read_bytes_nofollow(paths["target"])) != source["file_sha256"]:
        raise ValueError("canonical draft changed before resolver commit")
    if _sha(_read_bytes_nofollow(paths["checkpoint"])) != source["checkpoint_sha256"]:
        raise ValueError("checkpoint changed before resolver commit")
    _verify_current_routes(run_dir, prepare_doc, generation_capture)
    _atomic_json(paths["journal"], plan["journal"])
    _finish_transaction(plan, crash_after_replace=crash_after_replace)
    return {"state": "APPLIED", "receipt": _load_json(paths["receipt"])}


def _canonical_lock_path(prepare_doc: dict, chunk_index: int) -> Path:
    del chunk_index  # authority changes are area-wide, so every chunk shares one lock
    return tr.area_lock_path(prepare_doc["tmp"], prepare_doc["area"])


def apply_chunk(run_dir: Path, chunk_index: int, apply: bool = False,
                crash_after_replace: bool = False) -> dict:
    prepare_doc = _load_prepare(run_dir)
    tmp = Path(prepare_doc["tmp"])
    resource_modes = [
        (tr.dossier_resource_path(tmp), fcntl.LOCK_SH),
    ]
    if apply:
        resource_modes.append((DATA / "calibration.json", fcntl.LOCK_EX))
    with trusted_fs.locked_resources(
            tmp, resource_modes, tr.resource_lock_path):
        area_lock_path = _canonical_lock_path(prepare_doc, chunk_index)
        with _open_lock_file(area_lock_path) as area_lock:
            fcntl.flock(
                area_lock.fileno(), fcntl.LOCK_EX if apply else fcntl.LOCK_SH
            )
            generation_capture = dossier_output.capture_generation_locked(
                tmp, prepare_doc["area"]
            )
            return _apply_chunk_under_lock(
                run_dir, chunk_index, generation_capture, apply=apply,
                crash_after_replace=crash_after_replace,
            )


def _status_exit(status: dict) -> int:
    if status["state"] in ("READY", "APPLIED"):
        return 0
    if status["state"] == "PENDING":
        return 3
    return 2


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("slug")
    p.add_argument("--tmp", type=Path, default=DEFAULT_TMP)
    p.add_argument("--run-root", type=Path)
    p.add_argument("--primary-model", required=True)
    p.add_argument("--challenger-model", required=True)
    p.add_argument("--arbiter-model", required=True)
    s = sub.add_parser("status")
    s.add_argument("--run", type=Path, required=True)
    a = sub.add_parser("apply")
    a.add_argument("--run", type=Path, required=True)
    a.add_argument("--chunk", type=int, required=True)
    a.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            models = {
                "primary": _parse_model(args.primary_model),
                "challenger": _parse_model(args.challenger_model),
                "arbiter": _parse_model(args.arbiter_model),
            }
            run = prepare(args.slug, args.tmp, models, args.run_root)
            print(run)
            return 0
        if args.command == "status":
            status, _ = evaluate(args.run)
            print(json.dumps(status, sort_keys=True, separators=(",", ":")))
            return _status_exit(status)
        result = apply_chunk(args.run, args.chunk, apply=args.apply)
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0 if result["state"] in ("READY", "APPLIED", "PRESERVED") else 2
    except (OSError, ValueError, OverflowError, json.JSONDecodeError,
            TypeError, AttributeError) as error:
        print(f"trust resolver failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
