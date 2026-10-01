"""Tests for the judge fan-out tooling in scripts/parking-adjud/tools:
merge_drafts.py (the tool that writes the committed verdict stores, and the
human's --set pen), calibration.py (the human-vs-judge ledger), and the
one-source rule for the judge lessons."""
from __future__ import annotations

import base64
import contextlib
import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent
TOOLS = HERE / "parking-adjud" / "tools"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


merge_drafts = _load("merge_drafts", TOOLS / "merge_drafts.py")
calibration = _load("calibration", TOOLS / "calibration.py")
review_sheet = _load("judge_review_sheet_tests", TOOLS / "judge_review_sheet.py")
import _parking_verdict_source as verdict_source  # noqa: E402
import dossier_output  # noqa: E402

RING = [[40.0, -105.5], [40.0001, -105.5], [40.0001, -105.4999], [40.0, -105.4999], [40.0, -105.5]]


def _synthetic_source_generation(slug="test-co"):
    return {
        "manifest_path": f"{slug}_generation.json",
        "manifest_sha256": "a" * 64,
        "artifact_sha256": {
            "dossier": "b" * 64,
            "serves": "c" * 64,
            "context": "d" * 64,
            "walk": "e" * 64,
        },
    }


def _install_generation(root: Path, slug: str, facilities: list[dict],
                        serves: dict, context: dict, walk: dict):
    (root / f"{slug}_dossier.json").write_text(json.dumps({
        "slug": slug, "facilities": facilities,
    }))
    for suffix, value in (
        ("serves2", serves), ("context", context), ("walk", walk),
    ):
        (root / f"{slug}_{suffix}.json").write_text(json.dumps(value))
    capture = dossier_output.bootstrap_generation(root, slug)
    return capture, dossier_output.portable_source_generation(capture, slug)


def _production_packets(module, root: Path, fids: list[int], slug="test-co"):
    facilities = []
    serves = {}
    context = {}
    walk = {}
    ladder = root / f"{slug}_ladder"
    ladder.mkdir(parents=True, exist_ok=True)
    for fid in fids:
        facilities.append({
            "fid": fid, "lat": 40.0 + fid / 1000, "lon": -105.5,
            "osm": [f"way/{fid}"], "prior": "bare", "ring": RING,
            "rings": [RING], "tags_union": {}, "members": [],
            "area_m2": 100, "trailhead_nodes": [], "footways_60m": 0,
            "building_overlap": False,
        })
        serves[str(fid)] = {
            "served": True, "trail": "Test Trail", "dist_m": 10,
            "fallback": False, "n": 1,
        }
        context[str(fid)] = {
            "category": "NEUTRAL", "evidence": "", "fac_label": None,
            "fac_area_m": None,
        }
        walk[str(fid)] = {"walk_m": 10, "conn": "", "trail": None}
        for zoom in ("z1", "z2", "z3"):
            (ladder / f"{fid:04d}_{zoom}.png").write_bytes(
                b"\x89PNG\r\n\x1a\n" + f"tile-{fid}-{zoom}".encode()
            )
    capture, _source = _install_generation(
        root, slug, facilities, serves, context, walk
    )
    return module.build_packets(slug, str(root), None, capture)


_REAL_TERMINAL_GATE = merge_drafts._terminal_resolver_errors
_REAL_RESOLVER_DATA = merge_drafts.resolver.DATA


def _install_empty_publication_floor(store_path: Path) -> Path:
    path = merge_drafts.publication_floor_path(store_path)
    if not path.exists():
        document = verdict_source.build_empty_publication_floor_document(
            store_path.name
        )
        path.write_bytes(verdict_source.publication_floor_json_bytes(document))
    return path


def _install_rooted_publication_configuration(
        data_dir: Path, store_names: list[str], monkeypatch):
    """Install a strict one-generation test root and production-like config."""
    specs = tuple(
        verdict_source.StoreSpec(name, verdict_source.OSM_KEY, None, None)
        for name in store_names
    )
    store_bytes = {
        spec.filename: (data_dir / spec.filename).read_bytes()
        for spec in specs
    }
    dossiers = {
        path.name: path.read_bytes()
        for path in sorted(data_dir.glob("*_dossier.json"))
        if path.is_file() and not path.is_symlink()
    }
    baseline_document = verdict_source.build_legacy_baseline_document(
        specs, store_bytes, dossiers
    )
    baseline_path = data_dir / "test-proofless-source-baseline-v1.json"
    baseline_bytes = verdict_source.legacy_baseline_json_bytes(
        baseline_document
    )
    baseline_path.write_bytes(baseline_bytes)
    proof_bytes = {}
    floor_bytes = {}
    for spec in specs:
        proof_path = merge_drafts.publication_proof_path(
            data_dir / spec.filename
        )
        proof_bytes[spec.filename] = (
            proof_path.read_bytes() if proof_path.exists() else None
        )
        floor_bytes[spec.filename] = _install_empty_publication_floor(
            data_dir / spec.filename
        ).read_bytes()
    root_document = verdict_source.build_publication_trust_root_document(
        specs, store_bytes, proof_bytes, floor_bytes, baseline_path.name,
        baseline_bytes, dossiers,
    )
    root_path = data_dir / "publication-trust-root-v1.json"
    root_bytes = verdict_source.publication_trust_root_json_bytes(root_document)
    root_path.write_bytes(root_bytes)
    configuration = SimpleNamespace(
        STORES=[(spec.filename, None, None) for spec in specs],
        STORE_KEY_KINDS={
            spec.filename: spec.key_kind for spec in specs
        },
        DATA_PATH=str(data_dir.resolve()),
        LEGACY_ROW_BASELINE_PATH=str(baseline_path),
        LEGACY_ROW_BASELINE_SHA256=baseline_document["baseline_sha256"],
        PUBLICATION_TRUST_ROOT_PATH=str(root_path),
        PUBLICATION_TRUST_ROOT_SHA256=hashlib.sha256(root_bytes).hexdigest(),
    )
    builder_module = SimpleNamespace(
        **vars(configuration),
        _builder_configuration=lambda: configuration,
    )
    monkeypatch.setattr(
        merge_drafts.resolver.replay_trust, "_load_builder",
        lambda: builder_module,
    )
    return configuration, root_path, root_bytes


@pytest.fixture(autouse=True)
def _legacy_merge_fixtures_bypass_only_the_cli_terminal_gate(
        tmp_path, monkeypatch):
    data = tmp_path / ".resolver-data"
    data.mkdir(mode=0o700, exist_ok=True)
    (data / "calibration.json").write_bytes(
        (_REAL_RESOLVER_DATA / "calibration.json").read_bytes()
    )
    monkeypatch.setattr(merge_drafts.resolver, "DATA", data)
    monkeypatch.setattr(
        merge_drafts, "_terminal_resolver_errors",
        lambda tmp, slug, drafts, store, run_path, generation_capture: ([], None),
    )
    _install_empty_publication_floor(tmp_path / "store.json")


def _verdict(fid, verdict, confidence="certain", osm=None, hint=None, prior="bare"):
    return {"fid": fid, "area": "test-co", "osm": osm or [f"way/{fid}"], "verdict": verdict,
            "prior": prior, "confidence": confidence, "frames_used": ["z1", "z2", "z3"],
            "exists": {"call": "yes", "evidence": "Z2: cars"},
            "public": {"call": "yes", "evidence": "no access tag"},
            "serves": {"call": "no" if verdict == "DROP" else ("unclear" if verdict == "REVIEW" else "yes"),
                       "evidence": "Trail edge 50 m"},
            "tags_cited": {}, "resolve_hint": hint}


def _area(tmp_path, slug="test-co", drafts=None):
    """A minimal PADJ_TMP: dossier + _pub.txt + chunk drafts."""
    tmp = tmp_path / "work"
    tmp.mkdir(exist_ok=True)
    drafts = drafts if drafts is not None else [[_verdict(1, "KEEP"), _verdict(2, "DROP", "strong")],
                                                [_verdict(3, "REVIEW", "leaning", hint="fetch a clear frame")]]
    fids = [e["fid"] for d in drafts for e in d]
    by_fid = {e["fid"]: e for d in drafts for e in d}
    facilities = [{"fid": f, "lat": 40.0 + f * 1e-3, "lon": -105.5,
                   "osm": by_fid[f]["osm"], "prior": by_fid[f]["prior"],
                   "ring": RING, "rings": [RING], "tags_union": {"name": f"Lot {f}"}}
                  for f in fids]
    serves_doc = {
        str(fid): {"served": True, "trail": "Test Trail", "dist_m": 50,
                   "fallback": False, "n": 1}
        for fid in fids
    }
    context_doc = {
        str(fid): {"category": "NEUTRAL", "evidence": "",
                   "fac_label": None, "fac_area_m": None}
        for fid in fids
    }
    walk_doc = {
        str(fid): {"walk_m": 50, "conn": "path", "trail": "Test Trail"}
        for fid in fids
    }
    _capture, source_generation = _install_generation(
        tmp, slug, facilities, serves_doc, context_doc, walk_doc
    )
    (tmp / f"{slug}_pub.txt").write_text(",".join(str(f) for f in fids))
    packets = {}
    for fid in fids:
        tiles = {}
        ladder = tmp / f"{slug}_ladder"
        ladder.mkdir(parents=True, exist_ok=True)
        for zoom in ("z1", "z2", "z3"):
            tile = ladder / f"{fid:04d}_{zoom}.png"
            tile.write_bytes(b"\x89PNG\r\n\x1a\n" + f"tile-{fid}-{zoom}".encode())
            tiles[zoom] = str(tile)
        packets[str(fid)] = {
            "fid": fid, "area": slug,
            "source_generation": copy.deepcopy(source_generation),
            "osm": by_fid[fid]["osm"],
            "prior": by_fid[fid]["prior"], "tags_union": {}, "members": [],
            "mixed_access": False, "footprint": "polygon", "area_m2": 100,
            "serves": {"trail": "Test Trail", "edge_m": 50, "fallback": False,
                       "n_trails_in_range": 1},
            "walk": {"walk_m": 50, "conn": "path", "trail": "Test Trail"},
            "trailhead_nodes_120m": [], "footways_60m": 1,
            "building_overlap": False,
            "context": {"category": "NEUTRAL", "evidence": "", "facility": None,
                        "facility_edge_m": None},
            "tiles": tiles,
        }
    (tmp / f"{slug}_packets.json").write_text(json.dumps(packets))
    for chunk_index, rows in enumerate(drafts):
        draft_path = tmp / f"{slug}_verdict_draft_{chunk_index:02d}.json"
        draft_path.write_text(json.dumps(rows))
        chunk_packets = [packets[str(row["fid"])] for row in rows]
        state = merge_drafts.judge_packets.inspect_draft(chunk_packets, draft_path)
        decision_input, missing = merge_drafts.judge_packets.decision_fingerprint(chunk_packets)
        if state["status"] == "complete" and not missing:
            manifest = merge_drafts.judge_packets.manifest_value(
                slug, chunk_index, chunk_packets, decision_input, state
            )
            (tmp / f"{slug}_checkpoint_{chunk_index:02d}.json").write_text(
                json.dumps(manifest)
            )
    merge_drafts.PADJ_TMP = str(tmp)
    calibration.PADJ_TMP = str(tmp)
    review_sheet.PADJ_TMP = str(tmp)
    return tmp


def _drafts(tmp, slug="test-co"):
    return {e["fid"]: e for e in merge_drafts.load_draft(Path(tmp), slug)}


def _apply_legacy_overrides(tmp, slug, sets, note, today):
    staged = {}
    lines = merge_drafts.apply_overrides(
        tmp, slug, sets, note, today, staged
    )
    for path, rows in staged.items():
        merge_drafts._dump_atomic(path, rows)
    return lines


def _write_authority_run(tmp_path, tmp, fid, decision, packet, name,
                         render_review=True):
    models = {
        "primary": merge_drafts.tr.model_config("primary-model", "primary-family"),
        "challenger": merge_drafts.tr.model_config("challenger-model", "challenger-family"),
        "arbiter": merge_drafts.tr.model_config("arbiter-model", "arbiter-family"),
    }
    authority_run = merge_drafts.resolver.prepare(
        "test-co", Path(tmp), models, tmp_path / name
    )
    prepare = merge_drafts.resolver.load_prepare_document(
        authority_run / "prepare.json", expected_area="test-co"
    )
    item = next(row for row in prepare["items"] if row["fid"] == fid)
    assert item["packet_sha256"] == merge_drafts.tr.packet_sha256(packet)
    assert merge_drafts.tr.decision_projection(item["primary"]["decision"]) == (
        merge_drafts.tr.decision_projection(decision)
    )
    if render_review:
        review_sheet.PADJ_TMP = str(tmp)
        assert review_sheet.main([
            "test-co", "--authority-run", str(authority_run), "--sample", "0",
        ]) == 0
    return authority_run, prepare["run_id"]


def _review_receipt(authority_run):
    prepare = json.loads((Path(authority_run) / "prepare.json").read_text())
    return review_sheet.review.artifact_paths(
        prepare["tmp"], prepare["area"], prepare["run_id"]
    )["receipt"]


def _terminal_machine_run(tmp_path, tmp, fid, decision, packet, name):
    run, run_id = _write_authority_run(
        tmp_path, tmp, fid, decision, packet, name
    )
    prepare = json.loads((run / "prepare.json").read_text())
    item = next(value for value in prepare["items"] if value["fid"] == fid)
    assignment = item["assignments"]["challenger"]
    output = run / assignment["output_path"]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({
        "assignment_id": assignment["assignment_id"],
        "decision": item["primary"]["decision"],
        "external_evidence": [],
    }))
    status = merge_drafts.resolver.apply_chunk(run, item["chunk"], apply=True)
    assert status["state"] == "APPLIED"
    return run, run_id


def _test_publication_authorization(tmp_path, tag, fids=(1,)):
    digest = lambda value: merge_drafts.tr.sha256_json({"test": value})
    return {
        "version": merge_drafts.PUBLICATION_AUTHORIZATION_VERSION,
        "kind": merge_drafts.PUBLICATION_AUTHORIZATION_KIND,
        "area": "test-co",
        "resolver_run_path": str((tmp_path / f"resolver-{tag}").resolve()),
        "resolver_run_id": digest(f"run-{tag}"),
        "resolver_prepare_sha256": digest(f"prepare-{tag}"),
        "output_seal_sha256": digest(f"seal-{tag}"),
        "terminal_receipts": [{
            "chunk": 0,
            "kind": "parking-trust-receipt",
            "transaction_id": digest(f"transaction-{tag}"),
            "receipt_sha256": digest(f"receipt-{tag}"),
        }],
        "publication_fids": list(fids),
        "publication_proof_sha256": digest(f"proof-{tag}"),
        "human_review_receipts": [],
    }


def _explicit_publication_baselines(monkeypatch, rows_by_store):
    """Configure exact controlled legacy rows for low-level transaction tests."""
    policies = {}
    for store_name, rows in rows_by_store.items():
        spec = verdict_source.StoreSpec(
            store_name, verdict_source.OSM_KEY, None, None
        )
        raw = json.dumps(
            rows, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        document = verdict_source.build_legacy_baseline_document(
            (spec,), {store_name: raw}, {}
        )
        baseline = verdict_source.parse_legacy_baseline(
            verdict_source.legacy_baseline_json_bytes(document),
            (spec,),
            document["baseline_sha256"],
        )
        policies[store_name] = (spec, baseline)

    def policy(store_name):
        return policies.get(store_name, (
            verdict_source.StoreSpec(
                store_name, verdict_source.OSM_KEY, None, None
            ),
            None,
        ))

    monkeypatch.setattr(
        merge_drafts.resolver.replay_trust,
        "publication_store_policy",
        policy,
    )


def _stage_forged_publication(store_path, store_after, proof_after,
                              authorization):
    proof_path = merge_drafts.publication_proof_path(store_path)
    _install_empty_publication_floor(store_path)
    plan = merge_drafts._build_publication_plan(
        store_path, store_after, proof_path, proof_after, authorization
    )
    assert not plan.recovering
    for prefix in ("store", "proof", "floor"):
        before = plan.before_bytes[prefix]
        if before is not None:
            merge_drafts.resolver._atomic_bytes(
                plan.paths[f"{prefix}_backup"], before
            )
        merge_drafts.resolver._atomic_bytes(
            plan.paths[f"{prefix}_stage"], plan.after_bytes[prefix]
        )
    merge_drafts.resolver._atomic_json(plan.paths["journal"], plan.journal)
    return plan


# ---------------------------------------------------------------- merge_drafts


def test_legacy_set_helper_replays_original_call_without_losing_it(tmp_path):
    tmp = _area(tmp_path)
    lines = _apply_legacy_overrides(
        tmp, "test-co", {1: "DROP"}, "sheet review", "2026-09-13"
    )
    assert not any("!!" in line for line in lines)
    entry = _drafts(tmp)[1]
    assert entry["verdict"] == "DROP" and entry["confidence"] == "strong"
    assert entry["override"] == {
        "from": "KEEP", "by": "human", "date": "2026-09-13",
        "note": "sheet review", "resolve_hint": None,
        "confidence_from": "certain",
    }
    _apply_legacy_overrides(
        tmp, "test-co", {1: "REVIEW"}, "second look", "2026-09-13"
    )
    entry = _drafts(tmp)[1]
    assert entry["verdict"] == "REVIEW" and entry["override"]["from"] == "KEEP"
    assert entry["resolve_hint"] == "second look"
    lines = _apply_legacy_overrides(
        tmp, "test-co", {1: "KEEP"}, None, "2026-09-13"
    )
    entry = _drafts(tmp)[1]
    assert entry["verdict"] == "KEEP" and "override" not in entry
    assert any("back to the judge's call" in line for line in lines)


def test_set_cli_is_disabled_and_cannot_mutate_a_draft(tmp_path):
    tmp = _area(tmp_path)
    store = tmp_path / "store.json"
    before = (tmp / "test-co_verdict_draft_00.json").read_bytes()
    with pytest.raises(SystemExit, match="--set is disabled"):
        merge_drafts.main([str(store), "test-co", "--set", "1=DROP"])
    assert (tmp / "test-co_verdict_draft_00.json").read_bytes() == before
    assert not store.exists()


def test_confirm_records_hash_bound_authority_without_inventing_a_flip(tmp_path):
    tmp = _area(tmp_path)
    store = tmp_path / "store.json"
    note = "user accepted the conservative DROP recommendation"
    packets_path = tmp / "test-co_packets.json"
    packets = json.loads(packets_path.read_text())
    packet = packets["2"]
    packet["tiles"] = {}
    for zoom in ("z1", "z2", "z3"):
        tile = tmp / "test-co_ladder" / f"0002_{zoom}.png"
        tile.write_bytes(b"\x89PNG\r\n\x1a\n" + f"tile-2-{zoom}".encode())
        packet["tiles"][zoom] = str(tile)
    packets_path.write_text(json.dumps(packets))
    original = _drafts(tmp)[2]
    authority_run, source_run_id = _write_authority_run(
        tmp_path, tmp, 2, original, packet, "authority-run"
    )
    args = [
        str(store), "test-co", "--judged", "2026-09-27",
        "--confirm", "2=DROP", "--note", note,
        "--reviewer", "trekdex-project-owner",
        "--authority-run", str(authority_run),
        "--review-receipt", str(_review_receipt(authority_run)),
    ]
    assert merge_drafts.main(args) == 0
    entry = _drafts(tmp)[2]
    assert entry["verdict"] == "DROP" and entry["confidence"] == "strong"
    assert "override" not in entry
    confirmation = entry["human_confirmation"]
    receipt_sha = confirmation["authority_receipt_sha256"]
    review_receipt_value = json.loads(_review_receipt(authority_run).read_text())
    reviewed_item = review_sheet.review.review_item(review_receipt_value, 2)
    assert confirmation == {
        "version": merge_drafts.CONFIRMATION_VERSION,
        "verdict": "DROP",
        "by": "human",
        "reviewer": "trekdex-project-owner",
        "date": "2026-09-27",
        "note": note,
        "decision_sha256": merge_drafts.tr.sha256_json(
            merge_drafts.tr.decision_projection(original)
        ),
        "evidence_sha256": merge_drafts.tr.evidence_sha256(original),
        "packet_sha256": merge_drafts.tr.packet_sha256(packet),
        "source_run_id": source_run_id,
        "review_receipt_sha256": review_receipt_value["receipt_sha256"],
        "review_sheet_sha256": review_receipt_value["sheet_sha256"],
        "review_item_sha256": reviewed_item["review_item_sha256"],
        "authority_receipt_sha256": receipt_sha,
    }
    assert merge_drafts.authority_receipt_path(tmp, "test-co", receipt_sha).is_file()
    # One-file confirmation is idempotent; changed packet bytes fail before rewrite.
    before = (tmp / "test-co_verdict_draft_00.json").read_bytes()
    assert merge_drafts.main(args) == 0
    assert (tmp / "test-co_verdict_draft_00.json").read_bytes() == before
    prepare_path = authority_run / "prepare.json"
    valid_prepare = prepare_path.read_bytes()
    forged = json.loads(valid_prepare)
    forged["models"]["primary"]["id"] = "forged-primary"
    prepare_path.write_text(json.dumps(forged))
    assert merge_drafts.main(args) == 1
    assert (tmp / "test-co_verdict_draft_00.json").read_bytes() == before
    assert not store.exists()
    prepare_path.write_bytes(valid_prepare)
    Path(packet["tiles"]["z3"]).write_bytes(b"changed")
    assert merge_drafts.main(args) == 1
    assert (tmp / "test-co_verdict_draft_00.json").read_bytes() == before
    assert not store.exists()


def test_bound_human_decision_preserves_full_original_and_requires_coherent_axes(tmp_path):
    tmp = _area(tmp_path)
    store = tmp_path / "store.json"
    packets_path = tmp / "test-co_packets.json"
    packets = json.loads(packets_path.read_text())
    packet = packets["3"]
    packet["tiles"] = {}
    for zoom in ("z1", "z2", "z3"):
        tile = tmp / "test-co_ladder" / f"0003_{zoom}.png"
        tile.write_bytes(b"\x89PNG\r\n\x1a\n" + f"tile-3-{zoom}".encode())
        packet["tiles"][zoom] = str(tile)
    packets_path.write_text(json.dumps(packets))
    original = _drafts(tmp)[3]
    authority_run, source_run_id = _write_authority_run(
        tmp_path, tmp, 3, original, packet, "decision-authority-run"
    )
    note = "user rejected public access after reviewing the named exception"
    assert merge_drafts.main([
        str(store), "test-co", "--judged", "2026-09-28",
        "--decide", "3=DROP",
        "--axis", "public=no",
        "--axis-evidence", "public=User found no public parking authorization",
        "--note", note,
        "--reviewer", "trekdex-project-owner",
        "--authority-run", str(authority_run),
        "--review-receipt", str(_review_receipt(authority_run)),
    ]) == 0
    entry = _drafts(tmp)[3]
    assert entry["verdict"] == "DROP" and entry["public"]["call"] == "no"
    assert entry["override"]["version"] == merge_drafts.OVERRIDE_VERSION
    assert entry["override"]["reviewer"] == "trekdex-project-owner"
    assert entry["override"]["source_run_id"] == source_run_id
    errors, projection = merge_drafts.validate_verdict_row(entry, packet)
    assert errors == []
    assert projection == original
    items, corpus, sidecar = merge_drafts.resolver.replay_trust.load_work_area(
        tmp, "test-co"
    )
    report = merge_drafts.resolver.trust_engine.build_report(
        items, {"areas": []}, {}, sidecar, corpus, include_items=True
    )
    decided = next(item for item in report["items"] if item["source_key"] == "3")
    assert decided["route"] == merge_drafts.resolver.trust_engine.ROUTE_AUTHORITY
    before = (tmp / "test-co_verdict_draft_01.json").read_bytes()
    with pytest.raises(SystemExit, match="--set is disabled"):
        merge_drafts.main([str(store), "test-co", "--set", "3=KEEP", "--write"])
    assert (tmp / "test-co_verdict_draft_01.json").read_bytes() == before
    assert not store.exists()

    tampered = copy.deepcopy(entry)
    tampered["public"]["evidence"] = "changed after acceptance"
    errors, _ = merge_drafts.validate_verdict_row(tampered, packet)
    assert any("effective decision hash mismatch" in error for error in errors)


def test_review_flipped_to_drop_nulls_the_hint_and_lands_in_the_store_with_geometry(tmp_path):
    tmp = _area(tmp_path)
    store = tmp_path / "store.json"
    args = [str(store), "test-co", "--judged", "2026-09-13"]
    _apply_legacy_overrides(
        tmp, "test-co", {3: "DROP"}, None, "2026-09-13"
    )
    assert merge_drafts.main(args + ["--write"]) == 0
    s = json.loads(store.read_text())
    e = s["way/3"]
    assert e["verdict"] == "DROP" and e["resolve_hint"] is None
    assert e["override"]["from"] == "REVIEW" and e["override"]["resolve_hint"] == "fetch a clear frame"
    assert e["judged"] == "2026-09-13" and e["area"] == "test-co" and e["name"] == "Lot 3"
    assert (e["lat"], e["lon"]) == (40.003, -105.5) and e["rings"] == [RING]
    assert e["src"] == "judge-fanout"


def test_remerge_keeps_the_first_judged_date_and_is_byte_stable(tmp_path):
    _area(tmp_path)
    store = tmp_path / "store.json"
    assert merge_drafts.main([str(store), "test-co", "--judged", "2026-09-13", "--write"]) == 0
    first = store.read_text()
    assert merge_drafts.main([str(store), "test-co", "--judged", "2026-12-01", "--write"]) == 0
    assert store.read_text() == first
    assert all(v["judged"] == "2026-09-13" for v in json.loads(first).values())


def test_a_lot_already_held_by_another_area_is_refused_on_disagreement_and_kept_on_agreement(tmp_path, capsys):
    _area(tmp_path)
    store = tmp_path / "store.json"
    other = dict(_verdict(1, "DROP", "strong"), area="other-co", judged="2026-09-01", lat=1.0, lon=2.0,
                 rings=[], name=None, src="judge-fanout")
    store.write_text(json.dumps({"way/1": other}))
    rc = merge_drafts.main([str(store), "test-co", "--judged", "2026-09-29", "--write"])
    assert rc == 1 and "other-co already holds way/1 as DROP" in capsys.readouterr().out
    assert json.loads(store.read_text())["way/1"]["area"] == "other-co"        # untouched
    other["verdict"] = "KEEP"
    other["serves"]["call"] = "yes"
    store.write_text(json.dumps({"way/1": other}))
    assert merge_drafts.main([str(store), "test-co", "--judged", "2026-09-13", "--write"]) == 0
    s = json.loads(store.read_text())
    assert s["way/1"]["area"] == "other-co" and s["way/1"]["judged"] == "2026-09-01"   # first record stands
    assert s["way/2"]["area"] == "test-co" and "1 already held by another area, kept" in capsys.readouterr().out


def test_same_verdict_overlap_refuses_to_discard_human_confirmation(tmp_path, capsys):
    tmp = _area(tmp_path)
    draft_path = tmp / "test-co_verdict_draft_00.json"
    rows = json.loads(draft_path.read_text())
    entry = rows[1]
    entry["human_confirmation"] = {
        "version": 1,
        "verdict": "DROP",
        "by": "human",
        "reviewer": "trekdex-project-owner",
        "date": "2026-09-27",
        "note": "confirmed",
        "decision_sha256": merge_drafts.tr.sha256_json(
            merge_drafts.tr.decision_projection(entry)
        ),
        "evidence_sha256": merge_drafts.tr.evidence_sha256(entry),
        "packet_sha256": "a" * 64,
        "source_run_id": "b" * 64,
    }
    draft_path.write_text(json.dumps(rows))
    held = dict(entry)
    held.pop("human_confirmation")
    held.update({"area": "other-co", "judged": "2026-09-01", "lat": 1.0,
                 "lon": 2.0, "rings": [], "name": None, "src": "judge-fanout"})
    store = tmp_path / "store.json"
    store.write_text(json.dumps({"way/2": held}))
    assert merge_drafts.main([str(store), "test-co", "--judged", "2026-09-29", "--write"]) == 1
    assert "different human_confirmation provenance" in capsys.readouterr().out
    assert "human_confirmation" not in json.loads(store.read_text())["way/2"]


def test_bad_judged_date_and_missing_z3_on_a_surveyed_drop_are_refused(tmp_path, capsys):
    import pytest
    d = _verdict(5, "DROP", "strong", prior="surveyed")
    d["frames_used"] = ["z1", "z2"]
    _area(tmp_path, drafts=[[d]])
    store = tmp_path / "store.json"
    with pytest.raises(SystemExit):
        merge_drafts.main([str(store), "test-co", "--judged", "yesterday"])
    assert merge_drafts.main([str(store), "test-co", "--judged", "2026-09-29", "--write"]) == 1
    assert "without Z3" in capsys.readouterr().out and not store.exists()


# ----------------------------------------------------------------- calibration


def test_wilson_upper_bound_and_review_budget():
    w = calibration.wilson_upper
    assert abs(w(0, 38) - 0.0918) < 5e-4          # the Indian Peaks DROP figure
    assert abs(w(1, 10) - 0.4042) < 1e-3
    assert w(5, 5) == 1.0 and w(0, 0) == 1.0
    assert calibration.n_for_target(0.02) == 189 and calibration.n_for_target(0.01) == 381


def test_ledger_scores_the_judges_original_call_and_never_scores_a_review_as_a_miss(tmp_path, capsys):
    tmp = _area(tmp_path)
    store = tmp_path / "store.json"
    # Human flips the REVIEW to DROP and one certain KEEP to DROP, reviews DROPs
    # one by one, accepts KEEPs en bloc.
    lines = _apply_legacy_overrides(
        tmp, "test-co", {3: "DROP", 1: "DROP"}, None, "2026-09-13"
    )
    assert not any("!!" in line for line in lines)
    calibration.LEDGER = str(tmp_path / "calibration.json")
    assert calibration.main(["add", "test-co", "--reviewed", "DROP=each", "--reviewed", "KEEP=en-bloc",
                             "--date", "2026-09-13"]) == 0
    ledger = json.loads(Path(calibration.LEDGER).read_text())
    rec = ledger["areas"][0]
    # Counted as the judge called them, not as they stand after the flips.
    assert rec["judged"] == {"KEEP": {"certain": 1}, "DROP": {"strong": 1}, "REVIEW": {"leaning": 1}}
    assert sorted((f["from"], f["to"]) for f in rec["flips"]) == [("KEEP", "DROP"), ("REVIEW", "DROP")]
    out = capsys.readouterr().out
    keep_row = next(l for l in out.splitlines() if l.startswith("KEEP any"))
    drop_row = next(l for l in out.splitlines() if l.startswith("DROP any"))
    review_row = next(l for l in out.splitlines() if l.startswith("REVIEW (deferred)"))
    # The KEEP flip is surfaced even though KEEPs had no per-lot pass...
    assert "1 flip(s) caught without a per-lot pass" in keep_row
    # ...the DROP class is measured with its own denominator...
    assert " 1        1     0  100.0%" in drop_row
    # ...and the REVIEW resolution is listed, not scored.
    assert "resolved by the human: 1 -> DROP" in review_row
    assert not any(l.startswith(("REVIEW any", "REVIEW leaning")) for l in out.splitlines())
    # Re-adding the area replaces its record rather than appending a duplicate.
    assert calibration.main(["add", "test-co", "--reviewed", "DROP=each", "--date", "2026-09-14"]) == 0
    ledger = json.loads(Path(calibration.LEDGER).read_text())
    assert len(ledger["areas"]) == 1 and ledger["areas"][0]["date"] == "2026-09-14"


def test_sample_mode_requires_receipt_unless_explicit_legacy_replay_is_requested(
        tmp_path):
    tmp = _area(tmp_path, drafts=[[
        _verdict(fid, "KEEP") for fid in range(1, 5)
    ]])
    calibration.LEDGER = str(tmp_path / "calibration.json")
    with pytest.raises(SystemExit, match="sample review requires --review-receipt"):
        calibration.main([
            "add", "test-co", "--reviewed", "KEEP=sample",
            "--date", "2026-09-13",
        ])
    with pytest.raises(SystemExit, match="immutable manifest"):
        calibration.main([
            "add", "test-co", "--reviewed", "KEEP=sample",
            "--legacy-sample-manifest", "--date", "2026-09-13",
        ])
    assert not Path(calibration.LEDGER).exists()


# ---------------------------------------------------------- one source of truth


def test_judge_lessons_are_the_handoffs_section_5_verbatim():
    """judge_lessons.md is what every judge agent reads; the handoff is what
    every human reads. They must not drift apart."""
    handoff = (HERE.parent / "docs" / "parking-adjudication-handoff.md").read_text()
    lessons = (TOOLS / "judge_lessons.md").read_text()
    start = handoff.index("## 5. The eleven load-bearing lessons")
    end = handoff.index("\n## 6.", start)
    assert handoff[start:end].strip() == lessons.strip()


def test_prompt_template_keeps_its_placeholders():
    judge_packets = _load("judge_packets", TOOLS / "judge_packets.py")
    text = judge_packets.render_prompt_template()
    for ph in ("{PROTOCOL_PATH}", "{LESSONS_PATH}", "{CHUNK_PATH}", "{OUT_PATH}",
               "{RESUME_BLOCK}"):
        assert ph in text
    assert not text.startswith("#")                       # front matter stripped
    assert '"unclear"' in text and '"unknown"' in text    # the call vocabulary rule


def _packet(fid, root, osm=None):
    tiles = root / "tiles"
    tiles.mkdir(exist_ok=True)
    paths = {}
    for zoom in ("z1", "z2", "z3"):
        path = tiles / f"{fid:04d}_{zoom}.png"
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + f"tile-{fid}-{zoom}".encode())
        paths[zoom] = str(path)
    return {"fid": fid, "area": "test-co",
            "source_generation": _synthetic_source_generation(),
            "osm": osm or [f"way/{fid}"], "prior": "bare",
            "tags_union": {}, "members": [], "mixed_access": False, "footprint": "polygon",
            "area_m2": 100, "serves": {"trail": "Test Trail", "edge_m": 10,
            "fallback": False, "n_trails_in_range": 1}, "walk": {"walk_m": 10,
            "conn": "", "trail": None}, "trailhead_nodes_120m": [], "footways_60m": 0,
            "building_overlap": False, "context": {"category": "NEUTRAL", "evidence": "",
            "facility": None, "facility_edge_m": None}, "tiles": paths}


