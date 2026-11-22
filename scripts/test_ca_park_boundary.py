#!/usr/bin/env python3
"""Tests for the Canada park_boundary opt-in (scripts/ca_park_boundary.py, the registry validator in
ca_publish.py, the conditional step in ca-run-area.sh). Every gate has a mutation test: one valid
fixture, one change that breaks exactly that gate, and the refusal asserted. Controls show the
unflagged behavior is unchanged."""
import copy
import json
import re
import shutil
import subprocess
import sys
import types
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ca_park_boundary as pb  # noqa: E402
import ca_publish as ca  # noqa: E402
from test_ca_publish import _entry, _registry  # noqa: E402
from test_ca_run_area import _stub_run  # noqa: E402
ROOT = Path(__file__).resolve().parent.parent
SCRIPT = (ROOT / "scripts/ca-run-area.sh").read_text()
needs_osmium = pytest.mark.skipif(shutil.which("osmium") is None, reason="osmium CLI not installed")
OPT = {"reason": "Municipal park, no operator tag in OSM; the park polygon is the area boundary."}
BOX = (-63.0, 45.0, -62.8, 45.2)
# ------------------------------------------------------------------ fixtures --
def _nodes():
    w, s, e, n = BOX
    pts = [(1, s, w), (2, s, e), (3, n, e), (4, n, w), (5, 45.1, -62.9), (6, 45.12, -62.88)]
    return "".join(f'<node id="{i}" lat="{la}" lon="{lo}"/>' for i, la, lo in pts)
def _osm(body):
    return f'<?xml version="1.0"?><osm version="0.6">{_nodes()}{body}</osm>'
RING = '<nd ref="1"/><nd ref="2"/><nd ref="3"/><nd ref="4"/><nd ref="1"/>'
TRAIL = ('<way id="50"><nd ref="5"/><nd ref="6"/><tag k="highway" v="path"/><tag k="name" v="Lake Loop"/></way>')
def _rel(tags=None, rid=1000):
    t = {"type": "multipolygon", "leisure": "park", "name": "Area 0", **(tags or {})}
    tag_xml = "".join(f'<tag k="{k}" v="{v}"/>' for k, v in t.items() if v is not None)
    return (f'<way id="10">{RING}</way><relation id="{rid}"><member type="way" ref="10" role="outer"/>'
            f'{tag_xml}</relation>')
def _way(tags=None, closed=True):
    t = {"leisure": "park", "name": "Area 0", **(tags or {})}
    tag_xml = "".join(f'<tag k="{k}" v="{v}"/>' for k, v in t.items() if v is not None)
    nds = RING if closed else '<nd ref="1"/><nd ref="2"/><nd ref="3"/><nd ref="4"/>'
    return f'<way id="2000">{nds}{tag_xml}</way>'
def _setup(tmp_path, raw_body, aoi_body=TRAIL):
    (tmp_path / "raw.osm").write_text(_osm(raw_body + TRAIL), encoding="utf-8")
    (tmp_path / "aoi.osm").write_text(_osm(aoi_body), encoding="utf-8")
    return tmp_path / "raw.osm", tmp_path / "aoi.osm"
def _park_entry(**kw):
    return _entry(0, park_boundary=copy.deepcopy(OPT), **kw)
def _way_entry(**kw):
    e = _entry(0, bbox=[-63.0, 45.0, -62.8, 45.2], center=[45.1, -62.9], park_boundary=copy.deepcopy(OPT), **kw)
    del e["relation_id"]
    e["way_id"] = 2000
    return e
def _run(tmp_path, entry, raw_body, aoi_body=TRAIL):
    raw, aoi = _setup(tmp_path, raw_body, aoi_body)
    reg = _registry(tmp_path, [entry])
    report = tmp_path / "report.json"
    code = pb.main(["--registry", str(reg), "--area-id", entry["id"], "--raw-pbf", str(raw),
                    "--aoi-pbf", str(aoi), "--report", str(report)])
    return code, aoi, report
# ------------------------------------------------- gate 1: opt-in shape --
def test_validate_accepts_exactly_a_reason():
    assert pb.validate_park_boundary(OPT) == OPT
@pytest.mark.parametrize("bad", [None, [], "yes", True, {}, {"reason": ""}, {"reason": "  "}, {"reason": " padded "},
                                 {"reason": 3}, {"reason": "x", "extra": 1}, {"why": "x"}])
def test_validate_mutation_every_bad_shape_is_refused(bad):
    with pytest.raises(pb.ParkBoundaryError):
        pb.validate_park_boundary(bad)
