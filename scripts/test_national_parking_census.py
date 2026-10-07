#!/usr/bin/env python3
"""Focused synthetic tests for the read-only national parking census."""
from __future__ import annotations

import builtins
import copy
import hashlib
import importlib.util
import json
import math
import os
import random
import shutil
import signal
import sys
import time
import weakref
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
TOOLS = HERE / "parking-adjud" / "tools"
for path in (str(HERE), str(TOOLS)):
    if path not in sys.path:
        sys.path.insert(0, path)

import _parking_verdicts as pv  # noqa: E402
from _seed_constants import STATE_NAMES, US_JURISDICTION_CODES  # noqa: E402

SPEC = importlib.util.spec_from_file_location(
    "national_parking_census_tests", TOOLS / "national_census.py"
)
assert SPEC is not None and SPEC.loader is not None
census = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = census
SPEC.loader.exec_module(census)
sys.modules.setdefault("national_census", census)

SEAL_SPEC = importlib.util.spec_from_file_location(
    "seal_production_baseline_tests", TOOLS / "seal_production_baseline.py"
)
assert SEAL_SPEC is not None and SEAL_SPEC.loader is not None
sealer = importlib.util.module_from_spec(SEAL_SPEC)
SEAL_SPEC.loader.exec_module(sealer)

PRODUCTION_BASELINE = HERE / "parking-adjud" / "production-baseline-v1.json"
APPROVED_BASELINES = (
    HERE / "parking-adjud" / "approved-production-baselines-v1.json"
)
MALFORMED_RELATIONS_FIXTURE = (
    HERE / "parking-adjud" / "test-fixtures"
    / "malformed-relations-seq2401-6b5ac0402.geojsonseq"
)
MALFORMED_RELATIONS_FIXTURE_SHA256 = (
    "ac1d36c8cd40074edf262fad812e163ccd3c419de854122d87a46738f6ae4a15"
)
REAL_NEAR_ZERO_RELATION_CASES = (
    (
        "a14191209", "relation/7095604", (0, 2),
        -2.000014271810943e-13,
        census.Endpoint(40.90059875506795, -73.11006574999999), 8_217.779,
    ),
    (
        "a32909295", "relation/16454647", (0, 0),
        3.9999978197516556e-14,
        census.Endpoint(30.392075117349737, -97.76307924999998), 8_371.861,
    ),
)


def _offset(latitude=0.0, longitude=0.0, north_m=0.0, east_m=0.0):
    return (
        latitude + north_m / 111_320.0,
        longitude + east_m / (
            111_320.0 * max(1e-12, math.cos(math.radians(latitude)))
        ),
    )


def _point_feature(alias: str, *, north_m=0.0, east_m=0.0,
                   latitude=0.0, longitude=0.0, tags=None, endpoints=(0,)):
    latitude, longitude = _offset(
        latitude, longitude, north_m=north_m, east_m=east_m
    )
    properties = {"amenity": "parking"}
    properties.update(tags or {})
    return census.ParkingFeature(
        alias=alias,
        source_form=alias,
        geometry={"type": "Point", "coordinates": [longitude, latitude]},
        latitude=latitude,
        longitude=longitude,
        tags=properties,
        near_endpoint_ids=tuple(endpoints),
    )


def _rectangle(alias: str, *, west_m: float, east_m: float,
               south_m=-10.0, north_m=10.0, latitude=0.0,
               longitude=0.0, tags=None, endpoints=(0,), members=()):
    southwest = _offset(latitude, longitude, north_m=south_m, east_m=west_m)
    southeast = _offset(latitude, longitude, north_m=south_m, east_m=east_m)
    northeast = _offset(latitude, longitude, north_m=north_m, east_m=east_m)
    northwest = _offset(latitude, longitude, north_m=north_m, east_m=west_m)
    ring = [
        [southwest[1], southwest[0]],
        [southeast[1], southeast[0]],
        [northeast[1], northeast[0]],
        [northwest[1], northwest[0]],
        [southwest[1], southwest[0]],
    ]
    geometry = {"type": "Polygon", "coordinates": [ring]}
    representative = census.geometry_representative(geometry)
    properties = {"amenity": "parking"}
    properties.update(tags or {})
    return census.ParkingFeature(
        alias=alias,
        source_form=alias,
        geometry=geometry,
        latitude=representative[0],
        longitude=representative[1],
        tags=properties,
        near_endpoint_ids=tuple(endpoints),
        relation_members=tuple(members),
    )


def _empty_verdicts():
    return pv.Verdicts({"version": 1, "lots": {}})


def _component(*members, verdict=None):
    aliases = tuple(sorted(member.alias for member in members))
    ordered = tuple(sorted(members, key=lambda member: member.alias))
    value = census.Component(
        census.candidate_id_for_aliases(aliases),
        aliases,
        ordered,
        tuple(sorted({
            endpoint_id for member in members
            for endpoint_id in member.near_endpoint_ids
        })),
    )
    value.verdict_entry = verdict
    return value


def _minimal_verdict_entry(alias: str, verdict: str, *, aliases=None,
                           latitude=0.0, longitude=0.0, rings=None):
    return {
        "verdict": verdict,
        "reason": "not-public" if verdict == "DROP" else None,
        "lat": latitude,
        "lon": longitude,
        "rings": rings or [],
        "osm": aliases or [alias],
        "name": "Synthetic Lot",
        "area": "fixture-co",
        "prior": "surveyed",
        "confidence": "strong",
        "evidence": {"serves": "synthetic evidence"},
        "judged": "2026-10-05",
        "src": "synthetic-test",
    }


def _strict_verdict_document(*entries):
    return {
        "version": 1,
        "lots": {entry["osm"][0]: entry for entry in entries},
    }


def test_40m_proximity_is_transitive_and_input_order_independent_but_not_identity():
    features = (
        _point_feature("node/1", east_m=0),
        _point_feature("node/2", east_m=30),
        _point_feature("node/3", east_m=60),
    )
    first, first_edges = census.build_components(features, _empty_verdicts())
    second, second_edges = census.build_components(
        tuple(reversed(features)), _empty_verdicts()
    )
    assert len(first) == len(second) == 3
    assert first_edges == second_edges == {}
    assert [component.candidate_id for component in first] == [
        component.candidate_id for component in second
    ]

    first_groups, first_count = census.proximity_review_groups(first)
    second_groups, second_count = census.proximity_review_groups(second)
    assert first_count == second_count == 2
    assert first_groups == second_groups
    groups = {value["group_id"] for value in first_groups.values()}
    assert len(groups) == 1
    assert len(next(iter(first_groups.values()))["candidate_ids"]) == 3


def test_canonical_must_link_edges_cover_sidecar_relation_overlap_and_safe_point_polygon():
    sidecar = pv.Verdicts({
        "version": 1,
        "lots": {
            "node/1": _minimal_verdict_entry(
                "node/1", "KEEP", aliases=["node/1", "node/2"]
            ),
        },
    })
    sidecar_components, counts = census.build_components((
        _point_feature("node/1", east_m=0),
        _point_feature("node/2", east_m=500),
    ), sidecar)
    assert len(sidecar_components) == 1
    assert counts == {"verdict_alias_cluster": 1}

    relation = _rectangle(
        "relation/10", west_m=-20, east_m=20, members=("way/11",)
    )
    member = _rectangle("way/11", west_m=-18, east_m=18)
    relation_components, counts = census.build_components(
        (relation, member), _empty_verdicts()
    )
    assert len(relation_components) == 1
    assert counts["parking_relation_member"] == 1

    almost_same = _rectangle("way/21", west_m=-19.9, east_m=19.9)
    overlap_components, counts = census.build_components(
        (_rectangle("way/20", west_m=-20, east_m=20), almost_same),
        _empty_verdicts(),
    )
    assert len(overlap_components) == 1
    assert counts["polygon_overlap"] == 1

    polygon = _rectangle("way/30", west_m=-15, east_m=15)
    point = _point_feature("node/31", east_m=0)
    point_components, counts = census.build_components(
        (polygon, point), _empty_verdicts()
    )
    assert len(point_components) == 1
    assert counts["point_inside_polygon"] == 1


def test_point_polygon_tag_conflict_blocks_must_link():
    polygon = _rectangle(
        "way/1", west_m=-15, east_m=15, tags={"name": "North Lot"}
    )
    point = _point_feature("node/2", tags={"name": "South Lot"})
    components, counts = census.build_components(
        (polygon, point), _empty_verdicts()
    )
    assert len(components) == 2
    assert "point_inside_polygon" not in counts


def test_new_identity_edge_cannot_bridge_distinct_sidecar_clusters():
    polygon = _rectangle("way/1", west_m=-15, east_m=15)
    point = _point_feature("node/2")
    verdicts = pv.Verdicts({
        "version": 1,
        "lots": {
            "way/1": _minimal_verdict_entry("way/1", "KEEP"),
            "node/2": _minimal_verdict_entry("node/2", "DROP"),
        },
    })
    with pytest.raises(census.CensusError, match="distinct verdict clusters"):
        census.build_components((polygon, point), verdicts)


def test_three_near_candidates_and_current_filter_replacement():
    endpoint = census.Endpoint(0.0, 0.0)
    trail = census.Trail("fixture-co", "CO", "trail", "Trail", (0,))
    components = tuple(_component(feature) for feature in (
        _point_feature("node/1", east_m=100, tags={"access": "private"}),
        _point_feature("node/2", east_m=200),
        _point_feature("node/3", east_m=300),
        _point_feature("node/4", east_m=400),
    ))
    selected, counts = census.service_projections((trail,), (endpoint,), components)
    assert len(selected) == 4
    by_alias = {component.aliases[0]: component for component in components}
    assert by_alias["node/1"].bindings[0]["projections"] == ["raw"]
    assert by_alias["node/2"].bindings[0]["projections"] == [
        "raw", "current", "effective"
    ]
    assert by_alias["node/4"].bindings[0]["projections"] == [
        "current", "effective"
    ]
    assert counts["raw"]["selected_candidates"] == 3
    assert counts["current"]["selected_candidates"] == 3
    assert counts["effective"]["selected_candidates"] == 3
    assert all(counts[projection]["near_trails"] == 1 for projection in census.PROJECTIONS)


def test_two_fallback_candidates_recomputed_for_current_and_effective():
    endpoint = census.Endpoint(0.0, 0.0)
    trail = census.Trail("fixture-co", "CO", "trail", "Trail", (0,))
    drop = _minimal_verdict_entry("node/2", "DROP")
    components = (
        _component(_point_feature("node/1", east_m=1_000,
                                  tags={"parking": "shoulder"})),
        _component(_point_feature("node/2", east_m=2_000), verdict=drop),
        _component(_point_feature("node/3", east_m=3_000)),
        _component(_point_feature("node/4", east_m=4_000)),
    )
    selected, counts = census.service_projections((trail,), (endpoint,), components)
    assert len(selected) == 4
    by_alias = {component.aliases[0]: component for component in components}
    assert by_alias["node/1"].bindings[0]["projections"] == ["raw"]
    assert by_alias["node/2"].bindings[0]["projections"] == ["raw", "current"]
    assert by_alias["node/3"].bindings[0]["projections"] == ["current", "effective"]
    assert by_alias["node/4"].bindings[0]["projections"] == ["effective"]
    assert all(counts[projection]["fallback_trails"] == 1
               for projection in census.PROJECTIONS)
    assert all(counts[projection]["selected_candidates"] == 2
               for projection in census.PROJECTIONS)


def test_polygon_edge_not_centroid_binds_service():
    endpoint = census.Endpoint(0.0, 0.0)
    trail = census.Trail("fixture-co", "CO", "trail", "Trail", (0,))
    polygon = _rectangle("way/1", west_m=100, east_m=3_000)
    assert census.haversine_m(
        0.0, 0.0, polygon.latitude, polygon.longitude
    ) > census.NEAR_M
    component = _component(polygon)
    selected, counts = census.service_projections(
        (trail,), (endpoint,), (component,)
    )
    assert component.candidate_id in selected
    assert component.bindings[0]["distance_m"] == pytest.approx(100, abs=0.5)
    assert component.bindings[0]["fallback"] is False
    assert counts["effective"]["near_trails"] == 1


def test_hard_and_mixed_access_remain_visible_with_review_routing():
    hard = _component(_point_feature(
        "node/1", tags={"access": "private"}
    ))
    hard_access = census._access_summary(hard)
    assert hard_access["classification"] == "hard_non_public"
    assert census._routing(
        "osm", census._verdict_projection(None), hard_access
    )["bucket"] == "unmatched"

    mixed = _component(
        _point_feature("node/2", tags={"access": "private"}),
        _point_feature("node/3", east_m=5, tags={"access": "yes"}),
    )
    mixed_access = census._access_summary(mixed)
    assert mixed_access["classification"] == "mixed_or_ambiguous"
    assert census._routing(
        "osm", census._verdict_projection(None), mixed_access
    )["bucket"] == "review_hold"


def test_live_union_attaches_unique_match_and_retains_unmatched_or_ambiguous_synthetic():
    component = _component(_point_feature("node/1"))
    pins = (
        {"live_id": "live-near", "lat": 0.0, "lon": _offset(east_m=5)[1]},
        {"live_id": "live-far", "lat": 10.0, "lon": 10.0},
    )
    synthetic = census.attach_live_pins(
        (component,), pins, _empty_verdicts()
    )
    assert [pin["live_id"] for pin in component.live_pins] == ["live-near"]
    assert [pin["live_id"] for pin in synthetic] == ["live-far"]

    first = _component(_point_feature("node/2", east_m=-10))
    second = _component(_point_feature("node/3", east_m=10))
    ambiguous = census.attach_live_pins(
        (first, second), ({"live_id": "live-mid", "lat": 0.0, "lon": 0.0},),
        _empty_verdicts(),
    )
    assert ambiguous[0]["component_match"] == "ambiguous"
    assert len(ambiguous[0]["component_candidates"]) == 2
    assert first.live_pins == second.live_pins == []


def test_strict_production_verdict_matching_preserves_review_hold(tmp_path):
    entry = _minimal_verdict_entry("node/7", "REVIEW")
    path = tmp_path / "verdicts.json"
    path.write_text(json.dumps(_strict_verdict_document(entry)))
    captured = census._load_verdicts(path)
    component = _component(_point_feature("node/7"))
    census.match_component_verdicts((component,), captured.verdicts)
    projection = census._verdict_projection(component.verdict_entry)
    assert projection == {"status": "REVIEW", "effect": "HOLD", "key": "node/7"}
    assert census._routing(
        "osm", projection, census._access_summary(component)
    )["bucket"] == "review_hold"


def test_component_aliases_matching_distinct_verdicts_fail_closed():
    verdicts = pv.Verdicts({
        "version": 1,
        "lots": {
            "node/1": _minimal_verdict_entry("node/1", "KEEP"),
            "node/2": _minimal_verdict_entry("node/2", "DROP"),
        },
    })
    component = _component(
        _point_feature("node/1"), _point_feature("node/2", east_m=10)
    )
    with pytest.raises(census.CensusError, match="conflicting verdicts"):
        census.match_component_verdicts((component,), verdicts)


def test_equal_score_verdict_ambiguity_stays_unmatched_and_tombstoned():
    west = _offset(east_m=-5)
    east = _offset(east_m=5)
    verdicts = pv.Verdicts({
        "version": 1,
        "lots": {
            "node/10": _minimal_verdict_entry(
                "node/10", "KEEP", latitude=west[0], longitude=west[1]
            ),
            "node/11": _minimal_verdict_entry(
                "node/11", "DROP", latitude=east[0], longitude=east[1]
            ),
        },
    })
    component = _component(_point_feature("node/999"))
    ledger = census.match_component_verdicts((component,), verdicts)
    assert component.verdict_entry is None
    assert ledger.assignments == {}
    assert [entry["_key"] for entry in ledger.tombstone_entries] == [
        "node/10", "node/11"
    ]

    synthetic = [{"live_id": "live-mid", "lat": 0.0, "lon": 0.0}]
    ledger = census.reconcile_verdict_ledger((), synthetic, verdicts)
    assert ledger.assignments == {}
    assert "verdict_entry" not in synthetic[0]


def _geojson_feature(alias, geometry, **tags):
    properties = {"amenity": "parking"}
    properties.update(tags)
    return {
        "type": "Feature", "id": alias,
        "properties": properties, "geometry": geometry,
    }


def _real_near_zero_relation_rows():
    raw = MALFORMED_RELATIONS_FIXTURE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == (
        MALFORMED_RELATIONS_FIXTURE_SHA256
    )
    rows = tuple(
        json.loads(line.lstrip(b"\x1e"))
        for line in raw.splitlines()
        if line
    )
    assert tuple(row["id"] for row in rows) == (
        "a14191209", "a32909295",
    )
    return rows


def test_stream_canonicalizes_area_id_prefers_polygon_and_is_order_independent(tmp_path):
    endpoint_grid = census.EndpointGrid((census.Endpoint(0.0, 0.0),))
    line = _geojson_feature(
        "w1", {"type": "LineString", "coordinates": [
            [-0.0001, -0.0001], [0.0001, -0.0001],
            [0.0001, 0.0001], [-0.0001, 0.0001], [-0.0001, -0.0001],
        ]}
    )
    polygon = _geojson_feature(
        "a2", {"type": "Polygon", "coordinates": [[
            [-0.0001, -0.0001], [0.0001, -0.0001],
            [0.0001, 0.0001], [-0.0001, 0.0001], [-0.0001, -0.0001],
        ]]}
    )
    results = []
    for index, rows in enumerate(((line, polygon), (polygon, line))):
        path = tmp_path / f"fixture-{index}.geojsonseq"
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        results.append(census.stream_parking_source(path, "geojsonseq", endpoint_grid))
    for result in results:
        assert len(result.features) == 1
        assert result.features[0].alias == "way/1"
        assert result.features[0].geometry_type == "Polygon"
        assert result.counters["canonical_export_duplicates"] == 1
    assert results[0].features[0].geometry == results[1].features[0].geometry
    assert results[0].counters == results[1].counters


def test_stream_declared_non_area_way_prefers_line_over_area_copy(tmp_path):
    endpoint_grid = census.EndpointGrid((census.Endpoint(0.0, 0.0),))
    line = _geojson_feature(
        "w1", {"type": "LineString", "coordinates": [
            [-0.0001, -0.0001], [0.0001, -0.0001],
            [0.0001, 0.0001], [-0.0001, 0.0001], [-0.0001, -0.0001],
        ]}, area="false",
    )
    area_copy = _geojson_feature(
        "a2", {"type": "MultiPolygon", "coordinates": [[[
            [-0.0001, -0.0001], [0.0001, -0.0001],
            [0.0001, 0.0001], [-0.0001, 0.0001], [-0.0001, -0.0001],
        ]]]}, area="false",
    )
    for index, rows in enumerate(((line, area_copy), (area_copy, line))):
        path = tmp_path / f"non-area-{index}.geojsonseq"
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        result = census.stream_parking_source(
            path, "geojsonseq", endpoint_grid
        )
        assert len(result.features) == 1
        assert result.features[0].alias == "way/1"
        assert result.features[0].geometry_type == "LineString"
        assert result.counters["canonical_export_duplicates"] == 0
        assert result.counters["ignored_non_area_area_copies"] == 1


def test_declared_non_area_copy_adds_zero_endpoint_or_topology_work_in_both_orders(
        tmp_path, monkeypatch):
    endpoint_grid = census.EndpointGrid((census.Endpoint(0.0, 0.0),))
    ring = [
        [-0.06, -0.06], [0.06, -0.06], [0.06, 0.06],
        [-0.06, 0.06], [-0.06, -0.06],
    ]
    line = _geojson_feature(
        "w1", {"type": "LineString", "coordinates": ring}, area="false"
    )
    area_copy = _geojson_feature(
        "a2", {"type": "MultiPolygon", "coordinates": [[[point for point in ring]]]},
        area="false",
    )

    def forbid_topology(_geometry):
        pytest.fail("a declared non-area area copy reached topology validation")

    monkeypatch.setattr(census, "_validate_polygon_topology", forbid_topology)
    line_path = tmp_path / "non-area-line-only.geojsonseq"
    line_path.write_text(json.dumps(line) + "\n")
    baseline = census.stream_parking_source(
        line_path, "geojsonseq", endpoint_grid
    )
    endpoint_counters = (
        "coarse_endpoint_candidates_total",
        "coarse_outside_fallback_envelope",
        "exact_endpoint_distance_checks",
        "endpoint_associations_total",
        "max_coarse_endpoint_candidates",
        "max_endpoint_associations",
    )
    for index, rows in enumerate(((line, area_copy), (area_copy, line))):
        path = tmp_path / f"non-area-envelope-{index}.geojsonseq"
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        result = census.stream_parking_source(
            path, "geojsonseq", endpoint_grid
        )
        assert result.features == ()
        assert result.counters["outside_fallback_envelope"] == 1
        assert result.counters["exact_outside_fallback_envelope"] == 1
        assert result.counters["topology_not_required_outside"] == 0
        assert result.counters["topology_validation_calls"] == 0
        assert result.counters["ignored_non_area_area_copies"] == 1
        assert result.counters["canonical_export_duplicates"] == 0
        assert {
            key: result.counters[key] for key in endpoint_counters
        } == {
            key: baseline.counters[key] for key in endpoint_counters
        }
        assert result.inventory == baseline.inventory
        assert result.inventory[
            "topology_unvalidated_non_candidates"
        ]["records"] == 0


def test_declared_non_area_degenerate_copy_is_excluded_in_both_orders(
        tmp_path):
    ring = [
        [50.0, 50.0], [50.001, 50.0], [50.002, 50.0],
        [50.0, 50.0],
    ]
    line = _geojson_feature(
        "w1", {"type": "LineString", "coordinates": ring}, area="false"
    )
    area_copy = _geojson_feature(
        "a2", {"type": "Polygon", "coordinates": [ring]}, area="false"
    )
    results = []
    for index, rows in enumerate(((line, area_copy), (area_copy, line))):
        path = tmp_path / f"non-area-degenerate-copy-{index}.geojsonseq"
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        result = census.stream_parking_source(
            path,
            "geojsonseq",
            census.EndpointGrid((census.Endpoint(0.0, 0.0),)),
        )
        results.append(result)
        assert result.counters["parking_features"] == 2
        assert result.counters["ignored_non_area_area_copies"] == 1
        for name in (
                "staged_degenerate_polygon_records",
                "staged_degenerate_rings",
                "staged_degenerate_outer_rings",
                "staged_degenerate_inner_rings",
                "staged_degenerate_components",
                "topology_unvalidated_degenerate_records",
                "topology_rejected_degenerate_records"):
            assert result.counters[name] == 0
        assert result.counters["coarse_endpoint_candidates_total"] == 0
        assert result.counters["topology_validation_calls"] == 0
        assert result.counters["rejected_total"] == 0
    assert results[0].features == results[1].features == ()
    assert results[0].inventory == results[1].inventory
    assert results[0].counters == results[1].counters


def test_malformed_feature_records_are_explicitly_accounted(tmp_path):
    endpoint_grid = census.EndpointGrid((census.Endpoint(0.0, 0.0),))
    rows = [
        b"{not-json}\n",
        (json.dumps(_geojson_feature(
            "n1", {"type": "Point", "coordinates": [0.0]}
        )) + "\n").encode(),
        (json.dumps({
            "type": "Feature", "id": "n2", "properties": {"amenity": "school"},
            "geometry": {"type": "Point", "coordinates": [0.0, 0.0]},
        }) + "\n").encode(),
        (json.dumps(_geojson_feature(
            "n3", {"type": "Point", "coordinates": [0.0, 0.0]}
        )) + "\n").encode(),
    ]
    path = tmp_path / "malformed.geojsonseq"
    path.write_bytes(b"".join(rows))
    result = census.stream_parking_source(path, "geojsonseq", endpoint_grid)
    assert len(result.features) == 1
    assert result.counters["rejected_total"] == 2
    assert result.counters["rejections"] == {
        "invalid_json": 1,
        "malformed_geometry": 1,
    }
    assert result.counters["ignored_nonparking"] == 1


def test_existing_destination_and_invalid_shard_sizes_are_refused(tmp_path):
    destination = tmp_path / "existing"
    destination.mkdir()
    (destination / "operator-note.txt").write_text("preserve")
    called = False

    def verifier():
        nonlocal called
        called = True

    with pytest.raises(census.CensusError, match="already exists"):
        census.write_outputs(
            destination, [], {"counts": {}}, 500, verifier
        )
    assert (destination / "operator-note.txt").read_text() == "preserve"
    assert called is False
    with pytest.raises(census.CensusError, match="shard size"):
        census.write_outputs(
            tmp_path / "new", [], {"counts": {}}, 499, verifier
        )


def test_dateline_and_high_latitude_endpoint_search_is_geodesic():
    endpoint = census.Endpoint(85.0, 179.99)
    grid = census.EndpointGrid((endpoint,))
    point = {"type": "Point", "coordinates": [-179.99, 85.0]}
    distance = census.geometry_distance_to_point_m(point, 85.0, 179.99)
    assert 150 < distance < 250
    assert grid.within_geometry(point, 5_000) == (0,)

    ring = [[179.98, 84.99], [-179.98, 84.99], [-179.98, 85.01],
            [179.98, 85.01], [179.98, 84.99]]
    polygon = {"type": "Polygon", "coordinates": [ring]}
    assert census.point_in_geometry(85.0, 180.0, polygon)
    assert census.geometry_distance_to_point_m(polygon, 85.0, 180.0) == 0


def _independent_wrap_longitude(longitude):
    return (longitude + 180.0) % 360.0 - 180.0


def _independent_unit_sphere_point(latitude, longitude):
    latitude_radians = math.radians(latitude)
    longitude_radians = math.radians(longitude)
    cosine = math.cos(latitude_radians)
    return (
        cosine * math.cos(longitude_radians),
        cosine * math.sin(longitude_radians),
        math.sin(latitude_radians),
    )


def _independent_angular_distance(first, second):
    first_vector = _independent_unit_sphere_point(*first)
    second_vector = _independent_unit_sphere_point(*second)
    dot = sum(a * b for a, b in zip(first_vector, second_vector))
    return math.acos(max(-1.0, min(1.0, dot)))


def _great_circle_point(first, second, fraction):
    first_vector = _independent_unit_sphere_point(*first)
    second_vector = _independent_unit_sphere_point(*second)
    angle = _independent_angular_distance(first, second)
    if angle <= 1e-15:
        return first
    first_weight = math.sin((1.0 - fraction) * angle) / math.sin(angle)
    second_weight = math.sin(fraction * angle) / math.sin(angle)
    x = first_weight * first_vector[0] + second_weight * second_vector[0]
    y = first_weight * first_vector[1] + second_weight * second_vector[1]
    z = first_weight * first_vector[2] + second_weight * second_vector[2]
    return (
        math.degrees(math.atan2(z, math.hypot(x, y))),
        math.degrees(math.atan2(y, x)),
    )


def _independent_initial_bearing(first, second):
    first_latitude = math.radians(first[0])
    second_latitude = math.radians(second[0])
    delta_longitude = math.radians(
        _independent_wrap_longitude(second[1] - first[1])
    )
    return math.atan2(
        math.sin(delta_longitude) * math.cos(second_latitude),
        math.cos(first_latitude) * math.sin(second_latitude)
        - math.sin(first_latitude) * math.cos(second_latitude)
        * math.cos(delta_longitude),
    )


def _independent_destination(latitude, longitude, bearing, distance_m):
    angular_distance = distance_m / 6_371_000.0
    latitude_radians = math.radians(latitude)
    longitude_radians = math.radians(longitude)
    destination_latitude = math.asin(
        math.sin(latitude_radians) * math.cos(angular_distance)
        + math.cos(latitude_radians) * math.sin(angular_distance)
        * math.cos(bearing)
    )
    destination_longitude = longitude_radians + math.atan2(
        math.sin(bearing) * math.sin(angular_distance)
        * math.cos(latitude_radians),
        math.cos(angular_distance)
        - math.sin(latitude_radians) * math.sin(destination_latitude),
    )
    return (
        math.degrees(destination_latitude),
        _independent_wrap_longitude(math.degrees(destination_longitude)),
    )


def _independent_segment_offset(
        first, second, fraction, distance_m, side=1.0):
    target = _great_circle_point(first, second, fraction)
    forward = _independent_initial_bearing(target, second)
    return _independent_destination(
        target[0], target[1], forward + side * math.pi / 2.0, distance_m
    )


def _independent_lines(geometry):
    if geometry["type"] == "LineString":
        return (geometry["coordinates"],)
    if geometry["type"] == "Polygon":
        return tuple(geometry["coordinates"])
    return tuple(
        ring
        for polygon in geometry["coordinates"]
        for ring in polygon
    )


@pytest.mark.parametrize("geometry, endpoint", [
    (
        {"type": "LineString", "coordinates": [[-60, 60], [60, 60]]},
        census.Endpoint(73.897886248, 0.0),
    ),
    (
        {"type": "Polygon", "coordinates": [[
            [-60, 60], [60, 60], [0, 50], [-60, 60],
        ]]},
        census.Endpoint(73.897886248, 0.0),
    ),
    (
        {"type": "LineString", "coordinates": [
            [0, 0], [120, 0], [-120, 0], [0, 0],
        ]},
        census.Endpoint(0.0, -60.0),
    ),
    (
        {"type": "Polygon", "coordinates": [[
            [0, 0], [120, 10], [-120, 0], [0, 0],
        ]]},
        census.Endpoint(0.0, -60.0),
    ),
], ids=(
    "northern-great-circle-line",
    "northern-great-circle-polygon-boundary",
    "longitude-union-line",
    "longitude-union-polygon-boundary",
))
def test_confirmed_geodesic_envelope_misses_are_coarse_and_exact_matches(
        geometry, endpoint):
    if geometry["type"] == "Polygon":
        census._validate_polygon_topology(geometry)
    grid = census.EndpointGrid((endpoint,))
    assert census.geometry_distance_to_point_m(
        geometry, endpoint.latitude, endpoint.longitude
    ) < 0.001
    assert grid.candidate_ids(geometry, census.FALLBACK_M) == {0}
    assert grid.within_geometry(geometry, census.FALLBACK_M) == (0,)

    accumulator = census.FeatureAccumulator(grid, {})
    accumulator.consume_line((json.dumps(
        _geojson_feature("w9001", geometry)
    ) + "\n").encode())
    counters = accumulator.final_counters()
    assert counters["coarse_endpoint_candidates_total"] == 1
    assert counters["exact_endpoint_distance_checks"] == 1
    assert counters["topology_not_required_outside"] == 0
    assert counters["topology_validation_calls"] == (
        1 if geometry["type"] == "Polygon" else 0
    )
    assert counters["retained_canonical_features"] == 1


def test_polygon_interior_unwrap_has_no_antipodal_aliases():
    zero_polygon = {"type": "Polygon", "coordinates": [[
        [-1, -1], [1, -1], [1, 1], [-1, 1], [-1, -1],
    ]]}
    dateline_polygon = {"type": "Polygon", "coordinates": [[
        [179, -1], [-179, -1], [-179, 1], [179, 1], [179, -1],
    ]]}
    holed_polygon = {"type": "Polygon", "coordinates": [
        [[-2, -2], [2, -2], [2, 2], [-2, 2], [-2, -2]],
        [[-1, -1], [-1, 1], [1, 1], [1, -1], [-1, -1]],
    ]}
    assert census.point_in_geometry(0.0, 0.0, zero_polygon)
    assert not census.point_in_geometry(0.0, 180.0, zero_polygon)
    assert not census.point_in_geometry(0.0, -180.0, zero_polygon)
    zero_grid = census.EndpointGrid((
        census.Endpoint(0.0, 0.0),
        census.Endpoint(0.0, 180.0),
    ))
    assert zero_grid.candidate_ids(zero_polygon, census.FALLBACK_M) == {0}
    assert zero_grid.within_geometry(zero_polygon, census.FALLBACK_M) == (0,)
    assert census.point_in_geometry(0.0, 180.0, dateline_polygon)
    assert census.point_in_geometry(0.0, -180.0, dateline_polygon)
    assert not census.point_in_geometry(0.0, 0.0, dateline_polygon)
    assert not census.point_in_geometry(0.0, 0.0, holed_polygon)
    assert census.point_in_geometry(0.0, 1.5, holed_polygon)
    assert not census.point_in_geometry(0.0, 180.0, holed_polygon)
    assert not census.point_in_geometry(0.0, -180.0, holed_polygon)

    zero_multi = {
        "type": "MultiPolygon",
        "coordinates": [
            zero_polygon["coordinates"],
            [[[19, -1], [21, -1], [21, 1], [19, 1], [19, -1]]],
        ],
    }
    dateline_multi = {
        "type": "MultiPolygon",
        "coordinates": [
            dateline_polygon["coordinates"],
            [[[19, -1], [21, -1], [21, 1], [19, 1], [19, -1]]],
        ],
    }
    assert census.point_in_geometry(0.0, 0.0, zero_multi)
    assert not census.point_in_geometry(0.0, 180.0, zero_multi)
    assert not census.point_in_geometry(0.0, -180.0, zero_multi)
    assert not census.point_in_geometry(0.0, 0.0, dateline_multi)
    assert census.point_in_geometry(0.0, 180.0, dateline_multi)
    assert census.point_in_geometry(0.0, -180.0, dateline_multi)


@pytest.mark.parametrize("first, second", [
    ((60.0, -60.0), (60.0, 60.0)),
    ((-60.0, -60.0), (-60.0, 60.0)),
    ((10.0, 179.8), (10.0, -179.8)),
    ((89.8, -60.0), (89.8, 60.0)),
    ((-30.0, -150.0), (-30.0, -130.0)),
], ids=(
    "north-bulge", "south-bulge", "dateline-wrap", "near-pole",
    "negative-coordinates",
))
def test_spherical_segment_extrema_and_wrapped_longitudes_are_conservative(
        first, second):
    endpoint = census.Endpoint(*_great_circle_point(first, second, 0.5))
    geometry = {
        "type": "LineString",
        "coordinates": [[first[1], first[0]], [second[1], second[0]]],
    }
    grid = census.EndpointGrid((endpoint,))
    assert census.geometry_distance_to_point_m(
        geometry, endpoint.latitude, endpoint.longitude
    ) < 0.001
    assert grid.candidate_ids(geometry, census.FALLBACK_M) == {0}
    assert grid.within_geometry(geometry, census.FALLBACK_M) == (0,)


def test_multisegment_longitude_envelopes_are_unioned_not_vertex_minimized():
    geometry = {"type": "LineString", "coordinates": [
        [0, 0], [120, 0], [-120, 0], [0, 0],
    ]}
    endpoints = (
        census.Endpoint(0.0, 60.0),
        census.Endpoint(0.0, 180.0),
        census.Endpoint(0.0, -60.0),
        census.Endpoint(40.0, -60.0),
    )
    grid = census.EndpointGrid(endpoints)
    assert grid.candidate_ids(geometry, census.FALLBACK_M) == {0, 1, 2}
    assert grid.within_geometry(geometry, census.FALLBACK_M) == (0, 1, 2)


def test_pole_near_antipodal_long_and_degenerate_segments_fail_closed():
    pole = {"type": "LineString", "coordinates": [[0, 80], [180, 80]]}
    pole_endpoint = census.Endpoint(90.0, 73.0)
    pole_grid = census.EndpointGrid((pole_endpoint, census.Endpoint(0.0, 0.0)))
    assert census.geometry_distance_to_point_m(pole, 90.0, 73.0) < 0.001
    assert 0 in pole_grid.candidate_ids(pole, census.FALLBACK_M)
    assert pole_grid.within_geometry(pole, census.FALLBACK_M) == (0,)

    endpoints = (
        census.Endpoint(0.0, 90.0),
        census.Endpoint(0.0, -90.0),
        census.Endpoint(45.0, -90.0),
        census.Endpoint(-20.0, 10.0),
    )
    for longitude in (180.0, 179.999999):
        geometry = {
            "type": "LineString", "coordinates": [[0, 0], [longitude, 0]],
        }
        grid = census.EndpointGrid(endpoints)
        coarse = grid.candidate_ids(geometry, census.FALLBACK_M)
        exact = {
            endpoint_id for endpoint_id, endpoint in enumerate(endpoints)
            if census.geometry_distance_to_point_m(
                geometry, endpoint.latitude, endpoint.longitude
            ) <= census.FALLBACK_M
        }
        assert exact
        assert coarse == {0, 1, 2, 3}
        assert exact <= coarse
        with pytest.raises(census.CensusError, match="more than 3"):
            grid.candidate_ids(geometry, census.FALLBACK_M, limit=3)

    long_geometry = {
        "type": "LineString", "coordinates": [[0, 0], [179, 0]],
    }
    long_grid = census.EndpointGrid((census.Endpoint(0.0, 90.0),))
    assert long_grid.within_geometry(long_geometry, census.FALLBACK_M) == (0,)

    degenerate = {
        "type": "LineString",
        "coordinates": [[-123.4, -45.6], [-123.4, -45.6]],
    }
    degenerate_grid = census.EndpointGrid((census.Endpoint(-45.6, -123.4),))
    assert degenerate_grid.candidate_ids(degenerate, census.FALLBACK_M) == {0}
    assert degenerate_grid.within_geometry(degenerate, census.FALLBACK_M) == (0,)


def test_polygon_holes_and_multipolygons_have_conservative_coarse_union():
    geometry = {"type": "MultiPolygon", "coordinates": [
        [
            [[-102, -32], [-98, -32], [-98, -28], [-102, -28], [-102, -32]],
            [[-100.2, -30.2], [-99.8, -30.2], [-99.8, -29.8],
             [-100.2, -29.8], [-100.2, -30.2]],
        ],
        [[[29, -21], [31, -21], [31, -19], [29, -19], [29, -21]]],
    ]}
    endpoints = (
        census.Endpoint(-31.0, -100.0),
        census.Endpoint(-30.0, -100.0),
        census.Endpoint(-30.0, -100.2),
        census.Endpoint(-20.0, 30.0),
        census.Endpoint(40.0, 30.0),
    )
    grid = census.EndpointGrid(endpoints)
    exact = {
        endpoint_id for endpoint_id, endpoint in enumerate(endpoints)
        if census.geometry_distance_to_point_m(
            geometry, endpoint.latitude, endpoint.longitude
        ) <= census.FALLBACK_M
    }
    assert exact == {0, 2, 3}
    assert exact <= grid.candidate_ids(geometry, census.FALLBACK_M)
    assert grid.within_geometry(geometry, census.FALLBACK_M) == (0, 2, 3)


