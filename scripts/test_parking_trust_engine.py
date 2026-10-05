"""Regression tests for the read-only parking trust replay."""
from __future__ import annotations

import copy
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
TOOLS = HERE / "parking-adjud" / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import dossier_output
import replay_trust
import trust_engine
import trust_resolution as tr


def _deferred_oversized_registry() -> bool:
    """Return whether parent-owned production rollback is still pending."""
    path = replay_trust.DATA / "co_verdicts_osm_publication_proofs.json"
    try:
        value = os.lstat(path)
    except FileNotFoundError:
        return False
    return stat.S_ISREG(value.st_mode) and (
        value.st_size > replay_trust.publication_objects.REGISTRY_MAX_BYTES
    )


@pytest.fixture(autouse=True)
def _isolate_repository_resource_locks(tmp_path, monkeypatch):
    real_resource_lock_path = tr.resource_lock_path
    repository_data = replay_trust.DATA.resolve()
    lock_root = tmp_path / ".repository-resource-locks"

    def isolated_resource_lock_path(tmp, resource):
        resource_path = Path(resource).expanduser().resolve()
        try:
            resource_path.relative_to(repository_data)
        except ValueError:
            return real_resource_lock_path(tmp, resource)
        canonical = real_resource_lock_path(tmp, resource)
        return lock_root / canonical.name

    monkeypatch.setattr(tr, "resource_lock_path", isolated_resource_lock_path)


@pytest.fixture
def report_root(tmp_path, monkeypatch):
    root = tmp_path / "reports"
    monkeypatch.setattr(replay_trust, "REPORTS", root)
    return root


def _other_process_can_take_lock(path: Path, *, shared: bool = False) -> bool:
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
    if result.returncode not in (0, 1):
        raise AssertionError(result.stderr.decode("utf-8", errors="replace"))
    return result.returncode == 0


def _work_source_generation(work: Path, slug: str, fid: int) -> dict:
    for suffix, value in (
        ("dossier", {
            "slug": slug,
            "facilities": [{
                "fid": fid, "lat": 40.0 + fid / 1000, "lon": -105.0,
                "osm": [f"way/{fid}"], "prior": "surveyed",
            }],
        }),
        ("serves2", {str(fid): {"served": True}}),
        ("context", {str(fid): {"category": "NEUTRAL"}}),
        ("walk", {str(fid): {
            "walk_m": 100, "conn": "path", "trail": "Trail",
        }}),
    ):
        (work / f"{slug}_{suffix}.json").write_text(json.dumps(value))
    capture = dossier_output.bootstrap_generation(work, slug)
    return dossier_output.portable_source_generation(capture, slug)


def _write_work_replay_case(root: Path, slug: str = "new-area-co") -> tuple[Path, Path]:
    work = root / "work-input"
    work.mkdir()
    row = _row(71, area=slug, confidence="strong")
    packet = {
        "fid": 71, "area": slug,
        "source_generation": _work_source_generation(work, slug, 71),
        "osm": ["way/71"], "prior": "surveyed",
    }
    (work / f"{slug}_packets.json").write_text(json.dumps({"71": packet}))
    (work / f"{slug}_verdict_draft_00.json").write_text(json.dumps([row]))
    inputs = root / "replay-inputs"
    inputs.mkdir()
    ledger = inputs / "calibration.json"
    ledger.write_text(json.dumps({"areas": []}))
    return work, ledger


def _row(fid: int, verdict: str = "KEEP", confidence: str = "certain",
         area: str = "test-co", prior: str = "surveyed") -> dict:
    calls = {"exists": "yes", "public": "yes", "serves": "yes"}
    hint = None
    if verdict == "DROP":
        calls["serves"] = "no"
    elif verdict == "REVIEW":
        calls["serves"] = "unclear"
        hint = "fetch an independent frame"
    evidence = {
        "exists": "Z2: marked parking surface and cars",
        "public": "No access restriction or gate",
        "serves": "Trail edge 80 m; walk 100 m" if verdict != "DROP"
        else "Unrelated facility; trail walk 2200 m",
    }
    return {
        "fid": fid,
        "area": area,
        "osm": [f"way/{fid}"],
        "verdict": verdict,
        "prior": prior,
        "exists": {"call": calls["exists"], "evidence": evidence["exists"]},
        "public": {"call": calls["public"], "evidence": evidence["public"]},
        "serves": {"call": calls["serves"], "evidence": evidence["serves"]},
        "frames_used": ["z1", "z2", "z3"],
        "tags_cited": {"surface": "asphalt"},
        "confidence": confidence,
        "resolve_hint": hint,
    }


def _item(fid: int, row: dict | None = None, src: str = "judge-fanout") -> dict:
    row = row or _row(fid)
    key = row["osm"][0]
    return {
        "key": key,
        "source_key": key,
        "store": "test-store.json",
        "area": row.get("area"),
        "row": row,
        "published": {
            "verdict": row.get("verdict"),
            "lat": 40.0 + fid / 100000,
            "lon": -105.0,
            "area": row.get("area"),
            "src": src,
        },
    }


def _with_primary_provenance(row: dict, family: str = "primary-family",
                             model_id: str = "primary-model",
                             prompt_sha256: str = "b" * 64) -> dict:
    row["judge_provenance"] = {
        "model_id": model_id,
        "model_family": family,
        "decision_sha256": trust_engine.decision_sha256(row),
        "packet_sha256": "a" * 64,
        "prompt_sha256": prompt_sha256,
        "evidence_sha256": "c" * 64,
    }
    return row


def _blind_review(row: dict, reviewer_family: str = "reference-family-a",
                  reviewer_id: str = "reviewer-a", reference_verdict: str | None = None) -> dict:
    original = trust_engine.original_judge_projection(row)[0] or row
    reference = copy.deepcopy(original)
    reference.pop("judge_provenance", None)
    reference["verdict"] = reference_verdict or original["verdict"]
    return {
        "fid": original["fid"],
        "reference_verdict": reference["verdict"],
        "reference_decision": reference,
        "blind": True,
        "reviewer_id": reviewer_id,
        "reviewer_family": reviewer_family,
        "reviewer_kind": "external-reference",
        "primary_decision_sha256": trust_engine.decision_sha256(original),
        "reviewer_decision_sha256": trust_engine.decision_sha256(reference),
        "packet_sha256": original["judge_provenance"]["packet_sha256"],
        "prompt_sha256": "d" * 64,
        "evidence_sha256": "e" * 64,
    }


