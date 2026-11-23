"""Registry loader, run-area wiring, CLI and live pins for the Denmark name_unnamed_loops opt-in (M4 NAMING).
The naming gates are tested in test_dk_name_unnamed_loops.py (+ _mutants.py)."""
import json
import os
import subprocess
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parent))
import dk_name_unnamed_loops as naming  # noqa: E402
import dk_publish as dk  # noqa: E402
from test_dk_name_unnamed_loops import (  # noqa: E402
    RED, YELLOW, SRC, SCRIPT, REGISTRY, RUN_AREA, ROOT, entry, trails)
def _registry_with(tmp_path, mutate, name="r.json"):
    doc = json.loads(REGISTRY.read_text(encoding="utf-8"))
    mutate(doc)
    path = tmp_path / name
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path
PLAIN = "langeland-dk"                     # an area with no opt-ins of its own


def _area(doc, aid=PLAIN):
    return next(a for a in doc["areas"] if a["id"] == aid)


def _plain(doc, **fields):
    _area(doc).update(fields)
def test_registry_accepts_the_opt_in_only_on_dk_v3(tmp_path):
    path = _registry_with(tmp_path, lambda d: _plain(d, rules="dk-v3", name_unnamed_loops=[entry()]))
    assert _area(dk.load_registry(path))["name_unnamed_loops"] == [entry()]
    for rules, aid in (("dk-v2", PLAIN), ("legacy", "nationalpark-mols-bjerge-dk")):
        with pytest.raises(dk.DkError, match="name_unnamed_loops requires rules dk-v3"):
            dk.load_registry(_registry_with(
                tmp_path, lambda d, rules=rules, aid=aid: _area(d, aid).update(rules=rules, name_unnamed_loops=[entry()]),
                name=f"{rules}.json"))
@pytest.mark.parametrize("value", [
    [], None, "x", {}, [None], [entry(), entry()],
    [{k: v for k, v in entry().items() if k != "source"}],            # an entry without a source URL is refused
    [{**entry(), "source": ""}], [{**entry(), "source": "http://naturstyrelsen.dk/x"}],
    [{**entry(), "source": "https://www.alltrails.com/trail/denmark/x"}],
    [{**entry(), "listed_km": [3, 1]}], [entry(name="Pælebjerg")],
])
def test_registry_rejects_bad_entries(tmp_path, value):
    with pytest.raises(dk.DkError, match="name_unnamed_loops"):
        dk.load_registry(_registry_with(tmp_path, lambda d: _plain(d, rules="dk-v3", name_unnamed_loops=value)))
def test_registry_without_the_key_loads_as_before():
    reg = dk.load_registry(REGISTRY)
    assert [a["id"] for a in reg["areas"] if "name_unnamed_loops" in a] == ["fano-dk"]
def test_live_registry_names_exactly_the_two_reviewed_pælebjerg_loops():
    doc = json.loads(REGISTRY.read_text(encoding="utf-8"))
    fano = next(a for a in doc["areas"] if a["id"] == "fano-dk")
    assert fano["rules"] == "dk-v3" and fano["hold"] == "curated-do-not-ship"
    assert [(e["relation"], e["name"], e["listed_km"]) for e in fano["name_unnamed_loops"]] == [
        ("r4215022", "Pælebjerg Rød rute", [1, 2]), ("r4215023", "Pælebjerg Gul rute", [3, 5])]
    for e in fano["name_unnamed_loops"]:
        assert e["source"] == SRC and e["reason"].strip()
        naming.check_source(e["source"], "live")
    assert {a["id"] for a in doc["areas"] if "name_unnamed_loops" in a} == {"fano-dk"}
def test_the_unlisted_fano_loops_are_never_named_and_the_labelled_ones_are_excluded():
    doc = json.loads(REGISTRY.read_text(encoding="utf-8"))
    fano = next(a for a in doc["areas"] if a["id"] == "fano-dk")
    excluded = {e["id"]: e["name"] for e in fano["exclude_trails"]}
    named = {e["relation"] for e in fano["name_unnamed_loops"]}
    unlisted = {"r4209046", "r4209047", "r4215703", "r4215704"}
    assert not unlisted & named and not named & set(excluded)
    # Kikkebjerg is assembled under a road name and must not reach the app; the two Fuglekøje relations have no
    # label at all (the app writer skips a nameless trail), and the curated-exclude gate cannot name "no name".
    assert {i: excluded[i] for i in unlisted & set(excluded)} == {"r4209046": "Nørrebjergevej", "r4209047": "Nørrebjergevej"}
    assert len(excluded) == 19 + 2
    assert all(e["reason"].strip() for e in fano["exclude_trails"])
def test_no_shared_engine_file_mentions_the_opt_in():
    for relpath in ("trailforge/assemble/assemble.py", "trailforge/assemble/model.py", "trailforge/assemble/quality.py",
                    "trailforge/assemble/member_safety.py", "trailforge/serve/publish_areas.py",
                    "trailforge/serve/to_app_json.py", "trailforge/extract/prefilter.sh",
                    "scripts/ca_publish.py", "scripts/ca-run-area.sh", "trailforge/golden/ca-areas.json"):
        assert "name_unnamed_loops" not in (ROOT / relpath).read_text(encoding="utf-8"), relpath
