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
  qualify-aggregate qualify: merge per-area reports + proposed tables; fails closed
                    when one route appears whole in two areas of the set or in a live area
  merge-index       append the new area rows to public/areas/index.json
  verify-staged / verify-commit   exact 2N+2 path allowlist, rows append-only
  derive-areas / build-candidates   splice N areas onto LIVE R2 bytes
  rollback-decide   per shared file restore/skip (refuse on third-party bytes)
                    plus the exact 2N keys to delete
  remove-areas / derive-remove-areas / build-remove-candidates / rollback-decide-remove
                    UNLIST superseded Danish areas (rows leave the 3 shared files; the
                    2N per-area objects are kept, never deleted); Denmark-only opt-in
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
#   superseded                                 live area being removed (remove-areas / sync-dk-to-r2 *-remove); required
HOLD_REASONS = {
    "island", "np-nested", "name-collision", "merge-into-klosterhede",
    "duplicate-of-live", "needs-rescope", "merge-pending", "mtb-centre",
    "curated-do-not-ship",
    "superseded",
}
# Curated per-area exclusions (registry `exclude_trails`, dk-v2 only); same shape as
# trailforge/assemble/quality.py validate_dk_exclude: {"id": "r<relation>" | "n:<name>", "name", "reason"}.
EXCLUDE_ID = re.compile(r"^(r[1-9][0-9]*|n:\S.*)$")
# exclude_trails only: "n*:<name>" excludes every trail with that exact name (>= 1 match).
EXCLUDE_ALL_ID = re.compile(r"^n\*:\S.*$")
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
def _dk_names():
    """trailforge/assemble/dk_names.py (the one definition of 'generic name' and of a valid
    prefix), loaded by path so the assemble directory is not put on sys.path."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "dk_names", ROOT / "trailforge" / "assemble" / "dk_names.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

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
        multi = bool(EXCLUDE_ALL_ID.fullmatch(ident))
        if multi and field not in ("exclude_trails", "legacy_exclude_trails"):
            raise DkError(f"{aid}: {field} {ident!r}: the n*: selector is exclude_trails-only")
        if (ident != unicodedata.normalize("NFC", ident) or name != unicodedata.normalize("NFC", name)
                or not (multi or EXCLUDE_ID.fullmatch(ident))):
            raise DkError(f"{aid}: {field} id/name is invalid: {ident!r}")
        if (ident.startswith("n:") and ident[2:] != name) or (multi and ident[3:] != name):
            raise DkError(f"{aid}: {field} {ident!r}: name must equal the id name")
        if ident in seen:
            raise DkError(f"{aid}: {field} repeats {ident!r}")
        seen.add(ident)
def _dk_quality():
    """trailforge/assemble/quality.py (the one definition of a valid accept_gap list), loaded
    by path like _dk_names. Its top-level imports are stdlib only."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "dk_quality_rules", ROOT / "trailforge" / "assemble" / "quality.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
def _check_gap_and_roles(aid: str, entry: dict[str, Any]) -> None:
    """dk-v3-only opt-ins: accept_gap (per-relation wide-gap acceptance) and
    accept_directional_roles (forward/backward hiking-relation members)."""
    if "accept_gap" in entry:
        if entry.get("rules") != "dk-v3":
            raise DkError(f"{aid}: accept_gap requires rules dk-v3")
        items = entry["accept_gap"]
        if not isinstance(items, list) or not items:
            raise DkError(f"{aid}: accept_gap must be a non-empty list when present")
        try:
            _dk_quality().validate_dk_accept_gap(items)
        except ValueError as error:
            raise DkError(f"{aid}: accept_gap is invalid: {error}") from None
    if "accept_directional_roles" in entry:
        if entry.get("rules") != "dk-v3":
            raise DkError(f"{aid}: accept_directional_roles requires rules dk-v3")
        if entry["accept_directional_roles"] is not True:
            raise DkError(f"{aid}: accept_directional_roles must be true when present")
def _dk_areas():
    """trailforge/assemble/areas.py (the one definition of the polygons a container_by_id
    entry may name), loaded by path like _dk_names. Its top-level imports are stdlib only."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "dk_area_rules", ROOT / "trailforge" / "assemble" / "areas.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
def _check_boundary_by_id(aid: str, entry: dict[str, Any]) -> None:
    """dk-v3-only boundary opt-ins. boundary_by_id: true picks, among several same-name OSM
    boundaries, the one with the entry's relation_id/way_id. container_by_id: {"reason"}
    names an existing unnamed/other-named forest polygon as the container; the trails then
    come from include_trails only."""
    for field in ("boundary_by_id", "container_by_id", "container_from_routes"):
        if field in entry and entry.get("rules") != "dk-v3":
            raise DkError(f"{aid}: {field} requires rules dk-v3")
    if "container_from_routes" in entry:
        _check_container_from_routes(aid, entry)
    if "boundary_by_id" in entry and entry["boundary_by_id"] is not True:
        raise DkError(f"{aid}: boundary_by_id must be true when present")
    if "container_by_id" in entry:
        item = entry["container_by_id"]
        if (not isinstance(item, dict) or set(item) != {"reason"}
                or not isinstance(item["reason"], str) or not item["reason"].strip()):
            raise DkError(f"{aid}: container_by_id must be exactly {{\"reason\": text}} when present")
        if not entry.get("include_trails"):
            raise DkError(f"{aid}: container_by_id needs a non-empty include_trails "
                          "(the container yields no trails of its own)")
        if "boundary_by_id" in entry:
            raise DkError(f"{aid}: boundary_by_id and container_by_id are mutually exclusive")
def _check_allow_rename(aid: str, entry: dict[str, Any], name: str) -> None:
    """Opt-in for an update-mode display-name change: {"from": <exact live name>, "reason": text}.
    The new name is the entry's display_name, so display_name is required."""
    if "allow_rename" not in entry:
        return
    item = entry["allow_rename"]
    if not isinstance(item, dict) or set(item) != {"from", "reason"}:
        raise DkError(f"{aid}: allow_rename needs exactly from and reason")
    for key in ("from", "reason"):
        if not isinstance(item[key], str) or not item[key].strip():
            raise DkError(f"{aid}: allow_rename.{key} must be non-empty text")
    if item["from"] != unicodedata.normalize("NFC", item["from"]):
        raise DkError(f"{aid}: allow_rename.from must be NFC text")
    if entry.get("display_name") is None:
        raise DkError(f"{aid}: allow_rename needs a display_name (the new name)")
    if item["from"] == entry["display_name"]:
        raise DkError(f"{aid}: allow_rename.from equals display_name; nothing to rename")
