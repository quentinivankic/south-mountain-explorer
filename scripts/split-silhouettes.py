#!/usr/bin/env python3
"""
One-shot migration: split the monolithic `public/areas/silhouettes.json`
into one file per area at `public/areas/silhouettes/<id>.json`.

Used once, when moving the iOS app's silhouettes off-bundle and onto
R2. After this runs and the new files are committed + synced, the
monolithic `silhouettes.json` can be deleted from the repo.

Idempotent: re-running overwrites the per-area files; no merge logic
because the source of truth is the monolith on input.

Safe to delete this script once the migration has shipped and the
build-trail-index workflow's new per-area write path is in steady
state.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MONOLITH = ROOT / "public" / "areas" / "silhouettes.json"
OUT_DIR = ROOT / "public" / "areas" / "silhouettes"


def main() -> int:
    if not MONOLITH.exists():
        print(f"Source missing: {MONOLITH}", file=sys.stderr)
        return 1
    data = json.loads(MONOLITH.read_text())
    if not isinstance(data, dict):
        print(f"Unexpected shape in {MONOLITH}: expected dict at top level", file=sys.stderr)
        return 1
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    written = 0
    for area_id, sil in data.items():
        if not isinstance(area_id, str) or not sil:
            continue
        out_path = OUT_DIR / f"{area_id}.json"
        out_path.write_text(json.dumps(sil, separators=(",", ":")))
        written += 1
    print(f"Wrote {written} per-area silhouettes to {OUT_DIR.relative_to(ROOT)}")
    print(f"You can now `git rm {MONOLITH.relative_to(ROOT)}` once the new files are committed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
