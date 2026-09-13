#!/usr/bin/env python3
"""Generic foot-network walk for any area: network = <slug>_ctx.osm.pbf (walkable
highways), sources = <slug>_dossier.json facilities, targets = our shipped trail
vertices from every geom overlapping the area bbox. Writes <slug>_walk.json and
joins walk_m into the dossier. Usage: python3 foot_route_area.py <slug>"""
import osmium, math, json, heapq, collections, glob, os, sys
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

TMP=PADJ_TMP; GEOMDIR=PADJ_GEOM
SKIP={"motorway","trunk","motorway_link","trunk_link","construction","proposed","raceway","escape"}
INFRA={"path","footway","steps","pedestrian","track","bridleway","cycleway"}
def hav(a,b,c,d):
    R=6371000;p=math.radians;dl=p(c-a);dn=p(d-b)
    return 2*R*math.asin(math.sqrt(math.sin(dl/2)**2+math.cos(p(a))*math.cos(p(c))*math.sin(dn/2)**2))
class G(osmium.SimpleHandler):
    def __init__(s): super().__init__(); s.coords={}; s.adj=collections.defaultdict(list)
    def way(s,w):
        t={x.k:x.v for x in w.tags}; hw=t.get("highway")
        if not hw or hw in SKIP: return
        if t.get("foot") in ("no","private"): return
        if t.get("access") in ("private","no") and t.get("foot") not in ("yes","designated","permissive"): return
        infra = hw in INFRA or t.get("foot") in ("yes","designated"); sw=t.get("sidewalk","")
        pts=[(n.ref,n.location.lat,n.location.lon) for n in w.nodes if n.location.valid()]
        for A,B in zip(pts,pts[1:]):
            L=hav(A[1],A[2],B[1],B[2]); meta=(infra,hw,sw)
            s.adj[A[0]].append((B[0],L,meta)); s.adj[B[0]].append((A[0],L,meta))
            s.coords[A[0]]=(A[1],A[2]); s.coords[B[0]]=(B[1],B[2])
def main(slug):
    dos=json.load(open(f"{TMP}/{slug}_dossier.json")); b=dos["bbox"]
    RBOX=(b[0]-0.06,b[1]-0.06,b[2]+0.06,b[3]+0.06)
    g=G(); g.apply_file(f"{TMP}/{slug}_ctx.osm.pbf",locations=True); coords=g.coords; adj=g.adj
    print(f"network: {len(coords)} nodes")
    def cell(la,lo): return (int(la*1000),int(lo*1000))
    grid=collections.defaultdict(list)
    for nid,(la,lo) in coords.items(): grid[cell(la,lo)].append(nid)
    def nearest(la,lo,maxm):
        c=cell(la,lo); best=None; bd=maxm
        for dx in(-1,0,1):
            for dy in(-1,0,1):
                for nid in grid.get((c[0]+dx,c[1]+dy),[]):
                    d=hav(la,lo,*coords[nid])
                    if d<bd: bd=d; best=nid
        return best,bd
    def bbox_hit(bb): return bool(bb) and not (bb[2]<RBOX[0]-0.02 or bb[0]>RBOX[2]+0.02 or bb[3]<RBOX[1]-0.02 or bb[1]>RBOX[3]+0.02)
    targets=[]
    for fn in glob.glob(GEOMDIR+"/*.json"):
        try: d=json.load(open(fn))
        except Exception: continue
        if not bbox_hit(d.get("bbox")): continue
        for tr in d.get("trails",[]):
            nm=tr.get("name") or "(unnamed)"
            for s in tr["segments"]:
                for v in s: targets.append((v[0],v[1],nm))
    trailnodes=set(); nodename={}
    for la,lo,nm in targets:
        nid,dd=nearest(la,lo,30)
        if nid is not None: trailnodes.add(nid); nodename[nid]=nm
    print(f"trail vertices {len(targets)} -> snapped {len(trailnodes)}")
    INF=float("inf"); dist={}; h=[]
    for tn in trailnodes: dist[tn]=0.0; h.append((0.0,tn))
    heapq.heapify(h)
    while h:
        dd,u=heapq.heappop(h)
        if dd>dist.get(u,INF): continue
        for v,L,meta in adj[u]:
            nd=dd+L
            if nd<dist.get(v,INF): dist[v]=nd; heapq.heappush(h,(nd,v))
    out={}
    for f in dos["facilities"]:
        nid,snap=nearest(f["lat"],f["lon"],90)
        walk=dist.get(nid) if nid is not None else None
        if nid is None or walk is None or walk==INF:
            out[str(f["fid"])]={"walk_m":None,"conn":"no route","trail":None}
        else:
            out[str(f["fid"])]={"walk_m":round(walk),"conn":"","trail":nodename.get(nid)}
        f["walk"]=out[str(f["fid"])]
    json.dump(out,open(f"{TMP}/{slug}_walk.json","w"))
    json.dump(dos,open(f"{TMP}/{slug}_dossier.json","w"))
    n=sum(1 for v in out.values() if v["walk_m"] is not None)
    print(f"walk joined into dossier: {n}/{len(out)} routed")
if __name__=="__main__": raise SystemExit(main(sys.argv[1]))
