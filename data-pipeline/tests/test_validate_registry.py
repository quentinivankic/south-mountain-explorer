"""Tests for the fail-closed licensing gate (spec §2, §9)."""
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sources"))

import validate_registry as vr  # noqa: E402

REGISTRY = vr.load_registry(ROOT / "sources" / "registry.json")


class GateSemantics(unittest.TestCase):
    def test_true_true_ships(self):
        self.assertTrue(vr.is_shippable({"commercial_ok": True, "redistribute_ok": True}))

    def test_null_fails_closed(self):
        # The whole point: unverified (null) is a denial, not a default-allow.
        self.assertFalse(vr.is_shippable({"commercial_ok": None, "redistribute_ok": None}))
        self.assertFalse(vr.is_shippable({"commercial_ok": True, "redistribute_ok": None}))
        self.assertFalse(vr.is_shippable({"commercial_ok": None, "redistribute_ok": True}))

    def test_false_fails(self):
        self.assertFalse(vr.is_shippable({"commercial_ok": False, "redistribute_ok": True}))
        self.assertFalse(vr.is_shippable({"commercial_ok": True, "redistribute_ok": False}))

    def test_missing_keys_fail(self):
        self.assertFalse(vr.is_shippable({}))

    def test_truthy_nonbool_does_not_sneak_through(self):
        # `is True` guard: a truthy string/int must NOT pass.
        self.assertFalse(vr.is_shippable({"commercial_ok": 1, "redistribute_ok": "yes"}))


class RealRegistry(unittest.TestCase):
    def test_structure_valid(self):
        self.assertEqual(vr.validate_structure(REGISTRY), [])

    def test_nz_pilot_sources_ship(self):
        blocked = vr.assert_region_shippable(REGISTRY, ["osm", "nz_doc", "nz_linz"])
        self.assertEqual(blocked, [])

    def test_sernanp_blocked(self):
        by_id = vr.sources_by_id(REGISTRY)
        self.assertIn("pe_sernanp", by_id)
        self.assertFalse(vr.is_shippable(by_id["pe_sernanp"]))
        self.assertIn("pe_sernanp", vr.assert_region_shippable(REGISTRY, ["pe_sernanp"]))

    def test_null_flag_sources_blocked(self):
        # bc_rstbc / bfn / cl_mbn etc. carry null flags -> must be blocked.
        for sid in ("bc_rstbc", "bfn", "nls_fi", "cl_mbn", "ar_apn", "za_sanparks", "osi_ie"):
            self.assertIn(sid, vr.assert_region_shippable(REGISTRY, [sid]),
                          f"{sid} with null flags must fail closed")

    def test_unknown_source_blocked(self):
        self.assertEqual(vr.assert_region_shippable(REGISTRY, ["does_not_exist"]),
                         ["does_not_exist"])

    def test_wdpa_on_blocklist_not_in_sources(self):
        self.assertIn("wdpa", vr.prohibited_ids(REGISTRY))
        self.assertNotIn("wdpa", vr.sources_by_id(REGISTRY))


class StructuralValidation(unittest.TestCase):
    def test_prohibited_id_in_sources_is_error(self):
        bad = {"_meta": {"fail_closed": True},
               "sources": [{"id": "wdpa", "name": "x", "kind": "areas", "license": "?",
                            "commercial_ok": True, "redistribute_ok": True, "attribution": "x"}],
               "prohibited_sources": [{"id": "wdpa"}]}
        self.assertTrue(any("prohibited" in e for e in vr.validate_structure(bad)))

    def test_duplicate_id_is_error(self):
        s = {"id": "a", "name": "x", "kind": "areas", "license": "?",
             "commercial_ok": True, "redistribute_ok": True, "attribution": "x"}
        bad = {"_meta": {"fail_closed": True}, "sources": [dict(s), dict(s)]}
        self.assertTrue(any("duplicate" in e for e in vr.validate_structure(bad)))

    def test_fail_closed_meta_required(self):
        bad = {"_meta": {}, "sources": []}
        self.assertTrue(any("fail_closed" in e for e in vr.validate_structure(bad)))


if __name__ == "__main__":
    unittest.main()
