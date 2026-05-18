#!/usr/bin/env python3
"""
Rebuild public/areas/index.json with trail counts from Overpass, and
public/areas/silhouettes/<id>.json with downsampled trail polylines for
the iOS Explore-tab card art.

Each area in index.json becomes a 7-element tuple:
  [id, name, state, lat, lon, trail_count, total_mi]

Per-area silhouette files have shape:
  { "b": [w, s, e, n], "l": [{"d": "e|m|h", "p": [[lat,lon],...]}] }
where bbox is tight to the trails (not the park) and points are
downsampled to ~20 m spacing, rounded to 5 decimals. These files
are served from Cloudflare R2 at runtime (same bucket as the geom
files, under a `silhouettes/` prefix) so the iOS app binary stays
small as state coverage grows.

Incremental: results are saved to public/areas/counts-cache.json after each
batch so the script can be interrupted and resumed. Areas with cached counts
*and* a cached silhouette are skipped; areas missing silhouette data are
re-fetched even if their counts are cached. Areas with zero qualifying trails
are kept in the index with count=0 so the app can filter them client-side.

Usage:
  python3 scripts/build-trail-counts.py [--batch-size N] [--delay S] [--limit N]
                                        [--concurrency N]

Options:
  --batch-size N   Areas to process per batch before saving (default 50)
  --delay S        Seconds to sleep between Overpass requests, per worker
                   (default 1.5). Global request rate is concurrency / delay.
  --limit N        Stop after N areas (for testing)
  --force          Re-query even cached areas
  --concurrency N  Parallel worker count (default 3). Each worker holds the
                   per-request `--delay` between its own Overpass requests, so
                   global throughput is roughly N/delay req/s — still polite
                   to the public Overpass instance at the default 3/1.5 ≈ 2 rps.
"""

import asyncio
import json
import math
import re
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
import argparse
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
INDEX_PATH = ROOT / "public" / "areas" / "index.json"
CACHE_PATH = ROOT / "public" / "areas" / "counts-cache.json"
# Per-area silhouette JSON, one file per area. Mirrors the geom layout
# below — both live next to each other on R2 in production, so the
# iOS app fetches a silhouette the same way it fetches full geometry.
# Replaces the old monolithic `silhouettes.json` (~45 MB in-bundle) so
# the binary stays small as area count grows past California-and-
# Arizona scale.
SILHOUETTES_DIR = ROOT / "public" / "areas" / "silhouettes"
# Per-area full trail geometry, one JSON file per area. Served via
# Cloudflare R2 at runtime so the iOS app can skip the live Overpass
# call (and the resulting empty/timeout failure modes) on cold open.
GEOM_DIR = ROOT / "public" / "areas" / "geom"

SILHOUETTE_SPACING_M = 20.0
SILHOUETTE_DECIMALS = 5
# Cap the number of trails contributing to a single area's silhouette,
# keeping the longest first. Now that silhouettes live off-bundle on
# R2 we can afford a much higher cap; national forests show closer to
# their real trail network instead of the most-truncated-150 view.
# Above ~400 the rendered card just becomes noise — that's the cap.
SILHOUETTE_MAX_TRAILS = 400

# Geom output spacing — much finer than silhouettes since these polylines
# are rendered into the actual trail map, not a 220pt card. 5m is sub-pixel
# at hiking zoom levels, so increasing the resolution past this is just
# more bytes for no visible win.
GEOM_SPACING_M = 5.0
GEOM_DECIMALS = 6

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


# Difficulty as the full label string the iOS Trail.Difficulty enum
# decodes from JSON ("Easy" / "Moderate" / "Hard"). Same predicate as
# `_difficulty` above; we just emit different strings depending on the
# consumer. Silhouettes use "e"/"m"/"h" to keep the JSON tiny; the geom
# files use the full label because iOS expects the rawValue of the enum.
def _difficulty_label(tags: dict, miles: float) -> str:
    code = _difficulty(tags, miles)
    return {"e": "Easy", "m": "Moderate", "h": "Hard"}[code]


def _trail_slug(name: str) -> str:
    """Mirrors AreaDataService.slugify on the iOS side: lowercase,
    non-alphanumerics → hyphens, collapse repeats, trim to 60 chars.
    Pre-computing trail IDs in the build step (instead of letting iOS
    derive them at fetch time) is how we keep recorded-hike completions
    stable across builds — the id baked into the geom file is the same
    string iOS would have computed locally."""
    parts = re.split(r"[^a-z0-9]+", name.lower())
    parts = [p for p in parts if p]
    return "-".join(parts)[:60]


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


