"""Offline exact-boundary and structured-report tests for assemble.py."""
import copy
import hashlib
import importlib.util
import io
import json
import sys
import unittest
from contextlib import nullcontext, redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
from unittest.mock import patch

try:
    from shapely.geometry import box
    HAVE_SHAPELY = True
except ImportError:
    HAVE_SHAPELY = False

_HERE = Path(__file__).resolve().parent
_SPEC = importlib.util.spec_from_file_location(
    "assemble_pilot_module", _HERE / "assemble.py")
assemble = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(assemble)
import areas  # noqa: E402


class _Trail:
    def __init__(self, name="Mols Trail", *, source="name-stitch", kind="trail",
                 area="Nationalpark Mols Bjerge", member_ways=None,
                 coordinates=None, relation_ids=None):
        member_ways = list(member_ways or [10])
        coordinates = coordinates or [[[10.5, 56.15], [10.6, 56.2]]]
        self.name = name
        self.source = source
        self.area = area
        self.tags = {}
        self.lines = [
            [(float(point[0]), float(point[1])) for point in line]
            for line in coordinates
        ]
        self.member_ways = member_ways
        self.geometry_ways = list(member_ways)
        self.relation_ids = list(relation_ids or [])
        self.root_relation_ids = list(relation_ids or [])
        self.direct_relation_ways = {
            relation_id: list(member_ways) for relation_id in self.relation_ids
        }
        self.restored_relation_ways = []
        self.destinations = []
        self.destination_evidence = []
        self.welds = []
        self.terminal_nodes = []
        self.hike = kind == "hike"
        self._deferred_absorption_targets = []
        self._terminal_successor = None
        self._terminal_absorption_candidate_id = None
        self._promotion_endpoint_sources = {}
        self._promotion_decision = None
        self._feature = {
            "type": "Feature",
            "properties": {
                "name": name,
                "kind": kind,
                "area": area,
                "source": source,
                "length_mi": 1.0,
                "member_ways": member_ways,
                "destinations": [],
                "welds": [],
                "network": "",
                "operator": "",
                "sac_scale": "",
                "trail_visibility": "",
                "removed_reason": None,
                "removed_category": None,
                "ckey": "w" + "-".join(str(way_id) for way_id in member_ways),
            },
            "geometry": {
                "type": "MultiLineString",
                "coordinates": coordinates,
            },
        }

    def to_feature(self):
        feature = copy.deepcopy(self._feature)
        properties = feature["properties"]
        properties.update({
            "name": self.name,
            "kind": "hike" if self.hike else properties["kind"],
            "area": self.area,
            "source": self.source,
            "member_ways": list(self.member_ways),
            "destinations": list(self.destinations),
            "welds": list(self.welds),
            "ckey": "w" + "-".join(
                str(way_id) for way_id in self.member_ways),
        })
        if self.destination_evidence:
            properties["destination_evidence"] = copy.deepcopy(
                self.destination_evidence)
        else:
            properties.pop("destination_evidence", None)
        feature["geometry"]["coordinates"] = [
            [list(point) for point in line] for line in self.lines
        ]
        return feature


def _boundary(name="Nationalpark Mols Bjerge", osm_type="relation", osm_id=7046785):
    return {
        "name": name,
        "tags": {"name": name, "boundary": "national_park"},
        "geom": box(10.4, 56.08, 10.9, 56.35),
        "osm_type": osm_type,
        "osm_id": osm_id,
    }