def test_exact_five_kilometre_boundary_is_not_coarsely_dropped():
    geometry = {"type": "Point", "coordinates": [0.0, 0.0]}
    requested = (4_999.999, 5_000.0, 5_000.001)
    endpoints = tuple(
        census.Endpoint(0.0, math.degrees(distance / census.EARTH_RADIUS_M))
        for distance in requested
    )
    distances = tuple(
        census.geometry_distance_to_point_m(
            geometry, endpoint.latitude, endpoint.longitude
        )
        for endpoint in endpoints
    )
    assert distances == pytest.approx(requested, abs=1e-8)
    radius = distances[1]
    grid = census.EndpointGrid(endpoints)
    exact = {
        endpoint_id for endpoint_id, distance in enumerate(distances)
        if distance <= radius
    }
    assert exact == {0, 1}
    assert exact <= grid.candidate_ids(geometry, radius)
    assert grid.within_geometry(geometry, radius) == (0, 1)


@pytest.mark.parametrize("geometry, first, second, fraction, side, expected_plan", [
    (
        {"type": "LineString", "coordinates": [[-0.2, 0.0], [0.2, 0.0]]},
        (0.0, -0.2), (0.0, 0.2), 0.5, 1.0, "local-cap",
    ),
    (
        {"type": "LineString", "coordinates": [[-60.0, 60.0], [60.0, 60.0]]},
        (60.0, -60.0), (60.0, 60.0), 0.5, 1.0, "merged-segments",
    ),
    (
        {"type": "Polygon", "coordinates": [[
            [-0.1, -0.1], [0.1, -0.1], [0.1, 0.1],
            [-0.1, 0.1], [-0.1, -0.1],
        ]]},
        (-0.1, -0.1), (-0.1, 0.1), 0.5, 1.0, "local-cap",
    ),
    (
        {"type": "Polygon", "coordinates": [
            [[-0.2, -0.2], [0.2, -0.2], [0.2, 0.2],
             [-0.2, 0.2], [-0.2, -0.2]],
            [[-0.05, -0.05], [-0.05, 0.05], [0.05, 0.05],
             [0.05, -0.05], [-0.05, -0.05]],
        ]},
        (-0.05, -0.05), (0.05, -0.05), 0.5, 1.0, "local-cap",
    ),
    (
        {"type": "MultiPolygon", "coordinates": [
            [[[-0.1, -0.1], [0.1, -0.1], [0.1, 0.1],
              [-0.1, 0.1], [-0.1, -0.1]]],
            [[[9.9, -0.1], [10.1, -0.1], [10.1, 0.1],
              [9.9, 0.1], [9.9, -0.1]]],
        ]},
        (-0.1, -0.1), (-0.1, 0.1), 0.5, 1.0, "merged-segments",
    ),
    (
        {"type": "LineString", "coordinates": [[179.8, 10.0], [-179.8, 10.0]]},
        (10.0, 179.8), (10.0, -179.8), 0.5, 1.0, "local-cap",
    ),
    (
        {"type": "LineString", "coordinates": [[-60.0, 89.0], [60.0, 89.0]]},
        (89.0, -60.0), (89.0, 60.0), 0.5, 1.0, "merged-segments",
    ),
], ids=(
    "line-local-cap", "line-per-segment", "polygon-exterior",
    "polygon-hole-boundary", "multipolygon", "dateline", "near-pole",
))
def test_nonpoint_exact_five_kilometre_boundaries_are_independently_bracketed(
        geometry, first, second, fraction, side, expected_plan):
    if geometry["type"] in ("Polygon", "MultiPolygon"):
        census._validate_polygon_topology(geometry)
    requested = (4_999.999, 5_000.0, 5_000.001)
    endpoints = tuple(
        census.Endpoint(*_independent_segment_offset(
            first, second, fraction, distance, side
        ))
        for distance in requested
    )
    distances = tuple(
        census.geometry_distance_to_point_m(
            geometry, endpoint.latitude, endpoint.longitude
        )
        for endpoint in endpoints
    )
    assert distances == pytest.approx(requested, abs=2e-5)
    assert distances[0] < census.FALLBACK_M < distances[2]
    diagnostics = {}
    grid = census.EndpointGrid(endpoints)
    coarse = grid.candidate_ids(
        geometry, census.FALLBACK_M, diagnostics=diagnostics
    )
    policy_exact = {
        endpoint_id for endpoint_id, distance in enumerate(distances)
        if distance <= census.FALLBACK_M
    }
    assert diagnostics["plan"] == expected_plan
    assert policy_exact
    assert policy_exact <= coarse

    model_boundary = distances[1]
    boundary_coarse = grid.candidate_ids(geometry, model_boundary)
    assert {0, 1} <= boundary_coarse
    assert grid.within_geometry(geometry, model_boundary) == (0, 1)


def test_generated_geometry_coarse_candidates_superset_exact_five_kilometres():
    generator = random.Random(0x5E6D3E)
    exact_distance_evaluations = 0

    def wrapped(longitude):
        return (longitude + 180.0) % 360.0 - 180.0

    def rectangle(latitude, longitude, latitude_half, longitude_half):
        west = wrapped(longitude - longitude_half)
        east = wrapped(longitude + longitude_half)
        return [
            [west, latitude - latitude_half],
            [east, latitude - latitude_half],
            [east, latitude + latitude_half],
            [west, latitude + latitude_half],
            [west, latitude - latitude_half],
        ]

    for case in range(320):
        if case % 10 == 0:
            latitude = (-1.0 if case % 20 else 1.0) * generator.uniform(
                87.0, 88.5
            )
        else:
            latitude = generator.uniform(-88.0, 88.0)
        longitude = generator.uniform(-180.0, 180.0)
        endpoints = tuple(
            census.Endpoint(
                generator.uniform(-89.99, 89.99),
                generator.uniform(-180.0, 180.0),
            )
            for _ in range(40)
        )
        geometry_kind = case % 4
        if geometry_kind == 0:
            geometry = {
                "type": "Point", "coordinates": [longitude, latitude],
            }
            targeted = (census.Endpoint(latitude, longitude),)
        elif geometry_kind == 1:
            coordinates = [[longitude, latitude]]
            for _ in range(1 + case % 3):
                coordinates.append([
                    generator.uniform(-180.0, 180.0),
                    generator.uniform(-89.5, 89.5),
                ])
            geometry = {"type": "LineString", "coordinates": coordinates}
            first = (coordinates[0][1], coordinates[0][0])
            second = (coordinates[1][1], coordinates[1][0])
            midpoint = _great_circle_point(first, second, 0.37)
            nearby = census.Endpoint(*_independent_segment_offset(
                first, second, 0.37, 4_999.0,
                -1.0 if midpoint[0] >= 0.0 else 1.0,
            ))
            targeted = (census.Endpoint(*midpoint), nearby)
        elif geometry_kind == 2:
            if case % 32 == 2:
                south = latitude - 0.5
                north = latitude + 0.5
                outer = [
                    [longitude, south],
                    [wrapped(longitude + 170.0), south],
                    [wrapped(longitude + 170.0), north],
                    [wrapped(longitude - 170.0), north],
                    [wrapped(longitude - 170.0), south],
                    [longitude, south],
                ]
                geometry = {"type": "Polygon", "coordinates": [outer]}
                targeted = (census.Endpoint(latitude, longitude),)
            else:
                outer = rectangle(latitude, longitude, 1.0, 1.5)
                hole = rectangle(latitude, longitude, 0.2, 0.3)
                geometry = {"type": "Polygon", "coordinates": [outer, hole]}
                targeted = (
                    census.Endpoint(latitude + 0.6, longitude),
                    census.Endpoint(latitude, wrapped(longitude + 0.3)),
                )
        else:
            second_longitude = wrapped(longitude + 20.0)
            geometry = {"type": "MultiPolygon", "coordinates": [
                [rectangle(latitude, longitude, 0.5, 0.75)],
                [rectangle(latitude, second_longitude, 0.4, 0.6)],
            ]}
            targeted = (
                census.Endpoint(latitude, longitude),
                census.Endpoint(latitude, second_longitude),
            )
        boundary_targets = []
        if geometry["type"] != "Point":
            for line in _independent_lines(geometry):
                for first_value, second_value in zip(line, line[1:]):
                    first = (first_value[1], first_value[0])
                    second = (second_value[1], second_value[0])
                    angle = _independent_angular_distance(first, second)
                    if math.pi - angle <= 1e-6:
                        continue
                    midpoint = _great_circle_point(first, second, 0.37)
                    boundary_targets.append(census.Endpoint(*midpoint))
                    boundary_targets.append(census.Endpoint(
                        *_independent_segment_offset(
                            first, second, 0.37, 4_999.0,
                            -1.0 if midpoint[0] >= 0.0 else 1.0,
                        )
                    ))
        all_endpoints = endpoints + targeted + tuple(boundary_targets)
        grid = census.EndpointGrid(all_endpoints)
        exact_distance_evaluations += len(all_endpoints)
        exact = {
            endpoint_id for endpoint_id, endpoint in enumerate(all_endpoints)
            if census.geometry_distance_to_point_m(
                geometry, endpoint.latitude, endpoint.longitude
            ) <= census.FALLBACK_M
        }
        coarse = grid.candidate_ids(geometry, census.FALLBACK_M)
        assert exact
        assert exact <= coarse, (
            f"case={case} kind={geometry_kind} missing={sorted(exact - coarse)}"
        )
    assert exact_distance_evaluations == 16_172


def _write_national_fixture(root: Path):
    geom_dir = root / "geom"
    geom_dir.mkdir()
    rows = []
    for code in sorted(US_JURISDICTION_CODES):
        slug = f"fixture-{code.lower()}"
        rows.append([slug, f"Fixture {code}", STATE_NAMES[code], 0.0, 0.0, 1, 1.0])
        (geom_dir / f"{slug}.json").write_text(json.dumps({
            "id": slug,
            "name": f"Fixture {code}",
            "state": STATE_NAMES[code],
            "bbox": [0.0, 0.0, 0.001, 0.0],
            "trails": [{
                "id": "fixture-trail",
                "name": "Fixture Trail",
                "segments": [[[0.0, 0.0], [0.0, 0.001]]],
            }],
        }, separators=(",", ":")))
    bundle = root / "areas-index.json"
    bundle.write_text(json.dumps(rows, separators=(",", ":")))
    return bundle, geom_dir


def _write_end_to_end_inputs(root: Path):
    bundle, geom_dir = _write_national_fixture(root)
    fixture = root / "parking.geojsonseq"
    parking_rows = [
        {"_meta": {"timestamp": "2026-10-05T00:00:00Z", "sequence": 7}},
        _geojson_feature("n1", {"type": "Point", "coordinates": [0.0, 0.0]}),
        _geojson_feature("n2", {"type": "Point", "coordinates": [0.002, 0.0]}),
        _geojson_feature("n3", {"type": "Point", "coordinates": [0.003, 0.0]},
                         access="private"),
        _geojson_feature("n4", {"type": "Point", "coordinates": [0.004, 0.0]}),
    ]
    fixture.write_text("".join(json.dumps(row, separators=(",", ":")) + "\n"
                               for row in parking_rows))
    live = root / "live.json"
    live.write_text(json.dumps([
        [0.0, 0.0, "Matched Live"],
        [20.0, 20.0, "Live Only"],
    ], separators=(",", ":")))
    verdicts = root / "verdicts.json"
    verdicts.write_text(json.dumps(_strict_verdict_document(
        _minimal_verdict_entry("node/2", "REVIEW", latitude=0.0, longitude=0.002)
    ), separators=(",", ":")))
    return bundle, geom_dir, fixture, live, verdicts


def test_end_to_end_bytes_hashes_scope_order_and_live_union_are_deterministic(tmp_path):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    bundle, geom_dir, fixture, live, verdicts = _write_end_to_end_inputs(inputs)
    outputs = []
    for name in ("output-a", "output-b"):
        output = tmp_path / name
        result = census.run_census(
            bundle_path=bundle,
            geom_dir=geom_dir,
            parking_path=fixture,
            parking_kind="geojsonseq",
            live_pool_path=live,
            verdicts_path=verdicts,
            output_dir=output,
            shard_size=500,
        )
        outputs.append((output, result))

    first_manifest = (outputs[0][0] / "manifest.json").read_bytes()
    second_manifest = (outputs[1][0] / "manifest.json").read_bytes()
    first_shard = (outputs[0][0] / "work-units-00000.jsonl").read_bytes()
    second_shard = (outputs[1][0] / "work-units-00000.jsonl").read_bytes()
    assert first_manifest == second_manifest
    assert first_shard == second_shard
    manifest = json.loads(first_manifest)
    without_root = copy.deepcopy(manifest)
    root_hash = without_root.pop("root_sha256")
    assert root_hash == census._canonical_hash(without_root)
    assert manifest["kind"] == "national_parking_census"
    assert manifest["schema"] == census.SCHEMA_ID
    assert manifest["status"] == "non_authoritative"
    assert manifest["production_authorization"] == {
        "authoritative": False,
        "baseline_self_sha256": None,
        "national_pbf_floor_enforced": False,
        "non_authoritative_reason": (
            "fixture/subnational runs cannot claim production completion"
        ),
        "production_complete": False,
    }
    assert manifest["run_id"].startswith("pcr1:")
    assert manifest["algorithm"]["parking_capture_geometry_policy"] == (
        census._parking_geometry_policy()
    )
    assert manifest["algorithm"]["parking_filtered_pbf_byte_policy"] == (
        census._filtered_pbf_policy()
    )
    resource_policy = manifest["algorithm"]["resource_policy"]
    assert resource_policy["max_endpoint_candidates_per_feature"] == 100_000
    assert resource_policy["max_endpoint_grid_segment_envelopes"] == (
        census.MAX_ENDPOINT_GRID_SEGMENT_ENVELOPES
    )
    assert resource_policy["max_endpoint_grid_query_windows"] == (
        census.MAX_ENDPOINT_GRID_QUERY_WINDOWS
    )
    assert resource_policy["max_endpoint_grid_spherical_query_windows"] == (
        census.MAX_ENDPOINT_GRID_SPHERICAL_QUERY_WINDOWS
    )
    assert resource_policy[
        "max_endpoint_grid_head_parity_query_windows"
    ] == census.MAX_ENDPOINT_GRID_HEAD_PARITY_QUERY_WINDOWS
    assert resource_policy["max_endpoint_grid_query_windows"] == (
        resource_policy["max_endpoint_grid_spherical_query_windows"]
        + resource_policy["max_endpoint_grid_head_parity_query_windows"]
    )
    assert resource_policy["max_endpoint_grid_retained_window_slots"] == (
        census.MAX_ENDPOINT_GRID_RETAINED_WINDOW_SLOTS
    )
    assert resource_policy["max_endpoint_grid_retained_window_bytes"] == (
        64 * 1024 * 1024
    )
    assert resource_policy["endpoint_grid_retained_window_slot_bytes"] == 512
    assert resource_policy["head_endpoint_grid_query_cells"] == 500_000
    assert resource_policy["max_endpoint_grid_row_probes"] == 1_000_000
    assert resource_policy["max_endpoint_grid_spherical_row_probes"] == 500_000
    assert resource_policy[
        "max_endpoint_grid_head_parity_row_probes"
    ] == 500_000
    assert resource_policy["max_endpoint_grid_cell_probes"] == 1_000_000
    assert resource_policy[
        "max_endpoint_grid_spherical_cell_probes"
    ] == 500_000
    assert resource_policy[
        "max_endpoint_grid_head_parity_cell_probes"
    ] == 500_000
    assert resource_policy[
        "stream_structural_preparse_minimum_record_bytes"
    ] == census.STREAM_STRUCTURAL_PREPARSE_MIN_BYTES
    assert resource_policy["max_stream_json_nesting_depth"] == 128
    assert resource_policy["max_stream_record_bytes"] == 16 * 1024 * 1024
    assert resource_policy["endpoint_grid_vertex_scan"] == {
        "units": "GeoJSON coordinate-pair arrays",
        "admission": "maximum-record-bytes-only",
        "numeric_spelling_policy": "no-coordinate-count-ceiling",
        "maximum_json_nesting_depth": 128,
        "json_depth_fastpath": (
            "total-opening-delimiters-at-most-maximum-proves-bound"
        ),
        "structural_preparse_minimum_record_bytes": (
            census.STREAM_STRUCTURAL_PREPARSE_MIN_BYTES
        ),
        "structural_preparse_diagnostics": (
            "potentially-parking-depth-and-large-record-coordinate-pairs-"
            "after-parsed-amenity-relevance"
        ),
        "nonparking_preparse": "disabled",
        "record_framing": (
            "maximum-applies-to-JSON-payload-after-removing-one-optional-"
            "record-separator-and-one-LF-CRLF-or-CR-line-terminator"
        ),
        "vertices_inspected": "diagnostic-only-not-grid-work",
    }
    assert resource_policy["endpoint_grid_segment_planning"] == {
        "units": "precharged spherical segment envelopes",
        "maximum": census.MAX_ENDPOINT_GRID_SEGMENT_ENVELOPES,
        "derivation": (
            "ceil(max_stream_record_bytes / "
            "minimum_compact_geojson_coordinate_bytes)"
        ),
        "minimum_compact_geojson_coordinate_bytes": 6,
    }
    assert resource_policy["endpoint_grid_window_probes"] == {
        "units": (
            "precharged spherical/global and separate literal-HEAD-expanded-"
            "query windows actually scanned"
        ),
        "maximum": census.MAX_ENDPOINT_GRID_QUERY_WINDOWS,
        "spherical_maximum": (
            census.MAX_ENDPOINT_GRID_SPHERICAL_QUERY_WINDOWS
        ),
        "head_parity_maximum": (
            census.MAX_ENDPOINT_GRID_HEAD_PARITY_QUERY_WINDOWS
        ),
        "derivation": (
            "sum-of-three-times-segment-envelope-maximum-for-spherical-seam-"
            "splits-and-polygon-interiors-plus-one-literal-HEAD-expanded-"
            "query-window"
        ),
    }
    assert resource_policy["endpoint_grid_window_retention"] == {
        "units": "pre-reserved simultaneous merge input/output slots-and-bytes",
        "maximum_slots": census.MAX_ENDPOINT_GRID_RETAINED_WINDOW_SLOTS,
        "maximum_bytes": census.MAX_ENDPOINT_GRID_RETAINED_WINDOW_BYTES,
        "reserved_bytes_per_slot": (
            census.ENDPOINT_GRID_RETAINED_WINDOW_SLOT_BYTES
        ),
        "derivation": (
            "64MiB-conservative-budget-at-512-bytes-per-live-window-for-four-"
            "float-tuple-list-run-sort-and-final-output-worst-case"
        ),
        "policy": (
            "reserve-before-transient-tuple-compute-each-proportional-append-"
            "or-replacement-and-final-freeze"
        ),
        "reporting": "current-and-peak-reserved-slots-and-bytes",
    }
    assert resource_policy["endpoint_grid_row_probes"] == {
        "units": (
            "precharged occupied latitude rows in independent-spherical and "
            "literal-HEAD lanes"
        ),
        "maximum": 1_000_000,
        "spherical_maximum": 500_000,
        "head_parity_maximum": 500_000,
        "derivation": (
            "sum-of-independent-500000-spherical-and-500000-literal-HEAD-"
            "expanded-query-lanes"
        ),
    }
    assert resource_policy["endpoint_grid_cell_probes"] == {
        "units": (
            "precharged globally-deduplicated longitude/occupied-grid-cells "
            "in independent spherical and literal-HEAD lanes"
        ),
        "maximum": 1_000_000,
        "spherical_maximum": 500_000,
        "head_parity_maximum": 500_000,
        "derivation": (
            "sum-of-independent-500000-spherical-and-500000-literal-HEAD-"
            "expanded-query-lanes"
        ),
    }
    assert resource_policy["endpoint_grid_query_work_units"] == (
        "sum-of-segment-window-row-cell-diagnostics-only-not-a-limit"
    )
    assert "max_endpoint_grid_query_work_units" not in resource_policy
    assert "max_endpoint_grid_extent_scan_vertices" not in resource_policy
    assert "max_endpoint_grid_tight_segments" not in resource_policy
    assert resource_policy[
        "parking_stream_progress_every_records"
    ] == 100_000
    for bound_name in (
            "max_endpoint_candidates_per_feature",
            "max_endpoint_grid_segment_envelopes",
            "max_endpoint_grid_query_windows",
            "max_endpoint_grid_spherical_query_windows",
            "max_endpoint_grid_head_parity_query_windows",
            "max_endpoint_grid_retained_window_slots",
            "max_endpoint_grid_retained_window_bytes",
            "endpoint_grid_retained_window_slot_bytes",
            "head_endpoint_grid_query_cells",
            "max_endpoint_grid_row_probes",
            "max_endpoint_grid_spherical_row_probes",
            "max_endpoint_grid_head_parity_row_probes",
            "max_endpoint_grid_cell_probes",
            "max_endpoint_grid_spherical_cell_probes",
            "max_endpoint_grid_head_parity_cell_probes",
            "stream_structural_preparse_minimum_record_bytes",
            "max_stream_json_nesting_depth",
            "max_stream_record_bytes"):
        changed_algorithm = copy.deepcopy(manifest["algorithm"])
        changed_algorithm["resource_policy"][bound_name] += 1
        assert census._deterministic_run_id(
            manifest["sources"], changed_algorithm
        ) != manifest["run_id"]
    assert manifest["sources"]["parking"]["seen_authority_aliases"] == {
        "count": 1,
        "sha256": census._canonical_hash(["node/2"]),
    }
    topology_inventory = manifest["sources"]["parking"]["inventory"][
        "topology_unvalidated_non_candidates"
    ]
    assert topology_inventory["status"] == (
        census.TOPOLOGY_UNVALIDATED_NONCANDIDATE_STATUS
    )
    assert topology_inventory["records"] == manifest["parking_stream"][
        "topology_unvalidated_non_candidates"
    ]
    assert len(topology_inventory["record_identity_sha256"]) == 64
    for inventory_name, counter_name in (
            ("degenerate_records", "topology_unvalidated_degenerate_records"),
            ("degenerate_rings", "topology_unvalidated_degenerate_rings"),
            ("degenerate_outer_rings",
             "topology_unvalidated_degenerate_outer_rings"),
            ("degenerate_inner_rings",
             "topology_unvalidated_degenerate_inner_rings"),
            ("degenerate_components",
             "topology_unvalidated_degenerate_components")):
        assert topology_inventory[inventory_name] == manifest[
            "parking_stream"
        ][counter_name]
    assert topology_inventory["degenerate_rings"] == (
        topology_inventory["degenerate_outer_rings"]
        + topology_inventory["degenerate_inner_rings"]
    )
    assert len(topology_inventory["degenerate_identity_sha256"]) == 64
    assert manifest["parking_stream"]["seen_authority_aliases"] == 1
    assert manifest["parking_stream"][
        "peak_endpoint_grid_retained_window_bytes"
    ] == (
        manifest["parking_stream"][
            "peak_endpoint_grid_retained_window_slots"
        ] * census.ENDPOINT_GRID_RETAINED_WINDOW_SLOT_BYTES
    )
    assert manifest["parking_stream"][
        "peak_endpoint_grid_retained_window_bytes"
    ] > 0
    assert manifest["verdict_ledger"]["seen_authority_aliases"] == 1
    assert manifest["verdict_ledger"]["seen_authority_aliases_sha256"] == (
        census._canonical_hash(["node/2"])
    )
    assert manifest["verdict_ledger"]["seen_authority_keys"] == 1
    assert manifest["verdict_ledger"]["reserved_authoritative"] == 1
    assert manifest["counts"]["areas"] == 51
    assert manifest["counts"]["jurisdictions"] == 51
    assert manifest["counts"]["trails"] == 51
    assert manifest["counts"]["endpoint_occurrences"] == 102
    assert manifest["counts"]["unique_endpoints"] == 2
    assert manifest["counts"]["trails_without_source_id"] == 0
    assert manifest["counts"]["anonymous_trail_refs"] == 0
    assert manifest["counts"]["live_pins"] == 2
    assert manifest["counts"]["live_only_candidate_clusters"] == 1
    assert manifest["parking_stream"]["rejected_total"] == 0
    assert manifest["parking_stream"]["record_equation"]["records_total"] == (
        manifest["parking_stream"]["record_equation"]["classified_records"]
    )
    assert manifest["parking_stream"]["parking_equation"]["parking_features"] == (
        manifest["parking_stream"]["parking_equation"]["reconciled_parking_records"]
    )
    staged = manifest["parking_stream"]["staged_topology_observations"]
    assert staged["coarse_outside"] == staged[
        "topology_not_required_outside"
    ]
    assert staged["contract"] == (
        "branch-observation-counters-not-independent-closure-equations"
    )
    assert manifest["counts"]["exact_remaining_denominator"] == (
        manifest["counts"]["current_candidate_clusters"]
        - manifest["counts"]["uniquely_matched_verdict_clusters"]
    )
    assert manifest["counts"]["exact_remaining"] == (
        manifest["counts"]["current_candidate_clusters"]
        - manifest["counts"]["uniquely_matched_verdict_clusters"]
    )
    assert manifest["counts"]["gross_work_units"] == (
        manifest["counts"]["current_candidate_clusters"]
        + manifest["counts"]["verdict_tombstones"]
    )
    assert set(manifest["owners"]) == (
        set(US_JURISDICTION_CODES) | {census.UNASSIGNED_OWNER}
    )
    assert sum(
        row["total_work_units"] for row in manifest["owners"].values()
    ) == manifest["counts"]["work_units"]
    assert sum(
        manifest["counts"][f"candidate_verdict_{name}"]
        for name in ("unmatched", "keep", "drop", "review")
    ) == manifest["counts"]["current_candidate_clusters"]
    assert manifest["counts"]["open_review_candidate_clusters"] >= (
        manifest["counts"]["candidate_verdict_review"]
    )
    geometry_sources = manifest["sources"]["geometry"]["files"]
    assert {row["area"] for row in geometry_sources} == {
        f"fixture-{code.lower()}" for code in US_JURISDICTION_CODES
    }
    assert all(row["path"].startswith("external/geometry-sha256-")
               for row in geometry_sources)
    assert manifest["shards"][0]["length"] == manifest["counts"]["work_units"]
    assert manifest["shards"][0]["sha256"] == hashlib.sha256(first_shard).hexdigest()
    assert str(tmp_path).encode() not in first_manifest
    assert str(tmp_path).encode() not in first_shard

    units = [json.loads(line) for line in first_shard.splitlines()]
    assert units[0]["kind"] == "live_only"
    review = next(unit for unit in units if unit["aliases"] == ["node/2"])
    assert review["verdict"] == {
        "effect": "HOLD", "key": "node/2", "status": "REVIEW"
    }
    assert review["routing"]["bucket"] == "review_hold"
    hard = next(unit for unit in units if unit["aliases"] == ["node/3"])
    assert hard["access"]["classification"] == "hard_non_public"
    assert hard["selection_projections"] == ["raw"]
    receipt_bytes = (outputs[0][0] / "publication-receipt.json").read_bytes()
    receipt = json.loads(receipt_bytes)
    assert "parent_directory_fsynced_after_promotion" not in manifest["publication"]
    assert receipt["promotion_parent_directory_fsync_completed"] is True
    assert receipt["manifest_root_sha256"] == manifest["root_sha256"]
    assert outputs[0][1]["durability_confirmed"] is True
    assert outputs[0][1]["total_bytes"] == (
        len(first_manifest) + len(first_shard) + len(receipt_bytes)
    )


def test_scope_excludes_only_recognized_non_us_suffix_and_rejects_unknown(tmp_path):
    bundle, geom_dir = _write_national_fixture(tmp_path)
    rows = json.loads(bundle.read_text())
    rows.append(["fixture-ca-ab", "Fixture Alberta", "Canada", 0, 0, 1, 1])
    bundle.write_text(json.dumps(rows))
    (geom_dir / "fixture-ca-ab.json").write_text(json.dumps({
        "id": "fixture-ca-ab", "state": "Canada", "bbox": [0, 0, 0, 0],
        "trails": [],
    }))
    capture = census.capture_scope(bundle, geom_dir)
    assert capture.excluded_non_us_areas == 1
    assert len(capture.areas) == 51

    rows[-1][0] = "fixture-zz"
    bundle.write_text(json.dumps(rows))
    (geom_dir / "fixture-ca-ab.json").rename(geom_dir / "fixture-zz.json")
    with pytest.raises(census.CensusError, match="recognized canonical jurisdiction"):
        census.capture_scope(bundle, geom_dir)


def _polygon_feature(alias: str, points_m, *, holes_m=(), endpoints=(0,)):
    def ring(values):
        coordinates = []
        for north_m, east_m in values:
            latitude, longitude = _offset(north_m=north_m, east_m=east_m)
            coordinates.append([longitude, latitude])
        return coordinates

    geometry = {
        "type": "Polygon",
        "coordinates": [ring(points_m), *(ring(values) for values in holes_m)],
    }
    latitude, longitude = census.geometry_representative(geometry)
    return census.ParkingFeature(
        alias=alias,
        source_form=alias,
        geometry=geometry,
        latitude=latitude,
        longitude=longitude,
        tags={"amenity": "parking"},
        near_endpoint_ids=tuple(endpoints),
    )


def _verdict_ring(feature):
    return [
        [latitude, longitude]
        for longitude, latitude in feature.geometry["coordinates"][0]
    ]


def test_general_polygon_overlap_uses_validated_shapely_branch():
    feature = _polygon_feature("way/1", [
        (0, 0), (0, 100), (12, 100), (12, 12),
        (100, 12), (100, 0), (0, 0),
    ])
    assert census._rectangle_bounds(feature.geometry) is None
    assert census.polygon_overlap_ratio(
        feature.geometry, copy.deepcopy(feature.geometry)
    ) == pytest.approx(1.0)


def test_general_polygon_overlap_fails_closed_without_shapely(monkeypatch):
    feature = _polygon_feature("way/1", [
        (0, 0), (0, 100), (12, 100), (12, 12),
        (100, 12), (100, 0), (0, 0),
    ])
    original_import = builtins.__import__

    def block_shapely(name, *args, **kwargs):
        if name == "shapely.geometry":
            raise ImportError("simulated missing Shapely")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", block_shapely)
    with pytest.raises(census.CensusError, match=r"requires Shapely==2\.0\.6"):
        census.polygon_overlap_ratio(
            feature.geometry, copy.deepcopy(feature.geometry)
        )


@pytest.mark.parametrize("shape", ["ell", "u"])
def test_irregular_polygon_verdict_probe_uses_bbox_centre(shape):
    shapes = {
        "ell": [
            (0, 0), (0, 100), (12, 100), (12, 12),
            (100, 12), (100, 0), (0, 0),
        ],
        "u": [
            (0, 0), (0, 100), (100, 100), (100, 80),
            (20, 80), (20, 20), (100, 20), (100, 0), (0, 0),
        ],
    }
    feature = _polygon_feature("way/999", shapes[shape])
    bbox_latitude, bbox_longitude = census.geometry_bbox_center(feature.geometry)
    assert census.haversine_m(
        feature.latitude, feature.longitude, bbox_latitude, bbox_longitude
    ) > pv.BBOX_CENTRE_M
    entry = _minimal_verdict_entry(
        "way/1", "KEEP",
        latitude=feature.latitude,
        longitude=feature.longitude,
        rings=[_verdict_ring(feature)],
    )
    verdicts = pv.Verdicts(_strict_verdict_document(entry))
    component = _component(feature)
    ledger = census.reconcile_verdict_ledger((component,), [], verdicts)
    assert ledger.matched_keys == ("way/1",)
    assert component.verdict_entry["_key"] == "way/1"
    claims = verdicts.claims({
        "osm": feature.alias, "lat": bbox_latitude, "lon": bbox_longitude,
    })
    assert claims[0]["rank"] == 0


def test_global_verdict_ledger_resolves_one_to_many_without_reuse():
    entry = _minimal_verdict_entry("node/1", "DROP")
    verdicts = pv.Verdicts(_strict_verdict_document(entry))
    closest = _component(_point_feature("node/100", east_m=1))
    farther = _component(_point_feature("node/101", east_m=10))
    ledger = census.reconcile_verdict_ledger(
        (closest, farther), [], verdicts
    )
    assert ledger.matched_keys == ("node/1",)
    assert ledger.assignments == {closest.candidate_id: closest.verdict_entry}
    assert closest.verdict_entry["_key"] == "node/1"
    assert farther.verdict_entry is None
    assert ledger.tombstone_entries == ()


def test_global_verdict_ledger_resolves_many_to_one_and_tombstones_rest():
    near = _offset(east_m=1)
    farther = _offset(east_m=10)
    entries = (
        _minimal_verdict_entry(
            "node/1", "KEEP", latitude=near[0], longitude=near[1]
        ),
        _minimal_verdict_entry(
            "node/2", "DROP", latitude=farther[0], longitude=farther[1]
        ),
    )
    verdicts = pv.Verdicts(_strict_verdict_document(*entries))
    component = _component(_point_feature("node/999"))
    ledger = census.reconcile_verdict_ledger((component,), [], verdicts)
    assert component.verdict_entry["_key"] == "node/1"
    assert ledger.matched_keys == ("node/1",)
    assert [entry["_key"] for entry in ledger.tombstone_entries] == ["node/2"]


def test_authoritative_alias_beats_positional_verdict_claim():
    neighbour = _offset(east_m=1)
    entries = (
        _minimal_verdict_entry("node/1", "KEEP", latitude=1.0, longitude=1.0),
        _minimal_verdict_entry(
            "node/2", "DROP", latitude=neighbour[0], longitude=neighbour[1]
        ),
    )
    verdicts = pv.Verdicts(_strict_verdict_document(*entries))
    component = _component(
        _point_feature("node/1"),
        _point_feature("node/999", east_m=1),
    )
    ledger = census.reconcile_verdict_ledger((component,), [], verdicts)
    assert component.verdict_entry["_key"] == "node/1"
    assert ledger.matched_keys == ("node/1",)
    assert [entry["_key"] for entry in ledger.tombstone_entries] == ["node/2"]


def test_live_only_candidate_participates_in_global_verdict_ledger():
    entry = _minimal_verdict_entry("node/1", "REVIEW")
    verdicts = pv.Verdicts(_strict_verdict_document(entry))
    pin = {"live_id": "live-only", "lat": 0.0, "lon": 0.0}
    ledger = census.reconcile_verdict_ledger((), [pin], verdicts)
    candidate_id = census.live_candidate_id(pin)
    assert ledger.matched_keys == ("node/1",)
    assert ledger.assignments[candidate_id]["_key"] == "node/1"
    assert pin["verdict_entry"]["_key"] == "node/1"


def test_candidate_id_is_versioned_sha256_of_nul_joined_aliases():
    aliases = ("way/2", "node/1")
    expected = "pc1:" + hashlib.sha256(b"node/1\0way/2").hexdigest()
    assert census.candidate_id_for_aliases(aliases) == expected


def test_ranked_live_identity_thresholds_and_hole_behavior():
    point = _component(_point_feature("node/1"))
    attached = census.attach_live_pins((point,), ({
        "live_id": "point-20", "lat": _offset(east_m=20)[0],
        "lon": _offset(east_m=20)[1],
    },), _empty_verdicts())
    assert attached == []
    assert [pin["live_id"] for pin in point.live_pins] == ["point-20"]

    point = _component(_point_feature("node/2"))
    synthetic = census.attach_live_pins((point,), ({
        "live_id": "point-21", "lat": _offset(east_m=21)[0],
        "lon": _offset(east_m=21)[1],
    },), _empty_verdicts())
    assert synthetic[0]["component_match"] == "unmatched"

    polygon = _component(_rectangle(
        "way/10", west_m=-30, east_m=30, south_m=-30, north_m=30
    ))
    for live_id, east_m in (("bbox", 0), ("inside", 8), ("edge", 39.5)):
        synthetic = census.attach_live_pins((polygon,), ({
            "live_id": live_id,
            "lat": _offset(east_m=east_m)[0],
            "lon": _offset(east_m=east_m)[1],
        },), _empty_verdicts())
        assert synthetic == []
    outside = census.attach_live_pins((polygon,), ({
        "live_id": "edge-over", "lat": _offset(east_m=41)[0],
        "lon": _offset(east_m=41)[1],
    },), _empty_verdicts())
    assert outside[0]["component_match"] == "unmatched"

    outer = [(-40, -40), (-40, 40), (40, 40), (40, -40), (-40, -40)]
    hole = [(-20, -20), (20, -20), (20, 20), (-20, 20), (-20, -20)]
    holed = _component(_polygon_feature("way/20", outer, holes_m=(hole,)))
    in_hole = census.attach_live_pins((holed,), ({
        "live_id": "hole", "lat": _offset(east_m=5)[0],
        "lon": _offset(east_m=5)[1],
    },), _empty_verdicts())
    assert in_hole[0]["component_match"] == "unmatched"


def test_live_identity_exact_rank_transitions_at_3_10_and_20_metres():
    ell = _component(_polygon_feature("way/1", [
        (0, 0), (0, 100), (12, 100), (12, 12),
        (100, 12), (100, 0), (0, 0),
    ]))

    def claim(component, north_m, east_m):
        latitude, longitude = _offset(north_m=north_m, east_m=east_m)
        return census._live_component_claim(component, latitude, longitude)

    assert claim(ell, 50, 53)[0] == 0
    assert claim(ell, 50, 53.2) is None
    assert claim(ell, 50, 5)[0] == 1
    assert claim(ell, 50, -10)[0] == 2
    assert claim(ell, 50, -10.2) is None

    point = _component(_point_feature("node/2"))
    assert claim(point, 0, 20)[0] == 3
    assert claim(point, 0, 20.2) is None


