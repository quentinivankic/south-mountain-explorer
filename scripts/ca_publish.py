#!/usr/bin/env python3
"""Registry-driven Canada publish helpers (N areas per run, one province per run).

Canada lane on the UNMODIFIED generic engine (no --region, no country flags). Cloned from
the earlier country-lane registry tooling with its rule sets, container machinery, update and remove
families removed. Nothing here calls R2/AWS; the sync workflow does that with the files this
script builds. Ids are hand-set `<slug>-ca-<prov>` (never generated), single state "Canada".

  select            resolve the dispatch `areas` input (ids or batch=<n>) against
                    trailforge/golden/ca-areas.json for ONE province; enforce the chunk cap
  prepare-areas     derive each area's boundary bbox from the PBF boundary relation/way,
                    check/propose the padded bbox + centre, write the per-area one-row
                    publish index. Fail closed on a missing or inconsistent registry bbox
  check-boundary-unique   fail closed unless exactly one boundary of the registry name sits
                    in the area's AOI extract (the generic engine unions same-name polygons)
  assert-expected   refuse unless a published geom matches the blessed table
  report-area       qualify: per-area report from the pipeline outputs
  qualify-aggregate qualify: merge per-area reports + proposed tables; fails closed on a
                    route listed in two areas of the set or in a nearby live area
  merge-index       append the new area rows to public/areas/index.json
  verify-staged / verify-commit   exact 2N+2 path allowlist, rows append-only
  derive-areas / build-candidates   splice N areas onto LIVE R2 bytes
  rollback-decide   per shared file restore/skip (refuse on third-party bytes)
                    plus the exact 2N keys to delete
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ca_trail_filter  # noqa: E402
import r2_release_contract as contract  # noqa: E402
import simple_mols_publish as smp  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / "trailforge" / "golden" / "ca-areas.json"
EXPECTED_PATH = ROOT / "trailforge" / "golden" / "ca-areas-expected.json"
INDEX_REPO = "public/areas/index.json"
BUNDLE_REPO = "ios/SouthMountainExplorer/Resources/areas-index.json"
REGISTRY_SCHEMA = "trekdex-ca-areas-v1"
EXPECTED_SCHEMA = "trekdex-ca-areas-expected-v1"
STATE = "Canada"
EXTRACT_BASE = "north-america/canada"
# Province code -> Geofabrik extract slug (one province per run). NT/NU are out of scope.
PROVINCES = {
    "ab": "alberta", "bc": "british-columbia", "mb": "manitoba", "nb": "new-brunswick",
    "nl": "newfoundland-and-labrador", "ns": "nova-scotia", "on": "ontario",
    "pe": "prince-edward-island", "qc": "quebec", "sk": "saskatchewan", "yt": "yukon",
}
# Documented hold reasons (a held entry is skipped by batch= selection; name it in areas= to run it).
HOLD_REASONS = {
    "island", "np-nested", "name-collision", "duplicate-of-live", "needs-rescope",
    "merge-pending", "mtb-centre", "curated-do-not-ship", "few-trails", "municipal-deferred",
}
# Curated per-area exclusions (registry `exclude_trails`): {"id": "n:<name>" | "n*:<name>", "name",
# "reason"}; applied by scripts/ca_trail_filter.py (fail closed on no match). The generic assembler
# writes no relation ids on its trails, so there is no r<id> selector.
EXCLUDE_ID = re.compile(r"^n:\S.*$")
EXCLUDE_ALL_ID = re.compile(r"^n\*:\S.*$")
EXCLUDE_KEYS = {"id", "name", "reason"}
DEFAULT_CHUNK = 25
HARD_MAX_CHUNK = 40
MAX_SYNC_AREAS = 120
PAD = 0.05
MARGIN_MIN = 0.02          # registry bbox must clear the boundary by this much...
MARGIN_MAX = 0.10          # ...and by no more than this
DEFAULT_MI_TOLERANCE = 0.05
SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*-ca-(" + "|".join(sorted(PROVINCES)) + r")$")
SHARED_KEYS = smp.SHARED_KEYS
CA_LON, CA_LAT = (-141.1, -52.0), (41.0, 84.0)


class CaError(Exception):
    pass


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_json(path: Path, what: str) -> Any:
    try:
        return contract.strict_json_loads(Path(path).read_text(encoding="utf-8"), what)
    except (OSError, contract.InventoryError) as error:
        raise CaError(f"cannot read {what}: {error}") from error


def _write_json(path: Path, document: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _is_num(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def province_of(area_id: str) -> str:
    return area_id.rsplit("-", 1)[1]


def pbf_url(province: str) -> str:
    return f"https://download.geofabrik.de/{EXTRACT_BASE}/{PROVINCES[province]}-latest.osm.pbf"


# ----------------------------------------------------------------- registry --

def _check_exclude_trails(aid: str, entry: dict[str, Any], field: str = "exclude_trails") -> None:
    if field not in entry:
        return
    items = entry[field]
    if not isinstance(items, list) or not items:
        raise CaError(f"{aid}: {field} must be a non-empty list when present")
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict) or set(item) != EXCLUDE_KEYS:
            raise CaError(f"{aid}: {field} entries need exactly {sorted(EXCLUDE_KEYS)}")
        if not all(isinstance(item[k], str) and item[k].strip() for k in EXCLUDE_KEYS):
            raise CaError(f"{aid}: {field} entry has an empty field: {item.get('id')!r}")
        ident, name = item["id"], item["name"]
        multi = bool(EXCLUDE_ALL_ID.fullmatch(ident))
        if (ident != unicodedata.normalize("NFC", ident) or name != unicodedata.normalize("NFC", name)
                or not (multi or EXCLUDE_ID.fullmatch(ident))):
            raise CaError(f"{aid}: {field} id/name is invalid: {ident!r}")
        if (ident.startswith("n:") and ident[2:] != name) or (multi and ident[3:] != name):
            raise CaError(f"{aid}: {field} {ident!r}: name must equal the id name")
        if ident in seen:
            raise CaError(f"{aid}: {field} repeats {ident!r}")
        seen.add(ident)


def _check_keep_trails(aid: str, entry: dict[str, Any]) -> None:
    """Official-source waiver that keeps a trail the vocabulary or stub rules would drop."""
    if "keep_trails" not in entry:
        return
    items = entry["keep_trails"]
    if not isinstance(items, list) or not items:
        raise CaError(f"{aid}: keep_trails must be a non-empty list when present")
    try:
        ca_trail_filter.validate_keep_trails(items, f"{aid}: keep_trails")
    except ca_trail_filter.FilterError as error:
        raise CaError(str(error)) from None


def load_registry(path: Path = REGISTRY_PATH) -> dict[str, Any]:
    doc = _read_json(path, "Canada area registry")
    if not isinstance(doc, dict) or doc.get("schema") != REGISTRY_SCHEMA:
        raise CaError("registry schema is not " + REGISTRY_SCHEMA)
    if (doc.get("extract_base"), doc.get("state")) != (EXTRACT_BASE, STATE):
        raise CaError("registry extract_base/state are not the Canada pins")
    areas = doc.get("areas")
    if not isinstance(areas, list):
        raise CaError("registry areas is not a list")
    ids: set[str] = set()
    rels: set[int] = set()
    ways: set[int] = set()
    for entry in areas:
        if not isinstance(entry, dict):
            raise CaError("registry entry is not an object")
        aid, name, rel = entry.get("id"), entry.get("name"), entry.get("relation_id")
        way = entry.get("way_id")
        if not isinstance(aid, str) or not SLUG.fullmatch(aid):
            raise CaError(f"registry id is invalid: {aid!r}")
        if aid in ids:
            raise CaError(f"registry repeats id {aid}")
        if entry.get("province") != province_of(aid) or entry.get("province") not in PROVINCES:
            raise CaError(f"{aid}: province must equal the id suffix and be a supported province")
        if (not isinstance(name, str) or not name.strip()
                or name != unicodedata.normalize("NFC", name)):
            raise CaError(f"{aid}: name is invalid")
        if entry.get("state") != STATE:
            raise CaError(f"{aid}: state must be {STATE!r}")
        if "way_id" in entry:
            if "relation_id" in entry:
                raise CaError(f"{aid}: exactly one of relation_id and way_id is allowed")
            if not isinstance(way, int) or isinstance(way, bool) or way <= 0 or way in ways:
                raise CaError(f"{aid}: way_id is invalid or repeated")
        elif not isinstance(rel, int) or isinstance(rel, bool) or rel <= 0 or rel in rels:
            raise CaError(f"{aid}: relation_id is invalid or repeated")
        shown = entry.get("display_name")
        if shown is not None and (not isinstance(shown, str) or not shown.strip() or shown == name
                                  or shown != unicodedata.normalize("NFC", shown)):
            raise CaError(f"{aid}: display_name must be a non-empty NFC text different from name")
        bbox, center = entry.get("bbox"), entry.get("center")
        if (bbox is None) != (center is None):
            raise CaError(f"{aid}: bbox and center must both be set or both be null")
        if bbox is not None:
            if (not isinstance(bbox, list) or len(bbox) != 4 or not all(_is_num(v) for v in bbox)
                    or not (CA_LON[0] <= bbox[0] < bbox[2] <= CA_LON[1]
                            and CA_LAT[0] <= bbox[1] < bbox[3] <= CA_LAT[1])):
                raise CaError(f"{aid}: bbox must be [lon_min, lat_min, lon_max, lat_max] inside Canada")
            if (not isinstance(center, list) or len(center) != 2
                    or not all(_is_num(v) for v in center)):
                raise CaError(f"{aid}: center must be [lat, lon]")
        if entry.get("tier") not in (1, 2, 3) or isinstance(entry.get("tier"), bool):
            raise CaError(f"{aid}: tier must be 1 (national), 2 (provincial/reserve) or 3 (regional)")
        if not isinstance(entry.get("notes", ""), str):
            raise CaError(f"{aid}: notes must be text")
        hold = entry.get("hold")
        if hold is not None and (not isinstance(hold, str) or not hold.strip()):
            raise CaError(f"{aid}: hold must be null or a reason string")
        if hold is not None and hold not in HOLD_REASONS:
            raise CaError(f"{aid}: hold {hold!r} is not one of {sorted(HOLD_REASONS)}")
        _check_exclude_trails(aid, entry)
        _check_keep_trails(aid, entry)
        if "trail_name_fixes" in entry:
            try:
                ca_trail_filter.validate_fixes(entry["trail_name_fixes"], f"{aid}: trail_name_fixes")
            except ca_trail_filter.FilterError as error:
                raise CaError(str(error)) from None
        est_mi = entry.get("est_mi")
        if est_mi is not None and (not _is_num(est_mi) or est_mi < 0):
            raise CaError(f"{aid}: est_mi must be a non-negative number")
        est_trails = entry.get("est_trails")
        if est_trails is not None and (not isinstance(est_trails, int) or isinstance(est_trails, bool)
                                       or est_trails < 0):
            raise CaError(f"{aid}: est_trails must be a non-negative integer")
        ids.add(aid)
        if "way_id" in entry:
            ways.add(way)
        else:
            rels.add(rel)
    doc["by_id"] = {a["id"]: a for a in areas}
    return doc


def bbox_arg(bbox: list[float]) -> str:
    return ",".join(f"{v:.2f}" for v in bbox)


# ------------------------------------------------------------ expected table --

def load_expected(path: Path = EXPECTED_PATH) -> dict[str, dict[str, Any]]:
    doc = _read_json(path, "blessed expected table")
    if not isinstance(doc, dict) or doc.get("schema") != EXPECTED_SCHEMA or not isinstance(doc.get("areas"), dict):
        raise CaError("expected table schema is invalid")
    for aid, entry in doc["areas"].items():
        if (not isinstance(entry, dict) or not SLUG.fullmatch(aid)
                or not isinstance(entry.get("trail_count"), int) or isinstance(entry.get("trail_count"), bool)
                or entry["trail_count"] <= 0 or not _is_num(entry.get("total_mi")) or entry["total_mi"] <= 0
                or not _is_num(entry.get("total_mi_tolerance", DEFAULT_MI_TOLERANCE))
                or not all(isinstance(entry.get(k), list) and all(isinstance(n, str) for n in entry[k])
                           for k in ("required_names", "forbidden_names"))):
            raise CaError(f"expected table entry {aid!r} is invalid")
    return doc["areas"]


def expected_problems(geom: Any, area_id: str, entry: dict[str, Any]) -> list[str]:
    trails = geom.get("trails") if isinstance(geom, dict) else None
    names = [t.get("name") for t in trails] if isinstance(trails, list) else []
    problems: list[str] = []
    if not isinstance(geom, dict) or geom.get("id") != area_id:
        problems.append(f"area id {geom.get('id') if isinstance(geom, dict) else None!r}")
        return problems
    if geom.get("trail_count") != entry["trail_count"] or len(names) != entry["trail_count"]:
        problems.append(f"trail_count {geom.get('trail_count')} / {len(names)} trails, expected {entry['trail_count']}")
    miles = geom.get("total_mi")
    tol = entry.get("total_mi_tolerance", DEFAULT_MI_TOLERANCE)
    if not _is_num(miles) or abs(miles - entry["total_mi"]) > tol:
        problems.append(f"total_mi {miles}, expected {entry['total_mi']} +/- {tol}")
    problems += [f"missing required trail {n!r}" for n in entry["required_names"] if n not in names]
    problems += [f"unqualified trail present {n!r}" for n in entry["forbidden_names"] if n in names]
    return problems


def assert_expected(args: argparse.Namespace) -> int:
    expected = load_expected(args.expected)
    if args.area_id not in expected:
        raise CaError(f"{args.area_id} is not in the blessed expected table; run qualify and bless it first")
    geom = _read_json(args.geom, "published geom")
    problems = expected_problems(geom, args.area_id, expected[args.area_id])
    if problems:
        raise CaError(f"{args.area_id} is not the blessed result: " + "; ".join(problems))
    print(f"{args.area_id}: matches blessed table ({geom['trail_count']} trails, {geom['total_mi']} mi)")
    return 0


# ------------------------------------------------------------------- select --

def _index_ids(path: Path) -> set[str]:
    rows = _read_json(path, "area index")
    return {r[0] for r in rows if isinstance(r, list) and r and isinstance(r[0], str)}






def select_areas(registry: dict[str, Any], published: set[str], areas: str, chunk_size: int,
                 mode: str, province: str) -> list[str]:
    if mode not in ("qualify", "publish"):
        raise CaError("mode must be qualify or publish")
    if province not in PROVINCES:
        raise CaError(f"province must be one of {sorted(PROVINCES)}")
    if not 1 <= chunk_size <= HARD_MAX_CHUNK:
        raise CaError(f"chunk_size must be 1..{HARD_MAX_CHUNK}")
    if areas.startswith("batch="):
        text = areas[len("batch="):]
        if not re.fullmatch(r"[1-9][0-9]{0,3}", text):
            raise CaError("batch must be a positive integer")
        n = int(text)
        # Held entries (non-null `hold`) are skipped; name them in `areas=` to select them.
        # Only the dispatched province is considered. Order: tier 1 (national parks) first,
        # then tier, then est_mi descending, then registry order.
        rank = {a["id"]: (a["tier"], -(a.get("est_mi") or 0), n_) for n_, a in enumerate(registry["areas"])}
        unpublished = sorted((a["id"] for a in registry["areas"]
                              if a["province"] == province and a["id"] not in published
                              and a.get("hold") is None), key=rank.__getitem__)
        chosen = unpublished[(n - 1) * chunk_size: n * chunk_size]
        if not chosen:
            raise CaError(f"batch={n} selects nothing ({len(unpublished)} unpublished {province} areas, "
                          f"chunk {chunk_size})")
    else:
        chosen = areas.split(",")
        if any(not SLUG.fullmatch(i) for i in chosen):
            raise CaError("areas must be a comma list of registry ids (no spaces) or batch=<n>")
        if len(set(chosen)) != len(chosen):
            raise CaError("areas repeats an id")
        unknown = [i for i in chosen if i not in registry["by_id"]]
        if unknown:
            raise CaError(f"not in the registry: {unknown}")
        if len(chosen) > chunk_size:
            raise CaError(f"{len(chosen)} areas exceeds the chunk cap {chunk_size} (hard max {HARD_MAX_CHUNK})")
        wrong = [i for i in chosen if province_of(i) != province]
        if wrong:
            raise CaError(f"one province per run: {wrong} are not {province} areas")
    if mode == "publish":
        already = [i for i in chosen if i in published]
        if already:
            raise CaError(f"already in public/areas/index.json (publish appends new areas only): {already}")
    return chosen


def select_cmd(args: argparse.Namespace) -> int:
    registry = load_registry(args.registry)
    chosen = select_areas(registry, _index_ids(args.index), args.areas, int(args.chunk_size), args.mode,
                          args.province)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(chosen) + "\n", encoding="utf-8")
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as stream:
            ids = [f"w{registry['by_id'][i]['way_id']}" if "way_id" in registry["by_id"][i]
                   else f"r{registry['by_id'][i]['relation_id']}" for i in chosen]
            relations = " ".join(dict.fromkeys(ids))
            stream.write(f"area_ids={','.join(chosen)}\narea_count={len(chosen)}\n"
                         f"getid_args={relations}\n")
    print(f"selected {len(chosen)} area(s): {','.join(chosen)}")
    return 0


# ------------------------------------------------------ boundary bbox / prep --

def boundary_info(pbf: Path, relation_ids: list[int]) -> dict[int, dict[str, Any]]:
    """Per boundary relation: bbox of every way reachable from it (nested relations
    followed), its tags, and how many times the relation id occurs in the file.
    Reads a small `osmium getid -r` extract."""
    import osmium
    way_bbox: dict[int, list[float]] = {}
    members: dict[int, list[tuple[str, int]]] = {}
    tags: dict[int, dict[str, str]] = {}
    seen_count: dict[int, int] = {}

    class _Collector(osmium.SimpleHandler):  # works on pyosmium 3.7.x and 4.x
        def way(self, w):
            lons, lats = [], []
            for node in w.nodes:
                try:
                    lons.append(node.lon)
                    lats.append(node.lat)
                except Exception:  # missing location: skip the node
                    continue
            if lons:
                way_bbox[w.id] = [min(lons), min(lats), max(lons), max(lats)]

        def relation(self, r):
            members[r.id] = [(m.type, m.ref) for m in r.members]
            tags[r.id] = {t.k: t.v for t in r.tags}
            seen_count[r.id] = seen_count.get(r.id, 0) + 1

    _Collector().apply_file(str(pbf), locations=True)

    def collect(rel: int, seen: set[int]) -> list[list[float]]:
        if rel in seen or rel not in members:
            return []
        seen.add(rel)
        boxes: list[list[float]] = []
        for kind, ref in members[rel]:
            if kind == "w" and ref in way_bbox:
                boxes.append(way_bbox[ref])
            elif kind == "r":
                boxes += collect(ref, seen)
        return boxes

    result: dict[int, dict[str, Any]] = {}
    for rel in relation_ids:
        boxes = collect(rel, set())
        result[rel] = {
            "bbox": ([min(b[0] for b in boxes), min(b[1] for b in boxes),
                      max(b[2] for b in boxes), max(b[3] for b in boxes)] if boxes else None),
            "tags": tags.get(rel), "count": seen_count.get(rel, 0)}
    return result


def way_boundary_info(pbf: Path, way_ids: list[int]) -> dict[int, dict[str, Any]]:
    """Per way-backed boundary: bbox of the way, its tags, whether it is a closed
    ring, and how many times the way id occurs in the file (small `osmium getid -r`
    extract). Counterpart of boundary_info for registry `way_id` entries."""
    import osmium
    wanted = set(way_ids)
    found: dict[int, dict[str, Any]] = {}
    seen_count: dict[int, int] = {}
    class _Collector(osmium.SimpleHandler):  # works on pyosmium 3.7.x and 4.x
        def way(self, w):
            if w.id not in wanted:
                return
            seen_count[w.id] = seen_count.get(w.id, 0) + 1
            lons, lats, refs = [], [], []
            for node in w.nodes:
                refs.append(node.ref)
                try:
                    lons.append(node.lon)
                    lats.append(node.lat)
                except Exception:  # missing location: skip the node
                    continue
            found[w.id] = {
                "bbox": ([min(lons), min(lats), max(lons), max(lats)] if lons else None),
                "tags": {t.k: t.v for t in w.tags},
                "closed": len(refs) >= 4 and refs[0] == refs[-1]}
    _Collector().apply_file(str(pbf), locations=True)
    return {way: {**found.get(way, {"bbox": None, "tags": None, "closed": False}),
                  "count": seen_count.get(way, 0)} for way in way_ids}
def boundary_bboxes(pbf: Path, relation_ids: list[int]) -> dict[int, list[float] | None]:
    return {rel: info["bbox"] for rel, info in boundary_info(pbf, relation_ids).items()}


# The registry entry is cross-checked against its own OSM relation/way (identity, type, name,
# size). There is no discovery step in the Canada lane.
REL_TYPES = {"boundary", "multipolygon"}
# The generic engine keys boundaries on the OSM `name` tag only, so the registry name must equal it.
REL_NAME_KEYS = ("name",)
REL_SPAN_MIN, REL_SPAN_MAX = 0.01, 3.0     # degrees, per axis (Canadian parks can be very large)


def _size_problems(box: list[float] | None) -> list[str]:
    problems: list[str] = []
    if box is None:
        return problems
    w, h = box[2] - box[0], box[3] - box[1]
    if not (REL_SPAN_MIN <= w <= REL_SPAN_MAX and REL_SPAN_MIN <= h <= REL_SPAN_MAX):
        problems.append(f"boundary size {w:.3f} x {h:.3f} degrees is implausible "
                        f"(each side must be {REL_SPAN_MIN}..{REL_SPAN_MAX})")
    if not (CA_LON[0] <= box[0] and box[2] <= CA_LON[1] and CA_LAT[0] <= box[1] and box[3] <= CA_LAT[1]):
        problems.append(f"boundary bbox {box} is outside Canada")
    return problems


def _names(tags: dict[str, str]) -> set[str]:
    return {unicodedata.normalize("NFC", tags[k]) for k in REL_NAME_KEYS if tags.get(k)}


def relation_problems(entry: dict[str, Any], info: dict[str, Any] | None) -> list[str]:
    """Fail-closed identity/size check of a registry entry against its own PBF relation."""
    if not info or info.get("tags") is None:
        return [f"registry relation {entry['relation_id']} is not in the PBF"]
    problems: list[str] = []
    if info.get("count") != 1:
        problems.append(f"registry relation {entry['relation_id']} occurs {info.get('count')} times (not unique)")
    tags = info["tags"]
    if tags.get("type") not in REL_TYPES:
        problems.append(f"relation {entry['relation_id']} has type={tags.get('type')!r}, expected boundary/multipolygon")
    names = _names(tags)
    if entry["name"] not in names:
        problems.append(f"relation {entry['relation_id']} name {sorted(names)} does not match registry name {entry['name']!r}")
    return problems + _size_problems(info.get("bbox"))


def way_problems(entry: dict[str, Any], info: dict[str, Any] | None) -> list[str]:
    """Fail-closed identity/size check of a way-backed registry entry against its own
    PBF way: present once, a closed ring, named like the registry, plausible size."""
    if not info or info.get("tags") is None:
        return [f"registry way {entry['way_id']} is not in the PBF"]
    problems: list[str] = []
    if info.get("count") != 1:
        problems.append(f"registry way {entry['way_id']} occurs {info.get('count')} times (not unique)")
    if not info.get("closed"):
        problems.append(f"way {entry['way_id']} is not a closed ring")
    names = _names(info["tags"])
    if entry["name"] not in names:
        problems.append(f"way {entry['way_id']} name {sorted(names)} does not match registry name {entry['name']!r}")
    return problems + _size_problems(info.get("bbox"))


def union_bbox(boxes: list[list[float]]) -> list[float]:
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def pad_bbox(box: list[float]) -> list[float]:
    return [round(box[0] - PAD, 2), round(box[1] - PAD, 2), round(box[2] + PAD, 2), round(box[3] + PAD, 2)]


def center_of(box: list[float]) -> list[float]:
    return [round((box[1] + box[3]) / 2, 1), round((box[0] + box[2]) / 2, 1)]


def check_area_geometry(entry: dict[str, Any], boundary: list[float] | None, mode: str,
                        info: dict[str, Any] | None = None) -> dict[str, Any]:
    """Derive/check one area's bbox + centre. Returns the per-area prepare record."""
    problems: list[str] = []
    way_backed = "way_id" in entry
    rec: dict[str, Any] = {"id": entry["id"], "name": entry["name"], "province": entry["province"],
                           **({"way_id": entry["way_id"]} if way_backed
                              else {"relation_id": entry["relation_id"]}),
                           "boundary_bbox": boundary, "registry_bbox": entry.get("bbox"),
                           "registry_center": entry.get("center")}
    if boundary is None:
        problems.append("boundary way not found in the PBF (no node geometry)" if way_backed
                        else "boundary relation not found in the PBF (no member way geometry)")
        rec.update(status="failed", problems=problems, bbox_source=None, effective_bbox=None, center=None)
        return rec
    reg = entry.get("bbox")
    padded, centre = pad_bbox(boundary), center_of(boundary)
    rec["proposed_bbox"], rec["proposed_center"] = padded, centre
    if way_backed:
        rec["cross_check"] = "registry-way"
        problems += way_problems(entry, info)
    else:
        rec["cross_check"] = "registry-relation"
        problems += relation_problems(entry, info)
    if reg is None:
        if mode == "publish":
            problems.append("registry bbox/center missing: run qualify and commit the proposed values first")
        rec.update(bbox_source="computed", effective_bbox=padded, center=centre)
    else:
        margins = [boundary[0] - reg[0], boundary[1] - reg[1], reg[2] - boundary[2], reg[3] - boundary[3]]
        rec["registry_margins"] = [round(m, 4) for m in margins]
        if any(m < MARGIN_MIN - 1e-9 or m > MARGIN_MAX + 1e-9 for m in margins):
            problems.append(f"registry bbox margins {rec['registry_margins']} outside "
                            f"[{MARGIN_MIN}, {MARGIN_MAX}] of the boundary bbox {boundary}")
        # The pin may sit on the trails instead of the rounded boundary midpoint, but it must
        # stay inside the boundary bbox (the trail-centroid check runs at merge/verify time).
        c = entry["center"]
        if not (boundary[1] <= c[0] <= boundary[3] and boundary[0] <= c[1] <= boundary[2]):
            problems.append(f"registry center {entry['center']} is outside the boundary bbox {boundary}")
        rec.update(bbox_source="registry", effective_bbox=reg, center=entry["center"])
    rec["problems"] = problems
    rec["status"] = "failed" if problems else "ok"
    return rec


