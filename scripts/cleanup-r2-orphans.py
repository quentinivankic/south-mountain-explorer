#!/usr/bin/env python3
"""Delete orphaned area objects from the Cloudflare R2 bucket.

An "orphan" is any geom or silhouette object in R2 whose area slug is
no longer present in `public/areas/index.json`. This happens when areas
are removed from the index (e.g. the European-data removal) — the
`sync-geom-to-r2` workflow uses `aws s3 sync` WITHOUT `--delete` (it
can't safely: geom lives at the bucket root next to `index.json` and
the `silhouettes/` prefix, so a blanket --delete would nuke those),
so removed areas leave their objects behind.

This script lists the bucket, computes the orphan set by comparing
slugs against the current index, and deletes ONLY those keys. It never
deletes `index.json`, never touches a key whose slug is still in the
index, and never pattern-matches (so the Saskatchewan `-ca-sk` /
Slovakia `-sk` style suffix collisions can't bite).

Dry-run by default. Pass --apply to actually delete.

Bucket layout (see .github/workflows/sync-geom-to-r2.yml):
  s3://trekdex-areas/<slug>.json              geom, at root
  s3://trekdex-areas/silhouettes/<slug>.json  silhouettes
  s3://trekdex-areas/index.json               the index (NEVER deleted)

Usage (normally run via the cleanup-r2-orphans workflow, which has the
R2 creds in env):
    python3 scripts/cleanup-r2-orphans.py            # dry run
    python3 scripts/cleanup-r2-orphans.py --apply    # delete
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "public" / "areas" / "index.json"
BUCKET = "trekdex-areas"
# Keys at the bucket root that are NOT per-area objects — never delete.
PROTECTED_ROOT_KEYS = {"index.json"}


def endpoint() -> str:
    acct = os.environ.get("R2_ACCOUNT_ID", "")
    if not acct:
        sys.exit("R2_ACCOUNT_ID not set")
    return f"https://{acct}.r2.cloudflarestorage.com"


def valid_slugs() -> set[str]:
    rows = json.loads(INDEX.read_text())
    return {row[0] for row in rows}


def list_keys(ep: str) -> list[str]:
    """All object keys in the bucket, via paginated list-objects-v2."""
    keys: list[str] = []
    token: str | None = None
    while True:
        cmd = [
            "aws", "s3api", "list-objects-v2",
            "--bucket", BUCKET,
            "--endpoint-url", ep,
            "--output", "json",
            "--max-items", "1000",
        ]
        if token:
            cmd += ["--starting-token", token]
        out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
        data = json.loads(out) if out.strip() else {}
        for obj in data.get("Contents", []):
            keys.append(obj["Key"])
        token = data.get("NextToken")
        if not token:
            break
    return keys


def slug_for_key(key: str) -> str | None:
    """Area slug a key belongs to, or None if it's not a per-area object.
      `<slug>.json`              -> slug
      `silhouettes/<slug>.json`  -> slug
    Anything else (index.json, unexpected prefixes) -> None.
    """
    if key in PROTECTED_ROOT_KEYS:
        return None
    if not key.endswith(".json"):
        return None
    if key.startswith("silhouettes/"):
        return key[len("silhouettes/"):-len(".json")]
    if "/" in key:
        return None  # some other prefix we don't manage
    return key[:-len(".json")]


def delete_keys(ep: str, keys: list[str]) -> None:
    """Batch-delete via delete-objects (up to 1000 keys/call)."""
    for i in range(0, len(keys), 1000):
        batch = keys[i:i + 1000]
        payload = json.dumps({
            "Objects": [{"Key": k} for k in batch],
            "Quiet": True,
        })
        subprocess.run(
            [
                "aws", "s3api", "delete-objects",
                "--bucket", BUCKET,
                "--endpoint-url", ep,
                "--delete", payload,
            ],
            check=True,
        )
        print(f"  deleted batch of {len(batch)}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="Actually delete. Without this, dry-run only.")
    args = ap.parse_args()

    ep = endpoint()
    valid = valid_slugs()
    print(f"Index has {len(valid)} valid area slugs.")

    keys = list_keys(ep)
    print(f"Bucket has {len(keys)} objects.")

    orphans: list[str] = []
    unmanaged: list[str] = []
    for key in keys:
        slug = slug_for_key(key)
        if slug is None:
            unmanaged.append(key)
            continue
        if slug not in valid:
            orphans.append(key)

    print(f"Protected / unmanaged keys (kept): {len(unmanaged)} "
          f"(e.g. {unmanaged[:3]})")
    print(f"Orphan keys to delete: {len(orphans)}")
    for k in orphans[:20]:
        print(f"  - {k}")
    if len(orphans) > 20:
        print(f"  … and {len(orphans) - 20} more")

    if not orphans:
        print("Nothing to delete.")
        return 0

    if not args.apply:
        print("\nDRY RUN — re-run with --apply to delete the above.")
        return 0

    print(f"\nDeleting {len(orphans)} orphan objects…")
    delete_keys(ep, orphans)
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