def _report(items: list[dict], ledger: dict | None = None, groundtruth: dict | None = None):
    sidecar = {"version": 1, "lots": {item["key"]: item["published"] for item in items}}
    corpus = {
        "corpus_sha256": "test",
        "sidecar_sha256": "test",
        "source_rows": len(items),
        "unique_clusters": len(items),
        "folded_duplicates": 0,
    }
    return trust_engine.build_report(
        items, ledger or {"areas": []}, groundtruth or {}, sidecar, corpus,
        include_items=True,
    )


def test_shadow_routes_authority_refresh_challenge_and_arbiter_without_user_work():
    authority = _item(1, src="user-groundtruth")
    invalid_row = _row(2)
    invalid_row.pop("area")
    invalid = _item(2, invalid_row)
    invalid["area"] = "test-co"
    challenge = _item(3, _row(3, confidence="strong"))
    leaning = _item(4, _row(4, confidence="leaning"))
    review = _item(5, _row(5, verdict="REVIEW", confidence="leaning"))

    report = _report([authority, invalid, challenge, leaning, review])
    routes = {row["key"]: row["route"] for row in report["items"]}
    assert routes["way/1"] == trust_engine.ROUTE_AUTHORITY
    assert routes["way/2"] == trust_engine.ROUTE_REFRESH
    assert routes["way/3"] == trust_engine.ROUTE_CHALLENGE
    assert routes["way/4"] == trust_engine.ROUTE_ARBITER
    assert routes["way/5"] == trust_engine.ROUTE_ARBITER
    assert report["routing"]["direct_user_work_created"] == 0
    assert report["routing"]["counts"][trust_engine.ROUTE_HUMAN] == 0


def test_strict_candidates_require_specific_evidence_and_never_use_confidence_alone():
    keep = trust_engine.analyse_item(_item(1), {})
    assert keep["candidate"] == {"strict": True, "balanced": True}

    drop_row = _row(2, verdict="DROP", confidence="strong", prior="bare")
    drop_row["exists"] = {"call": "no", "evidence": "Z3: mapped footprint is lawn"}
    drop_row["public"] = {"call": "n/a", "evidence": "Not applicable because no lot exists"}
    drop_row["serves"] = {"call": "n/a", "evidence": "Not applicable because no lot exists"}
    drop = trust_engine.analyse_item(_item(2, drop_row), {})
    assert drop["candidate"]["strict"] is True

    permit = _row(20, verdict="DROP", confidence="strong")
    permit["public"] = {"call": "no", "evidence": "permit parking"}
    permit["serves"] = {"call": "yes", "evidence": "Trail walk 100 m"}
    permit["tags_cited"]["access"] = "permit"
    assessed = trust_engine.analyse_item(_item(20, permit), {})
    assert assessed["candidate"]["strict"] is False
    assert assessed["candidate"]["balanced"] is True

    soft = _row(3, confidence="strong")
    soft["serves"]["evidence"] = "Fallback candidate; trail walk 100 m"
    assessed = trust_engine.analyse_item(_item(3, soft), {})
    assert assessed["candidate"] == {"strict": False, "balanced": False}
    assert "fallback" in assessed["risk_signals"]

    ordinary = _row(30, confidence="strong")
    ordinary["serves"]["evidence"] = "Trail edge 80 m non-fallback; walk 100 m; fallback=false"
    assessed = trust_engine.analyse_item(_item(30, ordinary), {})
    assert assessed["fallback_signal"] is False
    assert "fallback" not in assessed["risk_signals"]

    incomplete = _row(4)
    incomplete["frames_used"] = ["z2", "z3"]
    assessed = trust_engine.analyse_item(_item(4, incomplete), {})
    assert assessed["candidate"]["strict"] is False
    assert "incomplete-ladder" in assessed["risk_signals"]


def test_historical_override_v3_remains_receipt_bound_human_authority(
        tmp_path):
    fid = 703
    original = _row(fid, verdict="REVIEW", confidence="leaning")
    row = _row(fid, verdict="DROP", confidence="strong")
    tiles = {}
    for zoom in ("z1", "z2", "z3"):
        path = tmp_path / f"{fid}-{zoom}.png"
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + zoom.encode("ascii"))
        tiles[zoom] = str(path)
    packet = {
        "fid": fid,
        "area": row["area"],
        "osm": row["osm"],
        "prior": row["prior"],
        "tiles": tiles,
    }
    receipt_sha = "b" * 64
    original_decision = tr.decision_projection(original)
    effective_decision = tr.decision_projection(row)
    row["override"] = {
        "version": trust_engine.PATH_BOUND_OVERRIDE_VERSION,
        "from_decision": original_decision,
        "from_decision_sha256": tr.sha256_json(original_decision),
        "from_evidence_sha256": tr.evidence_sha256(original_decision),
        "to_decision_sha256": tr.sha256_json(effective_decision),
        "to_evidence_sha256": tr.evidence_sha256(effective_decision),
        "by": "human",
        "reviewer": "historical-reviewer",
        "date": "2026-09-29",
        "note": "historical path-bound review resolved the frozen decision",
        "packet_sha256": tr.packet_sha256(packet),
        "source_run_id": "a" * 64,
        "authority_receipt_sha256": receipt_sha,
        "review_receipt_sha256": "c" * 64,
        "review_sheet_sha256": "d" * 64,
        "review_item_sha256": "e" * 64,
    }
    item = _item(fid, row)
    item["packet"] = packet
    item["validated_authority_receipt_sha256"] = receipt_sha

    assessed = trust_engine.analyse_item(item, {})
    assert assessed["validation_errors"] == []
    assert assessed["original_verdict"] == "REVIEW"
    assert assessed["effective_verdict"] == "DROP"
    assert assessed["override"] is True
    assert assessed["explicit_label_authority"] is True
    assert assessed["human_influenced"] is True
    report = _report([item])
    assert report["provenance"]["structured_overrides"] == 1
    assert report["provenance"]["explicit_label_authority"] == 1
    assert report["routing"]["counts"][trust_engine.ROUTE_AUTHORITY] == 1
    assert report["routing"]["direct_user_work_created"] == 0