def prepare_areas(args: argparse.Namespace) -> int:
    registry = load_registry(args.registry)
    ids = [i for i in args.selected.read_text(encoding="utf-8").split() if i]
    entries = [registry["by_id"][i] for i in ids]
    rel_entries = [e for e in entries if "way_id" not in e]
    way_entries = [e for e in entries if "way_id" in e]
    infos = boundary_info(args.bounds_pbf, [e["relation_id"] for e in rel_entries])
    way_infos = (way_boundary_info(args.bounds_pbf, [e["way_id"] for e in way_entries])
                 if way_entries else {})
    out: list[dict[str, Any]] = []
    for entry in entries:
        info = way_infos[entry["way_id"]] if "way_id" in entry else infos[entry["relation_id"]]
        rec = check_area_geometry(entry, info["bbox"], args.mode, info)
        if rec["status"] == "ok":
            rec["bbox_arg"] = bbox_arg(rec["effective_bbox"])
            rec["display_name"] = entry.get("display_name") or entry["name"]
            row = [entry["id"], rec["display_name"], STATE, rec["center"][0], rec["center"][1], None, None]
            if "way_id" not in entry:
                # Way-backed rows stay 7 long (the field is ABSENT, not null): see check_new_row.
                row.append(entry["relation_id"])
            _write_json(args.out_dir / entry["id"] / "index.json", [row])
        _write_json(args.out_dir / entry["id"] / "area.json", rec)
        out.append(rec)
        print(f"{entry['id']}: {rec['status']} bbox={rec.get('bbox_arg')} source={rec.get('bbox_source')} "
              f"{'; '.join(rec['problems'])}")
    _write_json(args.out_dir / "prepare.json", {"areas": out})
    failed = [r["id"] for r in out if r["status"] != "ok"]
    if failed and args.mode == "publish":
        raise CaError(f"area geometry check failed for {failed}")
    return 0


