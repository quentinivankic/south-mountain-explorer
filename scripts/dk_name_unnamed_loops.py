#!/usr/bin/env python3
"""Denmark-only naming of unnamed signed loops (registry `name_unnamed_loops`).
Run by scripts/dk-run-area.sh between assemble.py and publish_areas.py, and ONLY when the
area's registry entry has `name_unnamed_loops` (an absent key means this script is never
called, so every other Danish run, every Canadian run and every US run stays byte-identical).
OSM carries a few signed local loops (lwn, `osmc:symbol`, `roundtrip=yes`) with no name tag
at all; the assembler then labels them with a road name or leaves them nameless. Naming one
from its waymark colour ("Pælebjerg Rød rute") is allowed only when a Danish agency page
(Naturstyrelsen, a kommune, a Visit organisation) lists that loop. Each reviewed entry
`{relation, name, source, listed_km, reason}` therefore carries the page URL and the length
range the page states, and every gate below fails closed (exit 2, file untouched):
  * the relation must be unnamed (no `name`, no `name:*`), route=hiking|foot, signed
    (`osmc:symbol`), `roundtrip=yes`, and its waymark colour must be the colour word the name ends with;
  * `source` must be an https page on an agency host (not a blog, AllTrails or Komoot);
  * the assembled loop must be 0.75 x the lower to 1.25 x the upper `listed_km`, so a stub or a
    differently routed loop is never labelled with a listing it does not match;
  * exactly one assembled trail may carry the relation, and the new name may not equal any other.
An unlisted loop is not in the list and keeps its assembled label; the registry excludes it.
`osm_name` is not set: these loops have no OSM name to keep, the trail id derives from the new name.
"""
from __future__ import annotations
import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit
ENTRY_KEYS = {"relation", "name", "source", "listed_km", "reason"}
MAX_NAME_LEN = 120
MAX_LISTED_KM = 60
LENGTH_TOLERANCE = 0.25
KM_PER_MI = 1.609344
COLOURS = {  # OSM waymark colour -> the Danish word the name must end with ("<Colour> rute")
    "red": "Rød", "yellow": "Gul", "blue": "Blå", "green": "Grøn", "orange": "Orange",
    "purple": "Lilla", "white": "Hvid", "black": "Sort", "brown": "Brun",
}
AGENCY_HOSTS = {  # reviewed allowlist: Naturstyrelsen, the Fanø kommune, Visit organisations; extend it in a reviewed change
    "naturstyrelsen.dk", "nst.dk", "fanoe.dk", "visitdenmark.com", "visitdenmark.dk", "visitfanoe.dk",
    "vadehavskysten.dk", "vadehavskysten.com", "vadehavskysten.de",
}
class NamingError(Exception):
    pass
def _agency_host(host: str) -> bool:
    host = host.removeprefix("www.")
    return host in AGENCY_HOSTS or host.endswith((".naturstyrelsen.dk", ".nst.dk"))
def check_source(url: str, where: str) -> None:
    try:
        parts = urlsplit(url)
        host, port = (parts.hostname or "").lower(), parts.port
    except ValueError:
        raise NamingError(f"{where}: source is not a valid URL: {url!r}") from None
    if (parts.scheme != "https" or any(c.isspace() for c in url) or parts.username or parts.password
            or port not in (None, 443) or parts.path in ("", "/") or not _agency_host(host)):
        raise NamingError(f"{where}: source must be an https page on a Naturstyrelsen, kommune or Visit "
                         f"host (not a blog or trail site): {url!r}")
def colour_word_for(name: str) -> str | None:
    for word in COLOURS.values():
        if name.endswith(f" {word} rute"):
            return word
    return None
def validate_entries(entries: object, where: str = "name_unnamed_loops") -> list[dict]:
    """The validated list, or NamingError. Shared by dk_publish.load_registry."""
    if not isinstance(entries, list) or not entries:
        raise NamingError(f"{where} must be a non-empty list when present")
    seen_rel: set[str] = set()
    seen_name: set[str] = set()
    for item in entries:
        if not isinstance(item, dict) or set(item) != ENTRY_KEYS:
            raise NamingError(f"{where} entries need exactly {sorted(ENTRY_KEYS)}")
        rel, name = item["relation"], item["name"]
        for key in ("relation", "name", "source", "reason"):
            if not isinstance(item[key], str) or not item[key].strip():
                raise NamingError(f"{where} entry has an empty {key}: {rel!r}")
        if not re.fullmatch(r"r[1-9][0-9]*", rel):
            raise NamingError(f"{where} relation must look like 'r123': {rel!r}")
        if name != name.strip() or name != unicodedata.normalize("NFC", name) or len(name) > MAX_NAME_LEN \
                or any(unicodedata.category(c).startswith("C") for c in name):
            raise NamingError(f"{where} {rel}: name must be trimmed NFC text up to {MAX_NAME_LEN} characters")
        word = colour_word_for(name)
        if word is None or len(name) - len(f" {word} rute") < 2:
            raise NamingError(f"{where} {rel}: name must be '<Place> <Colour> rute' with a colour in "
                             f"{sorted(COLOURS.values())}: {name!r}")
        check_source(item["source"], f"{where} {rel}")
        km = item["listed_km"]
        if (not isinstance(km, list) or len(km) != 2
                or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in km)
                or not 0 < km[0] <= km[1] <= MAX_LISTED_KM):
            raise NamingError(f"{where} {rel}: listed_km must be [low, high] with 0 < low <= high <= "
                             f"{MAX_LISTED_KM}")
        if rel in seen_rel or name.casefold() in seen_name:
            raise NamingError(f"{where} repeats {rel} or {name!r}")
        seen_rel.add(rel)
        seen_name.add(name.casefold())
    return entries
