"""Synthetic and producer-round-trip tests for the Mols pilot validator."""
import copy
import hashlib
import importlib.util
import json
import math
import re
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

_HERE = Path(__file__).resolve().parent
_ASSEMBLE_DIR = _HERE.parent / "assemble"
sys.path.insert(0, str(_ASSEMBLE_DIR))
_SPEC = importlib.util.spec_from_file_location(
    "validate_publish_pilot_module", _HERE / "validate_publish_pilot.py")
validator = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(validator)
_ASSEMBLE_SPEC = importlib.util.spec_from_file_location(
    "roundtrip_assemble_module", _ASSEMBLE_DIR / "assemble.py")
producer = importlib.util.module_from_spec(_ASSEMBLE_SPEC)
_ASSEMBLE_SPEC.loader.exec_module(producer)
_VISUAL_SPEC = importlib.util.spec_from_file_location(
    "roundtrip_visual_module", _HERE / "build_mols_pilot_visual.py")
visual_producer = importlib.util.module_from_spec(_VISUAL_SPEC)
_VISUAL_SPEC.loader.exec_module(visual_producer)
_GOLDEN_SPEC = importlib.util.spec_from_file_location(
    "roundtrip_golden_module", _HERE / "build_mols_golden.py")
golden_producer = importlib.util.module_from_spec(_GOLDEN_SPEC)
_GOLDEN_SPEC.loader.exec_module(golden_producer)
_SERVE_DIR = _HERE.parent / "serve"
_PUBLISH_SPEC = importlib.util.spec_from_file_location(
    "roundtrip_publish_module", _SERVE_DIR / "publish_areas.py")
publish_producer = importlib.util.module_from_spec(_PUBLISH_SPEC)
_PUBLISH_SPEC.loader.exec_module(publish_producer)
import areas as area_module  # noqa: E402

try:
    from shapely.geometry import MultiPolygon, box, mapping, shape
    HAVE_SHAPELY = True
except ImportError:
    HAVE_SHAPELY = False

RELATION_ID = 7046785


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, str):
        path.write_text(value, encoding="utf-8")
    else:
        path.write_text(json.dumps(value), encoding="utf-8")


def _candidate(**changes):
    record = {
        "index_row": [
            validator.AREA_ID,
            validator.AREA_NAME,
            validator.STATE,
            56.2,
            10.6,
        ],
        "osm_relation_id": RELATION_ID,
    }
    record.update(changes)
    return record


def _discovery_report(candidates):
    return {
        "schema_version": 1,
        "requested_region_codes": ["DK"],
        "candidate_count": len(candidates),
        "candidates": candidates,
        "attribution": validator.ATTRIBUTION,
    }


def _polygon_geometry():
    return {
        "type": "Polygon",
        "coordinates": [[
            [10.4, 56.1], [10.9, 56.1], [10.9, 56.35],
            [10.4, 56.35], [10.4, 56.1],
        ]],
    }


def _relation_graph(direct=None, records=(), relation_tags=None, *,
                    scope="raw-denmark", source_sha256="b" * 64,
                    source_bytes=123):
    direct = direct or {}
    relation_tags = relation_tags or {}
    records_by_way = {record["way_id"]: record for record in records}
    relations = []
    for relation_id, way_ids in sorted(direct.items()):
        members = [
            {"sequence": index, "type": "way", "ref": way_id, "role": ""}
            for index, way_id in enumerate(way_ids)
        ]
        direct_ways = []
        for way_id in way_ids:
            record = records_by_way.get(way_id)
            if record is None:
                direct_ways.append({
                    "way_id": way_id, "status": "missing", "tags": None,
                    "node_ids": None, "coordinates": None,
                    "missing_node_ids": None,
                })
            else:
                direct_ways.append({
                    "way_id": way_id,
                    "status": ("incomplete" if record.get("missing_node_ids")
                               else "present"),
                    "tags": dict(record["tags"]),
                    "node_ids": list(record["node_ids"]),
                    "coordinates": [list(point) for point in record["coordinates"]],
                    "missing_node_ids": list(record.get("missing_node_ids") or []),
                })
        relations.append({
            "relation_id": relation_id,
            "accepted_hiking_route": True,
            "tags": dict(relation_tags.get(relation_id, {
                "name": "Mols Trail", "route": "hiking", "type": "route",
            })),
            "members": members,
            "direct_way_ids": list(way_ids),
            "direct_ways": direct_ways,
        })
    missing = sorted({
        way_id for way_ids in direct.values() for way_id in way_ids
        if way_id not in records_by_way
    })
    incomplete = sorted(
        way_id for way_id, record in records_by_way.items()
        if record.get("missing_node_ids"))
    return {
        "schema_version": 2,
        "scope": scope,
        "source": {"kind": "runner-local-osm-pbf",
                   "artifact": validator.PBF_RELATIVES_BY_SCOPE[scope],
                   "sha256": source_sha256, "bytes": source_bytes},
        "attribution": validator.ATTRIBUTION,
        "accepted_route_values": ["foot", "hiking", "running", "walking"],
        "root_relation_ids": sorted(direct),
        "relation_count": len(relations),
        "missing_relation_ids": [],
        "missing_direct_way_ids": missing,
        "incomplete_direct_way_ids": incomplete,
        "relations": relations,
    }


def _scope_receipt(scope, parent_payload, output_payload, roots, *,
                   parent_label, output_artifact, stage, counts):
    command = (
        ["osmium", "extract", "--strategy=smart", "--bbox",
         "10.40,56.08,10.90,56.35", parent_label,
         "-o", output_artifact, "--overwrite"]
        if scope == "aoi" else
        ["osmium", "getid", "-r", "--id-file", "/tmp/roots.txt",
         parent_label, "-o", f"/tmp/{scope}.osm.pbf", "--overwrite"])
    document = {
        "schema_version": 1,
        "receipt_kind": "scope",
        "scope": scope,
        "canonical_source_path_label": parent_label,
        "parent_pbf": {
            "sha256": hashlib.sha256(parent_payload).hexdigest(),
            "bytes": len(parent_payload),
            "object_counts": counts[0],
        },
        "root_relation_ids": roots,
        "root_set_sha256": validator._scope_root_hash(roots),
        "osmium": {"version": "osmium version synthetic", "command": command},
        "compact_output": {
            "artifact": output_artifact,
            "sha256": hashlib.sha256(output_payload).hexdigest(),
            "bytes": len(output_payload),
            "object_counts": counts[1],
        },
        "upstream_workflow_stage": stage,
    }
    if scope == "aoi":
        document["extraction"] = {
            "name": validator.PILOT_AOI_NAME,
            "bbox": validator.PILOT_BBOX,
            "root_bbox_sha256": validator._root_bbox_hash(roots),
        }
    return document


def _source_record(way_id, node_ids, coordinates, tags, *, direct=(),
                   missing_nodes=()):
    return {
        "way_id": way_id,
        "node_ids": list(node_ids),
        "missing_node_ids": list(missing_nodes),
        "coordinates": [list(point) for point in coordinates],
        "tags": dict(tags),
        "direct_relation_ids": list(direct),
        "renders_in_area": True,
    }


class CheckedInGoldenRecord(unittest.TestCase):
    def test_bjergetapen_template_is_validation_only_and_tolerance_free(self):
        path = _HERE.parent / "golden" / "mols-bjerge.json"
        record = json.loads(path.read_text())
        self.assertEqual(record["record_id"],
                         "mols-bjerge-bjergetapen-8350111")
        self.assertEqual(record["trail"]["osm_relation_id"], 8350111)
        self.assertIsNone(record["acceptance_tolerance"])
        self.assertFalse(validator._contains_geometry(record))
        self.assertEqual(record["osm_measurement"]["status"],
                         "generated-from-current-raw-authority-and-assembled-output")


class DiscoveryValidation(unittest.TestCase):
    def _run(self, report):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_path = root / "report.json"
            selected_path = root / "selected.json"
            index_path = root / "index.json"
            result_path = root / "result.json"
            _write(report_path, report)
            rc = validator.main([
                "discovery",
                "--discovery-report", str(report_path),
                "--expected-osm-relation-id", str(RELATION_ID),
                "--selected-record-out", str(selected_path),
                "--index-out", str(index_path),
                "--result-json", str(result_path),
            ])
            return {
                "rc": rc,
                "result": json.loads(result_path.read_text()),
                "selected": json.loads(selected_path.read_text())
                if selected_path.exists() else None,
                "index": json.loads(index_path.read_text())
                if index_path.exists() else None,
            }

    def test_success_selects_only_mols_and_writes_one_row_index(self):
        other = {
            "index_row": ["nationalpark-thy-dk", "Nationalpark Thy",
                          "Denmark", 56.9, 8.4],
            "osm_relation_id": 7045682,
        }
        result = self._run(_discovery_report([other, _candidate()]))
        self.assertEqual(result["rc"], 0)
        self.assertEqual(result["result"]["status"], "ok")
        self.assertEqual(result["selected"]["osm_relation_id"], RELATION_ID)
        self.assertEqual(result["index"], [[
            validator.AREA_ID, validator.AREA_NAME, validator.STATE,
            56.2, 10.6, None, None, RELATION_ID,
        ]])

    def test_every_target_identity_failure_is_closed_and_diagnostic(self):
        wrong_name = _candidate(index_row=[
            validator.AREA_ID, "Mols Bjerge", validator.STATE, 56.2, 10.6])
        wrong_state = _candidate(index_row=[
            validator.AREA_ID, validator.AREA_NAME, "Arizona", 56.2, 10.6])
        cases = {
            "no-target": [],
            "duplicate-target": [_candidate(), _candidate()],
            "wrong-name": [wrong_name],
            "wrong-state": [wrong_state],
            "wrong-relation": [_candidate(osm_relation_id=1)],
            "missing-relation": [_candidate(osm_relation_id=None)],
        }
        for label, candidates in cases.items():
            with self.subTest(label=label):
                result = self._run(_discovery_report(candidates))
                self.assertEqual(result["rc"], 1)
                self.assertTrue(result["result"]["errors"])
                self.assertIsNone(result["selected"])
                self.assertIsNone(result["index"])

    def test_malformed_json_still_writes_failed_result(self):
        result = self._run("{not-json")
        self.assertEqual(result["rc"], 1)
        self.assertTrue(any("invalid discovery report" in error
                            for error in result["result"]["errors"]))


class IndependentRelationScopeValidation(unittest.TestCase):
    def test_smart_completion_selectors_match_prefilter_area_policy_exactly(self):
        accepted = (
            (900, {"boundary": "protected_area"}),
            (901, {"boundary": "national_park"}),
            (902, {"leisure": "nature_reserve"}),
            (903, {"landuse": "forest"}),
            (RELATION_ID, {"type": "boundary",
                           "boundary": "national_park"}),
        )
        rejected = (
            (910, {"boundary": "administrative"}),
            (911, {"leisure": "park"}),
            (912, {"landuse": "wood"}),
            (913, {"boundary": "protected-area"}),
            (RELATION_ID, {"type": "boundary"}),
        )
        for relation_id, tags in accepted:
            with self.subTest(relation_id=relation_id, tags=tags):
                self.assertTrue(validator._is_verified_smart_completion_relation(
                    relation_id, tags))
        for relation_id, tags in rejected:
            with self.subTest(relation_id=relation_id, tags=tags):
                self.assertFalse(validator._is_verified_smart_completion_relation(
                    relation_id, tags))

    def test_root_display_name_uses_relation_then_first_eligible_main_order(self):
        expected = {
            "direct_way_members": [
                {"way_id": 10, "status": "excluded", "effective_role": "main"},
                {"way_id": 11, "status": "included", "effective_role": "main"},
                {"way_id": 12, "status": "included", "effective_role": "main"},
            ],
        }
        authority = {
            "relations": {700: {"tags": {"type": "route"}}},
            "ways": {
                10: {"tags": {"name": "Excluded Name"}},
                11: {"tags": {"name:da": "Første Rute"}},
                12: {"tags": {"name": "Second Route"}},
            },
        }
        self.assertEqual(
            validator._authoritative_root_display_name(
                700, expected, authority),
            "Første Rute")
        authority["relations"][700]["tags"]["name:en"] = "Relation Route"
        self.assertEqual(
            validator._authoritative_root_display_name(
                700, expected, authority),
            "Relation Route")

    def test_thru_hike_status_is_derived_from_raw_route_relation_edges(self):
        relations = {
            700: {
                "accepted_hiking_route": True,
                "members": [{"type": "relation", "ref": 701}],
            },
            701: {
                "accepted_hiking_route": True,
                "members": [{"type": "way", "ref": 10}],
            },
            702: {
                "accepted_hiking_route": True,
                "members": [{"type": "way", "ref": 20}],
            },
            703: {
                "accepted_hiking_route": False,
                "members": [{"type": "relation", "ref": 702}],
            },
        }
        authority = {
            "relations": relations,
            "scopes": {"raw-denmark": {"relations": relations}},
        }

        self.assertEqual(
            validator._derived_removed_thru_hike_relation_ids(authority),
            {700, 701})
        self.assertEqual(
            validator._derived_assembly_status(
                700, {"eligible_main_way_ids": [10]}, authority),
            "removed-thru-hike")
        self.assertEqual(
            validator._derived_assembly_status(
                700, {"eligible_main_way_ids": []}, authority),
            "entirely-filtered")
        self.assertEqual(
            validator._derived_assembly_status(
                702, {"eligible_main_way_ids": [20]}, authority),
            "emitted")
        self.assertEqual(
            validator._derived_assembly_status(
                702, {"eligible_main_way_ids": []}, authority),
            "entirely-filtered")

    @unittest.skipUnless(HAVE_SHAPELY, "shapely not installed")
    def test_child_relation_exact_area_relevance_outside_inside_unknown(self):
        boundary = box(-1, -1, 2, 1)
        cases = (
            ("outside", [[5.0, 0.0], [6.0, 0.0]], "present"),
            ("intersects", [[0.0, 0.0], [1.0, 0.0]], "present"),
            ("unknown", [None, None], "incomplete"),
        )
        for expected, coordinates, status in cases:
            with self.subTest(expected=expected):
                way = {
                    "way_id": 11, "status": status,
                    "coordinates": coordinates,
                }
                relations = {
                    700: {"members": [{"type": "relation", "ref": 701,
                                       "role": ""}], "direct_ways": {}},
                    701: {"members": [{"type": "way", "ref": 11,
                                       "role": "main"}],
                          "direct_ways": {11: way}},
                }
                authority = {
                    "relations": relations,
                    "scopes": {"raw-denmark": {"relations": relations}},
                }
                self.assertEqual(
                    validator._expected_relation_area_status(
                        701, authority, boundary), expected)

    def test_outside_gap_reconstruction_returns_proof_for_own_root_only(self):
        members = [
            {"way_id": 10, "status": "included", "effective_role": "main",
             "source_node_ids": [1, 2]},
            {"way_id": 11, "status": "included", "effective_role": "main",
             "source_node_ids": [3, 4]},
            {"way_id": 12, "status": "excluded", "effective_role": "main",
             "source_node_ids": [2, 3], "exact_area_status": "outside",
             "terminal_reason": None},
        ]
        for root, ancestor in ((700, 701), (701, 700)):
            with self.subTest(root=root):
                expected = {
                    "relation_id": root,
                    "relation_ids": [700, 701],
                    "direct_way_members": members,
                }
                self.assertTrue(
                    validator._audit_has_root_specific_outside_gap(
                        expected, "emitted"))
                accepted_roots = {expected["relation_id"]}
                self.assertIn(root, accepted_roots)
                self.assertNotIn(ancestor, accepted_roots)
    def test_nonrelation_candidate_cannot_fall_back_to_raw_authority(self):
        record = _source_record(
            99, [1, 2], [[10.5, 56.15], [10.6, 56.15]],
            {"name": "Raw Only", "highway": "path"})
        feature = {
            "type": "Feature",
            "properties": {
                "name": "Raw Only", "source": "name-stitch",
                "member_ways": [99], "source_geometry_way_ids": [99],
                "relation_ids": [], "root_relation_ids": [],
                "direct_relation_way_ids": {},
                "restored_relation_way_ids": [],
                "source_ways": [record], "rendered_source_way_ids": [99],
                "walking_identity": "explicit", "boundary_induced_split": False,
                "connectivity": {
                    "status": "accepted", "accepted_reasons": ["shared-osm-node"],
                    "source_components": 1, "postclip_components": 1,
                    "missing_way_ids": [], "exact_boundary_clip": True,
                },
            },
            "geometry": {"type": "MultiLineString",
                         "coordinates": [record["coordinates"]]},
        }
        authority = {"ways": {99: {
            "status": "present", "node_ids": [1, 2],
            "coordinates": record["coordinates"], "missing_node_ids": [],
            "tags": record["tags"],
        }}, "relations": {}, "accepted_relation_ids": []}
        boundary = {"geometry": _polygon_geometry()}
        errors = []

        rebuilt = validator._candidate_source_evidence(
            feature, boundary, {}, authority, "raw-only stitch", errors)

        self.assertEqual(rebuilt["missing_way_ids"], [99])
        self.assertTrue(any("unavailable raw evidence w99" in error
                            for error in errors), errors)

    def test_relation_claimed_pedestrian_ring_needs_no_standalone_removal(self):
        coordinates = [
            (10.7, 56.2), (10.71, 56.2),
            (10.71, 56.21), (10.7, 56.2),
        ]
        raw_ways = {90: {
            "node_ids": [1, 2, 3, 1],
            "coordinates": coordinates,
            "tags": {"name": "Signed Plaza", "highway": "pedestrian",
                     "area": "yes"},
        }}
        trails = [{
            "type": "Feature",
            "properties": {
                "source": "relation", "rendered_source_way_ids": [90],
                "member_ways": [90],
            },
            "geometry": {"type": "MultiLineString",
                         "coordinates": [coordinates]},
        }]
        errors = []

        validator._validate_maltgaarden(
            trails, [], raw_ways, {90}, errors)

        self.assertEqual(errors, [])


