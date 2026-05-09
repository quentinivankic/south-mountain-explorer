#!/usr/bin/env python3
"""
Rebuild public/areas/index.json with trail counts from Overpass.

Each area becomes a 7-element tuple:
  [id, name, state, lat, lon, trail_count, total_mi]

Incremental: results are saved to public/areas/counts-cache.json after each
batch so the script can be interrupted and resumed. Only uncached areas are
queried. Areas with zero qualifying trails are kept in the index with count=0
so the app can filter them client-side.

Usage:
  python3 scripts/build-trail-counts.py [--batch-size N] [--delay S] [--limit N]

Options:
  --batch-size N   Areas to process per batch before saving (default 50)
  --delay S        Seconds to sleep between Overpass requests (default 1.5)
  --limit N        Stop after N areas (for testing)
  --force          Re-query even cached areas
"""

import json
import math
import re
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
import argparse
from pathlib import Path

ROOT = Path(__file__).parent.parent
INDEX_PATH = ROOT / "public" / "areas" / "index.json"
CACHE_PATH = ROOT / "public" / "areas" / "counts-cache.json"

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

HIGHWAY_FILTER = '["highway"~"^(path|footway|track|bridleway)$"]'
MIN_TRAIL_MI = 0.59


def dist_mi(coords):
    total = 0.0
    for i in range(1, len(coords)):
        la1, lo1 = coords[i - 1]
        la2, lo2 = coords[i]
        R = 6_371_000.0
        d_la = (la2 - la1) * math.pi / 180
        d_lo = (lo2 - lo1) * math.pi / 180
        a = (math.sin(d_la / 2) ** 2
             + math.cos(la1 * math.pi / 180) * math.cos(la2 * math.pi / 180)
             * math.sin(d_lo / 2) ** 2)
        total += R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return total / 1609.344


def node_key(lat, lon, cell=0.0001):
    return f"{round(lat / cell)}:{round(lon / cell)}"


def neighbor_keys(lat, lon, cell=0.0001):
    r = round(lat / cell)
    c = round(lon / cell)
    return {f"{r+dr}:{c+dc}" for dr in (-1, 0, 1) for dc in (-1, 0, 1)}


def build_counts(data: bytes):
    """Parse Overpass JSON and return (trail_count, total_mi)."""
    try:
        obj = json.loads(data)
    except json.JSONDecodeError:
        return 0, 0.0

    elements = obj.get("elements", [])
    ways = [
        e for e in elements
        if e.get("type") == "way" and len(e.get("geometry", [])) > 1
    ]

    # Collect nodes belonging to named ways (for stitching unnamed connectors)
    named_nodes = set()
    for w in ways:
        tags = w.get("tags", {})
        name = tags.get("name", "").strip()
        if not name:
            continue
        for p in w.get("geometry", []):
            lat, lon = p.get("lat"), p.get("lon")
            if lat is not None and lon is not None:
                named_nodes.add(node_key(lat, lon))

    by_name: dict[str, float] = {}  # name → accumulated miles
    for w in ways:
        tags = w.get("tags", {})
        raw_name = tags.get("name", "").strip()
        geom = w.get("geometry", [])
        if not raw_name:
            # Only include unnamed ways that touch a named way
            endpoints = [geom[0], geom[-1]] if geom else []
            touches = any(
                neighbor_keys(p["lat"], p["lon"]) & named_nodes
                for p in endpoints
                if p.get("lat") is not None and p.get("lon") is not None
            )
            if not touches:
                continue
        name = raw_name if raw_name else f"Unnamed {w.get('id', 0)}"
        coords = [
            [p["lat"], p["lon"]]
            for p in geom
            if p.get("lat") is not None and p.get("lon") is not None
        ]
        by_name[name] = by_name.get(name, 0.0) + dist_mi(coords)

    qualifying = {k: v for k, v in by_name.items() if v >= MIN_TRAIL_MI}
    trail_count = len(qualifying)
    total_mi = round(sum(qualifying.values()), 2)
    return trail_count, total_mi


def nominatim_lookup(name: str, state: str) -> dict | None:
    """Look up an area via Nominatim. Returns dict with 'osm_id' and 'boundingbox', or None."""
    q = f"{name}, {state}, USA" if state != "Denmark" else f"{name}, Denmark"
    params = urllib.parse.urlencode({"q": q, "format": "json", "limit": 1, "featuretype": "relation"})
    req = urllib.request.Request(
        f"https://nominatim.openstreetmap.org/search?{params}",
        headers={"User-Agent": "SouthMountainExplorer/1.0 (trail-index-builder)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            results = json.loads(resp.read())
        if results and results[0].get("osm_type") == "relation":
            return results[0]
    except Exception:
        pass
    return None


def overpass_query(lat: float, lon: float, nominatim: dict | None = None) -> str:
    if nominatim:
        osm_id = int(nominatim["osm_id"])
        area_id = osm_id + 3_600_000_000
        return (
            f'[out:json][timeout:90];'
            f'area({area_id})->.a;'
            f'(way{HIGHWAY_FILTER}(area.a););'
            f'out tags geom;'
        )
    bb = nominatim.get("boundingbox") if nominatim else None
    if bb and len(bb) == 4:
        s, n, w, e = float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3])
        buf = 0.005
        s, w, n, e = s - buf, w - buf, n + buf, e + buf
    else:
        d = 0.10
        s, w, n, e = lat - d, lon - d, lat + d, lon + d
    return (
        f'[out:json][timeout:90];'
        f'(way{HIGHWAY_FILTER}({s},{w},{n},{e}););'
        f'out tags geom;'
    )


