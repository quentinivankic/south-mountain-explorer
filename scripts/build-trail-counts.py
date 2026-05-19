#!/usr/bin/env python3
"""Rebuild public/areas/index.json with trail counts from Overpass,
plus per-area silhouettes and full geom JSON for the iOS app.

Each area in index.json becomes a 7-element tuple:
  [id, name, state, lat, lon, trail_count, total_mi]
(8 elements if osm_relation_id is known.)

Per-area silhouette files (shape `{b: [w,s,e,n], l: [{d, p}, …]}`)
land in public/areas/silhouettes/. Per-area geom files (full
AreaRow shape) land in public/areas/geom/. Both are synced to R2.

Incremental: results save to public/areas/counts-cache.json after
each batch; areas with cached counts + silhouette are skipped
unless --force.

Constants / helpers (difficulty, slug, downsample, dedup, …) live in
`_seed_constants.py` so the new PBF pipeline can't drift from this.
"""

import argparse
import asyncio
import json
import math
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# Allow `from _seed_constants import ...` when invoked as
# `python3 scripts/build-trail-counts.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _seed_constants import (  # noqa: E402
    CACHE_PATH,
    GEOM_DIR,
    INDEX_PATH,
    MIN_TRAIL_MI,
    SILHOUETTES_DIR,
    _is_road_like,
    apply_threshold,
    deduplicate,
    dist_mi,
    finalize_area,
    haversine_mi,
    neighbor_keys,
    node_key,
    write_geom_file,
    write_silhouette,
)

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
HIGHWAY_FILTER = '["highway"~"^(path|footway|track|bridleway)$"]'


def build_counts(data: bytes):
    """Parse Overpass JSON and return
    (trail_count, total_mi, silhouette, geom_trails, geom_bbox).
    Reuses `_seed_constants.finalize_area` for the per-area finalize
    step shared with the PBF pipeline."""
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

    return finalize_area(by_name)


def nominatim_lookup(name: str, state: str) -> dict | None:
    q = f"{name}, {state}, USA" if state != "Denmark" else f"{name}, Denmark"
    params = urllib.parse.urlencode({
        "q": q, "format": "json", "limit": 1, "featuretype": "relation",
    })
    req = urllib.request.Request(
        f"https://nominatim.openstreetmap.org/search?{params}",
        headers={
            "User-Agent": "SouthMountainExplorer/1.0 (trail-index-builder)",
        },
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
            f'[out:json][timeout:25];'
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
        f'[out:json][timeout:25];'
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
                "User-Agent": (
                    "SouthMountainExplorer/1.0 (trail-index-builder; "
                    "contact@southmountainexplorer.app)"
                ),
            },
            method="POST",
        )
        try:
            # 30-sec socket timeout — bails fast on 504/hung connections so
            # one slow area doesn't tie up a worker for 2 min. Failed
            # areas stay un-hydrated, the next dispatch retries them with
            # a warm cache (so the cost amortizes).
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read()
        except Exception as e:
            last_err = e
            time.sleep(2)
    raise RuntimeError(f"All endpoints failed: {last_err}")


def fetch_one_area(area, cache, args) -> dict:
    """Per-area fetch + count. Pure with respect to cache state."""
    area_id = area[0]
    name, state = area[1], area[2]
    lat, lon = area[3], area[4]
    cached_entry = cache.get(area_id)
    if (not args.force and cached_entry is not None
            and "silhouette" in cached_entry):
        return {"area_id": area_id, "status": "skipped"}
    try:
        cached_osm_id = (cached_entry or {}).get("osm_id")
        if cached_osm_id is not None:
            nominatim = {"osm_id": str(cached_osm_id), "boundingbox": []}
            source = "cached_osm_id"
        else:
            nominatim = nominatim_lookup(name, state)
            time.sleep(1)
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
    semaphore = asyncio.Semaphore(args.concurrency)

    async def worker(area):
        async with semaphore:
            result = await asyncio.to_thread(fetch_one_area, area, cache, args)
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
        write_geom_file(
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
            cached_at=datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
        )
        processed += 1
        sil_lines = (
            len(result["silhouette"]["l"]) if result["silhouette"] else 0
        )
        print(
            f"[{completed}/{total}] {area_id}: {result['trail_count']} trails, "
            f"{result['total_mi']:.2f} mi, {sil_lines} silhouette lines "
            f"({result['source']})",
            flush=True,
        )
        if processed % args.batch_size == 0:
            CACHE_PATH.write_text(json.dumps(cache, separators=(",", ":")))

    return processed, skipped, errors


def write_silhouettes(cache: dict, index: list) -> None:
    """Write per-area silhouettes for every entry in `index` with
    cached data."""
    SILHOUETTES_DIR.mkdir(parents=True, exist_ok=True)
    for area in index:
        area_id = area[0]
        sil = cache.get(area_id, {}).get("silhouette")
        if not sil:
            continue
        write_silhouette(area_id, sil)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--delay", type=float, default=1.5)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--cache-only", action="store_true")
    parser.add_argument("--min-trails", type=int, default=0)
    parser.add_argument("--min-miles", type=float, default=0.0)
    parser.add_argument("--state-filter", action="append", default=None)
    args = parser.parse_args()

    index = json.loads(INDEX_PATH.read_text())
    cache: dict = (
        json.loads(CACHE_PATH.read_text()) if CACHE_PATH.exists() else {}
    )

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
        sil_count = sum(
            1 for a in new_index if cache.get(a[0], {}).get("silhouette")
        )
        print(
            f"Cache-only rebuild: {cached_count}/{len(new_index)} areas "
            f"have counts, {sil_count} have silhouettes."
        )
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

    print(
        f"Index has {len(index)} areas. Processing {total} "
        f"(concurrency={args.concurrency})."
    )

    processed, skipped, errors = asyncio.run(
        run_parallel(targets, cache, args, total)
    )

    CACHE_PATH.write_text(json.dumps(cache, separators=(",", ":")))

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
            new_index.append(area[:5])

    new_index = apply_threshold(new_index, args.min_trails, args.min_miles)
    new_index = deduplicate(new_index)
    INDEX_PATH.write_text(json.dumps(new_index, separators=(",", ":")))
    write_silhouettes(cache, new_index)

    cached_count = sum(1 for a in new_index if len(a) >= 7)
    sil_count = sum(
        1 for a in new_index if cache.get(a[0], {}).get("silhouette")
    )
    print(
        f"\nDone. processed={processed}, skipped={skipped}, errors={errors}. "
        f"{cached_count}/{len(new_index)} areas have counts, "
        f"{sil_count} have silhouettes."
    )


if __name__ == "__main__":
    main()
