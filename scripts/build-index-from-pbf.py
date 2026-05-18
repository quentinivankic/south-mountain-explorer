#!/usr/bin/env python3
"""Build the trail index from Geofabrik PBF extracts (no Overpass).

Produces the same artifacts as the old seed-areas.py + build-trail-
counts.py pipeline:

  - public/areas/index.json       7- or 8-tuple per area
  - public/areas/silhouettes/<id>.json
  - public/areas/geom/<id>.json
  - public/areas/osm-id-map.json  {relation_id: slug} for slug stability

Algorithm — three streaming passes over each filtered PBF (one per
continent):

  Pass 1: collect admin region polygons (admin_level=2,4 matching
          requested ISO codes). Drives state/country containment.
  Pass 2: collect area candidates (boundary=protected_area/national_park
          and leisure=nature_reserve). For each, run the same
          is_quality() predicate the Overpass pipeline uses; resolve
          the containing region by polygon test. Build a STRtree.
  Pass 3: stream highway=path/footway/track/bridleway ways, attach
          to the area whose polygon contains the way's midpoint.

After the pass-3 walk, run apply_named_endpoint_filter (drops unnamed
ways whose endpoints don't touch a named-trail endpoint) and
finalize_area (downsample, slug, write silhouette + geom). Threshold
+ dedup the index. Emit everything.

Constants and helpers live in `_seed_constants.py` so this script
cannot drift from the legacy Overpass pipeline during parity testing.

Usage:

  python3 scripts/build-index-from-pbf.py \\
      --pbf $RUNNER_TEMP/north-america-filtered.osm.pbf \\
            $RUNNER_TEMP/europe-filtered.osm.pbf \\
      --min-trails 3 --min-miles 2

Optional --regions limits which ISO codes get rebuilt (handy for
parity tests: --regions DK runs Denmark only). Default rebuilds the
full seed list baked into _seed_constants.STATE_NAMES.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Allow `from _seed_constants import ...` when invoked from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _seed_constants import (  # noqa: E402
    GEOM_DIR,
    INDEX_PATH,
    OSM_ID_MAP_PATH,
    SEED_EXCLUDE,
    SEED_INCLUDE,
    SILHOUETTES_DIR,
    STATE_NAMES,
    _is_road_like,
    apply_named_endpoint_filter,
    apply_threshold,
    atomic_write,
    code_from_slug,
    deduplicate,
    display_state,
    dist_mi,
    finalize_area,
    is_quality,
    load_overrides,
    slugify,
    write_geom_file,
    write_silhouette,
)

# These imports are deferred to keep `--help` and dry-runs working
# without osmium / shapely installed. The actual passes require them.
ALLOWED_HIGHWAYS = {"path", "footway", "track", "bridleway"}
BOUNDARY_VALUES = {"protected_area", "national_park"}


# ---------- Region polygon resolution ----------


def _ensure_imports():
    """Import osmium and shapely. Deferred so the script can be parsed
    and `--help`'d without the deps installed."""
    try:
        import osmium  # noqa: F401
        import shapely  # noqa: F401
        import shapely.wkb  # noqa: F401
        from shapely.geometry import Point  # noqa: F401
        from shapely.strtree import STRtree  # noqa: F401
    except ImportError as e:
        sys.stderr.write(
            f"\nMissing dependency: {e}\n"
            "Install with: pip install osmium shapely\n"
        )
        sys.exit(2)


def _region_iso_codes_for(regions: list[str]) -> tuple[set[str], set[str]]:
    """Split requested region codes into ISO3166-1 (country) and
    ISO3166-2 (subdivision) sets. US-state codes get the "US-" prefix
    added so they match what OSM tags carry. Denmark stays as "DK"."""
    iso1: set[str] = set()
    iso2: set[str] = set()
    for code in regions:
        if "-" in code:
            iso2.add(code)
        elif code in STATE_NAMES and len(code) == 2 and code not in {"DK"}:
            # US state — OSM carries ISO3166-2 = "US-AZ".
            iso2.add(f"US-{code}")
        else:
            iso1.add(code)
    return iso1, iso2


