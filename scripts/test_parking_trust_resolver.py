"""End-to-end tests for provenance-preserving autonomous parking resolution."""
from __future__ import annotations

import base64
import contextlib
import copy
import hashlib
import json
import os
import shutil
import stat
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
TOOLS = HERE / "parking-adjud" / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import judge_packets
import judge_review_sheet
from judge_validation import (
    current_authority,
    machine_decision_projection,
    original_judge_projection,
    validate_publication_attestation,
    validate_verdict_row,
)
import calibration
import dossier_output
import merge_drafts
import resolve_trust
import trust_engine
import trust_resolution as tr

_REAL_RESOLVER_DATA = resolve_trust.DATA


@pytest.fixture(autouse=True)
def _isolate_resolver_calibration_resource(tmp_path, monkeypatch):
    data = tmp_path / ".resolver-data"
    data.mkdir(mode=0o700, exist_ok=True)
    (data / "calibration.json").write_bytes(
        (_REAL_RESOLVER_DATA / "calibration.json").read_bytes()
    )
    monkeypatch.setattr(resolve_trust, "DATA", data)
    store = tmp_path / "store.json"
    floor = merge_drafts.publication_floor_path(store)
    floor.write_bytes(merge_drafts.verdict_source.publication_floor_json_bytes(
        merge_drafts.verdict_source.build_empty_publication_floor_document(
            store.name
        )
    ))


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
    ladder = tmp_path / f"{area}_ladder"
    ladder.mkdir(parents=True, exist_ok=True)
    for zoom in ("z1", "z2", "z3"):
        path = ladder / f"{fid:04d}_{zoom}.png"
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + f"tile-{fid}-{zoom}".encode())
        tiles[zoom] = str(path)
    return {
        "fid": fid, "area": area,
        "source_generation": {
            "manifest_path": f"{area}_generation.json",
            "manifest_sha256": "a" * 64,
            "artifact_sha256": {
                "dossier": "b" * 64, "serves": "c" * 64,
                "context": "d" * 64, "walk": "e" * 64,
            },
        },
        "osm": [f"way/{fid}"], "prior": "surveyed",
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
    facilities = [{
        "fid": row["fid"], "lat": 40.0 + row["fid"] / 1000, "lon": -105.0,
        "osm": row["osm"], "prior": row["prior"], "rings": [],
        "tags_union": {"name": f"Lot {row['fid']}"},
    } for row in rows]
    serves_doc = {
        str(row["fid"]): {
            "served": True, "trail": "Test Trail", "dist_m": 80,
            "fallback": False, "n": 1,
        }
        for row in rows
    }
    context_doc = {
        str(row["fid"]): {
            "category": "NEUTRAL", "evidence": "",
            "fac_label": None, "fac_area_m": None,
        }
        for row in rows
    }
    walk_doc = {
        str(row["fid"]): {
            "walk_m": 100, "conn": "path", "trail": "Test Trail",
        }
        for row in rows
    }
    (work / f"{area}_dossier.json").write_text(json.dumps({
        "slug": area, "facilities": facilities,
    }))
    for suffix, document in (
        ("serves2", serves_doc), ("context", context_doc), ("walk", walk_doc),
    ):
        (work / f"{area}_{suffix}.json").write_text(json.dumps(document))
    generation_capture = dossier_output.bootstrap_generation(work, area)
    source_generation = dossier_output.portable_source_generation(
        generation_capture, area
    )
    for packet in packets.values():
        packet["source_generation"] = copy.deepcopy(source_generation)
    packet_path = work / f"{area}_packets.json"
    packet_path.write_text(json.dumps({str(fid): packet for fid, packet in packets.items()}, indent=1))
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


def _advance_context_generation(work: Path, area="test-co") -> dict:
    manifest_path = dossier_output.generation_manifest_path(work, area)
    parent = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    context_path = work / f"{area}_context.json"
    context = json.loads(context_path.read_text())
    for value in context.values():
        value["ignored_generation_marker"] = parent[:12]
    context_path.write_text(json.dumps(context))
    capture = dossier_output.commit_generation(work, area, parent)
    return dossier_output.portable_source_generation(capture, area)


def _commit_generation_and_rebind(work: Path, area="test-co") -> dict:
    manifest_path = dossier_output.generation_manifest_path(work, area)
    parent = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    capture = dossier_output.commit_generation(work, area, parent)
    source_generation = dossier_output.portable_source_generation(capture, area)
    packet_path = work / f"{area}_packets.json"
    packets = json.loads(packet_path.read_text())
    for packet in packets.values():
        packet["source_generation"] = copy.deepcopy(source_generation)
    packet_path.write_text(json.dumps(packets, indent=1))
    for draft_path in sorted(work.glob(f"{area}_verdict_draft_*.json")):
        chunk_index = int(draft_path.stem.rsplit("_", 1)[1])
        rows = json.loads(draft_path.read_text())
        chunk = [packets[str(row["fid"])] for row in rows]
        state = judge_packets.inspect_draft(chunk, draft_path)
        decision_input, missing = judge_packets.decision_fingerprint(chunk)
        assert state["status"] == "complete" and decision_input and not missing
        checkpoint = judge_packets.manifest_value(
            area, chunk_index, chunk, decision_input, state
        )
        (work / f"{area}_checkpoint_{chunk_index:02d}.json").write_text(
            json.dumps(checkpoint, indent=1) + "\n"
        )
    return source_generation


def _models(same_family=False):
    return {
        "primary": tr.model_config("primary-model", "shared-family" if same_family else "family-a"),
        "challenger": tr.model_config("challenger-model", "shared-family" if same_family else "family-b"),
        "arbiter": tr.model_config("arbiter-model", "shared-family" if same_family else "family-c"),
    }


def _install_external_sources(work: Path, sources: list[tuple[Path, str]]) -> None:
    if not sources:
        return
    root = work / "test-co_external"
    root.mkdir(parents=True)
    manifest_sources = []
    for source_path, evidence_id in sources:
        artifact = root / f"{evidence_id}.json"
        artifact.write_bytes(source_path.read_bytes())
        metadata = root / f"{evidence_id}.item.json"
        metadata.write_text(json.dumps({"modified": 1790377200000}))
        manifest_sources.append({
            "id": evidence_id,
            "path": str(artifact.resolve()),
            "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            "query_endpoint": f"https://evidence.example/{evidence_id}",
            "retrieved_at": "2026-09-27T17:16:10+00:00",
            "item_metadata_path": str(metadata.resolve()),
            "item_metadata_sha256": hashlib.sha256(metadata.read_bytes()).hexdigest(),
        })
    (root / "manifest.json").write_text(json.dumps({
        "kind": "trekdex-frozen-bulk-evidence",
        "area": "test-co",
        "fetched_at": "2026-09-27T17:16:10+00:00",
        "sources": manifest_sources,
    }))


def _prepared(tmp_path, rows=None, same_family=False, external_sources=()):
    work, draft, checkpoint, rows, packets = _workspace(tmp_path, rows)
    _install_external_sources(work, list(external_sources))
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


def _external(path: Path, evidence_id: str, *supports: tuple[str, str, str]) -> dict:
    assert path.is_file()
    return {
        "id": evidence_id,
        "supports": [
            {"axis": axis, "call": call, "claim": claim}
            for axis, call, claim in supports
        ],
    }


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
         "row": resolved, "packet": packets[1],
         "published": {"verdict": "KEEP", "src": "judge-fanout", "area": "test-co"}}
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


def test_v2_arbiter_requires_exact_axis_agreement():
    primary_decision = _decision(verdict="DROP")
    challenger_decision = _decision(verdict="KEEP")
    arbiter_decision = copy.deepcopy(primary_decision)
    arbiter_decision["public"] = {
        "call": "no", "evidence": "Official record says access is restricted",
    }
    arbiter_decision["serves"] = {
        "call": "yes", "evidence": "The mapped trailhead is adjacent",
    }
    primary = {
        "decision": primary_decision,
        "model": tr.model_config("primary", "family-a"),
    }
    challenger = {
        "decision": challenger_decision,
        "model": tr.model_config("challenger", "family-b"),
    }
    arbiter = {
        "decision": arbiter_decision,
        "model": tr.model_config("arbiter", "family-c"),
    }

    result = tr.resolve(trust_engine.ROUTE_CHALLENGE, primary, challenger, arbiter)
    assert result["state"] == "human_exception"
    assert "exact axis agreement" in result["reason"]

    legacy = tr.resolve(
        trust_engine.ROUTE_CHALLENGE, primary, challenger, arbiter,
        policy_version=tr.LEGACY_VERSION,
    )
    assert legacy["state"] == "resolved"
    assert legacy["result"] == "ARBITRATED_AGREEMENT"


@pytest.mark.parametrize("collision", ["id", "family"])
def test_v2_arbiter_must_be_distinct_from_every_parent(collision):
    primary_model = tr.model_config("primary", "family-a")
    challenger_model = tr.model_config("challenger", "family-b")
    arbiter_model = tr.model_config(
        "primary" if collision == "id" else "arbiter",
        "family-c" if collision == "id" else "family-a",
    )
    primary = {"decision": _decision(verdict="KEEP"), "model": primary_model}
    challenger = {"decision": _decision(verdict="DROP"), "model": challenger_model}
    arbiter = {"decision": copy.deepcopy(challenger["decision"]), "model": arbiter_model}

    result = tr.resolve(trust_engine.ROUTE_CHALLENGE, primary, challenger, arbiter)
    assert result["state"] == "human_exception"
    assert "duplicate model id or family" in result["reason"]


def test_v1_distinct_family_agreement_remains_verdict_only():
    primary_decision = _decision(verdict="DROP")
    challenger_decision = copy.deepcopy(primary_decision)
    challenger_decision["public"] = {"call": "no", "evidence": "private owner"}
    challenger_decision["serves"] = {"call": "unclear", "evidence": "service uncertain"}
    primary = {
        "decision": primary_decision,
        "model": {"id": "legacy-a", "family": "family-a"},
    }
    challenger = {
        "decision": challenger_decision,
        "model": {"id": "legacy-b", "family": "family-b"},
    }
    result = tr.resolve(
        trust_engine.ROUTE_CHALLENGE, primary, challenger, None,
        policy_version=tr.LEGACY_VERSION,
    )
    assert result["state"] == "resolved"
    assert result["result"] == "DISTINCT_FAMILY_AGREEMENT"


def test_v2_same_family_parent_disagreement_stops_before_arbiter(tmp_path):
    primary = _decision()
    challenger = _decision(verdict="DROP")
    run, *_ = _prepared(tmp_path, [primary], same_family=True)
    _write_output(run, 1, "challenger", challenger)
    status, _ = resolve_trust.evaluate(run)
    assert status["state"] == "BLOCKED"
    assert status["counts"]["human_exceptions"] == 1
    assert status["counts"]["pending_arbiter"] == 0
    assert "same-family parents disagree" in status["items"][0]["reason"]


def test_same_family_never_auto_resolves_under_v2_even_with_structured_evidence(tmp_path):
    row = _decision()
    evidence = tmp_path / "fresh-official.json"
    evidence.write_bytes(b"fresh independent evidence bytes")
    run, work, draft, checkpoint, rows, packets, before = _prepared(
        tmp_path, [row], same_family=True,
        external_sources=[(evidence, "official-record")],
    )
    _write_output(run, 1, "challenger", copy.deepcopy(row))
    status, _ = resolve_trust.evaluate(run)
    assert status["state"] == "PENDING" and status["counts"]["pending_arbiter"] == 1
    _write_output(run, 1, "arbiter", copy.deepcopy(row))
    status, _ = resolve_trust.evaluate(run)
    assert status["state"] == "BLOCKED" and status["counts"]["human_exceptions"] == 1

    reused = Path(packets[1]["tiles"]["z1"])
    reused_external = [_external(
        reused, "reused-z1",
        ("public", "yes", "reused frame claims public access"),
        ("serves", "yes", "reused frame claims trail service"),
    )]
    arbiter_reusing_input = copy.deepcopy(row)
    arbiter_reusing_input["public"]["evidence"] += "; [external:reused-z1] claims public"
    arbiter_reusing_input["serves"]["evidence"] += "; [external:reused-z1] claims service"
    _write_output(run, 1, "arbiter", arbiter_reusing_input, reused_external)
    status, _ = resolve_trust.evaluate(run)
    assert status["state"] == "BLOCKED" and status["counts"]["invalid"] == 1
    assert any("is not host-assigned" in error
               for error in status["items"][0]["errors"])

    public_only = [_external(
        evidence, "official-record",
        ("public", "yes", "record states public access"),
    )]
    incomplete = copy.deepcopy(row)
    incomplete["public"]["evidence"] += "; [external:official-record] confirms public access"
    _write_output(run, 1, "arbiter", incomplete, public_only)
    status, _ = resolve_trust.evaluate(run)
    assert status["state"] == "BLOCKED" and status["counts"]["human_exceptions"] == 1

    complete_external = [_external(
        evidence, "official-record",
        ("public", "yes", "record states public access"),
        ("serves", "yes", "record identifies the served trailhead"),
    )]
    corroborated = copy.deepcopy(row)
    corroborated["public"]["evidence"] += "; [external:official-record] confirms public access"
    corroborated["serves"]["evidence"] += "; [external:official-record] identifies the trailhead"
    _write_output(run, 1, "arbiter", corroborated, complete_external)
    status, _ = resolve_trust.evaluate(run)
    assert status["state"] == "BLOCKED"
    assert status["counts"]["human_exceptions"] == 1
    assert "duplicate model id or family" in status["items"][0]["reason"]

    overturned = _decision(verdict="DROP")
    overturned["serves"]["evidence"] += "; [external:official-record] claims no service"
    overturn_external = [_external(
        evidence, "official-record", ("serves", "no", "record claims no trail service")
    )]
    _write_output(run, 1, "arbiter", overturned, overturn_external)
    status, _ = resolve_trust.evaluate(run)
    assert status["state"] == "BLOCKED"
    assert "duplicate model id or family" in status["items"][0]["reason"]


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


@pytest.mark.parametrize(
    ("wrapper_version", "envelope_version"),
    [(tr.VERSION, tr.LEGACY_VERSION), (tr.LEGACY_VERSION, tr.VERSION)],
)
def test_resolution_wrapper_rejects_mixed_envelope_versions(
        tmp_path, wrapper_version, envelope_version):
    run, work, draft, checkpoint, rows, packets, before = _prepared(tmp_path)
    _write_output(run, 1, "challenger", copy.deepcopy(rows[0]))
    assert resolve_trust.apply_chunk(run, 0, apply=True)["state"] == "APPLIED"
    resolved = json.loads(draft.read_text())[0]
    wrapper = resolved["trust_resolution"]
    wrapper["version"] = wrapper_version
    wrapper["policy_id"] = (
        tr.POLICY_ID if wrapper_version == tr.VERSION else tr.LEGACY_POLICY_ID
    )
    for role in tr.ROLES:
        envelope = wrapper.get(role)
        if envelope is None:
            continue
        envelope["version"] = envelope_version
        body = dict(envelope)
        body.pop("envelope_sha256")
        envelope["envelope_sha256"] = tr.sha256_json(body)
    body = dict(wrapper)
    body.pop("resolution_sha256")
    wrapper["resolution_sha256"] = tr.sha256_json(body)

    errors, _ = validate_verdict_row(resolved, packets[1])
    assert any("does not match wrapper version" in error for error in errors)


def test_resolved_provenance_survives_the_existing_store_merge(tmp_path, monkeypatch):
    run, work, draft, checkpoint, rows, packets, before = _prepared(tmp_path)
    _write_output(run, 1, "challenger", copy.deepcopy(rows[0]))
    assert resolve_trust.apply_chunk(run, 0, apply=True)["state"] == "APPLIED"
    store = tmp_path / "store.json"
    monkeypatch.setattr(merge_drafts, "PADJ_TMP", str(work))
    assert merge_drafts.main([
        str(store), "test-co", "--judged", "2026-09-26",
        "--resolver-run", str(run), "--write",
    ]) == 0
    stored = json.loads(store.read_text())["way/1"]
    assert stored["trust_resolution"]["result"] == "DISTINCT_FAMILY_AGREEMENT"
    assert stored["judge_provenance"]["model_id"] == "challenger-model"
    assert "override" not in stored
    assert validate_publication_attestation(stored) == []
    proof_registry = resolve_trust.replay_trust._load_publication_proofs(
        store.parent, store.name
    )
    assert resolve_trust.replay_trust._validate_publication_proof(
        stored, proof_registry
    ) == []
    forged = copy.deepcopy(stored)
    forged_attestation = forged["publication_attestation"]
    forged_attestation["publication_proof_sha256"] = "f" * 64
    body = dict(forged_attestation)
    body.pop("attestation_sha256")
    forged_attestation["attestation_sha256"] = tr.sha256_json(body)
    assert validate_publication_attestation(forged) == []
    assert "publication proof is absent from the canonical registry" in (
        resolve_trust.replay_trust._validate_publication_proof(
            forged, proof_registry
        )
    )
    coherently_rehashed = copy.deepcopy(stored)
    coherently_rehashed["serves"]["evidence"] = "forged published decision evidence"
    att = coherently_rehashed["publication_attestation"]
    att["decision_sha256"] = tr.sha256_json(tr.decision_projection(coherently_rehashed))
    att["evidence_sha256"] = tr.evidence_sha256(coherently_rehashed)
    att["publication_source_sha256"] = tr.sha256_json(
        {
            key: coherently_rehashed.get(key)
            for key in ("area", "fid", "osm", "lat", "lon", "rings", "name", "src", "judged")
        }
    )
    body = dict(att)
    body.pop("attestation_sha256")
    att["attestation_sha256"] = tr.sha256_json(body)
    assert "publication proof v4 invalid: proof v4 final decision/authority row mismatch" in (
        resolve_trust.replay_trust._validate_publication_proof(
            coherently_rehashed, proof_registry
        )
    )
    attestation_sha = stored["publication_attestation"]["attestation_sha256"]
    moved = copy.deepcopy(stored)
    moved["lat"] += 0.5
    assert "publication_attestation publication_source_sha256 mismatch" in (
        validate_publication_attestation(moved)
    )
    moved_attestation = moved["publication_attestation"]
    moved_attestation["publication_source_sha256"] = tr.sha256_json(
        merge_drafts.publication_projection(moved)
    )
    moved_body = dict(moved_attestation)
    moved_body.pop("attestation_sha256")
    moved_attestation["attestation_sha256"] = tr.sha256_json(moved_body)
    assert validate_publication_attestation(moved) == []
    assert resolve_trust.replay_trust._validate_publication_proof(
        moved, proof_registry
    ) == [
        "publication proof v4 invalid: proof v4 final lat differs from bound "
        "facility"
    ]
    report = trust_engine.build_report([{
        "key": "way/1", "source_key": "1", "store": "co_verdicts_osm.json",
        "area": "test-co", "row": stored,
        "published_authority_attestation_sha256": attestation_sha,
        "published": {"verdict": "KEEP", "src": "judge-fanout", "area": "test-co"},
    }], {"areas": []}, {}, {"lots": {}}, {
        "corpus_sha256": "x", "sidecar_sha256": None, "source_rows": 1,
        "unique_clusters": 1, "folded_duplicates": 0,
    }, include_items=True)
    assert report["items"][0]["machine_resolved"] is True
    assert report["items"][0]["route"] == trust_engine.ROUTE_RESOLVED
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
    prompt_before = prompt.read_bytes()
    prompt.write_bytes(prompt_before + b"\ntampered")
    with pytest.raises(ValueError, match="challenger prompt bytes/hash mismatch"):
        resolve_trust.evaluate(run)
    assert draft.read_bytes() == before[draft]
    prompt.write_bytes(prompt_before)

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


