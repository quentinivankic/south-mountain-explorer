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
