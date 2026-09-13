#!/usr/bin/env python3
"""Validate the 4 NE agent verdict drafts, report every DROP + any schema issue for
eyeball, then (with --write) merge into ne_verdicts_osm.json keyed by OSM id, keeping
Monadnock's 13. Job tmp is the stable working dir (the /tmp scratchpad is volatile)."""
import json, sys, glob
from pathlib import Path
# --- portable paths (added when these tools were graduated into the repo) -----
# PADJ_TMP   working dir holding <slug>_dossier.json etc.  default: ../work
# PADJ_GEOM  shipped trail geom                            default: <repo>/public/areas/geom
import os as _os
_HERE = _os.path.dirname(_os.path.abspath(__file__))
_ROOT = _os.path.normpath(_os.path.join(_HERE, "..", "..", ".."))
PADJ_TMP = _os.environ.get("PADJ_TMP") or _os.path.join(_HERE, "..", "work")
PADJ_GEOM = _os.environ.get("PADJ_GEOM") or _os.path.join(_ROOT, "public", "areas", "geom")
_os.makedirs(PADJ_TMP, exist_ok=True)
# -----------------------------------------------------------------------------

T = Path(PADJ_TMP)
AREAS = ["grafton-notch-state-park-me", "camels-hump-state-park-vt",
         "mount-major-state-forest-nh", "crawford-notch-state-park-nh"]
REQ = ["fid", "osm", "area", "prior", "verdict", "exists", "public", "serves"]

def name_of(dossier, fid):
    f = next((x for x in dossier["facilities"] if x["fid"] == fid), None)
    return (f or {}).get("tags_union", {}).get("name") or "(unnamed)"

issues, drops, all_entries = [], [], []
per_area = {}
for a in AREAS:
    dr = T / f"{a}_verdict_draft.json"
    if not dr.exists():
        issues.append(f"MISSING draft: {a}"); continue
    dos = json.load(open(T / f"{a}_dossier.json"))
    pub = [int(x) for x in open(T / f"{a}_pub.txt").read().split(",") if x.strip()]
    arr = json.load(open(dr))
    seen_fids = {e.get("fid") for e in arr}
    miss = set(pub) - seen_fids
    if miss:
        issues.append(f"{a}: NOT judged fids {sorted(miss)}")
    c = {"KEEP": 0, "DROP": 0, "REVIEW": 0}
    for e in arr:
        for k in REQ:
            if k not in e:
                issues.append(f"{a} fid{e.get('fid')}: missing '{k}'")
        v = e.get("verdict")
        if v not in c:
            issues.append(f"{a} fid{e.get('fid')}: bad verdict {v!r}"); continue
        c[v] += 1
        if v == "REVIEW" and not e.get("resolve_hint"):
            issues.append(f"{a} fid{e.get('fid')}: REVIEW w/o resolve_hint")
        if not e.get("osm"):
            issues.append(f"{a} fid{e.get('fid')}: empty osm")
        if v == "DROP":
            drops.append((a, e, name_of(dos, e["fid"])))
        all_entries.append(e)
    per_area[a] = c

print("=== per-area counts ===")
tot = {"KEEP": 0, "DROP": 0, "REVIEW": 0}
for a in AREAS:
    c = per_area.get(a, {})
    for k in tot: tot[k] += c.get(k, 0)
    print(f"  {a:34} {c}")
print(f"  {'NEW TOTAL':34} {tot}  (n={sum(tot.values())})")

print(f"\n=== {len(drops)} DROPS — eyeball these ===")
for a, e, nm in drops:
    fid = e["fid"]
    tile = T / f"{a}_ladder/{fid:04d}_z2.png"
    sv = e.get("serves", {}); ex = e.get("exists", {}); pu = e.get("public", {})
    print(f"\n  [{a}] #{fid} '{nm}' · prior={e.get('prior')} · conf={e.get('confidence')}")
    print(f"     EXISTS {ex.get('call')}: {ex.get('evidence')}")
    print(f"     PUBLIC {pu.get('call')}: {pu.get('evidence')}")
    print(f"     SERVES {sv.get('call')}: {sv.get('evidence')}")
    print(f"     tile: {tile}")

# flag suspicious drops: surveyed prior dropped (should usually be REVIEW unless positive contradiction)
susp = [(a, e, nm) for a, e, nm in drops if e.get("prior") == "surveyed"]
print(f"\n=== {len(susp)} SURVEYED-prior DROPS (extra scrutiny — canopy must NOT be the reason) ===")
for a, e, nm in susp:
    print(f"  [{a}] #{e['fid']} '{nm}': {e.get('exists',{}).get('evidence')}")

if issues:
    print("\n=== ISSUES ===")
    for i in issues: print("  " + i)
else:
    print("\n=== no schema issues ===")

if "--write" in sys.argv:
    store = json.load(open(T / "ne_verdicts_osm.json"))   # Monadnock 13
    before = len(store)
    for e in all_entries:
        for oid in e["osm"]:
            store[oid] = e
    json.dump(store, open(T / "ne_verdicts_osm.json", "w"), indent=0)
    print(f"\nWROTE ne_verdicts_osm.json: {before} -> {len(store)} osm keys ({len(all_entries)} new verdicts merged)")
