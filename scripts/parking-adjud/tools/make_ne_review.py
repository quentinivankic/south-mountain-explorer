#!/usr/bin/env python3
"""NE parking review artifact — every served/public lot across the 5 New England
areas, each with its Z2 NAIP tile + OSM context + serves gate. Monadnock carries my
verdicts (7 keep / 3 drop / 3 review); the other 4 areas are un-judged (your call).
Tiles downscaled + JPEG-embedded so it stays self-contained and under the size cap."""
import json, io, base64, html, os
from pathlib import Path
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


TMP = Path(PADJ_TMP)
OUT = Path("/tmp/claude-1000/-home-quentin-south-mountain-explorer/36f999b1-64e9-4306-b9da-46fc6709379d/scratchpad/ne_parking_review.html")
AREAS = [
    ("monadnock-reservation-nh", "Monadnock Reservation, NH"),
    ("crawford-notch-state-park-nh", "Crawford Notch State Park, NH"),
    ("grafton-notch-state-park-me", "Grafton Notch State Park, ME"),
    ("camels-hump-state-park-vt", "Camel's Hump State Park, VT"),
    ("mount-major-state-forest-nh", "Mount Major State Forest, NH"),
]
VERD = json.load(open(TMP / "ne_verdicts_osm.json"))
BADGE = {"KEEP": "keep", "DROP": "drop", "REVIEW": "review", None: "call"}
KEYTAGS = ["amenity", "parking", "access", "fee", "surface", "capacity", "operator"]

def thumb(path, px=440):
    im = Image.open(path).convert("RGB")
    im.thumbnail((px, px))
    b = io.BytesIO(); im.save(b, "JPEG", quality=72)
    return "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode()

def gmap(la, lo): return f"https://www.google.com/maps/search/?api=1&query={la},{lo}"

def verdict_for(osm_ids):
    for i in osm_ids or []:
        if i in VERD:
            return VERD[i]
    return None

def esc(x): return html.escape(str(x))

def card(area, f, srv, ctx):
    fid = f["fid"]
    tile = TMP / f"{area}_ladder/{fid:04d}_z2.png"
    img = (f'<img loading="lazy" src="{thumb(tile)}" alt="lot {fid}">'
           if tile.exists() else '<div class="notile">no tile</div>')
    tags = f.get("tags_union") or {}
    name = tags.get("name") or "(unnamed lot)"
    v = verdict_for(f.get("osm"))
    kind = BADGE[v["verdict"]] if v else "call"
    label = v["verdict"] if v else "YOUR CALL"
    # serves
    s = srv.get(str(fid)) or {}
    if s.get("served"):
        fb = " · fallback" if s.get("fallback") else ""
        serves = f'→ {esc(s.get("trail"))} · {s.get("dist_m")} m{fb}'
    else:
        serves = "not served"
    # context
    c = ctx.get(str(fid)) or {}
    cat = c.get("category"); cev = c.get("evidence")
    context = f'{esc(cat)}: {esc(cev)}' if cat and cat != "NEUTRAL" else (esc(cat) if cat else "")
    # tags line
    tl = " · ".join(f"{k}={esc(tags[k])}" for k in KEYTAGS if k in tags)
    # evidence block: my verdict's reasoning, or the raw signals for un-judged
    if v:
        ev = "".join(
            f'<div class="ax"><b>{ax}</b> {esc(v[ax]["call"])} — {esc(v[ax]["evidence"])}</div>'
            for ax in ("exists", "public", "serves") if v.get(ax))
    else:
        bits = []
        if f.get("prior"): bits.append(f'prior={esc(f["prior"])}')
        if f.get("footways_60m"): bits.append(f'footways≤60m={f["footways_60m"]}')
        if f.get("trailhead_nodes"): bits.append(f'trailhead nodes={len(f["trailhead_nodes"])}')
        if f.get("building_overlap"): bits.append("building overlap")
        if f.get("area_m2"): bits.append(f'{int(f["area_m2"])} m²')
        ev = f'<div class="ax raw">{esc(" · ".join(bits))}</div>'
    return f"""<div class="card {kind}">
  <div class="pic">{img}<span class="badge {kind}">{esc(label)}</span></div>
  <div class="body">
    <div class="hd"><span class="fid">#{fid}</span> {esc(name)}
      <a class="mp" href="{gmap(f['lat'],f['lon'])}" target="_blank">aerial ↗</a></div>
    <div class="serves">{serves}</div>
    {f'<div class="ctx">{context}</div>' if context else ''}
    {f'<div class="tags">{tl}</div>' if tl else ''}
    <div class="ev">{ev}</div>
  </div>
</div>"""

