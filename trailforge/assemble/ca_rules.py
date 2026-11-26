"""Canada (region=ca) per-relation opt-ins for the generic assembler.

Both rules are OFF unless the registry lists a relation; assemble.py takes them only with
`--region ca --require-exact-area --expected-area-relation-id` and the legacy rule set, and
model.assemble refuses them outside region ca. Neither rule synthesizes geometry: every
published metre is a real OSM member way of the listed relation.

accept_gap (registry `accept_gap`, per relation): {relation: "r<id>", max_gap_m, reason}
    A signed hiking route is published short because some of its own member ways are dropped at
    ingest ONLY for a ski-piste tag (piste:type, e.g. skitour) while carrying hiking evidence
    (positive foot tag or sac_scale). The listed relation gets exactly those members back. The
    gap those ways left (widest bridge between the pieces before the restore) must be at most
    max_gap_m (hard cap CA_ACCEPT_GAP_CAP_M), and so must any gap that remains afterwards;
    separate parts stay separate parts. foot=no, motor, road, bike-only, mtb, sidewalk, indoor
    and every other drop reason keep the member out.

keep_superroute (registry `keep_superroute`, per relation): {relation: "r<id>", reason}
    The shared assembler drops every super-relation (a route relation holding other route
    relations) and its children as a long-distance thru-hike. A listed super-relation that is a
    single-park named trail (Algonquin's Western Uplands) is kept as ONE trail; its children
    stay dropped (their ways are already part of it). Fail closed when it is a child of a larger
    route relation, when it or any relation under it is a recognised long-distance network, or
    when any of its geometry leaves the registry area polygon.
"""
from __future__ import annotations

import math
import re
import unicodedata
from typing import Any, Callable, Iterable, Mapping

import member_safety

CA_ACCEPT_GAP_CAP_M = 1000.0
ACCEPT_GAP_KEYS = frozenset({"relation", "max_gap_m", "reason"})
KEEP_SUPERROUTE_KEYS = frozenset({"relation", "reason"})
RELATION_ID = re.compile(r"^r[1-9][0-9]*$")
MAX_PIECES = 60                      # more pieces than this cannot be reviewed as one route
OUTSIDE_TOLERANCE_DEG = 0.0002       # about 20 m: boundary-hugging mapping noise, not a part outside
LONG_DISTANCE_NETWORKS = frozenset({"iwn", "nwn", "rwn"})
LONG_DISTANCE_TAGS = ("name", "alt_name", "official_name", "short_name", "old_name",
                      "name:en", "name:fr", "ref")


def _fold(text: Any) -> str:
    decomposed = unicodedata.normalize("NFKD", str(text or "")).casefold()
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^\w']+", " ", stripped.replace("\u2019", "'")).split())


_LONG_DISTANCE = re.compile(
    r"(?<!\w)(?:trans[- ]?canada(?: trail)?|great trail|sentier trans[- ]?canadien|"
    r"sentier transcanadien|bruce trail|rideau trail|great divide(?: trail)?|"
    r"sentier national|sentier international des appalaches|international appalachian trail|"
    r"eastern continental trail|sentier continental|appalachian trail|waterfront trail|"
    r"confederation trail|pacific crest trail|continental divide trail)(?!\w)")


def long_distance_name(tags: Mapping[str, Any]) -> str | None:
    """The recognised long-distance name found in any naming tag, else None."""
    for key in LONG_DISTANCE_TAGS:
        hit = _LONG_DISTANCE.search(_fold(tags.get(key)))
        if hit:
            return f"{key}={tags.get(key)!r} ({hit.group(0)})"
    return None


def _entries(entries: Any, label: str, keys: frozenset[str]) -> list[dict[str, Any]]:
    if not isinstance(entries, list):
        raise ValueError(f"{label} must be a list")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != keys:
            raise ValueError(f"{label} entry must be an object with exactly {sorted(keys)}: {entry!r}")
        relation = entry["relation"]
        if not (isinstance(relation, str) and RELATION_ID.fullmatch(relation)):
            raise ValueError(f"{label} relation is invalid: {relation!r}")
        reason = entry["reason"]
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 600:
            raise ValueError(f"{label} {relation}: reason is required (1..600 characters)")
        if relation in seen:
            raise ValueError(f"{label} repeats relation {relation!r}")
        seen.add(relation)
        out.append(dict(entry, reason=reason.strip()))
    return out


