"""Regression tests for the read-only parking trust replay."""
from __future__ import annotations

import copy
import json
import shutil
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
TOOLS = HERE / "parking-adjud" / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import replay_trust
import trust_engine


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
                             model_id: str = "primary-model") -> dict:
    row["judge_provenance"] = {
        "model_id": model_id,
        "model_family": family,
        "decision_sha256": trust_engine.decision_sha256(row),
        "packet_sha256": "a" * 64,
        "prompt_sha256": "b" * 64,
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


def test_promotion_requires_error_bound_area_and_source_breadth_before_direct_auto():
    items = []
    reviews_by_area = {f"reviewed-{index}": [] for index in range(3)}
    # 381 zero-error KEEP reviews are the Wilson requirement for a 1% bound.
    for fid in range(1, 382):
        area = f"reviewed-{fid % 3}"
        src = "judge-fanout" if fid % 2 else "opus-protocol"
        primary_family = "judge-fanout" if fid % 2 else "opus"
        row = _with_primary_provenance(_row(fid, area=area), primary_family, f"primary-{fid % 2}")
        items.append(_item(fid, row, src=src))
        family = "reference-family-a" if fid % 2 else "reference-family-b"
        reviews_by_area[area].append(_blind_review(row, family, f"reviewer-{fid % 2}"))
    ledger_areas = [{
        "area": area,
        "reviewed": {"KEEP": "each"},
        "blind_reviews": reviews,
    } for area, reviews in sorted(reviews_by_area.items())]
    future = _with_primary_provenance(
        _row(9999, area="future-area"), "judge-fanout", "future-primary"
    )
    items.append(_item(9999, future, src="judge-fanout"))

    report = _report(items, {"areas": ledger_areas})
    metric = report["candidate_policies"]["strict"]["KEEP"]
    assert metric["reviewed"] == 381 and metric["errors"] == 0
    assert metric["promotion_reviewed"] == 381 and metric["promotion_errors"] == 0
    assert metric["promotion_ready"] is True
    future_item = next(row for row in report["items"] if row["key"] == "way/9999")
    assert future_item["route"] == trust_engine.ROUTE_AUTO_KEEP
    # Reviewed history remains authority rather than being re-applied as model output.
    assert report["routing"]["counts"][trust_engine.ROUTE_AUTHORITY] == 381


def test_actual_corpus_replay_is_deterministic_and_keeps_claims_honest():
    items, corpus, sidecar = replay_trust.load_corpus()
    ledger = json.loads((replay_trust.DATA / "calibration.json").read_text())
    groundtruth = json.loads((replay_trust.DATA / "groundtruth.json").read_text())
    first = trust_engine.build_report(items, ledger, groundtruth, sidecar, corpus)
    second = trust_engine.build_report(items, ledger, groundtruth, sidecar, corpus)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)

    assert corpus["source_rows"] == 1268
    assert corpus["unique_clusters"] == 1199
    assert corpus["folded_duplicates"] == 69
    assert first["corpus"]["final_verdicts"] == {"DROP": 336, "KEEP": 863}
    assert first["corpus"]["original_judge_verdicts"] == {
        "DROP": 332, "KEEP": 863, "REVIEW": 4,
    }
    assert first["corpus"]["schema_current"] == 961
    assert first["corpus"]["schema_not_current"] == 238
    assert first["provenance"]["explicit_label_authority"] == 90
    assert first["provenance"]["human_influenced_union"] == 97
    assert first["calibration"]["binary"]["KEEP"]["reviewed"] == 0
    assert first["calibration"]["binary"]["DROP"]["reviewed"] == 38
    assert first["calibration"]["binary"]["DROP"]["errors"] == 0
    assert first["candidate_policies"]["strict"]["KEEP"]["eligible"] == 100
    assert first["candidate_policies"]["strict"]["DROP"]["eligible"] == 20
    assert first["candidate_policies"]["strict"]["KEEP"]["promotion_reviewed"] == 0
    assert first["candidate_policies"]["strict"]["DROP"]["promotion_reviewed"] == 0
    assert first["generalization"]["promotion_grade_reviewed"] == 0
    assert not first["candidate_policies"]["strict"]["KEEP"]["promotion_ready"]
    assert not first["candidate_policies"]["strict"]["DROP"]["promotion_ready"]
    assert first["routing"]["direct_user_work_created"] == 0
    assert first["routing"]["model_direct_auto"] == 0
    assert sum(first["routing"]["counts"].values()) == 1199

    zion = first["historical_replay"]["zion"]
    assert (zion["total_labels"], zion["matched_predictions"], zion["agreements"],
            zion["disagreements"], zion["missing_predictions"]) == (39, 31, 31, 0, 8)
    assert zion["independent_holdout"] is False
    griffith = first["historical_replay"]["griffith"]
    assert (griffith["total_labels"], griffith["agreements"],
            griffith["disagreements"]) == (28, 20, 8)
    assert griffith["label_authority"] == "historical-model"


