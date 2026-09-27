"""End-to-end tests for provenance-preserving autonomous parking resolution."""
from __future__ import annotations

import copy
import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
TOOLS = HERE / "parking-adjud" / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import judge_packets
from judge_validation import machine_decision_projection, original_judge_projection, validate_verdict_row
import calibration
import merge_drafts
import resolve_trust
import trust_engine
import trust_resolution as tr


def _decision(fid=1, verdict="KEEP", confidence="strong", area="test-co"):
    calls = {"exists": "yes", "public": "yes", "serves": "yes"}
    hint = None
    if verdict == "DROP":
        calls["serves"] = "no"
    elif verdict == "REVIEW":
        calls["serves"] = "unclear"
        hint = "fetch a distinct imagery date"
    return {
        "fid": fid, "area": area, "osm": [f"way/{fid}"], "verdict": verdict,
        "prior": "surveyed", "confidence": confidence,
        "exists": {"call": calls["exists"], "evidence": "Z2: marked paved lot with cars"},
        "public": {"call": calls["public"], "evidence": "No gate or access restriction"},
        "serves": {"call": calls["serves"], "evidence": (
            "Trail edge 80 m; walk 100 m" if verdict != "DROP"
            else "Office campus explains it; trail walk 2200 m"
        )},
        "frames_used": ["z1", "z2", "z3"], "tags_cited": {"surface": "asphalt"},
        "resolve_hint": hint,
    }


def _packet(tmp_path, fid=1, area="test-co"):
    tiles = {}
    for zoom in ("z1", "z2", "z3"):
        path = tmp_path / f"{fid:04d}_{zoom}.png"
        path.write_bytes(f"tile-{fid}-{zoom}".encode())
        tiles[zoom] = str(path)
    return {
        "fid": fid, "area": area, "osm": [f"way/{fid}"], "prior": "surveyed",
        "tags_union": {}, "members": [], "mixed_access": False,
        "footprint": "polygon", "area_m2": 100,
        "serves": {"trail": "Test Trail", "edge_m": 80, "fallback": False,
                   "n_trails_in_range": 1},
        "walk": {"walk_m": 100, "conn": "path", "trail": "Test Trail"},
        "trailhead_nodes_120m": [], "footways_60m": 1, "building_overlap": False,
        "context": {"category": "NEUTRAL", "evidence": "", "facility": None,
                    "facility_edge_m": None},
        "tiles": tiles,
    }


def _workspace(tmp_path, rows=None, area="test-co"):
    work = tmp_path / "work"
    work.mkdir(parents=True)
    rows = rows or [_decision(area=area)]
    packets = {row["fid"]: _packet(work, row["fid"], area) for row in rows}
    packet_path = work / f"{area}_packets.json"
    packet_path.write_text(json.dumps({str(fid): packet for fid, packet in packets.items()}, indent=1))
    facilities = [{
        "fid": row["fid"], "lat": 40.0 + row["fid"] / 1000, "lon": -105.0,
        "osm": row["osm"], "prior": row["prior"], "rings": [],
        "tags_union": {"name": f"Lot {row['fid']}"},
    } for row in rows]
    (work / f"{area}_dossier.json").write_text(json.dumps({"slug": area, "facilities": facilities}))
    (work / f"{area}_pub.txt").write_text(",".join(str(row["fid"]) for row in rows))
    draft = work / f"{area}_verdict_draft_00.json"
    draft.write_text(json.dumps(rows, indent=1) + "\n")
    chunk = [packets[row["fid"]] for row in rows]
    state = judge_packets.inspect_draft(chunk, draft)
    decision_input, missing = judge_packets.decision_fingerprint(chunk)
    assert state["status"] == "complete" and not missing
    manifest = judge_packets.manifest_value(area, 0, chunk, decision_input, state)
    checkpoint = work / f"{area}_checkpoint_00.json"
    checkpoint.write_text(json.dumps(manifest, indent=1) + "\n")
    return work, draft, checkpoint, rows, packets


def _models(same_family=False):
    return {
        "primary": tr.model_config("primary-model", "shared-family" if same_family else "family-a"),
        "challenger": tr.model_config("challenger-model", "shared-family" if same_family else "family-b"),
        "arbiter": tr.model_config("arbiter-model", "shared-family" if same_family else "family-c"),
    }


def _prepared(tmp_path, rows=None, same_family=False):
    work, draft, checkpoint, rows, packets = _workspace(tmp_path, rows)
    before = {draft: draft.read_bytes(), checkpoint: checkpoint.read_bytes()}
    run = resolve_trust.prepare("test-co", work, _models(same_family), tmp_path / "runs")
    return run, work, draft, checkpoint, rows, packets, before


def _write_output(run, fid, role, decision, external=()):
    prepare = json.loads((run / "prepare.json").read_text())
    item = next(item for item in prepare["items"] if item["fid"] == fid)
    assignment = item["assignments"][role]
    path = run / assignment["output_path"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "assignment_id": assignment["assignment_id"],
        "decision": decision,
        "external_evidence": list(external),
    }, indent=1) + "\n")
    return path


