#!/usr/bin/env python3
"""Trail-object assembly — the core algorithm (SPEC.md §2), pure Python.

No pyosmium here: this operates on plain dicts so it is fully unit-testable
without OSM data. `assemble.py` is the thin pyosmium reader that loads a
.osm.pbf into these structures, calls assemble(), and writes GeoJSON.

Input structures (built by the reader):
  nodes:     {node_id: (lon, lat)}
  ways:      {way_id: {"tags": {...}, "nodes": [node_id, ...]}}
  relations: {rel_id: {"tags": {...}, "members": [(type, ref, role), ...]}}
  pois:      [{"id": int, "coord": (lon, lat), "tags": {...}, "name": str|None}, ...]

Output: list[Trail] — one per assembled trail object (SPEC.md §2.4).

Algorithm order (SPEC.md §2, all steps confirmed by research):
  1. relations-first  — route relations are trail objects; member ORDER
     matters; superrelations resolve recursively; ROLES separate the
     canonical line (main) from spurs (approach/connection) and variants
     (alternative/excursion).
  2. name-stitch      — remaining named ways group by normalized name,
     stitched across highway-type boundaries (path->steps->path) via shared
     endpoint nodes.
  3. spur-attach      — an unnamed/steps segment that connects to exactly
     one assembled trail AND terminates at a destination POI is welded on
     (the missing 838 ft of Devils Bridge).
"""
from __future__ import annotations

import math
import re
import sys
import unicodedata
from typing import Any

HIKING_ROUTE_KINDS = {"hiking", "foot", "walking", "running"}
MAIN_ROLES = {"", "main"}
SPUR_ROLES = {"approach", "connection"}
VARIANT_ROLES = {"alternative", "excursion", "alternate"}

# Destination POI signatures — a way ending at one of these is "reaching
# the payoff" (SPEC.md §1). Keyed (tag, value); value None means any.
DESTINATION_POIS = {
    ("natural", "peak"), ("natural", "arch"), ("natural", "volcano"),
    ("natural", "saddle"), ("natural", "cliff"), ("natural", "hot_spring"),
    ("waterway", "waterfall"), ("tourism", "viewpoint"),
    ("tourism", "alpine_hut"), ("tourism", "wilderness_hut"),
    ("mountain_pass", "yes"),
}

# Spur-attach guardrails.
SPUR_MAX_MI = 0.6            # a payoff spur is short; don't weld long ways
SPUR_POI_REACH_FT = 250      # spur endpoint must land this close to a POI


