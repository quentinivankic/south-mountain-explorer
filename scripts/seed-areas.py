#!/usr/bin/env python3
"""Seed candidate hiking areas from OpenStreetMap for given regions.

Pulls protected_area / nature_reserve / national_park relations
bounded by each region, applies a tag-quality filter, and writes a
candidate index.json that build-trail-counts.py then hydrates with
trail counts and silhouettes.

Constants (state lists, name regex, protect-class whitelist, slug
math, …) live in `_seed_constants.py` so the new PBF pipeline
(`build-index-from-pbf.py`) can't drift from this one.

Manual overrides: `scripts/seeds-include.txt` (always include),
`scripts/seeds-exclude.txt` (always drop).

Resilience: each region writes incrementally on success, so a
mid-loop Overpass 504 doesn't lose prior states' work. Per-region
retry is 3× with 60s / 180s / 600s backoff. The companion workflow
wraps the script in an outer 3× retry, and `--resume` makes the
script skip regions already present in the index — 9 attempts per
region, idempotent across workflow runs.

Usage:
    python3 scripts/seed-areas.py AZ
    python3 scripts/seed-areas.py AZ CA
    python3 scripts/seed-areas.py --dry-run AZ
    python3 scripts/seed-areas.py --merge AZ
    python3 scripts/seed-areas.py --resume AZ CA ...
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

# Allow `from _seed_constants import ...` when invoked as
# `python3 scripts/seed-areas.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _seed_constants import (  # noqa: E402
    CACHE_PATH,
    COUNTRY_CODES,
    INDEX_PATH,
    SEED_EXCLUDE,
    SEED_INCLUDE,
    STATE_NAMES,
    atomic_write,
    code_from_slug,
    display_state,
    is_quality,
    load_overrides,
    slugify,
)

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]


def overpass_query(code: str) -> str:
    """Build an Overpass query for `code`. Three shapes:
      - 2-letter US state ("AZ"): ISO3166-2 = "US-AZ"
      - 2-letter country in COUNTRY_CODES ("DK"): ISO3166-1 = "DK"
      - Explicit ISO3166-2 with hyphen ("CA-AB"): ISO3166-2 = "CA-AB"
    """
    if "-" in code:
        bbox = f'area["ISO3166-2"="{code}"]->.region;'
    elif code in COUNTRY_CODES:
        bbox = f'area["ISO3166-1"="{code}"]->.region;'
    else:
        bbox = f'area["ISO3166-2"="US-{code}"]->.region;'
    # Both relation (multi-part boundary) AND way (a single closed polygon) —
    # a simple protected area is just as often mapped as one way as it is a
    # relation (e.g. NY's Otter Creek State Forest: boundary=protected_area,
    # leisure=nature_reserve, protect_class=6, all on a bare `way`). Missing
    # the way clause silently drops every area mapped that way, regardless of
    # how well it'd pass is_quality() — the candidate never even reaches it.
    return f"""
