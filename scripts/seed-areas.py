#!/usr/bin/env python3
"""Seed candidate hiking areas from OpenStreetMap for given US states.

Pulls protected_area / nature_reserve / national_park relations bounded
by each state, applies a tag-quality filter, dedupes, and writes a
candidate index.json that build-trail-counts.py can then hydrate with
trail counts and silhouettes.

Tag filter heuristics:
  * Drop access=private
  * Drop unnamed
  * Keep when protect_class is in the curated set
  * Otherwise require a recognizable keyword in the name
    (Park, Preserve, Wilderness, Forest, Monument, Recreation Area,
    Refuge, Sanctuary, Reserve, Open Space, Conservation, Wildlife)

Manual overrides:
  * scripts/seeds-include.txt — names to always include
  * scripts/seeds-exclude.txt — names or relation IDs to always drop

Resilience: each state writes incrementally to index.json + counts-cache.json
on success, so a mid-loop Overpass 504 doesn't lose prior states' work.
`fetch_state` retries 3× with 60s / 180s / 600s backoff before raising
to the caller. The companion build-trail-index workflow wraps the
script in an outer 3× retry, and `--resume` makes the script skip
states already present in the index — together that's 9 attempts per
state, idempotent across workflow runs.

Usage:
    python3 scripts/seed-areas.py AZ                    # write index for AZ
    python3 scripts/seed-areas.py AZ CA                 # AZ + CA
    python3 scripts/seed-areas.py --dry-run AZ          # just print
    python3 scripts/seed-areas.py --merge AZ            # add to existing
    python3 scripts/seed-areas.py --resume AZ CA ...    # skip states
                                                         # already in
                                                         # index (implies
                                                         # --merge)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX_PATH = ROOT / "public" / "areas" / "index.json"
CACHE_PATH = ROOT / "public" / "areas" / "counts-cache.json"
SCRIPTS_DIR = Path(__file__).resolve().parent
SEED_INCLUDE = SCRIPTS_DIR / "seeds-include.txt"
SEED_EXCLUDE = SCRIPTS_DIR / "seeds-exclude.txt"

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

# protect_class numeric values that map to "real outdoor destinations"
# per OSM wiki. Loosely: anything that's a recognized reserve / park /
# wilderness. Keeps the door open for a wide net while filtering out
# heritage sites, botanical gardens, etc.
ALLOWED_PROTECT_CLASSES = {
    "1",   # Strict nature reserve
    "1a", "1b",
    "2",   # National park
    "3",   # Natural monument
    "4",   # Habitat / species management
    "5",   # Protected landscape
    "6",   # Resource / managed
    "11",  # Wilderness area (US-specific)
    "12",  # Wilderness
    "21",  # Locally protected
    "22",  # Animal sanctuary
    "97", "98", "99",  # Other / unspecified
}

# When protect_class is missing, fall back to a name-keyword whitelist.
# Catches state parks, national forests, regional/county parks, etc. that
# don't tag protect_class (common in OSM US data).
NAME_KEYWORD_RE = re.compile(
    r"\b(park|preserve|wilderness|forest|monument|recreation area|"
    r"recreation site|refuge|sanctuary|reserve|open space|"
    r"conservation|wildlife|trailhead|trail system|nra|sra)\b",
    re.IGNORECASE,
)

STATE_NAMES = {
    "AL": "Alabama",
    "AK": "Alaska",
    "AZ": "Arizona",
    "AR": "Arkansas",
    "CA": "California",
    "CO": "Colorado",
    "CT": "Connecticut",
    "DE": "Delaware",
    "DC": "District of Columbia",
    "FL": "Florida",
    "GA": "Georgia",
    "HI": "Hawaii",
    "ID": "Idaho",
    "IL": "Illinois",
    "IN": "Indiana",
    "IA": "Iowa",
    "KS": "Kansas",
    "KY": "Kentucky",
    "LA": "Louisiana",
    "ME": "Maine",
    "MD": "Maryland",
    "MA": "Massachusetts",
    "MI": "Michigan",
    "MN": "Minnesota",
    "MS": "Mississippi",
    "MO": "Missouri",
    "MT": "Montana",
    "NE": "Nebraska",
    "NV": "Nevada",
    "NH": "New Hampshire",
    "NJ": "New Jersey",
    "NM": "New Mexico",
    "NY": "New York",
    "NC": "North Carolina",
    "ND": "North Dakota",
    "OH": "Ohio",
    "OK": "Oklahoma",
    "OR": "Oregon",
    "PA": "Pennsylvania",
    "RI": "Rhode Island",
    "SC": "South Carolina",
    "SD": "South Dakota",
    "TN": "Tennessee",
    "TX": "Texas",
    "UT": "Utah",
    "VT": "Vermont",
    "VA": "Virginia",
    "WA": "Washington",
    "WV": "West Virginia",
    "WI": "Wisconsin",
    "WY": "Wyoming",
}


def overpass_query(state_code: str) -> str:
    return f"""
