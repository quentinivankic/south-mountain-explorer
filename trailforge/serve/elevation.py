#!/usr/bin/env python3
"""Elevation gain + real difficulty from a global DEM (SPEC.md §6e).

Two halves, deliberately split so the MATH is unit-tested in the sandbox and
only the tile FETCH needs network (homelab / CI runner):

  pure math   terrarium_decode · densify · smooth · gain_ft · difficulty_label
              — no network, fully tested.
  TileSampler AWS "Terrarium" terrain tiles (global, pre-merged SRTM +
              Copernicus + 3DEP, free on AWS Open Data), PNG RGB-decoded,
              disk+memory cached. Needs urllib + Pillow.

Elevation is genuinely absent from 2D OSM ways, so it can't be derived from
geometry — but the DEM data is free/global and the gain math is ours (no
US-only source, no paid API).
"""
from __future__ import annotations

import math

# --- pure math -------------------------------------------------------------

# AWS terrarium native max zoom is 15; z13 (~19 m/px at the equator, finer
# toward the poles) comfortably resolves the ~30 m densify spacing below.
DEM_ZOOM = 13
_M_PER_FT = 0.3048


def terrarium_decode(r: int, g: int, b: int) -> float:
    """Terrarium RGB -> metres. elevation = (R*256 + G + B/256) - 32768."""
    return (r * 256 + g + b / 256.0) - 32768.0


def _px_x(lon: float, z: int) -> float:
    """Global pixel X (256 px/tile) for a longitude at zoom z."""
    return (lon + 180.0) / 360.0 * (2 ** z) * 256.0


def _px_y(lat: float, z: int) -> float:
    lat = max(min(lat, 85.05112878), -85.05112878)
    s = math.sin(math.radians(lat))
    y = 0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)
    return y * (2 ** z) * 256.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(a))


