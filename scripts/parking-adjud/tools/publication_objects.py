#!/usr/bin/env python3
"""Bounded-memory immutable objects for Trekdex publication proofs.

Object references are canonical paths relative to one parking-adjudication data
root. Authority comes only from a rooted registry and proof manifest; an exact
unreferenced object or staging file is inert.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Iterator

import trust_resolution as tr
import trusted_filesystem as trusted_fs

READ_SIZE = 1024 * 1024
MAX_LEAF_SIZE = 32 * 1024 * 1024
OBJECT_DIRECTORY = "publication-proof-objects"
OBJECT_ALGORITHM = "sha256"
OBJECT_PATH_PREFIX = f"{OBJECT_DIRECTORY}/{OBJECT_ALGORITHM}"
REGISTRY_VERSION = 2
REGISTRY_KIND = "parking-publication-proof-registry"
PROOF_MANIFEST_VERSION = 4
PROOF_MANIFEST_KIND = "parking-publication-proof"
REGISTRY_MAX_BYTES = 1024 * 1024
PROOF_MANIFEST_MAX_BYTES = 4 * 1024 * 1024
STAGE_JOURNAL_MAX_BYTES = 16 * 1024 * 1024
PUBLICATION_TRANSACTION_VERSION = 5
PUBLICATION_TRANSACTION_KIND = "parking-publication-transaction"
REGISTRY_CAP_GUIDANCE = (
    "see scripts/parking-adjud/README.md#oversized-registry-read-block-"
    "and-generation-1-restore"
)
_SHA256_CHARACTERS = frozenset("0123456789abcdef")
ARCHIVE_REPORT_VERSION = 1
ARCHIVE_REPORT_OPERATION = "trekdex-publication-archive-batch"
ARCHIVE_REPORT_MAX_BYTES = 16 * 1024 * 1024
ARCHIVE_REPORT_MAX_MOVES = 1024
ARCHIVE_VERIFY_MAX_BYTES = 1024 * 1024 * 1024 * 1024
ARCHIVE_VERIFY_MAX_TREE_ENTRIES = 1_000_000
_ARCHIVE_PLAN_KIND = "trekdex-publication-archive-plan"
_ARCHIVE_PREPARED_KIND = "trekdex-publication-archive-prepared"
_ARCHIVE_CHECKPOINT_KIND = "trekdex-publication-archive-checkpoint"
_ARCHIVE_OPTIONAL_CHECKPOINT_KIND = (
    "trekdex-publication-archive-optional-checkpoint"
)
_ARCHIVE_TERMINAL_KIND = "trekdex-publication-archive-terminal"
_GENERATION_1_RESTORE_PREPARED_KIND = (
    "trekdex-publication-generation-1-restore-prepared"
)
_GENERATION_1_RESTORE_TERMINAL_KIND = (
    "trekdex-publication-generation-1-restore-terminal"
)
GENERATION_1_RESTORE_OPERATION = "trekdex-colorado-generation-1-restore"
GENERATION_1_RESTORE_CONTRACT = "colorado-generation-1-v1"
GENERATION_1_RESTORE_COMMIT = "140f8c935864ca5fe9b1070256193eee807691e3"
GENERATION_1_STORE_PATH = (
    "scripts/parking-adjud/data/co_verdicts_osm.json"
)
GENERATION_1_FLOOR_PATH = (
    "scripts/parking-adjud/data/co_verdicts_osm_publication_floor.json"
)
GENERATION_1_ROOT_PATH = (
    "scripts/parking-adjud/publication-trust-root-v1.json"
)
GENERATION_1_BUILDER_PATH = "scripts/build-parking-verdicts.py"
_GENERATION_1_CLOSED_RESTORE_PATHS = (
    GENERATION_1_STORE_PATH,
    GENERATION_1_FLOOR_PATH,
    GENERATION_1_ROOT_PATH,
    GENERATION_1_BUILDER_PATH,
)
GENERATION_1_RESTORE_TARGETS = (
    (
        GENERATION_1_STORE_PATH,
        "44f3ac4c6793e1be66e301f6ee46875de92b8e1952351c607ae4e658d861a0cb",
    ),
    (
        GENERATION_1_FLOOR_PATH,
        "5b5d134bec54d6e3e4862cea31231e1701b5d116c4866819128b6e59b40f3cad",
    ),
    (
        GENERATION_1_ROOT_PATH,
        "43db143447d90f506d42b273c425e0d048e5580e605ac211750114ba350dfd85",
    ),
    (
        GENERATION_1_BUILDER_PATH,
        "a2ab1d97bb949422e539c7769223977090c4a5d449ce71e8e22b00764c9e3fdb",
    ),
)
GENERATION_1_LEGACY_PROOF_PATH = (
    "scripts/parking-adjud/data/co_verdicts_osm_publication_proofs.json"
)
GENERATION_1_OPTIONAL_PATHS = (
    "scripts/parking-adjud/data/.trekdex-publication-transactions",
    "scripts/parking-adjud/data/publication-proof-objects",
)
_GENERATION_1_CLOSED_ARCHIVE_SOURCE_PATHS = (
    _GENERATION_1_CLOSED_RESTORE_PATHS[0],
    GENERATION_1_LEGACY_PROOF_PATH,
    _GENERATION_1_CLOSED_RESTORE_PATHS[1],
    _GENERATION_1_CLOSED_RESTORE_PATHS[2],
    _GENERATION_1_CLOSED_RESTORE_PATHS[3],
)
GENERATION_1_ARCHIVE_SOURCE_PATHS = (
    GENERATION_1_RESTORE_TARGETS[0][0],
    GENERATION_1_LEGACY_PROOF_PATH,
    GENERATION_1_RESTORE_TARGETS[1][0],
    GENERATION_1_RESTORE_TARGETS[2][0],
    GENERATION_1_RESTORE_TARGETS[3][0],
)
_GENERATION_1_RESTORE_PREPARED_NAME = "generation-1-restore-prepared.json"
_GENERATION_1_RESTORE_TERMINAL_NAME = "generation-1-restore-terminal.json"


def _valid_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and set(value).issubset(_SHA256_CHARACTERS)
    )


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _pretty_json(value: object) -> bytes:
    return (json.dumps(
        value, ensure_ascii=False, sort_keys=True, indent=1, allow_nan=False,
    ) + "\n").encode("utf-8")


def registry_cap_message(subject: str = "publication proof registry") -> str:
    return f"{subject} exceeds 1 MiB; {REGISTRY_CAP_GUIDANCE}"


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key {key!r}")
        value[key] = item
    return value


def _strict_json(raw: bytes, label: str) -> object:
    try:
        value = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, ValueError) as error:
        raise ValueError(f"{label} is not strict JSON: {error}") from error
    if _pretty_json(value) != raw:
        raise ValueError(f"{label} bytes are noncanonical")
    return value


def canonical_object_path(sha256: str) -> str:
    """Return the sole repository-relative path for one SHA-256 leaf."""
    if not _valid_sha256(sha256):
        raise ValueError("object SHA-256 must be 64 lowercase hex characters")
    return f"{OBJECT_PATH_PREFIX}/{sha256[:2]}/{sha256[2:]}"


@dataclass(frozen=True)
class ObjectRef:
    path: str
    length: int
    sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "length": self.length,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class LogicalObjectRef:
    length: int
    sha256: str
    leaves: tuple[ObjectRef, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "length": self.length,
            "sha256": self.sha256,
            "leaves": [leaf.to_dict() for leaf in self.leaves],
        }


@dataclass(frozen=True)
class StagedObject:
    ref: ObjectRef
    stage_path: Path

    def to_dict(self, stage_root: str | Path | None = None) -> dict[str, object]:
        path = self.stage_path.absolute()
        if stage_root is None:
            stage = str(path)
        else:
            root = Path(stage_root).absolute()
            try:
                relative = path.relative_to(root)
            except ValueError as error:
                raise ValueError("staged object is outside its stage root") from error
            if len(relative.parts) != 1 or relative.name != self.ref.sha256:
                raise ValueError("staged object path is noncanonical")
            stage = relative.as_posix()
        return {"object": self.ref.to_dict(), "stage": stage}


class PublicationProofRegistry(dict):
    """Serializable registry carrying non-serialized local replay context."""

    def __init__(self, value: dict, data_root: str | Path, *,
                 staged_objects: Iterable[StagedObject] = ()):
        super().__init__(value)
        self.data_root = Path(data_root).absolute()
        self.staged_objects = tuple(staged_objects)


def _canonical_relative_path(value: object) -> str:
    if (not isinstance(value, str) or not value or "\\" in value
            or "\x00" in value):
        raise ValueError("object path is not a canonical POSIX relative path")
    path = PurePosixPath(value)
    if (path.is_absolute() or not path.parts
            or any(part in ("", ".", "..") for part in path.parts)
            or path.as_posix() != value):
        raise ValueError("object path is not a canonical POSIX relative path")
    return value


def validate_object_ref(value: ObjectRef | dict) -> ObjectRef:
    if isinstance(value, ObjectRef):
        ref = value
    elif isinstance(value, dict) and set(value) == {"path", "length", "sha256"}:
        ref = ObjectRef(
            path=value.get("path"),
            length=value.get("length"),
            sha256=value.get("sha256"),
        )
    else:
        raise ValueError("object reference schema mismatch")
    _canonical_relative_path(ref.path)
    if type(ref.length) is not int or not 0 <= ref.length <= MAX_LEAF_SIZE:
        raise ValueError("object leaf length is outside the 32 MiB limit")
    if not _valid_sha256(ref.sha256):
        raise ValueError("object leaf SHA-256 is invalid")
    if ref.path != canonical_object_path(ref.sha256):
        raise ValueError("object path does not match its SHA-256")
    return ref


def validate_logical_object_ref(
        value: LogicalObjectRef | dict,
) -> LogicalObjectRef:
    if isinstance(value, LogicalObjectRef):
        ref = value
    elif isinstance(value, dict) and set(value) == {"length", "sha256", "leaves"}:
        raw_leaves = value.get("leaves")
        if not isinstance(raw_leaves, list):
            raise ValueError("logical object leaves must be a list")
        ref = LogicalObjectRef(
            length=value.get("length"),
            sha256=value.get("sha256"),
            leaves=tuple(validate_object_ref(leaf) for leaf in raw_leaves),
        )
    else:
        raise ValueError("logical object reference schema mismatch")
    if type(ref.length) is not int or ref.length < 0:
        raise ValueError("logical object length must be a nonnegative integer")
    if not _valid_sha256(ref.sha256):
        raise ValueError("logical object SHA-256 is invalid")
    leaves = tuple(validate_object_ref(leaf) for leaf in ref.leaves)
    if not leaves:
        raise ValueError("logical object must contain at least one leaf")
    expected_leaf_count = max(1, (ref.length + MAX_LEAF_SIZE - 1) // MAX_LEAF_SIZE)
    if len(leaves) != expected_leaf_count:
        raise ValueError("logical object leaf count is noncanonical")
    remaining = ref.length
    for index, leaf in enumerate(leaves):
        expected = min(MAX_LEAF_SIZE, remaining)
        if ref.length == 0:
            expected = 0
        if leaf.length != expected:
            raise ValueError(
                f"logical object leaf {index} length is noncanonical"
            )
        remaining -= leaf.length
    if remaining != 0:
        raise ValueError("logical object leaves do not cover the exact length")
    return LogicalObjectRef(ref.length, ref.sha256, leaves)


def _object_path(data_root: str | Path, ref: ObjectRef) -> Path:
    root = Path(data_root).absolute()
    relative = PurePosixPath(validate_object_ref(ref).path)
    return root.joinpath(*relative.parts)


def _linked_install_pair_exact(
        stage: Path, target: Path, ref: ObjectRef,
) -> bool:
    """Return whether stage/target are one exact transitional two-link inode."""
    try:
        stage_stat = os.lstat(stage)
        target_stat = os.lstat(target)
    except FileNotFoundError:
        return False
    if ((stage_stat.st_dev, stage_stat.st_ino)
            != (target_stat.st_dev, target_stat.st_ino)
            or stage_stat.st_nlink != 2 or target_stat.st_nlink != 2):
        return False
    for path in (stage, target):
        for _chunk in trusted_fs.iter_verified_regular_chunks(
                path, chunk_size=READ_SIZE, require_owner=True,
                require_read_only=True, allowed_nlinks=(2,),
                expected_length=ref.length, expected_sha256=ref.sha256):
            pass
    return True


def iter_object_bytes(
        data_root: str | Path, value: ObjectRef | LogicalObjectRef | dict,
        staged_objects: Iterable[StagedObject] = (),
) -> Iterator[bytes]:
    """Yield one immutable object in 1 MiB reads without restarting a leaf.

    Exact verification completes only when the iterator is exhausted. A leaf
    may fall back to its exact transitional stage only before yielding bytes;
    any failure after the first yielded byte is terminal so consumers never
    receive a duplicated prefix.
    """
    if isinstance(value, ObjectRef) or (
            isinstance(value, dict) and set(value) == {"path", "length", "sha256"}):
        leaf = validate_object_ref(value)
        logical = LogicalObjectRef(leaf.length, leaf.sha256, (leaf,))
    else:
        logical = validate_logical_object_ref(value)
    staged_by_hash = {}
    for staged in staged_objects:
        if not isinstance(staged, StagedObject):
            raise ValueError("object reader received a non-stage")
        ref = validate_object_ref(staged.ref)
        previous = staged_by_hash.setdefault(ref.sha256, staged)
        if previous.ref != ref:
            raise ValueError("object reader stages conflict")
    digest = hashlib.sha256()
    length = 0
    for leaf in logical.leaves:
        path = _object_path(data_root, leaf)
        emitted = False
        try:
            for chunk in trusted_fs.iter_verified_regular_chunks(
                    path, chunk_size=READ_SIZE, require_owner=True,
                    require_read_only=True, expected_length=leaf.length,
                    expected_sha256=leaf.sha256):
                digest.update(chunk)
                length += len(chunk)
                emitted = True
                yield chunk
        except FileNotFoundError:
            if emitted:
                raise
            staged = staged_by_hash.get(leaf.sha256)
            if staged is None:
                raise
            for chunk in trusted_fs.iter_verified_regular_chunks(
                    staged.stage_path, chunk_size=READ_SIZE,
                    require_owner=True, require_read_only=True,
                    expected_length=leaf.length,
                    expected_sha256=leaf.sha256):
                digest.update(chunk)
                length += len(chunk)
                yield chunk
        except ValueError:
            if emitted:
                raise
            staged = staged_by_hash.get(leaf.sha256)
            if (staged is None or not _linked_install_pair_exact(
                    staged.stage_path, path, leaf)):
                raise
            for chunk in trusted_fs.iter_verified_regular_chunks(
                    path, chunk_size=READ_SIZE, require_owner=True,
                    require_read_only=True, allowed_nlinks=(2,),
                    expected_length=leaf.length,
                    expected_sha256=leaf.sha256):
                digest.update(chunk)
                length += len(chunk)
                yield chunk
    if length != logical.length:
        raise ValueError("logical object reconstructed length mismatch")
    if digest.hexdigest() != logical.sha256:
        raise ValueError("logical object reconstructed SHA-256 mismatch")


def hash_object_stream(chunks: Iterable[bytes]) -> tuple[int, str]:
    digest = hashlib.sha256()
    length = 0
    for chunk in chunks:
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise ValueError("object stream yielded a non-bytes chunk")
        view = memoryview(chunk)
        while view:
            piece = view[:READ_SIZE]
            digest.update(piece)
            length += len(piece)
            view = view[len(piece):]
    return length, digest.hexdigest()


def _write_all(fd: int, value: memoryview) -> None:
    while value:
        written = os.write(fd, value)
        if written <= 0:
            raise OSError("staged object write made no progress")
        value = value[written:]


def _remove_owned_private(parent_fd: int, name: str,
                          owned: os.stat_result) -> None:
    try:
        current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if (current.st_dev, current.st_ino) == (owned.st_dev, owned.st_ino):
        os.unlink(name, dir_fd=parent_fd)


def _verify_staged(path: Path, ref: ObjectRef, *,
                   allowed_nlinks: tuple[int, ...] = (1,)) -> None:
    for _chunk in trusted_fs.iter_verified_regular_chunks(
            path, chunk_size=READ_SIZE, require_owner=True,
            require_read_only=True, allowed_nlinks=allowed_nlinks,
            expected_length=ref.length, expected_sha256=ref.sha256):
        pass


def _finish_staged_leaf(
        root_fd: int, stage_root: Path, private_fd: int, private_name: str,
        private_before: os.stat_result, leaf_length: int,
        leaf_sha256: str,
) -> StagedObject:
    os.fchmod(private_fd, 0o400)
    os.fsync(private_fd)
    trusted_fs.require_trivial_acl_fd(private_fd, stage_root / private_name)
    fd_after = os.fstat(private_fd)
    entry_after = os.stat(
        private_name, dir_fd=root_fd, follow_symlinks=False
    )
    identity = (private_before.st_dev, private_before.st_ino)
    if (not stat.S_ISREG(private_before.st_mode)
            or private_before.st_uid != os.geteuid()
            or private_before.st_nlink != 1
            or private_before.st_size != 0):
        raise ValueError("private staged object creation identity changed")
    for value in (fd_after, entry_after):
        if (not stat.S_ISREG(value.st_mode)
                or value.st_uid != os.geteuid()
                or value.st_nlink != 1
                or value.st_size != leaf_length
                or (value.st_dev, value.st_ino) != identity):
            raise ValueError("private staged object identity changed")
    if stat.S_IMODE(fd_after.st_mode) != 0o400:
        raise ValueError("private staged object did not seal read-only")

    ref = ObjectRef(
        canonical_object_path(leaf_sha256), leaf_length, leaf_sha256
    )
    final_name = leaf_sha256
    try:
        os.link(
            private_name, final_name,
            src_dir_fd=root_fd, dst_dir_fd=root_fd,
            follow_symlinks=False,
        )
    except FileExistsError:
        _verify_staged(stage_root / final_name, ref, allowed_nlinks=(1, 2))
    else:
        os.fsync(root_fd)
    _remove_owned_private(root_fd, private_name, private_before)
    os.fsync(root_fd)
    _verify_staged(stage_root / final_name, ref, allowed_nlinks=(1, 2))
    return StagedObject(ref, stage_root / final_name)


def stage_logical_object(
        chunks: Iterable[bytes] | bytes, stage_root: str | Path,
) -> tuple[LogicalObjectRef, tuple[StagedObject, ...]]:
    """Stage one deterministic logical stream as sealed 32 MiB leaves."""
    if isinstance(chunks, bytes):
        source: Iterable[bytes] = (chunks,)
    else:
        source = chunks
    root = Path(stage_root).absolute()
    root_fd = trusted_fs.open_trusted_directory_fd(
        root, create=True, create_mode=0o700
    )
    logical_digest = hashlib.sha256()
    logical_length = 0
    staged: list[StagedObject] = []
    private_fd = None
    private_name = None
    private_before = None
    leaf_digest = None
    leaf_length = 0

    def begin_leaf() -> None:
        nonlocal private_fd, private_name, private_before, leaf_digest, leaf_length
        flags = (os.O_RDWR | os.O_CREAT | os.O_EXCL
                 | trusted_fs._required_flag("O_NOFOLLOW")
                 | getattr(os, "O_CLOEXEC", 0))
        for _ in range(128):
            candidate = f".object-stage-{secrets.token_hex(24)}"
            try:
                descriptor = os.open(candidate, flags, 0o600, dir_fd=root_fd)
            except FileExistsError:
                continue
            private_fd = descriptor
            private_name = candidate
            private_before = os.fstat(descriptor)
            leaf_digest = hashlib.sha256()
            leaf_length = 0
            trusted_fs.require_trivial_acl_fd(descriptor, root / candidate)
            return
        raise FileExistsError("could not allocate a private object stage")

    def finish_leaf() -> None:
        nonlocal private_fd, private_name, private_before, leaf_digest, leaf_length
        if (private_fd is None or private_name is None
                or private_before is None or leaf_digest is None):
            raise RuntimeError("no staged leaf is open")
        descriptor = private_fd
        name = private_name
        before = private_before
        try:
            staged.append(_finish_staged_leaf(
                root_fd, root, descriptor, name, before,
                leaf_length, leaf_digest.hexdigest(),
            ))
        finally:
            os.close(descriptor)
            private_fd = None
            private_name = None
            private_before = None
            leaf_digest = None
            leaf_length = 0

    try:
        for raw in source:
            if not isinstance(raw, (bytes, bytearray, memoryview)):
                raise ValueError("logical object source yielded non-bytes")
            view = memoryview(raw)
            while view:
                if private_fd is None:
                    begin_leaf()
                capacity = MAX_LEAF_SIZE - leaf_length
                piece = view[:min(capacity, READ_SIZE)]
                _write_all(private_fd, piece)
                leaf_digest.update(piece)
                logical_digest.update(piece)
                size = len(piece)
                leaf_length += size
                logical_length += size
                view = view[size:]
                if leaf_length == MAX_LEAF_SIZE:
                    finish_leaf()
        if private_fd is not None:
            finish_leaf()
        if not staged:
            begin_leaf()
            finish_leaf()
    except BaseException:
        if (private_fd is not None and private_name is not None
                and private_before is not None):
            _remove_owned_private(root_fd, private_name, private_before)
            os.close(private_fd)
        raise
    finally:
        os.close(root_fd)

    logical = LogicalObjectRef(
        logical_length,
        logical_digest.hexdigest(),
        tuple(value.ref for value in staged),
    )
    validate_logical_object_ref(logical)
    return logical, tuple(staged)


def _ensure_independent_stage(
        stage: Path, target: Path, ref: ObjectRef,
) -> None:
    """Retain one exact sealed stage on an inode distinct from its object."""
    try:
        _verify_staged(stage, ref)
    except FileNotFoundError:
        try:
            trusted_fs.atomic_write_stream(
                stage,
                trusted_fs.iter_verified_regular_chunks(
                    target, chunk_size=READ_SIZE,
                    require_owner=True, require_read_only=True,
                    expected_length=ref.length,
                    expected_sha256=ref.sha256,
                ),
                expected_length=ref.length,
                expected_sha256=ref.sha256,
                mode=0o400,
            )
        except FileExistsError:
            _verify_staged(stage, ref)
    _verify_staged(stage, ref)
    _verify_staged(target, ref)
    stage_stat = os.lstat(stage)
    target_stat = os.lstat(target)
    if ((stage_stat.st_dev, stage_stat.st_ino)
            == (target_stat.st_dev, target_stat.st_ino)):
        raise ValueError("object stage and installed object share an inode")


def _recover_linked_install(stage: Path, target: Path, ref: ObjectRef) -> bool:
    try:
        stage_stat = os.lstat(stage)
        target_stat = os.lstat(target)
    except FileNotFoundError:
        return False
    if ((stage_stat.st_dev, stage_stat.st_ino)
            != (target_stat.st_dev, target_stat.st_ino)):
        return False
    if stage_stat.st_nlink != 2 or target_stat.st_nlink != 2:
        return False
    _verify_staged(stage, ref, allowed_nlinks=(2,))
    _verify_staged(target, ref, allowed_nlinks=(2,))
    stage_parent_fd = trusted_fs.open_trusted_directory_fd(stage.parent)
    try:
        current = os.stat(
            stage.name, dir_fd=stage_parent_fd, follow_symlinks=False
        )
        if ((current.st_dev, current.st_ino)
                != (stage_stat.st_dev, stage_stat.st_ino)
                or current.st_nlink != 2):
            raise ValueError("linked object stage changed during recovery")
        os.unlink(stage.name, dir_fd=stage_parent_fd)
        os.fsync(stage_parent_fd)
    finally:
        os.close(stage_parent_fd)
    _verify_staged(target, ref)
    _ensure_independent_stage(stage, target, ref)
    return True


def install_staged_objects(
        data_root: str | Path, staged_objects: Iterable[StagedObject],
) -> tuple[ObjectRef, ...]:
    """Install absent leaves without overwrite, accepting only exact retries."""
    root = Path(data_root).absolute()
    by_hash: dict[str, StagedObject] = {}
    for value in staged_objects:
        if not isinstance(value, StagedObject):
            raise ValueError("staged object vector contains a non-stage")
        ref = validate_object_ref(value.ref)
        previous = by_hash.setdefault(ref.sha256, value)
        if (previous.ref != ref
                or previous.stage_path.absolute()
                != value.stage_path.absolute()):
            raise ValueError("staged object SHA-256 has conflicting stages")
    installed: list[ObjectRef] = []
    for value in sorted(by_hash.values(), key=lambda item: item.ref.sha256):
        ref = value.ref
        stage = value.stage_path.absolute()
        target = _object_path(root, ref)
        target_parent_fd = trusted_fs.open_trusted_directory_fd(
            target.parent, create=True, create_mode=0o700
        )
        source_parent_fd = trusted_fs.open_trusted_directory_fd(stage.parent)
        try:
            try:
                _verify_staged(target, ref)
            except FileNotFoundError:
                if _recover_linked_install(stage, target, ref):
                    installed.append(ref)
                    continue
                _verify_staged(stage, ref)
                try:
                    os.link(
                        stage.name, target.name,
                        src_dir_fd=source_parent_fd,
                        dst_dir_fd=target_parent_fd,
                        follow_symlinks=False,
                    )
                except FileExistsError:
                    if not _recover_linked_install(stage, target, ref):
                        _verify_staged(target, ref)
                else:
                    os.fsync(target_parent_fd)
                    if not _recover_linked_install(stage, target, ref):
                        raise ValueError(
                            "linked object install could not be finalized"
                        )
                _verify_staged(target, ref)
            except ValueError:
                if _recover_linked_install(stage, target, ref):
                    installed.append(ref)
                    continue
                raise
            _verify_staged(target, ref)
            _ensure_independent_stage(stage, target, ref)
            installed.append(ref)
        finally:
            os.close(source_parent_fd)
            os.close(target_parent_fd)
    return tuple(installed)


def seal_object_permissions(
        data_root: str | Path, values: Iterable[ObjectRef | dict],
) -> tuple[ObjectRef, ...]:
    """Replace checkout material with fresh immutable single-link inodes.

    The source may carry Git-restored write or execute bits. Its bytes,
    ownership, ACL, link count, mode, descriptor/entry identity, and metadata
    stability are verified while it is streamed into a private read-only inode.
    Atomic replacement retires the source inode, so a descriptor opened before
    sealing cannot alter the canonical bytes after this function returns.
    """
    refs = {validate_object_ref(value).sha256: validate_object_ref(value)
            for value in values}
    sealed = []

    for ref in sorted(refs.values(), key=lambda value: value.sha256):
        path = _object_path(data_root, ref)
        parent_fd = trusted_fs.open_trusted_directory_fd(path.parent)
        fd = None
        try:
            fd = os.open(
                path.name,
                os.O_RDONLY | trusted_fs._required_flag("O_NOFOLLOW")
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=parent_fd,
            )
            before = os.fstat(fd)
            entry = os.stat(
                path.name, dir_fd=parent_fd, follow_symlinks=False
            )
            trusted_fs.require_trivial_acl_fd(fd, path)
            identity = (before.st_dev, before.st_ino)
            source_mode = stat.S_IMODE(before.st_mode)
            if (not stat.S_ISREG(before.st_mode)
                    or not stat.S_ISREG(entry.st_mode)
                    or before.st_uid != os.geteuid()
                    or entry.st_uid != os.geteuid()
                    or before.st_nlink != 1 or entry.st_nlink != 1
                    or before.st_size != ref.length
                    or entry.st_size != ref.length
                    or (entry.st_dev, entry.st_ino) != identity
                    or stat.S_IMODE(entry.st_mode) != source_mode
                    or not source_mode & 0o400
                    or trusted_fs._regular_stream_signature(before)
                    != trusted_fs._regular_stream_signature(entry)):
                raise ValueError(f"object cannot be safely sealed: {path}")
        finally:
            if fd is not None:
                os.close(fd)
            os.close(parent_fd)

        sealed_mode = source_mode & 0o444
        trusted_fs.atomic_write_stream(
            path,
            trusted_fs.iter_verified_regular_chunks(
                path, chunk_size=READ_SIZE, require_owner=True,
                required_mode=source_mode, expected_length=ref.length,
                expected_sha256=ref.sha256,
            ),
            expected_length=ref.length,
            expected_sha256=ref.sha256,
            mode=sealed_mode,
            replace=True,
        )
        _verify_staged(path, ref)
        parent_fd = trusted_fs.open_trusted_directory_fd(path.parent)
        try:
            final = os.stat(
                path.name, dir_fd=parent_fd, follow_symlinks=False
            )
            if ((final.st_dev, final.st_ino) == identity
                    or not stat.S_ISREG(final.st_mode)
                    or final.st_uid != os.geteuid()
                    or final.st_nlink != 1
                    or final.st_size != ref.length
                    or stat.S_IMODE(final.st_mode) != sealed_mode):
                raise ValueError(f"object replacement seal is invalid: {path}")
        finally:
            os.close(parent_fd)
        sealed.append(ref)
    return tuple(sealed)


def object_closure_sha256(
        values: Iterable[LogicalObjectRef | dict],
) -> str:
    objects: dict[str, dict[str, object]] = {}
    for value in values:
        ref = validate_logical_object_ref(value)
        encoded = ref.to_dict()
        previous = objects.setdefault(ref.sha256, encoded)
        if previous != encoded:
            raise ValueError("logical object SHA-256 has conflicting descriptors")
    return hashlib.sha256(_canonical_json(objects)).hexdigest()


def verify_object_closure(
        data_root: str | Path,
        values: Iterable[LogicalObjectRef | dict],
        staged_objects: Iterable[StagedObject] = (),
) -> dict[str, int | str]:
    logical: dict[str, LogicalObjectRef] = {}
    leaves: dict[str, ObjectRef] = {}
    for value in values:
        ref = validate_logical_object_ref(value)
        previous = logical.setdefault(ref.sha256, ref)
        if previous != ref:
            raise ValueError("logical object closure has conflicting descriptors")
        for leaf in ref.leaves:
            existing = leaves.setdefault(leaf.sha256, leaf)
            if existing != leaf:
                raise ValueError("object closure has conflicting leaf descriptors")
    stages = tuple(staged_objects)
    for ref in logical.values():
        for _chunk in iter_object_bytes(data_root, ref, stages):
            pass
    return {
        "logical_objects": len(logical),
        "leaves": len(leaves),
        "unique_bytes": sum(ref.length for ref in leaves.values()),
        "max_leaf": max((ref.length for ref in leaves.values()), default=0),
        "object_closure_sha256": object_closure_sha256(logical.values()),
    }


def _read_bounded_object(
        data_root: Path, value: ObjectRef | LogicalObjectRef,
        maximum: int, label: str,
) -> bytes:
    chunks = []
    length = 0
    for chunk in iter_object_bytes(data_root, value):
        length += len(chunk)
        if length > maximum:
            raise ValueError(f"{label} exceeds {maximum} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


def read_bounded_regular(path: Path, maximum: int, label: str) -> bytes:
    """Capture one stable regular file and stop before exceeding ``maximum``."""
    if type(maximum) is not int or maximum < 0:
        raise ValueError("bounded regular maximum must be a nonnegative integer")
    chunks = []
    length = 0
    for chunk in trusted_fs.iter_verified_regular_chunks(
            path, chunk_size=READ_SIZE, require_owner=True):
        length += len(chunk)
        if length > maximum:
            if (label == "publication proof registry"
                    and maximum == REGISTRY_MAX_BYTES):
                raise ValueError(registry_cap_message(label))
            raise ValueError(f"{label} exceeds {maximum} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


def _seal_registry_objects_locked(
        registry_path: str | Path,
) -> dict[str, int | str]:
    """Seal one registry closure while its canonical writer lease is held."""
    path = Path(registry_path).expanduser().absolute()
    raw = read_bounded_regular(
        path, REGISTRY_MAX_BYTES, "publication proof registry"
    )
    registry = _strict_json(raw, "publication proof registry")
    if (not isinstance(registry, dict)
            or set(registry) != {"version", "kind", "proofs"}
            or registry.get("version") != REGISTRY_VERSION
            or registry.get("kind") != REGISTRY_KIND
            or not isinstance(registry.get("proofs"), dict)):
        raise ValueError("only compact publication proof registry v2 can be sealed")

    manifest_refs = []
    for proof_sha, descriptor in registry["proofs"].items():
        ref = validate_object_ref(descriptor)
        if ref.sha256 != proof_sha or ref.length > PROOF_MANIFEST_MAX_BYTES:
            raise ValueError("publication proof manifest descriptor is invalid")
        manifest_refs.append(ref)
    seal_object_permissions(path.parent, manifest_refs)

    manifests = []
    all_logical: dict[str, LogicalObjectRef] = {}
    all_leaves: dict[str, ObjectRef] = {}
    for ref in sorted(manifest_refs, key=lambda value: value.sha256):
        manifest_raw = _read_bounded_object(
            path.parent, ref, PROOF_MANIFEST_MAX_BYTES,
            "publication proof manifest",
        )
        manifest = _strict_json(
            manifest_raw, "publication proof manifest"
        )
        if (not isinstance(manifest, dict)
                or manifest.get("version") != PROOF_MANIFEST_VERSION
                or manifest.get("kind") != PROOF_MANIFEST_KIND
                or not isinstance(manifest.get("objects"), dict)
                or not manifest["objects"]):
            raise ValueError("publication proof manifest v4 is malformed")
        logical_values = []
        for object_sha, descriptor in manifest["objects"].items():
            logical = validate_logical_object_ref(descriptor)
            if logical.sha256 != object_sha:
                raise ValueError("proof manifest object key/hash mismatch")
            previous = all_logical.setdefault(logical.sha256, logical)
            if previous != logical:
                raise ValueError("proof manifests disagree on an object descriptor")
            logical_values.append(logical)
            for leaf in logical.leaves:
                existing = all_leaves.setdefault(leaf.sha256, leaf)
                if existing != leaf:
                    raise ValueError("proof manifests disagree on a leaf descriptor")
        if (manifest.get("object_closure_sha256")
                != object_closure_sha256(logical_values)):
            raise ValueError("proof manifest object closure hash mismatch")
        manifests.append((manifest, tuple(logical_values)))

    seal_object_permissions(path.parent, all_leaves.values())
    for _manifest, logical_values in manifests:
        verify_object_closure(path.parent, logical_values)
    result = verify_object_closure(path.parent, all_logical.values())
    return {
        "manifests": len(manifest_refs),
        **result,
    }


def seal_registry_objects(registry_path: str | Path) -> dict[str, int | str]:
    """Seal a registry closure under its registry/object-tree writer lease."""
    path = Path(registry_path).expanduser().absolute()
    resources = tr.publication_registry_object_resources(path)
    resource_modes = tuple(
        (resource, fcntl.LOCK_EX) for resource in resources
    )
    with trusted_fs.locked_resources(
            path.parent, resource_modes, tr.resource_lock_path):
        return _seal_registry_objects_locked(path)


_PUBLICATION_JOURNAL_KEYS = {
    "version", "kind", "plan_id", "transaction_id",
    "store_before_sha256", "store_after_sha256",
    "proof_before_sha256", "proof_after_sha256",
    "floor_before_sha256", "floor_after_sha256",
    "objects", "publication_trust_root", "authorization", "paths",
}
_PUBLICATION_PATH_KEYS = {
    "store", "proof", "floor", "store_stage", "proof_stage", "floor_stage",
    "store_backup", "proof_backup", "floor_backup", "journal", "archive",
}


def _archived_journal_descriptors(
        document: object, journal_path: Path,
) -> tuple[tuple[ObjectRef, ...], Path]:
    if (not isinstance(document, dict)
            or set(document) != _PUBLICATION_JOURNAL_KEYS
            or document.get("version") != PUBLICATION_TRANSACTION_VERSION
            or document.get("kind") != PUBLICATION_TRANSACTION_KIND):
        raise ValueError("archived publication journal schema mismatch")
    for field in ("plan_id", "transaction_id"):
        if not _valid_sha256(document.get(field)):
            raise ValueError(f"archived publication journal {field} is invalid")
    for prefix in ("store", "proof", "floor"):
        before = document.get(f"{prefix}_before_sha256")
        after = document.get(f"{prefix}_after_sha256")
        if before is not None and not _valid_sha256(before):
            raise ValueError(
                f"archived publication journal {prefix} before hash is invalid"
            )
        if not _valid_sha256(after):
            raise ValueError(
                f"archived publication journal {prefix} after hash is invalid"
            )

    paths = document.get("paths")
    if not isinstance(paths, dict):
        raise ValueError("archived publication journal paths are malformed")
    path_keys = set(paths)
    extras = path_keys - _PUBLICATION_PATH_KEYS
    if (not _PUBLICATION_PATH_KEYS.issubset(path_keys)
            or extras not in (set(), {"candidate", "candidate_stage"})):
        raise ValueError("archived publication journal path set is malformed")
    if ("candidate" in path_keys) != ("candidate_stage" in path_keys):
        raise ValueError("archived publication journal candidate paths are partial")
    for name, value in paths.items():
        if (not isinstance(value, str) or not Path(value).is_absolute()
                or Path(value) != Path(os.path.abspath(value))):
            raise ValueError(
                f"archived publication journal path {name} is noncanonical"
            )
    recorded_archive = Path(paths["archive"])
    if (recorded_archive.parent.name != "Archive"
            or recorded_archive.name != journal_path.name
            or not recorded_archive.name.endswith(
                f".{document['transaction_id']}.json")):
        raise ValueError("archived publication journal filename is noncanonical")
    if "Archive" not in journal_path.parts:
        raise ValueError("publication journal must be read from an Archive path")

    objects = document.get("objects")
    if not isinstance(objects, list) or not objects:
        raise ValueError("archived publication journal has no staged objects")
    refs = []
    original_stage_root = None
    seen = set()
    for entry in objects:
        if not isinstance(entry, dict) or set(entry) != {"object", "stage_path"}:
            raise ValueError("archived publication journal object entry is malformed")
        ref = validate_object_ref(entry["object"])
        raw_stage = entry.get("stage_path")
        if (not isinstance(raw_stage, str) or not Path(raw_stage).is_absolute()
                or Path(raw_stage) != Path(os.path.abspath(raw_stage))):
            raise ValueError("archived publication object stage path is noncanonical")
        stage = Path(raw_stage)
        if stage.name != ref.sha256:
            raise ValueError("archived publication object stage name/hash mismatch")
        if (stage.parent.parent.name != "object-stages"
                or stage.parent.parent.parent.name
                != ".trekdex-publication-transactions"
                or not _valid_sha256(stage.parent.name)):
            raise ValueError("archived publication object stage root is noncanonical")
        if original_stage_root is None:
            original_stage_root = stage.parent
        elif stage.parent != original_stage_root:
            raise ValueError("archived publication journal spans stage directories")
        if ref.sha256 in seen:
            raise ValueError("archived publication journal repeats a staged object")
        seen.add(ref.sha256)
        refs.append(ref)
    if [ref.sha256 for ref in refs] != sorted(seen):
        raise ValueError("archived publication journal object order is noncanonical")

    targets = {prefix: paths[prefix] for prefix in ("store", "proof", "floor")}
    after_hashes = {
        prefix: document[f"{prefix}_after_sha256"]
        for prefix in ("store", "proof", "floor")
    }
    before_hashes = {
        prefix: document[f"{prefix}_before_sha256"]
        for prefix in ("store", "proof", "floor")
    }
    plan_identity = {
        "version": PUBLICATION_TRANSACTION_VERSION,
        "kind": "parking-publication-plan",
        "targets": targets,
        "after_sha256": after_hashes,
        "objects": objects,
        "publication_trust_root": document["publication_trust_root"],
        "authorization": document["authorization"],
    }
    transaction_identity = {
        "version": PUBLICATION_TRANSACTION_VERSION,
        "kind": "parking-publication-transaction-identity",
        "targets": targets,
        "before_sha256": before_hashes,
        "after_sha256": after_hashes,
        "objects": objects,
        "publication_trust_root": document["publication_trust_root"],
        "authorization": document["authorization"],
    }
    if (hashlib.sha256(_canonical_json(plan_identity)).hexdigest()
            != document["plan_id"]):
        raise ValueError("archived publication journal plan hash mismatch")
    if (hashlib.sha256(_canonical_json(transaction_identity)).hexdigest()
            != document["transaction_id"]):
        raise ValueError("archived publication journal transaction hash mismatch")
    return tuple(refs), original_stage_root


def verify_archived_stage(
        journal_path: str | Path, stage_directory: str | Path,
) -> dict[str, int | str | bool]:
    """Enumerate and verify one archived journal's exact forensic stage."""
    journal = Path(journal_path).expanduser().absolute()
    stage_root = Path(stage_directory).expanduser().absolute()
    raw = read_bounded_regular(
        journal, STAGE_JOURNAL_MAX_BYTES, "archived publication journal"
    )
    document = _strict_json(raw, "archived publication journal")
    refs, original_stage_root = _archived_journal_descriptors(
        document, journal
    )
    if stage_root.name != original_stage_root.name:
        raise ValueError("archived stage directory does not preserve its run id")

    root_fd = trusted_fs.open_trusted_directory_fd(stage_root)
    try:
        before = os.fstat(root_fd)
        before_entry = os.stat(stage_root, follow_symlinks=False)
        names = sorted(os.listdir(root_fd))
        expected_names = [ref.sha256 for ref in refs]
        if names != expected_names:
            raise ValueError(
                "archived stage file set differs from journal descriptors"
            )
        for ref in refs:
            _verify_staged(stage_root / ref.sha256, ref)
        after = os.fstat(root_fd)
        after_entry = os.stat(stage_root, follow_symlinks=False)
        if (sorted(os.listdir(root_fd)) != names
                or trusted_fs._regular_stream_signature(before)
                != trusted_fs._regular_stream_signature(after)
                or trusted_fs._regular_stream_signature(before_entry)
                != trusted_fs._regular_stream_signature(after_entry)
                or (before.st_dev, before.st_ino)
                != (before_entry.st_dev, before_entry.st_ino)
                or (after.st_dev, after.st_ino)
                != (after_entry.st_dev, after_entry.st_ino)):
            raise ValueError("archived stage directory changed during verification")
    finally:
        os.close(root_fd)

    descriptor_bytes = _canonical_json([ref.to_dict() for ref in refs])
    return {
        "transaction_id": document["transaction_id"],
        "journal_sha256": hashlib.sha256(raw).hexdigest(),
        "stage_run_id": stage_root.name,
        "descriptor_count": len(refs),
        "file_count": len(names),
        "unique_bytes": sum(ref.length for ref in refs),
        "hash_closure_sha256": hashlib.sha256(descriptor_bytes).hexdigest(),
        "stage_is_in_archive": "Archive" in stage_root.parts,
    }


