#!/usr/bin/env python3
"""Human review sheet for one area's judge verdicts.

Reads the area's judge packets (judge_packets.py output) plus every
`<slug>_verdict_draft_*.json` and writes ONE self-contained HTML page: a card
per lot with its Z1/Z2/Z3 frames embedded as JPEG thumbnails, the three axis
calls with the agents' evidence, prior / confidence / serves / walk / context,
and Google Maps + OpenStreetMap links at the lot's centroid. Filter buttons
show KEEP / DROP / REVIEW subsets; DROPs sort first by default so the
eyeballing pass is quick.

    PADJ_TMP=work/co python3 tools/judge_review_sheet.py <slug> [--out FILE]
                                                                  [--open]

Read-only: touches nothing but the output file (default
`$PADJ_TMP/<slug>_review.html`). Verdict drafts may be partial; unjudged
packets appear as "UNJUDGED" cards.
"""
import argparse
import base64
import glob
import html
import io
import json
import os
import subprocess
import sys
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
PADJ_TMP = os.environ.get("PADJ_TMP") or os.path.join(_HERE, "..", "work")

ORDER = {"DROP": 0, "REVIEW": 1, "UNJUDGED": 2, "KEEP": 3}
THUMB_PX = {"z1": 360, "z2": 560, "z3": 360}


def thumb(path, px):
    """Downscaled JPEG data URI for a tile, or None when the tile is missing."""
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - environment dependent
        sys.exit("Pillow is required (pip install Pillow)")
    if not path or not os.path.exists(path):
        return None
    im = Image.open(path).convert("RGB")
    im.thumbnail((px, px))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=74)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def esc(x):
    return html.escape("" if x is None else str(x))


def load_verdicts(tmp, slug):
    """Same draft discovery as merge_drafts.py: a single consolidated
    `<slug>_verdict_draft.json` if present, else the chunk drafts."""
    out = {}
    single = os.path.join(tmp, f"{slug}_verdict_draft.json")
    files = [single] if os.path.exists(single) else sorted(
        glob.glob(os.path.join(tmp, f"{slug}_verdict_draft_*.json")))
    for f in files:
        try:
            rows = json.load(open(f))
        except json.JSONDecodeError as e:
            print(f"warning: {f}: {e}", file=sys.stderr)
            continue
        for v in rows:
            out[v["fid"]] = v
    return out


def centroid(pkt, dossier_row):
    """Packets carry no position (footprint is just "polygon"/"node-only");
    the dossier row does."""
    if dossier_row and dossier_row.get("lat") is not None:
        return dossier_row["lat"], dossier_row["lon"]
    return None, None


def osm_link(osm_id):
    kind, num = osm_id.split("/", 1)
    return f"https://www.openstreetmap.org/{kind}/{num}"


def axis_row(name, ax):
    if not isinstance(ax, dict):
        return f"<tr><th>{name}</th><td class='call'>-</td><td>-</td></tr>"
    call = ax.get("call", "-")
    return (f"<tr><th>{name}</th><td class='call {esc(call).replace('/', '')}'>"
            f"{esc(call)}</td><td>{esc(ax.get('evidence'))}</td></tr>")


