#!/usr/bin/env python3
"""Tests for the parking-verdicts sidecar: the shared matcher, the generator,
and the three consumers (pool builder, geom sweep, add-parking gate)."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import _parking_verdicts as pv  # noqa: E402


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / name)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


build_verdicts = _load("build-parking-verdicts.py")
pool = _load("build-parking-pool.py")
sweep = _load("sweep-parking-verdicts.py")
ap = _load("add-parking.py")

# A 60 x 40 m rectangular lot centred on (33.5000, -111.9000). One degree of
# latitude is ~111.3 km, one degree of longitude at 33.5°N ~92.8 km.
LAT, LON = 33.5000, -111.9000
D_LAT_30M = 30 / 111_320
D_LON_20M = 20 / (111_320 * 0.834)
RECT = [[LAT - D_LAT_30M, LON - D_LON_20M], [LAT - D_LAT_30M, LON + D_LON_20M],
        [LAT + D_LAT_30M, LON + D_LON_20M], [LAT + D_LAT_30M, LON - D_LON_20M],
        [LAT - D_LAT_30M, LON - D_LON_20M]]


def _doc(*entries):
    return {"version": 1, "lots": {e["osm"][0]: e for e in entries}}


def _entry(osm, verdict, lat=LAT, lon=LON, rings=None, **extra):
    e = {"verdict": verdict, "reason": "too-far" if verdict == "DROP" else None,
         "lat": lat, "lon": lon, "rings": rings or [], "osm": [osm],
         "name": extra.pop("name", None), "area": "test-area", "prior": "surveyed",
         "confidence": "strong", "evidence": {"serves": "test evidence"},
         "judged": "2026-09-13", "src": "test"}
    e.update(extra)
    return e


def _offset(lat, lon, north_m=0.0, east_m=0.0):
    import math
    return (lat + north_m / 111_320,
            lon + east_m / (111_320 * math.cos(math.radians(lat))))


# ------------------------------------------------------------------ matcher

def test_match_by_osm_id_is_exact_and_ignores_position():
    v = pv.Verdicts(_doc(_entry("way/1", "DROP")))
    far_lat, far_lon = _offset(LAT, LON, north_m=5000)
    assert v.drop_for({"lat": far_lat, "lon": far_lon, "osm": "way/1"}) is not None
    assert v.drop_for({"lat": LAT, "lon": LON, "osm": "way/999"}) is not None, \
        "an unjudged id still falls back to position"


def test_node_only_verdict_matches_within_near_m_only():
    v = pv.Verdicts(_doc(_entry("node/1", "DROP")))
    close = _offset(LAT, LON, north_m=pv.NEAR_M - 1)
    far = _offset(LAT, LON, north_m=pv.NEAR_M + 5)
    assert v.drop_for({"lat": close[0], "lon": close[1]}) is not None
    assert v.drop_for({"lat": far[0], "lon": far[1]}) is None


def test_footprint_matches_inside_and_near_edge_but_not_the_next_lot_over():
    v = pv.Verdicts(_doc(_entry("way/1", "DROP", rings=[RECT])))
    # Bounding-box centre of a member polygon: inside the ring, 25 m from the
    # centroid — beyond NEAR_M, so only the footprint test can claim it.
    inside = _offset(LAT, LON, north_m=25)
    assert v.drop_for({"lat": inside[0], "lon": inside[1]}) is not None
    # Just past the edge (30 m half-height + 6 m): within EDGE_M of it.
    fringe = _offset(LAT, LON, north_m=36)
    assert v.drop_for({"lat": fringe[0], "lon": fringe[1]}) is not None
    # A neighbouring lot 45 m north: outside the ring by 15 m — not this lot.
    neighbour = _offset(LAT, LON, north_m=45)
    assert v.drop_for({"lat": neighbour[0], "lon": neighbour[1]}) is None


def test_a_keep_node_beside_a_wide_drop_shields_the_lot_that_is_really_the_node():
    # A KEEP node 15 m past the DROP ring's north edge, and a shipped lot 1 m
    # from that node. The DROP's footprint does not reach it (16 m past the
    # edge is beyond EDGE_M) and its centroid is 46 m away; only the KEEP's
    # near-circle claims it. The lot is the node, and it stays.
    keep_lat, keep_lon = _offset(LAT, LON, north_m=45)      # 15 m past the edge
    v = pv.Verdicts(_doc(_entry("way/1", "DROP", rings=[RECT]),
                         _entry("node/2", "KEEP", lat=keep_lat, lon=keep_lon)))
    lot = _offset(keep_lat, keep_lon, east_m=1)
    hit = v.match({"lat": lot[0], "lon": lot[1]})
    assert hit is not None and hit["verdict"] == "KEEP"
    assert v.drop_for({"lat": lot[0], "lon": lot[1]}) is None


def test_containment_outranks_a_neighbours_near_circle():
    # A lot INSIDE the DROP polygon, 25 m from its centroid, and 18 m from a
    # KEEP node that sits outside the polygon. Distance alone would hand it to
    # the KEEP (18 < 25); the footprint says it is the DROP lot.
    inside = _offset(LAT, LON, north_m=25)
    keep = _offset(inside[0], inside[1], north_m=18)          # 43 m north: outside RECT
    v = pv.Verdicts(_doc(_entry("way/1", "DROP", rings=[RECT]),
                         _entry("node/2", "KEEP", lat=keep[0], lon=keep[1])))
    hit = v.match({"lat": inside[0], "lon": inside[1]})
    assert hit is not None and hit["verdict"] == "DROP"


def test_bbox_centre_of_a_concave_ring_is_that_lot_even_outside_the_pavement():
    # A thin L: a 100 m north–south arm 12 m wide and a 100 m east–west arm
    # 12 m wide, sharing the SW corner. Its bounding-box centre sits in the
    # empty notch, ~44 m from either arm — what Overpass `out center` ships for
    # this way, and what the old inside/edge rules could not reach.
    def pt(n, e):
        return list(_offset(LAT, LON, north_m=n, east_m=e))
    ell = [pt(0, 0), pt(0, 100), pt(12, 100), pt(12, 12), pt(100, 12), pt(100, 0), pt(0, 0)]
    centroid = _offset(LAT, LON, north_m=25, east_m=25)       # somewhere plausible
    v = pv.Verdicts(_doc(_entry("way/1", "KEEP", lat=centroid[0], lon=centroid[1], rings=[ell])))
    bbox_centre = _offset(LAT, LON, north_m=50, east_m=50)
    assert not pv.point_in_ring(bbox_centre[0], bbox_centre[1], ell)
    assert pv.dist_to_ring_m(bbox_centre[0], bbox_centre[1], ell) > 30
    rank, _ = pv.covers(v.entries["way/1"], bbox_centre[0], bbox_centre[1])
    assert rank == 0
    # ...but 6 m off that centre is nobody's lot: the rule is exact, not a circle.
    off = _offset(bbox_centre[0], bbox_centre[1], north_m=6)
    assert pv.covers(v.entries["way/1"], off[0], off[1]) is None


def test_review_verdicts_shield_a_lot_from_a_neighbouring_drop_but_never_drop():
    rev = _offset(LAT, LON, north_m=45)
    v = pv.Verdicts(_doc(_entry("way/1", "DROP", rings=[RECT]),
                         _entry("node/2", "REVIEW", lat=rev[0], lon=rev[1],
                                resolve_hint="leaf-off imagery")))
    lot = _offset(rev[0], rev[1], east_m=1)
    assert v.match({"lat": lot[0], "lon": lot[1]})["verdict"] == "REVIEW"
    assert v.drop_for({"lat": lot[0], "lon": lot[1]}) is None
    assert v.drop_for({"lat": rev[0], "lon": rev[1], "osm": "node/2"}) is None


def test_load_is_tolerant_of_a_missing_or_broken_sidecar(tmp_path):
    assert pv.load(tmp_path / "nope.json", quiet=True) is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert pv.load(bad, quiet=True) is None
    ok = tmp_path / "ok.json"
    ok.write_text(json.dumps(_doc(_entry("way/1", "KEEP"))))
    v = pv.load(ok, quiet=True)
    assert v is not None and len(v) == 1 and v.count("KEEP") == 1


# ---------------------------------------------------------------- generator

def _store_and_dossier(tmp_path, verdict="DROP", rings=True, serves_evidence="walk 2600 m"):
    data = tmp_path / "data"
    data.mkdir()
    dossier = {"slug": "test-area", "name": "Test", "bbox": [0, 0, 1, 1], "facilities": [{
        "fid": 7, "lat": LAT, "lon": LON, "osm": ["way/1", "node/2"],
        "members": [{"osm": "way/1", "tags": {"amenity": "parking"}},
                    {"osm": "node/2", "tags": {"amenity": "parking"}}],
        "tags_union": {"amenity": "parking", "name": "Test Lot"},
        "ring": RECT if rings else None, "rings": [RECT] if rings else None,
    }]}
    (data / "test-area_dossier.json").write_text(json.dumps(dossier))
    store = {"way/1": {"osm": ["way/1", "node/2"], "verdict": verdict, "prior": "surveyed",
                       "exists": {"call": "yes", "evidence": "Z2: striped lot"},
                       "public": {"call": "yes", "evidence": "no access tag"},
                       "serves": {"call": "no" if verdict == "DROP" else "yes",
                                  "evidence": serves_evidence},
                       "confidence": "strong", "src": "test"}}
    for name, _, _ in build_verdicts.STORES:
        (data / name).write_text(json.dumps(store if name.startswith("phx") else {}))
    return data


def test_generator_carries_ids_position_footprint_and_evidence(tmp_path):
    data = _store_and_dossier(tmp_path)
    doc, notes, folded = build_verdicts.build(str(data))
    assert notes == [] and folded == []
    e = doc["lots"]["way/1"]
    assert e["osm"] == ["way/1", "node/2"] and e["verdict"] == "DROP"
    assert e["reason"] == "too-far"                     # serves evidence talks distance
    assert (e["lat"], e["lon"]) == (LAT, LON) and e["name"] == "Test Lot"
    assert len(e["rings"]) == 1 and len(e["rings"][0]) == len(RECT)
    assert e["evidence"]["serves"] == "walk 2600 m" and e["judged"] == "2026-08-01"
    # Both member ids resolve to the one entry through the matcher.
    v = pv.Verdicts(doc)
    assert v.drop_for({"lat": 0, "lon": 0, "osm": "node/2"}) is not None


def test_generator_reason_vocabulary():
    r = build_verdicts._reason
    assert r({"verdict": "DROP", "exists": {"call": "no"}}) == "not-a-lot"
    assert r({"verdict": "DROP", "public": {"call": "no"}}) == "not-public"
    assert r({"verdict": "DROP", "serves": {"call": "no", "evidence": "equestrian center grounds"}}) \
        == "facility-only"
    # A distance is a number with a unit — "mansion" and "farm" are not distances.
    assert r({"verdict": "DROP", "serves": {"call": "no", "evidence": "walk 2926 m"}}) == "too-far"
    assert r({"verdict": "DROP", "serves": {"call": "no", "evidence": "1.4 km fallback"}}) == "too-far"
    assert r({"verdict": "DROP", "serves": {"call": "no",
                                            "evidence": "mansion grounds; farm lane"}}) == "facility-only"
    assert r({"verdict": "KEEP", "coverage_gap": True}) == "named-trailhead"
    assert r({"verdict": "KEEP"}) is None


def test_generator_unions_store_and_dossier_ids_and_folds_a_second_key_for_one_cluster(tmp_path):
    data = _store_and_dossier(tmp_path)
    # The New England store keeps one entry PER member id of a cluster. Give
    # the phx store a second key that names only the cluster's other member.
    path = data / "phx_verdicts_osm.json"
    store = json.loads(path.read_text())
    store["node/2"] = dict(store["way/1"], osm=["node/2"])
    path.write_text(json.dumps(store))
    doc, notes, folded = build_verdicts.build(str(data))
    assert notes == [] and len(folded) == 1 and "node/2" in folded[0]
    assert list(doc["lots"]) == ["way/1"]
    # Store ids first, then the rest of the dossier cluster — and both resolve.
    assert doc["lots"]["way/1"]["osm"] == ["way/1", "node/2"]
    v = pv.Verdicts(doc)
    assert v.match({"lat": 0, "lon": 0, "osm": "node/2"}) is v.match({"lat": 0, "lon": 0, "osm": "way/1"})


def test_generator_refuses_to_write_when_a_verdict_cannot_be_placed(tmp_path, capsys):
    data = _store_and_dossier(tmp_path)
    path = data / "phx_verdicts_osm.json"
    store = json.loads(path.read_text())
    store["way/404"] = dict(store["way/1"], osm=["way/404"])        # no dossier facility
    path.write_text(json.dumps(store))
    out = tmp_path / "parking-verdicts.json"
    assert build_verdicts.main(["--data-dir", str(data), "--out", str(out)]) == 1
    assert not out.exists()
    assert "could not be placed" in capsys.readouterr().err


def test_generator_is_deterministic_and_check_mode_detects_drift(tmp_path, capsys):
    data = _store_and_dossier(tmp_path)
    out = tmp_path / "parking-verdicts.json"
    assert build_verdicts.main(["--data-dir", str(data), "--out", str(out)]) == 0
    first = out.read_text()
    assert build_verdicts.main(["--data-dir", str(data), "--out", str(out)]) == 0
    assert out.read_text() == first
    assert build_verdicts.main(["--data-dir", str(data), "--out", str(out), "--check"]) == 0
    out.write_text(first.replace("DROP", "KEEP", 1))
    assert build_verdicts.main(["--data-dir", str(data), "--out", str(out), "--check"]) == 1


# ------------------------------------------------------------- pool builder

def _geom_dir(tmp_path, areas: dict[str, list[dict]]):
    geom = tmp_path / "geom"
    geom.mkdir()
    for slug, lots in areas.items():
        (geom / f"{slug}.json").write_text(json.dumps({"id": slug, "trails": [], "parking": lots}))
    bundle = tmp_path / "areas-index.json"
    bundle.write_text(json.dumps([[slug] for slug in areas]))
    return geom, bundle


def _run_pool(tmp_path, geom, bundle, verdicts_doc, *extra_args):
    vpath = tmp_path / "verdicts.json"
    vpath.write_text(json.dumps(verdicts_doc))
    out = tmp_path / "pool.json"
    rc = pool.main(["--geom-dir", str(geom), "--bundle", str(bundle), "--out", str(out),
                    "--verdicts", str(vpath), *extra_args])
    assert rc == 0
    return json.load(open(out))


def test_pool_drops_a_judged_lot_once_for_every_area_that_carries_it(tmp_path):
    other = _offset(LAT, LON, north_m=300)
    geom, bundle = _geom_dir(tmp_path, {
        "area-a": [{"lat": LAT, "lon": LON}, {"lat": other[0], "lon": other[1]}],
        "area-b": [{"lat": LAT, "lon": LON, "name": "Same lot, other area"}],
    })
    lots = _run_pool(tmp_path, geom, bundle, _doc(_entry("way/1", "DROP")))
    # area-a loses the judged lot and keeps the other; area-b's only lot IS the
    # judged lot, so area-b is refused and its copy stays in the pool.
    positions = {(round(r[0], 4), round(r[1], 4)) for r in lots}
    assert (round(other[0], 4), round(other[1], 4)) in positions
    assert (round(LAT, 4), round(LON, 4)) in positions, "refuse-to-empty kept area-b's lot"
    assert len(lots) == 2


def test_pool_refuses_to_empty_an_area_and_says_so(tmp_path, capsys):
    geom, bundle = _geom_dir(tmp_path, {"only": [{"lat": LAT, "lon": LON}]})
    lots = _run_pool(tmp_path, geom, bundle, _doc(_entry("way/1", "DROP")))
    assert len(lots) == 1
    assert "refused to empty 1 area(s): only (1)" in capsys.readouterr().out


def test_pool_lists_uncovered_keeps_and_adds_them_only_when_asked(tmp_path, capsys):
    keep_pos = _offset(LAT, LON, north_m=500)
    geom, bundle = _geom_dir(tmp_path, {"a": [{"lat": LAT, "lon": LON}]})
    doc = _doc(_entry("way/1", "KEEP"),                                  # covered by a's lot
               _entry("way/2", "KEEP", lat=keep_pos[0], lon=keep_pos[1], name="Hidden Trailhead"))
    lots = _run_pool(tmp_path, geom, bundle, doc)
    assert len(lots) == 1
    out = capsys.readouterr().out
    assert "1 judged-KEEP lot(s) nothing in the pool covers" in out and "Hidden Trailhead" in out
    lots = _run_pool(tmp_path, geom, bundle, doc, "--add-keeps")
    assert len(lots) == 2 and any(r[2] == "Hidden Trailhead" for r in lots)


def test_pool_without_a_sidecar_is_unchanged(tmp_path):
    geom, bundle = _geom_dir(tmp_path, {"a": [{"lat": LAT, "lon": LON}]})
    out = tmp_path / "pool.json"
    rc = pool.main(["--geom-dir", str(geom), "--bundle", str(bundle), "--out", str(out),
                    "--verdicts", str(tmp_path / "missing.json")])
    assert rc == 0 and len(json.load(open(out))) == 1


# -------------------------------------------------------------------- sweep

def test_sweep_removes_matched_lots_but_never_empties_an_area(tmp_path):
    other = _offset(LAT, LON, north_m=300)
    geom, _ = _geom_dir(tmp_path, {
        "two": [{"lat": LAT, "lon": LON}, {"lat": other[0], "lon": other[1]}],
        "one": [{"lat": LAT, "lon": LON}],
    })
    v = pv.Verdicts(_doc(_entry("way/1", "DROP")))
    r = sweep.sweep(str(geom), v, dry_run=True)
    assert [s for s, _ in r["refused"]] == ["one"] and r["changed"] == ["two"]
    assert json.load(open(geom / "two.json"))["parking"] and \
        len(json.load(open(geom / "two.json"))["parking"]) == 2, "dry run wrote nothing"
    r = sweep.sweep(str(geom), v, dry_run=False)
    assert len(json.load(open(geom / "two.json"))["parking"]) == 1
    assert len(json.load(open(geom / "one.json"))["parking"]) == 1
    assert r["reasons"] == {"too-far": 1}
    # Written exactly as add-parking.py writes geom (`json.dumps(geom)`), so a
    # swept file differs from a rolled one only in the lots removed.
    doc = json.load(open(geom / "two.json"))
    assert (geom / "two.json").read_text() == json.dumps(doc)


# --------------------------------------------------------------- add-parking

def test_parse_parking_emits_the_osm_id_that_ships_in_geom():
    data = {"elements": [
        {"type": "way", "id": 912577538, "center": {"lat": LAT, "lon": LON},
         "tags": {"amenity": "parking", "name": "Yard"}},
        {"type": "node", "id": 5, "lat": LAT, "lon": LON, "tags": {"amenity": "parking"}},
    ]}
    lots = ap.parse_parking(data)
    assert [l["osm"] for l in lots] == ["way/912577538", "node/5"]
    # It survives the geom-ready strip (only underscore keys are internal).
    assert ap._strip_internal(lots)[0]["osm"] == "way/912577538"


def test_apply_verdicts_removes_judged_drops_by_id_before_assignment():
    v = pv.Verdicts(_doc(_entry("way/912577538", "DROP")))
    far = _offset(LAT, LON, north_m=2000)
    lots = ap.parse_parking({"elements": [
        {"type": "way", "id": 912577538, "center": {"lat": far[0], "lon": far[1]},
         "tags": {"amenity": "parking"}},                       # judged, moved 2 km: id wins
        {"type": "way", "id": 42, "center": {"lat": far[0], "lon": far[1]},
         "tags": {"amenity": "parking", "name": "Innocent"}},
    ]})
    kept, gone = ap.apply_verdicts(lots, v)
    assert [l["osm"] for l in kept] == ["way/42"]
    assert gone[0][0]["osm"] == "way/912577538" and gone[0][1]["verdict"] == "DROP"
    assert ap.apply_verdicts(lots, None) == (lots, [])


def test_generator_refuses_a_second_entry_that_disagrees_with_its_cluster(tmp_path, capsys):
    data = _store_and_dossier(tmp_path)                      # way/1 + node/2 judged DROP
    path = data / "phx_verdicts_osm.json"
    store = json.loads(path.read_text())
    store["node/2"] = dict(store["way/1"], osm=["node/2"], verdict="KEEP")
    path.write_text(json.dumps(store))
    doc, notes, folded = build_verdicts.build(str(data))
    assert folded == [] and len(notes) == 1 and "says KEEP" in notes[0]
    out = tmp_path / "parking-verdicts.json"
    assert build_verdicts.main(["--data-dir", str(data), "--out", str(out)]) == 1
    assert not out.exists()
