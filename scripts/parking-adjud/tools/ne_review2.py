#!/usr/bin/env python3
"""NE parking review — SAME visual design as review_queue.py (the established parking
artifact). Reuses its tile()/CSS: two NAIP frames per lot (600 m context + 220 m detail),
sage theme, chips, OSM context. Difference: shows ALL served lots (not review-only), so
the 4 un-judged NE areas render with a YOUR-CALL badge and the raw signals to decide on.

Detail frame reuses the already-rendered <slug>_ladder/*_z2.png (same pipeline); only the
600 m context frame is fetched fresh (80 NAIP pulls, paced)."""
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
import sys, io, json, math, base64, html as _h, time, glob, subprocess
from pathlib import Path
from collections import OrderedDict
from PIL import Image, ImageDraw
sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import review_queue as RQ   # reuse fetch(), fonts, NAIP endpoints (its main() is __main__-guarded)

OSMDIR = _os.environ.get("PADJ_OSM") or "/mnt/raid/trekdex/parking-adjud/osm"

TMP = Path(PADJ_TMP)
GEOMDIR = Path(PADJ_GEOM)
OUT = TMP / "ne_parking_review.html"
AREAS = [
    ("monadnock-reservation-nh", "Monadnock Reservation · New Hampshire", True),
    ("crawford-notch-state-park-nh", "Crawford Notch State Park · New Hampshire", False),
    ("grafton-notch-state-park-me", "Grafton Notch State Park · Maine", False),
    ("camels-hump-state-park-vt", "Camel's Hump State Park · Vermont", False),
    ("mount-major-state-forest-nh", "Mount Major State Forest · New Hampshire", False),
]
VERD = json.load(open(TMP / "ne_verdicts_osm.json"))   # osm-id -> verdict (Monadnock only)

def verd_for(f):
    for i in f.get("osm") or []:
        if i in VERD:
            return VERD[i]
    return None

# --- one geom scan for all NE areas (RQ.region_trails globs 9k files PER call) ---
def load_all_trails(area_bboxes):
    xs = [b[0] for b in area_bboxes] + [b[2] for b in area_bboxes]
    ys = [b[1] for b in area_bboxes] + [b[3] for b in area_bboxes]
    U = (min(xs) - 0.06, min(ys) - 0.06, max(xs) + 0.06, max(ys) + 0.06)
    keep = []
    for fn in glob.glob(str(GEOMDIR / "*.json")):
        try:
            dd = json.load(open(fn))
        except Exception:
            continue
        bb = dd.get("bbox")
        if not bb or bb[2] < U[0] or bb[0] > U[2] or bb[3] < U[1] or bb[1] > U[3]:
            continue
        for tr in dd.get("trails", []):
            for s in tr["segments"]:
                if len(s) >= 2:
                    keep.append([[v[1], v[0]] for v in s])   # [lon,lat] like RQ
    return keep

def trails_for(bbox, allt):
    RB = (bbox[0] - 0.06, bbox[1] - 0.06, bbox[2] + 0.06, bbox[3] + 0.06)
    return [t for t in allt if any(RB[0] <= p[0] <= RB[2] and RB[1] <= p[1] <= RB[3] for p in t)]

