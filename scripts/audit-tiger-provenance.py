#!/usr/bin/env python3
"""Report which published areas are dominated by TIGER-derived "trails".

An AFTER-THE-FACT REPORT, not a gate — same posture as
`audit-easement-ownership.py`. It never edits geom.

## Why

The 2007 TIGER import loaded every US road and railway into OSM. In places where
nobody has since cleaned it up, those road ways got retagged `highway=path` and
given names years later, and our pipeline ingests them as hiking trails.

Worked example — "Trail 463", OSM way 5728825 (Helena-Lewis and Clark NF):
    v1 2007  DaveHansenTiger  highway=residential, NO NAME  (bulk TIGER import)
    v2 2014  bal_agates       -> highway=track, tracktype=grade4, still unnamed
    v3 2020  davidfg4         -> highway=path, motor_vehicle=no, named "Trail 463"
A census rural road (`tiger:cfcc=A41`), named 13 years after import. A user who
researched it against OSM, Google Maps and satellite concluded it is not a
hiking trail: a loop with no viewpoint and no destination. AllTrails has no
entry for it.

## Why this is worth measuring rather than assuming

Prevalence is LOCAL, not regional — it tracks whether local mappers cleaned up
TIGER, and varies wildly between neighbouring forests (measured 2026-07-20,
named path|footway|track ways):

    Little Belt / Judith MT   117 named, 111 tiger = 95%   (87 are A41 = ROAD)
    Ozark NF, AR               70 named,  34 tiger = 49%
    Adirondacks NY            469 named,  85 tiger = 18%
    South Mountain, Phoenix   233 named,   8 tiger =  3%
    Absaroka-Beartooth MT     190 named,   4 tiger =  2%   <- also Montana
    Great Smoky Mountains NP  116 named,   0 tiger =  0%

Real hiking areas sit near zero. So a high ratio flags an area whose "trail
network" is really an unimproved road grid.

## What this deliberately does NOT do

It does not drop anything, and no rule here should become an auto-drop without a
real example plus a test (see CLAUDE.md's curation policy — speculative pattern
enumeration is a proven dead end here).

Two reasons to stay cautious:
  * `tiger:reviewed=no` is a POOR quality proxy — the OSM wiki is explicit that
    mappers remove it inconsistently. This tool keys on `tiger:cfcc` instead,
    which records what the census CLASSIFIED the feature as, and reports
    `reviewed` only as context.
  * TIGER provenance can coexist with genuine ground truth. Trail 463's own 2020
    edit carries `source: Bing; survey` — someone was physically there. A naive
    "drop anything with tiger:cfcc" rule would delete surveyed trails.

    python3 scripts/audit-tiger-provenance.py --area helena-lewis-and-clark-national-forest-mt
    python3 scripts/audit-tiger-provenance.py --all --min-trails 25 --limit 40
"""
from __future__ import annotations
import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

GEOM = Path(__file__).resolve().parent.parent / "public" / "areas" / "geom"
_EPS = ("https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter")

# TIGER Census Feature Class Codes worth naming in the report. A4x/A5x are
# ROADS — a named `highway=path` carrying one came from a road import.
CFCC_MEANING = {
    "A41": "local/rural ROAD",
    "A45": "ROAD w/ special restriction",
    "A51": "4WD vehicular trail",
    "A61": "cul-de-sac / access ROAD",
    "A71": "walkway/trail (genuinely a path)",
    "A73": "alley",
}


def overpass(query: str, timeout: int = 120) -> dict | None:
    """One Overpass call with endpoint rotation. Returns None on total failure —
    the caller reports the area as unmeasured rather than inventing a ratio."""
    for ep in _EPS:
        for attempt in range(2):
            try:
                req = urllib.request.Request(
                    ep, data=urllib.parse.urlencode({"data": query}).encode(),
                    headers={"User-Agent": "trekdex-tiger-audit/1.0"})
                return json.loads(urllib.request.urlopen(req, timeout=timeout).read())
            except Exception as e:  # noqa: BLE001
                print(f"    overpass attempt {attempt + 1} failed ({type(e).__name__})",
                      file=sys.stderr)
                time.sleep(6 * (attempt + 1))
    return None


