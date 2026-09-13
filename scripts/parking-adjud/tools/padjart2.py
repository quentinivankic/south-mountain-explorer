#!/usr/bin/env python3
"""Protocol artifact — renders the dossier + serves2 + protocol verdicts into a
review page. Cards show the three per-axis calls with evidence and which frame
decided; the map colours every served lot; REVIEW cards embed the Z3 tile.

Usage: python3 padjart2.py <slug> "Display Name"  """
import base64, io, json, math, os, sys, html as _h, collections, glob
from PIL import Image
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
CSTA={"KEEP":"#37c46e","DROP":"#e2503a","REVIEW":"#e0a72c"}
def aerial(x0,y0,x1,y1,W,H):
    import urllib.request
    url=("https://services.arcgisonline.com/arcgis/rest/services/World_Imagery/MapServer/export"
         f"?bbox={x0},{y0},{x1},{y1}&bboxSR=4326&imageSR=4326&size={W},{H}&format=png&f=image")
    for _ in range(3):
        try: return Image.open(io.BytesIO(urllib.request.urlopen(urllib.request.Request(url,headers={"User-Agent":"trekdex/1.0"}),timeout=90).read())).convert("RGB")
        except Exception: pass
    return Image.new("RGB",(W,H),(40,40,40))
def uri(slug,fid,frame="z2",px=470,q=64):
    p=f"{TMP}/{slug}_ladder/{fid:04d}_{frame}.png"
    if not os.path.exists(p): p=f"{TMP}/{slug}_ladder/{fid:04d}_z1.png"
    if not os.path.exists(p): return ""
    im=Image.open(p).convert("RGB"); im.thumbnail((px,px)); rb=io.BytesIO(); im.save(rb,"JPEG",quality=q,optimize=True)
    return "data:image/jpeg;base64,"+base64.b64encode(rb.getvalue()).decode()
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
def main(slug,name=None):
    dos=json.load(open(f"{TMP}/{slug}_dossier.json")); srv=json.load(open(f"{TMP}/{slug}_serves2.json"))
    V=json.load(open(f"{TMP}/{slug}_verdicts2.json")); F={f["fid"]:f for f in dos["facilities"]}
    name=name or dos.get("name",slug); b=dos["bbox"]; trails=region_trails(b)
    served=[f for f in dos["facilities"] if srv.get(str(f["fid"]),{}).get("served")]
    pubserved=[f for f in served if str(f["fid"]) in V]
    priv=[f for f in served if f["tags_union"].get("access") in ("private","no","customers")]
    # map frame = bbox of served lots
    xs=[f["lon"] for f in served]; ys=[f["lat"] for f in served]
    x0,x1,y0,y1=min(xs),max(xs),min(ys),max(ys)
    px=max((x1-x0)*0.05,0.006); py=max((y1-y0)*0.05,0.005); x0-=px;x1+=px;y0-=py;y1+=py
    lat0=(y0+y1)/2;k=math.cos(math.radians(lat0));wd=(x1-x0)*k;hd=(y1-y0);L=1360
    W=L if wd>=hd else int(L*wd/hd); H=int(L*hd/wd) if wd>=hd else L
    img=aerial(x0,y0,x1,y1,W,H); buf=io.BytesIO(); img.save(buf,"JPEG",quality=80,optimize=True)
    b64="data:image/jpeg;base64,"+base64.b64encode(buf.getvalue()).decode()
    X=lambda lo:(lo-x0)/(x1-x0)*W; Y=lambda la:(y1-la)/(y1-y0)*H
    parts=[f'<image href="{b64}" x="0" y="0" width="{W}" height="{H}"/>']
    for t in trails:
        if len(t)>1:
            ps=" ".join(f"{X(p[0]):.1f},{Y(p[1]):.1f}" for p in t)
            parts.append(f'<polyline class="trc" points="{ps}"/>'); parts.append(f'<polyline class="tr" points="{ps}"/>')
    for f in served:
        v=V.get(str(f["fid"]))
        col=CSTA.get((v or {}).get("verdict"),"#888")
        why=_h.escape((v or {}).get("serves",{}).get("evidence","") or "private") if v else "private"
        parts.append(f'<circle cx="{X(f["lon"]):.1f}" cy="{Y(f["lat"]):.1f}" r="7" fill="{col}" stroke="#fff" stroke-width="2"><title>#{f["fid"]} {(v or {}).get("verdict","PRIVATE")}: {why}</title></circle>')
    svg=f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" class="map">{"".join(parts)}</svg>'
    keep=[f for f in pubserved if V[str(f["fid"])]["verdict"]=="KEEP"]
    drop=[f for f in pubserved if V[str(f["fid"])]["verdict"]=="DROP"]
    review=[f for f in pubserved if V[str(f["fid"])]["verdict"]=="REVIEW"]
    def axis(v,k):
        a=v.get(k,{}); call=a.get("call","?"); ev=_h.escape(a.get("evidence","") or "")
        cls={"yes":"ax-y","no":"ax-n","unclear":"ax-u"}.get(call,"ax-u")
        return f'<div class="ax {cls}"><b>{k}</b> {call} · <span>{ev}</span></div>'
    def card(f,frame="z2"):
        v=V[str(f["fid"])]; t=f["tags_union"]; nm=t.get("name","") or "(unnamed)"
        s=srv.get(str(f["fid"]),{}); dm=s.get("dist_m")
        badge=v["verdict"]; conf=v.get("confidence","")
        tagstr=" · ".join(f"{k}={t[k]}" for k in ("access","surface","parking","fee","capacity","operator") if t.get(k)) or "no descriptive tags"
        frames=", ".join(v.get("frames_used",[]))
        rh=f'<p class="rh">→ resolve: {_h.escape(v["resolve_hint"])}</p>' if v.get("resolve_hint") else ""
        return (f'<figure class="shot v-{badge}"><div class="iw"><img loading="lazy" src="{uri(f["ctxslug"],f["fid"],frame)}"></div>'
                f'<figcaption><span class="badge b-{badge}">{badge}</span><span class="conf">{conf}</span>'
                f'<p class="nm">#{f["fid"]} · {_h.escape(nm)}</p>'
                f'<p class="prior">{v["prior"]} prior · {_h.escape(tagstr)}</p>'
                f'{axis(v,"exists")}{axis(v,"public")}{axis(v,"serves")}'
                f'<p class="meta">{dm} m to {_h.escape(str(s.get("trail")))}{" (fallback)" if s.get("fallback") else ""} · frames: {frames}</p>{rh}'
                f'<p class="ref">{f["lat"]:.5f}, {f["lon"]:.5f} · {"/".join(f["osm"][:2])}</p></figcaption></figure>')
    for f in dos["facilities"]: f["ctxslug"]=slug
    def grid(fs,frame="z2"):
        fs=sorted(fs,key=lambda z:(srv.get(str(z["fid"]),{}).get("dist_m") or 9e9))
        return f'<div class="grid">{"".join(card(f,frame) for f in fs)}</div>'
    def sec(cls,title,fs,blurb,frame="z2"):
        return (f'<h2 class="{cls}">{title} <span class="ct">{len(fs)}</span></h2><p class="sub">{blurb}</p>{grid(fs,frame)}') if fs else ""
    nkeep=len(keep); undoss=len(served)-len(pubserved)-len(priv)
    page=f'''<style>
:root{{--bg:#e7ebe4;--surface:#f4f6f0;--line:#c9d0c2;--ink:#19211b;--muted:#59645b;--accent:#2b7a8b;--keep:#2f8f5b;--drop:#c15038;--review:#c9821a;--sans:ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif;--mono:ui-monospace,SFMono-Regular,Menlo,monospace;}}
@media(prefers-color-scheme:dark){{:root{{--bg:#0f140e;--surface:#161c15;--line:#2a3327;--ink:#e6ebe1;--muted:#9aa89b;--accent:#5db5cb;--keep:#43b072;--drop:#e2503a;--review:#e6a63a;}}}}
:root[data-theme=dark]{{--bg:#0f140e;--surface:#161c15;--line:#2a3327;--ink:#e6ebe1;--muted:#9aa89b;--accent:#5db5cb;--keep:#43b072;--drop:#e2503a;--review:#e6a63a;}}
:root[data-theme=light]{{--bg:#e7ebe4;--surface:#f4f6f0;--line:#c9d0c2;--ink:#19211b;--muted:#59645b;--accent:#2b7a8b;--keep:#2f8f5b;--drop:#c15038;--review:#c9821a;}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);line-height:1.5}}
.wrap{{max-width:1360px;margin:0 auto;padding:clamp(18px,3vw,44px)}}
.eyebrow{{font-family:var(--mono);font-size:11px;letter-spacing:.15em;text-transform:uppercase;color:var(--accent);margin:0 0 10px}}
h1{{font-size:clamp(24px,4vw,40px);margin:0;letter-spacing:-.02em}}
.lead{{color:var(--muted);max-width:82ch;margin:12px 0 0;font-size:15.5px}}.lead b{{color:var(--ink)}}
.legend{{display:flex;flex-wrap:wrap;gap:8px 18px;margin:18px 0;font-size:13.5px;color:var(--muted)}}
.legend span{{display:flex;align-items:center;gap:8px}}.d{{width:14px;height:14px;border-radius:50%;border:2px solid #fff}}.ln{{width:20px;border-top:4px solid #ffe600}}
.mapbox{{border:1px solid var(--line);border-radius:14px;overflow:hidden;background:var(--surface);line-height:0}}
.map{{width:100%;height:auto;display:block}}.map .trc{{fill:none;stroke:#101010;stroke-opacity:.6;stroke-width:5}}.map .tr{{fill:none;stroke:#ffe600;stroke-width:2.4}}
.map circle{{cursor:help}}.map circle:hover{{r:11}}
h2{{font-size:22px;margin:44px 0 2px;display:flex;align-items:baseline;gap:10px}}h2 .ct{{font-family:var(--mono);font-size:14px;color:#fff;padding:2px 10px;border-radius:20px}}
.h-k .ct{{background:var(--keep)}}.h-d .ct{{background:var(--drop)}}.h-r .ct{{background:var(--review)}}
.sub{{color:var(--muted);font-size:14px;margin:0 0 16px;max-width:82ch}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:16px}}
.shot{{margin:0;background:var(--surface);border:1px solid var(--line);border-radius:14px;overflow:hidden}}
.shot.v-KEEP{{box-shadow:inset 4px 0 0 var(--keep)}}.shot.v-DROP{{box-shadow:inset 4px 0 0 var(--drop)}}.shot.v-REVIEW{{box-shadow:inset 4px 0 0 var(--review)}}
.iw{{line-height:0}}.shot img{{width:100%;height:auto;display:block}}
figcaption{{padding:12px 14px}}
.badge{{display:inline-block;font-family:var(--mono);font-size:10.5px;letter-spacing:.07em;padding:3px 9px;border-radius:20px;color:#fff}}
.b-KEEP{{background:var(--keep)}}.b-DROP{{background:var(--drop)}}.b-REVIEW{{background:var(--review)}}
.conf{{font-family:var(--mono);font-size:10px;color:var(--muted);margin-left:8px;text-transform:uppercase}}
.nm{{margin:8px 0 2px;font-size:14px;font-weight:600}}
.prior{{margin:0 0 8px;font-family:var(--mono);font-size:10.5px;color:var(--accent)}}
.ax{{font-size:12px;margin:2px 0;padding:3px 7px;border-radius:6px;background:rgba(120,120,120,.08)}}
.ax b{{font-family:var(--mono);font-size:10px;text-transform:uppercase;letter-spacing:.04em}}.ax span{{color:var(--muted)}}
.ax-y{{border-left:3px solid var(--keep)}}.ax-n{{border-left:3px solid var(--drop)}}.ax-u{{border-left:3px solid var(--review)}}
.meta{{margin:8px 0 0;font-family:var(--mono);font-size:10.5px;color:var(--muted)}}
.rh{{margin:4px 0 0;font-size:11.5px;color:var(--review)}}
.ref{{margin:4px 0 0;font-family:var(--mono);font-size:10px;color:var(--muted);opacity:.85;user-select:all}}
.summary{{margin:16px 0 0;padding:14px 16px;border:1px dashed var(--line);border-radius:12px;background:var(--surface);color:var(--muted);font-size:13.5px}}.summary b{{color:var(--ink)}}
</style>
<div class="wrap">
<p class="eyebrow">{_h.escape(name)} · protocol v2 · exists · public · serves</p>
<h1>Parking, judged rung by rung</h1>
<p class="lead">Each candidate is decided on <b>three independent axes</b> — is there a real place to park (<b>exists</b>), may a hiker
use it (<b>public</b>), does a trail plausibly explain it (<b>serves</b>) — read against the lot's own OSM tags at up to
0.14 m/px, escalating to a second sensor (NAIP) under canopy. A surveyed lot is <b>kept unless the imagery positively
contradicts it</b>; "I can't see it" means zoom in, never drop. Every card cites what was seen and in which frame.</p>
<div class="legend">
  <span><span class="d" style="background:#37c46e"></span>keep ({len(keep)})</span>
  <span><span class="d" style="background:#e2503a"></span>drop ({len(drop)})</span>
  <span><span class="d" style="background:#e0a72c"></span>review ({len(review)})</span>
  <span><span class="ln"></span>trail (any area)</span>
</div>
<div class="mapbox">{svg}</div>
<div class="summary">From <b>{len(dos["facilities"])}</b> parking facilities the serves-gate keeps <b>{len(served)}</b> candidates:
<b>{len(priv)}</b> private (tag), <b>{len(pubserved)}</b> vision-judged here → <b>{len(keep)}</b> keep, <b>{len(drop)}</b> drop,
<b>{len(review)}</b> review. Review rate {len(review)}/{len(pubserved)}. Every keep cites cars or stripes or a surveyed prior with no contradiction.</div>
{sec("h-k","Keep — real trailhead parking",keep,"A place to leave a car that a trail explains. Cars, stripes, or a surveyed prior the aerial did not contradict.")}
{sec("h-r","Review — undecidable from the air",review,"Exists, but the serving purpose could not be confirmed. Each names what would resolve it.","z3")}
{sec("h-d","Drop — not trailhead parking",drop,"A better explanation than the trail: storage yard, garage in a complex, urban block, or a facility lot far from any trail.")}
<p class="sub" style="margin-top:36px">Imagery: ESRI World Imagery + USGS NAIP. Serves = the app's nearestParkingWithFallback on polygon-edge distances, 5 km fallback cap.
Verdict schema per lot: exists/public/serves calls with cited evidence, frames used, tags cited, confidence. Reversible — nothing shipped.</p>
</div>'''
    out=f"{TMP}/{slug}_parking_v2.html"; open(out,"w").write(page)
    print(f"wrote {out} {len(page)} bytes; keep {len(keep)} drop {len(drop)} review {len(review)} private {len(priv)}")
if __name__=="__main__": raise SystemExit(main(sys.argv[1], sys.argv[2] if len(sys.argv)>2 else None))
