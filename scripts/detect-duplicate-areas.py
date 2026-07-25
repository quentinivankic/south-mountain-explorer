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

# A nested area is only a REDUNDANT re-listing (hide from browse) when it is
# near-coextensive with its container. A 3-trail wilderness inside a 380-trail
# forest is a distinct destination someone searches by name — NOT redundant.
# The ratio = A's trails / container's trails is the knob; RATIO gates the alias.
RATIO = float(os.environ.get("NEST_RATIO", "0.75"))
nested_ratios = []
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
    if ratio >= RATIO:
        aliases[aid] = {"canonical": container, "kind": "nested", "ratio": round(ratio, 2)}

# How the nested count moves with the threshold, so the knob is picked on data.
import bisect
nr = sorted(nested_ratios)
print(f"\nnested subset candidates: {len(nr)} (ratio = A_trails / container_trails)",
      file=sys.stderr)
for th in (0.5, 0.6, 0.75, 0.9, 0.95):
    n = len(nr) - bisect.bisect_left(nr, th)
    print(f"  ratio >= {th}: {n} areas aliased", file=sys.stderr)

dups = {k: v for k, v in aliases.items() if v["kind"] == "duplicate"}
nested = {k: v for k, v in aliases.items() if v["kind"] == "nested"}
print(f"duplicate groups: {len(dup_groups)}  aliased-away: {len(dups)}", file=sys.stderr)
print(f"nested aliases:   {len(nested)}", file=sys.stderr)

os.makedirs(OUT, exist_ok=True)
with open(f"{OUT}/aliases.json", "w") as f:
    json.dump(dict(sorted(aliases.items())), f, indent=0)
with open(f"{OUT}/aliases-report.tsv", "w") as f:
    f.write("id\tkind\tcanonical\tid_name\tid_trails\tcanon_name\tcanon_trails\tstate\n")
    for a, v in sorted(aliases.items(), key=lambda kv: (kv[1]["kind"], kv[0])):
        c = v["canonical"]
        f.write(f"{a}\t{v['kind']}\t{c}\t{meta.get(a,{}).get('name')}\t"
                f"{meta.get(a,{}).get('trailCount')}\t{meta.get(c,{}).get('name')}\t"
                f"{meta.get(c,{}).get('trailCount')}\t{meta.get(a,{}).get('state')}\n")

# A few examples for the eyeball.
print("\n--- duplicate examples ---", file=sys.stderr)
for a, v in list(dups.items())[:6]:
    print(f"  {a}  ->  {v['canonical']}   ({meta.get(a,{}).get('name')})", file=sys.stderr)
print("--- nested examples ---", file=sys.stderr)
for a, v in list(nested.items())[:6]:
    print(f"  {a} ({meta.get(a,{}).get('trailCount')} tr)  ->  "
          f"{v['canonical']} ({meta.get(v['canonical'],{}).get('trailCount')} tr)", file=sys.stderr)
