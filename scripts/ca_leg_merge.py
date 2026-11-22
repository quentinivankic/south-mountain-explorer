#!/usr/bin/env python3
"""Canada leg_merges: merge the legs/pieces OSM maps separately into ONE named trail.
Runs in scripts/ca-run-area.sh after the optional adopt_ways / hiking_tag_wins / way_name_overrides steps and
before ca_trail_filter, only for an area whose registry entry has `leg_merges`. Some agency trails are mapped
in OSM as several same-route pieces (Grey Owl Trail as five campsite-to-campsite legs, Gun Creek Trail as
three sections, Prospector's Trail as Loop A-D). Each entry
  {name, legs: [{name, way_ids}, ...], source, reason, new_id, old_ids[, max_gap_m]}
replaces exactly those assembled legs with ONE feature (source "leg-merge") named `name`. A leg is an
assembled trail (or an assembler-dropped piece under the size floor) found by its exact OSM name AND its exact
OSM way set, so a changed extract fails closed instead of merging something else.
Id plan: `old_ids` are the live trail ids of the legs and `new_id` is the one id the merged trail gets. ca_publish
checks them against the live geom and the published geom (update mode keeps every other id).
Gates, all fail closed (exit 2 "SAFETY BLOCK"; trails and removed files untouched):
  - each leg matches exactly one feature by name and way set; a dropped leg must be a size-floor drop;
  - the legs form ONE whole route: every leg joins the rest within max_gap_m (default 30 m, cap 100 m), measured
    between the leg geometries, and no geometry is ever invented to bridge a gap (a merge with a wider gap is
    rejected, not stitched);
  - at least 80% of the merged length lies inside the exact-area boundary file (each leg already passed the
    whole-route curation; the BC waivers accepted 81-83%, so 90% would reject Gun Creek);
  - the new name is not the name of a trail that stays in the area;
  - no leg is used by two entries.
Nothing here touches shared engine code; no OSM edit is made. Absent key = this step never runs.
"""
from __future__ import annotations
import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ca_adopt_ways  # noqa: E402
import ca_trail_filter  # noqa: E402
LM_KEYS = {"name", "legs", "source", "reason", "new_id", "old_ids"}
OPTIONAL_KEYS = {"max_gap_m"}
MIN_LEGS = 2
MAX_LEGS = 12
DEFAULT_GAP_M = 30.0
MAX_GAP_M = 100.0
MIN_INSIDE = 0.80
M_PER_MI = 1609.344
SOURCE_TAG = "leg-merge"
ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
# An assembler-dropped piece may be merged back only when it was dropped for being short.
SIZE_DROP_RE = re.compile(r"^(Too short\b|[0-9.]+ mi is under [0-9.]+ mi$)")
class MergeError(Exception):
    pass
