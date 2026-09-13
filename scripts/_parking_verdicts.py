#!/usr/bin/env python3
"""Read `public/areas/parking-verdicts.json` and match its verdicts to lots.

The sidecar is the per-lot KEEP/DROP record from the vision adjudication (task
#53, `docs/parking-adjudication-handoff.md`). This module is the ONE place that
decides whether a shipped lot "is" a judged lot, so the three consumers cannot
drift apart:

  scripts/add-parking.py        drops judged-DROP lots at fetch time, by OSM id
  scripts/sweep-parking-verdicts.py  removes them from already-published geom
  scripts/build-parking-pool.py     keeps them out of the global pool

Pure Python on purpose. `build-parking-pool.py` runs inside
`sync-geom-to-r2.yml`, which installs neither shapely nor network, so the
point-in-polygon and edge-distance tests below are written out by hand — a few
hundred rings against the lots near them, not a geometry job.

HOW A LOT IS MATCHED. Shipped lots carry no OSM id today (all 40,339 records
in geom are lat / lon / trailhead / fee / name / source — measured 2026-09-13),
so identity has to come from position. Two facts about the positions:

  * a shipped lot's position is Overpass `out center` — the CENTRE OF THE
    BOUNDING BOX of the OSM way or relation;
  * a verdict's position is the dossier's cluster centroid, and a dossier
    cluster unions every `amenity=parking` element within 40 m.

For a plain single-polygon lot those two points sit within a few metres
(measured: 106 of 148 matched KEEPs within 5 m, 31 more within 15 m). For a
large or irregular lot, or a cluster of several elements, they can be tens of
metres apart (a Zion relation at 15 m, a North Mountain lot at 41 m). A
radius big enough for those would also swallow an unjudged neighbour, so the
match uses the footprint itself. A shipped lot is a judged lot when, in this
order of confidence:

  1. it sits within `BBOX_CENTRE_M` of the bounding-box centre of one of the
     verdict's rings (or of all its rings together) — that IS what Overpass
     ships, so this is exact, and it reaches the crescent and L-shaped lots
     whose bbox centre falls off the pavement (3 of 254 footprinted verdicts,
     up to 32 m outside their own ring);
  2. it sits INSIDE one of the verdict's mapped polygons;
  3. it sits within `EDGE_M` of a polygon's edge;
  4. it sits within `NEAR_M` of the verdict's position — the only test a
     node-only lot has.

When more than one verdict claims a lot, the more confident rule wins, then
the nearer verdict — so a KEEP lot beside a DROP lot with a generous footprint
is never dropped by its neighbour's verdict. The sidecar carries each
verdict's rings for exactly this.

An `osm` id on the shipped lot (add-parking.py emits one from 2026-09-13 on)
wins outright WHEN THE SIDECAR KNOWS IT. An id the sidecar has not judged
falls back to the position rules, deliberately: a verdict is about the place,
and OSM ids churn (a parking node redrawn as an area gets a new id), while a
lot that lands inside a judged footprint is that lot whatever its id is. That
fallback can do no worse than the pre-roll match it replaces.

Known miss, chosen deliberately: a node-only member of a multi-element cluster
farther than `NEAR_M` from the cluster centroid has no footprint of its own
and is not matched by position, so it is not dropped. The safe direction — a
lot stays until an id match can name it.
"""
from __future__ import annotations

import json
import math
import os
from typing import Iterable

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PATH = os.path.join(_ROOT, "public", "areas", "parking-verdicts.json")
VERSION = 1

# Centre-to-centre distance at which two positions describe one lot. Covers
# the out-center / centroid disagreement for anything a node could represent,
# and is half the 40 m at which the pipeline already merges two lots into one.
NEAR_M = 20.0
# How far outside a mapped footprint a position may fall and still be that
# lot: the out-center of a slightly bent polygon lands a few metres off the
# pavement. 10 m is well short of the gap to any neighbouring lot.
EDGE_M = 10.0
# Tolerance around a ring's bounding-box centre — the exact point Overpass
# `out center` ships for a way or relation. Six-decimal rounding on both sides
# is under 0.2 m; 3 m absorbs a ring vertex or two moving in a later OSM edit.
BBOX_CENTRE_M = 3.0

