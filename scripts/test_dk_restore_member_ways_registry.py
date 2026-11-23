"""Registry, run-area wiring and live pins for the Denmark restore_member_ways opt-in.
The assembler behavior is tested in trailforge/assemble/test_dk_restore_member_ways.py.
This file covers the country lane: the registry loader, scripts/dk-run-area.sh and
the reviewed entries in trailforge/golden/dk-areas.json, each with a source
mutation that must change the outcome."""
import json
import os
import subprocess
import sys
import types
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parent))
import dk_publish as dk  # noqa: E402
ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "trailforge/golden/dk-areas.json"
RUN_AREA = ROOT / "scripts/dk-run-area.sh"
GOOD = {"relation": "r12787516", "max_restored_m": 300,
        "reason": "reviewed: signed walk"}
# Reviewed opt-ins: area id -> relation ids (ENGINE-2, 2026-11-22).
EXPECTED = {
    "aarup-dk": ["r4031300"],
    "almind-so-dk": ["r12563795"],
    "fejo-dk": ["r12946644"],
    "grastenskovene-dk": ["r1853406"],
    "grenen-dk": ["r12554769", "r12554700"],
    "gribskov-dk": ["r12559901"],
    "hald-so-dk": ["r19703948", "r10868051"],
    "hammeren-dk": ["r14573576"],
    "harte-skov-dk": ["r961913"],
    "horning-dk": ["r21040378"],
    "nysted-dk": ["r14775897"],
    "ornhoj-dk": ["r12884946"],
    "pamhule-skov-dk": ["r2069076"],
    "samso-dk": ["r19937141"],
    "sindal-dk": ["r14371913"],
    "sonderskov-slaaenso-dk": ["r14910479"],
    "staurby-skov-dk": ["r13743192", "r13743990"],
    "store-hareskov-dk": ["r8428109"],
    "svinklov-klitplantage-dk": ["r19358585"],
    "tonder-dk": ["r7802913"],
    "tondermarsken-dk": ["r10015736"],
    "vester-vedsted-dk": ["r14654257"],
}
# accept_gap entries the restore made unneeded (the gap was the dropped members;
# an unneeded accept_gap fails the area closed), withdrawn in the same change.
WITHDRAWN_ACCEPT_GAP = {
    "hald-so-dk": "r19703948", "hammeren-dk": "r14573576",
    "samso-dk": "r19937141",
}
def _registry_with(tmp_path, mutate, name="r.json"):
    doc = json.loads(REGISTRY.read_text(encoding="utf-8"))
    mutate(doc)
    path = tmp_path / name
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path
def _plain(doc, **fields):
    """Area 1 without curated lists (those are rule-set bound), then the fields."""
    doc["areas"][1].pop("exclude_trails", None)
    doc["areas"][1].pop("include_trails", None)
    doc["areas"][1].update(fields)
def test_registry_accepts_the_opt_in_only_on_dk_v3(tmp_path):
    path = _registry_with(
        tmp_path, lambda d: _plain(d, rules="dk-v3", restore_member_ways=[GOOD]))
    assert dk.load_registry(path)["areas"][1]["restore_member_ways"] == [GOOD]
    for rules, index in (("dk-v2", 1), ("legacy", 0)):
        with pytest.raises(dk.DkError, match="requires rules dk-v3"):
            dk.load_registry(_registry_with(
                tmp_path,
                lambda d, rules=rules, index=index: (
                    d["areas"][index].pop("exclude_trails", None),
                    d["areas"][index].update(
                        rules=rules, restore_member_ways=[GOOD])),
                name=f"{rules}.json"))
@pytest.mark.parametrize("value", [
    [], None, "r12787516", {}, [None], [GOOD, GOOD],
    [{"relation": "r12787516", "reason": "x"}],
    [{"relation": "r12787516", "max_restored_m": 5}],
    [{"max_restored_m": 5, "reason": "x"}],
    [{**GOOD, "relation": "12787516"}], [{**GOOD, "relation": "w12787516"}],
    [{**GOOD, "reason": " "}], [{**GOOD, "extra": 1}], [{**GOOD, "reason": 5}],
    [{**GOOD, "max_restored_m": 0}], [{**GOOD, "max_restored_m": -1}],
    [{**GOOD, "max_restored_m": 3001}], [{**GOOD, "max_restored_m": "300"}],
    [{**GOOD, "max_restored_m": True}], [{**GOOD, "max_restored_m": None}],
])
def test_registry_rejects_bad_opt_ins(tmp_path, value):
    with pytest.raises(dk.DkError, match="restore_member_ways"):
        dk.load_registry(_registry_with(
            tmp_path,
            lambda d: _plain(d, rules="dk-v3", restore_member_ways=value)))
