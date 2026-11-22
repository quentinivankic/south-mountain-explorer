#!/usr/bin/env python3
"""Canada way_name_overrides: give a specific set of OSM ways the signed trail name an agency lists.
Runs in scripts/ca-run-area.sh right after the optional adopt_ways / hiking_tag_wins steps and before
ca_trail_filter, only for an area whose registry entry has `way_name_overrides`. Some signed hikes ride
ways that OSM names after a shared long-distance or winter route (Waterton's Carthew-Alderson is the
"Great Divide Trail" way by way), so the Canada vocabulary rules drop them (ca-long-distance,
ca-winter-only) and the assembler files them under the thru-hike relation. Each entry
{name, way_ids, evidence, reason[, max_gap_m]} appends ONE feature (source "name-override") built from
exactly those ways, which then goes through ca_trail_filter, publish and the duplicate gates.
`evidence` is REQUIRED: an https URL of an agency page (Parks Canada, a provincial park or government
site) that lists the signed trail. An entry without it is rejected by the validator.
Gates, all fail closed (exit 2 "SAFETY BLOCK"; trails file untouched):
  - every way carries an OSM name that the Canada filter blocks as long-distance or winter-only (a way
    with a normal name, or no name, is not an override: use trail_name_fixes or adopt_ways);
  - the new name is not itself blocked by the vocabulary rules;
  - every way is a path/footway/steps/pedestrian way with no access=private|no, foot=no, mtb:scale,
    piste:type, snowmobile or motor-only tag (adopt_ways checks, reused);
  - the ways chain into one route (shared nodes, gaps <= max_gap_m, default 30), at least 90% of the
    length lies inside the exact-area boundary, no way is already in an assembled trail, and the new
    name is not an assembled trail name (adopt_ways checks, reused).
Nothing here touches shared engine code; no OSM edit is made. Absent key = this step never runs.
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ca_adopt_ways  # noqa: E402
import ca_trail_filter  # noqa: E402
WNO_KEYS = {"name", "way_ids", "evidence", "reason"}
OPTIONAL_KEYS = {"max_gap_m"}
BLOCKED_CATEGORIES = {"ca-long-distance", "ca-winter-only"}
SOURCE_TAG = "name-override"
WINTER_OR_MOTOR_TAGS = {"snowmobile": ("yes", "designated"), "atv": ("yes", "designated"),
                        "motorcar": ("designated",), "motorcycle": ("designated",)}
# Agency hosts (exact domain or any subdomain). Aggregators, OSM and wikis are not agencies.
AGENCY_DOMAINS = (
    "canada.ca", "gc.ca", "bcparks.ca", "albertaparks.ca", "alberta.ca", "ontarioparks.com", "ontario.ca",
    "sepaq.com", "quebec.ca", "gouv.qc.ca", "novascotia.ca", "gnb.ca", "gov.nl.ca", "gov.pe.ca", "gov.mb.ca",
    "gov.sk.ca", "saskatchewan.ca", "manitobaparks.com", "gov.bc.ca", "yukon.ca", "gov.nt.ca", "gov.nu.ca",
)
class NameOverrideError(Exception):
    pass
def agency_url_problem(url: object) -> str | None:
    """None when `url` is an https page on an agency domain, else the reason it is not."""
    if not isinstance(url, str) or not url.strip():
        return "evidence is required (an agency page URL that lists the trail)"
    if url != url.strip() or any(c.isspace() for c in url):
        return "evidence must not contain whitespace"
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        return "evidence must be an https URL"
    host = parts.hostname.lower()
    if not any(host == d or host.endswith("." + d) for d in AGENCY_DOMAINS):
        return f"evidence host {host} is not a known agency domain"
    if parts.path in ("", "/"):
        return "evidence must be the page that lists the trail, not a site root"
    return None
def validate_way_name_overrides(items: object, where: str = "way_name_overrides") -> list[dict]:
    """The validated list, or NameOverrideError. Shared by ca_publish.load_registry."""
    if not isinstance(items, list) or not items:
        raise NameOverrideError(f"{where} must be a non-empty list when present")
    for item in items:
        if not isinstance(item, dict):
            raise NameOverrideError(f"{where} entries must be objects")
        label = f"{where} {item.get('name')!r}"
        if "evidence" not in item:
            raise NameOverrideError(f"{label}: evidence is required (an agency page URL that lists the trail)")
        problem = agency_url_problem(item["evidence"])
        if problem:
            raise NameOverrideError(f"{label}: {problem}")
        if not WNO_KEYS <= set(item) or set(item) - WNO_KEYS - OPTIONAL_KEYS:
            raise NameOverrideError(f"{where} entries need {sorted(WNO_KEYS)} and may add {sorted(OPTIONAL_KEYS)}")
    base = []
    for item in items:
        mapped = {k: item[k] for k in ("name", "way_ids", "reason")}
        mapped["source"] = item["evidence"]
        if "max_gap_m" in item:
            mapped["max_gap_m"] = item["max_gap_m"]
        base.append(mapped)
    try:
        ca_adopt_ways.validate_adopt_ways(base, where)
    except ca_adopt_ways.AdoptError as error:
        raise NameOverrideError(str(error).replace("adopt_ways", "way_name_overrides")) from None
    for item in items:
        hit = ca_trail_filter.vocabulary_category(item["name"], "")
        if hit:
            raise NameOverrideError(f"{where} {item['name']!r}: the new name would be dropped as {hit[0]} {hit[1]}")
    return items
def way_name_problems(wid: int, tags: dict[str, str]) -> list[str]:
    """Why a way is not an override candidate (empty = fine): its OSM name must be a blocked one."""
    name = tags.get("name")
    if not name:
        return [f"way {wid}: no OSM name (an unnamed way is an adopt_ways case, not an override)"]
    hit = ca_trail_filter.vocabulary_category(name, tags.get("network", ""))
    if hit is None or hit[0] not in BLOCKED_CATEGORIES:
        return [f"way {wid}: name {name!r} is not blocked as long-distance or winter-only "
                f"(use trail_name_fixes or adopt_ways)"]
    return []
def mode_problems(wid: int, tags: dict[str, str]) -> list[str]:
    return [f"way {wid}: {k}={tags[k]}" for k, bad in WINTER_OR_MOTOR_TAGS.items() if tags.get(k) in bad]
def build(entries: list[dict], ways: dict[int, dict], features: list[dict], boundary: Any, area_name: str,
          excluded: frozenset[str] = frozenset()) -> tuple[list[dict], list[dict]]:
    """(new features, evidence). Raises NameOverrideError on any failed check."""
    adopt_entries = []
    for entry in entries:
        label = f"way_name_overrides {entry['name']!r}"
        missing = [i for i in entry["way_ids"] if i not in ways]
        if missing:
            raise NameOverrideError(f"{label}: ways missing from the extract: {missing[:8]}")
        problems = []
        for i in entry["way_ids"]:
            problems += way_name_problems(i, ways[i]["tags"]) + mode_problems(i, ways[i]["tags"])
        if problems:
            raise NameOverrideError(f"{label}: " + "; ".join(problems[:6]))
        adopt = {"name": entry["name"], "way_ids": entry["way_ids"], "source": entry["evidence"],
                 "reason": entry["reason"]}
        if "max_gap_m" in entry:
            adopt["max_gap_m"] = entry["max_gap_m"]
        adopt_entries.append(adopt)
    try:
        new, evidence = ca_adopt_ways.build_features(adopt_entries, ways, features, boundary, area_name, excluded)
    except ca_adopt_ways.AdoptError as error:
        raise NameOverrideError(str(error).replace("adopt_ways", "way_name_overrides", 1)) from None
    for feature, item in zip(new, evidence):
        props = feature["properties"]
        props["source"] = SOURCE_TAG
        props["ckey"] = props["ckey"].replace("adopt-", "wno-", 1)
        props["name_override_evidence"] = props.pop("adopt_source")
        item["evidence"] = item.pop("source")
        item["osm_names"] = sorted({ways[w]["tags"]["name"] for w in item["way_ids"]})
    return new, evidence
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
        if entry is None or "way_name_overrides" not in entry:
            raise NameOverrideError(f"{args.area_id}: no way_name_overrides in the registry")
        entries = validate_way_name_overrides(entry["way_name_overrides"], f"{args.area_id}: way_name_overrides")
        trails_path = Path(args.trails)
        doc = json.loads(trails_path.read_text(encoding="utf-8"))
        features = doc["features"]
        ways = ca_adopt_ways.read_ways(args.pbf, {i for e in entries for i in e["way_ids"]})
        new, evidence = build(entries, ways, features, ca_adopt_ways.load_boundary(trails_path), entry["name"],
                              frozenset(i["id"].split(":", 1)[1] for i in entry.get("exclude_trails") or []
                                        if str(i.get("id", "")).startswith(("n:", "n*:"))))
    except (NameOverrideError, OSError, ValueError, KeyError) as error:
        print(f"SAFETY BLOCK: {error}", file=sys.stderr)
        return 2
    doc["features"] = features + new
    trails_path.write_text(json.dumps(doc), encoding="utf-8")
    Path(args.report).write_text(json.dumps({"area": args.area_id, "named": evidence}, indent=2) + "\n",
                                 encoding="utf-8")
    print(f"way_name_overrides: {len(new)} trail(s) appended to {trails_path.name}")
    return 0
if __name__ == "__main__":
    sys.exit(main())
