"""Shared constants and helpers for the trail-index build pipeline.

Lifted here so the legacy Overpass pipeline (seed-areas.py +
build-trail-counts.py) and the new PBF pipeline
(build-index-from-pbf.py) cannot drift during parity testing. When
PR J.4 retires the Overpass scripts this module becomes the
authoritative home for the constants and helpers — the deletion is
mechanical at that point.

Nothing here makes network calls or imports osmium / shapely. Pure
Python plus stdlib.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

# ---------- Paths ----------

ROOT = Path(__file__).resolve().parent.parent
INDEX_PATH = ROOT / "public" / "areas" / "index.json"
# Legacy cache (Overpass pipeline). Dies with PR J.4.
CACHE_PATH = ROOT / "public" / "areas" / "counts-cache.json"
# New cache (PBF pipeline). Just {relation_id: slug} — preserves slug
# stability across rebuilds when OSM renames an area.
OSM_ID_MAP_PATH = ROOT / "public" / "areas" / "osm-id-map.json"
SILHOUETTES_DIR = ROOT / "public" / "areas" / "silhouettes"
GEOM_DIR = ROOT / "public" / "areas" / "geom"
SCRIPTS_DIR = Path(__file__).resolve().parent
SEED_INCLUDE = SCRIPTS_DIR / "seeds-include.txt"
SEED_EXCLUDE = SCRIPTS_DIR / "seeds-exclude.txt"

# ---------- Filter thresholds ----------

MIN_TRAIL_MI = 0.59

SILHOUETTE_SPACING_M = 20.0
SILHOUETTE_DECIMALS = 5
# Cap the number of trails contributing to a silhouette — past ~400
# the rendered card just becomes noise.
SILHOUETTE_MAX_TRAILS = 400

GEOM_SPACING_M = 5.0
GEOM_DECIMALS = 6

# ---------- Area-quality filter ----------

# protect_class values that map to "real outdoor destinations" per OSM
# wiki. Loosely: recognized reserve / park / wilderness. Filters out
# heritage sites, botanical gardens, etc.
ALLOWED_PROTECT_CLASSES = {
    "1",
    "1a", "1b",
    "2",   # National park
    "3",   # Natural monument
    "4",   # Habitat / species management
    "5",   # Protected landscape
    "6",   # Resource / managed
    "11",  # Wilderness area (US-specific)
    "12",
    "21",  # Locally protected
    "22",  # Animal sanctuary
    "97", "98", "99",
}

# When protect_class is missing, fall back to a name-keyword whitelist.
# Catches state parks, national forests, regional/county parks, etc.
# that don't tag protect_class (common in OSM US data).
NAME_KEYWORD_RE = re.compile(
    # English + French keywords. Word boundaries on both sides since
    # both languages use whitespace between these terms.
    r"\b(park|preserve|wilderness|forest|monument|recreation area|"
    r"recreation site|refuge|sanctuary|reserve|open space|"
    r"conservation|wildlife|trailhead|trail system|nra|sra"
    # French (Quebec / other French-speaking jurisdictions).
    r"|parc|réserve|aire|faunique|écologique|sauvage|naturelle"
    r"|sanctuaire|forêt)\b"
    # Danish keywords. NO left word boundary because Danish compounds
    # words ("Naturpark", "Mols-Bjerge-fredning"). Right boundary
    # stays so "naturparken" matches via its base.
    r"|(naturpark|nationalpark|naturreservat|vildtreservat|"
    r"fredning|naturskov|vådområde)",
    re.IGNORECASE,
)

# ---------- Region codes ----------

STATE_NAMES = {
    # US states (ISO3166-2 subdivision codes without the "US-" prefix).
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut",
    "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida",
    "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky",
    "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio",
    "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming",

    # Countries (ISO3166-1).
    "DK": "Denmark",

    # Canadian provinces / territories (ISO3166-2). Each is its own
    # region — Canada is too large for a single country-wide Overpass
    # query, and the bare "CA" would collide with California.
    "CA-AB": "Alberta", "CA-BC": "British Columbia",
    "CA-MB": "Manitoba", "CA-NB": "New Brunswick",
    "CA-NL": "Newfoundland and Labrador", "CA-NS": "Nova Scotia",
    "CA-NT": "Northwest Territories", "CA-NU": "Nunavut",
    "CA-ON": "Ontario", "CA-PE": "Prince Edward Island",
    "CA-QC": "Quebec", "CA-SK": "Saskatchewan", "CA-YT": "Yukon",
}

# Subset of STATE_NAMES that are country-level (ISO3166-1) rather
# than US-state subdivisions.
COUNTRY_CODES: set[str] = {"DK"}

# Display-name override for `row[2]` (the user-facing state/country
# label shown under each area card on iOS). STATE_NAMES keeps the
# canonical per-region name for internal tracking; this override only
# affects the UI label so e.g. all Canadian provinces collapse to
# "Canada" in the Browse list.
DISPLAY_STATE_OVERRIDES: dict[str, str] = {
    "CA-AB": "Canada", "CA-BC": "Canada", "CA-MB": "Canada",
    "CA-NB": "Canada", "CA-NL": "Canada", "CA-NS": "Canada",
    "CA-NT": "Canada", "CA-NU": "Canada", "CA-ON": "Canada",
    "CA-PE": "Canada", "CA-QC": "Canada", "CA-SK": "Canada",
    "CA-YT": "Canada",
}

# Common non-ASCII letter → ASCII transliterations. Danish ø/æ/å must
# fold to ASCII for the slug to work as both a file path (R2 / iOS
# bundle) and a stable id.
ASCII_TRANSLIT: dict[str, str] = {
    "ø": "o", "Ø": "O",
    "æ": "ae", "Æ": "Ae",
    "å": "a", "Å": "A",
    "ö": "o", "Ö": "O",
    "ä": "a", "Ä": "A",
    "ü": "u", "Ü": "U",
    "ß": "ss",
    "ñ": "n", "Ñ": "N",
    "é": "e", "É": "E",
    "è": "e", "È": "E",
    "ê": "e", "Ê": "E",
    "ó": "o", "Ó": "O",
    "í": "i", "Í": "I",
    "á": "a", "Á": "A",
    "ú": "u", "Ú": "U",
    "ç": "c", "Ç": "C",
}


def display_state(code: str) -> str:
    """User-facing state/country label for a region code."""
    return DISPLAY_STATE_OVERRIDES.get(code) or STATE_NAMES.get(code, code)


def code_from_slug(slug: str) -> str | None:
    """Extract the ISO code suffix from a slug. Returns the code in
    its original case from STATE_NAMES, or None if the slug doesn't
    end with any known code. Used by `--resume` and by the PBF
    pipeline's slug-stability path."""
    slug_lower = slug.lower()
    # Longest first so 'CA-PE' beats a hypothetical 'PE' bare code.
    for code in sorted(STATE_NAMES.keys(), key=len, reverse=True):
        if slug_lower.endswith(f"-{code.lower()}"):
            return code
    return None


