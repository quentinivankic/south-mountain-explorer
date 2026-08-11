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
# Seams under this are unmapped scraps, not gaps worth telling the user about:
# 798 of 3,823 affected trails total under 0.5 mi, and a dashed break across a
# 300 m scrap implies a problem that is not there.
_GAP_MIN_M = 805.0   # 0.5 mi
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


def smooth(vals: list[float], window: int = 5,
           reflect: bool = False) -> list[float]:
    """Centered moving average. MANDATORY before summing gain — raw 30 m DEM
    elevation is noisy and naive up-tick summing massively inflates gain
    (§6e: 4,000 ft on a rolling 3 mi trail). Same idea as AllTrails/Strava.

    `reflect` mirrors the series at both ends instead of shrinking the window
    there. A shrinking window averages an endpoint against only its interior
    neighbours, which drags it toward the middle and FLATTENS the first and last
    step of a slope — on a synthetic linear ramp the end intervals came out at
    half the true rise. That understates how steep a trail is right where you
    start and finish it. Reflection reproduces a ramp exactly.

    Off by default so `gain_ft` keeps its long-standing, tuned behaviour; the
    profile opts in, since the chart is what shows the distortion."""
    n = len(vals)
    if n == 0 or window <= 1:
        return list(vals)
    half = window // 2
    if reflect and n > 1:
        # ANTISYMMETRIC reflection: pad with 2*edge - neighbour, i.e. continue
        # the slope through the endpoint rather than mirroring the values back.
        # Plain mirroring preserves a hump but NOT a ramp — it folds the climb
        # back on itself and pulls the endpoint further off than the shrinking
        # window did. Reflecting through the endpoint reproduces a constant
        # slope exactly, which is the case that was visibly wrong.
        pad_lo = [2 * vals[0] - vals[min(i, n - 1)] for i in range(half, 0, -1)]
        pad_hi = [2 * vals[-1] - vals[max(n - 1 - i, 0)] for i in range(1, half + 1)]
        src = pad_lo + list(vals) + pad_hi
        return [sum(src[i:i + window]) / window for i in range(n)]
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


def sample_segments(segments: list[list], sampler) -> list[tuple[list, list]]:
    """Densify each segment to ~30 m and sample its elevations ONCE.

    Returns [(points, elevs_m), ...]. Both `trail_gain_ft` and
    `trail_profile_ft` derive from this so a trail costs exactly one pass of
    DEM sampling — the expensive part — no matter how many outputs we want.
    """
    out = []
    for seg in segments:
        pts = densify([(p[0], p[1]) for p in seg if len(p) >= 2])
        if len(pts) < 2:
            continue
        out.append((pts, [sampler.elevation(la, lo) for la, lo in pts]))
    return out


def trail_gain_ft(segments: list[list], sampler) -> float:
    """Sum gain across a trail's segments (each a [[lat, lon], ...] polyline),
    densified to ~30 m and sampled via `sampler.elevation(lat, lon)`."""
    return round(sum(gain_ft(e) for _, e in sample_segments(segments, sampler)))


