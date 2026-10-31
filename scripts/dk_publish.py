#!/usr/bin/env python3
"""Registry-driven Denmark publish helpers (N areas per run, hundreds in total).

Generalises scripts/simple_mols_publish.py without touching it. Nothing here
calls R2/AWS; the sync workflow does that with the files this script builds.

  select            resolve the dispatch `areas` input (ids or batch=<n>) against
                    trailforge/golden/dk-areas.json; enforce the chunk cap
  prepare-areas     derive each area's boundary bbox from the PBF boundary
                    relation, check/propose the padded bbox + centre, write the
                    per-area one-row publish index. Fail closed on a missing or
                    inconsistent registry bbox when publishing
  assert-expected   refuse unless a published geom matches the blessed table
  report-area       qualify: per-area report from the pipeline outputs
  qualify-aggregate qualify: merge per-area reports + proposed tables
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
import r2_release_contract as contract  # noqa: E402
import simple_mols_publish as smp  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / "trailforge" / "golden" / "dk-areas.json"
# Per-entry Denmark rule set; Mols stays "legacy" so the pinned control is unchanged.
RULE_SETS = {"legacy", "dk-v2", "dk-v3"}
# Documented hold reasons (a held entry is skipped by batch= selection; name it in areas= to run it).
#   island / np-nested / name-collision        discovery holds
#   merge-into-klosterhede                     Kronhede is part of Klosterheden (same trails)
#   duplicate-of-live                          same trails as an already published park
#   needs-rescope                              island polygon is not one hiking destination
#   merge-pending                              should become one area with its sibling
#   mtb-centre                                 mostly an MTB trail centre
#   curated-do-not-ship                        reviewed by a local-expert curation pass: not a hiking destination
HOLD_REASONS = {
    "island", "np-nested", "name-collision", "merge-into-klosterhede",
    "duplicate-of-live", "needs-rescope", "merge-pending", "mtb-centre",
    "curated-do-not-ship",
}
# Curated per-area exclusions (registry `exclude_trails`, dk-v2 only); same shape as
# trailforge/assemble/quality.py validate_dk_exclude: {"id": "r<relation>" | "n:<name>", "name", "reason"}.
EXCLUDE_ID = re.compile(r"^(r[1-9][0-9]*|n:\S.*)$")
EXCLUDE_KEYS = {"id", "name", "reason"}
# A trail list this generic after filtering is not a hiking destination (no names are invented).
GENERIC_EXCLUDE_SHARE = 0.5
_GENERIC_WORDS = {
    "rød", "røde", "rødt", "gul", "gule", "gult", "blå", "blåt", "grøn", "grønne", "grønt",
    "orange", "brun", "brune", "brunt", "sort", "sorte", "lilla", "hvid", "hvide", "hvidt",
    "grå", "pink", "turkis", "lyserød", "rute", "ruten", "spor", "sporet", "tværspor", "tur",
    "løberute", "vandrerute", "vandretur", "route", "trail", "nr", "og",
    "første", "andet", "anden", "tredje", "fjerde", "femte", "sjette", "syvende", "ottende",
    "niende", "tiende",
}
EXPECTED_PATH = ROOT / "trailforge" / "golden" / "dk-areas-expected.json"
INDEX_REPO = "public/areas/index.json"
BUNDLE_REPO = "ios/SouthMountainExplorer/Resources/areas-index.json"
REGISTRY_SCHEMA = "trekdex-dk-areas-v1"
EXPECTED_SCHEMA = "trekdex-dk-areas-expected-v1"
EXTRACT, STATE, REGION = "europe/denmark", "Denmark", "dk"
DEFAULT_CHUNK = 25
HARD_MAX_CHUNK = 40
MAX_SYNC_AREAS = 120
PAD = 0.05
MARGIN_MIN = 0.02          # registry bbox must clear the boundary by this much...
MARGIN_MAX = 0.10          # ...and by no more than this (Mols: 0.0296 .. 0.0527)
DEFAULT_MI_TOLERANCE = 0.05
SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*-dk$")
SHARED_KEYS = smp.SHARED_KEYS


class DkError(Exception):
    pass


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_json(path: Path, what: str) -> Any:
    try:
        return contract.strict_json_loads(Path(path).read_text(encoding="utf-8"), what)
    except (OSError, contract.InventoryError) as error:
        raise DkError(f"cannot read {what}: {error}") from error


def _write_json(path: Path, document: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _is_num(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


# ----------------------------------------------------------------- registry --

def _check_exclude_trails(aid: str, entry: dict[str, Any], field: str = "exclude_trails",
                          rule_sets: tuple[str, ...] = ("dk-v2", "dk-v3")) -> None:
    if field not in entry:
        return
    items = entry[field]
    if entry.get("rules", "legacy") not in rule_sets:
        raise DkError(f"{aid}: {field} requires rules {rule_sets[0] if len(rule_sets) == 1 else 'dk-v2'}")
    if not isinstance(items, list) or not items:
        raise DkError(f"{aid}: {field} must be a non-empty list when present")
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict) or set(item) != EXCLUDE_KEYS:
            raise DkError(f"{aid}: {field} entries need exactly {sorted(EXCLUDE_KEYS)}")
        if not all(isinstance(item[k], str) and item[k].strip() for k in EXCLUDE_KEYS):
            raise DkError(f"{aid}: {field} entry has an empty field: {item.get('id')!r}")
        ident, name = item["id"], item["name"]
        if (ident != unicodedata.normalize("NFC", ident) or name != unicodedata.normalize("NFC", name)
                or not EXCLUDE_ID.fullmatch(ident)):
            raise DkError(f"{aid}: {field} id/name is invalid: {ident!r}")
        if ident.startswith("n:") and ident[2:] != name:
            raise DkError(f"{aid}: {field} {ident!r}: name must equal the id name")
        if ident in seen:
            raise DkError(f"{aid}: {field} repeats {ident!r}")
        seen.add(ident)
def load_registry(path: Path = REGISTRY_PATH) -> dict[str, Any]:
    doc = _read_json(path, "Denmark area registry")
    if not isinstance(doc, dict) or doc.get("schema") != REGISTRY_SCHEMA:
        raise DkError("registry schema is not " + REGISTRY_SCHEMA)
    if (doc.get("extract"), doc.get("state"), doc.get("region_code")) != (EXTRACT, STATE, REGION):
        raise DkError("registry extract/state/region_code are not the Denmark pins")
    areas = doc.get("areas")
    if not isinstance(areas, list) or not areas:
        raise DkError("registry has no areas")
    ids: set[str] = set()
    rels: set[int] = set()
    for entry in areas:
        if not isinstance(entry, dict):
            raise DkError("registry entry is not an object")
        aid, name, rel = entry.get("id"), entry.get("name"), entry.get("relation_id")
        if not isinstance(aid, str) or not SLUG.fullmatch(aid):
            raise DkError(f"registry id is invalid: {aid!r}")
        if aid in ids:
            raise DkError(f"registry repeats id {aid}")
        if (not isinstance(name, str) or not name.strip()
                or name != unicodedata.normalize("NFC", name)):
            raise DkError(f"{aid}: name is invalid")
        if entry.get("state") != STATE:
            raise DkError(f"{aid}: state must be {STATE!r}")
        if not isinstance(rel, int) or isinstance(rel, bool) or rel <= 0 or rel in rels:
            raise DkError(f"{aid}: relation_id is invalid or repeated")
        bbox, center = entry.get("bbox"), entry.get("center")
        if (bbox is None) != (center is None):
            raise DkError(f"{aid}: bbox and center must both be set or both be null")
        if bbox is not None:
            if (not isinstance(bbox, list) or len(bbox) != 4 or not all(_is_num(v) for v in bbox)
                    or not (6 <= bbox[0] < bbox[2] <= 16 and 54 <= bbox[1] < bbox[3] <= 58)):
                raise DkError(f"{aid}: bbox must be [lon_min, lat_min, lon_max, lat_max] inside Denmark")
            if (not isinstance(center, list) or len(center) != 2
                    or not all(_is_num(v) for v in center)):
                raise DkError(f"{aid}: center must be [lat, lon]")
        if not isinstance(entry.get("tier"), int) or isinstance(entry.get("tier"), bool):
            raise DkError(f"{aid}: tier must be an integer")
        if not isinstance(entry.get("notes", ""), str):
            raise DkError(f"{aid}: notes must be text")
        if entry.get("rules", "legacy") not in RULE_SETS:
            raise DkError(f"{aid}: rules must be one of {sorted(RULE_SETS)}")
        hold = entry.get("hold")
        if hold is not None and (not isinstance(hold, str) or not hold.strip()):
            raise DkError(f"{aid}: hold must be null or a reason string")
        if hold is not None and hold not in HOLD_REASONS:
            raise DkError(f"{aid}: hold {hold!r} is not one of {sorted(HOLD_REASONS)}")
        _check_exclude_trails(aid, entry)
        _check_exclude_trails(aid, entry, "include_trails", ("dk-v3",))
        est_mi = entry.get("est_mi")
        if est_mi is not None and (not _is_num(est_mi) or est_mi < 0):
            raise DkError(f"{aid}: est_mi must be a non-negative number")
        est_trails = entry.get("est_trails")
        if est_trails is not None and (not isinstance(est_trails, int) or isinstance(est_trails, bool)
                                       or est_trails < 0):
            raise DkError(f"{aid}: est_trails must be a non-negative integer")
        ids.add(aid)
        rels.add(rel)
    doc["by_id"] = {a["id"]: a for a in areas}
    return doc


def bbox_arg(bbox: list[float]) -> str:
    return ",".join(f"{v:.2f}" for v in bbox)


# ------------------------------------------------------------ expected table --

def load_expected(path: Path = EXPECTED_PATH) -> dict[str, dict[str, Any]]:
    doc = _read_json(path, "blessed expected table")
    if not isinstance(doc, dict) or doc.get("schema") != EXPECTED_SCHEMA or not isinstance(doc.get("areas"), dict):
        raise DkError("expected table schema is invalid")
    for aid, entry in doc["areas"].items():
        if (not isinstance(entry, dict) or not SLUG.fullmatch(aid)
                or not isinstance(entry.get("trail_count"), int) or isinstance(entry.get("trail_count"), bool)
                or entry["trail_count"] <= 0 or not _is_num(entry.get("total_mi")) or entry["total_mi"] <= 0
                or not _is_num(entry.get("total_mi_tolerance", DEFAULT_MI_TOLERANCE))
                or not all(isinstance(entry.get(k), list) and all(isinstance(n, str) for n in entry[k])
                           for k in ("required_names", "forbidden_names"))):
            raise DkError(f"expected table entry {aid!r} is invalid")
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
        raise DkError(f"{args.area_id} is not in the blessed expected table; run qualify and bless it first")
    geom = _read_json(args.geom, "published geom")
    problems = expected_problems(geom, args.area_id, expected[args.area_id])
    if problems:
        raise DkError(f"{args.area_id} is not the blessed result: " + "; ".join(problems))
    print(f"{args.area_id}: matches blessed table ({geom['trail_count']} trails, {geom['total_mi']} mi)")
    return 0


# ------------------------------------------------------------------- select --

def _index_ids(path: Path) -> set[str]:
    rows = _read_json(path, "area index")
    return {r[0] for r in rows if isinstance(r, list) and r and isinstance(r[0], str)}


def select_areas(registry: dict[str, Any], published: set[str], areas: str, chunk_size: int,
                 mode: str) -> list[str]:
    if mode not in ("qualify", "publish"):
        raise DkError("mode must be qualify or publish")
    if not 1 <= chunk_size <= HARD_MAX_CHUNK:
        raise DkError(f"chunk_size must be 1..{HARD_MAX_CHUNK}")
    if areas.startswith("batch="):
        text = areas[len("batch="):]
        if not re.fullmatch(r"[1-9][0-9]{0,3}", text):
            raise DkError("batch must be a positive integer")
        n = int(text)
        # Held entries (non-null `hold`) are skipped; name them in `areas=` to select them.
        # Order: tier 1 (national parks) first, then est_mi descending, then registry order.
        rank = {a["id"]: (a["tier"] != 1, -(a.get("est_mi") or 0), n_) for n_, a in enumerate(registry["areas"])}
        unpublished = sorted((a["id"] for a in registry["areas"]
                              if a["id"] not in published and a.get("hold") is None), key=rank.__getitem__)
        chosen = unpublished[(n - 1) * chunk_size: n * chunk_size]
        if not chosen:
            raise DkError(f"batch={n} selects nothing ({len(unpublished)} unpublished areas, chunk {chunk_size})")
    else:
        chosen = areas.split(",")
        if any(not SLUG.fullmatch(i) for i in chosen):
            raise DkError("areas must be a comma list of registry ids (no spaces) or batch=<n>")
        if len(set(chosen)) != len(chosen):
            raise DkError("areas repeats an id")
        unknown = [i for i in chosen if i not in registry["by_id"]]
        if unknown:
            raise DkError(f"not in the registry: {unknown}")
        if len(chosen) > chunk_size:
            raise DkError(f"{len(chosen)} areas exceeds the chunk cap {chunk_size} (hard max {HARD_MAX_CHUNK})")
    if mode == "publish":
        already = [i for i in chosen if i in published]
        if already:
            raise DkError(f"already in public/areas/index.json (publish appends new areas only): {already}")
    return chosen


def select_cmd(args: argparse.Namespace) -> int:
    registry = load_registry(args.registry)
    chosen = select_areas(registry, _index_ids(args.index), args.areas, int(args.chunk_size), args.mode)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(chosen) + "\n", encoding="utf-8")
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as stream:
            relations = " ".join(f"r{registry['by_id'][i]['relation_id']}" for i in chosen)
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


def boundary_bboxes(pbf: Path, relation_ids: list[int]) -> dict[int, list[float] | None]:
    return {rel: info["bbox"] for rel, info in boundary_info(pbf, relation_ids).items()}


# A registry entry without a discovery candidate (forests, plantations, heaths,
# islands: discovery lists protected areas only) is cross-checked against its own
# OSM relation instead of the discovery centre.
REL_TYPES = {"boundary", "multipolygon"}
REL_NAME_KEYS = ("name", "name:da", "official_name", "alt_name", "short_name")
REL_SPAN_MIN, REL_SPAN_MAX = 0.01, 1.0     # degrees, per axis (about 1 km .. 110 km)
DK_LON, DK_LAT = (6.0, 16.0), (54.0, 58.0)


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
    names = {unicodedata.normalize("NFC", tags[k]) for k in REL_NAME_KEYS if tags.get(k)}
    if entry["name"] not in names:
        problems.append(f"relation {entry['relation_id']} name {sorted(names)} does not match registry name {entry['name']!r}")
    box = info.get("bbox")
    if box is not None:
        w, h = box[2] - box[0], box[3] - box[1]
        if not (REL_SPAN_MIN <= w <= REL_SPAN_MAX and REL_SPAN_MIN <= h <= REL_SPAN_MAX):
            problems.append(f"boundary size {w:.3f} x {h:.3f} degrees is implausible "
                            f"(each side must be {REL_SPAN_MIN}..{REL_SPAN_MAX})")
        if not (DK_LON[0] <= box[0] and box[2] <= DK_LON[1] and DK_LAT[0] <= box[1] and box[3] <= DK_LAT[1]):
            problems.append(f"boundary bbox {box} is outside Denmark")
    return problems


def pad_bbox(box: list[float]) -> list[float]:
    return [round(box[0] - PAD, 2), round(box[1] - PAD, 2), round(box[2] + PAD, 2), round(box[3] + PAD, 2)]


def center_of(box: list[float]) -> list[float]:
    return [round((box[1] + box[3]) / 2, 1), round((box[0] + box[2]) / 2, 1)]


def check_area_geometry(entry: dict[str, Any], boundary: list[float] | None, discovery_center: Any,
                        mode: str, relation_info: dict[str, Any] | None = None) -> dict[str, Any]:
    """Derive/check one area's bbox + centre. Returns the per-area prepare record."""
    problems: list[str] = []
    rec: dict[str, Any] = {"id": entry["id"], "name": entry["name"], "relation_id": entry["relation_id"],
                           "boundary_bbox": boundary, "registry_bbox": entry.get("bbox"),
                           "registry_center": entry.get("center")}
    if boundary is None:
        problems.append("boundary relation not found in the PBF (no member way geometry)")
        rec.update(status="failed", problems=problems, bbox_source=None, effective_bbox=None, center=None)
        return rec
    padded, centre = pad_bbox(boundary), center_of(boundary)
    rec["proposed_bbox"], rec["proposed_center"] = padded, centre
    if isinstance(discovery_center, list) and len(discovery_center) == 2:
        mid = [(boundary[1] + boundary[3]) / 2, (boundary[0] + boundary[2]) / 2]
        if abs(mid[0] - discovery_center[0]) > 0.05 or abs(mid[1] - discovery_center[1]) > 0.05:
            problems.append(f"boundary bbox midpoint {mid} disagrees with the discovery centre {discovery_center}")
        rec["cross_check"] = "discovery"
    else:
        # No discovery entry for this area: the boundary-derived centre/bbox stands, but only
        # after the registry's own relation (id, type, name, size) checks out.
        rec["cross_check"] = "registry-relation"
        problems += relation_problems(entry, relation_info)
    reg = entry.get("bbox")
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
        if [round(v, 6) for v in entry["center"]] != centre:
            problems.append(f"registry center {entry['center']} is not the boundary centre {centre}")
        rec.update(bbox_source="registry", effective_bbox=reg, center=entry["center"])
    rec["problems"] = problems
    rec["status"] = "failed" if problems else "ok"
    return rec