def _checkpoint(fid, osm=None, verdict="KEEP", prior="bare"):
    return _verdict(fid, verdict, "strong", osm=osm, prior=prior)


def test_checkpoint_inspection_distinguishes_missing_partial_and_complete(tmp_path):
    judge_packets = _load("judge_packets_states", TOOLS / "judge_packets.py")
    chunk = [_packet(1, tmp_path), _packet(2, tmp_path), _packet(3, tmp_path)]
    path = tmp_path / "test-co_verdict_draft_00.json"
    state = judge_packets.inspect_draft(chunk, path)
    assert state["status"] == "missing" and state["missing"] == [1, 2, 3]

    path.write_text(json.dumps([_checkpoint(1)]))
    state = judge_packets.inspect_draft(chunk, path)
    assert state["status"] == "partial" and state["completed"] == [1] and state["missing"] == [2, 3]
    block = judge_packets.resume_block(state, path)
    assert "RESUME MODE" in block and "NEVER edit" in block and "[2, 3]" in block

    path.write_text(json.dumps([_checkpoint(1), _checkpoint(2), _checkpoint(3)]))
    state = judge_packets.inspect_draft(chunk, path)
    assert state["status"] == "complete" and not state["missing"]


def test_checkpoint_inspection_fails_closed_on_malformed_reordered_or_stale_identity(tmp_path):
    judge_packets = _load("judge_packets_invalid", TOOLS / "judge_packets.py")
    chunk = [_packet(1, tmp_path), _packet(2, tmp_path)]
    path = tmp_path / "test-co_verdict_draft_00.json"

    path.write_text("{not json")
    assert judge_packets.inspect_draft(chunk, path)["status"] == "invalid"
    path.write_text(json.dumps([_checkpoint(2)]))
    state = judge_packets.inspect_draft(chunk, path)
    assert state["status"] == "invalid" and any("packet-order prefix" in e for e in state["errors"])
    path.write_text(json.dumps([_checkpoint(1, osm=["way/999"])]))
    state = judge_packets.inspect_draft(chunk, path)
    assert state["status"] == "invalid" and any("osm does not match" in e for e in state["errors"])


def test_packet_generation_preserves_partial_draft_and_emits_only_pending_prompt(tmp_path, monkeypatch,
                                                                                  capsys):
    judge_packets = _load("judge_packets_main", TOOLS / "judge_packets.py")
    packets = _production_packets(judge_packets, tmp_path, [1, 2, 3])
    args = ["test-co", "--tmp", str(tmp_path), "--chunk", "2", "--status-json"]
    assert judge_packets.main(args) == 0
    status = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [(row["status"], row["missing"]) for row in status] == [
        ("missing", [1, 2]), ("missing", [3])]
    assert "START MODE" in (tmp_path / "test-co_prompt_00.txt").read_text()

    continuation = tmp_path / "test-co_verdict_continue_00.json"
    first_bytes = (json.dumps([_checkpoint(1)], indent=1) + "\n").encode()
    continuation.write_bytes(first_bytes)
    assert judge_packets.main(args) == 0
    capsys.readouterr()
    draft = tmp_path / "test-co_verdict_draft_00.json"
    assert draft.read_bytes() == first_bytes and not continuation.exists()
    assert "RESUME MODE" in (tmp_path / "test-co_prompt_00.txt").read_text()

    prefix = draft.read_bytes()
    continuation.write_text(json.dumps([_checkpoint(2)], indent=1) + "\n")
    assert judge_packets.main(args) == 0
    status = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    merged = draft.read_bytes()
    close = len(prefix.rstrip()) - 1
    assert merged[:close] == prefix[:close]
    assert status[0]["status"] == "complete" and status[0]["prompt"] is None
    complete_prompt = (tmp_path / "test-co_prompt_00.txt").read_text()
    assert "No agent work is scheduled" in complete_prompt and "write ONE" not in complete_prompt


def test_packet_generation_refuses_stale_higher_chunk_without_rewriting_artifacts(tmp_path,
                                                                                   monkeypatch,
                                                                                   capsys):
    judge_packets = _load("judge_packets_stale", TOOLS / "judge_packets.py")
    packets = _production_packets(judge_packets, tmp_path, [1, 2])
    (tmp_path / "test-co_verdict_draft_01.json").write_text(json.dumps([_checkpoint(2)]))

    assert judge_packets.main(["test-co", "--tmp", str(tmp_path), "--chunk", "15"]) == 2
    assert not (tmp_path / "test-co_pub.txt").exists()
    assert "noncanonical/stale draft" in capsys.readouterr().err


def test_shared_validator_accepts_human_override_and_naip_but_rejects_missing_review_hint(tmp_path):
    judge_packets = _load("judge_packets_shared_validation", TOOLS / "judge_packets.py")
    packet = _packet(1, tmp_path)
    row = _checkpoint(1)
    merge_drafts.set_verdict(row, "DROP", "human correction", "2026-09-14")
    path = tmp_path / "test-co_verdict_draft_00.json"
    path.write_text(json.dumps([row]))
    assert judge_packets.inspect_draft([packet], path)["status"] == "complete"

    packet["prior"] = "surveyed"
    row = _checkpoint(1, verdict="DROP", prior="surveyed")
    row["frames_used"] = ["z1", "z2", "z3_naip"]
    path.write_text(json.dumps([row]))
    assert judge_packets.inspect_draft([packet], path)["status"] == "complete"
    row["frames_used"] = ["z1", "z2"]
    path.write_text(json.dumps([row]))
    assert any("without Z3" in error for error in judge_packets.inspect_draft([packet], path)["errors"])

    packet["prior"] = "bare"
    row = _checkpoint(1, verdict="REVIEW")
    row["serves"]["call"] = "unclear"
    path.write_text(json.dumps([row]))
    assert any("REVIEW without resolve_hint" in error
               for error in judge_packets.inspect_draft([packet], path)["errors"])
    row["frames_used"] = [{}]
    path.write_text(json.dumps([row]))
    assert judge_packets.inspect_draft([packet], path)["status"] == "invalid"


def test_decision_fingerprint_changes_with_packet_or_tile_bytes(tmp_path):
    judge_packets = _load("judge_packets_fingerprint", TOOLS / "judge_packets.py")
    packet = _packet(1, tmp_path)
    first, missing = judge_packets.decision_fingerprint([packet])
    assert first and not missing
    packet["serves"]["trail"] = "Changed Trail"
    second, _ = judge_packets.decision_fingerprint([packet])
    assert second != first
    packet["serves"]["trail"] = "Test Trail"
    Path(packet["tiles"]["z2"]).write_bytes(b"different imagery")
    third, _ = judge_packets.decision_fingerprint([packet])
    assert third != first


def test_existing_draft_requires_adoption_then_rejects_changed_inputs(tmp_path, monkeypatch, capsys):
    judge_packets = _load("judge_packets_adopt", TOOLS / "judge_packets.py")
    packets = _production_packets(judge_packets, tmp_path, [1])
    draft = tmp_path / "test-co_verdict_draft_00.json"
    draft.write_text(json.dumps([_checkpoint(1)]))
    before = draft.read_bytes()
    args = ["test-co", "--tmp", str(tmp_path)]
    assert judge_packets.main(args) == 2
    assert "no checkpoint manifest" in capsys.readouterr().err
    assert judge_packets.main(args + ["--adopt-existing"]) == 0
    capsys.readouterr()
    assert draft.read_bytes() == before
    manifest = (tmp_path / "test-co_checkpoint_00.json").read_bytes()

    generation = dossier_output.verify_generation(tmp_path, "test-co")
    parent = generation["source_generation"]["manifest_sha256"]
    walk_path = tmp_path / "test-co_walk.json"
    walk = json.loads(walk_path.read_text())
    walk["1"]["walk_m"] = 999
    walk_path.write_text(json.dumps(walk))
    dossier_output.commit_generation(tmp_path, "test-co", parent)
    assert judge_packets.main(args) == 2
    assert (tmp_path / "test-co_checkpoint_00.json").read_bytes() == manifest


def test_generator_rejects_noncanonical_alias_and_consolidated_conflict(tmp_path, monkeypatch, capsys):
    judge_packets = _load("judge_packets_alias", TOOLS / "judge_packets.py")
    packets = _production_packets(judge_packets, tmp_path, [1])
    alias = tmp_path / "test-co_verdict_draft_000.json"
    alias.write_text(json.dumps([_checkpoint(1)]))
    assert judge_packets.main(["test-co", "--tmp", str(tmp_path)]) == 2
    assert "noncanonical/stale draft" in capsys.readouterr().err
    alias.rename(tmp_path / "test-co_verdict_draft.json")
    (tmp_path / "test-co_verdict_draft_00.json").write_text(json.dumps([_checkpoint(1)]))
    assert judge_packets.main(["test-co", "--tmp", str(tmp_path), "--adopt-existing"]) == 2
    assert "conflicts with numbered" in capsys.readouterr().err


def test_frozen_review_sample_is_deterministic_and_ignores_live_source_mutation(
        tmp_path):
    rows = [_verdict(fid, "KEEP") for fid in range(1, 7)]
    tmp = _area(tmp_path, drafts=[rows])
    packet = json.loads((tmp / "test-co_packets.json").read_text())["1"]
    authority_run, _ = _write_authority_run(
        tmp_path, tmp, 1, rows[0], packet, "sample-authority",
        render_review=False,
    )
    args = [
        "test-co", "--authority-run", str(authority_run), "--sample", "3",
    ]
    assert review_sheet.main(args) == 0
    receipt_path = _review_receipt(authority_run)
    receipt = json.loads(receipt_path.read_text())
    assert len(receipt["sample"]["selected_fids"]) == 3
    assert receipt["sample"]["eligible_fids"] == list(range(1, 7))
    sheet_path = Path(receipt["sheet_path"])
    before = (sheet_path.read_bytes(), receipt_path.read_bytes())

    # Live inputs are not rendering authority after prepare.
    (tmp / "test-co_verdict_draft_00.json").write_text("[]")
    Path(packet["tiles"]["z2"]).write_bytes(b"live tile changed")
    assert review_sheet.main(args) == 0
    assert (sheet_path.read_bytes(), receipt_path.read_bytes()) == before
    with pytest.raises(SystemExit, match="different bytes"):
        review_sheet.main([
            "test-co", "--authority-run", str(authority_run),
            "--sample", "4",
        ])


def test_sampled_agreement_excludes_out_of_sample_flips_and_rejects_unknown_fids():
    import pytest
    drafts = [_checkpoint(fid) for fid in range(1, 6)]
    merge_drafts.set_verdict(drafts[1], "DROP", "unsampled correction", "2026-09-14")
    rec = calibration.record_for("test-co", drafts, {"KEEP": "sample"}, [], None,
                                 "2026-09-14", "agent-fanout", [1], "abc")
    report = calibration.report({"areas": [rec]}, 0.02, 100)
    keep_any = next(line for line in report.splitlines() if line.startswith("KEEP any"))
    assert "  5        1     0  100.0%" in keep_any
    assert "out-of-sample flips" in report and "KEEP/strong=1" in report
    with pytest.raises(SystemExit):
        calibration.record_for("test-co", drafts, {"KEEP": "sample"}, [], None,
                               "2026-09-14", "agent-fanout", [999], "abc")


def test_calibration_rejects_malformed_override_provenance():
    import pytest
    row = _checkpoint(1)
    merge_drafts.set_verdict(row, "DROP", "human correction", "2026-09-26")
    assert calibration.judge_call(row) == ("KEEP", "strong")
    del row["override"]["confidence_from"]
    with pytest.raises(SystemExit, match="invalid judge/override provenance"):
        calibration.judge_call(row)
    with pytest.raises(SystemExit, match="invalid judge/override provenance"):
        calibration.record_for("test-co", [row], {"KEEP": "none"}, [], None,
                               "2026-09-26", "agent-fanout")


def test_manifest_allows_legal_override_but_rejects_original_evidence_drift(tmp_path,
                                                                             monkeypatch,
                                                                             capsys):
    judge_packets = _load("judge_packets_override_manifest", TOOLS / "judge_packets.py")
    packets = _production_packets(judge_packets, tmp_path, [1])
    draft = tmp_path / "test-co_verdict_draft_00.json"
    draft.write_text(json.dumps([_checkpoint(1)]))
    args = ["test-co", "--tmp", str(tmp_path)]

    assert judge_packets.main(args + ["--adopt-existing"]) == 0
    capsys.readouterr()
    manifest_path = tmp_path / "test-co_checkpoint_00.json"
    before = json.loads(manifest_path.read_text())

    rows = json.loads(draft.read_text())
    merge_drafts.set_verdict(rows[0], "DROP", "human correction", "2026-09-26")
    draft.write_text(json.dumps(rows))
    assert judge_packets.main(args) == 0
    capsys.readouterr()
    after_override = json.loads(manifest_path.read_text())
    assert after_override["judge_row_sha256"] == before["judge_row_sha256"]
    assert after_override["draft_sha256"] != before["draft_sha256"]

    rows = json.loads(draft.read_text())
    rows[0]["exists"]["evidence"] = "tampered original evidence"
    draft.write_text(json.dumps(rows))
    assert judge_packets.main(args) == 2
    assert "original judge rows changed" in capsys.readouterr().err


def _checkpoint_manifest_fixture(tmp_path, module_name):
    judge_packets = _load(module_name, TOOLS / "judge_packets.py")
    chunk = [_packet(1, tmp_path)]
    draft = tmp_path / "test-co_verdict_draft_00.json"
    draft.write_text(json.dumps([_checkpoint(1)]))
    state = judge_packets.inspect_draft(chunk, draft)
    decision_sha, missing = judge_packets.decision_fingerprint(chunk)
    assert state["status"] == "complete" and decision_sha and not missing
    manifest = judge_packets.manifest_value(
        "test-co", 0, chunk, decision_sha, state
    )
    return judge_packets, chunk, state, decision_sha, manifest


def test_checkpoint_manifest_supports_only_exact_generation_bound_v2_schema(
        tmp_path):
    judge_packets, chunk, state, decision_sha, current = (
        _checkpoint_manifest_fixture(tmp_path, "judge_packets_manifest_schemas")
    )
    assert set(current) == set(judge_packets.CHECKPOINT_V2_CURRENT_KEYS)
    assert current["version"] == 2
    assert current["source_generation"] == chunk[0]["source_generation"]
    assert judge_packets.validate_manifest(
        current, "test-co", 0, chunk, decision_sha, state
    ) == []

    legacy = copy.deepcopy(current)
    legacy["version"] = 1
    legacy.pop("source_generation")
    assert set(legacy) == set(judge_packets.CHECKPOINT_V1_CURRENT_KEYS)
    errors = judge_packets.validate_manifest(
        legacy, "test-co", 0, chunk, decision_sha, state
    )
    assert any("current v2 schema" in error for error in errors)

    missing_path = tmp_path / "missing-draft.json"
    initial_state = judge_packets.inspect_draft(chunk, missing_path)
    initial = judge_packets.manifest_value(
        "test-co", 0, chunk, decision_sha, initial_state
    )
    assert initial["draft_sha256"] is None
    assert initial["completed"] == []
    assert initial["judge_row_sha256"] == []
    assert initial["resolution_row_sha256"] == []
    assert initial["source_generation"] == chunk[0]["source_generation"]
    assert judge_packets.validate_manifest(
        initial, "test-co", 0, chunk, decision_sha, initial_state
    ) == []

    for missing_key in ("judge_row_sha256", "draft_sha256", "source_generation"):
        malformed = copy.deepcopy(current)
        malformed.pop(missing_key)
        errors = judge_packets.validate_manifest_static(
            malformed, "test-co", 0, chunk, decision_sha
        )
        assert any("current v2 schema" in error for error in errors)
    for base in (current, legacy):
        malformed = copy.deepcopy(base)
        malformed["extra"] = "forbidden"
        errors = judge_packets.validate_manifest_static(
            malformed, "test-co", 0, chunk, decision_sha
        )
        assert any("current v2 schema" in error for error in errors)


