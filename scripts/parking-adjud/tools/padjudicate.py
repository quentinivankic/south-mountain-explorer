#!/usr/bin/env python3
"""Area-agnostic parking adjudication — the generalized seed of the Zion harness.

Given an area SLUG, build every parking facility around it and the SERVES test
(the app's nearestParkingWithFallback + the fallback cap learned on Zion), run
area-agnostically against every trail we ship in the region. Emits <slug>_ctx.json
and <slug>_serves.json with the same shape the Zion artifact/tile scripts consume,
so the logic can be validated on a FRESH area (is it Zion-overfit?).

Usage: python3 padjudicate.py <slug>
Core logic is data-only (no foot-routing needed): serves_rel + fallback cap +
access tag. Vision (real-lot vs bleed) is the separate tile step."""
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
import json, math, os, sys, importlib.util, collections
sys.path.insert(0,_os.path.join(_ROOT,"scripts"))
_s=importlib.util.spec_from_file_location("ap",_os.path.join(_ROOT,"scripts","add-parking.py"))
ap=importlib.util.module_from_spec(_s); _s.loader.exec_module(ap)
from _local_osm import LocalOSM

TMP=PADJ_TMP
GEOMDIR=PADJ_GEOM
NEAR=805.0; MAXN=3; MAXF=2; FB_MAX=5000.0; CLUSTER=40.0; BUF=0.06
KEEP=("name","access","operator","fee","capacity","parking","park_ride","highway","surface","amenity")
def hav(a,b,c,d): return ap.haversine_m(a,b,c,d)

def region_trails(bbox):
    """(name, endpoints, verts) for every shipped trail whose bbox overlaps."""
    RB=(bbox[0]-BUF,bbox[1]-BUF,bbox[2]+BUF,bbox[3]+BUF); out=[]; areas=[]
    for fn in glob_geom():
        try: d=json.load(open(fn))
        except Exception: continue
        bb=d.get("bbox")
        if not bb or bb[2]<RB[0] or bb[0]>RB[2] or bb[3]<RB[1] or bb[1]>RB[3]: continue
        areas.append(os.path.basename(fn).replace(".json",""))
        for tr in d.get("trails",[]):
            nm=tr.get("name") or "(unnamed trail)"; ends=[]; verts=[]
            for s in tr["segments"]:
                if s:
                    if len(s[0])>=2: ends.append((s[0][0],s[0][1]))
                    if len(s[-1])>=2: ends.append((s[-1][0],s[-1][1]))
                    for v in s: verts.append((v[0],v[1]))
            if ends and any(RB[0]<=e[1]<=RB[2] and RB[1]<=e[0]<=RB[3] for e in ends):
                out.append((nm,ends,verts))
    return out,sorted(set(areas))
def glob_geom():
    import glob; return glob.glob(GEOMDIR+"/*.json")

def main(slug):
    g=json.load(open(f"{GEOMDIR}/{slug}.json")); b=g["bbox"]
    loc=LocalOSM()
    raw=[]
    for el in loc.parking_elements([b[0]-BUF,b[1]-BUF,b[2]+BUF,b[3]+BUF]).get("elements",[]):
        t=el.get("tags",{})
        if t.get("amenity")!="parking": continue
        lat,lon=ap._point(el)
        if lat is None: continue
        raw.append({"lat":lat,"lon":lon,"tags":{k:t[k] for k in KEEP if k in t}})
    # cluster ~40 m -> facilities
    par=list(range(len(raw)))
    def find(x):
        while par[x]!=x: par[x]=par[par[x]]; x=par[x]
        return x
    for i in range(len(raw)):
        for j in range(i+1,len(raw)):
            if hav(raw[i]["lat"],raw[i]["lon"],raw[j]["lat"],raw[j]["lon"])<=CLUSTER: par[find(i)]=find(j)
    cl=collections.defaultdict(list)
    for i in range(len(raw)): cl[find(i)].append(i)
    facs=[]
    for fid,idxs in enumerate(cl.values()):
        mem=[raw[i] for i in idxs]
        la=sum(m["lat"] for m in mem)/len(mem); lo=sum(m["lon"] for m in mem)/len(mem)
        mt={}
        for m in mem:
            for k,v in m["tags"].items(): mt.setdefault(k,v)
        facs.append({"fid":fid,"lat":la,"lon":lo,"n_lots":len(mem),"tags":mt})
    trails,areas=region_trails(b)
    # SERVES (app rule + fallback cap), area-agnostic
    served={f["fid"]:[] for f in facs}
    for nm,ends,_ in trails:
        scored=sorted((min(hav(f["lat"],f["lon"],e0,e1) for e0,e1 in ends),f["fid"]) for f in facs)
        near=[x for x in scored if x[0]<=NEAR]; fb=not near
        shown = near[:MAXN] if near else [x for x in scored[:MAXF] if x[0]<=FB_MAX]
        for dm,fid in shown: served[fid].append((nm,round(dm),fb))
    srv={}
    for f in facs:
        s=served[f["fid"]]
        if s:
            best=min(s,key=lambda z:z[1]); srv[str(f["fid"])]={"served":True,"trail":best[0],"dist_m":best[1],"fallback":best[2],"n":len(s)}
        else: srv[str(f["fid"])]={"served":False}
    # trails for drawing: [lon,lat] polylines
    draw=[]
    for fn in glob_geom():
        try: d=json.load(open(fn))
        except Exception: continue
        bb=d.get("bbox"); RB=(b[0]-BUF,b[1]-BUF,b[2]+BUF,b[3]+BUF)
        if not bb or bb[2]<RB[0] or bb[0]>RB[2] or bb[3]<RB[1] or bb[1]>RB[3]: continue
        for tr in d.get("trails",[]):
            for s in tr["segments"]:
                if len(s)>=2 and any(RB[0]<=v[1]<=RB[2] and RB[1]<=v[0]<=RB[3] for v in s):
                    draw.append([[v[1],v[0]] for v in s])
    ctx={"slug":slug,"name":g.get("name",slug),"bbox":b,"trails":draw,"facilities":facs}
    json.dump(ctx,open(f"{TMP}/{slug}_ctx.json","w"))
    json.dump(srv,open(f"{TMP}/{slug}_serves.json","w"),indent=0)
    ns=sum(1 for v in srv.values() if v["served"]); nfb=sum(1 for v in srv.values() if v.get("fallback"))
    npriv=sum(1 for f in facs if f["tags"].get("access") in ("private","no","customers"))
    print(f"slug={slug}  facilities={len(facs)}  trails(region)={len(trails)}  areas={len(areas)}")
    print(f"  served={ns}  via-fallback={nfb}  private/customers={npriv}")
    # show served, sorted by distance
    NAME={f["fid"]:(f["tags"].get("name","")or"") for f in facs}
    ss=sorted(((v['dist_m'],int(k),v['fallback']) for k,v in srv.items() if v['served']))
    print(f"  {'d':>6} {'fid':>4} fb  name")
    for d,fid,fb in ss:
        print(f"  {d:>6} {fid:>4}  {int(bool(fb))}  {NAME[fid][:44]}")
    return 0
if __name__=="__main__": raise SystemExit(main(sys.argv[1]))