def fetch_overpass(lat, lon, nominatim=None):
    query = overpass_query(lat, lon, nominatim)
    body = ("data=" + urllib.parse.quote(query)).encode()
    last_err = None
    for endpoint in OVERPASS_ENDPOINTS:
        req = urllib.request.Request(
            endpoint,
            data=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "SouthMountainExplorer/1.0 (trail-index-builder; contact@southmountainexplorer.app)",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=110) as resp:
                return resp.read()
        except Exception as e:
            last_err = e
            time.sleep(2)
    raise RuntimeError(f"All endpoints failed: {last_err}")


def haversine_mi(lat1, lon1, lat2, lon2):
    R = 3958.8
    d_la = math.radians(lat2 - lat1)
    d_lo = math.radians(lon2 - lon1)
    a = (math.sin(d_la / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(d_lo / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def deduplicate(index: list) -> list:
    """Drop duplicate areas: same location (<0.1 mi) where one name's words are
    a subset of the other's. Keeps the entry with more trails; ties go to the
    longer name."""
    kept: list = []
    for area in index:
        a_name = area[1].lower()
        a_trails = area[5] if len(area) > 5 else -1
        merged = False
        for i, b in enumerate(kept):
            if haversine_mi(area[3], area[4], b[3], b[4]) >= 0.1:
                continue
            a_words = set(a_name.split())
            b_words = set(b[1].lower().split())
            short, long_ = (a_words, b_words) if len(a_words) <= len(b_words) else (b_words, a_words)
            if not short.issubset(long_):
                continue
            b_trails = b[5] if len(b) > 5 else -1
            if a_trails > b_trails or (a_trails == b_trails and len(area[1]) > len(b[1])):
                print(f"Dedup: kept '{area[1]}' over '{b[1]}'")
                kept[i] = area
            else:
                print(f"Dedup: kept '{b[1]}' over '{area[1]}'")
            merged = True
            break
        if not merged:
            kept.append(area)
    return kept


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--delay", type=float, default=1.5)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--cache-only", action="store_true",
                        help="Skip all network requests; just rebuild index.json from existing cache")
    args = parser.parse_args()

    index = json.loads(INDEX_PATH.read_text())
    cache: dict = json.loads(CACHE_PATH.read_text()) if CACHE_PATH.exists() else {}

    if args.cache_only:
        # Just rebuild index.json from the existing cache — no network calls.
        new_index = []
        for area in index:
            entry = cache.get(area[0])
            if entry is not None:
                new_index.append([area[0], area[1], area[2], area[3], area[4],
                                   entry["trail_count"], entry["total_mi"]])
            else:
                new_index.append(area[:5])
        new_index = deduplicate(new_index)
        INDEX_PATH.write_text(json.dumps(new_index, separators=(",", ":")))
        cached_count = sum(1 for a in new_index if len(a) >= 7)
        print(f"Cache-only rebuild: {cached_count}/{len(new_index)} areas have counts.")
        return

    targets = index if args.limit is None else index[: args.limit]
    total = len(targets)
    skipped = 0
    processed = 0
    errors = 0

    print(f"Index has {len(index)} areas. Processing {total}.")

    for i, area in enumerate(targets):
        area_id = area[0]
        lat = area[3]
        lon = area[4]

        if not args.force and area_id in cache:
            skipped += 1
            continue

        try:
            name, state = area[1], area[2]
            nominatim = nominatim_lookup(name, state)
            time.sleep(1)  # Nominatim rate limit: 1 req/sec
            source = "relation" if nominatim else "radius"
            data = fetch_overpass(lat, lon, nominatim)
            trail_count, total_mi = build_counts(data)
            cache[area_id] = {"trail_count": trail_count, "total_mi": total_mi}
            processed += 1
            print(
                f"[{i+1}/{total}] {area_id}: {trail_count} trails, {total_mi:.2f} mi ({source})",
                flush=True,
            )
        except Exception as e:
            errors += 1
            print(f"[{i+1}/{total}] ERROR {area_id}: {e}", file=sys.stderr, flush=True)

        # Save cache incrementally
        if processed % args.batch_size == 0:
            CACHE_PATH.write_text(json.dumps(cache, separators=(",", ":")))

        time.sleep(args.delay)

    # Final cache save
    CACHE_PATH.write_text(json.dumps(cache, separators=(",", ":")))

    # Rebuild index with counts
    new_index = []
    for area in index:
        area_id = area[0]
        entry = cache.get(area_id)
        if entry is not None:
            new_index.append([
                area[0], area[1], area[2], area[3], area[4],
                entry["trail_count"],
                entry["total_mi"],
            ])
        else:
            # Not yet queried — keep as 5-element tuple
            new_index.append(area[:5])

    new_index = deduplicate(new_index)
    INDEX_PATH.write_text(json.dumps(new_index, separators=(",", ":")))

    cached_count = sum(1 for a in new_index if len(a) >= 7)
    print(
        f"\nDone. processed={processed}, skipped={skipped}, errors={errors}. "
        f"{cached_count}/{len(new_index)} areas have counts."
    )


if __name__ == "__main__":
    main()
