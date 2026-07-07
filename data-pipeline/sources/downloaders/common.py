#!/usr/bin/env python3
"""Shared helpers for source downloaders.

Every downloader is idempotent: it writes to raw/<source_id>/ and skips a
file that already exists (unless --force). raw/ is gitignored (large,
reproducible). Downloaders NEVER touch the runtime Overpass API — OSM
comes from Geofabrik extracts only (spec §0). Authoritative portals that
need an API key read it from an env var so no secret is committed.
"""
from __future__ import annotations

import os
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RAW = REPO / "raw"


def raw_dir(source_id: str) -> Path:
    d = RAW / source_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def download(url: str, dest: Path, *, force: bool = False, headers: dict | None = None) -> Path:
    """Stream `url` to `dest`. Skips if present unless force. Atomic via .part."""
    if dest.exists() and not force:
        print(f"  [skip] {dest.name} already present ({dest.stat().st_size:,} bytes)")
        return dest
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "trekdex-pipeline/0.1"})
    print(f"  [get ] {url}")
    try:
        with urllib.request.urlopen(req) as resp, tmp.open("wb") as out:
            total = 0
            while chunk := resp.read(1 << 20):
                out.write(chunk)
                total += len(chunk)
    except Exception as exc:  # noqa: BLE001 - surface any network/proxy failure clearly
        if tmp.exists():
            tmp.unlink()
        sys.stderr.write(
            f"ERROR downloading {url}: {exc}\n"
            f"  (build-time network access required; retry or fetch manually into {dest})\n"
        )
        raise SystemExit(4)
    tmp.replace(dest)
    print(f"  [ok  ] {dest.name} ({total:,} bytes)")
    return dest


def env_key(name: str, *, source_id: str) -> str:
    val = os.environ.get(name)
    if not val:
        sys.stderr.write(
            f"ERROR: {source_id} needs an API key in ${name}.\n"
            f"  export {name}=... then re-run. (No secret is committed.)\n"
        )
        raise SystemExit(5)
    return val