def test_override_restores_original_review_and_preserves_human_authority():
    row = _row(7, verdict="REVIEW", confidence="leaning")
    row["verdict"] = "DROP"
    row["confidence"] = "strong"
    row["resolve_hint"] = None
    row["override"] = {
        "from": "REVIEW",
        "by": "human",
        "date": "2026-09-26",
        "note": "independent frame resolved it",
        "resolve_hint": "fetch an independent frame",
        "confidence_from": "leaning",
    }
    ledger = {
        "areas": [{
            "area": "test-co",
            "reviewed": {"REVIEW": "each"},
            "sample_fids": [],
        }]
    }
    report = _report([_item(7, row)], ledger)
    item = report["items"][0]
    assert item["original_verdict"] == "REVIEW"
    assert item["effective_verdict"] == "DROP"
    assert item["route"] == trust_engine.ROUTE_AUTHORITY
    assert report["calibration"]["deferred_reviews_resolved"] == 1
    assert report["calibration"]["deferred_resolution_counts"] == {"DROP": 1}


def test_confirmation_without_loader_verified_receipt_is_not_authority():
    row = _row(8, verdict="DROP", confidence="strong")
    row["human_confirmation"] = {
        "version": 1,
        "verdict": "DROP",
        "by": "human",
        "reviewer": "trekdex-project-owner",
        "date": "2026-09-27",
        "note": "user accepted this specific conservative recommendation",
        "decision_sha256": tr.sha256_json(tr.decision_projection(row)),
        "evidence_sha256": tr.evidence_sha256(row),
        "packet_sha256": "a" * 64,
        "source_run_id": "b" * 64,
        "authority_receipt_sha256": "c" * 64,
    }
    report = _report([_item(8, row)], {"areas": []})
    item = report["items"][0]
    assert item["original_verdict"] == "DROP"
    assert item["effective_verdict"] == "DROP"
    assert item["human_confirmation"] is False
    assert item["explicit_label_authority"] is False
    assert item["route"] == trust_engine.ROUTE_REFRESH
    assert any("authority receipt was not verified" in error
               for error in item["validation_errors"])
    assert report["provenance"]["ledger_reviewed_per_lot"] == 0
    assert report["calibration"]["binary"]["DROP"]["reviewed"] == 0


def test_promotion_requires_exact_primary_identity_before_direct_auto():
    items = []
    reviews_by_area = {f"reviewed-{index}": [] for index in range(3)}
    # 381 zero-error KEEP reviews are the Wilson requirement for a 1% bound.
    # Mixed case/outer whitespace canonicalize to the same exact identity.
    for fid in range(1, 382):
        area = f"reviewed-{fid % 3}"
        row = _with_primary_provenance(
            _row(fid, area=area), " Primary-Family ", " Primary-Model ",
        )
        items.append(_item(fid, row))
        family = "reference-family-a" if fid % 2 else "reference-family-b"
        reviewer_id = "reviewer-a" if fid % 2 else "reviewer-b"
        reviews_by_area[area].append(_blind_review(row, family, reviewer_id))
    ledger_areas = [{
        "area": area,
        "reviewed": {"KEEP": "each"},
        "blind_reviews": reviews,
    } for area, reviews in sorted(reviews_by_area.items())]

    future_rows = {
        "exact": _with_primary_provenance(
            _row(9991, area="future-area"), "primary-family", "primary-model",
        ),
        "model-transfer": _with_primary_provenance(
            _row(9992, area="future-area"), "primary-family", "other-model",
        ),
        "family-transfer": _with_primary_provenance(
            _row(9993, area="future-area"), "other-family", "primary-model",
        ),
        "prompt-transfer": _with_primary_provenance(
            _row(9994, area="future-area"), "primary-family", "primary-model",
            "f" * 64,
        ),
    }
    items.extend(_item(row["fid"], row) for row in future_rows.values())

    report = _report(items, {"areas": ledger_areas})
    metric = report["candidate_policies"]["strict"]["KEEP"]
    assert report["version"] == trust_engine.REPORT_VERSION == 2
    assert report["policy"]["id"] == "parking-trust-shadow-v2"
    assert "promotion_ready" not in metric
    assert "promotion_ready" not in report["generalization"]
    assert metric["reviewed"] == 381 and metric["errors"] == 0
    assert metric["promotion_reviewed"] == 381 and metric["promotion_errors"] == 0
    assert metric["ready_identity_count"] == 1
    scope = next(
        scope for scope in metric["promotion_identity_metrics"]
        if scope["promotion_ready"]
    )
    assert scope["primary_identity"] == {
        "policy_id": "parking-trust-shadow-v2",
        "model_family": "primary-family",
        "model_id": "primary-model",
        "prompt_sha256": "b" * 64,
    }
    assert scope["promotion_ready"] is True
    routes = {row["key"]: row["route"] for row in report["items"]}
    assert routes["way/9991"] == trust_engine.ROUTE_AUTO_KEEP
    assert routes["way/9992"] == trust_engine.ROUTE_CHALLENGE
    assert routes["way/9993"] == trust_engine.ROUTE_CHALLENGE
    assert routes["way/9994"] == trust_engine.ROUTE_CHALLENGE
    # Reviewed history remains authority rather than being re-applied as model output.
    assert report["routing"]["counts"][trust_engine.ROUTE_AUTHORITY] == 381