def validate_accept_gap(entries: Any) -> list[dict[str, Any]]:
    """Normalized copy of a registry ``accept_gap`` list, or ValueError.
    max_gap_m must be a finite number in (0, CA_ACCEPT_GAP_CAP_M]; the registry cannot raise the cap."""
    out = _entries(entries, "accept_gap", ACCEPT_GAP_KEYS)
    for entry in out:
        gap = entry["max_gap_m"]
        if (isinstance(gap, bool) or not isinstance(gap, (int, float)) or not math.isfinite(gap)
                or not 0 < gap <= CA_ACCEPT_GAP_CAP_M):
            raise ValueError(
                f"accept_gap {entry['relation']}: max_gap_m must be a number above 0 and at most "
                f"{CA_ACCEPT_GAP_CAP_M:g}: {gap!r}")
        entry["max_gap_m"] = float(gap)
    return out


def validate_keep_superroute(entries: Any) -> list[dict[str, Any]]:
    """Normalized copy of a registry ``keep_superroute`` list, or ValueError."""
    return _entries(entries, "keep_superroute", KEEP_SUPERROUTE_KEYS)


def relation_number(entry: Mapping[str, Any]) -> int:
    return int(str(entry["relation"])[1:])


# ------------------------------------------------------------------ accept_gap --

def hiking_evidence(tags: Mapping[str, Any]) -> bool:
    """A member someone tagged as walkable: a positive foot value or a sac_scale grade."""
    return (member_safety.normalize(tags.get("foot")) in member_safety.POSITIVE_FOOT_VALUES
            or bool(member_safety.normalize(tags.get("sac_scale"))))


def piste_only_drop(tags: Mapping[str, Any], ingest_drop: tuple[str, str] | None) -> bool:
    """True when the ingest filter drops this way ONLY for a non-hiking ski piste tag and the way
    carries hiking evidence. ``ingest_drop`` is model.ingest_drop_reason(tags)."""
    return (ingest_drop is not None and ingest_drop[0] == "non-hiking-tag"
            and member_safety.nonhiking_reason(tags) == "non-hiking-piste"
            and hiking_evidence(tags))


def way_pieces(way_ids: Iterable[int], ways: Mapping[int, dict]) -> list[list[int]]:
    """Group ways into pieces joined by a shared OSM node (union-find). Deterministic order."""
    ids = list(dict.fromkeys(way_ids))
    parent = {w: w for w in ids}

    def find(w: int) -> int:
        while parent[w] != w:
            parent[w] = parent[parent[w]]
            w = parent[w]
        return w

    owner: dict[int, int] = {}
    for w in ids:
        for node in ways[w]["nodes"]:
            if node in owner:
                a, b = find(owner[node]), find(w)
                if a != b:
                    parent[max(a, b)] = min(a, b)
            else:
                owner[node] = w
    groups: dict[int, list[int]] = {}
    for w in ids:
        groups.setdefault(find(w), []).append(w)
    return sorted(groups.values(), key=lambda g: min(g))


def widest_bridge_m(pieces: list[list[int]], ways: Mapping[int, dict], nodes: Mapping[int, Any]) -> float | None:
    """Widest single gap, in metres, needed to join every piece into one (the bottleneck edge of the
    minimum spanning tree over true line-to-line distances). 0.0 for one piece; None when there are
    more than MAX_PIECES pieces or a piece has no geometry (unmeasurable)."""
    if len(pieces) <= 1:
        return 0.0
    if len(pieces) > MAX_PIECES:
        return None
    from shapely.geometry import LineString
    from shapely.ops import unary_union

    lats = [nodes[n][1] for piece in pieces for w in piece for n in ways[w]["nodes"] if n in nodes]
    if not lats:
        return None
    kx = 111320.0 * math.cos(math.radians(sum(lats) / len(lats)))
    ky = 110574.0
    geoms = []
    for piece in pieces:
        lines = []
        for w in piece:
            pts = [(nodes[n][0] * kx, nodes[n][1] * ky) for n in ways[w]["nodes"] if n in nodes]
            if len(pts) >= 2:
                lines.append(LineString(pts))
        if not lines:
            return None
        geoms.append(unary_union(lines))
    n = len(geoms)
    in_tree = {0}
    best = {i: geoms[0].distance(geoms[i]) for i in range(1, n)}
    widest = 0.0
    while len(in_tree) < n:
        nxt = min(best, key=lambda i: (best[i], i))
        widest = max(widest, best.pop(nxt))
        in_tree.add(nxt)
        for i in best:
            best[i] = min(best[i], geoms[nxt].distance(geoms[i]))
    return round(widest, 1)


