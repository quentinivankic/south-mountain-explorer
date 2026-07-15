#!/usr/bin/env python3
"""Delete ORPHANED area objects from the Cloudflare R2 bucket — the
geom / silhouette files whose area slug is no longer in the shipped
`public/areas/index.json`.

WHY THIS EXISTS. `sync-geom-to-r2.yml` uses `aws s3 sync` WITHOUT
`--delete` (a blanket delete would nuke index.json / trail-search.json /
trail-shapes.json / the silhouettes prefix, which live alongside the geom
at the bucket root). So when an area is dropped from the index — a stale
seed row pruned, a red-flag area removed, a curation/degenerate filter
tightened — its geom/silhouette lingers on R2 forever. The app never
requests those keys (it fetches by index id), but they're dead storage.
This script diffs the bucket against the CURRENT index and removes only
the objects no id references.

HISTORY. v1 deleted "any object not in index" and was scoped down to a
Europe-only allowlist after the EU-data removal, out of caution. This
version restores the general behavior on purpose (task #36) — but with
hard safety belts (below), because deleting against a bad/empty index
would wipe the bucket.

SAFETY BELTS (all abort before any delete):
  * the index must load and carry a sane number of ids (>= MIN_INDEX);
  * per-area objects that ARE in the index are never touched;
  * the protected root files (index.json, trail-search.json,
    trail-shapes.json) and any non-`silhouettes/` prefix are never touched;
  * if orphans exceed MAX_FRAC of the area-objects, abort unless --force
    (guards against an index that silently loaded short).

Bucket layout (see .github/workflows/sync-geom-to-r2.yml):
  s3://trekdex-areas/<slug>.json              geom, at root
  s3://trekdex-areas/silhouettes/<slug>.json  silhouettes
  s3://trekdex-areas/index.json               the index (NEVER deleted)
  s3://trekdex-areas/trail-search.json        (NEVER deleted)
  s3://trekdex-areas/trail-shapes.json        (NEVER deleted)

Dry-run by default. Pass --apply to actually delete. Run from the repo
root (needs public/areas/index.json); the workflow checks the repo out.

    python3 scripts/cleanup-r2-orphans.py            # dry run
    python3 scripts/cleanup-r2-orphans.py --apply    # delete
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

BUCKET = "trekdex-areas"
DEFAULT_INDEX = Path(__file__).resolve().parent.parent / "public" / "areas" / "index.json"

# Root objects that are NOT per-area geom and must never be deleted.
PROTECTED_KEYS = {"index.json", "trail-search.json", "trail-shapes.json"}

# Safety thresholds.
MIN_INDEX = 1000     # a healthy index has ~30k rows; refuse to run if it loaded short
MAX_FRAC = 0.60      # abort if > this fraction of area-objects look orphaned (unless --force)


def endpoint() -> str:
    acct = os.environ.get("R2_ACCOUNT_ID", "")
    if not acct:
        sys.exit("R2_ACCOUNT_ID not set")
    return f"https://{acct}.r2.cloudflarestorage.com"


def region_code(slug: str) -> str:
    """Region code for an area slug (for the dry-run breakdown only).
    Canadian provinces are `<name>-ca-xx` — check that FIRST so the
    `-sk` / `-nl` suffixes don't shadow CA-SK / CA-NL."""
    parts = slug.rsplit("-", 2)
    if len(parts) == 3 and parts[1] == "ca":
        return f"CA-{parts[2].upper()}"
    return slug.rsplit("-", 1)[-1].upper()


def slug_for_key(key: str) -> str | None:
    """The area slug a key belongs to, or None if it isn't a per-area
    object we're allowed to consider for deletion (protected root files,
    unknown prefixes)."""
    if not key.endswith(".json") or key in PROTECTED_KEYS:
        return None
    if key.startswith("silhouettes/"):
        return key[len("silhouettes/"):-len(".json")]
    if "/" in key:
        return None   # unknown prefix — leave it alone
    return key[:-len(".json")]


def load_index_ids(path: Path) -> set[str]:
    data = json.loads(path.read_text())
    rows = data["areas"] if isinstance(data, dict) and "areas" in data else data
    ids: set[str] = set()
    for r in rows:
        if isinstance(r, list) and r:
            ids.add(r[0])
        elif isinstance(r, dict) and r.get("id"):
            ids.add(r["id"])
    return ids


def list_keys(ep: str) -> list[str]:
    keys: list[str] = []
    token: str | None = None
    while True:
        cmd = ["aws", "s3api", "list-objects-v2",
               "--bucket", BUCKET, "--endpoint-url", ep,
               "--output", "json", "--max-items", "1000"]
        if token:
            cmd += ["--starting-token", token]
        out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
        data = json.loads(out) if out.strip() else {}
        keys.extend(obj["Key"] for obj in data.get("Contents", []))
        token = data.get("NextToken")
        if not token:
            break
    return keys


def delete_keys(ep: str, keys: list[str]) -> None:
    for i in range(0, len(keys), 1000):
        batch = keys[i:i + 1000]
        payload = json.dumps({"Objects": [{"Key": k} for k in batch], "Quiet": True})
        subprocess.run(
            ["aws", "s3api", "delete-objects",
             "--bucket", BUCKET, "--endpoint-url", ep, "--delete", payload],
            check=True,
        )
        print(f"  deleted batch of {len(batch)}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="Actually delete. Without this, dry-run only.")
    ap.add_argument("--index", default=str(DEFAULT_INDEX), help="Path to the shipped index.json.")
    ap.add_argument("--force", action="store_true",
                    help="Bypass the MAX_FRAC safety abort (use only after eyeballing a dry run).")
    args = ap.parse_args()

    index_ids = load_index_ids(Path(args.index))
    print(f"Index carries {len(index_ids)} area ids ({args.index}).")
    if len(index_ids) < MIN_INDEX:
        sys.exit(f"Index has only {len(index_ids)} ids (< {MIN_INDEX}) — refusing to run "
                 f"(a short/empty index would flag the whole bucket as orphaned).")

    ep = endpoint()
    keys = list_keys(ep)
    print(f"Bucket has {len(keys)} objects.")

    area_objs, orphans = 0, []
    for k in keys:
        slug = slug_for_key(k)
        if slug is None:
            continue          # protected / non-area object
        area_objs += 1
        if slug not in index_ids:
            orphans.append(k)

    print(f"Per-area objects: {area_objs}   |   orphaned (slug not in index): {len(orphans)}")
    if not orphans:
        print("Nothing to delete.")
        return 0

    frac = len(orphans) / max(1, area_objs)
    by_region = Counter(region_code(slug_for_key(k)) for k in orphans)
    print(f"Orphan fraction: {frac:.1%}   top regions: "
          f"{', '.join(f'{r}:{n}' for r, n in by_region.most_common(12))}")
    for k in orphans[:20]:
        print(f"  - {k}")
    if len(orphans) > 20:
        print(f"  … and {len(orphans) - 20} more")

    if frac > MAX_FRAC and not args.force:
        sys.exit(f"\nABORT: {frac:.1%} of area-objects look orphaned (> {MAX_FRAC:.0%}). "
                 f"That usually means the index loaded wrong. Eyeball the list above; "
                 f"re-run with --force if this is genuinely intended.")

    if not args.apply:
        print("\nDRY RUN — re-run with --apply to delete the above.")
        return 0

    print(f"\nDeleting {len(orphans)} orphaned objects…")
    delete_keys(ep, orphans)
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