def _check_container_from_routes(aid: str, entry: dict[str, Any]) -> None:
    """dk-v3 container_from_routes: {"reason"}. For an area with NO usable OSM polygon; the
    container is derived from the whole geometry of its include_trails routes (see
    areas.route_cluster_container). Needs at least two distinct r<id> includes, the registry
    relation_id is the anchor (the first include), the name is the explicit area name, and no
    other container opt-in, way_id or exclude_trails (the trails are exactly the includes)."""
    item = entry["container_from_routes"]
    if (not isinstance(item, dict) or set(item) != {"reason"}
            or not isinstance(item["reason"], str) or not item["reason"].strip()):
        raise DkError(f"{aid}: container_from_routes must be exactly {{\"reason\": text}} when present")
    if "boundary_by_id" in entry or "container_by_id" in entry:
        raise DkError(f"{aid}: container_from_routes is exclusive with boundary_by_id/container_by_id")
    if "way_id" in entry:
        raise DkError(f"{aid}: container_from_routes needs relation_id (the anchor route), not way_id")
    if entry.get("exclude_trails"):
        raise DkError(f"{aid}: container_from_routes publishes exactly its include_trails; "
                      "exclude_trails is not allowed")
    selectors = [i["id"] for i in entry.get("include_trails") or []]
    areas_mod = _dk_areas()
    if (len(selectors) < areas_mod.ROUTE_CLUSTER_MIN_ROUTES
            or not all(re.fullmatch(r"r[1-9][0-9]*", i) for i in selectors)
            or len(set(selectors)) != len(selectors)):
        raise DkError(f"{aid}: container_from_routes needs at least "
                      f"{areas_mod.ROUTE_CLUSTER_MIN_ROUTES} distinct r<id> include_trails")
    if entry.get("relation_id") != int(selectors[0][1:]):
        raise DkError(f"{aid}: container_from_routes relation_id must be the first include_trails route "
                      f"({selectors[0]})")
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
    ways: set[int] = set()
    for entry in areas:
        if not isinstance(entry, dict):
            raise DkError("registry entry is not an object")
        aid, name, rel = entry.get("id"), entry.get("name"), entry.get("relation_id")
        way = entry.get("way_id")
        if not isinstance(aid, str) or not SLUG.fullmatch(aid):
            raise DkError(f"registry id is invalid: {aid!r}")
        if aid in ids:
            raise DkError(f"registry repeats id {aid}")
        if (not isinstance(name, str) or not name.strip()
                or name != unicodedata.normalize("NFC", name)):
            raise DkError(f"{aid}: name is invalid")
        if entry.get("state") != STATE:
            raise DkError(f"{aid}: state must be {STATE!r}")
        if "way_id" in entry:
            # Way-backed boundary (closed OSM way): exactly one of relation_id / way_id,
            # and never the legacy Mols control rule set.
            if "relation_id" in entry:
                raise DkError(f"{aid}: exactly one of relation_id and way_id is allowed")
            if not isinstance(way, int) or isinstance(way, bool) or way <= 0 or way in ways:
                raise DkError(f"{aid}: way_id is invalid or repeated")
            if entry.get("rules") not in ("dk-v2", "dk-v3"):
                raise DkError(f"{aid}: a way-backed area needs rules dk-v2 or dk-v3")
        elif not isinstance(rel, int) or isinstance(rel, bool) or rel <= 0 or rel in rels:
            raise DkError(f"{aid}: relation_id is invalid or repeated")
        shown = entry.get("display_name")
        if shown is not None and (not isinstance(shown, str) or not shown.strip() or shown == name
                                  or shown != unicodedata.normalize("NFC", shown)):
            raise DkError(f"{aid}: display_name must be a non-empty NFC text different from name")
        _check_allow_rename(aid, entry, name)
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
        if "allow_update" in entry and not isinstance(entry["allow_update"], bool):
            raise DkError(f"{aid}: allow_update must be true or false")
        hold = entry.get("hold")
        if hold is not None and (not isinstance(hold, str) or not hold.strip()):
            raise DkError(f"{aid}: hold must be null or a reason string")
        if hold is not None and hold not in HOLD_REASONS:
            raise DkError(f"{aid}: hold {hold!r} is not one of {sorted(HOLD_REASONS)}")
        _check_exclude_trails(aid, entry)
        # Explicit opt-in: a legacy-rules area (Mols) may exclude named trails; same entry shape.
        _check_exclude_trails(aid, entry, "legacy_exclude_trails", ("legacy",))
        _check_exclude_trails(aid, entry, "include_trails", ("dk-v3",))
        _check_gap_and_roles(aid, entry)
        _check_boundary_by_id(aid, entry)
        if "trail_name_prefix" in entry:
            if entry.get("rules") != "dk-v3":
                raise DkError(f"{aid}: trail_name_prefix requires rules dk-v3")
            try:
                _dk_names().validate_prefix(entry["trail_name_prefix"])
            except ValueError as error:
                raise DkError(f"{aid}: trail_name_prefix is invalid: {error}") from None
        est_mi = entry.get("est_mi")
        if est_mi is not None and (not _is_num(est_mi) or est_mi < 0):
            raise DkError(f"{aid}: est_mi must be a non-negative number")
        est_trails = entry.get("est_trails")
        if est_trails is not None and (not isinstance(est_trails, int) or isinstance(est_trails, bool)
                                       or est_trails < 0):
            raise DkError(f"{aid}: est_trails must be a non-negative integer")
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


def update_refusal(entry: dict[str, Any]) -> str | None:
    """Why this registry entry may not be republished in update mode (None = allowed).
    Mols and every legacy-rules entry go through the hardened Mols path unless the
    registry explicitly sets allow_update: true."""
    if ((entry["id"] == contract.MOLS_AREA_ID or entry.get("rules", "legacy") == "legacy")
            and entry.get("allow_update") is not True):
        return f"{entry['id']} is a legacy/Mols entry; update needs an explicit allow_update: true in the registry"
    return None


def check_update_eligible(registry: dict[str, Any], expected: dict[str, Any], published: set[str],
                          ids: list[str]) -> None:
    for aid in ids:
        if aid not in registry["by_id"]:
            raise DkError(f"update: {aid} is not in the registry")
        if aid not in published:
            raise DkError(f"update: {aid} is not in public/areas/index.json (update republishes existing "
                          "areas only; new areas go through mode=publish)")
        if aid not in expected:
            raise DkError(f"update: {aid} is not in the blessed expected table (re-bless it first)")
        reason = update_refusal(registry["by_id"][aid])
        if reason:
            raise DkError("update: " + reason)


def select_areas(registry: dict[str, Any], published: set[str], areas: str, chunk_size: int,
                 mode: str, expected: dict[str, Any] | None = None) -> list[str]:
    if mode not in ("qualify", "publish", "update"):
        raise DkError("mode must be qualify, publish or update")
    if mode == "update" and areas.startswith("batch="):
        raise DkError("update takes an explicit id list, not batch=")
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
    if mode == "update":
        check_update_eligible(registry, expected if expected is not None else load_expected(),
                              published, chosen)
    return chosen


def select_cmd(args: argparse.Namespace) -> int:
    registry = load_registry(args.registry)
    expected = load_expected(args.expected) if args.mode == "update" else None
    chosen = select_areas(registry, _index_ids(args.index), args.areas, int(args.chunk_size), args.mode,
                          expected)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(chosen) + "\n", encoding="utf-8")
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as stream:
            ids = [f"w{registry['by_id'][i]['way_id']}" if "way_id" in registry["by_id"][i]
                   else f"r{registry['by_id'][i]['relation_id']}" for i in chosen]
            # dk-v3 include_trails relations ride along so prepare-areas can size the window
            # to reach them (no include_trails: the list is unchanged).
            for i in chosen:
                ids += [f"r{r}" for r in include_relation_ids(registry["by_id"][i])]
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


# A registry entry without a discovery candidate (forests, plantations, heaths,
# islands: discovery lists protected areas only) is cross-checked against its own
# OSM relation instead of the discovery centre.
REL_TYPES = {"boundary", "multipolygon"}
REL_NAME_KEYS = ("name", "name:da", "official_name", "alt_name", "short_name")
REL_SPAN_MIN, REL_SPAN_MAX = 0.01, 1.0     # degrees, per axis (about 1 km .. 110 km)
# container_by_id only: a real forest polygon is accepted down to about half a kilometre per axis.
CONTAINER_SPAN_MIN = 0.005
DK_LON, DK_LAT = (6.0, 16.0), (54.0, 58.0)


def relation_problems(entry: dict[str, Any], info: dict[str, Any] | None) -> list[str]:
    """Fail-closed identity/size check of a registry entry against its own PBF relation."""
    if not info or info.get("tags") is None:
        return [f"registry relation {entry['relation_id']} is not in the PBF"]
    problems: list[str] = []
    if info.get("count") != 1:
        problems.append(f"registry relation {entry['relation_id']} occurs {info.get('count')} times (not unique)")
    tags = info["tags"]
    if "container_from_routes" in entry:
        # The anchor is a hiking route, not a polygon: route checks and the cluster size
        # are made on every include by cluster_problems.
        return problems
    if tags.get("type") not in REL_TYPES:
        problems.append(f"relation {entry['relation_id']} has type={tags.get('type')!r}, expected boundary/multipolygon")
    container = "container_by_id" in entry
    names = {unicodedata.normalize("NFC", tags[k]) for k in REL_NAME_KEYS if tags.get(k)}
    if container:
        # The polygon only has to be a real forest / reserve; its own name (or none) is free.
        if not any(tags.get(k) in v for k, v in _dk_areas().DK_CONTAINER_TAGS.items()):
            problems.append(f"relation {entry['relation_id']} is not a forest, wood, nature reserve or "
                            "protected-area polygon (container_by_id)")
    elif entry["name"] not in names:
        problems.append(f"relation {entry['relation_id']} name {sorted(names)} does not match registry name {entry['name']!r}")
    box = info.get("bbox")
    if box is not None:
        span_min = CONTAINER_SPAN_MIN if container else REL_SPAN_MIN
        w, h = box[2] - box[0], box[3] - box[1]
        if not (span_min <= w <= REL_SPAN_MAX and span_min <= h <= REL_SPAN_MAX):
            problems.append(f"boundary size {w:.3f} x {h:.3f} degrees is implausible "
                            f"(each side must be {span_min}..{REL_SPAN_MAX})")
        if not (DK_LON[0] <= box[0] and box[2] <= DK_LON[1] and DK_LAT[0] <= box[1] and box[3] <= DK_LAT[1]):
            problems.append(f"boundary bbox {box} is outside Denmark")
    return problems


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
    tags = info["tags"]
    container = "container_by_id" in entry
    names = {unicodedata.normalize("NFC", tags[k]) for k in REL_NAME_KEYS if tags.get(k)}
    if container:
        # The polygon only has to be a real forest / reserve; its own name (or none) is free.
        if not any(tags.get(k) in v for k, v in _dk_areas().DK_CONTAINER_TAGS.items()):
            problems.append(f"way {entry['way_id']} is not a forest, wood, nature reserve or "
                            "protected-area polygon (container_by_id)")
    elif entry["name"] not in names:
        problems.append(f"way {entry['way_id']} name {sorted(names)} does not match registry name {entry['name']!r}")
    box = info.get("bbox")
    if box is not None:
        span_min = CONTAINER_SPAN_MIN if container else REL_SPAN_MIN
        w, h = box[2] - box[0], box[3] - box[1]
        if not (span_min <= w <= REL_SPAN_MAX and span_min <= h <= REL_SPAN_MAX):
            problems.append(f"boundary size {w:.3f} x {h:.3f} degrees is implausible "
                            f"(each side must be {span_min}..{REL_SPAN_MAX})")
        if not (DK_LON[0] <= box[0] and box[2] <= DK_LON[1] and DK_LAT[0] <= box[1] and box[3] <= DK_LAT[1]):
            problems.append(f"boundary bbox {box} is outside Denmark")
    return problems