# ------------------------------------------------------------- run-area wiring --
def _probe_name_loops(area_id, tmp_path, registry_area):
    """Run the real argument-building part of dk-run-area.sh and print NAME_LOOPS."""
    head = RUN_AREA.read_text(encoding="utf-8").split('\nROOT="$(pwd)"\n')[0]
    (tmp_path / "trailforge/golden").mkdir(parents=True, exist_ok=True)
    (tmp_path / "trailforge/golden/dk-areas.json").write_text(json.dumps({"areas": [registry_area]}), encoding="utf-8")
    probe = tmp_path / "probe.sh"
    probe.write_text(head + '\nprintf "%s" "$NAME_LOOPS"\n', encoding="utf-8")
    proc = subprocess.run(["/bin/bash", str(probe)], cwd=tmp_path, capture_output=True, text=True, env={
        **os.environ, "AREA_ID": area_id, "AREA_NAME": "x", "BBOX": "1,2,3,4", "RELATION_ID": "7",
        "WAY_ID": "", "W": str(tmp_path / "w")})
    assert proc.returncode == 0, proc.stderr
    return proc.stdout
def test_run_area_reads_the_opt_in_only_for_the_area_that_has_it(tmp_path):
    opted = _probe_name_loops("fano-dk", tmp_path / "a", {"id": "fano-dk", "rules": "dk-v3",
                                                           "name_unnamed_loops": [entry()]})
    assert json.loads(opted) == [entry()]
    other = _probe_name_loops("x-dk", tmp_path / "b", {"id": "x-dk", "rules": "dk-v3"})
    assert other == ""                                                   # absent key: no output, script never called
def test_run_area_calls_the_script_once_after_assemble_before_the_name_fixes_and_publish():
    text = RUN_AREA.read_text(encoding="utf-8")
    assert text.count("python3 ../scripts/dk_name_unnamed_loops.py") == 1
    call = text.index("python3 ../scripts/dk_name_unnamed_loops.py")
    assert text.index("assemble/assemble.py") < call < text.index("python3 ../scripts/dk_trail_name_fix.py") < text.index("serve/publish_areas.py")
    guard = text.rindex('if [ -n "$NAME_LOOPS" ]; then', 0, call)
    assert text.index("fi\n", call) > call > guard
    assert '--relations "$A/relation-members.aoi.json"' in text and "relation-members.aoi.json" in text[guard:call + 300]
# ----------------------------------------------------------------------- the CLI --
def _cli(tmp_path, entries, fc=None, rels=None):
    (tmp_path / "t.json").write_text(json.dumps(fc or trails()), encoding="utf-8")
    (tmp_path / "e.json").write_text(json.dumps(entries), encoding="utf-8")
    items = [{"relation_id": r, "tags": t} for r, t in (rels or {101: RED, 102: YELLOW}).items()]
    (tmp_path / "r.json").write_text(json.dumps({"relations": items}), encoding="utf-8")
    proc = subprocess.run([sys.executable, str(SCRIPT), "--trails", str(tmp_path / "t.json"), "--relations",
                           str(tmp_path / "r.json"), "--entries", str(tmp_path / "e.json"),
                           "--report", str(tmp_path / "rep.json")], capture_output=True, text=True)
    return proc
def test_cli_renames_writes_the_report_and_exits_zero(tmp_path):
    proc = _cli(tmp_path, [entry()])
    assert proc.returncode == 0, proc.stderr
    fc = json.loads((tmp_path / "t.json").read_text(encoding="utf-8"))
    assert fc["features"][0]["properties"]["name"] == "Pælebjerg Rød rute"
    report = json.loads((tmp_path / "rep.json").read_text(encoding="utf-8"))
    assert report["named"][0]["source"] == SRC and report["named"][0]["relation"] == "r101"
@pytest.mark.parametrize("entries", [
    [{k: v for k, v in entry().items() if k != "source"}],            # no source URL
    [{**entry(), "source": "https://www.komoot.com/de-de/guide/207606"}],
    [entry("r999")], [entry(km=(5, 6))],
])
def test_cli_fails_closed_with_exit_2_and_leaves_the_file_untouched(tmp_path, entries):
    before = json.dumps(trails())
    proc = _cli(tmp_path, entries)
    assert proc.returncode == 2 and "SAFETY BLOCK: name_unnamed_loops" in proc.stderr
    assert (tmp_path / "t.json").read_text(encoding="utf-8") == before
    assert not (tmp_path / "rep.json").exists()
def test_cli_fails_closed_on_unreadable_inputs(tmp_path):
    proc = subprocess.run([sys.executable, str(SCRIPT), "--trails", str(tmp_path / "none.json"), "--relations",
                           str(tmp_path / "none.json"), "--entries", str(tmp_path / "none.json"),
                           "--report", str(tmp_path / "rep.json")], capture_output=True, text=True)
    assert proc.returncode == 2 and "SAFETY BLOCK" in proc.stderr
