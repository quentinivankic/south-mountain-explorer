#!/usr/bin/env python3
"""Build the review manifest for the deferred-review viewer (viewer/review.html).

Problem areas = published areas holding a trail we flagged FOR human review
tonight but deliberately did NOT auto-drop (too risky to filter by name):
  - rail-bare:        a bare rail-corridor name (Old Railroad Grade, a trolley
                      / traction-company line) with NO trail-word suffix — the
                      `…Trail` ones are already kept as real rail-trails.
  - route-ambiguous:  an E-Route, a 'State/Old/Historic/County/US Route' road
                      code, a stage/mail/bridle/horse route, or 'minor route'.

For context we don't use a mile radius (the 99 problems are spread nationwide,
so any radius swallows most of the country). Instead we include every area /
trail that TOUCHES a problem area's trails — the locally-connected network,
found by a coarse spatial-cell hash. That's what answers the review question:
does this flagged line connect into a real trail network (probably a genuine
hiked path) or sit isolated (probably junk)?

Writes viewer/review-manifest.json (slugs + flags only, no geometry — the
viewer fetches the published geom on demand). Run from the repo; read-only.

    python3 trailforge/viewer/build-review.py
"""
from __future__ import annotations

import glob
import json
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_GEOM = os.path.join(_ROOT, "public", "areas", "geom")
_INDEX = os.path.join(_ROOT, "public", "areas", "index.json")
_OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "review-manifest.json")

import re

_RAIL = re.compile(r"\b(trolley|traction company|railway|railroad|rail road)\b", re.I)
_TRAILWORD = re.compile(r"\b(trail|path|pathway|loop|greenway|connector|trace|spur)\b", re.I)
_ROUTE_AMB = re.compile(
    r"\bE-?route\b"
    r"|\b(state|old|historic|county|us|paper|stage|mail|bridle|horse) route\b"
    r"|\bminor route\b",
    re.I)

# ~0.0005 deg ≈ 55 m cells; a problem point stamps its cell + the 8 neighbours,
# so a trail counts as "touching" when it passes within ~1 cell (~55-110 m) of
# a problem-area trail. Loose enough to catch a real connection across two
# separately-assembled areas, tight enough not to pull in the whole county.
_CELL = 0.0005


def _flag(name: str) -> str | None:
    # A trail-word suffix ("… Trail / Path / Loop …") is the strongest "this is
    # a real named trail" signal — spare it from BOTH buckets. Without this,
    # 'Old Trolley Line trail' (a real rail-trail) and 'El Camino Real Historic
    # Route Trail' get wrongly flagged. Confirmed false positives on review.
    if _TRAILWORD.search(name):
        return None
    if _RAIL.search(name):
        return "rail-bare"
    if _ROUTE_AMB.search(name):
        return "route-ambiguous"
    return None


def _cell(lat: float, lon: float) -> tuple[int, int]:
    return (round(lat / _CELL), round(lon / _CELL))


def main() -> int:
    idx = json.load(open(_INDEX))
    center = {r[0]: (r[3], r[4]) for r in idx if r and len(r) > 4}
    name_of = {r[0]: (r[1], r[2]) for r in idx if r and len(r) > 2}

    # Pass 1: load every published (trailforge-clean) area's trails.
    areas: dict[str, list[dict]] = {}
    for f in glob.glob(os.path.join(_GEOM, "*.json")):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        if not isinstance(d, dict) or "cached_at" in d:
            continue
        slug = d.get("id")
        if not slug:
            continue
        areas[slug] = d.get("trails", [])

    # Pass 2: problem areas + the flagged trails inside them. Stamp EVERY trail
    # point of a problem area (not just flagged trails — a neighbour touching
    # any part of a problem area is context) plus the 8-neighbour ring, and
    # record WHICH problem area owns each cell so pass 3 can attribute a
    # touching neighbour to the specific problem area(s) it connects to.
    problem: dict[str, list[dict]] = {}
    cell_owner: dict[tuple[int, int], set[str]] = {}
    for slug, trails in areas.items():
        flags = []
        for t in trails:
            r = _flag(t.get("name") or "")
            if r:
                flags.append({"id": t.get("id"), "name": t.get("name"), "reason": r})
        if not flags:
            continue
        problem[slug] = flags
        for t in trails:
            for seg in t.get("segments", []):
                for pt in seg:
                    if len(pt) < 2:
                        continue
                    cy, cx = _cell(pt[0], pt[1])
                    for dy in (-1, 0, 1):
                        for dx in (-1, 0, 1):
                            cell_owner.setdefault((cy + dy, cx + dx), set()).add(slug)

    # Pass 3: any OTHER area whose trail passes through a problem cell is a
    # neighbour of every problem area owning that cell.
    # per_problem[problem_slug] -> {neighbor_slug: [touching trail ids]}
    per_problem: dict[str, dict[str, list[str]]] = {s: {} for s in problem}
    for slug, trails in areas.items():
        if slug in problem:
            continue
        for t in trails:
            owners: set[str] = set()
            for seg in t.get("segments", []):
                for pt in seg:
                    if len(pt) >= 2:
                        owners |= cell_owner.get(_cell(pt[0], pt[1]), set())
            for owner in owners:
                per_problem[owner].setdefault(slug, []).append(t.get("id"))

    def _rec(slug):
        nm, st = name_of.get(slug, (slug, ""))
        lat, lon = center.get(slug, (None, None))
        return {"slug": slug, "name": nm, "state": st, "lat": lat, "lon": lon}

    all_neighbors = {n for nb in per_problem.values() for n in nb}
    manifest = {
        "note": "Deferred-review viewer: each problem area holds a flagged "
                "rail-bare or route-ambiguous trail, and carries the neighbour "
                "areas/trails that TOUCH it (~55-110m). Geometry is fetched on "
                "demand from /public/areas/geom/.",
        "problem_areas": [
            {**_rec(s), "flagged": problem[s],
             "neighbors": [{**_rec(n), "touching": per_problem[s][n]}
                           for n in sorted(per_problem[s])]}
            for s in sorted(problem)
        ],
    }
    json.dump(manifest, open(_OUT, "w"), separators=(",", ":"))
    print(f"problem areas: {len(problem)} ({sum(len(v) for v in problem.values())} flagged trails)")
    print(f"distinct touching-neighbour areas: {len(all_neighbors)}")
    print(f"wrote {_OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
