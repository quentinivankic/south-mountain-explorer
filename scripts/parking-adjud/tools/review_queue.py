#!/usr/bin/env python3
"""Multi-area REVIEW-ONLY artifact — the human-adjudication queue.

Scans the given areas' verdict files for verdict==REVIEW, renders a WIDE context
frame (600 m) + a DETAIL frame (220 m) per lot via NAIP, and lays them out grouped
by area with the OSM facts (tags, what it's next to, trail + walk) and what would
resolve each. Same visual design as padjart2. Usage: python3 review_queue.py"""
import base64, io, json, math, os, html as _h, collections, glob, time, urllib.request
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
# (slug, display, verdict-source): "fid" = <slug>_verdicts2.json; "osm" = phx_verdicts_osm mapped
SOURCES=[
 ("zion-wilderness-ut","Zion Wilderness · Utah","fid"),
 ("griffith-park-ca","Griffith Park · Los Angeles","fid"),
 ("echo-canyon-recreation-area-az","Camelback / Echo Canyon · Phoenix","osm"),
 ("phoenix-mountains-preserve-az","Phoenix Mountains Preserve","osm"),
 ("pinnacle-peak-park-az","Pinnacle Peak Park · Scottsdale","osm"),
 ("usery-mountain-regional-park-az","Usery Mountain · Maricopa County","osm"),
]
NAIP="https://imagery.nationalmap.gov/arcgis/rest/services/USGSNAIPPlus/ImageServer/exportImage?bbox={x0},{y0},{x1},{y1}&bboxSR=4326&imageSR=4326&size={PX},{PX}&format=png&f=image"
ESRI="https://services.arcgisonline.com/arcgis/rest/services/World_Imagery/MapServer/export?bbox={x0},{y0},{x1},{y1}&bboxSR=4326&imageSR=4326&size={PX},{PX}&format=png&f=image"
def fetch(x0,y0,x1,y1,PX):
    for tmpl in (NAIP,ESRI):
        for back in (0,6,15):
            if back: time.sleep(back)
            try: return Image.open(io.BytesIO(urllib.request.urlopen(urllib.request.Request(tmpl.format(x0=x0,y0=y0,x1=x1,y1=y1,PX=PX),headers={"User-Agent":"trekdex/1.0"}),timeout=40).read())).convert("RGB")
            except Exception: pass
    return None
def font(sz):
    try: return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",sz)
    except Exception: return ImageFont.load_default()
Fnt=font(17)
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
def tile(f,trails,lots,half,label):
    la,lo=f["lat"],f["lon"]; PX=900
    dlat=half/111320.0; dlon=half/(111320.0*math.cos(la*math.pi/180))
    x0,y0,x1,y1=lo-dlon,la-dlat,lo+dlon,la+dlat
    im=fetch(x0,y0,x1,y1,PX)
    if im is None: return None
    dr=ImageDraw.Draw(im,"RGBA"); X=lambda v:(v-x0)/(x1-x0)*PX; Y=lambda v:(y1-v)/(y1-y0)*PX
    for t in trails:
        if len(t)>1 and any(x0<=p[0]<=x1 and y0<=p[1]<=y1 for p in t):
            pts=[(X(p[0]),Y(p[1])) for p in t]; dr.line(pts,fill=(10,10,10,170),width=7); dr.line(pts,fill=(255,230,0,255),width=4)
    for l in lots:
        if x0<=l[1]<=x1 and y0<=l[0]<=y1 and (abs(l[0]-la)>1e-6 or abs(l[1]-lo)>1e-6):
            dr.ellipse([X(l[1])-5,Y(l[0])-5,X(l[1])+5,Y(l[0])+5],outline=(255,150,0,255),width=3)
    rings=f.get("rings") or ([f["ring"]] if f.get("ring") else [])
    if rings:
        for ring in rings:
            pts=[(X(p[1]),Y(p[0])) for p in ring]
            if len(pts)>2: dr.line(pts+[pts[0]],fill=(255,30,30,255),width=5)
    else:
        r=20.0/(2*half)*PX; cx,cy=X(lo),Y(la); dr.ellipse([cx-r,cy-r,cx+r,cy+r],outline=(255,30,30,255),width=5)
    dr.rectangle([0,0,PX,30],fill=(0,0,0,170)); dr.text((7,6),label,fill=(255,235,120,255),font=Fnt)
    im.thumbnail((520,520)); rb=io.BytesIO(); im.save(rb,"JPEG",quality=72,optimize=True)
    return "data:image/jpeg;base64,"+base64.b64encode(rb.getvalue()).decode()
