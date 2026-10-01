#!/usr/bin/env python3
"""Fold the banked parking verdicts into `public/areas/parking-verdicts.json`.

The adjudication (task #53) leaves its verdicts in stores under
`scripts/parking-adjud/data/`, keyed two different ways (OSM id for the Arizona,
New England and Colorado stores, dossier fid for Zion and Griffith). The early
stores carry no positions and resolve through their committed dossiers; the
Colorado store (the judge fan-out, `tools/merge_drafts.py`) is self-contained,
each entry carrying its own `lat`/`lon`/`rings`/`name`/`judged`, because its
dossiers stay on the homelab. This turns them into ONE committed sidecar the
pipeline can consume:
every judged lot once, with the position and footprint (rings) the consumers
match on (see `scripts/_parking_verdicts.py`), the OSM ids it was judged under, and the
evidence that justified the call — so a wrong verdict is one entry to delete,
the `nonhiking-trails.json` discipline.

    python3 scripts/build-parking-verdicts.py            # writes the sidecar
    python3 scripts/build-parking-verdicts.py --check    # diff against committed

Deterministic: same stores in, byte-identical sidecar out. Both CLI modes hold
canonical shared locks through store/proof/dossier capture, proof and pinned
legacy-baseline validation, compilation, and final sidecar check/write. Live
publication journals block as before; a canonical sidecar write also holds the
parking sweep live-journal resource and refuses while PREPARED exists, so a
cooperating publisher cannot replace a committed transaction's input. An
unproved current row, changed proofless baseline row, or unattested OSM
dictionary key also fails before output. Re-run after a completed store
transaction; the sidecar is committed and the stores are the source.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import stat
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)
sys.path.insert(0, os.path.join(_SCRIPT_DIR, "parking-adjud", "tools"))
import _parking_geom_guard as geom_guard  # noqa: E402
import _parking_verdict_source as pvs  # noqa: E402
import _parking_verdicts as pv  # noqa: E402
import trusted_filesystem as trusted_fs  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA = os.path.join(_ROOT, "scripts", "parking-adjud", "data")

# (store file, dossier slug when the store is fid-keyed, date judged). The
# dates are the run dates recorded in the handoff for the stores that do not
# carry one; an entry's own `judged` (merge_drafts.py stamps it) wins.
STORES = [
    ("phx_verdicts_osm.json", None, "2026-08-01"),
    ("ne_verdicts_osm.json", None, "2026-08-02"),
    ("zion-wilderness-ut_verdicts2.json", "zion-wilderness-ut", "2026-08-01"),
    ("griffith-park-ca_verdicts2.json", "griffith-park-ca", "2026-08-01"),
    ("co_verdicts_osm.json", None, "2026-09-13"),
]

# The two historical stores are keyed by run-local dossier fid. Every other
# store is keyed by a canonical OSM identity. Keep this explicit: a decimal fid
# must never enter the OSM alias graph.
STORE_KEY_KINDS = {
    "phx_verdicts_osm.json": pvs.OSM_KEY,
    "ne_verdicts_osm.json": pvs.OSM_KEY,
    "zion-wilderness-ut_verdicts2.json": pvs.FID_KEY,
    "griffith-park-ca_verdicts2.json": pvs.FID_KEY,
    "co_verdicts_osm.json": pvs.OSM_KEY,
}

# Immutable reviewed exception set for the exact 1,273 proofless source rows
# that predate current authority/attestation/proof publication requirements.
LEGACY_ROW_BASELINE_PATH = os.path.join(
    _ROOT, "scripts", "parking-adjud", "proofless-source-baseline-v1.json"
)
LEGACY_ROW_BASELINE_SHA256 = (
    "4a84784560ddde2a3867c9e0f1be5e303639a66dc28346b0c5faf56d1288c733"
)

# Git-reviewed exact-byte anchor for the complete authoritative publication
# image. This literal must change only with a reviewed successor root.
PUBLICATION_TRUST_ROOT_PATH = os.path.join(
    _ROOT, "scripts", "parking-adjud", "publication-trust-root-v1.json"
)
PUBLICATION_TRUST_ROOT_SHA256 = (
    "43db143447d90f506d42b273c425e0d048e5580e605ac211750114ba350dfd85"
)

ValidatedStoreSnapshot = pvs.ValidatedStoreSnapshot


# A measured distance ("walk 2926 m", "1.4 km", ">1 mile") or the serves-gate's
# own word for a far lot. Anchored on a number so "m" cannot match "mansion".
_DISTANCE_RE = re.compile(r"(\d+(\.\d+)?\s*(m|km|mi|miles?)\b)|fallback|>\s*1\s*mi", re.I)


def _reason(v: dict) -> str | None:
    """Why a DROP dropped, from the first axis that failed: EXISTS, then PUBLIC,
    then SERVES. Uses the handoff's vocabulary — `not-a-lot`, `not-public`,
    `too-far`, `facility-only`. A SERVES failure is `too-far` when its evidence
    quotes a distance and `facility-only` when it names what the lot serves
    instead ("equestrian center grounds"); the evidence string is kept beside
    it either way, so the label is a sort key, not the record. KEEP and REVIEW
    carry the coverage-gap flag instead of a reason."""
    if v.get("verdict") != "DROP":
        return "named-trailhead" if v.get("coverage_gap") else None
    if (v.get("exists") or {}).get("call") == "no":
        return "not-a-lot"
    if (v.get("public") or {}).get("call") == "no":
        return "not-public"
    serves = v.get("serves") or {}
    if serves.get("call") == "no":
        return "too-far" if _DISTANCE_RE.search(serves.get("evidence") or "") else "facility-only"
    return "dropped"


def load_dossiers(data_dir: str) -> tuple[dict, dict]:
    """Compatibility API backed by one validated captured dossier snapshot."""
    specs = _store_specs()
    store_names = tuple(spec.filename for spec in specs)
    with _validated_snapshot_scope(data_dir) as snapshot:
        return snapshot.dossier_indexes_for(data_dir, store_names)


# Compatibility aliases for private tests and tools that exercise the original
# helper surface directly. Canonical builder/replay flow uses the shared
# normalizer below.
embedded_facility = pvs.embedded_facility


def facility_for(v: dict, slug_hint: str | None, by_fid: dict,
                 by_osm: dict) -> dict | None:
    return pvs.facility_for(v, slug_hint, by_fid, by_osm)


def _fold_signature(value: dict, store_name: str | None = None,
                    default_judged: str | None = None) -> dict:
    return pvs.fold_signature(
        value,
        value.get("src") or store_name,
        value.get("judged") or default_judged,
    )


_normalize_store_record = pvs.normalize_store_record


def _store_specs() -> tuple[pvs.StoreSpec, ...]:
    return pvs.configured_store_specs(STORES, STORE_KEY_KINDS)


def _builder_configuration():
    # The test loader does not always register this script in sys.modules.
    return SimpleNamespace(
        STORES=STORES,
        STORE_KEY_KINDS=STORE_KEY_KINDS,
        DATA_PATH=_DATA,
        LEGACY_ROW_BASELINE_PATH=LEGACY_ROW_BASELINE_PATH,
        LEGACY_ROW_BASELINE_SHA256=LEGACY_ROW_BASELINE_SHA256,
        PUBLICATION_TRUST_ROOT_PATH=PUBLICATION_TRUST_ROOT_PATH,
        PUBLICATION_TRUST_ROOT_SHA256=PUBLICATION_TRUST_ROOT_SHA256,
    )


def _replay_module():
    tools = os.path.join(_ROOT, "scripts", "parking-adjud", "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import replay_trust  # noqa: E402  # lazy: replay dynamically loads this builder
    return replay_trust


def _validated_snapshot_scope(
        data_dir: str, *, output_path: str | None = None,
        output_exclusive: bool = False):
    additional = (
        geom_guard.sidecar_publisher_resource_modes(output_path)
        if output_exclusive and output_path is not None else ()
    )
    return _replay_module().validated_store_snapshot(
        Path(data_dir), _builder_configuration(),
        output_path=None if output_path is None else Path(output_path),
        output_exclusive=output_exclusive,
        additional_resource_modes=additional,
    )


def _build_from_store_documents(
        store_documents: dict[str, dict], by_fid: dict, by_osm: dict
) -> tuple[dict, list[str], list[str]]:
    """Private pure compiler for validated stores and captured dossiers."""
    lots: dict[str, dict] = {}
    notes: list[str] = []
    folded: list[str] = []
    claimed: dict[str, str] = {}          # OSM id -> key that already holds it
    signature_by_key: dict[str, dict] = {}
    for spec in _store_specs():
        if spec.filename not in store_documents:
            raise ValueError(f"validated snapshot is missing {spec.filename}")
        values = copy.deepcopy(store_documents[spec.filename])
        if not isinstance(values, dict):
            raise ValueError(f"{spec.filename} must contain an object")
        for source_key, value in values.items():
            if (not isinstance(value, dict)
                    or value.get("verdict") not in ("KEEP", "DROP", "REVIEW")):
                continue
            try:
                normalized = pvs.normalize_store_record(
                    spec, source_key, value, by_fid, by_osm
                )
            except (KeyError, TypeError, ValueError) as error:
                notes.append(f"{error} — skipped")
                continue
            aliases = list(normalized.aliases)
            holders = sorted({claimed[alias] for alias in aliases if alias in claimed})
            duplicate = holders[0] if holders else None
            if duplicate is not None:
                incompatible = [
                    holder for holder in holders
                    if signature_by_key.get(holder) != normalized.fold_signature
                ]
                if incompatible:
                    notes.append(
                        f"{spec.filename}:{source_key} has different complete "
                        f"decision/authority provenance from cluster holder(s) "
                        f"{incompatible} — resolve in the store"
                    )
                    continue
                for holder in holders[1:]:
                    for alias in lots[holder].get("osm") or []:
                        if alias not in lots[duplicate]["osm"]:
                            lots[duplicate]["osm"].append(alias)
                        claimed[alias] = duplicate
                    del lots[holder]
                    del signature_by_key[holder]
                    folded.append(
                        f"{spec.filename}:{source_key} bridges cluster holder "
                        f"{holder} into {duplicate}"
                    )
                for alias in aliases:
                    if alias not in lots[duplicate]["osm"]:
                        lots[duplicate]["osm"].append(alias)
                    claimed[alias] = duplicate
                folded.append(
                    f"{spec.filename}:{source_key} is the cluster already held "
                    f"by {duplicate}"
                )
                continue

            facility = normalized.facility
            rings = facility.get("rings") or (
                [facility["ring"]] if facility.get("ring") else []
            )
            entry = {
                "verdict": value["verdict"],
                "reason": _reason(value),
                "lat": round(float(facility["lat"]), 6),
                "lon": round(float(facility["lon"]), 6),
                # The mapped footprint, so consumers can tell "this lot" from
                # "a lot 30 m away" without a radius that would swallow both.
                "rings": [
                    [[round(float(point[0]), 6), round(float(point[1]), 6)]
                     for point in ring]
                    for ring in rings if ring
                ],
                "osm": aliases,
                "name": (facility.get("tags_union") or {}).get("name"),
                "area": value.get("area") or facility.get("_slug"),
                "prior": value.get("prior"),
                "confidence": value.get("confidence"),
                "evidence": {
                    axis: (value.get(axis) or {}).get("evidence")
                    for axis in ("exists", "public", "serves")
                    if (value.get(axis) or {}).get("evidence")
                },
                "judged": normalized.effective_judged,
                "src": normalized.effective_src,
            }
            if value.get("coverage_gap"):
                entry["coverage_gap"] = True
            if value.get("override"):
                # A human flipped the primary call. Legacy rows came from the
                # retired --set helper; current rows use receipt-bound --decide.
                entry["override"] = value["override"]
            if value.get("resolve_hint"):
                # Required on a REVIEW; kept on any verdict that has one.
                entry["resolve_hint"] = value["resolve_hint"]
            key = aliases[0]
            lots[key] = entry
            signature_by_key[key] = normalized.fold_signature
            for alias in aliases:
                claimed[alias] = key
    document = {
        "version": pv.VERSION,
        "about": ("Per-lot parking verdicts from the vision adjudication (task #53). "
                  "Generated by scripts/build-parking-verdicts.py from "
                  "scripts/parking-adjud/data; edit the stores, not this file. "
                  "Consumers match a shipped lot by a judged OSM id when it carries "
                  "one, else by footprint (rings) and position — see "
                  "scripts/_parking_verdicts.py."),
        "lots": {key: lots[key] for key in sorted(lots)},
    }
    return document, notes, folded


def _build_with_snapshot(
        data_dir: str, snapshot: ValidatedStoreSnapshot
        ) -> tuple[dict, list[str], list[str]]:
    """Compile inside the active lease that issued ``snapshot``."""
    if type(snapshot) is not ValidatedStoreSnapshot:
        raise TypeError("compiler requires an exact ValidatedStoreSnapshot capability")
    specs = _store_specs()
    store_names = tuple(spec.filename for spec in specs)
    documents = snapshot.documents_for(data_dir, store_names)
    by_fid, by_osm = snapshot.dossier_indexes_for(data_dir, store_names)
    return _build_from_store_documents(documents, by_fid, by_osm)


def build(data_dir: str = _DATA) -> tuple[dict, list[str], list[str]]:
    """Return a pure document compiled during this call's own locked lease."""
    with _validated_snapshot_scope(data_dir) as snapshot:
        return _build_with_snapshot(data_dir, snapshot)


