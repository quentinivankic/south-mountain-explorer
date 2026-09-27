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
is appended atomically to its canonical draft while preserving the existing
bytes, then archived. A checkpoint manifest binds every completed prefix to a
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
import sys
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
import trust_resolution as tr  # noqa: E402

PADJ_TMP = os.environ.get("PADJ_TMP") or os.path.join(_HERE, "..", "work")
NON_PUBLIC_ACCESS = ("private", "no", "customers")
PROMPT_TEMPLATE = os.path.join(_HERE, "judge_agent_prompt.md")
CHECKPOINT_VERSION = 1


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_sha(value: object) -> str:
    return _sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                              ensure_ascii=False).encode())


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


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


def build_packets(slug: str, tmp: str, tiles_dir: str | None) -> dict[int, dict]:
    dossier = json.loads(Path(tmp, f"{slug}_dossier.json").read_text())
    serves = json.loads(Path(tmp, f"{slug}_serves2.json").read_text())
    context = json.loads(Path(tmp, f"{slug}_context.json").read_text())
    walk = json.loads(Path(tmp, f"{slug}_walk.json").read_text())
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
    """Hash every decision input, including the imagery bytes and judge rules."""
    missing: list[str] = []
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
            "packet": {key: value for key, value in packet.items() if key != "tiles"},
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
    return _canonical_sha({"rules": rules, "packets": packet_rows}), []


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
        if not isinstance(fid, int):
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


def inspect_draft(chunk: list[dict], path: str | Path,
                  allow_override: bool = True) -> dict:
    draft_path = Path(path)
    expected = [packet["fid"] for packet in chunk]
    if not draft_path.exists():
        return _state("missing", [], expected)
    try:
        raw = draft_path.read_bytes()
        rows = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        return _state("invalid", [], expected, [f"cannot parse {draft_path.name}: {exc}"])
    state = inspect_rows(chunk, rows, draft_path.name, allow_override=allow_override)
    state["file_sha256"] = _sha256(raw)
    return state


def _manifest_path(tmp: str | Path, slug: str, chunk_index: int) -> Path:
    return Path(tmp, f"{slug}_checkpoint_{chunk_index:02d}.json")


def _draft_path(tmp: str | Path, slug: str, chunk_index: int) -> Path:
    return Path(tmp, f"{slug}_verdict_draft_{chunk_index:02d}.json")


def _continue_path(tmp: str | Path, slug: str, chunk_index: int) -> Path:
    return Path(tmp, f"{slug}_verdict_continue_{chunk_index:02d}.json")


def manifest_value(slug: str, chunk_index: int, chunk: list[dict], decision_sha256: str,
                   state: dict) -> dict:
    return {
        "version": CHECKPOINT_VERSION,
        "area": slug,
        "chunk": chunk_index,
        "fids": [packet["fid"] for packet in chunk],
        "decision_sha256": decision_sha256,
        "completed": state["completed"],
        "draft_sha256": state["file_sha256"],
        "judge_row_sha256": state["row_sha256"],
        "resolution_row_sha256": state["resolution_sha256"],
    }


def validate_manifest_static(value: object, slug: str, chunk_index: int,
                             chunk: list[dict], decision_sha256: str) -> list[str]:
    """Validate fields that do not depend on the canonical draft's progress."""
    if not isinstance(value, dict):
        return ["checkpoint manifest must be an object"]
    errors = []
    expected = {
        "version": CHECKPOINT_VERSION,
        "area": slug,
        "chunk": chunk_index,
        "fids": [packet["fid"] for packet in chunk],
        "decision_sha256": decision_sha256,
    }
    for key, wanted in expected.items():
        if value.get(key) != wanted:
            errors.append(f"checkpoint {key} changed: {value.get(key)!r} != {wanted!r}")
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
    stored_resolution = value.get("resolution_row_sha256", value.get("judge_row_sha256"))
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


def _append_json_arrays(existing: bytes | None, addition: bytes) -> bytes:
    """Append list members while retaining every existing prefix byte."""
    added = json.loads(addition)
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