def index_for_publish_cmd(args: argparse.Namespace) -> int:
    """The generic publisher finds an area's boundary by the index row NAME, which must be the OSM
    name. Copy the one-row index with the OSM name; finish-publish restores the display name."""
    rows = _read_json(args.index, "area index row")
    area = _read_json(args.area_json, "area.json")
    if not isinstance(rows, list) or len(rows) != 1 or rows[0][0] != area["id"]:
        raise CaError("index must hold exactly this area's row")
    row = list(rows[0])
    row[1] = area["name"]
    _write_json(args.out, [row])
    return 0


def finish_publish_cmd(args: argparse.Namespace) -> int:
    """Take the counts the publisher wrote into the OSM-name index, keep the registry display
    name in the row and in the geom, and fail closed on any identity drift."""
    rows = _read_json(args.index, "area index row")
    published = _read_json(args.publish_index, "published index row")
    area = _read_json(args.area_json, "area.json")
    if (len(rows) != 1 or len(published) != 1 or rows[0][0] != published[0][0] != area["id"]
            or published[0][2:5] != rows[0][2:5] or published[0][1] != area["name"]):
        raise CaError("published index row differs from the prepared row")
    row = list(rows[0])
    row[5], row[6] = published[0][5], published[0][6]
    geom = _read_json(args.geom, "geom")
    shown = area.get("display_name") or area["name"]
    if geom.get("id") != area["id"] or geom.get("name") != area["name"]:
        raise CaError("published geom identity differs from the registry")
    if shown != geom["name"]:
        geom["name"] = shown
        # Same serialization as the publisher's atomic_write_json (default separators, ASCII).
        args.geom.write_bytes(json.dumps(geom, ensure_ascii=True, allow_nan=False).encode("utf-8"))
    _write_json(args.index, [row])
    print(f"{area['id']}: {row[5]} trails, {row[6]} mi, shown as {shown!r}")
    return 0


