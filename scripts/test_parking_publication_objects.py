"""Synthetic hostile tests for bounded publication-proof objects."""
from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
TOOLS = HERE / "parking-adjud" / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import publication_objects as objects  # noqa: E402
import trusted_filesystem as trusted_fs  # noqa: E402


def _stage_install(tmp_path: Path, data: bytes):
    stage = tmp_path / "staged"
    data_root = tmp_path / "data"
    logical, staged = objects.stage_logical_object((data,), stage)
    installed = objects.install_staged_objects(data_root, staged)
    assert installed == tuple(sorted(
        (leaf for leaf in logical.leaves), key=lambda value: value.sha256
    ))
    return data_root, stage, logical, staged


def _object_path(data_root: Path, ref: objects.ObjectRef) -> Path:
    return data_root.joinpath(*ref.path.split("/"))


def test_content_addressed_objects_deduplicate_and_reconstruct_in_order(
        tmp_path, monkeypatch):
    monkeypatch.setattr(objects, "MAX_LEAF_SIZE", 8)
    payload = b"abcdefghABCDEFGHtail"
    first, first_stages = objects.stage_logical_object(
        (payload[:3], payload[3:11], payload[11:]), tmp_path / "stage"
    )
    second, second_stages = objects.stage_logical_object(
        (payload,), tmp_path / "stage"
    )
    assert first == second
    assert [leaf.length for leaf in first.leaves] == [8, 8, 4]
    assert len({stage.stage_path for stage in first_stages + second_stages}) == 3

    data_root = tmp_path / "data"
    objects.install_staged_objects(data_root, first_stages)
    objects.install_staged_objects(data_root, second_stages)
    assert b"".join(objects.iter_object_bytes(data_root, first)) == payload
    assert objects.hash_object_stream(
        objects.iter_object_bytes(data_root, second)
    ) == (len(payload), hashlib.sha256(payload).hexdigest())

    reversed_ref = objects.LogicalObjectRef(
        first.length, first.sha256, tuple(reversed(first.leaves))
    )
    with pytest.raises(ValueError, match="length is noncanonical|SHA-256 mismatch"):
        b"".join(objects.iter_object_bytes(data_root, reversed_ref))


def test_empty_object_has_one_zero_length_leaf(tmp_path):
    data_root, _stage, logical, _stages = _stage_install(tmp_path, b"")
    assert logical.length == 0
    assert len(logical.leaves) == 1 and logical.leaves[0].length == 0
    assert b"".join(objects.iter_object_bytes(data_root, logical)) == b""


@pytest.mark.parametrize(
    "path",
    [
        "/publication-proof-objects/sha256/aa/" + "a" * 62,
        "../publication-proof-objects/sha256/aa/" + "a" * 62,
        "publication-proof-objects/../sha256/aa/" + "a" * 62,
        "publication-proof-objects\\sha256\\aa\\" + "a" * 62,
        "publication-proof-objects//sha256/aa/" + "a" * 62,
        "publication-proof-objects/sha256/aa/" + "a" * 61,
    ],
)
def test_object_ref_rejects_absolute_traversal_backslash_and_noncanonical_paths(
        path):
    with pytest.raises(ValueError):
        objects.validate_object_ref({
            "path": path,
            "length": 1,
            "sha256": "a" * 64,
        })


def test_descriptor_transitively_binds_path_length_and_sha256(tmp_path):
    payload = b"bound bytes"
    data_root, _stage, logical, _stages = _stage_install(tmp_path, payload)
    leaf = logical.leaves[0]
    assert b"".join(objects.iter_object_bytes(data_root, logical)) == payload

    with pytest.raises(ValueError, match="length"):
        b"".join(objects.iter_object_bytes(data_root, {
            **logical.to_dict(),
            "length": logical.length + 1,
        }))
    wrong_leaf = leaf.to_dict() | {"length": leaf.length + 1}
    with pytest.raises(ValueError, match="length"):
        b"".join(objects.iter_object_bytes(data_root, {
            **logical.to_dict(), "length": logical.length + 1,
            "leaves": [wrong_leaf],
        }))
    with pytest.raises(ValueError, match="path does not match"):
        objects.validate_object_ref(
            leaf.to_dict() | {"sha256": "b" * 64}
        )

    path = _object_path(data_root, leaf)
    path.chmod(0o600)
    path.write_bytes(b"changed byte content")
    path.chmod(0o400)
    with pytest.raises(ValueError, match="length|SHA-256"):
        b"".join(objects.iter_object_bytes(data_root, logical))


def test_object_reads_reject_symlink_and_hard_link(tmp_path):
    payload = b"immutable"
    digest = hashlib.sha256(payload).hexdigest()
    ref = objects.ObjectRef(
        objects.canonical_object_path(digest), len(payload), digest
    )
    data_root = tmp_path / "data"
    path = _object_path(data_root, ref)
    path.parent.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.write_bytes(payload)
    outside.chmod(0o400)
    path.symlink_to(outside)
    with pytest.raises((OSError, ValueError), match="linked|regular"):
        b"".join(objects.iter_object_bytes(data_root, ref))

    path.unlink()
    path.write_bytes(payload)
    path.chmod(0o400)
    alias = tmp_path / "alias"
    os.link(path, alias)
    with pytest.raises(ValueError, match="link count"):
        b"".join(objects.iter_object_bytes(data_root, ref))


def test_object_read_rejects_wrong_owner_and_nontrivial_acl_mocked(
        tmp_path, monkeypatch):
    data_root, _stage, logical, _stages = _stage_install(tmp_path, b"owned")
    with monkeypatch.context() as patch:
        patch.setattr(trusted_fs.os, "geteuid", lambda: os.getuid() + 1)
        with pytest.raises(ValueError, match="owned"):
            b"".join(objects.iter_object_bytes(data_root, logical))

    real_acl = trusted_fs.require_trivial_acl_fd

    def reject_file_acl(fd, path, *, is_directory=False):
        if not is_directory and Path(path).name == logical.leaves[0].sha256[2:]:
            raise ValueError("synthetic nontrivial ACL")
        return real_acl(fd, path, is_directory=is_directory)

    monkeypatch.setattr(
        trusted_fs, "require_trivial_acl_fd", reject_file_acl
    )
    with pytest.raises(ValueError, match="nontrivial ACL"):
        b"".join(objects.iter_object_bytes(data_root, logical))


@pytest.mark.parametrize(
    "checkout_mode,sealed_mode",
    [(0o644, 0o444), (0o664, 0o444), (0o755, 0o444),
     (0o555, 0o444)],
)
def test_read_only_seal_replaces_git_checkout_modes_with_fresh_inode(
        tmp_path, checkout_mode, sealed_mode):
    data_root, _stage, logical, _stages = _stage_install(tmp_path, b"seal me")
    path = _object_path(data_root, logical.leaves[0])
    path.chmod(checkout_mode)
    original_inode = path.stat().st_ino
    if checkout_mode & 0o222:
        with pytest.raises(ValueError, match="writable and not sealed"):
            b"".join(objects.iter_object_bytes(data_root, logical))
    else:
        assert b"".join(objects.iter_object_bytes(data_root, logical)) == b"seal me"
    assert stat.S_IMODE(path.stat().st_mode) == checkout_mode

    assert objects.seal_object_permissions(
        data_root, logical.leaves
    ) == logical.leaves
    assert path.stat().st_ino != original_inode
    assert stat.S_IMODE(path.stat().st_mode) == sealed_mode
    assert b"".join(objects.iter_object_bytes(data_root, logical)) == b"seal me"


def test_seal_retires_preopened_writer_before_reporting_success(tmp_path):
    payload = b"seal-race-original"
    changed = b"seal-race-tampered"
    assert len(changed) == len(payload)
    data_root, _stage, logical, _stages = _stage_install(tmp_path, payload)
    path = _object_path(data_root, logical.leaves[0])
    path.chmod(0o600)
    old_inode = path.stat().st_ino
    writer = os.open(path, os.O_WRONLY)
    try:
        assert objects.seal_object_permissions(
            data_root, logical.leaves
        ) == logical.leaves
        canonical_inode = path.stat().st_ino
        assert canonical_inode != old_inode
        assert os.fstat(writer).st_ino == old_inode
        os.pwrite(writer, changed, 0)
        os.fsync(writer)
    finally:
        os.close(writer)

    assert path.stat().st_ino == canonical_inode
    assert path.read_bytes() == payload
    assert b"".join(objects.iter_object_bytes(data_root, logical)) == payload


