#!/usr/bin/env python3
"""Golden-trail evaluation logic (pure) — SPEC.md §5.

`evaluate(entry, feature_collection)` scores one golden trail against an
assembled trails GeoJSON. Kept separate from the osmium/AOI orchestration
(run_golden.py) so the pass/fail logic is unit-testable without OSM data.

Assertions:
  destination: some ONE trail's geometry reaches within reach_tolerance_ft.
  through:     some ONE trail comes within endpoint_tolerance_mi of BOTH
               endpoints (the route is a single object, not fragments).
  length:      if expected_one_way_mi is set, the reaching trail's length
               is within length_tolerance_pct.
  fragments:   how many DISTINCT trails reach the destination (want 1).
"""
from __future__ import annotations

import math
from typing import Any


def _hav_ft(a, b) -> float:
    lon1, lat1 = a
    lon2, lat2 = b
    r = 3958.7613 * 5280
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(h)))


def _points(feat) -> list[tuple[float, float]]:
    g = feat.get("geometry") or {}
    t, c = g.get("type"), g.get("coordinates") or []
    if t == "LineString":
        return [tuple(p) for p in c]
    if t == "MultiLineString":
        return [tuple(p) for line in c for p in line]
    return []


def _closest_ft(feat, coord) -> float:
    pts = _points(feat)
    return min((_hav_ft(p, coord) for p in pts), default=float("inf"))


def evaluate(entry: dict, fc: dict, defaults: dict | None = None) -> dict:
    """Score one golden trail, then attach OSM coverage + a diagnosis so a
    FAIL says *why* — missing data vs assembly gap."""
    res = _score(entry, fc, defaults)
    res["coverage"] = fc.get("coverage")
    res["diagnosis"] = diagnose(res)
    return res


def _score(entry: dict, fc: dict, defaults: dict | None = None) -> dict:
    d = defaults or {}
    reach_ft = entry.get("reach_tolerance_ft", d.get("reach_tolerance_ft", 200))
    ep_mi = entry.get("endpoint_tolerance_mi", d.get("endpoint_tolerance_mi", 1.0))
    len_pct = entry.get("length_tolerance_pct", d.get("length_tolerance_pct", 30))
    feats = fc.get("features", [])
    res: dict[str, Any] = {"id": entry["id"], "name": entry["name"],
                           "kind": entry["kind"], "passed": False, "reasons": []}

    if entry["kind"] == "destination":
        dest = (entry["destination"]["lon"], entry["destination"]["lat"])
        best = min((_closest_ft(f, dest) for f in feats), default=float("inf"))
        res["closest_ft"] = round(best) if best != float("inf") else None
        reaching = [(f, _closest_ft(f, dest)) for f in feats]
        reaching = [(f, ft) for f, ft in reaching if ft <= reach_ft]
        res["fragments"] = len(reaching)
        if not reaching:
            res["reasons"].append(f"no trail within {reach_ft:.0f} ft "
                                  f"(closest {best:.0f} ft)")
            return res
        feat, ft = min(reaching, key=lambda x: x[1])
        res["reach_ft"] = round(ft)
        res["matched"] = feat["properties"].get("name")
        res.update(_length_check(feat, entry, len_pct, res))
        res["passed"] = not res["reasons"]
        return res

    # through
    a = (entry["endpoints"][0]["lon"], entry["endpoints"][0]["lat"])
    b = (entry["endpoints"][1]["lon"], entry["endpoints"][1]["lat"])
    tol_ft = ep_mi * 5280
    connectors = [f for f in feats
                  if _closest_ft(f, a) <= tol_ft and _closest_ft(f, b) <= tol_ft]
    res["fragments"] = len(connectors)
    if not connectors:
        res["reasons"].append(f"no single trail within {ep_mi} mi of both endpoints")
        return res
    feat = max(connectors, key=lambda f: f["properties"].get("length_mi", 0))
    res["matched"] = feat["properties"].get("name")
    res.update(_length_check(feat, entry, len_pct, res))
    res["passed"] = not res["reasons"]
    return res


def diagnose(res: dict) -> str:
    """Turn a fail into a cause. 'missing-*' = OSM data gap (edit OSM, not
    code). 'assembly-gap' / 'no-route-relation' = our side to tune."""
    if res.get("passed"):
        return "ok"
    cov = res.get("coverage") or {}
    ways = cov.get("raw_trailish_ways")
    rels = cov.get("hiking_route_relations", 0)
    pois = cov.get("destination_pois")
    if ways is not None and ways < 5:
        return "missing-data: few/no trail ways mapped in this AOI (edit OSM)"
    if res["kind"] == "through":
        if rels == 0:
            return "no-route-relation: the long route isn't tagged as a relation in OSM"
        return "assembly-gap: route relation exists but didn't stitch end-to-end (tune assembler)"
    # destination
    if pois == 0:
        return "missing-poi: destination POI not in OSM to anchor to (add it to OSM)"
    cf = res.get("closest_ft")
    if cf is not None and cf <= 1320:      # within 1/4 mi of the payoff
        return "assembly-gap: a trail ends near the payoff but didn't reach it (tune weld thresholds)"
    return "missing-data: nearest trail is far from the destination — likely unmapped here"


def _length_check(feat, entry, len_pct, res) -> dict:
    exp = entry.get("expected_one_way_mi")
    got = feat["properties"].get("length_mi")
    if exp and got is not None:
        lo, hi = exp * (1 - len_pct / 100), exp * (1 + len_pct / 100)
        res["length_mi"] = got
        if not (lo <= got <= hi):
            res["reasons"].append(f"length {got:.1f} mi outside {lo:.1f}-{hi:.1f}")
    return {}


def summarize(results: list[dict]) -> str:
    lines = ["", f"{'trail':30} {'result':6} {'diagnosis':14} detail", "-" * 96]
    npass = 0
    buckets: dict[str, int] = {}
    for r in results:
        ok = r["passed"]
        npass += ok
        diag = (r.get("diagnosis") or "").split(":")[0]
        buckets[diag if not ok else "ok"] = buckets.get(diag if not ok else "ok", 0) + 1
        cov = r.get("coverage") or {}
        detail = (f"reach {r.get('reach_ft','?')}ft" if ok and r["kind"] == "destination"
                  else "; ".join(r["reasons"]) if r["reasons"]
                  else f"frags {r.get('fragments','?')}")
        if not ok and cov:
            detail += f"  (ways={cov.get('raw_trailish_ways')}, " \
                      f"routes={cov.get('hiking_route_relations')}, pois={cov.get('destination_pois')})"
        lines.append(f"{r['name'][:30]:30} {'PASS' if ok else 'FAIL':6} "
                     f"{diag:14} {detail}")
    lines.append("-" * 96)
    lines.append(f"{npass}/{len(results)} passed   " +
                 "  ".join(f"{k}:{v}" for k, v in sorted(buckets.items())))
    return "\n".join(lines)