# The generic engine unions every boundary that carries the same name inside the AOI, and
# --require-exact-area only pins relation ids. So the AOI extract must hold exactly one
# boundary of the registry name (itself), or the area fails closed (hold: name-collision).
def boundary_name_matches(pbf: Path, name: str, contains: bool = False) -> list[tuple[str, int]]:
    """Relations (type boundary/multipolygon) and closed non-trail ways whose `name` equals
    `name` (or, with contains=True, contains it case-insensitively: assemble's --only-area is a
    contains match) in the AOI extract. Ways that are members of any relation are rings."""
    import osmium
    wanted = unicodedata.normalize("NFC", name)

    def hit(tags: dict[str, str]) -> bool:
        have = tags.get("name")
        if not have:
            return False
        have = unicodedata.normalize("NFC", have)
        return wanted.casefold() in have.casefold() if contains else have == wanted

    rel_hits: list[int] = []
    way_hits: dict[int, bool] = {}
    member_ways: set[int] = set()

    class _Scan(osmium.SimpleHandler):  # works on pyosmium 3.7.x and 4.x
        def way(self, w):
            tags = {t.k: t.v for t in w.tags}
            if not hit(tags) or "highway" in tags or "route" in tags:
                return
            refs = [n.ref for n in w.nodes]
            way_hits[w.id] = len(refs) >= 4 and refs[0] == refs[-1]

        def relation(self, r):
            for m in r.members:
                if m.type == "w":
                    member_ways.add(m.ref)
            tags = {t.k: t.v for t in r.tags}
            if tags.get("type") in REL_TYPES and hit(tags):
                rel_hits.append(r.id)

    _Scan().apply_file(str(pbf))
    return ([("relation", r) for r in sorted(set(rel_hits))]
            + [("way", w) for w, closed in sorted(way_hits.items()) if closed and w not in member_ways])


def pbf_url_cmd(args: argparse.Namespace) -> int:
    print(pbf_url(args.province))
    return 0


def check_boundary_unique_cmd(args: argparse.Namespace) -> int:
    registry = load_registry(args.registry)
    entry = registry["by_id"][args.area_id]
    kind, ident = (("way", entry["way_id"]) if "way_id" in entry else ("relation", entry["relation_id"]))
    # Way-backed areas have no --require-exact-area pin, so no other boundary may even contain the name.
    hits = boundary_name_matches(args.pbf, entry["name"], contains="way_id" in entry)
    if hits != [(kind, ident)]:
        raise CaError(f"{args.area_id}: expected exactly one boundary named {entry['name']!r} "
                      f"({kind} {ident}) in the AOI, found {hits}; hold it as name-collision")
    print(f"{args.area_id}: unique boundary {kind} {ident}")
    return 0


# ------------------------------------------------------------- qualify report --

def _features(path: Path) -> list[dict[str, Any]]:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    return [f.get("properties") or {} for f in doc.get("features") or []]