# ------------------------------------- gate 2: registry loader (ca_publish) --
def test_registry_accepts_relation_and_way_entries(tmp_path):
    assert ca.load_registry(_registry(tmp_path, [_park_entry()]))
    assert ca.load_registry(_registry(tmp_path, [_way_entry()]))
@pytest.mark.parametrize("bad", [True, {}, {"reason": ""}, {"reason": "x", "extra": 1}])
def test_registry_mutation_bad_park_boundary_is_refused(tmp_path, bad):
    with pytest.raises(ca.CaError, match="park_boundary"):
        ca.load_registry(_registry(tmp_path, [_entry(0, park_boundary=bad)]))
def test_registry_mutation_park_boundary_and_keep_park_boundary_are_mutually_exclusive(tmp_path):
    with pytest.raises(ca.CaError, match="mutually exclusive"):
        ca.load_registry(_registry(tmp_path, [_park_entry(keep_park_boundary=True)]))
    assert ca.load_registry(_registry(tmp_path, [_entry(0, keep_park_boundary=True)]))        # control
# ------------------------------- gate 3: prepare-areas tag check on the PBF --
def _info(tags, closed=True):
    return {"bbox": list(BOX), "tags": tags, "count": 1, "closed": closed}
def test_prepare_relation_needs_leisure_park_but_no_operator():
    entry = _park_entry()
    park = {"type": "multipolygon", "leisure": "park", "name": "Area 0"}
    assert ca.relation_problems(entry, _info(park)) == []
    for label, tags in (("nature_reserve", {**park, "leisure": "nature_reserve"}),
                        ("no leisure", {k: v for k, v in park.items() if k != "leisure"})):
        assert any("is not leisure=park" in p for p in ca.relation_problems(entry, _info(tags))), label
    # control: an unflagged entry is not subject to the leisure=park check
    assert ca.relation_problems(_entry(0), _info({**park, "leisure": "nature_reserve"})) == []
def test_prepare_way_needs_leisure_park_but_no_operator():
    entry = _way_entry()
    park = {"leisure": "park", "name": "Area 0"}
    assert ca.way_problems(entry, _info(park)) == []
    assert any("is not leisure=park" in p for p in ca.way_problems(entry, _info({**park, "leisure": "garden"})))
    assert ca.way_problems({**entry, "park_boundary": None} if False else
                           {k: v for k, v in entry.items() if k != "park_boundary"},
                           _info({**park, "leisure": "garden"})) == []                        # control
# --------------------------------------- gate 4: the re-add step (script) --
@needs_osmium
def test_relation_boundary_is_merged_into_the_aoi_and_passes_check_boundary_unique(tmp_path, capsys):
    entry = _park_entry()
    code, aoi, report = _run(tmp_path, entry, _rel())
    assert code == 0 and json.loads(report.read_text())["action"] == "merged"
    assert ca.boundary_name_matches(aoi, "Area 0") == [("relation", 1000)]
    reg = _registry(tmp_path, [entry])
    assert ca.main(["check-boundary-unique", "--registry", str(reg), "--area-id", entry["id"],
                    "--pbf", str(aoi)]) == 0
@needs_osmium
def test_way_boundary_is_merged_into_the_aoi(tmp_path):
    entry = _way_entry()
    code, aoi, report = _run(tmp_path, entry, _way())
    assert code == 0 and json.loads(report.read_text())["action"] == "merged"
    assert ca.boundary_name_matches(aoi, "Area 0", contains=True) == [("way", 2000)]
@needs_osmium
def test_control_without_the_key_the_script_refuses_and_the_aoi_is_untouched(tmp_path):
    entry = _entry(0)
    raw, aoi = _setup(tmp_path, _rel())
    before = aoi.read_bytes()
    code = pb.main(["--registry", str(_registry(tmp_path, [entry])), "--area-id", entry["id"],
                    "--raw-pbf", str(raw), "--aoi-pbf", str(aoi), "--report", str(tmp_path / "r.json")])
    assert code == 2 and aoi.read_bytes() == before and not (tmp_path / "r.json").exists()
@needs_osmium
def test_already_present_boundary_is_a_noop_and_leaves_the_aoi_bytes(tmp_path):
    entry = _park_entry()
    code, aoi, report = _run(tmp_path, entry, _rel(), aoi_body=_rel() + TRAIL)
    assert code == 0 and json.loads(report.read_text())["action"] == "already-present"
    assert aoi.read_text() == _osm(_rel() + TRAIL)
