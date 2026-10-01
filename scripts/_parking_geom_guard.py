#!/usr/bin/env python3
"""Shared persistent gate for Trekdex canonical parking/geom writers.

Every cooperating writer takes the same exclusive geom resource and a shared
lease on the derived live-journal resource.  The journal check happens while
both leases are held, so a durable PREPARED sweep remains authoritative after
the sweep process and its ephemeral locks are gone.
"""
from __future__ import annotations

import fcntl
import json
import os
import stat
import sys
import threading
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
_TOOLS = _SCRIPTS / "parking-adjud" / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

import trust_resolution as tr  # noqa: E402
import trusted_filesystem as trusted_fs  # noqa: E402

ROOT = _SCRIPTS.parent
CANONICAL_GEOM_DIR = (ROOT / "public" / "areas" / "geom").resolve()
CANONICAL_SIDECAR = (ROOT / "public" / "areas" / "parking-verdicts.json").resolve()
TRANSACTION_DIRNAME = ".parking-sweep-transactions"
LIVE_JOURNAL_NAME = "live.journal.json"

# ContextVar propagation keeps a whole synchronous/async execution on one
# lease. The recorded process/thread owner prevents a copied context in a
# worker thread or forked process from bypassing the underlying file locks.
_ACTIVE_WRITER_LEASES: ContextVar[tuple] = ContextVar(
    "parking_geom_writer_leases", default=(),
)


class LiveSweepInProgress(RuntimeError):
    """A durable PREPARED parking sweep blocks a cooperating writer."""


class _WriterLease:
    """Mutable capability shared by copied contexts and invalidated on exit."""

    __slots__ = ("geom", "owner", "entries", "active")

    def __init__(self, geom: Path, owner: tuple[int, int], entries) -> None:
        self.geom = geom
        self.owner = owner
        self.entries = entries
        self.active = True


class StaleWriterLease(RuntimeError):
    """A copied execution context retained a lease after its locks closed."""


def canonical_path(value: str | os.PathLike) -> Path:
    return Path(value).expanduser().absolute().resolve()


def transaction_root(geom_dir: str | os.PathLike) -> Path:
    return canonical_path(geom_dir).parent / TRANSACTION_DIRNAME


def live_journal_path(geom_dir: str | os.PathLike) -> Path:
    return transaction_root(geom_dir) / LIVE_JOURNAL_NAME


def is_canonical_geom_dir(geom_dir: str | os.PathLike) -> bool:
    return canonical_path(geom_dir) == CANONICAL_GEOM_DIR