class PbfRelationMemberLoading(unittest.TestCase):
    def test_direct_relation_way_without_highway_is_still_loaded_for_audit(self):
        relation = SimpleNamespace(
            id=700,
            tags=[SimpleNamespace(k="type", v="route"),
                  SimpleNamespace(k="route", v="hiking")],
            members=[SimpleNamespace(type="w", ref=10, role="")],
        )
        ways = [
            SimpleNamespace(
                id=10,
                tags=[SimpleNamespace(k="surface", v="ground")],
                nodes=[SimpleNamespace(ref=1), SimpleNamespace(ref=2)]),
            SimpleNamespace(
                id=20,
                tags=[SimpleNamespace(k="surface", v="ground")],
                nodes=[SimpleNamespace(ref=3), SimpleNamespace(ref=4)]),
        ]
        nodes = [
            SimpleNamespace(id=node_id, tags=[],
                            location=SimpleNamespace(lon=float(node_id), lat=0.0))
            for node_id in range(1, 5)
        ]

        class FakeHandler:
            def apply_file(self, _path):
                if hasattr(self, "relation"):
                    self.relation(relation)
                if hasattr(self, "way"):
                    for way in ways:
                        self.way(way)
                if hasattr(self, "node"):
                    for node in nodes:
                        self.node(node)

        with patch.dict(sys.modules, {
                "osmium": SimpleNamespace(SimpleHandler=FakeHandler)}):
            loaded_nodes, loaded_ways, loaded_relations, _ = \
                assemble.read_pbf("synthetic.osm.pbf")

        self.assertEqual(set(loaded_ways), {10})
        self.assertEqual(loaded_ways[10]["tags"], {"surface": "ground"})
        self.assertEqual(loaded_nodes, {1: (1.0, 0.0), 2: (2.0, 0.0)})
        self.assertEqual(loaded_relations[700]["members"], [("w", 10, "")])
    def test_exact_denmark_and_legacy_poi_loading_policies_do_not_leak(self):
        poi_nodes = [
            SimpleNamespace(
                id=90,
                tags=[SimpleNamespace(k="tourism", v="attraction"),
                      SimpleNamespace(k="name", v="Attraction")],
                location=SimpleNamespace(lon=10.5, lat=56.15)),
            SimpleNamespace(
                id=91,
                tags=[SimpleNamespace(k="waterway", v="waterfall"),
                      SimpleNamespace(k="name", v="Waterfall")],
                location=SimpleNamespace(lon=10.6, lat=56.15)),
        ]

        class FakeHandler:
            def apply_file(self, _path):
                if hasattr(self, "node"):
                    for node in poi_nodes:
                        self.node(node)

        with patch.dict(sys.modules, {
                "osmium": SimpleNamespace(SimpleHandler=FakeHandler)}):
            *_, legacy = assemble.read_pbf("synthetic.osm.pbf")
            *_, denmark = assemble.read_pbf(
                "synthetic.osm.pbf", exact_denmark=True)

        self.assertEqual([poi["id"] for poi in legacy], [91])
        self.assertEqual([poi["id"] for poi in denmark], [90])

    def test_non_exact_dk_producer_preserves_legacy_poi_promotion(self):
        nodes = {
            1: (10.50, 56.15), 2: (10.52, 56.15),
            3: (10.50, 56.25), 4: (10.52, 56.25),
        }
        ways = {
            10: {"nodes": [1, 2], "tags": {"highway": "path"}},
            20: {"nodes": [3, 4], "tags": {"highway": "path"}},
        }
        relations = {
            700: {
                "tags": {"type": "route", "route": "hiking",
                         "name": "Falls Access Route"},
                "members": [("w", 10, "")],
            },
            701: {
                "tags": {"type": "route", "route": "hiking",
                         "name": "Hut Access Route"},
                "members": [("w", 20, "")],
            },
        }
        legacy_pois = [{
            "id": 90, "name": "Legacy Falls", "coord": nodes[2],
            "tags": {"name": "Legacy Falls", "waterway": "waterfall"},
        }, {
            "id": 91, "name": "Legacy Hut", "coord": nodes[4],
            "tags": {"name": "Legacy Hut", "tourism": "alpine_hut"},
        }]
        exact_only_poi = {
            "id": 92, "name": "Pilot Attraction", "coord": nodes[2],
            "tags": {"name": "Pilot Attraction", "tourism": "attraction"},
        }
        read_modes = []

        def read_pbf(_path, *, exact_denmark=False):
            read_modes.append(exact_denmark)
            pois = [*legacy_pois]
            if exact_denmark:
                pois.append(exact_only_poi)
            return (copy.deepcopy(nodes), copy.deepcopy(ways),
                    copy.deepcopy(relations), copy.deepcopy(pois))

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            outputs = {}
            with (
                patch.object(assemble, "read_pbf", side_effect=read_pbf),
                patch.object(areas, "assemble_areas", return_value=[]),
            ):
                for region in ("dk", "az"):
                    output = root / f"{region}.trails.geojson"
                    rc = assemble.main([
                        "--in", str(root / "synthetic.osm.pbf"),
                        "--out", str(output), "--region", region,
                    ])
                    self.assertEqual(rc, 0)
                    outputs[region] = json.loads(output.read_text())

        self.assertEqual(read_modes, [False, False])
        self.assertEqual(outputs["dk"], outputs["az"])
        properties = [
            feature["properties"] for feature in outputs["dk"]["features"]
        ]
        self.assertEqual(
            [(value["name"], value["kind"], value["destinations"])
             for value in properties],
            [("Legacy Falls Trail", "hike", ["Legacy Falls"]),
             ("Legacy Hut Trail", "hike", ["Legacy Hut"])])
        self.assertTrue(all(
            "destination_evidence" not in value for value in properties))


