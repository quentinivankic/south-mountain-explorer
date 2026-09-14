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

Deterministic: same stores in, byte-identical sidecar out. Re-run it whenever a
store changes; the sidecar is committed, the stores are the source.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _parking_verdicts as pv  # noqa: E402

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
    """(facility by (slug, fid), facility by OSM id -> list of (slug, facility))."""
    by_fid: dict[tuple[str, int], dict] = {}
    by_osm: dict[str, list[tuple[str, dict]]] = {}
    for f in sorted(glob.glob(os.path.join(data_dir, "*_dossier.json"))):
        d = json.load(open(f))
        slug = d.get("slug") or os.path.basename(f)[: -len("_dossier.json")]
        for fac in d.get("facilities") or []:
            by_fid[(slug, fac["fid"])] = fac
            fac.setdefault("_slug", slug)
            for o in fac.get("osm") or []:
                by_osm.setdefault(o, []).append((slug, fac))
    return by_fid, by_osm


def embedded_facility(v: dict) -> dict | None:
    """A store entry that carries its own position places itself: the geometry
    the judge was shown is the record, and no dossier needs committing. Returns
    a facility-shaped dict, or None when the entry has no `lat`/`lon`."""
    if v.get("lat") is None or v.get("lon") is None:
        return None
    return {
        "lat": v["lat"], "lon": v["lon"],
        "rings": v.get("rings") or [],
        "osm": list(v.get("osm") or []),
        "tags_union": {"name": v.get("name")} if v.get("name") else {},
        "_slug": v.get("area"),
    }


def facility_for(v: dict, slug_hint: str | None, by_fid: dict, by_osm: dict) -> dict | None:
    emb = embedded_facility(v)
    if emb is not None:
        return emb
    if slug_hint is not None and "fid" in v:
        fac = by_fid.get((slug_hint, v["fid"]))
        if fac is not None:
            return fac
    for o in v.get("osm") or []:
        cands = by_osm.get(o)
        if cands:
            area = v.get("area")
            for s, fac in cands:
                if s == area:
                    return fac
            return cands[0][1]
    return None