def plan_accept_gap(entry: Mapping[str, Any], members: list[tuple[int, str]], ways: Mapping[int, dict],
                    nodes: Mapping[int, Any], walkable: Callable[[int], bool],
                    restorable: Callable[[int], bool], main_roles: Iterable[str]) -> dict[str, Any]:
    """Decide one accept_gap entry for a relation's resolved ``members`` ([(way, role)]).
    Returns the evidence record; ``main_ways`` (restore applied) is set only when status is
    "accepted", otherwise ``failures`` explains why the entry must fail closed."""
    roles = set(main_roles)
    main = [w for w, role in members if role in roles]
    failures: list[str] = []
    missing = sorted({w for w in main if w not in ways})
    if missing:
        failures.append(f"member ways missing from the extract: {missing[:8]}")
    present = [w for w in dict.fromkeys(main) if w in ways]
    kept = [w for w in present if walkable(w)]
    restored = [w for w in present if not walkable(w) and restorable(w)]
    record: dict[str, Any] = {
        "relation": entry["relation"], "max_gap_m": entry["max_gap_m"], "reason": entry["reason"],
        "restored_way_ids": sorted(restored), "restored_count": len(restored),
        "main_ways": list(kept), "status": "failed", "failures": failures,
    }
    if missing:
        return record
    if not restored:
        failures.append("unneeded: no member way is dropped for a ski-piste tag alone")
        return record
    before = way_pieces(kept, ways)
    after = way_pieces(kept + restored, ways)
    gap_before = widest_bridge_m(before, ways, nodes)
    gap_after = widest_bridge_m(after, ways, nodes)
    record.update({
        "pieces_before": len(before), "pieces_after": len(after),
        "widest_gap_before_m": gap_before, "widest_gap_after_m": gap_after,
    })
    limit = entry["max_gap_m"]
    if len(before) < 2:
        failures.append("unneeded: the walkable members are not split, so there is no gap to accept")
    elif gap_before is None:
        failures.append(f"gap is unmeasurable ({len(before)} pieces; at most {MAX_PIECES})")
    elif gap_before > limit:
        failures.append(f"widest gap {gap_before:g} m is over max_gap_m {limit:g}")
    if gap_after is None:
        failures.append(f"remaining gap is unmeasurable ({len(after)} pieces)")
    elif gap_after > limit:
        failures.append(f"a gap of {gap_after:g} m remains after the restore (max_gap_m {limit:g})")
    if not failures:
        record["status"] = "accepted"
        # Member order is kept, so the chaining below sees the same sequence the default does.
        restore_set = set(restored)
        record["main_ways"] = [w for w, role in members
                               if role in roles and w in ways and (w in restore_set or walkable(w))]
    return record


# ------------------------------------------------------------- keep_superroute --

def plan_keep_superroutes(entries: list[dict[str, Any]], relations: Mapping[int, dict],
                          route_ids: set[int], is_thru_name: Callable[[str | None], bool]) -> dict[int, dict]:
    """Pre-emission decision per entry (no geometry): {relation id: record}. status "accepted" means
    the super-relation may be emitted as one trail; otherwise ``failures`` lists why not."""
    out: dict[int, dict] = {}
    for entry in entries:
        rid = relation_number(entry)
        record: dict[str, Any] = {"relation": entry["relation"], "reason": entry["reason"],
                                  "status": "failed", "failures": [], "children": []}
        out[rid] = record
        failures = record["failures"]
        rel = relations.get(rid)
        if rel is None or rid not in route_ids:
            failures.append("not a hiking route relation in this extract")
            continue
        tags = rel["tags"]
        record["name"] = tags.get("name")
        record["network"] = tags.get("network")
        hierarchy = _hierarchy(rid, relations, route_ids)
        children = [r for r in hierarchy if r != rid]
        record["children"] = sorted(children)
        if not children:
            failures.append("unneeded: it holds no route relations, so it is not a super-relation")
        parents = sorted(p for p, prel in relations.items() if p in route_ids and p != rid
                         and any(mt == "r" and ref == rid for (mt, ref, _role) in prel["members"]))
        if parents:
            failures.append(f"it is a child of larger route relation(s) {parents}: not a single-park trail")
        for r in hierarchy:
            rtags = relations[r]["tags"]
            net = member_safety.normalize(rtags.get("network"))
            if net in LONG_DISTANCE_NETWORKS:
                failures.append(f"r{r} is a long-distance network ({net})")
            hit = long_distance_name(rtags)
            if hit:
                failures.append(f"r{r} is a recognised long-distance trail: {hit}")
            elif any(is_thru_name(rtags.get(key)) for key in ("name", "alt_name", "official_name")):
                failures.append(f"r{r} is a famous thru-hike by name: {rtags.get('name')!r}")
        if not failures:
            record["status"] = "accepted"
    return out