def test_checkpoint_manifest_rejects_unbound_v1_resolution_downgrade(
        tmp_path):
    judge_packets, chunk, state, decision_sha, current = (
        _checkpoint_manifest_fixture(tmp_path, "judge_packets_manifest_downgrade")
    )
    divergent_state = copy.deepcopy(state)
    divergent_state["resolution_sha256"] = ["b" * 64]
    current = judge_packets.manifest_value(
        "test-co", 0, chunk, decision_sha, divergent_state
    )
    assert judge_packets.validate_manifest(
        current, "test-co", 0, chunk, decision_sha, divergent_state
    ) == []

    legacy_downgrade = copy.deepcopy(current)
    legacy_downgrade["version"] = 1
    legacy_downgrade.pop("source_generation")
    legacy_downgrade.pop("resolution_row_sha256")
    errors = judge_packets.validate_manifest(
        legacy_downgrade, "test-co", 0, chunk, decision_sha,
        divergent_state,
    )
    assert any("current v2 schema" in error for error in errors)

    malformed_current = copy.deepcopy(current)
    malformed_current["resolution_row_sha256"] = None
    errors = judge_packets.validate_manifest_static(
        malformed_current, "test-co", 0, chunk, decision_sha
    )
    assert "checkpoint resolution_row_sha256 is not a SHA-256 list" in errors


@pytest.mark.parametrize(
    ("field", "malformed"),
    [
        ("version", True),
        ("version", 1.0),
        ("chunk", False),
        ("chunk", 0.0),
        ("fids", [True]),
        ("fids", [1.0]),
        ("completed", [True]),
        ("completed", [1.0]),
    ],
)
def test_checkpoint_manifest_rejects_bool_and_float_integer_aliases(
        tmp_path, field, malformed):
    judge_packets, chunk, state, decision_sha, manifest = (
        _checkpoint_manifest_fixture(
            tmp_path, f"judge_packets_manifest_exact_int_{field}_{type(malformed).__name__}"
        )
    )
    manifest[field] = malformed
    errors = judge_packets.validate_manifest_static(
        manifest, "test-co", 0, chunk, decision_sha
    )
    assert errors
    assert any(field in error for error in errors)


@pytest.mark.parametrize(
    ("field", "malformed"),
    [
        ("decision_sha256", True),
        ("decision_sha256", "A" * 64),
        ("draft_sha256", False),
        ("draft_sha256", "A" * 64),
        ("judge_row_sha256", [True]),
        ("judge_row_sha256", ["A" * 64]),
        ("resolution_row_sha256", [1.0]),
        ("resolution_row_sha256", ["A" * 64]),
    ],
)
def test_checkpoint_manifest_rejects_noncanonical_hash_types(
        tmp_path, field, malformed):
    judge_packets, chunk, state, decision_sha, manifest = (
        _checkpoint_manifest_fixture(
            tmp_path, f"judge_packets_manifest_hash_{field}_{type(malformed).__name__}"
        )
    )
    manifest[field] = malformed
    errors = judge_packets.validate_manifest_static(
        manifest, "test-co", 0, chunk, decision_sha
    )
    assert errors
    assert any(field in error for error in errors)


@pytest.mark.parametrize("manifest_drift", ["bool-version", "extra-key"])
def test_invalid_later_checkpoint_with_continuation_has_zero_mutation(
        tmp_path, monkeypatch, manifest_drift):
    judge_packets = _load(
        f"judge_packets_checkpoint_zero_mutation_{manifest_drift}",
        TOOLS / "judge_packets.py",
    )
    packets = _production_packets(judge_packets, tmp_path, [1, 2])
    args = ["test-co", "--tmp", str(tmp_path), "--chunk", "1"]
    assert judge_packets.main(args) == 0

    continuation = tmp_path / "test-co_verdict_continue_00.json"
    continuation.write_text(json.dumps([_checkpoint(1)]))
    later_manifest_path = tmp_path / "test-co_checkpoint_01.json"
    later_manifest = json.loads(later_manifest_path.read_text())
    if manifest_drift == "bool-version":
        later_manifest["version"] = True
    else:
        later_manifest["extra"] = "forbidden"
    later_manifest_path.write_text(json.dumps(later_manifest))

    def snapshot():
        return {
            str(path.relative_to(tmp_path)): path.read_bytes()
            for path in sorted(tmp_path.rglob("*"))
            if path.is_file()
        }

    before = snapshot()

    def unexpected_write(*_args, **_kwargs):
        raise AssertionError("invalid all-chunk preflight reached a mutation")

    monkeypatch.setattr(judge_packets, "_atomic_write", unexpected_write)
    monkeypatch.setattr(judge_packets, "_atomic_json", unexpected_write)
    monkeypatch.setattr(judge_packets, "_archive_continuation", unexpected_write)
    assert judge_packets.main(args) == 2
    assert snapshot() == before
    assert continuation.read_bytes() == before[continuation.name]
    assert not (tmp_path / "Archive").exists()


def test_readme_documents_host_owned_continuation_workflow():
    readme = (TOOLS.parent / "README.md").read_text()
    assert "verdict_continue_NN.json" in readme
    assert "canonical <slug>_verdict_draft_NN.json files are host-owned" in readme
    assert "Re-run the same command after every wave" in readme
    assert "each writes <slug>_verdict_draft_NN.json" not in readme
    assert "--reviewed KEEP=en-bloc" not in readme


def test_continuation_recovers_when_draft_lands_before_manifest(tmp_path, monkeypatch, capsys):
    import pytest
    judge_packets = _load("judge_packets_recover_manifest", TOOLS / "judge_packets.py")
    packets = _production_packets(judge_packets, tmp_path, [1, 2])
    args = ["test-co", "--tmp", str(tmp_path), "--status-json"]
    assert judge_packets.main(args) == 0
    capsys.readouterr()
    continuation = tmp_path / "test-co_verdict_continue_00.json"
    continuation.write_text(json.dumps([_checkpoint(1)]))

    real_atomic_json = judge_packets._atomic_json
    def crash_before_manifest(path, value):
        if path.name == "test-co_checkpoint_00.json":
            raise RuntimeError("simulated crash before manifest")
        return real_atomic_json(path, value)
    monkeypatch.setattr(judge_packets, "_atomic_json", crash_before_manifest)
    with pytest.raises(RuntimeError, match="simulated crash"):
        judge_packets.main(args)
    assert [row["fid"] for row in json.loads(
        (tmp_path / "test-co_verdict_draft_00.json").read_text())] == [1]
    assert json.loads((tmp_path / "test-co_checkpoint_00.json").read_text())["completed"] == []
    assert continuation.exists()

    monkeypatch.setattr(judge_packets, "_atomic_json", real_atomic_json)
    assert judge_packets.main(args) == 0
    capsys.readouterr()
    assert [row["fid"] for row in json.loads(
        (tmp_path / "test-co_verdict_draft_00.json").read_text())] == [1]
    assert json.loads((tmp_path / "test-co_checkpoint_00.json").read_text())["completed"] == [1]
    assert not continuation.exists()


def test_continuation_recovers_when_manifest_lands_before_archive(tmp_path, monkeypatch, capsys):
    import pytest
    judge_packets = _load("judge_packets_recover_archive", TOOLS / "judge_packets.py")
    packets = _production_packets(judge_packets, tmp_path, [1, 2])
    args = ["test-co", "--tmp", str(tmp_path), "--status-json"]
    assert judge_packets.main(args) == 0
    capsys.readouterr()
    continuation = tmp_path / "test-co_verdict_continue_00.json"
    continuation.write_text(json.dumps([_checkpoint(1)]))

    real_archive = judge_packets._archive_continuation
    def crash_before_archive(path):
        raise RuntimeError("simulated crash before archive")
    monkeypatch.setattr(judge_packets, "_archive_continuation", crash_before_archive)
    with pytest.raises(RuntimeError, match="simulated crash"):
        judge_packets.main(args)
    assert [row["fid"] for row in json.loads(
        (tmp_path / "test-co_verdict_draft_00.json").read_text())] == [1]
    assert json.loads((tmp_path / "test-co_checkpoint_00.json").read_text())["completed"] == [1]
    assert continuation.exists()

    monkeypatch.setattr(judge_packets, "_archive_continuation", real_archive)
    assert judge_packets.main(args) == 0
    capsys.readouterr()
    assert [row["fid"] for row in json.loads(
        (tmp_path / "test-co_verdict_draft_00.json").read_text())] == [1]
    assert not continuation.exists()


def _prepared_continuation_capture_case(tmp_path, monkeypatch, module_name):
    judge_packets = _load(module_name, TOOLS / "judge_packets.py")
    packets = _production_packets(judge_packets, tmp_path, [1, 2])
    args = ["test-co", "--tmp", str(tmp_path), "--status-json"]
    draft = tmp_path / "test-co_verdict_draft_00.json"
    draft.write_bytes((json.dumps([_checkpoint(1)], indent=1) + "\n").encode())
    assert judge_packets.main(args + ["--adopt-existing"]) == 0

    continuation = tmp_path / "test-co_verdict_continue_00.json"
    captured_raw = (json.dumps([_checkpoint(2)], indent=1) + "\n").encode()
    attacker_raw = (json.dumps([_checkpoint(2, verdict="DROP")], indent=1) + "\n").encode()
    width = max(len(captured_raw), len(attacker_raw))
    captured_raw += b" " * (width - len(captured_raw))
    attacker_raw += b" " * (width - len(attacker_raw))
    assert captured_raw != attacker_raw and len(captured_raw) == len(attacker_raw)
    assert judge_packets.inspect_rows(
        [packets[2]], json.loads(attacker_raw), "valid alternate continuation",
        allow_override=False,
    )["status"] == "complete"
    continuation.write_bytes(captured_raw)
    checkpoint = tmp_path / "test-co_checkpoint_00.json"
    return {
        "module": judge_packets,
        "args": args,
        "draft": draft,
        "checkpoint": checkpoint,
        "continuation": continuation,
        "captured_raw": captured_raw,
        "attacker_raw": attacker_raw,
        "canonical_before": (draft.read_bytes(), checkpoint.read_bytes()),
    }


@pytest.mark.parametrize("swap_kind", ["symlink", "regular", "same-inode-same-length"])
def test_continuation_capture_rejects_entry_swap_before_transaction(
        tmp_path, monkeypatch, swap_kind):
    case = _prepared_continuation_capture_case(
        tmp_path, monkeypatch, f"judge_packets_capture_swap_{swap_kind}"
    )
    judge_packets = case["module"]
    continuation = case["continuation"]
    attacker_path = tmp_path / f"attacker-{swap_kind}.json"
    real_recheck = judge_packets._recheck_continuation
    swapped = False

    def swap_then_recheck(capture):
        nonlocal swapped
        if not swapped:
            if swap_kind == "symlink":
                attacker_path.write_bytes(case["attacker_raw"])
                continuation.unlink()
                continuation.symlink_to(attacker_path)
            elif swap_kind == "regular":
                attacker_path.write_bytes(case["attacker_raw"])
                os.replace(attacker_path, continuation)
            else:
                before = continuation.stat()
                with continuation.open("r+b") as stream:
                    stream.write(case["attacker_raw"])
                    stream.truncate()
                    stream.flush()
                    os.fsync(stream.fileno())
                after = continuation.stat()
                os.utime(continuation, ns=(
                    after.st_atime_ns,
                    capture.entry_after.mtime_ns + 1_000_000,
                ))
                final = continuation.stat()
                assert final.st_ino == before.st_ino
                assert final.st_size == before.st_size
            swapped = True
        return real_recheck(capture)

    monkeypatch.setattr(judge_packets, "_recheck_continuation", swap_then_recheck)
    assert judge_packets.main(case["args"]) == 2
    assert swapped
    assert (case["draft"].read_bytes(), case["checkpoint"].read_bytes()) == (
        case["canonical_before"]
    )
    archive = tmp_path / "Archive"
    archived = list(archive.glob("test-co_verdict_continue_00*.json")) if archive.exists() else []
    assert all(path.read_bytes() != case["attacker_raw"] for path in archived)


def test_continuation_source_is_captured_once_then_verified_in_quarantine(
        tmp_path, monkeypatch):
    case = _prepared_continuation_capture_case(
        tmp_path, monkeypatch, "judge_packets_capture_once"
    )
    judge_packets = case["module"]
    real_read = judge_packets._read_fd_bytes
    reads = 0

    def counted_read(fd):
        nonlocal reads
        reads += 1
        return real_read(fd)

    monkeypatch.setattr(judge_packets, "_read_fd_bytes", counted_read)
    assert judge_packets.main(case["args"]) == 0
    assert reads == 2  # one live capture, one verification after quarantine move
    assert not case["continuation"].exists()
    archived = list((tmp_path / "Archive").glob(
        "test-co_verdict_continue_00.captured-*.json"
    ))
    assert len(archived) == 1
    assert archived[0].read_bytes() == case["captured_raw"]
    assert judge_packets._sha256(case["captured_raw"]) in archived[0].name
    quarantined = list((
        tmp_path / "Archive" / "continuation-quarantine"
    ).glob("*/test-co_verdict_continue_00.json"))
    assert len(quarantined) == 1
    assert quarantined[0].read_bytes() == case["captured_raw"]


def test_continuation_merge_is_semantically_validated_before_canonical_write(
        tmp_path, monkeypatch):
    case = _prepared_continuation_capture_case(
        tmp_path, monkeypatch, "judge_packets_proposal_validation"
    )
    judge_packets = case["module"]
    invalid_rows = [_checkpoint(1), _checkpoint(2, osm=["way/attacker"])]
    invalid_proposal = (json.dumps(invalid_rows, indent=1) + "\n").encode()
    monkeypatch.setattr(
        judge_packets,
        "_append_json_arrays",
        lambda existing, addition, addition_rows=None: invalid_proposal,
    )

    assert judge_packets.main(case["args"]) == 2
    assert (case["draft"].read_bytes(), case["checkpoint"].read_bytes()) == (
        case["canonical_before"]
    )
    assert case["continuation"].read_bytes() == case["captured_raw"]
    assert not (tmp_path / "Archive").exists()