def test_cross_family_pooling_cannot_create_an_authorizing_identity():
    items = []
    reviews_by_area = {f"pooled-{index}": [] for index in range(3)}
    for fid in range(1, 382):
        area = f"pooled-{fid % 3}"
        primary_suffix = "a" if fid <= 191 else "b"
        row = _with_primary_provenance(
            _row(fid, area=area),
            f"primary-family-{primary_suffix}",
            f"primary-model-{primary_suffix}",
        )
        items.append(_item(fid, row))
        reviewer_suffix = "a" if fid % 2 else "b"
        reviews_by_area[area].append(_blind_review(
            row,
            f"reference-family-{reviewer_suffix}",
            f"reviewer-{reviewer_suffix}",
        ))
    ledger = {"areas": [{
        "area": area,
        "reviewed": {"KEEP": "each"},
        "blind_reviews": reviews,
    } for area, reviews in sorted(reviews_by_area.items())]}
    for fid, suffix in ((9001, "a"), (9002, "b")):
        row = _with_primary_provenance(
            _row(fid, area="future-area"),
            f"primary-family-{suffix}",
            f"primary-model-{suffix}",
        )
        items.append(_item(fid, row))

    report = _report(items, ledger)
    metric = report["candidate_policies"]["strict"]["KEEP"]
    assert metric["promotion_reviewed"] == 381
    assert metric["promotion_error_upper_95"] <= metric["target"]
    assert "promotion_ready" not in metric
    assert metric["ready_identity_count"] == 0
    assert len(metric["promotion_identity_metrics"]) == 2
    assert all(not scope["promotion_ready"]
               for scope in metric["promotion_identity_metrics"])
    routes = {row["key"]: row["route"] for row in report["items"]}
    assert routes["way/9001"] == trust_engine.ROUTE_CHALLENGE
    assert routes["way/9002"] == trust_engine.ROUTE_CHALLENGE


def test_reviewer_id_family_relabel_invalidates_all_credit_symmetrically():
    specs = (
        (1, "shared-reviewer", "reference-family-a"),
        (2, "stable-reviewer", "reference-family-c"),
        (3, "shared-reviewer", "reference-family-b"),
        (4, "stable-reviewer", "reference-family-c"),
    )
    items = []
    reviews = []
    for fid, reviewer_id, family in specs:
        row = _with_primary_provenance(_row(fid, area="conflict-area"))
        items.append(_item(fid, row))
        reviews.append(_blind_review(row, family, reviewer_id))
    ledger = {"areas": [{
        "area": "conflict-area",
        "reviewed": {"KEEP": "each"},
        "blind_reviews": reviews,
    }]}

    report = _report(items, ledger)
    metric = report["candidate_policies"]["strict"]["KEEP"]
    assert metric["reviewed"] == 4  # historical diagnostics are preserved
    assert metric["promotion_reviewed"] == 2
    conflict = report["provenance"]["promotion_reviewer_identity_conflicts"]
    assert conflict == [{
        "reviewer_id": "shared-reviewer",
        "claimed_families": ["reference-family-a", "reference-family-b"],
        "affected_records": [
            {"key": "way/1", "area": "conflict-area", "original_verdict": "KEEP",
             "claimed_family": "reference-family-a"},
            {"key": "way/3", "area": "conflict-area", "original_verdict": "KEEP",
             "claimed_family": "reference-family-b"},
        ],
    }]
    by_key = {row["key"]: row for row in report["items"]}
    for fid in (1, 3):
        review = by_key[f"way/{fid}"]["review"]
        assert review["promotion_evidence_locally_valid"] is True
        assert review["promotion_evidence"] is False
        assert review["promotion_reviewer_family_conflict"] == [
            "reference-family-a", "reference-family-b",
        ]
    for fid in (2, 4):
        review = by_key[f"way/{fid}"]["review"]
        assert review["promotion_evidence"] is True
        assert review["promotion_reviewer_family_conflict"] is None

    reversed_report = _report(list(reversed(items)), {
        "areas": [{**ledger["areas"][0], "blind_reviews": list(reversed(reviews))}]
    })
    assert (reversed_report["provenance"]["promotion_reviewer_identity_conflicts"]
            == conflict)


def test_existing_ledger_without_blind_reviews_is_valid_but_nonpromoting():
    row = _with_primary_provenance(_row(7001, area="legacy-area"))
    report = _report([_item(7001, row)], {"areas": [{
        "area": "legacy-area", "reviewed": {"KEEP": "each"},
    }]})
    item = report["items"][0]
    assert item["review"]["binary"] is True
    assert item["review"]["promotion_evidence"] is False
    assert item["review"]["promotion_provenance_errors"] == []
    assert report["candidate_policies"]["strict"]["KEEP"]["promotion_reviewed"] == 0


def test_actual_corpus_replay_is_deterministic_and_keeps_claims_honest():
    if _deferred_oversized_registry():
        pytest.skip(
            "parent-owned archive/rollback must remove the oversized legacy "
            "registry before actual-corpus replay"
        )
    items, corpus, sidecar = replay_trust.load_corpus()
    ledger = json.loads((replay_trust.DATA / "calibration.json").read_text())
    groundtruth = json.loads((replay_trust.DATA / "groundtruth.json").read_text())
    first = trust_engine.build_report(items, ledger, groundtruth, sidecar, corpus)
    second = trust_engine.build_report(items, ledger, groundtruth, sidecar, corpus)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)

    assert corpus["source_rows"] == 1326
    assert corpus["unique_clusters"] == 1253
    assert corpus["folded_duplicates"] == 73
    assert corpus["corpus_sha256"] == (
        "20da04cefa0dd10ca99dc03daeee01f267e0cf93c68dc6f248d67b9c264eecf7"
    )
    assert corpus["sidecar_sha256"] == (
        "6e109573d860d07c70d9f26f8fcda9695005e87c8d840ffd16f91a086db7be1d"
    )
    assert first["corpus"]["final_verdicts"] == {
        "DROP": 372, "KEEP": 874, "REVIEW": 7,
    }
    assert first["corpus"]["original_judge_verdicts"] == {
        "DROP": 370, "KEEP": 874, "REVIEW": 9,
    }
    assert first["corpus"]["schema_current"] == 1015
    assert first["corpus"]["schema_not_current"] == 238
    assert first["provenance"]["explicit_label_authority"] == 109
    assert first["provenance"]["human_influenced_union"] == 116
    assert first["calibration"]["binary"]["KEEP"]["reviewed"] == 0
    assert first["calibration"]["binary"]["DROP"]["reviewed"] == 38
    assert first["calibration"]["binary"]["DROP"]["errors"] == 0
    assert first["candidate_policies"]["strict"]["KEEP"]["eligible"] == 101
    assert first["candidate_policies"]["strict"]["DROP"]["eligible"] == 23
    assert first["candidate_policies"]["strict"]["KEEP"]["promotion_reviewed"] == 0
    assert first["candidate_policies"]["strict"]["DROP"]["promotion_reviewed"] == 0
    assert first["generalization"]["promotion_grade_reviewed"] == 0
    assert "promotion_ready" not in first["candidate_policies"]["strict"]["KEEP"]
    assert "promotion_ready" not in first["candidate_policies"]["strict"]["DROP"]
    assert first["candidate_policies"]["strict"]["KEEP"]["ready_identity_count"] == 0
    assert first["candidate_policies"]["strict"]["DROP"]["ready_identity_count"] == 0
    assert first["generalization"]["ready_exact_primary_identities"] == []
    assert first["routing"]["direct_user_work_created"] == 0
    assert first["routing"]["model_direct_auto"] == 0
    assert first["routing"]["counts"][trust_engine.ROUTE_RESOLVED] == 35
    assert sum(first["routing"]["counts"].values()) == 1253

    zion = first["historical_replay"]["zion"]
    assert (zion["total_labels"], zion["matched_predictions"], zion["agreements"],
            zion["disagreements"], zion["missing_predictions"]) == (39, 31, 31, 0, 8)
    assert zion["independent_holdout"] is False
    griffith = first["historical_replay"]["griffith"]
    assert (griffith["total_labels"], griffith["agreements"],
            griffith["disagreements"]) == (28, 20, 8)
    assert griffith["label_authority"] == "historical-model"


