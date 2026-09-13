#!/usr/bin/env python3
"""Derive WHAT a parking lot is next to, from OSM tags (not the aerial).

The lesson from the user: "next to a building" is not a drop signal — a visitor
center is a building too. So classify the lot's neighbourhood by the OSM
type of the place it sits in / beside:
  SUPPORT  = trailhead-supporting (visitor centre, ranger, picnic, toilets, park,
             nature reserve, protected area) -> corroborates KEEP / dual-use
  FACILITY = a non-trail draw the lot plainly serves (church, resort/hotel,
             golf/equestrian/sports venue, school, hospital, commercial/office,
             apartments/residential) -> corroborates DROP
  NEUTRAL  = nothing classifying nearby -> vision/other signals decide

For each lot: the smallest landuse/leisure/amenity AREA that contains it (the
ground it sits on), plus the nearest classifying POI within 120 m. Emits
<slug>_context.json {fid:{category, in_area, nearest_poi, evidence}}.
Usage: python3 context_classify.py"""
import json, math, glob, os, sys, collections
import osmium, shapely.wkb
from shapely.geometry import Point
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
# Extracts live on /mnt/raid (19 TB) — never the near-full root disk.
RAID=_os.environ.get("PADJ_OSM") or "/mnt/raid/trekdex/parking-adjud/osm"
PHX_PBF=f"{RAID}/phx_metro.osm.pbf" if os.path.exists(f"{RAID}/phx_metro.osm.pbf") else f"{TMP}/phx_metro.osm.pbf"
PHX_AREAS=["echo-canyon-recreation-area-az","phoenix-mountains-preserve-az","pinnacle-peak-park-az","usery-mountain-regional-park-az"]
def hav(a,b,c,d):
    R=6371000;p=math.radians;dl=p(c-a);dn=p(d-b)
    return 2*R*math.asin(math.sqrt(math.sin(dl/2)**2+math.cos(p(a))*math.cos(p(c))*math.sin(dn/2)**2))

# classification of a tag dict -> (category, label) or None
def classify(t):
    am=t.get("amenity"); le=t.get("leisure"); to=t.get("tourism"); lu=t.get("landuse")
    bd=t.get("boundary"); sp=t.get("sport"); bl=t.get("building"); sh=t.get("shop"); of=t.get("office")
    # SUPPORT — trailhead facilities / protected land
    if to in ("information","picnic_site","camp_site","caravan_site","wilderness_hut"): return ("SUPPORT",f"tourism={to}")
    if t.get("information"): return ("SUPPORT",f"information={t.get('information')}")
    if am in ("ranger_station","toilets","drinking_water","shelter","bbq"): return ("SUPPORT",f"amenity={am}")
    if le in ("park","nature_reserve","picnic_table","firepit","bird_hide"): return ("SUPPORT",f"leisure={le}")
    if bd=="protected_area" or bd=="national_park": return ("SUPPORT",f"boundary={bd}")
    if t.get("historic")=="monument" or t.get("man_made")=="cairn": return ("SUPPORT",f"landmark")
    # FACILITY — a non-trail draw
    if am=="place_of_worship": return ("FACILITY",f"amenity=place_of_worship{(' '+t['name']) if t.get('name') else ''}")
    if to in ("hotel","motel","resort","apartment","hostel"): return ("FACILITY",f"tourism={to}")
    if le in ("golf_course","sports_centre","stadium","horse_riding","ice_rink","water_park","fitness_centre","pitch","track"): return ("FACILITY",f"leisure={le}")
    if sp in ("equestrian","golf","baseball","soccer","american_football","tennis"): return ("FACILITY",f"sport={sp}")
    if am in ("school","college","university","kindergarten","hospital","clinic","fire_station","police","restaurant","cafe","fast_food","fuel","bar","pub","casino","cinema","theatre","marketplace","bank","community_centre","place_of_worship","car_wash","childcare"): return ("FACILITY",f"amenity={am}")
    if sh: return ("FACILITY",f"shop={sh}")
    if of: return ("FACILITY",f"office={of}")
    if bl in ("apartments","commercial","retail","office","industrial","warehouse","hotel","church","school","hospital","supermarket","residential","house","detached"): return ("FACILITY",f"building={bl}")
    if lu in ("residential","commercial","retail","industrial"): return ("FACILITY",f"landuse={lu}")
    return None

class H(osmium.SimpleHandler):
    def __init__(s):
        super().__init__(); s.areas=[]; s.pts=[]; s.wkb=osmium.geom.WKBFactory()
    def node(s,n):
        t={x.k:x.v for x in n.tags}; c=classify(t)
        if c and n.location.valid(): s.pts.append((n.location.lat,n.location.lon,c[0],c[1],t.get("name","")))
    def area(s,a):
        t={x.k:x.v for x in a.tags}; c=classify(t)
        if not c: return
        try: g=shapely.wkb.loads(s.wkb.create_multipolygon(a),hex=True)
        except Exception: return
        s.areas.append((g,c[0],c[1],t.get("name",""),g.area))