def test_skip_judged_keeps_current_area_and_skips_other_area(tmp_path, monkeypatch, capsys):
    judge_packets = _load("judge_packets_skip_owner", TOOLS / "judge_packets.py")
    packets = _production_packets(judge_packets, tmp_path, [1, 2, 3])
    store = tmp_path / "store.json"
    store.write_text(json.dumps({
        "way/1": {"area": "test-co", "verdict": "KEEP"},
        "way/2": {"area": "other-co", "verdict": "KEEP"},
        "way/3": {"verdict": "KEEP"},
    }))
    assert judge_packets.main([
        "test-co", "--tmp", str(tmp_path), "--skip-judged", str(store), "--status-json"
    ]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(rows) == 1 and rows[0]["missing"] == [1]
    generated = json.loads((tmp_path / "test-co_packets.json").read_text())
    assert set(generated) == {"1"}


def test_human_authority_helpers_reject_missing_reviewer_before_loading_run(tmp_path):
    tmp = _area(tmp_path)
    missing_run = tmp_path / "missing-run"
    confirm = merge_drafts.apply_confirmations(
        tmp, "test-co", {2: "DROP"}, "accepted", "2026-09-28",
        None, missing_run, missing_run / "review-receipt.json", {}, {},
    )
    assert "canonical --reviewer identity" in confirm[0]
    decide = merge_drafts.apply_bound_decision(
        tmp, "test-co", 3, "DROP", {"public": "no"},
        {"public": "user verified no public authorization"}, "accepted",
        "2026-09-28", None, missing_run,
        missing_run / "review-receipt.json", {}, {},
    )
    assert "canonical --reviewer identity" in decide[0]


def test_store_merge_requires_real_packet_tiles_for_human_confirmation(tmp_path, capsys):
    tmp = _area(tmp_path)
    store = tmp_path / "store.json"
    packets_path = tmp / "test-co_packets.json"
    packets = json.loads(packets_path.read_text())
    original = _drafts(tmp)[2]
    authority_run, _ = _write_authority_run(
        tmp_path, tmp, 2, original, packets["2"], "confirmation-authority"
    )
    assert merge_drafts.main([
        str(store), "test-co", "--judged", "2026-09-28",
        "--confirm", "2=DROP", "--note", "accepted conservative decision",
        "--reviewer", "trekdex-project-owner",
        "--authority-run", str(authority_run),
        "--review-receipt", str(_review_receipt(authority_run)),
    ]) == 0
    packets["2"].pop("tiles")
    packets_path.write_text(json.dumps(packets))
    assert merge_drafts.main([str(store), "test-co", "--judged", "2026-09-29", "--write"]) == 1
    assert "full packet with tiles is required" in capsys.readouterr().out
    assert not store.exists()


def test_store_merge_requires_real_packet_tiles_for_v2_override(tmp_path, capsys):
    tmp = _area(tmp_path)
    store = tmp_path / "store.json"
    packets_path = tmp / "test-co_packets.json"
    packets = json.loads(packets_path.read_text())
    original = _drafts(tmp)[3]
    authority_run, _ = _write_authority_run(
        tmp_path, tmp, 3, original, packets["3"], "decision-authority"
    )
    assert merge_drafts.main([
        str(store), "test-co", "--judged", "2026-09-28",
        "--decide", "3=DROP", "--axis", "public=no",
        "--axis-evidence", "public=User verified no public authorization",
        "--note", "accepted after named review",
        "--reviewer", "trekdex-project-owner",
        "--authority-run", str(authority_run),
        "--review-receipt", str(_review_receipt(authority_run)),
    ]) == 0
    packets["3"].pop("tiles")
    packets_path.write_text(json.dumps(packets))
    assert merge_drafts.main([str(store), "test-co", "--judged", "2026-09-29", "--write"]) == 1
    assert "full packet with tiles is required" in capsys.readouterr().out
    assert not store.exists()


def test_v2_override_requires_every_original_decision_field(tmp_path):
    tmp = _area(tmp_path)
    store = tmp_path / "store.json"
    packets = json.loads((tmp / "test-co_packets.json").read_text())
    original = _drafts(tmp)[3]
    authority_run, _ = _write_authority_run(
        tmp_path, tmp, 3, original, packets["3"], "required-fields-authority"
    )
    assert merge_drafts.main([
        str(store), "test-co", "--judged", "2026-09-28",
        "--decide", "3=DROP", "--axis", "public=no",
        "--axis-evidence", "public=User verified no public authorization",
        "--note", "accepted after named review",
        "--reviewer", "trekdex-project-owner",
        "--authority-run", str(authority_run),
        "--review-receipt", str(_review_receipt(authority_run)),
    ]) == 0
    entry = _drafts(tmp)[3]
    for field in sorted(merge_drafts.tr.REQUIRED_DECISION_FIELDS):
        tampered = copy.deepcopy(entry)
        source = tampered["override"]["from_decision"]
        source.pop(field)
        tampered["override"]["from_decision_sha256"] = merge_drafts.tr.sha256_json(source)
        tampered["override"]["from_evidence_sha256"] = merge_drafts.tr.evidence_sha256(source)
        errors, _ = merge_drafts.validate_verdict_row(tampered, packets["3"])
        assert any("from_decision missing required fields" in error for error in errors), field


def _stored_overlap(row, area, source, osm):
    stored = copy.deepcopy(row)
    stored.update({
        "area": area, "judged": "2026-09-01", "lat": 40.0, "lon": -105.5,
        "rings": [], "name": "Existing", "src": source, "osm": list(osm),
    })
    return stored


def test_overlap_checks_every_foreign_holder_not_only_first_alias(tmp_path, capsys):
    incoming = _verdict(1, "KEEP", osm=["way/1", "node/99"])
    _area(tmp_path, drafts=[[incoming]])
    store = tmp_path / "store.json"
    first = _stored_overlap(incoming, "automated-co", "judge-fanout", ["way/1"])
    conflicting = _stored_overlap(
        _verdict(1, "DROP", "strong", osm=["node/99"]),
        "human-co", "user-curated", ["node/99"],
    )
    store.write_text(json.dumps({"way/1": first, "node/99": conflicting}))
    before = store.read_bytes()

    assert merge_drafts.main([str(store), "test-co", "--judged", "2026-09-29", "--write"]) == 1
    output = capsys.readouterr().out
    assert "direct-human and automated overlap decisions differ" in output
    assert "direct-human and automated overlap decisions differ" in output
    assert store.read_bytes() == before


def test_overlap_preserves_later_direct_human_holder_over_automated_first_alias(tmp_path):
    incoming = _verdict(1, "KEEP", osm=["way/1", "node/99"])
    _area(tmp_path, drafts=[[incoming]])
    store = tmp_path / "store.json"
    automated = _stored_overlap(incoming, "automated-co", "judge-fanout", ["way/1"])
    human = _stored_overlap(incoming, "human-co", "user-curated", ["node/99"])
    store.write_text(json.dumps({"way/1": automated, "node/99": human}))

    assert merge_drafts.main([
        str(store), "test-co", "--judged", "2026-09-28", "--write",
    ]) == 0
    written = json.loads(store.read_text())
    assert written["way/1"]["src"] == "user-curated"
    assert written["node/99"]["src"] == "user-curated"
    assert written["way/1"]["area"] == "human-co"
    assert set(written["way/1"]["osm"]) == {"way/1", "node/99"}


def test_overlap_rejects_agent_injected_direct_human_source(tmp_path, capsys):
    incoming = _verdict(1, "KEEP", osm=["way/1"])
    incoming["src"] = "user-curated"
    _area(tmp_path, drafts=[[incoming]])
    store = tmp_path / "store.json"
    automated = _stored_overlap(incoming, "automated-co", "judge-fanout", ["way/1"])
    automated["src"] = "judge-fanout"
    store.write_text(json.dumps({"way/1": automated}))
    before = store.read_bytes()

    assert merge_drafts.main([
        str(store), "test-co", "--judged", "2026-09-28", "--write",
    ]) == 1
    assert "host-owned persistence fields ['src']" in capsys.readouterr().out
    assert store.read_bytes() == before


@pytest.mark.parametrize("bad_version", [True, 1.0])
def test_human_authority_versions_reject_boolean_and_float_downgrades(
        tmp_path, bad_version):
    tmp = _area(tmp_path)
    packets = json.loads((tmp / "test-co_packets.json").read_text())
    original = _drafts(tmp)[2]
    authority_run, _ = _write_authority_run(
        tmp_path, tmp, 2, original, packets["2"], "version-authority"
    )
    store = tmp_path / "store.json"
    assert merge_drafts.main([
        str(store), "test-co", "--confirm", "2=DROP",
        "--judged", "2026-09-28", "--note", "accepted",
        "--reviewer", "trekdex-project-owner",
        "--authority-run", str(authority_run),
        "--review-receipt", str(_review_receipt(authority_run)),
    ]) == 0
    entry = _drafts(tmp)[2]
    entry["human_confirmation"]["version"] = bad_version
    errors, _ = merge_drafts.validate_verdict_row(entry, packets["2"])
    assert "human_confirmation.version must be 1 or 2" in errors


def test_failed_authority_preflight_leaves_draft_and_receipts_untouched(tmp_path, capsys):
    tmp = _area(tmp_path)
    packets = json.loads((tmp / "test-co_packets.json").read_text())
    original = _drafts(tmp)[2]
    authority_run, _ = _write_authority_run(
        tmp_path, tmp, 2, original, packets["2"], "staged-authority"
    )
    held = _stored_overlap(original, "other-co", "judge-fanout", original["osm"])
    store = tmp_path / "store.json"
    store.write_text(json.dumps({original["osm"][0]: held}))
    draft = tmp / "test-co_verdict_draft_00.json"
    before = draft.read_bytes()

    assert merge_drafts.main([
        str(store), "test-co", "--confirm", "2=DROP",
        "--judged", "2026-09-28", "--note", "accepted",
        "--reviewer", "trekdex-project-owner",
        "--authority-run", str(authority_run),
        "--review-receipt", str(_review_receipt(authority_run)),
    ]) == 1
    assert "different human_confirmation provenance" in capsys.readouterr().out
    assert draft.read_bytes() == before
    receipt_root = tmp / merge_drafts.AUTHORITY_RECEIPT_DIR / "test-co"
    assert not receipt_root.exists()


def test_missing_authority_receipt_blocks_replay_and_store_merge(tmp_path):
    tmp = _area(tmp_path)
    packets = json.loads((tmp / "test-co_packets.json").read_text())
    original = _drafts(tmp)[2]
    authority_run, _ = _write_authority_run(
        tmp_path, tmp, 2, original, packets["2"], "receipt-authority"
    )
    store = tmp_path / "store.json"
    assert merge_drafts.main([
        str(store), "test-co", "--confirm", "2=DROP",
        "--judged", "2026-09-28", "--note", "accepted",
        "--reviewer", "trekdex-project-owner",
        "--authority-run", str(authority_run),
        "--review-receipt", str(_review_receipt(authority_run)),
    ]) == 0
    entry = _drafts(tmp)[2]
    receipt_sha = entry["human_confirmation"]["authority_receipt_sha256"]
    items, corpus, sidecar = merge_drafts.resolver.replay_trust.load_work_area(
        tmp, "test-co"
    )
    report = merge_drafts.resolver.trust_engine.build_report(
        items, {"areas": []}, {}, sidecar, corpus, include_items=True
    )
    confirmed = next(item for item in report["items"] if item["source_key"] == "2")
    assert confirmed["human_confirmation"] is True
    assert confirmed["route"] == merge_drafts.resolver.trust_engine.ROUTE_AUTHORITY
    receipt_path = merge_drafts.authority_receipt_path(tmp, "test-co", receipt_sha)
    archive = tmp / "Archive" / receipt_path.name
    archive.parent.mkdir()
    receipt_path.rename(archive)

    assert merge_drafts.main([str(store), "test-co"]) == 1
    with pytest.raises(ValueError, match="invalid authority receipt"):
        merge_drafts.resolver.replay_trust.load_work_area(tmp, "test-co")
    with pytest.raises(ValueError, match="invalid human authority"):
        merge_drafts.resolver.prepare(
            "test-co", tmp, {
                "primary": merge_drafts.tr.model_config("primary", "family-a"),
                "challenger": merge_drafts.tr.model_config("challenger", "family-b"),
                "arbiter": merge_drafts.tr.model_config("arbiter", "family-c"),
            }, tmp_path / "replay-runs",
        )


def test_new_store_rows_require_terminal_authoritative_resolver(tmp_path, monkeypatch, capsys):
    tmp = _area(tmp_path)
    store = tmp_path / "store.json"
    monkeypatch.setattr(
        merge_drafts, "_terminal_resolver_errors", _REAL_TERMINAL_GATE
    )
    assert merge_drafts.main([str(store), "test-co", "--judged", "2026-09-29", "--write"]) == 1
    assert "store write requires --resolver-run" in capsys.readouterr().out
    assert not store.exists()

    rows = _drafts(tmp)
    packets = json.loads((tmp / "test-co_packets.json").read_text())
    pending_run, _ = _write_authority_run(
        tmp_path, tmp, 1, rows[1], packets["1"], "pending-run"
    )
    assert merge_drafts.main([
        str(store), "test-co", "--resolver-run", str(pending_run),
        "--judged", "2026-09-29", "--write",
    ]) == 1
    assert "resolver run state is" in capsys.readouterr().out
    assert not store.exists()


def test_multi_area_store_write_is_rejected_before_mutation(tmp_path):
    _area(tmp_path, slug="first-co", drafts=[[_verdict(1, "KEEP")]])
    _area(tmp_path, slug="second-co", drafts=[[_verdict(2, "DROP", "strong")]])
    store = tmp_path / "store.json"
    with pytest.raises(SystemExit, match="exactly one area"):
        merge_drafts.main([
            str(store), "first-co", "second-co", "--write",
        ])
    assert not store.exists()


def test_same_area_direct_human_holder_and_all_aliases_survive_remerge(tmp_path):
    incoming = _verdict(1, "KEEP", osm=["way/1"])
    _area(tmp_path, drafts=[[incoming]])
    store = tmp_path / "store.json"
    human = _stored_overlap(
        incoming, "test-co", "user-curated", ["way/1", "node/99"]
    )
    store.write_text(json.dumps({"way/1": human, "node/99": human}))

    assert merge_drafts.main([
        str(store), "test-co", "--judged", "2026-09-28", "--write",
    ]) == 0
    written = json.loads(store.read_text())
    assert written["way/1"]["src"] == "user-curated"
    assert written["node/99"]["src"] == "user-curated"
    assert set(written["way/1"]["osm"]) == {"way/1", "node/99"}


def test_receipt_source_run_id_must_match_reopened_prepare(tmp_path):
    tmp = _area(tmp_path, drafts=[[_verdict(2, "DROP", "strong")]])
    packet = json.loads((tmp / "test-co_packets.json").read_text())["2"]
    original = _drafts(tmp)[2]
    authority_run, _ = _write_authority_run(
        tmp_path, tmp, 2, original, packet, "source-id-authority"
    )
    store = tmp_path / "store.json"
    assert merge_drafts.main([
        str(store), "test-co", "--confirm", "2=DROP",
        "--judged", "2026-09-28", "--note", "accepted",
        "--reviewer", "trekdex-project-owner",
        "--authority-run", str(authority_run),
        "--review-receipt", str(_review_receipt(authority_run)),
    ]) == 0
    row = copy.deepcopy(_drafts(tmp)[2])
    old_sha = row["human_confirmation"]["authority_receipt_sha256"]
    receipt = json.loads(
        merge_drafts.authority_receipt_path(tmp, "test-co", old_sha).read_text()
    )
    fake_run_id = "f" * 64
    row["human_confirmation"]["source_run_id"] = fake_run_id
    receipt["source_run_id"] = fake_run_id
    receipt["authority_payload"]["source_run_id"] = fake_run_id
    body = dict(receipt)
    body.pop("receipt_sha256")
    new_sha = merge_drafts.tr.sha256_json(body)
    receipt["receipt_sha256"] = new_sha
    row["human_confirmation"]["authority_receipt_sha256"] = new_sha
    assert merge_drafts.validate_authority_receipt(
        row, tmp, pending={new_sha: receipt}
    ) == []
    errors = merge_drafts._validate_receipt_source(receipt, row, packet)
    assert "authority receipt source run id mismatch" in errors


def test_receipt_bound_human_publication_replays_from_store_attestation(
        tmp_path, monkeypatch):
    tmp = _area(tmp_path, drafts=[[_verdict(2, "DROP", "strong")]])
    packet = json.loads((tmp / "test-co_packets.json").read_text())["2"]
    original = _drafts(tmp)[2]
    authority_run, _ = _write_authority_run(
        tmp_path, tmp, 2, original, packet, "human-publication-authority"
    )
    store = tmp_path / "store.json"
    assert merge_drafts.main([
        str(store), "test-co", "--confirm", "2=DROP",
        "--judged", "2026-09-28", "--note", "accepted",
        "--reviewer", "trekdex-project-owner",
        "--authority-run", str(authority_run),
        "--review-receipt", str(_review_receipt(authority_run)),
    ]) == 0
    confirmed = _drafts(tmp)[2]
    final_run, _ = _write_authority_run(
        tmp_path, tmp, 2, confirmed, packet, "human-publication-final"
    )
    assert merge_drafts.resolver.apply_chunk(final_run, 0, apply=True)["state"] == "PRESERVED"
    monkeypatch.setattr(
        merge_drafts, "_terminal_resolver_errors", _REAL_TERMINAL_GATE
    )
    assert merge_drafts.main([
        str(store), "test-co", "--resolver-run", str(final_run),
        "--judged", "2026-09-29", "--write",
    ]) == 0
    stored = json.loads(store.read_text())["way/2"]
    assert merge_drafts.validate_publication_attestation(stored) == []
    proof_registry = json.loads(
        merge_drafts.publication_proof_path(store).read_text()
    )
    assert merge_drafts.resolver.replay_trust._validate_publication_proof(
        stored, proof_registry
    ) == []
    attestation_sha = stored["publication_attestation"]["attestation_sha256"]
    receipt_sha = stored["human_confirmation"]["authority_receipt_sha256"]
    report = merge_drafts.resolver.trust_engine.build_report([{
        "key": "way/2", "source_key": "2", "store": "co_verdicts_osm.json",
        "area": "test-co", "row": stored,
        "validated_authority_receipt_sha256": receipt_sha,
        "published_authority_attestation_sha256": attestation_sha,
        "published": {"verdict": "DROP", "src": "judge-fanout", "area": "test-co"},
    }], {"areas": []}, {}, {"lots": {}}, {
        "corpus_sha256": "x", "sidecar_sha256": None, "source_rows": 1,
        "unique_clusters": 1, "folded_duplicates": 0,
    }, include_items=True)
    item = report["items"][0]
    assert item["human_confirmation"] is True
    assert item["publication_authority_verified"] is True
    assert item["route"] == merge_drafts.resolver.trust_engine.ROUTE_AUTHORITY


def test_transitive_alias_component_exposes_later_human_conflict(tmp_path, capsys):
    incoming = _verdict(1, "KEEP", osm=["way/1"])
    _area(tmp_path, drafts=[[incoming]])
    first = _stored_overlap(
        incoming, "first-co", "judge-fanout", ["way/1", "node/99"]
    )
    human_drop = _stored_overlap(
        _verdict(1, "DROP", "strong", osm=["node/99"]),
        "human-co", "user-curated", ["node/99"],
    )
    store = tmp_path / "store.json"
    store.write_text(json.dumps({"way/1": first, "node/99": human_drop}))
    before = store.read_bytes()

    assert merge_drafts.main([str(store), "test-co", "--judged", "2026-09-29", "--write"]) == 1
    output = capsys.readouterr().out
    assert "direct-human and automated overlap decisions differ" in output
    assert store.read_bytes() == before


def test_human_authority_rejects_sibling_checkpoint_drift_without_mutation(
        tmp_path, capsys):
    tmp = _area(tmp_path)
    packet = json.loads((tmp / "test-co_packets.json").read_text())["2"]
    original = _drafts(tmp)[2]
    authority_run, _ = _write_authority_run(
        tmp_path, tmp, 2, original, packet, "checkpoint-authority"
    )
    checkpoint = tmp / "test-co_checkpoint_01.json"
    checkpoint.write_text('{"drift":true}')
    draft = tmp / "test-co_verdict_draft_00.json"
    before = draft.read_bytes()
    store = tmp_path / "store.json"

    assert merge_drafts.main([
        str(store), "test-co", "--confirm", "2=DROP",
        "--judged", "2026-09-28", "--note", "accepted",
        "--reviewer", "trekdex-project-owner",
        "--authority-run", str(authority_run),
        "--review-receipt", str(_review_receipt(authority_run)),
    ]) == 1
    assert "checkpoint_01.json changed after authority prepare" in capsys.readouterr().out
    assert draft.read_bytes() == before
    assert not (tmp / merge_drafts.AUTHORITY_RECEIPT_DIR).exists()


def test_real_terminal_run_preserves_direct_human_holder_and_aliases(
        tmp_path, monkeypatch):
    incoming = _verdict(1, "KEEP", osm=["way/1"])
    tmp = _area(tmp_path, drafts=[[incoming]])
    packet = json.loads((tmp / "test-co_packets.json").read_text())["1"]
    run, _ = _write_authority_run(
        tmp_path, tmp, 1, incoming, packet, "machine-overlap-run"
    )
    prepare = json.loads((run / "prepare.json").read_text())
    item = prepare["items"][0]
    assignment = item["assignments"]["challenger"]
    output = run / assignment["output_path"]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({
        "assignment_id": assignment["assignment_id"],
        "decision": item["primary"]["decision"],
        "external_evidence": [],
    }))
    assert merge_drafts.resolver.apply_chunk(run, 0, apply=True)["state"] == "APPLIED"

    store = tmp_path / "store.json"
    human = _stored_overlap(
        incoming, "test-co", "user-curated", ["way/1", "node/99"]
    )
    human.update({"lat": 40.001, "lon": -105.5, "rings": [RING], "name": "Lot 1"})
    legacy_rows = {"way/1": human, "node/99": human}
    store.write_text(json.dumps(legacy_rows))
    _explicit_publication_baselines(
        monkeypatch, {store.name: legacy_rows}
    )
    monkeypatch.setattr(
        merge_drafts, "_terminal_resolver_errors", _REAL_TERMINAL_GATE
    )
    assert merge_drafts.main([
        str(store), "test-co", "--resolver-run", str(run),
        "--judged", "2026-09-29", "--write",
    ]) == 0
    written = json.loads(store.read_text())
    assert written["way/1"]["src"] == "user-curated"
    assert written["node/99"]["src"] == "user-curated"
    assert set(written["way/1"]["osm"]) == {"way/1", "node/99"}
    assert "publication_attestation" not in written["way/1"]
    proof_registry = json.loads(
        merge_drafts.publication_proof_path(store).read_text()
    )
    assert proof_registry["proofs"] == {}


def test_validation_only_refuses_self_consistent_empty_forged_journal(
        tmp_path, capsys):
    _area(tmp_path, drafts=[[_verdict(1, "KEEP")]])
    store_path = tmp_path / "store.json"
    proof_path = merge_drafts.publication_proof_path(store_path)
    store_path.write_text(json.dumps({
        "way/legacy": {"verdict": "KEEP", "osm": ["way/legacy"]},
    }))
    proof_path.write_bytes(merge_drafts.resolver._json_bytes({
        "version": 1, "proofs": {},
    }))
    plan = _stage_forged_publication(
        store_path, {}, {"version": 1, "proofs": {}},
        _test_publication_authorization(tmp_path, "empty-forgery"),
    )
    before = {
        name: merge_drafts._read_optional_bytes(path)
        for name, path in plan.paths.items()
    }

    assert merge_drafts.main([str(store_path), "test-co"]) == 1
    assert "publication recovery required" in capsys.readouterr().err
    assert merge_drafts.main([
        str(store_path), "test-co", "--confirm", "1=KEEP",
        "--judged", "2026-09-29",
    ]) == 1
    assert "publication recovery required" in capsys.readouterr().err
    assert {
        name: merge_drafts._read_optional_bytes(path)
        for name, path in plan.paths.items()
    } == before


def test_forged_journal_cannot_change_or_delete_legacy_rows(
        tmp_path, monkeypatch, capsys):
    incoming = _verdict(1, "KEEP")
    tmp = _area(tmp_path, drafts=[[incoming]])
    packet = json.loads((tmp / "test-co_packets.json").read_text())["1"]
    run, _ = _terminal_machine_run(
        tmp_path, tmp, 1, incoming, packet, "legacy-forgery-run"
    )
    store_path = tmp_path / "store.json"
    legacy = {"verdict": "KEEP", "osm": ["way/legacy", "node/alias"]}
    store_path.write_text(json.dumps({
        "way/legacy": legacy,
        "node/alias": legacy,
    }))
    forged = {
        "way/legacy": {"verdict": "DROP", "osm": ["way/legacy"]},
    }
    plan = _stage_forged_publication(
        store_path, forged, {"version": 1, "proofs": {}},
        _test_publication_authorization(tmp_path, "legacy-forgery"),
    )
    before_store = store_path.read_bytes()
    before_journal = plan.paths["journal"].read_bytes()
    monkeypatch.setattr(
        merge_drafts, "_terminal_resolver_errors", _REAL_TERMINAL_GATE
    )

    assert merge_drafts.main([
        str(store_path), "test-co", "--resolver-run", str(run),
        "--judged", "2026-09-29", "--write",
    ]) == 1
    assert "publication recovery required" in capsys.readouterr().err
    assert store_path.read_bytes() == before_store
    assert plan.paths["journal"].read_bytes() == before_journal
    assert not plan.paths["archive"].exists()


@pytest.mark.parametrize("mutation", ["strip", "downgrade"])
def test_forged_journal_cannot_strip_or_downgrade_current_v2_authority(
        tmp_path, monkeypatch, capsys, mutation):
    incoming = _verdict(1, "KEEP", osm=["way/1"])
    tmp = _area(tmp_path, drafts=[[incoming]])
    packet = json.loads((tmp / "test-co_packets.json").read_text())["1"]
    run, _ = _terminal_machine_run(
        tmp_path, tmp, 1, incoming, packet, "current-v2-run"
    )
    store_path = tmp_path / "store.json"
    monkeypatch.setattr(
        merge_drafts, "_terminal_resolver_errors", _REAL_TERMINAL_GATE
    )
    assert merge_drafts.main([
        str(store_path), "test-co", "--resolver-run", str(run),
        "--judged", "2026-09-29", "--write",
    ]) == 0
    before_store = store_path.read_bytes()
    before_proof = merge_drafts.publication_proof_path(store_path).read_bytes()
    forged = json.loads(before_store)
    row = forged["way/1"]
    if mutation == "strip":
        row.pop("trust_resolution")
    else:
        row["trust_resolution"]["version"] = 1
    row.pop("publication_attestation")
    floor_path = merge_drafts.publication_floor_path(store_path)
    before_floor = floor_path.read_bytes()
    with pytest.raises(ValueError, match="floor-marked key may not lose"):
        _stage_forged_publication(
            store_path, forged, {"version": 1, "proofs": {}},
            _test_publication_authorization(tmp_path, f"authority-{mutation}"),
        )
    assert store_path.read_bytes() == before_store
    assert merge_drafts.publication_proof_path(store_path).read_bytes() == before_proof
    assert floor_path.read_bytes() == before_floor
    journal = merge_drafts._publication_transaction_paths(
        store_path, merge_drafts.publication_proof_path(store_path), "fixed"
    )["journal"]
    assert not journal.exists()


def test_terminal_gate_failure_leaves_live_forged_journal_and_targets_unchanged(
        tmp_path, monkeypatch, capsys):
    tmp = _area(tmp_path, drafts=[[_verdict(1, "KEEP")]])
    packet = json.loads((tmp / "test-co_packets.json").read_text())["1"]
    pending_run, _ = _write_authority_run(
        tmp_path, tmp, 1, _drafts(tmp)[1], packet, "pending-recovery-run"
    )
    store_path = tmp_path / "store.json"
    store_path.write_text(json.dumps({
        "way/legacy": {"verdict": "KEEP", "osm": ["way/legacy"]},
    }))
    plan = _stage_forged_publication(
        store_path, {}, {"version": 1, "proofs": {}},
        _test_publication_authorization(tmp_path, "gate-failure"),
    )
    before = {
        name: merge_drafts._read_optional_bytes(path)
        for name, path in plan.paths.items()
    }
    monkeypatch.setattr(
        merge_drafts, "_terminal_resolver_errors", _REAL_TERMINAL_GATE
    )

    assert merge_drafts.main([
        str(store_path), "test-co", "--judged", "2026-09-29", "--write",
    ]) == 1
    missing_output = capsys.readouterr()
    assert "store write requires --resolver-run" in missing_output.out
    assert "publication recovery required" in missing_output.err
    assert {
        name: merge_drafts._read_optional_bytes(path)
        for name, path in plan.paths.items()
    } == before

    assert merge_drafts.main([
        str(store_path), "test-co", "--resolver-run", str(pending_run),
        "--judged", "2026-09-29", "--write",
    ]) == 1
    output = capsys.readouterr()
    assert "resolver run state is" in output.out
    assert "publication recovery required" in output.err
    assert {
        name: merge_drafts._read_optional_bytes(path)
        for name, path in plan.paths.items()
    } == before


def test_publication_transaction_recovers_only_from_exact_authorized_plan(
        tmp_path, monkeypatch):
    incoming = _verdict(1, "KEEP", osm=["way/1"])
    tmp = _area(tmp_path, drafts=[[incoming]])
    packet = json.loads((tmp / "test-co_packets.json").read_text())["1"]
    first_run, first_run_id = _terminal_machine_run(
        tmp_path, tmp, 1, incoming, packet, "first-terminal-run"
    )
    store_path = tmp_path / "store.json"
    proof_path = merge_drafts.publication_proof_path(store_path)
    monkeypatch.setattr(
        merge_drafts, "_terminal_resolver_errors", _REAL_TERMINAL_GATE
    )
    real_commit = merge_drafts._commit_publication

    def crash_after_proof(store, desired_store, proof, desired_proofs,
                          authorization, crash_after_proof=False, **kwargs):
        del crash_after_proof
        return real_commit(
            store, desired_store, proof, desired_proofs, authorization,
            crash_after_proof=True, **kwargs,
        )

    monkeypatch.setattr(
        merge_drafts, "_commit_publication", crash_after_proof
    )
    with pytest.raises(RuntimeError, match="simulated crash"):
        merge_drafts.main([
            str(store_path), "test-co", "--resolver-run", str(first_run),
            "--judged", "2026-09-29", "--write",
        ])
    monkeypatch.setattr(merge_drafts, "_commit_publication", real_commit)
    assert proof_path.is_file()
    assert not store_path.exists()
    journal = merge_drafts._publication_transaction_paths(
        store_path, proof_path, "fixed"
    )["journal"]
    first_proof_bytes = proof_path.read_bytes()
    first_journal_bytes = journal.read_bytes()
    class OneStoreBuilder:
        STORES = [(store_path.name, None, None)]
        STORE_KEY_KINDS = {store_path.name: verdict_source.OSM_KEY}
        LEGACY_ROW_BASELINE_PATH = str(
            HERE / "parking-adjud" / "proofless-source-baseline-v1.json"
        )
        LEGACY_ROW_BASELINE_SHA256 = (
            "4a84784560ddde2a3867c9e0f1be5e303639a66dc28346b0c5faf56d1288c733"
        )
        PUBLICATION_TRUST_ROOT_PATH = str(
            HERE / "parking-adjud" / "publication-trust-root-v1.json"
        )
        PUBLICATION_TRUST_ROOT_SHA256 = (
            "43db143447d90f506d42b273c425e0d048e5580e605ac211750114ba350dfd85"
        )
    with pytest.raises(ValueError, match="live publication journal"):
        with merge_drafts.resolver.replay_trust.validated_store_snapshot(
                tmp_path, OneStoreBuilder):
            pass

    current = _drafts(tmp)[1]
    second_run, _ = _write_authority_run(
        tmp_path, tmp, 1, current, packet, "different-terminal-run"
    )
    assert merge_drafts.resolver.apply_chunk(
        second_run, 0, apply=True
    )["state"] == "PRESERVED"
    assert merge_drafts.main([
        str(store_path), "test-co", "--resolver-run", str(second_run),
        "--judged", "2026-09-29", "--write",
    ]) == 1
    assert not store_path.exists()
    assert proof_path.read_bytes() == first_proof_bytes
    assert journal.read_bytes() == first_journal_bytes

    assert merge_drafts.main([
        str(store_path), "test-co", "--resolver-run", str(first_run),
        "--judged", "2026-09-29", "--write",
    ]) == 0
    stored = json.loads(store_path.read_text())["way/1"]
    assert stored["publication_attestation"]["resolver_run_id"] == first_run_id
    assert not journal.exists()
    assert list((journal.parent / "Archive").glob(f"{store_path.name}.*.json"))


def test_publication_recovery_rejects_store_first_state(tmp_path, monkeypatch):
    store_path = tmp_path / "store.json"
    proof_path = merge_drafts.publication_proof_path(store_path)
    authorization = _test_publication_authorization(
        tmp_path, "store-first"
    )
    desired_store = {"way/1": {"verdict": "KEEP", "osm": ["way/1"]}}
    desired_proofs = {"version": 1, "proofs": {}}
    _explicit_publication_baselines(
        monkeypatch, {store_path.name: desired_store}
    )
    plan = _stage_forged_publication(
        store_path, desired_store, desired_proofs, authorization
    )
    merge_drafts.resolver._replace_verified_nofollow(
        plan.paths["store_stage"], store_path,
        merge_drafts.resolver._sha(plan.after_bytes["store"]),
    )
    before_store = store_path.read_bytes()
    before_journal = plan.paths["journal"].read_bytes()

    with pytest.raises(ValueError, match="store advanced before its proof"):
        merge_drafts._commit_publication(
            store_path, desired_store, proof_path, desired_proofs,
            authorization,
        )
    assert store_path.read_bytes() == before_store
    assert not proof_path.exists()
    assert plan.paths["journal"].read_bytes() == before_journal


def test_publication_proofs_are_isolated_per_store(tmp_path, monkeypatch):
    first_store = tmp_path / "first_store.json"
    second_store = tmp_path / "second_store.json"
    first_proof = merge_drafts.publication_proof_path(first_store)
    second_proof = merge_drafts.publication_proof_path(second_store)
    _install_empty_publication_floor(first_store)
    _install_empty_publication_floor(second_store)
    first_rows = {"way/1": {"verdict": "KEEP", "osm": ["way/1"]}}
    second_rows = {"way/2": {"verdict": "DROP", "osm": ["way/2"]}}
    _explicit_publication_baselines(monkeypatch, {
        first_store.name: first_rows,
        second_store.name: second_rows,
    })
    assert first_proof != second_proof
    merge_drafts._commit_publication(
        first_store, first_rows, first_proof,
        {"version": 1, "proofs": {}},
        _test_publication_authorization(tmp_path, "first-store"),
    )
    first_bytes = first_proof.read_bytes()
    merge_drafts._commit_publication(
        second_store, second_rows, second_proof,
        {"version": 1, "proofs": {}},
        _test_publication_authorization(tmp_path, "second-store", fids=(2,)),
    )
    assert first_proof.read_bytes() == first_bytes
    assert json.loads(first_store.read_text())["way/1"]["verdict"] == "KEEP"
    assert json.loads(second_store.read_text())["way/2"]["verdict"] == "DROP"