def test_prepare_is_idempotent_blind_and_never_touches_canonical_sources(tmp_path):
    run, work, draft, checkpoint, rows, packets, before = _prepared(tmp_path)
    assert resolve_trust.prepare("test-co", work, _models(), tmp_path / "runs") == run
    assert draft.read_bytes() == before[draft] and checkpoint.read_bytes() == before[checkpoint]
    prepare = json.loads((run / "prepare.json").read_text())
    assert prepare["run_id"] == run.name and prepare["items"][0]["route"] == trust_engine.ROUTE_CHALLENGE
    prompt = (run / prepare["items"][0]["assignments"]["challenger"]["prompt_path"]).read_text()
    assert rows[0]["exists"]["evidence"] not in prompt
    assert str(draft) not in prompt and "Do not read any verdict draft" in prompt
    status, _ = resolve_trust.evaluate(run)
    assert status["state"] == "PENDING" and status["counts"]["pending_challenger"] == 1
    assert draft.read_bytes() == before[draft] and checkpoint.read_bytes() == before[checkpoint]


def test_distinct_family_agreement_applies_full_resolution_and_preserves_primary_hash(tmp_path):
    run, work, draft, checkpoint, rows, packets, before = _prepared(tmp_path)
    _write_output(run, 1, "challenger", copy.deepcopy(rows[0]))
    status, _ = resolve_trust.evaluate(run)
    assert status["state"] == "READY"
    assert status["items"][0]["result"] == "DISTINCT_FAMILY_AGREEMENT"

    plan = resolve_trust.apply_chunk(run, 0, apply=False)
    assert plan["state"] == "READY" and draft.read_bytes() == before[draft]
    old_manifest = json.loads(checkpoint.read_text())
    result = resolve_trust.apply_chunk(run, 0, apply=True)
    assert result["state"] == "APPLIED"
    resolved = json.loads(draft.read_text())[0]
    assert resolved["trust_resolution"]["selected_role"] == "challenger"
    assert resolved["verdict"] == "KEEP" and resolved["judge_provenance"]["model_id"] == "challenger-model"
    primary, errors = original_judge_projection(resolved)
    assert not errors and tr.decision_content(primary) == tr.decision_content(rows[0])
    machine, errors = machine_decision_projection(resolved)
    assert not errors and machine["verdict"] == "KEEP"
    assert validate_verdict_row(resolved, packets[1])[0] == []
    new_manifest = json.loads(checkpoint.read_text())
    assert new_manifest["judge_row_sha256"] == old_manifest["judge_row_sha256"]
    assert new_manifest["resolution_row_sha256"] != old_manifest["resolution_row_sha256"]
    report = trust_engine.build_report([
        {"key": "way/1", "source_key": "1", "store": "work:test-co", "area": "test-co",
         "row": resolved, "published": {"verdict": "KEEP", "src": "judge-fanout", "area": "test-co"}}
    ], {"areas": []}, {}, {"lots": {}}, {
        "corpus_sha256": "x", "sidecar_sha256": None, "source_rows": 1,
        "unique_clusters": 1, "folded_duplicates": 0,
    }, include_items=True)
    assert report["items"][0]["route"] == trust_engine.ROUTE_RESOLVED
    assert resolve_trust.apply_chunk(run, 0, apply=True)["state"] == "APPLIED"


def test_disagreement_requires_arbiter_and_distinct_arbiter_agreement_resolves(tmp_path):
    primary = _decision()
    challenger = _decision(verdict="DROP")
    arbiter = _decision(verdict="DROP", confidence="certain")
    run, *_ = _prepared(tmp_path, [primary])
    _write_output(run, 1, "challenger", challenger)
    status, _ = resolve_trust.evaluate(run)
    assert status["state"] == "PENDING" and status["counts"]["pending_arbiter"] == 1
    _write_output(run, 1, "arbiter", arbiter)
    status, _ = resolve_trust.evaluate(run)
    assert status["state"] == "READY"
    assert status["items"][0]["result"] == "ARBITRATED_AGREEMENT"
    assert status["items"][0]["selected_role"] == "arbiter"