def _archive_continuation(path: Path) -> Path:
    archive = path.parent / "Archive"
    archive.mkdir(exist_ok=True)
    digest = _sha256(path.read_bytes())[:12]
    target = archive / f"{path.stem}.merged-{digest}.json"
    counter = 2
    while target.exists():
        target = archive / f"{path.stem}.merged-{digest}-{counter}.json"
        counter += 1
    os.replace(path, target)
    return target


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
                                      continuation: Path) -> tuple[str | None, dict | None, list[str]]:
    """Classify or recover one continuation transaction without writing.

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
        normal = inspect_draft(suffix, continuation, allow_override=False)
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
            replay = inspect_draft(chunk[old_count:], continuation, allow_override=False)
            if (not replay["errors"] and replay["completed"]
                    and state["completed"] == old_completed + replay["completed"]
                    and state["row_sha256"] == old_hashes + replay["row_sha256"]
                    and state["resolution_sha256"]
                    == old_resolutions + replay["resolution_sha256"]):
                return "refresh_manifest", replay, []

    # Crash after manifest replacement but before continuation archival: the
    # manifest binds the advanced draft and the continuation duplicates its
    # exact final rows. It is safe to archive without appending again.
    try:
        raw_rows = json.loads(continuation.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return None, None, [f"cannot parse {continuation.name}: {exc}"]
    if (_manifest_matches_state(manifest, state) and isinstance(raw_rows, list)
            and raw_rows and len(raw_rows) <= len(state["completed"])):
        start = len(state["completed"]) - len(raw_rows)
        duplicate = inspect_draft(chunk[start:], continuation, allow_override=False)
        if (not duplicate["errors"]
                and duplicate["completed"] == state["completed"][start:]
                and duplicate["row_sha256"] == state["row_sha256"][start:]
                and duplicate["resolution_sha256"] == state["resolution_sha256"][start:]):
            return "archive_only", duplicate, []

    return None, None, [
        f"{continuation.name} is neither a new packet suffix nor an exact "
        "recoverable canonical suffix"
    ]


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


def _main_under_area_lock(args: argparse.Namespace) -> int:
    parser = _argument_parser()
    if args.chunk <= 0:
        parser.error("--chunk must be positive")

    packets = build_packets(args.slug, args.tmp, args.tiles)
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
        if continuation.exists():
            if manifest is None:
                errors.append(f"chunk {index:02d}: {continuation.name} requires an existing "
                              "checkpoint manifest")
            else:
                continuation_mode, continuation_state, transaction_errors = (
                    classify_continuation_transaction(
                        rows, state, manifest, args.slug, index, decision or "", continuation))
                errors.extend(f"chunk {index:02d}: {error}" for error in transaction_errors)
        elif manifest is not None:
            errors.extend(f"chunk {index:02d}: {error}" for error in
                          validate_manifest(manifest, args.slug, index, rows,
                                            decision or "", state))
        elif state["status"] != "missing" and not args.adopt_existing:
            errors.append(f"chunk {index:02d}: {draft.name} has no checkpoint manifest; "
                          "independently validate it, then rerun with --adopt-existing")
        continuation_states.append(continuation_state)
        continuation_modes.append(continuation_mode)

    if errors:
        if args.status_json:
            print(json.dumps({"version": 1, "area": args.slug, "status": "invalid",
                              "errors": errors}), file=sys.stderr)
        else:
            for error in errors:
                print(f"INVALID_CHECKPOINT\t{error}", file=sys.stderr)
            print("refusing to rewrite artifacts or merge continuations", file=sys.stderr)
        return 2

    # All chunks passed preflight. The recoverable transaction order is:
    # canonical draft -> refreshed manifest -> archived continuation. A retry can
    # prove and finish either interrupted boundary without appending twice.
    root = Path(args.tmp)
    root.mkdir(parents=True, exist_ok=True)
    for index, rows in enumerate(chunk_rows):
        draft = _draft_path(args.tmp, args.slug, index)
        continuation = _continue_path(args.tmp, args.slug, index)
        mode = continuation_modes[index]
        if mode == "merge":
            merged = _append_json_arrays(draft.read_bytes() if draft.exists() else None,
                                         continuation.read_bytes())
            _atomic_write(draft, merged)
            states[index] = inspect_draft(rows, draft)
            if states[index]["status"] == "invalid":  # defensive: impossible after preflight
                raise RuntimeError(f"host merge produced invalid {draft.name}: {states[index]['errors']}")
        elif mode not in (None, "refresh_manifest", "archive_only"):
            raise RuntimeError(f"unknown continuation transaction mode {mode!r}")

        # This manifest write also refreshes byte hashes after a legal human
        # override or completes recovery when the draft replacement landed first.
        _atomic_json(_manifest_path(root, args.slug, index),
                     manifest_value(args.slug, index, rows, decisions[index], states[index]))
        if mode is not None:
            _archive_continuation(continuation)

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
    lock_path = tr.area_lock_path(args.tmp, args.slug)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+b") as area_lock:
        fcntl.flock(area_lock.fileno(), fcntl.LOCK_EX)
        return _main_under_area_lock(args)


if __name__ == "__main__":
    raise SystemExit(main())