def test_registry_driven_seal_closes_git_style_manifest_and_object_modes(
        tmp_path, capsys):
    data_root = tmp_path / "data"
    stage_root = tmp_path / "stage"
    payload, payload_stages = objects.stage_logical_object(
        (b"registry payload",), stage_root
    )
    manifest = {
        "version": objects.PROOF_MANIFEST_VERSION,
        "kind": objects.PROOF_MANIFEST_KIND,
        "objects": {payload.sha256: payload.to_dict()},
        "object_closure_sha256": objects.object_closure_sha256([payload]),
    }
    manifest_raw = objects._pretty_json(manifest)
    manifest_logical, manifest_stages = objects.stage_logical_object(
        (manifest_raw,), stage_root
    )
    objects.install_staged_objects(
        data_root, payload_stages + manifest_stages
    )
    payload_path = _object_path(data_root, payload.leaves[0])
    manifest_path = _object_path(data_root, manifest_logical.leaves[0])
    payload_path.chmod(0o644)
    manifest_path.chmod(0o644)
    registry_path = data_root / "store_publication_proofs.json"
    registry_path.write_bytes(objects._pretty_json({
        "version": objects.REGISTRY_VERSION,
        "kind": objects.REGISTRY_KIND,
        "proofs": {
            manifest_logical.sha256:
                manifest_logical.leaves[0].to_dict(),
        },
    }))

    assert objects.main(["seal", str(registry_path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["manifests"] == 1
    assert result["logical_objects"] == 1
    assert result["leaves"] == 1
    assert result["unique_bytes"] == len(b"registry payload")
    assert stat.S_IMODE(payload_path.stat().st_mode) == 0o444
    assert stat.S_IMODE(manifest_path.stat().st_mode) == 0o444
    assert b"".join(objects.iter_object_bytes(data_root, payload)) == b"registry payload"


def test_registry_driven_seal_rejects_legacy_registry(tmp_path):
    registry = tmp_path / "legacy_publication_proofs.json"
    registry.write_bytes(objects._pretty_json({"version": 1, "proofs": {}}))
    with pytest.raises(ValueError, match="only compact"):
        objects.seal_registry_objects(registry)


def test_stream_rejects_inode_path_rotation(tmp_path, monkeypatch):
    data_root, _stage, logical, _stages = _stage_install(
        tmp_path, b"rotation target"
    )
    path = _object_path(data_root, logical.leaves[0])
    replacement = path.with_name("replacement")
    replacement.write_bytes(b"rotation target")
    replacement.chmod(0o400)
    displaced = path.with_name("displaced")
    real_read = trusted_fs.os.read
    rotated = False

    def rotate_after_read(fd, count):
        nonlocal rotated
        result = real_read(fd, count)
        if result and not rotated:
            rotated = True
            path.rename(displaced)
            replacement.rename(path)
        return result

    monkeypatch.setattr(trusted_fs.os, "read", rotate_after_read)
    with pytest.raises(ValueError, match="changed"):
        b"".join(objects.iter_object_bytes(data_root, logical))
    assert displaced.read_bytes() == b"rotation target"


def test_object_reader_never_restarts_leaf_after_yielding_bytes(
        tmp_path, monkeypatch):
    payload = b"yield-once-before-link-race"
    digest = hashlib.sha256(payload).hexdigest()
    ref = objects.ObjectRef(
        objects.canonical_object_path(digest), len(payload), digest
    )
    data_root = tmp_path / "data"
    target = _object_path(data_root, ref)
    target.parent.mkdir(parents=True)
    target.write_bytes(payload)
    target.chmod(0o400)
    stage_path = tmp_path / "stage" / digest
    stage_path.parent.mkdir()
    staged = (objects.StagedObject(ref, stage_path),)
    real_read = trusted_fs.os.read
    linked = False

    def link_after_first_read(fd, count):
        nonlocal linked
        result = real_read(fd, count)
        if result and not linked:
            linked = True
            os.link(target, stage_path)
        return result

    monkeypatch.setattr(trusted_fs.os, "read", link_after_first_read)
    yielded = []
    with pytest.raises(ValueError, match="changed|link count"):
        yielded.extend(objects.iter_object_bytes(data_root, ref, staged))
    assert linked
    assert yielded == [payload]


def test_reader_never_requests_more_than_one_mib(tmp_path, monkeypatch):
    payload = b"x" * (objects.READ_SIZE * 2 + 17)
    data_root, _stage, logical, _stages = _stage_install(tmp_path, payload)
    real_read = trusted_fs.os.read
    requested = []

    def recording_read(fd, count):
        requested.append(count)
        return real_read(fd, count)

    monkeypatch.setattr(trusted_fs.os, "read", recording_read)
    assert objects.hash_object_stream(
        objects.iter_object_bytes(data_root, logical)
    )[0] == len(payload)
    assert requested and max(requested) <= objects.READ_SIZE


def test_short_stream_read_fails_closed(tmp_path, monkeypatch):
    data_root, _stage, logical, _stages = _stage_install(
        tmp_path, b"short read must fail"
    )
    real_read = trusted_fs.os.read
    calls = 0

    def premature_eof(fd, count):
        nonlocal calls
        calls += 1
        if calls == 1:
            return real_read(fd, 3)
        return b""

    monkeypatch.setattr(trusted_fs.os, "read", premature_eof)
    with pytest.raises(ValueError, match="short read"):
        b"".join(objects.iter_object_bytes(data_root, logical))


def test_oversized_stream_read_fails_closed(tmp_path, monkeypatch):
    data_root, _stage, logical, _stages = _stage_install(
        tmp_path, b"oversized read must fail"
    )
    real_read = trusted_fs.os.read
    injected = False

    def oversized(fd, count):
        nonlocal injected
        result = real_read(fd, count)
        if result and not injected:
            injected = True
            return result + b"x" * (count - len(result) + 1)
        return result

    monkeypatch.setattr(trusted_fs.os, "read", oversized)
    with pytest.raises(ValueError, match="oversized read"):
        b"".join(objects.iter_object_bytes(data_root, logical))


def test_install_is_immutable_idempotent_and_recovers_link_cut(tmp_path):
    payload = b"crash-recoverable"
    stage_root = tmp_path / "stage"
    data_root = tmp_path / "data"
    logical, staged = objects.stage_logical_object((payload,), stage_root)
    stage = staged[0].stage_path
    ref = staged[0].ref
    target = _object_path(data_root, ref)
    target.parent.mkdir(parents=True)

    os.link(stage, target)
    assert stage.stat().st_ino == target.stat().st_ino
    assert stage.stat().st_nlink == 2
    retry_logical, retry_staged = objects.stage_logical_object(
        (payload,), stage_root
    )
    assert retry_logical == logical
    objects.install_staged_objects(data_root, retry_staged)
    assert stage.exists()
    assert stage.stat().st_nlink == target.stat().st_nlink == 1
    assert stage.stat().st_ino != target.stat().st_ino
    assert b"".join(objects.iter_object_bytes(data_root, logical)) == payload

    duplicate, duplicate_stages = objects.stage_logical_object(
        (payload,), stage_root
    )
    inode = target.stat().st_ino
    objects.install_staged_objects(data_root, duplicate_stages)
    assert target.stat().st_ino == inode
    assert duplicate == logical


def test_install_rejects_conflicting_existing_hash_path(tmp_path):
    payload = b"expected"
    logical, staged = objects.stage_logical_object(
        (payload,), tmp_path / "stage"
    )
    data_root = tmp_path / "data"
    target = _object_path(data_root, logical.leaves[0])
    target.parent.mkdir(parents=True)
    target.write_bytes(b"different")
    target.chmod(0o400)
    with pytest.raises(ValueError, match="length|SHA-256"):
        objects.install_staged_objects(data_root, staged)
    assert target.read_bytes() == b"different"


def test_install_never_replaces_target_created_at_link_cut(
        tmp_path, monkeypatch):
    logical, staged = objects.stage_logical_object(
        (b"expected",), tmp_path / "stage"
    )
    data_root = tmp_path / "data"
    target = _object_path(data_root, logical.leaves[0])
    real_link = objects.os.link
    injected = False

    def create_conflict_then_link(
            src, dst, *, src_dir_fd=None, dst_dir_fd=None,
            follow_symlinks=True):
        nonlocal injected
        if not injected and dst == target.name:
            injected = True
            conflict_fd = os.open(
                dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o400, dir_fd=dst_dir_fd,
            )
            try:
                os.write(conflict_fd, b"different")
                os.fsync(conflict_fd)
            finally:
                os.close(conflict_fd)
        return real_link(
            src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )

    monkeypatch.setattr(objects.os, "link", create_conflict_then_link)
    with pytest.raises(ValueError, match="length|SHA-256"):
        objects.install_staged_objects(data_root, staged)
    assert injected
    assert target.read_bytes() == b"different"


def test_atomic_stream_no_replace_preserves_concurrent_creator(
        tmp_path, monkeypatch):
    target = tmp_path / "atomic-target.bin"
    payload = b"intended stream"
    concurrent = b"concurrent owner"
    real_link = trusted_fs.os.link
    injected = False

    def create_target_then_link(
            src, dst, *, src_dir_fd=None, dst_dir_fd=None,
            follow_symlinks=True):
        nonlocal injected
        if not injected and dst == target.name:
            injected = True
            conflict_fd = os.open(
                dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600, dir_fd=dst_dir_fd,
            )
            try:
                os.write(conflict_fd, concurrent)
                os.fsync(conflict_fd)
            finally:
                os.close(conflict_fd)
        return real_link(
            src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )

    monkeypatch.setattr(trusted_fs.os, "link", create_target_then_link)
    with pytest.raises(FileExistsError, match="already exists"):
        trusted_fs.atomic_write_stream(
            target, (payload,), expected_length=len(payload),
            expected_sha256=hashlib.sha256(payload).hexdigest(),
        )
    assert injected
    assert target.read_bytes() == concurrent
    assert not any(".stream-" in path.name for path in tmp_path.iterdir())


def test_object_closure_deduplicates_leaves_and_ignores_unreferenced_objects(
        tmp_path):
    data_root = tmp_path / "data"
    first, first_stages = objects.stage_logical_object(
        (b"shared",), tmp_path / "stage"
    )
    second, second_stages = objects.stage_logical_object(
        (b"shared",), tmp_path / "stage"
    )
    unused, unused_stages = objects.stage_logical_object(
        (b"unreferenced",), tmp_path / "stage"
    )
    objects.install_staged_objects(
        data_root, first_stages + second_stages + unused_stages
    )
    result = objects.verify_object_closure(data_root, [first, second])
    assert result == {
        "logical_objects": 1,
        "leaves": 1,
        "unique_bytes": len(b"shared"),
        "max_leaf": len(b"shared"),
        "object_closure_sha256": objects.object_closure_sha256([first]),
    }
    assert unused.sha256 not in {first.sha256, second.sha256}


def test_logical_object_rejects_leaf_over_32_mib_without_reading():
    digest = "a" * 64
    with pytest.raises(ValueError, match="32 MiB"):
        objects.validate_object_ref({
            "path": objects.canonical_object_path(digest),
            "length": objects.MAX_LEAF_SIZE + 1,
            "sha256": digest,
        })


def _move_test_paths(tmp_path: Path) -> tuple[Path, Path]:
    source_parent = tmp_path / "source-parent"
    destination_parent = tmp_path / "destination-parent"
    source_parent.mkdir(mode=0o700)
    destination_parent.mkdir(mode=0o700)
    return source_parent, destination_parent


def _archive_report(tmp_path: Path, name: str = "archive-report") -> Path:
    return tmp_path / name


def _report_document(report: Path, name: str) -> dict:
    return objects._strict_json(
        (report / name).read_bytes(), f"test archive report {name}"
    )


def test_move_no_replace_moves_file_and_syncs_destination_before_source_parent(
        tmp_path, monkeypatch):
    source_parent, destination_parent = _move_test_paths(tmp_path)
    source = source_parent / "source.bin"
    destination = destination_parent / "destination.bin"
    source.write_bytes(b"forensic bytes")
    before = source.stat()
    source_parent_identity = (
        source_parent.stat().st_dev, source_parent.stat().st_ino
    )
    destination_parent_identity = (
        destination_parent.stat().st_dev, destination_parent.stat().st_ino
    )
    synced_after_rename = []
    renamed = False
    real_fsync = objects.os.fsync
    native_rename = objects._load_atomic_no_replace_rename()

    def load_recording_rename():
        def recording_rename(*args):
            nonlocal renamed
            native_rename(*args)
            renamed = True
        return recording_rename

    def recording_fsync(fd):
        value = os.fstat(fd)
        if renamed:
            synced_after_rename.append((value.st_dev, value.st_ino))
        return real_fsync(fd)

    monkeypatch.setattr(
        objects, "_load_atomic_no_replace_rename", load_recording_rename
    )
    monkeypatch.setattr(objects.os, "fsync", recording_fsync)
    result = objects.move_no_replace(source, destination)

    assert not source.exists()
    assert destination.read_bytes() == b"forensic bytes"
    after = destination.stat()
    assert (after.st_dev, after.st_ino) == (before.st_dev, before.st_ino)
    assert synced_after_rename[:3] == [
        (before.st_dev, before.st_ino),
        destination_parent_identity,
        source_parent_identity,
    ]
    assert len(synced_after_rename) > 3  # checkpoint and terminal durability
    assert result["source"] == str(source)
    assert result["destination"] == str(destination)
    assert result["kind"] == "file"
    assert result["device"] == before.st_dev
    assert result["inode"] == before.st_ino
    safety = Path(result["safety_copy"])
    assert safety.read_bytes() == b"forensic bytes"
    assert stat.S_IMODE(safety.stat().st_mode) == 0o400


def test_move_no_replace_moves_complete_directory(tmp_path):
    source_parent, destination_parent = _move_test_paths(tmp_path)
    source = source_parent / "object-stage"
    source.mkdir(mode=0o700)
    (source / "leaf").write_bytes(b"leaf bytes")
    destination = destination_parent / "archived-object-stage"
    before = source.stat()

    result = objects.move_no_replace(source, destination)

    assert not source.exists()
    assert (destination / "leaf").read_bytes() == b"leaf bytes"
    after = destination.stat()
    assert (after.st_dev, after.st_ino) == (before.st_dev, before.st_ino)
    assert result["kind"] == "directory"


def test_directory_archive_syncs_moved_directory_before_both_parents(
        tmp_path, monkeypatch):
    source_parent, destination_parent = _move_test_paths(tmp_path)
    source = source_parent / "source-tree"
    source.mkdir(mode=0o700)
    (source / "leaf").write_bytes(b"tree evidence")
    destination = destination_parent / "destination-tree"
    source_identity = (source.stat().st_dev, source.stat().st_ino)
    destination_parent_identity = (
        destination_parent.stat().st_dev, destination_parent.stat().st_ino
    )
    source_parent_identity = (
        source_parent.stat().st_dev, source_parent.stat().st_ino
    )
    native_rename = objects._load_atomic_no_replace_rename()
    real_fsync = objects.os.fsync
    renamed = False
    synced = []

    def load_recording_rename():
        def recording_rename(*args):
            nonlocal renamed
            native_rename(*args)
            renamed = True
        return recording_rename

    def recording_fsync(fd):
        value = os.fstat(fd)
        if renamed:
            synced.append((value.st_dev, value.st_ino))
        return real_fsync(fd)

    monkeypatch.setattr(
        objects, "_load_atomic_no_replace_rename", load_recording_rename
    )
    monkeypatch.setattr(objects.os, "fsync", recording_fsync)
    result = objects.archive_batch(((source, destination),))

    assert synced[:3] == [
        source_identity, destination_parent_identity, source_parent_identity,
    ]
    assert len(synced) > 3  # checkpoint and terminal durability
    assert (destination / "leaf").read_bytes() == b"tree evidence"
    safety = Path(result["moves"][0]["safety_copy"])
    assert (safety / "leaf").read_bytes() == b"tree evidence"


@pytest.mark.parametrize("destination_kind", ["file", "directory"])
def test_move_no_replace_preserves_preexisting_destination(
        tmp_path, destination_kind):
    source_parent, destination_parent = _move_test_paths(tmp_path)
    source = source_parent / "source"
    source.write_bytes(b"source bytes")
    destination = destination_parent / "destination"
    if destination_kind == "file":
        destination.write_bytes(b"existing bytes")
        expected = b"existing bytes"
    else:
        destination.mkdir(mode=0o700)
        (destination / "existing-leaf").write_bytes(b"existing tree")
        expected = b"existing tree"
    source_inode = source.stat().st_ino
    destination_inode = destination.stat().st_ino

    with pytest.raises(FileExistsError, match="already exists"):
        objects.move_no_replace(source, destination)

    assert source.read_bytes() == b"source bytes"
    assert source.stat().st_ino == source_inode
    assert destination.stat().st_ino == destination_inode
    if destination_kind == "file":
        assert destination.read_bytes() == expected
    else:
        assert (destination / "existing-leaf").read_bytes() == expected


def test_move_no_replace_preserves_creator_at_exact_syscall_race(
        tmp_path, monkeypatch):
    source_parent, destination_parent = _move_test_paths(tmp_path)
    source = source_parent / "source"
    source.write_bytes(b"source bytes")
    destination = destination_parent / "destination"
    source_inode = source.stat().st_ino
    native_rename = objects._load_atomic_no_replace_rename()
    raced = False

    def load_racing_rename():
        def racing_rename(
                source_parent_fd, source_name,
                destination_parent_fd, destination_name):
            nonlocal raced
            raced = True
            conflict_fd = os.open(
                destination_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=destination_parent_fd,
            )
            try:
                os.write(conflict_fd, b"concurrent bytes")
                os.fsync(conflict_fd)
            finally:
                os.close(conflict_fd)
            native_rename(
                source_parent_fd, source_name,
                destination_parent_fd, destination_name,
            )

        return racing_rename

    monkeypatch.setattr(
        objects, "_load_atomic_no_replace_rename", load_racing_rename
    )
    with pytest.raises(FileExistsError):
        objects.move_no_replace(source, destination)

    assert raced
    assert source.read_bytes() == b"source bytes"
    assert source.stat().st_ino == source_inode
    assert destination.read_bytes() == b"concurrent bytes"


@pytest.mark.parametrize("linked_side", ["source", "destination"])
def test_move_no_replace_rejects_symlinked_parent(
        tmp_path, linked_side):
    source_parent, destination_parent = _move_test_paths(tmp_path)
    source = source_parent / "source"
    source.write_bytes(b"source bytes")
    destination = destination_parent / "destination"
    if linked_side == "source":
        linked_parent = tmp_path / "linked-source-parent"
        linked_parent.symlink_to(source_parent, target_is_directory=True)
        source = linked_parent / source.name
    else:
        linked_parent = tmp_path / "linked-destination-parent"
        linked_parent.symlink_to(destination_parent, target_is_directory=True)
        destination = linked_parent / destination.name

    with pytest.raises((objects.ArchiveReportSetupError, OSError, ValueError)):
        objects.move_no_replace(source, destination)

    assert (source_parent / "source").read_bytes() == b"source bytes"
    assert not (destination_parent / "destination").exists()


@pytest.mark.parametrize("unsafe_side", ["source", "destination"])
def test_move_no_replace_rejects_group_world_writable_parent(
        tmp_path, unsafe_side):
    source_parent, destination_parent = _move_test_paths(tmp_path)
    source = source_parent / "source"
    source.write_bytes(b"source bytes")
    destination = destination_parent / "destination"
    unsafe_parent = (
        source_parent if unsafe_side == "source" else destination_parent
    )
    unsafe_parent.chmod(0o777)
    try:
        with pytest.raises(
                (objects.ArchiveReportSetupError, ValueError),
                match="writable|trusted"):
            objects.move_no_replace(source, destination)
    finally:
        unsafe_parent.chmod(0o700)

    assert source.read_bytes() == b"source bytes"
    assert not destination.exists()


def test_move_no_replace_rejects_source_symlink(tmp_path):
    source_parent, destination_parent = _move_test_paths(tmp_path)
    target = source_parent / "target"
    target.write_bytes(b"target bytes")
    source = source_parent / "source"
    source.symlink_to(target)
    destination = destination_parent / "destination"

    with pytest.raises(ValueError, match="never be a symlink"):
        objects.move_no_replace(source, destination)

    assert source.is_symlink()
    assert target.read_bytes() == b"target bytes"
    assert not destination.exists()


def test_move_no_replace_requires_canonical_absolute_distinct_paths(tmp_path):
    source_parent, destination_parent = _move_test_paths(tmp_path)
    source = source_parent / "source"
    source.write_bytes(b"source bytes")
    destination = destination_parent / "destination"

    with pytest.raises(ValueError, match="canonical and absolute"):
        objects.move_no_replace("source", destination)
    with pytest.raises(ValueError, match="distinct"):
        objects.move_no_replace(source, source)

    assert source.read_bytes() == b"source bytes"
    assert not destination.exists()


def test_move_no_replace_rejects_wrong_owner_and_nonregular_source(
        tmp_path, monkeypatch):
    source_parent, destination_parent = _move_test_paths(tmp_path)
    source = source_parent / "source"
    source.write_bytes(b"source bytes")
    destination = destination_parent / "destination"
    real_stat = objects.os.stat

    def wrong_owner_stat(path, *args, **kwargs):
        value = real_stat(path, *args, **kwargs)
        if (path == source.name and kwargs.get("dir_fd") is not None
                and kwargs.get("follow_symlinks") is False):
            fields = list(value)
            fields[4] = os.geteuid() + 1
            return os.stat_result(fields)
        return value

    monkeypatch.setattr(objects.os, "stat", wrong_owner_stat)
    with pytest.raises(ValueError, match="owned by the current uid"):
        objects.move_no_replace(source, destination)
    monkeypatch.setattr(objects.os, "stat", real_stat)

    source.unlink()
    os.mkfifo(source, 0o600)
    with pytest.raises(ValueError, match="regular file or directory"):
        objects.move_no_replace(source, destination)
    assert stat.S_ISFIFO(source.lstat().st_mode)
    assert not destination.exists()


def test_move_no_replace_detects_source_rotation_before_syscall(
        tmp_path, monkeypatch):
    source_parent, destination_parent = _move_test_paths(tmp_path)
    source = source_parent / "source"
    source.write_bytes(b"original source")
    displaced = source_parent / "displaced"
    destination = destination_parent / "destination"
    real_stat = objects.os.stat
    called = False
    rotated = False

    def rotate_at_destination_check(path, *args, **kwargs):
        nonlocal rotated
        if (not rotated and path == destination.name
                and kwargs.get("dir_fd") is not None
                and kwargs.get("follow_symlinks") is False):
            rotated = True
            source.rename(displaced)
            source.write_bytes(b"replacement source")
        return real_stat(path, *args, **kwargs)

    def load_unexpected_rename():
        def unexpected(*_args):
            nonlocal called
            called = True

        return unexpected

    monkeypatch.setattr(objects.os, "stat", rotate_at_destination_check)
    monkeypatch.setattr(
        objects, "_load_atomic_no_replace_rename", load_unexpected_rename
    )
    with pytest.raises(ValueError, match="identity changed before safety copy"):
        objects.move_no_replace(source, destination)

    assert rotated
    assert not called
    assert displaced.read_bytes() == b"original source"
    assert source.read_bytes() == b"replacement source"
    assert not destination.exists()


def test_move_no_replace_rejects_cross_device_before_syscall(
        tmp_path, monkeypatch):
    import errno

    source_parent, destination_parent = _move_test_paths(tmp_path)
    source = source_parent / "source"
    source.write_bytes(b"source bytes")
    destination = destination_parent / "destination"
    destination_parent_fd = None
    called = False
    real_open = trusted_fs.open_trusted_directory_fd
    real_fstat = objects.os.fstat

    def recording_open(path, *args, **kwargs):
        nonlocal destination_parent_fd
        fd = real_open(path, *args, **kwargs)
        if Path(path) == destination_parent:
            destination_parent_fd = fd
        return fd

    def cross_device_fstat(fd):
        value = real_fstat(fd)
        if fd == destination_parent_fd:
            fields = list(value)
            fields[2] = value.st_dev + 1
            return os.stat_result(fields)
        return value

    def load_unexpected_rename():
        def unexpected(*_args):
            nonlocal called
            called = True

        return unexpected

    monkeypatch.setattr(
        trusted_fs, "open_trusted_directory_fd", recording_open
    )
    monkeypatch.setattr(objects.os, "fstat", cross_device_fstat)
    monkeypatch.setattr(
        objects, "_load_atomic_no_replace_rename", load_unexpected_rename
    )
    with pytest.raises(OSError) as failure:
        objects.move_no_replace(
            source, destination, report_directory=tmp_path / "cross-device-report"
        )

    assert failure.value.errno == errno.EXDEV
    assert not called
    assert source.read_bytes() == b"source bytes"
    assert not destination.exists()


def test_atomic_no_replace_loader_uses_linux_renameat2_noreplace(
        monkeypatch):
    import ctypes

    calls = []

    class FakeRenameAt2:
        argtypes = None
        restype = None

        def __call__(self, *args):
            calls.append(args)
            return 0

    class FakeLibc:
        renameat2 = FakeRenameAt2()

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(ctypes, "CDLL", lambda *_args, **_kwargs: FakeLibc())
    rename = objects._load_atomic_no_replace_rename()
    rename(11, "source", 12, "destination")

    assert calls == [(11, b"source", 12, b"destination", 0x00000001)]


def test_move_no_replace_refuses_platform_without_secure_report_before_mutation(
        tmp_path, monkeypatch):
    source_parent, destination_parent = _move_test_paths(tmp_path)
    source = source_parent / "source"
    source.write_bytes(b"source bytes")
    destination = destination_parent / "destination"
    opened = False
    real_open = trusted_fs.open_trusted_directory_fd

    def recording_open(*args, **kwargs):
        nonlocal opened
        opened = True
        return real_open(*args, **kwargs)

    monkeypatch.setattr(sys, "platform", "unsupported-test-platform")
    monkeypatch.setattr(
        trusted_fs, "open_trusted_directory_fd", recording_open
    )
    with pytest.raises(
            objects.ArchiveReportSetupError, match="no archive source"):
        objects.move_no_replace(source, destination)

    assert opened
    assert source.read_bytes() == b"source bytes"
    assert not destination.exists()


def test_move_no_replace_refuses_missing_native_syscall_before_mutation(
        tmp_path, monkeypatch):
    import ctypes
    import errno

    source_parent, destination_parent = _move_test_paths(tmp_path)
    source = source_parent / "source"
    source.write_bytes(b"source bytes")
    destination = destination_parent / "destination"

    class LibcWithoutAtomicRename:
        pass

    monkeypatch.setattr(
        ctypes, "CDLL", lambda *args, **kwargs: LibcWithoutAtomicRename()
    )
    with pytest.raises(OSError) as failure:
        objects.move_no_replace(source, destination)

    assert failure.value.errno in (errno.ENOTSUP, errno.EOPNOTSUPP)
    assert source.read_bytes() == b"source bytes"
    assert not destination.exists()


@pytest.mark.parametrize("error_number", [18, 5], ids=["cross-device", "io-error"])
def test_move_no_replace_preserves_both_paths_on_native_error(
        tmp_path, monkeypatch, error_number):
    source_parent, destination_parent = _move_test_paths(tmp_path)
    source = source_parent / "source"
    source.write_bytes(b"source bytes")
    destination = destination_parent / "destination"
    source_inode = source.stat().st_ino

    def load_failing_rename():
        def fail(*_args):
            raise OSError(error_number, "synthetic native rename failure")

        return fail

    monkeypatch.setattr(
        objects, "_load_atomic_no_replace_rename", load_failing_rename
    )
    with pytest.raises(OSError) as failure:
        objects.move_no_replace(source, destination)

    assert failure.value.errno == error_number
    assert source.read_bytes() == b"source bytes"
    assert source.stat().st_ino == source_inode
    assert not destination.exists()


def test_move_no_replace_cli_reports_success_and_refuses_collision(
        tmp_path, capsys):
    source_parent, destination_parent = _move_test_paths(tmp_path)
    source = source_parent / "source"
    source.write_bytes(b"source bytes")
    destination = destination_parent / "destination"

    assert objects.main([
        "move-no-replace", str(source), str(destination),
    ]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["source"] == str(source)
    assert result["destination"] == str(destination)
    assert result["kind"] == "file"
    assert destination.read_bytes() == b"source bytes"

    second = source_parent / "second"
    second.write_bytes(b"second bytes")
    with pytest.raises(SystemExit, match="already exists"):
        objects.main([
            "move-no-replace", str(second), str(destination),
        ])
    assert second.read_bytes() == b"second bytes"
    assert destination.read_bytes() == b"source bytes"


def test_archive_batch_holds_writer_lock_at_exact_syscall_boundary(
        tmp_path, monkeypatch):
    source_parent, destination_parent = _move_test_paths(tmp_path)
    source = source_parent / "source"
    source.write_bytes(b"writer-locked evidence")
    destination = destination_parent / "destination"
    displaced = source_parent / "cooperating-writer-displaced"
    lock_path = objects.tr.resource_lock_path(tmp_path, source)
    native_rename = objects._load_atomic_no_replace_rename()
    probed = False

    child = """
import fcntl
import os
import sys

handle = os.open(sys.argv[1], os.O_RDWR)
try:
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit(23)
    os.rename(sys.argv[2], sys.argv[3])
finally:
    os.close(handle)
"""

    def load_boundary_probe():
        def boundary_probe(*args):
            nonlocal probed
            probed = True
            result = subprocess.run(
                [
                    sys.executable, "-c", child, str(lock_path),
                    str(source), str(displaced),
                ],
                check=False, capture_output=True, text=True, timeout=10,
            )
            assert result.returncode == 23, result.stderr
            assert source.read_bytes() == b"writer-locked evidence"
            assert not displaced.exists()
            native_rename(*args)
        return boundary_probe

    monkeypatch.setattr(
        objects, "_load_atomic_no_replace_rename", load_boundary_probe
    )
    result = objects.archive_batch(
        ((source, destination),), lock_resources=(source,)
    )

    assert probed
    assert result["archive_state"] == "committed"
    assert destination.read_bytes() == b"writer-locked evidence"
    assert not source.exists()
    assert str(lock_path) in {entry["lock"] for entry in result["locks"]}


def test_archive_batch_holds_all_sorted_locks_through_last_postcondition(
        tmp_path, monkeypatch):
    source_parent, destination_parent = _move_test_paths(tmp_path)
    first = source_parent / "first"
    second = source_parent / "second"
    first.write_bytes(b"first locked")
    second.write_bytes(b"second locked")
    first_destination = destination_parent / "first"
    second_destination = destination_parent / "second"
    transaction_resource = (
        tmp_path / "data" / ".trekdex-publication-transactions"
        / "co_verdicts_osm.json.journal.json"
    )
    opened_locks = []
    postconditions = 0
    real_open_lock = trusted_fs.open_lock_file
    real_sync = objects._sync_and_verify_rename
    child = """
import fcntl
import os
import sys

handle = os.open(sys.argv[1], os.O_RDWR)
try:
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit(23)
finally:
    os.close(handle)
"""

    def recording_open_lock(path):
        opened_locks.append(Path(path))
        return real_open_lock(path)

    def probe_after_postcondition(plan):
        nonlocal postconditions
        real_sync(plan)
        postconditions += 1
        if postconditions == 2:
            for lock_path in opened_locks:
                result = subprocess.run(
                    [sys.executable, "-c", child, str(lock_path)],
                    check=False, capture_output=True, text=True, timeout=10,
                )
                assert result.returncode == 23, (
                    lock_path, result.returncode, result.stderr
                )

    monkeypatch.setattr(
        trusted_fs, "open_lock_file", recording_open_lock
    )
    monkeypatch.setattr(
        objects, "_sync_and_verify_rename", probe_after_postcondition
    )
    result = objects.archive_batch(
        (
            (first, first_destination),
            (second, second_destination),
        ),
        lock_resources=(transaction_resource,),
    )

    assert postconditions == 2
    assert opened_locks == sorted(opened_locks, key=str)
    assert len(opened_locks) == len(set(opened_locks))
    assert result["archive_state"] == "committed"


def test_archive_batch_makes_no_source_change_before_every_lock_is_acquired(
        tmp_path, monkeypatch):
    source_parent, destination_parent = _move_test_paths(tmp_path)
    first = source_parent / "first"
    second = source_parent / "second"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    first_destination = destination_parent / "first"
    second_destination = destination_parent / "second"
    real_open_lock = trusted_fs.open_lock_file
    opened = 0
    safety_called = False

    def fail_second_lock(path):
        nonlocal opened
        opened += 1
        if opened == 2:
            raise OSError("synthetic lock acquisition failure")
        return real_open_lock(path)

    def unexpected_safety(_plan):
        nonlocal safety_called
        safety_called = True
        raise AssertionError("safety copy started before every lock")

    monkeypatch.setattr(trusted_fs, "open_lock_file", fail_second_lock)
    monkeypatch.setattr(
        objects, "_create_verified_safety_copy", unexpected_safety
    )
    report_path = _archive_report(tmp_path, "lock-failure-report")
    with pytest.raises(objects.ArchiveBatchError) as failure:
        objects.archive_batch(
            (
                (first, first_destination),
                (second, second_destination),
            ),
            report_directory=report_path,
        )

    terminal = _report_document(report_path, "terminal.json")
    assert failure.value.report == terminal
    assert terminal["archive_state"] == "not-committed"
    assert terminal["failure"]["phase"] == "lock-acquisition"
    assert opened == 2
    assert not safety_called
    assert first.read_bytes() == b"first"
    assert second.read_bytes() == b"second"
    assert not first_destination.exists()
    assert not second_destination.exists()
    assert not list(destination_parent.glob(".trekdex-archive-safety-*"))


def test_archive_batch_rejects_lock_path_collision_before_source_change(
        tmp_path, monkeypatch):
    source_parent, destination_parent = _move_test_paths(tmp_path)
    source = source_parent / "source"
    source.write_bytes(b"collision-safe")
    destination = destination_parent / "destination"
    collision = tmp_path / ".collision-locks" / "same.lock"

    monkeypatch.setattr(
        objects.tr, "resource_lock_path", lambda _root, _resource: collision
    )
    report_path = _archive_report(tmp_path, "lock-collision-report")
    with pytest.raises(objects.ArchiveBatchError) as failure:
        objects.archive_batch(
            ((source, destination),), report_directory=report_path
        )

    terminal = _report_document(report_path, "terminal.json")
    assert failure.value.report == terminal
    assert terminal["archive_state"] == "not-committed"
    assert terminal["failure"]["phase"] == "lock-acquisition"
    assert source.read_bytes() == b"collision-safe"
    assert not destination.exists()
    assert not list(destination_parent.glob(".trekdex-archive-safety-*"))


def test_archive_batch_preflights_every_destination_before_first_move(
        tmp_path, monkeypatch):
    source_parent, destination_parent = _move_test_paths(tmp_path)
    first = source_parent / "first"
    second = source_parent / "second"
    first.write_bytes(b"first source")
    second.write_bytes(b"second source")
    first_destination = destination_parent / "first"
    occupied = destination_parent / "second"
    occupied.write_bytes(b"existing destination")
    rename_called = False

    def load_unexpected_rename():
        def unexpected(*_args):
            nonlocal rename_called
            rename_called = True
        return unexpected

    monkeypatch.setattr(
        objects, "_load_atomic_no_replace_rename", load_unexpected_rename
    )
    report_path = _archive_report(tmp_path, "occupied-report")
    with pytest.raises(objects.ArchiveBatchError) as failure:
        objects.archive_batch(
            (
                (first, first_destination),
                (second, occupied),
            ),
            report_directory=report_path,
        )

    terminal = _report_document(report_path, "terminal.json")
    assert failure.value.report == terminal
    assert terminal["archive_state"] == "not-committed"
    assert terminal["failure"]["phase"] == "preflight"
    assert terminal["failure"]["type"] == "FileExistsError"
    assert sorted(path.name for path in report_path.iterdir()) == [
        "plan.json", "terminal.json",
    ]
    with pytest.raises(ValueError, match="terminal state is not committed"):
        objects.verify_archive_report(report_path)
    assert not rename_called
    assert first.read_bytes() == b"first source"
    assert second.read_bytes() == b"second source"
    assert not first_destination.exists()
    assert occupied.read_bytes() == b"existing destination"
    assert not list(destination_parent.glob(".trekdex-archive-safety-*"))


def test_noncooperating_directory_rotation_is_indeterminate_with_safety_image(
        tmp_path, monkeypatch):
    source_parent, destination_parent = _move_test_paths(tmp_path)
    source = source_parent / "source-tree"
    source.mkdir(mode=0o700)
    (source / "evidence.bin").write_bytes(b"original forensic evidence")
    replacement = source_parent / "replacement-tree"
    replacement.mkdir(mode=0o700)
    (replacement / "evidence.bin").write_bytes(b"noncooperating replacement")
    displaced = source_parent / "displaced-original"
    destination = destination_parent / "archived-tree"
    native_rename = objects._load_atomic_no_replace_rename()
    raced = False

    def load_noncooperating_rename():
        def noncooperating_rename(*args):
            nonlocal raced
            raced = True
            source.rename(displaced)
            replacement.rename(source)
            native_rename(*args)
        return noncooperating_rename

    monkeypatch.setattr(
        objects, "_load_atomic_no_replace_rename",
        load_noncooperating_rename,
    )
    with pytest.raises(objects.ArchiveBatchError) as failure:
        objects.archive_batch(((source, destination),))

    report = failure.value.report
    assert raced
    assert report["archive_state"] == "committed-indeterminate"
    assert report["blind_retry_forbidden"] is True
    assert report["failure"]["phase"] == "postcondition"
    assert report["failed_move"]["safety_verified"] is True
    safety = Path(report["failed_move"]["safety_copy"])
    safety_fd = trusted_fs.open_trusted_directory_fd(safety)
    try:
        assert objects._tree_evidence(safety_fd, safety) == (
            report["verified_safety_copies"][0]["evidence"]
        )
    finally:
        os.close(safety_fd)
    assert (safety / "evidence.bin").read_bytes() == b"original forensic evidence"
    assert (displaced / "evidence.bin").read_bytes() == b"original forensic evidence"
    assert (destination / "evidence.bin").read_bytes() == b"noncooperating replacement"
    assert not source.exists()


@pytest.mark.parametrize(
    "failure_phase,expected_syncs",
    [
        ("sync-destination-content", ["destination-content"]),
        (
            "sync-destination-parent",
            ["destination-content", "destination-parent"],
        ),
        (
            "sync-source-parent",
            [
                "destination-content", "destination-parent", "source-parent",
            ],
        ),
    ],
)
def test_post_rename_fsync_cut_is_explicit_and_never_blindly_retryable(
        tmp_path, monkeypatch, failure_phase, expected_syncs):
    source_parent, destination_parent = _move_test_paths(tmp_path)
    source = source_parent / "source"
    source.write_bytes(b"durability-cut evidence")
    destination = destination_parent / "destination"
    source_identity = (source.stat().st_dev, source.stat().st_ino)
    source_parent_identity = (
        source_parent.stat().st_dev, source_parent.stat().st_ino
    )
    destination_parent_identity = (
        destination_parent.stat().st_dev, destination_parent.stat().st_ino
    )
    native_rename = objects._load_atomic_no_replace_rename()
    real_fsync = objects.os.fsync
    renamed = False
    cut_triggered = False
    syncs = []

    def load_recording_rename():
        def recording_rename(*args):
            nonlocal renamed
            native_rename(*args)
            renamed = True
        return recording_rename

    def cut_fsync(fd):
        nonlocal cut_triggered
        if not renamed or cut_triggered:
            return real_fsync(fd)
        value = os.fstat(fd)
        identity = (value.st_dev, value.st_ino)
        if identity == source_identity:
            phase = "sync-destination-content"
            label = "destination-content"
        elif identity == destination_parent_identity:
            phase = "sync-destination-parent"
            label = "destination-parent"
        elif identity == source_parent_identity:
            phase = "sync-source-parent"
            label = "source-parent"
        else:
            return real_fsync(fd)
        syncs.append(label)
        result = real_fsync(fd)
        if phase == failure_phase:
            cut_triggered = True
            raise OSError(f"synthetic {phase} failure")
        return result

    monkeypatch.setattr(
        objects, "_load_atomic_no_replace_rename", load_recording_rename
    )
    monkeypatch.setattr(objects.os, "fsync", cut_fsync)
    with pytest.raises(objects.ArchiveBatchError) as failure:
        objects.archive_batch(((source, destination),))

    report = failure.value.report
    report_path = Path(report["report_directory"])
    assert _report_document(report_path, "terminal.json") == report
    assert (report_path / "prepared.json").is_file()
    assert not list(report_path.glob("checkpoint-*.json"))
    assert syncs == expected_syncs
    assert report["archive_state"] == "committed-indeterminate"
    assert report["failure"]["phase"] == failure_phase
    assert report["blind_retry_forbidden"] is True
    assert not source.exists()
    assert destination.read_bytes() == b"durability-cut evidence"
    assert Path(report["failed_move"]["safety_copy"]).read_bytes() == (
        b"durability-cut evidence"
    )


def test_partial_batch_failure_reports_recovery_and_never_overwrites(
        tmp_path, monkeypatch):
    source_parent, destination_parent = _move_test_paths(tmp_path)
    first = source_parent / "first"
    second = source_parent / "second"
    first.write_bytes(b"first evidence")
    second.write_bytes(b"second evidence")
    first_destination = destination_parent / "first"
    second_destination = destination_parent / "second"
    native_rename = objects._load_atomic_no_replace_rename()
    calls = 0

    def load_second_destination_race():
        def second_destination_race(
                source_parent_fd, source_name,
                destination_parent_fd, destination_name):
            nonlocal calls
            calls += 1
            if calls == 2:
                conflict_fd = os.open(
                    destination_name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=destination_parent_fd,
                )
                try:
                    os.write(conflict_fd, b"concurrent destination")
                    os.fsync(conflict_fd)
                finally:
                    os.close(conflict_fd)
            native_rename(
                source_parent_fd, source_name,
                destination_parent_fd, destination_name,
            )
        return second_destination_race

    monkeypatch.setattr(
        objects, "_load_atomic_no_replace_rename",
        load_second_destination_race,
    )
    with pytest.raises(objects.ArchiveBatchError) as failure:
        objects.archive_batch((
            (first, first_destination),
            (second, second_destination),
        ))

    report = failure.value.report
    report_path = Path(report["report_directory"])
    assert _report_document(report_path, "terminal.json") == report
    assert (report_path / "checkpoint-0001.json").is_file()
    assert not (report_path / "checkpoint-0002.json").exists()
    assert calls == 2
    assert report["archive_state"] == "partially-committed"
    assert report["failure"]["phase"] == "rename-no-replace"
    assert report["blind_retry_forbidden"] is True
    assert len(report["completed_moves"]) == 1
    assert len(report["verified_safety_copies"]) == 2
    assert not first.exists()
    assert first_destination.read_bytes() == b"first evidence"
    assert second.read_bytes() == b"second evidence"
    assert second_destination.read_bytes() == b"concurrent destination"
    safety_by_source = {
        entry["source"]: Path(entry["path"])
        for entry in report["verified_safety_copies"]
    }
    assert safety_by_source[str(first)].read_bytes() == b"first evidence"
    assert safety_by_source[str(second)].read_bytes() == b"second evidence"


def test_archive_batch_cli_reports_committed_set(tmp_path, capsys):
    source_parent, destination_parent = _move_test_paths(tmp_path)
    source = source_parent / "source"
    source.write_bytes(b"batch cli evidence")
    destination = destination_parent / "destination"

    report_path = _archive_report(tmp_path, "cli-success-report")
    assert objects.main([
        "archive-batch",
        "--report-directory", str(report_path),
        "--resource", str(source),
        "--pair", str(source), str(destination),
    ]) == 0
    result = json.loads(capsys.readouterr().out)

    assert result["archive_state"] == "committed"
    assert result["report_directory"] == str(report_path)
    assert _report_document(report_path, "terminal.json") == result
    prepared = _report_document(report_path, "prepared.json")
    assert prepared["moves"][0]["source_signature"]["ctime_ns"] > 0
    assert objects.verify_archive_report(report_path)["moves_verified"] == 1
    assert objects.main([
        "verify-archive-report", str(report_path),
    ]) == 0
    verified = json.loads(capsys.readouterr().out)
    assert verified["archive_state"] == "committed"
    assert verified["moves_verified"] == 1
    assert len(result["moves"]) == 1
    assert result["moves"][0]["destination"] == str(destination)
    assert destination.read_bytes() == b"batch cli evidence"
    assert Path(result["moves"][0]["safety_copy"]).read_bytes() == (
        b"batch cli evidence"
    )


def test_archive_batch_cli_emits_indeterminate_report_before_exit(
        tmp_path, monkeypatch, capsys):
    source_parent, destination_parent = _move_test_paths(tmp_path)
    source = source_parent / "source"
    source.write_bytes(b"cli failure evidence")
    destination = destination_parent / "destination"

    def fail_after_rename(_plan):
        raise objects._PostRenameError(
            "sync-source-parent", OSError("synthetic CLI durability failure")
        )

    monkeypatch.setattr(objects, "_sync_and_verify_rename", fail_after_rename)
    report_path = _archive_report(tmp_path, "cli-failure-report")
    with pytest.raises(SystemExit) as failure:
        objects.main([
            "archive-batch",
            "--report-directory", str(report_path),
            "--pair", str(source), str(destination),
        ])

    assert failure.value.code == 2
    report = json.loads(capsys.readouterr().out)
    assert _report_document(report_path, "terminal.json") == report
    assert report["archive_state"] == "committed-indeterminate"
    assert report["failure"]["phase"] == "sync-source-parent"
    assert report["blind_retry_forbidden"] is True
    assert destination.read_bytes() == b"cli failure evidence"
    assert Path(report["failed_move"]["safety_copy"]).read_bytes() == (
        b"cli failure evidence"
    )


def _committed_file_archive(
        tmp_path: Path, name: str = "committed",
) -> tuple[Path, Path, Path, dict]:
    source_parent = tmp_path / f"{name}-source"
    destination_parent = tmp_path / f"{name}-destination"
    source_parent.mkdir(mode=0o700)
    destination_parent.mkdir(mode=0o700)
    source = source_parent / "source.bin"
    destination = destination_parent / "destination.bin"
    source.write_bytes(b"durable archive evidence")
    report = tmp_path / f"{name}-report"
    result = objects.archive_batch(
        ((source, destination),), report_directory=report
    )
    return source, destination, report, result


def test_same_length_restored_mtime_mutation_is_committed_indeterminate(
        tmp_path, monkeypatch):
    source_parent, destination_parent = _move_test_paths(tmp_path)
    source = source_parent / "source.bin"
    original = b"original archive bytes"
    changed = b"tampered archive bytes"
    assert len(original) == len(changed)
    source.write_bytes(original)
    destination = destination_parent / "destination.bin"
    report_path = _archive_report(tmp_path, "metadata-race-report")
    native_rename = objects._load_atomic_no_replace_rename()

    def load_mutating_rename():
        def mutate_then_rename(*args):
            before = source.stat()
            source.write_bytes(changed)
            os.utime(
                source,
                ns=(before.st_atime_ns, before.st_mtime_ns),
                follow_symlinks=False,
            )
            native_rename(*args)
        return mutate_then_rename

    monkeypatch.setattr(
        objects, "_load_atomic_no_replace_rename", load_mutating_rename
    )
    with pytest.raises(objects.ArchiveBatchError) as failure:
        objects.archive_batch(
            ((source, destination),), report_directory=report_path
        )

    terminal = _report_document(report_path, "terminal.json")
    assert failure.value.report == terminal
    assert terminal["archive_state"] == "committed-indeterminate"
    assert terminal["failure"]["phase"] == "postcondition"
    assert not list(report_path.glob("checkpoint-*.json"))
    assert destination.read_bytes() == changed
    assert Path(terminal["failed_move"]["safety_copy"]).read_bytes() == original


def test_deep_restored_mtime_mutation_is_committed_indeterminate(
        tmp_path, monkeypatch):
    source_parent, destination_parent = _move_test_paths(tmp_path)
    source = source_parent / "source-tree"
    source.mkdir(mode=0o700)
    nested = source / "one" / "two"
    nested.mkdir(parents=True, mode=0o700)
    leaf = nested / "leaf.bin"
    original = b"deep original evidence"
    changed = b"deep tampered evidence"
    assert len(original) == len(changed)
    leaf.write_bytes(original)
    destination = destination_parent / "destination-tree"
    report_path = _archive_report(tmp_path, "deep-race-report")
    native_rename = objects._load_atomic_no_replace_rename()

    def load_mutating_rename():
        def mutate_then_rename(*args):
            before = leaf.stat()
            leaf.write_bytes(changed)
            os.utime(
                leaf,
                ns=(before.st_atime_ns, before.st_mtime_ns),
                follow_symlinks=False,
            )
            native_rename(*args)
        return mutate_then_rename

    monkeypatch.setattr(
        objects, "_load_atomic_no_replace_rename", load_mutating_rename
    )
    with pytest.raises(objects.ArchiveBatchError) as failure:
        objects.archive_batch(
            ((source, destination),), report_directory=report_path
        )

    terminal = _report_document(report_path, "terminal.json")
    assert failure.value.report == terminal
    assert terminal["archive_state"] == "committed-indeterminate"
    assert terminal["failure"]["phase"] == "postcondition"
    assert not list(report_path.glob("checkpoint-*.json"))
    assert (destination / "one" / "two" / "leaf.bin").read_bytes() == changed
    safety = Path(terminal["failed_move"]["safety_copy"])
    assert (safety / "one" / "two" / "leaf.bin").read_bytes() == original


def test_post_rename_safety_image_mutation_is_reverified_and_indeterminate(
        tmp_path, monkeypatch):
    source_parent, destination_parent = _move_test_paths(tmp_path)
    source = source_parent / "source.bin"
    original = b"original safety image"
    changed = b"tampered safety image"
    assert len(original) == len(changed)
    source.write_bytes(original)
    destination = destination_parent / "destination.bin"
    report_path = _archive_report(tmp_path, "safety-race-report")
    native_rename = objects._load_atomic_no_replace_rename()

    def load_safety_mutating_rename():
        def mutate_safety_after_rename(*args):
            native_rename(*args)
            safety = next(destination_parent.glob(".trekdex-archive-safety-*"))
            safety.chmod(0o600)
            safety.write_bytes(changed)
            safety.chmod(0o400)
        return mutate_safety_after_rename

    monkeypatch.setattr(
        objects, "_load_atomic_no_replace_rename",
        load_safety_mutating_rename,
    )
    with pytest.raises(objects.ArchiveBatchError) as failure:
        objects.archive_batch(
            ((source, destination),), report_directory=report_path
        )

    terminal = _report_document(report_path, "terminal.json")
    assert failure.value.report == terminal
    assert terminal["archive_state"] == "committed-indeterminate"
    assert terminal["failure"]["phase"] == "postcondition"
    assert destination.read_bytes() == original
    assert Path(terminal["failed_move"]["safety_copy"]).read_bytes() == changed


def test_archive_report_namespace_is_no_clobber_and_precedes_locks(
        tmp_path, monkeypatch):
    source_parent, destination_parent = _move_test_paths(tmp_path)
    source = source_parent / "source"
    destination = destination_parent / "destination"
    source.write_bytes(b"must remain")
    report_path = _archive_report(tmp_path, "occupied-report-namespace")
    report_path.mkdir(mode=0o700)
    marker = report_path / "operator-marker"
    marker.write_bytes(b"do not overwrite")
    lock_called = False

    def unexpected_lock(*_args, **_kwargs):
        nonlocal lock_called
        lock_called = True
        raise AssertionError("locks must not start without a report namespace")

    monkeypatch.setattr(trusted_fs, "locked_resources", unexpected_lock)
    with pytest.raises(
            objects.ArchiveReportSetupError,
            match="no archive source or destination was mutated"):
        objects.archive_batch(
            ((source, destination),), report_directory=report_path
        )

    assert not lock_called
    assert source.read_bytes() == b"must remain"
    assert not destination.exists()
    assert marker.read_bytes() == b"do not overwrite"


def test_archive_checkpoint_never_overwrites_concurrent_report_file(
        tmp_path, monkeypatch):
    source_parent, destination_parent = _move_test_paths(tmp_path)
    source = source_parent / "source"
    destination = destination_parent / "destination"
    source.write_bytes(b"checkpoint collision evidence")
    report = _archive_report(tmp_path, "checkpoint-collision-report")
    real_sync = objects._sync_and_verify_rename
    marker = b"concurrent report owner\n"

    def create_checkpoint_collision(plan):
        real_sync(plan)
        checkpoint = report / "checkpoint-0001.json"
        checkpoint.write_bytes(marker)
        checkpoint.chmod(0o400)

    monkeypatch.setattr(
        objects, "_sync_and_verify_rename", create_checkpoint_collision
    )
    with pytest.raises(objects.ArchiveBatchError) as failure:
        objects.archive_batch(
            ((source, destination),), report_directory=report
        )

    terminal = _report_document(report, "terminal.json")
    assert failure.value.report == terminal
    assert terminal["archive_state"] == "committed-indeterminate"
    assert terminal["failure"]["phase"] == "checkpoint"
    assert (report / "checkpoint-0001.json").read_bytes() == marker
    assert destination.read_bytes() == b"checkpoint collision evidence"


@pytest.mark.parametrize("damage", ["tamper", "missing", "extra"])
def test_verify_archive_report_rejects_report_tamper_missing_and_extra(
        tmp_path, damage):
    _source, _destination, report, _result = _committed_file_archive(
        tmp_path, damage
    )
    checkpoint = report / "checkpoint-0001.json"
    if damage == "tamper":
        checkpoint.chmod(0o600)
        document = json.loads(checkpoint.read_text())
        document["move_index"] = 9
        checkpoint.write_bytes(objects._pretty_json(document))
        checkpoint.chmod(0o400)
    elif damage == "missing":
        quarantine = tmp_path / "Archive-missing-checkpoint-0001.json"
        checkpoint.rename(quarantine)
    else:
        extra = report / "checkpoint-0002.json"
        extra.write_bytes(objects._pretty_json({"unexpected": True}))
        extra.chmod(0o400)

    with pytest.raises(ValueError, match="hash|missing|extra|reordered"):
        objects.verify_archive_report(report)


def test_verify_archive_report_enforces_document_byte_bound(
        tmp_path, monkeypatch):
    _source, _destination, report, _result = _committed_file_archive(
        tmp_path, "bounded"
    )
    monkeypatch.setattr(objects, "ARCHIVE_REPORT_MAX_BYTES", 8)
    with pytest.raises(ValueError, match="byte limit"):
        objects.verify_archive_report(report)


def test_verify_archive_report_bounds_tampered_tree_enumeration(tmp_path):
    source_parent, destination_parent = _move_test_paths(tmp_path)
    source = source_parent / "source-tree"
    source.mkdir(mode=0o700)
    (source / "expected").write_bytes(b"expected")
    destination = destination_parent / "destination-tree"
    report = _archive_report(tmp_path, "bounded-tree-report")
    objects.archive_batch(
        ((source, destination),), report_directory=report
    )
    (destination / "extra-one").write_bytes(b"one")
    (destination / "extra-two").write_bytes(b"two")

    with pytest.raises(ValueError, match="bounds"):
        objects.verify_archive_report(report)


def test_verify_archive_report_rejects_reordered_moves(tmp_path):
    source_parent, destination_parent = _move_test_paths(tmp_path)
    first = source_parent / "first"
    second = source_parent / "second"
    first.write_bytes(b"first report evidence")
    second.write_bytes(b"second report evidence")
    report = _archive_report(tmp_path, "reordered-report")
    objects.archive_batch(
        (
            (first, destination_parent / "first"),
            (second, destination_parent / "second"),
        ),
        report_directory=report,
    )
    first_checkpoint = report / "checkpoint-0001.json"
    second_checkpoint = report / "checkpoint-0002.json"
    holding = tmp_path / "Archive-checkpoint-holding.json"
    first_checkpoint.rename(holding)
    second_checkpoint.rename(first_checkpoint)
    holding.rename(second_checkpoint)

    with pytest.raises(ValueError, match="chain|reordered"):
        objects.verify_archive_report(report)


@pytest.mark.parametrize("image", ["destination", "safety"])
def test_verify_archive_report_rejects_destination_and_safety_tamper(
        tmp_path, image):
    _source, destination, report, result = _committed_file_archive(
        tmp_path, image
    )
    target = (
        destination if image == "destination"
        else Path(result["moves"][0]["safety_copy"])
    )
    target.chmod(0o600)
    target.write_bytes(b"tampered archive evidenc")
    target.chmod(0o400 if image == "safety" else 0o600)

    with pytest.raises(ValueError, match="evidence|SHA-256|mismatch"):
        objects.verify_archive_report(report)


def test_stdout_failure_cannot_erase_committed_archive_authority(
        tmp_path, monkeypatch):
    source_parent, destination_parent = _move_test_paths(tmp_path)
    source = source_parent / "source"
    destination = destination_parent / "destination"
    source.write_bytes(b"stdout-independent evidence")
    report = _archive_report(tmp_path, "stdout-failure-report")

    def fail_stdout(*_args, **_kwargs):
        raise OSError("synthetic stdout failure")

    monkeypatch.setattr("builtins.print", fail_stdout)
    with pytest.raises(OSError, match="stdout failure"):
        objects.main([
            "archive-batch",
            "--report-directory", str(report),
            "--pair", str(source), str(destination),
        ])

    terminal = _report_document(report, "terminal.json")
    assert terminal["archive_state"] == "committed"
    assert destination.read_bytes() == b"stdout-independent evidence"
    assert objects.verify_archive_report(report)["moves_verified"] == 1


@pytest.mark.parametrize(
    "cut",
    [
        "sync-destination-content",
        "sync-destination-parent",
        "sync-source-parent",
        "before-checkpoint",
        "checkpoint-file",
        "checkpoint-directory",
        "after-checkpoint",
    ],
)
def test_sigkill_leaves_prepared_and_last_durable_checkpoint_prefix(
        tmp_path, cut):
    source_parent, destination_parent = _move_test_paths(tmp_path)
    source = source_parent / "source"
    destination = destination_parent / "destination"
    source.write_bytes(b"real SIGKILL archive evidence")
    report = _archive_report(tmp_path, f"sigkill-{cut}")
    child = r'''
import os
import signal
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
import publication_objects as objects

source = Path(sys.argv[2])
destination = Path(sys.argv[3])
report = Path(sys.argv[4])
cut = sys.argv[5]
source_identity = (source.stat().st_dev, source.stat().st_ino)
destination_parent_identity = (
    destination.parent.stat().st_dev, destination.parent.stat().st_ino
)
source_parent_identity = (
    source.parent.stat().st_dev, source.parent.stat().st_ino
)
native_rename = objects._load_atomic_no_replace_rename()
renamed = False

def load_recording_rename():
    def recording_rename(*args):
        global renamed
        native_rename(*args)
        renamed = True
    return recording_rename

objects._load_atomic_no_replace_rename = load_recording_rename
real_fsync = objects.os.fsync

def killing_fsync(fd):
    result = real_fsync(fd)
    if not renamed or not cut.startswith("sync-"):
        return result
    value = os.fstat(fd)
    identity = (value.st_dev, value.st_ino)
    labels = {
        source_identity: "sync-destination-content",
        destination_parent_identity: "sync-destination-parent",
        source_parent_identity: "sync-source-parent",
    }
    if labels.get(identity) == cut:
        os.kill(os.getpid(), signal.SIGKILL)
    return result

objects.os.fsync = killing_fsync
real_write = objects._ArchiveReportJournal.write

def killing_write(self, name, value):
    if name.startswith("checkpoint-") and cut == "before-checkpoint":
        os.kill(os.getpid(), signal.SIGKILL)
    if name.startswith("checkpoint-") and cut in (
            "checkpoint-file", "checkpoint-directory"):
        prior_fsync = objects.os.fsync
        checkpoint_syncs = [0]
        def checkpoint_fsync(fd):
            result = prior_fsync(fd)
            checkpoint_syncs[0] += 1
            if (cut == "checkpoint-file" and checkpoint_syncs[0] == 1
                    or cut == "checkpoint-directory"
                    and checkpoint_syncs[0] == 2):
                os.kill(os.getpid(), signal.SIGKILL)
            return result
        objects.os.fsync = checkpoint_fsync
        try:
            return real_write(self, name, value)
        finally:
            objects.os.fsync = prior_fsync
    result = real_write(self, name, value)
    if name.startswith("checkpoint-") and cut == "after-checkpoint":
        os.kill(os.getpid(), signal.SIGKILL)
    return result

objects._ArchiveReportJournal.write = killing_write
objects.archive_batch(
    ((source, destination),), report_directory=report
)
'''
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [
            sys.executable, "-c", child, str(TOOLS), str(source),
            str(destination), str(report), cut,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
        env=environment,
    )

    assert result.returncode == -signal.SIGKILL, result.stderr
    names = sorted(path.name for path in report.iterdir())
    assert "plan.json" in names
    assert "prepared.json" in names
    assert "terminal.json" not in names
    checkpoint_names = [
        name for name in names if name.startswith("checkpoint-")
    ]
    assert checkpoint_names == (
        ["checkpoint-0001.json"]
        if cut in (
            "checkpoint-file", "checkpoint-directory", "after-checkpoint"
        ) else []
    )
    prepared = _report_document(report, "prepared.json")
    if checkpoint_names:
        checkpoint = _report_document(report, checkpoint_names[0])
        assert checkpoint["previous_sha256"] == prepared["document_sha256"]
        assert checkpoint["move_index"] == 0
    assert not source.exists()
    assert destination.read_bytes() == b"real SIGKILL archive evidence"
    safety = next(destination_parent.glob(".trekdex-archive-safety-*"))
    assert safety.read_bytes() == b"real SIGKILL archive evidence"
    with pytest.raises(ValueError, match="missing"):
        objects.verify_archive_report(report)


def _empty_seal_registry(tmp_path: Path) -> Path:
    data_root = tmp_path / "seal-data"
    data_root.mkdir(mode=0o700)
    registry = data_root / "store_publication_proofs.json"
    registry.write_bytes(objects._pretty_json({
        "version": objects.REGISTRY_VERSION,
        "kind": objects.REGISTRY_KIND,
        "proofs": {},
    }))
    return registry


def test_archive_excludes_registry_seal_until_terminal_report(
        tmp_path, monkeypatch):
    registry = _empty_seal_registry(tmp_path)
    source_parent, destination_parent = _move_test_paths(tmp_path)
    source = source_parent / "source"
    destination = destination_parent / "destination"
    source.write_bytes(b"archive owns seal resources")
    report = _archive_report(tmp_path, "archive-excludes-seal-report")
    native_rename = objects._load_atomic_no_replace_rename()
    seal_process = None

    def load_contention_probe():
        def contention_probe(*args):
            nonlocal seal_process
            seal_process = subprocess.Popen(
                [
                    sys.executable, str(TOOLS / "publication_objects.py"),
                    "seal", str(registry),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
            time.sleep(0.2)
            assert seal_process.poll() is None
            native_rename(*args)
        return contention_probe

    monkeypatch.setattr(
        objects, "_load_atomic_no_replace_rename", load_contention_probe
    )
    resources = objects.tr.publication_registry_object_resources(registry)
    result = objects.archive_batch(
        ((source, destination),),
        lock_resources=resources,
        report_directory=report,
    )
    assert result["archive_state"] == "committed"
    assert seal_process is not None
    stdout, stderr = seal_process.communicate(timeout=10)
    assert seal_process.returncode == 0, stderr
    assert json.loads(stdout)["manifests"] == 0


def test_registry_seal_excludes_archive_for_full_seal_lifecycle(
        tmp_path, monkeypatch):
    registry = _empty_seal_registry(tmp_path)
    source_parent, destination_parent = _move_test_paths(tmp_path)
    source = source_parent / "source"
    destination = destination_parent / "destination"
    source.write_bytes(b"seal owns archive resources")
    report = _archive_report(tmp_path, "seal-excludes-archive-report")
    resources = objects.tr.publication_registry_object_resources(registry)
    real_locked_seal = objects._seal_registry_objects_locked
    archive_process = None

    def seal_with_contention(path):
        nonlocal archive_process
        archive_process = subprocess.Popen(
            [
                sys.executable, str(TOOLS / "publication_objects.py"),
                "archive-batch",
                "--report-directory", str(report),
                "--resource", str(resources[0]),
                "--resource", str(resources[1]),
                "--pair", str(source), str(destination),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        time.sleep(0.2)
        assert archive_process.poll() is None
        result = real_locked_seal(path)
        assert archive_process.poll() is None
        assert source.exists()
        assert not destination.exists()
        return result

    monkeypatch.setattr(
        objects, "_seal_registry_objects_locked", seal_with_contention
    )
    assert objects.seal_registry_objects(registry)["manifests"] == 0
    assert archive_process is not None
    stdout, stderr = archive_process.communicate(timeout=10)
    assert archive_process.returncode == 0, stderr
    assert json.loads(stdout)["archive_state"] == "committed"
    assert destination.read_bytes() == b"seal owns archive resources"
    assert objects.verify_archive_report(report)["moves_verified"] == 1


def _optional_archive_fixture(tmp_path: Path):
    live = tmp_path / "live"
    archive = tmp_path / "archive"
    live.mkdir(mode=0o700)
    archive.mkdir(mode=0o700)
    required_source = live / "required.bin"
    required_source.write_bytes(b"required archive evidence")
    required_destination = archive / "required.bin"
    optional_sources = (
        live / ".trekdex-publication-transactions",
        live / "publication-proof-objects",
    )
    optional_destinations = tuple(
        archive / source.name for source in optional_sources
    )
    return (
        required_source,
        required_destination,
        optional_sources,
        optional_destinations,
        tmp_path / "archive-report",
    )


def _rewrite_archive_document(report: Path, name: str, mutate) -> None:
    path = report / name
    path.chmod(0o600)
    document = _report_document(report, name)
    document.pop("document_sha256")
    mutate(document)
    path.write_bytes(objects._pretty_json(
        objects._hashed_archive_report_document(document)
    ))
    path.chmod(0o400)


def _wait_for_path(path: Path, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while not path.exists():
        if time.monotonic() >= deadline:
            raise AssertionError(f"timed out waiting for {path}")
        time.sleep(0.01)


def _run_test_git(repo: Path, *arguments: str) -> str:
    result = subprocess.run(
        [objects._git_executable(), "-C", str(repo), *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
        env={**os.environ, "PATH": "/usr/local/bin:/usr/bin:/bin"},
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_generation_1_restore_contract_includes_reviewed_builder_blob():
    assert objects.GENERATION_1_RESTORE_COMMIT == (
        "140f8c935864ca5fe9b1070256193eee807691e3"
    )
    assert objects.GENERATION_1_RESTORE_TARGETS == (
        (
            "scripts/parking-adjud/data/co_verdicts_osm.json",
            "44f3ac4c6793e1be66e301f6ee46875de92b8e1952351c607ae4e658d861a0cb",
        ),
        (
            "scripts/parking-adjud/data/co_verdicts_osm_publication_floor.json",
            "5b5d134bec54d6e3e4862cea31231e1701b5d116c4866819128b6e59b40f3cad",
        ),
        (
            "scripts/parking-adjud/publication-trust-root-v1.json",
            "43db143447d90f506d42b273c425e0d048e5580e605ac211750114ba350dfd85",
        ),
        (
            "scripts/build-parking-verdicts.py",
            "a2ab1d97bb949422e539c7769223977090c4a5d449ce71e8e22b00764c9e3fdb",
        ),
    )
    assert objects.GENERATION_1_ARCHIVE_SOURCE_PATHS == (
        "scripts/parking-adjud/data/co_verdicts_osm.json",
        "scripts/parking-adjud/data/co_verdicts_osm_publication_proofs.json",
        "scripts/parking-adjud/data/co_verdicts_osm_publication_floor.json",
        "scripts/parking-adjud/publication-trust-root-v1.json",
        "scripts/build-parking-verdicts.py",
    )


def _generation_1_restore_fixture(
        tmp_path: Path, monkeypatch, *, optional_present_indexes=(),
        builder_pin_override: str | None = None,
        omitted_archive_paths: tuple[str, ...] = (),
):
    repo = tmp_path / "repo"
    repo.mkdir(mode=0o700)
    _run_test_git(repo, "init", "-q")
    _run_test_git(repo, "config", "user.email", "synthetic@example.invalid")
    _run_test_git(repo, "config", "user.name", "Synthetic Test")
    root_payload = b'{"generation":1,"parent_root_sha256":null}\n'
    root_sha256 = hashlib.sha256(root_payload).hexdigest()
    builder_pin = (
        root_sha256 if builder_pin_override is None else builder_pin_override
    )
    generation_payloads = (
        b'{"generation":"store-before-image"}\n',
        b'{"generation":"floor-before-image"}\n',
        root_payload,
        (
            "PUBLICATION_TRUST_ROOT_SHA256 = (\n"
            f'    "{builder_pin}"\n'
            ")\n"
        ).encode(),
    )
    target_contract = []
    for (relative, _production_sha256), payload in zip(
            objects.GENERATION_1_RESTORE_TARGETS, generation_payloads):
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        target_contract.append((relative, hashlib.sha256(payload).hexdigest()))
    _run_test_git(
        repo, "add", "--", *(relative for relative, _sha in target_contract)
    )
    _run_test_git(repo, "commit", "-q", "-m", "synthetic generation one")
    commit = _run_test_git(repo, "rev-parse", "HEAD")
    monkeypatch.setattr(
        objects, "GENERATION_1_RESTORE_TARGETS", tuple(target_contract)
    )
    monkeypatch.setattr(objects, "GENERATION_1_RESTORE_COMMIT", commit)

    current_payloads = {}
    for relative, _sha256 in target_contract:
        path = repo / relative
        payload = f"current image for {relative}\n".encode()
        path.write_bytes(payload)
        current_payloads[relative] = payload
    legacy = repo / objects.GENERATION_1_LEGACY_PROOF_PATH
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_bytes(b"untrusted legacy proof bytes; never execute\n")

    archive_root = tmp_path / "Archive" / "synthetic-generation-1"
    archive_root.mkdir(parents=True, mode=0o700)
    required_pairs = []
    for relative in objects.GENERATION_1_ARCHIVE_SOURCE_PATHS:
        if relative in omitted_archive_paths:
            continue
        destination = archive_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        required_pairs.append((repo / relative, destination))
    optional_pairs = []
    for index, relative in enumerate(objects.GENERATION_1_OPTIONAL_PATHS):
        source = repo / relative
        if index in optional_present_indexes:
            source.mkdir(parents=True, mode=0o700)
            (source / "synthetic-leaf").write_bytes(
                f"optional tree {index}\n".encode()
            )
        destination = archive_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        optional_pairs.append((source, destination))
    report = archive_root / "archive-report"
    objects.archive_batch(
        required_pairs,
        optional_pairs=optional_pairs,
        report_directory=report,
    )
    return {
        "repo": repo,
        "commit": commit,
        "report": report,
        "generation_payloads": generation_payloads,
        "current_payloads": current_payloads,
        "legacy": legacy,
        "optional_sources": tuple(source for source, _destination in optional_pairs),
    }


@pytest.mark.parametrize("interface", ["api", "cli"])
def test_generation_1_restore_is_closed_atomic_and_same_report_leased(
        tmp_path, monkeypatch, capsys, interface):
    fixture = _generation_1_restore_fixture(tmp_path, monkeypatch)
    if interface == "api":
        result = objects.restore_generation_1(
            fixture["report"],
            repo_root=fixture["repo"],
            source_commit=fixture["commit"],
        )
    else:
        assert objects.main([
            "restore-generation-1",
            "--report-directory", str(fixture["report"]),
            "--repo-root", str(fixture["repo"]),
            "--source-commit", fixture["commit"],
        ]) == 0
        result = json.loads(capsys.readouterr().out)

    assert result["generation_1_restore_state"] == "committed"
    assert result["restored_targets_verified"] == 4
    for ((relative, expected_sha256), expected_bytes) in zip(
            objects.GENERATION_1_RESTORE_TARGETS,
            fixture["generation_payloads"],
    ):
        path = fixture["repo"] / relative
        assert path.read_bytes() == expected_bytes
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected_sha256
    assert not fixture["legacy"].exists()
    assert all(not path.exists() for path in fixture["optional_sources"])
    prepared = _report_document(
        fixture["report"], "generation-1-restore-prepared.json"
    )
    terminal = _report_document(
        fixture["report"], "generation-1-restore-terminal.json"
    )
    assert prepared["state"] == "RESTORE_PREPARED"
    assert terminal["state"] == "RESTORE_COMMITTED"
    assert len(prepared["targets"]) == 4
    assert len(terminal["restored_targets"]) == 4
    assert [target["path"] for target in prepared["targets"]] == [
        str(fixture["repo"] / relative)
        for relative, _sha256 in objects.GENERATION_1_RESTORE_TARGETS
    ]
    assert terminal["restored_targets"] == [
        {
            "path": target["path"],
            "length": target["length"],
            "sha256": target["sha256"],
        }
        for target in prepared["targets"]
    ]
    assert terminal["prepared_sha256"] == prepared["document_sha256"]
    assert objects.verify_archive_report(fixture["report"])[
        "generation_1_restore_state"
    ] == "committed"


def test_generation_1_restore_rejects_unpinned_commit_without_report_mutation(
        tmp_path, monkeypatch):
    fixture = _generation_1_restore_fixture(tmp_path, monkeypatch)
    before = {
        path.name: path.read_bytes() for path in fixture["report"].iterdir()
    }
    with pytest.raises(ValueError, match="explicitly pinned commit"):
        objects.restore_generation_1(
            fixture["report"],
            repo_root=fixture["repo"],
            source_commit="0" * 40,
        )
    assert {
        path.name: path.read_bytes() for path in fixture["report"].iterdir()
    } == before
    assert all(
        not (fixture["repo"] / relative).exists()
        for relative, _sha256 in objects.GENERATION_1_RESTORE_TARGETS
    )


def test_generation_1_restore_rejects_wrong_reviewed_blob_before_target_write(
        tmp_path, monkeypatch):
    fixture = _generation_1_restore_fixture(tmp_path, monkeypatch)
    targets = list(objects.GENERATION_1_RESTORE_TARGETS)
    targets[0] = (targets[0][0], "0" * 64)
    monkeypatch.setattr(objects, "GENERATION_1_RESTORE_TARGETS", tuple(targets))
    with pytest.raises(
            objects.Generation1RestoreError,
            match="differs from the reviewed contract") as failure:
        objects.restore_generation_1(
            fixture["report"],
            repo_root=fixture["repo"],
            source_commit=fixture["commit"],
        )
    assert (
        fixture["report"] / "generation-1-restore-prepared.json"
    ).is_file()
    assert failure.value.report["state"] == "RESTORE_NOT_COMMITTED"
    assert failure.value.report["restored_targets"] == []
    assert all(
        not (fixture["repo"] / relative).exists()
        for relative, _sha256 in targets
    )


def test_generation_1_restore_rejects_wrong_builder_hash_before_target_write(
        tmp_path, monkeypatch):
    fixture = _generation_1_restore_fixture(tmp_path, monkeypatch)
    targets = list(objects.GENERATION_1_RESTORE_TARGETS)
    assert targets[-1][0] == objects.GENERATION_1_BUILDER_PATH
    targets[-1] = (targets[-1][0], "0" * 64)
    monkeypatch.setattr(objects, "GENERATION_1_RESTORE_TARGETS", tuple(targets))

    with pytest.raises(
            objects.Generation1RestoreError,
            match="differs from the reviewed contract") as failure:
        objects.restore_generation_1(
            fixture["report"],
            repo_root=fixture["repo"],
            source_commit=fixture["commit"],
        )

    prepared = _report_document(
        fixture["report"], "generation-1-restore-prepared.json"
    )
    assert len(prepared["targets"]) == 4
    assert prepared["targets"][-1]["sha256"] == "0" * 64
    assert failure.value.report["state"] == "RESTORE_NOT_COMMITTED"
    assert failure.value.report["restored_targets"] == []
    assert len(failure.value.report["unrestored_targets"]) == 4
    assert all(
        not (fixture["repo"] / relative).exists()
        for relative, _sha256 in targets
    )


def test_generation_1_restore_rejects_builder_pin_that_differs_from_root(
        tmp_path, monkeypatch):
    fixture = _generation_1_restore_fixture(
        tmp_path, monkeypatch, builder_pin_override="0" * 64
    )

    with pytest.raises(
            objects.Generation1RestoreError,
            match="literal pin does not equal") as failure:
        objects.restore_generation_1(
            fixture["report"],
            repo_root=fixture["repo"],
            source_commit=fixture["commit"],
        )

    prepared = _report_document(
        fixture["report"], "generation-1-restore-prepared.json"
    )
    assert len(prepared["targets"]) == 4
    assert all(Path(target["stage"]).is_file() for target in prepared["targets"])
    assert failure.value.report["state"] == "RESTORE_NOT_COMMITTED"
    assert failure.value.report["restored_targets"] == []
    assert failure.value.report["unrestored_targets"] == [
        {
            "path": target["path"],
            "length": target["length"],
            "sha256": target["sha256"],
        }
        for target in prepared["targets"]
    ]


def test_generation_1_restore_rejects_missing_builder_archive_pair(
        tmp_path, monkeypatch):
    fixture = _generation_1_restore_fixture(
        tmp_path,
        monkeypatch,
        omitted_archive_paths=(objects.GENERATION_1_BUILDER_PATH,),
    )
    before = {
        path.name: path.read_bytes() for path in fixture["report"].iterdir()
    }

    with pytest.raises(ValueError, match="required sources do not match"):
        objects.restore_generation_1(
            fixture["report"],
            repo_root=fixture["repo"],
            source_commit=fixture["commit"],
        )

    assert {
        path.name: path.read_bytes() for path in fixture["report"].iterdir()
    } == before
    builder = fixture["repo"] / objects.GENERATION_1_BUILDER_PATH
    assert builder.read_bytes() == fixture["current_payloads"][
        objects.GENERATION_1_BUILDER_PATH
    ]


def test_generation_1_restore_refuses_arbitrary_target_even_if_hash_pinned(
        tmp_path, monkeypatch):
    fixture = _generation_1_restore_fixture(tmp_path, monkeypatch)
    targets = list(objects.GENERATION_1_RESTORE_TARGETS)
    arbitrary = "scripts/arbitrary-restore-target.py"
    targets[-1] = (arbitrary, targets[-1][1])
    monkeypatch.setattr(objects, "GENERATION_1_RESTORE_TARGETS", tuple(targets))
    before = {
        path.name: path.read_bytes() for path in fixture["report"].iterdir()
    }

    with pytest.raises(
            ValueError, match="targets or archive sources do not match"):
        objects.restore_generation_1(
            fixture["report"],
            repo_root=fixture["repo"],
            source_commit=fixture["commit"],
        )

    assert {
        path.name: path.read_bytes() for path in fixture["report"].iterdir()
    } == before
    assert not (fixture["repo"] / arbitrary).exists()


def test_generation_1_restore_accepts_both_optional_present_states(
        tmp_path, monkeypatch):
    fixture = _generation_1_restore_fixture(
        tmp_path, monkeypatch, optional_present_indexes=(0, 1)
    )
    prepared_archive = _report_document(fixture["report"], "prepared.json")
    assert [
        value["resolution"]
        for value in prepared_archive["optional_resolutions"]
    ] == ["present", "present"]
    result = objects.restore_generation_1(
        fixture["report"],
        repo_root=fixture["repo"],
        source_commit=fixture["commit"],
    )
    assert result["generation_1_restore_state"] == "committed"
    assert all(not path.exists() for path in fixture["optional_sources"])


def test_generation_1_restore_partial_failure_is_durable_and_no_clobber(
        tmp_path, monkeypatch):
    fixture = _generation_1_restore_fixture(tmp_path, monkeypatch)
    native_rename = objects._load_atomic_no_replace_rename()
    calls = 0

    def load_failing_restore_rename():
        def fail_second(*arguments):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("synthetic second restore rename failure")
            native_rename(*arguments)
        return fail_second

    monkeypatch.setattr(
        objects, "_load_atomic_no_replace_rename", load_failing_restore_rename
    )
    with pytest.raises(objects.Generation1RestoreError) as failure:
        objects.restore_generation_1(
            fixture["report"],
            repo_root=fixture["repo"],
            source_commit=fixture["commit"],
        )
    terminal = failure.value.report
    assert terminal == _report_document(
        fixture["report"], "generation-1-restore-terminal.json"
    )
    assert terminal["state"] == "RESTORE_PARTIALLY_COMMITTED"
    assert terminal["blind_retry_forbidden"] is True
    assert len(terminal["restored_targets"]) == 1
    assert len(terminal["unrestored_targets"]) == 3
    first_relative = objects.GENERATION_1_RESTORE_TARGETS[0][0]
    assert (fixture["repo"] / first_relative).read_bytes() == (
        fixture["generation_payloads"][0]
    )
    for relative, _sha256 in objects.GENERATION_1_RESTORE_TARGETS[1:]:
        assert not (fixture["repo"] / relative).exists()
    with pytest.raises(ValueError, match="terminal state is not committed"):
        objects.verify_archive_report(fixture["report"])


def test_generation_1_restore_cli_emits_durable_failure_terminal(
        tmp_path, monkeypatch, capsys):
    fixture = _generation_1_restore_fixture(tmp_path, monkeypatch)

    def load_failing_rename():
        def fail(*_arguments):
            raise OSError("synthetic CLI restore rename failure")
        return fail

    monkeypatch.setattr(objects, "_load_atomic_no_replace_rename", load_failing_rename)
    with pytest.raises(SystemExit) as failure:
        objects.main([
            "restore-generation-1",
            "--report-directory", str(fixture["report"]),
            "--repo-root", str(fixture["repo"]),
            "--source-commit", fixture["commit"],
        ])
    assert failure.value.code == 2
    terminal = json.loads(capsys.readouterr().out)
    assert terminal == _report_document(
        fixture["report"], "generation-1-restore-terminal.json"
    )
    assert terminal["state"] == "RESTORE_NOT_COMMITTED"
    assert terminal["blind_retry_forbidden"] is True
    assert all(
        not (fixture["repo"] / relative).exists()
        for relative, _sha256 in objects.GENERATION_1_RESTORE_TARGETS
    )


def test_generation_1_restore_post_rename_failure_is_indeterminate(
        tmp_path, monkeypatch):
    fixture = _generation_1_restore_fixture(tmp_path, monkeypatch)
    first_target = fixture["repo"] / objects.GENERATION_1_RESTORE_TARGETS[0][0]
    real_measure = objects._measure_archive_entry

    def fail_first_restored_measure(
            parent_fd, name, path, kind, *, require_safety_modes,
            expected_evidence=None):
        if Path(path) == first_target:
            raise OSError("synthetic restored target verification failure")
        return real_measure(
            parent_fd, name, path, kind,
            require_safety_modes=require_safety_modes,
            expected_evidence=expected_evidence,
        )

    monkeypatch.setattr(objects, "_measure_archive_entry", fail_first_restored_measure)
    with pytest.raises(objects.Generation1RestoreError) as failure:
        objects.restore_generation_1(
            fixture["report"],
            repo_root=fixture["repo"],
            source_commit=fixture["commit"],
        )
    terminal = failure.value.report
    assert terminal["state"] == "RESTORE_COMMITTED_INDETERMINATE"
    assert terminal["restored_targets"] == []
    assert terminal["failed_target"]["path"] == str(first_target)
    assert terminal["failed_target"]["rename_may_have_committed"] is True
    assert first_target.read_bytes() == fixture["generation_payloads"][0]


def test_generation_1_restore_sigkill_leaves_prepared_without_terminal(
        tmp_path, monkeypatch):
    fixture = _generation_1_restore_fixture(tmp_path, monkeypatch)
    child = r'''
import json
import os
import signal
import sys

sys.path.insert(0, sys.argv[1])
import publication_objects as objects

objects.GENERATION_1_RESTORE_COMMIT = sys.argv[5]
objects.GENERATION_1_RESTORE_TARGETS = tuple(
    tuple(value) for value in json.loads(sys.argv[6])
)

def kill_before_stage(*_args, **_kwargs):
    os.kill(os.getpid(), signal.SIGKILL)

objects._write_generation_1_stage = kill_before_stage
objects.restore_generation_1(
    sys.argv[2], repo_root=sys.argv[3], source_commit=sys.argv[4]
)
'''
    process = subprocess.run(
        [
            sys.executable, "-c", child, str(TOOLS), str(fixture["report"]),
            str(fixture["repo"]), fixture["commit"], fixture["commit"],
            json.dumps(objects.GENERATION_1_RESTORE_TARGETS),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
        env={
            **os.environ,
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )
    assert process.returncode == -signal.SIGKILL, process.stderr
    assert (
        fixture["report"] / "generation-1-restore-prepared.json"
    ).is_file()
    assert not (
        fixture["report"] / "generation-1-restore-terminal.json"
    ).exists()
    assert all(
        not (fixture["repo"] / relative).exists()
        for relative, _sha256 in objects.GENERATION_1_RESTORE_TARGETS
    )
    with pytest.raises(ValueError, match="missing PREPARED or terminal"):
        objects.verify_archive_report(fixture["report"])


def test_generation_1_restore_crash_after_fourth_stage_refuses_second_use(
        tmp_path, monkeypatch):
    fixture = _generation_1_restore_fixture(tmp_path, monkeypatch)
    assert len(objects.GENERATION_1_RESTORE_TARGETS) == 4
    child = r'''
import json
import os
import signal
import sys

sys.path.insert(0, sys.argv[1])
import publication_objects as objects

objects.GENERATION_1_RESTORE_COMMIT = sys.argv[5]
objects.GENERATION_1_RESTORE_TARGETS = tuple(
    tuple(value) for value in json.loads(sys.argv[6])
)
real_write = objects._write_generation_1_stage
writes = 0

def kill_after_fourth_write(*args, **kwargs):
    global writes
    result = real_write(*args, **kwargs)
    writes += 1
    if writes == 4:
        os.kill(os.getpid(), signal.SIGKILL)
    return result

objects._write_generation_1_stage = kill_after_fourth_write
objects.restore_generation_1(
    sys.argv[2], repo_root=sys.argv[3], source_commit=sys.argv[4]
)
'''
    process = subprocess.run(
        [
            sys.executable, "-c", child, str(TOOLS), str(fixture["report"]),
            str(fixture["repo"]), fixture["commit"], fixture["commit"],
            json.dumps(objects.GENERATION_1_RESTORE_TARGETS),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
        env={
            **os.environ,
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )
    assert process.returncode == -signal.SIGKILL, process.stderr
    prepared = _report_document(
        fixture["report"], "generation-1-restore-prepared.json"
    )
    assert len(prepared["targets"]) == 4
    stage_bytes = {
        target["stage"]: Path(target["stage"]).read_bytes()
        for target in prepared["targets"]
    }
    assert all(
        not (fixture["repo"] / relative).exists()
        for relative, _sha256 in objects.GENERATION_1_RESTORE_TARGETS
    )
    assert not (
        fixture["report"] / "generation-1-restore-terminal.json"
    ).exists()
    report_before_second_use = {
        path.name: path.read_bytes() for path in fixture["report"].iterdir()
    }

    with pytest.raises(ValueError, match="missing PREPARED or terminal"):
        objects.restore_generation_1(
            fixture["report"],
            repo_root=fixture["repo"],
            source_commit=fixture["commit"],
        )

    assert {
        path.name: path.read_bytes() for path in fixture["report"].iterdir()
    } == report_before_second_use
    assert {
        stage: Path(stage).read_bytes() for stage in stage_bytes
    } == stage_bytes


def test_generation_1_restore_blocks_writer_until_terminal_and_lease_release(
        tmp_path, monkeypatch):
    fixture = _generation_1_restore_fixture(tmp_path, monkeypatch)
    source = fixture["optional_sources"][0]
    attempted = tmp_path / "restore-writer-attempted"
    ready = tmp_path / "restore-writer-ready"
    writer = None
    real_require_absent = objects._require_archive_path_absent

    def require_absent(path, label):
        nonlocal writer
        result = real_require_absent(path, label)
        prepared = fixture["report"] / "generation-1-restore-prepared.json"
        terminal = fixture["report"] / "generation-1-restore-terminal.json"
        if (writer is None and Path(path) == source
                and label == "generation-1 required absent path"
                and prepared.exists() and not terminal.exists()):
            writer = subprocess.Popen(
                [
                    sys.executable, "-c", _COOPERATING_OPTIONAL_WRITER,
                    str(TOOLS), str(source), str(attempted), str(ready), "-",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env={
                    **os.environ,
                    "PATH": "/usr/local/bin:/usr/bin:/bin",
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
            )
            _wait_for_path(attempted)
            time.sleep(0.1)
            assert writer.poll() is None
            assert not terminal.exists()
            assert not source.exists()
        return result

    monkeypatch.setattr(objects, "_require_archive_path_absent", require_absent)
    result = objects.restore_generation_1(
        fixture["report"],
        repo_root=fixture["repo"],
        source_commit=fixture["commit"],
    )
    assert result["generation_1_restore_state"] == "committed"
    assert writer is not None
    stdout, stderr = writer.communicate(timeout=10)
    assert writer.returncode == 0, (stdout, stderr)
    assert (
        fixture["report"] / "generation-1-restore-terminal.json"
    ).is_file()
    assert ready.is_file()
    assert (source / "writer-leaf").read_bytes() == (
        b"cooperating writer evidence"
    )
    with pytest.raises(ValueError, match="must remain absent"):
        objects.verify_archive_report(fixture["report"])


def test_generation_1_restore_records_refuse_second_use_without_clobber(
        tmp_path, monkeypatch):
    fixture = _generation_1_restore_fixture(tmp_path, monkeypatch)
    objects.restore_generation_1(
        fixture["report"],
        repo_root=fixture["repo"],
        source_commit=fixture["commit"],
    )
    before = {
        path.name: path.read_bytes() for path in fixture["report"].iterdir()
    }
    with pytest.raises(ValueError, match="already exists"):
        objects.restore_generation_1(
            fixture["report"],
            repo_root=fixture["repo"],
            source_commit=fixture["commit"],
        )
    assert {
        path.name: path.read_bytes() for path in fixture["report"].iterdir()
    } == before


def _assert_required_recovery_partition(
        terminal: dict, required_sources: tuple[Path, ...],
) -> None:
    required = {str(path) for path in required_sources}
    completed = {
        move["source"] for move in terminal["completed_moves"]
        if move["source"] in required
    }
    failed = (
        {terminal["failed_move"]["source"]}
        if terminal["failed_move"] is not None else set()
    )
    unattempted = {
        move["source"] for move in terminal["unattempted_moves"]
    }
    assert completed.isdisjoint(failed)
    assert completed.isdisjoint(unattempted)
    assert failed.isdisjoint(unattempted)
    assert completed | failed | unattempted == required


def _recovery_partition_fixture(
        tmp_path: Path, *, present_optional_indexes=(),
):
    source_parent, destination_parent = _move_test_paths(tmp_path)
    required_sources = tuple(
        source_parent / f"required-{index}" for index in range(2)
    )
    for index, source in enumerate(required_sources):
        source.write_bytes(f"required recovery {index}".encode())
    required_destinations = tuple(
        destination_parent / source.name for source in required_sources
    )
    optional_sources = tuple(
        source_parent / f"optional-{index}" for index in range(3)
    )
    for index in present_optional_indexes:
        optional_sources[index].mkdir(mode=0o700)
        (optional_sources[index] / "leaf").write_bytes(
            f"optional recovery {index}".encode()
        )
    optional_destinations = tuple(
        destination_parent / source.name for source in optional_sources
    )
    return (
        required_sources,
        required_destinations,
        optional_sources,
        optional_destinations,
        tmp_path / "recovery-partition-report",
    )


def _valid_test_report_document(report: Path, name: str) -> dict | None:
    try:
        document = _report_document(report, name)
    except (OSError, ValueError):
        return None
    if not isinstance(document, dict):
        return None
    unhashed = dict(document)
    claimed = unhashed.pop("document_sha256", None)
    if (not objects._valid_sha256(claimed)
            or hashlib.sha256(
                objects._canonical_json(unhashed)
            ).hexdigest() != claimed):
        return None
    return document


def _failure_disk_checkpoint_prefix(
        report: Path,
) -> tuple[dict, dict | None, str, list[dict], set[int]]:
    plan = _valid_test_report_document(report, "plan.json")
    assert plan is not None
    prepared = _valid_test_report_document(report, "prepared.json")
    latest = plan["document_sha256"]
    moves = []
    completed_optional_indexes: set[int] = set()
    if (prepared is None
            or prepared.get("plan_sha256") != plan["document_sha256"]
            or len(prepared.get("moves", ())) != len(plan["moves"])
            or len(prepared.get("optional_resolutions", ()))
            != len(plan["optional_moves"])):
        return plan, None, latest, moves, completed_optional_indexes

    latest = prepared["document_sha256"]
    required_complete = True
    for index in range(len(plan["moves"])):
        checkpoint = _valid_test_report_document(
            report, f"checkpoint-{index + 1:04d}.json"
        )
        if (checkpoint is None
                or checkpoint.get("kind") != objects._ARCHIVE_CHECKPOINT_KIND
                or checkpoint.get("move_index") != index
                or checkpoint.get("plan_sha256")
                != plan["document_sha256"]
                or checkpoint.get("prepared_sha256")
                != prepared["document_sha256"]
                or checkpoint.get("previous_sha256") != latest
                or not isinstance(checkpoint.get("move"), dict)):
            required_complete = False
            break
        moves.append(checkpoint["move"])
        latest = checkpoint["document_sha256"]

    if required_complete:
        for index in range(len(plan["optional_moves"])):
            checkpoint = _valid_test_report_document(
                report, f"optional-checkpoint-{index + 1:04d}.json"
            )
            resolution = (
                checkpoint.get("resolution")
                if checkpoint is not None else None
            )
            if (checkpoint is None
                    or checkpoint.get("kind")
                    != objects._ARCHIVE_OPTIONAL_CHECKPOINT_KIND
                    or checkpoint.get("optional_index") != index
                    or checkpoint.get("plan_sha256")
                    != plan["document_sha256"]
                    or checkpoint.get("prepared_sha256")
                    != prepared["document_sha256"]
                    or checkpoint.get("previous_sha256") != latest
                    or not isinstance(resolution, dict)):
                break
            completed_optional_indexes.add(index)
            if resolution.get("resolution") == "present":
                assert isinstance(resolution.get("move"), dict)
                moves.append(resolution["move"])
            else:
                assert resolution.get("resolution") == "absent"
            latest = checkpoint["document_sha256"]
    return plan, prepared, latest, moves, completed_optional_indexes


def _assert_optional_recovery_partition(
        terminal: dict, report: Path,
        optional_sources: tuple[Path, ...], *,
        completed_indexes: set[int], failed_index: int | None,
) -> None:
    (
        plan,
        prepared,
        latest,
        disk_moves,
        disk_completed_indexes,
    ) = _failure_disk_checkpoint_prefix(report)
    assert terminal["plan_sha256"] == plan["document_sha256"]
    assert terminal["prepared_sha256"] == (
        prepared["document_sha256"] if prepared is not None else None
    )
    assert terminal["latest_checkpoint_sha256"] == latest
    assert terminal["completed_move_count"] == len(disk_moves)
    assert terminal["completed_moves"] == disk_moves
    assert disk_completed_indexes == completed_indexes

    indexes_by_source = {
        str(source): index for index, source in enumerate(optional_sources)
    }
    failed_indexes = (
        {indexes_by_source[terminal["failed_optional_move"]["source"]]}
        if terminal["failed_optional_move"] is not None else set()
    )
    unresolved_indexes = {
        indexes_by_source[move["source"]]
        for move in terminal["unresolved_optional_moves"]
    }
    expected_failed = {failed_index} if failed_index is not None else set()
    all_indexes = set(range(len(optional_sources)))
    reported_completed = all_indexes - failed_indexes - unresolved_indexes
    assert failed_indexes == expected_failed
    assert reported_completed == completed_indexes
    assert reported_completed.isdisjoint(failed_indexes)
    assert reported_completed.isdisjoint(unresolved_indexes)
    assert failed_indexes.isdisjoint(unresolved_indexes)
    assert reported_completed | failed_indexes | unresolved_indexes == all_indexes
    if failed_index is not None:
        assert set(range(failed_index + 1, len(optional_sources))).issubset(
            unresolved_indexes
        )


@pytest.mark.parametrize("failed_optional_index", [0, 1, 2])
def test_optional_classification_failure_partitions_first_middle_and_last(
        tmp_path, monkeypatch, failed_optional_index):
    (
        required_sources,
        required_destinations,
        optional_sources,
        optional_destinations,
        report,
    ) = _recovery_partition_fixture(tmp_path)
    real_open = objects._open_archive_move_plan
    next_optional_index = 0

    def fail_classification(source, destination, safety, **kwargs):
        nonlocal next_optional_index
        if kwargs.get("source_may_be_absent"):
            current = next_optional_index
            next_optional_index += 1
            if current == failed_optional_index:
                raise OSError("synthetic indexed optional classification failure")
        return real_open(source, destination, safety, **kwargs)

    monkeypatch.setattr(objects, "_open_archive_move_plan", fail_classification)
    with pytest.raises(objects.ArchiveBatchError) as failure:
        objects.archive_batch(
            tuple(zip(required_sources, required_destinations)),
            optional_pairs=tuple(zip(optional_sources, optional_destinations)),
            report_directory=report,
        )
    terminal = failure.value.report
    assert terminal == _report_document(report, "terminal.json")
    assert terminal["failure"]["phase"] == "optional-classification"
    _assert_required_recovery_partition(terminal, required_sources)
    assert terminal["completed_moves"] == []
    _assert_optional_recovery_partition(
        terminal,
        report,
        optional_sources,
        completed_indexes=set(),
        failed_index=failed_optional_index,
    )


@pytest.mark.parametrize("failed_optional_index", [0, 1, 2])
def test_optional_safety_failure_partitions_first_middle_and_last(
        tmp_path, monkeypatch, failed_optional_index):
    (
        required_sources,
        required_destinations,
        optional_sources,
        optional_destinations,
        report,
    ) = _recovery_partition_fixture(
        tmp_path, present_optional_indexes=(0, 1, 2)
    )
    real_copy = objects._create_verified_safety_copy

    def fail_safety_copy(plan):
        if plan.source_path == optional_sources[failed_optional_index]:
            raise OSError("synthetic indexed optional safety-copy failure")
        return real_copy(plan)

    monkeypatch.setattr(objects, "_create_verified_safety_copy", fail_safety_copy)
    with pytest.raises(objects.ArchiveBatchError) as failure:
        objects.archive_batch(
            tuple(zip(required_sources, required_destinations)),
            optional_pairs=tuple(zip(optional_sources, optional_destinations)),
            report_directory=report,
        )
    terminal = failure.value.report
    assert terminal["failure"]["phase"] == "verified-safety-copy"
    _assert_required_recovery_partition(terminal, required_sources)
    _assert_optional_recovery_partition(
        terminal,
        report,
        optional_sources,
        completed_indexes=set(),
        failed_index=failed_optional_index,
    )


@pytest.mark.parametrize("failed_optional_index", [0, 1, 2])
def test_prepared_absence_failure_partitions_first_middle_and_last(
        tmp_path, monkeypatch, failed_optional_index):
    (
        required_sources,
        required_destinations,
        optional_sources,
        optional_destinations,
        report,
    ) = _recovery_partition_fixture(tmp_path)
    real_require = objects._require_optional_absent

    def fail_prepared_absence(plan, phase):
        if (phase == "before PREPARED"
                and plan.source_path == optional_sources[failed_optional_index]):
            raise OSError("synthetic indexed PREPARED absence failure")
        return real_require(plan, phase)

    monkeypatch.setattr(
        objects, "_require_optional_absent", fail_prepared_absence
    )
    with pytest.raises(objects.ArchiveBatchError) as failure:
        objects.archive_batch(
            tuple(zip(required_sources, required_destinations)),
            optional_pairs=tuple(zip(optional_sources, optional_destinations)),
            report_directory=report,
        )
    terminal = failure.value.report
    assert terminal["failure"]["phase"] == "prepared-optional-absence"
    _assert_optional_recovery_partition(
        terminal,
        report,
        optional_sources,
        completed_indexes=set(),
        failed_index=failed_optional_index,
    )


def test_prepared_report_write_failure_leaves_every_optional_unresolved(
        tmp_path, monkeypatch):
    (
        required_sources,
        required_destinations,
        optional_sources,
        optional_destinations,
        report,
    ) = _recovery_partition_fixture(tmp_path)
    real_write = objects._ArchiveReportJournal.write

    def fail_prepared_write(self, name, value):
        if name == "prepared.json":
            raise OSError("synthetic PREPARED report-write failure")
        return real_write(self, name, value)

    monkeypatch.setattr(
        objects._ArchiveReportJournal, "write", fail_prepared_write
    )
    with pytest.raises(objects.ArchiveBatchError) as failure:
        objects.archive_batch(
            tuple(zip(required_sources, required_destinations)),
            optional_pairs=tuple(zip(optional_sources, optional_destinations)),
            report_directory=report,
        )
    terminal = failure.value.report
    assert terminal["failure"]["phase"] == "prepared-report"
    _assert_optional_recovery_partition(
        terminal,
        report,
        optional_sources,
        completed_indexes=set(),
        failed_index=None,
    )


def test_required_checkpoint_failure_uses_only_disk_contiguous_prefix(
        tmp_path, monkeypatch):
    (
        required_sources,
        required_destinations,
        optional_sources,
        optional_destinations,
        report,
    ) = _recovery_partition_fixture(tmp_path)
    real_write = objects._ArchiveReportJournal.write

    def fail_second_required_checkpoint(self, name, value):
        if name == "checkpoint-0002.json":
            raise OSError("synthetic second required checkpoint failure")
        return real_write(self, name, value)

    monkeypatch.setattr(
        objects._ArchiveReportJournal,
        "write",
        fail_second_required_checkpoint,
    )
    with pytest.raises(objects.ArchiveBatchError) as failure:
        objects.archive_batch(
            tuple(zip(required_sources, required_destinations)),
            optional_pairs=tuple(zip(optional_sources, optional_destinations)),
            report_directory=report,
        )
    terminal = failure.value.report
    assert terminal["failure"]["phase"] == "checkpoint"
    _assert_required_recovery_partition(terminal, required_sources)
    assert [move["source"] for move in terminal["completed_moves"]] == [
        str(required_sources[0])
    ]
    assert terminal["failed_move"]["source"] == str(required_sources[1])
    _assert_optional_recovery_partition(
        terminal,
        report,
        optional_sources,
        completed_indexes=set(),
        failed_index=None,
    )


@pytest.mark.parametrize("failed_optional_index", [0, 1, 2])
def test_optional_absence_checkpoint_failure_partitions_exact_prefix(
        tmp_path, monkeypatch, failed_optional_index):
    (
        required_sources,
        required_destinations,
        optional_sources,
        optional_destinations,
        report,
    ) = _recovery_partition_fixture(tmp_path)
    real_require = objects._require_optional_absent

    def fail_absence_checkpoint(plan, phase):
        if (phase == "before its resolution checkpoint"
                and plan.source_path == optional_sources[failed_optional_index]):
            raise OSError("synthetic indexed optional absence failure")
        return real_require(plan, phase)

    monkeypatch.setattr(
        objects, "_require_optional_absent", fail_absence_checkpoint
    )
    with pytest.raises(objects.ArchiveBatchError) as failure:
        objects.archive_batch(
            tuple(zip(required_sources, required_destinations)),
            optional_pairs=tuple(zip(optional_sources, optional_destinations)),
            report_directory=report,
        )
    terminal = failure.value.report
    assert terminal["failure"]["phase"] == "optional-absence-checkpoint"
    _assert_optional_recovery_partition(
        terminal,
        report,
        optional_sources,
        completed_indexes=set(range(failed_optional_index)),
        failed_index=failed_optional_index,
    )


@pytest.mark.parametrize("failed_optional_index", [0, 1, 2])
def test_optional_present_checkpoint_failure_partitions_exact_prefix(
        tmp_path, monkeypatch, failed_optional_index):
    (
        required_sources,
        required_destinations,
        optional_sources,
        optional_destinations,
        report,
    ) = _recovery_partition_fixture(
        tmp_path, present_optional_indexes=(0, 1, 2)
    )
    real_write = objects._ArchiveReportJournal.write
    failed_name = f"optional-checkpoint-{failed_optional_index + 1:04d}.json"

    def fail_optional_checkpoint(self, name, value):
        if name == failed_name:
            raise OSError("synthetic indexed optional checkpoint failure")
        return real_write(self, name, value)

    monkeypatch.setattr(
        objects._ArchiveReportJournal, "write", fail_optional_checkpoint
    )
    with pytest.raises(objects.ArchiveBatchError) as failure:
        objects.archive_batch(
            tuple(zip(required_sources, required_destinations)),
            optional_pairs=tuple(zip(optional_sources, optional_destinations)),
            report_directory=report,
        )
    terminal = failure.value.report
    assert terminal["failure"]["phase"] == "optional-checkpoint"
    _assert_optional_recovery_partition(
        terminal,
        report,
        optional_sources,
        completed_indexes=set(range(failed_optional_index)),
        failed_index=failed_optional_index,
    )


@pytest.mark.parametrize("terminal_fault", ["absence", "report"])
def test_terminal_phase_failure_keeps_all_optional_checkpoints_completed(
        tmp_path, monkeypatch, terminal_fault):
    (
        required_sources,
        required_destinations,
        optional_sources,
        optional_destinations,
        report,
    ) = _recovery_partition_fixture(tmp_path)
    if terminal_fault == "absence":
        real_require = objects._require_optional_absent

        def fail_terminal_absence(plan, phase):
            if phase == "before terminal":
                raise OSError("synthetic terminal optional absence failure")
            return real_require(plan, phase)

        monkeypatch.setattr(
            objects, "_require_optional_absent", fail_terminal_absence
        )
        expected_phase = "terminal-optional-absence"
    else:
        real_lock_entries = objects._archive_lock_entries
        first_call = True

        def fail_terminal_report_once(lock_entries):
            nonlocal first_call
            if first_call:
                first_call = False
                raise OSError("synthetic terminal report construction failure")
            return real_lock_entries(lock_entries)

        monkeypatch.setattr(
            objects, "_archive_lock_entries", fail_terminal_report_once
        )
        expected_phase = "terminal-report"

    with pytest.raises(objects.ArchiveBatchError) as failure:
        objects.archive_batch(
            tuple(zip(required_sources, required_destinations)),
            optional_pairs=tuple(zip(optional_sources, optional_destinations)),
            report_directory=report,
        )
    terminal = failure.value.report
    assert terminal["failure"]["phase"] == expected_phase
    _assert_optional_recovery_partition(
        terminal,
        report,
        optional_sources,
        completed_indexes={0, 1, 2},
        failed_index=None,
    )


def test_checkpoint_directory_fsync_fault_before_names_update_is_reconciled(
        tmp_path, monkeypatch):
    (
        required_sources,
        required_destinations,
        optional_sources,
        optional_destinations,
        report,
    ) = _recovery_partition_fixture(
        tmp_path, present_optional_indexes=(0, 1, 2)
    )
    target_name = "optional-checkpoint-0002.json"
    real_write = objects._ArchiveReportJournal.write
    real_fsync = objects.os.fsync
    active_journal = None
    active_name = None
    injected = False

    def track_write(self, name, value):
        nonlocal active_journal, active_name
        active_journal = self
        active_name = name
        try:
            return real_write(self, name, value)
        finally:
            active_journal = None
            active_name = None

    def fail_after_directory_fsync(fd):
        nonlocal injected
        result = real_fsync(fd)
        if (not injected
                and active_journal is not None
                and active_name == target_name
                and fd == active_journal.directory_fd):
            assert target_name not in active_journal._names
            injected = True
            raise OSError(
                "synthetic fault after checkpoint directory fsync"
            )
        return result

    monkeypatch.setattr(objects._ArchiveReportJournal, "write", track_write)
    monkeypatch.setattr(objects.os, "fsync", fail_after_directory_fsync)
    with pytest.raises(objects.ArchiveBatchError) as failure:
        objects.archive_batch(
            tuple(zip(required_sources, required_destinations)),
            optional_pairs=tuple(zip(optional_sources, optional_destinations)),
            report_directory=report,
        )
    assert injected
    assert (report / target_name).is_file()
    terminal = failure.value.report
    assert terminal["failure"]["phase"] == "optional-checkpoint"
    assert terminal["failed_optional_move"] is None
    _assert_optional_recovery_partition(
        terminal,
        report,
        optional_sources,
        completed_indexes={0, 1},
        failed_index=None,
    )


def test_torn_optional_checkpoint_is_not_promoted_into_terminal_prefix(
        tmp_path, monkeypatch):
    (
        required_sources,
        required_destinations,
        optional_sources,
        optional_destinations,
        report,
    ) = _recovery_partition_fixture(
        tmp_path, present_optional_indexes=(0, 1, 2)
    )
    target_name = "optional-checkpoint-0002.json"
    real_journal_write = objects._ArchiveReportJournal.write
    real_os_write = objects.os.write
    writing_target = False
    injected = False

    def track_target_write(self, name, value):
        nonlocal writing_target
        writing_target = name == target_name
        try:
            return real_journal_write(self, name, value)
        finally:
            writing_target = False

    def tear_target_write(fd, raw):
        nonlocal injected
        if writing_target and not injected:
            injected = True
            real_os_write(fd, raw[:max(1, len(raw) // 2)])
            raise OSError("synthetic torn optional checkpoint write")
        return real_os_write(fd, raw)

    monkeypatch.setattr(
        objects._ArchiveReportJournal, "write", track_target_write
    )
    monkeypatch.setattr(objects.os, "write", tear_target_write)
    with pytest.raises(objects.ArchiveBatchError) as failure:
        objects.archive_batch(
            tuple(zip(required_sources, required_destinations)),
            optional_pairs=tuple(zip(optional_sources, optional_destinations)),
            report_directory=report,
        )
    assert injected
    assert _valid_test_report_document(report, target_name) is None
    terminal = failure.value.report
    _assert_optional_recovery_partition(
        terminal,
        report,
        optional_sources,
        completed_indexes={0},
        failed_index=1,
    )


def test_extra_hash_valid_checkpoint_after_gap_remains_unresolved(
        tmp_path, monkeypatch):
    (
        required_sources,
        required_destinations,
        optional_sources,
        optional_destinations,
        report,
    ) = _recovery_partition_fixture(tmp_path)
    target_name = "optional-checkpoint-0001.json"
    extra_name = "optional-checkpoint-0002.json"
    real_write = objects._ArchiveReportJournal.write
    extra_created = False

    def create_extra_then_fail(self, name, value):
        nonlocal extra_created
        if name == target_name:
            extra_value = json.loads(json.dumps(value))
            extra_value["optional_index"] = 1
            extra_value["resolution"]["index"] = 1
            raw = objects._pretty_json(
                objects._hashed_archive_report_document(extra_value)
            )
            flags = (
                os.O_WRONLY | os.O_CREAT | os.O_EXCL
                | trusted_fs._required_flag("O_NOFOLLOW")
                | getattr(os, "O_CLOEXEC", 0)
            )
            fd = os.open(
                extra_name, flags, 0o400, dir_fd=self.directory_fd
            )
            try:
                os.fchmod(fd, 0o400)
                offset = 0
                while offset < len(raw):
                    offset += os.write(fd, raw[offset:])
                os.fsync(fd)
            finally:
                os.close(fd)
            os.fsync(self.directory_fd)
            extra_created = True
            raise OSError("synthetic gap before extra checkpoint")
        return real_write(self, name, value)

    monkeypatch.setattr(
        objects._ArchiveReportJournal, "write", create_extra_then_fail
    )
    with pytest.raises(objects.ArchiveBatchError) as failure:
        objects.archive_batch(
            tuple(zip(required_sources, required_destinations)),
            optional_pairs=tuple(zip(optional_sources, optional_destinations)),
            report_directory=report,
        )
    assert extra_created
    assert _valid_test_report_document(report, extra_name) is not None
    terminal = failure.value.report
    _assert_optional_recovery_partition(
        terminal,
        report,
        optional_sources,
        completed_indexes=set(),
        failed_index=0,
    )


@pytest.mark.parametrize(
    "failure_phase",
    ["optional-classification", "optional-checkpoint", "terminal-optional-absence"],
)
def test_optional_phase_failure_uses_durable_required_checkpoint_partition(
        tmp_path, monkeypatch, failure_phase):
    source_parent, destination_parent = _move_test_paths(tmp_path)
    required_sources = (source_parent / "first", source_parent / "second")
    for index, source in enumerate(required_sources):
        source.write_bytes(f"required {index}".encode())
    required_destinations = tuple(
        destination_parent / source.name for source in required_sources
    )
    optional_sources = (
        source_parent / ".trekdex-publication-transactions",
        source_parent / "publication-proof-objects",
    )
    optional_destinations = tuple(
        destination_parent / source.name for source in optional_sources
    )
    report = tmp_path / f"{failure_phase}-report"

    if failure_phase == "optional-classification":
        real_open = objects._open_archive_move_plan

        def fail_classification(source, destination, safety, **kwargs):
            if kwargs.get("source_may_be_absent"):
                raise OSError("synthetic optional classification failure")
            return real_open(source, destination, safety, **kwargs)

        monkeypatch.setattr(objects, "_open_archive_move_plan", fail_classification)
    elif failure_phase == "optional-checkpoint":
        real_write = objects._ArchiveReportJournal.write

        def fail_checkpoint(self, name, value):
            if name == "optional-checkpoint-0001.json":
                raise OSError("synthetic optional checkpoint failure")
            return real_write(self, name, value)

        monkeypatch.setattr(objects._ArchiveReportJournal, "write", fail_checkpoint)
    else:
        real_require = objects._require_optional_absent

        def fail_terminal_absence(plan, phase):
            if phase == "before terminal":
                raise OSError("synthetic optional terminal failure")
            return real_require(plan, phase)

        monkeypatch.setattr(objects, "_require_optional_absent", fail_terminal_absence)

    with pytest.raises(objects.ArchiveBatchError) as failure:
        objects.archive_batch(
            tuple(zip(required_sources, required_destinations)),
            optional_pairs=tuple(zip(optional_sources, optional_destinations)),
            report_directory=report,
        )
    terminal = failure.value.report
    assert terminal == _report_document(report, "terminal.json")
    assert terminal["failure"]["phase"] == failure_phase
    _assert_required_recovery_partition(terminal, required_sources)
    if failure_phase == "optional-classification":
        assert terminal["completed_moves"] == []
        assert terminal["failed_move"] is None
        assert {move["source"] for move in terminal["unattempted_moves"]} == {
            str(path) for path in required_sources
        }
    else:
        assert [
            move["source"] for move in terminal["completed_moves"]
        ] == [str(path) for path in required_sources]
        assert terminal["failed_move"] is None
        assert terminal["unattempted_moves"] == []


def test_failure_report_recovers_required_checkpoint_written_before_raise(
        tmp_path, monkeypatch):
    source_parent, destination_parent = _move_test_paths(tmp_path)
    source = source_parent / "required"
    source.write_bytes(b"durable checkpoint evidence")
    destination = destination_parent / "required"
    optional_source = source_parent / "optional"
    optional_destination = destination_parent / "optional"
    report = tmp_path / "durable-checkpoint-before-raise-report"
    real_write = objects._ArchiveReportJournal.write

    def write_then_raise(self, name, value):
        result = real_write(self, name, value)
        if name == "checkpoint-0001.json":
            raise OSError("synthetic return-path failure after durable checkpoint")
        return result

    monkeypatch.setattr(objects._ArchiveReportJournal, "write", write_then_raise)
    with pytest.raises(objects.ArchiveBatchError) as failure:
        objects.archive_batch(
            ((source, destination),),
            optional_pairs=((optional_source, optional_destination),),
            report_directory=report,
        )
    terminal = failure.value.report
    _assert_required_recovery_partition(terminal, (source,))
    assert [move["source"] for move in terminal["completed_moves"]] == [
        str(source)
    ]
    assert terminal["failed_move"] is None
    assert terminal["unattempted_moves"] == []


def test_failure_report_recovers_optional_checkpoint_written_before_raise(
        tmp_path, monkeypatch):
    source_parent, destination_parent = _move_test_paths(tmp_path)
    required = source_parent / "required"
    required.write_bytes(b"required durable evidence")
    optional = source_parent / "optional"
    optional.mkdir(mode=0o700)
    (optional / "leaf").write_bytes(b"optional durable evidence")
    report = tmp_path / "durable-optional-checkpoint-before-raise-report"
    real_write = objects._ArchiveReportJournal.write

    def write_then_raise(self, name, value):
        result = real_write(self, name, value)
        if name == "optional-checkpoint-0001.json":
            raise OSError("synthetic optional return-path failure")
        return result

    monkeypatch.setattr(objects._ArchiveReportJournal, "write", write_then_raise)
    with pytest.raises(objects.ArchiveBatchError) as failure:
        objects.archive_batch(
            ((required, destination_parent / "required"),),
            optional_pairs=((optional, destination_parent / "optional"),),
            report_directory=report,
        )
    terminal = failure.value.report
    _assert_required_recovery_partition(terminal, (required,))
    assert [move["source"] for move in terminal["completed_moves"]] == [
        str(required), str(optional),
    ]
    assert terminal["failed_move"] is None
    assert terminal["failed_optional_move"] is None
    assert terminal["unattempted_moves"] == []


def _optional_only_fixture(tmp_path: Path):
    live = tmp_path / "optional-only-live"
    archive = tmp_path / "optional-only-archive"
    live.mkdir(mode=0o700)
    archive.mkdir(mode=0o700)
    sources = (
        live / ".trekdex-publication-transactions",
        live / "publication-proof-objects",
    )
    destinations = tuple(archive / source.name for source in sources)
    return sources, destinations, tmp_path / "optional-only-report"


def _optional_only_cli_args(
        sources: tuple[Path, ...], destinations: tuple[Path, ...], report: Path,
) -> list[str]:
    arguments = ["archive-batch", "--report-directory", str(report)]
    for source, destination in zip(sources, destinations):
        arguments.extend(("--optional-pair", str(source), str(destination)))
    return arguments


@pytest.mark.parametrize("interface", ["api", "cli"])
def test_optional_only_both_absent_commits_and_verifies(
        tmp_path, capsys, interface):
    sources, destinations, report = _optional_only_fixture(tmp_path)
    if interface == "api":
        result = objects.archive_batch(
            (), optional_pairs=tuple(zip(sources, destinations)),
            report_directory=report,
        )
    else:
        assert objects.main(
            _optional_only_cli_args(sources, destinations, report)
        ) == 0
        result = json.loads(capsys.readouterr().out)
    assert result["moves"] == []
    assert [
        resolution["resolution"]
        for resolution in result["optional_resolutions"]
    ] == ["absent", "absent"]
    verified = objects.verify_archive_report(report)
    assert verified["moves_verified"] == 0
    assert verified["optional_sources_absent"] == 2
    assert all(not path.exists() for path in sources + destinations)


@pytest.mark.parametrize("interface", ["api", "cli"])
def test_optional_only_present_tree_moves_and_verifies(
        tmp_path, capsys, interface):
    sources, destinations, report = _optional_only_fixture(tmp_path)
    sources[0].mkdir(mode=0o700)
    (sources[0] / "leaf").write_bytes(b"optional-only present evidence")
    if interface == "api":
        result = objects.archive_batch(
            (), optional_pairs=tuple(zip(sources, destinations)),
            report_directory=report,
        )
    else:
        assert objects.main(
            _optional_only_cli_args(sources, destinations, report)
        ) == 0
        result = json.loads(capsys.readouterr().out)
    assert [
        resolution["resolution"]
        for resolution in result["optional_resolutions"]
    ] == ["present", "absent"]
    assert not sources[0].exists()
    assert (destinations[0] / "leaf").read_bytes() == (
        b"optional-only present evidence"
    )
    verified = objects.verify_archive_report(report)
    assert verified["moves_verified"] == 1
    assert verified["optional_sources_present"] == 1


@pytest.mark.parametrize("interface", ["api", "cli"])
def test_optional_only_lock_failure_is_not_committed(
        tmp_path, monkeypatch, capsys, interface):
    sources, destinations, report = _optional_only_fixture(tmp_path)
    sources[0].mkdir(mode=0o700)
    (sources[0] / "leaf").write_bytes(b"must stay live")

    def fail_lock(_path):
        raise OSError("synthetic optional-only lock failure")

    monkeypatch.setattr(trusted_fs, "open_lock_file", fail_lock)
    if interface == "api":
        with pytest.raises(objects.ArchiveBatchError) as failure:
            objects.archive_batch(
                (), optional_pairs=tuple(zip(sources, destinations)),
                report_directory=report,
            )
        terminal = failure.value.report
    else:
        with pytest.raises(SystemExit) as failure:
            objects.main(_optional_only_cli_args(sources, destinations, report))
        assert failure.value.code == 2
        terminal = json.loads(capsys.readouterr().out)
    assert terminal["archive_state"] == "not-committed"
    assert terminal["failure"]["phase"] == "lock-acquisition"
    assert terminal["completed_moves"] == []
    assert terminal["failed_move"] is None
    assert terminal["unattempted_moves"] == []
    assert (sources[0] / "leaf").read_bytes() == b"must stay live"
    assert all(not destination.exists() for destination in destinations)


@pytest.mark.parametrize("interface", ["api", "cli"])
def test_optional_only_occupied_report_namespace_precedes_locks(
        tmp_path, monkeypatch, interface):
    sources, destinations, report = _optional_only_fixture(tmp_path)
    report.mkdir(mode=0o700)
    marker = report / "operator-marker"
    marker.write_bytes(b"preserve")
    lock_called = False

    def unexpected_lock(*_args, **_kwargs):
        nonlocal lock_called
        lock_called = True
        raise AssertionError("occupied report must fail before locks")

    monkeypatch.setattr(trusted_fs, "locked_resources", unexpected_lock)
    if interface == "api":
        with pytest.raises(objects.ArchiveReportSetupError):
            objects.archive_batch(
                (), optional_pairs=tuple(zip(sources, destinations)),
                report_directory=report,
            )
    else:
        with pytest.raises(SystemExit, match="no archive source"):
            objects.main(_optional_only_cli_args(sources, destinations, report))
    assert not lock_called
    assert marker.read_bytes() == b"preserve"
    assert sorted(path.name for path in report.iterdir()) == ["operator-marker"]
    assert all(not path.exists() for path in sources + destinations)


@pytest.mark.parametrize("interface", ["api", "cli"])
def test_optional_only_report_failure_has_durable_consistent_terminal(
        tmp_path, monkeypatch, capsys, interface):
    sources, destinations, report = _optional_only_fixture(tmp_path)
    real_write = objects._ArchiveReportJournal.write

    def fail_optional_checkpoint(self, name, value):
        if name == "optional-checkpoint-0001.json":
            raise OSError("synthetic optional-only report failure")
        return real_write(self, name, value)

    monkeypatch.setattr(
        objects._ArchiveReportJournal, "write", fail_optional_checkpoint
    )
    if interface == "api":
        with pytest.raises(objects.ArchiveBatchError) as failure:
            objects.archive_batch(
                (), optional_pairs=tuple(zip(sources, destinations)),
                report_directory=report,
            )
        terminal = failure.value.report
    else:
        with pytest.raises(SystemExit) as failure:
            objects.main(_optional_only_cli_args(sources, destinations, report))
        assert failure.value.code == 2
        terminal = json.loads(capsys.readouterr().out)
    assert terminal == _report_document(report, "terminal.json")
    assert terminal["archive_state"] == "not-committed"
    assert terminal["failure"]["phase"] == "optional-checkpoint"
    assert terminal["completed_moves"] == []
    assert terminal["failed_move"] is None
    assert terminal["unattempted_moves"] == []
    assert terminal["failed_optional_move"]["source"] == str(sources[0])
    assert all(not path.exists() for path in sources + destinations)


@pytest.mark.parametrize("cut", ["before", "after"])
def test_optional_only_sigkill_preserves_exact_checkpoint_prefix(
        tmp_path, cut):
    sources, destinations, report = _optional_only_fixture(tmp_path)
    child = r'''
import os
import signal
import sys

sys.path.insert(0, sys.argv[1])
import publication_objects as objects

cut = sys.argv[7]
real_write = objects._ArchiveReportJournal.write

def killing_write(self, name, value):
    if name == "optional-checkpoint-0001.json" and cut == "before":
        os.kill(os.getpid(), signal.SIGKILL)
    result = real_write(self, name, value)
    if name == "optional-checkpoint-0001.json" and cut == "after":
        os.kill(os.getpid(), signal.SIGKILL)
    return result

objects._ArchiveReportJournal.write = killing_write
objects.main([
    "archive-batch", "--report-directory", sys.argv[2],
    "--optional-pair", sys.argv[3], sys.argv[4],
    "--optional-pair", sys.argv[5], sys.argv[6],
])
'''
    process = subprocess.run(
        [
            sys.executable, "-c", child, str(TOOLS), str(report),
            str(sources[0]), str(destinations[0]),
            str(sources[1]), str(destinations[1]), cut,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
        env={
            **os.environ,
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )
    assert process.returncode == -signal.SIGKILL, process.stderr
    names = sorted(path.name for path in report.iterdir())
    expected_names = (
        ["optional-checkpoint-0001.json", "plan.json", "prepared.json"]
        if cut == "after" else ["plan.json", "prepared.json"]
    )
    assert names == expected_names
    assert "terminal.json" not in names
    assert all(not path.exists() for path in sources + destinations)
    with pytest.raises(ValueError, match="missing"):
        objects.verify_archive_report(report)


@pytest.mark.parametrize("interface", ["api", "cli"])
def test_optional_only_verifier_rejects_resolution_state_flip(
        tmp_path, capsys, interface):
    sources, destinations, report = _optional_only_fixture(tmp_path)
    objects.archive_batch(
        (), optional_pairs=tuple(zip(sources, destinations)),
        report_directory=report,
    )

    def flip(document):
        document["optional_resolutions"][0]["resolution"] = "present"

    _rewrite_archive_document(report, "prepared.json", flip)
    if interface == "api":
        with pytest.raises(ValueError, match="optional|chain|invalid"):
            objects.verify_archive_report(report)
    else:
        with pytest.raises(SystemExit, match="optional|chain|invalid"):
            objects.main(["verify-archive-report", str(report)])
    assert all(not path.exists() for path in sources + destinations)


def test_optional_only_verifier_rereads_plan_after_lock_acquisition(
        tmp_path, monkeypatch):
    sources, destinations, report = _optional_only_fixture(tmp_path)
    objects.archive_batch(
        (), optional_pairs=tuple(zip(sources, destinations)),
        report_directory=report,
    )
    real_enter = objects._enter_archive_report_lease

    def enter_then_flip(report_path, plan_document):
        lease, entries = real_enter(report_path, plan_document)
        _rewrite_archive_document(
            report, "plan.json",
            lambda document: document.__setitem__("state", "FLIPPED"),
        )
        return lease, entries

    monkeypatch.setattr(objects, "_enter_archive_report_lease", enter_then_flip)
    with pytest.raises(ValueError, match="identity|changed"):
        objects.verify_archive_report(report)
    assert all(not path.exists() for path in sources + destinations)


@pytest.mark.parametrize("interface", ["api", "cli"])
def test_optional_only_verifier_lock_failure_refuses_without_archive_mutation(
        tmp_path, monkeypatch, interface):
    sources, destinations, report = _optional_only_fixture(tmp_path)
    objects.archive_batch(
        (), optional_pairs=tuple(zip(sources, destinations)),
        report_directory=report,
    )
    before = {path.name: path.read_bytes() for path in report.iterdir()}

    def fail_lock(_path):
        raise OSError("synthetic verifier lock failure")

    monkeypatch.setattr(trusted_fs, "open_lock_file", fail_lock)
    if interface == "api":
        with pytest.raises(
                ValueError, match="verification refused without archive mutation"):
            objects.verify_archive_report(report)
    else:
        with pytest.raises(
                SystemExit, match="verification refused without archive mutation"):
            objects.main(["verify-archive-report", str(report)])
    assert {path.name: path.read_bytes() for path in report.iterdir()} == before
    assert all(not path.exists() for path in sources + destinations)


def test_optional_only_verifier_acquires_exact_global_plan_lock_order(
        tmp_path, monkeypatch):
    sources, destinations, report = _optional_only_fixture(tmp_path)
    terminal = objects.archive_batch(
        (), optional_pairs=tuple(zip(sources, destinations)),
        report_directory=report,
    )
    opened = []
    real_open = trusted_fs.open_lock_file

    def recording_open(path):
        opened.append(Path(path))
        return real_open(path)

    monkeypatch.setattr(trusted_fs, "open_lock_file", recording_open)
    assert objects.verify_archive_report(report)["moves_verified"] == 0
    assert opened == [Path(entry["lock"]) for entry in terminal["locks"]]
    assert opened == sorted(opened, key=str)
    assert len(opened) == len(set(opened))


def test_optional_only_verifier_blocks_writer_until_lease_release(
        tmp_path, monkeypatch):
    sources, destinations, report = _optional_only_fixture(tmp_path)
    objects.archive_batch(
        (), optional_pairs=tuple(zip(sources, destinations)),
        report_directory=report,
    )
    source = sources[0]
    attempted = tmp_path / "verify-writer-attempted"
    ready = tmp_path / "verify-writer-ready"
    writer = None
    real_require_absent = objects._require_archive_path_absent

    def require_absent(path, label):
        nonlocal writer
        result = real_require_absent(path, label)
        if (writer is None and Path(path) == source
                and label == "optional archive source recorded absent"):
            writer = subprocess.Popen(
                [
                    sys.executable, "-c", _COOPERATING_OPTIONAL_WRITER,
                    str(TOOLS), str(source), str(attempted), str(ready), "-",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env={
                    **os.environ,
                    "PATH": "/usr/local/bin:/usr/bin:/bin",
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
            )
            _wait_for_path(attempted)
            time.sleep(0.1)
            assert writer.poll() is None
            assert not source.exists()
        return result

    monkeypatch.setattr(objects, "_require_archive_path_absent", require_absent)
    result = objects.verify_archive_report(report)
    assert result["optional_sources_absent"] == 2
    assert writer is not None
    stdout, stderr = writer.communicate(timeout=10)
    assert writer.returncode == 0, (stdout, stderr)
    assert ready.is_file()
    assert (source / "writer-leaf").read_bytes() == (
        b"cooperating writer evidence"
    )
    with pytest.raises(ValueError, match="recorded absent|must remain absent"):
        objects.verify_archive_report(report)


def test_archive_batch_records_absent_optional_pairs_in_complete_lock_chain(
        tmp_path):
    (
        required_source,
        required_destination,
        optional_sources,
        optional_destinations,
        report,
    ) = _optional_archive_fixture(tmp_path)
    result = objects.archive_batch(
        ((required_source, required_destination),),
        optional_pairs=tuple(zip(optional_sources, optional_destinations)),
        report_directory=report,
    )

    plan = _report_document(report, "plan.json")
    prepared = _report_document(report, "prepared.json")
    assert [entry["source"] for entry in plan["optional_moves"]] == [
        str(path) for path in optional_sources
    ]
    assert plan["lock_resources"] == sorted(plan["lock_resources"])
    for entry in plan["optional_moves"]:
        assert {
            entry["source"], entry["destination"], entry["safety_copy"],
        }.issubset(plan["lock_resources"])
    assert [
        entry["resolution"] for entry in prepared["optional_resolutions"]
    ] == ["absent", "absent"]
    assert result["optional_resolutions"] == [
        _report_document(
            report, f"optional-checkpoint-{index + 1:04d}.json"
        )["resolution"]
        for index in range(2)
    ]
    assert [
        entry["resolution"] for entry in result["optional_resolutions"]
    ] == ["absent", "absent"]
    verified = objects.verify_archive_report(report)
    assert verified["moves_verified"] == 1
    assert verified["optional_resolutions_verified"] == 2
    assert verified["optional_sources_present"] == 0
    assert verified["optional_sources_absent"] == 2


@pytest.mark.parametrize("optional_index", [0, 1])
@pytest.mark.parametrize("appearance", ["source", "destination", "safety"])
def test_verify_archive_report_rejects_late_optional_path_appearance(
        tmp_path, optional_index, appearance):
    (
        required_source,
        required_destination,
        optional_sources,
        optional_destinations,
        report,
    ) = _optional_archive_fixture(tmp_path)
    objects.archive_batch(
        ((required_source, required_destination),),
        optional_pairs=tuple(zip(optional_sources, optional_destinations)),
        report_directory=report,
    )
    planned = _report_document(report, "plan.json")["optional_moves"][
        optional_index
    ]
    target = {
        "source": optional_sources[optional_index],
        "destination": optional_destinations[optional_index],
        "safety": Path(planned["safety_copy"]),
    }[appearance]
    target.mkdir(mode=0o700)

    with pytest.raises(ValueError, match="recorded absent|must remain absent"):
        objects.verify_archive_report(report)


@pytest.mark.parametrize("optional_index", [0, 1])
def test_archive_batch_moves_present_optional_tree_with_exact_evidence(
        tmp_path, optional_index):
    (
        required_source,
        required_destination,
        optional_sources,
        optional_destinations,
        report,
    ) = _optional_archive_fixture(tmp_path)
    source = optional_sources[optional_index]
    source.mkdir(mode=0o700)
    (source / "leaf.bin").write_bytes(b"optional tree evidence")
    result = objects.archive_batch(
        ((required_source, required_destination),),
        optional_pairs=tuple(zip(optional_sources, optional_destinations)),
        report_directory=report,
    )

    resolutions = result["optional_resolutions"]
    assert [entry["resolution"] for entry in resolutions] == [
        "present" if index == optional_index else "absent"
        for index in range(2)
    ]
    present = resolutions[optional_index]
    assert present["move"] in result["moves"]
    assert not source.exists()
    assert (optional_destinations[optional_index] / "leaf.bin").read_bytes() == (
        b"optional tree evidence"
    )
    safety = Path(present["move"]["safety_copy"])
    assert (safety / "leaf.bin").read_bytes() == b"optional tree evidence"
    verified = objects.verify_archive_report(report)
    assert verified["moves_verified"] == 2
    assert verified["optional_sources_present"] == 1
    assert verified["optional_sources_absent"] == 1


@pytest.mark.parametrize(
    "damage",
    ["omitted", "extra", "state-flipped", "reordered", "tampered"],
)
def test_verify_archive_report_rejects_optional_entry_damage(tmp_path, damage):
    (
        required_source,
        required_destination,
        optional_sources,
        optional_destinations,
        report,
    ) = _optional_archive_fixture(tmp_path)
    objects.archive_batch(
        ((required_source, required_destination),),
        optional_pairs=tuple(zip(optional_sources, optional_destinations)),
        report_directory=report,
    )

    if damage == "omitted":
        _rewrite_archive_document(
            report, "plan.json",
            lambda document: document["optional_moves"].pop(),
        )
    elif damage == "extra":
        def add_extra(document):
            document["optional_moves"].append({
                "index": 2,
                "source": str(tmp_path / "live" / "extra-optional"),
                "destination": str(tmp_path / "archive" / "extra-optional"),
                "safety_copy": str(
                    tmp_path / "archive" / ".extra-optional-safety"
                ),
            })
        _rewrite_archive_document(report, "plan.json", add_extra)
    elif damage == "state-flipped":
        def flip_state(document):
            document["optional_resolutions"][0]["resolution"] = "present"
        _rewrite_archive_document(report, "prepared.json", flip_state)
    elif damage == "reordered":
        _rewrite_archive_document(
            report, "plan.json",
            lambda document: document["optional_moves"].reverse(),
        )
    else:
        def tamper_checkpoint(document):
            document["resolution"]["source"] += "-tampered"
        _rewrite_archive_document(
            report, "optional-checkpoint-0001.json", tamper_checkpoint
        )

    with pytest.raises(
            ValueError,
            match="optional|batch|hash|chain|reordered|schema|mismatch",
    ):
        objects.verify_archive_report(report)


@pytest.mark.parametrize("optional_present", [False, True])
@pytest.mark.parametrize("cut", ["before", "after"])
def test_sigkill_preserves_optional_resolution_checkpoint_prefix(
        tmp_path, optional_present, cut):
    (
        required_source,
        required_destination,
        optional_sources,
        optional_destinations,
        report,
    ) = _optional_archive_fixture(tmp_path)
    optional_source = optional_sources[0]
    optional_destination = optional_destinations[0]
    if optional_present:
        optional_source.mkdir(mode=0o700)
        (optional_source / "leaf").write_bytes(b"SIGKILL optional evidence")
    child = r'''
import os
import signal
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
import publication_objects as objects

required_source = Path(sys.argv[2])
required_destination = Path(sys.argv[3])
optional_source = Path(sys.argv[4])
optional_destination = Path(sys.argv[5])
report = Path(sys.argv[6])
cut = sys.argv[7]
real_write = objects._ArchiveReportJournal.write

def killing_write(self, name, value):
    if name.startswith("optional-checkpoint-") and cut == "before":
        os.kill(os.getpid(), signal.SIGKILL)
    result = real_write(self, name, value)
    if name.startswith("optional-checkpoint-") and cut == "after":
        os.kill(os.getpid(), signal.SIGKILL)
    return result

objects._ArchiveReportJournal.write = killing_write
objects.archive_batch(
    ((required_source, required_destination),),
    optional_pairs=((optional_source, optional_destination),),
    report_directory=report,
)
'''
    process = subprocess.run(
        [
            sys.executable, "-c", child, str(TOOLS),
            str(required_source), str(required_destination),
            str(optional_source), str(optional_destination), str(report), cut,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )

    assert process.returncode == -signal.SIGKILL, process.stderr
    names = sorted(path.name for path in report.iterdir())
    assert "plan.json" in names
    assert "prepared.json" in names
    assert "checkpoint-0001.json" in names
    assert "terminal.json" not in names
    assert ("optional-checkpoint-0001.json" in names) is (cut == "after")
    prepared = _report_document(report, "prepared.json")
    assert prepared["optional_resolutions"][0]["resolution"] == (
        "present" if optional_present else "absent"
    )
    if cut == "after":
        checkpoint = _report_document(
            report, "optional-checkpoint-0001.json"
        )
        assert checkpoint["resolution"]["resolution"] == (
            "present" if optional_present else "absent"
        )
    assert optional_destination.exists() is optional_present
    with pytest.raises(ValueError, match="missing"):
        objects.verify_archive_report(report)


_COOPERATING_OPTIONAL_WRITER = r'''
import fcntl
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, sys.argv[1])
import trust_resolution as tr
import trusted_filesystem as trusted_fs

source = Path(sys.argv[2])
attempted = Path(sys.argv[3])
ready = Path(sys.argv[4])
release = None if sys.argv[5] == "-" else Path(sys.argv[5])
attempted.write_text("attempted")
with trusted_fs.locked_resources(
        source.parent, ((source, fcntl.LOCK_EX),), tr.resource_lock_path):
    source.mkdir(mode=0o700)
    (source / "writer-leaf").write_bytes(b"cooperating writer evidence")
    ready.write_text("ready")
    while release is not None and not release.exists():
        time.sleep(0.01)
'''


@pytest.mark.parametrize(
    "optional_name",
    [".trekdex-publication-transactions", "publication-proof-objects"],
)
def test_optional_writer_wins_before_archive_lease_and_tree_is_included(
        tmp_path, optional_name):
    (
        required_source,
        required_destination,
        optional_sources,
        optional_destinations,
        report,
    ) = _optional_archive_fixture(tmp_path)
    index = [path.name for path in optional_sources].index(optional_name)
    source = optional_sources[index]
    destination = optional_destinations[index]
    attempted = tmp_path / "writer-attempted"
    ready = tmp_path / "writer-ready"
    release = tmp_path / "writer-release"
    writer = subprocess.Popen(
        [
            sys.executable, "-c", _COOPERATING_OPTIONAL_WRITER,
            str(TOOLS), str(source), str(attempted), str(ready), str(release),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    _wait_for_path(ready)
    archive = subprocess.Popen(
        [
            sys.executable, str(TOOLS / "publication_objects.py"),
            "archive-batch", "--report-directory", str(report),
            "--pair", str(required_source), str(required_destination),
            "--optional-pair", str(source), str(destination),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    _wait_for_path(report / "plan.json")
    time.sleep(0.1)
    assert archive.poll() is None
    release.write_text("release")
    writer_stdout, writer_stderr = writer.communicate(timeout=10)
    assert writer.returncode == 0, (writer_stdout, writer_stderr)
    stdout, stderr = archive.communicate(timeout=20)
    assert archive.returncode == 0, stderr
    result = json.loads(stdout)

    assert result["optional_resolutions"][0]["resolution"] == "present"
    assert not source.exists()
    assert (destination / "writer-leaf").read_bytes() == (
        b"cooperating writer evidence"
    )
    assert objects.verify_archive_report(report)[
        "optional_sources_present"
    ] == 1


@pytest.mark.parametrize(
    "optional_name",
    [".trekdex-publication-transactions", "publication-proof-objects"],
)
def test_optional_writer_blocks_until_terminal_then_verifier_rejects_appearance(
        tmp_path, monkeypatch, optional_name):
    (
        required_source,
        required_destination,
        optional_sources,
        optional_destinations,
        report,
    ) = _optional_archive_fixture(tmp_path)
    index = [path.name for path in optional_sources].index(optional_name)
    source = optional_sources[index]
    destination = optional_destinations[index]
    attempted = tmp_path / "writer-attempted"
    ready = tmp_path / "writer-ready"
    real_open = objects._open_archive_move_plan
    writer = None

    def open_with_blocked_writer(*args, **kwargs):
        nonlocal writer
        if writer is None:
            writer = subprocess.Popen(
                [
                    sys.executable, "-c", _COOPERATING_OPTIONAL_WRITER,
                    str(TOOLS), str(source), str(attempted), str(ready), "-",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
            _wait_for_path(attempted)
            time.sleep(0.1)
            assert writer.poll() is None
            assert not source.exists()
            assert not (report / "terminal.json").exists()
            plan = _report_document(report, "plan.json")
            planned = plan["optional_moves"][0]
            assert planned["source"] == str(source)
            assert planned["destination"] == str(destination)
            assert {
                planned["source"],
                planned["destination"],
                planned["safety_copy"],
            }.issubset(plan["lock_resources"])
        return real_open(*args, **kwargs)

    monkeypatch.setattr(objects, "_open_archive_move_plan", open_with_blocked_writer)
    result = objects.archive_batch(
        ((required_source, required_destination),),
        optional_pairs=((source, destination),),
        report_directory=report,
    )
    assert writer is not None
    stdout, stderr = writer.communicate(timeout=10)
    assert writer.returncode == 0, (stdout, stderr)
    _wait_for_path(ready)

    assert result["optional_resolutions"][0]["resolution"] == "absent"
    assert source.is_dir()
    assert not destination.exists()
    with pytest.raises(ValueError, match="recorded absent|must remain absent"):
        objects.verify_archive_report(report)


def test_publication_transaction_locks_are_anchored_outside_movable_tree(
        tmp_path):
    data_root = tmp_path / "data"
    transaction_root = data_root / ".trekdex-publication-transactions"
    journal = transaction_root / "co_verdicts_osm.json.journal.json"
    lock_path = objects.tr.resource_lock_path(data_root, journal)

    assert lock_path.parent == data_root / ".trekdex-locks"
    assert transaction_root not in lock_path.parents
    assert lock_path == objects.tr.resource_lock_path(tmp_path / "other", journal)


def test_archive_recipes_use_one_complete_lock_held_batch_per_recipe():
    readme = (HERE / "parking-adjud" / "README.md").read_text()
    fenced = re.findall(r"```bash\n(.*?)\n\s*```", readme, flags=re.DOTALL)
    recipes = {}
    for variable, reports in (
        (
            "ARCHIVE_ROOT",
            (
                "current-image.lengths", "current-image.sha256",
            ),
        ),
        (
            "DURABLE_ARCHIVE",
            ("verify-before.json", "verify-after.json"),
        ),
    ):
        matches = [block for block in fenced if f"{variable}=" in block]
        assert len(matches) == 1
        recipe = matches[0]
        recipes[variable] = recipe
        syntax = subprocess.run(
            ["bash", "-n", "-c", recipe],
            check=False,
            capture_output=True,
            text=True,
        )
        assert syntax.returncode == 0, syntax.stderr
        lines = [line.strip() for line in recipe.splitlines() if line.strip()]
        assert lines[0] == "set -euo pipefail"

        guard = lines.index(f'if test -e "${variable}"; then')
        guard_end = lines.index("fi", guard)
        assert "exit 1" in " ".join(lines[guard:guard_end])
        mkdir = lines.index(f'mkdir -m 700 "${variable}"')
        assert guard_end < mkdir
        assert lines.index(f'case "${variable}" in') < mkdir
        assert not any("mkdir -p" in line for line in lines)
        fail_open = re.compile(
            rf'(?:test|\[)\s+!\s+-e\s+"\$(?:{variable}|\{{{variable}\}})"'
            rf'(?:\s*\])?\s*&&'
        )
        assert fail_open.search(recipe) is None

        noclobber = next(
            index for index, line in enumerate(lines) if line.startswith("set -C")
        )
        for report in reports:
            report_write = next(
                index for index, line in enumerate(lines)
                if report in line and ">" in line
            )
            assert noclobber < report_write

        assert lines.count("archive-batch") == 1
        expected_report_arguments = 2 if variable == "ARCHIVE_ROOT" else 1
        assert recipe.count("--report-directory") == expected_report_arguments
        expected_verifiers = 0 if variable == "ARCHIVE_ROOT" else 1
        assert recipe.count("verify-archive-report") == expected_verifiers
        assert "archive-batch.json" not in recipe
        batch_calls = [
            index for index, line in enumerate(lines)
            if "${ARCHIVE_BATCH_ARGS[@]}" in line
        ]
        assert len(batch_calls) == 1
        batch_call = batch_calls[0]
        pair_lines = [
            index for index, line in enumerate(lines)
            if line.startswith("--pair")
        ]
        assert pair_lines and max(pair_lines) < batch_call
        assert guard_end < batch_call
        assert "move-no-replace" not in recipe
        assert re.search(r"(?m)^\s*mv(?:\s|$)", recipe) is None

        for store_name in (
            "phx_verdicts_osm.json",
            "ne_verdicts_osm.json",
            "zion-wilderness-ut_verdicts2.json",
            "griffith-park-ca_verdicts2.json",
            "co_verdicts_osm.json",
        ):
            assert store_name in recipe
        for exact_resource in (
            "publication-trust-root-v1.json",
            "publication-proof-objects",
            "${STORE_STEM}_publication_proofs.json",
            "${STORE_STEM}_publication_floor.json",
            "$STORE_NAME.journal.json",
        ):
            assert exact_resource in recipe

    assert "move-no-replace" not in readme
    assert "committed-indeterminate" in readme
    assert "blind_retry_forbidden: true" in readme
    assert "destination content" in readme
    assert "destination parent" in readme
    assert "source parent" in readme
    assert "same-UID process that ignores" in readme
    assert "impossible" in readme
    assert "not claimed" in readme
    assert ".*trekdex-archive-safety-*" in readme

    legacy = recipes["ARCHIVE_ROOT"]
    assert "stat -f" not in legacy
    assert "wc -c" in legacy
    assert legacy.count("--pair") == 5
    assert legacy.count("--optional-pair") == 2
    assert 'test -e "$TRANSACTION_TREE"' not in legacy
    assert 'test -L "$TRANSACTION_TREE"' not in legacy
    assert 'test -e "$OBJECT_TREE"' not in legacy
    assert 'test -L "$OBJECT_TREE"' not in legacy
    for destination in (
        "$ARCHIVE_ROOT/scripts/parking-adjud/data/co_verdicts_osm.json",
        "$ARCHIVE_ROOT/scripts/parking-adjud/data/"
        "co_verdicts_osm_publication_proofs.json",
        "$ARCHIVE_ROOT/scripts/parking-adjud/data/"
        "co_verdicts_osm_publication_floor.json",
        "$ARCHIVE_ROOT/scripts/parking-adjud/publication-trust-root-v1.json",
        "$ARCHIVE_ROOT/scripts/build-parking-verdicts.py",
        "$ARCHIVE_ROOT/scripts/parking-adjud/data/"
        ".trekdex-publication-transactions",
        "$ARCHIVE_ROOT/scripts/parking-adjud/data/publication-proof-objects",
    ):
        assert f'"{destination}"' in legacy
    restore_call = legacy.index("restore-generation-1")
    assert legacy.index("${ARCHIVE_BATCH_ARGS[@]}") < restore_call
    assert '--repo-root "$REPO_ROOT"' in legacy
    assert '--source-commit "$BASE"' in legacy
    assert 'verify-archive-report "$ARCHIVE_REPORT"' not in legacy
    assert re.search(r"(?m)^\s*git\s+restore(?:\s|$)", legacy) is None
    assert legacy.count("restore-generation-1") == 1
    assert legacy.count("scripts/build-parking-verdicts.py") >= 5
    assert (
        "a2ab1d97bb949422e539c7769223977090c4a5d449ce71e8e22b00764c9e3fdb"
        in legacy
    )
    assert 'grep -Fqx "    \\"$RESTORED_ROOT_SHA256\\""' in legacy

    stage = recipes["DURABLE_ARCHIVE"]
    stage_lines = [line.strip() for line in stage.splitlines() if line.strip()]
    collision = stage_lines.index(
        'if test "$JOURNAL_NAME" = "$STAGE_NAME"; then'
    )
    collision_end = stage_lines.index("fi", collision)
    batch_call = next(
        index for index, line in enumerate(stage_lines)
        if "${ARCHIVE_BATCH_ARGS[@]}" in line
    )
    assert "exit 1" in " ".join(stage_lines[collision:collision_end])
    assert collision_end < batch_call
    assert stage.count("--pair") == 2
    assert stage.index('> "$DURABLE_ARCHIVE/verify-before.json"') < stage.index(
        "${ARCHIVE_BATCH_ARGS[@]}"
    )
    assert stage.index("${ARCHIVE_BATCH_ARGS[@]}") < stage.index(
        "verify-archive-report"
    ) < stage.index('> "$DURABLE_ARCHIVE/verify-after.json"')
    assert '"$DURABLE_ARCHIVE/$STAGE_NAME"' in stage
    assert '"$DURABLE_ARCHIVE/$JOURNAL_NAME"' in stage
