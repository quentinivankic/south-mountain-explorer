"""Source mutants for the Denmark name_unnamed_loops opt-in (M4 NAMING): every gate is removed or weakened in a
scratch copy of the source and the scenario table must change, so no gate is untested.
Covers dk_name_unnamed_loops.py (the naming gates and the CLI), dk_publish.py (the registry gate) and
dk-run-area.sh (the wiring)."""
import copy
import json
import subprocess
import sys
import types
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parent))
import dk_publish as dk  # noqa: E402
from test_dk_name_unnamed_loops import (  # noqa: E402
    RED, YELLOW, SRC as URL, ROOT, REGISTRY, RUN_AREA, entry, feature, trails, relations)
MOD_PATH = ROOT / "scripts" / "dk_name_unnamed_loops.py"
SRC = MOD_PATH.read_text(encoding="utf-8")
def _load(source, path, name):
    mod = types.ModuleType(name)
    mod.__file__ = str(path)
    sys.modules[name] = mod
    try:
        exec(compile(source, str(path), "exec"), mod.__dict__)
    finally:
        del sys.modules[name]
    return mod
def _src(url):
    return {**entry(), "source": url}
def _no(key):
    return {k: v for k, v in entry().items() if k != key}
def _rel(**tags):
    return {**relations(), 101: {**RED, **tags}}
def _fc(mut):
    fc = trails()
    mut(fc["features"])
    return fc
def _len(mi):
    return _fc(lambda f: f[0]["properties"].update(length_mi=mi))
