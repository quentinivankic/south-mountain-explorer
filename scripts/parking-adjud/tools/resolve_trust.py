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
import fcntl
import hashlib
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DATA = ROOT / "scripts" / "parking-adjud" / "data"
DEFAULT_TMP = ROOT / "scripts" / "parking-adjud" / "work"
for value in (str(HERE), str(ROOT / "scripts")):
    if value not in sys.path:
        sys.path.insert(0, value)

import judge_packets  # noqa: E402
from judge_validation import (  # noqa: E402
    canonical_draft_files,
    original_judge_projection,
    validate_verdict_row,
)
import replay_trust  # noqa: E402
import trust_engine  # noqa: E402
import trust_resolution as tr  # noqa: E402

PREPARE_VERSION = 1
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_TOKEN_RE = re.compile(r"\{([A-Z0-9_]+)\}")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=1, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")


def _fsync_dir(path: Path) -> None:
    directory = os.open(path, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    with open(temporary, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    _fsync_dir(path.parent)


def _atomic_json(path: Path, value: object) -> None:
    _atomic_bytes(path, _json_bytes(value))


def _load_json(path: Path, expected: type = dict):
    value = json.loads(path.read_text())
    if not isinstance(value, expected):
        raise ValueError(f"{path} must contain a {expected.__name__}")
    return value


def _write_idempotent(path: Path, data: bytes) -> None:
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError(f"prepared artifact drifted: {path}")
        return
    _atomic_bytes(path, data)


def _parse_model(value: str) -> dict:
    parts = value.split(":")
    if len(parts) not in (2, 3, 4):
        raise ValueError("model wants ID:FAMILY[:PROVIDER[:BUILD]]")
    parts += ["kiro", "unspecified"][len(parts) - 2:]
    return tr.model_config(parts[0], parts[1], parts[2], parts[3])


def _render(path: Path, bindings: dict[str, str]) -> bytes:
    text = path.read_text()
    tokens = _TOKEN_RE.findall(text)
    if sorted(tokens) != sorted(bindings):
        raise ValueError(f"{path.name} placeholders {sorted(tokens)} != {sorted(bindings)}")
    for token, value in bindings.items():
        text = text.replace("{" + token + "}", value)
    if _TOKEN_RE.search(text):
        raise ValueError(f"{path.name} retains an unresolved placeholder")
    return text.encode("utf-8")


def _packet_map(tmp: Path, slug: str) -> tuple[Path, dict[int, dict]]:
    path = tmp / f"{slug}_packets.json"
    raw = _load_json(path)
    packets = {}
    for key, packet in raw.items():
        if not isinstance(packet, dict) or not isinstance(packet.get("fid"), int):
            raise ValueError(f"{path}: malformed packet {key!r}")
        if str(packet["fid"]) != str(key) or packet.get("area") != slug:
            raise ValueError(f"{path}: packet key/fid/area mismatch at {key!r}")
        packets[packet["fid"]] = packet
    return path, packets


def _source_chunks(tmp: Path, slug: str, packets: dict[int, dict]) -> list[dict]:
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
        rows = _load_json(path, list)
        chunk_packets = []
        for row in rows:
            fid = row.get("fid") if isinstance(row, dict) else None
            if fid not in packets:
                raise ValueError(f"{path.name}: unknown fid {fid!r}")
            chunk_packets.append(packets[fid])
        state = judge_packets.inspect_rows(chunk_packets, rows, path.name)
        if state["status"] != "complete":
            raise ValueError(f"{path.name} is not a complete valid chunk: {state['errors']}")
        continuation = tmp / f"{slug}_verdict_continue_{index:02d}.json"
        if continuation.exists():
            raise ValueError(f"{continuation.name} is still live; finish its transaction first")
        checkpoint = tmp / f"{slug}_checkpoint_{index:02d}.json"
        manifest = _load_json(checkpoint)
        decision_input, missing = judge_packets.decision_fingerprint(chunk_packets)
        if missing or not decision_input:
            raise ValueError(f"chunk {index:02d} missing decision inputs: {missing}")
        manifest_errors = judge_packets.validate_manifest(
            manifest, slug, index, chunk_packets, decision_input, state
        )
        if manifest_errors:
            raise ValueError(f"chunk {index:02d} checkpoint invalid: {manifest_errors}")
        chunks.append({
            "chunk": index,
            "draft": path,
            "checkpoint": checkpoint,
            "rows": rows,
            "packets": chunk_packets,
            "state": state,
            "manifest": manifest,
            "decision_input_sha256": decision_input,
        })
    return chunks


def _assignment_id(run_id: str, fid: int, role: str, model: dict,
                   packet_sha: str, template_sha: str) -> str:
    return tr.sha256_json({
        "run_id": run_id, "fid": fid, "role": role, "model": model,
        "packet_sha256": packet_sha, "template_sha256": template_sha,
    })


def _prompt(role: str, run_dir: Path, assignment_id: str, packet_path: Path,
            output_path: Path, model: dict) -> bytes:
    template = HERE / f"trust_{role}_prompt.md"
    return _render(template, {
        "ASSIGNMENT_ID": assignment_id,
        "PACKET_PATH": str(packet_path.resolve()),
        "OUTPUT_PATH": str(output_path.resolve()),
        "MODEL_ID": model["id"],
        "MODEL_FAMILY": model["family"],
        "PROTOCOL_PATH": str((HERE / "judge_protocol.md").resolve()),
        "LESSONS_PATH": str((HERE / "judge_lessons.md").resolve()),
    })


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _safe_run_root(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    allowed = ROOT / "scripts" / "parking-adjud" / "work"
    if _is_within(resolved, ROOT) and not _is_within(resolved, allowed):
        raise ValueError("resolver run directories inside the repository must be under ignored parking-adjud/work")
    return resolved


def _current_trust_report(tmp: Path, slug: str) -> tuple[list[dict], dict]:
    items, corpus, sidecar = replay_trust.load_work_area(tmp, slug)
    ledger = _load_json(DATA / "calibration.json")
    report = trust_engine.build_report(items, ledger, {}, sidecar, corpus, include_items=True)
    return report["items"], report


def prepare(slug: str, tmp: Path, models: dict[str, dict], run_root: Path | None = None) -> Path:
    packet_path, packets = _packet_map(tmp, slug)
    chunks = _source_chunks(tmp, slug, packets)
    routes, replay = _current_trust_report(tmp, slug)
    route_by_fid = {int(item["source_key"]): item for item in routes}
    source = {
        "packets_path": packet_path.name,
        "packets_file_sha256": _sha(packet_path.read_bytes()),
        "replay_sha256": tr.sha256_json(replay),
        "drafts": [{
            "chunk": chunk["chunk"],
            "path": chunk["draft"].name,
            "file_sha256": _sha(chunk["draft"].read_bytes()),
            "checkpoint_path": chunk["checkpoint"].name,
            "checkpoint_sha256": _sha(chunk["checkpoint"].read_bytes()),
            "checkpoint_value": copy.deepcopy(chunk["manifest"]),
            "decision_input_sha256": chunk["decision_input_sha256"],
            "judge_row_sha256": chunk["state"]["row_sha256"],
            "resolution_row_sha256": chunk["state"]["resolution_sha256"],
        } for chunk in chunks],
    }
    run_id = tr.sha256_json({
        "version": PREPARE_VERSION,
        "policy_id": tr.POLICY_ID,
        "area": slug,
        "tmp": str(tmp.resolve()),
        "source": source,
        "models": models,
        "routes": {str(fid): route_by_fid[fid]["route"] for fid in sorted(route_by_fid)},
    })
    base = _safe_run_root(run_root or (tmp / "trust-resolver" / slug))
    run_dir = base / run_id

    chunk_by_fid = {}
    for chunk in chunks:
        for row_index, row in enumerate(chunk["rows"]):
            chunk_by_fid[row["fid"]] = (chunk, row_index, row)
    items = []
    generated: list[tuple[Path, bytes]] = []
    template_hashes = {
        role: _sha((HERE / f"trust_{role}_prompt.md").read_bytes())
        for role in ("challenger", "arbiter")
    }
    primary_template_sha = _sha((HERE / "judge_agent_prompt.md").read_bytes())
    for fid in sorted(route_by_fid):
        chunk, row_index, row = chunk_by_fid[fid]
        packet = packets[fid]
        packet_sha = tr.packet_sha256(packet)
        _packet_payload, tile_hashes = tr.packet_components(packet)
        packet_bytes = _json_bytes(packet)
        input_evidence_hashes = sorted(set(
            list(tile_hashes.values()) + [_sha(packet_bytes), packet_sha]
        ))
        packet_out = run_dir / "packets" / f"fid-{fid:04d}.json"
        generated.append((packet_out, packet_bytes))
        primary_assignment = {
            "assignment_id": tr.sha256_json({"run_id": run_id, "fid": fid, "role": "primary"}),
            "role": "primary",
            "model": models["primary"],
            "packet_sha256": packet_sha,
            "prompt_sha256": primary_template_sha,
            "input_evidence_sha256": input_evidence_hashes,
        }
        primary_row, primary_errors = original_judge_projection(row)
        if primary_row is None or primary_errors:
            raise ValueError(f"fid {fid}: cannot recover immutable primary: {primary_errors}")
        primary = tr.make_envelope("primary", primary_assignment, primary_row)
        assignments = {}
        for role in ("challenger", "arbiter"):
            assignment_id = _assignment_id(
                run_id, fid, role, models[role], packet_sha, template_hashes[role]
            )
            output = run_dir / "inbox" / f"fid-{fid:04d}.{role}.json"
            prompt_path = run_dir / "prompts" / f"fid-{fid:04d}.{role}.txt"
            prompt = _prompt(role, run_dir, assignment_id, packet_out, output, models[role])
            assignment = {
                "assignment_id": assignment_id,
                "role": role,
                "model": models[role],
                "packet_sha256": packet_sha,
                "prompt_sha256": _sha(prompt),
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
        "models": models,
        "source": source,
        "items": items,
    }
    generated.append((run_dir / "prepare.json", _json_bytes(document)))
    for path, data in generated:
        _write_idempotent(path, data)
    return run_dir


def _run_identity(document: dict) -> str:
    return tr.sha256_json({
        "version": document["version"],
        "policy_id": document["policy_id"],
        "area": document["area"],
        "tmp": document["tmp"],
        "source": document["source"],
        "models": document["models"],
        "routes": {str(item["fid"]): item["route"] for item in document["items"]},
    })


def _load_prepare(run_dir: Path) -> dict:
    document = _load_json(run_dir / "prepare.json")
    if document.get("version") != PREPARE_VERSION or document.get("kind") != "parking-trust-prepare":
        raise ValueError("prepare.json version/kind mismatch")
    if document.get("policy_id") != tr.POLICY_ID or document.get("run_id") != run_dir.name:
        raise ValueError("prepare.json policy/run identity mismatch")
    if _run_identity(document) != document["run_id"]:
        raise ValueError("prepare.json content does not match its run_id")
    return document


def _verify_external(raw: object) -> list[dict]:
    if not isinstance(raw, list):
        raise ValueError("external_evidence must be a list")
    result = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict) or set(item) != {"id", "path", "sha256"}:
            raise ValueError(f"external_evidence[{index}] must contain id,path,sha256")
        path = Path(item["path"])
        if not path.is_file() or _sha(path.read_bytes()) != item["sha256"]:
            raise ValueError(f"external_evidence[{index}] bytes do not match sha256")
        result.append({"id": item["id"], "sha256": item["sha256"]})
    return result


def _load_agent_envelope(run_dir: Path, item: dict, role: str,
                         packet: dict) -> tuple[dict | None, list[str]]:
    assignment = item["assignments"][role]
    expected_output = f"inbox/fid-{item['fid']:04d}.{role}.json"
    if assignment.get("output_path") != expected_output:
        return None, [f"{role} output path is noncanonical"]
    path = run_dir / expected_output
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
        external = _verify_external(raw.get("external_evidence"))
        envelope = tr.make_envelope(role, assignment, raw["decision"], external)
    except (KeyError, TypeError, ValueError) as error:
        return None, [f"{role} output invalid: {error}"]

    def validate_plain(decision: dict) -> list[str]:
        return validate_verdict_row(decision, packet, allow_override=False)[0]

    errors = tr.validate_envelope(
        envelope, role, packet, validate_plain,
        expected_assignment=assignment,
        expected_packet_sha256=item["packet_sha256"],
    )
    return envelope, errors


def _source_draft_state(prepare_doc: dict, source: dict, run_dir: Path) -> tuple[str, list[str]]:
    draft = Path(prepare_doc["tmp"]) / source["path"]
    before = source["file_sha256"]
    journal = run_dir / "transactions" / f"chunk-{source['chunk']:02d}.json"
    if journal.exists():
        return "recovery", []
    receipt = run_dir / "receipts" / f"chunk-{source['chunk']:02d}.json"
    current = _sha(draft.read_bytes()) if draft.exists() else None
    if receipt.exists():
        return "receipt", []
    if current != before:
        return "changed", [f"{draft.name} changed after prepare"]
    checkpoint = Path(prepare_doc["tmp"]) / source["checkpoint_path"]
    if not checkpoint.exists() or _sha(checkpoint.read_bytes()) != source["checkpoint_sha256"]:
        return "changed", [f"{checkpoint.name} changed after prepare"]
    return "source", []


def _verify_prepared_item(run_dir: Path, prepare_doc: dict, item: dict,
                          packet: dict) -> list[str]:
    errors = []
    expected_packet_rel = f"packets/fid-{item['fid']:04d}.json"
    if item.get("packet_path") != expected_packet_rel:
        errors.append(f"fid {item['fid']}: packet path is noncanonical")
    packet_path = run_dir / expected_packet_rel
    if not _is_within(packet_path, run_dir):
        return errors + [f"fid {item['fid']}: packet path escapes run directory"]
    try:
        frozen_packet = _load_json(packet_path)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return [f"fid {item.get('fid')}: frozen packet invalid: {error}"]
    if frozen_packet != packet:
        errors.append(f"fid {item['fid']}: frozen packet differs from current packet")
    try:
        packet_hash = tr.packet_sha256(packet)
        _payload, tile_hashes = tr.packet_components(packet)
        input_evidence_hashes = sorted(set(
            list(tile_hashes.values()) + [_sha(packet_path.read_bytes()), packet_hash]
        ))
    except ValueError as error:
        errors.append(str(error))
        packet_hash = None
        input_evidence_hashes = []
    if item.get("packet_sha256") != packet_hash:
        errors.append(f"fid {item['fid']}: packet hash mismatch")
    primary = item.get("primary") or {}
    if primary.get("model") != prepare_doc["models"]["primary"]:
        errors.append(f"fid {item['fid']}: primary model differs from prepare config")
    if primary.get("packet_sha256") != item.get("packet_sha256"):
        errors.append(f"fid {item['fid']}: primary packet hash mismatch")
    if primary.get("input_evidence_sha256") != input_evidence_hashes:
        errors.append(f"fid {item['fid']}: primary input evidence hashes mismatch")
    expected_primary_prompt = _sha((HERE / "judge_agent_prompt.md").read_bytes())
    if primary.get("prompt_sha256") != expected_primary_prompt:
        errors.append(f"fid {item['fid']}: primary prompt template hash mismatch")
    expected_primary_id = tr.sha256_json({
        "run_id": prepare_doc["run_id"], "fid": item["fid"], "role": "primary"
    })
    if primary.get("assignment_id") != expected_primary_id:
        errors.append(f"fid {item['fid']}: primary assignment id mismatch")
    for role in ("challenger", "arbiter"):
        assignment = (item.get("assignments") or {}).get(role)
        if not isinstance(assignment, dict):
            errors.append(f"fid {item['fid']}: missing {role} assignment")
            continue
        template_sha = _sha((HERE / f"trust_{role}_prompt.md").read_bytes())
        expected_id = _assignment_id(
            prepare_doc["run_id"], item["fid"], role,
            prepare_doc["models"][role], item["packet_sha256"], template_sha,
        )
        if assignment.get("assignment_id") != expected_id:
            errors.append(f"fid {item['fid']}: {role} assignment id mismatch")
        if assignment.get("model") != prepare_doc["models"][role]:
            errors.append(f"fid {item['fid']}: {role} model differs from prepare config")
        if assignment.get("input_evidence_sha256") != input_evidence_hashes:
            errors.append(f"fid {item['fid']}: {role} input evidence hashes mismatch")
        expected_prompt_rel = f"prompts/fid-{item['fid']:04d}.{role}.txt"
        expected_output_rel = f"inbox/fid-{item['fid']:04d}.{role}.json"
        if assignment.get("prompt_path") != expected_prompt_rel:
            errors.append(f"fid {item['fid']}: {role} prompt path is noncanonical")
        if assignment.get("output_path") != expected_output_rel:
            errors.append(f"fid {item['fid']}: {role} output path is noncanonical")
        prompt_path = run_dir / expected_prompt_rel
        output_path = run_dir / expected_output_rel
        if not _is_within(prompt_path, run_dir) or not _is_within(output_path, run_dir):
            errors.append(f"fid {item['fid']}: {role} assignment escapes run directory")
            continue
        try:
            expected_prompt = _prompt(
                role, run_dir, expected_id, packet_path, output_path,
                prepare_doc["models"][role],
            )
            actual_prompt = prompt_path.read_bytes()
            if actual_prompt != expected_prompt or _sha(actual_prompt) != assignment.get("prompt_sha256"):
                errors.append(f"fid {item['fid']}: {role} prompt bytes/hash mismatch")
        except (OSError, ValueError) as error:
            errors.append(f"fid {item['fid']}: {role} prompt invalid: {error}")
    return errors


def evaluate(run_dir: Path) -> tuple[dict, dict]:
    prepare_doc = _load_prepare(run_dir)
    tmp = Path(prepare_doc["tmp"])
    packet_path, packets = _packet_map(tmp, prepare_doc["area"])
    errors = []
    if _sha(packet_path.read_bytes()) != prepare_doc["source"]["packets_file_sha256"]:
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
        packet = packets[fid]
        primary = item["primary"]

        def validate_plain(decision: dict) -> list[str]:
            return validate_verdict_row(decision, packet, allow_override=False)[0]

        item_errors = _verify_prepared_item(run_dir, prepare_doc, item, packet)
        item_errors.extend(tr.validate_envelope(
            primary, "primary", packet, validate_plain,
            expected_packet_sha256=item["packet_sha256"],
        ))
        challenger, challenger_errors = _load_agent_envelope(run_dir, item, "challenger", packet)
        arbiter, arbiter_errors = _load_agent_envelope(run_dir, item, "arbiter", packet)
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
            if _sha(paths["target"].read_bytes()) == source["file_sha256"]:
                receipt_before = paths["target"].read_bytes()
            elif (paths["backup"].exists()
                  and _sha(paths["backup"].read_bytes()) == source["file_sha256"]):
                receipt_before = paths["backup"].read_bytes()
            else:
                raise ValueError("receipt has no exact prepared source or backup")
            plan = _build_plan(
                run_dir, prepare_doc, source, internal, receipt_before
            )
            if plan.get("state") == "PRESERVED":
                _validate_exact_artifact(paths["receipt"], plan["receipt"], "preservation receipt")
                if (_sha(paths["target"].read_bytes()) != source["file_sha256"]
                        or _sha(paths["checkpoint"].read_bytes()) != source["checkpoint_sha256"]):
                    raise ValueError("preservation receipt source hashes changed")
                chunk_states[source["chunk"]] = "preserved"
            elif plan.get("state") == "READY":
                _validate_exact_artifact(paths["receipt"], plan["receipt"], "apply receipt")
                if (paths["target"].read_bytes() != plan["after_bytes"]
                        or _sha(paths["checkpoint"].read_bytes())
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
        "counts": counts,
        "chunks": [{"chunk": key, "source_state": value}
                   for key, value in sorted(chunk_states.items())],
        "items": sorted(public_items, key=lambda value: value["fid"]),
        "errors": errors,
    }
    return status, internal


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


def _transaction_id(prepare_doc: dict, source: dict, values: list[dict]) -> str:
    selected = []
    for value in values:
        role = value["resolution"].get("selected_role")
        envelope = value.get(role) if role in tr.ROLES else None
        selected.append({
            "fid": value["item"]["fid"],
            "role": role,
            "decision_sha256": envelope.get("decision_sha256") if envelope else None,
            "state": value["resolution"]["state"],
        })
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
    target = Path(prepare_doc["tmp"]) / source["path"]
    checkpoint = Path(prepare_doc["tmp"]) / source["checkpoint_path"]
    chunk = source["chunk"]
    return {
        "target": target,
        "checkpoint": checkpoint,
        "backup": run_dir / "backups" / f"{target.name}.{source['file_sha256'][:12]}.json",
        "stage": run_dir / "staged" / f"{target.name}.{transaction_id}.json",
        "journal": run_dir / "transactions" / f"chunk-{chunk:02d}.json",
        "receipt": run_dir / "receipts" / f"chunk-{chunk:02d}.json",
        "archive": run_dir / "Archive" / "transactions" / f"chunk-{chunk:02d}.{transaction_id}.json",
    }


def _build_plan(run_dir: Path, prepare_doc: dict, source: dict, internal: dict,
                before_bytes: bytes) -> dict:
    if _sha(before_bytes) != source["file_sha256"]:
        raise ValueError("resolver backup/source does not match prepared draft")
    values = _chunk_values(internal, source["chunk"])
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
            "chunk": source["chunk"],
            "draft_sha256": source["file_sha256"],
            "checkpoint_sha256": source["checkpoint_sha256"],
            "routes_sha256": tr.sha256_json({
                str(value["item"]["fid"]): value["item"]["route"] for value in values
            }),
        }
        return {
            "state": "PRESERVED", "chunk": source["chunk"], "applied_fids": [],
            "transaction_id": transaction_id, "receipt": receipt, "paths": paths,
        }
    after_bytes = (json.dumps(rows, indent=1, ensure_ascii=False) + "\n").encode("utf-8")
    packet_path, packets = _packet_map(Path(prepare_doc["tmp"]), prepare_doc["area"])
    chunk_packets = [packets[row["fid"]] for row in rows]
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
        "chunk": source["chunk"],
        "before_sha256": source["file_sha256"],
        "after_sha256": _sha(after_bytes),
        "checkpoint_after_sha256": _sha(_json_bytes(checkpoint_after)),
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
    if value != expected:
        raise ValueError(f"{kind} does not match the prepared canonical transaction")
    return value


def _finish_transaction(plan: dict) -> None:
    paths = plan["paths"]
    _validate_exact_artifact(paths["journal"], plan["journal"], "transaction journal")
    target = paths["target"]
    current = _sha(target.read_bytes())
    if current == plan["before_sha256"]:
        if not paths["stage"].exists() or paths["stage"].read_bytes() != plan["after_bytes"]:
            raise ValueError("staged resolver bytes are missing or changed")
        if _sha(paths["checkpoint"].read_bytes()) != plan["journal"]["checkpoint_before_sha256"]:
            raise ValueError("checkpoint changed before resolver replacement")
        os.replace(paths["stage"], target)
        _fsync_dir(target.parent)
    elif current != plan["after_sha256"]:
        raise ValueError("canonical draft matches neither resolver transaction hash")
    if target.read_bytes() != plan["after_bytes"]:
        raise ValueError("canonical draft content differs from the recomputed plan")
    checkpoint_hash = _sha(paths["checkpoint"].read_bytes())
    if checkpoint_hash == plan["journal"]["checkpoint_before_sha256"]:
        _atomic_json(paths["checkpoint"], plan["checkpoint_after"])
    elif checkpoint_hash != plan["receipt"]["checkpoint_after_sha256"]:
        raise ValueError("checkpoint matches neither resolver transaction hash")
    _atomic_json(paths["receipt"], plan["receipt"])
    paths["archive"].parent.mkdir(parents=True, exist_ok=True)
    os.replace(paths["journal"], paths["archive"])
    _fsync_dir(paths["archive"].parent)


def _verify_current_routes(prepare_doc: dict) -> None:
    current_routes, current_replay = _current_trust_report(
        Path(prepare_doc["tmp"]), prepare_doc["area"]
    )
    if tr.sha256_json(current_replay) != prepare_doc["source"]["replay_sha256"]:
        raise ValueError("trust replay/authority routes changed after prepare")
    expected = {item["fid"]: item["route"] for item in prepare_doc["items"]}
    actual = {int(item["source_key"]): item["route"] for item in current_routes}
    if actual != expected:
        raise ValueError("per-fid trust routes changed after prepare")


def _apply_chunk_under_lock(run_dir: Path, chunk_index: int, apply: bool = False,
                            crash_after_replace: bool = False) -> dict:
    prepare_doc = _load_prepare(run_dir)
    source = next((row for row in prepare_doc["source"]["drafts"]
                   if row["chunk"] == chunk_index), None)
    if source is None:
        raise ValueError(f"unknown chunk {chunk_index:02d}")

    live_journal_path = run_dir / "transactions" / f"chunk-{chunk_index:02d}.json"
    status, internal = evaluate(run_dir)
    values = _chunk_values(internal, chunk_index)
    transaction_id = _transaction_id(prepare_doc, source, values)
    paths = _artifact_paths(run_dir, prepare_doc, source, transaction_id)
    current_hash = _sha(paths["target"].read_bytes())
    if current_hash == source["file_sha256"]:
        before_bytes = paths["target"].read_bytes()
    elif paths["backup"].exists() and _sha(paths["backup"].read_bytes()) == source["file_sha256"]:
        before_bytes = paths["backup"].read_bytes()
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
            if (_sha(plan["paths"]["target"].read_bytes()) != source["file_sha256"]
                    or _sha(plan["paths"]["checkpoint"].read_bytes()) != source["checkpoint_sha256"]):
                raise ValueError("preservation receipt source hashes changed")
            return {"state": "PRESERVED", "receipt": receipt}
        if not apply:
            return {key: value for key, value in plan.items()
                    if key not in ("receipt", "paths")}
        _verify_current_routes(prepare_doc)
        if (_sha(plan["paths"]["target"].read_bytes()) != source["file_sha256"]
                or _sha(plan["paths"]["checkpoint"].read_bytes()) != source["checkpoint_sha256"]):
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
        if _sha(paths["target"].read_bytes()) == source["file_sha256"]:
            _verify_current_routes(prepare_doc)
        _finish_transaction(plan)
        return {"state": "APPLIED", "receipt": _load_json(paths["receipt"])}

    if paths["receipt"].exists():
        receipt = _validate_exact_artifact(paths["receipt"], plan["receipt"], "apply receipt")
        if (paths["target"].read_bytes() != plan["after_bytes"]
                or _sha(paths["checkpoint"].read_bytes()) != receipt["checkpoint_after_sha256"]):
            raise ValueError("applied receipt does not match canonical draft/checkpoint")
        return {"state": "APPLIED", "receipt": receipt}

    if status["errors"]:
        return {"state": "BLOCKED", "errors": status["errors"]}
    source_state = next(
        (row["source_state"] for row in status["chunks"] if row["chunk"] == chunk_index),
        None,
    )
    if source_state != "source":
        return {"state": "BLOCKED", "errors": [f"chunk source state is {source_state}"]}
    _verify_current_routes(prepare_doc)
    public_plan = {key: value for key, value in plan.items()
                   if key not in ("before_bytes", "after_bytes", "checkpoint_after",
                                  "receipt", "journal", "paths")}
    if not apply:
        return public_plan

    _write_idempotent(paths["backup"], before_bytes)
    _write_idempotent(paths["stage"], plan["after_bytes"])
    if _sha(paths["target"].read_bytes()) != source["file_sha256"]:
        raise ValueError("canonical draft changed before resolver commit")
    if _sha(paths["checkpoint"].read_bytes()) != source["checkpoint_sha256"]:
        raise ValueError("checkpoint changed before resolver commit")
    _verify_current_routes(prepare_doc)
    _atomic_json(paths["journal"], plan["journal"])
    os.replace(paths["stage"], paths["target"])
    _fsync_dir(paths["target"].parent)
    if crash_after_replace:
        raise RuntimeError("simulated crash after canonical draft replacement")
    _finish_transaction(plan)
    return {"state": "APPLIED", "receipt": _load_json(paths["receipt"])}


def _canonical_lock_path(prepare_doc: dict, chunk_index: int) -> Path:
    del chunk_index  # authority changes are area-wide, so every chunk shares one lock
    return tr.area_lock_path(prepare_doc["tmp"], prepare_doc["area"])


def apply_chunk(run_dir: Path, chunk_index: int, apply: bool = False,
                crash_after_replace: bool = False) -> dict:
    prepare_doc = _load_prepare(run_dir)
    if not apply:
        return _apply_chunk_under_lock(
            run_dir, chunk_index, apply=False,
            crash_after_replace=crash_after_replace,
        )
    area_lock_path = _canonical_lock_path(prepare_doc, chunk_index)
    authority_lock_path = tr.resource_lock_path(
        prepare_doc["tmp"], DATA / "calibration.json"
    )
    authority_lock_path.parent.mkdir(parents=True, exist_ok=True)
    area_lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(authority_lock_path, "a+b") as authority_lock:
        fcntl.flock(authority_lock.fileno(), fcntl.LOCK_EX)
        with open(area_lock_path, "a+b") as area_lock:
            fcntl.flock(area_lock.fileno(), fcntl.LOCK_EX)
            return _apply_chunk_under_lock(
                run_dir, chunk_index, apply=apply,
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
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"trust resolver failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
