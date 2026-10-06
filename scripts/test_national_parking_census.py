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
        assert result.counters["canonical_export_duplicates"] == 1


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
    assert manifest["sources"]["parking"]["seen_authority_aliases"] == {
        "count": 1,
        "sha256": census._canonical_hash(["node/2"]),
    }
    assert manifest["parking_stream"]["seen_authority_aliases"] == 1
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
        "closed_non_area": "LineString-required-area-copies-ignored",
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
    assert artifact["filter"]["input_inventory_command"] == (
        "tags-filter -R -f opl"
    )
    assert artifact["filter"]["closed_way_non_area_policy"] == (
        "LineString-required-area-copies-ignored"
    )
    assert artifact["filter"]["closed_way_non_area_values"] == [
        "0", "false", "no"
    ]
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


def test_coarse_envelope_rejection_avoids_exact_geometry_work_at_scale(tmp_path):
    accumulator = census.FeatureAccumulator(
        census.EndpointGrid((census.Endpoint(0.0, 0.0),)), {}
    )
    for identifier in range(1, 1_001):
        row = _geojson_feature(
            f"n{identifier}",
            {"type": "Point", "coordinates": [50.0, 50.0]},
        )
        accumulator.consume_line((json.dumps(row) + "\n").encode())
    counters = accumulator.final_counters()
    assert counters["parking_features"] == 1_000
    assert counters["coarse_outside_fallback_envelope"] == 1_000
    assert counters["outside_fallback_envelope"] == 1_000
    assert counters["exact_endpoint_distance_checks"] == 0
    assert counters["retained_canonical_features"] == 0


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
    with pytest.raises(census.CensusError, match="parking feature way/999"):
        census.normalize_feature(
            _geojson_feature("w999", geometry),
            census.EndpointGrid(()),
            {},
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


def test_topology_engine_is_exactly_shapely_206(monkeypatch):
    assert census._geometry_engine_manifest() == {
        "engine": "Shapely",
        "version": "2.0.6",
        "requirement": "Shapely==2.0.6",
        "validity": "complete Polygon/MultiPolygon topology",
    }
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
    assert "mismatched source/osmium/filter/inventory provenance" in message
    stages = list(artifact_dir.glob(".parking-filter-stage-*"))
    assert len(stages) == 1
    assert (stages[0] / "parking.pbf").read_bytes() == b"same-filtered-bytes"
    assert existing.is_dir()


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
