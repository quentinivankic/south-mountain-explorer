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

# A route SUPER-RELATION (2000) whose members are child route relations
# (2001, 2002) that actually hold the ways — the Te Araroa shape. 2001
# has a weaker (rwn) network; the parent is nwn, so both ways must end up
# stamped with the strongest (nwn) route.
_OSM_SUPER = """<?xml version="1.0"?>
<osm version="0.6" generator="test">
 <node id="1" lat="0" lon="0" version="1"/>
 <node id="2" lat="1" lon="1" version="1"/>
 <node id="3" lat="2" lon="2" version="1"/>
 <node id="4" lat="3" lon="3" version="1"/>
 <way id="301" version="1"><nd ref="1"/><nd ref="2"/><tag k="highway" v="path"/></way>
 <way id="302" version="1"><nd ref="3"/><nd ref="4"/><tag k="highway" v="path"/></way>
 <relation id="2001" version="1">
  <member type="way" ref="301" role=""/>
  <tag k="type" v="route"/><tag k="route" v="hiking"/><tag k="network" v="rwn"/>
 </relation>
 <relation id="2002" version="1">
  <member type="way" ref="302" role=""/>
  <tag k="type" v="route"/><tag k="route" v="hiking"/>
 </relation>
 <relation id="2000" version="1">
  <member type="relation" ref="2001" role=""/>
  <member type="relation" ref="2002" role=""/>
  <tag k="type" v="route"/><tag k="route" v="hiking"/>
  <tag k="network" v="nwn"/><tag k="name" v="Long Trail"/>
 </relation>
</osm>
"""


@unittest.skipUnless(HAVE_OSMIUM, "pyosmium not installed")
class RouteIndex(unittest.TestCase):
    def _index(self, osm=_OSM):
        with tempfile.NamedTemporaryFile(suffix=".osm", mode="w", delete=False) as fh:
            fh.write(osm)
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

    def test_super_relation_ways_resolved_transitively(self):
        # Ways hang off child relations, not the parent super-relation.
        idx = self._index(_OSM_SUPER)
        self.assertIn("w301", idx)
        self.assertIn("w302", idx)

    def test_super_relation_keeps_strongest_network(self):
        # w301 is in child 2001 (rwn) and parent 2000 (nwn) → nwn wins,
        # and it carries the parent's name.
        idx = self._index(_OSM_SUPER)
        self.assertEqual(idx["w301"]["network"], "nwn")
        self.assertEqual(idx["w301"]["route_name"], "Long Trail")
        # w302's child (2002) has no network; it inherits nwn from the parent.
        self.assertEqual(idx["w302"]["network"], "nwn")