class ThreeScopeAuthorityLoading(unittest.TestCase):
    def _ledger(self, scope, source, *, way_status="present", highway="path",
                way_name=None):
        if way_status == "missing":
            way = {
                "way_id": 10, "status": "missing", "tags": None,
                "node_ids": None, "coordinates": None,
                "missing_node_ids": None,
            }
            missing = [10]
        else:
            tags = {"highway": highway}
            if way_name is not None:
                tags["name"] = way_name
            way = {
                "way_id": 10, "status": "present",
                "tags": tags,
                "node_ids": [1, 2],
                "coordinates": [[10.5, 56.15], [10.6, 56.2]],
                "missing_node_ids": [],
            }
            missing = []
        return {
            "schema_version": 2,
            "scope": scope,
            "source": {
                "kind": "runner-local-osm-pbf",
                "artifact": assemble._RELATION_SOURCE_ARTIFACTS[scope],
                **source,
            },
            "attribution": "© OpenStreetMap contributors",
            "accepted_route_values": ["foot", "hiking", "running", "walking"],
            "root_relation_ids": [700],
            "relation_count": 1,
            "missing_relation_ids": [],
            "missing_direct_way_ids": missing,
            "incomplete_direct_way_ids": [],
            "relations": [{
                "relation_id": 700,
                "accepted_hiking_route": True,
                "tags": {"type": "route", "route": "hiking",
                         "name": "Scoped Route"},
                "members": [{
                    "sequence": 0, "type": "way", "ref": 10, "role": "",
                }],
                "direct_way_ids": [10],
                "direct_ways": [way],
            }],
        }

    def _load(self, *, prefilter_highway="path", aoi_status="missing",
              aoi_identity=None, way_name=None):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_identity = {
                "sha256": hashlib.sha256(b"aoi").hexdigest(), "bytes": 3,
            }
            documents = {
                "raw": self._ledger(
                    "raw-denmark", {"sha256": "a" * 64, "bytes": 100},
                    way_name=way_name),
                "prefilter": self._ledger(
                    "prefiltered-denmark",
                    {"sha256": "b" * 64, "bytes": 80},
                    highway=prefilter_highway, way_name=way_name),
                "aoi": self._ledger(
                    "aoi", aoi_identity or input_identity,
                    way_status=aoi_status, way_name=way_name),
            }
            paths = {}
            for label, document in documents.items():
                path = root / f"{label}.json"
                path.write_text(json.dumps(document), encoding="utf-8")
                paths[label] = path
            scopes = assemble._load_relation_scopes(
                str(paths["raw"]), str(paths["prefilter"]), str(paths["aoi"]),
                input_identity)
            return scopes

    def test_raw_authority_augments_geometry_while_aoi_absence_stays_scope_only(self):
        scopes = self._load()
        nodes, ways, relations = {}, {}, {}

        assemble._augment_with_raw_authority(nodes, ways, relations, scopes)

        self.assertEqual(nodes, {1: (10.5, 56.15), 2: (10.6, 56.2)})
        self.assertEqual(ways[10], {
            "tags": {"highway": "path"}, "nodes": [1, 2],
        })
        self.assertEqual(relations[700]["members"], [("w", 10, "")])
        self.assertEqual(scopes["aoi"]["ways"][10]["status"], "missing")

    def test_raw_only_distinct_name_is_relation_geometry_not_standalone(self):
        scopes = self._load(way_name="Raw-only Distinct Trail")
        nodes, ways, relations = {}, {}, {}
        aoi_way_ids = frozenset(ways)
        assemble._augment_with_raw_authority(nodes, ways, relations, scopes)
        removed = []

        trails = assemble.model.assemble(
            nodes, ways, relations, [], collect_removed=removed, region="dk",
            relation_scope_evidence=scopes,
            standalone_way_ids=aoi_way_ids)

        self.assertEqual([trail.name for trail in trails], ["Scoped Route"])
        self.assertEqual(trails[0].member_ways, [10])
        self.assertEqual(trails[0].source, "relation")
        self.assertEqual(removed, [])

    def test_prefilter_tag_mismatch_fails_before_assembly(self):
        with self.assertRaisesRegex(ValueError, "contradicts raw Denmark"):
            self._load(prefilter_highway="service")

    def test_aoi_ledger_must_bind_exact_input_pbf(self):
        with self.assertRaisesRegex(ValueError, "source disagrees"):
            self._load(aoi_identity={"sha256": "d" * 64, "bytes": 3})


