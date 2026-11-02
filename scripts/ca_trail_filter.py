#!/usr/bin/env python3
"""Canada trail filter: runs between assemble.py and publish_areas.py in scripts/ca-run-area.sh.

The generic assembler knows nothing about Canadian long-distance trails, winter or motorised
networks, portages or French/English road names. This script drops those from the assembled
`trails.geojson` and appends each drop (with a category and a plain-language reason) to the
assembler's `removed.geojson`, so the qualify report and the blessed table's forbidden names see
them. It then applies the registry's `trail_name_fixes`. Nothing here touches shared code; it is a
standalone post-assemble step used only by the Canada lane.

Rules, in order (the first that matches wins):
  1. registry `exclude_trails`   n:<exact name> | n*:<exact name>; every selector
                                 must match at least one trail or the run fails closed
  2. ca-unnamed                  empty or "Unnamed ..." / "Sans nom ..." (never waivable)
  3. ca-long-distance            Trans Canada / Great Trail / Bruce / Rideau / Great Divide /
                                 Appalachian / Waterfront / Confederation names, rwn/nwn/iwn network
  4. ca-winter-only              ski, Nordic, snowshoe, snowmobile, fat bike
  5. ca-motorised                ATV, quad, OHV, VTT, dirt bike
  6. ca-bike                     mountain bike, MTB, cycling
  7. ca-portage                  portage routes
  8. ca-road-name                road/street/chemin/rue ... names, unless a trail word is also present
  9. ca-case-duplicate           same name differing only by case: keep the longest
 10. ca-fragment                 under 0.6 mi
A registry `keep_trails` waiver (official-page evidence) keeps a trail through rules 3-8 and 10.
Exit 2 with "SAFETY BLOCK" on any failure; the trails file is untouched on failure.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

MIN_TRAIL_MI = 0.6
MAX_NAME_LEN = 120
CATEGORIES = (
    "ca-curated-exclude", "ca-unnamed", "ca-long-distance", "ca-winter-only", "ca-motorised",
    "ca-bike", "ca-portage", "ca-road-name", "ca-case-duplicate", "ca-fragment",
)
LONG_DISTANCE_NETWORKS = {"rwn", "nwn", "iwn"}
FIX_KEYS = {"osm_name", "name", "source", "reason"}
KEEP_KEYS = {"name", "source", "reason"}


class FilterError(Exception):
    pass


def fold(text: Any) -> str:
    """Lower-case, accent-stripped, whitespace-collapsed form used for vocabulary matching."""
    decomposed = unicodedata.normalize("NFKD", str(text or "")).casefold()
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^\w']+", " ", stripped.replace("\u2019", "'")).split())


def _phrases(*words: str) -> re.Pattern[str]:
    return re.compile(r"(?<!\w)(?:" + "|".join(words) + r")(?!\w)")


LONG_DISTANCE = _phrases(
    r"trans[- ]?canada(?: trail)?", r"great trail", r"sentier trans[- ]?canadien", r"sentier transcanadien",
    r"bruce trail", r"rideau trail", r"great divide(?: trail)?", r"sentier national",
    r"sentier international des appalaches", r"international appalachian trail",
    r"waterfront trail", r"confederation trail",
)
WINTER = _phrases(
    r"ski", r"skiing", r"nordic", r"snowshoe", r"snowshoeing", r"snowmobile", r"snowmobiling",
    r"motoneige", r"fat ?bike", r"raquette", r"raquettes", r"ski de fond", r"cross country ski",
    r"sledding", r"toboggan", r"luge",
)
MOTORISED = _phrases(
    r"atv", r"quads?", r"ohv", r"vtt", r"orv", r"utv", r"dirt ?bike", r"off road vehicles?",
)
BIKE = _phrases(
    r"mtb", r"mountain bik(?:e|es|ing)", r"velo de montagne", r"bike park", r"downhill", r"freeride",
    r"pump track", r"cycling", r"cyclable", r"bike path", r"bike trail", r"bikeway",
)
PORTAGE = _phrases(r"portages?")
ROAD = _phrases(
    r"roads?", r"rd", r"streets?", r"avenues?", r"ave", r"boulevard", r"blvd", r"highway", r"hwy",
    r"drive", r"lane", r"parkway", r"expressway", r"route \d+", r"chemin", r"chemins", r"rue", r"rues",
    r"autoroute", r"rang", r"montee",
)
# A road word in a name that also holds a trail word (Montee in summit names, "Old Mill Road Trail")
# is a trail, not a road.
TRAIL_WORDS = _phrases(
    r"trails?", r"sentiers?", r"paths?", r"loop", r"boucle", r"walk", r"hike", r"lookout", r"belvedere",
    r"summit", r"sommet", r"falls", r"chutes?", r"boardwalk", r"nature",
)


def vocabulary_category(name: str, network: str) -> tuple[str, str] | None:
    """(category, reason) for a name/network the Canada lane never publishes, or None."""
    f = fold(name)
    if str(network or "").strip().lower() in LONG_DISTANCE_NETWORKS:
        return "ca-long-distance", f"network={network} long-distance route"
    for category, pattern, reason in (
            ("ca-long-distance", LONG_DISTANCE, "long-distance trail name"),
            ("ca-winter-only", WINTER, "winter-only trail name"),
            ("ca-motorised", MOTORISED, "motorised trail name"),
            ("ca-bike", BIKE, "bike trail name"),
            ("ca-portage", PORTAGE, "portage route")):
        hit = pattern.search(f)
        if hit:
            return category, f"{reason} ({hit.group(0)!r})"
    hit = ROAD.search(f)
    if hit and not TRAIL_WORDS.search(f):
        return "ca-road-name", f"road name ({hit.group(0)!r}) with no trail word"
    return None


def _clean_text(text: Any, where: str, key: str) -> None:
    if not isinstance(text, str) or not text.strip():
        raise FilterError(f"{where} has an empty {key}")
    if text != text.strip() or text != unicodedata.normalize("NFC", text):
        raise FilterError(f"{where} {key} must be trimmed NFC text: {text!r}")
    if len(text) > MAX_NAME_LEN:
        raise FilterError(f"{where} {key} is longer than {MAX_NAME_LEN} characters: {text!r}")
    if any(unicodedata.category(c).startswith("C") for c in text):
        raise FilterError(f"{where} {key} contains a control character: {text!r}")


def _check_source(item: dict[str, Any], where: str) -> None:
    source = item["source"]
    if not isinstance(source, str) or not source.startswith("https://") or any(c.isspace() for c in source):
        raise FilterError(f"{where}: source must be an https URL")
    if not isinstance(item["reason"], str) or not item["reason"].strip():
        raise FilterError(f"{where}: reason must be text")


def validate_fixes(fixes: object, where: str = "trail_name_fixes") -> list[dict]:
    """The validated list, or FilterError. Shared by ca_publish.load_registry."""
    if not isinstance(fixes, list) or not fixes:
        raise FilterError(f"{where} must be a non-empty list when present")
    seen_old: set[str] = set()
    seen_new: set[str] = set()
    for item in fixes:
        if not isinstance(item, dict) or set(item) != FIX_KEYS:
            raise FilterError(f"{where} entries need exactly {sorted(FIX_KEYS)}")
        _clean_text(item["osm_name"], where, "osm_name")
        _clean_text(item["name"], where, "name")
        if item["osm_name"] == item["name"]:
            raise FilterError(f"{where} {item['osm_name']!r}: name equals osm_name; nothing to fix")
        _check_source(item, f"{where} {item['osm_name']!r}")
        if item["osm_name"] in seen_old or item["name"] in seen_new:
            raise FilterError(f"{where} repeats {item['osm_name']!r} / {item['name']!r}")
        seen_old.add(item["osm_name"])
        seen_new.add(item["name"])
    return fixes


def validate_keep_trails(items: object, where: str = "keep_trails") -> list[dict]:
    """Official-source waivers: {name, source, reason}. Shared by ca_publish.load_registry."""
    if not isinstance(items, list) or not items:
        raise FilterError(f"{where} must be a non-empty list when present")
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict) or set(item) != KEEP_KEYS:
            raise FilterError(f"{where} entries need exactly {sorted(KEEP_KEYS)}")
        _clean_text(item["name"], where, "name")
        _check_source(item, f"{where} {item['name']!r}")
        if item["name"] in seen:
            raise FilterError(f"{where} repeats {item['name']!r}")
        seen.add(item["name"])
    return items


def _selector_hits(selector: str, props: dict[str, Any]) -> bool:
    if selector.startswith(("n*:", "n:")):
        return props.get("name") == selector.split(":", 1)[1]
    raise FilterError(f"unsupported exclude selector {selector!r} (use n:<name> or n*:<name>)")


def filter_features(features: list[dict[str, Any]], entry: dict[str, Any]
                    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(kept, removed) features; removed ones carry removed_category + removed_reason.
    Raises FilterError when an exclude selector matches nothing."""
    keep_names = {k["name"] for k in entry.get("keep_trails") or []}
    verdict: dict[int, tuple[str, str, dict[str, Any]]] = {}
    for item in entry.get("exclude_trails") or []:
        selector = item["id"]
        hits = [i for i, f in enumerate(features) if _selector_hits(selector, f.get("properties") or {})]
        if not hits:
            raise FilterError(f"exclude {selector} ({item['name']!r}) matches no assembled trail")
        for i in hits:
            verdict.setdefault(i, ("ca-curated-exclude", f"registry exclude: {item['reason']}", item))
    for i, f in enumerate(features):
        if i in verdict:
            continue
        props = f.get("properties") or {}
        name = props.get("name")
        if not str(name or "").strip() or re.match(r"(?:unnamed|sans nom)\b", fold(name)):
            verdict[i] = ("ca-unnamed", "no real name", {})
            continue
        if name in keep_names:
            continue
        found = vocabulary_category(name, props.get("network"))
        if found:
            verdict[i] = (found[0], found[1], {})
            continue
        length = props.get("length_mi")
        if isinstance(length, (int, float)) and not isinstance(length, bool) and length < MIN_TRAIL_MI:
            verdict[i] = ("ca-fragment", f"{length} mi is under {MIN_TRAIL_MI} mi", {})
    groups: dict[str, list[int]] = {}
    for i, f in enumerate(features):
        if i not in verdict:
            groups.setdefault(fold((f.get("properties") or {}).get("name")), []).append(i)
    for members in groups.values():
        if len({(features[i].get("properties") or {}).get("name") for i in members}) > 1:
            best = max(members, key=lambda i: ((features[i].get("properties") or {}).get("length_mi") or 0, -i))
            for i in members:
                if i != best:
                    verdict[i] = ("ca-case-duplicate", "same name differing only by case", {})
    kept, removed = [], []
    for i, f in enumerate(features):
        if i not in verdict:
            kept.append(f)
            continue
        category, reason, item = verdict[i]
        props = dict(f.get("properties") or {})
        props["removed_category"], props["removed_reason"] = category, reason
        if category == "ca-curated-exclude":
            props["curated_exclude_id"], props["curated_exclude_reason"] = item["id"], item["reason"]
        removed.append({**f, "properties": props})
    return kept, removed