def _hierarchy(rid: int, relations: Mapping[int, dict], route_ids: set[int]) -> list[int]:
    seen: list[int] = []
    stack = [rid]
    while stack:
        cur = stack.pop()
        if cur in seen or cur not in relations:
            continue
        seen.append(cur)
        for mt, ref, _role in relations[cur]["members"]:
            if mt == "r" and ref in route_ids:
                stack.append(ref)
    return seen


def outside_length_deg(lines: list[list[tuple[float, float]]], area_geom: Any) -> float:
    """Length (degrees) of the route that lies outside the area polygon grown by the tolerance."""
    from shapely.geometry import LineString
    from shapely.ops import unary_union

    parts = [LineString(line) for line in lines if len(line) >= 2]
    if not parts:
        return 0.0
    outside = unary_union(parts).difference(area_geom.buffer(OUTSIDE_TOLERANCE_DEG))
    return float(outside.length)


# --------------------------------------------------------------------- verdict --

def finalize(rule_report: Mapping[str, Any], accept_entries: list[dict[str, Any]],
             keep_entries: list[dict[str, Any]], ways: Mapping[int, dict], nodes: Mapping[int, Any],
             area_geom: Any, final_features: list[dict]) -> tuple[list[str], dict[str, Any]]:
    """Fail-closed verdict for both opt-ins once assembly and the exact-area clip are done.
    Returns (failures, evidence); evidence is what the assembly report publishes."""
    failures: list[str] = []
    evidence: dict[str, Any] = {}
    published = [set(f.get("properties", {}).get("member_ways") or []) for f in final_features]
    accept = rule_report.get("accept_gap", {})
    accepted_rows: list[dict] = []
    for entry in accept_entries:
        rid = relation_number(entry)
        rec = accept.get(rid)
        if rec is None:
            failures.append(f"accept_gap {entry['relation']}: not an emitted hiking route relation in "
                            "this extract (missing, not a route, or entirely filtered)")
            continue
        if rec["status"] != "accepted":
            failures.extend(f"accept_gap {entry['relation']}: {reason}" for reason in rec["failures"])
        elif not any(set(rec["restored_way_ids"]) <= pub for pub in published):
            failures.append(f"accept_gap {entry['relation']}: the restored ways are not in any published "
                            "trail (removed by a later rule)")
        row = {k: v for k, v in rec.items() if k not in ("main_ways", "failures")}
        accepted_rows.append(row)
    if accept_entries:
        evidence["accept_gap"] = accepted_rows
    keep = rule_report.get("keep_superroute", {})
    keep_rows: list[dict] = []
    for entry in keep_entries:
        rid = relation_number(entry)
        rec = keep.get(rid)
        if rec is None:
            failures.append(f"keep_superroute {entry['relation']}: not planned")
            continue
        row = {k: v for k, v in rec.items() if k != "main_ways"}
        if rec["status"] != "accepted":
            failures.extend(f"keep_superroute {entry['relation']}: {reason}" for reason in rec["failures"])
        elif "main_ways" not in rec:
            failures.append(f"keep_superroute {entry['relation']}: no trail was emitted for it")
        else:
            lines = [[tuple(nodes[n]) for n in ways[w]["nodes"] if n in nodes]
                     for w in rec["main_ways"] if w in ways]
            outside = outside_length_deg(lines, area_geom) if area_geom is not None else None
            row["outside_deg"] = None if outside is None else round(outside, 6)
            if outside is None:
                failures.append(f"keep_superroute {entry['relation']}: no area polygon to test against")
            elif outside > 0:
                failures.append(f"keep_superroute {entry['relation']}: part of the route lies outside the "
                                f"area polygon ({outside:.4f} degrees of line): not a single-park trail")
            main = set(rec["main_ways"])
            if not any(main <= pub for pub in published):
                failures.append(f"keep_superroute {entry['relation']}: the trail is not in the published "
                                "population (removed by a later rule)")
        keep_rows.append(row)
    if keep_entries:
        evidence["keep_superroute"] = keep_rows
    evidence["failures"] = failures
    return failures, evidence
