#!/usr/bin/env python3
"""
Rebuild public/areas/index.json with trail counts from Overpass, and
public/areas/silhouettes.json with downsampled trail polylines for the iOS
Explore-tab card art.

Each area in index.json becomes a 7-element tuple:
  [id, name, state, lat, lon, trail_count, total_mi]

silhouettes.json is a single dict keyed by area id:
  { area_id: { "b": [w, s, e, n], "l": [{"d": "e|m|h", "p": [[lat,lon],...]}] } }
where bbox is tight to the trails (not the park) and points are downsampled to
~20 m spacing, rounded to 5 decimals.

Incremental: results are saved to public/areas/counts-cache.json after each
batch so the script can be interrupted and resumed. Areas with cached counts
*and* a cached silhouette are skipped; areas missing silhouette data are
re-fetched even if their counts are cached. Areas with zero qualifying trails
are kept in the index with count=0 so the app can filter them client-side.

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
SILHOUETTES_PATH = ROOT / "public" / "areas" / "silhouettes.json"

SILHOUETTE_SPACING_M = 20.0
SILHOUETTE_DECIMALS = 5
# Cap the number of trails contributing to a single area's silhouette,
# keeping the longest first. Without this, national-forest-sized areas
# blow silhouettes.json up by orders of magnitude (Coconino NF alone
# contributes ~1100 polylines). The card art is 220×160pt — beyond
# ~150 polylines you're rendering noise the user can't see.
SILHOUETTE_MAX_TRAILS = 150

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


def _difficulty(tags: dict, miles: float) -> str:
    """Mirrors AreaDataService.difficulty in the iOS app."""
    sac = (tags.get("sac_scale") or "").strip()
    if sac and sac != "hiking":
        return "h"
    if miles > 4:
        return "h"
    if miles > 2 or tags.get("trail_visibility") == "intermediate":
        return "m"
    return "e"


def _haversine_m(lat1, lon1, lat2, lon2):
    R = 6_371_000.0
    d_la = math.radians(lat2 - lat1)
    d_lo = math.radians(lon2 - lon1)
    a = (math.sin(d_la / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(d_lo / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _downsample(coords: list, spacing_m: float) -> list:
    """Keep the first point, then any point that is at least spacing_m from
    the last kept point. Always emit at least the endpoints if 2+ points."""
    if len(coords) < 2:
        return list(coords)
    kept = [coords[0]]
    for p in coords[1:-1]:
        if _haversine_m(kept[-1][0], kept[-1][1], p[0], p[1]) >= spacing_m:
            kept.append(p)
    kept.append(coords[-1])
    return kept


def build_counts(data: bytes):
    """Parse Overpass JSON. Returns (trail_count, total_mi, silhouette).

    silhouette is None if no qualifying trails were found, otherwise:
      {"b": [w, s, e, n], "l": [{"d": "e|m|h", "p": [[lat,lon],...]}, ...]}
    """
    try:
        obj = json.loads(data)
    except json.JSONDecodeError:
        return 0, 0.0, None

    elements = obj.get("elements", [])
    ways = [
        e for e in elements
        if e.get("type") == "way" and len(e.get("geometry", [])) > 1
    ]

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

    # name -> {"miles": float, "tags": dict, "segments": [[[lat,lon],...]]}
    by_name: dict = {}
    for w in ways:
        tags = w.get("tags", {})
        raw_name = tags.get("name", "").strip()
        geom = w.get("geometry", [])
        if not raw_name:
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
        if len(coords) < 2:
            continue
        if name not in by_name:
            by_name[name] = {"miles": 0.0, "tags": tags, "segments": []}
        by_name[name]["miles"] += dist_mi(coords)
        by_name[name]["segments"].append(coords)

    qualifying = {k: v for k, v in by_name.items() if v["miles"] >= MIN_TRAIL_MI}
    trail_count = len(qualifying)
    total_mi = round(sum(t["miles"] for t in qualifying.values()), 2)

    if not qualifying:
        return trail_count, total_mi, None

    # Cap the silhouette to the longest N trails so card-art bundles
    # don't balloon for huge areas (e.g. national forests). The full
    # trail_count and total_mi above still reflect every qualifying
    # trail — the cap only affects the visual silhouette.
    silhouette_trails = sorted(
        qualifying.values(), key=lambda t: -t["miles"]
    )[:SILHOUETTE_MAX_TRAILS]

    lines = []
    min_lat = min_lon = float("inf")
    max_lat = max_lon = float("-inf")
    for trail in silhouette_trails:
        d = _difficulty(trail["tags"], trail["miles"])
        for seg in trail["segments"]:
            ds = _downsample(seg, SILHOUETTE_SPACING_M)
            if len(ds) < 2:
                continue
            pts = [[round(p[0], SILHOUETTE_DECIMALS), round(p[1], SILHOUETTE_DECIMALS)] for p in ds]
            for la, lo in pts:
                if la < min_lat: min_lat = la
                if la > max_lat: max_lat = la
                if lo < min_lon: min_lon = lo
                if lo > max_lon: max_lon = lo
            lines.append({"d": d, "p": pts})

    silhouette = {
        "b": [
            round(min_lon, SILHOUETTE_DECIMALS),
            round(min_lat, SILHOUETTE_DECIMALS),
            round(max_lon, SILHOUETTE_DECIMALS),
            round(max_lat, SILHOUETTE_DECIMALS),
        ],
        "l": lines,
    }
    return trail_count, total_mi, silhouette


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


def apply_threshold(index: list, min_trails: int, min_miles: float) -> list:
    """Drop entries whose hydrated trail_count / total_mi is below the
    given thresholds. Areas that haven't been fetched yet (5-element
    tuples) are always kept so a partial run doesn't lose them."""
    if min_trails <= 0 and min_miles <= 0:
        return index
    out = []
    dropped = 0
    for area in index:
        if len(area) < 7:
            out.append(area)
            continue
        trail_count = area[5]
        total_mi = area[6]
        if trail_count < min_trails or total_mi < min_miles:
            dropped += 1
            continue
        out.append(area)
    if dropped:
        print(
            f"Threshold: dropped {dropped} areas with < {min_trails} trails "
            f"or < {min_miles} mi"
        )
    return out


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
    parser.add_argument(
        "--min-trails", type=int, default=0,
        help="After fetching, drop areas with fewer than N qualifying trails. "
        "Useful when the index was auto-seeded and contains pocket parks with "
        "no real hiking. 0 = keep everything (default).",
    )
    parser.add_argument(
        "--min-miles", type=float, default=0.0,
        help="After fetching, drop areas with less than N total trail miles. "
        "Pairs with --min-trails. 0 = keep everything (default).",
    )
    args = parser.parse_args()

    index = json.loads(INDEX_PATH.read_text())
    cache: dict = json.loads(CACHE_PATH.read_text()) if CACHE_PATH.exists() else {}

    if args.cache_only:
        new_index = []
        for area in index:
            entry = cache.get(area[0])
            if entry is not None:
                new_index.append([area[0], area[1], area[2], area[3], area[4],
                                   entry["trail_count"], entry["total_mi"]])
            else:
                new_index.append(area[:5])
        new_index = apply_threshold(new_index, args.min_trails, args.min_miles)
        new_index = deduplicate(new_index)
        INDEX_PATH.write_text(json.dumps(new_index, separators=(",", ":")))
        write_silhouettes(cache, new_index)
        cached_count = sum(1 for a in new_index if len(a) >= 7)
        sil_count = sum(1 for a in new_index if cache.get(a[0], {}).get("silhouette"))
        print(f"Cache-only rebuild: {cached_count}/{len(new_index)} areas have counts, {sil_count} have silhouettes.")
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

        cached_entry = cache.get(area_id)
        if not args.force and cached_entry is not None and "silhouette" in cached_entry:
            skipped += 1
            continue

        try:
            name, state = area[1], area[2]
            nominatim = nominatim_lookup(name, state)
            time.sleep(1)  # Nominatim rate limit: 1 req/sec
            source = "relation" if nominatim else "radius"
            data = fetch_overpass(lat, lon, nominatim)
            trail_count, total_mi, silhouette = build_counts(data)
            cache[area_id] = {
                "trail_count": trail_count,
                "total_mi": total_mi,
                "silhouette": silhouette,
            }
            processed += 1
            sil_lines = len(silhouette["l"]) if silhouette else 0
            print(
                f"[{i+1}/{total}] {area_id}: {trail_count} trails, {total_mi:.2f} mi, {sil_lines} silhouette lines ({source})",
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

    new_index = apply_threshold(new_index, args.min_trails, args.min_miles)
    new_index = deduplicate(new_index)
    INDEX_PATH.write_text(json.dumps(new_index, separators=(",", ":")))
    write_silhouettes(cache, new_index)

    cached_count = sum(1 for a in new_index if len(a) >= 7)
    sil_count = sum(1 for a in new_index if cache.get(a[0], {}).get("silhouette"))
    print(
        f"\nDone. processed={processed}, skipped={skipped}, errors={errors}. "
        f"{cached_count}/{len(new_index)} areas have counts, {sil_count} have silhouettes."
    )


def write_silhouettes(cache: dict, index: list) -> None:
    """Write silhouettes.json from the cache, scoped to the given index."""
    out: dict = {}
    for area in index:
        area_id = area[0]
        sil = cache.get(area_id, {}).get("silhouette")
        if sil:
            out[area_id] = sil
    SILHOUETTES_PATH.write_text(json.dumps(out, separators=(",", ":")))


if __name__ == "__main__":
    main()