def haversine_mi(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle miles between (lon, lat) points."""
    (lon1, lat1), (lon2, lat2) = a, b
    r = 3958.7613
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(h)))


def line_mi(coords: list[tuple[float, float]]) -> float:
    return sum(haversine_mi(coords[i], coords[i + 1]) for i in range(len(coords) - 1))


def norm_name(name: str | None) -> str:
    """Canonical key for name-stitching: NFC, casefold, collapse spaces.

    Keeps non-Latin scripts intact (Mt Fuji 富士山 stays 富士山) — we group
    by the canonical `name`, not name:en.
    """
    if not name:
        return ""
    n = unicodedata.normalize("NFC", name).casefold().strip()
    return " ".join(n.split())


def display_name(tags: dict[str, str]) -> str | None:
    """Human-facing name: canonical name, else localized/en fallback."""
    if tags.get("name"):
        return tags["name"]
    for k, v in tags.items():
        if k.startswith("name:") and v:
            return v
    return None


def merge_key(name: str | None) -> str:
    """Grouping key for the same-trail merge — looser than the display name.

    Folds hyphens→spaces and drops a trailing 'Trail' so OSM's inconsistent
    tagging of one trail collapses: 'Alta' == 'Alta Trail', 'Ma-Ha-Tuak' ==
    'Ma Ha Tuak'. Distinct trails stay distinct ('Mormon' vs 'Mormon Loop',
    'Alta' vs 'West Alta').
    """
    k = " ".join(norm_name(name).replace("-", " ").split())
    if k.endswith(" trail"):
        k = k[:-6].rstrip()
    return k


def is_closed_name(name: str | None) -> bool:
    """A trail whose name flags it closed (e.g. 'CLOSED - old Pyramid Trail')."""
    return bool(name) and "closed" in norm_name(name).split()


# Names that are pure generic category words — no identity, so a useless
# completion-checklist item ("complete: Trail" — which one?). These are also
# what fuse into the region-scale "Trail" blobs. Dropped in curation UNLESS a
# route relation carries the object (relations reflect human curation).
_GENERIC_NAMES = {
    "trail", "path", "trailhead", "loop", "the loop", "loop trail",
    "connector", "connector trail", "nature trail", "spur", "cutoff",
    "shortcut", "access", "access trail", "main trail", "upper loop",
    "lower loop", "unnamed", "trail 1", "trail 2", "hiking trail",
}


def is_generic_name(name: str | None) -> bool:
    """The name is nothing but a generic trail-category word — no identity."""
    return norm_name(name) in _GENERIC_NAMES


# network grades that mark a long-distance thru-route (vs a local trail).
_ROUTE_NETWORKS = {"rwn", "nwn", "iwn"}
_ROUTE_WORD = re.compile(r"\broute\b", re.IGNORECASE)

# Bike-park vocabulary in a trail NAME — only trusted when the way is also
# IMBA-rated (see _is_nonhiking), so a lava 'Obsidian Flow' hiking trail (no
# imba) or 'Mayflower' (not the word 'flow') is never caught.
_BIKEPARK_NAME = re.compile(
    r"\b(down\s*hill|dh|slalom|flow|jump\s*line|pump\s*track|berm|freeride)\b",
    re.IGNORECASE)

# A name that literally says MTB / mountain bike — 'Party of 5 (MTB)', 'Yellow
# MTB Trail', 'Thomas Mountain Bike Trail'. Unlike _BIKEPARK_NAME this needs no
# mtb:scale:imba co-signal: whole-word 'MTB' or 'mountain bike' never appears
# in a genuine hiking-trail name by accident (a whole bike-park network audit
# — Adirondack, Blue Knob, Montgomery Bell — found this exact naming pattern
# on every entry, 0 false positives). The one guard that matters is a slash or
# ' and ' in the name: 'Ryan Gulch MTB/Hiking Trail' explicitly declares dual
# use, and 'X Trail and Y Mountain Bike Trail' is a merge artifact (two ways
# fused under a concatenated name) — neither should silently drop a trail that
# may still be a real, walkable hike.
_BIKE_ONLY_NAME = re.compile(r"\bmtb\b|\bmountain\s*bike\b", re.IGNORECASE)
_BIKE_NAME_COMPOSITE = re.compile(r"/| and ", re.IGNORECASE)

# Famous long-distance thru-hikes, matched by NAME. Tag-driven detection fails
# here — OSM tags US thru-hikes inconsistently (no network, the name slapped on
# ways, flat/nameless relations), so the name is the only reliable signal. A
# trail literally named one of these IS the thru-hike; its numbered segments
# ('Colorado Trail (Segment 5)') and name-stitched pieces ('Continental Divide
# Trail') all match. A curated registry, not fuzzy guessing — extend per region
# as coverage grows (Te Araroa, GR/E-paths, …).
_THRU_HIKE_RE = re.compile(
    r"\b("
    r"arizona (national )?(scenic )?trail|azt"
    r"|pacific crest trail|pct"
    r"|continental divide (national )?(scenic )?(trail|nst)|cdt|cdnst"
    r"|colorado trail"
    r"|hayduke"
    r"|grand enchantment"
    r"|sky islands traverse"
    r"|maricopa trail"
    r"|great western trail"
    r"|appalachian trail"
    r"|john muir trail|jmt"
    r"|pacific northwest trail|pnt"
    r"|tahoe rim trail"
    r"|oregon coast trail"
    r"|idaho centennial trail"
    # New England — distinctive names that don't collide with local trails
    r"|new england trail"
    r"|metacomet"
    r"|cohos trail"
    r"|wapack trail"
    r"|mid\s*state trail"
    r"|monadnock[\s-]sunapee"
    r"|ragged[\s-]kearsarge"        # Sunapee-Ragged-Kearsarge Greenway (NH)
    # Western long-distance trails — the FULL distinctive name only. Bare
    # 'Highline Trail' (Tonto #31, Mogollon Rim, Mt Adams #114) and 'Fremont'
    # (Mount Fremont Lookout, Fremont River) are unrelated local trails.
    r"|uinta highline trail"
    r"|fremont (national recreation trail|nrt)"
    r")\b"
    # NET only in its real abbreviated forms ('NET/M&M Trail', 'NET Trail
    # (white)', 'Metacomet Trail (NET)') — never a bare 'Net' word, which would
    # eat 'Net Zero' and 'Lazy H Horse Trail Net'.
    r"|\bnet\s*/|\bnet trail\b|\(net\)",
    re.IGNORECASE)

# Region-scoped thru-hike names — currently EMPTY by policy. A trail that lives
# in one park IS that park's trail, however long, so contained single-park
# thru-hikes are KEPT regardless of length (Vermont's 'Long Trail' 244mi in
# Green Mountain NF, NM's 'Skyline Trail' 62mi in the Pecos, like Wonderland
# 93mi / Tonto 91mi / Northville-Placid 124mi). The always-match _THRU_HIKE_RE
# still drops the genuinely CROSS-PARK named routes (AT/PCT/CDT/…) that smear
# across many areas with no home. The `region` hook stays wired so a future
# name that must be scoped to its owning state can be added here without a
# signature change — e.g. `(frozenset({"xx"}), re.compile(r"^foo\b", re.I))`.
_THRU_HIKE_REGIONAL: tuple = ()


def is_thru_hike_name(name: str | None, region: str | None = None) -> bool:
    """True if the name is a famous long-distance thru-hike (or a numbered
    segment / name-stitched piece of one) that SPANS parks and should be dropped
    from the per-park checklist. Distinctive cross-park names (_THRU_HIKE_RE)
    match anywhere; `region` (a 2-letter state code) enables any region-scoped
    names in _THRU_HIKE_REGIONAL (empty by policy — contained trails are kept)."""
    n = name or ""
    if _THRU_HIKE_RE.search(n):
        return True
    if region:
        r = region.lower()
        for states, rx in _THRU_HIKE_REGIONAL:
            if r in states and rx.search(n):
                return True
    return False


def classify_kind(name: str | None, tags: dict) -> str:
    """'route' for a meta-object that traverses named trails; 'trail' otherwise.

    A route is a completable object in its own right but overlaps the named
    trails it runs over (the overlap is real, not a bug — SPEC §6b). Tagging
    it lets a park/completion view show named trails and suppress the route
    layer. Three tells, all generic:
      - `network` grade regional-or-higher (rwn/nwn/iwn) — a thru-route like
        the Hayduke;
      - a composite `--` name joining two trail names (OSM's way of naming a
        route over two trails, e.g. 'Angels Landing Trail--West Rim Trail');
      - the word 'Route' in the name ('Zion Narrows Top-Down Hiking Route').
    A single-named trail that happens to be a route relation (West Rim Trail,
    network=lwn) stays a trail.
    """
    if str(tags.get("network", "")).strip().lower() in _ROUTE_NETWORKS:
        return "route"
    if name and ("--" in name or _ROUTE_WORD.search(name)):
        return "route"
    return "trail"


def _hike_name(dest_name: str) -> str:
    n = dest_name.strip()
    return n if n.lower().endswith("trail") else f"{n} Trail"


# ---------------------------------------------------------------------------
# geometry helpers
# ---------------------------------------------------------------------------

def way_coords(way: dict, nodes: dict[int, tuple[float, float]]) -> list[tuple[float, float]]:
    return [nodes[n] for n in way["nodes"] if n in nodes]


def _endpoints(nds: list[int]) -> tuple[int, int] | None:
    return (nds[0], nds[-1]) if len(nds) >= 2 else None


def _pick_unused(cands: set[int] | None, used: set[int]) -> int | None:
    if not cands:
        return None
    for c in cands:
        if c not in used:
            return c
    return None


def order_ways(way_ids: list[int], ways: dict) -> list[list[int]]:
    """Order a set of ways into connected chains by shared endpoint nodes.

    Returns a list of chains (each a list of way_ids in walkable order,
    with member way node-orientation normalized so consecutive ways join
    head-to-tail). A set with a gap yields more than one chain — surfaced,
    not hidden. Grows each chain from its tail node, then its head node.
    """
    remaining = list(dict.fromkeys(way_ids))
    by_end: dict[int, set[int]] = {}
    for wid in remaining:
        ep = _endpoints(ways[wid]["nodes"])
        if ep:
            by_end.setdefault(ep[0], set()).add(wid)
            by_end.setdefault(ep[1], set()).add(wid)

    used: set[int] = set()
    chains: list[list[int]] = []
    for seed in remaining:
        if seed in used:
            continue
        used.add(seed)
        chain = [seed]
        nds = ways[seed]["nodes"]
        head_node, tail_node = nds[0], nds[-1]

        while True:                                   # grow the tail
            nxt = _pick_unused(by_end.get(tail_node), used)
            if nxt is None:
                break
            used.add(nxt)
            wn = ways[nxt]["nodes"]
            if wn[0] != tail_node:                     # orient to continue
                ways[nxt] = {**ways[nxt], "nodes": list(reversed(wn))}
                wn = ways[nxt]["nodes"]
            chain.append(nxt)
            tail_node = wn[-1]

        while True:                                   # grow the head
            nxt = _pick_unused(by_end.get(head_node), used)
            if nxt is None:
                break
            used.add(nxt)
            wn = ways[nxt]["nodes"]
            if wn[-1] != head_node:                    # far end becomes new head
                ways[nxt] = {**ways[nxt], "nodes": list(reversed(wn))}
                wn = ways[nxt]["nodes"]
            chain.insert(0, nxt)
            head_node = wn[0]

        chains.append(chain)
    return chains


def chains_to_multiline(chains: list[list[int]], ways: dict,
                        nodes: dict) -> list[list[tuple[float, float]]]:
    out = []
    for chain in chains:
        coords: list[tuple[float, float]] = []
        for wid in chain:
            c = way_coords(ways[wid], nodes)
            if coords and c and coords[-1] == c[0]:
                coords.extend(c[1:])
            else:
                coords.extend(c)
        if len(coords) >= 2:
            out.append(coords)
    return out


# ---------------------------------------------------------------------------
# step 1 — relations-first
# ---------------------------------------------------------------------------

def resolve_route_members(rel_id: int, relations: dict,
                          _seen: set[int] | None = None) -> list[tuple[int, str]]:
    """Transitively collect (way_id, role) under a route relation.

    Recurses through child route relations (superrelations). A child
    relation's own role propagates to its ways unless the way has a more
    specific role. Cycle-safe.
    """
    seen = _seen if _seen is not None else set()
    if rel_id in seen or rel_id not in relations:
        return []
    seen.add(rel_id)
    out: list[tuple[int, str]] = []
    for mtype, ref, role in relations[rel_id]["members"]:
        if mtype == "w":
            out.append((ref, role or "main"))
        elif mtype == "r":
            child = resolve_route_members(ref, relations, seen)
            for wid, crole in child:
                out.append((wid, role if role in SPUR_ROLES else crole))
    return out


def _is_route(tags: dict) -> bool:
    return (tags.get("type") == "route"
            and (tags.get("route") or "").strip().lower() in HIKING_ROUTE_KINDS)


# ---------------------------------------------------------------------------
# step 2 — name-stitch (union-find on shared endpoints, same normalized name)
# ---------------------------------------------------------------------------

class _UF:
    def __init__(self):
        self.p: dict[int, int] = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        self.p[self.find(a)] = self.find(b)


def stitch_by_name(way_ids: list[int], ways: dict) -> list[list[int]]:
    """Group ways sharing a normalized name AND a connecting node.

    Crosses highway types (path/steps/footway) — that's the whole point.
    Returns groups (each a list of way_ids). Two same-name ways with no
    shared node stay separate groups (distinct trails of the same name).
    """
    by_name: dict[str, list[int]] = {}
    for wid in way_ids:
        nm = norm_name(ways[wid]["tags"].get("name"))
        if nm:
            by_name.setdefault(nm, []).append(wid)

    groups: list[list[int]] = []
    for wids in by_name.values():
        uf = _UF()
        node_owner: dict[int, int] = {}
        for wid in wids:
            uf.find(wid)
            for nd in (ways[wid]["nodes"][0], ways[wid]["nodes"][-1]):
                if nd in node_owner:
                    uf.union(wid, node_owner[nd])
                node_owner[nd] = wid
        buckets: dict[int, list[int]] = {}
        for wid in wids:
            buckets.setdefault(uf.find(wid), []).append(wid)
        groups.extend(buckets.values())
    return groups


# ---------------------------------------------------------------------------
# step 3 — spur-attach
# ---------------------------------------------------------------------------

def _poi_near(coord, pois, reach_ft) -> dict | None:
    best, bestd = None, reach_ft / 5280.0
    for p in pois:
        d = haversine_mi(coord, p["coord"])
        if d <= bestd:
            best, bestd = p, d
    return best


def attach_spurs(trails: list["Trail"], leftover_ways: list[int], ways: dict,
                 nodes: dict, pois: list[dict]) -> None:
    """Weld unnamed/steps spurs that reach a destination POI onto their trail.

    A leftover way qualifies when it (a) is short, (b) shares an endpoint
    node with exactly ONE assembled trail, and (c) its far end lands near a
    destination POI. Mutates trails in place; records the weld for QA.
    """
    trail_end_nodes: dict[int, list["Trail"]] = {}
    for t in trails:
        for nd in t.terminal_nodes:
            trail_end_nodes.setdefault(nd, []).append(t)

    for wid in leftover_ways:
        nds = ways[wid]["nodes"]
        if len(nds) < 2:
            continue
        coords = way_coords(ways[wid], nodes)
        if line_mi(coords) > SPUR_MAX_MI:
            continue
        a, b = nds[0], nds[-1]
        # which end touches an assembled trail (exactly one trail)?
        for near_node, far_coord in ((a, coords[-1]), (b, coords[0])):
            owners = trail_end_nodes.get(near_node, [])
            uniq = {id(t): t for t in owners}
            if len(uniq) != 1:
                continue
            poi = _poi_near(far_coord, pois, SPUR_POI_REACH_FT)
            if not poi:
                continue
            t = next(iter(uniq.values()))
            oriented = coords if near_node == a else list(reversed(coords))
            t.weld_spur(wid, oriented, poi)
            break


# ---------------------------------------------------------------------------
# Trail record + orchestration
# ---------------------------------------------------------------------------

class Trail:
    def __init__(self, name: str | None, source: str, member_ways: list[int],
                 lines: list[list[tuple[float, float]]], tags: dict,
                 terminal_nodes: list[int]):
        self.name = name
        self.source = source            # "relation" | "name-stitch"
        self.member_ways = list(member_ways)
        self.lines = lines              # list of coord-lists (MultiLineString)
        self.tags = tags
        self.terminal_nodes = list(terminal_nodes)
        self.destinations: list[str] = []
        self.welds: list[dict] = []     # QA: what got attached and why
        self.hike = False               # tier-1 promoted canonical hike (SPEC §6c)
        self.area: str | None = None    # park area assigned for per-area merge (§6b)
        self.removed_reason: str | None = None  # QA: why curation dropped it (§5)
        self.removed_category: str | None = None  # QA: short slug of that rule

    def weld_spur(self, wid, coords, poi):
        self.member_ways.append(wid)
        self.lines.append(coords)
        nm = poi.get("name") or f"{poi['tags']}"
        self.destinations.append(nm)
        self.welds.append({"way": wid, "poi": nm, "reached": poi["coord"]})

    @property
    def length_mi(self) -> float:
        return round(sum(line_mi(l) for l in self.lines), 3)

    def to_feature(self) -> dict:
        return {
            "type": "Feature",
            "properties": {
                "name": self.name,
                "kind": "hike" if self.hike else classify_kind(self.name, self.tags),
                "area": self.area,
                "source": self.source,
                "length_mi": self.length_mi,
                "member_ways": self.member_ways,
                "destinations": self.destinations,
                "welds": self.welds,
                "network": self.tags.get("network", ""),
                "operator": self.tags.get("operator", ""),
                "sac_scale": self.tags.get("sac_scale", ""),
                "trail_visibility": self.tags.get("trail_visibility", ""),
                "removed_reason": self.removed_reason,
                "removed_category": self.removed_category,
                # Stable per-trail key for run-to-run diff review (member OSM
                # ways don't change when only a curation rule is re-tuned).
                "ckey": "w" + "-".join(str(w) for w in sorted(self.member_ways)),
            },
            "geometry": {"type": "MultiLineString",
                         "coordinates": [[list(p) for p in line] for line in self.lines]},
        }


def _terminal_nodes(chains: list[list[int]], ways: dict) -> list[int]:
    ends = []
    for chain in chains:
        if chain:
            ends.append(ways[chain[0]]["nodes"][0])
            ends.append(ways[chain[-1]]["nodes"][-1])
    return ends


def _removal_verdict(trail: "Trail", min_length_mi: float = 0.0,
                     region: str | None = None) -> tuple[str | None, str | None]:
    """(category-slug, plain-language reason) curation would drop this trail on,
    or (None, None) if it's kept. The checks and their ORDER mirror the curation
    predicate in assemble() exactly — first match wins. Returning BOTH from one
    place keeps the short slug (for the viewer's per-reason review buckets) and
    the sentence (for the popup) from ever drifting. `region` (a 2-letter state
    code) enables region-scoped thru-hike names like Vermont's 'Long Trail'."""
    name = trail.name
    if is_closed_name(name):
        return ("closed",
                "Marked closed in OSM — the name itself says CLOSED "
                "(e.g. 'CLOSED - old Pyramid Trail').")
    if is_access_blocked(trail.tags):
        return ("access",
                "Not open to the public — OSM marks this way access=private / "
                "access=no (or foot=no) with no foot permission, so it isn't a "
                "trail the public can legally complete.")
    if is_thru_hike_name(name, region):
        return ("thru-hike",
                "Long-distance thru-hike, not a single completable trail — a "
                "named national/regional route (PCT, CDT, Colorado Trail, "
                "Hayduke, Arizona Trail, …) or one of its numbered segments.")
    if is_road_code_name(name):
        return ("road-code",
                "Bare road/ref code, not a trail name — nothing but an agency "
                "or OSM reference number (e.g. 'FR 231', 'CR 12', '9A').")
    if name is not None and len(name.strip()) <= 2:
        return ("short-name",
                "No real name — a 1–2 character stub or agency abbreviation "
                "('FR', 'FS', 'BR', '?'), not a trail name.")
    if is_offtrail_name(name):
        return ("off-trail",
                "Agency road or non-trail feature, not a foot trail — a Forest "
                "Service / National Forest / FSR / NF / IDL / BIA road, or a "
                "freeway ramp, airport concourse, or parking lot.")
    if is_motorized_name(name):
        return ("motorized",
                "Motorized route, not a foot trail — the name marks it "
                "ATV / OHV / UTV / 4WD / snowmobile / Jeep.")
    if is_nonhiking_route_name(name):
        return ("nonhiking-route",
                "Named '… Route' that isn't a hiking trail — a mountain-bike "
                "route, a technical climbing / glacier mountaineering line "
                "('Emmons Glacier Route', 'Monitor Ridge Climbing Route'), an "
                "evacuation route, or a dead mapping artifact ('… NOT VISIBLE', "
                "'… Obliterated …'). Real named routes (Zion Narrows, Grand "
                "Canyon's Escalante/Royal Arch, El Camino Real) are kept.")
    if is_utility_corridor_name(name):
        return ("utility",
                "Utility corridor, not a foot trail — a bare powerline / "
                "pipeline / gas-line / aqueduct right-of-way (e.g. 'Power "
                "Line', 'Pipeline Clearing'). A named path along one "
                "('Powerline Trail') is kept.")
    if is_nontrail_feature_name(name):
        return ("non-trail-feature",
                "Named non-trail feature, not a foot trail — a sidewalk, "
                "airport runway, ski-lift line, or bare parking area (e.g. "
                "'Tusyan Sidewalk', 'Lift 8 Tower', 'Opal Parking'). A path "
                "that merely touches one ('Parking Lot Trail') is kept.")
    if is_named_road_name(name):
        return ("named-road",
                "Named road carried as a path — a bare '… Road / Highway' with "
                "no trail identity (e.g. 'Maxwell Ranch Road'). Carriage roads "
                "and any '… Trail' are kept; a washed-out road you still hike "
                "can be rescued from this bucket by eye.")
    if is_grid_address_name(name):
        return ("grid-address",
                "Section-line grid road, not a trail — a rural PLSS grid "
                "address like 'North 3325 West', part of the farm-road "
                "lattice, never a hiking trail.")
    if is_generic_name(name) and trail.source != "relation":
        return ("generic",
                "Generic name with no identity ('Trail', 'Connector', 'Path', "
                "'Loop') and not backed by an OSM route relation — usually a "
                "fragment that would blob into unrelated trails.")
    if min_length_mi > 0 and trail.length_mi < min_length_mi:
        return ("min-length",
                f"Too short — {trail.length_mi} mi is below the "
                f"{min_length_mi} mi minimum (likely a connector stub, not a "
                f"hike on its own).")
    return (None, None)


def removal_reason(trail: "Trail", min_length_mi: float = 0.0,
                   region: str | None = None) -> str | None:
    """Plain-language reason curation would drop this trail, or None if kept.
    Used to surface removals in the QA viewer popup. See _removal_verdict."""
    return _removal_verdict(trail, min_length_mi, region)[1]


def removal_category(trail: "Trail", min_length_mi: float = 0.0,
                     region: str | None = None) -> str | None:
    """Short category slug for the rule that would drop this trail (e.g.
    'motorized', 'road-code', 'thru-hike'), or None if kept. Drives the viewer's
    per-reason review buckets so a whole class is judged at once. See
    _removal_verdict."""
    return _removal_verdict(trail, min_length_mi, region)[0]


def assemble(nodes: dict, ways: dict, relations: dict,
             pois: list[dict], min_length_mi: float = 0.0,
             areas: list[dict] | None = None,
             collect_removed: list | None = None,
             region: str | None = None,
             collect_ingest_dropped: list | None = None) -> list[Trail]:
    trails: list[Trail] = []
    claimed: set[int] = set()

    # Diagnostic: NAMED ways whose highway type is trail-ish but which a tag
    # gate filters out before assembly (foot=no, ski piste, road-like track,
    # motor-vehicle). Collected so the viewer can show them with a reason — a
    # named trail wrongly eaten by a tag rule is otherwise invisible. Nameless
    # drops (sidewalks, pistes) are skipped: pure noise, no false-negative risk.
    if collect_ingest_dropped is not None:
        for wid, w in ways.items():
            nm = display_name(w["tags"])
            if not nm:
                continue
            verdict = ingest_drop_reason(w["tags"])
            if not verdict:
                continue
            coords = [nodes[n] for n in w["nodes"] if n in nodes]
            if len(coords) < 2:
                continue
            cat, reason = verdict
            collect_ingest_dropped.append({
                "type": "Feature",
                "properties": {"name": nm, "removed_category": cat,
                               "removed_reason": reason,
                               "highway": w["tags"].get("highway", ""),
                               "ckey": f"w{wid}"},
                # nodes are stored (lon, lat) — already GeoJSON order.
                "geometry": {"type": "LineString",
                             "coordinates": [list(c) for c in coords]},
            })

    # Long-distance thru-hikes (Arizona Trail, PCT, CDT, Hayduke, …) are mapped
    # as SUPERROUTES: a route relation whose members include other route
    # relations (the numbered segments). Flag the whole hierarchy — the parent
    # superroute AND every child segment — so relations-first drops the thru-hike
    # object and its fragments. This is member/ref-driven (not name-guessing) and
    # scales globally. The differently-named local trails a thru-hike runs over
    # survive: their ways aren't claimed as the route's own, so name-stitch still
    # emits them.
    _route_ids = {rid for rid, rel in relations.items() if _is_route(rel["tags"])}
    superroute_ids: set[int] = set()
    for rid in _route_ids:
        kids = [ref for (mt, ref, _role) in relations[rid]["members"]
                if mt == "r" and ref in _route_ids]
        if kids:
            superroute_ids.add(rid)          # the parent superroute
            superroute_ids.update(kids)      # its child segments

    # 1. relations-first
    for rid, rel in relations.items():
        if not _is_route(rel["tags"]):
            continue
        members = resolve_route_members(rid, relations)
        main_ways = [w for w, role in members
                     if role in MAIN_ROLES and w in ways and _is_trailish(ways[w]["tags"])]
        spur_ways = [w for w, role in members
                     if role in SPUR_ROLES and w in ways and _is_trailish(ways[w]["tags"])]
        if not main_ways:
            continue
        chains = order_ways(main_ways, ways)
        lines = chains_to_multiline(chains, ways, nodes)
        if not lines:
            continue
        rel_name = norm_name(display_name(rel["tags"]))
        t = Trail(display_name(rel["tags"]) or _first_named(main_ways, ways),
                  "relation", main_ways, lines, rel["tags"],
                  _terminal_nodes(chains, ways))
        # approach/connection spurs are part of the object but not the main line
        for sw in spur_ways:
            t.member_ways.append(sw)
        # Claim ONLY members without their own distinct identity — unnamed
        # connectors or ways sharing the route's name. A member with its OWN
        # different name (e.g. "Bursera Canyon" inside the umbrella "Maricopa
        # Trail" route) is NOT claimed, so name-stitch still emits it as its
        # own trail. The umbrella route keeps its full geometry regardless.
        for w in main_ways + spur_ways:
            wname = norm_name(ways[w]["tags"].get("name"))
            if not wname or wname == rel_name:
                claimed.add(w)
        # Super-relation drop: a thru-hike superroute or one of its numbered
        # segments. Its own ways are claimed above so name-stitch can't resurface
        # the fragment; drop the object itself (underlying local trails survive).
        if rid in superroute_ids:
            if collect_removed is not None:
                t.removed_reason = ("part of a long-distance thru-hike route "
                                    "(super-relation) — dropped from the checklist")
                t.removed_category = "thru-hike"
                collect_removed.append(t)
            continue
        trails.append(t)

    # 2. name-stitch the remainder
    named_leftover = [wid for wid, w in ways.items()
                      if wid not in claimed and w["tags"].get("name")
                      and _is_trailish(w["tags"])]
    for group in stitch_by_name(named_leftover, ways):
        chains = order_ways(group, ways)
        lines = chains_to_multiline(chains, ways, nodes)
        if not lines:
            continue
        rep = ways[group[0]]["tags"]
        t = Trail(display_name(rep), "name-stitch", group, lines, rep,
                  _terminal_nodes(chains, ways))
        claimed.update(group)
        trails.append(t)

    # 3. spur-attach unnamed/steps leftovers that reach a POI
    unnamed_leftover = [wid for wid, w in ways.items()
                        if wid not in claimed and _is_trailish(w["tags"])]
    attach_spurs(trails, unnamed_leftover, ways, nodes, pois)

    # 4. one object per named trail WITHIN an area — fuse same-name pieces that
    #    sit in one park, but split a sprawling same-name group (a bare
    #    long-trail name mapped as many disconnected stretches, e.g. Bonneville
    #    Shoreline) into connected components instead of one scattered blob.
    merged = merge_same_name(trails, area_of=make_area_of(areas) if areas else None)

    # 4b. drop geometry-duplicate trails the name-merge missed — a route
    #     relation and a name-stitch built over the same ways under names that
    #     normalize differently ('Casner Canyon Trail' vs 'Casner Canyon #11').
    merged = dedupe_duplicate_trails(merged)

    # 5. curation: drop name-flagged-closed trails, sub-threshold stubs (tiny
    #    connectors), and pure-generic-named objects with no identity ("Trail",
    #    "Connector" — also what fuse into region-scale blobs); a route relation
    #    spares its object. min_length_mi<=0 keeps everything.
    kept = []
    for t in merged:
        category, reason = _removal_verdict(t, min_length_mi, region)
        if reason is None:
            kept.append(t)
        elif collect_removed is not None:
            t.removed_reason = reason
            t.removed_category = category
            collect_removed.append(t)

    # 6. tier-1 canonical hikes: promote local routes that reach a named
    #    destination POI into a 'hike', renamed from the payoff, and absorb the
    #    redundant physical fragment the hike covers (SPEC §6c).
    promoted = promote_hikes(kept, pois)

    # 7. checklist coalesce: one object per (name, area). The spread-gate splits
    #    a long trail into contiguous pieces for clean map geometry, but for a
    #    completion checklist a named trail should be ONE row per park — the
    #    Bonneville Shoreline Trail is 28 pieces in Uinta-Wasatch-Cache NF, and
    #    that should read as one "Bonneville Shoreline Trail", not 28. Merge
    #    same-name objects sharing an area into one multi-segment object. No-op
    #    when areas aren't assigned (park runs / tests keep area=None).
    return coalesce_by_area(promoted)


def coalesce_by_area(trails: list["Trail"]) -> list["Trail"]:
    """One object per (name, area) for the completion checklist. Merges every
    same-name trail assigned to the same park into a single multi-segment
    object, so a long trail split across a huge area (or a loop split by shared
    tread) is one completion row instead of many duplicates. Trails with no
    assigned area (backcountry) or no name pass through untouched."""
    groups: dict[tuple, list["Trail"]] = {}
    order: list[tuple] = []
    out: list["Trail"] = []
    for t in trails:
        k = merge_key(t.name) if t.name else ""
        if not k or t.area is None:
            out.append(t)
            continue
        gk = (t.area, k)
        if gk not in groups:
            groups[gk] = []
            order.append(gk)
        groups[gk].append(t)
    out.extend(_fuse_cluster(groups[gk]) for gk in order)
    return out


def _rep_point(trail: "Trail") -> tuple[float, float] | None:
    """A representative interior point of a trail — midpoint of its longest
    line. Used to assign the trail to an area for per-area merge scoping."""
    best = None
    for line in trail.lines:
        if line and (best is None or len(line) > len(best)):
            best = line
    return best[len(best) // 2] if best else None


def _point_in_ring(x: float, y: float, ring: list) -> bool:
    """Ray-casting point-in-polygon (stdlib, keeps model.py shapely-free)."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def make_area_of(areas: list[dict]):
    """Build an `area_of(trail)` -> area name | None from park polygons.

    `areas`: [{"name", "bbox": (x0,y0,x1,y1), "rings": [[(x,y),...], ...]}].
    A trail is assigned to the first area whose bbox contains its rep point
    and one of whose rings encloses it. bbox pre-filter keeps it fast at
    region scale. Trails in no park area return None (backcountry).
    """
    def area_of(trail: "Trail") -> str | None:
        pt = _rep_point(trail)
        if pt is None:
            return None
        x, y = pt
        for a in areas:
            x0, y0, x1, y1 = a["bbox"]
            if not (x0 <= x <= x1 and y0 <= y <= y1):
                continue
            if any(_point_in_ring(x, y, r) for r in a["rings"]):
                return a["name"]
        return None
    return area_of


# A same-name group whose pieces sprawl farther than this is treated as a
# blob (a bare long-trail name mapped as many disconnected stretches, e.g. the
# Bonneville Shoreline Trail at 70+ mi) and split into connected components.
# A compact group under it is one real trail — a loop or perimeter whose named
# ways are separated by short shared-tread segments — and fuses whole, so
# South Mountain's Pima Canyon Loop stays ONE object instead of fragmenting.
MERGE_SPREAD_CAP_MI = 15.0


def _group_spread_mi(group: list["Trail"]) -> float:
    """Diagonal of the bounding box over every vertex in the group, in miles —
    how far the same-name pieces sprawl."""
    xs, ys = [], []
    for t in group:
        for line in t.lines:
            for x, y in line:
                xs.append(x)
                ys.append(y)
    if not xs:
        return 0.0
    return haversine_mi((min(xs), min(ys)), (max(xs), max(ys)))


def _connectivity_clusters(group: list["Trail"]) -> list[list["Trail"]]:
    """Partition same-name trails into physically-connected clusters. Two
    trails join the same cluster iff they share a vertex — an OSM junction
    node, which after coordinate rounding (~1 m) is an exact coord match.
    Union-find over a coord->owner map, O(total vertices)."""
    uf = _UF()
    coord_owner: dict[tuple, int] = {}
    for i, t in enumerate(group):
        uf.find(i)
        for c in _coord_set(t):
            if c in coord_owner:
                uf.union(i, coord_owner[c])
            else:
                coord_owner[c] = i
    buckets: dict[int, list["Trail"]] = {}
    for i, t in enumerate(group):
        buckets.setdefault(uf.find(i), []).append(t)
    return list(buckets.values())


def _fuse_cluster(cluster: list["Trail"]) -> "Trail":
    """Fuse one connected same-name cluster into a single Trail. A route
    relation is the base so its metadata wins; otherwise the piece with the
    most member ways. Geometry, member ways, destinations and welds all
    accumulate onto the base."""
    if len(cluster) == 1:
        return cluster[0]
    base = next((t for t in cluster if t.source == "relation"), None) \
        or max(cluster, key=lambda t: len(t.member_ways))
    for t in cluster:
        if t is base:
            continue
        base.lines.extend(t.lines)
        base.member_ways.extend(w for w in t.member_ways if w not in base.member_ways)
        base.destinations.extend(t.destinations)
        base.welds.extend(t.welds)
        base.terminal_nodes.extend(t.terminal_nodes)
        if base.source != "relation" and t.source == "relation":
            base.source, base.tags, base.name = "relation", t.tags, t.name
    return base


def merge_same_name(trails: list["Trail"], area_of=None) -> list["Trail"]:
    """Fold same-name trail objects into one, scoped to an area and gated on
    geographic SPREAD.

    Within ONE area a repeated trail name is normally the same trail — a route
    relation plus standalone same-named ways, or a loop whose named segments are
    split by short shared-tread sections — so we fuse the whole group. But a
    bare long-trail name mapped as dozens of disconnected stretches (the
    Bonneville Shoreline Trail, 70+ mi) would fuse into a scattered blob, so a
    group that sprawls past MERGE_SPREAD_CAP_MI is instead split into its
    physically-connected components. Spread — not raw connectivity — is the
    right discriminator: it keeps a compact loop whole (Pima Canyon Loop stays
    ONE object) while still breaking the cross-state blob apart.

    `area_of(trail) -> area-name | None` scopes the merge so a generic name
    ("Ridge Trail") in adjacent parks never fuses. None (park runs, tests) =
    the whole AOI is one implicit area. A trail in NO named area (backcountry)
    never cross-merges. Relation metadata wins. Unnamed trails pass through.
    """
    groups: dict[tuple, list["Trail"]] = {}
    out: list["Trail"] = []
    for t in trails:
        if area_of is not None:
            t.area = area_of(t)                 # record for emit + viewer filter
        k = merge_key(t.name) if t.name else ""
        if not k:
            out.append(t)
            continue
        area = t.area if area_of is not None else "\x00aoi"
        if area is None:                        # backcountry: never cross-merge
            out.append(t)
            continue
        groups.setdefault((area, k), []).append(t)
    for group in groups.values():
        if len(group) == 1 or _group_spread_mi(group) <= MERGE_SPREAD_CAP_MI:
            out.append(_fuse_cluster(group))            # one real trail
        else:
            for cluster in _connectivity_clusters(group):
                out.append(_fuse_cluster(cluster))      # sprawling blob -> split
    return out


_REF_SUFFIX = re.compile(r"#\s*\d+[a-z]?\s*$", re.IGNORECASE)
_GENERIC_TOKENS = {"trail", "trails", "loop", "path", "pathway", "way", "route",
                   "spur", "connector", "cutoff", "bypass", "segment", "extension"}


def _name_rank(name: str | None) -> tuple:
    """Keep-preference key for a duplicate pair (higher tuple wins):
      1. a name with >=1 DISTINCTIVE word beats a pure-generic one, so
         'Granite Mountain Trail #261' > 'Trail 261';
      2. then fuller spelling (more letters), so 'Wilson Mountain' > 'Willson
         Mtn' and 'Raspberry' > 'Rasberry';
      3. then a clean name over a '#<ref>' form, so 'Casner Canyon Trail' >
         'Casner Canyon #11'.
    A distinctive word is any token that isn't a generic trail word or a
    number/ref code."""
    if not name:
        return (-1, 0, 0)
    toks = re.findall(r"[a-z0-9]+", name.lower())
    has_distinct = 1 if any(t not in _GENERIC_TOKENS and not t[0].isdigit()
                            for t in toks) else 0
    alpha = sum(c.isalpha() for c in name)
    no_ref = 0 if _REF_SUFFIX.search(name.strip()) else 1
    return (has_distinct, alpha, no_ref)


def _coord_set(t: "Trail") -> frozenset:
    """ALL rounded coords of the trail (~1 m at 5 decimals) — a geometry
    fingerprint. Endpoints alone are too weak: two DISTINCT trails between the
    same trailhead and peak share endpoints but not the path between, so we
    fingerprint the whole line and require near-total overlap to call a dup."""
    return frozenset((round(c[0], 5), round(c[1], 5))
                     for line in t.lines for c in line if len(c) >= 2)


def _trail_sig(t: "Trail") -> tuple:
    """(member-way set, coord set, length) — the values the duplicate test
    needs, computed once so an O(n^2) area scan doesn't rebuild them per pair
    (a 657-trail forest went from ~20 min to seconds with this precompute)."""
    return (frozenset(t.member_ways), _coord_set(t), t.length_mi)


def _sig_duplicate(sa: tuple, sb: tuple) -> bool:
    """Two trail signatures describe the SAME physical trail: near-equal length
    AND either substantially the same OSM ways OR near-total coordinate
    overlap (>=90% of the smaller trail's nodes coincide). The length gate
    stops a short fragment from absorbing a full-length trail; the coordinate
    (not endpoint) test stops two distinct trails that merely share endpoints
    from folding — only genuine full duplicates match."""
    wa, ca, la = sa
    wb, cb, lb = sb
    if la <= 0 or lb <= 0 or abs(la - lb) > 0.05 * max(la, lb):
        return False
    if wa and wb and len(wa & wb) >= 0.5 * len(wa | wb):
        return True
    if ca and cb:
        return len(ca & cb) >= 0.9 * min(len(ca), len(cb))
    return False


def _is_duplicate(a: "Trail", b: "Trail") -> bool:
    return _sig_duplicate(_trail_sig(a), _trail_sig(b))


def _prefer(a: "Trail", b: "Trail") -> "Trail":
    """The keeper of a duplicate pair: best name, then longer geometry, then
    the route-relation object (human-curated)."""
    ra, rb = _name_rank(a.name), _name_rank(b.name)
    if ra != rb:
        return a if ra > rb else b
    if abs(a.length_mi - b.length_mi) > 1e-6:
        return a if a.length_mi > b.length_mi else b
    if (a.source == "relation") != (b.source == "relation"):
        return a if a.source == "relation" else b
    return a


def dedupe_duplicate_trails(trails: list["Trail"]) -> list["Trail"]:
    """Drop trails that duplicate another trail's geometry within the same
    area — e.g. a route relation 'Casner Canyon Trail' over the same ways a
    name-stitch emits as 'Casner Canyon #11'. merge_same_name misses these
    because the names normalize differently; matching on GEOMETRY collapses
    only provably-identical trails and never fuses two DISTINCT trails that
    merely share a base name (Bear Canyon #29 vs #31 differ in geometry -> both
    kept). Keeps the better-named object; area-scoped via the area set by
    merge_same_name."""
    from collections import defaultdict
    by_area: dict = defaultdict(list)
    for t in trails:
        by_area[t.area].append(t)
    drop: set = set()
    dropped: list = []          # (loser, keeper) names — logged for review
    for group in by_area.values():
        sig = {id(t): _trail_sig(t) for t in group}   # once per trail, not per pair
        n = len(group)
        for i in range(n):
            a = group[i]
            if id(a) in drop:
                continue
            sa = sig[id(a)]
            for j in range(i + 1, n):
                b = group[j]
                if id(b) in drop or not _sig_duplicate(sa, sig[id(b)]):
                    continue
                keep = _prefer(a, b)
                lose = b if keep is a else a
                drop.add(id(lose))
                dropped.append((lose.name, keep.name))
                if lose is a:
                    break
    # Every drop is logged with the trail it deferred to, so a review can
    # confirm each removal really had a surviving twin (not an over-merge).
    if dropped:
        print(f"dedupe: dropped {len(dropped)} geometry-duplicate trails",
              file=sys.stderr)
        for lo, wi in sorted(dropped, key=lambda p: (p[0] or "", p[1] or "")):
            print(f"  dedupe: {lo!r} -> kept {wi!r}", file=sys.stderr)
    return [t for t in trails if id(t) not in drop]


def _reached_destination(trail: "Trail", dest_pois: list[dict]) -> dict | None:
    """The named destination POI a trail terminates at/near (its payoff), or
    None. Checks the endpoints of every line — a summit/arch sits at an end."""
    ends = [c for line in trail.lines if line for c in (line[0], line[-1])]
    best, bestd = None, SPUR_POI_REACH_FT / 5280.0
    for c in ends:
        for p in dest_pois:
            d = haversine_mi(c, p["coord"])
            if d <= bestd:
                best, bestd = p, d
    return best


def promote_hikes(trails: list["Trail"], pois: list[dict]) -> list["Trail"]:
    """Tier 1 — HARVEST, don't synthesize. Promote a *local* route that
    reaches a named destination POI into a canonical 'hike', renamed from the
    payoff: OSM's 'Angels Landing Trail--West Rim Trail' route (which ends at
    the Angels Landing peak) becomes the hike 'Angels Landing Trail', drawn as
    the whole thing. Returns the surviving trails (mutating promoted ones).

    Only local routes qualify — a regional+ thru-route (network rwn/nwn/iwn,
    e.g. the Hayduke) passes through many payoffs and is not one hike, so it
    stays a route. Named physical trails are never touched. Overlap with the
    trails a hike runs over is expected (a hike is a curated overlay); the
    completion checklist is per-hike, not per-mile (SPEC §6c).

    Then ABSORB the physical fragment a hike already covers: a shorter
    standalone trail sharing the hike's name (the 0.43 mi 'Angels Landing
    Trail' spur that sits inside the promoted 2.17 mi hike) is redundant and
    dropped, so the checklist doesn't list the same payoff twice.
    """
    dest_pois = [p for p in pois if p.get("name")]
    if not dest_pois:
        return trails
    promoted: list["Trail"] = []
    for t in trails:
        if str(t.tags.get("network", "")).strip().lower() in _ROUTE_NETWORKS:
            continue                                  # thru-route, not one hike
        if classify_kind(t.name, t.tags) != "route":
            continue                                  # only promote route-ish objects
        dest = _reached_destination(t, dest_pois)
        if dest:
            t.destinations = [dest["name"]]
            t.name = _hike_name(dest["name"])
            t.hike = True
            promoted.append(t)
    if not promoted:
        return trails

    hikes_by_key: dict[str, list["Trail"]] = {}
    for h in promoted:
        hikes_by_key.setdefault(merge_key(h.name), []).append(h)
    out: list["Trail"] = []
    for t in trails:
        if not t.hike:
            covers = hikes_by_key.get(merge_key(t.name))
            if covers and any(t.length_mi < h.length_mi for h in covers):
                continue                              # absorbed into its hike
        out.append(t)
    return out


TRAILISH_HIGHWAY = {"path", "footway", "steps", "track", "bridleway",
                    "via_ferrata", "cycleway", "pedestrian"}

# A highway=track that's really a vehicle/utility/access road — not a hike.
# Ported from System 2 (data-pipeline stage_osm._vehicle_or_utility_road):
# catches the paved/graded access roads (e.g. the road to a trailhead lot).
# Name-based road tells for a highway=track. Word-boundary matched, so
# "Placerita" isn't caught by "place" and "Broadway Trail" isn't caught by
# "road". Covers named rural roads AND the street/lane/court/place suffixes
# that flood grid/subdivision areas at region scale (AZ statewide run).
_ROAD_NAME = re.compile(
    r"\b(road|street|avenue|boulevard|drive|lane|court|place|"
    r"canal|drain|ditch|highway|freeway|parkway|route)\b", re.IGNORECASE)

# US numeric grid-address road names — "3900 East", "N 400 W", "700 South".
# Pervasive in UT/AZ/ID rural grids; a hiking trail is never named this way.
_GRID_ROAD = re.compile(
    r"^\s*(?:[nsew]\.?\s+)?\d+\s+(?:north|south|east|west|n|s|e|w)\.?\s*$",
    re.IGNORECASE)

# Agency dirt-road codes — "NF-418C", "BLM 1048", "FR 236", "FS 6005",
# "NV-9040V". Two branches so we don't play prefix whack-a-mole:
#   (a) known agency prefixes + any digits (catches 1-2 digit "CR 15");
#   (b) GENERIC: a short (<=4) letter code + 3+ digits (+opt trailing letter),
#       as the whole name — catches unfamiliar prefixes (NV, ranger-district
#       letters) WITHOUT hitting 1-2-digit TRAIL codes (GR20, E5) or worded
#       names ("Trail 100" — "Trail" is 5 letters). Track-scoped either way,
#       so real named paths are never touched.
_FOREST_ROAD = re.compile(
    r"^\s*(?:(?:nf|fr|fsr|fs|usfs|blm|cr|nv)\b[-\s]?\d"
    r"|[a-z]{1,4}[-\s]?\d{3,}[a-z]?\s*$)", re.IGNORECASE)


# Name-only road/ID-code junk that rides on highway=path/footway or comes in
# as a route relation, so _road_like_track (track-scoped) never sees it —
# "[FR 1098]", ";NF-246C", "NF-D1857", "MT-2026 - FDR 2026", "U2259", "PST012",
# "212E", "F R 8080", "Forest Service Road 420". Digit-GATED and word-aware:
# a name with any genuine word (alphabetic, >=3 chars, has a vowel, not a road
# designator) is kept, so real trails that merely carry a number survive
# ("Aerie #168", "Calloway Trail 33", "See Canyon Trail #184").
_CODE_DECOR = re.compile(r"\*\*.*?\*\*|\bOHV\b|\b4WD\b|[\[\]();#]", re.IGNORECASE)
_CODE_TOKEN = re.compile(r"^[a-z]{0,4}\d+[a-z]?\d*[a-z]?$", re.IGNORECASE)
_ROAD_DESIGNATORS = {"fr", "fdr", "fs", "fsr", "nf", "usf", "usfs", "blm", "cr",
                     "nv", "mt", "rd", "rt", "u", "t", "forest", "service",
                     "road", "route", "hwy", "highway"}


def is_road_code_name(name: str | None) -> bool:
    """The name is nothing but an agency road / OSM-ref code — no trail identity."""
    if not name:
        return False
    toks = [t for t in _CODE_DECOR.sub(" ", name).replace("-", " ").split() if t]
    if not toks or not any(c.isdigit() for t in toks for c in t):
        return False  # no digits => a real name; leave it alone
    for t in toks:
        tl = t.lower()
        if tl in _ROAD_DESIGNATORS or _CODE_TOKEN.match(t):
            continue
        if t.isalpha() and len(t) >= 3 and any(v in tl for v in "aeiou"):
            return False  # a genuine word token => keep as a real trail
    return True


# WORDED agency-road / non-trail-feature names that is_road_code_name lets
# through because they're full of real English words — surfaced by the ID/WA
# audit: "National Forest Development Road 005", "Forest Service Road 420",
# "Natl Forrest Develop Rd 2798-A", "FSR 1562A", "NF-65 (abandoned)", "IDL
# 43D", "Bia 37", "Bureau of Indian Affairs Road 115", plus freeway ramps
# ("Ramp 23", "Soundside Ramp 52"), airport concourses ("Concourse A"), and
# parking lots. Deliberately NARROW — it does NOT touch bare "X Road" names
# (Alligator Road, Fire Road), which we keep on purpose so whimsical trail
# names ("Yellow Brick Road", "Thunder Road") survive.
_ROAD_WORD = re.compile(r"\b(road|rd|route)\b", re.IGNORECASE)
_AGENCY_PREFIX_CODE = re.compile(r"\b(fsr|fs|nf|nfd|idl|bia)\b[-\s]?\d", re.IGNORECASE)
_FOREST_ROAD_PHRASE = re.compile(
    r"\bnational forest\b|\bnatl?\.?\s*forr?e?st\b|\bforest (service|development)\b",
    re.IGNORECASE)
_RAMP = re.compile(r"^\s*ramp\s*$|\bramp\s*\d|\boff[\s-]?ramp\b", re.IGNORECASE)
_CONCOURSE = re.compile(r"\bconcourse\b", re.IGNORECASE)
_PARKING_LOT = re.compile(r"\bparking\s*lot\b", re.IGNORECASE)
_TRAIL_WORD = re.compile(r"\b(trail|loop|path|pathway|connector|greenway|trace|spur)\b",
                         re.IGNORECASE)


# Section-line GRID addresses — the rural Utah/PLSS naming where every farm
# road is "<dir> <number> <dir>": "North 3325 West", "West 6000 North", "North
# 4000 West". These form a dense regular lattice of tracks that OSM tags with a
# grid name; no hiking trail is ever named this way, so the pattern is a safe,
# unambiguous cull. Surfaced by the Utah state audit.
_GRID_ADDRESS = re.compile(
    r"^\s*(north|south|east|west|n|s|e|w)\s+\d{2,6}\s+"
    r"(north|south|east|west|n|s|e|w)\s*$",
    re.IGNORECASE)


def is_grid_address_name(name: str | None) -> bool:
    """The name is a section-line grid address ('North 3325 West') — a rural
    road-lattice coordinate, never a trail name."""
    return bool(name and _GRID_ADDRESS.match(name))


def is_offtrail_name(name: str | None) -> bool:
    """A worded agency road (Forest Service / National Forest / FSR / NF- /
    IDL / BIA code) or a non-trail feature (freeway ramp, airport concourse,
    parking lot) — not a foot trail. Complements is_road_code_name, which only
    catches code-like names with no real words."""
    if not name:
        return False
    if _AGENCY_PREFIX_CODE.search(name):        # FSR 1562A, NF-65, IDL 43D, Bia 37
        return True
    if _FOREST_ROAD_PHRASE.search(name) and _ROAD_WORD.search(name):
        return True                             # National Forest / Forest Service … Road
    if "bureau of indian affairs" in name.lower():
        return True
    # ramp / concourse / parking-lot features — but spare a named trail that
    # merely touches one ("Parking Lot Connector Trail").
    if _RAMP.search(name) or _CONCOURSE.search(name) or _PARKING_LOT.search(name):
        return not _TRAIL_WORD.search(name)
    return False


_MOTORIZED_NAME = re.compile(r"\b(ATV|OHV|UTV|4WD|4x4|snowmobile|jeep|motorcycle)\b", re.IGNORECASE)


def is_motorized_name(name: str | None) -> bool:
    """Name marks a motor route — ATV/OHV/UTV/4WD/snowmobile/Jeep. US mappers
    routinely name these ('Basalt Jeep Trail', 'Pine Creek South ATV Trail',
    'Deer Creek Trail (OHV Section)') without the atv/ohv access tags, so the
    tag-based _is_motorized can't see them; this catches the named-but-untagged
    ones in curation. Every one reviewed in the ID audit was a genuine vehicle
    route, not a foot hike."""
    return bool(name and _MOTORIZED_NAME.search(name))


_UTILITY_CORRIDOR = re.compile(
    r"\b(power\s*line|pipe\s*line|gas\s*line|transmission line|penstock|aqueduct)\b",
    re.IGNORECASE)


def is_utility_corridor_name(name: str | None) -> bool:
    """A bare utility corridor — a powerline / pipeline / gas line / aqueduct
    right-of-way mapped as a way, not a hiking trail ('Power Line', 'Gasline',
    'Pipeline Clearing', 'underground powerline'). SPARES a named footpath that
    merely follows the corridor ('Powerline Trail', 'Aqueduct Path',
    'Quemazon/Pipeline Loop') via _TRAIL_WORD — those are real walked trails.
    'Row' is deliberately NOT a signal: 'Stone Row', 'Greek Row', 'Skid Row'
    are New England / place names, not rights-of-way."""
    if not name or not _UTILITY_CORRIDOR.search(name):
        return False
    return not _TRAIL_WORD.search(name)


_NONHIKING_ROUTE = re.compile(
    r"\b(bike|climbing|glacier|evacuation)\s+route\b"
    r"|\b(not\s+visible|obliterated)\b",
    re.IGNORECASE)


def is_nonhiking_route_name(name: str | None) -> bool:
    """A named '… Route' that isn't a hiking trail: a mountain-bike route
    ('Nose Dive Bike Route'), a technical climbing / glacier mountaineering
    line ('Emmons Glacier Route', 'Monitor Ridge Climbing Route'), an
    evacuation route, or a dead mapping artifact whose name says so ('Trail
    Route: NOT VISIBLE 2019', 'Forest Route 31 Obliterated at Mud Creek').

    Deliberately NARROW — only the phrase '<bike|climbing|glacier|evacuation>
    route' or an explicit not-visible / obliterated marker. This is the whole
    point: the vast majority of '… Route' names are REAL hikes and must be
    kept — Grand Canyon's Escalante / Esplanade / Royal Arch Routes, both Zion
    Narrows Hiking Routes, Ozark Trail segment routes, El Camino Real Historic
    Route — so a blanket 'Route' drop would be a serious quality regression.
    The genuinely ambiguous ones (E-Routes, 'State Route …' road codes, ridge
    scrambles) are left for the viewer's review bucket, not auto-dropped.
    Every pattern here was checked against the full published set (277 '… Route'
    trails across all states) to hit only the unambiguous non-hiking cases with
    zero false positives; no _TRAIL_WORD spare, because a 'Climbing Route
    (… Trail)' is still a climb."""
    return bool(name and _NONHIKING_ROUTE.search(name))


# Named non-trail features mapped as a path but not hikeable. Two tiers:
#   HARD — a sidewalk / airport runway / taxiway is NEVER a foot trail, whatever
#     else is in the name ('Village Loop Drive Sidewalk' has 'Loop' but is still
#     a sidewalk).
#   SOFT — a ski-lift line ('Lift 8 Tower') or a bare parking area ('Opal
#     Parking') is usually not a trail, BUT a real path can share the word
#     ('Parking Lot Trail', 'Lift Line Trail'), so spare it via _TRAIL_WORD.
# Deliberately excludes 'tramway/gondola' (a hike can be named for the tram it
# parallels — Sandia's 'Tramway Trail') and any '…Lift' without a number
# ('Lift Line' is a common ski-area HIKING trail).
_NONTRAIL_HARD = re.compile(r"\b(sidewalk|runway|taxiway)\b", re.IGNORECASE)
_NONTRAIL_SOFT = re.compile(r"\b(chairlift|lift\s+\d+|parking)\b", re.IGNORECASE)


def is_nontrail_feature_name(name: str | None) -> bool:
    """A named non-trail feature — sidewalk / runway / ski-lift line / bare
    parking area — mapped as a path but not hikeable. See _NONTRAIL_HARD /
    _NONTRAIL_SOFT."""
    if not name:
        return False
    if _NONTRAIL_HARD.search(name):
        return True
    if _NONTRAIL_SOFT.search(name):
        return not _TRAIL_WORD.search(name)
    return False


# A named ROAD carried as a path. Only the UNAMBIGUOUS road words — 'Drive /
# Avenue / Street / Lane' are deliberately excluded because they collide with
# real trails ('Leif Erikson Drive', 'Park Avenue Trail', '136th Street
# Express'), and using only 'Road/Rd/Highway/…' keeps those traps automatically.
_ROAD_SUFFIX = re.compile(
    r"\b(road|rd|highway|hwy|turnpike|freeway|expressway)\b", re.IGNORECASE)


def is_named_road_name(name: str | None) -> bool:
    """A bare '… Road / Rd / Highway' with no trail identity — a road carried as
    a path ('Maxwell Ranch Road', 'Anderson Lake Road'). SPARES anything with a
    trail word (_TRAIL_WORD → 'Battle Road Trail', 'Bear Canyon Road Connector')
    and Acadia-style 'Carriage Road's (a signature hiking network). Drop-by-
    default but REVIEWABLE: a washed-out / abandoned road that's now hiked
    ('Dosewallips River Road') lands in this bucket to rescue by eye."""
    if not name or not _ROAD_SUFFIX.search(name):
        return False
    if _TRAIL_WORD.search(name):
        return False
    return "carriage" not in name.lower()


# Public-access gate. OSM `access=private` / `access=no` marks a way the public
# can't legally use — a first-class filter distinct from whether the trail
# exists (AllTrails treats access the same way). foot=* WINS over access=*: a
# gated road (access=no) that is explicitly foot=yes/designated is a legitimate
# hike (a washed-out forest road open to walkers), so it stays.
_FOOT_ALLOWED = {"yes", "designated", "permissive", "official",
                 "customers", "destination", "permit"}


def is_access_blocked(tags: dict) -> bool:
    """True if OSM marks this way closed to the public on foot — foot=no/private,
    or access=private/no with no overriding foot permission."""
    foot = str(tags.get("foot", "")).strip().lower()
    if foot in {"no", "private"}:
        return True
    if foot in _FOOT_ALLOWED:
        return False                      # explicit foot permission overrides access
    return str(tags.get("access", "")).strip().lower() in {"private", "no"}


def _is_motorized(tags: dict) -> bool:
    """The way is designated for motor vehicles / off-highway use — an
    ATV/OHV/4WD/snowmobile route, not a foot trail. OSM tags these explicitly
    (atv/ohv/motor_vehicle/4wd_only/snowmobile), so we drop by TAG, not by a
    'Jeep'/'ATV' NAME: a foot-only path that merely carries such a name has
    none of these tags and is kept, and a real hiking route survives via its
    route relation regardless. (Research: OSM tags ATV/4WD tracks atv=yes,
    Jeep/OHV routes ohv=yes.)"""
    if str(tags.get("4wd_only", "")).strip().lower() in {"yes", "designated"}:
        return True
    return any(str(tags.get(k, "")).strip().lower() in {"yes", "designated"}
               for k in ("motor_vehicle", "motorcar", "atv", "ohv", "snowmobile",
                         "motorcycle"))


def _is_nonhiking(tags: dict) -> bool:
    """A purpose-built non-hiking way — a bike-park flow run or a ski piste —
    that carries highway=path but is not a foot trail. Detection uses only
    POSITIVE exclusion signals that never appear on a genuine hike:

      - foot=no — hikers are banned.
      - mtb:type=flow/downhill — a built downhill/flow bike feature.
      - piste:type without 'hike' — a ski piste (nordic/downhill). A
        'nordic;hike' piste is genuinely dual-use and kept.
      - a name that literally says '(No Hiking)' (some are tagged foot=yes, so
        only the name gives them away).
      - a name that says MTB / mountain bike outright ('Party of 5 (MTB)',
        'Yellow MTB Trail') with no slash or ' and ' — a whole-word 'MTB' or
        'mountain bike' never shows up in a real hike's name by accident, so
        (unlike mtb:scale:imba) it needs no tag co-signal. The slash/'and'
        guard spares explicit dual-use names ('MTB/Hiking Trail') and merge
        artifacts (two ways fused into one concatenated name).

    mtb:scale:imba (an IMBA difficulty RATING) is NOT a signal on its own — it
    rides on countless shared-use HIKING trails (South Mountain's whole network
    is foot=yes/bicycle=yes with imba=2..4), and gating on it alone silently ate
    them. But COMBINED with a directional or bike-park-named signal it cleanly
    fingers a flow run (Colorado audit — Keystone/Breck/Vail): a rated path that
    is one-way, or whose name is Downhill/Slalom/Flow/Jump Line/etc., is a bike
    feature, never a hike. Requiring imba first keeps Rainbow Trail (imba, but
    two-way and normally named) and every real trail untouched.
    """
    name = str(tags.get("name", "") or "")
    if "no hiking" in name.strip().lower():
        return True
    if _BIKE_ONLY_NAME.search(name) and not _BIKE_NAME_COMPOSITE.search(name):
        return True
    if str(tags.get("foot", "")).strip().lower() == "no":
        return True
    if str(tags.get("mtb:type", "")).strip().lower() in {"flow", "downhill"}:
        return True
    piste = str(tags.get("piste:type", "")).strip().lower()
    if piste and "hike" not in piste:
        return True
    # Bike-park flow trails: only when IMBA-rated AND (one-way OR bike-park name).
    if str(tags.get("mtb:scale:imba", "")).strip() != "":
        if str(tags.get("oneway", "")).strip().lower() in {"yes", "1", "true"}:
            return True
        if _BIKEPARK_NAME.search(name):
            return True
    return False


def _road_like_track_kind(tags: dict) -> str | None:
    """Why a highway=track reads as a road, or None. 'tag' = an unambiguous
    hard signal (motor_vehicle / motorcar / 2+ lanes). 'name' = only its NAME
    looks road-like (a Road/Drive/Ditch suffix, an FR-code, a PLSS grid). The
    split matters for review: a 'name' drop is the fuzzy one that could eat a
    real trail mis-tagged as a track, so the viewer flags it CHECK; a 'tag'
    drop is a real road."""
    if tags.get("highway") != "track":
        return None
    if str(tags.get("motor_vehicle", "")).strip().lower() in {"yes", "designated"}:
        return "tag"
    if str(tags.get("motorcar", "")).strip().lower() == "yes":
        return "tag"
    # 2+ lanes = a drivable road, not a foot trail (e.g. the sand service
    # road through South Mountain Park mis-named after the park itself).
    try:
        if int(str(tags.get("lanes", "")).strip()) >= 2:
            return "tag"
    except ValueError:
        pass
    name = str(tags.get("name", "") or "")
    if (_GRID_ROAD.match(name)          # "3900 East"
            or _FOREST_ROAD.match(name)  # "NF-418C", "BLM 1048"
            or _ROAD_NAME.search(name)):  # "7th Street", "Holley Lane"
        return "name"
    return None


def _road_like_track(tags: dict) -> bool:
    return _road_like_track_kind(tags) is not None


def ingest_drop_reason(tags: dict) -> tuple[str, str] | None:
    """For a way whose highway type IS trail-ish but which the ingest filter
    drops by a SECONDARY tag rule, the (category, plain-language reason); else
    None (kept, or never a trail candidate). Surfaced in the viewer's
    'ingest-filtered' diagnostic so a NAMED trail silently eaten by a tag gate
    (the class of bug that once ate South Mountain) is visible, not invisible.
    _is_trailish delegates the secondary checks here so the two can't drift."""
    hw = tags.get("highway")
    if hw not in TRAILISH_HIGHWAY:
        return None                      # a road / non-highway — never a trail candidate
    if hw == "footway" and tags.get("footway") in {"sidewalk", "crossing"}:
        return ("sidewalk", "footway=sidewalk / =crossing — pavement, not a trail.")
    if tags.get("indoor") == "yes" or tags.get("trail") == "no":
        return ("indoor/trail=no", "tagged indoor=yes or trail=no.")
    rk = _road_like_track_kind(tags)
    if rk == "tag":
        return ("road-track-tag",
                "highway=track marked for motor vehicles / multi-lane — a "
                "drivable road, not a foot trail.")
    if rk == "name":
        return ("road-track-name",
                "highway=track whose NAME reads as a road (a Road/Drive/Ditch "
                "suffix, an FR-code, or a PLSS grid address). CHECK — a real "
                "trail mis-tagged as a track would land here.")
    if _is_motorized(tags):
        return ("motorized-tag",
                "tagged for motor vehicles — atv / ohv / 4wd / motor_vehicle.")
    if _is_nonhiking(tags):
        return ("non-hiking-tag",
                "foot=no, a ski piste (piste:type), or a bike-park flow run.")
    return None


def _is_trailish(tags: dict) -> bool:
    hw = tags.get("highway")
    if hw not in TRAILISH_HIGHWAY:
        return "highway" in tags and tags.get("highway", "").startswith("abandoned")
    return ingest_drop_reason(tags) is None


def _first_named(way_ids: list[int], ways: dict) -> str | None:
    for wid in way_ids:
        nm = display_name(ways[wid]["tags"])
        if nm:
            return nm
    return None


def coverage_stats(ways: dict, relations: dict, pois: list[dict]) -> dict:
    """What does OSM actually contain in this AOI? Diagnostic — lets the
    golden runner tell 'missing data' (nothing we can fix in code) apart
    from 'assembly gap' (our thresholds to tune) when a trail fails."""
    return {
        "raw_trailish_ways": sum(1 for w in ways.values() if _is_trailish(w["tags"])),
        "named_trailish_ways": sum(1 for w in ways.values()
                                   if _is_trailish(w["tags"]) and w["tags"].get("name")),
        "hiking_route_relations": sum(1 for r in relations.values() if _is_route(r["tags"])),
        "route_relations_total": sum(1 for r in relations.values()
                                     if r["tags"].get("type") == "route"),
        "destination_pois": len(pois),
    }
