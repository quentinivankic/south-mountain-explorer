#!/usr/bin/env python3
"""Tests for the registry-driven Denmark publish tooling (dk_publish.py, the two
dk workflows, and the publish_areas / validator parametrization)."""
import copy
import importlib.util
import json
import os
import re
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dk_publish as dk  # noqa: E402
import r2_release_contract as contract  # noqa: E402
import simple_mols_publish as smp  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "trailforge/golden/dk-areas.json"
EXPECTED = ROOT / "trailforge/golden/dk-areas-expected.json"
MOLS = contract.MOLS_AREA_ID
OLD_ROW = ["old-area-co", "Old Area", "Colorado", 39.0, -105.0, 1, 1.0, None]
SHARED = ("index.json", "trail-search.json", "trail-shapes.json")


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


publish_areas = _load("publish_areas_dk_tests", "trailforge/serve/publish_areas.py")
validator = _load("validate_publish_pilot_dk_tests", "trailforge/tools/validate_publish_pilot.py")


def _compact(value):
    return json.dumps(value, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _default(value):
    return json.dumps(value, ensure_ascii=True).encode("utf-8")


# ----------------------------------------------------------------- registry --

def test_registry_loads_and_mols_is_entry_one_equal_to_the_hardened_pins():
    reg = dk.load_registry(REGISTRY)
    first = reg["areas"][0]
    assert first["id"] == contract.MOLS_AREA_ID == publish_areas._MOLS_AREA_ID
    assert first["name"] == contract.MOLS_AREA_NAME == publish_areas._MOLS_AREA_NAME
    assert first["relation_id"] == contract.MOLS_RELATION_ID == publish_areas._MOLS_RELATION_ID
    assert first["state"] == contract.MOLS_STATE
    assert dk.bbox_arg(first["bbox"]) == contract.MOLS_BBOX
    assert first["center"] == [contract.MOLS_CENTER_LAT, contract.MOLS_CENTER_LON]
    assert (reg["extract"], reg["state"], reg["region_code"]) == (
        contract.MOLS_EXTRACT, contract.MOLS_STATE, contract.MOLS_REGION_CODE)


def test_registry_has_the_four_national_parks_with_blessed_computed_geometry():
    by_id = dk.load_registry(REGISTRY)["by_id"]
    rels = {"nationalpark-thy-dk": 7045682, "nationalpark-vadehavet-dk": 14045506,
            "nationalpark-skjoldungernes-land-dk": 13648864,
            "nationalpark-kongernes-nordsjaelland-dk": 15312449}
    geom = {"nationalpark-thy-dk": ([8.16, 56.65, 8.73, 57.17], [56.9, 8.4]),
            "nationalpark-vadehavet-dk": ([7.94, 54.84, 8.92, 55.68], [55.3, 8.4]),
            "nationalpark-skjoldungernes-land-dk": ([11.75, 55.49, 12.17, 55.82], [55.7, 12.0]),
            "nationalpark-kongernes-nordsjaelland-dk": ([11.8, 55.84, 12.67, 56.15], [56.0, 12.2])}
    for aid, rel in rels.items():
        assert by_id[aid]["relation_id"] == rel
        assert (by_id[aid]["bbox"], by_id[aid]["center"]) == geom[aid]


def test_publish_areas_allowlist_equals_registry_and_always_has_mols():
    allowed = publish_areas._exact_publish_allowlist()
    reg = dk.load_registry(REGISTRY)
    assert allowed == {a["id"]: (a.get("display_name") or a["name"], a["relation_id"]) for a in reg["areas"]}
    assert allowed[MOLS] == (publish_areas._MOLS_AREA_NAME, publish_areas._MOLS_RELATION_ID)


def _write_registry(path, mutate):
    doc = json.loads(REGISTRY.read_text(encoding="utf-8"))
    mutate(doc)
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


@pytest.mark.parametrize("mutate", [
    lambda d: d["areas"][0].update(relation_id=1),            # Mols drift
    lambda d: d["areas"][0].update(name="Other"),
    lambda d: d["areas"][1].update(state="Sweden"),
    lambda d: d["areas"][2].update(id=d["areas"][1]["id"]),   # duplicate
])
def test_publish_areas_allowlist_fails_closed_on_a_bad_registry(tmp_path, mutate):
    path = _write_registry(tmp_path / "r.json", mutate)
    with pytest.raises(ValueError):
        publish_areas._exact_publish_allowlist(path)


def test_publish_areas_allowlist_without_registry_is_mols_only(tmp_path):
    assert publish_areas._exact_publish_allowlist(tmp_path / "missing.json") == {
        MOLS: (publish_areas._MOLS_AREA_NAME, publish_areas._MOLS_RELATION_ID)}


@pytest.mark.parametrize("mutate", [
    lambda d: d["areas"][1].update(bbox=[1, 2, 3, 4], center=[1, 2]),   # outside Denmark
    lambda d: d["areas"][1].update(bbox=[8.0, 56.0, 9.0, 57.0], center=None),   # bbox without center
    lambda d: d["areas"][1].update(id="no-suffix"),
    lambda d: d["areas"][1].update(relation_id=7046785),                # repeated relation
    lambda d: d.update(schema="x"),
])
def test_registry_rejects_invalid_entries(tmp_path, mutate):
    with pytest.raises(dk.DkError):
        dk.load_registry(_write_registry(tmp_path / "r.json", mutate))


def _big_registry(tmp_path, n):
    areas = [{"id": f"area-{i:03d}-dk", "name": f"Test Area {i}", "state": "Denmark",
              "relation_id": 1000 + i, "bbox": [9.0, 56.0, 9.5, 56.5], "center": [56.3, 9.3],
              "tier": 2, "notes": ""} for i in range(n)]
    doc = {"schema": dk.REGISTRY_SCHEMA, "extract": "europe/denmark", "state": "Denmark",
           "region_code": "dk", "areas": areas}
    path = tmp_path / "big.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return dk.load_registry(path)


# ------------------------------------------------------------------- select --

def test_chunking_cap_and_batches_scale_to_hundreds(tmp_path):
    reg = _big_registry(tmp_path, 300)
    first = dk.select_areas(reg, set(), "batch=1", 25, "qualify")
    assert len(first) == 25 and first[0] == "area-000-dk"
    assert dk.select_areas(reg, set(), "batch=12", 25, "qualify")[-1] == "area-299-dk"
    with pytest.raises(dk.DkError):
        dk.select_areas(reg, set(), "batch=13", 25, "qualify")
    # published areas drop out, so batch=1 is always "the next unpublished chunk"
    published = set(first)
    assert dk.select_areas(reg, published, "batch=1", 25, "publish")[0] == "area-025-dk"
    assert len(dk.select_areas(reg, set(), "batch=1", 40, "qualify")) == 40
    with pytest.raises(dk.DkError):
        dk.select_areas(reg, set(), "batch=1", 41, "qualify")      # hard max 40
    ids = ",".join(f"area-{i:03d}-dk" for i in range(26))
    with pytest.raises(dk.DkError):
        dk.select_areas(reg, set(), ids, 25, "qualify")            # explicit list over the cap
    assert len(dk.select_areas(reg, set(), ids, 26, "qualify")) == 26


def test_select_refuses_unknown_duplicate_malformed_and_published_for_publish(tmp_path):
    reg = _big_registry(tmp_path, 5)
    for bad in ("nope-dk", "area-000-dk,area-000-dk", "Area 1", "batch=0", "batch=x"):
        with pytest.raises(dk.DkError):
            dk.select_areas(reg, set(), bad, 25, "qualify")
    with pytest.raises(dk.DkError):
        dk.select_areas(reg, {"area-001-dk"}, "area-001-dk", 25, "publish")
    assert dk.select_areas(reg, {"area-001-dk"}, "area-001-dk", 25, "qualify") == ["area-001-dk"]
    with pytest.raises(dk.DkError):
        dk.select_areas(reg, set(), "area-000-dk", 25, "dry")


# ------------------------------------------------- full Danish registry ------

PARKS = ("nationalpark-thy-dk", "nationalpark-vadehavet-dk",
         "nationalpark-skjoldungernes-land-dk", "nationalpark-kongernes-nordsjaelland-dk")


def _reg():
    return dk.load_registry(REGISTRY)


BATCH2 = ("klosterhede-plantage-dk", "almindingen-dk", "skelhoj-plantage-dk", "hammer-bakker-dk",
          "stensbaek-plantage-dk", "rye-norreskov-dk", "pamhule-skov-dk", "godding-skov-dk",
          "skagen-klitplantage-dk", "nordskoven-416103-dk")
FLAGSHIPS = ("rold-skov-dk", "mons-klint-dk", "randbol-hede-frederikshab-dk", "nymindegab-blabjerg-dk")
BATCH_A_SHIP = ("stenderupskovene-dk", "grastenskovene-dk", "store-hareskov-dk", "stursbol-plantage-dk",
                "norreskoven-pa-als-dk", "harrild-hede-dk")
def test_full_registry_counts_ids_and_relations_are_unique():
    areas = _reg()["areas"]
    assert len(areas) == 112
    ids = [a["id"] for a in areas]
    rels = [a["relation_id"] for a in areas]
    assert len(set(ids)) == len(ids) and all(i.endswith("-dk") for i in ids)
    assert len(set(rels)) == len(rels)
    assert [a["id"] for a in areas[:5]][0] == MOLS and set(PARKS) <= set(ids)
    assert all(a["bbox"] is None for a in areas
               if a["id"] != MOLS and a["id"] not in PARKS and a["id"] not in BATCH2
               and a["id"] not in FLAGSHIPS and a["id"] not in BATCH_A_SHIP)


def test_hold_counts_match_the_discovery_candidates():
    areas = _reg()["areas"]
    holds = {}
    for a in areas:
        holds.setdefault(a["hold"], []).append(a["id"])
    assert {k: len(v) for k, v in holds.items()} == {
        None: 25, "name-collision": 2, "np-nested": 16, "island": 11, "duplicate-of-live": 5,
        "needs-rescope": 4, "merge-pending": 4, "merge-into-klosterhede": 1, "mtb-centre": 1,
        "curated-do-not-ship": 43}
    assert sorted(holds["duplicate-of-live"]) == [
        "gribskov-dk", "nordskoven-8166504-dk", "romo-dk", "store-dyrehave-dk", "tisvilde-hegn-dk"]
    assert sorted(holds["needs-rescope"]) == ["amager-dk", "bornholm-dk", "mors-dk", "samso-dk"]
    assert sorted(holds["merge-pending"]) == [
        "blabjerg-klitplantage-dk", "jonstrup-vang-dk", "randbol-hede-dk", "sonderskov-1891272-dk"]
    assert holds["merge-into-klosterhede"] == ["kronhede-plantage-dk"]
    assert holds["mtb-centre"] == ["vesterskov-2325780-dk"]
    assert sorted(holds["name-collision"]) == ["hejede-overdrev-dk", "vestvolden-dk"]
    assert all(a["hold"] is None for a in areas if a["id"] == MOLS or a["id"] in PARKS)
    assert all(a["tier"] in (1, 2) for a in areas)


def test_blessed_table_is_exactly_mols_plus_the_four_blessed_parks_plus_batch2():
    table = dk.load_expected(EXPECTED)
    assert set(table) == {MOLS, *PARKS, *BATCH2, *FLAGSHIPS, *BATCH_A_SHIP}
    assert {k: (v["trail_count"], v["total_mi"]) for k, v in table.items()} == {
        MOLS: (26, 85.4), "nationalpark-thy-dk": (32, 64.8), "nationalpark-vadehavet-dk": (43, 63.0),
        "nationalpark-skjoldungernes-land-dk": (11, 40.5),
        "nationalpark-kongernes-nordsjaelland-dk": (74, 82.3),
        "klosterhede-plantage-dk": (10, 34.9), "almindingen-dk": (7, 18.4),
        "skelhoj-plantage-dk": (4, 10.4), "hammer-bakker-dk": (4, 10.2),
        "stensbaek-plantage-dk": (3, 8.4), "rye-norreskov-dk": (7, 13.7),
        "pamhule-skov-dk": (4, 6.6), "godding-skov-dk": (4, 6.4),
        "skagen-klitplantage-dk": (4, 8.7), "nordskoven-416103-dk": (3, 6.8),
        "rold-skov-dk": (10, 39.4), "mons-klint-dk": (6, 17.1),
        "randbol-hede-frederikshab-dk": (9, 26.6), "nymindegab-blabjerg-dk": (7, 21.9),
        "stenderupskovene-dk": (5, 14.8), "grastenskovene-dk": (4, 13.3), "store-hareskov-dk": (2, 6.4),
        "stursbol-plantage-dk": (2, 4.8), "norreskoven-pa-als-dk": (2, 2.9), "harrild-hede-dk": (3, 11.0)}
    # Mols entry is byte-identical to its pre-bless form (as committed in the raw file)
    raw = json.loads(EXPECTED.read_text(encoding="utf-8"))["areas"][MOLS]
    assert json.dumps(raw, sort_keys=True) == json.dumps(table[MOLS], sort_keys=True)


def test_batch_skips_held_and_explicit_areas_include_them():
    reg = _reg()
    held = [a["id"] for a in reg["areas"] if a["hold"]]
    picked = set()
    for n in (1, 2):
        picked |= set(dk.select_areas(reg, {MOLS}, f"batch={n}", 12, "qualify"))
    assert not picked & set(held)
    assert dk.select_areas(reg, set(), ",".join(held[:3]), 25, "qualify") == held[:3]
    assert dk.select_areas(reg, set(), "hammer-bakker-dk", 25, "publish") == ["hammer-bakker-dk"]


def test_batches_are_deterministic_parks_first_then_est_miles_desc():
    reg = _reg()
    batches = [dk.select_areas(reg, {MOLS}, f"batch={n}", 12, "qualify") for n in (1, 2)]
    assert [len(b) for b in batches] == [12, 12]
    with pytest.raises(dk.DkError):
        dk.select_areas(reg, {MOLS}, "batch=3", 12, "qualify")
    assert batches == [dk.select_areas(reg, {MOLS}, f"batch={n}", 12, "qualify") for n in (1, 2)]
    flat = [i for b in batches for i in b]
    assert len(set(flat)) == 24 and MOLS not in flat
    assert set(flat[:4]) == set(PARKS)               # priority rule: tier 1 parks lead batch 1
    mi = [reg["by_id"][i]["est_mi"] for i in flat[4:]]
    assert mi == sorted(mi, reverse=True)
    # publishing batch 1 makes the old batch 2 the new batch 1
    assert dk.select_areas(reg, {MOLS, *batches[0]}, "batch=1", 12, "publish") == batches[1]


@pytest.mark.parametrize("mutate", [
    lambda d: d["areas"][1].update(hold=3),
    lambda d: d["areas"][1].update(hold=""),
    lambda d: d["areas"][1].update(est_mi=-1),
    lambda d: d["areas"][1].update(est_trails=1.5),
])
def test_registry_rejects_bad_hold_and_estimates(tmp_path, mutate):
    with pytest.raises(dk.DkError):
        dk.load_registry(_write_registry(tmp_path / "r.json", mutate))


# ------------------------------------------------------------- rule sets ----
DK_V3_BATCH_A = (
    "tokkekob-hegn-dk",
    "ro-plantage-dk",
    "jaegersborg-hegn-dk",
    "lystrup-skov-dk",
    "urhoje-plantage-dk",
    "sonderskov-10671204-dk",
    "ganlose-ore-dk",
    "varde-sondre-plantage-dk",
    "hannenov-skov-dk",
    "store-hareskov-dk",
    "sonderskov-17950902-dk",
    "norreskoven-pa-als-dk",
    "folehaven-dk",
    "paradisbakkerne-dk",
    "tversted-plantage-dk",
    "albaek-klitplantage-dk",
    "hojbjerg-skove-dk",
    "stursbol-plantage-dk",
    "lild-klit-dk",
    "norreskov-2823046-dk",
    "ravnsholt-dk",
    "kongenshus-dk",
    "harrild-hede-dk",
    "jonstrup-vang-dk",
    "bjerge-skov-dk",
    "sonderskov-1891272-dk",
    "pedersker-plantage-dk",
    "mellemskov-dk",
    "husby-klitplantage-dk",
    "staurby-skov-dk",
    "sogard-skov-dk",
    "rude-skov-dk",
    "grastenskovene-dk",
    "stenderupskovene-dk",
    "knagelbjerg-skov-dk",
    "rorvig-sandflugtsplantage-dk",
    "svinklov-klitplantage-dk",
    "dronninglund-storskov-dk",
    "langdal-plantage-dk",
)
DK_V3_UPDATE_1 = ("skagen-klitplantage-dk", "hammer-bakker-dk", "stensbaek-plantage-dk",
                  "klosterhede-plantage-dk", "rye-norreskov-dk")
def test_mols_control_is_legacy_and_every_other_entry_is_dk_v2_except_the_dk_v3_flagships():
    areas = dk.load_registry(REGISTRY)["areas"]
    assert areas[0]["id"] == MOLS and areas[0]["rules"] == "legacy"
    assert len(areas) > 1
    assert {a["id"]: a.get("rules") for a in areas[1:] if a.get("rules") != "dk-v2"} == {
        i: "dk-v3" for i in (
            "rold-skov-dk", "mons-klint-dk", "randbol-hede-frederikshab-dk", "nymindegab-blabjerg-dk",
            *DK_V3_BATCH_A, *DK_V3_UPDATE_1)}
def test_registry_rejects_unknown_rule_set(tmp_path):
    with pytest.raises(dk.DkError):
        dk.load_registry(_write_registry(
            tmp_path / "r.json", lambda d: d["areas"][1].update(rules="dk-v4")))
def test_registry_accepts_dk_v3_rules(tmp_path):
    reg = dk.load_registry(_write_registry(
        tmp_path / "r.json", lambda d: d["areas"][1].update(rules="dk-v3")))
    assert reg["areas"][1]["rules"] == "dk-v3"
def test_run_area_script_reads_rules_from_the_registry_and_passes_them_on():
    script = (ROOT / "scripts/dk-run-area.sh").read_text()
    assert 'select(.id == $id) | .rules // "legacy"' in script
    assert '--dk-rules "$RULES"' in script
    assert "(legacy|dk-v2|dk-v3)" in script
    assert "--dk-curated-include" in script and ".include_trails // []" in script
def test_road_name_removals_count_as_forbidden_junk_in_proposed_tables():
    assert "dk-road-name" in (ROOT / "scripts/dk_publish.py").read_text().split("junk_categories = ", 1)[1][:200]
# --------------------------------------------------------- bbox / prepare ----

OSM = """<?xml version='1.0' encoding='UTF-8'?>
<osm version="0.6">
<node id="1" lat="{s}" lon="{w}" version="1"/><node id="2" lat="{s}" lon="{e}" version="1"/>
<node id="3" lat="{n}" lon="{e}" version="1"/><node id="4" lat="{n}" lon="{w}" version="1"/>
<way id="10" version="1"><nd ref="1"/><nd ref="2"/><nd ref="3"/><nd ref="4"/><nd ref="1"/></way>
<relation id="{rel}" version="1"><member type="way" ref="10" role="outer"/>
<tag k="boundary" v="national_park"/></relation>
</osm>
"""


def _osm(tmp_path, rel, box):
    path = tmp_path / f"b{rel}.osm"
    path.write_text(OSM.format(rel=rel, w=box[0], s=box[1], e=box[2], n=box[3]), encoding="utf-8")
    return path


def test_boundary_bbox_from_pbf_relation(tmp_path):
    path = _osm(tmp_path, 100, (10.4513, 56.1298, 10.8473, 56.3204))
    got = dk.boundary_bboxes(path, [100, 999])
    assert got[100] == pytest.approx([10.4513, 56.1298, 10.8473, 56.3204], abs=1e-6)
    assert got[999] is None


def test_no_pyosmium_4x_only_api_in_dk_tooling():
    # ubuntu-24.04 apt python3-pyosmium is 3.7.0, which has no FileProcessor.
    for name in ("dk_publish.py", "dk-run-area.sh"):
        assert "FileProcessor" not in (ROOT / "scripts" / name).read_text(encoding="utf-8"), name


MOLS_BOUNDARY = [10.4513, 56.1298, 10.8473, 56.3204]


def test_mols_registry_entry_is_consistent_with_its_real_boundary_bbox():
    entry = dk.load_registry(REGISTRY)["by_id"][MOLS]
    rec = dk.check_area_geometry(entry, MOLS_BOUNDARY, [56.2251, 10.6493], "publish")
    assert rec["status"] == "ok", rec["problems"]
    assert rec["proposed_center"] == [56.2, 10.6] == entry["center"]
    assert rec["effective_bbox"] == entry["bbox"] and rec["bbox_source"] == "registry"


def _null_bbox_registry(tmp_path):
    """Synthetic registry: a copy whose Thy entry has no bbox/center (not yet blessed)."""
    def nullify(doc):
        thy = next(a for a in doc["areas"] if a["id"] == "nationalpark-thy-dk")
        thy.update(bbox=None, center=None)
    return _write_registry(tmp_path / "null-bbox-registry.json", nullify)


def test_missing_bbox_is_proposed_in_qualify_and_fails_closed_in_publish(tmp_path):
    entry = dk.load_registry(_null_bbox_registry(tmp_path))["by_id"]["nationalpark-thy-dk"]
    box = [8.1, 56.7, 8.7, 57.1]
    q = dk.check_area_geometry(entry, box, [56.9, 8.4], "qualify")
    assert q["status"] == "ok" and q["bbox_source"] == "computed"
    assert q["effective_bbox"] == dk.pad_bbox(box) == [8.05, 56.65, 8.75, 57.15]
    p = dk.check_area_geometry(entry, box, [56.9, 8.4], "publish")
    assert p["status"] == "failed" and any("missing" in x for x in p["problems"])


@pytest.mark.parametrize("bbox,center,why", [
    ([10.45, 56.08, 10.90, 56.35], [56.2, 10.6], "margin below minimum"),
    ([10.0, 55.5, 11.5, 57.0], [56.2, 10.6], "margin above maximum"),
    ([10.40, 56.08, 10.90, 56.35], [56.3, 10.6], "centre differs"),
])
def test_inconsistent_registry_bbox_or_center_fails_closed(bbox, center, why):
    entry = copy.deepcopy(dk.load_registry(REGISTRY)["by_id"][MOLS])
    entry["bbox"], entry["center"] = bbox, center
    rec = dk.check_area_geometry(entry, MOLS_BOUNDARY, [56.2251, 10.6493], "qualify")
    assert rec["status"] == "failed", why


def test_boundary_missing_or_disagreeing_with_discovery_fails():
    entry = dk.load_registry(REGISTRY)["by_id"][MOLS]
    assert dk.check_area_geometry(entry, None, [56.2, 10.6], "qualify")["status"] == "failed"
    assert dk.check_area_geometry(entry, MOLS_BOUNDARY, [57.9, 12.0], "qualify")["status"] == "failed"


def test_prepare_areas_cli_writes_one_row_index_and_fails_publish_on_null_bbox(tmp_path):
    reg = dk.load_registry(REGISTRY)
    null_reg = _null_bbox_registry(tmp_path)
    mols = reg["by_id"][MOLS]
    thy = dk.load_registry(null_reg)["by_id"]["nationalpark-thy-dk"]
    bounds = tmp_path / "bounds.osm"
    bounds.write_text(OSM.format(rel=mols["relation_id"], w=MOLS_BOUNDARY[0], s=MOLS_BOUNDARY[1],
                                 e=MOLS_BOUNDARY[2], n=MOLS_BOUNDARY[3]), encoding="utf-8")
    sel = tmp_path / "sel.txt"
    sel.write_text(f"{MOLS}\n", encoding="utf-8")
    disc = tmp_path / "disc.json"
    disc.write_text(json.dumps({"candidates": [
        {"index_row": [MOLS, mols["name"], "Denmark", 56.2251, 10.6493],
         "osm_relation_id": mols["relation_id"]}]}), encoding="utf-8")
    out = tmp_path / "out"
    argv = ["prepare-areas", "--selected", str(sel), "--bounds-pbf", str(bounds),
            "--discovery-report", str(disc), "--mode", "publish", "--out-dir", str(out)]
    assert dk.main(argv) == 0
    row = json.loads((out / MOLS / "index.json").read_text())
    assert row == [[MOLS, mols["name"], "Denmark", 56.2, 10.6, None, None, mols["relation_id"]]]
    area = json.loads((out / MOLS / "area.json").read_text())
    assert area["bbox_arg"] == contract.MOLS_BBOX
    # Thy has no registry bbox: qualify proposes it, publish refuses.
    bounds.write_text(OSM.format(rel=thy["relation_id"], w=8.1, s=56.7, e=8.7, n=57.1), encoding="utf-8")
    sel.write_text("nationalpark-thy-dk\n", encoding="utf-8")
    disc.write_text(json.dumps({"candidates": [
        {"index_row": ["nationalpark-thy-dk", thy["name"], "Denmark", 56.9, 8.4],
         "osm_relation_id": thy["relation_id"]}]}), encoding="utf-8")
    argv += ["--registry", str(null_reg)]
    argv[argv.index("publish")] = "qualify"
    assert dk.main(argv) == 0
    assert json.loads((out / "nationalpark-thy-dk/area.json").read_text())["bbox_arg"] == "8.05,56.65,8.75,57.15"
    argv[argv.index("qualify")] = "publish"
    assert dk.main(argv) == 1


# ------------------------------------- areas without a discovery entry ----

FOREST = "nordskoven-416103-dk"
FOREST_BOX = (11.95, 55.95, 12.15, 56.10)
FOREST_OSM = """<?xml version='1.0' encoding='UTF-8'?>
<osm version="0.6">
<node id="1" lat="{s}" lon="{w}" version="1"/><node id="2" lat="{s}" lon="{e}" version="1"/>
<node id="3" lat="{n}" lon="{e}" version="1"/><node id="4" lat="{n}" lon="{w}" version="1"/>
<way id="10" version="1"><nd ref="1"/><nd ref="2"/><nd ref="3"/><nd ref="4"/><nd ref="1"/></way>
<relation id="{rel}" version="1"><member type="way" ref="10" role="outer"/>
<tag k="type" v="{rtype}"/><tag k="landuse" v="forest"/><tag k="name" v="{name}"/></relation>
</osm>
"""


def _forest_prepare(tmp_path, *, rel=None, box=FOREST_BOX, name=None, rtype="multipolygon",
                    mode="qualify", disc=None, registry=None):
    """Drive the real `prepare-areas` CLI for a forest entry (no discovery candidate unless given)."""
    if registry is None:                      # synthetic registry: FOREST is not yet blessed (no bbox/center)
        registry = _write_registry(
            tmp_path / "forest-null-bbox-registry.json",
            lambda d: next(a for a in d["areas"] if a["id"] == FOREST).update(bbox=None, center=None))
    entry = dk.load_registry(registry)["by_id"][FOREST]
    bounds = tmp_path / "bounds.osm"
    bounds.write_text(FOREST_OSM.format(rel=rel or entry["relation_id"], w=box[0], s=box[1], e=box[2],
                                        n=box[3], rtype=rtype, name=name or entry["name"]), encoding="utf-8")
    sel = tmp_path / "sel.txt"
    sel.write_text(f"{FOREST}\n", encoding="utf-8")
    report = tmp_path / "disc.json"
    report.write_text(json.dumps({"candidates": disc or []}), encoding="utf-8")
    out = tmp_path / "out"
    argv = ["prepare-areas", "--selected", str(sel), "--bounds-pbf", str(bounds),
            "--discovery-report", str(report), "--mode", mode, "--out-dir", str(out)]
    argv += ["--registry", str(registry)]
    rc = dk.main(argv)
    return rc, json.loads((out / FOREST / "area.json").read_text()), out


def test_forest_without_discovery_entry_passes_on_its_own_relation(tmp_path):
    rc, area, out = _forest_prepare(tmp_path)
    assert rc == 0 and area["status"] == "ok", area["problems"]
    assert area["cross_check"] == "registry-relation" and area["bbox_source"] == "computed"
    assert area["bbox_arg"] == dk.bbox_arg(dk.pad_bbox(list(FOREST_BOX)))
    row = json.loads((out / FOREST / "index.json").read_text())
    assert row[0][:3] == [FOREST, "Nordskoven", "Denmark"] and row[0][7] == area["relation_id"]


def test_discovery_entry_still_uses_the_discovery_centre_cross_check(tmp_path):
    entry = dk.load_registry(REGISTRY)["by_id"][FOREST]
    row = [FOREST, entry["name"], "Denmark", 56.02, 12.05]
    rc, area, _ = _forest_prepare(tmp_path, disc=[{"index_row": row, "osm_relation_id": entry["relation_id"]}])
    assert rc == 0 and area["cross_check"] == "discovery"
    far = [FOREST, entry["name"], "Denmark", 57.5, 9.0]
    (tmp_path / "far").mkdir()
    rc, area, _ = _forest_prepare(tmp_path / "far", disc=[{"index_row": far, "osm_relation_id": entry["relation_id"]}])
    assert area["status"] == "failed" and any("disagrees" in p for p in area["problems"])


@pytest.mark.parametrize("kwargs,needle", [
    ({"rel": 4242}, "relation not found in the PBF"),                                  # registry relation absent
    ({"name": "Anden Skov"}, "does not match registry name"),           # wrong entity
    ({"rtype": "route"}, "expected boundary/multipolygon"),            # not an area relation
    ({"box": (12.0, 56.0, 12.0005, 56.0005)}, "implausible"),           # tiny
    ({"box": (10.0, 55.0, 13.5, 57.5)}, "implausible"),                 # huge
    ({"box": (17.0, 55.0, 17.2, 55.2)}, "outside Denmark"),            # not Denmark
])
def test_forest_without_discovery_entry_fails_closed(tmp_path, kwargs, needle):
    rc, area, out = _forest_prepare(tmp_path, **kwargs)
    assert area["status"] == "failed", kwargs
    assert any(needle in p for p in area["problems"]), area["problems"]
    assert not (out / FOREST / "index.json").exists()
    # publish mode refuses the whole run
    (tmp_path / "pub").mkdir()
    rc, _area, _out = _forest_prepare(tmp_path / "pub", mode="publish", **kwargs)
    assert rc == 1


def test_duplicate_relation_id_is_not_unique():
    entry = dk.load_registry(REGISTRY)["by_id"][FOREST]
    info = {"bbox": list(FOREST_BOX), "count": 2,
            "tags": {"type": "multipolygon", "landuse": "forest", "name": entry["name"]}}
    assert any("not unique" in p for p in dk.relation_problems(entry, info))
    assert dk.relation_problems(entry, None)


def test_run_area_script_skips_discovery_only_after_the_registry_relation_check(tmp_path):
    """Run the real discovery gate of dk-run-area.sh (everything before `cd trailforge`)."""
    import subprocess
    script = (ROOT / "scripts/dk-run-area.sh").read_text(encoding="utf-8").split("\ncd trailforge\n")[0]
    gate = tmp_path / "gate.sh"
    gate.write_text(script, encoding="utf-8")
    entry = dk.load_registry(REGISTRY)["by_id"][FOREST]
    w = tmp_path / "w"
    (w / FOREST).mkdir(parents=True)
    (w / "discovery-report.json").write_text(json.dumps({"candidates": []}), encoding="utf-8")

    def run(area_json):
        (w / FOREST / "area.json").write_text(json.dumps(area_json), encoding="utf-8")
        return subprocess.run(["bash", str(gate)], cwd=ROOT, capture_output=True, text=True, env={
            **os.environ, "AREA_ID": FOREST, "AREA_NAME": entry["name"], "RELATION_ID": str(entry["relation_id"]),
            "BBOX": "11.9,55.9,12.2,56.2", "W": str(w)})

    ok = run({"id": FOREST, "status": "ok", "cross_check": "registry-relation"})
    assert ok.returncode == 0, ok.stderr
    assert json.loads((w / FOREST / "discovery-validation.json").read_text())["status"] == "skipped"
    for bad in ({"id": FOREST, "status": "ok", "cross_check": "discovery"},
                {"id": FOREST, "status": "failed", "cross_check": "registry-relation"},
                {"id": "other-dk", "status": "ok", "cross_check": "registry-relation"}, {}):
        assert run(bad).returncode != 0, bad


def test_workflow_prefilters_with_the_denmark_area_selectors():
    assert "PREFILTER_DK_AREAS=1 bash extract/prefilter.sh" in PUBLISH_DK


# ------------------------------------------------------------ expected table --

def test_committed_expected_table_mols_entry_equals_the_hardened_constants():
    entry = dk.load_expected(EXPECTED)[MOLS]
    assert entry["trail_count"] == smp.EXPECTED_TRAILS
    assert entry["total_mi"] == smp.EXPECTED_MILES
    assert entry["total_mi_tolerance"] == smp.MILES_TOLERANCE
    assert tuple(entry["required_names"]) == smp.REQUIRED_TRAILS
    assert tuple(entry["forbidden_names"]) == smp.FORBIDDEN_TRAILS


def _geom(aid, trail_count=2, total_mi=3.0, names=None):
    names = names or [f"{aid} Trail {i}" for i in range(trail_count)]
    return {"id": aid, "name": aid, "trail_count": len(names), "total_mi": total_mi,
            "trails": [{"id": f"t{i}", "name": n, "distanceMi": 1.5, "difficulty": "Easy",
                        "segments": [[[56.2, 10.6 + i / 100], [56.21, 10.62 + i / 100], [56.22, 10.6 + i / 100]]]}
                       for i, n in enumerate(names)]}


def _expected_file(tmp_path, aid, **override):
    entry = {"trail_count": 2, "total_mi": 3.0, "total_mi_tolerance": 0.05,
             "required_names": [f"{aid} Trail 0"], "forbidden_names": ["Bad Road"]}
    entry.update(override)
    path = tmp_path / "expected.json"
    path.write_text(json.dumps({"schema": dk.EXPECTED_SCHEMA, "areas": {aid: entry}}), encoding="utf-8")
    return path


def _assert_expected(tmp_path, geom, expected_path, aid="x-dk"):
    gp = tmp_path / "g.json"
    gp.write_text(json.dumps(geom), encoding="utf-8")
    return dk.main(["assert-expected", "--geom", str(gp), "--area-id", aid, "--expected", str(expected_path)])


def test_assert_expected_passes_and_fails(tmp_path):
    aid = "x-dk"
    exp = _expected_file(tmp_path, aid)
    assert _assert_expected(tmp_path, _geom(aid), exp) == 0
    assert _assert_expected(tmp_path, _geom(aid, total_mi=3.04), exp) == 0
    assert _assert_expected(tmp_path, _geom(aid, total_mi=3.2), exp) == 1       # miles
    assert _assert_expected(tmp_path, _geom(aid, trail_count=3), exp) == 1      # count
    assert _assert_expected(tmp_path, _geom(aid, names=["Other", f"{aid} Trail 1"]), exp) == 1  # required
    assert _assert_expected(tmp_path, _geom(aid, names=[f"{aid} Trail 0", "Bad Road"]), exp) == 1  # forbidden
    assert _assert_expected(tmp_path, _geom("y-dk"), exp) == 1                  # wrong id
    assert _assert_expected(tmp_path, _geom("z-dk"), exp, aid="z-dk") == 1      # not blessed


def test_mols_66_trail_statewide_result_fails_the_blessed_mols_entry(tmp_path):
    names = ["Enhøjvej", "Lyngevej"] + [f"Trail {i}" for i in range(64)]
    gp = tmp_path / "g.json"
    gp.write_text(json.dumps({"id": MOLS, "trail_count": 66, "total_mi": 127.2,
                              "trails": [{"name": n} for n in names]}), encoding="utf-8")
    assert dk.main(["assert-expected", "--geom", str(gp), "--area-id", MOLS]) == 1


# -------------------------------------------------- N-area splice / sync ----

def _make_repo(tmp_path, n, *, live_extra_rows=0):
    """Fixture repo with n new areas + live R2 bytes. Returns (repo, live_dir, ids)."""
    reg = _big_registry(tmp_path, n)
    repo, live = tmp_path / "repo", tmp_path / "live"
    for d in (repo / "trailforge/golden", repo / "public/areas/geom", repo / "public/areas/silhouettes",
              repo / "ios/SouthMountainExplorer/Resources", live):
        d.mkdir(parents=True, exist_ok=True)
    registry_doc = {k: v for k, v in reg.items() if k != "by_id"}
    (repo / "trailforge/golden/dk-areas.json").write_text(json.dumps(registry_doc), encoding="utf-8")
    ids = sorted(a["id"] for a in reg["areas"])
    expected = {"schema": dk.EXPECTED_SCHEMA, "areas": {}}
    rows = []
    for aid in ids:
        entry = reg["by_id"][aid]
        geom = _geom(aid)
        (repo / f"public/areas/geom/{aid}.json").write_bytes(_compact(geom))
        (repo / f"public/areas/silhouettes/{aid}.json").write_bytes(_compact({"b": [0, 0, 1, 1], "l": [[1, 2]]}))
        rows.append([aid, entry["name"], "Denmark", 56.3, 9.3, 2, 3.0, entry["relation_id"]])
        expected["areas"][aid] = {"trail_count": 2, "total_mi": 3.0, "total_mi_tolerance": 0.05,
                                  "required_names": [f"{aid} Trail 0"], "forbidden_names": []}
    (repo / "trailforge/golden/dk-areas-expected.json").write_text(json.dumps(expected), encoding="utf-8")
    (repo / "ios/SouthMountainExplorer/Resources/areas-index.json").write_bytes(_compact([OLD_ROW, *rows]))
    (live / "index.json").write_bytes(_compact([OLD_ROW]))
    (live / "trail-search.json").write_bytes(_compact([["Old Trail", "old-area-co", "a", 1.0, "Easy"]]))
    (live / "trail-shapes.json").write_bytes(_compact({"old-area-co/a": [0, 0, 255, 255]}))
    (live / "parking.json").write_bytes(b'{"parking":"untouched"}')
    return repo, live, ids


def _build(repo, live, out, extra=()):
    return dk.main(["build-candidates", "--repo", str(repo),
                    "--live-index", str(live / "index.json"), "--live-search", str(live / "trail-search.json"),
                    "--live-shapes", str(live / "trail-shapes.json"), "--out-dir", str(out), *extra])


@pytest.mark.parametrize("n", [1, 3, 40])
def test_splice_n_areas_onto_live_bytes(tmp_path, n):
    repo, live, ids = _make_repo(tmp_path, n)
    parking_before = (live / "parking.json").read_bytes()
    out = tmp_path / "out"
    assert _build(repo, live, out) == 0
    plan = json.loads((out / "plan.json").read_text())
    assert plan["areas"] == ids
    keys = [w["key"] for w in plan["writes"]]
    assert len(keys) == 2 * n + 3
    assert keys[:n] == [f"{i}.json" for i in ids]
    assert keys[n:2 * n] == [f"silhouettes/{i}.json" for i in ids]
    assert keys[2 * n:] == ["trail-shapes.json", "trail-search.json", "index.json"]   # index last
    assert not any("parking" in k for k in keys)
    assert (live / "parking.json").read_bytes() == parking_before
    for key, closer in (("trail-search.json", b"]"), ("trail-shapes.json", b"}")):
        old, new = (live / key).read_bytes(), (out / key).read_bytes()
        assert new.startswith(old[:-1] + b",") and new.endswith(closer)
    search = json.loads((out / "trail-search.json").read_bytes())
    shapes = json.loads((out / "trail-shapes.json").read_bytes())
    assert search[0] == ["Old Trail", "old-area-co", "a", 1.0, "Easy"] and len(search) == 1 + 2 * n
    assert shapes["old-area-co/a"] == [0, 0, 255, 255] and len(shapes) == 1 + 2 * n
    index = json.loads((out / "index.json").read_bytes())
    assert index[0] == OLD_ROW and [r[0] for r in index[1:]] == ids
    for w in plan["writes"]:
        assert dk._sha((out / w["file"]).read_bytes()) == w["sha256"]


def test_derive_areas_comes_from_the_committed_index_diff(tmp_path):
    repo, live, ids = _make_repo(tmp_path, 3)
    out = tmp_path / "areas.txt"
    assert dk.main(["derive-areas", "--repo", str(repo), "--live-index", str(live / "index.json"),
                    "--out", str(out)]) == 0
    assert out.read_text().split() == ids
    # an areas file that disagrees with the diff is refused
    out.write_text(ids[0] + "\n", encoding="utf-8")
    assert _build(repo, live, tmp_path / "o2", ["--areas-file", str(out)]) == 1


def test_refuses_when_a_new_key_already_exists_in_live(tmp_path):
    repo, live, ids = _make_repo(tmp_path, 3)
    aid = ids[1]
    # (a) live index already has the area
    (live / "index.json").write_bytes(_compact([OLD_ROW, json.loads(
        (repo / "ios/SouthMountainExplorer/Resources/areas-index.json").read_bytes())[2]]))
    assert _build(repo, live, tmp_path / "a") == 1
    (live / "index.json").write_bytes(_compact([OLD_ROW]))
    # (b) live trail-search already has a record for it
    (live / "trail-search.json").write_bytes(_compact(
        [["Old Trail", "old-area-co", "a", 1.0, "Easy"], ["X", aid, "t0", 1.5, "Easy"]]))
    assert _build(repo, live, tmp_path / "b") == 1
    (live / "trail-search.json").write_bytes(_compact([["Old Trail", "old-area-co", "a", 1.0, "Easy"]]))
    # (c) live trail-shapes already has a key for it
    (live / "trail-shapes.json").write_bytes(_compact({"old-area-co/a": [0, 0, 255, 255], f"{aid}/t0": [0, 0, 9, 9]}))
    assert _build(repo, live, tmp_path / "c") == 1
    (live / "trail-shapes.json").write_bytes(_compact({"old-area-co/a": [0, 0, 255, 255]}))
    assert _build(repo, live, tmp_path / "ok") == 0


def test_refuses_bundle_that_is_not_live_plus_sorted_registry_rows(tmp_path):
    repo, live, ids = _make_repo(tmp_path, 3)
    bundle = repo / "ios/SouthMountainExplorer/Resources/areas-index.json"
    rows = json.loads(bundle.read_bytes())
    changed = copy.deepcopy(rows)
    changed[0][1] = "Renamed"                                   # live row modified
    bundle.write_bytes(_compact(changed))
    assert _build(repo, live, tmp_path / "a") == 1
    unsorted = [rows[0], rows[2], rows[1], rows[3]]
    bundle.write_bytes(_compact(unsorted))
    assert _build(repo, live, tmp_path / "b") == 1
    bundle.write_bytes(_compact(rows[:-1] + [["ghost-dk", "Ghost", "Denmark", 56.0, 9.0, 2, 3.0, 5]]))
    assert _build(repo, live, tmp_path / "c") == 1              # not in the registry
    bundle.write_bytes(json.dumps(rows).encode("utf-8"))        # wrong framing
    assert _build(repo, live, tmp_path / "d") == 1


def test_refuses_area_that_is_not_blessed_or_differs_from_the_table(tmp_path):
    repo, live, ids = _make_repo(tmp_path, 2)
    path = repo / "trailforge/golden/dk-areas-expected.json"
    table = json.loads(path.read_text())
    del table["areas"][ids[0]]
    path.write_text(json.dumps(table), encoding="utf-8")
    assert _build(repo, live, tmp_path / "a") == 1
    table["areas"][ids[0]] = {"trail_count": 9, "total_mi": 3.0, "total_mi_tolerance": 0.05,
                              "required_names": [], "forbidden_names": []}
    path.write_text(json.dumps(table), encoding="utf-8")
    assert _build(repo, live, tmp_path / "b") == 1


def test_sync_area_cap_is_enforced(tmp_path):
    repo, live, ids = _make_repo(tmp_path, 3)
    (repo / "ios/SouthMountainExplorer/Resources/areas-index.json").write_bytes(_compact([OLD_ROW]))
    assert _build(repo, live, tmp_path / "a") == 1              # N = 0


# ---------------------------------------------------------------- rollback ---

def _plan_and_states(tmp_path, n=3):
    repo, live, ids = _make_repo(tmp_path, n)
    out = tmp_path / "out"
    assert _build(repo, live, out) == 0
    return out, live, ids


def _decide(tmp_path, capsys, state):
    out, live, ids = _plan_and_states(tmp_path)
    capsys.readouterr()
    cur = tmp_path / "cur"
    cur.mkdir()
    for key, what in state.items():
        data = {"cand": (out / key).read_bytes(), "base": (live / key).read_bytes(),
                "other": b'["third-party"]'}[what]
        (cur / key).write_bytes(data)
    code = dk.main(["rollback-decide", "--plan", str(out / "plan.json"), "--live-dir", str(cur)])
    cap = capsys.readouterr()
    lines = [line.split("\t") for line in cap.out.splitlines() if "\t" in line]
    return code, lines, cap.err, ids


def test_rollback_fully_applied_restores_all_three_and_deletes_exactly_2n_keys(tmp_path, capsys):
    code, lines, _, ids = _decide(tmp_path, capsys, {k: "cand" for k in SHARED})
    assert code == 0
    assert [tuple(x) for x in lines[:3]] == [("index.json", "restore"), ("trail-search.json", "restore"),
                                             ("trail-shapes.json", "restore")]
    deletes = [k for k, a in lines if a == "delete"]
    assert deletes == [f"{i}.json" for i in ids] + [f"silhouettes/{i}.json" for i in ids]
    assert len(deletes) == 2 * len(ids) and not any(k in SHARED or "parking" in k for k in deletes)


def test_rollback_partial_apply_restores_only_candidate_files(tmp_path, capsys):
    state = {"index.json": "base", "trail-search.json": "base", "trail-shapes.json": "cand"}
    code, lines, _, ids = _decide(tmp_path, capsys, state)
    assert code == 0
    assert dict(lines[:3]) == {"index.json": "skip", "trail-search.json": "skip", "trail-shapes.json": "restore"}
    assert len([1 for _, a in lines if a == "delete"]) == 2 * len(ids)


def test_rollback_nothing_applied_skips_shared_files(tmp_path, capsys):
    code, lines, _, _ = _decide(tmp_path, capsys, {k: "base" for k in SHARED})
    assert code == 0 and dict(lines[:3]) == {k: "skip" for k in SHARED}


@pytest.mark.parametrize("bad", SHARED)
def test_rollback_refuses_third_party_modified_file(tmp_path, capsys, bad):
    state = {"index.json": "cand", "trail-search.json": "base", "trail-shapes.json": "cand"}
    state[bad] = "other"
    code, lines, err, _ = _decide(tmp_path, capsys, state)
    assert code == 1 and lines == [] and "refusing" in err


# ------------------------------------------- merge-index + 2N+2 allowlist ----

def _git(repo, *argv):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
    return subprocess.run(["git", *argv], cwd=repo, env=env, check=True, capture_output=True)


def _baseline_repo(tmp_path, n):
    """Git repo at the pre-publish state (OLD_ROW only) + the n areas' files kept aside."""
    repo, live, ids = _make_repo(tmp_path, n)
    new = tmp_path / "new"
    new.mkdir()
    bundle_path = repo / "ios/SouthMountainExplorer/Resources/areas-index.json"
    rows = json.loads(bundle_path.read_bytes())
    for aid in ids:
        for rel in (f"public/areas/geom/{aid}.json", f"public/areas/silhouettes/{aid}.json"):
            (new / f"{Path(rel).parent.name}-{Path(rel).name}").write_bytes((repo / rel).read_bytes())
            (repo / rel).unlink()
    bundle_path.write_bytes(_compact([OLD_ROW]))
    (repo / "public/areas/index.json").write_bytes(_default([OLD_ROW]))
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "baseline")
    return repo, ids, rows, new


