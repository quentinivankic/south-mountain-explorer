"""publish_areas.py --ca-route-container (Canada registry container_from_routes): the area's boundary is the sealed
derived route container, not a polygon read from the PBF. Every gate refuses closed, and a run without the flag keeps
reading the PBF's polygons exactly as before."""
import importlib.util
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
try:
    import shapely  # noqa: F401
    HAVE_SHAPELY = True
except ImportError:
    HAVE_SHAPELY = False
_HERE = Path(__file__).resolve().parent
_SPEC = importlib.util.spec_from_file_location("publish_areas_container_module", _HERE / "publish_areas.py")
publish = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(publish)
AREA_ID = "coast-ca-nl"
NAME = "Coast Trail"
REL = 3001
INSIDE = [[-52.80, 47.30], [-52.78, 47.30]]                 # on the container's route, about 1.0 mi
OUTSIDE = [[-52.70, 47.30], [-52.68, 47.30]]                # 7.6 km east of it
ROUTES = {10: [(-52.80, 47.30), (-52.78, 47.30)], 11: [(-52.80, 47.31), (-52.78, 47.31)]}
def _sealed(name=NAME, osm_id=REL, evidence=None, tamper=False):
    from shapely.geometry import mapping, shape
    import areas
    nodes, ways = {}, {}
    for way_id, coords in ROUTES.items():
        ways[way_id] = {"tags": {"name": f"Path {way_id}", "highway": "path"}, "nodes": [way_id * 10, way_id * 10 + 1]}
        nodes[way_id * 10], nodes[way_id * 10 + 1] = coords
    relation = {"tags": {"type": "route", "route": "hiking", "network": "none", "name": NAME},
                "members": [("w", w, "") for w in ROUTES]}
    area, failure = areas.ca_route_container({REL: relation}, ways, nodes, REL, ["Path 10", "Path 11"], name, 0.5)
    assert failure is None, failure
    geometry = mapping(area["geom"])
    digest = publish._canonical_json_sha256(shape(geometry).__geo_interface__)
    props = {"name": name, "osm_type": "relation", "osm_id": osm_id, "evidence_type": "route-cluster",
             "route_cluster": {**area["route_cluster"], **(evidence or {})},
             "geometry_sha256": ("0" * 64) if tamper else digest}
    return {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": props, "geometry": geometry}]}
def _trails():
    def feat(trail, line, mi):
        return {"type": "Feature",
                "properties": {"name": trail, "kind": "trail", "source": "name-stitch", "length_mi": mi,
                               "sac_scale": "", "trail_visibility": ""},
                "geometry": {"type": "MultiLineString", "coordinates": [line]}}
    return {"type": "FeatureCollection", "features": [feat("Path 10", INSIDE, 1.0), feat("Far Path", OUTSIDE, 1.0)]}
def _park():
    # a PBF polygon that would hold BOTH trails if it were read
    return [{"name": NAME, "bbox": (-52.9, 47.2, -52.5, 47.4),
             "rings": [[(-52.9, 47.2), (-52.5, 47.2), (-52.5, 47.4), (-52.9, 47.4), (-52.9, 47.2)]],
             "osm_id": 999, "osm_type": "relation"}]
def _publish(state="Canada", sealed="default", rows=None, extra=None, use_flag=True, no_fetch=True):
    with TemporaryDirectory() as tmp:
        work = Path(tmp)
        (work / "out").mkdir()
        (work / "qa").mkdir()
        index = work / "index.json"
        index.write_text(json.dumps(rows or [[AREA_ID, NAME, state, 47.3, -52.8, None, None, REL]]), encoding="utf-8")
        (work / "t.geojson").write_text(json.dumps(_trails()), encoding="utf-8")
        preview = work / "qa" / "preview.json"
        argv = ["--trails", str(work / "t.geojson"), "--hiking", str(work / "x.osm.pbf"), "--index", str(index),
                "--out-dir", str(work / "out"), "--state", state, "--dry-run", "--no-elevation",
                "--preview-json", str(preview), "--preview-root", str(work / "qa")]
        if no_fetch:
            argv.append("--no-boundary-fetch")
        if use_flag:
            (work / "sealed.json").write_text(json.dumps(_sealed() if sealed == "default" else sealed), encoding="utf-8")
            argv += ["--ca-route-container", str(work / "sealed.json")]
        argv += extra or []
        out, err = io.StringIO(), io.StringIO()
        with (patch.object(publish.areamod, "merge_areas", return_value=_park()) as merge,
              redirect_stdout(out), redirect_stderr(err)):
            try:
                rc = publish.main(argv)
            except SystemExit as exit_:
                rc = exit_.code
        rows_out = json.loads(preview.read_text())["areas"] if preview.exists() else []
        by_name = {t["name"]: t for row in rows_out for t in row["trails"]}
        return rc, by_name, merge.call_count, out.getvalue(), err.getvalue()
@unittest.skipUnless(HAVE_SHAPELY, "shapely not installed")
class Publish(unittest.TestCase):
    def test_the_sealed_container_is_the_boundary_and_the_pbf_polygons_are_not_read(self):
        rc, trails, merged, _, err = _publish()
        self.assertEqual(rc, 0, err)
        self.assertEqual(merged, 0)                                       # merge_areas (PBF polygons) never called
        self.assertEqual(sorted(trails), ["Path 10"])                     # Far Path lies outside the container
    def test_control_without_the_flag_reads_the_pbf_polygon_exactly_as_before(self):
        rc, trails, merged, _, err = _publish(use_flag=False)
        self.assertEqual(rc, 0, err)
        self.assertEqual(merged, 1)
        self.assertEqual(sorted(trails), ["Far Path", "Path 10"])         # the big polygon holds both
    def test_only_canada_may_use_the_flag(self):
        for state in ("Denmark", "Colorado", "Rhode Island", "canada"):
            with self.subTest(state):
                rc, trails, _, _, err = _publish(state=state)
                self.assertEqual(rc, 2)
                self.assertEqual(trails, {})
                self.assertIn("--ca-route-container is for --state Canada only", err)
    def test_never_with_exact_boundary_or_only_area_and_always_with_no_boundary_fetch(self):
        for extra in (["--exact-boundary", "x"], ["--only-area", AREA_ID]):
            with self.subTest(extra):
                rc, trails, _, _, err = _publish(extra=extra)
                self.assertEqual(rc, 2)
                self.assertIn("--ca-route-container is for --state Canada only", err)
        rc, trails, _, _, err = _publish(no_fetch=False)
        self.assertEqual(rc, 2)
        self.assertIn("--ca-route-container is for --state Canada only", err)
    def test_exactly_one_indexed_area(self):
        rows = [[AREA_ID, NAME, "Canada", 47.3, -52.8, None, None, REL],
                ["other-ca-nl", "Other", "Canada", 47.3, -52.8, None, None, 5]]
        rc, trails, _, _, err = _publish(rows=rows)
        self.assertEqual(rc, 2)
        self.assertIn("exactly one indexed area", err)
    def test_identity_must_agree_with_the_indexed_area(self):
        for label, sealed in (("name", _sealed(name="Other Name")), ("osm id", _sealed(osm_id=REL + 1, evidence={"anchor_relation_id": REL + 1}))):
            with self.subTest(label):
                rc, trails, _, _, err = _publish(sealed=sealed)
                self.assertEqual(rc, 2)
                self.assertEqual(trails, {})
                self.assertIn("identity disagrees with the indexed area", err)
        rows = [[AREA_ID, NAME, "Canada", 47.3, -52.8, None, None, None]]            # no relation id in the row
        rc, _, _, _, err = _publish(rows=rows)
        self.assertEqual(rc, 2)
    def test_the_seal_must_be_a_canada_route_container(self):
        for label, evidence in (("region", {"region": "dk"}), ("version", {"version": "route-cluster-v1"}),
                                ("anchor", {"anchor_relation_id": 1})):
            with self.subTest(label):
                rc, _, _, _, err = _publish(sealed=_sealed(evidence=evidence))
                self.assertEqual(rc, 2)
                self.assertIn("not a sealed Canada route container", err)
        sealed = _sealed()
        del sealed["features"][0]["properties"]["evidence_type"]
        rc, _, _, _, err = _publish(sealed=sealed)
        self.assertEqual(rc, 2)
        self.assertIn("not a sealed Canada route container", err)
    def test_a_tampered_geometry_hash_is_refused(self):
        rc, trails, _, _, err = _publish(sealed=_sealed(tamper=True))
        self.assertEqual(rc, 2)
        self.assertEqual(trails, {})
        self.assertIn("geometry hash is invalid", err)
    def test_the_version_constant_is_the_gate(self):
        sealed = _sealed()                                                # sealed with the real version
        with patch.object(publish.areamod, "CA_ROUTE_CONTAINER_VERSION", "route-cluster-v1"):
            rc, _, _, _, err = _publish(sealed=sealed)
        self.assertEqual(rc, 2)
        self.assertIn("not a sealed Canada route container", err)
if __name__ == "__main__":
    unittest.main()