SCENARIOS = [
    ("base", {}), ("two", {"entries": [entry(), entry("r102", "Pælebjerg Gul rute")]}),
    ("www", {"entries": [_src("https://www.visitdenmark.com/denmark/plan-your-trip/x")]}),
    ("no source", {"entries": [_no("source")]}), ("no km", {"entries": [_no("listed_km")]}),
    ("extra key", {"entries": [{**entry(), "extra": 1}]}), ("empty source", {"entries": [_src("")]}),
    ("empty reason", {"entries": [{**entry(), "reason": " "}]}), ("http", {"entries": [_src("http://naturstyrelsen.dk/x")]}),
    ("blog", {"entries": [_src("https://esmark.de/wanderweg-fano/")]}),
    ("alltrails", {"entries": [_src("https://www.alltrails.com/trail/denmark/x")]}),
    ("suffix lookalike", {"entries": [_src("https://naturstyrelsen.dk.evil.example/x")]}),
    ("prefix lookalike", {"entries": [_src("https://evilnaturstyrelsen.dk/x")]}),
    ("root page", {"entries": [_src("https://naturstyrelsen.dk/")]}), ("no path", {"entries": [_src("https://naturstyrelsen.dk")]}),
    ("userinfo", {"entries": [_src("https://user:pw@naturstyrelsen.dk/x")]}),
    ("port", {"entries": [_src("https://naturstyrelsen.dk:8443/x")]}),
    ("space", {"entries": [_src("https://naturstyrelsen.dk/a b")]}),
    ("bad relation", {"entries": [{**entry(), "relation": "101"}]}), ("r0", {"entries": [{**entry(), "relation": "r0"}]}),
    ("no colour", {"entries": [entry(name="Pælebjerg")]}), ("short place", {"entries": [entry(name="X Rød rute")]}),
    ("lower colour", {"entries": [entry(name="Pælebjerg rød rute")]}),
    ("glued place", {"entries": [entry(name="PælebjergRød rute")]}),
    ("padded name", {"entries": [entry(name="Pælebjerg Rød rute ")]}),
    ("non-NFC name", {"entries": [entry(name="Pe\u0301lebjerg Rød rute")]}),
    ("control char", {"entries": [entry(name="Pælebjerg\u0007 Rød rute")]}),
    ("long name", {"entries": [entry(name="P" * 120 + " Rød rute")]}),
    ("km reversed", {"entries": [entry(km=(2, 1))]}), ("km zero", {"entries": [entry(km=(0, 1))]}),
    ("km huge", {"entries": [entry(km=(1, 61))]}), ("km one value", {"entries": [{**entry(), "listed_km": [1]}]}),
    ("km text", {"entries": [{**entry(), "listed_km": ["1", 2]}]}), ("km bool", {"entries": [{**entry(), "listed_km": [True, 2]}]}),
    ("km scalar", {"entries": [{**entry(), "listed_km": 2}]}),
    ("dup relation", {"entries": [entry(), entry()]}),
    ("dup name", {"entries": [entry(), entry("r102", "pælebjerg rød rute")]}),
    ("empty list", {"entries": []}), ("not list", {"entries": "x"}),
    ("no features", {"fc": {"type": "FeatureCollection"}}),
    ("relation missing", {"rels": {102: YELLOW}}), ("named", {"rels": _rel(name="x")}), ("name:da", {"rels": _rel(**{"name:da": "x"})}),
    ("official_name", {"rels": _rel(official_name="x")}), ("alt_name", {"rels": _rel(alt_name="x")}),
    ("no symbol", {"rels": _rel(**{"osmc:symbol": " "})}), ("no loop", {"rels": _rel(roundtrip="no")}),
    ("bicycle", {"rels": _rel(route="bicycle")}), ("not route", {"rels": _rel(type="multipolygon")}),
    ("wrong colour", {"rels": _rel(**{"osmc:symbol": "red:brown:yellow_dot"})}),
    ("unknown colour", {"rels": _rel(**{"osmc:symbol": "red:brown:cyan_dot"})}),
    ("way colour only", {"rels": _rel(**{"osmc:symbol": "red:white"})}),
    ("foreground wins", {"rels": _rel(**{"osmc:symbol": "red:white:blue_bar"})}),
    ("yellow loop", {"entries": [entry("r102", "Pælebjerg Gul rute")]}),
    ("no match", {"fc": _fc(lambda f: f.pop(0))}),
    ("two matches", {"fc": _fc(lambda f: f.append(feature(101, "Twin", 1.1)))}),
    ("merged", {"fc": _fc(lambda f: f[0]["properties"].update(root_relation_ids=[101, 105]))}),
    ("stitched", {"fc": _fc(lambda f: f[0]["properties"].update(source="name-stitch"))}),
    ("too short", {"fc": _len(0.46)}), ("short edge", {"fc": _len(0.47)}), ("too long", {"fc": _len(1.56)}),
    ("long edge", {"fc": _len(1.55)}), ("stub", {"fc": _fc(lambda f: f[1]["properties"].update(length_mi=0.486)),
                                        "entries": [entry("r102", "Fuglekøje Gul rute", (5.5, 5.5))]}),
    ("clash", {"fc": _fc(lambda f: f[2]["properties"].update(name="Pælebjerg Rød rute"))}),
    ("clash case", {"fc": _fc(lambda f: f[2]["properties"].update(name="PÆLEBJERG RØD RUTE"))}),
    ("atomic", {"entries": [entry(), entry("r102", "Pælebjerg Gul rute")],
                "fc": _fc(lambda f: f[1]["properties"].update(length_mi=9.0))}),
]
def table(mod):
    rows = []
    for label, kw in SCENARIOS:
        fc = copy.deepcopy(kw.get("fc") or trails())
        try:
            mod.apply_entries(fc, copy.deepcopy(kw.get("rels") or relations()), copy.deepcopy(kw.get("entries", [entry()])))
            out = "ok"
        except mod.NamingError as error:
            out = "refused: " + str(error)[:40]
        except Exception as error:  # noqa: BLE001 - a crash is a different outcome from a refusal
            out = "crash: " + type(error).__name__
        rows.append((label, out, tuple(f["properties"].get("name") for f in fc.get("features", []))))
    return rows
def _cli_table(mod, tmp_path):
    rows = []
    for n, (entries, rels) in enumerate([([entry()], None), ([_no("source")], None), ([entry("r999")], None)]):
        d = tmp_path / str(n)
        d.mkdir(parents=True)
        (d / "t.json").write_text(json.dumps(trails()), encoding="utf-8")
        (d / "e.json").write_text(json.dumps(entries), encoding="utf-8")
        (d / "r.json").write_text(json.dumps({"relations": [{"relation_id": 101, "tags": RED}]}), encoding="utf-8")
        code = mod.main(["--trails", str(d / "t.json"), "--relations", str(d / "r.json"), "--entries", str(d / "e.json"),
                         "--report", str(d / "rep.json")])
        rows.append((code, (d / "t.json").read_text(encoding="utf-8"), (d / "rep.json").exists()))
    return rows
def _mutant(old, new):
    assert SRC.count(old) == 1, old
    return _load(SRC.replace(old, new), MOD_PATH, "dk_name_unnamed_mutant")