def test_cli_is_read_only_by_default_and_report_writes_are_sandboxed(
        report_root, capsys):
    if _deferred_oversized_registry():
        pytest.skip(
            "parent-owned archive/rollback must remove the oversized legacy "
            "registry before actual-corpus CLI replay"
        )
    assert not report_root.exists()
    assert replay_trust.main(["--format", "summary"]) == 0
    output = capsys.readouterr().out
    assert "1253 clusters" in output
    assert "direct user work created: 0" in output
    assert "SHADOW ONLY" in output
    assert not report_root.exists()

    report = report_root / "shadow.json"
    assert replay_trust.main(["--out", str(report), "--format", "summary"]) == 0
    assert json.loads(report.read_text())["policy"]["shadow_only"] is True
    assert stat.S_IMODE(report.stat().st_mode) == 0o600
    assert not list(report_root.glob(f".{report.name}.tmp-*"))

    with pytest.raises(ValueError, match="only under scripts/parking-adjud/reports"):
        replay_trust.validate_report_path(
            replay_trust.ROOT / "public" / "trust-report.json"
        )
    allowed = replay_trust.validate_report_path(
        report_root / "parking-trust-shadow-v2.json"
    )
    assert allowed == report_root / "parking-trust-shadow-v2.json"
    with pytest.raises(SystemExit):
        replay_trust.main(["--write"])


@pytest.mark.parametrize("candidate", [
    replay_trust.WORK / "shadow" / "parking-trust-shadow-v1.json",
    replay_trust.WORK / "test-co" / "test-co_packets.json",
    replay_trust.WORK / "test-co" / "test-co_verdict_draft_00.json",
    replay_trust.WORK / "test-co" / "test-co_checkpoint.json",
    replay_trust.WORK / ".human-authority-transactions" / "test-co.journal.json",
    replay_trust.DATA / "co_verdicts_osm.json",
    replay_trust.DATA / "co_verdicts_osm_publication_proofs.json",
    replay_trust.DATA / "co_verdicts_osm_publication_floor.json",
    replay_trust.DATA / ".trekdex-publication-transactions" / "co_verdicts_osm.journal.json",
    replay_trust.PUBLIC / "areas" / "parking-verdicts.json",
    replay_trust.PUBLIC / "areas" / "parking-pool.json",
])
def test_report_path_rejects_protocol_and_canonical_artifact_targets(candidate):
    with pytest.raises(ValueError, match="only under scripts/parking-adjud/reports"):
        replay_trust.validate_report_path(candidate)


def test_report_path_rejects_custom_inputs_and_lock_namespaces(report_root):
    custom_tmp = report_root / "tmp-inputs"
    custom_data = report_root / "data-inputs"
    with pytest.raises(ValueError, match="--tmp"):
        replay_trust.validate_report_path(
            custom_tmp / "packet-report.json", tmp_dir=custom_tmp
        )
    with pytest.raises(ValueError, match="--data-dir"):
        replay_trust.validate_report_path(
            custom_data / "store-report.json", data_dir=custom_data
        )

    for keyword in ("sidecar_path", "ledger_path", "groundtruth_path"):
        collision = report_root / f"{keyword}.json"
        with pytest.raises(ValueError, match="collides with a replay input"):
            replay_trust.validate_report_path(
                collision, **{keyword: collision}
            )

    for relative in (
        Path(".trekdex-locks/report.json"),
        Path(".trust-resolver-locks/report.json"),
        Path(".human-authority-transactions/report.journal.json"),
        Path(".trekdex-publication-transactions/report.journal.json"),
        Path("locks/report.json"),
        Path("report.lock.json"),
    ):
        with pytest.raises(ValueError, match="dot or lock namespaces"):
            replay_trust.validate_report_path(report_root / relative)


def test_report_path_rejects_traversal_and_symlinked_components(
        report_root, tmp_path):
    report_root.mkdir(mode=0o700)
    with pytest.raises(ValueError, match="traversal components"):
        replay_trust.validate_report_path(
            report_root / "nested" / ".." / "report.json"
        )

    outside = tmp_path / "outside"
    outside.mkdir()
    linked_parent = report_root / "linked-parent"
    linked_parent.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlinked path components"):
        replay_trust.validate_report_path(linked_parent / "report.json")

    victim = tmp_path / "victim.json"
    victim.write_bytes(b"victim\n")
    linked_target = report_root / "linked-report.json"
    linked_target.symlink_to(victim)
    with pytest.raises(ValueError, match="symlinked path components"):
        replay_trust.write_report(linked_target, "replacement\n")
    assert victim.read_bytes() == b"victim\n"