def _posint(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v > 0
def validate_leg_merges(items: object, where: str = "leg_merges") -> list[dict]:
    """The validated list, or MergeError. Shared by ca_publish.load_registry."""
    if not isinstance(items, list) or not items:
        raise MergeError(f"{where} must be a non-empty list when present")
    names: set[str] = set()
    used_legs: set[tuple[str, ...]] = set()
    used_ways: set[int] = set()
    used_ids: set[str] = set()
    for item in items:
        if not isinstance(item, dict) or not LM_KEYS <= set(item) or set(item) - LM_KEYS - OPTIONAL_KEYS:
            raise MergeError(f"{where} entries need {sorted(LM_KEYS)} and may add {sorted(OPTIONAL_KEYS)}")
        label = f"{where} {item.get('name')!r}"
        try:
            ca_trail_filter._clean_text(item["name"], where, "name")
            ca_trail_filter._check_source(item, label)
        except ca_trail_filter.FilterError as error:
            raise MergeError(str(error)) from None
        if ca_trail_filter.fold(item["name"]) in names:
            raise MergeError(f"{where} repeats {item['name']!r}")
        names.add(ca_trail_filter.fold(item["name"]))
        legs = item["legs"]
        if not isinstance(legs, list) or not MIN_LEGS <= len(legs) <= MAX_LEGS:
            raise MergeError(f"{label}: legs must list {MIN_LEGS}..{MAX_LEGS} legs (a merge needs at least two)")
        leg_names: set[str] = set()
        for leg in legs:
            if (not isinstance(leg, dict) or set(leg) != {"name", "way_ids"}
                    or not isinstance(leg["name"], str) or not leg["name"].strip()):
                raise MergeError(f"{label}: each leg needs exactly name and way_ids")
            ids = leg["way_ids"]
            if not isinstance(ids, list) or not ids or not all(_posint(i) for i in ids) or len(set(ids)) != len(ids):
                raise MergeError(f"{label}: leg {leg['name']!r} way_ids must be unique positive integers")
            if used_ways & set(ids):
                raise MergeError(f"{label}: a way id of leg {leg['name']!r} is already used by another leg or entry")
            used_ways |= set(ids)
            key = (leg["name"], *map(str, sorted(ids)))
            if key in used_legs or leg["name"] in leg_names:
                raise MergeError(f"{label}: leg {leg['name']!r} is listed twice")
            used_legs.add(key)
            leg_names.add(leg["name"])
        gap = item.get("max_gap_m", DEFAULT_GAP_M)
        if isinstance(gap, bool) or not isinstance(gap, (int, float)) or not 0 < gap <= MAX_GAP_M:
            raise MergeError(f"{label}: max_gap_m must be a number in (0, {MAX_GAP_M:g}]")
        new_id, old_ids = item["new_id"], item["old_ids"]
        if not isinstance(new_id, str) or not ID_RE.fullmatch(new_id):
            raise MergeError(f"{label}: new_id must be a lowercase slug")
        if (not isinstance(old_ids, list) or not old_ids or not all(isinstance(i, str) and ID_RE.fullmatch(i) for i in old_ids)
                or len(set(old_ids)) != len(old_ids)):
            raise MergeError(f"{label}: old_ids must be a non-empty list of unique lowercase slugs")
        if len(old_ids) > len(legs):
            raise MergeError(f"{label}: old_ids lists more ids ({len(old_ids)}) than legs ({len(legs)})")
        if used_ids & ({new_id} | set(old_ids)):
            raise MergeError(f"{label}: an id is already used by another entry")
        used_ids |= {new_id} | set(old_ids)
    return items
# ---------------------------------------------------------------- geometry --
def _parts(geom: dict) -> list[list[tuple[float, float]]]:
    coords = geom.get("coordinates") or []
    if geom.get("type") == "LineString":
        coords = [coords]
    elif geom.get("type") != "MultiLineString":
        raise MergeError(f"unsupported leg geometry {geom.get('type')!r}")
    return [[(float(p[0]), float(p[1])) for p in part] for part in coords if len(part) >= 2]
def _project(parts: list[list[tuple[float, float]]], lat0: float) -> list[list[tuple[float, float]]]:
    kx = 111320.0 * math.cos(math.radians(lat0))
    return [[(lon * kx, lat * 110574.0) for lon, lat in part] for part in parts]
def leg_gap_m(a: list[list[tuple[float, float]]], b: list[list[tuple[float, float]]]) -> float:
    """Shortest distance in metres between two legs' line parts (local equirectangular projection)."""
    from shapely.geometry import MultiLineString
    lat0 = sum(p[1] for part in a + b for p in part) / sum(len(part) for part in a + b)
    return float(MultiLineString(_project(a, lat0)).distance(MultiLineString(_project(b, lat0))))
def widest_join_gap(leg_parts: list[list[list[tuple[float, float]]]]) -> float:
    """Smallest W such that the legs form one connected route when pieces closer than W are joined: the
    largest edge of the minimum spanning tree over leg-to-leg distances (0.0 for touching legs)."""
    n = len(leg_parts)
    dist = {(i, j): leg_gap_m(leg_parts[i], leg_parts[j]) for i in range(n) for j in range(i + 1, n)}
    in_tree = {0}
    widest = 0.0
    while len(in_tree) < n:
        best = min(((dist[(min(i, j), max(i, j))], j) for i in in_tree for j in range(n) if j not in in_tree))
        widest = max(widest, best[0])
        in_tree.add(best[1])
    return widest
def _length_m(parts: list[list[tuple[float, float]]]) -> float:
    return sum(ca_adopt_ways._line_m(p) for p in parts)
def _inside_m(parts: list[list[tuple[float, float]]], boundary: Any) -> float:
    from shapely.geometry import LineString
    return sum(ca_adopt_ways._line_m(list(seg.coords)) for p in parts
               for seg in ca_adopt_ways._lines_of(LineString(p).intersection(boundary)))
# ------------------------------------------------------------------- build --
def _find_leg(leg: dict, kept: list[dict], dropped: list[dict], label: str) -> tuple[dict, str]:
    """(feature, "trails" | "removed") for the one feature with the leg's exact name and way set."""
    want = set(leg["way_ids"])
    def hits(pool: list[dict]) -> list[dict]:
        return [f for f in pool if (f.get("properties") or {}).get("name") == leg["name"]
                and set((f.get("properties") or {}).get("member_ways") or []) == want]
    in_kept, in_dropped = hits(kept), hits(dropped)
    if len(in_kept) + len(in_dropped) != 1:
        raise MergeError(f"{label}: leg {leg['name']!r} with ways {sorted(want)} matches "
                         f"{len(in_kept) + len(in_dropped)} feature(s), expected exactly 1")
    if in_kept:
        return in_kept[0], "trails"
    reason = str((in_dropped[0].get("properties") or {}).get("removed_reason") or "")
    if not SIZE_DROP_RE.match(reason):
        raise MergeError(f"{label}: leg {leg['name']!r} was dropped for {reason!r}, not for being short")
    return in_dropped[0], "removed"
def build(entries: list[dict], features: list[dict], dropped: list[dict], boundary: Any,
          area_name: str) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """(trails, removed, evidence, merged features). Raises MergeError on any failed check; inputs are not mutated."""
    kept, gone = list(features), list(dropped)
    merged, evidence = [], []
    for entry in entries:
        label = f"leg_merges {entry['name']!r}"
        found = [_find_leg(leg, kept, gone, label) for leg in entry["legs"]]
        leg_feats = [f for f, _ in found]
        parts = [_parts(f["geometry"]) for f in leg_feats]
        if any(not p for p in parts):
            raise MergeError(f"{label}: a leg has no line geometry")
        widest = widest_join_gap(parts)
        limit = float(entry.get("max_gap_m", DEFAULT_GAP_M))
        if widest > limit:
            raise MergeError(f"{label}: the legs leave a {widest:.1f} m gap, wider than the {limit:g} m tolerance "
                             "(not one whole route; nothing is stitched)")
        all_parts = [p for ps in parts for p in ps]
        total_m = _length_m(all_parts)
        if boundary is None:
            raise MergeError(f"{label}: no exact-area boundary file to check the merged route against")
        inside_m = _inside_m(all_parts, boundary)
        if inside_m < MIN_INSIDE * total_m:
            raise MergeError(f"{label}: {inside_m / M_PER_MI:.2f} of {total_m / M_PER_MI:.2f} mi inside the "
                             f"boundary (needs {MIN_INSIDE:.0%})")
        leg_ids = {id(f) for f in leg_feats}
        staying = {ca_trail_filter.fold((f.get("properties") or {}).get("name")) for f in kept if id(f) not in leg_ids}
        if ca_trail_filter.fold(entry["name"]) in staying:
            raise MergeError(f"{label}: a trail that stays in the area already has this name")
        ways = sorted({w for f in leg_feats for w in (f["properties"].get("member_ways") or [])})
        props = {"name": entry["name"], "kind": "trail", "area": area_name, "source": SOURCE_TAG,
                 "length_mi": round(total_m / M_PER_MI, 3), "member_ways": ways, "destinations": [], "welds": [],
                 "network": "", "operator": "", "sac_scale": "", "trail_visibility": "",
                 "removed_reason": None, "removed_category": None,
                 "ckey": "merge-" + "-".join(f"w{w}" for w in ways), "merge_source": entry["source"],
                 "merged_legs": [leg["name"] for leg in entry["legs"]]}
        feature = {"type": "Feature", "properties": props,
                   "geometry": {"type": "MultiLineString", "coordinates": [[list(p) for p in part] for part in all_parts]}}
        kept = [f for f in kept if id(f) not in leg_ids]
        gone = [f for f in gone if id(f) not in leg_ids]
        merged.append(feature)
        evidence.append({"name": entry["name"], "new_id": entry["new_id"], "old_ids": entry["old_ids"],
                         "legs": [{"name": f["properties"]["name"], "from": src,
                                   "length_mi": round(_length_m(_parts(f["geometry"])) / M_PER_MI, 3)}
                                  for f, src in found],
                         "length_mi": props["length_mi"], "inside_mi": round(inside_m / M_PER_MI, 3),
                         "widest_gap_m": round(widest, 1), "max_gap_m": limit,
                         "source": entry["source"], "reason": entry["reason"]})
    return kept + merged, gone, evidence, merged
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--registry", required=True)
    ap.add_argument("--area-id", required=True)
    ap.add_argument("--trails", required=True)
    ap.add_argument("--removed", required=True)
    ap.add_argument("--report", required=True)
    args = ap.parse_args(argv)
    try:
        registry = json.loads(Path(args.registry).read_text(encoding="utf-8"))
        entry = next((a for a in registry["areas"] if a["id"] == args.area_id), None)
        if entry is None or "leg_merges" not in entry:
            raise MergeError(f"{args.area_id}: no leg_merges in the registry")
        entries = validate_leg_merges(entry["leg_merges"], f"{args.area_id}: leg_merges")
        trails_path, removed_path = Path(args.trails), Path(args.removed)
        doc = json.loads(trails_path.read_text(encoding="utf-8"))
        rdoc = json.loads(removed_path.read_text(encoding="utf-8"))
        trails, removed, evidence, _ = build(entries, doc["features"], rdoc["features"],
                                             ca_adopt_ways.load_boundary(trails_path), entry["name"])
    except (MergeError, OSError, ValueError, KeyError) as error:
        print(f"SAFETY BLOCK: {error}", file=sys.stderr)
        return 2
    doc["features"], rdoc["features"] = trails, removed
    trails_path.write_text(json.dumps(doc), encoding="utf-8")
    removed_path.write_text(json.dumps(rdoc), encoding="utf-8")
    Path(args.report).write_text(json.dumps({"area": args.area_id, "merged": evidence}, indent=2) + "\n",
                                 encoding="utf-8")
    print(f"leg_merges: {len(evidence)} trail(s) merged in {trails_path.name}")
    return 0
if __name__ == "__main__":
    sys.exit(main())
