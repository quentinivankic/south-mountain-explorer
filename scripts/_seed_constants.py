"""Shared constants and helpers for the trail-index build pipeline.

Lifted out of seed-areas.py and build-trail-counts.py so the two
scripts share one source of truth for state lists, name regex,
protect-class whitelist, slugify, difficulty math, downsample,
threshold + dedup, and write helpers.

Pure stdlib — no network calls.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

# ---------- Paths ----------

ROOT = Path(__file__).resolve().parent.parent
INDEX_PATH = ROOT / "public" / "areas" / "index.json"
CACHE_PATH = ROOT / "public" / "areas" / "counts-cache.json"
SILHOUETTES_DIR = ROOT / "public" / "areas" / "silhouettes"
GEOM_DIR = ROOT / "public" / "areas" / "geom"
SCRIPTS_DIR = Path(__file__).resolve().parent
SEED_INCLUDE = SCRIPTS_DIR / "seeds-include.txt"
SEED_EXCLUDE = SCRIPTS_DIR / "seeds-exclude.txt"

# ---------- Filter thresholds ----------

MIN_TRAIL_MI = 0.59

SILHOUETTE_SPACING_M = 20.0
SILHOUETTE_DECIMALS = 5
# Cap on the number of trails contributing to a silhouette. `None` = no cap:
# draw the whole network. The old 400 cap dated to System-1, whose networks
# were full of road/utility/junk ways, so past ~400 the card was noise. With
# curated trailforge geom every line is a real trail, so an uncapped silhouette
# is just more faithful. Only ~21 areas (big national forests, Adirondack Park
# at 1,217 trails) ever exceeded 400; point count plateaus past ~800 (the extra
# trails are short), so the file-size cost is bounded (~+30% on those few
# files). Used as a plain slice `[:SILHOUETTE_MAX_TRAILS]`, so None = all.
SILHOUETTE_MAX_TRAILS = None

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
    # English + French keywords, covering the whole shipped footprint
    # (US + anglophone Canada, plus Québec / francophone Canada). Word
    # boundaries on both sides since these languages use whitespace
    # between terms. The old Italian / Danish / German / Icelandic
    # branches were dropped — seeding is North-America-only, so those
    # never matched a shipped area (the "accidental Spanish coverage on
    # parco/riserva" note was also wrong: those are Italian, not Spanish).
    r"\b(park|preserve|wilderness|forest|monument|recreation area|"
    r"recreation site|refuge|sanctuary|reserve|open space|"
    r"conservation|wildlife|trailhead|trail system|nra|sra"
    # French (Québec / francophone Canada).
    r"|parc|réserve|aire|faunique|écologique|sauvage|naturelle"
    r"|sanctuaire|forêt)\b",
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
#
# Intentionally EMPTY: the app is North America only. Denmark /
# Iceland / Switzerland and the 24-country EU batch were seeded
# during an expansion experiment, but the OSM tagging produced too
# much low-signal noise (tiny nature_reserve fragments with no real
# trail coverage) to ship, so all non-NA data was removed and the
# seeder restricted back to US states + Canadian provinces. To
# re-introduce a country, add its ISO3166-1 code here and its display
# name to STATE_NAMES, then dispatch build-trail-index for it.
COUNTRY_CODES: set[str] = set()

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

# Common non-ASCII letter → ASCII transliterations. Slugs are used
# as both file paths (R2 / iOS bundle) and stable ids, so every
# letter in an area name must fold to ASCII. Adding a region with
# new diacritics? Add them here BEFORE seeding, or those areas
# silently lose characters via `re.sub([^\w\s-])`.
ASCII_TRANSLIT: dict[str, str] = {
    # Nordic (DK, IS, NO, SE, FI):
    "ø": "o", "Ø": "O",
    "æ": "ae", "Æ": "Ae",
    "å": "a", "Å": "A",
    "þ": "th", "Þ": "Th",
    "ð": "d", "Ð": "D",
    "ý": "y", "Ý": "Y",
    # German / Swiss German (CH, DE, AT):
    "ö": "o", "Ö": "O",
    "ä": "a", "Ä": "A",
    "ü": "u", "Ü": "U",
    "ß": "ss",
    # Romance + Iberian (FR, IT, ES, PT):
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


# ---------- Red-flag exclusion (private/restricted land signals) ----------
#
# Started as a manual-review tool (scripts/audit-easement-ownership.py) —
# v1 tried to REQUIRE positive proof of public ownership before trusting an
# area and flagged 439/2957 NY candidates, ~93% of them false positives
# (legitimate county parks, land trusts, NYC DEP watershed land the keyword
# list just didn't recognize). v2/v3 flipped to a narrow, evidence-backed
# set of actual red flags instead — proof of BADNESS, not goodness, which is
# a bounded problem. Verified against 17 real cases (2026-07-12 NY rollout)
# with zero false positives, plus clean 0-flag runs on GA and VT. Promoted
# from "flag for manual review" to an automatic is_quality() exclusion once
# that held up — the user explicitly signed off on trusting these categories
# so future seeding doesn't need a human to read and strip a review list by
# hand. scripts/audit-easement-ownership.py still exists as a lightweight
# after-the-fact report of what got auto-excluded (not a gate).
_RED_FLAGS_TITLE_DESC_ONLY = {
    "mine/mining/quarry": re.compile(r"\b(mine|mining|quarry|quarries)\b", re.IGNORECASE),
    "water supply/watershed": re.compile(r"\b(water supply|watershed)\b", re.IGNORECASE),
}
_HUNTING_FLAG = re.compile(r"\bhunting\b", re.IGNORECASE)

# Government agency naming is a small, closed, genuinely nationwide-consistent
# pattern — unlike land-trust names (unbounded, the v1 mistake), "Department
# of", "County", "City of", "Town of" etc. reliably signal a public operator
# regardless of which state or agency. Real example: 'Mongaup Valley
# Wildlife Management Area' has description="mine" (a historical-feature
# note, not an access restriction) but operator="New York State Department
# of Environmental Conservation" — obviously public despite the mine flag.
_GOVERNMENT_OPERATOR = re.compile(
    r"\b(department of|state of|county|city of|town of|village of|"
    r"national park service|forest service|bureau of land management|"
    r"fish and wildlife service|u\.?s\.? |commonwealth of)\b",
    re.IGNORECASE,
)

# Water-supply operators that run genuinely PUBLIC hiking land, so the
# water-supply flag shouldn't exclude them. Each is a real, verified example —
# NOT a speculative allowlist (proving legitimacy is unbounded; this stays a
# short list of specific operators we've confirmed open their watershed to
# hikers):
#   * NYC DEP — Catskill/Delaware watershed, hundreds of individually-mapped
#     "Unit" parcels, a documented Public Access Program; popular Catskill
#     trailheads sit on this land.
#   * Marin Municipal Water District — the Mt Tamalpais watershed is legally
#     open to hikers and one of the most-hiked areas in the Bay Area
#     ('Mount Tamalpais Watershed', 83 trails; #27 audit false positive).
#   * Portland Water District — maintains a public hiking network on the
#     Sebago Lake Land Reserve, ME ('Sebago Lake Land Reserve', 29 trails;
#     #27 audit false positive).
# The flag was calibrated on small municipal reservoir buffers (Town of
# Chester, Village of Warwick — plausibly closed) and strict closed watersheds
# (SF PUC's Alameda, Seattle Public Utilities' Tolt, Providence Water's
# Scituate), which this exemption deliberately does NOT touch.
_KNOWN_PUBLIC_WATER_OPERATOR = re.compile(
    r"new york city department of environmental protection"
    r"|marin municipal water district"
    r"|portland water district",
    re.IGNORECASE)

# A name containing "Trail(s)" is an unambiguous public-hiking signal on its
# own — 'Middletown Reservoir Trails' would otherwise flag purely because its
# OWN title/description said water-supply, despite its name literally saying
# what it is.
_NAME_SAYS_TRAIL = re.compile(r"\btrails?\b", re.IGNORECASE)


def red_flag(tags: dict) -> str | None:
    """A narrow, real-example-backed reason this area is likely private/
    restricted land, or None if it's trusted. See the module comment above
    for why this list stays narrow instead of trying to enumerate every
    legitimate operator."""
    name = tags.get("name") or ""
    operator = tags.get("operator") or ""
    title_desc = " ".join(str(tags.get(k) or "") for k in ("protection_title", "description"))
    flag = next((label for label, pattern in _RED_FLAGS_TITLE_DESC_ONLY.items()
                 if pattern.search(title_desc)), None)
    if flag is None and _HUNTING_FLAG.search(name + " " + title_desc):
        flag = "hunting club/preserve"
    if flag is None:
        return None
    if _GOVERNMENT_OPERATOR.search(operator):
        return None
    if flag == "water supply/watershed" and _KNOWN_PUBLIC_WATER_OPERATOR.search(operator):
        return None
    if _NAME_SAYS_TRAIL.search(name):
        return None
    return flag


def is_quality(tags: dict) -> bool:
    """Whether an OSM relation's tags qualify it as an outdoor area
    we want to surface. protect_class whitelist OR name keyword.

    `ownership=private` is an explicit, unambiguous assertion — distinct
    from a name-based guess — so it's as safe to act on as `access=private`.
    Found via a real example: 'Bucktown LLC Conservation Easement' (NY) is
    private mining-company land tied to an environmental permit, tagged
    boundary=protected_area + a protect_class we already whitelist — it
    would otherwise pass every check we have. protect_class alone doesn't
    distinguish public land from a private easement; explicit ownership
    does.

    red_flag() catches the harder case: areas that DON'T explicitly declare
    ownership at all (the tag is just missing) but show a real-example-backed
    sign of being private/restricted anyway (a mining easement, a hunting
    club, a closed municipal water-supply zone). See red_flag()'s docstring
    for why this is a narrow exclusion list, not an attempt to whitelist
    every legitimate public operator."""
    if tags.get("access") == "private":
        return False
    if tags.get("ownership") == "private":
        return False
    name = (tags.get("name") or "").strip()
    if not name:
        return False
    pc = (tags.get("protect_class") or "").strip().lower()
    has_pc = bool(pc and pc in ALLOWED_PROTECT_CLASSES)
    has_keyword = bool(NAME_KEYWORD_RE.search(name))
    if not (has_pc or has_keyword):
        return False
    return red_flag(tags) is None


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