def card(pkt, v, dossier_row):
    fid = pkt["fid"]
    verdict = (v or {}).get("verdict") or "UNJUDGED"
    conf = (v or {}).get("confidence") or ""
    tags = pkt.get("tags_union") or {}
    name = tags.get("name") or tags.get("operator") or "(unnamed)"
    lat, lon = centroid(pkt, dossier_row)
    srv = pkt.get("serves") or {}
    walk = pkt.get("walk") or {}
    ctx = pkt.get("context") or {}
    tiles = pkt.get("tiles") or {}

    imgs = []
    for z in ("z1", "z2", "z3"):
        uri = thumb(tiles.get(z), THUMB_PX[z])
        cap = {"z1": "Z1 · 600 m", "z2": "Z2 · 220 m", "z3": "Z3 · 140 m"}[z]
        if uri:
            imgs.append(f"<figure class='{z}'><a href='file://{esc(tiles.get(z))}' "
                        f"target='_blank'><img loading='lazy' src='{uri}' "
                        f"alt='fid {fid} {z}'></a><figcaption>{cap}</figcaption></figure>")
        else:
            imgs.append(f"<figure class='{z}'><div class='notile'>no {z} tile</div>"
                        f"<figcaption>{cap}</figcaption></figure>")

    links = []
    if lat is not None:
        links.append(f"<a target='_blank' href='https://www.google.com/maps/search/?api=1&"
                     f"query={lat},{lon}'>Google Maps</a>")
        links.append(f"<a target='_blank' href='https://www.google.com/maps/@?api=1&map_action=map&"
                     f"center={lat},{lon}&zoom=19&basemap=satellite'>Google sat</a>")
    for oid in pkt.get("osm") or []:
        links.append(f"<a target='_blank' href='{osm_link(oid)}'>{esc(oid)}</a>")

    keytags = {k: tags[k] for k in ("amenity", "parking", "access", "fee", "surface",
                                    "capacity", "operator", "name") if k in tags}
    facts = [
        f"prior <b>{esc(pkt.get('prior'))}</b>",
        f"area {esc(pkt.get('area_m2'))} m²" if pkt.get("area_m2") else "node lot",
        (f"serves <b>{esc(srv.get('trail'))}</b> edge {esc(srv.get('edge_m'))} m"
         + (" <span class='fb'>FALLBACK</span>" if srv.get("fallback") else "")),
        f"walk {esc(walk.get('walk_m'))} m" + ("" if walk.get("conn") else " (no foot route)"),
        f"context {esc(ctx.get('category'))} {esc(ctx.get('evidence'))}".rstrip(),
        f"trailhead nodes {esc(pkt.get('trailhead_nodes_120m'))}" if pkt.get("trailhead_nodes_120m") else "",
        "building overlap" if pkt.get("building_overlap") else "",
        f"tags {esc(json.dumps(keytags, ensure_ascii=False))}" if keytags else "",
    ]
    facts = [f for f in facts if f]

    hint = (v or {}).get("resolve_hint")
    gap = (v or {}).get("coverage_gap")
    extra = ""
    if hint:
        extra += f"<p class='hint'><b>resolve:</b> {esc(hint)}</p>"
    if gap:
        extra += "<p class='hint'><b>coverage gap</b> flagged</p>"

    return f"""
<section class='card {verdict}' data-verdict='{verdict}' id='fid{fid}'>
  <header>
    <span class='badge'>{verdict}</span>
    <span class='conf'>{esc(conf)}</span>
    <span class='fid'>#{fid}</span>
    <span class='name'>{esc(name)}</span>
    <span class='links'>{' · '.join(links)}</span>
  </header>
  <div class='body'>
    <div class='frames'>{''.join(imgs)}</div>
    <div class='text'>
      <ul class='facts'>{''.join(f'<li>{f}</li>' for f in facts)}</ul>
      <table class='axes'>
        {axis_row('EXISTS', (v or {}).get('exists'))}
        {axis_row('PUBLIC', (v or {}).get('public'))}
        {axis_row('SERVES', (v or {}).get('serves'))}
      </table>
      {extra}
    </div>
  </div>
</section>"""


CSS = """
body{font:14px/1.4 -apple-system,Helvetica,Arial,sans-serif;margin:0;background:#f4f4f2;color:#222}
.top{position:sticky;top:0;background:#fff;border-bottom:1px solid #ccc;padding:10px 16px;z-index:9;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.top h1{font-size:16px;margin:0 12px 0 0}
.top button{border:1px solid #999;background:#fafafa;border-radius:4px;padding:4px 10px;cursor:pointer}
.top button.on{background:#222;color:#fff;border-color:#222}
.card{background:#fff;margin:14px 16px;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,.15);border-left:8px solid #999}
.card.DROP{border-left-color:#c0392b}.card.KEEP{border-left-color:#27ae60}.card.REVIEW{border-left-color:#e67e22}.card.UNJUDGED{border-left-color:#7f8c8d}
.card header{display:flex;gap:12px;align-items:baseline;padding:10px 14px;border-bottom:1px solid #eee;flex-wrap:wrap}
.badge{font-weight:700;padding:2px 8px;border-radius:4px;color:#fff;background:#999}
.DROP .badge{background:#c0392b}.KEEP .badge{background:#27ae60}.REVIEW .badge{background:#e67e22}
.conf{color:#666}.fid{font-family:Menlo,monospace}.name{font-weight:600}.links{margin-left:auto;font-size:13px}
.body{display:flex;gap:14px;padding:12px 14px;flex-wrap:wrap}
.frames{display:flex;gap:8px;align-items:flex-start}
figure{margin:0;text-align:center;font-size:12px;color:#666}
figure img{display:block;border:1px solid #ddd;border-radius:4px}
.z1 img,.z3 img{width:360px}.z2 img{width:560px}
.notile{width:300px;height:200px;display:flex;align-items:center;justify-content:center;background:#eee;color:#999;border-radius:4px}
.text{flex:1;min-width:360px}
.facts{margin:0 0 10px;padding-left:18px;color:#444}
.axes{border-collapse:collapse;width:100%}.axes th{text-align:left;vertical-align:top;padding:4px 8px 4px 0;width:64px}
.axes td{vertical-align:top;padding:4px 6px;border-top:1px solid #eee}
.call{font-weight:700;width:60px}.call.no{color:#c0392b}.call.yes{color:#27ae60}.call.unclear{color:#e67e22}
.fb{color:#c0392b;font-weight:700}.hint{background:#fff6e5;padding:6px 10px;border-radius:4px}
.hidden{display:none}
"""