_NAME_KEYS = ("official_name", "alt_name", "short_name", "loc_name", "old_name")
def waymark_colour(symbol: str) -> str | None:
    """The colour of an OSMC symbol 'waycolour:background:foreground...': the colour prefix of the
    foreground ('red:brown:yellow_dot' -> 'yellow'); the waycolour when there is no foreground."""
    parts = [p.strip().lower() for p in symbol.split(":")]
    token = parts[2] if len(parts) >= 3 and parts[2] else parts[0]
    colour = token.split("_")[0]
    return colour if colour in COLOURS else None
def check_relation(rel: str, tags: dict, name: str) -> None:
    """The relation is an unnamed, signed, round-trip hiking loop whose waymark colour is the name's colour."""
    if tags.get("type") != "route" or tags.get("route") not in ("hiking", "foot"):
        raise NamingError(f"{rel}: not a hiking/foot route relation")
    if any(k == "name" or k.startswith("name:") or k in _NAME_KEYS for k in tags):
        raise NamingError(f"{rel}: the relation already has a name in OSM; only unnamed loops are named here")
    if not (tags.get("osmc:symbol") or "").strip():
        raise NamingError(f"{rel}: no osmc:symbol, the loop is not signed")
    if tags.get("roundtrip") != "yes":
        raise NamingError(f"{rel}: roundtrip is not yes, not a loop")
    colour = waymark_colour(tags["osmc:symbol"])
    if colour is None or colour_word_for(name) != COLOURS[colour]:
        raise NamingError(f"{rel}: waymark {tags['osmc:symbol']!r} does not give the colour word of {name!r}")
def apply_entries(fc: dict, relations: dict[int, dict], entries: list[dict]) -> list[dict]:
    """Rename the listed loops IN PLACE; return the report rows. NamingError leaves fc unchanged."""
    entries = validate_entries(entries)
    features = fc.get("features")
    if not isinstance(features, list):
        raise NamingError("trails file has no features list")
    targets = []
    for entry in entries:
        rel, rid = entry["relation"], int(entry["relation"][1:])
        if rid not in relations:
            raise NamingError(f"{rel}: relation not found in the relation authority file")
        check_relation(rel, relations[rid], entry["name"])
        hits = [f for f in features if (f.get("properties") or {}).get("source") == "relation"
                and (f.get("properties") or {}).get("root_relation_ids") == [rid]]
        if len(hits) != 1:
            raise NamingError(f"{rel}: matches {len(hits)} assembled trail(s), expected exactly 1")
        km = hits[0]["properties"].get("length_mi", 0.0) * KM_PER_MI
        low, high = entry["listed_km"]
        if not low * (1 - LENGTH_TOLERANCE) <= km <= high * (1 + LENGTH_TOLERANCE):
            raise NamingError(f"{rel}: the loop is {km:.2f} km but the agency page lists {low}-{high} km "
                              f"(tolerance {LENGTH_TOLERANCE:.0%}); it is not the listed loop")
        targets.append(hits[0])
    renamed = {id(f): e["name"] for f, e in zip(targets, entries)}
    for f in features:
        final = unicodedata.normalize("NFC", str(renamed.get(id(f), (f.get("properties") or {}).get("name")) or ""))
        for g, entry in zip(targets, entries):
            if f is not g and final.casefold() == entry["name"].casefold():
                raise NamingError(f"{entry['relation']}: the name {entry['name']!r} equals another trail's name")
    rows = []
    for f, entry in zip(targets, entries):
        props = f["properties"]
        rows.append({"relation": entry["relation"], "from": props.get("name"), "name": entry["name"],
                     "source": entry["source"], "listed_km": entry["listed_km"],
                     "length_km": round(props["length_mi"] * KM_PER_MI, 2)})
        props["name"] = entry["name"]
    return rows
def load_relation_tags(document: dict) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for item in document.get("relations") or []:
        rid = item.get("relation_id")
        if isinstance(rid, int) and isinstance(item.get("tags"), dict):
            out[rid] = item["tags"]
    return out
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--trails", required=True, type=Path, help="assembled trails.geojson (rewritten in place)")
    parser.add_argument("--relations", required=True, type=Path, help="relation-members.aoi.json (relation tags)")
    parser.add_argument("--entries", required=True, type=Path, help="JSON list of registry name_unnamed_loops")
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        entries = json.loads(args.entries.read_text(encoding="utf-8"))
        relations = load_relation_tags(json.loads(args.relations.read_text(encoding="utf-8")))
        fc = json.loads(args.trails.read_text(encoding="utf-8"))
        rows = apply_entries(fc, relations, entries)
    except (NamingError, OSError, ValueError) as error:
        print(f"SAFETY BLOCK: name_unnamed_loops: {error}", file=sys.stderr)
        return 2
    args.trails.write_text(json.dumps(fc), encoding="utf-8")
    args.report.write_text(json.dumps({"named": rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for row in rows:
        print(f"named unnamed loop: {row['relation']} -> {row['name']!r} ({row['length_km']} km)")
    return 0
if __name__ == "__main__":
    sys.exit(main())
