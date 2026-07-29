#!/usr/bin/env python3
"""Fan-in for the pre-ownership parking sidecar (task #44).

`trailforge-parking.yml` runs ONE JOB PER STATE, so `--pool-sidecar` produces one
partial sidecar per state rather than a single national file. This merges those
partials into the committed `public/areas/parking-pool.json`, the same way
`merge-published-geom.py` merges per-state geom for a publish.

STRICTLY ADDITIVE, for the same reason that one is: a state whose job failed or
timed out must not lose the entry it already had. Only states PRESENT in the
partials are replaced; every other state is carried through untouched. And a
state that arrives empty while it currently holds lots is refused outright — an
empty answer is far more often a fetch that quietly returned nothing than a real
change, and the cost of being wrong is pins vanishing from the map.

    python3 scripts/merge-parking-pool-sidecars.py \
        --into public/areas/parking-pool.json artifacts/pool-*/*.json
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT = _ROOT / "public" / "areas" / "parking-pool.json"

# add-parking.py owns the sidecar's shape and its merge rule; importing it keeps
# one definition rather than two that can drift. Its shapely import is lazy
# (inside the boundary assembler), so this costs no dependency.
_spec = importlib.util.spec_from_file_location(
    "add_parking", Path(__file__).resolve().parent / "add-parking.py")
_ap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ap)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--into", default=str(_DEFAULT),
                    help="the committed sidecar to merge into (created if absent)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("partials", nargs="*",
                    help="per-state sidecars from add-parking.py --pool-sidecar")
    args = ap.parse_args(argv)

    existing = _ap.load_pool_sidecar(args.into)
    print(f"existing sidecar: {sum(len(v) for v in existing.values())} lot(s) "
          f"across {len(existing)} state(s)")

    fresh: dict[str, list[dict]] = {}
    read = 0
    for path in args.partials:
        if not os.path.exists(path):
            print(f"  {path}: missing — skipped")
            continue
        part = _ap.load_pool_sidecar(path)
        if not part:
            print(f"  {path}: no states — skipped")
            continue
        for code, lots in part.items():
            # Two partials naming the same state would mean a matrix ran it
            # twice; last one wins, which is also what a re-dispatch means.
            fresh[code] = lots
            read += len(lots)
        print(f"  {path}: {', '.join(sorted(part))} "
              f"({sum(len(v) for v in part.values())} lot(s))")

    if not fresh:
        # Zero partials is the one case that is always an error: the caller asked
        # for a merge and there is nothing to merge, so silently rewriting the
        # file would be a no-op dressed as success.
        print("::error::no usable partial sidecars — nothing merged", file=sys.stderr)
        return 1

    merged, refused = _ap.merge_pool_sidecar(existing, fresh)
    for code in refused:
        print(f"::warning::{code} came back empty but currently holds "
              f"{len(existing[code])} lot(s) — kept the existing entry. "
              "Re-dispatch that state.", file=sys.stderr)

    total = sum(len(v) for v in merged.values())
    carried = sorted(set(existing) - set(fresh))
    print(f"merged: {total} lot(s) across {len(merged)} state(s) — "
          f"{len(fresh)} refreshed, {len(carried)} carried forward unchanged")
    if carried:
        print(f"  carried: {', '.join(carried)}")

    if args.dry_run:
        print("dry run — nothing written")
        return 0

    doc = {"version": _ap.POOL_SIDECAR_VERSION,
           "states": {k: merged[k] for k in sorted(merged)}}
    Path(args.into).write_text(json.dumps(doc, separators=(",", ":")))
    print(f"wrote {args.into} ({os.path.getsize(args.into) / 1e6:.2f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