def _apply_publish(repo, ids, rows, new, *, stage=True):
    for aid in ids:
        (repo / f"public/areas/geom/{aid}.json").write_bytes((new / f"geom-{aid}.json").read_bytes())
        (repo / f"public/areas/silhouettes/{aid}.json").write_bytes((new / f"silhouettes-{aid}.json").read_bytes())
    (repo / "public/areas/index.json").write_bytes(_default(rows))
    (repo / "ios/SouthMountainExplorer/Resources/areas-index.json").write_bytes(_compact(rows))
    if stage:
        _git(repo, "add", "public/areas", "ios/SouthMountainExplorer/Resources/areas-index.json")


def _verify(repo, ids, cmd="verify-staged"):
    return dk.main([cmd, "--repo", str(repo), "--areas", ",".join(ids)])


@pytest.mark.parametrize("n", [1, 3, 40])
def test_commit_allowlist_is_exactly_2n_plus_2(tmp_path, n):
    repo, ids, rows, new = _baseline_repo(tmp_path, n)
    _apply_publish(repo, ids, rows, new)
    assert len(dk.area_paths(ids)) == 2 * n + 2
    names = _git(repo, "diff", "--cached", "--name-only").stdout.decode().split()
    assert len(names) == 2 * n + 2
    assert _verify(repo, ids) == 0
    _git(repo, "commit", "-q", "-m", "publish")
    assert _verify(repo, ids, "verify-commit") == 0


