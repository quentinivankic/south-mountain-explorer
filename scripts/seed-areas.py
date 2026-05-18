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
    return f"""
[out:json][timeout:300];
{bbox}
(
  relation["boundary"~"^(protected_area|national_park)$"]["name"](area.region);
  relation["leisure"="nature_reserve"]["name"](area.region);
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


def fetch_state(state_code: str) -> list[tuple[list, int]]:
    """Returns (index_row, osm_relation_id) pairs. The osm_id pins
    the same polygon Python and iOS both query — Nominatim's
    `featuretype=relation` was unstable for ambiguous names."""
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
    out: list[tuple[list, int]] = []
    raw = 0
    for el in data.get("elements", []):
        if el.get("type") != "relation":
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
        name = tags["name"].strip()
        state_name = display_state(state_code)
        row = [
            slugify(name, state_code),
            name,
            state_name,
            round(float(lat), 4),
            round(float(lon), 4),
        ]
        out.append((row, int(osm_id)))
    print(
        f"  {state_code}: {raw} candidates, {len(out)} passed quality filter",
        file=sys.stderr,
        flush=True,
    )
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
    ap.add_argument("states", nargs="+", help="State codes, e.g. AZ CA")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--merge", action="store_true")
    ap.add_argument(
        "--resume",
        action="store_true",
        help="Skip states already in index.json (implies --merge).",
    )
    args = ap.parse_args()

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
