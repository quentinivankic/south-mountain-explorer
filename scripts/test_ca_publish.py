#!/usr/bin/env python3
"""Tests for the Canada lane registry tooling (scripts/ca_publish.py): registry, selection,
boundary checks, blessed table, publish-row helpers. Sync, git and duplicate-gate tests live in
test_ca_publish_sync.py; workflow/script contracts in test_ca_run_area.py."""
import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ca_publish as ca  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "trailforge/golden/ca-areas.json"
EXPECTED = ROOT / "trailforge/golden/ca-areas-expected.json"


def _entry(i, prov="ns", **kw):
    return {"id": f"area-{i:03d}-ca-{prov}", "name": f"Area {i}", "state": "Canada", "province": prov,
            "relation_id": 1000 + i, "bbox": [-63.0, 45.0, -62.5, 45.5], "center": [45.2, -62.8],
            "tier": 2, "hold": None, **kw}


def _registry(tmp_path, entries, **top):
    doc = {"schema": ca.REGISTRY_SCHEMA, "extract_base": ca.EXTRACT_BASE, "state": "Canada", "areas": entries}
    doc.update(top)
    path = tmp_path / "ca-areas.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


# ----------------------------------------------------------------- registry --
def test_committed_registry_and_table_load_and_are_pinned_to_canada():
    reg = ca.load_registry(REGISTRY)
    assert (reg["schema"], reg["extract_base"], reg["state"]) == (
        "trekdex-ca-areas-v1", "north-america/canada", "Canada")
    assert all(a["state"] == "Canada" and ca.SLUG.fullmatch(a["id"]) for a in reg["areas"])
    table = ca.load_expected(EXPECTED)
    assert set(table) <= {a["id"] for a in reg["areas"]}


@pytest.mark.parametrize("good", ["algonquin-ca-on", "x-y-z-ca-ns", "a1-ca-pe"])
def test_slug_accepts_province_suffixed_ids(good):
    assert ca.SLUG.fullmatch(good)


@pytest.mark.parametrize("bad", ["algonquin-ca", "yosemite-ca", "a-dk", "a-ca-nt", "a-ca-nu", "a-ca-xx",
                                 "a-ca-NS", "-ca-ns", "A-ca-ns", "a_b-ca-ns", "a-ca-ns-ns"])
def test_slug_rejects_bare_california_foreign_and_unsupported_ids(bad):
    assert not ca.SLUG.fullmatch(bad)


def test_every_province_id_maps_to_a_bundled_ca_region_code():
    """filter-ios-bundle admits an area by code_from_slug(id); a Canada id must resolve to CA-<PROV>,
    never to the bare US state code CA."""
    spec = importlib.util.spec_from_file_location("filter_ios_bundle_ca_tests", ROOT / "scripts/filter-ios-bundle.py")
    bundle = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bundle)
    for prov in ca.PROVINCES:
        code = bundle.code_from_slug(f"some-park-ca-{prov}")
        assert code == f"CA-{prov.upper()}" and code in bundle.BUNDLED_REGION_CODES


def test_registry_accepts_a_valid_entry_and_way_backed_entry(tmp_path):
    way = _entry(2, relation_id=None)
    del way["relation_id"]
    way["way_id"] = 555
    reg = ca.load_registry(_registry(tmp_path, [_entry(1), way]))
    assert set(reg["by_id"]) == {"area-001-ca-ns", "area-002-ca-ns"}


def _bad(**kw):
    return [_entry(1, **kw)]


def _drop(key):
    e = _entry(1)
    del e[key]
    return [e]


@pytest.mark.parametrize("entries,needle", [
    (_bad(id="area-001-ca"), "id is invalid"),
    (_bad(id="area-001-ca-nu", province="nu"), "id is invalid"),
    (_bad(province="pe"), "province must equal"),
    (_bad(state="Denmark"), "state must be"),
    (_bad(name="Cafe\u0301"), "name is invalid"),
    (_bad(way_id=5), "exactly one of relation_id and way_id"),
    (_bad(relation_id=0), "relation_id is invalid"),
    (_bad(display_name="Area 1"), "display_name"),
    (_bad(bbox=[-63.0, 45.0, -62.5, 45.5], center=None), "both be set or both be null"),
    (_bad(bbox=[10.0, 56.0, 10.5, 56.5], center=[56.2, 10.2]), "inside Canada"),
    (_bad(bbox=[-62.5, 45.0, -63.0, 45.5]), "inside Canada"),
    (_bad(tier=4), "tier must be"),
    (_bad(tier=True), "tier must be"),
    (_bad(hold="because"), "hold"),
    (_bad(est_mi=-1), "est_mi"),
    (_bad(est_trails=1.5), "est_trails"),
    (_bad(exclude_trails=[{"id": "r123", "name": "X", "reason": "r"}]), "invalid"),
    (_bad(exclude_trails=[{"id": "n:X", "name": "Y", "reason": "r"}]), "name must equal"),
    (_bad(exclude_trails=[]), "non-empty"),
    (_bad(keep_trails=[{"name": "X", "source": "http://x", "reason": "r"}]), "https"),
    (_bad(trail_name_fixes=[{"osm_name": "A", "name": "A", "source": "https://x.example", "reason": "r"}]), "nothing to fix"),
    ([_entry(1), _entry(1)], "repeats id"),
    ([_entry(1), _entry(2, relation_id=1001)], "repeated"),
    (_drop("tier"), "tier must be"),
])
def test_registry_rejects_invalid_entries(tmp_path, entries, needle):
    with pytest.raises(ca.CaError, match=needle):
        ca.load_registry(_registry(tmp_path, copy.deepcopy(entries)))


