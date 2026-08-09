#!/usr/bin/env python3
"""Bake a handful of park silhouettes into one small JSON resource for the
onboarding "Discover" page gallery cards. Same projection + simplification as
gen-onboarding-shapes.py, but each area is normalized to its OWN 0..1 box and
all its segments are flattened into one line list (the cards draw a static
silhouette, no per-trail animation).

Output: ios/SouthMountainExplorer/Resources/onboarding-discover-gallery.json
    {"areas": [{"id","label","lines": [ [[x,y],...], ... ]}]}

Deterministic art asset; re-run any time. Not wired into publish.
"""
import json, math, os

# (geom id, short display label) — recognizable, visually distinct parks.
AREAS = [
    ("zion-national-park-ut", "Zion"),
    ("grand-canyon-national-park-az", "Grand Canyon"),
    ("joshua-tree-national-park-ca", "Joshua Tree"),
    ("griffith-park-ca", "Griffith Park"),
]
OUT = "ios/SouthMountainExplorer/Resources/onboarding-discover-gallery.json"
RDP_EPS = 0.012          # simplify harder than page 1: cards are small
MARGIN = 0.06

# Per-trail difficulty → one-letter code the app colours (green / orange / red /
# gray), matching MapKitMapView's difficultyColor so the cards read like the map.
DIFF = {"Easy": "e", "Moderate": "m", "Hard": "h"}


def rdp(points, eps):
    if len(points) < 3:
        return points
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
        return rdp(points[: idx + 1], eps)[:-1] + rdp(points[idx:], eps)
    return [points[0], points[-1]]


def bake(area_id):
    geom = json.load(open(f"public/areas/geom/{area_id}.json"))
    clat, clon = geom["center_lat"], geom["center_lon"]
    cosf = math.cos(math.radians(clat))
    segs = []  # (difficulty_code, [(px, py), ...])
    minx = miny = math.inf
    maxx = maxy = -math.inf
    for tr in geom["trails"]:
        dcode = DIFF.get(tr.get("difficulty"), "u")
        for seg in tr.get("segments", []):
            pts = []
            for node in seg:
                if len(node) < 2:
                    continue
                px = (node[1] - clon) * cosf
                py = -(node[0] - clat)
                pts.append((px, py))
                minx, maxx = min(minx, px), max(maxx, px)
                miny, maxy = min(miny, py), max(maxy, py)
            if len(pts) >= 2:
                segs.append((dcode, pts))
    spanx = (maxx - minx) or 1e-9
    spany = (maxy - miny) or 1e-9
    scale = (1.0 - 2 * MARGIN) / max(spanx, spany)
    offx = (1.0 - spanx * scale) / 2.0
    offy = (1.0 - spany * scale) / 2.0
    lines = []
    for dcode, pts in segs:
        npts = [[round(offx + (p[0] - minx) * scale, 4),
                 round(offy + (p[1] - miny) * scale, 4)] for p in pts]
        npts = rdp(npts, RDP_EPS)
        if len(npts) >= 2:
            lines.append({"d": dcode, "p": npts})
    return lines


def main():
    areas = []
    for aid, label in AREAS:
        lines = bake(aid)
        areas.append({"id": aid, "label": label, "lines": lines})
        print(f"  {label}: {len(lines)} lines, {sum(len(l) for l in lines)} pts")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump({"areas": areas}, f, separators=(",", ":"))
    print(f"wrote {OUT}: {os.path.getsize(OUT)/1024:.1f} KB")


if __name__ == "__main__":
    main()
