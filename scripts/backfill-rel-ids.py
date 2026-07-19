#!/usr/bin/env python3
"""Backfill `osm_relation_id` (row[7]) into master-index rows that lack one.

583 substantial areas (Death Valley, Humboldt-Toiyabe + big National Forests,
Great Smoky's TN side) are bare 5-field seed rows with NO rel id, so
`publish_areas.py`'s multi-state boundary fix can't reach them. `seed-areas.py
--merge` won't fix them (slug-keyed append-only) and the osm_id cache is CI-only.

This reads the target list (`no-relid-missing-areas.tsv`, cols trails/total_mi/
slug/name/state), queries OSM in BATCHES of exact-name clauses (small, reliable
queries — the per-STATE query is too heavy and 504s), and for each target writes
the rel id of the same-NAMED boundary whose center is nearest the row's stored
center (disambiguates e.g. Cherokee NF in TN vs NC). Matches on NAME, not slug,
so a multi-state area filed under one state resolves. Then republish those states.

    python3 scripts/backfill-rel-ids.py --targets <tsv> --dry-run
    python3 scripts/backfill-rel-ids.py --targets <tsv>          # writes index.json
"""
from __future__ import annotations
import argparse
import json
import math
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

INDEX = Path(__file__).resolve().parent.parent / "public" / "areas" / "index.json"
_EPS = ("https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter")
BATCH = 25


def overpass(q: str) -> dict | None:
    for ep in _EPS:
        for attempt in range(2):
            try:
                req = urllib.request.Request(
                    ep, data=urllib.parse.urlencode({"data": q}).encode(),
                    headers={"User-Agent": "trekdex-backfill/1.0"})
                return json.loads(urllib.request.urlopen(req, timeout=90).read())
            except Exception:  # noqa: BLE001
                time.sleep(5 * (attempt + 1))
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--targets", required=True, help="TSV: trails/total_mi/slug/name/state")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, help="only the first N targets (testing)")
    args = ap.parse_args()

    idx = json.loads(INDEX.read_text())
    by_slug = {r[0]: r for r in idx}

    targets = []  # (slug, name)
    for line in Path(args.targets).read_text().splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) >= 4:
            targets.append((parts[2], parts[3]))
    if args.limit:
        targets = targets[:args.limit]
    print(f"targets: {len(targets)}", file=sys.stderr)

    # unique names to query (skip any with a double-quote — would break the clause)
    names = sorted({nm for _, nm in targets if '"' not in nm})
    found: dict[str, list[tuple[int, float, float]]] = {}  # name.casefold -> [(id,lat,lon)]
    for i in range(0, len(names), BATCH):
        batch = names[i:i + BATCH]
        clauses = "".join(
            f'relation["name"="{nm}"]["boundary"~"^(protected_area|national_park)$"];'
            f'relation["name"="{nm}"]["leisure"="nature_reserve"];'
            for nm in batch)
        d = overpass(f"[out:json][timeout:90];({clauses});out tags center;")
        got = 0
        for el in (d.get("elements", []) if d else []):
            nm = (el.get("tags") or {}).get("name")
            c = el.get("center") or {}
            if nm and c:
                found.setdefault(nm.casefold(), []).append((el["id"], c["lat"], c["lon"]))
                got += 1
        print(f"  batch {i // BATCH + 1}/{-(-len(names) // BATCH)}: {got} relations "
              f"({'FETCH FAILED' if d is None else 'ok'})", file=sys.stderr)

    filled, missing = 0, []
    for slug, name in targets:
        row = by_slug.get(slug)
        if row is None:
            continue
        cands = found.get(name.casefold())
        if not cands:
            missing.append(name)
            continue
        rlat, rlon = row[3], row[4]
        osm_id, _, _ = min(cands, key=lambda c: math.hypot(c[1] - rlat, c[2] - rlon))
        while len(row) < 8:
            row.append(None)
        row[7] = osm_id
        filled += 1

    print(f"\nbackfilled {filled}/{len(targets)} "
          f"({'dry-run, not written' if args.dry_run else 'writing index.json'}); "
          f"no OSM match for {len(missing)}")
    if missing[:10]:
        print("  sample no-match:", missing[:10])
    if not args.dry_run and filled:
        INDEX.write_text(json.dumps(idx, separators=(",", ":")))


if __name__ == "__main__":
    main()