def test_registry_rejects_wrong_pins(tmp_path):
    with pytest.raises(ca.CaError, match="schema"):
        ca.load_registry(_registry(tmp_path, [], schema="trekdex-dk-areas-v1"))
    with pytest.raises(ca.CaError, match="Canada pins"):
        ca.load_registry(_registry(tmp_path, [], state="Denmark"))


def test_every_hold_reason_is_documented_and_registry_accepts_each(tmp_path):
    for reason in sorted(ca.HOLD_REASONS):
        assert ca.load_registry(_registry(tmp_path, [_entry(1, hold=reason)]))


# ------------------------------------------------------------------- select --
def _many(tmp_path, n, prov="ns", **kw):
    return ca.load_registry(_registry(tmp_path, [_entry(i, prov, **kw) for i in range(n)]))


def test_batch_selection_is_per_province_unheld_tier_then_miles(tmp_path):
    entries = [_entry(0, tier=2, est_mi=50), _entry(1, tier=1, est_mi=5), _entry(2, tier=3, est_mi=99),
               _entry(3, tier=2, est_mi=80), _entry(4, hold="few-trails"), _entry(5, "pe", tier=1)]
    reg = ca.load_registry(_registry(tmp_path, entries))
    got = ca.select_areas(reg, set(), "batch=1", 25, "qualify", "ns")
    assert got == ["area-001-ca-ns", "area-003-ca-ns", "area-000-ca-ns", "area-002-ca-ns"]
    assert ca.select_areas(reg, set(), "batch=2", 2, "qualify", "ns") == ["area-000-ca-ns", "area-002-ca-ns"]
    assert ca.select_areas(reg, set(), "batch=1", 25, "qualify", "pe") == ["area-005-ca-pe"]
    with pytest.raises(ca.CaError, match="selects nothing"):
        ca.select_areas(reg, set(), "batch=3", 2, "qualify", "ns")


def test_explicit_ids_may_name_held_areas_but_must_match_the_province(tmp_path):
    reg = ca.load_registry(_registry(tmp_path, [_entry(1, hold="few-trails"), _entry(2, "pe")]))
    assert ca.select_areas(reg, set(), "area-001-ca-ns", 25, "qualify", "ns") == ["area-001-ca-ns"]
    with pytest.raises(ca.CaError, match="one province per run"):
        ca.select_areas(reg, set(), "area-002-ca-pe", 25, "qualify", "ns")


def test_select_refuses_unknown_duplicate_malformed_published_and_oversized(tmp_path):
    reg = _many(tmp_path, 5)
    ids = [f"area-{i:03d}-ca-ns" for i in range(5)]
    with pytest.raises(ca.CaError, match="not in the registry"):
        ca.select_areas(reg, set(), "ghost-ca-ns", 25, "qualify", "ns")
    with pytest.raises(ca.CaError, match="repeats"):
        ca.select_areas(reg, set(), f"{ids[0]},{ids[0]}", 25, "qualify", "ns")
    with pytest.raises(ca.CaError, match="comma list"):
        ca.select_areas(reg, set(), "area-000-ca", 25, "qualify", "ns")
    with pytest.raises(ca.CaError, match="already in public/areas/index.json"):
        ca.select_areas(reg, {ids[0]}, ids[0], 25, "publish", "ns")
    assert ca.select_areas(reg, {ids[0]}, ids[0], 25, "qualify", "ns") == [ids[0]]
    with pytest.raises(ca.CaError, match="exceeds the chunk cap"):
        ca.select_areas(reg, set(), ",".join(ids), 3, "qualify", "ns")
    with pytest.raises(ca.CaError, match="chunk_size"):
        ca.select_areas(reg, set(), ids[0], 41, "qualify", "ns")
    with pytest.raises(ca.CaError, match="mode must be"):
        ca.select_areas(reg, set(), ids[0], 25, "update", "ns")
    with pytest.raises(ca.CaError, match="province must be"):
        ca.select_areas(reg, set(), ids[0], 25, "qualify", "nt")


