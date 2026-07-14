#!/usr/bin/env python3
"""Bake real elevation gain + difficulty into published area geom (SPEC.md §6e).

Post-processes public/areas/geom/*.json: for each trail, densify to ~30 m,
sample a global DEM (AWS Terrarium), sum smoothed positive gain, and set
`gainFt` + a gain-aware `difficulty`. Also writes a per-area `total_gain_ft`.

Homelab / CI only — the DEM sampling needs network + Pillow (`pip install
Pillow`). Idempotent; disk-cached tiles make re-runs cheap. Start with one
state to eyeball the numbers against AllTrails before going nationwide.

    python3 trailforge/serve/add-elevation.py --state az        # one state
    python3 trailforge/serve/add-elevation.py --state az --dry-run --top 15
    python3 trailforge/serve/add-elevation.py                   # every published area

Then commit the changed geom + run sync-geom-to-r2. (Durable pipeline
integration — sampling inside publish — is the follow-up; this post-process
ships the improvement now without a full re-assembly.)
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
import elevation  # noqa: E402

_GEOM = os.path.join(_HERE, "..", "..", "public", "areas", "geom")


def process_area(geom: dict, sampler) -> tuple[int, dict]:
    """Mutate `geom` in place: set gainFt + difficulty per trail and a per-area
    total_gain_ft. Returns (trails_updated, difficulty_delta) where the delta
    counts label changes old->new for the run summary. Testable with any
    object exposing `.elevation(lat, lon)`."""
    changed = 0
    delta: dict[str, int] = {}
    total_gain = 0.0
    for t in geom.get("trails", []):
        miles = t.get("distanceMi", 0.0)
        try:
            g = elevation.trail_gain_ft(t.get("segments", []), sampler)
        except Exception as e:                      # a bad/missing tile: skip trail
            print(f"    ! gain failed for {t.get('id')}: {e}", file=sys.stderr)
            continue
        old = t.get("difficulty")
        new = elevation.difficulty_label(miles, g)
        t["gainFt"] = int(g)
        t["difficulty"] = new
        total_gain += g
        changed += 1
        if old != new:
            delta[f"{old}->{new}"] = delta.get(f"{old}->{new}", 0) + 1
    geom["total_gain_ft"] = int(total_gain)
    return changed, delta


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--geom-dir", default=_GEOM)
    ap.add_argument("--state", help="2-letter code suffix filter, e.g. az (slug ends -az)")
    ap.add_argument("--zoom", type=int, default=elevation.DEM_ZOOM)
    ap.add_argument("--cache-dir", default=os.path.join(_HERE, "..", "data", "dem-cache"))
    ap.add_argument("--dry-run", action="store_true", help="compute + report, write nothing")
    ap.add_argument("--top", type=int, default=10, help="print the N highest-gain trails")
    ap.add_argument("--name", help="calibration: print gain for every trail whose "
                    "name contains this (case-insensitive), e.g. --name humphreys")
    args = ap.parse_args(argv)

    files = sorted(glob.glob(os.path.join(args.geom_dir, "*.json")))
    if args.state:
        suf = f"-{args.state.lower()}.json"
        files = [f for f in files if f.endswith(suf)]
    if not files:
        print("no matching geom files", file=sys.stderr)
        return 1

    sampler = elevation.TileSampler(zoom=args.zoom, cache_dir=args.cache_dir)

    total_changed = 0
    delta_all: dict[str, int] = {}
    top: list[tuple[int, str, str]] = []   # (gain, trail, area)
    for i, f in enumerate(files, 1):
        try:
            geom = json.load(open(f))
        except Exception:
            continue
        if "cached_at" in geom or not geom.get("trails"):
            continue
        changed, delta = process_area(geom, sampler)
        total_changed += changed
        for k, v in delta.items():
            delta_all[k] = delta_all.get(k, 0) + v
        for t in geom.get("trails", []):
            top.append((t.get("gainFt", 0), t.get("name") or "", geom.get("name") or "",
                        t.get("distanceMi", 0), t.get("difficulty", "")))
        if not args.dry_run:
            json.dump(geom, open(f, "w"), separators=(",", ":"))
        if i % 25 == 0 or i == len(files):
            print(f"  [{i}/{len(files)}] {os.path.basename(f)} "
                  f"({changed} trails)", file=sys.stderr)

    print(f"\n{'DRY-RUN — ' if args.dry_run else ''}updated {total_changed} trails "
          f"across {len(files)} areas")
    if delta_all:
        print("difficulty label changes:")
        for k in sorted(delta_all, key=lambda k: -delta_all[k]):
            print(f"  {delta_all[k]:5}  {k}")
    top.sort(reverse=True)
    if args.name:
        q = args.name.lower()
        hits = [r for r in top if q in r[1].lower()]
        print(f"\ncalibration — trails matching {args.name!r} ({len(hits)}):")
        for g, tn, an, mi, diff in hits:
            print(f"  {g:6} ft  {mi:5} mi  {diff:8}  {tn}  ({an})")
    else:
        print(f"\ntop {args.top} by gain (sanity-check vs AllTrails):")
        for g, tn, an, mi, diff in top[:args.top]:
            print(f"  {g:6} ft  {mi:5} mi  {diff:8}  {tn}  ({an})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