def test_cli_is_read_only_by_default_and_report_writes_are_sandboxed(tmp_path, capsys):
    assert replay_trust.main(["--format", "summary"]) == 0
    output = capsys.readouterr().out
    assert "1199 clusters" in output
    assert "direct user work created: 0" in output
    assert "SHADOW ONLY" in output

    report = tmp_path / "shadow.json"
    assert replay_trust.main(["--out", str(report), "--format", "summary"]) == 0
    assert json.loads(report.read_text())["policy"]["shadow_only"] is True
    assert not list(tmp_path.glob(".*.tmp-*"))

    with pytest.raises(ValueError, match="only under scripts/parking-adjud/work"):
        replay_trust.validate_report_path(replay_trust.ROOT / "public" / "trust-report.json")
    allowed = replay_trust.validate_report_path(
        replay_trust.WORK / "shadow" / "parking-trust-shadow-v1.json"
    )
    assert str(allowed).endswith("scripts/parking-adjud/work/shadow/parking-trust-shadow-v1.json")
    with pytest.raises(SystemExit):
        replay_trust.main(["--write"])


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


def test_promoted_classes_still_send_fallback_no_route_and_coverage_gaps_to_arbiter():
    promoted = {"KEEP": {"promotion_ready": True}, "DROP": {"promotion_ready": True}}
    no_provenance = trust_engine.analyse_item(_item(60, _row(60)), {})
    assert no_provenance["candidate"]["strict"] is True
    assert no_provenance["primary_provenance_valid"] is False
    assert trust_engine._route(no_provenance, promoted) == trust_engine.ROUTE_CHALLENGE

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
    sidecar = json.loads(replay_trust.SIDECAR.read_text())
    noncanonical = tmp_path / "parking-verdicts.json"
    noncanonical.write_text(json.dumps(sidecar))
    with pytest.raises(ValueError, match="byte-exact canonical output"):
        replay_trust.load_corpus(replay_trust.DATA, noncanonical)


def test_replay_fails_closed_on_unplaceable_and_conflicting_source_rows(tmp_path):
    unplaceable_dir = tmp_path / "unplaceable"
    shutil.copytree(replay_trust.DATA, unplaceable_dir)
    co_path = unplaceable_dir / "co_verdicts_osm.json"
    co = json.loads(co_path.read_text())
    key = next(key for key, row in co.items() if row.get("area") == "pike-national-forest-co")
    co[key].pop("lat", None)
    co[key].pop("lon", None)
    co_path.write_text(json.dumps(co))
    with pytest.raises(ValueError, match="source compiler rejected entries"):
        replay_trust.load_corpus(unplaceable_dir, replay_trust.SIDECAR)

    conflict_dir = tmp_path / "conflict"
    shutil.copytree(replay_trust.DATA, conflict_dir)
    co_path = conflict_dir / "co_verdicts_osm.json"
    co = json.loads(co_path.read_text())
    duplicate = co["way/229731562"]
    duplicate["verdict"] = "DROP" if duplicate["verdict"] == "KEEP" else "KEEP"
    co_path.write_text(json.dumps(co))
    with pytest.raises(ValueError, match="source compiler rejected entries"):
        replay_trust.load_corpus(conflict_dir, replay_trust.SIDECAR)


def test_completed_work_area_becomes_a_read_only_machine_route_manifest(tmp_path, capsys):
    row = _row(71, area="new-area-co", confidence="strong")
    packet = {"fid": 71, "area": "new-area-co", "osm": ["way/71"], "prior": "surveyed"}
    (tmp_path / "new-area-co_packets.json").write_text(json.dumps({"71": packet}))
    (tmp_path / "new-area-co_verdict_draft_00.json").write_text(json.dumps([row]))

    items, corpus, _ = replay_trust.load_work_area(tmp_path, "new-area-co")
    assert corpus["unique_clusters"] == 1 and corpus["work_area"] == "new-area-co"
    assert items[0]["row"] == row
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    assert replay_trust.main([
        "--work-area", "new-area-co", "--tmp", str(tmp_path), "--format", "json",
    ]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["items"][0]["route"] == trust_engine.ROUTE_CHALLENGE
    assert report["routing"]["direct_user_work_created"] == 0
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before

    (tmp_path / "new-area-co_verdict_draft_00.json").write_text("[]")
    with pytest.raises(ValueError, match="missing packet fids"):
        replay_trust.load_work_area(tmp_path, "new-area-co")

    foreign = tmp_path / "foreign"
    foreign.mkdir()
    foreign_row = _row(72, area="different-area", confidence="strong")
    foreign_packet = {"fid": 72, "area": "different-area", "osm": ["way/72"], "prior": "surveyed"}
    (foreign / "claimed-area_packets.json").write_text(json.dumps({"72": foreign_packet}))
    (foreign / "claimed-area_verdict_draft_00.json").write_text(json.dumps([foreign_row]))
    with pytest.raises(ValueError, match="does not match 'claimed-area'"):
        replay_trust.load_work_area(foreign, "claimed-area")


def test_reviewer_family_aliases_cannot_manufacture_promotion_breadth():
    analyses = []
    for index in range(381):
        analyses.append({
            "candidate": {"strict": True},
            "original_verdict": "KEEP",
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
    assert metric["promotion_ready"] is False
