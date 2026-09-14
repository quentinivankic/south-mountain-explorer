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
            "serves": {"call": "no" if verdict == "DROP" else "yes", "evidence": "Trail edge 50 m"},
            "resolve_hint": hint}


def _area(tmp_path, slug="test-co", drafts=None):
    """A minimal PADJ_TMP: dossier + _pub.txt + chunk drafts."""
    tmp = tmp_path / "work"
    tmp.mkdir(exist_ok=True)
    drafts = drafts if drafts is not None else [[_verdict(1, "KEEP"), _verdict(2, "DROP", "strong")],
                                                [_verdict(3, "REVIEW", "leaning", hint="fetch a clear frame")]]
    fids = [e["fid"] for d in drafts for e in d]
    facilities = [{"fid": f, "lat": 40.0 + f * 1e-3, "lon": -105.5, "osm": [f"way/{f}"],
                   "ring": RING, "rings": [RING], "tags_union": {"name": f"Lot {f}"}} for f in fids]
    (tmp / f"{slug}_dossier.json").write_text(json.dumps({"slug": slug, "facilities": facilities}))
    (tmp / f"{slug}_pub.txt").write_text(",".join(str(f) for f in fids))
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
    for ph in ("{PROTOCOL_PATH}", "{LESSONS_PATH}", "{CHUNK_PATH}", "{OUT_PATH}"):
        assert ph in text
    assert not text.startswith("#")                       # front matter stripped
    assert '"unclear"' in text and '"unknown"' in text    # the call vocabulary rule
