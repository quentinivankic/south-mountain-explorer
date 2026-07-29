#!/usr/bin/env python3
"""Enrich published area geom with trailhead PARKING from OpenStreetMap.

People need to know where to *park* before they can hike — the app draws a
beautiful trail network but says nothing about how to get to it. This adds an
`amenity=parking` layer to each area's geom so the app can draw parking pins.

Runs off the ALREADY-published geom via Overpass (which CI runners can reach,
so no OSM extract / homelab needed — same pattern as seed-areas /
audit-easement-ownership). Queries ONE state at a time (all parking +
trailheads in the state's ISO admin area), then assigns lots to areas
locally — one Overpass query per state, not per area (247 per-area queries
triggered 504 timeout storms).

Extraction logic (evidence-based; see docs/parking.md for the OSM-wiki
citations behind each choice):
  1. Read the area's bbox from public/areas/geom/<id>.json.
  2. Overpass for `amenity=parking` (the LOT — never `amenity=parking_space`,
     which tags individual stalls and would shatter one lot into many pins)
     AND `highway=trailhead` (a de-facto tag that marks where a trail starts,
     often on/at the parking) in that bbox, `out center` so ways/relations
     resolve to a point.
  3. Drop what isn't usable public trailhead parking:
       - access in {private, no, customers, permit}  (customers = a store's
         lot; keep untagged + permissive — real trailhead lots are usually one
         of those).
       - parking in {street_side, lane, on_kerb, half_on_kerb, on_street,
         shoulder, layby, painted_area}  (on-street parking, not a lot).
  4. Associate with THIS area. PRIMARY gate = point-in-polygon containment in
     the area's real boundary (fetched by osm_relation_id, buffered out 30 m
     for fence-line lots): proximity alone can't tell "inside the park" from
     "across the road" — a neighbour/school lot 26 m from a perimeter trail is
     still across the fence (see the probe: Thunderbird 26->12, 14 across-road
     lots dropped). Within the boundary a lot is kept if it's within
     `PARKING_TRAIL_MAX_M` of a trail vertex OR within `TRAILHEAD_COINCIDE_M`
     of a `highway=trailhead` (the trailhead tag IS the association). Lots
     corroborated by a trailhead are flagged `trailhead: true`. If a boundary
     can't be fetched the area degrades to proximity-only.
  5. Dedup near-duplicates; write a compact `parking` list into the geom.

Every run prints an aggregate REPORT (counts, coverage, lot→trail distance
histogram, % trailhead-corroborated, outlier areas) so quality is judged from
numbers, not by eyeballing hundreds of areas. `--dry-run` reports without
writing; the histogram is how we tune `PARKING_TRAIL_MAX_M` from real data.

    python3 scripts/add-parking.py --state az --dry-run   # measure, write nothing
    python3 scripts/add-parking.py --state az             # write + report
    python3 scripts/add-parking.py --all

Post-process for now (a republish drops it), mirroring how DEM elevation
started — fold into the publish pipeline once proven.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPTS_DIR))
from _seed_constants import STATE_NAMES  # noqa: E402

# seed-areas.py has a hyphen, so it can't be a plain import target.
_spec = importlib.util.spec_from_file_location(
    "seed_areas", _SCRIPTS_DIR / "seed-areas.py")
_seed_areas = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_seed_areas)
fetch_overpass = _seed_areas.fetch_overpass

GEOM_DIR = _SCRIPTS_DIR.parent / "public" / "areas" / "geom"
GOLDEN_FILE = _SCRIPTS_DIR / "golden-parking.json"

# A lot is trailhead parking if it's within this of a trail vertex. 250 m is a
# heuristic (OSM has no standard) — the run's distance histogram is what
# tells us whether to tighten it.
PARKING_TRAIL_MAX_M = 250.0
# A lot within this of a highway=trailhead is trailhead parking regardless of
# trail-geometry distance (the trailhead tag is the association).
TRAILHEAD_COINCIDE_M = 80.0
# Two lots closer than this are the same lot mapped twice; keep one.
PARKING_DEDUP_M = 40.0
# The PRIMARY quality gate: a lot must sit inside the area's real boundary
# polygon (from osm_relation_id) — proximity alone can't tell "inside the park"
# from "across the road" (a neighbour/school lot 26 m from a perimeter trail is
# still across the fence). We buffer the boundary OUTWARD by this much so a real
# trailhead lot right at the fence-line survives, while across-the-road lots
# (road width + setback >> 30 m) stay cut. See docs/parking.md + the probe.
_BOUNDARY_BUFFER_M = 30.0

# `access` values that are NOT usable public trailhead parking. Untagged +
# permissive are KEPT (that's how most public trailhead land is tagged).
_EXCLUDE_ACCESS = {"private", "no", "customers", "permit"}
# `parking=*` values that describe ON-STREET parking (linear, along a road),
# not a trailhead LOT. Real lots are surface / multi-storey / underground.
_STREET_PARKING = {
    "street_side", "lane", "on_kerb", "half_on_kerb",
    "on_street", "shoulder", "layby", "painted_area",
}

RETRY_BACKOFFS_SECONDS = [30, 90, 300]

# ── Tier-2: authoritative federal fallback ────────────────────────────────
# OSM is primary. Where OSM mapped NO contained parking for an area (typically
# remote BLM/USFS wilderness), we fill from the managing agency's own trailhead/
# parking geodata. All three are US-government public domain (ODbL-compatible,
# free for commercial use, attribution encouraged) and NATIONWIDE from one
# ArcGIS REST service each — so this scales to all 50 states with no per-state
# integration. Measured AZ fill (2026-07-18): 4 (OSM-only) -> 13 blank areas.
# Federal points are only ever ADDED to areas OSM left blank, never merged into
# areas that already have OSM lots (OSM stays authoritative where it exists).
_FED_EDGE_BUFFER_M = 250.0   # a curated federal trailhead may sit just OUTSIDE
                             # a wilderness boundary (the access road is excluded
                             # from the wilderness polygon by design). Admit such
                             # a point only if it's contained by NO area at all —
                             # then it belongs to the nearest blank area's edge.
# BLM's "Natl RIDB Trailhead" (MapServer/7) was DROPPED (2026-07-18). Despite
# the layer name, its points are generic RECREATION-AREA markers — `RecAreaName`
# is the wilderness/monument itself and the point sits wherever, often deep in
# the interior — not curated trailheads with parking. Verified bad across Kanab
# Creek (0.58 mi from any road) AND Grand Canyon-Parashant (RecAreaName = the
# monument / "Mt. Trumbull", activities incl. fishing/OHV/camping, middle of
# nowhere). It produced ZERO usable pins; the road gate can't save it because
# the AZ Strip's dirt tracks let generic markers pass. NPS parking POLYGONS are
# real lots (Saguaro verified). USFS EDW is named trailhead points (different,
# more specific dataset) — kept, but VALIDATE it on the first states it fills
# before trusting it in the roll (it filled nothing in AZ).
_FED_SOURCES = [
    {"key": "nps",
     "url": "https://mapservices.nps.gov/arcgis/rest/services/NationalDatasets/"
            "NPS_Public_ParkingLots/FeatureServer/0",
     "where": "1=1", "trailhead": False},         # NPS public parking polygons
    {"key": "usfs",
     "url": "https://apps.fs.usda.gov/arcx/rest/services/EDW/"
            "EDW_RecreationOpportunities_01/MapServer/0",
     "where": "MARKERACTIVITY='Trailhead'", "trailhead": True},
]
# A federal point ships ONLY if it's within this of a DRIVABLE road — the
# gate that distinguishes a real drive-to trailhead from a BLM wilderness-marker
# point dropped deep in the interior (Kanab Creek's marker sat 0.58 mi from any
# road; containment alone let it through). `track` = dirt road: real AZ
# trailheads are reached by them, so they count as drivable access.
_ROAD_GATE_MAX_M = 250.0
_DRIVABLE_HW = ("motorway|trunk|primary|secondary|tertiary|"
                "unclassified|residential|service|track|road")
_ROAD_CHUNK = 60  # around-clauses per Overpass query (batch for nationwide runs)
# Attribute names each agency uses for a feature's display name (first hit wins).
_FED_NAME_KEYS = ("LOTNAME", "RECAREANAME", "NAME", "name", "SITE_NAME",
                  "FEATURENAME", "MAPLABEL", "RECAREA")
_ARCGIS_MAXREC = 1000
_ARCGIS_TIMEOUT = 60
# Some agency GIS hosts present certificate chains urllib dislikes; the data is
# public and read-only, so an unverified context is acceptable here (we only GET
# open geodata, never send credentials).
_ARCGIS_SSL = ssl.create_default_context()
_ARCGIS_SSL.check_hostname = False
_ARCGIS_SSL.verify_mode = ssl.CERT_NONE


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _pip(x: float, y: float, ring: list[tuple[float, float]]) -> bool:
    """Ray-casting point-in-polygon. `ring` is [(lon, lat), ...]; (x, y) is
    (lon, lat). Pure Python so the gate is unit-testable without shapely."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def point_in_rings(lat: float, lon: float,
                   rings: list[list[tuple[float, float]]]) -> bool:
    """Inside the boundary if inside ANY outer ring (multipolygon parks have
    several). Holes are ignored — negligible for trailhead parking."""
    return any(_pip(lon, lat, r) for r in rings)


