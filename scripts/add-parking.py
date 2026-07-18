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
import sys
import time
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
                     stats: dict | None = None) -> list[dict]:
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


def _strip_internal(lots: list[dict]) -> list[dict]:
    """Geom-ready copy: drop the `_dist_m` metric field (keep lat/lon/name/
    fee/trailhead)."""
    clean = []
    for lot in lots:
        clean.append({k: v for k, v in lot.items() if not k.startswith("_")})
    return clean


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


def _state_boundaries(files: list[Path]) -> tuple[dict[str, list], int]:
    """Fetch every area's boundary polygon for this state in one batched
    Overpass query. Returns ({area_id: rings}, n_areas_with_boundary). On any
    failure (no shapely, Overpass down) returns ({}, 0) so the run degrades to
    proximity-only rather than dying."""
    rel_of: dict[str, int] = {}
    for f in files:
        try:
            rid = json.loads(f.read_text()).get("osm_relation_id")
        except Exception:  # noqa: BLE001
            continue
        if rid:
            rel_of[f.stem] = rid
    if not rel_of:
        return {}, 0
    try:
        per_rel = fetch_state_boundaries(sorted(set(rel_of.values())))
    except Exception as e:  # noqa: BLE001
        print(f"  boundary fetch failed ({e}); proximity-only fallback",
              file=sys.stderr)
        return {}, 0
    rings_by_area = {aid: per_rel[rid] for aid, rid in rel_of.items()
                     if rid in per_rel}
    return rings_by_area, len(rings_by_area)


def process(state_codes: list[str], dry_run: bool) -> bool:
    groups = geom_by_state()
    per_area: list[tuple[str, list[dict]]] = []
    area_counts: dict[str, int] = {}
    stats: dict[str, int] = {}
    changed = 0
    for code in state_codes:
        name = STATE_NAMES.get(code.upper())
        files = groups.get(name, []) if name else []
        if not files:
            continue
        print(f"{code.upper()}: 1 Overpass query for {len(files)} areas...")
        data = fetch_state(code)
        if data is None:
            print(f"  {code.upper()}: SKIPPED (overpass unavailable)", file=sys.stderr)
            continue
        lots = parse_parking(data)
        trailheads = parse_trailheads(data)
        print(f"  {code.upper()}: {len(lots)} parking + {len(trailheads)} trailheads statewide")

        rings_by_area, n_bnd = _state_boundaries(files)
        print(f"  {code.upper()}: {n_bnd}/{len(files)} area boundaries loaded "
              f"(containment gate; rest proximity-only)")

        for f in files:
            geom = json.loads(f.read_text())
            if not geom.get("trails") or not geom.get("bbox"):
                continue
            rings = rings_by_area.get(f.stem)
            kept = parking_for_area(geom, lots, trailheads, rings=rings, stats=stats)
            per_area.append((f.stem, kept))
            area_counts[f.stem] = len(kept)
            clean = _strip_internal(kept)
            if geom.get("parking") != clean:
                changed += 1
                if not dry_run:
                    if clean:
                        geom["parking"] = clean
                    else:
                        geom.pop("parking", None)
                    f.write_text(json.dumps(geom))

    print_report(per_area, dry_run)
    if stats.get("containment_dropped"):
        print(f"  dropped by containment : {stats['containment_dropped']}  "
              "(near a trail but OUTSIDE the park boundary — across-road bleed)")
    golden_ok = check_golden(area_counts)
    verb = "would write" if dry_run else "wrote"
    print(f"\n{verb} parking for {changed} area(s).")
    return golden_ok


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--state", help="two-letter state code, e.g. az")
    g.add_argument("--all", action="store_true", help="every state")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.all:
        codes = sorted(STATE_NAMES.keys())
    else:
        if args.state.upper() not in STATE_NAMES:
            raise SystemExit(f"Unknown state code: {args.state}")
        codes = [args.state]
    golden_ok = process(codes, args.dry_run)
    if not golden_ok:
        sys.exit(2)


if __name__ == "__main__":
    main()
