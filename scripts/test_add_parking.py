#!/usr/bin/env python3
"""Unit tests for add-parking.py's pure transforms (no network)."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "add_parking", Path(__file__).resolve().parent / "add-parking.py")
ap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ap)


# A tiny area: one trail from vertex A (33.7400,-118.3730) to B (33.7405,-118.3725).
GEOM = {"trails": [{"segments": [[[33.7400, -118.3730], [33.7405, -118.3725]]]}]}


def _park_node(nid, lat, lon, **tags):
    tags.setdefault("amenity", "parking")
    return {"type": "node", "id": nid, "lat": lat, "lon": lon, "tags": tags}


def _park_way(wid, lat, lon, **tags):
    tags.setdefault("amenity", "parking")
    return {"type": "way", "id": wid, "center": {"lat": lat, "lon": lon}, "tags": tags}


def _trailhead(nid, lat, lon):
    return {"type": "node", "id": nid, "lat": lat, "lon": lon,
            "tags": {"highway": "trailhead"}}


def _names(lots):
    return {l.get("name") for l in lots}


def test_keeps_lots_near_a_trail_drops_far_ones():
    data = {"elements": [
        _park_node(1, 33.74005, -118.37298, name="Trailhead Lot"),   # ~5 m from A
        _park_way(2, 33.7406, -118.3724, fee="yes"),                 # ~15 m from B
        _park_node(3, 33.7480, -118.3600),                           # ~1 km away -> drop
    ]}
    lots = ap.parking_for_geom(GEOM, data)
    assert len(lots) == 2, lots
    named = [l for l in lots if l.get("name") == "Trailhead Lot"]
    assert named and "fee" not in named[0]
    assert any(l.get("fee") is True for l in lots), "fee=yes -> fee:true"
    assert all("_dist_m" in l for l in lots), "distance captured for the report"


def test_drops_non_public_access_keeps_permissive():
    data = {"elements": [
        _park_node(1, 33.74005, -118.37298, name="Gated", access="private"),
        _park_node(2, 33.74006, -118.37299, name="Store", access="customers"),
        _park_node(3, 33.74007, -118.37300, name="Permit", access="permit"),
        _park_node(4, 33.74008, -118.37301, name="Public"),          # untagged -> keep
        _park_node(5, 33.74009, -118.37302, name="Permissive", access="permissive"),
    ]}
    # Dedup would merge these (all ~<5 m apart); test parse_parking directly.
    parsed = ap.parse_parking(data)
    assert _names(parsed) == {"Public", "Permissive"}, _names(parsed)


def test_drops_on_street_parking():
    data = {"elements": [
        _park_node(1, 33.74005, -118.37298, name="Roadside", parking="street_side"),
        _park_node(2, 33.74006, -118.37299, name="Lane", parking="lane"),
        _park_node(3, 33.74007, -118.37300, name="Shoulder", parking="shoulder"),
        _park_node(4, 33.74008, -118.37301, name="Real Lot", parking="surface"),
    ]}
    assert _names(ap.parse_parking(data)) == {"Real Lot"}


def test_dedups_colocated_lots_preferring_named():
    data = {"elements": [
        _park_node(1, 33.74005, -118.37298),                         # unnamed
        _park_node(2, 33.740051, -118.372981, name="Main Lot"),      # ~<5 m -> same lot
    ]}
    lots = ap.parking_for_geom(GEOM, data)
    assert len(lots) == 1
    assert lots[0].get("name") == "Main Lot"


def test_trailhead_corroboration_keeps_far_lot():
    # A lot ~1.5 km from the trail (would be dropped on proximity) but ~7 m
    # from a highway=trailhead node -> kept and flagged.
    data = {"elements": [
        _trailhead(9, 33.7300, -118.3600),
        _park_node(1, 33.73005, -118.36005, name="TH Lot"),  # far from trail, near TH
        _park_node(2, 33.7200, -118.3500, name="Random"),    # far from both -> drop
    ]}
    lots = ap.parking_for_geom(GEOM, data)
    assert _names(lots) == {"TH Lot"}, _names(lots)
    assert lots[0].get("trailhead") is True


def test_lot_self_tagged_trailhead_is_flagged():
    data = {"elements": [
        _park_node(1, 33.74005, -118.37298, name="Combo", highway="trailhead"),
    ]}
    lots = ap.parking_for_geom(GEOM, data)
    assert lots[0].get("trailhead") is True


def test_strip_internal_removes_dist_keeps_trailhead():
    data = {"elements": [_park_node(1, 33.74005, -118.37298, name="Lot", highway="trailhead")]}
    lots = ap.parking_for_geom(GEOM, data)
    clean = ap._strip_internal(lots)
    assert "_dist_m" not in clean[0]
    assert clean[0].get("trailhead") is True
    assert clean[0]["name"] == "Lot"


def test_dedup_handles_none_distance():
    # Two co-located lots kept via trailhead corroboration with no trail
    # nearby -> both _dist_m None. dedup must merge without crashing.
    lots = [
        {"lat": 33.74, "lon": -118.37, "trailhead": True, "_dist_m": None},
        {"lat": 33.740001, "lon": -118.370001, "trailhead": True, "_dist_m": None, "name": "TH"},
    ]
    kept = ap.dedup(lots, ap.PARKING_DEDUP_M)
    assert len(kept) == 1
    assert kept[0]["_dist_m"] is None
    assert kept[0].get("name") == "TH"


def test_no_trails_no_parking():
    data = {"elements": [_park_node(1, 33.74005, -118.37298, name="Lot")]}
    assert ap.parking_for_geom({"trails": []}, data) == []


def test_point_in_rings():
    # Unit square in (lon, lat); point_in_rings takes (lat, lon).
    ring = [(-1, -1), (1, -1), (1, 1), (-1, 1), (-1, -1)]
    assert ap.point_in_rings(0, 0, [ring])         # centre
    assert not ap.point_in_rings(2, 2, [ring])     # outside
    assert not ap.point_in_rings(0, 5, [ring])     # outside on one axis


def test_containment_gate_drops_across_boundary_lot():
    # A small ring around vertex A only. Both lots are near the trail (A and
    # B), but the B-area lot is OUTSIDE the ring -> dropped by containment,
    # exactly like a lot across the park fence.
    rings = [[(-118.3732, 33.7399), (-118.3728, 33.7399),
              (-118.3728, 33.7402), (-118.3732, 33.7402), (-118.3732, 33.7399)]]
    data = {"elements": [
        _park_node(1, 33.74005, -118.37298, name="Inside"),   # near A, in ring
        _park_node(2, 33.7406, -118.3724, name="Outside"),    # near B, out of ring
    ]}
    lots, ths = ap.parse_parking(data), ap.parse_trailheads(data)
    stats: dict = {}
    kept = ap.parking_for_area(GEOM, lots, ths, rings=rings, stats=stats)
    assert _names(kept) == {"Inside"}, _names(kept)
    assert stats.get("containment_dropped") == 1
    # Without a boundary, both survive (proximity-only fallback).
    kept_none = ap.parking_for_area(GEOM, lots, ths, rings=None)
    assert _names(kept_none) == {"Inside", "Outside"}, _names(kept_none)


def test_query_includes_both_layers_and_bbox_order():
    q = ap.overpass_query([-118.38, 33.73, -118.36, 33.75])
    assert '"amenity"="parking"' in q
    assert '"highway"="trailhead"' in q
    assert "(33.73,-118.38,33.75,-118.36)" in q   # S,W,N,E


def test_state_query_scopes_to_iso_area():
    q = ap.overpass_state_query("az")
    assert 'area["ISO3166-2"="US-AZ"]->.s' in q
    assert '"amenity"="parking"' in q and '"highway"="trailhead"' in q
    assert "(area.s)" in q


def test_bbox_prefilter_and_shared_lots_not_mutated():
    # State-wide lots shared across areas: one inside the area near the trail,
    # one far outside the bbox. parking_for_area must keep the near one, drop
    # the far one (bbox pre-filter), and NOT mutate the shared input.
    geom = {"trails": GEOM["trails"], "bbox": [-118.3735, 33.7398, -118.3720, 33.7407]}
    shared = [
        {"lat": 33.74005, "lon": -118.37298, "name": "In", "_self_th": False},
        {"lat": 34.50, "lon": -117.00, "name": "FarAway", "_self_th": False},
    ]
    snapshot = [dict(l) for l in shared]
    kept = ap.parking_for_area(geom, shared, [])
    assert _names(kept) == {"In"}, _names(kept)
    assert shared == snapshot, "shared lots must not be mutated"


def test_fed_name_picks_first_real_name():
    assert ap._fed_name({"LOTNAME": "Main Lot"}) == "Main Lot"
    assert ap._fed_name({"NAME": "None", "RECAREANAME": "Kanab Creek TH"}) == "Kanab Creek TH"
    assert ap._fed_name({"NAME": "  ", "foo": "bar"}) is None


def test_road_gate_drops_roadless_points():
    # Two federal points; a road node sits ~50 m from the first, none near the
    # second (Kanab-style interior marker). Only the road-adjacent one survives.
    near = {"lat": 32.0, "lon": -110.0, "source": "blm", "trailhead": True}
    far = {"lat": 33.0, "lon": -111.0, "source": "blm", "trailhead": True}
    road_nodes = [(32.0 + 50.0 / 111_000.0, -110.0)]     # ~50 m north of `near`
    kept = ap._road_gate_filter([near, far], road_nodes, ap._ROAD_GATE_MAX_M)
    assert kept == [near], kept


def test_assign_federal_fills_only_blank_areas():
    # Ring A (a blank area) around (0,0)-(1,1); ring B (a non-blank area) around
    # (10,10)-(11,11). A federal point inside A -> A; inside B -> dropped
    # (OSM covers B); an orphan just outside A -> nearest blank area A by edge.
    ringA = [[(0, 0), (0, 1), (1, 1), (1, 0), (0, 0)]]        # (lon,lat)
    ringB = [[(10, 10), (10, 11), (11, 11), (11, 10), (10, 10)]]
    rings_by_area = {"blankA": ringA, "osmB": ringB}
    blank_ids = {"blankA"}
    # ~1 m outside A's east edge at lat 0.5 (edge buffer is 250 m).
    orphan_lon = 1.0 + 1.0 / 111_000.0
    fed = [
        {"lat": 0.5, "lon": 0.5, "source": "blm", "trailhead": True},   # inside A
        {"lat": 10.5, "lon": 10.5, "source": "nps"},                    # inside B
        {"lat": 0.5, "lon": orphan_lon, "source": "usfs", "trailhead": True},  # orphan near A
    ]
    out = ap.assign_federal(fed, rings_by_area, blank_ids)
    assert set(out) == {"blankA"}, out                 # osmB never filled
    got = {(l["source"]) for l in out["blankA"]}
    assert got == {"blm", "usfs"}, got                 # inside + orphan-edge, not nps


def test_assign_federal_wilderness_nested_in_forest_still_fills():
    # REGRESSION (2026-07-19): a wilderness trailhead sits just OUTSIDE the
    # wilderness polygon, on the road — but that road is still INSIDE the
    # surrounding national forest. assign_federal used to test "contained by a
    # non-blank area" BEFORE the edge-buffer check, so the point was dropped as
    # "OSM covers it" and the wilderness stayed blank forever. That left 55 of
    # Arizona's 58 blank areas empty (Chiricahua, Mazatzal, Galiuro, ... all
    # nested in Coronado/Tonto/Prescott NF).
    forest = [[(0, 0), (0, 10), (10, 10), (10, 0), (0, 0)]]      # non-blank
    wild = [[(4, 4), (4, 5), (5, 5), (5, 4), (4, 4)]]            # blank, nested
    rings_by_area = {"osmForest": forest, "blankWild": wild}
    # ~1 m east of the wilderness edge: outside `wild`, inside `forest`.
    lon = 5.0 + 1.0 / 111_000.0
    fed = [{"lat": 4.5, "lon": lon, "source": "usfs", "trailhead": True}]
    out = ap.assign_federal(fed, rings_by_area, {"blankWild"})
    assert set(out) == {"blankWild"}, out
    assert len(out["blankWild"]) == 1, out


def test_assign_federal_non_blank_containment_still_wins_without_blank_neighbor():
    # The other half of the same fix: with no blank area in range, a point
    # inside a non-blank area is STILL dropped (OSM stays authoritative). This
    # is the anti-bleed guarantee #423 established — the reorder must not
    # weaken it.
    forest = [[(0, 0), (0, 10), (10, 10), (10, 0), (0, 0)]]
    far_wild = [[(90, 40), (90, 41), (91, 41), (91, 40), (90, 40)]]
    fed = [{"lat": 5.0, "lon": 5.0, "source": "usfs", "trailhead": True}]
    out = ap.assign_federal(fed, {"osmForest": forest, "blankFar": far_wild},
                            {"blankFar"})
    assert out == {}, out


def test_assign_federal_orphan_beyond_buffer_dropped():
    ringA = [[(0, 0), (0, 1), (1, 1), (1, 0), (0, 0)]]
    # 500 m east of A's edge — beyond the 250 m edge buffer.
    far_lon = 1.0 + 500.0 / 111_000.0
    fed = [{"lat": 0.5, "lon": far_lon, "source": "blm", "trailhead": True}]
    out = ap.assign_federal(fed, {"blankA": ringA}, {"blankA"})
    assert out == {}, out


def test_boundary_fetch_failure_reports_not_ok(tmp_dir=None):
    # A transient Overpass failure must surface ok=False so process() refuses
    # to WRITE proximity-only (bleed-carrying) results — found 2026-07-18 when
    # a 504 silently degraded AZ to proximity-only and Thunderbird went 14->26.
    import json as _json
    import tempfile
    from pathlib import Path as _P
    with tempfile.TemporaryDirectory() as td:
        f = _P(td) / "some-area-xx.json"
        f.write_text(_json.dumps({"osm_relation_id": 12345}))
        orig = ap.fetch_state_boundaries
        orig_backoff = ap.RETRY_BACKOFFS_SECONDS
        try:
            # Neutralize the retry backoff so the always-fail path doesn't
            # actually sleep through the 30/90/300 s ladder in the test.
            ap.RETRY_BACKOFFS_SECONDS = []

            def _boom(rel_ids):
                raise RuntimeError("504")
            ap.fetch_state_boundaries = _boom
            rings, n, ok = ap._state_boundaries([f])
            assert rings == {} and n == 0 and ok is False, (rings, n, ok)
        finally:
            ap.fetch_state_boundaries = orig
            ap.RETRY_BACKOFFS_SECONDS = orig_backoff
        # No relation ids at all -> nothing to fetch -> ok=True (not a failure).
        f.write_text(_json.dumps({}))
        rings, n, ok = ap._state_boundaries([f])
        assert rings == {} and n == 0 and ok is True, (rings, n, ok)


def test_road_gate_reports_whether_it_could_run():
    # A road-gate OUTAGE drops every federal point, which looks exactly like
    # "none of these points had a road nearby". The caller has to be able to
    # tell them apart, or a flaky Overpass deletes pins that already shipped.
    orig_fetch = ap.fetch_roads_near
    try:
        ap.fetch_roads_near = lambda pts: [(0.0, 0.0)]
        kept, ok = ap.road_gate([{"lat": 0.0, "lon": 0.0, "source": "usfs"}])
        assert ok is True and len(kept) == 1, (kept, ok)

        # Every point genuinely roadless -> gate RAN, so ok stays True.
        ap.fetch_roads_near = lambda pts: [(40.0, -100.0)]
        kept, ok = ap.road_gate([{"lat": 0.0, "lon": 0.0, "source": "usfs"}])
        assert ok is True and kept == [], (kept, ok)

        # Fetch blew up -> nothing verified, ok False.
        def _boom(pts):
            raise RuntimeError("504")
        ap.fetch_roads_near = _boom
        kept, ok = ap.road_gate([{"lat": 0.0, "lon": 0.0, "source": "usfs"}])
        assert ok is False and kept == [], (kept, ok)
    finally:
        ap.fetch_roads_near = orig_fetch
    # Nothing to gate is not a failure.
    assert ap.road_gate([]) == ([], True)


def test_features_or_raise_separates_an_empty_answer_from_a_failed_one():
    # REGRESSION (2026-07-27): ArcGIS answers a throttled/rejected query with
    # HTTP 200 and {"error": {...}} — no "features" key. The old
    # `data.get("features") or []` read that as "0 features in bbox", so the
    # caller cleared every pin the layer had contributed. That is how the
    # national roll deleted Kootenai National Forest's 50 USFS trailheads while
    # logging a clean "federal usfs: 0 features in bbox" and exiting 0.
    # A genuine empty result IS a FeatureCollection with "features": [].
    assert ap._features_or_raise({"type": "FeatureCollection", "features": []}) == []
    assert ap._features_or_raise({"features": [{"a": 1}]}) == [{"a": 1}]

    for bad in ({"error": {"code": 429, "message": "Too many requests"}},
                {"error": {"code": 400, "message": "Invalid where clause"}},
                {"error": "boom"},
                {"type": "FeatureCollection"},          # unexpected shape
                {}):
        try:
            ap._features_or_raise(bad)
        except ap.ArcGISUnavailable:
            pass
        else:
            raise AssertionError(f"should have raised for {bad}")


def test_arcgis_transient_retries_throttles_but_not_rejected_queries():
    # Same lesson as the missing-shapely ImportError: do not burn the 30/90/300
    # backoff ladder on a deterministic failure and then blame the remote host.
    assert ap._arcgis_transient(ap.ArcGISUnavailable("ArcGIS error 429: slow down"))
    assert ap._arcgis_transient(ap.ArcGISUnavailable("ArcGIS error 503: busy"))
    assert ap._arcgis_transient(ap.ArcGISUnavailable("response has no 'features' key"))
    assert ap._arcgis_transient(TimeoutError("timed out"))
    # A rejected query is not worth retrying — it answers the same every time.
    assert not ap._arcgis_transient(
        ap.ArcGISUnavailable("ArcGIS error 400: Invalid where clause"))


def test_classify_write_treats_missing_key_and_empty_list_as_the_same_blank():
    # REGRESSION (2026-07-26): an area with no `parking` key reads back as None
    # and an area with no qualifying lots computes as []. `None != []`, so every
    # area that was blank before and blank after was counted as "cleared" and
    # reported as having LOST parking. The AZ/NM dry run claimed "would clear
    # 55" / "would clear 45" when the real answer was 0 — those were exactly the
    # areas that stayed blank (59-4 and 51-6). Nationally that is ~2,000 phantom
    # losses, which buries a real regression.
    assert ap._classify_write(None, []) is None
    assert ap._classify_write([], []) is None

    lot = {"lat": 1.0, "lon": 2.0}
    other = {"lat": 3.0, "lon": 4.0}
    # Blank -> has lots is an ADD, from either representation of blank.
    assert ap._classify_write(None, [lot]) == "added"
    assert ap._classify_write([], [lot]) == "added"
    # Same lots either way round is not a change.
    assert ap._classify_write([lot], [lot]) is None
    # Different lots is an update, not an add.
    assert ap._classify_write([lot], [other]) == "updated"
    # A GENUINE clear still reports as cleared — that signal must survive.
    assert ap._classify_write([lot], []) == "cleared"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