def test_write_report_defeats_predictable_temp_symlink_and_sets_mode_0600(
        report_root, tmp_path):
    report_root.mkdir(mode=0o700)
    target = report_root / "safe-report.json"
    victim = tmp_path / "predictable-temp-victim"
    victim.write_bytes(b"do not change\n")
    predictable = report_root / f".{target.name}.tmp-{os.getpid()}"
    predictable.symlink_to(victim)

    assert replay_trust.write_report(target, '{"safe": true}\n') == target

    assert target.read_bytes() == b'{"safe": true}\n'
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert victim.read_bytes() == b"do not change\n"
    assert predictable.is_symlink()


def test_corpus_report_holds_source_and_output_locks_through_build_and_write(
        report_root, monkeypatch):
    if _deferred_oversized_registry():
        pytest.skip(
            "parent-owned archive/rollback must remove the oversized legacy "
            "registry before actual-corpus lock replay"
        )
    target = report_root / "corpus-report.json"
    output_lock = tr.resource_lock_path(replay_trust.PARKING_ADJUD, target)
    source_lock = tr.resource_lock_path(
        replay_trust.DATA, replay_trust.DATA / "co_verdicts_osm.json"
    )
    ledger_lock = tr.resource_lock_path(
        replay_trust.DATA, replay_trust.DATA / "calibration.json"
    )
    groundtruth_lock = tr.resource_lock_path(
        replay_trust.DATA, replay_trust.DATA / "groundtruth.json"
    )
    observed = []
    real_build = replay_trust.trust_engine.build_report
    real_atomic = replay_trust.trusted_fs.atomic_write_bytes

    def observe(stage):
        observed.append((
            stage,
            _other_process_can_take_lock(output_lock, shared=True),
            _other_process_can_take_lock(source_lock),
            _other_process_can_take_lock(ledger_lock),
            _other_process_can_take_lock(groundtruth_lock),
        ))

    def build_under_locks(*args, **kwargs):
        observe("build")
        return real_build(*args, **kwargs)

    def write_under_locks(path, data):
        observe("write")
        return real_atomic(path, data)

    monkeypatch.setattr(replay_trust.trust_engine, "build_report", build_under_locks)
    monkeypatch.setattr(
        replay_trust.trusted_fs, "atomic_write_bytes", write_under_locks
    )

    assert replay_trust.main([
        "--out", str(target), "--format", "summary",
    ]) == 0
    assert observed == [
        ("build", False, False, False, False),
        ("write", False, False, False, False),
    ]
    assert _other_process_can_take_lock(output_lock)
    assert _other_process_can_take_lock(source_lock)
    assert _other_process_can_take_lock(ledger_lock)
    assert _other_process_can_take_lock(groundtruth_lock)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_work_report_holds_global_then_area_and_output_locks_through_write(
        tmp_path, report_root, monkeypatch):
    work, ledger = _write_work_replay_case(tmp_path)
    target = report_root / "work-report.json"
    output_lock = tr.resource_lock_path(replay_trust.PARKING_ADJUD, target)
    ledger_lock = tr.resource_lock_path(replay_trust.PARKING_ADJUD, ledger)
    inventory_lock = tr.resource_lock_path(
        replay_trust.PARKING_ADJUD, tr.dossier_resource_path(work)
    )
    area_lock = tr.area_lock_path(work, "new-area-co")
    opened = []
    observed = []
    real_open = replay_trust.trusted_fs.open_lock_file
    real_build = replay_trust.trust_engine.build_report
    real_atomic = replay_trust.trusted_fs.atomic_write_bytes

    def recording_open(path):
        opened.append(Path(path).resolve())
        return real_open(path)

    def observe(stage):
        observed.append((
            stage,
            _other_process_can_take_lock(output_lock, shared=True),
            _other_process_can_take_lock(ledger_lock),
            _other_process_can_take_lock(inventory_lock),
            _other_process_can_take_lock(area_lock),
        ))

    def build_under_locks(*args, **kwargs):
        observe("build")
        return real_build(*args, **kwargs)

    def write_under_locks(path, data):
        observe("write")
        return real_atomic(path, data)

    monkeypatch.setattr(
        replay_trust.trusted_fs, "open_lock_file", recording_open
    )
    monkeypatch.setattr(replay_trust.trust_engine, "build_report", build_under_locks)
    monkeypatch.setattr(
        replay_trust.trusted_fs, "atomic_write_bytes", write_under_locks
    )

    assert replay_trust.main([
        "--work-area", "new-area-co", "--tmp", str(work),
        "--ledger", str(ledger), "--out", str(target),
        "--format", "summary",
    ]) == 0
    assert observed == [
        ("build", False, False, False, False),
        ("write", False, False, False, False),
    ]
    assert opened[-1] == area_lock.resolve()
    assert output_lock.resolve() in opened[:-1]
    assert ledger_lock.resolve() in opened[:-1]
    assert inventory_lock.resolve() in opened[:-1]
    assert _other_process_can_take_lock(output_lock)
    assert _other_process_can_take_lock(ledger_lock)
    assert _other_process_can_take_lock(inventory_lock)
    assert _other_process_can_take_lock(area_lock)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_report_atomic_failure_preserves_inputs_and_existing_bytes_and_unlocks(
        tmp_path, report_root, monkeypatch, capsys):
    work, ledger = _write_work_replay_case(tmp_path)
    target = report_root / "existing-report.json"
    replay_trust.write_report(target, "existing report\n")
    before_report = target.read_bytes()
    protected_inputs = {
        path: path.read_bytes()
        for path in (*sorted(work.glob("*.json")), ledger)
    }
    output_lock = tr.resource_lock_path(replay_trust.PARKING_ADJUD, target)
    area_lock = tr.area_lock_path(work, "new-area-co")
    real_replace = replay_trust.trusted_fs.os.replace

    def fail_report_replace(source_name, target_name, **kwargs):
        if target_name == target.name:
            raise OSError("simulated report replacement failure")
        return real_replace(source_name, target_name, **kwargs)

    monkeypatch.setattr(
        replay_trust.trusted_fs.os, "replace", fail_report_replace
    )
    assert replay_trust.main([
        "--work-area", "new-area-co", "--tmp", str(work),
        "--ledger", str(ledger), "--out", str(target),
        "--format", "summary",
    ]) == 2

    assert "simulated report replacement failure" in capsys.readouterr().err
    assert target.read_bytes() == before_report
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert all(path.read_bytes() == value for path, value in protected_inputs.items())
    assert not list(report_root.glob(f".{target.name}.tmp-*"))
    assert _other_process_can_take_lock(output_lock)
    assert _other_process_can_take_lock(area_lock)


