"""Tests for the judge fan-out tooling in scripts/parking-adjud/tools:
merge_drafts.py (the tool that writes the committed verdict stores, and the
human's --set pen), calibration.py (the human-vs-judge ledger), and the
one-source rule for the judge lessons."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

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

RING = [[40.0, -105.5], [40.0001, -105.5], [40.0001, -105.4999], [40.0, -105.4999], [40.0, -105.5]]


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
                   "osm": [f"way/{f}"], "prior": by_fid[f]["prior"],
                   "ring": RING, "rings": [RING], "tags_union": {"name": f"Lot {f}"}}
                  for f in fids]
    (tmp / f"{slug}_dossier.json").write_text(json.dumps({"slug": slug, "facilities": facilities}))
    (tmp / f"{slug}_pub.txt").write_text(",".join(str(f) for f in fids))
    (tmp / f"{slug}_packets.json").write_text(json.dumps({
        str(f): {"fid": f, "area": slug, "osm": [f"way/{f}"], "prior": by_fid[f]["prior"]}
        for f in fids
    }))
    for k, d in enumerate(drafts):
        (tmp / f"{slug}_verdict_draft_{k:02d}.json").write_text(json.dumps(d))
    merge_drafts.PADJ_TMP = str(tmp)
    calibration.PADJ_TMP = str(tmp)
    return tmp


def _drafts(tmp, slug="test-co"):
    return {e["fid"]: e for e in merge_drafts.load_draft(Path(tmp), slug)}


# ---------------------------------------------------------------- merge_drafts


def test_set_records_the_judges_call_and_a_second_set_keeps_it(tmp_path, capsys):
    tmp = _area(tmp_path)
    store = tmp_path / "store.json"
    args = [str(store), "test-co", "--judged", "2026-09-13"]
    assert merge_drafts.main(args + ["--set", "1=DROP", "--note", "sheet review"]) == 0
    e = _drafts(tmp)[1]
    assert e["verdict"] == "DROP" and e["confidence"] == "strong"
    assert e["override"] == {"from": "KEEP", "by": "human", "date": "2026-09-13", "note": "sheet review",
                             "resolve_hint": None, "confidence_from": "certain"}
    # Changing one's mind to a third verdict revises the override but keeps
    # the judge's original call; flipping back to it removes the override.
    assert merge_drafts.main(args + ["--set", "1=REVIEW", "--note", "second look"]) == 0
    e = _drafts(tmp)[1]
    assert e["verdict"] == "REVIEW" and e["override"]["from"] == "KEEP"
    assert e["override"]["confidence_from"] == "certain" and e["override"]["note"] == "second look"
    assert e["resolve_hint"] == "second look"          # a human REVIEW carries its own hint
    assert merge_drafts.main(args + ["--set", "1=KEEP"]) == 0
    e = _drafts(tmp)[1]
    assert e["verdict"] == "KEEP" and "override" not in e and e["confidence"] == "certain"
    assert "back to the judge's call" in capsys.readouterr().out


def test_set_to_the_current_verdict_is_a_no_op_and_unknown_fid_refuses_write(tmp_path, capsys):
    tmp = _area(tmp_path)
    store = tmp_path / "store.json"
    before = (tmp / "test-co_verdict_draft_00.json").read_text()
    assert merge_drafts.main([str(store), "test-co", "--set", "1=KEEP"]) == 0
    assert (tmp / "test-co_verdict_draft_00.json").read_text() == before     # untouched, still certain
    assert "nothing to set" in capsys.readouterr().out
    rc = merge_drafts.main([str(store), "test-co", "--set", "99=DROP", "--write"])
    assert rc == 1 and not store.exists()
    assert (tmp / "test-co_verdict_draft_00.json").read_text() == before


def test_review_flipped_to_drop_nulls_the_hint_and_lands_in_the_store_with_geometry(tmp_path):
    tmp = _area(tmp_path)
    store = tmp_path / "store.json"
    args = [str(store), "test-co", "--judged", "2026-09-13"]
    assert merge_drafts.main(args + ["--set", "3=DROP", "--write"]) == 0
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
    rc = merge_drafts.main([str(store), "test-co", "--write"])
    assert rc == 1 and "other-co already holds way/1 as DROP" in capsys.readouterr().out
    assert json.loads(store.read_text())["way/1"]["area"] == "other-co"        # untouched
    other["verdict"] = "KEEP"
    other["serves"]["call"] = "yes"
    store.write_text(json.dumps({"way/1": other}))
    assert merge_drafts.main([str(store), "test-co", "--judged", "2026-09-13", "--write"]) == 0
    s = json.loads(store.read_text())
    assert s["way/1"]["area"] == "other-co" and s["way/1"]["judged"] == "2026-09-01"   # first record stands
    assert s["way/2"]["area"] == "test-co" and "1 already held by another area, kept" in capsys.readouterr().out


def test_bad_judged_date_and_missing_z3_on_a_surveyed_drop_are_refused(tmp_path, capsys):
    import pytest
    d = _verdict(5, "DROP", "strong", prior="surveyed")
    d["frames_used"] = ["z1", "z2"]
    _area(tmp_path, drafts=[[d]])
    store = tmp_path / "store.json"
    with pytest.raises(SystemExit):
        merge_drafts.main([str(store), "test-co", "--judged", "yesterday"])
    assert merge_drafts.main([str(store), "test-co", "--write"]) == 1
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
    assert merge_drafts.main([str(store), "test-co", "--judged", "2026-09-13",
                              "--set", "3=DROP", "--set", "1=DROP"]) == 0
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


def test_sample_mode_gives_a_keep_denominator_of_exactly_the_sampled_lots(tmp_path, capsys):
    drafts = [[_verdict(f, "KEEP", "certain" if f % 2 else "strong") for f in range(1, 11)],
              [_verdict(20, "DROP", "strong")]]
    tmp = _area(tmp_path, drafts=drafts)
    store = tmp_path / "store.json"
    # Human reviews the sampled KEEPs 1,2,3,4 one by one and flips #2; KEEPs 5..10 unseen.
    assert merge_drafts.main([str(store), "test-co", "--judged", "2026-09-13", "--set", "2=DROP"]) == 0
    calibration.LEDGER = str(tmp_path / "calibration.json")
    import pytest
    with pytest.raises(SystemExit):        # sample mode without the immutable manifest is refused
        calibration.main(["add", "test-co", "--reviewed", "KEEP=sample", "--date", "2026-09-13"])
    current_drafts = list(_drafts(tmp).values())
    packets = {int(fid): packet for fid, packet in json.loads(
        (tmp / "test-co_packets.json").read_text()).items()}
    population_sha = calibration._sample_population_sha(current_drafts, packets)
    manifest = {"version": 1, "area": "test-co", "requested": 4,
                "population_sha256": population_sha, "sample": [1, 2, 3, 4]}
    (tmp / "test-co_keep_sample.json").write_text(json.dumps(manifest))
    assert calibration.main(["add", "test-co", "--reviewed", "KEEP=sample", "--reviewed", "DROP=each",
                             "--sample-fids", "1,2,3,4", "--date", "2026-09-13"]) == 0
    rec = json.loads(Path(calibration.LEDGER).read_text())["areas"][0]
    assert rec["sample"] == {"KEEP": {"certain": 2, "strong": 2}} and rec["sample_fids"] == [1, 2, 3, 4]
    assert rec["flips"][0]["sampled"] is True
    out = capsys.readouterr().out
    keep_row = next(l for l in out.splitlines() if l.startswith("KEEP any"))
    # judged 10, reviewed 4 (the sample), 1 flip, 75% agree
    assert " 10        4     1   75.0%" in keep_row
    keep_strong = next(l for l in out.splitlines() if l.startswith("KEEP strong"))
    assert "  5        2     1   50.0%" in keep_strong           # #2 is strong, in the sample


# ---------------------------------------------------------- one source of truth


def test_judge_lessons_are_the_handoffs_section_5_verbatim():
    """judge_lessons.md is what every judge agent reads; the handoff is what
    every human reads. They must not drift apart."""
    handoff = (HERE.parent / "docs" / "parking-adjudication-handoff.md").read_text()
    lessons = (TOOLS / "judge_lessons.md").read_text()
    start = handoff.index("## 5. The ten load-bearing lessons")
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
        path.write_bytes(f"tile-{fid}-{zoom}".encode())
        paths[zoom] = str(path)
    return {"fid": fid, "area": "test-co", "osm": osm or [f"way/{fid}"], "prior": "bare",
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
    packets = {1: _packet(1, tmp_path), 2: _packet(2, tmp_path), 3: _packet(3, tmp_path)}
    monkeypatch.setattr(judge_packets, "build_packets", lambda slug, tmp, tiles: dict(packets))
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
    packets = {1: _packet(1, tmp_path), 2: _packet(2, tmp_path)}
    monkeypatch.setattr(judge_packets, "build_packets", lambda slug, tmp, tiles: dict(packets))
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
    packets = {1: _packet(1, tmp_path)}
    monkeypatch.setattr(judge_packets, "build_packets", lambda slug, tmp, tiles: dict(packets))
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

    packets[1]["walk"]["walk_m"] = 999
    assert judge_packets.main(args) == 2
    assert (tmp_path / "test-co_checkpoint_00.json").read_bytes() == manifest
    packets[1]["walk"]["walk_m"] = 10
    Path(packets[1]["tiles"]["z1"]).write_bytes(b"new imagery")
    assert judge_packets.main(args) == 2


def test_generator_rejects_noncanonical_alias_and_consolidated_conflict(tmp_path, monkeypatch, capsys):
    judge_packets = _load("judge_packets_alias", TOOLS / "judge_packets.py")
    packets = {1: _packet(1, tmp_path)}
    monkeypatch.setattr(judge_packets, "build_packets", lambda slug, tmp, tiles: dict(packets))
    alias = tmp_path / "test-co_verdict_draft_000.json"
    alias.write_text(json.dumps([_checkpoint(1)]))
    assert judge_packets.main(["test-co", "--tmp", str(tmp_path)]) == 2
    assert "noncanonical/stale draft" in capsys.readouterr().err
    alias.rename(tmp_path / "test-co_verdict_draft.json")
    (tmp_path / "test-co_verdict_draft_00.json").write_text(json.dumps([_checkpoint(1)]))
    assert judge_packets.main(["test-co", "--tmp", str(tmp_path), "--adopt-existing"]) == 2
    assert "conflicts with numbered" in capsys.readouterr().err


def test_keep_sample_is_immutable_and_uses_original_judge_calls(tmp_path):
    import pytest
    review_sheet = _load("judge_review_sheet_test", TOOLS / "judge_review_sheet.py")
    packets = {fid: _packet(fid, tmp_path) for fid in range(1, 7)}
    verdicts = {fid: _checkpoint(fid) for fid in packets}
    sample = review_sheet.load_or_create_sample(tmp_path, "test-co", 3, verdicts, packets)
    assert len(sample) == 3
    manifest_before = (tmp_path / "test-co_keep_sample.json").read_bytes()
    merge_drafts.set_verdict(verdicts[sample[0]], "DROP", "reviewed", "2026-09-14")
    assert review_sheet.load_or_create_sample(tmp_path, "test-co", 3, verdicts, packets) == sample
    assert (tmp_path / "test-co_keep_sample.json").read_bytes() == manifest_before
    unsampled = next(fid for fid in packets if fid not in sample)
    verdicts[unsampled]["verdict"] = "DROP"
    verdicts[unsampled]["serves"]["call"] = "no"
    with pytest.raises(SystemExit):
        review_sheet.load_or_create_sample(tmp_path, "test-co", 3, verdicts, packets)
    with pytest.raises(SystemExit):
        review_sheet.load_or_create_sample(tmp_path, "test-co", 4, verdicts, packets)


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
    packets = {1: _packet(1, tmp_path)}
    monkeypatch.setattr(judge_packets, "build_packets", lambda slug, tmp, tiles: dict(packets))
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
    packets = {1: _packet(1, tmp_path), 2: _packet(2, tmp_path)}
    monkeypatch.setattr(judge_packets, "build_packets", lambda slug, tmp, tiles: dict(packets))
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
    packets = {1: _packet(1, tmp_path), 2: _packet(2, tmp_path)}
    monkeypatch.setattr(judge_packets, "build_packets", lambda slug, tmp, tiles: dict(packets))
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


def test_skip_judged_keeps_current_area_and_skips_other_area(tmp_path, monkeypatch, capsys):
    judge_packets = _load("judge_packets_skip_owner", TOOLS / "judge_packets.py")
    packets = {1: _packet(1, tmp_path), 2: _packet(2, tmp_path), 3: _packet(3, tmp_path)}
    monkeypatch.setattr(judge_packets, "build_packets", lambda slug, tmp, tiles: dict(packets))
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