def cluster_problems(entry: dict[str, Any], boundary: list[float] | None,
                     infos: dict[int, dict[str, Any]]) -> list[str]:
    """Fail-closed checks of a container_from_routes entry against the PBF: every include is
    present once as a hiking/foot route relation with geometry, the union (``boundary``) is
    inside Denmark and within the cluster size cap. Same limits as areas.route_cluster_container."""
    rules = _dk_areas()
    problems: list[str] = []
    for rid, info in sorted(infos.items()):
        tags = (info or {}).get("tags")
        if tags is None:
            problems.append(f"include route relation {rid} is not in the PBF")
            continue
        if info.get("count") != 1:
            problems.append(f"include route relation {rid} occurs {info.get('count')} times (not unique)")
        if tags.get("type") != "route" or (tags.get("route") or "").strip().lower() \
                not in rules.ROUTE_CLUSTER_ROUTE_KINDS:
            problems.append(f"include relation {rid} is type={tags.get('type')!r} route={tags.get('route')!r}, "
                            "expected a hiking/foot route")
        if info.get("bbox") is None:
            problems.append(f"include route relation {rid} has no member way geometry")
    if boundary is not None:
        w, h = boundary[2] - boundary[0], boundary[3] - boundary[1]
        if w > rules.ROUTE_CLUSTER_MAX_SPAN_DEG or h > rules.ROUTE_CLUSTER_MAX_SPAN_DEG:
            problems.append(f"route cluster is {w:.3f} x {h:.3f} degrees, over the "
                            f"{rules.ROUTE_CLUSTER_MAX_SPAN_DEG} cap per axis")
        if not (DK_LON[0] <= boundary[0] and boundary[2] <= DK_LON[1]
                and DK_LAT[0] <= boundary[1] and boundary[3] <= DK_LAT[1]):
            problems.append(f"route cluster bbox {boundary} is outside Denmark")
    return problems
def include_relation_ids(entry: dict[str, Any]) -> list[int]:
    """Relation ids of a dk-v3 area's include_trails (r<id> selectors only); [] otherwise."""
    if entry.get("rules") != "dk-v3":
        return []
    return [int(i["id"][1:]) for i in entry.get("include_trails") or [] if i["id"].startswith("r")]
def union_bbox(boxes: list[list[float]]) -> list[float]:
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]
def pad_bbox(box: list[float]) -> list[float]:
    return [round(box[0] - PAD, 2), round(box[1] - PAD, 2), round(box[2] + PAD, 2), round(box[3] + PAD, 2)]


def center_of(box: list[float]) -> list[float]:
    return [round((box[1] + box[3]) / 2, 1), round((box[0] + box[2]) / 2, 1)]