def test_staged_diff_refuses_extra_missing_modified_and_reordered_paths(tmp_path):
    repo, ids, rows, new = _baseline_repo(tmp_path, 3)
    _apply_publish(repo, ids, rows, new)
    (repo / "public/areas/geom/other-dk.json").write_text("{}", encoding="utf-8")
    _git(repo, "add", "public/areas/geom/other-dk.json")
    assert _verify(repo, ids) == 1                              # extra path
    _git(repo, "rm", "-q", "-f", "public/areas/geom/other-dk.json")
    assert _verify(repo, ids) == 0
    _git(repo, "rm", "-q", "--cached", f"public/areas/silhouettes/{ids[0]}.json")
    assert _verify(repo, ids) == 1                              # missing path
    _git(repo, "add", f"public/areas/silhouettes/{ids[0]}.json")
    bad = copy.deepcopy(rows)
    bad[0][1] = "Changed live row"
    _apply_publish(repo, ids, bad, new)
    assert _verify(repo, ids) == 1                              # non-Danish row modified
    _apply_publish(repo, ids, [rows[0], rows[2], rows[1], rows[3]], new)
    assert _verify(repo, ids) == 1                              # new rows not sorted
    wrong = copy.deepcopy(rows)
    wrong[1][5] = 99                                            # count differs from geom
    _apply_publish(repo, ids, wrong, new)
    assert _verify(repo, ids) == 1
    _apply_publish(repo, ids, rows, new)
    assert _verify(repo, ids) == 0
    (repo / "public/areas/index.json").write_bytes(json.dumps(rows, indent=1).encode("utf-8"))
    _git(repo, "add", "public/areas/index.json")
    assert _verify(repo, ids) == 1                              # framing differs from producer