def test_verified_replace_rejects_stage_entry_swap_before_rename(tmp_path, monkeypatch):
    source = tmp_path / "stage.json"
    target = tmp_path / "target.json"
    outside = tmp_path / "outside.json"
    verified = b"verified bytes"
    source.write_bytes(verified)
    target.write_bytes(b"old bytes")
    outside.write_bytes(verified)
    outside_before = outside.read_bytes()
    expected = merge_drafts.resolver._sha(verified)
    original_replace = merge_drafts.resolver.os.replace
    rename_calls = []

    def swap_then_replace(src, dst, *, src_dir_fd=None, dst_dir_fd=None):
        rename_calls.append((src, dst, src_dir_fd, dst_dir_fd))
        merge_drafts.resolver.os.unlink(source.name, dir_fd=src_dir_fd)
        merge_drafts.resolver.os.symlink(
            str(outside), source.name, dir_fd=src_dir_fd
        )
        return original_replace(
            src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd
        )

    monkeypatch.setattr(merge_drafts.resolver.os, "replace", swap_then_replace)
    merge_drafts.resolver._replace_verified_nofollow(
        source, target, expected
    )

    assert len(rename_calls) == 1
    rename_source, rename_target, source_dir_fd, target_dir_fd = rename_calls[0]
    assert rename_source != source.name
    assert rename_source.startswith(".verified-replace-")
    assert rename_target == target.name
    assert source_dir_fd == target_dir_fd
    assert source.is_symlink()
    assert not target.is_symlink() and target.is_file()
    assert target.read_bytes() == verified
    assert outside.read_bytes() == outside_before


def test_verified_replace_ignores_different_regular_stage_entry_swap(
        tmp_path, monkeypatch):
    source = tmp_path / "stage.json"
    target = tmp_path / "target.json"
    verified = b"captured verified bytes"
    substituted = b"different regular attacker bytes"
    source.write_bytes(verified)
    target.write_bytes(b"old bytes")
    expected = merge_drafts.resolver._sha(verified)
    original_replace = merge_drafts.resolver.os.replace
    swapped = []

    def swap_then_replace(src, dst, *, src_dir_fd=None, dst_dir_fd=None):
        swapped.append(src)
        merge_drafts.resolver.os.unlink(source.name, dir_fd=src_dir_fd)
        source.write_bytes(substituted)
        return original_replace(
            src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd
        )

    monkeypatch.setattr(merge_drafts.resolver.os, "replace", swap_then_replace)
    merge_drafts.resolver._replace_verified_nofollow(
        source, target, expected
    )

    assert len(swapped) == 1 and swapped[0] != source.name
    assert source.read_bytes() == substituted
    assert not target.is_symlink() and target.read_bytes() == verified


def test_verified_replace_dealiases_a_hardlinked_source(tmp_path):
    external = tmp_path / "external.json"
    source = tmp_path / "stage.json"
    target = tmp_path / "target.json"
    verified = b"hardlinked verified bytes"
    external.write_bytes(verified)
    os.link(external, source)
    source_inode = source.stat().st_ino
    target.write_bytes(b"old bytes")

    merge_drafts.resolver._replace_verified_nofollow(
        source, target, merge_drafts.resolver._sha(verified)
    )

    assert source.stat().st_ino == source_inode == external.stat().st_ino
    assert target.stat().st_ino != source_inode
    external.write_bytes(b"mutated through the external hardlink")
    assert source.read_bytes() == b"mutated through the external hardlink"
    assert target.read_bytes() == verified


def test_verified_replace_private_fsync_failure_leaves_target_unchanged(
        tmp_path, monkeypatch):
    source = tmp_path / "stage.json"
    target = tmp_path / "target.json"
    source.write_bytes(b"verified bytes")
    target.write_bytes(b"old bytes")
    before = target.read_bytes()
    fsync_calls = []

    def fail_private_fsync(fd):
        fsync_calls.append(fd)
        raise OSError("simulated private fsync failure")

    monkeypatch.setattr(merge_drafts.resolver.os, "fsync", fail_private_fsync)
    with pytest.raises(OSError, match="simulated private fsync failure"):
        merge_drafts.resolver._replace_verified_nofollow(
            source, target, merge_drafts.resolver._sha(source.read_bytes())
        )

    assert len(fsync_calls) == 1
    assert source.read_bytes() == b"verified bytes"
    assert target.read_bytes() == before
    assert not any(
        path.name.startswith(".verified-replace-")
        for path in tmp_path.iterdir()
    )


def test_verified_replace_short_writes_copy_large_content_exactly(
        tmp_path, monkeypatch):
    source = tmp_path / "stage.json"
    target = tmp_path / "target.json"
    verified = (b"0123456789abcdef" * (72 * 1024)) + b"exact tail"
    assert len(verified) > 1024 * 1024
    source.write_bytes(verified)
    target.write_bytes(b"old bytes")
    original_write = merge_drafts.resolver.os.write
    write_sizes = []

    def short_write(fd, data):
        chunk = data[:4093]
        written = original_write(fd, chunk)
        write_sizes.append(written)
        return written

    monkeypatch.setattr(merge_drafts.resolver.os, "write", short_write)
    merge_drafts.resolver._replace_verified_nofollow(
        source, target, merge_drafts.resolver._sha(verified)
    )

    assert len(write_sizes) > 1 and max(write_sizes) <= 4093
    assert source.read_bytes() == verified
    assert target.read_bytes() == verified


# ------------------------------------------------ human authority transaction


def _authority_confirmation_case(tmp_path, run_name="authority-transaction-run"):
    tmp = _area(
        tmp_path,
        drafts=[[_verdict(2, "DROP", "strong")]],
    )
    packet = json.loads((tmp / "test-co_packets.json").read_text())["2"]
    original = _drafts(tmp)[2]
    authority_run, _ = _write_authority_run(
        tmp_path, tmp, 2, original, packet, run_name
    )
    store = tmp_path / "store.json"
    args = [
        str(store), "test-co", "--confirm", "2=DROP",
        "--judged", "2026-09-28", "--note", "accepted exact source",
        "--reviewer", "trekdex-project-owner",
        "--authority-run", str(authority_run),
        "--review-receipt", str(_review_receipt(authority_run)),
    ]
    return tmp, store, packet, original, authority_run, args


def _authority_decision_case(tmp_path, run_name="decision-transaction-run"):
    tmp = _area(
        tmp_path,
        drafts=[[_verdict(3, "REVIEW", "leaning", hint="inspect access")]],
    )
    packet = json.loads((tmp / "test-co_packets.json").read_text())["3"]
    original = _drafts(tmp)[3]
    authority_run, _ = _write_authority_run(
        tmp_path, tmp, 3, original, packet, run_name
    )
    store = tmp_path / "store.json"
    args = [
        str(store), "test-co", "--decide", "3=DROP",
        "--axis", "public=no",
        "--axis-evidence", "public=reviewer found no public authorization",
        "--judged", "2026-09-28", "--note", "bound access decision",
        "--reviewer", "trekdex-project-owner",
        "--authority-run", str(authority_run),
        "--review-receipt", str(_review_receipt(authority_run)),
    ]
    return tmp, store, packet, original, authority_run, args


def _crash_authority_main(monkeypatch, args, crash_after):
    real_commit = merge_drafts._commit_authority_changes

    def crashing_commit(*call_args, **call_kwargs):
        call_kwargs["crash_after"] = crash_after
        return real_commit(*call_args, **call_kwargs)

    monkeypatch.setattr(
        merge_drafts, "_commit_authority_changes", crashing_commit
    )
    with pytest.raises(RuntimeError, match="simulated crash after authority"):
        merge_drafts.main(args)
    monkeypatch.setattr(
        merge_drafts, "_commit_authority_changes", real_commit
    )


def _authority_journal_document(tmp):
    journal = merge_drafts.tr.authority_journal_path(tmp, "test-co")
    return journal, journal.read_bytes(), json.loads(journal.read_text())


def _optional_bytes(path):
    return path.read_bytes() if path.exists() else None


def _authority_canonical_snapshot(document):
    return {
        key: _optional_bytes(Path(document["paths"][key]))
        for key in ("receipt", "draft", "checkpoint", "journal")
    }


def _authority_transaction_snapshot(tmp):
    root = tmp / ".human-authority-transactions"
    if not root.exists():
        return {}
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@pytest.mark.parametrize(
    "crash_after", ["journal", "receipt", "draft", "checkpoint"]
)
def test_confirmation_transaction_recovers_every_crash_boundary(
        tmp_path, monkeypatch, crash_after):
    tmp, _, packet, _, _, args = _authority_confirmation_case(tmp_path)
    draft = tmp / "test-co_verdict_draft_00.json"
    checkpoint = tmp / "test-co_checkpoint_00.json"
    draft_before = draft.read_bytes()
    checkpoint_before = checkpoint.read_bytes()

    _crash_authority_main(monkeypatch, args, crash_after)
    journal, journal_bytes, document = _authority_journal_document(tmp)
    assert journal.is_file()
    assert merge_drafts.main(args) == 0
    assert not journal.exists()
    archive = Path(document["paths"]["archive"])
    assert archive.read_bytes() == journal_bytes

    row = _drafts(tmp)[2]
    receipt = merge_drafts.authority_receipt_path(
        tmp, "test-co",
        row["human_confirmation"]["authority_receipt_sha256"],
    )
    assert receipt.is_file()
    assert draft.read_bytes() != draft_before
    checkpoint_after = json.loads(checkpoint.read_text())
    checkpoint_before_value = json.loads(checkpoint_before)
    changed = {
        key for key in checkpoint_after
        if checkpoint_after.get(key) != checkpoint_before_value.get(key)
    }
    assert changed == {"draft_sha256"}
    assert checkpoint_after["draft_sha256"] == merge_drafts.resolver._sha(
        draft.read_bytes()
    )
    assert merge_drafts.validate_authority_receipt(row, tmp) == []
    assert merge_drafts._validate_receipt_source(
        json.loads(receipt.read_text()), row, packet
    ) == []
    assert merge_drafts.main(args) == 0


@pytest.mark.parametrize("crash_after", ["draft", "checkpoint"])
def test_bound_decision_transaction_recovers_split_and_complete_crashes(
        tmp_path, monkeypatch, crash_after):
    tmp, _, packet, original, _, args = _authority_decision_case(tmp_path)
    _crash_authority_main(monkeypatch, args, crash_after)
    journal, journal_bytes, document = _authority_journal_document(tmp)

    assert merge_drafts.main(args) == 0
    assert not journal.exists()
    assert Path(document["paths"]["archive"]).read_bytes() == journal_bytes
    row = _drafts(tmp)[3]
    assert row["verdict"] == "DROP"
    assert row["public"] == {
        "call": "no", "evidence": "reviewer found no public authorization"
    }
    assert row["override"]["from_decision"] == original
    errors, projection = merge_drafts.validate_verdict_row(row, packet)
    assert errors == [] and projection == original
    assert merge_drafts.main(args) == 0


def test_changed_human_request_or_authority_run_cannot_recover(
        tmp_path, monkeypatch):
    tmp, _, packet, original, _, args = _authority_confirmation_case(
        tmp_path, "first-authority-run"
    )
    second_run, _ = _write_authority_run(
        tmp_path, tmp, 2, original, packet, "second-authority-run"
    )
    _crash_authority_main(monkeypatch, args, "receipt")
    _, _, document = _authority_journal_document(tmp)
    canonical_before = _authority_canonical_snapshot(document)
    artifacts_before = _authority_transaction_snapshot(tmp)

    changed_note = list(args)
    changed_note[changed_note.index("--note") + 1] = "different human note"
    assert merge_drafts.main(changed_note) == 1
    assert _authority_canonical_snapshot(document) == canonical_before
    assert _authority_transaction_snapshot(tmp) == artifacts_before

    changed_run = list(args)
    changed_run[changed_run.index("--authority-run") + 1] = str(second_run)
    assert merge_drafts.main(changed_run) == 1
    assert _authority_canonical_snapshot(document) == canonical_before
    assert _authority_transaction_snapshot(tmp) == artifacts_before


@pytest.mark.parametrize(
    "corruption", ["missing-stage", "missing-backup", "tampered-backup", "unknown-live"]
)
def test_authority_recovery_rejects_missing_tampered_or_unknown_state_before_mutation(
        tmp_path, monkeypatch, corruption):
    tmp, _, _, _, _, args = _authority_confirmation_case(tmp_path)
    _crash_authority_main(monkeypatch, args, "journal")
    journal, _, document = _authority_journal_document(tmp)
    paths = {key: Path(value) for key, value in document["paths"].items()}

    if corruption == "missing-stage":
        moved = tmp_path / "Archive" / paths["draft_stage"].name
        moved.parent.mkdir(parents=True, exist_ok=True)
        paths["draft_stage"].rename(moved)
    elif corruption == "missing-backup":
        moved = tmp_path / "Archive" / paths["checkpoint_backup"].name
        moved.parent.mkdir(parents=True, exist_ok=True)
        paths["checkpoint_backup"].rename(moved)
    elif corruption == "tampered-backup":
        paths["draft_backup"].write_bytes(b"tampered backup")
    else:
        paths["draft"].write_bytes(b"unknown live draft bytes")

    canonical_before = _authority_canonical_snapshot(document)
    artifacts_before = _authority_transaction_snapshot(tmp)
    assert merge_drafts.main(args) == 1
    assert journal.is_file()
    assert _authority_canonical_snapshot(document) == canonical_before
    assert _authority_transaction_snapshot(tmp) == artifacts_before


def test_exact_confirmation_retry_restores_missing_receipt_and_rejects_altered_receipt(
        tmp_path):
    tmp, _, _, _, _, args = _authority_confirmation_case(tmp_path)
    assert merge_drafts.main(args) == 0
    row = _drafts(tmp)[2]
    receipt = merge_drafts.authority_receipt_path(
        tmp, "test-co",
        row["human_confirmation"]["authority_receipt_sha256"],
    )
    receipt_bytes = receipt.read_bytes()
    moved = tmp_path / "Archive" / receipt.name
    moved.parent.mkdir(parents=True, exist_ok=True)
    receipt.rename(moved)

    assert merge_drafts.main(args) == 0
    assert receipt.read_bytes() == receipt_bytes
    assert not merge_drafts.tr.authority_journal_path(tmp, "test-co").exists()

    receipt.write_bytes(b"altered existing receipt")
    canonical_before = {
        "receipt": receipt.read_bytes(),
        "draft": (tmp / "test-co_verdict_draft_00.json").read_bytes(),
        "checkpoint": (tmp / "test-co_checkpoint_00.json").read_bytes(),
    }
    assert merge_drafts.main(args) == 1
    assert {
        "receipt": receipt.read_bytes(),
        "draft": (tmp / "test-co_verdict_draft_00.json").read_bytes(),
        "checkpoint": (tmp / "test-co_checkpoint_00.json").read_bytes(),
    } == canonical_before
    assert not merge_drafts.tr.authority_journal_path(tmp, "test-co").exists()


def test_recovered_checkpoint_changes_only_draft_hash_and_resolver_accepts_state(
        tmp_path, monkeypatch):
    tmp, _, _, _, _, args = _authority_confirmation_case(tmp_path)
    checkpoint = tmp / "test-co_checkpoint_00.json"
    checkpoint_before = json.loads(checkpoint.read_text())
    _crash_authority_main(monkeypatch, args, "draft")

    assert merge_drafts.main(args) == 0
    checkpoint_after = json.loads(checkpoint.read_text())
    assert {
        key for key in checkpoint_after
        if checkpoint_after.get(key) != checkpoint_before.get(key)
    } == {"draft_sha256"}
    assert checkpoint_after["draft_sha256"] == merge_drafts.resolver._sha(
        (tmp / "test-co_verdict_draft_00.json").read_bytes()
    )
    models = {
        "primary": merge_drafts.tr.model_config("primary", "family-a"),
        "challenger": merge_drafts.tr.model_config("challenger", "family-b"),
        "arbiter": merge_drafts.tr.model_config("arbiter", "family-c"),
    }
    downstream = merge_drafts.resolver.prepare(
        "test-co", tmp, models, tmp_path / "downstream-runs"
    )
    assert (downstream / "prepare.json").is_file()


def test_live_authority_journal_blocks_non_authority_merge_resolver_and_replay(
        tmp_path, monkeypatch, capsys):
    tmp, store, _, _, authority_run, args = _authority_confirmation_case(tmp_path)
    _crash_authority_main(monkeypatch, args, "journal")
    journal, _, document = _authority_journal_document(tmp)
    canonical_before = _authority_canonical_snapshot(document)

    assert merge_drafts.main([str(store), "test-co"]) == 1
    assert "RECOVERY_REQUIRED" in capsys.readouterr().err
    assert merge_drafts.main([str(store), "test-co", "--judged", "2026-09-29", "--write"]) == 1
    assert "RECOVERY_REQUIRED" in capsys.readouterr().err
    assert merge_drafts.judge_packets.main([
        "test-co", "--tmp", str(tmp)
    ]) == 2
    assert "RECOVERY_REQUIRED" in capsys.readouterr().err
    monkeypatch.setattr(
        calibration, "LEDGER", str(tmp_path / "calibration.json")
    )
    with pytest.raises(SystemExit, match="RECOVERY_REQUIRED"):
        calibration.main(["add", "test-co"])
    with pytest.raises(ValueError, match="RECOVERY_REQUIRED"):
        merge_drafts.resolver.prepare(
            "test-co", tmp, {
                "primary": merge_drafts.tr.model_config("p", "family-a"),
                "challenger": merge_drafts.tr.model_config("c", "family-b"),
                "arbiter": merge_drafts.tr.model_config("a", "family-c"),
            }, tmp_path / "blocked-runs",
        )
    assert merge_drafts.resolver.evaluate(authority_run)[0]["state"] == (
        "RECOVERY_REQUIRED"
    )
    assert merge_drafts.resolver.apply_chunk(
        authority_run, 0, apply=False
    )["state"] == "RECOVERY_REQUIRED"
    assert merge_drafts.resolver.apply_chunk(
        authority_run, 0, apply=True
    )["state"] == "RECOVERY_REQUIRED"
    with pytest.raises(ValueError, match="RECOVERY_REQUIRED"):
        merge_drafts.resolver.replay_trust.load_work_area(tmp, "test-co")
    assert _authority_canonical_snapshot(document) == canonical_before
    assert journal.is_file()

    assert merge_drafts.main(args) == 0
    assert not journal.exists()


@pytest.mark.parametrize(
    "mutation",
    ["reviewer", "date", "note", "verdict", "axis", "evidence", "run", "review"],
)
def test_every_bound_human_request_field_is_required_for_recovery(
        tmp_path, monkeypatch, mutation):
    tmp, _, packet, original, _, args = _authority_decision_case(
        tmp_path, "bound-request-first-run"
    )
    second_run = None
    if mutation in ("run", "review"):
        second_run, _ = _write_authority_run(
            tmp_path, tmp, 3, original, packet, "bound-request-second-run"
        )
    _crash_authority_main(monkeypatch, args, "journal")
    _, _, document = _authority_journal_document(tmp)
    canonical_before = _authority_canonical_snapshot(document)
    artifacts_before = _authority_transaction_snapshot(tmp)

    changed = list(args)
    replacements = {
        "reviewer": ("--reviewer", "different-project-owner"),
        "date": ("--judged", "2026-09-29"),
        "note": ("--note", "different bound decision"),
        "verdict": ("--decide", "3=KEEP"),
        "axis": ("--axis", "public=yes"),
        "evidence": (
            "--axis-evidence", "public=different reviewed evidence"
        ),
        "run": ("--authority-run", str(second_run)),
        "review": (
            "--review-receipt",
            str(_review_receipt(second_run)) if second_run is not None else "",
        ),
    }
    option, value = replacements[mutation]
    changed[changed.index(option) + 1] = value

    assert merge_drafts.main(changed) == 1
    assert _authority_canonical_snapshot(document) == canonical_before
    assert _authority_transaction_snapshot(tmp) == artifacts_before


def test_receipt_only_retry_handles_an_exact_receipt_that_preexisted_commit(
        tmp_path):
    tmp, _, _, _, authority_run, args = _authority_confirmation_case(
        tmp_path, "preexisting-receipt-run"
    )
    prepare = merge_drafts._load_authority_prepare(authority_run, "test-co")
    frozen = merge_drafts._authority_frozen_source_drafts(
        tmp, "test-co", authority_run, prepare
    )
    staged_drafts = {}
    staged_receipts = {}
    lines = merge_drafts.apply_confirmations(
        tmp, "test-co", {2: "DROP"}, "accepted exact source",
        "2026-09-28", "trekdex-project-owner", authority_run,
        _review_receipt(authority_run),
        staged_drafts, staged_receipts, source_drafts=frozen,
        check_live_source=False,
    )
    assert not any("!!" in line for line in lines)
    receipt_sha, receipt_value = next(iter(staged_receipts.items()))
    receipt_path = merge_drafts.authority_receipt_path(
        tmp, "test-co", receipt_sha
    )
    merge_drafts.resolver._atomic_bytes(
        receipt_path, merge_drafts.resolver._json_bytes(receipt_value)
    )

    assert merge_drafts.main(args) == 0
    exact_receipt = receipt_path.read_bytes()
    moved = tmp_path / "Archive" / f"preexisting-{receipt_path.name}"
    moved.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.rename(moved)
    assert merge_drafts.main(args) == 0
    assert receipt_path.read_bytes() == exact_receipt


# ------------------------------------------------ review-sheet snapshot locking


def _other_process_can_take_lock(path, shared=False):
    script = (
        "import fcntl, sys\n"
        "handle = open(sys.argv[1], 'a+b')\n"
        "mode = fcntl.LOCK_SH if sys.argv[2] == 'shared' else fcntl.LOCK_EX\n"
        "try:\n"
        "    fcntl.flock(handle.fileno(), mode | fcntl.LOCK_NB)\n"
        "except BlockingIOError:\n"
        "    raise SystemExit(1)\n"
        "raise SystemExit(0)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(path),
         "shared" if shared else "exclusive"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        timeout=5,
    )
    assert result.returncode in (0, 1), result.stderr.decode()
    return result.returncode == 0


def _other_process_can_take_exclusive_lock(path):
    return _other_process_can_take_lock(path, shared=False)


def _other_process_can_take_shared_lock(path):
    return _other_process_can_take_lock(path, shared=True)


def test_merge_main_releases_every_lock_after_unexpected_post_lock_error(
        tmp_path, monkeypatch):
    tmp = _area(tmp_path, drafts=[[_verdict(1, "KEEP")]])
    store = tmp_path / "store.json"
    store.write_bytes(b"{}")
    proof = merge_drafts.publication_proof_path(store)
    journal = merge_drafts._publication_transaction_paths(
        store, proof, "fixed", floor_path=merge_drafts.publication_floor_path(store)
    )["journal"]
    opened = []
    real_open = merge_drafts.resolver._open_lock_file
    real_lexists = merge_drafts.os.path.lexists

    with monkeypatch.context() as patch:
        def recording_open(path):
            handle = real_open(path)
            opened.append((Path(path).resolve(), handle))
            return handle

        def fail_after_all_locks(path):
            if Path(path).resolve() == journal.resolve():
                raise RuntimeError("simulated post-lock failure")
            return real_lexists(path)

        patch.setattr(
            merge_drafts.resolver, "_open_lock_file", recording_open
        )
        patch.setattr(merge_drafts.os.path, "lexists", fail_after_all_locks)
        with pytest.raises(
                RuntimeError, match="simulated post-lock failure") as exc_info:
            merge_drafts.main([str(store), "test-co"])

    assert exc_info.traceback is not None
    assert merge_drafts.tr.area_lock_path(tmp, "test-co").resolve() in {
        path for path, _handle in opened
    }
    assert opened and all(handle.closed for _path, handle in opened)
    assert all(
        _other_process_can_take_exclusive_lock(path)
        for path, _handle in opened
    )
    assert merge_drafts.main([str(store), "test-co"]) == 0