def check_area_geometry(entry: dict[str, Any], boundary: list[float] | None, discovery_center: Any,
                        mode: str, relation_info: dict[str, Any] | None = None,
                        include_boxes: list[list[float]] | None = None,
                        cluster_infos: dict[int, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Derive/check one area's bbox + centre. Returns the per-area prepare record.
    include_boxes (dk-v3 include_trails relation bboxes found in the PBF) widen only the
    ceiling of the margin check and the proposed bbox: the window must be able to reach the
    included routes. MARGIN_MIN/MARGIN_MAX and the centre-in-boundary rule are unchanged."""
    problems: list[str] = []
    way_backed = "way_id" in entry
    rec: dict[str, Any] = {"id": entry["id"], "name": entry["name"],
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
    reach = union_bbox([boundary, *include_boxes]) if include_boxes else boundary
    if reg is not None:
        # A blessed bbox keeps every existing record byte-identical: the include reach is
        # consulted only when the boundary alone would reject it (a bbox widened for includes).
        within = [boundary[0] - reg[0], boundary[1] - reg[1], reg[2] - boundary[2], reg[3] - boundary[3]]
        if all(MARGIN_MIN - 1e-9 <= m <= MARGIN_MAX + 1e-9 for m in within):
            reach = boundary
    padded, centre = pad_bbox(reach if reg is None else boundary), center_of(boundary)
    if reach != boundary:
        rec["include_reach_bbox"] = [round(v, 6) for v in reach]
    rec["proposed_bbox"], rec["proposed_center"] = padded, centre
    if way_backed:
        # The discovery step lists relations; a way-backed area is always cross-checked
        # against its own PBF way (identity, closed ring, name, size).
        rec["cross_check"] = "registry-way"
        problems += way_problems(entry, relation_info)
    elif isinstance(discovery_center, list) and len(discovery_center) == 2 and cluster_infos is None:
        mid = [(boundary[1] + boundary[3]) / 2, (boundary[0] + boundary[2]) / 2]
        if abs(mid[0] - discovery_center[0]) > 0.05 or abs(mid[1] - discovery_center[1]) > 0.05:
            problems.append(f"boundary bbox midpoint {mid} disagrees with the discovery centre {discovery_center}")
        rec["cross_check"] = "discovery"
    else:
        # No discovery entry for this area: the boundary-derived centre/bbox stands, but only
        # after the registry's own relation (id, type, name, size) checks out.
        rec["cross_check"] = "registry-relation"
        problems += relation_problems(entry, relation_info)
        if cluster_infos is not None:
            # container_from_routes: ``boundary`` is the union bbox of the include routes.
            rec["container"] = "route-cluster"
            problems += cluster_problems(entry, boundary, cluster_infos)
    if reg is None:
        if mode == "publish":
            problems.append("registry bbox/center missing: run qualify and commit the proposed values first")
        rec.update(bbox_source="computed", effective_bbox=padded, center=centre)
    else:
        margins = [boundary[0] - reg[0], boundary[1] - reg[1], reg[2] - boundary[2], reg[3] - boundary[3]]
        rec["registry_margins"] = [round(m, 4) for m in margins]
        reach_margins = [reach[0] - reg[0], reach[1] - reg[1], reg[2] - reach[2], reg[3] - reach[3]]
        if (any(m < MARGIN_MIN - 1e-9 for m in margins)
                or any(m > MARGIN_MAX + 1e-9 for m in reach_margins)):
            problems.append(f"registry bbox margins {rec['registry_margins']} outside "
                            f"[{MARGIN_MIN}, {MARGIN_MAX}] of the boundary bbox {boundary}"
                            + (f" (max measured from the include_trails reach {rec['include_reach_bbox']})"
                               if reach != boundary else ""))
        if entry.get("rules") == "dk-v3":
            # dk-v3 only: the pin may sit on the trails instead of the rounded boundary midpoint,
            # but it must stay inside the boundary bbox (the trail-centroid check runs at publish).
            c = entry["center"]
            if not (boundary[1] <= c[0] <= boundary[3] and boundary[0] <= c[1] <= boundary[2]):
                problems.append(f"registry center {entry['center']} is outside the boundary bbox {boundary}")
        elif [round(v, 6) for v in entry["center"]] != centre:
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
    rel_entries = [e for e in entries if "way_id" not in e]
    way_entries = [e for e in entries if "way_id" in e]
    inc_ids = sorted({r for e in entries for r in include_relation_ids(e)})
    infos = boundary_info(args.bounds_pbf, [e["relation_id"] for e in rel_entries] + inc_ids)
    way_infos = (way_boundary_info(args.bounds_pbf, [e["way_id"] for e in way_entries])
                 if way_entries else {})
    out: list[dict[str, Any]] = []
    for entry in entries:
        cand = by_disc.get(entry["id"])
        disc_center = cand["index_row"][3:5] if cand else None
        info = way_infos[entry["way_id"]] if "way_id" in entry else infos[entry["relation_id"]]
        inc_boxes = [b for r in include_relation_ids(entry)
                     if (b := (infos.get(r) or {}).get("bbox")) is not None]
        boundary, cluster_infos = info["bbox"], None
        if "container_from_routes" in entry:
            cluster_infos = {r: infos.get(r) or {} for r in include_relation_ids(entry)}
            boxes = [i.get("bbox") for i in cluster_infos.values()]
            boundary = union_bbox(boxes) if boxes and all(b is not None for b in boxes) else None
        rec = check_area_geometry(entry, boundary, disc_center, args.mode, info, inc_boxes, cluster_infos)
        if rec["status"] == "ok":
            rec["bbox_arg"] = bbox_arg(rec["effective_bbox"])
            rec["display_name"] = entry.get("display_name") or entry["name"]
            row = [entry["id"], rec["display_name"], STATE, rec["center"][0], rec["center"][1], None, None]
            if "way_id" not in entry:
                # Way-backed rows stay 7 long (the field is ABSENT, not null): see
                # check_new_row and publish_areas.py's note on iOS decoding.
                row.append(entry["relation_id"])
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
# ----------------------------------------------- cross-area duplicate gate --
# A route (relation id, or identical geometry, or the same name over the same ground)
# must not be listed whole in two areas: the selected set and the live Danish areas.
DUP_NODE_SAMPLE = 4          # keep nodes whose hash % 4 == 0 (same rule on both sides)
DUP_NAME_OVERLAP = 0.5       # same normalized name + this share of the smaller sample
DUP_MIN_SAMPLE = 5
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
def live_conflicts(area_id: str, identities: list[dict[str, Any]], live_dir: Path) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if not live_dir.is_dir():
        return found
    for path in sorted(live_dir.glob("*-dk.json")):
        live = _read_json(path, "live geom")
        if not isinstance(live, dict) or live.get("id") == area_id:
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
                    if shared and ta.get("whole") and tb.get("whole"):
                        why = "relation " + ", ".join(f"r{r}" for r in shared) + " whole in both"
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
def _trail_relations(features: list[dict[str, Any]], name: Any) -> tuple[list[int], bool]:
    rels: set[int] = set()
    whole = False
    for p in features:
        if p.get("name") == name and p.get("kind") != "poi":
            rels.update(int(r) for r in (p.get("relation_ids") or []) if isinstance(r, int))
            whole = whole or bool(p.get("dk_whole_route"))
    return sorted(rels), whole
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
    # A dk-v3 name prefix fixes search ambiguity, not a list made of colour routes: the
    # generic-share gate keeps judging the OSM names.
    prefix_block = assembly.get("name_prefix") or {}
    osm_of = {r["name"]: r["osm_name"] for r in prefix_block.get("renamed") or []}
    generic = [n for n in names if is_generic_trail_name(osm_of.get(n, n))]
    generic_share = round(len(generic) / len(names), 3) if names else 0.0
    rec = {
        "id": area["id"], "name": area["name"],
        **({"way_id": area["way_id"]} if "way_id" in area else {"relation_id": area["relation_id"]}),
        "status": "ok",
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
    identities = []
    for t in geom.get("trails") or []:
        ident = trail_identity(t)
        ident["relation_ids"], ident["whole"] = _trail_relations(kept, t.get("name"))
        identities.append(ident)
    if prefix_block:
        rec["name_prefix"] = {"prefix": prefix_block.get("prefix"), "renamed": len(osm_of)}
    rec["trail_identities"] = identities
    rec["live_duplicates"] = live_conflicts(area["id"], identities, args.live_geom_dir)
    # dk-v3 only: routes published whole (key absent for dk-v2/legacy runs).
    whole = [{"name": p.get("name"), "length_mi": p.get("length_mi"),
              "area_share": p.get("dk_route_share")}
             for p in kept if p.get("dk_whole_route")]
    if whole:
        rec["whole_routes"] = sorted(whole, key=lambda item: str(item["name"]))
    # dk-v3 registry accept_gap only: routes published as connected parts across a source gap
    # wider than 100 m (key absent otherwise, so other reports are unchanged).
    accepted = (assembly.get("quality") or {}).get("dk_accept_gap")
    if accepted:
        rec["accepted_gaps"] = accepted
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
                  *([f"accepted gaps (registry accept_gap): {[(x['relation'], x['name'], x['parts'], x['widest_gap_m'], x['max_gap_m']) for x in r['accepted_gaps']]}"]
                    if r.get("accepted_gaps") else []),
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
    if "way_id" in entry:
        # Way-backed: no relation id exists, so the row has no 8th element at all.
        id_ok = len(row) == 7
    else:
        id_ok = len(row) == 8 and row[7] == entry["relation_id"]
    if (not id_ok or row[0] != entry["id"] or row[1] != (entry.get("display_name") or entry["name"])
            or row[2] != STATE):
        raise DkError(f"{context}: {entry['id']} row has the wrong identity")
    if entry.get("center") is None or [row[3], row[4]] != entry["center"]:
        raise DkError(f"{context}: {entry['id']} row centre differs from the registry")
    if entry.get("rules") == "dk-v3":
        pts = [pt for t in geom.get("trails") or [] for seg in t.get("segments") or [] for pt in seg]
        if pts:
            mid = ((min(p[0] for p in pts) + max(p[0] for p in pts)) / 2,
                   (min(p[1] for p in pts) + max(p[1] for p in pts)) / 2)
            if abs(mid[0] - entry["center"][0]) > 0.1 or abs(mid[1] - entry["center"][1]) > 0.1:
                raise DkError(f"{context}: {entry['id']} centre is more than 0.1 deg from the trails")
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
    staged = args.command == "verify-staged"
    if args.mode == "update":
        if len(ids) > MAX_UPDATE_AREAS:
            raise DkError(f"update is capped at {MAX_UPDATE_AREAS} areas")
        summary = _verify_update_delta(args.repo.resolve(), ids, staged=staged)
    elif args.mode == "remove":
        summary = _verify_remove_delta(args.repo.resolve(), ids, staged=staged)
    else:
        summary = _verify_delta(args.repo.resolve(), ids, staged=staged)
    summary["receipt_sha256"] = contract.canonical_sha256(summary)
    if args.output:
        _write_json(args.output, summary)
    print(f"verified {args.mode} delta for {len(ids)} area(s) ({len(summary.get('allowed_paths', []))} allowed paths) "
          f"sha256={summary['receipt_sha256']}")
    return 0


# ------------------------------------------------------------ UPDATE mode ----
# Republish EXISTING Danish areas. Everything above (append-only publish, N-area
# sync) is untouched; these helpers only ever REPLACE the records of the listed
# areas in place and prove every other record is byte-identical.

MAX_UPDATE_AREAS = HARD_MAX_CHUNK


def _split_records(live: bytes, as_dict: bool, context: str, sep: str = ",") -> list[tuple[Any, str]]:
    """Top-level records of a JSON array/object WITH their exact raw text, so records
    can be kept or replaced byte-for-byte. Refuses any framing it cannot reproduce
    exactly (re-joined pieces must equal the input)."""
    try:
        text = live.decode("utf-8")
    except UnicodeDecodeError as error:
        raise DkError(f"{context} is not UTF-8") from error
    opener, closer = ("{", "}") if as_dict else ("[", "]")
    if len(text) < 2 or not text.startswith(opener) or not text.endswith(closer):
        raise DkError(f"{context} has unexpected framing")
    decoder = json.JSONDecoder()
    end = len(text) - 1
    pos = 1
    out: list[tuple[Any, str]] = []
    while pos < end:
        start = pos
        if as_dict:
            key, pos = decoder.raw_decode(text, pos)
            if not isinstance(key, str) or text[pos:pos + 1] != ":":
                raise DkError(f"{context} has an unexpected object entry")
            value, pos = decoder.raw_decode(text, pos + 1)
            rec: Any = [key, value]
        else:
            rec, pos = decoder.raw_decode(text, pos)
        out.append((rec, text[start:pos]))
        if pos == end:
            break
        if not text.startswith(sep, pos):
            raise DkError(f"{context} has unexpected record separators")
        pos += len(sep)
    if opener + sep.join(raw for _, raw in out) + closer != text:
        raise DkError(f"{context} framing cannot be reproduced exactly")
    return out


def _row_identity_kept(old: list[Any], new: list[Any], aid: str, entry: dict[str, Any] | None = None) -> None:
    """Update mode keeps the index row identity (id, name, state, relation/way). The one exception is
    an explicit registry opt-in `allow_rename: {from, reason}`: the live name must equal `from` exactly
    and the new name must be the entry's display_name; id, state and relation stay equal. A row that
    already carries the new name passes (idempotent re-run); anything else is refused."""
    if (len(old) not in (7, 8) or len(old) != len(new)
            or (old[0], old[2], *old[7:]) != (new[0], new[2], *new[7:])):
        raise DkError(f"{aid}: update must keep the row identity (id, name, state, relation)")
    if old[1] == new[1]:
        return
    rename = (entry or {}).get("allow_rename")
    if rename is None:
        raise DkError(f"{aid}: update must keep the row identity (id, name, state, relation)")
    if old[1] != rename["from"] or new[1] != entry.get("display_name"):
        raise DkError(f"{aid}: allow_rename needs the live name {rename['from']!r} and the registry "
                      f"display_name as the new name (live {old[1]!r}, new {new[1]!r})")
def replace_index(args: argparse.Namespace) -> int:
    """Replace ONLY the listed areas' rows in public/areas/index.json, in place."""
    registry = load_registry(args.registry)
    target = args.target
    if target.is_symlink() or not target.is_file():
        raise DkError("index target is missing or unsafe")
    before = target.read_bytes()
    _rows(before, "canonical master index", compact=False)
    pieces = _split_records(before, False, "canonical master index", sep=", ")
    ids = [i for i in args.selected.read_text(encoding="utf-8").split() if i]
    if not 1 <= len(ids) <= MAX_UPDATE_AREAS or len(set(ids)) != len(ids):
        raise DkError(f"update needs 1..{MAX_UPDATE_AREAS} unique areas")
    raws = [raw for _, raw in pieces]
    for aid in ids:
        src = _read_json(args.rows_dir / aid / "index.json", f"{aid} published row")
        if not isinstance(src, list) or len(src) != 1:
            raise DkError(f"{aid}: published index is not exactly one row")
        geom = _read_json(args.geom_dir / f"{aid}.json", f"{aid} geom")
        check_new_row(src[0], registry["by_id"][aid], geom, "published row")
        spots = [n for n, (row, _) in enumerate(pieces) if row[0] == aid]
        if len(spots) != 1:
            raise DkError(f"{aid} is not in the canonical master index; update never adds areas")
        _row_identity_kept(pieces[spots[0]][0], src[0], aid, registry["by_id"][aid])
        raws[spots[0]] = _dumps_default([src[0]])[1:-1].decode("utf-8")
    after = ("[" + ", ".join(raws) + "]").encode("utf-8")
    after_rows = _rows(after, "updated master index", compact=False)
    if [r[0] for r in after_rows] != [r[0] for r, _ in pieces]:
        raise DkError("row order changed")
    target.write_bytes(after)
    print(f"replaced {len(ids)} row(s) in place in {target}")
    return 0


def _verify_update_delta(repo: Path, ids: list[str], *, staged: bool) -> dict[str, Any]:
    registry = load_registry(repo / "trailforge/golden/dk-areas.json")
    names_cmd = ["diff", "--cached"] if staged else ["diff"]
    span = [] if staged else ["HEAD^", "HEAD"]

    def names(flt: str) -> list[str]:
        return [n for n in _git(repo, *names_cmd, "--name-only", f"--diff-filter={flt}", "-z", *span)
                .decode("utf-8").split("\0") if n]

    if names("D") or names("A") or names("R") or names("C") or names("T"):
        raise DkError("update diff contains an added, deleted, renamed or type-changed path")
    changed = names("M")
    allowed = set(area_paths(ids))
    if len(allowed) != 2 * len(ids) + 2 or set(changed) - allowed or len(changed) != len(set(changed)):
        raise DkError(f"update diff is outside the 2N+2 allowlist: {changed}")
    missing = [f"public/areas/geom/{a}.json" for a in ids if f"public/areas/geom/{a}.json" not in changed]
    if missing:
        raise DkError(f"update did not change these geoms: {missing}")
    index_changed = [INDEX_REPO in changed, BUNDLE_REPO in changed]
    if index_changed[0] != index_changed[1]:
        raise DkError("only one of the two index files changed")

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
        _rows(before, f"baseline {path}", compact)
        _rows(after, f"candidate {path}", compact)
        sep = "," if compact else ", "
        bp = _split_records(before, False, f"baseline {path}", sep)
        ap = _split_records(after, False, f"candidate {path}", sep)
        if [r[0] for r, _ in bp] != [r[0] for r, _ in ap]:
            raise DkError(f"{path}: row ids or order changed (update replaces rows in place)")
        dump = _dumps_compact if compact else _dumps_default
        replaced = []
        for (brow, braw), (arow, araw) in zip(bp, ap):
            if arow[0] in geoms:
                check_new_row(arow, registry["by_id"][arow[0]], geoms[arow[0]], path)
                _row_identity_kept(brow, arow, arow[0], registry["by_id"][arow[0]])
                if araw != dump([arow])[1:-1].decode("utf-8"):
                    raise DkError(f"{path}: {arow[0]} row framing differs from the producer")
                replaced.append(arow[0])
            elif araw != braw:
                raise DkError(f"{path}: row {arow[0]} is not one of the updated areas but its bytes changed")
        if sorted(replaced) != sorted(ids):
            raise DkError(f"{path}: an updated area has no row")
        transitions.append({"path": path, "baseline_sha256": _sha(before), "candidate_sha256": _sha(after),
                            "replaced_ids": sorted(replaced)})
    return {"schema": "trekdex-dk-update-delta-v1", "area_ids": ids, "allowed_paths": sorted(allowed),
            "changed_paths": changed, "index_transitions": transitions}


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


# ------------------------------------------------- R2 UPDATE (overwrite) ----

def _owner_search(rec: Any) -> Any:
    return rec[1] if isinstance(rec, list) and len(rec) > 1 else None


def _owner_shapes(rec: Any) -> Any:
    return rec[0].split("/", 1)[0] if isinstance(rec[0], str) and "/" in rec[0] else None


def replace_area_records(live: bytes, key: str, new_records: dict[str, list[Any]]) -> bytes:
    """LIVE bytes with each listed area's contiguous record block replaced IN PLACE by its
    new records; every other record keeps its exact live bytes and position."""
    as_dict = key == "trail-shapes.json"
    owner = _owner_shapes if as_dict else _owner_search
    pieces = _split_records(live, as_dict, f"live {key}")
    out: list[str] = []
    blocks: dict[int, str] = {}
    skip: set[int] = set()
    for aid, records in new_records.items():
        idx = [n for n, (rec, _) in enumerate(pieces) if owner(rec) == aid]
        if not idx or idx != list(range(idx[0], idx[-1] + 1)):
            raise DkError(f"live {key} has no single contiguous block for {aid}; refusing to update in place")
        if as_dict:
            parts = [json.dumps({r[0]: r[1]}, separators=(",", ":"))[1:-1] for r in records]
        else:
            parts = [json.dumps(r, separators=(",", ":"), ensure_ascii=True) for r in records]
        blocks[idx[0]] = ",".join(parts)
        skip.update(idx)
    for n, (_, raw) in enumerate(pieces):
        if n in blocks:
            out.append(blocks[n])
        elif n not in skip:
            out.append(raw)
    opener, closer = ("{", "}") if as_dict else ("[", "]")
    return (opener + ",".join(out) + closer).encode("utf-8")


def prove_update_delta(key: str, before: bytes, after: bytes, geoms: dict[str, bytes]) -> dict[str, Any]:
    """Only the listed areas' records changed (to the exact deterministic records), all in
    their original position; every other record is byte-identical."""
    as_dict = key == "trail-shapes.json"
    owner = _owner_shapes if as_dict else _owner_search
    strict = contract._strict_trail_shape_records if as_dict else contract._strict_trail_search_records
    base = strict(before, f"baseline {key}")
    cand = strict(after, f"candidate {key}")
    bp = _split_records(before, as_dict, f"baseline {key}")
    ap = _split_records(after, as_dict, f"candidate {key}")
    others_b = [raw for rec, raw in bp if owner(rec) not in geoms]
    others_a = [raw for rec, raw in ap if owner(rec) not in geoms]
    if others_b != others_a:
        raise DkError(f"candidate {key} changed a record outside the updated areas")
    # list-level cross-check: same positions, blocks swapped for the deterministic records
    expect: list[Any] = []
    done: set[str] = set()
    for rec in base:
        aid = owner(rec)
        if aid in geoms:
            if aid not in done:
                expect += expected_records(key, aid, geoms[aid])
                done.add(aid)
        else:
            expect.append(rec)
    if done != set(geoms):
        raise DkError(f"live {key} is missing records for an updated area")
    if cand != expect:
        raise DkError(f"candidate {key} is not live records with the exact deterministic replacements")
    old_n = sum(1 for r in base if owner(r) in geoms)
    new_n = sum(1 for r in cand if owner(r) in geoms)
    return {"key": key, "baseline_records": len(base), "candidate_records": len(cand),
            "replaced_records_old": old_n, "replaced_records_new": new_n,
            "other_records_byte_identical": len(others_b),
            "baseline_sha256": _sha(before), "candidate_sha256": _sha(after)}


def live_dk_ids(repo: Path, live_index: bytes) -> list[str]:
    registry = load_registry(repo / "trailforge/golden/dk-areas.json")
    return [r[0] for r in json.loads(live_index) if isinstance(r, list) and r and r[0] in registry["by_id"]]


def live_dk_ids_cmd(args: argparse.Namespace) -> int:
    ids = live_dk_ids(args.repo, args.live_index.read_bytes())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(ids) + "\n", encoding="utf-8")
    print(f"{len(ids)} Danish area(s) in the live index")
    return 0


def derive_update_ids(repo: Path, live_index: bytes, live_areas_dir: Path) -> list[str]:
    """The update set = Danish areas whose repo geom/silhouette (or index row) differ from
    the LIVE R2 objects. The repo bundle must list exactly the live areas in the same order
    (update never adds or removes an area)."""
    registry = load_registry(repo / "trailforge/golden/dk-areas.json")
    expected = load_expected(repo / "trailforge/golden/dk-areas-expected.json")
    live_rows = json.loads(live_index)
    bundle_rows = _rows((repo / BUNDLE_REPO).read_bytes(), "repo bundle index", compact=True)
    if [r[0] for r in live_rows] != [r[0] for r in bundle_rows]:
        raise DkError("repo bundle index lists different areas (or order) than the live index; "
                      "update replaces existing areas only, publish new ones first")
    changed: set[str] = set()
    for live_row, bundle_row in zip(live_rows, bundle_rows):
        if live_row != bundle_row:
            if bundle_row[0] not in registry["by_id"]:
                raise DkError(f"non-Danish index row {bundle_row[0]} differs from live; refusing")
            changed.add(bundle_row[0])
    for aid in live_dk_ids(repo, live_index):
        for rel, live_path in ((f"public/areas/geom/{aid}.json", live_areas_dir / f"{aid}.json"),
                               (f"public/areas/silhouettes/{aid}.json", live_areas_dir / "silhouettes" / f"{aid}.json")):
            if not live_path.is_file():
                raise DkError(f"live object for {rel} was not downloaded: the 2N keys must already exist")
            repo_path = repo / rel
            if not repo_path.is_file():
                raise DkError(f"{rel} is missing from the repo")
            if repo_path.read_bytes() != live_path.read_bytes():
                changed.add(aid)
    ids = sorted(changed)
    if not 1 <= len(ids) <= MAX_UPDATE_AREAS:
        raise DkError(f"{len(ids)} area(s) to update (need 1..{MAX_UPDATE_AREAS})")
    check_update_eligible(registry, expected, {r[0] for r in live_rows}, ids)
    return ids


def derive_update_areas_cmd(args: argparse.Namespace) -> int:
    ids = derive_update_ids(args.repo, args.live_index.read_bytes(), args.live_areas_dir)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(ids) + "\n", encoding="utf-8")
    print(f"{len(ids)} area(s) to update from the repo vs live R2: {','.join(ids)}")
    return 0


