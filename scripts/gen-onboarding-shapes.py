#!/usr/bin/env python3
"""Bake South Mountain's per-trail geometry into a small normalized JSON
resource for the onboarding hero animation (trails lighting cyan one by one).

Reads the shipped geom, projects to a local equirectangular frame (so the
silhouette keeps its true proportions at ~33 deg N), normalizes to a 0..1 box
with y pointing DOWN (screen space), simplifies each polyline with
Ramer-Douglas-Peucker, and orders trails left-to-right so the animation reads
as a wave filling across the park.

Output: ios/SouthMountainExplorer/Resources/onboarding-south-mountain.json
    {"trails": [ [ [[x,y],...], ...segments ], ...trails ]}

Deterministic; re-run any time. Not wired into publish — this is a one-off
art asset baked from a curated area.
"""
import json, math, os, sys

SRC = "public/areas/geom/south-mountain-park-and-preserve-az.json"
OUT = "ios/SouthMountainExplorer/Resources/onboarding-south-mountain.json"
RDP_EPS = 0.006          # in normalized 0..1 units
MARGIN = 0.04            # keep strokes off the very edge


def rdp(points, eps):
    if len(points) < 3:
        return points
    # farthest point from the chord
    (x0, y0), (x1, y1) = points[0], points[-1]
    dx, dy = x1 - x0, y1 - y0
    norm = math.hypot(dx, dy) or 1e-9
    dmax, idx = 0.0, 0
    for i in range(1, len(points) - 1):
        px, py = points[i]
        d = abs((px - x0) * dy - (py - y0) * dx) / norm
        if d > dmax:
            dmax, idx = d, i
    if dmax > eps:
        left = rdp(points[: idx + 1], eps)
        right = rdp(points[idx:], eps)
        return left[:-1] + right
    return [points[0], points[-1]]


def main():
    geom = json.load(open(SRC))
    clat = geom["center_lat"]
    clon = geom["center_lon"]
    cosf = math.cos(math.radians(clat))

    # Project every node once, tracking the bounds.
    raw = []  # per trail: list of segments of (px, py)
    minx = miny = math.inf
    maxx = maxy = -math.inf
    for tr in geom["trails"]:
        segs = []
        for seg in tr.get("segments", []):
            pts = []
            for node in seg:
                if len(node) < 2:
                    continue
                lat, lon = node[0], node[1]
                px = (lon - clon) * cosf
                py = -(lat - clat)          # invert so north is up (screen y-down)
                pts.append((px, py))
                minx, maxx = min(minx, px), max(maxx, px)
                miny, maxy = min(miny, py), max(maxy, py)
            if len(pts) >= 2:
                segs.append(pts)
        if segs:
            raw.append(segs)

    spanx = (maxx - minx) or 1e-9
    spany = (maxy - miny) or 1e-9
    scale = (1.0 - 2 * MARGIN) / max(spanx, spany)   # uniform → preserve aspect
    offx = (1.0 - spanx * scale) / 2.0
    offy = (1.0 - spany * scale) / 2.0

    def norm(p):
        x = offx + (p[0] - minx) * scale
        y = offy + (p[1] - miny) * scale
        return [round(x, 4), round(y, 4)]

    trails = []
    for segs in raw:
        nsegs = []
        for pts in segs:
            npts = [norm(p) for p in pts]
            npts = rdp(npts, RDP_EPS)
            if len(npts) >= 2:
                nsegs.append(npts)
        if nsegs:
            cx = sum(p[0] for s in nsegs for p in s) / sum(len(s) for s in nsegs)
            trails.append((cx, nsegs))

    trails.sort(key=lambda t: t[0])              # left-to-right sweep order
    out = {"trails": [t[1] for t in trails]}

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    total_pts = sum(len(s) for t in out["trails"] for s in t)
    sz = os.path.getsize(OUT)
    print(f"wrote {OUT}: {len(out['trails'])} trails, {total_pts} points, {sz/1024:.1f} KB")


if __name__ == "__main__":
    main()
