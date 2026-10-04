"""Default sequential curation replay from a compact run-33 test fixture."""
import hashlib
import json
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import quality  # noqa: E402

from shapely.geometry import shape  # noqa: E402

_FIXTURE_DIR = Path(__file__).resolve().parent / "testdata"
_FIXTURE = _FIXTURE_DIR / "mols-run33-curation-replay.json"
_FIXTURE_MANIFEST = _FIXTURE_DIR / "mols-run33-curation-replay.manifest.json"
_EXPECTATIONS = json.loads(_FIXTURE_MANIFEST.read_text(encoding="utf-8"))
_FIXTURE_SHA256 = _EXPECTATIONS["output"]["sha256"]
_FIXTURE_BYTES = _EXPECTATIONS["output"]["bytes"]
_RUN_ID = _EXPECTATIONS["github_run_id"]
_AREA = "Nationalpark Mols Bjerge"
_SOURCE_SHA = _EXPECTATIONS["source_sha"]
_ATTRIBUTION = "© OpenStreetMap contributors"
_EXPECTED_UNRESOLVED = [
    "Molsruten", "Kaløstien", "Djurslandstien",
    "Mols Bjerge-stien Bjergetapen",
    "Mols Bjerge-stien Ebeltoftetapen",
    "Mols Bjerge-stien Kaløetapen", "Kløverstier-Naturruten",
    "Kløverstier-Landsbyruten", "Langemosestien", "Helligkildestien",
    "Mols Bjerge-stien Gåsehage-etapen", "Kvalitetssti Agri Bavnehøj",
    "Toggerbo-stien", "Tinghulestien", "Langs Kysten", "Den gamle købstad",
]
_EXPECTED_REMOVED = {
    "disconnected-name-stitch": [
        "Lyngevej", "Skolestien", "Gl. Møllevej", "Provstskovvej",
        "Ved Mølleåen", "Møgelbjergvej", "Gravlevstien", "Jægergårdsvej",
        "Søhusvej", "Søvej",
    ],
    "dk-unqualified-road-track": [
        "Enhøjvej", "Septembervej", "Nederste Vej", "Kirkevejen",
        "Skovriderstedsvej", "Munkevej", "Toldvejen", "Skolevej",
        "Kristoffervejen", "Øervej", "Kejlstrupvej", "Fruerlundvej",
        "Agertoften", "Bækkevangen", "Skærsø Skovvej", "Blåbærvej",
        "Ebeltoftvej", "Teglgårdsvej", "Hjelm", "Vestre Skovvej",
        "Ved hullerne", "Mælkevejen", "Porskærvej", "Troldbakkevej",
    ],
    "nested-name-stitch-overlap": ["Fægyden"],
}
_EXPECTED_OVERLAPS = [
    ["Molsruten", "Djurslandstien"],
    ["Molsruten", "Eriks Vej"],
    ["Kaløstien", "Fægyden"],
    ["Djurslandstien", "Eriks Vej"],
    ["Mols Bjerge-stien Gåsehage-etapen", "Søhusvej"],
    ["Den gamle købstad", "Adelgade"],
]
_EXPECTED_DISPOSITION_SEQUENCE = [
    "unresolved-relation-gap", "unresolved-relation-gap",
    "unresolved-relation-gap", "boundary-induced-split", "kept", "kept",
    "kept", "unresolved-relation-gap", "kept", "kept",
    "unresolved-relation-gap", "unresolved-relation-gap",
    "unresolved-relation-gap", "unresolved-relation-gap",
    "boundary-induced-split", "unresolved-relation-gap",
    "unresolved-relation-gap", "kept", "unresolved-relation-gap",
    "unresolved-relation-gap", "unresolved-relation-gap", "kept",
    "unresolved-relation-gap", "unresolved-relation-gap",
    "unresolved-relation-gap", "kept", "kept", "kept",
    "dk-unqualified-road-track", "dk-unqualified-road-track",
    "dk-unqualified-road-track", "dk-unqualified-road-track",
    "nested-name-stitch-overlap", "kept", "dk-unqualified-road-track",
    "kept", "disconnected-name-stitch", "dk-unqualified-road-track",
    "dk-unqualified-road-track", "dk-unqualified-road-track",
    "disconnected-name-stitch", "dk-unqualified-road-track",
    "dk-unqualified-road-track", "disconnected-name-stitch",
    "disconnected-name-stitch", "dk-unqualified-road-track",
    "dk-unqualified-road-track", "kept", "dk-unqualified-road-track",
    "dk-unqualified-road-track", "dk-unqualified-road-track",
    "dk-unqualified-road-track", "disconnected-name-stitch",
    "dk-unqualified-road-track", "disconnected-name-stitch",
    "disconnected-name-stitch", "dk-unqualified-road-track",
    "disconnected-name-stitch", "dk-unqualified-road-track",
    "disconnected-name-stitch", "kept", "dk-unqualified-road-track",
    "dk-unqualified-road-track", "disconnected-name-stitch",
    "dk-unqualified-road-track", "dk-unqualified-road-track",
    "dk-unqualified-road-track", "kept", "kept", "missing-source-way",
]