@pytest.mark.parametrize("failure_site", ["open", "flock"])
def test_merge_main_releases_partial_lock_set_after_acquisition_failure(
        tmp_path, monkeypatch, failure_site):
    _area(tmp_path, drafts=[[_verdict(1, "KEEP")]])
    store = tmp_path / "store.json"
    store.write_bytes(b"{}")
    opened = []
    acquisition_calls = 0
    real_open = merge_drafts.resolver._open_lock_file
    real_flock = merge_drafts.fcntl.flock

    with monkeypatch.context() as patch:
        def fail_later_open(path):
            if failure_site == "open" and len(opened) == 2:
                raise RuntimeError("simulated partial lock open failure")
            handle = real_open(path)
            opened.append((Path(path).resolve(), handle))
            return handle

        def fail_later_flock(descriptor, mode):
            nonlocal acquisition_calls
            if mode != merge_drafts.fcntl.LOCK_UN:
                acquisition_calls += 1
                if failure_site == "flock" and acquisition_calls == 3:
                    raise RuntimeError("simulated partial lock flock failure")
            return real_flock(descriptor, mode)

        patch.setattr(
            merge_drafts.resolver, "_open_lock_file", fail_later_open
        )
        patch.setattr(merge_drafts.fcntl, "flock", fail_later_flock)
        with pytest.raises(
                RuntimeError, match=f"simulated partial lock {failure_site} failure"
        ) as exc_info:
            merge_drafts.main([str(store), "test-co"])

    assert exc_info.traceback is not None
    assert len(opened) == (2 if failure_site == "open" else 3)
    assert all(handle.closed for _path, handle in opened)
    assert all(
        _other_process_can_take_exclusive_lock(path)
        for path, _handle in opened
    )


def _unrendered_review_case(tmp_path, run_name):
    tmp = _area(tmp_path, drafts=[[_verdict(1, "KEEP")]])
    packet = json.loads((tmp / "test-co_packets.json").read_text())["1"]
    run, _ = _write_authority_run(
        tmp_path, tmp, 1, _drafts(tmp)[1], packet, run_name,
        render_review=False,
    )
    return tmp, run, review_sheet.review.artifact_paths(
        tmp, "test-co", run.name
    )


def test_review_sheet_holds_exclusive_area_lock_through_render_and_output(
        tmp_path, monkeypatch):
    tmp, run, paths = _unrendered_review_case(tmp_path, "review-exclusive-lock")
    lock_path = review_sheet.tr.area_lock_path(tmp, "test-co")
    observed = []
    real_build = review_sheet.review.build_review_artifacts
    real_write = review_sheet.resolver._write_review_artifact

    def build_under_lock(*args, **kwargs):
        observed.append(("render", _other_process_can_take_shared_lock(lock_path)))
        return real_build(*args, **kwargs)

    def write_under_lock(*args, **kwargs):
        observed.append(("write", _other_process_can_take_shared_lock(lock_path)))
        return real_write(*args, **kwargs)

    monkeypatch.setattr(
        review_sheet.review, "build_review_artifacts", build_under_lock
    )
    monkeypatch.setattr(
        review_sheet.resolver, "_write_review_artifact", write_under_lock
    )
    assert review_sheet.main([
        "test-co", "--authority-run", str(run), "--sample", "0",
    ]) == 0
    assert paths["sheet"].is_file() and paths["receipt"].is_file()
    assert observed == [("render", False), ("write", False), ("write", False)]
    assert _other_process_can_take_exclusive_lock(lock_path)


def test_review_sheet_refuses_preexisting_authority_journal_without_output(
        tmp_path):
    tmp, run, paths = _unrendered_review_case(
        tmp_path, "review-preexisting-journal"
    )
    journal = review_sheet.tr.authority_journal_path(tmp, "test-co")
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text("{}")
    with pytest.raises(SystemExit, match="RECOVERY_REQUIRED"):
        review_sheet.main([
            "test-co", "--authority-run", str(run), "--sample", "0",
        ])
    assert not paths["directory"].exists()


def test_review_sheet_detects_noncooperating_journal_after_output(
        tmp_path, monkeypatch):
    tmp, run, paths = _unrendered_review_case(tmp_path, "review-late-journal")
    journal = review_sheet.tr.authority_journal_path(tmp, "test-co")
    real_write = review_sheet.resolver._write_review_artifact
    writes = 0

    def write_then_inject(*args, **kwargs):
        nonlocal writes
        result = real_write(*args, **kwargs)
        writes += 1
        if writes == 2:
            journal.parent.mkdir(parents=True, exist_ok=True)
            journal.write_text("{}")
        return result

    monkeypatch.setattr(
        review_sheet.resolver, "_write_review_artifact", write_then_inject
    )
    with pytest.raises(SystemExit, match="RECOVERY_REQUIRED"):
        review_sheet.main([
            "test-co", "--authority-run", str(run), "--sample", "0",
        ])
    assert paths["sheet"].is_file() and paths["receipt"].is_file()


def test_review_sheet_rejects_arbitrary_out_before_creating_artifacts(tmp_path):
    tmp, run, paths = _unrendered_review_case(tmp_path, "review-no-out")
    with pytest.raises(SystemExit):
        review_sheet.main([
            "test-co", "--authority-run", str(run),
            "--out", str(tmp_path / "attacker.html"),
        ])
    assert not paths["directory"].exists()


# ------------------------------------------------ publication tail recovery


def _real_publication_case(tmp_path, monkeypatch, run_name):
    incoming = _verdict(1, "KEEP", osm=["way/1"])
    tmp = _area(tmp_path, drafts=[[incoming]])
    packet = json.loads((tmp / "test-co_packets.json").read_text())["1"]
    run, _ = _terminal_machine_run(
        tmp_path, tmp, 1, incoming, packet, run_name
    )
    store = tmp_path / "store.json"
    store.write_bytes(b"{}")
    proof = merge_drafts.publication_proof_path(store)
    _install_empty_publication_floor(store)
    _install_rooted_publication_configuration(
        tmp_path, [store.name], monkeypatch
    )
    monkeypatch.setattr(
        merge_drafts, "_terminal_resolver_errors", _REAL_TERMINAL_GATE
    )
    args = [
        str(store), "test-co", "--resolver-run", str(run),
        "--judged", "2026-09-29", "--write",
    ]
    journal = merge_drafts._publication_transaction_paths(
        store, proof, "fixed"
    )["journal"]
    return store, proof, journal, args


def _publication_transaction_snapshot(journal):
    root = journal.parent
    if not root.exists():
        return {}
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@pytest.mark.parametrize(
    "crash_point", [
        "after-floor", "after-store", "after-candidate",
        "before-archive", "archive-installed",
    ]
)
def test_publication_exact_plan_recovers_every_tail_crash(
        tmp_path, monkeypatch, crash_point):
    store, proof, journal, args = _real_publication_case(
        tmp_path, monkeypatch, f"publication-tail-{crash_point}"
    )
    floor = merge_drafts.publication_floor_path(store)
    real_replace = merge_drafts.resolver._replace_verified_nofollow

    def crashing_replace(source, target, expected_sha256, *,
                         retire_trusted_source=False):
        if retire_trusted_source:
            if crash_point == "before-archive":
                raise RuntimeError("simulated crash before publication archive")
            if crash_point == "archive-installed":
                real_replace(
                    source, target, expected_sha256,
                    retire_trusted_source=False,
                )
                raise RuntimeError("simulated crash after publication archive")
        result = real_replace(
            source, target, expected_sha256,
            retire_trusted_source=retire_trusted_source,
        )
        if crash_point == "after-floor" and Path(target) == floor:
            raise RuntimeError("simulated crash after publication floor")
        if crash_point == "after-store" and Path(target) == store:
            raise RuntimeError("simulated crash after publication store")
        if (crash_point == "after-candidate"
                and "candidates" in Path(target).parts):
            raise RuntimeError("simulated crash after publication candidate root")
        return result

    monkeypatch.setattr(
        merge_drafts.resolver, "_replace_verified_nofollow", crashing_replace
    )
    with pytest.raises(RuntimeError, match="simulated crash"):
        merge_drafts.main(args)
    monkeypatch.setattr(
        merge_drafts.resolver, "_replace_verified_nofollow", real_replace
    )

    assert journal.is_file()
    journal_bytes = journal.read_bytes()
    journal_document = json.loads(journal_bytes)
    candidate = Path(journal_document["paths"]["candidate"])
    candidate_at_crash = merge_drafts._read_optional_bytes(candidate)
    if crash_point == "after-candidate":
        assert candidate_at_crash is not None
    store_bytes = merge_drafts._read_optional_bytes(store)
    proof_bytes = proof.read_bytes()
    floor_bytes = floor.read_bytes()
    assert merge_drafts.main(args) == 0
    assert not journal.exists()
    if crash_point == "after-floor":
        assert store_bytes == b"{}"
        assert store.read_bytes() != store_bytes
    else:
        assert store.read_bytes() == store_bytes
    assert proof.read_bytes() == proof_bytes
    assert floor.read_bytes() == floor_bytes
    assert candidate.is_file()
    assert merge_drafts.resolver._sha(candidate.read_bytes()) == (
        journal_document["publication_trust_root"]["candidate_root_sha256"]
    )
    if candidate_at_crash is not None:
        assert candidate.read_bytes() == candidate_at_crash
    archives = list((journal.parent / "Archive").glob(
        f"{store.name}.*.json"
    ))
    assert len(archives) == 1
    assert archives[0].read_bytes() == journal_bytes


def test_publication_recovery_rejects_corrupted_installed_archive_without_mutation(
        tmp_path, monkeypatch):
    store, proof, journal, args = _real_publication_case(
        tmp_path, monkeypatch, "publication-corrupt-archive"
    )
    real_replace = merge_drafts.resolver._replace_verified_nofollow
    installed_archive = None

    def crash_after_archive(source, target, expected_sha256, *,
                            retire_trusted_source=False):
        nonlocal installed_archive
        if retire_trusted_source:
            installed_archive = Path(target)
            real_replace(
                source, target, expected_sha256,
                retire_trusted_source=False,
            )
            raise RuntimeError("simulated crash after publication archive")
        return real_replace(
            source, target, expected_sha256,
            retire_trusted_source=False,
        )

    monkeypatch.setattr(
        merge_drafts.resolver, "_replace_verified_nofollow", crash_after_archive
    )
    with pytest.raises(RuntimeError, match="simulated crash"):
        merge_drafts.main(args)
    monkeypatch.setattr(
        merge_drafts.resolver, "_replace_verified_nofollow", real_replace
    )
    assert installed_archive is not None and installed_archive.is_file()
    installed_archive.write_bytes(b"corrupted archive")
    canonical_before = (store.read_bytes(), proof.read_bytes(), journal.read_bytes())
    transaction_before = _publication_transaction_snapshot(journal)

    assert merge_drafts.main(args) == 1
    assert (store.read_bytes(), proof.read_bytes(), journal.read_bytes()) == (
        canonical_before
    )
    assert _publication_transaction_snapshot(journal) == transaction_before


def test_publication_recovery_requires_the_original_explicit_judged_date(
        tmp_path, monkeypatch):
    store, proof, journal, args = _real_publication_case(
        tmp_path, monkeypatch, "publication-date-bound"
    )
    real_replace = merge_drafts.resolver._replace_verified_nofollow

    def crash_after_proof(source, target, expected_sha256, *,
                          retire_trusted_source=False):
        result = real_replace(
            source, target, expected_sha256,
            retire_trusted_source=retire_trusted_source,
        )
        if Path(target) == proof:
            raise RuntimeError("simulated crash after publication proof")
        return result

    monkeypatch.setattr(
        merge_drafts.resolver, "_replace_verified_nofollow", crash_after_proof
    )
    with pytest.raises(RuntimeError, match="simulated crash"):
        merge_drafts.main(args)
    monkeypatch.setattr(
        merge_drafts.resolver, "_replace_verified_nofollow", real_replace
    )
    before = (store.exists(), proof.read_bytes(), journal.read_bytes())
    changed_date = list(args)
    changed_date[changed_date.index("--judged") + 1] = "2026-09-30"

    assert merge_drafts.main(changed_date) == 1
    assert (store.exists(), proof.read_bytes(), journal.read_bytes()) == before
    assert merge_drafts.main(args) == 0
    assert store.is_file() and not journal.exists()


# -------------------------------------- hostile review evidence and replay


def test_review_artifacts_are_canonical_private_closed_and_idempotent(tmp_path):
    tmp, run, paths = _unrendered_review_case(tmp_path, "review-private")
    args = ["test-co", "--authority-run", str(run), "--sample", "1"]
    assert review_sheet.main(args) == 0
    expected_directory = (
        tmp.resolve() / ".review-artifacts" / "test-co" / run.name
    )
    assert paths["directory"] == expected_directory
    for parent in (
        tmp / ".review-artifacts",
        tmp / ".review-artifacts" / "test-co",
        expected_directory,
    ):
        value = parent.stat()
        assert value.st_uid == os.geteuid()
        assert value.st_mode & 0o777 == 0o700
    for target in (paths["sheet"], paths["receipt"]):
        value = target.stat()
        assert value.st_uid == os.geteuid()
        assert value.st_mode & 0o777 == 0o600
        assert value.st_nlink == 1

    receipt = json.loads(paths["receipt"].read_text())
    assert review_sheet.review.validate_review_receipt(receipt) == []
    assert receipt["source_run_id"] == run.name
    assert receipt["source_prepare_sha256"] == hashlib.sha256(
        (run / "prepare.json").read_bytes()
    ).hexdigest()
    assert receipt["sheet_sha256"] == hashlib.sha256(
        paths["sheet"].read_bytes()
    ).hexdigest()
    assert [item["fid"] for item in receipt["items"]] == [1]
    item = receipt["items"][0]
    assert set(item["tile_sha256"]) == {"z1", "z2", "z3"}
    assert item["primary"]["envelope_sha256"] == item["primary_envelope_sha256"]
    page = paths["sheet"].read_text()
    assert "data:image/png;base64," in page
    assert "file://" not in page and "https://" not in page

    before = {
        target: (target.read_bytes(), target.stat().st_ino)
        for target in (paths["sheet"], paths["receipt"])
    }
    assert review_sheet.main(args) == 0
    assert {
        target: (target.read_bytes(), target.stat().st_ino)
        for target in (paths["sheet"], paths["receipt"])
    } == before


@pytest.mark.parametrize("hostile", ["wrong", "symlink", "hardlink", "directory"])
def test_review_receipt_hostile_existing_target_fails_without_replacement(
        tmp_path, hostile):
    tmp, run, paths = _unrendered_review_case(
        tmp_path, f"review-hostile-{hostile}"
    )
    for parent in (
        tmp / ".review-artifacts",
        tmp / ".review-artifacts" / "test-co",
        paths["directory"],
    ):
        parent.mkdir(mode=0o700, exist_ok=True)
        parent.chmod(0o700)
    target = paths["receipt"]
    if hostile == "wrong":
        target.write_bytes(b"wrong existing receipt")
        target.chmod(0o600)
    elif hostile == "symlink":
        target.symlink_to(run / "prepare.json")
    elif hostile == "hardlink":
        source = tmp_path / "external-hardlink-source"
        source.write_bytes(b"hardlinked receipt")
        source.chmod(0o600)
        os.link(source, target)
    else:
        target.mkdir(mode=0o700)
    before = os.lstat(target)
    with pytest.raises(SystemExit):
        review_sheet.main([
            "test-co", "--authority-run", str(run), "--sample", "0",
        ])
    after = os.lstat(target)
    assert (after.st_dev, after.st_ino, after.st_mode, after.st_nlink) == (
        before.st_dev, before.st_ino, before.st_mode, before.st_nlink
    )
    assert not paths["sheet"].exists()


def test_review_artifact_rejects_nonprivate_parent_without_chmod_or_output(
        tmp_path):
    tmp, run, paths = _unrendered_review_case(tmp_path, "review-bad-parent")
    root = tmp / ".review-artifacts"
    root.mkdir(mode=0o755)
    root.chmod(0o755)
    before_mode = root.stat().st_mode & 0o777
    with pytest.raises(SystemExit, match="owner-only directory"):
        review_sheet.main([
            "test-co", "--authority-run", str(run), "--sample", "0",
        ])
    assert root.stat().st_mode & 0o777 == before_mode
    assert not paths["sheet"].exists() and not paths["receipt"].exists()


def test_confirm_and_decide_require_exactly_one_review_receipt_without_mutation(
        tmp_path):
    tmp, _, _, _, _, confirm_args = _authority_confirmation_case(tmp_path)
    draft = tmp / "test-co_verdict_draft_00.json"
    checkpoint = tmp / "test-co_checkpoint_00.json"
    before = (draft.read_bytes(), checkpoint.read_bytes())
    omitted = list(confirm_args)
    index = omitted.index("--review-receipt")
    del omitted[index:index + 2]
    with pytest.raises(SystemExit, match="exactly one --review-receipt"):
        merge_drafts.main(omitted)
    duplicate = list(confirm_args) + [
        "--review-receipt", confirm_args[confirm_args.index("--review-receipt") + 1]
    ]
    with pytest.raises(SystemExit, match="exactly one --review-receipt"):
        merge_drafts.main(duplicate)

    decision_root = tmp_path / "decision"
    decision_root.mkdir()
    _, _, _, _, _, decide_args = _authority_decision_case(
        decision_root
    )
    omitted_decide = list(decide_args)
    index = omitted_decide.index("--review-receipt")
    del omitted_decide[index:index + 2]
    with pytest.raises(SystemExit, match="exactly one --review-receipt"):
        merge_drafts.main(omitted_decide)
    assert (draft.read_bytes(), checkpoint.read_bytes()) == before
    assert not (tmp / merge_drafts.AUTHORITY_RECEIPT_DIR).exists()
    assert not merge_drafts.tr.authority_journal_path(tmp, "test-co").exists()


@pytest.mark.parametrize(
    "mutation",
    ["sheet", "coherent-receipt", "frozen-source", "live-publication",
     "wrong-run", "wrong-fid", "wrong-area"],
)
def test_invalid_review_or_source_blocks_authority_with_zero_canonical_mutation(
        tmp_path, mutation):
    tmp, _, _, original, authority_run, args = _authority_confirmation_case(
        tmp_path, f"invalid-review-{mutation}"
    )
    draft = tmp / "test-co_verdict_draft_00.json"
    checkpoint = tmp / "test-co_checkpoint_00.json"
    before = (draft.read_bytes(), checkpoint.read_bytes())
    receipt_path = _review_receipt(authority_run)
    if mutation == "sheet":
        receipt = json.loads(receipt_path.read_text())
        Path(receipt["sheet_path"]).write_bytes(b"altered reviewed sheet")
    elif mutation == "coherent-receipt":
        receipt = json.loads(receipt_path.read_text())
        receipt["sheet_sha256"] = "0" * 64
        body = dict(receipt)
        body.pop("receipt_sha256")
        receipt["receipt_sha256"] = merge_drafts.tr.sha256_json(body)
        receipt_path.write_bytes(review_sheet.review.json_bytes(receipt))
    elif mutation == "frozen-source":
        prepare = json.loads((authority_run / "prepare.json").read_text())
        packet_path = authority_run / prepare["items"][0]["packet_path"]
        packet_path.write_bytes(packet_path.read_bytes() + b" ")
    elif mutation == "live-publication":
        dossier = tmp / "test-co_dossier.json"
        dossier.write_bytes(dossier.read_bytes() + b" ")
    elif mutation == "wrong-run":
        packet = json.loads((tmp / "test-co_packets.json").read_text())["2"]
        second_run, _ = _write_authority_run(
            tmp_path, tmp, 2, original, packet,
            "invalid-review-second-run",
        )
        args[args.index("--review-receipt") + 1] = str(
            _review_receipt(second_run)
        )
    elif mutation == "wrong-fid":
        args[args.index("--confirm") + 1] = "999=DROP"
    else:
        args[args.index("--review-receipt") + 1] = str(
            tmp / ".review-artifacts" / "other-co" / authority_run.name
            / "review-receipt.json"
        )
    assert merge_drafts.main(args) == 1
    assert (draft.read_bytes(), checkpoint.read_bytes()) == before
    assert not (tmp / merge_drafts.AUTHORITY_RECEIPT_DIR).exists()
    assert not merge_drafts.tr.authority_journal_path(tmp, "test-co").exists()


def _published_human_review_case(tmp_path, monkeypatch, tag):
    tmp = _area(tmp_path, drafts=[[_verdict(2, "DROP", "strong")]])
    packet = json.loads((tmp / "test-co_packets.json").read_text())["2"]
    original = _drafts(tmp)[2]
    authority_run, _ = _write_authority_run(
        tmp_path, tmp, 2, original, packet, f"{tag}-authority"
    )
    store = tmp_path / "store.json"
    _install_empty_publication_floor(store)
    assert merge_drafts.main([
        str(store), "test-co", "--confirm", "2=DROP",
        "--judged", "2026-09-29", "--note", "exact frozen review",
        "--reviewer", "trekdex-project-owner",
        "--authority-run", str(authority_run),
        "--review-receipt", str(_review_receipt(authority_run)),
    ]) == 0
    confirmed = _drafts(tmp)[2]
    terminal_run, _ = _write_authority_run(
        tmp_path, tmp, 2, confirmed, packet, f"{tag}-terminal"
    )
    assert merge_drafts.resolver.apply_chunk(
        terminal_run, 0, apply=True
    )["state"] == "PRESERVED"
    monkeypatch.setattr(
        merge_drafts, "_terminal_resolver_errors", _REAL_TERMINAL_GATE
    )
    assert merge_drafts.main([
        str(store), "test-co", "--resolver-run", str(terminal_run),
        "--judged", "2026-09-29", "--write",
    ]) == 0
    stored = json.loads(store.read_text())["way/2"]
    registry = json.loads(
        merge_drafts.publication_proof_path(store).read_text()
    )
    return stored, registry


def _rehash_proof_and_attestation(stored, registry, proof):
    body = copy.deepcopy(proof)
    body.pop("proof_sha256", None)
    proof_sha = merge_drafts.tr.sha256_json(body)
    proof = {**body, "proof_sha256": proof_sha}
    row = copy.deepcopy(stored)
    attestation = row["publication_attestation"]
    attestation["publication_proof_sha256"] = proof_sha
    attestation_body = dict(attestation)
    attestation_body.pop("attestation_sha256", None)
    attestation["attestation_sha256"] = merge_drafts.tr.sha256_json(
        attestation_body
    )
    return row, {
        "version": registry["version"], "proofs": {proof_sha: proof}
    }


@pytest.mark.parametrize(
    "tamper",
    ["sheet", "coherent-receipt", "missing-source", "extra-source",
     "missing-review", "wrong-fid"],
)
def test_publication_proof_rejects_review_tampering_even_after_coherent_rehash(
        tmp_path, monkeypatch, tamper):
    stored, registry = _published_human_review_case(
        tmp_path, monkeypatch, f"proof-{tamper}"
    )
    original_sha = stored["publication_attestation"]["publication_proof_sha256"]
    proof = copy.deepcopy(registry["proofs"][original_sha])
    review_sha = stored["human_confirmation"]["review_receipt_sha256"]
    if tamper == "missing-review":
        proof["human_reviews"].pop(review_sha)
    else:
        bundle = proof["human_reviews"][review_sha]
        if tamper == "sheet":
            raw = base64.b64decode(bundle["sheet_b64"])
            bundle["sheet_b64"] = base64.b64encode(raw + b"altered").decode()
        elif tamper == "coherent-receipt":
            receipt = json.loads(base64.b64decode(bundle["receipt_b64"]))
            receipt["sheet_sha256"] = "0" * 64
            body = dict(receipt)
            body.pop("receipt_sha256")
            receipt["receipt_sha256"] = merge_drafts.tr.sha256_json(body)
            bundle["receipt_b64"] = base64.b64encode(
                review_sheet.review.json_bytes(receipt)
            ).decode()
        elif tamper == "missing-source":
            bundle["source_artifacts_b64"].pop(
                sorted(bundle["source_artifacts_b64"])[0]
            )
        elif tamper == "extra-source":
            bundle["source_artifacts_b64"]["extra.bin"] = base64.b64encode(
                b"extra"
            ).decode()
        else:
            receipt = json.loads(base64.b64decode(bundle["receipt_b64"]))
            receipt["items"][0]["fid"] = 999
            item_body = dict(receipt["items"][0])
            item_body.pop("review_item_sha256")
            receipt["items"][0]["review_item_sha256"] = (
                merge_drafts.tr.sha256_json(item_body)
            )
            body = dict(receipt)
            body.pop("receipt_sha256")
            receipt["receipt_sha256"] = merge_drafts.tr.sha256_json(body)
            bundle["receipt_b64"] = base64.b64encode(
                review_sheet.review.json_bytes(receipt)
            ).decode()
    row, forged_registry = _rehash_proof_and_attestation(
        stored, registry, proof
    )
    assert merge_drafts.validate_publication_attestation(row) == []
    errors = merge_drafts.resolver.replay_trust._validate_publication_proof(
        row, forged_registry
    )
    assert errors
    assert any("review" in error for error in errors)