REFUSALS = {
    "not leisure=park": (_park_entry(), _rel({"leisure": "nature_reserve"}), "is not leisure=park"),
    "no leisure tag": (_park_entry(), _rel({"leisure": None}), "is not leisure=park"),
    "wrong relation type": (_park_entry(), _rel({"type": "route"}), "expected boundary/multipolygon"),
    "name differs": (_park_entry(), _rel({"name": "Other Park"}), "does not match registry name"),
    "relation absent": (_park_entry(), _rel(rid=1999), "is not in the raw PBF"),
    "way not leisure=park": (_way_entry(), _way({"leisure": "garden"}), "is not leisure=park"),
    "way not closed": (_way_entry(), _way(closed=False), "is not a closed ring"),
    "way name differs": (_way_entry(), _way({"name": "Other Park"}), "does not match registry name"),
    "way absent": (_way_entry(), TRAIL, "is not in the raw PBF"),
}
@needs_osmium
@pytest.mark.parametrize("label", sorted(REFUSALS))
def test_mutation_each_boundary_defect_is_a_safety_block_and_the_aoi_is_untouched(tmp_path, capsys, label):
    entry, raw_body, message = REFUSALS[label]
    raw, aoi = _setup(tmp_path, raw_body)
    before = aoi.read_bytes()
    code = pb.main(["--registry", str(_registry(tmp_path, [entry])), "--area-id", entry["id"],
                    "--raw-pbf", str(raw), "--aoi-pbf", str(aoi), "--report", str(tmp_path / "r.json")])
    err = capsys.readouterr().err
    assert code == 2 and "SAFETY BLOCK" in err and message in err
    assert aoi.read_bytes() == before and not (tmp_path / "r.json").exists()
@needs_osmium
def test_mutation_missing_input_files_and_unknown_area_are_refused(tmp_path):
    entry = _park_entry()
    raw, aoi = _setup(tmp_path, _rel())
    reg = str(_registry(tmp_path, [entry]))
    base = ["--registry", reg, "--area-id", entry["id"], "--report", str(tmp_path / "r.json")]
    assert pb.main(base + ["--raw-pbf", str(tmp_path / "nope.osm"), "--aoi-pbf", str(aoi)]) == 2
    assert pb.main(base + ["--raw-pbf", str(raw), "--aoi-pbf", str(tmp_path / "nope.osm")]) == 2
    assert pb.main(["--registry", reg, "--area-id", "ghost-ca-ns", "--raw-pbf", str(raw), "--aoi-pbf", str(aoi),
                    "--report", str(tmp_path / "r.json")]) == 2
# Second-line gates: the osmium CLI normally stops these first, so drive them with stubbed helpers.
def _keep(tmp_path, monkeypatch, scan_result=None, osmium=None, entry=None):
    raw, aoi = _setup(tmp_path, _rel())
    if scan_result is not None:
        monkeypatch.setattr(pb, "scan", lambda path: scan_result(path))
    if osmium is not None:
        monkeypatch.setattr(pb, "_osmium", osmium)
    return pb.keep_boundary(entry or _park_entry(), raw, aoi), aoi
def _fake_osmium(*args):
    out = Path(args[args.index("-o") + 1])
    out.write_text(_osm(_rel() + TRAIL), encoding="utf-8")
GOOD = {"tags": {"type": "multipolygon", "leisure": "park", "name": "Area 0"}, "closed": None, "count": 1}
def test_keep_boundary_refuses_an_entry_without_the_key(tmp_path):
    raw, aoi = _setup(tmp_path, _rel())
    with pytest.raises(pb.ParkBoundaryError, match="no park_boundary"):
        pb.keep_boundary(_entry(0), raw, aoi)
def test_keep_boundary_refuses_an_object_missing_from_the_extract(tmp_path, monkeypatch):
    with pytest.raises(pb.ParkBoundaryError, match="is not in the raw PBF"):
        _keep(tmp_path, monkeypatch, scan_result=lambda p: {}, osmium=_fake_osmium)
def test_keep_boundary_refuses_a_duplicated_object(tmp_path, monkeypatch):
    with pytest.raises(pb.ParkBoundaryError, match="occurs 2 times"):
        _keep(tmp_path, monkeypatch, scan_result=lambda p: {("relation", 1000): {**GOOD, "count": 2}},
              osmium=_fake_osmium)