def test_same_verdict_human_confirmation_is_hash_bound_and_preserved(tmp_path, monkeypatch):
    human_row = _decision(verdict="DROP", confidence="strong")
    work, draft, checkpoint, rows, packets = _workspace(tmp_path, [human_row])
    authority_run = resolve_trust.prepare(
        "test-co", work, _models(), tmp_path / "authority-runs"
    )
    monkeypatch.setattr(merge_drafts, "PADJ_TMP", str(work))
    monkeypatch.setattr(judge_review_sheet, "PADJ_TMP", str(work))
    assert judge_review_sheet.main([
        "test-co", "--authority-run", str(authority_run), "--sample", "0",
    ]) == 0
    review_receipt = judge_review_sheet.review.artifact_paths(
        work, "test-co", authority_run.name
    )["receipt"]
    store = tmp_path / "store.json"
    assert merge_drafts.main([
        str(store), "test-co", "--judged", "2026-09-27",
        "--confirm", "1=DROP",
        "--note", "accepted specific conservative recommendation",
        "--reviewer", "trekdex-project-owner",
        "--authority-run", str(authority_run),
        "--review-receipt", str(review_receipt),
    ]) == 0
    confirmed = json.loads(draft.read_text())[0]
    assert confirmed["human_confirmation"]["authority_receipt_sha256"]

    run = resolve_trust.prepare("test-co", work, _models(), tmp_path / "runs")
    status, _ = resolve_trust.evaluate(run)
    assert status["state"] == "READY" and status["counts"]["preserved_authority"] == 1
    result = resolve_trust.apply_chunk(run, 0, apply=True)
    assert result["state"] == "PRESERVED"
    preserved = json.loads(draft.read_text())[0]
    assert preserved["human_confirmation"] == confirmed["human_confirmation"]
    assert "override" not in preserved and "trust_resolution" not in preserved

    tampered = copy.deepcopy(confirmed)
    tampered["human_confirmation"]["packet_sha256"] = "b" * 64
    assert any(
        "packet_sha256" in error
        for error in validate_verdict_row(tampered, packets[1])[0]
    )