def build_update_candidates(args: argparse.Namespace) -> int:
    repo = args.repo
    registry = load_registry(repo / "trailforge/golden/dk-areas.json")
    expected = load_expected(repo / "trailforge/golden/dk-areas-expected.json")
    live = {k: Path(v).read_bytes() for k, v in (("index.json", args.live_index),
            ("trail-search.json", args.live_search), ("trail-shapes.json", args.live_shapes))}
    ids = derive_update_ids(repo, live["index.json"], args.live_areas_dir)
    if args.areas_file:
        listed = [i for i in args.areas_file.read_text(encoding="utf-8").split() if i]
        if listed != ids:
            raise DkError("areas file differs from the repo-vs-live derivation")
    geoms: dict[str, bytes] = {}
    sils: dict[str, bytes] = {}
    old: dict[str, bytes] = {}
    for aid in ids:
        geoms[aid] = (repo / f"public/areas/geom/{aid}.json").read_bytes()
        sils[aid] = (repo / f"public/areas/silhouettes/{aid}.json").read_bytes()
        _area_geom_trails(geoms[aid], aid)
        contract.strict_json_loads(sils[aid].decode("utf-8"), f"{aid} silhouette")
        problems = expected_problems(json.loads(geoms[aid]), aid, expected[aid])
        if problems:
            raise DkError(f"{aid} is not the blessed result: " + "; ".join(problems))
        old[f"{aid}.json"] = (args.live_areas_dir / f"{aid}.json").read_bytes()
        old[f"silhouettes/{aid}.json"] = (args.live_areas_dir / "silhouettes" / f"{aid}.json").read_bytes()
    # index.json: splice the new rows onto the LIVE bytes (live framing kept), then prove the
    # result is exactly the repo bundle's rows.
    bundle = (repo / BUNDLE_REPO).read_bytes()
    bundle_rows = _rows(bundle, "repo bundle index", compact=True)
    live_pieces = _split_records(live["index.json"], False, "live index.json")
    by_id = {r[0]: r for r in bundle_rows}
    raws = []
    for row, raw in live_pieces:
        if row[0] in geoms:
            new_row = by_id[row[0]]
            check_new_row(new_row, registry["by_id"][row[0]], json.loads(geoms[row[0]]), "repo bundle index")
            _row_identity_kept(row, new_row, row[0], registry["by_id"][row[0]])
            raws.append(_dumps_compact([new_row])[1:-1].decode("utf-8"))
        else:
            raws.append(raw)
    index_after = ("[" + ",".join(raws) + "]").encode("utf-8")
    if json.loads(index_after) != bundle_rows:
        raise DkError("candidate index.json rows differ from the repo bundle index")
    candidates: dict[str, bytes] = {"index.json": index_after}
    proofs = [{"key": "index.json", "baseline_sha256": _sha(live["index.json"]),
               "candidate_sha256": _sha(index_after), "replaced_ids": ids,
               "other_rows_byte_identical": len(live_pieces) - len(ids)}]
    for key in ("trail-search.json", "trail-shapes.json"):
        new_records = {aid: expected_records(key, aid, geoms[aid]) for aid in ids}
        after = replace_area_records(live[key], key, new_records)
        proofs.append(prove_update_delta(key, live[key], after, geoms))
        candidates[key] = after

    out = args.out_dir
    plan: dict[str, Any] = {"schema": "trekdex-dk-r2-update-plan-v1", "mode": "update", "areas": ids,
                            "writes": [], "baseline": {}, "old_objects": {}, "proofs": proofs}
    order: list[tuple[str, str, bytes]] = []
    order += [(f"{a}.json", f"geom/{a}.json", geoms[a]) for a in ids]
    order += [(f"silhouettes/{a}.json", f"sil/{a}.json", sils[a]) for a in ids]
    order += [("trail-shapes.json", "trail-shapes.json", candidates["trail-shapes.json"]),
              ("trail-search.json", "trail-search.json", candidates["trail-search.json"]),
              ("index.json", "index.json", candidates["index.json"])]
    keys = [k for k, _, _ in order]
    if len(keys) != 2 * len(ids) + 3 or len(set(keys)) != len(keys) or any("parking" in k for k in keys):
        raise DkError("write set is not exactly 2N area keys + the three shared files")
    for key, name, data in order:
        (out / name).parent.mkdir(parents=True, exist_ok=True)
        (out / name).write_bytes(data)
        plan["writes"].append({"key": key, "file": name, "bytes": len(data), "sha256": _sha(data)})
    for key, data in live.items():
        plan["baseline"][key] = {"bytes": len(data), "sha256": _sha(data)}
    for key, data in old.items():
        plan["old_objects"][key] = {"file": f"areas/{key}", "bytes": len(data), "sha256": _sha(data)}
    _write_json(out / "plan.json", plan)
    print(json.dumps({"areas": ids, "writes": len(plan["writes"])}, indent=2))
    return 0