def prepare_areas(args: argparse.Namespace) -> int:
    registry = load_registry(args.registry)
    ids = [i for i in args.selected.read_text(encoding="utf-8").split() if i]
    discovery = _read_json(args.discovery_report, "discovery report")
    by_disc = {c["index_row"][0]: c for c in discovery.get("candidates", [])
               if isinstance(c, dict) and isinstance(c.get("index_row"), list) and c["index_row"]}
    entries = [registry["by_id"][i] for i in ids]
    infos = boundary_info(args.bounds_pbf, [e["relation_id"] for e in entries])
    out: list[dict[str, Any]] = []
    for entry in entries:
        cand = by_disc.get(entry["id"])
        disc_center = cand["index_row"][3:5] if cand else None
        info = infos[entry["relation_id"]]
        rec = check_area_geometry(entry, info["bbox"], disc_center, args.mode, info)
        if rec["status"] == "ok":
            rec["bbox_arg"] = bbox_arg(rec["effective_bbox"])
            row = [entry["id"], entry["name"], STATE, rec["center"][0], rec["center"][1], None, None,
                   entry["relation_id"]]
            _write_json(args.out_dir / entry["id"] / "index.json", [row])
        _write_json(args.out_dir / entry["id"] / "area.json", rec)
        out.append(rec)
        print(f"{entry['id']}: {rec['status']} bbox={rec.get('bbox_arg')} source={rec.get('bbox_source')} "
              f"{'; '.join(rec['problems'])}")
    _write_json(args.out_dir / "prepare.json", {"areas": out})
    failed = [r["id"] for r in out if r["status"] != "ok"]
    if failed and args.mode == "publish":
        raise DkError(f"area geometry check failed for {failed}")
    return 0