def test_ranked_live_identity_prefers_stronger_rank_and_holds_equal_rank():
    edge = _component(_rectangle("way/1", west_m=9, east_m=40))
    closer_point = _component(_point_feature("node/2", east_m=1))
    synthetic = census.attach_live_pins(
        (edge, closer_point),
        ({"live_id": "ranked", "lat": 0.0, "lon": 0.0},),
        _empty_verdicts(),
    )
    assert synthetic == []
    assert [pin["live_id"] for pin in edge.live_pins] == ["ranked"]
    assert closer_point.live_pins == []

    first = _component(_point_feature("node/3", east_m=1))
    second = _component(_point_feature("node/4", east_m=10))
    synthetic = census.attach_live_pins(
        (first, second),
        ({"live_id": "same-rank", "lat": 0.0, "lon": 0.0},),
        _empty_verdicts(),
    )
    assert synthetic[0]["component_match"] == "ambiguous"
    assert [claim["rank"] for claim in synthetic[0]["component_claims"]] == [3, 3]
    assert first.live_pins == second.live_pins == []


def test_point_at_21_to_40m_is_not_identity_but_remains_duplicate_review():
    first = _component(_point_feature("node/1"))
    pin = {"live_id": "thirty", "lat": _offset(east_m=30)[0],
           "lon": _offset(east_m=30)[1]}
    synthetic = census.attach_live_pins((first,), (pin,), _empty_verdicts())
    assert synthetic[0]["component_match"] == "unmatched"
    second = _component(_point_feature("node/2", east_m=30))
    groups, edge_count = census.proximity_review_groups((first, second))
    assert edge_count == 1
    assert groups[first.candidate_id]["group_id"] == groups[second.candidate_id]["group_id"]


@pytest.mark.parametrize("code, raw", [
    ("invalid_json", b"\xffnot-json"),
    ("malformed_meta", json.dumps({"_meta": []}).encode()),
    ("malformed_feature", json.dumps("not-a-feature").encode()),
    ("malformed_tags", json.dumps({
        "type": "Feature", "id": "n1",
        "properties": {"amenity": "parking", "bad": 1},
        "geometry": {"type": "Point", "coordinates": [0.0, 0.0]},
    }).encode()),
    ("missing_identity", json.dumps({
        "type": "Feature", "properties": {"amenity": "parking"},
        "geometry": {"type": "Point", "coordinates": [0.0, 0.0]},
    }).encode()),
    ("malformed_geometry", json.dumps(_geojson_feature(
        "n1", {"type": "Point", "coordinates": [0.0]}
    )).encode()),
    ("malformed_geometry", json.dumps(_geojson_feature(
        "w2", {"type": "Polygon", "coordinates": [[
            [0.0, 0.0], [3.0, 3.0], [0.0, 3.0],
            [2.0, 0.0], [0.0, 0.0],
        ]]}
    )).encode()),
    ("unsupported_geometry", json.dumps(_geojson_feature(
        "n1", {"type": "GeometryCollection", "geometries": []}
    )).encode()),
    ("malformed_relation_members", json.dumps({
        **_geojson_feature(
            "r1", {"type": "Point", "coordinates": [0.0, 0.0]}
        ),
        "parking_members": "not-an-array",
    }).encode()),
])
def test_every_stream_rejection_blocks_run_before_final_output(tmp_path, code, raw):
    bundle, geom_dir = _write_national_fixture(tmp_path)
    parking = tmp_path / "parking.geojsonseq"
    parking.write_bytes(raw + b"\n")
    live = tmp_path / "live.json"
    live.write_text("[]")
    verdicts = tmp_path / "verdicts.json"
    verdicts.write_text(json.dumps({"version": 1, "lots": {}}))
    streamed = census.stream_parking_source(
        parking, "geojsonseq",
        census.EndpointGrid((census.Endpoint(0.0, 0.0),)),
    )
    assert streamed.counters["rejections"] == {code: 1}
    output = tmp_path / "final"
    with pytest.raises(census.CensusError, match="complete census refused"):
        census.run_census(
            bundle_path=bundle,
            geom_dir=geom_dir,
            parking_path=parking,
            parking_kind="geojsonseq",
            live_pool_path=live,
            verdicts_path=verdicts,
            output_dir=output,
            shard_size=500,
        )
    assert not output.exists()
    assert not list(tmp_path.glob(".final.stage-*"))


def test_699052_pair_compact_record_is_accepted_and_processed():
    pair_count = 699_052
    prefix = (
        b'{"type":"Feature","id":"w9","properties":'
        b'{"amenity":"parking"},"geometry":{"type":"LineString",'
        b'"coordinates":['
    )
    raw = prefix + b"[0,0], " * (pair_count - 1) + b"[0,0]" + b"]}}"
    assert len(raw) == 4_893_475
    assert len(raw) < census.MAX_STREAM_RECORD_BYTES

    accumulator = census.FeatureAccumulator(census.EndpointGrid(()), {})
    accumulator.consume_line(raw)
    counters = accumulator.final_counters()
    assert counters["records_total"] == 1
    assert counters["parking_features"] == 1
    assert counters["outside_fallback_envelope"] == 1
    assert counters["rejected_total"] == 0
    assert counters["structural_preparse_records"] == 1
    assert counters["structural_preparse_coordinate_pairs"] == pair_count
    assert counters["max_structural_preparse_coordinate_pairs"] == pair_count
    assert counters["max_structural_preparse_nesting_depth"] >= 4


def test_near_16mib_compact_record_remains_parser_eligible(monkeypatch):
    prefix = (
        b'{"type":"Feature","id":"w987","properties":'
        b'{"amenity":"parking"},"geometry":{"type":"LineString",'
        b'"coordinates":['
    )
    suffix = b"]}}"
    point_bytes = b"[0,0],"
    point_count = (
        census.MAX_STREAM_RECORD_BYTES - len(prefix) - len(suffix) + 1
    ) // len(point_bytes)
    raw = prefix + point_bytes * (point_count - 1) + b"[0,0]" + suffix
    assert len(raw) <= census.MAX_STREAM_RECORD_BYTES
    assert len(raw) >= census.MAX_STREAM_RECORD_BYTES * 99 // 100
    assert point_count > 2_700_000

    parsed = []

    def bounded_parse(value, **_kwargs):
        parsed.append((len(value), value.startswith('{"type":"Feature"')))
        return {
            "type": "Feature",
            "id": "n1",
            "properties": {"amenity": "school"},
            "geometry": None,
        }

    monkeypatch.setattr(census.json, "loads", bounded_parse)
    accumulator = census.FeatureAccumulator(census.EndpointGrid(()), {})
    accumulator.consume_line(raw)
    counters = accumulator.final_counters()
    assert parsed == [(len(raw), True)]
    assert counters["ignored_nonparking"] == 1
    assert counters["rejected_total"] == 0
    assert counters["json_depth_fastpath_records"] == 0
    assert counters["json_depth_scanned_records"] == 0
    assert counters["structural_preparse_records"] == 0
    assert counters["structural_preparse_coordinate_pairs"] == 0
    assert counters["max_structural_preparse_coordinate_pairs"] == 0


def _sized_nonparking_json_payload(size):
    prefix = (
        b'{"type":"Feature","properties":{"amenity":"school",'
        b'"padding":"'
    )
    suffix = b'"},"geometry":null}'
    payload = prefix + b"x" * (size - len(prefix) - len(suffix)) + suffix
    assert len(payload) == size
    return payload


@pytest.mark.parametrize(
    "record_separator", (b"", b"\x1e"), ids=("LF", "RS-plus-LF")
)
def test_exact_16mib_json_payload_with_sequence_framing_is_eligible(
        tmp_path, record_separator):
    payload = _sized_nonparking_json_payload(
        census.MAX_STREAM_RECORD_BYTES
    )
    parking = tmp_path / "exact-limit.geojsonseq"
    parking.write_bytes(record_separator + payload + b"\n")

    streamed = census.stream_parking_source(
        parking, "geojsonseq", census.EndpointGrid(())
    )
    assert streamed.counters["records_total"] == 1
    assert streamed.counters["ignored_nonparking"] == 1
    assert streamed.counters["rejected_total"] == 0
    assert streamed.counters["rejections"] == {}


def test_16mib_plus_one_json_payload_with_line_framing_is_rejected(tmp_path):
    payload = _sized_nonparking_json_payload(
        census.MAX_STREAM_RECORD_BYTES + 1
    )
    parking = tmp_path / "over-limit.geojsonseq"
    parking.write_bytes(payload + b"\n")

    streamed = census.stream_parking_source(
        parking, "geojsonseq", census.EndpointGrid(())
    )
    assert streamed.counters["records_total"] == 1
    assert streamed.counters["ignored_nonparking"] == 0
    assert streamed.counters["rejections"] == {"oversized_record": 1}
    assert streamed.counters["rejected_total"] == 1


def test_shallow_parking_uses_delimiter_depth_proof_without_python_scan(
        monkeypatch):
    def forbid_scan(*_args, **_kwargs):
        pytest.fail("shallow parking record reached full structural scanner")

    monkeypatch.setattr(census, "_preparse_stream_structure", forbid_scan)
    accumulator = census.FeatureAccumulator(census.EndpointGrid(()), {})
    accumulator.consume_line(json.dumps(_geojson_feature(
        "n987", {"type": "Point", "coordinates": [0.0, 0.0]}
    )).encode())
    counters = accumulator.final_counters()
    assert counters["parking_features"] == 1
    assert counters["json_depth_fastpath_records"] == 1
    assert counters["json_depth_scanned_records"] == 0
    assert counters["rejected_total"] == 0


@pytest.mark.parametrize("error_type", [MemoryError, RecursionError])
def test_structural_scan_resource_error_is_contextual_record_rejection(
        monkeypatch, error_type):
    raw = json.dumps(_geojson_feature(
        "w88", {
            "type": "LineString",
            "coordinates": [[0.0, 0.0], [2.0, 2.0]],
        },
    )).encode()

    def exhaust_resources(*_args, **_kwargs):
        raise error_type("synthetic structural scan exhaustion")

    monkeypatch.setattr(census, "STREAM_STRUCTURAL_PREPARSE_MIN_BYTES", 0)
    monkeypatch.setattr(
        census, "_preparse_stream_structure", exhaust_resources
    )
    accumulator = census.FeatureAccumulator(census.EndpointGrid(()), {})
    accumulator.consume_line(raw)
    counters = accumulator.final_counters()
    assert counters["records_total"] == 1
    assert counters["rejections"] == {"resource_exhaustion": 1}
    assert counters["record_equation"] == {
        "records_total": 1, "classified_records": 1,
    }
    assert counters["rejection_contexts"] == {
        "resource_exhaustion": (
            "parking feature way/88: host resources exhausted during "
            "structural JSON scan"
        )
    }
    with pytest.raises(
            census.CensusError,
            match="complete census refused.*parking feature way/88"):
        census._assert_stream_complete(census.ParkingStreamResult(
            (), None, {}, counters, {}, (), {}
        ))


def test_json_parser_memory_error_is_contextual_record_rejection(monkeypatch):
    raw = json.dumps(_geojson_feature(
        "n88", {"type": "Point", "coordinates": [0.0, 0.0]}
    )).encode()

    def exhaust_memory(*_args, **_kwargs):
        raise MemoryError("synthetic parser exhaustion")

    monkeypatch.setattr(census.json, "loads", exhaust_memory)
    accumulator = census.FeatureAccumulator(census.EndpointGrid(()), {})
    accumulator.consume_line(raw)
    counters = accumulator.final_counters()
    assert counters["records_total"] == 1
    assert counters["rejections"] == {"resource_exhaustion": 1}
    assert counters["rejection_contexts"] == {
        "resource_exhaustion": (
            "parking feature node/88: host memory exhausted during JSON parsing"
        )
    }


def test_overdepth_json_is_contextual_invalid_record_and_stream_continues():
    nested = (
        b"[" * (census.MAX_STREAM_JSON_NESTING_DEPTH + 1)
        + b"0"
        + b"]" * (census.MAX_STREAM_JSON_NESTING_DEPTH + 1)
    )
    raw = (
        b'{"type":"Feature","id":"w128","properties":'
        b'{"amenity":"parking"},"geometry":{"type":"LineString",'
        b'"coordinates":[[0,0],[1,1]]},"diagnostic":'
        + nested
        + b"}"
    )
    accumulator = census.FeatureAccumulator(census.EndpointGrid(()), {})
    accumulator.consume_line(raw)
    accumulator.consume_line(json.dumps(_geojson_feature(
        "n129", {"type": "Point", "coordinates": [0.0, 0.0]}
    )).encode())
    counters = accumulator.final_counters()
    assert counters["records_total"] == 2
    assert counters["parking_features"] == 1
    assert counters["json_nesting_depth_rejections"] == 1
    assert counters["rejections"] == {"invalid_json": 1}
    assert counters["record_equation"] == {
        "records_total": 2, "classified_records": 2,
    }
    assert "parking feature way/128: JSON nesting depth" in counters[
        "rejection_contexts"
    ]["invalid_json"]
    with pytest.raises(census.CensusError, match="complete census refused"):
        census._assert_stream_complete(census.ParkingStreamResult(
            (), None, {}, counters, {}, (), {}
        ))


def test_json_parser_recursion_error_is_record_rejection_and_stream_continues(
        monkeypatch):
    real_loads = census.json.loads
    calls = 0

    def recurse_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RecursionError("synthetic parser recursion")
        return real_loads(*args, **kwargs)

    monkeypatch.setattr(census.json, "loads", recurse_once)
    accumulator = census.FeatureAccumulator(census.EndpointGrid(()), {})
    accumulator.consume_line(json.dumps(_geojson_feature(
        "n130", {"type": "Point", "coordinates": [0.0, 0.0]}
    )).encode())
    accumulator.consume_line(json.dumps(_geojson_feature(
        "n131", {"type": "Point", "coordinates": [0.0, 0.0]}
    )).encode())
    counters = accumulator.final_counters()
    assert counters["records_total"] == 2
    assert counters["parking_features"] == 1
    assert counters["rejections"] == {"resource_exhaustion": 1}
    assert counters["record_equation"] == {
        "records_total": 2, "classified_records": 2,
    }
    assert counters["rejection_contexts"] == {
        "resource_exhaustion": (
            "parking feature node/130: JSON parser exhausted recursion below "
            "the explicit nesting-depth ceiling"
        )
    }


def test_endpoint_grid_memory_error_is_typed_and_keeps_fresh_diagnostics(
        monkeypatch):
    def exhaust_memory(*_args, **_kwargs):
        raise MemoryError("synthetic planner exhaustion")

    monkeypatch.setattr(census, "_geometry_envelope_plan", exhaust_memory)
    diagnostics = {"stale": 1}
    with pytest.raises(
            census.CensusError,
            match=(
                "parking feature way/99 endpoint-grid processing exhausted "
                "host memory"
            )):
        census.EndpointGrid((census.Endpoint(0.0, 0.0),)).candidate_ids(
            {"type": "LineString", "coordinates": [[0.0, 0.0], [2.0, 2.0]]},
            census.FALLBACK_M,
            context="parking feature way/99",
            diagnostics=diagnostics,
        )
    assert "stale" not in diagnostics
    assert diagnostics["plan"] == "none"
    assert diagnostics["candidate_ids"] == 0


def test_endpoint_grid_recursion_error_is_typed_with_fresh_diagnostics(
        monkeypatch):
    def exhaust_recursion(*_args, **_kwargs):
        raise RecursionError("synthetic planner exhaustion")

    monkeypatch.setattr(census, "_geometry_envelope_plan", exhaust_recursion)
    diagnostics = {"stale": 1}
    with pytest.raises(
            census.CensusError,
            match=(
                "parking feature way/100 endpoint-grid processing exhausted "
                "recursion resources"
            )):
        census.EndpointGrid((census.Endpoint(0.0, 0.0),)).candidate_ids(
            {"type": "LineString", "coordinates": [[0.0, 0.0], [2.0, 2.0]]},
            census.FALLBACK_M,
            context="parking feature way/100",
            diagnostics=diagnostics,
        )
    assert "stale" not in diagnostics
    assert diagnostics["plan"] == "none"
    assert diagnostics["candidate_ids"] == 0


def test_oversized_stream_record_is_a_fatal_rejection(
        tmp_path, monkeypatch):
    bundle, geom_dir = _write_national_fixture(tmp_path)
    parking = tmp_path / "parking.geojsonseq"
    parking.write_text(json.dumps(_geojson_feature(
        "n1", {"type": "Point", "coordinates": [0.0, 0.0]}
    )) + "\n")
    live = tmp_path / "live.json"
    live.write_text("[]")
    verdicts = tmp_path / "verdicts.json"
    verdicts.write_text(json.dumps({"version": 1, "lots": {}}))
    monkeypatch.setattr(census, "MAX_STREAM_RECORD_BYTES", 32)
    streamed = census.stream_parking_source(
        parking, "geojsonseq",
        census.EndpointGrid((census.Endpoint(0.0, 0.0),)),
    )
    assert streamed.counters["rejections"] == {"oversized_record": 1}
    output = tmp_path / "final"
    with pytest.raises(census.CensusError, match="oversized_record"):
        census.run_census(
            bundle_path=bundle,
            geom_dir=geom_dir,
            parking_path=parking,
            parking_kind="geojsonseq",
            live_pool_path=live,
            verdicts_path=verdicts,
            output_dir=output,
            shard_size=500,
        )
    assert not output.exists()


def test_valid_definitively_nonparking_record_is_ignored_not_rejected(tmp_path):
    bundle, geom_dir = _write_national_fixture(tmp_path)
    parking = tmp_path / "parking.geojsonseq"
    parking.write_text(json.dumps({
        "type": "Feature",
        "id": "n1",
        "properties": {"amenity": "school"},
        "geometry": {"type": "GeometryCollection", "geometries": []},
    }) + "\n")
    live = tmp_path / "live.json"
    live.write_text("[]")
    verdicts = tmp_path / "verdicts.json"
    verdicts.write_text(json.dumps({"version": 1, "lots": {}}))
    output = tmp_path / "complete"
    result = census.run_census(
        bundle_path=bundle,
        geom_dir=geom_dir,
        parking_path=parking,
        parking_kind="geojsonseq",
        live_pool_path=live,
        verdicts_path=verdicts,
        output_dir=output,
        shard_size=500,
    )
    assert result["manifest"]["parking_stream"]["ignored_nonparking"] == 1
    assert result["manifest"]["parking_stream"]["rejected_total"] == 0
    assert result["manifest"]["counts"]["work_units"] == 0
    assert output.is_dir()


def test_end_to_end_malformed_record_is_inverted_to_fail_closed(tmp_path):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    bundle, geom_dir, fixture, live, verdicts = _write_end_to_end_inputs(inputs)
    with fixture.open("a") as stream:
        stream.write(json.dumps("malformed-record") + "\n")
    output = tmp_path / "output"
    with pytest.raises(census.CensusError, match="malformed_feature"):
        census.run_census(
            bundle_path=bundle,
            geom_dir=geom_dir,
            parking_path=fixture,
            parking_kind="geojsonseq",
            live_pool_path=live,
            verdicts_path=verdicts,
            output_dir=output,
            shard_size=500,
        )
    assert not output.exists()


def test_unmatched_verdict_emits_one_owned_tombstone_and_exact_equations(tmp_path):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    bundle, geom_dir, fixture, live, verdicts_path = _write_end_to_end_inputs(inputs)
    document = json.loads(verdicts_path.read_text())
    entry = _minimal_verdict_entry(
        "way/999", "DROP", latitude=40.0, longitude=40.0
    )
    document["lots"]["way/999"] = entry
    verdicts_path.write_text(json.dumps(document, separators=(",", ":")))
    output = tmp_path / "output"
    result = census.run_census(
        bundle_path=bundle,
        geom_dir=geom_dir,
        parking_path=fixture,
        parking_kind="geojsonseq",
        live_pool_path=live,
        verdicts_path=verdicts_path,
        output_dir=output,
        shard_size=500,
    )
    manifest = result["manifest"]
    counts = manifest["counts"]
    assert counts["sidecar_verdict_clusters"] == 2
    assert counts["uniquely_matched_verdict_clusters"] == 1
    assert counts["verdict_tombstones"] == 1
    assert counts["exact_remaining_denominator"] == (
        counts["current_candidate_clusters"]
        - counts["uniquely_matched_verdict_clusters"]
    )
    assert counts["exact_remaining"] == (
        counts["current_candidate_clusters"]
        - counts["uniquely_matched_verdict_clusters"]
    )
    assert counts["gross_work_units"] == (
        counts["current_candidate_clusters"] + counts["verdict_tombstones"]
    )
    tombstones = [
        unit for unit in result["work_units"]
        if unit["kind"] == "verdict_tombstone"
    ]
    assert len(tombstones) == 1
    assert tombstones[0]["verdict"]["key"] == "way/999"
    assert tombstones[0]["owner"] == {
        "jurisdiction": "CO", "area": "fixture-co"
    }


def test_cross_jurisdiction_candidate_has_one_owner_and_separate_references(tmp_path):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    bundle, geom_dir, fixture, live, verdicts = _write_end_to_end_inputs(inputs)
    result = census.run_census(
        bundle_path=bundle,
        geom_dir=geom_dir,
        parking_path=fixture,
        parking_kind="geojsonseq",
        live_pool_path=live,
        verdicts_path=verdicts,
        output_dir=tmp_path / "output",
        shard_size=500,
    )
    unit = next(
        value for value in result["work_units"]
        if value.get("aliases") == ["node/1"]
    )
    assert len(unit["service"]["jurisdictions"]) == 51
    assert unit["owner"]["jurisdiction"] in US_JURISDICTION_CODES
    assert unit["owner"]["area"] in unit["service"]["areas"]
    manifest = result["manifest"]
    assert sum(
        row["candidate_clusters"] for row in manifest["owners"].values()
    ) == manifest["counts"]["current_candidate_clusters"]
    assert sum(
        row["tombstones"] for row in manifest["owners"].values()
    ) == manifest["counts"]["verdict_tombstones"]
    assert sum(
        row["candidate_clusters"]
        for row in manifest["referenced_service"].values()
    ) > manifest["counts"]["current_candidate_clusters"]


def _output_contract(count):
    units = []
    for index in range(count):
        alias = f"node/{index + 1}"
        units.append({
            "candidate_id": census.candidate_id_for_aliases((alias,)),
            "kind": "osm",
            "aliases": [alias],
            "owner": {
                "jurisdiction": census.UNASSIGNED_OWNER,
                "area": census.UNASSIGNED_OWNER,
            },
            "verdict": {"status": "UNMATCHED", "effect": "UNRESOLVED",
                        "key": None},
            "routing": {"bucket": "unmatched", "rank": 1},
        })
    owners = {
        code: {"total_work_units": 0}
        for code in sorted(US_JURISDICTION_CODES)
    }
    owners[census.UNASSIGNED_OWNER] = {"total_work_units": count}
    manifest = {
        "kind": "national_parking_census",
        "schema": census.SCHEMA_ID,
        "schema_version": census.SCHEMA_VERSION,
        "status": "non_authoritative",
        "production_authorization": {
            "authoritative": False,
            "production_complete": False,
            "baseline_self_sha256": None,
            "national_pbf_floor_enforced": False,
            "non_authoritative_reason": "synthetic output contract",
        },
        "run_id": "pcr1:" + "0" * 64,
        "algorithm": {},
        "sources": {
            "bundle": {"path": "external/bundle", "sha256": "a"},
            "geometry": {
                "directory": "external/geometry",
                "portable_inventory_sha256": "b-portable",
                "authority_sha256": "b",
                "files": [],
            },
            "parking": {
                "path": "external/parking", "sha256": "c",
                "kind": "geojsonseq",
                "seen_authority_aliases": {
                    "count": 0, "sha256": census._canonical_hash([]),
                },
            },
            "live_pool": {"path": "external/live", "sha256": "d"},
            "verdicts": {"path": "external/verdicts", "sha256": "e"},
        },
        "parking_stream": {"seen_authority_aliases": 0},
        "counts": {
            "current_candidate_clusters": count,
            "uniquely_matched_verdict_clusters": 0,
            "exact_remaining_denominator": count,
            "exact_remaining": count,
            "verdict_tombstones": 0,
            "sidecar_verdict_clusters": 0,
            "gross_work_units": count,
            "work_units": count,
            "routing_live_unmatched": 0,
            "routing_unmatched": count,
            "routing_review_hold": 0,
            "routing_closed_trusted_decision": 0,
            "verdict_unmatched": count,
            "verdict_keep": 0,
            "verdict_drop": 0,
            "verdict_review": 0,
            "candidate_verdict_unmatched": count,
            "candidate_verdict_keep": 0,
            "candidate_verdict_drop": 0,
            "candidate_verdict_review": 0,
            "tombstone_verdict_unmatched": 0,
            "tombstone_verdict_keep": 0,
            "tombstone_verdict_drop": 0,
            "tombstone_verdict_review": 0,
            "binary_verdict_candidate_clusters": 0,
            "review_verdict_candidate_clusters": 0,
            "open_review_candidate_clusters": count,
            "tombstone_binary_verdict_clusters": 0,
            "tombstone_review_verdict_clusters": 0,
            "unassigned_work_units": count,
            "area_less_tombstones": 0,
            "retired_area_tombstones": 0,
            "unassigned_candidate_clusters": count,
        },
        "owners": owners,
        "verdict_ledger": {
            "matched": 0, "tombstones": 0, "sidecar_rows": 0,
            "reserved_authoritative": 0,
            "seen_authority_aliases": 0,
            "seen_authority_aliases_sha256": census._canonical_hash([]),
            "seen_authority_keys": 0,
        },
    }
    manifest["run_id"] = census._deterministic_run_id(
        manifest["sources"], manifest["algorithm"]
    )
    return units, manifest


def test_revalidation_failure_preserves_stage_and_never_exposes_final(tmp_path):
    units, manifest = _output_contract(1)
    destination = tmp_path / "final"

    def verifier():
        raise census.CensusError("simulated source mutation")

    with pytest.raises(census.CensusError, match="diagnostic stage"):
        census.write_outputs(destination, units, manifest, 500, verifier)
    assert not destination.exists()
    stages = list(tmp_path.glob(".final.stage-*"))
    assert len(stages) == 1
    assert {path.name for path in stages[0].iterdir()} == {
        "manifest.json", "work-units-00000.jsonl"
    }


def test_readback_failure_preserves_stage_and_final_absence(tmp_path, monkeypatch):
    units, manifest = _output_contract(1)
    destination = tmp_path / "final"

    def fail_readback(path, expected):
        raise census.CensusError(f"readback failed for {path.name}")

    monkeypatch.setattr(census, "_verify_staged_file", fail_readback)
    with pytest.raises(census.CensusError, match="readback failed"):
        census.write_outputs(destination, units, manifest, 500, lambda: None)
    assert not destination.exists()
    assert len(list(tmp_path.glob(".final.stage-*"))) == 1


def test_rename_race_preserves_intruder_and_diagnostic_stage(tmp_path):
    units, manifest = _output_contract(1)
    destination = tmp_path / "final"

    def race():
        destination.mkdir()
        (destination / "operator-note.txt").write_text("preserve")

    with pytest.raises(census.CensusError, match="appeared before promotion"):
        census.write_outputs(destination, units, manifest, 500, race)
    assert (destination / "operator-note.txt").read_text() == "preserve"
    assert len(list(tmp_path.glob(".final.stage-*"))) == 1


def test_rename_failure_keeps_final_absent_and_stage(tmp_path, monkeypatch):
    units, manifest = _output_contract(1)
    destination = tmp_path / "final"

    def fail_rename(stage, final):
        raise OSError("simulated rename failure")

    monkeypatch.setattr(census, "_rename_stage_exclusive", fail_rename)
    with pytest.raises(census.CensusError, match="rename failure"):
        census.write_outputs(destination, units, manifest, 500, lambda: None)
    assert not destination.exists()
    assert len(list(tmp_path.glob(".final.stage-*"))) == 1


def test_parent_fsync_failure_is_reported_after_atomic_promotion(
        tmp_path, monkeypatch):
    units, manifest = _output_contract(1)
    destination = tmp_path / "final"
    original = census._fsync_directory
    calls = []

    def fsync_directory(path):
        calls.append(Path(path))
        if len(calls) == 2:
            raise OSError("simulated parent fsync failure")
        return original(path)

    monkeypatch.setattr(census, "_fsync_directory", fsync_directory)
    with pytest.raises(census.CensusError, match="no durable publication success"):
        census.write_outputs(destination, units, manifest, 500, lambda: None)
    assert destination.is_dir()
    surviving_manifest = json.loads((destination / "manifest.json").read_bytes())
    assert "parent_directory_fsynced_after_promotion" not in (
        surviving_manifest["publication"]
    )
    assert surviving_manifest["publication"][
        "parent_directory_fsync_required_after_promotion"
    ] is True
    assert not (destination / "publication-receipt.json").exists()
    assert calls[0].name.startswith(".final.stage-")
    assert calls[1] == destination.parent
    assert not list(tmp_path.glob(".final.stage-*"))