def area_order(area, pub, srv):
    """served first (they matter), REVIEW/your-call bubbled up within that."""
    def rank(fid, f):
        v = verdict_for(f.get("osm"))
        served = (srv.get(str(fid)) or {}).get("served")
        base = 0 if served else 5
        vr = {"REVIEW": 0, None: 1, "DROP": 2, "KEEP": 3}.get(v["verdict"] if v else None, 1)
        return (base, vr, fid)
    return rank

sections = []
counts = {"keep": 0, "drop": 0, "review": 0, "call": 0}
for area, disp in AREAS:
    dos = json.load(open(TMP / f"{area}_dossier.json"))
    srv = json.load(open(TMP / f"{area}_serves2.json"))
    ctx = json.load(open(TMP / f"{area}_context.json"))
    pub = [int(x) for x in open(TMP / f"{area}_pub.txt").read().split(",") if x.strip()]
    F = {f["fid"]: f for f in dos["facilities"]}
    rank = area_order(area, pub, srv)
    fids = sorted(pub, key=lambda fid: rank(fid, F[fid]))
    cards = []
    for fid in fids:
        f = F[fid]
        v = verdict_for(f.get("osm"))
        counts[BADGE[v["verdict"]] if v else "call"] += 1
        cards.append(card(area, f, srv, ctx))
    judged = "judged" if area == "monadnock-reservation-nh" else "un-judged — your call"
    sections.append(f"""<section>
  <div class="ahd"><h2>{esc(disp)}</h2><span class="apill">{len(fids)} public lots · {judged}</span></div>
  <div class="grid">{''.join(cards)}</div>
</section>""")