MUTANTS = (
    ("non-empty list required", "if not isinstance(entries, list) or not entries:", "if False:"),
    ("exact key set", "set(item) != ENTRY_KEYS", "False"),
    ("non-empty text fields", "if not isinstance(item[key], str) or not item[key].strip():", "if False:"),
    ("relation shape", 'if not re.fullmatch(r"r[1-9][0-9]*", rel):', "if False:"),
    ("name trimmed", "name != name.strip()", "False"),
    ("name NFC", 'name != unicodedata.normalize("NFC", name)', "False"),
    ("name length", "len(name) > MAX_NAME_LEN", "False"),
    ("name control chars", 'any(unicodedata.category(c).startswith("C") for c in name)', "False"),
    ("colour word required", "word is None or", "False or"),
    ("place required", '< 2:\n            raise NamingError(f"{where} {rel}: name must be', '< 0:\n            raise NamingError(f"{where} {rel}: name must be'),
    ("colour word glued", 'name.endswith(f" {word} rute")', 'name.endswith(f"{word} rute")'),
    ("https only", 'parts.scheme != "https"', "False"),
    ("no whitespace in URL", "any(c.isspace() for c in url)", "False"),
    ("no userinfo", "parts.username or parts.password", "False"),
    ("default port only", "port not in (None, 443)", "False"),
    ("page not root", 'parts.path in ("", "/")', "False"),
    ("agency host only", "not _agency_host(host)", "False"),
    ("subdomain boundary", 'host.endswith((".naturstyrelsen.dk", ".nst.dk"))', 'host.endswith(("naturstyrelsen.dk", "nst.dk"))'),
    ("www allowed", 'host = host.removeprefix("www.")', "host = host"),
    ("listed_km is a pair", "not isinstance(km, list) or len(km) != 2", "False"),
    ("listed_km numbers", "not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in km)", "False"),
    ("listed_km order and cap", "not 0 < km[0] <= km[1] <= MAX_LISTED_KM", "False"),
    ("no duplicates", "if rel in seen_rel or name.casefold() in seen_name:", "if False:"),
    ("route relation", 'tags.get("type") != "route" or tags.get("route") not in ("hiking", "foot")', "False"),
    ("unnamed: name", 'k == "name" or', "False or"),
    ("unnamed: name:*", 'k.startswith("name:") or', "False or"),
    ("unnamed: other names", "k in _NAME_KEYS", "False"),
    ("signed", 'not (tags.get("osmc:symbol") or "").strip()', "False"),
    ("loop", 'tags.get("roundtrip") != "yes"', "False"),
    ("colour match", "colour is None or colour_word_for(name) != COLOURS[colour]", "False"),
    ("foreground colour first", "token = parts[2] if len(parts) >= 3 and parts[2] else parts[0]", "token = parts[0]"),
    ("way colour fallback", "token = parts[2] if len(parts) >= 3 and parts[2] else parts[0]", "token = parts[2]"),
    ("entries validated again", "    entries = validate_entries(entries)\n    features", "    features"),
    ("features list", "if not isinstance(features, list):", "if False:"),
    ("relation in authority", "if rid not in relations:", "if False:"),
    ("relation source only", '.get("source") == "relation"', '.get("source") != ""'),
    ("root relation id", '.get("root_relation_ids") == [rid]', '.get("root_relation_ids") != []'),
    ("exactly one trail", "if len(hits) != 1:", "if len(hits) < 1:"),
    ("lower length bound", "(1 - LENGTH_TOLERANCE)", "0"),
    ("upper length bound", "(1 + LENGTH_TOLERANCE)", "1000"),
    ("tolerance value", "LENGTH_TOLERANCE = 0.25", "LENGTH_TOLERANCE = 0.5"),
    ("km per mile", "KM_PER_MI = 1.609344", "KM_PER_MI = 1.0"),
    ("name clash", 'if f is not g and final.casefold() == entry["name"].casefold():', "if False:"),
    ("name clash ignores case", 'final.casefold() == entry["name"].casefold()', 'final == entry["name"]'),
    ("rename applied", 'props["name"] = entry["name"]', "pass"),
    ("only the listed loop", 'props["name"] = entry["name"]',
     '[x["properties"].__setitem__("name", entry["name"]) for x in features if not x["properties"].get("name")]'),
    ("renames only after every gate", "targets.append(hits[0])",
     'targets.append(hits[0]); hits[0]["properties"]["name"] = entry["name"]'),
)
def test_every_naming_gate_mutant_changes_the_scenario_table():
    baseline = table(_load(SRC, MOD_PATH, "dk_name_unnamed_same"))
    assert dict((l, o) for l, o, _ in baseline)["base"] == "ok"
    assert sum(o == "ok" for _, o, _ in baseline) == 7 and sum(o.startswith("refused") for _, o, _ in baseline) >= 40
    assert not any(o.startswith("crash") for _, o, _ in baseline)
    survivors = []
    for label, old, new in MUTANTS:
        try:
            changed = table(_mutant(old, new)) != baseline
        except Exception:  # noqa: BLE001
            changed = True
        if not changed:
            survivors.append(label)
    assert survivors == []