def build(data_dir: str = _DATA) -> tuple[dict, list[str], list[str]]:
    """(sidecar document, skipped entries, folded duplicates). A skip is a
    verdict the sidecar could not place — it must be fixed in the store, never
    ignored; a fold is a second entry for a cluster already in."""
    by_fid, by_osm = load_dossiers(data_dir)
    lots: dict[str, dict] = {}
    notes: list[str] = []
    folded: list[str] = []
    claimed: dict[str, str] = {}          # osm id -> key that already holds it
    for store, slug_hint, judged in STORES:
        path = os.path.join(data_dir, store)
        for key, v in json.load(open(path)).items():
            if not isinstance(v, dict) or v.get("verdict") not in ("KEEP", "DROP", "REVIEW"):
                continue
            fac = facility_for(v, slug_hint, by_fid, by_osm)
            if fac is None:
                notes.append(f"{store}:{key} has no dossier position — skipped")
                continue
            # The store's ids first (the elements the judge was shown), then
            # every other member of the dossier cluster, so a member way that
            # rolls with its own id later still matches by id, not by fallback.
            osm = list(v.get("osm") or [])
            osm += [o for o in fac.get("osm") or [] if o not in osm]
            if not osm:
                notes.append(f"{store}:{key} has no OSM id — skipped")
                continue
            dup = next((claimed[o] for o in osm if o in claimed), None)
            if dup is not None:
                # A cluster judged under two of its member ids (New England
                # stores one entry per id): same lot, same call — fold it. Two
                # entries for one lot that DISAGREE are a store defect, and
                # silently letting the first store win would hide it.
                if lots[dup]["verdict"] != v["verdict"]:
                    notes.append(f"{store}:{key} says {v['verdict']} but {dup} already "
                                 f"holds this cluster as {lots[dup]['verdict']} — resolve in the store")
                    continue
                folded.append(f"{store}:{key} is the cluster already held by {dup}")
                continue
            rings = fac.get("rings") or ([fac["ring"]] if fac.get("ring") else [])
            entry = {
                "verdict": v["verdict"],
                "reason": _reason(v),
                "lat": round(float(fac["lat"]), 6),
                "lon": round(float(fac["lon"]), 6),
                # The mapped footprint, so consumers can tell "this lot" from
                # "a lot 30 m away" without a radius that would swallow both.
                "rings": [[[round(float(p[0]), 6), round(float(p[1]), 6)] for p in ring]
                          for ring in rings if ring],
                "osm": osm,
                "name": (fac.get("tags_union") or {}).get("name"),
                "area": v.get("area") or fac.get("_slug"),
                "prior": v.get("prior"),
                "confidence": v.get("confidence"),
                "evidence": {
                    ax: (v.get(ax) or {}).get("evidence")
                    for ax in ("exists", "public", "serves")
                    if (v.get(ax) or {}).get("evidence")
                },
                "judged": v.get("judged") or judged,
                "src": v.get("src") or store,
            }
            if v.get("coverage_gap"):
                entry["coverage_gap"] = True
            if v.get("override"):
                # A human flipped the judge's call (merge_drafts.py --set). The
                # flip is part of the record: who, when, from what, and why.
                entry["override"] = v["override"]
            if v.get("resolve_hint"):
                # Required on a REVIEW; kept on any verdict that has one
                # (Zion's "sibling Kolob pullouts dropped — cars are present
                # here" is the record of a judgement call, not a leftover).
                entry["resolve_hint"] = v["resolve_hint"]
            lots[osm[0]] = entry
            for o in osm:
                claimed[o] = osm[0]
    doc = {
        "version": pv.VERSION,
        "about": ("Per-lot parking verdicts from the vision adjudication (task #53). "
                  "Generated by scripts/build-parking-verdicts.py from "
                  "scripts/parking-adjud/data; edit the stores, not this file. "
                  "Consumers match a shipped lot by a judged OSM id when it carries "
                  "one, else by footprint (rings) and position — see "
                  "scripts/_parking_verdicts.py."),
        "lots": {k: lots[k] for k in sorted(lots)},
    }
    return doc, notes, folded


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default=_DATA)
    ap.add_argument("--out", default=pv.DEFAULT_PATH)
    ap.add_argument("--check", action="store_true",
                    help="do not write; exit 1 if the committed sidecar differs")
    args = ap.parse_args(argv)

    doc, notes, folded = build(args.data_dir)
    text = json.dumps(doc, indent=1, sort_keys=False, ensure_ascii=False) + "\n"

    lots = doc["lots"]
    verdicts = Counter(e["verdict"] for e in lots.values())
    reasons = Counter(e["reason"] for e in lots.values() if e["verdict"] == "DROP")
    areas = Counter(e["area"] for e in lots.values())
    print(f"parking verdicts: {len(lots)} judged lot(s) — "
          + ", ".join(f"{n} {k}" for k, n in sorted(verdicts.items())))
    print("  drop reasons: " + ", ".join(f"{n} {k}" for k, n in reasons.most_common()))
    print("  by area: " + ", ".join(f"{s} {n}" for s, n in sorted(areas.items())))
    with_ring = sum(1 for e in lots.values() if e["rings"])
    print(f"  footprints: {with_ring} with a mapped polygon, "
          f"{len(lots) - with_ring} node-only ({pv.NEAR_M:.0f} m near-circle)")
    for n in folded:
        print(f"  folded: {n}")
    if notes:
        # A verdict that cannot be placed is a store problem; refusing to write
        # is what keeps `--check` honest about it.
        for n in notes:
            print(f"  !! {n}", file=sys.stderr)
        print(f"  !! {len(notes)} verdict(s) could not be placed — fix the store", file=sys.stderr)
        return 1

    if args.check:
        current = open(args.out).read() if os.path.exists(args.out) else ""
        if current != text:
            print(f"  !! {args.out} is out of date — run without --check", file=sys.stderr)
            return 1
        print(f"  {args.out} is current")
        return 0
    with open(args.out, "w") as fh:
        fh.write(text)
    print(f"  wrote {args.out} ({len(text) / 1e3:.1f} kB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