STRONG_FAC=("place_of_worship","hotel","resort","motel","golf_course","pitch","horse_riding","sports_centre","stadium","school","college","university","hospital","apartments","commercial","retail","industrial","office","supermarket","fuel","shop=")
def collect():
    items=[]  # (kind, slug, disp, fac, verdict, serves, context)  kind in review|flag
    for slug,disp,kind in SOURCES:
        if not os.path.exists(f"{TMP}/{slug}_dossier.json"): continue
        dos=json.load(open(f"{TMP}/{slug}_dossier.json")); srv=json.load(open(f"{TMP}/{slug}_serves2.json"))
        ctx=json.load(open(f"{TMP}/{slug}_context.json")) if os.path.exists(f"{TMP}/{slug}_context.json") else {}
        F={f["fid"]:f for f in dos["facilities"]}
        def vof(f):
            if kind=="fid":
                V=json.load(open(f"{TMP}/{slug}_verdicts2.json")); return V.get(str(f["fid"]))
            OV=json.load(open(f"{TMP}/phx_verdicts_osm.json")); return OV.get(f["osm"][0])
        for f in dos["facilities"]:
            s=srv.get(str(f["fid"]),{})
            if not s.get("served"): continue
            oid=f["osm"][0]
            if oid in collect.seen: continue          # dedupe one physical lot across areas
            v=vof(f)
            if not v: continue
            c=ctx.get(str(f["fid"]),{})
            if v["verdict"]=="REVIEW":
                collect.seen.add(oid); items.append(("review",slug,disp,f,v,s,c))
            elif v["verdict"]=="KEEP":
                # Overflow principle (user): a public lot the app surfaces for a trail
                # is a keep even if it mainly serves a ball field / pool / etc. — you'd
                # use it when the main lot is full. So facility-adjacency is NOT a flag.
                # The only real over-keep is one TOO FAR to be overflow: >1 mile walk.
                edge=s.get("dist_m"); walk=(f.get("walk") or {}).get("walk_m")
                dist=walk if walk is not None else edge
                if dist is not None and dist>1609 and not s.get("fallback"):
                    collect.seen.add(oid); items.append(("flag",slug,disp,f,v,s,c))
    return items