def test_merge_index_appends_sorted_rows_and_keeps_existing_bytes(tmp_path):
    repo, ids, rows, new = _baseline_repo(tmp_path, 3)
    work = tmp_path / "work"
    reg = repo / "trailforge/golden/dk-areas.json"
    for aid, row in zip(ids, rows[1:]):
        (work / aid).mkdir(parents=True)
        (work / aid / "index.json").write_text(json.dumps([row]), encoding="utf-8")
        (repo / f"public/areas/geom/{aid}.json").write_bytes((new / f"geom-{aid}.json").read_bytes())
    sel = tmp_path / "sel.txt"
    sel.write_text("\n".join(reversed(ids)) + "\n", encoding="utf-8")        # input order must not matter
    target = repo / "public/areas/index.json"
    before = target.read_bytes()
    argv = ["merge-index", "--registry", str(reg), "--selected", str(sel), "--rows-dir", str(work),
            "--geom-dir", str(repo / "public/areas/geom"), "--target", str(target)]
    assert dk.main(argv) == 0
    after = target.read_bytes()
    assert after.startswith(before[:-1] + b", ") and json.loads(after) == rows
    assert dk.main(argv) == 1                                   # already present -> refuse


def test_dk_v3_centre_may_sit_on_the_trails_but_legacy_and_v2_keep_the_rounded_midpoint():
    boundary = [10.4513, 56.1298, 10.8473, 56.3204]                     # rounded midpoint [56.2, 10.6]
    v3 = copy.deepcopy(dk.load_registry(REGISTRY)["by_id"][MOLS])
    v3["rules"], v3["center"] = "dk-v3", [56.25, 10.55]
    assert dk.check_area_geometry(v3, boundary, [56.2251, 10.6493], "publish")["status"] == "ok"
    v3["center"] = [57.5, 10.55]                                        # outside the boundary bbox
    assert dk.check_area_geometry(v3, boundary, [56.2251, 10.6493], "publish")["status"] == "failed"
    for rules in ("legacy", "dk-v2"):
        old = copy.deepcopy(v3)
        old["rules"], old["center"] = rules, [56.25, 10.55]
        assert dk.check_area_geometry(old, boundary, [56.2251, 10.6493], "publish")["status"] == "failed"
def test_merge_index_cli_dk_v3_centre_must_be_within_0_1_deg_of_the_trails(tmp_path):
    repo, ids, rows, new = _baseline_repo(tmp_path, 1)
    aid, row = ids[0], copy.deepcopy(rows[1])
    work = tmp_path / "work"
    (work / aid).mkdir(parents=True)
    (repo / f"public/areas/geom/{aid}.json").write_bytes((new / f"geom-{aid}.json").read_bytes())
    sel = tmp_path / "sel.txt"
    sel.write_text(aid + "\n", encoding="utf-8")
    target = repo / "public/areas/index.json"
    for centre, code in (([56.25, 10.55], 0), ([56.5, 10.6], 1)):      # trails sit near [56.21, 10.61]
        row[3], row[4] = centre
        (work / aid / "index.json").write_text(json.dumps([row]), encoding="utf-8")
        reg = repo / "trailforge/golden/dk-areas.json"
        doc = json.loads(reg.read_text(encoding="utf-8"))
        next(a for a in doc["areas"] if a["id"] == aid).update(rules="dk-v3", center=list(centre))
        reg.write_text(json.dumps(doc), encoding="utf-8")
        argv = ["merge-index", "--registry", str(reg), "--selected", str(sel), "--rows-dir", str(work),
                "--geom-dir", str(repo / "public/areas/geom"), "--target", str(target)]
        assert dk.main(argv) == code
# --------------------------------------------------------------- qualify -----

def test_qualify_report_lists_names_routes_removed_categories_and_proposes_tables(tmp_path):
    aid = "nationalpark-thy-dk"
    area = {"id": aid, "name": "Nationalpark Thy", "relation_id": 7045682, "boundary_bbox": [8.1, 56.7, 8.7, 57.1],
            "effective_bbox": [8.05, 56.65, 8.75, 57.15], "bbox_source": "computed", "center": [56.9, 8.4],
            "proposed_bbox": [8.05, 56.65, 8.75, 57.15], "proposed_center": [56.9, 8.4]}
    d = tmp_path / aid
    d.mkdir()
    (d / "area.json").write_text(json.dumps(area), encoding="utf-8")
    (d / "geom.json").write_text(json.dumps(_geom(aid, names=["Thy Route", "Short Loop"], total_mi=5.0)))
    feat = lambda **p: {"type": "Feature", "properties": p, "geometry": None}  # noqa: E731
    (d / "trails.geojson").write_text(json.dumps({"features": [
        feat(name="Thy Route", kind="route", length_mi=4.0), feat(name="Short Loop", kind="hike", length_mi=1.0)]}))
    (d / "removed.geojson").write_text(json.dumps({"features": [
        feat(name="Stage 1", removed_category="unresolved-relation-gap", length_mi=9.5),
        feat(name="Sandvej", removed_category="dk-unqualified-road-track", length_mi=1.2),
        feat(name="Tiny", removed_category="min-length", length_mi=0.01),
        feat(name="Short Loop", removed_category="disconnected-name-stitch", length_mi=0.5)]}))
    (d / "assembly.json").write_text(json.dumps({"pre_clip_trail_count": 10, "post_clip_trail_count": 2}))
    assert dk.main(["report-area", "--area-json", str(d / "area.json"), "--geom", str(d / "geom.json"),
                    "--trails-geojson", str(d / "trails.geojson"), "--removed-geojson", str(d / "removed.geojson"),
                    "--assembly-json", str(d / "assembly.json"), "--out", str(d / "report.json"),
                    "--expected", str(tmp_path / "none.json")]) == 0
    rep = json.loads((d / "report.json").read_text())
    assert rep["trail_names"] == ["Thy Route", "Short Loop"]
    assert rep["route_segments"] == [{"name": "Thy Route", "length_mi": 4.0}]
    assert rep["dropped_unresolved_relation_gap"] == [{"name": "Stage 1", "length_mi": 9.5}]
    assert rep["removed_category_counts"] == {"dk-unqualified-road-track": 1, "disconnected-name-stitch": 1,
                                              "min-length": 1, "unresolved-relation-gap": 1}
    pe = rep["proposed_expected"]
    assert pe["required_names"] == ["Thy Route"]
    assert pe["forbidden_names"] == ["Sandvej", "Stage 1"]      # kept names are never forbidden
    out = tmp_path / "q"
    null_reg = _null_bbox_registry(tmp_path)
    assert dk.main(["qualify-aggregate", "--reports-dir", str(tmp_path), "--out-dir", str(out),
                    "--registry", str(null_reg)]) == 0
    assert json.loads((out / "dk-areas-expected.proposed.json").read_text())["areas"][aid]["trail_count"] == 2
    prop = json.loads((out / "dk-areas.proposed.json").read_text())
    thy = next(a for a in prop["areas"] if a["id"] == aid)
    assert thy["bbox"] == [8.05, 56.65, 8.75, 57.15] and thy["center"] == [56.9, 8.4]
    assert prop["areas"][0]["bbox"] == [10.40, 56.08, 10.90, 56.35]
    (tmp_path / "bad-dk").mkdir()
    dk.main(["mark-failed", "--area-id", "bad-dk", "--stage", "pipeline", "--out", str(tmp_path / "bad-dk/report.json")])
    assert dk.main(["qualify-aggregate", "--reports-dir", str(tmp_path), "--out-dir", str(out),
                    "--registry", str(null_reg)]) == 1


# ------------------------------------------------------- publish_areas guard --

def _guard_argv(tmp, *extra, state="Denmark"):
    return ["--trails", f"{tmp}/t.geojson", "--hiking", f"{tmp}/h.pbf", "--index", f"{tmp}/missing-index.json",
            "--out-dir", f"{tmp}/out", "--state", state, "--exact-boundary", f"{tmp}/exact.geojson", *extra]


def test_publish_areas_guard_accepts_registry_areas_only(tmp_path):
    tmp = str(tmp_path)
    for aid in (MOLS, "nationalpark-thy-dk"):
        ok = ["--no-boundary-fetch", "--no-elevation", "--only-area", aid]
        with pytest.raises(FileNotFoundError):          # guards pass; fails later on the missing index
            publish_areas.main(_guard_argv(tmp, *ok))
    for extra, state in ((["--no-boundary-fetch", "--no-elevation", "--only-area", "not-in-registry-dk"], "Denmark"),
                         (["--no-boundary-fetch", "--only-area", "nationalpark-thy-dk"], "Denmark"),
                         (["--no-boundary-fetch", "--no-elevation"], "Denmark"),
                         (["--no-boundary-fetch", "--no-elevation", "--only-area", "nationalpark-thy-dk"], "Arizona"),
                         (["--no-boundary-fetch", "--no-elevation", "--only-area", "nationalpark-thy-dk",
                           "--preview-json", f"{tmp}/p.json", "--preview-root", tmp], "Denmark")):
        with pytest.raises(SystemExit) as raised:
            publish_areas.main(_guard_argv(tmp, *extra, state=state))
        assert raised.value.code == 2


def test_publish_areas_only_area_still_refuses_non_registry_ids(tmp_path):
    index = tmp_path / "index.json"
    index.write_text(json.dumps([["other-dk", "Other", "Denmark", 56.0, 9.0, None, None, 5]]), encoding="utf-8")
    for aid in ("other-dk",):
        with pytest.raises(SystemExit) as raised:
            publish_areas.main(["--trails", "t", "--hiking", "h", "--index", str(index), "--state", "Denmark",
                                "--out-dir", str(tmp_path / "o"), "--dry-run", "--only-area", aid])
        assert raised.value.code == 2


# --------------------------------------------------- validator discovery ------

def _discovery_doc(rows):
    return {"schema_version": 1, "requested_region_codes": ["DK"], "attribution": validator.ATTRIBUTION,
            "candidate_count": len(rows), "candidates": [{"index_row": r[:5], "osm_relation_id": r[5]} for r in rows]}


def test_validator_discovery_accepts_registry_override_and_keeps_mols_default(tmp_path):
    rows = [[MOLS, "Nationalpark Mols Bjerge", "Denmark", 56.2251, 10.6493, 7046785],
            ["nationalpark-thy-dk", "Nationalpark Thy", "Denmark", 56.9077, 8.4468, 7045682]]
    report = tmp_path / "r.json"
    report.write_text(json.dumps(_discovery_doc(rows)), encoding="utf-8")

    def run(rel, **extra):
        args = Namespace(discovery_report=str(report), overpass_receipt=None, pbf_provenance=None, source_pbf=None,
                         expected_osm_relation_id=str(rel), selected_record_out=str(tmp_path / "sel.json"),
                         index_out=str(tmp_path / "idx.json"), result_json=str(tmp_path / "res.json"), **extra)
        return validator._run_discovery(args), json.loads((tmp_path / "res.json").read_text())

    code, result = run(7046785)                          # unchanged Mols behaviour
    assert code == 0 and result["area_id"] == MOLS
    code, result = run(7045682, expected_area_id="nationalpark-thy-dk", expected_area_name="Nationalpark Thy")
    assert code == 0 and result["area_id"] == "nationalpark-thy-dk"
    assert json.loads((tmp_path / "idx.json").read_text())[0][2:3] == ["Denmark"]
    code, _ = run(7045682, expected_area_id="nationalpark-thy-dk", expected_area_name="Wrong Name")
    assert code == 1
    code, _ = run(7045682)                               # Thy relation but the Mols default id/name
    assert code == 1
    code, _ = run(7045682, expected_area_id="nationalpark-thy-dk", expected_area_name=None)
    assert code == 1                                     # id override without its name is refused


# ------------------------------------------------------------- workflows -----

PUBLISH_DK = (ROOT / ".github/workflows/trailforge-publish-dk.yml").read_text()
SYNC_DK = (ROOT / ".github/workflows/sync-dk-to-r2.yml").read_text()