def bbox_of(geom: dict) -> tuple[float, float, float, float] | None:
    """(south, west, north, east) from the area's own bbox, else from its trails.
    The stored bbox is [minLon, minLat, maxLon, maxLat]."""
    b = geom.get("bbox")
    if b and len(b) == 4:
        return (b[1], b[0], b[3], b[2])
    lats, lons = [], []
    for t in geom.get("trails") or []:
        for s in t.get("segments") or []:
            for p in s:
                if len(p) >= 2:
                    lats.append(p[0]); lons.append(p[1])
    if not lats:
        return None
    return (min(lats), min(lons), max(lats), max(lons))


def audit(area_id: str, pause: float = 3.0) -> dict | None:
    f = GEOM / f"{area_id}.json"
    if not f.exists():
        print(f"  {area_id}: no geom", file=sys.stderr)
        return None
    geom = json.loads(f.read_text())
    bb = bbox_of(geom)
    if not bb:
        return None
    s, w, n, e = bb
    box = f"{s},{w},{n},{e}"
    base = f'way[highway~"^(path|footway|track)$"][name]({box})'
    total = overpass(f"[out:json][timeout:90];{base};out count;")
    time.sleep(pause)
    tiger = overpass(f'[out:json][timeout:90];{base}["tiger:cfcc"];out count;')
    if total is None or tiger is None:
        return None

    def n_ways(d):
        els = d.get("elements") or []
        return int(els[0]["tags"]["ways"]) if els and "tags" in els[0] else 0

    tot, tig = n_ways(total), n_ways(tiger)
    return {
        "area": area_id,
        "name": geom.get("name"),
        "shipped_trails": len(geom.get("trails") or []),
        "osm_named_ways": tot,
        "tiger_ways": tig,
        "pct": (tig / tot * 100) if tot else 0.0,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--area", help="one area id")
    g.add_argument("--all", action="store_true", help="every published area")
    ap.add_argument("--min-trails", type=int, default=10,
                    help="skip areas with fewer shipped trails (default 10)")
    ap.add_argument("--limit", type=int, help="stop after N areas (sampling)")
    ap.add_argument("--flag-pct", type=float, default=40.0,
                    help="report areas at or above this %% as REVIEW (default 40)")
    args = ap.parse_args()

    if args.area:
        ids = [args.area]
    else:
        ids = []
        for f in sorted(GEOM.glob("*.json")):
            try:
                d = json.loads(f.read_text())
            except Exception:  # noqa: BLE001
                continue
            if len(d.get("trails") or []) >= args.min_trails:
                ids.append(f.stem)
        if args.limit:
            ids = ids[:args.limit]

    print(f"auditing {len(ids)} area(s); flagging >= {args.flag_pct:.0f}% TIGER\n",
          file=sys.stderr)
    rows, unmeasured = [], []
    for i, aid in enumerate(ids, 1):
        r = audit(aid)
        if r is None:
            unmeasured.append(aid)
            continue
        rows.append(r)
        print(f"  [{i}/{len(ids)}] {aid[:44]:46} {r['pct']:>5.0f}%  "
              f"({r['tiger_ways']}/{r['osm_named_ways']})", file=sys.stderr)

    rows.sort(key=lambda r: -r["pct"])
    print(f"\n{'pct':>5}  {'tiger/named':>13}  {'shipped':>7}  area")
    for r in rows:
        mark = "REVIEW" if r["pct"] >= args.flag_pct else "      "
        print(f"{r['pct']:>4.0f}% {mark} {r['tiger_ways']:>5}/{r['osm_named_ways']:<7} "
              f"{r['shipped_trails']:>7}  {r['area']}")
    flagged = [r for r in rows if r["pct"] >= args.flag_pct]
    print(f"\nmeasured {len(rows)}; {len(flagged)} at or above {args.flag_pct:.0f}% TIGER")
    if unmeasured:
        # Never let a failed fetch masquerade as a clean result.
        print(f"UNMEASURED (fetch failed): {len(unmeasured)} — {unmeasured[:6]}")


if __name__ == "__main__":
    main()