def test_current_publication_proof_embeds_exact_closed_review_evidence(
        tmp_path, monkeypatch):
    stored, registry = _published_human_review_case(
        tmp_path, monkeypatch, "proof-exact"
    )
    assert merge_drafts.validate_publication_attestation(stored) == []
    proof = registry["proofs"][
        stored["publication_attestation"]["publication_proof_sha256"]
    ]
    assert proof["version"] == merge_drafts.PUBLICATION_PROOF_VERSION
    review_sha = stored["human_confirmation"]["review_receipt_sha256"]
    assert set(proof["human_reviews"]) == {review_sha}
    bundle = proof["human_reviews"][review_sha]
    receipt = json.loads(base64.b64decode(bundle["receipt_b64"]))
    assert receipt["receipt_sha256"] == review_sha
    assert hashlib.sha256(base64.b64decode(bundle["sheet_b64"])).hexdigest() == (
        receipt["sheet_sha256"]
    )
    artifact_names = set(bundle["source_artifacts_b64"])
    assert "source/packets.json" in artifact_names
    assert any(name.startswith("source/draft-") for name in artifact_names)
    assert any(name.startswith("packets/fid-") for name in artifact_names)
    assert any(name.startswith("tiles/") for name in artifact_names)

    # Replay must be self-contained: make both original authority inputs
    # unavailable before validation, rather than accidentally reading them.
    hidden = tmp_path / "hidden-original-review-inputs"
    hidden.mkdir()
    source_run = Path(receipt["source_run_path"])
    review_directory = Path(receipt["sheet_path"]).parent
    source_run.rename(hidden / "authority-run")
    review_directory.rename(hidden / "review-artifacts")
    assert not source_run.exists() and not review_directory.exists()
    assert merge_drafts.resolver.replay_trust._validate_publication_proof(
        stored, registry
    ) == []


def test_legacy_authority_receipt_remains_readable_but_current_cli_emits_only_v2(
        tmp_path):
    tmp, _, packet, _, authority_run, args = _authority_confirmation_case(
        tmp_path, "legacy-receipt-control"
    )
    assert merge_drafts.main(args) == 0
    current_row = _drafts(tmp)[2]
    current_wrapper = current_row["human_confirmation"]
    assert current_wrapper["version"] == merge_drafts.CONFIRMATION_VERSION
    current_receipt = json.loads(
        merge_drafts.authority_receipt_path(
            tmp, "test-co", current_wrapper["authority_receipt_sha256"]
        ).read_text()
    )
    assert current_receipt["version"] == merge_drafts.AUTHORITY_RECEIPT_VERSION

    legacy_row = copy.deepcopy(current_row)
    wrapper = legacy_row["human_confirmation"]
    wrapper["version"] = 1
    for field in (
        "review_receipt_sha256", "review_sheet_sha256", "review_item_sha256",
    ):
        wrapper.pop(field)
    legacy_receipt = copy.deepcopy(current_receipt)
    legacy_receipt["version"] = 1
    legacy_receipt["authority_kind"] = "human_confirmation"
    for field in (
        "review_receipt_sha256", "review_receipt_path",
        "review_sheet_sha256", "review_sheet_path", "review_item_sha256",
    ):
        legacy_receipt.pop(field)
    payload = copy.deepcopy(wrapper)
    payload.pop("authority_receipt_sha256")
    legacy_receipt["authority_payload"] = payload
    body = dict(legacy_receipt)
    body.pop("receipt_sha256")
    legacy_sha = merge_drafts.tr.sha256_json(body)
    legacy_receipt["receipt_sha256"] = legacy_sha
    wrapper["authority_receipt_sha256"] = legacy_sha
    assert merge_drafts.validate_authority_receipt(
        legacy_row, tmp, pending={legacy_sha: legacy_receipt}
    ) == []
    assert merge_drafts._validate_receipt_source(
        legacy_receipt, legacy_row, packet
    ) == []


def test_generation_bound_publication_proofs_cannot_downgrade(
        tmp_path, monkeypatch):
    machine_root = tmp_path / "machine"
    machine_root.mkdir()
    store, proof_path, _, args = _real_publication_case(
        machine_root, monkeypatch, "legacy-proof-machine"
    )
    assert merge_drafts.main(args) == 0
    machine_row = json.loads(store.read_text())["way/1"]
    registry = json.loads(proof_path.read_text())
    current_sha = machine_row["publication_attestation"][
        "publication_proof_sha256"
    ]
    legacy_proof = copy.deepcopy(registry["proofs"][current_sha])
    legacy_proof["version"] = merge_drafts.LEGACY_PUBLICATION_PROOF_VERSION
    legacy_proof.pop("human_reviews")
    legacy_row, legacy_registry = _rehash_proof_and_attestation(
        machine_row, registry, legacy_proof
    )
    attestation = legacy_row["publication_attestation"]
    attestation["version"] = 1
    attestation.pop("review_receipt_sha256")
    body = dict(attestation)
    body.pop("attestation_sha256")
    attestation["attestation_sha256"] = merge_drafts.tr.sha256_json(body)
    assert merge_drafts.validate_publication_attestation(legacy_row) == []
    assert "publication proof/prepare generation version mismatch" in (
        merge_drafts.resolver.replay_trust._validate_publication_proof(
            legacy_row, legacy_registry
        )
    )

    human_root = tmp_path / "human"
    human_root.mkdir()
    human_row, human_registry = _published_human_review_case(
        human_root, monkeypatch, "legacy-proof-human"
    )
    human_sha = human_row["publication_attestation"][
        "publication_proof_sha256"
    ]
    downgraded = copy.deepcopy(human_registry["proofs"][human_sha])
    downgraded["version"] = merge_drafts.LEGACY_PUBLICATION_PROOF_VERSION
    downgraded.pop("human_reviews")
    downgraded_row, downgraded_registry = _rehash_proof_and_attestation(
        human_row, human_registry, downgraded
    )
    downgraded_attestation = downgraded_row["publication_attestation"]
    downgraded_attestation["version"] = 1
    downgraded_attestation.pop("review_receipt_sha256")
    body = dict(downgraded_attestation)
    body.pop("attestation_sha256")
    downgraded_attestation["attestation_sha256"] = (
        merge_drafts.tr.sha256_json(body)
    )
    assert merge_drafts.validate_publication_attestation(downgraded_row)
    assert merge_drafts.resolver.replay_trust._validate_publication_proof(
        downgraded_row, downgraded_registry
    )


def test_review_sheet_releases_area_lock_before_open(tmp_path, monkeypatch):
    tmp, run, _ = _unrendered_review_case(tmp_path, "review-open-unlocked")
    lock_path = review_sheet.tr.area_lock_path(tmp, "test-co")
    opened = []

    def observe_open(command, check=False):
        opened.append((command, _other_process_can_take_exclusive_lock(lock_path)))
        return subprocess.CompletedProcess(command, 0)

    class LocalOpen:
        run = staticmethod(observe_open)

    monkeypatch.setattr(review_sheet, "subprocess", LocalOpen)
    assert review_sheet.main([
        "test-co", "--authority-run", str(run), "--sample", "0", "--open",
    ]) == 0
    assert len(opened) == 1 and opened[0][1] is True


# ------------------------------------------------ review artifact crash/totality


def test_review_artifact_partial_private_write_never_creates_canonical_target(
        tmp_path, monkeypatch):
    tmp, run, paths = _unrendered_review_case(
        tmp_path, "review-private-write-crash"
    )
    real_write = review_sheet.resolver.os.write
    writes = 0

    def short_then_fail(fd, data):
        nonlocal writes
        writes += 1
        if writes == 1:
            return real_write(fd, data[:17])
        raise OSError("simulated review private-write failure")

    monkeypatch.setattr(review_sheet.resolver.os, "write", short_then_fail)
    with pytest.raises(SystemExit, match="simulated review private-write failure"):
        review_sheet.main([
            "test-co", "--authority-run", str(run), "--sample", "0",
        ])
    assert not paths["sheet"].exists()
    assert not paths["receipt"].exists()
    if paths["directory"].exists():
        assert not any(
            path.name.startswith((".review.html.install-", ".review-receipt.json.install-"))
            for path in paths["directory"].iterdir()
        )

    monkeypatch.setattr(review_sheet.resolver.os, "write", real_write)
    assert review_sheet.main([
        "test-co", "--authority-run", str(run), "--sample", "0",
    ]) == 0
    assert paths["sheet"].is_file() and paths["receipt"].is_file()


@pytest.mark.parametrize("malformed_fid", [[], {}, True, 1.0, None])
def test_review_receipt_validator_is_total_for_nested_json_fid_types(
        tmp_path, malformed_fid):
    tmp, run, paths = _unrendered_review_case(
        tmp_path, f"review-receipt-total-{type(malformed_fid).__name__}"
    )
    assert review_sheet.main([
        "test-co", "--authority-run", str(run), "--sample", "0",
    ]) == 0
    receipt = json.loads(paths["receipt"].read_text())
    receipt["items"][0]["fid"] = copy.deepcopy(malformed_fid)

    errors = review_sheet.review.validate_review_receipt(receipt)
    assert errors
    assert any("coordinates" in error or "sorted and unique" in error
               for error in errors)


# Independent filesystem review: immutable review writers serialize as a pair.


