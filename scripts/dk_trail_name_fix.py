#!/usr/bin/env python3
"""Denmark-only trail display-name fixes (registry `trail_name_fixes`).

Run by scripts/dk-run-area.sh between assemble.py and publish_areas.py, and ONLY when the
area's registry entry has `trail_name_fixes` (an absent key means this script is never
called, so every other Danish run and every US run stays byte-identical).

Each fix `{osm_name, name, source, reason}` renames exactly one assembled trail feature:
`properties.name` becomes the corrected spelling and `properties.osm_name` keeps the OSM name
(or the name the dk-v3 prefix step already stored there), so the trail id, which
serve/to_app_json.py derives from `osm_name`, never moves. Nothing else in the file changes.
Fail closed (exit 2, file untouched) when a fix matches zero or several features, or when the
new name would equal another trail's final name.
"""
from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from pathlib import Path

FIX_KEYS = {"osm_name", "name", "source", "reason"}
MAX_NAME_LEN = 120


class FixError(Exception):
    pass


def validate_fixes(fixes: object, where: str = "trail_name_fixes") -> list[dict]:
    """The validated list, or FixError. Shared by dk_publish.load_registry."""
    if not isinstance(fixes, list) or not fixes:
        raise FixError(f"{where} must be a non-empty list when present")
    seen_old: set[str] = set()
    seen_new: set[str] = set()
    for item in fixes:
        if not isinstance(item, dict) or set(item) != FIX_KEYS:
            raise FixError(f"{where} entries need exactly {sorted(FIX_KEYS)}")
        for key in FIX_KEYS:
            if not isinstance(item[key], str) or not item[key].strip():
                raise FixError(f"{where} entry has an empty {key}: {item.get('osm_name')!r}")
        for key in ("osm_name", "name"):
            text = item[key]
            if text != text.strip() or text != unicodedata.normalize("NFC", text):
                raise FixError(f"{where} {key} must be trimmed NFC text: {text!r}")
            if len(text) > MAX_NAME_LEN:
                raise FixError(f"{where} {key} is longer than {MAX_NAME_LEN} characters: {text!r}")
            if any(unicodedata.category(c).startswith("C") for c in text):
                raise FixError(f"{where} {key} contains a control character: {text!r}")
        if item["osm_name"] == item["name"]:
            raise FixError(f"{where} {item['osm_name']!r}: name equals osm_name; nothing to fix")
        if not item["source"].startswith("https://") or any(c.isspace() for c in item["source"]):
            raise FixError(f"{where} {item['osm_name']!r}: source must be an https URL")
        if item["osm_name"] in seen_old:
            raise FixError(f"{where} repeats osm_name {item['osm_name']!r}")
        if item["name"] in seen_new:
            raise FixError(f"{where} repeats name {item['name']!r}")
        seen_old.add(item["osm_name"])
        seen_new.add(item["name"])
    return fixes


def apply_fixes(fc: dict, fixes: list[dict]) -> list[dict]:
    """Rename the features IN PLACE; return the report rows. FixError leaves fc unchanged."""
    features = fc.get("features")
    if not isinstance(features, list):
        raise FixError("trails file has no features list")
    targets = []
    for fix in fixes:
        hits = [f for f in features if (f.get("properties") or {}).get("name") == fix["osm_name"]]
        if len(hits) != 1:
            raise FixError(f"fix {fix['osm_name']!r} matches {len(hits)} trail(s), expected exactly 1")
        targets.append(hits[0])
    renamed = {id(f): fix["name"] for f, fix in zip(targets, fixes)}
    for f in features:
        final = renamed.get(id(f), (f.get("properties") or {}).get("name"))
        for g, fix in zip(targets, fixes):
            if f is not g and final == fix["name"]:
                raise FixError(f"fix {fix['osm_name']!r} -> {fix['name']!r} equals another trail's name")
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
    parser.add_argument("--trails", required=True, type=Path, help="assembled trails.geojson (rewritten in place)")
    parser.add_argument("--fixes", required=True, type=Path, help="JSON list of registry trail_name_fixes")
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        fixes = validate_fixes(json.loads(args.fixes.read_text(encoding="utf-8")))
        fc = json.loads(args.trails.read_text(encoding="utf-8"))
        rows = apply_fixes(fc, fixes)
    except (FixError, OSError, ValueError) as error:
        print(f"SAFETY BLOCK: trail_name_fixes: {error}", file=sys.stderr)
        return 2
    args.trails.write_text(json.dumps(fc), encoding="utf-8")
    args.report.write_text(json.dumps({"renamed": rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for row in rows:
        print(f"trail name fix: {row['from']!r} -> {row['name']!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