CLI_MUTANTS = (
    ("cli: exit 2 on a refusal", "    return 2\n", "    return 0\n"),
    ("cli: the trails file is rewritten", 'args.trails.write_text(json.dumps(fc), encoding="utf-8")', "pass"),
    ("cli: the report is written", "args.report.write_text(", "args.report.with_suffix('.x').write_text("),
    ("cli: entries come from the file", 'json.loads(args.entries.read_text(encoding="utf-8"))', "[]"),
    ("cli: relation tags come from the authority file", "load_relation_tags(json.loads(args.relations.read_text(encoding=\"utf-8\")))", "{}"),
)
def test_every_cli_mutant_changes_the_outcome(tmp_path):
    baseline = _cli_table(_load(SRC, MOD_PATH, "dk_name_unnamed_same2"), tmp_path / "base")
    assert [r[0] for r in baseline] == [0, 2, 2] and baseline[0][2] and not baseline[1][2]
    survivors = []
    for n, (label, old, new) in enumerate(CLI_MUTANTS):
        sub = tmp_path / f"m{n}"
        sub.mkdir()
        try:
            changed = _cli_table(_mutant(old, new), sub) != baseline
        except Exception:  # noqa: BLE001
            changed = True
        if not changed:
            survivors.append(label)
    assert survivors == []
# ------------------------------------------------------------------ registry loader --
DK_SRC = (ROOT / "scripts" / "dk_publish.py").read_text(encoding="utf-8")
def _loader_table(mod, tmp_path):
    out = []
    tmp_path.mkdir(parents=True, exist_ok=True)
    cases = [("dk-v3", [entry()]), ("dk-v3", []), ("dk-v3", None), ("dk-v3", [entry(), entry()]),
             ("dk-v3", [_no("source")]), ("dk-v3", [_src("https://www.alltrails.com/trail/denmark/x")]),
             ("dk-v3", [entry(name="Pælebjerg")]), ("dk-v2", [entry()]), ("legacy", [entry()])]
    for n, (rules, value) in enumerate(cases):
        doc = json.loads(REGISTRY.read_text(encoding="utf-8"))
        aid = "nationalpark-mols-bjerge-dk" if rules == "legacy" else "langeland-dk"
        next(a for a in doc["areas"] if a["id"] == aid).update(rules=rules, name_unnamed_loops=value)
        path = tmp_path / f"m{n}.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        try:
            mod.load_registry(path)
            out.append("ok")
        except mod.DkError as error:
            out.append(str(error))
    return tuple(out)
LOADER_MUTANTS = (
    ("loader: dk-v3 only", 'if "name_unnamed_loops" in entry:\n            if entry.get("rules") != "dk-v3":',
     'if "name_unnamed_loops" in entry:\n            if False:'),
    ("loader: entries validated",
     'dk_name_unnamed_loops.validate_entries(entry["name_unnamed_loops"], f"{aid}: name_unnamed_loops")', "None"),
    ("loader: refusals surface as DkError",
     "except dk_name_unnamed_loops.NamingError as error:\n                raise DkError(str(error)) from None",
     "except dk_name_unnamed_loops.NamingError as error:\n                pass"),
    ("loader: the key is checked at all", 'if "name_unnamed_loops" in entry:', 'if "name_unnamed_loops_" in entry:'),
)
def test_every_loader_mutant_changes_the_table(tmp_path):
    def load(source):
        return _load(source, ROOT / "scripts" / "dk_publish.py", "dk_publish_mutant")
    baseline = _loader_table(dk, tmp_path / "base")
    assert baseline[0] == "ok" and all("name_unnamed_loops" in row for row in baseline[1:]), baseline
    assert _loader_table(load(DK_SRC), tmp_path / "same") == baseline
    survivors = []
    for n, (label, old, new) in enumerate(LOADER_MUTANTS):
        assert DK_SRC.count(old) == 1, label
        sub = tmp_path / f"mut{n}"
        sub.mkdir()
        try:
            changed = _loader_table(load(DK_SRC.replace(old, new)), sub) != baseline
        except Exception:  # noqa: BLE001
            changed = True
        if not changed:
            survivors.append(label)
    assert survivors == []
