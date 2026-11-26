"""Registry, run-area wiring and live pins for the Denmark allow_classA_name opt-in
(per-relation exemption from the name rule of dk-non-hiking; class-A ruling 2026-11-24).
The assembler behavior is tested in trailforge/assemble/test_dk_classa_name.py."""
import json
import os
import subprocess
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parent))
import dk_publish as dk  # noqa: E402
ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "trailforge/golden/dk-areas.json"
RUN_AREA = ROOT / "scripts/dk-run-area.sh"
GOOD = {"relation": "r2174378", "reason": "reviewed: class-A signed løberute"}
# Reviewed opt-ins: area id -> relation ids (class-A ruling, set per shipped batch).
EXPECTED = {
    "vestskoven-dk": ["r2174378", "r2174418", "r2174419"],
    "varde-sondre-plantage-dk": ["r5989929"],
    "riis-skov-dk": ["r8351442"],
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


def _plain(doc, **fields):
    """Area 1 without curated lists (those are rule-set bound), then the fields."""
    doc["areas"][1].pop("exclude_trails", None)
    doc["areas"][1].pop("include_trails", None)
    doc["areas"][1].update(fields)
def test_registry_accepts_the_opt_in_only_on_dk_v3_with_the_route_included(tmp_path):
    ok = _registry_with(tmp_path, lambda d: _plain(
        d, rules="dk-v3", allow_classA_name=[GOOD],
        include_trails=[{"id": "r2174378", "name": "x", "reason": "y"}]))
    assert dk.load_registry(ok)["areas"][1]["allow_classA_name"] == [GOOD]
    with pytest.raises(dk.DkError, match="include_trails"):
        dk.load_registry(_registry_with(
            tmp_path, lambda d: _plain(d, rules="dk-v3", allow_classA_name=[GOOD]),
            name="noinc.json"))
    with pytest.raises(dk.DkError, match="requires rules dk-v3"):
        dk.load_registry(_registry_with(
            tmp_path, lambda d: (d["areas"][1].pop("exclude_trails", None),
                                 d["areas"][1].update(rules="dk-v2", allow_classA_name=[GOOD])),
            name="v2.json"))
@pytest.mark.parametrize("value", [
    [], None, "r1", {}, [None], [GOOD, GOOD],
    [{"relation": "r2174378"}], [{"reason": "x"}],
    [{**GOOD, "relation": "2174378"}], [{**GOOD, "reason": " "}], [{**GOOD, "extra": 1}],
])
def test_registry_rejects_bad_opt_ins(tmp_path, value):
    with pytest.raises(dk.DkError, match="allow_classA_name"):
        dk.load_registry(_registry_with(
            tmp_path, lambda d: _plain(d, rules="dk-v3", allow_classA_name=value)))
def test_live_registry_carries_exactly_the_reviewed_opt_ins():
    doc = json.loads(REGISTRY.read_text(encoding="utf-8"))
    found = {a["id"]: [e["relation"] for e in a["allow_classA_name"]]
             for a in doc["areas"] if "allow_classA_name" in a}
    assert found == EXPECTED
    for area in doc["areas"]:
        for entry in area.get("allow_classA_name", []):
            assert set(entry) == {"relation", "reason"} and entry["reason"].strip()
            assert area["rules"] == "dk-v3"
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
        assert args.count("--dk-allow-classa-name") == 1, area_id
        file = Path(args[args.index("--dk-allow-classa-name") + 1])
        assert file.name == "dk-allow-classa-name.json"
        assert json.loads(file.read_text()) == by_id[area_id][
            "allow_classA_name"], area_id
    # Every other registry area: no flag, no file (byte-identical run).
    others = [a["id"] for a in doc["areas"] if a["id"] not in EXPECTED]
    assert len(others) > 100
    for area_id in others[:: max(1, len(others) // 12)]:
        args, work = _exclude_args(area_id, tmp_path)
        assert "--dk-allow-classa-name" not in args, area_id
        assert not (work / "w" / area_id / "dk-allow-classa-name.json").exists()


def test_run_area_script_text_wires_the_opt_in():
    script = RUN_AREA.read_text(encoding="utf-8")
    assert ".allow_classA_name // []" in script
    assert '"$CLASSA_NAME_JSON" != "[]"' in script
    assert "--dk-allow-classa-name" in script


def test_other_lanes_never_mention_the_opt_in():
    for relpath in ("scripts/ca-run-area.sh", "scripts/ca_publish.py",
                    "trailforge/golden/ca-areas.json",
                    "trailforge/serve/publish_areas.py",
                    "trailforge/extract/prefilter.sh"):
        assert "allow_classA_name" not in (ROOT / relpath).read_text(
            encoding="utf-8"), relpath


# ----------------------------------------------------------- source mutation --
def _probe_with_script(tmp_path, source, name):
    global RUN_AREA
    real = RUN_AREA
    alt = tmp_path / name
    alt.write_text(source, encoding="utf-8")
    RUN_AREA = alt
    try:
        doc = json.loads(REGISTRY.read_text(encoding="utf-8"))
        reg = tmp_path / f"{name}.json"
        probe = doc["areas"][1]
        probe["allow_classA_name"] = [GOOD]
        reg.write_text(json.dumps(doc), encoding="utf-8")
        sub = tmp_path / name.replace(".", "_"); sub.mkdir()
        args, _ = _exclude_args(probe["id"], sub, registry=reg)
        return args
    finally:
        RUN_AREA = real
def test_run_area_mutants_change_the_probe(tmp_path):
    source = RUN_AREA.read_text(encoding="utf-8")
    base = _probe_with_script(tmp_path, source, "base.sh")
    assert "--dk-allow-classa-name" in base
    for n, (label, old, new) in enumerate((
            ("the registry key is read", ".allow_classA_name // []", ".allow_classA_name_x // []"),
            ("the empty guard", '"$CLASSA_NAME_JSON" != "[]"', '"$CLASSA_NAME_JSON" == "[]"'),
            ("the flag name", "--dk-allow-classa-name", "--dk-allow-classa-nameX"))):
        assert source.count(old) >= 1, label
        mutant = _probe_with_script(tmp_path, source.replace(old, new), f"m{n}.sh")
        assert mutant != base, label
def test_registry_check_mutants_change_the_loader(tmp_path):
    src = (ROOT / "scripts" / "dk_publish.py").read_text(encoding="utf-8")
    old = 'if item["relation"] not in included:'
    assert src.count(old) == 1
    import types
    def table(source):
        mod = types.ModuleType("dk_classa_mutant")
        mod.__file__ = str(ROOT / "scripts" / "dk_publish.py")
        sys.modules["dk_classa_mutant"] = mod
        try:
            exec(compile(source, mod.__file__, "exec"), mod.__dict__)
        finally:
            del sys.modules["dk_classa_mutant"]
        try:
            doc = json.loads(REGISTRY.read_text(encoding="utf-8"))
            _plain(doc, rules="dk-v3", allow_classA_name=[GOOD])
            path = tmp_path / "mut.json"
            path.write_text(json.dumps(doc), encoding="utf-8")
            mod.load_registry(path)
            return "accepted"
        except mod.DkError as error:
            return str(error)[:60]
    assert table(src) != table(src.replace(old, "if False:"))