def test_registry_without_the_key_loads_as_before(tmp_path):
    reg = dk.load_registry(_registry_with(tmp_path, lambda d: None))
    assert all("restore_member_ways" not in a or a["id"] in EXPECTED
               for a in reg["areas"])
def test_live_registry_carries_exactly_the_reviewed_opt_ins():
    doc = json.loads(REGISTRY.read_text(encoding="utf-8"))
    found = {a["id"]: [e["relation"] for e in a["restore_member_ways"]]
             for a in doc["areas"] if "restore_member_ways" in a}
    assert found == EXPECTED
    assert sum(len(v) for v in EXPECTED.values()) == 25
    for area in doc["areas"]:
        for entry in area.get("restore_member_ways", []):
            assert set(entry) == {"relation", "max_restored_m", "reason"}
            assert entry["reason"].strip()
            assert 0 < entry["max_restored_m"] <= 3000
        if area["id"] in EXPECTED:
            assert area["rules"] == "dk-v3"
def test_every_cap_is_a_reviewed_number_above_the_measured_need():
    # The caps were set from the replay: the measured restored length, 25 % more,
    # rounded up to 50 m (minimum 100 m; Troldtoft-ruten sits at the 3000 m hard
    # cap). The share limit (25 % of the route) is enforced by the assembler.
    doc = json.loads(REGISTRY.read_text(encoding="utf-8"))
    caps = {e["relation"]: e["max_restored_m"] for a in doc["areas"]
            for e in a.get("restore_member_ways", [])}
    assert caps == {
        "r4031300": 500, "r12563795": 250, "r12946644": 450, "r1853406": 150,
        "r12554769": 250, "r12554700": 250, "r12559901": 250,
        "r19703948": 1000, "r10868051": 150, "r14573576": 400, "r961913": 200,
        "r21040378": 700, "r14775897": 300, "r12884946": 3000, "r2069076": 100,
        "r19937141": 450, "r14371913": 200, "r14910479": 100,
        "r13743192": 650, "r13743990": 650, "r8428109": 100, "r19358585": 100,
        "r7802913": 100, "r10015736": 1050, "r14654257": 300,
    }
def test_the_opt_in_does_not_change_any_holds():
    # Every opted-in area is a live area (no hold); the opt-in changes how a
    # route is assembled, never whether an area ships.
    doc = json.loads(REGISTRY.read_text(encoding="utf-8"))
    holds = {a["id"]: a.get("hold") for a in doc["areas"] if a["id"] in EXPECTED}
    assert holds == {area_id: None for area_id in EXPECTED}
def test_withdrawn_accept_gaps_are_gone_and_the_others_stay():
    doc = json.loads(REGISTRY.read_text(encoding="utf-8"))
    by_id = {a["id"]: a for a in doc["areas"]}
    for area_id, relation in WITHDRAWN_ACCEPT_GAP.items():
        gaps = [g["relation"] for g in by_id[area_id].get("accept_gap", [])]
        assert relation not in gaps, area_id
        assert relation in [e["relation"]
                            for e in by_id[area_id]["restore_member_ways"]]
    # Hald Sø Rød keeps its real 202 m source gap.
    assert [g["relation"] for g in by_id["hald-so-dk"]["accept_gap"]] == [
        "r10876676"]
    # Orø Tur 4 is deliberately NOT opted in: restoring its driveway makes it
    # 3.79 mi, longer than Orø Tur 3 (3.76 mi), and the app's trail ids derive
    # from a longest-first order, so the ids tur-3 and tur-4-x would swap.
    assert "restore_member_ways" not in by_id["oro-dk"]
    assert [g["relation"] for g in by_id["oro-dk"]["accept_gap"]] == ["r3435156"]