def _chain_segments(sampled: list[tuple[list, list]]) -> list[tuple[list, list]]:
    """Order sampled segments end-to-end, reversing any that fit backwards.

    OSM stores a trail's segments in arbitrary order and arbitrary direction.
    South Mountain's National Trail ships as 5 pieces whose consecutive ends sit
    3.2, 7.3, 9.7 and 15.1 km apart, yet its internal point spacing is sane (max
    192 m) — the pieces are genuinely one trail, just shuffled. Walking them in
    stored order is what put a fabricated cliff in the chart.

    Greedy nearest-end chaining: start from the first segment, then repeatedly
    take whichever remaining segment has an endpoint closest to the current
    tail, flipping it if its far end is the nearer one. That is O(n^2) in
    segment count, which is nothing — the 99th-percentile trail has a handful of
    segments, and the work is trivial beside the DEM sampling that produced this
    input.

    Makes no claim to fix genuinely disconnected trails: it only guarantees the
    best available ordering. The caller still advances x by the real gap
    distance, so whatever separation survives is drawn as ground rather than as
    a vertical step.
    """
    if len(sampled) < 2:
        return sampled

    def ends(seg):
        pts = seg[0]
        return pts[0], pts[-1]

    def build(start: int, flip_start: bool):
        remaining = list(sampled)
        first = remaining.pop(start)
        chain = [(first[0][::-1], first[1][::-1]) if flip_start else first]
        total = 0.0
        while remaining:
            tail = ends(chain[-1])[1]
            best_i, best_d, best_flip = 0, float("inf"), False
            for i, seg in enumerate(remaining):
                head, end = ends(seg)
                d_head = haversine_m(tail[0], tail[1], head[0], head[1])
                d_end = haversine_m(tail[0], tail[1], end[0], end[1])
                if d_head < best_d:
                    best_i, best_d, best_flip = i, d_head, False
                if d_end < best_d:
                    best_i, best_d, best_flip = i, d_end, True
            pts, es = remaining.pop(best_i)
            chain.append((pts[::-1], es[::-1]) if best_flip else (pts, es))
            total += best_d
        return total, chain

    # Greedy from a fixed start is order-dependent: beginning in the MIDDLE of a
    # trail strands one arm and leaves a long jump at the end. National Trail
    # chained to gaps of 178/15/73 m plus a stray 15 km until the start was
    # chosen properly. Segment counts are tiny, so try every start (and both
    # directions) and keep whichever ordering leaves the least total gap.
    best = min((build(i, f) for i in range(len(sampled)) for f in (False, True)),
               key=lambda tc: tc[0])
    return best[1]


# Samples per mile in the shipped elevation profile.
#
# Was 8/mile (cap 64), which read as "smoothed out" on a real device — and it
# was, measurably. Checked against 25 recorded GPS tracks, denoised and treated
# as ground truth: resampling them at 8/mile preserved only 62% of the real
# elevation change, with ~5.7 ft median error. At 32/mile it is 91% and ~1.6 ft.
#
# 32 is the right stopping point, not a round number. The DEM underneath is
# 30 m (~98 ft) and is already walked at that spacing — `n` only controls the
# FINAL downsample, so denser profiles cost no extra publish time, just bytes.
# 32/mile lands at ~165 ft between samples, comfortably above the source
# resolution; 48 or 64 would interpolate detail the DEM never had.
#
# Cost, measured over 60 sampled areas: +1-3 KB gzipped on a typical area
# (~2 KB today), and about +0.2 MB across the sample. The worst case is
# Bridger-Teton at +111 KB on an already-1.1 MB file.
_POINTS_PER_MILE = 32