def rollback_update_actions(plan: dict[str, Any], live_dir: Path) -> list[tuple[str, str]]:
    """Per overwritten object: 'restore' when live is the applied candidate, 'skip' when it
    is already the old bytes, REFUSE for anything else (a third party wrote it).
    Shared files first (index first so the app stops listing new data), then per-area."""
    if plan.get("schema") != "trekdex-dk-r2-update-plan-v1":
        raise DkError("plan is not an update plan")
    actions: list[tuple[str, str]] = []
    shared = smp.rollback_actions(plan, live_dir)
    actions += [(key, shared[key]) for key in SHARED_KEYS]
    cand = {w["key"]: w["sha256"] for w in plan["writes"]}
    for key, old in plan["old_objects"].items():
        path = live_dir / key
        if not path.is_file():
            raise DkError(f"live {key} is missing; an update never deletes, refusing to roll back over it")
        now = _sha(path.read_bytes())
        if now == cand[key] and now != old["sha256"]:
            actions.append((key, "restore"))
        elif now == old["sha256"]:
            actions.append((key, "skip"))
        else:
            raise DkError(f"live {key} is neither the applied candidate nor the old bytes; refusing to roll back over it")
    return actions


def rollback_decide_update(args: argparse.Namespace) -> int:
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    for key, action in rollback_update_actions(plan, args.live_dir):
        print(f"{key}\t{action}")
    return 0


