from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[1]
_LEDGER = _HERE.parent / "golden" / "mols-bjerge-local-dispositions.json"
_FIXTURE = (
    _HERE.parent / "assemble" / "testdata" /
    "mols-run35-root-cause-replay.json"
)
sys.path.insert(0, str(_HERE))
import validate_mols_local_dispositions as validator  # noqa: E402


class MolsLocalDispositionValidation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = _LEDGER.read_bytes()
        cls.fixture = validator._strict_json(
            _FIXTURE.read_bytes(), "replay fixture")
        cls.document = validator._strict_json(
            cls.raw, "local disposition ledger")

    def _mutated_errors(self, mutate):
        document = copy.deepcopy(self.document)
        mutate(document)
        raw = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode()
        _parsed, errors = validator.validate_ledger_bytes(raw, self.fixture)
        self.assertIn(
            "local disposition ledger exact-byte hash is wrong", errors)
        return errors

    def test_exact_ledger_and_frozen_fixture_pass(self):
        document, errors = validator.validate_ledger_bytes(
            self.raw, self.fixture)
        self.assertEqual(errors, [])
        self.assertEqual(document, self.document)
        self.assertEqual(
            validator._sha256(self.raw), validator.EXPECTED_LEDGER_SHA256)

    def test_five_omissions_are_visible_but_keep_genuine_residuals(self):
        decisions = self.document["decisions"][:5]
        self.assertEqual(
            [row["trail"]["osm_relation_id"] for row in decisions],
            [10507412, 13944294, 14190349, 19113817, 19113818])
        self.assertTrue(all(
            row["local_disposition"] ==
            "explicitly-omitted-from-this-snapshot-local-acceptance"
            and row["genuine_residual"]["producer_quality_disposition"] ==
            "unresolved-relation-gap"
            and row["status"] == "accepted"
            for row in decisions))
        self.assertEqual(len(self.document["preserved_genuine_errors"]), 9)
        self.assertEqual(
            self.document["preserved_genuine_errors"],
            validator.EXPECTED_FINAL_ERRORS)

    def test_duplicate_json_key_fails_closed(self):
        raw = self.raw.replace(
            b'{\n  "schema_version": 1,',
            b'{\n  "schema_version": 1,\n  "schema_version": 1,', 1)
        document, errors = validator.validate_ledger_bytes(raw, self.fixture)
        self.assertIsNone(document)
        self.assertTrue(any("duplicate JSON key" in error for error in errors))

    def test_missing_reordered_or_duplicated_decisions_fail(self):
        attacks = {
            "missing": lambda value: value["decisions"].pop(2),
            "reordered": lambda value: value["decisions"].reverse(),
            "duplicated": lambda value: value["decisions"].append(
                copy.deepcopy(value["decisions"][0])),
        }
        for label, attack in attacks.items():
            with self.subTest(label=label):
                errors = self._mutated_errors(attack)
                self.assertTrue(any(
                    "decision" in error for error in errors), errors)

    def test_suppress_relabel_waive_or_delete_attacks_fail(self):
        for field in (
                "expected_errors_suppressed", "expected_errors_relabelled",
                "expected_errors_waived", "expected_errors_deleted"):
            with self.subTest(field=field):
                errors = self._mutated_errors(
                    lambda value, field=field:
                    value["effects"].__setitem__(field, True))
                self.assertIn(
                    f"observational ledger cannot set {field}", errors)
        errors = self._mutated_errors(
            lambda value: value["preserved_genuine_errors"].pop())
        self.assertIn(
            "ledger must preserve the exact ordered final nine errors", errors)

    def test_geometry_substitution_and_connectivity_attacks_fail(self):
        attacks = {
            "snap": ("coordinate_snapping", True),
            "tolerance": ("coordinate_tolerance_m", 0.385),
            "connector": ("synthetic_connectors", True),
            "unsafe-restore": ("unsafe_way_restoration", True),
            "visitdenmark-geometry": ("visitdenmark_geometry", True),
            "non-exact-node": (
                "connectivity_authority", "coordinate-nearness"),
        }
        for label, (field, value) in attacks.items():
            with self.subTest(label=label):
                errors = self._mutated_errors(
                    lambda document, field=field, value=value:
                    document["safety_policy"].__setitem__(field, value))
                self.assertTrue(any(
                    field.replace("_", " ").split()[0] in error
                    or "connectivity" in error or "tolerance" in error
                    for error in errors), errors)

    def test_relation_snapshot_or_boundary_substitution_fails(self):
        attacks = [
            lambda value: value["decisions"][0]["trail"].__setitem__(
                "osm_relation_id", 999),
            lambda value: value["snapshot"].__setitem__(
                "implementation_commit", "0" * 40),
            lambda value: value["scope"].__setitem__(
                "boundary_osm_relation_id", 1),
        ]
        for attack in attacks:
            with self.subTest(attack=attack):
                errors = self._mutated_errors(attack)
                self.assertGreater(len(errors), 1)

    def test_unsafe_aisle_omission_or_invention_fails(self):
        attacks = [
            lambda value: value["safety_policy"][
                "excluded_parking_aisle_way_ids"].pop(),
            lambda value: value["decisions"][0]["genuine_residual"][
                "excluded_way_ids"].clear(),
            lambda value: value["decisions"][1]["genuine_residual"][
                "excluded_way_ids"].append(999),
        ]
        for attack in attacks:
            with self.subTest(attack=attack):
                errors = self._mutated_errors(attack)
                self.assertTrue(any(
                    "unsafe" in error or "parking aisles" in error
                    for error in errors), errors)

    def test_distinct_0385_m_nodes_cannot_be_snapped(self):
        def attack(value):
            residual = value["decisions"][4]["genuine_residual"]
            residual["nodes_are_distinct"] = False
            residual["coordinate_tolerance_m"] = 0.385

        errors = self._mutated_errors(attack)
        self.assertIn(
            "D5 cannot snap or apply tolerance to distinct nodes", errors)

    def test_bjergetapen_occurrence_and_ckey_attacks_fail(self):
        attacks = [
            lambda value: value["decisions"][5]["identity"].__setitem__(
                "raw_way_occurrence_count", 82),
            lambda value: value["decisions"][5]["identity"].__setitem__(
                "unique_raw_way_count", 85),
            lambda value: value["decisions"][5]["identity"].__setitem__(
                "ckey_sha256", "0" * 64),
            lambda value: value["decisions"][5]["trail"].__setitem__(
                "osm_relation_id", 1),
        ]
        for attack in attacks:
            with self.subTest(attack=attack):
                errors = self._mutated_errors(attack)
                self.assertGreater(len(errors), 1)

    def test_visual_acceptance_cannot_authorize_redesign_or_publication(self):
        for field in ("visual_redesign_authorized", "publication_authority"):
            with self.subTest(field=field):
                errors = self._mutated_errors(
                    lambda value, field=field:
                    value["decisions"][6].__setitem__(field, True))
                self.assertTrue(any(
                    "redesign or publication" in error for error in errors))

    def test_run_38181006310_remains_final_with_no_retry(self):
        attacks = [
            lambda value: value["decisions"][7].__setitem__(
                "retry_authorized", True),
            lambda value: value["decisions"][7].__setitem__(
                "retry_performed", True),
            lambda value: value["decisions"][7]["run"].__setitem__(
                "conclusion", "success"),
            lambda value: value["decisions"][7].__setitem__(
                "hosted_success_claimed", True),
        ]
        for attack in attacks:
            with self.subTest(attack=attack):
                errors = self._mutated_errors(attack)
                self.assertTrue(any("D8" in error for error in errors), errors)

    def test_unsafe_or_duplicate_evidence_inventory_fails(self):
        def traversal(value):
            value["evidence_files"][0]["path"] = "../escape"

        errors = self._mutated_errors(traversal)
        self.assertTrue(any("unsafe evidence path" in error for error in errors))

        def duplicate(value):
            value["evidence_files"][1]["key"] = value[
                "evidence_files"][0]["key"]

        errors = self._mutated_errors(duplicate)
        self.assertIn(
            "evidence file inventory must have 17 unique keys", errors)

    def test_archive_reader_rejects_traversal_and_symlinks(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.write_text("evidence", encoding="utf-8")
            link = root / "link"
            link.symlink_to(target)
            with self.assertRaisesRegex(
                    validator.DispositionError, "unsafe archive path"):
                validator._safe_archive_file(root, "../target")
            with self.assertRaisesRegex(
                    validator.DispositionError, "uses symlink"):
                validator._safe_archive_file(root, "link")

    def test_runtime_modules_do_not_consume_local_disposition_ledger(self):
        needle = _LEDGER.name
        runtime_paths = [
            _HERE.parent / "assemble" / "assemble.py",
            _HERE.parent / "assemble" / "model.py",
            _HERE.parent / "assemble" / "quality.py",
            _HERE.parent / "serve" / "publish_areas.py",
            _HERE / "validate_publish_pilot.py",
        ]
        self.assertTrue(all(
            needle not in path.read_text(encoding="utf-8")
            for path in runtime_paths))

    def test_record_paths_are_the_only_new_runtime_independent_scope(self):
        self.assertEqual(_LEDGER.parent.name, "golden")
        self.assertEqual(_FIXTURE.name, "mols-run35-root-cause-replay.json")
        self.assertTrue(_LEDGER.is_file())
        self.assertTrue(_FIXTURE.is_file())


    def test_source_way_fidelity_allows_tag_subset_but_rejects_node_change(self):
        relation = {
            "direct_ways": [{
                "way_id": 7,
                "node_ids": [1, 2],
                "coordinates": [[10.0, 56.0], [10.1, 56.1]],
                "missing_node_ids": [],
                "tags": {"highway": "path", "source": "complete-raw-tag"},
            }],
        }
        feature = {"properties": {
            "source_geometry_way_ids": [7],
            "source_ways": [{
                "way_id": 7,
                "node_ids": [1, 2],
                "coordinates": [[10.0, 56.0], [10.1, 56.1]],
                "missing_node_ids": [],
                "tags": {"highway": "path"},
            }],
        }}
        errors = []
        validator._validate_source_way_fidelity(
            feature, relation, "fixture", errors)
        self.assertEqual(errors, [])
        feature["properties"]["source_ways"][0]["node_ids"].reverse()
        validator._validate_source_way_fidelity(
            feature, relation, "fixture", errors)
        self.assertIn("fixture source way 7 changed node_ids", errors)

if __name__ == "__main__":
    unittest.main()
