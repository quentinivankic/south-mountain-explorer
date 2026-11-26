"""publish_areas.py honours the Canada registry whole_route stamp (`ca_whole_route`, set by assemble.py) for
--state Canada only. Any other state, and any feature without the stamp, takes the unchanged clamp path."""
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
_SPEC = importlib.util.spec_from_file_location("publish_areas_whole_module", _HERE / "publish_areas.py")
publish = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(publish)

AREA_ID = "test-park-ca-bc"
NAME = "Test Provincial Park"
REL = 424242
EAST = 10.9                    # the polygon's east edge
FAR = 11.2                     # where the whole route ends


def _trails(extra=None, end=FAR):
    props = {"name": "Long Ridge Trail", "kind": "trail", "source": "name-stitch", "length_mi": 20.0,
             "sac_scale": "", "trail_visibility": ""}
    props.update(extra or {})
    return {"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": props,
        "geometry": {"type": "MultiLineString", "coordinates": [[[10.5, 56.15], [end, 56.15]]]}}]}


def _boundary():
    return [{"name": NAME, "bbox": (10.4, 56.08, EAST, 56.35),
             "rings": [[(10.4, 56.08), (EAST, 56.08), (EAST, 56.35), (10.4, 56.35), (10.4, 56.08)]],
             "osm_id": REL, "osm_type": "relation"}]


def _publish(trails, state):
    with TemporaryDirectory() as tmp:
        work = Path(tmp)
        (work / "out").mkdir()
        (work / "qa").mkdir()
        index = work / "index.json"
        index.write_text(json.dumps([[AREA_ID, NAME, state, 56.2, 10.6, None, None, REL]]), encoding="utf-8")
        (work / "t.geojson").write_text(json.dumps(trails), encoding="utf-8")
        preview = work / "qa" / "preview.json"
        argv = ["--trails", str(work / "t.geojson"), "--hiking", str(work / "x.osm.pbf"), "--index", str(index),
                "--out-dir", str(work / "out"), "--state", state, "--dry-run", "--no-boundary-fetch",
                "--no-elevation", "--preview-json", str(preview), "--preview-root", str(work / "qa")]
        with (patch.object(publish.areamod, "merge_areas", return_value=_boundary()),
              redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO())):
            rc = publish.main(argv)
        rows = json.loads(preview.read_text())["areas"] if preview.exists() else []
        trails_out = rows[0]["trails"] if rows else []
        east = max((p[1] for t in trails_out for seg in t["segments"] for p in seg), default=None)
        return rc, trails_out, east


class Predicate(unittest.TestCase):
    def test_gate_truth_table(self):
        whole = publish._is_whole_route
        self.assertTrue(whole({"ca_whole_route": True}, "Canada", False))
        self.assertTrue(whole({"ca_whole_route": True}, "Canada", True))
        self.assertTrue(whole({"dk_whole_route": True}, "Denmark", True))
        self.assertFalse(whole({"ca_whole_route": True}, "Denmark", True))
        self.assertFalse(whole({"ca_whole_route": True}, "Colorado", False))
        self.assertFalse(whole({"ca_whole_route": True}, "canada", False))
        self.assertFalse(whole({"dk_whole_route": True}, "Canada", False))      # Denmark stamp needs --exact-boundary
        self.assertFalse(whole({}, "Canada", False))
        self.assertFalse(whole({"ca_whole_route": False}, "Canada", False))


@unittest.skipUnless(HAVE_SHAPELY, "shapely not installed")
class Publish(unittest.TestCase):
    def test_canada_whole_route_keeps_the_complete_geometry(self):
        rc, trails, east = _publish(_trails({"ca_whole_route": True, "ca_inside_mi": 12.0,
                                             "ca_route_share": 0.8}), "Canada")
        self.assertEqual(rc, 0)
        self.assertEqual(len(trails), 1)
        self.assertAlmostEqual(east, FAR, places=4)
        self.assertEqual(trails[0]["distanceMi"], 20.0)

    def test_control_without_the_stamp_is_clamped_to_the_polygon(self):
        rc, trails, east = _publish(_trails(), "Canada")
        self.assertEqual(rc, 0)
        self.assertLessEqual(east, EAST + 1e-6)

    def test_the_stamp_is_ignored_for_any_other_state(self):
        for state in ("Denmark", "Colorado", "Rhode Island"):
            with self.subTest(state):
                rc, _, east = _publish(_trails({"ca_whole_route": True}), state)
                self.assertEqual(rc, 0)
                if east is not None:
                    self.assertLessEqual(east, EAST + 1e-6)


if __name__ == "__main__":
    unittest.main()
