#!/usr/bin/env python3
"""Delete ORPHANED area objects from the Cloudflare R2 bucket — the
geom / silhouette files whose area slug is no longer in the shipped
`public/areas/index.json`.

WHY THIS EXISTS. `sync-geom-to-r2.yml` publishes without blanket deletion
because global objects and the silhouettes prefix share the bucket root with
per-area geom. This cleaner therefore classifies only exact per-area JSON keys.

SAFETY BELTS (all abort before any delete):
  * the index must load and carry a sane number of ids (>= MIN_INDEX);
  * per-area objects that ARE in the index are never touched;
  * every enumerated global root object, including `parking.json`, is protected;
  * unknown prefixes and non-JSON objects are never classified as areas;
  * the protected-key contract self-checks before bucket access;
  * if orphans exceed MAX_FRAC of area objects, abort unless --force.

Bucket layout (see .github/workflows/sync-geom-to-r2.yml):
  s3://trekdex-areas/<slug>.json              geom, at root
  s3://trekdex-areas/silhouettes/<slug>.json  silhouettes
  s3://trekdex-areas/index.json               global (NEVER deleted)
  s3://trekdex-areas/trail-search.json        global (NEVER deleted)
  s3://trekdex-areas/trail-shapes.json        global (NEVER deleted)
  s3://trekdex-areas/parking.json             global (NEVER deleted)

Dry-run by default. Pass --apply to actually delete. Run from the repo root.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import unicodedata
from collections import Counter
from pathlib import Path

BUCKET = "trekdex-areas"
DEFAULT_INDEX = Path(__file__).resolve().parent.parent / "public" / "areas" / "index.json"

# Versioned-by-source bucket-root contract. These are global/control objects,
# never per-area geom. Keep this immutable at runtime so classification cannot
# accidentally narrow it after the preflight check.
REQUIRED_PROTECTED_KEYS = frozenset(
    {"index.json", "trail-search.json", "trail-shapes.json", "parking.json"}
)
PROTECTED_KEYS = REQUIRED_PROTECTED_KEYS

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


# Keys that must remain unclassifiable even if the classifier is later edited.
# This sentinel set is checked before index, endpoint, inventory, or deletion.
REQUIRED_REJECTED_KEYS = frozenset({
    "",
    "archive/control.json",
    "silhouettes/",
    "silhouettes/.json",
    "silhouettes//control.json",
    "silhouettes/archive/control.json",
    "silhouettes/control/.json",
    "silhouettes/../control.json",
    "silhouettes/Control.json",
    "silhouettes/control_name.json",
    "silhouettes/control.json\n",
    *(f"silhouettes/{key}" for key in REQUIRED_PROTECTED_KEYS),
})


def _canonical_area_filename(filename: str) -> str | None:
    """Return the canonical flat area slug represented by ``filename``."""
    if not isinstance(filename, str) or filename in PROTECTED_KEYS:
        return None
    if any(unicodedata.category(char).startswith("C") for char in filename):
        return None
    if not filename.endswith(".json"):
        return None
    stem = filename[:-len(".json")]
    if (not stem or stem != unicodedata.normalize("NFC", stem) or
            stem != stem.casefold() or stem.startswith("-") or stem.endswith("-") or
            not any(char.isalnum() for char in stem) or
            any(not (char.isalnum() or char == "-") for char in stem)):
        return None
    return stem


def slug_for_key(key: str) -> str | None:
    """Return a canonical area slug, or None for every other bucket key."""
    if not isinstance(key, str) or key in PROTECTED_KEYS:
        return None
    if key.startswith("silhouettes/"):
        filename = key[len("silhouettes/"):]
    elif "/" in key:
        return None  # unknown namespace
    else:
        filename = key
    # The filename grammar rejects nested separators after a recognized prefix.
    return _canonical_area_filename(filename)


def validate_protected_key_contract() -> None:
    """Fail closed before access if protected or hostile keys can classify."""
    if PROTECTED_KEYS != REQUIRED_PROTECTED_KEYS:
        raise RuntimeError("protected root-key contract drifted")
    candidates = set(REQUIRED_PROTECTED_KEYS) | set(REQUIRED_REJECTED_KEYS)
    misclassified = sorted(repr(key) for key in candidates if slug_for_key(key) is not None)
    if misclassified:
        raise RuntimeError(f"non-area keys classified as areas: {misclassified}")


def load_index_ids(path: Path) -> set[str]:
    data = json.loads(path.read_text())
    rows = data["areas"] if isinstance(data, dict) and "areas" in data else data
    ids: set[str] = set()
    for row in rows:
        if isinstance(row, list) and row:
            ids.add(row[0])
        elif isinstance(row, dict) and row.get("id"):
            ids.add(row["id"])
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
    for offset in range(0, len(keys), 1000):
        batch = keys[offset:offset + 1000]
        payload = json.dumps({"Objects": [{"Key": key} for key in batch], "Quiet": True})
        subprocess.run(
            ["aws", "s3api", "delete-objects",
             "--bucket", BUCKET, "--endpoint-url", ep, "--delete", payload],
            check=True,
        )
        print(f"  deleted batch of {len(batch)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Actually delete. Without this, dry-run only.")
    parser.add_argument("--index", default=str(DEFAULT_INDEX), help="Path to the shipped index.json.")
    parser.add_argument("--force", action="store_true",
                        help="Bypass the MAX_FRAC safety abort (use only after reviewing a dry run).")
    args = parser.parse_args()

    # This precedes endpoint construction, inventory, and every delete path.
    validate_protected_key_contract()

    index_ids = load_index_ids(Path(args.index))
    print(f"Index carries {len(index_ids)} area ids ({args.index}).")
    if len(index_ids) < MIN_INDEX:
        sys.exit(f"Index has only {len(index_ids)} ids (< {MIN_INDEX}) — refusing to run "
                 f"(a short/empty index would flag the whole bucket as orphaned).")

    ep = endpoint()
    keys = list_keys(ep)
    print(f"Bucket has {len(keys)} objects.")

    area_objects, orphans = 0, []
    for key in keys:
        slug = slug_for_key(key)
        if slug is None:
            continue
        area_objects += 1
        if slug not in index_ids:
            orphans.append(key)

    print(f"Per-area objects: {area_objects}   |   orphaned (slug not in index): {len(orphans)}")
    if not orphans:
        print("Nothing to delete.")
        return 0

    fraction = len(orphans) / max(1, area_objects)
    by_region = Counter(region_code(slug_for_key(key)) for key in orphans)
    print(f"Orphan fraction: {fraction:.1%}   top regions: "
          f"{', '.join(f'{region}:{count}' for region, count in by_region.most_common(12))}")
    for key in orphans[:20]:
        print(f"  - {key}")
    if len(orphans) > 20:
        print(f"  … and {len(orphans) - 20} more")

    if fraction > MAX_FRAC and not args.force:
        sys.exit(f"\nABORT: {fraction:.1%} of area-objects look orphaned (> {MAX_FRAC:.0%}). "
                 f"That usually means the index loaded wrong. Review the list above; "
                 f"re-run with --force only if this is genuinely intended.")

    if not args.apply:
        print("\nDRY RUN — re-run with --apply to delete the above.")
        return 0

    print(f"\nDeleting {len(orphans)} orphaned objects…")
    delete_keys(ep, orphans)
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