# ------------------------------------------------------------------ ring-way dedupe --
# Registry `dedupe_ring_way: true` (Canada, relation-backed, per area). OSM can hold a boundary relation
# AND its own tagged outer ring way with the identical name (Cypress Hills Centre Block r15678445 /
# w120623144): the area assembler yields both, so exact-area assembly sees two same-name boundaries.
# The opt-in drops ONLY same-name way areas that are `outer` members of the registry relation itself.
# Any other same-name boundary still fails closed, and an opt-in with nothing to dedupe is stale.
def outer_ring_ways(pbf_path: str, relation_id: int) -> set[int]:
    """Way ids that are `outer` members of relation `relation_id` in the PBF."""
    import osmium
    found: set[int] = set()
    class _Scan(osmium.SimpleHandler):
        def relation(self, r):
            if r.id == relation_id:
                found.update(m.ref for m in r.members if m.type == "w" and m.role == "outer")
    _Scan().apply_file(str(pbf_path))
    return found
def dedupe_ring_way_areas(matches: list[dict], rings: set[int],
                          relation_id: int) -> tuple[list[dict], list[int], str | None]:
    """(kept matches, dropped ring way ids, failure). Only way-built areas whose id is an outer
    ring of the registry relation are dropped; a stale opt-in (nothing dropped) fails closed."""
    dropped = sorted(int(a.get("osm_id") or 0) for a in matches
                     if a.get("osm_type") == "way" and int(a.get("osm_id") or 0) in rings)
    if not dropped:
        return matches, [], (f"dedupe_ring_way is not needed: no same-name boundary is an outer ring "
                             f"way of relation {relation_id}; remove it from the registry entry")
    kept = [a for a in matches
            if not (a.get("osm_type") == "way" and int(a.get("osm_id") or 0) in rings)]
    return kept, dropped, None
# ------------------------------------------------------------------ whole-route publish --
# Registry `whole_route` (Canada, relation-backed, per area, per trail). The exact-area clip cuts a trail at the
# polygon, so a route that runs a little past the boundary is published short (West Coast Trail 45.28 of 47.95 mi).
# An entry lists ONE assembled trail by its assembled (OSM) name. When the area holds at least
# CA_WHOLE_ROUTE_MIN_SHARE of the route's own length, the trail is published whole: its coordinates are the
# assembled source coordinates, untouched, flagged `ca_whole_route` (never `clipped`). Nothing is created or moved.
# One trail belongs to one area: the entry lists every other live area that also holds part of the route
# (`rivals`, boundary object "r<id>" or "w<id>" + name); each rival polygon must be found in the extract and hold a strictly smaller
# share than this area (the larger share owns the trail). Fail closed: no match, several matches, a route that is
# not clipped, a share under the floor, an unlocatable rival or a rival with an equal or larger share.
CA_WHOLE_ROUTE_MIN_SHARE = 0.75      # fixed floor; an entry cannot lower it
CA_WHOLE_ROUTE_MIN_OUTSIDE_MI = 0.01  # under this the route is not clipped: the entry is stale
WHOLE_ROUTE_KEYS = frozenset({"name", "source", "reason"})
WHOLE_ROUTE_OPTIONAL_KEYS = frozenset({"rivals"})
RIVAL_KEYS = frozenset({"osm", "name"})
RIVAL_OSM = re.compile(r"^[rw][1-9][0-9]*$")
def validate_whole_route(entries: Any, label: str = "whole_route") -> list[dict[str, Any]]:
    """Normalised list of {name, source, reason[, rivals]}; any other shape raises ValueError."""
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"{label} must be a non-empty list")
    seen: set[str] = set()
    for entry in entries:
        if (not isinstance(entry, dict) or not WHOLE_ROUTE_KEYS <= set(entry)
                or set(entry) - WHOLE_ROUTE_KEYS - WHOLE_ROUTE_OPTIONAL_KEYS):
            raise ValueError(f"{label} entry must be an object with {sorted(WHOLE_ROUTE_KEYS)} and optional "
                             f"{sorted(WHOLE_ROUTE_OPTIONAL_KEYS)}: {entry!r}")
        for key in WHOLE_ROUTE_KEYS:
            text = entry[key]
            if (not isinstance(text, str) or not text.strip() or text != unicodedata.normalize("NFC", text)
                    or len(text) > 600):
                raise ValueError(f"{label} {key} must be non-empty NFC text (at most 600 characters): {text!r}")
        if not re.fullmatch(r"https://[A-Za-z0-9.-]+(/\S*)?", entry["source"]):
            raise ValueError(f"{label} source must be an https URL: {entry['source']!r}")
        if entry["name"] in seen:
            raise ValueError(f"{label} repeats {entry['name']!r}")
        seen.add(entry["name"])
        rivals = entry.get("rivals", [])
        if not isinstance(rivals, list) or ("rivals" in entry and not rivals):
            raise ValueError(f"{label} {entry['name']!r}: rivals must be a non-empty list when present")
        rival_ids: set[str] = set()
        for rival in rivals:
            if (not isinstance(rival, dict) or set(rival) != RIVAL_KEYS
                    or not isinstance(rival["osm"], str) or not RIVAL_OSM.fullmatch(rival["osm"])
                    or not isinstance(rival["name"], str) or not rival["name"].strip()
                    or rival["name"] != unicodedata.normalize("NFC", rival["name"])):
                raise ValueError(f"{label} {entry['name']!r}: a rival needs exactly osm (\"r<id>\" or \"w<id>\") "
                                 f"and name (NFC text): {rival!r}")
            if rival["osm"] in rival_ids:
                raise ValueError(f"{label} {entry['name']!r}: rivals repeat {rival['osm']}")
            rival_ids.add(rival["osm"])
    return entries