def profile_and_gaps(sampled: list[tuple[list, list]],
                     max_points: int = 256) -> tuple[list[int], list[list[int]]]:
    """Evenly-spaced elevation series in FEET along the trail, for the app's
    elevation-profile chart. Returns [] when there's nothing to sample.

    DIRECTION IS ARBITRARY, and that is fine. OSM way order is meaningless
    (Humphreys Summit Trail is stored summit->trailhead), so index 0 is NOT
    "the trailhead" and must never be drawn as one. The app anchors the chart
    on the hiker's snapped position instead, which sidesteps the question
    entirely — that is why this ships as a bare series with no start/end
    semantics attached. `gain_ft` stays direction-invariant for the same reason.

    Evenly spaced by DISTANCE, so the app maps position -> index with a plain
    `fraction * (count - 1)` and needs no distance array of its own. Point
    count scales with length (`_POINTS_PER_MILE`, floor 8, cap `max_points`).
    Smoothed before downsampling for the same reason `gain_ft` smooths — raw
    30 m DEM is noisy enough to draw visible teeth.
    """
    # Order the segments end-to-end BEFORE flattening, then walk them.
    #
    # OSM gives a trail's segments in arbitrary order, and concatenating them
    # blind adds ZERO distance across a seam while stepping the full elevation
    # difference — an infinitely steep wall that smoothing and downsampling turn
    # into a plausible-looking cliff. Reported from the shipped app: South
    # Mountain's National Trail fell 1,048 ft over one 1,269 ft sample interval
    # (-82.6%) where its stored segments jumped 3-15 km across the park.
    # Measured 2026-07-19: 7,834 of 91,976 profiled trails (8.5%) carried at
    # least one such seam, the worst a 659 km jump on the California Coastal
    # Trail.
    #
    # `trail_gain_ft` was never affected — it sums gain PER SEGMENT and so never
    # crosses a seam. This is a profile-only defect.
    ordered = _chain_segments(sampled)

    # Gap distance is deliberately NOT added to `run`. `distanceMi` is the sum of
    # SEGMENT lengths and excludes gaps, and the app labels the x-axis from
    # `distanceMi` while mapping position -> index as `fraction * (count - 1)`.
    # Adding gaps here would stretch the series past the axis it is drawn
    # against: National Trail would span 24.72 mi under a 15.14 mi label. So the
    # series stays exactly as long as the trail, and ordering does the work.
    dists: list[float] = []
    elevs: list[float] = []
    seams: list[tuple[float, float]] = []   # (distance_along, gap_metres)
    run = 0.0
    prev_end = None
    for pts, es in ordered:
        if prev_end is not None:
            g = haversine_m(prev_end[0], prev_end[1], pts[0][0], pts[0][1])
            if g > _GAP_MIN_M:
                # Record WHERE the discontinuity falls along the series. The gap
                # itself takes no x — see above — so it can only be marked, not
                # spaced. The app draws a break here rather than a line implying
                # you can walk it.
                seams.append((run, g))
        for i, ((la, lo), e) in enumerate(zip(pts, es)):
            if i:
                run += haversine_m(pts[i - 1][0], pts[i - 1][1], la, lo)
            dists.append(run)
            elevs.append(e)
        prev_end = (pts[-1][0], pts[-1][1])
    if len(elevs) < 2:
        return [], []
    total = dists[-1]
    if total <= 0:
        return [], []
    # reflect=True: keep the ends of the ramp honest — see smooth().
    elevs = smooth(elevs, reflect=True)
    miles = total / 1609.344
    n = min(max_points, max(8, round(miles * _POINTS_PER_MILE)))
    out: list[int] = []
    j = 0
    for k in range(n):
        target = total * k / (n - 1)
        while j + 1 < len(dists) and dists[j + 1] < target:
            j += 1
        # Linear interpolation between the two bracketing DEM samples.
        if j + 1 < len(dists) and dists[j + 1] > dists[j]:
            f = (target - dists[j]) / (dists[j + 1] - dists[j])
            e = elevs[j] + (elevs[j + 1] - elevs[j]) * f
        else:
            e = elevs[j]
        out.append(int(round(e / _M_PER_FT)))

    # Map each seam's distance-along onto the DOWNSAMPLED index it precedes, so
    # the app can mark it without carrying a distance array. A gap takes no x
    # (see above), so it is reported as the boundary between two samples.
    gaps: list[list[int]] = []
    for at, metres in seams:
        idx = min(n - 1, max(1, round(at / total * (n - 1))))
        gaps.append([idx, int(round(metres))])
    return out, gaps


def profile_ft(sampled: list[tuple[list, list]], max_points: int = 256) -> list[int]:
    """Back-compat wrapper: the series alone, without seam positions."""
    return profile_and_gaps(sampled, max_points)[0]


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
            # One DEM pass feeds both gain and the chart series.
            sampled = sample_segments(t.get("segments", []), sampler)
            g = round(sum(gain_ft(e) for _, e in sampled))
            prof, prof_gaps = profile_and_gaps(sampled)
        except Exception as e:                      # a bad/missing tile: skip trail
            print(f"    ! gain failed for {t.get('id')}: {e}", file=sys.stderr)
            continue
        old = t.get("difficulty")
        new = difficulty_label(miles, g)
        t["gainFt"] = int(g)
        t["difficulty"] = new
        if prof:
            t["profileFt"] = prof
            # Only present when the trail actually has a discontinuity, so the
            # common case costs nothing. Additive: an older app ignores it and
            # renders exactly as before.
            if prof_gaps:
                t["profileGaps"] = prof_gaps
            else:
                t.pop("profileGaps", None)
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