# ----------------------------------------------- cross-area duplicate gate --
# A route (relation id, or identical geometry, or the same name over the same ground)
# must not be listed in two areas: the selected set, or any nearby live area (Canadian,
# US border; only areas whose index centre is within LIVE_NEAR_DEG are read).
DUP_NODE_SAMPLE = 4          # keep nodes whose hash % 4 == 0 (same rule on both sides)
DUP_NAME_OVERLAP = 0.5       # same normalized name + this share of the smaller sample
DUP_MIN_SAMPLE = 5
LIVE_NEAR_DEG = 3.0
LIVE_GEOM_DIR = ROOT / "public" / "areas" / "geom"


def _norm_name(name: Any) -> str:
    return " ".join(unicodedata.normalize("NFC", str(name or "")).casefold().split())


def _segments(trail: dict[str, Any]) -> list[list[tuple[float, float]]]:
    out = []
    for seg in trail.get("segments") or []:
        pts = [(round(float(p[0]), 5), round(float(p[1]), 5)) for p in seg]
        if pts:
            out.append(min(pts, list(reversed(pts))))
    return sorted(out)


def trail_identity(trail: dict[str, Any]) -> dict[str, Any]:
    segs = _segments(trail)
    digest = hashlib.sha256(json.dumps(segs, separators=(",", ":")).encode("utf-8")).hexdigest()
    nodes = {hashlib.sha1(f"{a:.5f},{b:.5f}".encode("ascii")).hexdigest()[:8]
             for seg in segs for a, b in seg}
    sample = sorted(n for n in nodes if int(n, 16) % DUP_NODE_SAMPLE == 0)
    return {"name": trail.get("name"), "geom_sha256": digest, "nodes": sample}


def _overlap(a: list[str], b: list[str]) -> float:
    sa, sb = set(a), set(b)
    if min(len(sa), len(sb)) < DUP_MIN_SAMPLE:
        return 0.0
    return len(sa & sb) / min(len(sa), len(sb))


def _same_route(a: dict[str, Any], b: dict[str, Any]) -> str | None:
    if a["geom_sha256"] == b["geom_sha256"]:
        return "identical geometry"
    if _norm_name(a["name"]) == _norm_name(b["name"]) and _overlap(a["nodes"], b["nodes"]) >= DUP_NAME_OVERLAP:
        return "same name over the same ground"
    return None


def nearby_live_ids(index_path: Path, area_id: str, center: Any, radius: float = LIVE_NEAR_DEG) -> list[str]:
    """Live area ids to compare against: every Canadian row, plus any row whose index centre is
    within `radius` degrees of this area's centre (US border states)."""
    rows = _read_json(index_path, "live area index")
    near = []
    for r in rows:
        if not isinstance(r, list) or len(r) < 5 or r[0] == area_id:
            continue
        in_box = (isinstance(center, list) and len(center) == 2 and _is_num(r[3]) and _is_num(r[4])
                  and abs(r[3] - center[0]) <= radius and abs(r[4] - center[1]) <= radius)
        if in_box or SLUG.fullmatch(str(r[0])):
            near.append(r[0])
    return sorted(set(near))


