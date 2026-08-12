#!/usr/bin/env python3
"""Name-stitch teleport audit (task #36) — the baseline #36 was missing.

We build route/named trails by NAME-STITCH ("all ways named X"). When two
same-named ways sit far apart, the merge welds them into ONE shipped "trail"
whose geometry teleports: its vertices form >=2 spatially disjoint chunks with a
big empty gap. Confirmed smoking gun: "Yellow" in adirondack-park (two chunks
111 km apart — every color-coded local "Yellow" trail welded into one object).

Runs off shipped geom in public/areas/geom (no OSM, no network). Complements
audit-trail-quality.py's #31 measure: that reads profileGaps (gaps ALONG segment
order) and so misses trails split into SEPARATE segments with no in-segment gap.

Detector: per trail, connected-components of its (distance-decimated) vertices by
proximity (link <= LINK m). A continuous real trail collapses to ONE component
however long; welded chunks stay separate. Flag when the two biggest chunks are
>= GAPMIN m apart.

  python3 scripts/audit-namestitch-teleport.py [out.json]   # national + optional dump
  python3 scripts/audit-namestitch-teleport.py --area <slug> [name-substr]

CAVEAT (do not over-read): the flag is the UNION of (a) real name-stitch welds of
unrelated ways — usually generic/color names — and (b) genuinely long routes with
real missing-data gaps (#35, e.g. Mokelumne Coast to Crest). Geom alone can't
separate them; that needs OSM way-ids / relation membership, which is the #36
pipeline decision. Generic/single-word/color names are the high-confidence welds.
"""
import json, glob, math, sys, re
from pathlib import Path
from collections import defaultdict, Counter

GEOM = Path(__file__).resolve().parent.parent / "public" / "areas" / "geom"
LINK = 600.0      # m: verts within this are the same chunk
DECIM = 80.0      # m: keep <=1 vertex per DECIM along the path (preserves gaps >= DECIM)
MINPTS = 3        # a chunk needs this many verts to count
GAPMIN = 1000.0   # m: flag when the two biggest chunks are >= this apart
MI = 1 / 1609.34


def dm(a, b, mlat, mlon):
    return math.hypot((a[0] - b[0]) * mlat, (a[1] - b[1]) * mlon)


def trail_verts(t):
    """concatenated [lat,lon] vertices, distance-decimated to <=1 per DECIM m."""
    out, last, lat0 = [], None, None
    for s in t.get("segments") or []:
        for p in s:
            if len(p) < 2:
                continue
            if lat0 is None:
                lat0 = p[0]
            if last is None:
                out.append([p[0], p[1]]); last = p; continue
            mlat = 111320.0; mlon = 111320.0 * math.cos(math.radians(lat0))
            if math.hypot((p[0] - last[0]) * mlat, (p[1] - last[1]) * mlon) >= DECIM:
                out.append([p[0], p[1]]); last = p
    return out


