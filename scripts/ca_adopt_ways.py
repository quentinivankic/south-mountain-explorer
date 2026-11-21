#!/usr/bin/env python3
"""Canada adopt_ways: name a specific set of unnamed OSM ways as one official trail.
Runs in scripts/ca-run-area.sh between assemble.py and ca_trail_filter.py, only for an area whose
registry entry has `adopt_ways`. The generic assembler drops unnamed ways and ca_trail_filter never
waives "unnamed", so an agency-listed hike whose path is unnamed in OSM cannot otherwise appear.
Each entry {name, way_ids, source, reason[, max_gap_m, allow_piste]} appends ONE feature to the
assembled `trails.geojson` (same schema, source "adopt-ways"). The feature then goes through
ca_trail_filter like any trail (vocabulary rules, 0.6 mi floor unless keep_trails), publish_areas
and the live-duplicate gates. Nothing here touches shared engine code; no OSM edit is made.
Checks, all fail closed (exit 2 "SAFETY BLOCK"; the trails file is untouched on failure):
  every way is in the extract and is a path/footway/steps/pedestrian way; no access=private|no,
  foot=no, mtb:scale, or piste:type (unless listed in allow_piste); the ways chain into one route
  (shared nodes, remaining gaps <= max_gap_m, default 30); at least 90% of the length lies inside the
  area boundary (`<trails stem>.exact-area.geojson`, written by assemble --require-exact-area); no way
  is already a member of an assembled trail; the name is not already an assembled trail.
"""
from __future__ import annotations
import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ca_trail_filter  # noqa: E402

ADOPT_KEYS = {"name", "way_ids", "source", "reason"}
OPTIONAL_KEYS = {"max_gap_m", "allow_piste"}
MAX_WAYS = 60
DEFAULT_GAP_M = 30.0
MAX_GAP_M = 100.0
MIN_INSIDE = 0.90
ALLOWED_HIGHWAY = {"path", "footway", "steps", "pedestrian"}
M_PER_MI = 1609.344


class AdoptError(Exception):
    pass


def _posint(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v > 0


def validate_adopt_ways(items: object, where: str = "adopt_ways") -> list[dict]:
    """The validated list, or AdoptError. Shared by ca_publish.load_registry."""
    if not isinstance(items, list) or not items:
        raise AdoptError(f"{where} must be a non-empty list when present")
    names: set[str] = set()
    used: set[int] = set()
    for item in items:
        if not isinstance(item, dict) or not ADOPT_KEYS <= set(item) or set(item) - ADOPT_KEYS - OPTIONAL_KEYS:
            raise AdoptError(f"{where} entries need {sorted(ADOPT_KEYS)} and may add {sorted(OPTIONAL_KEYS)}")
        try:
            ca_trail_filter._clean_text(item["name"], where, "name")
            ca_trail_filter._check_source(item, f"{where} {item['name']!r}")
        except ca_trail_filter.FilterError as error:
            raise AdoptError(str(error)) from None
        label = f"{where} {item['name']!r}"
        if ca_trail_filter.fold(item["name"]).startswith(("unnamed", "sans nom")):
            raise AdoptError(f"{label}: name must be a real name")
        if ca_trail_filter.fold(item["name"]) in names:
            raise AdoptError(f"{where} repeats {item['name']!r}")
        names.add(ca_trail_filter.fold(item["name"]))
        ids = item["way_ids"]
        if (not isinstance(ids, list) or not ids or len(ids) > MAX_WAYS
                or not all(_posint(i) for i in ids) or len(set(ids)) != len(ids)):
            raise AdoptError(f"{label}: way_ids must be 1..{MAX_WAYS} unique positive integers")
        if used & set(ids):
            raise AdoptError(f"{label}: a way id is already adopted by another entry")
        used |= set(ids)
        gap = item.get("max_gap_m", DEFAULT_GAP_M)
        if isinstance(gap, bool) or not isinstance(gap, (int, float)) or not 0 < gap <= MAX_GAP_M:
            raise AdoptError(f"{label}: max_gap_m must be a number in (0, {MAX_GAP_M:g}]")
        piste = item.get("allow_piste", [])
        if not isinstance(piste, list) or not all(i in ids for i in piste):
            raise AdoptError(f"{label}: allow_piste must list way ids that are in way_ids")
    return items


def _haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    (lo1, la1), (lo2, la2) = a, b
    p1, p2 = math.radians(la1), math.radians(la2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lo2 - lo1) / 2) ** 2
    return 2 * 6371008.8 * math.asin(math.sqrt(h))