def _region_code_from_tags(tags) -> str | None:
    """Given an admin relation's tags, return the matching region
    code in our internal form (US state 'AZ', country 'DK',
    province 'CA-AB'). Returns None if no match."""
    iso2 = tags.get("ISO3166-2")
    if iso2:
        if iso2.startswith("US-"):
            return iso2[3:]
        return iso2
    iso1 = tags.get("ISO3166-1")
    if iso1:
        return iso1
    return None


# ---------- Pass 1 + 2: region polys & area candidates ----------


class _RegionAndAreaHandler:
    """Pyosmium handler that streams a filtered PBF once, collecting:
      - admin region polygons (admin_level in {2,4} matching our codes)
      - area candidates (boundary=protected_area/national_park, leisure=nature_reserve)

    Stored on the instance for the caller to consume after apply_file()."""

    def __init__(self, requested_codes: set[str]):
        # `requested_codes` is the full set in our internal form
        # ('AZ', 'DK', 'CA-AB'). Region polygons whose code matches
        # are kept; everything else is discarded to save memory.
        _ensure_imports()
        import osmium
        from osmium.geom import WKBFactory

        self._wkb = WKBFactory()
        self._osmium = osmium
        self.requested_codes = requested_codes
        # code → shapely (Multi)Polygon for the admin boundary
        self.region_polys: dict[str, object] = {}
        # list of {
        #   "osm_id": int, "name": str, "tags": dict,
        #   "polygon": shapely (Multi)Polygon, "centroid": (lat, lon),
        #   "bbox": [w,s,e,n]
        # }
        self.area_candidates: list[dict] = []
        self._handler = self._build_handler()

    def _build_handler(self):
        outer = self

        class _Inner(self._osmium.SimpleHandler):
            def area(self, a):
                outer._on_area(a)

        return _Inner()

    def apply_file(self, path: str) -> None:
        self._handler.apply_file(path, locations=True, idx="flex_mem")

    def _on_area(self, a):
        tags = {t.k: t.v for t in a.tags}
        if not tags:
            return

        admin_level = tags.get("admin_level")
        if admin_level in {"2", "4"}:
            code = _region_code_from_tags(tags)
            if code and code in self.requested_codes:
                poly = self._area_to_shapely(a)
                if poly is not None:
                    # If multiple admin relations claim the same code
                    # (rare but happens for disputed/overlapping
                    # subdivisions), prefer the higher admin level —
                    # admin_level=2 outranks 4 in our schema.
                    existing = self.region_polys.get(code)
                    if existing is None or admin_level == "2":
                        self.region_polys[code] = poly
            return

        # Area candidate?
        boundary = tags.get("boundary")
        leisure = tags.get("leisure")
        if boundary not in BOUNDARY_VALUES and leisure != "nature_reserve":
            return
        if not is_quality(tags):
            return

        poly = self._area_to_shapely(a)
        if poly is None:
            return

        cx, cy = poly.centroid.x, poly.centroid.y
        # shapely point is (lon, lat); convert to our (lat, lon) tuple.
        bounds = poly.bounds  # (minx, miny, maxx, maxy) = (w, s, e, n)
        self.area_candidates.append({
            "osm_id": int(a.orig_id()),
            "name": tags["name"].strip(),
            "tags": tags,
            "polygon": poly,
            "centroid": (cy, cx),
            "bbox": [bounds[0], bounds[1], bounds[2], bounds[3]],
        })

    def _area_to_shapely(self, a):
        """Convert pyosmium Area → shapely (Multi)Polygon. Returns
        None on assembly failure (broken topology)."""
        import shapely.wkb
        try:
            wkb_hex = self._wkb.create_multipolygon(a)
        except Exception:
            return None
        try:
            return shapely.wkb.loads(bytes.fromhex(wkb_hex))
        except Exception:
            return None


# ---------- Pass 3: highway ways ----------


