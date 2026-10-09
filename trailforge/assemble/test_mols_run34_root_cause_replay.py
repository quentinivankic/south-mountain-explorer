"""Immutable regression contract for the run-34 Denmark root-cause evidence."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import unittest

_HERE = Path(__file__).resolve().parent
_TESTDATA = _HERE / "testdata"
_FIXTURE_PATH = _TESTDATA / "mols-run34-root-cause-replay.json"
_MANIFEST_PATH = _TESTDATA / "mols-run34-root-cause-replay.manifest.json"


def _load():
    return (json.loads(_FIXTURE_PATH.read_text(encoding="utf-8")),
            json.loads(_MANIFEST_PATH.read_text(encoding="utf-8")))


def _member_class(row):
    tags = row["tags"]
    roles = set(row["effective_roles"])
    if roles.intersection({"alternative", "alternate", "excursion"}):
        return "variant-role"
    if (str(tags.get("foot", "")).strip().casefold() == "private"
            or str(tags.get("service", "")).strip().casefold()
            in {"parking_aisle", "driveway"}):
        return "unsafe"
    highway = str(tags.get("highway", "")).strip().casefold()
    if not highway or str(tags.get("route", "")).strip().casefold() == "ferry":
        return "context"
    return "default-foot"


class MolsRun34RootCauseReplay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture, cls.manifest = _load()

    def test_fixture_is_canonical_manifest_bound_and_odbl_attributed(self):
        raw = _FIXTURE_PATH.read_bytes()
        canonical = (json.dumps(
            self.fixture, ensure_ascii=False, sort_keys=True,
            separators=(",", ":")) + "\n").encode("utf-8")
        self.assertEqual(raw, canonical)
        self.assertEqual(len(raw), self.manifest["output"]["bytes"])
        self.assertEqual(hashlib.sha256(raw).hexdigest(),
                         self.manifest["output"]["sha256"])
        provenance = self.fixture["provenance"]
        self.assertEqual(provenance["github_run_id"], 37213946834)
        self.assertEqual(
            provenance["source_sha"],
            "c2357749ed06dc1860c8ce469a926ed9f108ade8")
        self.assertEqual(provenance["attribution"],
                         "© OpenStreetMap contributors")
        self.assertEqual(provenance["license"],
                         "Open Database License (ODbL) 1.0")
        self.assertEqual(provenance["geometry_source"], "OpenStreetMap")

    def test_missing_partition_reproduces_1479_86_106_and_nested_scope(self):
        roots = {row["relation_id"]: set(row["missing_way_ids"])
                 for row in self.fixture["missing_root_audits"]}
        removed = roots[2006949] | roots[2203180]
        emitted = roots[178382] | roots[4603299] | roots[14190349]
        self.assertEqual(len(removed - emitted), 1479)
        self.assertEqual(len(emitted - removed), 86)
        self.assertEqual(len(removed & emitted), 106)
        self.assertEqual(len(removed | emitted), 1671)
        rows = {row["relation_id"]: row
                for row in self.fixture["missing_root_audits"]}
        self.assertEqual(rows[2006949]["missing_relation_ids"],
                         [1166772, 1970035])
        self.assertTrue(all(
            not rows[root]["missing_relation_ids"]
            for root in (178382, 2203180, 4603299, 14190349)))
        self.assertEqual(rows[2006949]["assembly_status"],
                         "removed-thru-hike")
        self.assertEqual(rows[2203180]["assembly_status"],
                         "removed-thru-hike")

    def test_available_exclusions_reproduce_117_7_3_1_classes(self):
        rows = self.fixture["available_exclusions"]
        self.assertEqual(len(rows), 128)
        self.assertEqual(
            Counter(_member_class(row) for row in rows),
            Counter({
                "default-foot": 117,
                "unsafe": 7,
                "context": 3,
                "variant-role": 1,
            }),
        )
        unsafe = {row["way_id"] for row in rows
                  if _member_class(row) == "unsafe"}
        self.assertEqual(unsafe, {
            158830579, 324442353, 1288399597, 532412676,
            532412678, 273639315, 1426251464,
        })

    def test_all_eleven_component_rows_and_expected_dispositions_are_frozen(self):
        rows = self.fixture["component_rows"]
        self.assertEqual(len(rows), 11)
        by_name = {row["name"]: row for row in rows}
        self.assertEqual(
            (by_name["Molsruten"]["run34_source_components"],
             by_name["Molsruten"]["run34_postclip_components"],
             by_name["Molsruten"]["safe_source_components"],
             by_name["Molsruten"]["safe_postclip_components"]),
            (11, 10, 2, 5),
        )
        resolved = {
            name for name, row in by_name.items()
            if row["expected_run35_disposition"] in {
                "boundary-induced-after-full-authority",
                "boundary-induced-after-default-foot",
                "resolved-by-default-foot",
            }
        }
        self.assertEqual(resolved, {
            "Molsruten", "Kaløstien", "Dråby Sporet. Blå rute",
            "Djurslandstien", "Mols Bjerge-stien Kaløetapen",
            "Kløverstier-Naturruten", "Kløverstier-Landsbyruten",
        })
        self.assertEqual(
            {name for name, row in by_name.items()
             if row["expected_run35_disposition"] == "unsafe-in-area"},
            {"Mols Bjerge-stien Ebeltoftetapen",
             "Mols Bjerge-stien Gåsehage-etapen"},
        )
        self.assertEqual(
            {name for name, row in by_name.items()
             if row["expected_run35_disposition"] == "unresolved-topology"},
            {"Langs Kysten", "Den gamle købstad"},
        )

    def test_maltgaarden_is_closed_standalone_pedestrian_area(self):
        malt = self.fixture["maltgaarden"]
        self.assertEqual(malt["name"], "Maltgården")
        self.assertEqual(malt["way_id"], 1027606528)
        self.assertEqual(malt["source"], "name-stitch")
        self.assertEqual(malt["relation_ids"], [])
        self.assertEqual(malt["tags"], {
            "name": "Maltgården",
            "highway": "pedestrian",
            "surface": "sett",
        })
        self.assertEqual(len(malt["node_ids"]), 51)
        self.assertEqual(malt["node_ids"][0], malt["node_ids"][-1])
        self.assertEqual(malt["coordinates"][0], malt["coordinates"][-1])

    def test_all_27_road_rows_and_full_member_diagnostics_are_preserved(self):
        rows = self.fixture["restored_road_rows"]
        self.assertEqual(len(rows), 27)
        self.assertEqual([row["relation_id"] for row in rows], [
            178382, 4603299, 12875831, 14190349, 177276, 178383,
            8348510, 8348558, 8350111, 8351111, 8351151, 10507412,
            10523727, 11350069, 11350070, 11350072, 11789766,
            11964103, 13944294, 18499734, 18500586, 18500727,
            18501031, 19113817, 19113818, 19627372, 20047139,
        ])
        aggregate = self.fixture["restored_road_aggregate"]
        self.assertEqual(aggregate, {
            "restored_way_count": 185,
            "restored_miles": 27.155889,
            "total_relation_miles": 132.61264,
            "restored_share": 0.204776,
        })
        tags = {row["way_id"]: row["tags"]
                for row in self.fixture["restored_way_tags"]}
        every_restored = {way_id for row in rows
                          for way_id in row["restored_way_ids"]}
        self.assertEqual(set(tags), every_restored)
        self.assertTrue(all("highway" in value for value in tags.values()))
        self.assertEqual(tags[36908522]["name"], "Skærsø Skovvej")
        self.assertEqual(tags[36908522]["lanes"], "1")
        self.assertEqual(tags[36908522]["surface"], "gravel")
        bjergetapen = next(row for row in rows
                           if row["relation_id"] == 8350111)
        self.assertEqual(bjergetapen["restored_miles"], 1.189852)
        self.assertEqual(bjergetapen["total_relation_miles"], 12.557509)
        self.assertEqual(bjergetapen["restored_share"], 0.094752)

    def test_fixture_is_not_referenced_by_runtime_modules(self):
        needle = _FIXTURE_PATH.name
        runtime_files = [
            _HERE / "assemble.py", _HERE / "model.py", _HERE / "quality.py",
            _HERE.parent / "serve" / "publish_areas.py",
            _HERE.parent / "tools" / "validate_publish_pilot.py",
        ]
        self.assertTrue(all(
            needle not in path.read_text(encoding="utf-8")
            for path in runtime_files
        ))


if __name__ == "__main__":
    unittest.main()