def test_review_human_confirmation_is_hash_bound_and_preserved(
        tmp_path, monkeypatch):
    human_row = _decision(verdict="REVIEW", confidence="leaning")
    work, draft, checkpoint, rows, packets = _workspace(
        tmp_path, [human_row]
    )
    authority_run = resolve_trust.prepare(
        "test-co", work, _models(), tmp_path / "authority-runs"
    )
    monkeypatch.setattr(merge_drafts, "PADJ_TMP", str(work))
    monkeypatch.setattr(judge_review_sheet, "PADJ_TMP", str(work))
    assert judge_review_sheet.main([
        "test-co", "--authority-run", str(authority_run), "--sample", "0",
    ]) == 0
    review_receipt = judge_review_sheet.review.artifact_paths(
        work, "test-co", authority_run.name
    )["receipt"]
    store = tmp_path / "store.json"
    assert merge_drafts.main([
        str(store), "test-co", "--judged", "2026-10-02",
        "--confirm", "1=REVIEW",
        "--note", "explicitly deferred for later human review",
        "--reviewer", "trekdex-project-owner",
        "--authority-run", str(authority_run),
        "--review-receipt", str(review_receipt),
    ]) == 0
    confirmed = json.loads(draft.read_text())[0]
    assert confirmed["verdict"] == "REVIEW"
    assert confirmed["resolve_hint"] == human_row["resolve_hint"]
    assert confirmed["human_confirmation"]["verdict"] == "REVIEW"
    assert validate_verdict_row(confirmed, packets[1])[0] == []
    machine, errors = machine_decision_projection(confirmed)
    assert errors == [] and machine == human_row

    terminal_run = resolve_trust.prepare(
        "test-co", work, _models(), tmp_path / "terminal-runs"
    )
    status, _ = resolve_trust.evaluate(terminal_run)
    assert status["state"] == "READY"
    assert status["counts"]["preserved_authority"] == 1
    assert resolve_trust.apply_chunk(
        terminal_run, 0, apply=True
    )["state"] == "PRESERVED"
    shielded = json.loads(draft.read_text())[0]
    assert shielded == confirmed
    assert shielded["verdict"] == "REVIEW"
    assert shielded["resolve_hint"] == human_row["resolve_hint"]
    assert "override" not in shielded and "trust_resolution" not in shielded


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
    valid = json.loads(prepare_path.read_text())
    mutations = (
        ("packet path", lambda doc: doc["items"][0].__setitem__(
            "packet_path", "../../outside-packet.json"
        )),
        ("prompt path", lambda doc: doc["items"][0]["assignments"]["challenger"].__setitem__(
            "prompt_path", "../../outside-prompt.txt"
        )),
        ("output path", lambda doc: doc["items"][0]["assignments"]["challenger"].__setitem__(
            "output_path", "../../outside-output.json"
        )),
    )
    for message, mutate in mutations:
        forged = copy.deepcopy(valid)
        mutate(forged)
        prepare_path.write_text(json.dumps(forged))
        with pytest.raises(ValueError, match=message):
            resolve_trust.evaluate(run)
    prepare_path.write_text(json.dumps(valid))
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
    assert resolve_trust._canonical_lock_path(
        run_a, prepare_a, 0
    ) == resolve_trust._canonical_lock_path(run_b, prepare_b, 0)
    assert resolve_trust._canonical_lock_path(
        run_a, prepare_a, 0
    ) == work / ".trust-resolver-locks" / "test-co.lock"
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
    assert any("without a verified whole-run output seal" in error
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
    assert merge_drafts.main([str(store), "test-co", "--judged", "2026-09-29", "--write"]) == 1
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
    monkeypatch.setattr(
        judge_packets, "_main_under_area_lock",
        lambda namespace, generation_capture: seen.append(namespace) or 17,
    )
    first, *_ = _workspace(tmp_path / "first")
    second, *_ = _workspace(tmp_path / "second")
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
    assert result["state"] == "APPLIED"
    assert draft.read_bytes() == replaced and checkpoint.read_bytes() != old_checkpoint
    assert not (run / "transactions" / "chunk-00.json").exists()


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
    assert not any("decision is not an object" in item_error
                   for item in status["items"] for item_error in item["errors"])
    result = resolve_trust.apply_chunk(run, 0, apply=True)
    assert result["state"] == "APPLIED"
    assert draft.read_bytes() == replaced
    assert checkpoint.read_bytes() != old_checkpoint
    assert not journal.exists()


def test_prepare_schema_binds_models_items_primary_and_assignments(tmp_path):
    rows = [_decision(fid=1), _decision(fid=2)]
    run, work, draft, checkpoint, source_rows, packets, before = _prepared(
        tmp_path, rows
    )
    valid = json.loads((run / "prepare.json").read_text())

    cases = []

    missing_model_field = copy.deepcopy(valid)
    missing_model_field["models"]["primary"].pop("provider")
    cases.append((missing_model_field, "primary model schema mismatch"))

    missing_item_field = copy.deepcopy(valid)
    missing_item_field["items"][0].pop("row_index")
    cases.append((missing_item_field, "item schema mismatch"))

    missing_assignment_field = copy.deepcopy(valid)
    missing_assignment_field["items"][0]["assignments"]["challenger"].pop("role")
    cases.append((missing_assignment_field, "challenger assignment schema mismatch"))

    duplicate_fid = copy.deepcopy(valid)
    duplicate_fid["items"][1]["fid"] = duplicate_fid["items"][0]["fid"]
    cases.append((duplicate_fid, "duplicate fids"))

    duplicate_coordinate = copy.deepcopy(valid)
    duplicate_coordinate["items"][1]["row_index"] = duplicate_coordinate["items"][0]["row_index"]
    cases.append((duplicate_coordinate, "duplicate chunk/row coordinates"))

    primary_drift = copy.deepcopy(valid)
    primary_drift["items"][0]["primary"]["decision"]["exists"]["evidence"] = "forged"
    cases.append((primary_drift, "content does not match its run_id"))

    assignment_forgery = copy.deepcopy(valid)
    assignment_forgery["items"][0]["assignments"]["challenger"]["assignment_id"] = "f" * 64
    cases.append((assignment_forgery, "challenger assignment id mismatch"))

    primary_assignment_forgery = copy.deepcopy(valid)
    primary = primary_assignment_forgery["items"][0]["primary"]
    primary["assignment_id"] = "e" * 64
    body = dict(primary)
    body.pop("envelope_sha256")
    primary["envelope_sha256"] = tr.sha256_json(body)
    cases.append((primary_assignment_forgery, "assignment_id does not match assignment"))

    for forged, message in cases:
        with pytest.raises(ValueError, match=message):
            resolve_trust.validate_prepare_document(forged)
    assert draft.read_bytes() == before[draft]


def test_prepare_binds_normative_document_bytes_into_prompts_and_envelopes(
        tmp_path, monkeypatch):
    normative_dir = tmp_path / "normative"
    normative_dir.mkdir()
    protocol = normative_dir / "judge_protocol.md"
    lessons = normative_dir / "judge_lessons.md"
    protocol.write_bytes((TOOLS / "judge_protocol.md").read_bytes())
    lessons.write_bytes((TOOLS / "judge_lessons.md").read_bytes())
    monkeypatch.setattr(resolve_trust, "_NORMATIVE_PATHS", {
        "judge_protocol.md": protocol,
        "judge_lessons.md": lessons,
    })

    run, work, draft, checkpoint, rows, packets, before = _prepared(tmp_path / "case")
    prepare = json.loads((run / "prepare.json").read_text())
    hashes = prepare["normative_document_sha256"]
    assert hashes == {
        "judge_protocol.md": hashlib.sha256(protocol.read_bytes()).hexdigest(),
        "judge_lessons.md": hashlib.sha256(lessons.read_bytes()).hexdigest(),
    }
    item = prepare["items"][0]
    assert item["primary"]["normative_document_sha256"] == hashes
    for role in ("challenger", "arbiter"):
        assignment = item["assignments"][role]
        assert assignment["normative_document_sha256"] == hashes
        prompt = (run / assignment["prompt_path"]).read_text()
        assert hashes["judge_protocol.md"] in prompt
        assert hashes["judge_lessons.md"] in prompt

    protocol.write_bytes(protocol.read_bytes() + b"\nchanged")
    assert resolve_trust.evaluate(run)[0]["state"] == "PENDING"
    replacement = resolve_trust.prepare(
        "test-co", work, _models(), tmp_path / "case" / "runs"
    )
    assert replacement != run
    assert draft.read_bytes() == before[draft]


def test_external_catalog_supports_honest_per_source_retrieval_times(
        tmp_path):
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_bytes(b'{"source":1}')
    second.write_bytes(b'{"source":2}')
    work, *_ = _workspace(tmp_path / "workspace")
    _install_external_sources(work, [
        (first, "first-source"), (second, "second-source"),
    ])
    manifest_path = work / "test-co_external" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["sources"][1]["retrieved_at"] = "2026-10-01T17:52:40+00:00"
    manifest_path.write_text(json.dumps(manifest))

    run = resolve_trust.prepare(
        "test-co", work, _models(), tmp_path / "runs"
    )
    prepare = json.loads((run / "prepare.json").read_text())
    catalog = prepare["external_evidence_catalog"]
    assert catalog["first-source"]["retrieved_at"] == manifest["fetched_at"]
    assert catalog["second-source"]["retrieved_at"] == (
        "2026-10-01T17:52:40+00:00"
    )


@pytest.mark.parametrize("retrieved_at", [None, True, "not-a-timestamp"])
def test_external_catalog_rejects_invalid_per_source_retrieval_time(
        tmp_path, retrieved_at):
    source = tmp_path / "official.json"
    source.write_bytes(b'{"official":true}')
    work, *_ = _workspace(tmp_path / "workspace")
    _install_external_sources(work, [(source, "official-source")])
    manifest_path = work / "test-co_external" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["sources"][0]["retrieved_at"] = retrieved_at
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(ValueError, match="retrieval time"):
        resolve_trust.prepare(
            "test-co", work, _models(), tmp_path / "runs"
        )


@pytest.mark.parametrize(
    "retrieved_at",
    ["2026-09-26T17:16:10+00:00", "9999-01-01T00:00:00+00:00"],
)
def test_external_catalog_bounds_retrieval_against_fetch_and_capture(
        tmp_path, retrieved_at):
    source = tmp_path / "official.json"
    source.write_bytes(b'{"official":true}')
    work, *_ = _workspace(tmp_path / "workspace")
    _install_external_sources(work, [(source, "official-source")])
    manifest_path = work / "test-co_external" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["sources"][0]["retrieved_at"] = retrieved_at
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(ValueError, match="outside manifest fetch and capture"):
        resolve_trust.prepare(
            "test-co", work, _models(), tmp_path / "runs"
        )


def test_external_catalog_requires_retrieved_at_per_source(tmp_path):
    source = tmp_path / "official.json"
    source.write_bytes(b'{"official":true}')
    work, *_ = _workspace(tmp_path / "workspace")
    _install_external_sources(work, [(source, "official-source")])
    manifest_path = work / "test-co_external" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    del manifest["sources"][0]["retrieved_at"]
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(ValueError, match="source 0 is malformed"):
        resolve_trust.prepare(
            "test-co", work, _models(), tmp_path / "runs"
        )


def test_v2_external_evidence_persists_locator_and_freshness_metadata(tmp_path):
    primary = _decision(verdict="KEEP")
    challenger = _decision(verdict="DROP")
    arbiter = _decision(verdict="DROP", confidence="certain")
    evidence_path = tmp_path / "official-source.json"
    evidence_path.write_bytes(b'{"official":true}')
    external = _external(
        evidence_path, "official-source",
        ("serves", "no", "official inventory does not identify trail service"),
    )
    arbiter["serves"]["evidence"] += "; [external:official-source] confirms no service"

    run, work, draft, checkpoint, rows, packets, before = _prepared(
        tmp_path / "run", [primary],
        external_sources=[(evidence_path, "official-source")],
    )
    prepare = json.loads((run / "prepare.json").read_text())
    assigned = prepare["external_evidence_catalog"]["official-source"]
    _write_output(run, 1, "challenger", challenger)
    _write_output(run, 1, "arbiter", arbiter, [external])
    status, _ = resolve_trust.evaluate(run)
    assert status["state"] == "READY"
    assert resolve_trust.apply_chunk(run, 0, apply=True)["state"] == "APPLIED"
    resolved = json.loads(draft.read_text())[0]
    persisted = resolved["trust_resolution"]["arbiter"]["external_evidence"][0]
    assert "path" not in persisted
    for field in (
        "source_locator", "retrieved_at", "source_updated_at", "frozen_path",
        "manifest_sha256", "manifest_frozen_path", "metadata_sha256",
        "metadata_frozen_path",
    ):
        assert persisted[field] == assigned[field]
    assert (run / persisted["frozen_path"]).read_bytes() == evidence_path.read_bytes()

    tampered = copy.deepcopy(resolved)
    envelope = tampered["trust_resolution"]["arbiter"]
    envelope["external_evidence"][0]["source_locator"] = "http://insecure.example/data"
    body = dict(envelope)
    body.pop("envelope_sha256")
    envelope["envelope_sha256"] = tr.sha256_json(body)
    wrapper = tampered["trust_resolution"]
    body = dict(wrapper)
    body.pop("resolution_sha256")
    wrapper["resolution_sha256"] = tr.sha256_json(body)
    errors, _ = validate_verdict_row(tampered, packets[1])
    assert any("canonical HTTPS URL" in error for error in errors)

    tampered = copy.deepcopy(resolved)
    envelope = tampered["trust_resolution"]["arbiter"]
    envelope["external_evidence"][0]["source_updated_at"] = "2026-09-28T00:00:00+00:00"
    envelope["external_evidence"][0]["retrieved_at"] = "2026-09-27T00:00:00+00:00"
    body = dict(envelope)
    body.pop("envelope_sha256")
    envelope["envelope_sha256"] = tr.sha256_json(body)
    wrapper = tampered["trust_resolution"]
    body = dict(wrapper)
    body.pop("resolution_sha256")
    wrapper["resolution_sha256"] = tr.sha256_json(body)
    errors, _ = validate_verdict_row(tampered, packets[1])
    assert any("source update is after retrieval" in error for error in errors)


def test_ready_chunk_cannot_apply_while_any_sibling_is_pending_or_blocked(tmp_path):
    rows = [_decision(fid=1, verdict="KEEP"), _decision(fid=2, verdict="KEEP")]
    work, draft_zero, checkpoint_zero, rows, packets = _workspace(tmp_path, rows)

    def write_chunk(index, chunk_rows):
        draft = work / f"test-co_verdict_draft_{index:02d}.json"
        draft.write_text(json.dumps(chunk_rows, indent=1) + "\n")
        chunk_packets = [packets[row["fid"]] for row in chunk_rows]
        state = judge_packets.inspect_draft(chunk_packets, draft)
        decision_input, missing = judge_packets.decision_fingerprint(chunk_packets)
        assert state["status"] == "complete" and not missing
        checkpoint = work / f"test-co_checkpoint_{index:02d}.json"
        checkpoint.write_text(json.dumps(
            judge_packets.manifest_value(
                "test-co", index, chunk_packets, decision_input, state
            ), indent=1,
        ) + "\n")
        return draft, checkpoint

    draft_zero, checkpoint_zero = write_chunk(0, [rows[0]])
    draft_one, checkpoint_one = write_chunk(1, [rows[1]])
    before = {
        draft_zero: draft_zero.read_bytes(),
        checkpoint_zero: checkpoint_zero.read_bytes(),
        draft_one: draft_one.read_bytes(),
        checkpoint_one: checkpoint_one.read_bytes(),
    }
    run = resolve_trust.prepare("test-co", work, _models(), tmp_path / "runs")
    _write_output(run, 1, "challenger", copy.deepcopy(rows[0]))
    _write_output(run, 2, "challenger", _decision(fid=2, verdict="DROP"))

    status, _ = resolve_trust.evaluate(run)
    assert status["state"] == "PENDING"
    for apply in (False, True):
        result = resolve_trust.apply_chunk(run, 0, apply=apply)
        assert result["state"] == "PENDING"
        assert result["fids"] == [2]
    assert all(path.read_bytes() == content for path, content in before.items())
    assert not (run / "transactions" / "chunk-00.json").exists()

    _write_output(run, 2, "arbiter", _decision(
        fid=2, verdict="REVIEW", confidence="leaning"
    ))
    status, _ = resolve_trust.evaluate(run)
    assert status["state"] == "BLOCKED"
    result = resolve_trust.apply_chunk(run, 0, apply=True)
    assert result["state"] == "BLOCKED" and result["fids"] == [2]
    assert all(path.read_bytes() == content for path, content in before.items())

    _write_output(run, 2, "arbiter", _decision(
        fid=2, verdict="DROP", confidence="certain"
    ))
    assert resolve_trust.evaluate(run)[0]["state"] == "READY"
    assert resolve_trust.apply_chunk(run, 0, apply=True)["state"] == "APPLIED"
    assert draft_zero.read_bytes() != before[draft_zero]
    assert draft_one.read_bytes() == before[draft_one]
    assert resolve_trust.apply_chunk(run, 1, apply=True)["state"] == "APPLIED"
    assert draft_one.read_bytes() != before[draft_one]
    assert resolve_trust.evaluate(run)[0]["state"] == "APPLIED"


@pytest.mark.parametrize("defect", ["challenger", "call", "citation", "duplicate"])
def test_v2_external_evidence_rejects_unbound_or_misrepresented_support(
        tmp_path, defect):
    primary = _decision(verdict="KEEP")
    challenger = _decision(verdict="DROP")
    arbiter = _decision(verdict="DROP", confidence="certain")
    source = tmp_path / f"{defect}.json"
    source.write_bytes(b'{"official":true}')
    external = _external(
        source, f"official-{defect}",
        ("serves", "no", "official inventory does not identify trail service"),
    )
    arbiter["serves"]["evidence"] += f"; [external:official-{defect}] confirms no service"
    run, *_ = _prepared(
        tmp_path / "run", [primary],
        external_sources=[(source, f"official-{defect}")],
    )

    if defect == "challenger":
        challenger["serves"]["evidence"] += "; [external:official-challenger] extra"
        _write_output(run, 1, "challenger", challenger, [external])
    else:
        _write_output(run, 1, "challenger", challenger)
        if defect == "call":
            external["supports"][0]["call"] = "yes"
        elif defect == "citation":
            arbiter["serves"]["evidence"] = "Official inventory omits trail service"
        elif defect == "duplicate":
            external = [external, copy.deepcopy(external)]
        _write_output(
            run, 1, "arbiter", arbiter,
            external if isinstance(external, list) else [external],
        )

    status, _ = resolve_trust.evaluate(run)
    assert status["state"] == "BLOCKED"
    assert status["counts"]["invalid"] == 1
    joined = " ".join(status["items"][0]["errors"])
    expected = {
        "challenger": "challenger external_evidence must be empty",
        "call": "call does not match decision",
        "citation": "is not cited in serves evidence",
        "duplicate": "repeats host-assigned id",
    }
    assert expected[defect] in joined


def test_v2_challenger_undeclared_token_blocks_otherwise_clean_agreement(tmp_path):
    run, work, draft, checkpoint, rows, packets, before = _prepared(tmp_path)
    challenger = copy.deepcopy(rows[0])
    challenger["public"]["evidence"] += "; [external:undeclared-source]"
    _write_output(run, 1, "challenger", challenger)

    status, _ = resolve_trust.evaluate(run)
    assert status["state"] == "BLOCKED"
    joined = " ".join(status["items"][0]["errors"])
    assert "challenger external citations must be absent" in joined
    assert "undeclared external source 'undeclared-source'" in joined
    assert draft.read_bytes() == before[draft]


def test_v2_rejects_external_token_outside_axis_evidence(tmp_path):
    run, work, draft, checkpoint, rows, packets, before = _prepared(tmp_path)
    challenger = copy.deepcopy(rows[0])
    challenger["tags_cited"]["source"] = "[external:undeclared-source]"
    _write_output(run, 1, "challenger", challenger)

    status, _ = resolve_trust.evaluate(run)
    assert status["state"] == "BLOCKED"
    assert any("external reference is outside axis evidence at tags_cited.source" in error
               for error in status["items"][0]["errors"])


def _arbiter_external_case(tmp_path, evidence_ids=("official-source",)):
    primary = _decision(verdict="KEEP")
    challenger = _decision(verdict="DROP")
    arbiter = _decision(verdict="DROP", confidence="certain")
    sources = {}
    for index, evidence_id in enumerate(evidence_ids):
        source = tmp_path / f"{evidence_id}.json"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(json.dumps({"official": index}).encode())
        sources[evidence_id] = source
    run, *_ = _prepared(
        tmp_path / "run", [primary],
        external_sources=[(path, evidence_id) for evidence_id, path in sources.items()],
    )
    _write_output(run, 1, "challenger", challenger)
    return run, arbiter, sources


@pytest.mark.parametrize(
    ("defect", "evidence_suffix", "expected"),
    [
        (
            "extra",
            "; [external:official-source] confirms no service; "
            "[external:undeclared-source] claims more",
            "undeclared external source 'undeclared-source'",
        ),
        (
            "wrong-axis",
            "",
            "is cited in public evidence but declared for axes ['serves']",
        ),
        ("missing", "", "is not cited in serves evidence"),
        (
            "duplicate",
            "; [external:official-source] and [EXTERNAL:Official-Source]",
            "repeats external citation 'official-source' after normalization",
        ),
        (
            "whitespace",
            "; [external: official-source]",
            "contains malformed external reference",
        ),
        ("empty", "; [external:]", "contains malformed external reference"),
        ("bad-character", "; [external:official!source]", "contains malformed external reference"),
        ("unclosed", "; [external:official-source", "contains unclosed external reference"),
    ],
)
def test_v2_arbiter_external_tokens_are_exact_axis_local_and_canonical(
        tmp_path, defect, evidence_suffix, expected):
    run, arbiter, sources = _arbiter_external_case(tmp_path)
    external = _external(
        sources["official-source"], "official-source",
        ("serves", "no", "official inventory does not identify trail service"),
    )
    if defect == "wrong-axis":
        arbiter["public"]["evidence"] += "; [external:official-source]"
    else:
        arbiter["serves"]["evidence"] += evidence_suffix
    _write_output(run, 1, "arbiter", arbiter, [external])

    status, _ = resolve_trust.evaluate(run)
    assert status["state"] == "BLOCKED"
    assert expected in " ".join(status["items"][0]["errors"])


def test_v2_arbiter_accepts_multi_axis_multi_source_exact_closure(tmp_path):
    run, arbiter, sources = _arbiter_external_case(
        tmp_path, ("official-a", "official-b"),
    )
    arbiter["public"]["evidence"] += "; [EXTERNAL:Official-A] confirms public access"
    arbiter["serves"]["evidence"] += "; [external:official-a] and official map"
    arbiter["exists"]["evidence"] += "; [external:official-b] confirms the lot"
    external_a = _external(
        sources["official-a"], "official-a",
        ("public", "yes", "official record permits public parking"),
        ("serves", "no", "official map omits a trail connection"),
    )
    external_b = _external(
        sources["official-b"], "official-b",
        ("exists", "yes", "official inventory lists the lot"),
    )
    _write_output(run, 1, "arbiter", arbiter, [external_a, external_b])

    status, _ = resolve_trust.evaluate(run)
    assert status["state"] == "READY"
    assert status["items"][0]["result"] == "ARBITRATED_AGREEMENT"
    assert status["items"][0]["selected_role"] == "arbiter"


def test_v2_arbiter_rejects_duplicate_support_for_source_axis(tmp_path):
    run, arbiter, sources = _arbiter_external_case(tmp_path)
    arbiter["serves"]["evidence"] += "; [external:official-source] confirms no service"
    external = _external(
        sources["official-source"], "official-source",
        ("serves", "no", "official inventory omits trail service"),
        ("serves", "yes", "conflicting duplicate claim"),
    )
    _write_output(run, 1, "arbiter", arbiter, [external])

    status, _ = resolve_trust.evaluate(run)
    joined = " ".join(status["items"][0]["errors"])
    assert status["state"] == "BLOCKED"
    assert "repeats support for axis 'serves'" in joined
    assert "call does not match decision" in joined


@pytest.mark.parametrize("url_text", [
    "http://agent.example/source",
    "https://agent.example/source",
    "www.agent.example/source",
])
def test_v2_challenger_rejects_url_like_decision_prose(tmp_path, url_text):
    run, work, draft, checkpoint, rows, packets, before = _prepared(tmp_path)
    challenger = copy.deepcopy(rows[0])
    challenger["public"]["evidence"] += f"; agent lookup {url_text}"
    _write_output(run, 1, "challenger", challenger)

    status, _ = resolve_trust.evaluate(run)
    assert status["state"] == "BLOCKED"
    assert any("contains an agent-authored URL-like locator" in error
               for error in status["items"][0]["errors"])


def test_v2_arbiter_rejects_external_token_in_support_claim(tmp_path):
    run, arbiter, sources = _arbiter_external_case(tmp_path)
    arbiter["serves"]["evidence"] += "; [external:official-source] confirms no service"
    external = _external(
        sources["official-source"], "official-source",
        ("serves", "no", "Claim repeats [external:official-source] outside axis evidence"),
    )
    _write_output(run, 1, "arbiter", arbiter, [external])

    status, _ = resolve_trust.evaluate(run)
    joined = " ".join(status["items"][0]["errors"])
    assert status["state"] == "BLOCKED"
    assert "external reference is outside axis evidence at external_evidence.0.supports.0.claim" in joined


def test_v2_arbiter_rejects_url_like_support_claim_but_not_host_locator(tmp_path):
    run, arbiter, sources = _arbiter_external_case(tmp_path)
    arbiter["serves"]["evidence"] += "; [external:official-source] confirms no service"
    external = _external(
        sources["official-source"], "official-source",
        ("serves", "no", "See www.agent-authored.example/source"),
    )
    _write_output(run, 1, "arbiter", arbiter, [external])

    status, _ = resolve_trust.evaluate(run)
    joined = " ".join(status["items"][0]["errors"])
    assert status["state"] == "BLOCKED"
    assert "claim contains an agent-authored URL-like locator" in joined
    assert "source_locator is not a canonical HTTPS URL" not in joined


@pytest.mark.parametrize("bad_version", [True, 1.0])
def test_persisted_versions_reject_boolean_and_float_downgrades(tmp_path, bad_version):
    run, work, draft, checkpoint, rows, packets, before = _prepared(tmp_path)
    _write_output(run, 1, "challenger", copy.deepcopy(rows[0]))
    assert resolve_trust.apply_chunk(run, 0, apply=True)["state"] == "APPLIED"
    tampered = json.loads(draft.read_text())[0]
    wrapper = tampered["trust_resolution"]
    wrapper["version"] = bad_version
    wrapper["policy_id"] = tr.LEGACY_POLICY_ID
    for role in tr.ROLES:
        envelope = wrapper.get(role)
        if envelope is None:
            continue
        envelope["version"] = bad_version
        body = dict(envelope)
        body.pop("envelope_sha256")
        envelope["envelope_sha256"] = tr.sha256_json(body)
    body = dict(wrapper)
    body.pop("resolution_sha256")
    wrapper["resolution_sha256"] = tr.sha256_json(body)
    errors, _ = validate_verdict_row(tampered, packets[1])
    assert any("version/policy mismatch" in error for error in errors)
    assert any("envelope version" in error for error in errors)


def test_plain_agent_decision_cannot_inject_host_owned_source(tmp_path):
    run, work, draft, checkpoint, rows, packets, before = _prepared(tmp_path)
    forged = copy.deepcopy(rows[0])
    forged["src"] = "user-forged"
    _write_output(run, 1, "challenger", forged)
    status, _ = resolve_trust.evaluate(run)
    assert status["state"] == "BLOCKED"
    assert status["counts"]["invalid"] == 1
    assert any("plain decision contains forbidden keys ['src']" in error
               for error in status["items"][0]["errors"])
    assert draft.read_bytes() == before[draft]


def test_same_inputs_under_different_run_roots_have_same_portable_run_identity(
        tmp_path):
    work, draft, checkpoint, rows, packets = _workspace(tmp_path)
    run_a = resolve_trust.prepare("test-co", work, _models(), tmp_path / "runs-a")
    run_b = resolve_trust.prepare("test-co", work, _models(), tmp_path / "runs-b")
    assert run_a.name == run_b.name
    prepare_a = (run_a / "prepare.json").read_bytes()
    prepare_b = (run_b / "prepare.json").read_bytes()
    assert prepare_a == prepare_b
    document = json.loads(prepare_a)
    assert document["version"] == resolve_trust.PREPARE_VERSION
    assert document["path_scheme"] == resolve_trust.PREPARE_PATH_SCHEME
    assert "tmp" not in document and "run_root" not in document
    for item in document["items"]:
        packet = (run_a / item["packet_path"]).read_bytes()
        assert str(tmp_path).encode() not in packet
        for assignment in item["assignments"].values():
            assert (run_a / assignment["prompt_path"]).read_bytes() == (
                run_b / assignment["prompt_path"]
            ).read_bytes()
            assert str(tmp_path).encode() not in (
                run_a / assignment["prompt_path"]
            ).read_bytes()


def test_prepare_v2_v3_historical_identity_remains_path_bound(tmp_path):
    work, *_ = _workspace(tmp_path)
    run = resolve_trust.prepare("test-co", work, _models(), tmp_path / "runs")
    portable = json.loads((run / "prepare.json").read_text())
    identities = []
    for root in (tmp_path / "historical-a", tmp_path / "historical-b"):
        historical = copy.deepcopy(portable)
        historical["version"] = resolve_trust.PATH_BOUND_PREPARE_VERSION
        historical.pop("path_scheme")
        historical["tmp"] = str(work.resolve())
        historical["run_root"] = str(root.resolve())
        identities.append(resolve_trust._run_identity(historical))
    assert identities[0] != identities[1]


def test_host_evidence_is_retained_and_agent_metadata_is_not_accepted(tmp_path):
    evidence = tmp_path / "official.json"
    evidence.write_bytes(b'{"record":"official"}')
    run, work, draft, checkpoint, rows, packets, before = _prepared(
        tmp_path / "case", external_sources=[(evidence, "official-record")]
    )
    prepare = json.loads((run / "prepare.json").read_text())
    catalog = prepare["external_evidence_catalog"]["official-record"]
    assert (run / catalog["frozen_path"]).read_bytes() == evidence.read_bytes()
    assert (run / catalog["manifest_frozen_path"]).is_file()
    assert (run / catalog["metadata_frozen_path"]).is_file()
    arbiter_prompt = run / prepare["items"][0]["assignments"]["arbiter"]["prompt_path"]
    prompt_text = arbiter_prompt.read_text()
    assert "evidence/catalog.json" in prompt_text
    assert str(run.resolve()) not in prompt_text

    source_pack = work / "test-co_external"
    archived_pack = work / "Archive" / "test-co_external"
    archived_pack.parent.mkdir()
    source_pack.rename(archived_pack)
    assert resolve_trust.evaluate(run)[0]["state"] == "PENDING"

    decision = copy.deepcopy(rows[0])
    decision["public"]["evidence"] += "; [external:unassigned] claim"
    _write_output(run, 1, "arbiter", decision, [{
        "id": "unassigned",
        "supports": [{"axis": "public", "call": "yes", "claim": "claim"}],
    }])
    status, _ = resolve_trust.evaluate(run)
    assert status["state"] == "BLOCKED"
    assert any("is not host-assigned" in error
               for error in status["items"][0]["errors"])


def test_output_seal_ignores_later_live_inbox_mutation(tmp_path):
    run, work, draft, checkpoint, rows, packets, before = _prepared(tmp_path)
    output = _write_output(run, 1, "challenger", copy.deepcopy(rows[0]))
    assert resolve_trust.apply_chunk(run, 0, apply=True)["state"] == "APPLIED"
    seal = json.loads((run / "output-seal.json").read_text())
    assert seal["seal_sha256"]
    sealed_path = run / "sealed-inbox" / output.name
    sealed_before = sealed_path.read_bytes()
    output.write_text('{"forged":"after-seal"}')
    status, _ = resolve_trust.evaluate(run)
    assert status["state"] == "APPLIED"
    assert sealed_path.read_bytes() == sealed_before


def test_malformed_prepare_and_output_seal_types_fail_structurally(tmp_path):
    run, work, draft, checkpoint, rows, packets, before = _prepared(tmp_path)
    prepare_path = run / "prepare.json"
    valid = json.loads(prepare_path.read_text())
    malformed = copy.deepcopy(valid)
    malformed["items"][0]["input_evidence_sha256"] = [{}]
    with pytest.raises(ValueError, match="input evidence hashes are invalid"):
        resolve_trust.validate_prepare_document(malformed)

    _write_output(run, 1, "challenger", copy.deepcopy(rows[0]))
    assert resolve_trust.apply_chunk(run, 0, apply=True)["state"] == "APPLIED"
    seal_path = run / "output-seal.json"
    seal = json.loads(seal_path.read_text())
    first = next(iter(seal["outputs"]))
    seal["outputs"][first] = {}
    body = dict(seal)
    body.pop("seal_sha256")
    seal["seal_sha256"] = tr.sha256_json(body)
    seal_path.write_text(json.dumps(seal))
    status, _ = resolve_trust.evaluate(run)
    assert status["state"] == "RECOVERY_REQUIRED"
    assert any("output seal invalid" in error for error in status["errors"])


def test_same_family_parents_cannot_be_rescued_by_a_distinct_arbiter():
    primary = {
        "decision": _decision(verdict="KEEP"),
        "model": tr.model_config("primary", "shared-family"),
    }
    challenger = {
        "decision": _decision(verdict="KEEP"),
        "model": tr.model_config("challenger", "shared-family"),
    }
    arbiter = {
        "decision": _decision(verdict="KEEP", confidence="certain"),
        "model": tr.model_config("arbiter", "independent-family"),
    }
    result = tr.resolve(trust_engine.ROUTE_CHALLENGE, primary, challenger, arbiter)
    assert result["state"] == "human_exception"
    assert "duplicate model id or family" in result["reason"]


def test_terminal_run_binds_publication_dossier_and_judge_set(tmp_path, monkeypatch, capsys):
    run, work, draft, checkpoint, rows, packets, before = _prepared(tmp_path)
    _write_output(run, 1, "challenger", copy.deepcopy(rows[0]))
    assert resolve_trust.apply_chunk(run, 0, apply=True)["state"] == "APPLIED"
    dossier = work / "test-co_dossier.json"
    changed = json.loads(dossier.read_text())
    changed["facilities"][0]["lat"] += 1.0
    dossier.write_text(json.dumps(changed))
    store = tmp_path / "store.json"
    monkeypatch.setattr(merge_drafts, "PADJ_TMP", str(work))
    assert merge_drafts.main([
        str(store), "test-co", "--resolver-run", str(run),
        "--judged", "2026-09-29", "--write",
    ]) == 1
    assert "source generation validation failed" in capsys.readouterr().err
    assert not store.exists()


def test_public_validators_are_total_over_nested_json_types(tmp_path):
    row = _decision()
    row["trust_resolution"] = [1]
    errors, _ = validate_verdict_row(row)
    assert errors
    assert tr._source_locator("https://[") is False
    malformed_provenance = _decision()
    malformed_provenance["trust_resolution"] = {
        "version": tr.VERSION, "resolution_sha256": "a" * 64,
    }
    malformed_provenance["judge_provenance"] = [1]
    assert current_authority(malformed_provenance) == (
        "machine_v2", "a" * 64, None,
    )

    run, work, draft, checkpoint, rows, packets, before = _prepared(tmp_path)
    output = _write_output(run, 1, "challenger", copy.deepcopy(rows[0]))
    raw = json.loads(output.read_text())
    raw["external_evidence"] = [{"id": {}, "supports": []}]
    output.write_text(json.dumps(raw))
    status, _ = resolve_trust.evaluate(run)
    assert status["state"] == "BLOCKED"
    assert status["counts"]["invalid"] == 1


def _persisted_resolution_control(tmp_path, version):
    packet = _packet(tmp_path)
    decision = _decision()
    packet_sha = tr.packet_sha256(packet)
    normative = {
        name: tr.sha256_json({"rule": name})
        for name in tr.NORMATIVE_DOCUMENT_NAMES
    }
    envelopes = {}
    for role, model in _models().items():
        assignment = {
            "assignment_id": tr.sha256_json({"assignment": role}),
            "model": model,
            "packet_sha256": packet_sha,
            "prompt_sha256": tr.sha256_json({"prompt": role}),
            "external_evidence_catalog_sha256": tr.sha256_json({"catalog": role}),
            "normative_document_sha256": normative,
            "input_evidence_sha256": [tr.sha256_json({"input": role})],
        }
        envelopes[role] = tr.make_envelope(role, assignment, decision)
    resolution = tr.resolve(
        trust_engine.ROUTE_CHALLENGE,
        envelopes["primary"], envelopes["challenger"], None,
        policy_version=version,
    )
    row = tr.make_resolved_row(
        decision, trust_engine.ROUTE_CHALLENGE, resolution,
        {"primary": envelopes["primary"], "challenger": envelopes["challenger"],
         "arbiter": None},
        tr.sha256_json({"run": version}),
        tr.sha256_json({"transaction": version}),
    )
    if version == tr.LEGACY_VERSION:
        wrapper = row["trust_resolution"]
        wrapper["version"] = tr.LEGACY_VERSION
        wrapper["policy_id"] = tr.LEGACY_POLICY_ID
        for role in tr.ROLES:
            envelope = wrapper.get(role)
            if not isinstance(envelope, dict):
                continue
            envelope["version"] = tr.LEGACY_VERSION
            envelope.pop("normative_document_sha256")
            envelope.pop("external_evidence_catalog_sha256")
            body = dict(envelope)
            body.pop("envelope_sha256")
            envelope["envelope_sha256"] = tr.sha256_json(body)
        body = dict(wrapper)
        body.pop("resolution_sha256")
        wrapper["resolution_sha256"] = tr.sha256_json(body)
    return row, packet


def _rehash_persisted_resolution(row):
    wrapper = row["trust_resolution"]
    for role in tr.ROLES:
        envelope = wrapper.get(role)
        if not isinstance(envelope, dict):
            continue
        body = dict(envelope)
        body.pop("envelope_sha256", None)
        envelope["envelope_sha256"] = tr.sha256_json(body)
    body = dict(wrapper)
    body.pop("resolution_sha256", None)
    wrapper["resolution_sha256"] = tr.sha256_json(body)


def _coherently_rebind_selected_decision(row):
    wrapper = row["trust_resolution"]
    envelope = wrapper[wrapper["selected_role"]]
    decision = envelope["decision"]
    envelope["decision_sha256"] = tr.decision_sha256(decision)
    envelope["evidence_sha256"] = tr.evidence_sha256(decision)
    wrapper["selected_decision_sha256"] = envelope["decision_sha256"]
    for field in tr.DECISION_FIELDS:
        row.pop(field, None)
    row.update(copy.deepcopy(decision))
    row["judge_provenance"] = tr.expected_judge_provenance(envelope)
    _rehash_persisted_resolution(row)


def _core_resolution_errors(row, packet):
    return tr.validate_persisted_resolution(
        row, packet,
        lambda decision: validate_verdict_row(decision, packet)[0],
        expected_packet_sha256=tr.packet_sha256(packet),
    )


@pytest.mark.parametrize("version", [tr.LEGACY_VERSION, tr.VERSION])
def test_persisted_resolution_valid_v1_v2_controls_replay(tmp_path, version):
    row, packet = _persisted_resolution_control(tmp_path, version)
    assert _core_resolution_errors(row, packet) == []
    assert validate_verdict_row(row, packet)[0] == []

    wrapper = row["trust_resolution"]
    wrapper["result"] = "ARBITRATED_AGREEMENT"
    wrapper["correlation"]["basis"] = wrapper["result"]
    _rehash_persisted_resolution(row)
    errors = _core_resolution_errors(row, packet)
    assert "persisted consensus does not replay to the stored result" in errors


@pytest.mark.parametrize(
    ("violation", "expected"),
    [
        ("[external:undeclared-source]", "undeclared external source"),
        ("https://agent-authored.example/source", "agent-authored URL-like locator"),
    ],
)
@pytest.mark.parametrize("version", [tr.LEGACY_VERSION, tr.VERSION])
def test_persisted_v2_semantic_closure_survives_coherent_rehash_with_v1_control(
        tmp_path, version, violation, expected):
    row, packet = _persisted_resolution_control(tmp_path, version)
    selected = row["trust_resolution"]["challenger"]["decision"]
    selected["public"]["evidence"] += f"; {violation}"
    _coherently_rebind_selected_decision(row)

    errors = _core_resolution_errors(row, packet)
    if version == tr.VERSION:
        assert any(expected in error for error in errors)
        assert not any("hash mismatch" in error for error in errors)
    else:
        assert errors == []


@pytest.mark.parametrize("version", [tr.LEGACY_VERSION, tr.VERSION])
@pytest.mark.parametrize(
    ("case", "malformed", "expected_error"),
    [
        ("primary-envelope", [1], "primary envelope is not an object"),
        ("selected-envelope", "bad", "challenger envelope is not an object"),
        ("primary-model", [1], "primary model identity is malformed"),
        ("selected-model", True, "challenger model identity is malformed"),
        ("primary-family", [], "primary model.family is noncanonical"),
        ("selected-family", {}, "challenger model.family is noncanonical"),
        ("primary-model-id", {}, "primary model.id is noncanonical"),
        ("selected-model-id", [], "challenger model.id is noncanonical"),
        ("packet-hashes", [], "packet_sha256 is not a lowercase SHA-256"),
        ("packet-hashes", {}, "packet_sha256 is not a lowercase SHA-256"),
        ("selected-role", [], "trust_resolution selected_role is invalid"),
        ("selected-role", {}, "trust_resolution selected_role is invalid"),
        ("selected-decision", [1], "challenger decision is not an object"),
        ("selected-decision", "bad", "challenger decision is not an object"),
        ("correlation", [1], "trust_resolution correlation is not an object"),
    ],
)
def test_persisted_resolution_nested_json_totality_matrix(
        tmp_path, version, case, malformed, expected_error):
    row, packet = _persisted_resolution_control(tmp_path, version)
    wrapper = row["trust_resolution"]
    malformed = copy.deepcopy(malformed)
    if case == "primary-envelope":
        wrapper["primary"] = malformed
    elif case == "selected-envelope":
        wrapper["challenger"] = malformed
    elif case == "primary-model":
        wrapper["primary"]["model"] = malformed
    elif case == "selected-model":
        wrapper["challenger"]["model"] = malformed
    elif case == "primary-family":
        wrapper["primary"]["model"]["family"] = malformed
    elif case == "selected-family":
        wrapper["challenger"]["model"]["family"] = malformed
    elif case == "primary-model-id":
        wrapper["primary"]["model"]["id"] = malformed
    elif case == "selected-model-id":
        wrapper["challenger"]["model"]["id"] = malformed
    elif case == "packet-hashes":
        wrapper["primary"]["packet_sha256"] = copy.deepcopy(malformed)
        wrapper["challenger"]["packet_sha256"] = copy.deepcopy(malformed)
    elif case == "selected-role":
        wrapper["selected_role"] = malformed
    elif case == "selected-decision":
        wrapper["challenger"]["decision"] = malformed
    elif case == "correlation":
        wrapper["correlation"] = malformed
    else:  # pragma: no cover - the parameter table is closed above
        raise AssertionError(case)
    _rehash_persisted_resolution(row)

    core_errors = _core_resolution_errors(row, packet)
    public_errors, _ = validate_verdict_row(row, packet)
    for errors in (core_errors, public_errors):
        assert errors
        assert any(expected_error in error for error in errors)
        assert not any("wrapper hash mismatch" in error for error in errors)
    assert tr.family(wrapper.get("primary")) in (None, "family-a")
    assert tr.model_id(wrapper.get("challenger")) in (None, "challenger-model")


def test_persisted_resolution_validator_rejects_non_object_roots_without_raising():
    validate_plain = lambda decision: []
    assert tr.validate_persisted_resolution([], {}, validate_plain) == [
        "machine row must be an object"
    ]
    assert tr.validate_persisted_resolution({"trust_resolution": {}}, [], validate_plain) == [
        "packet must be an object"
    ]
    for value in (None, True, [], {"model": []}, {"model": True}):
        assert tr.family(value) is None
        assert tr.model_id(value) is None


def test_capture_packet_reads_each_stable_tile_once_with_secure_open_flags(
        tmp_path, monkeypatch):
    work, draft, checkpoint, rows, packets = _workspace(tmp_path)
    packet = packets[1]
    expected = {
        zoom: Path(path).read_bytes()
        for zoom, path in packet["tiles"].items()
    }
    real_open = resolve_trust.os.open
    open_calls = []

    def tracked_open(path, flags, *args, **kwargs):
        open_calls.append((path, flags, kwargs.get("dir_fd")))
        return real_open(path, flags, *args, **kwargs)

    real_read = resolve_trust._read_fd_bytes
    reads = []

    def counted_read(fd):
        reads.append(fd)
        return real_read(fd)

    monkeypatch.setattr(resolve_trust.os, "open", tracked_open)
    monkeypatch.setattr(resolve_trust, "_read_fd_bytes", counted_read)
    payload, hashes, captured = resolve_trust._capture_packet(
        packet, work, "test-co"
    )

    assert "tiles" not in payload
    assert captured == expected
    assert hashes == {zoom: hashlib.sha256(data).hexdigest()
                      for zoom, data in expected.items()}
    assert len(reads) == 3
    required = resolve_trust.os.O_NOFOLLOW | getattr(
        resolve_trust.os, "O_CLOEXEC", 0
    )
    root_calls = [call for call in open_calls if call[2] is None]
    tile_calls = [call for call in open_calls if call[2] is not None]
    assert len(root_calls) == 1 and len(tile_calls) == 3
    assert root_calls[0][1] & required == required
    assert root_calls[0][1] & resolve_trust.os.O_DIRECTORY
    assert all(flags & required == required for _path, flags, _dir_fd in tile_calls)


@pytest.mark.parametrize(
    "drift", ["replacement", "same-inode-equal-length", "link-count", "size", "mode"]
)
def test_capture_packet_rejects_source_tile_drift(tmp_path, monkeypatch, drift):
    work, draft, checkpoint, rows, packets = _workspace(tmp_path)
    packet = packets[1]
    source = Path(packet["tiles"]["z1"])
    real_read = resolve_trust._read_fd_bytes
    mutated = False

    def mutate_after_read(fd):
        nonlocal mutated
        data = real_read(fd)
        if mutated:
            return data
        before = source.stat()
        if drift == "replacement":
            attacker = source.with_name("replacement.png")
            attacker.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * (before.st_size - 8))
            resolve_trust.os.replace(attacker, source)
        elif drift == "same-inode-equal-length":
            replacement = b"\x89PNG\r\n\x1a\n" + b"y" * (before.st_size - 8)
            with source.open("r+b") as stream:
                stream.write(replacement)
                stream.truncate()
                stream.flush()
                resolve_trust.os.fsync(stream.fileno())
            changed = source.stat()
            resolve_trust.os.utime(
                source, ns=(changed.st_atime_ns, before.st_mtime_ns)
            )
            after = source.stat()
            assert (after.st_ino, after.st_size, after.st_mode, after.st_nlink,
                    after.st_mtime_ns) == (
                before.st_ino, before.st_size, before.st_mode, before.st_nlink,
                before.st_mtime_ns,
            )
            assert after.st_ctime_ns != before.st_ctime_ns
        elif drift == "link-count":
            resolve_trust.os.link(source, source.with_name("hardlink.png"))
            assert source.stat().st_nlink == before.st_nlink + 1
        elif drift == "size":
            with source.open("ab") as stream:
                stream.write(b"x")
                stream.flush()
                resolve_trust.os.fsync(stream.fileno())
        elif drift == "mode":
            resolve_trust.os.chmod(
                source, before.st_mode ^ resolve_trust.stat.S_IXUSR
            )
        mutated = True
        return data

    monkeypatch.setattr(resolve_trust, "_read_fd_bytes", mutate_after_read)
    with pytest.raises(ValueError, match="changed during secure capture"):
        resolve_trust._capture_packet(packet, work, "test-co")
    assert mutated