# ~275 m cells for the neighbour search — the same bucketing the pool builder
# uses. Each verdict is indexed into every cell its footprint touches.
_CELL = 0.0025


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r, p = 6371000.0, math.radians
    x = (math.sin(p(lat2 - lat1) / 2) ** 2
         + math.cos(p(lat1)) * math.cos(p(lat2)) * math.sin(p(lon2 - lon1) / 2) ** 2)
    return 2 * r * math.asin(min(1.0, math.sqrt(x)))


def point_in_ring(lat: float, lon: float, ring: list) -> bool:
    """Even-odd test; `ring` is a list of [lat, lon] vertices, closed or not."""
    inside = False
    n = len(ring)
    if n < 3:
        return False
    for i in range(n):
        y1, x1 = float(ring[i][0]), float(ring[i][1])
        y2, x2 = float(ring[(i + 1) % n][0]), float(ring[(i + 1) % n][1])
        if (y1 > lat) != (y2 > lat):
            xin = (x2 - x1) * (lat - y1) / (y2 - y1) + x1
            if lon < xin:
                inside = not inside
    return inside


def dist_to_ring_m(lat: float, lon: float, ring: list) -> float:
    """Metres from (lat, lon) to the nearest edge of `ring`. Flat-earth in a
    local metre frame — exact enough at lot scale."""
    best = math.inf
    n = len(ring)
    if n == 0:
        return best
    k = math.cos(math.radians(lat)) * 111_320.0
    px, py = lon * k, lat * 111_320.0
    for i in range(n if n > 1 else 0):
        a, b = ring[i], ring[(i + 1) % n]
        ax, ay = float(a[1]) * k, float(a[0]) * 111_320.0
        bx, by = float(b[1]) * k, float(b[0]) * 111_320.0
        dx, dy = bx - ax, by - ay
        seg2 = dx * dx + dy * dy
        t = 0.0 if seg2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg2))
        ex, ey = ax + t * dx, ay + t * dy
        best = min(best, math.hypot(px - ex, py - ey))
    if n == 1:
        best = math.hypot(px - float(ring[0][1]) * k, py - float(ring[0][0]) * 111_320.0)
    return best


def _bbox_centres(rings: list) -> list[tuple[float, float]]:
    """Bounding-box centre of every ring, plus of all rings together when there
    is more than one (a multipolygon relation's `out center` is the centre of
    its whole extent)."""
    out: list[tuple[float, float]] = []
    all_lat: list[float] = []
    all_lon: list[float] = []
    for ring in rings or ():
        lats = [float(v[0]) for v in ring if len(v) >= 2]
        lons = [float(v[1]) for v in ring if len(v) >= 2]
        if not lats:
            continue
        out.append(((min(lats) + max(lats)) / 2, (min(lons) + max(lons)) / 2))
        all_lat += lats
        all_lon += lons
    if len(out) > 1:
        out.append(((min(all_lat) + max(all_lat)) / 2, (min(all_lon) + max(all_lon)) / 2))
    return out


def covers(entry: dict, lat: float, lon: float) -> tuple[int, float] | None:
    """(rank, distance) when (lat, lon) is the lot `entry` judged, else None.

    Rank is the rule that matched — 0 bbox-centre, 1 inside a ring, 2 near an
    edge, 3 near the position — so callers can prefer the more confident claim
    before the nearer one. Distance is to the verdict's position.

    `entry` is a `Verdicts` entry (the private copies `Verdicts` makes): the
    ring bbox centres are cached on it under `_bbox_centres`. Passing a raw
    sidecar entry works but leaves that key on the caller's object."""
    d = haversine_m(lat, lon, entry["lat"], entry["lon"])
    rings = entry.get("rings") or ()
    if rings:
        centres = entry.get("_bbox_centres")
        if centres is None:
            centres = entry["_bbox_centres"] = _bbox_centres(rings)
        for clat, clon in centres:
            if haversine_m(lat, lon, clat, clon) <= BBOX_CENTRE_M:
                return 0, d
        for ring in rings:
            if point_in_ring(lat, lon, ring):
                return 1, d
        for ring in rings:
            if dist_to_ring_m(lat, lon, ring) <= EDGE_M:
                return 2, d
    if d <= NEAR_M:
        return 3, d
    return None