class _QA:
    def __init__(self, root):
        self.root = root
        self.scope_trust_path = root.parent / "scope-trust.json"
        coordinates = [[10.5, 56.15], [10.6, 56.2]]
        self.trail = {
            "type": "Feature",
            "properties": {
                "name": "Mols Trail",
                "area": validator.AREA_NAME,
                "kind": "trail",
                "source": "name-stitch",
                "length_mi": 1.0,
                "member_ways": [10],
                "ckey": "w10",
                "quality_candidate_index": 0,
                "quality_disposition": "kept",
                "boundary_induced_split": False,
                "relation_ids": [],
                "direct_relation_way_ids": {},
                "source_geometry_way_ids": [10],
                "restored_relation_way_ids": [],
                "source_ways": [_source_record(
                    10, [1, 2], coordinates,
                    {"name": "Mols Trail", "highway": "path"})],
                "walking_identity": "explicit",
                "rendered_source_way_ids": [10],
                "connectivity": {
                    "status": "accepted",
                    "accepted_reasons": ["shared-osm-node"],
                    "source_components": 1,
                    "postclip_components": 1,
                    "missing_way_ids": [],
                    "exact_boundary_clip": True,
                },
            },
            "geometry": {
                "type": "MultiLineString",
                "coordinates": [coordinates],
            },
        }
        candidate = _candidate()
        selected = {
            "schema_version": 1,
            **candidate,
            "attribution": validator.ATTRIBUTION,
        }
        area_record = {
            "area_id": validator.AREA_ID,
            "name": validator.AREA_NAME,
            "osm_relation_id": RELATION_ID,
            "trail_count": 1,
            "total_miles": 1.0,
        }
        pbf_payloads = {
            "raw-denmark": b"synthetic-runner-local-raw-slice",
            "prefiltered-denmark": b"synthetic-runner-local-prefilter-slice",
            "aoi": b"synthetic-runner-local-aoi-pbf",
        }
        pbf_bytes = pbf_payloads["aoi"]
        pbf_sha = hashlib.sha256(pbf_bytes).hexdigest()
        golden_coordinates = [[10.5001, 56.2], [10.503, 56.2]]
        golden_source = _source_record(
            835, [8351, 8352], golden_coordinates,
            {"name": "Mols Bjerge-stien Bjergetapen",
             "highway": "path"}, direct=[8350111])
        golden_direct = {8350111: [835]}
        golden_tags = {8350111: {
            "name": "Mols Bjerge-stien Bjergetapen",
            "route": "hiking", "type": "route",
        }}
        raw_graph = _relation_graph(
            golden_direct, [golden_source], relation_tags=golden_tags,
            scope="raw-denmark",
            source_sha256=hashlib.sha256(
                pbf_payloads["raw-denmark"]).hexdigest(),
            source_bytes=len(pbf_payloads["raw-denmark"]))
        prefilter_graph = _relation_graph(
            golden_direct, [golden_source], relation_tags=golden_tags,
            scope="prefiltered-denmark",
            source_sha256=hashlib.sha256(
                pbf_payloads["prefiltered-denmark"]).hexdigest(),
            source_bytes=len(pbf_payloads["prefiltered-denmark"]))
        aoi_graph = _relation_graph(
            golden_direct, [golden_source], relation_tags=golden_tags,
            scope="aoi", source_sha256=hashlib.sha256(pbf_bytes).hexdigest(),
            source_bytes=len(pbf_bytes))
        raw_parent = b"synthetic-full-raw-denmark-parent"
        prefilter_parent = b"synthetic-full-prefilter-parent"
        raw_parent_counts = {"nodes": 1000, "ways": 400, "relations": 80}
        prefilter_parent_counts = {"nodes": 600, "ways": 220, "relations": 40}
        raw_scope_receipt = _scope_receipt(
            "raw-denmark", raw_parent, pbf_payloads["raw-denmark"],
            [8350111], parent_label="data/raw/denmark.osm.pbf",
            output_artifact=validator.RAW_PBF_RELATIVE,
            stage="relation-root-closure-extract",
            counts=(raw_parent_counts,
                    {"nodes": 12, "ways": 3, "relations": 1}))
        prefilter_scope_receipt = _scope_receipt(
            "prefiltered-denmark", prefilter_parent,
            pbf_payloads["prefiltered-denmark"], [8350111],
            parent_label="data/hiking.osm.pbf",
            output_artifact=validator.PREFILTER_PBF_RELATIVE,
            stage="relation-root-closure-extract",
            counts=(prefilter_parent_counts,
                    {"nodes": 10, "ways": 2, "relations": 1}))
        aoi_scope_receipt = _scope_receipt(
            "aoi", prefilter_parent, pbf_payloads["aoi"], [8350111],
            parent_label="data/hiking.osm.pbf",
            output_artifact=validator.AOI_PBF_RELATIVE,
            stage="aoi-extract",
            counts=(prefilter_parent_counts,
                    {"nodes": 8, "ways": 3, "relations": 2}))
        prefilter_transform_receipt = {
            "schema_version": 1,
            "receipt_kind": "transformation",
            "scope": "prefiltered-denmark",
            "canonical_source_path_label": "data/raw/denmark.osm.pbf",
            "parent_pbf": raw_scope_receipt["parent_pbf"],
            "osmium": {
                "version": "osmium version synthetic",
                "command": validator._pinned_prefilter_command(),
            },
            "output_pbf": {
                "artifact": "data/hiking.osm.pbf",
                **prefilter_scope_receipt["parent_pbf"],
            },
            "upstream_workflow_stage": "prefilter",
        }
        golden_miles = round(validator._linework_miles([golden_coordinates]), 3)
        self.golden_trail = {
            "type": "Feature",
            "properties": {
                "name": "Mols Bjerge-stien Bjergetapen",
                "area": validator.AREA_NAME,
                "kind": "trail",
                "source": "relation",
                "length_mi": golden_miles,
                "member_ways": [835],
                "ckey": "w835",
                "quality_candidate_index": 1,
                "quality_disposition": "kept",
                "boundary_induced_split": False,
                "relation_ids": [8350111],
                "root_relation_ids": [8350111],
                "direct_relation_way_ids": {8350111: [835]},
                "source_geometry_way_ids": [835],
                "restored_relation_way_ids": [],
                "source_ways": [golden_source],
                "walking_identity": "explicit",
                "rendered_source_way_ids": [835],
                "connectivity": {
                    "status": "accepted",
                    "accepted_reasons": ["shared-osm-node"],
                    "source_components": 1,
                    "postclip_components": 1,
                    "missing_way_ids": [],
                    "exact_boundary_clip": True,
                },
            },
            "geometry": {
                "type": "MultiLineString",
                "coordinates": [golden_coordinates],
            },
        }
        app_trail = {
            "id": "mols-trail", "name": "Mols Trail",
            "distanceMi": 1.0, "difficulty": "Easy",
            "segments": [[[56.15, 10.5], [56.2, 10.6]]],
        }
        app_row = {
            "id": validator.AREA_ID,
            "name": validator.AREA_NAME,
            "state": validator.STATE,
            "center_lat": 56.2,
            "center_lon": 10.6,
            "zoom": 13,
            "bbox": [10.5, 56.15, 10.6, 56.2],
            "trails": [app_trail],
            "trail_count": 1,
            "total_mi": 1.0,
            "osm_relation_id": RELATION_ID,
        }
        visual_text = (
            '<svg xmlns="http://www.w3.org/2000/svg">\n'
            '<g id="raw-osm-context"/><g id="exact-boundary"/>'
            '<g id="safe-relation-members"/>'
            '<g id="unsafe-relation-members"/>'
            '<g id="unresolved-endpoints"/></svg>\n')
        visual_raw = visual_text.encode("utf-8")
        self.documents = {
            "manifest": {
                "schema_version": 1,
                "branch_ref": "chat/denmark-trails",
                "source_sha": "a" * 40,
                "inputs": validator._expected_inputs(RELATION_ID),
                "visual_review_url": validator.VISUAL_REVIEW_URL,
                "attribution": validator.ATTRIBUTION,
                "artifacts": {},
            },
            "raw_relation_members": raw_graph,
            "prefilter_relation_members": prefilter_graph,
            "aoi_relation_members": aoi_graph,
            "raw_scope_receipt": raw_scope_receipt,
            "prefilter_scope_receipt": prefilter_scope_receipt,
            "aoi_scope_receipt": aoi_scope_receipt,
            "prefilter_transform_receipt": prefilter_transform_receipt,
            "raw": {
                "type": "FeatureCollection",
                "features": [{
                    "type": "Feature",
                    "id": "w10",
                    "properties": {"name": "Mols Trail", "highway": "path"},
                    "geometry": {"type": "LineString", "coordinates": coordinates},
                }, {
                    "type": "Feature",
                    "id": "w835",
                    "properties": copy.deepcopy(golden_source["tags"]),
                    "geometry": {
                        "type": "LineString",
                        "coordinates": copy.deepcopy(golden_coordinates),
                    },
                }, {
                    "type": "Feature",
                    "id": "w1027606528",
                    "properties": {
                        "name": "Maltgården", "highway": "pedestrian",
                        "surface": "sett",
                    },
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[
                            [10.7, 56.2], [10.71, 56.2],
                            [10.71, 56.21], [10.7, 56.2],
                        ]],
                    },
                }],
            },
            "raw_way_topology": {
                "schema_version": 3,
                "source": {
                    "kind": "runner-local-osm-pbf",
                    "artifact": validator.AOI_PBF_RELATIVE,
                    "sha256": pbf_sha,
                    "bytes": len(pbf_bytes),
                },
                "attribution": validator.ATTRIBUTION,
                "way_count": 3,
                "incomplete_way_ids": [],
                "ways": [{
                    "way_id": 10,
                    "tags": {"name": "Mols Trail", "highway": "path"},
                    "node_ids": [1, 2],
                    "coordinates": copy.deepcopy(coordinates),
                    "missing_node_ids": [],
                }, {
                    "way_id": 835,
                    "tags": copy.deepcopy(golden_source["tags"]),
                    "node_ids": [8351, 8352],
                    "coordinates": copy.deepcopy(golden_coordinates),
                    "missing_node_ids": [],
                }, {
                    "way_id": 1027606528,
                    "tags": {
                        "name": "Maltgården", "highway": "pedestrian",
                        "surface": "sett",
                    },
                    "node_ids": [9001, 9002, 9003, 9001],
                    "coordinates": [
                        [10.7, 56.2], [10.71, 56.2],
                        [10.71, 56.21], [10.7, 56.2],
                    ],
                    "missing_node_ids": [],
                }],
                "relation_count": 2,
                "relations": [{
                    "relation_id": RELATION_ID,
                    "tags": {
                        "name": validator.AREA_NAME,
                        "boundary": "national_park",
                        "type": "boundary",
                    },
                    "members": [],
                }, {
                    "relation_id": 8350111,
                    "tags": copy.deepcopy(golden_tags[8350111]),
                    "members": [{
                        "sequence": 0,
                        "type": "way",
                        "ref": 835,
                        "role": "",
                    }],
                }],
                "destination_poi_count": 0,
                "destination_pois": [],
            },
            "trails": {"type": "FeatureCollection",
                       "features": [self.trail, self.golden_trail]},
            "removed": {"type": "FeatureCollection", "features": [{
                "type": "Feature",
                "properties": {
                    "name": "Maltgården",
                    "source": "name-stitch",
                    "member_ways": [1027606528],
                    "removed_category": "standalone-pedestrian-area",
                    "removed_reason": (
                        "Closed standalone highway=pedestrian OSM area; "
                        "retained as its exterior source-ring diagnostic, not "
                        "emitted as a completion trail."),
                },
                "geometry": {
                    "type": "MultiLineString",
                    "coordinates": [[
                        [10.7, 56.2], [10.71, 56.2],
                        [10.71, 56.21], [10.7, 56.2],
                    ]],
                },
            }]},
            "ingest_dropped": {"type": "FeatureCollection", "features": []},
            "areas": {
                "type": "FeatureCollection",
                "features": [{
                    "type": "Feature",
                    "properties": {
                        "name": validator.AREA_NAME,
                        "osm_type": "relation",
                        "osm_id": RELATION_ID,
                    },
                    "geometry": _polygon_geometry(),
                }],
            },
            "exact_area": {
                "type": "FeatureCollection",
                "features": [{
                    "type": "Feature",
                    "properties": {
                        "name": validator.AREA_NAME,
                        "osm_type": "relation",
                        "osm_id": RELATION_ID,
                        "geometry_sha256": validator._publisher_json_sha256(
                            _polygon_geometry()),
                    },
                    "geometry": _polygon_geometry(),
                }],
            },
            "curation": {"w10": "kept"},
            "curation_diff": {
                "has_baseline": False,
                "new_removed": [],
                "new_kept": [],
                "reason_changed": [],
            },
            "selected_discovery": selected,
            "discovery_report": _discovery_report([candidate]),
            "assembly_report": {
                "schema_version": 1,
                "status": "ok",
                "failure": None,
                "exact_area_required": True,
                "area_query": validator.AREA_NAME,
                "expected_area_relation_id": RELATION_ID,
                "input_pbf": {
                    "sha256": pbf_sha,
                    "bytes": len(pbf_bytes),
                },
                "relation_authority": {
                    "mode": "raw-primary-prefilter-witness-aoi-render-scope",
                    "root_relation_ids": [8350111],
                    "scopes": {
                        "raw-denmark": raw_graph["source"],
                        "prefiltered-denmark": prefilter_graph["source"],
                        "aoi": aoi_graph["source"],
                    },
                },
                "boundary_match_count": 1,
                "boundary_matches": [{
                    "name": validator.AREA_NAME,
                    "osm_type": "relation",
                    "osm_id": RELATION_ID,
                }],
                "boundary": {
                    "name": validator.AREA_NAME,
                    "osm_type": "relation",
                    "osm_id": RELATION_ID,
                },
                "boundary_geometry_sha256": validator._publisher_json_sha256(
                    _polygon_geometry()),
                "clip_applied": True,
                "min_length_mi": validator.PILOT_MIN_LENGTH_MI,
                "min_inside_mi": validator.PILOT_MIN_INSIDE_MI,
                "pre_clip_trail_count": 2,
                "post_clip_trail_count": 2,
                "assembled_trail_count": 2,
                "coverage": {
                    "raw_trailish_ways": 2,
                    "named_trailish_ways": 1,
                    "hiking_route_relations": 1,
                    "route_relations_total": 1,
                    "destination_pois": 0,
                },
                "quality": {
                    "schema_version": 7,
                    "enabled": True,
                    "region": "dk",
                    "area": validator.AREA_NAME,
                    "connectivity_authority":
                    "shared-osm-node-or-exact-boundary-clip",
                    "overlap_threshold": 0.9,
                    "candidate_count": 2,
                    "kept_count": 2,
                    "removed_count": 0,
                    "removed_counts": {},
                    "signed_relation_unresolved_count": 0,
                    "signed_relation_unresolved_miles": 0,
                    "signed_relation_unresolved": [],
                    "relation_member_audit": [],
                    "relation_member_unresolved_count": 0,
                    "relation_member_unresolved": [],
                    "restored_road_policy":
                    "diagnostic-only-member-safety-gated",
                    "restored_road_relations": [],
                    "restored_road_aggregate": {
                        "restored_way_count": 0,
                        "restored_miles": 0.0,
                        "total_relation_miles": 0.0,
                        "restored_share": 0.0,
                    },
                    "overlap_evidence": [],
                    "remaining_overlaps": [],
                    "terminal_absorption_unresolved_count": 0,
                    "terminal_absorption_unresolved": [],
                    "promotion_decisions": [{
                        "candidate_index": 1,
                        "candidate_ckey": "w835",
                        "root_relation_ids": [8350111],
                        "decision": "declined",
                        "reason": "not-route-candidate",
                        "skipped_ambiguous_pois": [],
                    }],
                    "validation_failures": [],
                },
            },
            "publish_report": {
                "schema_version": 1,
                "state": validator.STATE,
                "exact_boundary": {
                    "enabled": True,
                    "name": validator.AREA_NAME,
                    "osm_type": "relation",
                    "osm_id": RELATION_ID,
                    "geometry_sha256": validator._publisher_json_sha256(
                        _polygon_geometry()),
                },
                "dry_run": True,
                "write_mode": "dry-run",
                "canonical_write": False,
                "preview": {
                    "enabled": True,
                    "area_count": 1,
                    "area_ids": [validator.AREA_ID],
                    "assembly_feature_count": 1,
                    "assembly_property_miles": 1.0,
                    "preview_distance_miles": 1.0,
                    "publisher_total_miles": 1.0,
                    "trails": [{
                        "id": "mols-trail", "name": "Mols Trail",
                        "distance_mi": 1.0, "assembly_length_mi": 1.0,
                    }],
                },
                "index_area_count": 1,
                "validated_areas": [copy.deepcopy(area_record)],
                "published_areas": [copy.deepcopy(area_record)],
                "skipped_areas": [],
                "validation_failures": [],
                "routes": {"kept": 0, "dropped": 0},
            },
            "preview": {
                "schema_version": 1,
                "state": validator.STATE,
                "write_mode": "dry-run-preview",
                "canonical_write": False,
                "sealed_inputs": [{
                    "area_id": validator.AREA_ID,
                    "nonhiking_sidecar_entry": {},
                    "prior_output_exists": False,
                    "prior_parking": None,
                }],
                "areas": [app_row],
            },
            "golden": json.loads(
                (_HERE.parent / "golden" / "mols-bjerge.json").read_text()),
            "visual_review": {
                "schema_version": 1,
                "artifact": validator.VISUAL_RELATIVE,
                "sha256": hashlib.sha256(visual_raw).hexdigest(),
                "bytes": len(visual_raw),
                "geometry_source": "OpenStreetMap",
                "external_network_resources": False,
                "raster_or_external_tiles": False,
                "layers": {
                    "raw_context_paths": 1,
                    "exact_boundary_paths": 1,
                    "emitted_paths": 1,
                    "removed_paths": 1,
                    "safe_relation_member_paths": 1,
                    "unsafe_relation_member_paths": 1,
                    "unresolved_endpoint_markers": 1,
                },
                "kiro_disposition": "generated-and-structurally-validated",
                "human_disposition": "not-yet-recorded",
                "attribution": validator.ATTRIBUTION,
            },
        }
        parsed_scopes = {}
        for label, scope in (
                ("raw_relation_members", "raw-denmark"),
                ("prefilter_relation_members", "prefiltered-denmark"),
                ("aoi_relation_members", "aoi")):
            parse_errors = []
            parsed_scopes[scope] = validator._validate_relation_ledger_v2(
                self.documents[label], scope, parse_errors)
            if parse_errors:
                raise AssertionError(parse_errors)
        base_authority = parsed_scopes["raw-denmark"]
        base_authority["scopes"] = parsed_scopes
        base_audit = validator._expected_relation_audit(
            8350111, base_authority,
            shape(self.documents["areas"]["features"][0]["geometry"]))
        base_audit["assembly_status"] = "emitted"
        base_audit["emitted_in_exact_area"] = True
        base_audit["review_status"] = "accepted"
        base_audit["unresolved_reasons"] = []
        base_quality = self.documents["assembly_report"]["quality"]
        base_quality["relation_member_audit"] = [base_audit]
        golden_measurements = validator._restored_measurements(
            self.golden_trail,
            [{**golden_source, "_raw_bound": True}], [],
            {8350111: [835]},
            shape(self.documents["areas"]["features"][0]["geometry"]))
        base_quality["restored_road_relations"] = [
            golden_measurements[0][0]]
        base_quality["restored_road_aggregate"] = {
            "restored_way_count": 0,
            "restored_miles": 0.0,
            "total_relation_miles": round(sum(
                golden_measurements[0][3].values()), 6),
            "restored_share": 0.0,
        }
        self._sync_golden()
        self._sync_preview()
        raw_svg, visual_review = visual_producer.build_svg(
            raw=self.documents["raw"], areas=self.documents["areas"],
            trails=self.documents["trails"], removed=self.documents["removed"],
            authority=self.documents["raw_relation_members"],
            assembly=self.documents["assembly_report"])
        self.documents["visual_review"] = visual_review
        visual_text = raw_svg.decode("utf-8")
        for label, relative in validator.REQUIRED_JSON_FILES.items():
            _write(root / relative, self.documents[label])
        _write(root / "README.md",
               f"Visual review: {validator.VISUAL_REVIEW_URL}\n\n"
               f"Attribution: {validator.ATTRIBUTION}\n")
        _write(root / "discovery/discovery.log", "discovery completed\n")
        _write(root / "reports/publish.log", "publisher completed\n")
        _write(root / "viewer/index.html", "<!doctype html><title>QA</title>\n")
        _write(root / "viewer/serve.py", "print('serve')\n")
        _write(root / validator.VISUAL_RELATIVE, visual_text)
        for scope, relative in validator.PBF_RELATIVES_BY_SCOPE.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(pbf_payloads[scope])
        self._refresh_manifest_seals()
        self._refresh_scope_trust()

    def _sync_golden(self):
        template = json.loads(
            (_HERE.parent / "golden" / "mols-bjerge.json").read_text())
        self.documents["golden"] = golden_producer.build_record(
            template, self.documents["raw_relation_members"],
            self.documents["trails"],
            self.documents["manifest"]["source_sha"])
        _write(self.root / validator.GOLDEN_RELATIVE,
               self.documents["golden"])

    def _sync_visual(self):
        raw_svg, review = visual_producer.build_svg(
            raw=self.documents["raw"], areas=self.documents["areas"],
            trails=self.documents["trails"], removed=self.documents["removed"],
            authority=self.documents["raw_relation_members"],
            assembly=self.documents["assembly_report"])
        self.documents["visual_review"] = review
        (self.root / validator.VISUAL_RELATIVE).write_bytes(raw_svg)
        _write(self.root / validator.VISUAL_REVIEW_RELATIVE, review)

    def write_synchronized_visual(self, text: str, *, layers=None):
        raw = text.encode("utf-8")
        (self.root / validator.VISUAL_RELATIVE).write_bytes(raw)
        review = self.documents["visual_review"]
        review["sha256"] = hashlib.sha256(raw).hexdigest()
        review["bytes"] = len(raw)
        if layers is not None:
            review["layers"] = layers
        _write(self.root / validator.VISUAL_REVIEW_RELATIVE, review)
        self._refresh_manifest_seals()

    def set_exact_geometry(self, geometry):
        self.documents["areas"]["features"][0]["geometry"] = copy.deepcopy(
            geometry)
        exact = self.documents["exact_area"]["features"][0]
        exact["geometry"] = copy.deepcopy(geometry)
        geometry_sha256 = validator._publisher_json_sha256(geometry)
        exact["properties"]["geometry_sha256"] = geometry_sha256
        self.documents["assembly_report"][
            "boundary_geometry_sha256"] = geometry_sha256
        self.rewrite("areas")
        self.rewrite("exact_area")
        self.rewrite("assembly_report")

    def set_topology_way(self, way_id, *, tags, node_ids, coordinates):
        values = [value for value in self.documents["raw_way_topology"]["ways"]
                  if value["way_id"] != way_id]
        values.append({
            "way_id": way_id,
            "tags": copy.deepcopy(tags),
            "node_ids": list(node_ids),
            "coordinates": copy.deepcopy(coordinates),
            "missing_node_ids": [],
        })
        values.sort(key=lambda value: value["way_id"])
        self.documents["raw_way_topology"].update({
            "way_count": len(values),
            "incomplete_way_ids": [],
            "ways": values,
        })
        self.rewrite("raw_way_topology")
        self._sync_scope_receipts()
        self._refresh_manifest_seals()
        self._refresh_scope_trust()

    def set_topology_destination(self, node_id, *, tags, coordinate,
                                 refresh_trust=True):
        values = [
            value for value in self.documents["raw_way_topology"][
                "destination_pois"]
            if value["node_id"] != node_id
        ]
        values.append({
            "node_id": node_id,
            "tags": copy.deepcopy(tags),
            "name": validator._destination_display_name(tags),
            "coordinate": list(coordinate),
            "eligibility_class": validator._destination_eligibility_class(tags),
        })
        values.sort(key=lambda value: value["node_id"])
        self.documents["raw_way_topology"].update({
            "destination_poi_count": len(values),
            "destination_pois": values,
        })
        raw_features = [
            feature for feature in self.documents["raw"]["features"]
            if feature.get("id") != f"n{node_id}"
        ]
        raw_features.append({
            "type": "Feature",
            "id": f"n{node_id}",
            "properties": copy.deepcopy(tags),
            "geometry": {
                "type": "Point", "coordinates": list(coordinate),
            },
        })
        self.documents["raw"]["features"] = raw_features
        topology = self.documents["raw_way_topology"]
        topology_node_ids = {
            value for way in topology["ways"]
            for value in way.get("node_ids", [])
        } | {value["node_id"] for value in topology["destination_pois"]}
        self.documents["aoi_scope_receipt"]["compact_output"][
            "object_counts"]["nodes"] = len(topology_node_ids)
        self.rewrite("raw")
        self.rewrite("raw_way_topology")
        self.rewrite("aoi_scope_receipt")
        self._refresh_manifest_seals()
        if refresh_trust:
            self._refresh_scope_trust()

    def set_topology_relation(self, relation_id, *, tags, members):
        values = [
            value for value in self.documents["raw_way_topology"]["relations"]
            if value["relation_id"] != relation_id
        ]
        values.append({
            "relation_id": relation_id,
            "tags": copy.deepcopy(tags),
            "members": [
                {"sequence": sequence, **copy.deepcopy(member)}
                for sequence, member in enumerate(members)
            ],
        })
        values.sort(key=lambda value: value["relation_id"])
        self.documents["raw_way_topology"].update({
            "relation_count": len(values),
            "relations": values,
        })
        self.rewrite("raw_way_topology")
        self._sync_scope_receipts()
        self._refresh_manifest_seals()
        self._refresh_scope_trust()

    def _sync_scope_receipts(self):
        roots = self.documents["raw_relation_members"]["root_relation_ids"]
        records = (
            ("raw-denmark", "raw_scope_receipt", "raw_relation_members"),
            ("prefiltered-denmark", "prefilter_scope_receipt",
             "prefilter_relation_members"),
            ("aoi", "aoi_scope_receipt", "aoi_relation_members"),
        )
        for scope, receipt_label, ledger_label in records:
            receipt = self.documents[receipt_label]
            receipt["root_relation_ids"] = list(roots)
            receipt["root_set_sha256"] = validator._scope_root_hash(roots)
            relative = validator.PBF_RELATIVES_BY_SCOPE[scope]
            payload = (self.root / relative).read_bytes()
            output_key = "compact_output"
            receipt[output_key].update({
                "artifact": relative,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
            })
            self.documents[ledger_label]["source"].update({
                "kind": "runner-local-osm-pbf",
                "artifact": relative,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
            })
            if scope == "aoi":
                receipt["extraction"] = {
                    "name": validator.PILOT_AOI_NAME,
                    "bbox": validator.PILOT_BBOX,
                    "root_bbox_sha256": validator._root_bbox_hash(roots),
                }
                topology = self.documents.get("raw_way_topology", {})
                ways = topology.get("ways") or []
                if ways:
                    receipt[output_key]["object_counts"].update({
                        "nodes": len({
                            node_id for way in ways
                            for node_id in way.get("node_ids", [])
                        } | {
                            poi["node_id"] for poi in
                            topology.get("destination_pois", [])
                        }),
                        "ways": len(ways),
                        "relations": len(
                            topology.get("relations") or []),
                    })
            _write(self.root / validator.REQUIRED_JSON_FILES[receipt_label],
                   receipt)

    def _synchronized_scope_relabel(self, mapping):
        payloads = {
            scope: (self.root / relative).read_bytes()
            for scope, relative in validator.PBF_RELATIVES_BY_SCOPE.items()
        }
        receipt_labels = {
            "raw-denmark": "raw_scope_receipt",
            "prefiltered-denmark": "prefilter_scope_receipt",
            "aoi": "aoi_scope_receipt",
        }
        ledger_labels = {
            "raw-denmark": "raw_relation_members",
            "prefiltered-denmark": "prefilter_relation_members",
            "aoi": "aoi_relation_members",
        }
        source_counts = {
            scope: copy.deepcopy(
                self.documents[receipt_labels[scope]]["compact_output"][
                    "object_counts"])
            for scope in validator.PBF_RELATIVES_BY_SCOPE
        }
        for target, source in mapping.items():
            relative = validator.PBF_RELATIVES_BY_SCOPE[target]
            (self.root / relative).write_bytes(payloads[source])
            identity = {
                "kind": "runner-local-osm-pbf",
                "artifact": relative,
                "sha256": hashlib.sha256(payloads[source]).hexdigest(),
                "bytes": len(payloads[source]),
            }
            self.documents[ledger_labels[target]]["source"] = identity
            receipt = self.documents[receipt_labels[target]]
            receipt["compact_output"].update({
                "artifact": relative,
                "sha256": identity["sha256"],
                "bytes": identity["bytes"],
                "object_counts": copy.deepcopy(source_counts[source]),
            })
            _write(
                self.root / validator.REQUIRED_JSON_FILES[
                    receipt_labels[target]], receipt)
            _write(
                self.root / validator.REQUIRED_JSON_FILES[
                    ledger_labels[target]],
                self.documents[ledger_labels[target]])
        report = self.documents["assembly_report"]
        report["relation_authority"]["scopes"] = {
            scope: copy.deepcopy(
                self.documents[ledger_labels[scope]]["source"])
            for scope in validator.PBF_RELATIVES_BY_SCOPE
        }
        if "aoi" in mapping:
            aoi_identity = self.documents["aoi_relation_members"]["source"]
            self.documents["raw_way_topology"]["source"] = \
                copy.deepcopy(aoi_identity)
            report["input_pbf"] = {
                key: aoi_identity[key] for key in ("sha256", "bytes")}
            _write(
                self.root /
                validator.REQUIRED_JSON_FILES["raw_way_topology"],
                self.documents["raw_way_topology"])
        _write(
            self.root / validator.REQUIRED_JSON_FILES["assembly_report"],
            report)
        if "raw-denmark" in mapping:
            self._sync_golden()
        self._sync_visual()
        self._refresh_manifest_seals()

    def _refresh_scope_trust(self):
        def identity(path):
            raw = path.read_bytes()
            return {
                "sha256": hashlib.sha256(raw).hexdigest(),
                "bytes": len(raw),
            }

        scopes = {}
        for scope, receipt_label, ledger_label in (
                ("raw-denmark", "raw_scope_receipt",
                 "raw_relation_members"),
                ("prefiltered-denmark", "prefilter_scope_receipt",
                 "prefilter_relation_members"),
                ("aoi", "aoi_scope_receipt", "aoi_relation_members")):
            receipt = self.documents[receipt_label]
            record = {
                "parent": {
                    key: receipt["parent_pbf"][key]
                    for key in ("sha256", "bytes")
                },
                "slice": identity(
                    self.root / validator.PBF_RELATIVES_BY_SCOPE[scope]),
                "receipt": identity(
                    self.root / validator.REQUIRED_JSON_FILES[receipt_label]),
                "relation_ledger": identity(
                    self.root / validator.REQUIRED_JSON_FILES[ledger_label]),
                "root_set_sha256": receipt["root_set_sha256"],
            }
            if scope == "aoi":
                record["root_bbox_sha256"] = receipt["extraction"][
                    "root_bbox_sha256"]
                record["way_topology"] = identity(
                    self.root / validator.RAW_WAY_TOPOLOGY_RELATIVE)
            scopes[scope] = record
        transform = self.documents["prefilter_transform_receipt"]
        trust = {
            "schema_version": 1,
            "pilot": {
                "name": validator.PILOT_AOI_NAME,
                "bbox": validator.PILOT_BBOX,
            },
            "scopes": scopes,
            "prefilter_transformation": {
                "input": {
                    key: transform["parent_pbf"][key]
                    for key in ("sha256", "bytes")
                },
                "output": {
                    key: transform["output_pbf"][key]
                    for key in ("sha256", "bytes")
                },
                "receipt": identity(
                    self.root /
                    validator.PREFILTER_TRANSFORM_RECEIPT_RELATIVE),
            },
        }
        _write(self.scope_trust_path, trust)

    def _refresh_manifest_seals(self):
        relatives = sorted([
            *[relative for label, relative in validator.REQUIRED_JSON_FILES.items()
              if label != "manifest"],
            *validator.REQUIRED_TEXT_FILES.values(),
            *validator.PBF_RELATIVES_BY_SCOPE.values(),
        ])
        optional = self.root / validator.OPTIONAL_DROPPED_ROUTES
        if optional.is_file():
            relatives.append(validator.OPTIONAL_DROPPED_ROUTES)
        scopes = {
            validator.RAW_RELATION_MEMBERS_RELATIVE: "raw-denmark",
            validator.PREFILTER_RELATION_MEMBERS_RELATIVE:
                "prefiltered-denmark",
            validator.AOI_RELATION_MEMBERS_RELATIVE: "aoi-render-scope",
            validator.RAW_PBF_RELATIVE: "raw-denmark",
            validator.PREFILTER_PBF_RELATIVE: "prefiltered-denmark",
            validator.AOI_PBF_RELATIVE: "aoi-render-scope",
            validator.RAW_WAY_TOPOLOGY_RELATIVE: "aoi-source-topology",
            validator.EXACT_AREA_RELATIVE: "exact-area-geometry",
            validator.RAW_SCOPE_RECEIPT_RELATIVE: "raw-scope-provenance",
            validator.PREFILTER_SCOPE_RECEIPT_RELATIVE:
                "prefiltered-scope-provenance",
            validator.AOI_SCOPE_RECEIPT_RELATIVE: "aoi-scope-provenance",
            validator.PREFILTER_TRANSFORM_RECEIPT_RELATIVE:
                "prefilter-transformation-provenance",
            validator.PREVIEW_RELATIVE: "runner-local-app-preview",
            validator.GOLDEN_RELATIVE: "validation-only-no-geometry",
            validator.VISUAL_RELATIVE: "offline-osm-visual",
            validator.VISUAL_REVIEW_RELATIVE: "offline-osm-visual-review",
        }
        artifacts = {}
        for relative in relatives:
            raw = (self.root / relative).read_bytes()
            artifacts[relative] = {
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "producer_stage": "synthetic-test",
                "source_scope": scopes.get(relative, "runner-local-qa"),
                "schema_version": "test",
            }
        self.documents["manifest"]["artifacts"] = artifacts
        _write(self.root / validator.REQUIRED_JSON_FILES["manifest"],
               self.documents["manifest"])

    def _sync_preview(self):
        features = self.documents["trails"].get("features") or []
        conversion_errors = []
        boundary = shape(
            self.documents["exact_area"]["features"][0]["geometry"])
        postclip = validator._publisher_postclip_features(
            features, boundary, conversion_errors)
        finalization = {}
        app_row = validator._expected_app_row(
            features, boundary,
            self.documents["selected_discovery"], RELATION_ID,
            conversion_errors, postclip_features=postclip,
            finalization_out=finalization)
        if conversion_errors:
            raise AssertionError(conversion_errors)
        self.documents["preview"]["areas"] = [app_row]
        app_trails = app_row["trails"]
        distance_sum = round(sum(row["distanceMi"] for row in app_trails), 6)
        area_record = {
            "area_id": validator.AREA_ID,
            "name": validator.AREA_NAME,
            "osm_relation_id": RELATION_ID,
            "trail_count": len(app_trails),
            "total_miles": app_row["total_mi"],
        }
        report = self.documents["publish_report"]
        exact_properties = self.documents["exact_area"]["features"][0][
            "properties"]
        report["exact_boundary"] = {
            "enabled": True,
            "name": exact_properties["name"],
            "osm_type": exact_properties["osm_type"],
            "osm_id": exact_properties["osm_id"],
            "geometry_sha256": exact_properties["geometry_sha256"],
        }
        report["validated_areas"] = [copy.deepcopy(area_record)]
        report["published_areas"] = [copy.deepcopy(area_record)]
        report["preview"] = {
            "enabled": True,
            "area_count": 1,
            "area_ids": [validator.AREA_ID],
            "assembly_feature_count": len(features),
            "assembly_property_miles": round(sum(
                float((feature.get("properties") or {}).get("length_mi", 0))
                for feature in features), 6),
            "preview_distance_miles": distance_sum,
            "publisher_total_miles": app_row["total_mi"],
            "postclip": validator._expected_postclip_evidence(
                features, boundary, postclip),
            "finalization": finalization,
            "trails": [{
                "id": row["id"], "name": row["name"],
                "distance_mi": row["distanceMi"],
                "assembly_length_mi": next((
                    (feature.get("properties") or {}).get("length_mi")
                    for feature in features
                    if (feature.get("properties") or {}).get("name") ==
                    row["name"]
                ), None),
            } for row in app_trails],
        }
        _write(self.root / validator.REQUIRED_JSON_FILES["preview"],
               self.documents["preview"])
        _write(self.root / validator.REQUIRED_JSON_FILES["publish_report"],
               report)

    def rewrite(self, label):
        _write(self.root / validator.REQUIRED_JSON_FILES[label], self.documents[label])
        if label in {"trails", "exact_area"}:
            self._sync_preview()
        if label in {
                "raw", "areas", "trails", "removed",
                "raw_relation_members", "assembly_report"}:
            self._sync_visual()
        if label != "manifest":
            self._refresh_manifest_seals()

    def _sync_topology_relations_from_aoi(self):
        topology = self.documents["raw_way_topology"]
        retained = [
            copy.deepcopy(relation)
            for relation in topology.get("relations", [])
            if not (
                str((relation.get("tags") or {}).get("type", "")).casefold()
                == "route"
                and str((relation.get("tags") or {}).get("route", "")).casefold()
                in {"hiking", "foot", "walking", "running"}
            )
        ]
        retained.extend({
            "relation_id": relation["relation_id"],
            "tags": copy.deepcopy(relation["tags"]),
            "members": copy.deepcopy(relation["members"]),
        } for relation in self.documents["aoi_relation_members"]["relations"])
        retained.sort(key=lambda relation: relation["relation_id"])
        topology["relations"] = retained
        topology["relation_count"] = len(retained)

    def set_relation_authority(self, direct, records, *, emitted=True,
                               update_quality=True, relation_tags=None):
        direct = {relation_id: list(way_ids)
                  for relation_id, way_ids in direct.items()}
        direct[8350111] = [835]
        records = [record for record in records if record["way_id"] != 835]
        records.append(copy.deepcopy(
            self.golden_trail["properties"]["source_ways"][0]))
        relation_tags = dict(relation_tags or {})
        relation_tags[8350111] = {
            "name": "Mols Bjerge-stien Bjergetapen",
            "route": "hiking", "type": "route",
        }
        identities = {}
        for scope, relative in validator.PBF_RELATIVES_BY_SCOPE.items():
            payload = (self.root / relative).read_bytes()
            identities[scope] = (hashlib.sha256(payload).hexdigest(),
                                 len(payload))
        graphs = {
            "raw_relation_members": _relation_graph(
                direct, records, relation_tags=relation_tags,
                scope="raw-denmark",
                source_sha256=identities["raw-denmark"][0],
                source_bytes=identities["raw-denmark"][1]),
            "prefilter_relation_members": _relation_graph(
                direct, records, relation_tags=relation_tags,
                scope="prefiltered-denmark",
                source_sha256=identities["prefiltered-denmark"][0],
                source_bytes=identities["prefiltered-denmark"][1]),
            "aoi_relation_members": _relation_graph(
                direct, records, relation_tags=relation_tags, scope="aoi",
                source_sha256=identities["aoi"][0],
                source_bytes=identities["aoi"][1]),
        }
        self.documents.update(graphs)
        self._sync_topology_relations_from_aoi()
        self.rewrite("raw_way_topology")
        self._sync_scope_receipts()
        parsed = {}
        for label, scope in (
                ("raw_relation_members", "raw-denmark"),
                ("prefilter_relation_members", "prefiltered-denmark"),
                ("aoi_relation_members", "aoi")):
            parse_errors = []
            parsed[scope] = validator._validate_relation_ledger_v2(
                graphs[label], scope, parse_errors)
            if parse_errors:
                raise AssertionError(parse_errors)
        authority = parsed["raw-denmark"]
        authority["scopes"] = parsed
        report = self.documents["assembly_report"]
        report["relation_authority"] = {
            "mode": "raw-primary-prefilter-witness-aoi-render-scope",
            "root_relation_ids": graphs["raw_relation_members"][
                "root_relation_ids"],
            "scopes": {
                scope: parsed[scope]["source"] for scope in parsed
            },
        }
        if update_quality:
            boundary = shape(self.documents["areas"]["features"][0]["geometry"])
            audits = []
            unresolved = []
            for relation_id in authority["accepted_relation_ids"]:
                relation_emitted = emitted or relation_id == 8350111
                status = "emitted" if relation_emitted else "entirely-filtered"
                audit = validator._expected_relation_audit(
                    relation_id, authority, boundary)
                audit["assembly_status"] = status
                audit["emitted_in_exact_area"] = relation_emitted
                reasons, record = validator._audit_resolution(audit)
                audit["review_status"] = "unresolved" if reasons else "accepted"
                audit["unresolved_reasons"] = reasons
                audits.append(audit)
                if record is not None:
                    unresolved.append(record)
            quality = report["quality"]
            quality["relation_member_audit"] = audits
            quality["relation_member_unresolved_count"] = len(unresolved)
            quality["relation_member_unresolved"] = unresolved
            quality["validation_failures"] = [
                "relation-member-audit unresolved: "
                f"relation {record['relation_id']!r} {record['reasons']!r}"
                for record in unresolved
            ]
        for label in graphs:
            self.rewrite(label)
        self.rewrite("assembly_report")
        self._refresh_scope_trust()

    def _sync_declined_promotion_decisions(self):
        graph = self.documents["raw_relation_members"]
        relation_values = graph["relations"]
        relation_tags = {
            relation["relation_id"]: relation["tags"]
            for relation in relation_values
        }
        root_positions = {
            root_id: position
            for position, root_id in enumerate(graph["root_relation_ids"])
        }
        candidates = [
            *self.documents["trails"]["features"],
            *self.documents["removed"]["features"],
        ]
        decisions = []
        for feature in sorted(
                candidates,
                key=lambda value: (value.get("properties") or {}).get(
                    "quality_candidate_index", -1)):
            properties = feature.get("properties") or {}
            candidate_index = properties.get("quality_candidate_index")
            root_ids = properties.get("root_relation_ids") or []
            if (properties.get("source") != "relation"
                    or not isinstance(candidate_index, int)):
                continue
            ordered_roots = sorted(
                root_ids,
                key=lambda root_id: (
                    root_positions.get(root_id, len(root_positions)), root_id),
            )
            root_tags = (relation_tags.get(ordered_roots[0], {})
                         if ordered_roots else {})
            root_name = (validator.assembly_model._dk_display_name(root_tags)
                         if ordered_roots else None)
            identity = {
                "candidate_index": candidate_index,
                "candidate_ckey": properties.get("ckey"),
                "root_relation_ids": ordered_roots,
            }
            blocking_roots = []
            if len(ordered_roots) > 1:
                identity["identity_root_relation_id"] = ordered_roots[0]
                properties["identity_root_relation_id"] = ordered_roots[0]
                properties["name"] = root_name
                for field in (
                        "network", "operator", "sac_scale",
                        "trail_visibility"):
                    properties[field] = root_tags.get(field, "")
                for root_id in ordered_roots:
                    tags = relation_tags.get(root_id, {})
                    name = validator.assembly_model._dk_display_name(tags)
                    reason = None
                    if str(tags.get("network", "")).strip().lower() in {
                            "rwn", "nwn", "iwn"}:
                        reason = "not-local-route"
                    elif validator.assembly_model.classify_kind(
                            name, tags) != "route":
                        reason = "not-route-candidate"
                    if reason is not None:
                        blocking_roots.append({
                            "root_relation_id": root_id,
                            "reason": reason,
                        })
            if blocking_roots:
                reason = (
                    "not-local-route"
                    if any(blocker["reason"] == "not-local-route"
                           for blocker in blocking_roots)
                    else "not-route-candidate"
                )
            elif str(root_tags.get("network", "")).strip().lower() in {
                    "rwn", "nwn", "iwn"}:
                reason = "not-local-route"
            elif validator.assembly_model.classify_kind(
                    root_name, root_tags) != "route":
                reason = "not-route-candidate"
            else:
                reason = "no-eligible-poi-in-reach"
            decision = {
                **identity,
                "decision": "declined",
                "reason": reason,
                "skipped_ambiguous_pois": [],
            }
            if blocking_roots:
                decision["blocking_roots"] = blocking_roots
            decisions.append(decision)
        self.documents["assembly_report"]["quality"][
            "promotion_decisions"] = decisions

    def set_relation(self, records, coordinates, *, clipped=False,
                     reported_components=None, direct=None):
        trail = self.documents["trails"]["features"][0]
        properties = trail["properties"]
        way_ids = [record["way_id"] for record in records]
        direct = direct or {700: way_ids}
        relation_ids = list(direct)
        for record in records:
            record["direct_relation_ids"] = [
                relation_id for relation_id, member_ids in direct.items()
                if record["way_id"] in member_ids
            ]
        self.set_relation_authority(direct, records)
        restored_ids = [
            record["way_id"] for record in records
            if validator._is_expected_restored_member(record.get("tags") or {})
        ]
        properties.update({
            "source": "relation",
            "kind": "trail",
            "member_ways": way_ids,
            "ckey": "w" + "-".join(str(value) for value in way_ids),
            "relation_ids": relation_ids,
            "root_relation_ids": relation_ids,
            "direct_relation_way_ids": direct,
            "source_geometry_way_ids": way_ids,
            "restored_relation_way_ids": restored_ids,
            "source_ways": records,
            "rendered_source_way_ids": way_ids,
            "walking_identity": validator._walking_identity(records),
            "quality_disposition": "kept",
        })
        if clipped:
            properties["clipped"] = True
        else:
            properties.pop("clipped", None)
        trail["geometry"] = {
            "type": "MultiLineString",
            "coordinates": coordinates,
        }
        actual_components = validator._line_component_count(coordinates)
        source_components = reported_components if reported_components is not None else 1
        boundary_split = source_components == 1 and actual_components > 1
        reasons = ["shared-osm-node"] if source_components == 1 else []
        if boundary_split:
            reasons.append("boundary-induced-split")
        properties["boundary_induced_split"] = boundary_split
        properties["connectivity"] = {
            "status": "accepted" if source_components == 1 else "rejected",
            "accepted_reasons": reasons,
            "source_components": source_components,
            "postclip_components": actual_components,
            "missing_way_ids": [],
            "exact_boundary_clip": True,
        }
        malt = next(
            feature for feature in self.documents["raw"]["features"]
            if feature.get("id") == "w1027606528")
        golden_raw = next(
            feature for feature in self.documents["raw"]["features"]
            if feature.get("id") == "w835")
        self.documents["raw"]["features"] = [{
            "type": "Feature",
            "id": f"w{record['way_id']}",
            "properties": copy.deepcopy(record["tags"]),
            "geometry": {
                "type": "LineString",
                "coordinates": copy.deepcopy(record["coordinates"]),
            },
        } for record in records] + [golden_raw, malt]
        retained_topology = {
            value["way_id"]: copy.deepcopy(value)
            for value in self.documents["raw_way_topology"]["ways"]
            if value["way_id"] in {835, 1027606528}
        }
        topology_values = [{
            "way_id": record["way_id"],
            "tags": copy.deepcopy(record["tags"]),
            "node_ids": list(record["node_ids"]),
            "coordinates": copy.deepcopy(record["coordinates"]),
            "missing_node_ids": list(record.get("missing_node_ids") or []),
        } for record in records]
        topology_values.extend(retained_topology.values())
        topology_values.sort(key=lambda value: value["way_id"])
        self.documents["raw_way_topology"].update({
            "way_count": len(topology_values),
            "incomplete_way_ids": [
                value["way_id"] for value in topology_values
                if value["missing_node_ids"]],
            "ways": topology_values,
        })
        boundary = shape(self.documents["areas"]["features"][0]["geometry"])
        measurements = validator._restored_measurements(
            trail, [{**record, "_raw_bound": True} for record in records],
            restored_ids, direct, boundary)
        golden_record = copy.deepcopy(
            self.golden_trail["properties"]["source_ways"][0])
        measurements.extend(validator._restored_measurements(
            self.golden_trail, [{**golden_record, "_raw_bound": True}], [],
            {8350111: [835]}, boundary))
        quality = self.documents["assembly_report"]["quality"]
        quality["restored_road_relations"] = [
            measurement[0] for measurement in measurements
        ]
        relation_way_miles = {}
        restored_relation_ways = set()
        for _, _, _, tuple_miles, restored_tuples in measurements:
            relation_way_miles.update(tuple_miles)
            restored_relation_ways.update(restored_tuples)
        restored_miles = sum(
            relation_way_miles[relation_way]
            for relation_way in restored_relation_ways)
        relation_miles = sum(relation_way_miles.values())
        share = restored_miles / relation_miles if relation_miles > 0 else 0.0
        quality["restored_road_aggregate"] = {
            "restored_way_count": len(restored_relation_ways),
            "restored_miles": round(restored_miles, 6),
            "total_relation_miles": round(relation_miles, 6),
            "restored_share": round(share, 6),
        }
        self._sync_declined_promotion_decisions()
        self.rewrite("raw")
        self.rewrite("raw_way_topology")
        self.rewrite("trails")
        self.rewrite("assembly_report")
        self._sync_scope_receipts()
        self._refresh_manifest_seals()
        self._refresh_scope_trust()

    def add_overlap(self):
        second = copy.deepcopy(self.documents["trails"]["features"][0])
        properties = second["properties"]
        properties.update({
            "name": "Second Trail",
            "member_ways": [20],
            "ckey": "w20",
            "quality_candidate_index": 1,
            "source_geometry_way_ids": [20],
            "rendered_source_way_ids": [20],
            "source_ways": [_source_record(
                20, [3, 4], [[10.5, 56.15], [10.6, 56.2]],
                {"name": "Second Trail", "highway": "path"})],
        })
        self.documents["trails"]["features"].insert(1, second)
        self.golden_trail["properties"]["quality_candidate_index"] = 2
        report = self.documents["assembly_report"]
        report["pre_clip_trail_count"] = 3
        report["post_clip_trail_count"] = 3
        report["assembled_trail_count"] = 3
        quality = report["quality"]
        quality["candidate_count"] = 3
        quality["kept_count"] = 3
        row = {
            "candidate_indexes": [0, 1],
            "names": ["Mols Trail", "Second Trail"],
            "keys": ["w10", "w20"],
            "sources": ["name-stitch", "name-stitch"],
            "relation_ids": [[], []],
            "shorter_overlap_ratio": 1.0,
            "left_overlap_ratio": 1.0,
            "right_overlap_ratio": 1.0,
            "final_dispositions": ["kept", "kept"],
            "disposition": "preserved-nonweak-overlap",
            "removed_candidates": [],
        }
        quality["overlap_evidence"] = [row]
        quality["remaining_overlaps"] = [copy.deepcopy(row)]
        self.documents["raw"]["features"].append({
            "type": "Feature",
            "id": "w20",
            "properties": {"name": "Second Trail", "highway": "path"},
            "geometry": {
                "type": "LineString",
                "coordinates": [[10.5, 56.15], [10.6, 56.2]],
            },
        })
        for key in ("validated_areas", "published_areas"):
            self.documents["publish_report"][key][0]["trail_count"] = 2
            self.documents["publish_report"][key][0]["total_miles"] = 2.0
        self.rewrite("raw")
        self.rewrite("trails")
        self.rewrite("assembly_report")
        self.rewrite("publish_report")


