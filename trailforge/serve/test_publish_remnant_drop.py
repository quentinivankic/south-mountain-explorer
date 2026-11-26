"""publish_areas.py --ca-remnant-drop (Canada registry remnant_drop): the named trail loses its stray pieces, every
other trail and every run without the flag is unchanged, any other --state refuses the flag, and an entry that cannot
be applied fails the area."""
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
_SPEC = importlib.util.spec_from_file_location("publish_areas_remnant_module", _HERE / "publish_areas.py")
publish = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(publish)

AREA_ID = "test-park-ca-bc"
NAME = "Test Provincial Park"
REL = 424242
SOURCE = "https://example.gc.ca/parks/ridge"
ENTRY = {"name": "Ridge Trail", "source": SOURCE, "reason": "stray remnant"}
MAIN = [[10.50, 56.15], [10.56, 56.15]]            # about 2.3 mi
STRAY = [[10.52, 56.20], [10.5215, 56.20]]         # about 0.06 mi, 5.5 km north of the main piece (inside the polygon)
OTHER = [[10.60, 56.30], [10.64, 56.30]]
OTHER_STRAY = [[10.70, 56.10], [10.7012, 56.10]]


def _trails(name="Ridge Trail"):
    def feat(trail, lines, mi):
        return {"type": "Feature",
                "properties": {"name": trail, "kind": "trail", "source": "name-stitch", "length_mi": mi,
                               "sac_scale": "", "trail_visibility": ""},
                "geometry": {"type": "MultiLineString", "coordinates": lines}}
    return {"type": "FeatureCollection", "features": [
        feat(name, [MAIN, STRAY], 2.4), feat("Other Trail", [OTHER, OTHER_STRAY], 1.7)]}


def _boundary():
    return [{"name": NAME, "bbox": (10.4, 56.0, 10.9, 56.4),
             "rings": [[(10.4, 56.0), (10.9, 56.0), (10.9, 56.4), (10.4, 56.4), (10.4, 56.0)]],
             "osm_id": REL, "osm_type": "relation"}]


def _publish(trails, state, entries=None, extra=None):
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
        if entries is not None:
            (work / "remnant.json").write_text(json.dumps(entries), encoding="utf-8")
            argv += ["--ca-remnant-drop", str(work / "remnant.json"),
                     "--ca-remnant-drop-report", str(work / "report.json")]
        argv += extra or []
        out, err = io.StringIO(), io.StringIO()
        with (patch.object(publish.areamod, "merge_areas", return_value=_boundary()),
              redirect_stdout(out), redirect_stderr(err)):
            try:
                rc = publish.main(argv)
            except SystemExit as exit_:
                rc = exit_.code
        rows = json.loads(preview.read_text())["areas"] if preview.exists() else []
        by_name = {t["name"]: t for row in rows for t in row["trails"]}
        report = json.loads((work / "report.json").read_text()) if (work / "report.json").exists() else None
        return rc, by_name, report, out.getvalue(), err.getvalue()


@unittest.skipUnless(HAVE_SHAPELY, "shapely not installed")
class Publish(unittest.TestCase):
    def test_canada_drops_the_stray_piece_of_the_named_trail_only(self):
        rc, trails, report, out, _ = _publish(_trails(), "Canada", [ENTRY])
        self.assertEqual(rc, 0)
        self.assertEqual(len(trails["Ridge Trail"]["segments"]), 1)
        self.assertEqual(len(trails["Other Trail"]["segments"]), 2)               # unlisted trail keeps its piece
        self.assertLess(trails["Ridge Trail"]["distanceMi"], 2.4)
        self.assertIn("remnant-drop Ridge Trail", out)                              # the printed per-trail report
        self.assertEqual(report["failures"], [])
        self.assertEqual(report["dropped"][0]["name"], "Ridge Trail")
        self.assertEqual(report["area"], AREA_ID)

    def test_control_without_the_flag_is_unchanged(self):
        rc, trails, report, out, _ = _publish(_trails(), "Canada")
        self.assertEqual(rc, 0)
        self.assertEqual(len(trails["Ridge Trail"]["segments"]), 2)
        self.assertEqual(len(trails["Other Trail"]["segments"]), 2)
        self.assertIsNone(report)
        self.assertNotIn("remnant-drop", out)

    def test_ids_and_every_other_field_are_unchanged(self):
        _, base, _, _, _ = _publish(_trails(), "Canada")
        _, dropped, _, _, _ = _publish(_trails(), "Canada", [ENTRY])
        self.assertEqual(base["Other Trail"], dropped["Other Trail"])
        for key in set(base["Ridge Trail"]) - {"segments", "distanceMi", "distinct_mi"}:
            self.assertEqual(base["Ridge Trail"][key], dropped["Ridge Trail"][key], key)

    def test_other_states_refuse_the_flag(self):
        for state in ("Denmark", "Colorado", "Rhode Island", "canada"):
            with self.subTest(state):
                rc, trails, _, _, err = _publish(_trails(), state, [ENTRY])
                self.assertEqual(rc, 2)
                self.assertEqual(trails, {})
                self.assertIn("--ca-remnant-drop is for --state Canada only", err)

    def test_exact_boundary_refuses_the_flag(self):
        rc, trails, _, _, err = _publish(_trails(), "Canada", [ENTRY], extra=["--exact-boundary", "x"])
        self.assertNotEqual(rc, 0)
        self.assertEqual(trails, {})

    def test_report_flag_needs_the_drop_flag(self):
        with TemporaryDirectory() as tmp:
            argv = ["--trails", "t", "--hiking", "h", "--out-dir", tmp, "--ca-remnant-drop-report",
                    str(Path(tmp) / "r.json")]
            with redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as ctx:
                    publish.main(argv)
            self.assertEqual(ctx.exception.code, 2)

    def test_malformed_entry_file_is_refused(self):
        rc, trails, _, _, err = _publish(_trails(), "Canada", [{**ENTRY, "max_mi": 0.05}])
        self.assertEqual(rc, 2)
        self.assertEqual(trails, {})
        self.assertIn("--ca-remnant-drop is invalid", err)

    def test_stale_entry_fails_the_area(self):
        rc, trails, report, out, err = _publish(_trails(), "Canada", [{**ENTRY, "name": "No Such Trail"}])
        self.assertEqual(trails, {})                                                # nothing published
        self.assertIn("FAILED validation 1", out)       # publish_areas reports a failed area; ca-run-area.sh then
                                                        # stops on the missing geom file (SAFETY BLOCK exit 3)
        self.assertIn("stale entry", err)
        self.assertTrue(report["failures"])

    def test_a_piece_over_the_default_threshold_stays_until_the_entry_raises_it(self):
        big = _trails()
        big["features"][0]["geometry"]["coordinates"][1] = [[10.52, 56.20], [10.5260, 56.20]]   # about 0.25 mi
        rc, trails, _, _, err = _publish(big, "Canada", [ENTRY])
        self.assertEqual(trails, {})                                                # fails closed: nothing droppable
        self.assertIn("no stray piece", err)
        rc, trails, _, _, _ = _publish(big, "Canada", [{**ENTRY, "max_mi": 0.3}])
        self.assertEqual(rc, 0)
        self.assertEqual(len(trails["Ridge Trail"]["segments"]), 1)


if __name__ == "__main__":
    unittest.main()
