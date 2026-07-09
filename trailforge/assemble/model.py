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


# network grades that mark a long-distance thru-route (vs a local trail).
_ROUTE_NETWORKS = {"rwn", "nwn", "iwn"}
_ROUTE_WORD = re.compile(r"\broute\b", re.IGNORECASE)


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
                "source": self.source,
                "length_mi": self.length_mi,
                "member_ways": self.member_ways,
                "destinations": self.destinations,
                "welds": self.welds,
                "network": self.tags.get("network", ""),
                "operator": self.tags.get("operator", ""),
                "sac_scale": self.tags.get("sac_scale", ""),
                "trail_visibility": self.tags.get("trail_visibility", ""),
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


def assemble(nodes: dict, ways: dict, relations: dict,
             pois: list[dict], min_length_mi: float = 0.0) -> list[Trail]:
    trails: list[Trail] = []
    claimed: set[int] = set()

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

    # 4. one object per named trail — combine the pieces split across a route
    #    relation and standalone same-named ways (e.g. "National Trail").
    merged = merge_same_name(trails)

    # 5. curation: drop name-flagged-closed trails + sub-threshold stubs
    #    (tiny connectors). min_length_mi<=0 keeps everything.
    kept = [t for t in merged
            if not is_closed_name(t.name)
            and (min_length_mi <= 0 or t.length_mi >= min_length_mi)]

    # 6. tier-1 canonical hikes: promote local routes that reach a named
    #    destination POI into a 'hike', renamed from the payoff, and absorb the
    #    redundant physical fragment the hike covers (SPEC §6c).
    return promote_hikes(kept, pois)


def merge_same_name(trails: list["Trail"]) -> list["Trail"]:
    """Fold trail objects sharing a name into one.

    Within an AOI a repeated trail name is the same trail: "National Trail"
    that appears as a route relation PLUS standalone same-named ways should
    be one object, so it highlights and counts as one. Relation metadata
    wins. Unnamed trails (welded spurs etc.) pass through untouched.
    """
    base_by_name: dict[str, "Trail"] = {}
    out: list["Trail"] = []
    for t in trails:
        key = merge_key(t.name) if t.name else ""
        if not key:
            out.append(t)
            continue
        base = base_by_name.get(key)
        if base is None:
            base_by_name[key] = t
            out.append(t)
            continue
        base.lines.extend(t.lines)
        base.member_ways.extend(w for w in t.member_ways if w not in base.member_ways)
        base.destinations.extend(t.destinations)
        base.welds.extend(t.welds)
        if base.source != "relation" and t.source == "relation":
            base.source, base.tags, base.name = "relation", t.tags, t.name
    return out


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

# Forest Service / BLM / county road codes — "NF-418C", "BLM 1048", "FR 236",
# "FS 6005". Dirt vehicle roads through national forests (Kaibab, Coconino,
# Tonto…), not hiking trails. 1,338 of them in the Arizona statewide run.
_FOREST_ROAD = re.compile(r"^\s*(nf|fr|fsr|fs|usfs|blm|cr)\b[-\s]?\d", re.IGNORECASE)


def _road_like_track(tags: dict) -> bool:
    if tags.get("highway") != "track":
        return False
    if str(tags.get("motor_vehicle", "")).strip().lower() in {"yes", "designated"}:
        return True
    if str(tags.get("motorcar", "")).strip().lower() == "yes":
        return True
    # 2+ lanes = a drivable road, not a foot trail (e.g. the sand service
    # road through South Mountain Park mis-named after the park itself).
    try:
        if int(str(tags.get("lanes", "")).strip()) >= 2:
            return True
    except ValueError:
        pass
    name = str(tags.get("name", "") or "")
    return bool(_GRID_ROAD.match(name)        # "3900 East"
                or _FOREST_ROAD.match(name)   # "NF-418C", "BLM 1048"
                or _ROAD_NAME.search(name))    # "7th Street", "Holley Lane"


def _is_trailish(tags: dict) -> bool:
    hw = tags.get("highway")
    if hw not in TRAILISH_HIGHWAY:
        return "highway" in tags and tags.get("highway", "").startswith("abandoned")
    if hw == "footway" and tags.get("footway") in {"sidewalk", "crossing"}:
        return False
    if tags.get("indoor") == "yes" or tags.get("trail") == "no":
        return False
    if _road_like_track(tags):          # access/utility road masquerading as a track
        return False
    return True


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