@pytest.mark.parametrize("name", ["trailforge-publish-dk.yml", "sync-dk-to-r2.yml"])
def test_new_workflows_parse_and_are_dispatch_only(name):
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load((ROOT / ".github/workflows" / name).read_text())
    triggers = doc.get("on", doc.get(True))
    assert list(triggers) == ["workflow_dispatch"]
    assert doc["jobs"]


def test_publish_dk_workflow_contract():
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(PUBLISH_DK)
    inputs = (doc.get("on") or doc.get(True))["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"areas", "mode", "chunk_size"}
    assert inputs["mode"]["options"] == ["qualify", "publish", "update"] and inputs["mode"]["default"] == "qualify"
    assert inputs["chunk_size"]["default"] == "25"
    assert doc["concurrency"]["group"] == "trailforge-publish"
    assert len(doc["jobs"]) == 1                                  # single job, not a matrix
    for forbidden in ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_ACCOUNT_ID", "aws s3", "s3api",
                      "--elevation ", "sync-geom-to-r2.yml", "gh workflow run", "--force"):
        assert forbidden not in PUBLISH_DK, forbidden
    assert '[[ "$RAW_CHUNK" =~ ^([1-9]|[1-3][0-9]|40)$ ]]' in PUBLISH_DK          # hard max 40
    assert 'test "$GITHUB_REF_NAME" = "main"' in PUBLISH_DK
    assert 'test "$(git rev-parse origin/main)" = "$(git rev-parse HEAD^)"' in PUBLISH_DK
    assert "verify-staged" in PUBLISH_DK and "verify-commit" in PUBLISH_DK
    # one Denmark download + discovery, then a per-area loop; publish merge/commit are publish-only
    assert PUBLISH_DK.count("curl -fL") == 1 and PUBLISH_DK.count("seed-areas.py --dry-run DK") == 1
    for step, cond in (("Install blessed geoms", "inputs.mode == 'publish' }}"),
                       ("Install updated geoms", "inputs.mode == 'update' }}"),
                       ("Commit published data", "inputs.mode == 'publish' || inputs.mode == 'update' }}")):
        block = PUBLISH_DK.split(f"- name: {step}", 1)[1].split("\n      - name:", 1)[0]
        assert f"if: ${{{{ {cond}" in block, step
    # qualify never reaches a write step
    assert "git push" not in PUBLISH_DK.split("- name: Commit published data", 1)[0]
    assert "assert-expected" in PUBLISH_DK and "prepare-areas" in PUBLISH_DK


def test_run_area_script_copies_the_mols_simple_exact_pipeline_flags():
    script = (ROOT / "scripts/dk-run-area.sh").read_text()
    for needle in ("validate_publish_pilot.py discovery", "--expected-area-id", "bash extract/aoi.sh",
                   "build_relation_slice.py", "export_relation_members.py", "--only-area \"$AREA_NAME\"",
                   "--require-exact-area", "--expected-area-relation-id \"$RELATION_ID\"",
                   "--relation-authority", "--prefilter-authority", "--aoi-relation-members",
                   "--min-length-mi 0.1 --per-area-merge --region dk", "--no-boundary-fetch", "--no-elevation",
                   "--exact-boundary \"data/aoi/$AREA_ID.exact-area.geojson\"", "--only-area \"$AREA_ID\"",
                   "--state Denmark"):
        assert needle in script, needle
    assert "--dry-run" not in script.split("serve/publish_areas.py", 1)[1]
    assert subprocess.run(["bash", "-n", str(ROOT / "scripts/dk-run-area.sh")]).returncode == 0