# ------------------------------------------------------------------- run-area wiring --
RUN_SRC = RUN_AREA.read_text(encoding="utf-8")
def _block(text):
    call = text.index("python3 ../scripts/dk_name_unnamed_loops.py")
    start = text.rindex("\nif ", 0, call) + 1
    return text[start:text.index("\nfi\n", call) + 4]
def _run_block(text, tmp_path, name_loops):
    """Run the NAME_LOOPS block of the script with a recording python3 and print what it was called with."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    script = tmp_path / "block.sh"
    script.write_text('set -euo pipefail\npython3() { printf "CALL %s\\n" "$*"; }\n'
                      f'A="{tmp_path}/a"; mkdir -p "$A"; AREA_ID=fano-dk; NAME_LOOPS=\'{name_loops}\'\n' + _block(text)
                      + 'if [ -f "$A/dk-name-unnamed-loops.json" ]; then echo FILE; cat "$A/dk-name-unnamed-loops.json"; fi\n',
                      encoding="utf-8")
    proc = subprocess.run(["/bin/bash", str(script)], capture_output=True, text=True, cwd=tmp_path)
    return proc.returncode, proc.stdout.replace(str(tmp_path), "<T>")
def _jq_table(text, tmp_path):
    head = text.split('\nROOT="$(pwd)"\n')[0]
    rows = []
    for n, area in enumerate(({"id": "fano-dk", "rules": "dk-v3", "name_unnamed_loops": [entry()]},
                              {"id": "fano-dk", "rules": "dk-v3"})):
        d = tmp_path / f"j{n}"
        (d / "trailforge/golden").mkdir(parents=True)
        (d / "trailforge/golden/dk-areas.json").write_text(json.dumps({"areas": [area]}), encoding="utf-8")
        (d / "p.sh").write_text(head + '\nprintf "%s" "$NAME_LOOPS"\n', encoding="utf-8")
        import os
        proc = subprocess.run(["/bin/bash", str(d / "p.sh")], cwd=d, capture_output=True, text=True, env={
            **os.environ, "AREA_ID": "fano-dk", "AREA_NAME": "x", "BBOX": "1,2,3,4", "RELATION_ID": "7",
            "WAY_ID": "", "W": str(d / "w")})
        rows.append((proc.returncode, proc.stdout))
    return rows
RUN_MUTANTS = (
    ("script: the registry key is read", ".name_unnamed_loops // empty", ".name_unnamed_loops_x // empty"),
    ("script: only when set", 'if [ -n "$NAME_LOOPS" ]; then', "if true; then"),
    ("script: the list is the file content", "printf '%s\\n' \"$NAME_LOOPS\" > \"$A/dk-name-unnamed-loops.json\"",
     "printf '%s\\n' '[]' > \"$A/dk-name-unnamed-loops.json\""),
    ("script: the relation authority is the aoi file", '--relations "$A/relation-members.aoi.json"',
     '--relations "$A/relation-members.raw.json"'),
    ("script: the entries file is passed", '--entries "$A/dk-name-unnamed-loops.json"', '--entries "$A/other.json"'),
    ("script: the trails file is rewritten", '--trails "data/aoi/$AREA_ID.trails.geojson" \\\n    --relations',
     '--trails "data/aoi/$AREA_ID.removed.geojson" \\\n    --relations'),
    ("script: the report is written", '--report "$A/name-unnamed-loops.json"', '--report "$A/x.json"'),
)
def test_every_run_area_mutant_changes_the_probes(tmp_path):
    entries = json.dumps([entry()])
    def probes(text, sub):
        return (_run_block(text, sub / "set", entries), _run_block(text, sub / "empty", ""), _jq_table(text, sub / "jq"))
    baseline = probes(RUN_SRC, tmp_path / "base")
    assert baseline[0][0] == 0 and "CALL ../scripts/dk_name_unnamed_loops.py --trails data/aoi/fano-dk.trails.geojson" in baseline[0][1]
    assert baseline[1] == (0, "") and json.loads(baseline[2][0][1]) == [entry()] and baseline[2][1][1] == ""
    survivors = []
    for n, (label, old, new) in enumerate(RUN_MUTANTS):
        assert RUN_SRC.count(old) == 1, label
        if probes(RUN_SRC.replace(old, new), tmp_path / f"m{n}") == baseline:
            survivors.append(label)
    assert survivors == []