def _line_m(coords: list[tuple[float, float]]) -> float:
    return sum(_haversine_m(coords[i], coords[i + 1]) for i in range(len(coords) - 1))


def _way_problems(wid: int, way: dict, allow_piste: list[int]) -> list[str]:
    tags = way["tags"]
    out = []
    if tags.get("highway") not in ALLOWED_HIGHWAY:
        out.append(f"way {wid}: highway={tags.get('highway')!r} is not one of {sorted(ALLOWED_HIGHWAY)}")
    if tags.get("access") in ("private", "no"):
        out.append(f"way {wid}: access={tags['access']}")
    if tags.get("foot") == "no":
        out.append(f"way {wid}: foot=no")
    if "mtb:scale" in tags:
        out.append(f"way {wid}: mtb:scale is tagged")
    if "piste:type" in tags and wid not in allow_piste:
        out.append(f"way {wid}: piste:type={tags['piste:type']} (list it in allow_piste if it is the hiking trail)")
    return out


def _components(ways: dict[int, dict], ids: list[int], gap_m: float) -> tuple[int, float]:
    """(pieces joined by shared nodes, widest gap needed to join them all) ; gap is inf if > gap_m."""
    parent = {i: i for i in ids}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    owner: dict[int, int] = {}
    for i in ids:
        for n in ways[i]["nodes"]:
            if n in owner:
                parent[find(i)] = find(owner[n])
            else:
                owner[n] = i
    pieces = len({find(i) for i in ids})
    widest = 0.0
    while len({find(i) for i in ids}) > 1:
        best = None
        for a in ids:
            for b in ids:
                if find(a) >= find(b):
                    continue
                for pa in (ways[a]["coords"][0], ways[a]["coords"][-1]):
                    for pb in (ways[b]["coords"][0], ways[b]["coords"][-1]):
                        d = _haversine_m(pa, pb)
                        if best is None or d < best[0]:
                            best = (d, a, b)
        if best is None or best[0] > gap_m:
            return pieces, math.inf
        widest = max(widest, best[0])
        parent[find(best[1])] = find(best[2])
    return pieces, widest


def build_features(entries: list[dict], ways: dict[int, dict], features: list[dict], boundary: Any,
                   area_name: str) -> tuple[list[dict], list[dict]]:
    """(new features, per-entry evidence). Raises AdoptError on any failed check."""
    existing = {ca_trail_filter.fold((f.get("properties") or {}).get("name")) for f in features}
    taken = {w for f in features for w in (f.get("properties") or {}).get("member_ways") or []}
    new, evidence = [], []
    for entry in entries:
        label = f"adopt_ways {entry['name']!r}"
        ids = entry["way_ids"]
        missing = [i for i in ids if i not in ways]
        if missing:
            raise AdoptError(f"{label}: ways missing from the extract: {missing[:8]}")
        problems = [p for i in ids for p in _way_problems(i, ways[i], entry.get("allow_piste", []))]
        if problems:
            raise AdoptError(f"{label}: " + "; ".join(problems[:6]))
        if ca_trail_filter.fold(entry["name"]) in existing:
            raise AdoptError(f"{label}: an assembled trail already has this name")
        clash = sorted(set(ids) & taken)
        if clash:
            raise AdoptError(f"{label}: ways already in an assembled trail: {clash[:8]}")
        gap_limit = float(entry.get("max_gap_m", DEFAULT_GAP_M))
        pieces, widest = _components(ways, ids, gap_limit)
        if math.isinf(widest):
            raise AdoptError(f"{label}: the ways do not chain into one route within {gap_limit:g} m")
        lines = [ways[i]["coords"] for i in ids]
        total_m = sum(_line_m(c) for c in lines)
        inside_m = total_m
        if boundary is not None:
            from shapely.geometry import LineString
            inside_m = sum(_line_m(list(seg.coords)) for c in lines
                           for seg in _lines_of(LineString(c).intersection(boundary)))
        if boundary is None or inside_m < MIN_INSIDE * total_m:
            raise AdoptError(f"{label}: {inside_m / M_PER_MI:.2f} of {total_m / M_PER_MI:.2f} mi inside the "
                             f"boundary (needs {MIN_INSIDE:.0%}, and an exact-area boundary file)")
        length_mi = round(total_m / M_PER_MI, 3)
        props = {"name": entry["name"], "kind": "trail", "area": area_name, "source": "adopt-ways",
                 "length_mi": length_mi, "member_ways": list(ids), "destinations": [], "welds": [],
                 "network": "", "operator": "", "sac_scale": "", "trail_visibility": "",
                 "removed_reason": None, "removed_category": None,
                 "ckey": "adopt-" + "-".join(f"w{i}" for i in sorted(ids)),
                 "adopt_source": entry["source"]}
        new.append({"type": "Feature", "properties": props,
                    "geometry": {"type": "MultiLineString",
                                 "coordinates": [[list(p) for p in c] for c in lines]}})
        existing.add(ca_trail_filter.fold(entry["name"]))
        taken |= set(ids)
        evidence.append({"name": entry["name"], "way_ids": ids, "length_mi": length_mi,
                         "inside_mi": round(inside_m / M_PER_MI, 3), "pieces_by_shared_nodes": pieces,
                         "widest_gap_m": round(widest, 1), "source": entry["source"], "reason": entry["reason"]})
    return new, evidence


