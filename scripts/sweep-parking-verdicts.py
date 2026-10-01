#!/usr/bin/env python3
"""Apply ``public/areas/parking-verdicts.json`` to published geom safely.

The sidecar holds per-lot KEEP/DROP verdicts from parking adjudication.  This
sweep applies DROPs to every ``parking`` array under ``public/areas/geom``.
Before an apply it captures and validates the complete sidecar and geom JSON
inventory, computes the complete report, and refuses the whole operation if
any area would be emptied without an exact reviewed-empty signature.

Apply is a durable roll-forward transaction. Exact backups, retained stages,
and an owner-only exact sidecar snapshot are written below an ignored
owner-only transaction tree, followed by a durable PREPARED journal, before any
canonical geom replacement. A later non-dry invocation reconstructs authority
from that snapshot and a code-approved historical policy version, validates the
complete inventory/artifacts, and rolls only a valid BEFORE suffix forward.
Mutable current sidecar/policy state cannot strand PREPARED. Dry-run never
creates locks or transaction artifacts and never recovers.

All same-UID canonical geom writers share the persistent guard in
``_parking_geom_guard.py``: one globally sorted lock set includes geom and the
live journal, and a surviving PREPARED journal blocks every peer writer after
the sweep process exits. The receipt and targets finish while the journal stays
live; the bound report is printed and flushed before journal archival. Recovery
is always an exact rerun of this command.

    python3 scripts/sweep-parking-verdicts.py --dry-run
    python3 scripts/sweep-parking-verdicts.py

A judged OSM id wins when present; otherwise matching uses the adjudicated
footprint and position through ``scripts/_parking_verdicts.py``.  A reviewed
empty approval binds exact (verdict key, reason) multiplicity, so parking or
verdict drift restores the refusal.
"""
from __future__ import annotations

import argparse
import errno
import fcntl
import hashlib
import json
import math
import os
import secrets
import stat
import sys
import unicodedata
from collections import Counter
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
_TOOLS = _SCRIPTS / "parking-adjud" / "tools"
for _import_path in (str(_SCRIPTS), str(_TOOLS)):
    if _import_path not in sys.path:
        sys.path.insert(0, _import_path)

import _parking_geom_guard as geom_guard  # noqa: E402
import _parking_verdicts as pv  # noqa: E402
import trust_resolution as tr  # noqa: E402
import trusted_filesystem as trusted_fs  # noqa: E402

_ROOT = _SCRIPTS.parent
_TRANSACTION_DIRNAME = geom_guard.TRANSACTION_DIRNAME
_LIVE_JOURNAL_NAME = geom_guard.LIVE_JOURNAL_NAME
_TRANSACTION_VERSION = 2
_TRANSACTION_KIND = "parking-verdict-sweep-transaction"
_RECEIPT_KIND = "parking-verdict-sweep-receipt"
_POLICY_VERSION = "reviewed-empty-v1"

# Exact singleton populations manually double-reviewed on 2026-09-26. Both
# independent reviews agreed the retained lot is not public, and a search of all
# validated KEEPs found no replacement within the app's 805 m rule. Binding the
# approval to verdict key + reason + multiplicity means any future lot/verdict
# change is refused again instead of inheriting a broad slug-level exception.
_REVIEWED_EMPTY_SIGNATURES = {
    "mesa-valley-open-space-co": (("way/58294967", "not-public"),),
    "promntory-point-open-space-co": (("way/1206954210", "not-public"),),
    "sondermann-park-co": (("way/58294967", "not-public"),),
}

_FILE_SIGNATURE_FIELDS = (
    "dev", "inode", "mode", "uid", "gid", "nlink", "size", "mtime_ns",
    "ctime_ns",
)
_DIRECTORY_SIGNATURE_FIELDS = (
    "dev", "inode", "mode", "uid", "gid", "nlink", "mtime_ns", "ctime_ns",
)