def test_prepare_uses_only_captured_tile_bytes_after_stable_capture(
        tmp_path, monkeypatch):
    work, draft, checkpoint, rows, packets = _workspace(tmp_path)
    expected = {
        zoom: Path(path).read_bytes()
        for zoom, path in packets[1]["tiles"].items()
    }
    real_capture = resolve_trust._capture_packet
    replaced = False

    def capture_then_replace(packet, tmp, slug):
        nonlocal replaced
        capture = real_capture(packet, tmp, slug)
        if not replaced:
            for path in packet["tiles"].values():
                Path(path).write_bytes(b"source changed after stable capture")
            replaced = True
        return capture

    monkeypatch.setattr(resolve_trust, "_capture_packet", capture_then_replace)
    run = resolve_trust.prepare(
        "test-co", work, _models(), tmp_path / "runs"
    )
    prepare = json.loads((run / "prepare.json").read_text())
    frozen_packet = json.loads(
        (run / prepare["items"][0]["packet_path"]).read_text()
    )
    assert replaced
    assert {
        zoom: (run / path).read_bytes()
        for zoom, path in frozen_packet["tiles"].items()
    } == expected


def test_area_and_run_artifact_paths_reject_traversal_and_symlinks(tmp_path):
    with pytest.raises(ValueError, match="canonical slug"):
        tr.area_lock_path(tmp_path, "a/../../outside")
    with pytest.raises(ValueError, match="separator-free area slug"):
        merge_drafts.authority_receipt_path(
            tmp_path, "a/../../outside", "a" * 64
        )

    run, work, draft, checkpoint, rows, packets, before = _prepared(tmp_path / "case")
    _write_output(run, 1, "challenger", copy.deepcopy(rows[0]))
    outside = tmp_path / "outside-sealed"
    outside.mkdir()
    (run / "sealed-inbox").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        resolve_trust.apply_chunk(run, 0, apply=True)
    assert draft.read_bytes() == before[draft]