# ------------------------------------------------------------ REMOVE mode ----
# UNLIST existing Danish areas (Denmark-only, opt-in). A removed area's rows leave
# index.json, trail-search.json and trail-shapes.json; the area's own 2N per-area
# objects (<id>.json, silhouettes/<id>.json) are NEVER deleted (archive, not delete):
# nothing lists them, removed ids are never re-used, and rollback is only "restore the
# three shared files". Every id must be a registry area marked hold=superseded, so a
# non-Danish area can never be removed. Append/update modes above are untouched.

REMOVE_SCHEMA = "trekdex-dk-r2-remove-plan-v1"
MAX_REMOVE_AREAS = HARD_MAX_CHUNK
SUPERSEDED = "superseded"
REMOVE_WRITE_ORDER = ("index.json", "trail-search.json", "trail-shapes.json")   # index first: app stops listing


def _check_removable(registry: dict[str, Any], ids: list[str]) -> None:
    if not 1 <= len(ids) <= MAX_REMOVE_AREAS or len(set(ids)) != len(ids):
        raise DkError(f"removal needs 1..{MAX_REMOVE_AREAS} unique areas")
    for aid in ids:
        entry = registry["by_id"].get(aid)
        if entry is None:
            raise DkError(f"{aid}: not a Danish registry area; removal is Denmark-only")
        if entry.get("hold") != SUPERSEDED:
            raise DkError(f"{aid}: registry hold must be {SUPERSEDED!r} before removal (got {entry.get('hold')!r})")


def drop_index_rows(live: bytes, ids: list[str], sep: str, context: str) -> bytes:
    """`live` index bytes without the listed rows; every other row keeps its exact bytes."""
    pieces = _split_records(live, False, context, sep)
    gone = set(ids)
    found = [row[0] for row, _ in pieces if isinstance(row, list) and row and row[0] in gone]
    if sorted(found) != sorted(ids):
        raise DkError(f"{context} must list each removed area exactly once")
    return ("[" + sep.join(raw for row, raw in pieces if row[0] not in gone) + "]").encode("utf-8")


def drop_area_records(live: bytes, key: str, ids: list[str]) -> bytes:
    """`live` trail-search/trail-shapes bytes without the listed areas' contiguous record
    blocks; every other record keeps its exact live bytes and position."""
    as_dict = key == "trail-shapes.json"
    owner = _owner_shapes if as_dict else _owner_search
    pieces = _split_records(live, as_dict, f"live {key}")
    gone = set(ids)
    for aid in ids:
        idx = [n for n, (rec, _) in enumerate(pieces) if owner(rec) == aid]
        if not idx or idx != list(range(idx[0], idx[-1] + 1)):
            raise DkError(f"live {key} has no single contiguous block for {aid}; refusing to remove it")
    opener, closer = ("{", "}") if as_dict else ("[", "]")
    return (opener + ",".join(raw for rec, raw in pieces if owner(rec) not in gone) + closer).encode("utf-8")


def prove_remove_delta(key: str, before: bytes, after: bytes, ids: list[str]) -> dict[str, Any]:
    """Only the listed areas' records are gone; every other record is byte-identical and in place."""
    as_dict = key == "trail-shapes.json"
    owner = _owner_shapes if as_dict else _owner_search
    strict = contract._strict_trail_shape_records if as_dict else contract._strict_trail_search_records
    gone = set(ids)
    base, cand = strict(before, f"baseline {key}"), strict(after, f"candidate {key}")
    bp = _split_records(before, as_dict, f"baseline {key}")
    ap = _split_records(after, as_dict, f"candidate {key}")
    if any(owner(rec) in gone for rec, _ in ap):
        raise DkError(f"candidate {key} still has a record of a removed area")
    if [raw for rec, raw in bp if owner(rec) not in gone] != [raw for _, raw in ap]:
        raise DkError(f"candidate {key} changed a record outside the removed areas")
    if cand != [r for r in base if owner(r) not in gone]:
        raise DkError(f"candidate {key} is not the live records minus the removed areas")
    removed = len(base) - len(cand)
    if removed < len(ids):
        raise DkError(f"live {key} had fewer records than removed areas")
    return {"key": key, "baseline_records": len(base), "candidate_records": len(cand), "removed_records": removed,
            "other_records_byte_identical": len(cand), "baseline_sha256": _sha(before),
            "candidate_sha256": _sha(after)}


def _removal_paths(aid: str) -> list[str]:
    return [f"public/areas/geom/{aid}.json", f"public/areas/silhouettes/{aid}.json"]


def remove_areas(args: argparse.Namespace) -> int:
    """Repo edit for the removal commit: drop the rows from both index files and delete the
    repo copies of the geom/silhouette. Nothing is written until every input is validated."""
    repo = args.repo.resolve()
    registry = load_registry(repo / "trailforge/golden/dk-areas.json")
    ids = [i for i in args.ids.split(",") if i]
    _check_removable(registry, ids)
    files = [repo / p for aid in ids for p in _removal_paths(aid)]
    for path in files:
        if path.is_symlink() or not path.is_file():
            raise DkError(f"{path.relative_to(repo)} is missing or unsafe")
    new_index: dict[str, bytes] = {}
    for rel, compact in ((INDEX_REPO, False), (BUNDLE_REPO, True)):
        target = repo / rel
        if target.is_symlink() or not target.is_file():
            raise DkError(f"{rel} is missing or unsafe")
        data = target.read_bytes()
        _rows(data, rel, compact)
        new_index[rel] = drop_index_rows(data, ids, "," if compact else ", ", rel)
        _rows(new_index[rel], f"{rel} after removal", compact)
    for rel, data in new_index.items():
        (repo / rel).write_bytes(data)
    for path in files:
        path.unlink()
    print(f"removed {len(ids)} area(s) from both index files and deleted {len(files)} repo file(s): {','.join(ids)}")
    return 0


def _verify_remove_delta(repo: Path, ids: list[str], *, staged: bool) -> dict[str, Any]:
    registry = load_registry(repo / "trailforge/golden/dk-areas.json")
    _check_removable(registry, ids)
    names_cmd = ["diff", "--cached"] if staged else ["diff"]
    span = [] if staged else ["HEAD^", "HEAD"]

    def names(flt: str) -> list[str]:
        return [n for n in _git(repo, *names_cmd, "--name-only", f"--diff-filter={flt}", "-z", *span)
                .decode("utf-8").split("\0") if n]

    if names("A") or names("R") or names("C") or names("T"):
        raise DkError("removal diff contains an added, renamed, copied or type-changed path")
    want_deleted = sorted(p for aid in ids for p in _removal_paths(aid))
    if sorted(names("D")) != want_deleted:
        raise DkError(f"removal must delete exactly the 2N area files: {sorted(names('D'))}")
    if sorted(names("M")) != sorted([INDEX_REPO, BUNDLE_REPO]):
        raise DkError(f"removal must modify exactly the two index files: {names('M')}")

    def show(path: str, new: bool) -> bytes:
        return _git(repo, "show", (f":{path}" if staged else f"HEAD:{path}") if new else
                    (f"HEAD:{path}" if staged else f"HEAD^:{path}"))

    transitions = []
    for path, compact in ((INDEX_REPO, False), (BUNDLE_REPO, True)):
        before, after = show(path, False), show(path, True)
        _rows(before, f"baseline {path}", compact)
        _rows(after, f"candidate {path}", compact)
        if after != drop_index_rows(before, ids, "," if compact else ", ", path):
            raise DkError(f"{path}: candidate is not the baseline minus the removed rows (others byte-identical)")
        transitions.append({"path": path, "baseline_sha256": _sha(before), "candidate_sha256": _sha(after),
                            "removed_ids": sorted(ids)})
    return {"schema": "trekdex-dk-remove-delta-v1", "area_ids": ids, "deleted_paths": want_deleted,
            "allowed_paths": sorted(set(want_deleted) | {INDEX_REPO, BUNDLE_REPO}), "index_transitions": transitions}


