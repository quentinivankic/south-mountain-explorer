"""Offline deterministic controls for the Mols evidence SVG."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

_HERE = Path(__file__).resolve().parent
_SPEC = importlib.util.spec_from_file_location(
    "build_mols_visual_module", _HERE / "build_mols_pilot_visual.py")
visual = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(visual)


def _feature(geometry, **properties):
    return {"type": "Feature", "properties": properties,
            "geometry": geometry}


class MolsPilotVisual(unittest.TestCase):
    def _documents(self):
        boundary = {
            "type": "Polygon",
            "coordinates": [[
                [10.0, 56.0], [11.0, 56.0], [11.0, 57.0],
                [10.0, 57.0], [10.0, 56.0],
            ]],
        }
        raw = {"features": [
            _feature({"type": "LineString",
                      "coordinates": [[10.1, 56.1], [10.9, 56.9]]}),
            _feature({"type": "Polygon", "coordinates": [[
                [10.2, 56.2], [10.3, 56.2], [10.3, 56.3],
                [10.2, 56.2],
            ]]}),
        ]}
        areas = {"features": [
            _feature(boundary, name=visual.AREA_NAME),
        ]}
        trails = {"features": [
            _feature({"type": "MultiLineString", "coordinates": [
                [[10.1, 56.1], [10.4, 56.4]],
                [[10.6, 56.6], [10.9, 56.9]],
            ]}, name="Unresolved", quality_disposition="unresolved-relation-gap"),
        ]}
        removed = {"features": [
            _feature({"type": "LineString",
                      "coordinates": [[10.2, 56.8], [10.4, 56.8]]},
                     name="Removed"),
        ]}
        authority = {"relations": [{
            "direct_ways": [
                {"way_id": 10, "status": "present",
                 "coordinates": [[10.1, 56.1], [10.4, 56.4]]},
                {"way_id": 11, "status": "present",
                 "coordinates": [[10.4, 56.4], [10.6, 56.6]]},
            ],
        }]}
        assembly = {"quality": {"relation_member_audit": [{
            "direct_way_members": [
                {"way_id": 10, "status": "included"},
                {"way_id": 11, "status": "excluded"},
            ],
        }]}}
        return raw, areas, trails, removed, authority, assembly

    def test_svg_is_deterministic_self_contained_and_complete(self):
        documents = self._documents()
        first, first_review = visual.build_svg(
            raw=documents[0], areas=documents[1], trails=documents[2],
            removed=documents[3], authority=documents[4], assembly=documents[5])
        second, second_review = visual.build_svg(
            raw=documents[0], areas=documents[1], trails=documents[2],
            removed=documents[3], authority=documents[4], assembly=documents[5])

        self.assertEqual(first, second)
        self.assertEqual(first_review, second_review)
        text = first.decode("utf-8")
        self.assertIn('id="raw-osm-context"', text)
        self.assertIn('id="exact-boundary"', text)
        self.assertIn('id="safe-relation-members"', text)
        self.assertIn('data-way="10"', text)
        self.assertIn('data-way="11"', text)
        self.assertEqual(text.count('class="unresolved-endpoint"'), 4)
        self.assertNotIn("<image", text)
        self.assertNotIn("href=", text)
        self.assertEqual(text.count("http://"), 1)
        self.assertIn('xmlns="http://www.w3.org/2000/svg"', text)
        self.assertNotIn("https://", text)
        self.assertFalse(first_review["external_network_resources"])
        self.assertFalse(first_review["raster_or_external_tiles"])
        self.assertEqual(first_review["layers"], {
            "raw_context_paths": 2,
            "exact_boundary_paths": 1,
            "emitted_paths": 2,
            "removed_paths": 1,
            "safe_relation_member_paths": 1,
            "unsafe_relation_member_paths": 1,
            "unresolved_endpoint_markers": 4,
        })

    def test_clean_package_has_exactly_zero_unresolved_markers(self):
        raw, areas, trails, removed, authority, assembly = self._documents()
        trails["features"][0]["properties"]["quality_disposition"] = "kept"
        raw_svg, review = visual.build_svg(
            raw=raw, areas=areas, trails=trails, removed=removed,
            authority=authority, assembly=assembly)
        self.assertEqual(review["layers"]["unresolved_endpoint_markers"], 0)
        self.assertNotIn(
            'class="unresolved-endpoint"', raw_svg.decode("utf-8"))

    def test_missing_or_duplicate_exact_boundary_fails(self):
        raw, areas, trails, removed, authority, assembly = self._documents()
        for features in ([], areas["features"] * 2):
            with self.subTest(count=len(features)):
                with self.assertRaisesRegex(ValueError, "exactly one"):
                    visual.build_svg(
                        raw=raw, areas={"features": features}, trails=trails,
                        removed=removed, authority=authority,
                        assembly=assembly)


if __name__ == "__main__":
    unittest.main()
