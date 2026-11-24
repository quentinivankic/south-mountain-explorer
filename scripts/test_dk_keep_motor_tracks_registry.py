"""Registry, run-area wiring and live pins for the Denmark keep_motor_tracks opt-in.

The assembler behavior is tested in trailforge/assemble/test_dk_motor_tracks.py.
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
GOOD = {"relation": "r12787516", "reason": "reviewed: signed walk"}

# Reviewed opt-ins: area id -> relation ids (ENGINE-1, 2026-11-22).
EXPECTED = {
    "endelave-dk": ["r12787516"],
    "laeso-klitplantage-dk": ["r12571926"],
    "varde-sondre-plantage-dk": ["r6153047", "r6153048", "r5989929"],   # + class-A Løberute
    "svinklov-klitplantage-dk": ["r10043412"],
    "bulbjerg-dk": ["r12612397", "r12671441"],   # dk-ship: new area
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
        tmp_path, lambda d: _plain(d, rules="dk-v3", keep_motor_tracks=[GOOD]))
    assert dk.load_registry(path)["areas"][1]["keep_motor_tracks"] == [GOOD]
    for rules, index in (("dk-v2", 1), ("legacy", 0)):
        with pytest.raises(dk.DkError, match="requires rules dk-v3"):
            dk.load_registry(_registry_with(
                tmp_path,
                lambda d, rules=rules, index=index: (
                    d["areas"][index].pop("exclude_trails", None),
                    d["areas"][index].update(
                        rules=rules, keep_motor_tracks=[GOOD])),
                name=f"{rules}.json"))


@pytest.mark.parametrize("value", [
    [], None, "r12787516", {}, [None], [GOOD, GOOD],
    [{"relation": "r12787516"}], [{"reason": "x"}],
    [{**GOOD, "relation": "12787516"}], [{**GOOD, "relation": "w12787516"}],
    [{**GOOD, "reason": " "}], [{**GOOD, "extra": 1}],
    [{**GOOD, "reason": 5}],
])
def test_registry_rejects_bad_opt_ins(tmp_path, value):
    with pytest.raises(dk.DkError, match="keep_motor_tracks"):
        dk.load_registry(_registry_with(
            tmp_path,
            lambda d: _plain(d, rules="dk-v3", keep_motor_tracks=value)))


def test_registry_without_the_key_loads_as_before(tmp_path):
    reg = dk.load_registry(_registry_with(tmp_path, lambda d: None))
    assert all("keep_motor_tracks" not in a or a["id"] in EXPECTED
               for a in reg["areas"])


def test_live_registry_carries_exactly_the_reviewed_opt_ins():
    doc = json.loads(REGISTRY.read_text(encoding="utf-8"))
    found = {a["id"]: [e["relation"] for e in a["keep_motor_tracks"]]
             for a in doc["areas"] if "keep_motor_tracks" in a}
    assert found == EXPECTED
    for area in doc["areas"]:
        for entry in area.get("keep_motor_tracks", []):
            assert set(entry) == {"relation", "reason"}
            assert entry["reason"].strip()
        if area["id"] in EXPECTED:
            assert area["rules"] == "dk-v3"


def test_the_opt_in_does_not_change_any_holds():
    doc = json.loads(REGISTRY.read_text(encoding="utf-8"))
    by_id = {a["id"]: a for a in doc["areas"]}
    # The opt-in unlocks the mechanism only; shipping was a separate decision (dk-ship, 2026-11-23, unheld
    # Endelave and Læsø Klitplantage and created Bulbjerg).
    assert by_id["endelave-dk"]["hold"] is None
    assert by_id["laeso-klitplantage-dk"]["hold"] is None
    assert by_id["bulbjerg-dk"]["hold"] is None
    assert by_id["varde-sondre-plantage-dk"]["hold"] is None
    assert by_id["svinklov-klitplantage-dk"]["hold"] is None


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
        assert args.count("--dk-keep-motor-tracks") == 1, area_id
        file = Path(args[args.index("--dk-keep-motor-tracks") + 1])
        assert file.name == "dk-keep-motor-tracks.json"
        assert json.loads(file.read_text()) == by_id[area_id][
            "keep_motor_tracks"], area_id
    # Every other registry area: no flag, no file (byte-identical run).
    others = [a["id"] for a in doc["areas"] if a["id"] not in EXPECTED]
    assert len(others) > 100
    for area_id in others[:: max(1, len(others) // 12)]:
        args, work = _exclude_args(area_id, tmp_path)
        assert "--dk-keep-motor-tracks" not in args, area_id
        assert not (work / "w" / area_id / "dk-keep-motor-tracks.json").exists()


def test_run_area_script_text_wires_the_opt_in():
    script = RUN_AREA.read_text(encoding="utf-8")
    assert ".keep_motor_tracks // []" in script
    assert '"$KEEP_MOTOR_JSON" != "[]"' in script
    assert "--dk-keep-motor-tracks" in script


def test_other_lanes_never_mention_the_opt_in():
    for relpath in ("scripts/ca-run-area.sh", "scripts/ca_publish.py",
                    "trailforge/golden/ca-areas.json",
                    "trailforge/serve/publish_areas.py",
                    "trailforge/extract/prefilter.sh"):
        assert "keep_motor_tracks" not in (ROOT / relpath).read_text(
            encoding="utf-8"), relpath


# ----------------------------------------------------------- source mutation --
SRC = (ROOT / "scripts" / "dk_publish.py").read_text(encoding="utf-8")


def _load_mutant(source):
    mod = types.ModuleType("dk_motor_mutant")
    mod.__file__ = str(ROOT / "scripts" / "dk_publish.py")
    sys.modules["dk_motor_mutant"] = mod
    try:
        exec(compile(source, mod.__file__, "exec"), mod.__dict__)
    finally:
        del sys.modules["dk_motor_mutant"]
    return mod


def _loader_table(mod, tmp_path):
    out = []
    tmp_path.mkdir(parents=True, exist_ok=True)
    cases = [
        ("dk-v3", [GOOD]), ("dk-v3", []), ("dk-v3", None), ("dk-v3", [GOOD, GOOD]),
        ("dk-v3", [{**GOOD, "relation": "12787516"}]),
        ("dk-v3", [{**GOOD, "reason": " "}]), ("dk-v3", [{**GOOD, "x": 1}]),
        ("dk-v2", [GOOD]),
    ]
    for n, (rules, value) in enumerate(cases):
        def mutate(doc, rules=rules, value=value):
            doc["areas"][1].pop("exclude_trails", None)
            doc["areas"][1].pop("include_trails", None)
            doc["areas"][1].update(rules=rules, keep_motor_tracks=value)
        path = _registry_with(tmp_path, mutate, name=f"m{n}.json")
        try:
            mod.load_registry(path)
            out.append("ok")
        except mod.DkError as error:
            out.append(str(error))
    return tuple(out)


MUTANTS = (
    ("loader: dk-v3 only",
     '    if "keep_motor_tracks" in entry:\n        if entry.get("rules") != "dk-v3":\n            raise DkError(f"{aid}: keep_motor_tracks requires rules dk-v3")\n',
     '    if "keep_motor_tracks" in entry:\n'),
    ("loader: shape is validated",
     '_dk_quality().validate_dk_keep_motor_tracks(entry["keep_motor_tracks"])',
     'None'),
    ("loader: errors surface as DkError",
     'raise DkError(f"{aid}: keep_motor_tracks is invalid: {error}") from None',
     'pass'),
    ("loader: the key is checked at all",
     'if "keep_motor_tracks" in entry:\n        if entry.get("rules")',
     'if "keep_motor_tracks_" in entry:\n        if entry.get("rules")'),
)


def test_every_loader_mutant_changes_the_table(tmp_path):
    baseline = _loader_table(dk, tmp_path / "base")
    assert baseline[0] == "ok" and all(
        "keep_motor_tracks" in row for row in baseline[1:])
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
     '  EXCLUDE_ARGS+=(--dk-keep-motor-tracks "$A/dk-keep-motor-tracks.json")\n',
     '  :\n'),
    ("script: only when set",
     'if [ "$KEEP_MOTOR_JSON" != "[]" ]; then',
     'if true; then'),
    ("script: the registry key is read",
     '.keep_motor_tracks // []', '.keep_motor_tracks_x // []'),
    ("script: the list is the file content",
     "printf '%s\\n' \"$KEEP_MOTOR_JSON\" > \"$A/dk-keep-motor-tracks.json\"",
     "printf '%s\\n' '[]' > \"$A/dk-keep-motor-tracks.json\""),
)


def test_every_run_area_mutant_changes_the_probe(tmp_path, monkeypatch):
    source = RUN_AREA.read_text(encoding="utf-8")

    def table(text, sub):
        sub.mkdir()
        monkeypatch.setattr(sys.modules[__name__], "RUN_AREA",
                            _write(sub / "script.sh", text))
        rows = []
        for area_id in ("varde-sondre-plantage-dk", "endelave-dk",
                        "nationalpark-mols-bjerge-dk"):
            try:
                args, work = _exclude_args(area_id, sub)
                flagged = "--dk-keep-motor-tracks" in args
                content = None
                if flagged:
                    content = Path(args[args.index(
                        "--dk-keep-motor-tracks") + 1]).read_text()
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
