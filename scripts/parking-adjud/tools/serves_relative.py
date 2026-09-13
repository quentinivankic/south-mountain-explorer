#!/usr/bin/env python3
"""Relative 'serves a trail' test = the app's own rule, run agnostically.

For every trail we ship (all nearby areas), rank ALL parking lots by straight-line
distance to the trail's ENDPOINTS (Area.trailEndpoints), and take that trail's shown
set: nearest 3 within 805 m, else fallback to the nearest 2 at any distance
(Area.nearestParkingWithFallback). A lot SERVES a trail if it lands in that trail's
shown set. Include if it serves >=1 trail; else it's never the nearest parking for
anything -> drop. Output serves_rel.json {fid:{served,trail,dist_m,fallback,n}}."""
import json, math, glob, os
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

TMP=PADJ_TMP; CTX=f"{TMP}/zion_ctx.json"
GEOMDIR=PADJ_GEOM
NEAR=805.0; MAXN=3; MAXF=2
# Fallback cap: the app's nearest-2 fallback has NO distance limit, which is right
# for "give a driver a lot to aim at" but wrong for "does this lot serve a trail we
# ship". A far trail (Dixie NF, deep-creek) with no parking near it was crediting a
# random Zion-area lot as its nearest-2 — #112 at 20.7 km, #99 7.1 km, #92 6.8 km,
# all real trailheads for trails we DON'T ship nearby (the Gooseberry/JEM class the
# user drops). The sorted fallback distances split cleanly at [..4352 | 6841..], so
# a fallback lot only counts as "serving" a trail if it's within FB_MAX of it. Keeps
# Chamberlain's/Narrows (4176, the deliberate river-route exception) + #15 (4314);
# drops the 6.8 km+ other-area trailheads. Measured against the user's KEEP/DROP set.
FB_MAX=5000.0
def hav(a,b,c,d):
    R=6371000;p=math.radians
    dl=p(c-a);dn=p(d-b);x=math.sin(dl/2)**2+math.cos(p(a))*math.cos(p(c))*math.sin(dn/2)**2
    return 2*R*math.asin(math.sqrt(x))
ctx=json.load(open(CTX)); b=ctx["bbox"]; RB=(b[0]-0.06,b[1]-0.06,b[2]+0.06,b[3]+0.06)
lots=[(f["fid"],f["lat"],f["lon"]) for f in ctx["facilities"]]
# every trail in the region: (name, endpoints[(lat,lon)...]) — first+last of each segment, app-style
trails=[]
for fn in glob.glob(GEOMDIR+"/*.json"):
    try: d=json.load(open(fn))
    except Exception: continue
    bb=d.get("bbox")
    if not bb or bb[2]<RB[0] or bb[0]>RB[2] or bb[3]<RB[1] or bb[1]>RB[3]: continue
    for tr in d.get("trails",[]):
        nm=tr.get("name") or "(unnamed trail)"; ends=[]
        for s in tr["segments"]:
            if s:
                if len(s[0])>=2: ends.append((s[0][0],s[0][1]))
                if len(s[-1])>=2: ends.append((s[-1][0],s[-1][1]))
        # keep the trail only if any endpoint is in the region (edge trails still counted)
        if ends and any(RB[0]<=e[1]<=RB[2] and RB[1]<=e[0]<=RB[3] for e in ends):
            trails.append((nm,ends))
print(f"trails in region: {len(trails)} ; lots: {len(lots)}")
served={fid:[] for fid,_,_ in lots}
for nm,ends in trails:
    scored=sorted(((min(hav(la,lo,e0,e1) for e0,e1 in ends),fid) for fid,la,lo in lots))
    near=[x for x in scored if x[0]<=NEAR]
    fb = not near
    shown = (near[:MAXN] if near else [x for x in scored[:MAXF] if x[0]<=FB_MAX])
    for dm,fid in shown:
        served[fid].append((nm,round(dm),fb))
out={}
for fid,_,_ in lots:
    s=served[fid]
    if s:
        best=min(s,key=lambda z:z[1])
        out[str(fid)]={"served":True,"trail":best[0],"dist_m":best[1],"fallback":best[2],"n":len(s)}
    else:
        out[str(fid)]={"served":False}
json.dump(out,open(f"{TMP}/serves_rel.json","w"),indent=0)
ns=sum(1 for v in out.values() if v["served"]); nfb=sum(1 for v in out.values() if v.get("fallback"))
print(f"serves >=1 trail: {ns}/{len(lots)}  (of which nearest-parking-via-fallback: {nfb})")
# spot rows
F={f["fid"]:f for f in ctx["facilities"]}
for fid in (5,95,107,104,128,84,55,120,58,141):
    v=out[str(fid)]; nm=F[fid]["tags"].get("name","") or ""
    if v["served"]: print(f"  #{fid:<4} SERVES {v['trail'][:30]:30} {v['dist_m']:>5} m {'(fallback)' if v['fallback'] else '':11} {nm[:22]}")
    else: print(f"  #{fid:<4} DROP   (nearest parking for no trail){'':40} {nm[:22]}")
