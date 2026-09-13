#!/usr/bin/env python3
"""Render Z2 (220 m, 0.21 m/px) for every public-served lot in the given areas,
with a delay + retries to avoid ESRI throttling (the batch run only got z1).
Polygon drawn, trails yellow, other lots orange. Usage: python3 z2render.py"""
import io, json, math, os, time, glob, urllib.request, sys
from PIL import Image, ImageDraw, ImageFont
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
AREAS=["echo-canyon-recreation-area-az","phoenix-mountains-preserve-az","pinnacle-peak-park-az","usery-mountain-regional-park-az"]
def font(sz):
    try: return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",sz)
    except Exception: return ImageFont.load_default()
Fnt=font(18)
def region_trails(b):
    RB=(b[0]-0.06,b[1]-0.06,b[2]+0.06,b[3]+0.06); out=[]
    for fn in glob.glob(GEOMDIR+"/*.json"):
        try: dd=json.load(open(fn))
        except Exception: continue
        bb=dd.get("bbox")
        if not bb or bb[2]<RB[0] or bb[0]>RB[2] or bb[3]<RB[1] or bb[1]>RB[3]: continue
        for tr in dd.get("trails",[]):
            for s in tr["segments"]:
                if len(s)>=2: out.append([[v[1],v[0]] for v in s])
    return out
NAIP="https://imagery.nationalmap.gov/arcgis/rest/services/USGSNAIPPlus/ImageServer/exportImage?bbox={x0},{y0},{x1},{y1}&bboxSR=4326&imageSR=4326&size={PX},{PX}&format=png&f=image"
ESRI="https://services.arcgisonline.com/arcgis/rest/services/World_Imagery/MapServer/export?bbox={x0},{y0},{x1},{y1}&bboxSR=4326&imageSR=4326&size={PX},{PX}&format=png&f=image"
def fetch(x0,y0,x1,y1,PX):
    # NAIP primary (not throttling us); ESRI fallback. Returns (image, source).
    for src,tmpl in (("NAIP",NAIP),("ESRI",ESRI)):
        for back in (0,6,15):
            if back: time.sleep(back)
            try:
                im=Image.open(io.BytesIO(urllib.request.urlopen(urllib.request.Request(tmpl.format(x0=x0,y0=y0,x1=x1,y1=y1,PX=PX),headers={"User-Agent":"trekdex/1.0"}),timeout=40).read())).convert("RGB")
                return im,src
            except Exception: pass
    return None,None
half=110.0; PX=1024; n=0
for slug in AREAS:
    dos=json.load(open(f"{TMP}/{slug}_dossier.json")); srv=json.load(open(f"{TMP}/{slug}_serves2.json")); F={f["fid"]:f for f in dos["facilities"]}
    trails=region_trails(dos["bbox"]); lots=[(f["lat"],f["lon"]) for f in dos["facilities"]]
    pub=[int(x) for x in open(f"{TMP}/{slug}_pub.txt").read().split(",")]
    D=f"{TMP}/{slug}_ladder"
    for fid in pub:
        out=f"{D}/{fid:04d}_z2.png"
        if os.path.exists(out): continue
        f=F[fid]; la,lo=f["lat"],f["lon"]
        dlat=half/111320.0; dlon=half/(111320.0*math.cos(la*math.pi/180))
        x0,y0,x1,y1=lo-dlon,la-dlat,lo+dlon,la+dlat
        im,isrc=fetch(x0,y0,x1,y1,PX)
        if im is None: print("FAIL",slug,fid); continue
        dr=ImageDraw.Draw(im,"RGBA"); X=lambda v:(v-x0)/(x1-x0)*PX; Y=lambda v:(y1-v)/(y1-y0)*PX
        for t in trails:
            if len(t)>1 and any(x0<=p[0]<=x1 and y0<=p[1]<=y1 for p in t):
                pts=[(X(p[0]),Y(p[1])) for p in t]; dr.line(pts,fill=(10,10,10,170),width=8); dr.line(pts,fill=(255,230,0,255),width=4)
        for l in lots:
            if x0<=l[1]<=x1 and y0<=l[0]<=y1 and (abs(l[0]-la)>1e-6 or abs(l[1]-lo)>1e-6):
                dr.ellipse([X(l[1])-6,Y(l[0])-6,X(l[1])+6,Y(l[0])+6],outline=(255,150,0,255),width=3)
        rings=f.get("rings") or ([f["ring"]] if f.get("ring") else [])
        if rings:
            for ring in rings:
                pts=[(X(p[1]),Y(p[0])) for p in ring]
                if len(pts)>2: dr.line(pts+[pts[0]],fill=(255,30,30,255),width=5)
        else:
            r=20.0/(2*half)*PX; cx,cy=X(lo),Y(la); dr.ellipse([cx-r,cy-r,cx+r,cy+r],outline=(255,30,30,255),width=5)
        dr.rectangle([0,0,PX,32],fill=(0,0,0,170)); dr.text((8,7),f"#{fid} Z2 · 220 m · {isrc} · red=lot yellow=trails",fill=(255,235,120,255),font=Fnt)
        im.save(out); n+=1; time.sleep(1.0)
    print(f"{slug}: z2 done")
print(f"rendered {n} z2 tiles")