def _dist_point_to_seg_m(lat: float, lon: float,
                         a: tuple[float, float], b: tuple[float, float]) -> float:
    """Distance (m) from (lat,lon) to segment a-b, each (lon,lat). Projects in a
    local equirectangular frame — fine at trailhead scale (<1 km)."""
    latr = math.radians(lat)
    mx = 111_320.0 * math.cos(latr)          # m per degree lon at this lat
    my = 110_540.0                            # m per degree lat
    px, py = lon * mx, lat * my
    ax, ay = a[0] * mx, a[1] * my
    bx, by = b[0] * mx, b[1] * my
    dx, dy = bx - ax, by - ay
    seg2 = dx * dx + dy * dy
    if seg2 <= 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def dist_to_rings_m(lat: float, lon: float,
                    rings: list[list[tuple[float, float]]]) -> float:
    """Min distance (m) from (lat,lon) to the nearest ring EDGE. 0-ish when the
    point sits on the boundary; used to measure how far OUTSIDE a dropped lot is."""
    best = math.inf
    for r in rings:
        for i in range(len(r) - 1):
            d = _dist_point_to_seg_m(lat, lon, r[i], r[i + 1])
            if d < best:
                best = d
    return best


def _rings_from_union(u, buffer_m: float) -> list[list[tuple[float, float]]]:
    """shapely (Multi)Polygon -> list of exterior ring coord lists [(lon,lat)],
    buffered outward by `buffer_m` (approx degrees) so fence-line lots survive."""
    if buffer_m:
        u = u.buffer(buffer_m / 111_000.0)
    geoms = list(u.geoms) if hasattr(u, "geoms") else [u]
    rings: list[list[tuple[float, float]]] = []
    for g in geoms:
        if g.geom_type == "Polygon":
            rings.append([(x, y) for x, y in g.exterior.coords])
    return rings


def fetch_state_boundaries(rel_ids: list[int]) -> dict[int, list]:
    """ONE batched Overpass `out geom` query for a state's area boundary
    relations -> {rel_id: [outer rings]}. shapely assembles the (possibly
    unordered / split) member ways into rings; a relation that won't polygonize
    is simply omitted (that area falls back to proximity-only). Needs shapely
    (homelab / CI have it); a missing shapely raises and the caller degrades."""
    from shapely.geometry import LineString
    from shapely.ops import polygonize, unary_union

    ids = ";".join(f"rel({r})" for r in rel_ids if r)
    if not ids:
        return {}
    data = fetch_overpass(f"[out:json][timeout:600];({ids};);out geom;")
    per_rel: dict[int, list] = {}
    for el in data.get("elements", []):
        if el.get("type") != "relation":
            continue
        lines = []
        for m in el.get("members", []):
            if m.get("role") not in ("outer", "", None):
                continue
            g = m.get("geometry")
            if not g or len(g) < 2:
                continue
            lines.append(LineString([(p["lon"], p["lat"]) for p in g]))
        if not lines:
            continue
        polys = list(polygonize(lines))
        if not polys:
            continue
        u = unary_union(polys)
        if not u.is_valid:
            u = u.buffer(0)
        rings = _rings_from_union(u, _BOUNDARY_BUFFER_M)
        if rings:
            per_rel[el["id"]] = rings
    return per_rel


def _fed_name(props: dict) -> str | None:
    # Case-insensitive: USFS EDW uses lowercase recareaname, NPS uppercase LOTNAME.
    lower = {k.lower(): v for k, v in props.items()}
    for k in _FED_NAME_KEYS:
        v = lower.get(k.lower())
        if isinstance(v, str) and v.strip() and v.strip().lower() not in ("none", "null"):
            return v.strip()
    return None


class ArcGISUnavailable(Exception):
    """The layer could not be read. NOT the same as "the layer has no features
    here" — see `_features_or_raise`."""


def _features_or_raise(data: dict) -> list:
    """Pull `features` out of an ArcGIS geojson response, distinguishing an
    honest empty answer from a failure dressed up as one.

    ArcGIS answers a throttled, malformed or over-limit query with **HTTP 200**
    and a body like `{"error": {"code": 429, "message": ...}}` — no `features`
    key at all. The old code did `data.get("features") or []`, so that failure
    read as "zero features in bbox" and the caller cheerfully cleared every pin
    the layer had contributed last run. That is how the 2026-07-27 national roll
    deleted Kootenai National Forest's 50 USFS trailheads while logging
    `federal usfs: 0 features in bbox` and exiting 0.

    A genuine empty result comes back as a well-formed FeatureCollection with
    `"features": []`, so the key being PRESENT is the signal that the query ran.
    """
    err = data.get("error")
    if err:
        code = err.get("code") if isinstance(err, dict) else None
        msg = err.get("message") if isinstance(err, dict) else err
        raise ArcGISUnavailable(f"ArcGIS error {code}: {msg}")
    if "features" not in data:
        # Not an error object either — an unexpected shape. Still not zero.
        raise ArcGISUnavailable(
            f"response has no 'features' key (keys: {sorted(data)[:6]})")
    return data["features"] or []


def _arcgis_transient(exc: Exception) -> bool:
    """Is retrying this worth the wait? A throttle or a gateway blip is
    transient; a rejected query returns the same answer three times, and burning
    the backoff ladder on it repeats the missing-shapely mistake of retrying a
    deterministic local failure and then blaming the remote service."""
    if isinstance(exc, ArcGISUnavailable):
        m = str(exc)
        return any(f"error {c}:" in m for c in (429, 500, 502, 503, 504)) \
            or "no 'features' key" in m
    return True                    # transport / timeout / bad JSON — worth a retry


def fetch_arcgis(url: str, bbox: list[float], where: str) -> list[tuple[float, float, dict]]:
    """Query an ArcGIS REST feature/map layer for features intersecting `bbox`
    ([lonmin, latmin, lonmax, latmax]). Returns [(lat, lon, props)]; a polygon
    feature is reduced to its ring centroid. Paginates via resultOffset.

    Raises `ArcGISUnavailable` when the layer could not be read — including the
    HTTP-200-with-error-body case, which must never be mistaken for an empty
    result. Transient failures are retried on the shared backoff ladder first,
    which is what the un-retried single attempt was missing: Idaho's USFS query
    returned 0 on one run and 362 features twenty minutes later.
    """
    xmin, ymin, xmax, ymax = bbox
    out: list[tuple[float, float, dict]] = []
    offset = 0
    while True:
        params = urllib.parse.urlencode({
            "where": where,
            "geometry": f"{xmin},{ymin},{xmax},{ymax}",
            "geometryType": "esriGeometryEnvelope",
            "inSR": "4326", "outSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "*", "returnGeometry": "true", "f": "geojson",
            "resultOffset": offset, "resultRecordCount": _ARCGIS_MAXREC,
        })
        feats = None
        last: Exception | None = None
        for i, backoff in enumerate([0] + RETRY_BACKOFFS_SECONDS):
            if backoff:
                time.sleep(backoff)
            try:
                req = urllib.request.Request(
                    url + "/query?" + params,
                    headers={"User-Agent": "trekdex-parking/1.0"})
                with urllib.request.urlopen(req, timeout=_ARCGIS_TIMEOUT,
                                            context=_ARCGIS_SSL) as r:
                    feats = _features_or_raise(json.loads(r.read()))
                break
            except Exception as e:                              # noqa: BLE001
                last = e
                if not _arcgis_transient(e):
                    print(f"    arcgis query rejected, not retrying ({e})",
                          file=sys.stderr)
                    break
                print(f"    arcgis attempt {i + 1} failed ({e})", file=sys.stderr)
        if feats is None:
            raise ArcGISUnavailable(str(last))
        if not feats:
            break
        for ft in feats:
            g = ft.get("geometry") or {}
            gt, coords = g.get("type"), g.get("coordinates")
            if not coords:
                continue
            if gt == "Point":
                lon, lat = coords[0], coords[1]
            elif gt in ("Polygon", "MultiPolygon"):
                ring = coords[0] if gt == "Polygon" else coords[0][0]
                lon = sum(p[0] for p in ring) / len(ring)
                lat = sum(p[1] for p in ring) / len(ring)
            else:
                continue
            out.append((lat, lon, ft.get("properties") or {}))
        if len(feats) < _ARCGIS_MAXREC:
            break
        offset += _ARCGIS_MAXREC
    return out