def test_same_family_needs_new_content_hashed_evidence_or_becomes_human_exception(tmp_path):
    row = _decision()
    run, work, draft, checkpoint, rows, packets, before = _prepared(
        tmp_path, [row], same_family=True
    )
    _write_output(run, 1, "challenger", copy.deepcopy(row))
    status, _ = resolve_trust.evaluate(run)
    assert status["state"] == "PENDING" and status["counts"]["pending_arbiter"] == 1
    _write_output(run, 1, "arbiter", copy.deepcopy(row))
    status, _ = resolve_trust.evaluate(run)
    assert status["state"] == "BLOCKED" and status["counts"]["human_exceptions"] == 1

    reused = Path(packets[1]["tiles"]["z1"])
    reused_external = [{"id": "reused-z1", "path": str(reused),
                        "sha256": hashlib.sha256(reused.read_bytes()).hexdigest()}]
    arbiter_reusing_input = copy.deepcopy(row)
    arbiter_reusing_input["exists"]["evidence"] += "; [external:reused-z1] confirms the lot"
    _write_output(run, 1, "arbiter", arbiter_reusing_input, reused_external)
    status, _ = resolve_trust.evaluate(run)
    assert status["state"] == "BLOCKED" and status["counts"]["human_exceptions"] == 1

    prepare = json.loads((run / "prepare.json").read_text())
    frozen_packet = run / prepare["items"][0]["packet_path"]
    packet_external = [{"id": "reused-packet", "path": str(frozen_packet),
                        "sha256": hashlib.sha256(frozen_packet.read_bytes()).hexdigest()}]
    arbiter_reusing_packet = copy.deepcopy(row)
    arbiter_reusing_packet["exists"]["evidence"] += "; [external:reused-packet] confirms it"
    _write_output(run, 1, "arbiter", arbiter_reusing_packet, packet_external)
    status, _ = resolve_trust.evaluate(run)
    assert status["state"] == "BLOCKED" and status["counts"]["human_exceptions"] == 1

    evidence = tmp_path / "fresh-naip.png"
    evidence.write_bytes(b"fresh independent evidence bytes")
    external = [{"id": "naip-new-date", "path": str(evidence),
                 "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()}]
    _write_output(run, 1, "arbiter", copy.deepcopy(row), external)
    status, _ = resolve_trust.evaluate(run)
    assert status["state"] == "BLOCKED" and status["counts"]["human_exceptions"] == 1
    arbiter_with_evidence = copy.deepcopy(row)
    arbiter_with_evidence["exists"]["evidence"] += "; [external:naip-new-date] confirms the same paved lot"
    _write_output(run, 1, "arbiter", arbiter_with_evidence, external)
    status, _ = resolve_trust.evaluate(run)
    assert status["state"] == "READY"
    assert status["items"][0]["result"] == "CORRELATED_EVIDENCE_ARBITRATION"


def test_review_arbiter_stays_a_human_exception_and_apply_is_blocked(tmp_path):
    row = _decision(confidence="leaning")
    run, *_ = _prepared(tmp_path, [row])
    _write_output(run, 1, "challenger", copy.deepcopy(row))
    _write_output(run, 1, "arbiter", _decision(verdict="REVIEW", confidence="leaning"))
    status, _ = resolve_trust.evaluate(run)
    assert status["state"] == "BLOCKED" and status["counts"]["human_exceptions"] == 1
    assert resolve_trust.apply_chunk(run, 0, apply=False)["state"] == "BLOCKED"


def test_invalid_assignment_and_stale_source_fail_before_any_draft_write(tmp_path):
    run, work, draft, checkpoint, rows, packets, before = _prepared(tmp_path)
    output = _write_output(run, 1, "challenger", copy.deepcopy(rows[0]))
    value = json.loads(output.read_text())
    value["assignment_id"] = "0" * 64
    output.write_text(json.dumps(value))
    status, _ = resolve_trust.evaluate(run)
    assert status["state"] == "BLOCKED" and status["counts"]["invalid"] == 1
    assert draft.read_bytes() == before[draft]

    stale_root = tmp_path / "stale"
    stale_run, _, stale_draft, _, stale_rows, _, stale_before = _prepared(stale_root)
    stale_draft.write_text(json.dumps([dict(stale_rows[0], confidence="certain")]))
    status, _ = resolve_trust.evaluate(stale_run)
    assert status["state"] == "RECOVERY_REQUIRED"
    with pytest.raises(ValueError, match="cannot recover the exact prepared"):
        resolve_trust.apply_chunk(stale_run, 0, apply=False)
    assert stale_before[stale_draft] != stale_draft.read_bytes()


def test_apply_recovers_after_draft_replace_before_manifest_and_receipt(tmp_path):
    run, work, draft, checkpoint, rows, packets, before = _prepared(tmp_path)
    _write_output(run, 1, "challenger", copy.deepcopy(rows[0]))
    old_manifest = checkpoint.read_bytes()
    with pytest.raises(RuntimeError, match="simulated crash"):
        resolve_trust.apply_chunk(run, 0, apply=True, crash_after_replace=True)
    assert draft.read_bytes() != before[draft]
    assert checkpoint.read_bytes() == old_manifest
    status, _ = resolve_trust.evaluate(run)
    assert status["state"] == "RECOVERY_REQUIRED"
    recovery = resolve_trust.apply_chunk(run, 0, apply=False)
    assert recovery["state"] == "RECOVERY_REQUIRED"
    result = resolve_trust.apply_chunk(run, 0, apply=True)
    assert result["state"] == "APPLIED"
    assert json.loads(checkpoint.read_text())["resolution_row_sha256"]
    assert (run / "receipts" / "chunk-00.json").exists()
    assert not (run / "transactions" / "chunk-00.json").exists()
    assert list((run / "Archive" / "transactions").glob("chunk-00.*.json"))


def test_human_override_remains_outermost_and_calibration_keeps_primary_baseline(tmp_path):
    run, work, draft, checkpoint, rows, packets, before = _prepared(tmp_path)
    _write_output(run, 1, "challenger", copy.deepcopy(rows[0]))
    assert resolve_trust.apply_chunk(run, 0, apply=True)["state"] == "APPLIED"
    resolved = json.loads(draft.read_text())[0]
    trust_wrapper = copy.deepcopy(resolved["trust_resolution"])

    merge_drafts.set_verdict(resolved, "DROP", "user exception", "2026-09-26")
    assert resolved["override"]["from"] == "KEEP"
    assert resolved["trust_resolution"] == trust_wrapper
    primary, errors = original_judge_projection(resolved)
    machine, machine_errors = machine_decision_projection(resolved)
    assert not errors and not machine_errors
    assert primary["verdict"] == "KEEP" and machine["verdict"] == "KEEP"
    assert resolved["verdict"] == "DROP"
    assert validate_verdict_row(resolved, packets[1])[0] == []

    record = calibration.record_for(
        "test-co", [resolved], {"KEEP": "none"}, [], None,
        "2026-09-26", "agent-fanout",
    )
    assert record["judged"] == {"KEEP": {"strong": 1}}
    assert record["flips"][0]["from"] == "KEEP" and record["flips"][0]["to"] == "DROP"
    assert record["human_exceptions"] == [{"fid": 1, "from": "KEEP", "to": "DROP", "by": "human"}]

    merge_drafts.set_verdict(resolved, "KEEP", None, "2026-09-26")
    assert "override" not in resolved and resolved["trust_resolution"] == trust_wrapper


def test_persisted_resolution_rejects_nested_tampering_and_continuation_reuse(tmp_path):
    run, work, draft, checkpoint, rows, packets, before = _prepared(tmp_path)
    _write_output(run, 1, "challenger", copy.deepcopy(rows[0]))
    assert resolve_trust.apply_chunk(run, 0, apply=True)["state"] == "APPLIED"
    resolved = json.loads(draft.read_text())[0]

    tampered = copy.deepcopy(resolved)
    tampered["trust_resolution"]["challenger"]["decision"]["exists"]["evidence"] = "tampered"
    errors, _ = validate_verdict_row(tampered, packets[1])
    assert any("challenger decision hash mismatch" in error for error in errors)

    tampered = copy.deepcopy(resolved)
    tampered["serves"]["evidence"] = "top-level drift"
    errors, _ = validate_verdict_row(tampered, packets[1])
    assert any("top-level machine decision differs" in error for error in errors)

    tampered = copy.deepcopy(resolved)
    tampered["unexpected_selected_extension"] = "drift"
    errors, _ = validate_verdict_row(tampered, packets[1])
    assert any("top-level machine decision differs" in error for error in errors)

    tampered = copy.deepcopy(resolved)
    for role in tr.ROLES:
        envelope = tampered["trust_resolution"].get(role)
        if envelope is None:
            continue
        envelope["packet_sha256"] = "f" * 64
        envelope_without_hash = dict(envelope)
        envelope_without_hash.pop("envelope_sha256")
        envelope["envelope_sha256"] = tr.sha256_json(envelope_without_hash)
    tampered["judge_provenance"]["packet_sha256"] = "f" * 64
    wrapper = tampered["trust_resolution"]
    wrapper_without_hash = dict(wrapper)
    wrapper_without_hash.pop("resolution_sha256")
    wrapper["resolution_sha256"] = tr.sha256_json(wrapper_without_hash)
    errors, _ = validate_verdict_row(tampered, packets[1])
    assert any("packet hash mismatch" in error for error in errors)

    errors, _ = validate_verdict_row(resolved, packets[1], allow_override=False)
    assert "continuation rows may not contain machine resolutions" in errors


def test_resolved_provenance_survives_the_existing_store_merge(tmp_path, monkeypatch):
    run, work, draft, checkpoint, rows, packets, before = _prepared(tmp_path)
    _write_output(run, 1, "challenger", copy.deepcopy(rows[0]))
    assert resolve_trust.apply_chunk(run, 0, apply=True)["state"] == "APPLIED"
    store = tmp_path / "store.json"
    monkeypatch.setattr(merge_drafts, "PADJ_TMP", str(work))
    assert merge_drafts.main([str(store), "test-co", "--judged", "2026-09-26", "--write"]) == 0
    stored = json.loads(store.read_text())["way/1"]
    assert stored["trust_resolution"]["result"] == "DISTINCT_FAMILY_AGREEMENT"
    assert stored["judge_provenance"]["model_id"] == "challenger-model"
    assert "override" not in stored
    primary, errors = original_judge_projection(stored)
    assert not errors and primary["verdict"] == "KEEP"


def test_recovery_never_overwrites_a_concurrently_changed_checkpoint(tmp_path):
    run, work, draft, checkpoint, rows, packets, before = _prepared(tmp_path)
    _write_output(run, 1, "challenger", copy.deepcopy(rows[0]))
    with pytest.raises(RuntimeError, match="simulated crash"):
        resolve_trust.apply_chunk(run, 0, apply=True, crash_after_replace=True)
    checkpoint.write_text(json.dumps({"concurrent": "writer"}))
    with pytest.raises(ValueError, match="checkpoint matches neither"):
        resolve_trust.apply_chunk(run, 0, apply=True)
    assert json.loads(checkpoint.read_text()) == {"concurrent": "writer"}


def test_prepare_manifest_prompt_and_run_root_tampering_fail_closed(tmp_path):
    run, work, draft, checkpoint, rows, packets, before = _prepared(tmp_path)
    prepare = json.loads((run / "prepare.json").read_text())
    prompt = run / prepare["items"][0]["assignments"]["challenger"]["prompt_path"]
    prompt.write_text(prompt.read_text() + "\ntampered")
    status, _ = resolve_trust.evaluate(run)
    assert status["state"] == "BLOCKED" and status["counts"]["invalid"] == 1
    assert draft.read_bytes() == before[draft]

    prepare["items"][0]["route"] = trust_engine.ROUTE_ARBITER
    (run / "prepare.json").write_text(json.dumps(prepare))
    with pytest.raises(ValueError, match="does not match its run_id"):
        resolve_trust.evaluate(run)

    with pytest.raises(ValueError, match="must be under ignored"):
        resolve_trust.prepare(
            "test-co", work, _models(),
            resolve_trust.ROOT / "public" / "resolver-runs",
        )


def test_direct_arbiter_route_does_not_wait_for_a_challenger(tmp_path):
    row = _decision(confidence="strong")
    row["serves"]["evidence"] = "Fallback edge 900 m; trail walk 1000 m"
    run, *_ = _prepared(tmp_path, [row])
    status, _ = resolve_trust.evaluate(run)
    assert status["state"] == "PENDING"
    assert status["counts"]["pending_arbiter"] == 1
    assert status["counts"]["pending_challenger"] == 0
    _write_output(run, 1, "arbiter", copy.deepcopy(row))
    status, _ = resolve_trust.evaluate(run)
    assert status["state"] == "READY"
    assert status["items"][0]["result"] == "ARBITRATED_AGREEMENT"


def test_human_and_existing_machine_layers_are_preserved_without_fresh_envelopes(tmp_path):
    human_row = _decision()
    merge_drafts.set_verdict(human_row, "DROP", "human authority", "2026-09-26")
    run, work, draft, checkpoint, rows, packets, before = _prepared(tmp_path / "human", [human_row])
    status, _ = resolve_trust.evaluate(run)
    assert status["state"] == "READY" and status["counts"]["preserved_authority"] == 1
    result = resolve_trust.apply_chunk(run, 0, apply=True)
    assert result["state"] == "PRESERVED"
    preserved_status, _ = resolve_trust.evaluate(run)
    assert preserved_status["state"] == "APPLIED"
    assert preserved_status["chunks"] == [{"chunk": 0, "source_state": "preserved"}]
    preserved = json.loads(draft.read_text())[0]
    assert preserved["override"]["by"] == "human" and "trust_resolution" not in preserved

    machine_root = tmp_path / "machine"
    first, work, draft, checkpoint, rows, packets, before = _prepared(machine_root)
    _write_output(first, 1, "challenger", copy.deepcopy(rows[0]))
    assert resolve_trust.apply_chunk(first, 0, apply=True)["state"] == "APPLIED"
    second = resolve_trust.prepare("test-co", work, _models(), machine_root / "second-runs")
    status, _ = resolve_trust.evaluate(second)
    assert status["state"] == "READY" and status["counts"]["preserved_authority"] == 1
    assert status["items"][0]["route"] == trust_engine.ROUTE_RESOLVED


def test_apply_rechecks_current_authority_routes_under_the_canonical_lock(tmp_path, monkeypatch):
    ledger_dir = tmp_path / "ledger"
    ledger_dir.mkdir()
    ledger = ledger_dir / "calibration.json"
    ledger.write_text(json.dumps({"areas": []}))
    monkeypatch.setattr(resolve_trust, "DATA", ledger_dir)
    run, work, draft, checkpoint, rows, packets, before = _prepared(tmp_path / "workspace")
    _write_output(run, 1, "challenger", copy.deepcopy(rows[0]))
    ledger.write_text(json.dumps({"areas": [{
        "area": "test-co", "reviewed": {"KEEP": "each"},
    }]}))
    with pytest.raises(ValueError, match="routes changed after prepare"):
        resolve_trust.apply_chunk(run, 0, apply=True)
    assert draft.read_bytes() == before[draft]


def test_assignment_path_traversal_is_rejected_before_reading_outside_run(tmp_path):
    run, work, draft, checkpoint, rows, packets, before = _prepared(tmp_path)
    prepare_path = run / "prepare.json"
    prepare = json.loads(prepare_path.read_text())
    assignment = prepare["items"][0]["assignments"]["challenger"]
    prepare["items"][0]["packet_path"] = "../../outside-packet.json"
    assignment["prompt_path"] = "../../outside-prompt.txt"
    assignment["output_path"] = "../../outside-output.json"
    prepare_path.write_text(json.dumps(prepare))
    status, _ = resolve_trust.evaluate(run)
    assert status["state"] == "BLOCKED"
    errors = status["items"][0]["errors"]
    assert any("packet path is noncanonical" in error for error in errors)
    assert any("prompt path is noncanonical" in error for error in errors)
    assert any("output path is noncanonical" in error for error in errors)
    assert draft.read_bytes() == before[draft]


def test_different_runs_share_one_canonical_chunk_lock(tmp_path):
    work, draft, checkpoint, rows, packets = _workspace(tmp_path)
    run_a = resolve_trust.prepare("test-co", work, _models(), tmp_path / "runs-a")
    models_b = _models()
    models_b["challenger"] = tr.model_config("other-challenger", "family-z")
    run_b = resolve_trust.prepare("test-co", work, models_b, tmp_path / "runs-b")
    prepare_a = json.loads((run_a / "prepare.json").read_text())
    prepare_b = json.loads((run_b / "prepare.json").read_text())
    assert run_a != run_b
    assert resolve_trust._canonical_lock_path(prepare_a, 0) == resolve_trust._canonical_lock_path(prepare_b, 0)
    assert resolve_trust._canonical_lock_path(prepare_a, 0) == work / ".trust-resolver-locks" / "test-co.lock"
    shared_store = tmp_path / "co_verdicts_osm.json"
    assert tr.resource_lock_path(work, shared_store) == tr.resource_lock_path(tmp_path / "other-work", shared_store)


def test_checkpoint_resolution_vector_detects_validly_rehashed_machine_metadata_drift(tmp_path):
    run, work, draft, checkpoint, rows, packets, before = _prepared(tmp_path)
    _write_output(run, 1, "challenger", copy.deepcopy(rows[0]))
    assert resolve_trust.apply_chunk(run, 0, apply=True)["state"] == "APPLIED"
    stored_manifest = json.loads(checkpoint.read_text())
    resolved = json.loads(draft.read_text())
    wrapper = resolved[0]["trust_resolution"]
    wrapper["transaction_id"] = "9" * 64
    content = dict(wrapper)
    content.pop("resolution_sha256")
    wrapper["resolution_sha256"] = tr.sha256_json(content)
    draft.write_text(json.dumps(resolved, indent=1) + "\n")
    chunk = [packets[1]]
    state = judge_packets.inspect_draft(chunk, draft)
    assert state["status"] == "complete"
    decision_input, missing = judge_packets.decision_fingerprint(chunk)
    assert not missing
    errors = judge_packets.validate_manifest(
        stored_manifest, "test-co", 0, chunk, decision_input, state
    )
    assert "canonical draft's machine resolution rows changed" in errors


def test_forged_receipt_cannot_claim_unresolved_source_applied(tmp_path):
    run, work, draft, checkpoint, rows, packets, before = _prepared(tmp_path)
    receipt = run / "receipts" / "chunk-00.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps({
        "version": 1, "kind": "parking-trust-receipt", "transaction_id": "0" * 64,
        "run_id": run.name, "chunk": 0,
        "before_sha256": hashlib.sha256(draft.read_bytes()).hexdigest(),
        "after_sha256": hashlib.sha256(draft.read_bytes()).hexdigest(),
        "checkpoint_after_sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
        "applied_fids": [],
    }))
    status, _ = resolve_trust.evaluate(run)
    assert status["state"] == "RECOVERY_REQUIRED"
    result = resolve_trust.apply_chunk(run, 0, apply=True)
    assert result["state"] == "BLOCKED" and result["fids"] == [1]
    assert draft.read_bytes() == before[draft]


