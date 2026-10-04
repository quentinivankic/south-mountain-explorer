"""Synthetic and producer-round-trip tests for the Mols pilot validator."""
import copy
import importlib.util
import json
import math
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
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
                    source_sha256="b" * 64, source_bytes=123):
    direct = direct or {}
    relation_tags = relation_tags or {}
    tags_by_way = {record["way_id"]: dict(record["tags"])
                   for record in records}
    relations = []
    for relation_id, way_ids in sorted(direct.items()):
        members = [
            {"sequence": index, "type": "way", "ref": way_id, "role": ""}
            for index, way_id in enumerate(way_ids)
        ]
        relations.append({
            "relation_id": relation_id,
            "accepted_hiking_route": True,
            "tags": dict(relation_tags.get(relation_id, {
                "name": "Mols Trail", "route": "hiking", "type": "route",
            })),
            "members": members,
            "direct_way_ids": list(way_ids),
            "direct_ways": [{
                "way_id": way_id,
                "present": way_id in tags_by_way,
                "tags": tags_by_way.get(way_id),
            } for way_id in way_ids],
        })
    missing = sorted({
        way_id for way_ids in direct.values() for way_id in way_ids
        if way_id not in tags_by_way
    })
    return {
        "schema_version": 1,
        "source": {"kind": "runner-local-osm-pbf",
                   "sha256": source_sha256, "bytes": source_bytes},
        "attribution": validator.ATTRIBUTION,
        "accepted_route_values": ["foot", "hiking", "running", "walking"],
        "relation_count": len(relations),
        "accepted_relation_ids": sorted(direct),
        "missing_direct_way_ids": missing,
        "relations": relations,
    }


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


