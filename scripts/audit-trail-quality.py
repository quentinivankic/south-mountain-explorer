#!/usr/bin/env python3
"""Trail-quality audit — reproduces the baselines for tasks #30/#31/#35.

Runs off the shipped geom in public/areas/geom (no OSM, no network). Re-run after
a curation/pipeline change to confirm the numbers moved. Baselines recorded
2026-07-26 in the auto-memory `area-quality-grayling-audit`.

  python3 scripts/audit-trail-quality.py

Metrics:
  #30 degenerate ~0-length trails  — true geometry < 0.01mi, split isolated vs
       both-ends connector (drop the isolated+spur, keep point-connectors)
  #31 huge internal gaps           — from each trail's profileGaps [[idx, metres]]
  #35 fragmentation score          — gap-miles / trail-miles, and gap count
"""
import json, glob, math
from pathlib import Path
from collections import defaultdict

GEOM = Path(__file__).resolve().parent.parent / "public" / "areas" / "geom"
MI_PER_M = 1 / 1609.34

def mi(a, b):  # miles between [lat,lon] points
    R = 3959.0
    dlat = math.radians(b[0] - a[0]); dlon = math.radians(b[1] - a[1])
    x = (math.sin(dlat/2)**2
         + math.cos(math.radians(a[0])) * math.cos(math.radians(b[0])) * math.sin(dlon/2)**2)
    return 2 * R * math.asin(math.sqrt(x))

def geom_len(t):
    return sum(mi(s[k], s[k+1]) for s in (t.get("segments") or []) if len(s) >= 2
               for k in range(len(s)-1))

def rnd(p): return (round(p[0], 4), round(p[1], 4))

def main():
    total = 0
    degen = []                 # (len_mi, klass, name, area)
    gap_ge2 = gap_dom = 0
    max_gap_m = 0.0; max_gap_name = ""
    frag_scores = []           # gap_miles / length, per gapped trail
    gap_buckets = defaultdict(int)

    for f in glob.glob(str(GEOM / "*.json")):
        try: d = json.load(open(f))
        except Exception: continue
        aid = Path(f).stem
        trails = d.get("trails") or []
        # vertex ownership for connector classification
        owners = defaultdict(set)
        for i, t in enumerate(trails):
            for s in t.get("segments") or []:
                for p in s:
                    if len(p) >= 2: owners[rnd(p)].add(i)
        for i, t in enumerate(trails):
            total += 1
            L = geom_len(t)
            # #30 degenerate
            if L < 0.01:
                segs = [s for s in (t.get("segments") or []) if len(s) >= 2]
                if segs:
                    ep = [rnd(segs[0][0]), rnd(segs[-1][-1])]
                    touch = sum(1 for e in ep if any(o != i for o in owners[e]))
                    klass = {2: "connector", 1: "spur", 0: "isolated"}[touch]
                else:
                    klass = "noseg"
                degen.append((L, klass, t.get("name"), aid))
            # #31 gaps
            g = t.get("profileGaps") or []
            gaps_m = [x[1] for x in g if len(x) >= 2]
            if gaps_m:
                mx = max(gaps_m)
                if mx > max_gap_m: max_gap_m, max_gap_name = mx, f"{t.get('name')} @ {aid}"
                if mx * MI_PER_M >= 2: gap_ge2 += 1
                gsum_mi = sum(gaps_m) * MI_PER_M
                if L > 0 and gsum_mi > L: gap_dom += 1
                if L > 0: frag_scores.append(gsum_mi / L)
                for th, lbl in [(1,">=1mi"),(2,">=2mi"),(5,">=5mi"),(20,">=20mi"),(50,">=50mi")]:
                    if mx * MI_PER_M >= th: gap_buckets[lbl] += 1

    print(f"shipped trails: {total}\n")
    print("#30 DEGENERATE (true geometry < 0.01mi):")
    from collections import Counter
    kc = Counter(k for _, k, _, _ in degen)
    drop = kc["isolated"] + kc["spur"]
    print(f"  total {len(degen)}  | isolated {kc['isolated']} + spur {kc['spur']} = {drop} DROP"
          f"  | connector {kc['connector']} keep")
    print("\n#31 HUGE GAPS (from profileGaps):")
    print(f"  gap >=2mi: {gap_ge2}   gap-dominated (gap-mi > trail-mi): {gap_dom}")
    print(f"  max single gap: {max_gap_m*MI_PER_M:.0f}mi  ({max_gap_name})")
    print(f"  buckets: " + "  ".join(f"{l}:{gap_buckets[l]}" for l in [">=1mi",">=2mi",">=5mi",">=20mi",">=50mi"]))
    print("\n#35 FRAGMENTATION (gap-miles / trail-miles, gapped trails only):")
    fs = sorted(frag_scores)
    if fs:
        poor = sum(1 for x in fs if x > 0.30)
        print(f"  gapped trails: {len(fs)}   median ratio: {fs[len(fs)//2]:.2f}   >30% gap: {poor}")

if __name__ == "__main__":
    main()
