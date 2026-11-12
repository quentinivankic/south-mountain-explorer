#!/usr/bin/env python3
"""Warn-only data quality audit: same-name gaps and overlapping trails, per area.

Reads published geom (default public/areas/geom). Never writes there, never
fails a run: findings are warnings, the exit code is 0. Exit 2 only for bad
arguments, for example a --json path inside public/areas.

What it reports, per area:

  same-name gaps   Pieces (segments) of trails with the same name that do not
                   touch but come within --max-gap-m (default 200 m) of each
                   other. Usually one trail broken by a data split; the app
                   bridges gaps up to 180 m (TrailPieceStitcher), so these are
                   breaks the app is quietly papering over. Pieces closer than
                   1 m are treated as connected. Distance is endpoint to line.
  overlaps         Pairs of trail rows that share ground: the same edge (two
                   consecutive points, either direction) in both. Shared miles
                   per pair, plus the area's distinct-ground miles from
                   trailforge/serve/distinct_ground.py. Pairs under 0.01 mi
                   are left out as noise.

Manual for now, not wired into a publish workflow: those workflows and their
producers are hash-pinned and contract-tested (scripts/r2_release_contract.py,
scripts/trailforge_candidate_contract.py), so a new step needs its own
reviewed change.

    python3 scripts/audit-gaps-overlaps.py
    python3 scripts/audit-gaps-overlaps.py --area south-mountain-park-and-preserve-az
    python3 scripts/audit-gaps-overlaps.py --json /tmp/gaps-overlaps.json --top 40
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(_ROOT / "trailforge" / "serve"))
import distinct_ground  # noqa: E402

PUBLIC_AREAS = _ROOT / "public" / "areas"
DEFAULT_GEOM = PUBLIC_AREAS / "geom"
TOUCH_M = 1.0
DEFAULT_MAX_GAP_M = 200.0
MIN_OVERLAP_MI = 0.01
_R_M = 6_371_000.0
_M_PER_MI = 1609.344


def _norm_name(name) -> str:
    return " ".join(str(name or "").split()).casefold()


def _xy(p, lat0: float) -> tuple[float, float]:
    """Local equirectangular metres; fine at the few-hundred-metre scale used here."""
    return (math.radians(p[1]) * _R_M * math.cos(math.radians(lat0)),
            math.radians(p[0]) * _R_M)


def _point_to_line_m(p, line, lat0: float) -> float:
    px, py = _xy(p, lat0)
    best = math.inf
    pts = [_xy(q, lat0) for q in line]
    if len(pts) == 1:
        return math.hypot(px - pts[0][0], py - pts[0][1])
    for (ax, ay), (bx, by) in zip(pts, pts[1:]):
        dx, dy = bx - ax, by - ay
        seg2 = dx * dx + dy * dy
        t = 0.0 if seg2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg2))
        best = min(best, math.hypot(px - (ax + t * dx), py - (ay + t * dy)))
    return best


def _bbox(line) -> tuple[float, float, float, float]:
    lats = [p[0] for p in line]
    lons = [p[1] for p in line]
    return min(lats), min(lons), max(lats), max(lons)


def _bbox_near(a, b, pad_deg_lat: float, pad_deg_lon: float) -> bool:
    return not (a[2] + pad_deg_lat < b[0] or b[2] + pad_deg_lat < a[0]
                or a[3] + pad_deg_lon < b[1] or b[3] + pad_deg_lon < a[1])


def _piece_gap_m(a, b, lat0: float) -> float:
    """Smallest endpoint-to-line distance between two pieces, either way round."""
    return min(_point_to_line_m(a[0], b, lat0), _point_to_line_m(a[-1], b, lat0),
               _point_to_line_m(b[0], a, lat0), _point_to_line_m(b[-1], a, lat0))


def _clean_line(seg) -> list | None:
    pts = [[float(p[0]), float(p[1])] for p in seg or [] if isinstance(p, (list, tuple)) and len(p) >= 2]
    return pts if len(pts) >= 2 else None


def same_name_gaps(trails: list[dict], max_gap_m: float = DEFAULT_MAX_GAP_M) -> list[dict]:
    """Gaps between unconnected pieces of one name, TOUCH_M < gap < max_gap_m."""
    groups: dict[str, list[tuple[str, list]]] = {}
    shown: dict[str, str] = {}
    for t in trails:
        key = _norm_name(t.get("name"))
        if not key:
            continue
        shown.setdefault(key, str(t.get("name")))
        for seg in t.get("segments") or []:
            line = _clean_line(seg)
            if line:
                groups.setdefault(key, []).append((str(t.get("id")), line))
    out = []
    for key, pieces in sorted(groups.items()):
        if len(pieces) < 2:
            continue
        lat0 = pieces[0][1][0][0]
        pad_lat = max_gap_m / 111_320.0
        pad_lon = pad_lat / max(0.01, math.cos(math.radians(lat0)))
        boxes = [_bbox(line) for _, line in pieces]
        parent = list(range(len(pieces)))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        near: list[tuple[float, int, int]] = []
        for i in range(len(pieces)):
            for j in range(i + 1, len(pieces)):
                if not _bbox_near(boxes[i], boxes[j], pad_lat, pad_lon):
                    continue
                gap = _piece_gap_m(pieces[i][1], pieces[j][1], lat0)
                if gap <= TOUCH_M:
                    parent[find(i)] = find(j)
                elif gap < max_gap_m:
                    near.append((gap, i, j))
        best: dict[tuple[int, int], tuple[float, int, int]] = {}
        for gap, i, j in near:
            ci, cj = find(i), find(j)
            if ci == cj:
                continue
            pair = (min(ci, cj), max(ci, cj))
            if pair not in best or gap < best[pair][0]:
                best[pair] = (gap, i, j)
        for gap, i, j in sorted(best.values()):
            out.append({
                "name": shown[key],
                "gap_m": round(gap, 1),
                "trail_ids": sorted({pieces[i][0], pieces[j][0]}),
                "near": [round(pieces[i][1][0][0], 5), round(pieces[i][1][0][1], 5)],
            })
    return out


def overlaps(trails: list[dict], min_mi: float = MIN_OVERLAP_MI) -> list[dict]:
    """Trail-row pairs sharing edges, with shared miles, largest first."""
    owner: dict[tuple, int] = {}
    shared: dict[tuple[int, int], float] = {}
    for idx, t in enumerate(trails):
        for seg in t.get("segments") or []:
            line = _clean_line(seg)
            if not line:
                continue
            for a, b in zip(line, line[1:]):
                pa, pb = (a[0], a[1]), (b[0], b[1])
                if pa == pb:
                    continue
                key = (pa, pb) if pa <= pb else (pb, pa)
                first = owner.setdefault(key, idx)
                if first != idx:
                    pair = (min(first, idx), max(first, idx))
                    shared[pair] = shared.get(pair, 0.0) + distinct_ground._edge_miles(pa, pb)
    out = []
    for (i, j), miles in sorted(shared.items(), key=lambda kv: -kv[1]):
        if miles < min_mi:
            continue
        a, b = trails[i], trails[j]
        out.append({
            "trails": [str(a.get("name")), str(b.get("name"))],
            "trail_ids": [str(a.get("id")), str(b.get("id"))],
            "same_name": _norm_name(a.get("name")) == _norm_name(b.get("name")),
            "shared_mi": round(miles, 2),
        })
    return out


def audit_area(document: dict, max_gap_m: float = DEFAULT_MAX_GAP_M) -> dict:
    trails = [t for t in (document.get("trails") or []) if isinstance(t, dict)]
    return {
        "area_id": document.get("id"),
        "trail_count": len(trails),
        "total_mi": document.get("total_mi"),
        "distinct_mi": distinct_ground.distinct_miles(trails),
        "same_name_gaps": same_name_gaps(trails, max_gap_m),
        "overlaps": overlaps(trails),
    }


def _inside(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
        return True
    except ValueError:
        return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--geom-dir", default=str(DEFAULT_GEOM), help="read-only input")
    ap.add_argument("--area", action="append", default=[], help="area id; repeatable")
    ap.add_argument("--max-gap-m", type=float, default=DEFAULT_MAX_GAP_M)
    ap.add_argument("--json", help="write the full report here (not inside public/areas)")
    ap.add_argument("--top", type=int, default=20, help="areas to list per section")
    args = ap.parse_args(argv)
    if args.json:
        target = Path(os.path.realpath(args.json))
        if _inside(target, Path(os.path.realpath(PUBLIC_AREAS))) or \
                _inside(target, Path(os.path.realpath(args.geom_dir))):
            ap.error("--json must not point inside public/areas or the geom dir")
    geom_dir = Path(args.geom_dir)
    paths = ([geom_dir / f"{a}.json" for a in args.area] if args.area
             else sorted(geom_dir.glob("*.json")))
    reports, unreadable = [], []
    for path in paths:
        try:
            with open(path, encoding="utf-8") as f:
                document = json.load(f)
        except (OSError, ValueError) as error:
            unreadable.append(f"{path.name}: {type(error).__name__}")
            continue
        if isinstance(document, dict):
            reports.append(audit_area(document, args.max_gap_m))
    gapped = [r for r in reports if r["same_name_gaps"]]
    overlapped = [r for r in reports if r["overlaps"]]
    print(f"WARN-ONLY audit of {len(reports)} areas in {geom_dir}")
    print(f"  same-name gaps under {args.max_gap_m:g} m: "
          f"{sum(len(r['same_name_gaps']) for r in gapped)} in {len(gapped)} areas")
    print(f"  overlapping trail pairs (>= {MIN_OVERLAP_MI} mi shared): "
          f"{sum(len(r['overlaps']) for r in overlapped)} in {len(overlapped)} areas")
    if unreadable:
        print(f"  unreadable files skipped: {len(unreadable)} (first: {unreadable[0]})")
    print("\nMost same-name gaps:")
    for r in sorted(gapped, key=lambda r: -len(r["same_name_gaps"]))[:args.top]:
        g = min(r["same_name_gaps"], key=lambda gap: gap["gap_m"])
        print(f"  {r['area_id']}: {len(r['same_name_gaps'])} gaps, "
              f"smallest {g['gap_m']} m on {g['name']!r}")
    print("\nMost shared ground:")
    for r in sorted(overlapped, key=lambda r: -sum(o["shared_mi"] for o in r["overlaps"]))[:args.top]:
        o = r["overlaps"][0]
        print(f"  {r['area_id']}: total_mi {r['total_mi']}, distinct_mi {r['distinct_mi']}, "
              f"{len(r['overlaps'])} pairs; largest {o['shared_mi']} mi "
              f"{o['trails'][0]!r} / {o['trails'][1]!r}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"max_gap_m": args.max_gap_m, "touch_m": TOUCH_M,
                       "min_overlap_mi": MIN_OVERLAP_MI, "areas": reports,
                       "unreadable": unreadable}, f, indent=1)
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