class _QA:
    def __init__(self, root):
        self.root = root
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
        relation_graph = _relation_graph()
        relation_seal = {
            "path": validator.RELATION_MEMBERS_RELATIVE,
            "sha256": validator._canonical_json_sha256(relation_graph),
            "source_pbf_sha256": relation_graph["source"]["sha256"],
        }
        self.documents = {
            "manifest": {
                "schema_version": 1,
                "branch_ref": "chat/denmark-trails",
                "source_sha": "a" * 40,
                "inputs": validator._expected_inputs(RELATION_ID),
                "visual_review_url": validator.VISUAL_REVIEW_URL,
                "attribution": validator.ATTRIBUTION,
                "relation_members_evidence": relation_seal,
            },
            "relation_members": relation_graph,
            "raw": {
                "type": "FeatureCollection",
                "features": [{
                    "type": "Feature",
                    "id": "w10",
                    "properties": {"name": "Mols Trail", "highway": "path"},
                    "geometry": {"type": "LineString", "coordinates": coordinates},
                }],
            },
            "trails": {"type": "FeatureCollection", "features": [self.trail]},
            "removed": {"type": "FeatureCollection", "features": []},
            "ingest_dropped": {"type": "FeatureCollection", "features": []},
            "areas": {
                "type": "FeatureCollection",
                "features": [{
                    "type": "Feature",
                    "properties": {"name": validator.AREA_NAME},
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
                    "sha256": relation_graph["source"]["sha256"],
                    "bytes": relation_graph["source"]["bytes"],
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
                "clip_applied": True,
                "pre_clip_trail_count": 1,
                "post_clip_trail_count": 1,
                "assembled_trail_count": 1,
                "coverage": {
                    "raw_trailish_ways": 2,
                    "named_trailish_ways": 1,
                    "hiking_route_relations": 0,
                    "route_relations_total": 0,
                    "destination_pois": 0,
                },
                "quality": {
                    "schema_version": 5,
                    "enabled": True,
                    "region": "dk",
                    "area": validator.AREA_NAME,
                    "connectivity_authority":
                    "shared-osm-node-or-exact-boundary-clip",
                    "overlap_threshold": 0.9,
                    "candidate_count": 1,
                    "kept_count": 1,
                    "removed_count": 0,
                    "removed_counts": {},
                    "signed_relation_unresolved_count": 0,
                    "signed_relation_unresolved_miles": 0,
                    "signed_relation_unresolved": [],
                    "relation_member_audit": [],
                    "relation_member_unresolved_count": 0,
                    "relation_member_unresolved": [],
                    "restored_road_limits": {
                        "max_relation_share": 0.10,
                        "max_relation_miles": 1.0,
                        "max_aggregate_share": 0.10,
                    },
                    "restored_road_relations": [],
                    "restored_road_aggregate": {
                        "restored_way_count": 0,
                        "restored_miles": 0.0,
                        "total_relation_miles": 0.0,
                        "restored_share": 0.0,
                    },
                    "overlap_evidence": [],
                    "remaining_overlaps": [],
                    "validation_failures": [],
                },
            },
            "publish_report": {
                "schema_version": 1,
                "state": validator.STATE,
                "dry_run": True,
                "write_mode": "dry-run",
                "canonical_write": False,
                "index_area_count": 1,
                "validated_areas": [copy.deepcopy(area_record)],
                "published_areas": [copy.deepcopy(area_record)],
                "skipped_areas": [],
                "validation_failures": [],
                "routes": {"kept": 0, "dropped": 0},
            },
        }
        for label, relative in validator.REQUIRED_JSON_FILES.items():
            _write(root / relative, self.documents[label])
        _write(root / "README.md",
               f"Visual review: {validator.VISUAL_REVIEW_URL}\n\n"
               f"Attribution: {validator.ATTRIBUTION}\n")
        _write(root / "discovery/discovery.log", "discovery completed\n")
        _write(root / "reports/publish.log", "publisher completed\n")
        _write(root / "viewer/index.html", "<!doctype html><title>QA</title>\n")
        _write(root / "viewer/serve.py", "print('serve')\n")

    def rewrite(self, label):
        _write(self.root / validator.REQUIRED_JSON_FILES[label], self.documents[label])

    def set_relation_authority(self, direct, records, *, emitted=True,
                               update_quality=True, relation_tags=None,
                               source_sha256="b" * 64, source_bytes=123):
        graph = _relation_graph(
            direct, records, relation_tags=relation_tags,
            source_sha256=source_sha256, source_bytes=source_bytes)
        self.documents["relation_members"] = graph
        self.documents["manifest"]["relation_members_evidence"] = {
            "path": validator.RELATION_MEMBERS_RELATIVE,
            "sha256": validator._canonical_json_sha256(graph),
            "source_pbf_sha256": graph["source"]["sha256"],
        }
        if not update_quality:
            self.rewrite("relation_members")
            self.rewrite("manifest")
            return
        authority = {
            "relations": {
                record["relation_id"]: {
                    **record,
                    "direct_ways": {
                        way["way_id"]: {
                            "present": way["present"], "tags": way["tags"],
                        }
                        for way in record["direct_ways"]
                    },
                }
                for record in graph["relations"]
            },
            "accepted_relation_ids": graph["accepted_relation_ids"],
        }
        audits = []
        unresolved = []
        for relation_id in graph["accepted_relation_ids"]:
            audit = validator._expected_relation_audit(relation_id, authority)
            audit["assembly_status"] = "emitted" if emitted else "entirely-filtered"
            audit["emitted_in_exact_area"] = emitted
            reasons, record = validator._audit_resolution(audit)
            audit["review_status"] = "unresolved" if reasons else "accepted"
            audit["unresolved_reasons"] = reasons
            audits.append(audit)
            if record is not None:
                unresolved.append(record)
        quality = self.documents["assembly_report"]["quality"]
        quality["relation_member_audit"] = audits
        quality["relation_member_unresolved_count"] = len(unresolved)
        quality["relation_member_unresolved"] = unresolved
        quality["validation_failures"] = [
            "relation-member-audit unresolved: "
            f"relation {record['relation_id']!r} {record['reasons']!r}"
            for record in unresolved
        ]
        self.rewrite("relation_members")
        self.rewrite("manifest")

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
            "kind": "route",
            "member_ways": way_ids,
            "ckey": "w" + "-".join(str(value) for value in way_ids),
            "relation_ids": relation_ids,
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
        self.documents["raw"]["features"] = [{
            "type": "Feature",
            "id": f"w{record['way_id']}",
            "properties": copy.deepcopy(record["tags"]),
            "geometry": {
                "type": "LineString",
                "coordinates": copy.deepcopy(record["coordinates"]),
            },
        } for record in records]
        boundary = shape(self.documents["areas"]["features"][0]["geometry"])
        measurements = validator._restored_measurements(
            trail, [{**record, "_raw_bound": True} for record in records],
            restored_ids, direct, boundary)
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
        self.rewrite("raw")
        self.rewrite("trails")
        self.rewrite("assembly_report")

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
        self.documents["trails"]["features"].append(second)
        report = self.documents["assembly_report"]
        report["pre_clip_trail_count"] = 2
        report["post_clip_trail_count"] = 2
        report["assembled_trail_count"] = 2
        quality = report["quality"]
        quality["candidate_count"] = 2
        quality["kept_count"] = 2
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
    def _run(self, mutate=None, *, remove=None, malformed=None, optional=None):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            qa = _QA(root)
            if mutate:
                mutate(qa)
            if remove:
                (root / remove).unlink()
            if malformed:
                (root / malformed).write_text("{not-json", encoding="utf-8")
            if optional is not None:
                _write(root / validator.OPTIONAL_DROPPED_ROUTES, optional)
            result_path = root / "validation/final.json"
            rc = validator.main([
                "final",
                "--qa-dir", str(root),
                "--relation-members", str(
                    root / validator.RELATION_MEMBERS_RELATIVE),
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
        self.assertEqual(result["assembled_trail_count"], 1)

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
            graph = qa.documents["relation_members"]
            parent = graph["relations"][0]
            parent["members"].append({
                "sequence": 1, "type": "relation", "ref": 701, "role": "",
            })
            qa.documents["manifest"]["relation_members_evidence"]["sha256"] = \
                validator._canonical_json_sha256(graph)
            properties = qa.documents["trails"]["features"][0]["properties"]
            properties["direct_relation_way_ids"] = {
                700: [10, 11], 701: [11],
            }
            properties["source_ways"][1]["direct_relation_ids"] = [700, 701]
            qa.rewrite("relation_members")
            qa.rewrite("manifest")
            qa.rewrite("trails")

        rc, result = self._run(mutate)
        self.assertEqual(rc, 1)
        self.assertTrue(any(
            "direct relation membership contradicts independent graph" in error
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
            "missing-source-way unavailable evidence w99" in error
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
            graph = qa.documents["relation_members"]
            graph["source"]["sha256"] = "c" * 64
            qa.documents["manifest"]["relation_members_evidence"].update({
                "sha256": validator._canonical_json_sha256(graph),
                "source_pbf_sha256": "c" * 64,
            })
            qa.rewrite("relation_members")
            qa.rewrite("manifest")

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

        for mutate, fragment in ((coordinates, "contradicts raw way geometry"),
                                 (tags, "contradicts raw way tags")):
            with self.subTest(mutate=mutate.__name__):
                rc, result = self._run(mutate)
                self.assertEqual(rc, 1)
                self.assertTrue(any(fragment in error for error in result["errors"]))

    def test_missing_raw_way_reports_unavailable_source_evidence(self):
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
        self.assertTrue(any("missing-source-way unavailable raw evidence w10" in error
                            for error in result["errors"]))
        self.assertFalse(any("source-way records disagree" in error
                             for error in result["errors"]))

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
            {"highway": "service", "foot": foot, **extra}
            for foot in ("yes", "designated", "permissive")
            for extra in ({}, {"service": "alley"})
        ] + [{
            "highway": " SERVICE ", "service": " ALLEY ",
            "foot": " Permissive ",
        }]
        denied = [
            {"highway": "service"},
            {"highway": "service", "foot": "destination"},
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

    def test_validator_enforces_restored_share_below_equal_and_above(self):
        for share, accepted in ((0.099, True), (0.100, True), (0.101, False)):
            with self.subTest(share=share):
                def mutate(qa, share=share):
                    self._set_restored_road_lengths(qa, share, 1.0)

                rc, result = self._run(mutate)
                self.assertEqual(rc == 0, accepted, result["errors"])
                self.assertEqual(
                    any("restored-road share limit" in error
                        or "aggregate share exceeds" in error
                        for error in result["errors"]),
                    not accepted,
                )

    def test_validator_enforces_restored_miles_below_equal_and_above(self):
        for miles, accepted in ((0.999, True), (1.000, True), (1.001, False)):
            with self.subTest(miles=miles):
                def mutate(qa, miles=miles):
                    self._set_restored_road_lengths(qa, miles, 12.0)

                rc, result = self._run(mutate)
                self.assertEqual(rc == 0, accepted, result["errors"])
                self.assertEqual(any("restored-road mile limit" in error
                                     for error in result["errors"]),
                                 not accepted)

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
                [700, 701])
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
            qa.documents["areas"]["features"][0]["geometry"] = mapping(boundary)
            record = _source_record(
                10, [1, 2], source, {"highway": "path"}, direct=[700])
            qa.set_relation([record], [rendered["coordinates"]], clipped=True)
            measurement = qa.documents["assembly_report"]["quality"][
                "restored_road_relations"][0]
            expected = validator._haversine_miles((10.5, 56.15), (10.6, 56.15))
            self.assertAlmostEqual(measurement["total_relation_miles"],
                                   expected, places=6)
            qa.rewrite("areas")

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
            qa.documents["areas"]["features"][0]["geometry"] = mapping(boundary)
            record = _source_record(10, [1, 2, 3, 4], source_coordinates,
                                    {"highway": "path"}, direct=[700])
            qa.set_relation([record], rendered["coordinates"], clipped=True)
            properties = qa.documents["trails"]["features"][0]["properties"]
            properties["quality_disposition"] = "boundary-induced-split"
            properties["connectivity"].update({
                "accepted_reasons": ["shared-osm-node", "boundary-induced-split"],
                "postclip_components": 2,
            })
            qa.rewrite("areas")
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
            qa.documents["areas"]["features"][0]["geometry"] = mapping(boundary)
            qa.documents["raw"]["features"][0].update({
                "properties": copy.deepcopy(record["tags"]),
                "geometry": {"type": "LineString",
                             "coordinates": source_coordinates},
            })
            qa.documents["assembly_report"]["quality"]["validation_failures"] = [
                "preserved-other-needs-review: 'Boundary Cycleway'",
            ]
            qa.rewrite("areas")
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
                "quality_candidate_index": 1,
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
            qa.documents["removed"]["features"] = [removed]
            quality = qa.documents["assembly_report"]["quality"]
            quality.update({
                "candidate_count": 2,
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
            qa.rewrite("raw")
            qa.rewrite("removed")
            qa.rewrite("assembly_report")

        rc, result = self._run(valid_removed)
        self.assertEqual(rc, 0, result["errors"])

        def destination_is_not_walking(qa):
            valid_removed(qa)
            qa.documents["removed"]["features"][0]["properties"]["source_ways"][0][
                "tags"]["foot"] = "destination"
            qa.documents["raw"]["features"][-1]["properties"]["foot"] = "destination"
            qa.rewrite("raw")
            qa.rewrite("removed")

        rc, result = self._run(destination_is_not_walking)
        self.assertEqual(rc, 0, result["errors"])

        def falsified_explicit(qa):
            valid_removed(qa)
            feature = qa.documents["removed"]["features"][0]
            feature["properties"]["source_ways"][0]["tags"]["foot"] = "yes"
            qa.documents["raw"]["features"][-1]["properties"]["foot"] = "yes"
            qa.rewrite("raw")
            qa.rewrite("removed")

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

    def test_malformed_and_each_missing_mandatory_artifact_are_rejected(self):
        self.assert_rejected(malformed="reports/assembly.json")
        required = [
            *validator.REQUIRED_JSON_FILES.values(),
            *validator.REQUIRED_TEXT_FILES.values(),
        ]
        for relative in required:
            with self.subTest(relative=relative):
                self.assert_rejected(remove=relative)


@unittest.skipUnless(HAVE_SHAPELY, "shapely not installed")
class ProducerValidatorRoundTrip(unittest.TestCase):
    def _run(self, case):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            qa = _QA(root)
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
            if case == "superroute-private":
                relations = {
                    700: {
                        "tags": {"type": "route", "route": "hiking",
                                 "name": "Signed Route"},
                        "members": [("r", 701, "")],
                    },
                    701: {
                        "tags": {"type": "route", "route": "hiking",
                                 "name": "Signed Route"},
                        "members": [("w", 10, ""), ("w", 11, "")],
                    },
                }
            else:
                relations = {700: {
                    "tags": {"type": "route", "route": "hiking",
                             "name": "Signed Route"},
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
            with (
                patch.object(producer, "read_pbf",
                             return_value=(nodes, ways, relations, [])),
                patch.object(area_module, "assemble_areas", return_value=[boundary]),
            ):
                producer_rc = producer.main([
                    "--in", str(input_pbf),
                    "--out", str(output),
                    "--only-area", validator.AREA_NAME,
                    "--require-exact-area",
                    "--expected-area-relation-id", str(RELATION_ID),
                    "--report-json", str(assembly_report),
                    "--region", "dk",
                ])
            self.assertEqual(producer_rc, 0)
            trails = json.loads(output.read_text())
            removed = json.loads((root / "data/aoi/mols-bjerge.removed.geojson").read_text())
            report = json.loads(assembly_report.read_text())
            count = len(trails["features"])
            miles = round(sum(feature["properties"]["length_mi"]
                              for feature in trails["features"]), 1)
            publication = qa.documents["publish_report"]
            for key in ("validated_areas", "published_areas"):
                publication[key][0]["trail_count"] = count
                publication[key][0]["total_miles"] = miles
            qa.rewrite("publish_report")
            _write(root / "data/aoi/mols-bjerge.raw.geojson", {
                "type": "FeatureCollection",
                "features": [{
                    "type": "Feature",
                    "id": f"w{way_id}",
                    "properties": way["tags"],
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [nodes[node] for node in way["nodes"]],
                    },
                } for way_id, way in ways.items()],
            })
            graph_records = [
                _source_record(
                    way_id, way["nodes"],
                    [nodes[node] for node in way["nodes"]], way["tags"])
                for way_id, way in ways.items()
            ]
            graph_direct = {
                relation_id: list(dict.fromkeys(
                    ref for member_type, ref, _role in relation["members"]
                    if member_type == "w"))
                for relation_id, relation in relations.items()
            }
            qa.set_relation_authority(
                graph_direct, graph_records, update_quality=False,
                relation_tags={relation_id: relation["tags"]
                               for relation_id, relation in relations.items()},
                source_sha256=report["input_pbf"]["sha256"],
                source_bytes=report["input_pbf"]["bytes"])
            if case == "superroute-private":
                graph = qa.documents["relation_members"]
                parent = next(
                    relation for relation in graph["relations"]
                    if relation["relation_id"] == 700)
                parent["members"] = [{
                    "sequence": 0, "type": "relation", "ref": 701,
                    "role": "",
                }]
                qa.documents["manifest"]["relation_members_evidence"][
                    "sha256"] = validator._canonical_json_sha256(graph)
                qa.rewrite("relation_members")
                qa.rewrite("manifest")
            result_path = root / "validation/final.json"
            validator_rc = validator.main([
                "final", "--qa-dir", str(root),
                "--relation-members", str(
                    root / validator.RELATION_MEMBERS_RELATIVE),
                "--expected-osm-relation-id", str(RELATION_ID),
                "--result-json", str(result_path),
            ])
            return validator_rc, json.loads(result_path.read_text()), trails, removed, report

    def test_real_exact_area_producer_output_is_accepted(self):
        rc, result, trails, removed, report = self._run("connected")
        self.assertEqual(rc, 0, result["errors"])
        self.assertEqual(len(trails["features"]), 1)
        properties = trails["features"][0]["properties"]
        self.assertEqual(properties["member_ways"], [10, 11])
        self.assertEqual(properties["restored_relation_way_ids"], [11])
        self.assertEqual(removed["features"], [])
        self.assertEqual(report["quality"]["signed_relation_unresolved_count"], 0)
        self.assertEqual(
            report["quality"]["restored_road_relations"][0]["restored_way_ids"],
            [11])

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
                    self.assertIn("excluded-direct-member",
                                  audit["unresolved_reasons"])

    def test_removed_thru_hike_exclusion_fails_final_validator_without_geometry(self):
        rc, result, trails, _, report = self._run("superroute-private")
        self.assertEqual(rc, 1)
        self.assertEqual(trails["features"], [])
        quality = report["quality"]
        self.assertEqual(quality["relation_member_unresolved_count"], 2)
        self.assertEqual(
            [audit["assembly_status"]
             for audit in quality["relation_member_audit"]],
            ["removed-thru-hike", "removed-thru-hike"])
        self.assertEqual(
            [audit["review_status"]
             for audit in quality["relation_member_audit"]],
            ["unresolved", "unresolved"])
        for audit, unresolved in zip(
                quality["relation_member_audit"],
                quality["relation_member_unresolved"]):
            self.assertEqual(audit["unresolved_reasons"],
                             ["excluded-direct-member"])
            self.assertEqual(unresolved["excluded_way_ids"], [11])
            self.assertEqual(unresolved["reasons"],
                             ["excluded-direct-member"])
        self.assertTrue(any(
            "contains unresolved member exclusions or evidence" in error
            for error in result["errors"]))

    def test_absorbed_same_name_path_cannot_dilute_relation_share(self):
        rc, result, trails, removed, report = self._run("diluted-restoration")
        self.assertEqual(rc, 1)
        self.assertEqual(removed["features"], [])
        properties = trails["features"][0]["properties"]
        self.assertEqual(properties["member_ways"], [10, 11, 12])
        measurement = report["quality"]["restored_road_relations"][0]
        self.assertEqual(measurement["relation_id"], 700)
        self.assertEqual(measurement["relation_way_ids"], [10, 11])
        self.assertNotIn(12, measurement["relation_way_ids"])
        self.assertGreater(measurement["restored_share"], 0.10)
        self.assertTrue(any("exceeds restored-road share limit" in error
                            for error in result["errors"]))

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
        self.assertEqual(len(trails["features"]), 1)
        self.assertEqual(removed["features"], [])
        self.assertEqual(report["quality"]["signed_relation_unresolved_count"], 1)
        self.assertTrue(any("unresolved-relation-gap" in error
                            for error in result["errors"]))


if __name__ == "__main__":
    unittest.main()