def fetch_federal(bbox: list[float]) -> tuple[list[dict], set[str]]:
    """Fetch every federal source's trailhead/parking features in `bbox`.

    Returns `(lots, failed_source_keys)`. Each lot is
    {lat, lon, source, name?, trailhead?}. A source that fails to fetch is
    skipped rather than failing the whole run — OSM already shipped and federal
    is a bonus fill — but its key comes back in the second element so the caller
    can FAIL CLOSED and carry that source's existing pins forward. Without that
    flag an unreachable layer is indistinguishable from an empty one, and the
    difference is whether a wilderness keeps its trailheads or loses them.
    """
    out: list[dict] = []
    failed: set[str] = set()
    for s in _FED_SOURCES:
        try:
            feats = fetch_arcgis(s["url"], bbox, s["where"])
        except Exception as e:  # noqa: BLE001
            print(f"  federal source {s['key']} fetch FAILED ({e}); "
                  f"existing {s['key']} pins will be kept, not cleared",
                  file=sys.stderr)
            failed.add(s["key"])
            continue
        n = 0
        for lat, lon, props in feats:
            lot = {"lat": round(lat, 6), "lon": round(lon, 6), "source": s["key"]}
            nm = _fed_name(props)
            if nm:
                lot["name"] = nm
            if s["trailhead"]:
                lot["trailhead"] = True
            out.append(lot)
            n += 1
        print(f"  federal {s['key']}: {n} features in bbox")
    return out, failed