def require_no_live_sweep(geom_dir: str | os.PathLike) -> None:
    """Fail closed when the derived owner-only live journal entry exists."""
    root = transaction_root(geom_dir)
    try:
        root_fd = trusted_fs.open_trusted_directory_fd(root)
    except FileNotFoundError:
        return
    try:
        root_stat = trusted_fs.require_trusted_directory_fd(root_fd, root)
        if stat.S_IMODE(root_stat.st_mode) != 0o700:
            raise LiveSweepInProgress(
                f"parking sweep transaction root is not owner-only mode 0700: {root}"
            )
        try:
            os.stat(LIVE_JOURNAL_NAME, dir_fd=root_fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        raise LiveSweepInProgress(
            f"live parking sweep journal blocks canonical geom writes: "
            f"{root / LIVE_JOURNAL_NAME}"
        )
    finally:
        os.close(root_fd)


def writer_resource_modes(
        geom_dir: str | os.PathLike, additional_resource_modes=(),
) -> tuple[tuple[Path, int], ...]:
    """Return one globally sortable writer lease, including persistent gate."""
    geom = canonical_path(geom_dir)
    return tuple(
        (canonical_path(resource), mode)
        for resource, mode in additional_resource_modes
    ) + (
        (geom, fcntl.LOCK_EX),
        (live_journal_path(geom), fcntl.LOCK_SH),
    )


def _lease_covers(active_entries, requested_entries) -> bool:
    held_modes = {
        resource: mode for _lock_path, resource, mode in active_entries
    }
    for _lock_path, resource, requested_mode in requested_entries:
        held_mode = held_modes.get(resource)
        if held_mode is None:
            return False
        if requested_mode == fcntl.LOCK_EX and held_mode != fcntl.LOCK_EX:
            return False
    return True


@contextmanager
def geom_writer(
        geom_dir: str | os.PathLike, *, additional_resource_modes=(),
):
    """Hold the common writer lease and reject a durable live transaction."""
    geom = canonical_path(geom_dir)
    resources = writer_resource_modes(geom, additional_resource_modes)
    requested_entries = trusted_fs.resource_lock_entries(
        ROOT, resources, tr.resource_lock_path,
    )
    owner = (os.getpid(), threading.get_ident())
    active_leases = _ACTIVE_WRITER_LEASES.get()
    for lease in reversed(active_leases):
        if lease.geom != geom or lease.owner != owner:
            continue
        if not lease.active:
            raise StaleWriterLease(
                "copied context retained an expired geom writer lease"
            )
        if not _lease_covers(lease.entries, requested_entries):
            raise RuntimeError(
                "nested geom writer requested resources outside its active lease"
            )
        require_no_live_sweep(geom)
        yield lease.entries
        return

    with trusted_fs.locked_resources(
            ROOT, resources, tr.resource_lock_path) as entries:
        require_no_live_sweep(geom)
        lease = _WriterLease(geom, owner, entries)
        token = _ACTIVE_WRITER_LEASES.set(active_leases + (lease,))
        try:
            yield entries
        finally:
            # A copied Context inherits the same object. Invalidate it before
            # the underlying handles are released so stale re-entry can never
            # mistake historical lock metadata for a live capability.
            lease.active = False
            _ACTIVE_WRITER_LEASES.reset(token)


def canonical_geom_writer(
        geom_dir: str | os.PathLike, *, enabled: bool = True,
        additional_resource_modes=(),
):
    """Guard only the canonical output; artifact/staging output stays separate."""
    if not enabled or not is_canonical_geom_dir(geom_dir):
        return nullcontext()
    return geom_writer(
        geom_dir, additional_resource_modes=additional_resource_modes,
    )


def _within(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
        return True
    except ValueError:
        return False


def validate_sidecar_output_path(output_path: str | os.PathLike) -> Path:
    """Reject any sidecar-builder destination in canonical geom/control state."""
    output = canonical_path(output_path)
    geom = CANONICAL_GEOM_DIR
    transactions = transaction_root(geom)
    if (output == geom or _within(output, geom)
            or output == transactions or _within(output, transactions)):
        raise ValueError(
            "parking sidecar output may not target canonical geom or its "
            "sweep transaction tree"
        )
    return output


def sidecar_publisher_resource_modes(
        output_path: str | os.PathLike,
) -> tuple[tuple[Path, int], ...]:
    """Join the canonical sidecar publisher to the live-journal lease set."""
    output = validate_sidecar_output_path(output_path)
    if output != CANONICAL_SIDECAR:
        return ()
    return ((live_journal_path(CANONICAL_GEOM_DIR), fcntl.LOCK_SH),)


def require_sidecar_publish_allowed(output_path: str | os.PathLike) -> None:
    output = validate_sidecar_output_path(output_path)
    if output == CANONICAL_SIDECAR:
        require_no_live_sweep(CANONICAL_GEOM_DIR)


def atomic_write_bytes(path: str | os.PathLike, data: bytes) -> None:
    trusted_fs.atomic_write_bytes(canonical_path(path), data)


def atomic_write_json(
        path: str | os.PathLike, value: object, *,
        separators: tuple[str, str] | None = None,
) -> None:
    options = {"ensure_ascii": True, "allow_nan": False}
    if separators is not None:
        options["separators"] = separators
    atomic_write_bytes(path, json.dumps(value, **options).encode("utf-8"))