[out:json][timeout:300];
area["ISO3166-2"="US-{state_code}"]->.state;
(
  relation["boundary"~"^(protected_area|national_park)$"]["name"](area.state);
  relation["leisure"="nature_reserve"]["name"](area.state);
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


def is_quality(tags: dict) -> bool:
    if tags.get("access") == "private":
        return False
    name = (tags.get("name") or "").strip()
    if not name:
        return False
    pc = (tags.get("protect_class") or "").strip().lower()
    if pc and pc in ALLOWED_PROTECT_CLASSES:
        return True
    # No protect_class — name keyword whitelist as fallback
    return bool(NAME_KEYWORD_RE.search(name))


def slugify(name: str, state_code: str) -> str:
    s = re.sub(r"[^\w\s-]", "", name.lower())
    s = re.sub(r"[-\s]+", "-", s).strip("-")
    return f"{s[:60]}-{state_code.lower()}"


# Outer per-state retry. `fetch_overpass` already cycles two Overpass
# mirrors with a short 3s sleep, but when BOTH endpoints return 504
# (rate-limit / overload) we want a longer cool-off before trying
# again rather than failing the whole loop. 60s / 180s / 600s
# matches Overpass's typical recovery window for transient overload.
STATE_RETRY_BACKOFFS_SECONDS = [60, 180, 600]


def fetch_state(state_code: str) -> list[tuple[list, int]]:
    """Returns (index_row, osm_relation_id) pairs. The osm_id is what
    we'll pin into counts-cache so both build-trail-counts.py and the
    iOS app query Overpass with the same polygon — Nominatim's
    `featuretype=relation` lookup is unstable for ambiguous names
    and was causing 48 vs 56 trail-count mismatches."""
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
    assert data is not None, f"unreachable: fetch_overpass succeeded but data is None ({last_err})"
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
        state_name = STATE_NAMES.get(state_code, state_code)
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


def load_overrides(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {
        line.strip().lower()
        for line in path.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    }


def atomic_write(path: Path, payload: str) -> None:
    """Write `payload` to `path` atomically — write to a sibling .tmp
    file, then rename. Guards against half-written JSON if the process
    is killed mid-write (relevant during long-running multi-state seeds
    that flush per state)."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload)
    tmp.replace(path)


def flush_state(
    new_rows: list[list],
    new_osm_ids: dict[str, int],
    current_state_name: str,
    args: argparse.Namespace,
) -> int:
    """Merge `new_rows` into INDEX_PATH and `new_osm_ids` into CACHE_PATH,
    writing both atomically. Called after each state's fetch so a
    mid-loop Overpass failure preserves prior states' work. Returns
    the total number of rows now in the index.

    Merge semantics:
    - `--merge` or `--resume`: append rows whose slug isn't already in
      the index. Hand-curated entries and prior seeds untouched.
    - replace (neither flag): drop existing entries whose state is
      `current_state_name`, then append. Only this one state's entries
      get cleared — entries from earlier states in the same loop (or
      any other state not in args.states) are preserved.
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

    cache: dict = json.loads(CACHE_PATH.read_text()) if CACHE_PATH.exists() else {}
    for area_id, osm_id in new_osm_ids.items():
        entry = cache.get(area_id) or {}
        entry["osm_id"] = osm_id
        cache[area_id] = entry
    atomic_write(CACHE_PATH, json.dumps(cache, separators=(",", ":")))

    return len(merged)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("states", nargs="+", help="State codes, e.g. AZ CA")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print candidates to stdout without writing index.json")
    ap.add_argument(
        "--merge",
        action="store_true",
        help="Merge with existing index.json instead of replacing (keeps "
        "any hand-curated entries)",
    )
    ap.add_argument(
        "--resume",
        action="store_true",
        help="Skip states that already appear in index.json (implies "
        "--merge). Used by the build-trail-index workflow to retry the "
        "seed step after a transient Overpass failure without "
        "re-fetching everything that already succeeded.",
    )
    args = ap.parse_args()

    excludes = load_overrides(SEED_EXCLUDE)
    includes = load_overrides(SEED_INCLUDE)

    # Resume mode: skip any state whose full name is already represented
    # in the existing index. Idempotent across workflow restarts.
    already_seeded: set[str] = set()
    if args.resume and INDEX_PATH.exists():
        existing = json.loads(INDEX_PATH.read_text())
        already_seeded = {row[2] for row in existing}
        skipped = [
            s for s in args.states
            if STATE_NAMES.get(s, s) in already_seeded
        ]
        if skipped:
            print(
                f"--resume: skipping {len(skipped)} states already in "
                f"index ({' '.join(skipped)})",
                file=sys.stderr,
                flush=True,
            )

    if includes:
        print(
            f"  applied {len(includes)} entries from seeds-include.txt "
            "(matched by name)",
            file=sys.stderr,
        )

    if args.dry_run:
        # Dry-run still does a full fetch (good for spot-checking the
        # filter) but never writes. Doesn't flush per-state.
        seen_ids: set[str] = set()
        candidates: list[list] = []
        for state in args.states:
            if STATE_NAMES.get(state, state) in already_seeded:
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

    # Per-state flush: write to disk after each successful fetch so a
    # mid-loop Overpass 504 preserves prior states' work for the next
    # workflow retry (which uses --resume to skip them).
    total_rows_written = 0
    total_osm_ids_written = 0
    for state in args.states:
        if STATE_NAMES.get(state, state) in already_seeded:
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
    print(
        "Next: run `python3 scripts/build-trail-counts.py "
        "--min-trails 3 --min-miles 2` to fetch trail counts and apply "
        "the post-build threshold.",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