def fetch_roads_near(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Overpass: all DRIVABLE road vertices within `_ROAD_GATE_MAX_M` of ANY of
    `points`, in chunked `around` queries (batched for nationwide runs). Returns
    [(lat, lon)] road nodes. Raises if a chunk can't be fetched after retries —
    the caller then drops federal points rather than shipping unverified ones."""
    nodes: list[tuple[float, float]] = []
    for i in range(0, len(points), _ROAD_CHUNK):
        chunk = points[i:i + _ROAD_CHUNK]
        clauses = "".join(
            f'way["highway"~"^({_DRIVABLE_HW})(_link)?$"]'
            f'(around:{_ROAD_GATE_MAX_M},{lat},{lon});'
            for lat, lon in chunk)
        q = f"[out:json][timeout:180];({clauses});out geom;"
        data = None
        for j, backoff in enumerate([0] + RETRY_BACKOFFS_SECONDS):
            if backoff:
                time.sleep(backoff)
            try:
                data = fetch_overpass(q)
                break
            except Exception as e:  # noqa: BLE001
                print(f"  road-gate fetch attempt {j + 1} failed ({e})", file=sys.stderr)
        if data is None:
            raise RuntimeError("road-gate Overpass fetch failed")
        for el in data.get("elements", []):
            for g in el.get("geometry", []) or []:
                nodes.append((g["lat"], g["lon"]))
    return nodes


def _road_gate_filter(fed: list[dict], road_nodes: list[tuple[float, float]],
                      max_m: float) -> list[dict]:
    """Keep only federal points within `max_m` of a road node. Pure — the
    fetch is separate so this is unit-testable."""
    return [f for f in fed
            if min_dist_m(f["lat"], f["lon"], road_nodes) <= max_m]


def road_gate(fed: list[dict], stats: dict | None = None) -> tuple[list[dict], bool]:
    """Drop federal points with no drivable road within `_ROAD_GATE_MAX_M` — a
    'trailhead' POINT is only usable parking if you can actually drive to it.

    Returns `(kept, ok)`. If the road fetch fails outright, ALL federal points
    are dropped — never ship access we couldn't verify — and `ok` is False.

    That flag exists because dropping everything looks identical to "none of
    these points had a road", and the caller must not read a verification
    OUTAGE as a reason to delete pins it already shipped. Same hazard as the
    ArcGIS HTTP-200-with-error-body case, one step further down the pipe: the
    fetch can succeed and this gate still wipe the lot. Overpass 504s freely,
    so this is not hypothetical.
    """
    if not fed:
        return fed, True
    pts = [(f["lat"], f["lon"]) for f in fed]
    try:
        road_nodes = fetch_roads_near(pts)
    except Exception as e:  # noqa: BLE001
        print(f"  road gate: fetch failed ({e}); dropping all {len(fed)} federal "
              "point(s) — cannot verify road access", file=sys.stderr)
        if stats is not None:
            stats["federal_road_unverified"] = stats.get("federal_road_unverified", 0) + len(fed)
        return [], False
    kept = _road_gate_filter(fed, road_nodes, _ROAD_GATE_MAX_M)
    dropped = len(fed) - len(kept)
    if stats is not None:
        stats["federal_road_dropped"] = stats.get("federal_road_dropped", 0) + dropped
    print(f"  federal road gate: kept {len(kept)}/{len(fed)} "
          f"(dropped {dropped} roadless)")
    return kept, True


FED_SAME_NAME_M = 500.0
# Dedup radius for two federal pins that carry the SAME name. `PARKING_DEDUP_M`
# (40 m) compares position only, which is right for unrelated lots and too tight
# for one facility that an agency ships as several polygons: NPS gave Black
# Canyon of the Gunnison Wilderness "East Portal Parking" 3x and
# "So. Rim Visitor Ctr." 2x, each centroid 100-200 m apart.
#
# The radius is what makes this safe, and it was NOT obvious. Name alone is not
# identity, because agency names are often placeholders: Saguaro Wilderness has
# NINETEEN distinct NPS lots all called "Parking Lot", and Marjory Stoneman
# Douglas Wilderness has eight "Pull-Out Parking". Deduping on name alone
# collapsed Saguaro 24 -> 5 and destroyed 18 real, separate lots. Same name AND
# within 500 m is one facility; same name a mile apart is a lazy label.


def clean_federal_lots(lots: list[dict], trail_pts: list[tuple[float, float]] | None = None,
                       same_name_m: float = FED_SAME_NAME_M) -> tuple[list[dict], dict]:
    """Collapse same-named federal pins that are also close together. Returns
    `(kept, {reason: n})`. `trail_pts` is accepted but unused — see below.

    OSM lots are NEVER touched: authoritative, already boundary-containment
    gated, and none of the observed defects were OSM.

    ── A distance-to-trail cap was BUILT, MEASURED AND REJECTED (2026-07-27).
    The idea was to drop a federal pin further than N metres from any trail in
    its area, on the theory that such a pin must be misattributed — the
    motivating case being "McClellan Butte Trailhead" landing 116 km from any
    trail in palouse-to-cascades-state-park-wa. It does not work at any
    threshold, because the metric measures OUR TRAIL COVERAGE, not the pin:

        cap  2 km -> 217 pins from 48 areas, incl. kootenai-national-forest-id
                     51 -> 4 and death-valley-national-park-nv 37 -> 0
        cap 25 km ->  57 pins, STILL including Springer Mountain Trailhead (the
                     southern terminus of the Appalachian Trail), Carver's Gap,
                     Bridge of the Gods, Herman Creek and Eagle Creek

    Those are real, famous trailheads. Kootenai is 2.2 million acres and we hold
    a fraction of its trails; columbia-river-gorge ships ONE trail, so five real
    trailheads read as impossibly far from it. The pins are better data than our
    geometry, and a cap tight enough to catch the 116 km case deletes them.
    Harmlessness argues the same way: the app only ever draws the <=3 nearest
    lots within 805 m of the selected trail (`Area.nearestParking`), so a distant
    pin is invisible, not wrong. Gross misattribution is better addressed by the
    boundary containment gate that already exists at assign time.
    Do not re-propose without a signal that measures the PIN.
    """
    stats: dict[str, int] = {}
    if not lots:
        return lots, stats

    kept: list[dict] = []
    named: list[dict] = []          # federal pins carrying a name
    for lot in lots:
        if not lot.get("source"):
            kept.append(lot)                      # OSM — leave alone
        elif (lot.get("name") or "").strip():
            named.append(lot)
        else:
            kept.append(lot)                      # unnamed federal — nothing to match on

    groups: dict[str, list[dict]] = {}
    for lot in named:
        groups.setdefault((lot.get("name") or "").strip().casefold(), []).append(lot)
    for same in groups.values():
        # Greedy: each pin joins an existing cluster within `same_name_m`, else
        # starts its own. Distinct facilities that share a placeholder name stay
        # distinct because they are far apart.
        clusters: list[dict] = []
        for lot in same:
            hit = next((c for c in clusters
                        if haversine_m(lot["lat"], lot["lon"], c["lat"], c["lon"])
                        <= same_name_m), None)
            if hit is None:
                clusters.append(lot)
            else:
                hit["trailhead"] = hit.get("trailhead") or lot.get("trailhead")
                stats["dup-name"] = stats.get("dup-name", 0) + 1
        kept.extend(clusters)
    return kept, stats


# NPS ships every PUBLIC lot in a park unit, including staff housing, clinic and
# visitor-centre parking. USFS does not have that problem — its layer is filtered
# server-side to MARKERACTIVITY='Trailhead' — so this applies to NPS only.
#
# The NAME is the only usable signal, and this was measured the hard way (task
# #44): `LOTTYPE` is blank on all 805 AZ+NM lots, `OPENTOPUBLIC` is "Unknown" on
# 803 of them, and every judged lot — good and bad — is byte-identical on those
# attributes. A DISTANCE cut to the nearest OSM-corroborated lot was proposed on
# a perfect-looking sample of 8 and REFUTED on 6,570: real trailheads and staff
# lots appear in every band, from "Lower Residence Parking B" at 8 m to "Wawona
# Trailhead Parking" at 428 m. Do not re-propose one.
#
# So this is an INCLUSION rule, and that is the point: its failure mode is
# missing a real trailhead, never shipping a clinic. It accepts that the unnamed
# middle ("Parking Lot", "SOUTH BEACH PARKING") is unusable.
_NPS_TRAILHEAD_RE = re.compile(r"\btrail\s*head\b", re.I)


def pool_candidates(fed: list[dict]) -> list[dict]:
    """The federal points that belong in the GLOBAL POOL, chosen BEFORE any
    ownership decision (task #44).

    `assign_federal` gives a point to at most one blank area, which discards 87%
    of USFS trailheads and 95% of NPS lots — Peralta, String Lake, Two Medicine
    Lake and five Yellowstone trailheads among them — purely because some
    OVERLAPPING unit already had parking. The pool has no owner, so it does not
    have to make that call; it only has to be sure the point is a trailhead.

    Callers must apply the containment and road gates FIRST. This adds the one
    filter the pool needs beyond them: USFS is already trailhead-only at source,
    NPS is not, so NPS has to name itself. See `_NPS_TRAILHEAD_RE`.
    """
    out: list[dict] = []
    for f in fed:
        if f.get("source") == "nps":
            name = f.get("name") or ""
            if not _NPS_TRAILHEAD_RE.search(name):
                continue
            f = dict(f, trailhead=True)
        out.append(f)
    return out


def assign_federal(fed: list[dict], rings_by_area: dict[str, list],
                   blank_ids: set[str]) -> dict[str, list[dict]]:
    """Assign federal features to BLANK areas only (OSM stays authoritative
    where it mapped anything). A feature CONTAINED by a blank area is that
    area's (nested areas each get it). A feature contained by a NON-blank area
    is dropped (OSM covers it). A feature contained by NO area is an orphan —
    given to the nearest blank area whose boundary edge is within
    `_FED_EDGE_BUFFER_M` (wilderness trailheads sit just outside the polygon).
    Returns {area_id: [lots]}, deduped per area."""
    by_area: dict[str, list[dict]] = {}
    for f in fed:
        lat, lon = f["lat"], f["lon"]
        contained = [aid for aid, rings in rings_by_area.items()
                     if point_in_rings(lat, lon, rings)]
        blank_containers = [aid for aid in contained if aid in blank_ids]
        if blank_containers:
            for aid in blank_containers:
                by_area.setdefault(aid, []).append(f)
            continue
        # Edge fill, checked BEFORE the non-blank drop below. A wilderness
        # trailhead sits just OUTSIDE the wilderness polygon, on the road — but
        # that road is usually still INSIDE the surrounding national forest.
        # Testing `contained` first therefore dropped the feature as "OSM
        # covers it" and left the wilderness blank forever (55 of Arizona's 58
        # blank areas were Wildernesses nested in Coronado/Tonto/Prescott NF).
        # The nearest blank area's edge wins regardless of what else contains
        # the point; OSM still stays authoritative for any area that has lots.
        best_aid, best_d = None, _FED_EDGE_BUFFER_M
        for aid in blank_ids:
            rings = rings_by_area.get(aid)
            if not rings:
                continue
            d = dist_to_rings_m(lat, lon, rings)
            if d <= best_d:
                best_d, best_aid = d, aid
        if best_aid:
            by_area.setdefault(best_aid, []).append(f)
            continue
        if contained:
            continue  # inside a non-blank area — OSM already covers it
    return {aid: dedup(lots, PARKING_DEDUP_M) for aid, lots in by_area.items()}


def _bbox_of_geoms(files: list[Path]) -> list[float] | None:
    """Envelope [lonmin, latmin, lonmax, latmax] over the areas' own bboxes —
    the query window for federal sources. No hardcoded state extents, so this
    works for any state."""
    xs0, ys0, xs1, ys1 = [], [], [], []
    for f in files:
        try:
            b = json.loads(f.read_text()).get("bbox")
        except Exception:  # noqa: BLE001
            continue
        if b and len(b) == 4:
            xs0.append(b[0]); ys0.append(b[1]); xs1.append(b[2]); ys1.append(b[3])
    if not xs0:
        return None
    return [min(xs0), min(ys0), max(xs1), max(ys1)]


def _layer_clauses(selector: str) -> str:
    """parking + trailhead node/way/relation clauses inside `(selector)`."""
    parts = []
    for kv in ('"amenity"="parking"', '"highway"="trailhead"'):
        for typ in ("node", "way", "relation"):
            parts.append(f"{typ}[{kv}]{selector};")
    return "".join(parts)


def overpass_query(bbox: list[float]) -> str:
    """Single-area bbox query (used for the raw-data probe / debugging).
    `bbox` is the geom's [lonmin, latmin, lonmax, latmax]; Overpass wants
    (south, west, north, east)."""
    lonmin, latmin, lonmax, latmax = bbox
    b = f"({latmin},{lonmin},{latmax},{lonmax})"
    return "[out:json][timeout:180];(" + _layer_clauses(b) + ");out center tags;"


def overpass_state_query(state_code: str) -> str:
    """ONE query for a whole state's parking + trailheads, scoped to the
    state's ISO3166-2 admin area (same approach as seed-areas.py). Replaces
    247 per-area bbox queries with one — far faster and much kinder to
    Overpass (per-area querying triggered 504 storms)."""
    code = state_code.upper()
    return (
        "[out:json][timeout:600];"
        f'area["ISO3166-2"="US-{code}"]->.s;'
        "(" + _layer_clauses("(area.s)") + ");"
        "out center tags;"
    )


def _point(el: dict) -> tuple[float | None, float | None]:
    if el.get("type") == "node":
        return el.get("lat"), el.get("lon")
    c = el.get("center") or {}
    return c.get("lat"), c.get("lon")


def parse_parking(data: dict) -> list[dict]:
    """Overpass response -> candidate lots. Each: {lat, lon, name?, fee?,
    _self_th}. `_self_th` (stripped before write) marks a lot whose own
    element is also tagged highway=trailhead. Filters non-public access and
    on-street parking."""
    out: list[dict] = []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        if tags.get("amenity") != "parking":
            continue
        lat, lon = _point(el)
        if lat is None or lon is None:
            continue
        if tags.get("access") in _EXCLUDE_ACCESS:
            continue
        if tags.get("parking") in _STREET_PARKING:
            continue
        entry: dict = {"lat": lat, "lon": lon, "_self_th": tags.get("highway") == "trailhead"}
        if tags.get("name"):
            entry["name"] = tags["name"]
        if tags.get("fee") in ("yes", "no"):
            entry["fee"] = tags["fee"] == "yes"
        out.append(entry)
    return out


def parse_trailheads(data: dict) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for el in data.get("elements", []):
        if el.get("tags", {}).get("highway") == "trailhead":
            lat, lon = _point(el)
            if lat is not None and lon is not None:
                pts.append((lat, lon))
    return pts


def trail_vertices(geom: dict) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for t in geom.get("trails", []):
        for seg in t.get("segments", []):
            for p in seg:
                pts.append((p[0], p[1]))
    return pts


def min_dist_m(lat: float, lon: float, pts: list[tuple[float, float]]) -> float:
    """Nearest of `pts` in metres (inf if empty). Cheap bbox reject before the
    trig for speed on dense trail networks."""
    best = math.inf
    for plat, plon in pts:
        if abs(plat - lat) > 0.02 or abs(plon - lon) > 0.024:
            continue
        d = haversine_m(lat, lon, plat, plon)
        if d < best:
            best = d
    return best


def dedup(lots: list[dict], min_m: float) -> list[dict]:
    kept: list[dict] = []
    for lot in lots:
        dupe = next(
            (k for k in kept
             if haversine_m(lot["lat"], lot["lon"], k["lat"], k["lon"]) <= min_m),
            None,
        )
        if dupe is None:
            kept.append(lot)
        else:
            # Merge: prefer a name, keep trailhead corroboration, keep the
            # smaller trail distance. `_dist_m` is None for a trailhead-only
            # lot with no trail nearby, so min() over the non-None values.
            if "name" not in dupe and "name" in lot:
                dupe["name"] = lot["name"]
            dupe["trailhead"] = dupe.get("trailhead") or lot.get("trailhead")
            dists = [d for d in (dupe.get("_dist_m"), lot.get("_dist_m")) if d is not None]
            dupe["_dist_m"] = min(dists) if dists else None
    return kept


# Degrees to pre-filter the state-wide lot set down to an area's bbox before
# the distance math. Must exceed PARKING_TRAIL_MAX_M (~250 m ≈ 0.0025°) so no
# keepable lot is filtered out; 0.006° ≈ 660 m is a safe margin.
_BBOX_BUFFER_DEG = 0.006


def parking_for_area(geom: dict, lots: list[dict],
                     trailheads: list[tuple[float, float]],
                     rings: list | None = None,
                     stats: dict | None = None,
                     drops: list | None = None) -> list[dict]:
    """Assign the SHARED, state-wide parking + trailheads to one area.

    Pure + unit-tested. `lots`/`trailheads` are parsed once per state and
    passed to every area, so this MUST NOT mutate them (each kept lot is
    copied first). A bbox pre-filter trims the state set to the area's
    neighbourhood before the O(lots×vertices) distance math.

    `rings` (the area's buffered boundary outer rings, from
    `fetch_state_boundaries`) is the PRIMARY gate when present: a lot must be
    inside the boundary AND near a trail/trailhead. Proximity alone can't tell
    "inside the park" from "across the road". When `rings` is None (no
    boundary) it degrades to proximity-only. `stats["containment_dropped"]`
    counts lots that passed proximity but failed containment."""
    verts = trail_vertices(geom)
    if not verts:
        return []

    bbox = geom.get("bbox")
    if bbox:
        lonmin, latmin, lonmax, latmax = bbox
        b = _BBOX_BUFFER_DEG
        cand = [l for l in lots
                if latmin - b <= l["lat"] <= latmax + b
                and lonmin - b <= l["lon"] <= lonmax + b]
    else:
        cand = lots

    kept: list[dict] = []
    for src in cand:
        lot = dict(src)                       # copy — never mutate the shared lot
        dist = min_dist_m(lot["lat"], lot["lon"], verts)
        th = lot.pop("_self_th", False)
        if not th and trailheads:
            th = min_dist_m(lot["lat"], lot["lon"], trailheads) <= TRAILHEAD_COINCIDE_M
        if dist > PARKING_TRAIL_MAX_M and not th:
            continue
        if rings is not None and not point_in_rings(lot["lat"], lot["lon"], rings):
            if stats is not None:
                stats["containment_dropped"] = stats.get("containment_dropped", 0) + 1
            if drops is not None:
                drops.append((bool(th), dist_to_rings_m(lot["lat"], lot["lon"], rings)))
            continue
        lot["lat"] = round(lot["lat"], 6)
        lot["lon"] = round(lot["lon"], 6)
        lot["_dist_m"] = round(dist, 1) if math.isfinite(dist) else None
        if th:
            lot["trailhead"] = True
        kept.append(lot)

    kept = dedup(kept, PARKING_DEDUP_M)
    kept.sort(key=lambda p: (p["lat"], p["lon"]))
    return kept


def parking_for_geom(geom: dict, data: dict) -> list[dict]:
    """Convenience wrapper (used by the raw-probe + tests): parse a single
    Overpass response and assign it to one area."""
    return parking_for_area(geom, parse_parking(data), parse_trailheads(data))


def _classify_write(had, clean: list[dict]) -> str | None:
    """What this area's parking write is: "added" | "updated" | "cleared", or
    None for no change at all.

    An area with NO `parking` key reads back as `None`, and an area with no
    qualifying lots computes as `[]`. Those mean the same thing — blank — so
    they must compare EQUAL. Comparing the raw values did not: `None != []`,
    so every area that was blank before and blank after fell through to
    "cleared" and got reported as having LOST parking. The 2026-07-26 AZ/NM dry
    run said "would clear 55" and "would clear 45" when the true answer was
    zero — those were exactly the 59-4 and 51-6 areas that stayed blank. At
    national scale that is ~2,000 phantom losses in the log, which would bury a
    real regression instead of surfacing one.

    A genuine clear (`had` non-empty, `clean` empty) still reports as cleared.
    """
    before = had or []
    if before == clean:
        return None
    if clean and not before:
        return "added"
    if clean:
        return "updated"
    return "cleared"


def _strip_internal(lots: list[dict]) -> list[dict]:
    """Geom-ready copy: drop the `_dist_m` metric field (keep lat/lon/name/
    fee/trailhead)."""
    clean = []
    for lot in lots:
        clean.append({k: v for k, v in lot.items() if not k.startswith("_")})
    return clean


# ------------------------------------------------------------ pool sidecar

POOL_SIDECAR_VERSION = 1


def load_pool_sidecar(path: str | Path) -> dict[str, list[dict]]:
    """Read the committed pool sidecar as {STATE: [lot, ...]}. Missing or
    unreadable file reads as empty — the sidecar is additive, so starting from
    nothing is a valid first run."""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        doc = json.loads(p.read_text())
    except Exception:                       # noqa: BLE001
        return {}
    states = doc.get("states") if isinstance(doc, dict) else None
    if not isinstance(states, dict):
        return {}
    return {k.upper(): v for k, v in states.items() if isinstance(v, list)}


def merge_pool_sidecar(existing: dict[str, list[dict]],
                       fresh: dict[str, list[dict]]) -> tuple[dict[str, list[dict]], list[str]]:
    """Replace only the states this run actually processed, and REFUSE to let a
    state go from lots to none.

    Per-state replacement is what makes a single-state re-run safe: `--state az`
    must not delete the other fifty states' contributions. The refusal is the
    same fail-closed rule the sweeps use — an empty answer for a state that had
    lots is far more likely to be a fetch that quietly returned nothing than a
    real change, and the cost of being wrong is pins vanishing from the map.

    Returns `(merged, refused_state_codes)`.
    """
    merged = dict(existing)
    refused: list[str] = []
    for code, lots in fresh.items():
        code = code.upper()
        if not lots and existing.get(code):
            refused.append(code)
            continue
        merged[code] = lots
    return merged, refused


def write_pool_sidecar(path: str | Path, fresh: dict[str, list[dict]],
                       dry_run: bool) -> dict[str, int]:
    """Merge `fresh` into the sidecar at `path` and write it. Returns counts for
    the report. Writes nothing on a dry run."""
    existing = load_pool_sidecar(path)
    merged, refused = merge_pool_sidecar(existing, fresh)
    for code in refused:
        print(f"::warning::pool sidecar: {code} produced 0 lots but currently "
              f"has {len(existing[code])} — keeping the existing entry rather "
              "than emptying it. Re-run that state.", file=sys.stderr)
    total = sum(len(v) for v in merged.values())
    if not dry_run:
        doc = {"version": POOL_SIDECAR_VERSION,
               "states": {k: merged[k] for k in sorted(merged)}}
        Path(path).write_text(json.dumps(doc, separators=(",", ":"),
                                         sort_keys=False))
    verb = "would hold" if dry_run else "holds"
    print(f"\npool sidecar: {verb} {total} lot(s) across {len(merged)} state(s) "
          f"({len(fresh)} refreshed this run)")
    for code in sorted(fresh):
        print(f"  {code}: {len(fresh[code])}")
    return {"total": total, "states": len(merged), "refused": len(refused)}


# ---------------------------------------------------------------- reporting

def _histogram(dists: list[float]) -> str:
    buckets = [(0, 25), (25, 50), (50, 100), (100, 150), (150, 200), (200, 250)]
    counts = [0] * (len(buckets) + 1)  # +1 = trailhead-only (dist > 250 / None)
    for d in dists:
        if d is None or d > 250:
            counts[-1] += 1
            continue
        for i, (lo, hi) in enumerate(buckets):
            if lo <= d < hi:
                counts[i] += 1
                break
    total = max(1, len(dists))
    lines = ["  lot -> nearest trail (metres):"]
    labels = [f"{lo:>3}-{hi:<3}" for lo, hi in buckets] + ["trailhead-only"]
    for label, c in zip(labels, counts):
        bar = "#" * round(40 * c / total)
        lines.append(f"    {label:>13} | {c:5d}  {bar}")
    return "\n".join(lines)


def print_report(per_area: list[tuple[str, list[dict]]], dry_run: bool) -> None:
    areas = len(per_area)
    with_lots = [(aid, lots) for aid, lots in per_area if lots]
    all_lots = [lot for _, lots in per_area for lot in lots]
    dists = [lot.get("_dist_m") for lot in all_lots]
    named = sum(1 for lot in all_lots if lot.get("name"))
    th = sum(1 for lot in all_lots if lot.get("trailhead"))
    counts = sorted((len(lots) for _, lots in per_area), reverse=True)

    def pct(n: int) -> str:
        return f"{100 * n / max(1, len(all_lots)):.0f}%"

    print("\n" + "=" * 60)
    print("PARKING EXTRACTION REPORT" + ("  (dry run — nothing written)" if dry_run else ""))
    print("=" * 60)
    print(f"  areas processed        : {areas}")
    print(f"  areas with >=1 lot     : {len(with_lots)} ({100 * len(with_lots) / max(1, areas):.0f}%)")
    print(f"  areas with 0 lots      : {areas - len(with_lots)}")
    print(f"  total lots             : {len(all_lots)}")
    if counts:
        mid = counts[len(counts) // 2]
        p95 = counts[max(0, int(len(counts) * 0.05))]
        print(f"  lots/area  median {mid}  p95 {p95}  max {counts[0]}")
    print(f"  named lots             : {named} ({pct(named)})")
    print(f"  trailhead-corroborated : {th} ({pct(th)})  <- precision signal")
    if dists:
        print(_histogram(dists))
    # Outlier areas — where a loose threshold would show as bbox bleed.
    top = sorted(with_lots, key=lambda x: -len(x[1]))[:10]
    if top:
        print("  highest lot counts (eyeball these few, not all areas):")
        for aid, lots in top:
            print(f"    {len(lots):3d}  {aid}")
    print("=" * 60)


def check_golden(area_counts: dict[str, int]) -> bool:
    """Regression gate: assert known areas land in expected [min,max] lot
    bounds. Loose bounds now (no real-data baseline yet); tighten after the
    first real run. Only checks golden areas that were in this run."""
    if not GOLDEN_FILE.exists():
        return True
    golden = json.loads(GOLDEN_FILE.read_text())
    ok = True
    checked = 0
    print("\nGOLDEN CHECK:")
    for aid, bounds in sorted(golden.items()):
        if aid not in area_counts:
            continue
        checked += 1
        n = area_counts[aid]
        lo, hi = bounds.get("min", 0), bounds.get("max", 10 ** 9)
        status = "ok  " if lo <= n <= hi else "FAIL"
        if status == "FAIL":
            ok = False
        print(f"  {status}  {aid}: {n} lots (expect {lo}-{hi})")
    if checked == 0:
        print("  (no golden areas in this run)")
    return ok


# ---------------------------------------------------------------- driver

def fetch_state(state_code: str) -> dict | None:
    """One Overpass query for the whole state, with the same retry backoff.
    Returns the response, or None if every attempt failed."""
    for i, backoff in enumerate([0] + RETRY_BACKOFFS_SECONDS):
        if backoff:
            time.sleep(backoff)
        try:
            return fetch_overpass(overpass_state_query(state_code))
        except Exception as e:  # noqa: BLE001
            print(f"  {state_code.upper()}: overpass attempt {i + 1} failed ({e})",
                  file=sys.stderr)
    return None


def geom_by_state() -> dict[str, list[Path]]:
    """Map each state NAME -> its geom files, scanning the geom dir once."""
    groups: dict[str, list[Path]] = {}
    for f in sorted(GEOM_DIR.glob("*.json")):
        try:
            st = json.loads(f.read_text()).get("state")
        except Exception:  # noqa: BLE001
            continue
        if st:
            groups.setdefault(st, []).append(f)
    return groups


def _state_boundaries(files: list[Path]) -> tuple[dict[str, list], int, bool]:
    """Fetch every area's boundary polygon for this state in one batched
    Overpass query. Returns ({area_id: rings}, n_areas_with_boundary, ok).

    ok=False means the fetch itself FAILED (Overpass down / no shapely) — the
    containment gate is unavailable, so proximity-only results would carry the
    across-road bleed the gate exists to stop. Callers must not WRITE in that
    state (2026-07-18: a transient 504 here silently degraded a dry-run to
    proximity-only and Thunderbird ballooned 14 -> 26 lots; only the golden
    check caught it, and golden covers just 7 AZ areas — a nationwide real run
    would have shipped the bleed everywhere else)."""
    rel_of: dict[str, int] = {}
    for f in files:
        try:
            rid = json.loads(f.read_text()).get("osm_relation_id")
        except Exception:  # noqa: BLE001
            continue
        if rid:
            rel_of[f.stem] = rid
    if not rel_of:
        # Genuinely nothing to fetch — proximity-only is the best possible
        # here, not a degradation.
        return {}, 0, True
    # The boundary query is one heavy batched `out geom` over every rel id, so
    # it 504s more than the parking query does. Retry with the same backoff
    # ladder as fetch_state before declaring the containment gate unavailable.
    rel_ids = sorted(set(rel_of.values()))
    per_rel = None
    local_cause = None
    for i, backoff in enumerate([0] + RETRY_BACKOFFS_SECONDS):
        if backoff:
            time.sleep(backoff)
        try:
            per_rel = fetch_state_boundaries(rel_ids)
            break
        except ImportError as e:
            # NOT transient — retrying a missing module just burns the whole
            # backoff ladder (~7 min) and then blames Overpass for a local
            # dependency problem. The CI parking workflow shipped with no
            # install step at all, so every run died on `No module named
            # 'shapely'` while the log advised waiting for Overpass to recover.
            local_cause = f"{type(e).__name__}: {e}"
            print(f"  boundary fetch cannot run locally ({local_cause}) — "
                  f"not retrying; this is a missing dependency, NOT an "
                  f"Overpass outage. Install shapely.", file=sys.stderr)
            break
        except Exception as e:  # noqa: BLE001
            print(f"  boundary fetch attempt {i + 1} failed ({e})", file=sys.stderr)
    if per_rel is None:
        print(f"  boundary fetch FAILED ({local_cause or 'after retries'})",
              file=sys.stderr)
        return {}, 0, False
    rings_by_area = {aid: per_rel[rid] for aid, rid in rel_of.items()
                     if rid in per_rel}
    return rings_by_area, len(rings_by_area), True


def _rel_of(files: list[Path]) -> dict[str, int]:
    """{area slug: osm_relation_id} for the areas that name one."""
    out: dict[str, int] = {}
    for f in files:
        try:
            rid = json.loads(f.read_text()).get("osm_relation_id")
        except Exception:  # noqa: BLE001
            continue
        if rid:
            out[f.stem] = int(rid)
    return out


def _state_boundaries_local(files: list[Path], local) -> tuple[dict[str, list], int, bool]:
    """`_state_boundaries` answered from the local extract instead of Overpass.

    Same return shape and the same failure semantics: a cache that cannot answer
    returns ok=False rather than an empty gate, because "no boundaries" and "the
    containment gate is unavailable" must never look alike — that confusion is
    what let a transient 504 balloon Thunderbird from 14 lots to 26.
    """
    rel_of = _rel_of(files)
    if not rel_of:
        return {}, 0, True
    try:
        per_rel = local.boundaries(sorted(set(rel_of.values())))
    except Exception as e:  # noqa: BLE001
        print(f"  local boundary cache FAILED ({e})", file=sys.stderr)
        return {}, 0, False
    rings_by_area = {aid: per_rel[rid] for aid, rid in rel_of.items()
                     if rid in per_rel}
    return rings_by_area, len(rings_by_area), True


def _gate_key(f: dict) -> str:
    return f"{f['lat']:.6f},{f['lon']:.6f}"


def road_gate_local(fed: list[dict], local,
                    stats: dict | None = None) -> tuple[list[dict], bool]:
    """The road gate, answered from the cache built by build-local-osm-cache.py.

    Identical contract to `road_gate`, including the `ok` flag. Points the cache
    has never seen fall back to Overpass for exactly those points rather than
    being guessed either way — a cache older than the current ArcGIS answer is
    the expected case after the Forest Service publishes, and guessing would
    either invent road access or delete a real trailhead.
    """
    if not fed:
        return fed, True
    try:
        kept, unknown = local.road_gate(fed, _gate_key)
    except Exception as e:  # noqa: BLE001
        print(f"  local road-gate cache FAILED ({e}); falling back to Overpass",
              file=sys.stderr)
        return road_gate(fed, stats=stats)
    if unknown:
        print(f"  road gate: {len(unknown)} point(s) not in the cache "
              f"(built before this ArcGIS answer) — asking Overpass for those",
              file=sys.stderr)
        extra, ok = road_gate(unknown, stats=stats)
        if not ok:
            return [], False
        kept = kept + extra
    dropped = len(fed) - len(kept)
    if stats is not None:
        stats["federal_road_dropped"] = stats.get("federal_road_dropped", 0) + dropped
    print(f"  federal road gate (local): kept {len(kept)}/{len(fed)} "
          f"(dropped {dropped} roadless)")
    return kept, True


def print_containment_diag(cont_diag: list[tuple[str, int, list]]) -> None:
    """Measure the wilderness-trailhead problem: lots that passed the
    proximity/trailhead check but were dropped for sitting OUTSIDE the boundary.
    Reports how many are trailhead-corroborated (OSM says a trail starts there)
    by distance-outside, and — the deciding number — how many currently-BLANK
    areas would light up if we admitted a trailhead lot within buffer B outside.
    Read-only: informs where to set a trailhead-only outward buffer; ships nothing."""
    if not cont_diag:
        return
    all_drops = [d for _, _, ds in cont_diag for d in ds]
    if not all_drops:
        return
    edges = [(0, 50), (50, 100), (100, 150), (150, 250), (250, 500), (500, 10 ** 9)]
    labels = ["  0-50", " 50-100", "100-150", "150-250", "250-500", "  >500"]

    def bucket(dm: float) -> int:
        for i, (lo, hi) in enumerate(edges):
            if lo <= dm < hi:
                return i
        return len(edges) - 1

    th_hist = [0] * len(edges)
    non_hist = [0] * len(edges)
    for th, dm in all_drops:
        (th_hist if th else non_hist)[bucket(dm)] += 1

    print("\n  CONTAINMENT DIAGNOSTIC (measure only — nothing changed):")
    print(f"  lots dropped just OUTSIDE a boundary, by metres out:")
    print(f"    {'range(m)':<9}{'trailhead':<11}{'plain':<8}")
    for i, lab in enumerate(labels):
        print(f"    {lab:<9}{th_hist[i]:<11}{non_hist[i]:<8}")

    blank = [(aid, ds) for aid, kept, ds in cont_diag if kept == 0]
    print(f"\n  currently-blank areas w/ a boundary: {len(blank)}")
    print(f"  of those, # rescued if we admit a TRAILHEAD lot within B metres out:")
    for B in (50, 100, 150, 250):
        n = sum(1 for _, ds in blank if any(th and dm <= B for th, dm in ds))
        print(f"    B={B:>3}m : {n} areas rescued")
    print(f"  (plain-parking admit for the same buffers, for contrast:)")
    for B in (50, 100, 150, 250):
        n = sum(1 for _, ds in blank if any((not th) and dm <= B for th, dm in ds))
        print(f"    B={B:>3}m : {n} areas")


def print_trailhead_cover(th_cover: list[tuple[str, int, int]]) -> None:
    """Measure the trailhead-marker lever: how many areas (esp. PARKING-BLANK
    ones) contain a `highway=trailhead` node we could surface as a distinct
    'trail starts here' marker where no lot is mapped. Read-only."""
    if not th_cover:
        return
    total = len(th_cover)
    with_th = sum(1 for _, _, n in th_cover if n > 0)
    blank = [(a, n) for a, k, n in th_cover if k == 0]
    blank_with_th = sum(1 for _, n in blank if n > 0)
    print("\n  TRAILHEAD-MARKER DIAGNOSTIC (measure only):")
    print(f"  boundaried areas                     : {total}")
    print(f"    with >=1 trailhead node inside      : {with_th}")
    print(f"  parking-BLANK boundaried areas       : {len(blank)}")
    print(f"    of those, >=1 trailhead node inside : {blank_with_th}"
          f"  <- blanks a trailhead marker would fill")


def print_federal_fill(fed_fill: list[tuple[str, str, list[str]]]) -> None:
    """Report which blank areas were filled from federal sources, and by which
    agency — this is the eyeball list before a real write."""
    if not fed_fill:
        return
    import collections
    src_totals = collections.Counter(s for _, _, srcs in fed_fill for s in srcs)
    print(f"\n  FEDERAL FILL (tier-2 — OSM-blank areas given agency access points):")
    print(f"  areas filled : {len(fed_fill)} | pins by source : {dict(src_totals)}")
    for aid, name, srcs in sorted(fed_fill, key=lambda x: x[1] or x[0]):
        by = collections.Counter(srcs)
        print(f"    {name or aid:45} {dict(by)}")


def process(state_codes: list[str], dry_run: bool, use_federal: bool = True,
            pool_sidecar: str | None = None, local=None) -> bool:
    """`local` is a `_local_osm.LocalOSM` when the homelab cache should answer
    the three queries this used to send to Overpass. Everything downstream of the
    fetch is identical either way — the containment maths, the trailhead
    corroboration and the gates are the single tested copy."""
    groups = geom_by_state()
    # {STATE: [lot, ...]} for the global pool — populated only when a sidecar
    # path was asked for, and replaced per state so a single-state run cannot
    # wipe the other fifty.
    pool_by_state: dict[str, list[dict]] = {}
    per_area: list[tuple[str, list[dict]]] = []
    area_counts: dict[str, int] = {}
    stats: dict[str, int] = {}
    # (area_id, kept_count, [(trailhead_bool, dist_outside_m), ...]) for areas
    # that HAD a boundary — feeds the containment diagnostic below.
    cont_diag: list[tuple[str, int, list]] = []
    # (area_id, kept_lot_count, trailhead_nodes_inside_boundary)
    th_cover: list[tuple[str, int, int]] = []
    # (area_id, name, [source,...]) for blank areas filled from federal data.
    fed_fill: list[tuple[str, str, list[str]]] = []
    # Split the outcome three ways. A single `changed` counter reported
    # "wrote parking for N area(s)" while N included areas whose parking was
    # REMOVED — Colorado logged 261 when 197 gained parking and 64 had a stale
    # key cleared. The number was true; the sentence inverted its meaning.
    added = 0        # had no parking, now has some
    updated = 0      # had parking, lots changed
    cleared = 0      # had parking, no longer qualifies -> key removed
    boundary_failed = False
    for code in state_codes:
        name = STATE_NAMES.get(code.upper())
        files = groups.get(name, []) if name else []
        if not files:
            continue
        if local is not None:
            print(f"{code.upper()}: local extract for {len(files)} areas...")
            bbox = _bbox_of_geoms(files)
            try:
                data = local.parking_elements(bbox) if bbox else {"elements": []}
            except Exception as e:  # noqa: BLE001
                # Same treatment as an Overpass outage: skip the state rather
                # than write a state's worth of parking from a cache that could
                # not answer.
                print(f"  {code.upper()}: SKIPPED (local cache: {e})", file=sys.stderr)
                continue
        else:
            print(f"{code.upper()}: 1 Overpass query for {len(files)} areas...")
            data = fetch_state(code)
            if data is None:
                print(f"  {code.upper()}: SKIPPED (overpass unavailable)", file=sys.stderr)
                continue
        lots = parse_parking(data)
        trailheads = parse_trailheads(data)
        print(f"  {code.upper()}: {len(lots)} parking + {len(trailheads)} trailheads statewide")

        rings_by_area, n_bnd, bnd_ok = (
            _state_boundaries_local(files, local) if local is not None
            else _state_boundaries(files))
        print(f"  {code.upper()}: {n_bnd}/{len(files)} area boundaries loaded "
              f"(containment gate; rest proximity-only)")
        if not bnd_ok:
            # No containment gate -> proximity-only bleed would ship. Refuse
            # to write this state; a dry run may proceed (report is still
            # useful) but is flagged as a failed run either way.
            boundary_failed = True
            print(f"  {code.upper()}: boundary fetch failed — "
                  f"{'skipping WRITES for this state' if not dry_run else 'dry-run report only'} "
                  "(containment gate unavailable; see the cause logged above — "
                  "a missing dependency needs fixing, an Overpass error needs a rerun)",
                  file=sys.stderr)
            if not dry_run:
                continue

        # Pass 1 — OSM parking per area (primary). No write yet: we may still
        # fill blanks from federal sources below, and want one write per file.
        state_areas: list[tuple[Path, dict, list]] = []
        for f in files:
            geom = json.loads(f.read_text())
            if not geom.get("trails") or not geom.get("bbox"):
                continue
            rings = rings_by_area.get(f.stem)
            area_drops: list = []
            kept = parking_for_area(geom, lots, trailheads, rings=rings,
                                    stats=stats, drops=area_drops)
            if rings is not None:
                cont_diag.append((f.stem, len(kept), area_drops))
                th_in = sum(1 for tlat, tlon in trailheads
                            if point_in_rings(tlat, tlon, rings))
                th_cover.append((f.stem, len(kept), th_in))
            state_areas.append((f, geom, kept))

        # Pass 2 — tier-2 federal fill for areas OSM left blank (needs the
        # containment boundaries; skipped if they didn't load or --no-federal).
        if use_federal and bnd_ok:
            blank_ids = {f.stem for f, _, kept in state_areas
                         if not kept and rings_by_area.get(f.stem)}
            # The fetch used to be gated on `blank_ids` alone, which is exactly
            # why a state where OSM already mapped every area contributed NOTHING
            # to the pool — there was no blank area to fill, so no federal call
            # was ever made. The pool has no owner and therefore no such
            # precondition, so wanting a sidecar is reason enough to fetch.
            want_pool = pool_sidecar is not None
            if blank_ids or want_pool:
                bbox = _bbox_of_geoms(files)
                fed, fed_failed = fetch_federal(bbox) if bbox else ([], set())
                road_ok = True
                if fed:
                    fed, road_ok = (road_gate_local(fed, local, stats=stats)
                                    if local is not None
                                    else road_gate(fed, stats=stats))
                    if not road_ok:
                        # The gate itself could not run, so EVERY source is
                        # unverified this run — not "these points have no road".
                        # Treat all of them as unavailable so existing pins are
                        # carried forward rather than deleted.
                        fed_failed |= {s["key"] for s in _FED_SOURCES}

                # POOL EMIT — here, BEFORE assign_federal, is the whole point of
                # task #44. The containment gate ran when the boundaries loaded
                # (`bnd_ok` above) and the road gate ran just now, so these
                # points have passed every QUALITY check; the only thing they
                # have not been put through is the ownership question, which the
                # pool does not ask.
                #
                # Skipped entirely when a source failed or the gate could not
                # run: an unverified point must not enter a pool that every area
                # in the country reads.
                if want_pool:
                    if fed_failed or not road_ok:
                        print(f"::warning::{code.upper()}: pool sidecar SKIPPED "
                              f"— source(s) {sorted(fed_failed) or 'ok'}, road "
                              f"gate ok={road_ok}. Existing entry kept.",
                              file=sys.stderr)
                    else:
                        cands = pool_candidates(fed)
                        cands, why = clean_federal_lots(cands)
                        pool_by_state[code.upper()] = _strip_internal(cands)
                        print(f"  {code.upper()}: pool sidecar {len(cands)} lot(s) "
                              f"from {len(fed)} gated federal point(s) "
                              f"({why.get('dup-name', 0)} same-name collapsed)")

            if blank_ids:
                fed_by_area = assign_federal(fed, rings_by_area, blank_ids) if fed else {}
                n_area = n_pin = 0
                n_dedup = 0
                for f, geom, kept in state_areas:
                    add = fed_by_area.get(f.stem)
                    if add:
                        # One facility can arrive as several polygon centroids
                        # (NPS "East Portal Parking" x3). Collapse before the
                        # count so the reported fill is facilities, not polygons.
                        add, why = clean_federal_lots(add)
                        n_dedup += why.get("dup-name", 0)
                        kept.extend(add)
                        n_area += 1
                        n_pin += len(add)
                        fed_fill.append((f.stem, geom.get("name"),
                                         [lot["source"] for lot in add]))
                if n_dedup:
                    print(f"  {code.upper()}: collapsed {n_dedup} duplicate-named "
                          f"federal pin(s) (same facility, several polygons)")
                print(f"  {code.upper()}: federal fill added {n_pin} pin(s) to "
                      f"{n_area} of {len(blank_ids)} blank area(s)")
                if fed_failed:
                    # FAIL CLOSED. A layer we could not read must not delete the
                    # pins it gave us last time: carry them forward so pass 3
                    # sees no change. Counted separately from the fill above so
                    # "federal fill added N" stays an honest count of NEW pins.
                    #
                    # Only for areas that are blank in THIS run — if OSM now maps
                    # parking for an area, OSM is authoritative and the old
                    # federal pins are meant to go.
                    n_carry = n_areas_carry = 0
                    for f, geom, kept in state_areas:
                        if f.stem not in blank_ids:
                            continue
                        old = [lot for lot in (geom.get("parking") or [])
                               if lot.get("source") in fed_failed]
                        if old:
                            kept.extend(old)
                            n_carry += len(old)
                            n_areas_carry += 1
                    print(f"::warning::{code.upper()}: federal source(s) "
                          f"{sorted(fed_failed)} unavailable — kept {n_carry} "
                          f"existing pin(s) across {n_areas_carry} area(s) rather "
                          f"than clearing them. RE-RUN THIS STATE: any area the "
                          f"source would newly fill is still missing.",
                          file=sys.stderr)

        # Pass 3 — record + write (one write per changed file).
        for f, geom, kept in state_areas:
            per_area.append((f.stem, kept))
            area_counts[f.stem] = len(kept)
            clean = _strip_internal(kept)
            had = geom.get("parking")
            outcome = _classify_write(had, clean)
            if outcome:
                # Classify BEFORE writing: clearing a stale key is a legitimate
                # outcome (better than leaving pins for parking that no longer
                # qualifies) but it must never be reported as if parking were
                # added.
                if outcome == "added":
                    added += 1
                elif outcome == "updated":
                    updated += 1
                else:
                    cleared += 1
                if not dry_run:
                    if clean:
                        geom["parking"] = clean
                    else:
                        geom.pop("parking", None)
                    f.write_text(json.dumps(geom))

    if pool_sidecar is not None:
        write_pool_sidecar(pool_sidecar, pool_by_state, dry_run)

    print_report(per_area, dry_run)
    print_federal_fill(fed_fill)
    if stats.get("containment_dropped"):
        print(f"  dropped by containment : {stats['containment_dropped']}  "
              "(near a trail but OUTSIDE the park boundary — across-road bleed)")
    print_containment_diag(cont_diag)
    print_trailhead_cover(th_cover)
    golden_ok = check_golden(area_counts)
    # Report the three outcomes separately. "wrote parking for N" conflated
    # them, so a run that CLEARED stale keys read as if it had added parking.
    verb = "would add" if dry_run else "added"
    verb2 = "would update" if dry_run else "updated"
    verb3 = "would clear" if dry_run else "cleared"
    print(f"\nparking: {verb} {added}, {verb2} {updated}, "
          f"{verb3} {cleared} (stale key removed — no longer qualifies)")
    print(f"  areas touched in total: {added + updated + cleared}")
    if cleared:
        print(f"  NOTE: {cleared} area(s) LOST parking in this run. That is "
              "intended when parking no longer qualifies (OSM deleted, or the "
              "containment gate tightened), but check it is not a regression.")
    if boundary_failed:
        print("\n!! boundary fetch failed for >=1 state — containment gate was "
              "unavailable there (writes skipped); rerun those states.",
              file=sys.stderr)
    return golden_ok and not boundary_failed


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--state", help="two-letter state code, e.g. az")
    g.add_argument("--all", action="store_true", help="every state")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-federal", action="store_true",
                    help="skip the tier-2 federal (BLM/NPS/USFS) fill of "
                         "OSM-blank areas; OSM parking only")
    ap.add_argument("--local-cache", nargs="?", const="", metavar="DIR",
                    help="answer the parking, boundary and road-gate queries "
                         "from the homelab's OSM extracts instead of Overpass "
                         "(build it with scripts/build-local-osm-cache.py). "
                         "Defaults to $TREKDEX_OSM_DIR/cache. Overpass was "
                         "measured at 87%% of a state's runtime (VT) and 71%% "
                         "(CO), and is the reason the CI roll caps at 8 jobs.")
    ap.add_argument("--pool-sidecar", metavar="PATH",
                    help="also emit road-gated federal trailheads to this "
                         "sidecar, BEFORE ownership assignment, for the global "
                         "parking pool (task #44). Makes the federal fetch run "
                         "for every state, not only states with blank areas.")
    args = ap.parse_args()

    if args.all:
        codes = sorted(STATE_NAMES.keys())
    else:
        if args.state.upper() not in STATE_NAMES:
            raise SystemExit(f"Unknown state code: {args.state}")
        codes = [args.state]
    if args.pool_sidecar and args.no_federal:
        raise SystemExit("--pool-sidecar needs the federal sources; "
                         "it cannot be combined with --no-federal")

    local = None
    if args.local_cache is not None:
        # Import here so a run without --local-cache has no new dependency and
        # GitHub Actions, which has no extracts, is unaffected.
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from _local_osm import LocalOSM
        local = LocalOSM(cache_dir=args.local_cache or None)
        print(f"local OSM cache: {local.dir}")

    golden_ok = process(codes, args.dry_run, use_federal=not args.no_federal,
                        pool_sidecar=args.pool_sidecar, local=local)
    if not golden_ok:
        sys.exit(2)


if __name__ == "__main__":
    main()