def _load_fixture():
    raw = _FIXTURE.read_bytes()
    if len(raw) != _FIXTURE_BYTES:
        raise AssertionError("run-33 replay fixture size changed")
    if hashlib.sha256(raw).hexdigest() != _FIXTURE_SHA256:
        raise AssertionError("run-33 replay fixture digest changed")
    return json.loads(raw)


def _production_inputs(fixture):
    nodes = {
        index: (float(point[0]), float(point[1]))
        for index, point in enumerate(fixture["coordinates"], start=1)
    }
    ways = {
        record["way_id"]: {
            "nodes": list(record["nodes"]),
            "tags": dict(record["tags"]),
        }
        for record in fixture["ways"]
    }
    features = []
    synthetic_relation_base = 9_000_000_000
    for index, candidate in enumerate(fixture["candidates"]):
        member_ways = list(candidate["member_ways"])
        geometry_way_ids = list(dict.fromkeys(member_ways))
        relation_ids = ([synthetic_relation_base + index]
                        if candidate["source"] == "relation" else [])
        properties = {
            "name": candidate["name"],
            "source": candidate["source"],
            "member_ways": member_ways,
            "length_mi": candidate["length_mi"],
            "ckey": "w" + "-".join(str(way_id)
                                      for way_id in sorted(member_ways)),
            "area": fixture["area"]["name"],
            "_quality_source": {
                "geometry_way_ids": geometry_way_ids,
                "relation_ids": relation_ids,
                "direct_relation_way_ids": (
                    {relation_ids[0]: geometry_way_ids} if relation_ids else {}),
                # The archived artifact predates road restoration; fresh PBF
                # output, not this replay, owns restoration calibration.
                "restored_relation_way_ids": [],
                "missing_way_ids": [way_id for way_id in geometry_way_ids
                                    if way_id not in ways],
            },
        }
        if candidate.get("clipped") is True:
            properties["clipped"] = True
        features.append({
            "type": "Feature",
            "properties": properties,
            "geometry": {
                "type": "MultiLineString",
                "coordinates": [
                    [list(nodes[node_id]) for node_id in line]
                    for line in candidate["lines"]
                ],
            },
        })
    return features, ways, nodes, shape(fixture["area"]["geometry"])


class Run33Replay(unittest.TestCase):
    def test_fixture_drives_actual_sequential_production_curation_by_default(self):
        before = (_FIXTURE.stat().st_size, _FIXTURE.stat().st_mtime_ns)
        fixture = _load_fixture()
        self.assertEqual(fixture["schema_version"], 1)
        self.assertEqual(fixture["source"]["github_run_id"], _RUN_ID)
        self.assertEqual(fixture["source"]["source_sha"], _SOURCE_SHA)
        self.assertEqual(fixture["source"]["attribution"], _ATTRIBUTION)
        self.assertIn("test-only", fixture["description"])
        self.assertEqual(fixture["missing_member_way_ids"], [1027606528])
        self.assertEqual(len(fixture["limitations"]), 3)

        features, ways, nodes, boundary = _production_inputs(fixture)
        kept, removed, report = quality.curate_exact_area(
            features, ways=ways, nodes=nodes, area_union=boundary,
            area_name=_AREA, region="dk")

        ordered = sorted(
            kept + removed,
            key=lambda feature: feature["properties"]["quality_candidate_index"],
        )
        dispositions = [
            feature["properties"].get("removed_category")
            or feature["properties"].get("quality_disposition")
            for feature in ordered
        ]
        removed_by_category = {}
        for feature in removed:
            properties = feature["properties"]
            removed_by_category.setdefault(properties["removed_category"], []).append(
                properties.get("name"))

        self.assertEqual(report["candidate_count"], 70)
        self.assertEqual(report["kept_count"], 35)
        self.assertEqual(report["removed_count"], 35)
        self.assertEqual(dispositions, _EXPECTED_DISPOSITION_SEQUENCE)
        self.assertEqual(removed_by_category, _EXPECTED_REMOVED)
        self.assertEqual(
            [record["name"] for record in report["signed_relation_unresolved"]],
            _EXPECTED_UNRESOLVED,
        )
        self.assertEqual(report["signed_relation_unresolved_count"], 16)
        self.assertEqual(report["signed_relation_unresolved_miles"], 87.222)
        self.assertEqual(
            [record["names"] for record in report["overlap_evidence"]],
            _EXPECTED_OVERLAPS,
        )
        self.assertEqual(report["restored_road_aggregate"]["restored_way_count"], 0)
        self.assertTrue(all(feature["properties"]["area"] == _AREA
                            for feature in ordered))
        maltgaarden = next(feature for feature in kept
                           if feature["properties"]["name"] == "Maltgården")
        self.assertEqual(maltgaarden["properties"]["quality_disposition"],
                         "missing-source-way")
        self.assertTrue(any("missing-source-way unavailable evidence" in error
                            for error in report["validation_failures"]))
        self.assertEqual((_FIXTURE.stat().st_size, _FIXTURE.stat().st_mtime_ns),
                         before)


if __name__ == "__main__":
    unittest.main()