def _line_miles(geometry: Any) -> float:
    import model
    from areas import _line_parts
    return sum(model.line_mi([(c[0], c[1]) for c in line.coords]) for line in _line_parts(geometry))
def whole_route_decisions(features: list[dict[str, Any]], area_union: Any, entries: list[dict[str, Any]],
                          area_objs: list[dict[str, Any]], own_osm: str | None, *,
                          min_inside_mi: float) -> tuple[dict[int, dict[str, Any]], list[str]]:
    """({feature index: evidence}, failures) for the pre-clip assembled `features`.
    Pure decision: nothing is changed. A feature is published whole only when its entry passes every gate."""
    from shapely.geometry import shape
    decisions: dict[int, dict[str, Any]] = {}
    failures: list[str] = []
    for entry in entries:
        name = entry["name"]
        label = f"whole_route {name!r}"
        hits = [i for i, f in enumerate(features)
                if (f.get("properties") or {}).get("name") == name
                and (f.get("properties") or {}).get("kind") != "poi"]
        if not hits:
            failures.append(f"{label}: no assembled trail has this name (stale entry or the trail was filtered)")
            continue
        if len(hits) > 1:
            failures.append(f"{label}: {len(hits)} assembled trails have this name (ambiguous)")
            continue
        index = hits[0]
        try:
            source = shape(features[index]["geometry"])
            inside = source.intersection(area_union)
        except Exception as error:  # noqa: BLE001 - invalid geometry fails closed
            failures.append(f"{label}: geometry is not usable ({error})")
            continue
        full_mi = _line_miles(source)
        inside_mi = _line_miles(inside)
        if full_mi <= 0:
            failures.append(f"{label}: the route has no length")
            continue
        share = inside_mi / full_mi
        outside_mi = full_mi - inside_mi
        if outside_mi < CA_WHOLE_ROUTE_MIN_OUTSIDE_MI:
            failures.append(f"{label}: the route is not clipped by the polygon ({outside_mi:.3f} mi outside); "
                            "remove the entry")
            continue
        if inside_mi < min_inside_mi or share + 1e-12 < CA_WHOLE_ROUTE_MIN_SHARE:
            failures.append(f"{label}: the area holds {inside_mi:.2f} of {full_mi:.2f} mi ({share:.1%}), under "
                            f"the {CA_WHOLE_ROUTE_MIN_SHARE:.0%} ownership floor")
            continue
        rival_rows: list[dict[str, Any]] = []
        owned = True
        for rival in entry.get("rivals", []):
            if own_osm is not None and rival["osm"] == own_osm:
                failures.append(f"{label}: a rival cannot be the area itself ({own_osm})")
                owned = False
                continue
            kind = "relation" if rival["osm"][0] == "r" else "way"
            match = [a for a in area_objs
                     if a.get("osm_type") == kind and int(a.get("osm_id") or 0) == int(rival["osm"][1:])
                     and a.get("geom") is not None]
            if len(match) != 1:
                failures.append(f"{label}: rival {rival['osm']} ({rival['name']!r}) is not exactly one polygon in "
                                f"the extract (found {len(match)}); ownership cannot be proved")
                owned = False
                continue
            rival_mi = _line_miles(source.intersection(match[0]["geom"]))
            rival_share = rival_mi / full_mi
            rival_rows.append({"osm": rival["osm"], "name": rival["name"],
                               "inside_mi": round(rival_mi, 3), "share": round(rival_share, 4)})
            if not share > rival_share:
                failures.append(f"{label}: rival {rival['name']!r} holds {rival_share:.1%} of the route, not "
                                f"less than this area's {share:.1%}: the larger share owns the trail")
                owned = False
        if not owned:
            continue
        decisions[index] = {"name": name, "full_mi": round(full_mi, 3), "inside_mi": round(inside_mi, 3),
                            "outside_mi": round(outside_mi, 3), "share": round(share, 4),
                            "rivals": rival_rows, "source": entry["source"]}
    return decisions, failures