JS = """
const btns=[...document.querySelectorAll('.top button[data-f]')];
function apply(f){document.querySelectorAll('.card').forEach(c=>{c.classList.toggle('hidden',f!=='ALL'&&c.dataset.verdict!==f)});
 btns.forEach(b=>b.classList.toggle('on',b.dataset.f===f));location.hash='';}
btns.forEach(b=>b.addEventListener('click',()=>apply(b.dataset.f)));
apply(new URLSearchParams(location.search).get('f')||'ALL');
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slug")
    ap.add_argument("--out", help="output HTML (default $PADJ_TMP/<slug>_review.html)")
    ap.add_argument("--open", action="store_true", help="open the page when done (macOS `open`)")
    a = ap.parse_args()

    tmp = PADJ_TMP
    pk_path = os.path.join(tmp, f"{a.slug}_packets.json")
    if not os.path.exists(pk_path):
        sys.exit(f"no packets at {pk_path}; run judge_packets.py first")
    packets = {int(k): v for k, v in json.load(open(pk_path)).items()}
    verdicts = load_verdicts(tmp, a.slug)
    dossier = {}
    dpath = os.path.join(tmp, f"{a.slug}_dossier.json")
    if os.path.exists(dpath):
        d = json.load(open(dpath))
        rows = d.get("facilities", d) if isinstance(d, dict) else d
        for r in rows:
            if isinstance(r, dict) and "fid" in r:
                dossier[r["fid"]] = r

    counts = {}
    ordered = []
    for fid, pkt in sorted(packets.items()):
        v = verdicts.get(fid)
        verdict = (v or {}).get("verdict") or "UNJUDGED"
        counts[verdict] = counts.get(verdict, 0) + 1
        ordered.append((ORDER.get(verdict, 9), fid, pkt, v))
    ordered.sort(key=lambda t: (t[0], t[1]))

    cards = "".join(card(pkt, v, dossier.get(fid)) for _, fid, pkt, v in ordered)
    summary = " · ".join(f"{k} {counts[k]}" for k in ("DROP", "REVIEW", "UNJUDGED", "KEEP") if k in counts)
    buttons = "".join(f"<button data-f='{k}'>{k} ({counts.get(k, 0)})</button>"
                      for k in ("ALL", "DROP", "REVIEW", "KEEP", "UNJUDGED")
                      if k == "ALL" or counts.get(k))
    buttons = buttons.replace(f"ALL ({counts.get('ALL', 0)})", f"ALL ({len(packets)})")

    page = f"""<!doctype html><html><head><meta charset='utf-8'>
<title>{esc(a.slug)} parking judge review</title><style>{CSS}</style></head><body>
<div class='top'><h1>{esc(a.slug)}</h1><span>{len(packets)} lots · {summary}</span>{buttons}
<span style='margin-left:auto;color:#666'>red = mapped lot · yellow = shipped trails · orange = other lots · click a frame for full size</span></div>
{cards}
<script>{JS}</script></body></html>"""

    out = a.out or os.path.join(tmp, f"{a.slug}_review.html")
    Path(out).write_text(page, encoding="utf-8")
    print(f"wrote {out} ({os.path.getsize(out) / 1e6:.1f} MB): {len(packets)} lots, {summary}")
    if a.open:
        subprocess.run(["open", out], check=False)


if __name__ == "__main__":
    main()
