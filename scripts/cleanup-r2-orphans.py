#!/usr/bin/env python3
"""Delete the orphaned EUROPEAN area objects from the Cloudflare R2
bucket — the geom / silhouette files left behind when all non-North-
America area data was removed from the index (see the EU-removal PR).

IMPORTANT — scope is deliberately narrow. An earlier version of this
script deleted "any R2 object whose slug isn't in index.json", which
was wrong: R2 holds geom/silhouette files for FAR more areas than the
shipped index (every area ever seeded, including ones later dropped by
the trail-count / dedup filters). That set was ~11k objects, almost
all North American. We only want to remove the European ones.

So this targets an explicit allowlist of European ISO-3166-1 country
codes by slug suffix, with a guard against the Canadian-province
collisions (Saskatchewan `-ca-sk` vs Slovakia `-sk`; Newfoundland
`-ca-nl` vs Netherlands `-nl`). It NEVER deletes index.json, never a
US/Canada object, never anything outside the EU code set.

Bucket layout (see .github/workflows/sync-geom-to-r2.yml):
  s3://trekdex-areas/<slug>.json              geom, at root
  s3://trekdex-areas/silhouettes/<slug>.json  silhouettes
  s3://trekdex-areas/index.json               the index (NEVER deleted)

Dry-run by default. Pass --apply to actually delete.

Usage (normally via the cleanup-r2-orphans workflow, which has creds):
    python3 scripts/cleanup-r2-orphans.py            # dry run
    python3 scripts/cleanup-r2-orphans.py --apply    # delete
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

BUCKET = "trekdex-areas"

# The non-NA (European) ISO3166-1 country codes whose area objects are
# being purged. Matches the set removed from the index/repo in the
# EU-removal PR. US state codes and Canadian province codes are
# deliberately absent.
EU_CODES = {
    "AT", "BE", "BG", "HR", "CY", "CZ", "EE", "FI", "FR", "GR",
    "HU", "IE", "IT", "LV", "LT", "LU", "NL", "PL", "PT", "RO",
    "SK", "SI", "ES", "SE", "DK", "IS", "CH",
}


def endpoint() -> str:
    acct = os.environ.get("R2_ACCOUNT_ID", "")
    if not acct:
        sys.exit("R2_ACCOUNT_ID not set")
    return f"https://{acct}.r2.cloudflarestorage.com"


def region_code(slug: str) -> str:
    """Region code for an area slug.
    Canadian provinces are `<name>-ca-xx` — check that FIRST so the
    `-sk` / `-nl` country suffixes don't shadow CA-SK / CA-NL.
    """
    parts = slug.rsplit("-", 2)
    if len(parts) == 3 and parts[1] == "ca":
        return f"CA-{parts[2].upper()}"
    return slug.rsplit("-", 1)[-1].upper()


def slug_for_key(key: str) -> str | None:
    """Area slug a key belongs to, or None if not a per-area object."""
    if not key.endswith(".json") or key == "index.json":
        return None
    if key.startswith("silhouettes/"):
        return key[len("silhouettes/"):-len(".json")]
    if "/" in key:
        return None
    return key[:-len(".json")]


def is_european(key: str) -> bool:
    slug = slug_for_key(key)
    if slug is None:
        return False
    return region_code(slug) in EU_CODES


def list_keys(ep: str) -> list[str]:
    keys: list[str] = []
    token: str | None = None
    while True:
        cmd = [
            "aws", "s3api", "list-objects-v2",
            "--bucket", BUCKET, "--endpoint-url", ep,
            "--output", "json", "--max-items", "1000",
        ]
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
    ap.add_argument("--apply", action="store_true",
                    help="Actually delete. Without this, dry-run only.")
    args = ap.parse_args()

    ep = endpoint()
    keys = list_keys(ep)
    print(f"Bucket has {len(keys)} objects.")

    europeans = [k for k in keys if is_european(k)]
    # Safety belt: surface anything that resolves to a CA province so a
    # reviewer can eyeball that the collision guard held (should be 0).
    ca_hits = [k for k in keys
               if (s := slug_for_key(k)) and region_code(s).startswith("CA-")
               and is_european(k)]
    assert not ca_hits, f"Canadian objects wrongly flagged European: {ca_hits[:5]}"

    print(f"European objects to delete: {len(europeans)}")
    for k in europeans[:20]:
        print(f"  - {k}")
    if len(europeans) > 20:
        print(f"  … and {len(europeans) - 20} more")

    if not europeans:
        print("Nothing to delete.")
        return 0

    if not args.apply:
        print("\nDRY RUN — re-run with --apply to delete the above.")
        return 0

    print(f"\nDeleting {len(europeans)} European objects…")
    delete_keys(ep, europeans)
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
