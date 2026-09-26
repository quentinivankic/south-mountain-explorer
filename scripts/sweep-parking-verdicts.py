#!/usr/bin/env python3
"""Apply `public/areas/parking-verdicts.json` to already-published geom.

The sidecar holds the per-lot KEEP/DROP verdicts from the vision adjudication
(task #53). `build-parking-verdicts.py` produces it; this applies the DROPs to
the `parking` arrays inside `public/areas/geom/*.json`. Same arrangement as the
non-hiking trails sidecar: a sweep so a verdict reaches shipped data in
minutes, a gate in `add-parking.py` so the next parking roll does not bring the
lot back, and the global pool builder honouring it as well — because the app
merges an area's OWN lots with the pool (`Area.mergingPool`), a lot removed
from the pool alone would still draw from the area's geom.

    python3 scripts/sweep-parking-verdicts.py --dry-run
    python3 scripts/sweep-parking-verdicts.py

Every area is checked, not just the ones that were adjudicated: a judged lot
can ship inside any area whose bbox reaches it (Zion Wilderness verdicts land
on lots that `zion-national-park-ut` carries). A lot is matched by a JUDGED OSM
id when the geom carries one, else by footprint — see `scripts/_parking_verdicts.py`.

An area is refused by default when a curation sidecar would remove every lot.
A tiny committed reviewed-empty policy may permit an exact, manually reviewed
(key, reason, count) signature; any future parking or verdict drift fails closed
and restores the refusal. Reversible by design — delete the entry from the store,
rebuild the sidecar, and the next parking roll brings the lot back (or `git revert`
the sweep commit).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _parking_verdicts as pv  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Exact singleton populations manually double-reviewed on 2026-09-26. Both
# independent reviews agreed the retained lot is not public, and a search of all
# validated KEEPs found no replacement within the app's 805 m rule. Binding the
# approval to verdict key + reason + multiplicity means any future lot/verdict
# change is refused again instead of inheriting a broad slug-level exception.
_REVIEWED_EMPTY_SIGNATURES = {
    "mesa-valley-open-space-co": (("way/58294967", "not-public"),),
    "promntory-point-open-space-co": (("way/1206954210", "not-public"),),
    "sondermann-park-co": (("way/58294967", "not-public"),),
}


def sweep(geom_dir: str, verdicts: pv.Verdicts, dry_run: bool, *,
          reviewed_empty_signatures: dict[str, tuple] | None = None) -> dict:
    reasons: Counter = Counter()
    removed: list[tuple[str, dict, dict]] = []
    refused: list[tuple[str, int]] = []
    reviewed_empty: list[tuple[str, int, tuple]] = []
    changed: list[str] = []
    if reviewed_empty_signatures is None:
        reviewed_empty_signatures = _REVIEWED_EMPTY_SIGNATURES
    for f in sorted(os.listdir(geom_dir)):
        if not f.endswith(".json"):
            continue
        slug = f[:-5]
        path = os.path.join(geom_dir, f)
        try:
            d = json.load(open(path))
        except Exception:                  # noqa: BLE001
            continue
        lots = d.get("parking") or []
        if not lots:
            continue
        gone = [(lot, verdicts.drop_for(lot)) for lot in lots]
        gone = [(lot, e) for lot, e in gone if e is not None]
        if not gone:
            continue
        if len(gone) == len(lots):
            actual = tuple(sorted((entry["_key"], entry.get("reason") or "?")
                                  for _, entry in gone))
            expected = reviewed_empty_signatures.get(slug)
            if actual != expected:
                refused.append((slug, len(gone)))
                continue
            reviewed_empty.append((slug, len(gone), actual))
        doomed = {id(lot) for lot, _ in gone}
        keep = [lot for lot in lots if id(lot) not in doomed]
        for lot, e in gone:
            reasons[e.get("reason") or "?"] += 1
            removed.append((slug, lot, e))
        changed.append(slug)
        if not dry_run:
            d["parking"] = keep
            json.dump(d, open(path, "w"))
    return {"reasons": reasons, "removed": removed, "refused": refused,
            "reviewed_empty": reviewed_empty, "changed": changed}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="apply the parking verdicts sidecar to geom")
    ap.add_argument("--geom-dir", default=os.path.join(_ROOT, "public", "areas", "geom"))
    ap.add_argument("--sidecar", default=pv.DEFAULT_PATH)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    verdicts = pv.load(args.sidecar)
    if verdicts is None:
        print("nothing to apply", file=sys.stderr)
        return 1
    r = sweep(args.geom_dir, verdicts, args.dry_run)

    print(f"{'DRY-RUN — ' if args.dry_run else ''}removed {len(r['removed'])} "
          f"lot(s) from {len(r['changed'])} area(s)")
    for reason, n in r["reasons"].most_common():
        print(f"  {n:5}  {reason}")
    print("\nremoved:")
    for slug, lot, e in r["removed"]:
        d = pv.haversine_m(lot["lat"], lot["lon"], e["lat"], e["lon"])
        name = lot.get("name") or e.get("name") or "(unnamed)"
        # Show the axis that failed, which is what the reason label summarises.
        axis = {"not-public": "public", "not-a-lot": "exists"}.get(e.get("reason"), "serves")
        why = e["evidence"].get(axis) or next(iter(e["evidence"].values()), "")
        print(f"   {slug:34} {name[:30]:32} {e['_key']:20} {d:5.1f} m  "
              f"[{e.get('reason')}] {why}")
    for slug, n, signature in r["reviewed_empty"]:
        sig = ", ".join(f"{key}:{reason}" for key, reason in signature)
        print(f"  REVIEWED-EMPTY {slug} — {n} flagged, exact signature [{sig}]")
    for slug, n in r["refused"]:
        print(f"  !! REFUSING to empty {slug} — {n} flagged, 0 would remain. "
              f"Review the verdicts for this area.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
