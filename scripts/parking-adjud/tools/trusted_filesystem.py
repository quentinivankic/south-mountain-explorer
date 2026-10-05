#!/usr/bin/env python3
"""Descriptor-relative primitives for trusted Trekdex write namespaces.

A trusted directory is owned by the current effective user, is a directory,
and has no group/world write bits. These checks exclude other-user and ambient
write access; they do not make portable POSIX rename/link operations safe from
a malicious same-UID process. Every same-UID process with access to a trusted
namespace is part of the external contract and must acquire the canonical lock
before inspecting, rotating, linking, or replacing entries in that namespace.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import errno
import fcntl
import os
import secrets
import stat
import sys
from contextlib import contextmanager
from pathlib import Path


_DARWIN_ACL_TYPE_EXTENDED = 0x00000100
_ACL_LIBRARIES: dict[str, ctypes.CDLL] = {}


def _acl_library(kind: str) -> ctypes.CDLL:
    cached = _ACL_LIBRARIES.get(kind)
    if cached is not None:
        return cached
    if kind == "darwin":
        name = ctypes.util.find_library("c")
    elif kind == "linux-libc":
        name = ctypes.util.find_library("c")
    else:  # pragma: no cover - internal misuse
        raise ValueError(f"unknown ACL library kind {kind}")
    if not name:
        raise OSError(errno.ENOTSUP, f"ACL safety library is unavailable: {kind}")
    try:
        library = ctypes.CDLL(name, use_errno=True)
    except OSError as error:
        raise OSError(
            errno.ENOTSUP, f"ACL safety library cannot be loaded: {kind}"
        ) from error
    _ACL_LIBRARIES[kind] = library
    return library


def _darwin_acl_is_trivial(fd: int, _is_directory: bool) -> bool:
    library = _acl_library("darwin")
    try:
        get_acl = library.acl_get_fd_np
        free_acl = library.acl_free
    except AttributeError as error:
        raise OSError(
            errno.ENOTSUP, "descriptor-bound macOS ACL APIs are unavailable"
        ) from error
    get_acl.argtypes = (ctypes.c_int, ctypes.c_int)
    get_acl.restype = ctypes.c_void_p
    free_acl.argtypes = (ctypes.c_void_p,)
    free_acl.restype = ctypes.c_int
    ctypes.set_errno(0)
    acl = get_acl(fd, _DARWIN_ACL_TYPE_EXTENDED)
    if not acl:
        error_number = ctypes.get_errno()
        if error_number == errno.ENOENT:
            return True
        raise OSError(
            error_number or errno.EIO,
            "cannot inspect descriptor-bound macOS ACL",
        )
    # ACL_TYPE_EXTENDED contains only entries beyond the POSIX mode. Any
    # returned ACL therefore grants or denies nontrivial extended access.
    free_acl(acl)
    return False


def _linux_acl_xattr_absent(fd: int, name: bytes) -> bool:
    library = _acl_library("linux-libc")
    try:
        fgetxattr = library.fgetxattr
    except AttributeError as error:
        raise OSError(
            errno.ENOTSUP, "descriptor-bound Linux xattr API is unavailable"
        ) from error
    fgetxattr.argtypes = (
        ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t,
    )
    fgetxattr.restype = ctypes.c_ssize_t
    ctypes.set_errno(0)
    size = fgetxattr(fd, name, None, 0)
    if size >= 0:
        return False
    error_number = ctypes.get_errno()
    absent_errors = {getattr(errno, "ENODATA", -1), getattr(errno, "ENOATTR", -2)}
    if error_number in absent_errors:
        return True
    raise OSError(
        error_number or errno.EIO,
        f"cannot inspect descriptor-bound Linux ACL xattr {name!r}",
    )


def _linux_acl_is_trivial(fd: int, is_directory: bool) -> bool:
    # Linux stores POSIX access/default ACLs in descriptor-queryable system
    # xattrs. Absence proves mode-only access. Presence is rejected
    # conservatively, including mode-equivalent ACLs, rather than depending on
    # an optional libacl runtime or attempting to rewrite the ACL.
    if not _linux_acl_xattr_absent(fd, b"system.posix_acl_access"):
        return False
    return (
        not is_directory
        or _linux_acl_xattr_absent(fd, b"system.posix_acl_default")
    )


def _acl_platform() -> str:
    return sys.platform


def _require_acl_platform_support() -> None:
    platform = _acl_platform()
    if platform == "darwin":
        library = _acl_library("darwin")
        for symbol in ("acl_get_fd_np", "acl_free"):
            if not hasattr(library, symbol):
                raise OSError(
                    errno.ENOTSUP,
                    "descriptor-bound macOS ACL APIs are unavailable",
                )
        return
    if platform.startswith("linux"):
        libc = _acl_library("linux-libc")
        if not hasattr(libc, "fgetxattr"):
            raise OSError(
                errno.ENOTSUP,
                "descriptor-bound Linux default-ACL API is unavailable",
            )
        return
    raise OSError(
        errno.ENOTSUP,
        f"ACL safety is unsupported on mutation platform {platform}",
    )


def require_trivial_acl_fd(fd: int, path: str | Path, *,
                           is_directory: bool = False) -> None:
    """Reject extended access or inheritance using descriptor-bound APIs."""
    _require_acl_platform_support()
    platform = _acl_platform()
    if platform == "darwin":
        safe = _darwin_acl_is_trivial(fd, is_directory)
    elif platform.startswith("linux"):
        safe = _linux_acl_is_trivial(fd, is_directory)
    else:  # guarded above
        safe = False
    if not safe:
        kind = "directory" if is_directory else "file"
        raise ValueError(
            f"trusted {kind} has a nontrivial or inherited ACL: {Path(path).absolute()}"
        )


def _required_flag(name: str) -> int:
    if not hasattr(os, name):
        raise OSError(
            errno.ENOTSUP,
            f"trusted filesystem operations require {name}",
        )
    return getattr(os, name)


def _directory_identity(value: os.stat_result) -> tuple[int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_uid,
    )


def _safe_directory_mode(create_mode: int) -> int:
    requested_mode = stat.S_IMODE(create_mode)
    if requested_mode & 0o022:
        raise ValueError(
            f"trusted directory create mode is group/world writable: "
            f"{requested_mode:#05o}"
        )
    return requested_mode


def _require_created_directory_entry(
        child_fd: int, parent_fd: int, name: str, child: Path,
        requested_mode: int, expected_identity: tuple[int, int, int, int] | None = None,
) -> tuple[int, int, int, int]:
    """Verify one newly created child through its fd and parent entry."""
    fd_value = os.fstat(child_fd)
    entry_value = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    for value in (fd_value, entry_value):
        if not stat.S_ISDIR(value.st_mode):
            raise ValueError(f"new trusted child is not a directory: {child}")
        if value.st_uid != os.geteuid():
            raise ValueError(
                f"new trusted child is not owned by effective uid: {child}"
            )
        if stat.S_IMODE(value.st_mode) != requested_mode:
            raise ValueError(
                f"new trusted child is not exact mode {requested_mode:#05o}: "
                f"{child}"
            )
    identity = _directory_identity(fd_value)
    if _directory_identity(entry_value) != identity:
        raise ValueError(
            f"new trusted child fd and entry identity differ: {child}"
        )
    if expected_identity is not None and identity != expected_identity:
        raise ValueError(
            f"new trusted child identity or metadata changed: {child}"
        )
    require_trivial_acl_fd(child_fd, child, is_directory=True)
    return identity


def _require_directory_identity(
        fd: int, expected: tuple[int, int, int, int], path: Path,
) -> None:
    if _directory_identity(os.fstat(fd)) != expected:
        raise ValueError(f"trusted directory identity or metadata changed: {path}")


def create_trusted_directory_fd(
        parent_fd: int, parent_path: str | Path, name: str, *,
        create_mode: int = 0o700,
) -> int:
    """Create, verify, and durably link one descriptor-relative directory.

    The caller retains ``parent_fd`` and owns the returned child fd. A failed
    operation leaves no owned child descriptor open; an empty directory may
    remain after ``mkdir`` because cleanup never falls back to a path lookup.
    """
    requested_mode = _safe_directory_mode(create_mode)
    _require_acl_platform_support()
    flags = (os.O_RDONLY | _required_flag("O_DIRECTORY")
             | _required_flag("O_NOFOLLOW")
             | getattr(os, "O_CLOEXEC", 0))
    parent = Path(parent_path).absolute()
    child = parent / name
    require_trivial_acl_fd(parent_fd, parent, is_directory=True)
    parent_identity = _directory_identity(os.fstat(parent_fd))
    os.mkdir(name, mode=requested_mode, dir_fd=parent_fd)
    child_fd = None
    try:
        child_fd = os.open(name, flags, dir_fd=parent_fd)
        child_identity = _require_created_directory_entry(
            child_fd, parent_fd, name, child, requested_mode
        )
        _require_directory_identity(parent_fd, parent_identity, parent)
        os.fsync(parent_fd)
        _require_created_directory_entry(
            child_fd, parent_fd, name, child, requested_mode, child_identity
        )
        _require_directory_identity(parent_fd, parent_identity, parent)
        return child_fd
    except BaseException:
        if child_fd is not None:
            try:
                os.close(child_fd)
            except BaseException:
                pass
        raise


def open_directory_fd(path: str | Path, *, create: bool = False,
                      create_mode: int = 0o700) -> int:
    """Open an absolute directory by descriptor-walking every component.

    Missing components are created only when requested and use ``create_mode``;
    existing components are never chmodded. ``O_NOFOLLOW`` makes every path
    component a no-symlink boundary.
    """
    absolute = Path(path).absolute()
    if create:
        # Probe every mutation primitive before mkdir/O_CREAT so unsupported
        # safety boundaries cannot leave a partial directory tree behind.
        _require_acl_platform_support()
        _safe_directory_mode(create_mode)
        _required_flag("O_DIRECTORY")
        _required_flag("O_NOFOLLOW")
    if not absolute.is_absolute():  # defensive; Path.absolute currently is absolute
        raise ValueError(f"directory path is not absolute: {path}")
    flags = (os.O_RDONLY | _required_flag("O_DIRECTORY")
             | _required_flag("O_NOFOLLOW")
             | getattr(os, "O_CLOEXEC", 0))
    fd = os.open("/", flags)
    opened = Path("/")
    try:
        for part in absolute.parts[1:]:
            try:
                next_fd = os.open(part, flags, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                # FileExistsError after this observed ENOENT is intentionally
                # fatal. Never adopt a raced entry or retry path-wise.
                next_fd = create_trusted_directory_fd(
                    fd, opened, part, create_mode=create_mode
                )
            parent_fd = fd
            fd = next_fd
            os.close(parent_fd)
            opened /= part
        return fd
    except BaseException:
        try:
            os.close(fd)
        except BaseException:
            pass
        raise


def _entry_stat(path: Path) -> os.stat_result:
    absolute = path.absolute()
    if absolute == Path("/"):
        return os.stat("/", follow_symlinks=False)
    parent_fd = open_directory_fd(absolute.parent)
    try:
        return os.stat(
            absolute.name, dir_fd=parent_fd, follow_symlinks=False
        )
    finally:
        os.close(parent_fd)


def require_trusted_directory_fd(fd: int, path: str | Path) -> os.stat_result:
    """Require ``fd`` to remain bound to one safe canonical parent entry.

    The directory may be owner-readable/executable by a wider audience, but it
    must be owned by the current euid and not group/world writable. This is a
    cooperative same-UID boundary, not protection from a malicious peer that
    ignores the canonical lock contract.
    """
    canonical = Path(path).absolute()
    before = os.fstat(fd)
    require_trivial_acl_fd(fd, canonical, is_directory=True)
    entry_before = _entry_stat(canonical)
    after = os.fstat(fd)
    require_trivial_acl_fd(fd, canonical, is_directory=True)
    entry_after = _entry_stat(canonical)
    expected = _directory_identity(before)
    for value in (before, entry_before, after, entry_after):
        if not stat.S_ISDIR(value.st_mode):
            raise ValueError(f"trusted parent is not a directory: {canonical}")
        if value.st_uid != os.geteuid():
            raise ValueError(
                f"trusted parent is not owned by effective uid: {canonical}"
            )
        if stat.S_IMODE(value.st_mode) & 0o022:
            raise ValueError(
                f"trusted parent is group/world writable: {canonical}"
            )
        if _directory_identity(value) != expected:
            raise ValueError(
                f"trusted parent identity, mode, or path changed: {canonical}"
            )
    return after


def open_trusted_directory_fd(path: str | Path, *, create: bool = False,
                              create_mode: int = 0o700) -> int:
    """Open and verify one trusted parent, creating owned parents as 0700."""
    fd = open_directory_fd(path, create=create, create_mode=create_mode)
    try:
        require_trusted_directory_fd(fd, path)
        return fd
    except Exception:
        os.close(fd)
        raise


def _lock_identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_uid,
        value.st_nlink,
    )


def _require_lock_stat(value: os.stat_result, path: Path) -> None:
    if not stat.S_ISREG(value.st_mode):
        raise ValueError(f"lock is not a regular file: {path}")
    if value.st_uid != os.geteuid():
        raise ValueError(f"lock is not owned by effective uid: {path}")
    if stat.S_IMODE(value.st_mode) != 0o600:
        raise ValueError(f"lock is not owner-only mode 0600: {path}")
    if value.st_nlink != 1:
        raise ValueError(f"lock is not a single-link inode: {path}")


def open_lock_file(path: str | Path):
    """Open one canonical cooperative lock through a trusted parent.

    The lock inode is opened no-follow and must remain the owner-only,
    single-link regular entry named by ``path`` throughout this operation.
    Portable POSIX cannot stop a malicious same-UID process from rotating the
    name after return; all same-UID participants inside this trusted directory
    must therefore obey this lock rather than mutate the lock namespace.
    """
    canonical = Path(path).absolute()
    if not canonical.name:
        raise ValueError("lock path has no basename")
    parent_fd = open_trusted_directory_fd(
        canonical.parent, create=True, create_mode=0o700
    )
    fd = None
    try:
        parent_before = require_trusted_directory_fd(
            parent_fd, canonical.parent
        )
        flags = (os.O_RDWR | os.O_CREAT | _required_flag("O_NOFOLLOW")
                 | getattr(os, "O_CLOEXEC", 0))
        fd = os.open(canonical.name, flags, 0o600, dir_fd=parent_fd)
        fd_before = os.fstat(fd)
        entry_before = os.stat(
            canonical.name, dir_fd=parent_fd, follow_symlinks=False
        )
        require_trivial_acl_fd(fd, canonical)
        if ((fd_before.st_dev, fd_before.st_ino)
                != (entry_before.st_dev, entry_before.st_ino)):
            raise ValueError(
                f"lock identity, mode, or path changed while opening: "
                f"{canonical}"
            )
        for value in (fd_before, entry_before):
            if not stat.S_ISREG(value.st_mode):
                raise ValueError(f"lock is not a regular file: {canonical}")
            if value.st_uid != os.geteuid():
                raise ValueError(
                    f"lock is not owned by effective uid: {canonical}"
                )
            if value.st_nlink != 1:
                raise ValueError(
                    f"lock is not a single-link inode: {canonical}"
                )
        initial_modes = {
            stat.S_IMODE(fd_before.st_mode),
            stat.S_IMODE(entry_before.st_mode),
        }
        if initial_modes != {0o600}:
            if initial_modes != {0o644}:
                raise ValueError(
                    f"lock is not owner-only mode 0600: {canonical}"
                )
            # Older Trekdex lock files were created as 0644. A canonical,
            # current-owner, regular single-link inode can be tightened in
            # place without rotating its identity or invalidating an existing
            # advisory lock held by another cooperating process.
            os.fchmod(fd, 0o600)
            os.fsync(fd)

        fd_after = os.fstat(fd)
        entry_after = os.stat(
            canonical.name, dir_fd=parent_fd, follow_symlinks=False
        )
        require_trivial_acl_fd(fd, canonical)
        parent_after = require_trusted_directory_fd(
            parent_fd, canonical.parent
        )
        lock_stats = (fd_after, entry_after)
        if any(_lock_identity(value) != _lock_identity(fd_after)
               for value in lock_stats):
            raise ValueError(
                f"lock identity, mode, or path changed while opening: "
                f"{canonical}"
            )
        if ((fd_after.st_dev, fd_after.st_ino, fd_after.st_uid, fd_after.st_nlink)
                != (fd_before.st_dev, fd_before.st_ino,
                    fd_before.st_uid, fd_before.st_nlink)):
            raise ValueError(
                f"lock identity changed during mode migration: {canonical}"
            )
        for value in lock_stats:
            _require_lock_stat(value, canonical)
        if _directory_identity(parent_before) != _directory_identity(parent_after):
            raise ValueError(
                f"lock parent identity, mode, or path changed: {canonical.parent}"
            )
        handle = os.fdopen(fd, "a+b")
        fd = None
        return handle
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent_fd)


def resource_lock_entries(
        work_root: str | Path,
        resource_modes: list[tuple[str | Path, int]] | tuple[tuple[str | Path, int], ...],
        lock_path_for) -> tuple[tuple[Path, Path, int], ...]:
    """Canonicalize, deduplicate, and globally sort cooperative resources."""
    modes: dict[Path, int] = {}
    for resource, mode in resource_modes:
        if mode not in (fcntl.LOCK_SH, fcntl.LOCK_EX):
            raise ValueError("resource lock mode must be shared or exclusive")
        canonical = Path(resource).expanduser().resolve()
        previous = modes.get(canonical)
        modes[canonical] = (
            fcntl.LOCK_EX
            if mode == fcntl.LOCK_EX or previous == fcntl.LOCK_EX
            else fcntl.LOCK_SH
        )
    entries = [
        (Path(lock_path_for(work_root, resource)).resolve(), resource, mode)
        for resource, mode in modes.items()
    ]
    entries.sort(key=lambda entry: str(entry[0]))
    lock_paths = [lock_path for lock_path, _resource, _mode in entries]
    if len(lock_paths) != len(set(lock_paths)):
        raise ValueError("distinct resources resolve to one canonical lock path")
    return tuple(entries)


@contextmanager
def locked_resources(
        work_root: str | Path,
        resource_modes: list[tuple[str | Path, int]] | tuple[tuple[str | Path, int], ...],
        lock_path_for):
    """Hold one sorted shared/exclusive resource-lock set through a lease."""
    entries = resource_lock_entries(
        work_root, resource_modes, lock_path_for
    )
    handles = []
    try:
        for lock_path, _resource, mode in entries:
            handle = open_lock_file(lock_path)
            fcntl.flock(handle.fileno(), mode)
            handles.append(handle)
        yield entries
    finally:
        for handle in reversed(handles):
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()


def _read_fd_bytes(fd: int) -> bytes:
    os.lseek(fd, 0, os.SEEK_SET)
    chunks = []
    while True:
        chunk = os.read(fd, 1024 * 1024)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


MAX_STREAM_READ_SIZE = 1024 * 1024


def _regular_stream_signature(
        value: os.stat_result,
) -> tuple[int, int, int, int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_uid,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _require_stream_file_stat(
        value: os.stat_result, path: Path, *, require_owner: bool,
        require_read_only: bool, required_mode: int | None,
        allowed_nlinks: tuple[int, ...],
) -> None:
    if not stat.S_ISREG(value.st_mode):
        raise ValueError(f"stream source is not a regular file: {path}")
    if require_owner and value.st_uid != os.geteuid():
        raise ValueError(f"stream source is not owned by effective uid: {path}")
    if value.st_nlink not in allowed_nlinks:
        raise ValueError(
            f"stream source link count {value.st_nlink} is not allowed: {path}"
        )
    mode = stat.S_IMODE(value.st_mode)
    if require_read_only and mode & 0o222:
        raise ValueError(f"stream source is writable and not sealed: {path}")
    if required_mode is not None and mode != required_mode:
        raise ValueError(
            f"stream source mode {mode:#05o} is not {required_mode:#05o}: {path}"
        )


def iter_verified_regular_chunks(
        path: str | Path, *, chunk_size: int = MAX_STREAM_READ_SIZE,
        require_owner: bool = False, require_read_only: bool = False,
        required_mode: int | None = None, allowed_nlinks: tuple[int, ...] = (1,),
        expected_length: int | None = None,
        expected_sha256: str | None = None,
):
    """Yield a stable no-follow regular file in bounded chunks.

    Exact length/hash and final metadata checks complete only when the iterator
    is exhausted. Callers must consume it fully before treating the bytes as
    verified. The live directory entry and opened descriptor must identify one
    unchanged inode before and after the stream.
    """
    import hashlib

    if (type(chunk_size) is not int or chunk_size < 1
            or chunk_size > MAX_STREAM_READ_SIZE):
        raise ValueError(
            f"stream chunk size must be 1..{MAX_STREAM_READ_SIZE} bytes"
        )
    if (expected_length is not None
            and (type(expected_length) is not int or expected_length < 0)):
        raise ValueError("expected stream length must be a nonnegative integer")
    if (expected_sha256 is not None
            and (not isinstance(expected_sha256, str)
                 or len(expected_sha256) != 64
                 or any(character not in "0123456789abcdef"
                        for character in expected_sha256))):
        raise ValueError("expected stream SHA-256 is invalid")
    if (not isinstance(allowed_nlinks, tuple) or not allowed_nlinks
            or any(type(value) is not int or value < 1
                   for value in allowed_nlinks)):
        raise ValueError("allowed stream link counts are invalid")
    if required_mode is not None and (
            type(required_mode) is not int
            or stat.S_IMODE(required_mode) != required_mode):
        raise ValueError("required stream mode is invalid")

    canonical = Path(path).absolute()
    if not canonical.name:
        raise ValueError("stream source path has no basename")
    parent_fd = open_trusted_directory_fd(canonical.parent)
    fd = None
    try:
        parent_before = require_trusted_directory_fd(
            parent_fd, canonical.parent
        )
        try:
            fd = os.open(
                canonical.name,
                os.O_RDONLY | _required_flag("O_NOFOLLOW")
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=parent_fd,
            )
        except OSError as error:
            if error.errno == errno.ELOOP:
                raise ValueError(
                    f"no-follow stream rejected linked file: {canonical}"
                ) from error
            raise
        before = os.fstat(fd)
        entry_before = os.stat(
            canonical.name, dir_fd=parent_fd, follow_symlinks=False
        )
        if require_owner:
            require_trivial_acl_fd(fd, canonical)
        expected_signature = _regular_stream_signature(before)
        if _regular_stream_signature(entry_before) != expected_signature:
            raise ValueError(
                f"stream descriptor and entry identity differ: {canonical}"
            )
        for value in (before, entry_before):
            _require_stream_file_stat(
                value, canonical, require_owner=require_owner,
                require_read_only=require_read_only,
                required_mode=required_mode,
                allowed_nlinks=allowed_nlinks,
            )
        if (expected_length is not None
                and before.st_size != expected_length):
            raise ValueError(
                f"stream source length {before.st_size} != {expected_length}: "
                f"{canonical}"
            )

        digest = hashlib.sha256()
        length = 0
        while True:
            chunk = os.read(fd, chunk_size)
            if not chunk:
                break
            if len(chunk) > chunk_size:
                raise ValueError(f"stream source returned an oversized read: {canonical}")
            length += len(chunk)
            if expected_length is not None and length > expected_length:
                raise ValueError(f"stream source exceeded expected length: {canonical}")
            digest.update(chunk)
            yield chunk

        after = os.fstat(fd)
        if require_owner:
            require_trivial_acl_fd(fd, canonical)
        try:
            entry_after = os.stat(
                canonical.name, dir_fd=parent_fd, follow_symlinks=False
            )
        except FileNotFoundError as error:
            raise ValueError(
                f"stream source disappeared while reading: {canonical}"
            ) from error
        parent_after = require_trusted_directory_fd(
            parent_fd, canonical.parent
        )
        for value in (after, entry_after):
            _require_stream_file_stat(
                value, canonical, require_owner=require_owner,
                require_read_only=require_read_only,
                required_mode=required_mode,
                allowed_nlinks=allowed_nlinks,
            )
            if _regular_stream_signature(value) != expected_signature:
                raise ValueError(f"stream source changed while reading: {canonical}")
        if (_directory_identity(parent_before)
                != _directory_identity(parent_after)):
            raise ValueError(f"stream source parent changed while reading: {canonical}")
        if length != before.st_size:
            raise ValueError(f"stream source returned a short read: {canonical}")
        if expected_length is not None and length != expected_length:
            raise ValueError(
                f"stream source length {length} != {expected_length}: {canonical}"
            )
        if (expected_sha256 is not None
                and digest.hexdigest() != expected_sha256):
            raise ValueError(f"stream source SHA-256 mismatch: {canonical}")
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent_fd)


def atomic_write_stream(
        path: str | Path, chunks, *, expected_length: int,
        expected_sha256: str, mode: int = 0o600,
        replace: bool = False) -> None:
    """Durably install an exact bounded stream without retaining its bytes."""
    import hashlib

    if type(expected_length) is not int or expected_length < 0:
        raise ValueError("atomic stream length must be a nonnegative integer")
    if (not isinstance(expected_sha256, str) or len(expected_sha256) != 64
            or any(character not in "0123456789abcdef"
                   for character in expected_sha256)):
        raise ValueError("atomic stream SHA-256 is invalid")
    if (type(mode) is not int or stat.S_IMODE(mode) != mode
            or mode & 0o022):
        raise ValueError("atomic stream mode must not be group/world writable")

    canonical = Path(path).absolute()
    parent_fd = open_trusted_directory_fd(
        canonical.parent, create=True, create_mode=0o700
    )
    private_fd = None
    private_name = None
    private_stat = None
    renamed = False
    try:
        flags = (os.O_RDWR | os.O_CREAT | os.O_EXCL
                 | _required_flag("O_NOFOLLOW")
                 | getattr(os, "O_CLOEXEC", 0))
        for _ in range(128):
            candidate = f".{canonical.name}.stream-{secrets.token_hex(24)}"
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
            raise FileExistsError("could not allocate a private stream entry")

        require_trivial_acl_fd(private_fd, canonical)
        digest = hashlib.sha256()
        length = 0
        for raw in chunks:
            if not isinstance(raw, (bytes, bytearray, memoryview)):
                raise ValueError("atomic stream yielded a non-bytes chunk")
            view = memoryview(raw)
            while view:
                piece = view[:MAX_STREAM_READ_SIZE]
                written_view = piece
                while written_view:
                    written = os.write(private_fd, written_view)
                    if written <= 0:
                        raise OSError("atomic stream write made no progress")
                    written_view = written_view[written:]
                digest.update(piece)
                length += len(piece)
                if length > expected_length:
                    raise ValueError("atomic stream exceeded expected length")
                view = view[len(piece):]
        if length != expected_length:
            raise ValueError(
                f"atomic stream length {length} != {expected_length}"
            )
        if digest.hexdigest() != expected_sha256:
            raise ValueError("atomic stream SHA-256 mismatch")
        os.fchmod(private_fd, mode)
        os.fsync(private_fd)

        final_private = os.fstat(private_fd)
        require_trivial_acl_fd(private_fd, canonical)
        private_entry = os.stat(
            private_name, dir_fd=parent_fd, follow_symlinks=False
        )
        identity = (private_stat.st_dev, private_stat.st_ino)
        for value in (final_private, private_entry):
            if (not stat.S_ISREG(value.st_mode)
                    or value.st_uid != os.geteuid()
                    or stat.S_IMODE(value.st_mode) != mode
                    or value.st_nlink != 1
                    or value.st_size != expected_length
                    or (value.st_dev, value.st_ino) != identity):
                raise ValueError(
                    f"private atomic stream entry is not fd-owned: {canonical}"
                )

        os.lseek(private_fd, 0, os.SEEK_SET)
        verify_digest = hashlib.sha256()
        verify_length = 0
        while True:
            chunk = os.read(private_fd, MAX_STREAM_READ_SIZE)
            if not chunk:
                break
            verify_digest.update(chunk)
            verify_length += len(chunk)
        if (verify_length != expected_length
                or verify_digest.hexdigest() != expected_sha256):
            raise ValueError(f"private atomic stream bytes changed: {canonical}")
        require_trusted_directory_fd(parent_fd, canonical.parent)
        if replace:
            os.replace(
                private_name, canonical.name,
                src_dir_fd=parent_fd, dst_dir_fd=parent_fd,
            )
            renamed = True
        else:
            try:
                os.link(
                    private_name, canonical.name,
                    src_dir_fd=parent_fd, dst_dir_fd=parent_fd,
                    follow_symlinks=False,
                )
            except FileExistsError as error:
                raise FileExistsError(
                    f"atomic stream target already exists: {canonical}"
                ) from error
            linked_private = os.fstat(private_fd)
            linked_target = os.stat(
                canonical.name, dir_fd=parent_fd, follow_symlinks=False
            )
            if ((linked_private.st_dev, linked_private.st_ino)
                    != (linked_target.st_dev, linked_target.st_ino)
                    or linked_private.st_nlink != 2
                    or linked_target.st_nlink != 2):
                raise ValueError(
                    f"atomic stream exclusive link is not exact: {canonical}"
                )
            os.fsync(parent_fd)
            os.unlink(private_name, dir_fd=parent_fd)
            renamed = True
        os.fsync(parent_fd)
    finally:
        if (not renamed and private_name is not None
                and private_stat is not None):
            _remove_owned_entry(parent_fd, private_name, private_stat)
        if private_fd is not None:
            os.close(private_fd)
        os.close(parent_fd)

    for _chunk in iter_verified_regular_chunks(
            canonical, require_owner=True, required_mode=mode,
            expected_length=expected_length,
            expected_sha256=expected_sha256):
        pass


def _remove_owned_entry(parent_fd: int, name: str,
                        owned: os.stat_result) -> None:
    try:
        current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if (current.st_dev, current.st_ino) == (owned.st_dev, owned.st_ino):
        os.unlink(name, dir_fd=parent_fd)


def atomic_write_bytes(path: str | Path, data: bytes) -> None:
    """Durably replace a canonical file through one private parent inode.

    The unpredictable private basename and trusted-parent check prevent access
    by other users. Same-UID writers in the directory must still serialize on
    the canonical lock; POSIX has no portable fd-conditioned rename primitive.
    """
    canonical = Path(path).absolute()
    parent_fd = open_trusted_directory_fd(
        canonical.parent, create=True, create_mode=0o700
    )
    private_fd = None
    private_name = None
    private_stat = None
    renamed = False
    try:
        try:
            existing_fd = os.open(
                canonical.name,
                os.O_RDONLY | _required_flag("O_NOFOLLOW")
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=parent_fd,
            )
        except FileNotFoundError:
            existing_fd = None
        if existing_fd is not None:
            try:
                require_trivial_acl_fd(existing_fd, canonical)
            finally:
                os.close(existing_fd)
        flags = (os.O_RDWR | os.O_CREAT | os.O_EXCL
                 | _required_flag("O_NOFOLLOW")
                 | getattr(os, "O_CLOEXEC", 0))
        for _ in range(128):
            candidate = f".{canonical.name}.tmp-{secrets.token_hex(24)}"
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
            raise FileExistsError("could not allocate a private atomic-write entry")

        require_trivial_acl_fd(private_fd, canonical)
        view = memoryview(data)
        while view:
            written = os.write(private_fd, view)
            if written <= 0:
                raise OSError("atomic write made no progress")
            view = view[written:]
        os.fsync(private_fd)

        fd_stat = os.fstat(private_fd)
        require_trivial_acl_fd(private_fd, canonical)
        entry_stat = os.stat(
            private_name, dir_fd=parent_fd, follow_symlinks=False
        )
        identity = (private_stat.st_dev, private_stat.st_ino)
        if (not stat.S_ISREG(private_stat.st_mode)
                or private_stat.st_uid != os.geteuid()
                or stat.S_IMODE(private_stat.st_mode) != 0o600
                or private_stat.st_nlink != 1):
            raise ValueError(
                f"private atomic-write entry is not fd-owned: {canonical}"
            )
        for value in (fd_stat, entry_stat):
            if (not stat.S_ISREG(value.st_mode)
                    or value.st_uid != os.geteuid()
                    or stat.S_IMODE(value.st_mode) != 0o600
                    or value.st_nlink != 1
                    or value.st_size != len(data)
                    or (value.st_dev, value.st_ino) != identity):
                raise ValueError(
                    f"private atomic-write entry is not fd-owned: {canonical}"
                )
        if _read_fd_bytes(private_fd) != data:
            raise ValueError(f"private atomic-write bytes changed: {canonical}")
        require_trusted_directory_fd(parent_fd, canonical.parent)
        os.replace(
            private_name, canonical.name,
            src_dir_fd=parent_fd, dst_dir_fd=parent_fd,
        )
        renamed = True

        target_fd = os.open(
            canonical.name,
            os.O_RDONLY | _required_flag("O_NOFOLLOW")
            | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent_fd,
        )
        try:
            target_stat = os.fstat(target_fd)
            require_trivial_acl_fd(target_fd, canonical)
            target_entry = os.stat(
                canonical.name, dir_fd=parent_fd, follow_symlinks=False
            )
            for value in (os.fstat(private_fd), target_stat, target_entry):
                if (not stat.S_ISREG(value.st_mode)
                        or value.st_uid != os.geteuid()
                        or stat.S_IMODE(value.st_mode) != 0o600
                        or value.st_nlink != 1
                        or value.st_size != len(data)
                        or (value.st_dev, value.st_ino) != identity):
                    raise ValueError(
                        f"atomic target is not the verified private inode: "
                        f"{canonical}"
                    )
            if _read_fd_bytes(target_fd) != data:
                raise ValueError(f"atomic target bytes changed: {canonical}")
        finally:
            os.close(target_fd)
        os.fsync(parent_fd)
    finally:
        if (not renamed and private_name is not None
                and private_stat is not None):
            _remove_owned_entry(parent_fd, private_name, private_stat)
        if private_fd is not None:
            os.close(private_fd)
        os.close(parent_fd)


def read_regular_bytes(path: str | Path, *,
                       require_owner_only: bool = False) -> bytes:
    """Read one no-follow regular file through its descriptor-opened parent."""
    canonical = Path(path).absolute()
    parent_fd = open_directory_fd(canonical.parent)
    fd = None
    try:
        fd = os.open(
            canonical.name,
            os.O_RDONLY | _required_flag("O_NOFOLLOW")
            | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent_fd,
        )
        value = os.fstat(fd)
        entry = os.stat(
            canonical.name, dir_fd=parent_fd, follow_symlinks=False
        )
        if (not stat.S_ISREG(value.st_mode)
                or not stat.S_ISREG(entry.st_mode)
                or (value.st_dev, value.st_ino) != (entry.st_dev, entry.st_ino)):
            raise ValueError(f"not one stable regular file: {canonical}")
        if require_owner_only:
            require_trivial_acl_fd(fd, canonical)
        data = _read_fd_bytes(fd)
        after = os.fstat(fd)
        entry_after = os.stat(
            canonical.name, dir_fd=parent_fd, follow_symlinks=False
        )
        if (_lock_identity(value) != _lock_identity(after)
                or _lock_identity(value) != _lock_identity(entry_after)
                or value.st_size != len(data)
                or after.st_size != len(data)):
            raise ValueError(f"file changed while reading: {canonical}")
        if require_owner_only:
            require_trivial_acl_fd(fd, canonical)
            for current in (value, entry, after, entry_after):
                if (current.st_uid != os.geteuid()
                        or stat.S_IMODE(current.st_mode) != 0o600
                        or current.st_nlink != 1):
                    raise ValueError(
                        f"file is not owner-only and single-link: {canonical}"
                    )
        return data
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent_fd)


def write_idempotent_bytes(path: str | Path, data: bytes) -> None:
    """Install exact bytes once; reject a different existing canonical image."""
    canonical = Path(path).absolute()
    parent_fd = open_trusted_directory_fd(
        canonical.parent, create=True, create_mode=0o700
    )
    os.close(parent_fd)
    try:
        existing = read_regular_bytes(canonical, require_owner_only=True)
    except FileNotFoundError:
        atomic_write_bytes(canonical, data)
        return
    if existing != data:
        raise ValueError(f"idempotent artifact has different bytes: {path}")