def test_keep_boundary_refuses_when_the_merge_did_not_bring_the_boundary(tmp_path, monkeypatch):
    def scan(path):
        return {("relation", 1000): GOOD} if "piece" in Path(path).name else {}
    with pytest.raises(pb.ParkBoundaryError, match="missing after the merge"):
        _keep(tmp_path, monkeypatch, scan_result=scan, osmium=_fake_osmium)
def test_keep_boundary_refuses_when_osmium_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(pb.subprocess, "run", lambda *a, **k: types.SimpleNamespace(returncode=3, stderr="boom"))
    raw, aoi = _setup(tmp_path, _rel())
    with pytest.raises(pb.ParkBoundaryError, match="boom"):
        pb.keep_boundary(_park_entry(), raw, aoi)
def test_tag_problems_flags_each_defect_independently():
    ok = {"type": "multipolygon", "leisure": "park", "name": "A"}
    assert pb.tag_problems("relation", 1, ok, "A") == []
    assert pb.tag_problems("way", 1, {"leisure": "park", "name": "A"}, "A", True) == []
    assert len(pb.tag_problems("relation", 1, {"type": "route", "name": "B"}, "A")) == 3
# -------------------------------------- gate 5: shell step and isolation --
def _code(text):
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
def test_shell_step_is_registry_gated_and_runs_before_the_boundary_check(tmp_path):
    code = _code(SCRIPT)
    assert subprocess.run(["bash", "-n", str(ROOT / "scripts/ca-run-area.sh")]).returncode == 0
    assert 'if has("park_boundary") then "yes" else empty end' in code and 'if [ -n "$PARK_BOUNDARY" ]; then' in code
    plain, _ = _stub_run(tmp_path / "plain", {})
    plain_log = (tmp_path / "plain/argv.log").read_text().splitlines()
    assert not any("ca_park_boundary.py" in x for x in plain_log)                      # control: key absent, no step
    flagged, work = _stub_run(tmp_path / "flag", {"park_boundary": OPT})
    log = (tmp_path / "flag/argv.log").read_text().splitlines()
    step = [i for i, x in enumerate(log) if "ca_park_boundary.py" in x]
    check = [i for i, x in enumerate(log) if "check-boundary-unique" in x]
    assert len(step) == 1 and len(check) == 1 and step[0] < check[0]
    assert "--area-id x-ca-bc" in log[step[0]] and "--raw-pbf data/raw/canada-bc.osm.pbf" in log[step[0]]
    assert "--aoi-pbf data/aoi/x-ca-bc.osm.pbf" in log[step[0]] and f"--report {work}/ca-park-boundary.json" in log[step[0]]
    assert flagged.replace(str(tmp_path / "flag"), "T") == plain.replace(str(tmp_path / "plain"), "T")                                                           # no assemble flag is added
    assert re.search(r"-ca-\(ab\|bc\|mb\|nb\|nl\|ns\|on\|pe\|qc\|sk\|yt\|nt\|nu\)\$", code)
def test_canada_only_no_shared_engine_or_other_lane_references_the_opt_in():
    for rel in ("trailforge/assemble", "trailforge/serve", "trailforge/extract", "trailforge/tools"):
        for path in (ROOT / rel).rglob("*"):
            if path.is_file() and path.suffix in {".py", ".sh"}:
                assert "park_boundary" not in path.read_text(errors="ignore"), path
    for name in (".github/workflows/trailforge-publish.yml", ".github/workflows/trailforge-publish-dk.yml"):
        assert "park_boundary" not in (ROOT / name).read_text(), name
    assert "park_boundary" not in (ROOT / "scripts/dk_publish.py").read_text() if (ROOT / "scripts/dk_publish.py").exists() else True
def test_live_registry_loads_and_park_boundary_opt_ins_are_intact():
    # Rows are unheld one province at a time after curation (ship BC unheld Thetis Lake and Francis/King),
    # so the opt-in key itself is what must stay on all 11 intended rows.
    reg = ca.load_registry(ca.REGISTRY_PATH)
    flagged = {a["id"] for a in reg["areas"] if "park_boundary" in a}
    intended = {"thetis-lake-regional-park-ca-bc", "francis-king-regional-park-ca-bc",
                "bear-creek-recreation-site-ca-bc", "cranbrook-community-forest-recreation-site-ca-bc",
                "mount-abriel-recreation-site-ca-bc", "mount-cartier-recreation-site-ca-bc",
                "west-kokanee-recreation-site-ca-bc", "foothills-park-ca-bc", "sunnybrook-park-ca-on",
                "12-mile-coulee-ca-ab", "tatagwa-parkway-ca-sk"}
    assert intended <= flagged, sorted(intended - flagged)