collect.seen=set()
def card(kind,f,v,s,c,trails,lots):
    fid=f["fid"]; t=f["tags_union"]
    z1=tile(f,trails,lots,300.0,f"#{fid} · 600 m — surrounding area")
    z2=tile(f,trails,lots,110.0,f"#{fid} · 220 m — the lot")
    tagstr=" · ".join(f"{k}={t[k]}" for k in ("name","access","surface","parking","fee","capacity","operator") if t.get(k)) or "no descriptive tags"
    near=f'<p class="near"><b>next to (OSM):</b> {_h.escape(c.get("category",""))} — {_h.escape(c.get("evidence") or "")}</p>' if c.get("category") else ""
    th=f.get("trailhead_nodes") or []
    thline=f'<span class="chip">trailhead node {th[0][0]} m{(" "+_h.escape(th[0][1])) if th[0][1] else ""}</span>' if th else ''
    dm=s.get("dist_m"); walk=(f.get("walk") or {}).get("walk_m")
    reason=_h.escape(v.get("serves",{}).get("evidence","") or v.get("exists",{}).get("evidence","") or "")
    rh=_h.escape(v.get("resolve_hint") or "")
    imgs=""
    if z1: imgs+=f'<div class="fr"><img src="{z1}"></div>'
    if z2: imgs+=f'<div class="fr"><img src="{z2}"></div>'
    if kind=="review":
        badge='<span class="badge">REVIEW</span>'; whyhdr="why it's unresolved:"; whytxt=reason
        rhline=f'<p class="rh"><b>would resolve it:</b> {rh}</p>' if rh else ''
    else:
        badge='<span class="badge flag">KEPT · CHECK</span>'
        whyhdr="I kept it because:"; whytxt=reason
        rhline='<p class="rh"><b>but it may be too far to be overflow</b> — beyond ~1 mile from the trail. Confirm a hiker would still use it.</p>'
    return f'''<figure class="shot {kind}">
<div class="tw">{imgs}</div>
<figcaption>
<div class="hd">{badge}<span class="conf">{_h.escape(v.get("confidence",""))}</span>
<span class="nm">#{fid} · {_h.escape(t.get("name","(unnamed)") or "(unnamed)")}</span></div>
<p class="prior">{_h.escape(f.get("prior",""))} prior · {_h.escape(tagstr)}</p>
{near}
<div class="chips"><span class="chip">{dm} m to {_h.escape(str(s.get("trail")))}{" · fallback" if s.get("fallback") else ""}</span>
<span class="chip">{("%s m walk"%walk) if walk is not None else "no foot route"}</span>{thline}</div>
<p class="why"><b>{whyhdr}</b> {whytxt}</p>
{rhline}
<p class="ref">{f["lat"]:.5f}, {f["lon"]:.5f} · {"/".join(f["osm"][:2])}</p>
</figcaption></figure>'''
def render_group(rows):
    by=collections.OrderedDict()
    for kind,slug,disp,f,v,s,c in rows: by.setdefault((slug,disp),[]).append((kind,f,v,s,c))
    out=[]
    for (slug,disp),rr in by.items():
        dos=json.load(open(f"{TMP}/{slug}_dossier.json")); trails=region_trails(dos["bbox"]); lots=[(x["lat"],x["lon"]) for x in dos["facilities"]]
        cards=[card(k,f,v,s,c,trails,lots) for k,f,v,s,c in sorted(rr,key=lambda z:z[1]["fid"])]
        out.append(f'<h3>{_h.escape(disp)} <span class="ct">{len(rr)}</span></h3><div class="grid">{"".join(cards)}</div>')
    return "".join(out)