def apply_fixes(features: list[dict[str, Any]], fixes: list[dict]) -> list[dict]:
    """Rename the features IN PLACE; return the report rows. FilterError leaves them unchanged."""
    targets = []
    for fix in fixes:
        hits = [f for f in features if (f.get("properties") or {}).get("name") == fix["osm_name"]]
        if len(hits) != 1:
            raise FilterError(f"fix {fix['osm_name']!r} matches {len(hits)} trail(s), expected exactly 1")
        targets.append(hits[0])
    renamed = {id(f): fix["name"] for f, fix in zip(targets, fixes)}
    for f in features:
        final = renamed.get(id(f), (f.get("properties") or {}).get("name"))
        for g, fix in zip(targets, fixes):
            if f is not g and final == fix["name"]:
                raise FilterError(f"fix {fix['osm_name']!r} -> {fix['name']!r} equals another trail's name")
    rows = []
    for f, fix in zip(targets, fixes):
        props = f["properties"]
        props.setdefault("osm_name", props["name"])
        props["name"] = fix["name"]
        rows.append({"osm_name": props["osm_name"], "from": fix["osm_name"], "name": fix["name"],
                     "source": fix["source"]})
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--area-id", required=True)
    parser.add_argument("--trails", required=True, type=Path, help="assembled trails.geojson (rewritten in place)")
    parser.add_argument("--removed", required=True, type=Path,
                        help="assembler removed.geojson (the filter's drops are appended in place)")
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        registry = json.loads(args.registry.read_text(encoding="utf-8"))
        entry = next((a for a in registry.get("areas") or [] if a.get("id") == args.area_id), None)
        if entry is None:
            raise FilterError(f"{args.area_id} is not in the registry")
        fc = json.loads(args.trails.read_text(encoding="utf-8"))
        removed_fc = json.loads(args.removed.read_text(encoding="utf-8"))
        features = fc.get("features")
        if not isinstance(features, list) or not isinstance(removed_fc.get("features"), list):
            raise FilterError("trails/removed files have no features list")
        kept, dropped = filter_features(features, entry)
        rows = apply_fixes(kept, validate_fixes(entry["trail_name_fixes"])) if entry.get("trail_name_fixes") else []
    except (FilterError, OSError, ValueError, KeyError) as error:
        print(f"SAFETY BLOCK: ca_trail_filter: {error}", file=sys.stderr)
        return 2
    fc["features"] = kept
    removed_fc["features"] = [*removed_fc["features"], *dropped]
    args.trails.write_text(json.dumps(fc), encoding="utf-8")
    args.removed.write_text(json.dumps(removed_fc), encoding="utf-8")
    counts: dict[str, int] = {}
    for f in dropped:
        cat = f["properties"]["removed_category"]
        counts[cat] = counts.get(cat, 0) + 1
    args.report.write_text(json.dumps({
        "kept": len(kept), "removed_counts": dict(sorted(counts.items())),
        "removed": [{"name": f["properties"].get("name"), "category": f["properties"]["removed_category"],
                     "reason": f["properties"]["removed_reason"], "length_mi": f["properties"].get("length_mi")}
                    for f in dropped],
        "renamed": rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for f in dropped:
        print(f"ca filter: dropped {f['properties'].get('name')!r}: {f['properties']['removed_category']}")
    for row in rows:
        print(f"ca filter: renamed {row['from']!r} -> {row['name']!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
