#!/usr/bin/env python3
"""Zoom-ladder tile renderer — Z1 context / Z2 lot / Z3 confirm per candidate.

Every frame draws: the facility's OSM polygon outline(s) in red (or a 20 m red
circle for node-only lots), every shipped trail yellow with dark casing, other
lots orange. The old adaptive half=max(240,dist+150) framing is gone — it is the
measured cause of the #1596 class of false drops. NAIP variant fetch for Z3 on
demand (--naip fid).

Usage: python3 ladder_tiles.py <slug> <fid,fid,...|all-served>
Writes <slug>_ladder/<fid>_z1.png,_z2.png,_z3.png (skip-if-exists)."""
import io, json, math, os, sys, glob, urllib.request
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

TMP=PADJ_TMP
GEOMDIR=PADJ_GEOM
ESRI=("https://services.arcgisonline.com/arcgis/rest/services/World_Imagery/MapServer/export"
      "?bbox={x0},{y0},{x1},{y1}&bboxSR=4326&imageSR=4326&size={W},{H}&format=png&f=image")
NAIP=("https://imagery.nationalmap.gov/arcgis/rest/services/USGSNAIPPlus/ImageServer/exportImage"
      "?bbox={x0},{y0},{x1},{y1}&bboxSR=4326&imageSR=4326&size={W},{H}&format=png&f=image")
# Z-frames: (name, half-width m, px)
LADDER=[("z1",300.0,1024),("z2",110.0,1024),("z3",70.0,1024)]
def fetch(tmpl,x0,y0,x1,y1,W,H):
    url=tmpl.format(x0=x0,y0=y0,x1=x1,y1=y1,W=W,H=H)
    for _ in range(3):
        try: return Image.open(io.BytesIO(urllib.request.urlopen(urllib.request.Request(url,headers={"User-Agent":"trekdex/1.0"}),timeout=90).read())).convert("RGB")
        except Exception: pass
    return None
def font(sz):
    try: return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",sz)
    except Exception: return ImageFont.load_default()
def region_trails(slug,b):
    RB=(b[0]-0.06,b[1]-0.06,b[2]+0.06,b[3]+0.06); out=[]
    for fn in glob.glob(GEOMDIR+"/*.json"):
        try: dd=json.load(open(fn))
        except Exception: continue
        bb=dd.get("bbox")
        if not bb or bb[2]<RB[0] or bb[0]>RB[2] or bb[3]<RB[1] or bb[1]>RB[3]: continue
        for tr in dd.get("trails",[]):
            for s in tr["segments"]:
                if len(s)>=2: out.append([[v[1],v[0]] for v in s])  # [lon,lat]
    return out
def render(slug,fids,naip_fids=()):
    dos=json.load(open(f"{TMP}/{slug}_dossier.json")); srv=json.load(open(f"{TMP}/{slug}_serves2.json"))
    F={f["fid"]:f for f in dos["facilities"]}; b=dos["bbox"]
    trails=region_trails(slug,b)
    lots=[(f["lat"],f["lon"]) for f in dos["facilities"]]
    D=f"{TMP}/{slug}_ladder"; os.makedirs(D,exist_ok=True)
    Fnt=font(19); n=0
    for fid in fids:
        f=F[fid]; la,lo=f["lat"],f["lon"]; s=srv.get(str(fid)) or {}
        for zname,half,PX in LADDER:
            for src,tag in ((ESRI,""),) + (((NAIP,"_naip"),) if fid in naip_fids else ()):
                out=f"{D}/{fid:04d}_{zname}{tag}.png"
                if os.path.exists(out): continue
                dlat=half/111320.0; dlon=half/(111320.0*math.cos(la*math.pi/180))
                x0,y0,x1,y1=lo-dlon,la-dlat,lo+dlon,la+dlat
                im=fetch(src,x0,y0,x1,y1,PX,PX)
                if im is None: print(f"FETCH FAIL {out}"); continue
                dr=ImageDraw.Draw(im,"RGBA")
                X=lambda v:(v-x0)/(x1-x0)*PX; Y=lambda v:(y1-v)/(y1-y0)*PX
                for t in trails:
                    if len(t)>1 and any(x0<=p[0]<=x1 and y0<=p[1]<=y1 for p in t):
                        pts=[(X(p[0]),Y(p[1])) for p in t]
                        dr.line(pts,fill=(10,10,10,170),width=8); dr.line(pts,fill=(255,230,0,255),width=4)
                for l in lots:
                    if x0<=l[1]<=x1 and y0<=l[0]<=y1 and (abs(l[0]-la)>1e-6 or abs(l[1]-lo)>1e-6):
                        cx,cy=X(l[1]),Y(l[0]); dr.ellipse([cx-6,cy-6,cx+6,cy+6],outline=(255,150,0,255),width=3)
                rings=f.get("rings") or ([f["ring"]] if f.get("ring") else [])
                if rings:
                    for ring in rings:
                        pts=[(X(p[1]),Y(p[0])) for p in ring]
                        if len(pts)>2: dr.line(pts+[pts[0]],fill=(255,30,30,255),width=5)
                else:
                    r=20.0/(2*half)*PX; cx,cy=X(lo),Y(la)
                    dr.ellipse([cx-r,cy-r,cx+r,cy+r],outline=(255,30,30,255),width=5)
                mpp=2*half/PX
                hdr=f"#{fid} {zname.upper()}{tag} · {int(2*half)} m across ({mpp:.2f} m/px) · red = mapped lot · yellow = our trails"
                dr.rectangle([0,0,PX,32],fill=(0,0,0,170)); dr.text((8,7),hdr[:118],fill=(255,235,120,255),font=Fnt)
                im.save(out); n+=1
        print(f"fid{fid} done")
    print(f"rendered {n} frames into {D}")
def main():
    slug=sys.argv[1]; arg=sys.argv[2]
    naip=set(int(x) for x in sys.argv[3].split(",")) if len(sys.argv)>3 and sys.argv[3] else set()
    if arg=="all-served":
        srv=json.load(open(f"{TMP}/{slug}_serves2.json"))
        dos=json.load(open(f"{TMP}/{slug}_dossier.json")); F={f["fid"]:f for f in dos["facilities"]}
        fids=[int(k) for k,v in srv.items() if v.get("served")
              and F[int(k)]["tags_union"].get("access") not in ("private","no","customers")]
    else:
        fids=[int(x) for x in arg.split(",")]
    render(slug,sorted(fids),naip)
if __name__=="__main__": raise SystemExit(main())
