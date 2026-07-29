#!/usr/bin/env python3
"""Read the local OSM cache so a parking roll needs no Overpass.

Companion to `build-local-osm-cache.py`, which writes the three artifacts. This
module only READS them, and returns exactly the shapes add-parking.py already
consumes — Overpass-style `{"elements": [...]}` for parking, `{rel_id: rings}`
for boundaries — so the local path is a swap at the FETCH boundary and nothing
downstream changes. That matters: the containment maths, the trailhead
corroboration and the road gate's own arithmetic stay the single tested copy.

STALENESS IS THE ONLY REAL HAZARD. A cache that answers confidently from last
month's OSM is worse than no cache, so every artifact carries the source
extract's replication timestamp and `check_fresh()` refuses a mismatch against
the extract on disk. `fetch-us-extract.sh --update` regenerates the extracts;
rebuild the cache after it.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

DEFAULT_OSM_DIR = Path(os.environ.get("TREKDEX_OSM_DIR", "/mnt/raid/trekdex/osm"))


class LocalCacheError(RuntimeError):
    """The cache cannot answer. Callers treat this like an Overpass failure —
    fail closed, never silently degrade — because the two are the same event:
    the data we need is not available right now."""


def _stamp(pbf: Path) -> str:
    out = subprocess.run(["osmium", "fileinfo", "-g", "header.option.timestamp",
                          str(pbf)], capture_output=True, text=True)
    return (out.stdout or "").strip() or "unknown"


class LocalOSM:
    def __init__(self, cache_dir: str | Path | None = None,
                 osm_dir: str | Path | None = None, strict: bool = True):
        self.osm_dir = Path(osm_dir or DEFAULT_OSM_DIR)
        self.dir = Path(cache_dir or (self.osm_dir / "cache"))
        self.strict = strict
        self._parking: list[dict] | None = None
        self._polys: dict[int, list] | None = None
        self._gate: dict[str, int] | None = None

    # ------------------------------------------------------------- freshness
    def check_fresh(self, artifact: str, meta: dict) -> None:
        """Refuse a cache built from a different extract than the one on disk.

        Not advisory: a roll that reads a stale cache produces numbers that look
        completely normal and describe a different day — the same failure family
        as the stale-geom cache bug (#424) and the publish that reported success
        while doing nothing.
        """
        if not self.strict:
            return
        src = self.osm_dir / (meta.get("source") or "us-access.osm.pbf")
        if not src.exists():
            return
        have, want = _stamp(src), meta.get("stamp")
        if want and have != "unknown" and have != want:
            raise LocalCacheError(
                f"{artifact} was built from {meta.get('source')} at {want}, but "
                f"that file is now {have}. Re-run build-local-osm-cache.py.")

    # --------------------------------------------------------------- parking
    def parking_elements(self, bbox: list[float]) -> dict:
        """Overpass-shaped parking + trailhead features inside `bbox`
        ([lonmin, latmin, lonmax, latmax]).

        The Overpass version scopes to the state's ISO admin area; this scopes to
        the bounding box of the state's areas, which is WIDER, never narrower.
        Wider is safe — `parking_for_area` keeps only lots near that area's own
        trails, so extra candidates cannot leak into a different area's result.
        """
        if self._parking is None:
            path = self.dir / "parking.jsonl"
            if not path.exists():
                raise LocalCacheError(f"missing {path}")
            rows: list[dict] = []
            with open(path) as fh:
                first = fh.readline()
                try:
                    meta = json.loads(first).get("_meta") or {}
                except ValueError:
                    meta = {}
                self.check_fresh("parking.jsonl", meta)
                for line in fh:
                    if line.strip():
                        rows.append(json.loads(line))
            if not rows:
                raise LocalCacheError(f"{path} holds no features")
            self._parking = rows
        lonmin, latmin, lonmax, latmax = bbox
        els = []
        for r in self._parking:
            if not (latmin <= r["lat"] <= latmax and lonmin <= r["lon"] <= lonmax):
                continue
            if r["type"] == "node":
                els.append({"type": "node", "lat": r["lat"], "lon": r["lon"],
                            "tags": r["tags"]})
            else:
                # A way/relation MUST carry `center`, not bare lat/lon: add-
                # parking's `_point()` reads top-level lat/lon for nodes and
                # `center` for everything else, so a way with only lat/lon reads
                # as having no position at all. That silently dropped every one
                # of Vermont's 8,074 cached lots down to the 430 that happened
                # to be nodes — the pipeline ran clean and produced a tenth of
                # the answer.
                els.append({"type": r["type"],
                            "center": {"lat": r["lat"], "lon": r["lon"]},
                            "tags": r["tags"]})
        return {"elements": els}

    # ------------------------------------------------------------ boundaries
    def _load_polys(self) -> dict[int, list]:
        """{relation_id: [outer ring as [(lon, lat), ...], ...]}.

        osmium area ids encode the source: odd = built from a relation, with
        relation_id = (area_id - 1) // 2. Even ids are closed WAYS, which are not
        what `osm_relation_id` names, so they are skipped.
        """
        if self._polys is not None:
            return self._polys
        path = self.dir / "boundaries.geojsonseq"
        if not path.exists():
            raise LocalCacheError(f"missing {path}")
        polys: dict[int, list] = {}
        with open(path) as fh:
            for line in fh:
                line = line.strip().lstrip("\x1e")
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                aid = str(d.get("id") or "")
                if not aid.startswith("a"):
                    continue
                try:
                    n = int(aid[1:])
                except ValueError:
                    continue
                if n % 2 == 0:
                    continue
                g = d.get("geometry") or {}
                rings: list[list[tuple[float, float]]] = []
                if g.get("type") == "Polygon":
                    rings = [[(c[0], c[1]) for c in g["coordinates"][0]]]
                elif g.get("type") == "MultiPolygon":
                    rings = [[(c[0], c[1]) for c in poly[0]]
                             for poly in g["coordinates"]]
                if rings:
                    polys.setdefault((n - 1) // 2, []).extend(rings)
        if not polys:
            raise LocalCacheError(f"{path} yielded no polygons")
        self._polys = polys
        return polys

    def boundaries(self, rel_ids: list[int]) -> dict[int, list]:
        polys = self._load_polys()
        return {r: polys[r] for r in rel_ids if r in polys}

    # ------------------------------------------------------------- road gate
    def road_gate_verdicts(self) -> dict[str, int]:
        if self._gate is None:
            path = self.dir / "road-gate.json"
            if not path.exists():
                raise LocalCacheError(f"missing {path}")
            doc = json.loads(path.read_text())
            self.check_fresh("road-gate.json", doc)
            v = doc.get("verdicts")
            if not isinstance(v, dict) or not v:
                raise LocalCacheError(f"{path} holds no verdicts")
            self._gate = v
        return self._gate

    def road_gate(self, fed: list[dict], gate_key) -> tuple[list[dict], list[dict]]:
        """Split `fed` into (kept, unknown) using the cached verdicts.

        `unknown` is returned rather than assumed either way. A point the cache
        has never seen means the cache predates this ArcGIS answer, and guessing
        would either invent access or delete a real trailhead — the caller
        decides, and today it falls back to Overpass for exactly those points.
        """
        verdicts = self.road_gate_verdicts()
        kept, unknown = [], []
        for f in fed:
            hit = verdicts.get(gate_key(f))
            if hit is None:
                unknown.append(f)
            elif hit:
                kept.append(f)
        return kept, unknown
