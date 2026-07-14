#!/usr/bin/env python3
"""Build the per-trail thumbnail-shapes file for Browse search results.

The trail-search index (build-trail-search-index.py) carries no geometry, so a
search hit in an UN-visited area can only show a generic icon. This emits a
compact companion — one Douglas-Peucker-simplified polyline per trail, pre-
normalized to a 0-255 box — so the app can draw the trail's linework in the
search row without fetching the full area geom.

Deliberately a SEPARATE file from the search index: it's ~3 MB (vs the index's
~1.3 MB), so it loads in the BACKGROUND off the search critical path (see
TrailShapeService). Only the LONGEST segment of each trail is kept — plenty for
a 44 px thumbnail, and it keeps the file small.

Simplification uses a fixed epsilon relative to each trail's own bounding box
(2% of the projected bbox diagonal), so it's one DP pass per trail (fast enough
for CI over 83k trails) and gives consistent visual fidelity regardless of
trail size. Verified: ~11 points/trail, ~3 MB gzipped.

    python3 scripts/build-trail-shapes.py --out /tmp/trail-shapes.json
"""
from __future__ import annotations

import argparse
import json
import math
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BUNDLE = os.path.join(_ROOT, "ios", "SouthMountainExplorer", "Resources", "areas-index.json")
_GEOM = os.path.join(_ROOT, "public", "areas", "geom")

_EPS_FRAC = 0.02   # DP epsilon as a fraction of each trail's bbox diagonal
_MAX_PTS = 40      # coarsen the rare monster trail a second time above this


def _perp(pt, a, b):
    if a == b:
        return math.dist(pt, a)
    (x0, y0), (x1, y1), (x2, y2) = pt, a, b
    num = abs((y2 - y1) * x0 - (x2 - x1) * y0 + x2 * y1 - y2 * x1)
    den = math.hypot(y2 - y1, x2 - x1)
    return num / den if den else 0


def _dp(pts, eps):
    if len(pts) < 3:
        return pts
    dmax, idx = 0.0, 0
    for i in range(1, len(pts) - 1):
        d = _perp(pts[i], pts[0], pts[-1])
        if d > dmax:
            dmax, idx = d, i
    if dmax > eps:
        return _dp(pts[:idx + 1], eps)[:-1] + _dp(pts[idx:], eps)
    return [pts[0], pts[-1]]


def _shape(seg: list) -> list[int] | None:
    """A trail's [lat, lon] segment -> flat [x0, y0, x1, y1, ...] 0-255 ints."""
    if len(seg) < 2:
        return None
    clat = sum(p[0] for p in seg) / len(seg)
    k = math.cos(math.radians(clat))
    proj = [(p[1] * k, p[0]) for p in seg]      # (x=lon*cos, y=lat)
    xs = [p[0] for p in proj]
    ys = [p[1] for p in proj]
    diag = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
    eps = diag * _EPS_FRAC
    s = _dp(proj, eps)
    if len(s) > _MAX_PTS:
        s = _dp(proj, eps * 2)
    minx, miny = min(xs), min(ys)
    rng = max(max(xs) - minx, max(ys) - miny, 1e-9)
    flat: list[int] = []
    for x, y in s:
        flat.append(round((x - minx) / rng * 255))
        flat.append(round((y - miny) / rng * 255))
    return flat


def build(bundle_path: str, geom_dir: str) -> dict[str, list[int]]:
    slugs = [r[0] for r in json.load(open(bundle_path)) if r]
    out: dict[str, list[int]] = {}
    for slug in slugs:
        try:
            g = json.load(open(os.path.join(geom_dir, f"{slug}.json")))
        except Exception:
            continue
        if not isinstance(g, dict) or "cached_at" in g:
            continue
        for t in g.get("trails", []):
            tid, segs = t.get("id"), t.get("segments") or []
            if not tid or not segs:
                continue
            shape = _shape(max(segs, key=len))
            if shape:
                out[f"{slug}/{tid}"] = shape
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bundle", default=_BUNDLE)
    ap.add_argument("--geom-dir", default=_GEOM)
    ap.add_argument("--out", required=True, help="output path (R2-served, not committed)")
    args = ap.parse_args(argv)

    shapes = build(args.bundle, args.geom_dir)
    json.dump(shapes, open(args.out, "w"), separators=(",", ":"))
    size = os.path.getsize(args.out)
    pts = sum(len(v) // 2 for v in shapes.values())
    print(f"{len(shapes):,} trail shapes ({pts/max(len(shapes),1):.1f} avg pts) "
          f"-> {args.out} ({size/1_048_576:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
