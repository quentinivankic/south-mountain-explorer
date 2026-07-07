#!/usr/bin/env python3
"""Licensing gate + registry validator (spec §2, §9).

This module is the single, authoritative implementation of the
**fail-closed licensing gate**. Nothing else in the pipeline is allowed
to decide whether a source may ship — every writer of `dist/` tiles must
route through `is_shippable()` / `assert_region_shippable()` here.

The rule (spec §0, §2, §7, §9):

    A source may contribute geometry to dist/ tiles ONLY IF
        commercial_ok is exactly True  AND  redistribute_ok is exactly True.

    Anything else — False, null/None, or missing — is NOT shippable.
    That is what "fail closed" means: the ABSENCE of a positive
    permission is a denial, never a default-allow. A source whose terms
    are still being verified (null flags) stays out of the tiles until a
    human sets both flags to true.

This is deliberately dumb and conservative. The licensing gate answers
exactly one question — "do we have the legal right to redistribute this
geometry?" — and nothing about data quality or curation. Those are the
separate confidence/curation concerns (spec §0, §4) and must never be
conflated with this gate.

Usage:
    python3 validate_registry.py [--registry PATH]
        Validate structure + print the shippable/blocked source table.
        Exits non-zero if the registry is structurally invalid.

    python3 validate_registry.py --require osm nz_doc nz_linz
        Assert every listed source id is shippable. Exits non-zero (and
        names the offender) if any is blocked — use this in CI before a
        region build so a fail-closed source can never slip into a tile.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

DEFAULT_REGISTRY = Path(__file__).with_name("registry.json")

# Every source entry must carry these keys for the gate to reason about it.
REQUIRED_FIELDS = (
    "id",
    "name",
    "kind",
    "license",
    "commercial_ok",
    "redistribute_ok",
    "attribution",
)


class RegistryError(Exception):
    """Structural problem with the registry itself (not a licensing denial)."""


def load_registry(path: str | Path = DEFAULT_REGISTRY) -> dict[str, Any]:
    """Read + JSON-parse the registry. Raises RegistryError on I/O/parse failure."""
    p = Path(path)
    try:
        with p.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError as exc:
        raise RegistryError(f"registry not found: {p}") from exc
    except json.JSONDecodeError as exc:
        raise RegistryError(f"registry is not valid JSON ({p}): {exc}") from exc
    if not isinstance(data, dict) or "sources" not in data:
        raise RegistryError("registry must be an object with a 'sources' array")
    return data


def sources_by_id(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for src in registry.get("sources", []):
        sid = src.get("id")
        if sid:
            out[sid] = src
    return out


def prohibited_ids(registry: dict[str, Any]) -> set[str]:
    return {p.get("id") for p in registry.get("prohibited_sources", []) if p.get("id")}


def is_shippable(source: dict[str, Any]) -> bool:
    """The gate. True IFF the source is legally clear to redistribute.

    Fail-closed: both flags must be *exactly* boolean True. `None`
    (unverified), `False`, or a missing key all deny. We use `is True`
    rather than a truthiness test on purpose so a stray truthy string or
    1 can never sneak a source through.
    """
    return source.get("commercial_ok") is True and source.get("redistribute_ok") is True


def block_reason(source: dict[str, Any]) -> str | None:
    """Human-readable reason a source is blocked, or None if it ships."""
    if is_shippable(source):
        return None
    c, r = source.get("commercial_ok"), source.get("redistribute_ok")
    bad = []
    if c is not True:
        bad.append(f"commercial_ok={c!r}")
    if r is not True:
        bad.append(f"redistribute_ok={r!r}")
    return "fail-closed: " + ", ".join(bad)


def shippable_source_ids(registry: dict[str, Any]) -> set[str]:
    return {sid for sid, src in sources_by_id(registry).items() if is_shippable(src)}


def validate_structure(registry: dict[str, Any]) -> list[str]:
    """Return a list of structural errors (empty == valid). Does not raise."""
    errors: list[str] = []
    seen: set[str] = set()
    prohibited = prohibited_ids(registry)

    for i, src in enumerate(registry.get("sources", [])):
        where = f"sources[{i}]"
        for field in REQUIRED_FIELDS:
            if field not in src:
                errors.append(f"{where}: missing required field '{field}'")
        sid = src.get("id")
        if sid:
            if sid in seen:
                errors.append(f"{where}: duplicate source id '{sid}'")
            seen.add(sid)
            if sid in prohibited:
                errors.append(
                    f"{where}: '{sid}' is listed in prohibited_sources and must "
                    f"NOT appear in the shippable sources array"
                )
        # A source that claims to ship must actually be permitted to.
        for flag in ("commercial_ok", "redistribute_ok"):
            val = src.get(flag)
            if val not in (True, False, None):
                errors.append(f"{where}: {flag} must be true/false/null, got {val!r}")

    if not registry.get("_meta", {}).get("fail_closed") is True:
        errors.append("_meta.fail_closed must be true (the gate depends on it)")

    return errors


def assert_region_shippable(registry: dict[str, Any], required: list[str]) -> list[str]:
    """Return the list of required source ids that are NOT shippable.

    Empty return == every required source may ship. A non-empty return is
    a hard build stop: those sources would fail the licensing gate.
    """
    by_id = sources_by_id(registry)
    blocked: list[str] = []
    for sid in required:
        src = by_id.get(sid)
        if src is None or not is_shippable(src):
            blocked.append(sid)
    return blocked


def _print_table(registry: dict[str, Any]) -> None:
    rows = sources_by_id(registry)
    width = max((len(s) for s in rows), default=4)
    print(f"{'SOURCE'.ljust(width)}  SHIP  DETAIL")
    for sid in sorted(rows):
        src = rows[sid]
        reason = block_reason(src)
        mark = "yes " if reason is None else "NO  "
        detail = src.get("license", "") if reason is None else reason
        print(f"{sid.ljust(width)}  {mark}  {detail}")
    ship = len(shippable_source_ids(registry))
    print(f"\n{ship}/{len(rows)} sources shippable; "
          f"{len(prohibited_ids(registry))} prohibited sources on the blocklist.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Registry validator + fail-closed licensing gate")
    ap.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    ap.add_argument("--require", nargs="*", metavar="SOURCE_ID",
                    help="assert every listed source id is shippable; non-zero exit if not")
    args = ap.parse_args(argv)

    try:
        registry = load_registry(args.registry)
    except RegistryError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    errors = validate_structure(registry)
    if errors:
        print("REGISTRY INVALID:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 2

    if args.require:
        blocked = assert_region_shippable(registry, args.require)
        if blocked:
            print("LICENSING GATE FAILED — these sources are not shippable "
                  "(fail-closed):", file=sys.stderr)
            by_id = sources_by_id(registry)
            for sid in blocked:
                src = by_id.get(sid)
                reason = "unknown source id" if src is None else block_reason(src)
                print(f"  - {sid}: {reason}", file=sys.stderr)
            return 1
        print(f"OK: all {len(args.require)} required sources are shippable.")
        return 0

    _print_table(registry)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