def test_review_sheet_different_sample_processes_serialize_without_mixed_pair(
        tmp_path):
    tmp, run, paths = _unrendered_review_case(
        tmp_path, "review-process-serialization"
    )
    marker = tmp_path / "first-render-entered"
    release = tmp_path / "release-first-render"
    script = (
        "import pathlib, sys, time\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import judge_review_sheet as sheet\n"
        "marker = pathlib.Path(sys.argv[2])\n"
        "release = pathlib.Path(sys.argv[3])\n"
        "real = sheet.review.build_review_artifacts\n"
        "def blocked(*args, **kwargs):\n"
        "    marker.write_text('locked')\n"
        "    deadline = time.monotonic() + 10\n"
        "    while not release.exists():\n"
        "        if time.monotonic() >= deadline:\n"
        "            raise RuntimeError('test release timeout')\n"
        "        time.sleep(0.01)\n"
        "    return real(*args, **kwargs)\n"
        "sheet.review.build_review_artifacts = blocked\n"
        "raise SystemExit(sheet.main([\n"
        "    'test-co', '--authority-run', sys.argv[4], '--sample', '0'\n"
        "]))\n"
    )
    environment = dict(os.environ)
    environment["PADJ_TMP"] = str(tmp)
    first = subprocess.Popen(
        [sys.executable, "-c", script, str(TOOLS), str(marker),
         str(release), str(run)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment,
    )
    deadline = time.monotonic() + 10
    while not marker.exists() and first.poll() is None:
        if time.monotonic() >= deadline:
            first.kill()
            raise AssertionError("first review writer never entered render")
        time.sleep(0.01)
    assert marker.exists() and first.poll() is None

    second = subprocess.Popen(
        [sys.executable, str(TOOLS / "judge_review_sheet.py"),
         "test-co", "--authority-run", str(run), "--sample", "1"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment,
    )
    time.sleep(0.2)
    assert second.poll() is None  # blocked behind the first writer's LOCK_EX
    release.write_text("continue")

    first_stdout, first_stderr = first.communicate(timeout=10)
    second_stdout, second_stderr = second.communicate(timeout=10)
    assert first.returncode == 0, first_stderr.decode()
    assert second.returncode != 0
    assert b"existing immutable review artifact has different bytes" in second_stderr
    assert paths["sheet"].is_file() and paths["receipt"].is_file()
    receipt = json.loads(paths["receipt"].read_text())
    assert receipt["sample"]["requested"] == 0
    assert receipt["sample"]["selected_fids"] == []
    assert receipt["sheet_sha256"] == hashlib.sha256(
        paths["sheet"].read_bytes()
    ).hexdigest()
    assert review_sheet.review.validate_review_receipt(receipt) == []


# Independent filesystem review: continuation retirement preserves every race.


def test_quarantine_directory_routes_unique_child_through_durable_creator(
        tmp_path, monkeypatch):
    judge_packets = _load(
        "judge_packets_quarantine_durable_directory", TOOLS / "judge_packets.py"
    )
    archive_parent = tmp_path / "Archive"
    archive_parent.mkdir(mode=0o700)
    quarantine_root = archive_parent / "continuation-quarantine"
    quarantine_root.mkdir(mode=0o700)
    capture = SimpleNamespace(
        path=tmp_path / "test-co_verdict_continue_00.json",
        sha256="a" * 64,
    )
    archive = archive_parent / "captured.json"
    real_create = judge_packets.trusted_fs.create_trusted_directory_fd
    created = []

    def observed_create(parent_fd, parent_path, name, *, create_mode=0o700):
        child_fd = real_create(
            parent_fd, parent_path, name, create_mode=create_mode
        )
        created.append(Path(parent_path) / name)
        return child_fd

    monkeypatch.setattr(
        judge_packets.trusted_fs, "create_trusted_directory_fd", observed_create
    )
    root_fd, child_fd, child = judge_packets._new_quarantine_directory(
        capture, archive
    )
    try:
        assert created == [child]
        assert child.parent == quarantine_root
        assert child.stat().st_mode & 0o777 == 0o700
        assert child.stat().st_uid == os.geteuid()
    finally:
        os.close(child_fd)
        os.close(root_fd)


def test_continuation_archive_is_content_addressed_and_idempotent(tmp_path):
    judge_packets = _load(
        "judge_packets_archive_idempotent", TOOLS / "judge_packets.py"
    )
    continuation = tmp_path / "test-co_verdict_continue_00.json"
    raw = (json.dumps([_checkpoint(1)], indent=1) + "\n").encode()
    continuation.write_bytes(raw)
    first_capture = judge_packets._capture_continuation(continuation)
    first_archive = judge_packets._archive_continuation(first_capture)

    continuation.write_bytes(raw)
    second_capture = judge_packets._capture_continuation(continuation)
    second_archive = judge_packets._archive_continuation(second_capture)

    assert first_archive == second_archive
    assert first_archive.read_bytes() == raw
    assert hashlib.sha256(raw).hexdigest() in first_archive.name
    quarantined = list((
        tmp_path / "Archive" / "continuation-quarantine"
    ).glob("*/test-co_verdict_continue_00.json"))
    assert len(quarantined) == 2
    assert all(path.read_bytes() == raw for path in quarantined)


def test_final_retirement_swap_preserves_newer_valid_bytes_and_fails(
        tmp_path, monkeypatch, capsys):
    case = _prepared_continuation_capture_case(
        tmp_path, monkeypatch, "judge_packets_final_retirement_swap"
    )
    judge_packets = case["module"]
    continuation = case["continuation"]
    real_replace = judge_packets.os.replace
    swapped = False

    def swap_live_then_quarantine(src, dst, *, src_dir_fd=None,
                                  dst_dir_fd=None):
        nonlocal swapped
        if (not swapped and src == continuation.name
                and src_dir_fd != dst_dir_fd):
            os.unlink(src, dir_fd=src_dir_fd)
            replacement = os.open(
                src,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=src_dir_fd,
            )
            try:
                os.write(replacement, case["attacker_raw"])
                os.fsync(replacement)
            finally:
                os.close(replacement)
            swapped = True
        return real_replace(
            src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd
        )

    monkeypatch.setattr(judge_packets.os, "replace", swap_live_then_quarantine)
    assert judge_packets.main(case["args"]) == 2
    error = capsys.readouterr().err
    assert swapped and "newer continuation won final retirement" in error
    assert "continuation-quarantine" in error
    assert not continuation.exists()

    canonical = json.loads(case["draft"].read_bytes())
    assert [row["fid"] for row in canonical] == [1, 2]
    assert canonical[-1]["verdict"] == json.loads(case["captured_raw"])[0]["verdict"]
    assert canonical[-1]["verdict"] != json.loads(case["attacker_raw"])[0]["verdict"]
    assert json.loads(case["checkpoint"].read_text())["completed"] == [1, 2]

    archived = list((tmp_path / "Archive").glob(
        "test-co_verdict_continue_00.captured-*.json"
    ))
    quarantined = list((
        tmp_path / "Archive" / "continuation-quarantine"
    ).glob("*/test-co_verdict_continue_00.json"))
    assert len(archived) == 1 and archived[0].read_bytes() == case["captured_raw"]
    assert len(quarantined) == 1
    assert quarantined[0].read_bytes() == case["attacker_raw"]
    assert str(quarantined[0]) in error


def test_new_live_continuation_after_retirement_is_left_for_next_run(
        tmp_path, monkeypatch):
    case = _prepared_continuation_capture_case(
        tmp_path, monkeypatch, "judge_packets_post_retirement_live"
    )
    judge_packets = case["module"]
    real_verify = judge_packets._capture_quarantined_entry
    created = False

    def create_next_live_then_verify(*args, **kwargs):
        nonlocal created
        if not created:
            case["continuation"].write_bytes(case["attacker_raw"])
            created = True
        return real_verify(*args, **kwargs)

    monkeypatch.setattr(
        judge_packets, "_capture_quarantined_entry", create_next_live_then_verify
    )
    assert judge_packets.main(case["args"]) == 0
    assert created
    assert case["continuation"].read_bytes() == case["attacker_raw"]
    quarantined = list((
        tmp_path / "Archive" / "continuation-quarantine"
    ).glob("*/test-co_verdict_continue_00.json"))
    assert len(quarantined) == 1
    assert quarantined[0].read_bytes() == case["captured_raw"]


# ------------------------------------------------ latest filesystem re-review regressions


def _review_test_models():
    return {
        "primary": merge_drafts.tr.model_config("primary-model", "primary-family"),
        "challenger": merge_drafts.tr.model_config("challenger-model", "challenger-family"),
        "arbiter": merge_drafts.tr.model_config("arbiter-model", "arbiter-family"),
    }


def test_review_sheet_visibly_embeds_complete_frozen_packet_and_publication_facts(
        tmp_path):
    import html

    tmp = _area(tmp_path, drafts=[[_verdict(1, "KEEP")]])
    packets_path = tmp / "test-co_packets.json"
    packets = json.loads(packets_path.read_text())
    packet = packets["1"]
    packet.update({
        "prior": "surveyed",
        "tags_union": {
            "access": "private",
            "description": "Full <descriptive> & tagged facts",
            "name": "Packet label sentinel",
        },
        "members": [{
            "osm": "node/991",
            "tags": {"access": "private", "barrier": "gate"},
        }],
        "mixed_access": True,
        "footprint": "multipolygon-sentinel",
        "area_m2": 4321.25,
        "serves": {
            "trail": "Fallback Trail <sentinel>",
            "edge_m": 876.5,
            "fallback": True,
            "n_trails_in_range": 7,
        },
        "walk": {
            "walk_m": 654.25,
            "conn": "private-footway",
            "trail": "Walk Trail & sentinel",
        },
        "trailhead_nodes_120m": [{
            "osm": "node/992", "tags": {"highway": "trailhead"},
        }],
        "footways_60m": 9,
        "building_overlap": True,
        "context": {
            "category": "PRIVATE_FACILITY",
            "evidence": "Building <overlap> & private access",
            "facility": "Restricted Campus",
            "facility_edge_m": 12.5,
        },
    })
    packets_path.write_text(json.dumps(packets))
    draft_path = tmp / "test-co_verdict_draft_00.json"
    draft_rows = json.loads(draft_path.read_text())
    draft_rows[0]["prior"] = packet["prior"]
    draft_path.write_text(json.dumps(draft_rows))
    state = merge_drafts.judge_packets.inspect_draft([packet], draft_path)
    decision_input, missing = merge_drafts.judge_packets.decision_fingerprint([packet])
    assert state["status"] == "complete" and not missing
    checkpoint = merge_drafts.judge_packets.manifest_value(
        "test-co", 0, [packet], decision_input, state
    )
    (tmp / "test-co_checkpoint_00.json").write_text(json.dumps(checkpoint))

    dossier_path = tmp / "test-co_dossier.json"
    dossier = json.loads(dossier_path.read_text())
    facility = dossier["facilities"][0]
    facility.update({
        "prior": packet["prior"],
        "name": "Exact publication <label> & sentinel",
        "tags_union": {
            "name": "Exact publication <label> & sentinel",
            "operator": "Publication operator & sentinel",
        },
    })
    dossier_path.write_text(json.dumps(dossier))
    generation = dossier_output.commit_generation(
        tmp, "test-co", packet["source_generation"]["manifest_sha256"]
    )
    packet["source_generation"] = dossier_output.portable_source_generation(
        generation, "test-co"
    )
    packets["1"] = packet
    packets_path.write_text(json.dumps(packets))
    state = merge_drafts.judge_packets.inspect_draft([packet], draft_path)
    decision_input, missing = merge_drafts.judge_packets.decision_fingerprint([packet])
    assert state["status"] == "complete" and not missing
    checkpoint = merge_drafts.judge_packets.manifest_value(
        "test-co", 0, [packet], decision_input, state
    )
    (tmp / "test-co_checkpoint_00.json").write_text(json.dumps(checkpoint))

    run = merge_drafts.resolver.prepare(
        "test-co", tmp, _review_test_models(), tmp_path / "complete-facts-run"
    )
    prepared = merge_drafts.resolver.load_prepare_document(run / "prepare.json")
    frozen_facility = copy.deepcopy(
        prepared["source"]["publication"]["facilities"]["1"]
    )
    packets["1"]["tags_union"] = {"name": "LIVE MUTATION MUST NOT APPEAR"}
    packets_path.write_text(json.dumps(packets))
    dossier["facilities"][0]["name"] = "LIVE DOSSIER MUTATION"
    dossier_path.write_text(json.dumps(dossier))

    review_sheet.PADJ_TMP = str(tmp)
    assert review_sheet.main([
        "test-co", "--authority-run", str(run), "--sample", "1",
    ]) == 0
    paths = review_sheet.review.artifact_paths(tmp, "test-co", run.name)
    page = paths["sheet"].read_text()
    exact_facts = {
        "packet": {key: value for key, value in packet.items() if key != "tiles"},
        "publication_facility": frozen_facility,
    }
    exact_facts["packet"]["tags_union"] = {
        "access": "private",
        "description": "Full <descriptive> & tagged facts",
        "name": "Packet label sentinel",
    }
    canonical_escaped = html.escape(
        merge_drafts.tr.canonical_json(exact_facts).decode("utf-8"), quote=True
    )
    assert canonical_escaped in page
    assert "LIVE MUTATION MUST NOT APPEAR" not in page
    assert "LIVE DOSSIER MUTATION" not in page


def test_review_pair_crash_leaves_sheet_unauthoritative_and_exact_retry_keeps_inode(
        tmp_path, monkeypatch):
    tmp, run, paths = _unrendered_review_case(
        tmp_path, "review-sheet-installed-crash"
    )
    real_write = review_sheet.resolver._write_review_artifact

    def crash_after_sheet(path, *args, **kwargs):
        result = real_write(path, *args, **kwargs)
        if Path(path) == paths["sheet"]:
            raise OSError("simulated crash after exact sheet install")
        return result

    monkeypatch.setattr(
        review_sheet.resolver, "_write_review_artifact", crash_after_sheet
    )
    with pytest.raises(SystemExit, match="simulated crash after exact sheet install"):
        review_sheet.main([
            "test-co", "--authority-run", str(run), "--sample", "0",
        ])
    assert paths["sheet"].is_file() and not paths["receipt"].exists()
    sheet_inode = paths["sheet"].stat().st_ino
    draft = tmp / "test-co_verdict_draft_00.json"
    checkpoint = tmp / "test-co_checkpoint_00.json"
    canonical_before = (draft.read_bytes(), checkpoint.read_bytes())
    authority_args = [
        str(tmp_path / "store.json"), "test-co", "--confirm", "1=KEEP",
        "--judged", "2026-09-29", "--note", "sheet alone is insufficient",
        "--reviewer", "trekdex-project-owner", "--authority-run", str(run),
        "--review-receipt", str(paths["receipt"]),
    ]
    assert merge_drafts.main(authority_args) == 1
    assert (draft.read_bytes(), checkpoint.read_bytes()) == canonical_before
    assert not (tmp / merge_drafts.AUTHORITY_RECEIPT_DIR).exists()

    monkeypatch.setattr(
        review_sheet.resolver, "_write_review_artifact", real_write
    )
    assert review_sheet.main([
        "test-co", "--authority-run", str(run), "--sample", "0",
    ]) == 0
    assert paths["sheet"].stat().st_ino == sheet_inode
    assert paths["receipt"].is_file()


def _without_judged(arguments):
    omitted = list(arguments)
    index = omitted.index("--judged")
    del omitted[index:index + 2]
    return omitted


@pytest.mark.parametrize("authority_kind", ["confirm", "decide"])
def test_mutating_human_authority_requires_explicit_judged_before_fresh_mutation(
        tmp_path, authority_kind):
    root = tmp_path / authority_kind
    root.mkdir()
    factory = (
        _authority_confirmation_case
        if authority_kind == "confirm" else _authority_decision_case
    )
    tmp, _store, _packet_value, _original, _run, arguments = factory(root)
    draft = tmp / "test-co_verdict_draft_00.json"
    checkpoint = tmp / "test-co_checkpoint_00.json"
    before = (draft.read_bytes(), checkpoint.read_bytes())
    with pytest.raises(SystemExit, match="--judged YYYY-MM-DD is required"):
        merge_drafts.main(_without_judged(arguments))
    assert (draft.read_bytes(), checkpoint.read_bytes()) == before
    assert not merge_drafts.tr.authority_journal_path(tmp, "test-co").exists()


def test_human_authority_recovery_rejects_omitted_judged_without_wall_clock_fallback(
        tmp_path, monkeypatch):
    tmp, _store, _packet_value, _original, _run, arguments = (
        _authority_confirmation_case(tmp_path, "authority-date-omission")
    )
    _crash_authority_main(monkeypatch, arguments, "receipt")
    journal, journal_bytes, document = _authority_journal_document(tmp)
    canonical_before = _authority_canonical_snapshot(document)
    artifacts_before = _authority_transaction_snapshot(tmp)
    with pytest.raises(SystemExit, match="--judged YYYY-MM-DD is required"):
        merge_drafts.main(_without_judged(arguments))
    assert journal.read_bytes() == journal_bytes
    assert _authority_canonical_snapshot(document) == canonical_before
    assert _authority_transaction_snapshot(tmp) == artifacts_before


def test_publication_requires_explicit_judged_for_fresh_and_live_journal_states(
        tmp_path, monkeypatch):
    store, proof, journal, arguments = _real_publication_case(
        tmp_path, monkeypatch, "publication-date-omission"
    )
    omitted = _without_judged(arguments)
    with pytest.raises(SystemExit, match="--judged YYYY-MM-DD is required"):
        merge_drafts.main(omitted)
    assert store.read_bytes() == b"{}" and not journal.exists()

    real_replace = merge_drafts.resolver._replace_verified_nofollow

    def crash_after_proof(source, target, expected_sha256, *,
                          retire_trusted_source=False):
        result = real_replace(
            source, target, expected_sha256,
            retire_trusted_source=retire_trusted_source,
        )
        if Path(target) == proof:
            raise RuntimeError("simulated crash after publication proof")
        return result

    monkeypatch.setattr(
        merge_drafts.resolver, "_replace_verified_nofollow", crash_after_proof
    )
    with pytest.raises(RuntimeError, match="simulated crash"):
        merge_drafts.main(arguments)
    monkeypatch.setattr(
        merge_drafts.resolver, "_replace_verified_nofollow", real_replace
    )
    before = (store.exists(), proof.read_bytes(), journal.read_bytes())
    with pytest.raises(SystemExit, match="--judged YYYY-MM-DD is required"):
        merge_drafts.main(omitted)
    assert (store.exists(), proof.read_bytes(), journal.read_bytes()) == before


def test_noncanonical_prepare_bytes_fail_before_review_and_human_authority(
        tmp_path):
    tmp, run, paths = _unrendered_review_case(
        tmp_path, "noncanonical-review-prepare"
    )
    prepare_path = run / "prepare.json"
    canonical = prepare_path.read_bytes()
    for variant in (
        b" " + canonical,
        b'{"version":2,' + canonical[1:],
    ):
        prepare_path.write_bytes(variant)
        with pytest.raises(SystemExit, match="strict JSON|noncanonical"):
            review_sheet.main([
                "test-co", "--authority-run", str(run), "--sample", "0",
            ])
        assert not paths["sheet"].exists() and not paths["receipt"].exists()
        prepare_path.write_bytes(canonical)

    assert review_sheet.main([
        "test-co", "--authority-run", str(run), "--sample", "0",
    ]) == 0
    prepare_path.write_bytes(b"\n" + canonical)
    draft = tmp / "test-co_verdict_draft_00.json"
    checkpoint = tmp / "test-co_checkpoint_00.json"
    before = (draft.read_bytes(), checkpoint.read_bytes())
    assert merge_drafts.main([
        str(tmp_path / "store.json"), "test-co", "--confirm", "1=KEEP",
        "--judged", "2026-09-29", "--note", "canonical prepare required",
        "--reviewer", "trekdex-project-owner", "--authority-run", str(run),
        "--review-receipt", str(paths["receipt"]),
    ]) == 1
    assert (draft.read_bytes(), checkpoint.read_bytes()) == before


def test_review_receipt_validator_is_total_for_heterogeneous_multi_item_coordinates(
        tmp_path):
    tmp = _area(tmp_path, drafts=[[
        _verdict(1, "KEEP"), _verdict(2, "KEEP"), _verdict(3, "DROP"),
    ]])
    run = merge_drafts.resolver.prepare(
        "test-co", tmp, _review_test_models(), tmp_path / "receipt-total-run"
    )
    review_sheet.PADJ_TMP = str(tmp)
    assert review_sheet.main([
        "test-co", "--authority-run", str(run), "--sample", "2",
    ]) == 0
    path = _review_receipt(run)
    valid = json.loads(path.read_text())
    mutations = []
    missing = copy.deepcopy(valid)
    missing["items"][0].pop("fid")
    mutations.append(missing)
    heterogeneous = copy.deepcopy(valid)
    heterogeneous["items"][0]["fid"] = []
    heterogeneous["items"][1]["fid"] = True
    heterogeneous["items"][2]["row_index"] = 10 ** 1000
    mutations.append(heterogeneous)
    vectors = copy.deepcopy(valid)
    vectors["sample"]["eligible_fids"] = [1, [], True, 10 ** 1000]
    mutations.append(vectors)

    for malformed in mutations:
        errors = review_sheet.review.validate_review_receipt(malformed)
        assert errors
        assert isinstance(errors, list)


def test_calibration_sample_uses_canonical_review_receipt_and_binds_hash(
        tmp_path, capsys):
    drafts = [[
        _verdict(fid, "KEEP", "certain" if fid % 2 else "strong")
        for fid in range(1, 11)
    ], [_verdict(20, "DROP", "strong")]]
    tmp = _area(tmp_path, drafts=drafts)
    run = merge_drafts.resolver.prepare(
        "test-co", tmp, _review_test_models(), tmp_path / "calibration-review-run"
    )
    review_sheet.PADJ_TMP = str(tmp)
    assert review_sheet.main([
        "test-co", "--authority-run", str(run), "--sample", "4",
    ]) == 0
    receipt_path = _review_receipt(run)
    receipt = json.loads(receipt_path.read_text())
    selected = receipt["sample"]["selected_fids"]
    assert len(selected) == 4
    lines = _apply_legacy_overrides(
        tmp, "test-co", {selected[0]: "DROP"}, None, "2026-09-13"
    )
    assert not any("!!" in line for line in lines)
    calibration.LEDGER = str(tmp_path / "calibration.json")
    assert calibration.main([
        "add", "test-co", "--reviewed", "KEEP=sample",
        "--reviewed", "DROP=each", "--review-receipt", str(receipt_path),
        "--date", "2026-09-13",
    ]) == 0
    record = json.loads(Path(calibration.LEDGER).read_text())["areas"][0]
    assert record["sample_fids"] == selected
    assert record["sample_review_receipt_sha256"] == receipt["receipt_sha256"]
    assert "sample_manifest_sha256" not in record
    assert record["flips"][0]["sampled"] is True
    assert sum(record["sample"]["KEEP"].values()) == 4
    output = capsys.readouterr().out
    keep_row = next(line for line in output.splitlines()
                    if line.startswith("KEEP any"))
    assert " 10        4     1   75.0%" in keep_row


@pytest.mark.parametrize("embedded_location", ["review", "human-authority"])
def test_publication_proof_rejects_noncanonical_embedded_prepare_bytes(
        tmp_path, monkeypatch, embedded_location):
    stored, registry = _published_human_review_case(
        tmp_path, monkeypatch, f"noncanonical-embedded-{embedded_location}"
    )
    proof_sha = stored["publication_attestation"]["publication_proof_sha256"]
    proof = copy.deepcopy(registry["proofs"][proof_sha])
    if embedded_location == "review":
        review_sha = stored["human_confirmation"]["review_receipt_sha256"]
        bundle = proof["human_reviews"][review_sha]
        raw = base64.b64decode(bundle["source_prepare_b64"])
        bundle["source_prepare_b64"] = base64.b64encode(b" " + raw).decode()
    else:
        human = next(iter(proof["human_authority"].values()))
        raw = base64.b64decode(human["source_prepare_b64"])
        human["source_prepare_b64"] = base64.b64encode(b" " + raw).decode()
    row, forged_registry = _rehash_proof_and_attestation(
        stored, registry, proof
    )
    errors = merge_drafts.resolver.replay_trust._validate_publication_proof(
        row, forged_registry
    )
    assert errors
    if embedded_location == "review":
        assert any("noncanonical" in error for error in errors)
    else:
        assert any("human source prepare mismatch" in error for error in errors)


# -------------------------------- publication trust-root candidate boundary


def test_successful_publication_installs_only_review_candidate_under_root_lock(
        tmp_path, monkeypatch):
    store, _proof, journal, arguments = _real_publication_case(
        tmp_path, monkeypatch, "root-candidate-success"
    )
    configuration = (
        merge_drafts.resolver.replay_trust.publication_configuration_for_store(
            store.name
        )
    )
    root_path = Path(configuration.PUBLICATION_TRUST_ROOT_PATH)
    root_before = root_path.read_bytes()
    pin_before = configuration.PUBLICATION_TRUST_ROOT_SHA256
    root_lock = merge_drafts.tr.resource_lock_path(tmp_path, root_path)
    observed = []
    real_commit = merge_drafts._commit_publication

    def commit_under_root_lock(*args, **kwargs):
        observed.append(_other_process_can_take_shared_lock(root_lock))
        return real_commit(*args, **kwargs)

    monkeypatch.setattr(
        merge_drafts, "_commit_publication", commit_under_root_lock
    )
    assert merge_drafts.main(arguments) == 0
    monkeypatch.setattr(merge_drafts, "_commit_publication", real_commit)
    assert observed == [False]
    assert not journal.exists()
    assert root_path.read_bytes() == root_before
    assert configuration.PUBLICATION_TRUST_ROOT_SHA256 == pin_before

    archives = list((journal.parent / "Archive").glob(
        f"{store.name}.*.json"
    ))
    assert len(archives) == 1
    archived = json.loads(archives[0].read_bytes())
    candidate_path = Path(archived["paths"]["candidate"])
    candidate_bytes = candidate_path.read_bytes()
    assert hashlib.sha256(candidate_bytes).hexdigest() == (
        archived["publication_trust_root"]["candidate_root_sha256"]
    )
    specs = merge_drafts.resolver.replay_trust._configured_store_specs(
        configuration
    )
    candidate = verdict_source.parse_publication_trust_root(
        candidate_bytes, specs, hashlib.sha256(candidate_bytes).hexdigest(),
        Path(configuration.LEGACY_ROW_BASELINE_PATH).name,
    )
    assert candidate.generation == 2
    assert candidate.parent_root_sha256 == hashlib.sha256(root_before).hexdigest()
    rooted_target = candidate.stores_by_name[store.name]
    assert rooted_target["store_sha256"] == hashlib.sha256(
        store.read_bytes()
    ).hexdigest()
    assert rooted_target["proof_registry_sha256"] == hashlib.sha256(
        merge_drafts.publication_proof_path(store).read_bytes()
    ).hexdigest()
    assert rooted_target["publication_floor_sha256"] == hashlib.sha256(
        merge_drafts.publication_floor_path(store).read_bytes()
    ).hexdigest()

    candidates_before = {
        path: path.read_bytes()
        for path in candidate_path.parents[1].rglob("*.json")
    }
    assert merge_drafts.main(arguments) == 1
    assert {
        path: path.read_bytes()
        for path in candidate_path.parents[1].rglob("*.json")
    } == candidates_before
    assert root_path.read_bytes() == root_before


def test_authority_only_operation_creates_no_publication_candidate(
        tmp_path, monkeypatch):
    tmp, store, _packet, _original, _run, arguments = (
        _authority_confirmation_case(tmp_path, "authority-no-root-candidate")
    )
    store.write_bytes(b"{}")
    _install_empty_publication_floor(store)
    configuration, root_path, root_before = (
        _install_rooted_publication_configuration(
            tmp_path, [store.name], monkeypatch
        )
    )
    assert merge_drafts.main(arguments) == 0
    assert root_path.read_bytes() == root_before
    assert configuration.PUBLICATION_TRUST_ROOT_SHA256 == hashlib.sha256(
        root_before
    ).hexdigest()
    candidates = (
        tmp_path / ".trekdex-publication-transactions" / "candidates"
    )
    assert not candidates.exists()
    assert merge_drafts.tr.authority_journal_path(
        tmp, "test-co"
    ).exists() is False


def test_candidate_floor_cannot_omit_prior_floor_identity():
    spec = verdict_source.StoreSpec(
        "store.json", verdict_source.OSM_KEY, None, None
    )
    identity = {
        "first_authority_kind": "machine_v2",
        "first_authority_sha256": "a" * 64,
        "first_attestation_sha256": "b" * 64,
        "first_proof_sha256": "c" * 64,
    }
    parent_floor = verdict_source.publication_floor_json_bytes(
        verdict_source.build_publication_floor_document(
            spec.filename, {"way/1": identity}
        )
    )
    omitted_floor = verdict_source.publication_floor_json_bytes(
        verdict_source.build_empty_publication_floor_document(spec.filename)
    )
    with pytest.raises(ValueError, match="not append-only"):
        merge_drafts.resolver.replay_trust.validate_publication_floor_transition(
            spec, parent_floor, omitted_floor
        )


def test_mismatched_existing_candidate_fails_closed_before_recovery_mutation(
        tmp_path, monkeypatch):
    store, proof, journal, arguments = _real_publication_case(
        tmp_path, monkeypatch, "mismatched-root-candidate"
    )
    floor = merge_drafts.publication_floor_path(store)
    real_replace = merge_drafts.resolver._replace_verified_nofollow

    def crash_after_floor(source, target, expected_sha256, *,
                          retire_trusted_source=False):
        result = real_replace(
            source, target, expected_sha256,
            retire_trusted_source=retire_trusted_source,
        )
        if Path(target) == floor:
            raise RuntimeError("simulated crash after publication floor")
        return result

    monkeypatch.setattr(
        merge_drafts.resolver, "_replace_verified_nofollow", crash_after_floor
    )
    with pytest.raises(RuntimeError, match="simulated crash"):
        merge_drafts.main(arguments)
    monkeypatch.setattr(
        merge_drafts.resolver, "_replace_verified_nofollow", real_replace
    )
    journal_document = json.loads(journal.read_bytes())
    candidate = Path(journal_document["paths"]["candidate"])
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_bytes(b"mismatched candidate bytes")
    before = (
        store.read_bytes(), proof.read_bytes(), floor.read_bytes(),
        journal.read_bytes(),
    )
    assert merge_drafts.main(arguments) == 1
    assert (
        store.read_bytes(), proof.read_bytes(), floor.read_bytes(),
        journal.read_bytes(),
    ) == before
    assert candidate.read_bytes() == b"mismatched candidate bytes"


@pytest.mark.parametrize("generation_state", ["missing", "stale", "mixed-member"])
def test_judge_generation_failure_has_zero_generated_outputs(
        tmp_path, generation_state):
    judge_packets = _load(
        f"judge_generation_zero_{generation_state}", TOOLS / "judge_packets.py"
    )
    _production_packets(judge_packets, tmp_path, [1])
    manifest_path = tmp_path / "test-co_generation.json"
    if generation_state == "missing":
        manifest_path.rename(tmp_path / "hidden-test-co_generation.json")
    elif generation_state == "stale":
        context_path = tmp_path / "test-co_context.json"
        context = json.loads(context_path.read_text())
        context["1"]["ignored_generation_marker"] = "stale"
        context_path.write_text(json.dumps(context))
    else:
        other = tmp_path / "other-generation"
        _production_packets(judge_packets, other, [1])
        other_context = other / "test-co_context.json"
        context = json.loads(other_context.read_text())
        context["1"]["ignored_generation_marker"] = "other"
        other_context.write_text(json.dumps(context))
        parent = hashlib.sha256(
            (other / "test-co_generation.json").read_bytes()
        ).hexdigest()
        dossier_output.commit_generation(other, "test-co", parent)
        (tmp_path / "test-co_context.json").write_bytes(
            other_context.read_bytes()
        )

    generated = lambda: sorted(
        path.name for path in tmp_path.iterdir()
        if path.name.startswith("test-co_") and any(token in path.name for token in (
            "_packets.json", "_checkpoint_", "_verdict_draft_", "_chunk_",
            "_prompt_", "_pub.txt",
        ))
    )
    assert generated() == []
    assert judge_packets.main([
        "test-co", "--tmp", str(tmp_path), "--status-json",
    ]) == 2
    assert generated() == []


def test_judge_binds_identical_generation_and_rotates_fingerprint_checkpoint(
        tmp_path):
    judge_packets = _load("judge_generation_fingerprint", TOOLS / "judge_packets.py")
    packets = _production_packets(judge_packets, tmp_path, [1, 2])
    bindings = [packet["source_generation"] for packet in packets.values()]
    assert bindings and all(binding == bindings[0] for binding in bindings)
    assert bindings[0]["manifest_path"] == "test-co_generation.json"
    first, missing = judge_packets.decision_fingerprint(list(packets.values()))
    assert first and not missing
    state = judge_packets.inspect_draft(
        list(packets.values()), tmp_path / "missing-draft.json"
    )
    checkpoint = judge_packets.manifest_value(
        "test-co", 0, list(packets.values()), first, state
    )
    assert checkpoint["source_generation"] == bindings[0]

    context_path = tmp_path / "test-co_context.json"
    context = json.loads(context_path.read_text())
    for value in context.values():
        value["ignored_generation_marker"] = "next"
    context_path.write_text(json.dumps(context))
    parent = bindings[0]["manifest_sha256"]
    capture = dossier_output.commit_generation(tmp_path, "test-co", parent)
    changed = judge_packets.build_packets(
        "test-co", str(tmp_path), None, capture
    )
    for fid in packets:
        before = copy.deepcopy(packets[fid])
        after = copy.deepcopy(changed[fid])
        before.pop("source_generation")
        after.pop("source_generation")
        assert before == after
    second, missing = judge_packets.decision_fingerprint(list(changed.values()))
    assert second and not missing and second != first
    changed_checkpoint = judge_packets.manifest_value(
        "test-co", 0, list(changed.values()), second, state
    )
    assert changed_checkpoint["source_generation"] != checkpoint["source_generation"]


def test_judge_holds_inventory_then_area_locks_through_every_write(
        tmp_path, monkeypatch):
    judge_packets = _load("judge_generation_lock_lifetime", TOOLS / "judge_packets.py")
    _production_packets(judge_packets, tmp_path, [1])
    inventory_held = False
    area_held = False
    events = []
    area_path = judge_packets.tr.area_lock_path(tmp_path, "test-co").resolve()
    real_locked = judge_packets.trusted_fs.locked_resources
    real_open = judge_packets.trusted_fs.open_lock_file
    real_atomic_write = judge_packets._atomic_write
    real_atomic_json = judge_packets._atomic_json

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

    class ObservedAreaHandle:
        def __init__(self, handle):
            self.handle = handle

        def fileno(self):
            return self.handle.fileno()

        def __enter__(self):
            nonlocal area_held
            assert inventory_held
            events.append("area")
            area_held = True
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            nonlocal area_held
            area_held = False
            self.handle.close()

    def observed_open(path):
        canonical = Path(path).resolve()
        handle = real_open(path)
        return ObservedAreaHandle(handle) if canonical == area_path else handle

    def observed_write(path, data):
        assert inventory_held and area_held
        return real_atomic_write(path, data)

    def observed_json(path, value):
        assert inventory_held and area_held
        return real_atomic_json(path, value)

    monkeypatch.setattr(judge_packets.trusted_fs, "locked_resources", observed_locked)
    monkeypatch.setattr(judge_packets.trusted_fs, "open_lock_file", observed_open)
    monkeypatch.setattr(judge_packets, "_atomic_write", observed_write)
    monkeypatch.setattr(judge_packets, "_atomic_json", observed_json)
    assert judge_packets.main([
        "test-co", "--tmp", str(tmp_path), "--status-json",
    ]) == 0
    assert events[:2] == ["inventory", "area"]
    assert not inventory_held and not area_held


def test_human_authority_generation_drift_has_zero_mutation(
        tmp_path, capsys):
    tmp, _, _, _original, _authority_run, args = _authority_confirmation_case(
        tmp_path, "authority-generation-drift"
    )
    draft = tmp / "test-co_verdict_draft_00.json"
    checkpoint = tmp / "test-co_checkpoint_00.json"
    before = (draft.read_bytes(), checkpoint.read_bytes())
    context_path = tmp / "test-co_context.json"
    context = json.loads(context_path.read_text())
    context["2"]["ignored_generation_marker"] = "next"
    context_path.write_text(json.dumps(context))
    parent = hashlib.sha256((tmp / "test-co_generation.json").read_bytes()).hexdigest()
    dossier_output.commit_generation(tmp, "test-co", parent)

    assert merge_drafts.main(args) == 1
    assert "source generation changed after prepare" in capsys.readouterr().out
    assert (draft.read_bytes(), checkpoint.read_bytes()) == before
    assert not (tmp / merge_drafts.AUTHORITY_RECEIPT_DIR).exists()
    assert not merge_drafts.tr.authority_journal_path(tmp, "test-co").exists()


@pytest.mark.parametrize("tamper", ["prepare", "packet", "checkpoint"])
def test_publication_proof_rejects_generation_tamper_after_outer_rehash(
        tmp_path, monkeypatch, tamper):
    store, proof_path, _journal, args = _real_publication_case(
        tmp_path, monkeypatch, f"generation-proof-{tamper}"
    )
    assert merge_drafts.main(args) == 0
    stored = json.loads(store.read_text())["way/1"]
    registry = json.loads(proof_path.read_text())
    proof_sha = stored["publication_attestation"]["publication_proof_sha256"]
    proof = copy.deepcopy(registry["proofs"][proof_sha])
    if tamper == "prepare":
        proof["prepare"]["source"]["source_generation"][
            "manifest_sha256"
        ] = "f" * 64
    elif tamper == "packet":
        proof["packet_bindings"]["1"]["packet"]["source_generation"][
            "artifact_sha256"
        ]["walk"] = "f" * 64
    else:
        checkpoint = json.loads(base64.b64decode(
            proof["terminal_checkpoints"]["0"]
        ))
        checkpoint["source_generation"]["artifact_sha256"]["context"] = "f" * 64
        proof["terminal_checkpoints"]["0"] = base64.b64encode(
            json.dumps(checkpoint).encode()
        ).decode()
    row, forged_registry = _rehash_proof_and_attestation(
        stored, registry, proof
    )
    errors = merge_drafts.resolver.replay_trust._validate_publication_proof(
        row, forged_registry
    )
    assert errors
    assert any("source_generation" in error or "run_id" in error
               for error in errors)


def test_publication_proof_replays_after_original_work_generation_is_hidden(
        tmp_path, monkeypatch):
    store, proof_path, _journal, args = _real_publication_case(
        tmp_path, monkeypatch, "hidden-original-generation"
    )
    assert merge_drafts.main(args) == 0
    stored = json.loads(store.read_text())["way/1"]
    registry = json.loads(proof_path.read_text())
    proof_sha = stored["publication_attestation"]["publication_proof_sha256"]
    prepare = registry["proofs"][proof_sha]["prepare"]
    work = Path(prepare["tmp"])
    hidden = work / "hidden-original-generation"
    hidden.mkdir()
    for suffix in ("generation", "dossier", "serves2", "context", "walk"):
        source = work / f"test-co_{suffix}.json"
        source.rename(hidden / source.name)

    assert merge_drafts.resolver.replay_trust._validate_publication_proof(
        stored, registry
    ) == []


def test_human_authority_validation_uses_captured_dossier_not_path_reopen(
        tmp_path, monkeypatch):
    import builtins

    tmp, _store, _packet, _original, _authority_run, args = (
        _authority_confirmation_case(tmp_path, "authority-captured-dossier")
    )
    dossier_path = (tmp / "test-co_dossier.json").resolve()
    real_open = builtins.open

    def reject_dossier_reopen(file, *open_args, **open_kwargs):
        try:
            candidate = Path(file).resolve()
        except (TypeError, ValueError):
            candidate = None
        if candidate == dossier_path:
            raise AssertionError("authority validation reopened the dossier path")
        return real_open(file, *open_args, **open_kwargs)

    monkeypatch.setattr(builtins, "open", reject_dossier_reopen)
    assert merge_drafts.main(args) == 0
    assert _drafts(tmp)[2]["human_confirmation"]["version"] == (
        merge_drafts.CONFIRMATION_VERSION
    )