def live_conflicts(area_id: str, identities: list[dict[str, Any]], live_dir: Path,
                   live_ids: list[str]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for lid in live_ids:
        path = live_dir / f"{lid}.json"
        if lid == area_id or not path.is_file():
            continue
        live = _read_json(path, "live geom")
        if not isinstance(live, dict):
            continue
        for lt in live.get("trails") or []:
            li = trail_identity(lt)
            for mine in identities:
                why = _same_route(mine, li)
                if why:
                    found.append({"area": area_id, "trail": mine["name"], "other_area": live.get("id"),
                                  "other_trail": li["name"], "other_is_live": True, "reason": why})
    return found


def selected_conflicts(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for i, ra in enumerate(reports):
        for rb in reports[i + 1:]:
            for ta in ra.get("trail_identities") or []:
                for tb in rb.get("trail_identities") or []:
                    shared = sorted(set(ta.get("relation_ids") or []) & set(tb.get("relation_ids") or []))
                    if shared:
                        why = "relation " + ", ".join(f"r{r}" for r in shared) + " in both"
                    else:
                        why = _same_route(ta, tb)
                    if why:
                        found.append({"area": ra["id"], "trail": ta["name"], "other_area": rb["id"],
                                      "other_trail": tb["name"], "other_is_live": False, "reason": why})
    return found


def duplicate_conflicts(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    found = selected_conflicts(reports)
    for r in reports:
        found += r.get("live_duplicates") or []
    return found


def duplicate_message(c: dict[str, Any]) -> str:
    where = "live area" if c.get("other_is_live") else "area"
    return (f"{c['area']} '{c['trail']}' duplicates {where} {c['other_area']} '{c['other_trail']}' "
            f"({c['reason']})")


def _trail_relations(features: list[dict[str, Any]], name: Any) -> list[int]:
    rels: set[int] = set()
    for p in features:
        if p.get("name") == name and p.get("kind") != "poi":
            for key in ("relation_ids", "root_relation_ids"):
                rels.update(int(r) for r in (p.get(key) or []) if isinstance(r, int))
    return sorted(rels)


# Removed categories that mean "a rule said this is not a Canadian hiking trail"; their
# names go into the blessed table's forbidden_names so a regression fails loudly.
JUNK_CATEGORIES = {
    "disconnected-name-stitch", "unresolved-relation-gap", "thru-hike", *ca_trail_filter.CATEGORIES,
}


def report_area(args: argparse.Namespace) -> int:
    area = _read_json(args.area_json, "area.json")
    geom = _read_json(args.geom, "geom")
    kept = _features(args.trails_geojson)
    removed = _features(args.removed_geojson)
    assembly = _read_json(args.assembly_json, "assembly report")
    names = [t.get("name") for t in geom.get("trails") or []]
    categories: dict[str, int] = {}
    for props in removed:
        cat = props.get("removed_category") or "removed"
        categories[cat] = categories.get(cat, 0) + 1
    routes = [{"name": p.get("name"), "length_mi": p.get("length_mi")}
              for p in kept if p.get("kind") == "route"]
    forbidden = sorted({p.get("name") for p in removed
                        if p.get("removed_category") in JUNK_CATEGORIES and p.get("name")} - set(names))
    curated = sorted(
        ({"id": p.get("curated_exclude_id"), "name": p.get("name"), "length_mi": p.get("length_mi"),
          "reason": p.get("curated_exclude_reason")}
         for p in removed if p.get("removed_category") == "ca-curated-exclude"),
        key=lambda item: str(item["id"]))
    rec = {
        "id": area["id"], "name": area["name"], "province": area.get("province"),
        **({"way_id": area["way_id"]} if "way_id" in area else {"relation_id": area["relation_id"]}),
        "status": "ok",
        "trail_count": geom.get("trail_count"), "total_mi": geom.get("total_mi"),
        "pre_clip_trail_count": assembly.get("pre_clip_trail_count"),
        "post_clip_trail_count": assembly.get("post_clip_trail_count"),
        "removed_category_counts": dict(sorted(categories.items())),
        "trail_names": names,
        "route_segments": routes,
        "curated_excluded": curated,
        "boundary_bbox": area.get("boundary_bbox"), "effective_bbox": area.get("effective_bbox"),
        "bbox_source": area.get("bbox_source"), "center": area.get("center"),
        "proposed_bbox": area.get("proposed_bbox"), "proposed_center": area.get("proposed_center"),
        "proposed_expected": {
            "trail_count": geom.get("trail_count"), "total_mi": geom.get("total_mi"),
            "total_mi_tolerance": DEFAULT_MI_TOLERANCE,
            "required_names": sorted({r["name"] for r in routes if r["name"] in names}),
            "forbidden_names": forbidden[:50],
        },
    }
    identities = []
    for t in geom.get("trails") or []:
        ident = trail_identity(t)
        ident["relation_ids"] = _trail_relations(kept, t.get("name"))
        identities.append(ident)
    rec["trail_identities"] = identities
    live_ids = nearby_live_ids(args.live_index, area["id"], area.get("center"))
    rec["live_duplicates"] = live_conflicts(area["id"], identities, args.live_geom_dir, live_ids)
    if args.expected and Path(args.expected).is_file():
        table = load_expected(args.expected)
        if area["id"] in table:
            rec["blessed_match"] = expected_problems(geom, area["id"], table[area["id"]]) or "match"
    _write_json(args.out, rec)
    print(f"{area['id']}: {rec['trail_count']} trails, {rec['total_mi']} mi, removed {rec['removed_category_counts']}")
    return 0


def mark_failed(args: argparse.Namespace) -> int:
    area = _read_json(args.area_json, "area.json") if args.area_json and Path(args.area_json).is_file() else {}
    _write_json(args.out, {"id": args.area_id, "name": area.get("name"), "status": "failed",
                           "stage": args.stage, "problems": area.get("problems") or [f"failed at {args.stage}"]})
    return 0


def qualify_aggregate(args: argparse.Namespace) -> int:
    registry = load_registry(args.registry)
    reports = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(args.reports_dir.glob("*/report.json"))]
    ok = [r for r in reports if r["status"] == "ok"]
    failed = [r for r in reports if r["status"] != "ok"]
    proposed_expected = {"schema": EXPECTED_SCHEMA, "areas": {r["id"]: r["proposed_expected"] for r in ok}}
    proposed_registry = json.loads(json.dumps({k: v for k, v in registry.items() if k != "by_id"}))
    for entry in proposed_registry["areas"]:
        rec = next((r for r in ok if r["id"] == entry["id"]), None)
        if rec and entry["bbox"] is None and rec.get("proposed_bbox"):
            entry["bbox"], entry["center"] = rec["proposed_bbox"], rec["proposed_center"]
    conflicts = duplicate_conflicts(ok)
    _write_json(args.out_dir / "duplicate-conflicts.json", {"conflicts": conflicts})
    _write_json(args.out_dir / "qualify-report.json", {"areas": reports})
    _write_json(args.out_dir / "ca-areas-expected.proposed.json", proposed_expected)
    _write_json(args.out_dir / "ca-areas.proposed.json", proposed_registry)
    lines = ["# Canada qualify report", "",
             "| area | status | trails | mi | pre>post clip | bbox source | blessed |", "|---|---|---|---|---|---|---|"]
    for r in reports:
        lines.append("| {id} | {status} | {t} | {m} | {pre}>{post} | {src} | {b} |".format(
            id=r["id"], status=r["status"], t=r.get("trail_count", "-"), m=r.get("total_mi", "-"),
            pre=r.get("pre_clip_trail_count", "-"), post=r.get("post_clip_trail_count", "-"),
            src=r.get("bbox_source", "-"), b=r.get("blessed_match", "-")))
    for r in ok:
        lines += ["", f"## {r['id']}",
                  f"removed: `{json.dumps(r['removed_category_counts'], ensure_ascii=False)}`",
                  f"route segments: {[(x['name'], x['length_mi']) for x in r['route_segments']]}",
                  f"curated excluded: {[(x['id'], x['name'], x['length_mi'], x['reason']) for x in r.get('curated_excluded', [])]}",
                  f"bbox: boundary {r['boundary_bbox']} proposed {r['proposed_bbox']} centre {r['proposed_center']}",
                  "", "trails: " + "; ".join(str(n) for n in r["trail_names"])]
    lines += ["", "## Cross-area duplicates", ""]
    lines += [f"- {duplicate_message(c)}" for c in conflicts] or ["none"]
    for r in failed:
        lines += ["", f"## {r['id']} FAILED at {r.get('stage', 'prepare')}", *[f"- {p}" for p in r.get("problems", [])]]
    (args.out_dir / "qualify-report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:6 + len(reports)]))
    for c in conflicts:
        print(f"REFUSED: {duplicate_message(c)}", file=sys.stderr)
    return 1 if failed or conflicts else 0


# ------------------------------------------------------------- index handling --

def _dumps_default(rows: list[Any]) -> bytes:
    return json.dumps(rows, ensure_ascii=True, allow_nan=False).encode("utf-8")


def _dumps_compact(rows: list[Any]) -> bytes:
    return json.dumps(rows, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("utf-8")


def _rows(payload: bytes, context: str, compact: bool) -> list[list[Any]]:
    try:
        rows = contract.strict_json_loads(payload.decode("utf-8"), context)
    except (UnicodeDecodeError, contract.InventoryError) as error:
        raise CaError(f"{context} is invalid: {error}") from error
    if not isinstance(rows, list) or (_dumps_compact if compact else _dumps_default)(rows) != payload:
        raise CaError(f"{context} byte framing differs from its producer")
    seen: set[str] = set()
    for number, row in enumerate(rows, start=1):
        if not isinstance(row, list) or len(row) < 5 or not isinstance(row[0], str) or row[0] in seen:
            raise CaError(f"{context} row {number} is invalid")
        seen.add(row[0])
    return rows


def check_new_row(row: list[Any], entry: dict[str, Any], geom: dict[str, Any], context: str) -> None:
    if "way_id" in entry:
        # Way-backed: no relation id exists, so the row has no 8th element at all.
        id_ok = len(row) == 7
    else:
        id_ok = len(row) == 8 and row[7] == entry["relation_id"]
    if (not id_ok or row[0] != entry["id"] or row[1] != (entry.get("display_name") or entry["name"])
            or row[2] != STATE):
        raise CaError(f"{context}: {entry['id']} row has the wrong identity")
    if entry.get("center") is None or [row[3], row[4]] != entry["center"]:
        raise CaError(f"{context}: {entry['id']} row centre differs from the registry")
    pts = [pt for t in geom.get("trails") or [] for seg in t.get("segments") or [] for pt in seg]
    if pts:
        mid = ((min(p[0] for p in pts) + max(p[0] for p in pts)) / 2,
               (min(p[1] for p in pts) + max(p[1] for p in pts)) / 2)
        if abs(mid[0] - entry["center"][0]) > 0.1 or abs(mid[1] - entry["center"][1]) > 0.1:
            raise CaError(f"{context}: {entry['id']} centre is more than 0.1 deg from the trails")
    if (row[5] != geom.get("trail_count") or row[5] != len(geom.get("trails") or [])
            or not _is_num(row[6]) or row[6] != geom.get("total_mi") or row[6] <= 0 or row[5] <= 0):
        raise CaError(f"{context}: {entry['id']} row counts differ from its geom")


def merge_index(args: argparse.Namespace) -> int:
    registry = load_registry(args.registry)
    target = args.target
    if target.is_symlink() or not target.is_file():
        raise CaError("index target is missing or unsafe")
    before = target.read_bytes()
    rows = _rows(before, "canonical master index", compact=False)
    ids = [i for i in args.selected.read_text(encoding="utf-8").split() if i]
    new_rows = []
    for aid in sorted(ids):
        src = _read_json(args.rows_dir / aid / "index.json", f"{aid} published row")
        if not isinstance(src, list) or len(src) != 1:
            raise CaError(f"{aid}: published index is not exactly one row")
        row = src[0]
        geom = _read_json(args.geom_dir / f"{aid}.json", f"{aid} geom")
        check_new_row(row, registry["by_id"][aid], geom, "published row")
        if any(r[0] == aid for r in rows):
            raise CaError(f"{aid} is already in the canonical master index")
        new_rows.append(row)
    target.write_bytes(_dumps_default([*rows, *new_rows]))
    after = target.read_bytes()
    if not after.startswith(before[:-1] + b", "):
        raise CaError("merge would change existing index bytes")
    print(f"appended {len(new_rows)} row(s) to {target}")
    return 0


def _git(repo: Path, *argv: str) -> bytes:
    try:
        done = subprocess.run(["git", *argv], cwd=repo, capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as error:
        raise CaError(f"git probe failed: {type(error).__name__}") from error
    if done.returncode != 0:
        raise CaError("git probe returned nonzero: " + " ".join(argv[:3]))
    return done.stdout


def area_paths(ids: list[str]) -> list[str]:
    paths = [INDEX_REPO, BUNDLE_REPO]
    for aid in ids:
        paths += [f"public/areas/geom/{aid}.json", f"public/areas/silhouettes/{aid}.json"]
    return sorted(paths)


def _verify_delta(repo: Path, ids: list[str], *, staged: bool) -> dict[str, Any]:
    registry = load_registry(repo / "trailforge/golden/ca-areas.json")
    names_cmd = ["diff", "--cached"] if staged else ["diff"]
    span = [] if staged else ["HEAD^", "HEAD"]
    changed = [n for n in _git(repo, *names_cmd, "--name-only", "--diff-filter=ACMR", "-z", *span)
               .decode("utf-8").split("\0") if n]
    if _git(repo, *names_cmd, "--name-only", "--diff-filter=D", "-z", *span):
        raise CaError("diff contains a deletion")
    want = area_paths(ids)
    if len(want) != 2 * len(ids) + 2 or changed != want:
        raise CaError(f"diff is not the exact 2N+2 allowlist: {changed}")

    def show(path: str, new: bool) -> bytes:
        return _git(repo, "show", (f":{path}" if staged else f"HEAD:{path}") if new else
                    (f"HEAD:{path}" if staged else f"HEAD^:{path}"))

    geoms = {}
    for aid in ids:
        geom = contract.strict_json_loads(show(f"public/areas/geom/{aid}.json", True).decode("utf-8"), f"{aid} geom")
        if not isinstance(geom, dict) or geom.get("id") != aid or not geom.get("trails"):
            raise CaError(f"{aid}: geom identity/trails are invalid")
        sil = contract.strict_json_loads(show(f"public/areas/silhouettes/{aid}.json", True).decode("utf-8"),
                                         f"{aid} silhouette")
        if (not isinstance(sil, dict) or set(sil) != {"b", "l"} or not isinstance(sil["b"], list)
                or len(sil["b"]) != 4 or not isinstance(sil["l"], list) or not sil["l"]):
            raise CaError(f"{aid}: silhouette is malformed")
        geoms[aid] = geom
    transitions = []
    for path, compact in ((INDEX_REPO, False), (BUNDLE_REPO, True)):
        before, after = show(path, False), show(path, True)
        brows, arows = _rows(before, f"baseline {path}", compact), _rows(after, f"candidate {path}", compact)
        added = arows[len(brows):]
        if arows[:len(brows)] != brows or [r[0] for r in added] != sorted(ids):
            raise CaError(f"{path}: rows are not baseline + the new areas appended in sorted id order")
        dump = _dumps_compact if compact else _dumps_default
        sep = b"," if compact else b", "
        if after != before[:-1] + sep + sep.join(dump([r])[1:-1] for r in added) + b"]":
            raise CaError(f"{path}: existing bytes changed")
        for row in added:
            check_new_row(row, registry["by_id"][row[0]], geoms[row[0]], path)
        transitions.append({"path": path, "baseline_sha256": _sha(before), "candidate_sha256": _sha(after),
                            "appended_ids": [r[0] for r in added]})
    return {"schema": "trekdex-ca-delta-v1", "area_ids": ids, "allowed_paths": want, "index_transitions": transitions}


def verify_cmd(args: argparse.Namespace) -> int:
    ids = [i for i in args.areas.split(",") if i]
    if not ids or len(ids) > HARD_MAX_CHUNK or len(set(ids)) != len(ids):
        raise CaError("areas must be 1..40 unique ids")
    staged = args.command == "verify-staged"
    summary = _verify_delta(args.repo.resolve(), ids, staged=staged)
    summary["receipt_sha256"] = contract.canonical_sha256(summary)
    if args.output:
        _write_json(args.output, summary)
    print(f"verified publish delta for {len(ids)} area(s) ({len(summary.get('allowed_paths', []))} allowed paths) "
          f"sha256={summary['receipt_sha256']}")
    return 0


# ----------------------------------------------------------- R2 candidates ---

def _area_geom_trails(payload: bytes, area_id: str) -> list[dict[str, Any]]:
    doc = contract.strict_json_loads(payload.decode("utf-8"), f"{area_id} geom")
    trails = doc.get("trails") if isinstance(doc, dict) else None
    if doc.get("id") != area_id or not isinstance(trails, list) or not trails:
        raise CaError(f"{area_id}: geom identity/trails are invalid")
    seen: set[str] = set()
    for number, trail in enumerate(trails, start=1):
        if (not isinstance(trail, dict) or not contract._is_plain_safe_text(trail.get("id"), maximum=512)
                or trail["id"] in seen):
            raise CaError(f"{area_id}: trail {number} identity is invalid or duplicate")
        seen.add(trail["id"])
    return trails


def expected_records(key: str, area_id: str, geom: bytes) -> list[list[Any]]:
    records: list[list[Any]] = []
    for trail in _area_geom_trails(geom, area_id):
        if key == "trail-search.json":
            if not contract._is_safe_display_text(trail.get("name"), maximum=1024):
                raise CaError(f"{area_id}: a trail has no qualified name")
            records.append([trail["name"], area_id, trail["id"], trail.get("distanceMi"), trail.get("difficulty")])
        else:
            segments = trail.get("segments") or []
            if not isinstance(segments, list) or not segments or any(not isinstance(s, list) for s in segments):
                raise CaError(f"{area_id}: trail {trail['id']} has no qualified segment")
            shape = contract._mols_shape(max(segments, key=len))
            if shape is None:
                raise CaError(f"{area_id}: trail {trail['id']} has no drawable segment")
            records.append([f"{area_id}/{trail['id']}", shape])
    if key == "trail-search.json":
        contract._strict_trail_search_records(
            json.dumps(records, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("utf-8"),
            f"{area_id} expected search records")
    else:
        contract._strict_trail_shape_records(
            json.dumps({r[0]: r[1] for r in records}, separators=(",", ":")).encode("utf-8"),
            f"{area_id} expected shape records")
    return records


def prove_global_delta(key: str, before: bytes, after: bytes, geoms: dict[str, bytes]) -> dict[str, Any]:
    """Only the exact deterministic records for the N areas are added, after every
    live record, in sorted area order; every other record is identical."""
    ids = sorted(geoms)
    if key == "trail-search.json":
        base = contract._strict_trail_search_records(before, "baseline trail-search")
        cand = contract._strict_trail_search_records(after, "candidate trail-search")
        belongs = lambda row: row[1] in geoms  # noqa: E731
    else:
        base = contract._strict_trail_shape_records(before, "baseline trail-shapes")
        cand = contract._strict_trail_shape_records(after, "candidate trail-shapes")
        prefixes = tuple(f"{i}/" for i in ids)
        belongs = lambda row: row[0].startswith(prefixes)  # noqa: E731
    if any(belongs(r) for r in base):
        raise CaError(f"live {key} already has records for one of the new areas; refusing to overwrite")
    want = [rec for aid in ids for rec in expected_records(key, aid, geoms[aid])]
    if cand[:len(base)] != base or cand[len(base):] != want:
        raise CaError(f"candidate {key} is not live records + the exact deterministic additions")
    return {"key": key, "baseline_records": len(base), "added_records": len(want),
            "baseline_sha256": _sha(before), "candidate_sha256": _sha(after)}


def derive_new_ids(repo: Path, live_index: bytes) -> list[str]:
    """The new areas = repo bundle rows beyond the LIVE index rows (the committed
    publish diff vs what R2 serves). Nothing comes from an input."""
    registry = load_registry(repo / "trailforge/golden/ca-areas.json")
    bundle = (repo / BUNDLE_REPO).read_bytes()
    new_rows = _rows(bundle, "repo bundle index", compact=True)
    live_rows = json.loads(live_index)
    if new_rows[:len(live_rows)] != live_rows:
        raise CaError("repo bundle index does not start with the live index rows")
    ids = [r[0] for r in new_rows[len(live_rows):]]
    if not 1 <= len(ids) <= MAX_SYNC_AREAS:
        raise CaError(f"{len(ids)} new areas (need 1..{MAX_SYNC_AREAS})")
    if ids != sorted(ids):
        raise CaError("new index rows are not in sorted id order")
    unknown = [i for i in ids if i not in registry["by_id"]]
    if unknown:
        raise CaError(f"new index rows not in the Canada registry: {unknown}")
    if any(r[0] in set(ids) for r in live_rows):
        raise CaError("a new area is already in the live index")
    return ids


def derive_areas_cmd(args: argparse.Namespace) -> int:
    ids = derive_new_ids(args.repo, args.live_index.read_bytes())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(ids) + "\n", encoding="utf-8")
    print(f"{len(ids)} new area(s) from the committed index diff: {','.join(ids)}")
    return 0


def build_candidates(args: argparse.Namespace) -> int:
    repo = args.repo
    registry = load_registry(repo / "trailforge/golden/ca-areas.json")
    expected = load_expected(repo / "trailforge/golden/ca-areas-expected.json")
    live = {k: Path(v).read_bytes() for k, v in (("index.json", args.live_index),
            ("trail-search.json", args.live_search), ("trail-shapes.json", args.live_shapes))}
    ids = derive_new_ids(repo, live["index.json"])
    if args.areas_file:
        listed = [i for i in args.areas_file.read_text(encoding="utf-8").split() if i]
        if listed != ids:
            raise CaError("areas file differs from the committed index diff")
    geoms: dict[str, bytes] = {}
    sils: dict[str, bytes] = {}
    for aid in ids:
        geoms[aid] = (repo / f"public/areas/geom/{aid}.json").read_bytes()
        sils[aid] = (repo / f"public/areas/silhouettes/{aid}.json").read_bytes()
        _area_geom_trails(geoms[aid], aid)
        contract.strict_json_loads(sils[aid].decode("utf-8"), f"{aid} silhouette")
        if aid not in expected:
            raise CaError(f"{aid} is not in the blessed expected table")
        problems = expected_problems(json.loads(geoms[aid]), aid, expected[aid])
        if problems:
            raise CaError(f"{aid} is not the blessed result: " + "; ".join(problems))
    bundle = (repo / BUNDLE_REPO).read_bytes()
    rows = json.loads(bundle)
    for row in rows[len(rows) - len(ids):]:
        check_new_row(row, registry["by_id"][row[0]], json.loads(geoms[row[0]]), "repo bundle index")
    if _dumps_compact(rows) != bundle:
        raise CaError("repo bundle index framing is not the compact producer framing")
    candidates: dict[str, bytes] = {}
    proofs = []
    for key, opener, closer, as_dict in (("trail-search.json", b"[", b"]", False),
                                         ("trail-shapes.json", b"{", b"}", True)):
        records = [rec for aid in ids for rec in expected_records(key, aid, geoms[aid])]
        after = smp._splice(live[key], records, opener, closer, as_dict)
        proofs.append(prove_global_delta(key, live[key], after, geoms))
        candidates[key] = after
    candidates["index.json"] = bundle

    out = args.out_dir
    plan: dict[str, Any] = {"schema": "trekdex-ca-r2-plan-v1", "areas": ids, "writes": [], "baseline": {},
                            "proofs": proofs}
    order: list[tuple[str, str, bytes]] = []
    order += [(f"{a}.json", f"geom/{a}.json", geoms[a]) for a in ids]
    order += [(f"silhouettes/{a}.json", f"sil/{a}.json", sils[a]) for a in ids]
    order += [("trail-shapes.json", "trail-shapes.json", candidates["trail-shapes.json"]),
              ("trail-search.json", "trail-search.json", candidates["trail-search.json"]),
              ("index.json", "index.json", candidates["index.json"])]
    keys = [k for k, _, _ in order]
    if len(keys) != 2 * len(ids) + 3 or len(set(keys)) != len(keys):
        raise CaError("write set is not exactly 2N area keys + the three shared files")
    for key, name, data in order:
        (out / name).parent.mkdir(parents=True, exist_ok=True)
        (out / name).write_bytes(data)
        plan["writes"].append({"key": key, "file": name, "bytes": len(data), "sha256": _sha(data)})
    for key, data in live.items():
        plan["baseline"][key] = {"bytes": len(data), "sha256": _sha(data)}
    _write_json(out / "plan.json", plan)
    print(json.dumps({"areas": ids, "writes": len(plan["writes"])}, indent=2))
    return 0


def rollback_decide(args: argparse.Namespace) -> int:
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    actions = smp.rollback_actions(plan, args.live_dir)
    for key in ("index.json", "trail-search.json", "trail-shapes.json"):
        print(f"{key}\t{actions[key]}")
    for write in plan["writes"]:
        if write["key"] not in SHARED_KEYS:
            print(f"{write['key']}\tdelete")
    return 0


# ----------------------------------------------------------------------- CLI --

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("select")
    s.add_argument("--registry", type=Path, default=REGISTRY_PATH)
    s.add_argument("--index", type=Path, default=ROOT / INDEX_REPO)
    s.add_argument("--areas", required=True)
    s.add_argument("--province", required=True, choices=sorted(PROVINCES))
    s.add_argument("--chunk-size", default=str(DEFAULT_CHUNK))
    s.add_argument("--mode", required=True)
    s.add_argument("--out", required=True, type=Path)
    s.add_argument("--github-output", type=Path)
    a = sub.add_parser("prepare-areas")
    a.add_argument("--registry", type=Path, default=REGISTRY_PATH)
    a.add_argument("--selected", required=True, type=Path)
    a.add_argument("--bounds-pbf", required=True, type=Path)
    a.add_argument("--mode", required=True, choices=["qualify", "publish"])
    a.add_argument("--out-dir", required=True, type=Path)
    u = sub.add_parser("check-boundary-unique")
    u.add_argument("--registry", type=Path, default=REGISTRY_PATH)
    u.add_argument("--area-id", required=True)
    u.add_argument("--pbf", required=True, type=Path)
    pu = sub.add_parser("pbf-url")
    pu.add_argument("--province", required=True, choices=sorted(PROVINCES))
    ip = sub.add_parser("index-for-publish")
    ip.add_argument("--index", required=True, type=Path)
    ip.add_argument("--area-json", required=True, type=Path)
    ip.add_argument("--out", required=True, type=Path)
    fp = sub.add_parser("finish-publish")
    fp.add_argument("--index", required=True, type=Path)
    fp.add_argument("--publish-index", required=True, type=Path)
    fp.add_argument("--geom", required=True, type=Path)
    fp.add_argument("--area-json", required=True, type=Path)
    e = sub.add_parser("assert-expected")
    e.add_argument("--geom", required=True, type=Path)
    e.add_argument("--area-id", required=True)
    e.add_argument("--expected", type=Path, default=EXPECTED_PATH)
    r = sub.add_parser("report-area")
    for name in ("area-json", "geom", "trails-geojson", "removed-geojson", "assembly-json", "out"):
        r.add_argument(f"--{name}", required=True, type=Path)
    r.add_argument("--expected", type=Path, default=EXPECTED_PATH)
    r.add_argument("--live-geom-dir", type=Path, default=LIVE_GEOM_DIR)
    r.add_argument("--live-index", type=Path, default=ROOT / INDEX_REPO)
    f = sub.add_parser("mark-failed")
    f.add_argument("--area-id", required=True)
    f.add_argument("--stage", required=True)
    f.add_argument("--area-json", type=Path)
    f.add_argument("--out", required=True, type=Path)
    q = sub.add_parser("qualify-aggregate")
    q.add_argument("--registry", type=Path, default=REGISTRY_PATH)
    q.add_argument("--reports-dir", required=True, type=Path)
    q.add_argument("--out-dir", required=True, type=Path)
    m = sub.add_parser("merge-index")
    m.add_argument("--registry", type=Path, default=REGISTRY_PATH)
    m.add_argument("--selected", required=True, type=Path)
    m.add_argument("--rows-dir", required=True, type=Path)
    m.add_argument("--geom-dir", required=True, type=Path)
    m.add_argument("--target", required=True, type=Path)
    for name in ("verify-staged", "verify-commit"):
        v = sub.add_parser(name)
        v.add_argument("--repo", type=Path, default=Path("."))
        v.add_argument("--areas", required=True)
        v.add_argument("--mode", choices=["publish"], default="publish")
        v.add_argument("--output", type=Path)
    d = sub.add_parser("derive-areas")
    d.add_argument("--repo", type=Path, default=Path("."))
    d.add_argument("--live-index", required=True, type=Path)
    d.add_argument("--out", required=True, type=Path)
    b = sub.add_parser("build-candidates")
    b.add_argument("--repo", type=Path, default=Path("."))
    for name in ("live-index", "live-search", "live-shapes"):
        b.add_argument(f"--{name}", required=True, type=Path)
    b.add_argument("--areas-file", type=Path)
    b.add_argument("--out-dir", required=True, type=Path)
    c = sub.add_parser("rollback-decide")
    c.add_argument("--plan", required=True, type=Path)
    c.add_argument("--live-dir", required=True, type=Path)
    args = p.parse_args(argv)
    handlers = {"select": select_cmd, "prepare-areas": prepare_areas,
                "check-boundary-unique": check_boundary_unique_cmd,
                "pbf-url": pbf_url_cmd, "index-for-publish": index_for_publish_cmd, "finish-publish": finish_publish_cmd, "assert-expected": assert_expected,
                "report-area": report_area, "mark-failed": mark_failed, "qualify-aggregate": qualify_aggregate,
                "merge-index": merge_index, "verify-staged": verify_cmd, "verify-commit": verify_cmd,
                "derive-areas": derive_areas_cmd, "build-candidates": build_candidates,
                "rollback-decide": rollback_decide}
    try:
        return handlers[args.command](args)
    except (CaError, smp.SimpleError, contract.ManifestError, contract.InventoryError, OSError, ValueError,
            KeyError) as error:
        print(f"REFUSED: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
