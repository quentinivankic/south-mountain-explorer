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

Usage:
    python3 scripts/seed-areas.py AZ                    # write index for AZ
    python3 scripts/seed-areas.py AZ CA                 # AZ + CA
    python3 scripts/seed-areas.py --dry-run AZ          # just print
    python3 scripts/seed-areas.py --merge AZ            # add to existing
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
    "AZ": "Arizona",
    "CA": "California",
    "NV": "Nevada",
    "UT": "Utah",
    "NM": "New Mexico",
    "CO": "Colorado",
    "OR": "Oregon",
    "WA": "Washington",
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


def fetch_state(state_code: str) -> list[tuple[list, int]]:
    """Returns (index_row, osm_relation_id) pairs. The osm_id is what
    we'll pin into counts-cache so both build-trail-counts.py and the
    iOS app query Overpass with the same polygon — Nominatim's
    `featuretype=relation` lookup is unstable for ambiguous names
    and was causing 48 vs 56 trail-count mismatches."""
    print(f"Querying Overpass for {state_code}...", file=sys.stderr, flush=True)
    data = fetch_overpass(overpass_query(state_code))
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
    args = ap.parse_args()

    excludes = load_overrides(SEED_EXCLUDE)
    includes = load_overrides(SEED_INCLUDE)

    seen_ids: set[str] = set()
    candidates: list[list] = []
    osm_id_by_area: dict[str, int] = {}
    for state in args.states:
        for row, osm_id in fetch_state(state):
            if row[1].lower() in excludes:
                continue
            if row[0] in seen_ids:
                continue
            seen_ids.add(row[0])
            candidates.append(row)
            osm_id_by_area[row[0]] = osm_id

    if includes:
        print(
            f"  applied {len(includes)} entries from seeds-include.txt "
            "(matched by name)",
            file=sys.stderr,
        )

    candidates.sort(key=lambda x: (x[2], x[1]))

    print(f"\nTotal candidates after filter + dedupe: {len(candidates)}", file=sys.stderr)

    if args.dry_run:
        for c in candidates:
            print(f"  {c[1]:60s}  {c[2]:12s}  {c[3]:>8.3f}, {c[4]:>9.3f}")
        return

    if args.merge and INDEX_PATH.exists():
        existing = json.loads(INDEX_PATH.read_text())
        existing_ids = {row[0] for row in existing}
        merged = list(existing)
        for c in candidates:
            if c[0] not in existing_ids:
                merged.append(c)
        candidates = merged
        candidates.sort(key=lambda x: (x[2], x[1]))

    INDEX_PATH.write_text(json.dumps(candidates, separators=(",", ":")))

    # Stash the OSM relation IDs in counts-cache so build-trail-counts.py
    # can skip Nominatim entirely (and so iOS gets the same id baked
    # into the bundled index alongside trail counts).
    cache: dict = json.loads(CACHE_PATH.read_text()) if CACHE_PATH.exists() else {}
    for area_id, osm_id in osm_id_by_area.items():
        entry = cache.get(area_id) or {}
        entry["osm_id"] = osm_id
        cache[area_id] = entry
    CACHE_PATH.write_text(json.dumps(cache, separators=(",", ":")))

    print(f"Wrote {len(candidates)} areas to {INDEX_PATH}", file=sys.stderr)
    print(f"Cached {len(osm_id_by_area)} OSM relation IDs in {CACHE_PATH}", file=sys.stderr)
    print(
        "Next: run `python3 scripts/build-trail-counts.py "
        "--min-trails 3 --min-miles 2` to fetch trail counts and apply "
        "the post-build threshold.",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
