#!/usr/bin/env python3
"""Detect redundant published areas so browse/search shows each place once.

Two kinds, both keyed on TRAIL geometry (not area name / bbox), because the
duplication is at the trail level:

  duplicate  A and B carry the SAME multiset of trail geometries (accent/slug
             twins like south-mountain-preserve vs -park-and-preserve). Safe to
             collapse: canonical survives, the other is aliased to it.
  nested     A's trails are a STRICT SUBSET of B's — A adds no trail B lacks
             (a district fully inside its park). Keep BOTH areas; only hide A
             from the top-level list, canonical = the container B.

An area that has even ONE trail no other area has is never aliased — that trail
would be lost from browse. Identity = per-trail fingerprint (coords rounded to
5dp, SHA256), the same notion the app uses so completion already crosses these.

Emits scratchpad/aliases.json {id: {canonical, kind}} + a TSV report. Nothing
here mutates the index or geom — this is the dry-run to eyeball first.
"""
import json, glob, hashlib, os, sys
from collections import defaultdict

GEOM = "public/areas/geom"
OUT = os.environ.get("OUT_DIR", ".")

def trail_fp(trail):
    parts = []
    for seg in trail.get("segments") or []:
        for pt in seg:
            parts.append("%.5f,%.5f" % (pt[0], pt[1]))
    h = hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]
    return h

# Load index rows for name/state/osm_rel metadata.
idx = json.load(open("public/areas/index.json"))
def row_meta(r):
    return {"name": r[1] if len(r) > 1 else r[0],
            "state": r[2] if len(r) > 2 else "",
            "trailCount": r[5] if len(r) > 5 else 0,
            "osmRel": r[7] if len(r) > 7 else None}
meta = {r[0]: row_meta(r) for r in idx}

area_fps = {}          # id -> frozenset of trail fps
area_multiset = {}     # id -> sorted tuple (for identical detection)
for path in glob.glob(f"{GEOM}/*.json"):
    aid = os.path.basename(path)[:-5]
    try:
        d = json.load(open(path))
    except Exception:
        continue
    trails = d.get("trails") or []
    fps = [trail_fp(t) for t in trails if t.get("segments")]
    if not fps:
        continue
    area_fps[aid] = frozenset(fps)
    area_multiset[aid] = tuple(sorted(fps))

print(f"areas with geom+trails: {len(area_fps)}", file=sys.stderr)

# --- duplicates: identical trail multiset -----------------------------------
by_multiset = defaultdict(list)
for aid, ms in area_multiset.items():
    by_multiset[ms].append(aid)
dup_groups = [ids for ids in by_multiset.values() if len(ids) > 1]

def canonical_of(ids):
    # Prefer an area with an OSM relation id, then the longest display name,
    # then the lexicographically smallest id. Deterministic across runs.
    def key(a):
        m = meta.get(a, {})
        return (0 if m.get("osmRel") else 1, -len(m.get("name") or ""), a)
    return sorted(ids, key=key)[0]

aliases = {}   # id -> {canonical, kind}
for ids in dup_groups:
    canon = canonical_of(ids)
    for a in ids:
        if a != canon:
            aliases[a] = {"canonical": canon, "kind": "duplicate"}

# --- nested: strict subset of a bigger area ---------------------------------
# Inverted index fp -> areas; A is contained in every area appearing in ALL of
# A's postings. Skip A already aliased as a duplicate.
fp2areas = defaultdict(set)
for aid, fps in area_fps.items():
    for fp in fps:
        fp2areas[fp].add(aid)

# Designation ladder — higher = the name a hiker searches for and expects to
# survive, INDEPENDENT of which polygon geometrically contains which. A National
# Park inside a binational Peace Park is still what people look up; a Wilderness
# inside a National Forest is the destination, but inside a State Park it is a
# sub-unit. Order verified against the 2026-07-19 sanity set (see memory
# nested-area-dedup.md). Unrecognised names score 0.
RANK = [
    ("national park", 100), ("peace park", 96), ("national seashore", 95),
    ("national lakeshore", 95), ("national monument", 90),
    ("national volcanic", 88), ("state park", 70),
    ("national recreation area", 60), ("recreation area", 58),
    ("wilderness", 55), ("wildlife refuge", 52), ("preserve", 50),
    ("state natural area", 42), ("natural area", 42), ("state forest", 36),
    ("open space", 34), ("national forest", 30),
]
def rank(name):
    n = (name or "").lower()
    return max((r for kw, r in RANK if kw in n), default=0)