@unittest.skipUnless(HAVE_SHAPELY, "shapely not installed")
class ExactPilotAssembly(unittest.TestCase):
    def _run(self, boundaries, *, nodes=None, ways=None, trails=None,
             extra_args=(), unresolved_terminal=False):
        nodes = nodes if nodes is not None else {
            1: (10.5, 56.15),
            2: (10.6, 56.2),
        }
        ways = ways if ways is not None else {
            10: {"tags": {"highway": "path", "name": "Mols Trail"},
                 "nodes": [1, 2]},
        }
        trails = trails if trails is not None else [_Trail()]
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_pbf = root / "mols.osm.pbf"
            input_pbf.write_bytes(b"synthetic-runner-local-aoi-pbf")
            output = root / "mols-bjerge.trails.geojson"
            report = root / "assembly.json"
            argv = [
                "--in", str(input_pbf),
                "--out", str(output),
                "--only-area", "Nationalpark Mols Bjerge",
                "--require-exact-area",
                "--expected-area-relation-id", "7046785",
                "--report-json", str(report),
                *extra_args,
            ]
            stderr = io.StringIO()
            unresolved = [{
                "reason": "missing-target",
                "source_candidate": {},
                "target_candidates": [{}],
            }]
            terminal_patch = (
                patch.object(
                    assemble, "_resolve_terminal_relation_absorptions",
                    side_effect=lambda features, _targets, **_kwargs:
                    (features, [], unresolved))
                if unresolved_terminal else nullcontext()
            )
            with (
                patch.object(assemble, "read_pbf", return_value=(nodes, ways, {}, [])),
                patch.object(areas, "assemble_areas", return_value=boundaries),
                patch.object(areas, "merge_areas", return_value=[]),
                patch.object(assemble.model, "assemble", return_value=trails) as build,
                terminal_patch,
                redirect_stderr(stderr),
            ):
                rc = assemble.main(argv)
            payload = json.loads(report.read_text())
            output_payload = json.loads(output.read_text()) if output.exists() else None
            files = sorted(path.name for path in root.iterdir()
                           if path != input_pbf)
            return rc, payload, output_payload, files, build.call_count, stderr.getvalue()

    def _run_ingest_reconciliation(self, coordinates):
        nodes = {index: tuple(point)
                 for index, point in enumerate(coordinates, start=1)}
        ways = {
            10: {"tags": {"highway": "path", "name": "Signed Route"},
                 "nodes": list(nodes)},
        }
        trail = _Trail(
            "Signed Route", source="relation", kind="route", area=None,
            member_ways=[10], coordinates=[[list(point) for point in coordinates]],
            relation_ids=[700],
        )
        diagnostic = {
            "type": "Feature",
            "properties": {
                "name": "Source Diagnostic",
                "way_id": 10,
                "ckey": "w10",
                "highway": "track",
                "removed_category": "road-track-tag",
                "removed_reason": "Original standalone drop reason.",
                "retained_in_route": False,
                "relation_ids": [],
            },
            "geometry": {"type": "LineString", "coordinates": coordinates},
        }

        def build(_nodes, _ways, _relations, _pois, **kwargs):
            kwargs["collect_ingest_dropped"].append(copy.deepcopy(diagnostic))
            return [trail]

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_pbf = root / "mols.osm.pbf"
            input_pbf.write_bytes(b"synthetic-runner-local-aoi-pbf")
            output = root / "mols-bjerge.trails.geojson"
            report = root / "assembly.json"
            with (
                patch.object(assemble, "read_pbf",
                             return_value=(nodes, ways, {}, [])),
                patch.object(areas, "assemble_areas", return_value=[_boundary()]),
                patch.object(assemble.model, "assemble", side_effect=build),
            ):
                rc = assemble.main([
                    "--in", str(input_pbf),
                    "--out", str(output),
                    "--only-area", "Nationalpark Mols Bjerge",
                    "--require-exact-area",
                    "--expected-area-relation-id", "7046785",
                    "--report-json", str(report),
                    "--region", "dk",
                ])
            ingest = json.loads(
                (root / "mols-bjerge.ingest-dropped.geojson").read_text())
            trails = json.loads(output.read_text())
            return rc, trails, ingest

    def test_terminal_resolver_collapses_chains_and_reports_every_failure_reason(self):
        marker = assemble._TERMINAL_CANDIDATE_ID

        def feature(index, name=None, coordinates=None, *, category=None):
            properties = {
                marker: index,
                "name": name or f"Candidate {index}",
                "source": "relation",
                "ckey": f"w{index + 1}",
                "root_relation_ids": [700 + index],
                "quality_disposition": "kept",
            }
            if category is not None:
                properties["removed_category"] = category
            return {
                "type": "Feature",
                "properties": properties,
                "geometry": {
                    "type": "MultiLineString",
                    "coordinates": coordinates or [
                        [[10.5, 56.15], [10.55, 56.15]]],
                },
            }

        chain = [feature(0), feature(1), feature(2, "Terminal")]
        kept, removed, unresolved = \
            assemble._resolve_terminal_relation_absorptions(
                chain, {0: [1], 1: [2]})
        self.assertEqual(unresolved, [])
        self.assertEqual([row["properties"][marker] for row in kept], [2])
        self.assertEqual(
            [row["properties"][marker] for row in removed], [0, 1])
        self.assertTrue(all(
            row["properties"]["drop_evidence"]["survivor"]["name"] ==
            "Terminal" for row in removed))
        kept[0]["properties"]["ckey"] = "w-shared"
        removed[0]["properties"]["ckey"] = "w-shared"
        self.assertEqual(
            assemble._curation_snapshot(
                kept, removed, published_wins=True)["w-shared"], "kept")
        self.assertEqual(
            assemble._curation_snapshot(kept, removed)["w-shared"],
            assemble.model.GEOMETRY_DUPLICATE_ABSORBED_CATEGORY)

        clipped = feature(1, category="outside-exact-area")
        quality_removed = feature(1, category="dk-unqualified-road-track")
        model_removed = feature(1, category="closed")
        model_removed["properties"].update({
            assemble._TERMINAL_REMOVAL_STAGE: "model-curation",
            "removed_reason": "Closed by authoritative model curation.",
            assemble._TERMINAL_SUCCESSOR_IDS: [],
        })
        different = feature(1, coordinates=[[
            [10.5, 56.16], [10.55, 56.16],
        ]])
        cases = (
            ("cycle", [feature(0), feature(1)], {0: [1], 1: [0]}, [],
             ["cycle", "cycle"]),
            ("divergent", [feature(0), feature(1), feature(2)], {0: [1, 2]},
             [], ["divergent-targets"]),
            ("missing", [feature(0)], {0: [99]}, [], ["missing-target"]),
            ("clipped", [feature(0)], {0: [1]}, [clipped],
             ["clipped-target"]),
            ("model-curation", [feature(0)], {0: [1]}, [model_removed],
             ["model-curation-removed-target"]),
            ("quality-removed", [feature(0)], {0: [1]}, [quality_removed],
             ["quality-removed-target"]),
            ("coverage", [feature(0), different], {0: [1]}, [],
             ["coverage-failure"]),
            ("ambiguous", [feature(0), feature(1), feature(1)], {0: [1]}, [],
             ["ambiguous-survivor"]),
            ("nested-ambiguous",
             [feature(0), feature(1), feature(2), feature(3)],
             {0: [1], 1: [2, 3]}, [],
             ["ambiguous-survivor", "divergent-targets"]),
        )
        for label, candidates, targets, removed_targets, expected_reasons in cases:
            with self.subTest(label=label):
                kept, removed, unresolved = \
                    assemble._resolve_terminal_relation_absorptions(
                        candidates, targets, removed_targets=removed_targets)
                self.assertEqual(kept, candidates)
                self.assertEqual(removed, [])
                self.assertEqual(
                    [record["reason"] for record in unresolved],
                    expected_reasons)
                for record in unresolved:
                    self.assertEqual(
                        record["source_candidate"]["population"], "published")
                    self.assertEqual(
                        record["source_candidate"]["source"], "relation")
                    self.assertTrue(
                        record["source_candidate"]["root_relation_ids"])
                    self.assertRegex(
                        record["source_candidate"]["geometry_sha256"],
                        r"^[0-9a-f]{64}$")
                    self.assertTrue(record["target_candidates"])
                    for target in record["target_candidates"]:
                        if target["population"] == "missing":
                            self.assertIsNone(target["geometry_sha256"])
                        else:
                            self.assertRegex(
                                target["geometry_sha256"], r"^[0-9a-f]{64}$")
                    if label == "model-curation":
                        target = record["target_candidates"][0]
                        self.assertEqual(target["removal_stage"],
                                         "model-curation")
                        self.assertEqual(target["removed_category"], "closed")
                        self.assertEqual(
                            target["removed_reason"],
                            "Closed by authoritative model curation.")
                        self.assertEqual(target["successor_candidate_ids"], [])

        distinct = [feature(0), different]
        kept, removed, unresolved = \
            assemble._resolve_terminal_relation_absorptions(distinct, {})
        self.assertEqual(kept, distinct)
        self.assertEqual(removed, [])
        self.assertEqual(unresolved, [])

    def test_ingest_relation_fully_outside_remains_ordinary_drop(self):
        rc, trails, ingest = self._run_ingest_reconciliation(
            [[11.0, 56.15], [11.1, 56.15]])

        self.assertEqual(rc, 0)
        self.assertEqual(trails["features"], [])
        properties = ingest["features"][0]["properties"]
        self.assertFalse(properties["retained_in_route"])
        self.assertEqual(properties["relation_ids"], [])
        self.assertEqual(properties["removed_category"], "road-track-tag")
        self.assertEqual(properties["removed_reason"],
                         "Original standalone drop reason.")
        self.assertNotIn("standalone_drop_category", properties)

    def test_ingest_partially_clipped_relation_uses_surviving_binding(self):
        rc, trails, ingest = self._run_ingest_reconciliation(
            [[10.5, 56.15], [11.0, 56.15]])

        self.assertEqual(rc, 0)
        self.assertEqual(len(trails["features"]), 1)
        self.assertTrue(trails["features"][0]["properties"]["clipped"])
        properties = ingest["features"][0]["properties"]
        self.assertTrue(properties["retained_in_route"])
        self.assertEqual(properties["relation_ids"], [700])
        self.assertEqual(properties["standalone_drop_category"], "road-track-tag")
        self.assertEqual(properties["standalone_drop_reason"],
                         "Original standalone drop reason.")

    def test_ingest_inside_relation_uses_final_binding(self):
        rc, trails, ingest = self._run_ingest_reconciliation(
            [[10.5, 56.15], [10.6, 56.15]])

        self.assertEqual(rc, 0)
        self.assertEqual(len(trails["features"]), 1)
        properties = ingest["features"][0]["properties"]
        self.assertTrue(properties["retained_in_route"])
        self.assertEqual(properties["relation_ids"], [700])

    def test_exact_relation_succeeds_and_reports_clip_and_coverage(self):
        rc, report, output, files, calls, _ = self._run(
            [_boundary()], extra_args=("--per-area-merge", "--region", "dk"))

        self.assertEqual(rc, 0)
        self.assertEqual(calls, 1)
        self.assertEqual(report["status"], "ok")
        self.assertTrue(report["exact_area_required"])
        self.assertEqual(report["boundary_match_count"], 1)
        self.assertEqual(report["boundary"], {
            "name": "Nationalpark Mols Bjerge",
            "osm_type": "relation",
            "osm_id": 7046785,
        })
        source_bytes = b"synthetic-runner-local-aoi-pbf"
        self.assertEqual(report["input_pbf"], {
            "sha256": hashlib.sha256(source_bytes).hexdigest(),
            "bytes": len(source_bytes),
        })
        self.assertTrue(report["clip_applied"])
        self.assertEqual(report["pre_clip_trail_count"], 1)
        self.assertEqual(report["post_clip_trail_count"], 1)
        self.assertEqual(report["coverage"]["raw_trailish_ways"], 1)
        self.assertEqual(report["coverage"]["named_trailish_ways"], 1)
        self.assertEqual(len(output["features"]), 1)
        self.assertIn("mols-bjerge.areas.geojson", files)
        self.assertIn("mols-bjerge.curation-diff.json", files)

    def test_mols_pilot_fails_closed_and_reports_unresolved_absorption(self):
        rc, report, output, _, _, stderr = self._run(
            [_boundary()], extra_args=("--region", "dk"),
            unresolved_terminal=True)

        self.assertEqual(rc, 2)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["failure"],
                         "unresolved terminal relation absorptions: 1")
        self.assertEqual(
            report["quality"]["terminal_absorption_unresolved_count"], 1)
        self.assertEqual(
            report["quality"]["terminal_absorption_unresolved"][0]["reason"],
            "missing-target")
        self.assertEqual(len(output["features"]), 1)
        self.assertIn("ERROR: unresolved terminal relation absorptions: 1",
                      stderr)

    def test_exact_clip_assigns_area_to_run33_producer_patterns(self):
        nodes = {
            1: (10.50, 56.15), 2: (10.55, 56.16),
            3: (10.56, 56.17), 4: (10.60, 56.18),
            5: (10.61, 56.19), 6: (10.65, 56.20),
            7: (10.80, 56.21), 8: (11.00, 56.22),
        }
        ways = {
            way_id: {"tags": {"highway": "path", "name": name},
                     "nodes": node_ids}
            for way_id, name, node_ids in (
                (10, "Molsruten", [1, 2]),
                (11, "Midtervej", [3, 4]),
                (12, "Skolestien", [5, 6]),
                (13, "Boundary Trail", [7, 8]),
            )
        }
        trails = [
            _Trail("Molsruten", source="relation", kind="route", area=None,
                   member_ways=[10], coordinates=[[list(nodes[1]), list(nodes[2])]],
                   relation_ids=[100]),
            _Trail("Midtervej", source="relation", kind="trail", area=None,
                   member_ways=[11], coordinates=[[list(nodes[3]), list(nodes[4])]],
                   relation_ids=[101]),
            _Trail("Skolestien", area=None, member_ways=[12],
                   coordinates=[[list(nodes[5]), list(nodes[6])]]),
            _Trail("Boundary Trail", area=None, member_ways=[13],
                   coordinates=[[list(nodes[7]), list(nodes[8])]]),
        ]

        rc, report, output, _, _, _ = self._run(
            [_boundary()], nodes=nodes, ways=ways, trails=trails,
            extra_args=("--per-area-merge", "--region", "dk"))

        self.assertEqual(rc, 0)
        self.assertEqual(report["post_clip_trail_count"], 4)
        self.assertEqual(len(output["features"]), 4)
        self.assertEqual(
            {feature["properties"]["area"] for feature in output["features"]},
            {"Nationalpark Mols Bjerge"},
        )
        clipped = next(feature for feature in output["features"]
                       if feature["properties"]["name"] == "Boundary Trail")
        self.assertTrue(clipped["properties"]["clipped"])

    def test_zero_coverage_counters_are_reported_faithfully(self):
        rc, report, _, _, _, _ = self._run([_boundary()], ways={})
        self.assertEqual(rc, 0)
        self.assertEqual(report["coverage"]["raw_trailish_ways"], 0)
        self.assertEqual(report["coverage"]["named_trailish_ways"], 0)

    def test_missing_duplicate_wrong_name_way_and_wrong_relation_fail_closed(self):
        cases = {
            "missing": [],
            "duplicate": [_boundary(), _boundary()],
            "wrong-name": [_boundary(name="nationalpark mols bjerge")],
            "way-source": [_boundary(osm_type="way")],
            "wrong-relation": [_boundary(osm_id=1)],
        }
        for label, boundaries in cases.items():
            with self.subTest(label=label):
                rc, report, output, files, calls, stderr = self._run(boundaries)
                self.assertEqual(rc, 2)
                self.assertEqual(calls, 0)
                self.assertEqual(report["status"], "failed")
                self.assertFalse(report["clip_applied"])
                self.assertIsNone(output)
                self.assertEqual(files, ["assembly.json"])
                self.assertIn("exact-area validation failed", stderr)

    def test_legacy_substring_union_path_still_clips_case_insensitively(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "legacy.trails.geojson"
            boundaries = [
                _boundary(),
                _boundary(name="Mols Bjerge Annex", osm_id=99),
            ]
            with (
                patch.object(assemble, "read_pbf", return_value=({}, {}, {}, [])),
                patch.object(areas, "assemble_areas", return_value=boundaries),
                patch.object(assemble.model, "assemble", return_value=[_Trail()]),
                patch.object(areas, "union_matching", wraps=areas.union_matching) as union,
            ):
                rc = assemble.main([
                    "--in", str(root / "legacy.osm.pbf"),
                    "--out", str(output),
                    "--only-area", "mols bjerge",
                ])
            payload = json.loads(output.read_text())
            self.assertEqual(rc, 0)
            self.assertEqual(len(payload["features"]), 1)
            union.assert_called_once()
            self.assertEqual(union.call_args.args[1], "mols bjerge")

    def test_legacy_missing_substring_match_still_warns_and_leaves_output_unclipped(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "legacy.trails.geojson"
            stderr = io.StringIO()
            with (
                patch.object(assemble, "read_pbf", return_value=({}, {}, {}, [])),
                patch.object(areas, "assemble_areas", return_value=[]),
                patch.object(assemble.model, "assemble", return_value=[_Trail()]),
                redirect_stderr(stderr),
            ):
                rc = assemble.main([
                    "--in", str(root / "legacy.osm.pbf"),
                    "--out", str(output),
                    "--only-area", "missing park",
                ])
            payload = json.loads(output.read_text())
            self.assertEqual(rc, 0)
            self.assertEqual(len(payload["features"]), 1)
            self.assertIn("leaving trails unclipped", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