def test_prepare_freezes_tiles_and_ignores_later_source_tile_mutation(tmp_path):
    run, work, draft, checkpoint, rows, packets, before = _prepared(tmp_path)
    prepare = json.loads((run / "prepare.json").read_text())
    frozen_packet = json.loads(
        (run / prepare["items"][0]["packet_path"]).read_text()
    )
    frozen_z1 = run / frozen_packet["tiles"]["z1"]
    frozen_before = frozen_z1.read_bytes()
    Path(packets[1]["tiles"]["z1"]).write_bytes(b"changed source after prepare")
    _write_output(run, 1, "challenger", copy.deepcopy(rows[0]))
    status, _ = resolve_trust.evaluate(run)
    assert status["state"] == "READY"
    assert frozen_z1.read_bytes() == frozen_before
    assert frozen_z1.is_relative_to(run)


def test_prepare_rejects_symlinked_or_out_of_ladder_tiles(tmp_path):
    work, draft, checkpoint, rows, packets = _workspace(tmp_path)
    real = Path(packets[1]["tiles"]["z1"])
    outside = tmp_path / "outside.png"
    outside.write_bytes(real.read_bytes())
    symlink = real.with_name("symlink.png")
    symlink.symlink_to(outside)
    packets[1]["tiles"]["z1"] = str(symlink)
    (work / "test-co_packets.json").write_text(json.dumps({"1": packets[1]}))
    with pytest.raises(ValueError, match="symlink"):
        resolve_trust.prepare("test-co", work, _models(), tmp_path / "runs")


@pytest.mark.parametrize(
    ("injected", "expected"),
    [
        ("[external:proof-rogue]", "undeclared external source 'proof-rogue'"),
        ("https://agent-authored.example/proof", "agent-authored URL-like locator"),
    ],
)
def test_publication_proof_envelope_reconstruction_inherits_v2_text_closure(
        tmp_path, injected, expected):
    run, work, draft, checkpoint, rows, packets, before = _prepared(tmp_path)
    prepare = json.loads((run / "prepare.json").read_text())
    item = prepare["items"][0]
    assignment = item["assignments"]["challenger"]
    decision = copy.deepcopy(rows[0])
    decision["public"]["evidence"] += f"; {injected}"
    raw = {
        "assignment_id": assignment["assignment_id"],
        "decision": decision,
        "external_evidence": [],
    }
    sealed_outputs = {
        assignment["output_path"]: base64.b64encode(
            json.dumps(raw, sort_keys=True).encode()
        ).decode("ascii")
    }

    envelope, errors = resolve_trust.replay_trust._reconstruct_proof_envelope(
        prepare, item, "challenger", sealed_outputs, packets[1],
    )
    assert envelope is not None
    assert any(expected in error for error in errors)


def test_publication_proof_reconstructs_assignment_from_sealed_output(tmp_path, monkeypatch):
    run, work, draft, checkpoint, rows, packets, before = _prepared(tmp_path)
    _write_output(run, 1, "challenger", copy.deepcopy(rows[0]))
    assert resolve_trust.apply_chunk(run, 0, apply=True)["state"] == "APPLIED"
    store = tmp_path / "store.json"
    monkeypatch.setattr(merge_drafts, "PADJ_TMP", str(work))
    assert merge_drafts.main([
        str(store), "test-co", "--resolver-run", str(run),
        "--judged", "2026-09-29", "--write",
    ]) == 0
    row = json.loads(store.read_text())["way/1"]
    registry = resolve_trust.replay_trust._load_publication_proofs(
        store.parent, store.name
    )
    old_proof_sha = row["publication_attestation"]["publication_proof_sha256"]
    manifest = copy.deepcopy(resolve_trust.replay_trust._load_proof_manifest(
        old_proof_sha, registry
    ))

    envelope = row["trust_resolution"]["challenger"]
    envelope["assignment_id"] = "f" * 64
    body = dict(envelope)
    body.pop("envelope_sha256")
    envelope["envelope_sha256"] = tr.sha256_json(body)
    wrapper = row["trust_resolution"]
    body = dict(wrapper)
    body.pop("resolution_sha256")
    wrapper["resolution_sha256"] = tr.sha256_json(body)
    manifest["final_rows"]["1"] = (
        resolve_trust.replay_trust._proof_decision_row(row)
    )
    raw = resolve_trust._json_bytes(manifest)
    logical, stages = merge_drafts.publication_objects.stage_logical_object(
        (raw,), tmp_path / "forged-proof-stages"
    )
    merge_drafts.publication_objects.install_staged_objects(
        registry.data_root, stages
    )
    value = copy.deepcopy(dict(registry))
    value["proofs"][logical.sha256] = logical.leaves[0].to_dict()
    forged_registry = merge_drafts.publication_objects.PublicationProofRegistry(
        value, registry.data_root
    )
    attestation = row["publication_attestation"]
    attestation["authority_sha256"] = wrapper["resolution_sha256"]
    attestation["publication_proof_sha256"] = logical.sha256
    body = dict(attestation)
    body.pop("attestation_sha256")
    attestation["attestation_sha256"] = tr.sha256_json(body)

    errors = resolve_trust.replay_trust._validate_publication_proof(
        row, forged_registry
    )
    assert any("persisted resolution differs from sealed outputs" in error
               for error in errors)


def _forge_v4_manifest(row, registry, manifest, stage_root):
    raw = resolve_trust._json_bytes(manifest)
    logical, stages = merge_drafts.publication_objects.stage_logical_object(
        (raw,), stage_root
    )
    merge_drafts.publication_objects.install_staged_objects(
        registry.data_root, stages
    )
    value = copy.deepcopy(dict(registry))
    value["proofs"][logical.sha256] = logical.leaves[0].to_dict()
    forged_registry = merge_drafts.publication_objects.PublicationProofRegistry(
        value, registry.data_root
    )
    attestation = row["publication_attestation"]
    attestation["publication_proof_sha256"] = logical.sha256
    body = dict(attestation)
    body.pop("attestation_sha256", None)
    attestation["attestation_sha256"] = tr.sha256_json(body)
    return forged_registry


def _stage_replacement_object(manifest, registry, old_ref, raw, stage_root):
    logical, stages = merge_drafts.publication_objects.stage_logical_object(
        (raw,), stage_root
    )
    merge_drafts.publication_objects.install_staged_objects(
        registry.data_root, stages
    )
    manifest["objects"].pop(old_ref)
    manifest["objects"][logical.sha256] = logical.to_dict()
    return logical.sha256


def test_out_of_ladder_tile_is_rejected_before_checkpoint_fingerprinting(
        tmp_path, monkeypatch):
    work, draft, checkpoint, rows, packets = _workspace(tmp_path)
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"\x89PNG\r\n\x1a\noutside")
    packets[1]["tiles"]["z1"] = str(outside)
    (work / "test-co_packets.json").write_text(json.dumps({"1": packets[1]}))
    called = []
    monkeypatch.setattr(
        judge_packets, "decision_fingerprint",
        lambda *args, **kwargs: called.append(True) or (_ for _ in ()).throw(
            AssertionError("fingerprinting ran before tile confinement")
        ),
    )
    with pytest.raises(ValueError, match="outside canonical ladder"):
        resolve_trust.prepare("test-co", work, _models(), tmp_path / "runs")
    assert called == []


def test_publication_geometry_rejects_nonfinite_coordinates_at_generation_boundary(tmp_path):
    work, draft, checkpoint, rows, packets = _workspace(tmp_path)
    dossier = work / "test-co_dossier.json"
    value = json.loads(dossier.read_text())
    value["facilities"][0]["lat"] = float("nan")
    dossier.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="dossier artifact (byte length|hash)"):
        resolve_trust.prepare("test-co", work, _models(), tmp_path / "runs")


def test_preserved_machine_v2_publication_proof_uses_current_preservation_receipt(
        tmp_path, monkeypatch):
    first, work, draft, checkpoint, rows, packets, before = _prepared(tmp_path)
    _write_output(first, 1, "challenger", copy.deepcopy(rows[0]))
    assert resolve_trust.apply_chunk(first, 0, apply=True)["state"] == "APPLIED"
    existing = json.loads(draft.read_text())[0]
    old_transaction = existing["trust_resolution"]["transaction_id"]

    second = resolve_trust.prepare(
        "test-co", work, _models(), tmp_path / "preservation-runs"
    )
    second_prepare = json.loads((second / "prepare.json").read_text())
    _write_output(
        second, 1, "challenger",
        copy.deepcopy(second_prepare["items"][0]["primary"]["decision"]),
    )
    assert resolve_trust.evaluate(second)[0]["state"] == "READY"
    assert resolve_trust.apply_chunk(second, 0, apply=True)["state"] == "PRESERVED"
    store = tmp_path / "store.json"
    monkeypatch.setattr(merge_drafts, "PADJ_TMP", str(work))
    assert merge_drafts.main([
        str(store), "test-co", "--resolver-run", str(second),
        "--judged", "2026-09-29", "--write",
    ]) == 0
    stored = json.loads(store.read_text())["way/1"]
    assert stored["trust_resolution"]["transaction_id"] == old_transaction
    registry = resolve_trust.replay_trust._load_publication_proofs(
        store.parent, store.name
    )
    assert resolve_trust.replay_trust._validate_publication_proof(
        stored, registry
    ) == []
    proof_sha = stored["publication_attestation"]["publication_proof_sha256"]
    manifest = copy.deepcopy(resolve_trust.replay_trust._load_proof_manifest(
        proof_sha, registry
    ))
    forged = copy.deepcopy(stored)
    primary = forged["trust_resolution"]["primary"]
    primary["decision"]["exists"]["evidence"] = "coherently forged preserved primary"
    primary["evidence_sha256"] = tr.evidence_sha256(primary["decision"])
    primary["decision_sha256"] = tr.decision_sha256(primary["decision"])
    body = dict(primary)
    body.pop("envelope_sha256")
    primary["envelope_sha256"] = tr.sha256_json(body)
    wrapper = forged["trust_resolution"]
    body = dict(wrapper)
    body.pop("resolution_sha256")
    wrapper["resolution_sha256"] = tr.sha256_json(body)
    manifest["final_rows"]["1"] = resolve_trust.replay_trust._proof_decision_row(forged)
    attestation = forged["publication_attestation"]
    attestation["authority_sha256"] = wrapper["resolution_sha256"]
    forged_registry = _forge_v4_manifest(
        forged, registry, manifest, tmp_path / "preserved-forgery-stages"
    )
    errors = resolve_trust.replay_trust._validate_publication_proof(
        forged, forged_registry
    )
    assert any(
        "preserved machine resolution differs from its prepared primary" in error
        for error in errors
    )


def test_publication_proof_preserves_crlf_sealed_output_bytes(tmp_path, monkeypatch):
    run, work, draft, checkpoint, rows, packets, before = _prepared(tmp_path)
    output = _write_output(run, 1, "challenger", copy.deepcopy(rows[0]))
    output.write_bytes(output.read_bytes().replace(b"\n", b"\r\n"))
    assert resolve_trust.apply_chunk(run, 0, apply=True)["state"] == "APPLIED"
    store = tmp_path / "store.json"
    monkeypatch.setattr(merge_drafts, "PADJ_TMP", str(work))
    assert merge_drafts.main([
        str(store), "test-co", "--resolver-run", str(run),
        "--judged", "2026-09-29", "--write",
    ]) == 0
    stored = json.loads(store.read_text())["way/1"]
    registry = resolve_trust.replay_trust._load_publication_proofs(
        store.parent, store.name
    )
    assert resolve_trust.replay_trust._validate_publication_proof(
        stored, registry
    ) == []


def test_publication_proof_rejects_coherently_rehashed_false_ready_snapshot(
        tmp_path, monkeypatch):
    run, work, draft, checkpoint, rows, packets, before = _prepared(tmp_path)
    _write_output(run, 1, "challenger", copy.deepcopy(rows[0]))
    assert resolve_trust.apply_chunk(run, 0, apply=True)["state"] == "APPLIED"
    store = tmp_path / "store.json"
    monkeypatch.setattr(merge_drafts, "PADJ_TMP", str(work))
    assert merge_drafts.main([
        str(store), "test-co", "--resolver-run", str(run),
        "--judged", "2026-09-29", "--write",
    ]) == 0
    row = json.loads(store.read_text())["way/1"]
    registry = resolve_trust.replay_trust._load_publication_proofs(
        store.parent, store.name
    )
    proof_sha = row["publication_attestation"]["publication_proof_sha256"]
    manifest = copy.deepcopy(resolve_trust.replay_trust._load_proof_manifest(
        proof_sha, registry
    ))

    old_seal_ref = manifest["output_seal_ref"]
    seal = json.loads(resolve_trust.replay_trust._load_manifest_object(
        manifest, registry, old_seal_ref
    ))
    seal["ready_snapshot_sha256"] = "f" * 64
    seal_body = dict(seal)
    seal_body.pop("seal_sha256")
    seal["seal_sha256"] = tr.sha256_json(seal_body)
    manifest["output_seal_ref"] = _stage_replacement_object(
        manifest, registry, old_seal_ref, resolve_trust._json_bytes(seal),
        tmp_path / "false-ready-seal-stages",
    )

    old_receipt_ref = manifest["chunk_receipts"]["0"]
    receipt = json.loads(resolve_trust.replay_trust._load_manifest_object(
        manifest, registry, old_receipt_ref
    ))
    receipt["output_seal_sha256"] = seal["seal_sha256"]
    manifest["chunk_receipts"]["0"] = _stage_replacement_object(
        manifest, registry, old_receipt_ref,
        resolve_trust._json_bytes(receipt),
        tmp_path / "false-ready-receipt-stages",
    )
    manifest["object_closure_sha256"] = (
        merge_drafts.publication_objects.object_closure_sha256(
            manifest["objects"].values()
        )
    )
    attestation = row["publication_attestation"]
    attestation["output_seal_sha256"] = seal["seal_sha256"]
    attestation["terminal_receipt_sha256"] = tr.sha256_json(receipt)
    forged_registry = _forge_v4_manifest(
        row, registry, manifest, tmp_path / "false-ready-proof-stages"
    )
    errors = resolve_trust.replay_trust._validate_publication_proof(
        row, forged_registry
    )
    assert any("whole-run READY snapshot mismatch" in error for error in errors)


@pytest.mark.parametrize(
    "field", ["after_sha256", "checkpoint_after_sha256", "transaction_id"]
)
def test_publication_proof_rejects_tampered_terminal_end_state_hashes(
        tmp_path, monkeypatch, field):
    run, work, draft, checkpoint, rows, packets, before = _prepared(tmp_path)
    _write_output(run, 1, "challenger", copy.deepcopy(rows[0]))
    assert resolve_trust.apply_chunk(run, 0, apply=True)["state"] == "APPLIED"
    store = tmp_path / "store.json"
    monkeypatch.setattr(merge_drafts, "PADJ_TMP", str(work))
    assert merge_drafts.main([
        str(store), "test-co", "--resolver-run", str(run),
        "--judged", "2026-09-29", "--write",
    ]) == 0
    row = json.loads(store.read_text())["way/1"]
    registry = resolve_trust.replay_trust._load_publication_proofs(
        store.parent, store.name
    )
    old_proof_sha = row["publication_attestation"]["publication_proof_sha256"]
    manifest = copy.deepcopy(resolve_trust.replay_trust._load_proof_manifest(
        old_proof_sha, registry
    ))
    old_receipt_ref = manifest["chunk_receipts"]["0"]
    receipt = json.loads(resolve_trust.replay_trust._load_manifest_object(
        manifest, registry, old_receipt_ref
    ))
    receipt[field] = "f" * 64
    receipt_ref = _stage_replacement_object(
        manifest, registry, old_receipt_ref,
        resolve_trust._json_bytes(receipt),
        tmp_path / f"receipt-{field}-stages",
    )
    manifest["chunk_receipts"]["0"] = receipt_ref
    manifest["object_closure_sha256"] = (
        merge_drafts.publication_objects.object_closure_sha256(
            manifest["objects"].values()
        )
    )
    attestation = row["publication_attestation"]
    attestation["terminal_receipt_sha256"] = tr.sha256_json(receipt)
    forged_registry = _forge_v4_manifest(
        row, registry, manifest, tmp_path / "terminal-receipt-proof-stages"
    )
    errors = resolve_trust.replay_trust._validate_publication_proof(
        row, forged_registry
    )
    assert any("apply receipt mismatch" in error for error in errors), errors