def test_multi_shard_output_fsyncs_and_readback_verifies_every_file(
        tmp_path, monkeypatch):
    units, manifest = _output_contract(1_001)
    destination = tmp_path / "final"
    verified = []
    fsynced_directories = []
    fsynced_regular_files = 0
    original_verify = census._verify_staged_file
    original_fsync_directory = census._fsync_directory
    original_fsync = census.os.fsync

    def verify(path, expected):
        verified.append(path.name)
        return original_verify(path, expected)

    def fsync_directory(path):
        fsynced_directories.append(Path(path))
        return original_fsync_directory(path)

    def fsync(fd):
        nonlocal fsynced_regular_files
        if census.stat.S_ISREG(census.os.fstat(fd).st_mode):
            fsynced_regular_files += 1
        return original_fsync(fd)

    monkeypatch.setattr(census, "_verify_staged_file", verify)
    monkeypatch.setattr(census, "_fsync_directory", fsync_directory)
    monkeypatch.setattr(census.os, "fsync", fsync)
    result = census.write_outputs(
        destination, units, manifest, 500, lambda: None
    )
    assert [row["length"] for row in result["manifest"]["shards"]] == [500, 500, 1]
    assert verified == [
        "work-units-00000.jsonl", "work-units-00001.jsonl",
        "work-units-00002.jsonl", "manifest.json", "publication-receipt.json",
    ]
    assert destination.parent in fsynced_directories
    assert fsynced_directories[-1] == destination
    assert fsynced_regular_files == 5
    assert result["durability_confirmed"] is True
    assert destination.is_dir()
    assert not list(tmp_path.glob(".final.stage-*"))
    for descriptor in result["manifest"]["shards"]:
        data = (destination / descriptor["path"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == descriptor["sha256"]


def test_pbf_opl_inventory_extracts_canonical_forms_and_relation_members(
        tmp_path, monkeypatch):
    executable = tmp_path / "osmium"
    executable.write_text("tool")
    executable.chmod(0o700)
    tool = census.OsmiumTool(
        census._capture_file_hash(executable, role="osmium-executable"),
        "v1",
    )

    def stream_lines(command, label, consume, **_kwargs):
        for row in (
            b"n1 v1 Tamenity=parking x0 y0\n",
            b"w2 v1 Tamenity=parking Nn10,n11,n12,n10\n",
            b"r3 v1 Tamenity=parking,type=multipolygon Mw2@outer,n1@label\n",
            b"n9 v1 Tamenity=school x0 y0\n",
        ):
            consume(row)

    monkeypatch.setattr(census, "_stream_osmium_lines", stream_lines)
    inventory, memberships = census._pbf_inventory(
        tmp_path / "filtered.pbf", tool
    )
    assert inventory == {
        "node/1": {
            "source_type": "node", "closed_way": False,
            "declared_area": None,
        },
        "relation/3": {
            "source_type": "relation", "closed_way": False,
            "declared_area": None,
        },
        "way/2": {
            "source_type": "way", "closed_way": True,
            "declared_area": None,
        },
    }
    assert memberships == {"relation/3": ("node/1", "way/2")}


def test_pbf_inventory_reconciles_node_way_relation_forms_exactly():
    source = {
        "node/1": {"source_type": "node", "closed_way": False},
        "way/2": {"source_type": "way", "closed_way": True},
        "relation/3": {"source_type": "relation", "closed_way": False},
    }
    exported = {
        "node/1": {"geometry_types": ["Point"], "source_forms": ["n1"]},
        "way/2": {"geometry_types": ["Polygon"], "source_forms": ["a4"]},
        "relation/3": {
            "geometry_types": ["MultiPolygon"], "source_forms": ["a7"]
        },
    }
    result = census._reconcile_pbf_export(
        source, exported, enforce_national_floor=False
    )
    assert result["source_parking_identities"] == 3
    assert result["exported_parking_identities"] == 3
    assert result["source_forms"] == {"node": 1, "relation": 1, "way": 1}
    assert result["exact_identity_reconciliation"] is True


@pytest.mark.parametrize("exported, message", [
    ({"node/1": {"geometry_types": ["Point"], "source_forms": ["n1"]}},
     "missing"),
    ({
        "node/1": {"geometry_types": ["Point"], "source_forms": ["n1"]},
        "way/2": {"geometry_types": ["LineString"], "source_forms": ["w2"]},
        "relation/3": {"geometry_types": ["Point"], "source_forms": ["r3"]},
    }, "unsupported"),
])
def test_pbf_reconciliation_rejects_point_only_missing_and_unsupported_forms(
        exported, message):
    source = {
        "node/1": {"source_type": "node", "closed_way": False},
        "way/2": {"source_type": "way", "closed_way": True},
        "relation/3": {"source_type": "relation", "closed_way": False},
    }
    with pytest.raises(census.CensusError, match=message):
        census._reconcile_pbf_export(
            source, exported, enforce_national_floor=False
        )


def test_closed_false_values_require_lines_while_ordinary_closed_way_requires_polygon():
    source = {
        "way/30": {
            "source_type": "way", "closed_way": True,
            "declared_area": "no",
        },
        "way/31": {
            "source_type": "way", "closed_way": True,
            "declared_area": " FALSE ",
        },
        "way/32": {
            "source_type": "way", "closed_way": True,
            "declared_area": "0",
        },
        "way/33": {
            "source_type": "way", "closed_way": True,
            "declared_area": None,
        },
    }
    exported = {
        "way/30": {
            "geometry_types": ["LineString"], "source_forms": ["way/30"],
        },
        "way/31": {
            "geometry_types": ["LineString", "MultiPolygon"],
            "source_forms": ["way/31"],
        },
        "way/32": {
            "geometry_types": ["LineString", "MultiPolygon"],
            "source_forms": ["way/32"],
        },
        "way/33": {
            "geometry_types": ["MultiPolygon"], "source_forms": ["way/33"],
        },
    }
    result = census._reconcile_pbf_export(
        source, exported, enforce_national_floor=False
    )
    assert result["way_geometry_policy"] == {
        "open": "LineString-required",
        "closed_default": "Polygon-or-MultiPolygon-required",
        "closed_non_area": "LineString-required-area-copies-ignored-before-endpoint-association",
        "closed_non_area_values": ["0", "false", "no"],
    }
    normalized_source = copy.deepcopy(source)
    normalized_source["way/31"]["declared_area"] = "false"
    normalized_result = census._reconcile_pbf_export(
        normalized_source, exported, enforce_national_floor=False
    )
    assert normalized_result["identity_sha256"] == result["identity_sha256"]
    assert normalized_result["input_identity_sha256"] == (
        result["input_identity_sha256"]
    )
    for alias in ("way/30", "way/31", "way/32"):
        wrong = copy.deepcopy(exported)
        wrong[alias]["geometry_types"] = ["Polygon"]
        with pytest.raises(census.CensusError, match="under-answered"):
            census._reconcile_pbf_export(
                source, wrong, enforce_national_floor=False
            )
    ordinary_line = copy.deepcopy(exported)
    ordinary_line["way/33"]["geometry_types"] = ["LineString"]
    with pytest.raises(census.CensusError, match="under-answered"):
        census._reconcile_pbf_export(
            source, ordinary_line, enforce_national_floor=False
        )


def test_real_osmium_tiny_dynamic_pbf_forms_and_commands(tmp_path):
    osmium = shutil.which("osmium")
    if osmium is None:
        pytest.skip("real osmium is not installed")
    osmium_path = Path(osmium).resolve()
    tool = census._resolve_osmium(osmium_path)
    xml = tmp_path / "tiny.osm"
    timestamp = "2026-10-05T00:00:00Z"
    nodes = {
        1: (0.0000, 0.0000),
        2: (0.0010, 0.0000), 3: (0.0020, 0.0000),
        4: (0.0100, 0.0000), 5: (0.0110, 0.0000),
        6: (0.0110, 0.0010), 7: (0.0100, 0.0010),
        8: (0.0200, 0.0000), 9: (0.0210, 0.0000),
        10: (0.0210, 0.0010), 11: (0.0200, 0.0010),
        12: (0.0300, 0.0000), 13: (0.0310, 0.0000),
        14: (0.0310, 0.0010), 15: (0.0300, 0.0010),
        16: (0.0400, 0.0000), 17: (0.0410, 0.0000),
        18: (0.0410, 0.0010), 19: (0.0400, 0.0010),
        20: (0.0500, 0.0000), 21: (0.0510, 0.0000),
        22: (0.0510, 0.0010), 23: (0.0500, 0.0010),
    }
    node_rows = "".join(
        f'<node id="{identifier}" version="1" timestamp="{timestamp}" '
        f'lat="{latitude}" lon="{longitude}">'
        + ('<tag k="amenity" v="parking"/>' if identifier == 1 else '')
        + '</node>\n'
        for identifier, (longitude, latitude) in nodes.items()
    )
    xml.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<osm version="0.6" generator="trekdex-pytest">\n'
        + node_rows
        + f'<way id="10" version="1" timestamp="{timestamp}">'
          '<nd ref="2"/><nd ref="3"/>'
          '<tag k="amenity" v="parking"/></way>\n'
        + f'<way id="20" version="1" timestamp="{timestamp}">'
          '<nd ref="4"/><nd ref="5"/><nd ref="6"/><nd ref="7"/><nd ref="4"/>'
          '<tag k="amenity" v="parking"/><tag k="area" v="yes"/></way>\n'
        + f'<way id="30" version="1" timestamp="{timestamp}">'
          '<nd ref="8"/><nd ref="9"/><nd ref="10"/><nd ref="11"/><nd ref="8"/>'
          '<tag k="amenity" v="parking"/><tag k="area" v="no"/></way>\n'
        + f'<way id="31" version="1" timestamp="{timestamp}">'
          '<nd ref="16"/><nd ref="17"/><nd ref="18"/><nd ref="19"/><nd ref="16"/>'
          '<tag k="amenity" v="parking"/><tag k="area" v="false"/></way>\n'
        + f'<way id="32" version="1" timestamp="{timestamp}">'
          '<nd ref="20"/><nd ref="21"/><nd ref="22"/><nd ref="23"/><nd ref="20"/>'
          '<tag k="amenity" v="parking"/><tag k="area" v="0"/></way>\n'
        + f'<way id="41" version="1" timestamp="{timestamp}">'
          '<nd ref="12"/><nd ref="13"/><nd ref="14"/><nd ref="15"/><nd ref="12"/>'
          '</way>\n'
        + f'<relation id="40" version="1" timestamp="{timestamp}">'
          '<member type="way" ref="41" role="outer"/>'
          '<tag k="type" v="multipolygon"/>'
          '<tag k="amenity" v="parking"/></relation>\n'
        + '</osm>\n'
    )
    raw_source = tmp_path / "tiny-raw.osm.pbf"
    census._run_osmium(
        [str(osmium_path), "cat", str(xml), "-o", str(raw_source)],
        "test osmium XML to raw PBF",
    )
    source = tmp_path / "tiny.osm.pbf"
    census._run_osmium(
        [
            str(osmium_path), "cat", str(raw_source), "-o", str(source),
            "--output-header=osmosis_replication_sequence_number=1",
            f"--output-header=osmosis_replication_timestamp={timestamp}",
            f"--output-header=timestamp={timestamp}",
        ],
        "test osmium timestamped PBF rewrite",
    )
    source_inventory, source_memberships = census._pbf_inventory(
        source, tool, source_side=True
    )
    metadata = census._pbf_metadata(
        source, tool, require_replication=True
    )
    assert metadata["timestamp"] == timestamp
    assert metadata["replication_timestamp"] == timestamp
    assert metadata["sequence"] == "1"
    assert set(source_inventory) == {
        "node/1", "way/10", "way/20", "way/30", "way/31", "way/32",
        "relation/40",
    }
    assert source_inventory["way/20"]["declared_area"] == "yes"
    assert source_inventory["way/30"]["declared_area"] == "no"
    assert source_inventory["way/31"]["declared_area"] == "false"
    assert source_inventory["way/32"]["declared_area"] == "0"
    filtered = tmp_path / "parking.osm.pbf"
    census._run_osmium([
        str(osmium_path), "tags-filter", str(source),
        census.FILTER_EXPRESSION, "-o", str(filtered),
    ], "test osmium parking filter")
    filtered_inventory, filtered_memberships = census._pbf_inventory(
        filtered, tool
    )
    census._reconcile_filtered_inventory(
        source_inventory, source_memberships,
        filtered_inventory, filtered_memberships,
    )
    accumulator = census.FeatureAccumulator(
        census.EndpointGrid(()), source_memberships
    )
    census._stream_osmium_lines([
        str(osmium_path), "export", str(filtered),
        "-f", "geojsonseq",
        "--geometry-types=point,linestring,polygon",
        "--add-unique-id=type_id",
    ], "test osmium parking export", accumulator.consume_line)
    exported = accumulator.export_inventory()
    reconciliation = census._reconcile_pbf_export(
        source_inventory, exported, enforce_national_floor=False
    )
    assert reconciliation["exact_identity_reconciliation"] is True
    assert exported["node/1"]["geometry_types"] == ["Point"]
    assert exported["way/10"]["geometry_types"] == ["LineString"]
    assert exported["way/20"]["geometry_types"] == ["MultiPolygon"]
    assert exported["way/30"]["geometry_types"] == ["LineString"]
    assert exported["way/31"]["geometry_types"] == [
        "LineString", "MultiPolygon",
    ]
    assert exported["way/32"]["geometry_types"] == [
        "LineString", "MultiPolygon",
    ]
    assert exported["relation/40"]["geometry_types"] == ["MultiPolygon"]


def test_national_pbf_floor_is_exclusive_and_fixture_opt_out_is_explicit(
        monkeypatch):
    monkeypatch.setattr(census, "MIN_NATIONAL_PBF_PARKING_IDENTITIES", 1)
    source = {"node/1": {"source_type": "node", "closed_way": False}}
    exported = {
        "node/1": {"geometry_types": ["Point"], "source_forms": ["n1"]}
    }
    census._reconcile_pbf_export(
        source, exported, enforce_national_floor=False
    )
    with pytest.raises(census.CensusError, match="must exceed"):
        census._reconcile_pbf_export(
            source, exported, enforce_national_floor=True
        )
    source["node/2"] = {"source_type": "node", "closed_way": False}
    exported["node/2"] = {
        "geometry_types": ["Point"], "source_forms": ["n2"]
    }
    result = census._reconcile_pbf_export(
        source, exported, enforce_national_floor=True
    )
    assert result["source_parking_identities"] == 2
    assert result["national_floor_exclusive_minimum"] == 1


def test_stream_pbf_uses_native_filter_and_binds_osmium_identity(
        tmp_path, monkeypatch):
    source = tmp_path / "source.pbf"
    source.write_bytes(b"synthetic-pbf")
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir(mode=0o700)
    executable = tmp_path / "osmium"
    executable.write_bytes(b"synthetic-osmium")
    executable.chmod(0o700)
    tool = census.OsmiumTool(
        census._capture_file_hash(executable, role="osmium-executable"),
        "osmium version 1.2.3",
    )
    commands = []

    def run_osmium(command, label, **_kwargs):
        commands.append((list(command), label))
        if "tags-filter" in command:
            Path(command[-1]).write_bytes(b"filtered-pbf")
        return census.subprocess.CompletedProcess(command, 0, "ok\n", "")

    source_inventory = {
        "node/1": {"source_type": "node", "closed_way": False},
        "way/2": {"source_type": "way", "closed_way": True},
        "relation/3": {"source_type": "relation", "closed_way": False},
    }

    def stream_lines(command, label, consume, **_kwargs):
        commands.append((list(command), label))
        assert "export" in command
        polygon = {"type": "Polygon", "coordinates": [[
            [-0.0001, -0.0001], [0.0001, -0.0001],
            [0.0001, 0.0001], [-0.0001, 0.0001], [-0.0001, -0.0001],
        ]]}
        rows = (
            _geojson_feature(
                "n1", {"type": "Point", "coordinates": [0.0, 0.0]}
            ),
            _geojson_feature("a4", polygon),
            _geojson_feature("a7", polygon),
        )
        for row in rows:
            consume((json.dumps(row) + "\n").encode())

    monkeypatch.setattr(
        census, "_resolve_osmium", lambda *args, **kwargs: tool
    )
    monkeypatch.setattr(census, "_run_osmium", run_osmium)
    monkeypatch.setattr(
        census, "_pbf_metadata",
        lambda path, value, **kwargs: {
            "timestamp": "t", "replication_timestamp": "r",
            "sequence": "1", "replication_headers_required": False,
            "replication_headers_present": True,
        },
    )
    monkeypatch.setattr(
        census, "_pbf_inventory",
        lambda path, value, **kwargs: (
            source_inventory, {"relation/3": ("way/2",)}
        ),
    )
    monkeypatch.setattr(census, "_stream_osmium_lines", stream_lines)
    result = census._stream_pbf(
        source,
        census.EndpointGrid((census.Endpoint(0.0, 0.0),)),
        sidecar_aliases={"node/1", "node/999"},
        artifact_dir=artifact_dir,
        enforce_national_floor=False,
        minimum_artifact_free_bytes=0,
    )
    assert [feature.alias for feature in result.features] == [
        "node/1", "relation/3", "way/2"
    ]
    assert result.seen_authority_aliases == ("node/1",)
    assert result.counters["seen_authority_aliases"] == 1
    assert result.inventory["source_parking_identities"] == 3
    assert result.inventory["exact_identity_reconciliation"] is True
    assert result.osmium == {
        "version": "osmium version 1.2.3",
        "expected_sha256": None,
        "digest_authorized": False,
    }
    assert result.osmium_binding.sha256 == hashlib.sha256(
        b"synthetic-osmium"
    ).hexdigest()
    assert result.filtered_binding.sha256 == hashlib.sha256(
        b"filtered-pbf"
    ).hexdigest()
    installed = artifact_dir / (
        f"filtered-pbf-sha256-{result.filtered_binding.sha256}"
    )
    assert result.filtered_binding.path == installed / "parking.pbf"
    artifact_raw = (installed / "artifact-manifest.json").read_bytes()
    artifact = json.loads(artifact_raw)
    assert artifact_raw == census._canonical_bytes(artifact)
    artifact_without_self = dict(artifact)
    supplied_self = artifact_without_self.pop("self_sha256")
    assert supplied_self == census._canonical_hash(artifact_without_self)
    assert artifact["filter"] == {
        "expression": "nwr/amenity=parking",
        "filter_command": (
            "tags-filter INPUT nwr/amenity=parking -o OUTPUT"
        ),
    }
    assert "parking_geometry_policy" not in artifact["filter"]
    assert artifact["filter"] == census._filtered_pbf_policy()
    assert set(artifact["source_pbf"]) == {"sha256", "size_bytes"}
    assert "artifact_free_bytes_before" not in artifact["inventory"]
    assert str(tmp_path) not in json.dumps(artifact)
    assert not list(artifact_dir.glob(".parking-filter-stage-*"))
    tag_filter = next(command for command, _label in commands if "tags-filter" in command)
    assert tag_filter[0] == str(executable)
    assert "nwr/amenity=parking" in tag_filter
    export = next(command for command, _label in commands if "export" in command)
    assert export[0] == str(executable)
    assert "parking.pbf" in export[2]

    first_command_count = len(commands)
    first_filter_count = sum("tags-filter" in command for command, _ in commands)

    def forbid_stage(*_args, **_kwargs):
        pytest.fail("exact artifact reuse must not create a filter stage")

    monkeypatch.setattr(census.tempfile, "mkdtemp", forbid_stage)
    reused = census._stream_pbf(
        source,
        census.EndpointGrid((census.Endpoint(0.0, 0.0),)),
        sidecar_aliases={"node/1", "node/999"},
        artifact_dir=artifact_dir,
        enforce_national_floor=False,
        minimum_artifact_free_bytes=0,
    )
    assert reused.filtered_binding.sha256 == result.filtered_binding.sha256
    assert reused.seen_authority_aliases == ("node/1",)
    assert reused.filtered_manifest_binding.sha256 == (
        result.filtered_manifest_binding.sha256
    )
    assert sum("tags-filter" in command for command, _ in commands) == (
        first_filter_count
    )
    assert all(
        "tags-filter" not in command
        for command, _label in commands[first_command_count:]
    )
    assert not list(artifact_dir.glob(".parking-filter-stage-*"))


def test_pbf_source_mutation_during_native_filter_fails_closed(
        tmp_path, monkeypatch):
    source = tmp_path / "source.pbf"
    source.write_bytes(b"original-pbf")
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir(mode=0o700)
    executable = tmp_path / "osmium"
    executable.write_bytes(b"synthetic-osmium")
    executable.chmod(0o700)
    tool = census.OsmiumTool(
        census._capture_file_hash(executable, role="osmium-executable"),
        "osmium version 1.2.3",
    )

    def run_osmium(command, label, **_kwargs):
        if "tags-filter" in command:
            Path(command[-1]).write_bytes(b"filtered")
            source.write_bytes(b"mutated-pbf")
        return census.subprocess.CompletedProcess(command, 0, "ok\n", "")

    monkeypatch.setattr(
        census, "_resolve_osmium", lambda *args, **kwargs: tool
    )
    monkeypatch.setattr(census, "_run_osmium", run_osmium)
    monkeypatch.setattr(
        census, "_pbf_metadata",
        lambda path, value, **kwargs: {
            "timestamp": "t", "replication_timestamp": "r",
            "sequence": "1", "replication_headers_required": False,
            "replication_headers_present": True,
        },
    )
    monkeypatch.setattr(
        census, "_pbf_inventory", lambda *args, **kwargs: ({}, {})
    )
    with pytest.raises(census.CensusError, match="source changed after capture"):
        census._stream_pbf(
            source,
            census.EndpointGrid((census.Endpoint(0.0, 0.0),)),
            artifact_dir=artifact_dir,
            enforce_national_floor=False,
            minimum_artifact_free_bytes=0,
        )


def test_osmium_commands_fail_closed_on_error_empty_metadata_and_timeout(
        tmp_path, monkeypatch):
    executable = tmp_path / "osmium"
    executable.write_text("tool")
    executable.chmod(0o700)
    binding = census._capture_file_hash(executable, role="osmium-executable")
    tool = census.OsmiumTool(binding, "v1")
    source = tmp_path / "source.pbf"
    source.write_text("source")
    original_run_osmium = census._run_osmium

    def failed(_command, _label, **_kwargs):
        raise census.CensusError("metadata failed (7)")

    monkeypatch.setattr(census, "_run_osmium", failed)
    with pytest.raises(census.CensusError, match=r"failed \(7\)"):
        census._pbf_metadata(source, tool, require_replication=True)

    monkeypatch.setattr(
        census,
        "_run_osmium",
        lambda command, label, **_kwargs: census.subprocess.CompletedProcess(
            command, 0, "", ""
        ),
    )
    with pytest.raises(census.CensusError, match="returned no value"):
        census._pbf_metadata(source, tool, require_replication=True)

    monkeypatch.setattr(census, "_run_osmium", original_run_osmium)
    monkeypatch.setattr(census, "OSMIUM_TIMEOUT_S", 0.05)
    monkeypatch.setattr(census, "OSMIUM_TERM_GRACE_S", 0.05)
    with pytest.raises(census.CensusError, match="timed out.*terminated and reaped"):
        census._run_osmium(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            "version",
        )


def test_resolved_osmium_is_absolute_hashed_versioned_and_timed(
        tmp_path, monkeypatch):
    executable = tmp_path / "osmium"
    executable.write_bytes(b"binary")
    executable.chmod(0o700)
    seen = []

    def run(command, label, **kwargs):
        seen.append((command, label, kwargs))
        return census.subprocess.CompletedProcess(
            command, 0, "osmium 9.9\n", ""
        )

    expected = hashlib.sha256(b"binary").hexdigest()
    monkeypatch.setattr(census.shutil, "which", lambda name: str(executable))
    monkeypatch.setattr(census, "_run_osmium", run)
    tool = census._resolve_osmium(
        executable.resolve(),
        expected_sha256=expected,
        require_explicit_absolute=True,
        timeout_s=42.0,
    )
    assert tool.binding.path == executable.resolve()
    assert tool.binding.sha256 == expected
    assert tool.version == "osmium 9.9"
    assert seen[0][0][0] == str(executable.resolve())
    assert seen[0][2]["timeout_s"] == 42.0
    with pytest.raises(census.CensusError, match="must be absolute"):
        census._resolve_osmium(
            "osmium", expected_sha256=expected,
            require_explicit_absolute=True,
        )
    with pytest.raises(census.CensusError, match="SHA-256 mismatch"):
        census._resolve_osmium(
            executable.resolve(), expected_sha256="0" * 64,
            require_explicit_absolute=True,
        )


def test_streaming_osmium_child_is_terminated_and_reaped_on_consumer_error(
        monkeypatch):
    class Output:
        def __iter__(self):
            yield b"row\n"

        def close(self):
            pass

    class Process:
        def __init__(self):
            self.stdout = Output()
            self.returncode = None
            self.terminated = False
            self.waited = 0

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True
            self.returncode = -15

        def kill(self):
            self.returncode = -9

        def wait(self, timeout=None):
            self.waited += 1
            return self.returncode

    process = Process()
    def popen(*args, **kwargs):
        assert kwargs["start_new_session"] is True
        return process

    monkeypatch.setattr(census.subprocess, "Popen", popen)

    def reject(_line):
        raise census.CensusError("consumer rejected row")

    with pytest.raises(census.CensusError, match="consumer rejected"):
        census._stream_osmium_lines(["tool"], "stream", reject)
    assert process.terminated is True
    assert process.waited >= 1


def _parsed_geometry_binary_float_structure(
        geometry, *, canonicalize_signed_zero=False):
    def visit(value):
        if isinstance(value, list):
            return tuple(visit(item) for item in value)
        assert isinstance(value, (int, float)) and not isinstance(value, bool)
        number = float(value)
        if canonicalize_signed_zero and number == 0:
            number = 0.0
        return number.hex()

    return geometry["type"], visit(geometry["coordinates"])


def test_degenerate_ring_twice_area_threshold_is_exactly_pinned_above_and_below():
    threshold = census.DEGENERATE_RING_TWICE_SIGNED_AREA_EPSILON
    assert threshold == 1e-15
    below = math.nextafter(threshold, 0.0)
    above = math.nextafter(threshold, math.inf)

    def geometry(twice_area):
        return {"type": "Polygon", "coordinates": [[
            [0.0, 0.0], [1.0, 0.0], [0.0, twice_area], [0.0, 0.0],
        ]]}

    for twice_area in (below, threshold):
        candidate = geometry(twice_area)
        assert census._ring_twice_area(candidate["coordinates"][0]) == twice_area
        assert census._polygon_degeneracy_locations(candidate) == (
            ((0, 0),), (0,),
        )
    candidate = geometry(above)
    assert census._ring_twice_area(candidate["coordinates"][0]) == above
    assert census._polygon_degeneracy_locations(candidate) == ((), ())


def _modulo_first_relative_ring_twice_area(ring):
    anchor_longitude, anchor_latitude = ring[0]

    def local(point):
        return (
            (point[0] - anchor_longitude + 180.0) % 360.0 - 180.0,
            point[1] - anchor_latitude,
        )

    return abs(math.fsum(
        local(first)[0] * local(second)[1]
        - local(second)[0] * local(first)[1]
        for first, second in zip(ring, ring[1:])
    ))


def _head_global_ring_twice_area(ring):
    anchor_longitude = ring[0][0]
    points = [
        (census._unwrapped_longitude(point[0], anchor_longitude), point[1])
        for point in ring
    ]
    return abs(sum(
        first[0] * second[1] - second[0] * first[1]
        for first, second in zip(points, points[1:])
    ))


@pytest.mark.parametrize(
    (
        "anchor_longitude,anchor_latitude,width,nextafter_direction,"
        "expected_stable,expected_modulo,stable_is_degenerate"
    ),
    (
        (
            30.99996301615232,
            19.582394390998843,
            1.1974244593737686e-06,
            0.0,
            9.999999958627043e-16,
            1.0000000017966296e-15,
            True,
        ),
        (
            93.16895123800197,
            0.05021513057999982,
            3.0313282280813685e-06,
            math.inf,
            1.0000000038714703e-15,
            9.999999991834742e-16,
            False,
        ),
    ),
    ids=("modulo-false-admission", "modulo-false-rejection"),
)
def test_nonzero_anchor_direct_delta_pins_both_threshold_directions(
        anchor_longitude, anchor_latitude, width, nextafter_direction,
        expected_stable, expected_modulo, stable_is_degenerate):
    threshold = census.DEGENERATE_RING_TWICE_SIGNED_AREA_EPSILON
    longitude = anchor_longitude + width
    height = math.nextafter(
        threshold / (longitude - anchor_longitude), nextafter_direction
    )
    ring = [
        [anchor_longitude, anchor_latitude],
        [longitude, anchor_latitude],
        [anchor_longitude, anchor_latitude + height],
        [anchor_longitude, anchor_latitude],
    ]
    geometry = {"type": "Polygon", "coordinates": [ring]}
    stable = census._ring_twice_area(ring)
    modulo = _modulo_first_relative_ring_twice_area(ring)
    assert stable == expected_stable
    assert modulo == expected_modulo
    assert (stable <= threshold) is stable_is_degenerate
    assert (modulo <= threshold) is not stable_is_degenerate
    assert census._polygon_degeneracy_locations(geometry) == (
        (((0, 0),), (0,)) if stable_is_degenerate else ((), ())
    )
    shape, _explain_validity = census._require_shapely()
    assert shape(geometry).is_valid


def test_ring_delta_pins_dateline_wrap_and_positive_180_tie_policy():
    assert census._ring_longitude_delta(180.0, 0.0) == -180.0
    assert census._ring_longitude_delta(-180.0, 0.0) == -180.0
    assert census._ring_longitude_delta(180.0, 0.0) == (
        census._longitude_delta(0.0, 180.0)
    )
    anchor_longitude = 179.999
    other_longitude = -179.999
    anchor_latitude = 10.0
    other_latitude = 10.001
    ring = [
        [anchor_longitude, anchor_latitude],
        [other_longitude, anchor_latitude],
        [anchor_longitude, other_latitude],
        [anchor_longitude, anchor_latitude],
    ]
    expected = (
        (other_longitude - anchor_longitude + 360.0)
        * (other_latitude - anchor_latitude)
    )
    assert census._ring_twice_signed_area(ring) == expected


def test_candidate_degeneracy_reuses_staged_locations_and_has_dedicated_counters(
        monkeypatch):
    threshold = census.DEGENERATE_RING_TWICE_SIGNED_AREA_EPSILON
    geometry = {"type": "Polygon", "coordinates": [[
        [0.0, 0.0], [1.0, 0.0],
        [0.0, math.nextafter(threshold, 0.0)], [0.0, 0.0],
    ]]}
    real_detector = census._polygon_degeneracy_locations
    detector_calls = 0

    def track_detector(candidate):
        nonlocal detector_calls
        detector_calls += 1
        return real_detector(candidate)

    monkeypatch.setattr(census, "_polygon_degeneracy_locations", track_detector)
    accumulator = census.FeatureAccumulator(
        census.EndpointGrid((census.Endpoint(0.0, 0.0),)), {}
    )
    accumulator.consume_line((json.dumps(
        _geojson_feature("w998", geometry)
    ) + "\n").encode())
    counters = accumulator.final_counters()
    assert detector_calls == 1
    assert counters["staged_degenerate_polygon_records"] == 1
    assert counters["topology_rejected_degenerate_records"] == 1
    assert counters["topology_rejected_degenerate_rings"] == 1
    assert counters["topology_rejected_degenerate_outer_rings"] == 1
    assert counters["topology_rejected_degenerate_inner_rings"] == 0
    assert counters["topology_rejected_degenerate_components"] == 1
    assert counters["topology_rejected_shapely_records"] == 0
    assert counters["topology_validation_failures"] == 1
    assert counters["exact_endpoint_distance_checks"] == 0
    assert "near-zero signed-area ring" in counters[
        "rejection_contexts"
    ]["malformed_geometry"]
    assert "1e-15 square degrees" in counters[
        "rejection_contexts"
    ]["malformed_geometry"]
    expected_identity = census._canonical_bytes([
        "way/998", "w998", "Polygon", [[0, 0]], [0],
    ])
    assert counters["topology_rejected_degenerate_identity_sha256"] == (
        hashlib.sha256(expected_identity).hexdigest()
    )


def test_private_staged_degeneracy_cannot_be_suppressed_or_mismatched():
    geometry = {"type": "Polygon", "coordinates": [[
        [0.0, 0.0], [1.0, 0.0], [0.0, 0.0], [0.0, 0.0],
    ]]}
    staged = census._normalize_feature_structure(
        _geojson_feature("w997", geometry), {}
    )
    assert staged is not None
    with pytest.raises(
            census.CensusError, match="diagnostics do not match geometry"):
        census._validate_polygon_topology(
            staged.geometry,
            _staged_degeneracy=(),
        )
    with pytest.raises(
            census.CensusError, match="diagnostics do not match geometry"):
        census._validate_polygon_topology(
            copy.deepcopy(staged.geometry),
            _staged_degeneracy=staged._polygon_degeneracy,
        )
    with pytest.raises(census.FeatureRejection, match="near-zero signed-area"):
        census._validate_polygon_topology(staged.geometry)


def test_stable_measurement_rejects_head_noise_admitted_near_ring():
    ring = [
        [-97.7631455, 30.4675338],
        [-97.76314548999899, 30.4675338],
        [-97.7631455, 30.467533899989974],
        [-97.7631455, 30.4675338],
    ]
    threshold = census.DEGENERATE_RING_TWICE_SIGNED_AREA_EPSILON
    assert census._ring_twice_area(ring) == 9.999999777899136e-16
    assert census._ring_twice_area(ring) <= threshold
    assert _head_global_ring_twice_area(ring) == 4.547473508864641e-13
    assert _head_global_ring_twice_area(ring) > threshold
    shape, _explain_validity = census._require_shapely()
    assert shape({"type": "Polygon", "coordinates": [ring]}).is_valid

    accumulator = census.FeatureAccumulator(
        census.EndpointGrid((census.Endpoint(30.4675338, -97.7631455),)),
        {},
    )
    accumulator.consume_line((json.dumps(_geojson_feature(
        "w996", {"type": "Polygon", "coordinates": [ring]}
    )) + "\n").encode())
    counters = accumulator.final_counters()
    assert counters["topology_validation_failures"] == 1
    assert counters["topology_rejected_degenerate_records"] == 1
    assert counters["topology_rejected_shapely_records"] == 0
    assert counters["exact_endpoint_distance_checks"] == 0
    assert counters["rejections"] == {"malformed_geometry": 1}


def test_ring_centroid_uses_first_relative_fsum_and_stays_within_bounds():
    ring = [
        [-97.7631455, 30.4675338],
        [-97.76314547525068, 30.4675338],
        [-97.7631455, 30.467535566715654],
        [-97.7631455, 30.4675338],
    ]
    latitude, longitude, weight = census._ring_centroid(ring)
    assert weight == 4.372499676850837e-14
    assert latitude == pytest.approx(30.467534388905218, abs=1e-15)
    assert longitude == pytest.approx(-97.76314549175022, abs=1e-15)
    assert min(point[1] for point in ring) <= latitude <= max(
        point[1] for point in ring
    )
    assert min(point[0] for point in ring) <= longitude <= max(
        point[0] for point in ring
    )

    anchor = ring[0][0]
    points = [
        (census._unwrapped_longitude(point[0], anchor), point[1])
        for point in ring
    ]
    cross_sum = sum(
        first[0] * second[1] - second[0] * first[1]
        for first, second in zip(points, points[1:])
    )
    x_sum = sum(
        (first[0] + second[0])
        * (first[0] * second[1] - second[0] * first[1])
        for first, second in zip(points, points[1:])
    )
    y_sum = sum(
        (first[1] + second[1])
        * (first[0] * second[1] - second[0] * first[1])
        for first, second in zip(points, points[1:])
    )
    noisy_centroid = (
        y_sum / (3.0 * cross_sum), x_sum / (3.0 * cross_sum)
    )
    assert noisy_centroid == (19.335182189941406, -62.04204813639323)
    assert not (
        min(point[1] for point in ring) <= noisy_centroid[0]
        <= max(point[1] for point in ring)
    )


def test_ring_centroid_bounds_sanity_falls_back_to_vertex_mean():
    ring = [
        [0.4685659066798089, 1.5605542128102003],
        [-0.1740511208157618, 0.8456190372316783],
        [1.7479252135129313, 0.08223931076245661],
        [0.9353606836391113, 0.9121297309164911],
        [1.432746467861095, -1.8528302636349752],
        [0.4685659066798089, 1.5605542128102003],
    ]
    assert census._ring_twice_area(ring) == 0.7569993134802989
    latitude, longitude, weight = census._ring_centroid(ring)
    assert weight == 0.0
    assert latitude == ring[0][1] + math.fsum(
        point[1] - ring[0][1] for point in ring[:-1]
    ) / 5
    assert longitude == census._wrap_longitude(
        ring[0][0] + math.fsum(
            point[0] - ring[0][0] for point in ring[:-1]
        ) / 5
    )
    assert min(point[1] for point in ring) <= latitude <= max(
        point[1] for point in ring
    )
    assert min(point[0] for point in ring) <= longitude <= max(
        point[0] for point in ring
    )


@pytest.mark.parametrize("geometry", (
    {"type": "Point", "coordinates": [-0.0, 0.0]},
    {"type": "LineString", "coordinates": [[-0.0, 0.0], [1.0, -0.0]]},
    {"type": "Polygon", "coordinates": [[
        [-0.0, -0.0], [1.0, -0.0], [1.0, 1.0], [-0.0, 1.0],
        [0.0, 0.0],
    ]]},
    {"type": "MultiPolygon", "coordinates": [[[
        [-0.0, -0.0], [1.0, -0.0], [1.0, 1.0], [-0.0, 1.0],
        [0.0, 0.0],
    ]]]},
))
def test_every_exactly_2d_geojson_position_canonicalizes_signed_zero(geometry):
    row = _geojson_feature("r999", geometry)
    staged = census._normalize_feature_structure(row, {})
    assert staged is not None
    assert _parsed_geometry_binary_float_structure(staged.geometry) == (
        _parsed_geometry_binary_float_structure(
            geometry, canonicalize_signed_zero=True
        )
    )
    assert census._finite_number(-0.0, "value").hex() == "0x0.0p+0"
    longitude, latitude = census._coordinate([-0.0, -0.0], "position")
    assert longitude.hex() == "0x0.0p+0"
    assert latitude.hex() == "0x0.0p+0"
    if staged.geometry["type"] in ("Polygon", "MultiPolygon"):
        ring = next(census._iter_polygons(staged.geometry))[0]
        assert ring[0] == ring[-1] == [0.0, 0.0]
        assert census._canonical_bytes(ring[0]) == census._canonical_bytes(
            ring[-1]
        )


def test_signed_zero_canonicalization_preserves_trail_and_live_identity(
        tmp_path):
    assert census._trail_coordinate(
        [-0.0, -0.0], "trail position"
    ) == (0.0, 0.0)
    assert all(
        value.hex() == "0x0.0p+0"
        for value in census._trail_coordinate([-0.0, -0.0], "trail position")
    )

    negative_path = tmp_path / "negative-zero-live.json"
    positive_path = tmp_path / "positive-zero-live.json"
    negative_path.write_text("[[-0.0,-0.0],[1.0,1.0]]")
    positive_path.write_text("[[0.0,0.0],[1.0,1.0]]")
    negative = census._load_live_pool(negative_path)
    positive = census._load_live_pool(positive_path)
    assert negative.pins == positive.pins
    assert [
        (pin["lat"], pin["lon"]) for pin in negative.pins
    ] == [(0.0, 0.0), (1.0, 1.0)]
    base = census._canonical_hash({"lat": 0.0, "lon": 0.0})
    expected_live_id = "live-" + hashlib.sha256(
        f"{base}#0".encode()
    ).hexdigest()
    assert negative.pins[0] == {
        "lat": 0.0,
        "lon": 0.0,
        "live_id": expected_live_id,
    }


@pytest.mark.parametrize("geometry", (
    {"type": "Point", "coordinates": [0.0, 0.0, 10.0]},
    {"type": "LineString", "coordinates": [
        [0.0, 0.0], [1.0, 1.0, 10.0],
    ]},
    {"type": "Polygon", "coordinates": [[
        [0.0, 0.0], [1.0, 0.0, 10.0], [1.0, 1.0], [0.0, 0.0],
    ]]},
    {"type": "MultiPolygon", "coordinates": [[[
        [0.0, 0.0], [1.0, 0.0], [1.0, 1.0, 10.0], [0.0, 0.0],
    ]]]},
))
def test_geojson_positions_with_extra_ordinates_are_structurally_rejected(
        geometry):
    with pytest.raises(
            census.FeatureRejection, match="exactly two-dimensional"):
        census._normalize_feature_structure(
            _geojson_feature("r999", geometry), {}
        )


@pytest.mark.parametrize(
    (
        "source_id,alias,near_zero_ring_location,expected_twice_signed_area,"
        "far_endpoint,nearest_m"
    ),
    REAL_NEAR_ZERO_RELATION_CASES,
    ids=("relation-7095604-inner", "relation-16454647-outer"),
)
def test_real_near_zero_relation_preserves_parsed_coordinate_sequence_and_structure(
        source_id, alias, near_zero_ring_location, expected_twice_signed_area,
        far_endpoint, nearest_m):
    del far_endpoint, nearest_m
    row = {item["id"]: item for item in _real_near_zero_relation_rows()}[
        source_id
    ]
    original = copy.deepcopy(row)
    staged = census._normalize_feature_structure(row, {})
    assert staged is not None
    assert staged.alias == alias
    assert _parsed_geometry_binary_float_structure(staged.geometry) == (
        _parsed_geometry_binary_float_structure(row["geometry"])
    )
    assert staged.degenerate_ring_locations == ()
    assert staged.degenerate_component_indices == ()
    polygon_index, ring_index = near_zero_ring_location
    ring = staged.geometry["coordinates"][polygon_index][ring_index]
    assert len(ring) >= 4
    assert ring[0] == ring[-1]
    twice_signed_area = census._ring_twice_signed_area(ring)
    assert twice_signed_area == expected_twice_signed_area
    assert abs(twice_signed_area) > (
        census.DEGENERATE_RING_TWICE_SIGNED_AREA_EPSILON
    )
    assert row == original


def test_relation_16454647_lexeme_normalizes_without_parsed_geometry_drift():
    raw = MALFORMED_RELATIONS_FIXTURE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == (
        MALFORMED_RELATIONS_FIXTURE_SHA256
    )
    source_fragment = (
        b"[-97.7641664,30.467533800000003],[-97.7641607,30.4675369]"
    )
    staged_fragment = b"[-97.7641664,30.4675338],[-97.7641607,30.4675369]"
    assert raw.count(source_fragment) == 1

    row = next(
        item for item in _real_near_zero_relation_rows()
        if item["id"] == "a32909295"
    )
    staged = census._normalize_feature_structure(row, {})
    assert staged is not None
    assert staged.alias == "relation/16454647"
    assert staged.source_form == "a32909295"
    assert staged.degenerate_ring_locations == ()
    assert staged.degenerate_component_indices == ()
    assert _parsed_geometry_binary_float_structure(staged.geometry) == (
        _parsed_geometry_binary_float_structure(row["geometry"])
    )

    source_value = row["geometry"]["coordinates"][1][2][2][1]
    staged_value = staged.geometry["coordinates"][1][2][2][1]
    expected_binary_float = float("30.4675338").hex()
    assert float("30.467533800000003").hex() == expected_binary_float
    assert source_value.hex() == expected_binary_float
    assert staged_value.hex() == expected_binary_float

    canonical_staged_geometry = census._canonical_bytes(staged.geometry)
    assert source_fragment not in canonical_staged_geometry
    assert canonical_staged_geometry.count(staged_fragment) == 1


def test_real_near_zero_relations_are_far_topology_unvalidated_noncandidates_and_bind_run_id():
    rows = _real_near_zero_relation_rows()
    endpoints = tuple(case[4] for case in REAL_NEAR_ZERO_RELATION_CASES)
    for row, case in zip(rows, REAL_NEAR_ZERO_RELATION_CASES):
        endpoint = case[4]
        nearest_m = census.geometry_distance_to_point_m(
            row["geometry"], endpoint.latitude, endpoint.longitude
        )
        assert nearest_m == pytest.approx(case[5], abs=1e-6)
        assert nearest_m > census.FALLBACK_M

    sidecar_aliases = {case[1] for case in REAL_NEAR_ZERO_RELATION_CASES}
    result = census.stream_parking_source(
        MALFORMED_RELATIONS_FIXTURE,
        "geojsonseq",
        census.EndpointGrid(endpoints),
        sidecar_aliases=sidecar_aliases,
    )
    assert result.binding.sha256 == MALFORMED_RELATIONS_FIXTURE_SHA256
    assert result.features == ()
    assert result.seen_authority_aliases == tuple(sorted(sidecar_aliases))
    assert result.counters["records_total"] == 2
    assert result.counters["parking_features"] == 0
    assert result.counters["coarse_endpoint_candidates_total"] == 0
    assert result.counters["coarse_outside_fallback_envelope"] == 2
    assert result.counters["topology_not_required_outside"] == 2
    assert result.counters["topology_unvalidated_non_candidates"] == 2
    assert result.counters["topology_validation_calls"] == 0
    assert result.counters["topology_validation_failures"] == 0
    assert result.counters["exact_endpoint_distance_checks"] == 0
    assert result.counters["rejected_total"] == 0
    assert {
        key: result.counters[key]
        for key in (
            "staged_degenerate_polygon_records",
            "staged_degenerate_rings",
            "staged_degenerate_outer_rings",
            "staged_degenerate_inner_rings",
            "staged_degenerate_components",
            "topology_unvalidated_degenerate_records",
            "topology_unvalidated_degenerate_rings",
            "topology_unvalidated_degenerate_outer_rings",
            "topology_unvalidated_degenerate_inner_rings",
            "topology_unvalidated_degenerate_components",
        )
    } == {
        "staged_degenerate_polygon_records": 0,
        "staged_degenerate_rings": 0,
        "staged_degenerate_outer_rings": 0,
        "staged_degenerate_inner_rings": 0,
        "staged_degenerate_components": 0,
        "topology_unvalidated_degenerate_records": 0,
        "topology_unvalidated_degenerate_rings": 0,
        "topology_unvalidated_degenerate_outer_rings": 0,
        "topology_unvalidated_degenerate_inner_rings": 0,
        "topology_unvalidated_degenerate_components": 0,
    }
    assert result.counters["record_equation"] == {
        "records_total": 2, "classified_records": 2,
    }
    assert result.counters["parking_equation"] == {
        "parking_features": 0, "reconciled_parking_records": 0,
    }
    assert result.inventory == {
        "topology_unvalidated_non_candidates": {
            "status": census.TOPOLOGY_UNVALIDATED_NONCANDIDATE_STATUS,
            "records": 2,
            "record_identity_sha256": (
                "2b7b0618bde5c86c86126f9eaab97ca627796363c176119e2b0b9b564fa53301"
            ),
            "degenerate_records": 0,
            "degenerate_rings": 0,
            "degenerate_outer_rings": 0,
            "degenerate_inner_rings": 0,
            "degenerate_components": 0,
            "degenerate_identity_sha256": hashlib.sha256().hexdigest(),
        }
    }

    sources = copy.deepcopy(_output_contract(0)[1]["sources"])
    sources["parking"]["inventory"] = result.inventory
    algorithm = {
        "parking_capture_geometry_policy": census._parking_geometry_policy()
    }
    run_id = census._deterministic_run_id(sources, algorithm)
    changed_sources = copy.deepcopy(sources)
    changed_sources["parking"]["inventory"][
        "topology_unvalidated_non_candidates"
    ]["records"] += 1
    assert census._deterministic_run_id(changed_sources, algorithm) != run_id
    changed_algorithm = copy.deepcopy(algorithm)
    changed_algorithm["parking_capture_geometry_policy"][
        "degenerate_polygon_staging"
    ]["twice_signed_area_epsilon"] *= 2
    assert census._deterministic_run_id(
        sources, changed_algorithm
    ) != run_id


@pytest.mark.parametrize(
    (
        "source_id,alias,near_zero_ring_location,expected_twice_signed_area,"
        "far_endpoint,nearest_m"
    ),
    REAL_NEAR_ZERO_RELATION_CASES,
    ids=("relation-7095604-inner", "relation-16454647-outer"),
)
def test_real_near_zero_relations_are_shapely_valid_when_coarsely_near(
        source_id, alias, near_zero_ring_location, expected_twice_signed_area,
        far_endpoint, nearest_m):
    del expected_twice_signed_area, far_endpoint, nearest_m
    row = copy.deepcopy({
        item["id"]: item for item in _real_near_zero_relation_rows()
    }[source_id])
    original = copy.deepcopy(row)
    polygon_index, ring_index = near_zero_ring_location
    longitude, latitude = row["geometry"]["coordinates"][
        polygon_index
    ][ring_index][0]
    accumulator = census.FeatureAccumulator(
        census.EndpointGrid((census.Endpoint(latitude, longitude),)), {},
        {alias},
    )
    accumulator.consume_line((json.dumps(row) + "\n").encode())
    counters = accumulator.final_counters()
    assert tuple(accumulator.features) == (alias,)
    retained = accumulator.features[alias]
    assert _parsed_geometry_binary_float_structure(retained.geometry) == (
        _parsed_geometry_binary_float_structure(original["geometry"])
    )
    assert row == original
    assert retained.near_endpoint_ids == (0,)
    assert accumulator.export_inventory() == {
        alias: {
            "geometry_types": ["MultiPolygon"],
            "source_forms": [source_id],
        }
    }
    assert tuple(sorted(accumulator.seen_authority_aliases)) == (alias,)
    assert counters["parking_features"] == 1
    assert counters["staged_degenerate_polygon_records"] == 0
    assert counters["staged_degenerate_rings"] == 0
    assert counters["coarse_endpoint_candidates_total"] == 1
    assert counters["topology_unvalidated_non_candidates"] == 0
    assert counters["topology_validation_calls"] == 1
    assert counters["topology_validation_failures"] == 0
    assert counters["topology_rejected_degenerate_records"] == 0
    assert counters["topology_rejected_shapely_records"] == 0
    assert counters["exact_endpoint_distance_checks"] == 1
    assert counters["endpoint_associations_total"] == 1
    assert counters["retained_canonical_features"] == 1
    assert counters["rejections"] == {}


@pytest.mark.parametrize(
    "geometry,ring_locations,component_indices",
    (
        (
            {"type": "MultiPolygon", "coordinates": [
                [[[-0.001, 0.0], [0.0, 0.0], [0.001, 0.0],
                  [-0.001, 0.0]]],
                [[[50.0, 50.0], [50.01, 50.0], [50.01, 50.01],
                  [50.0, 50.01], [50.0, 50.0]]],
            ]},
            ((0, 0),),
            (0,),
        ),
        (
            {"type": "MultiPolygon", "coordinates": [[
                [[50.0, 50.0], [50.01, 50.0], [50.01, 50.01],
                 [50.0, 50.01], [50.0, 50.0]],
                [[-0.001, 0.0], [0.0, 0.0], [0.001, 0.0],
                 [-0.001, 0.0]],
            ]]},
            ((0, 1),),
            (),
        ),
    ),
    ids=("mixed-degenerate-outer-component", "isolated-degenerate-inner"),
)
def test_coarse_envelope_keeps_every_degenerate_ring_segment(
        geometry, ring_locations, component_indices):
    endpoint_grid = census.EndpointGrid((census.Endpoint(0.0, 0.0),))
    staged = census._normalize_feature_structure(
        _geojson_feature("r30", geometry), {}
    )
    assert staged is not None
    assert staged.geometry == geometry
    assert staged.degenerate_ring_locations == ring_locations
    assert staged.degenerate_component_indices == component_indices
    assert endpoint_grid.candidate_ids(
        staged.geometry, census.FALLBACK_M, _coordinates_validated=True
    ) == {0}

    accumulator = census.FeatureAccumulator(endpoint_grid, {})
    accumulator.consume_line((json.dumps(
        _geojson_feature("r30", geometry)
    ) + "\n").encode())
    counters = accumulator.final_counters()
    assert counters["coarse_endpoint_candidates_total"] == 1
    assert counters["topology_validation_calls"] == 1
    assert counters["topology_validation_failures"] == 1
    assert counters["topology_rejected_degenerate_records"] == 1
    assert counters["topology_rejected_degenerate_rings"] == len(
        ring_locations
    )
    assert counters["topology_rejected_degenerate_components"] == len(
        component_indices
    )
    assert counters["topology_rejected_shapely_records"] == 0
    assert counters["exact_endpoint_distance_checks"] == 0
    assert counters["topology_unvalidated_non_candidates"] == 0
    assert counters["rejections"] == {"malformed_geometry": 1}
    assert accumulator.features == {}


def test_coarse_envelope_rejection_skips_topology_at_scale(monkeypatch):
    topology_calls = 0

    def track_topology(_geometry):
        nonlocal topology_calls
        topology_calls += 1

    def forbid_exact_work(*_args, **_kwargs):
        pytest.fail("coarse-outside geometry reached exact geometry work")

    monkeypatch.setattr(census, "_validate_polygon_topology", track_topology)
    monkeypatch.setattr(census, "geometry_representative", forbid_exact_work)
    monkeypatch.setattr(
        census.EndpointGrid, "within_candidates", forbid_exact_work
    )
    accumulator = census.FeatureAccumulator(
        census.EndpointGrid((census.Endpoint(0.0, 0.0),)), {}
    )
    far_polygon = {"type": "Polygon", "coordinates": [[
        [50.0, 50.0], [50.001, 50.0], [50.001, 50.001],
        [50.0, 50.001], [50.0, 50.0],
    ]]}
    for identifier in range(1, 10_002):
        row = _geojson_feature(f"w{identifier}", far_polygon)
        accumulator.consume_line((json.dumps(row) + "\n").encode())
    counters = accumulator.final_counters()
    assert counters["parking_features"] == 0
    assert counters["coarse_outside_fallback_envelope"] == 10_001
    assert counters["topology_not_required_outside"] == 10_001
    assert counters["topology_unvalidated_non_candidates"] == 10_001
    assert counters["outside_fallback_envelope"] == 0
    assert counters["topology_validation_calls"] == 0
    assert counters["topology_validation_failures"] == 0
    assert topology_calls == 0
    assert counters["exact_endpoint_distance_checks"] == 0
    assert counters["exact_outside_fallback_envelope"] == 0
    assert counters["retained_canonical_features"] == 0
    assert counters["peak_endpoint_grid_retained_window_slots"] == 1
    assert counters["peak_endpoint_grid_retained_window_bytes"] == 512
    assert counters["staged_topology_observations"] == {
        "coarse_outside": 10_001,
        "topology_not_required_outside": 10_001,
        "topology_unvalidated_non_candidates": 10_001,
        "topology_validation_calls": 0,
        "topology_validation_failures": 0,
        "exact_outside": 0,
        "contract": (
            "branch-observation-counters-not-independent-closure-equations"
        ),
    }


def test_near_invalid_hole_blocks_before_exact_distance(tmp_path):
    geometry = {"type": "Polygon", "coordinates": [
        [[-0.01, -0.01], [0.01, -0.01], [0.01, 0.01],
         [-0.01, 0.01], [-0.01, -0.01]],
        [[0.02, 0.02], [0.021, 0.02], [0.021, 0.021],
         [0.02, 0.021], [0.02, 0.02]],
    ]}
    path = tmp_path / "near-invalid-hole.geojsonseq"
    path.write_text(json.dumps(_geojson_feature("w1", geometry)) + "\n")
    result = census.stream_parking_source(
        path,
        "geojsonseq",
        census.EndpointGrid((census.Endpoint(0.0, 0.0),)),
        sidecar_aliases={"way/1"},
    )
    assert result.features == ()
    assert result.seen_authority_aliases == ("way/1",)
    assert result.counters["parking_features"] == 0
    assert result.counters["topology_validation_calls"] == 1
    assert result.counters["topology_validation_failures"] == 1
    assert result.counters["topology_rejected_degenerate_records"] == 0
    assert result.counters["topology_rejected_shapely_records"] == 1
    assert result.counters["exact_endpoint_distance_checks"] == 0
    assert result.counters["rejections"] == {"malformed_geometry": 1}
    with pytest.raises(census.CensusError, match="complete census refused"):
        census._assert_stream_complete(result)


def test_far_topologically_invalid_polygon_is_nonblocking_outside(monkeypatch):
    topology_calls = 0

    def track_topology(_geometry):
        nonlocal topology_calls
        topology_calls += 1

    monkeypatch.setattr(census, "_validate_polygon_topology", track_topology)
    accumulator = census.FeatureAccumulator(
        census.EndpointGrid((census.Endpoint(0.0, 0.0),)), {}, {"way/2"}
    )
    geometry = {"type": "Polygon", "coordinates": [
        [[50.0, 50.0], [50.01, 50.0], [50.01, 50.01],
         [50.0, 50.01], [50.0, 50.0]],
        [[51.0, 51.0], [51.001, 51.0], [51.001, 51.001],
         [51.0, 51.001], [51.0, 51.0]],
    ]}
    accumulator.consume_line(
        (json.dumps(_geojson_feature("w2", geometry)) + "\n").encode()
    )
    counters = accumulator.final_counters()
    assert topology_calls == 0
    assert counters["parking_features"] == 0
    assert counters["topology_not_required_outside"] == 1
    assert counters["topology_unvalidated_non_candidates"] == 1
    assert counters["topology_validation_calls"] == 0
    assert counters["outside_fallback_envelope"] == 0
    assert counters["record_equation"] == {
        "records_total": 1, "classified_records": 1,
    }
    assert counters["rejected_total"] == 0
    assert tuple(sorted(accumulator.seen_authority_aliases)) == ("way/2",)
    assert accumulator.export_inventory() == {
        "way/2": {"geometry_types": ["Polygon"], "source_forms": ["w2"]}
    }
    assert accumulator.topology_unvalidated_non_candidate_inventory() == {
        "topology_unvalidated_non_candidates": {
            "status": census.TOPOLOGY_UNVALIDATED_NONCANDIDATE_STATUS,
            "records": 1,
            "record_identity_sha256": hashlib.sha256(
                census._canonical_bytes(["way/2", "w2", "Polygon"])
            ).hexdigest(),
            "degenerate_records": 0,
            "degenerate_rings": 0,
            "degenerate_outer_rings": 0,
            "degenerate_inner_rings": 0,
            "degenerate_components": 0,
            "degenerate_identity_sha256": hashlib.sha256().hexdigest(),
        }
    }


def test_asymmetric_bow_tie_is_nonblocking_far_but_fatal_near():
    def bow_tie(offset):
        return {"type": "Polygon", "coordinates": [[
            [offset, offset], [offset + 0.02, offset + 0.02],
            [offset, offset + 0.02], [offset + 0.03, offset],
            [offset, offset],
        ]]}

    far = census.FeatureAccumulator(
        census.EndpointGrid((census.Endpoint(0.0, 0.0),)), {}, {"way/21"}
    )
    far.consume_line((json.dumps(
        _geojson_feature("w21", bow_tie(50.0))
    ) + "\n").encode())
    far_counters = far.final_counters()
    assert far_counters["parking_features"] == 0
    assert far_counters["coarse_outside_fallback_envelope"] == 1
    assert far_counters["topology_not_required_outside"] == 1
    assert far_counters["topology_unvalidated_non_candidates"] == 1
    assert far_counters["topology_validation_calls"] == 0
    assert far_counters["outside_fallback_envelope"] == 0
    assert far_counters["rejected_total"] == 0
    assert far.export_inventory() == {
        "way/21": {"geometry_types": ["Polygon"], "source_forms": ["w21"]}
    }
    far_inventory = far.topology_unvalidated_non_candidate_inventory()
    assert far_inventory["topology_unvalidated_non_candidates"]["status"] == (
        "structurally-valid-topology-unvalidated-noncandidate"
    )
    assert far_inventory["topology_unvalidated_non_candidates"]["records"] == 1

    near = census.FeatureAccumulator(
        census.EndpointGrid((census.Endpoint(0.01, 0.01),)), {}, {"way/22"}
    )
    near.consume_line((json.dumps(
        _geojson_feature("w22", bow_tie(0.0))
    ) + "\n").encode())
    near_counters = near.final_counters()
    assert near_counters["parking_features"] == 0
    assert near_counters["topology_validation_calls"] == 1
    assert near_counters["topology_validation_failures"] == 1
    assert near_counters["topology_rejected_degenerate_records"] == 0
    assert near_counters["topology_rejected_shapely_records"] == 1
    assert near_counters["topology_unvalidated_non_candidates"] == 0
    assert near_counters["rejections"] == {"malformed_geometry": 1}
    assert "parking feature way/22" in near_counters[
        "rejection_contexts"
    ]["malformed_geometry"]
    with pytest.raises(census.CensusError, match="complete census refused"):
        census._assert_stream_complete(census.ParkingStreamResult(
            (), None, {}, near_counters, {}, (), {}
        ))


@pytest.mark.parametrize("offset", [0.0, 50.0], ids=("near", "far"))
def test_symmetric_zero_area_bow_tie_is_staged_then_topology_gated(offset):
    geometry = {"type": "Polygon", "coordinates": [[
        [offset, offset], [offset + 0.02, offset + 0.02],
        [offset, offset + 0.02], [offset + 0.02, offset],
        [offset, offset],
    ]]}
    accumulator = census.FeatureAccumulator(
        census.EndpointGrid((census.Endpoint(0.01, 0.01),)), {}
    )
    accumulator.consume_line((json.dumps(
        _geojson_feature("w23", geometry)
    ) + "\n").encode())
    counters = accumulator.final_counters()
    assert counters["parking_features"] == 0
    assert counters["staged_degenerate_polygon_records"] == 1
    assert counters["staged_degenerate_rings"] == 1
    assert counters["staged_degenerate_outer_rings"] == 1
    assert counters["staged_degenerate_inner_rings"] == 0
    assert counters["staged_degenerate_components"] == 1
    assert accumulator.export_inventory() == {
        "way/23": {"geometry_types": ["Polygon"], "source_forms": ["w23"]}
    }
    if offset == 0.0:
        assert counters["coarse_endpoint_candidates_total"] == 1
        assert counters["topology_validation_calls"] == 1
        assert counters["topology_validation_failures"] == 1
        assert counters["topology_rejected_degenerate_records"] == 1
        assert counters["topology_rejected_degenerate_rings"] == 1
        assert counters["topology_rejected_degenerate_components"] == 1
        assert counters["topology_rejected_shapely_records"] == 0
        assert counters["topology_unvalidated_non_candidates"] == 0
        assert counters["exact_endpoint_distance_checks"] == 0
        assert counters["rejections"] == {"malformed_geometry": 1}
    else:
        assert counters["coarse_endpoint_candidates_total"] == 0
        assert counters["topology_validation_calls"] == 0
        assert counters["topology_validation_failures"] == 0
        assert counters["topology_unvalidated_non_candidates"] == 1
        assert counters["topology_unvalidated_degenerate_records"] == 1
        assert counters["topology_rejected_degenerate_records"] == 0
        assert counters["topology_rejected_shapely_records"] == 0
        assert counters["rejected_total"] == 0


@pytest.mark.parametrize(
    "geometry",
    (
        {"type": "Polygon", "coordinates": [[
            [50.0, 50.0], [50.01, 50.0], [50.01, 50.01],
            [50.0, 50.01],
        ]]},
        {"type": "Polygon", "coordinates": [[
            [50.0, 50.0], [50.01, 50.0], [50.0, 50.0],
        ]]},
        {"type": "Polygon", "coordinates": [[
            [50.0, 50.0], ["NONFINITE", 50.0], [50.01, 50.01],
            [50.0, 50.01], [50.0, 50.0],
        ]]},
    ),
    ids=("unclosed", "too-few-points", "nonfinite"),
)
def test_far_structurally_malformed_polygon_remains_fatal_before_coarse(
        geometry):
    accumulator = census.FeatureAccumulator(
        census.EndpointGrid((census.Endpoint(0.0, 0.0),)), {}
    )
    raw = json.dumps(_geojson_feature("w3", geometry)).replace(
        '"NONFINITE"', "1e309"
    )
    accumulator.consume_line((raw + "\n").encode())
    counters = accumulator.final_counters()
    assert counters["parking_features"] == 0
    assert counters["staged_degenerate_polygon_records"] == 0
    assert counters["coarse_endpoint_candidates_total"] == 0
    assert counters["coarse_outside_fallback_envelope"] == 0
    assert counters["topology_validation_calls"] == 0
    assert counters["rejections"] == {"malformed_geometry": 1}
    assert accumulator.export_inventory() == {}
    with pytest.raises(census.CensusError, match="complete census refused"):
        census._assert_stream_complete(census.ParkingStreamResult(
            (), None, {}, counters, {}, (), {}
        ))


def test_dateline_high_latitude_near_polygon_reaches_topology(monkeypatch):
    real_validator = census._validate_polygon_topology
    topology_calls = 0

    def track_topology(geometry, **kwargs):
        nonlocal topology_calls
        topology_calls += 1
        real_validator(geometry, **kwargs)

    monkeypatch.setattr(census, "_validate_polygon_topology", track_topology)
    accumulator = census.FeatureAccumulator(
        census.EndpointGrid((census.Endpoint(85.0, 179.99),)), {}
    )
    polygon = {"type": "Polygon", "coordinates": [[
        [179.98, 84.99], [-179.98, 84.99], [-179.98, 85.01],
        [179.98, 85.01], [179.98, 84.99],
    ]]}
    accumulator.consume_line(
        (json.dumps(_geojson_feature("w4", polygon)) + "\n").encode()
    )
    counters = accumulator.final_counters()
    assert topology_calls == 1
    assert counters["topology_validation_calls"] == 1
    assert counters["coarse_endpoint_candidates_total"] == 1
    assert counters["topology_not_required_outside"] == 0
    assert counters["exact_endpoint_distance_checks"] == 1
    assert counters["retained_canonical_features"] == 1


def test_boundary_just_inside_coarse_mask_cannot_skip_topology(monkeypatch):
    real_validator = census._validate_polygon_topology
    topology_calls = 0

    def track_topology(geometry, **kwargs):
        nonlocal topology_calls
        topology_calls += 1
        real_validator(geometry, **kwargs)

    monkeypatch.setattr(census, "_validate_polygon_topology", track_topology)
    centre = math.degrees((census.FALLBACK_M - 1.0) / census.EARTH_RADIUS_M)
    half_width = 1e-7
    polygon = {"type": "Polygon", "coordinates": [[
        [centre - half_width, -half_width],
        [centre + half_width, -half_width],
        [centre + half_width, half_width],
        [centre - half_width, half_width],
        [centre - half_width, -half_width],
    ]]}
    accumulator = census.FeatureAccumulator(
        census.EndpointGrid((census.Endpoint(0.0, 0.0),)), {}
    )
    accumulator.consume_line(
        (json.dumps(_geojson_feature("w5", polygon)) + "\n").encode()
    )
    counters = accumulator.final_counters()
    assert topology_calls == 1
    assert counters["topology_validation_calls"] == 1
    assert counters["coarse_endpoint_candidates_total"] == 1
    assert counters["topology_not_required_outside"] == 0
    assert counters["exact_endpoint_distance_checks"] == 1
    assert counters["retained_canonical_features"] == 1


def test_parking_stream_progress_is_record_deterministic(monkeypatch, capsys):
    monkeypatch.setattr(census, "PARKING_STREAM_PROGRESS_EVERY", 2)
    accumulator = census.FeatureAccumulator(
        census.EndpointGrid((census.Endpoint(0.0, 0.0),)), {}
    )
    for identifier in (10, 11):
        accumulator.consume_line((json.dumps(_geojson_feature(
            f"n{identifier}",
            {"type": "Point", "coordinates": [50.0, 50.0]},
        )) + "\n").encode())
    assert capsys.readouterr().err == (
        "national_census: parking-stream progress records=2 parking=2 "
        "topology_calls=0 topology_failures=0 "
        "topology_rejected_degenerate_records=0 "
        "topology_rejected_shapely_records=0 coarse_outside=2 "
        "topology_noncandidates=0 exact_outside=0 retained=0\n"
    )


def test_endpoint_associations_are_exact_and_never_nearest_k_truncated():
    endpoints = tuple(census.Endpoint(0.0, 0.0) for _ in range(200))
    metrics = census.collections.Counter()
    feature = census.normalize_feature(
        _geojson_feature(
            "n1", {"type": "Point", "coordinates": [0.0, 0.0]}
        ),
        census.EndpointGrid(endpoints),
        {},
        metrics,
    )
    assert feature.near_endpoint_ids == tuple(range(200))
    assert metrics["max_endpoint_associations"] == 200
    assert metrics["endpoint_associations_total"] == 200


def test_endpoint_and_service_resource_limits_fail_closed_with_identity(
        monkeypatch):
    endpoints = tuple(census.Endpoint(0.0, 0.0) for _ in range(3))
    monkeypatch.setattr(census, "MAX_ENDPOINT_CANDIDATES_PER_FEATURE", 2)
    with pytest.raises(census.CensusError, match="parking feature node/1"):
        census.normalize_feature(
            _geojson_feature(
                "n1", {"type": "Point", "coordinates": [0.0, 0.0]}
            ),
            census.EndpointGrid(endpoints),
            {},
        )

    monkeypatch.setattr(census, "MAX_COMPONENTS_PER_ENDPOINT", 1)
    components = (
        _component(_point_feature("node/1")),
        _component(_point_feature("node/2", east_m=1)),
    )
    with pytest.raises(census.CensusError, match="endpoint 0"):
        census.service_projections(
            (census.Trail("fixture-co", "CO", "trail", None, (0,)),),
            (census.Endpoint(0.0, 0.0),),
            components,
        )


def test_service_association_counters_cover_all_candidates_without_truncation():
    components = tuple(
        _component(_point_feature(f"node/{index + 1}", east_m=index))
        for index in range(20)
    )
    selected, counters = census.service_projections(
        (census.Trail("fixture-co", "CO", "trail", None, (0,)),),
        (census.Endpoint(0.0, 0.0),),
        components,
    )
    associations = counters["associations"]
    assert len(selected) == census.NEAR_LIMIT
    assert associations["endpoint_component_associations"] == 20
    assert associations["max_components_per_endpoint"] == 20
    assert associations["max_trail_candidate_clusters"] == 20
    assert associations["exact_trail_distance_evaluations"] == 20
    assert associations["endpoint_associations_truncated"] == 0


def test_wide_geometry_names_itself_and_never_falls_back_to_all_pairs():
    geometry = {"type": "Polygon", "coordinates": [[
        [-100.0, -20.0], [100.0, -20.0], [100.0, 20.0],
        [-100.0, 20.0], [-100.0, -20.0],
    ]]}
    assert census.EndpointGrid(()).candidate_ids(
        geometry, census.FALLBACK_M
    ) == set()
    occupied_grid = census.EndpointGrid((
        census.Endpoint(0.0, 170.0),
        census.Endpoint(0.0, -170.0),
    ))
    diagnostics = {}
    assert occupied_grid.candidate_ids(
        geometry,
        census.FALLBACK_M,
        context="parking feature way/999",
        diagnostics=diagnostics,
    ) == {0, 1}
    assert diagnostics["plan"] == "merged-segments"
    assert diagnostics["segment_envelopes_planned"] < (
        census.MAX_ENDPOINT_GRID_SEGMENT_ENVELOPES
    )
    assert diagnostics["windows_scanned"] < (
        census.MAX_ENDPOINT_GRID_QUERY_WINDOWS
    )
    assert diagnostics["row_probes"] < census.MAX_ENDPOINT_GRID_ROW_PROBES
    assert diagnostics["grid_cell_probes"] < (
        census.MAX_ENDPOINT_GRID_CELL_PROBES
    )
    latitude, longitude = census.geometry_representative(geometry)
    feature = census.ParkingFeature(
        "way/999", "way/999", geometry, latitude, longitude,
        {"amenity": "parking"}, (0,),
    )
    component = _component(feature)
    index = census.ComponentPointIndex((component,), census.CLUSTER_M)
    assert len(index.active_levels) == 1
    assert index.active_levels[0] > 0
    assert index.candidates(latitude, longitude) == {0}
    assert index.max_geometry_cells <= census.TARGET_COMPONENT_INDEX_CELLS


def _unchanged_head_grid_candidates(grid, geometry, radius_m):
    minimum_latitude, maximum_latitude, start, span = (
        census.geometry_bounds(geometry)
    )
    latitude_pad = math.degrees(radius_m / census.EARTH_RADIUS_M)
    south = max(-90.0, minimum_latitude - latitude_pad)
    north = min(90.0, maximum_latitude + latitude_pad)
    maximum_abs = max(abs(south), abs(north))
    cosine = math.cos(math.radians(min(90.0, maximum_abs)))
    longitude_pad = (
        180.0 if cosine <= 1e-8
        else min(180.0, latitude_pad / cosine)
    )
    expanded_span = span + 2.0 * longitude_pad
    expanded_start = (start - longitude_pad) % 360.0
    first_latitude_cell = grid._latitude_cell(south)
    last_latitude_cell = grid._latitude_cell(north)
    first_longitude_cell = (
        int(math.floor(expanded_start / grid.cell)) - 1
    )
    longitude_cell_count = (
        grid.longitude_cells if expanded_span >= 360.0
        else int(math.ceil(expanded_span / grid.cell)) + 3
    )
    expected = set()
    for latitude_cell in range(
            first_latitude_cell, last_latitude_cell + 1):
        row = grid.rows.get(latitude_cell)
        if not row:
            continue
        if expanded_span >= 360.0:
            for endpoint_ids in row.values():
                expected.update(endpoint_ids)
        else:
            for offset in range(longitude_cell_count):
                expected.update(row.get(
                    (first_longitude_cell + offset)
                    % grid.longitude_cells,
                    (),
                ))
    query_cells = (
        last_latitude_cell - first_latitude_cell + 1
    ) * longitude_cell_count
    return expected, query_cells


def _grid_with_one_endpoint_per_head_query_cell(geometry, radius_m):
    template = census.EndpointGrid(())
    window = template._head_parity_query_window(
        census.geometry_bounds(geometry), radius_m
    )
    (
        first_latitude_cell,
        last_latitude_cell,
        first_longitude_cell,
        longitude_cell_count,
        query_cells,
    ) = window
    endpoints = tuple(
        census.Endpoint(
            -90.0 + (latitude_cell + 0.5) * template.cell,
            (
                ((longitude_cell + 0.5) * template.cell + 180.0)
                % 360.0
            ) - 180.0,
        )
        for latitude_cell in range(
            first_latitude_cell, last_latitude_cell + 1
        )
        for longitude_cell in (
            (first_longitude_cell + offset) % template.longitude_cells
            for offset in range(longitude_cell_count)
        )
    )
    return census.EndpointGrid(endpoints), query_cells


def test_reviewer_high_latitude_line_unions_literal_head_query_without_omission():
    geometry = {"type": "LineString", "coordinates": [
        [-83.61891773293743, 60.0],
        [-80.02182929758645, 60.0],
    ]}
    grid, query_cells = _grid_with_one_endpoint_per_head_query_cell(
        geometry, census.FALLBACK_M
    )
    expected, unchanged_head_query_cells = _unchanged_head_grid_candidates(
        grid, geometry, census.FALLBACK_M
    )
    assert query_cells == unchanged_head_query_cells == 264
    assert len(expected) == 264

    diagnostics = {}
    candidates = grid.candidate_ids(
        geometry, census.FALLBACK_M, diagnostics=diagnostics
    )
    assert expected.issubset(candidates)
    assert len(candidates) == 264
    assert diagnostics["head_parity_status"] == (
        "included-literal-HEAD-expanded-query"
    )
    assert diagnostics["head_parity_query_cells"] == 264
    assert diagnostics["head_parity_windows"] == 1
    assert diagnostics["head_parity_row_probes"] == 3
    assert diagnostics["head_parity_cell_probes"] == 3
    assert diagnostics["spherical_windows_scanned"] == 1
    assert diagnostics["spherical_row_probes"] == 3
    assert diagnostics["spherical_cell_probes"] == 261
    assert diagnostics["query_work_units"] == sum(
        diagnostics[name]
        for name in (
            "segment_envelopes_planned", "windows_scanned", "row_probes",
            "window_cell_probes", "occupied_cell_probes",
        )
    )


def test_reviewer_head_span_rounding_boundary_recovers_every_candidate():
    geometry = {"type": "LineString", "coordinates": [
        [-83.61891773293743, 0.0],
        [-82.31490143205083, 0.0],
    ]}
    head_bounds = census.geometry_bounds(geometry)
    assert head_bounds == (
        0.0, 0.0, 276.38108226706254, 1.3040163008866443,
    )
    assert census._head_parity_geometry_bounds(geometry) == head_bounds

    grid, query_cells = _grid_with_one_endpoint_per_head_query_cell(
        geometry, census.FALLBACK_M
    )
    window = grid._head_parity_query_window(
        head_bounds, census.FALLBACK_M
    )
    assert window[3:] == (35, 105)
    expected, unchanged_head_query_cells = _unchanged_head_grid_candidates(
        grid, geometry, census.FALLBACK_M
    )
    assert query_cells == unchanged_head_query_cells == 105
    assert expected == set(range(105))

    diagnostics = {}
    candidates = grid.candidate_ids(
        geometry, census.FALLBACK_M, diagnostics=diagnostics
    )
    assert candidates == expected
    assert {34, 69, 104}.issubset(candidates)
    assert diagnostics["head_parity_query_cells"] == 105
    assert diagnostics["head_parity_status"] == (
        "included-literal-HEAD-expanded-query"
    )


@pytest.mark.parametrize(
    "maximum_longitude,expected_longitude_cells,expected_query_cells",
    (
        (
            math.nextafter(-82.31490143205083, -math.inf),
            34,
            102,
        ),
        (
            math.nextafter(-82.31490143205083, math.inf),
            35,
            105,
        ),
    ),
    ids=("below-ceil-cell-boundary", "above-ceil-cell-boundary"),
)
def test_literal_head_span_matches_nextafter_ceil_cell_boundaries(
        maximum_longitude, expected_longitude_cells, expected_query_cells):
    geometry = {"type": "LineString", "coordinates": [
        [-83.61891773293743, 0.0],
        [maximum_longitude, 0.0],
    ]}
    head_bounds = census.geometry_bounds(geometry)
    assert census._head_parity_geometry_bounds(geometry) == head_bounds

    grid, query_cells = _grid_with_one_endpoint_per_head_query_cell(
        geometry, census.FALLBACK_M
    )
    window = grid._head_parity_query_window(
        head_bounds, census.FALLBACK_M
    )
    assert window[3:] == (
        expected_longitude_cells, expected_query_cells,
    )
    expected, unchanged_head_query_cells = _unchanged_head_grid_candidates(
        grid, geometry, census.FALLBACK_M
    )
    assert query_cells == unchanged_head_query_cells == expected_query_cells

    diagnostics = {}
    candidates = grid.candidate_ids(
        geometry, census.FALLBACK_M, diagnostics=diagnostics
    )
    assert candidates == expected == set(range(expected_query_cells))
    assert diagnostics["head_parity_query_cells"] == expected_query_cells


@pytest.mark.parametrize("coordinates", (
    [[-10.25, -2.0], [-9.75, 3.0], [-10.0, 0.0]],
    [[10.25, -2.0], [10.75, 3.0], [10.5, 0.0]],
    [[-0.25, -2.0], [0.25, 3.0], [0.0, 0.0]],
    [[179.90, -2.0], [179.99, 3.0], [179.95, 0.0]],
    [[-179.99, -2.0], [-179.90, 3.0], [-179.95, 0.0]],
), ids=(
    "negative-longitudes",
    "positive-longitudes",
    "negative-positive-longitudes",
    "east-of-dateline",
    "west-of-dateline",
))
def test_literal_head_bounds_match_head_for_eligible_nonwrapping_cases(
        coordinates):
    geometry = {"type": "LineString", "coordinates": coordinates}
    head_bounds = census.geometry_bounds(geometry)
    assert head_bounds[3] < 180.0
    assert census._head_parity_geometry_bounds(geometry) == head_bounds


def test_literal_head_equal_longitudes_keep_head_start_and_zero_span():
    geometry = {"type": "LineString", "coordinates": [
        [-83.5, -2.0], [-83.5, 3.0], [-83.5, 0.0],
    ]}
    expected = (-2.0, 3.0, 276.5, 0.0)
    assert census.geometry_bounds(geometry) == expected
    assert census._head_parity_geometry_bounds(geometry) == expected


def test_literal_head_expanded_query_wraps_dateline_cells():
    geometry = {"type": "LineString", "coordinates": [
        [179.96, 0.0], [179.99, 0.0],
    ]}
    grid, query_cells = _grid_with_one_endpoint_per_head_query_cell(
        geometry, census.FALLBACK_M
    )
    expected, unchanged_head_query_cells = _unchanged_head_grid_candidates(
        grid, geometry, census.FALLBACK_M
    )
    assert query_cells == unchanged_head_query_cells
    assert len(expected) == query_cells
    assert any(endpoint.longitude < -179.9 for endpoint in grid.endpoints)
    assert any(endpoint.longitude > 179.9 for endpoint in grid.endpoints)

    diagnostics = {}
    candidates = grid.candidate_ids(
        geometry, census.FALLBACK_M, diagnostics=diagnostics
    )
    assert expected.issubset(candidates)
    assert diagnostics["head_parity_status"] == (
        "included-literal-HEAD-expanded-query"
    )
    assert diagnostics["head_parity_query_cells"] == query_cells
    assert diagnostics["head_parity_windows"] == 1


def test_shipped_endpoint_grid_dense_local_controls_and_wide_limits():
    occupied_cells = 152_598
    occupied_rows = 535
    spacing = census.ENDPOINT_GRID_DEGREES * 1.01
    unique_endpoints = tuple(
        census.Endpoint(
            -30.0 + latitude_index * spacing,
            -30.0 + longitude_index * spacing,
        )
        for latitude_index in range(occupied_rows)
        for longitude_index in range(
            occupied_cells // occupied_rows
            + (latitude_index < occupied_cells % occupied_rows)
        )
    )
    endpoints = unique_endpoints + unique_endpoints[
        :172_830 - len(unique_endpoints)
    ]
    grid = census.EndpointGrid(endpoints)
    assert len(grid.endpoints) == 172_830
    assert grid.occupied_cell_count == occupied_cells
    assert len(grid.rows) == occupied_rows
    assert grid.occupied_cell_count > 50_000
    assert grid.occupied_cell_count < census.MAX_ENDPOINT_GRID_CELL_PROBES

    def dense_ring(vertex_count, latitude, longitude):
        radius = 0.0001
        points = [
            [
                longitude + radius * math.cos(
                    2.0 * math.pi * index / (vertex_count - 1)
                ),
                latitude + radius * math.sin(
                    2.0 * math.pi * index / (vertex_count - 1)
                ),
            ]
            for index in range(vertex_count - 1)
        ]
        return points + [points[0][:]]

    # Exact ids returned by unchanged HEAD candidate_ids for both controls.
    head_candidates = {
        62_756, 62_757, 62_758, 62_759, 62_760, 62_761,
        63_041, 63_042, 63_043, 63_044, 63_045, 63_046,
        63_326, 63_327, 63_328, 63_329, 63_330, 63_331,
    }
    small_dense_controls = (
        (
            5_000,
            4_999,
            {"type": "Polygon", "coordinates": [
                dense_ring(5_000, -20.0, -20.0)
            ]},
        ),
        (
            20_000,
            19_998,
            {"type": "MultiPolygon", "coordinates": [
                [dense_ring(10_000, -20.0, -20.0003)],
                [dense_ring(10_000, -20.0, -19.9997)],
            ]},
        ),
    )
    for vertex_count, segment_count, geometry in small_dense_controls:
        local_diagnostics = {}
        assert grid.candidate_ids(
            geometry,
            census.FALLBACK_M,
            limit=census.MAX_ENDPOINT_CANDIDATES_PER_FEATURE,
            diagnostics=local_diagnostics,
        ) == head_candidates
        assert local_diagnostics["plan"] == "local-cap"
        assert local_diagnostics["vertices_inspected"] == vertex_count
        assert sum(
            max(0, len(line) - 1)
            for line in census._iter_lines(geometry)
        ) == segment_count
        assert segment_count > 128
        assert local_diagnostics["segment_envelopes_planned"] == 0
        assert local_diagnostics["envelopes_generated"] == 1
        assert local_diagnostics["spherical_windows_scanned"] == 1
        assert local_diagnostics["head_parity_windows"] == 1
        assert local_diagnostics["windows_scanned"] == 2
        assert local_diagnostics["global_fallbacks"] == 0
        assert local_diagnostics["query_work_units"] == 26
        assert local_diagnostics["query_work_units"] == sum(
            local_diagnostics[name]
            for name in (
                "segment_envelopes_planned", "windows_scanned", "row_probes",
                "window_cell_probes", "occupied_cell_probes",
            )
        )

    def dense_box_ring(vertex_count, latitude, longitude):
        corners = (
            (longitude - 0.0001, latitude - 0.0001),
            (longitude + 0.0001, latitude - 0.0001),
            (longitude + 0.0001, latitude + 0.0001),
            (longitude - 0.0001, latitude + 0.0001),
        )
        side_size, extra = divmod(vertex_count - 1, len(corners))
        points = []
        for side, first in enumerate(corners):
            second = corners[(side + 1) % len(corners)]
            count = side_size + (side < extra)
            points.extend([
                [
                    first[0] + (second[0] - first[0]) * index / count,
                    first[1] + (second[1] - first[1]) * index / count,
                ]
                for index in range(count)
            ])
        return points + [points[0][:]]

    dense_specs = (
        (
            40_002,
            ((20_001, -20.0, -20.0003), (20_001, -20.0, -19.9997)),
        ),
        (100_000, ((100_000, -20.0, -20.0),)),
        (550_000, ((550_000, -20.0, -20.0),)),
    )
    for vertex_count, parts in dense_specs:
        rings = [
            dense_box_ring(part_vertices, latitude, longitude)
            for part_vertices, latitude, longitude in parts
        ]
        simple_rings = [
            dense_box_ring(5, latitude, longitude)
            for _part_vertices, latitude, longitude in parts
        ]
        if len(rings) == 1:
            geometry = {"type": "Polygon", "coordinates": [rings[0]]}
            simplified = {
                "type": "Polygon", "coordinates": [simple_rings[0]],
            }
        else:
            geometry = {
                "type": "MultiPolygon",
                "coordinates": [[ring] for ring in rings],
            }
            simplified = {
                "type": "MultiPolygon",
                "coordinates": [[ring] for ring in simple_rings],
            }
        census._validate_polygon_topology(geometry)
        raw = json.dumps(
            _geojson_feature(f"w{vertex_count}", geometry),
            separators=(",", ":"),
        ).encode()
        assert len(raw) <= census.MAX_STREAM_RECORD_BYTES
        if vertex_count == 550_000:
            assert len(raw) >= census.MAX_STREAM_RECORD_BYTES * 99 // 100
        expected = grid.candidate_ids(
            simplified,
            census.FALLBACK_M,
            limit=census.MAX_ENDPOINT_CANDIDATES_PER_FEATURE,
        )
        dense_diagnostics = {}
        assert grid.candidate_ids(
            geometry,
            census.FALLBACK_M,
            limit=census.MAX_ENDPOINT_CANDIDATES_PER_FEATURE,
            context=f"parking feature way/{vertex_count}",
            diagnostics=dense_diagnostics,
        ) == expected
        assert dense_diagnostics["plan"] == "local-cap"
        assert dense_diagnostics["vertices_inspected"] == vertex_count
        assert dense_diagnostics["segment_envelopes_planned"] == 0
        assert dense_diagnostics["envelopes_generated"] == 1
        assert dense_diagnostics["spherical_windows_scanned"] == 1
        assert dense_diagnostics["head_parity_windows"] == 1
        assert dense_diagnostics["windows_scanned"] == 2
        assert dense_diagnostics["global_fallbacks"] == 0
        assert dense_diagnostics["query_work_units"] == sum(
            dense_diagnostics[name]
            for name in (
                "segment_envelopes_planned", "windows_scanned", "row_probes",
                "window_cell_probes", "occupied_cell_probes",
            )
        )
        del geometry, simplified, rings, simple_rings, raw

    centers = tuple(-29.925 + 2.0 * index / 19.0 for index in range(20))
    wide_parts = []
    for center in centers:
        ring = [
            [
                center + 0.002 * math.cos(2.0 * math.pi * index / 20.0),
                center + 0.002 * math.sin(2.0 * math.pi * index / 20.0),
            ]
            for index in range(20)
        ]
        wide_parts.append([ring + [ring[0][:]]])
    wide_geometry = {
        "type": "MultiPolygon", "coordinates": wide_parts,
    }
    census._validate_polygon_topology(wide_geometry)
    assert centers[-1] - centers[0] == pytest.approx(2.0)
    assert sum(
        len(line) - 1 for line in census._iter_lines(wide_geometry)
    ) == 400
    wide_raw = json.dumps(
        _geojson_feature("r400", wide_geometry), separators=(",", ":")
    ).encode()
    assert len(wide_raw) < census.MAX_STREAM_RECORD_BYTES
    expected, head_query_cells = _unchanged_head_grid_candidates(
        grid, wide_geometry, census.FALLBACK_M
    )
    assert head_query_cells == 2_400
    assert len(expected) == 4_704
    wide_diagnostics = {}
    wide_candidates = grid.candidate_ids(
        wide_geometry,
        census.FALLBACK_M,
        limit=census.MAX_ENDPOINT_CANDIDATES_PER_FEATURE,
        context="parking feature relation/400",
        diagnostics=wide_diagnostics,
    )
    assert expected.issubset(wide_candidates)
    assert wide_diagnostics["plan"] == "merged-segments"
    assert wide_diagnostics["vertices_inspected"] == 420
    assert wide_diagnostics["segment_envelopes_planned"] == 400
    assert wide_diagnostics["envelopes_generated"] == 20
    assert wide_diagnostics["spherical_windows_scanned"] == 20
    assert wide_diagnostics["head_parity_windows"] == 1
    assert wide_diagnostics["windows_scanned"] == 21
    assert wide_diagnostics["head_parity_query_cells"] == 2_400
    assert wide_diagnostics["spherical_row_probes"] == 62
    assert wide_diagnostics["head_parity_row_probes"] == 48
    assert wide_diagnostics["spherical_cell_probes"] == 322
    assert wide_diagnostics["head_parity_cell_probes"] == 2_078
    assert wide_diagnostics["global_fallbacks"] == 0
    assert wide_diagnostics["grid_cell_probes"] == 2_400
    assert wide_diagnostics["query_work_units"] == 2_931


def test_line_segments_uses_iterator_and_charges_before_envelope_compute(
        monkeypatch):
    class IteratorOnlyCoordinates:
        def __init__(self, values):
            self.values = values

        def __iter__(self):
            return iter(self.values)

        def __len__(self):
            return len(self.values)

        def __getitem__(self, key):
            if isinstance(key, slice):
                pytest.fail("line coordinate sequence was sliced")
            pytest.fail("line coordinate sequence was indexed")

        def __copy__(self):
            pytest.fail("line coordinate sequence was copied")

        def __deepcopy__(self, _memo):
            pytest.fail("line coordinate sequence was deep-copied")

    coordinates = IteratorOnlyCoordinates((
        [0.0, 0.0], [2.0, 2.0], [3.0, 1.0],
    ))
    geometry = {"type": "LineString", "coordinates": coordinates}
    assert list(census._line_segments(geometry)) == [
        ((0.0, 0.0), (2.0, 2.0)),
        ((2.0, 2.0), (1.0, 3.0)),
    ]
    assert census.geometry_distance_to_point_m(
        geometry, 0.0, 0.0
    ) == pytest.approx(0.0)

    stats = {
        "grid_cell_probes": 0,
        "occupied_cell_probes": 0,
        "peak_retained_window_slots": 0,
        "query_work_units": 0,
        "retained_window_slots": 0,
        "row_probes": 0,
        "segment_envelopes_planned": 0,
        "window_cell_probes": 0,
        "windows_scanned": 0,
        "vertices_inspected": 0,
    }
    ledger = census._EndpointGridResourceLedger(
        "parking feature way/no-slice", stats
    )

    def forbid_envelope_compute(*_args, **_kwargs):
        pytest.fail("segment envelope computed before its charge")

    monkeypatch.setattr(census, "MAX_ENDPOINT_GRID_SEGMENT_ENVELOPES", 0)
    monkeypatch.setattr(
        census, "_segment_path_envelope", forbid_envelope_compute
    )
    with pytest.raises(
            census.CensusError,
            match="parking feature way/no-slice.*segment planning"):
        census._geometry_envelope_plan(
            geometry,
            ledger=ledger,
            context="parking feature way/no-slice",
        )
    assert stats["segment_envelopes_planned"] == 1
    assert stats["retained_window_slots"] == 0
    assert stats["windows_scanned"] == 0


def test_envelope_merge_refuses_retention_before_over_bound_append(
        monkeypatch):
    class IteratorOnlyEnvelopes:
        yielded = 0

        def __iter__(self):
            self.yielded += 1
            yield (10.0, 11.0, 359.0, 2.0)

        def __getitem__(self, _key):
            pytest.fail("envelope input was indexed or sliced")

        def __copy__(self):
            pytest.fail("envelope input was copied")

        def __deepcopy__(self, _memo):
            pytest.fail("envelope input was deep-copied")

    stats = {
        "grid_cell_probes": 0,
        "occupied_cell_probes": 0,
        "peak_retained_window_slots": 0,
        "query_work_units": 0,
        "retained_window_slots": 0,
        "row_probes": 0,
        "segment_envelopes_planned": 0,
        "window_cell_probes": 0,
        "windows_scanned": 0,
        "vertices_inspected": 0,
    }
    ledger = census._EndpointGridResourceLedger(
        "parking feature way/retention-bound", stats
    )
    envelopes = IteratorOnlyEnvelopes()
    monkeypatch.setattr(
        census, "MAX_ENDPOINT_GRID_RETAINED_WINDOW_SLOTS", 1
    )
    with pytest.raises(
            census.CensusError,
            match=(
                "parking feature way/retention-bound.*merge retention.*"
                "reserved before compute, append, or allocation"
            )):
        census._merge_geometry_envelopes(
            envelopes,
            ledger=ledger,
            context="parking feature way/retention-bound",
        )
    assert envelopes.yielded == 1
    assert stats["retained_window_slots"] == 1
    assert stats["peak_retained_window_slots"] == 1
    assert stats["retained_window_bytes"] == (
        census.ENDPOINT_GRID_RETAINED_WINDOW_SLOT_BYTES
    )
    assert stats["peak_retained_window_bytes"] == (
        census.ENDPOINT_GRID_RETAINED_WINDOW_SLOT_BYTES
    )
    assert stats["windows_scanned"] == 0
    assert stats["query_work_units"] == 0


def test_envelope_merge_refuses_conservative_bytes_before_second_allocation(
        monkeypatch):
    stats = {
        "grid_cell_probes": 0,
        "occupied_cell_probes": 0,
        "peak_retained_window_bytes": 0,
        "peak_retained_window_slots": 0,
        "query_work_units": 0,
        "retained_window_bytes": 0,
        "retained_window_slots": 0,
        "row_probes": 0,
        "segment_envelopes_planned": 0,
        "window_cell_probes": 0,
        "windows_scanned": 0,
        "vertices_inspected": 0,
    }
    ledger = census._EndpointGridResourceLedger(
        "parking feature way/byte-budget", stats
    )
    monkeypatch.setattr(
        census, "MAX_ENDPOINT_GRID_RETAINED_WINDOW_SLOTS", 100
    )
    monkeypatch.setattr(
        census, "MAX_ENDPOINT_GRID_RETAINED_WINDOW_BYTES",
        census.ENDPOINT_GRID_RETAINED_WINDOW_SLOT_BYTES,
    )
    with pytest.raises(
            census.CensusError,
            match="parking feature way/byte-budget.*512 reserved bytes"):
        census._merge_geometry_envelopes(iter((
            (10.0, 11.0, 359.0, 2.0),
        )), ledger=ledger, context="parking feature way/byte-budget")
    assert stats["retained_window_slots"] == 1
    assert stats["retained_window_bytes"] == 512
    assert stats["peak_retained_window_bytes"] == 512


def test_retention_slot_budget_dominates_actual_tuple_list_run_and_final_bytes():
    values = tuple(float(index) for index in range(4))
    appended_list = []
    appended_list.append(values)
    run_list = []
    run_list.append(appended_list)
    final_output = (values,)
    measured_worst_case = (
        sys.getsizeof(values)
        + sum(sys.getsizeof(value) for value in values)
        + sys.getsizeof(appended_list)
        + sys.getsizeof(run_list)
        + sys.getsizeof(final_output)
        + 8  # one pointer of conservative sort workspace
    )
    assert measured_worst_case <= (
        census.ENDPOINT_GRID_RETAINED_WINDOW_SLOT_BYTES
    )
    assert census.MAX_ENDPOINT_GRID_RETAINED_WINDOW_SLOTS == (
        census.MAX_ENDPOINT_GRID_RETAINED_WINDOW_BYTES
        // census.ENDPOINT_GRID_RETAINED_WINDOW_SLOT_BYTES
    )


def test_window_ledger_invariants_are_feature_context_census_errors():
    ledger = census._EndpointGridResourceLedger(
        "parking feature relation/ledger-invariant"
    )
    with pytest.raises(
            census.CensusError,
            match="relation/ledger-invariant.*negative.*-1"):
        ledger.reserve_window_slots(-1)
    with pytest.raises(
            census.CensusError,
            match="relation/ledger-invariant.*release 1.*0 retained"):
        ledger.release_window_slots(1)


def test_envelope_merge_keeps_group_latitudes_and_wraps_the_seam():
    merged = census._merge_geometry_envelopes(iter((
        (10.0, 11.0, 350.0, 15.0),
        (8.0, 12.0, 2.0, 10.0),
        (50.0, 60.0, 100.0, 10.0),
        (55.0, 65.0, 105.0, 10.0),
    )))
    assert merged == (
        (50.0, 65.0, 100.0, 15.0),
        (8.0, 12.0, 350.0, 22.0),
    )


def test_envelope_merge_preserves_latitudes_across_chunk_run_boundary():
    values = [(70.0, 71.0, 200.0, 1.0) for _ in range(5_000)]
    values[4_095] = (49.0, 52.0, 100.0, 2.0)
    values[4_096] = (51.0, 55.0, 101.0, 2.0)
    values[4_500] = (8.0, 10.0, 359.0, 1.5)
    values[4_700] = (9.0, 12.0, 0.25, 1.0)
    stats = {
        "grid_cell_probes": 0,
        "occupied_cell_probes": 0,
        "peak_retained_window_slots": 0,
        "query_work_units": 0,
        "retained_window_slots": 0,
        "row_probes": 0,
        "segment_envelopes_planned": 0,
        "window_cell_probes": 0,
        "windows_scanned": 0,
        "vertices_inspected": 0,
    }
    ledger = census._EndpointGridResourceLedger(
        "parking feature relation/chunk-boundary", stats
    )

    merged = census._merge_geometry_envelopes(
        iter(values),
        ledger=ledger,
        context="parking feature relation/chunk-boundary",
    )

    assert merged == (
        (49.0, 55.0, 100.0, 3.0),
        (70.0, 71.0, 200.0, 1.0),
        (8.0, 12.0, 359.0, 2.25),
    )
    assert stats["retained_window_slots"] == len(merged)
    assert len(merged) < stats["peak_retained_window_slots"] <= 2 * len(values)
    assert stats["retained_window_bytes"] == (
        len(merged) * census.ENDPOINT_GRID_RETAINED_WINDOW_SLOT_BYTES
    )
    assert stats["peak_retained_window_bytes"] == (
        stats["peak_retained_window_slots"]
        * census.ENDPOINT_GRID_RETAINED_WINDOW_SLOT_BYTES
    )
    assert stats["peak_retained_window_bytes"] < (
        census.MAX_ENDPOINT_GRID_RETAINED_WINDOW_BYTES
    )
    assert stats["windows_scanned"] == 0
    assert stats["query_work_units"] == 0


def test_national_shaped_head_parity_controls_stay_within_separate_budgets():
    occupied_cells = 152_598
    occupied_rows = 535
    spacing = census.ENDPOINT_GRID_DEGREES * 1.01
    unique_endpoints = tuple(
        census.Endpoint(
            -30.0 + latitude_index * spacing,
            -30.0 + longitude_index * spacing,
        )
        for latitude_index in range(occupied_rows)
        for longitude_index in range(
            occupied_cells // occupied_rows
            + (latitude_index < occupied_cells % occupied_rows)
        )
    )
    endpoints = unique_endpoints + unique_endpoints[
        :172_830 - len(unique_endpoints)
    ]
    grid = census.EndpointGrid(endpoints)
    assert (len(grid.endpoints), grid.occupied_cell_count, len(grid.rows)) == (
        172_830, 152_598, 535,
    )

    controls = []
    for label, longitude_span, latitude_span, head_cells in (
            ("15x15", 15.0, 15.0, 114_243),
            ("24x12", 12.0, 24.0, 146_601),
            ("27x15", 15.0, 27.0, 204_417)):
        start = -29.95
        geometry = {"type": "Polygon", "coordinates": [[
            [start, start], [start + longitude_span, start],
            [start + longitude_span, start + latitude_span],
            [start, start + latitude_span], [start, start],
        ]]}
        controls.append((label, geometry, head_cells, 4, 5))

    def multipart(parts, spread, start):
        half_size = 0.0005
        polygons = []
        for index in range(parts):
            center = (
                start + half_size
                + (spread - 2.0 * half_size) * index / (parts - 1)
            )
            polygons.append([[
                [center - half_size, center - half_size],
                [center + half_size, center - half_size],
                [center + half_size, center + half_size],
                [center - half_size, center + half_size],
                [center - half_size, center - half_size],
            ]])
        return {"type": "MultiPolygon", "coordinates": polygons}

    controls.extend((
        ("400-part-2-degree", multipart(400, 2.0, -29.95),
         2_350, 1_600, 2_000),
        ("120-part-6-degree", multipart(120, 6.0, -29.94),
         19_043, 480, 600),
    ))

    spherical_only_candidates = 0
    for label, geometry, expected_head_cells, segments, vertices in controls:
        expected, head_query_cells = _unchanged_head_grid_candidates(
            grid, geometry, census.FALLBACK_M
        )
        assert head_query_cells == expected_head_cells
        assert head_query_cells <= census.HEAD_ENDPOINT_GRID_QUERY_CELLS
        diagnostics = {}
        candidates = grid.candidate_ids(
            geometry,
            census.FALLBACK_M,
            context=f"parking feature relation/{label}",
            diagnostics=diagnostics,
        )
        assert expected.issubset(candidates)
        spherical_only_candidates += len(candidates - expected)
        assert diagnostics["plan"] == "merged-segments"
        assert diagnostics["head_parity_status"] == (
            "included-literal-HEAD-expanded-query"
        )
        assert diagnostics["head_parity_query_cells"] == head_query_cells
        assert diagnostics["segment_envelopes_planned"] == segments
        assert diagnostics["vertices_inspected"] == vertices
        assert diagnostics["head_parity_windows"] == 1
        assert diagnostics["spherical_windows_scanned"] <= (
            census.MAX_ENDPOINT_GRID_SPHERICAL_QUERY_WINDOWS
        )
        assert diagnostics["windows_scanned"] <= (
            census.MAX_ENDPOINT_GRID_QUERY_WINDOWS
        )
        assert diagnostics["head_parity_row_probes"] <= (
            census.MAX_ENDPOINT_GRID_HEAD_PARITY_ROW_PROBES
        )
        assert diagnostics["spherical_row_probes"] <= (
            census.MAX_ENDPOINT_GRID_SPHERICAL_ROW_PROBES
        )
        assert diagnostics["row_probes"] <= (
            census.MAX_ENDPOINT_GRID_ROW_PROBES
        )
        assert diagnostics["head_parity_cell_probes"] <= (
            census.MAX_ENDPOINT_GRID_HEAD_PARITY_CELL_PROBES
        )
        assert diagnostics["spherical_cell_probes"] <= (
            census.MAX_ENDPOINT_GRID_SPHERICAL_CELL_PROBES
        )
        assert diagnostics["grid_cell_probes"] <= (
            census.MAX_ENDPOINT_GRID_CELL_PROBES
        )
        assert diagnostics["query_work_units"] == sum(
            diagnostics[name]
            for name in (
                "segment_envelopes_planned", "windows_scanned", "row_probes",
                "window_cell_probes", "occupied_cell_probes",
            )
        )
    assert spherical_only_candidates > 0


@pytest.mark.parametrize("geometry,endpoints", [
    (
        {"type": "LineString", "coordinates": [
            [179.0, 52.0], [-177.0, 52.0],
        ]},
        (
            census.Endpoint(52.0, 180.0),
            census.Endpoint(0.0, 0.0),
        ),
    ),
    (
        {"type": "LineString", "coordinates": [
            [0.0, 0.0], [120.0, 0.0], [-120.0, 0.0],
        ]},
        (
            census.Endpoint(0.0, 60.0),
            census.Endpoint(0.0, 180.0),
            census.Endpoint(45.0, 45.0),
        ),
    ),
], ids=("aleutian-wrap", "zero-120-240"))
def test_wrapping_geometry_disables_false_head_parity_with_bounded_resources(
        geometry, endpoints):
    grid = census.EndpointGrid(endpoints)
    exact = {
        endpoint_id
        for endpoint_id, endpoint in enumerate(endpoints)
        if census.geometry_distance_to_point_m(
            geometry, endpoint.latitude, endpoint.longitude
        ) <= census.FALLBACK_M
    }
    diagnostics = {}
    candidates = grid.candidate_ids(
        geometry, census.FALLBACK_M, diagnostics=diagnostics
    )
    assert exact
    assert exact.issubset(candidates)
    assert diagnostics["plan"] == "merged-segments"
    assert diagnostics["head_parity_status"] == (
        "disabled-literal-HEAD-expanded-query-wrapping-longitudes"
    )
    assert diagnostics["head_parity_query_cells"] == 0
    assert diagnostics["head_parity_windows"] == 0
    assert diagnostics["head_parity_row_probes"] == 0
    assert diagnostics["head_parity_cell_probes"] == 0
    assert diagnostics["spherical_windows_scanned"] == (
        diagnostics["windows_scanned"]
    )
    assert diagnostics["spherical_row_probes"] == diagnostics["row_probes"]
    assert diagnostics["spherical_cell_probes"] == (
        diagnostics["grid_cell_probes"]
    )
    assert diagnostics["retained_window_slots"] == 0
    assert diagnostics["retained_window_bytes"] == 0
    assert diagnostics["peak_retained_window_bytes"] == (
        diagnostics["peak_retained_window_slots"]
        * census.ENDPOINT_GRID_RETAINED_WINDOW_SLOT_BYTES
    )
    assert 0 < diagnostics["peak_retained_window_bytes"] <= (
        census.MAX_ENDPOINT_GRID_RETAINED_WINDOW_BYTES
    )


def test_endpoint_grid_resource_ledgers_refuse_at_exact_independent_boundaries():
    def stats():
        return {
            "grid_cell_probes": 0,
            "occupied_cell_probes": 0,
            "peak_retained_window_slots": 0,
            "query_work_units": 0,
            "retained_window_slots": 0,
            "row_probes": 0,
            "segment_envelopes_planned": 0,
            "window_cell_probes": 0,
            "windows_scanned": 0,
            "vertices_inspected": 0,
        }

    segment_stats = stats()
    segment_ledger = census._EndpointGridResourceLedger(
        "parking feature way/segment-boundary", segment_stats
    )
    segment_ledger._charge(
        "segment_envelopes_planned",
        census.MAX_ENDPOINT_GRID_SEGMENT_ENVELOPES,
        census.MAX_ENDPOINT_GRID_SEGMENT_ENVELOPES,
        "spherical segment planning",
        "segment envelopes",
    )
    with pytest.raises(
            census.CensusError,
            match="parking feature way/segment-boundary.*segment planning"):
        segment_ledger.charge_segment_envelope()
    assert segment_stats["segment_envelopes_planned"] == (
        census.MAX_ENDPOINT_GRID_SEGMENT_ENVELOPES + 1
    )
    assert segment_stats["windows_scanned"] == 0

    for lane_name, total_name, limit, charge, kwargs, message in (
            (
                "spherical_windows_scanned", "windows_scanned",
                census.MAX_ENDPOINT_GRID_SPHERICAL_QUERY_WINDOWS,
                "charge_window", {}, "spherical window scanning",
            ),
            (
                "head_parity_windows", "windows_scanned",
                census.MAX_ENDPOINT_GRID_HEAD_PARITY_QUERY_WINDOWS,
                "charge_window", {"head_parity": True},
                "literal HEAD-parity window scanning",
            ),
            (
                "spherical_row_probes", "row_probes",
                census.MAX_ENDPOINT_GRID_SPHERICAL_ROW_PROBES,
                "charge_row", {}, "spherical row probing",
            ),
            (
                "head_parity_row_probes", "row_probes",
                census.MAX_ENDPOINT_GRID_HEAD_PARITY_ROW_PROBES,
                "charge_row", {"head_parity": True},
                "literal HEAD-parity row probing",
            )):
        boundary_stats = stats()
        boundary_stats[lane_name] = limit
        boundary_stats[total_name] = limit
        ledger = census._EndpointGridResourceLedger(
            f"parking feature way/{lane_name}", boundary_stats
        )
        with pytest.raises(census.CensusError, match=message):
            getattr(ledger, charge)(**kwargs)
        assert boundary_stats[lane_name] == limit + 1
        assert boundary_stats[total_name] == limit + 1

    for lane_name, limit, head_parity in (
            (
                "spherical_cell_probes",
                census.MAX_ENDPOINT_GRID_SPHERICAL_CELL_PROBES,
                False,
            ),
            (
                "head_parity_cell_probes",
                census.MAX_ENDPOINT_GRID_HEAD_PARITY_CELL_PROBES,
                True,
            )):
        cell_stats = stats()
        cell_stats[lane_name] = limit - 1
        cell_stats["grid_cell_probes"] = limit - 1
        cell_ledger = census._EndpointGridResourceLedger(
            f"parking feature way/{lane_name}", cell_stats
        )
        cell_ledger.charge_cell(
            "window_cell_probes", head_parity=head_parity
        )
        assert cell_stats[lane_name] == limit
        assert cell_stats["grid_cell_probes"] == limit
        with pytest.raises(
                census.CensusError,
                match=f"parking feature way/{lane_name}.*cell probing"):
            cell_ledger.charge_cell(
                "window_cell_probes", head_parity=head_parity
            )
        assert cell_stats[lane_name] == limit + 1
        assert cell_stats["grid_cell_probes"] == limit + 1


def test_empty_vertex_geometry_fails_with_typed_feature_context():
    geometry = {"type": "LineString", "coordinates": []}
    for endpoints in ((), (census.Endpoint(0.0, 0.0),)):
        diagnostics = {"stale": 1}
        with pytest.raises(
                census.CensusError,
                match="parking feature way/empty has no vertices"):
            census.EndpointGrid(endpoints).candidate_ids(
                geometry,
                census.FALLBACK_M,
                context="parking feature way/empty",
                diagnostics=diagnostics,
            )
        assert "stale" not in diagnostics
        assert diagnostics["plan"] == "none"
        assert diagnostics["candidate_ids"] == 0
        assert diagnostics["query_work_units"] == 0


@pytest.mark.parametrize("geometry", [
    {"type": "Polygon", "coordinates": [[]]},
    {"type": "Polygon", "coordinates": [[[0.0, 0.0]]]},
    {"type": "Polygon", "coordinates": [[
        [0.0, 0.0], [0.1, 0.0],
    ]]},
    {"type": "MultiPolygon", "coordinates": [[[
        [0.0, 0.0], [0.1, 0.0], [0.0, 0.0],
    ]]]},
])
def test_subminimal_public_surface_ring_has_typed_feature_context(geometry):
    for endpoints in ((), (census.Endpoint(0.0, 0.0),)):
        diagnostics = {"stale": 1}
        with pytest.raises(
                census.CensusError,
                match=(
                    "parking feature way/subminimal polygon .* ring .* "
                    "has fewer than 4 vertices"
                )):
            census.EndpointGrid(endpoints).candidate_ids(
                geometry,
                census.FALLBACK_M,
                context="parking feature way/subminimal",
                diagnostics=diagnostics,
            )
        assert "stale" not in diagnostics
        assert diagnostics["plan"] == "none"
        assert diagnostics["candidate_ids"] == 0


@pytest.mark.parametrize("geometry", [
    {"type": "Point", "coordinates": [0.0, 95.0]},
    {"type": "LineString", "coordinates": [[0.0, 0.0], [400.0, 0.0]]},
    {"type": "Polygon", "coordinates": [[
        [0.0, 0.0], [1.0, 0.0], [1.0, -91.0], [0.0, 0.0],
    ]]},
    {"type": "MultiPolygon", "coordinates": [[[
        [0.0, 0.0], [181.0, 0.0], [1.0, 1.0], [0.0, 0.0],
    ]]]},
])
def test_public_endpoint_grid_range_errors_are_typed_and_clear_diagnostics(
        geometry):
    diagnostics = {"stale": 1}
    with pytest.raises(
            census.CensusError,
            match=(
                "parking feature way/out-of-range.*outside geographic "
                "coordinate range"
            )):
        census.EndpointGrid((census.Endpoint(0.0, 0.0),)).candidate_ids(
            geometry,
            census.FALLBACK_M,
            context="parking feature way/out-of-range",
            diagnostics=diagnostics,
        )
    assert "stale" not in diagnostics
    assert diagnostics["plan"] == "none"
    assert diagnostics["candidate_ids"] == 0
    assert diagnostics["query_work_units"] == 0


def test_endpoint_grid_query_charges_rows_and_cells_once_per_window():
    def box(center):
        return [[
            [center - 0.01, -0.01], [center + 0.01, -0.01],
            [center + 0.01, 0.01], [center - 0.01, 0.01],
            [center - 0.01, -0.01],
        ]]

    geometry = {
        "type": "MultiPolygon",
        "coordinates": [box(-2.0), box(0.0), box(2.0)],
    }
    diagnostics = {}
    assert census.EndpointGrid((
        census.Endpoint(0.0, -2.0), census.Endpoint(0.0, 0.0),
    )).candidate_ids(
        geometry, census.FALLBACK_M, diagnostics=diagnostics
    ) == {0, 1}
    assert diagnostics["plan"] == "merged-segments"
    assert diagnostics["vertices_inspected"] == 15
    assert diagnostics["segment_envelopes_planned"] == 12
    assert diagnostics["envelopes_generated"] == 3
    assert diagnostics["spherical_windows_scanned"] == 3
    assert diagnostics["head_parity_windows"] == 1
    assert diagnostics["windows_scanned"] == 4
    assert diagnostics["head_parity_query_cells"] == 285
    assert diagnostics["head_parity_status"] == (
        "included-literal-HEAD-expanded-query"
    )
    assert diagnostics["spherical_row_probes"] == 3
    assert diagnostics["head_parity_row_probes"] == 1
    assert diagnostics["row_probes"] == 4
    assert diagnostics["occupied_cell_probes"] == 2
    assert diagnostics["window_cell_probes"] == 0
    assert diagnostics["spherical_cell_probes"] == 2
    assert diagnostics["head_parity_cell_probes"] == 0
    assert diagnostics["grid_cell_probes"] == 2
    assert diagnostics["query_work_units"] == 22


def test_common_local_polygon_uses_one_window_without_vertex_haversines(
        monkeypatch):
    geometry = {"type": "Polygon", "coordinates": [[
        [-0.001, -0.001], [0.001, -0.001], [0.001, 0.001],
        [-0.001, 0.001], [-0.001, -0.001],
    ]]}
    grid = census.EndpointGrid((
        census.Endpoint(0.0, 0.0), census.Endpoint(50.0, 50.0),
    ))

    def forbid_haversine(*_args, **_kwargs):
        pytest.fail("local envelope planning called per-vertex haversine")

    monkeypatch.setattr(census, "haversine_m", forbid_haversine)
    diagnostics = {}
    assert grid.candidate_ids(
        geometry, census.FALLBACK_M, diagnostics=diagnostics
    ) == {0}
    assert diagnostics["plan"] == "local-cap"
    assert diagnostics["vertices_inspected"] == 5
    assert diagnostics["envelopes_generated"] == 1
    assert diagnostics["spherical_windows_scanned"] == 1
    assert diagnostics["head_parity_windows"] == 1
    assert diagnostics["windows_scanned"] == 2
    assert diagnostics["segment_envelopes_planned"] == 0


def test_spatial_pair_overflow_fails_closed(monkeypatch):
    monkeypatch.setattr(census, "MAX_SPATIAL_PAIRS", 0)
    components = (
        _component(_point_feature("node/1")),
        _component(_point_feature("node/2", east_m=1)),
    )
    with pytest.raises(census.CensusError, match="pair resource limit"):
        census.proximity_review_groups(components)


def test_duplicate_exports_fail_closed_on_tag_or_geometry_conflict():
    first = _point_feature("node/1")
    with pytest.raises(census.CensusError, match="conflicting export tags"):
        census._merge_duplicate_feature(
            first, _point_feature("node/1", tags={"name": "different"})
        )
    with pytest.raises(census.CensusError, match="conflicting export geometry"):
        census._merge_duplicate_feature(
            first, _point_feature("node/1", east_m=1)
        )


def test_external_provenance_labels_are_content_derived_and_collision_free(tmp_path):
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    first = first_dir / "same.json"
    second = second_dir / "same.json"
    first.write_text("first")
    second.write_text("second")
    first_binding = census._capture_file_hash(first, role="test-input")
    second_binding = census._capture_file_hash(second, role="test-input")
    assert first_binding.logical_path != second_binding.logical_path
    assert first_binding.logical_path.startswith("external/test-input-sha256-")
    assert second_binding.logical_path.startswith("external/test-input-sha256-")
    assert str(tmp_path) not in first_binding.logical_path
    assert str(tmp_path) not in second_binding.logical_path


def test_repo_provenance_stays_repo_relative():
    binding = census._capture_file_hash(
        HERE / "_parking_verdicts.py", role="test-input"
    )
    assert binding.logical_path == "scripts/_parking_verdicts.py"
    assert not Path(binding.logical_path).is_absolute()


def test_run_id_ignores_paths_and_timestamps_but_binds_authority_and_policy():
    units, manifest = _output_contract(0)
    del units
    sources = copy.deepcopy(manifest["sources"])
    algorithm = {"policy": 1}
    first = census._deterministic_run_id(sources, algorithm)
    moved = copy.deepcopy(sources)
    moved["bundle"]["path"] = "external/a-different-label"
    moved["parking"]["osm_metadata"] = {"timestamp": "later"}
    assert census._deterministic_run_id(moved, algorithm) == first
    changed = copy.deepcopy(sources)
    changed["parking"]["sha256"] = "different"
    assert census._deterministic_run_id(changed, algorithm) != first
    baseline_bound = copy.deepcopy(sources)
    baseline_bound["production_baseline"] = {
        "sha256": "f" * 64, "self_sha256": "e" * 64,
    }
    with_baseline = census._deterministic_run_id(baseline_bound, algorithm)
    assert with_baseline != first
    baseline_bound["production_baseline"]["self_sha256"] = "d" * 64
    assert census._deterministic_run_id(baseline_bound, algorithm) != with_baseline
    inventory_bound = copy.deepcopy(sources)
    inventory_bound["parking"]["inventory"] = {"input_identity_sha256": "a" * 64}
    assert census._deterministic_run_id(inventory_bound, algorithm) != first
    seen_alias_bound = copy.deepcopy(sources)
    seen_alias_bound["parking"]["seen_authority_aliases"] = {
        "count": 1, "sha256": "b" * 64,
    }
    assert census._deterministic_run_id(seen_alias_bound, algorithm) != first
    manifest_migration = copy.deepcopy(sources)
    manifest_migration["parking"]["filtered_artifact_manifest"] = {
        "path": "external/filtered-manifest",
        "sha256": "c" * 64,
        "size_bytes": 1,
    }
    before_migration = census._deterministic_run_id(
        manifest_migration, algorithm
    )
    manifest_migration["parking"]["filtered_artifact_manifest"][
        "sha256"
    ] = "d" * 64
    assert census._deterministic_run_id(
        manifest_migration, algorithm
    ) == before_migration
    assert census._deterministic_run_id(
        sources, {"parking_filtered_pbf_byte_policy": {"expression": "changed"}}
    ) != first
    assert census._deterministic_run_id(sources, {"policy": 2}) != first


def test_current_9074_geometry_corpus_capture_is_exact_and_repeatable():
    bundle = HERE.parent / "ios" / "SouthMountainExplorer" / "Resources" / "areas-index.json"
    geom_dir = HERE.parent / "public" / "areas" / "geom"
    first = census.capture_scope(bundle, geom_dir)
    second = census.capture_scope(bundle, geom_dir)
    assert len(first.areas) == 9_074
    assert len(first.jurisdictions) == 51
    assert len(first.trails) == 92_442
    assert first.endpoint_occurrences == 245_026
    assert len(first.endpoints) == 172_830
    assert first.trails_without_source_id == 5
    assert len(first.anonymous_trail_refs) == 5
    assert len(set(first.anonymous_trail_refs)) == 5
    assert all(value.startswith("atr1:") for value in first.anonymous_trail_refs)
    assert first.anonymous_trail_refs == second.anonymous_trail_refs
    assert len({trail.key for trail in first.trails}) == 92_442
    assert census._geometry_authority_sha256(first.geom_bindings) == (
        "4364a8ef0c74d529fbbcd9b7be4bd410e51aa92a2aaab93d7bfcb140e3175b04"
    )
    assert census._area_slugs_sha256(first.areas) == (
        "4690c1491b86e10f904b765848125dbb3f15c969a1edb1f88c182d74879bb417"
    )


def test_anonymous_trail_refs_are_content_bound_and_order_independent():
    first_rows = [
        {"id": "", "name": "A", "segments": [[[1, 2], [3, 4]]]},
        {"id": "source", "name": "Known", "segments": []},
        {"id": "", "name": "B", "segments": [[[5, 6], [7, 8]]]},
    ]
    second_rows = [first_rows[2], first_rows[1], first_rows[0]]

    def by_name(rows):
        refs = census._anonymous_trail_refs("fixture-co", rows)
        return {rows[index]["name"]: value for index, value in refs.items()}

    assert by_name(first_rows) == by_name(second_rows)
    assert by_name(first_rows)["A"] != by_name(first_rows)["B"]
    duplicates = [copy.deepcopy(first_rows[0]), copy.deepcopy(first_rows[0])]
    duplicate_refs = census._anonymous_trail_refs("fixture-co", duplicates)
    assert len(set(duplicate_refs.values())) == 2
    assert sorted(duplicate_refs.values()) == sorted(
        census._anonymous_trail_refs("fixture-co", list(reversed(duplicates))).values()
    )


def test_production_baseline_file_is_exact_canonical_self_hashed_and_pinned(tmp_path):
    baseline = census._load_production_baseline(
        PRODUCTION_BASELINE, require_approved=True
    )
    document = baseline.document
    assert baseline.registry_binding is not None
    assert baseline.registry_binding.path == APPROVED_BASELINES
    assert baseline.registry_binding.sha256 == (
        "9ca2bc5c15939bac68fdf7b65bccb4f4960b90b3e86d317fe2b341df6a03b731"
    )
    assert baseline.binding.sha256 == (
        "0379418d36dceb9fdd9b804b8e387c40ecf2a81f72f467ba8f110c40ed673aa9"
    )
    approvals, registry_binding = census._load_approved_baseline_registry()
    assert registry_binding.sha256 == census.APPROVED_BASELINE_REGISTRY_SHA256
    assert approvals == ({
        "version": 1,
        "name": "production-baseline-v1",
        "self_sha256": (
            "08d869a9d877432089ff052034fda0f9f27b37b8c377cb1df61f14da5ae755bc"
        ),
        "canonical_file_sha256": (
            "0379418d36dceb9fdd9b804b8e387c40ecf2a81f72f467ba8f110c40ed673aa9"
        ),
        "predecessor_self_sha256": None,
    },)
    assert baseline.self_sha256 == (
        "08d869a9d877432089ff052034fda0f9f27b37b8c377cb1df61f14da5ae755bc"
    )
    assert document["bundle"]["sha256"] == (
        "6a4ca46946457313381b6f3d862dc0b73936ab3374d36fb73717bd5163aa0af3"
    )
    assert document["geometry"]["authority_sha256"] == (
        "4364a8ef0c74d529fbbcd9b7be4bd410e51aa92a2aaab93d7bfcb140e3175b04"
    )
    assert document["scope"] == {
        "area_slugs_sha256": (
            "4690c1491b86e10f904b765848125dbb3f15c969a1edb1f88c182d74879bb417"
        ),
        "areas": 9_074,
        "endpoint_occurrences": 245_026,
        "jurisdictions": 51,
        "trails": 92_442,
        "trails_without_source_id": 5,
        "unique_endpoints": 172_830,
    }
    assert document["live_pool"] == {
        "rows": 30_934,
        "sha256": "66ae82a88233b7e7414c4234819e1e253415680a0f73fb12eed29a9550aaeb2c",
        "size_bytes": 1_398_828,
    }
    assert document["verdicts"] == {
        "drop": 372,
        "keep": 874,
        "review": 7,
        "rows": 1_253,
        "sha256": "6e109573d860d07c70d9f26f8fcda9695005e87c8d840ffd16f91a086db7be1d",
    }
    assert (HERE / "parking-adjud" / "requirements.txt").read_text() == (
        "Shapely==2.0.6\n"
    )
    tampered = tmp_path / "tampered-baseline.json"
    changed = copy.deepcopy(document)
    changed["scope"]["areas"] -= 1
    tampered.write_bytes(census._canonical_bytes(changed))
    with pytest.raises(census.CensusError, match="self-hash mismatch"):
        census._load_production_baseline(tampered)


def test_baseline_validation_accepts_exact_inputs_and_rejects_any_drift(tmp_path):
    bundle, geom_dir = _write_national_fixture(tmp_path)
    scope = census.capture_scope(bundle, geom_dir)
    live_path = tmp_path / "live.json"
    live_path.write_text("[]")
    verdict_path = tmp_path / "verdicts.json"
    verdict_path.write_text('{"lots":{},"version":1}')
    live = census._load_live_pool(live_path)
    verdicts = census._load_verdicts(verdict_path)
    payload = {
        "schema": census.PRODUCTION_BASELINE_SCHEMA,
        "version": 1,
        "name": "production-baseline-v1",
        "bundle": {"sha256": scope.bundle_binding.sha256},
        "geometry": {
            "authority_sha256": census._geometry_authority_sha256(
                scope.geom_bindings
            )
        },
        "scope": {
            "areas": 51,
            "jurisdictions": 51,
            "trails": 51,
            "trails_without_source_id": 0,
            "endpoint_occurrences": 102,
            "unique_endpoints": 2,
            "area_slugs_sha256": census._area_slugs_sha256(scope.areas),
        },
        "live_pool": {
            "rows": 0,
            "size_bytes": live.binding.size_bytes,
            "sha256": live.binding.sha256,
        },
        "verdicts": {
            "rows": 0, "keep": 0, "drop": 0, "review": 0,
            "sha256": verdicts.binding.sha256,
        },
        "self_hash_algorithm": census.PRODUCTION_BASELINE_SELF_HASH,
    }
    document = census._self_hashed_document(payload)
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_bytes(census._canonical_bytes(document))
    baseline = census._load_production_baseline(baseline_path)
    census._validate_production_baseline(baseline, scope, live, verdicts)
    with pytest.raises(census.CensusError, match="not in the reviewed approved"):
        census._load_production_baseline(
            baseline_path, require_approved=True
        )
    drifted_live_path = tmp_path / "drifted-live.json"
    drifted_live_path.write_text("[[0,0]]")
    with pytest.raises(census.CensusError, match="live_pool"):
        census._validate_production_baseline(
            baseline, scope, census._load_live_pool(drifted_live_path), verdicts
        )


def test_sealer_emits_canonical_versioned_unapproved_successor(tmp_path):
    bundle, geom_dir = _write_national_fixture(tmp_path)
    live_path = tmp_path / "live.json"
    live_path.write_text("[]")
    verdict_path = tmp_path / "verdicts.json"
    verdict_path.write_text('{"lots":{},"version":1}')
    candidate_path = tmp_path / "production-baseline-v2.json"
    registry_before = APPROVED_BASELINES.read_bytes()
    result = sealer.seal_candidate(
        bundle_path=bundle,
        geom_dir=geom_dir,
        live_pool_path=live_path,
        verdicts_path=verdict_path,
        predecessor_path=PRODUCTION_BASELINE,
        output_path=candidate_path,
    )
    assert result["version"] == 2
    assert result["name"] == "production-baseline-v2"
    assert result["approved"] is False
    assert result["predecessor_self_sha256"] == (
        "08d869a9d877432089ff052034fda0f9f27b37b8c377cb1df61f14da5ae755bc"
    )
    raw = candidate_path.read_bytes()
    document = json.loads(raw)
    assert raw == census._canonical_bytes(document)
    assert document["version"] == 2
    assert document["name"] == "production-baseline-v2"
    assert document["predecessor_self_sha256"] == result[
        "predecessor_self_sha256"
    ]
    payload = dict(document)
    supplied_self = payload.pop("self_sha256")
    assert supplied_self == census._canonical_hash(payload)
    assert hashlib.sha256(raw).hexdigest() == result["canonical_file_sha256"]
    assert candidate_path.stat().st_mode & 0o777 == 0o600
    assert APPROVED_BASELINES.read_bytes() == registry_before
    candidate = census._load_production_baseline(candidate_path)
    census._validate_production_baseline(
        candidate,
        census.capture_scope(bundle, geom_dir),
        census._load_live_pool(live_path),
        census._load_verdicts(verdict_path),
    )
    with pytest.raises(census.CensusError, match="not in the reviewed approved"):
        census._load_production_baseline(
            candidate_path, require_approved=True
        )
    with pytest.raises(census.CensusError, match="already exists"):
        sealer.seal_candidate(
            bundle_path=bundle,
            geom_dir=geom_dir,
            live_pool_path=live_path,
            verdicts_path=verdict_path,
            predecessor_path=PRODUCTION_BASELINE,
            output_path=candidate_path,
        )


def test_authoritative_api_and_cli_require_explicit_production_gates():
    common = {
        "bundle_path": "bundle",
        "geom_dir": "geom",
        "parking_path": "parking",
        "live_pool_path": "live",
        "verdicts_path": "verdicts",
        "output_dir": "output",
    }
    with pytest.raises(census.CensusError, match="only PBF"):
        census.run_census(
            **common, parking_kind="geojsonseq", authoritative=True
        )
    with pytest.raises(census.CensusError, match="requires a production baseline"):
        census.run_census(**common, parking_kind="pbf", authoritative=True)
    with pytest.raises(census.CensusError, match="absolute --osmium"):
        census.run_census(
            **common,
            parking_kind="pbf",
            authoritative=True,
            production_baseline_path=PRODUCTION_BASELINE,
        )
    arguments = census._parser().parse_args([
        "--parking-pbf", "/input/us.pbf",
        "--authoritative",
        "--production-baseline", "/input/baseline.json",
        "--pbf-artifact-dir", "/archive/filter",
        "--osmium", "/usr/local/bin/osmium",
        "--expected-osmium-sha256", "a" * 64,
        "--osmium-timeout-seconds", "7200",
        "--live-pool", "/input/live.json",
        "--output-dir", "/output/census",
    ])
    assert arguments.authoritative is True
    assert arguments.production_baseline == "/input/baseline.json"
    assert arguments.expected_osmium_sha256 == "a" * 64
    assert arguments.osmium_timeout_seconds == 7_200.0


def test_excluded_exact_osm_alias_is_reserved_before_selected_ledger():
    components = tuple(
        _component(_point_feature(f"node/{index}", east_m=(index - 1) * 100))
        for index in range(1, 6)
    )
    verdicts = pv.Verdicts(_strict_verdict_document(
        _minimal_verdict_entry("node/5", "DROP", latitude=0.0, longitude=0.0)
    ))
    selected = {component.candidate_id for component in components[:4]}
    ledger = census.reconcile_verdict_ledger(
        components, [], verdicts, component_ids=selected
    )
    assert ledger.assignments == {}
    assert ledger.matched_keys == ()
    assert ledger.reserved_authoritative_keys == ("node/5",)
    assert ledger.reserved_unselected_keys == ("node/5",)
    assert [entry["_key"] for entry in ledger.tombstone_entries] == ["node/5"]
    assert all(component.verdict_entry is None for component in components)

    selected.add(components[4].candidate_id)
    ledger = census.reconcile_verdict_ledger(
        components, [], verdicts, component_ids=selected
    )
    assert ledger.assignments == {
        components[4].candidate_id: components[4].verdict_entry
    }
    assert components[4].verdict_entry["_key"] == "node/5"


@pytest.mark.parametrize("verdict", ("KEEP", "DROP", "REVIEW"))
def test_envelope_dropped_exact_alias_stays_tombstone_not_positional_neighbor(
        tmp_path, verdict):
    retained_lon = math.degrees(4_990.0 / census.EARTH_RADIUS_M)
    dropped_lon = math.degrees(5_005.0 / census.EARTH_RADIUS_M)
    assert census.haversine_m(0.0, 0.0, 0.0, retained_lon) == pytest.approx(
        4_990.0
    )
    assert census.haversine_m(0.0, 0.0, 0.0, dropped_lon) == pytest.approx(
        5_005.0
    )
    assert census.haversine_m(
        0.0, retained_lon, 0.0, dropped_lon
    ) == pytest.approx(15.0)

    verdicts = pv.strict_verdicts_document(_strict_verdict_document(
        _minimal_verdict_entry(
            "node/2", verdict, latitude=0.0, longitude=dropped_lon
        )
    ))
    fixture = tmp_path / f"cutoff-{verdict.lower()}.geojsonseq"
    fixture.write_text("".join(
        json.dumps(row, separators=(",", ":")) + "\n"
        for row in (
            _geojson_feature(
                "n1", {"type": "Point", "coordinates": [retained_lon, 0.0]}
            ),
            _geojson_feature(
                "n2", {"type": "Point", "coordinates": [dropped_lon, 0.0]}
            ),
        )
    ))
    parking = census.stream_parking_source(
        fixture,
        "geojsonseq",
        census.EndpointGrid((census.Endpoint(0.0, 0.0),)),
        sidecar_aliases=verdicts.osm_aliases,
    )
    assert [feature.alias for feature in parking.features] == ["node/1"]
    assert parking.counters["outside_fallback_envelope"] == 1
    assert parking.counters["seen_authority_aliases"] == 1
    assert parking.seen_authority_aliases == ("node/2",)
    positional_claims = verdicts.claims({
        "osm": "node/1", "lat": 0.0, "lon": retained_lon,
    })
    assert [(claim["entry"]["_key"], claim["kind"])
            for claim in positional_claims] == [("node/2", "position")]

    components, _edges = census.build_components(parking.features, verdicts)
    ledger = census.reconcile_verdict_ledger(
        components,
        [],
        verdicts,
        seen_authority_aliases=parking.seen_authority_aliases,
    )
    assert ledger.assignments == {}
    assert ledger.matched_keys == ()
    assert ledger.reserved_authoritative_keys == ("node/2",)
    assert ledger.reserved_unselected_keys == ("node/2",)
    assert ledger.seen_authority_aliases == ("node/2",)
    assert ledger.seen_authority_keys == ("node/2",)
    assert [entry["_key"] for entry in ledger.tombstone_entries] == ["node/2"]
    assert components[0].verdict_entry is None


def test_indexed_verdict_ledger_matches_reference_mutual_best_scan(monkeypatch):
    entries = tuple(
        _minimal_verdict_entry(
            f"node/{1_000 + index}",
            "KEEP" if index % 2 else "DROP",
            latitude=20.0 + index * 0.01,
            longitude=-100.0,
        )
        for index in range(30)
    )
    verdicts = pv.Verdicts(_strict_verdict_document(*entries))
    components = tuple(
        _component(_point_feature(f"node/{index + 1}", east_m=index * 50))
        for index in range(100)
    )
    rng = random.Random(454)
    graph = {}
    for component in components:
        claims = {}
        for key, entry in verdicts.entries.items():
            if rng.random() < 0.08:
                score = (1, rng.randrange(3), float(rng.randrange(5)))
                claims[key] = (score, entry)
        graph[component.candidate_id] = claims

    def reference():
        edges = {
            (candidate_id, key): edge
            for candidate_id, claims in graph.items()
            for key, edge in claims.items()
        }
        remaining_candidates = set(graph)
        remaining_keys = set(verdicts.entries)
        assigned = {}
        while True:
            candidate_best = {}
            for candidate_id in sorted(remaining_candidates):
                options = [
                    (score, key)
                    for (edge_candidate, key), (score, _entry) in edges.items()
                    if edge_candidate == candidate_id and key in remaining_keys
                ]
                if options:
                    best_score = min(score for score, _key in options)
                    keys = sorted(key for score, key in options if score == best_score)
                    if len(keys) == 1:
                        candidate_best[candidate_id] = (keys[0], best_score)
            verdict_best = {}
            for key in sorted(remaining_keys):
                options = [
                    (score, candidate_id)
                    for (candidate_id, edge_key), (score, _entry) in edges.items()
                    if edge_key == key and candidate_id in remaining_candidates
                ]
                if options:
                    best_score = min(score for score, _candidate in options)
                    candidates = sorted(
                        candidate_id for score, candidate_id in options
                        if score == best_score
                    )
                    if len(candidates) == 1:
                        verdict_best[key] = (candidates[0], best_score)
            accepted = sorted(
                (candidate_id, key)
                for candidate_id, (key, score) in candidate_best.items()
                if verdict_best.get(key) == (candidate_id, score)
            )
            if not accepted:
                return assigned
            for candidate_id, key in accepted:
                assigned[candidate_id] = key
                remaining_candidates.remove(candidate_id)
                remaining_keys.remove(key)

    monkeypatch.setattr(
        census, "_component_verdict_claims",
        lambda component, _verdicts: graph[component.candidate_id],
    )
    ledger = census.reconcile_verdict_ledger(components, [], verdicts)
    assert {
        candidate_id: entry["_key"]
        for candidate_id, entry in ledger.assignments.items()
    } == reference()


def test_drop_replacement_fixed_point_reuses_one_immutable_service_graph():
    endpoint = census.Endpoint(0.0, 0.0)
    trail = census.Trail("fixture-co", "CO", "trail", "Trail", (0,))
    components = tuple(
        _component(_point_feature(f"node/{index}", east_m=index * 100))
        for index in range(1, 5)
    )
    verdicts = pv.Verdicts(_strict_verdict_document(
        _minimal_verdict_entry("node/1", "DROP")
    ))
    cache = {}
    graph = census.build_service_graph(
        (trail,), (endpoint,), components, distance_cache=cache
    )
    passes = []

    def service_pass():
        result = census.evaluate_service_graph(graph, components)
        passes.append(copy.deepcopy(result[1]["associations"]))
        return result

    final_ids, projections, ledger = census._close_candidate_verdict_service(
        components, [], verdicts, service_pass
    )
    assert final_ids == {component.candidate_id for component in components}
    assert ledger.matched_keys == ("node/1",)
    assert components[0].verdict_entry["verdict"] == "DROP"
    assert projections["closure"]["rounds"] == 2
    assert projections["closure"]["service_evaluations"] == 3
    assert projections["closure"]["association_rows_built_once"] == 4
    assert projections["closure"]["association_rows_sorted_once"] == 4
    assert projections["closure"]["association_rows_rebuilt_in_rounds"] == 0
    assert projections["closure"]["association_rows_resorted_in_rounds"] == 0
    assert len(cache) == 4
    assert graph.new_distance_computations == 4
    assert graph.reused_distance_evaluations == 0
    assert all(row["service_graph_builds"] == 1 for row in passes)
    assert all(
        row["association_rows_rebuilt_this_evaluation"] == 0
        and row["association_rows_resorted_this_evaluation"] == 0
        for row in passes
    )


def test_service_graph_matches_independent_projection_reference():
    endpoints = (
        census.Endpoint(0.0, 0.0),
        census.Endpoint(*_offset(north_m=1_000)),
        census.Endpoint(*_offset(east_m=2_000)),
    )
    trails = tuple(
        census.Trail(
            "fixture-co", "CO", f"trail-{index}", f"Trail {index}",
            (index % 3, (index + 1) % 3),
        )
        for index in range(6)
    )
    components = []
    for index in range(20):
        tags = {}
        if index % 7 == 0:
            tags["access"] = "private"
        elif index % 11 == 0:
            tags["parking"] = "street_side"
        feature = _point_feature(
            f"node/{index + 1}", east_m=index * 170,
            tags=tags, endpoints=(index % 3, (index + 1) % 3),
        )
        component = _component(feature)
        if index % 9 == 0:
            component.verdict_entry = _minimal_verdict_entry(
                feature.alias, "DROP"
            )
            component.verdict_entry["_key"] = feature.alias
        components.append(component)
    components = tuple(components)

    def reference():
        by_endpoint = census.collections.defaultdict(list)
        for component_index, component in enumerate(components):
            for endpoint_id in component.near_endpoint_ids:
                by_endpoint[endpoint_id].append(component_index)
        selected_ids = set()
        projection_counts = {
            projection: {
                "selected_candidates": set(), "trail_selections": 0,
                "near_trails": 0, "fallback_trails": 0,
            }
            for projection in census.PROJECTIONS
        }
        bindings = census.collections.defaultdict(dict)
        for trail in sorted(trails, key=lambda value: value.key):
            candidate_indices = sorted({
                component_index
                for endpoint_id in trail.endpoint_ids
                for component_index in by_endpoint[endpoint_id]
            }, key=lambda value: components[value].candidate_id)
            scored = sorted(
                (
                    census.component_trail_distance_m(
                        components[index], trail, endpoints
                    ),
                    index,
                )
                for index in candidate_indices
            )
            for projection in census.PROJECTIONS:
                eligible = [
                    item for item in scored
                    if census._projection_eligibility(
                        components[item[1]]
                    )[projection]["eligible"]
                ]
                near = [item for item in eligible if item[0] <= census.NEAR_M]
                fallback = not near
                chosen = (
                    near[:census.NEAR_LIMIT] if near else
                    [item for item in eligible if item[0] <= census.FALLBACK_M][
                        :census.FALLBACK_LIMIT
                    ]
                )
                if chosen:
                    projection_counts[projection]["trail_selections"] += 1
                    projection_counts[projection][
                        "fallback_trails" if fallback else "near_trails"
                    ] += 1
                for distance_m, component_index in chosen:
                    component = components[component_index]
                    selected_ids.add(component.candidate_id)
                    projection_counts[projection]["selected_candidates"].add(
                        component.candidate_id
                    )
                    binding = bindings[component.candidate_id].setdefault(
                        trail.key,
                        {
                            "distance_m": round(distance_m, 3),
                            "fallback": fallback,
                            "projections": [],
                        },
                    )
                    binding["projections"].append(projection)
        return selected_ids, projection_counts, bindings

    expected_ids, expected_counts, expected_bindings = reference()
    graph = census.build_service_graph(trails, endpoints, components)
    actual_ids, actual_counts = census.evaluate_service_graph(graph, components)
    assert actual_ids == expected_ids
    for projection in census.PROJECTIONS:
        assert actual_counts[projection] == {
            **{
                key: value for key, value in expected_counts[projection].items()
                if key != "selected_candidates"
            },
            "selected_candidates": len(
                expected_counts[projection]["selected_candidates"]
            ),
        }
    for component in components:
        actual = {
            (row["area"], row["trail_id"]): {
                "distance_m": row["distance_m"],
                "fallback": row["fallback"],
                "projections": row["projections"],
            }
            for row in component.bindings
        }
        assert actual == expected_bindings.get(component.candidate_id, {})


def test_production_shaped_service_graph_benchmark_never_rebuilds_rows(
        monkeypatch):
    components = tuple(
        _component(_point_feature(f"node/{index + 1}", endpoints=(0,)))
        for index in range(10)
    )
    trails = tuple(
        census.Trail(
            "fixture-co", "CO", f"trail-{index:05d}", None, (0,)
        )
        for index in range(9_244)
    )
    calls = 0

    def distance(component, _trail, _endpoints):
        nonlocal calls
        calls += 1
        return float(int(component.aliases[0].split("/")[1]))

    monkeypatch.setattr(census, "component_trail_distance_m", distance)
    graph = census.build_service_graph(
        trails, (census.Endpoint(0.0, 0.0),), components
    )
    assert graph.association_rows_built == 92_440
    assert graph.association_rows_sorted == 92_440
    assert calls == 92_440

    def service_pass():
        return census.evaluate_service_graph(graph, components)

    _selected, projections, _ledger = census._close_candidate_verdict_service(
        components, [], _empty_verdicts(), service_pass
    )
    assert calls == 92_440
    assert projections["closure"]["service_evaluations"] == 2
    assert projections["closure"]["association_rows_built_once"] == 92_440
    assert projections["closure"]["association_rows_sorted_once"] == 92_440
    assert projections["closure"]["association_rows_rebuilt_in_rounds"] == 0
    assert projections["closure"]["association_rows_resorted_in_rounds"] == 0
    assert projections["closure"]["raw_current_selection_precomputations"] == 1


def test_fixed_point_cycle_and_exhaustion_are_named_census_errors():
    verdicts = _empty_verdicts()
    counters = {"associations": {"distance_cache_entries": 0}}
    cycle_values = iter(({"a"}, {"b"}, {"a"}))

    def cycle_pass():
        return next(cycle_values), copy.deepcopy(counters)

    with pytest.raises(census.CensusError, match="entered a cycle"):
        census._close_candidate_verdict_service(
            (), [], verdicts, cycle_pass, max_rounds=4
        )

    exhaustion_values = iter(({"a"}, {"b"}, {"c"}))

    def exhaustion_pass():
        return next(exhaustion_values), copy.deepcopy(counters)

    with pytest.raises(census.CensusError, match="did not converge within 2 rounds"):
        census._close_candidate_verdict_service(
            (), [], verdicts, exhaustion_pass, max_rounds=2
        )


@pytest.mark.parametrize("geometry", [
    {
        "type": "Polygon",
        "coordinates": [
            [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
            [[20, 20], [21, 20], [21, 21], [20, 21], [20, 20]],
        ],
    },
    {
        "type": "Polygon",
        "coordinates": [
            [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
            [[0, 2], [2, 2], [2, 4], [0, 4], [0, 2]],
        ],
    },
    {
        "type": "Polygon",
        "coordinates": [
            [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
            [[2, 2], [6, 2], [6, 6], [2, 6], [2, 2]],
            [[4, 4], [8, 4], [8, 8], [4, 8], [4, 4]],
        ],
    },
    {
        "type": "MultiPolygon",
        "coordinates": [
            [[[0, 0], [6, 0], [6, 6], [0, 6], [0, 0]]],
            [[[4, 4], [10, 4], [10, 10], [4, 10], [4, 4]]],
        ],
    },
])
def test_complete_polygon_and_multipolygon_topology_is_strictly_validated(geometry):
    with pytest.raises(census.FeatureRejection, match="invalid complete polygon topology"):
        census.normalize_geometry(geometry)


def test_default_geometry_and_feature_normalizers_validate_topology(monkeypatch):
    real_validator = census._validate_polygon_topology
    topology_calls = 0

    def track_topology(geometry, **kwargs):
        nonlocal topology_calls
        topology_calls += 1
        real_validator(geometry, **kwargs)

    monkeypatch.setattr(census, "_validate_polygon_topology", track_topology)
    polygon = {"type": "Polygon", "coordinates": [[
        [50.0, 50.0], [50.001, 50.0], [50.001, 50.001],
        [50.0, 50.001], [50.0, 50.0],
    ]]}
    assert census.normalize_geometry(polygon) == polygon
    feature = census.normalize_feature(
        _geojson_feature("w99", polygon), census.EndpointGrid(()), {},
        associate_endpoints=False,
    )
    assert feature is not None
    assert feature.geometry == polygon
    assert topology_calls == 2


def test_topology_engine_is_exactly_shapely_206(monkeypatch):
    assert census._geometry_engine_manifest() == {
        "engine": "Shapely",
        "version": "2.0.6",
        "requirement": "Shapely==2.0.6",
        "validity": (
            "complete Polygon/MultiPolygon topology with explicit near-zero "
            "ring rejection at absolute twice-signed area <= 1e-15 square "
            "degrees"
        ),
        "capture_validation_scope": (
            "polygonal parking with a nonempty conservative 5km EndpointGrid "
            "candidate set after non-area area-copy suppression; polygonal "
            "records outside every envelope are explicit topology-unvalidated "
            "noncandidates"
        ),
        "default_validation_scope": "eager for normalize_geometry/normalize_feature",
    }
    policy = census._parking_geometry_policy()
    assert policy["structure_validation"] == (
        "strict-feature-tags-id-finite-in-range-exactly-two-dimensional-"
        "coordinates-with-signed-zero-value-equality-positive-zero-"
        "canonicalization-and-explicitly-closed-rings-with-at-least-four-"
        "points-before-spatial-gating"
    )
    assert policy["degenerate_polygon_staging"] == {
        "ring_detection": (
            "absolute-first-coordinate-relative-direct-longitude-delta-"
            "conditional-shortest-wrap-fsum-shoelace-near-zero-ring-when-"
            "twice-signed-area-is-at-or-below-the-exact-threshold"
        ),
        "head_threshold_compatibility": (
            "same-1e-15-threshold-with-stable-first-coordinate-relative-fsum-"
            "can-admit-above-threshold-rings-or-reject-at-or-below-threshold-"
            "rings-opposite-of-global-coordinate-sum-noise"
        ),
        "twice_signed_area_epsilon": 1e-15,
        "twice_signed_area_epsilon_units": "square-degrees",
        "component_definition": (
            "polygon-component-whose-outer-ring-is-degenerate"
        ),
        "stream_policy": (
            "retain-complete-parsed-coordinate-sequence-and-diagnostics-"
            "through-coarse-candidate-gating"
        ),
        "json_numeric_lexical_policy": (
            "source-spellings-normalize-under-parse-and-canonical-staging-"
            "serialization-not-preserved-as-raw-token-bytes"
        ),
        "parsed_coordinate_sequence_preservation": (
            "exactly-two-dimensional-positions-with-exact-nonzero-binary-"
            "float-values-and-signed-zero-value-equality-canonicalized-to-"
            "positive-zero-plus-ring-component-structure-order-and-explicit-"
            "closure"
        ),
        "staged_identity_preservation": (
            "exact-canonical-alias-source-form-and-private-typed-geometry-"
            "paired-degeneracy-locations"
        ),
        "source_byte_authority": (
            "source-binding-sha256-separate-from-staged-geometry-"
            "representation"
        ),
        "coarse_coverage": (
            "every-retained-coordinate-and-segment-in-every-ring-and-component"
        ),
        "empty_coarse_candidates": (
            census.TOPOLOGY_UNVALIDATED_NONCANDIDATE_STATUS
        ),
        "nonempty_coarse_candidates": (
            "reject-sub-threshold-near-zero-or-shapely-invalid-complete-"
            "topology-before-exact-association"
        ),
        "candidate_rejection_counters": (
            "partition-topology-failures-into-staged-near-zero-threshold-"
            "rejections-with-stream-order-identity-sha256-and-shapely-"
            "topology-rejections"
        ),
        "public_normalization": (
            "eager-pinned-Shapely-plus-strict-near-zero-threshold-contract"
        ),
        "geometry_mutation": (
            "forbidden-no-ring-or-component-dropping-or-candidate-conversion"
        ),
        "staged_counter_scope": (
            "polygonal-records-after-declared-non-area-area-copy-suppression"
        ),
        "inventory": (
            "staged-and-terminal-counts-plus-separate-far-noncandidate-and-"
            "candidate-rejection-sha256-values-over-concatenated-canonical-"
            "json-lines-alias-source_form-geometry_type-ring_locations-"
            "component_indices-in-stream-order"
        ),
    }
    assert policy["polygon_representative"] == {
        "ring_centroid": (
            "first-coordinate-relative-direct-longitude-delta-conditional-"
            "shortest-wrap-fsum-shoelace-moments"
        ),
        "sanity": "centroid-must-remain-within-local-ring-coordinate-bounds",
        "fallback": "first-coordinate-relative-fsum-open-ring-vertex-mean",
    }
    assert policy["topology_engine"] == "Shapely==2.0.6"
    assert policy["coarse_radius_m"] == 5_000.0
    assert policy["coarse_index"] == (
        "conservative-spherical-segment-union-EndpointGrid"
    )
    assert policy["coarse_geometry_model"] == (
        "local-convex-spherical-caps-or-great-circle-segment-tubes-plus-"
        "anchor-unwrapped-polygon-interiors"
    )
    assert policy["coarse_local_cap_radians"] == math.radians(1.0)
    assert policy["spherical_envelope_epsilon"] == {
        "name": "SPHERICAL_ENVELOPE_EPSILON_DEGREES",
        "value": 1e-10,
        "units": "degrees",
    }
    assert policy["polygon_interior_predicate"] == (
        "fixed-first-vertex-anchor-unwrapped-even-odd"
    )
    assert policy["coarse_ambiguous_segment_radians"] == 1e-7
    assert policy["coarse_ambiguous_segment_policy"] == (
        "query-all-endpoint-cells-or-fail-resource-caps"
    )
    assert policy["coarse_vertex_scan"] == {
        "policy": (
            "record-byte-admission-then-parsed-amenity-relevance-before-"
            "delimiter-count-depth-proof-or-no-copy-structural-scan"
        ),
        "admission": (
            "all-valid-json-records-at-or-below-maximum-record-bytes-"
            "regardless-of-coordinate-count-or-numeric-spelling"
        ),
        "accounting": "vertices-inspected-diagnostic-only",
        "maximum_json_nesting_depth": 128,
        "json_depth_fastpath": (
            "total-opening-delimiters-at-most-maximum-proves-depth-bound"
        ),
        "structural_preparse_minimum_record_bytes": (
            census.STREAM_STRUCTURAL_PREPARSE_MIN_BYTES
        ),
        "structural_preparse_diagnostics": (
            "potentially-parking-record-depth-and-large-record-coordinate-"
            "pair-arrays; definitely-nonparking-records-not-scanned"
        ),
        "coordinate_units": "GeoJSON coordinate-pair arrays",
        "record_bound_kind": "processing-not-buffering-or-peak-memory",
        "record_framing": (
            "maximum-applies-to-JSON-payload-after-removing-one-optional-"
            "record-separator-and-one-LF-CRLF-or-CR-line-terminator"
        ),
        "maximum_record_bytes": 16 * 1024 * 1024,
    }
    assert policy["coarse_segment_planning"] == {
        "maximum_segment_envelopes": (
            census.MAX_ENDPOINT_GRID_SEGMENT_ENVELOPES
        ),
        "derivation": (
            "ceil(max_stream_record_bytes / "
            "minimum_compact_geojson_coordinate_bytes)"
        ),
        "minimum_compact_geojson_coordinate_bytes": 6,
        "units": "precharged spherical segment envelopes",
        "policy": (
            "precharge-before-each-tight-segment-envelope-computation-or-"
            "retention"
        ),
    }
    assert policy["coarse_grid_query"] == {
        "maximum_windows": census.MAX_ENDPOINT_GRID_QUERY_WINDOWS,
        "spherical_maximum_windows": (
            census.MAX_ENDPOINT_GRID_SPHERICAL_QUERY_WINDOWS
        ),
        "head_parity_maximum_windows": (
            census.MAX_ENDPOINT_GRID_HEAD_PARITY_QUERY_WINDOWS
        ),
        "window_derivation": (
            "sum-of-three-times-maximum-segment-envelopes-for-spherical-seam-"
            "splits-and-polygon-interiors-plus-one-separate-literal-HEAD-"
            "expanded-query-window"
        ),
        "maximum_retained_window_slots": (
            census.MAX_ENDPOINT_GRID_RETAINED_WINDOW_SLOTS
        ),
        "maximum_retained_window_bytes": (
            census.MAX_ENDPOINT_GRID_RETAINED_WINDOW_BYTES
        ),
        "reserved_bytes_per_window_slot": (
            census.ENDPOINT_GRID_RETAINED_WINDOW_SLOT_BYTES
        ),
        "retention_derivation": (
            "64MiB-conservative-budget-at-512-bytes-per-live-window-for-four-"
            "float-tuple-list-run-sort-and-final-output-worst-case"
        ),
        "maximum_row_probes": 1_000_000,
        "spherical_maximum_row_probes": 500_000,
        "head_parity_maximum_row_probes": 500_000,
        "maximum_cell_probes": 1_000_000,
        "spherical_maximum_cell_probes": 500_000,
        "head_parity_maximum_cell_probes": 500_000,
        "head_expanded_query_parity_maximum_cells": 500_000,
        "head_expanded_query_parity_policy": (
            "literal-HEAD-expanded-cell-window-with-latitude-expansion-"
            "expanded-maximum-absolute-latitude-linear-radius-over-cos-"
            "longitude-padding-cell-rounding-and-dateline-wrap-only-when-raw-"
            "longitude-span-is-less-than-180-degrees; wrapping-parity-disabled-"
            "while-spherical-superset-query-remains-authoritative"
        ),
        "row_cell_derivation": (
            "sum-of-independent-500000-spherical-and-500000-literal-HEAD-"
            "expanded-query-lanes-with-global-cell-deduplication"
        ),
        "units": {
            "windows": (
                "precharged spherical/global plus separate literal-HEAD-"
                "expanded-query windows actually scanned"
            ),
            "retained_window_slots": (
                "pre-reserved simultaneous merge input/output slots"
            ),
            "retained_window_bytes": (
                "512-byte conservative reservations for tuple/list/run/sort/"
                "final-output live memory"
            ),
            "rows": (
                "precharged occupied latitude-row probes in separate-"
                "spherical and HEAD-parity lanes"
            ),
            "cells": (
                "precharged globally-deduplicated longitude/occupied-cell-"
                "probes in separate spherical and HEAD-parity lanes"
            ),
        },
        "aggregate_query_work_units": "diagnostic-only-not-a-limit",
    }
    assert policy["coarse_global_fallback"] == {
        "maximum_candidates": 100_000,
        "policy": (
            "total-endpoint-candidate-preflight-then-one-global-window-"
            "over-all-occupied-cells-with-incremental-candidate-cap"
        ),
        "candidate_preflight": (
            "fail-before-global-scan-whenever-total-endpoints-exceed-"
            "maximum-candidates"
        ),
    }
    assert policy["topology_before_exact_distance"] is True
    assert policy["default_normalization_validates_topology"] is True
    assert policy["far_topology_policy"] == {
        "status": census.TOPOLOGY_UNVALIDATED_NONCANDIDATE_STATUS,
        "scope": (
            "structurally-admissible-Polygon-or-MultiPolygon-including-"
            "diagnosed-near-zero-sub-threshold-rings-proven-outside-every-"
            "conservative-5km-endpoint-envelope"
        ),
        "candidate": False,
        "denominator_policy": (
            "excluded-from-parking_features-and-candidate-denominator-while-"
            "record-accounting-and-export-reconciliation-close"
        ),
        "inventory_authority": (
            "record-count-and-stream-order-record-identity-sha256-plus-"
            "degenerate-record-ring-outer-inner-component-counts-and-location-"
            "identity-sha256-in-parking-source-inventory"
        ),
        "run_id_compatibility": (
            "intentional-delta-from-HEAD-preserves-candidate-denominator-"
            "without-restoring-quadratic-or-far-Shapely-topology-work"
        ),
    }
    filtered_policy = census._filtered_pbf_policy()
    assert "parking_geometry_policy" not in filtered_policy
    filtered_policy_bytes = census._canonical_bytes(filtered_policy)
    assert b"DEGENERATE_RING_TWICE_SIGNED_AREA_EPSILON" not in (
        filtered_policy_bytes
    )
    assert b"degenerate_polygon_staging" not in filtered_policy_bytes
    first_run_id = census._deterministic_run_id(
        _output_contract(0)[1]["sources"], {"geometry": policy}
    )
    for constant_name, section, policy_key in (
            ("MAX_ENDPOINT_GRID_SEGMENT_ENVELOPES",
             "coarse_segment_planning", "maximum_segment_envelopes"),
            ("MAX_ENDPOINT_GRID_QUERY_WINDOWS",
             "coarse_grid_query", "maximum_windows"),
            ("MAX_ENDPOINT_GRID_SPHERICAL_QUERY_WINDOWS",
             "coarse_grid_query", "spherical_maximum_windows"),
            ("MAX_ENDPOINT_GRID_HEAD_PARITY_QUERY_WINDOWS",
             "coarse_grid_query", "head_parity_maximum_windows"),
            ("MAX_ENDPOINT_GRID_RETAINED_WINDOW_SLOTS",
             "coarse_grid_query", "maximum_retained_window_slots"),
            ("MAX_ENDPOINT_GRID_RETAINED_WINDOW_BYTES",
             "coarse_grid_query", "maximum_retained_window_bytes"),
            ("ENDPOINT_GRID_RETAINED_WINDOW_SLOT_BYTES",
             "coarse_grid_query", "reserved_bytes_per_window_slot"),
            ("HEAD_ENDPOINT_GRID_QUERY_CELLS",
             "coarse_grid_query",
             "head_expanded_query_parity_maximum_cells"),
            ("MAX_ENDPOINT_GRID_ROW_PROBES",
             "coarse_grid_query", "maximum_row_probes"),
            ("MAX_ENDPOINT_GRID_SPHERICAL_ROW_PROBES",
             "coarse_grid_query", "spherical_maximum_row_probes"),
            ("MAX_ENDPOINT_GRID_HEAD_PARITY_ROW_PROBES",
             "coarse_grid_query", "head_parity_maximum_row_probes"),
            ("MAX_ENDPOINT_GRID_CELL_PROBES",
             "coarse_grid_query", "maximum_cell_probes"),
            ("MAX_ENDPOINT_GRID_SPHERICAL_CELL_PROBES",
             "coarse_grid_query", "spherical_maximum_cell_probes"),
            ("MAX_ENDPOINT_GRID_HEAD_PARITY_CELL_PROBES",
             "coarse_grid_query", "head_parity_maximum_cell_probes"),
            ("DEGENERATE_RING_TWICE_SIGNED_AREA_EPSILON",
             "degenerate_polygon_staging", "twice_signed_area_epsilon"),
            ("STREAM_STRUCTURAL_PREPARSE_MIN_BYTES",
             "coarse_vertex_scan",
             "structural_preparse_minimum_record_bytes"),
            ("MAX_STREAM_JSON_NESTING_DEPTH", "coarse_vertex_scan",
             "maximum_json_nesting_depth"),
            ("MAX_ENDPOINT_CANDIDATES_PER_FEATURE", "coarse_global_fallback",
             "maximum_candidates"),
            ("MAX_STREAM_RECORD_BYTES", "coarse_vertex_scan",
             "maximum_record_bytes")):
        original = getattr(census, constant_name)
        monkeypatch.setattr(census, constant_name, original + 1)
        changed_bound_policy = census._parking_geometry_policy()
        assert changed_bound_policy[section][policy_key] == original + 1
        assert census._deterministic_run_id(
            _output_contract(0)[1]["sources"],
            {"geometry": changed_bound_policy},
        ) != first_run_id
        monkeypatch.setattr(census, constant_name, original)
    monkeypatch.setattr(
        census, "SPHERICAL_ENVELOPE_EPSILON_DEGREES", 2e-10
    )
    changed_policy = census._parking_geometry_policy()
    assert changed_policy["spherical_envelope_epsilon"]["value"] == 2e-10
    assert census._filtered_pbf_policy() == filtered_policy
    assert census._deterministic_run_id(
        _output_contract(0)[1]["sources"], {"geometry": changed_policy}
    ) != first_run_id
    import shapely
    monkeypatch.setattr(shapely, "__version__", "2.1.0")
    with pytest.raises(census.CensusError, match="requires Shapely==2.0.6"):
        census._geometry_engine_manifest()


def test_malformed_values_are_ignored_only_when_definitively_nonparking():
    accumulator = census.FeatureAccumulator(census.EndpointGrid(()), {})
    school = {
        "type": "Feature", "id": "n1",
        "properties": {"amenity": "school", "bad": 7},
        "geometry": {"type": "GeometryCollection", "geometries": []},
    }
    potential = {
        "type": "Feature", "id": "n2",
        "properties": {"amenity": 7},
        "geometry": {"type": "Point", "coordinates": [0, 0]},
    }
    accumulator.consume_line((json.dumps(school) + "\n").encode())
    accumulator.consume_line((json.dumps(potential) + "\n").encode())
    counters = accumulator.final_counters()
    assert counters["ignored_nonparking"] == 1
    assert counters["rejections"] == {"malformed_tags": 1}


def test_input_pbf_inventory_uses_independent_tags_filter_R_opl(
        tmp_path, monkeypatch):
    executable = tmp_path / "osmium"
    executable.write_text("tool")
    executable.chmod(0o700)
    tool = census.OsmiumTool(
        census._capture_file_hash(executable, role="osmium-executable"), "v1"
    )
    commands = []

    def stream(command, label, consume, **kwargs):
        commands.append((command, label, kwargs.get("timeout_s")))
        consume(b"n1 v1 Tamenity=parking x0 y0\n")

    monkeypatch.setattr(census, "_stream_osmium_lines", stream)
    inventory, memberships = census._pbf_inventory(
        tmp_path / "input.pbf", tool, source_side=True, timeout_s=321.0
    )
    assert inventory == {
        "node/1": {
            "source_type": "node", "closed_way": False,
            "declared_area": None,
        }
    }
    assert memberships == {}
    command, label, timeout = commands[0]
    assert timeout == 321.0
    assert command[:5] == [str(executable), "tags-filter", "-R", "-f", "opl"]
    assert command[-1] == "nwr/amenity=parking"
    assert label == "osmium input PBF parking inventory"


def test_filtered_inventory_mismatch_names_filter_step():
    source = {"node/1": {"source_type": "node", "closed_way": False}}
    with pytest.raises(census.CensusError, match="tags-filter parking identity"):
        census._reconcile_filtered_inventory(source, {}, {}, {})
    with pytest.raises(census.CensusError, match="tags-filter parking membership"):
        census._reconcile_filtered_inventory(
            source, {"relation/1": ("way/1",)}, source, {}
        )


def test_optional_replication_headers_are_absent_only_in_nonauthoritative_mode(
        tmp_path, monkeypatch):
    executable = tmp_path / "osmium"
    executable.write_text("tool")
    executable.chmod(0o700)
    tool = census.OsmiumTool(
        census._capture_file_hash(executable, role="osmium-executable"), "v1"
    )

    timeouts = []

    def fileinfo(command, label, **kwargs):
        timeouts.append(kwargs.get("timeout_s"))
        value = "2026-10-05T00:00:00Z" if command[3] == (
            "header.option.timestamp"
        ) else ""
        return census.subprocess.CompletedProcess(command, 0, value + "\n", "")

    monkeypatch.setattr(census, "_run_osmium", fileinfo)
    metadata = census._pbf_metadata(
        tmp_path / "local.pbf", tool, require_replication=False,
        timeout_s=654.0,
    )
    assert metadata["timestamp"] == "2026-10-05T00:00:00Z"
    assert metadata["replication_timestamp"] is None
    assert metadata["sequence"] is None
    assert metadata["replication_headers_present"] is False
    assert timeouts == [654.0, 654.0, 654.0]
    with pytest.raises(census.CensusError, match="replication_timestamp"):
        census._pbf_metadata(
            tmp_path / "local.pbf", tool, require_replication=True
        )


def _mock_pbf_tool(tmp_path):
    executable = tmp_path / "osmium"
    executable.write_bytes(b"mocked-osmium-authority")
    executable.chmod(0o700)
    return census.OsmiumTool(
        census._capture_file_hash(executable, role="osmium-executable"),
        "osmium mocked 1",
    )


def _mock_pbf_capture(tmp_path, monkeypatch):
    source = tmp_path / "source.pbf"
    source.write_bytes(b"source-one")
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir(mode=0o700)
    tool = _mock_pbf_tool(tmp_path)
    inventory = {
        "node/1": {
            "source_type": "node", "closed_way": False,
            "declared_area": None,
        }
    }
    calls = []

    def run(command, label, **kwargs):
        calls.append((list(command), label, kwargs.get("timeout_s")))
        if "tags-filter" in command:
            Path(command[-1]).write_bytes(b"same-filtered-bytes")
        return census.subprocess.CompletedProcess(command, 0, "", "")

    def stream(command, label, consume, **kwargs):
        calls.append((list(command), label, kwargs.get("timeout_s")))
        consume((json.dumps(_geojson_feature(
            "n1", {"type": "Point", "coordinates": [0.0, 0.0]}
        )) + "\n").encode())

    monkeypatch.setattr(census, "_resolve_osmium", lambda *a, **k: tool)
    monkeypatch.setattr(
        census, "_pbf_metadata",
        lambda *a, **k: {
            "timestamp": "t", "replication_timestamp": None, "sequence": None,
            "replication_headers_required": False,
            "replication_headers_present": False,
        },
    )
    monkeypatch.setattr(
        census, "_pbf_inventory", lambda *a, **k: (inventory, {})
    )
    monkeypatch.setattr(census, "_run_osmium", run)
    monkeypatch.setattr(census, "_stream_osmium_lines", stream)
    return source, artifact_dir, tool, calls


def _legacy_filtered_pbf_policy():
    return {
        "expression": "nwr/amenity=parking",
        "input_inventory_command": "tags-filter -R -f opl",
        "filtered_inventory_command": "cat -f opl",
        "filter_command": "tags-filter INPUT nwr/amenity=parking -o OUTPUT",
        "export_command": (
            "export INPUT -f geojsonseq "
            "--geometry-types=point,linestring,polygon "
            "--add-unique-id=type_id"
        ),
        "closed_way_non_area_policy": (
            "LineString-required-area-copies-ignored-before-endpoint-association"
        ),
        "closed_way_non_area_values": ["0", "false", "no"],
    }


def _install_legacy_filter_policy(manifest_path, policy):
    document = json.loads(manifest_path.read_bytes())
    document["filter"] = policy
    document.pop("self_sha256")
    legacy_document = census._self_hashed_document(document)
    raw = census._canonical_bytes(legacy_document)
    manifest_path.write_bytes(raw)
    manifest_path.chmod(0o600)
    return raw


@pytest.mark.parametrize("closed_way_non_area_policy", [
    "LineString-required",
    "LineString-required-area-copies-ignored",
    "LineString-required-area-copies-ignored-before-endpoint-association",
])
def test_legacy_filtered_artifact_reuses_same_bytes_without_manifest_rewrite(
        tmp_path, monkeypatch, closed_way_non_area_policy):
    source, artifact_dir, _tool, calls = _mock_pbf_capture(
        tmp_path, monkeypatch
    )
    first = census._stream_pbf(
        source,
        census.EndpointGrid((census.Endpoint(0.0, 0.0),)),
        artifact_dir=artifact_dir,
        enforce_national_floor=False,
        minimum_artifact_free_bytes=0,
    )
    artifact = first.filtered_binding.path.parent
    manifest_path = artifact / "artifact-manifest.json"
    policy = _legacy_filtered_pbf_policy()
    policy["closed_way_non_area_policy"] = closed_way_non_area_policy
    legacy_raw = _install_legacy_filter_policy(manifest_path, policy)
    filtered_raw = (artifact / "parking.pbf").read_bytes()
    filter_calls = sum(
        "tags-filter" in command for command, _label, _timeout in calls
    )

    def forbid_stage(*_args, **_kwargs):
        pytest.fail("legacy same-byte reuse must not create a filter stage")

    monkeypatch.setattr(census.tempfile, "mkdtemp", forbid_stage)
    reused = census._stream_pbf(
        source,
        census.EndpointGrid((census.Endpoint(0.0, 0.0),)),
        artifact_dir=artifact_dir,
        enforce_national_floor=False,
        minimum_artifact_free_bytes=0,
    )
    assert reused.filtered_binding.sha256 == first.filtered_binding.sha256
    assert reused.filtered_manifest_binding.sha256 == hashlib.sha256(
        legacy_raw
    ).hexdigest()
    assert manifest_path.read_bytes() == legacy_raw
    assert (artifact / "parking.pbf").read_bytes() == filtered_raw
    assert sum(
        "tags-filter" in command for command, _label, _timeout in calls
    ) == filter_calls
    assert not list(artifact_dir.glob(".parking-filter-stage-*"))


@pytest.mark.parametrize("key,value", [
    ("closed_way_non_area_policy", "LineString-required-never-shipped"),
    ("input_inventory_command", "tags-filter -f opl"),
    ("filtered_inventory_command", "cat -f geojsonseq"),
    ("export_command", "export INPUT -f geojsonseq"),
    ("closed_way_non_area_values", ["no", "false", "0"]),
])
def test_legacy_filter_projection_rejects_unshipped_metadata_and_none_parity(
        key, value):
    invalid = _legacy_filtered_pbf_policy()
    invalid[key] = value
    assert census._filtered_pbf_policy_identity(invalid) is None
    document = {"filter": invalid, "self_sha256": "same"}
    assert not census._filtered_artifact_documents_equivalent(
        document, copy.deepcopy(document)
    )


def test_never_shipped_legacy_geometry_policy_shape_is_rejected(
        tmp_path, monkeypatch):
    source, artifact_dir, _tool, _calls = _mock_pbf_capture(
        tmp_path, monkeypatch
    )
    first = census._stream_pbf(
        source,
        census.EndpointGrid((census.Endpoint(0.0, 0.0),)),
        artifact_dir=artifact_dir,
        enforce_national_floor=False,
        minimum_artifact_free_bytes=0,
    )
    artifact = first.filtered_binding.path.parent
    manifest_path = artifact / "artifact-manifest.json"
    unsupported = _legacy_filtered_pbf_policy()
    unsupported["parking_geometry_policy"] = {
        "coarse_index": "arbitrary-uncommitted-policy"
    }
    unsupported_raw = _install_legacy_filter_policy(
        manifest_path, unsupported
    )

    with pytest.raises(
            census.CensusError, match="filter-byte provenance does not match"):
        census._read_filtered_artifact_manifest(artifact)
    assert manifest_path.read_bytes() == unsupported_raw
    assert not list(artifact_dir.glob(".parking-filter-stage-*"))


def test_filtered_digest_collision_rejects_mismatched_provenance_and_names_artifact(
        tmp_path, monkeypatch):
    source, artifact_dir, _tool, _calls = _mock_pbf_capture(
        tmp_path, monkeypatch
    )
    first = census._stream_pbf(
        source,
        census.EndpointGrid((census.Endpoint(0.0, 0.0),)),
        artifact_dir=artifact_dir,
        enforce_national_floor=False,
        minimum_artifact_free_bytes=0,
    )
    existing = first.filtered_binding.path.parent
    other_source = tmp_path / "source-two.pbf"
    other_source.write_bytes(b"source-two")
    with pytest.raises(census.CensusError) as raised:
        census._stream_pbf(
            other_source,
            census.EndpointGrid((census.Endpoint(0.0, 0.0),)),
            artifact_dir=artifact_dir,
            enforce_national_floor=False,
            minimum_artifact_free_bytes=0,
        )
    message = str(raised.value)
    assert str(existing) in message
    assert "mismatched source/osmium/tags-filter/inventory provenance" in message
    stages = list(artifact_dir.glob(".parking-filter-stage-*"))
    assert len(stages) == 1
    assert (stages[0] / "parking.pbf").read_bytes() == b"same-filtered-bytes"
    assert existing.is_dir()


def test_changed_true_filter_refuses_occupied_same_byte_destination(
        tmp_path, monkeypatch):
    source, artifact_dir, _tool, calls = _mock_pbf_capture(
        tmp_path, monkeypatch
    )
    first = census._stream_pbf(
        source,
        census.EndpointGrid((census.Endpoint(0.0, 0.0),)),
        artifact_dir=artifact_dir,
        enforce_national_floor=False,
        minimum_artifact_free_bytes=0,
    )
    existing = first.filtered_binding.path.parent
    manifest_path = existing / "artifact-manifest.json"
    document = json.loads(manifest_path.read_bytes())
    document["filter"]["expression"] = "nwr/amenity=parking_space"
    document.pop("self_sha256")
    changed_raw = census._canonical_bytes(
        census._self_hashed_document(document)
    )
    manifest_path.write_bytes(changed_raw)
    manifest_path.chmod(0o600)
    filter_calls = sum(
        "tags-filter" in command for command, _label, _timeout in calls
    )

    with pytest.raises(census.CensusError) as raised:
        census._stream_pbf(
            source,
            census.EndpointGrid((census.Endpoint(0.0, 0.0),)),
            artifact_dir=artifact_dir,
            enforce_national_floor=False,
            minimum_artifact_free_bytes=0,
        )
    assert "filter-byte provenance" in str(raised.value)
    assert manifest_path.read_bytes() == changed_raw
    assert sum(
        "tags-filter" in command for command, _label, _timeout in calls
    ) == filter_calls + 1
    stages = list(artifact_dir.glob(".parking-filter-stage-*"))
    assert len(stages) == 1
    assert (stages[0] / "parking.pbf").read_bytes() == b"same-filtered-bytes"


def test_filtered_artifact_reuse_rejects_mode_manifest_and_byte_tampering(
        tmp_path, monkeypatch):
    source, artifact_dir, _tool, _calls = _mock_pbf_capture(
        tmp_path, monkeypatch
    )
    first = census._stream_pbf(
        source,
        census.EndpointGrid((census.Endpoint(0.0, 0.0),)),
        artifact_dir=artifact_dir,
        enforce_national_floor=False,
        minimum_artifact_free_bytes=0,
    )
    artifact = first.filtered_binding.path.parent
    pbf = artifact / "parking.pbf"
    pbf.chmod(0o640)
    with pytest.raises(census.CensusError, match="mode 0600"):
        census._stream_pbf(
            source, census.EndpointGrid((census.Endpoint(0.0, 0.0),)),
            artifact_dir=artifact_dir, enforce_national_floor=False,
            minimum_artifact_free_bytes=0,
        )
    pbf.chmod(0o600)
    original = pbf.read_bytes()
    pbf.write_bytes(original + b"tamper")
    pbf.chmod(0o600)
    with pytest.raises(census.CensusError, match="byte hash/length mismatch"):
        census._stream_pbf(
            source, census.EndpointGrid((census.Endpoint(0.0, 0.0),)),
            artifact_dir=artifact_dir, enforce_national_floor=False,
            minimum_artifact_free_bytes=0,
        )
    pbf.write_bytes(original)
    pbf.chmod(0o600)
    manifest = artifact / "artifact-manifest.json"
    manifest_document = json.loads(manifest.read_bytes())
    manifest_document["inventory"]["input_parking_identities"] += 1
    manifest.write_bytes(census._canonical_bytes(manifest_document))
    manifest.chmod(0o600)
    with pytest.raises(census.CensusError, match="self-hash mismatch"):
        census._stream_pbf(
            source, census.EndpointGrid((census.Endpoint(0.0, 0.0),)),
            artifact_dir=artifact_dir, enforce_national_floor=False,
            minimum_artifact_free_bytes=0,
        )
    assert not list(artifact_dir.glob(".parking-filter-stage-*"))


def test_exact_promotion_race_reuses_verified_winner_and_preserves_stage(
        tmp_path, monkeypatch):
    source, artifact_dir, _tool, _calls = _mock_pbf_capture(
        tmp_path, monkeypatch
    )

    def race(stage, destination):
        destination.mkdir(mode=0o700)
        for name in ("parking.pbf", "artifact-manifest.json"):
            shutil.copyfile(stage / name, destination / name)
            (destination / name).chmod(0o600)
        raise census.ArtifactDestinationExists(
            f"output destination appeared before promotion: {destination.name}"
        )

    monkeypatch.setattr(census, "_rename_stage_exclusive", race)
    result = census._stream_pbf(
        source,
        census.EndpointGrid((census.Endpoint(0.0, 0.0),)),
        artifact_dir=artifact_dir,
        enforce_national_floor=False,
        minimum_artifact_free_bytes=0,
    )
    assert result.filtered_binding.path.parent.is_dir()
    stages = list(artifact_dir.glob(".parking-filter-stage-*"))
    assert len(stages) == 1
    assert (stages[0] / "parking.pbf").is_file()


def test_osmium_timeout_is_positive_finite_propagated_and_policy_bound(
        tmp_path, monkeypatch):
    for invalid in (0, -1, float("inf"), float("nan"), True):
        with pytest.raises(census.CensusError, match="positive and finite"):
            census._validated_osmium_timeout(invalid)
    assert census._validated_osmium_timeout(7_200) == 7_200.0
    source, artifact_dir, _tool, calls = _mock_pbf_capture(
        tmp_path, monkeypatch
    )
    census._stream_pbf(
        source,
        census.EndpointGrid((census.Endpoint(0.0, 0.0),)),
        artifact_dir=artifact_dir,
        enforce_national_floor=False,
        minimum_artifact_free_bytes=0,
        osmium_timeout_s=7_200,
    )
    assert calls
    assert all(timeout == 7_200.0 for _command, _label, timeout in calls)


def test_run_census_binds_configured_osmium_timeout_into_policy(
        tmp_path, monkeypatch):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    bundle, geom_dir, fixture, live, verdicts = _write_end_to_end_inputs(inputs)
    observed = {}

    def stream(path, kind, endpoint_grid, **kwargs):
        observed.update(kwargs)
        assert kind == "pbf"
        return census._stream_fixture(
            Path(path), endpoint_grid, kwargs["sidecar_aliases"]
        )

    monkeypatch.setattr(census, "stream_parking_source", stream)
    result = census.run_census(
        bundle_path=bundle,
        geom_dir=geom_dir,
        parking_path=fixture,
        parking_kind="pbf",
        live_pool_path=live,
        verdicts_path=verdicts,
        output_dir=tmp_path / "output",
        shard_size=500,
        pbf_artifact_dir=tmp_path / "artifacts",
        osmium_timeout_s=4_321.5,
    )
    assert observed["osmium_timeout_s"] == 4_321.5
    assert result["manifest"]["algorithm"]["resource_policy"][
        "osmium_timeout_seconds"
    ] == 4_321.5


def test_filtered_inventory_and_export_form_maps_release_before_assembly(
        tmp_path, monkeypatch):
    class TrackedInventory(dict):
        __slots__ = ("__weakref__",)

    tool = _mock_pbf_tool(tmp_path)
    filtered_path = tmp_path / "filtered.pbf"
    filtered_path.write_bytes(b"filtered")
    filtered_path.chmod(0o600)
    filtered = census._capture_file_hash(
        filtered_path, role="filtered-parking-pbf"
    )
    source_inventory = {
        "node/1": {
            "source_type": "node", "closed_way": False,
            "declared_area": None,
        }
    }
    observed = {}

    def inventory(*_args, **_kwargs):
        value = TrackedInventory(source_inventory)
        observed["filtered_inventory"] = weakref.ref(value)
        return value, {}

    def stream(_command, _label, consume, **_kwargs):
        assert observed["filtered_inventory"]() is None
        consume((json.dumps(_geojson_feature(
            "n1", {"type": "Point", "coordinates": [0.0, 0.0]}
        )) + "\n").encode())

    monkeypatch.setattr(census, "_pbf_inventory", inventory)
    monkeypatch.setattr(census, "_stream_osmium_lines", stream)
    accumulator, counters, inventory_result = census._process_filtered_pbf(
        filtered,
        census.EndpointGrid((census.Endpoint(0.0, 0.0),)),
        tool,
        source_inventory,
        {},
        {"node/1"},
        enforce_national_floor=False,
        minimum_artifact_free_bytes=0,
        timeout_s=60.0,
    )
    assert counters["rejected_total"] == 0
    assert inventory_result["exact_filter_identity_reconciliation"] is True
    assert accumulator.exported_forms == {}
    assert accumulator.exported_source_forms == {}
    assert accumulator.seen_authority_aliases == {"node/1"}
    assert accumulator.seen_authority_aliases <= accumulator.sidecar_alias_allow_set


def test_pbf_artifact_directory_is_absolute_owned_private_and_space_checked(
        tmp_path, monkeypatch):
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir(mode=0o700)
    assert census._operator_artifact_directory(artifact_dir) == artifact_dir
    with pytest.raises(census.CensusError, match="must be absolute"):
        census._operator_artifact_directory("relative-artifacts")
    artifact_dir.chmod(0o777)
    with pytest.raises(census.CensusError, match="mode must be exactly 0700"):
        census._operator_artifact_directory(artifact_dir)
    artifact_dir.chmod(0o700)
    link = tmp_path / "artifact-link"
    link.symlink_to(artifact_dir, target_is_directory=True)
    with pytest.raises(census.CensusError, match="operator-owned directory"):
        census._operator_artifact_directory(link)
    usage_type = type(census.shutil.disk_usage(artifact_dir))
    monkeypatch.setattr(
        census.shutil, "disk_usage", lambda _path: usage_type(1_000, 950, 50)
    )
    with pytest.raises(census.CensusError, match="requires at least 100"):
        census._assert_artifact_free_space(artifact_dir, 100)


def test_pbf_failure_preserves_operator_stage_without_deletion(tmp_path, monkeypatch):
    source = tmp_path / "source.pbf"
    source.write_bytes(b"source")
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir(mode=0o700)
    tool = _mock_pbf_tool(tmp_path)
    inventory = {"node/1": {"source_type": "node", "closed_way": False}}
    monkeypatch.setattr(census, "_resolve_osmium", lambda *a, **k: tool)
    monkeypatch.setattr(
        census, "_pbf_metadata",
        lambda *a, **k: {
            "timestamp": "t", "replication_timestamp": None, "sequence": None,
            "replication_headers_required": False,
            "replication_headers_present": False,
        },
    )
    monkeypatch.setattr(
        census, "_pbf_inventory", lambda *a, **k: (inventory, {})
    )

    def run(command, label, **_kwargs):
        if "tags-filter" in command:
            Path(command[-1]).write_bytes(b"filtered")
        return census.subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(census, "_run_osmium", run)
    monkeypatch.setattr(
        census, "_stream_osmium_lines",
        lambda *a, **k: (_ for _ in ()).throw(census.CensusError("export failed")),
    )
    with pytest.raises(census.CensusError, match="diagnostic stage preserved"):
        census._stream_pbf(
            source,
            census.EndpointGrid(()),
            artifact_dir=artifact_dir,
            enforce_national_floor=False,
            minimum_artifact_free_bytes=0,
        )
    stages = list(artifact_dir.glob(".parking-filter-stage-*"))
    assert len(stages) == 1
    assert (stages[0] / "parking.pbf").read_bytes() == b"filtered"


def test_pbf_rejection_precedes_identity_reconciliation_diagnostic(
        tmp_path, monkeypatch):
    source = tmp_path / "source.pbf"
    source.write_bytes(b"source")
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir(mode=0o700)
    tool = _mock_pbf_tool(tmp_path)
    inventory = {"node/1": {"source_type": "node", "closed_way": False}}
    monkeypatch.setattr(census, "_resolve_osmium", lambda *a, **k: tool)
    monkeypatch.setattr(
        census, "_pbf_metadata",
        lambda *a, **k: {
            "timestamp": "t", "replication_timestamp": None, "sequence": None,
            "replication_headers_required": False,
            "replication_headers_present": False,
        },
    )
    monkeypatch.setattr(
        census, "_pbf_inventory", lambda *a, **k: (inventory, {})
    )

    def run(command, label, **_kwargs):
        if "tags-filter" in command:
            Path(command[-1]).write_bytes(b"filtered")
        return census.subprocess.CompletedProcess(command, 0, "", "")

    def stream(command, label, consume, **_kwargs):
        row = _geojson_feature(
            "n1", {"type": "Point", "coordinates": [0.0]}
        )
        consume((json.dumps(row) + "\n").encode())

    monkeypatch.setattr(census, "_run_osmium", run)
    monkeypatch.setattr(census, "_stream_osmium_lines", stream)
    with pytest.raises(census.CensusError, match="malformed_geometry"):
        census._stream_pbf(
            source,
            census.EndpointGrid(()),
            artifact_dir=artifact_dir,
            enforce_national_floor=False,
            minimum_artifact_free_bytes=0,
        )


def test_stream_timeout_kills_resistant_child_and_descendant_group(
        tmp_path, monkeypatch):
    child_pid_path = tmp_path / "descendant.pid"
    descendant = (
        "import signal,time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)"
    )
    parent = (
        "import signal,subprocess,sys,time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"p=subprocess.Popen([sys.executable,'-c',{descendant!r}]); "
        f"open({str(child_pid_path)!r},'w').write(str(p.pid)); "
        "print('ready', flush=True); time.sleep(30)"
    )
    monkeypatch.setattr(census, "OSMIUM_TIMEOUT_S", 0.1)
    monkeypatch.setattr(census, "OSMIUM_TERM_GRACE_S", 0.1)
    started = time.monotonic()
    with pytest.raises(census.CensusError, match="timed out.*terminated and reaped"):
        census._stream_osmium_lines(
            [sys.executable, "-c", parent], "resistant stream", lambda line: None
        )
    assert time.monotonic() - started < 2.0
    child_pid = int(child_pid_path.read_text())
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.02)
    else:
        pytest.fail("resistant osmium descendant survived process-group cleanup")


def test_stream_timeout_kills_descendant_after_group_leader_exits(
        tmp_path, monkeypatch):
    child_pid_path = tmp_path / "orphan-descendant.pid"
    descendant = (
        "import signal,time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)"
    )
    leader = (
        "import subprocess,sys; "
        f"p=subprocess.Popen([sys.executable,'-c',{descendant!r}]); "
        f"open({str(child_pid_path)!r},'w').write(str(p.pid)); "
        "print('leader-exiting', flush=True)"
    )
    monkeypatch.setattr(census, "OSMIUM_TIMEOUT_S", 0.1)
    monkeypatch.setattr(census, "OSMIUM_TERM_GRACE_S", 0.1)
    with pytest.raises(census.CensusError, match="timed out.*terminated and reaped"):
        census._stream_osmium_lines(
            [sys.executable, "-c", leader], "descendant pipe", lambda line: None
        )
    child_pid = int(child_pid_path.read_text())
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.02)
    else:
        pytest.fail("stdout-holding osmium descendant survived group cleanup")


def test_radius_sized_component_indexes_avoid_dense_old_cell_all_pairs():
    components = tuple(
        _component(_point_feature(f"node/{index + 1}", east_m=index * 5))
        for index in range(1_000)
    )
    review_index = census.ComponentPointIndex(components, census.CLUSTER_M)
    live_index = census.ComponentPointIndex(components, census.LIVE_POINT_M)
    assert review_index.cell_degrees == pytest.approx(
        math.degrees(census.CLUSTER_M / census.EARTH_RADIUS_M)
    )
    assert live_index.cell_degrees == pytest.approx(
        math.degrees(census.LIVE_POINT_M / census.EARTH_RADIUS_M)
    )
    assert max(len(values) for values in review_index.rows.values()) < 60
    assert max(len(values) for values in live_index.rows.values()) < 30
    pairs = list(census._candidate_pairs_by_bounds(
        components, census.CLUSTER_M
    ))
    assert 0 < len(pairs) < 50_000
    assert len(live_index.candidates(0.0, _offset(east_m=2_500)[1])) < 30


def test_multiresolution_index_and_sweep_handle_2_8km_and_6km_footprints():
    footprint_2_8km = _rectangle(
        "way/2800", west_m=-1_400, east_m=1_400,
        south_m=-1_400, north_m=1_400,
    )
    component = _component(footprint_2_8km)
    diagnostics = {}
    synthetic = census.attach_live_pins(
        (component,),
        ({"live_id": "large-centre", "lat": 0.0, "lon": 0.0},),
        diagnostics=diagnostics,
    )
    assert synthetic == []
    assert component.live_pins[0]["live_id"] == "large-centre"
    assert diagnostics["index_levels"]
    assert max(diagnostics["index_levels"]) > 0
    assert diagnostics["max_geometry_index_cells"] <= (
        census.TARGET_COMPONENT_INDEX_CELLS
    )

    footprint_6km = _component(_rectangle(
        "way/6000", west_m=-3_000, east_m=3_000,
        south_m=-3_000, north_m=3_000,
    ))
    neighbour = _component(_point_feature("node/6001", east_m=3_030))
    groups, edge_count = census.proximity_review_groups(
        (footprint_6km, neighbour)
    )
    assert edge_count == 1
    assert groups[footprint_6km.candidate_id]["group_id"] == groups[
        neighbour.candidate_id
    ]["group_id"]


def test_multiresolution_index_and_sweep_are_dateline_safe():
    geometry = {
        "type": "Polygon",
        "coordinates": [[
            [179.99, -0.01], [-179.99, -0.01], [-179.99, 0.01],
            [179.99, 0.01], [179.99, -0.01],
        ]],
    }
    latitude, longitude = census.geometry_representative(geometry)
    feature = census.ParkingFeature(
        "way/7000", "way/7000", geometry, latitude, longitude,
        {"amenity": "parking"}, (0,),
    )
    index = census.ComponentPointIndex((_component(feature),), census.LIVE_POINT_M)
    assert index.candidates(0.0, 179.999) == {0}
    assert index.candidates(0.0, -179.999) == {0}

    east = _component(_point_feature(
        "node/7001", longitude=179.9999
    ))
    west = _component(_point_feature(
        "node/7002", longitude=-179.9999
    ))
    groups, edge_count = census.proximity_review_groups((east, west))
    assert edge_count == 1
    assert groups[east.candidate_id]["group_id"] == groups[
        west.candidate_id
    ]["group_id"]


def test_multiresolution_index_has_total_not_per_footprint_cap(monkeypatch):
    large = _component(_rectangle(
        "way/8000", west_m=-3_000, east_m=3_000,
        south_m=-3_000, north_m=3_000,
    ))
    census.ComponentPointIndex((large,), census.LIVE_POINT_M)
    monkeypatch.setattr(census, "MAX_COMPONENT_INDEX_ASSOCIATIONS", 0)
    with pytest.raises(census.CensusError, match="total .*association limit"):
        census.ComponentPointIndex((large,), census.LIVE_POINT_M)


def test_endpoint_grid_is_fallback_radius_sized_and_dense_queries_are_bounded():
    endpoints = tuple(
        census.Endpoint(0.0, _offset(east_m=index * 100)[1])
        for index in range(10_000)
    )
    grid = census.EndpointGrid(endpoints)
    assert grid.cell == pytest.approx(
        math.degrees(census.FALLBACK_M / census.EARTH_RADIUS_M)
    )
    candidates = grid.candidate_ids(
        {"type": "Point", "coordinates": [_offset(east_m=500_000)[1], 0.0]},
        census.FALLBACK_M,
        limit=census.MAX_ENDPOINT_CANDIDATES_PER_FEATURE,
        context="dense endpoint test",
    )
    assert 0 < len(candidates) < 500


def test_stale_tombstone_uses_unassigned_and_split_metrics_reconcile(tmp_path):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    bundle, geom_dir, fixture, live, verdicts_path = _write_end_to_end_inputs(inputs)
    document = json.loads(verdicts_path.read_text())
    stale = _minimal_verdict_entry(
        "way/999", "DROP", latitude=40.0, longitude=40.0
    )
    stale["area"] = "retired-area-co"
    document["lots"]["way/999"] = stale
    area_less = _minimal_verdict_entry(
        "way/998", "REVIEW", latitude=41.0, longitude=41.0
    )
    area_less.pop("area")
    document["lots"]["way/998"] = area_less
    verdicts_path.write_text(json.dumps(document, separators=(",", ":")))
    result = census.run_census(
        bundle_path=bundle,
        geom_dir=geom_dir,
        parking_path=fixture,
        parking_kind="geojsonseq",
        live_pool_path=live,
        verdicts_path=verdicts_path,
        output_dir=tmp_path / "output",
        shard_size=500,
    )
    tombstones = {
        unit["verdict"]["key"]: unit
        for unit in result["work_units"]
        if unit["kind"] == "verdict_tombstone"
    }
    tombstone = tombstones["way/999"]
    assert tombstone["owner"] == {
        "jurisdiction": census.UNASSIGNED_OWNER,
        "area": census.UNASSIGNED_OWNER,
    }
    assert tombstone["prior_area_status"] == "retired"
    assert tombstones["way/998"]["prior_area_status"] == "area_less"
    counts = result["manifest"]["counts"]
    assert counts["candidate_verdict_unmatched"] + counts[
        "candidate_verdict_review"
    ] + counts["binary_verdict_candidate_clusters"] == counts[
        "current_candidate_clusters"
    ]
    assert counts["tombstone_verdict_drop"] == 1
    assert counts["tombstone_verdict_review"] == 1
    assert counts["area_less_tombstones"] == 1
    assert counts["retired_area_tombstones"] == 1
    assert counts["unassigned_candidate_clusters"] == 1
    assert counts["unassigned_work_units"] == 3
    assert (
        counts["area_less_tombstones"]
        + counts["retired_area_tombstones"]
        + counts["unassigned_candidate_clusters"]
    ) == counts["unassigned_work_units"]
    assert counts["equation_values"]["unassigned_split"] == [
        1, 1, 1, 3,
    ]
    assert sum(
        row["open_review_candidates"]
        for row in result["manifest"]["owners"].values()
    ) == counts["open_review_candidate_clusters"]


def test_unsupported_exclusive_rename_errno_has_named_diagnostic(tmp_path):
    with pytest.raises(census.CensusError, match="lacks a supported exclusive"):
        census._raise_exclusive_rename_error(census.errno.EINVAL, tmp_path / "out")