# THE LOSSLESS INVARIANT. Only alias A -> canonical when the canonical's trail
# set is a SUPERSET of A's AND the canonical is at least as iconic. A nested area
# is a strict subset of its container, so the container is always a superset —
# but if the container is the LESS iconic name (Glacier NP inside Waterton-Glacier
# Peace Park), aliasing would hide the name people search for. In that "trap"
# case we do NOT alias either way and keep BOTH entries: never hide the
# more-iconic area, and never hide a trail that has no visible superset home.
# Consequence: nothing that gets hidden has a single trail its canonical lacks,
# so no union / graft is ever needed and 0 trails can be lost. Proven below.
RATIO = float(os.environ.get("NEST_RATIO", "0.75"))
nested_ratios, traps = [], []
for aid, fps in area_fps.items():
    if aid in aliases:
        continue
    supersets = set.intersection(*(fp2areas[fp] for fp in fps)) - {aid}
    strict = [s for s in supersets
              if len(area_fps[s]) > len(fps) and s not in aliases]
    if not strict:
        continue
    container = min(strict, key=lambda s: (len(area_fps[s]), s))
    ratio = len(fps) / len(area_fps[container])
    nested_ratios.append(ratio)
    if ratio < RATIO:
        continue
    if rank(meta[container]["name"]) >= rank(meta[aid]["name"]):
        aliases[aid] = {"canonical": container, "kind": "nested", "ratio": round(ratio, 2)}
    else:
        traps.append((aid, container, round(ratio, 2)))  # kept as two entries

# Resolve any A->B->C chain to its ultimate visible canonical (defensive; near-
# coextensive chains are possible). Break cycles rather than loop forever.
for a in list(aliases):
    seen, c = {a}, aliases[a]["canonical"]
    while c in aliases and c not in seen:
        seen.add(c); c = aliases[c]["canonical"]
    aliases[a]["canonical"] = c

# ---- VERIFY completeness: no trail is lost (quality gate, not exit code) -----
visible = {a: f for a, f in area_fps.items() if a not in aliases}
visible_fps = set().union(*visible.values()) if visible else set()
all_fps = set().union(*area_fps.values())
orphaned = all_fps - visible_fps                       # trails with no visible home
bad_superset = [a for a, v in aliases.items()
                if not area_fps[a] <= area_fps.get(v["canonical"], frozenset())]
print(f"\nVERIFY superset invariant: {len(bad_superset)} violations "
      f"(hidden area whose canonical lacks one of its trails)", file=sys.stderr)
print(f"VERIFY trail completeness: {len(orphaned)} orphaned fingerprints "
      f"of {len(all_fps)} (trails visible nowhere after hiding)", file=sys.stderr)
assert not bad_superset, f"LOSSY: {bad_superset[:5]}"
assert not orphaned, f"LOSSY: {len(orphaned)} trails would vanish"
print("VERIFY: PASS — every hidden area's trails all survive under its canonical",
      file=sys.stderr)

import bisect
nr = sorted(nested_ratios)
print(f"\nnested subset candidates: {len(nr)} (ratio = A_trails / container_trails)",
      file=sys.stderr)
for th in (0.5, 0.6, 0.75, 0.9, 0.95):
    n = len(nr) - bisect.bisect_left(nr, th)
    print(f"  ratio >= {th}: {n}", file=sys.stderr)

dups = {k: v for k, v in aliases.items() if v["kind"] == "duplicate"}
nested = {k: v for k, v in aliases.items() if v["kind"] == "nested"}
print(f"\nduplicate groups: {len(dup_groups)}  hidden: {len(dups)}", file=sys.stderr)
print(f"nested hidden:    {len(nested)}", file=sys.stderr)
print(f"traps kept as two entries (never hidden): {len(traps)}", file=sys.stderr)

os.makedirs(OUT, exist_ok=True)
# Ship only id -> {canonical, kind}; the ratio was a tuning aid, not runtime data.
ship = {a: {"canonical": v["canonical"], "kind": v["kind"]}
        for a, v in sorted(aliases.items())}
with open(f"{OUT}/aliases.json", "w") as f:
    json.dump(ship, f, separators=(",", ":"), sort_keys=True)
    f.write("\n")
with open(f"{OUT}/aliases-report.tsv", "w") as f:
    f.write("id\tkind\tcanonical\tid_name\tid_trails\tcanon_name\tcanon_trails\tstate\n")
    for a, v in sorted(aliases.items(), key=lambda kv: (kv[1]["kind"], kv[0])):
        c = v["canonical"]
        f.write(f"{a}\t{v['kind']}\t{c}\t{meta.get(a,{}).get('name')}\t"
                f"{meta.get(a,{}).get('trailCount')}\t{meta.get(c,{}).get('name')}\t"
                f"{meta.get(c,{}).get('trailCount')}\t{meta.get(a,{}).get('state')}\n")
with open(f"{OUT}/traps-kept-both.tsv", "w") as f:
    f.write("iconic_kept\ttrails\tless_iconic_also_kept\ttrails\tratio\tstate\n")
    for aid, cont, ratio in sorted(traps):
        f.write(f"{meta[aid]['name']}\t{meta.get(aid,{}).get('trailCount')}\t"
                f"{meta[cont]['name']}\t{meta.get(cont,{}).get('trailCount')}\t"
                f"{ratio}\t{meta.get(aid,{}).get('state')}\n")

print("\n--- trap pairs kept as TWO entries (iconic never hidden) ---", file=sys.stderr)
for aid, cont, ratio in sorted(traps):
    print(f"  keep BOTH: {meta[aid]['name']} + {meta[cont]['name']}", file=sys.stderr)