@pytest.mark.parametrize("field", ["completed", "resolution_row_sha256"])
def test_publication_proof_rejects_coherently_rehashed_false_checkpoint_manifest(
        tmp_path, monkeypatch, field):
    run, work, draft, checkpoint, rows, packets, before = _prepared(tmp_path)
    _write_output(run, 1, "challenger", copy.deepcopy(rows[0]))
    assert resolve_trust.apply_chunk(run, 0, apply=True)["state"] == "APPLIED"
    store = tmp_path / "store.json"
    monkeypatch.setattr(merge_drafts, "PADJ_TMP", str(work))
    assert merge_drafts.main([
        str(store), "test-co", "--resolver-run", str(run),
        "--judged", "2026-09-29", "--write",
    ]) == 0
    row = json.loads(store.read_text())["way/1"]
    registry = resolve_trust.replay_trust._load_publication_proofs(
        store.parent, store.name
    )
    old_proof_sha = row["publication_attestation"]["publication_proof_sha256"]
    manifest = copy.deepcopy(resolve_trust.replay_trust._load_proof_manifest(
        old_proof_sha, registry
    ))
    old_checkpoint_ref = manifest["terminal_checkpoints"]["0"]
    checkpoint_value = json.loads(
        resolve_trust.replay_trust._load_manifest_object(
            manifest, registry, old_checkpoint_ref
        )
    )
    checkpoint_value[field] = [] if field == "completed" else ["f" * 64]
    checkpoint_bytes = resolve_trust._json_bytes(checkpoint_value)
    checkpoint_ref = _stage_replacement_object(
        manifest, registry, old_checkpoint_ref, checkpoint_bytes,
        tmp_path / f"checkpoint-{field}-stages",
    )
    manifest["terminal_checkpoints"]["0"] = checkpoint_ref
    old_receipt_ref = manifest["chunk_receipts"]["0"]
    receipt = json.loads(resolve_trust.replay_trust._load_manifest_object(
        manifest, registry, old_receipt_ref
    ))
    receipt["checkpoint_after_sha256"] = hashlib.sha256(
        checkpoint_bytes
    ).hexdigest()
    receipt_ref = _stage_replacement_object(
        manifest, registry, old_receipt_ref,
        resolve_trust._json_bytes(receipt),
        tmp_path / f"checkpoint-receipt-{field}-stages",
    )
    manifest["chunk_receipts"]["0"] = receipt_ref
    manifest["object_closure_sha256"] = (
        merge_drafts.publication_objects.object_closure_sha256(
            manifest["objects"].values()
        )
    )
    row["publication_attestation"]["terminal_receipt_sha256"] = (
        tr.sha256_json(receipt)
    )
    forged_registry = _forge_v4_manifest(
        row, registry, manifest, tmp_path / "checkpoint-proof-stages"
    )
    errors = resolve_trust.replay_trust._validate_publication_proof(
        row, forged_registry
    )
    assert any("apply receipt mismatch" in error for error in errors), errors


# Independent filesystem review: trusted-parent and lock-inode boundaries.


def _track_trusted_descriptor_lifecycle(monkeypatch, trusted):
    real_open = trusted.os.open
    real_close = trusted.os.close
    opened = []
    closed = []
    active = {}
    unexpected_closes = []

    def tracked_open(path, flags, *args, **kwargs):
        fd = real_open(path, flags, *args, **kwargs)
        token = (len(opened), fd)
        assert fd not in active
        active[fd] = token
        opened.append(token)
        return fd

    def tracked_close(fd):
        token = active.pop(fd, None)
        if token is None:
            unexpected_closes.append(fd)
        else:
            closed.append(token)
        return real_close(fd)

    monkeypatch.setattr(trusted.os, "open", tracked_open)
    monkeypatch.setattr(trusted.os, "close", tracked_close)
    return opened, closed, active, unexpected_closes


def test_recursive_directory_creation_opens_validates_and_fsyncs_each_parent(
        tmp_path, monkeypatch):
    trusted = resolve_trust.trusted_fs
    base = tmp_path / "durable-root"
    base.mkdir(mode=0o700)
    names = ("level-one", "level-two", "level-three")
    target = base.joinpath(*names)
    events = []
    fsync_index = 0
    real_mkdir = trusted.os.mkdir
    real_open = trusted.os.open
    real_validate = trusted._require_created_directory_entry
    real_fsync = trusted.os.fsync

    def observed_mkdir(name, *args, **kwargs):
        result = real_mkdir(name, *args, **kwargs)
        if name in names and kwargs.get("dir_fd") is not None:
            events.append(("mkdir", name))
        return result

    def observed_open(name, flags, *args, **kwargs):
        fd = real_open(name, flags, *args, **kwargs)
        if name in names and kwargs.get("dir_fd") is not None:
            events.append(("open", name))
        return fd

    def observed_validation(child_fd, parent_fd, name, child,
                            requested_mode, expected_identity=None):
        result = real_validate(
            child_fd, parent_fd, name, child, requested_mode,
            expected_identity,
        )
        phase = "validate-after" if expected_identity is not None else "validate-before"
        events.append((phase, name))
        return result

    def observed_fsync(fd):
        nonlocal fsync_index
        result = real_fsync(fd)
        name = names[fsync_index]
        fsync_index += 1
        events.append(("fsync-parent", name, trusted.os.fstat(fd).st_ino))
        return result

    monkeypatch.setattr(trusted.os, "mkdir", observed_mkdir)
    monkeypatch.setattr(trusted.os, "open", observed_open)
    monkeypatch.setattr(
        trusted, "_require_created_directory_entry", observed_validation
    )
    monkeypatch.setattr(trusted.os, "fsync", observed_fsync)

    fd = trusted.open_directory_fd(target, create=True, create_mode=0o700)
    trusted.os.close(fd)
    expected = []
    parents = (base, base / names[0], base / names[0] / names[1])
    for name, parent in zip(names, parents):
        expected.extend([
            ("mkdir", name),
            ("open", name),
            ("validate-before", name),
            ("fsync-parent", name, parent.stat().st_ino),
            ("validate-after", name),
        ])
    assert events == expected
    assert fsync_index == len(names)

    events.clear()
    fd = trusted.open_directory_fd(target, create=True, create_mode=0o700)
    trusted.os.close(fd)
    assert not any(event[0] in {
        "mkdir", "validate-before", "validate-after", "fsync-parent",
    } for event in events)


@pytest.mark.parametrize("fail_on_fsync", [1, 2])
def test_recursive_directory_parent_fsync_failure_stops_descent_and_closes_fds(
        tmp_path, monkeypatch, fail_on_fsync):
    trusted = resolve_trust.trusted_fs
    base = tmp_path / "fsync-failure-root"
    base.mkdir(mode=0o700)
    names = ("failure-one", "failure-two", "failure-three")
    target = base.joinpath(*names)
    opened, closed, active, unexpected = _track_trusted_descriptor_lifecycle(
        monkeypatch, trusted
    )
    real_mkdir = trusted.os.mkdir
    real_fsync = trusted.os.fsync
    mkdirs = []
    fsyncs = 0
    failure = OSError(f"simulated parent fsync failure {fail_on_fsync}")

    def observed_mkdir(name, *args, **kwargs):
        result = real_mkdir(name, *args, **kwargs)
        if name in names and kwargs.get("dir_fd") is not None:
            mkdirs.append(name)
        return result

    def fail_selected_fsync(fd):
        nonlocal fsyncs
        fsyncs += 1
        if fsyncs == fail_on_fsync:
            raise failure
        return real_fsync(fd)

    monkeypatch.setattr(trusted.os, "mkdir", observed_mkdir)
    monkeypatch.setattr(trusted.os, "fsync", fail_selected_fsync)
    with pytest.raises(OSError) as raised:
        trusted.open_directory_fd(target, create=True, create_mode=0o700)

    assert raised.value is failure
    assert mkdirs == list(names[:fail_on_fsync])
    assert fsyncs == fail_on_fsync
    current = base
    for index, name in enumerate(names, start=1):
        current /= name
        assert current.exists() is (index <= fail_on_fsync)
    assert not active and not unexpected
    assert set(closed) == set(opened) and len(closed) == len(opened)


@pytest.mark.parametrize("mismatch", ["identity", "mode", "acl"])
def test_recursive_directory_child_mismatch_precedes_parent_fsync_and_descent(
        tmp_path, monkeypatch, mismatch):
    trusted = resolve_trust.trusted_fs
    base = tmp_path / f"child-{mismatch}-root"
    base.mkdir(mode=0o700)
    child = base / "untrusted-child"
    target = child / "must-not-exist"
    opened, closed, active, unexpected = _track_trusted_descriptor_lifecycle(
        monkeypatch, trusted
    )
    real_stat = trusted.os.stat
    real_acl = trusted.require_trivial_acl_fd
    real_fsync = trusted.os.fsync
    injected = False
    fsyncs = 0
    acl_failure = ValueError("simulated child ACL mismatch")

    def mismatched_stat(path, *args, **kwargs):
        nonlocal injected
        value = real_stat(path, *args, **kwargs)
        if (mismatch in ("identity", "mode") and not injected
                and path == child.name and kwargs.get("dir_fd") is not None):
            injected = True
            fields = list(value)
            if mismatch == "identity":
                fields[stat.ST_INO] = value.st_ino + 1
            else:
                fields[stat.ST_MODE] = (
                    value.st_mode & ~0o7777
                ) | 0o755
            return os.stat_result(fields)
        return value

    def mismatched_acl(fd, path, *, is_directory=False):
        nonlocal injected
        if mismatch == "acl" and Path(path) == child and not injected:
            injected = True
            raise acl_failure
        return real_acl(fd, path, is_directory=is_directory)

    def counted_fsync(fd):
        nonlocal fsyncs
        fsyncs += 1
        return real_fsync(fd)

    monkeypatch.setattr(trusted.os, "stat", mismatched_stat)
    monkeypatch.setattr(trusted, "require_trivial_acl_fd", mismatched_acl)
    monkeypatch.setattr(trusted.os, "fsync", counted_fsync)
    with pytest.raises(ValueError) as raised:
        trusted.open_directory_fd(target, create=True, create_mode=0o700)

    assert injected
    if mismatch == "acl":
        assert raised.value is acl_failure
    assert fsyncs == 0
    assert child.is_dir() and not target.exists()
    assert not active and not unexpected
    assert set(closed) == set(opened) and len(closed) == len(opened)


def test_recursive_directory_file_exists_race_is_not_adopted_or_retried(
        tmp_path, monkeypatch):
    trusted = resolve_trust.trusted_fs
    base = tmp_path / "file-exists-race-root"
    base.mkdir(mode=0o700)
    raced = base / "raced-child"
    target = raced / "must-not-exist"
    real_mkdir = trusted.os.mkdir
    real_fsync = trusted.os.fsync
    failure = FileExistsError("simulated mkdir race after ENOENT")
    fsyncs = 0

    def win_race_then_fail(name, *args, **kwargs):
        real_mkdir(name, *args, **kwargs)
        raise failure

    def counted_fsync(fd):
        nonlocal fsyncs
        fsyncs += 1
        return real_fsync(fd)

    monkeypatch.setattr(trusted.os, "mkdir", win_race_then_fail)
    monkeypatch.setattr(trusted.os, "fsync", counted_fsync)
    with pytest.raises(FileExistsError) as raised:
        trusted.open_directory_fd(target, create=True, create_mode=0o700)
    assert raised.value is failure
    assert fsyncs == 0
    assert raced.is_dir() and not target.exists()


def test_review_artifact_directory_routes_missing_components_through_durable_creator(
        tmp_path, monkeypatch):
    root = tmp_path / "review-root"
    root.mkdir(mode=0o700)
    trusted = resolve_trust.trusted_fs
    real_create = trusted.create_trusted_directory_fd
    created = []

    def observed_create(parent_fd, parent_path, name, *, create_mode=0o700):
        child_fd = real_create(
            parent_fd, parent_path, name, create_mode=create_mode
        )
        created.append(Path(parent_path) / name)
        return child_fd

    monkeypatch.setattr(
        trusted, "create_trusted_directory_fd", observed_create
    )
    run_id = "a" * 64
    fd = resolve_trust._open_review_artifact_directory(
        root, "test-co", run_id, create=True
    )
    os.close(fd)
    expected = [
        root / ".review-artifacts",
        root / ".review-artifacts" / "test-co",
        root / ".review-artifacts" / "test-co" / run_id,
    ]
    assert created == expected
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o700 for path in expected)

    created.clear()
    fd = resolve_trust._open_review_artifact_directory(
        root, "test-co", run_id, create=True
    )
    os.close(fd)
    assert created == []


def test_transaction_directory_fsync_failure_precedes_private_journal_and_replace(
        tmp_path, monkeypatch):
    run, work, draft, checkpoint, rows, packets, before = _prepared(tmp_path)
    _write_output(run, 1, "challenger", copy.deepcopy(rows[0]))
    backup_directory = run / "backups"
    assert not backup_directory.exists()
    trusted = resolve_trust.trusted_fs
    real_fsync = trusted.os.fsync
    run_stat = run.stat()
    failure = OSError("simulated transaction-directory parent fsync failure")
    failed = False
    replacements = []

    def fail_backup_parent_fsync(fd):
        nonlocal failed
        value = trusted.os.fstat(fd)
        if ((value.st_dev, value.st_ino) == (run_stat.st_dev, run_stat.st_ino)
                and backup_directory.is_dir()):
            failed = True
            raise failure
        return real_fsync(fd)

    def observe_replace(*args, **kwargs):
        replacements.append((args, kwargs))
        raise AssertionError("canonical replacement ran after directory fsync failure")

    monkeypatch.setattr(trusted.os, "fsync", fail_backup_parent_fsync)
    monkeypatch.setattr(
        resolve_trust, "_replace_verified_nofollow", observe_replace
    )
    with pytest.raises(OSError) as raised:
        resolve_trust.apply_chunk(run, 0, apply=True)

    assert raised.value is failure and failed
    assert draft.read_bytes() == before[draft]
    assert checkpoint.read_bytes() == before[checkpoint]
    assert replacements == []
    assert not (run / "transactions" / "chunk-00.json").exists()
    assert not (run / "staged").exists()
    assert backup_directory.is_dir() and list(backup_directory.iterdir()) == []


def test_lock_open_creates_private_parent_and_owner_only_single_link_inode(
        tmp_path):
    lock_path = tr.area_lock_path(tmp_path, "test-co")
    with resolve_trust._open_lock_file(lock_path):
        pass
    parent = lock_path.parent.stat()
    lock = lock_path.stat()
    assert stat.S_IMODE(parent.st_mode) == 0o700
    assert parent.st_uid == os.geteuid()
    assert stat.S_IMODE(lock.st_mode) == 0o600
    assert lock.st_uid == os.geteuid() and lock.st_nlink == 1


@pytest.mark.parametrize("mode", [0o770, 0o707])
def test_lock_open_rejects_insecure_existing_parent_without_chmod(
        tmp_path, mode):
    lock_path = tr.area_lock_path(tmp_path, "test-co")
    lock_path.parent.mkdir(mode=mode)
    lock_path.parent.chmod(mode)
    with pytest.raises(ValueError, match="group/world writable"):
        resolve_trust._open_lock_file(lock_path)
    assert stat.S_IMODE(lock_path.parent.stat().st_mode) == mode
    assert not lock_path.exists()


def test_lock_open_rejects_hardlinked_lock_inode_without_mutation(tmp_path):
    lock_path = tr.area_lock_path(tmp_path, "test-co")
    lock_path.parent.mkdir(mode=0o700)
    source = tmp_path / "external-lock-inode"
    source.write_bytes(b"")
    source.chmod(0o600)
    os.link(source, lock_path)
    before = os.lstat(lock_path)
    with pytest.raises(ValueError, match="single-link"):
        resolve_trust._open_lock_file(lock_path)
    after = os.lstat(lock_path)
    assert (after.st_dev, after.st_ino, after.st_mode, after.st_nlink) == (
        before.st_dev, before.st_ino, before.st_mode, before.st_nlink
    )
    assert source.stat().st_ino == after.st_ino


def test_lock_open_rejects_path_rotation_between_fd_and_entry(
        tmp_path, monkeypatch):
    lock_path = tr.area_lock_path(tmp_path, "test-co")
    real_open = resolve_trust.trusted_fs.os.open
    rotated = False

    def rotate_after_open(path, flags, *args, **kwargs):
        nonlocal rotated
        fd = real_open(path, flags, *args, **kwargs)
        if (not rotated and path == lock_path.name
                and flags & os.O_RDWR and flags & os.O_CREAT
                and kwargs.get("dir_fd") is not None):
            parent_fd = kwargs["dir_fd"]
            os.unlink(path, dir_fd=parent_fd)
            replacement = real_open(
                path,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent_fd,
            )
            os.close(replacement)
            rotated = True
        return fd

    monkeypatch.setattr(resolve_trust.trusted_fs.os, "open", rotate_after_open)
    with pytest.raises(ValueError, match="identity, mode, or path changed"):
        resolve_trust._open_lock_file(lock_path)
    assert rotated and lock_path.is_file()


def test_verified_replace_rejects_insecure_target_parent_before_private_entry(
        tmp_path):
    source_parent = tmp_path / "source"
    target_parent = tmp_path / "target"
    source_parent.mkdir(mode=0o700)
    target_parent.mkdir(mode=0o770)
    target_parent.chmod(0o770)
    source = source_parent / "stage.json"
    target = target_parent / "canonical.json"
    source.write_bytes(b"captured")
    target.write_bytes(b"old")
    with pytest.raises(ValueError, match="group/world writable"):
        resolve_trust._replace_verified_nofollow(
            source, target, hashlib.sha256(b"captured").hexdigest()
        )
    assert target.read_bytes() == b"old"
    assert not any(
        entry.name.startswith(".verified-replace-")
        for entry in target_parent.iterdir()
    )


def test_verified_replace_detects_same_uid_private_basename_rotation_boundary(
        tmp_path, monkeypatch):
    source = tmp_path / "stage.json"
    target = tmp_path / "canonical.json"
    source.write_bytes(b"captured")
    target.write_bytes(b"old")
    attacker = b"same-uid lock-contract violation"
    real_replace = resolve_trust.os.replace
    rotated = False

    def rotate_private_then_replace(src, dst, *, src_dir_fd=None,
                                    dst_dir_fd=None):
        nonlocal rotated
        if not rotated and str(src).startswith(".verified-replace-"):
            os.unlink(src, dir_fd=src_dir_fd)
            fd = os.open(
                src,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=src_dir_fd,
            )
            try:
                os.write(fd, attacker)
                os.fsync(fd)
            finally:
                os.close(fd)
            rotated = True
        return real_replace(
            src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd
        )

    monkeypatch.setattr(resolve_trust.os, "replace", rotate_private_then_replace)
    with pytest.raises(ValueError, match="replacement target"):
        resolve_trust._replace_verified_nofollow(
            source, target, hashlib.sha256(b"captured").hexdigest()
        )
    assert rotated
    # Detection is fail-closed, but portable POSIX cannot restore a canonical
    # target after a malicious same-UID process violates the lock contract.
    assert target.read_bytes() == attacker
    assert source.read_bytes() == b"captured"


