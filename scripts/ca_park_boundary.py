#!/usr/bin/env python3
"""Canada park_boundary: keep a registry area's own `leisure=park` boundary (relation or way).
Runs in scripts/ca-run-area.sh right after aoi.sh, only for an area whose registry entry has
`park_boundary`. prefilter.sh does not select leisure=park, so the AOI extract of such an area has no
boundary and `check-boundary-unique` / assemble find none. This step re-adds exactly that one object
(and its member ways and nodes, `osmium getid -r`) from the raw province PBF into the AOI PBF.
Registry opt-in: `park_boundary: {"reason": "<why this park is the right boundary>"}`. Unlike
`keep_park_boundary` (relation only, needs an operator tag, re-added by the workflow) it works for a
relation or a way and needs no operator tag; the two keys are mutually exclusive.
Checks, all fail closed (exit 2 "SAFETY BLOCK"; the AOI PBF is untouched on failure):
  the registry entry has park_boundary; the raw PBF holds the object exactly once with leisure=park,
  type boundary|multipolygon (relation) or a closed ring (way), and a name equal to the registry name.
Nothing here touches shared engine code; no OSM edit is made. Absent key = this step never runs.
"""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path
from typing import Any

PARK_KEYS = {"reason"}
REL_TYPES = {"boundary", "multipolygon"}
NAME_KEYS = ("name",)  # same as ca_publish.REL_NAME_KEYS: the engine keys boundaries on `name`


class ParkBoundaryError(Exception):
    pass


def validate_park_boundary(value: object, where: str = "park_boundary") -> dict:
    """The validated opt-in, or ParkBoundaryError. Shared by ca_publish.load_registry."""
    if (not isinstance(value, dict) or set(value) != PARK_KEYS or not isinstance(value["reason"], str)
            or not value["reason"].strip() or value["reason"] != value["reason"].strip()):
        raise ParkBoundaryError(f"{where} must be exactly {{reason: non-empty text}}")
    return value


def tag_problems(kind: str, ident: int, tags: dict[str, str], name: str, closed: bool | None = None) -> list[str]:
    """Why one OSM object is not an acceptable park_boundary for the registry entry (empty = fine)."""
    out = []
    if tags.get("leisure") != "park":
        out.append(f"{kind} {ident} is not leisure=park (park_boundary needs it)")
    if kind == "relation" and tags.get("type") not in REL_TYPES:
        out.append(f"relation {ident} has type={tags.get('type')!r}, expected boundary/multipolygon")
    if kind == "way" and not closed:
        out.append(f"way {ident} is not a closed ring")
    names = {unicodedata.normalize("NFC", tags[k]) for k in NAME_KEYS if tags.get(k)}
    if name not in names:
        out.append(f"{kind} {ident} name {sorted(names)} does not match registry name {name!r}")
    return out


def scan(pbf: Path) -> dict[tuple[str, int], dict[str, Any]]:
    """{(kind, id): {tags, closed, count}} for every relation and way in a (small) OSM file."""
    import osmium
    found: dict[tuple[str, int], dict[str, Any]] = {}

    def note(kind: str, ident: int, tags: dict[str, str], closed: bool | None) -> None:
        item = found.setdefault((kind, ident), {"tags": tags, "closed": closed, "count": 0})
        item["count"] += 1

    class _H(osmium.SimpleHandler):
        def way(self, w):
            refs = [n.ref for n in w.nodes]
            note("way", w.id, {t.k: t.v for t in w.tags}, len(refs) >= 4 and refs[0] == refs[-1])

        def relation(self, r):
            note("relation", r.id, {t.k: t.v for t in r.tags}, None)

    _H().apply_file(str(pbf))
    return found


def _osmium(*args: str) -> None:
    proc = subprocess.run(["osmium", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise ParkBoundaryError(f"osmium {args[0]} failed: {proc.stderr.strip()[:300]}")


def keep_boundary(entry: dict, raw_pbf: Path, aoi_pbf: Path) -> dict:
    """Merge the entry's park boundary from the raw PBF into the AOI PBF; the evidence record."""
    if "park_boundary" not in entry:
        raise ParkBoundaryError(f"{entry['id']}: no park_boundary in the registry")
    validate_park_boundary(entry["park_boundary"], f"{entry['id']}: park_boundary")
    kind, ident = (("way", entry["way_id"]) if "way_id" in entry else ("relation", entry["relation_id"]))
    if not raw_pbf.is_file() or not aoi_pbf.is_file():
        raise ParkBoundaryError("raw or AOI PBF is missing")
    letter = "w" if kind == "way" else "r"
    with tempfile.TemporaryDirectory(dir=str(aoi_pbf.parent)) as tmp:
        piece = Path(tmp) / ("piece." + aoi_pbf.name)
        try:  # getid exits non-zero when the id is not in the file
            _osmium("getid", "-r", "--overwrite", "-o", str(piece), str(raw_pbf), f"{letter}{ident}")
        except ParkBoundaryError as error:
            raise ParkBoundaryError(f"{entry['id']}: {kind} {ident} is not in the raw PBF ({error})") from None
        info = scan(piece).get((kind, ident))
        if info is None:
            raise ParkBoundaryError(f"{entry['id']}: {kind} {ident} is not in the raw PBF")
        if info["count"] != 1:
            raise ParkBoundaryError(f"{entry['id']}: {kind} {ident} occurs {info['count']} times (not unique)")
        problems = tag_problems(kind, ident, info["tags"], entry["name"], info["closed"])
        if problems:
            raise ParkBoundaryError(f"{entry['id']}: " + "; ".join(problems))
        action = "already-present"
        if (kind, ident) not in scan(aoi_pbf):
            merged = Path(tmp) / ("merged." + aoi_pbf.name)
            _osmium("merge", "--overwrite", "-o", str(merged), str(aoi_pbf), str(piece))
            if (kind, ident) not in scan(merged):
                raise ParkBoundaryError(f"{entry['id']}: {kind} {ident} is missing after the merge")
            os.replace(merged, aoi_pbf)
            action = "merged"
    return {"area": entry["id"], "kind": kind, "id": ident, "action": action,
            "reason": entry["park_boundary"]["reason"]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--registry", required=True)
    ap.add_argument("--area-id", required=True)
    ap.add_argument("--raw-pbf", required=True)
    ap.add_argument("--aoi-pbf", required=True)
    ap.add_argument("--report", required=True)
    args = ap.parse_args(argv)
    try:
        registry = json.loads(Path(args.registry).read_text(encoding="utf-8"))
        entry = next((a for a in registry["areas"] if a["id"] == args.area_id), None)
        if entry is None:
            raise ParkBoundaryError(f"{args.area_id}: not in the registry")
        evidence = keep_boundary(entry, Path(args.raw_pbf), Path(args.aoi_pbf))
    except (ParkBoundaryError, OSError, ValueError, KeyError) as error:
        print(f"SAFETY BLOCK: {error}", file=sys.stderr)
        return 2
    Path(args.report).write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(f"park_boundary: {evidence['kind']} {evidence['id']} {evidence['action']} in {Path(args.aoi_pbf).name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