# --- remnant_drop (registry `remnant_drop`, per trail name; Canada publish only) -------------------------------
# A published trail can carry stray pieces: small separate line pieces that are not connected to the trail's main
# piece (name-stitched leftovers of a clip). An entry drops them from ONE named trail, only when the piece is at
# most `max_mi` long (0.1 mi unless the entry raises it, hard cap CA_REMNANT_CAP_MI) AND farther than
# CA_REMNANT_MIN_DIST_M from the main piece. Nothing else changes: ids, names, and every other piece stay.
CA_REMNANT_DEFAULT_MAX_MI = 0.1
CA_REMNANT_CAP_MI = 0.5              # an entry cannot raise the threshold past this
CA_REMNANT_MIN_DIST_M = 5.0          # fixed: a piece this close or closer to the main piece is a gap, kept
CA_REMNANT_LINK_M = 1.0              # pieces within this distance of each other are ONE piece (shared ends)
REMNANT_KEYS = frozenset({"name", "source", "reason"})
REMNANT_OPTIONAL_KEYS = frozenset({"max_mi"})
def validate_remnant_drop(entries: Any, label: str = "remnant_drop") -> list[dict[str, Any]]:
    """Normalised list of {name, source, reason[, max_mi]}; any other shape raises ValueError."""
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"{label} must be a non-empty list")
    seen: set[str] = set()
    for entry in entries:
        if (not isinstance(entry, dict) or not REMNANT_KEYS <= set(entry)
                or set(entry) - REMNANT_KEYS - REMNANT_OPTIONAL_KEYS):
            raise ValueError(f"{label} entry must be an object with {sorted(REMNANT_KEYS)} and optional "
                             f"{sorted(REMNANT_OPTIONAL_KEYS)}: {entry!r}")
        for key in REMNANT_KEYS:
            text = entry[key]
            if (not isinstance(text, str) or not text.strip() or text != unicodedata.normalize("NFC", text)
                    or len(text) > 600):
                raise ValueError(f"{label} {key} must be non-empty NFC text (at most 600 characters): {text!r}")
        if not re.fullmatch(r"https://[A-Za-z0-9.-]+(/\S*)?", entry["source"]):
            raise ValueError(f"{label} source must be an https URL: {entry['source']!r}")
        if entry["name"] in seen:
            raise ValueError(f"{label} repeats {entry['name']!r}")
        seen.add(entry["name"])
        if "max_mi" in entry:
            value = entry["max_mi"]
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not CA_REMNANT_DEFAULT_MAX_MI < value <= CA_REMNANT_CAP_MI):
                raise ValueError(f"{label} {entry['name']!r}: max_mi must be a number above "
                                 f"{CA_REMNANT_DEFAULT_MAX_MI} and at most {CA_REMNANT_CAP_MI} "
                                 f"(omit it for the {CA_REMNANT_DEFAULT_MAX_MI} mi default): {value!r}")
    return entries