def test_select_cli_writes_ids_and_getid_args(tmp_path):
    way = _entry(2)
    del way["relation_id"]
    way["way_id"] = 77
    reg = _registry(tmp_path, [_entry(1), way])
    index = tmp_path / "index.json"
    index.write_text("[]", encoding="utf-8")
    out, gh = tmp_path / "sel.txt", tmp_path / "gh.txt"
    assert ca.main(["select", "--registry", str(reg), "--index", str(index), "--areas", "batch=1",
                    "--province", "ns", "--mode", "qualify", "--out", str(out), "--github-output", str(gh)]) == 0
    assert out.read_text().split() == ["area-001-ca-ns", "area-002-ca-ns"]
    text = gh.read_text()
    assert "area_count=2" in text and "getid_args=r1001 w77" in text


def test_pbf_urls_use_the_geofabrik_canada_province_extracts():
    assert ca.pbf_url("pe") == "https://download.geofabrik.de/north-america/canada/prince-edward-island-latest.osm.pbf"
    assert ca.pbf_url("nl").endswith("/canada/newfoundland-and-labrador-latest.osm.pbf")
    assert set(ca.PROVINCES) == {"ab", "bc", "mb", "nb", "nl", "ns", "on", "pe", "qc", "sk", "yt"}


# ---------------------------------------------------------- blessed table ----
def _geom(aid, trail_count=2, total_mi=3.0, names=None):
    names = names or [f"{aid} Trail {i}" for i in range(trail_count)]
    return {"id": aid, "name": aid, "trail_count": len(names), "total_mi": total_mi,
            "trails": [{"id": f"t{i}", "name": n, "distanceMi": 1.5, "difficulty": "Easy",
                        "segments": [[[45.2, -62.8 + i / 100], [45.21, -62.78 + i / 100], [45.22, -62.8 + i / 100]]]}
                       for i, n in enumerate(names)]}


def _expected_file(tmp_path, aid, **override):
    entry = {"trail_count": 2, "total_mi": 3.0, "total_mi_tolerance": 0.05,
             "required_names": [f"{aid} Trail 0"], "forbidden_names": ["Trans Canada Trail"]}
    entry.update(override)
    path = tmp_path / "expected.json"
    path.write_text(json.dumps({"schema": ca.EXPECTED_SCHEMA, "areas": {aid: entry}}), encoding="utf-8")
    return path


def _assert_expected(tmp_path, geom, expected_path, aid="x-ca-ns"):
    gp = tmp_path / "g.json"
    gp.write_text(json.dumps(geom), encoding="utf-8")
    return ca.main(["assert-expected", "--geom", str(gp), "--area-id", aid, "--expected", str(expected_path)])


def test_assert_expected_passes_and_fails(tmp_path):
    aid = "x-ca-ns"
    exp = _expected_file(tmp_path, aid)
    assert _assert_expected(tmp_path, _geom(aid), exp) == 0
    assert _assert_expected(tmp_path, _geom(aid, total_mi=3.04), exp) == 0
    assert _assert_expected(tmp_path, _geom(aid, total_mi=3.2), exp) == 1
    assert _assert_expected(tmp_path, _geom(aid, trail_count=3), exp) == 1
    assert _assert_expected(tmp_path, _geom(aid, names=["Other", f"{aid} Trail 1"]), exp) == 1
    assert _assert_expected(tmp_path, _geom(aid, names=[f"{aid} Trail 0", "Trans Canada Trail"]), exp) == 1
    assert _assert_expected(tmp_path, _geom("y-ca-ns"), exp) == 1
    assert _assert_expected(tmp_path, _geom("z-ca-ns"), exp, aid="z-ca-ns") == 1


def test_expected_table_rejects_foreign_ids_and_wrong_schema(tmp_path):
    path = tmp_path / "e.json"
    path.write_text(json.dumps({"schema": "trekdex-dk-areas-expected-v1", "areas": {}}), encoding="utf-8")
    with pytest.raises(ca.CaError, match="schema"):
        ca.load_expected(path)
    path.write_text(json.dumps({"schema": ca.EXPECTED_SCHEMA, "areas": {"a-dk": {
        "trail_count": 1, "total_mi": 1.0, "required_names": [], "forbidden_names": []}}}), encoding="utf-8")
    with pytest.raises(ca.CaError, match="invalid"):
        ca.load_expected(path)
