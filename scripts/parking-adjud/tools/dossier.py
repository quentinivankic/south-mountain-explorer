#!/usr/bin/env python3
"""Evidence dossier per parking facility — the data layer of the adjudication
protocol (plan: swift-napping-biscuit).

Reads parking geometry + FULL tags + real OSM ids straight from the parking-only
pbf (area handler for closed ways/relations, node handler for point lots — the
JSON cache strips ids/geometry/tags, verified). Clusters at 40 m keeping
PER-MEMBER tags. Re-runs the serves gate with POLYGON-EDGE distances. Joins the
foot-walk output and OSM context (trailhead nodes, footways, buildings). Emits:
  <slug>_dossier.json   {facilities:[{fid, osm:[...], lat, lon, members, tags_union,
                          mixed_access, ring, area_m2, prior, ...}], ...}
  <slug>_serves2.json   edge-distance serves gate result (same shape as _serves.json)
  membership diff printed vs the old centroid gate.

Usage: python3 dossier.py <slug>"""
import json, math, os, sys, glob, collections
import osmium, shapely.wkb
from shapely.geometry import Point, Polygon
from shapely.strtree import STRtree
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

TMP=PADJ_TMP
GEOMDIR=PADJ_GEOM
# PADJ_PARKING_PBF  amenity=parking extract. default: a region pbf in PADJ_TMP
#                   if present, else the national parking-only cache on /mnt/raid.
# PADJ_US           the extract per-area context is cut from.
PBF=_os.environ.get("PADJ_PARKING_PBF") or (
    f"{PADJ_TMP}/phx_parking.osm.pbf"
    if os.path.exists(f"{PADJ_TMP}/phx_parking.osm.pbf")
    else "/mnt/raid/trekdex/osm/cache/parking-only.osm.pbf")
US=_os.environ.get("PADJ_US") or "/mnt/raid/trekdex/osm/us-access.osm.pbf"
NEAR=805.0; MAXN=3; MAXF=2; FB_MAX=5000.0; CLUSTER=40.0; BUF=0.06
DESCRIPTIVE=("surface","parking","fee","capacity","name","operator")
def hav(a,b,c,d):
    R=6371000;p=math.radians;dl=p(c-a);dn=p(d-b)
    return 2*R*math.asin(math.sqrt(math.sin(dl/2)**2+math.cos(p(a))*math.cos(p(c))*math.sin(dn/2)**2))

class ParkH(osmium.SimpleHandler):
    """Collect amenity=parking: ways (with rings) AND nodes, full tags, real ids."""
    def __init__(s,bb):
        super().__init__(); s.bb=bb; s.lots=[]; s.wkb=osmium.geom.WKBFactory()
    def _in(s,la,lo):
        return s.bb[0]<=lo<=s.bb[2] and s.bb[1]<=la<=s.bb[3]
    def node(s,n):
        t={x.k:x.v for x in n.tags}
        if t.get("amenity")!="parking": return
        if not n.location.valid() or not s._in(n.location.lat,n.location.lon): return
        s.lots.append({"osm":f"node/{n.id}","lat":n.location.lat,"lon":n.location.lon,"tags":t,"ring":None})
    def area(s,a):
        t={x.k:x.v for x in a.tags}
        if t.get("amenity")!="parking": return
        try: g=shapely.wkb.loads(s.wkb.create_multipolygon(a),hex=True)
        except Exception: return
        c=g.centroid
        if not s._in(c.y,c.x): return
        p=max(g.geoms,key=lambda q:q.area) if g.geom_type=="MultiPolygon" else g
        ring=[[round(y,7),round(x,7)] for x,y in p.exterior.coords]  # [lat,lon]
        kind="way" if a.from_way() else "relation"
        s.lots.append({"osm":f"{kind}/{a.orig_id()}","lat":c.y,"lon":c.x,"tags":t,"ring":ring})

class CtxH(osmium.SimpleHandler):
    """Context: highway=trailhead nodes + footway/path ways + building areas in bbox."""
    def __init__(s,bb):
        super().__init__(); s.bb=bb; s.thn=[]; s.foot=[]; s.bld=[]; s.wkb=osmium.geom.WKBFactory()
    def _in(s,la,lo): return s.bb[0]<=lo<=s.bb[2] and s.bb[1]<=la<=s.bb[3]
    def node(s,n):
        t={x.k:x.v for x in n.tags}
        if t.get("highway")=="trailhead" and n.location.valid() and s._in(n.location.lat,n.location.lon):
            s.thn.append((n.location.lat,n.location.lon,t.get("name","")))
    def way(s,w):
        t={x.k:x.v for x in w.tags}
        if t.get("highway") in ("footway","path","steps"):
            pts=[(n.lat,n.lon) for n in w.nodes if n.location.valid()]
            if pts and s._in(*pts[0]): s.foot.append(pts)
    def area(s,a):
        t={x.k:x.v for x in a.tags}
        if "building" not in t: return
        try: g=shapely.wkb.loads(s.wkb.create_multipolygon(a),hex=True)
        except Exception: return
        c=g.centroid
        if s._in(c.y,c.x): s.bld.append(g)