def serialize(document: dict) -> str:
    """Canonical committed bytes for the generated sidecar."""
    return json.dumps(
        document, indent=1, sort_keys=False, ensure_ascii=False, allow_nan=False
    ) + "\n"


def _read_text_nofollow(path: str) -> str:
    absolute = os.path.abspath(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(absolute, flags)
    try:
        value = os.fstat(descriptor)
        if not stat.S_ISREG(value.st_mode):
            raise ValueError(f"not a regular output file: {absolute}")
        chunks = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks).decode("utf-8")
    finally:
        os.close(descriptor)


def _atomic_write_text(path: str, text: str) -> None:
    """Replace output durably inside its trusted, lock-serialized parent."""
    trusted_fs.atomic_write_bytes(Path(path), text.encode("utf-8"))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--data-dir", default=_DATA)
    parser.add_argument("--out", default=pv.DEFAULT_PATH)
    parser.add_argument(
        "--check", action="store_true",
        help="do not write; exit 1 if the committed sidecar differs",
    )
    args = parser.parse_args(argv)

    try:
        geom_guard.validate_sidecar_output_path(args.out)
        # The context owns every shared store lock through compilation and the
        # final check comparison or atomic replacement. Every return unwinds it.
        with _validated_snapshot_scope(
                args.data_dir, output_path=args.out,
                output_exclusive=not args.check) as snapshot:
            if not args.check:
                geom_guard.require_sidecar_publish_allowed(args.out)
            document, notes, folded = _build_with_snapshot(
                args.data_dir, snapshot
            )
            text = serialize(document)

            lots = document["lots"]
            verdicts = Counter(entry["verdict"] for entry in lots.values())
            reasons = Counter(
                entry["reason"] for entry in lots.values()
                if entry["verdict"] == "DROP"
            )
            areas = Counter(entry["area"] for entry in lots.values())
            print(
                f"parking verdicts: {len(lots)} judged lot(s) — "
                + ", ".join(
                    f"{count} {name}"
                    for name, count in sorted(verdicts.items())
                )
            )
            print(
                "  drop reasons: "
                + ", ".join(
                    f"{count} {name}" for name, count in reasons.most_common()
                )
            )
            print(
                "  by area: "
                + ", ".join(
                    f"{slug} {count}" for slug, count in sorted(areas.items())
                )
            )
            with_ring = sum(1 for entry in lots.values() if entry["rings"])
            print(
                f"  footprints: {with_ring} with a mapped polygon, "
                f"{len(lots) - with_ring} node-only ({pv.NEAR_M:.0f} m near-circle)"
            )
            for message in folded:
                print(f"  folded: {message}")
            if notes:
                for message in notes:
                    print(f"  !! {message}", file=sys.stderr)
                print(
                    f"  !! {len(notes)} verdict(s) could not be placed — fix the store",
                    file=sys.stderr,
                )
                return 1

            if args.check:
                try:
                    current = _read_text_nofollow(args.out)
                except FileNotFoundError:
                    current = ""
                if current != text:
                    print(
                        f"  !! {args.out} is out of date — run without --check",
                        file=sys.stderr,
                    )
                    return 1
                print(f"  {args.out} is current")
                return 0

            _atomic_write_text(args.out, text)
            print(f"  wrote {args.out} ({len(text) / 1e3:.1f} kB)")
            return 0
    except geom_guard.LiveSweepInProgress as error:
        print(f"  !! REFUSING sidecar publish: {error}", file=sys.stderr)
        return 2
    except (KeyError, OSError, TypeError, ValueError) as error:
        print(
            f"  !! parking verdict source validation failed: {error}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