def test_forged_journal_paths_cannot_redirect_recovery_writes(tmp_path):
    run, work, draft, checkpoint, rows, packets, before = _prepared(tmp_path)
    _write_output(run, 1, "challenger", copy.deepcopy(rows[0]))
    plan = resolve_trust.apply_chunk(run, 0, apply=False)
    unrelated = tmp_path / "unrelated.json"
    unrelated.write_text('{"safe":true}')
    journal = run / "transactions" / "chunk-00.json"
    journal.parent.mkdir(parents=True)
    forged = {
        "version": 1, "kind": "parking-trust-transaction",
        "run_id": run.name, "transaction_id": plan["transaction_id"], "chunk": 0,
        "target": str(unrelated), "checkpoint": str(unrelated),
        "before_sha256": plan["before_sha256"], "after_sha256": plan["after_sha256"],
        "stage": str(unrelated), "backup": str(unrelated), "receipt": str(unrelated),
        "journal": str(journal), "archive": str(unrelated),
        "checkpoint_before_sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
        "checkpoint_after": json.loads(checkpoint.read_text()),
        "checkpoint_after_sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
        "applied_fids": plan["applied_fids"],
    }
    journal.write_text(json.dumps(forged))
    result = resolve_trust.apply_chunk(run, 0, apply=True)
    assert result["state"] == "RECOVERY_REQUIRED"
    assert any("does not match the prepared canonical transaction" in error
               for error in result["errors"])
    assert unrelated.read_text() == '{"safe":true}'
    assert draft.read_bytes() == before[draft]