def _lines_of(geom: Any) -> list[Any]:
    if geom.is_empty:
        return []
    if geom.geom_type == "LineString":
        return [geom]
    return [g for part in getattr(geom, "geoms", []) for g in _lines_of(part)]


def read_ways(pbf: str, wanted: set[int]) -> dict[int, dict]:
    import osmium

    found: dict[int, dict] = {}

    class _H(osmium.SimpleHandler):
        def way(self, w):
            if w.id in wanted:
                try:
                    coords = [(n.lon, n.lat) for n in w.nodes]
                except Exception:
                    return
                found[w.id] = {"tags": {t.k: t.v for t in w.tags}, "nodes": [n.ref for n in w.nodes],
                               "coords": coords}

    _H().apply_file(pbf, locations=True)
    return found


def load_boundary(trails_path: Path) -> Any:
    path = trails_path.with_name(trails_path.name.replace(".geojson", ".exact-area.geojson"))
    if not path.is_file():
        return None
    from shapely.geometry import shape
    from shapely.ops import unary_union
    doc = json.loads(path.read_text(encoding="utf-8"))
    geoms = [shape(f["geometry"]) for f in doc.get("features") or []]
    return unary_union(geoms) if geoms else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--registry", required=True)
    ap.add_argument("--area-id", required=True)
    ap.add_argument("--pbf", required=True)
    ap.add_argument("--trails", required=True)
    ap.add_argument("--report", required=True)
    args = ap.parse_args(argv)
    try:
        registry = json.loads(Path(args.registry).read_text(encoding="utf-8"))
        entry = next((a for a in registry["areas"] if a["id"] == args.area_id), None)
        if entry is None or "adopt_ways" not in entry:
            raise AdoptError(f"{args.area_id}: no adopt_ways in the registry")
        entries = validate_adopt_ways(entry["adopt_ways"], f"{args.area_id}: adopt_ways")
        trails_path = Path(args.trails)
        doc = json.loads(trails_path.read_text(encoding="utf-8"))
        features = doc["features"]
        ways = read_ways(args.pbf, {i for e in entries for i in e["way_ids"]})
        new, evidence = build_features(entries, ways, features, load_boundary(trails_path), entry["name"])
    except (AdoptError, OSError, ValueError, KeyError) as error:
        print(f"SAFETY BLOCK: {error}", file=sys.stderr)
        return 2
    doc["features"] = features + new
    trails_path.write_text(json.dumps(doc), encoding="utf-8")
    Path(args.report).write_text(json.dumps({"area": args.area_id, "adopted": evidence}, indent=2) + "\n",
                                 encoding="utf-8")
    print(f"adopt_ways: {len(new)} trail(s) appended to {trails_path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