def split_stray_pieces(lines: list[list[tuple[float, float]]], max_mi: float) -> dict[str, Any]:
    """Pure decision on one trail's line pieces ((lon, lat) lists, in order).
    Connected pieces (<= CA_REMNANT_LINK_M apart) form one component; the longest component is the main piece.
    A component is stray when its length is <= max_mi and it lies more than CA_REMNANT_MIN_DIST_M from the main
    piece. Returns {status, components, main_mi, stray: [{indices, mi, distance_m}]}; status "ok" or
    "ambiguous-main" (the two longest components have the same length: nothing may be dropped)."""
    import model
    from shapely.geometry import LineString
    from shapely.ops import unary_union
    if len(lines) < 2:
        return {"status": "ok", "components": len(lines), "main_mi": 0.0, "stray": []}
    lat0 = sum(p[1] for line in lines for p in line) / max(1, sum(len(line) for line in lines))
    kx, ky = 111320.0 * math.cos(math.radians(lat0)), 110540.0
    flat = [LineString([(p[0] * kx, p[1] * ky) for p in line]) for line in lines]
    parent = list(range(len(lines)))
    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    for i in range(len(lines)):
        for j in range(i + 1, len(lines)):
            if flat[i].distance(flat[j]) <= CA_REMNANT_LINK_M:
                parent[find(i)] = find(j)
    groups: dict[int, list[int]] = {}
    for i in range(len(lines)):
        groups.setdefault(find(i), []).append(i)
    comps = sorted(({"indices": sorted(g), "mi": sum(model.line_mi([(p[0], p[1]) for p in lines[i]]) for i in g)}
                    for g in groups.values()), key=lambda c: (-c["mi"], c["indices"][0]))
    if len(comps) < 2:
        return {"status": "ok", "components": 1, "main_mi": comps[0]["mi"], "stray": []}
    if abs(comps[0]["mi"] - comps[1]["mi"]) < 1e-9:
        return {"status": "ambiguous-main", "components": len(comps), "main_mi": comps[0]["mi"], "stray": []}
    main = unary_union([flat[i] for i in comps[0]["indices"]])
    stray = []
    for comp in comps[1:]:
        distance = main.distance(unary_union([flat[i] for i in comp["indices"]]))
        if comp["mi"] <= max_mi and distance > CA_REMNANT_MIN_DIST_M:
            stray.append({"indices": comp["indices"], "mi": comp["mi"], "distance_m": distance})
    return {"status": "ok", "components": len(comps), "main_mi": comps[0]["mi"], "stray": stray}
def drop_stray_remnants(features: list[dict[str, Any]], entries: list[dict[str, Any]]
                        ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """(features, report rows, failures). Only the trails named by `entries` can change, and only by losing their
    stray pieces (length_mi becomes the kept length; id, name and every other property stay). A failure (no such
    trail, several such trails, an ambiguous main piece, nothing to drop) leaves that trail unchanged and the
    caller must then fail the area."""
    import model
    out = list(features)
    report: list[dict[str, Any]] = []
    failures: list[str] = []
    for entry in entries:
        name = entry["name"]
        label = f"remnant_drop {name!r}"
        hits = [i for i, f in enumerate(out)
                if (f.get("properties") or {}).get("name") == name
                and (f.get("properties") or {}).get("kind") != "poi"]
        if len(hits) != 1:
            failures.append(f"{label}: {len(hits)} published trails have this name "
                            f"({'stale entry' if not hits else 'ambiguous'})")
            continue
        feature = out[hits[0]]
        geometry = feature.get("geometry") or {}
        if geometry.get("type") == "LineString":
            lines = [[(c[0], c[1]) for c in geometry["coordinates"]]]
        elif geometry.get("type") == "MultiLineString":
            lines = [[(c[0], c[1]) for c in line] for line in geometry["coordinates"]]
        else:
            failures.append(f"{label}: geometry is not a line")
            continue
        max_mi = float(entry.get("max_mi", CA_REMNANT_DEFAULT_MAX_MI))
        verdict = split_stray_pieces(lines, max_mi)
        if verdict["status"] != "ok":
            failures.append(f"{label}: the main piece is ambiguous (two pieces of equal length)")
            continue
        if not verdict["stray"]:
            failures.append(f"{label}: no stray piece of at most {max_mi} mi lies more than "
                            f"{CA_REMNANT_MIN_DIST_M:g} m from the main piece ({verdict['components']} piece(s)); "
                            "remove the entry")
            continue
        gone = {i for item in verdict["stray"] for i in item["indices"]}
        kept = [line for i, line in enumerate(lines) if i not in gone]
        before = (feature.get("properties") or {}).get("length_mi")
        after = round(sum(model.line_mi(line) for line in kept), 3)
        props = dict(feature["properties"])
        props["length_mi"] = after
        out[hits[0]] = {**feature, "properties": props,
                        "geometry": {"type": "MultiLineString",
                                     "coordinates": [[list(p) for p in line] for line in kept]}}
        report.append({"name": name, "source": entry["source"], "max_mi": max_mi,
                       "length_mi_before": before, "length_mi_after": after,
                       "main_mi": round(verdict["main_mi"], 3),
                       "dropped": [{"mi": round(item["mi"], 3), "distance_m": round(item["distance_m"], 1)}
                                   for item in verdict["stray"]]})
    return out, report, failures