def ring_poly(ring):
    try:
        p=Polygon([(lo,la) for la,lo in ring])
        return p if p.is_valid and p.area>0 else p.buffer(0)
    except Exception: return None
def edge_dist_m(la,lo,ring):
    """meters from point to polygon boundary (0 if inside)."""
    p=ring_poly(ring)
    if p is None: return None
    pt=Point(lo,la)
    if p.contains(pt): return 0.0
    q=p.exterior.interpolate(p.exterior.project(pt))
    return hav(la,lo,q.y,q.x)
def poly_area_m2(ring):
    p=ring_poly(ring)
    if p is None: return None
    la=sum(r[0] for r in ring)/len(ring)
    return abs(p.area)*(111320.0**2)*math.cos(math.radians(la))

def main(slug):
    g=json.load(open(f"{GEOMDIR}/{slug}.json")); b=g["bbox"]
    bb=(b[0]-BUF,b[1]-BUF,b[2]+BUF,b[3]+BUF)
    # bbox context extract once (65 s on us-access for Griffith, measured) — never
    # run handlers over the 3.5 GB national file directly.
    ctx_pbf=f"{TMP}/{slug}_ctx.osm.pbf"
    if not os.path.exists(ctx_pbf):
        import subprocess
        subprocess.run(["osmium","extract",f"--bbox={bb[0]},{bb[1]},{bb[2]},{bb[3]}",
                        "-o",ctx_pbf,"--overwrite",US],check=True)
    ph=ParkH(bb); ph.apply_file(PBF,locations=True)
    lots=ph.lots
    print(f"pbf lots in bbox: {len(lots)} ({sum(1 for l in lots if l['ring'])} with rings)")
    # cluster 40 m, per-member tags kept
    n=len(lots); par=list(range(n))
    def find(x):
        while par[x]!=x: par[x]=par[par[x]]; x=par[x]
        return x
    cell=lambda la,lo:(int(la*2000),int(lo*2000))
    grid=collections.defaultdict(list)
    for i,l in enumerate(lots): grid[cell(l["lat"],l["lon"])].append(i)
    for i,l in enumerate(lots):
        c=cell(l["lat"],l["lon"])
        for dx in(-1,0,1):
            for dy in(-1,0,1):
                for j in grid.get((c[0]+dx,c[1]+dy),[]):
                    if j>i and hav(l["lat"],l["lon"],lots[j]["lat"],lots[j]["lon"])<=CLUSTER: par[find(i)]=find(j)
    cl=collections.defaultdict(list)
    for i in range(n): cl[find(i)].append(i)
    facs=[]
    for fid,idxs in enumerate(sorted(cl.values(),key=lambda ix:min(ix))):
        mem=[lots[i] for i in idxs]
        la=sum(m["lat"] for m in mem)/len(mem); lo=sum(m["lon"] for m in mem)/len(mem)
        union={}
        for m in mem:
            for k,v in m["tags"].items(): union.setdefault(k,v)
        accs={m["tags"].get("access") for m in mem}
        rings=[m["ring"] for m in mem if m["ring"]]
        biggest=max(rings,key=lambda r:len(r)) if rings else None
        desc=[k for k in DESCRIPTIVE if union.get(k)]
        prior=("surveyed" if desc else "bare")
        facs.append({"fid":fid,"lat":round(la,7),"lon":round(lo,7),
                     "osm":[m["osm"] for m in mem],
                     "members":[{"osm":m["osm"],"tags":m["tags"]} for m in mem],
                     "tags_union":union,"mixed_access":len(accs-{None})>1,
                     "ring":biggest,"rings":rings if len(rings)>1 else None,
                     "area_m2":round(poly_area_m2(biggest)) if biggest else None,
                     "prior":prior,"descriptive":desc})
    print(f"facilities: {len(facs)}  surveyed-prior: {sum(1 for f in facs if f['prior']=='surveyed')}")
    # ---- serves gate, EDGE distances ----
    trails=[]
    RB=bb
    for fn in glob.glob(GEOMDIR+"/*.json"):
        try: d=json.load(open(fn))
        except Exception: continue
        tb=d.get("bbox")
        if not tb or tb[2]<RB[0] or tb[0]>RB[2] or tb[3]<RB[1] or tb[1]>RB[3]: continue
        for tr in d.get("trails",[]):
            nm=tr.get("name") or "(unnamed trail)"; ends=[]
            for s in tr["segments"]:
                if s:
                    if len(s[0])>=2: ends.append((s[0][0],s[0][1]))
                    if len(s[-1])>=2: ends.append((s[-1][0],s[-1][1]))
            if ends and any(RB[0]<=e[1]<=RB[2] and RB[1]<=e[0]<=RB[3] for e in ends):
                trails.append((nm,ends))
    print(f"trails in region: {len(trails)}")
    def fac_dist(f,e0,e1):
        d=hav(f["lat"],f["lon"],e0,e1)
        if f["ring"] and d<3000:            # edge refine only when it could matter
            ed=edge_dist_m(e0,e1,f["ring"])
            if ed is not None: return ed
        return d
    served={f["fid"]:[] for f in facs}
    for nm,ends in trails:
        scored=sorted((min(fac_dist(f,e0,e1) for e0,e1 in ends),f["fid"]) for f in facs)
        near=[x for x in scored if x[0]<=NEAR]; fb=not near
        shown = near[:MAXN] if near else [x for x in scored[:MAXF] if x[0]<=FB_MAX]
        for dm,fid in shown: served[fid].append((nm,round(dm),fb))
    srv={}
    for f in facs:
        s=served[f["fid"]]
        if s:
            best=min(s,key=lambda z:z[1])
            srv[str(f["fid"])]={"served":True,"trail":best[0],"dist_m":best[1],"fallback":best[2],"n":len(s)}
        else: srv[str(f["fid"])]={"served":False}
    ns=sum(1 for v in srv.values() if v["served"])
    print(f"EDGE gate: served {ns}/{len(facs)}")
    # ---- context joins ----
    ch=CtxH(bb); ch.apply_file(ctx_pbf,locations=True)
    print(f"context: trailhead nodes={len(ch.thn)} footways={len(ch.foot)} buildings={len(ch.bld)}")
    btree=STRtree(ch.bld) if ch.bld else None
    for f in facs:
        th=[(round(hav(f['lat'],f['lon'],a,b)),nm) for a,b,nm in ch.thn if hav(f['lat'],f['lon'],a,b)<=120]
        f["trailhead_nodes"]=sorted(th)[:3]
        fw=0
        for pts in ch.foot:
            if any(hav(f["lat"],f["lon"],a,b)<=60 for a,b in pts[::max(1,len(pts)//8)]): fw+=1
        f["footways_60m"]=fw
        if btree is not None and f["ring"]:
            p=ring_poly(f["ring"])
            f["building_overlap"]=bool(p) and any(p.intersects(ch.bld[i]) for i in btree.query(p))
        else: f["building_overlap"]=False
    # ---- walk join (if present) ----
    wf=f"{TMP}/zion_walk_our.json" if slug.startswith("zion") else f"{TMP}/{slug}_walk.json"
    walk=json.load(open(wf)) if os.path.exists(wf) else {}
    out={"slug":slug,"name":g.get("name",slug),"bbox":b,"facilities":facs}
    json.dump(out,open(f"{TMP}/{slug}_dossier.json","w"))
    json.dump(srv,open(f"{TMP}/{slug}_serves2.json","w"),indent=0)
    # ---- membership diff vs old centroid gate ----
    oldf=f"{TMP}/{slug}_serves.json" if os.path.exists(f"{TMP}/{slug}_serves.json") else (f"{TMP}/serves_rel.json" if slug.startswith("zion") else None)
    if oldf and os.path.exists(oldf):
        old=json.load(open(oldf))
        # old universe used different fids — diff by nearest-coord matching
        octx_f=f"{TMP}/{slug}_ctx.json" if os.path.exists(f"{TMP}/{slug}_ctx.json") else f"{TMP}/zion_ctx.json"
        octx=json.load(open(octx_f))
        oldpos={f["fid"]:(f["lat"],f["lon"]) for f in octx["facilities"]}
        oldserved={fid for fid,(la,lo) in oldpos.items() if old.get(str(fid),{}).get("served")}
        def near_old(f,S):
            return any(hav(f["lat"],f["lon"],*oldpos[fid])<60 for fid in S)
        added=[f for f in facs if srv[str(f["fid"])]["served"] and not near_old(f,oldserved)]
        dropped=[fid for fid in oldserved if not any(srv[str(f['fid'])]['served'] and hav(f['lat'],f['lon'],*oldpos[fid])<60 for f in facs)]
        print(f"membership diff vs centroid gate: +{len(added)} newly served, -{len(dropped)} no longer served")
        for f in added[:20]:
            s=srv[str(f["fid"])]
            print(f"  + fid{f['fid']} osm={f['osm'][0]} d={s['dist_m']} {s['trail'][:30]} tags={ {k:f['tags_union'][k] for k in f['descriptive']} }")
        for fid in dropped[:20]: print(f"  - old fid{fid} at {oldpos[fid]}")
    return 0
if __name__=="__main__": raise SystemExit(main(sys.argv[1]))