def test_groundtruth_matching_is_one_to_one_and_counts_missing_predictions():
    sidecar = {
        "lots": {
            "way/1": {
                "area": "zion-wilderness-ut", "lat": 37.0, "lon": -113.0,
                "verdict": "KEEP",
            }
        }
    }
    labels = {
        "zion": {
            "a": {"lat": 37.0, "lon": -113.0, "want": "KEEP", "confidence": "certain", "src": "user"},
            "b": {"lat": 37.0, "lon": -113.0, "want": "DROP", "confidence": "certain", "src": "user"},
        }
    }
    replay = trust_engine.replay_groundtruth(sidecar, labels)["zion"]
    assert replay["matched_predictions"] == 1
    assert replay["agreements"] == 1
    assert replay["missing_predictions"] == 1
    assert replay["missing_label_keys"] == ["b"]


def test_human_words_in_model_prose_are_contamination_not_authority():
    row = _row(41, confidence="strong")
    row["public"]["evidence"] = "Open co-user lot beside the trail; no gate"
    report = _report([_item(41, row, src="judge-fanout")])
    item = report["items"][0]
    assert item["evidence_is_human"] is True
    assert item["explicit_label_authority"] is False
    assert item["route"] == trust_engine.ROUTE_CHALLENGE
    assert report["provenance"]["human_influenced_union"] == 1
    assert report["provenance"]["explicit_label_authority"] == 0


def test_promotion_rejects_area_booleans_unknown_same_family_and_bad_hashes():
    row = _with_primary_provenance(_row(51, area="identity-test"), "judge-fanout")
    item = _item(51, row, src="judge-fanout")

    area_flags_only = {"identity-test": {
        "area": "identity-test", "reviewed": {"KEEP": "each"},
        "blind": True, "independent": True,
    }}
    assessed = trust_engine.analyse_item(item, area_flags_only)
    assert assessed["review"]["promotion_evidence"] is False

    same = _blind_review(
        row, reviewer_family=" JUDGE-FANOUT ", reviewer_id=" PRIMARY-MODEL "
    )
    assessed = trust_engine.analyse_item(item, {"identity-test": {
        "area": "identity-test", "reviewed": {"KEEP": "each"}, "blind_reviews": [same],
    }})
    assert assessed["review"]["promotion_evidence"] is False
    assert "reviewer identity matches primary model" in assessed["review"]["promotion_provenance_errors"]
    assert "reviewer family matches primary model family" in assessed["review"]["promotion_provenance_errors"]

    unknown = _blind_review(row, reviewer_family=" Unknown ")
    unknown["primary_decision_sha256"] = "bad"
    assessed = trust_engine.analyse_item(item, {"identity-test": {
        "area": "identity-test", "reviewed": {"KEEP": "each"}, "blind_reviews": [unknown],
    }})
    assert assessed["review"]["promotion_evidence"] is False
    assert "reviewer_family is missing, unknown, or noncanonical" in assessed["review"]["promotion_provenance_errors"]
    assert "primary_decision_sha256 is not a lowercase SHA-256" in assessed["review"]["promotion_provenance_errors"]

    valid = _blind_review(row, reviewer_family=" Reference-Family ")
    assessed = trust_engine.analyse_item(item, {"identity-test": {
        "area": "identity-test", "reviewed": {"KEEP": "each"}, "blind_reviews": [valid],
    }})
    assert assessed["review"]["promotion_evidence"] is True
    assert assessed["review"]["promotion_reviewer_family"] == "reference-family"


def test_promoted_classes_still_send_missing_identity_fallback_and_gaps_away():
    ready_identity = {
        "policy_id": trust_engine.POLICY_VERSION,
        "model_family": "primary-family",
        "model_id": "primary-model",
        "prompt_sha256": "b" * 64,
    }
    promoted = {
        verdict: {"promotion_identity_metrics": [{
            "primary_identity": ready_identity,
            "promotion_ready": True,
        }]}
        for verdict in ("KEEP", "DROP")
    }
    no_provenance = trust_engine.analyse_item(_item(60, _row(60)), {})
    assert no_provenance["candidate"]["strict"] is True
    assert no_provenance["primary_provenance_valid"] is False
    assert no_provenance["primary_promotion_identity"] is None
    assert trust_engine._route(no_provenance, promoted) == trust_engine.ROUTE_CHALLENGE

    malformed_row = _with_primary_provenance(_row(600), prompt_sha256="not-a-hash")
    malformed = trust_engine.analyse_item(_item(600, malformed_row), {})
    assert malformed["candidate"]["strict"] is True
    assert malformed["primary_provenance_valid"] is False
    assert malformed["primary_promotion_identity"] is None
    assert trust_engine._route(malformed, promoted) == trust_engine.ROUTE_CHALLENGE

    fallback = _row(61, verdict="DROP", confidence="strong", prior="bare")
    fallback["serves"]["evidence"] = "Fallback-served trail walk 2200 m; no route"
    assessed = trust_engine.analyse_item(_item(61, fallback), {})
    assert assessed["fallback_signal"] and assessed["no_route_signal"]
    assert assessed["candidate"]["strict"] is False
    assert trust_engine._route(assessed, promoted) == trust_engine.ROUTE_ARBITER

    gap = _row(62, confidence="certain")
    gap["coverage_gap"] = True
    assessed = trust_engine.analyse_item(_item(62, gap), {})
    assert assessed["candidate"]["strict"] is False
    assert trust_engine._route(assessed, promoted) == trust_engine.ROUTE_ARBITER


