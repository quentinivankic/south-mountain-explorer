"""Tests for route-relation indexing (global 'official' signal).

Gated on pyosmium: the light licensing-gate CI step runs the unit suite
before the geo toolchain installs, so this skips there and runs wherever
pyosmium is present.
"""
import unittest
import tempfile
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "build"))

try:
    import osmium  # noqa: F401
    HAVE_OSMIUM = True
except ImportError:
    HAVE_OSMIUM = False

import route_index  # noqa: E402

_OSM = """<?xml version="1.0"?>
<osm version="0.6" generator="test">
 <node id="1" lat="0" lon="0" version="1"/>
 <node id="2" lat="1" lon="1" version="1"/>
 <node id="3" lat="2" lon="2" version="1"/>
 <way id="101" version="1"><nd ref="1"/><nd ref="2"/><tag k="highway" v="path"/></way>
 <way id="102" version="1"><nd ref="2"/><nd ref="3"/><tag k="highway" v="path"/></way>
 <way id="200" version="1"><nd ref="1"/><nd ref="3"/><tag k="highway" v="footway"/></way>
 <relation id="1001" version="1">
  <member type="way" ref="101" role=""/>
  <member type="way" ref="102" role=""/>
  <tag k="type" v="route"/><tag k="route" v="hiking"/>
  <tag k="network" v="nwn"/><tag k="name" v="Te Araroa"/>
  <tag k="operator" v="Te Araroa Trust"/>
 </relation>
 <relation id="1002" version="1">
  <member type="way" ref="200" role=""/>
  <tag k="type" v="route"/><tag k="route" v="bicycle"/>
 </relation>
</osm>
"""


@unittest.skipUnless(HAVE_OSMIUM, "pyosmium not installed")
class RouteIndex(unittest.TestCase):
    def _index(self):
        with tempfile.NamedTemporaryFile(suffix=".osm", mode="w", delete=False) as fh:
            fh.write(_OSM)
            path = fh.name
        return route_index.build_index(path)

    def test_hiking_route_members_indexed_with_network(self):
        idx = self._index()
        self.assertIn("w101", idx)
        self.assertIn("w102", idx)
        self.assertEqual(idx["w101"]["network"], "nwn")
        self.assertEqual(idx["w101"]["route_name"], "Te Araroa")
        self.assertTrue(idx["w101"]["in_route"])

    def test_non_hiking_route_excluded(self):
        # w200 is only in a bicycle route → not a walking-route member.
        self.assertNotIn("w200", self._index())