# ------------------------------------------------------------- qualify report --

def _features(path: Path) -> list[dict[str, Any]]:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    return [f.get("properties") or {} for f in doc.get("features") or []]


def is_generic_trail_name(name: Any) -> bool:
    """'Rute 8', 'Spor 3', 'gul rute', 'Blå Rute': only colour/number/route words."""
    tokens = [t for t in re.split(r"[\s,;:/()\-–.]+", str(name or "").casefold()) if t]
    return bool(tokens) and all(t in _GENERIC_WORDS or t.isdigit() for t in tokens)
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
    gap = sorted({(p.get("name"), p.get("length_mi")) for p in removed
                  if p.get("removed_category") == "unresolved-relation-gap"},
                 key=lambda item: (str(item[0]), item[1] or 0))
    junk_categories = {"dk-unqualified-road-track", "dk-road-name", "disconnected-name-stitch",
                       "unresolved-relation-gap", "dk-mtb-trail", "dk-non-hiking",
                       "dk-long-distance", "dk-fragment", "dk-curated-exclude"}
    forbidden = sorted({p.get("name") for p in removed
                        if p.get("removed_category") in junk_categories and p.get("name")} - set(names))
    curated = sorted(
        ({"id": p.get("curated_exclude_id"), "name": p.get("name"), "length_mi": p.get("length_mi"),
          "reason": p.get("curated_exclude_reason")}
         for p in removed if p.get("removed_category") == "dk-curated-exclude"),
        key=lambda item: str(item["id"]))
    generic = [n for n in names if is_generic_trail_name(n)]
    generic_share = round(len(generic) / len(names), 3) if names else 0.0
    rec = {
        "id": area["id"], "name": area["name"], "relation_id": area["relation_id"], "status": "ok",
        "generic_names": generic, "generic_share": generic_share,
        "exclude_recommended": bool(names) and generic_share >= GENERIC_EXCLUDE_SHARE,
        "trail_count": geom.get("trail_count"), "total_mi": geom.get("total_mi"),
        "pre_clip_trail_count": assembly.get("pre_clip_trail_count"),
        "post_clip_trail_count": assembly.get("post_clip_trail_count"),
        "removed_category_counts": dict(sorted(categories.items())),
        "trail_names": names,
        "route_segments": routes,
        "curated_excluded": curated,
        "dropped_unresolved_relation_gap": [{"name": n, "length_mi": m} for n, m in gap],
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
    # dk-v3 only: routes published whole (key absent for dk-v2/legacy runs).
    whole = [{"name": p.get("name"), "length_mi": p.get("length_mi"),
              "area_share": p.get("dk_route_share")}
             for p in kept if p.get("dk_whole_route")]
    if whole:
        rec["whole_routes"] = sorted(whole, key=lambda item: str(item["name"]))
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
    _write_json(args.out_dir / "qualify-report.json", {"areas": reports})
    _write_json(args.out_dir / "dk-areas-expected.proposed.json", proposed_expected)
    _write_json(args.out_dir / "dk-areas.proposed.json", proposed_registry)
    lines = ["# Denmark qualify report", "",
             "| area | status | trails | mi | pre>post clip | bbox source | blessed |", "|---|---|---|---|---|---|---|"]
    for r in reports:
        lines.append("| {id} | {status} | {t} | {m} | {pre}>{post} | {src} | {b} |".format(
            id=r["id"], status=r["status"], t=r.get("trail_count", "-"), m=r.get("total_mi", "-"),
            pre=r.get("pre_clip_trail_count", "-"), post=r.get("post_clip_trail_count", "-"),
            src=r.get("bbox_source", "-"), b=r.get("blessed_match", "-")))
    for r in ok:
        lines += ["", f"## {r['id']}"
                  + (" EXCLUDE-RECOMMENDED (generic names)" if r.get("exclude_recommended") else ""),
                  f"removed: `{json.dumps(r['removed_category_counts'], ensure_ascii=False)}`",
                  f"generic names ({r.get('generic_share', 0)}): {r.get('generic_names', [])}",
                  f"route segments: {[(x['name'], x['length_mi']) for x in r['route_segments']]}",
                  f"curated excluded: {[(x['id'], x['name'], x['length_mi'], x['reason']) for x in r.get('curated_excluded', [])]}",
                  f"dropped unresolved-relation-gap: {[(x['name'], x['length_mi']) for x in r['dropped_unresolved_relation_gap']]}",
                  f"bbox: boundary {r['boundary_bbox']} proposed {r['proposed_bbox']} centre {r['proposed_center']}",
                  "", "trails: " + "; ".join(str(n) for n in r["trail_names"])]
    for r in failed:
        lines += ["", f"## {r['id']} FAILED at {r.get('stage', 'prepare')}", *[f"- {p}" for p in r.get("problems", [])]]
    (args.out_dir / "qualify-report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:6 + len(reports)]))
    return 1 if failed else 0


# ------------------------------------------------------------- index handling --

def _dumps_default(rows: list[Any]) -> bytes:
    return json.dumps(rows, ensure_ascii=True, allow_nan=False).encode("utf-8")


def _dumps_compact(rows: list[Any]) -> bytes:
    return json.dumps(rows, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("utf-8")


def _rows(payload: bytes, context: str, compact: bool) -> list[list[Any]]:
    try:
        rows = contract.strict_json_loads(payload.decode("utf-8"), context)
    except (UnicodeDecodeError, contract.InventoryError) as error:
        raise DkError(f"{context} is invalid: {error}") from error
    if not isinstance(rows, list) or (_dumps_compact if compact else _dumps_default)(rows) != payload:
        raise DkError(f"{context} byte framing differs from its producer")
    seen: set[str] = set()
    for number, row in enumerate(rows, start=1):
        if not isinstance(row, list) or len(row) < 5 or not isinstance(row[0], str) or row[0] in seen:
            raise DkError(f"{context} row {number} is invalid")
        seen.add(row[0])
    return rows


def check_new_row(row: list[Any], entry: dict[str, Any], geom: dict[str, Any], context: str) -> None:
    if (len(row) != 8 or row[0] != entry["id"] or row[1] != entry["name"] or row[2] != STATE
            or row[7] != entry["relation_id"]):
        raise DkError(f"{context}: {entry['id']} row has the wrong identity")
    if entry.get("center") is None or [row[3], row[4]] != entry["center"]:
        raise DkError(f"{context}: {entry['id']} row centre differs from the registry")
    if (row[5] != geom.get("trail_count") or row[5] != len(geom.get("trails") or [])
            or not _is_num(row[6]) or row[6] != geom.get("total_mi") or row[6] <= 0 or row[5] <= 0):
        raise DkError(f"{context}: {entry['id']} row counts differ from its geom")


def merge_index(args: argparse.Namespace) -> int:
    registry = load_registry(args.registry)
    target = args.target
    if target.is_symlink() or not target.is_file():
        raise DkError("index target is missing or unsafe")
    before = target.read_bytes()
    rows = _rows(before, "canonical master index", compact=False)
    ids = [i for i in args.selected.read_text(encoding="utf-8").split() if i]
    new_rows = []
    for aid in sorted(ids):
        src = _read_json(args.rows_dir / aid / "index.json", f"{aid} published row")
        if not isinstance(src, list) or len(src) != 1:
            raise DkError(f"{aid}: published index is not exactly one row")
        row = src[0]
        geom = _read_json(args.geom_dir / f"{aid}.json", f"{aid} geom")
        check_new_row(row, registry["by_id"][aid], geom, "published row")
        if any(r[0] == aid for r in rows):
            raise DkError(f"{aid} is already in the canonical master index")
        new_rows.append(row)
    target.write_bytes(_dumps_default([*rows, *new_rows]))
    after = target.read_bytes()
    if not after.startswith(before[:-1] + b", "):
        raise DkError("merge would change existing index bytes")
    print(f"appended {len(new_rows)} row(s) to {target}")
    return 0


def _git(repo: Path, *argv: str) -> bytes:
    try:
        done = subprocess.run(["git", *argv], cwd=repo, capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as error:
        raise DkError(f"git probe failed: {type(error).__name__}") from error
    if done.returncode != 0:
        raise DkError("git probe returned nonzero: " + " ".join(argv[:3]))
    return done.stdout


def area_paths(ids: list[str]) -> list[str]:
    paths = [INDEX_REPO, BUNDLE_REPO]
    for aid in ids:
        paths += [f"public/areas/geom/{aid}.json", f"public/areas/silhouettes/{aid}.json"]
    return sorted(paths)


def _verify_delta(repo: Path, ids: list[str], *, staged: bool) -> dict[str, Any]:
    registry = load_registry(repo / "trailforge/golden/dk-areas.json")
    names_cmd = ["diff", "--cached"] if staged else ["diff"]
    span = [] if staged else ["HEAD^", "HEAD"]
    changed = [n for n in _git(repo, *names_cmd, "--name-only", "--diff-filter=ACMR", "-z", *span)
               .decode("utf-8").split("\0") if n]
    if _git(repo, *names_cmd, "--name-only", "--diff-filter=D", "-z", *span):
        raise DkError("diff contains a deletion")
    want = area_paths(ids)
    if len(want) != 2 * len(ids) + 2 or changed != want:
        raise DkError(f"diff is not the exact 2N+2 allowlist: {changed}")

    def show(path: str, new: bool) -> bytes:
        return _git(repo, "show", (f":{path}" if staged else f"HEAD:{path}") if new else
                    (f"HEAD:{path}" if staged else f"HEAD^:{path}"))

    geoms = {}
    for aid in ids:
        geom = contract.strict_json_loads(show(f"public/areas/geom/{aid}.json", True).decode("utf-8"), f"{aid} geom")
        if not isinstance(geom, dict) or geom.get("id") != aid or not geom.get("trails"):
            raise DkError(f"{aid}: geom identity/trails are invalid")
        sil = contract.strict_json_loads(show(f"public/areas/silhouettes/{aid}.json", True).decode("utf-8"),
                                         f"{aid} silhouette")
        if (not isinstance(sil, dict) or set(sil) != {"b", "l"} or not isinstance(sil["b"], list)
                or len(sil["b"]) != 4 or not isinstance(sil["l"], list) or not sil["l"]):
            raise DkError(f"{aid}: silhouette is malformed")
        geoms[aid] = geom
    transitions = []
    for path, compact in ((INDEX_REPO, False), (BUNDLE_REPO, True)):
        before, after = show(path, False), show(path, True)
        brows, arows = _rows(before, f"baseline {path}", compact), _rows(after, f"candidate {path}", compact)
        added = arows[len(brows):]
        if arows[:len(brows)] != brows or [r[0] for r in added] != sorted(ids):
            raise DkError(f"{path}: rows are not baseline + the new areas appended in sorted id order")
        dump = _dumps_compact if compact else _dumps_default
        sep = b"," if compact else b", "
        if after != before[:-1] + sep + sep.join(dump([r])[1:-1] for r in added) + b"]":
            raise DkError(f"{path}: existing bytes changed")
        for row in added:
            check_new_row(row, registry["by_id"][row[0]], geoms[row[0]], path)
        transitions.append({"path": path, "baseline_sha256": _sha(before), "candidate_sha256": _sha(after),
                            "appended_ids": [r[0] for r in added]})
    return {"schema": "trekdex-dk-delta-v1", "area_ids": ids, "allowed_paths": want, "index_transitions": transitions}


def verify_cmd(args: argparse.Namespace) -> int:
    ids = [i for i in args.areas.split(",") if i]
    if not ids or len(ids) > HARD_MAX_CHUNK or len(set(ids)) != len(ids):
        raise DkError("areas must be 1..40 unique ids")
    summary = _verify_delta(args.repo.resolve(), ids, staged=args.command == "verify-staged")
    summary["receipt_sha256"] = contract.canonical_sha256(summary)
    if args.output:
        _write_json(args.output, summary)
    print(f"verified exact {len(ids)}-area delta ({2 * len(ids) + 2} paths) sha256={summary['receipt_sha256']}")
    return 0


# ----------------------------------------------------------- R2 candidates ---

def _area_geom_trails(payload: bytes, area_id: str) -> list[dict[str, Any]]:
    doc = contract.strict_json_loads(payload.decode("utf-8"), f"{area_id} geom")
    trails = doc.get("trails") if isinstance(doc, dict) else None
    if doc.get("id") != area_id or not isinstance(trails, list) or not trails:
        raise DkError(f"{area_id}: geom identity/trails are invalid")
    seen: set[str] = set()
    for number, trail in enumerate(trails, start=1):
        if (not isinstance(trail, dict) or not contract._is_plain_safe_text(trail.get("id"), maximum=512)
                or trail["id"] in seen):
            raise DkError(f"{area_id}: trail {number} identity is invalid or duplicate")
        seen.add(trail["id"])
    return trails


def expected_records(key: str, area_id: str, geom: bytes) -> list[list[Any]]:
    records: list[list[Any]] = []
    for trail in _area_geom_trails(geom, area_id):
        if key == "trail-search.json":
            if not contract._is_safe_display_text(trail.get("name"), maximum=1024):
                raise DkError(f"{area_id}: a trail has no qualified name")
            records.append([trail["name"], area_id, trail["id"], trail.get("distanceMi"), trail.get("difficulty")])
        else:
            segments = trail.get("segments") or []
            if not isinstance(segments, list) or not segments or any(not isinstance(s, list) for s in segments):
                raise DkError(f"{area_id}: trail {trail['id']} has no qualified segment")
            shape = contract._mols_shape(max(segments, key=len))
            if shape is None:
                raise DkError(f"{area_id}: trail {trail['id']} has no drawable segment")
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
        raise DkError(f"live {key} already has records for one of the new areas; refusing to overwrite")
    want = [rec for aid in ids for rec in expected_records(key, aid, geoms[aid])]
    if cand[:len(base)] != base or cand[len(base):] != want:
        raise DkError(f"candidate {key} is not live records + the exact deterministic additions")
    return {"key": key, "baseline_records": len(base), "added_records": len(want),
            "baseline_sha256": _sha(before), "candidate_sha256": _sha(after)}


def derive_new_ids(repo: Path, live_index: bytes) -> list[str]:
    """The new areas = repo bundle rows beyond the LIVE index rows (the committed
    publish diff vs what R2 serves). Nothing comes from an input."""
    registry = load_registry(repo / "trailforge/golden/dk-areas.json")
    bundle = (repo / BUNDLE_REPO).read_bytes()
    new_rows = _rows(bundle, "repo bundle index", compact=True)
    live_rows = json.loads(live_index)
    if new_rows[:len(live_rows)] != live_rows:
        raise DkError("repo bundle index does not start with the live index rows")
    ids = [r[0] for r in new_rows[len(live_rows):]]
    if not 1 <= len(ids) <= MAX_SYNC_AREAS:
        raise DkError(f"{len(ids)} new areas (need 1..{MAX_SYNC_AREAS})")
    if ids != sorted(ids):
        raise DkError("new index rows are not in sorted id order")
    unknown = [i for i in ids if i not in registry["by_id"]]
    if unknown:
        raise DkError(f"new index rows not in the Denmark registry: {unknown}")
    if any(r[0] in set(ids) for r in live_rows):
        raise DkError("a new area is already in the live index")
    return ids


def derive_areas_cmd(args: argparse.Namespace) -> int:
    ids = derive_new_ids(args.repo, args.live_index.read_bytes())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(ids) + "\n", encoding="utf-8")
    print(f"{len(ids)} new area(s) from the committed index diff: {','.join(ids)}")
    return 0


def build_candidates(args: argparse.Namespace) -> int:
    repo = args.repo
    registry = load_registry(repo / "trailforge/golden/dk-areas.json")
    expected = load_expected(repo / "trailforge/golden/dk-areas-expected.json")
    live = {k: Path(v).read_bytes() for k, v in (("index.json", args.live_index),
            ("trail-search.json", args.live_search), ("trail-shapes.json", args.live_shapes))}
    ids = derive_new_ids(repo, live["index.json"])
    if args.areas_file:
        listed = [i for i in args.areas_file.read_text(encoding="utf-8").split() if i]
        if listed != ids:
            raise DkError("areas file differs from the committed index diff")
    geoms: dict[str, bytes] = {}
    sils: dict[str, bytes] = {}
    for aid in ids:
        geoms[aid] = (repo / f"public/areas/geom/{aid}.json").read_bytes()
        sils[aid] = (repo / f"public/areas/silhouettes/{aid}.json").read_bytes()
        _area_geom_trails(geoms[aid], aid)
        contract.strict_json_loads(sils[aid].decode("utf-8"), f"{aid} silhouette")
        if aid not in expected:
            raise DkError(f"{aid} is not in the blessed expected table")
        problems = expected_problems(json.loads(geoms[aid]), aid, expected[aid])
        if problems:
            raise DkError(f"{aid} is not the blessed result: " + "; ".join(problems))
    bundle = (repo / BUNDLE_REPO).read_bytes()
    rows = json.loads(bundle)
    for row in rows[len(rows) - len(ids):]:
        check_new_row(row, registry["by_id"][row[0]], json.loads(geoms[row[0]]), "repo bundle index")
    if _dumps_compact(rows) != bundle:
        raise DkError("repo bundle index framing is not the compact producer framing")
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
    plan: dict[str, Any] = {"schema": "trekdex-dk-r2-plan-v1", "areas": ids, "writes": [], "baseline": {},
                            "proofs": proofs}
    order: list[tuple[str, str, bytes]] = []
    order += [(f"{a}.json", f"geom/{a}.json", geoms[a]) for a in ids]
    order += [(f"silhouettes/{a}.json", f"sil/{a}.json", sils[a]) for a in ids]
    order += [("trail-shapes.json", "trail-shapes.json", candidates["trail-shapes.json"]),
              ("trail-search.json", "trail-search.json", candidates["trail-search.json"]),
              ("index.json", "index.json", candidates["index.json"])]
    keys = [k for k, _, _ in order]
    if len(keys) != 2 * len(ids) + 3 or len(set(keys)) != len(keys):
        raise DkError("write set is not exactly 2N area keys + the three shared files")
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
    s.add_argument("--chunk-size", default=str(DEFAULT_CHUNK))
    s.add_argument("--mode", required=True)
    s.add_argument("--out", required=True, type=Path)
    s.add_argument("--github-output", type=Path)

    a = sub.add_parser("prepare-areas")
    a.add_argument("--registry", type=Path, default=REGISTRY_PATH)
    a.add_argument("--selected", required=True, type=Path)
    a.add_argument("--bounds-pbf", required=True, type=Path)
    a.add_argument("--discovery-report", required=True, type=Path)
    a.add_argument("--mode", required=True, choices=["qualify", "publish"])
    a.add_argument("--out-dir", required=True, type=Path)

    e = sub.add_parser("assert-expected")
    e.add_argument("--geom", required=True, type=Path)
    e.add_argument("--area-id", required=True)
    e.add_argument("--expected", type=Path, default=EXPECTED_PATH)

    r = sub.add_parser("report-area")
    for name in ("area-json", "geom", "trails-geojson", "removed-geojson", "assembly-json", "out"):
        r.add_argument(f"--{name}", required=True, type=Path)
    r.add_argument("--expected", type=Path, default=EXPECTED_PATH)

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
    handlers = {"select": select_cmd, "prepare-areas": prepare_areas, "assert-expected": assert_expected,
                "report-area": report_area, "mark-failed": mark_failed, "qualify-aggregate": qualify_aggregate,
                "merge-index": merge_index, "verify-staged": verify_cmd, "verify-commit": verify_cmd,
                "derive-areas": derive_areas_cmd, "build-candidates": build_candidates,
                "rollback-decide": rollback_decide}
    try:
        return handlers[args.command](args)
    except (DkError, smp.SimpleError, contract.ManifestError, contract.InventoryError, OSError, ValueError,
            KeyError) as error:
        print(f"REFUSED: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
