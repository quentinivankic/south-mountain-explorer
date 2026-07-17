#!/usr/bin/env python3
"""Enrich published area geom with trailhead PARKING from OpenStreetMap.

People need to know where to *park* before they can hike — the app draws a
beautiful trail network but says nothing about how to get to it. This script
adds an `amenity=parking` layer to each area's published geom so the app can
draw parking pins (and, later, offer directions to one).

How it works (per area, driven off the ALREADY-published geom — no OSM
extract or homelab needed, just Overpass, which CI runners can reach):
  1. Read the area's bbox from `public/areas/geom/<id>.json`.
  2. Query Overpass for parking (node/way/relation `amenity=parking`) in
     that bbox, `out center` so ways/relations resolve to a point.
  3. Keep only lots within `PARKING_TRAIL_MAX_M` of a trail vertex — that
     turns "every lot in the bbox" (shops, neighborhoods) into "the lots
     that actually serve this trail network," i.e. trailhead parking.
  4. Dedup near-duplicates and write a compact `parking` list into the geom.

    python3 scripts/add-parking.py --state az            # write
    python3 scripts/add-parking.py --state az --dry-run  # report only
    python3 scripts/add-parking.py --all                 # every state

Idempotent: re-running over unchanged OSM reproduces the same `parking`.
Post-process for now (a republish would drop it), mirroring how DEM
elevation started — fold into the publish pipeline once it's proven.

Overpass is flaky under load; each area retries a few times before it's
skipped (its geom is left untouched, never emptied).
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

# A lot is "trailhead parking" if it's within this many metres of any trail
# vertex. 250 m keeps the lot at the end of a trail (and small pull-offs a
# short walk away) while dropping unrelated city/retail parking that merely
# shares the area's bbox.
PARKING_TRAIL_MAX_M = 250.0
# Two lots closer than this are the same lot mapped twice (a node inside a
# way, or overlapping polygons); keep one.
PARKING_DEDUP_M = 40.0

RETRY_BACKOFFS_SECONDS = [30, 90, 300]


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def overpass_parking_query(bbox: list[float]) -> str:
    """`bbox` is the geom's [lonmin, latmin, lonmax, latmax]; Overpass wants
    (south, west, north, east)."""
    lonmin, latmin, lonmax, latmax = bbox
    b = f"{latmin},{lonmin},{latmax},{lonmax}"
    return (
        "[out:json][timeout:180];"
        "("
        f'node["amenity"="parking"]({b});'
        f'way["amenity"="parking"]({b});'
        f'relation["amenity"="parking"]({b});'
        ");"
        "out center tags;"
    )


def parse_parking(data: dict) -> list[dict]:
    """Overpass response -> list of {lat, lon, name?, fee?}. Ways/relations
    use their `center` (from `out center`); nodes use their own lat/lon.
    `access=private/no` lots are dropped — you can't park there."""
    out: list[dict] = []
    for el in data.get("elements", []):
        if el.get("type") == "node":
            lat, lon = el.get("lat"), el.get("lon")
        else:
            c = el.get("center") or {}
            lat, lon = c.get("lat"), c.get("lon")
        if lat is None or lon is None:
            continue
        tags = el.get("tags", {})
        if tags.get("access") in ("private", "no"):
            continue
        entry: dict = {"lat": lat, "lon": lon}
        if tags.get("name"):
            entry["name"] = tags["name"]
        if tags.get("fee") in ("yes", "no"):
            entry["fee"] = tags["fee"] == "yes"
        out.append(entry)
    return out


def trail_vertices(geom: dict) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for t in geom.get("trails", []):
        for seg in t.get("segments", []):
            for p in seg:
                pts.append((p[0], p[1]))
    return pts


def near_trail(lat: float, lon: float, verts: list[tuple[float, float]],
               max_m: float) -> bool:
    for vlat, vlon in verts:
        # Cheap bbox reject before the trig (≈ max_m in degrees).
        if abs(vlat - lat) > 0.004 or abs(vlon - lon) > 0.005:
            continue
        if haversine_m(lat, lon, vlat, vlon) <= max_m:
            return True
    return False


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
        elif "name" not in dupe and "name" in lot:
            # Prefer the named copy of a duplicated lot.
            dupe.update(lot)
    return kept


def parking_for_geom(geom: dict, data: dict) -> list[dict]:
    """Pure transform (unit-tested): Overpass parking + geom trails -> the
    trailhead-parking list written into the geom."""
    verts = trail_vertices(geom)
    if not verts:
        return []
    lots = [p for p in parse_parking(data)
            if near_trail(p["lat"], p["lon"], verts, PARKING_TRAIL_MAX_M)]
    for lot in lots:
        lot["lat"] = round(lot["lat"], 6)
        lot["lon"] = round(lot["lon"], 6)
    lots = dedup(lots, PARKING_DEDUP_M)
    lots.sort(key=lambda p: (p["lat"], p["lon"]))
    return lots


def geom_files_for_state(state_code: str) -> list[Path]:
    name = STATE_NAMES.get(state_code.upper())
    if not name:
        raise SystemExit(f"Unknown state code: {state_code}")
    files = []
    for f in sorted(GEOM_DIR.glob("*.json")):
        try:
            g = json.loads(f.read_text())
        except Exception:  # noqa: BLE001
            continue
        if g.get("state") == name:
            files.append(f)
    return files


def process(files: list[Path], dry_run: bool) -> None:
    total_lots = 0
    changed = 0
    for f in files:
        geom = json.loads(f.read_text())
        if not geom.get("trails"):
            continue
        bbox = geom.get("bbox")
        if not bbox:
            continue
        data = None
        for i, backoff in enumerate([0] + RETRY_BACKOFFS_SECONDS):
            if backoff:
                time.sleep(backoff)
            try:
                data = fetch_overpass(overpass_parking_query(bbox))
                break
            except Exception as e:  # noqa: BLE001
                print(f"  {f.stem}: overpass attempt {i + 1} failed ({e})",
                      file=sys.stderr)
        if data is None:
            print(f"  {f.stem}: SKIPPED (overpass unavailable)", file=sys.stderr)
            continue

        lots = parking_for_geom(geom, data)
        total_lots += len(lots)
        if geom.get("parking") != lots:
            changed += 1
            print(f"  {f.stem}: {len(lots)} parking")
            if not dry_run:
                if lots:
                    geom["parking"] = lots
                else:
                    geom.pop("parking", None)
                f.write_text(json.dumps(geom))
    verb = "would write" if dry_run else "wrote"
    print(f"{verb} parking for {changed} area(s); {total_lots} lots total "
          f"across {len(files)} area(s)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--state", help="two-letter state code, e.g. az")
    g.add_argument("--all", action="store_true", help="every state")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.all:
        files = sorted(GEOM_DIR.glob("*.json"))
    else:
        files = geom_files_for_state(args.state)
    print(f"Processing {len(files)} area(s)...")
    process(files, args.dry_run)


if __name__ == "__main__":
    main()