class Verdicts:
    """The loaded sidecar, indexed by OSM id and by position."""

    def __init__(self, doc: dict):
        lots = doc.get("lots") if isinstance(doc, dict) else None
        if not isinstance(lots, dict):
            raise ValueError("parking-verdicts.json: no 'lots' object")
        self.entries: dict[str, dict] = {}
        self._by_osm: dict[str, dict] = {}
        self._cells: dict[tuple[int, int], list[dict]] = {}
        for key, e in lots.items():
            if not isinstance(e, dict) or e.get("verdict") not in ("KEEP", "DROP", "REVIEW"):
                continue
            if e.get("lat") is None or e.get("lon") is None:
                continue
            e = dict(e)
            e["_key"] = key
            self.entries[key] = e
            for o in e.get("osm") or [key]:
                self._by_osm[o] = e
            # Index into every cell the footprint (plus the near circle) touches.
            lats = [e["lat"]] + [float(v[0]) for r in e.get("rings") or () for v in r]
            lons = [e["lon"]] + [float(v[1]) for r in e.get("rings") or () for v in r]
            pad = _CELL / 4                      # ~70 m, more than NEAR_M + EDGE_M
            for i in range(int((min(lats) - pad) / _CELL), int((max(lats) + pad) / _CELL) + 1):
                for j in range(int((min(lons) - pad) / _CELL), int((max(lons) + pad) / _CELL) + 1):
                    self._cells.setdefault((i, j), []).append(e)

    def __len__(self) -> int:
        return len(self.entries)

    def count(self, verdict: str) -> int:
        return sum(1 for e in self.entries.values() if e["verdict"] == verdict)

    def by_verdict(self, verdict: str) -> list[dict]:
        return [e for e in self.entries.values() if e["verdict"] == verdict]

    def match(self, lot: dict) -> dict | None:
        """The verdict that applies to `lot` ({lat, lon, osm?}), or None.

        A judged `osm` id on the lot is authoritative. Otherwise (no id, or an
        id the sidecar has not judged) the verdict that claims the lot by the
        most confident rule wins, then the nearest; a lot nobody claims has no
        verdict."""
        osm = lot.get("osm")
        if osm:
            hit = self._by_osm.get(osm)
            if hit is not None:
                return hit
        la, lo = lot.get("lat"), lot.get("lon")
        if la is None or lo is None:
            return None
        best, best_score = None, (math.inf, math.inf)
        seen: set[str] = set()
        for e in self._cells.get((int(la / _CELL), int(lo / _CELL)), ()):
            if e["_key"] in seen:
                continue
            seen.add(e["_key"])
            score = covers(e, la, lo)
            if score is not None and score < best_score:
                best, best_score = e, score
        return best

    def drop_for(self, lot: dict) -> dict | None:
        """The DROP verdict that applies to `lot`, or None. A KEEP or REVIEW
        that matches more closely shields the lot (see `match`)."""
        hit = self.match(lot)
        return hit if hit is not None and hit["verdict"] == "DROP" else None


def load(path: str | os.PathLike | None = None, *, quiet: bool = False) -> Verdicts | None:
    """The sidecar at `path` (default: the committed one), or None when it is
    absent or unreadable. Absence is NOT an error — every consumer must keep
    working without it, exactly as they do without the pre-ownership sidecar."""
    path = str(path or DEFAULT_PATH)
    if not os.path.exists(path):
        if not quiet:
            print(f"  verdicts {path}: not present — skipping")
        return None
    try:
        doc = json.load(open(path))
        v = Verdicts(doc)
    except Exception as e:                 # noqa: BLE001
        if not quiet:
            print(f"  verdicts {path}: unreadable ({e}) — skipping")
        return None
    if not quiet:
        print(f"  verdicts {path}: {len(v)} judged lot(s), "
              f"{v.count('KEEP')} keep / {v.count('DROP')} drop / "
              f"{v.count('REVIEW')} review")
    return v
