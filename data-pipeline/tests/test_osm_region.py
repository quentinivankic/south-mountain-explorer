"""Tests for the region OSM downloader's PBF validation. Pure stdlib."""
import unittest
import tempfile
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sources" / "downloaders"))

import osm_region as osm  # noqa: E402


def _write(data: bytes) -> Path:
    fh = tempfile.NamedTemporaryFile(suffix=".osm.pbf", delete=False)
    fh.write(data)
    fh.close()
    return Path(fh.name)


class AssertPbf(unittest.TestCase):
    def test_valid_pbf_header_passes(self):
        # A real .osm.pbf opens with a header blob carrying the "OSMHeader"
        # type string in the first bytes.
        osm.assert_pbf(_write(b"\x00\x00\x00\x0d\x0a\x09OSMHeader\x18\x00rest..."))

    def test_html_error_page_rejected(self):
        # The exact failure mode: a bad slug → Geofabrik HTML page (200).
        with self.assertRaises(SystemExit):
            osm.assert_pbf(_write(b"<!DOCTYPE html>\n<html><head><title>404</title>"))

    def test_empty_file_rejected(self):
        with self.assertRaises(SystemExit):
            osm.assert_pbf(_write(b""))


if __name__ == "__main__":
    unittest.main()