def _is_road_like(tags: dict, name: str) -> bool:
    """Mirrors AreaDataService.isRoadLike on the iOS side. Drops
    forest-service / utility roads tagged as `highway=track` with names
    like "FR-123 Road" or motor_vehicle=yes — those flood the trail
    count for national forests but aren't real hiking trails. Without
    this filter the bundled count is hundreds higher than what the iOS
    app shows after fetching the same area (Apache-Sitgreaves was 741
    in the bundle vs 465 on device)."""
    if tags.get("highway") != "track":
        return False
    road_words = ("road", "drive", "avenue", "canal", "drain", "ditch",
                  "boulevard", "highway", "freeway")
    lower = (name or "").lower()
    if any(w in lower for w in road_words):
        return True
    if tags.get("motor_vehicle") == "yes" or tags.get("motorcar") == "yes":
        return True
    if tags.get("access") == "private":
        return True
    return False


def build_counts(data: bytes):
    """Parse Overpass JSON.

    Returns ``(trail_count, total_mi, silhouette, geom_trails, geom_bbox)``.

    silhouette is None if no qualifying trails were found, otherwise:
      {"b": [w, s, e, n], "l": [{"d": "e|m|h", "p": [[lat,lon],...]}, ...]}

    geom_trails is the list of full trail dicts the iOS app expects in
    its ``AreaRow.trails`` payload — each item carries ``id``, ``name``,
    ``distanceMi``, ``difficulty`` (the full "Easy"/"Moderate"/"Hard"
    label), and ``segments`` downsampled to ``GEOM_SPACING_M``. IDs are
    the slugified trail name; on the rare slug collision a
    deterministic per-slug counter resolves the duplicate. Critically
    no build-time ordinal is part of the id, so a refetch that adds
    or removes trails doesn't reshuffle the surviving ones' ids and
    invalidate existing user completions.

    geom_bbox is ``[minLon, minLat, maxLon, maxLat]`` covering every
    point in geom_trails (note: looser than the silhouette bbox, which
    is capped at the longest 150 trails).
    """
    try:
        obj = json.loads(data)
    except json.JSONDecodeError:
        return 0, 0.0, None, [], None

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
        if _is_road_like(tags, name):
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
        if _is_road_like(tags, raw_name):
            continue
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

    # Build the geom payload in the same name-sorted order iOS uses
    # locally. Trail ids are derived from the slug alone — never from
    # the build-time ordinal — so a refetch with shifted trail
    # composition doesn't reshuffle ids and break recorded-hike
    # completion dedup. For the rare slug collision (two distinct
    # named trails that lowercase-hyphenate identically), suffix the
    # second-and-later occurrence with a deterministic counter so the
    # collisions resolve the same way on every rebuild.
    geom_trails: list = []
    g_min_lat = g_min_lon = float("inf")
    g_max_lat = g_max_lon = float("-inf")
    slug_counts: dict = {}
    for name in sorted(qualifying.keys()):
        info = qualifying[name]
        miles = info["miles"]
        ds_segments: list = []
        for seg in info["segments"]:
            ds = _downsample(seg, GEOM_SPACING_M)
            if len(ds) < 2:
                continue
            pts = [
                [round(p[0], GEOM_DECIMALS), round(p[1], GEOM_DECIMALS)]
                for p in ds
            ]
            for la, lo in pts:
                if la < g_min_lat: g_min_lat = la
                if la > g_max_lat: g_max_lat = la
                if lo < g_min_lon: g_min_lon = lo
                if lo > g_max_lon: g_max_lon = lo
            ds_segments.append(pts)
        base_slug = _trail_slug(name)
        seen = slug_counts.get(base_slug, 0)
        slug_counts[base_slug] = seen + 1
        trail_id = base_slug if seen == 0 else f"{base_slug}-{seen}"
        geom_trails.append({
            "id": trail_id,
            "name": name,
            "distanceMi": round(miles, 2),
            "difficulty": _difficulty_label(info["tags"], miles),
            "segments": ds_segments,
        })
    if g_min_lat != float("inf"):
        geom_bbox = [
            round(g_min_lon, GEOM_DECIMALS),
            round(g_min_lat, GEOM_DECIMALS),
            round(g_max_lon, GEOM_DECIMALS),
            round(g_max_lat, GEOM_DECIMALS),
        ]
    else:
        geom_bbox = None

    if not qualifying:
        return trail_count, total_mi, None, geom_trails, geom_bbox

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
    return trail_count, total_mi, silhouette, geom_trails, geom_bbox


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