def test_sync_dk_workflow_contract():
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(SYNC_DK)
    assert doc["concurrency"]["group"] == "sync-geom-to-r2"
    inputs = (doc.get("on") or doc.get(True))["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"mode", "rollback_run_id"}              # the area list is never an input
    assert inputs["mode"]["options"] == ["plan", "apply", "rollback", "plan-update", "apply-update",
                                         "rollback-update"]
    assert doc["permissions"] == {"contents": "read", "actions": "read"}
    assert sorted(set(re.findall(r"secrets\.([A-Z0-9_]+)", SYNC_DK))) == [
        "R2_ACCESS_KEY_ID", "R2_ACCOUNT_ID", "R2_SECRET_ACCESS_KEY"]
    assert "environment:" not in SYNC_DK and "HMAC" not in SYNC_DK
    # parking.json: snapshotted and ETag-checked, never written
    assert "parking.json" in SYNC_DK and "put cand/parking" not in SYNC_DK
    assert 'put "cand/$file" "$key" "$cc"' in SYNC_DK and "cand/parking" not in SYNC_DK
    assert SYNC_DK.index("Snapshot live R2") < SYNC_DK.index("Save snapshot + candidates") < SYNC_DK.index("- name: Apply")
    assert "PARTIAL APPLY: dispatch mode=rollback with rollback_run_id=$GITHUB_RUN_ID" in SYNC_DK
    assert "trap - EXIT" in SYNC_DK and "rollback-decide" in SYNC_DK and "derive-areas" in SYNC_DK
    assert 'test "$(cur "$key")" = "$(base "$key")"' in SYNC_DK       # ETag race guard
    assert SYNC_DK.count('test "$(cur parking.json)" = "$(base parking.json)"') == 2   # apply + apply-update
    assert "parking_after" in SYNC_DK                                  # rollback checks parking too


def test_every_workflow_holding_r2_write_credentials_shares_the_sync_lock():
    for text in (SYNC_DK, (ROOT / ".github/workflows/sync-mols-to-r2.yml").read_text()):
        assert "group: sync-geom-to-r2" in text and "cancel-in-progress: false" in text
    assert "R2_ACCESS_KEY_ID" not in PUBLISH_DK


def _run_areas_step(tmp_path, mode):
    """Run the real 'Run the exact pipeline per area' script under `bash -e`
    (as GitHub does) with stub pipeline/report scripts; area `b` fails."""
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(PUBLISH_DK)
    step = next(s for s in next(iter(doc["jobs"].values()))["steps"] if s.get("id") == "areas")
    work, temp = tmp_path / "repo", tmp_path / "tmp"
    (work / "scripts").mkdir(parents=True)
    (temp / "dk").mkdir(parents=True)
    ids = ["a", "b", "c"]
    (temp / "dk/selected.txt").write_text("\n".join(ids) + "\n")
    for i in ids:
        (temp / "dk" / i).mkdir()
        (temp / "dk" / i / "area.json").write_text(json.dumps(
            {"status": "ok", "name": i, "relation_id": 1, "bbox_arg": "0,0,1,1"}))
    (work / "scripts/dk-run-area.sh").write_text(
        'echo "pipeline $AREA_ID" >> "$W/calls.log"\n[ "$AREA_ID" = b ] && exit 1\nexit 0\n')
    (work / "scripts/dk_publish.py").write_text(
        "import os, sys\nopen(os.environ['RUNNER_TEMP'] + '/dk/calls.log', 'a')"
        ".write(' '.join(sys.argv[1:3]) + ' ' + sys.argv[3] + '\\n')\n")
    out = tmp_path / "gh_output"
    env = {**os.environ, "RUNNER_TEMP": str(temp), "MODE": mode, "GITHUB_OUTPUT": str(out)}
    proc = subprocess.run(["bash", "-e", "-c", step["run"]], cwd=work, env=env,
                          capture_output=True, text=True)
    calls = (temp / "dk/calls.log").read_text().splitlines()
    return proc, calls, out.read_text()


def test_areas_loop_continues_past_failing_area_in_qualify(tmp_path):
    proc, calls, output = _run_areas_step(tmp_path, "qualify")
    assert proc.returncode == 0, proc.stderr
    assert "pipeline a" in calls and "pipeline b" in calls and "pipeline c" in calls   # c still ran
    assert any(c.startswith("mark-failed --area-id b") for c in calls)
    assert "failed=1" in output                                  # final qualify step fails on this


def test_areas_loop_publish_refuses_when_any_area_failed(tmp_path):
    proc, calls, output = _run_areas_step(tmp_path, "publish")
    assert proc.returncode == 1 and "REFUSED" in proc.stdout
    assert "pipeline c" in calls and "failed=1" in output

def test_every_hold_reason_is_from_the_documented_set():
    reasons = {a["hold"] for a in _reg()["areas"] if a["hold"] is not None}
    assert reasons <= dk.HOLD_REASONS
    assert dk.HOLD_REASONS == {
        "island", "np-nested", "name-collision", "merge-into-klosterhede",
        "duplicate-of-live", "needs-rescope", "merge-pending", "mtb-centre",
        "curated-do-not-ship"}


def test_registry_rejects_an_undocumented_hold_reason(tmp_path):
    with pytest.raises(dk.DkError):
        dk.load_registry(_write_registry(
            tmp_path / "r.json", lambda d: d["areas"][1].update(hold="because")))


def test_generic_names_are_detected_without_inventing_any():
    for name in ("Rute 8", "Spor 3", "gul rute", "Blå Rute", "Rød tur", "Gul", "Syvende Spor",
                 "Rute 14 - rød og 15 - Gul"):
        assert dk.is_generic_trail_name(name), name
    for name in ("Kirkestien", "Den Vilde Vej", "Skelhøj Plantage", "Pamhule - rød rute",
                 "Kollerup Plantage Blå", "Spor ved Tiset", ""):
        assert not dk.is_generic_trail_name(name), name


def _report_for(tmp_path, names):
    aid = "x-dk"
    d = tmp_path / aid
    d.mkdir()
    area = {"id": aid, "name": "X", "relation_id": 1}
    (d / "area.json").write_text(json.dumps(area), encoding="utf-8")
    (d / "geom.json").write_text(json.dumps(_geom(aid, names=names, total_mi=5.0)))
    (d / "trails.geojson").write_text(json.dumps({"features": []}))
    (d / "removed.geojson").write_text(json.dumps({"features": [
        {"type": "Feature", "geometry": None, "properties": {
            "name": "Gammel Vej", "removed_category": "dk-fragment", "length_mi": 0.1}}]}))
    (d / "assembly.json").write_text(json.dumps({}))
    assert dk.main(["report-area", "--area-json", str(d / "area.json"), "--geom", str(d / "geom.json"),
                    "--trails-geojson", str(d / "trails.geojson"), "--removed-geojson",
                    str(d / "removed.geojson"), "--assembly-json", str(d / "assembly.json"),
                    "--out", str(d / "report.json")]) == 0
    return json.loads((d / "report.json").read_text())


def test_report_area_recommends_exclusion_at_half_generic_and_lists_the_rest(tmp_path):
    rep = _report_for(tmp_path, ["Spor 1", "Spor 2", "Kirkestien", "Ulvestien"])
    assert rep["exclude_recommended"] is True and rep["generic_share"] == 0.5
    assert rep["generic_names"] == ["Spor 1", "Spor 2"]
    assert rep["proposed_expected"]["forbidden_names"] == ["Gammel Vej"]   # new categories count as junk


def test_report_area_keeps_an_area_with_few_generic_names(tmp_path):
    rep = _report_for(tmp_path, ["Gul rute", "Kirkestien", "Ulvestien", "Bakkestien"])
    assert rep["exclude_recommended"] is False
    assert rep["generic_names"] == ["Gul rute"]

# ------------------------------------------------------ curated exclusions --
GOOD_EXCLUDE = {"id": "r123", "name": "Test Sti", "reason": "reviewed: road"}
def test_registry_accepts_exclude_trails_on_dk_v2_entries(tmp_path):
    def mutate(d):
        d["areas"][1]["exclude_trails"] = [GOOD_EXCLUDE, {"id": "n:Foo Sti", "name": "Foo Sti", "reason": "x"}]
    reg = dk.load_registry(_write_registry(tmp_path / "r.json", mutate))
    assert len(reg["areas"][1]["exclude_trails"]) == 2
@pytest.mark.parametrize("value", [
    [], "r123", [{"id": "123", "name": "A", "reason": "x"}],
    [{**GOOD_EXCLUDE, "reason": " "}], [{**GOOD_EXCLUDE, "extra": 1}], [GOOD_EXCLUDE, GOOD_EXCLUDE],
    [{"id": "n:A", "name": "B", "reason": "x"}],
])
def test_registry_rejects_bad_exclude_trails(tmp_path, value):
    with pytest.raises(dk.DkError):
        dk.load_registry(_write_registry(
            tmp_path / "r.json", lambda d: d["areas"][1].update(exclude_trails=value)))
def test_registry_rejects_exclude_trails_on_the_legacy_mols_entry(tmp_path):
    with pytest.raises(dk.DkError):
        dk.load_registry(_write_registry(
            tmp_path / "r.json", lambda d: d["areas"][0].update(exclude_trails=[GOOD_EXCLUDE])))
# ------------------------------------------------------ curated inclusions --
def test_registry_accepts_include_trails_only_on_dk_v3_entries(tmp_path):
    def v3(d):
        d["areas"][1].update(rules="dk-v3", include_trails=[
            GOOD_EXCLUDE, {"id": "n:Foo Sti", "name": "Foo Sti", "reason": "x"}])
    reg = dk.load_registry(_write_registry(tmp_path / "r.json", v3))
    assert len(reg["areas"][1]["include_trails"]) == 2
    for rules, idx in (("dk-v2", 1), ("legacy", 0)):
        with pytest.raises(dk.DkError):
            dk.load_registry(_write_registry(
                tmp_path / "r2.json",
                lambda d, rules=rules, idx=idx: d["areas"][idx].update(
                    rules=rules, include_trails=[GOOD_EXCLUDE])))
@pytest.mark.parametrize("value", [
    [], "r123", [{"id": "123", "name": "A", "reason": "x"}],
    [{**GOOD_EXCLUDE, "reason": " "}], [{**GOOD_EXCLUDE, "extra": 1}], [GOOD_EXCLUDE, GOOD_EXCLUDE],
    [{"id": "n:A", "name": "B", "reason": "x"}],
])
def test_registry_rejects_bad_include_trails(tmp_path, value):
    with pytest.raises(dk.DkError):
        dk.load_registry(_write_registry(
            tmp_path / "r.json",
            lambda d: d["areas"][1].update(rules="dk-v3", include_trails=value)))
def test_dk_v3_entry_may_carry_exclude_trails_too(tmp_path):
    reg = dk.load_registry(_write_registry(
        tmp_path / "r.json",
        lambda d: d["areas"][1].update(rules="dk-v3", exclude_trails=[GOOD_EXCLUDE])))
    assert reg["areas"][1]["exclude_trails"] == [GOOD_EXCLUDE]
def test_report_area_lists_whole_routes_only_when_present(tmp_path):
    def run(features):
        d = tmp_path / ("x%d-dk" % len(features))
        d.mkdir()
        (d / "area.json").write_text(json.dumps({"id": "x-dk", "name": "X", "relation_id": 1}), encoding="utf-8")
        (d / "geom.json").write_text(json.dumps(_geom("x-dk", names=["Kirkestien"], total_mi=5.0)))
        (d / "trails.geojson").write_text(json.dumps({"features": features}))
        (d / "removed.geojson").write_text(json.dumps({"features": []}))
        (d / "assembly.json").write_text(json.dumps({"pre_clip_trail_count": 2, "post_clip_trail_count": 1}))
        assert dk.main(["report-area", "--area-json", str(d / "area.json"), "--geom", str(d / "geom.json"),
                        "--trails-geojson", str(d / "trails.geojson"), "--removed-geojson", str(d / "removed.geojson"),
                        "--assembly-json", str(d / "assembly.json"), "--out", str(d / "report.json")]) == 0
        return json.loads((d / "report.json").read_text())
    whole = {"type": "Feature", "geometry": None, "properties": {
        "name": "Hele Ruten", "length_mi": 6.6, "dk_whole_route": True, "dk_route_share": 0.56}}
    plain = {"type": "Feature", "geometry": None, "properties": {"name": "Kirkestien", "length_mi": 1.0}}
    assert run([whole, plain])["whole_routes"] == [
        {"name": "Hele Ruten", "length_mi": 6.6, "area_share": 0.56}]
    assert "whole_routes" not in run([plain])
def test_real_registry_exclusions_and_inclusions_have_reasons_and_live_areas_stay_dk_v2():
    for a in dk.load_registry(REGISTRY)["areas"]:
        for item in a.get("exclude_trails", []) + a.get("include_trails", []):
            assert item["reason"].strip()
        if a["rules"] != "dk-v3":
            assert "include_trails" not in a
    live = {p.stem for p in (ROOT / "public/areas/geom").glob("*-dk.json")}
    reg = dk.load_registry(REGISTRY)["by_id"]
    blessed = set(dk.load_expected(EXPECTED))
    assert all(reg[i]["rules"] != "dk-v3" or i in blessed for i in live if i in reg)  # live dk-v3 areas are blessed ones
def test_report_area_lists_curated_exclusions_and_forbids_them(tmp_path):
    d = tmp_path / "x-dk"
    d.mkdir()
    (d / "area.json").write_text(json.dumps({"id": "x-dk", "name": "X", "relation_id": 1}), encoding="utf-8")
    (d / "geom.json").write_text(json.dumps(_geom("x-dk", names=["Kirkestien"], total_mi=5.0)))
    (d / "trails.geojson").write_text(json.dumps({"features": []}))
    (d / "removed.geojson").write_text(json.dumps({"features": [{"type": "Feature", "geometry": None, "properties": {
        "name": "Skovvejen", "removed_category": "dk-curated-exclude", "length_mi": 1.25,
        "curated_exclude_id": "r9", "curated_exclude_reason": "forest road"}}]}))
    (d / "assembly.json").write_text(json.dumps({"pre_clip_trail_count": 2, "post_clip_trail_count": 1}))
    assert dk.main(["report-area", "--area-json", str(d / "area.json"), "--geom", str(d / "geom.json"),
                    "--trails-geojson", str(d / "trails.geojson"), "--removed-geojson", str(d / "removed.geojson"),
                    "--assembly-json", str(d / "assembly.json"), "--out", str(d / "report.json")]) == 0
    rec = json.loads((d / "report.json").read_text(encoding="utf-8"))
    assert rec["curated_excluded"] == [{"id": "r9", "name": "Skovvejen", "length_mi": 1.25, "reason": "forest road"}]
    assert rec["removed_category_counts"] == {"dk-curated-exclude": 1}
    assert rec["proposed_expected"]["forbidden_names"] == ["Skovvejen"]
# ------------------------------------------------ cross-area duplicate gate ----
def _line(lat0, lon0, n=80, step=0.001):
    return [[round(lat0 + i * step, 5), round(lon0 + i * step / 2, 5)] for i in range(n)]
def _dup_report(tmp_path, aid, trails, *, live_dir=None, relations=None, whole=()):
    """Write one area's report-area inputs and run the real `report-area` CLI.
    trails: {name: segment points}; relations: {name: [relation ids]}; whole: names published whole."""
    d = tmp_path / aid
    d.mkdir()
    (d / "area.json").write_text(json.dumps({"id": aid, "name": aid, "relation_id": 1}), encoding="utf-8")
    geom = {"id": aid, "name": aid, "trail_count": len(trails), "total_mi": float(len(trails)),
            "trails": [{"id": f"t{i}", "name": n, "distanceMi": 1.0, "difficulty": "Easy", "segments": [seg]}
                       for i, (n, seg) in enumerate(trails.items())]}
    (d / "geom.json").write_text(json.dumps(geom), encoding="utf-8")
    (d / "trails.geojson").write_text(json.dumps({"features": [
        {"type": "Feature", "geometry": None, "properties": {
            "name": n, "kind": "trail", "length_mi": 1.0, "relation_ids": (relations or {}).get(n, []),
            **({"dk_whole_route": True} if n in whole else {})}} for n in trails]}), encoding="utf-8")
    (d / "removed.geojson").write_text(json.dumps({"features": []}), encoding="utf-8")
    (d / "assembly.json").write_text(json.dumps({"pre_clip_trail_count": 2, "post_clip_trail_count": 1}), encoding="utf-8")
    argv = ["report-area", "--area-json", str(d / "area.json"), "--geom", str(d / "geom.json"),
            "--trails-geojson", str(d / "trails.geojson"), "--removed-geojson", str(d / "removed.geojson"),
            "--assembly-json", str(d / "assembly.json"), "--out", str(d / "report.json"),
            "--live-geom-dir", str(live_dir or tmp_path / "no-live")]
    assert dk.main(argv) == 0
def _aggregate(tmp_path, capsys):
    out = tmp_path / "q"
    rc = dk.main(["qualify-aggregate", "--reports-dir", str(tmp_path), "--out-dir", str(out),
                  "--registry", str(REGISTRY)])
    return rc, capsys.readouterr().err, out
def test_two_selected_areas_sharing_a_whole_relation_fail_closed_and_name_both(tmp_path, capsys):
    _dup_report(tmp_path, "a-dk", {"Hele Ruten": _line(56.2, 10.6)}, relations={"Hele Ruten": [77]}, whole=("Hele Ruten",))
    _dup_report(tmp_path, "b-dk", {"Hele Ruten (kopi)": _line(56.5, 10.9)}, relations={"Hele Ruten (kopi)": [77]},
                whole=("Hele Ruten (kopi)",))
    rc, err, out = _aggregate(tmp_path, capsys)
    assert rc == 1
    assert "a-dk 'Hele Ruten' duplicates area b-dk 'Hele Ruten (kopi)' (relation r77 whole in both)" in err
    conflicts = json.loads((out / "duplicate-conflicts.json").read_text())["conflicts"]
    assert [(c["area"], c["other_area"], c["other_is_live"]) for c in conflicts] == [("a-dk", "b-dk", False)]
    assert "## Cross-area duplicates" in (out / "qualify-report.md").read_text()
def test_identical_geometry_in_two_selected_areas_fails_closed_even_with_different_names(tmp_path, capsys):
    _dup_report(tmp_path, "a-dk", {"Rute Et": _line(56.2, 10.6)})
    _dup_report(tmp_path, "b-dk", {"Rute To": list(reversed(_line(56.2, 10.6)))})    # same ground, reversed
    rc, err, _ = _aggregate(tmp_path, capsys)
    assert rc == 1 and "a-dk 'Rute Et' duplicates area b-dk 'Rute To' (identical geometry)" in err
def test_same_name_over_the_same_ground_fails_but_other_cases_pass(tmp_path, capsys):
    _dup_report(tmp_path, "a-dk", {"Skovturen": _line(56.2, 10.6), "Andet": _line(56.3, 10.7)})
    _dup_report(tmp_path, "b-dk", {"Skovturen": _line(56.2, 10.6)[:50] + _line(57.0, 11.0, 30),   # 50 of 80 shared
                                   "Andet": _line(56.9, 11.5)})                                   # same name, other ground
    rc, err, _ = _aggregate(tmp_path, capsys)
    assert rc == 1
    assert "'Skovturen' duplicates area b-dk 'Skovturen' (same name over the same ground)" in err
    assert "'Andet'" not in err                                                                  # same name elsewhere is fine
def test_different_names_over_overlapping_ground_are_not_duplicates(tmp_path, capsys):
    _dup_report(tmp_path, "a-dk", {"Rute Et": _line(56.2, 10.6)})
    _dup_report(tmp_path, "b-dk", {"Rute To": _line(56.2, 10.6)[:50] + _line(57.0, 11.0, 30)})
    rc, err, out = _aggregate(tmp_path, capsys)
    assert rc == 0 and err == ""
    assert json.loads((out / "duplicate-conflicts.json").read_text()) == {"conflicts": []}
def test_selected_area_duplicating_a_live_area_fails_closed_naming_the_live_area(tmp_path, capsys):
    live = tmp_path / "live"
    live.mkdir()
    (live / "old-park-dk.json").write_text(json.dumps(_geom_with_line("old-park-dk", "Kongestien", _line(56.2, 10.6))))
    (live / "new-dk.json").write_text(json.dumps(_geom_with_line("new-dk", "Selvet", _line(56.2, 10.6))))
    _dup_report(tmp_path, "new-dk", {"Kongestien": _line(56.2, 10.6)}, live_dir=live)      # own live file is ignored
    rep = json.loads((tmp_path / "new-dk" / "report.json").read_text())
    assert [(c["other_area"], c["other_trail"]) for c in rep["live_duplicates"]] == [("old-park-dk", "Kongestien")]
    rc, err, _ = _aggregate(tmp_path, capsys)
    assert rc == 1 and "new-dk 'Kongestien' duplicates live area old-park-dk 'Kongestien'" in err
def test_selected_area_that_is_itself_live_is_not_compared_with_its_own_live_copy(tmp_path, capsys):
    live = tmp_path / "live"
    live.mkdir()
    (live / "same-dk.json").write_text(json.dumps(_geom_with_line("same-dk", "Egenstien", _line(56.2, 10.6))))
    _dup_report(tmp_path, "same-dk", {"Egenstien": _line(56.2, 10.6)}, live_dir=live)
    rc, err, _ = _aggregate(tmp_path, capsys)
    assert rc == 0 and err == ""
def _geom_with_line(aid, name, segment):
    return {"id": aid, "name": aid, "trail_count": 1, "total_mi": 1.0,
            "trails": [{"id": "t0", "name": name, "distanceMi": 1.0, "difficulty": "Easy", "segments": [segment]}]}
def test_real_live_danish_areas_have_no_duplicates_among_themselves():
    geoms = sorted((ROOT / "public/areas/geom").glob("*-dk.json"))
    assert len(geoms) >= 15
    reports = []
    for path in geoms:
        geom = json.loads(path.read_text(encoding="utf-8"))
        reports.append({"id": geom["id"], "trail_identities": [dk.trail_identity(t) for t in geom["trails"]]})
    assert dk.selected_conflicts(reports) == []
# ------------------------------------------------------------ display_name ----
def test_registry_accepts_a_display_name_and_rejects_bad_ones(tmp_path):
    reg = dk.load_registry(_write_registry(
        tmp_path / "r.json", lambda d: d["areas"][1].update(display_name="Thy Nationalpark")))
    assert reg["areas"][1]["display_name"] == "Thy Nationalpark"
    for bad in ("", "  ", 5, reg["areas"][1]["name"], "Mo\u0308ns Klint"):
        with pytest.raises(dk.DkError):
            dk.load_registry(_write_registry(
                tmp_path / "r2.json", lambda d, b=bad: d["areas"][1].update(display_name=b)))
def test_prepare_areas_writes_the_display_name_into_the_row_but_keeps_the_osm_name(tmp_path):
    def mutate(doc):
        a = next(a for a in doc["areas"] if a["id"] == FOREST)
        a.update(bbox=None, center=None, display_name="Nordskoven Nord")
    registry = _write_registry(tmp_path / "display-registry.json", mutate)
    rc, area, out = _forest_prepare(tmp_path, registry=registry)
    assert rc == 0 and area["status"] == "ok"
    name = dk.load_registry(registry)["by_id"][FOREST]["name"]
    assert area["name"] == name and area["display_name"] == "Nordskoven Nord"      # OSM name drives the pipeline
    row = json.loads((out / FOREST / "index.json").read_text())[0]
    assert row[1] == "Nordskoven Nord"                                              # app-visible name
def test_check_new_row_expects_the_display_name(tmp_path):
    registry = dk.load_registry(_write_registry(
        tmp_path / "r.json", lambda d: d["areas"][1].update(display_name="Thy Nationalpark")))
    entry = registry["areas"][1]
    geom = _geom(entry["id"], names=["A"], total_mi=1.0)
    row = [entry["id"], "Thy Nationalpark", "Denmark", entry["center"][0], entry["center"][1], 1, 1.0,
           entry["relation_id"]]
    dk.check_new_row(row, entry, geom, "t")
    row[1] = entry["name"]
    with pytest.raises(dk.DkError):
        dk.check_new_row(row, entry, geom, "t")
def test_publish_areas_allowlist_pins_the_display_name_and_still_knows_the_osm_name(tmp_path):
    registry = _write_registry(tmp_path / "r.json", lambda d: next(
        a for a in d["areas"] if a["id"] == FOREST).update(display_name="Nordskoven Nord"))
    osm_name = dk.load_registry(registry)["by_id"][FOREST]["name"]
    rel = dk.load_registry(registry)["by_id"][FOREST]["relation_id"]
    assert publish_areas._exact_publish_allowlist(registry)[FOREST] == ("Nordskoven Nord", rel)
    assert publish_areas._exact_osm_name(FOREST, registry) == osm_name
    assert publish_areas._exact_publish_allowlist(REGISTRY)[MOLS] == ("Nationalpark Mols Bjerge", 7046785)
# ------------------------------------------------------------ Phase 1c flagships ----
FLAGSHIPS = {"rold-skov-dk": "Rold Skov", "mons-klint-dk": "Møns Klint",
             "randbol-hede-frederikshab-dk": "Randbøl Hede og Frederikshåb Plantage",
             "nymindegab-blabjerg-dk": "Nymindegab og Blåbjerg Klitplantage"}
def test_flagships_are_dk_v3_unheld_with_written_reasons_and_their_siblings_stay_held():
    reg = dk.load_registry(REGISTRY)["by_id"]
    for aid, shown in FLAGSHIPS.items():
        a = reg[aid]
        assert a["rules"] == "dk-v3" and a["hold"] is None
        assert shown in (a.get("display_name"), a["name"])
        for item in a.get("exclude_trails", []) + a.get("include_trails", []):
            assert len(item["reason"]) > 20
    assert reg["mons-klint-dk"]["name"] == "Store Klinteskov" and "store-klinteskov-dk" not in reg
    for sibling in ("randbol-hede-dk", "blabjerg-klitplantage-dk"):
        assert reg[sibling]["hold"] == "merge-pending"
    # a registry include never repeats an exclude of the same id
    for a in reg.values():
        assert not {i["id"] for i in a.get("include_trails", [])} & {i["id"] for i in a.get("exclude_trails", [])}



# ====================================================== UPDATE mode ==========
# Republish EXISTING Danish areas: publish-side (in-place index rows, only-modified
# 2N+2 allowlist) and R2-side (overwrite 2N keys, in-place shared-file splice, rollback).

OLD_SIL = {"b": [0, 0, 2, 2], "l": [[3, 4]]}


def _set_rules_v2(repo):
    path = repo / "trailforge/golden/dk-areas.json"
    doc = json.loads(path.read_text())
    for entry in doc["areas"]:
        entry["rules"] = "dk-v2"
    path.write_text(json.dumps(doc), encoding="utf-8")


def _update_fixture(tmp_path, total, upd, *, row_changes=True):
    """Repo whose committed (HEAD) state is the OLD published state; the working tree already
    holds the NEW geoms/silhouettes for the `upd` positions. live/ = OLD R2 bytes.
    `finish(fx)` applies the CLI replace-index step and writes the NEW bundle."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    repo, live, ids = _make_repo(tmp_path, total)
    _set_rules_v2(repo)
    upd_ids = [ids[i] for i in upd]
    reg = dk.load_registry(repo / "trailforge/golden/dk-areas.json")
    bundle_path = repo / "ios/SouthMountainExplorer/Resources/areas-index.json"
    new_rows = json.loads(bundle_path.read_bytes())[1:]
    new_geoms = {a: (repo / f"public/areas/geom/{a}.json").read_bytes() for a in ids}
    new_sils = {a: (repo / f"public/areas/silhouettes/{a}.json").read_bytes() for a in ids}
    old_geoms, old_sils, old_rows = {}, {}, []
    for aid, row in zip(ids, new_rows):
        if aid in upd_ids:
            if row_changes:
                g = _geom(aid, trail_count=3, total_mi=4.0)
                old_rows.append([*row[:5], 3, 4.0, row[7]])
            else:
                g = _geom(aid, names=[f"{aid} Old A", f"{aid} Old B"])
                old_rows.append(list(row))
            old_geoms[aid], old_sils[aid] = _compact(g), _compact(OLD_SIL)
        else:
            old_geoms[aid], old_sils[aid] = new_geoms[aid], new_sils[aid]
            old_rows.append(list(row))
    # committed OLD state
    for aid in ids:
        (repo / f"public/areas/geom/{aid}.json").write_bytes(old_geoms[aid])
        (repo / f"public/areas/silhouettes/{aid}.json").write_bytes(old_sils[aid])
    (repo / "public/areas/index.json").write_bytes(_default([OLD_ROW, *old_rows]))
    bundle_path.write_bytes(_compact([OLD_ROW, *old_rows]))
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "old published state")
    # live R2 = old state (shared files = the old records in area order)
    search = [["Old Trail", "old-area-co", "a", 1.0, "Easy"]]
    shapes = {"old-area-co/a": [0, 0, 255, 255]}
    for aid in ids:
        search += dk.expected_records("trail-search.json", aid, old_geoms[aid])
        shapes.update({r[0]: r[1] for r in dk.expected_records("trail-shapes.json", aid, old_geoms[aid])})
    (live / "index.json").write_bytes(_compact([OLD_ROW, *old_rows]))
    (live / "trail-search.json").write_bytes(_compact(search))
    (live / "trail-shapes.json").write_bytes(_compact(shapes))
    areas = tmp_path / "liveareas"
    (areas / "silhouettes").mkdir(parents=True)
    for aid in ids:
        (areas / f"{aid}.json").write_bytes(old_geoms[aid])
        (areas / "silhouettes" / f"{aid}.json").write_bytes(old_sils[aid])
    # NEW geoms/silhouettes in the working tree (what the dk pipeline + silhouette script write)
    for aid in upd_ids:
        (repo / f"public/areas/geom/{aid}.json").write_bytes(new_geoms[aid])
        (repo / f"public/areas/silhouettes/{aid}.json").write_bytes(new_sils[aid])
    return {"repo": repo, "live": live, "areas": areas, "ids": ids, "upd": upd_ids, "reg": reg,
            "new_rows": new_rows, "old_rows": old_rows, "tmp": tmp_path}


def _finish(fx, *, stage=True):
    """The workflow's index steps: replace-index via the real CLI, then the regenerated bundle."""
    repo, tmp_path = fx["repo"], fx["tmp"]
    work = tmp_path / "work"
    for aid, row in zip(fx["ids"], fx["new_rows"]):
        if aid in fx["upd"]:
            (work / aid).mkdir(parents=True, exist_ok=True)
            (work / aid / "index.json").write_text(json.dumps([row]), encoding="utf-8")
    sel = tmp_path / "sel.txt"
    sel.write_text("\n".join(fx["upd"]) + "\n", encoding="utf-8")
    rc = dk.main(["replace-index", "--registry", str(repo / "trailforge/golden/dk-areas.json"),
                  "--selected", str(sel), "--rows-dir", str(work),
                  "--geom-dir", str(repo / "public/areas/geom"), "--target", str(repo / "public/areas/index.json")])
    final_rows = json.loads((repo / "public/areas/index.json").read_bytes())
    (repo / "ios/SouthMountainExplorer/Resources/areas-index.json").write_bytes(_compact(final_rows))
    if stage:
        _git(repo, "add", "public/areas", "ios/SouthMountainExplorer/Resources/areas-index.json")
    return rc


def _derive(fx, out=None):
    out = out or fx["tmp"] / "areas.txt"
    rc = dk.main(["derive-update-areas", "--repo", str(fx["repo"]), "--live-index", str(fx["live"] / "index.json"),
                  "--live-areas-dir", str(fx["areas"]), "--out", str(out)])
    return rc, out


def _build_update(fx, out):
    return dk.main(["build-update-candidates", "--repo", str(fx["repo"]),
                    "--live-index", str(fx["live"] / "index.json"),
                    "--live-search", str(fx["live"] / "trail-search.json"),
                    "--live-shapes", str(fx["live"] / "trail-shapes.json"),
                    "--live-areas-dir", str(fx["areas"]), "--out-dir", str(out)])


def _pieces(data, as_dict):
    return [raw for _, raw in dk._split_records(data, as_dict, "t")]


UPDATE_CASES = [(1, [0]), (5, [0, 2, 4]), (45, [i for i in range(45) if i % 10 != 9][:40])]


@pytest.mark.parametrize("total,upd", UPDATE_CASES, ids=["N1", "N3", "N40"])
def test_update_splice_replaces_exactly_the_intended_records(tmp_path, total, upd):
    fx = _update_fixture(tmp_path, total, upd)
    assert _finish(fx) == 0
    rc, areas_file = _derive(fx)
    assert rc == 0 and areas_file.read_text().split() == sorted(fx["upd"])
    out = tmp_path / "out"
    parking = (fx["live"] / "parking.json").read_bytes()
    assert _build_update(fx, out) == 0
    plan = json.loads((out / "plan.json").read_text())
    n = len(fx["upd"])
    keys = [w["key"] for w in plan["writes"]]
    ids = sorted(fx["upd"])
    assert len(keys) == 2 * n + 3 and not any("parking" in k for k in keys)
    assert keys[:n] == [f"{i}.json" for i in ids] and keys[n:2 * n] == [f"silhouettes/{i}.json" for i in ids]
    assert keys[2 * n:] == ["trail-shapes.json", "trail-search.json", "index.json"]       # index last
    assert sorted(plan["old_objects"]) == sorted(keys[:2 * n])
    for key, meta in plan["old_objects"].items():
        assert meta["sha256"] == dk._sha((fx["areas"] / key).read_bytes())               # OLD bytes recorded
    assert (fx["live"] / "parking.json").read_bytes() == parking
    for key, as_dict, owner in (("trail-search.json", False, lambda r: r[1]),
                                ("trail-shapes.json", True, lambda r: r[0].split("/")[0])):
        old, new = (fx["live"] / key).read_bytes(), (out / key).read_bytes()
        old_p, new_p = dk._split_records(old, as_dict, "o"), dk._split_records(new, as_dict, "n")
        keep_old = [raw for rec, raw in old_p if owner(rec) not in ids]
        keep_new = [raw for rec, raw in new_p if owner(rec) not in ids]
        assert keep_old == keep_new                                                      # byte-identical
        # area blocks stay at their original relative position (order preserved)
        seq = lambda pieces: [k for i, k in enumerate(owner(r) for r, _ in pieces) if i == 0 or k != owner(pieces[i - 1][0])]  # noqa: E731
        assert seq(old_p) == seq(new_p)
        assert sum(1 for r, _ in new_p if owner(r) in ids) == 2 * n                       # 3 old -> 2 new trails
        assert sum(1 for r, _ in old_p if owner(r) in ids) == 3 * n
    old_idx, new_idx = json.loads((fx["live"] / "index.json").read_bytes()), json.loads((out / "index.json").read_bytes())
    assert [r[0] for r in new_idx] == [r[0] for r in old_idx]                            # order preserved
    for o, nw in zip(old_idx, new_idx):
        assert (nw != o) == (nw[0] in ids)
    assert new_idx == json.loads((fx["repo"] / "ios/SouthMountainExplorer/Resources/areas-index.json").read_bytes())
    for w in plan["writes"]:
        assert dk._sha((out / w["file"]).read_bytes()) == w["sha256"]


def test_update_derivation_cap_and_refusals(tmp_path):
    fx = _update_fixture(tmp_path, 41, list(range(41)))
    # replace-index itself refuses 41; write the bundle directly to prove derivation caps too
    assert _finish(fx) == 1
    (fx["repo"] / "ios/SouthMountainExplorer/Resources/areas-index.json").write_bytes(
        _compact([OLD_ROW, *fx["new_rows"]]))
    assert _derive(fx)[0] == 1                                       # 41 > cap 40
    fx2 = _update_fixture(tmp_path / "b", 3, [1])
    assert _finish(fx2) == 0 and _derive(fx2)[0] == 0
    # nothing changed -> nothing to update
    fx3 = _update_fixture(tmp_path / "c", 2, [])
    assert _derive(fx3)[0] == 1
    # a new area in the bundle (append) is not an update
    bundle = fx2["repo"] / "ios/SouthMountainExplorer/Resources/areas-index.json"
    rows = json.loads(bundle.read_bytes())
    bundle.write_bytes(_compact(rows[:-1]))
    assert _derive(fx2)[0] == 1
    # a non-Danish live row that differs is refused
    bundle.write_bytes(_compact([[*OLD_ROW[:1], "Renamed", *OLD_ROW[2:]], *rows[1:]]))
    assert _derive(fx2)[0] == 1
    # an updated area whose live key is missing: the 2N keys must exist
    bundle.write_bytes(_compact(rows))
    (fx2["areas"] / f"{fx2['ids'][1]}.json").unlink()
    assert _derive(fx2)[0] == 1


def test_update_refuses_unblessed_or_legacy_area_at_derivation(tmp_path):
    fx = _update_fixture(tmp_path, 2, [0])
    assert _finish(fx) == 0
    reg_path = fx["repo"] / "trailforge/golden/dk-areas.json"
    doc = json.loads(reg_path.read_text())
    doc["areas"][0]["rules"] = "legacy"
    reg_path.write_text(json.dumps(doc), encoding="utf-8")
    assert _derive(fx)[0] == 1                                       # legacy needs allow_update
    doc["areas"][0]["allow_update"] = True
    reg_path.write_text(json.dumps(doc), encoding="utf-8")
    assert _derive(fx)[0] == 0
    exp_path = fx["repo"] / "trailforge/golden/dk-areas-expected.json"
    table = json.loads(exp_path.read_text())
    del table["areas"][fx["ids"][0]]
    exp_path.write_text(json.dumps(table), encoding="utf-8")
    assert _derive(fx)[0] == 1                                       # not blessed


def test_update_build_refuses_non_contiguous_blocks_and_unblessed_results(tmp_path):
    fx = _update_fixture(tmp_path, 3, [1])
    assert _finish(fx) == 0
    search_path = fx["live"] / "trail-search.json"
    original = search_path.read_bytes()
    rows = json.loads(original)
    aid = fx["upd"][0]
    mine = [r for r in rows if r[1] == aid]
    others = [r for r in rows if r[1] != aid]
    search_path.write_bytes(_compact([*others[:3], mine[0], *others[3:], *mine[1:]]))   # block split in two
    assert _build_update(fx, tmp_path / "a") == 1
    search_path.write_bytes(_compact([r for r in rows if r[1] != aid]))                  # no records at all
    assert _build_update(fx, tmp_path / "b") == 1
    search_path.write_bytes(original)
    assert _build_update(fx, tmp_path / "ok") == 0
    # result must be the blessed result
    exp_path = fx["repo"] / "trailforge/golden/dk-areas-expected.json"
    table = json.loads(exp_path.read_text())
    table["areas"][aid]["trail_count"] = 9
    exp_path.write_text(json.dumps(table), encoding="utf-8")
    assert _build_update(fx, tmp_path / "c") == 1


def test_update_select_refuses_new_areas_and_publish_still_refuses_existing(tmp_path, capsys):
    reg = tmp_path / "reg.json"
    reg.write_text(json.dumps({k: v for k, v in _big_registry(tmp_path, 3).items() if k != "by_id"}))
    doc = json.loads(reg.read_text())
    for e in doc["areas"]:
        e["rules"] = "dk-v2"
    reg.write_text(json.dumps(doc), encoding="utf-8")
    index = tmp_path / "index.json"
    index.write_bytes(_default([OLD_ROW, ["area-000-dk", "Test Area 0", "Denmark", 56.3, 9.3, 2, 3.0, 1000],
                                ["area-001-dk", "Test Area 1", "Denmark", 56.3, 9.3, 2, 3.0, 1001]]))
    exp = tmp_path / "exp.json"
    exp.write_text(json.dumps({"schema": dk.EXPECTED_SCHEMA, "areas": {
        a: {"trail_count": 2, "total_mi": 3.0, "total_mi_tolerance": 0.05, "required_names": [],
            "forbidden_names": []} for a in ("area-000-dk", "area-001-dk", "area-002-dk")}}))

    def select(areas, mode):
        return dk.main(["select", "--registry", str(reg), "--index", str(index), "--expected", str(exp),
                        "--areas", areas, "--mode", mode, "--out", str(tmp_path / "sel.txt")])
    assert select("area-000-dk,area-001-dk", "update") == 0
    assert (tmp_path / "sel.txt").read_text().split() == ["area-000-dk", "area-001-dk"]
    assert select("area-002-dk", "update") == 1                      # not published yet: update refuses new areas
    assert "not in public/areas/index.json" in capsys.readouterr().err
    assert select("batch=1", "update") == 1                          # explicit ids only
    assert select("area-000-dk", "publish") == 1                     # publish still refuses existing areas
    assert select("area-002-dk", "publish") == 0                     # ...and still accepts new ones
    exp_doc = json.loads(exp.read_text())
    del exp_doc["areas"]["area-001-dk"]
    exp.write_text(json.dumps(exp_doc), encoding="utf-8")
    assert select("area-001-dk", "update") == 1                      # must be blessed
    assert select("area-000-dk,area-000-dk", "update") == 1          # duplicate id


def test_update_refuses_mols_and_legacy_unless_the_registry_flag_allows_it(tmp_path):
    real_index = ROOT / "public/areas/index.json"
    assert MOLS in dk._index_ids(real_index)                          # Mols is live, so only the legacy rule can stop it
    out = tmp_path / "sel.txt"
    base = ["select", "--index", str(real_index), "--areas", MOLS, "--mode", "update", "--out", str(out)]
    assert dk.main(base) == 1 and not out.exists()                    # default: refused
    flagged = tmp_path / "reg.json"
    _write_registry(flagged, lambda d: d["areas"][0].update(allow_update=True))
    assert dk.main([*base, "--registry", str(flagged)]) == 0 and out.read_text().split() == [MOLS]
    # bool only
    bad = _write_registry(tmp_path / "bad.json", lambda d: d["areas"][0].update(allow_update="yes"))
    assert dk.main([*base, "--registry", str(bad)]) == 1
    # a dk-v2 live park is selectable, the committed registry carries no allow_update flag
    assert all("allow_update" not in a for a in json.loads(REGISTRY.read_text())["areas"])
    assert dk.main(["select", "--index", str(real_index), "--areas", "nationalpark-thy-dk", "--mode", "update",
                    "--out", str(out)]) == 0


# ------------------------------------------------ update rollback table ------

def _update_plan(tmp_path, capsys):
    fx = _update_fixture(tmp_path, 3, [0, 2])
    assert _finish(fx) == 0
    out = tmp_path / "out"
    assert _build_update(fx, out) == 0
    capsys.readouterr()
    return fx, out


def _live_state(fx, out, tmp_path, spec):
    """spec: key -> 'cand' | 'old' | 'other'. Unlisted keys default to `default`."""
    plan = json.loads((out / "plan.json").read_text())
    cur = tmp_path / "cur"
    for w in plan["writes"]:
        key = w["key"]
        what = spec.get(key, spec["*"])
        if key in SHARED:
            old = (fx["live"] / key).read_bytes()
        else:
            old = (fx["areas"] / key).read_bytes()
        data = {"cand": (out / w["file"]).read_bytes(), "old": old, "other": b'{"third":"party"}'}[what]
        (cur / key).parent.mkdir(parents=True, exist_ok=True)
        (cur / key).write_bytes(data)
    return plan, cur


def _decide_update(fx, out, tmp_path, capsys, spec):
    _, cur = _live_state(fx, out, tmp_path, spec)
    capsys.readouterr()
    code = dk.main(["rollback-decide-update", "--plan", str(out / "plan.json"), "--live-dir", str(cur)])
    cap = capsys.readouterr()
    return code, [line.split("\t") for line in cap.out.splitlines() if "\t" in line], cap.err


def test_update_rollback_full_apply_restores_every_overwritten_object(tmp_path, capsys):
    fx, out = _update_plan(tmp_path, capsys)
    code, lines, _ = _decide_update(fx, out, tmp_path, capsys, {"*": "cand"})
    assert code == 0 and len(lines) == 2 * 2 + 3
    assert [k for k, _ in lines[:3]] == ["index.json", "trail-search.json", "trail-shapes.json"]   # index first
    assert all(a == "restore" for _, a in lines)
    assert not any("parking" in k for k, _ in lines)
    assert not any(a == "delete" for _, a in lines)                  # update never deletes


def test_update_rollback_partial_apply_restores_only_candidate_objects(tmp_path, capsys):
    fx, out = _update_plan(tmp_path, capsys)
    first = fx["upd"][0]
    spec = {"*": "old", f"{first}.json": "cand", "trail-shapes.json": "cand"}
    code, lines, _ = _decide_update(fx, out, tmp_path, capsys, spec)
    assert code == 0
    got = dict(lines)
    assert got[f"{first}.json"] == "restore" and got["trail-shapes.json"] == "restore"
    assert got["index.json"] == "skip" and got[f"silhouettes/{first}.json"] == "skip"


def test_update_rollback_nothing_applied_skips_everything(tmp_path, capsys):
    fx, out = _update_plan(tmp_path, capsys)
    code, lines, _ = _decide_update(fx, out, tmp_path, capsys, {"*": "old"})
    assert code == 0 and {a for _, a in lines} == {"skip"}


@pytest.mark.parametrize("bad", ["index.json", "trail-search.json", "trail-shapes.json", "AREA_GEOM", "AREA_SIL"])
def test_update_rollback_refuses_third_party_modified_object(tmp_path, capsys, bad):
    fx, out = _update_plan(tmp_path, capsys)
    key = {"AREA_GEOM": f"{fx['upd'][1]}.json", "AREA_SIL": f"silhouettes/{fx['upd'][1]}.json"}.get(bad, bad)
    code, lines, err = _decide_update(fx, out, tmp_path, capsys, {"*": "cand", key: "other"})
    assert code == 1 and lines == [] and "refusing" in err


def test_update_rollback_refuses_a_missing_live_object(tmp_path, capsys):
    fx, out = _update_plan(tmp_path, capsys)
    _, cur = _live_state(fx, out, tmp_path, {"*": "cand"})
    (cur / f"{fx['upd'][0]}.json").unlink()
    assert dk.main(["rollback-decide-update", "--plan", str(out / "plan.json"), "--live-dir", str(cur)]) == 1
    # an append plan is not an update plan
    plan = json.loads((out / "plan.json").read_text())
    plan["schema"] = "trekdex-dk-r2-plan-v1"
    (out / "plan2.json").write_text(json.dumps(plan))
    assert dk.main(["rollback-decide-update", "--plan", str(out / "plan2.json"), "--live-dir", str(cur)]) == 1


# ---------------------------- publish side: in-place rows + 2N+2 allowlist ----

def _verify_update(fx, cmd="verify-staged"):
    return dk.main([cmd, "--repo", str(fx["repo"]), "--areas", ",".join(fx["upd"]), "--mode", "update"])


@pytest.mark.parametrize("total,upd", UPDATE_CASES, ids=["N1", "N3", "N40"])
def test_update_allowlist_is_exactly_2n_plus_2_and_rows_are_replaced_in_place(tmp_path, total, upd):
    fx = _update_fixture(tmp_path, total, upd)
    before = (fx["repo"] / "public/areas/index.json").read_bytes()
    assert _finish(fx) == 0
    names = _git(fx["repo"], "diff", "--cached", "--name-only").stdout.decode().split()
    assert len(names) == 2 * len(upd) + 2 == len(dk.area_paths(fx["upd"]))
    assert _verify_update(fx) == 0
    after = json.loads((fx["repo"] / "public/areas/index.json").read_bytes())
    assert [r[0] for r in after] == [r[0] for r in json.loads(before)]                  # row order preserved
    # every non-updated row is byte-identical in the file
    old_p = _pieces_default(before)
    new_p = _pieces_default((fx["repo"] / "public/areas/index.json").read_bytes())
    for (orow, oraw), (nrow, nraw) in zip(old_p, new_p):
        assert (nraw == oraw) == (orow[0] not in fx["upd"])
    _git(fx["repo"], "commit", "-q", "-m", "update")
    assert _verify_update(fx, "verify-commit") == 0


def _pieces_default(data):
    return dk._split_records(data, False, "t", sep=", ")


def test_update_allowlist_accepts_a_geom_only_change_when_the_row_is_unchanged(tmp_path):
    fx = _update_fixture(tmp_path, 2, [0], row_changes=False)
    assert _finish(fx) == 0
    names = _git(fx["repo"], "diff", "--cached", "--name-only").stdout.decode().split()
    assert len(names) == 2 and not any("index" in n for n in names)
    assert _verify_update(fx) == 0


def test_update_staged_diff_refusals(tmp_path):
    fx = _update_fixture(tmp_path, 4, [1, 2])
    assert _finish(fx) == 0
    repo = fx["repo"]
    assert _verify_update(fx) == 0
    # extra path outside the allowlist
    (repo / "public/areas/geom/other-dk.json").write_text("{}", encoding="utf-8")
    _git(repo, "add", "public/areas/geom/other-dk.json")
    assert _verify_update(fx) == 1
    _git(repo, "rm", "-q", "-f", "public/areas/geom/other-dk.json")
    # a bystander geom modified
    by = [a for a in fx["ids"] if a not in fx["upd"]][0]
    (repo / f"public/areas/geom/{by}.json").write_bytes(b'{"id":"x"}')
    _git(repo, "add", f"public/areas/geom/{by}.json")
    assert _verify_update(fx) == 1
    _git(repo, "checkout", "-q", "HEAD", "--", f"public/areas/geom/{by}.json")
    assert _verify_update(fx) == 0
    # an updated geom left unchanged
    _git(repo, "checkout", "-q", "HEAD", "--", f"public/areas/geom/{fx['upd'][0]}.json")
    assert _verify_update(fx) == 1
    _git(repo, "add", "-A", "public/areas")
    # deletion
    _git(repo, "rm", "-q", "-f", f"public/areas/silhouettes/{fx['upd'][1]}.json")
    assert _verify_update(fx) == 1


def test_update_staged_diff_refuses_index_tampering(tmp_path):
    fx = _update_fixture(tmp_path, 4, [1, 2])
    assert _finish(fx) == 0
    repo = fx["repo"]
    index = repo / "public/areas/index.json"
    good = index.read_bytes()
    rows = json.loads(good)
    by = [i for i, r in enumerate(rows) if r[0] in fx["ids"] and r[0] not in fx["upd"]][0]

    def put(new_rows, *, bundle=True):
        index.write_bytes(_default(new_rows))
        if bundle:
            (repo / "ios/SouthMountainExplorer/Resources/areas-index.json").write_bytes(_compact(new_rows))
        _git(repo, "add", "public/areas", "ios/SouthMountainExplorer/Resources/areas-index.json")
    changed = copy.deepcopy(rows)
    changed[by][1] = "Renamed bystander"
    put(changed)
    assert _verify_update(fx) == 1                                    # a non-updated row changed
    changed = copy.deepcopy(rows)
    changed[0][1] = "Renamed US row"
    put(changed)
    assert _verify_update(fx) == 1
    swapped = copy.deepcopy(rows)
    swapped[1], swapped[2] = swapped[2], swapped[1]
    put(swapped)
    assert _verify_update(fx) == 1                                    # order changed
    put(rows + [["extra-dk", "Extra", "Denmark", 56.0, 9.0, 2, 3.0, 5]])
    assert _verify_update(fx) == 1                                    # a row was appended
    wrong = copy.deepcopy(rows)
    wrong[fx["ids"].index(fx["upd"][0]) + 1][5] = 99
    put(wrong)
    assert _verify_update(fx) == 1                                    # count differs from the geom
    ident = copy.deepcopy(rows)
    ident[fx["ids"].index(fx["upd"][0]) + 1][1] = "Another name"
    put(ident)
    assert _verify_update(fx) == 1                                    # identity changed
    put(rows, bundle=False)                                           # only one of the two index files changed
    assert _verify_update(fx) == 1
    put(rows)
    assert _verify_update(fx) == 0
    index.write_bytes(json.dumps(rows, indent=1).encode("utf-8"))
    _git(repo, "add", "public/areas/index.json")
    assert _verify_update(fx) == 1                                    # producer framing differs


def test_replace_index_refuses_new_areas_and_identity_changes(tmp_path):
    fx = _update_fixture(tmp_path, 3, [1])
    repo = fx["repo"]
    work = tmp_path / "work"
    aid = fx["upd"][0]
    (work / aid).mkdir(parents=True)
    row = fx["new_rows"][fx["ids"].index(aid)]
    sel = tmp_path / "sel.txt"
    sel.write_text(aid + "\n")

    def run():
        return dk.main(["replace-index", "--registry", str(repo / "trailforge/golden/dk-areas.json"),
                        "--selected", str(sel), "--rows-dir", str(work),
                        "--geom-dir", str(repo / "public/areas/geom"),
                        "--target", str(repo / "public/areas/index.json")])
    renamed = list(row)
    renamed[1] = "Another"
    (work / aid / "index.json").write_text(json.dumps([renamed]))
    assert run() == 1                                                 # registry identity mismatch
    (work / aid / "index.json").write_text(json.dumps([row]))
    before = (repo / "public/areas/index.json").read_bytes()
    sel.write_text("ghost-dk\n")
    assert run() == 1 and (repo / "public/areas/index.json").read_bytes() == before
    sel.write_text(aid + "\n")
    assert run() == 0 and run() == 0                                  # idempotent (row already replaced)


# ------------------------------------------------------ update workflows -----

def test_publish_dk_update_mode_is_main_only_explicit_ids_and_publish_unchanged():
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(PUBLISH_DK)
    assert (doc.get("on") or doc.get(True))["workflow_dispatch"]["inputs"]["mode"]["options"] == [
        "qualify", "publish", "update"]
    validate = PUBLISH_DK.split("- name: Validate input shape before checkout", 1)[1].split("- uses:", 1)[0]
    assert "(qualify|publish|update)" in validate
    assert '[[ "$RAW_AREAS" != batch=* ]]' in validate               # update takes explicit ids only
    assert 'if [ "$RAW_MODE" != "qualify" ]' in validate and 'test "$GITHUB_REF_NAME" = "main"' in validate
    assert "replace-index" in PUBLISH_DK and "--mode \"$MODE\"" in PUBLISH_DK
    # the append-only publish step still uses merge-index and refuses existing geoms
    install = PUBLISH_DK.split("- name: Install blessed geoms", 1)[1].split("\n      - name:", 1)[0]
    assert 'test ! -e "public/areas/geom/$ID.json"' in install and "merge-index" in install
    upd = PUBLISH_DK.split("- name: Install updated geoms", 1)[1].split("\n      - name:", 1)[0]
    assert 'test -f "public/areas/geom/$ID.json"' in upd and "merge-index" not in upd
    # every blessed-table gate also guards update
    assert 'if [ "$MODE" != "qualify" ]; then\n              python3 scripts/dk_publish.py assert-expected' in PUBLISH_DK
    for forbidden in ("R2_ACCESS_KEY_ID", "aws s3", "s3api", "--force"):
        assert forbidden not in PUBLISH_DK


def test_sync_dk_update_modes_contract():
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(SYNC_DK)
    assert doc["concurrency"]["group"] == "sync-geom-to-r2" and doc["concurrency"]["cancel-in-progress"] is False
    inputs = (doc.get("on") or doc.get(True))["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"mode", "rollback_run_id"}                  # the area list is never an input
    steps = {s["name"]: s for s in next(iter(doc["jobs"].values()))["steps"] if "name" in s}
    cond = {n: s.get("if", "") for n, s in steps.items()}
    guards = SYNC_DK.split("- name: Guards", 1)[1].split("- name: Snapshot", 1)[0]
    assert "plan|apply|rollback|plan-update|apply-update|rollback-update" in guards
    assert 'if [ "$MODE" = "rollback" ] || [ "$MODE" = "rollback-update" ]' in guards
    # append path conditions are exactly the old modes (update modes never run them)
    assert "inputs.mode == 'plan' || inputs.mode == 'apply'" in cond["Plan (area list from the committed index diff; build + prove candidates)"]
    assert cond["Apply (write 2N+3 keys in order, read each back)"] == "${{ inputs.mode == 'apply' }}"
    update_plan = SYNC_DK.split("- name: Update plan", 1)[1].split("\n      - name:", 1)[0]
    apply_update = SYNC_DK.split("- name: Apply update", 1)[1].split("\n      - name:", 1)[0]
    rollback_update = SYNC_DK.split("- name: Rollback update", 1)[1]
    assert "inputs.mode == 'plan-update' || inputs.mode == 'apply-update'" in cond[
        "Update plan (live Danish objects, repo-vs-live area set, candidates, proofs)"]
    # the 2N keys must exist (get-object fails on a missing key) and the set is derived, never an input
    assert "get-object" in update_plan and "derive-update-areas" in update_plan and "already exists" not in update_plan
    assert "build-update-candidates" in update_plan and "live-dk-ids" in update_plan
    # old bytes are snapshotted and uploaded BEFORE the apply step
    assert (SYNC_DK.index("- name: Update plan") < SYNC_DK.index("dk-r2-update-snapshot-${{ github.run_id }}")
            < SYNC_DK.index("- name: Apply update"))
    # conditional writes on every key; ETag race guard first; sha256 readback; parking never written
    assert "--if-match" in apply_update and 'put "cand/$file" "$key" "$cc" "$(base "$key")"' in apply_update
    assert apply_update.index("changed since snapshot") < apply_update.index('put "cand/$file"')
    assert "PARTIAL APPLY: dispatch mode=rollback-update with rollback_run_id=$GITHUB_RUN_ID" in apply_update
    assert "trap - EXIT" in apply_update and "READBACK MISMATCH" in apply_update
    assert "put-object" in apply_update and "cand/parking" not in apply_update
    assert 'test "$(cur parking.json)" = "$(base parking.json)"' in apply_update
    assert "delete-object" not in apply_update and "delete-object" not in rollback_update    # update never deletes
    assert "--if-match" in rollback_update and "rollback-decide-update" in rollback_update
    assert "ROLLBACK READBACK MISMATCH" in rollback_update and "parking_after" in rollback_update
    # parking.json is never a put/copy target anywhere in the file
    assert not re.search(r"--key\s+parking\.json[^\n]*--body|put[^\n]*parking", SYNC_DK)
    assert sorted(set(re.findall(r"secrets\.([A-Z0-9_]+)", SYNC_DK))) == [
        "R2_ACCESS_KEY_ID", "R2_ACCOUNT_ID", "R2_SECRET_ACCESS_KEY"]


def test_update_write_order_in_plan_is_geoms_silhouettes_shapes_search_index_last(tmp_path):
    fx = _update_fixture(tmp_path, 3, [0, 1])
    assert _finish(fx) == 0
    assert _build_update(fx, tmp_path / "o") == 0
    keys = [w["key"] for w in json.loads((tmp_path / "o/plan.json").read_text())["writes"]]
    ids = sorted(fx["upd"])
    assert keys == [f"{i}.json" for i in ids] + [f"silhouettes/{i}.json" for i in ids] + [
        "trail-shapes.json", "trail-search.json", "index.json"]
