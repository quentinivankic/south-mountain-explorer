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
import json, math, os, sys, collections
import osmium, shapely.wkb
from shapely.geometry import Point, Polygon
from shapely.strtree import STRtree
import dossier_output
import geom_source
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
        super().__init__(); s.bb=bb; s.lots=[]; s.errors=[]; s.wkb=osmium.geom.WKBFactory()
    def _in(s,la,lo):
        return s.bb[0]<=lo<=s.bb[2] and s.bb[1]<=la<=s.bb[3]
    def _identity(s,a):
        try: kind="way" if a.from_way() else "relation"
        except Exception: kind="area"
        try: ident=a.orig_id()
        except Exception:
            try: ident=a.id
            except Exception: ident="unknown"
        return f"{kind}/{ident}"
    def _error(s,identity,reason):
        s.errors.append((identity,reason))
    def raise_for_errors(s):
        if not s.errors: return
        details="; ".join(f"{identity}: {reason}" for identity,reason in sorted(s.errors))
        raise ValueError(f"parking area geometry failures: {details}")
    def node(s,n):
        t={x.k:x.v for x in n.tags}
        if t.get("amenity")!="parking": return
        if not n.location.valid() or not s._in(n.location.lat,n.location.lon): return
        s.lots.append({"osm":f"node/{n.id}","lat":n.location.lat,"lon":n.location.lon,"tags":t,"ring":None})
    def area(s,a):
        t={x.k:x.v for x in a.tags}
        if t.get("amenity")!="parking": return
        identity=s._identity(a)
        try: encoded=s.wkb.create_multipolygon(a)
        except Exception:
            s._error(identity,"WKB multipolygon conversion failed"); return
        try: g=shapely.wkb.loads(encoded,hex=True)
        except Exception:
            s._error(identity,"WKB multipolygon decode failed"); return
        try:
            if g is None or g.is_empty:
                s._error(identity,"multipolygon is empty"); return
            geom_type=g.geom_type
        except Exception:
            s._error(identity,"multipolygon inspection failed"); return
        if geom_type=="Polygon": polygons=[g]
        elif geom_type=="MultiPolygon":
            try: polygons=list(g.geoms)
            except Exception:
                s._error(identity,"multipolygon components are unreadable"); return
            if not polygons:
                s._error(identity,"multipolygon has no polygon components"); return
        else:
            s._error(identity,"WKB geometry is not a polygon or multipolygon"); return
        exteriors=[]
        for p in polygons:
            try:
                if p is None or p.is_empty or p.geom_type!="Polygon": raise ValueError
                area=float(p.area)
                exterior=p.exterior
                if exterior is None or exterior.is_empty: raise ValueError
                coords=list(exterior.coords)
            except Exception:
                s._error(identity,"polygon exterior is missing or unreadable"); return
            if not math.isfinite(area) or len(coords)<4:
                s._error(identity,"polygon exterior is empty or nonfinite"); return
            normalized=[]
            for coordinate in coords:
                try: x=float(coordinate[0]); y=float(coordinate[1])
                except (TypeError,ValueError,IndexError):
                    s._error(identity,"polygon exterior coordinate is malformed"); return
                if (not math.isfinite(x) or not math.isfinite(y)
                        or not -180<=x<=180 or not -90<=y<=90):
                    s._error(identity,"polygon exterior coordinate is nonfinite or out of range"); return
                normalized.append((x,y))
            exteriors.append((area,normalized))
        try:
            c=g.centroid
            if c is None or c.is_empty: raise ValueError
            cx=float(c.x); cy=float(c.y)
        except Exception:
            s._error(identity,"parking centroid is missing or unreadable"); return
        if (not math.isfinite(cx) or not math.isfinite(cy)
                or not -180<=cx<=180 or not -90<=cy<=90):
            s._error(identity,"parking centroid is nonfinite or out of range"); return
        _area,coords=max(exteriors,key=lambda item:item[0])
        if not s._in(cy,cx): return
        ring=[[round(y,7),round(x,7)] for x,y in coords]  # [lat,lon]
        s.lots.append({"osm":identity,"lat":cy,"lon":cx,"tags":t,"ring":ring})

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

def _write_generated_outputs(tmp, slug, dossier, serves):
    """Atomically publish generated files under the shared dossier-set lock."""
    dossier_output.write_generated_outputs(tmp, slug, dossier, serves)


def main(slug):
    slug=dossier_output.canonical_area(slug)
    geom_inventory=geom_source.load_geom_inventory(GEOMDIR)
    area_geom=geom_source.document_for_slug(geom_inventory,slug)
    g=area_geom.document; b=g["bbox"]
    bb=(b[0]-BUF,b[1]-BUF,b[2]+BUF,b[3]+BUF)
    relevant_geoms=geom_source.relevant_trail_documents(geom_inventory,bb)
    # bbox context extract once (65 s on us-access for Griffith, measured) — never
    # run handlers over the 3.5 GB national file directly.
    ctx_pbf=f"{TMP}/{slug}_ctx.osm.pbf"
    if not os.path.exists(ctx_pbf):
        import subprocess
        subprocess.run(["osmium","extract",f"--bbox={bb[0]},{bb[1]},{bb[2]},{bb[3]}",
                        "-o",ctx_pbf,"--overwrite",US],check=True)
    ph=ParkH(bb); ph.apply_file(PBF,locations=True); ph.raise_for_errors()
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
    for entry in relevant_geoms:
        d=entry.document
        for tr in d["trails"]:
            nm=tr.get("name") or "(unnamed trail)"; ends=[]
            for segment in tr["segments"]:
                if segment:
                    ends.append((segment[0][0],segment[0][1]))
                    ends.append((segment[-1][0],segment[-1][1]))
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
    out={"slug":slug,"name":g.get("name",slug),"bbox":b,"facilities":facs}
    _write_generated_outputs(TMP, slug, out, srv)
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
