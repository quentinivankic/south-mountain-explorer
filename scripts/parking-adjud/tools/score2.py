#!/usr/bin/env python3
"""Score protocol verdicts against groundtruth.json (confidence-tiered).

Bar (from the plan): ZERO confident-wrong — no KEEP on a certain/strong DROP row,
no DROP on a certain/strong KEEP row. `leaning` rows may land REVIEW freely.
REVIEW-rate reported against the ~12%-urban band. Matches groundtruth rows to
current facilities by coordinate (fids are run-local).

Usage: python3 score2.py <area:zion|griffith> <verdicts.json> <dossier.json>"""
import json, math, sys
# --- portable paths (added when these tools were graduated into the repo) -----
# PADJ_TMP holds groundtruth.json. Default: the sibling data/ directory.
import os as _os
_HERE = _os.path.dirname(_os.path.abspath(__file__))
PADJ_TMP = _os.environ.get("PADJ_TMP") or _os.path.join(_HERE, "..", "data")
GROUNDTRUTH = _os.path.join(PADJ_TMP, "groundtruth.json")
# -----------------------------------------------------------------------------
def hav(a,b,c,d):
    R=6371000;p=math.radians;dl=p(c-a);dn=p(d-b)
    return 2*R*math.asin(math.sqrt(math.sin(dl/2)**2+math.cos(p(a))*math.cos(p(c))*math.sin(dn/2)**2))
def main(area,vf,df):
    G=json.load(open(GROUNDTRUTH))[area]
    V=json.load(open(vf)); dos=json.load(open(df))
    facs=dos["facilities"]
    def near_fac(la,lo):
        best=None;bd=60
        for f in facs:
            d=hav(la,lo,f["lat"],f["lon"])
            if d<bd: bd=d; best=f
        return best
    cw=[]; ok=0; rev=0; miss=[]
    for key,row in sorted(G.items()):
        f=near_fac(row["lat"],row["lon"])
        if f is None: miss.append(key); continue
        v=V.get(str(f["fid"]))
        got=(v or {}).get("verdict","(unjudged)")
        want=row["want"]
        if got=="REVIEW": rev+=1
        if want in ("KEEP","DROP") and got in ("KEEP","DROP") and got!=want and row["confidence"] in ("certain","strong"):
            cw.append((key,f["fid"],want,got,row["confidence"],(v or {}).get("exists",{}).get("evidence","")))
        elif got==want: ok+=1
    print(f"{area}: rows={len(G)} matched-agree={ok} review={rev} unmatched={miss}")
    print(f"CONFIDENT-WRONG: {len(cw)}  <-- must be 0")
    for key,fid,want,got,conf,ev in cw:
        print(f"  {key} (fid{fid}) want {want} got {got} [{conf}] {ev[:70]}")
    total=len(V); revall=sum(1 for v in V.values() if v.get("verdict")=="REVIEW")
    print(f"verdicts total={total} review-rate={revall}/{total}")
    return 1 if cw else 0
if __name__=="__main__": raise SystemExit(main(sys.argv[1],sys.argv[2],sys.argv[3]))