def test_no_relation_is_in_both_motor_and_restore_lists():
    doc = json.loads(REGISTRY.read_text(encoding="utf-8"))
    for area in doc["areas"]:
        motor = {e["relation"] for e in area.get("keep_motor_tracks", [])}
        restore = {e["relation"] for e in area.get("restore_member_ways", [])}
        assert not motor & restore, area["id"]
def _exclude_args(area_id, tmp_path, registry=REGISTRY):
    """Run the real dk-run-area.sh up to the assemble arguments and print them."""
    text = RUN_AREA.read_text(encoding="utf-8")
    head = text[:text.index('ROOT="$(pwd)"')]
    probe = head + 'printf "%s\\n" ${EXCLUDE_ARGS[@]+"${EXCLUDE_ARGS[@]}"}\n'
    work = tmp_path / f"w-{area_id}"
    work.mkdir()
    (work / "trailforge" / "golden").mkdir(parents=True)
    (work / "trailforge" / "golden" / "dk-areas.json").write_text(
        Path(registry).read_text(encoding="utf-8"), encoding="utf-8")
    script = work / "probe.sh"
    script.write_text(probe, encoding="utf-8")
    env = {**os.environ, "AREA_ID": area_id, "AREA_NAME": "X",
           "BBOX": "9.0,56.0,9.1,56.1", "RELATION_ID": "1", "W": str(work / "w")}
    env.pop("WAY_ID", None)
    result = subprocess.run(["/bin/bash", str(script)], cwd=work, env=env,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout.split("\n"), work
def test_run_area_passes_the_flag_only_for_opted_in_areas(tmp_path):
    doc = json.loads(REGISTRY.read_text(encoding="utf-8"))
    by_id = {a["id"]: a for a in doc["areas"]}
    for area_id in EXPECTED:
        args, work = _exclude_args(area_id, tmp_path)
        assert args.count("--dk-restore-member-ways") == 1, area_id
        file = Path(args[args.index("--dk-restore-member-ways") + 1])
        assert file.name == "dk-restore-member-ways.json"
        assert json.loads(file.read_text()) == by_id[area_id][
            "restore_member_ways"], area_id
    # Every other registry area: no flag, no file (byte-identical run).
    others = [a["id"] for a in doc["areas"] if a["id"] not in EXPECTED]
    assert len(others) > 100
    for area_id in others[:: max(1, len(others) // 12)] + ["oro-dk"]:
        args, work = _exclude_args(area_id, tmp_path)
        assert "--dk-restore-member-ways" not in args, area_id
        assert not (work / "w" / area_id / "dk-restore-member-ways.json").exists()
def test_run_area_script_text_wires_the_opt_in():
    script = RUN_AREA.read_text(encoding="utf-8")
    assert ".restore_member_ways // []" in script
    assert '"$RESTORE_WAYS_JSON" != "[]"' in script
    assert "--dk-restore-member-ways" in script
def test_other_lanes_never_mention_the_opt_in():
    for relpath in ("scripts/ca-run-area.sh", "scripts/ca_publish.py",
                    "trailforge/golden/ca-areas.json",
                    "trailforge/serve/publish_areas.py",
                    "trailforge/extract/prefilter.sh",
                    "trailforge/serve/to_app_json.py"):
        assert "restore_member_ways" not in (ROOT / relpath).read_text(
            encoding="utf-8"), relpath
# ----------------------------------------------------------- source mutation --
SRC = (ROOT / "scripts" / "dk_publish.py").read_text(encoding="utf-8")
def _load_mutant(source):
    mod = types.ModuleType("dk_restore_mutant")
    mod.__file__ = str(ROOT / "scripts" / "dk_publish.py")
    sys.modules["dk_restore_mutant"] = mod
    try:
        exec(compile(source, mod.__file__, "exec"), mod.__dict__)
    finally:
        del sys.modules["dk_restore_mutant"]
    return mod
def _loader_table(mod, tmp_path):
    out = []
    tmp_path.mkdir(parents=True, exist_ok=True)
    cases = [
        ("dk-v3", [GOOD]), ("dk-v3", []), ("dk-v3", None), ("dk-v3", [GOOD, GOOD]),
        ("dk-v3", [{**GOOD, "relation": "12787516"}]),
        ("dk-v3", [{**GOOD, "reason": " "}]), ("dk-v3", [{**GOOD, "x": 1}]),
        ("dk-v3", [{**GOOD, "max_restored_m": 9000}]),
        ("dk-v2", [GOOD]),
    ]
    for n, (rules, value) in enumerate(cases):
        def mutate(doc, rules=rules, value=value):
            doc["areas"][1].pop("exclude_trails", None)
            doc["areas"][1].pop("include_trails", None)
            doc["areas"][1].update(rules=rules, restore_member_ways=value)
        path = _registry_with(tmp_path, mutate, name=f"m{n}.json")
        try:
            mod.load_registry(path)
            out.append("ok")
        except mod.DkError as error:
            out.append(str(error))
    return tuple(out)
MUTANTS = (
    ("loader: dk-v3 only",
     '    if "restore_member_ways" in entry:\n        if entry.get("rules") != "dk-v3":\n            raise DkError(f"{aid}: restore_member_ways requires rules dk-v3")\n',
     '    if "restore_member_ways" in entry:\n'),
    ("loader: shape is validated",
     '_dk_quality().validate_dk_restore_member_ways(\n                entry["restore_member_ways"])',
     'None'),
    ("loader: errors surface as DkError",
     'raise DkError(\n                f"{aid}: restore_member_ways is invalid: {error}") from None',
     'pass'),
    ("loader: the key is checked at all",
     'if "restore_member_ways" in entry:\n        if entry.get("rules")',
     'if "restore_member_ways_" in entry:\n        if entry.get("rules")'),
)
def test_every_loader_mutant_changes_the_table(tmp_path):
    baseline = _loader_table(dk, tmp_path / "base")
    assert baseline[0] == "ok" and all(
        "restore_member_ways" in row for row in baseline[1:])
    assert _loader_table(_load_mutant(SRC), tmp_path / "same") == baseline
    survivors = []
    for n, (label, old, new) in enumerate(MUTANTS):
        assert SRC.count(old) == 1, label
        sub = tmp_path / f"mut{n}"
        sub.mkdir()
        try:
            changed = _loader_table(_load_mutant(SRC.replace(old, new)),
                                    sub) != baseline
        except Exception:
            changed = True
        if not changed:
            survivors.append(label)
    assert survivors == []
SCRIPT_MUTANTS = (
    ("script: flag is passed",
     '  EXCLUDE_ARGS+=(--dk-restore-member-ways "$A/dk-restore-member-ways.json")\n',
     '  :\n'),
    ("script: only when set",
     'if [ "$RESTORE_WAYS_JSON" != "[]" ]; then',
     'if true; then'),
    ("script: the registry key is read",
     '.restore_member_ways // []', '.restore_member_ways_x // []'),
    ("script: the list is the file content",
     "printf '%s\\n' \"$RESTORE_WAYS_JSON\" > \"$A/dk-restore-member-ways.json\"",
     "printf '%s\\n' '[]' > \"$A/dk-restore-member-ways.json\""),
)
def test_every_run_area_mutant_changes_the_probe(tmp_path, monkeypatch):
    source = RUN_AREA.read_text(encoding="utf-8")
    def table(text, sub):
        sub.mkdir()
        monkeypatch.setattr(sys.modules[__name__], "RUN_AREA",
                            _write(sub / "script.sh", text))
        rows = []
        for area_id in ("hald-so-dk", "grenen-dk",
                        "nationalpark-mols-bjerge-dk"):
            try:
                args, work = _exclude_args(area_id, sub)
                flagged = "--dk-restore-member-ways" in args
                content = None
                if flagged:
                    content = Path(args[args.index(
                        "--dk-restore-member-ways") + 1]).read_text()
                rows.append((flagged, content))
            except AssertionError as error:
                rows.append(("error", str(error)[:40]))
        return rows
    def _write(path, text):
        path.write_text(text, encoding="utf-8")
        return path
    baseline = table(source, tmp_path / "base")
    assert baseline[0][0] is True and baseline[1][0] is True
    assert baseline[2] == (False, None)
    survivors = []
    for n, (label, old, new) in enumerate(SCRIPT_MUTANTS):
        assert source.count(old) == 1, label
        if table(source.replace(old, new), tmp_path / f"m{n}") == baseline:
            survivors.append(label)
    assert survivors == []