def densify(coords: list[tuple[float, float]],
            spacing_m: float = 30.0) -> list[tuple[float, float]]:
    """Resample a (lat, lon) polyline to ~`spacing_m` point spacing, keeping the
    original vertices as anchors. So a straight 300 m segment yields ~11 evenly
    spaced points — one elevation sample every ~30 m, per §6e."""
    if len(coords) < 2:
        return list(coords)
    out: list[tuple[float, float]] = [coords[0]]
    for (la1, lo1), (la2, lo2) in zip(coords, coords[1:]):
        d = haversine_m(la1, lo1, la2, lo2)
        n = max(1, int(d // spacing_m))
        for i in range(1, n + 1):
            t = i / n
            out.append((la1 + (la2 - la1) * t, lo1 + (lo2 - lo1) * t))
    return out


def smooth(vals: list[float], window: int = 5) -> list[float]:
    """Centered moving average. MANDATORY before summing gain — raw 30 m DEM
    elevation is noisy and naive up-tick summing massively inflates gain
    (§6e: 4,000 ft on a rolling 3 mi trail). Same idea as AllTrails/Strava."""
    n = len(vals)
    if n == 0 or window <= 1:
        return list(vals)
    half = window // 2
    out = []
    for i in range(n):
        lo, hi = max(0, i - half), min(n, i + half + 1)
        out.append(sum(vals[lo:hi]) / (hi - lo))
    return out


def gain_ft(elevs_m: list[float], window: int = 5,
            min_delta_m: float = 0.5) -> float:
    """Elevation gain in FEET for hiking difficulty — DIRECTION-INVARIANT.

    OSM way direction is arbitrary: Humphreys Summit Trail is stored
    summit->trailhead, so summing only *uphill* deltas gives ~0. We instead
    return max(total ascent, total descent) — the climb you do in the harder
    direction, which is what AllTrails reports for an out-and-back (and matches
    Humphreys' ~3,333 ft either way). Smooths first (kills the DEM noise that
    would inflate gain); the small floor rejects residual noise without
    under-counting a gentle continuous climb. Window / floor tuned against
    known trails on the homelab (§6e)."""
    if len(elevs_m) < 2:
        return 0.0
    s = smooth(elevs_m, window)
    up = down = 0.0
    for a, b in zip(s, s[1:]):
        d = b - a
        if d > min_delta_m:
            up += d
        elif -d > min_delta_m:
            down += -d
    return round(max(up, down) / _M_PER_FT)


def difficulty_label(miles: float, gain_ft: float | None,
                     sac: str | None = None, vis: str | None = None) -> str:
    """Easy / Moderate / Hard.

    With `gain_ft` (DEM sampled): a real function of effort, the NPS/Shenandoah
    numerical rating `sqrt(2 * gain_ft * miles)` plus a pure-distance floor so a
    long FLAT walk still reads Moderate/Hard. Fixes the backwards length-only
    behaviour (a flat 5 mi path was Hard; a 2 mi / 2,000 ft climb was Moderate).

    STEEPNESS FLOOR: the NPS rating scales with `sqrt(distance)`, so it
    under-rates a SHORT brutal climb — Acadia's Precipice (966 ft in 0.67 mi,
    ~1,440 ft/mi) scored 36 and read "Easy" despite being an iron-rung ascent.
    A per-mile grade (gain / miles) floors the label so sustained steepness
    reads hard regardless of total length: >= 1,500 ft/mi (~28% grade) is Hard,
    >= 1,000 ft/mi (~19%) is at least Moderate. It only ever RAISES the label
    (a flat trail's grade is tiny), so long/flat behaviour is unchanged.

    Without `gain_ft` (no DEM): the legacy length-only fallback, so the pipeline
    still works when elevation wasn't sampled.
    """
    sac = (sac or "").strip()
    if sac and sac != "hiking":
        return "Hard"                       # technical terrain — beyond walking
    if gain_ft is None:
        if miles > 4:
            return "Hard"
        if miles > 2 or (vis or "") == "intermediate":
            return "Moderate"
        return "Easy"
    rating = math.sqrt(2 * max(gain_ft, 0.0) * max(miles, 0.0))
    grade = max(gain_ft, 0.0) / max(miles, 0.05)   # ft per mile
    if rating >= 80 or miles >= 10 or grade >= 1500:
        return "Hard"
    if rating >= 45 or miles >= 5 or grade >= 1000:
        return "Moderate"
    return "Easy"


def trail_gain_ft(segments: list[list], sampler) -> float:
    """Sum gain across a trail's segments (each a [[lat, lon], ...] polyline),
    densified to ~30 m and sampled via `sampler.elevation(lat, lon)`."""
    total = 0.0
    for seg in segments:
        pts = densify([(p[0], p[1]) for p in seg if len(p) >= 2])
        if len(pts) < 2:
            continue
        elevs = [sampler.elevation(la, lo) for la, lo in pts]
        total += gain_ft(elevs)
    return round(total)


def process_area(geom: dict, sampler) -> tuple[int, dict]:
    """Set `gainFt` + a gain-aware `difficulty` per trail and a per-area
    `total_gain_ft`, sampling elevation via `sampler.elevation(lat, lon)`.

    Mutates `geom` in place; returns `(trails_updated, {"old->new": count})`
    so a caller can print a label-change summary. A per-trail sampling failure
    (a bad/missing tile) is skipped — that trail keeps its existing
    length-based difficulty — so one bad tile never aborts the whole area.
    Testable with any object exposing `.elevation(lat, lon)`.

    Shared by `add-elevation.py` (the standalone post-process) and
    `publish_areas.py` (which calls this inline so gain survives a republish).
    """
    import sys
    changed = 0
    delta: dict[str, int] = {}
    total_gain = 0.0
    for t in geom.get("trails", []):
        miles = t.get("distanceMi", 0.0)
        try:
            g = trail_gain_ft(t.get("segments", []), sampler)
        except Exception as e:                      # a bad/missing tile: skip trail
            print(f"    ! gain failed for {t.get('id')}: {e}", file=sys.stderr)
            continue
        old = t.get("difficulty")
        new = difficulty_label(miles, g)
        t["gainFt"] = int(g)
        t["difficulty"] = new
        total_gain += g
        changed += 1
        if old != new:
            delta[f"{old}->{new}"] = delta.get(f"{old}->{new}", 0) + 1
    geom["total_gain_ft"] = int(total_gain)
    return changed, delta


# --- tile sampler (needs network + Pillow) ---------------------------------

class TileSampler:
    """Bilinear elevation lookup against AWS Terrarium tiles, cached.

    Tiles: https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png
    Decoded tiles are memoised in RAM; PNGs optionally cached on disk so a
    re-run (or the next state sharing a tile) skips the download.
    """

    URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"

    def __init__(self, zoom: int = DEM_ZOOM, cache_dir: str | None = None):
        self.z = zoom
        self.cache_dir = cache_dir
        self._tiles: dict[tuple[int, int], list[list[float]]] = {}
        if cache_dir:
            import os
            os.makedirs(cache_dir, exist_ok=True)

    def _tile(self, tx: int, ty: int) -> list[list[float]]:
        key = (tx, ty)
        cached = self._tiles.get(key)
        if cached is not None:
            return cached
        from PIL import Image                    # lazy: only when fetching
        import io
        import urllib.request
        png = None
        path = None
        if self.cache_dir:
            import os
            path = os.path.join(self.cache_dir, f"{self.z}_{tx}_{ty}.png")
            if os.path.exists(path):
                png = open(path, "rb").read()
        if png is None:
            url = self.URL.format(z=self.z, x=tx, y=ty)
            with urllib.request.urlopen(url, timeout=60) as r:
                png = r.read()
            if path:
                open(path, "wb").write(png)
        img = Image.open(io.BytesIO(png)).convert("RGB")
        w, h = img.size
        px = img.load()
        grid = [[terrarium_decode(*px[x, y]) for x in range(w)] for y in range(h)]
        self._tiles[key] = grid
        return grid

    def _pixel(self, gx: int, gy: int) -> float:
        tx, ox = divmod(gx, 256)
        ty, oy = divmod(gy, 256)
        return self._tile(tx, ty)[oy][ox]

    def elevation(self, lat: float, lon: float) -> float:
        gx = _px_x(lon, self.z)
        gy = _px_y(lat, self.z)
        x0, y0 = int(math.floor(gx)), int(math.floor(gy))
        fx, fy = gx - x0, gy - y0
        e00 = self._pixel(x0, y0)
        e10 = self._pixel(x0 + 1, y0)
        e01 = self._pixel(x0, y0 + 1)
        e11 = self._pixel(x0 + 1, y0 + 1)
        top = e00 * (1 - fx) + e10 * fx
        bot = e01 * (1 - fx) + e11 * fx
        return top * (1 - fy) + bot * fy


def build_sampler(zoom: int = DEM_ZOOM, cache_dir: str | None = None,
                  probe: tuple[float, float] = (36.106, -112.113)):
    """Build a `TileSampler` and verify it can actually fetch + decode one tile
    (elevation sampling needs network egress + Pillow). Returns the sampler, or
    **None** — with a one-line warning — when elevation is unavailable, so
    callers fall back to length-based difficulty instead of crashing (the
    sandbox has no DEM egress; a runner may lack Pillow). The default probe is
    a Grand Canyon point that certainly has tile data."""
    import sys
    s = TileSampler(zoom=zoom, cache_dir=cache_dir)
    try:
        s.elevation(*probe)
    except Exception as e:
        print(f"! elevation unavailable ({type(e).__name__}: {e}); "
              f"falling back to length-based difficulty", file=sys.stderr)
        return None
    return s