# Independent filesystem review: host external evidence is captured once.


def _external_capture_paths(work: Path) -> dict[str, Path]:
    root = work / "test-co_external"
    manifest = root / "manifest.json"
    source = json.loads(manifest.read_text())["sources"][0]
    return {
        "manifest": manifest,
        "artifact": Path(source["path"]),
        "metadata": Path(source["item_metadata_path"]),
    }


@pytest.mark.parametrize("kind", ["manifest", "artifact", "metadata"])
def test_prepare_materializes_external_evidence_only_from_captured_buffers(
        tmp_path, monkeypatch, kind):
    source = tmp_path / "official-source.json"
    source.write_bytes(b'{"official":true}')
    work, draft, checkpoint, rows, packets = _workspace(tmp_path / "workspace")
    _install_external_sources(work, [(source, "official-source")])
    host_paths = _external_capture_paths(work)
    originals = {name: path.read_bytes() for name, path in host_paths.items()}
    real_capture = resolve_trust._host_external_evidence_catalog
    replaced = False

    def capture_then_replace(*args, **kwargs):
        nonlocal replaced
        result = real_capture(*args, **kwargs)
        host_paths[kind].write_bytes(b'{"replacement":true}')
        replaced = True
        return result

    monkeypatch.setattr(
        resolve_trust, "_host_external_evidence_catalog", capture_then_replace
    )
    run = resolve_trust.prepare(
        "test-co", work, _models(), tmp_path / "runs"
    )
    prepared = resolve_trust.load_prepare_document(
        run / "prepare.json", expected_area="test-co"
    )
    bound = prepared["external_evidence_catalog"]["official-source"]
    frozen = {
        "manifest": run / bound["manifest_frozen_path"],
        "artifact": run / bound["frozen_path"],
        "metadata": run / bound["metadata_frozen_path"],
    }
    assert replaced
    assert frozen[kind].read_bytes() == originals[kind]
    assert frozen[kind].read_bytes() != host_paths[kind].read_bytes()


@pytest.mark.parametrize("kind", ["manifest", "artifact", "metadata"])
@pytest.mark.parametrize("drift", ["replacement", "same-inode"])
def test_external_evidence_stable_capture_rejects_entry_and_in_place_drift(
        tmp_path, monkeypatch, kind, drift):
    source = tmp_path / "official-source.json"
    source.write_bytes(b'{"official":true}')
    work, draft, checkpoint, rows, packets = _workspace(tmp_path / "workspace")
    _install_external_sources(work, [(source, "official-source")])
    target = _external_capture_paths(work)[kind]
    original = target.read_bytes()
    target_inode = target.stat().st_ino
    real_read = resolve_trust._read_fd_bytes
    mutated = False

    def mutate_after_read(fd):
        nonlocal mutated
        data = real_read(fd)
        if mutated or os.fstat(fd).st_ino != target_inode:
            return data
        before = target.stat()
        replacement = bytes([original[0] ^ 1]) + original[1:]
        if drift == "replacement":
            attacker = target.with_name(f"{target.name}.replacement")
            attacker.write_bytes(replacement)
            os.replace(attacker, target)
        else:
            with target.open("r+b") as stream:
                stream.write(replacement)
                stream.truncate()
                stream.flush()
                os.fsync(stream.fileno())
            changed = target.stat()
            os.utime(target, ns=(changed.st_atime_ns, before.st_mtime_ns))
            after = target.stat()
            assert after.st_ino == before.st_ino
            assert after.st_size == before.st_size
            assert after.st_mtime_ns == before.st_mtime_ns
        mutated = True
        return data

    monkeypatch.setattr(resolve_trust, "_read_fd_bytes", mutate_after_read)
    with pytest.raises(ValueError, match="changed during secure capture"):
        resolve_trust._host_external_evidence_catalog(work, "test-co")
    assert mutated


def test_external_evidence_host_paths_are_each_read_exactly_once(
        tmp_path, monkeypatch):
    source = tmp_path / "official-source.json"
    source.write_bytes(b'{"official":true}')
    work, draft, checkpoint, rows, packets = _workspace(tmp_path / "workspace")
    _install_external_sources(work, [(source, "official-source")])
    host_paths = _external_capture_paths(work)
    external_root = work / "test-co_external"
    real_capture = resolve_trust._capture_stable_regular
    reads: dict[Path, int] = {}

    def counted_capture(path, label):
        canonical = Path(path)
        if canonical.is_relative_to(external_root):
            reads[canonical] = reads.get(canonical, 0) + 1
        return real_capture(canonical, label)

    monkeypatch.setattr(
        resolve_trust, "_capture_stable_regular", counted_capture
    )
    run = resolve_trust.prepare(
        "test-co", work, _models(), tmp_path / "runs"
    )
    assert resolve_trust.load_prepare_document(run / "prepare.json")["run_id"] == run.name
    assert reads == {path: 1 for path in host_paths.values()}


def test_prepare_refuses_to_return_run_that_fails_completed_load(
        tmp_path, monkeypatch):
    source = tmp_path / "official-source.json"
    source.write_bytes(b'{"official":true}')
    work, draft, checkpoint, rows, packets = _workspace(tmp_path / "workspace")
    _install_external_sources(work, [(source, "official-source")])
    real_write = resolve_trust._write_idempotent
    corrupted = False

    def write_then_corrupt(path, data):
        nonlocal corrupted
        real_write(path, data)
        if not corrupted and Path(path).suffix == ".bin":
            Path(path).write_bytes(b"corrupted after materialization")
            corrupted = True

    monkeypatch.setattr(resolve_trust, "_write_idempotent", write_then_corrupt)
    with pytest.raises(ValueError, match="external evidence.*bytes changed"):
        resolve_trust.prepare(
            "test-co", work, _models(), tmp_path / "runs"
        )
    assert corrupted


# Independent filesystem review: nested JSON and CLI boundaries are total.


def _prepare_cli_args(work: Path, run_root: Path) -> list[str]:
    return [
        "prepare", "test-co", "--tmp", str(work),
        "--run-root", str(run_root),
        "--primary-model", "primary-model:family-a",
        "--challenger-model", "challenger-model:family-b",
        "--arbiter-model", "arbiter-model:family-c",
    ]


@pytest.mark.parametrize("field", ["path", "item_metadata_path"])
@pytest.mark.parametrize(
    "malformed", [None, True, 7, ["bad"], {"bad": "path"}],
    ids=["null", "boolean", "number", "list", "object"],
)
def test_prepare_cli_rejects_non_string_external_paths_without_traceback(
        tmp_path, capsys, field, malformed):
    source = tmp_path / "official-source.json"
    source.write_bytes(b'{"official":true}')
    work, draft, checkpoint, rows, packets = _workspace(tmp_path / "workspace")
    _install_external_sources(work, [(source, "official-source")])
    manifest_path = work / "test-co_external" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["sources"][0][field] = malformed
    manifest_path.write_text(json.dumps(manifest))

    assert resolve_trust.main(
        _prepare_cli_args(work, tmp_path / "runs")
    ) == 2
    captured = capsys.readouterr()
    assert "trust resolver failed:" in captured.err
    assert field in captured.err
    assert "Traceback" not in captured.err + captured.out


def test_prepare_accepts_current_producer_null_optional_rings(tmp_path):
    work, *_ = _workspace(tmp_path / "workspace")
    dossier_path = work / "test-co_dossier.json"
    dossier = json.loads(dossier_path.read_text())
    dossier["facilities"][0]["ring"] = None
    dossier["facilities"][0]["rings"] = None
    dossier_path.write_text(json.dumps(dossier))
    _commit_generation_and_rebind(work)

    run = resolve_trust.prepare(
        "test-co", work, _models(), tmp_path / "runs"
    )
    prepare = json.loads((run / "prepare.json").read_text())
    assert prepare["source"]["publication"]["facilities"]["1"]["rings"] == []


@pytest.mark.parametrize(
    "field,malformed,expected",
    [
        ("tags_union", None, "tags_union must be an object"),
        ("tags_union", [], "tags_union must be an object"),
        ("tags_union", "", "tags_union must be an object"),
        ("tags_union", False, "tags_union must be an object"),
        ("tags_union", 7, "tags_union must be an object"),
        ("rings", {}, "rings must be a list or null"),
        ("rings", "", "rings must be a list or null"),
        ("rings", True, "rings must be a list or null"),
        ("rings", [None], "ring is invalid"),
        ("rings", [{}], "ring is invalid"),
        ("rings", ["bad"], "ring is invalid"),
        ("rings", [[[40.0]]], "ring point is invalid"),
        ("ring", "bad", "ring must be a list or null"),
    ],
)
def test_prepare_cli_rejects_malformed_dossier_tags_and_rings_without_traceback(
        tmp_path, capsys, field, malformed, expected):
    work, draft, checkpoint, rows, packets = _workspace(tmp_path / "workspace")
    dossier_path = work / "test-co_dossier.json"
    dossier = json.loads(dossier_path.read_text())
    dossier["facilities"][0][field] = malformed
    dossier_path.write_text(json.dumps(dossier))
    _commit_generation_and_rebind(work)

    assert resolve_trust.main(
        _prepare_cli_args(work, tmp_path / "runs")
    ) == 2
    captured = capsys.readouterr()
    assert expected in captured.err
    assert "Traceback" not in captured.err + captured.out


@pytest.mark.parametrize(
    "malformed", [None, True, 7, {}, "bad", [None], [{}], ["bad"]],
    ids=["null", "boolean", "number", "object", "string", "null-ring",
         "object-ring", "string-ring"],
)
def test_status_and_apply_cli_reject_malformed_persisted_rings_without_traceback(
        tmp_path, capsys, malformed):
    run, work, draft, checkpoint, rows, packets, before = _prepared(tmp_path)
    prepare_path = run / "prepare.json"
    document = json.loads(prepare_path.read_text())
    document["source"]["publication"]["facilities"]["1"]["rings"] = malformed
    prepare_path.write_text(json.dumps(document))

    assert resolve_trust.main(["status", "--run", str(run)]) == 2
    status_output = capsys.readouterr()
    assert "trust resolver failed:" in status_output.err
    assert "publication facility 1" in status_output.err
    assert "Traceback" not in status_output.err + status_output.out

    assert resolve_trust.main([
        "apply", "--run", str(run), "--chunk", "0",
    ]) == 2
    apply_output = capsys.readouterr()
    assert "trust resolver failed:" in apply_output.err
    assert "publication facility 1" in apply_output.err
    assert "Traceback" not in apply_output.err + apply_output.out
    assert draft.read_bytes() == before[draft]
    assert checkpoint.read_bytes() == before[checkpoint]


@pytest.mark.parametrize(
    "command,error_type", [
        ("prepare", TypeError),
        ("prepare", AttributeError),
        ("status", TypeError),
        ("status", AttributeError),
        ("apply", TypeError),
        ("apply", AttributeError),
    ],
)
def test_resolver_cli_expected_nested_type_failures_have_no_traceback(
        tmp_path, monkeypatch, capsys, command, error_type):
    def fail(*args, **kwargs):
        raise error_type("malformed nested input")

    if command == "prepare":
        monkeypatch.setattr(resolve_trust, "prepare", fail)
        argv = _prepare_cli_args(tmp_path / "work", tmp_path / "runs")
    elif command == "status":
        monkeypatch.setattr(resolve_trust, "evaluate", fail)
        argv = ["status", "--run", str(tmp_path / "run")]
    else:
        monkeypatch.setattr(resolve_trust, "apply_chunk", fail)
        argv = ["apply", "--run", str(tmp_path / "run"), "--chunk", "0"]

    assert resolve_trust.main(argv) == 2
    captured = capsys.readouterr()
    assert captured.err == "trust resolver failed: malformed nested input\n"
    assert "Traceback" not in captured.err + captured.out


def test_lock_open_safely_tightens_canonical_legacy_0644_inode(tmp_path):
    lock_path = tr.area_lock_path(tmp_path, "test-co")
    lock_path.parent.mkdir(mode=0o700)
    lock_path.write_bytes(b"")
    lock_path.chmod(0o644)
    before = os.lstat(lock_path)

    with resolve_trust._open_lock_file(lock_path) as handle:
        during = os.fstat(handle.fileno())
        entry = os.lstat(lock_path)
        assert stat.S_IMODE(during.st_mode) == 0o600
        assert stat.S_IMODE(entry.st_mode) == 0o600
        assert (during.st_dev, during.st_ino) == (before.st_dev, before.st_ino)
        assert (entry.st_dev, entry.st_ino) == (before.st_dev, before.st_ino)

    after = os.lstat(lock_path)
    assert stat.S_IMODE(after.st_mode) == 0o600
    assert (after.st_dev, after.st_ino, after.st_nlink) == (
        before.st_dev, before.st_ino, before.st_nlink
    )


# ------------------------------------------------ latest human/filesystem re-review regressions


def test_prepare_requires_strict_duplicate_free_exact_canonical_bytes(
        tmp_path, capsys):
    run, work, draft, checkpoint, rows, packets, before = _prepared(tmp_path)
    prepare_path = run / "prepare.json"
    canonical = prepare_path.read_bytes()
    document = resolve_trust.parse_prepare_bytes(canonical, expected_area="test-co")
    assert canonical == resolve_trust._json_bytes(document)
    variants = (
        b" " + canonical,
        canonical.rstrip(b"\n"),
        b'{"version":2,' + canonical[1:],
    )
    for variant in variants:
        with pytest.raises(ValueError, match="strict JSON|noncanonical"):
            resolve_trust.parse_prepare_bytes(variant, expected_area="test-co")
        prepare_path.write_bytes(variant)
        assert resolve_trust.main(["status", "--run", str(run)]) == 2
        output = capsys.readouterr()
        assert "strict JSON" in output.err or "noncanonical" in output.err
        assert "Traceback" not in output.err + output.out
        assert draft.read_bytes() == before[draft]
        assert checkpoint.read_bytes() == before[checkpoint]
    prepare_path.write_bytes(canonical)
    assert resolve_trust.load_prepare_document(prepare_path) == document


@pytest.mark.parametrize(
    "locator",
    [
        "https:///hostless",
        "https://:443/hostless",
        "https://@evidence.example/path",
        "https://:secret@evidence.example/path",
        "https://user@evidence.example/path",
        "https://evidence.example:notaport/path",
        "https://evidence.example:99999/path",
        "https://evidence.example:-1/path",
        "https://evidence.example/path\nignored",
        "https://EVIDENCE.example/path",
        "https://evidence.example./path",
    ],
)
def test_https_source_locator_rejects_hostless_credentials_ports_controls_and_noncanonical_hosts(
        locator):
    assert tr._source_locator(locator) is False


def test_https_source_locator_accepts_canonical_dns_and_ip_hosts():
    assert tr._source_locator("https://evidence.example/path?query=1") is True
    assert tr._source_locator("https://127.0.0.1:443/path") is True
    assert tr._source_locator("https://[::1]/path") is True


@pytest.mark.parametrize(
    "locator",
    [
        "https://:443/hostless",
        "https://:secret@evidence.example/path",
        "https://evidence.example:notaport/path",
        "https://evidence.example/path\nignored",
    ],
)
def test_prepare_cli_rejects_malformed_https_locators_without_traceback(
        tmp_path, capsys, locator):
    source = tmp_path / "official-source.json"
    source.write_bytes(b'{"official":true}')
    work, *_ = _workspace(tmp_path / "workspace")
    _install_external_sources(work, [(source, "official-source")])
    manifest_path = work / "test-co_external" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["sources"][0]["query_endpoint"] = locator
    manifest_path.write_text(json.dumps(manifest))
    assert resolve_trust.main(
        _prepare_cli_args(work, tmp_path / "runs")
    ) == 2
    output = capsys.readouterr()
    assert "locator is invalid" in output.err
    assert "Traceback" not in output.err + output.out


def test_oversized_coordinate_and_evidence_timestamp_are_controlled_validation_errors(
        tmp_path, capsys):
    huge = 10 ** 1000
    with pytest.raises(ValueError, match="out of range"):
        merge_drafts.verdict_source._coordinate(
            huge, "test coordinate", -90.0, 90.0
        )

    work, *_ = _workspace(tmp_path / "coordinate-workspace")
    dossier_path = work / "test-co_dossier.json"
    dossier = json.loads(dossier_path.read_text())
    dossier["facilities"][0]["lat"] = huge
    dossier_path.write_text(json.dumps(dossier))
    _commit_generation_and_rebind(work)
    assert resolve_trust.main(
        _prepare_cli_args(work, tmp_path / "coordinate-runs")
    ) == 2
    coordinate_output = capsys.readouterr()
    assert "latitude is out of range" in coordinate_output.err
    assert "Traceback" not in coordinate_output.err + coordinate_output.out

    source = tmp_path / "official-source.json"
    source.write_bytes(b'{"official":true}')
    evidence_work, *_ = _workspace(tmp_path / "evidence-workspace")
    _install_external_sources(
        evidence_work, [(source, "official-source")]
    )
    paths = _external_capture_paths(evidence_work)
    paths["metadata"].write_text(json.dumps({"modified": huge}))
    manifest = json.loads(paths["manifest"].read_text())
    manifest["sources"][0]["item_metadata_sha256"] = hashlib.sha256(
        paths["metadata"].read_bytes()
    ).hexdigest()
    paths["manifest"].write_text(json.dumps(manifest))
    assert resolve_trust.main(
        _prepare_cli_args(evidence_work, tmp_path / "evidence-runs")
    ) == 2
    timestamp_output = capsys.readouterr()
    assert "modified time is invalid" in timestamp_output.err
    assert "Traceback" not in timestamp_output.err + timestamp_output.out