def fetch_one_area(area, cache, args) -> dict:
    """Synchronous per-area fetch + count. Pure with respect to
    cache state — returns a result dict the caller will merge into
    the cache + write-geom on the main task. Splitting the network
    work (slow, parallelizable) from the cache mutation (fast,
    serial) is what lets `run_parallel` use a thread pool without
    needing a lock around the cache dict.
    """
    area_id = area[0]
    name, state = area[1], area[2]
    lat, lon = area[3], area[4]
    cached_entry = cache.get(area_id)
    if not args.force and cached_entry is not None and "silhouette" in cached_entry:
        return {"area_id": area_id, "status": "skipped"}
    try:
        cached_osm_id = (cached_entry or {}).get("osm_id")
        if cached_osm_id is not None:
            # Skip Nominatim — we already know the relation. Avoids
            # the instability where Nominatim picks different
            # relations for the same name on different runs (Python
            # and iOS would then fetch different polygons and report
            # different counts).
            nominatim = {"osm_id": str(cached_osm_id), "boundingbox": []}
            source = "cached_osm_id"
        else:
            nominatim = nominatim_lookup(name, state)
            time.sleep(1)  # Nominatim rate limit: 1 req/sec
            source = "relation" if nominatim else "radius"
        data = fetch_overpass(lat, lon, nominatim)
        trail_count, total_mi, silhouette, geom_trails, geom_bbox = build_counts(data)
        return {
            "area_id": area_id,
            "status": "ok",
            "name": name,
            "state": state,
            "lat": lat,
            "lon": lon,
            "trail_count": trail_count,
            "total_mi": total_mi,
            "silhouette": silhouette,
            "geom_trails": geom_trails,
            "geom_bbox": geom_bbox,
            "cached_osm_id": cached_osm_id,
            "nominatim": nominatim,
            "source": source,
        }
    except Exception as e:
        return {"area_id": area_id, "status": "error", "error": str(e)}