def test_replay_rejects_semantically_equal_noncanonical_sidecar_bytes(tmp_path):
    if _deferred_oversized_registry():
        pytest.skip(
            "parent-owned archive/rollback must remove the oversized legacy "
            "registry before sidecar replay"
        )
    sidecar = json.loads(replay_trust.SIDECAR.read_text())
    noncanonical = tmp_path / "parking-verdicts.json"
    noncanonical.write_text(json.dumps(sidecar))
    with pytest.raises(ValueError, match="byte-exact canonical output"):
        replay_trust.load_corpus(replay_trust.DATA, noncanonical)


def test_replay_fails_closed_on_unplaceable_and_conflicting_source_rows(tmp_path):
    if _deferred_oversized_registry():
        pytest.skip(
            "do not copy the parent-owned oversized legacy registry before "
            "its archive/rollback"
        )
    unplaceable_dir = tmp_path / "unplaceable"
    shutil.copytree(replay_trust.DATA, unplaceable_dir)
    co_path = unplaceable_dir / "co_verdicts_osm.json"
    co = json.loads(co_path.read_text())
    key = next(key for key, row in co.items() if row.get("area") == "pike-national-forest-co")
    co[key].pop("lat", None)
    co[key].pop("lon", None)
    co_path.write_text(json.dumps(co))
    with pytest.raises(
            ValueError,
            match="publication trust root store_sha256 mismatch|not an exact reviewed",
    ):
        replay_trust.load_corpus(unplaceable_dir, replay_trust.SIDECAR)

    conflict_dir = tmp_path / "conflict"
    shutil.copytree(replay_trust.DATA, conflict_dir)
    co_path = conflict_dir / "co_verdicts_osm.json"
    co = json.loads(co_path.read_text())
    duplicate = co["way/229731562"]
    duplicate["verdict"] = "DROP" if duplicate["verdict"] == "KEEP" else "KEEP"
    co_path.write_text(json.dumps(co))
    with pytest.raises(
            ValueError,
            match="publication trust root store_sha256 mismatch|not an exact reviewed|does not share one exact row",
    ):
        replay_trust.load_corpus(conflict_dir, replay_trust.SIDECAR)


def test_completed_work_area_becomes_a_read_only_machine_route_manifest(
        tmp_path, report_root, capsys):
    row = _row(71, area="new-area-co", confidence="strong")
    packet = {
        "fid": 71, "area": "new-area-co",
        "source_generation": _work_source_generation(
            tmp_path, "new-area-co", 71
        ),
        "osm": ["way/71"], "prior": "surveyed",
    }
    (tmp_path / "new-area-co_packets.json").write_text(json.dumps({"71": packet}))
    (tmp_path / "new-area-co_verdict_draft_00.json").write_text(json.dumps([row]))

    items, corpus, _ = replay_trust.load_work_area(tmp_path, "new-area-co")
    assert corpus["unique_clusters"] == 1 and corpus["work_area"] == "new-area-co"
    assert items[0]["row"] == row
    protocol_files = tuple(sorted(tmp_path.glob("*.json")))
    before = {path: path.read_bytes() for path in protocol_files}
    assert not report_root.exists()
    assert replay_trust.main([
        "--work-area", "new-area-co", "--tmp", str(tmp_path), "--format", "json",
    ]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["items"][0]["route"] == trust_engine.ROUTE_CHALLENGE
    assert report["routing"]["direct_user_work_created"] == 0
    assert all(path.read_bytes() == value for path, value in before.items())
    assert tr.area_lock_path(tmp_path, "new-area-co").is_file()
    assert not report_root.exists()

    (tmp_path / "new-area-co_verdict_draft_00.json").write_text("[]")
    with pytest.raises(ValueError, match="missing packet fids"):
        replay_trust.load_work_area(tmp_path, "new-area-co")

    foreign = tmp_path / "foreign"
    foreign.mkdir()
    foreign_row = _row(72, area="different-area", confidence="strong")
    foreign_packet = {
        "fid": 72, "area": "different-area",
        "source_generation": _work_source_generation(
            foreign, "claimed-area", 72
        ),
        "osm": ["way/72"], "prior": "surveyed",
    }
    (foreign / "claimed-area_packets.json").write_text(json.dumps({"72": foreign_packet}))
    (foreign / "claimed-area_verdict_draft_00.json").write_text(json.dumps([foreign_row]))
    with pytest.raises(ValueError, match="does not match 'claimed-area'"):
        replay_trust.load_work_area(foreign, "claimed-area")


def test_reviewer_family_aliases_cannot_manufacture_promotion_breadth():
    identity = {
        "policy_id": trust_engine.POLICY_VERSION,
        "model_family": "primary-family",
        "model_id": "primary-model",
        "prompt_sha256": "b" * 64,
    }
    analyses = []
    for index in range(381):
        analyses.append({
            "candidate": {"strict": True},
            "original_verdict": "KEEP",
            "primary_promotion_identity": identity,
            "review": {
                "binary": True,
                "error": False,
                "promotion_evidence": True,
                "promotion_error": False,
                "promotion_reviewer_family": "reference-family",
            },
            "area": f"area-{index % 3}",
            "source_family": "primary-family",
        })
    metric = trust_engine.candidate_policy_metrics(analyses, "strict")["KEEP"]
    assert metric["reviewed"] == 381 and metric["promotion_reviewed"] == 381
    assert metric["promotion_reviewed_source_families"] == ["reference-family"]
    assert "promotion_ready" not in metric
    assert metric["ready_identity_count"] == 0
    scope = metric["promotion_identity_metrics"][0]
    assert scope["promotion_reviewed_reviewer_families"] == ["reference-family"]
    assert scope["promotion_ready"] is False


def test_work_area_structured_packet_risk_overrides_incomplete_prose():
    row = _row(880, confidence="strong")
    row["serves"]["evidence"] = "Trail appears nearby"
    item = _item(880, row)
    item["packet"] = {
        "fid": 880,
        "area": "test-co",
        "osm": ["way/880"],
        "prior": "surveyed",
        "serves": {"fallback": True},
        "walk": {"walk_m": 2744, "conn": "no route"},
    }
    assessed = trust_engine.analyse_item(item, {})
    assert assessed["fallback_signal"] is True
    assert assessed["no_route_signal"] is True
    assert assessed["walk_m"] == 2744.0
    report = _report([item])
    assert report["items"][0]["route"] == trust_engine.ROUTE_ARBITER