def test_second_prepared_run_cannot_overwrite_first_run_on_same_chunk(tmp_path):
    work, draft, checkpoint, rows, packets = _workspace(tmp_path)
    run_a = resolve_trust.prepare("test-co", work, _models(), tmp_path / "runs-a")
    models_b = _models()
    models_b["challenger"] = tr.model_config("other-challenger", "family-z")
    run_b = resolve_trust.prepare("test-co", work, models_b, tmp_path / "runs-b")
    _write_output(run_a, 1, "challenger", copy.deepcopy(rows[0]))
    _write_output(run_b, 1, "challenger", copy.deepcopy(rows[0]))
    assert resolve_trust.apply_chunk(run_a, 0, apply=True)["state"] == "APPLIED"
    with pytest.raises(ValueError, match="cannot recover the exact prepared"):
        resolve_trust.apply_chunk(run_b, 0, apply=True)


def test_store_merge_rechecks_packet_bytes_and_resolution_checkpoint(tmp_path, monkeypatch):
    run, work, draft, checkpoint, rows, packets, before = _prepared(tmp_path)
    _write_output(run, 1, "challenger", copy.deepcopy(rows[0]))
    assert resolve_trust.apply_chunk(run, 0, apply=True)["state"] == "APPLIED"
    values = json.loads(draft.read_text())
    resolved = values[0]
    for role in tr.ROLES:
        envelope = resolved["trust_resolution"].get(role)
        if envelope is None:
            continue
        envelope["packet_sha256"] = "f" * 64
        body = dict(envelope)
        body.pop("envelope_sha256")
        envelope["envelope_sha256"] = tr.sha256_json(body)
    resolved["judge_provenance"]["packet_sha256"] = "f" * 64
    wrapper = resolved["trust_resolution"]
    body = dict(wrapper)
    body.pop("resolution_sha256")
    wrapper["resolution_sha256"] = tr.sha256_json(body)
    draft.write_text(json.dumps(values, indent=1) + "\n")

    store = tmp_path / "store.json"
    monkeypatch.setattr(merge_drafts, "PADJ_TMP", str(work))
    assert merge_drafts.main([str(store), "test-co", "--write"]) == 1
    assert not store.exists()


