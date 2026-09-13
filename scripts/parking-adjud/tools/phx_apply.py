#!/usr/bin/env python3
"""Map OSM-id-keyed verdicts (phx_verdicts_osm.json) onto each area's fids so
padjart2.py can build per-area artifacts. A lot's verdict is the same in every
area it appears in. Usage: python3 phx_apply.py"""
import json
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
AREAS=["echo-canyon-recreation-area-az","phoenix-mountains-preserve-az","pinnacle-peak-park-az","usery-mountain-regional-park-az"]
OV=json.load(open(f"{TMP}/phx_verdicts_osm.json"))  # {osm_primary: verdict-obj}
for slug in AREAS:
    dos=json.load(open(f"{TMP}/{slug}_dossier.json")); srv=json.load(open(f"{TMP}/{slug}_serves2.json"))
    V={}
    for f in dos["facilities"]:
        if not srv.get(str(f["fid"]),{}).get("served"): continue
        key=f["osm"][0]
        vv=OV.get(key)
        if not vv: continue
        V[str(f["fid"])]=dict(vv, fid=f["fid"], osm=f["osm"])
    json.dump(V,open(f"{TMP}/{slug}_verdicts2.json","w"),indent=0)
    from collections import Counter
    print(f"{slug}: {len(V)} verdicts {dict(Counter(v['verdict'] for v in V.values()))}")