[out:json][timeout:300];
{bbox}
(
  relation["boundary"~"^(protected_area|national_park)$"]["name"](area.region);
  relation["leisure"="nature_reserve"]["name"](area.region);
  way["boundary"~"^(protected_area|national_park)$"]["name"](area.region);
  way["leisure"="nature_reserve"]["name"](area.region);
);
out tags center;
""".strip()


def fetch_overpass(query: str) -> dict:
    body = ("data=" + urllib.parse.quote(query)).encode()
    last_err: Exception | None = None
    for endpoint in OVERPASS_ENDPOINTS:
        req = urllib.request.Request(
            endpoint,
            data=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "SouthMountainExplorer/1.0 (seed-areas)",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                return json.loads(resp.read())
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(3)
    raise RuntimeError(f"All Overpass endpoints failed: {last_err}")


# Outer per-state retry. `fetch_overpass` already cycles two Overpass
# mirrors with a short 3s sleep, but when BOTH endpoints return 504
# we want a longer cool-off. 60s / 180s / 600s matches Overpass's
# typical recovery window for transient overload.
STATE_RETRY_BACKOFFS_SECONDS = [60, 180, 600]


def region_bbox_query(code: str) -> str:
    """Build an Overpass query for the requested region's
    administrative boundary bbox. Used as a centroid post-filter to
    drop candidate areas whose `out center;` coordinate falls outside
    the requested region — fixes mis-attribution of cross-border
    relations (e.g. the trinational Wadden Sea relation was being
    returned for DK and slugged `-dk` even though its center is in
    Germany; Falsterbo Naturreservat is in Sweden but used to land
    under DK via bbox overlap)."""
    if "-" in code:
        # ISO3166-2 subdivision: admin_level=4 in OSM.
        return (
            f'[out:json][timeout:60];'
            f'relation["ISO3166-2"="{code}"]'
            f'["boundary"="administrative"]["admin_level"="4"];'
            f'out bb;'
        )
    if code in COUNTRY_CODES:
        # Country: admin_level=2.
        return (
            f'[out:json][timeout:60];'
            f'relation["ISO3166-1"="{code}"]'
            f'["boundary"="administrative"]["admin_level"="2"];'
            f'out bb;'
        )
    # US state, legacy default.
    return (
        f'[out:json][timeout:60];'
        f'relation["ISO3166-2"="US-{code}"]'
        f'["boundary"="administrative"]["admin_level"="4"];'
        f'out bb;'
    )


def fetch_region_bbox(state_code: str) -> tuple[float, float, float, float] | None:
    """Fetch the region's admin boundary bbox. Returns
    (min_lat, min_lon, max_lat, max_lon) or None when the query
    fails or returns nothing — in which case fetch_state falls back
    to no centroid filter (parity with the prior behavior)."""
    try:
        data = fetch_overpass(region_bbox_query(state_code))
    except RuntimeError as e:
        print(
            f"  {state_code}: bbox fetch failed ({e}); skipping centroid filter",
            file=sys.stderr,
            flush=True,
        )
        return None
    # When multiple relations match (rare — usually only one
    # admin_level=2/4 relation per ISO code), take the widest bbox:
    # it's almost always the canonical national / state boundary,
    # and "widest" beats "first" for a forgiving filter.
    best: tuple[float, float, float, float] | None = None
    best_area = -1.0
    for el in data.get("elements", []):
        if el.get("type") != "relation":
            continue
        b = el.get("bounds") or {}
        if not all(k in b for k in ("minlat", "minlon", "maxlat", "maxlon")):
            continue
        area = (b["maxlat"] - b["minlat"]) * (b["maxlon"] - b["minlon"])
        if area > best_area:
            best = (b["minlat"], b["minlon"], b["maxlat"], b["maxlon"])
            best_area = area
    if best is None:
        print(
            f"  {state_code}: no admin boundary relation returned; "
            "skipping centroid filter",
            file=sys.stderr,
            flush=True,
        )
    return best


def fetch_state(state_code: str) -> list[tuple[list, int | None]]:
    """Returns (index_row, osm_relation_id) pairs. The osm_id pins
    the same polygon Python and iOS both query — Nominatim's
    `featuretype=relation` was unstable for ambiguous names.

    osm_relation_id is None for a way-sourced candidate: the app's live-
    Overpass fallback (AreaDataService.fetchFromOverpass) computes the
    Overpass `area()` id as `osmId + 3_600_000_000`, which is the RELATION-
    only offset (a way's is +2_400_000_000). Storing a way id there would
    silently point the fallback at the wrong polygon; leaving it unset makes
    it fall through to the next tier (Nominatim lookup, then a bbox query) —
    a soft degrade instead of a silent wrong answer. Only the (rare) live-
    fallback path is affected — the primary path is trailforge's own
    PBF-based boundary assembly, which already handles both ways and
    relations natively (see assemble/areas.py)."""
    print(f"Querying Overpass for {state_code}...", file=sys.stderr, flush=True)
    data: dict | None = None
    last_err: Exception | None = None
    for attempt, backoff in enumerate(STATE_RETRY_BACKOFFS_SECONDS, start=1):
        try:
            data = fetch_overpass(overpass_query(state_code))
            break
        except RuntimeError as e:
            last_err = e
            if attempt == len(STATE_RETRY_BACKOFFS_SECONDS):
                print(
                    f"  {state_code}: all {attempt} retries exhausted ({e})",
                    file=sys.stderr,
                    flush=True,
                )
                raise
            print(
                f"  {state_code}: attempt {attempt} failed ({e}); "
                f"sleeping {backoff}s before retry",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(backoff)
    assert data is not None, (
        f"unreachable: fetch_overpass succeeded but data is None ({last_err})"
    )

    # Fetch the region's admin boundary bbox up-front. Used below to
    # drop candidates whose `out center;` falls outside the region —
    # see `fetch_region_bbox` for the why.
    bbox = fetch_region_bbox(state_code)


    out: list[tuple[list, int | None]] = []
    raw = 0
    out_of_bbox = 0
    for el in data.get("elements", []):
        el_type = el.get("type")
        if el_type not in ("relation", "way"):
            continue
        raw += 1
        tags = el.get("tags") or {}
        if not is_quality(tags):
            continue
        center = el.get("center") or {}
        lat, lon = center.get("lat"), center.get("lon")
        if lat is None or lon is None:
            continue
        osm_id = el.get("id")
        if osm_id is None:
            continue

        # Centroid post-filter. Overpass's `(area.region)` returns
        # relations that OVERLAP the region's bbox, not relations
        # whose center is inside it — so cross-border relations
        # like the Wadden Sea UNESCO area used to land under DK
        # even though their centroid is in DE. Bbox containment
        # isn't a perfect substitute for polygon containment (a
        # candidate near a coast bulge might pass bbox while
        # actually being offshore), but it catches the obvious
        # cross-border bug at trivial cost. Falls back to no
        # filter when the region's bbox couldn't be fetched.
        if bbox is not None:
            min_lat, min_lon, max_lat, max_lon = bbox
            if not (min_lat <= lat <= max_lat
                    and min_lon <= lon <= max_lon):
                out_of_bbox += 1
                continue

        name = tags["name"].strip()
        state_name = display_state(state_code)
        row = [
            slugify(name, state_code),
            name,
            state_name,
            round(float(lat), 4),
            round(float(lon), 4),
        ]
        out.append((row, int(osm_id) if el_type == "relation" else None))
    msg = (
        f"  {state_code}: {raw} candidates, {len(out)} passed quality filter"
    )
    if out_of_bbox:
        msg += f" ({out_of_bbox} dropped — center outside region bbox)"
    print(msg, file=sys.stderr, flush=True)
    return out


def flush_state(
    new_rows: list[list],
    new_osm_ids: dict[str, int],
    current_state_name: str,
    args: argparse.Namespace,
) -> int:
    """Merge `new_rows` into INDEX_PATH and `new_osm_ids` into
    CACHE_PATH, writing both atomically. Returns total rows after merge.
    """
    existing: list[list] = (
        json.loads(INDEX_PATH.read_text()) if INDEX_PATH.exists() else []
    )

    if args.merge or args.resume:
        existing_ids = {row[0] for row in existing}
        for row in new_rows:
            if row[0] not in existing_ids:
                existing.append(row)
                existing_ids.add(row[0])
        merged = existing
    else:
        preserved = [row for row in existing if row[2] != current_state_name]
        preserved_ids = {row[0] for row in preserved}
        for row in new_rows:
            if row[0] not in preserved_ids:
                preserved.append(row)
                preserved_ids.add(row[0])
        merged = preserved

    merged.sort(key=lambda x: (x[2], x[1]))
    atomic_write(INDEX_PATH, json.dumps(merged, separators=(",", ":")))

    cache: dict = (
        json.loads(CACHE_PATH.read_text()) if CACHE_PATH.exists() else {}
    )
    for area_id, osm_id in new_osm_ids.items():
        entry = cache.get(area_id) or {}
        entry["osm_id"] = osm_id
        cache[area_id] = entry
    atomic_write(CACHE_PATH, json.dumps(cache, separators=(",", ":")))

    return len(merged)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "states",
        nargs="*",
        help="State codes, e.g. AZ CA. Required unless --all is set.",
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--merge", action="store_true")
    ap.add_argument(
        "--resume",
        action="store_true",
        help="Skip states already in index.json (implies --merge).",
    )
    ap.add_argument(
        "--all",
        action="store_true",
        help="Seed every region in STATE_NAMES. Use to refresh the "
        "osm_id cache for all regions in one go — combined with "
        "--merge (no --resume) this re-runs the per-region Overpass "
        "query for everything and updates counts-cache.json with "
        "fresh osm_ids, which lets build-trail-counts skip Nominatim "
        "lookups for un-cached areas. Cannot be combined with "
        "positional state codes.",
    )
    args = ap.parse_args()

    if args.all:
        if args.states:
            ap.error("--all is mutually exclusive with positional state codes")
        args.states = sorted(STATE_NAMES.keys())
    elif not args.states:
        ap.error("at least one state code is required (or pass --all)")

    excludes = load_overrides(SEED_EXCLUDE)
    includes = load_overrides(SEED_INCLUDE)

    already_seeded: set[str] = set()
    if args.resume and INDEX_PATH.exists():
        existing = json.loads(INDEX_PATH.read_text())
        for row in existing:
            if len(row) >= 1:
                c = code_from_slug(row[0])
                if c is not None:
                    already_seeded.add(c)
        skipped = [s for s in args.states if s in already_seeded]
        if skipped:
            print(
                f"--resume: skipping {len(skipped)} states already in "
                f"index ({' '.join(skipped)})",
                file=sys.stderr,
                flush=True,
            )

    if includes:
        print(
            f"  applied {len(includes)} entries from seeds-include.txt",
            file=sys.stderr,
        )

    if args.dry_run:
        seen_ids: set[str] = set()
        candidates: list[list] = []
        for state in args.states:
            if state in already_seeded:
                continue
            for row, _ in fetch_state(state):
                if row[1].lower() in excludes:
                    continue
                if row[0] in seen_ids:
                    continue
                seen_ids.add(row[0])
                candidates.append(row)
        candidates.sort(key=lambda x: (x[2], x[1]))
        for c in candidates:
            print(f"  {c[1]:60s}  {c[2]:12s}  {c[3]:>8.3f}, {c[4]:>9.3f}")
        return

    total_rows_written = 0
    total_osm_ids_written = 0
    for state in args.states:
        if state in already_seeded:
            continue
        new_rows: list[list] = []
        new_osm_ids: dict[str, int] = {}
        for row, osm_id in fetch_state(state):
            if row[1].lower() in excludes:
                continue
            new_rows.append(row)
            if osm_id is not None:      # way-sourced candidates carry no id (see fetch_state)
                new_osm_ids[row[0]] = osm_id
        index_size = flush_state(
            new_rows,
            new_osm_ids,
            STATE_NAMES.get(state, state),
            args,
        )
        total_rows_written += len(new_rows)
        total_osm_ids_written += len(new_osm_ids)
        print(
            f"  flushed {state}: +{len(new_rows)} rows "
            f"(index now {index_size} total)",
            file=sys.stderr,
            flush=True,
        )

    print(
        f"\nWrote {total_rows_written} new areas across {len(args.states)} "
        f"states to {INDEX_PATH}",
        file=sys.stderr,
    )
    print(
        f"Cached {total_osm_ids_written} OSM relation IDs in {CACHE_PATH}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