def detail_from_png(path):
    if not path.exists():
        return None
    im = Image.open(path).convert("RGB"); im.thumbnail((520, 520))
    b = io.BytesIO(); im.save(b, "JPEG", quality=72, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode()

def _hike_ok(pr):
    """Keep HIKING-relevant OSM ways only; drop ski/bike-park/forest-road noise so cyan
    means 'a hiking trail we don't ship'. Mirrors the pipeline's own bias (track = drivable
    road unless foot=yes; ski/bike-purpose excluded; shared foot=yes trails kept)."""
    hw = pr.get("highway")
    if hw not in ("path", "footway", "steps", "bridleway", "track"):
        return False
    if any(k.startswith("piste") for k in pr):            # ski run
        return False
    footy = pr.get("foot") in ("yes", "designated", "permissive")
    if hw == "track" and not footy:                       # forest/service road, not a foot trail
        return False
    if pr.get("bicycle") == "designated" and not footy:   # purpose-built bike-park trail
        return False
    if hw == "footway" and pr.get("footway") in ("sidewalk", "crossing"):
        return False
    return True

def osm_lines(area):
    """HIKING OSM ways in the area (ctx pbf), as [lon,lat] lines, drawn cyan UNDER the yellow
    shipped trail — so cyan = a hiking trail OSM has that we don't ship (e.g. a trimmed
    trailhead spur). Ski pistes / bike-park / forest-road tracks filtered out (_hike_ok)."""
    cache = TMP / f"{area}_osmhike.json"
    if cache.exists():
        return json.load(open(cache))
    pbf = f"{OSMDIR}/{area}_ctx.osm.pbf"
    tmp = f"/tmp/{area}_ways.osm.pbf"
    subprocess.run(["osmium", "tags-filter", "-o", tmp, "--overwrite", pbf,
                    "w/highway=path,footway,track,bridleway,steps"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    gj = subprocess.run(["osmium", "export", "-f", "geojsonseq", tmp],
                        capture_output=True, text=True).stdout
    lines = []
    for ln in gj.splitlines():
        ln = ln.strip().lstrip("\x1e")
        if not ln:
            continue
        try:
            o = json.loads(ln)
        except Exception:
            continue
        g = o.get("geometry"); pr = o.get("properties") or {}
        if not g or not _hike_ok(pr):
            continue
        segs = [g["coordinates"]] if g["type"] == "LineString" else (g["coordinates"] if g["type"] == "MultiLineString" else [])
        for seg in segs:
            if len(seg) >= 2:
                lines.append([[x, y] for x, y in seg])   # [lon,lat]
    json.dump(lines, open(cache, "w"))
    return lines

def render_frame(area, f, half, label, shipped, osm, lots):
    """NAIP frame: cyan OSM foot-ways (under) → yellow shipped trails (over) → orange other
    lots → red this-lot. Raw imagery cached per (fid,half) so re-renders don't re-fetch."""
    la, lo = f["lat"], f["lon"]; PX = 900
    dlat = half / 111320.0; dlon = half / (111320.0 * math.cos(la * math.pi / 180))
    x0, y0, x1, y1 = lo - dlon, la - dlat, lo + dlon, la + dlat
    rawdir = TMP / f"{area}_raw"; rawdir.mkdir(exist_ok=True)
    rawp = rawdir / f"{f['fid']:04d}_{int(half)}.png"
    if rawp.exists():
        im = Image.open(rawp).convert("RGB")
    else:
        im = RQ.fetch(x0, y0, x1, y1, PX)
        if im is None:
            return None
        im.save(rawp); time.sleep(0.4)
    dr = ImageDraw.Draw(im, "RGBA")
    X = lambda v: (v - x0) / (x1 - x0) * PX; Y = lambda v: (y1 - v) / (y1 - y0) * PX
    def hits(t): return len(t) > 1 and any(x0 <= p[0] <= x1 and y0 <= p[1] <= y1 for p in t)
    for t in osm:                       # cyan under
        if hits(t):
            pts = [(X(p[0]), Y(p[1])) for p in t]
            dr.line(pts, fill=(6, 22, 34, 160), width=6); dr.line(pts, fill=(0, 209, 255, 255), width=3)
    for t in shipped:                   # yellow over
        if hits(t):
            pts = [(X(p[0]), Y(p[1])) for p in t]
            dr.line(pts, fill=(10, 10, 10, 175), width=8); dr.line(pts, fill=(255, 230, 0, 255), width=4)
    for l in lots:
        if x0 <= l[1] <= x1 and y0 <= l[0] <= y1 and (abs(l[0] - la) > 1e-6 or abs(l[1] - lo) > 1e-6):
            dr.ellipse([X(l[1]) - 5, Y(l[0]) - 5, X(l[1]) + 5, Y(l[0]) + 5], outline=(255, 150, 0, 255), width=3)
    rings = f.get("rings") or ([f["ring"]] if f.get("ring") else [])
    if rings:
        for ring in rings:
            pts = [(X(p[1]), Y(p[0])) for p in ring]
            if len(pts) > 2:
                dr.line(pts + [pts[0]], fill=(255, 30, 30, 255), width=5)
    else:
        r = 20.0 / (2 * half) * PX
        dr.ellipse([X(lo) - r, Y(la) - r, X(lo) + r, Y(la) + r], outline=(255, 30, 30, 255), width=5)
    dr.rectangle([0, 0, PX, 30], fill=(0, 0, 0, 175)); dr.text((7, 6), label, fill=(255, 235, 120, 255), font=RQ.Fnt)
    im.thumbnail((520, 520)); b = io.BytesIO(); im.save(b, "JPEG", quality=72, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode()

BADGE = {"KEEP": ("keep", "KEEP"), "DROP": ("drop", "DROP"), "REVIEW": ("review", "REVIEW")}

def card(area, f, s, c, ctx_img, det_img):
    fid = f["fid"]; t = f.get("tags_union") or {}
    v = verd_for(f)
    kind, label = BADGE.get(v["verdict"], ("call", "YOUR CALL")) if v else ("call", "YOUR CALL")
    imgs = ""
    if ctx_img: imgs += f'<div class="fr"><img src="{ctx_img}"></div>'
    if det_img: imgs += f'<div class="fr"><img src="{det_img}"></div>'
    tagstr = " · ".join(f"{k}={t[k]}" for k in ("name", "access", "surface", "parking", "fee", "capacity", "operator") if t.get(k)) or "no descriptive tags"
    near = f'<p class="near"><b>next to (OSM):</b> {_h.escape(c.get("category",""))} — {_h.escape(c.get("evidence") or "")}</p>' if c.get("category") and c.get("category") != "NEUTRAL" else ""
    th = f.get("trailhead_nodes") or []
    thline = f'<span class="chip">trailhead node {th[0][0]} m</span>' if th else ''
    dm = s.get("dist_m"); walk = (f.get("walk") or {}).get("walk_m")
    fb = " · fallback" if s.get("fallback") else ""
    chips = (f'<span class="chip">{dm} m to {_h.escape(str(s.get("trail")))}{fb}</span>'
             f'<span class="chip">{("%s m walk" % walk) if walk is not None else "no foot route"}</span>{thline}')
    if v:
        reason = _h.escape(v.get("serves", {}).get("evidence", "") or v.get("exists", {}).get("evidence", "") or "")
        rh = _h.escape(v.get("resolve_hint") or "")
        why = f'<p class="why"><b>my read:</b> {reason}</p>' if reason else ''
        rhline = f'<p class="rh"><b>would resolve it:</b> {rh}</p>' if rh else ''
        conf = _h.escape(v.get("confidence", ""))
    else:
        why = f'<p class="why"><b>{_h.escape(f.get("prior",""))} prior</b> — real parking by tag; you decide keep / drop / review.</p>'
        rhline = ''
        conf = ''
    return f'''<figure class="shot {kind}">
<div class="tw">{imgs}</div>
<figcaption>
<div class="hd"><span class="badge {kind}">{_h.escape(label)}</span><span class="conf">{conf}</span>
<span class="nm">#{fid} · {_h.escape(t.get("name","(unnamed)") or "(unnamed)")}</span></div>
<p class="prior">{_h.escape(tagstr)}</p>
{near}
<div class="chips">{chips}</div>
{why}
{rhline}
<p class="ref">{f["lat"]:.5f}, {f["lon"]:.5f} · {"/".join(f["osm"][:2])}</p>
</figcaption></figure>'''

def main():
    boxes = [json.load(open(TMP / f"{a}_dossier.json"))["bbox"] for a, _, _ in AREAS]
    print("loading NE trails (one geom scan)...", flush=True)
    allt = load_all_trails(boxes)
    print(f"trails in region: {len(allt)}", flush=True)
    counts = {"keep": 0, "drop": 0, "review": 0}
    buckets = {"review": [], "drop": [], "keep": []}   # each: (area_display, card_html)
    nf = 0
    for area, disp, _is in AREAS:
        dos = json.load(open(TMP / f"{area}_dossier.json"))
        srv = json.load(open(TMP / f"{area}_serves2.json"))
        ctx = json.load(open(TMP / f"{area}_context.json"))
        pub = [int(x) for x in open(TMP / f"{area}_pub.txt").read().split(",") if x.strip()]
        F = {f["fid"]: f for f in dos["facilities"]}
        shipped = trails_for(dos["bbox"], allt)               # [lon,lat] lines
        osm = osm_lines(area)                                  # [lon,lat] lines
        lots = [(x["lat"], x["lon"]) for x in dos["facilities"]]
        shipverts = [(p[1], p[0]) for t in shipped for p in t]  # (lat,lon) for nearest-dist
        for fid in sorted(pub):
            f = F[fid]; s = srv.get(str(fid), {}); c = ctx.get(str(fid), {})
            v = verd_for(f)
            kind = BADGE.get(v["verdict"], ("review",))[0] if v else "review"
            counts[kind] += 1
            la, lo = f["lat"], f["lon"]
            # context frame widens to include the nearest shipped-trail point, so a lot whose
            # trail was trimmed short shows the lot + the cyan gap + where our yellow starts.
            if shipverts:
                dsh = min(math.hypot((la - a) * 111320, (lo - b) * 111320 * math.cos(math.radians(la))) for a, b in shipverts)
            else:
                dsh = 200.0
            ch = max(110.0, min(2200.0, dsh * 1.25 + 60))
            ctx_img = render_frame(area, f, ch, f"#{fid} · {int(ch*2)} m · cyan = OSM trail we don't ship", shipped, osm, lots)
            det_img = render_frame(area, f, 110.0, f"#{fid} · 220 m — the lot", shipped, osm, lots)
            nf += 2
            buckets[kind].append((disp, card(area, f, s, c, ctx_img, det_img)))
        print(f"  {area}: {len(pub)} lots rendered", flush=True)
    total = sum(counts.values())

    def render_bucket(items):
        by = OrderedDict()
        for disp, h in items:
            by.setdefault(disp, []).append(h)
        return "".join(f'<h3>{_h.escape(d)} <span class="ct">{len(cs)}</span></h3>'
                       f'<div class="grid">{"".join(cs)}</div>' for d, cs in by.items())

    # RQ's exact CSS + a small verdict-badge extension in the same palette
    css = RQ_CSS + """
.badge.keep{background:#2f7d4f}.badge.drop{background:#b34a3a}
.shot.keep{box-shadow:inset 4px 0 0 #2f7d4f}.shot.drop{box-shadow:inset 4px 0 0 #b34a3a}
.chip.keepc{color:#2f7d4f}.chip.dropc{color:#b34a3a}.chip.reviewc{color:var(--review)}
"""
    page = f'''<style>{css}</style>
<div class="wrap">
<p class="eyebrow">parking adjudication · New England · {total} lots</p>
<h1>New England parking — review</h1>
<p class="lead">The sparse-forest run, fully judged. Every public lot the serves-gate surfaces across
5 areas, two NAIP frames each: a <b>context</b> view (widened to reach our nearest trail) and a
<b>220 m</b> lot view. Sorted by verdict — the <b>reviews</b> need your call, the <b>drops</b> are worth
a sanity-check, the <b>keeps</b> are here for completeness.<br>
Red = the mapped lot · <b style="color:#e0a000">yellow = our shipped trail</b> ·
<b style="color:#00b3d8">cyan = OSM trail we DON'T ship</b> (the trimmed spur / coverage gap) · orange = other lots.
So a lot where yellow starts far away but cyan runs right to it is a real trailhead whose access spur we clipped.</p>
<div class="tally">
  <span class="chip reviewc">{counts['review']} review</span>
  <span class="chip dropc">{counts['drop']} drop</span>
  <span class="chip keepc">{counts['keep']} keep</span>
</div>
<h2>Review — needs your call <span class="ct">{counts['review']}</span></h2>
{render_bucket(buckets['review'])}
<h2>Drops — sanity-check these <span class="ct">{counts['drop']}</span></h2>
{render_bucket(buckets['drop'])}
<h2>Keeps <span class="ct">{counts['keep']}</span></h2>
{render_bucket(buckets['keep'])}
<p class="lead" style="margin-top:36px;font-size:13px">Imagery: USGS NAIP (0.21 m/px detail). "Next to
(OSM)" is from OpenStreetMap tags, not the image. Reversible — nothing shipped.</p>
</div>'''
    OUT.write_text(page)
    print(f"wrote {OUT} {len(page)/1e6:.2f} MB · {nf} context fetches · counts {counts}", flush=True)

# pull RQ's <style> body verbatim so the theme/layout is identical
_src = (Path(_os.path.dirname(_os.path.abspath(__file__))) / "review_queue.py").read_text()
# extract RQ's <style> body; it lives in an f-string so braces are DOUBLED — un-double them
RQ_CSS = _src.split("page=f'''<style>\n", 1)[1].split("\n</style>", 1)[0].replace("{{", "{").replace("}}", "}")

if __name__ == "__main__":
    main()