class RecoveryRequired(ValueError):
    """The durable transaction exists but cannot safely advance."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_json(value: object) -> bytes:
    return (json.dumps(
        value, indent=1, sort_keys=True, ensure_ascii=False, allow_nan=False,
    ) + "\n").encode("utf-8")


# Recovery accepts only exact policy bytes named by this code-reviewed registry.
# The live journal cannot supply a new policy or reinterpret a historical one.
_APPROVED_POLICY_REGISTRY = {
    _POLICY_VERSION: _canonical_json([
        ["mesa-valley-open-space-co", [["way/58294967", "not-public"]]],
        ["promntory-point-open-space-co", [["way/1206954210", "not-public"]]],
        ["sondermann-park-co", [["way/58294967", "not-public"]]],
    ]),
}


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_nonfinite(token: str):
    raise ValueError(f"non-finite JSON number {token}")


def _strict_json(data: bytes, label: str):
    if type(data) is not bytes:
        raise ValueError(f"{label} input must be bytes")
    try:
        text = data.decode("utf-8")
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError(f"{label} is not strict JSON: {error}") from error


def _coordinate(value: object, minimum: float, maximum: float, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a nonboolean number")
    number = float(value)
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise ValueError(f"{field} must be finite and in [{minimum}, {maximum}]")
    return number


def _reject_untrusted_text(value: object, field: str) -> None:
    """Reject strings that could change physical report/log line framing."""
    if type(value) is str:
        for character in value:
            if unicodedata.category(character) in {"Cc", "Cf", "Zl", "Zp"}:
                raise ValueError(
                    f"{field} contains forbidden control/format/separator text"
                )
        return
    if type(value) is dict:
        for key, child in value.items():
            _reject_untrusted_text(key, f"{field} key")
            _reject_untrusted_text(child, f"{field}.{key!r}")
    elif type(value) is list:
        for index, child in enumerate(value):
            _reject_untrusted_text(child, f"{field}[{index}]")


def _single_line(value: object) -> str:
    """Render one untrusted value without permitting a physical line break."""
    encoded = json.dumps(str(value), ensure_ascii=True, allow_nan=False)
    return encoded[1:-1]


def _validate_rings(value: object, field: str,
                    verdict_lat: float, verdict_lon: float) -> None:
    if type(value) is not list:
        raise ValueError(f"{field} must be a list")
    nearest_edge = math.inf
    contains_verdict = False
    for ring_index, ring in enumerate(value):
        ring_field = f"{field}[{ring_index}]"
        if type(ring) is not list or len(ring) < 4:
            raise ValueError(f"{ring_field} must contain at least four points")
        coordinates = []
        for point_index, point in enumerate(ring):
            point_field = f"{ring_field}[{point_index}]"
            if type(point) is not list or len(point) != 2:
                raise ValueError(f"{point_field} must be [lat, lon]")
            coordinates.append((
                _coordinate(point[0], -90.0, 90.0, f"{point_field}[0]"),
                _coordinate(point[1], -180.0, 180.0, f"{point_field}[1]"),
            ))
        if coordinates[0] != coordinates[-1]:
            raise ValueError(f"{ring_field} must be explicitly closed")
        distinct = set(coordinates[:-1])
        if len(distinct) < 3:
            raise ValueError(f"{ring_field} must contain at least three distinct vertices")
        twice_area = abs(sum(
            first[1] * second[0] - second[1] * first[0]
            for first, second in zip(coordinates, coordinates[1:])
        ))
        if twice_area <= 1e-15:
            raise ValueError(f"{ring_field} is a degenerate zero-area polygon")
        lats = [point[0] for point in distinct]
        lons = [point[1] for point in distinct]
        lat_span_m = (max(lats) - min(lats)) * 111_320.0
        lon_span_m = ((max(lons) - min(lons)) * 111_320.0
                      * math.cos(math.radians((min(lats) + max(lats)) / 2)))
        diagonal = math.hypot(lat_span_m, lon_span_m)
        if diagonal > 2_000.0:
            raise ValueError(
                f"{ring_field} diagonal {diagonal:.2f} m exceeds 2000 m"
            )
        contains_verdict = (
            contains_verdict
            or pv.point_in_ring(verdict_lat, verdict_lon, ring)
        )
        nearest_edge = min(
            nearest_edge, pv.dist_to_ring_m(verdict_lat, verdict_lon, ring),
        )
    if value and not contains_verdict and nearest_edge > 100.0:
        raise ValueError(
            f"{field} is {nearest_edge:.2f} m from its verdict position; maximum is 100 m"
        )


def _validate_sidecar(document: object, path: Path) -> pv.Verdicts:
    """Use the shared strict matching contract for every mutating consumer."""
    return pv.strict_verdicts_document(document, f"sidecar {path}")


def _validate_geom(document: object, path: Path) -> None:
    _reject_untrusted_text(path.name, "geom basename")
    _reject_untrusted_text(document, f"geom {path}")
    if type(document) is not dict:
        raise ValueError(f"geom {path} root must be an object")
    if "parking" not in document:
        return
    parking = document["parking"]
    if type(parking) is not list:
        raise ValueError(f"geom {path} parking must be a list when present")
    for index, lot in enumerate(parking):
        label = f"geom {path} parking[{index}]"
        if type(lot) is not dict:
            raise ValueError(f"{label} must be an object")
        _coordinate(lot.get("lat"), -90.0, 90.0, f"{label}.lat")
        _coordinate(lot.get("lon"), -180.0, 180.0, f"{label}.lon")
        # These are the documented shipped ParkingLot fields. Unknown fields
        # remain opaque JSON and are preserved when the parking list is rebuilt.
        for field in ("name", "source", "osm"):
            if field in lot and type(lot[field]) is not str:
                raise ValueError(f"{label}.{field} must be a string")
        if "fee" in lot and type(lot["fee"]) is not bool:
            raise ValueError(f"{label}.fee must be boolean")
        # Legacy published geom contains explicit null for this optional field.
        if ("trailhead" in lot and lot["trailhead"] is not None
                and type(lot["trailhead"]) is not bool):
            raise ValueError(f"{label}.trailhead must be boolean or null")


def _stat_signature(value: os.stat_result) -> dict[str, int]:
    return {
        "dev": value.st_dev,
        "inode": value.st_ino,
        "mode": value.st_mode,
        "uid": value.st_uid,
        "gid": value.st_gid,
        "nlink": value.st_nlink,
        "size": value.st_size,
        "mtime_ns": value.st_mtime_ns,
        "ctime_ns": value.st_ctime_ns,
    }


def _directory_signature(value: os.stat_result) -> dict[str, int]:
    signature = _stat_signature(value)
    return {field: signature[field] for field in _DIRECTORY_SIGNATURE_FIELDS}


def _directory_identity(signature: dict[str, int]) -> tuple[int, ...]:
    return tuple(signature[field] for field in (
        "dev", "inode", "mode", "uid", "gid", "nlink",
    ))


def _require_safe_regular(signature: dict[str, int], path: Path, label: str) -> None:
    if not stat.S_ISREG(signature["mode"]):
        raise ValueError(f"{label} is not a regular file: {path}")
    if signature["uid"] != os.geteuid():
        raise ValueError(f"{label} is not owned by the effective uid: {path}")
    if signature["nlink"] != 1:
        raise ValueError(f"{label} is not a single-link file: {path}")
    if stat.S_IMODE(signature["mode"]) & 0o022:
        raise ValueError(f"{label} is group/world writable: {path}")


def _read_fd_bytes(fd: int) -> bytes:
    os.lseek(fd, 0, os.SEEK_SET)
    chunks = []
    while True:
        chunk = os.read(fd, 1024 * 1024)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _capture_entry(parent_fd: int, parent: Path, name: str, label: str) -> dict:
    if not name or Path(name).name != name:
        raise ValueError(f"{label} has a noncanonical basename")
    path = parent / name
    fd = None
    try:
        flags = (os.O_RDONLY | trusted_fs._required_flag("O_NOFOLLOW")
                 | getattr(os, "O_CLOEXEC", 0))
        try:
            fd = os.open(name, flags, dir_fd=parent_fd)
        except OSError as error:
            if error.errno == errno.ELOOP:
                raise ValueError(f"{label} is a symlink: {path}") from error
            raise
        before = _stat_signature(os.fstat(fd))
        entry_before = _stat_signature(os.stat(
            name, dir_fd=parent_fd, follow_symlinks=False,
        ))
        trusted_fs.require_trivial_acl_fd(fd, path)
        _require_safe_regular(before, path, label)
        _require_safe_regular(entry_before, path, label)
        if before != entry_before:
            raise ValueError(f"{label} path/descriptor identity differs: {path}")
        data = _read_fd_bytes(fd)
        after = _stat_signature(os.fstat(fd))
        try:
            entry_after = _stat_signature(os.stat(
                name, dir_fd=parent_fd, follow_symlinks=False,
            ))
        except FileNotFoundError as error:
            raise ValueError(f"{label} disappeared during capture: {path}") from error
        trusted_fs.require_trivial_acl_fd(fd, path)
        if not (before == entry_before == after == entry_after):
            raise ValueError(f"{label} changed during stable capture: {path}")
        if len(data) != before["size"]:
            raise ValueError(f"{label} size changed during stable capture: {path}")
        return {
            "path": path,
            "name": name,
            "data": data,
            "length": len(data),
            "sha256": _sha256(data),
            "signature": before,
        }
    finally:
        if fd is not None:
            os.close(fd)


def _capture_file(path: Path, label: str) -> dict:
    parent_fd = trusted_fs.open_trusted_directory_fd(path.parent)
    try:
        parent_before = _directory_signature(os.fstat(parent_fd))
        capture = _capture_entry(parent_fd, path.parent, path.name, label)
        parent_after = _directory_signature(os.fstat(parent_fd))
        trusted_fs.require_trusted_directory_fd(parent_fd, path.parent)
        if parent_before != parent_after:
            raise ValueError(f"{label} parent changed during capture: {path.parent}")
        return capture
    finally:
        os.close(parent_fd)


def _capture_sidecar(path: Path) -> dict:
    capture = _capture_file(path, "parking verdict sidecar")
    document = _strict_json(capture["data"], f"sidecar {path}")
    capture["document"] = document
    capture["verdicts"] = _validate_sidecar(document, path)
    return capture


def _capture_inventory(geom_dir: Path) -> dict:
    directory_fd = trusted_fs.open_trusted_directory_fd(geom_dir)
    try:
        before = _directory_signature(os.fstat(directory_fd))
        names_before = tuple(sorted(
            name for name in os.listdir(directory_fd) if name.endswith(".json")
        ))
        files = []
        for name in names_before:
            capture = _capture_entry(
                directory_fd, geom_dir, name, f"geom inventory entry {name}",
            )
            document = _strict_json(capture["data"], f"geom {capture['path']}")
            _validate_geom(document, capture["path"])
            capture["document"] = document
            files.append(capture)
        names_after = tuple(sorted(
            name for name in os.listdir(directory_fd) if name.endswith(".json")
        ))
        after = _directory_signature(os.fstat(directory_fd))
        trusted_fs.require_trusted_directory_fd(directory_fd, geom_dir)
        if names_before != names_after:
            raise ValueError("geom JSON inventory changed during complete capture")
        if before != after:
            raise ValueError("geom directory changed during complete capture")
        return {
            "path": geom_dir,
            "signature": before,
            "names": names_before,
            "files": files,
            "by_name": {item["name"]: item for item in files},
        }
    finally:
        os.close(directory_fd)


def _normalize_policy(policy: object) -> tuple[dict[str, tuple], list[list[object]]]:
    if type(policy) is not dict:
        raise ValueError("reviewed-empty policy must be an object")
    lookup: dict[str, tuple] = {}
    serialized: list[list[object]] = []
    for slug in sorted(policy):
        if type(slug) is not str or not slug or Path(slug).name != slug:
            raise ValueError("reviewed-empty policy slug is invalid")
        _reject_untrusted_text(slug, "reviewed-empty policy slug")
        raw_signature = policy[slug]
        if not isinstance(raw_signature, (tuple, list)):
            raise ValueError(f"reviewed-empty signature for {slug} must be a sequence")
        pairs = []
        for pair in raw_signature:
            if (not isinstance(pair, (tuple, list)) or len(pair) != 2
                    or any(type(value) is not str or not value for value in pair)):
                raise ValueError(
                    f"reviewed-empty signature for {slug} must contain string pairs"
                )
            pairs.append((pair[0], pair[1]))
            _reject_untrusted_text(pair[0], f"reviewed-empty key for {slug}")
            _reject_untrusted_text(pair[1], f"reviewed-empty reason for {slug}")
        lookup[slug] = tuple(pairs)
        serialized.append([slug, [[key, reason] for key, reason in pairs]])
    return lookup, serialized


def _policy_identity(version: str, encoded: bytes) -> dict:
    return {
        "version": version,
        "length": len(encoded),
        "sha256": _sha256(encoded),
        "serialized": _strict_json(encoded, f"approved policy {version}"),
    }


def _approved_policy_from_serialized(serialized: list) -> tuple[str, dict]:
    encoded = _canonical_json(serialized)
    for version, approved in _APPROVED_POLICY_REGISTRY.items():
        if encoded == approved:
            return version, _policy_identity(version, approved)
    raise ValueError(
        "reviewed-empty policy is not an exact code-approved policy version"
    )


def _approved_policy_from_identity(identity: object) -> tuple[dict, list]:
    if type(identity) is not dict:
        raise RecoveryRequired("reviewed-empty policy identity is invalid")
    version = identity.get("version")
    approved = _APPROVED_POLICY_REGISTRY.get(version)
    if approved is None or identity != _policy_identity(version, approved):
        raise RecoveryRequired(
            "reviewed-empty policy version/bytes are not code-approved"
        )
    serialized = _strict_json(approved, f"approved policy {version}")
    if type(serialized) is not list:
        raise RecoveryRequired("approved reviewed-empty policy schema is invalid")
    policy = {}
    for item in serialized:
        if type(item) is not list or len(item) != 2 or type(item[1]) is not list:
            raise RecoveryRequired("approved reviewed-empty policy schema is invalid")
        policy[item[0]] = tuple(tuple(pair) for pair in item[1])
    lookup, normalized = _normalize_policy(policy)
    if normalized != serialized:
        raise RecoveryRequired("approved reviewed-empty policy is not canonical")
    return lookup, normalized


def _serialize_geom(document: dict) -> bytes:
    return json.dumps(
        document, ensure_ascii=True, allow_nan=False,
    ).encode("utf-8")


def _render_report(result: dict, dry_run: bool, apply_refused: bool) -> tuple[str, str]:
    planned_lots = len(result["removed"])
    planned_areas = len(result["changed"])
    if apply_refused:
        headline = (
            f"NOT APPLIED — 0 lots removed; planned {planned_lots} lot(s) "
            f"from {planned_areas} area(s)"
        )
        detail_heading = "planned removals:"
    elif dry_run:
        headline = (
            f"DRY-RUN — would remove {planned_lots} lot(s) "
            f"from {planned_areas} area(s)"
        )
        detail_heading = "planned removals:"
    else:
        headline = (
            f"removed {planned_lots} lot(s) from {planned_areas} area(s)"
        )
        detail_heading = "removed:"
    lines = [headline]
    for reason, count in result["reasons"].most_common():
        lines.append(f"  {count:5}  {_single_line(reason)}")
    lines.extend(("", detail_heading))
    for slug, lot, entry in result["removed"]:
        distance = pv.haversine_m(
            lot["lat"], lot["lon"], entry["lat"], entry["lon"],
        )
        name = _single_line(lot.get("name") or entry.get("name") or "(unnamed)")
        axis = {
            "not-public": "public", "not-a-lot": "exists",
        }.get(entry.get("reason"), "serves")
        evidence = entry["evidence"]
        why = evidence.get(axis) or next(iter(evidence.values()), "")
        lines.append(
            f"   {_single_line(slug):34} {name[:30]:32} "
            f"{_single_line(entry['_key']):20} {distance:5.1f} m  "
            f"[{_single_line(entry.get('reason'))}] {_single_line(why)}"
        )
    for slug, count, signature in result["reviewed_empty"]:
        rendered = ", ".join(
            f"{_single_line(key)}:{_single_line(reason)}"
            for key, reason in signature
        )
        lines.append(
            f"  REVIEWED-EMPTY {_single_line(slug)} — {count} flagged, "
            f"exact signature [{rendered}]"
        )

    errors = []
    for slug, count in result["refused"]:
        errors.append(
            f"  !! REFUSING to empty {_single_line(slug)} — {count} flagged, "
            f"0 would remain. Review the verdicts for this area."
        )
    if apply_refused and result["refused"]:
        errors.append(
            "NOT APPLIED: at least one empty-area refusal blocks all geom writes."
        )
    return "\n".join(lines) + "\n", ("\n".join(errors) + "\n" if errors else "")


def _plan_inventory(sidecar: dict, inventory: dict, policy: object,
                    dry_run: bool) -> dict:
    policy_lookup, policy_serialized = _normalize_policy(policy)
    verdicts: pv.Verdicts = sidecar["verdicts"]
    reasons: Counter = Counter()
    removed: list[tuple[str, dict, dict]] = []
    refused: list[tuple[str, int]] = []
    reviewed_empty: list[tuple[str, int, tuple]] = []
    changed: list[str] = []
    after_by_name: dict[str, bytes] = {}

    for source in inventory["files"]:
        document = source["document"]
        if "parking" not in document or not document["parking"]:
            continue
        lots = document["parking"]
        gone = []
        for index, lot in enumerate(lots):
            entry = verdicts.drop_for(lot)
            if entry is not None:
                gone.append((index, lot, entry))
        if not gone:
            continue
        slug = source["name"][:-5]
        if len(gone) == len(lots):
            actual = tuple(sorted(
                (entry["_key"], entry.get("reason") or "?")
                for _index, _lot, entry in gone
            ))
            if actual != policy_lookup.get(slug):
                refused.append((slug, len(gone)))
                continue
            reviewed_empty.append((slug, len(gone), actual))

        doomed = {index for index, _lot, _entry in gone}
        after_document = dict(document)
        after_document["parking"] = [
            lot for index, lot in enumerate(lots) if index not in doomed
        ]
        after_bytes = _serialize_geom(after_document)
        if after_bytes == source["data"]:
            raise ValueError(f"planned changed geom has identical bytes: {source['path']}")
        after_by_name[source["name"]] = after_bytes
        changed.append(slug)
        for _index, lot, entry in gone:
            reason = entry.get("reason") or "?"
            reasons[reason] += 1
            removed.append((slug, lot, entry))

    result = {
        "reasons": reasons,
        "removed": removed,
        "refused": refused,
        "reviewed_empty": reviewed_empty,
        "changed": changed,
    }
    stdout, stderr = _render_report(
        result, dry_run=dry_run,
        apply_refused=(not dry_run and bool(refused)),
    )
    return {
        "sidecar": sidecar,
        "inventory": inventory,
        "policy_lookup": policy_lookup,
        "policy_serialized": policy_serialized,
        "result": result,
        "after_by_name": after_by_name,
        "stdout": stdout,
        "stderr": stderr,
        "dry_run": dry_run,
    }


def _sidecar_identity(capture: dict) -> dict:
    return {
        "path": str(capture["path"]),
        "length": capture["length"],
        "sha256": capture["sha256"],
    }


def _transaction_root(geom_dir: Path) -> Path:
    return geom_dir.parent / _TRANSACTION_DIRNAME


def _artifact_paths(root: Path, transaction_id: str,
                    changed_names: list[str]) -> dict:
    transaction_dir = root / "transactions" / transaction_id
    return {
        "root": root,
        "live": root / _LIVE_JOURNAL_NAME,
        "transaction_dir": transaction_dir,
        "sidecar_snapshot": transaction_dir / "sidecar.snapshot.json",
        "receipt": transaction_dir / "receipt.json",
        "archive": root / "Archive" / f"{transaction_id}.journal.json",
        "backup_by_name": {
            name: transaction_dir / "backups" / name for name in changed_names
        },
        "stage_by_name": {
            name: transaction_dir / "stages" / name for name in changed_names
        },
    }


def _settable_gids() -> set[int]:
    return {os.getegid(), *os.getgroups()}


def _require_preservable_target_metadata(plan: dict) -> None:
    allowed = _settable_gids()
    for name in sorted(plan["after_by_name"]):
        source = plan["inventory"]["by_name"][name]
        gid = source["signature"]["gid"]
        if gid not in allowed:
            raise ValueError(
                f"target GID cannot be preserved by this process: {name} gid={gid}"
            )


def _prepare_transaction(plan: dict) -> dict:
    _require_preservable_target_metadata(plan)
    policy_version, policy_identity = _approved_policy_from_serialized(
        plan["policy_serialized"],
    )
    inventory_items = []
    for capture in plan["inventory"]["files"]:
        item = {
            "name": capture["name"],
            "before_length": capture["length"],
            "before_sha256": capture["sha256"],
            "before_signature": capture["signature"],
        }
        after = plan["after_by_name"].get(capture["name"])
        if after is not None:
            item.update({
                "after_length": len(after),
                "after_sha256": _sha256(after),
            })
        inventory_items.append(item)

    identity = {
        "contract": "parking-verdict-sweep-v2",
        "geom_dir": str(plan["inventory"]["path"]),
        "geom_directory_signature": plan["inventory"]["signature"],
        "sidecar": _sidecar_identity(plan["sidecar"]),
        "reviewed_empty_policy": policy_identity,
        "inventory": inventory_items,
    }
    transaction_id = _sha256(_canonical_json(identity))
    changed_names = sorted(plan["after_by_name"])
    paths = _artifact_paths(
        _transaction_root(plan["inventory"]["path"]), transaction_id,
        changed_names,
    )
    changed_after = [
        {
            "name": name,
            "after_length": len(plan["after_by_name"][name]),
            "after_sha256": _sha256(plan["after_by_name"][name]),
        }
        for name in changed_names
    ]
    receipt = {
        "version": _TRANSACTION_VERSION,
        "kind": _RECEIPT_KIND,
        "state": "APPLIED",
        "transaction_id": transaction_id,
        "geom_dir": str(plan["inventory"]["path"]),
        "sidecar_sha256": plan["sidecar"]["sha256"],
        "policy_version": policy_version,
        "changed": changed_after,
    }
    artifacts = {
        "root": str(paths["root"]),
        "live_journal": str(paths["live"]),
        "sidecar_snapshot": str(paths["sidecar_snapshot"]),
        "receipt": str(paths["receipt"]),
        "archive": str(paths["archive"]),
        "targets": [
            {
                "name": name,
                "target": str(plan["inventory"]["path"] / name),
                "backup": str(paths["backup_by_name"][name]),
                "stage": str(paths["stage_by_name"][name]),
            }
            for name in changed_names
        ],
    }
    journal = {
        "version": _TRANSACTION_VERSION,
        "kind": _TRANSACTION_KIND,
        "state": "PREPARED",
        "transaction_id": transaction_id,
        "identity": identity,
        "report": {"stdout": plan["stdout"], "stderr": plan["stderr"]},
        "receipt": receipt,
        "artifacts": artifacts,
    }
    plan.update({
        "identity": identity,
        "transaction_id": transaction_id,
        "paths": paths,
        "receipt": receipt,
        "receipt_bytes": _canonical_json(receipt),
        "journal": journal,
        "journal_bytes": _canonical_json(journal),
    })
    return plan


def _canonical_path(value: str | os.PathLike) -> Path:
    return Path(value).expanduser().absolute()


def _within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _lock_entries(sidecar: Path, geom_dir: Path, live: Path):
    resources = [
        (sidecar, fcntl.LOCK_SH),
        (geom_dir, fcntl.LOCK_EX),
        (live, fcntl.LOCK_EX),
    ]
    return trusted_fs.resource_lock_entries(
        live.parent, resources, tr.resource_lock_path,
    )


def _validate_initial_paths(sidecar: Path, geom_dir: Path) -> tuple[Path, tuple]:
    root = _transaction_root(geom_dir)
    live = root / _LIVE_JOURNAL_NAME
    resolved_sidecar = sidecar.resolve(strict=False)
    resolved_geom = geom_dir.resolve(strict=False)
    resolved_root = root.resolve(strict=False)
    if resolved_sidecar == resolved_geom or _within(resolved_sidecar, resolved_geom):
        raise ValueError("sidecar must not collide with or live inside geom_dir")
    if (resolved_root == resolved_geom or _within(resolved_root, resolved_geom)
            or _within(resolved_geom, resolved_root)):
        raise ValueError("transaction tree and geom_dir paths collide")
    if resolved_sidecar == live.resolve(strict=False):
        raise ValueError("sidecar and live journal paths collide")
    entries = _lock_entries(sidecar, geom_dir, live)
    endpoints = {resolved_sidecar, resolved_geom, live.resolve(strict=False)}
    for lock_path, resource, _mode in entries:
        if lock_path in endpoints or lock_path == resource:
            raise ValueError("resource lock path collides with a source or target")
    return root, entries


def _validate_transaction_paths(plan: dict, lock_entries: tuple) -> None:
    paths = plan["paths"]
    file_paths = [
        plan["sidecar"]["path"], paths["live"], paths["sidecar_snapshot"],
        paths["receipt"], paths["archive"],
    ]
    file_paths.extend(item["path"] for item in plan["inventory"]["files"])
    file_paths.extend(paths["backup_by_name"].values())
    file_paths.extend(paths["stage_by_name"].values())
    resolved = [Path(path).resolve(strict=False) for path in file_paths]
    if len(resolved) != len(set(resolved)):
        raise ValueError("source, target, backup, stage, journal, or receipt paths collide")
    lock_paths = {lock_path.resolve(strict=False) for lock_path, _resource, _mode in lock_entries}
    if lock_paths.intersection(resolved):
        raise ValueError("resource lock path collides with a transaction path")
    root = paths["root"].resolve(strict=False)
    for path in (paths["live"], paths["sidecar_snapshot"], paths["receipt"],
                 paths["archive"], *paths["backup_by_name"].values(),
                 *paths["stage_by_name"].values()):
        if not _within(Path(path).resolve(strict=False), root):
            raise ValueError("transaction artifact escapes its owner-only root")


def _require_owner_only_root(root: Path) -> None:
    fd = trusted_fs.open_trusted_directory_fd(root)
    try:
        value = trusted_fs.require_trusted_directory_fd(fd, root)
        if stat.S_IMODE(value.st_mode) != 0o700:
            raise ValueError(f"transaction root must be owner-only mode 0700: {root}")
    finally:
        os.close(fd)


def _live_journal_exists(root: Path) -> bool:
    try:
        fd = trusted_fs.open_trusted_directory_fd(root)
    except FileNotFoundError:
        return False
    try:
        value = trusted_fs.require_trusted_directory_fd(fd, root)
        if stat.S_IMODE(value.st_mode) != 0o700:
            raise RecoveryRequired(
                f"transaction root is not owner-only mode 0700: {root}"
            )
        try:
            entry = os.stat(
                _LIVE_JOURNAL_NAME, dir_fd=fd, follow_symlinks=False,
            )
        except FileNotFoundError:
            return False
        if (not stat.S_ISREG(entry.st_mode) or entry.st_uid != os.geteuid()
                or stat.S_IMODE(entry.st_mode) != 0o600 or entry.st_nlink != 1):
            raise RecoveryRequired("live journal entry is not an owner-only regular file")
        return True
    finally:
        os.close(fd)


def _same_file_capture(first: dict, second: dict) -> bool:
    return (
        first["path"] == second["path"]
        and first["signature"] == second["signature"]
        and first["data"] == second["data"]
    )


def _require_initial_unchanged(plan: dict) -> None:
    sidecar = _capture_sidecar(plan["sidecar"]["path"])
    inventory = _capture_inventory(plan["inventory"]["path"])
    if not _same_file_capture(plan["sidecar"], sidecar):
        raise ValueError("sidecar changed after complete preflight")
    if (plan["inventory"]["signature"] != inventory["signature"]
            or plan["inventory"]["names"] != inventory["names"]):
        raise ValueError("geom inventory changed after complete preflight")
    for original, current in zip(plan["inventory"]["files"], inventory["files"]):
        if not _same_file_capture(original, current):
            raise ValueError(
                f"geom inventory entry changed after complete preflight: {original['name']}"
            )


def _read_owner_only_artifact(path: Path, label: str) -> bytes:
    try:
        return trusted_fs.read_regular_bytes(path, require_owner_only=True)
    except (OSError, ValueError) as error:
        raise RecoveryRequired(f"{label} is missing or untrusted: {path}: {error}") from error


def _optional_owner_only_artifact(path: Path, label: str) -> bytes | None:
    try:
        return _read_owner_only_artifact(path, label)
    except RecoveryRequired:
        if not os.path.lexists(path):
            return None
        raise


def _validate_terminal_recovery_artifacts(plan: dict) -> None:
    receipt = _optional_owner_only_artifact(
        plan["paths"]["receipt"], "terminal receipt",
    )
    if receipt is not None and receipt != plan["receipt_bytes"]:
        raise RecoveryRequired("terminal receipt differs from journal authority")
    archive = _optional_owner_only_artifact(
        plan["paths"]["archive"], "journal archive",
    )
    if archive is not None:
        raise RecoveryRequired(
            "live PREPARED journal conflicts with an existing archive artifact"
        )


def _validate_artifacts(plan: dict) -> None:
    sidecar_snapshot = _read_owner_only_artifact(
        plan["paths"]["sidecar_snapshot"], "sidecar snapshot",
    )
    if sidecar_snapshot != plan["sidecar"]["data"]:
        raise RecoveryRequired("sidecar snapshot bytes differ from PREPARED authority")
    if (len(sidecar_snapshot) != plan["identity"]["sidecar"]["length"]
            or _sha256(sidecar_snapshot) != plan["identity"]["sidecar"]["sha256"]):
        raise RecoveryRequired("sidecar snapshot hash/length differs from journal")
    snapshot_document = _strict_json(sidecar_snapshot, "sidecar snapshot")
    _validate_sidecar(snapshot_document, plan["sidecar"]["path"])
    for name in sorted(plan["after_by_name"]):
        source = plan["inventory"]["by_name"][name]
        backup = _read_owner_only_artifact(
            plan["paths"]["backup_by_name"][name], f"backup for {name}",
        )
        stage = _read_owner_only_artifact(
            plan["paths"]["stage_by_name"][name], f"stage for {name}",
        )
        if backup != source["data"]:
            raise RecoveryRequired(f"backup bytes differ from BEFORE target: {name}")
        if stage != plan["after_by_name"][name]:
            raise RecoveryRequired(f"stage bytes differ from AFTER target: {name}")


def _after_capture_is_exact(current: dict, original: dict, after: bytes) -> bool:
    signature = current["signature"]
    before_signature = original["signature"]
    return (
        current["data"] == after
        and signature["uid"] == before_signature["uid"]
        and signature["gid"] == before_signature["gid"]
        and stat.S_IMODE(signature["mode"])
        == stat.S_IMODE(before_signature["mode"])
        and signature["nlink"] == 1
    )


def _classify_transaction(plan: dict) -> tuple[dict[str, str], dict]:
    current = _capture_inventory(plan["inventory"]["path"])
    if (_directory_identity(current["signature"])
            != _directory_identity(plan["inventory"]["signature"])):
        raise RecoveryRequired("geom directory identity or mode changed")
    if current["names"] != plan["inventory"]["names"]:
        raise RecoveryRequired("geom inventory has an extra or missing JSON file")

    changed_names = sorted(plan["after_by_name"])
    states: dict[str, str] = {}
    for name in current["names"]:
        original = plan["inventory"]["by_name"][name]
        candidate = current["by_name"][name]
        after = plan["after_by_name"].get(name)
        if after is None:
            if not _same_file_capture(original, candidate):
                raise RecoveryRequired(f"unchanged inventory entry drifted: {name}")
            continue
        is_before = _same_file_capture(original, candidate)
        is_after = _after_capture_is_exact(candidate, original, after)
        if is_before == is_after:
            raise RecoveryRequired(
                f"changed target matches neither one unambiguous BEFORE/AFTER state: {name}"
            )
        states[name] = "BEFORE" if is_before else "AFTER"

    vector = [states[name] for name in changed_names]
    prefix = 0
    while prefix < len(vector) and vector[prefix] == "AFTER":
        prefix += 1
    if any(state != "BEFORE" for state in vector[prefix:]):
        raise RecoveryRequired("changed targets are not an exact AFTER prefix and BEFORE suffix")
    return states, current


def _write_transaction_artifacts(plan: dict) -> None:
    trusted_fs.write_idempotent_bytes(
        plan["paths"]["sidecar_snapshot"], plan["sidecar"]["data"],
    )
    for name in sorted(plan["after_by_name"]):
        source = plan["inventory"]["by_name"][name]
        trusted_fs.write_idempotent_bytes(
            plan["paths"]["backup_by_name"][name], source["data"],
        )
        trusted_fs.write_idempotent_bytes(
            plan["paths"]["stage_by_name"][name], plan["after_by_name"][name],
        )
    _validate_artifacts(plan)


def _write_live_journal(plan: dict) -> None:
    if _live_journal_exists(plan["paths"]["root"]):
        raise RecoveryRequired("a live journal appeared before PREPARED commit")
    trusted_fs.atomic_write_bytes(plan["paths"]["live"], plan["journal_bytes"])
    installed = _read_owner_only_artifact(plan["paths"]["live"], "live journal")
    if installed != plan["journal_bytes"]:
        raise RecoveryRequired("durable live journal bytes differ from PREPARED plan")


def _remove_owned_private_entry(parent_fd: int, name: str,
                                owned: os.stat_result) -> None:
    """Retire only an unlinked install temp still bound to our exact inode."""
    try:
        current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if (current.st_dev, current.st_ino) == (owned.st_dev, owned.st_ino):
        os.unlink(name, dir_fd=parent_fd)


def _replace_target(stage: Path, target: Path, original: dict,
                    after: bytes) -> None:
    """Descriptor-safely install retained stage bytes with verified identity.

    This follows the resolver's verified replacement pattern but preserves the
    canonical geom file's safe permission mode.  The unpredictable private
    install entry is not a transaction artifact; if installation fails before
    rename, only the exact fd-owned inode is retired.
    """
    trusted_fs._require_acl_platform_support()
    nofollow = trusted_fs._required_flag("O_NOFOLLOW")
    cloexec = getattr(os, "O_CLOEXEC", 0)
    stage_parent = trusted_fs.open_trusted_directory_fd(stage.parent)
    target_parent = trusted_fs.open_trusted_directory_fd(target.parent)
    stage_fd = private_fd = target_fd = None
    private_name = None
    private_stat = None
    renamed = False
    try:
        stage_fd = os.open(stage.name, os.O_RDONLY | nofollow | cloexec,
                           dir_fd=stage_parent)
        stage_stat = _stat_signature(os.fstat(stage_fd))
        trusted_fs.require_trivial_acl_fd(stage_fd, stage)
        _require_safe_regular(stage_stat, stage, "transaction stage")
        if stat.S_IMODE(stage_stat["mode"]) != 0o600:
            raise RecoveryRequired(f"transaction stage is not mode 0600: {stage}")
        stage_bytes = _read_fd_bytes(stage_fd)
        stage_entry = _stat_signature(os.stat(
            stage.name, dir_fd=stage_parent, follow_symlinks=False,
        ))
        if stage_stat != stage_entry or stage_bytes != after:
            raise RecoveryRequired(f"transaction stage changed before replace: {stage}")

        before = _capture_entry(
            target_parent, target.parent, target.name, "replacement target",
        )
        if not _same_file_capture(original, before):
            raise RecoveryRequired(f"replacement target is no longer exact BEFORE: {target}")

        permissions = stat.S_IMODE(original["signature"]["mode"])
        if permissions & 0o022:
            raise RecoveryRequired(f"replacement target mode is unsafe: {target}")
        flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | nofollow | cloexec
        for _attempt in range(128):
            candidate = f".parking-sweep-install-{secrets.token_hex(24)}"
            try:
                private_fd = os.open(candidate, flags, permissions,
                                     dir_fd=target_parent)
            except FileExistsError:
                continue
            private_name = candidate
            private_stat = os.fstat(private_fd)
            break
        else:
            raise FileExistsError("could not allocate a private verified install entry")

        view = memoryview(after)
        while view:
            written = os.write(private_fd, view)
            if written <= 0:
                raise OSError("verified install write made no progress")
            view = view[written:]
        os.fchown(
            private_fd,
            original["signature"]["uid"],
            original["signature"]["gid"],
        )
        os.fchmod(private_fd, permissions)
        os.fsync(private_fd)
        trusted_fs.require_trivial_acl_fd(private_fd, target)
        private_after = os.fstat(private_fd)
        private_entry = os.stat(
            private_name, dir_fd=target_parent, follow_symlinks=False,
        )
        private_bytes = _read_fd_bytes(private_fd)
        private_identity = (private_after.st_dev, private_after.st_ino)
        for value in (private_after, private_entry):
            signature = _stat_signature(value)
            _require_safe_regular(signature, target, "private verified install")
            if (value.st_dev, value.st_ino) != private_identity:
                raise RecoveryRequired("private verified install identity changed")
            if (value.st_uid != original["signature"]["uid"]
                    or value.st_gid != original["signature"]["gid"]):
                raise RecoveryRequired(
                    "private verified install ownership changed before rename"
                )
            if stat.S_IMODE(value.st_mode) != permissions:
                raise RecoveryRequired("private verified install mode changed")
        if private_bytes != after or private_after.st_size != len(after):
            raise RecoveryRequired("private verified install bytes changed")

        # Recheck the named BEFORE immediately before the cooperative rename.
        before_again = _capture_entry(
            target_parent, target.parent, target.name, "replacement target",
        )
        if not _same_file_capture(original, before_again):
            raise RecoveryRequired(f"replacement target drifted before rename: {target}")
        os.replace(
            private_name, target.name,
            src_dir_fd=target_parent, dst_dir_fd=target_parent,
        )
        renamed = True

        target_fd = os.open(target.name, os.O_RDONLY | nofollow | cloexec,
                            dir_fd=target_parent)
        target_stat = os.fstat(target_fd)
        target_entry = os.stat(
            target.name, dir_fd=target_parent, follow_symlinks=False,
        )
        trusted_fs.require_trivial_acl_fd(target_fd, target)
        target_bytes = _read_fd_bytes(target_fd)
        for value in (os.fstat(private_fd), target_stat, target_entry):
            if ((value.st_dev, value.st_ino) != private_identity
                    or not stat.S_ISREG(value.st_mode)
                    or value.st_uid != original["signature"]["uid"]
                    or value.st_gid != original["signature"]["gid"]
                    or value.st_nlink != 1
                    or stat.S_IMODE(value.st_mode) != permissions
                    or value.st_size != len(after)):
                raise RecoveryRequired(f"installed target identity is invalid: {target}")
        if target_bytes != after:
            raise RecoveryRequired(f"installed target bytes are invalid: {target}")
        os.fsync(target_parent)
    finally:
        try:
            if (not renamed and private_name is not None and private_stat is not None):
                _remove_owned_private_entry(target_parent, private_name, private_stat)
        finally:
            for fd in (target_fd, private_fd, stage_fd):
                if fd is not None:
                    os.close(fd)
            os.close(target_parent)
            os.close(stage_parent)


def _archive_journal(plan: dict) -> None:
    source = plan["paths"]["live"]
    target = plan["paths"]["archive"]
    source_parent = trusted_fs.open_trusted_directory_fd(source.parent)
    target_parent = trusted_fs.open_trusted_directory_fd(
        target.parent, create=True, create_mode=0o700,
    )
    source_fd = target_fd = None
    try:
        try:
            os.stat(target.name, dir_fd=target_parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise RecoveryRequired(f"journal archive target already exists: {target}")
        nofollow = trusted_fs._required_flag("O_NOFOLLOW")
        source_fd = os.open(
            source.name, os.O_RDONLY | nofollow | getattr(os, "O_CLOEXEC", 0),
            dir_fd=source_parent,
        )
        source_stat = os.fstat(source_fd)
        trusted_fs.require_trivial_acl_fd(source_fd, source)
        source_bytes = _read_fd_bytes(source_fd)
        source_entry = os.stat(
            source.name, dir_fd=source_parent, follow_symlinks=False,
        )
        if ((source_stat.st_dev, source_stat.st_ino)
                != (source_entry.st_dev, source_entry.st_ino)
                or source_bytes != plan["journal_bytes"]):
            raise RecoveryRequired("live journal changed before archival")
        os.replace(
            source.name, target.name,
            src_dir_fd=source_parent, dst_dir_fd=target_parent,
        )
        target_fd = os.open(
            target.name, os.O_RDONLY | nofollow | getattr(os, "O_CLOEXEC", 0),
            dir_fd=target_parent,
        )
        target_stat = os.fstat(target_fd)
        target_bytes = _read_fd_bytes(target_fd)
        if ((target_stat.st_dev, target_stat.st_ino)
                != (source_stat.st_dev, source_stat.st_ino)
                or target_bytes != plan["journal_bytes"]):
            raise RecoveryRequired("archived journal identity or bytes changed")
        os.fsync(source_parent)
        os.fsync(target_parent)
    finally:
        if target_fd is not None:
            os.close(target_fd)
        if source_fd is not None:
            os.close(source_fd)
        os.close(target_parent)
        os.close(source_parent)


def _finish_transaction(plan: dict) -> None:
    live = _read_owner_only_artifact(plan["paths"]["live"], "live journal")
    if live != plan["journal_bytes"]:
        raise RecoveryRequired("live journal does not match reconstructed PREPARED plan")
    _validate_artifacts(plan)
    changed_names = sorted(plan["after_by_name"])
    for name in changed_names:
        states, _current = _classify_transaction(plan)
        if states[name] == "AFTER":
            continue
        _replace_target(
            plan["paths"]["stage_by_name"][name],
            plan["inventory"]["path"] / name,
            plan["inventory"]["by_name"][name],
            plan["after_by_name"][name],
        )
    states, _current = _classify_transaction(plan)
    if any(states[name] != "AFTER" for name in changed_names):
        raise RecoveryRequired("not every changed target reached exact AFTER")
    trusted_fs.write_idempotent_bytes(
        plan["paths"]["receipt"], plan["receipt_bytes"],
    )
    receipt = _read_owner_only_artifact(plan["paths"]["receipt"], "terminal receipt")
    if receipt != plan["receipt_bytes"]:
        raise RecoveryRequired("terminal receipt differs from reconstructed plan")
    states, _current = _classify_transaction(plan)
    if any(states[name] != "AFTER" for name in changed_names):
        raise RecoveryRequired("postcondition changed while live journal remained")


def _validate_signature(value: object, fields: tuple[str, ...], label: str) -> dict:
    if type(value) is not dict or set(value) != set(fields):
        raise RecoveryRequired(f"{label} signature schema is invalid")
    if any(type(value[field]) is not int for field in fields):
        raise RecoveryRequired(f"{label} signature values must be integers")
    return value


def _validate_recovery_header(journal: object, raw: bytes, sidecar: Path,
                              geom_dir: Path) -> tuple[list[dict], dict]:
    if type(journal) is not dict:
        raise RecoveryRequired("live journal root is not an object")
    required = {
        "version", "kind", "state", "transaction_id", "identity", "report",
        "receipt", "artifacts",
    }
    if set(journal) != required:
        raise RecoveryRequired("live journal root schema is invalid")
    if (journal["version"] != _TRANSACTION_VERSION
            or journal["kind"] != _TRANSACTION_KIND
            or journal["state"] != "PREPARED"):
        raise RecoveryRequired("live journal contract is invalid")
    if raw != _canonical_json(journal):
        raise RecoveryRequired("live journal bytes are not canonical")
    identity = journal["identity"]
    identity_fields = {
        "contract", "geom_dir", "geom_directory_signature", "sidecar",
        "reviewed_empty_policy", "inventory",
    }
    if type(identity) is not dict or set(identity) != identity_fields:
        raise RecoveryRequired("live journal identity schema is invalid")
    if identity["contract"] != "parking-verdict-sweep-v2":
        raise RecoveryRequired("live journal identity contract is invalid")
    transaction_id = journal["transaction_id"]
    if (type(transaction_id) is not str or len(transaction_id) != 64
            or any(character not in "0123456789abcdef" for character in transaction_id)
            or transaction_id != _sha256(_canonical_json(identity))):
        raise RecoveryRequired("live journal transaction id is invalid")
    if identity["geom_dir"] != str(geom_dir):
        raise RecoveryRequired("CLI geom_dir differs from the live journal authority")
    sidecar_identity = identity["sidecar"]
    if (type(sidecar_identity) is not dict
            or set(sidecar_identity) != {"path", "length", "sha256"}
            or sidecar_identity["path"] != str(sidecar)
            or type(sidecar_identity["length"]) is not int
            or sidecar_identity["length"] < 0
            or type(sidecar_identity["sha256"]) is not str
            or len(sidecar_identity["sha256"]) != 64
            or any(character not in "0123456789abcdef"
                   for character in sidecar_identity["sha256"])):
        raise RecoveryRequired("live journal sidecar identity is invalid")
    policy_lookup, _policy_serialized = _approved_policy_from_identity(
        identity["reviewed_empty_policy"],
    )
    _validate_signature(
        identity["geom_directory_signature"],
        _DIRECTORY_SIGNATURE_FIELDS, "geom directory",
    )
    report = journal["report"]
    if (type(report) is not dict or set(report) != {"stdout", "stderr"}
            or type(report["stdout"]) is not str
            or type(report["stderr"]) is not str):
        raise RecoveryRequired("live journal report schema is invalid")
    inventory = identity["inventory"]
    if type(inventory) is not list or not inventory:
        raise RecoveryRequired("live journal inventory is invalid")
    names = []
    for item in inventory:
        if type(item) is not dict:
            raise RecoveryRequired("live journal inventory item is not an object")
        base = {"name", "before_length", "before_sha256", "before_signature"}
        changed = {"after_length", "after_sha256"}
        if set(item) not in (base, base | changed):
            raise RecoveryRequired("live journal inventory item schema is invalid")
        name = item.get("name")
        if (type(name) is not str or Path(name).name != name
                or not name.endswith(".json")):
            raise RecoveryRequired("live journal inventory basename is invalid")
        try:
            _reject_untrusted_text(name, "live journal inventory basename")
        except ValueError as error:
            raise RecoveryRequired(str(error)) from error
        for prefix in ("before", "after") if changed <= set(item) else ("before",):
            length = item.get(f"{prefix}_length")
            digest = item.get(f"{prefix}_sha256")
            if (type(length) is not int or length < 0 or type(digest) is not str
                    or len(digest) != 64
                    or any(character not in "0123456789abcdef" for character in digest)):
                raise RecoveryRequired(f"live journal {prefix} image is invalid")
        _validate_signature(
            item["before_signature"], _FILE_SIGNATURE_FIELDS,
            f"inventory {name}",
        )
        if changed <= set(item) and (
                item["before_length"] == item["after_length"]
                and item["before_sha256"] == item["after_sha256"]):
            raise RecoveryRequired(f"changed inventory image is identical: {name}")
        names.append(name)
    if names != sorted(set(names)):
        raise RecoveryRequired("live journal inventory is not sorted and unique")
    return inventory, policy_lookup


def _synthetic_before_capture(current: dict, item: dict, data: bytes) -> dict:
    document = _strict_json(data, f"recovery backup for {current['path']}")
    _validate_geom(document, current["path"])
    return {
        "path": current["path"],
        "name": current["name"],
        "data": data,
        "length": len(data),
        "sha256": _sha256(data),
        "signature": item["before_signature"],
        "document": document,
    }


def _recover_plan(sidecar_path: Path, geom_dir: Path,
                  root: Path, lock_entries: tuple) -> dict:
    raw = _read_owner_only_artifact(root / _LIVE_JOURNAL_NAME, "live journal")
    try:
        journal = _strict_json(raw, "live journal")
    except ValueError as error:
        raise RecoveryRequired(str(error)) from error
    inventory_items, policy = _validate_recovery_header(
        journal, raw, sidecar_path, geom_dir,
    )
    transaction_id = journal["transaction_id"]
    changed_names = sorted(
        item["name"] for item in inventory_items if "after_sha256" in item
    )
    if not changed_names:
        raise RecoveryRequired("live journal has no changed targets")
    derived_paths = _artifact_paths(root, transaction_id, changed_names)
    expected_artifacts = {
        "root": str(derived_paths["root"]),
        "live_journal": str(derived_paths["live"]),
        "sidecar_snapshot": str(derived_paths["sidecar_snapshot"]),
        "receipt": str(derived_paths["receipt"]),
        "archive": str(derived_paths["archive"]),
        "targets": [
            {
                "name": name,
                "target": str(geom_dir / name),
                "backup": str(derived_paths["backup_by_name"][name]),
                "stage": str(derived_paths["stage_by_name"][name]),
            }
            for name in changed_names
        ],
    }
    if journal["artifacts"] != expected_artifacts:
        raise RecoveryRequired("live journal artifact paths are not canonical")

    sidecar_bytes = _read_owner_only_artifact(
        derived_paths["sidecar_snapshot"], "sidecar snapshot",
    )
    sidecar_identity = journal["identity"]["sidecar"]
    if (len(sidecar_bytes) != sidecar_identity["length"]
            or _sha256(sidecar_bytes) != sidecar_identity["sha256"]):
        raise RecoveryRequired("sidecar snapshot does not match journal authority")
    try:
        sidecar_document = _strict_json(sidecar_bytes, "sidecar snapshot")
        sidecar_verdicts = _validate_sidecar(sidecar_document, sidecar_path)
    except ValueError as error:
        raise RecoveryRequired(f"sidecar snapshot schema is invalid: {error}") from error
    sidecar = {
        "path": sidecar_path,
        "name": sidecar_path.name,
        "data": sidecar_bytes,
        "length": len(sidecar_bytes),
        "sha256": _sha256(sidecar_bytes),
        "document": sidecar_document,
        "verdicts": sidecar_verdicts,
    }
    current = _capture_inventory(geom_dir)
    expected_names = tuple(item["name"] for item in inventory_items)
    if current["names"] != expected_names:
        raise RecoveryRequired("recovery inventory has an extra or missing JSON file")
    if (_directory_identity(current["signature"])
            != _directory_identity(journal["identity"]["geom_directory_signature"])):
        raise RecoveryRequired("recovery geom directory identity or mode changed")

    before_files = []
    states = []
    by_item = {item["name"]: item for item in inventory_items}
    for name in current["names"]:
        item = by_item[name]
        capture = current["by_name"][name]
        before_exact = (
            capture["length"] == item["before_length"]
            and capture["sha256"] == item["before_sha256"]
            and capture["signature"] == item["before_signature"]
        )
        if "after_sha256" not in item:
            if not before_exact:
                raise RecoveryRequired(f"unchanged recovery inventory drifted: {name}")
            before_files.append(capture)
            continue

        backup_path = derived_paths["backup_by_name"][name]
        stage_path = derived_paths["stage_by_name"][name]
        backup = _read_owner_only_artifact(backup_path, f"backup for {name}")
        stage = _read_owner_only_artifact(stage_path, f"stage for {name}")
        if (len(backup) != item["before_length"]
                or _sha256(backup) != item["before_sha256"]):
            raise RecoveryRequired(f"backup does not match journal BEFORE: {name}")
        if (len(stage) != item["after_length"]
                or _sha256(stage) != item["after_sha256"]):
            raise RecoveryRequired(f"stage does not match journal AFTER: {name}")
        after_exact = (
            capture["length"] == item["after_length"]
            and capture["sha256"] == item["after_sha256"]
            and capture["signature"]["uid"] == item["before_signature"]["uid"]
            and capture["signature"]["gid"] == item["before_signature"]["gid"]
            and stat.S_IMODE(capture["signature"]["mode"])
            == stat.S_IMODE(item["before_signature"]["mode"])
            and capture["signature"]["nlink"] == 1
        )
        if before_exact == after_exact:
            raise RecoveryRequired(
                f"recovery target matches neither one unambiguous BEFORE/AFTER: {name}"
            )
        states.append("BEFORE" if before_exact else "AFTER")
        before_files.append(
            capture if before_exact else _synthetic_before_capture(capture, item, backup)
        )

    prefix = 0
    while prefix < len(states) and states[prefix] == "AFTER":
        prefix += 1
    if any(state != "BEFORE" for state in states[prefix:]):
        raise RecoveryRequired("recovery targets are not an AFTER prefix and BEFORE suffix")

    synthetic_inventory = {
        "path": geom_dir,
        "signature": journal["identity"]["geom_directory_signature"],
        "names": expected_names,
        "files": before_files,
        "by_name": {item["name"]: item for item in before_files},
    }
    plan = _plan_inventory(sidecar, synthetic_inventory, policy, dry_run=False)
    if plan["result"]["refused"]:
        raise RecoveryRequired("reconstructed transaction now contains a refusal")
    _prepare_transaction(plan)
    _validate_transaction_paths(plan, lock_entries)
    if plan["journal_bytes"] != raw:
        raise RecoveryRequired("live journal differs from independently reconstructed plan")
    _validate_artifacts(plan)
    _validate_terminal_recovery_artifacts(plan)
    return plan


def _print_report(plan: dict, *, recovered: bool = False) -> None:
    sys.stdout.write(plan["stdout"])
    if recovered:
        sys.stdout.write(
            f"RECOVERED {plan['transaction_id']} — transaction rolled forward\n"
        )
    if plan["stderr"]:
        sys.stderr.write(plan["stderr"])
    sys.stdout.flush()
    sys.stderr.flush()


def sweep(geom_dir: str, verdicts: pv.Verdicts, dry_run: bool, *,
          reviewed_empty_signatures: dict[str, tuple] | None = None) -> dict:
    """Compatibility planner returning the historical result dictionary.

    This API is intentionally pure even when ``dry_run`` is false; mutation
    without a captured sidecar path could bypass the sidecar resource lock and
    durable journal.  Call ``main``/``run`` for transactional apply.
    """
    if not isinstance(verdicts, pv.Verdicts):
        raise TypeError("sweep planner requires a parking Verdicts instance")
    inventory = _capture_inventory(_canonical_path(geom_dir))
    sidecar = {"verdicts": verdicts}
    policy = (_REVIEWED_EMPTY_SIGNATURES if reviewed_empty_signatures is None
              else reviewed_empty_signatures)
    return _plan_inventory(sidecar, inventory, policy, dry_run)["result"]


def run(geom_dir: str | os.PathLike, sidecar: str | os.PathLike, dry_run: bool,
        *, reviewed_empty_signatures: dict[str, tuple] | None = None) -> int:
    geom_path = _canonical_path(geom_dir)
    sidecar_path = _canonical_path(sidecar)
    policy = (_REVIEWED_EMPTY_SIGNATURES if reviewed_empty_signatures is None
              else reviewed_empty_signatures)
    root, lock_entries = _validate_initial_paths(sidecar_path, geom_path)

    if dry_run:
        if _live_journal_exists(root):
            raise RecoveryRequired(
                "live parking sweep journal exists; dry-run never performs recovery"
            )
        first_sidecar = _capture_sidecar(sidecar_path)
        first_inventory = _capture_inventory(geom_path)
        plan = _plan_inventory(first_sidecar, first_inventory, policy, dry_run=True)
        _require_initial_unchanged(plan)
        _print_report(plan)
        return 2 if plan["result"]["refused"] else 0

    resources = [
        (sidecar_path, fcntl.LOCK_SH),
        (geom_path, fcntl.LOCK_EX),
        (root / _LIVE_JOURNAL_NAME, fcntl.LOCK_EX),
    ]
    with trusted_fs.locked_resources(
            root, resources, tr.resource_lock_path) as acquired:
        # Ensure the actual acquired order/set is exactly the collision-checked set.
        if tuple(acquired) != tuple(lock_entries):
            raise ValueError("acquired resource lock set differs from preflight")
        _require_owner_only_root(root)
        if _live_journal_exists(root):
            recovery = _recover_plan(
                sidecar_path, geom_path, root, tuple(acquired),
            )
            _finish_transaction(recovery)
            _print_report(recovery, recovered=True)
            _archive_journal(recovery)
            return 0

        sidecar_capture = _capture_sidecar(sidecar_path)
        inventory = _capture_inventory(geom_path)
        plan = _plan_inventory(
            sidecar_capture, inventory, policy, dry_run=False,
        )
        _require_initial_unchanged(plan)
        if plan["result"]["refused"]:
            _print_report(plan)
            return 2
        if not plan["after_by_name"]:
            _print_report(plan)
            return 0

        _prepare_transaction(plan)
        _validate_transaction_paths(plan, tuple(acquired))
        _write_transaction_artifacts(plan)
        _require_initial_unchanged(plan)
        try:
            _write_live_journal(plan)
        except Exception as error:
            if _live_journal_exists(root):
                raise RecoveryRequired(
                    f"PREPARED journal may be durable after write failure: {error}"
                ) from error
            raise
        try:
            _finish_transaction(plan)
        except Exception as error:
            if _live_journal_exists(root):
                raise RecoveryRequired(
                    f"live transaction retained after interrupted apply: {error}"
                ) from error
            raise
        _print_report(plan)
        _archive_journal(plan)
        return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="apply the parking verdicts sidecar to geom transactionally",
    )
    parser.add_argument(
        "--geom-dir", default=str(_ROOT / "public" / "areas" / "geom"),
    )
    parser.add_argument("--sidecar", default=pv.DEFAULT_PATH)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        return run(args.geom_dir, args.sidecar, args.dry_run)
    except RecoveryRequired as error:
        print(f"RECOVERY_REQUIRED: {error}", file=sys.stderr)
        return 1
    except Exception as error:  # one fail-closed CLI boundary; nothing is skipped
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