def derive_remove_ids(repo: Path, live_index: bytes) -> list[str]:
    """The removal set = live Danish rows absent from the repo bundle. The bundle must be EXACTLY the
    live rows minus that set (same rows, same order), and every removed id must be a registry area
    marked hold=superseded whose repo geom/silhouette are already gone."""
    registry = load_registry(repo / "trailforge/golden/dk-areas.json")
    live_rows = json.loads(live_index)
    bundle_rows = _rows((repo / BUNDLE_REPO).read_bytes(), "repo bundle index", compact=True)
    keep = {r[0] for r in bundle_rows}
    removed = [r[0] for r in live_rows if r[0] not in keep]
    if not removed:
        raise DkError("nothing to remove: the repo bundle lists every live area")
    _check_removable(registry, removed)
    if [r for r in live_rows if r[0] not in set(removed)] != bundle_rows:
        raise DkError("repo bundle index is not the live index minus the removed areas "
                      "(same rows, same order); remove-areas only removes")
    for aid in removed:
        for rel in _removal_paths(aid):
            if (repo / rel).exists():
                raise DkError(f"{rel} still exists in the repo for a removed area")
    return removed


def derive_remove_areas_cmd(args: argparse.Namespace) -> int:
    ids = derive_remove_ids(args.repo, args.live_index.read_bytes())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(ids) + "\n", encoding="utf-8")
    print(f"{len(ids)} area(s) to remove from the repo vs live R2: {','.join(ids)}")
    return 0


def build_remove_candidates(args: argparse.Namespace) -> int:
    repo = args.repo
    live = {k: Path(v).read_bytes() for k, v in (("index.json", args.live_index),
            ("trail-search.json", args.live_search), ("trail-shapes.json", args.live_shapes))}
    ids = derive_remove_ids(repo, live["index.json"])
    if args.areas_file:
        listed = [i for i in args.areas_file.read_text(encoding="utf-8").split() if i]
        if listed != ids:
            raise DkError("areas file differs from the repo-vs-live derivation")
    bundle_rows = _rows((repo / BUNDLE_REPO).read_bytes(), "repo bundle index", compact=True)
    index_after = drop_index_rows(live["index.json"], ids, ",", "live index.json")
    if json.loads(index_after) != bundle_rows:
        raise DkError("candidate index.json rows differ from the repo bundle index")
    candidates = {"index.json": index_after}
    proofs = [{"key": "index.json", "baseline_sha256": _sha(live["index.json"]),
               "candidate_sha256": _sha(index_after), "removed_ids": ids,
               "other_rows_byte_identical": len(bundle_rows)}]
    for key in ("trail-search.json", "trail-shapes.json"):
        after = drop_area_records(live[key], key, ids)
        proofs.append(prove_remove_delta(key, live[key], after, ids))
        candidates[key] = after
    out = args.out_dir
    plan: dict[str, Any] = {"schema": REMOVE_SCHEMA, "mode": "remove", "areas": ids, "writes": [], "baseline": {},
                            "kept_objects": [k for a in ids for k in (f"{a}.json", f"silhouettes/{a}.json")],
                            "proofs": proofs}
    if len(plan["kept_objects"]) != 2 * len(ids):
        raise DkError("kept object set is not exactly 2N keys")
    for key in REMOVE_WRITE_ORDER:
        (out / key).parent.mkdir(parents=True, exist_ok=True)
        (out / key).write_bytes(candidates[key])
        plan["writes"].append({"key": key, "file": key, "bytes": len(candidates[key]), "sha256": _sha(candidates[key])})
    for key, data in live.items():
        plan["baseline"][key] = {"bytes": len(data), "sha256": _sha(data)}
    _write_json(out / "plan.json", plan)
    print(json.dumps({"areas": ids, "writes": len(plan["writes"]), "kept_objects": len(plan["kept_objects"])}, indent=2))
    return 0


def rollback_remove_actions(plan: dict[str, Any], live_dir: Path) -> list[tuple[str, str]]:
    """Per shared file: 'restore' when live is the applied candidate, 'skip' when it is already the
    baseline, REFUSE for anything else. A removal never deleted an object, so there is no delete."""
    if plan.get("schema") != REMOVE_SCHEMA:
        raise DkError("plan is not a removal plan")
    shared = smp.rollback_actions(plan, live_dir)
    return [(key, shared[key]) for key in SHARED_KEYS]


def rollback_decide_remove(args: argparse.Namespace) -> int:
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    for key, action in rollback_remove_actions(plan, args.live_dir):
        print(f"{key}\t{action}")
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
    s.add_argument("--expected", type=Path, default=EXPECTED_PATH)
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
    r.add_argument("--live-geom-dir", type=Path, default=LIVE_GEOM_DIR)

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
        v.add_argument("--mode", choices=["publish", "update", "remove"], default="publish")
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

    ri = sub.add_parser("replace-index")
    ri.add_argument("--registry", type=Path, default=REGISTRY_PATH)
    ri.add_argument("--selected", required=True, type=Path)
    ri.add_argument("--rows-dir", required=True, type=Path)
    ri.add_argument("--geom-dir", required=True, type=Path)
    ri.add_argument("--target", required=True, type=Path)

    ld = sub.add_parser("live-dk-ids")
    ld.add_argument("--repo", type=Path, default=Path("."))
    ld.add_argument("--live-index", required=True, type=Path)
    ld.add_argument("--out", required=True, type=Path)

    du = sub.add_parser("derive-update-areas")
    du.add_argument("--repo", type=Path, default=Path("."))
    du.add_argument("--live-index", required=True, type=Path)
    du.add_argument("--live-areas-dir", required=True, type=Path)
    du.add_argument("--out", required=True, type=Path)

    bu = sub.add_parser("build-update-candidates")
    bu.add_argument("--repo", type=Path, default=Path("."))
    for name in ("live-index", "live-search", "live-shapes", "live-areas-dir"):
        bu.add_argument(f"--{name}", required=True, type=Path)
    bu.add_argument("--areas-file", type=Path)
    bu.add_argument("--out-dir", required=True, type=Path)

    ru = sub.add_parser("rollback-decide-update")
    ru.add_argument("--plan", required=True, type=Path)
    ru.add_argument("--live-dir", required=True, type=Path)

    ra = sub.add_parser("remove-areas")
    ra.add_argument("--repo", type=Path, default=Path("."))
    ra.add_argument("--ids", required=True)

    dr = sub.add_parser("derive-remove-areas")
    dr.add_argument("--repo", type=Path, default=Path("."))
    dr.add_argument("--live-index", required=True, type=Path)
    dr.add_argument("--out", required=True, type=Path)

    br = sub.add_parser("build-remove-candidates")
    br.add_argument("--repo", type=Path, default=Path("."))
    for name in ("live-index", "live-search", "live-shapes"):
        br.add_argument(f"--{name}", required=True, type=Path)
    br.add_argument("--areas-file", type=Path)
    br.add_argument("--out-dir", required=True, type=Path)

    rr = sub.add_parser("rollback-decide-remove")
    rr.add_argument("--plan", required=True, type=Path)
    rr.add_argument("--live-dir", required=True, type=Path)

    args = p.parse_args(argv)
    handlers = {"remove-areas": remove_areas, "derive-remove-areas": derive_remove_areas_cmd,
                "build-remove-candidates": build_remove_candidates,
                "rollback-decide-remove": rollback_decide_remove,
                "replace-index": replace_index, "live-dk-ids": live_dk_ids_cmd,
                "derive-update-areas": derive_update_areas_cmd,
                "build-update-candidates": build_update_candidates,
                "rollback-decide-update": rollback_decide_update,
                "select": select_cmd, "prepare-areas": prepare_areas, "assert-expected": assert_expected,
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