def test_live_journal_dominates_receipt_and_retry_archives_it(tmp_path):
    run, work, draft, checkpoint, rows, packets, before = _prepared(tmp_path)
    _write_output(run, 1, "challenger", copy.deepcopy(rows[0]))
    assert resolve_trust.apply_chunk(run, 0, apply=True)["state"] == "APPLIED"
    archived = next((run / "Archive" / "transactions").glob("chunk-00.*.json"))
    live = run / "transactions" / "chunk-00.json"
    live.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(archived, live)
    status, _ = resolve_trust.evaluate(run)
    assert status["state"] == "RECOVERY_REQUIRED"
    assert resolve_trust.apply_chunk(run, 0, apply=False)["state"] == "RECOVERY_REQUIRED"
    assert resolve_trust.apply_chunk(run, 0, apply=True)["state"] == "APPLIED"
    assert not live.exists()


def test_calibration_authority_writer_uses_the_resolver_area_lock(tmp_path, monkeypatch):
    work, draft, checkpoint, rows, packets = _workspace(tmp_path)
    ledger = tmp_path / "calibration.json"
    monkeypatch.setattr(calibration, "PADJ_TMP", str(work))
    monkeypatch.setattr(calibration, "LEDGER", str(ledger))
    assert calibration.main([
        "add", "test-co", "--reviewed", "KEEP=none", "--date", "2026-09-26",
    ]) == 0
    assert (work / ".trust-resolver-locks" / "test-co.lock").exists()