class FinalValidation(unittest.TestCase):
    def _run(self, mutate=None, *, remove=None, malformed=None, optional=None,
             trust_in_package=False):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "qa"
            qa = _QA(root)
            if mutate:
                mutate(qa)
            trust_path = qa.scope_trust_path
            if trust_in_package:
                trust_path = root / "scope-trust.json"
                trust_path.write_bytes(qa.scope_trust_path.read_bytes())
            if remove:
                (root / remove).unlink()
            if malformed:
                (root / malformed).write_text("{not-json", encoding="utf-8")
            if optional is not None:
                _write(root / validator.OPTIONAL_DROPPED_ROUTES, optional)
                qa._refresh_manifest_seals()
            result_path = root / "validation/final.json"
            rc = validator.main([
                "final",
                "--qa-dir", str(root),
                "--scope-trust-file", str(trust_path),
                "--raw-relation-members", str(
                    root / validator.RAW_RELATION_MEMBERS_RELATIVE),
                "--prefilter-relation-members", str(
                    root / validator.PREFILTER_RELATION_MEMBERS_RELATIVE),
                "--aoi-relation-members", str(
                    root / validator.AOI_RELATION_MEMBERS_RELATIVE),
                "--expected-osm-relation-id", str(RELATION_ID),
                "--result-json", str(result_path),
            ])
            return rc, json.loads(result_path.read_text())

    def assert_rejected(self, mutate=None, **kwargs):
        rc, result = self._run(mutate, **kwargs)
        self.assertEqual(rc, 1)
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["errors"])

    def test_complete_fixture_passes_with_bound_raw_way(self):
        rc, result = self._run()
        self.assertEqual(rc, 0, result["errors"])
        self.assertEqual(result["assembled_trail_count"], 2)

    def test_scope_trust_must_remain_outside_rewriteable_package(self):
        rc, result = self._run(trust_in_package=True)
        self.assertEqual(rc, 1)
        self.assertTrue(any(
            "scope trust file must be outside" in error
            for error in result["errors"]), result["errors"])

    def test_semantic_ledger_trust_rejects_missing_and_wrong_values(self):
        def missing(qa):
            trust = json.loads(qa.scope_trust_path.read_text())
            del trust["scopes"]["raw-denmark"]["relation_ledger"]
            _write(qa.scope_trust_path, trust)

        def wrong(qa):
            trust = json.loads(qa.scope_trust_path.read_text())
            trust["scopes"]["aoi"]["way_topology"]["sha256"] = "0" * 64
            _write(qa.scope_trust_path, trust)

        for mutate, fragment in (
                (missing, "raw-denmark relation ledger"),
                (wrong, "AOI way topology disagrees")):
            with self.subTest(mutate=mutate.__name__):
                rc, result = self._run(mutate)
                self.assertEqual(rc, 1)
                self.assertTrue(any(fragment in error
                                    for error in result["errors"]),
                                result["errors"])

    def test_frozen_trust_rejects_resealed_topology_tag_and_member_rewrites(self):
        for field in ("tags", "members"):
            with self.subTest(field=field):
                def mutate(qa, field=field):
                    qa.set_topology_way(
                        999, tags={"source": "boundary-survey"},
                        node_ids=[9991, 9992],
                        coordinates=[[8.0, 55.0], [8.1, 55.1]])
                    initial_tags = (
                        {"name": "Unqualified", "type": "multipolygon"}
                        if field == "tags" else
                        {"name": "Protected", "type": "multipolygon",
                         "landuse": "forest"})
                    initial_members = ([{
                        "type": "way", "ref": 999, "role": "outer",
                    }] if field == "tags" else [])
                    qa.set_topology_relation(
                        900, tags=initial_tags, members=initial_members)
                    qa.documents["raw"]["features"].append({
                        "type": "Feature", "id": "w999",
                        "properties": {"source": "boundary-survey"},
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [[8.0, 55.0], [8.1, 55.1]],
                        },
                    })
                    qa.rewrite("raw")
                    relation = next(
                        row for row in qa.documents["raw_way_topology"][
                            "relations"] if row["relation_id"] == 900)
                    if field == "tags":
                        relation["tags"]["landuse"] = "forest"
                    else:
                        relation["members"] = [{
                            "sequence": 0, "type": "way", "ref": 999,
                            "role": "outer",
                        }]
                    # Reseal only the package; creator-owned trust stays frozen.
                    qa.rewrite("raw_way_topology")

                rc, result = self._run(mutate)
                self.assertEqual(rc, 1)
                self.assertTrue(any(
                    "AOI way topology disagrees with trusted stage output"
                    in error for error in result["errors"]), result["errors"])

    def test_frozen_trust_rejects_resealed_raw_graph_edge_and_order_rewrites(self):
        for field in ("edge", "order"):
            with self.subTest(field=field):
                def mutate(qa, field=field):
                    relation = qa.documents["raw_relation_members"][
                        "relations"][0]
                    if field == "edge":
                        relation["members"][0]["ref"] = 10
                    else:
                        relation["members"].append({
                            "sequence": 1, "type": "node", "ref": 999,
                            "role": "guidepost",
                        })
                        relation["members"].reverse()
                        for sequence, member in enumerate(relation["members"]):
                            member["sequence"] = sequence
                    qa.rewrite("raw_relation_members")

                rc, result = self._run(mutate)
                self.assertEqual(rc, 1)
                self.assertTrue(any(
                    "raw-denmark relation ledger disagrees with trusted stage output"
                    in error for error in result["errors"]), result["errors"])

    def test_complete_relation_membership_is_graph_bound_and_accepted(self):
        def mutate(qa):
            records = [
                _source_record(
                    10, [1, 2], [[10.5, 56.15], [10.55, 56.15]],
                    {"highway": "path"}, direct=[700]),
                _source_record(
                    11, [2, 3], [[10.55, 56.15], [10.6, 56.15]],
                    {"highway": "footway"}, direct=[700]),
            ]
            qa.set_relation(records, [record["coordinates"] for record in records])

        rc, result = self._run(mutate)
        self.assertEqual(rc, 0, result["errors"])

    def test_graph_rejects_omitted_extra_wrong_order_and_wrong_relation_members(self):
        def prepare(qa):
            records = [
                _source_record(
                    10, [1, 2], [[10.5, 56.15], [10.54, 56.15]],
                    {"highway": "path"}, direct=[700]),
                _source_record(
                    11, [2, 3], [[10.54, 56.15], [10.56, 56.15]],
                    {"highway": "residential"}, direct=[700]),
                _source_record(
                    12, [3, 4], [[10.56, 56.15], [10.6, 56.15]],
                    {"highway": "path"}, direct=[700]),
            ]
            qa.set_relation(records, [record["coordinates"] for record in records])
            return records

        def omitted(qa):
            prepare(qa)
            properties = qa.documents["trails"]["features"][0]["properties"]
            properties["direct_relation_way_ids"] = {700: [10, 12]}
            properties["source_ways"][1]["direct_relation_ids"] = []
            qa.rewrite("trails")

        def extra_transitive(qa):
            prepare(qa)
            properties = qa.documents["trails"]["features"][0]["properties"]
            properties["direct_relation_way_ids"] = {700: [10, 11, 12, 99]}
            qa.rewrite("trails")

        def wrong_order(qa):
            prepare(qa)
            properties = qa.documents["trails"]["features"][0]["properties"]
            properties["direct_relation_way_ids"] = {700: [12, 11, 10]}
            qa.rewrite("trails")

        def wrong_relation(qa):
            records = prepare(qa)
            properties = qa.documents["trails"]["features"][0]["properties"]
            properties["relation_ids"] = [701]
            properties["direct_relation_way_ids"] = {701: [10, 11, 12]}
            for record in properties["source_ways"]:
                record["direct_relation_ids"] = [701]
            qa.rewrite("trails")
            del records

        for mutate in (omitted, extra_transitive, wrong_order, wrong_relation):
            with self.subTest(mutate=mutate.__name__):
                rc, result = self._run(mutate)
                self.assertEqual(rc, 1)
                self.assertTrue(any(
                    "independent graph" in error or "graph authority" in error
                    or "order/hierarchy" in error
                    for error in result["errors"]), result["errors"])

    def test_graph_rejects_transitive_way_forged_as_parent_direct(self):
        def mutate(qa):
            records = [
                _source_record(
                    10, [1, 2], [[10.5, 56.15], [10.55, 56.15]],
                    {"highway": "path"}, direct=[700]),
                _source_record(
                    11, [2, 3], [[10.55, 56.15], [10.6, 56.15]],
                    {"highway": "path"}, direct=[701]),
            ]
            qa.set_relation(
                records, [record["coordinates"] for record in records],
                direct={700: [10], 701: [11]})
            for label in (
                    "raw_relation_members", "prefilter_relation_members",
                    "aoi_relation_members"):
                graph = qa.documents[label]
                parent = graph["relations"][0]
                parent["members"].append({
                    "sequence": 1, "type": "relation", "ref": 701,
                    "role": "",
                })
                qa.rewrite(label)
            properties = qa.documents["trails"]["features"][0]["properties"]
            properties["direct_relation_way_ids"] = {
                700: [10, 11], 701: [11],
            }
            properties["source_ways"][1]["direct_relation_ids"] = [700, 701]
            qa.rewrite("trails")
            qa._refresh_scope_trust()

        rc, result = self._run(mutate)
        self.assertEqual(rc, 1)
        self.assertTrue(any(
            "direct relation membership contradicts independent graph" in error
            for error in result["errors"]), result["errors"])

    def test_relation_source_node_order_is_bound_to_raw_authority(self):
        def mutate(qa):
            record = _source_record(
                10, [1, 2, 3],
                [[10.5, 56.15], [10.55, 56.15], [10.6, 56.15]],
                {"highway": "path"}, direct=[700])
            qa.set_relation([record], [record["coordinates"]])
            source = qa.documents["trails"]["features"][0]["properties"][
                "source_ways"][0]
            source["node_ids"] = [3, 2, 1]
            qa.rewrite("trails")

        rc, result = self._run(mutate)
        self.assertEqual(rc, 1)
        self.assertTrue(any("raw authority node order" in error
                            for error in result["errors"]), result["errors"])

    def test_entirely_filtered_relation_remains_a_failing_audit_record(self):
        def mutate(qa):
            record = _source_record(
                20, [3, 4], [[10.6, 56.15], [10.65, 56.15]],
                {"highway": "path", "foot": "private"}, direct=[700])
            qa.documents["raw"]["features"].append({
                "type": "Feature",
                "id": "w20",
                "properties": copy.deepcopy(record["tags"]),
                "geometry": {"type": "LineString",
                             "coordinates": copy.deepcopy(record["coordinates"])},
            })
            qa.set_relation_authority({700: [20]}, [record], emitted=False)
            qa.rewrite("raw")
            qa.rewrite("assembly_report")

        rc, result = self._run(mutate)
        self.assertEqual(rc, 1)
        self.assertTrue(any(
            "entirely filtered" in error or "unresolved member exclusions" in error
            for error in result["errors"]), result["errors"])

    def test_graph_rejects_missing_direct_source_way(self):
        def mutate(qa):
            record = _source_record(
                10, [1, 2], [[10.5, 56.15], [10.6, 56.15]],
                {"highway": "path"}, direct=[700])
            qa.set_relation([record], [record["coordinates"]])
            qa.set_relation_authority(
                {700: [10, 99]}, [record], emitted=True)

        rc, result = self._run(mutate)
        self.assertEqual(rc, 1)
        self.assertTrue(any(
            "raw-missing-relevant-member" in error
            or "contains unresolved member exclusions or evidence" in error
            for error in result["errors"]), result["errors"])

    def test_composite_service_round_trip_is_fail_closed(self):
        for service, accepted in (("alley", True),
                                  ("driveway;parking_aisle", False),
                                  ("parking_aisle;alley", False),
                                  (" alley;unknown ", False)):
            with self.subTest(service=service):
                def mutate(qa, service=service):
                    records = [
                        _source_record(
                            10, [1, 2], [[10.5, 56.15], [10.55, 56.15]],
                            {"highway": "path"}, direct=[700]),
                        _source_record(
                            11, [2, 3], [[10.55, 56.15], [10.551, 56.15]],
                            {"highway": "service", "service": service,
                             "foot": "yes"}, direct=[700]),
                        _source_record(
                            12, [3, 4], [[10.551, 56.15], [10.6, 56.15]],
                            {"highway": "path"}, direct=[700]),
                    ]
                    qa.set_relation(
                        records, [record["coordinates"] for record in records])

                rc, result = self._run(mutate)
                self.assertEqual(rc == 0, accepted, result["errors"])

    def test_relation_graph_must_match_assembly_input_pbf_identity(self):
        def mutate(qa):
            graph = qa.documents["aoi_relation_members"]
            graph["source"]["sha256"] = "c" * 64
            qa.rewrite("aoi_relation_members")
            qa._refresh_scope_trust()

        rc, result = self._run(mutate)
        self.assertEqual(rc, 1)
        self.assertTrue(any(
            "assembly input PBF identity disagrees" in error
            for error in result["errors"]), result["errors"])

    def test_reversed_raw_way_orientation_is_same_source_geometry(self):
        def mutate(qa):
            coordinates = qa.documents["raw"]["features"][0]["geometry"][
                "coordinates"]
            qa.documents["raw"]["features"][0]["geometry"]["coordinates"] = list(
                reversed(coordinates))
            qa.rewrite("raw")

        rc, result = self._run(mutate)
        self.assertEqual(rc, 0, result["errors"])

    def test_raw_coordinate_and_tag_mismatches_are_rejected(self):
        def coordinates(qa):
            qa.documents["raw"]["features"][0]["geometry"]["coordinates"][1] = [
                10.61, 56.21]
            qa.rewrite("raw")

        def tags(qa):
            qa.documents["raw"]["features"][0]["properties"]["highway"] = "service"
            qa.rewrite("raw")

        for mutate, fragment in (
                (coordinates, "raw GeoJSON geometry contradicts source topology"),
                (tags, "raw GeoJSON tags contradict source topology")):
            with self.subTest(mutate=mutate.__name__):
                rc, result = self._run(mutate)
                self.assertEqual(rc, 1)
                self.assertTrue(any(fragment in error for error in result["errors"]))

    def test_raw_visual_substitution_cannot_replace_source_topology(self):
        def mutate(qa):
            qa.documents["raw"]["features"] = [{
                "type": "Feature",
                "id": "w99",
                "properties": {"highway": "path"},
                "geometry": {"type": "LineString",
                             "coordinates": [[10.7, 56.2], [10.8, 56.2]]},
            }]
            qa.rewrite("raw")

        rc, result = self._run(mutate)
        self.assertEqual(rc, 1)
        self.assertTrue(any("raw GeoJSON ways lack source topology: [99]" in error
                            for error in result["errors"]), result["errors"])
        self.assertFalse(any("missing-source-way unavailable raw evidence w10"
                             in error for error in result["errors"]))

    def test_missing_source_node_is_unavailable_not_raw_contradiction(self):
        def mutate(qa):
            properties = qa.documents["trails"]["features"][0]["properties"]
            properties["source_ways"][0].update({
                "node_ids": [1, 3],
                "missing_node_ids": [2],
                "coordinates": [[10.5, 56.15], [10.6, 56.2]],
            })
            properties["quality_disposition"] = "missing-source-way"
            properties["connectivity"].update({
                "status": "unavailable-evidence",
                "missing_way_ids": [10],
            })
            qa.documents["raw"]["features"][0]["geometry"]["coordinates"] = [
                [10.5, 56.15], [10.55, 56.175], [10.6, 56.2],
            ]
            qa.documents["assembly_report"]["quality"]["validation_failures"] = [
                "missing-source-way unavailable evidence: 'Mols Trail' [10]"]
            qa.rewrite("raw")
            qa.rewrite("trails")
            qa.rewrite("assembly_report")

        rc, result = self._run(mutate)
        self.assertEqual(rc, 1)
        self.assertTrue(any("missing-source-node unavailable evidence" in error
                            for error in result["errors"]))
        self.assertFalse(any("contradicts raw way geometry" in error
                             for error in result["errors"]))

    def test_malformed_raw_way_id_is_rejected(self):
        def mutate(qa):
            qa.documents["raw"]["features"][0]["id"] = "way10"
            qa.rewrite("raw")

        rc, result = self._run(mutate)
        self.assertEqual(rc, 1)
        self.assertTrue(any("malformed or missing way id" in error
                            for error in result["errors"]))

    def test_declared_missing_source_record_uses_explicit_verdict(self):
        def mutate(qa):
            properties = qa.documents["trails"]["features"][0]["properties"]
            properties["source_ways"] = []
            properties["rendered_source_way_ids"] = []
            properties["quality_disposition"] = "missing-source-way"
            properties["walking_identity"] = "unknown"
            properties["connectivity"].update({
                "status": "unavailable-evidence",
                "accepted_reasons": [],
                "source_components": 0,
                "missing_way_ids": [10],
                "exact_boundary_clip": False,
            })
            qa.documents["assembly_report"]["quality"]["validation_failures"] = [
                "missing-source-way unavailable evidence: 'Mols Trail' [10]"]
            qa.rewrite("trails")
            qa.rewrite("assembly_report")

        rc, result = self._run(mutate)
        self.assertEqual(rc, 1)
        self.assertTrue(any("missing-source-way unavailable evidence" in error
                            for error in result["errors"]))
        self.assertFalse(any("source-way records disagree" in error
                             for error in result["errors"]))

    def test_validator_service_restoration_allow_deny_matrix(self):
        allowed = [
            {"highway": "service"},
            {"highway": "service", "foot": "destination"},
            *({"highway": "service", "foot": foot, **extra}
              for foot in ("yes", "designated", "permissive")
              for extra in ({}, {"service": "alley"})),
            {"highway": " SERVICE ", "service": " ALLEY ",
             "foot": " Permissive "},
        ]
        denied = [
            *({"highway": "service", "service": service, "foot": foot}
              for foot in ("yes", "designated", "permissive")
              for service in (
                  "parking_aisle", "driveway", "drive-through",
                  "drive_through", "drivethrough", "parking",
                  "parking_space", "emergency_access", "emergency-access",
                  "bus", "unknown",
              )),
        ]
        for tags in allowed:
            with self.subTest(allowed=tags):
                self.assertTrue(validator._relation_member_should_render(tags))
                self.assertTrue(validator._is_expected_restored_member(tags))
        for tags in denied:
            with self.subTest(denied=tags):
                self.assertFalse(validator._relation_member_should_render(tags))
                self.assertFalse(validator._is_expected_restored_member(tags))

    def test_validator_track_and_provstskovvej_allow_deny_matrix(self):
        allowed = (
            ({"highway": "track", "lanes": "1"}, False),
            ({"highway": "track", "name": "Provstskovvej",
              "tracktype": "grade3"}, False),
            ({"highway": "track", "name": "Provstskovvej",
              "foot": "yes"}, False),
            ({"highway": "track", "motor_vehicle": "yes", "foot": "yes"}, True),
            ({"highway": "track", "lanes": "1;1",
              "motor_vehicle": "yes", "foot": "yes"}, True),
            ({"highway": "track", "bicycle": "yes", "foot": "yes",
              "horse": "yes", "motorcar": "yes", "name": "Provstskovvej"}, True),
            ({"highway": "unclassified", "bicycle": "yes", "foot": "yes",
              "horse": "yes", "name": "Provstskovvej", "surface": "gravel",
              "tracktype": "grade2", "width": "3"}, True),
            ({"highway": "pedestrian", "motor_vehicle": "yes",
              "foot": "yes"}, True),
        )
        denied = (
            {"highway": "track", "lanes": "2", "foot": "yes"},
            {"highway": "track", "lanes": "03", "foot": "yes"},
            {"highway": "track", "lanes": "2;1", "motor_vehicle": "yes",
             "foot": "yes"},
            {"highway": "track", "lanes": "1;2", "motor_vehicle": "yes",
             "foot": "yes"},
            {"highway": "track", "lanes": "two", "motor_vehicle": "yes",
             "foot": "yes"},
            {"highway": "track", "lanes": "2.5", "motor_vehicle": "yes",
             "foot": "yes"},
            {"highway": "track", "lanes": "1;;1", "motor_vehicle": "yes",
             "foot": "yes"},
            {"highway": "track", "lanes": "2", "motor_vehicle": "yes",
             "foot": "yes"},
            {"highway": "track", "name": "Synthetic Service Road",
             "motor_vehicle": "yes", "foot": "yes"},
            {"highway": "track", "name": "NF-418C", "foot": "yes"},
            {"highway": "track", "name": "3900 East", "foot": "yes"},
            {"highway": "track", "motor_vehicle": "yes"},
            {"highway": "track", "motorcar": "yes"},
            {"highway": "track", "atv": "yes", "foot": "yes"},
        )
        for tags, restored in allowed:
            with self.subTest(allowed=tags):
                self.assertTrue(validator._relation_member_should_render(tags))
                self.assertEqual(validator._is_expected_restored_member(tags),
                                 restored)
        for tags in denied:
            with self.subTest(denied=tags):
                self.assertFalse(validator._relation_member_should_render(tags))
                self.assertFalse(validator._is_expected_restored_member(tags))

    def test_validator_rejects_unsafe_serialized_direct_members(self):
        unsafe = (
            {"highway": "service", "service": "parking_aisle", "foot": "yes"},
            {"highway": "service", "service": "drive_through", "foot": "yes"},
            {"highway": "service", "service": "parking", "foot": "yes"},
            {"highway": "service", "service": "unknown", "foot": "yes"},
            {"highway": "track", "lanes": "2", "foot": "yes"},
            {"highway": "track", "lanes": "2;1", "motor_vehicle": "yes",
             "foot": "yes"},
            {"highway": "track", "lanes": "two", "motor_vehicle": "yes",
             "foot": "yes"},
            {"highway": "track", "name": "NF-418C", "foot": "yes"},
            {"highway": "track", "name": "Synthetic Service Road",
             "motor_vehicle": "yes", "foot": "yes"},
        )
        for tags in unsafe:
            with self.subTest(tags=tags):
                def mutate(qa, tags=tags):
                    record = _source_record(
                        10, [1, 2], [[10.5, 56.15], [10.6, 56.2]],
                        tags, direct=[700])
                    qa.set_relation([record], [record["coordinates"]])

                rc, result = self._run(mutate)
                self.assertEqual(rc, 1)
                self.assertTrue(any("unsafe direct relation member" in error
                                    for error in result["errors"]))

    def _set_restored_road_lengths(self, qa, road_miles, total_miles):
        miles_per_degree = math.pi * 3958.7613 / 180.0
        start = 56.15
        road_start = start + (total_miles - road_miles) / 2 / miles_per_degree
        road_end = road_start + road_miles / miles_per_degree
        end = start + total_miles / miles_per_degree
        records = [
            _source_record(10, [1, 2], [[10.5, start], [10.5, road_start]],
                           {"highway": "path"}, direct=[700]),
            _source_record(11, [2, 3], [[10.5, road_start], [10.5, road_end]],
                           {"highway": "residential"}, direct=[700]),
            _source_record(12, [3, 4], [[10.5, road_end], [10.5, end]],
                           {"highway": "path"}, direct=[700]),
        ]
        qa.set_relation(records, [record["coordinates"] for record in records])

    def test_validator_keeps_restored_share_as_diagnostic_at_all_values(self):
        for share in (0.099, 0.100, 0.101):
            with self.subTest(share=share):
                def mutate(qa, share=share):
                    self._set_restored_road_lengths(qa, share, 1.0)

                rc, result = self._run(mutate)
                self.assertEqual(rc, 0, result["errors"])
                self.assertFalse(any(
                    "restored-road" in error and "limit" in error
                    for error in result["errors"]))

    def test_validator_keeps_restored_miles_as_diagnostic_at_all_values(self):
        for miles in (0.999, 1.000, 1.001):
            with self.subTest(miles=miles):
                def mutate(qa, miles=miles):
                    self._set_restored_road_lengths(qa, miles, 12.0)

                rc, result = self._run(mutate)
                self.assertEqual(rc, 0, result["errors"])
                self.assertFalse(any(
                    "restored-road" in error and "limit" in error
                    for error in result["errors"]))

    def test_validator_multi_relation_aggregate_counts_unique_tuples(self):
        def mutate(qa):
            records = [
                _source_record(10, [1, 2], [[10.5, 56.15], [10.55, 56.15]],
                               {"highway": "path"}, direct=[700]),
                _source_record(11, [2, 3], [[10.55, 56.15], [10.551, 56.15]],
                               {"highway": "residential"}, direct=[700, 701]),
                _source_record(12, [3, 4], [[10.551, 56.15], [10.6, 56.15]],
                               {"highway": "path"}, direct=[701]),
            ]
            qa.set_relation(
                records, [record["coordinates"] for record in records],
                direct={700: [10, 11], 701: [11, 12]})
            quality = qa.documents["assembly_report"]["quality"]
            self.assertEqual(
                [row["relation_id"] for row in quality["restored_road_relations"]],
                [700, 701, 8350111])
            self.assertEqual(
                quality["restored_road_aggregate"]["restored_way_count"], 2)

        rc, result = self._run(mutate)
        self.assertEqual(rc, 0, result["errors"])

    @unittest.skipUnless(HAVE_SHAPELY, "shapely not installed")
    def test_validator_relation_denominator_uses_clipped_direct_way(self):
        def mutate(qa):
            boundary = box(10.5, 56.1, 10.7, 56.25)
            source = [[10.4, 56.15], [10.6, 56.15]]
            from shapely.geometry import LineString
            rendered = mapping(LineString(source).intersection(boundary))
            qa.set_exact_geometry(mapping(boundary))
            record = _source_record(
                10, [1, 2], source, {"highway": "path"}, direct=[700])
            qa.set_relation([record], [rendered["coordinates"]], clipped=True)
            measurement = qa.documents["assembly_report"]["quality"][
                "restored_road_relations"][0]
            expected = validator._haversine_miles((10.5, 56.15), (10.6, 56.15))
            self.assertAlmostEqual(measurement["total_relation_miles"],
                                   expected, places=6)

        rc, result = self._run(mutate)
        self.assertEqual(rc, 0, result["errors"])

    def test_ingest_sidecar_route_overlap_requires_truthful_binding(self):
        def annotated(qa):
            records = [
                _source_record(10, [1, 2], [[10.5, 56.15], [10.55, 56.15]],
                               {"highway": "path"}, direct=[700]),
                _source_record(
                    11, [2, 3], [[10.55, 56.15], [10.551, 56.15]],
                    {"highway": "service", "name": "Synthetic Access Way",
                     "foot": "yes"}, direct=[700]),
                _source_record(12, [3, 4], [[10.551, 56.15], [10.6, 56.15]],
                               {"highway": "path"}, direct=[700]),
            ]
            qa.set_relation(records, [record["coordinates"] for record in records])
            qa.documents["ingest_dropped"]["features"] = [{
                "type": "Feature",
                "properties": {
                    "name": "Synthetic Access Way",
                    "way_id": 11,
                    "ckey": "w11",
                    "highway": "service",
                    "removed_category": validator.RETAINED_ROUTE_INGEST_CATEGORY,
                    "removed_reason": "Retained only in a signed route.",
                    "retained_in_route": True,
                    "relation_ids": [700],
                    "standalone_drop_category": "non-trail-highway",
                    "standalone_drop_reason": "Not a standalone trail.",
                },
                "geometry": {"type": "LineString",
                             "coordinates": records[1]["coordinates"]},
            }]
            qa.rewrite("ingest_dropped")

        rc, result = self._run(annotated)
        self.assertEqual(rc, 0, result["errors"])

        def unannotated(qa):
            annotated(qa)
            properties = qa.documents["ingest_dropped"]["features"][0]["properties"]
            properties.update({
                "removed_category": "non-trail-highway",
                "removed_reason": "Not eligible as a standalone trail.",
                "retained_in_route": False,
                "relation_ids": [],
            })
            properties.pop("standalone_drop_category", None)
            properties.pop("standalone_drop_reason", None)
            qa.rewrite("ingest_dropped")

        rc, result = self._run(unannotated)
        self.assertEqual(rc, 1)
        self.assertTrue(any("overlaps published route without annotation" in error
                            for error in result["errors"]))

    def test_optional_dropped_routes_is_parsed_when_present(self):
        rc, result = self._run(optional={
            "type": "FeatureCollection", "features": []})
        self.assertEqual(rc, 0, result["errors"])
        self.assert_rejected(optional={"not": "geojson"})

    def test_exact_clip_and_coverage_failures_are_independent(self):
        def no_clip(qa):
            qa.documents["assembly_report"]["clip_applied"] = False
            qa.rewrite("assembly_report")

        def zero_raw(qa):
            qa.documents["assembly_report"]["coverage"]["raw_trailish_ways"] = 0
            qa.rewrite("assembly_report")

        def wrong_relation(qa):
            qa.documents["assembly_report"]["boundary"]["osm_id"] = 1
            qa.rewrite("assembly_report")

        for mutate in (no_clip, zero_raw, wrong_relation):
            with self.subTest(mutate=mutate.__name__):
                self.assert_rejected(mutate)

    def test_zero_unnamed_and_wrong_area_are_rejected(self):
        def zero(qa):
            qa.documents["trails"]["features"] = []
            report = qa.documents["assembly_report"]
            report["pre_clip_trail_count"] = 0
            report["post_clip_trail_count"] = 0
            report["assembled_trail_count"] = 0
            report["quality"]["candidate_count"] = 0
            report["quality"]["kept_count"] = 0
            qa.rewrite("trails")
            qa.rewrite("assembly_report")

        def unnamed(qa):
            qa.documents["trails"]["features"][0]["properties"]["name"] = "Unnamed 123"
            qa.rewrite("trails")

        def wrong_area(qa):
            qa.documents["trails"]["features"][0]["properties"]["area"] = "Other"
            qa.rewrite("trails")

        def malformed_feature(qa):
            qa.documents["trails"]["features"][0] = {"not": "a feature"}
            qa.rewrite("trails")

        for mutate in (zero, unnamed, wrong_area, malformed_feature):
            with self.subTest(mutate=mutate.__name__):
                self.assert_rejected(mutate)

    def test_shared_node_t_junction_is_independently_accepted(self):
        def mutate(qa):
            records = [
                _source_record(10, [1, 2, 3],
                               [[10.5, 56.15], [10.55, 56.15], [10.6, 56.15]],
                               {"highway": "path"}, direct=[700]),
                _source_record(11, [2, 4],
                               [[10.55, 56.15], [10.55, 56.2]],
                               {"highway": "path"}, direct=[700]),
            ]
            qa.set_relation(records, [record["coordinates"] for record in records])

        rc, result = self._run(mutate)
        self.assertEqual(rc, 0, result["errors"])

    @unittest.skipUnless(HAVE_SHAPELY, "shapely not installed")
    def test_exact_boundary_induced_split_is_independently_accepted(self):
        def mutate(qa):
            boundary = MultiPolygon([
                box(10.44, 56.1, 10.52, 56.25),
                box(10.68, 56.1, 10.76, 56.25),
            ])
            source_coordinates = [
                [10.45, 56.15], [10.55, 56.15],
                [10.65, 56.15], [10.75, 56.15],
            ]
            # Use Shapely directly while keeping the production validator independent.
            from shapely.geometry import LineString
            rendered = mapping(LineString(source_coordinates).intersection(boundary))
            qa.set_exact_geometry(mapping(boundary))
            record = _source_record(10, [1, 2, 3, 4], source_coordinates,
                                    {"highway": "path"}, direct=[700])
            qa.set_relation([record], rendered["coordinates"], clipped=True)
            properties = qa.documents["trails"]["features"][0]["properties"]
            properties["quality_disposition"] = "boundary-induced-split"
            properties["connectivity"].update({
                "accepted_reasons": ["shared-osm-node", "boundary-induced-split"],
                "postclip_components": 2,
            })
            qa.rewrite("trails")

        rc, result = self._run(mutate)
        self.assertEqual(rc, 0, result["errors"])

    @unittest.skipUnless(HAVE_SHAPELY, "shapely not installed")
    def test_boundary_split_other_reports_only_honest_review_failures(self):
        def mutate(qa):
            boundary = MultiPolygon([
                box(10.44, 56.1, 10.52, 56.25),
                box(10.68, 56.1, 10.76, 56.25),
            ])
            source_coordinates = [
                [10.45, 56.15], [10.55, 56.15],
                [10.65, 56.15], [10.75, 56.15],
            ]
            from shapely.geometry import LineString
            rendered = mapping(LineString(source_coordinates).intersection(boundary))
            trail = qa.documents["trails"]["features"][0]
            properties = trail["properties"]
            record = _source_record(
                10, [1, 2, 3, 4], source_coordinates,
                {"name": "Boundary Cycleway", "highway": "cycleway"})
            properties.update({
                "name": "Boundary Cycleway",
                "source": "name-stitch",
                "relation_ids": [],
                "direct_relation_way_ids": {},
                "restored_relation_way_ids": [],
                "source_ways": [record],
                "rendered_source_way_ids": [10],
                "walking_identity": "other",
                "boundary_induced_split": True,
                "quality_disposition": "preserved-other-needs-review",
                "clipped": True,
                "connectivity": {
                    "status": "accepted",
                    "accepted_reasons": [
                        "shared-osm-node", "boundary-induced-split"],
                    "source_components": 1,
                    "postclip_components": 2,
                    "missing_way_ids": [],
                    "exact_boundary_clip": True,
                },
            })
            trail["geometry"] = rendered
            qa.set_exact_geometry(mapping(boundary))
            qa.documents["raw"]["features"][0].update({
                "properties": copy.deepcopy(record["tags"]),
                "geometry": {"type": "LineString",
                             "coordinates": source_coordinates},
            })
            qa.documents["assembly_report"]["quality"]["validation_failures"] = [
                "preserved-other-needs-review: 'Boundary Cycleway'",
            ]
            qa.set_topology_way(
                10, tags=record["tags"], node_ids=record["node_ids"],
                coordinates=record["coordinates"])
            qa.rewrite("raw")
            qa.rewrite("trails")
            qa.rewrite("assembly_report")

        rc, result = self._run(mutate)
        self.assertEqual(rc, 1)
        self.assertTrue(result["errors"])
        self.assertTrue(all(
            "preserved-other-needs-review" in error
            or "quality report contains unresolved failures" in error
            for error in result["errors"]
        ), result["errors"])
        self.assertFalse(any("boundary-induced-split fact" in error
                             or "lacks boundary-induced-split" in error
                             for error in result["errors"]))

    def test_small_coordinate_gap_with_distinct_nodes_is_not_bridged(self):
        def mutate(qa):
            records = [
                _source_record(10, [1, 2], [[10.5, 56.15], [10.55, 56.15]],
                               {"highway": "path"}, direct=[700]),
                _source_record(11, [3, 4],
                               [[10.550001, 56.15], [10.6, 56.15]],
                               {"highway": "path"}, direct=[700]),
            ]
            qa.set_relation(records, [record["coordinates"] for record in records],
                            reported_components=1)

        self.assert_rejected(mutate)

    def test_three_components_one_close_pair_and_distant_third_is_rejected(self):
        def mutate(qa):
            records = [
                _source_record(10, [1, 2], [[10.5, 56.15], [10.52, 56.15]],
                               {"highway": "path"}, direct=[700]),
                _source_record(11, [3, 4],
                               [[10.520001, 56.15], [10.54, 56.15]],
                               {"highway": "path"}, direct=[700]),
                _source_record(12, [5, 6], [[10.75, 56.2], [10.8, 56.2]],
                               {"highway": "path"}, direct=[700]),
            ]
            qa.set_relation(records, [record["coordinates"] for record in records],
                            reported_components=1)

        self.assert_rejected(mutate)

    def test_source_component_summary_cannot_override_node_evidence(self):
        def mutate(qa):
            trail = qa.documents["trails"]["features"][0]
            trail["properties"]["source_ways"][0]["node_ids"] = [9, 10]
            trail["properties"]["source_ways"][0]["coordinates"] = [
                [10.7, 56.2], [10.8, 56.2]]
            qa.rewrite("trails")

        self.assert_rejected(mutate)

    def test_overlap_ledger_rejects_omission_extra_shares_and_removed_side(self):
        def omission(qa):
            qa.add_overlap()
            quality = qa.documents["assembly_report"]["quality"]
            quality["overlap_evidence"] = []
            quality["remaining_overlaps"] = []
            qa.rewrite("assembly_report")

        def extra(qa):
            qa.add_overlap()
            row = copy.deepcopy(
                qa.documents["assembly_report"]["quality"]["overlap_evidence"][0])
            row["candidate_indexes"] = [0, 99]
            qa.documents["assembly_report"]["quality"]["overlap_evidence"].append(row)
            qa.rewrite("assembly_report")

        def missing_share(qa):
            qa.add_overlap()
            del qa.documents["assembly_report"]["quality"]["overlap_evidence"][0][
                "left_overlap_ratio"]
            qa.rewrite("assembly_report")

        def wrong_share(qa):
            qa.add_overlap()
            qa.documents["assembly_report"]["quality"]["overlap_evidence"][0][
                "right_overlap_ratio"] = 0.5
            qa.rewrite("assembly_report")

        def contradictory_removed_side(qa):
            qa.add_overlap()
            qa.documents["assembly_report"]["quality"]["overlap_evidence"][0][
                "removed_candidates"] = [{
                    "side": "right", "candidate_index": 1, "key": "w20",
                    "name": "Second Trail", "category": "dk-unqualified-road-track",
                }]
            qa.rewrite("assembly_report")

        for mutate in (omission, extra, missing_share, wrong_share,
                       contradictory_removed_side):
            with self.subTest(mutate=mutate.__name__):
                self.assert_rejected(mutate)

    def test_quality_removed_sidecar_requires_rebuilt_weak_track_evidence(self):
        def valid_removed(qa):
            removed = copy.deepcopy(qa.documents["trails"]["features"][0])
            properties = removed["properties"]
            coordinates = [[10.7, 56.2], [10.75, 56.2]]
            properties.update({
                "name": "Synthetic Farm Track",
                "source": "name-stitch",
                "member_ways": [20],
                "ckey": "w20",
                "quality_candidate_index": 2,
                "quality_disposition": "dk-unqualified-road-track",
                "removed_category": "dk-unqualified-road-track",
                "removed_reason": "Unqualified Denmark track.",
                "source_geometry_way_ids": [20],
                "source_ways": [_source_record(
                    20, [3, 4], coordinates,
                    {"name": "Synthetic Farm Track", "highway": "track"})],
                "rendered_source_way_ids": [20],
                "walking_identity": "weak-track",
            })
            removed["geometry"]["coordinates"] = [coordinates]
            qa.documents["removed"]["features"] = [
                removed, *qa.documents["removed"]["features"]]
            quality = qa.documents["assembly_report"]["quality"]
            quality.update({
                "candidate_count": 3,
                "removed_count": 1,
                "removed_counts": {"dk-unqualified-road-track": 1},
            })
            qa.documents["raw"]["features"].append({
                "type": "Feature",
                "id": "w20",
                "properties": {"name": "Synthetic Farm Track",
                               "highway": "track"},
                "geometry": {"type": "LineString", "coordinates": coordinates},
            })
            qa.set_topology_way(
                20, tags={"name": "Synthetic Farm Track", "highway": "track"},
                node_ids=[3, 4], coordinates=coordinates)
            qa.rewrite("raw")
            qa.rewrite("removed")
            qa.rewrite("assembly_report")
            qa._refresh_scope_trust()

        rc, result = self._run(valid_removed)
        self.assertEqual(rc, 0, result["errors"])

        def destination_is_not_walking(qa):
            valid_removed(qa)
            qa.documents["removed"]["features"][0]["properties"]["source_ways"][0][
                "tags"]["foot"] = "destination"
            qa.documents["raw"]["features"][-1]["properties"]["foot"] = "destination"
            topology = next(value for value in
                            qa.documents["raw_way_topology"]["ways"]
                            if value["way_id"] == 20)
            topology["tags"]["foot"] = "destination"
            qa.rewrite("raw_way_topology")
            qa.rewrite("raw")
            qa.rewrite("removed")
            qa._refresh_scope_trust()

        rc, result = self._run(destination_is_not_walking)
        self.assertEqual(rc, 0, result["errors"])

        def falsified_explicit(qa):
            valid_removed(qa)
            feature = qa.documents["removed"]["features"][0]
            feature["properties"]["source_ways"][0]["tags"]["foot"] = "yes"
            qa.documents["raw"]["features"][-1]["properties"]["foot"] = "yes"
            topology = next(value for value in
                            qa.documents["raw_way_topology"]["ways"]
                            if value["way_id"] == 20)
            topology["tags"]["foot"] = "yes"
            qa.rewrite("raw_way_topology")
            qa.rewrite("raw")
            qa.rewrite("removed")
            qa._refresh_scope_trust()

        self.assert_rejected(falsified_explicit)

    def test_cycleway_other_identity_fails_closed_for_review(self):
        def mutate(qa):
            properties = qa.documents["trails"]["features"][0]["properties"]
            properties["source_ways"][0]["tags"] = {
                "name": "Synthetic Cycleway", "highway": "cycleway"}
            qa.documents["raw"]["features"][0]["properties"] = {
                "name": "Synthetic Cycleway", "highway": "cycleway"}
            properties["walking_identity"] = "other"
            properties["quality_disposition"] = "preserved-other-needs-review"
            qa.documents["assembly_report"]["quality"]["validation_failures"] = [
                "preserved-other-needs-review: 'Synthetic Cycleway'"]
            qa.rewrite("raw")
            qa.rewrite("trails")
            qa.rewrite("assembly_report")

        self.assert_rejected(mutate)

    def test_unresolved_signed_relation_population_fails_closed_without_removal(self):
        def mutate(qa):
            records = [
                _source_record(10, [1, 2], [[10.5, 56.15], [10.52, 56.15]],
                               {"highway": "path"}, direct=[700]),
                _source_record(11, [3, 4], [[10.7, 56.2], [10.75, 56.2]],
                               {"highway": "path"}, direct=[700]),
            ]
            qa.set_relation(records, [record["coordinates"] for record in records],
                            reported_components=2)
            properties = qa.documents["trails"]["features"][0]["properties"]
            properties["quality_disposition"] = "unresolved-relation-gap"
            unresolved = {
                "name": properties["name"], "key": properties["ckey"],
                "relation_ids": [700], "miles": 1.0,
                "source_components": 2, "postclip_components": 2,
                "missing_way_ids": [],
            }
            quality = qa.documents["assembly_report"]["quality"]
            quality["signed_relation_unresolved_count"] = 1
            quality["signed_relation_unresolved_miles"] = 1.0
            quality["signed_relation_unresolved"] = [unresolved]
            quality["validation_failures"] = ["unresolved-relation-gap: 'Mols Trail'"]
            qa.rewrite("trails")
            qa.rewrite("assembly_report")

        rc, result = self._run(mutate)
        self.assertEqual(rc, 1)
        self.assertTrue(any("unresolved-relation-gap" in error
                            for error in result["errors"]))

    def test_publisher_cardinality_and_write_failures_are_rejected(self):
        def zero_index(qa):
            qa.documents["publish_report"]["index_area_count"] = 0
            qa.rewrite("publish_report")

        def skipped(qa):
            qa.documents["publish_report"]["skipped_areas"] = [{"reason": "no trails"}]
            qa.rewrite("publish_report")

        def canonical(qa):
            qa.documents["publish_report"]["canonical_write"] = True
            qa.rewrite("publish_report")

        for mutate in (zero_index, skipped, canonical):
            with self.subTest(mutate=mutate.__name__):
                self.assert_rejected(mutate)

    @unittest.skipUnless(HAVE_SHAPELY, "shapely not installed")
    def test_real_publisher_preview_round_trip_passes_final_validator(self):
        def mutate(qa):
            boundary = [{
                "name": validator.AREA_NAME,
                "rings": [[
                    (10.4, 56.1), (10.9, 56.1), (10.9, 56.35),
                    (10.4, 56.35), (10.4, 56.1),
                ]],
                "osm_id": RELATION_ID,
                "osm_type": "relation",
            }]
            with TemporaryDirectory() as producer_tmp:
                producer_root = Path(producer_tmp)
                index_path = producer_root / "producer-index.json"
                output_dir = producer_root / "producer-output"
                output_dir.mkdir()
                index_path.write_text(json.dumps([[
                    validator.AREA_ID, validator.AREA_NAME, validator.STATE,
                    56.2, 10.6, None, None, RELATION_ID,
                ]]), encoding="utf-8")
                with patch.object(
                        publish_producer.areamod, "merge_areas",
                        return_value=boundary):
                    rc = publish_producer.main([
                        "--trails", str(
                            qa.root / validator.REQUIRED_JSON_FILES["trails"]),
                        "--hiking", str(qa.root / validator.AOI_PBF_RELATIVE),
                        "--index", str(index_path),
                        "--out-dir", str(output_dir),
                        "--state", validator.STATE,
                        "--dry-run", "--no-boundary-fetch", "--no-elevation",
                        "--exact-boundary", str(
                            qa.root / validator.EXACT_AREA_RELATIVE),
                        "--preview-root", str(qa.root),
                        "--preview-json", str(
                            qa.root / validator.PREVIEW_RELATIVE),
                        "--report-json", str(
                            qa.root / validator.REQUIRED_JSON_FILES[
                                "publish_report"]),
                    ])
                self.assertEqual(rc, 0)
            qa.documents["preview"] = json.loads(
                (qa.root / validator.PREVIEW_RELATIVE).read_text())
            qa.documents["publish_report"] = json.loads(
                (qa.root / validator.REQUIRED_JSON_FILES[
                    "publish_report"]).read_text())
            qa._refresh_manifest_seals()

        rc, result = self._run(mutate)
        self.assertEqual(rc, 0, result["errors"])

    @unittest.skipUnless(HAVE_SHAPELY, "shapely not installed")
    def test_publisher_clips_several_trails_and_validator_rebuilds_exact_row(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = root / "work"
            qa_root = root / "qa"
            reports = qa_root / "reports"
            output = work / "output"
            work.mkdir()
            reports.mkdir(parents=True)
            output.mkdir()
            names = (
                "Helligkildestien", "Skovbjerg",
                "Strandkærstien", "Toggerbo-stien",
            )
            features = []
            for index, name in enumerate(names):
                latitude = 56.15 + index * 0.025
                coordinates = [
                    [10.45, latitude], [10.52, latitude],
                    [10.68, latitude], [10.75, latitude],
                ]
                features.append({
                    "type": "Feature",
                    "properties": {
                        "name": name, "kind": "trail", "source": "name-stitch",
                        "ckey": f"w{100 + index}",
                        "length_mi": round(
                            validator._linework_miles([coordinates]), 3),
                        "sac_scale": "", "trail_visibility": "",
                    },
                    "geometry": {"type": "MultiLineString",
                                 "coordinates": [coordinates]},
                })
            remnant = [[10.69995, 56.275], [10.75, 56.275]]
            excluded = {
                "Hole Only": [[10.56, 56.25], [10.64, 56.25]],
                "Neighbor Only": [[10.72, 56.25], [10.78, 56.25]],
            }
            features.append({
                "type": "Feature",
                "properties": {
                    "name": "Clipped Remnant", "kind": "trail",
                    "source": "name-stitch", "ckey": "w999",
                    "length_mi": round(
                        validator._linework_miles([remnant]), 3),
                    "sac_scale": "", "trail_visibility": "",
                },
                "geometry": {"type": "MultiLineString",
                             "coordinates": [remnant]},
            })
            for offset, (name, coordinates) in enumerate(excluded.items()):
                features.append({
                    "type": "Feature",
                    "properties": {
                        "name": name, "kind": "trail",
                        "source": "name-stitch", "ckey": f"w{1000 + offset}",
                        "length_mi": round(
                            validator._linework_miles([coordinates]), 3),
                        "sac_scale": "", "trail_visibility": "",
                    },
                    "geometry": {"type": "MultiLineString",
                                 "coordinates": [coordinates]},
                })
            trails = {"type": "FeatureCollection", "features": features}
            trails_path = work / "trails.geojson"
            index_path = work / "index.json"
            preview_path = reports / "app-preview.json"
            report_path = reports / "publish.json"
            trails_path.write_text(json.dumps(trails), encoding="utf-8")
            index_path.write_text(json.dumps([[
                validator.AREA_ID, validator.AREA_NAME, validator.STATE,
                56.2, 10.6, None, None, RELATION_ID,
            ]]), encoding="utf-8")
            ring = [
                (10.5, 56.1), (10.7, 56.1), (10.7, 56.3),
                (10.5, 56.3), (10.5, 56.1),
            ]
            hole = [
                [10.55, 56.24], [10.65, 56.24], [10.65, 56.26],
                [10.55, 56.26], [10.55, 56.24],
            ]
            island = [
                [10.48, 56.1], [10.49, 56.1], [10.49, 56.3],
                [10.48, 56.3], [10.48, 56.1],
            ]
            exact_geometry = {
                "type": "MultiPolygon",
                "coordinates": [
                    [[list(point) for point in ring], hole],
                    [island],
                ],
            }
            exact_document = {
                "type": "FeatureCollection",
                "features": [{
                    "type": "Feature",
                    "properties": {
                        "name": validator.AREA_NAME,
                        "osm_type": "relation",
                        "osm_id": RELATION_ID,
                        "geometry_sha256": validator._publisher_json_sha256(
                            exact_geometry),
                    },
                    "geometry": exact_geometry,
                }],
            }
            exact_path = reports / "exact-area.geojson"
            exact_path.write_text(json.dumps(exact_document), encoding="utf-8")
            boundary = [{
                "name": validator.AREA_NAME, "rings": [ring],
                "osm_id": RELATION_ID, "osm_type": "relation",
            }]
            with patch.object(
                    publish_producer.areamod, "merge_areas",
                    return_value=boundary) as merge_areas:
                rc = publish_producer.main([
                    "--trails", str(trails_path),
                    "--hiking", str(work / "aoi.osm.pbf"),
                    "--index", str(index_path), "--out-dir", str(output),
                    "--state", validator.STATE, "--dry-run",
                    "--no-boundary-fetch", "--no-elevation",
                    "--exact-boundary", str(exact_path),
                    "--preview-root", str(qa_root),
                    "--preview-json", str(preview_path),
                    "--report-json", str(report_path),
                ])
            self.assertEqual(rc, 0)
            merge_areas.assert_not_called()
            preview = json.loads(preview_path.read_text())
            report = json.loads(report_path.read_text())
            areas = [{
                "type": "Feature",
                "properties": {
                    "name": validator.AREA_NAME,
                    "osm_type": "relation",
                    "osm_id": RELATION_ID,
                },
                "geometry": exact_geometry,
            }, {
                "type": "Feature",
                "properties": {
                    "name": "Mols Bjerge Neighbor",
                    "landuse": "forest",
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [10.71, 56.2], [10.79, 56.2], [10.79, 56.3],
                        [10.71, 56.3], [10.71, 56.2],
                    ]],
                },
            }]
            selected = {
                "index_row": [validator.AREA_ID, validator.AREA_NAME,
                              validator.STATE, 56.2, 10.6],
            }
            errors = []
            validated_boundary, boundary_evidence = \
                validator._sealed_exact_boundary(
                    exact_document, areas, {
                        "boundary": {
                            "name": validator.AREA_NAME,
                            "osm_type": "relation",
                            "osm_id": RELATION_ID,
                        },
                        "boundary_geometry_sha256":
                            report["exact_boundary"]["geometry_sha256"],
                    }, RELATION_ID, errors)

            validator._validate_preview(
                preview, report, features, validated_boundary,
                boundary_evidence, selected, RELATION_ID, errors)

            self.assertEqual(errors, [])
            app_trails = preview["areas"][0]["trails"]
            self.assertEqual(len(app_trails), 4)
            self.assertEqual(report["preview"]["postclip"]["feature_count"], 5)
            self.assertEqual(report["preview"]["finalization"][
                "degenerate_removed"], [{
                    "id": "clipped-remnant", "reason": "isolated",
                }])
            self.assertTrue(all(
                trail["segments"][0][0][1] == 10.48
                and trail["segments"][0][-1][1] == 10.7
                for trail in app_trails))
            full_by_name = {
                feature["properties"]["name"]:
                    feature["properties"]["length_mi"]
                for feature in features
            }
            self.assertTrue(all(
                trail["distanceMi"] < full_by_name[trail["name"]]
                for trail in app_trails))

    def test_synchronized_exact_boundary_union_tamper_is_rejected(self):
        def mutate(qa):
            geometry = copy.deepcopy(
                qa.documents["exact_area"]["features"][0]["geometry"])
            geometry["coordinates"][0][1] = [10.95, 56.1]
            digest = validator._publisher_json_sha256(geometry)
            exact = qa.documents["exact_area"]["features"][0]
            exact["geometry"] = geometry
            exact["properties"]["geometry_sha256"] = digest
            qa.documents["assembly_report"]["boundary_geometry_sha256"] = digest
            qa.documents["publish_report"]["exact_boundary"][
                "geometry_sha256"] = digest
            qa.rewrite("exact_area")
            qa.rewrite("assembly_report")
            qa.rewrite("publish_report")

        rc, result = self._run(mutate)
        self.assertEqual(rc, 1)
        self.assertTrue(any(
            "sealed exact area does not equal one assembly target relation"
            in error for error in result["errors"]), result["errors"])

    def test_nonhiking_and_prior_parking_preview_claims_fail_closed(self):
        def nonhiking(qa):
            qa.documents["preview"]["sealed_inputs"][0][
                "nonhiking_sidecar_entry"] = {"mols-trail": "drop"}
            qa.documents["publish_report"]["preview"]["finalization"][
                "nonhiking_sidecar_ids"] = ["mols-trail"]
            qa.rewrite("preview")
            qa.rewrite("publish_report")

        def parking(qa):
            parking_value = [{"id": "fake"}]
            qa.documents["preview"]["areas"][0]["parking"] = parking_value
            sealed = qa.documents["preview"]["sealed_inputs"][0]
            sealed["prior_output_exists"] = True
            sealed["prior_parking"] = parking_value
            qa.documents["publish_report"]["preview"]["finalization"][
                "prior_parking_present"] = True
            qa.rewrite("preview")
            qa.rewrite("publish_report")

        for mutate in (nonhiking, parking):
            with self.subTest(mutate=mutate.__name__):
                self.assert_rejected(mutate)

    def test_app_preview_rejects_every_independent_row_tamper(self):
        def changed_id(qa):
            qa.documents["preview"]["areas"][0]["trails"][0]["id"] = "changed"
            qa.rewrite("preview")

        def invalid_difficulty(qa):
            qa.documents["preview"]["areas"][0]["trails"][0][
                "difficulty"] = "Impossible"
            qa.rewrite("preview")

        def unrelated_geometry(qa):
            qa.documents["preview"]["areas"][0]["trails"][0]["segments"] = [
                [[55.0, 9.0], [55.1, 9.1]]]
            qa.rewrite("preview")

        def missing_trail(qa):
            row = qa.documents["preview"]["areas"][0]
            row["trails"].pop()
            row["trail_count"] -= 1
            qa.rewrite("preview")

        def extra_trail(qa):
            row = qa.documents["preview"]["areas"][0]
            row["trails"].append(copy.deepcopy(row["trails"][0]))
            row["trail_count"] += 1
            qa.rewrite("preview")

        def reordered(qa):
            qa.documents["preview"]["areas"][0]["trails"].reverse()
            qa.rewrite("preview")

        def synchronized_distance(qa):
            row = qa.documents["preview"]["areas"][0]
            row["trails"][0]["distanceMi"] = 9.99
            row["total_mi"] = round(sum(
                trail["distanceMi"] for trail in row["trails"]), 1)
            report = qa.documents["publish_report"]
            report["preview"]["trails"][0]["distance_mi"] = 9.99
            report["preview"]["preview_distance_miles"] = round(sum(
                trail["distanceMi"] for trail in row["trails"]), 6)
            report["preview"]["publisher_total_miles"] = row["total_mi"]
            for key in ("validated_areas", "published_areas"):
                report[key][0]["total_miles"] = row["total_mi"]
            qa.rewrite("preview")
            qa.rewrite("publish_report")

        for mutate in (changed_id, invalid_difficulty, unrelated_geometry,
                       missing_trail, extra_trail, reordered,
                       synchronized_distance):
            with self.subTest(mutate=mutate.__name__):
                self.assert_rejected(mutate)

    def test_visual_rejects_empty_extra_wrong_geometry_and_false_counts(self):
        def synchronized_edit(qa, transform, *, change_counts=False):
            text = (qa.root / validator.VISUAL_RELATIVE).read_text()
            edited = transform(text)
            layers = copy.deepcopy(qa.documents["visual_review"]["layers"])
            if change_counts:
                layers["emitted_paths"] += 1
            qa.write_synchronized_visual(edited, layers=layers)

        def empty_group(qa):
            synchronized_edit(
                qa,
                lambda text: re.sub(
                    r'(<g id="emitted-output">).*?(</g>)', r'\1\2',
                    text, count=1, flags=re.S))

        def unexpected_path(qa):
            synchronized_edit(
                qa,
                lambda text: text.replace(
                    '<g id="emitted-output">',
                    '<g id="emitted-output"><path class="emitted" d="M0,0 L1,1"/>',
                    1), change_counts=True)

        def wrong_geometry(qa):
            synchronized_edit(
                qa,
                lambda text: text.replace(' d="M', ' d="M999,999 L', 1))

        def false_counts(qa):
            layers = copy.deepcopy(qa.documents["visual_review"]["layers"])
            layers["unresolved_endpoint_markers"] = 9
            qa.write_synchronized_visual(
                (qa.root / validator.VISUAL_RELATIVE).read_text(),
                layers=layers)

        for mutate in (empty_group, unexpected_path, wrong_geometry,
                       false_counts):
            with self.subTest(mutate=mutate.__name__):
                self.assert_rejected(mutate)

    def test_visual_rejects_scripts_events_hrefs_and_external_styles(self):
        injections = (
            '<script>alert(1)</script>',
            '<path onload="alert(1)" d="M0,0 L1,1"/>',
            '<use href="#x"/>',
            '<style>@import "https://example.invalid/x.css";</style>',
        )
        for injection in injections:
            with self.subTest(injection=injection):
                def mutate(qa, injection=injection):
                    text = (qa.root / validator.VISUAL_RELATIVE).read_text()
                    qa.write_synchronized_visual(
                        text.replace('</svg>', injection + '</svg>'))
                self.assert_rejected(mutate)

    def test_visual_rejects_complete_canonical_presentation_tampering(self):
        transforms = {
            "opaque-cover": lambda text: text.replace(
                "</svg>", '<rect width="1400" height="1000" fill="white"/></svg>'),
            "zero-width": lambda text: text.replace(
                'width="1400"', 'width="0"', 1),
            "hidden-groups": lambda text: text.replace(
                "<style>", "<style>g { display:none; }", 1),
            "zero-opacity": lambda text: text.replace(
                ".emitted {", ".emitted { opacity: 0;", 1),
            "changed-stroke": lambda text: text.replace(
                "stroke: #6a1b9a", "stroke: #ffffff", 1),
            "changed-fill": lambda text: text.replace(
                "fill: #f8f6ef", "fill: #000000", 1),
        }
        for name, transform in transforms.items():
            with self.subTest(name=name):
                def mutate(qa, transform=transform):
                    text = (qa.root / validator.VISUAL_RELATIVE).read_text()
                    qa.write_synchronized_visual(transform(text))
                self.assert_rejected(mutate)

    def test_golden_binding_rejects_missing_identity_wrong_source_and_stale_sha(self):
        def missing_trail(qa):
            qa.documents["trails"]["features"] = [
                feature for feature in qa.documents["trails"]["features"]
                if 8350111 not in ((feature.get("properties") or {}).get(
                    "root_relation_ids") or [])]
            qa.rewrite("trails")

        def missing_relation(qa):
            for label in ("raw_relation_members", "prefilter_relation_members",
                          "aoi_relation_members"):
                graph = qa.documents[label]
                graph["relations"] = [
                    relation for relation in graph["relations"]
                    if relation["relation_id"] != 8350111]
                graph["root_relation_ids"] = []
                graph["relation_count"] = len(graph["relations"])
                qa.rewrite(label)

        def wrong_name(qa):
            relation = qa.documents["raw_relation_members"]["relations"][0]
            relation["tags"]["name"] = "Wrong stage"
            qa.rewrite("raw_relation_members")

        def wrong_source(qa):
            qa.documents["golden"]["official_fact"]["publisher"] = "Unknown"
            qa.rewrite("golden")

        def stale_sha(qa):
            qa.documents["golden"]["osm_measurement"]["source_sha"] = "f" * 40
            qa.rewrite("golden")

        for mutate in (missing_trail, missing_relation, wrong_name,
                       wrong_source, stale_sha):
            with self.subTest(mutate=mutate.__name__):
                self.assert_rejected(mutate)

    def test_contributing_root_cannot_be_relabelled_removed_thru_hike(self):
        def mutate(qa):
            record = _source_record(
                10, [1, 2], [[10.5, 56.15], [10.6, 56.15]],
                {"highway": "path", "name": "Fused"}, direct=[700])
            qa.set_relation([record], [record["coordinates"]],
                            direct={700: [10]})
            quality = qa.documents["assembly_report"]["quality"]
            audit = next(row for row in quality["relation_member_audit"]
                         if row["relation_id"] == 700)
            audit["assembly_status"] = "removed-thru-hike"
            audit["emitted_in_exact_area"] = False
            audit["review_status"] = "accepted"
            audit["unresolved_reasons"] = []
            for status in audit["relation_scope_statuses"].values():
                status["render_disposition"] = \
                    "removed-thru-hike-informational"
                status["terminal_reason"] = None
            for member in audit["direct_way_members"]:
                member["render_disposition"] = \
                    "removed-thru-hike-informational"
                member["terminal_reason"] = None
            qa.rewrite("assembly_report")

        rc, result = self._run(mutate)
        self.assertEqual(rc, 1)
        self.assertTrue(any(
            "assembly status contradicts independently derived 'emitted'"
            in error for error in result["errors"]), result["errors"])
        self.assertTrue(any(
            "hides an emitted candidate root" in error
            for error in result["errors"]), result["errors"])

    def test_unsafe_in_area_member_cannot_be_declared_away(self):
        def mutate(qa):
            safe = _source_record(
                10, [1, 2], [[10.5, 56.15], [10.55, 56.15]],
                {"highway": "path", "name": "Fused"}, direct=[700])
            unsafe = _source_record(
                11, [2, 3], [[10.55, 56.15], [10.6, 56.15]],
                {"highway": "motorway"}, direct=[700])
            qa.set_relation([safe], [safe["coordinates"]],
                            direct={700: [10]})
            qa.set_relation_authority({700: [10, 11]}, [safe, unsafe])
            qa.set_topology_way(
                11, tags=unsafe["tags"], node_ids=unsafe["node_ids"],
                coordinates=unsafe["coordinates"])
            qa.documents["raw"]["features"].append({
                "type": "Feature", "id": "w11",
                "properties": copy.deepcopy(unsafe["tags"]),
                "geometry": {"type": "LineString",
                             "coordinates": copy.deepcopy(
                                 unsafe["coordinates"])},
            })
            properties = qa.documents["trails"]["features"][0]["properties"]
            properties["direct_relation_way_ids"] = {700: [10, 11]}
            quality = qa.documents["assembly_report"]["quality"]
            audit = next(row for row in quality["relation_member_audit"]
                         if row["relation_id"] == 700)
            self.assertIn("unsafe-member-in-exact-area",
                          audit["unresolved_reasons"])
            audit["assembly_status"] = "removed-thru-hike"
            audit["emitted_in_exact_area"] = False
            audit["review_status"] = "accepted"
            audit["unresolved_reasons"] = []
            for status in audit["relation_scope_statuses"].values():
                status["render_disposition"] = \
                    "removed-thru-hike-informational"
                status["terminal_reason"] = None
            for member in audit["direct_way_members"]:
                member["render_disposition"] = \
                    "removed-thru-hike-informational"
                member["terminal_reason"] = None
            quality["relation_member_unresolved"] = []
            quality["relation_member_unresolved_count"] = 0
            quality["validation_failures"] = []
            qa.rewrite("raw")
            qa.rewrite("trails")
            qa.rewrite("assembly_report")

        rc, result = self._run(mutate)
        self.assertEqual(rc, 1)
        self.assertTrue(any(
            "unsafe-member-in-exact-area" in error
            for error in result["errors"]), result["errors"])
        self.assertTrue(any(
            "assembly status contradicts independently derived 'emitted'"
            in error for error in result["errors"]), result["errors"])

    def test_fused_candidate_root_set_omission_extra_and_order_fail(self):
        for tampered_roots in ([700], [700, 701, 8350111], [701, 700]):
            with self.subTest(root_relation_ids=tampered_roots):
                def mutate(qa, roots=tampered_roots):
                    records = [
                        _source_record(
                            10, [1, 2], [[10.5, 56.15], [10.55, 56.15]],
                            {"highway": "path", "name": "Fused"},
                            direct=[700]),
                        _source_record(
                            20, [2, 3], [[10.55, 56.15], [10.6, 56.15]],
                            {"highway": "path"}, direct=[701]),
                    ]
                    qa.set_relation(
                        records, [record["coordinates"] for record in records],
                        direct={700: [10], 701: [20]})
                    feature = qa.documents["trails"]["features"][0]
                    self.assertEqual(
                        feature["properties"]["root_relation_ids"], [700, 701])
                    feature["properties"]["root_relation_ids"] = list(roots)
                    qa.rewrite("trails")

                rc, result = self._run(mutate)
                self.assertEqual(rc, 1)
                self.assertTrue(any(
                    "root relation ids" in error
                    for error in result["errors"]), result["errors"])

    def test_synchronized_root_omission_cannot_hide_authority_contribution(self):
        def mutate(qa):
            records = [
                _source_record(
                    10, [1, 2], [[10.5, 56.15], [10.55, 56.15]],
                    {"highway": "path", "name": "Fused"}, direct=[700]),
                _source_record(
                    20, [2, 3], [[10.55, 56.15], [10.6, 56.15]],
                    {"highway": "path"}, direct=[701]),
            ]
            qa.set_relation(
                records, [record["coordinates"] for record in records],
                direct={700: [10], 701: [20]})
            properties = qa.documents["trails"]["features"][0]["properties"]
            properties["relation_ids"] = [700]
            properties["root_relation_ids"] = [700]
            properties["direct_relation_way_ids"] = {700: [10]}
            properties["source_ways"][1]["direct_relation_ids"] = []
            quality = qa.documents["assembly_report"]["quality"]
            audit = next(row for row in quality["relation_member_audit"]
                         if row["relation_id"] == 701)
            self.assertEqual(audit["eligible_main_way_ids"], [20])
            audit["emitted_in_exact_area"] = False
            quality["restored_road_relations"] = [
                row for row in quality["restored_road_relations"]
                if row["relation_id"] != 701
            ]
            quality["restored_road_aggregate"]["total_relation_miles"] = round(
                sum(row["total_relation_miles"]
                    for row in quality["restored_road_relations"]), 6)
            qa.rewrite("trails")
            qa.rewrite("assembly_report")

        rc, result = self._run(mutate)
        self.assertEqual(rc, 1)
        self.assertTrue(any(
            "root relation ids contradict contributing graph roots" in error
            for error in result["errors"]), result["errors"])
        self.assertTrue(any(
            "r701 exact-area emission flag" in error
            for error in result["errors"]), result["errors"])

    def test_synchronized_identical_and_contained_way_erasure_fails(self):
        cases = {
            "identical": (
                _source_record(
                    10, [1, 2], [[10.5, 56.15], [10.6, 56.15]],
                    {"highway": "path", "name": "Fused"}, direct=[700]),
                _source_record(
                    20, [1, 2], [[10.5, 56.15], [10.6, 56.15]],
                    {"highway": "path"}, direct=[701]),
            ),
            "contained": (
                _source_record(
                    10, [1, 2, 3, 4],
                    [[10.5, 56.15], [10.53, 56.15],
                     [10.57, 56.15], [10.6, 56.15]],
                    {"highway": "path", "name": "Fused"}, direct=[700]),
                _source_record(
                    20, [2, 3], [[10.53, 56.15], [10.57, 56.15]],
                    {"highway": "path"}, direct=[701]),
            ),
        }
        for name, source_records in cases.items():
            with self.subTest(name=name):
                def mutate(qa, source_records=source_records):
                    records = copy.deepcopy(list(source_records))
                    qa.set_relation(
                        records,
                        [record["coordinates"] for record in records],
                        direct={700: [10], 701: [20]})
                    properties = qa.documents["trails"]["features"][0][
                        "properties"]
                    properties.update({
                        "member_ways": [10],
                        "ckey": "w10",
                        "relation_ids": [700],
                        "root_relation_ids": [700],
                        "direct_relation_way_ids": {700: [10]},
                        "source_geometry_way_ids": [10],
                        "source_ways": [copy.deepcopy(records[0])],
                        "rendered_source_way_ids": [10],
                    })
                    properties["source_ways"][0][
                        "direct_relation_ids"] = [700]
                    qa.documents["trails"]["features"][0]["geometry"] = {
                        "type": "MultiLineString",
                        "coordinates": [copy.deepcopy(
                            records[0]["coordinates"])],
                    }
                    quality = qa.documents["assembly_report"]["quality"]
                    audit = next(
                        row for row in quality["relation_member_audit"]
                        if row["relation_id"] == 701)
                    audit["emitted_in_exact_area"] = False
                    quality["restored_road_relations"] = [
                        row for row in quality["restored_road_relations"]
                        if row["relation_id"] != 701
                    ]
                    quality["restored_road_aggregate"].update({
                        "restored_way_count": 0,
                        "restored_miles": 0.0,
                        "total_relation_miles": round(sum(
                            row["total_relation_miles"] for row in
                            quality["restored_road_relations"]), 6),
                        "restored_share": 0.0,
                    })
                    qa.rewrite("trails")
                    qa.rewrite("assembly_report")

                rc, result = self._run(mutate)
                self.assertEqual(rc, 1)
                self.assertTrue(any(
                    "r701 has no output candidate or verified producer drop"
                    in error for error in result["errors"]), result["errors"])

    def test_shared_way_derives_every_same_identity_root_in_authority_order(self):
        def prepare(qa):
            record = _source_record(
                10, [1, 2], [[10.5, 56.15], [10.6, 56.15]],
                {"highway": "path", "name": "Fused"}, direct=[700, 701])
            qa.set_relation(
                [record], [record["coordinates"]],
                direct={700: [10], 701: [10]})

        rc, result = self._run(prepare)
        self.assertEqual(rc, 0, result["errors"])

        def omit_shared_root(qa):
            prepare(qa)
            properties = qa.documents["trails"]["features"][0]["properties"]
            properties["relation_ids"] = [700]
            properties["root_relation_ids"] = [700]
            properties["direct_relation_way_ids"] = {700: [10]}
            properties["source_ways"][0]["direct_relation_ids"] = [700]
            qa.rewrite("trails")

        self.assert_rejected(omit_shared_root)

    def test_relation_candidate_unbound_name_fails_closed(self):
        def mutate(qa):
            record = _source_record(
                10, [1, 2], [[10.5, 56.15], [10.6, 56.15]],
                {"highway": "path", "name": "Fused"}, direct=[700])
            qa.set_relation([record], [record["coordinates"]],
                            direct={700: [10]})
            qa.documents["trails"]["features"][0]["properties"]["name"] = \
                "Unbound Candidate"
            qa.rewrite("trails")

        rc, result = self._run(mutate)
        self.assertEqual(rc, 1)
        self.assertTrue(any(
            "name contradicts authority-derived root r700" in error
            for error in result["errors"]), result["errors"])

    def test_duplicate_relation_candidate_name_ckey_is_ambiguous(self):
        def mutate(qa):
            record = _source_record(
                10, [1, 2], [[10.5, 56.15], [10.6, 56.15]],
                {"highway": "path", "name": "Fused"}, direct=[700])
            qa.set_relation([record], [record["coordinates"]],
                            direct={700: [10]})
            duplicate = copy.deepcopy(qa.documents["trails"]["features"][0])
            duplicate["properties"]["quality_candidate_index"] = 2
            qa.documents["trails"]["features"].append(duplicate)
            report = qa.documents["assembly_report"]
            report["pre_clip_trail_count"] = 3
            report["post_clip_trail_count"] = 3
            report["assembled_trail_count"] = 3
            report["quality"]["candidate_count"] = 3
            report["quality"]["kept_count"] = 3
            qa.rewrite("trails")
            qa.rewrite("assembly_report")

        rc, result = self._run(mutate)
        self.assertEqual(rc, 1)
        self.assertTrue(any(
            "name/ckey binding is ambiguous" in error
            for error in result["errors"]), result["errors"])

    def test_synchronized_extra_root_without_contributing_way_fails(self):
        def mutate(qa):
            record = _source_record(
                10, [1, 2], [[10.5, 56.15], [10.6, 56.15]],
                {"highway": "path", "name": "Fused"}, direct=[700])
            qa.set_relation(
                [record], [record["coordinates"]],
                direct={700: [10], 701: [20]})

        rc, result = self._run(mutate)
        self.assertEqual(rc, 1)
        self.assertTrue(any(
            "root relation ids contradict contributing graph roots" in error
            for error in result["errors"]), result["errors"])

    def test_relation_slice_provenance_rejects_mislabel_swap_wrong_and_tamper(self):
        def mislabeled(qa):
            qa.documents["raw_relation_members"]["source"]["artifact"] = \
                validator.AOI_PBF_RELATIVE
            qa.rewrite("raw_relation_members")

        def swapped(qa):
            raw_path = qa.root / validator.RAW_PBF_RELATIVE
            prefilter_path = qa.root / validator.PREFILTER_PBF_RELATIVE
            raw_bytes, prefilter_bytes = (
                raw_path.read_bytes(), prefilter_path.read_bytes())
            raw_path.write_bytes(prefilter_bytes)
            prefilter_path.write_bytes(raw_bytes)
            qa._refresh_manifest_seals()

        def wrong_source(qa):
            qa.documents["raw_relation_members"]["source"]["sha256"] = "0" * 64
            qa.rewrite("raw_relation_members")

        def tampered(qa):
            (qa.root / validator.RAW_PBF_RELATIVE).write_bytes(b"tampered")
            qa._refresh_manifest_seals()

        for mutate in (mislabeled, swapped, wrong_source, tampered):
            with self.subTest(mutate=mutate.__name__):
                self.assert_rejected(mutate)

    def test_aoi_content_accepts_exact_and_protected_smart_members(self):
        cases = (
            (RELATION_ID, {"name": validator.AREA_NAME,
                           "boundary": "national_park", "type": "boundary"}),
            (900, {"name": "Protected completion",
                   "boundary": "protected_area", "type": "multipolygon"}),
        )
        for relation_id, relation_tags in cases:
            with self.subTest(relation_id=relation_id):
                def mutate(qa, relation_id=relation_id,
                           relation_tags=relation_tags):
                    qa.set_topology_way(
                        999, tags={"source": "boundary-survey"},
                        node_ids=[9991, 9992],
                        coordinates=[[8.0, 55.0], [8.1, 55.1]])
                    qa.set_topology_relation(
                        relation_id, tags=relation_tags,
                        members=[{"type": "way", "ref": 999,
                                  "role": "outer"}])
                    qa.documents["raw"]["features"].append({
                        "type": "Feature",
                        "id": "w999",
                        "properties": {"source": "boundary-survey"},
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [[8.0, 55.0], [8.1, 55.1]],
                        },
                    })
                    qa.rewrite("raw")

                rc, result = self._run(mutate)
                self.assertEqual(rc, 0, result["errors"])

    def test_boundary_id_without_selector_tags_cannot_bind_outside_way(self):
        def mutate(qa):
            qa.set_topology_way(
                999, tags={"source": "boundary-survey"},
                node_ids=[9991, 9992],
                coordinates=[[8.0, 55.0], [8.1, 55.1]])
            qa.set_topology_relation(
                RELATION_ID,
                tags={"name": validator.AREA_NAME, "type": "multipolygon"},
                members=[{"type": "way", "ref": 999, "role": "outer"}])
            qa.documents["raw"]["features"].append({
                "type": "Feature", "id": "w999",
                "properties": {"source": "boundary-survey"},
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[8.0, 55.0], [8.1, 55.1]],
                },
            })
            qa.rewrite("raw")

        rc, result = self._run(mutate)
        self.assertEqual(rc, 1)
        self.assertTrue(any(
            "outside bbox without selected-root or verified smart-area binding"
            in error for error in result["errors"]), result["errors"])

    def test_aoi_content_rejects_unrelated_relation_bound_outside_way(self):
        def mutate(qa):
            qa.set_topology_way(
                999, tags={"name": "Country Way", "highway": "path"},
                node_ids=[9991, 9992],
                coordinates=[[8.0, 55.0], [8.1, 55.1]])
            qa.set_topology_relation(
                900,
                tags={"name": "Unrelated relation", "type": "multipolygon"},
                members=[{"type": "way", "ref": 999, "role": "outer"}])
            qa.documents["raw"]["features"].append({
                "type": "Feature",
                "id": "w999",
                "properties": {"name": "Country Way", "highway": "path"},
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[8.0, 55.0], [8.1, 55.1]],
                },
            })
            qa.rewrite("raw")

        rc, result = self._run(mutate)
        self.assertEqual(rc, 1)
        self.assertTrue(any(
            "outside bbox without selected-root or verified smart-area binding"
            in error for error in result["errors"]), result["errors"])

    def test_aoi_topology_hiking_relation_tags_and_members_are_bound(self):
        for field in ("tags", "members"):
            with self.subTest(field=field):
                def mutate(qa, field=field):
                    relation = next(
                        value for value in
                        qa.documents["raw_way_topology"]["relations"]
                        if value["relation_id"] == 8350111)
                    if field == "tags":
                        relation["tags"]["name"] = "Rewritten"
                    else:
                        relation["members"][0]["role"] = "alternative"
                    qa.rewrite("raw_way_topology")
                    qa._refresh_scope_trust()

                rc, result = self._run(mutate)
                self.assertEqual(rc, 1)
                self.assertTrue(any(
                    "contradicts AOI topology" in error
                    for error in result["errors"]), result["errors"])

    def test_destination_poi_bytes_must_be_bound_to_external_stage_trust(self):
        def mutate(qa):
            qa.set_topology_destination(
                900, tags={"name": "Mols Summit", "natural": "peak"},
                coordinate=[10.55, 56.15], refresh_trust=False)

        rc, result = self._run(mutate)
        self.assertEqual(rc, 1)
        self.assertIn(
            "AOI way topology disagrees with trusted stage output",
            result["errors"])

    def test_destination_poi_schema_name_class_and_count_are_replayed(self):
        def wrong_schema(qa):
            qa.documents["raw_way_topology"]["schema_version"] = 2
            qa.rewrite("raw_way_topology")
            qa._refresh_scope_trust()

        def wrong_name(qa):
            qa.set_topology_destination(
                900, tags={"name": "Mols Summit", "natural": "peak"},
                coordinate=[10.55, 56.15])
            qa.documents["raw_way_topology"]["destination_pois"][0][
                "name"] = "Forged Summit"
            qa.rewrite("raw_way_topology")
            qa._refresh_scope_trust()

        def wrong_class(qa):
            qa.set_topology_destination(
                900, tags={"name": "Mols Summit", "natural": "peak"},
                coordinate=[10.55, 56.15])
            qa.documents["raw_way_topology"]["destination_pois"][0][
                "eligibility_class"] = "tourism=viewpoint"
            qa.rewrite("raw_way_topology")
            qa._refresh_scope_trust()

        def wrong_count(qa):
            qa.set_topology_destination(
                900, tags={"name": "Mols Summit", "natural": "peak"},
                coordinate=[10.55, 56.15])
            qa.documents["raw_way_topology"]["destination_poi_count"] = 2
            qa.rewrite("raw_way_topology")
            qa._refresh_scope_trust()

        cases = (
            (wrong_schema, "raw way-topology schema_version must be 3"),
            (wrong_name,
             "raw-way-topology.destination_pois[0] normalized display name "
             "contradicts tags"),
            (wrong_class,
             "raw-way-topology.destination_pois[0] eligibility class "
             "contradicts complete tags"),
            (wrong_count,
             "raw way-topology destination POI count is inconsistent"),
        )
        for mutate, message in cases:
            with self.subTest(mutate=mutate.__name__):
                rc, result = self._run(mutate)
                self.assertEqual(rc, 1)
                self.assertIn(message, result["errors"])

    def test_raw_destination_points_seal_complete_topology_ledger(self):
        tags = {"name": "Nearest Omitted Peak", "natural": "peak"}
        coordinate = [10.55, 56.15]

        def complete(qa):
            qa.set_topology_destination(
                900, tags=tags, coordinate=coordinate)

        rc, result = self._run(complete)
        self.assertEqual(rc, 0, result["errors"])

        def missing_ledger_row(qa):
            qa.set_topology_destination(
                900, tags=tags, coordinate=coordinate)
            qa.documents["raw_way_topology"].update({
                "destination_poi_count": 0,
                "destination_pois": [],
            })
            qa.rewrite("raw_way_topology")
            qa._refresh_scope_trust()

        rc, result = self._run(missing_ledger_row)
        self.assertEqual(rc, 1)
        self.assertIn(
            "raw destination point n900 is missing from AOI topology ledger",
            result["errors"])

        def extra_ledger_row(qa):
            qa.set_topology_destination(
                900, tags=tags, coordinate=coordinate)
            qa.documents["raw"]["features"] = [
                feature for feature in qa.documents["raw"]["features"]
                if feature.get("id") != "n900"
            ]
            qa.rewrite("raw")

        rc, result = self._run(extra_ledger_row)
        self.assertEqual(rc, 1)
        self.assertIn(
            "AOI topology destination n900 is absent from raw GeoJSON points",
            result["errors"])

        def mismatched_raw_name(qa):
            qa.set_topology_destination(
                900, tags=tags, coordinate=coordinate)
            raw_point = next(
                feature for feature in qa.documents["raw"]["features"]
                if feature.get("id") == "n900")
            raw_point["properties"]["name"] = "Different Peak"
            qa.rewrite("raw")

        def mismatched_raw_coordinate(qa):
            qa.set_topology_destination(
                900, tags=tags, coordinate=coordinate)
            raw_point = next(
                feature for feature in qa.documents["raw"]["features"]
                if feature.get("id") == "n900")
            raw_point["geometry"]["coordinates"] = [10.5501, 56.15]
            qa.rewrite("raw")

        def mismatched_raw_tags_and_class(qa):
            qa.set_topology_destination(
                900, tags=tags, coordinate=coordinate)
            raw_point = next(
                feature for feature in qa.documents["raw"]["features"]
                if feature.get("id") == "n900")
            raw_point["properties"] = {
                "name": "Nearest Omitted Peak", "tourism": "viewpoint",
            }
            qa.rewrite("raw")

        for mutate in (
                mismatched_raw_name, mismatched_raw_coordinate,
                mismatched_raw_tags_and_class):
            with self.subTest(mutate=mutate.__name__):
                rc, result = self._run(mutate)
                self.assertEqual(rc, 1)
                self.assertIn(
                    "raw destination point n900 contradicts AOI topology ledger",
                    result["errors"])

    def test_terminal_absorption_unresolved_report_is_counted_and_terminal(self):
        def nonempty(qa):
            feature = qa.golden_trail
            properties = feature["properties"]
            quality = qa.documents["assembly_report"]["quality"]
            quality["terminal_absorption_unresolved_count"] = 1
            quality["terminal_absorption_unresolved"] = [{
                "reason": "missing-target",
                "source_candidate": {
                    "candidate_id": 1,
                    "population": "published",
                    "ckey": properties["ckey"],
                    "name": properties["name"],
                    "source": "relation",
                    "root_relation_ids": properties["root_relation_ids"],
                    "geometry_sha256": validator._publisher_json_sha256(
                        feature["geometry"]),
                    "quality_disposition": properties["quality_disposition"],
                    "removed_category": None,
                    "removed_reason": None,
                    "removal_stage": None,
                    "successor_candidate_ids": [],
                },
                "target_candidates": [{
                    "candidate_id": 99,
                    "population": "missing",
                    "ckey": None,
                    "name": None,
                    "source": None,
                    "root_relation_ids": [],
                    "geometry_sha256": None,
                    "quality_disposition": None,
                    "removed_category": None,
                    "removed_reason": None,
                    "removal_stage": None,
                    "successor_candidate_ids": [],
                }],
            }]
            qa.rewrite("assembly_report")

        rc, result = self._run(nonempty)
        self.assertEqual(rc, 1)
        self.assertEqual(result["unresolved_terminal_absorption_count"], 1)
        self.assertIn(
            "assembly terminal relation absorption unresolved population is nonzero",
            result["errors"])

        def count_tamper(qa):
            qa.documents["assembly_report"]["quality"][
                "terminal_absorption_unresolved_count"] = 1
            qa.rewrite("assembly_report")

        rc, result = self._run(count_tamper)
        self.assertEqual(rc, 1)
        self.assertIn(
            "assembly terminal absorption unresolved count is inconsistent",
            result["errors"])

    def test_aoi_receipt_population_must_match_source_topology(self):
        for object_type, message in (
                ("ways", "AOI receipt way count contradicts source topology"),
                ("relations",
                 "AOI receipt relation count contradicts source topology")):
            with self.subTest(object_type=object_type):
                def mutate(qa, object_type=object_type):
                    receipt = qa.documents["aoi_scope_receipt"]
                    receipt["compact_output"]["object_counts"][object_type] += 1
                    qa.rewrite("aoi_scope_receipt")
                    qa._refresh_scope_trust()

                rc, result = self._run(mutate)
                self.assertEqual(rc, 1)
                self.assertTrue(any(
                    message in error for error in result["errors"]),
                    result["errors"])

    def test_synchronized_pairwise_scope_relabels_fail_trusted_stage_values(self):
        cases = {
            "raw-to-aoi": {"aoi": "raw-denmark"},
            "prefilter-to-aoi": {"aoi": "prefiltered-denmark"},
            "aoi-to-raw": {"raw-denmark": "aoi"},
            "aoi-to-prefilter": {"prefiltered-denmark": "aoi"},
            "raw-prefilter-swap": {
                "raw-denmark": "prefiltered-denmark",
                "prefiltered-denmark": "raw-denmark",
            },
        }
        for name, mapping in cases.items():
            with self.subTest(name=name):
                rc, result = self._run(
                    lambda qa, mapping=mapping:
                    qa._synchronized_scope_relabel(mapping))
                self.assertEqual(rc, 1)
                self.assertTrue(any(
                    "trusted stage output" in error
                    for error in result["errors"]), result["errors"])
                unexpected = [
                    error for error in result["errors"]
                    if "trusted stage output" not in error
                    and "AOI compact PBF is byte-identical" not in error
                    and "AOI receipt way count" not in error
                    and "AOI receipt relation count" not in error
                ]
                self.assertEqual(unexpected, [], result["errors"])

    def test_synchronized_three_scope_byte_relabel_is_rejected(self):
        rc, result = self._run(
            lambda qa: qa._synchronized_scope_relabel({
                "prefiltered-denmark": "raw-denmark",
                "aoi": "raw-denmark",
            }))
        self.assertEqual(rc, 1)
        self.assertTrue(any(
            "trusted stage output" in error
            for error in result["errors"]), result["errors"])

    def test_reconstructed_aoi_argv_cannot_replace_executed_receipt(self):
        def mutate(qa):
            receipt = qa.documents["aoi_scope_receipt"]
            receipt["osmium"]["command"] = [
                "osmium", "extract", "--strategy=smart", "--bbox",
                validator.PILOT_BBOX, "data/hiking.osm.pbf",
                "--overwrite", "-o", "data/aoi/mols-bjerge.osm.pbf",
            ]
            qa.rewrite("aoi_scope_receipt")

        rc, result = self._run(mutate)
        self.assertEqual(rc, 1)
        self.assertTrue(any(
            "aoi scope receipt disagrees with trusted stage output" in error
            for error in result["errors"]), result["errors"])

    def test_trusted_but_reconstructed_aoi_argv_still_fails(self):
        def mutate(qa):
            receipt = qa.documents["aoi_scope_receipt"]
            command = list(receipt["osmium"]["command"])
            command[-3:] = ["--overwrite", "-o", validator.AOI_PBF_RELATIVE]
            receipt["osmium"]["command"] = command
            qa.rewrite("aoi_scope_receipt")
            qa._refresh_scope_trust()

        rc, result = self._run(mutate)
        self.assertEqual(rc, 1)
        self.assertTrue(any(
            "AOI extract command is invalid" in error
            for error in result["errors"]), result["errors"])

    def test_trusted_but_modified_prefilter_argv_still_fails(self):
        def mutate(qa):
            receipt = qa.documents["prefilter_transform_receipt"]
            receipt["osmium"]["command"].insert(3, "w/highway=construction")
            qa.rewrite("prefilter_transform_receipt")
            qa._refresh_scope_trust()

        rc, result = self._run(mutate)
        self.assertEqual(rc, 1)
        self.assertTrue(any(
            "prefilter transformation command/provenance is invalid" in error
            for error in result["errors"]), result["errors"])

    def test_swapped_receipt_and_fake_parent_are_rejected(self):
        def swapped_receipts(qa):
            raw = qa.documents["raw_scope_receipt"]
            prefilter = qa.documents["prefilter_scope_receipt"]
            qa.documents["raw_scope_receipt"] = prefilter
            qa.documents["prefilter_scope_receipt"] = raw
            qa.rewrite("raw_scope_receipt")
            qa.rewrite("prefilter_scope_receipt")

        def fake_parent(qa):
            fake = copy.deepcopy(
                qa.documents["prefilter_scope_receipt"]["parent_pbf"])
            qa.documents["raw_scope_receipt"]["parent_pbf"] = fake
            qa.documents["prefilter_transform_receipt"]["parent_pbf"] = fake
            qa.rewrite("raw_scope_receipt")
            qa.rewrite("prefilter_transform_receipt")

        for mutate in (swapped_receipts, fake_parent):
            with self.subTest(mutate=mutate.__name__):
                self.assert_rejected(mutate)

    def test_legitimate_identical_content_relation_slices_are_accepted(self):
        def mutate(qa):
            raw = (qa.root / validator.RAW_PBF_RELATIVE).read_bytes()
            prefilter_path = qa.root / validator.PREFILTER_PBF_RELATIVE
            prefilter_path.write_bytes(raw)
            identity = {
                "kind": "runner-local-osm-pbf",
                "artifact": validator.PREFILTER_PBF_RELATIVE,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "bytes": len(raw),
            }
            qa.documents["prefilter_relation_members"]["source"] = identity
            qa.documents["assembly_report"]["relation_authority"][
                "scopes"]["prefiltered-denmark"] = identity
            qa._sync_scope_receipts()
            qa.rewrite("prefilter_relation_members")
            qa.rewrite("assembly_report")
            qa._refresh_manifest_seals()
            qa._refresh_scope_trust()

        rc, result = self._run(mutate)
        self.assertEqual(rc, 0, result["errors"])

    def test_maltgaarden_class_accepts_tag_supersets_and_equivalent_new_id(self):
        def tag_superset(qa):
            feature = next(
                row for row in qa.documents["raw"]["features"]
                if row.get("id") == "w1027606528")
            feature["properties"].update({
                "surface": "paving_stones", "area": "yes",
                "lit": "yes", "source": "survey",
            })
            topology = next(value for value in
                            qa.documents["raw_way_topology"]["ways"]
                            if value["way_id"] == 1027606528)
            topology["tags"] = copy.deepcopy(feature["properties"])
            qa.rewrite("raw_way_topology")
            qa.rewrite("raw")
            qa._refresh_scope_trust()

        rc, result = self._run(tag_superset)
        self.assertEqual(rc, 0, result["errors"])

        def equivalent_new_id(qa):
            feature = next(
                row for row in qa.documents["raw"]["features"]
                if row.get("id") == "w1027606528")
            feature["id"] = "w999"
            feature["properties"].update({"area": "yes", "surface": "gravel"})
            removal = qa.documents["removed"]["features"][0]
            removal["properties"]["member_ways"] = [999]
            removal["properties"]["name"] = "Equivalent pedestrian court"
            old = next(value for value in
                       qa.documents["raw_way_topology"]["ways"]
                       if value["way_id"] == 1027606528)
            qa.documents["raw_way_topology"]["ways"].remove(old)
            old.update({"way_id": 999,
                        "tags": copy.deepcopy(feature["properties"])})
            qa.documents["raw_way_topology"]["ways"].append(old)
            qa.documents["raw_way_topology"]["ways"].sort(
                key=lambda value: value["way_id"])
            qa.rewrite("raw_way_topology")
            qa.rewrite("raw")
            qa.rewrite("removed")
            qa._refresh_scope_trust()

        rc, result = self._run(equivalent_new_id)
        self.assertEqual(rc, 0, result["errors"])

    def test_unnamed_pedestrian_polygon_needs_no_removal_row(self):
        def mutate(qa):
            qa.documents["raw"]["features"].append({
                "type": "Feature",
                "id": "w998",
                "properties": {"highway": "pedestrian", "area": "yes"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [10.72, 56.20], [10.73, 56.20],
                        [10.73, 56.21], [10.72, 56.20],
                    ]],
                },
            })
            qa.set_topology_way(
                998, tags={"highway": "pedestrian", "area": "yes"},
                node_ids=[9981, 9982, 9983, 9981],
                coordinates=[
                    [10.72, 56.20], [10.73, 56.20],
                    [10.73, 56.21], [10.72, 56.20],
                ])
            qa.rewrite("raw")

        rc, result = self._run(mutate)
        self.assertEqual(rc, 0, result["errors"])

    def test_maltgaarden_area_no_is_linear_and_missing_class_exclusion_fails(self):
        def area_no(qa):
            feature = next(
                row for row in qa.documents["raw"]["features"]
                if row.get("id") == "w1027606528")
            feature["properties"]["area"] = "no"
            topology = next(value for value in
                            qa.documents["raw_way_topology"]["ways"]
                            if value["way_id"] == 1027606528)
            topology["tags"]["area"] = "no"
            qa.documents["removed"]["features"] = []
            qa.rewrite("raw_way_topology")
            qa.rewrite("raw")
            qa.rewrite("removed")
            qa._refresh_scope_trust()

        rc, result = self._run(area_no)
        self.assertEqual(rc, 0, result["errors"])

        def missing(qa):
            qa.documents["removed"]["features"] = []
            qa.rewrite("removed")

        self.assert_rejected(missing)

    def test_pedestrian_closedness_uses_topology_not_visual_geometry_type(self):
        def open_topology(qa):
            topology = next(value for value in
                            qa.documents["raw_way_topology"]["ways"]
                            if value["way_id"] == 1027606528)
            topology["node_ids"][-1] = 9004
            qa.rewrite("raw_way_topology")
            qa._refresh_scope_trust()

        rc, result = self._run(open_topology)
        self.assertEqual(rc, 1)
        self.assertTrue(any("not a closed standalone pedestrian area" in error
                            for error in result["errors"]), result["errors"])

    def test_preview_golden_visual_and_pbf_tampering_fail_closed(self):
        def preview_distance(qa):
            qa.documents["preview"]["areas"][0]["trails"][0][
                "distanceMi"] = 9.99
            qa.rewrite("preview")

        def official_geometry(qa):
            qa.documents["golden"]["geometry"] = {
                "type": "LineString", "coordinates": [[10, 56], [11, 57]]}
            qa.rewrite("golden")

        def external_visual(qa):
            _write(qa.root / validator.VISUAL_RELATIVE,
                   '<svg xmlns="http://www.w3.org/2000/svg">'
                   '<image href="https://example.invalid/tile.png"/></svg>')
            qa._refresh_manifest_seals()

        def changed_pbf(qa):
            (qa.root / validator.AOI_PBF_RELATIVE).write_bytes(b"changed-pbf")
            qa._refresh_manifest_seals()

        for mutate in (preview_distance, official_geometry,
                       external_visual, changed_pbf):
            with self.subTest(mutate=mutate.__name__):
                self.assert_rejected(mutate)

    def test_prefilter_authority_tag_mismatch_is_terminal(self):
        def mutate(qa):
            record = _source_record(
                10, [1, 2], [[10.5, 56.15], [10.6, 56.15]],
                {"highway": "path"}, direct=[700])
            qa.set_relation([record], [record["coordinates"]])
            graph = qa.documents["prefilter_relation_members"]
            graph["relations"][0]["direct_ways"][0]["tags"]["highway"] = \
                "service"
            qa.rewrite("prefilter_relation_members")
            qa._refresh_scope_trust()

        rc, result = self._run(mutate)
        self.assertEqual(rc, 1)
        self.assertTrue(any("contradicts raw Denmark" in error
                            for error in result["errors"]), result["errors"])

    def test_malformed_and_each_missing_mandatory_artifact_are_rejected(self):
        self.assert_rejected(malformed="reports/assembly.json")
        required = [
            *validator.REQUIRED_JSON_FILES.values(),
            *validator.REQUIRED_TEXT_FILES.values(),
            *validator.PBF_RELATIVES_BY_SCOPE.values(),
        ]
        for relative in required:
            with self.subTest(relative=relative):
                self.assert_rejected(remove=relative)


