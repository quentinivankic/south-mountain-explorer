#!/usr/bin/env python3
"""Tests for the parking-verdicts sidecar: the shared matcher, the generator,
and the three consumers (pool builder, geom sweep, add-parking gate)."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import stat
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
TOOLS = HERE / "parking-adjud" / "tools"
for path in (str(HERE), str(TOOLS)):
    if path not in sys.path:
        sys.path.insert(0, path)
import _parking_verdicts as pv  # noqa: E402
import dossier_output  # noqa: E402


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


@pytest.mark.parametrize("raw, message", [
    (b'{"version":1,"lots":{},"lots":{}}', "duplicate JSON key"),
    (b'{"version":1,"lots":{"way/1":{"verdict":"DROP"}}}', "lat"),
])
def test_strict_mutation_loader_rejects_silently_omitted_rows(raw, message):
    with pytest.raises(ValueError, match=message):
        pv.strict_verdicts_bytes(raw)


def test_strict_mutation_loader_rejects_overmatching_ring():
    document = _doc(_entry(
        "way/1", "DROP",
        rings=[[[LAT, LON], [LAT + 1.0, LON], [LAT, LON]]],
    ))
    with pytest.raises(ValueError, match="at least four points"):
        pv.strict_verdicts_bytes(json.dumps(document).encode())


# ---------------------------------------------------------------- generator

def _install_generator_baseline(data: Path) -> None:
    """Pin exact controlled stores, dossiers, and empty floors for each build."""
    specs = build_verdicts._store_specs()
    raw = {
        spec.filename: (data / spec.filename).read_bytes()
        for spec in specs
    }
    dossiers = {
        path.name: path.read_bytes()
        for path in sorted(data.glob("*_dossier.json"))
    }
    document = build_verdicts.pvs.build_legacy_baseline_document(
        specs, raw, dossiers
    )
    path = data / f"test-baseline-{document['baseline_sha256']}.json"
    path.write_bytes(build_verdicts.pvs.legacy_baseline_json_bytes(document))
    build_verdicts.LEGACY_ROW_BASELINE_PATH = str(path)
    build_verdicts.LEGACY_ROW_BASELINE_SHA256 = document["baseline_sha256"]
    for spec in specs:
        floor = data / f"{Path(spec.filename).stem}_publication_floor.json"
        if not floor.exists():
            floor.write_bytes(build_verdicts.pvs.publication_floor_json_bytes(
                build_verdicts.pvs.build_empty_publication_floor_document(
                    spec.filename
                )
            ))
    proof_image = {}
    floor_image = {}
    for spec in specs:
        proof = data / f"{Path(spec.filename).stem}_publication_proofs.json"
        proof_image[spec.filename] = proof.read_bytes() if proof.exists() else None
        floor_image[spec.filename] = (
            data / f"{Path(spec.filename).stem}_publication_floor.json"
        ).read_bytes()
    root_document = build_verdicts.pvs.build_publication_trust_root_document(
        specs, raw, proof_image, floor_image, path.name, path.read_bytes(),
        dossiers,
    )
    root_path = data / "test-publication-trust-root-v1.json"
    root_bytes = build_verdicts.pvs.publication_trust_root_json_bytes(
        root_document
    )
    root_path.write_bytes(root_bytes)
    build_verdicts.PUBLICATION_TRUST_ROOT_PATH = str(root_path)
    build_verdicts.PUBLICATION_TRUST_ROOT_SHA256 = hashlib.sha256(
        root_bytes
    ).hexdigest()


def _install_dossier_generation(data: Path, dossier: dict) -> None:
    slug = dossier["slug"]
    fids = [facility["fid"] for facility in dossier["facilities"]]
    sidecars = {
        "serves2": {str(fid): {"served": False} for fid in fids},
        "context": {str(fid): {"category": "NEUTRAL"} for fid in fids},
        "walk": {
            str(fid): {"walk_m": None, "conn": "no route", "trail": None}
            for fid in fids
        },
    }
    for suffix, document in sidecars.items():
        (data / f"{slug}_{suffix}.json").write_text(json.dumps(document))
    dossier_output.bootstrap_generation(data, slug)


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
    _install_dossier_generation(data, dossier)
    store = {"way/1": {"osm": ["way/1", "node/2"], "verdict": verdict, "prior": "surveyed",
                       "exists": {"call": "yes", "evidence": "Z2: striped lot"},
                       "public": {"call": "yes", "evidence": "no access tag"},
                       "serves": {"call": "no" if verdict == "DROP" else "yes",
                                  "evidence": serves_evidence},
                       "confidence": "strong", "src": "test"}}
    for name, _, _ in build_verdicts.STORES:
        (data / name).write_text(json.dumps(store if name.startswith("phx") else {}))
    _install_generator_baseline(data)
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
    store["node/2"] = copy.deepcopy(store["way/1"])
    path.write_text(json.dumps(store))
    _install_generator_baseline(data)
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
    _install_generator_baseline(data)
    out = tmp_path / "parking-verdicts.json"
    assert build_verdicts.main(["--data-dir", str(data), "--out", str(out)]) == 1
    assert not out.exists()
    assert "could not be placed" in capsys.readouterr().err


def test_generator_places_a_self_contained_store_entry_without_a_dossier(tmp_path):
    """The Colorado store (merge_drafts.py) embeds lat/lon/rings/name/judged in
    every entry because its dossiers are not committed; the entry places
    itself, carries its own date, and a human override rides along."""
    data = _store_and_dossier(tmp_path)
    far_lat, far_lon = LAT + 0.5, LON + 0.5           # nowhere near the dossier lot
    ring = [[far_lat, far_lon], [far_lat + 1e-4, far_lon], [far_lat + 1e-4, far_lon + 1e-4],
            [far_lat, far_lon + 1e-4], [far_lat, far_lon]]
    co = {"way/9": {"fid": 9, "area": "indian-peaks-wilderness-co", "osm": ["way/9"],
                    "verdict": "DROP", "prior": "bare", "confidence": "strong",
                    "exists": {"call": "yes", "evidence": "Z2: paved apron, 3 cars"},
                    "public": {"call": "unclear", "evidence": "no access tag"},
                    "serves": {"call": "unclear", "evidence": "cloud on Z1/Z2"},
                    "resolve_hint": None,
                    "override": {"from": "REVIEW", "by": "human", "date": "2026-09-13",
                                 "note": "sheet review", "resolve_hint": "fetch a clear frame"},
                    "lat": far_lat, "lon": far_lon, "rings": [ring], "name": "Far Lot",
                    "judged": "2026-09-13", "src": "judge-fanout"}}
    (data / "co_verdicts_osm.json").write_text(json.dumps(co))
    _install_generator_baseline(data)
    doc, notes, folded = build_verdicts.build(str(data))
    assert notes == [] and folded == []
    e = doc["lots"]["way/9"]
    assert (e["lat"], e["lon"]) == (far_lat, far_lon) and e["name"] == "Far Lot"
    assert e["rings"] == [[[round(p[0], 6), round(p[1], 6)] for p in ring]]
    assert e["area"] == "indian-peaks-wilderness-co" and e["judged"] == "2026-09-13"
    assert e["verdict"] == "DROP" and e["reason"] == "dropped"     # no axis said no
    assert e["override"]["from"] == "REVIEW" and e["override"]["by"] == "human"
    assert "resolve_hint" not in e                                 # nulled by the flip
    # The dossier-backed entry is untouched and still dated from STORES.
    assert doc["lots"]["way/1"]["judged"] == "2026-08-01"
    # And the matcher finds the far lot by footprint, not just by id.
    v = pv.Verdicts(doc)
    assert v.drop_for({"lat": far_lat + 5e-5, "lon": far_lon + 5e-5}) is not None


def test_generator_still_refuses_an_entry_with_neither_dossier_nor_position(tmp_path, capsys):
    data = _store_and_dossier(tmp_path)
    co = {"way/77": {"fid": 77, "area": "x-co", "osm": ["way/77"], "verdict": "KEEP",
                     "prior": "bare", "confidence": "strong", "exists": {"call": "yes", "evidence": "Z2"},
                     "public": {"call": "yes", "evidence": "t"}, "serves": {"call": "yes", "evidence": "t"}}}
    (data / "co_verdicts_osm.json").write_text(json.dumps(co))
    _install_generator_baseline(data)
    doc, notes, _ = build_verdicts.build(str(data))
    assert "way/77" not in doc["lots"] and len(notes) == 1 and "no dossier position" in notes[0]


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
    assert "ADDED 1" in capsys.readouterr().out


def test_pool_add_keeps_holds_leaning_verdicts_unless_asked_and_merges_close_pairs(tmp_path, capsys):
    geom, bundle = _geom_dir(tmp_path, {"a": [{"lat": LAT, "lon": LON}]})
    strong = _offset(LAT, LON, north_m=500)
    twin = _offset(strong[0], strong[1], east_m=25)              # 25 m from `strong`: one pin
    soft = _offset(LAT, LON, north_m=1000)
    unrated = _offset(LAT, LON, north_m=1500)
    doc = _doc(_entry("way/2", "KEEP", lat=strong[0], lon=strong[1], name="Main Lot"),
               _entry("way/3", "KEEP", lat=twin[0], lon=twin[1], name="Overflow Pad"),
               _entry("node/4", "KEEP", lat=soft[0], lon=soft[1], name="Maybe Pull-off",
                      confidence="leaning"),
               _entry("node/5", "KEEP", lat=unrated[0], lon=unrated[1], name="Unrated",
                      confidence=None))
    lots = _run_pool(tmp_path, geom, bundle, doc, "--add-keeps")
    out = capsys.readouterr().out
    assert len(lots) == 2                                          # a's lot + Main Lot
    assert not any(r[2] in ("Maybe Pull-off", "Unrated") for r in lots)
    assert "ADDED 1, 1 merged into a neighbouring pin, 2 leaning held" in out
    assert "held  node/4" in out and "held  node/5" in out
    # The leaning flag releases the leaning verdict only; an unrated one stays
    # held — unknown confidence fails closed.
    lots = _run_pool(tmp_path, geom, bundle, doc, "--add-keeps", "--add-leaning-keeps")
    assert len(lots) == 3 and any(r[2] == "Maybe Pull-off" for r in lots)
    assert not any(r[2] == "Unrated" for r in lots)


def test_pool_without_a_sidecar_is_unchanged(tmp_path):
    geom, bundle = _geom_dir(tmp_path, {"a": [{"lat": LAT, "lon": LON}]})
    out = tmp_path / "pool.json"
    rc = pool.main(["--geom-dir", str(geom), "--bundle", str(bundle), "--out", str(out),
                    "--verdicts", str(tmp_path / "missing.json")])
    assert rc == 0 and len(json.load(open(out))) == 1


# -------------------------------------------------------------------- sweep


def test_production_geom_is_a_parking_verdict_fixed_point():
    repo = HERE.parent
    sidecar_path = repo / "public/areas/parking-verdicts.json"
    sidecar_document = json.loads(sidecar_path.read_bytes())
    assert len(sidecar_document["lots"]) == 1253, (
        "sidecar lot count changed; confirm intentional corpus growth or "
        "shrinkage before updating this production pin"
    )
    assert sum(
        row["verdict"] == "DROP"
        for row in sidecar_document["lots"].values()
    ) == 372, (
        "sidecar DROP count changed; confirm intentional verdict changes "
        "before updating this production pin"
    )
    geom_dir = repo / "public/areas/geom"
    assert len(list(geom_dir.glob("*.json"))) == 9074, (
        "flat geom file count changed; confirm intentional corpus growth, "
        "shrinkage, or layout changes before updating this production pin"
    )

    verdicts = pv.strict_verdicts_bytes(sidecar_path.read_bytes())
    result = sweep.sweep(str(geom_dir), verdicts, dry_run=True)
    assert result["removed"] == []
    assert result["changed"] == []
    assert result["refused"] == []
    assert result["reviewed_empty"] == []
    assert not result["reasons"]


def test_sweep_planner_is_pure_and_refusal_blocks_every_target(tmp_path):
    other = _offset(LAT, LON, north_m=300)
    geom, _ = _geom_dir(tmp_path, {
        "two": [{"lat": LAT, "lon": LON}, {"lat": other[0], "lon": other[1]}],
        "one": [{"lat": LAT, "lon": LON}],
    })
    document = _doc(_entry("way/1", "DROP"))
    verdicts = pv.Verdicts(document)
    before = {path.name: path.read_bytes() for path in geom.glob("*.json")}

    dry = sweep.sweep(str(geom), verdicts, dry_run=True)
    planned = sweep.sweep(str(geom), verdicts, dry_run=False)
    assert [slug for slug, _ in dry["refused"]] == ["one"]
    assert dry["changed"] == ["two"] and planned == dry
    assert dry["reasons"] == {"too-far": 1}
    assert {path.name: path.read_bytes() for path in geom.glob("*.json")} == before

    sidecar = tmp_path / "verdicts.json"
    sidecar.write_text(json.dumps(document))
    assert sweep.run(geom, sidecar, False) == 2
    assert {path.name: path.read_bytes() for path in geom.glob("*.json")} == before


def test_sweep_allows_only_exact_reviewed_empty_signature(tmp_path):
    geom, _ = _geom_dir(tmp_path, {"approved": [{"lat": LAT, "lon": LON}]})
    verdicts = pv.Verdicts(_doc(_entry("way/1", "DROP", reason="not-public")))
    policy = {"approved": (("way/1", "not-public"),)}
    before = (geom / "approved.json").read_bytes()

    dry = sweep.sweep(str(geom), verdicts, dry_run=True,
                      reviewed_empty_signatures=policy)
    assert dry["refused"] == []
    assert dry["reviewed_empty"] == [
        ("approved", 1, (("way/1", "not-public"),))
    ]
    assert dry["changed"] == ["approved"] and len(dry["removed"]) == 1
    assert (geom / "approved.json").read_bytes() == before

    sidecar = tmp_path / "verdicts.json"
    sidecar.write_text(json.dumps(_doc(_entry(
        "way/1", "DROP", reason="not-public",
    ))))
    with pytest.raises(ValueError, match="code-approved policy"):
        sweep.run(
            geom, sidecar, False, reviewed_empty_signatures=policy,
        )
    assert (geom / "approved.json").read_bytes() == before


def test_sweep_reviewed_empty_signature_drift_refuses(tmp_path):
    other = _offset(LAT, LON, north_m=300)
    geom, _ = _geom_dir(tmp_path, {
        "wrong-reason": [{"lat": LAT, "lon": LON}],
        "extra-drop": [{"lat": LAT, "lon": LON},
                       {"lat": other[0], "lon": other[1]}],
    })
    verdicts = pv.Verdicts(_doc(
        _entry("way/1", "DROP", reason="too-far"),
        _entry("way/2", "DROP", lat=other[0], lon=other[1], reason="not-public"),
    ))
    policy = {
        "wrong-reason": (("way/1", "not-public"),),
        "extra-drop": (("way/1", "too-far"),),
    }
    before = {path.name: path.read_bytes() for path in geom.glob("*.json")}
    result = sweep.sweep(str(geom), verdicts, dry_run=False,
                         reviewed_empty_signatures=policy)
    assert sorted(result["refused"]) == [("extra-drop", 2), ("wrong-reason", 1)]
    assert result["reviewed_empty"] == [] and result["changed"] == []
    assert {path.name: path.read_bytes() for path in geom.glob("*.json")} == before


def test_production_reviewed_empty_signatures_are_exact_singletons(tmp_path, capsys):
    assert sweep._REVIEWED_EMPTY_SIGNATURES == {
        "mesa-valley-open-space-co": (("way/58294967", "not-public"),),
        "promntory-point-open-space-co": (("way/1206954210", "not-public"),),
        "sondermann-park-co": (("way/58294967", "not-public"),),
    }
    church = _offset(LAT, LON, north_m=300)
    geom, _ = _geom_dir(tmp_path, {
        "mesa-valley-open-space-co": [{"lat": LAT, "lon": LON}],
        "sondermann-park-co": [{"lat": LAT, "lon": LON}],
        "promntory-point-open-space-co": [{"lat": church[0], "lon": church[1]}],
    })
    document = _doc(
        _entry("way/58294967", "DROP", reason="not-public"),
        _entry("way/1206954210", "DROP", lat=church[0], lon=church[1],
               reason="not-public"),
    )
    verdicts = pv.Verdicts(document)
    result = sweep.sweep(str(geom), verdicts, dry_run=False)
    assert [slug for slug, _, _ in result["reviewed_empty"]] == [
        "mesa-valley-open-space-co", "promntory-point-open-space-co",
        "sondermann-park-co"
    ]
    assert result["refused"] == []
    sidecar = tmp_path / "verdicts.json"
    sidecar.write_text(json.dumps(document))
    assert sweep.run(geom, sidecar, False) == 0
    captured = capsys.readouterr()
    for slug in sweep._REVIEWED_EMPTY_SIGNATURES:
        assert f"REVIEWED-EMPTY {slug} — 1 flagged, exact signature" in captured.out
    assert "REFUSING" not in captured.err
    assert all(json.loads(path.read_text())["parking"] == []
               for path in geom.glob("*.json"))


def _sweep_transaction_fixture(tmp_path):
    other = _offset(LAT, LON, north_m=350)
    geom, _ = _geom_dir(tmp_path, {
        "area-a": [
            {"lat": LAT, "lon": LON, "name": "drop-a"},
            {"lat": other[0], "lon": other[1], "name": "keep-a"},
        ],
        "area-b": [
            {"lat": LAT, "lon": LON, "name": "drop-b"},
            {"lat": other[0], "lon": other[1], "name": "keep-b"},
        ],
    })
    sidecar = tmp_path / "parking-verdicts.json"
    sidecar.write_bytes(json.dumps(_doc(_entry("way/1", "DROP"))).encode())
    return geom, sidecar


def _interrupt_sweep_after(monkeypatch, count):
    original = sweep._replace_target
    calls = {"count": 0}

    def interrupted(stage, target, before, after):
        if count == 0:
            raise OSError("fault before first replacement")
        original(stage, target, before, after)
        calls["count"] += 1
        if calls["count"] == count:
            raise OSError(f"fault after replacement {count}")

    monkeypatch.setattr(sweep, "_replace_target", interrupted)
    return original


def test_sweep_rejects_degenerate_large_and_distant_rings_without_mutation(
        tmp_path):
    geom, sidecar = _sweep_transaction_fixture(tmp_path)
    before = {path.name: path.read_bytes() for path in geom.glob("*.json")}
    far = _offset(LAT, LON, north_m=5_000)
    p1 = [LAT, LON]
    p2 = [far[0], far[1]]
    large_sw = _offset(LAT, LON, north_m=-1_500, east_m=-1_500)
    large_ne = _offset(LAT, LON, north_m=1_500, east_m=1_500)
    distant_center = _offset(LAT, LON, north_m=300)
    distant = [
        list(_offset(*distant_center, north_m=-20, east_m=-20)),
        list(_offset(*distant_center, north_m=-20, east_m=20)),
        list(_offset(*distant_center, north_m=20, east_m=20)),
        list(_offset(*distant_center, north_m=20, east_m=-20)),
    ]
    distant.append(distant[0])
    cases = [
        [[p1]],
        [[p1, p2, p1, p2, p1]],
        [[*RECT[:-1]]],
        [[[LAT, LON], [LAT, LON], [LAT, LON], [LAT, LON]]],
        [[[LAT, LON], [LAT, LON + 0.0001], [LAT, LON + 0.0002],
          [LAT, LON]]],
        [[list(large_sw), [large_sw[0], large_ne[1]], list(large_ne),
          [large_ne[0], large_sw[1]], list(large_sw)]],
        [distant],
    ]
    for rings in cases:
        sidecar.write_bytes(json.dumps(_doc(_entry(
            "way/1", "DROP", rings=rings,
        ))).encode())
        with pytest.raises(ValueError):
            sweep.run(geom, sidecar, False)
        assert {path.name: path.read_bytes() for path in geom.glob("*.json")} == before
        assert not (tmp_path / ".parking-sweep-transactions" / "live.journal.json").exists()


def test_sweep_rejects_all_report_control_categories_and_cannot_forge_status(
        tmp_path, capsys):
    geom, sidecar = _sweep_transaction_fixture(tmp_path)
    controls = ["\n", "\r", "\x1b", "\x85", "\u202e", "\u2028", "\u2029"]
    for control in controls:
        hostile = _entry(
            "way/1", "DROP",
            reason=f"too-far{control}RECOVERED forged-transaction",
            evidence={"serves": f"evidence{control}NOT APPLIED: forged"},
        )
        sidecar.write_bytes(json.dumps(_doc(hostile)).encode())
        assert sweep.main([
            "--geom-dir", str(geom), "--sidecar", str(sidecar), "--dry-run",
        ]) == 1
        captured = capsys.readouterr()
        physical = (captured.out + captured.err).splitlines()
        assert "RECOVERED forged-transaction" not in physical
        assert "NOT APPLIED: forged" not in physical


def test_report_renderer_single_line_escapes_untrusted_fields_even_if_bypassed():
    entry = _entry(
        "way/1\nRECOVERED forged", "DROP",
        reason="too-far\nNOT APPLIED: forged",
        evidence={"serves": "evidence\rREFUSING forged"},
    )
    entry["_key"] = entry["osm"][0]
    result = {
        "reasons": sweep.Counter({entry["reason"]: 1}),
        "removed": [("area\nRECOVERED forged", {
            "lat": LAT, "lon": LON, "name": "lot\nNOT APPLIED: forged",
        }, entry)],
        "refused": [("refused\nRECOVERED forged", 1)],
        "reviewed_empty": [],
        "changed": ["area"],
    }
    stdout, stderr = sweep._render_report(result, dry_run=True, apply_refused=False)
    physical = (stdout + stderr).splitlines()
    assert "RECOVERED forged" not in physical
    assert "NOT APPLIED: forged" not in physical
    assert "REFUSING forged" not in physical
    assert "\\nRECOVERED forged" in stdout
    assert "\\rREFUSING forged" in stdout


def test_sweep_strict_json_rejects_duplicate_and_nonfinite_sidecar_and_geom(
        tmp_path):
    geom, sidecar = _sweep_transaction_fixture(tmp_path)
    target = geom / "area-a.json"
    original_target = target.read_bytes()
    invalid_sidecars = [
        b'{"version":1,"version":1,"lots":{}}',
        b'{"version":1,"lots":{"way/1":{"lat":NaN}}}',
    ]
    for raw in invalid_sidecars:
        sidecar.write_bytes(raw)
        with pytest.raises(ValueError, match="strict JSON"):
            sweep.run(geom, sidecar, True)
        assert target.read_bytes() == original_target
    sidecar.write_bytes(json.dumps(_doc(_entry("way/1", "DROP"))).encode())
    invalid_geom = [
        b'{"parking":[],"parking":[]}',
        b'{"parking":[{"lat":NaN,"lon":0}]}',
    ]
    for raw in invalid_geom:
        target.write_bytes(raw)
        with pytest.raises(ValueError, match="strict JSON"):
            sweep.run(geom, sidecar, True)


def test_sidecar_and_geom_share_emoji_zero_width_joiner_policy():
    for name in ("🐦‍⬛ Parking", "👩🏽‍💻 Trailhead"):
        pv.strict_verdicts_document(
            _doc(_entry("way/1", "KEEP", name=name))
        )
    for hostile in (
        "\u200dadmin",
        "admin\u200d",
        "ad\u200dmin",
        "🐦\u200d\u200d⬛",
    "©\u200d®",
    ):
        with pytest.raises(ValueError, match="forbidden control"):
            pv.strict_verdicts_document(
                _doc(_entry("way/1", "KEEP", name=hostile))
            )


def test_sweep_allows_emoji_zero_width_joiner_in_geom_text(tmp_path):
    geom, sidecar = _sweep_transaction_fixture(tmp_path)
    target = geom / "area-a.json"
    document = json.loads(target.read_text())
    document["trails"] = [
        {"name": "🐦‍⬛ Path"},
        {"name": "👩🏽‍💻 Trail"},
    ]
    target.write_text(json.dumps(document))
    assert sweep.run(geom, sidecar, True) == 0


@pytest.mark.parametrize("hostile", [
    "\u200dadmin",
    "admin\u200d",
    "ad\u200dmin",
    "🐦\u200d\u200d⬛",
    "©\u200d®",
])
def test_sweep_rejects_zero_width_joiner_outside_emoji_sequence(
        tmp_path, hostile):
    geom, sidecar = _sweep_transaction_fixture(tmp_path)
    target = geom / "area-a.json"
    document = json.loads(target.read_text())
    document["unknown"] = hostile
    target.write_text(json.dumps(document))
    before = target.read_bytes()
    with pytest.raises(ValueError, match="forbidden control"):
        sweep.run(geom, sidecar, True)
    assert target.read_bytes() == before


def test_sweep_rejects_control_text_in_unknown_geom_fields(tmp_path):
    geom, sidecar = _sweep_transaction_fixture(tmp_path)
    target = geom / "area-a.json"
    document = json.loads(target.read_text())
    document["unknown"] = "safe\nNOT APPLIED: forged"
    target.write_text(json.dumps(document))
    before = target.read_bytes()
    with pytest.raises(ValueError, match="forbidden control"):
        sweep.run(geom, sidecar, False)
    assert target.read_bytes() == before


def test_sweep_refusal_is_truthful_global_and_exit_two(tmp_path, capsys):
    other = _offset(LAT, LON, north_m=300)
    geom, _ = _geom_dir(tmp_path, {
        "changed": [{"lat": LAT, "lon": LON},
                    {"lat": other[0], "lon": other[1]}],
        "refused": [{"lat": LAT, "lon": LON}],
    })
    sidecar = tmp_path / "parking-verdicts.json"
    sidecar.write_text(json.dumps(_doc(_entry("way/1", "DROP"))))
    before = {path.name: path.read_bytes() for path in geom.glob("*.json")}
    assert sweep.run(geom, sidecar, False) == 2
    captured = capsys.readouterr()
    assert captured.out.splitlines()[0] == (
        "NOT APPLIED — 0 lots removed; planned 1 lot(s) from 1 area(s)"
    )
    assert "planned removals:" in captured.out
    assert "removed 1 lot" not in captured.out
    assert "REFUSING to empty refused — 1 flagged, 0 would remain" in captured.err
    assert "NOT APPLIED:" in captured.err
    assert {path.name: path.read_bytes() for path in geom.glob("*.json")} == before


def test_sweep_dry_run_is_recursively_nonmutating(tmp_path, capsys):
    geom, sidecar = _sweep_transaction_fixture(tmp_path)
    before = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*") if path.is_file()
    }
    assert sweep.run(geom, sidecar, True) == 0
    assert capsys.readouterr().out.startswith("DRY-RUN — would remove 2 lot(s)")
    after = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*") if path.is_file()
    }
    assert after == before
    assert not (tmp_path / ".parking-sweep-transactions").exists()


@pytest.mark.parametrize("drift", ["same-byte-inode", "changed-sidecar", "current-policy"])
def test_prepared_recovery_uses_retained_sidecar_and_approved_policy(
        tmp_path, monkeypatch, capsys, drift):
    geom, sidecar = _sweep_transaction_fixture(tmp_path)
    original_replace = _interrupt_sweep_after(monkeypatch, 1)
    with pytest.raises(sweep.RecoveryRequired, match="live transaction retained"):
        sweep.run(geom, sidecar, False)
    monkeypatch.setattr(sweep, "_replace_target", original_replace)
    live = tmp_path / ".parking-sweep-transactions" / "live.journal.json"
    assert live.exists()
    if drift == "same-byte-inode":
        replacement = tmp_path / "sidecar-refresh.json"
        replacement.write_bytes(sidecar.read_bytes())
        os.replace(replacement, sidecar)
    elif drift == "changed-sidecar":
        sidecar.write_text(json.dumps({"version": 1, "lots": {}}))
    else:
        monkeypatch.setattr(sweep, "_REVIEWED_EMPTY_SIGNATURES", {})
    assert sweep.run(geom, sidecar, False) == 0
    captured = capsys.readouterr().out
    assert "removed 2 lot(s) from 2 area(s)" in captured
    assert "RECOVERED " in captured
    assert not live.exists()
    assert all(len(json.loads(path.read_text())["parking"]) == 1
               for path in geom.glob("*.json"))


def test_prepared_recovery_refuses_unsafe_target_drift_without_more_mutation(
        tmp_path, monkeypatch):
    geom, sidecar = _sweep_transaction_fixture(tmp_path)
    original_replace = _interrupt_sweep_after(monkeypatch, 1)
    with pytest.raises(sweep.RecoveryRequired):
        sweep.run(geom, sidecar, False)
    monkeypatch.setattr(sweep, "_replace_target", original_replace)
    first = geom / "area-a.json"
    second = geom / "area-b.json"
    first_after = first.read_bytes()
    drifted = json.loads(second.read_text())
    drifted["unrelated"] = True
    second.write_text(json.dumps(drifted))
    second_drift = second.read_bytes()
    with pytest.raises(sweep.RecoveryRequired, match="neither"):
        sweep.run(geom, sidecar, False)
    assert first.read_bytes() == first_after
    assert second.read_bytes() == second_drift


@pytest.mark.parametrize("prefix", [0, 1, 2])
def test_every_prepared_replacement_prefix_rolls_forward(
        tmp_path, monkeypatch, prefix):
    geom, sidecar = _sweep_transaction_fixture(tmp_path)
    original_replace = _interrupt_sweep_after(monkeypatch, prefix)
    with pytest.raises(sweep.RecoveryRequired):
        sweep.run(geom, sidecar, False)
    monkeypatch.setattr(sweep, "_replace_target", original_replace)
    assert sweep.run(geom, sidecar, False) == 0
    assert all(len(json.loads(path.read_text())["parking"]) == 1
               for path in geom.glob("*.json"))


@pytest.mark.parametrize(
    "artifact", ["sidecar_snapshot", "backup", "stage", "receipt", "archive"],
)
def test_prepared_recovery_rejects_artifact_tamper_without_mutation(
        tmp_path, monkeypatch, artifact):
    geom, sidecar = _sweep_transaction_fixture(tmp_path)
    original_replace = _interrupt_sweep_after(monkeypatch, 1)
    with pytest.raises(sweep.RecoveryRequired):
        sweep.run(geom, sidecar, False)
    monkeypatch.setattr(sweep, "_replace_target", original_replace)
    live = tmp_path / ".parking-sweep-transactions" / "live.journal.json"
    journal = json.loads(live.read_text())
    if artifact == "sidecar_snapshot":
        path = Path(journal["artifacts"]["sidecar_snapshot"])
    elif artifact in {"receipt", "archive"}:
        path = Path(journal["artifacts"][artifact])
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_bytes(b"tamper")
        os.chmod(path, 0o600)
    else:
        path = Path(journal["artifacts"]["targets"][1][artifact])
    if artifact not in {"receipt", "archive"}:
        path.write_bytes(path.read_bytes() + b"tamper")
    before = {path.name: path.read_bytes() for path in geom.glob("*.json")}
    with pytest.raises(sweep.RecoveryRequired):
        sweep.run(geom, sidecar, False)
    assert {path.name: path.read_bytes() for path in geom.glob("*.json")} == before


def test_pre_journal_orphans_are_idempotently_reused(tmp_path):
    geom, sidecar_path = _sweep_transaction_fixture(tmp_path)
    sidecar = sweep._capture_sidecar(sidecar_path)
    inventory = sweep._capture_inventory(geom)
    plan = sweep._plan_inventory(
        sidecar, inventory, sweep._REVIEWED_EMPTY_SIGNATURES, dry_run=False,
    )
    sweep._prepare_transaction(plan)
    _root, entries = sweep._validate_initial_paths(sidecar_path, geom)
    sweep._validate_transaction_paths(plan, entries)
    sweep._write_transaction_artifacts(plan)
    assert not plan["paths"]["live"].exists()
    assert sweep.run(geom, sidecar_path, False) == 0
    assert all(len(json.loads(path.read_text())["parking"]) == 1
               for path in geom.glob("*.json"))


@pytest.mark.parametrize("tail", ["after-receipt", "after-print", "archive-failure"])
def test_terminal_report_precedes_archive_and_retry_recovers(
        tmp_path, monkeypatch, capsys, tail):
    geom, sidecar = _sweep_transaction_fixture(tmp_path)
    if tail == "after-receipt":
        original = sweep._finish_transaction

        def fail_after_receipt(plan):
            original(plan)
            raise OSError("fault after receipt")

        monkeypatch.setattr(sweep, "_finish_transaction", fail_after_receipt)
    elif tail == "after-print":
        original = sweep._print_report

        def fail_after_print(plan, **kwargs):
            original(plan, **kwargs)
            raise OSError("fault after report flush")

        monkeypatch.setattr(sweep, "_print_report", fail_after_print)
    else:
        original = sweep._archive_journal

        def fail_archive(_plan):
            raise OSError("fault before archive")

        monkeypatch.setattr(sweep, "_archive_journal", fail_archive)
    with pytest.raises((OSError, sweep.RecoveryRequired)):
        sweep.run(geom, sidecar, False)
    monkeypatch.setattr(
        sweep,
        {"after-receipt": "_finish_transaction",
         "after-print": "_print_report",
         "archive-failure": "_archive_journal"}[tail],
        original,
    )
    live = tmp_path / ".parking-sweep-transactions" / "live.journal.json"
    assert live.exists()
    capsys.readouterr()
    assert sweep.run(geom, sidecar, False) == 0
    replay = capsys.readouterr().out
    assert "removed 2 lot(s) from 2 area(s)" in replay
    assert "RECOVERED " in replay
    assert not live.exists()


def test_archive_tail_failure_cannot_precede_the_bound_report(
        tmp_path, monkeypatch, capsys):
    geom, sidecar = _sweep_transaction_fixture(tmp_path)
    original_archive = sweep._archive_journal

    def fail_after_archive(plan):
        original_archive(plan)
        raise OSError("fault after archive durability")

    monkeypatch.setattr(sweep, "_archive_journal", fail_after_archive)
    with pytest.raises(OSError, match="after archive durability"):
        sweep.run(geom, sidecar, False)
    first = capsys.readouterr().out
    assert "removed 2 lot(s) from 2 area(s)" in first
    assert not (tmp_path / ".parking-sweep-transactions" / "live.journal.json").exists()
    monkeypatch.setattr(sweep, "_archive_journal", original_archive)
    after = {path.name: path.read_bytes() for path in geom.glob("*.json")}
    assert sweep.run(geom, sidecar, False) == 0
    assert capsys.readouterr().out.startswith("removed 0 lot(s) from 0 area(s)")
    assert {path.name: path.read_bytes() for path in geom.glob("*.json")} == after


def test_completed_recovery_is_idempotent_after_archive(
        tmp_path, monkeypatch, capsys):
    geom, sidecar = _sweep_transaction_fixture(tmp_path)
    original_replace = _interrupt_sweep_after(monkeypatch, 1)
    with pytest.raises(sweep.RecoveryRequired):
        sweep.run(geom, sidecar, False)
    monkeypatch.setattr(sweep, "_replace_target", original_replace)
    assert sweep.run(geom, sidecar, False) == 0
    capsys.readouterr()
    after = {path.name: path.read_bytes() for path in geom.glob("*.json")}
    archive = tmp_path / ".parking-sweep-transactions" / "Archive"
    archived = sorted(archive.glob("*.journal.json"))
    assert len(archived) == 1
    assert sweep.run(geom, sidecar, False) == 0
    assert capsys.readouterr().out.startswith("removed 0 lot(s) from 0 area(s)")
    assert {path.name: path.read_bytes() for path in geom.glob("*.json")} == after
    assert sorted(archive.glob("*.journal.json")) == archived


def test_sweep_preserves_nondefault_supplementary_gid(tmp_path):
    geom, sidecar = _sweep_transaction_fixture(tmp_path)
    target = geom / "area-a.json"
    supplementary = next(gid for gid in os.getgroups() if gid != os.getegid())
    os.chown(target, -1, supplementary)
    assert target.stat().st_gid == supplementary
    assert sweep.run(geom, sidecar, False) == 0
    assert target.stat().st_gid == supplementary


def test_sweep_refuses_unpreservable_gid_before_journal_or_geom_mutation(
        tmp_path, monkeypatch):
    geom, sidecar = _sweep_transaction_fixture(tmp_path)
    before = {path.name: path.read_bytes() for path in geom.glob("*.json")}
    monkeypatch.setattr(sweep, "_settable_gids", lambda: set())
    with pytest.raises(ValueError, match="GID cannot be preserved"):
        sweep.run(geom, sidecar, False)
    assert {path.name: path.read_bytes() for path in geom.glob("*.json")} == before
    assert not (tmp_path / ".parking-sweep-transactions" / "live.journal.json").exists()


def test_transaction_id_is_deterministic_and_recomputed_from_identity(tmp_path):
    geom, sidecar_path = _sweep_transaction_fixture(tmp_path)
    sidecar = sweep._capture_sidecar(sidecar_path)
    inventory = sweep._capture_inventory(geom)
    plans = []
    for _ in range(2):
        plan = sweep._plan_inventory(
            sidecar, inventory, sweep._REVIEWED_EMPTY_SIGNATURES, dry_run=False,
        )
        plans.append(sweep._prepare_transaction(plan))
    assert plans[0]["transaction_id"] == plans[1]["transaction_id"]
    assert plans[0]["transaction_id"] == sweep._sha256(
        sweep._canonical_json(plans[0]["identity"]),
    )


@pytest.mark.parametrize("tamper", [
    "transaction-id", "artifact-escape", "policy-authority", "root-schema",
])
def test_recovery_rejects_journal_authority_and_path_tamper_without_more_mutation(
        tmp_path, monkeypatch, tamper):
    geom, sidecar = _sweep_transaction_fixture(tmp_path)
    original_replace = _interrupt_sweep_after(monkeypatch, 1)
    with pytest.raises(sweep.RecoveryRequired):
        sweep.run(geom, sidecar, False)
    monkeypatch.setattr(sweep, "_replace_target", original_replace)
    live = tmp_path / ".parking-sweep-transactions" / "live.journal.json"
    journal = json.loads(live.read_text())
    if tamper == "transaction-id":
        journal["transaction_id"] = "0" * 64
    elif tamper == "artifact-escape":
        journal["artifacts"]["targets"][0]["stage"] = str(tmp_path / "escape.json")
    elif tamper == "policy-authority":
        journal["identity"]["reviewed_empty_policy"]["version"] = "attacker-v1"
        journal["transaction_id"] = sweep._sha256(
            sweep._canonical_json(journal["identity"]),
        )
    else:
        journal["attacker"] = True
    live.write_bytes(sweep._canonical_json(journal))
    before = {path.name: path.read_bytes() for path in geom.glob("*.json")}
    with pytest.raises(sweep.RecoveryRequired):
        sweep.run(geom, sidecar, False)
    assert {path.name: path.read_bytes() for path in geom.glob("*.json")} == before


@pytest.mark.parametrize("kind", ["sidecar-symlink", "geom-symlink", "geom-hardlink"])
def test_sweep_rejects_symlink_and_hardlink_inputs_without_transaction(
        tmp_path, kind):
    geom, sidecar = _sweep_transaction_fixture(tmp_path)
    target = geom / "area-a.json"
    if kind == "sidecar-symlink":
        real = tmp_path / "real-sidecar.json"
        real.write_bytes(sidecar.read_bytes())
        sidecar.unlink()
        sidecar.symlink_to(real)
    elif kind == "geom-symlink":
        real = tmp_path / "real-geom.json"
        real.write_bytes(target.read_bytes())
        target.unlink()
        target.symlink_to(real)
    else:
        os.link(target, tmp_path / "second-link.json")
    with pytest.raises((OSError, ValueError)):
        sweep.run(geom, sidecar, False)
    assert not (tmp_path / ".parking-sweep-transactions" / "live.journal.json").exists()


def test_same_byte_geom_inode_replacement_before_prepared_is_refused(
        tmp_path, monkeypatch):
    geom, sidecar = _sweep_transaction_fixture(tmp_path)
    target = geom / "area-a.json"
    original_plan = sweep._plan_inventory
    replaced = {"done": False}

    def replace_after_plan(*args, **kwargs):
        plan = original_plan(*args, **kwargs)
        if not replaced["done"]:
            replaced["done"] = True
            temporary = tmp_path / "same-bytes.json"
            temporary.write_bytes(target.read_bytes())
            os.replace(temporary, target)
        return plan

    monkeypatch.setattr(sweep, "_plan_inventory", replace_after_plan)
    with pytest.raises(ValueError, match="changed after complete preflight"):
        sweep.run(geom, sidecar, False)
    assert not (tmp_path / ".parking-sweep-transactions" / "live.journal.json").exists()
    assert len(json.loads(target.read_text())["parking"]) == 2


def test_recovery_rejects_geom_inventory_drift_without_advancing_prefix(
        tmp_path, monkeypatch):
    geom, sidecar = _sweep_transaction_fixture(tmp_path)
    original_replace = _interrupt_sweep_after(monkeypatch, 1)
    with pytest.raises(sweep.RecoveryRequired):
        sweep.run(geom, sidecar, False)
    monkeypatch.setattr(sweep, "_replace_target", original_replace)
    first = geom / "area-a.json"
    first_after = first.read_bytes()
    extra = geom / "unrelated.json"
    extra.write_text(json.dumps({"parking": []}))
    with pytest.raises(sweep.RecoveryRequired, match="extra or missing"):
        sweep.run(geom, sidecar, False)
    assert first.read_bytes() == first_after
    assert extra.exists()


def test_recovery_rejects_tampered_terminal_receipt_and_keeps_live_journal(
        tmp_path, monkeypatch):
    geom, sidecar = _sweep_transaction_fixture(tmp_path)
    original_print = sweep._print_report
    monkeypatch.setattr(
        sweep, "_print_report",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("before report")),
    )
    with pytest.raises(OSError, match="before report"):
        sweep.run(geom, sidecar, False)
    monkeypatch.setattr(sweep, "_print_report", original_print)
    live = tmp_path / ".parking-sweep-transactions" / "live.journal.json"
    journal = json.loads(live.read_text())
    receipt = Path(journal["artifacts"]["receipt"])
    receipt.write_bytes(receipt.read_bytes() + b"tamper")
    before = {path.name: path.read_bytes() for path in geom.glob("*.json")}
    with pytest.raises(sweep.RecoveryRequired, match="terminal receipt differs"):
        sweep.run(geom, sidecar, False)
    assert live.exists()
    assert {path.name: path.read_bytes() for path in geom.glob("*.json")} == before


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
    _install_generator_baseline(data)
    with pytest.raises(ValueError, match="does not share one exact row"):
        build_verdicts.build(str(data))
    out = tmp_path / "parking-verdicts.json"
    assert build_verdicts.main(["--data-dir", str(data), "--out", str(out)]) == 1
    assert not out.exists()


def test_generator_propagates_transitive_aliases_before_checking_later_conflict(tmp_path):
    data = _store_and_dossier(tmp_path, verdict="KEEP", serves_evidence="trail access")
    path = data / "phx_verdicts_osm.json"
    store = json.loads(path.read_text())
    base = store["way/1"]
    base["osm"] = ["way/1"]
    embedded = {
        "lat": LAT, "lon": LON, "rings": [RECT], "name": "Test Lot",
        "area": "test-area",
    }
    store["node/2"] = dict(
        base, osm=["node/2", "node/900"], **embedded
    )
    conflicting = copy.deepcopy(base)
    conflicting.update(embedded)
    conflicting["osm"] = ["node/3", "node/900", "way/4"]
    conflicting["verdict"] = "DROP"
    conflicting["serves"] = {"call": "no", "evidence": "different decision"}
    store["node/3"] = conflicting
    path.write_text(json.dumps(store))
    _install_generator_baseline(data)

    doc, notes, folded = build_verdicts.build(str(data))
    assert len(notes) == 1
    assert "different complete decision/authority provenance" in notes[0]
    assert list(doc["lots"]) == ["way/1"]
    assert set(doc["lots"]["way/1"]["osm"]) == {
        "way/1", "node/2", "node/900",
    }
    assert len(folded) == 1