def _canonical_absolute_move_path(
        value: str | Path, label: str,
) -> Path:
    try:
        raw = os.fspath(value)
    except TypeError as error:
        raise TypeError(f"{label} path must be path-like") from error
    if not isinstance(raw, str):
        raise TypeError(f"{label} path must be text")
    if (not raw or "\x00" in raw or not os.path.isabs(raw)
            or raw.startswith("//") or os.path.normpath(raw) != raw):
        raise ValueError(f"{label} path must be canonical and absolute")
    path = Path(raw)
    if not path.name:
        raise ValueError(f"{label} path cannot be a filesystem root")
    return path


def _move_source_signature(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        stat.S_IFMT(value.st_mode),
        stat.S_IMODE(value.st_mode),
        value.st_uid,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


_ARCHIVE_SIGNATURE_FIELDS = (
    "device", "inode", "file_type", "mode", "uid", "link_count",
    "size", "mtime_ns", "ctime_ns",
)


@dataclass
class _ArchiveMovePlan:
    source_path: Path
    destination_path: Path
    safety_path: Path
    source_parent_fd: int
    destination_parent_fd: int
    source_fd: int
    source_entry: os.stat_result
    source_signature: tuple[int, ...]
    kind: str
    safety_evidence: dict[str, int | str] | None = None
    destination_evidence: dict[str, int | str] | None = None


@dataclass
class _ArchiveAbsentPlan:
    source_path: Path
    destination_path: Path
    safety_path: Path
    source_parent_fd: int
    destination_parent_fd: int


class ArchiveBatchError(RuntimeError):
    """A batch stopped with durable state that requires explicit recovery."""

    def __init__(self, report: dict[str, object]):
        self.report = report
        super().__init__(json.dumps(
            report, sort_keys=True, separators=(",", ":"),
        ))


class Generation1RestoreError(RuntimeError):
    """A generation-1 restore stopped after durable PREPARED state."""

    def __init__(self, report: dict[str, object]):
        self.report = report
        super().__init__(json.dumps(
            report, sort_keys=True, separators=(",", ":"),
        ))


class _PostRenameError(RuntimeError):
    def __init__(self, phase: str, error: Exception):
        self.phase = phase
        self.error = error
        super().__init__(f"{phase}: {error}")


class ArchiveReportSetupError(RuntimeError):
    """The durable report namespace failed before archive mutation began."""


def _hashed_archive_report_document(
        value: dict[str, object],
) -> dict[str, object]:
    if "document_sha256" in value:
        raise ValueError("archive report document already has a hash")
    document = dict(value)
    document["document_sha256"] = hashlib.sha256(
        _canonical_json(document)
    ).hexdigest()
    return document


def _verify_archive_report_document_hash(
        value: object, expected_kind: str, *,
        expected_operation: str = ARCHIVE_REPORT_OPERATION,
) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("archive report document must be an object")
    document = dict(value)
    claimed = document.pop("document_sha256", None)
    if not _valid_sha256(claimed):
        raise ValueError("archive report document hash is invalid")
    if hashlib.sha256(_canonical_json(document)).hexdigest() != claimed:
        raise ValueError("archive report document hash mismatch")
    if (document.get("version") != ARCHIVE_REPORT_VERSION
            or document.get("kind") != expected_kind
            or document.get("operation") != expected_operation):
        raise ValueError("archive report document identity is invalid")
    return dict(value)


class _ArchiveReportJournal:
    """No-clobber, descriptor-held durable report namespace."""

    def __init__(self, report_directory: Path):
        self.path = report_directory
        self.report_id = secrets.token_hex(32)
        self.directory_fd: int | None = None
        self._names: set[str] = set()
        parent_fd = None
        try:
            parent_fd = trusted_fs.open_trusted_directory_fd(
                report_directory.parent
            )
            os.mkdir(report_directory.name, 0o700, dir_fd=parent_fd)
            flags = (
                os.O_RDONLY
                | trusted_fs._required_flag("O_DIRECTORY")
                | trusted_fs._required_flag("O_NOFOLLOW")
                | getattr(os, "O_CLOEXEC", 0)
            )
            self.directory_fd = os.open(
                report_directory.name, flags, dir_fd=parent_fd
            )
            report_stat = os.fstat(self.directory_fd)
            if (not stat.S_ISDIR(report_stat.st_mode)
                    or report_stat.st_uid != os.geteuid()
                    or stat.S_IMODE(report_stat.st_mode) != 0o700):
                raise ValueError(
                    "archive report namespace is not an owner-only directory"
                )
            trusted_fs.require_trivial_acl_fd(
                self.directory_fd, report_directory, is_directory=True
            )
            os.fsync(self.directory_fd)
            os.fsync(parent_fd)
        except BaseException as error:
            if self.directory_fd is not None:
                os.close(self.directory_fd)
                self.directory_fd = None
            raise ArchiveReportSetupError(
                "archive report namespace could not be created durably; "
                "no archive source or destination was mutated: "
                f"{report_directory}: {error}"
            ) from error
        finally:
            if parent_fd is not None:
                os.close(parent_fd)

    def write(
            self, name: str, value: dict[str, object],
    ) -> dict[str, object]:
        if (self.directory_fd is None or not name or "/" in name
                or name in (".", "..") or name in self._names):
            raise ValueError("archive report file name is invalid or reused")
        document = _hashed_archive_report_document(value)
        raw = _pretty_json(document)
        if len(raw) > ARCHIVE_REPORT_MAX_BYTES:
            raise ValueError("archive report document exceeds its byte limit")
        flags = (
            os.O_WRONLY | os.O_CREAT | os.O_EXCL
            | trusted_fs._required_flag("O_NOFOLLOW")
            | getattr(os, "O_CLOEXEC", 0)
        )
        try:
            fd = os.open(name, flags, 0o400, dir_fd=self.directory_fd)
            try:
                os.fchmod(fd, 0o400)
                offset = 0
                while offset < len(raw):
                    written = os.write(fd, raw[offset:])
                    if written <= 0:
                        raise OSError("archive report write made no progress")
                    offset += written
                os.fsync(fd)
            finally:
                os.close(fd)
            os.fsync(self.directory_fd)
        except BaseException:
            try:
                if _read_archive_report_file(
                        self.directory_fd, name) == document:
                    self._names.add(name)
            except BaseException:
                pass
            raise
        self._names.add(name)
        return document

    def close(self) -> None:
        if self.directory_fd is not None:
            os.close(self.directory_fd)
            self.directory_fd = None


def _canonical_absolute_resource_path(
        value: str | Path, label: str,
) -> Path:
    path = _canonical_absolute_move_path(value, label)
    return path


def _archive_safety_path(
        destination: Path, batch_id: str, index: int,
) -> Path:
    return destination.with_name(
        f".trekdex-archive-safety-{batch_id[:20]}-{index:04d}"
    )


def _paths_overlap(first: Path, second: Path) -> bool:
    return first == second or first in second.parents or second in first.parents


def _canonical_archive_pairs(
        pairs: Iterable[tuple[str | Path, str | Path]],
        optional_pairs: Iterable[tuple[str | Path, str | Path]] = (),
) -> tuple[
        str,
        tuple[tuple[Path, Path, Path], ...],
        tuple[tuple[Path, Path, Path], ...],
]:
    def canonicalize(
            values: Iterable[tuple[str | Path, str | Path]], label: str,
    ) -> list[tuple[Path, Path]]:
        try:
            raw_pairs = tuple(values)
        except TypeError as error:
            raise TypeError(f"archive batch {label} pairs must be iterable") from error
        result = []
        for index, pair in enumerate(raw_pairs):
            if (not isinstance(pair, (tuple, list)) or len(pair) != 2):
                raise ValueError(
                    f"archive batch {label} pair {index} must contain source "
                    "and destination"
                )
            source = _canonical_absolute_move_path(pair[0], "source")
            destination = _canonical_absolute_move_path(
                pair[1], "destination"
            )
            if source == destination:
                raise ValueError(
                    "source and destination paths must be distinct"
                )
            result.append((source, destination))
        return result

    required = canonicalize(pairs, "required")
    optional = canonicalize(optional_pairs, "optional")
    if not required and not optional:
        raise ValueError(
            "archive batch requires at least one required or optional "
            "source/destination pair"
        )
    if len(required) + len(optional) > ARCHIVE_REPORT_MAX_MOVES:
        raise ValueError(
            f"archive batch exceeds {ARCHIVE_REPORT_MAX_MOVES} moves"
        )

    endpoints = [path for pair in required + optional for path in pair]
    for index, first in enumerate(endpoints):
        for second in endpoints[index + 1:]:
            if _paths_overlap(first, second):
                raise ValueError(
                    "archive batch source/destination paths collide or overlap"
                )

    batch_id = hashlib.sha256(_canonical_json({
        "required": [
            [str(source), str(destination)]
            for source, destination in required
        ],
        "optional": [
            [str(source), str(destination)]
            for source, destination in optional
        ],
    })).hexdigest()
    all_pairs = required + optional
    canonical = tuple(
        (source, destination, _archive_safety_path(destination, batch_id, index))
        for index, (source, destination) in enumerate(all_pairs)
    )
    all_paths = [path for values in canonical for path in values]
    for index, first in enumerate(all_paths):
        for second in all_paths[index + 1:]:
            if _paths_overlap(first, second):
                raise ValueError(
                    "archive batch source/destination/safety paths collide or "
                    "overlap"
                )
    required_count = len(required)
    return (
        batch_id,
        canonical[:required_count],
        canonical[required_count:],
    )


def _canonical_archive_report_path(
        value: str | Path | None, batch_id: str,
        canonical_pairs: tuple[tuple[Path, Path, Path], ...],
) -> Path:
    if value is None:
        report = canonical_pairs[0][1].parent / (
            f".trekdex-archive-report-{batch_id[:12]}-"
            f"{secrets.token_hex(8)}"
        )
    else:
        report = _canonical_absolute_move_path(value, "archive report directory")
    for paths in canonical_pairs:
        for path in paths:
            if _paths_overlap(report, path):
                raise ValueError(
                    "archive report directory must not overlap a move path"
                )
    return report


def _archive_planned_moves(
        canonical_pairs: tuple[tuple[Path, Path, Path], ...],
) -> list[dict[str, object]]:
    return [
        {
            "index": index,
            "source": str(source),
            "destination": str(destination),
            "safety_copy": str(safety),
        }
        for index, (source, destination, safety) in enumerate(canonical_pairs)
    ]


def _archive_signature_dict(
        signature: tuple[int, ...],
) -> dict[str, int]:
    if len(signature) != len(_ARCHIVE_SIGNATURE_FIELDS):
        raise ValueError("archive source signature is malformed")
    return dict(zip(_ARCHIVE_SIGNATURE_FIELDS, signature))


def _archive_prepared_move(
        index: int, plan: _ArchiveMovePlan,
) -> dict[str, object]:
    if plan.safety_evidence is None:
        raise ValueError("archive move lacks verified safety evidence")
    return {
        "index": index,
        "source": str(plan.source_path),
        "destination": str(plan.destination_path),
        "safety_copy": str(plan.safety_path),
        "kind": plan.kind,
        "device": plan.source_entry.st_dev,
        "inode": plan.source_entry.st_ino,
        "source_signature": _archive_signature_dict(plan.source_signature),
        "evidence": dict(plan.safety_evidence),
    }


def _archive_prepared_optional(
        index: int, plan: _ArchiveMovePlan | _ArchiveAbsentPlan,
) -> dict[str, object]:
    if isinstance(plan, _ArchiveAbsentPlan):
        return {
            "index": index,
            "source": str(plan.source_path),
            "destination": str(plan.destination_path),
            "safety_copy": str(plan.safety_path),
            "resolution": "absent",
        }
    return _archive_prepared_move(index, plan) | {"resolution": "present"}


def _require_archive_source_stat(
        value: os.stat_result, path: Path,
) -> str:
    if stat.S_ISLNK(value.st_mode):
        raise ValueError("move source must never be a symlink")
    if stat.S_ISREG(value.st_mode):
        kind = "file"
    elif stat.S_ISDIR(value.st_mode):
        kind = "directory"
    else:
        raise ValueError("move source must be a regular file or directory")
    if value.st_uid != os.geteuid():
        raise ValueError("move source must be owned by the current uid")
    if kind == "file" and value.st_nlink != 1:
        raise ValueError("move source file must have one link")
    if kind == "directory" and stat.S_IMODE(value.st_mode) & 0o022:
        raise ValueError(f"move source directory is not trusted: {path}")
    return kind


def _entry_absent(parent_fd: int, name: str, path: Path) -> None:
    import errno

    try:
        os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    raise FileExistsError(
        errno.EEXIST,
        "move destination already exists",
        str(path),
    )


def _open_archive_move_plan(
        source_path: Path, destination_path: Path, safety_path: Path, *,
        source_may_be_absent: bool = False,
) -> _ArchiveMovePlan | _ArchiveAbsentPlan:
    import errno

    source_parent_fd = trusted_fs.open_trusted_directory_fd(source_path.parent)
    destination_parent_fd = None
    source_fd = None
    try:
        destination_parent_fd = trusted_fs.open_trusted_directory_fd(
            destination_path.parent
        )
        try:
            source_entry = os.stat(
                source_path.name,
                dir_fd=source_parent_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            if not source_may_be_absent:
                raise
            _entry_absent(
                destination_parent_fd, destination_path.name, destination_path
            )
            _entry_absent(
                destination_parent_fd, safety_path.name, safety_path
            )
            return _ArchiveAbsentPlan(
                source_path=source_path,
                destination_path=destination_path,
                safety_path=safety_path,
                source_parent_fd=source_parent_fd,
                destination_parent_fd=destination_parent_fd,
            )
        kind = _require_archive_source_stat(source_entry, source_path)
        flags = (
            os.O_RDONLY
            | trusted_fs._required_flag("O_NOFOLLOW")
            | getattr(os, "O_CLOEXEC", 0)
        )
        if kind == "directory":
            flags |= trusted_fs._required_flag("O_DIRECTORY")
        source_fd = os.open(source_path.name, flags, dir_fd=source_parent_fd)
        opened_source = os.fstat(source_fd)
        source_signature = _move_source_signature(source_entry)
        if _move_source_signature(opened_source) != source_signature:
            raise ValueError("move source identity changed while opening")
        trusted_fs.require_trivial_acl_fd(
            source_fd, source_path, is_directory=(kind == "directory")
        )

        destination_parent = os.fstat(destination_parent_fd)
        if source_entry.st_dev != destination_parent.st_dev:
            raise OSError(
                errno.EXDEV,
                "source and destination must be on the same filesystem",
            )
        _entry_absent(
            destination_parent_fd, destination_path.name, destination_path
        )
        _entry_absent(destination_parent_fd, safety_path.name, safety_path)

        current_source = os.stat(
            source_path.name,
            dir_fd=source_parent_fd,
            follow_symlinks=False,
        )
        if (_move_source_signature(current_source) != source_signature
                or _move_source_signature(os.fstat(source_fd))
                != source_signature):
            raise ValueError("move source identity changed before safety copy")
        return _ArchiveMovePlan(
            source_path=source_path,
            destination_path=destination_path,
            safety_path=safety_path,
            source_parent_fd=source_parent_fd,
            destination_parent_fd=destination_parent_fd,
            source_fd=source_fd,
            source_entry=source_entry,
            source_signature=source_signature,
            kind=kind,
        )
    except BaseException:
        if source_fd is not None:
            os.close(source_fd)
        if destination_parent_fd is not None:
            os.close(destination_parent_fd)
        os.close(source_parent_fd)
        raise


def _close_archive_move_plan(plan: _ArchiveMovePlan) -> None:
    os.close(plan.source_fd)
    os.close(plan.destination_parent_fd)
    os.close(plan.source_parent_fd)


def _close_archive_absent_plan(plan: _ArchiveAbsentPlan) -> None:
    os.close(plan.destination_parent_fd)
    os.close(plan.source_parent_fd)


def _require_optional_absent(
        plan: _ArchiveAbsentPlan, phase: str,
) -> None:
    for parent_fd, path in (
        (plan.source_parent_fd, plan.source_path),
        (plan.destination_parent_fd, plan.destination_path),
        (plan.destination_parent_fd, plan.safety_path),
    ):
        try:
            os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            continue
        raise ValueError(
            f"optional archive path appeared {phase}: {path}"
        )


def _require_source_name(plan: _ArchiveMovePlan, phase: str) -> None:
    try:
        current = os.stat(
            plan.source_path.name,
            dir_fd=plan.source_parent_fd,
            follow_symlinks=False,
        )
    except FileNotFoundError as error:
        raise ValueError(
            f"move source disappeared {phase}: {plan.source_path}"
        ) from error
    if (_move_source_signature(current) != plan.source_signature
            or _move_source_signature(os.fstat(plan.source_fd))
            != plan.source_signature):
        raise ValueError(
            f"move source identity changed {phase}: {plan.source_path}"
        )


def _verified_regular_fd_chunks(
        fd: int, expected_signature: tuple[int, ...], label: Path,
) -> Iterator[bytes]:
    if _move_source_signature(os.fstat(fd)) != expected_signature:
        raise ValueError(f"archive source changed before copy: {label}")
    os.lseek(fd, 0, os.SEEK_SET)
    length = 0
    while True:
        chunk = os.read(fd, READ_SIZE)
        if not chunk:
            break
        if len(chunk) > READ_SIZE:
            raise ValueError(f"archive source returned oversized read: {label}")
        length += len(chunk)
        yield chunk
    after = os.fstat(fd)
    if (_move_source_signature(after) != expected_signature
            or length != after.st_size):
        raise ValueError(f"archive source changed during copy: {label}")


def _regular_fd_evidence(
        fd: int, expected_signature: tuple[int, ...], label: Path,
) -> dict[str, int | str]:
    digest = hashlib.sha256()
    length = 0
    for chunk in _verified_regular_fd_chunks(fd, expected_signature, label):
        digest.update(chunk)
        length += len(chunk)
    return {
        "kind": "file",
        "length": length,
        "sha256": digest.hexdigest(),
    }


def _open_archive_child(
        parent_fd: int, name: str, entry: os.stat_result, label: Path,
) -> int:
    flags = (
        os.O_RDONLY
        | trusted_fs._required_flag("O_NOFOLLOW")
        | getattr(os, "O_CLOEXEC", 0)
    )
    if stat.S_ISDIR(entry.st_mode):
        flags |= trusted_fs._required_flag("O_DIRECTORY")
    child_fd = os.open(name, flags, dir_fd=parent_fd)
    opened = os.fstat(child_fd)
    if _move_source_signature(opened) != _move_source_signature(entry):
        os.close(child_fd)
        raise ValueError(f"archive tree entry changed while opening: {label}")
    return child_fd


def _require_archive_tree_entry(
        entry: os.stat_result, label: Path,
) -> str:
    if entry.st_uid != os.geteuid():
        raise ValueError(f"archive tree entry is not owned by current uid: {label}")
    if stat.S_ISREG(entry.st_mode):
        if entry.st_nlink != 1:
            raise ValueError(f"archive tree file must have one link: {label}")
        return "file"
    if stat.S_ISDIR(entry.st_mode):
        if stat.S_IMODE(entry.st_mode) & 0o022:
            raise ValueError(f"archive tree directory is not trusted: {label}")
        return "directory"
    if stat.S_ISLNK(entry.st_mode):
        raise ValueError(f"archive tree must not contain symlinks: {label}")
    raise ValueError(f"archive tree contains a special file: {label}")


def _tree_evidence(
        fd: int, label: Path, *, require_safety_modes: bool = False,
        expected_evidence: dict[str, int | str] | None = None,
) -> dict[str, int | str]:
    digest = hashlib.sha256()
    totals = {"directories": 0, "files": 0, "bytes": 0}
    maximum_entries = None
    if expected_evidence is not None:
        maximum_entries = (
            int(expected_evidence["directories"])
            + int(expected_evidence["files"])
        )

    def record(value: dict[str, object]) -> None:
        digest.update(_canonical_json(value))
        digest.update(b"\n")

    def names_in(directory_fd: int) -> list[str]:
        if maximum_entries is None:
            return sorted(os.listdir(directory_fd))
        names = []
        with os.scandir(directory_fd) as entries:
            for entry in entries:
                names.append(entry.name)
                if len(names) > maximum_entries:
                    raise ValueError(
                        "archive tree exceeds PREPARED entry bounds"
                    )
        return sorted(names)

    def visit(directory_fd: int, relative: PurePosixPath) -> None:
        before = os.fstat(directory_fd)
        if _require_archive_tree_entry(before, label / relative) != "directory":
            raise ValueError(f"archive tree root is not a directory: {label}")
        if (require_safety_modes
                and stat.S_IMODE(before.st_mode) != 0o700):
            raise ValueError(
                f"archive safety directory mode changed: {label / relative}"
            )
        trusted_fs.require_trivial_acl_fd(
            directory_fd, label / relative, is_directory=True
        )
        signature = _move_source_signature(before)
        names = names_in(directory_fd)
        totals["directories"] += 1
        if (expected_evidence is not None
                and totals["directories"]
                > expected_evidence["directories"]):
            raise ValueError("archive tree exceeds PREPARED directory bounds")
        record({"kind": "directory", "path": relative.as_posix()})
        for name in names:
            if (not isinstance(name, str) or not name or name in (".", "..")
                    or "/" in name or "\x00" in name):
                raise ValueError(f"archive tree contains an invalid name: {label}")
            child_relative = relative / name
            child_label = label.joinpath(*child_relative.parts)
            entry = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            kind = _require_archive_tree_entry(entry, child_label)
            child_fd = _open_archive_child(
                directory_fd, name, entry, child_label
            )
            try:
                if kind == "directory":
                    visit(child_fd, child_relative)
                else:
                    if (expected_evidence is not None
                            and (totals["files"] + 1
                                 > expected_evidence["files"]
                                 or totals["bytes"] + entry.st_size
                                 > expected_evidence["bytes"])):
                        raise ValueError(
                            "archive tree exceeds PREPARED file/byte bounds"
                        )
                    if (require_safety_modes
                            and stat.S_IMODE(entry.st_mode) != 0o400):
                        raise ValueError(
                            f"archive safety file mode changed: {child_label}"
                        )
                    trusted_fs.require_trivial_acl_fd(child_fd, child_label)
                    evidence = _regular_fd_evidence(
                        child_fd, _move_source_signature(entry), child_label
                    )
                    totals["files"] += 1
                    totals["bytes"] += evidence["length"]
                    record({
                        "kind": "file",
                        "path": child_relative.as_posix(),
                        "length": evidence["length"],
                        "sha256": evidence["sha256"],
                    })
            finally:
                os.close(child_fd)
            entry_after = os.stat(
                name, dir_fd=directory_fd, follow_symlinks=False
            )
            if _move_source_signature(entry_after) != _move_source_signature(entry):
                raise ValueError(f"archive tree entry changed: {child_label}")
        if (names_in(directory_fd) != names
                or _move_source_signature(os.fstat(directory_fd)) != signature):
            raise ValueError(f"archive tree directory changed: {label / relative}")

    visit(fd, PurePosixPath("."))
    result = {
        "kind": "directory",
        "directories": totals["directories"],
        "files": totals["files"],
        "bytes": totals["bytes"],
        "tree_sha256": digest.hexdigest(),
    }
    if expected_evidence is not None and result != expected_evidence:
        raise ValueError("archive tree differs from PREPARED evidence")
    return result


def _copy_archive_tree(
        source_fd: int, source_label: Path,
        destination_fd: int, destination_path: Path,
) -> None:
    before = os.fstat(source_fd)
    source_signature = _move_source_signature(before)
    names = sorted(os.listdir(source_fd))
    for name in names:
        child_label = source_label / name
        entry = os.stat(name, dir_fd=source_fd, follow_symlinks=False)
        kind = _require_archive_tree_entry(entry, child_label)
        child_fd = _open_archive_child(source_fd, name, entry, child_label)
        try:
            if kind == "directory":
                destination_child = destination_path / name
                destination_child_fd = trusted_fs.create_trusted_directory_fd(
                    destination_fd, destination_path, name, create_mode=0o700
                )
                try:
                    _copy_archive_tree(
                        child_fd, child_label,
                        destination_child_fd, destination_child,
                    )
                    os.fsync(destination_child_fd)
                finally:
                    os.close(destination_child_fd)
            else:
                trusted_fs.require_trivial_acl_fd(child_fd, child_label)
                child_signature = _move_source_signature(entry)
                evidence = _regular_fd_evidence(
                    child_fd, child_signature, child_label
                )
                trusted_fs.atomic_write_stream(
                    destination_path / name,
                    _verified_regular_fd_chunks(
                        child_fd, child_signature, child_label
                    ),
                    expected_length=evidence["length"],
                    expected_sha256=evidence["sha256"],
                    mode=0o400,
                )
        finally:
            os.close(child_fd)
        entry_after = os.stat(name, dir_fd=source_fd, follow_symlinks=False)
        if _move_source_signature(entry_after) != _move_source_signature(entry):
            raise ValueError(f"archive tree entry changed: {child_label}")
    if (sorted(os.listdir(source_fd)) != names
            or _move_source_signature(os.fstat(source_fd)) != source_signature):
        raise ValueError(f"archive tree directory changed: {source_label}")


def _create_verified_safety_copy(
        plan: _ArchiveMovePlan,
) -> dict[str, int | str]:
    _require_source_name(plan, "before safety copy")
    if plan.kind == "file":
        source_before = _regular_fd_evidence(
            plan.source_fd, plan.source_signature, plan.source_path
        )
        trusted_fs.atomic_write_stream(
            plan.safety_path,
            _verified_regular_fd_chunks(
                plan.source_fd, plan.source_signature, plan.source_path
            ),
            expected_length=source_before["length"],
            expected_sha256=source_before["sha256"],
            mode=0o400,
        )
        source_after = _regular_fd_evidence(
            plan.source_fd, plan.source_signature, plan.source_path
        )
        safety_after = None
        for _chunk in trusted_fs.iter_verified_regular_chunks(
                plan.safety_path, chunk_size=READ_SIZE,
                require_owner=True, required_mode=0o400,
                expected_length=source_before["length"],
                expected_sha256=source_before["sha256"]):
            pass
        safety_after = source_before
        if source_after != source_before:
            raise ValueError("archive source changed while safety copy was made")
    else:
        source_before = _tree_evidence(plan.source_fd, plan.source_path)
        safety_fd = trusted_fs.create_trusted_directory_fd(
            plan.destination_parent_fd,
            plan.destination_path.parent,
            plan.safety_path.name,
            create_mode=0o700,
        )
        try:
            _copy_archive_tree(
                plan.source_fd, plan.source_path,
                safety_fd, plan.safety_path,
            )
            os.fsync(safety_fd)
        finally:
            os.close(safety_fd)
        source_after = _tree_evidence(plan.source_fd, plan.source_path)
        safety_fd = trusted_fs.open_trusted_directory_fd(plan.safety_path)
        try:
            safety_after = _tree_evidence(
                safety_fd, plan.safety_path, require_safety_modes=True
            )
        finally:
            os.close(safety_fd)
        if source_after != source_before or safety_after != source_before:
            raise ValueError("archive directory safety copy is not exact")

    _require_source_name(plan, "after verified safety copy")
    return dict(safety_after)


def _measure_archive_entry(
        parent_fd: int, name: str, path: Path, kind: str, *,
        require_safety_modes: bool,
        expected_evidence: dict[str, int | str] | None = None,
) -> dict[str, int | str]:
    entry = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if _require_archive_source_stat(entry, path) != kind:
        raise ValueError(f"archive evidence kind changed: {path}")
    if (expected_evidence is not None and kind == "file"
            and entry.st_size != expected_evidence["length"]):
        raise ValueError("archive file exceeds or differs from PREPARED length")
    flags = (
        os.O_RDONLY
        | trusted_fs._required_flag("O_NOFOLLOW")
        | getattr(os, "O_CLOEXEC", 0)
    )
    if kind == "directory":
        flags |= trusted_fs._required_flag("O_DIRECTORY")
    fd = os.open(name, flags, dir_fd=parent_fd)
    try:
        signature = _move_source_signature(entry)
        if _move_source_signature(os.fstat(fd)) != signature:
            raise ValueError(f"archive evidence entry changed while opening: {path}")
        trusted_fs.require_trivial_acl_fd(
            fd, path, is_directory=(kind == "directory")
        )
        if require_safety_modes:
            expected_mode = 0o400 if kind == "file" else 0o700
            if stat.S_IMODE(entry.st_mode) != expected_mode:
                raise ValueError(f"archive safety image mode changed: {path}")
        if kind == "file":
            evidence = _regular_fd_evidence(fd, signature, path)
        else:
            evidence = _tree_evidence(
                fd, path,
                require_safety_modes=require_safety_modes,
                expected_evidence=expected_evidence,
            )
        if expected_evidence is not None and evidence != expected_evidence:
            raise ValueError("archive image differs from PREPARED evidence")
        after = os.fstat(fd)
        after_entry = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (_move_source_signature(after) != signature
                or _move_source_signature(after_entry) != signature):
            raise ValueError(f"archive evidence entry changed: {path}")
        return evidence
    finally:
        os.close(fd)


def _measure_archive_path(
        path: Path, kind: str, *, require_safety_modes: bool,
        expected_evidence: dict[str, int | str] | None = None,
) -> dict[str, int | str]:
    parent_fd = trusted_fs.open_trusted_directory_fd(path.parent)
    try:
        return _measure_archive_entry(
            parent_fd, path.name, path, kind,
            require_safety_modes=require_safety_modes,
            expected_evidence=expected_evidence,
        )
    finally:
        os.close(parent_fd)


def _sync_and_verify_rename(plan: _ArchiveMovePlan) -> None:
    destination_fd = None
    try:
        try:
            flags = (
                os.O_RDONLY
                | trusted_fs._required_flag("O_NOFOLLOW")
                | getattr(os, "O_CLOEXEC", 0)
            )
            if plan.kind == "directory":
                flags |= trusted_fs._required_flag("O_DIRECTORY")
            destination_fd = os.open(
                plan.destination_path.name, flags,
                dir_fd=plan.destination_parent_fd,
            )
        except Exception as error:
            raise _PostRenameError("open-destination-content", error) from error
        try:
            os.fsync(destination_fd)
        except Exception as error:
            raise _PostRenameError("sync-destination-content", error) from error
        try:
            os.fsync(plan.destination_parent_fd)
        except Exception as error:
            raise _PostRenameError("sync-destination-parent", error) from error
        try:
            os.fsync(plan.source_parent_fd)
        except Exception as error:
            raise _PostRenameError("sync-source-parent", error) from error

        try:
            try:
                os.stat(
                    plan.source_path.name,
                    dir_fd=plan.source_parent_fd,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                pass
            else:
                raise ValueError("move source still exists after rename")
            destination_entry = os.stat(
                plan.destination_path.name,
                dir_fd=plan.destination_parent_fd,
                follow_symlinks=False,
            )
            expected = plan.source_signature
            current = _move_source_signature(os.fstat(destination_fd))
            if (_move_source_signature(os.fstat(plan.source_fd)) != current
                    or _move_source_signature(destination_entry) != current
                    or current[:-1] != expected[:-1]
                    or current[-1] < expected[-1]):
                raise ValueError(
                    "move destination is not the exact source inode after rename"
                )
            trusted_fs.require_trivial_acl_fd(
                destination_fd, plan.destination_path,
                is_directory=(plan.kind == "directory"),
            )
            prepared_evidence = plan.safety_evidence
            if prepared_evidence is None:
                raise ValueError("archive move lacks PREPARED safety evidence")
            if plan.kind == "file":
                destination_evidence = _regular_fd_evidence(
                    destination_fd, current, plan.destination_path
                )
            else:
                destination_evidence = _tree_evidence(
                    destination_fd,
                    plan.destination_path,
                    expected_evidence=prepared_evidence,
                )
            safety_evidence = _measure_archive_entry(
                plan.destination_parent_fd,
                plan.safety_path.name,
                plan.safety_path,
                plan.kind,
                require_safety_modes=True,
                expected_evidence=prepared_evidence,
            )
            if (destination_evidence != prepared_evidence
                    or safety_evidence != prepared_evidence):
                raise ValueError(
                    "move destination or safety image differs from PREPARED evidence"
                )
            if (_move_source_signature(os.fstat(plan.source_fd)) != current
                    or _move_source_signature(os.fstat(destination_fd)) != current
                    or _move_source_signature(os.stat(
                        plan.destination_path.name,
                        dir_fd=plan.destination_parent_fd,
                        follow_symlinks=False,
                    )) != current):
                raise ValueError(
                    "move destination changed during exact evidence verification"
                )
            plan.destination_evidence = dict(destination_evidence)
        except Exception as error:
            raise _PostRenameError("postcondition", error) from error
    finally:
        if destination_fd is not None:
            os.close(destination_fd)


def _archive_move_result(plan: _ArchiveMovePlan) -> dict[str, object]:
    if (plan.safety_evidence is None or plan.destination_evidence is None
            or plan.destination_evidence != plan.safety_evidence):
        raise ValueError("archive move lacks exact committed evidence")
    return {
        "status": "committed",
        "source": str(plan.source_path),
        "destination": str(plan.destination_path),
        "safety_copy": str(plan.safety_path),
        "kind": plan.kind,
        "device": plan.source_entry.st_dev,
        "inode": plan.source_entry.st_ino,
        "evidence": dict(plan.destination_evidence),
    }


def _archive_optional_result(
        index: int, plan: _ArchiveMovePlan | _ArchiveAbsentPlan,
) -> dict[str, object]:
    result: dict[str, object] = {
        "index": index,
        "source": str(plan.source_path),
        "destination": str(plan.destination_path),
        "safety_copy": str(plan.safety_path),
        "resolution": (
            "absent" if isinstance(plan, _ArchiveAbsentPlan) else "present"
        ),
    }
    if isinstance(plan, _ArchiveMovePlan):
        result["move"] = _archive_move_result(plan)
    return result


def _archive_lock_entries(lock_entries) -> list[dict[str, str]]:
    return [
        {"lock": str(lock_path), "resource": str(resource)}
        for lock_path, resource, _mode in lock_entries
    ]


def _archive_failure_report(
        *, journal: _ArchiveReportJournal, batch_id: str,
        plan_document: dict[str, object],
        lock_entries, plans: list[_ArchiveMovePlan],
        optional_plans: list[_ArchiveMovePlan | _ArchiveAbsentPlan],
        failed_index: int | None, failed_optional_index: int | None,
        phase: str, error: BaseException, archive_state: str,
) -> dict[str, object]:
    def reconciled_chain() -> tuple[
            dict[str, object], list[dict[str, object]],
            list[dict[str, object]], dict[str, object] | None,
            list[dict[str, object]], list[dict[str, object]],
            list[dict[str, object]], list[dict[str, object]],
            list[dict[str, object]],
    ]:
        if journal.directory_fd is None:
            raise ValueError("archive report journal is closed")
        report_fd = journal.directory_fd
        before = os.fstat(report_fd)
        signature = _move_source_signature(before)
        names = sorted(os.listdir(report_fd))
        if len(names) > ARCHIVE_REPORT_MAX_MOVES + 5:
            raise ValueError("archive report file count exceeds its bound")
        allowed_name = re.compile(
            r"(?:plan|prepared|terminal)\.json|"
            r"(?:optional-)?checkpoint-[0-9]{4}\.json|"
            r"generation-1-restore-(?:prepared|terminal)\.json"
        )
        entry_flags = (
            os.O_RDONLY
            | trusted_fs._required_flag("O_NOFOLLOW")
            | getattr(os, "O_CLOEXEC", 0)
        )
        for name in names:
            if allowed_name.fullmatch(name) is None:
                raise ValueError("archive report has an unexpected file name")
            entry_fd = os.open(name, entry_flags, dir_fd=report_fd)
            try:
                opened = os.fstat(entry_fd)
                entry = os.stat(
                    name, dir_fd=report_fd, follow_symlinks=False
                )
                entry_signature = _move_source_signature(opened)
                if (not stat.S_ISREG(opened.st_mode)
                        or opened.st_uid != os.geteuid()
                        or opened.st_nlink != 1
                        or stat.S_IMODE(opened.st_mode) != 0o400
                        or opened.st_size > ARCHIVE_REPORT_MAX_BYTES
                        or _move_source_signature(entry) != entry_signature):
                    raise ValueError(
                        f"archive report file is not trusted: {name}"
                    )
                trusted_fs.require_trivial_acl_fd(
                    entry_fd, journal.path / name
                )
                if _move_source_signature(os.fstat(entry_fd)) != entry_signature:
                    raise ValueError(
                        f"archive report file changed while reconciled: {name}"
                    )
            finally:
                os.close(entry_fd)

        disk_plan = _verify_archive_report_document_hash(
            _read_archive_report_file(report_fd, "plan.json"),
            _ARCHIVE_PLAN_KIND,
        )
        (
            disk_planned_moves,
            disk_planned_optional_moves,
            _canonical_required,
            _canonical_optional,
        ) = _validate_archive_plan(disk_plan, journal.path)
        if (disk_plan["document_sha256"]
                != plan_document["document_sha256"]
                or disk_plan["batch_id"] != batch_id
                or disk_plan["report_id"] != journal.report_id):
            raise ValueError(
                "archive report plan changed before failure recovery"
            )

        disk_prepared = None
        prepared_moves: list[dict[str, object]] = []
        prepared_optional: list[dict[str, object]] = []
        if "prepared.json" in names:
            try:
                candidate = _verify_archive_report_document_hash(
                    _read_archive_report_file(report_fd, "prepared.json"),
                    _ARCHIVE_PREPARED_KIND,
                )
                candidate_moves, candidate_optional = (
                    _validate_archive_prepared(
                        candidate,
                        disk_plan,
                        disk_planned_moves,
                        disk_planned_optional_moves,
                    )
                )
            except (OSError, ValueError):
                pass
            else:
                disk_prepared = candidate
                prepared_moves = candidate_moves
                prepared_optional = candidate_optional

        required_documents: list[dict[str, object]] = []
        required_moves: list[dict[str, object]] = []
        optional_documents: list[dict[str, object]] = []
        optional_resolutions: list[dict[str, object]] = []
        if disk_prepared is not None:
            previous_sha256 = disk_prepared["document_sha256"]
            for index, prepared_move in enumerate(prepared_moves):
                name = f"checkpoint-{index + 1:04d}.json"
                if name not in names:
                    break
                try:
                    checkpoint = _verify_archive_report_document_hash(
                        _read_archive_report_file(report_fd, name),
                        _ARCHIVE_CHECKPOINT_KIND,
                    )
                    move = _validate_archive_checkpoint(
                        checkpoint,
                        index,
                        disk_plan,
                        disk_prepared,
                        prepared_move,
                        previous_sha256,
                    )
                except (OSError, ValueError):
                    break
                required_documents.append(checkpoint)
                required_moves.append(move)
                previous_sha256 = checkpoint["document_sha256"]

            if len(required_documents) == len(prepared_moves):
                for index, prepared_resolution in enumerate(prepared_optional):
                    name = f"optional-checkpoint-{index + 1:04d}.json"
                    if name not in names:
                        break
                    try:
                        checkpoint = _verify_archive_report_document_hash(
                            _read_archive_report_file(report_fd, name),
                            _ARCHIVE_OPTIONAL_CHECKPOINT_KIND,
                        )
                        resolution = _validate_archive_optional_checkpoint(
                            checkpoint,
                            index,
                            disk_plan,
                            disk_prepared,
                            prepared_resolution,
                            previous_sha256,
                        )
                    except (OSError, ValueError):
                        break
                    optional_documents.append(checkpoint)
                    optional_resolutions.append(resolution)
                    previous_sha256 = checkpoint["document_sha256"]

        after = os.fstat(report_fd)
        if (sorted(os.listdir(report_fd)) != names
                or _move_source_signature(after) != signature):
            raise ValueError(
                "archive report directory changed during failure recovery"
            )
        return (
            disk_plan,
            disk_planned_moves,
            disk_planned_optional_moves,
            disk_prepared,
            prepared_optional,
            required_documents,
            required_moves,
            optional_documents,
            optional_resolutions,
        )

    (
        disk_plan_document,
        planned_moves,
        planned_optional_moves,
        disk_prepared_document,
        prepared_optional_resolutions,
        durable_required_documents,
        durable_required_moves,
        durable_optional_documents,
        durable_optional_resolutions,
    ) = reconciled_chain()

    def safety_verified(source: object) -> bool:
        return any(
            str(plan.source_path) == source
            and plan.safety_evidence is not None
            for plan in plans
        )

    def describe(index: int) -> dict[str, object]:
        value = dict(planned_moves[index])
        value.pop("index", None)
        value["safety_verified"] = safety_verified(value["source"])
        return value

    def describe_optional(index: int) -> dict[str, object]:
        value = dict(planned_optional_moves[index])
        value.pop("index", None)
        value["safety_verified"] = safety_verified(value["source"])
        if index < len(durable_optional_resolutions):
            value["resolution"] = durable_optional_resolutions[index][
                "resolution"
            ]
        elif disk_prepared_document is not None:
            value["resolution"] = prepared_optional_resolutions[index][
                "resolution"
            ]
        elif index < len(optional_plans):
            value["resolution"] = (
                "absent"
                if isinstance(optional_plans[index], _ArchiveAbsentPlan)
                else "present"
            )
        else:
            value["resolution"] = "unresolved"
        return value

    durable_completed = list(durable_required_moves)
    durable_completed.extend(
        dict(resolution["move"])
        for resolution in durable_optional_resolutions
        if resolution["resolution"] == "present"
    )
    durable_required_count = len(durable_required_documents)
    durable_failed_index = (
        failed_index
        if (failed_index is not None
            and 0 <= failed_index < len(planned_moves)
            and failed_index >= durable_required_count)
        else None
    )
    completed_optional_indexes = set(range(len(durable_optional_documents)))
    durable_failed_optional_index = (
        failed_optional_index
        if (failed_optional_index is not None
            and 0 <= failed_optional_index < len(planned_optional_moves)
            and failed_optional_index not in completed_optional_indexes)
        else None
    )
    unattempted_indexes = [
        index for index in range(durable_required_count, len(planned_moves))
        if index != durable_failed_index
    ]
    unresolved_optional_indexes = [
        index for index in range(len(planned_optional_moves))
        if (index not in completed_optional_indexes
            and index != durable_failed_optional_index)
    ]
    durable_chain = durable_required_documents + durable_optional_documents
    latest_checkpoint = (
        durable_chain[-1]["document_sha256"]
        if durable_chain else (
            disk_prepared_document["document_sha256"]
            if disk_prepared_document is not None
            else disk_plan_document["document_sha256"]
        )
    )
    if disk_prepared_document is not None:
        recorded_optional = [
            (
                dict(durable_optional_resolutions[index])
                if index < len(durable_optional_resolutions)
                else dict(prepared)
            )
            for index, prepared in enumerate(prepared_optional_resolutions)
        ]
    else:
        recorded_optional = [
            describe_optional(index)
            for index in range(len(planned_optional_moves))
        ]
    return {
        "version": ARCHIVE_REPORT_VERSION,
        "kind": _ARCHIVE_TERMINAL_KIND,
        "operation": ARCHIVE_REPORT_OPERATION,
        "batch_id": disk_plan_document["batch_id"],
        "report_id": disk_plan_document["report_id"],
        "report_directory": str(journal.path),
        "archive_state": archive_state,
        "blind_retry_forbidden": True,
        "plan_sha256": disk_plan_document["document_sha256"],
        "prepared_sha256": (
            disk_prepared_document["document_sha256"]
            if disk_prepared_document is not None else None
        ),
        "latest_checkpoint_sha256": latest_checkpoint,
        "completed_move_count": len(durable_completed),
        "completed_moves": durable_completed,
        "optional_resolutions": recorded_optional,
        "failed_move": (
            describe(durable_failed_index)
            if durable_failed_index is not None else None
        ),
        "failed_optional_move": (
            describe_optional(durable_failed_optional_index)
            if durable_failed_optional_index is not None else None
        ),
        "unattempted_moves": [
            describe(index) for index in unattempted_indexes
        ],
        "unresolved_optional_moves": [
            describe_optional(index) for index in unresolved_optional_indexes
        ],
        "verified_safety_copies": [
            {
                "source": str(plan.source_path),
                "path": str(plan.safety_path),
                "evidence": dict(plan.safety_evidence),
            }
            for plan in plans
            if plan.safety_evidence is not None
        ],
        "locks": _archive_lock_entries(lock_entries),
        "failure": {
            "phase": phase,
            "type": type(error).__name__,
            "message": str(error),
        },
        "recovery": (
            "Preserve the report directory and every source, destination, "
            "and safety_copy exactly as reported; inspect the durable "
            "PREPARED/checkpoint prefix and namespace state. Never blindly "
            "retry, remove a safety image, or overwrite a destination."
        ),
    }


def _rename_failure_state(plan: _ArchiveMovePlan) -> str:
    try:
        source = os.stat(
            plan.source_path.name,
            dir_fd=plan.source_parent_fd,
            follow_symlinks=False,
        )
    except (FileNotFoundError, OSError):
        source = None
    if (source is not None
            and _move_source_signature(source) == plan.source_signature
            and _move_source_signature(os.fstat(plan.source_fd))
            == plan.source_signature):
        return "not-committed"
    return "committed-indeterminate"


def _load_atomic_no_replace_rename():
    """Return the native descriptor-relative no-replace rename primitive."""
    import ctypes
    import errno
    import sys

    if sys.platform == "darwin":
        symbol_name = "renameatx_np"
        flag = 0x00000004  # RENAME_EXCL
    elif sys.platform.startswith("linux"):
        symbol_name = "renameat2"
        flag = 0x00000001  # RENAME_NOREPLACE
    else:
        raise OSError(
            errno.ENOTSUP,
            f"atomic no-replace rename is unsupported on {sys.platform}",
        )

    try:
        libc = ctypes.CDLL(None, use_errno=True)
        rename = getattr(libc, symbol_name)
    except (AttributeError, OSError) as error:
        raise OSError(
            errno.ENOTSUP,
            f"atomic no-replace rename syscall {symbol_name} is unavailable",
        ) from error
    rename.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    rename.restype = ctypes.c_int

    def invoke(
            source_parent_fd: int, source_name: str,
            destination_parent_fd: int, destination_name: str,
    ) -> None:
        ctypes.set_errno(0)
        result = rename(
            source_parent_fd, os.fsencode(source_name),
            destination_parent_fd, os.fsencode(destination_name), flag,
        )
        if result != 0:
            error_number = ctypes.get_errno() or errno.EIO
            raise OSError(
                error_number,
                f"atomic no-replace rename failed: "
                f"{source_name} -> {destination_name}",
            )

    return invoke


def archive_batch(
        pairs: Iterable[tuple[str | Path, str | Path]], *,
        optional_pairs: Iterable[tuple[str | Path, str | Path]] = (),
        lock_resources: Iterable[str | Path] = (),
        report_directory: str | Path | None = None,
) -> dict[str, object]:
    """Archive required and optional pairs under one exclusive lock lease.

    Every optional path is durably planned and locked before its source is
    classified under the lease. A canonical plan, exact PREPARED evidence, one
    chained checkpoint per required move and optional resolution, and a terminal
    state are each fsynced before the lease is released. POSIX locking remains
    cooperative: a malicious same-UID process that ignores the resource locks
    and tampers with both archive and report namespaces is outside the claimed
    threat model.
    """
    batch_id, canonical_pairs, canonical_optional_pairs = (
        _canonical_archive_pairs(pairs, optional_pairs)
    )
    all_canonical_pairs = canonical_pairs + canonical_optional_pairs
    report_path = _canonical_archive_report_path(
        report_directory, batch_id, all_canonical_pairs
    )
    try:
        explicit_resources = tuple(
            _canonical_absolute_resource_path(value, "lock resource")
            for value in lock_resources
        )
    except TypeError as error:
        raise TypeError("archive lock resources must be iterable paths") from error

    resource_paths = set(explicit_resources)
    for source, destination, safety in all_canonical_pairs:
        resource_paths.update((source, destination, safety))
    sorted_resources = tuple(sorted(resource_paths, key=str))
    resource_modes = tuple(
        (resource, fcntl.LOCK_EX) for resource in sorted_resources
    )
    planned_moves = _archive_planned_moves(canonical_pairs)
    planned_optional_moves = _archive_planned_moves(canonical_optional_pairs)
    journal = _ArchiveReportJournal(report_path)
    plan_document = None
    prepared_document = None
    terminal_document = None
    terminal_attempted = False
    required_plans: list[_ArchiveMovePlan] = []
    optional_plans: list[_ArchiveMovePlan | _ArchiveAbsentPlan] = []
    plans: list[_ArchiveMovePlan] = []
    completed: list[dict[str, object]] = []
    optional_resolutions: list[dict[str, object]] = []
    lock_entries = ()

    def write_terminal(value: dict[str, object]) -> dict[str, object]:
        nonlocal terminal_attempted, terminal_document
        terminal_attempted = True
        try:
            terminal_document = journal.write("terminal.json", value)
        except BaseException as error:
            raise ArchiveReportSetupError(
                "archive terminal state could not be written durably; preserve "
                f"the report namespace and every move path: {report_path}: {error}"
            ) from error
        return terminal_document

    def fail(
            *, failed_index: int | None,
            failed_optional_index: int | None,
            phase: str, error: BaseException, archive_state: str,
    ) -> ArchiveBatchError:
        report = _archive_failure_report(
            journal=journal,
            batch_id=batch_id,
            plan_document=plan_document,
            lock_entries=lock_entries,
            plans=plans,
            optional_plans=optional_plans,
            failed_index=failed_index,
            failed_optional_index=failed_optional_index,
            phase=phase,
            error=error,
            archive_state=archive_state,
        )
        return ArchiveBatchError(write_terminal(report))

    try:
        try:
            plan_document = journal.write("plan.json", {
                "version": ARCHIVE_REPORT_VERSION,
                "kind": _ARCHIVE_PLAN_KIND,
                "operation": ARCHIVE_REPORT_OPERATION,
                "batch_id": batch_id,
                "report_id": journal.report_id,
                "report_directory": str(report_path),
                "state": "PLANNED",
                "moves": planned_moves,
                "optional_moves": planned_optional_moves,
                "additional_lock_resources": [
                    str(path) for path in sorted(set(explicit_resources), key=str)
                ],
                "lock_resources": [str(path) for path in sorted_resources],
            })
        except BaseException as error:
            raise ArchiveReportSetupError(
                "archive plan could not be written durably; no archive source "
                f"or destination was mutated: {report_path}: {error}"
            ) from error

        try:
            atomic_rename = _load_atomic_no_replace_rename()
        except BaseException as error:
            raise fail(
                failed_index=None,
                failed_optional_index=None,
                phase="atomic-rename-preflight",
                error=error,
                archive_state="not-committed",
            ) from error

        work_root = all_canonical_pairs[0][0].parent
        try:
            with trusted_fs.locked_resources(
                    work_root, resource_modes,
                    tr.resource_lock_path) as acquired_entries:
                lock_entries = acquired_entries
                failed_index: int | None = None
                failed_optional_index: int | None = None
                phase = "preflight"
                renamed_current = False
                state_override = None
                current_error: BaseException | None = None

                def rename_plan(plan: _ArchiveMovePlan) -> dict[str, object]:
                    nonlocal phase, renamed_current, state_override, current_error
                    current_error = None
                    phase = "pre-syscall-identity"
                    _require_source_name(plan, "at pre-syscall boundary")
                    phase = "rename-no-replace"
                    try:
                        atomic_rename(
                            plan.source_parent_fd,
                            plan.source_path.name,
                            plan.destination_parent_fd,
                            plan.destination_path.name,
                        )
                    except BaseException:
                        state_override = _rename_failure_state(plan)
                        if state_override == "not-committed" and completed:
                            state_override = "partially-committed"
                        raise
                    renamed_current = True
                    try:
                        _sync_and_verify_rename(plan)
                    except _PostRenameError as error:
                        phase = error.phase
                        current_error = error.error
                        raise error.error from error
                    return _archive_move_result(plan)

                try:
                    for index, (source, destination, safety) in enumerate(
                            canonical_pairs):
                        failed_index = index
                        opened = _open_archive_move_plan(
                            source, destination, safety
                        )
                        if not isinstance(opened, _ArchiveMovePlan):
                            raise AssertionError(
                                "required archive source resolved as absent"
                            )
                        required_plans.append(opened)
                        plans.append(opened)

                    failed_index = None
                    phase = "optional-classification"
                    for index, (source, destination, safety) in enumerate(
                            canonical_optional_pairs):
                        failed_optional_index = index
                        opened = _open_archive_move_plan(
                            source, destination, safety,
                            source_may_be_absent=True,
                        )
                        optional_plans.append(opened)
                        if isinstance(opened, _ArchiveMovePlan):
                            plans.append(opened)

                    failed_optional_index = None
                    phase = "verified-safety-copy"
                    optional_move_indexes = {
                        id(plan): index
                        for index, plan in enumerate(optional_plans)
                        if isinstance(plan, _ArchiveMovePlan)
                    }
                    for index, plan in enumerate(plans):
                        if index < len(required_plans):
                            failed_index = index
                            failed_optional_index = None
                        else:
                            failed_index = None
                            failed_optional_index = optional_move_indexes[id(plan)]
                        plan.safety_evidence = _validate_archive_evidence(
                            _create_verified_safety_copy(plan), plan.kind
                        )

                    failed_index = None
                    phase = "prepared-optional-absence"
                    for index, plan in enumerate(optional_plans):
                        failed_optional_index = index
                        if isinstance(plan, _ArchiveAbsentPlan):
                            _require_optional_absent(plan, "before PREPARED")

                    failed_optional_index = None
                    phase = "prepared-report"
                    prepared_document = journal.write("prepared.json", {
                        "version": ARCHIVE_REPORT_VERSION,
                        "kind": _ARCHIVE_PREPARED_KIND,
                        "operation": ARCHIVE_REPORT_OPERATION,
                        "batch_id": batch_id,
                        "report_id": journal.report_id,
                        "state": "PREPARED",
                        "plan_sha256": plan_document["document_sha256"],
                        "moves": [
                            _archive_prepared_move(index, plan)
                            for index, plan in enumerate(required_plans)
                        ],
                        "optional_resolutions": [
                            _archive_prepared_optional(index, plan)
                            for index, plan in enumerate(optional_plans)
                        ],
                    })

                    previous_sha256 = prepared_document["document_sha256"]
                    for index, plan in enumerate(required_plans):
                        failed_index = index
                        failed_optional_index = None
                        result = rename_plan(plan)
                        phase = "checkpoint"
                        checkpoint = journal.write(
                            f"checkpoint-{index + 1:04d}.json",
                            {
                                "version": ARCHIVE_REPORT_VERSION,
                                "kind": _ARCHIVE_CHECKPOINT_KIND,
                                "operation": ARCHIVE_REPORT_OPERATION,
                                "batch_id": batch_id,
                                "report_id": journal.report_id,
                                "state": "MOVE_COMMITTED",
                                "move_index": index,
                                "plan_sha256": plan_document[
                                    "document_sha256"
                                ],
                                "prepared_sha256": prepared_document[
                                    "document_sha256"
                                ],
                                "previous_sha256": previous_sha256,
                                "move": result,
                            },
                        )
                        previous_sha256 = checkpoint["document_sha256"]
                        completed.append(result)
                        renamed_current = False
                        state_override = None

                    failed_index = None
                    for index, plan in enumerate(optional_plans):
                        failed_optional_index = index
                        if isinstance(plan, _ArchiveAbsentPlan):
                            phase = "optional-absence-checkpoint"
                            _require_optional_absent(
                                plan, "before its resolution checkpoint"
                            )
                        else:
                            result = rename_plan(plan)
                        resolution = _archive_optional_result(index, plan)
                        phase = "optional-checkpoint"
                        checkpoint = journal.write(
                            f"optional-checkpoint-{index + 1:04d}.json",
                            {
                                "version": ARCHIVE_REPORT_VERSION,
                                "kind": _ARCHIVE_OPTIONAL_CHECKPOINT_KIND,
                                "operation": ARCHIVE_REPORT_OPERATION,
                                "batch_id": batch_id,
                                "report_id": journal.report_id,
                                "state": "OPTIONAL_RESOLVED",
                                "optional_index": index,
                                "plan_sha256": plan_document[
                                    "document_sha256"
                                ],
                                "prepared_sha256": prepared_document[
                                    "document_sha256"
                                ],
                                "previous_sha256": previous_sha256,
                                "resolution": resolution,
                            },
                        )
                        previous_sha256 = checkpoint["document_sha256"]
                        optional_resolutions.append(resolution)
                        if isinstance(plan, _ArchiveMovePlan):
                            completed.append(result)
                        renamed_current = False
                        state_override = None

                    failed_optional_index = None
                    phase = "terminal-optional-absence"
                    for plan in optional_plans:
                        if isinstance(plan, _ArchiveAbsentPlan):
                            _require_optional_absent(plan, "before terminal")

                    phase = "terminal-report"
                    return write_terminal({
                        "version": ARCHIVE_REPORT_VERSION,
                        "kind": _ARCHIVE_TERMINAL_KIND,
                        "operation": ARCHIVE_REPORT_OPERATION,
                        "batch_id": batch_id,
                        "report_id": journal.report_id,
                        "report_directory": str(report_path),
                        "archive_state": "committed",
                        "blind_retry_forbidden": True,
                        "plan_sha256": plan_document["document_sha256"],
                        "prepared_sha256": prepared_document[
                            "document_sha256"
                        ],
                        "latest_checkpoint_sha256": previous_sha256,
                        "completed_move_count": len(completed),
                        "moves": completed,
                        "optional_resolutions": optional_resolutions,
                        "locks": _archive_lock_entries(lock_entries),
                    })
                except ArchiveReportSetupError:
                    raise
                except BaseException as error:
                    if terminal_attempted:
                        raise
                    effective_error = current_error or error
                    if state_override is not None:
                        archive_state = state_override
                    elif renamed_current:
                        archive_state = "committed-indeterminate"
                    elif completed:
                        archive_state = "partially-committed"
                    else:
                        archive_state = "not-committed"
                    raise fail(
                        failed_index=failed_index,
                        failed_optional_index=failed_optional_index,
                        phase=phase,
                        error=effective_error,
                        archive_state=archive_state,
                    ) from effective_error
                finally:
                    for plan in reversed(plans):
                        _close_archive_move_plan(plan)
                    for plan in reversed(optional_plans):
                        if isinstance(plan, _ArchiveAbsentPlan):
                            _close_archive_absent_plan(plan)
        except (ArchiveBatchError, ArchiveReportSetupError):
            raise
        except BaseException as error:
            if terminal_document is not None or terminal_attempted:
                raise
            raise fail(
                failed_index=None,
                failed_optional_index=None,
                phase="lock-acquisition",
                error=error,
                archive_state="not-committed",
            ) from error
    finally:
        journal.close()


def move_no_replace(
        source: str | Path, destination: str | Path, *,
        report_directory: str | Path | None = None,
) -> dict[str, object]:
    """Safely archive one child; publication recipes must use archive_batch."""
    try:
        result = archive_batch(
            ((source, destination),), report_directory=report_directory
        )
    except ArchiveBatchError as error:
        cause = error.__cause__
        if (error.report.get("archive_state") == "not-committed"
                and isinstance(cause, (OSError, TypeError, ValueError))):
            raise cause
        raise
    return result["moves"][0]


def _read_archive_report_file(
        report_fd: int, name: str,
) -> dict[str, object]:
    flags = (
        os.O_RDONLY
        | trusted_fs._required_flag("O_NOFOLLOW")
        | getattr(os, "O_CLOEXEC", 0)
    )
    fd = os.open(name, flags, dir_fd=report_fd)
    try:
        before = os.fstat(fd)
        entry = os.stat(name, dir_fd=report_fd, follow_symlinks=False)
        signature = _move_source_signature(before)
        if before.st_size > ARCHIVE_REPORT_MAX_BYTES:
            raise ValueError("archive report file exceeds its byte limit")
        if (not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.geteuid()
                or before.st_nlink != 1
                or stat.S_IMODE(before.st_mode) != 0o400
                or _move_source_signature(entry) != signature):
            raise ValueError(f"archive report file is not trusted: {name}")
        trusted_fs.require_trivial_acl_fd(fd, Path(name))
        chunks = []
        length = 0
        while True:
            chunk = os.read(fd, min(READ_SIZE, ARCHIVE_REPORT_MAX_BYTES + 1 - length))
            if not chunk:
                break
            chunks.append(chunk)
            length += len(chunk)
            if length > ARCHIVE_REPORT_MAX_BYTES:
                raise ValueError("archive report file exceeds its byte limit")
        after = os.fstat(fd)
        after_entry = os.stat(name, dir_fd=report_fd, follow_symlinks=False)
        if (length != before.st_size
                or _move_source_signature(after) != signature
                or _move_source_signature(after_entry) != signature):
            raise ValueError(f"archive report file changed while read: {name}")
    finally:
        os.close(fd)
    return _strict_json(b"".join(chunks), f"archive report {name}")


def _append_archive_report_document(
        report_fd: int, name: str, value: dict[str, object],
) -> dict[str, object]:
    """Durably append one canonical no-clobber document to an open report."""
    if not name or "/" in name or name in (".", ".."):
        raise ValueError("archive report append name is invalid")
    document = _hashed_archive_report_document(value)
    raw = _pretty_json(document)
    if len(raw) > ARCHIVE_REPORT_MAX_BYTES:
        raise ValueError("archive report document exceeds its byte limit")
    flags = (
        os.O_WRONLY | os.O_CREAT | os.O_EXCL
        | trusted_fs._required_flag("O_NOFOLLOW")
        | getattr(os, "O_CLOEXEC", 0)
    )
    fd = os.open(name, flags, 0o400, dir_fd=report_fd)
    try:
        os.fchmod(fd, 0o400)
        _write_all(fd, memoryview(raw))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.fsync(report_fd)
    return document


def _validate_archive_evidence(
        value: object, kind: str,
) -> dict[str, int | str]:
    if not isinstance(value, dict):
        raise ValueError("archive evidence must be an object")
    if kind == "file":
        if set(value) != {"kind", "length", "sha256"}:
            raise ValueError("archive file evidence schema mismatch")
        if (value.get("kind") != "file"
                or type(value.get("length")) is not int
                or not 0 <= value["length"] <= ARCHIVE_VERIFY_MAX_BYTES
                or not _valid_sha256(value.get("sha256"))):
            raise ValueError("archive file evidence is invalid")
    elif kind == "directory":
        if set(value) != {
                "kind", "directories", "files", "bytes", "tree_sha256"}:
            raise ValueError("archive tree evidence schema mismatch")
        if (value.get("kind") != "directory"
                or type(value.get("directories")) is not int
                or value["directories"] < 1
                or type(value.get("files")) is not int
                or value["files"] < 0
                or value["directories"] + value["files"]
                > ARCHIVE_VERIFY_MAX_TREE_ENTRIES
                or type(value.get("bytes")) is not int
                or not 0 <= value["bytes"] <= ARCHIVE_VERIFY_MAX_BYTES
                or not _valid_sha256(value.get("tree_sha256"))):
            raise ValueError("archive tree evidence is invalid")
    else:
        raise ValueError("archive evidence kind is invalid")
    return dict(value)


def _validate_planned_archive_moves(
        value: object, label: str,
) -> tuple[list[dict[str, object]], list[tuple[object, object]]]:
    if not isinstance(value, list):
        raise ValueError(f"archive plan {label} moves are malformed")
    validated = []
    pairs = []
    for index, move in enumerate(value):
        if (not isinstance(move, dict)
                or set(move) != {
                    "index", "source", "destination", "safety_copy"
                }
                or move.get("index") != index):
            raise ValueError(
                f"archive plan {label} move sequence is invalid or reordered"
            )
        validated.append(dict(move))
        pairs.append((move.get("source"), move.get("destination")))
    return validated, pairs


def _validate_archive_plan(
        document: dict[str, object], report_path: Path,
) -> tuple[
        list[dict[str, object]],
        list[dict[str, object]],
        tuple[tuple[Path, Path, Path], ...],
        tuple[tuple[Path, Path, Path], ...],
]:
    expected_keys = {
        "version", "kind", "operation", "batch_id", "report_id",
        "report_directory", "state", "moves", "optional_moves",
        "additional_lock_resources", "lock_resources", "document_sha256",
    }
    if set(document) != expected_keys:
        raise ValueError("archive plan schema mismatch")
    if (document.get("state") != "PLANNED"
            or not _valid_sha256(document.get("batch_id"))
            or not _valid_sha256(document.get("report_id"))
            or document.get("report_directory") != str(report_path)):
        raise ValueError("archive plan identity is invalid")
    moves, pairs = _validate_planned_archive_moves(
        document.get("moves"), "required"
    )
    optional_moves, optional_pairs = _validate_planned_archive_moves(
        document.get("optional_moves"), "optional"
    )
    if (not moves and not optional_moves
            or len(moves) + len(optional_moves) > ARCHIVE_REPORT_MAX_MOVES):
        raise ValueError("archive plan move count is invalid")
    batch_id, canonical_pairs, canonical_optional_pairs = (
        _canonical_archive_pairs(pairs, optional_pairs)
    )
    if batch_id != document["batch_id"]:
        raise ValueError("archive plan batch hash mismatch")
    for planned, canonical in (
        (moves, canonical_pairs),
        (optional_moves, canonical_optional_pairs),
    ):
        for move, (source, destination, safety) in zip(planned, canonical):
            if move != {
                    "index": move["index"],
                    "source": str(source),
                    "destination": str(destination),
                    "safety_copy": str(safety),
            }:
                raise ValueError("archive plan move path is noncanonical")
            if (_paths_overlap(report_path, source)
                    or _paths_overlap(report_path, destination)
                    or _paths_overlap(report_path, safety)):
                raise ValueError("archive report overlaps a planned move path")

    additional = document.get("additional_lock_resources")
    lock_resources = document.get("lock_resources")
    if (not isinstance(additional, list)
            or not isinstance(lock_resources, list)):
        raise ValueError("archive plan lock resources are malformed")
    canonical_additional = [
        str(_canonical_absolute_resource_path(value, "lock resource"))
        for value in additional
    ]
    if canonical_additional != sorted(set(canonical_additional)):
        raise ValueError("archive additional lock resources are noncanonical")
    expected_resources = set(Path(value) for value in canonical_additional)
    for paths in canonical_pairs + canonical_optional_pairs:
        expected_resources.update(paths)
    expected_locks = [str(path) for path in sorted(expected_resources, key=str)]
    if lock_resources != expected_locks:
        raise ValueError("archive plan lock resource closure mismatch")
    return moves, optional_moves, canonical_pairs, canonical_optional_pairs


def _validate_archive_prepared_present(
        move: object, planned: dict[str, object], index: int, *,
        optional: bool,
) -> dict[str, object]:
    expected_keys = {
        "index", "source", "destination", "safety_copy", "kind",
        "device", "inode", "source_signature", "evidence",
    }
    if optional:
        expected_keys.add("resolution")
    if (not isinstance(move, dict)
            or set(move) != expected_keys
            or move.get("index") != index
            or any(move.get(key) != planned[key] for key in (
                "index", "source", "destination", "safety_copy"
            ))
            or (optional and move.get("resolution") != "present")
            or move.get("kind") not in ("file", "directory")
            or type(move.get("device")) is not int
            or type(move.get("inode")) is not int):
        raise ValueError("archive PREPARED move is invalid or reordered")
    signature = move.get("source_signature")
    if (not isinstance(signature, dict)
            or set(signature) != set(_ARCHIVE_SIGNATURE_FIELDS)
            or any(type(signature[field]) is not int
                   for field in _ARCHIVE_SIGNATURE_FIELDS)
            or signature["device"] != move["device"]
            or signature["inode"] != move["inode"]):
        raise ValueError("archive PREPARED source signature is invalid")
    evidence = _validate_archive_evidence(
        move.get("evidence"), move["kind"]
    )
    expected_type = (
        stat.S_IFREG if move["kind"] == "file" else stat.S_IFDIR
    )
    if (signature["file_type"] != expected_type
            or signature["uid"] != os.geteuid()
            or signature["device"] != move["device"]
            or signature["inode"] != move["inode"]
            or signature["link_count"] < 1
            or (move["kind"] == "file"
                and (signature["link_count"] != 1
                     or signature["size"] != evidence["length"]))
            or (move["kind"] == "directory"
                and signature["mode"] & 0o022)):
        raise ValueError("archive PREPARED source signature is inconsistent")
    return dict(move)


def _validate_archive_prepared(
        document: dict[str, object], plan_document: dict[str, object],
        planned_moves: list[dict[str, object]],
        planned_optional_moves: list[dict[str, object]],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    expected_keys = {
        "version", "kind", "operation", "batch_id", "report_id", "state",
        "plan_sha256", "moves", "optional_resolutions", "document_sha256",
    }
    if set(document) != expected_keys:
        raise ValueError("archive PREPARED schema mismatch")
    if (document.get("state") != "PREPARED"
            or document.get("batch_id") != plan_document["batch_id"]
            or document.get("report_id") != plan_document["report_id"]
            or document.get("plan_sha256")
            != plan_document["document_sha256"]):
        raise ValueError("archive PREPARED chain is invalid")
    moves = document.get("moves")
    if not isinstance(moves, list) or len(moves) != len(planned_moves):
        raise ValueError("archive PREPARED move count mismatch")
    validated = [
        _validate_archive_prepared_present(
            move, planned, index, optional=False
        )
        for index, (move, planned) in enumerate(zip(moves, planned_moves))
    ]

    optional_resolutions = document.get("optional_resolutions")
    if (not isinstance(optional_resolutions, list)
            or len(optional_resolutions) != len(planned_optional_moves)):
        raise ValueError("archive PREPARED optional resolution count mismatch")
    validated_optional = []
    for index, (resolution, planned) in enumerate(zip(
            optional_resolutions, planned_optional_moves)):
        if not isinstance(resolution, dict):
            raise ValueError("archive PREPARED optional resolution is invalid")
        if resolution.get("resolution") == "absent":
            if resolution != {
                    "index": index,
                    "source": planned["source"],
                    "destination": planned["destination"],
                    "safety_copy": planned["safety_copy"],
                    "resolution": "absent",
            }:
                raise ValueError(
                    "archive PREPARED optional absence is invalid or reordered"
                )
            validated_optional.append(dict(resolution))
        elif resolution.get("resolution") == "present":
            validated_optional.append(_validate_archive_prepared_present(
                resolution, planned, index, optional=True
            ))
        else:
            raise ValueError(
                "archive PREPARED optional resolution must be present or absent"
            )
    return validated, validated_optional


def _validate_archive_checkpoint(
        document: dict[str, object], index: int,
        plan_document: dict[str, object],
        prepared_document: dict[str, object],
        prepared_move: dict[str, object], previous_sha256: str,
) -> dict[str, object]:
    expected_keys = {
        "version", "kind", "operation", "batch_id", "report_id", "state",
        "move_index", "plan_sha256", "prepared_sha256", "previous_sha256",
        "move", "document_sha256",
    }
    if set(document) != expected_keys:
        raise ValueError("archive checkpoint schema mismatch")
    if (document.get("state") != "MOVE_COMMITTED"
            or document.get("move_index") != index
            or document.get("batch_id") != plan_document["batch_id"]
            or document.get("report_id") != plan_document["report_id"]
            or document.get("plan_sha256")
            != plan_document["document_sha256"]
            or document.get("prepared_sha256")
            != prepared_document["document_sha256"]
            or document.get("previous_sha256") != previous_sha256):
        raise ValueError("archive checkpoint chain is invalid or reordered")
    expected_move = {
        "status": "committed",
        "source": prepared_move["source"],
        "destination": prepared_move["destination"],
        "safety_copy": prepared_move["safety_copy"],
        "kind": prepared_move["kind"],
        "device": prepared_move["device"],
        "inode": prepared_move["inode"],
        "evidence": prepared_move["evidence"],
    }
    if document.get("move") != expected_move:
        raise ValueError("archive checkpoint move differs from PREPARED plan")
    return dict(expected_move)


def _expected_archive_optional_result(
        prepared: dict[str, object],
) -> dict[str, object]:
    result = {
        "index": prepared["index"],
        "source": prepared["source"],
        "destination": prepared["destination"],
        "safety_copy": prepared["safety_copy"],
        "resolution": prepared["resolution"],
    }
    if prepared["resolution"] == "present":
        result["move"] = {
            "status": "committed",
            "source": prepared["source"],
            "destination": prepared["destination"],
            "safety_copy": prepared["safety_copy"],
            "kind": prepared["kind"],
            "device": prepared["device"],
            "inode": prepared["inode"],
            "evidence": prepared["evidence"],
        }
    return result


def _validate_archive_optional_checkpoint(
        document: dict[str, object], index: int,
        plan_document: dict[str, object],
        prepared_document: dict[str, object],
        prepared_resolution: dict[str, object], previous_sha256: str,
) -> dict[str, object]:
    expected_keys = {
        "version", "kind", "operation", "batch_id", "report_id", "state",
        "optional_index", "plan_sha256", "prepared_sha256",
        "previous_sha256", "resolution", "document_sha256",
    }
    if set(document) != expected_keys:
        raise ValueError("archive optional checkpoint schema mismatch")
    if (document.get("state") != "OPTIONAL_RESOLVED"
            or document.get("optional_index") != index
            or document.get("batch_id") != plan_document["batch_id"]
            or document.get("report_id") != plan_document["report_id"]
            or document.get("plan_sha256")
            != plan_document["document_sha256"]
            or document.get("prepared_sha256")
            != prepared_document["document_sha256"]
            or document.get("previous_sha256") != previous_sha256):
        raise ValueError(
            "archive optional checkpoint chain is invalid or reordered"
        )
    expected = _expected_archive_optional_result(prepared_resolution)
    if document.get("resolution") != expected:
        raise ValueError(
            "archive optional checkpoint differs from PREPARED resolution"
        )
    return expected


def _require_archive_path_absent(path: Path, label: str) -> None:
    parent_fd = trusted_fs.open_trusted_directory_fd(path.parent)
    try:
        try:
            os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        raise ValueError(f"{label} must remain absent: {path}")
    finally:
        os.close(parent_fd)


def _require_archived_source_absent(source: Path) -> None:
    _require_archive_path_absent(source, "committed archive source")


def _generation_1_repo_root(value: str | Path) -> Path:
    root = _canonical_absolute_move_path(value, "generation-1 repository root")
    fd = trusted_fs.open_trusted_directory_fd(root)
    os.close(fd)
    return root


def _generation_1_contract_paths(
        repo_root: Path,
) -> tuple[list[dict[str, str]], list[str], list[str], list[str]]:
    if (tuple(relative for relative, _sha256 in GENERATION_1_RESTORE_TARGETS)
            != _GENERATION_1_CLOSED_RESTORE_PATHS
            or GENERATION_1_ARCHIVE_SOURCE_PATHS
            != _GENERATION_1_CLOSED_ARCHIVE_SOURCE_PATHS
            or any(not _valid_sha256(sha256)
                   for _relative, sha256 in GENERATION_1_RESTORE_TARGETS)):
        raise ValueError(
            "generation-1 restore targets or archive sources do not match "
            "the closed contract"
        )
    targets = [
        {"path": str(repo_root / relative), "sha256": sha256}
        for relative, sha256 in GENERATION_1_RESTORE_TARGETS
    ]
    archive_sources = [
        str(repo_root / relative)
        for relative in GENERATION_1_ARCHIVE_SOURCE_PATHS
    ]
    optional_sources = [
        str(repo_root / relative) for relative in GENERATION_1_OPTIONAL_PATHS
    ]
    required_absent = [
        str(repo_root / GENERATION_1_LEGACY_PROOF_PATH),
        *optional_sources,
    ]
    return targets, archive_sources, optional_sources, required_absent


def _require_generation_1_archive_contract(
        *, repo_root: Path,
        checkpoint_moves: list[dict[str, object]],
        planned_optional_moves: list[dict[str, object]],
        optional_resolutions: list[dict[str, object]],
) -> tuple[list[dict[str, str]], list[str]]:
    targets, archive_sources, optional_sources, required_absent = (
        _generation_1_contract_paths(repo_root)
    )
    if [move["source"] for move in checkpoint_moves] != archive_sources:
        raise ValueError(
            "archive report required sources do not match the closed "
            "generation-1 contract"
        )
    if [move["source"] for move in planned_optional_moves] != optional_sources:
        raise ValueError(
            "archive report optional sources do not match the closed "
            "generation-1 contract"
        )
    if ([resolution["source"] for resolution in optional_resolutions]
            != optional_sources):
        raise ValueError(
            "archive report optional resolutions do not match the closed "
            "generation-1 contract"
        )
    return targets, required_absent


def _generation_1_stage_path(
        target: Path, report_id: str, index: int,
) -> Path:
    return target.with_name(
        f".trekdex-generation-1-restore-{report_id[:20]}-{index:04d}"
    )


def _validate_generation_1_restore_records(
        *, report_fd: int, report_path: Path, names: list[str],
        plan_document: dict[str, object],
        terminal_document: dict[str, object],
        checkpoint_moves: list[dict[str, object]],
        planned_optional_moves: list[dict[str, object]],
        optional_resolutions: list[dict[str, object]],
) -> dict[str, object] | None:
    restore_names = {
        _GENERATION_1_RESTORE_PREPARED_NAME,
        _GENERATION_1_RESTORE_TERMINAL_NAME,
    }
    present_names = restore_names.intersection(names)
    if not present_names:
        return None
    if present_names != restore_names:
        raise ValueError(
            "generation-1 restore report is missing PREPARED or terminal state"
        )

    prepared = _verify_archive_report_document_hash(
        _read_archive_report_file(
            report_fd, _GENERATION_1_RESTORE_PREPARED_NAME
        ),
        _GENERATION_1_RESTORE_PREPARED_KIND,
        expected_operation=GENERATION_1_RESTORE_OPERATION,
    )
    terminal = _verify_archive_report_document_hash(
        _read_archive_report_file(
            report_fd, _GENERATION_1_RESTORE_TERMINAL_NAME
        ),
        _GENERATION_1_RESTORE_TERMINAL_KIND,
        expected_operation=GENERATION_1_RESTORE_OPERATION,
    )
    if terminal.get("state") != "RESTORE_COMMITTED":
        raise ValueError("generation-1 restore terminal state is not committed")

    prepared_keys = {
        "version", "kind", "operation", "contract", "state", "batch_id",
        "report_id", "report_directory", "archive_terminal_sha256",
        "source_commit", "repo_root", "targets", "required_absent_paths",
        "document_sha256",
    }
    terminal_keys = {
        "version", "kind", "operation", "contract", "state", "batch_id",
        "report_id", "report_directory", "archive_terminal_sha256",
        "prepared_sha256", "source_commit", "repo_root", "restored_targets",
        "required_absent_paths", "document_sha256",
    }
    if set(prepared) != prepared_keys or set(terminal) != terminal_keys:
        raise ValueError("generation-1 restore report schema mismatch")
    common_valid = (
        prepared.get("version") == ARCHIVE_REPORT_VERSION
        and terminal.get("version") == ARCHIVE_REPORT_VERSION
        and prepared.get("operation") == GENERATION_1_RESTORE_OPERATION
        and terminal.get("operation") == GENERATION_1_RESTORE_OPERATION
        and prepared.get("contract") == GENERATION_1_RESTORE_CONTRACT
        and terminal.get("contract") == GENERATION_1_RESTORE_CONTRACT
        and prepared.get("state") == "RESTORE_PREPARED"
        and prepared.get("batch_id") == plan_document["batch_id"]
        and terminal.get("batch_id") == plan_document["batch_id"]
        and prepared.get("report_id") == plan_document["report_id"]
        and terminal.get("report_id") == plan_document["report_id"]
        and prepared.get("report_directory") == str(report_path)
        and terminal.get("report_directory") == str(report_path)
        and prepared.get("archive_terminal_sha256")
        == terminal_document["document_sha256"]
        and terminal.get("archive_terminal_sha256")
        == terminal_document["document_sha256"]
        and terminal.get("prepared_sha256") == prepared["document_sha256"]
        and prepared.get("source_commit") == GENERATION_1_RESTORE_COMMIT
        and terminal.get("source_commit") == GENERATION_1_RESTORE_COMMIT
        and terminal.get("repo_root") == prepared.get("repo_root")
    )
    if not common_valid:
        raise ValueError("generation-1 restore report chain is invalid")

    repo_root = _generation_1_repo_root(prepared.get("repo_root"))
    expected_targets, required_absent = _require_generation_1_archive_contract(
        repo_root=repo_root,
        checkpoint_moves=checkpoint_moves,
        planned_optional_moves=planned_optional_moves,
        optional_resolutions=optional_resolutions,
    )
    raw_targets = prepared.get("targets")
    if not isinstance(raw_targets, list) or len(raw_targets) != len(
            expected_targets):
        raise ValueError("generation-1 restore target count is invalid")
    targets = []
    restored_targets = []
    for index, (raw, expected) in enumerate(zip(raw_targets, expected_targets)):
        target = Path(expected["path"])
        expected_stage = _generation_1_stage_path(
            target, plan_document["report_id"], index
        )
        if (not isinstance(raw, dict)
                or set(raw) != {"path", "stage", "length", "sha256"}
                or raw.get("path") != expected["path"]
                or raw.get("stage") != str(expected_stage)
                or raw.get("sha256") != expected["sha256"]
                or type(raw.get("length")) is not int
                or not 0 <= raw["length"] <= ARCHIVE_VERIFY_MAX_BYTES):
            raise ValueError("generation-1 restore target contract is invalid")
        targets.append(dict(raw))
        restored_targets.append({
            "path": raw["path"],
            "length": raw["length"],
            "sha256": raw["sha256"],
        })
    if (terminal.get("restored_targets") != restored_targets
            or prepared.get("required_absent_paths") != required_absent
            or terminal.get("required_absent_paths") != required_absent):
        raise ValueError("generation-1 restore terminal evidence is invalid")
    for target in targets:
        _require_archive_path_absent(
            Path(target["stage"]), "generation-1 restore staging path"
        )
    return {
        "prepared": prepared,
        "terminal": terminal,
        "repo_root": repo_root,
        "targets": targets,
        "required_absent_paths": required_absent,
    }


def _verify_archive_report_locked(
        report_directory: str | Path, *,
        expected_plan_sha256: str,
        acquired_entries: tuple[tuple[Path, Path, int], ...],
) -> tuple[dict[str, int | str], dict[str, object]]:
    """Validate one report and its retained images under its full lease."""
    report_path = _canonical_absolute_move_path(
        report_directory, "archive report directory"
    )
    report_fd = trusted_fs.open_trusted_directory_fd(report_path)
    try:
        before = os.fstat(report_fd)
        if (not stat.S_ISDIR(before.st_mode)
                or before.st_uid != os.geteuid()
                or stat.S_IMODE(before.st_mode) != 0o700):
            raise ValueError("archive report directory is not owner-only")
        trusted_fs.require_trivial_acl_fd(
            report_fd, report_path, is_directory=True
        )
        signature = _move_source_signature(before)
        names = sorted(os.listdir(report_fd))
        if len(names) > ARCHIVE_REPORT_MAX_MOVES + 5:
            raise ValueError("archive report file count exceeds its bound")
        allowed_name = re.compile(
            r"(?:plan|prepared|terminal)\.json|"
            r"(?:optional-)?checkpoint-[0-9]{4}\.json|"
            r"generation-1-restore-(?:prepared|terminal)\.json"
        )
        if ("plan.json" not in names or "terminal.json" not in names
                or any(allowed_name.fullmatch(name) is None for name in names)):
            raise ValueError("archive report has missing or extra files")

        plan_document = _verify_archive_report_document_hash(
            _read_archive_report_file(report_fd, "plan.json"),
            _ARCHIVE_PLAN_KIND,
        )
        (
            planned_moves,
            planned_optional_moves,
            canonical_pairs,
            canonical_optional_pairs,
        ) = _validate_archive_plan(plan_document, report_path)
        if plan_document["document_sha256"] != expected_plan_sha256:
            raise ValueError(
                "archive plan changed between lock selection and leased verification"
            )
        expected_entries = trusted_fs.resource_lock_entries(
            report_path,
            tuple(
                (Path(resource), fcntl.LOCK_EX)
                for resource in plan_document["lock_resources"]
            ),
            tr.resource_lock_path,
        )
        if (acquired_entries != expected_entries
                or any(mode != fcntl.LOCK_EX
                       for _lock, _resource, mode in acquired_entries)):
            raise ValueError(
                "archive verifier does not hold the exact exclusive plan lock set"
            )
        terminal_document = _verify_archive_report_document_hash(
            _read_archive_report_file(report_fd, "terminal.json"),
            _ARCHIVE_TERMINAL_KIND,
        )
        if terminal_document.get("archive_state") != "committed":
            raise ValueError("archive report terminal state is not committed")

        restore_names = {
            _GENERATION_1_RESTORE_PREPARED_NAME,
            _GENERATION_1_RESTORE_TERMINAL_NAME,
        }
        present_restore_names = restore_names.intersection(names)
        if present_restore_names and present_restore_names != restore_names:
            raise ValueError(
                "generation-1 restore report is missing PREPARED or terminal state"
            )
        expected_names = sorted(
            ["plan.json", "prepared.json", "terminal.json"]
            + [
                f"checkpoint-{index + 1:04d}.json"
                for index in range(len(planned_moves))
            ]
            + [
                f"optional-checkpoint-{index + 1:04d}.json"
                for index in range(len(planned_optional_moves))
            ]
            + sorted(present_restore_names)
        )
        if names != expected_names:
            raise ValueError(
                "committed archive report has missing, extra, or reordered "
                "moves or optional resolutions"
            )
        prepared_document = _verify_archive_report_document_hash(
            _read_archive_report_file(report_fd, "prepared.json"),
            _ARCHIVE_PREPARED_KIND,
        )
        prepared_moves, prepared_optional = _validate_archive_prepared(
            prepared_document,
            plan_document,
            planned_moves,
            planned_optional_moves,
        )
        checkpoint_moves = []
        previous_sha256 = prepared_document["document_sha256"]
        for index, prepared_move in enumerate(prepared_moves):
            checkpoint = _verify_archive_report_document_hash(
                _read_archive_report_file(
                    report_fd, f"checkpoint-{index + 1:04d}.json"
                ),
                _ARCHIVE_CHECKPOINT_KIND,
            )
            checkpoint_moves.append(_validate_archive_checkpoint(
                checkpoint, index, plan_document, prepared_document,
                prepared_move, previous_sha256,
            ))
            previous_sha256 = checkpoint["document_sha256"]

        optional_resolutions = []
        for index, prepared_resolution in enumerate(prepared_optional):
            checkpoint = _verify_archive_report_document_hash(
                _read_archive_report_file(
                    report_fd,
                    f"optional-checkpoint-{index + 1:04d}.json",
                ),
                _ARCHIVE_OPTIONAL_CHECKPOINT_KIND,
            )
            optional_resolutions.append(
                _validate_archive_optional_checkpoint(
                    checkpoint, index, plan_document, prepared_document,
                    prepared_resolution, previous_sha256,
                )
            )
            previous_sha256 = checkpoint["document_sha256"]

        all_checkpoint_moves = checkpoint_moves + [
            resolution["move"]
            for resolution in optional_resolutions
            if resolution["resolution"] == "present"
        ]
        terminal_keys = {
            "version", "kind", "operation", "batch_id", "report_id",
            "report_directory", "archive_state", "blind_retry_forbidden",
            "plan_sha256", "prepared_sha256", "latest_checkpoint_sha256",
            "completed_move_count", "moves", "optional_resolutions", "locks",
            "document_sha256",
        }
        if (set(terminal_document) != terminal_keys
                or terminal_document.get("batch_id") != plan_document["batch_id"]
                or terminal_document.get("report_id") != plan_document["report_id"]
                or terminal_document.get("report_directory") != str(report_path)
                or terminal_document.get("blind_retry_forbidden") is not True
                or terminal_document.get("plan_sha256")
                != plan_document["document_sha256"]
                or terminal_document.get("prepared_sha256")
                != prepared_document["document_sha256"]
                or terminal_document.get("latest_checkpoint_sha256")
                != previous_sha256
                or terminal_document.get("completed_move_count")
                != len(all_checkpoint_moves)
                or terminal_document.get("moves") != all_checkpoint_moves
                or terminal_document.get("optional_resolutions")
                != optional_resolutions):
            raise ValueError("archive terminal chain is invalid")
        locks = terminal_document.get("locks")
        if (not isinstance(locks, list)
                or any(not isinstance(item, dict)
                       or set(item) != {"lock", "resource"}
                       or not isinstance(item.get("lock"), str)
                       or not isinstance(item.get("resource"), str)
                       for item in locks)
                or sorted(item["resource"] for item in locks)
                != plan_document["lock_resources"]
                or len({item["resource"] for item in locks}) != len(locks)):
            raise ValueError("archive terminal lock set is invalid")
        expected_lock_records = {
            (
                str(tr.resource_lock_path(report_path, resource)),
                resource,
            )
            for resource in plan_document["lock_resources"]
        }
        if {(item["lock"], item["resource"]) for item in locks} != (
                expected_lock_records):
            raise ValueError("archive terminal lock mapping is invalid")
        if locks != _archive_lock_entries(acquired_entries):
            raise ValueError(
                "archive terminal locks are not the exact acquired global order"
            )

        restore_context = _validate_generation_1_restore_records(
            report_fd=report_fd,
            report_path=report_path,
            names=names,
            plan_document=plan_document,
            terminal_document=terminal_document,
            checkpoint_moves=checkpoint_moves,
            planned_optional_moves=planned_optional_moves,
            optional_resolutions=optional_resolutions,
        )
        restored_by_path = {
            target["path"]: target
            for target in (
                restore_context["targets"] if restore_context is not None else []
            )
        }
        verified_bytes = 0

        def verify_move(
                move: dict[str, object], destination: Path, safety: Path,
        ) -> None:
            nonlocal verified_bytes
            evidence = _validate_archive_evidence(
                move["evidence"], move["kind"]
            )
            source = Path(move["source"])
            restored = restored_by_path.get(str(source))
            if restored is None:
                _require_archived_source_absent(source)
            else:
                restored_evidence = {
                    "kind": "file",
                    "length": restored["length"],
                    "sha256": restored["sha256"],
                }
                if _measure_archive_path(
                        source,
                        "file",
                        require_safety_modes=False,
                        expected_evidence=restored_evidence,
                ) != restored_evidence:
                    raise ValueError(
                        "generation-1 restored source evidence mismatch"
                    )
            destination_evidence = _measure_archive_path(
                destination,
                move["kind"],
                require_safety_modes=False,
                expected_evidence=evidence,
            )
            safety_evidence = _measure_archive_path(
                safety,
                move["kind"],
                require_safety_modes=True,
                expected_evidence=evidence,
            )
            if destination_evidence != evidence or safety_evidence != evidence:
                raise ValueError(
                    "archive destination or safety image evidence mismatch"
                )
            verified_bytes += (
                evidence["length"]
                if move["kind"] == "file" else evidence["bytes"]
            )

        for move, (_source, destination, safety) in zip(
                checkpoint_moves, canonical_pairs):
            verify_move(move, destination, safety)
        for resolution, (source, destination, safety) in zip(
                optional_resolutions, canonical_optional_pairs):
            if resolution["resolution"] == "present":
                verify_move(resolution["move"], destination, safety)
            else:
                _require_archive_path_absent(
                    source, "optional archive source recorded absent"
                )
                _require_archive_path_absent(
                    destination, "optional archive destination recorded absent"
                )
                _require_archive_path_absent(
                    safety, "optional archive safety copy recorded absent"
                )

        if restore_context is not None:
            for path in restore_context["required_absent_paths"]:
                _require_archive_path_absent(
                    Path(path), "generation-1 required absent path"
                )

        after = os.fstat(report_fd)
        if (sorted(os.listdir(report_fd)) != names
                or _move_source_signature(after) != signature):
            raise ValueError("archive report directory changed during verification")
    finally:
        os.close(report_fd)

    optional_present = sum(
        resolution["resolution"] == "present"
        for resolution in optional_resolutions
    )
    result = {
        "version": ARCHIVE_REPORT_VERSION,
        "operation": ARCHIVE_REPORT_OPERATION,
        "batch_id": plan_document["batch_id"],
        "archive_state": "committed",
        "report_directory": str(report_path),
        "moves_verified": len(planned_moves) + optional_present,
        "optional_resolutions_verified": len(optional_resolutions),
        "optional_sources_present": optional_present,
        "optional_sources_absent": len(optional_resolutions) - optional_present,
        "verified_bytes": verified_bytes,
        "generation_1_restore_state": (
            "committed" if restore_context is not None else "not-recorded"
        ),
        "restored_targets_verified": (
            len(restore_context["targets"])
            if restore_context is not None else 0
        ),
    }
    context = {
        "plan_document": plan_document,
        "prepared_document": prepared_document,
        "terminal_document": terminal_document,
        "planned_moves": planned_moves,
        "planned_optional_moves": planned_optional_moves,
        "canonical_pairs": canonical_pairs,
        "canonical_optional_pairs": canonical_optional_pairs,
        "checkpoint_moves": checkpoint_moves,
        "optional_resolutions": optional_resolutions,
        "restore_context": restore_context,
        "report_names": names,
    }
    return result, context


def _archive_report_lock_contract(
        report_directory: str | Path,
) -> tuple[
        Path, dict[str, object], tuple[tuple[Path, Path, int], ...],
]:
    """Select and validate the immutable plan used to acquire its full lease."""
    report_path = _canonical_absolute_move_path(
        report_directory, "archive report directory"
    )
    report_fd = trusted_fs.open_trusted_directory_fd(report_path)
    try:
        before = os.fstat(report_fd)
        if (not stat.S_ISDIR(before.st_mode)
                or before.st_uid != os.geteuid()
                or stat.S_IMODE(before.st_mode) != 0o700):
            raise ValueError("archive report directory is not owner-only")
        trusted_fs.require_trivial_acl_fd(
            report_fd, report_path, is_directory=True
        )
        signature = _move_source_signature(before)
        names = sorted(os.listdir(report_fd))
        if "plan.json" not in names:
            raise ValueError("archive report has no plan for lock selection")
        plan_document = _verify_archive_report_document_hash(
            _read_archive_report_file(report_fd, "plan.json"),
            _ARCHIVE_PLAN_KIND,
        )
        _validate_archive_plan(plan_document, report_path)
        after = os.fstat(report_fd)
        if (sorted(os.listdir(report_fd)) != names
                or _move_source_signature(after) != signature):
            raise ValueError(
                "archive report directory changed during lock selection"
            )
    finally:
        os.close(report_fd)

    resource_modes = tuple(
        (Path(resource), fcntl.LOCK_EX)
        for resource in plan_document["lock_resources"]
    )
    expected_entries = trusted_fs.resource_lock_entries(
        report_path, resource_modes, tr.resource_lock_path
    )
    if (len(expected_entries) != len(resource_modes)
            or any(mode != fcntl.LOCK_EX
                   for _lock, _resource, mode in expected_entries)):
        raise ValueError("archive plan does not select one exact exclusive lock set")
    return report_path, plan_document, expected_entries


def _enter_archive_report_lease(
        report_path: Path, plan_document: dict[str, object],
):
    resource_modes = tuple(
        (Path(resource), fcntl.LOCK_EX)
        for resource in plan_document["lock_resources"]
    )
    lease = trusted_fs.locked_resources(
        report_path, resource_modes, tr.resource_lock_path
    )
    try:
        acquired_entries = lease.__enter__()
    except BaseException as error:
        raise ValueError(
            "archive report lock acquisition failed; verification refused "
            f"without archive mutation: {error}"
        ) from error
    return lease, acquired_entries


def verify_archive_report(
        report_directory: str | Path,
) -> dict[str, int | str]:
    """Validate a committed report and every image under its exact lease."""
    report_path, plan_document, expected_entries = (
        _archive_report_lock_contract(report_directory)
    )
    lease, acquired_entries = _enter_archive_report_lease(
        report_path, plan_document
    )
    try:
        if acquired_entries != expected_entries:
            raise ValueError("archive verifier acquired a different lock set")
        result, _context = _verify_archive_report_locked(
            report_path,
            expected_plan_sha256=plan_document["document_sha256"],
            acquired_entries=acquired_entries,
        )
        return result
    finally:
        lease.__exit__(None, None, None)


def _git_executable() -> str:
    executable = shutil.which("git", path="/usr/local/bin:/usr/bin:/bin")
    if executable is None:
        raise ValueError("git executable is unavailable for generation-1 restore")
    return executable


def _run_restore_git(
        repo_root: Path, arguments: list[str], *, text: bool,
) -> subprocess.CompletedProcess:
    result = subprocess.run(
        [_git_executable(), "-C", str(repo_root), *arguments],
        check=False,
        capture_output=True,
        text=text,
        timeout=30,
        env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1"},
    )
    if result.returncode != 0:
        stderr = result.stderr if text else result.stderr.decode(
            "utf-8", errors="replace"
        )
        raise ValueError(
            "generation-1 restore could not read the pinned Git object: "
            f"{stderr.strip()}"
        )
    return result


def _generation_1_git_blob_metadata(
        repo_root: Path, source_commit: str,
) -> list[dict[str, object]]:
    if (source_commit != GENERATION_1_RESTORE_COMMIT
            or re.fullmatch(r"[0-9a-f]{40}", source_commit) is None):
        raise ValueError(
            "generation-1 restore source commit does not match its closed "
            "explicit contract"
        )
    top_level = _run_restore_git(
        repo_root, ["rev-parse", "--show-toplevel"], text=True
    ).stdout.strip()
    if top_level != str(repo_root):
        raise ValueError("generation-1 repository root is not the Git top level")
    resolved_commit = _run_restore_git(
        repo_root,
        ["rev-parse", "--verify", f"{source_commit}^{{commit}}"],
        text=True,
    ).stdout.strip()
    if resolved_commit != source_commit:
        raise ValueError("generation-1 source is not the exact pinned commit")

    blobs = []
    for relative, expected_sha256 in GENERATION_1_RESTORE_TARGETS:
        object_spec = f"{source_commit}:{relative}"
        object_type = _run_restore_git(
            repo_root, ["cat-file", "-t", object_spec], text=True
        ).stdout.strip()
        if object_type != "blob":
            raise ValueError(
                f"generation-1 source is not a blob: {relative}"
            )
        raw_size = _run_restore_git(
            repo_root, ["cat-file", "-s", object_spec], text=True
        ).stdout.strip()
        try:
            size = int(raw_size)
        except ValueError as error:
            raise ValueError("generation-1 Git blob size is invalid") from error
        if not 0 <= size <= ARCHIVE_VERIFY_MAX_BYTES:
            raise ValueError(
                f"generation-1 Git blob exceeds restore bound: {relative}"
            )
        blobs.append({
            "relative": relative,
            "path": repo_root / relative,
            "length": size,
            "sha256": expected_sha256,
        })
    return blobs


def _open_archive_report_for_restore(
        report_path: Path, expected_names: list[str],
) -> int:
    report_fd = trusted_fs.open_trusted_directory_fd(report_path)
    try:
        value = os.fstat(report_fd)
        if (not stat.S_ISDIR(value.st_mode)
                or value.st_uid != os.geteuid()
                or stat.S_IMODE(value.st_mode) != 0o700):
            raise ValueError("archive report directory is not owner-only")
        trusted_fs.require_trivial_acl_fd(
            report_fd, report_path, is_directory=True
        )
        if sorted(os.listdir(report_fd)) != expected_names:
            raise ValueError(
                "archive report changed before generation-1 restore PREPARED"
            )
        return report_fd
    except BaseException:
        os.close(report_fd)
        raise


def _read_generation_1_builder_pin(fd: int, length: object) -> str:
    if type(length) is not int or not 0 <= length <= 1024 * 1024:
        raise ValueError("generation-1 builder exceeds its restore bound")
    raw = bytearray()
    while len(raw) <= length:
        chunk = os.pread(
            fd, min(READ_SIZE, length + 1 - len(raw)), len(raw)
        )
        if not chunk:
            break
        raw.extend(chunk)
    if len(raw) != length:
        raise ValueError("generation-1 builder length changed during restore")
    matches = re.findall(
        rb'(?m)^PUBLICATION_TRUST_ROOT_SHA256 = \(\n'
        rb'    "([0-9a-f]{64})"\n'
        rb'\)$',
        raw,
    )
    if len(matches) != 1:
        raise ValueError(
            "generation-1 builder literal publication root pin is invalid"
        )
    return matches[0].decode("ascii")


def _write_generation_1_stage(
        repo_root: Path, source_commit: str, blob: dict[str, object],
        parent_fd: int, stage: Path,
) -> str | None:
    _entry_absent(parent_fd, stage.name, stage)
    flags = (
        os.O_RDWR | os.O_CREAT | os.O_EXCL
        | trusted_fs._required_flag("O_NOFOLLOW")
        | getattr(os, "O_CLOEXEC", 0)
    )
    fd = os.open(stage.name, flags, 0o600, dir_fd=parent_fd)
    builder_pin = None
    try:
        os.fchmod(fd, 0o600)
        object_spec = f"{source_commit}:{blob['relative']}"
        process = subprocess.run(
            [
                _git_executable(), "-C", str(repo_root),
                "cat-file", "blob", object_spec,
            ],
            check=False,
            stdout=fd,
            stderr=subprocess.PIPE,
            timeout=120,
            env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1"},
        )
        if process.returncode != 0:
            raise ValueError(
                "generation-1 restore could not stream the pinned Git blob: "
                f"{process.stderr.decode('utf-8', errors='replace').strip()}"
            )
        os.fsync(fd)
        signature = _move_source_signature(os.fstat(fd))
        trusted_fs.require_trivial_acl_fd(fd, stage)
        evidence = _regular_fd_evidence(fd, signature, stage)
        if (evidence["length"] != blob["length"]
                or evidence["sha256"] != blob["sha256"]):
            raise ValueError(
                "generation-1 Git blob differs from the reviewed contract: "
                f"{blob['relative']}"
            )
        if blob["relative"] == GENERATION_1_BUILDER_PATH:
            builder_pin = _read_generation_1_builder_pin(fd, blob["length"])
    finally:
        os.close(fd)
    os.fsync(parent_fd)
    return builder_pin


def restore_generation_1(
        report_directory: str | Path, *, repo_root: str | Path,
        source_commit: str,
) -> dict[str, int | str]:
    """Restore the closed Colorado generation-1 contract under archive lease."""
    root = _generation_1_repo_root(repo_root)
    if source_commit != GENERATION_1_RESTORE_COMMIT:
        raise ValueError(
            "generation-1 restore accepts only its explicitly pinned commit"
        )
    report_path, plan_document, expected_entries = (
        _archive_report_lock_contract(report_directory)
    )
    lease, acquired_entries = _enter_archive_report_lease(
        report_path, plan_document
    )
    report_fd = None
    parent_fds: dict[Path, int] = {}
    prepared_document = None
    terminal_written = False
    restored_targets: list[dict[str, object]] = []
    renamed_current = False
    current_target: dict[str, object] | None = None
    try:
        if acquired_entries != expected_entries:
            raise ValueError("generation-1 restore acquired a different lock set")
        _verified, context = _verify_archive_report_locked(
            report_path,
            expected_plan_sha256=plan_document["document_sha256"],
            acquired_entries=acquired_entries,
        )
        if context["restore_context"] is not None:
            raise ValueError("generation-1 restore state already exists")
        expected_targets, required_absent = _require_generation_1_archive_contract(
            repo_root=root,
            checkpoint_moves=context["checkpoint_moves"],
            planned_optional_moves=context["planned_optional_moves"],
            optional_resolutions=context["optional_resolutions"],
        )
        blobs = _generation_1_git_blob_metadata(root, source_commit)
        target_documents = []
        for index, (blob, expected) in enumerate(zip(blobs, expected_targets)):
            target = blob["path"]
            if (str(target) != expected["path"]
                    or blob["sha256"] != expected["sha256"]):
                raise ValueError("generation-1 in-memory target contract changed")
            parent_fd = parent_fds.get(target.parent)
            if parent_fd is None:
                parent_fd = trusted_fs.open_trusted_directory_fd(target.parent)
                parent_fds[target.parent] = parent_fd
            _entry_absent(parent_fd, target.name, target)
            stage = _generation_1_stage_path(
                target, plan_document["report_id"], index
            )
            _entry_absent(parent_fd, stage.name, stage)
            target_documents.append({
                "path": str(target),
                "stage": str(stage),
                "length": blob["length"],
                "sha256": blob["sha256"],
            })
        for path in required_absent:
            _require_archive_path_absent(
                Path(path), "generation-1 required absent path"
            )

        report_fd = _open_archive_report_for_restore(
            report_path, context["report_names"]
        )
        prepared_document = _append_archive_report_document(
            report_fd, _GENERATION_1_RESTORE_PREPARED_NAME, {
                "version": ARCHIVE_REPORT_VERSION,
                "kind": _GENERATION_1_RESTORE_PREPARED_KIND,
                "operation": GENERATION_1_RESTORE_OPERATION,
                "contract": GENERATION_1_RESTORE_CONTRACT,
                "state": "RESTORE_PREPARED",
                "batch_id": plan_document["batch_id"],
                "report_id": plan_document["report_id"],
                "report_directory": str(report_path),
                "archive_terminal_sha256": context["terminal_document"][
                    "document_sha256"
                ],
                "source_commit": source_commit,
                "repo_root": str(root),
                "targets": target_documents,
                "required_absent_paths": required_absent,
            },
        )

        try:
            atomic_rename = _load_atomic_no_replace_rename()
            reviewed_root_sha256 = next(
                blob["sha256"] for blob in blobs
                if blob["relative"] == GENERATION_1_ROOT_PATH
            )
            builder_pin = None
            for blob, target_document in zip(blobs, target_documents):
                target = Path(target_document["path"])
                stage = Path(target_document["stage"])
                parent_fd = parent_fds[target.parent]
                staged_pin = _write_generation_1_stage(
                    root, source_commit, blob, parent_fd, stage
                )
                if blob["relative"] == GENERATION_1_BUILDER_PATH:
                    builder_pin = staged_pin
            if builder_pin != reviewed_root_sha256:
                raise ValueError(
                    "generation-1 builder literal pin does not equal the "
                    "restored publication root hash"
                )
            for target_document in target_documents:
                current_target = target_document
                target = Path(target_document["path"])
                stage = Path(target_document["stage"])
                parent_fd = parent_fds[target.parent]
                atomic_rename(
                    parent_fd, stage.name, parent_fd, target.name
                )
                renamed_current = True
                target_fd = os.open(
                    target.name,
                    os.O_RDONLY | trusted_fs._required_flag("O_NOFOLLOW")
                    | getattr(os, "O_CLOEXEC", 0),
                    dir_fd=parent_fd,
                )
                try:
                    os.fsync(target_fd)
                finally:
                    os.close(target_fd)
                os.fsync(parent_fd)
                evidence = {
                    "kind": "file",
                    "length": target_document["length"],
                    "sha256": target_document["sha256"],
                }
                if _measure_archive_entry(
                        parent_fd, target.name, target, "file",
                        require_safety_modes=False,
                        expected_evidence=evidence,
                ) != evidence:
                    raise ValueError(
                        "generation-1 restored target evidence mismatch"
                    )
                restored_targets.append({
                    "path": str(target),
                    "length": target_document["length"],
                    "sha256": target_document["sha256"],
                })
                renamed_current = False
                current_target = None
            for path in required_absent:
                _require_archive_path_absent(
                    Path(path), "generation-1 required absent path"
                )
            terminal = _append_archive_report_document(
                report_fd, _GENERATION_1_RESTORE_TERMINAL_NAME, {
                    "version": ARCHIVE_REPORT_VERSION,
                    "kind": _GENERATION_1_RESTORE_TERMINAL_KIND,
                    "operation": GENERATION_1_RESTORE_OPERATION,
                    "contract": GENERATION_1_RESTORE_CONTRACT,
                    "state": "RESTORE_COMMITTED",
                    "batch_id": plan_document["batch_id"],
                    "report_id": plan_document["report_id"],
                    "report_directory": str(report_path),
                    "archive_terminal_sha256": context["terminal_document"][
                        "document_sha256"
                    ],
                    "prepared_sha256": prepared_document["document_sha256"],
                    "source_commit": source_commit,
                    "repo_root": str(root),
                    "restored_targets": restored_targets,
                    "required_absent_paths": required_absent,
                },
            )
            terminal_written = True
            result, _final_context = _verify_archive_report_locked(
                report_path,
                expected_plan_sha256=plan_document["document_sha256"],
                acquired_entries=acquired_entries,
            )
            if terminal["document_sha256"] != _final_context[
                    "restore_context"]["terminal"]["document_sha256"]:
                raise ValueError("generation-1 restore terminal changed")
            return result
        except BaseException as error:
            if terminal_written:
                raise
            failure_state = (
                "RESTORE_COMMITTED_INDETERMINATE"
                if renamed_current else (
                    "RESTORE_PARTIALLY_COMMITTED"
                    if restored_targets else "RESTORE_NOT_COMMITTED"
                )
            )
            failed_target = None
            if current_target is not None:
                failed_target = {
                    "path": current_target["path"],
                    "length": current_target["length"],
                    "sha256": current_target["sha256"],
                    "rename_may_have_committed": renamed_current,
                }
            failure_report = {
                "version": ARCHIVE_REPORT_VERSION,
                "kind": _GENERATION_1_RESTORE_TERMINAL_KIND,
                "operation": GENERATION_1_RESTORE_OPERATION,
                "contract": GENERATION_1_RESTORE_CONTRACT,
                "state": failure_state,
                "batch_id": plan_document["batch_id"],
                "report_id": plan_document["report_id"],
                "report_directory": str(report_path),
                "archive_terminal_sha256": context["terminal_document"][
                    "document_sha256"
                ],
                "prepared_sha256": prepared_document["document_sha256"],
                "source_commit": source_commit,
                "repo_root": str(root),
                "restored_targets": restored_targets,
                "failed_target": failed_target,
                "unrestored_targets": [
                    {
                        "path": target["path"],
                        "length": target["length"],
                        "sha256": target["sha256"],
                    }
                    for target in target_documents[len(restored_targets):]
                ],
                "required_absent_paths": required_absent,
                "blind_retry_forbidden": True,
                "failure": {
                    "type": type(error).__name__,
                    "message": str(error),
                },
            }
            try:
                durable_failure = _append_archive_report_document(
                    report_fd,
                    _GENERATION_1_RESTORE_TERMINAL_NAME,
                    failure_report,
                )
            except BaseException as report_error:
                raise ArchiveReportSetupError(
                    "generation-1 restore terminal could not be written "
                    f"durably; preserve every report and target path: "
                    f"{report_error}"
                ) from report_error
            raise Generation1RestoreError(durable_failure) from error
    finally:
        for parent_fd in reversed(tuple(parent_fds.values())):
            os.close(parent_fd)
        if report_fd is not None:
            os.close(report_fd)
        lease.__exit__(None, None, None)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Manage immutable Trekdex publication-proof objects."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    seal = commands.add_parser(
        "seal", help="verify and remove write bits from a registry v2 closure"
    )
    seal.add_argument("registry", help="compact proof registry v2 path")
    verify_stage = commands.add_parser(
        "verify-stage",
        help="enumerate and verify an archived publication-journal stage",
    )
    verify_stage.add_argument("journal", help="archived publication journal path")
    verify_stage.add_argument(
        "stage_directory", help="source or relocated object-stage directory"
    )
    verify_report = commands.add_parser(
        "verify-archive-report",
        help="validate one durable committed archive report and every image",
    )
    verify_report.add_argument(
        "report_directory", help="canonical durable archive report directory"
    )
    restore = commands.add_parser(
        "restore-generation-1",
        help=(
            "verify archive and atomically restore the closed Colorado "
            "generation-1 contract under the same lease"
        ),
    )
    restore.add_argument(
        "--report-directory", required=True,
        help="committed archive report that recorded the complete current image",
    )
    restore.add_argument(
        "--repo-root", required=True,
        help="canonical Git worktree root containing the fixed restore targets",
    )
    restore.add_argument(
        "--source-commit", required=True,
        help="exact commit pinned by the built-in generation-1 contract",
    )
    archive = commands.add_parser(
        "archive-batch",
        help=(
            "archive one complete source/destination set under canonical "
            "exclusive publication locks"
        ),
    )
    archive.add_argument(
        "--report-directory", required=True,
        help="new canonical directory owned by this archive operation",
    )
    archive.add_argument(
        "--resource", action="append", default=[], dest="resources",
        help=(
            "additional exact writer resource to lock exclusively; repeat for "
            "nested journal/object writer resources"
        ),
    )
    archive.add_argument(
        "--pair", action="append", nargs=2, default=[],
        metavar=("SOURCE", "DESTINATION"),
        help="required canonical archive pair; repeat within the same lease",
    )
    archive.add_argument(
        "--optional-pair", action="append", nargs=2, default=[],
        metavar=("SOURCE", "DESTINATION"),
        help=(
            "canonical pair whose source is classified present or absent only "
            "after every archive lock is held; repeat within the same lease"
        ),
    )
    move = commands.add_parser(
        "move-no-replace",
        help="safely archive one owned file/directory to one absent exact path",
    )
    move.add_argument(
        "--report-directory",
        help="new canonical directory owned by this archive operation",
    )
    move.add_argument("source", help="canonical absolute source path")
    move.add_argument("destination", help="canonical absolute destination path")
    args = parser.parse_args(argv)
    try:
        if args.command == "seal":
            result = seal_registry_objects(args.registry)
        elif args.command == "verify-stage":
            result = verify_archived_stage(
                args.journal, args.stage_directory
            )
        elif args.command == "verify-archive-report":
            result = verify_archive_report(args.report_directory)
        elif args.command == "restore-generation-1":
            result = restore_generation_1(
                args.report_directory,
                repo_root=args.repo_root,
                source_commit=args.source_commit,
            )
        elif args.command == "archive-batch":
            result = archive_batch(
                args.pair,
                optional_pairs=args.optional_pair,
                lock_resources=args.resources,
                report_directory=args.report_directory,
            )
        else:
            result = move_no_replace(
                args.source, args.destination,
                report_directory=args.report_directory,
            )
    except (ArchiveBatchError, Generation1RestoreError) as error:
        print(json.dumps(
            error.report, sort_keys=True, separators=(",", ":"),
        ))
        raise SystemExit(2) from error
    except (ArchiveReportSetupError, OSError, TypeError, ValueError) as error:
        raise SystemExit(str(error)) from error
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