def components(verts):
    """grid union-find by proximity -> list of index lists."""
    if not verts:
        return [], 0, 0
    lat0 = sum(v[0] for v in verts) / len(verts)
    mlat = 111320.0; mlon = 111320.0 * math.cos(math.radians(lat0))
    def key(v):
        return (int(v[0] * mlat // LINK), int(v[1] * mlon // LINK))
    grid = defaultdict(list)
    for i, v in enumerate(verts):
        grid[key(v)].append(i)
    parent = list(range(len(verts)))
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    def uni(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    for (cx, cy), idxs in grid.items():
        neigh = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                neigh += grid.get((cx + dx, cy + dy), [])
        for i in idxs:
            for j in neigh:
                if j > i and dm(verts[i], verts[j], mlat, mlon) <= LINK:
                    uni(i, j)
    comps = defaultdict(list)
    for i in range(len(verts)):
        comps[find(i)].append(i)
    return list(comps.values()), mlat, mlon


def cluster_gap(verts, ca, cb, mlat, mlon):
    A = ca if len(ca) <= 300 else ca[::max(1, len(ca) // 300)]
    B = cb if len(cb) <= 300 else cb[::max(1, len(cb) // 300)]
    best = 1e18
    for i in A:
        vi = verts[i]
        for j in B:
            d = dm(vi, verts[j], mlat, mlon)
            if d < best:
                best = d
    return best


def centroid(verts, idxs):
    return [round(sum(verts[i][0] for i in idxs) / len(idxs), 5),
            round(sum(verts[i][1] for i in idxs) / len(idxs), 5)]


def bbox_diag_m(verts):
    la = [v[0] for v in verts]; lo = [v[1] for v in verts]
    lat0 = (min(la) + max(la)) / 2
    return math.hypot((max(la) - min(la)) * 111320.0,
                      (max(lo) - min(lo)) * 111320.0 * math.cos(math.radians(lat0)))


def max_profile_gap_mi(t):
    g = t.get("profileGaps") or []
    return max((x[1] for x in g if len(x) >= 2), default=0.0) * MI


def analyze(t):
    verts = trail_verts(t)
    if len(verts) < 2 * MINPTS:
        return None
    if bbox_diag_m(verts) < GAPMIN:
        return None
    comps, mlat, mlon = components(verts)
    big = sorted((c for c in comps if len(c) >= MINPTS), key=len, reverse=True)
    if len(big) < 2:
        return None
    gap = cluster_gap(verts, big[0], big[1], mlat, mlon)
    if gap < GAPMIN:
        return None
    return {"gap_km": round(gap / 1000, 2), "ncomp": len(big),
            "sizes": [len(c) for c in big[:4]], "nverts": len(verts),
            "c0": centroid(verts, big[0]), "c1": centroid(verts, big[1]),
            "profgap_mi": round(max_profile_gap_mi(t), 1)}


def national(dump=None):
    flagged, tot = [], 0
    for f in sorted(glob.glob(str(GEOM / "*.json"))):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        aid = Path(f).stem
        for t in d.get("trails") or []:
            tot += 1
            r = analyze(t)
            if r:
                r["area"] = aid; r["name"] = t.get("name")
                flagged.append(r)
    flagged.sort(key=lambda r: r["gap_km"], reverse=True)
    n1 = sum(1 for r in flagged if r["gap_km"] >= 1)
    n2 = sum(1 for r in flagged if r["gap_km"] >= 2)
    n4 = sum(1 for r in flagged if r["gap_km"] >= 4)
    miss31 = sum(1 for r in flagged if r["profgap_mi"] < 2)
    names = Counter(r["name"] or "?" for r in flagged)
    dup = sum(1 for n, c in names.items() if c > 1)
    color = re.compile(r'^(yellow|red|blue|green|orange|white|black|purple|pink)\b', re.I)
    generic = sum(1 for r in flagged if r["name"]
                  and (color.match(r["name"]) or len(r["name"].split()) <= 2))
    print(f"trails scanned: {tot}")
    print(f"TELEPORTS (>=2 chunks, two biggest >= {GAPMIN:.0f} m apart):")
    print(f"  gap>=1km: {n1}   gap>=2km: {n2}   gap>=4km: {n4}")
    print(f"  distinct names: {len(names)}   name in >1 area (route dup): {dup}")
    print(f"  generic/color/short-named (high-confidence welds): {generic}")
    print(f"  profileGap<2mi (missed by audit-trail-quality.py #31): {miss31}")
    print("\nTop 20 by gap:")
    for r in flagged[:20]:
        print(f"  {r['gap_km']:7.1f}km  {str(r['sizes']):16} prof={r['profgap_mi']:5.1f}mi  "
              f"{(r['name'] or '?')[:32]:32}  {r['area']}")
    if dump:
        Path(dump).write_text(json.dumps(flagged))
        print(f"\nfull list -> {dump}  ({len(flagged)} rows)")


def inspect(slug, sub=None):
    d = json.load(open(GEOM / f"{slug}.json"))
    for t in d.get("trails") or []:
        nm = t.get("name") or ""
        if sub and sub.lower() not in nm.lower():
            continue
        verts = trail_verts(t)
        comps, _, _ = components(verts) if verts else ([], 0, 0)
        big = [c for c in comps if len(c) >= MINPTS]
        r = analyze(t)
        tag = f"TELEPORT gap={r['gap_km']}km {r['sizes']}" if r else "ok"
        print(f"{nm[:40]:40} verts={len(verts):5} comps={len(big):3} {tag}")
        if r:
            print(f"    chunk0 {r['c0']}  chunk1 {r['c1']}  profileGap={r['profgap_mi']}mi")


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--area":
        inspect(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
    else:
        national(sys.argv[1] if len(sys.argv) > 1 else None)
