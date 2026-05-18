#!/usr/bin/env python3
"""Diff the output of two trail-index pipeline runs.

Used in PR J.1/J.2 to verify the new PBF pipeline produces
acceptably-equivalent artifacts to the legacy Overpass pipeline.

Inputs: two directories that look like ``public/areas/`` —
each must contain ``index.json`` and (optionally) ``silhouettes/``
and ``geom/`` subdirs.

Usage:
    python3 tools/diff-pipelines.py <old-snapshot> <new-snapshot> \\
        [--region DK] [--max-print 50]

Where ``<old-snapshot>`` is the legacy Overpass-built output and
``<new-snapshot>`` is the PBF-built output. Filtering by region
limits the diff to slugs ending with that ISO code (e.g.
``--region DK`` looks at all ``…-dk`` slugs).

Exit code:
    0  — no diffs OR all diffs within tolerance
    1  — diffs exceeded tolerance somewhere
    2  — usage / IO error

Tolerances baked into the script (per the PR J plan):
    index.json:
      - slug sets must be IDENTICAL
      - trail_count: ±2 or ±5%, whichever is larger
      - total_mi:    ±5%
      - centroid:    ≤ 5 km drift (≤ 500 m typical)
    silhouettes/<id>.json:
      - difficulty histogram (counts of "e"/"m"/"h"): identical
      - line count: ±10% (downsampling order is identical, so this
        mostly catches missing or extra trails feeding the silhouette)
    geom/<id>.json:
      - trail count: exact
      - trail-id set: exact
      - distanceMi per trail: ±0.05 mi
      - difficulty per trail: exact
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path


TRAIL_COUNT_ABS_TOL = 2
TRAIL_COUNT_PCT_TOL = 0.05
TOTAL_MI_PCT_TOL = 0.05
CENTROID_KM_TOL = 5.0
SILHOUETTE_LINE_PCT_TOL = 0.10
GEOM_DISTANCE_TOL = 0.05  # miles


# ---------- IO helpers ----------


def _read_index(path: Path) -> dict[str, list]:
    """Read an index.json file into a dict keyed by slug."""
    rows = json.loads(path.read_text())
    return {row[0]: row for row in rows}


def _read_optional_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None


# ---------- Distance ----------


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    R = 6371.0
    d_la = math.radians(lat2 - lat1)
    d_lo = math.radians(lon2 - lon1)
    a = (math.sin(d_la / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(d_lo / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ---------- Diff sections ----------


class _Counter:
    def __init__(self):
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def err(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)


def diff_index(old: dict, new: dict, counter: _Counter) -> set[str]:
    """Compare two indexes keyed by slug. Returns the slug set common
    to both — the caller uses it to drive per-slug silhouette/geom
    comparisons."""
    old_slugs = set(old)
    new_slugs = set(new)
    common = old_slugs & new_slugs

    only_old = old_slugs - new_slugs
    only_new = new_slugs - old_slugs

    if only_old:
        counter.err(
            f"index: {len(only_old)} slugs in OLD but not NEW: "
            f"{sorted(only_old)[:10]}"
        )
    if only_new:
        counter.err(
            f"index: {len(only_new)} slugs in NEW but not OLD: "
            f"{sorted(only_new)[:10]}"
        )

    for slug in sorted(common):
        a = old[slug]
        b = new[slug]
        # Both rows: [slug, name, state, lat, lon, count, miles, osm_id?]

        # Centroid drift.
        if len(a) >= 5 and len(b) >= 5:
            km = _haversine_km(a[3], a[4], b[3], b[4])
            if km > CENTROID_KM_TOL:
                counter.err(
                    f"  {slug}: centroid drift {km:.1f} km "
                    f"(old=({a[3]},{a[4]}), new=({b[3]},{b[4]}))"
                )
            elif km > 0.5:
                counter.warn(
                    f"  {slug}: centroid drift {km:.2f} km (within tol)"
                )

        # Trail count.
        if len(a) >= 6 and len(b) >= 6:
            diff = abs(a[5] - b[5])
            tol = max(TRAIL_COUNT_ABS_TOL,
                      int(TRAIL_COUNT_PCT_TOL * max(a[5], b[5])))
            if diff > tol:
                counter.err(
                    f"  {slug}: trail_count old={a[5]} new={b[5]} "
                    f"(Δ={diff}, tol={tol})"
                )

        # Total miles.
        if len(a) >= 7 and len(b) >= 7:
            avg = (a[6] + b[6]) / 2 if (a[6] + b[6]) > 0 else 1
            pct = abs(a[6] - b[6]) / avg
            if pct > TOTAL_MI_PCT_TOL:
                counter.err(
                    f"  {slug}: total_mi old={a[6]:.2f} new={b[6]:.2f} "
                    f"(Δ={pct:.1%}, tol={TOTAL_MI_PCT_TOL:.0%})"
                )

    return common


def diff_silhouettes(
    old_dir: Path, new_dir: Path, slugs: set[str], counter: _Counter
) -> None:
    for slug in sorted(slugs):
        a = _read_optional_json(old_dir / f"{slug}.json")
        b = _read_optional_json(new_dir / f"{slug}.json")
        if a is None and b is None:
            continue
        if a is None or b is None:
            counter.err(
                f"silhouette {slug}: old={'present' if a else 'missing'}, "
                f"new={'present' if b else 'missing'}"
            )
            continue

        # Difficulty histogram (counts of each "d" value).
        def _hist(s):
            out = {"e": 0, "m": 0, "h": 0}
            for line in s.get("l", []):
                d = line.get("d")
                if d in out:
                    out[d] += 1
            return out

        ha, hb = _hist(a), _hist(b)
        if ha != hb:
            counter.err(
                f"silhouette {slug}: difficulty histogram differs "
                f"old={ha} new={hb}"
            )

        # Line count tolerance.
        na, nb = len(a.get("l", [])), len(b.get("l", []))
        if na > 0 or nb > 0:
            avg = (na + nb) / 2 if (na + nb) > 0 else 1
            pct = abs(na - nb) / avg
            if pct > SILHOUETTE_LINE_PCT_TOL:
                counter.err(
                    f"silhouette {slug}: line count old={na} new={nb} "
                    f"(Δ={pct:.1%}, tol={SILHOUETTE_LINE_PCT_TOL:.0%})"
                )


def diff_geoms(
    old_dir: Path, new_dir: Path, slugs: set[str], counter: _Counter
) -> None:
    for slug in sorted(slugs):
        a = _read_optional_json(old_dir / f"{slug}.json")
        b = _read_optional_json(new_dir / f"{slug}.json")
        if a is None and b is None:
            continue
        if a is None or b is None:
            counter.err(
                f"geom {slug}: old={'present' if a else 'missing'}, "
                f"new={'present' if b else 'missing'}"
            )
            continue

        ta = a.get("trails") or []
        tb = b.get("trails") or []

        if len(ta) != len(tb):
            counter.err(
                f"geom {slug}: trail count old={len(ta)} new={len(tb)}"
            )

        ids_a = {t["id"] for t in ta}
        ids_b = {t["id"] for t in tb}
        only_a = ids_a - ids_b
        only_b = ids_b - ids_a
        if only_a:
            counter.err(
                f"geom {slug}: {len(only_a)} trail IDs in OLD only: "
                f"{sorted(only_a)[:5]}"
            )
        if only_b:
            counter.err(
                f"geom {slug}: {len(only_b)} trail IDs in NEW only: "
                f"{sorted(only_b)[:5]}"
            )

        by_id_a = {t["id"]: t for t in ta}
        by_id_b = {t["id"]: t for t in tb}
        for tid in sorted(ids_a & ids_b):
            ra, rb = by_id_a[tid], by_id_b[tid]
            d_old = ra.get("distanceMi", 0)
            d_new = rb.get("distanceMi", 0)
            if abs(d_old - d_new) > GEOM_DISTANCE_TOL:
                counter.err(
                    f"geom {slug}/{tid}: distanceMi old={d_old} "
                    f"new={d_new} (tol=±{GEOM_DISTANCE_TOL})"
                )
            if ra.get("difficulty") != rb.get("difficulty"):
                counter.err(
                    f"geom {slug}/{tid}: difficulty "
                    f"old={ra.get('difficulty')} "
                    f"new={rb.get('difficulty')}"
                )


# ---------- Main ----------


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "old_dir",
        type=Path,
        help="Snapshot directory of the legacy (Overpass) pipeline.",
    )
    ap.add_argument(
        "new_dir",
        type=Path,
        help="Snapshot directory of the new (PBF) pipeline.",
    )
    ap.add_argument(
        "--region",
        default=None,
        help="Filter slugs by ISO code suffix (e.g. 'DK' or 'AZ').",
    )
    ap.add_argument(
        "--max-print",
        type=int,
        default=80,
        help="Maximum number of error/warning lines to print before "
        "truncating.",
    )
    args = ap.parse_args()

    old_dir: Path = args.old_dir
    new_dir: Path = args.new_dir

    for d in (old_dir, new_dir):
        if not d.is_dir():
            print(f"Not a directory: {d}", file=sys.stderr)
            return 2

    old_index_path = old_dir / "index.json"
    new_index_path = new_dir / "index.json"
    if not old_index_path.exists() or not new_index_path.exists():
        print("Missing index.json in one of the snapshots", file=sys.stderr)
        return 2

    old = _read_index(old_index_path)
    new = _read_index(new_index_path)

    if args.region:
        suffix = f"-{args.region.lower()}"
        old = {k: v for k, v in old.items() if k.endswith(suffix)}
        new = {k: v for k, v in new.items() if k.endswith(suffix)}
        print(
            f"--region {args.region}: old={len(old)} new={len(new)} slugs",
            file=sys.stderr,
        )

    counter = _Counter()
    common = diff_index(old, new, counter)

    diff_silhouettes(
        old_dir / "silhouettes", new_dir / "silhouettes", common, counter,
    )
    diff_geoms(old_dir / "geom", new_dir / "geom", common, counter)

    # Report.
    if counter.warnings:
        print(f"\n{len(counter.warnings)} warnings (within tolerance):")
        for w in counter.warnings[: args.max_print]:
            print(f"  WARN: {w}")
        if len(counter.warnings) > args.max_print:
            print(f"  … {len(counter.warnings) - args.max_print} more truncated")

    if counter.errors:
        print(f"\n{len(counter.errors)} errors (exceeded tolerance):")
        for e in counter.errors[: args.max_print]:
            print(f"  ERR:  {e}")
        if len(counter.errors) > args.max_print:
            print(f"  … {len(counter.errors) - args.max_print} more truncated")
        print(
            f"\nFAIL: {len(counter.errors)} tolerance violations across "
            f"{len(common)} shared slugs.",
            file=sys.stderr,
        )
        return 1

    print(
        f"\nPASS: {len(common)} slugs match within tolerance "
        f"({len(counter.warnings)} sub-threshold warnings).",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
