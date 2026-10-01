#!/usr/bin/env python3
"""Build judge packets, bind checkpoints to their inputs, and schedule safe fan-out.

    python3 judge_packets.py <slug> [--chunk N] [--tiles DIR]
                             [--skip-judged STORE ...]
                             [--adopt-existing] [--status-json]

For each chunk this writes the packet JSON plus either:
- a START/RESUME prompt whose agent-owned output is a separate
  `<slug>_verdict_continue_NN.json`; or
- a minimal COMPLETE prompt that cannot accidentally rewrite the canonical
  `<slug>_verdict_draft_NN.json`.

Canonical drafts are host-owned. On the next run, a valid continuation prefix
is appended atomically from one stable capture while preserving existing bytes.
That exact image is archived under its full hash; the live entry is moved—not
deleted—into owner-only quarantine. A newer entry that wins the final retirement
race is preserved and reported as recovery-required. A checkpoint manifest
binds every completed prefix to a
SHA-256 over the complete packet, judge rules/prompt, and Z1/Z2/Z3 tile bytes.
Changed decision inputs, malformed rows, changed prefixes, stale aliases, and
missing imagery all fail closed before generated artifacts or drafts change.

Existing pre-manifest drafts require one explicit `--adopt-existing` run after
independent validation. `--status-json` emits one JSON object per chunk and no
human-oriented status prose, for orchestration.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import secrets
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from judge_validation import (  # noqa: E402
    canonical_draft_files,
    machine_decision_projection,
    original_judge_projection,
    validate_verdict_row,
)
import dossier_output  # noqa: E402
import trust_resolution as tr  # noqa: E402
import trusted_filesystem as trusted_fs  # noqa: E402

PADJ_TMP = os.environ.get("PADJ_TMP") or os.path.join(_HERE, "..", "work")
NON_PUBLIC_ACCESS = ("private", "no", "customers")
PROMPT_TEMPLATE = os.path.join(_HERE, "judge_agent_prompt.md")
CHECKPOINT_VERSION = 2


@dataclass(frozen=True)
class _FileSignature:
    device: int
    inode: int
    mode: int
    links: int
    size: int
    mtime_ns: int
    ctime_ns: int


@dataclass(frozen=True)
class _ContinuationCapture:
    path: Path
    raw: bytes
    rows: object
    sha256: str
    parent_identity: tuple[int, int]
    fd_before: _FileSignature
    fd_after: _FileSignature
    entry_before: _FileSignature
    entry_after: _FileSignature


@dataclass(frozen=True)
class _ChunkProposal:
    mode: str | None
    state: dict
    draft_raw: bytes | None
    manifest: dict
    continuation: _ContinuationCapture | None


def _file_signature(value: os.stat_result) -> _FileSignature:
    return _FileSignature(
        device=value.st_dev,
        inode=value.st_ino,
        mode=value.st_mode,
        links=value.st_nlink,
        size=value.st_size,
        mtime_ns=value.st_mtime_ns,
        ctime_ns=value.st_ctime_ns,
    )


def _open_parent_fd(path: Path) -> int:
    return trusted_fs.open_trusted_directory_fd(path, create=False)


def _read_fd_bytes(fd: int) -> bytes:
    chunks = []
    while True:
        chunk = os.read(fd, 1024 * 1024)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _capture_continuation(path: Path) -> _ContinuationCapture | None:
    """Read one agent-owned continuation once and bind it to its live entry."""
    parent_fd = _open_parent_fd(path.parent)
    fd = None
    try:
        parent_stat = os.fstat(parent_fd)
        parent_identity = (parent_stat.st_dev, parent_stat.st_ino)
        flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        try:
            fd = os.open(path.name, flags, dir_fd=parent_fd)
        except FileNotFoundError:
            return None
        fd_before = _file_signature(os.fstat(fd))
        entry_before = _file_signature(os.stat(
            path.name, dir_fd=parent_fd, follow_symlinks=False
        ))
        if not stat.S_ISREG(fd_before.mode) or not stat.S_ISREG(entry_before.mode):
            raise ValueError(f"{path.name} is not a regular file")
        if (fd_before.device, fd_before.inode) != (
                entry_before.device, entry_before.inode):
            raise ValueError(f"{path.name} changed while it was opened")

        raw = _read_fd_bytes(fd)
        fd_after = _file_signature(os.fstat(fd))
        try:
            entry_after = _file_signature(os.stat(
                path.name, dir_fd=parent_fd, follow_symlinks=False
            ))
        except FileNotFoundError as exc:
            raise ValueError(f"{path.name} disappeared during secure capture") from exc
        if not stat.S_ISREG(fd_after.mode) or not stat.S_ISREG(entry_after.mode):
            raise ValueError(f"{path.name} stopped being a regular file during secure capture")
        if not (fd_before == fd_after == entry_before == entry_after):
            raise ValueError(f"{path.name} changed during secure capture")
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent_fd)

    try:
        rows = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"cannot parse {path.name}: {exc}") from exc
    return _ContinuationCapture(
        path=path,
        raw=raw,
        rows=rows,
        sha256=_sha256(raw),
        parent_identity=parent_identity,
        fd_before=fd_before,
        fd_after=fd_after,
        entry_before=entry_before,
        entry_after=entry_after,
    )


def _validate_capture_entry(parent_fd: int, capture: _ContinuationCapture) -> None:
    parent_stat = os.fstat(parent_fd)
    if (parent_stat.st_dev, parent_stat.st_ino) != capture.parent_identity:
        raise ValueError(f"{capture.path.name} parent directory changed after capture")
    try:
        current = _file_signature(os.stat(
            capture.path.name, dir_fd=parent_fd, follow_symlinks=False
        ))
    except FileNotFoundError as exc:
        raise ValueError(f"{capture.path.name} disappeared after capture") from exc
    if not stat.S_ISREG(current.mode):
        raise ValueError(f"{capture.path.name} is no longer a regular file")
    if current != capture.entry_after:
        raise ValueError(f"{capture.path.name} changed after capture")


def _recheck_continuation(capture: _ContinuationCapture) -> None:
    parent_fd = _open_parent_fd(capture.path.parent)
    try:
        _validate_capture_entry(parent_fd, capture)
    finally:
        os.close(parent_fd)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_sha(value: object) -> str:
    return _sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                              ensure_ascii=False).encode())


def _atomic_write(path: Path, data: bytes) -> None:
    """Write canonical bytes inside a trusted, lock-serialized parent."""
    trusted_fs.atomic_write_bytes(path, data)


def _atomic_json(path: Path, value: object) -> None:
    _atomic_write(path, (json.dumps(value, indent=1, ensure_ascii=False) + "\n").encode())


def render_prompt_template(path: str = PROMPT_TEMPLATE) -> str:
    text = Path(path).read_text()
    marker = "\n---\n"
    if marker in text:
        text = text.split(marker, 1)[1]
    for placeholder in ("{PROTOCOL_PATH}", "{LESSONS_PATH}", "{CHUNK_PATH}",
                        "{OUT_PATH}", "{RESUME_BLOCK}"):
        if placeholder not in text:
            raise ValueError(f"{path} lost its {placeholder} placeholder")
    return text


def _packet_source_generation(chunk: list[dict]) -> dict:
    if not isinstance(chunk, list) or not chunk:
        raise ValueError("packet chunk has no source_generation binding")
    expected = None
    for packet in chunk:
        if not isinstance(packet, dict):
            raise ValueError("packet chunk contains a non-object packet")
        area = packet.get("area")
        binding = dossier_output.validate_source_generation(
            packet.get("source_generation"), area
        )
        if expected is None:
            expected = binding
        elif binding != expected:
            raise ValueError("packet source_generation bindings are not identical")
    assert expected is not None
    return expected


def build_packets(slug: str, tmp: str, tiles_dir: str | None,
                  generation_capture) -> dict[int, dict]:
    dossier = dossier_output.captured_generation_document(
        generation_capture, "dossier"
    )
    serves = dossier_output.captured_generation_document(
        generation_capture, "serves"
    )
    context = dossier_output.captured_generation_document(
        generation_capture, "context"
    )
    walk = dossier_output.captured_generation_document(
        generation_capture, "walk"
    )
    source_generation = dossier_output.portable_source_generation(
        generation_capture, slug
    )
    if not isinstance(dossier, dict):
        raise ValueError("captured dossier must be an object")
    if not all(isinstance(value, dict) for value in (serves, context, walk)):
        raise ValueError("captured dossier sidecars must be objects")
    tiles = tiles_dir or os.path.join(tmp, f"{slug}_ladder")
    packets: dict[int, dict] = {}
    for facility in dossier["facilities"]:
        fid = facility["fid"]
        served = serves.get(str(fid)) or {}
        if not served.get("served"):
            continue
        if (facility.get("tags_union") or {}).get("access") in NON_PUBLIC_ACCESS:
            continue
        route = walk.get(str(fid)) or facility.get("walk") or {}
        nearby = context.get(str(fid)) or {}
        packets[fid] = {
            "fid": fid,
            "area": slug,
            "source_generation": {
                "manifest_path": source_generation["manifest_path"],
                "manifest_sha256": source_generation["manifest_sha256"],
                "artifact_sha256": dict(source_generation["artifact_sha256"]),
            },
            "osm": facility.get("osm") or [],
            "prior": facility.get("prior"),
            "descriptive_tags": facility.get("descriptive") or [],
            "tags_union": facility.get("tags_union") or {},
            "members": [{"osm": member.get("osm"), "tags": member.get("tags") or {}}
                        for member in facility.get("members") or []],
            "mixed_access": bool(facility.get("mixed_access")),
            "footprint": "polygon" if (facility.get("ring") or facility.get("rings")) else "node-only",
            "area_m2": facility.get("area_m2"),
            "serves": {"trail": served.get("trail"), "edge_m": served.get("dist_m"),
                       "fallback": bool(served.get("fallback")), "n_trails_in_range": served.get("n")},
            "walk": {"walk_m": route.get("walk_m"), "conn": route.get("conn"),
                     "trail": route.get("trail")},
            "trailhead_nodes_120m": facility.get("trailhead_nodes") or [],
            "footways_60m": facility.get("footways_60m"),
            "building_overlap": bool(facility.get("building_overlap")),
            "context": {"category": nearby.get("category"), "evidence": nearby.get("evidence"),
                        "facility": nearby.get("fac_label"), "facility_edge_m": nearby.get("fac_area_m")},
            "tiles": {zoom: os.path.join(tiles, f"{fid:04d}_{zoom}.png")
                      for zoom in ("z1", "z2", "z3")},
        }
    return packets


def decision_fingerprint(chunk: list[dict]) -> tuple[str | None, list[str]]:
    """Hash every decision input, including generation, imagery, and rules."""
    missing: list[str] = []
    try:
        source_generation = _packet_source_generation(chunk)
    except (TypeError, ValueError) as error:
        source_generation = None
        missing.append(str(error))
    packet_rows = []
    for packet in chunk:
        tile_hashes = {}
        tiles = packet.get("tiles")
        if not isinstance(tiles, dict):
            missing.append(f"fid {packet.get('fid')}: tiles is not an object")
            tiles = {}
        for zoom in ("z1", "z2", "z3"):
            path = tiles.get(zoom)
            if not isinstance(path, str) or not os.path.isfile(path):
                missing.append(str(path or f"fid {packet.get('fid')} missing {zoom}"))
            else:
                tile_hashes[zoom] = _sha256(Path(path).read_bytes())
        packet_rows.append({
            "packet": {
                key: value for key, value in packet.items()
                if key not in ("tiles", "source_generation")
            },
            "tile_sha256": tile_hashes,
        })
    rule_paths = {
        "protocol": Path(_HERE, "judge_protocol.md"),
        "lessons": Path(_HERE, "judge_lessons.md"),
        "prompt": Path(PROMPT_TEMPLATE),
    }
    rules = {}
    for name, path in rule_paths.items():
        if not path.is_file():
            missing.append(str(path))
        else:
            rules[name] = _sha256(path.read_bytes())
    if missing:
        return None, missing
    return _canonical_sha({
        "source_generation": source_generation,
        "rules": rules,
        "packets": packet_rows,
    }), []


def _row_sha(projection: dict) -> str:
    return _canonical_sha(projection)


def _state(status: str, completed: list[int], missing: list[int], errors=None,
           row_sha256=None, resolution_sha256=None, file_sha256=None, rows=None) -> dict:
    return {
        "status": status,
        "completed": completed,
        "missing": missing,
        "errors": errors or [],
        "row_sha256": row_sha256 or [],
        "resolution_sha256": resolution_sha256 or [],
        "file_sha256": file_sha256,
        "rows": rows or [],
    }


def inspect_rows(chunk: list[dict], rows: object, source: str,
                 allow_override: bool = True) -> dict:
    expected = [packet["fid"] for packet in chunk]
    if not isinstance(rows, list):
        return _state("invalid", [], expected, [f"{source} must contain a JSON list"])
    completed: list[int] = []
    row_hashes: list[str] = []
    resolution_hashes: list[str] = []
    errors: list[str] = []
    seen: set[int] = set()
    packets = {packet["fid"]: packet for packet in chunk}
    for position, row in enumerate(rows):
        label = f"{source} row {position}"
        fid = row.get("fid") if isinstance(row, dict) else None
        if type(fid) is not int:
            errors.append(f"{label}: fid is not an integer: {fid!r}")
            continue
        if fid in seen:
            errors.append(f"{label}: duplicates fid {fid}")
            continue
        seen.add(fid)
        completed.append(fid)
        packet = packets.get(fid)
        if packet is None:
            errors.append(f"{label}: extra/stale fid {fid}")
            continue
        row_errors, projection = validate_verdict_row(row, packet, allow_override=allow_override)
        errors.extend(f"{label} fid {fid}: {error}" for error in row_errors)
        if projection is not None:
            row_hashes.append(_row_sha(projection))
        machine, _ = machine_decision_projection(row)
        if machine is not None:
            resolution_hashes.append(_row_sha(machine))
    if completed != expected[:len(completed)]:
        errors.append(f"{source} fids {completed} are not packet-order prefix {expected[:len(completed)]}")
    if len(rows) > len(chunk):
        errors.append(f"{source} has {len(rows)} rows for a {len(chunk)}-packet sequence")
    missing = expected[len(completed):] if not errors else expected
    return _state("invalid" if errors else ("complete" if not missing else "partial"),
                  completed, missing, errors, row_sha256=row_hashes,
                  resolution_sha256=resolution_hashes, rows=rows)


def _inspect_bytes(chunk: list[dict], raw: bytes, source: str,
                   allow_override: bool = True) -> dict:
    expected = [packet["fid"] for packet in chunk]
    try:
        rows = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return _state("invalid", [], expected, [f"cannot parse {source}: {exc}"])
    state = inspect_rows(chunk, rows, source, allow_override=allow_override)
    state["file_sha256"] = _sha256(raw)
    state["raw_bytes"] = raw
    return state


def _inspect_continuation(chunk: list[dict], capture: _ContinuationCapture,
                          allow_override: bool = False) -> dict:
    state = inspect_rows(
        chunk, capture.rows, capture.path.name, allow_override=allow_override
    )
    state["file_sha256"] = capture.sha256
    state["raw_bytes"] = capture.raw
    return state


def inspect_draft(chunk: list[dict], path: str | Path,
                  allow_override: bool = True) -> dict:
    draft_path = Path(path)
    expected = [packet["fid"] for packet in chunk]
    if not draft_path.exists():
        return _state("missing", [], expected)
    try:
        raw = draft_path.read_bytes()
    except OSError as exc:
        return _state("invalid", [], expected, [f"cannot parse {draft_path.name}: {exc}"])
    return _inspect_bytes(chunk, raw, draft_path.name, allow_override=allow_override)


def _manifest_path(tmp: str | Path, slug: str, chunk_index: int) -> Path:
    return Path(tmp, f"{slug}_checkpoint_{chunk_index:02d}.json")


def _draft_path(tmp: str | Path, slug: str, chunk_index: int) -> Path:
    return Path(tmp, f"{slug}_verdict_draft_{chunk_index:02d}.json")


def _continue_path(tmp: str | Path, slug: str, chunk_index: int) -> Path:
    return Path(tmp, f"{slug}_verdict_continue_{chunk_index:02d}.json")


CHECKPOINT_V1_LEGACY_KEYS = frozenset({
    "version", "area", "chunk", "fids", "decision_sha256", "completed",
    "draft_sha256", "judge_row_sha256",
})
CHECKPOINT_V1_CURRENT_KEYS = CHECKPOINT_V1_LEGACY_KEYS | {"resolution_row_sha256"}
CHECKPOINT_V2_CURRENT_KEYS = CHECKPOINT_V1_CURRENT_KEYS | {"source_generation"}
_CHECKPOINT_HEX = frozenset("0123456789abcdef")


def _checkpoint_sha(value: object) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(character in _CHECKPOINT_HEX for character in value))


def _manifest_resolution_hashes(value: dict) -> object:
    if "resolution_row_sha256" in value:
        return value.get("resolution_row_sha256")
    return value.get("judge_row_sha256")


def manifest_value(slug: str, chunk_index: int, chunk: list[dict], decision_sha256: str,
                   state: dict) -> dict:
    return {
        "version": CHECKPOINT_VERSION,
        "area": slug,
        "chunk": chunk_index,
        "fids": [packet["fid"] for packet in chunk],
        "source_generation": _packet_source_generation(chunk),
        "decision_sha256": decision_sha256,
        "completed": state["completed"],
        "draft_sha256": state["file_sha256"],
        "judge_row_sha256": state["row_sha256"],
        "resolution_row_sha256": state["resolution_sha256"],
    }


def validate_manifest_static(value: object, slug: str, chunk_index: int,
                             chunk: list[dict], decision_sha256: str) -> list[str]:
    """Validate the exact generation-bound current checkpoint schema."""
    if not isinstance(value, dict):
        return ["checkpoint manifest must be an object"]
    errors = []
    keys = frozenset(value)
    if keys != CHECKPOINT_V2_CURRENT_KEYS:
        errors.append(
            "checkpoint manifest keys are not the exact current v2 schema: "
            f"{sorted(keys)}"
        )

    version = value.get("version")
    if type(version) is not int or version != CHECKPOINT_VERSION:
        errors.append(
            f"checkpoint version changed: {version!r} != {CHECKPOINT_VERSION!r}"
        )
    area = value.get("area")
    if not isinstance(area, str) or area != slug:
        errors.append(f"checkpoint area changed: {area!r} != {slug!r}")
    stored_chunk = value.get("chunk")
    if type(stored_chunk) is not int or stored_chunk != chunk_index:
        errors.append(
            f"checkpoint chunk changed: {stored_chunk!r} != {chunk_index!r}"
        )

    try:
        expected_generation = _packet_source_generation(chunk)
    except (TypeError, ValueError) as error:
        errors.append(f"checkpoint packet source_generation is invalid: {error}")
        expected_generation = None
    try:
        stored_generation = dossier_output.validate_source_generation(
            value.get("source_generation"), slug
        )
    except (TypeError, ValueError) as error:
        errors.append(f"checkpoint source_generation is invalid: {error}")
        stored_generation = None
    if (expected_generation is not None and stored_generation is not None
            and stored_generation != expected_generation):
        errors.append("checkpoint source_generation differs from packet generation")

    expected_fids = [packet["fid"] for packet in chunk]
    fids = value.get("fids")
    if not isinstance(fids, list) or any(type(fid) is not int for fid in fids):
        errors.append("checkpoint fids must be an exact integer list")
    elif fids != expected_fids:
        errors.append(f"checkpoint fids changed: {fids!r} != {expected_fids!r}")

    claimed_decision = value.get("decision_sha256")
    if not _checkpoint_sha(claimed_decision):
        errors.append("checkpoint decision_sha256 is not a lowercase SHA-256")
    elif claimed_decision != decision_sha256:
        errors.append(
            f"checkpoint decision_sha256 changed: {claimed_decision!r} != {decision_sha256!r}"
        )

    completed = value.get("completed")
    completed_is_int_list = (
        isinstance(completed, list)
        and all(type(fid) is int for fid in completed)
    )
    if not completed_is_int_list:
        errors.append("checkpoint completed must be an exact integer list")
    elif completed != expected_fids[:len(completed)]:
        errors.append("checkpoint completed fids are not a packet-order prefix")

    draft_sha = value.get("draft_sha256")
    if draft_sha is not None and not _checkpoint_sha(draft_sha):
        errors.append("checkpoint draft_sha256 is neither null nor a lowercase SHA-256")

    judge_hashes = value.get("judge_row_sha256")
    judge_hashes_valid = (
        isinstance(judge_hashes, list)
        and all(_checkpoint_sha(item) for item in judge_hashes)
    )
    if not judge_hashes_valid:
        errors.append("checkpoint judge_row_sha256 is not a SHA-256 list")
    elif completed_is_int_list and len(judge_hashes) != len(completed):
        errors.append("checkpoint judge_row_sha256 length does not match completed")

    if "resolution_row_sha256" in value:
        resolution_hashes = value.get("resolution_row_sha256")
        resolution_hashes_valid = (
            isinstance(resolution_hashes, list)
            and all(_checkpoint_sha(item) for item in resolution_hashes)
        )
        if not resolution_hashes_valid:
            errors.append("checkpoint resolution_row_sha256 is not a SHA-256 list")
        elif completed_is_int_list and len(resolution_hashes) != len(completed):
            errors.append("checkpoint resolution_row_sha256 length does not match completed")
    return errors


def validate_manifest(value: object, slug: str, chunk_index: int, chunk: list[dict],
                      decision_sha256: str, state: dict) -> list[str]:
    errors = validate_manifest_static(value, slug, chunk_index, chunk, decision_sha256)
    if errors or not isinstance(value, dict):
        return errors
    if value.get("completed") != state["completed"]:
        errors.append(f"checkpoint completed fids {value.get('completed')!r} != {state['completed']!r}")
    if value.get("judge_row_sha256") != state["row_sha256"]:
        errors.append("canonical draft's original judge rows changed")
    stored_resolution = _manifest_resolution_hashes(value)
    if stored_resolution != state["resolution_sha256"]:
        errors.append("canonical draft's machine resolution rows changed")
    # Byte changes are tolerated only when the original and machine projections
    # are unchanged (the normal human override lifecycle). The refreshed
    # hash is written after the whole preflight passes.
    return errors


def strict_work_files(tmp: str | Path, slug: str, chunk_count: int) -> list[str]:
    root = Path(tmp)
    expected_drafts = {_draft_path(root, slug, index) for index in range(chunk_count)}
    expected_continuations = {_continue_path(root, slug, index) for index in range(chunk_count)}
    expected_manifests = {_manifest_path(root, slug, index) for index in range(chunk_count)}
    checks = [
        (root.glob(f"{slug}_verdict_draft*.json"), expected_drafts, "draft"),
        (root.glob(f"{slug}_verdict_continue*.json"), expected_continuations, "continuation"),
        (root.glob(f"{slug}_checkpoint*.json"), expected_manifests, "checkpoint"),
    ]
    errors = []
    for candidates, expected, kind in checks:
        extras = sorted(path for path in candidates if path not in expected)
        if extras:
            errors.append(f"noncanonical/stale {kind} file(s): " + ", ".join(path.name for path in extras))
    # This also rejects a legacy consolidated draft mixed into a numbered run.
    try:
        files = canonical_draft_files(root, slug)
        if files and files != sorted(path for path in expected_drafts if path.exists()):
            errors.append("canonical draft layout does not match the current numbered chunks")
    except ValueError as exc:
        errors.append(str(exc))
    return errors


def _append_json_arrays(existing: bytes | None, addition: bytes,
                        addition_rows: object | None = None) -> bytes:
    """Append list members while retaining every existing prefix byte."""
    added = json.loads(addition) if addition_rows is None else addition_rows
    if not isinstance(added, list) or not added:
        raise ValueError("continuation must contain a non-empty JSON list")
    if existing is None:
        return addition
    current = json.loads(existing)
    if not isinstance(current, list):
        raise ValueError("canonical draft must contain a JSON list")
    if not current:
        return addition
    close_existing = len(existing.rstrip()) - 1
    stripped_addition = addition.rstrip()
    open_addition = next(index for index, byte in enumerate(stripped_addition) if chr(byte).strip())
    close_addition = len(stripped_addition) - 1
    if existing[close_existing:close_existing + 1] != b"]" or stripped_addition[open_addition:open_addition + 1] != b"[":
        raise ValueError("draft/continuation roots must be JSON lists")
    inner = stripped_addition[open_addition + 1:close_addition]
    return existing[:close_existing] + b"," + inner + existing[close_existing:]


def _capture_quarantined_entry(parent_fd: int, name: str,
                                path: Path) -> tuple[_FileSignature, bytes]:
    """Read a moved quarantine entry stably without following a replacement."""
    fd = None
    try:
        flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        fd = os.open(name, flags, dir_fd=parent_fd)
        fd_before = _file_signature(os.fstat(fd))
        entry_before = _file_signature(os.stat(
            name, dir_fd=parent_fd, follow_symlinks=False
        ))
        if (not stat.S_ISREG(fd_before.mode)
                or not stat.S_ISREG(entry_before.mode)
                or (fd_before.device, fd_before.inode)
                != (entry_before.device, entry_before.inode)):
            raise ValueError(f"quarantined continuation is not regular: {path}")
        raw = _read_fd_bytes(fd)
        fd_after = _file_signature(os.fstat(fd))
        entry_after = _file_signature(os.stat(
            name, dir_fd=parent_fd, follow_symlinks=False
        ))
        if not (fd_before == entry_before == fd_after == entry_after):
            raise ValueError(f"quarantined continuation changed while reading: {path}")
        if len(raw) != fd_after.size:
            raise ValueError(f"quarantined continuation size changed: {path}")
        return fd_after, raw
    finally:
        if fd is not None:
            os.close(fd)


def _new_quarantine_directory(capture: _ContinuationCapture,
                              archive: Path) -> tuple[int, int, Path]:
    """Allocate one unpredictable 0700 retirement directory."""
    root = archive.parent / "continuation-quarantine"
    root_fd = trusted_fs.open_trusted_directory_fd(
        root, create=True, create_mode=0o700
    )
    child_fd = None
    try:
        for _ in range(128):
            name = (
                f"{capture.path.stem}.{capture.sha256[:16]}."
                f"{secrets.token_hex(16)}"
            )
            try:
                child_fd = trusted_fs.create_trusted_directory_fd(
                    root_fd, root, name, create_mode=0o700
                )
            except FileExistsError:
                continue
            child = root / name
            trusted_fs.require_trusted_directory_fd(child_fd, child)
            return root_fd, child_fd, child
        raise FileExistsError("could not allocate a continuation quarantine")
    except BaseException:
        if child_fd is not None:
            try:
                os.close(child_fd)
            except BaseException:
                pass
        try:
            os.close(root_fd)
        except BaseException:
            pass
        raise


def _archive_continuation(capture: _ContinuationCapture) -> Path:
    """Preserve captured bytes, then quarantine—not unlink—the live entry.

    The capture archive is deterministic and idempotent under the full content
    hash. The final source-name race is resolved by moving whichever entry is
    present into an unpredictable dedicated directory. A newer same-UID agent
    entry can win that race, but it is retained and reported; it is never
    admitted into canonical draft/checkpoint bytes and is never deleted.
    """
    _recheck_continuation(capture)
    path = capture.path
    archive = path.parent / "Archive"
    target = archive / f"{path.stem}.captured-{capture.sha256}.json"
    trusted_fs.write_idempotent_bytes(target, capture.raw)

    source_fd = _open_parent_fd(path.parent)
    quarantine_root_fd = None
    quarantine_fd = None
    quarantine_directory = None
    moved_path = None
    try:
        quarantine_root_fd, quarantine_fd, quarantine_directory = (
            _new_quarantine_directory(capture, target)
        )
        moved_path = quarantine_directory / path.name
        # This check intentionally precedes an atomic descriptor-relative move.
        # POSIX cannot condition that move on the checked inode. If a same-UID
        # writer violates the lock contract and wins here, its entry is moved
        # intact and the post-move identity/hash check below fails with its path.
        _validate_capture_entry(source_fd, capture)
        try:
            os.replace(
                path.name, path.name,
                src_dir_fd=source_fd, dst_dir_fd=quarantine_fd,
            )
        except OSError as error:
            raise ValueError(
                "RECOVERY_REQUIRED: continuation retirement move failed; "
                f"captured bytes remain at {target}: {error}"
            ) from error
        os.fsync(source_fd)
        os.fsync(quarantine_fd)
        os.fsync(quarantine_root_fd)

        try:
            moved_stat, moved_raw = _capture_quarantined_entry(
                quarantine_fd, path.name, moved_path
            )
        except (OSError, ValueError) as error:
            raise ValueError(
                "RECOVERY_REQUIRED: unverified retirement entry preserved at "
                f"{moved_path}: {error}"
            ) from error
        expected = capture.entry_after
        if ((moved_stat.device, moved_stat.inode)
                != (expected.device, expected.inode)
                or moved_stat.mode != expected.mode
                or moved_stat.links != expected.links
                or moved_stat.size != expected.size
                or moved_raw != capture.raw
                or _sha256(moved_raw) != capture.sha256):
            raise ValueError(
                "RECOVERY_REQUIRED: newer continuation won final retirement; "
                f"preserved at {moved_path}"
            )
        return target
    finally:
        if quarantine_fd is not None:
            os.close(quarantine_fd)
        if quarantine_root_fd is not None:
            os.close(quarantine_root_fd)
        os.close(source_fd)


def resume_block(state: dict, draft_path: str | Path,
                 continuation_path: str | Path | None = None) -> str:
    canonical = os.path.abspath(str(draft_path))
    continuation = os.path.abspath(str(continuation_path or Path(draft_path).with_name(
        Path(draft_path).name.replace("_draft_", "_continue_"))))
    completed = ", ".join(str(fid) for fid in state["completed"]) or "none"
    missing = ", ".join(str(fid) for fid in state["missing"]) or "none"
    if state["status"] == "missing":
        return (f"START MODE — judge every packet fid in chunk order and write only those verdict "
                f"objects to the agent-owned continuation file `{continuation}`. The canonical draft "
                f"`{canonical}` is host-owned; never create or edit it. The host validates and merges "
                "your continuation on the next packet-generator run.")
    if state["status"] == "partial":
        return (f"RESUME MODE — canonical draft `{canonical}` already contains completed fids "
                f"[{completed}]. Read it for context but NEVER edit or copy those objects. Judge ONLY "
                f"the missing fids in this exact order: [{missing}], writing only those new objects "
                f"to agent-owned continuation file `{continuation}`. Rewrite that continuation after "
                "each new lot. The host validates the unchanged canonical-prefix hash and atomically "
                "merges your continuation on the next packet-generator run.")
    raise ValueError(f"no pending prompt for checkpoint state {state['status']!r}")


def _complete_prompt(slug: str, chunk_index: int, draft_path: Path) -> str:
    return (f"# Completed parking judge chunk\n\nArea `{slug}`, chunk {chunk_index:02d}, is complete. "
            f"The host-owned canonical draft is `{draft_path.resolve()}`. Do not judge, edit, create, "
            "or rewrite any verdict file for this chunk. No agent work is scheduled.\n")


def _status_record(slug: str, chunk_index: int, state: dict, prompt: Path,
                  draft: Path, continuation: Path) -> dict:
    return {
        "version": 1,
        "area": slug,
        "chunk": chunk_index,
        "status": state["status"],
        "completed": state["completed"],
        "missing": state["missing"],
        "prompt": None if state["status"] == "complete" else str(prompt.resolve()),
        "draft": str(draft.resolve()),
        "continuation": None if state["status"] == "complete" else str(continuation.resolve()),
    }


def _manifest_matches_state(manifest: dict, state: dict) -> bool:
    stored_resolution = manifest.get("resolution_row_sha256", manifest.get("judge_row_sha256"))
    return (manifest.get("completed") == state["completed"]
            and manifest.get("judge_row_sha256") == state["row_sha256"]
            and stored_resolution == state["resolution_sha256"])


def classify_continuation_transaction(chunk: list[dict], state: dict, manifest: object,
                                      slug: str, chunk_index: int, decision_sha256: str,
                                      continuation: _ContinuationCapture,
                                      ) -> tuple[str | None, dict | None, list[str]]:
    """Classify or recover one captured continuation transaction without writing.

    Normal state is manifest==draft plus a continuation for the next packet
    suffix. Two crash states are also safe and recoverable:
    - draft already contains the continuation but the manifest is still old;
    - draft+manifest are current but the continuation was not archived yet.
    Every recovery proves the continuation fids and original-judge row hashes
    exactly match the canonical suffix before allowing a write/archive step.
    """
    static_errors = validate_manifest_static(
        manifest, slug, chunk_index, chunk, decision_sha256)
    if static_errors or not isinstance(manifest, dict):
        return None, None, static_errors

    # Ordinary new continuation: current manifest binds the current canonical
    # prefix, and the continuation starts at the next packet.
    if _manifest_matches_state(manifest, state):
        suffix = chunk[len(state["completed"]):]
        normal = _inspect_continuation(suffix, continuation, allow_override=False)
        if not normal["errors"] and normal["completed"]:
            return "merge", normal, []

    # Crash after canonical draft replacement but before manifest replacement:
    # the old manifest binds a strict prefix, while the continuation is exactly
    # the already-appended canonical suffix.
    old_completed = manifest.get("completed")
    old_hashes = manifest.get("judge_row_sha256")
    old_resolutions = manifest.get("resolution_row_sha256", old_hashes)
    if (isinstance(old_completed, list) and isinstance(old_hashes, list)
            and isinstance(old_resolutions, list)):
        old_count = len(old_completed)
        if (state["completed"][:old_count] == old_completed
                and state["row_sha256"][:old_count] == old_hashes
                and state["resolution_sha256"][:old_count] == old_resolutions):
            replay = _inspect_continuation(
                chunk[old_count:], continuation, allow_override=False
            )
            if (not replay["errors"] and replay["completed"]
                    and state["completed"] == old_completed + replay["completed"]
                    and state["row_sha256"] == old_hashes + replay["row_sha256"]
                    and state["resolution_sha256"]
                    == old_resolutions + replay["resolution_sha256"]):
                return "refresh_manifest", replay, []

    # Crash after manifest replacement but before continuation archival: the
    # manifest binds the advanced draft and the continuation duplicates its
    # exact final rows. It is safe to archive without appending again.
    raw_rows = continuation.rows
    if (_manifest_matches_state(manifest, state) and isinstance(raw_rows, list)
            and raw_rows and len(raw_rows) <= len(state["completed"])):
        start = len(state["completed"]) - len(raw_rows)
        duplicate = _inspect_continuation(
            chunk[start:], continuation, allow_override=False
        )
        if (not duplicate["errors"]
                and duplicate["completed"] == state["completed"][start:]
                and duplicate["row_sha256"] == state["row_sha256"][start:]
                and duplicate["resolution_sha256"] == state["resolution_sha256"][start:]):
            return "archive_only", duplicate, []

    return None, None, [
        f"{continuation.path.name} is neither a new packet suffix nor an exact "
        "recoverable canonical suffix"
    ]


def _build_chunk_proposal(
        tmp: str | Path, slug: str, chunk_index: int, chunk: list[dict],
        decision_sha256: str, state: dict, continuation_state: dict | None,
        mode: str | None, continuation: _ContinuationCapture | None,
        ) -> tuple[_ChunkProposal | None, list[str]]:
    draft = _draft_path(tmp, slug, chunk_index)
    proposed_state = state
    proposed_raw = None
    errors: list[str] = []

    if mode == "merge":
        if continuation is None or continuation_state is None:
            return None, ["merge proposal lost its captured continuation"]
        draft = _draft_path(continuation.path.parent, slug, chunk_index)
        try:
            proposed_raw = _append_json_arrays(
                state.get("raw_bytes"), continuation.raw, continuation.rows
            )
        except (ValueError, UnicodeDecodeError) as exc:
            return None, [f"cannot build proposed {draft.name}: {exc}"]
        proposed_state = _inspect_bytes(chunk, proposed_raw, draft.name)
        errors.extend(proposed_state["errors"])
        expected = {
            "rows": state["rows"] + continuation_state["rows"],
            "completed": state["completed"] + continuation_state["completed"],
            "row_sha256": state["row_sha256"] + continuation_state["row_sha256"],
            "resolution_sha256": (
                state["resolution_sha256"] + continuation_state["resolution_sha256"]
            ),
        }
        for key, wanted in expected.items():
            if proposed_state[key] != wanted:
                errors.append(f"proposed {draft.name} changed its {key}")
    elif mode not in (None, "refresh_manifest", "archive_only"):
        errors.append(f"unknown continuation transaction mode {mode!r}")

    manifest = manifest_value(
        slug, chunk_index, chunk, decision_sha256, proposed_state
    )
    errors.extend(validate_manifest(
        manifest, slug, chunk_index, chunk, decision_sha256, proposed_state
    ))
    if errors:
        return None, errors
    return _ChunkProposal(
        mode=mode,
        state=proposed_state,
        draft_raw=proposed_raw,
        manifest=manifest,
        continuation=continuation,
    ), []


def _refuse_invalid_checkpoint(args: argparse.Namespace, errors: list[str]) -> int:
    if args.status_json:
        print(json.dumps({"version": 1, "area": args.slug, "status": "invalid",
                          "errors": errors}), file=sys.stderr)
    else:
        for error in errors:
            print(f"INVALID_CHECKPOINT\t{error}", file=sys.stderr)
        print("refusing to rewrite artifacts or merge continuations", file=sys.stderr)
    return 2


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("slug")
    parser.add_argument("--chunk", type=int, default=15)
    parser.add_argument("--tiles", default=None, help="tile dir as the judges will see it")
    parser.add_argument("--tmp", default=PADJ_TMP)
    parser.add_argument("--skip-judged", action="append", default=[], metavar="STORE")
    parser.add_argument("--adopt-existing", action="store_true",
                        help="bind valid pre-manifest drafts to current packet/rule/tile hashes")
    parser.add_argument("--status-json", action="store_true",
                        help="emit only one JSON status object per chunk")
    return parser


def _main_under_area_lock(args: argparse.Namespace,
                          generation_capture) -> int:
    parser = _argument_parser()
    if args.chunk <= 0:
        parser.error("--chunk must be positive")
    try:
        authority_journal = tr.authority_journal_path(args.tmp, args.slug)
    except ValueError as error:
        print(f"RECOVERY_REQUIRED: {error}", file=sys.stderr)
        return 2
    if authority_journal.exists():
        print(
            "RECOVERY_REQUIRED: live human-authority journal blocks packet "
            "validation and writes",
            file=sys.stderr,
        )
        return 2

    packets = build_packets(
        args.slug, args.tmp, args.tiles, generation_capture
    )
    already_elsewhere: set[str] = set()
    for store in args.skip_judged:
        if os.path.exists(store):
            value = json.loads(Path(store).read_text())
            if not isinstance(value, dict):
                parser.error(f"--skip-judged {store} must contain an object")
            for osm_id, record in value.items():
                # Once an area is folded into its store, its own records must
                # remain in the packet set so checkpoint validation and human
                # overrides can still be rerun. Only another area's ownership
                # is deduplication evidence; legacy/unattributed rows stay
                # conservative and count as elsewhere.
                if not isinstance(record, dict) or record.get("area") != args.slug:
                    already_elsewhere.add(osm_id)
    skipped = [fid for fid, packet in packets.items()
               if any(osm in already_elsewhere for osm in packet["osm"])]
    for fid in skipped:
        del packets[fid]
    fids = sorted(packets)
    chunks = [fids[index:index + args.chunk] for index in range(0, len(fids), args.chunk)]
    chunk_rows = [[packets[fid] for fid in chunk] for chunk in chunks]

    errors = strict_work_files(args.tmp, args.slug, len(chunks))
    decisions: list[str] = []
    states: list[dict] = []
    continuation_states: list[dict | None] = []
    continuation_modes: list[str | None] = []
    continuation_captures: list[_ContinuationCapture | None] = []
    for index, rows in enumerate(chunk_rows):
        decision, missing_inputs = decision_fingerprint(rows)
        if missing_inputs:
            errors.append(f"chunk {index:02d} missing decision inputs: {missing_inputs}")
            decision = ""
        decisions.append(decision or "")
        draft = _draft_path(args.tmp, args.slug, index)
        state = inspect_draft(rows, draft)
        states.append(state)
        errors.extend(f"chunk {index:02d}: {error}" for error in state["errors"])

        manifest_path = _manifest_path(args.tmp, args.slug, index)
        manifest = None
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text())
            except json.JSONDecodeError as exc:
                errors.append(f"chunk {index:02d}: cannot parse {manifest_path.name}: {exc}")

        continuation = _continue_path(args.tmp, args.slug, index)
        continuation_state = None
        continuation_mode = None
        capture_failed = False
        try:
            continuation_capture = _capture_continuation(continuation)
        except (OSError, ValueError) as exc:
            errors.append(f"chunk {index:02d}: cannot securely capture "
                          f"{continuation.name}: {exc}")
            continuation_capture = None
            capture_failed = True
        if continuation_capture is not None:
            if manifest is None:
                errors.append(f"chunk {index:02d}: {continuation.name} requires an existing "
                              "checkpoint manifest")
            else:
                continuation_mode, continuation_state, transaction_errors = (
                    classify_continuation_transaction(
                        rows, state, manifest, args.slug, index, decision or "",
                        continuation_capture))
                errors.extend(f"chunk {index:02d}: {error}" for error in transaction_errors)
        elif capture_failed:
            pass
        elif manifest is not None:
            errors.extend(f"chunk {index:02d}: {error}" for error in
                          validate_manifest(manifest, args.slug, index, rows,
                                            decision or "", state))
        elif state["status"] != "missing" and not args.adopt_existing:
            errors.append(f"chunk {index:02d}: {draft.name} has no checkpoint manifest; "
                          "independently validate it, then rerun with --adopt-existing")
        continuation_states.append(continuation_state)
        continuation_modes.append(continuation_mode)
        continuation_captures.append(continuation_capture)

    # Construct and semantically validate every canonical/checkpoint proposal
    # before the first canonical mutation. Merge proposals use only bytes from
    # the canonical preflight and the immutable continuation capture.
    proposals: list[_ChunkProposal | None] = []
    if not errors:
        for index, rows in enumerate(chunk_rows):
            proposal, proposal_errors = _build_chunk_proposal(
                args.tmp, args.slug, index, rows, decisions[index], states[index],
                continuation_states[index], continuation_modes[index],
                continuation_captures[index],
            )
            proposals.append(proposal)
            errors.extend(f"chunk {index:02d}: {error}" for error in proposal_errors)

    if errors:
        return _refuse_invalid_checkpoint(args, errors)

    # Catch any capture drift before any chunk mutates canonical state. Each
    # continuation is checked again immediately before its own transaction so
    # races during an earlier chunk cannot select replacement commit bytes.
    for index, capture in enumerate(continuation_captures):
        if capture is None:
            continue
        try:
            _recheck_continuation(capture)
        except (OSError, ValueError) as exc:
            return _refuse_invalid_checkpoint(
                args, [f"chunk {index:02d}: {exc}"]
            )

    # All chunks passed preflight. The recoverable transaction order remains:
    # canonical draft -> refreshed manifest -> archived continuation. A retry
    # can prove and finish either interrupted boundary without appending twice.
    root = Path(args.tmp)
    root.mkdir(parents=True, exist_ok=True)
    for index, proposal in enumerate(proposals):
        if proposal is None:  # defensive: proposals are complete before this loop
            raise RuntimeError(f"chunk {index:02d} lost its validated proposal")
        capture = proposal.continuation
        if capture is not None:
            try:
                _recheck_continuation(capture)
            except (OSError, ValueError) as exc:
                return _refuse_invalid_checkpoint(
                    args, [f"chunk {index:02d}: {exc}"]
                )

        draft = _draft_path(root, args.slug, index)
        if proposal.draft_raw is not None:
            _atomic_write(draft, proposal.draft_raw)
        states[index] = proposal.state

        # This manifest write also refreshes byte hashes after a legal human
        # override or completes recovery when the draft replacement landed first.
        _atomic_json(_manifest_path(root, args.slug, index), proposal.manifest)
        if capture is not None:
            try:
                _archive_continuation(capture)
            except (OSError, ValueError) as exc:
                return _refuse_invalid_checkpoint(
                    args, [f"chunk {index:02d}: {exc}"]
                )

    _atomic_write(root / f"{args.slug}_pub.txt", ",".join(str(fid) for fid in fids).encode())
    _atomic_json(root / f"{args.slug}_packets.json", {str(fid): packets[fid] for fid in fids})
    template = render_prompt_template()
    status_records = []
    for index, rows in enumerate(chunk_rows):
        chunk_path = root / f"{args.slug}_chunk_{index:02d}.json"
        _atomic_json(chunk_path, rows)
        draft = _draft_path(root, args.slug, index)
        continuation = _continue_path(root, args.slug, index)
        prompt_path = root / f"{args.slug}_prompt_{index:02d}.txt"
        state = states[index]
        if state["status"] == "complete":
            prompt = _complete_prompt(args.slug, index, draft)
        else:
            prompt = (template.replace("{PROTOCOL_PATH}", os.path.join(_HERE, "judge_protocol.md"))
                              .replace("{LESSONS_PATH}", os.path.join(_HERE, "judge_lessons.md"))
                              .replace("{CHUNK_PATH}", str(chunk_path.resolve()))
                              .replace("{OUT_PATH}", str(continuation.resolve()))
                              .replace("{RESUME_BLOCK}", resume_block(state, draft, continuation)))
        _atomic_write(prompt_path, prompt.encode())
        status_records.append(_status_record(args.slug, index, state, prompt_path, draft, continuation))

    if args.status_json:
        for record in status_records:
            print(json.dumps(record, separators=(",", ":")))
        return 0

    surveyed = sum(1 for fid in fids if packets[fid]["prior"] == "surveyed")
    fallback = sum(1 for fid in fids if packets[fid]["serves"]["fallback"])
    print(f"{args.slug}: {len(fids)} public-served lots to judge "
          f"({surveyed} surveyed prior, {fallback} fallback-served), {len(chunks)} chunk(s) of ≤{args.chunk}"
          + (f"; {len(skipped)} already judged in another area, skipped" if skipped else ""))
    for record in status_records:
        if record["status"] == "complete":
            print(f"COMPLETE_CHUNK\t{record['chunk']:02d}\t{record['draft']}")
        else:
            missing = ",".join(str(fid) for fid in record["missing"])
            print(f"PENDING_PROMPT\t{record['prompt']}\t{record['status']}\t{missing}")
    return 0


def main(argv=None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    args = _argument_parser().parse_args(values)
    inventory_resource = tr.dossier_resource_path(args.tmp)
    with trusted_fs.locked_resources(
            args.tmp, [(inventory_resource, fcntl.LOCK_SH)],
            tr.resource_lock_path):
        lock_path = tr.area_lock_path(args.tmp, args.slug)
        with trusted_fs.open_lock_file(lock_path) as area_lock:
            fcntl.flock(area_lock.fileno(), fcntl.LOCK_EX)
            try:
                generation_capture = dossier_output.capture_generation_locked(
                    args.tmp, args.slug
                )
            except (OSError, ValueError, KeyError, TypeError) as error:
                return _refuse_invalid_checkpoint(
                    args, [f"source generation validation failed: {error}"]
                )
            return _main_under_area_lock(args, generation_capture)


if __name__ == "__main__":
    raise SystemExit(main())