def run(pbf,slugs):
    h=H(); h.apply_file(pbf,locations=True)
    print(f"[{os.path.basename(pbf)}] classifying features: {len(h.areas)} areas, {len(h.pts)} points")
    # Split areas: FACILITY (adjacency matters), STRONG support (nature_reserve/
    # protected_area/tourism — containment matters), and WEAK generic parks
    # (leisure=park — too coarse to be support; a park contains pitches & clubhouses).
    FAC=[a for a in h.areas if a[1]=="FACILITY" and not any(x in a[2] for x in ("building=house","building=detached","building=residential"))]
    fgeoms=[a[0] for a in FAC]; ftree=STRtree(fgeoms) if fgeoms else None
    SUP=[a for a in h.areas if a[1]=="SUPPORT" and not a[2].startswith("leisure=park")]
    sgeoms=[a[0] for a in SUP]; stree=STRtree(sgeoms) if sgeoms else None
    PARK=[a for a in h.areas if a[2].startswith("leisure=park")]
    pgeoms=[a[0] for a in PARK]; ptree=STRtree(pgeoms) if pgeoms else None
    cell=lambda la,lo:(int(la*1000),int(lo*1000)); grid=collections.defaultdict(list)
    for i,(la,lo,cat,lab,nm) in enumerate(h.pts): grid[cell(la,lo)].append(i)
    def m2deg(m): return m/111320.0
    for slug in slugs:
        dos=json.load(open(f"{TMP}/{slug}_dossier.json")); out={}
        for f in dos["facilities"]:
            la,lo=f["lat"],f["lon"]; pt=Point(lo,la)
            # nearest FACILITY area by EDGE distance (adjacency, not just containment)
            fac=None
            if ftree is not None:
                for j in ftree.query(pt.buffer(m2deg(70))):
                    d=pt.distance(fgeoms[int(j)])*111320.0
                    if fac is None or d<fac[0]: fac=(d,FAC[int(j)])
            # smallest STRONG-support area containing the lot
            sup=None
            if stree is not None:
                for j in stree.query(pt):
                    if sgeoms[int(j)].covers(pt) and (sup is None or SUP[int(j)][4]<sup[4]): sup=SUP[int(j)]
            # generic park containing the lot (weak)
            park=None
            if ptree is not None:
                for j in ptree.query(pt):
                    if pgeoms[int(j)].covers(pt): park=PARK[int(j)]; break
            # nearest classifying POI within 120 m, split by category
            fpoi=spoi=None; c=cell(la,lo)
            for dx in(-1,0,1):
                for dy in(-1,0,1):
                    for i in grid.get((c[0]+dx,c[1]+dy),[]):
                        pla,plo,pcat,plab,pnm=h.pts[i]; d=hav(la,lo,pla,plo)
                        if d>120: continue
                        if pcat=="FACILITY" and (fpoi is None or d<fpoi[3]): fpoi=(pcat,plab,pnm,round(d))
                        if pcat=="SUPPORT" and (spoi is None or d<spoi[3]): spoi=(pcat,plab,pnm,round(d))
            fac_m=round(fac[0]) if fac else None
            # priority: adjacent facility ≤45 m > strong support containing > facility POI
            #           > generic park (weak) > support POI > neutral
            if fac and fac[0]<=45:
                g,cat,lab,nm,ar=fac[1]; category="FACILITY"; ev=f"{fac_m} m from {lab}"+(f" '{nm}'" if nm else "")
            elif sup is not None:
                g,cat,lab,nm,ar=sup; category="SUPPORT"; ev=f"in {lab}"+(f" '{nm}'" if nm else "")
            elif fpoi is not None:
                category="FACILITY"; ev=f"{fpoi[3]} m from {fpoi[1]}"+(f" '{fpoi[2]}'" if fpoi[2] else "")
            elif fac and fac[0]<=90:
                g,cat,lab,nm,ar=fac[1]; category="FACILITY"; ev=f"{fac_m} m from {lab}"+(f" '{nm}'" if nm else "")
            elif park is not None:
                category="PARK"; ev=f"in leisure=park"+(f" '{park[3]}'" if park[3] else "")+" — generic park, weak"
            elif spoi is not None:
                category="SUPPORT"; ev=f"{spoi[3]} m from {spoi[1]}"+(f" '{spoi[2]}'" if spoi[2] else "")
            else:
                category="NEUTRAL"; ev=""
            out[str(f["fid"])]={"category":category,"evidence":ev,"fac_area_m":fac_m,
                                "fac_label":(fac[1][2] if fac else None)}
        json.dump(out,open(f"{TMP}/{slug}_context.json","w"),indent=0)
        cc=collections.Counter(v["category"] for v in out.values())
        print(f"{slug}: {dict(cc)}")
def main():
    if len(sys.argv)>=3:              # single area: <slug> <pbf>
        run(sys.argv[2],[sys.argv[1]])
    else:                             # default: the phoenix batch
        run(PHX_PBF,PHX_AREAS)
    return 0
if __name__=="__main__": raise SystemExit(main())