def test_status_reconstructs_and_rejects_a_self_consistent_but_noncanonical_receipt(tmp_path):
    run, work, draft, checkpoint, rows, packets, before = _prepared(tmp_path)
    _write_output(run, 1, "challenger", copy.deepcopy(rows[0]))
    assert resolve_trust.apply_chunk(run, 0, apply=True)["state"] == "APPLIED"
    receipt_path = run / "receipts" / "chunk-00.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["transaction_id"] = "8" * 64
    receipt_path.write_text(json.dumps(receipt))
    status, _ = resolve_trust.evaluate(run)
    assert status["state"] == "RECOVERY_REQUIRED"
    assert any("does not match the prepared canonical transaction" in error for error in status["errors"])


def test_preservation_retry_never_hides_checkpoint_drift_or_live_journal(tmp_path):
    human_row = _decision()
    merge_drafts.set_verdict(human_row, "DROP", "human authority", "2026-09-26")
    run, work, draft, checkpoint, rows, packets, before = _prepared(tmp_path / "drift", [human_row])
    assert resolve_trust.apply_chunk(run, 0, apply=True)["state"] == "PRESERVED"
    checkpoint.write_text(json.dumps({"drift": True}))
    status, _ = resolve_trust.evaluate(run)
    assert status["state"] == "RECOVERY_REQUIRED"
    with pytest.raises(ValueError, match="preservation receipt source hashes changed"):
        resolve_trust.apply_chunk(run, 0, apply=True)

    run, work, draft, checkpoint, rows, packets, before = _prepared(tmp_path / "journal", [human_row])
    journal = run / "transactions" / "chunk-00.json"
    journal.parent.mkdir(parents=True)
    journal.write_text(json.dumps({"unexpected": True}))
    status, _ = resolve_trust.evaluate(run)
    assert status["state"] == "RECOVERY_REQUIRED"
    result = resolve_trust.apply_chunk(run, 0, apply=True)
    assert result["state"] == "RECOVERY_REQUIRED"
    assert journal.exists() and draft.read_bytes() == before[draft]