PAGE = f"""<title>New England parking — review</title>
<style>
:root {{ --bg:#f5f6f8; --panel:#fff; --ink:#1b2130; --muted:#5c6573; --line:#e4e7ec;
  --keep:#1a9d63; --drop:#d6483b; --review:#c07807; --call:#6b7280;
  --shadow:0 1px 2px rgba(20,28,45,.06),0 4px 14px rgba(20,28,45,.05); }}
@media (prefers-color-scheme:dark) {{ :root {{ --bg:#0f141c; --panel:#161d28; --ink:#e6eaf1;
  --muted:#95a0b0; --line:#26303f; --keep:#37c785; --drop:#f0776f; --review:#f0a742; --call:#9aa4b2;
  --shadow:0 1px 2px rgba(0,0,0,.4),0 6px 18px rgba(0,0,0,.35); }} }}
:root[data-theme="dark"] {{ --bg:#0f141c; --panel:#161d28; --ink:#e6eaf1; --muted:#95a0b0;
  --line:#26303f; --keep:#37c785; --drop:#f0776f; --review:#f0a742; --call:#9aa4b2; }}
:root[data-theme="light"] {{ --bg:#f5f6f8; --panel:#fff; --ink:#1b2130; --muted:#5c6573;
  --line:#e4e7ec; --keep:#1a9d63; --drop:#d6483b; --review:#c07807; --call:#6b7280; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink);
  font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }}
.wrap {{ max-width:1200px; margin:0 auto; padding:30px 22px 64px; }}
h1 {{ font-size:22px; margin:0 0 6px; letter-spacing:-.01em; }}
.lede {{ color:var(--muted); max-width:74ch; margin:0 0 14px; }}
.tally {{ display:flex; gap:8px; flex-wrap:wrap; margin:8px 0 4px; }}
.chip {{ font-size:12.5px; padding:3px 10px; border-radius:20px; border:1px solid var(--line);
  font-variant-numeric:tabular-nums; }}
.chip b {{ font-weight:700; }}
.chip.keep {{ color:var(--keep); }} .chip.drop {{ color:var(--drop); }}
.chip.review {{ color:var(--review); }} .chip.call {{ color:var(--call); }}
section {{ margin-top:30px; }}
.ahd {{ display:flex; align-items:baseline; gap:12px; border-bottom:1px solid var(--line);
  padding-bottom:7px; margin-bottom:14px; }}
.ahd h2 {{ font-size:16.5px; margin:0; }}
.apill {{ color:var(--muted); font-size:12.5px; }}
.grid {{ display:grid; gap:15px; grid-template-columns:repeat(auto-fill,minmax(320px,1fr)); }}
.card {{ background:var(--panel); border:1px solid var(--line); border-radius:13px; overflow:hidden;
  box-shadow:var(--shadow); border-left:4px solid var(--call); }}
.card.keep {{ border-left-color:var(--keep); }} .card.drop {{ border-left-color:var(--drop); }}
.card.review {{ border-left-color:var(--review); }} .card.call {{ border-left-color:var(--call); }}
.pic {{ position:relative; background:#0b0f16; aspect-ratio:1/1; }}
.pic img {{ width:100%; height:100%; object-fit:cover; display:block; }}
.notile {{ color:var(--muted); font-size:12px; display:grid; place-items:center; height:100%; }}
.badge {{ position:absolute; top:9px; right:9px; font-size:11px; font-weight:700; letter-spacing:.03em;
  padding:3px 9px; border-radius:20px; background:rgba(10,14,22,.78); color:#fff; }}
.badge.keep {{ background:var(--keep); }} .badge.drop {{ background:var(--drop); }}
.badge.review {{ background:var(--review); }} .badge.call {{ background:#374151; }}
.body {{ padding:11px 13px 13px; }}
.hd {{ font-weight:600; font-size:14px; }}
.hd .fid {{ color:var(--muted); font-weight:700; margin-right:4px; }}
.hd .mp {{ float:right; font-size:12px; font-weight:500; color:#3b82f6; text-decoration:none; }}
.serves {{ font-size:12.5px; margin-top:5px; }}
.ctx {{ font-size:12px; color:var(--muted); margin-top:3px; }}
.tags {{ font-size:11.5px; color:var(--muted); margin-top:5px; font-family:ui-monospace,Menlo,monospace;
  word-break:break-word; }}
.ev {{ margin-top:8px; border-top:1px dashed var(--line); padding-top:7px; font-size:12px; }}
.ax {{ margin:2px 0; }} .ax b {{ text-transform:uppercase; font-size:10.5px; letter-spacing:.04em;
  color:var(--muted); margin-right:4px; }}
.ax.raw {{ color:var(--muted); font-family:ui-monospace,Menlo,monospace; font-size:11.5px; }}
</style>
<div class="wrap">
<h1>New England parking — review</h1>
<p class="lede">The sparse-forest discovery run. Every public served lot across 5 areas, with its
Z2 NAIP aerial (0.21 m/px), OSM tags, and the serves gate. <b>Monadnock is judged</b>; the other
four are <b>un-judged</b> — tell me keep / drop / review and I'll write the verdicts.</p>
<div class="tally">
  <span class="chip keep"><b>{counts['keep']}</b> keep</span>
  <span class="chip drop"><b>{counts['drop']}</b> drop</span>
  <span class="chip review"><b>{counts['review']}</b> review</span>
  <span class="chip call"><b>{counts['call']}</b> your call</span>
</div>
{''.join(sections)}
</div>"""

OUT.write_text(PAGE)
print("wrote", OUT, f"{len(PAGE)/1e6:.2f} MB", "| counts", counts)
