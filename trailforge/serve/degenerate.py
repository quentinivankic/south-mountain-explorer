"""Drop trails that ship with ~zero length, keeping the ones the network needs.

WHY THESE EXIST. Publishing clips every trail to the area's boundary polygon. A
trail that lies mostly OUTSIDE the polygon can clip down to a sliver or, when
only a single vertex falls inside, to a point — start coordinate equal to end
coordinate, 0.00 mi. Found in Au Sable State Forest's Grayling Unit: Gorget
Trail, Tabitha Trail and West Gobblers all shipped at 0.00 mi.

WHY IT MATTERS. They are named rows in the trail list that pad an area's count
and can never be walked or completed, which is exactly the wrong thing for an app
whose whole premise is completing every trail in an area. `publish_areas.py`
already refuses to publish an AREA whose trails clip to under `_MIN_AREA_MI`
(0.1 mi) — there was simply no equivalent gate per TRAIL.

WHY NOT ALL OF THEM. A ~zero-length way can be load-bearing: a short link at a
junction is what makes two real trails one connected network. The example that
taught this is a 0.11 mi "(Nordic)" way in a ski loop — a real connector, and a
blind length filter would have cut the loop in half. So classification is by
CONNECTIVITY, not length alone:

    connector  both endpoints shared with another trail  -> KEEP
    spur       one endpoint shared                       -> drop
    isolated   neither endpoint shared                   -> drop
    noseg      no segment with 2+ points at all          -> drop

JUNCTION TOLERANCE is the one real judgment call. Endpoints are compared rounded
to `_JUNCTION_DP` decimal places — 4 dp is about 11 m at US latitudes. Exact
vertex identity (6 dp, ~0.1 m) is the strictly truthful test of OSM topology,
since ways that meet share a node, and it classifies 289 trails as droppable
rather than 251. The extra 38 are cases where two endpoints sit within ~11 m but
are not the same node: a sloppily-drawn junction, or two genuinely separate
trail ends. We cannot tell which from geometry, so the tolerant reading wins and
those 38 stay. This matches the existing pipeline stance that junction snapping
in the tens of metres is legitimate while >50 m fuses unconnected trails.

Deliberately NOT a length floor above 0.01 mi (~16 m). Measured earlier: dropping
everything under 0.15 mi would empty 6 areas outright and cut 30%+ of the trails
in 31 more — real short preserve loops live in the 0.1-0.3 mi band. 0.01 mi only
catches geometry that is a point or a few metres of clipped remnant.

Shared by `publish_areas.py` (so a republish never re-creates them) and
`scripts/sweep-degenerate-trails.py` (so already-published geom gets cleaned
without waiting for a republish, including areas the publisher skips for lack of
a boundary). One implementation, so the two cannot drift.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict

_MIN_TRAIL_MI = 0.01      # ~16 m: a point, or a few metres of clipped remnant
_JUNCTION_DP = 4          # ~11 m — see JUNCTION TOLERANCE above
_R_MI = 3958.8

KEEP = ""                 # falsy: this trail stays


def _seg_miles(segments) -> float:
    tot = 0.0
    for seg in segments or []:
        for i in range(1, len(seg)):
            a, b = seg[i - 1], seg[i]
            if len(a) < 2 or len(b) < 2:
                continue
            lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
            h = (math.sin((lat2 - lat1) / 2) ** 2
                 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
            tot += 2 * _R_MI * math.asin(min(1.0, math.sqrt(h)))
    return tot


def trail_miles(trail: dict) -> float:
    """True geometry length in miles. Deliberately recomputed rather than read
    from `distanceMi`, which a stale or hand-edited geom can disagree with."""
    return _seg_miles(trail.get("segments"))


def area_miles(trails: list[dict]) -> float:
    """The area total, computed the way `to_app_json.convert` computes it —
    `round(sum(distanceMi), 1)` over the per-trail values the app actually
    displays, NOT a fresh haversine over the geometry. Those two disagree in the
    last decimal, and an area total that doesn't equal the sum of its visible
    trail rows is a bug report waiting to happen. Falls back to geometry for a
    trail with no `distanceMi`."""
    tot = 0.0
    for t in trails:
        d = t.get("distanceMi")
        tot += float(d) if isinstance(d, (int, float)) else trail_miles(t)
    return round(tot, 1)


def _node(p) -> tuple[float, float]:
    return (round(p[0], _JUNCTION_DP), round(p[1], _JUNCTION_DP))


def classify(trails: list[dict]) -> list[str]:
    """Per-trail verdict, positionally aligned with `trails`: KEEP ("") or one of
    "isolated" / "spur" / "noseg". Only trails under `_MIN_TRAIL_MI` are ever
    judged; everything else is KEEP."""
    owners: dict[tuple[float, float], set[int]] = defaultdict(set)
    for i, t in enumerate(trails):
        for seg in t.get("segments") or []:
            for p in seg:
                if len(p) >= 2:
                    owners[_node(p)].add(i)

    out: list[str] = []
    for i, t in enumerate(trails):
        if trail_miles(t) >= _MIN_TRAIL_MI:
            out.append(KEEP)
            continue
        segs = [s for s in (t.get("segments") or []) if len(s) >= 2]
        if not segs:
            out.append("noseg")
            continue
        ends = (_node(segs[0][0]), _node(segs[-1][-1]))
        shared = sum(1 for e in ends if any(o != i for o in owners[e]))
        # Both ends attached => this stub IS the junction. Keep it.
        out.append(KEEP if shared >= 2 else ("spur" if shared == 1 else "isolated"))
    return out


def prune(trails: list[dict]) -> tuple[list[dict], Counter]:
    """`(kept_trails, {reason: n})`. Never empties a list that had trails: if
    every trail in an area is degenerate, the area's problem is the boundary, not
    the trails, and silently returning nothing would make the area vanish from
    the app. `_MIN_AREA_MI` in publish_areas.py is the gate for that case."""
    verdicts = classify(trails)
    kept = [t for t, v in zip(trails, verdicts) if not v]
    if not kept:
        return list(trails), Counter()
    return kept, Counter(v for v in verdicts if v)
