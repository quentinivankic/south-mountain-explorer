#!/usr/bin/env python3
"""Stage only CONTENT-changed geom/silhouette files for the R2 sync.

The R2 sync used to run `aws s3 sync --size-only`, which compares just the
byte size. A difficulty recompute flips a trail's code in place ("e" -> "m")
— identical size — so --size-only judged the file unchanged and never
re-uploaded it, stranding stale silhouettes on R2 (the Abalone Cove
all-green bug). Dropping --size-only fixed correctness but made every run
re-upload all ~18k files (a fresh checkout stamps every mtime to "now").

This restores incrementality WITHOUT reintroducing the bug: compare each
local file's MD5 to the R2 object's ETag (an unencrypted single-part PUT's
ETag IS the body MD5). Copy only changed/new files into per-dataset staging
dirs so the workflow can `aws s3 cp --recursive` just those. A same-size
content change has a different MD5, so it's always caught.

Input:  /tmp/r2_etags.tsv  — "<key>\t<etag>" lines from
        `aws s3api list-objects-v2 ... --query 'Contents[].[Key,ETag]'`.
Output: /tmp/up_geom/  and  /tmp/up_sil/  — flat dirs of changed files
        (absent when a dataset has no changes, so the workflow can skip it).
"""
from __future__ import annotations

import hashlib
import os
import shutil

ETAGS = "/tmp/r2_etags.tsv"
# (local dir, R2 key prefix, staging dir)
PLANS = [
    ("public/areas/geom", "", "/tmp/up_geom"),
    ("public/areas/silhouettes", "silhouettes/", "/tmp/up_sil"),
]


def load_etags(path: str) -> dict[str, str]:
    m: dict[str, str] = {}
    try:
        with open(path) as f:
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if len(parts) == 2:
                    m[parts[0]] = parts[1].strip('"')
    except FileNotFoundError:
        pass
    return m


def md5(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fp:
        for chunk in iter(lambda: fp.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    etag = load_etags(ETAGS)
    total = 0
    for src, prefix, stage in PLANS:
        names = sorted(n for n in os.listdir(src) if n.endswith(".json"))
        changed = 0
        for name in names:
            local = os.path.join(src, name)
            if etag.get(prefix + name) != md5(local):
                os.makedirs(stage, exist_ok=True)
                shutil.copy2(local, os.path.join(stage, name))
                changed += 1
        print(f"{src}: {changed} changed of {len(names)}")
        total += changed
    print(f"total changed: {total}")


if __name__ == "__main__":
    main()