@unittest.skipUnless(HAVE_SHAPELY, "shapely not installed")
class ProducerValidatorRoundTrip(unittest.TestCase):
    def _run(self, case, mutate=None):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "qa"
            qa = _QA(root)
            pois = []
            per_area_merge = False
            model_areas = []
            if case == "disconnected":
                nodes = {
                    1: (10.5, 56.15), 2: (10.54, 56.15),
                    3: (10.7, 56.2), 4: (10.75, 56.2),
                }
                ways = {
                    10: {"nodes": [1, 2],
                         "tags": {"highway": "path", "name": "Signed Route"}},
                    11: {"nodes": [3, 4], "tags": {"highway": "path"}},
                }
                boundary_geometry = box(10.4, 56.1, 10.9, 56.35)
            elif case in {"boundary", "sub-rounding-boundary"}:
                if case == "boundary":
                    nodes = {
                        1: (10.45, 56.15), 2: (10.55, 56.15),
                        3: (10.65, 56.15), 4: (10.75, 56.15),
                    }
                    way_nodes = [1, 2, 3, 4]
                    boundary_geometry = MultiPolygon([
                        box(10.44, 56.1, 10.52, 56.25),
                        box(10.68, 56.1, 10.76, 56.25),
                    ])
                else:
                    gap = 0.000001
                    nodes = {
                        1: (10.5, 56.15), 2: (10.505, 56.15),
                        3: (10.51, 56.15),
                    }
                    way_nodes = [1, 2, 3]
                    boundary_geometry = MultiPolygon([
                        box(10.499, 56.1, 10.505, 56.25),
                        box(10.505 + gap, 56.1, 10.511, 56.25),
                    ])
                ways = {
                    10: {"nodes": way_nodes,
                         "tags": {"highway": "path", "name": "Signed Route"}},
                }
            elif case == "min-length-drop":
                nodes = {
                    1: (10.5000, 56.15), 2: (10.5010, 56.15),
                }
                ways = {10: {
                    "nodes": [1, 2],
                    "tags": {"highway": "path", "name": "Tiny Route"},
                }}
                boundary_geometry = box(10.4, 56.1, 10.9, 56.35)
            elif case in {"clip-sliver-drop", "outside-drop"}:
                nodes = {
                    1: (10.5000, 56.15), 2: (10.5200, 56.15),
                }
                route_name = (
                    "Sliver Route" if case == "clip-sliver-drop"
                    else "Outside Route")
                ways = {10: {
                    "nodes": [1, 2],
                    "tags": {"highway": "path", "name": route_name},
                }}
                polygons = [box(10.499, 56.19, 10.51, 56.21)]
                if case == "clip-sliver-drop":
                    polygons.insert(0, box(10.499, 56.1, 10.5007, 56.17))
                boundary_geometry = MultiPolygon(polygons)
            elif case.startswith("outside-curation:"):
                category = case.split(":", 1)[1]
                names = {
                    "access": "Private Path",
                    "closed": "CLOSED - Old Trail",
                    "grid-address": "North 3325 West",
                    "min-length": "Tiny Outside Trail",
                    "motorized": "Basalt Jeep Trail",
                    "named-road": "Maxwell Ranch Road",
                    "non-trail-feature": "Tusyan Sidewalk",
                    "nonhiking-route": "Emmons Glacier Route",
                    "off-trail": "Concourse A (Main Access Road)",
                    "road-code": "FR 231",
                    "short-name": "XY",
                    "thru-hike": "Appalachian Trail",
                    "utility": "Powerline",
                }
                end_x = 10.501 if category == "min-length" else 10.520
                nodes = {1: (10.500, 56.15), 2: (end_x, 56.15)}
                ways = {10: {
                    "nodes": [1, 2],
                    "tags": {"highway": "path", "name": names[category]},
                }}
                boundary_geometry = MultiPolygon([
                    box(10.49, 56.19, 10.53, 56.21),
                ])
            elif case.startswith("model-curation-target:"):
                category = case.split(":", 1)[1]
                if category == "min-length":
                    nodes = {
                        1: (10.500000, 56.15),
                        2: (10.500004, 56.15),
                        3: (10.502580, 56.15),
                        4: (10.502584, 56.15),
                    }
                    ways = {
                        10: {"nodes": [1, 2, 1, 3],
                             "tags": {"highway": "path"}},
                        20: {"nodes": [1, 2, 3, 4],
                             "tags": {"highway": "path"}},
                    }
                else:
                    nodes = {
                        1: (10.500, 56.15), 2: (10.550, 56.15),
                    }
                    ways = {10: {
                        "nodes": [1, 2], "tags": {"highway": "path"},
                    }}
                boundary_geometry = box(10.4, 56.1, 10.9, 56.35)
            elif case == "partial-preclip-evidence":
                nodes = {
                    1: (10.500, 56.15), 2: (10.501, 56.15),
                    3: (10.601, 56.15),
                }
                ways = {
                    10: {"nodes": [1, 2], "tags": {
                        "highway": "path", "name": "Long Preclip Trail"}},
                    11: {"nodes": [2, 3], "tags": {"highway": "path"}},
                }
                boundary_geometry = MultiPolygon([
                    box(10.499, 56.14, 10.501, 56.16),
                    box(10.499, 56.19, 10.510, 56.21),
                ])
            elif case == "duplicate-relations-outside":
                nodes = {
                    1: (10.500, 56.15), 2: (10.550, 56.15),
                }
                ways = {10: {
                    "nodes": [1, 2], "tags": {
                        "highway": "path", "name": "Outside Hiking Path",
                    },
                }}
                boundary_geometry = MultiPolygon([
                    box(10.49, 56.19, 10.56, 56.21),
                ])
            elif case == "three-relation-chain":
                nodes = {
                    1: (10.500, 56.15), 2: (10.550, 56.15),
                }
                ways = {10: {
                    "nodes": [1, 2], "tags": {
                        "highway": "path",
                        "name": "Descriptive Ridge Trail",
                    },
                }}
                boundary_geometry = box(10.4, 56.1, 10.9, 56.35)
            elif case == "survivor-quality-removed":
                nodes = {
                    1: (10.500, 56.15), 2: (10.550, 56.15),
                    3: (10.500, 56.15), 4: (10.550, 56.15),
                }
                ways = {
                    10: {"nodes": [1, 2], "tags": {"highway": "path"}},
                    20: {"nodes": [3, 4], "tags": {
                        "highway": "track",
                        "name": "Descriptive Mountain Trail",
                    }},
                }
                boundary_geometry = box(10.4, 56.1, 10.9, 56.35)
            elif case in {
                    "promotion-clipped-out", "promotion-clipped-index-shift",
                    "promotion-clipped-reselect"}:
                nodes = {
                    1: (10.400, 56.15), 2: (10.480, 56.15),
                    3: (10.550, 56.15),
                }
                ways = {10: {
                    "nodes": [1, 2, 3],
                    "tags": {"highway": "path",
                             "name": "Boundary Access Route"},
                }}
                clipped_poi = {
                    "id": 800, "name": "Clipped Summit",
                    "coord": nodes[1],
                    "tags": {"name": "Clipped Summit", "natural": "peak"},
                }
                surviving_poi = {
                    "id": 900, "name": "Surviving View",
                    "coord": nodes[3],
                    "tags": {"name": "Surviving View",
                             "tourism": "viewpoint"},
                }
                if case == "promotion-clipped-out":
                    pois = [clipped_poi]
                elif case == "promotion-clipped-index-shift":
                    pois = [surviving_poi]
                else:
                    pois = [clipped_poi, surviving_poi]
                boundary_geometry = box(10.45, 56.1, 10.9, 56.35)
            elif case in {
                    "promotion-no-authoritative-endpoints",
                    "promotion-no-poi"}:
                if case == "promotion-no-authoritative-endpoints":
                    nodes = {
                        1: (10.400, 56.15), 2: (10.600, 56.15),
                        3: (10.900, 56.15),
                    }
                    boundary_geometry = box(10.45, 56.1, 10.85, 56.35)
                    pois = [{
                        "id": 900, "name": "Interior View",
                        "coord": nodes[2],
                        "tags": {"name": "Interior View",
                                 "tourism": "viewpoint"},
                    }]
                else:
                    nodes = {
                        1: (10.500, 56.15), 2: (10.550, 56.15),
                    }
                    boundary_geometry = box(10.4, 56.1, 10.9, 56.35)
                    pois = []
                ways = {10: {
                    "nodes": list(nodes),
                    "tags": {"highway": "path",
                             "name": "No Destination Access Route"},
                }}
            elif case in {
                    "promotion-fused-consumed", "promotion-fused-reselect",
                    "promotion-ambiguous-fallback"}:
                nodes = {
                    1: (10.500, 56.15), 2: (10.530, 56.15),
                    3: (10.560, 56.15),
                }
                route_name = (
                    "Ambiguous Access Route"
                    if case == "promotion-ambiguous-fallback"
                    else "Fused Access Route")
                ways = {
                    10: {"nodes": [1, 2], "tags": {
                        "highway": "path", "name": route_name}},
                    20: {"nodes": ([1, 2, 3]
                                   if case == "promotion-ambiguous-fallback"
                                   else [2, 3]),
                         "tags": {"highway": "path", "name": route_name}},
                }
                pois = [{
                    "id": 800, "name": "Consumed Summit",
                    "coord": nodes[1 if case == "promotion-ambiguous-fallback"
                                   else 2],
                    "tags": {"name": "Consumed Summit", "natural": "peak"},
                }]
                if case in {
                        "promotion-fused-reselect",
                        "promotion-ambiguous-fallback"}:
                    pois.append({
                        "id": 900, "name": "Surviving View",
                        "coord": nodes[3],
                        "tags": {"name": "Surviving View",
                                 "tourism": "viewpoint"},
                    })
                boundary_geometry = box(10.4, 56.1, 10.9, 56.35)
            elif case == "promotion-duplicate-destination":
                nodes = {
                    1: (10.500, 56.15), 2: (10.560, 56.15),
                    3: (10.530, 56.15),
                }
                ways = {
                    10: {"nodes": [1, 3], "tags": {
                        "highway": "path", "name": "North Access Route"}},
                    20: {"nodes": [2, 3], "tags": {
                        "highway": "path", "name": "South Access Route"}},
                }
                pois = [{
                    "id": 900, "name": "Shared Summit", "coord": nodes[3],
                    "tags": {"name": "Shared Summit", "natural": "peak"},
                }]
                boundary_geometry = box(10.4, 56.1, 10.9, 56.35)
            elif case.startswith("multi-root-policy:"):
                mode = case.split(":", 1)[1]
                nodes = {
                    1: (10.500, 56.15), 2: (10.530, 56.15),
                    3: (10.550, 56.15),
                }
                names = {
                    "identity-mols-forward": ("Mols Rute", "Mols rute"),
                    "identity-mols-reverse": ("Mols Rute", "Mols rute"),
                    "identity-kalo-forward": ("Kalø", "Kalø Trail"),
                    "identity-kalo-reverse": ("Kalø", "Kalø Trail"),
                    "identity-fallback": ("Første Rute", "første rute"),
                    "local-rwn": ("Shared Access Route",
                                  "Shared access route"),
                    "rwn-local": ("Shared Access Route",
                                  "Shared access route"),
                    "route-nonroute": ("Mols--Rute", "Mols Rute"),
                    "mixed-blockers": ("Mols Rute", "Mols rute"),
                    "all-local": ("Shared Access Route",
                                  "Shared access route"),
                }
                first_name, second_name = names[mode]
                first_way_tags = {"highway": "path", "name": first_name}
                if mode == "identity-fallback":
                    first_way_tags = {
                        "highway": "path", "name:da": first_name,
                    }
                ways = {
                    10: {"nodes": [1, 2], "tags": first_way_tags},
                    20: {"nodes": [2, 3], "tags": {
                        "highway": "path", "name": second_name}},
                }
                if mode == "all-local":
                    pois = [{
                        "id": 900, "name": "Policy Summit",
                        "coord": nodes[3],
                        "tags": {"name": "Policy Summit",
                                 "natural": "peak"},
                    }]
                boundary_geometry = box(10.4, 56.1, 10.9, 56.35)
            elif case in {
                    "promotion-weld-only", "promotion-weld-source",
                    "multi-root-promotion", "multi-root-promotion-ambiguous"}:
                if case == "promotion-weld-only":
                    nodes = {
                        1: (10.500, 56.15), 2: (10.530, 56.15),
                        3: (10.540, 56.15),
                    }
                    ways = {
                        10: {"nodes": [1, 2], "tags": {
                            "highway": "path", "name": "Weld Access Route"}},
                        11: {"nodes": [2, 3],
                             "tags": {"highway": "path"}},
                    }
                    pois = [{
                        "id": 900, "name": "Weld Summit",
                        "coord": nodes[3],
                        "tags": {"name": "Weld Summit", "natural": "peak"},
                    }]
                elif case == "promotion-weld-source":
                    nodes = {
                        1: (10.500, 56.15), 2: (10.530, 56.15),
                        3: (10.490, 56.15),
                    }
                    ways = {
                        10: {"nodes": [1, 2], "tags": {
                            "highway": "path", "name": "Source Access Route"}},
                        11: {"nodes": [1, 3],
                             "tags": {"highway": "path"}},
                    }
                    pois = [{
                        "id": 800, "name": "Weld Shelter",
                        "coord": nodes[3],
                        "tags": {"name": "Weld Shelter",
                                 "amenity": "shelter"},
                    }, {
                        "id": 900, "name": "Source Summit",
                        "coord": nodes[2],
                        "tags": {"name": "Source Summit", "natural": "peak"},
                    }]
                else:
                    nodes = {
                        1: (10.500, 56.15), 2: (10.530, 56.15),
                        3: (10.550, 56.15),
                    }
                    ways = {
                        10: {"nodes": [1, 2 if case == "multi-root-promotion"
                                       else 3],
                             "tags": {"highway": "path",
                                      "name": "Shared Multi Root Access Route"}},
                        20: {"nodes": ([2, 3]
                                       if case == "multi-root-promotion"
                                       else [1, 3]),
                             "tags": {"highway": "path",
                                      "name": "Shared Multi Root Access Route"}},
                    }
                    pois = [{
                        "id": 900, "name": "Multi Root Summit",
                        "coord": nodes[3],
                        "tags": {"name": "Multi Root Summit",
                                 "natural": "peak"},
                    }]
                boundary_geometry = box(10.4, 56.1, 10.9, 56.35)
            elif case in {
                    "promoted-hike", "promoted-hike-outside",
                    "promoted-target-quality-removed", "winner-coalesced"}:
                if case in {"promoted-hike", "promoted-hike-outside"}:
                    nodes = {
                        1: (10.500, 56.15), 2: (10.530, 56.15),
                        3: (10.550, 56.15),
                    }
                    ways = {
                        10: {"nodes": [1, 2, 3], "tags": {
                            "highway": "path",
                            "name": "Mols Summit Access Route",
                        }},
                        11: {"nodes": [2, 3], "tags": {
                            "highway": "path", "name": "Mols Summit Trail",
                        }},
                    }
                    pois = [{
                        "id": 900, "name": "Mols Summit",
                        "coord": nodes[3], "tags": {"name": "Mols Summit", "natural": "peak"},
                    }]
                elif case == "promoted-target-quality-removed":
                    nodes = {
                        1: (10.530, 56.15), 2: (10.550, 56.15),
                        3: (10.500, 56.15), 4: (10.530, 56.15),
                        5: (10.550, 56.15),
                    }
                    ways = {
                        10: {"nodes": [1, 2],
                             "tags": {"highway": "path"}},
                        20: {"nodes": [3, 4, 5], "tags": {
                            "highway": "track",
                            "name": "Mols Summit Trail--Access",
                        }},
                    }
                    pois = [{
                        "id": 900, "name": "Mols Summit",
                        "coord": nodes[5], "tags": {"name": "Mols Summit", "natural": "peak"},
                    }]
                else:
                    nodes = {
                        1: (10.500, 56.15), 2: (10.520, 56.15),
                        3: (10.500, 56.15), 4: (10.520, 56.15),
                        5: (10.450, 56.15),
                    }
                    ways = {
                        10: {"nodes": [1, 2], "tags": {
                            "highway": "path", "name": "Mols Summit Trail",
                        }},
                        20: {"nodes": [3, 4], "tags": {
                            "highway": "path",
                            "name": "Mols Summit Access Route",
                        }},
                        30: {"nodes": [5, 3], "tags": {
                            "highway": "path",
                            "name": "Mols Summit Access Route",
                        }},
                    }
                    pois = [{
                        "id": 900, "name": "Mols Summit",
                        "coord": nodes[4], "tags": {"name": "Mols Summit", "natural": "peak"},
                    }]
                    per_area_merge = True
                    model_areas = [{
                        "name": validator.AREA_NAME,
                        "bbox": (10.4, 56.1, 10.9, 56.35),
                        "rings": [[
                            (10.4, 56.1), (10.9, 56.1),
                            (10.9, 56.35), (10.4, 56.35),
                            (10.4, 56.1),
                        ]],
                    }]
                boundary_geometry = (
                    MultiPolygon([box(10.49, 56.19, 10.58, 56.21)])
                    if case == "promoted-hike-outside"
                    else box(10.4, 56.1, 10.9, 56.35))
            elif case in {"duplicate-relations", "duplicate-named-member",
                           "multi-root-access-one", "multi-root-access-both"}:
                nodes = {
                    1: (10.500, 56.15), 2: (10.550, 56.15),
                }
                way_tags = {"highway": "path"}
                if case == "duplicate-relations":
                    way_tags["name"] = "Primary Hiking Footpath"
                elif case == "duplicate-named-member":
                    way_tags["name"] = "Independent Member Trail"
                elif case.startswith("multi-root-access"):
                    way_tags["name"] = "Shared Private Trail"
                ways = {10: {"nodes": [1, 2], "tags": way_tags}}
                boundary_geometry = box(10.4, 56.1, 10.9, 56.35)

            elif case in {"ref-only", "localized-relation", "unnamed",
                           "localized-order-de-da", "localized-order-da-de"}:
                nodes = {
                    1: (10.5, 56.15), 2: (10.55, 56.15),
                }
                way_tags = {"highway": "path"}
                if case == "ref-only":
                    way_tags["name"] = "Member Named Route"
                elif case == "localized-relation":
                    way_tags["name"] = "Lokal Rute"
                elif case.startswith("localized-order-"):
                    way_tags["name"] = "Dansk Sti"
                ways = {10: {"nodes": [1, 2], "tags": way_tags}}
                boundary_geometry = box(10.4, 56.1, 10.9, 56.35)
            elif case == "superroute-filtered":
                nodes = {
                    1: (10.50, 56.15), 2: (10.55, 56.15),
                    3: (10.60, 56.15),
                }
                ways = {
                    10: {"nodes": [1, 2], "tags": {"highway": "motorway"}},
                    11: {"nodes": [2, 3], "tags": {"highway": "motorway"}},
                }
                boundary_geometry = box(10.4, 56.1, 10.9, 56.35)
            elif case.startswith("fused-"):
                nodes = {
                    1: (10.500, 56.15), 2: (10.505, 56.15),
                    3: (10.510, 56.15), 4: (10.515, 56.15),
                    5: (10.500, 56.16), 6: (10.505, 56.16),
                    7: (10.510, 56.16), 8: (10.515, 56.16),
                }
                ways = {
                    10: {"nodes": [1, 2], "tags": {
                        "highway": "path", "name": "Fused Route"}},
                    11: {"nodes": [2, 3], "tags": {
                        "highway": "service", "service": "parking_aisle"}},
                    12: {"nodes": [3, 4], "tags": {"highway": "path"}},
                    20: {"nodes": [5, 6], "tags": {"highway": "path"}},
                    22: {"nodes": [7, 8], "tags": {"highway": "path"}},
                }
                if case == "fused-both-proofs":
                    ways[21] = {"nodes": [6, 7], "tags": {
                        "highway": "service", "service": "parking_aisle"}}
                boundary_geometry = MultiPolygon([
                    box(10.499, 56.14, 10.505, 56.17),
                    box(10.510, 56.14, 10.516, 56.17),
                    box(10.499, 56.19, 10.504, 56.21),
                ])
            elif case.startswith("pedestrian:"):
                mode = case.split(":", 1)[1]
                nodes = {
                    1: (10.50, 56.15), 2: (10.55, 56.15),
                    3: (10.56, 56.15), 4: (10.56, 56.16),
                    5: (10.55, 56.16),
                }
                if mode == "claimed-outside":
                    nodes.update({
                        3: (11.00, 56.15), 4: (11.01, 56.15),
                        5: (11.01, 56.16),
                    })
                area_tags = {"highway": "pedestrian"}
                if mode == "area-no":
                    area_tags["area"] = "no"
                elif mode != "implicit-unclaimed":
                    area_tags["area"] = "yes"
                if mode != "unnamed":
                    area_tags["name"] = "Signed Plaza"
                ways = {
                    10: {"nodes": [1, 2],
                         "tags": {"highway": "path",
                                  "name": "Signed Route"}},
                    90: {"nodes": [2, 3, 4, 5, 2], "tags": area_tags},
                }
                boundary_geometry = box(10.4, 56.1, 10.9, 56.35)
            elif case == "diluted-restoration":
                nodes = {
                    1: (10.45, 56.15), 2: (10.459, 56.15),
                    3: (10.461, 56.15), 4: (10.70, 56.15),
                }
                ways = {
                    10: {"nodes": [1, 2], "tags": {"highway": "path"}},
                    11: {"nodes": [2, 3],
                         "tags": {"highway": "residential"}},
                    12: {"nodes": [3, 4],
                         "tags": {"highway": "path", "name": "Signed Route"}},
                }
                boundary_geometry = box(10.4, 56.1, 10.9, 56.35)
            elif case.startswith("service:"):
                service = case.split(":", 1)[1]
                nodes = {
                    1: (10.50, 56.15), 2: (10.55, 56.15),
                    3: (10.551, 56.15), 4: (10.60, 56.15),
                }
                ways = {
                    10: {"nodes": [1, 2], "tags": {"highway": "path"}},
                    11: {"nodes": [2, 3], "tags": {
                        "highway": " SERVICE ", "service": service,
                        "foot": " YES ",
                    }},
                    12: {"nodes": [3, 4],
                         "tags": {"highway": "path", "name": "Signed Route"}},
                }
                boundary_geometry = box(10.4, 56.1, 10.9, 56.35)
            elif case.startswith("track-lanes:"):
                lanes = case.split(":", 1)[1]
                nodes = {
                    1: (10.50, 56.15), 2: (10.55, 56.15),
                    3: (10.551, 56.15), 4: (10.60, 56.15),
                }
                ways = {
                    10: {"nodes": [1, 2], "tags": {"highway": "path"}},
                    11: {"nodes": [2, 3], "tags": {
                        "highway": "track", "lanes": lanes,
                        "motor_vehicle": "yes", "foot": "yes",
                    }},
                    12: {"nodes": [3, 4],
                         "tags": {"highway": "path", "name": "Signed Route"}},
                }
                boundary_geometry = box(10.4, 56.1, 10.9, 56.35)
            elif case == "superroute-private":
                nodes = {
                    1: (10.50, 56.15), 2: (10.55, 56.15),
                    3: (10.60, 56.15),
                }
                ways = {
                    10: {"nodes": [1, 2], "tags": {
                        "highway": "path", "name": "Signed Route",
                    }},
                    11: {"nodes": [2, 3], "tags": {
                        "highway": "path", "access": "private",
                    }},
                }
                boundary_geometry = box(10.4, 56.1, 10.9, 56.35)
            else:
                nodes = {
                    1: (10.5, 56.15), 2: (10.55, 56.15),
                    3: (10.551, 56.15),
                }
                ways = {
                    10: {"nodes": [1, 2],
                         "tags": {"highway": "path", "name": "Signed Route"}},
                    11: {"nodes": [2, 3],
                         "tags": {"highway": "residential",
                                  "name": "Synthetic Road"}},
                }
                boundary_geometry = box(10.4, 56.1, 10.9, 56.35)
            if case in {"superroute-private", "superroute-filtered"}:
                if case == "superroute-private":
                    parent_members = [("r", 701, "")]
                    child_members = [("w", 10, ""), ("w", 11, "")]
                else:
                    parent_members = [("w", 10, ""), ("r", 701, "")]
                    child_members = [("w", 11, "")]
                relations = {
                    700: {
                        "tags": {"type": "route", "route": "hiking",
                                 "name": "Signed Route"},
                        "members": parent_members,
                    },
                    701: {
                        "tags": {"type": "route", "route": "hiking",
                                 "name": "Signed Route"},
                        "members": child_members,
                    },
                }
            elif case == "duplicate-relations-outside":
                relations = {
                    700: {
                        "tags": {"type": "route", "route": "hiking",
                                 "name": "Outside Hiking Path"},
                        "members": [("w", 10, "")],
                    },
                    701: {
                        "tags": {"type": "route", "route": "foot",
                                 "name": "Outside Foot Path"},
                        "members": [("w", 10, "")],
                    },
                }
            elif case == "three-relation-chain":
                relations = {
                    700: {
                        "tags": {"type": "route", "route": "hiking",
                                 "name": "Path"},
                        "members": [("w", 10, "")],
                    },
                    701: {
                        "tags": {"type": "route", "route": "foot",
                                 "name": "Ridge Path"},
                        "members": [("w", 10, "")],
                    },
                    702: {
                        "tags": {"type": "route", "route": "walking",
                                 "name": "Descriptive Ridge Trail"},
                        "members": [("w", 10, "")],
                    },
                }
            elif case == "survivor-quality-removed":
                relations = {700: {
                    "tags": {"type": "route", "route": "hiking",
                             "name": "Foot Path"},
                    "members": [("w", 10, "")],
                }}
            elif case in {
                    "promotion-clipped-out", "promotion-clipped-index-shift",
                    "promotion-clipped-reselect"}:
                relations = {700: {
                    "tags": {"type": "route", "route": "hiking",
                             "name": "Boundary Access Route"},
                    "members": [("w", 10, "")],
                }}
            elif case in {
                    "promotion-no-authoritative-endpoints",
                    "promotion-no-poi"}:
                relations = {700: {
                    "tags": {"type": "route", "route": "hiking",
                             "name": "No Destination Access Route"},
                    "members": [("w", 10, "")],
                }}
            elif case in {
                    "promotion-fused-consumed", "promotion-fused-reselect",
                    "promotion-ambiguous-fallback"}:
                route_name = (
                    "Ambiguous Access Route"
                    if case == "promotion-ambiguous-fallback"
                    else "Fused Access Route")
                relations = {
                    700: {
                        "tags": {"type": "route", "route": "hiking",
                                 "name": route_name},
                        "members": [("w", 10, "")],
                    },
                    701: {
                        "tags": {"type": "route", "route": "foot",
                                 "name": route_name},
                        "members": [("w", 20, "")],
                    },
                }
            elif case == "promotion-duplicate-destination":
                relations = {
                    700: {
                        "tags": {"type": "route", "route": "hiking",
                                 "name": "North Access Route"},
                        "members": [("w", 10, "")],
                    },
                    701: {
                        "tags": {"type": "route", "route": "foot",
                                 "name": "South Access Route"},
                        "members": [("w", 20, "")],
                    },
                }
            elif case.startswith("multi-root-policy:"):
                mode = case.split(":", 1)[1]
                names = {
                    "identity-mols-forward": ("Mols Rute", "Mols rute"),
                    "identity-mols-reverse": ("Mols Rute", "Mols rute"),
                    "identity-kalo-forward": ("Kalø", "Kalø Trail"),
                    "identity-kalo-reverse": ("Kalø", "Kalø Trail"),
                    "identity-fallback": ("Første Rute", "første rute"),
                    "local-rwn": ("Shared Access Route",
                                  "Shared access route"),
                    "rwn-local": ("Shared Access Route",
                                  "Shared access route"),
                    "route-nonroute": ("Mols--Rute", "Mols Rute"),
                    "mixed-blockers": ("Mols Rute", "Mols rute"),
                    "all-local": ("Shared Access Route",
                                  "Shared access route"),
                }
                first_name, second_name = names[mode]
                first_tags = {
                    "type": "route", "route": "hiking",
                    "name": first_name, "network": "lwn",
                    "operator": "Primary Operator", "sac_scale": "hiking",
                    "trail_visibility": "excellent",
                }
                second_tags = {
                    "type": "route", "route": "foot",
                    "name": second_name, "network": "lwn",
                    "operator": "Secondary Operator",
                    "sac_scale": "mountain_hiking",
                    "trail_visibility": "good",
                }
                if mode == "identity-fallback":
                    first_tags.pop("name")
                if mode in {"local-rwn", "mixed-blockers"}:
                    second_tags["network"] = "rwn"
                elif mode == "rwn-local":
                    first_tags["network"] = "rwn"
                relation_items = [
                    (700, {"tags": first_tags,
                           "members": [("w", 10, "")]}),
                    (701, {"tags": second_tags,
                           "members": [("w", 20, "")]}),
                ]
                if mode.endswith("reverse") or mode == "rwn-local":
                    relation_items.reverse()
                relations = dict(relation_items)
            elif case in {
                    "promotion-weld-only", "promotion-weld-source"}:
                route_name = (
                    "Weld Access Route" if case == "promotion-weld-only"
                    else "Source Access Route")
                relations = {700: {
                    "tags": {"type": "route", "route": "hiking",
                             "name": route_name},
                    "members": [("w", 10, "")],
                }}
            elif case in {
                    "multi-root-promotion", "multi-root-promotion-ambiguous"}:
                relations = {
                    700: {
                        "tags": {"type": "route", "route": "hiking",
                                 "name": "Shared Multi Root Access Route"},
                        "members": [("w", 10, "")],
                    },
                    701: {
                        "tags": {"type": "route", "route": "foot",
                                 "name": "Shared Multi Root Access Route"},
                        "members": [("w", 20, "")],
                    },
                }
            elif case in {"promoted-hike", "promoted-hike-outside"}:
                relations = {
                    700: {
                        "tags": {"type": "route", "route": "hiking",
                                 "name": "Mols Summit Access Route"},
                        "members": [("w", 10, "")],
                    },
                    701: {
                        "tags": {"type": "route", "route": "foot",
                                 "name": "Mols Summit Trail"},
                        "members": [("w", 11, "")],
                    },
                }
            elif case == "promoted-target-quality-removed":
                relations = {700: {
                    "tags": {"type": "route", "route": "hiking",
                             "name": "Mols Summit Trail"},
                    "members": [("w", 10, "")],
                }}
            elif case == "winner-coalesced":
                relations = {
                    700: {
                        "tags": {"type": "route", "route": "hiking",
                                 "name": "Mols Summit Trail"},
                        "members": [("w", 10, "")],
                    },
                    701: {
                        "tags": {"type": "route", "route": "foot",
                                 "name": "Mols Summit Access Route"},
                        "members": [("w", 20, "")],
                    },
                    702: {
                        "tags": {"type": "route", "route": "walking",
                                 "name": "Mols Summit Access Route"},
                        "members": [("w", 30, "")],
                    },
                }
            elif case == "duplicate-relations":
                relations = {
                    700: {
                        "tags": {"type": "route", "route": "hiking",
                                 "name": "Primary Hiking Footpath"},
                        "members": [("w", 10, "")],
                    },
                    701: {
                        "tags": {"type": "route", "route": "foot",
                                 "name": "Foot Path"},
                        "members": [("w", 10, "")],
                    },
                }
            elif case == "duplicate-named-member":
                relations = {700: {
                    "tags": {"type": "route", "route": "hiking",
                             "name": "Signed Path"},
                    "members": [("w", 10, "")],
                }}
            elif case in {"multi-root-access-one", "multi-root-access-both"}:
                first_tags = {
                    "type": "route", "route": "hiking",
                    "name": "Shared Private Trail", "access": "private",
                }
                second_tags = {
                    "type": "route", "route": "foot",
                    "name": "Shared Private Trail",
                }
                if case == "multi-root-access-both":
                    second_tags["access"] = "private"
                relations = {
                    700: {"tags": first_tags, "members": [("w", 10, "")]},
                    701: {"tags": second_tags, "members": [("w", 10, "")]},
                }
            elif case.startswith("model-curation-target:"):
                category = case.split(":", 1)[1]
                target_names = {
                    "closed": "CLOSED - Descriptive Ridge Trail",
                    "access": "Descriptive Private Ridge Trail",
                    "min-length": "Descriptive Tiny Ridge Trail",
                    "thru-hike": "Appalachian Trail",
                    "named-road": "Descriptive Mountain Road",
                }
                target_tags = {
                    "type": "route", "route": "foot",
                    "name": target_names[category],
                }
                if category == "access":
                    target_tags["access"] = "private"
                relations = {
                    700: {
                        "tags": {"type": "route", "route": "hiking",
                                 "name": "Foot Path"},
                        "members": [("w", 10, "")],
                    },
                    701: {
                        "tags": target_tags,
                        "members": [("w", 20 if category == "min-length"
                                     else 10, "")],
                    },
                }
            elif case.startswith("fused-"):
                relations = {
                    700: {
                        "tags": {"type": "route", "route": "hiking",
                                 "name": "Fused Route"},
                        "members": [("w", 10, ""), ("w", 11, ""),
                                    ("w", 12, "")],
                    },
                    701: {
                        "tags": {"type": "route", "route": "hiking",
                                 "name": "Fused Route"},
                        "members": ([("w", 20, ""), ("w", 21, ""),
                                     ("w", 22, "")]
                                    if case == "fused-both-proofs" else
                                    [("w", 20, ""), ("w", 22, "")]),
                    },
                }
            elif case.startswith("pedestrian:"):
                mode = case.split(":", 1)[1]
                if mode == "claimed-connection":
                    relation_members = [("w", 10, ""),
                                        ("w", 90, "connection")]
                elif mode in {"relation-claimed", "claimed-outside"}:
                    relation_members = [("w", 10, ""), ("w", 90, "")]
                else:
                    relation_members = [("w", 10, "")]
                relations = {700: {
                    "tags": {"type": "route", "route": "hiking",
                             "name": "Signed Route"},
                    "members": relation_members,
                }}
            else:
                route_tags = {"type": "route", "route": "hiking",
                              "name": "Signed Route"}
                if case == "ref-only":
                    route_tags = {"type": "route", "route": "hiking",
                                  "ref": "M1"}
                elif case == "localized-relation":
                    route_tags = {"type": "route", "route": "hiking",
                                  "name:da": "Lokal Rute"}
                elif case == "localized-order-de-da":
                    route_tags = {
                        "type": "route", "route": "hiking",
                        "name:de": "Deutscher Weg", "name:da": "Dansk Sti",
                    }
                elif case == "localized-order-da-de":
                    route_tags = {
                        "type": "route", "route": "hiking",
                        "name:da": "Dansk Sti", "name:de": "Deutscher Weg",
                    }
                elif case == "unnamed":
                    route_tags = {"type": "route", "route": "hiking",
                                  "ref": "U1"}
                elif case == "min-length-drop":
                    route_tags["name"] = "Tiny Route"
                elif case in {"clip-sliver-drop", "outside-drop"}:
                    route_tags["name"] = (
                        "Sliver Route" if case == "clip-sliver-drop"
                        else "Outside Route")
                elif case.startswith("outside-curation:"):
                    category = case.split(":", 1)[1]
                    route_tags["name"] = names[category]
                    if category == "access":
                        route_tags["access"] = "private"
                elif case == "partial-preclip-evidence":
                    route_tags["name"] = "Long Preclip Trail"
                relations = {700: {
                    "tags": route_tags,
                    "members": [
                        ("w", way_id, "") for way_id in ways
                        if case != "diluted-restoration" or way_id != 12
                    ],
                }}
            boundary = {
                "name": validator.AREA_NAME,
                "tags": {"name": validator.AREA_NAME, "boundary": "national_park"},
                "geom": boundary_geometry,
                "osm_type": "relation",
                "osm_id": RELATION_ID,
            }
            output = root / "data/aoi/mols-bjerge.trails.geojson"
            assembly_report = root / "reports/assembly.json"
            input_pbf = root / "synthetic.osm.pbf"
            input_pbf.write_bytes(b"synthetic-runner-local-aoi-pbf")
            input_identity = {
                "sha256": hashlib.sha256(input_pbf.read_bytes()).hexdigest(),
                "bytes": len(input_pbf.read_bytes()),
            }
            graph_records = [
                _source_record(
                    way_id, way["nodes"],
                    [nodes[node] for node in way["nodes"]], way["tags"])
                for way_id, way in ways.items()
            ]
            graph_records.append(copy.deepcopy(
                qa.golden_trail["properties"]["source_ways"][0]))
            graph_direct = {
                relation_id: list(dict.fromkeys(
                    ref for member_type, ref, _role in relation["members"]
                    if member_type == "w"))
                for relation_id, relation in relations.items()
            }
            relation_tags = {
                relation_id: relation["tags"]
                for relation_id, relation in relations.items()
            }
            graph_direct[8350111] = [835]
            relation_tags[8350111] = {
                "name": "Mols Bjerge-stien Bjergetapen",
                "route": "hiking", "type": "route",
            }
            slice_identities = {}
            for scope, relative in validator.PBF_RELATIVES_BY_SCOPE.items():
                payload = (root / relative).read_bytes()
                slice_identities[scope] = {
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "bytes": len(payload),
                }
            graphs = {
                "raw_relation_members": _relation_graph(
                    graph_direct, graph_records, relation_tags=relation_tags,
                    scope="raw-denmark",
                    source_sha256=slice_identities["raw-denmark"]["sha256"],
                    source_bytes=slice_identities["raw-denmark"]["bytes"]),
                "prefilter_relation_members": _relation_graph(
                    graph_direct, graph_records, relation_tags=relation_tags,
                    scope="prefiltered-denmark",
                    source_sha256=slice_identities[
                        "prefiltered-denmark"]["sha256"],
                    source_bytes=slice_identities[
                        "prefiltered-denmark"]["bytes"]),
                "aoi_relation_members": _relation_graph(
                    graph_direct, graph_records, relation_tags=relation_tags,
                    scope="aoi", source_sha256=input_identity["sha256"],
                    source_bytes=input_identity["bytes"]),
            }
            for graph in graphs.values():
                by_id = {row["relation_id"]: row
                         for row in graph["relations"]}
                for relation_id, relation in relations.items():
                    if relation_id not in by_id:
                        continue
                    by_id[relation_id]["members"] = [
                        {
                            "sequence": sequence,
                            "type": {
                                "n": "node", "w": "way", "r": "relation",
                            }[member_type],
                            "ref": ref,
                            "role": role,
                        }
                        for sequence, (member_type, ref, role)
                        in enumerate(relation["members"])
                    ]
            if case in {"superroute-private", "superroute-filtered"}:
                for graph in graphs.values():
                    graph["root_relation_ids"] = [700, 701, 8350111]
            for label, graph in graphs.items():
                _write(root / validator.REQUIRED_JSON_FILES[label], graph)
            producer_args = [
                "--in", str(input_pbf),
                "--out", str(output),
                "--only-area", validator.AREA_NAME,
                "--require-exact-area",
                "--expected-area-relation-id", str(RELATION_ID),
                "--min-length-mi", str(validator.PILOT_MIN_LENGTH_MI),
                "--min-inside-mi", str(validator.PILOT_MIN_INSIDE_MI),
                "--report-json", str(assembly_report),
                "--region", "dk",
                "--relation-authority", str(
                    root / validator.RAW_RELATION_MEMBERS_RELATIVE),
                "--prefilter-authority", str(
                    root / validator.PREFILTER_RELATION_MEMBERS_RELATIVE),
                "--aoi-relation-members", str(
                    root / validator.AOI_RELATION_MEMBERS_RELATIVE),
            ]
            if per_area_merge:
                producer_args.append("--per-area-merge")
            with (
                patch.object(producer, "read_pbf",
                             return_value=(nodes, ways, relations, pois)),
                patch.object(area_module, "assemble_areas", return_value=[boundary]),
                patch.object(area_module, "merge_areas", return_value=model_areas),
            ):
                producer_rc = producer.main(producer_args)
            expected_producer_rc = (
                2 if (case == "survivor-quality-removed"
                      or case.startswith("model-curation-target:")) else 0)
            self.assertEqual(producer_rc, expected_producer_rc)
            trails = json.loads(output.read_text())
            removed = json.loads((root / "data/aoi/mols-bjerge.removed.geojson").read_text())
            report = json.loads(assembly_report.read_text())
            qa.documents["areas"] = json.loads(
                (root / "data/aoi/mols-bjerge.areas.geojson").read_text())
            qa.documents["exact_area"] = json.loads(
                (root / "data/aoi/mols-bjerge.exact-area.geojson").read_text())
            malt_raw = next(
                feature for feature in qa.documents["raw"]["features"]
                if feature.get("id") == "w1027606528")
            malt_removed = next(
                feature for feature in qa.documents["removed"]["features"]
                if (feature.get("properties") or {}).get("name") == "Maltgården")
            qa.documents["trails"] = trails
            qa.documents["removed"] = {
                "type": "FeatureCollection",
                "features": [*removed["features"], malt_removed],
            }
            qa.documents["assembly_report"] = report
            qa.documents["raw"] = {
                "type": "FeatureCollection",
                "features": [{
                    "type": "Feature",
                    "id": f"w{way_id}",
                    "properties": way["tags"],
                    "geometry": {
                        "type": (
                            "Polygon" if way["tags"].get("highway") == "pedestrian"
                            and not case.endswith("raw-line-unclaimed")
                            and way["tags"].get("area") != "no"
                            and way["nodes"][0] == way["nodes"][-1]
                            else "LineString"),
                        "coordinates": (
                            [[nodes[node] for node in way["nodes"]]]
                            if way["tags"].get("highway") == "pedestrian"
                            and not case.endswith("raw-line-unclaimed")
                            and way["tags"].get("area") != "no"
                            and way["nodes"][0] == way["nodes"][-1]
                            else [nodes[node] for node in way["nodes"]]),
                    },
                } for way_id, way in ways.items()] + [{
                    "type": "Feature",
                    "id": f"n{poi['id']}",
                    "properties": copy.deepcopy(poi["tags"]),
                    "geometry": {
                        "type": "Point", "coordinates": list(poi["coord"]),
                    },
                } for poi in pois] + [malt_raw],
            }
            qa.documents["raw_way_topology"] = {
                "schema_version": 3,
                "source": {
                    "kind": "runner-local-osm-pbf",
                    "artifact": validator.AOI_PBF_RELATIVE,
                    "sha256": input_identity["sha256"],
                    "bytes": input_identity["bytes"],
                },
                "attribution": validator.ATTRIBUTION,
                "way_count": len(ways) + 1,
                "incomplete_way_ids": [],
                "ways": sorted([{
                    "way_id": way_id,
                    "tags": copy.deepcopy(way["tags"]),
                    "node_ids": list(way["nodes"]),
                    "coordinates": [list(nodes[node]) for node in way["nodes"]],
                    "missing_node_ids": [],
                } for way_id, way in ways.items()] + [{
                    "way_id": 1027606528,
                    "tags": copy.deepcopy(malt_raw["properties"]),
                    "node_ids": [9001, 9002, 9003, 9001],
                    "coordinates": copy.deepcopy(
                        malt_raw["geometry"]["coordinates"][0]),
                    "missing_node_ids": [],
                }], key=lambda value: value["way_id"]),
                "relation_count": (
                    len(graphs["aoi_relation_members"]["relations"]) + 1),
                "relations": sorted([{
                    "relation_id": RELATION_ID,
                    "tags": {
                        "name": validator.AREA_NAME,
                        "boundary": "national_park",
                        "type": "boundary",
                    },
                    "members": [],
                }, *[{
                    "relation_id": relation["relation_id"],
                    "tags": copy.deepcopy(relation["tags"]),
                    "members": copy.deepcopy(relation["members"]),
                } for relation in graphs[
                    "aoi_relation_members"]["relations"]]],
                    key=lambda relation: relation["relation_id"]),
                "destination_poi_count": len(pois),
                "destination_pois": sorted([{
                    "node_id": poi["id"],
                    "tags": copy.deepcopy(poi["tags"]),
                    "name": validator._destination_display_name(poi["tags"]),
                    "coordinate": list(poi["coord"]),
                    "eligibility_class":
                        validator._destination_eligibility_class(poi["tags"]),
                } for poi in pois], key=lambda poi: poi["node_id"]),
            }
            qa.documents.update(graphs)
            qa._sync_scope_receipts()
            qa.rewrite("areas")
            qa.rewrite("exact_area")
            qa.rewrite("raw")
            qa.rewrite("raw_way_topology")
            qa.rewrite("removed")
            for label in graphs:
                qa.rewrite(label)
            qa.rewrite("assembly_report")
            qa.rewrite("trails")
            qa._refresh_scope_trust()
            if mutate is not None:
                mutate(qa)
            result_path = root / "validation/final.json"
            validator_rc = validator.main([
                "final", "--qa-dir", str(root),
                "--scope-trust-file", str(qa.scope_trust_path),
                "--raw-relation-members", str(
                    root / validator.RAW_RELATION_MEMBERS_RELATIVE),
                "--prefilter-relation-members", str(
                    root / validator.PREFILTER_RELATION_MEMBERS_RELATIVE),
                "--aoi-relation-members", str(
                    root / validator.AOI_RELATION_MEMBERS_RELATIVE),
                "--expected-osm-relation-id", str(RELATION_ID),
                "--result-json", str(result_path),
            ])
            return validator_rc, json.loads(result_path.read_text()), trails, removed, report

    def test_real_exact_area_producer_output_is_accepted(self):
        rc, result, trails, removed, report = self._run("connected")
        self.assertEqual(rc, 0, result["errors"])
        self.assertEqual(len(trails["features"]), 2)
        properties = trails["features"][0]["properties"]
        self.assertEqual(properties["member_ways"], [10, 11])
        self.assertEqual(properties["restored_relation_way_ids"], [11])
        self.assertEqual(removed["features"], [])
        self.assertEqual(report["quality"]["signed_relation_unresolved_count"], 0)
        self.assertEqual(
            report["quality"]["restored_road_relations"][0]["restored_way_ids"],
            [11])

    def test_pedestrian_area_class_producer_to_final_matrix(self):
        expected_removals = {
            "named-unclaimed": 1,
            "implicit-unclaimed": 1,
            "raw-line-unclaimed": 1,
            "unnamed": 0,
            "relation-claimed": 0,
            "claimed-outside": 0,
            "claimed-connection": 0,
            "area-no": 0,
        }
        for mode, removal_count in expected_removals.items():
            with self.subTest(mode=mode):
                rc, result, trails, removed, _ = self._run(
                    f"pedestrian:{mode}")
                self.assertEqual(rc, 0, result["errors"])
                pedestrian_removals = [
                    feature for feature in removed["features"]
                    if (feature.get("properties") or {}).get(
                        "removed_category") == "standalone-pedestrian-area"
                ]
                self.assertEqual(len(pedestrian_removals), removal_count)
                member_90 = [
                    feature for feature in trails["features"]
                    if 90 in ((feature.get("properties") or {}).get(
                        "member_ways") or [])
                ]
                if mode == "unnamed":
                    self.assertEqual(member_90, [])
                elif mode in {"relation-claimed", "claimed-outside",
                              "claimed-connection"}:
                    self.assertEqual(len(member_90), 1)
                    self.assertEqual(
                        member_90[0]["properties"]["source"], "relation")
                elif mode == "area-no":
                    self.assertEqual(len(member_90), 1)
                    self.assertEqual(
                        member_90[0]["properties"]["source"], "name-stitch")

    def test_fused_roots_producer_to_final_requires_every_proof(self):
        failed_rc, failed_result, failed_trails, _, failed_report = self._run(
            "fused-one-proof")
        self.assertEqual(failed_rc, 1)
        fused = next(
            feature for feature in failed_trails["features"]
            if (feature.get("properties") or {}).get("root_relation_ids") ==
            [700, 701])
        self.assertEqual(
            fused["properties"]["quality_disposition"],
            "unresolved-relation-gap")
        self.assertEqual(
            failed_report["quality"]["signed_relation_unresolved_count"], 1)
        self.assertTrue(any("unresolved-relation-gap" in error
                            for error in failed_result["errors"]),
                        failed_result["errors"])

        passed_rc, passed_result, passed_trails, _, passed_report = self._run(
            "fused-both-proofs")
        self.assertEqual(passed_rc, 0, passed_result["errors"])
        fused = next(
            feature for feature in passed_trails["features"]
            if (feature.get("properties") or {}).get("root_relation_ids") ==
            [700, 701])
        self.assertEqual(fused["properties"]["connectivity"]["status"],
                         "accepted")
        self.assertIn(
            "out-of-area-member-gap",
            fused["properties"]["connectivity"]["accepted_reasons"])
        self.assertEqual(
            passed_report["quality"]["signed_relation_unresolved_count"], 0)

    def test_real_producer_to_validator_composite_service_matrix(self):
        cases = (
            (" ALLEY ", True),
            ("driveway;parking_aisle", False),
            ("parking_aisle;alley", False),
            (" DRIVEWAY ; PARKING_AISLE ", False),
            ("drive-through", False),
            ("drive_through", False),
            ("drivethrough", False),
            ("parking", False),
            ("parking_space", False),
            ("emergency_access", False),
            ("emergency-access", False),
            ("bus", False),
            ("unknown", False),
            ("alley;unknown", False),
        )
        for service, accepted in cases:
            with self.subTest(service=service):
                rc, result, trails, _, report = self._run(
                    f"service:{service}")
                self.assertEqual(rc == 0, accepted, result["errors"])
                properties = trails["features"][0]["properties"]
                self.assertEqual(11 in properties["member_ways"], accepted)
                self.assertEqual(
                    properties["direct_relation_way_ids"]["700"],
                    [10, 11, 12])
                audit = report["quality"]["relation_member_audit"][0]
                self.assertEqual(
                    audit["direct_way_members"][1]["status"],
                    "included" if accepted else "excluded")
                self.assertEqual(
                    report["quality"]["relation_member_unresolved_count"],
                    0 if accepted else 1)

    def test_real_producer_to_validator_conservative_lanes_matrix(self):
        cases = (
            ("1", True),
            ("1;1", True),
            ("2", False),
            ("03", False),
            ("2;1", False),
            ("1;2", False),
            ("two", False),
            ("2.5", False),
            ("1;;1", False),
        )
        for lanes, accepted in cases:
            with self.subTest(lanes=lanes):
                rc, result, trails, _, report = self._run(
                    f"track-lanes:{lanes}")
                self.assertEqual(rc == 0, accepted, result["errors"])
                properties = trails["features"][0]["properties"]
                self.assertEqual(11 in properties["member_ways"], accepted)
                audit = report["quality"]["relation_member_audit"][0]
                member = audit["direct_way_members"][1]
                self.assertEqual(member["status"],
                                 "included" if accepted else "excluded")
                if accepted:
                    self.assertEqual(member["exclusion_reason"], None)
                    self.assertEqual(properties["restored_relation_way_ids"],
                                     [11])
                else:
                    self.assertEqual(member["exclusion_reason"],
                                     "road-like-track-tag")
                    self.assertIn("unsafe-member-in-exact-area",
                                  audit["unresolved_reasons"])

    def test_removed_thru_hike_exclusion_is_informational_without_geometry(self):
        rc, result, trails, _, report = self._run("superroute-private")
        self.assertEqual(rc, 0, result["errors"])
        self.assertEqual(len(trails["features"]), 1)
        self.assertEqual(
            trails["features"][0]["properties"]["root_relation_ids"],
            [8350111])
        quality = report["quality"]
        self.assertEqual(quality["relation_member_unresolved_count"], 0)
        self.assertEqual(quality["relation_member_unresolved"], [])
        self.assertEqual(
            [audit["assembly_status"]
             for audit in quality["relation_member_audit"]],
            ["removed-thru-hike", "removed-thru-hike", "emitted"])
        self.assertEqual(
            [audit["review_status"]
             for audit in quality["relation_member_audit"]],
            ["accepted", "accepted", "accepted"])
        removed_audit = quality["relation_member_audit"][0]
        self.assertEqual(removed_audit["unresolved_reasons"], [])
        excluded = [member for member in removed_audit["direct_way_members"]
                    if member["status"] == "excluded"]
        self.assertEqual(excluded[0]["render_disposition"],
                         "removed-thru-hike-informational")
        self.assertIsNone(excluded[0]["terminal_reason"])

    def test_legitimate_curation_and_clip_drops_round_trip(self):
        for case, category in (
                ("min-length-drop", "min-length"),
                ("clip-sliver-drop", "min-inside-mi"),
                ("outside-drop", "outside-exact-area")):
            with self.subTest(case=case):
                rc, result, trails, removed, report = self._run(case)
                self.assertEqual(rc, 0, result["errors"])
                self.assertEqual(len(trails["features"]), 1)
                row = next(
                    feature for feature in removed["features"]
                    if (feature.get("properties") or {}).get(
                        "root_relation_ids") == [700])
                properties = row["properties"]
                self.assertEqual(properties["removed_category"], category)
                self.assertEqual(properties["source"], "relation")
                self.assertEqual(properties["direct_relation_way_ids"],
                                 {"700": [10]})
                self.assertEqual(properties["source_geometry_way_ids"], [10])
                self.assertFalse(next(
                    audit for audit in report["quality"][
                        "relation_member_audit"]
                    if audit["relation_id"] == 700)["emitted_in_exact_area"])

    def test_forged_curation_drop_root_source_and_geometry_are_rejected(self):
        def wrong_root(qa):
            row = next(
                feature for feature in qa.documents["removed"]["features"]
                if (feature.get("properties") or {}).get(
                    "root_relation_ids") == [700])
            row["properties"]["root_relation_ids"] = [701]
            qa.rewrite("removed")

        def wrong_source(qa):
            row = next(
                feature for feature in qa.documents["removed"]["features"]
                if (feature.get("properties") or {}).get(
                    "root_relation_ids") == [700])
            row["properties"]["source_ways"][0]["node_ids"] = [2, 1]
            qa.rewrite("removed")

        def wrong_geometry(qa):
            row = next(
                feature for feature in qa.documents["removed"]["features"]
                if (feature.get("properties") or {}).get(
                    "root_relation_ids") == [700])
            row["geometry"]["coordinates"] = [
                [[10.7, 56.3], [10.701, 56.3]]]
            qa.rewrite("removed")

        def wrong_category(qa):
            row = next(
                feature for feature in qa.documents["removed"]["features"]
                if (feature.get("properties") or {}).get(
                    "root_relation_ids") == [700])
            properties = row["properties"]
            properties["removed_category"] = "closed"
            properties["removed_reason"] = "Marked closed in OSM."
            properties["drop_evidence"]["category"] = "closed"
            qa.rewrite("removed")

        def partial_source(qa):
            row = next(
                feature for feature in qa.documents["removed"]["features"]
                if (feature.get("properties") or {}).get(
                    "root_relation_ids") == [700])
            row["properties"]["source_ways"] = []
            qa.rewrite("removed")

        for mutate in (wrong_root, wrong_source, wrong_geometry,
                       wrong_category, partial_source):
            with self.subTest(mutate=mutate.__name__):
                rc, result, *_ = self._run("min-length-drop", mutate)
                self.assertEqual(rc, 1)
                self.assertTrue(any(
                    "drop" in error or "dropped authority" in error
                    or "raw authority" in error or "source-way" in error
                    or "curation evidence" in error
                    or "category/reason is not reproducible" in error
                    for error in result["errors"]), result["errors"])

    def test_verified_drop_cannot_hide_authoritative_way_in_output(self):
        def mutate(qa):
            golden = next(
                feature for feature in qa.documents["trails"]["features"]
                if feature["properties"].get("root_relation_ids") == [8350111])
            golden["properties"]["member_ways"].append(10)
            qa.rewrite("trails")

        rc, result, *_ = self._run("min-length-drop", mutate)
        self.assertEqual(rc, 1)
        self.assertTrue(any(
            "dropped relation root r700 unexpectedly ships" in error
            for error in result["errors"]), result["errors"])

    def test_relation_display_name_fallback_round_trips_and_is_post_matched(self):
        rc, result, trails, _, _ = self._run("ref-only")
        self.assertEqual(rc, 0, result["errors"])
        route = next(feature for feature in trails["features"]
                     if feature["properties"].get("root_relation_ids") == [700])
        self.assertEqual(route["properties"]["name"], "Member Named Route")

        rc, result, trails, _, _ = self._run("localized-relation")
        self.assertEqual(rc, 0, result["errors"])
        route = next(feature for feature in trails["features"]
                     if feature["properties"].get("root_relation_ids") == [700])
        self.assertEqual(route["properties"]["name"], "Lokal Rute")

        def rename_after_production(qa):
            route = next(
                feature for feature in qa.documents["trails"]["features"]
                if feature["properties"].get("root_relation_ids") == [700])
            route["properties"]["name"] = "Forged Display"
            qa.rewrite("trails")

        rc, result, *_ = self._run("ref-only", rename_after_production)
        self.assertEqual(rc, 1)
        self.assertTrue(any(
            "name contradicts authority-derived root r700" in error
            for error in result["errors"]), result["errors"])

    def test_relation_without_any_authoritative_name_fails_diagnostically(self):
        rc, result, *_ = self._run("unnamed")
        self.assertEqual(rc, 1)
        self.assertTrue(any(
            "no authoritative display name or named eligible main way" in error
            for error in result["errors"]), result["errors"])

    def test_fully_filtered_superroute_status_precedes_thru_hike_removal(self):
        rc, result, trails, _, report = self._run("superroute-filtered")
        self.assertEqual(rc, 1)
        self.assertEqual(len(trails["features"]), 1)
        audits = {row["relation_id"]: row for row in
                  report["quality"]["relation_member_audit"]}
        self.assertEqual(audits[700]["assembly_status"], "entirely-filtered")
        self.assertEqual(audits[701]["assembly_status"], "entirely-filtered")
        self.assertIn("unsafe-member-in-exact-area",
                      audits[700]["unresolved_reasons"])
        self.assertIn("unsafe-member-in-exact-area",
                      audits[701]["unresolved_reasons"])
        self.assertFalse(any(
            "assembly status contradicts" in error
            for error in result["errors"]), result["errors"])

    def test_absorbed_same_name_path_cannot_dilute_relation_share(self):
        rc, result, trails, removed, report = self._run("diluted-restoration")
        self.assertEqual(rc, 0, result["errors"])
        self.assertEqual(removed["features"], [])
        properties = trails["features"][0]["properties"]
        self.assertEqual(properties["member_ways"], [10, 11, 12])
        measurement = report["quality"]["restored_road_relations"][0]
        self.assertEqual(measurement["relation_id"], 700)
        self.assertEqual(measurement["relation_way_ids"], [10, 11])
        self.assertNotIn(12, measurement["relation_way_ids"])
        self.assertGreater(measurement["restored_share"], 0.10)
        self.assertFalse(any("restored-road" in error and "limit" in error
                             for error in result["errors"]))

    def test_geometry_duplicate_absorption_round_trips_and_rejects_attacks(self):
        for case, dropped_root, survivor_source in (
                ("duplicate-relations", [701], "relation"),
                ("duplicate-named-member", [700], "name-stitch")):
            with self.subTest(case=case):
                rc, result, trails, removed, report = self._run(case)
                self.assertEqual(rc, 0, result["errors"])
                rows = [
                    feature for feature in removed["features"]
                    if (feature.get("properties") or {}).get(
                        "removed_category") ==
                    validator.assembly_model.GEOMETRY_DUPLICATE_ABSORBED_CATEGORY
                ]
                self.assertEqual(len(rows), 1)
                properties = rows[0]["properties"]
                self.assertEqual(properties["root_relation_ids"], dropped_root)
                self.assertEqual(properties["source_geometry_way_ids"], [10])
                self.assertEqual([record["way_id"]
                                  for record in properties["source_ways"]], [10])
                evidence = properties["drop_evidence"]
                self.assertEqual(evidence["survivor"]["source"], survivor_source)
                self.assertTrue(evidence["geometry_proof"][
                    "absorbed_covered_by_survivor"])
                self.assertEqual(evidence["geometry_proof"]["relationship"],
                                 "equal")
                survivor = next(
                    feature for feature in trails["features"]
                    if feature["properties"].get("source") == survivor_source
                    and feature["properties"].get("ckey") ==
                    evidence["survivor"]["ckey"]
                    and feature["properties"].get("name") ==
                    evidence["survivor"]["name"])
                self.assertIsNotNone(survivor)
                audit = next(
                    row for row in report["quality"]["relation_member_audit"]
                    if row["relation_id"] == dropped_root[0])
                self.assertFalse(audit["emitted_in_exact_area"])

        def absorption_row(qa):
            return next(
                feature for feature in qa.documents["removed"]["features"]
                if (feature.get("properties") or {}).get(
                    "removed_category") ==
                validator.assembly_model.GEOMETRY_DUPLICATE_ABSORBED_CATEGORY)

        def forged_survivor(qa):
            absorption_row(qa)["properties"]["drop_evidence"]["survivor"][
                "name"] = "Forged Survivor"
            qa.rewrite("removed")

        def forged_geometry(qa):
            absorption_row(qa)["geometry"]["coordinates"] = [[
                [10.500, 56.15], [10.510, 56.16],
            ]]
            qa.rewrite("removed")

        def forged_survivor_geometry(qa):
            survivor = next(
                feature for feature in qa.documents["trails"]["features"]
                if (feature.get("properties") or {}).get("source") ==
                "name-stitch")
            survivor["geometry"]["coordinates"] = [[
                [10.500, 56.15], [10.510, 56.15],
            ]]
            qa.rewrite("trails")

        def forged_root(qa):
            absorption_row(qa)["properties"]["root_relation_ids"] = [701]
            qa.rewrite("removed")

        attacks = (
            (forged_survivor,
             "removed relation[0] absorption does not identify one "
             "unambiguous survivor"),
            (forged_geometry,
             "emitted relation root r700 has no output candidate or verified "
             "producer drop"),
            (forged_survivor_geometry,
             "removed relation[0] absorption does not identify one "
             "unambiguous survivor"),
            (forged_root,
             "emitted relation root r700 has no output candidate or verified "
             "producer drop"),
        )
        for mutate, expected_message in attacks:
            with self.subTest(attack=mutate.__name__):
                rc, result, *_ = self._run("duplicate-named-member", mutate)
                self.assertEqual(rc, 1)
                self.assertIn(expected_message, result["errors"])

    def test_terminal_absorption_uses_only_final_population(self):
        rc, result, trails, removed, report = self._run(
            "duplicate-relations-outside")
        self.assertEqual(rc, 0, result["errors"])
        outside_rows = [
            feature for feature in removed["features"]
            if feature["properties"].get("removed_category") ==
            "outside-exact-area"
        ]
        self.assertEqual(
            sorted(feature["properties"]["root_relation_ids"]
                   for feature in outside_rows), [[700], [701]])
        self.assertFalse(any(
            feature["properties"].get("removed_category") ==
            validator.assembly_model.GEOMETRY_DUPLICATE_ABSORBED_CATEGORY
            for feature in removed["features"]))
        self.assertFalse(any(
            set(feature["properties"].get("root_relation_ids") or [])
            & {700, 701} for feature in trails["features"]))

        rc, result, trails, removed, report = self._run(
            "survivor-quality-removed")
        self.assertEqual(rc, 1)
        self.assertIn(
            "assembly terminal relation absorption unresolved population is nonzero",
            result["errors"])
        relation = next(
            feature for feature in trails["features"]
            if feature["properties"].get("root_relation_ids") == [700])
        self.assertEqual(relation["properties"]["name"], "Foot Path")
        self.assertFalse(any(
            feature["properties"].get("removed_category") ==
            validator.assembly_model.GEOMETRY_DUPLICATE_ABSORBED_CATEGORY
            for feature in removed["features"]))
        quality_target = next(
            feature for feature in removed["features"]
            if feature["properties"].get("name") ==
            "Descriptive Mountain Trail")
        self.assertEqual(quality_target["properties"]["removed_category"],
                         "nested-name-stitch-overlap")
        self.assertEqual(report["quality"]["kept_count"],
                         len(trails["features"]))
        self.assertEqual(
            report["quality"]["terminal_absorption_unresolved_count"], 1)
        self.assertEqual(
            report["quality"]["terminal_absorption_unresolved"][0]["reason"],
            "quality-removed-target")

        rc, result, trails, removed, report = self._run(
            "three-relation-chain")
        self.assertEqual(rc, 0, result["errors"])
        survivor = next(
            feature for feature in trails["features"]
            if feature["properties"].get("root_relation_ids") == [702])
        rows = [
            feature for feature in removed["features"]
            if feature["properties"].get("removed_category") ==
            validator.assembly_model.GEOMETRY_DUPLICATE_ABSORBED_CATEGORY
        ]
        self.assertEqual(
            sorted(feature["properties"]["root_relation_ids"] for feature in rows),
            [[700], [701]])
        expected_survivor = {
            "ckey": survivor["properties"]["ckey"],
            "name": "Descriptive Ridge Trail",
            "source": "relation",
            "geometry_sha256": validator._publisher_json_sha256(
                survivor["geometry"]),
        }
        for row in rows:
            evidence = row["properties"]["drop_evidence"]
            self.assertEqual(evidence["survivor"], expected_survivor)
            self.assertEqual(evidence["geometry_proof"]["relationship"],
                             "equal")
        audits = {
            audit["relation_id"]: audit["emitted_in_exact_area"]
            for audit in report["quality"]["relation_member_audit"]
        }
        self.assertFalse(audits[700])
        self.assertFalse(audits[701])
        self.assertTrue(audits[702])
        self.assertEqual(report["quality"]["candidate_count"],
                         len(trails["features"]) + 2)
        self.assertEqual(report["quality"]["removed_counts"][
            validator.assembly_model.GEOMETRY_DUPLICATE_ABSORBED_CATEGORY], 2)

        def intermediate_cycle(qa):
            rows = [
                feature for feature in qa.documents["removed"]["features"]
                if feature["properties"].get("removed_category") ==
                validator.assembly_model.GEOMETRY_DUPLICATE_ABSORBED_CATEGORY
            ]
            for row, other in zip(rows, reversed(rows)):
                other_properties = other["properties"]
                row["properties"]["drop_evidence"]["survivor"] = {
                    "ckey": other_properties["ckey"],
                    "name": other_properties["name"],
                    "source": other_properties["source"],
                    "geometry_sha256": validator._publisher_json_sha256(
                        other["geometry"]),
                }
            qa.rewrite("removed")

        rc, result, *_ = self._run("three-relation-chain", intermediate_cycle)
        self.assertEqual(rc, 1)
        self.assertTrue(any(
            "absorption does not identify one unambiguous survivor" in error
            for error in result["errors"]), result["errors"])

    def test_model_curation_removed_absorption_targets_fail_closed(self):
        expected_reasons = {
            "closed": (
                "Marked closed in OSM — the name itself says CLOSED "
                "(e.g. 'CLOSED - old Pyramid Trail')."),
            "access": (
                "Not open to the public — OSM marks this way access=private / "
                "access=no (or foot=no) with no foot permission, so it isn't a "
                "trail the public can legally complete."),
            "min-length": (
                "Too short — 0.099 mi is below the 0.1 mi minimum (likely a "
                "connector stub, not a hike on its own)."),
            "thru-hike": (
                "Long-distance thru-hike, not a single completable trail — a "
                "named national/regional route (PCT, CDT, Colorado Trail, "
                "Hayduke, Arizona Trail, …) or one of its numbered segments."),
            "named-road": (
                "Named road carried as a path — a bare '… Road / Highway' with "
                "no trail identity (e.g. 'Maxwell Ranch Road'). Carriage roads "
                "and any '… Trail' are kept; a washed-out road you still hike "
                "can be rescued from this bucket by eye."),
        }
        for category, expected_reason in expected_reasons.items():
            with self.subTest(category=category):
                rc, result, trails, removed, report = self._run(
                    f"model-curation-target:{category}")
                self.assertEqual(rc, 1)
                self.assertIn(
                    "assembly terminal relation absorption unresolved "
                    "population is nonzero", result["errors"])
                self.assertEqual(
                    report["failure"],
                    "unresolved terminal relation absorptions: 1")
                self.assertEqual(
                    report["quality"]["terminal_absorption_unresolved_count"],
                    1)
                record = report["quality"][
                    "terminal_absorption_unresolved"][0]
                self.assertEqual(
                    record["reason"], "model-curation-removed-target")
                self.assertEqual(
                    record["source_candidate"]["population"], "published")
                target = record["target_candidates"][0]
                self.assertEqual(target["population"], "removed")
                self.assertEqual(target["removal_stage"], "model-curation")
                self.assertEqual(target["removed_category"], category)
                self.assertEqual(target["removed_reason"], expected_reason)
                self.assertEqual(target["successor_candidate_ids"], [])
                self.assertTrue(any(
                    feature["properties"].get("root_relation_ids") == [700]
                    for feature in trails["features"]))
                removed_target = next(
                    feature for feature in removed["features"]
                    if feature["properties"].get("root_relation_ids") == [701])
                self.assertEqual(
                    removed_target["properties"]["removed_category"], category)
                self.assertEqual(
                    removed_target["properties"]["removed_reason"],
                    expected_reason)

    def test_promoted_hike_terminal_absorption_round_trips_and_rejects_tamper(self):
        rc, result, trails, removed, report = self._run("promoted-hike")
        self.assertEqual(rc, 0, result["errors"])
        survivor = next(
            feature for feature in trails["features"]
            if feature["properties"].get("root_relation_ids") == [700])
        self.assertEqual(survivor["properties"]["name"], "Mols Summit Trail")
        self.assertEqual(survivor["properties"]["kind"], "hike")
        self.assertEqual(survivor["properties"]["destinations"], ["Mols Summit"])
        self.assertEqual(survivor["properties"]["destination_evidence"], [{
            "osm_node_id": 900,
            "name": "Mols Summit",
            "tags": {"name": "Mols Summit", "natural": "peak"},
            "eligibility_class": "natural=peak",
            "coordinate": [10.55, 56.15],
            "selected_endpoint_index": 1,
            "selected_endpoint_coordinate": [10.55, 56.15],
            "distance_ft": 0.0,
            "reach_limit_ft": 250,
            "selection_rank": 1,
            "promotion_root_relation_id": 700,
            "endpoint_source_way_id": 10,
            "endpoint_source_node_id": 3,
        }])
        row = next(
            feature for feature in removed["features"]
            if feature["properties"].get("removed_category") ==
            validator.assembly_model.GEOMETRY_DUPLICATE_ABSORBED_CATEGORY)
        properties = row["properties"]
        self.assertEqual(properties["root_relation_ids"], [701])
        self.assertEqual(properties["relation_ids"], [701])
        self.assertEqual(properties["direct_relation_way_ids"], {"701": [11]})
        self.assertEqual(properties["source_geometry_way_ids"], [11])
        self.assertEqual([record["way_id"] for record in properties["source_ways"]],
                         [11])
        evidence = properties["drop_evidence"]
        self.assertEqual(evidence["survivor"], {
            "ckey": "w10", "name": "Mols Summit Trail", "source": "relation",
            "geometry_sha256": validator._publisher_json_sha256(
                survivor["geometry"]),
        })
        self.assertEqual(evidence["geometry_proof"], {
            "method": "exact-area-linework-difference",
            "absorbed_covered_by_survivor": True,
            "survivor_covered_by_absorbed": False,
            "relationship": "covered-by-survivor",
        })
        self.assertTrue(shape(row["geometry"]).difference(
            shape(survivor["geometry"])).is_empty)
        self.assertFalse(shape(survivor["geometry"]).difference(
            shape(row["geometry"])).is_empty)
        self.assertEqual(report["quality"]["candidate_count"],
                         len(trails["features"]) + 1)
        decisions = report["quality"]["promotion_decisions"]
        self.assertEqual(
            [(decision["candidate_index"], decision["decision"])
             for decision in decisions],
            [(0, "promoted"), (1, "declined"), (2, "declined")])
        self.assertEqual(decisions[1]["reason"], "not-route-candidate")
        self.assertEqual(
            survivor["properties"]["destination_evidence"][0]["osm_node_id"],
            decisions[0]["poi_osm_node_id"])
        self.assertFalse(any(
            feature is not survivor
            and feature["properties"].get("ckey") ==
            evidence["survivor"]["ckey"]
            for feature in trails["features"]))

        def absorption_row(qa):
            return next(
                feature for feature in qa.documents["removed"]["features"]
                if feature["properties"].get("removed_category") ==
                validator.assembly_model.GEOMETRY_DUPLICATE_ABSORBED_CATEGORY)

        def claim_name_tamper(qa):
            absorption_row(qa)["properties"]["drop_evidence"]["survivor"][
                "name"] = "Forged Summit Trail"
            qa.rewrite("removed")

        def claim_source_tamper(qa):
            absorption_row(qa)["properties"]["drop_evidence"]["survivor"][
                "source"] = "name-stitch"
            qa.rewrite("removed")

        def claim_geometry_tamper(qa):
            absorption_row(qa)["properties"]["drop_evidence"]["survivor"][
                "geometry_sha256"] = "0" * 64
            qa.rewrite("removed")

        def survivor_name_tamper(qa):
            target = next(
                feature for feature in qa.documents["trails"]["features"]
                if feature["properties"].get("root_relation_ids") == [700])
            target["properties"]["name"] = "Forged Summit Trail"
            qa.rewrite("trails")

        def survivor_geometry_tamper(qa):
            target = next(
                feature for feature in qa.documents["trails"]["features"]
                if feature["properties"].get("root_relation_ids") == [700])
            target["geometry"]["coordinates"] = [[
                [10.500, 56.15], [10.520, 56.16],
            ]]
            qa.rewrite("trails")

        def multiple_survivors(qa):
            target = next(
                feature for feature in qa.documents["trails"]["features"]
                if feature["properties"].get("root_relation_ids") == [700])
            duplicate = copy.deepcopy(target)
            duplicate["properties"]["quality_candidate_index"] = 99
            qa.documents["trails"]["features"].append(duplicate)
            qa.rewrite("trails")

        attacks = (
            (claim_name_tamper,
             "removed relation[0] absorption does not identify one "
             "unambiguous survivor"),
            (claim_source_tamper,
             "removed relation[0] absorption does not identify one "
             "unambiguous survivor"),
            (claim_geometry_tamper,
             "removed relation[0] absorption does not identify one "
             "unambiguous survivor"),
            (survivor_name_tamper,
             "relation candidate[0] name contradicts authority-derived root "
             "r700"),
            (survivor_geometry_tamper,
             "emitted relation root r700 has no output candidate or verified "
             "producer drop"),
            (multiple_survivors,
             "removed relation[0] absorption does not identify one "
             "unambiguous survivor"),
        )
        for mutate, expected_message in attacks:
            with self.subTest(mutate=mutate.__name__):
                rc, result, *_ = self._run("promoted-hike", mutate)
                self.assertEqual(rc, 1)
                self.assertIn(expected_message, result["errors"])

    def test_postclip_promotion_drops_stale_evidence_and_reselects_by_source_id(self):
        rc, result, trails, _removed, _report = self._run(
            "promotion-clipped-out")
        self.assertEqual(rc, 0, result["errors"])
        route = next(
            feature for feature in trails["features"]
            if feature["properties"].get("root_relation_ids") == [700])
        self.assertEqual(route["properties"]["name"], "Boundary Access Route")
        self.assertEqual(route["properties"]["kind"], "route")
        self.assertEqual(route["properties"]["destinations"], [])
        self.assertTrue(route["properties"]["clipped"])
        self.assertNotIn("destination_evidence", route["properties"])

        rc, result, trails, _removed, _report = self._run(
            "promotion-clipped-index-shift")
        self.assertEqual(rc, 0, result["errors"])
        shifted = next(
            feature for feature in trails["features"]
            if feature["properties"].get("root_relation_ids") == [700])
        shifted_evidence = shifted["properties"]["destination_evidence"][0]
        self.assertEqual(shifted["properties"]["name"], "Surviving View Trail")
        self.assertEqual(shifted_evidence["selected_endpoint_index"], 0)
        self.assertEqual(shifted_evidence["endpoint_source_way_id"], 10)
        self.assertEqual(shifted_evidence["endpoint_source_node_id"], 3)
        self.assertEqual(shifted_evidence["selected_endpoint_coordinate"],
                         [10.55, 56.15])

        rc, result, trails, _removed, _report = self._run(
            "promotion-clipped-reselect")
        self.assertEqual(rc, 0, result["errors"])
        reselected = next(
            feature for feature in trails["features"]
            if feature["properties"].get("root_relation_ids") == [700])
        reselected_evidence = reselected["properties"][
            "destination_evidence"][0]
        self.assertEqual(reselected["properties"]["name"],
                         "Surviving View Trail")
        self.assertEqual(reselected_evidence["osm_node_id"], 900)
        self.assertEqual(reselected_evidence["endpoint_source_way_id"], 10)
        self.assertEqual(reselected_evidence["endpoint_source_node_id"], 3)

        def omit_diagnostic_index(qa):
            candidate = next(
                feature for feature in qa.documents["trails"]["features"]
                if feature["properties"].get("root_relation_ids") == [700])
            candidate["properties"]["destination_evidence"][0].pop(
                "selected_endpoint_index")
            qa.rewrite("trails")

        rc, result, *_ = self._run(
            "promotion-clipped-index-shift", omit_diagnostic_index)
        self.assertEqual(rc, 0, result["errors"])

    def test_multi_root_identity_uses_authority_order_not_input_order(self):
        controls = (
            ("identity-mols", "Mols Rute"),
            ("identity-kalo", "Kalø"),
        )
        for prefix, expected_name in controls:
            snapshots = []
            for order in ("forward", "reverse"):
                case = f"multi-root-policy:{prefix}-{order}"
                with self.subTest(case=case):
                    rc, result, trails, _removed, report = self._run(case)
                    self.assertEqual(rc, 0, result["errors"])
                    candidate = next(
                        feature for feature in trails["features"]
                        if feature["properties"].get(
                            "root_relation_ids") == [700, 701])
                    properties = candidate["properties"]
                    identity = {
                        key: properties[key] for key in (
                            "name", "kind", "network", "operator",
                            "sac_scale", "trail_visibility",
                            "identity_root_relation_id", "root_relation_ids")
                    }
                    self.assertEqual(identity, {
                        "name": expected_name,
                        "kind": "trail",
                        "network": "lwn",
                        "operator": "Primary Operator",
                        "sac_scale": "hiking",
                        "trail_visibility": "excellent",
                        "identity_root_relation_id": 700,
                        "root_relation_ids": [700, 701],
                    })
                    decision = report["quality"]["promotion_decisions"][0]
                    self.assertEqual(decision, {
                        "candidate_index": 0,
                        "candidate_ckey": "w10-20",
                        "root_relation_ids": [700, 701],
                        "identity_root_relation_id": 700,
                        "decision": "declined",
                        "reason": "not-route-candidate",
                        "blocking_roots": [
                            {"root_relation_id": 700,
                             "reason": "not-route-candidate"},
                            {"root_relation_id": 701,
                             "reason": "not-route-candidate"},
                        ],
                        "skipped_ambiguous_pois": [],
                    })
                    snapshots.append((identity, decision))
            self.assertEqual(snapshots[0], snapshots[1])

        rc, result, trails, _removed, report = self._run(
            "multi-root-policy:identity-fallback")
        self.assertEqual(rc, 0, result["errors"])
        fallback = next(
            feature for feature in trails["features"]
            if feature["properties"].get("root_relation_ids") == [700, 701])
        self.assertEqual(fallback["properties"]["name"], "Første Rute")
        self.assertEqual(
            fallback["properties"]["identity_root_relation_id"], 700)
        self.assertEqual(
            report["quality"]["promotion_decisions"][0][
                "identity_root_relation_id"], 700)

    def test_multi_root_promotion_eligibility_requires_every_root(self):
        blocked_cases = {
            "local-rwn": (
                "not-local-route",
                [{"root_relation_id": 701, "reason": "not-local-route"}],
                "route",
            ),
            "rwn-local": (
                "not-local-route",
                [{"root_relation_id": 700, "reason": "not-local-route"}],
                "route",
            ),
            "route-nonroute": (
                "not-route-candidate",
                [{"root_relation_id": 701,
                  "reason": "not-route-candidate"}],
                "route",
            ),
            "mixed-blockers": (
                "not-local-route",
                [
                    {"root_relation_id": 700,
                     "reason": "not-route-candidate"},
                    {"root_relation_id": 701,
                     "reason": "not-local-route"},
                ],
                "trail",
            ),
        }
        for mode, (reason, blockers, expected_kind) in blocked_cases.items():
            with self.subTest(mode=mode):
                rc, result, trails, _removed, report = self._run(
                    f"multi-root-policy:{mode}")
                self.assertEqual(rc, 0, result["errors"])
                candidate = next(
                    feature for feature in trails["features"]
                    if feature["properties"].get(
                        "root_relation_ids") == [700, 701])
                self.assertEqual(candidate["properties"]["kind"],
                                 expected_kind)
                self.assertNotIn(
                    "destination_evidence", candidate["properties"])
                decision = report["quality"]["promotion_decisions"][0]
                self.assertEqual(decision["reason"], reason)
                self.assertEqual(decision["blocking_roots"], blockers)
                self.assertEqual(decision["identity_root_relation_id"], 700)

        rc, result, trails, _removed, report = self._run(
            "multi-root-policy:all-local")
        self.assertEqual(rc, 0, result["errors"])
        hike = next(
            feature for feature in trails["features"]
            if feature["properties"].get("root_relation_ids") == [700, 701])
        self.assertEqual(hike["properties"]["name"], "Policy Summit Trail")
        self.assertEqual(hike["properties"]["kind"], "hike")
        self.assertEqual(hike["properties"]["identity_root_relation_id"], 700)
        self.assertEqual(
            report["quality"]["promotion_decisions"][0]["decision"],
            "promoted")
        self.assertNotIn(
            "blocking_roots", report["quality"]["promotion_decisions"][0])

    def test_multi_root_identity_and_policy_tampering_fails_precisely(self):
        def secondary_display_text(qa):
            candidate = next(
                feature for feature in qa.documents["trails"]["features"]
                if feature["properties"].get(
                    "root_relation_ids") == [700, 701])
            candidate["properties"]["name"] = "Mols rute"
            qa.rewrite("trails")

        rc, result, *_ = self._run(
            "multi-root-policy:identity-mols-reverse",
            secondary_display_text)
        self.assertEqual(rc, 1)
        self.assertIn(
            "relation candidate[0] declined identity field 'name' diverges: "
            "expected 'Mols Rute', got 'Mols rute'", result["errors"])

        def different_name_key(qa):
            candidate = next(
                feature for feature in qa.documents["trails"]["features"]
                if feature["properties"].get(
                    "root_relation_ids") == [700, 701])
            candidate["properties"]["name"] = "Mols Other"
            qa.rewrite("trails")

        rc, result, *_ = self._run(
            "multi-root-policy:identity-mols-forward", different_name_key)
        self.assertEqual(rc, 1)
        self.assertIn(
            "relation candidate[0] declined identity field 'name_key' "
            "diverges: expected 'mols rute', got 'mols other'",
            result["errors"])

        def synchronized_identity_root(qa):
            candidate = next(
                feature for feature in qa.documents["trails"]["features"]
                if feature["properties"].get(
                    "root_relation_ids") == [700, 701])
            candidate["properties"]["identity_root_relation_id"] = 701
            row = qa.documents["assembly_report"]["quality"][
                "promotion_decisions"][0]
            row["identity_root_relation_id"] = 701
            qa.rewrite("trails")
            qa.rewrite("assembly_report")

        rc, result, *_ = self._run(
            "multi-root-policy:identity-mols-forward",
            synchronized_identity_root)
        self.assertEqual(rc, 1)
        self.assertIn(
            "relation candidate[0] identity_root_relation_id diverges: "
            "expected 700, got 701", result["errors"])
        self.assertIn(
            "relation candidate[0] promotion ledger field "
            "'identity_root_relation_id' diverges: expected 700, got 701",
            result["errors"])

        def synchronized_network_decline(qa):
            candidate = next(
                feature for feature in qa.documents["trails"]["features"]
                if feature["properties"].get(
                    "root_relation_ids") == [700, 701])
            candidate["properties"].update({
                "name": "Shared Access Route",
                "kind": "route",
                "network": "rwn",
                "destinations": [],
            })
            candidate["properties"].pop("destination_evidence")
            row = qa.documents["assembly_report"]["quality"][
                "promotion_decisions"][0]
            identity = {
                key: row[key] for key in (
                    "candidate_index", "candidate_ckey",
                    "root_relation_ids", "identity_root_relation_id")
            }
            row.clear()
            row.update({
                **identity,
                "decision": "declined",
                "reason": "not-local-route",
                "blocking_roots": [{
                    "root_relation_id": 700,
                    "reason": "not-local-route",
                }],
                "skipped_ambiguous_pois": [],
            })
            qa.rewrite("trails")
            qa.rewrite("assembly_report")

        rc, result, *_ = self._run(
            "multi-root-policy:all-local", synchronized_network_decline)
        self.assertEqual(rc, 1)
        self.assertIn(
            "relation candidate[0] identity field 'network' diverges: "
            "expected 'lwn', got 'rwn'", result["errors"])
        self.assertIn(
            "relation candidate[0] promotion ledger field 'decision' "
            "diverges: expected 'promoted', got 'declined'",
            result["errors"])

    def test_final_geometry_precedes_promotion_for_consumed_endpoints(self):
        rc, result, trails, _removed, report = self._run(
            "promotion-fused-consumed")
        self.assertEqual(rc, 0, result["errors"])
        route = next(
            feature for feature in trails["features"]
            if feature["properties"].get("root_relation_ids") == [700, 701])
        self.assertEqual(route["properties"]["ckey"], "w10-20")
        self.assertEqual(route["properties"]["kind"], "route")
        self.assertNotIn([10.53, 56.15], [
            list(point) for point in validator._independent_true_endpoints(
                route["geometry"]["coordinates"])
        ])
        decision = next(
            row for row in report["quality"]["promotion_decisions"]
            if row["candidate_index"] ==
            route["properties"]["quality_candidate_index"])
        self.assertEqual(decision, {
            "candidate_index": 0,
            "candidate_ckey": "w10-20",
            "root_relation_ids": [700, 701],
            "identity_root_relation_id": 700,
            "decision": "declined",
            "reason": "no-eligible-poi-in-reach",
            "skipped_ambiguous_pois": [],
        })

        rc, result, trails, _removed, report = self._run(
            "promotion-fused-reselect")
        self.assertEqual(rc, 0, result["errors"])
        hike = next(
            feature for feature in trails["features"]
            if feature["properties"].get("root_relation_ids") == [700, 701])
        evidence = hike["properties"]["destination_evidence"]
        self.assertEqual(len(evidence), 1)
        self.assertEqual(hike["properties"]["ckey"], "w10-20")
        self.assertEqual(hike["properties"]["name"], "Surviving View Trail")
        self.assertEqual(evidence[0]["selected_endpoint_coordinate"],
                         [10.56, 56.15])
        self.assertEqual(evidence[0]["endpoint_source_way_id"], 20)
        self.assertEqual(evidence[0]["endpoint_source_node_id"], 3)
        self.assertEqual(
            report["quality"]["promotion_decisions"][0]["decision"],
            "promoted")

    def test_ambiguous_rank_falls_through_and_records_skipped_poi(self):
        rc, result, trails, _removed, report = self._run(
            "promotion-ambiguous-fallback")
        self.assertEqual(rc, 0, result["errors"])
        hike = next(
            feature for feature in trails["features"]
            if feature["properties"].get("root_relation_ids") == [700, 701])
        evidence = hike["properties"]["destination_evidence"][0]
        skipped = [{
            "osm_node_id": 800,
            "name": "Consumed Summit",
            "selected_endpoint_coordinate": [10.5, 56.15],
            "distance_ft": 0.0,
            "selection_rank": 1,
            "endpoint_sources": [
                {"root_relation_id": 700, "way_id": 10, "node_id": 1},
                {"root_relation_id": 701, "way_id": 20, "node_id": 1},
            ],
        }]
        self.assertEqual(evidence["osm_node_id"], 900)
        self.assertEqual(evidence["selection_rank"], 2)
        self.assertEqual(evidence["skipped_ambiguous_pois"], skipped)
        decision = report["quality"]["promotion_decisions"][0]
        self.assertEqual(decision["selection_rank"], 2)
        self.assertEqual(decision["skipped_ambiguous_pois"], skipped)

        def omit_skipped_evidence(qa):
            candidate = next(
                feature for feature in qa.documents["trails"]["features"]
                if feature["properties"].get("root_relation_ids") == [700, 701])
            candidate["properties"]["destination_evidence"][0].pop(
                "skipped_ambiguous_pois")
            qa.rewrite("trails")

        rc, result, *_ = self._run(
            "promotion-ambiguous-fallback", omit_skipped_evidence)
        self.assertEqual(rc, 1)
        self.assertIn(
            "relation candidate[0] promoted destination ambiguity evidence "
            "is inconsistent", result["errors"])

    def test_duplicate_destination_has_one_owner_without_fusion(self):
        rc, result, trails, _removed, report = self._run(
            "promotion-duplicate-destination")
        self.assertEqual(rc, 0, result["errors"])
        candidates = {
            tuple(feature["properties"].get("root_relation_ids") or []): feature
            for feature in trails["features"]
        }
        winner = candidates[(700,)]
        declined = candidates[(701,)]
        self.assertEqual(winner["properties"]["name"], "Shared Summit Trail")
        self.assertEqual(winner["properties"]["kind"], "hike")
        self.assertEqual(winner["properties"]["ckey"], "w10")
        self.assertEqual(len(winner["properties"]["destination_evidence"]), 1)
        self.assertEqual(declined["properties"]["name"], "South Access Route")
        self.assertEqual(declined["properties"]["kind"], "route")
        self.assertEqual(declined["properties"]["ckey"], "w20")
        self.assertNotIn("destination_evidence", declined["properties"])
        decisions = {
            tuple(row["root_relation_ids"]): row
            for row in report["quality"]["promotion_decisions"]
        }
        self.assertEqual(decisions[(700,)]["decision"], "promoted")
        self.assertEqual(decisions[(701,)]["decision"], "declined")
        self.assertEqual(decisions[(701,)]["reason"],
                         "destination-already-claimed")

        def force_second_owner(qa):
            winner = next(
                feature for feature in qa.documents["trails"]["features"]
                if feature["properties"].get("root_relation_ids") == [700])
            second = next(
                feature for feature in qa.documents["trails"]["features"]
                if feature["properties"].get("root_relation_ids") == [701])
            evidence = copy.deepcopy(
                winner["properties"]["destination_evidence"][0])
            evidence.update({
                "selected_endpoint_index": 0,
                "promotion_root_relation_id": 701,
                "endpoint_source_way_id": 20,
                "endpoint_source_node_id": 3,
            })
            second["properties"].update({
                "name": "Shared Summit Trail",
                "kind": "hike",
                "destinations": ["Shared Summit"],
                "destination_evidence": [evidence],
            })
            row = next(
                row for row in qa.documents["assembly_report"]["quality"][
                    "promotion_decisions"]
                if row["root_relation_ids"] == [701])
            identity = {key: row[key] for key in (
                "candidate_index", "candidate_ckey", "root_relation_ids")}
            row.clear()
            row.update({
                **identity,
                "decision": "promoted",
                "poi_osm_node_id": 900,
                "promotion_root_relation_id": 701,
                "endpoint_source_way_id": 20,
                "endpoint_source_node_id": 3,
                "distance_ft": 0.0,
                "selection_rank": 1,
                "skipped_ambiguous_pois": [],
            })
            qa.rewrite("assembly_report")
            qa.rewrite("trails")

        rc, result, *_ = self._run(
            "promotion-duplicate-destination", force_second_owner)
        self.assertEqual(rc, 1)
        self.assertIn(
            "relation candidate[1] declined promotion output contradicts "
            "independent decision", result["errors"])
        self.assertIn(
            "assembly promotion decision ledger is incomplete or inconsistent",
            result["errors"])

    def test_promotion_ledger_emits_exact_decline_reasons(self):
        expected = {
            "promotion-no-authoritative-endpoints":
                "no-authoritative-endpoints",
            "promotion-no-poi": "no-eligible-poi-in-reach",
            "promotion-fused-consumed": "no-eligible-poi-in-reach",
            "multi-root-promotion-ambiguous":
                "ambiguous-endpoint-authority",
        }
        for case, reason in expected.items():
            with self.subTest(case=case):
                rc, result, trails, _removed, report = self._run(case)
                self.assertEqual(rc, 0, result["errors"])
                relation_indexes = {
                    feature["properties"]["quality_candidate_index"]
                    for feature in trails["features"]
                    if feature["properties"].get("source") == "relation"
                }
                rows = report["quality"]["promotion_decisions"]
                self.assertEqual(
                    {row["candidate_index"] for row in rows},
                    relation_indexes)
                self.assertEqual(rows[0]["decision"], "declined")
                self.assertEqual(rows[0]["reason"], reason)

    def test_independent_ledger_rejects_suppression_and_row_tamper(self):
        def suppress(qa, *, rewrite_ledger=False):
            candidate = next(
                feature for feature in qa.documents["trails"]["features"]
                if feature["properties"].get("root_relation_ids") == [700])
            properties = candidate["properties"]
            properties.update({
                "name": "Boundary Access Route",
                "kind": "route",
                "destinations": [],
            })
            properties.pop("destination_evidence", None)
            qa.rewrite("trails")
            if rewrite_ledger:
                row = next(
                    row for row in qa.documents["assembly_report"]["quality"][
                        "promotion_decisions"]
                    if row["root_relation_ids"] == [700])
                identity = {
                    key: row[key] for key in (
                        "candidate_index", "candidate_ckey",
                        "root_relation_ids")
                }
                row.clear()
                row.update({
                    **identity,
                    "decision": "declined",
                    "reason": "no-eligible-poi-in-reach",
                    "skipped_ambiguous_pois": [],
                })
                qa.rewrite("assembly_report")

        def silent_suppression(qa):
            suppress(qa)

        def coherent_suppression(qa):
            suppress(qa, rewrite_ledger=True)

        for mutate, expected_message in (
                (silent_suppression,
                 "relation candidate[0] promotion output contradicts "
                 "independent decision"),
                (coherent_suppression,
                 "assembly promotion decision ledger is incomplete or "
                 "inconsistent")):
            with self.subTest(mutate=mutate.__name__):
                rc, result, *_ = self._run(
                    "promotion-clipped-index-shift", mutate)
                self.assertEqual(rc, 1)
                self.assertIn(expected_message, result["errors"])

        def missing_decline(qa):
            rows = qa.documents["assembly_report"]["quality"][
                "promotion_decisions"]
            rows[:] = [row for row in rows
                       if row["root_relation_ids"] != [700]]
            qa.rewrite("assembly_report")

        def extra_decline(qa):
            rows = qa.documents["assembly_report"]["quality"][
                "promotion_decisions"]
            extra = copy.deepcopy(next(
                row for row in rows if row["root_relation_ids"] == [700]))
            extra.update({
                "candidate_index": 99,
                "candidate_ckey": "w999",
                "root_relation_ids": [999],
            })
            rows.append(extra)
            qa.rewrite("assembly_report")

        def false_decline(qa):
            row = next(
                row for row in qa.documents["assembly_report"]["quality"][
                    "promotion_decisions"]
                if row["root_relation_ids"] == [700])
            row["reason"] = "no-authoritative-endpoints"
            qa.rewrite("assembly_report")

        for mutate in (missing_decline, extra_decline, false_decline):
            with self.subTest(mutate=mutate.__name__):
                rc, result, *_ = self._run("promotion-no-poi", mutate)
                self.assertEqual(rc, 1)
                self.assertIn(
                    "assembly promotion decision ledger is incomplete or "
                    "inconsistent", result["errors"])

        def impossible_hike(qa):
            candidate = next(
                feature for feature in qa.documents["trails"]["features"]
                if feature["properties"].get("root_relation_ids") == [700])
            candidate["properties"].update({
                "name": "Invented Trail", "kind": "hike",
                "destinations": ["Invented"],
            })
            qa.rewrite("trails")

        rc, result, *_ = self._run("promotion-no-poi", impossible_hike)
        self.assertEqual(rc, 1)
        self.assertIn(
            "relation candidate[0] declined promotion output contradicts "
            "independent decision", result["errors"])

    def test_exact_promotion_uses_source_endpoints_not_weld_authority(self):
        rc, result, trails, _removed, _report = self._run(
            "promotion-weld-only")
        self.assertEqual(rc, 0, result["errors"])
        route = next(
            feature for feature in trails["features"]
            if feature["properties"].get("root_relation_ids") == [700])
        self.assertEqual(route["properties"]["kind"], "route")
        self.assertEqual(len(route["properties"]["welds"]), 1)
        self.assertNotIn("destination_evidence", route["properties"])

        rc, result, trails, _removed, _report = self._run(
            "promotion-weld-source")
        self.assertEqual(rc, 0, result["errors"])
        hike = next(
            feature for feature in trails["features"]
            if feature["properties"].get("root_relation_ids") == [700])
        self.assertEqual(hike["properties"]["name"], "Source Summit Trail")
        self.assertEqual(hike["properties"]["kind"], "hike")
        self.assertEqual(len(hike["properties"]["welds"]), 1)
        evidence = hike["properties"]["destination_evidence"][0]
        self.assertEqual(evidence["osm_node_id"], 900)
        self.assertEqual(evidence["promotion_root_relation_id"], 700)
        self.assertEqual(evidence["endpoint_source_way_id"], 10)
        self.assertEqual(evidence["endpoint_source_node_id"], 2)
        self.assertEqual(evidence["selected_endpoint_coordinate"],
                         [10.53, 56.15])

    def test_multi_root_promotion_binds_only_endpoint_supplying_root(self):
        rc, result, trails, _removed, _report = self._run(
            "multi-root-promotion")
        self.assertEqual(rc, 0, result["errors"])
        hike = next(
            feature for feature in trails["features"]
            if feature["properties"].get("root_relation_ids") == [700, 701])
        evidence = hike["properties"]["destination_evidence"][0]
        self.assertEqual(evidence["promotion_root_relation_id"], 701)
        self.assertEqual(evidence["endpoint_source_way_id"], 20)
        self.assertEqual(evidence["endpoint_source_node_id"], 3)

        def synchronized_root_tamper(qa):
            candidate = next(
                feature for feature in qa.documents["trails"]["features"]
                if feature["properties"].get("root_relation_ids") == [700, 701])
            row = candidate["properties"]["destination_evidence"][0]
            row["promotion_root_relation_id"] = 700
            row["endpoint_source_way_id"] = 10
            row["endpoint_source_node_id"] = 2
            qa.rewrite("trails")

        rc, result, *_ = self._run(
            "multi-root-promotion", synchronized_root_tamper)
        self.assertEqual(rc, 1)
        self.assertIn(
            "relation candidate[0] promoted destination root/source identity "
            "contradicts authoritative endpoint", result["errors"])

        rc, result, trails, _removed, _report = self._run(
            "multi-root-promotion-ambiguous")
        self.assertEqual(rc, 0, result["errors"])
        route = next(
            feature for feature in trails["features"]
            if feature["properties"].get("root_relation_ids") == [700, 701])
        self.assertEqual(route["properties"]["kind"], "route")
        self.assertNotIn("destination_evidence", route["properties"])

        def force_ambiguous_promotion(qa):
            candidate = next(
                feature for feature in qa.documents["trails"]["features"]
                if feature["properties"].get("root_relation_ids") == [700, 701])
            properties = candidate["properties"]
            properties.update({
                "name": "Multi Root Summit Trail",
                "kind": "hike",
                "destinations": ["Multi Root Summit"],
                "destination_evidence": [{
                    "osm_node_id": 900,
                    "name": "Multi Root Summit",
                    "tags": {"name": "Multi Root Summit", "natural": "peak"},
                    "eligibility_class": "natural=peak",
                    "coordinate": [10.55, 56.15],
                    "selected_endpoint_index": 1,
                    "selected_endpoint_coordinate": [10.55, 56.15],
                    "distance_ft": 0.0,
                    "reach_limit_ft": 250,
                    "selection_rank": 1,
                    "promotion_root_relation_id": 700,
                    "endpoint_source_way_id": 10,
                    "endpoint_source_node_id": 3,
                }],
            })
            qa.rewrite("trails")

        rc, result, *_ = self._run(
            "multi-root-promotion-ambiguous", force_ambiguous_promotion)
        self.assertEqual(rc, 1)
        self.assertIn(
            "relation candidate[0] promoted destination endpoint has "
            "ambiguous root authority", result["errors"])

    def test_promoted_destination_independent_replay_rejects_every_attack(self):
        def promoted(qa):
            return next(
                feature for feature in qa.documents["trails"]["features"]
                if feature["properties"].get("root_relation_ids") == [700])

        def evidence(qa):
            return promoted(qa)["properties"]["destination_evidence"][0]

        def rewrite_trail(qa):
            qa.rewrite("trails")

        def unknown_poi(qa):
            evidence(qa)["osm_node_id"] = 999
            rewrite_trail(qa)

        def wrong_tags_class(qa):
            evidence(qa)["tags"] = {
                "name": "Mols Summit", "amenity": "parking"}
            evidence(qa)["eligibility_class"] = "amenity=parking"
            rewrite_trail(qa)

        def wrong_name(qa):
            evidence(qa)["name"] = "Forged Summit"
            rewrite_trail(qa)

        def wrong_coordinate(qa):
            evidence(qa)["coordinate"] = [10.551, 56.15]
            rewrite_trail(qa)

        def wrong_endpoint(qa):
            evidence(qa)["selected_endpoint_index"] = 0
            evidence(qa)["selected_endpoint_coordinate"] = [10.53, 56.15]
            rewrite_trail(qa)

        def wrong_distance(qa):
            evidence(qa)["distance_ft"] = 1.0
            rewrite_trail(qa)

        def wrong_rank_and_limit(qa):
            evidence(qa)["selection_rank"] = 2
            evidence(qa)["reach_limit_ft"] = 251
            rewrite_trail(qa)

        def move_destination(qa, coordinate):
            tags = {"name": "Mols Summit", "natural": "peak"}
            qa.set_topology_destination(900, tags=tags, coordinate=coordinate)
            row = evidence(qa)
            row["coordinate"] = list(coordinate)
            row["distance_ft"] = round(
                validator.assembly_model.haversine_mi(
                    (10.55, 56.15), tuple(coordinate)) * 5280.0, 6)
            rewrite_trail(qa)

        def over_limit(qa):
            move_destination(qa, [10.552, 56.15])

        def interior_only(qa):
            move_destination(qa, [10.53, 56.15])

        def farther_poi(qa):
            move_destination(qa, [10.5503, 56.15])
            qa.set_topology_destination(
                901, tags={"name": "Nearer View", "tourism": "viewpoint"},
                coordinate=[10.55, 56.15])

        def tie_break(qa):
            qa.set_topology_destination(
                800, tags={"name": "Lower ID View", "tourism": "viewpoint"},
                coordinate=[10.55, 56.15])

        def ineligible_trusted_tags(qa):
            qa.set_topology_destination(
                900, tags={"name": "Mols Summit", "amenity": "parking"},
                coordinate=[10.55, 56.15])

        def omitted_nearer_raw_poi(qa):
            qa.documents["raw"]["features"].append({
                "type": "Feature",
                "id": "n800",
                "properties": {
                    "name": "Omitted Nearer View", "tourism": "viewpoint",
                },
                "geometry": {
                    "type": "Point", "coordinates": [10.55, 56.15],
                },
            })
            qa.documents["aoi_scope_receipt"]["compact_output"][
                "object_counts"]["nodes"] += 1
            qa.rewrite("raw")
            qa.rewrite("aoi_scope_receipt")
            qa._refresh_scope_trust()

        def coherent_forgery(qa):
            def replace(value):
                if isinstance(value, dict):
                    return {key: replace(item) for key, item in value.items()}
                if isinstance(value, list):
                    return [replace(item) for item in value]
                if value == "Mols Summit Trail":
                    return "Forged Summit Trail"
                if value == "Mols Summit":
                    return "Forged Summit"
                return value

            qa.documents["trails"] = replace(qa.documents["trails"])
            qa.documents["removed"] = replace(qa.documents["removed"])
            qa.documents["assembly_report"] = replace(
                qa.documents["assembly_report"])
            qa.rewrite("assembly_report")
            qa.rewrite("removed")
            qa.rewrite("trails")

        cases = (
            (unknown_poi,
             "relation candidate[0] promoted destination OSM node is not trusted"),
            (wrong_tags_class,
             "relation candidate[0] promoted destination identity contradicts "
             "trusted AOI topology"),
            (wrong_name,
             "relation candidate[0] promoted destination identity contradicts "
             "trusted AOI topology"),
            (wrong_coordinate,
             "relation candidate[0] promoted destination identity contradicts "
             "trusted AOI topology"),
            (wrong_endpoint,
             "relation candidate[0] promoted destination selected endpoint is "
             "not an authoritative source endpoint"),
            (wrong_distance,
             "relation candidate[0] promoted destination measured distance is "
             "inconsistent"),
            (wrong_rank_and_limit,
             "relation candidate[0] promoted destination rank/reach evidence "
             "is invalid"),
            (over_limit,
             "relation candidate[0] promoted hike has no trusted destination "
             "within endpoint reach"),
            (interior_only,
             "relation candidate[0] promoted hike has no trusted destination "
             "within endpoint reach"),
            (farther_poi,
             "relation candidate[0] promoted destination selection violates "
             "distance/OSM-ID rank"),
            (tie_break,
             "relation candidate[0] promoted destination selection violates "
             "distance/OSM-ID rank"),
            (ineligible_trusted_tags,
             "raw-way-topology.destination_pois[0] eligibility class "
             "contradicts complete tags"),
            (omitted_nearer_raw_poi,
             "raw destination point n800 is missing from AOI topology ledger"),
            (coherent_forgery,
             "relation candidate[0] promoted destination identity contradicts "
             "trusted AOI topology"),
        )
        for mutate, expected_message in cases:
            with self.subTest(attack=mutate.__name__):
                rc, result, *_ = self._run("promoted-hike", mutate)
                self.assertEqual(rc, 1)
                self.assertIn(expected_message, result["errors"])

    def test_promoted_target_fallbacks_and_coalesced_terminal_identity(self):
        rc, result, trails, removed, report = self._run(
            "promoted-hike-outside")
        self.assertEqual(rc, 0, result["errors"])
        self.assertFalse(any(
            feature["properties"].get("removed_category") ==
            validator.assembly_model.GEOMETRY_DUPLICATE_ABSORBED_CATEGORY
            for feature in removed["features"]))
        self.assertTrue(any(
            feature["properties"].get("removed_category") ==
            "outside-exact-area" for feature in removed["features"]))
        self.assertEqual(
            report["quality"]["terminal_absorption_unresolved"], [])

        rc, result, trails, removed, report = self._run(
            "promoted-target-quality-removed")
        self.assertEqual(rc, 0, result["errors"])
        self.assertTrue(any(
            feature["properties"].get("root_relation_ids") == [700]
            for feature in trails["features"]))
        self.assertTrue(any(
            feature["properties"].get("removed_category") ==
            "dk-unqualified-road-track" for feature in removed["features"]))
        self.assertFalse(any(
            feature["properties"].get("kind") == "hike"
            for feature in trails["features"]))
        self.assertEqual(
            report["quality"]["terminal_absorption_unresolved_count"], 0)
        self.assertEqual(
            report["quality"]["terminal_absorption_unresolved"], [])

        rc, result, trails, removed, _ = self._run("winner-coalesced")
        self.assertEqual(rc, 0, result["errors"])
        survivor = next(
            feature for feature in trails["features"]
            if feature["properties"].get("root_relation_ids") == [701, 702])
        self.assertEqual(survivor["properties"]["ckey"], "w20-30")
        self.assertEqual(survivor["properties"]["name"], "Mols Summit Trail")
        row = next(
            feature for feature in removed["features"]
            if feature["properties"].get("root_relation_ids") == [700])
        claim = row["properties"]["drop_evidence"]["survivor"]
        self.assertEqual(claim, {
            "ckey": "w20-30", "name": "Mols Summit Trail",
            "source": "relation",
            "geometry_sha256": validator._publisher_json_sha256(
                survivor["geometry"]),
        })
        self.assertFalse(any(
            feature["properties"].get("ckey") == "w20"
            for feature in trails["features"]))
        self.assertTrue(shape(row["geometry"]).difference(
            shape(survivor["geometry"])).is_empty)

    def test_fully_outside_relation_accepts_every_reachable_curation_rule(self):
        categories = (
            "access", "closed", "grid-address", "min-length", "motorized",
            "named-road", "non-trail-feature", "nonhiking-route", "off-trail",
            "road-code", "short-name", "thru-hike", "utility",
        )
        for category in categories:
            with self.subTest(category=category):
                rc, result, trails, removed, report = self._run(
                    f"outside-curation:{category}")
                self.assertEqual(rc, 0, result["errors"])
                row = next(
                    feature for feature in removed["features"]
                    if (feature.get("properties") or {}).get(
                        "root_relation_ids") == [700])
                properties = row["properties"]
                self.assertEqual(properties["removed_category"], category)
                self.assertEqual(properties["drop_evidence"], {
                    "kind": "curation", "category": category,
                    "min_length_mi": validator.PILOT_MIN_LENGTH_MI,
                })
                self.assertEqual(properties["source_geometry_way_ids"], [10])
                self.assertEqual(properties["rendered_source_way_ids"], [])
                self.assertEqual([record["way_id"]
                                  for record in properties["source_ways"]], [10])
                self.assertFalse(next(
                    audit for audit in report["quality"][
                        "relation_member_audit"]
                    if audit["relation_id"] == 700)["emitted_in_exact_area"])
                self.assertFalse(any(
                    feature["properties"].get("root_relation_ids") == [700]
                    for feature in trails["features"]))

        def incorrect_category(qa):
            row = next(
                feature for feature in qa.documents["removed"]["features"]
                if (feature.get("properties") or {}).get(
                    "root_relation_ids") == [700])
            row["properties"].update({
                "removed_category": "closed",
                "removed_reason": (
                    "Marked closed in OSM — the name itself says CLOSED "
                    "(e.g. 'CLOSED - old Pyramid Trail')."),
                "drop_evidence": {
                    "kind": "curation", "category": "closed",
                    "min_length_mi": validator.PILOT_MIN_LENGTH_MI,
                },
            })
            qa.rewrite("removed")

        rc, result, *_ = self._run(
            "outside-curation:access", incorrect_category)
        self.assertEqual(rc, 1)
        self.assertTrue(any(
            "not reproducible for root r700" in error
            for error in result["errors"]), result["errors"])

    def test_drop_requires_complete_preclip_evidence_in_every_field(self):
        rc, result, _, removed, _ = self._run("partial-preclip-evidence")
        self.assertEqual(rc, 0, result["errors"])
        row = next(
            feature for feature in removed["features"]
            if (feature.get("properties") or {}).get(
                "root_relation_ids") == [700])
        properties = row["properties"]
        self.assertEqual(properties["removed_category"], "min-inside-mi")
        self.assertEqual(properties["member_ways"], [10, 11])
        self.assertEqual(properties["source_geometry_way_ids"], [10, 11])
        self.assertEqual([record["way_id"]
                          for record in properties["source_ways"]], [10, 11])
        self.assertEqual(properties["direct_relation_way_ids"],
                         {"700": [10, 11]})
        self.assertEqual(properties["rendered_source_way_ids"], [10])
        inside_mi = validator.assembly_model.line_mi([
            tuple(point) for point in properties["source_ways"][0]["coordinates"]
        ])
        outside_mi = validator.assembly_model.line_mi([
            tuple(point) for point in properties["source_ways"][1]["coordinates"]
        ])
        self.assertAlmostEqual(inside_mi, 0.038, delta=0.002)
        self.assertGreater(outside_mi, 3.8)

        def relation_row(qa):
            return next(
                feature for feature in qa.documents["removed"]["features"]
                if (feature.get("properties") or {}).get(
                    "root_relation_ids") == [700])

        def omit_member_way(qa):
            props = relation_row(qa)["properties"]
            props["member_ways"] = [10]
            props["ckey"] = "w10"
            qa.rewrite("removed")

        def omit_source_geometry_way(qa):
            relation_row(qa)["properties"]["source_geometry_way_ids"] = [10]
            qa.rewrite("removed")

        def omit_source_record(qa):
            relation_row(qa)["properties"]["source_ways"] = [
                relation_row(qa)["properties"]["source_ways"][0]]
            qa.rewrite("removed")

        def omit_direct_member(qa):
            relation_row(qa)["properties"]["direct_relation_way_ids"] = {
                "700": [10]}
            qa.rewrite("removed")

        def omit_preclip_geometry(qa):
            row = relation_row(qa)
            coordinates = row["properties"]["source_ways"][0]["coordinates"]
            row["geometry"] = {
                "type": "MultiLineString", "coordinates": [coordinates]}
            qa.rewrite("removed")

        attacks = {
            omit_member_way: "complete authoritative preclip identity",
            omit_source_geometry_way: "no output candidate or verified producer drop",
            omit_source_record: "source-way order contradicts",
            omit_direct_member: "direct relation membership contradicts",
            omit_preclip_geometry: "geometry contradicts raw source ways",
        }
        for mutate, expected_error in attacks.items():
            with self.subTest(attack=mutate.__name__):
                rc, result, *_ = self._run(
                    "partial-preclip-evidence", mutate)
                self.assertEqual(rc, 1)
                self.assertTrue(any(
                    expected_error in error for error in result["errors"]),
                    result["errors"])

        def forged_min_length(qa):
            row = relation_row(qa)
            props = row["properties"]
            inside_record = props["source_ways"][0]
            coordinates = inside_record["coordinates"]
            miles = round(validator.assembly_model.line_mi(
                [tuple(point) for point in coordinates]), 3)
            category, reason = validator.assembly_model._removal_verdict(
                SimpleNamespace(
                    name=props["name"], tags={}, source="relation",
                    length_mi=miles),
                validator.PILOT_MIN_LENGTH_MI, "dk")
            self.assertEqual(category, "min-length")
            props.update({
                "length_mi": miles,
                "member_ways": [10],
                "ckey": "w10",
                "source_geometry_way_ids": [10],
                "rendered_source_way_ids": [10],
                "source_ways": [inside_record],
                "direct_relation_way_ids": {"700": [10]},
                "removed_category": category,
                "removed_reason": reason,
                "drop_evidence": {
                    "kind": "curation", "category": category,
                    "min_length_mi": validator.PILOT_MIN_LENGTH_MI,
                },
            })
            row["geometry"] = {
                "type": "MultiLineString", "coordinates": [coordinates]}
            qa.rewrite("removed")

        rc, result, *_ = self._run(
            "partial-preclip-evidence", forged_min_length)
        self.assertEqual(rc, 1)
        self.assertTrue(any(
            "no output candidate or verified producer drop" in error
            or "complete authoritative preclip" in error
            for error in result["errors"]), result["errors"])

    def test_dk_localized_relation_name_is_sorted_independent_of_tag_order(self):
        for case in ("localized-order-de-da", "localized-order-da-de"):
            with self.subTest(case=case):
                rc, result, trails, removed, report = self._run(case)
                self.assertEqual(rc, 0, result["errors"])
                route = next(
                    feature for feature in trails["features"]
                    if feature["properties"].get("root_relation_ids") == [700])
                self.assertEqual(route["properties"]["name"], "Dansk Sti")
                audit = next(
                    row for row in report["quality"]["relation_member_audit"]
                    if row["relation_id"] == 700)
                self.assertEqual(audit["name"], "Dansk Sti")
                self.assertFalse(any(
                    (feature.get("properties") or {}).get(
                        "root_relation_ids") == [700]
                    for feature in removed["features"]))

    def test_multi_root_drop_requires_each_root_to_justify_category(self):
        rc, result, _, removed, _ = self._run("multi-root-access-one")
        self.assertEqual(rc, 1)
        row = next(
            feature for feature in removed["features"]
            if (feature.get("properties") or {}).get(
                "root_relation_ids") == [700, 701])
        self.assertEqual(row["properties"]["removed_category"], "access")
        self.assertTrue(any(
            "not reproducible for root r701" in error
            for error in result["errors"]), result["errors"])

        rc, result, _, removed, _ = self._run("multi-root-access-both")
        self.assertEqual(rc, 0, result["errors"])
        row = next(
            feature for feature in removed["features"]
            if (feature.get("properties") or {}).get(
                "root_relation_ids") == [700, 701])
        self.assertEqual(row["properties"]["removed_category"], "access")

    def test_real_boundary_split_relation_round_trip_is_accepted(self):
        rc, result, trails, removed, report = self._run("boundary")
        self.assertEqual(rc, 0, result["errors"])
        self.assertEqual(removed["features"], [])
        properties = trails["features"][0]["properties"]
        self.assertEqual(properties["quality_disposition"],
                         "boundary-induced-split")
        self.assertEqual(properties["connectivity"]["postclip_components"], 2)
        self.assertEqual(report["quality"]["validation_failures"], [])

    def test_sub_rounding_boundary_split_round_trip_is_accepted(self):
        rc, result, trails, _, report = self._run("sub-rounding-boundary")
        self.assertEqual(rc, 0, result["errors"])
        properties = trails["features"][0]["properties"]
        self.assertTrue(properties["clipped"])
        self.assertEqual(properties["length_mi"], properties["full_length_mi"])
        self.assertEqual(properties["quality_disposition"],
                         "boundary-induced-split")
        self.assertEqual(report["quality"]["validation_failures"], [])

    def test_real_exact_area_producer_keeps_but_rejects_unresolved_relation(self):
        rc, result, trails, removed, report = self._run("disconnected")
        self.assertEqual(rc, 1)
        self.assertEqual(len(trails["features"]), 2)
        self.assertEqual(removed["features"], [])
        self.assertEqual(report["quality"]["signed_relation_unresolved_count"], 1)
        self.assertTrue(any("unresolved-relation-gap" in error
                            for error in result["errors"]))


if __name__ == "__main__":
    unittest.main()