def test_judge_packet_writer_parses_once_and_locks_exact_destination(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(judge_packets, "_main_under_area_lock",
                        lambda namespace: seen.append(namespace) or 17)
    first = tmp_path / "first"
    second = tmp_path / "second"
    assert judge_packets.main(["--tmp", str(first), "test-co"]) == 17
    assert judge_packets.main(["test-co", f"--tmp={second}"]) == 17
    assert judge_packets.main([
        "test-co", "--tmp", str(first), "--tmp", str(second),
    ]) == 17
    assert [Path(namespace.tmp) for namespace in seen] == [first, second, second]
    assert (first / ".trust-resolver-locks" / "test-co.lock").exists()
    assert (second / ".trust-resolver-locks" / "test-co.lock").exists()


def test_live_journal_remains_recovery_dominant_after_inbox_drift(tmp_path):
    run, work, draft, checkpoint, rows, packets, before = _prepared(tmp_path)
    output = _write_output(run, 1, "challenger", copy.deepcopy(rows[0]))
    old_checkpoint = checkpoint.read_bytes()
    with pytest.raises(RuntimeError, match="simulated crash"):
        resolve_trust.apply_chunk(run, 0, apply=True, crash_after_replace=True)
    replaced = draft.read_bytes()
    value = json.loads(output.read_text())
    value["assignment_id"] = "0" * 64
    output.write_text(json.dumps(value))
    status, _ = resolve_trust.evaluate(run)
    assert status["state"] == "RECOVERY_REQUIRED"
    result = resolve_trust.apply_chunk(run, 0, apply=True)
    assert result["state"] == "RECOVERY_REQUIRED"
    assert draft.read_bytes() == replaced and checkpoint.read_bytes() == old_checkpoint
    assert (run / "transactions" / "chunk-00.json").exists()


def test_wrong_type_inbox_after_crash_stays_structured_recovery_without_mutation(tmp_path):
    run, work, draft, checkpoint, rows, packets, before = _prepared(tmp_path)
    output = _write_output(run, 1, "challenger", copy.deepcopy(rows[0]))
    old_checkpoint = checkpoint.read_bytes()
    with pytest.raises(RuntimeError, match="simulated crash"):
        resolve_trust.apply_chunk(run, 0, apply=True, crash_after_replace=True)
    replaced = draft.read_bytes()
    journal = run / "transactions" / "chunk-00.json"
    journal_bytes = journal.read_bytes()
    value = json.loads(output.read_text())
    value["decision"] = []
    output.write_text(json.dumps(value))
    status, _ = resolve_trust.evaluate(run)
    assert status["state"] == "RECOVERY_REQUIRED"
    assert any("decision is not an object" in item_error
               for item in status["items"] for item_error in item["errors"])
    result = resolve_trust.apply_chunk(run, 0, apply=True)
    assert result["state"] == "RECOVERY_REQUIRED"
    assert draft.read_bytes() == replaced
    assert checkpoint.read_bytes() == old_checkpoint
    assert journal.read_bytes() == journal_bytes