def main():
    items=collect()
    reviews=[it for it in items if it[0]=="review"]; flags=[it for it in items if it[0]=="flag"]
    print(f"review:{len(reviews)} flag:{len(flags)}")
    sec_rev=render_group(reviews); sec_flag=render_group(flags)
    total=len(items)
    page=f'''<style>
:root{{--bg:#e7ebe4;--surface:#f4f6f0;--line:#c9d0c2;--ink:#19211b;--muted:#59645b;--accent:#2b7a8b;--review:#c9821a;--sans:ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif;--mono:ui-monospace,SFMono-Regular,Menlo,monospace;}}
@media(prefers-color-scheme:dark){{:root{{--bg:#0f140e;--surface:#161c15;--line:#2a3327;--ink:#e6ebe1;--muted:#9aa89b;--accent:#5db5cb;--review:#e6a63a;}}}}
:root[data-theme=dark]{{--bg:#0f140e;--surface:#161c15;--line:#2a3327;--ink:#e6ebe1;--muted:#9aa89b;--accent:#5db5cb;--review:#e6a63a;}}
:root[data-theme=light]{{--bg:#e7ebe4;--surface:#f4f6f0;--line:#c9d0c2;--ink:#19211b;--muted:#59645b;--accent:#2b7a8b;--review:#c9821a;}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);line-height:1.5}}
.wrap{{max-width:1280px;margin:0 auto;padding:clamp(18px,3vw,44px)}}
.eyebrow{{font-family:var(--mono);font-size:11px;letter-spacing:.15em;text-transform:uppercase;color:var(--review);margin:0 0 10px}}
h1{{font-size:clamp(24px,4vw,38px);margin:0;letter-spacing:-.02em}}
.lead{{color:var(--muted);max-width:80ch;margin:12px 0 0;font-size:15.5px}}.lead b{{color:var(--ink)}}
h2{{font-size:23px;margin:44px 0 14px;display:flex;align-items:baseline;gap:10px;border-bottom:2px solid var(--review);padding-bottom:8px}}
h2 .ct{{font-family:var(--mono);font-size:13px;color:#fff;background:var(--review);padding:2px 10px;border-radius:20px}}
h3{{font-size:16px;margin:22px 0 12px;color:var(--muted);display:flex;align-items:baseline;gap:8px}}
h3 .ct{{font-family:var(--mono);font-size:12px;color:var(--muted);background:rgba(120,120,120,.15);padding:1px 8px;border-radius:20px}}
.badge.flag{{background:#8a6d1a}}.shot.flag{{box-shadow:inset 4px 0 0 #8a6d1a}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(420px,1fr));gap:18px}}
.shot{{margin:0;background:var(--surface);border:1px solid var(--line);border-radius:14px;overflow:hidden;box-shadow:inset 4px 0 0 var(--review)}}
.tw{{display:grid;grid-template-columns:1fr 1fr;gap:2px;background:var(--line)}}.fr{{line-height:0}}.fr img{{width:100%;height:auto;display:block}}
figcaption{{padding:12px 14px}}
.hd{{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}}
.badge{{font-family:var(--mono);font-size:10px;letter-spacing:.06em;padding:3px 8px;border-radius:20px;color:#fff;background:var(--review)}}
.conf{{font-family:var(--mono);font-size:10px;color:var(--muted);text-transform:uppercase}}
.nm{{font-size:14px;font-weight:600}}
.prior{{margin:8px 0 4px;font-family:var(--mono);font-size:10.5px;color:var(--accent)}}
.near{{margin:2px 0;font-size:12.5px;color:var(--muted)}}
.chips{{display:flex;flex-wrap:wrap;gap:6px;margin:6px 0}}
.chip{{font-family:var(--mono);font-size:10.5px;background:rgba(120,120,120,.12);border:1px solid var(--line);border-radius:20px;padding:2px 9px;color:var(--muted)}}
.why{{margin:8px 0 0;font-size:13px;color:var(--ink)}}.rh{{margin:4px 0 0;font-size:12.5px;color:var(--review)}}
.ref{{margin:6px 0 0;font-family:var(--mono);font-size:10px;color:var(--muted);opacity:.85;user-select:all}}
</style>
<div class="wrap">
<p class="eyebrow">parking adjudication · the review queue · {total} lots</p>
<h1>Reviews — the lots I couldn't call from the air</h1>
<p class="lead">Every lot here <b>serves a trail by distance</b> and is real parking, but one axis stayed undecidable after the zoom
ladder — usually whether it's public trail parking or something else. Two frames per lot: a <b>600 m</b> view for the surrounding
area and a <b>220 m</b> view of the lot. Each card carries the OSM tags, what it sits next to, the trail and walk, and what would
settle it. Your call resolves it.</p>
{(f'<h2>Undecided — needs your call <span class="ct">{len(reviews)}</span></h2>'+sec_rev) if reviews else ''}
{(f'<h2>Kept, but far — too far to be overflow? <span class="ct">{len(flags)}</span></h2><p class="lead" style="margin:0 0 8px">I kept these, but each is more than ~1 mile from its trail. A hiker parks at a facility lot for overflow only if it is reasonably close — confirm these still count.</p>'+sec_flag) if flags else ''}
{"<p class='lead'>Nothing is in review or flagged — every candidate resolved to keep or drop.</p>" if not items else ""}
<p class="lead" style="margin-top:36px;font-size:13px">Imagery: USGS NAIP + ESRI. Red = the mapped lot, yellow = our trails, orange = other lots. "Next to (OSM)" is derived from OpenStreetMap landuse/leisure/amenity tags, not the image. Reversible — nothing shipped.</p>
</div>'''
    out=f"{TMP}/review_queue.html"; open(out,"w").write(page)
    print(f"wrote {out} {len(page)} bytes; {len(reviews)} review + {len(flags)} flagged = {total} lots")
if __name__=="__main__": raise SystemExit(main())