async def run_parallel(targets, cache, args, total):
    """Drive `fetch_one_area` across `args.concurrency` worker
    tasks via asyncio + to_thread. Cache writes + result handling
    stay on the main task so we don't have to lock the cache dict
    (and so the existing per-batch checkpoint semantics carry over).

    Per-worker delay (not global): each worker sleeps `args.delay`
    after its own request, so the global request rate is roughly
    `concurrency / delay` per second. With the defaults (3 / 1.5)
    that's ~2 rps to Overpass — well within the public instance's
    tolerance and only a 3× speedup relative to the serial loop,
    but enough to take the AZ+CA rebuild from ~50min → ~17min and
    a hypothetical full-US rebuild from days → most-of-a-day.
    """
    semaphore = asyncio.Semaphore(args.concurrency)

    async def worker(area):
        async with semaphore:
            result = await asyncio.to_thread(fetch_one_area, area, cache, args)
            # Stagger inside the semaphore so the per-request delay
            # serializes against this worker's NEXT acquisition,
            # not against unrelated cache-write work on the main
            # task. Skip the sleep on a cache hit — those return
            # without making a network call.
            if result["status"] == "ok":
                await asyncio.sleep(args.delay)
            return result

    tasks = [asyncio.create_task(worker(area)) for area in targets]
    processed = 0
    skipped = 0
    errors = 0
    completed = 0
    for finished in asyncio.as_completed(tasks):
        result = await finished
        completed += 1
        area_id = result["area_id"]
        if result["status"] == "skipped":
            skipped += 1
            continue
        if result["status"] == "error":
            errors += 1
            print(
                f"[{completed}/{total}] ERROR {area_id}: {result['error']}",
                file=sys.stderr,
                flush=True,
            )
            continue
        # status == "ok" — merge into cache and write the geom file.
        cache_entry = cache.get(area_id) or {}
        cache_entry.update({
            "trail_count": result["trail_count"],
            "total_mi": result["total_mi"],
            "silhouette": result["silhouette"],
        })
        if result["cached_osm_id"] is not None:
            cache_entry["osm_id"] = result["cached_osm_id"]
        elif result["nominatim"] and "osm_id" in result["nominatim"]:
            cache_entry["osm_id"] = int(result["nominatim"]["osm_id"])
        cache[area_id] = cache_entry
        # Write the per-area geom file for jsDelivr. We deliberately
        # write even when geom_trails is empty so the file exists
        # and the iOS CDN path doesn't have to distinguish "no
        # trails" from "not built yet" (those mean different things).
        write_geom(
            area_id=area_id,
            name=result["name"],
            state=result["state"],
            center_lat=result["lat"],
            center_lon=result["lon"],
            trail_count=result["trail_count"],
            total_mi=result["total_mi"],
            osm_relation_id=cache_entry.get("osm_id"),
            geom_trails=result["geom_trails"],
            geom_bbox=result["geom_bbox"],
        )
        processed += 1
        sil_lines = len(result["silhouette"]["l"]) if result["silhouette"] else 0
        print(
            f"[{completed}/{total}] {area_id}: {result['trail_count']} trails, "
            f"{result['total_mi']:.2f} mi, {sil_lines} silhouette lines ({result['source']})",
            flush=True,
        )
        # Save cache incrementally — same cadence the serial loop used.
        if processed % args.batch_size == 0:
            CACHE_PATH.write_text(json.dumps(cache, separators=(",", ":")))

    return processed, skipped, errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--delay", type=float, default=1.5)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--concurrency", type=int, default=3,
        help="Parallel worker count. Each worker holds the per-request "
        "--delay between its own Overpass requests, so global throughput "
        "is roughly N/delay req/s. 1 = serial (the pre-PR-3 behavior).",
    )
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
    parser.add_argument(
        "--state-filter", action="append", default=None,
        help="Process only areas whose state name (row[2]) is in this list. "
        "Repeatable for multiple states: --state-filter 'Alberta' "
        "--state-filter 'British Columbia'. Areas NOT matching are silently "
        "skipped from the fetch loop — their existing cached data (counts, "
        "silhouettes, geom) is preserved in the rebuild step. Pairs with "
        "`seed_states` for fast per-country / per-state seed runs: a typical "
        "DK or CA-PE seed only needs to fetch ~10-20 new areas, not iterate "
        "all ~3000.",
    )
    args = parser.parse_args()

    index = json.loads(INDEX_PATH.read_text())
    cache: dict = json.loads(CACHE_PATH.read_text()) if CACHE_PATH.exists() else {}

    if args.cache_only:
        new_index = []
        for area in index:
            entry = cache.get(area[0])
            if entry is not None and "trail_count" in entry:
                row = [area[0], area[1], area[2], area[3], area[4],
                       entry["trail_count"], entry["total_mi"]]
                if entry.get("osm_id") is not None:
                    row.append(int(entry["osm_id"]))
                new_index.append(row)
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
    if args.state_filter:
        filters = set(args.state_filter)
        before = len(targets)
        targets = [a for a in targets if len(a) >= 3 and a[2] in filters]
        print(
            f"--state-filter {sorted(filters)} → {len(targets)}/{before} targets",
            file=sys.stderr,
        )
    total = len(targets)

    print(f"Index has {len(index)} areas. Processing {total} (concurrency={args.concurrency}).")

    processed, skipped, errors = asyncio.run(
        run_parallel(targets, cache, args, total)
    )

    # Final cache save
    CACHE_PATH.write_text(json.dumps(cache, separators=(",", ":")))

    # Rebuild index with counts (and osm_id when we have it, so iOS can
    # skip Nominatim and query the same polygon Python did).
    new_index = []
    for area in index:
        area_id = area[0]
        entry = cache.get(area_id)
        if entry is not None and "trail_count" in entry:
            row = [
                area[0], area[1], area[2], area[3], area[4],
                entry["trail_count"],
                entry["total_mi"],
            ]
            if entry.get("osm_id") is not None:
                row.append(int(entry["osm_id"]))
            new_index.append(row)
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
    """Write per-area silhouettes to `public/areas/silhouettes/<id>.json`.

    Replaces the old monolithic `silhouettes.json`. The iOS app fetches
    one file per area from R2 on demand (mirrors the per-area geom
    pattern). This function only WRITES — it never deletes — so a
    previously-built area whose row vanishes from `index` keeps its
    file on disk until the next clean rebuild. Acceptable; the index
    is what governs which areas the app considers visible.
    """
    SILHOUETTES_DIR.mkdir(parents=True, exist_ok=True)
    for area in index:
        area_id = area[0]
        sil = cache.get(area_id, {}).get("silhouette")
        if not sil:
            continue
        path = SILHOUETTES_DIR / f"{area_id}.json"
        path.write_text(json.dumps(sil, separators=(",", ":")))


def write_geom(
    area_id: str,
    name: str,
    state: str,
    center_lat: float,
    center_lon: float,
    trail_count: int,
    total_mi: float,
    osm_relation_id: int | None,
    geom_trails: list,
    geom_bbox: list | None,
) -> None:
    """Write one area's full trail geometry to ``public/areas/geom/<id>.json``.

    Shape matches what ``AreaRow`` decodes on the iOS side — same
    snake_case keys, same ``trails`` shape (``id`` / ``name`` /
    ``distanceMi`` / ``difficulty`` / ``segments``). The iOS app fetches
    these via jsDelivr, falls back to live Overpass only on 404.
    """
    GEOM_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "id": area_id,
        "name": name,
        "state": state,
        "center_lat": center_lat,
        "center_lon": center_lon,
        # AreaRow requires `zoom`; 13 is what the Overpass path used to
        # bake into stubs — preserve the value so AreaView's initial
        # camera span is unchanged.
        "zoom": 13,
        "bbox": geom_bbox,
        "trails": geom_trails,
        "trail_count": trail_count,
        "total_mi": total_mi,
        "cached_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "osm_relation_id": osm_relation_id,
    }
    out_path = GEOM_DIR / f"{area_id}.json"
    out_path.write_text(json.dumps(payload, separators=(",", ":")))


if __name__ == "__main__":
    main()