class _WayHandler:
    """Streams highway=path/footway/track/bridleway ways and
    accumulates per-area `by_name` dicts matching the shape the legacy
    pipeline's build_counts() builds."""

    def __init__(self, area_index: "_AreaIndex"):
        _ensure_imports()
        import osmium
        self._osmium = osmium
        self.area_index = area_index
        # area_idx (into area_index.candidates) → by_name dict
        self.per_area: dict[int, dict] = {}
        self._handler = self._build_handler()
        self._scanned = 0
        self._attached = 0

    def _build_handler(self):
        outer = self

        class _Inner(self._osmium.SimpleHandler):
            def way(self, w):
                outer._on_way(w)

        return _Inner()

    def apply_file(self, path: str) -> None:
        self._handler.apply_file(path, locations=True, idx="flex_mem")

    def _on_way(self, w):
        tags = {t.k: t.v for t in w.tags}
        highway = tags.get("highway")
        if highway not in ALLOWED_HIGHWAYS:
            return

        self._scanned += 1

        # Materialize coordinates. Out-of-extract nodes have invalid
        # locations; filter them out.
        coords: list[list[float]] = []
        for node in w.nodes:
            loc = node.location
            if not loc.valid():
                continue
            coords.append([loc.lat, loc.lon])
        if len(coords) < 2:
            return

        raw_name = tags.get("name", "").strip()

        # Drop "road-like" tracks even before the area lookup — they
        # never qualify for any area, and skipping saves a STRtree
        # query per row.
        if _is_road_like(tags, raw_name):
            return

        # Midpoint test against the spatial index.
        mid = coords[len(coords) // 2]
        area_idx = self.area_index.containing_index(mid[0], mid[1])
        if area_idx is None:
            return

        # Unnamed ways get a synthetic name; the named-endpoint filter
        # later drops the ones whose endpoints don't touch a named
        # trail's endpoint.
        if raw_name:
            name = raw_name
        else:
            name = f"Unnamed {w.id}"

        bucket = self.per_area.setdefault(area_idx, {})
        entry = bucket.get(name)
        if entry is None:
            entry = {"miles": 0.0, "tags": tags, "segments": []}
            bucket[name] = entry
        entry["miles"] += dist_mi(coords)
        entry["segments"].append(coords)
        self._attached += 1


# ---------- Spatial index over area candidates ----------


class _AreaIndex:
    """STRtree wrapper that returns the index of the area candidate
    whose polygon truly contains a given (lat, lon). Used by
    _WayHandler to attach trails to areas in O(log N + k) per query."""

    def __init__(self, candidates: list[dict]):
        _ensure_imports()
        from shapely.geometry import Point
        from shapely.strtree import STRtree

        self.candidates = candidates
        self._Point = Point
        self._tree = STRtree([c["polygon"] for c in candidates])
        # shapely 2.x STRtree.query returns ARRAY of indexes (not
        # geometries). Build a reverse map polygon-id → list index in
        # case we're on 1.x (returns geometries).
        self._poly_id_to_idx = {
            id(c["polygon"]): i for i, c in enumerate(candidates)
        }

    def containing_index(self, lat: float, lon: float) -> int | None:
        point = self._Point(lon, lat)
        # Two shapely API generations: 2.x returns ndarray of ints;
        # 1.x returns list of geometries. Handle both.
        result = self._tree.query(point)
        try:
            iterator = list(result)
        except TypeError:
            return None
        for hit in iterator:
            if isinstance(hit, int) or hasattr(hit, "__index__"):
                idx = int(hit)
                poly = self.candidates[idx]["polygon"]
            else:
                idx = self._poly_id_to_idx.get(id(hit))
                if idx is None:
                    continue
                poly = hit
            if poly.contains(point):
                return idx
        return None


# ---------- Index writing & osm-id-map ----------


def _load_osm_id_map() -> dict[int, str]:
    if not OSM_ID_MAP_PATH.exists():
        return {}
    raw = json.loads(OSM_ID_MAP_PATH.read_text())
    # Stored as {"<int>": "slug"} (JSON keys must be strings).
    out: dict[int, str] = {}
    for k, v in raw.items():
        try:
            out[int(k)] = str(v)
        except (TypeError, ValueError):
            continue
    return out


def _save_osm_id_map(mapping: dict[int, str]) -> None:
    OSM_ID_MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Sort by key for clean diffs.
    serialized = {
        str(k): mapping[k] for k in sorted(mapping.keys())
    }
    atomic_write(OSM_ID_MAP_PATH, json.dumps(serialized, indent=2))


# ---------- Main ----------


def _all_default_regions() -> list[str]:
    """Default region list when --regions is omitted: everything in
    STATE_NAMES except codes that conflict (none currently). Stable
    order matters for predictable PBF iteration order."""
    return sorted(STATE_NAMES.keys())


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--pbf",
        nargs="+",
        required=True,
        help="One or more filtered PBFs (post osmium tags-filter). "
        "Order doesn't matter — the script handles continental "
        "overlap by region containment.",
    )
    ap.add_argument(
        "--regions",
        nargs="+",
        default=None,
        help="ISO codes to build (e.g. 'DK US-AZ CA-AB'). Default: "
        "every code in STATE_NAMES.",
    )
    ap.add_argument("--min-trails", type=int, default=0)
    ap.add_argument("--min-miles", type=float, default=0.0)
    ap.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process at most N area candidates (for fast smoke tests).",
    )
    args = ap.parse_args()

    _ensure_imports()

    regions = args.regions or _all_default_regions()
    print(f"Building index for {len(regions)} regions: {' '.join(regions)}",
          file=sys.stderr, flush=True)
    print(f"PBFs: {' '.join(args.pbf)}", file=sys.stderr, flush=True)

    excludes = load_overrides(SEED_EXCLUDE)
    includes = load_overrides(SEED_INCLUDE)
    osm_id_map = _load_osm_id_map()
    # Reverse lookup: relation_id → historical slug (preserves slug
    # stability when OSM renames an area between builds).
    historical_slug_by_osm = dict(osm_id_map)
    # Reverse: slug → relation_id, used to detect when a stable slug
    # already belongs to a different relation (skip the historical
    # path in that case).
    historical_osm_by_slug: dict[str, int] = {
        slug: oid for oid, slug in osm_id_map.items()
    }

    # ---- Pass 1 + 2: region polygons + area candidates ----

    t0 = time.time()
    handler = _RegionAndAreaHandler(requested_codes=set(regions))
    for pbf in args.pbf:
        print(f"  Pass 1+2: streaming {pbf} for regions + area candidates...",
              file=sys.stderr, flush=True)
        handler.apply_file(pbf)

    missing = [r for r in regions if r not in handler.region_polys]
    if missing:
        print(
            f"  WARNING: {len(missing)} regions had no matching admin "
            f"polygon in the PBFs: {' '.join(missing)}",
            file=sys.stderr,
            flush=True,
        )
    print(
        f"  Found {len(handler.region_polys)} region polygons "
        f"and {len(handler.area_candidates)} area candidates "
        f"in {time.time() - t0:.1f}s",
        file=sys.stderr,
        flush=True,
    )

    # ---- Resolve each candidate to a region & build the index rows ----

    resolved: list[dict] = []
    dropped_no_region = 0
    dropped_excluded = 0
    slug_counts: dict[str, int] = {}

    for cand in handler.area_candidates:
        if cand["name"].lower() in excludes:
            dropped_excluded += 1
            continue

        from shapely.geometry import Point
        c_lat, c_lon = cand["centroid"]
        point = Point(c_lon, c_lat)

        # Find first region whose polygon truly contains the centroid.
        # Iterate in deterministic order so ties resolve identically
        # across runs.
        region_code: str | None = None
        for code in sorted(handler.region_polys.keys()):
            if handler.region_polys[code].contains(point):
                region_code = code
                break
        if region_code is None:
            dropped_no_region += 1
            continue

        # Slug resolution.
        osm_id = cand["osm_id"]
        historical = historical_slug_by_osm.get(osm_id)
        if historical:
            slug = historical
        else:
            base = slugify(cand["name"], region_code)
            seen = slug_counts.get(base, 0)
            # If a different relation already owns this base slug
            # historically, force the counter to start at 1 so we
            # don't collide with its identity.
            if seen == 0 and historical_osm_by_slug.get(base) not in (None, osm_id):
                seen = 1
            slug = base if seen == 0 else f"{base}-{seen + 1}"
            slug_counts[base] = seen + 1

        # Bookkeeping for future builds.
        osm_id_map[osm_id] = slug

        resolved.append({
            "osm_id": osm_id,
            "slug": slug,
            "name": cand["name"],
            "region_code": region_code,
            "state_label": display_state(region_code),
            "centroid": cand["centroid"],
            "bbox": cand["bbox"],
            "polygon": cand["polygon"],
            "tags": cand["tags"],
        })

    print(
        f"  Resolved {len(resolved)} candidates "
        f"(dropped {dropped_no_region} outside requested regions, "
        f"{dropped_excluded} via seeds-exclude.txt)",
        file=sys.stderr,
        flush=True,
    )

    if args.limit is not None:
        resolved = resolved[: args.limit]
        print(f"  --limit {args.limit}: trimmed to {len(resolved)} candidates",
              file=sys.stderr, flush=True)

    if not resolved:
        print("No area candidates resolved. Nothing to build.",
              file=sys.stderr)
        return

    # ---- Pass 3: highway ways ----

    t1 = time.time()
    area_index = _AreaIndex(resolved)
    way_handler = _WayHandler(area_index)
    for pbf in args.pbf:
        print(f"  Pass 3: streaming {pbf} for highway ways...",
              file=sys.stderr, flush=True)
        way_handler.apply_file(pbf)
    print(
        f"  Scanned {way_handler._scanned} highway ways, "
        f"attached {way_handler._attached} to areas "
        f"in {time.time() - t1:.1f}s",
        file=sys.stderr,
        flush=True,
    )

    # ---- Finalize each area ----

    cached_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    SILHOUETTES_DIR.mkdir(parents=True, exist_ok=True)
    GEOM_DIR.mkdir(parents=True, exist_ok=True)

    index_rows: list[list] = []
    for idx, area in enumerate(resolved):
        by_name = way_handler.per_area.get(idx, {})
        by_name = apply_named_endpoint_filter(by_name)
        trail_count, total_mi, silhouette, geom_trails, geom_bbox = (
            finalize_area(by_name)
        )

        write_geom_file(
            area_id=area["slug"],
            name=area["name"],
            state=area["state_label"],
            center_lat=round(area["centroid"][0], 4),
            center_lon=round(area["centroid"][1], 4),
            trail_count=trail_count,
            total_mi=total_mi,
            osm_relation_id=area["osm_id"],
            geom_trails=geom_trails,
            geom_bbox=geom_bbox,
            cached_at=cached_at,
        )
        if silhouette is not None:
            write_silhouette(area["slug"], silhouette)

        row = [
            area["slug"],
            area["name"],
            area["state_label"],
            round(area["centroid"][0], 4),
            round(area["centroid"][1], 4),
            trail_count,
            total_mi,
            area["osm_id"],
        ]
        index_rows.append(row)

    # ---- Threshold + dedup + write index ----

    index_rows = apply_threshold(index_rows, args.min_trails, args.min_miles)
    index_rows = deduplicate(index_rows)
    index_rows.sort(key=lambda r: (r[2], r[1]))

    atomic_write(INDEX_PATH, json.dumps(index_rows, separators=(",", ":")))
    _save_osm_id_map(osm_id_map)

    print(
        f"\nDone. {len(index_rows)} areas in index. "
        f"osm-id-map.json has {len(osm_id_map)} entries.",
        file=sys.stderr,
        flush=True,
    )


if __name__ == "__main__":
    main()