def test_terminal_receipt_validator_is_total_for_heterogeneous_nested_values(
        tmp_path):
    run, work, draft, checkpoint, rows, packets, before = _prepared(tmp_path)
    _write_output(run, 1, "challenger", copy.deepcopy(rows[0]))
    assert resolve_trust.apply_chunk(run, 0, apply=True)["state"] == "APPLIED"
    receipt = json.loads((run / "receipts" / "chunk-00.json").read_text())
    assert resolve_trust.validate_terminal_receipt(receipt) == []

    malformed_receipts = []
    missing = copy.deepcopy(receipt)
    missing["terminal_fids"][0].pop("fid")
    malformed_receipts.append(missing)
    heterogeneous = copy.deepcopy(receipt)
    heterogeneous["terminal_fids"] = [
        {"fid": [], "state": "resolved", "selected_role": "challenger",
         "decision_sha256": "0" * 64},
        {"fid": True, "state": "resolved", "selected_role": "challenger",
         "decision_sha256": "0" * 64},
        {"fid": 10 ** 1000, "state": "resolved",
         "selected_role": "challenger", "decision_sha256": "0" * 64},
    ]
    malformed_receipts.append(heterogeneous)
    malformed_applied = copy.deepcopy(receipt)
    malformed_applied["applied_fids"] = [None, [], True, 10 ** 1000]
    malformed_receipts.append(malformed_applied)
    for malformed in malformed_receipts:
        errors = resolve_trust.validate_terminal_receipt(malformed)
        assert errors
        assert isinstance(errors, list)


def test_publication_proof_validator_is_total_for_malformed_receipts_and_terminal_rows(
        tmp_path, monkeypatch):
    run, work, draft, checkpoint, rows, packets, before = _prepared(tmp_path)
    _write_output(run, 1, "challenger", copy.deepcopy(rows[0]))
    assert resolve_trust.apply_chunk(run, 0, apply=True)["state"] == "APPLIED"
    store = tmp_path / "store.json"
    monkeypatch.setattr(merge_drafts, "PADJ_TMP", str(work))
    assert merge_drafts.main([
        str(store), "test-co", "--judged", "2026-09-29",
        "--resolver-run", str(run), "--write",
    ]) == 0
    stored = json.loads(store.read_text())["way/1"]
    registry = resolve_trust.replay_trust._load_publication_proofs(
        store.parent, store.name
    )
    proof_sha = stored["publication_attestation"]["publication_proof_sha256"]
    original = resolve_trust.replay_trust._load_proof_manifest(
        proof_sha, registry
    )

    cases = []
    malformed_shapes = (
        ("human_authority", []),
        ("human_reviews", []),
        ("packet_bindings", []),
        ("final_rows", []),
    )
    for field, malformed in malformed_shapes:
        manifest = copy.deepcopy(original)
        manifest[field] = malformed
        cases.append((copy.deepcopy(stored), manifest))
    manifest = copy.deepcopy(original)
    manifest["packet_bindings"]["1"]["tile_sha256"] = []
    cases.append((copy.deepcopy(stored), manifest))
    for terminal_fids in (None, [None], [True], [[1]], [{"fid": 10 ** 1000}]):
        manifest = copy.deepcopy(original)
        old_ref = manifest["chunk_receipts"]["0"]
        receipt = json.loads(resolve_trust.replay_trust._load_manifest_object(
            manifest, registry, old_ref
        ))
        receipt["terminal_fids"] = terminal_fids
        new_ref = _stage_replacement_object(
            manifest, registry, old_ref, resolve_trust._json_bytes(receipt),
            tmp_path / f"malformed-receipt-{len(cases)}",
        )
        manifest["chunk_receipts"]["0"] = new_ref
        row = copy.deepcopy(stored)
        row["publication_attestation"]["terminal_receipt_sha256"] = (
            tr.sha256_json(receipt)
        )
        cases.append((row, manifest))
    for terminal_rows in (None, True, [None, True, [1], {"fid": 10 ** 1000}]):
        manifest = copy.deepcopy(original)
        old_ref = manifest["terminal_drafts"]["0"]
        new_ref = _stage_replacement_object(
            manifest, registry, old_ref,
            resolve_trust._json_bytes(terminal_rows),
            tmp_path / f"malformed-rows-{len(cases)}",
        )
        manifest["terminal_drafts"]["0"] = new_ref
        cases.append((copy.deepcopy(stored), manifest))

    for index, (row, manifest) in enumerate(cases):
        manifest["object_closure_sha256"] = (
            merge_drafts.publication_objects.object_closure_sha256(
                manifest["objects"].values()
            )
        )
        forged_registry = _forge_v4_manifest(
            row, registry, manifest, tmp_path / f"malformed-proof-{index}"
        )
        errors = resolve_trust.replay_trust._validate_publication_proof(
            row, forged_registry
        )
        assert errors
        assert isinstance(errors, list)


def test_prepare_holds_exclusive_area_lock_while_writing_captures_and_run(
        tmp_path, monkeypatch):
    work, *_ = _workspace(tmp_path / "workspace")
    real_flock = resolve_trust.fcntl.flock
    real_write = resolve_trust._write_idempotent
    current_mode = None
    observed_writes = []

    def observe_lock(fd, mode):
        nonlocal current_mode
        current_mode = mode
        return real_flock(fd, mode)

    def observe_write(path, data):
        observed_writes.append((Path(path), current_mode))
        return real_write(path, data)

    monkeypatch.setattr(resolve_trust.fcntl, "flock", observe_lock)
    monkeypatch.setattr(resolve_trust, "_write_idempotent", observe_write)
    resolve_trust.prepare(
        "test-co", work, _models(), tmp_path / "runs"
    )
    assert observed_writes
    assert all(mode == resolve_trust.fcntl.LOCK_EX
               for _path, mode in observed_writes)


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS ACL command required")
def test_macos_extended_acl_is_rejected_without_replacement_or_acl_removal(
        tmp_path):
    import pwd
    import subprocess

    target = tmp_path / "canonical.json"
    target.write_bytes(b"original")
    target.chmod(0o600)
    principal = pwd.getpwuid(os.geteuid()).pw_name
    subprocess.run([
        "chmod", "+a", f"{principal} allow read,write", str(target),
    ], check=True, capture_output=True)
    fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        with pytest.raises(ValueError, match="nontrivial or inherited ACL"):
            resolve_trust.trusted_fs.require_trivial_acl_fd(fd, target)
    finally:
        os.close(fd)
    with pytest.raises(ValueError, match="nontrivial or inherited ACL"):
        resolve_trust.trusted_fs.atomic_write_bytes(target, b"replacement")
    assert target.read_bytes() == b"original"
    listing = subprocess.run(
        ["ls", "-le", str(target)], check=True, capture_output=True, text=True
    ).stdout
    assert principal in listing and "allow read,write" in listing


@pytest.mark.parametrize(
    "failure", ["unsupported", "query-error", "nontrivial", "linux-nontrivial"]
)
def test_acl_boundary_fails_closed_before_mutation_on_platform_or_query_failure(
        tmp_path, monkeypatch, failure):
    trusted = resolve_trust.trusted_fs
    target = tmp_path / "new-canonical.json"
    if failure == "unsupported":
        monkeypatch.setattr(trusted, "_acl_platform", lambda: "unsupported-os")
        expected = OSError
    elif failure == "linux-nontrivial":
        monkeypatch.setattr(trusted, "_acl_platform", lambda: "linux")
        monkeypatch.setattr(trusted, "_require_acl_platform_support", lambda: None)
        monkeypatch.setattr(
            trusted, "_linux_acl_is_trivial",
            lambda _fd, _is_directory: False,
        )
        expected = ValueError
    else:
        monkeypatch.setattr(trusted, "_acl_platform", lambda: "darwin")
        monkeypatch.setattr(trusted, "_require_acl_platform_support", lambda: None)
        if failure == "query-error":
            def fail_query(_fd, _is_directory):
                raise OSError("ACL query failed")
            monkeypatch.setattr(trusted, "_darwin_acl_is_trivial", fail_query)
            expected = OSError
        else:
            monkeypatch.setattr(
                trusted, "_darwin_acl_is_trivial",
                lambda _fd, _is_directory: False,
            )
            expected = ValueError
    with pytest.raises(expected):
        trusted.atomic_write_bytes(target, b"must not be installed")
    assert not target.exists()


def test_generation_change_rotates_prepare_run_and_assignment_identity(tmp_path):
    work, *_ = _workspace(tmp_path / "workspace")
    run_root = tmp_path / "runs"
    first = resolve_trust.prepare("test-co", work, _models(), run_root)
    first_prepare = resolve_trust.load_prepare_document(first / "prepare.json")
    _advance_context_generation(work)
    current_generation = _commit_generation_and_rebind(work)
    second = resolve_trust.prepare("test-co", work, _models(), run_root)
    second_prepare = resolve_trust.load_prepare_document(second / "prepare.json")

    assert first != second
    assert first_prepare["source"]["source_generation"] != current_generation
    assert second_prepare["source"]["source_generation"] == current_generation
    for item in second_prepare["items"]:
        for assignment in item["assignments"].values():
            assert assignment["source_generation"] == current_generation
    assert (
        first_prepare["items"][0]["assignments"]["challenger"]["assignment_id"]
        != second_prepare["items"][0]["assignments"]["challenger"]["assignment_id"]
    )


def test_prepare_rejects_packet_live_generation_mismatch_before_run_mutation(
        tmp_path):
    work, draft, checkpoint, _rows, _packets = _workspace(
        tmp_path / "workspace"
    )
    packet_path = work / "test-co_packets.json"
    packets = json.loads(packet_path.read_text())
    packets["1"]["source_generation"]["manifest_sha256"] = "f" * 64
    packet_path.write_text(json.dumps(packets))
    before = (draft.read_bytes(), checkpoint.read_bytes())
    run_root = tmp_path / "runs"

    with pytest.raises(ValueError, match="generation differs from current capture"):
        resolve_trust.prepare("test-co", work, _models(), run_root)
    assert not run_root.exists()
    assert not (work / ".trust-resolver-captures").exists()
    assert (draft.read_bytes(), checkpoint.read_bytes()) == before


def test_apply_generation_drift_has_zero_authority_mutation(tmp_path):
    run, work, draft, checkpoint, rows, _packets, _before = _prepared(tmp_path)
    _write_output(run, 1, "challenger", copy.deepcopy(rows[0]))
    assert resolve_trust.evaluate(run)[0]["state"] == "READY"
    protected_before = {
        path: path.read_bytes() for path in sorted(run.rglob("*")) if path.is_file()
    }
    canonical_before = (draft.read_bytes(), checkpoint.read_bytes())
    _advance_context_generation(work)

    for apply in (False, True):
        with pytest.raises(ValueError, match="source generation changed after prepare"):
            resolve_trust.apply_chunk(run, 0, apply=apply)
    assert (draft.read_bytes(), checkpoint.read_bytes()) == canonical_before
    assert not (run / "output-seal.json").exists()
    assert {
        path: path.read_bytes() for path in protected_before
    } == protected_before


def test_terminal_publication_generation_drift_has_zero_mutation(
        tmp_path, monkeypatch, capsys):
    run, work, draft, checkpoint, rows, _packets, _before = _prepared(tmp_path)
    _write_output(run, 1, "challenger", copy.deepcopy(rows[0]))
    assert resolve_trust.apply_chunk(run, 0, apply=True)["state"] == "APPLIED"
    canonical_before = (draft.read_bytes(), checkpoint.read_bytes())
    _advance_context_generation(work)
    store = tmp_path / "store.json"
    proof = merge_drafts.publication_proof_path(store)
    monkeypatch.setattr(merge_drafts, "PADJ_TMP", str(work))

    assert merge_drafts.main([
        str(store), "test-co", "--resolver-run", str(run),
        "--judged", "2026-09-29", "--write",
    ]) == 1
    assert "source generation changed after prepare" in capsys.readouterr().out
    assert (draft.read_bytes(), checkpoint.read_bytes()) == canonical_before
    assert not store.exists() and not proof.exists()


def test_prepare_holds_inventory_then_area_through_completed_reload(
        tmp_path, monkeypatch):
    work, *_ = _workspace(tmp_path / "workspace")
    inventory_held = False
    area_held = False
    events = []
    real_locked = resolve_trust.trusted_fs.locked_resources
    real_open = resolve_trust._open_lock_file
    real_write = resolve_trust._write_idempotent
    real_load = resolve_trust.load_prepare_document

    @contextlib.contextmanager
    def observed_locked(*args, **kwargs):
        nonlocal inventory_held
        events.append("inventory")
        with real_locked(*args, **kwargs) as entries:
            inventory_held = True
            try:
                yield entries
            finally:
                inventory_held = False

    @contextlib.contextmanager
    def observed_open(path):
        nonlocal area_held
        assert inventory_held
        events.append("area")
        with real_open(path) as handle:
            area_held = True
            try:
                yield handle
            finally:
                area_held = False

    def observed_write(path, data):
        assert inventory_held and area_held
        return real_write(path, data)

    def observed_load(*args, **kwargs):
        assert inventory_held and area_held
        events.append("reload")
        return real_load(*args, **kwargs)

    monkeypatch.setattr(
        resolve_trust.trusted_fs, "locked_resources", observed_locked
    )
    monkeypatch.setattr(resolve_trust, "_open_lock_file", observed_open)
    monkeypatch.setattr(resolve_trust, "_write_idempotent", observed_write)
    monkeypatch.setattr(resolve_trust, "load_prepare_document", observed_load)
    resolve_trust.prepare("test-co", work, _models(), tmp_path / "runs")
    assert events[0:2] == ["inventory", "area"]
    assert events[-1] == "reload"
    assert not inventory_held and not area_held


def test_live_status_rejects_packet_prepare_generation_mismatch_without_mutation(
        tmp_path):
    run, work, draft, checkpoint, _rows, _packets, _before = _prepared(tmp_path)
    packet_path = work / "test-co_packets.json"
    packets = json.loads(packet_path.read_text())
    packets["1"]["source_generation"]["artifact_sha256"]["serves"] = "f" * 64
    packet_path.write_text(json.dumps(packets))
    before = (draft.read_bytes(), checkpoint.read_bytes())

    with pytest.raises(ValueError, match="generation differs from current capture"):
        resolve_trust.evaluate(run)
    assert (draft.read_bytes(), checkpoint.read_bytes()) == before
    assert not (run / "output-seal.json").exists()


def test_prepare_rejects_checkpoint_value_independent_of_frozen_checkpoint(tmp_path):
    run, *_ = _prepared(tmp_path)
    document = json.loads((run / "prepare.json").read_text())
    source = document["source"]["drafts"][0]
    source["checkpoint_value"]["decision_sha256"] = "f" * 64
    document["run_id"] = resolve_trust._run_identity(document)

    with pytest.raises(ValueError, match="checkpoint invalid"):
        resolve_trust.validate_prepare_document(document)


def test_completed_prepare_compares_item_packet_to_frozen_source_map(
        tmp_path, monkeypatch):
    run, *_ = _prepared(tmp_path)
    prepare_path = run / "prepare.json"
    document = json.loads(prepare_path.read_text())
    source_packets_path = run / document["source"]["packets_frozen_path"]
    source_packets = json.loads(source_packets_path.read_text())
    source_packets["1"]["tags_union"]["coherent_forgery"] = "different"
    source_packet_bytes = json.dumps(
        source_packets, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    source_packets_path.write_bytes(source_packet_bytes)
    document["source"]["packets_file_sha256"] = resolve_trust._sha(
        source_packet_bytes
    )
    prepare_path.write_bytes(resolve_trust._json_bytes(document))

    # Isolate the cross-image check from the earlier run-id gate. All genuine
    # assignment and per-item packet artifacts remain untouched.
    monkeypatch.setattr(
        resolve_trust, "_run_identity", lambda value: value["run_id"]
    )
    with pytest.raises(ValueError, match="frozen packet payload differs"):
        resolve_trust.load_prepare_document(prepare_path)


def test_publication_proof_version_matrix_preserves_historical_review_v2():
    compatibility = resolve_trust.replay_trust._publication_proof_compatibility
    assert compatibility(1) == {
        "prepare_version": resolve_trust.LEGACY_PREPARE_VERSION,
        "human_receipt_version": 1,
        "attestation_version": 1,
        "human_reviews": False,
    }
    assert compatibility(2) == {
        "prepare_version": resolve_trust.LEGACY_PREPARE_VERSION,
        "human_receipt_version": merge_drafts.PATH_BOUND_AUTHORITY_RECEIPT_VERSION,
        "attestation_version": 2,
        "human_reviews": True,
    }
    assert compatibility(3) == {
        "prepare_version": resolve_trust.PATH_BOUND_PREPARE_VERSION,
        "human_receipt_version": merge_drafts.PATH_BOUND_AUTHORITY_RECEIPT_VERSION,
        "attestation_version": 2,
        "human_reviews": True,
    }
    assert compatibility(True) is None
    assert compatibility(4) is None


def test_embedded_publication_prepare_must_match_outer_proof_version(tmp_path):
    run, *_ = _prepared(tmp_path)
    prepare_bytes = (run / "prepare.json").read_bytes()
    parse_embedded = resolve_trust.replay_trust._parse_embedded_proof_prepare

    parsed = parse_embedded(
        prepare_bytes, resolve_trust, resolve_trust.PREPARE_VERSION
    )
    assert parsed["version"] == resolve_trust.PREPARE_VERSION
    with pytest.raises(ValueError, match="does not match outer proof"):
        parse_embedded(
            prepare_bytes, resolve_trust,
            resolve_trust.LEGACY_PREPARE_VERSION,
        )