def slugify(name: str, state_code: str) -> str:
    """Stable area slug. Mirrors AreaDataService.slugify on iOS so
    recorded-hike completions stay valid across builds."""
    s = name
    for src, dst in ASCII_TRANSLIT.items():
        s = s.replace(src, dst)
    s = s.lower()
    s = re.sub(r"[^\w\s-]", "", s, flags=re.ASCII)
    s = re.sub(r"[-\s]+", "-", s).strip("-")
    return f"{s[:60]}-{state_code.lower()}"


def is_quality(tags: dict) -> bool:
    """Whether an OSM relation's tags qualify it as an outdoor area
    we want to surface. protect_class whitelist OR name keyword."""
    if tags.get("access") == "private":
        return False
    name = (tags.get("name") or "").strip()
    if not name:
        return False
    pc = (tags.get("protect_class") or "").strip().lower()
    if pc and pc in ALLOWED_PROTECT_CLASSES:
        return True
    return bool(NAME_KEYWORD_RE.search(name))


def load_overrides(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {
        line.strip().lower()
        for line in path.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    }


def atomic_write(path: Path, payload: str) -> None:
    """Write `payload` to `path` atomically via temp + rename."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload)
    tmp.replace(path)


# ---------- Trail-level helpers ----------

EARTH_R_M = 6_371_000.0
EARTH_R_MI = 3958.8


def dist_mi(coords) -> float:
    """Polyline length in miles. Haversine, used for trail filtering."""
    total = 0.0
    for i in range(1, len(coords)):
        la1, lo1 = coords[i - 1]
        la2, lo2 = coords[i]
        d_la = (la2 - la1) * math.pi / 180
        d_lo = (lo2 - lo1) * math.pi / 180
        a = (math.sin(d_la / 2) ** 2
             + math.cos(la1 * math.pi / 180) * math.cos(la2 * math.pi / 180)
             * math.sin(d_lo / 2) ** 2)
        total += EARTH_R_M * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return total / 1609.344


def haversine_mi(lat1, lon1, lat2, lon2) -> float:
    d_la = math.radians(lat2 - lat1)
    d_lo = math.radians(lon2 - lon1)
    a = (math.sin(d_la / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(d_lo / 2) ** 2)
    return EARTH_R_MI * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _haversine_m(lat1, lon1, lat2, lon2) -> float:
    d_la = math.radians(lat2 - lat1)
    d_lo = math.radians(lon2 - lon1)
    a = (math.sin(d_la / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(d_lo / 2) ** 2)
    return EARTH_R_M * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def node_key(lat, lon, cell=0.0001) -> str:
    return f"{round(lat / cell)}:{round(lon / cell)}"


def neighbor_keys(lat, lon, cell=0.0001) -> set[str]:
    r = round(lat / cell)
    c = round(lon / cell)
    return {f"{r+dr}:{c+dc}" for dr in (-1, 0, 1) for dc in (-1, 0, 1)}


def _difficulty(tags: dict, miles: float) -> str:
    """Single-char difficulty for silhouettes. Mirrors iOS."""
    sac = (tags.get("sac_scale") or "").strip()
    if sac and sac != "hiking":
        return "h"
    if miles > 4:
        return "h"
    if miles > 2 or tags.get("trail_visibility") == "intermediate":
        return "m"
    return "e"


def _difficulty_label(tags: dict, miles: float) -> str:
    """Full label for geom files (iOS Trail.Difficulty rawValue)."""
    return {"e": "Easy", "m": "Moderate", "h": "Hard"}[_difficulty(tags, miles)]


def _trail_slug(name: str) -> str:
    """Mirrors AreaDataService.slugify for trail-name → trail-id."""
    parts = re.split(r"[^a-z0-9]+", name.lower())
    parts = [p for p in parts if p]
    return "-".join(parts)[:60]


def _downsample(coords: list, spacing_m: float) -> list:
    """Keep first point, then any point >= spacing_m from the last
    kept point. Always emits endpoints if 2+ points."""
    if len(coords) < 2:
        return list(coords)
    kept = [coords[0]]
    for p in coords[1:-1]:
        if _haversine_m(kept[-1][0], kept[-1][1], p[0], p[1]) >= spacing_m:
            kept.append(p)
    kept.append(coords[-1])
    return kept


def _is_road_like(tags: dict, name: str) -> bool:
    """Drops forest-service / utility roads tagged as highway=track."""
    if tags.get("highway") != "track":
        return False
    road_words = ("road", "drive", "avenue", "canal", "drain", "ditch",
                  "boulevard", "highway", "freeway")
    lower = (name or "").lower()
    if any(w in lower for w in road_words):
        return True
    if tags.get("motor_vehicle") == "yes" or tags.get("motorcar") == "yes":
        return True
    if tags.get("access") == "private":
        return True
    return False


# ---------- Per-area finalize (shared between pipelines) ----------


def finalize_area(by_name: dict) -> tuple[int, float, dict | None, list, list | None]:
    """Given a per-area accumulator `{name: {miles, tags, segments}}`,
    return (trail_count, total_mi, silhouette, geom_trails, geom_bbox).

    Lifted verbatim from build-trail-counts.build_counts' tail (the
    portion AFTER ways are accumulated into by_name). Both pipelines
    feed this the same shape so output is identical.
    """
    qualifying = {k: v for k, v in by_name.items() if v["miles"] >= MIN_TRAIL_MI}
    trail_count = len(qualifying)
    total_mi = round(sum(t["miles"] for t in qualifying.values()), 2)

    geom_trails: list = []
    g_min_lat = g_min_lon = float("inf")
    g_max_lat = g_max_lon = float("-inf")
    slug_counts: dict = {}
    for name in sorted(qualifying.keys()):
        info = qualifying[name]
        miles = info["miles"]
        ds_segments: list = []
        for seg in info["segments"]:
            ds = _downsample(seg, GEOM_SPACING_M)
            if len(ds) < 2:
                continue
            pts = [
                [round(p[0], GEOM_DECIMALS), round(p[1], GEOM_DECIMALS)]
                for p in ds
            ]
            for la, lo in pts:
                if la < g_min_lat: g_min_lat = la
                if la > g_max_lat: g_max_lat = la
                if lo < g_min_lon: g_min_lon = lo
                if lo > g_max_lon: g_max_lon = lo
            ds_segments.append(pts)
        base_slug = _trail_slug(name)
        seen = slug_counts.get(base_slug, 0)
        slug_counts[base_slug] = seen + 1
        trail_id = base_slug if seen == 0 else f"{base_slug}-{seen}"
        geom_trails.append({
            "id": trail_id,
            "name": name,
            "distanceMi": round(miles, 2),
            "difficulty": _difficulty_label(info["tags"], miles),
            "segments": ds_segments,
        })
    if g_min_lat != float("inf"):
        geom_bbox = [
            round(g_min_lon, GEOM_DECIMALS),
            round(g_min_lat, GEOM_DECIMALS),
            round(g_max_lon, GEOM_DECIMALS),
            round(g_max_lat, GEOM_DECIMALS),
        ]
    else:
        geom_bbox = None

    if not qualifying:
        return trail_count, total_mi, None, geom_trails, geom_bbox

    silhouette_trails = sorted(
        qualifying.values(), key=lambda t: -t["miles"]
    )[:SILHOUETTE_MAX_TRAILS]

    lines = []
    min_lat = min_lon = float("inf")
    max_lat = max_lon = float("-inf")
    for trail in silhouette_trails:
        d = _difficulty(trail["tags"], trail["miles"])
        for seg in trail["segments"]:
            ds = _downsample(seg, SILHOUETTE_SPACING_M)
            if len(ds) < 2:
                continue
            pts = [[round(p[0], SILHOUETTE_DECIMALS),
                    round(p[1], SILHOUETTE_DECIMALS)] for p in ds]
            for la, lo in pts:
                if la < min_lat: min_lat = la
                if la > max_lat: max_lat = la
                if lo < min_lon: min_lon = lo
                if lo > max_lon: max_lon = lo
            lines.append({"d": d, "p": pts})

    silhouette = {
        "b": [
            round(min_lon, SILHOUETTE_DECIMALS),
            round(min_lat, SILHOUETTE_DECIMALS),
            round(max_lon, SILHOUETTE_DECIMALS),
            round(max_lat, SILHOUETTE_DECIMALS),
        ],
        "l": lines,
    }
    return trail_count, total_mi, silhouette, geom_trails, geom_bbox


def apply_named_endpoint_filter(by_name: dict) -> dict:
    """Drop unnamed-way entries whose endpoints don't touch a
    named-trail endpoint. Mirrors the per-area named-endpoint
    heuristic from build-trail-counts.build_counts but operates on
    the already-accumulated `by_name` dict (the PBF pipeline can't
    look ahead while streaming, so it accumulates then filters)."""
    named_nodes: set[str] = set()
    for name, info in by_name.items():
        if name.startswith("Unnamed "):
            continue
        for seg in info["segments"]:
            for lat, lon in seg:
                named_nodes.add(node_key(lat, lon))

    out: dict = {}
    for name, info in by_name.items():
        if not name.startswith("Unnamed "):
            out[name] = info
            continue
        endpoints: list[tuple[float, float]] = []
        for seg in info["segments"]:
            if len(seg) >= 2:
                endpoints.append((seg[0][0], seg[0][1]))
                endpoints.append((seg[-1][0], seg[-1][1]))
        touches = any(
            neighbor_keys(lat, lon) & named_nodes for lat, lon in endpoints
        )
        if touches:
            out[name] = info
    return out


# ---------- Threshold + dedup ----------


def apply_threshold(index: list, min_trails: int, min_miles: float) -> list:
    if min_trails <= 0 and min_miles <= 0:
        return index
    out = []
    dropped = 0
    for area in index:
        if len(area) < 7:
            out.append(area)
            continue
        trail_count = area[5]
        total_mi = area[6]
        if trail_count < min_trails or total_mi < min_miles:
            dropped += 1
            continue
        out.append(area)
    if dropped:
        print(
            f"Threshold: dropped {dropped} areas with < {min_trails} trails "
            f"or < {min_miles} mi"
        )
    return out


def deduplicate(index: list) -> list:
    """Drop near-duplicate areas (<0.1 mi apart, one name a word-subset
    of the other). Keeps the entry with more trails; ties go to the
    longer name."""
    kept: list = []
    for area in index:
        a_name = area[1].lower()
        a_trails = area[5] if len(area) > 5 else -1
        merged = False
        for i, b in enumerate(kept):
            if haversine_mi(area[3], area[4], b[3], b[4]) >= 0.1:
                continue
            a_words = set(a_name.split())
            b_words = set(b[1].lower().split())
            short, long_ = (
                (a_words, b_words) if len(a_words) <= len(b_words)
                else (b_words, a_words)
            )
            if not short.issubset(long_):
                continue
            b_trails = b[5] if len(b) > 5 else -1
            if (a_trails > b_trails or
                    (a_trails == b_trails and len(area[1]) > len(b[1]))):
                print(f"Dedup: kept '{area[1]}' over '{b[1]}'")
                kept[i] = area
            else:
                print(f"Dedup: kept '{b[1]}' over '{area[1]}'")
            merged = True
            break
        if not merged:
            kept.append(area)
    return kept


# ---------- Writers ----------


def write_silhouette(area_id: str, silhouette: dict) -> None:
    """Write one area's silhouette to public/areas/silhouettes/<id>.json."""
    SILHOUETTES_DIR.mkdir(parents=True, exist_ok=True)
    path = SILHOUETTES_DIR / f"{area_id}.json"
    path.write_text(json.dumps(silhouette, separators=(",", ":")))


def write_geom_file(
    *,
    area_id: str,
    name: str,
    state: str,
    center_lat: float,
    center_lon: float,
    trail_count: int,
    total_mi: float,
    osm_relation_id: int | None,
    geom_trails: list,
    geom_bbox: list | None,
    cached_at: str,
) -> None:
    """Write one area's full geometry to public/areas/geom/<id>.json.
    `cached_at` is passed in (not derived inside) so the PBF pipeline
    can stamp every file with the same build timestamp for clean
    diffs against prior builds."""
    GEOM_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "id": area_id,
        "name": name,
        "state": state,
        "center_lat": center_lat,
        "center_lon": center_lon,
        "zoom": 13,
        "bbox": geom_bbox,
        "trails": geom_trails,
        "trail_count": trail_count,
        "total_mi": total_mi,
        "cached_at": cached_at,
        "osm_relation_id": osm_relation_id,
    }
    out_path = GEOM_DIR / f"{area_id}.json"
    out_path.write_text(json.dumps(payload, separators=(",", ":")))
