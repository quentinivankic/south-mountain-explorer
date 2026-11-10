#!/usr/bin/env python3
"""Canada-only discovery + draft registry-row generator (read-only on its inputs).

Turns the province PBFs into a reproducible candidate list for the Canada registry lane and
draft `trailforge/golden/ca-areas.json` rows. It never edits the registry, never publishes and
never writes next to its input PBFs (the p1 province files are hardlinked into other worktrees).

  python3 scripts/ca_discover.py --pbf-dir <dir with <slug>.osm.pbf> --cache-dir <dir> --out-dir <dir>
                                 [--provinces bc,ab,...] [--registry PATH] [--live-geom-dir PATH]

Method (final/scale-scope.md section 2, made reproducible):
  1. per province: `osmium tags-filter` into --cache-dir (paths/footways/tracks/bridleways/steps,
     protected-area/park polygons, hiking routes, admin_level=4 provinces); cached by input sha256
  2. named ways after the lane's tag filters and scripts/ca_trail_filter.py vocabulary; members of
     rwn/nwn/iwn or long-distance-named routes removed; per named polygon the miles of each trail
     name inside it (>= ca_trail_filter.MIN_TRAIL_MI)
  3. one trail in one area: greedy claim over ALL provinces at once by OSM way id. Live registry
     areas claim first, then tier order, then the larger polygon. A trail whose folded name matches
     a trail of a live Canadian geom (public/areas/geom) with an overlapping extent is live-claimed
  4. per polygon: tier (heuristic, from name/operator/protection_title), pre-flags (mtb, numbered,
     ski, ohv, pluz, size, private, centre-gate, outside-province), floor (>= 2 trails and 2.5 mi, or >= 3 trails) on the raw and the
     clean (mtb/numbered-name-free) trail set
  5. trail-extent bbox of the clean trails; centre = the trail vertex nearest the extent midpoint
     (3 dp), checked against the ca_publish centre gate (CENTRE_GATE_DEG of the midpoint, both
     axes) and the prepare-areas gate (inside the boundary bbox, padded registry bbox margins)
  6. draft rows for NEW (not in the registry) clean passers, every one `hold: discovery-draft`;
     registry + drafts must load through ca_publish.load_registry or the run fails closed

Outputs (no timestamps; same inputs give the same bytes): manifest.json, candidates.json,
candidates.tsv, draft-rows.json, summary.txt.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ca_publish as ca  # noqa: E402
import ca_trail_filter as ctf  # noqa: E402

SCHEMA = "trekdex-ca-discovery-v1"
DRAFT_SCHEMA = "trekdex-ca-areas-draft-v1"
DRAFT_HOLD = "discovery-draft"
LIVE_GEOM_DIR = ca.ROOT / "public" / "areas" / "geom"
FILTER_ARGS = (
    "w/highway=path,footway,track,bridleway,steps",
    "wr/boundary=protected_area,national_park",
    "wr/leisure=nature_reserve,park",
    "r/route=hiking,foot,walking",
    "r/admin_level=4",
)
HW = {"path", "footway", "track", "bridleway", "steps"}
FLOOR_MI, FLOOR_FEW = 2.5, 3          # >= 2 trails and >= 2.5 mi, or >= 3 trails
MTB_SHARE = 0.5                       # a trail is mtb when >= this share of its miles is mtb-tagged
FLAG_SHARE = 0.5                      # polygon ski/ohv flag: dropped ski/ohv miles share
LIVE_PAD_DEG = 0.01                   # live-geom name dedupe: extents must overlap within this pad
TIER_TO_REGISTRY = {"1-federal": 1, "2-provincial": 2, "3-regional": 3}  # others -> 3 (registry max)
MTB_NAME = re.compile(r"(?i)\b(enduro|jump|jumps|flow|descent|berm|berms|shred|gnar|huck|downhill|dh|xc"
                      r"|singletrack|pump|freeride|drop|drops|skills)\b")
NUMBERED = re.compile(r"[A-Z]?\d+[A-Z]?|[#\d &\-]+|[A-Z]{1,3}\d{2,4}|(?i:(piste|trail|sentier|loop|"
                      r"boucle|segment|section)\s*#?\d+[a-z]?)")
POLY_OHV = re.compile(r"(?i)\b(ohv|atv|off[- ]highway|off[- ]road|snow vehicle|motori[sz]ed|motocross)\b")
POLY_SKI = re.compile(r"(?i)\b(ski|nordic|snowshoe|snowmobile|motoneige|ski de fond)\b")
POLY_CONTAINER = re.compile(r"(?i)\b(public land use zone|pluz)\b")
ISO_PROV = {f"CA-{c.upper()}": c for c in ca.PROVINCES}
LETTERS = {"ß": "ss", "æ": "ae", "Æ": "ae", "œ": "oe", "Œ": "oe", "ø": "o", "Ø": "o", "ł": "l",
           "Ł": "l", "đ": "d", "Đ": "d", "þ": "th", "Þ": "th"}


class DiscoverError(Exception):
    pass


# ------------------------------------------------------------------ io safety --

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def check_output_dirs(inputs: list[Path], dirs: list[Path]) -> None:
    """Refuse an output/cache dir equal to or inside the directory of any input PBF."""
    for d in dirs:
        rd = d.resolve()
        for src in inputs:
            if _inside(rd, src.resolve().parent):
                raise DiscoverError(f"refusing to write into {rd}: it is inside the input directory "
                                    f"{src.resolve().parent} (input PBFs are read-only)")


def guard_target(path: Path, inputs: list[Path]) -> None:
    """Refuse to replace an input file or any existing hardlinked file."""
    if path.exists():
        st = path.stat()
        if st.st_nlink > 1:
            raise DiscoverError(f"refusing to overwrite hardlinked file {path} (nlink {st.st_nlink})")
        for src in inputs:
            if src.exists() and os.path.samefile(path, src):
                raise DiscoverError(f"refusing to overwrite input {src}")


def safe_write(path: Path, data: bytes, inputs: list[Path]) -> None:
    """Write via temp + rename; never onto an input file or an existing hardlinked file."""
    guard_target(path, inputs)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def _dump(document: Any) -> bytes:
    return (json.dumps(document, indent=1, ensure_ascii=False, sort_keys=False) + "\n").encode("utf-8")


def province_pbf(pbf_dir: Path, prov: str) -> Path:
    slug = ca.PROVINCES[prov]
    for name in (f"{slug}.osm.pbf", f"{slug}-latest.osm.pbf", f"{slug}.osm"):
        if (pbf_dir / name).is_file():
            return pbf_dir / name
    raise DiscoverError(f"no input PBF for {prov} in {pbf_dir} (expected {slug}.osm.pbf)")


def prefilter(src: Path, src_sha: str, prov: str, cache_dir: Path, inputs: list[Path],
              osmium_bin: str) -> Path:
    key = hashlib.sha256((src_sha + "\n" + "\n".join(FILTER_ARGS)).encode()).hexdigest()[:16]
    out = cache_dir / f"{prov}-{key}.filtered.osm.pbf"
    if out.is_file():
        return out
    cache_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=cache_dir, prefix=f".{prov}-", suffix=".osm.pbf")
    os.close(fd)
    try:
        subprocess.run([osmium_bin, "tags-filter", str(src), *FILTER_ARGS, "-o", tmp, "--overwrite",
                        "--no-progress"], check=True, capture_output=True)
        guard_target(out, inputs)
        os.replace(tmp, out)
    except subprocess.CalledProcessError as error:
        raise DiscoverError(f"osmium tags-filter failed for {src}: {error.stderr.decode()[-400:]}") from None
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return out


# ------------------------------------------------------------------ OSM read --

def drop_reason(t: dict[str, str]) -> str | None:
    """The scale-scope way filter. 'ski'/'ohv' drops feed the polygon pre-flags."""
    if t.get("access") in ("no", "private") or t.get("foot") in ("no", "private"):
        return "access"
    if "piste:type" in t:
        return "ski"
    if t.get("informal") == "yes":
        return "informal"
    if t.get("footway") in ("sidewalk", "crossing") or t.get("highway") == "steps":
        return "sidewalk"
    for k in ("motor_vehicle", "atv", "snowmobile", "motorcycle"):
        if t.get(k) in ("yes", "designated") and t.get("highway") == "track":
            return "ohv"
    if t.get("bicycle") == "designated" and t.get("foot") != "designated" and (
            "mtb:scale" in t or (t.get("highway") == "path"
                                 and t.get("surface") in ("asphalt", "paved", "concrete"))):
        return "bike"
    return None


def name_drop(name: str) -> str | None:
    if re.match(r"(?i)^(unnamed|sans nom)", name):
        return "unnamed"
    hit = ctf.vocabulary_category(name, "")
    if not hit:
        return None
    return {"ca-winter-only": "ski", "ca-motorised": "ohv"}.get(hit[0], hit[0])


def tier_of(t: dict[str, str]) -> str:
    """Heuristic tier from name, operator/owner and protection_title (scale-scope section 2)."""
    n = ctf.fold(t.get("name", ""))
    op = ctf.fold(t.get("operator", "") + " " + t.get("owner", ""))
    s = f"{n} | {op} | {ctf.fold(t.get('protection_title', ''))}"
    if re.search(r"parks canada|parcs canada|national park|parc national du canada|national historic|lieu "
                 r"historique national|national wildlife area|migratory bird|refuge d'oiseaux migrateurs|"
                 r"national marine|reserve de parc national", s):
        return "1-federal"
    if re.search(r"sepaq|parc national|reserve faunique|provincial park|territorial park|parc provincial|"
                 r"wildland|ecological reserve|reserve ecologique|wilderness area|wilderness park|"
                 r"conservation reserve|provincial recreation|recreation area|protected natural area|"
                 r"nature reserve|wilderness reserve|protected area|reserve de biodiversite|biodiversity|"
                 r"provincial|heritage rangeland|natural area|environmental reserve|ecological|"
                 r"habitat protection", s):
        if re.search(r"regional|parc regional", s):
            return "3-regional"
        if re.search(r"conservation area|conservation authority|conservation halton", s):
            return "4-conservation-auth"
        return "2-provincial"
    if re.search(r"regional park|parc regional|regional district|metro vancouver|capital regional|crd parks|"
                 r"regional parks", s):
        return "3-regional"
    if re.search(r"conservation area|conservation authority|conservation halton|region conservation|"
                 r"niagara escarpment|credit valley|grand river|aire de conservation", s):
        return "4-conservation-auth"
    if re.search(r"recreation site|rec site|recreation sites and trails|forest recreation|crown|"
                 r"demonstration forest|community forest|trail network|trail system|sentiers", s):
        return "5-crown-rec"
    if re.search(r"nature conservancy|conservation de la nature|land trust|nature trust|naturalist|"
                 r"ducks unlimited|conservancy|fiducie", s):
        return "6-land-trust"
    if re.search(r"city|town|municipal|ville|municipalite|village|county|district of|township|park board",
                 s) or t.get("leisure") == "park":
        return "7-municipal"
    return "8-other"


POLY_KEYS = ("name", "type", "boundary", "leisure", "operator", "owner", "protect_class",
             "protection_title", "access", "ownership", "designation")


def read_province(path: Path) -> dict[str, Any]:
    """Ways (kept + ski/ohv drops), named polygons, hiking routes and admin_level=4 provinces."""
    import osmium
    wkbf = osmium.geom.WKBFactory()

    class Handler(osmium.SimpleHandler):
        def __init__(self) -> None:
            super().__init__()
            self.ways: list[tuple] = []
            self.areas: list[tuple] = []
            self.routes: list[tuple] = []
            self.provinces: list[tuple] = []
            self.rel_types: dict[int, str] = {}

        def way(self, w: Any) -> None:
            t = w.tags
            if t.get("highway") not in HW or not t.get("name"):
                return
            try:
                g = wkbf.create_linestring(w)
            except Exception:  # noqa: BLE001 - broken way geometry (missing nodes): skip like the estimator
                return
            self.ways.append((w.id, unicodedata.normalize("NFC", t.get("name")), dict(t), g))

        def area(self, a: Any) -> None:
            t = a.tags
            if t.get("boundary") == "administrative" and t.get("admin_level") == "4" \
                    and t.get("ISO3166-2") in ISO_PROV and not a.from_way():
                try:
                    self.provinces.append((ISO_PROV[t.get("ISO3166-2")], wkbf.create_multipolygon(a)))
                except Exception:  # noqa: BLE001
                    pass
                return
            if not t.get("name"):
                return
            if not (t.get("boundary") in ("protected_area", "national_park")
                    or t.get("leisure") in ("nature_reserve", "park")):
                return
            try:
                g = wkbf.create_multipolygon(a)
            except Exception:  # noqa: BLE001
                return
            keep = {k: unicodedata.normalize("NFC", t.get(k)) for k in POLY_KEYS if t.get(k)}
            self.areas.append(("w" if a.from_way() else "r", a.orig_id(), keep, g))

        def relation(self, r: Any) -> None:
            t = r.tags
            if t.get("type"):  # libosmium drops `type` from area tags; the prepare gate checks it
                self.rel_types[r.id] = t.get("type")
            if t.get("route") in ("hiking", "foot", "walking"):
                self.routes.append((r.id, t.get("name") or "", t.get("network") or "",
                                    [m.ref for m in r.members if m.type == "w"]))

    h = Handler()
    h.apply_file(str(path), locations=True, idx="flex_mem")
    for kind, oid, keep, _g in h.areas:
        if kind == "r" and oid in h.rel_types:
            keep["type"] = h.rel_types[oid]
    return {"ways": h.ways, "areas": h.areas, "routes": h.routes, "provinces": h.provinces}


def miles(g: Any) -> float:
    """Length in miles (local equirectangular at the geometry's centroid latitude)."""
    from shapely.ops import transform
    if g.is_empty:
        return 0.0
    kx = 69.172 * math.cos(math.radians(g.centroid.y))
    return transform(lambda x, y, z=None: (x * kx, y * 69.0), g).length


def _lines(g: Any) -> list[list[tuple[float, float]]]:
    """Line parts of a clipped geometry as [(lat, lon), ...] lists."""
    if g.is_empty:
        return []
    if g.geom_type == "LineString":
        return [[(round(y, 6), round(x, 6)) for x, y in g.coords]]
    if hasattr(g, "geoms"):
        return [p for part in g.geoms for p in _lines(part)]
    return []


def measure_province(prov: str, data: dict[str, Any], reg_index: dict[tuple[str, int], dict]) -> list[dict]:
    """Per named polygon: miles of each kept trail name inside it, plus the pre-flag inputs."""
    import shapely.wkb as wkblib
    from shapely.strtree import STRtree
    ld_ways: set[int] = set()
    for _rid, rname, net, members in data["routes"]:
        if net in ctf.LONG_DISTANCE_NETWORKS or ctf.LONG_DISTANCE.search(ctf.fold(rname)):
            ld_ways.update(members)
    kept, dropped = [], []
    for wid, name, t, wk in data["ways"]:
        why = drop_reason(t) or name_drop(name)
        g = wkblib.loads(wk, hex=True)
        if why in ("ski", "ohv"):
            dropped.append((why, g))
        if why or wid in ld_ways:
            continue
        mtb = any(k.startswith("mtb") for k in t) or t.get("bicycle") == "designated"
        kept.append((wid, name, g, mtb))
    tree = STRtree([w[2] for w in kept]) if kept else None
    dtree = STRtree([d[1] for d in dropped]) if dropped else None
    out = []
    for kind, oid, tags, wk in data["areas"]:
        g = wkblib.loads(wk, hex=True)
        if not g.is_valid:
            g = g.buffer(0)
        reg = reg_index.get((kind, oid))
        per: dict[str, dict[str, Any]] = {}
        for i in (tree.query(g) if tree is not None else []):
            wid, name, wg, mtb = kept[int(i)]
            inter = wg.intersection(g)
            length = miles(inter)
            if length <= 0:
                continue
            rec = per.setdefault(name, {"mi": 0.0, "mtb_mi": 0.0, "wids": [], "lines": []})
            rec["mi"] += length
            rec["mtb_mi"] += length if mtb else 0.0
            rec["wids"].append(wid)
            rec["lines"] += _lines(inter)
        if not per and reg is None:
            continue
        flag_mi = {"ski": 0.0, "ohv": 0.0}
        for i in (dtree.query(g) if dtree is not None else []):
            why, dg = dropped[int(i)]
            flag_mi[why] += miles(dg.intersection(g))
        minx, miny, maxx, maxy = g.bounds
        out.append({"prov": prov, "kind": kind, "osm_id": oid, "tags": tags, "tier": tier_of(tags),
                    "boundary_bbox": [minx, miny, maxx, maxy], "per": per,
                    "ski_mi": flag_mi["ski"], "ohv_mi": flag_mi["ohv"], "reg": reg})
    return out


def province_of_point(lat: float, lon: float, provinces: dict[str, list[Any]]) -> str | None:
    from shapely.geometry import Point
    pt = Point(lon, lat)
    for code in sorted(provinces):
        if any(poly.covers(pt) for poly in provinces[code]):
            return code
    return None


# ------------------------------------------------------------- claim + gates --

def floor_gate(miles_by_name: dict[str, float]) -> tuple[bool, int, float]:
    q = [v for v in miles_by_name.values() if v >= ctf.MIN_TRAIL_MI]
    total = round(sum(q), 1)
    return len(q) >= 2 and (sum(q) >= FLOOR_MI or len(q) >= FLOOR_FEW), len(q), total


def slugify(name: str) -> str:
    text = "".join(LETTERS.get(c, c) for c in name)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower().replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def live_geom_index(registry: dict[str, Any], geom_dir: Path) -> tuple[dict[str, list], list[str]]:
    """fold(name) -> [(area id, [lat0, lon0, lat1, lon1])] over the live Canadian geoms."""
    index: dict[str, list] = collections.defaultdict(list)
    missing = []
    for entry in sorted(registry["areas"], key=lambda e: e["id"]):
        if entry.get("hold") is not None:
            continue
        path = geom_dir / f"{entry['id']}.json"
        if not path.is_file():
            missing.append(entry["id"])
            continue
        geom = json.loads(path.read_text(encoding="utf-8"))
        for trail in geom.get("trails") or []:
            pts = [p for seg in trail.get("segments") or [] for p in seg]
            if pts:
                index[ctf.fold(trail.get("name"))].append((entry["id"], [
                    min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)]))
    return index, missing


def _extent(lines: list[list[tuple[float, float]]]) -> list[float]:
    pts = [p for line in lines for p in line]
    return [min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)]


def _overlaps(a: list[float], b: list[float], pad: float) -> bool:
    return a[0] - pad <= b[2] and b[0] - pad <= a[2] and a[1] - pad <= b[3] and b[1] - pad <= a[3]


def _size_ok(box: list[float]) -> bool:
    w, h = box[2] - box[0], box[3] - box[1]
    return ca.REL_SPAN_MIN <= w <= ca.REL_SPAN_MAX and ca.REL_SPAN_MIN <= h <= ca.REL_SPAN_MAX


def place_centre(lines: list[list[tuple[float, float]]], boundary: list[float]) -> dict[str, Any]:
    """Pin = the trail vertex nearest the trail-extent midpoint (3 dp); fallback = the midpoint
    clamped into the boundary bbox. Kept only if it passes the ca_publish centre gate (within
    CENTRE_GATE_DEG of the extent midpoint on both axes) and sits inside the boundary bbox."""
    ext = _extent(lines)
    mid = ((ext[0] + ext[2]) / 2, (ext[1] + ext[3]) / 2)
    pts = [p for line in lines for p in line]
    near = min(pts, key=lambda p: (max(abs(p[0] - mid[0]), abs(p[1] - mid[1])), p))
    clamp = (min(max(mid[0], boundary[1]), boundary[3]), min(max(mid[1], boundary[0]), boundary[2]))
    for how, cand in (("trail-vertex", near), ("extent-midpoint", clamp)):
        c = [round(cand[0], 3), round(cand[1], 3)]
        off = max(abs(mid[0] - c[0]), abs(mid[1] - c[1]))
        inside = boundary[1] <= c[0] <= boundary[3] and boundary[0] <= c[1] <= boundary[2]
        if off <= ca.CENTRE_GATE_DEG and inside:
            nearest = min(max(abs(p[0] - c[0]), abs(p[1] - c[1])) for p in pts)
            return {"center": c, "how": how, "gate_offset_deg": round(off, 4),
                    "nearest_trail_deg": round(nearest, 4), "trail_bbox": [round(v, 5) for v in (
                        ext[1], ext[0], ext[3], ext[2])]}
    return {"center": None, "how": "none", "trail_bbox": [round(v, 5) for v in (ext[1], ext[0], ext[3], ext[2])]}


def claim_all(polys: list[dict], live_names: dict[str, list]) -> None:
    """One trail in one area, greedy over every province: live first, then tier, then larger span."""
    def span(p: dict) -> float:
        b = p["boundary_bbox"]
        return max(b[2] - b[0], b[3] - b[1])

    def is_live(p: dict) -> bool:
        return p["reg"] is not None and p["reg"].get("hold") is None

    order = sorted(polys, key=lambda p: (not is_live(p), p["tier"], -round(span(p), 6), p["kind"], p["osm_id"]))
    owner: dict[int, str] = {}
    for p in order:
        p["live"] = is_live(p)
        tags = p["tags"]
        p["private"] = tags.get("access") in ("private", "no") or tags.get("ownership") == "private"
        trails = []
        for name in sorted(p["per"]):
            rec = p["per"][name]
            if rec["mi"] < ctf.MIN_TRAIL_MI:
                continue
            by = next((owner[w] for w in rec["wids"] if w in owner), None)
            if by is None and not p["live"]:
                ext = _extent(rec["lines"])
                by = next((f"live-geom:{aid}" for aid, box in live_names.get(ctf.fold(name), [])
                           if _overlaps(ext, box, LIVE_PAD_DEG)), None)
            flags = []
            if rec["mtb_mi"] >= MTB_SHARE * rec["mi"] or MTB_NAME.search(name):
                flags.append("mtb")
            if NUMBERED.fullmatch(name.strip()):
                flags.append("numbered")
            trails.append({"name": name, "mi": round(rec["mi"], 2), "flags": flags, "claimed_by": by})
        free = [t for t in trails if t["claimed_by"] is None] if not p["private"] else []
        p["trails"] = trails
        p["raw"] = floor_gate({t["name"]: t["mi"] for t in free})
        p["clean_names"] = [t["name"] for t in free if not t["flags"]]
        p["clean"] = floor_gate({t["name"]: t["mi"] for t in free if not t["flags"]})
        free_mi = sum(t["mi"] for t in free)
        p["mtb_share"] = round(sum(t["mi"] for t in free if "mtb" in t["flags"]) / free_mi, 2) if free_mi else 0.0
        p["free_mi"] = round(free_mi, 2)
        if p["live"] or p["raw"][0]:
            label = p["reg"]["id"] if p["reg"] else f"{p['kind']}{p['osm_id']}"
            for t in free:
                for w in p["per"][t["name"]]["wids"]:
                    owner.setdefault(w, label)


def poly_flags(p: dict, provinces: dict[str, list]) -> None:
    tags, b = p["tags"], p["boundary_bbox"]
    flags = []
    if p["mtb_share"] >= FLAG_SHARE:
        flags.append("mtb")
    if any("numbered" in t["flags"] for t in p["trails"] if t["claimed_by"] is None):
        flags.append("numbered")
    text = " ".join(tags.get(k, "") for k in ("name", "designation", "protection_title"))
    for key, rx in (("ski", POLY_SKI), ("ohv", POLY_OHV)):
        dropped = p[f"{key}_mi"]
        if rx.search(ctf.fold(text)) or (dropped > 0 and dropped >= FLAG_SHARE * (dropped + p["free_mi"])):
            flags.append(key)
    if POLY_CONTAINER.search(ctf.fold(text)):
        flags.append("pluz")  # patchwork land-use zone: valley split is Phase D (region containers)
    if not _size_ok(b):
        flags.append("size")
    if p["private"]:
        flags.append("private")
    lines = [ln for name in p["clean_names"] for ln in p["per"][name]["lines"]]
    p["pin"] = place_centre(lines, b) if lines else {"center": None, "how": "none", "trail_bbox": None}
    if lines and p["pin"]["center"] is None:
        flags.append("centre-gate")
    lat, lon = p["pin"]["center"] or ((b[1] + b[3]) / 2, (b[0] + b[2]) / 2)
    found = province_of_point(lat, lon, provinces)
    if found is None:
        flags.append("province-unverified" if p["prov"] not in provinces else "outside-province")
    elif found != p["prov"]:
        if p["reg"] is not None:  # the registry owns a listed area's province; just say so
            p["prov"] = p["reg"]["province"]
            if found != p["prov"]:
                flags.append(f"centre-in-{found}")
        else:
            p["found_in"], p["prov"] = p["prov"], found
    p["flags"] = flags


def status_of(p: dict) -> str:
    if p["reg"] is None:
        return "new"
    return "live" if p["reg"].get("hold") is None else f"registry-held:{p['reg']['hold']}"


def draft_row(p: dict, area_id: str, inputs_sha: dict[str, str]) -> dict[str, Any]:
    pin = p["pin"]
    ident = {"relation_id": p["osm_id"]} if p["kind"] == "r" else {"way_id": p["osm_id"]}
    tier = TIER_TO_REGISTRY.get(p["tier"], 3)
    flags = ", ".join(p["flags"]) or "none"
    src = p.get("found_in", p["prov"])
    notes = (f"DRAFT from scripts/ca_discover.py ({src} input sha256 {inputs_sha[src][:12]}); "
             f"unreviewed, held until per-area curation (official list, MTB/stub/whole-route check). "
             f"Discovery tier {p['tier']}; estimator {p['clean'][1]} clean trails / {p['clean'][2]} mi "
             f"({p['raw'][1]} raw / {p['raw'][2]} mi); pre-flags: {flags}; mtb share {p['mtb_share']}. "
             f"Centre = {pin['how']} {pin['gate_offset_deg']} deg from the trail-extent midpoint "
             f"(gate {ca.CENTRE_GATE_DEG}); trail bbox {pin['trail_bbox']}.")
    return {"id": area_id, "province": p["prov"], "state": ca.STATE, **ident, "name": p["tags"]["name"],
            "bbox": ca.pad_bbox(p["boundary_bbox"]), "center": pin["center"], "tier": tier,
            "hold": DRAFT_HOLD, "est_trails": p["clean"][1], "est_mi": p["clean"][2], "notes": notes}


def prepare_problems(p: dict, row: dict) -> list[str]:
    """The real ca_publish prepare-areas gate (publish mode) on the draft row and its boundary."""
    tags = dict(p["tags"])
    info = {"tags": tags, "count": 1, "bbox": p["boundary_bbox"], "closed": True}
    rec = ca.check_area_geometry(row, p["boundary_bbox"], "publish", info)
    return rec["problems"]


def propose_ids(polys: list[dict], taken: set[str]) -> None:
    for p in sorted(polys, key=lambda q: (q["prov"], slugify(q["tags"]["name"]), q["kind"], q["osm_id"])):
        if p["reg"] is not None:
            p["area_id"] = p["reg"]["id"]
            continue
        base = slugify(p["tags"]["name"]) or f"{p['kind']}{p['osm_id']}"
        aid = f"{base}-ca-{p['prov']}"
        if aid in taken or not ca.SLUG.fullmatch(aid):
            aid = f"{base}-{p['kind']}{p['osm_id']}-ca-{p['prov']}".lstrip("-")
            p.setdefault("id_notes", []).append("id-collision")
        taken.add(aid)
        p["area_id"] = aid


def validate_rows(registry_path: Path, rows: list[dict], out_dir: Path, inputs: list[Path]) -> None:
    """Registry + drafts must pass ca_publish.load_registry (fail closed)."""
    doc = json.loads(registry_path.read_text(encoding="utf-8"))
    doc["areas"] = doc["areas"] + rows
    fd, tmp = tempfile.mkstemp(dir=out_dir, prefix=".registry-check.", suffix=".json")
    os.close(fd)
    try:
        Path(tmp).write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        loaded = ca.load_registry(Path(tmp))
    except ca.CaError as error:
        raise DiscoverError(f"draft rows do not load with the registry: {error}") from None
    finally:
        os.unlink(tmp)
    for row in rows:
        if loaded["by_id"][row["id"]]["hold"] != DRAFT_HOLD:
            raise DiscoverError(f"{row['id']}: draft row is not held")


# ------------------------------------------------------------------- outputs --

def _r(box: list[float] | None, nd: int = 5) -> list[float] | None:
    return None if box is None else [round(v, nd) for v in box]


def candidate_record(p: dict) -> dict[str, Any]:
    tags = p["tags"]
    return {"area_id": p["area_id"], "status": status_of(p), "province": p["prov"],
            **({"found_in_extract": p["found_in"]} if "found_in" in p else {}),
            "osm": f"{p['kind']}{p['osm_id']}", "name": tags["name"], "tier": p["tier"],
            "operator": tags.get("operator"), "protection_title": tags.get("protection_title"),
            "boundary_bbox": _r(p["boundary_bbox"]),
            "raw": {"pass": p["raw"][0], "trails": p["raw"][1], "mi": p["raw"][2]},
            "clean": {"pass": p["clean"][0], "trails": p["clean"][1], "mi": p["clean"][2]},
            "mtb_share": p["mtb_share"], "ski_dropped_mi": round(p["ski_mi"], 2),
            "ohv_dropped_mi": round(p["ohv_mi"], 2), "flags": p["flags"], "pin": p["pin"],
            "draft": p["draft"], **({"id_notes": p["id_notes"]} if p.get("id_notes") else {}),
            "trails": p["trails"]}


def summary_text(cands: list[dict], rows: list[dict], n_named: int) -> str:
    tiers = sorted({c["tier"] for c in cands})
    provs = sorted(ca.PROVINCES)
    lines = [f"named polygons measured {n_named}; candidates (>=1 trail or in registry) {len(cands)}; "
             f"draft rows {len(rows)}", "",
             "tier | live | registry-held (clean pass) | >=1 free trail | new raw pass | new clean pass | rows"]
    for t in tiers:
        cs = [c for c in cands if c["tier"] == t]
        held = [c for c in cs if c["status"].startswith("registry-held")]
        new = [c for c in cs if c["status"] == "new"]
        free = [c for c in cs if any(tr["claimed_by"] is None for tr in c["trails"])]
        lines.append(f"{t} | {sum(c['status'] == 'live' for c in cs)} | {len(held)} "
                     f"({sum(c['clean']['pass'] for c in held)}) | {len(free)} | "
                     f"{sum(c['raw']['pass'] for c in new)} | {sum(c['clean']['pass'] for c in new)} | "
                     f"{sum(c['draft'] == 'row' for c in cs)}")
    lines += ["", "draft rows by province: " + ", ".join(
        f"{p} {n}" for p in provs if (n := sum(r['province'] == p for r in rows)))]
    flags = collections.Counter(f for c in cands if c["draft"] == "row" for f in c["flags"])
    lines.append("pre-flags on draft rows: " + (", ".join(f"{k} {v}" for k, v in sorted(flags.items())) or "none"))
    skipped = collections.Counter(c["draft"].split(":")[0] for c in cands
                                  if c["status"] == "new" and c["clean"]["pass"] and c["draft"] != "row")
    lines.append("new clean passers without a row: " + (", ".join(
        f"{k} {v}" for k, v in sorted(skipped.items())) or "none"))
    return "\n".join(lines) + "\n"


def tsv_text(cands: list[dict]) -> str:
    head = ["province", "status", "tier", "area_id", "osm", "name", "clean_trails", "clean_mi", "raw_trails",
            "raw_mi", "mtb_share", "flags", "center", "draft"]
    out = ["\t".join(head)]
    for c in cands:
        centre = c["pin"]["center"]
        out.append("\t".join(str(v) for v in (
            c["province"], c["status"], c["tier"], c["area_id"], c["osm"], c["name"].replace("\t", " "),
            c["clean"]["trails"], c["clean"]["mi"], c["raw"]["trails"], c["raw"]["mi"], c["mtb_share"],
            ",".join(c["flags"]), "" if centre is None else f"{centre[0]},{centre[1]}", c["draft"])))
    return "\n".join(out) + "\n"


def _tool_versions(osmium_bin: str) -> dict[str, str]:
    import osmium.version
    import shapely
    cli = subprocess.run([osmium_bin, "--version"], capture_output=True, text=True).stdout.splitlines()
    return {"osmium_cli": cli[0] if cli else "", "pyosmium": osmium.version.pyosmium_release,
            "shapely": shapely.__version__}


def run(args: argparse.Namespace) -> dict[str, Any]:
    import shapely.wkb as wkblib
    provs = [p.strip() for p in args.provinces.split(",") if p.strip()]
    unknown = [p for p in provs if p not in ca.PROVINCES]
    if unknown or not provs or len(set(provs)) != len(provs):
        raise DiscoverError(f"--provinces must be distinct codes from {sorted(ca.PROVINCES)}: {args.provinces!r}")
    osmium_bin = args.osmium or shutil.which("osmium")
    if not osmium_bin:
        raise DiscoverError("osmium CLI not found (pass --osmium)")
    pbfs = {p: province_pbf(args.pbf_dir, p) for p in sorted(provs)}
    inputs = list(pbfs.values())
    check_output_dirs(inputs, [args.cache_dir, args.out_dir])
    args.out_dir.mkdir(parents=True, exist_ok=True)
    registry = ca.load_registry(args.registry)
    reg_index = {(("w", e["way_id"]) if "way_id" in e else ("r", e["relation_id"])): e for e in registry["areas"]}
    shas, cache_files, polys, named = {}, {}, [], set()
    provinces: dict[str, list] = collections.defaultdict(list)
    for prov, src in pbfs.items():
        shas[prov] = sha256_file(src)
        filtered = prefilter(src, shas[prov], prov, args.cache_dir, inputs, osmium_bin)
        cache_files[prov] = filtered.name
        data = read_province(filtered)
        named.update((k, i) for k, i, _t, _g in data["areas"])
        for code, wk in data["provinces"]:
            provinces[code].append(wkblib.loads(wk, hex=True))
        polys += measure_province(prov, data, reg_index)
        print(f"{prov}: {len(data['ways'])} named ways, {len(data['areas'])} polygons", file=sys.stderr)
    best: dict[tuple, dict] = {}
    for p in polys:  # cross-province duplicates: keep the extract with the most trail miles
        key = (p["kind"], p["osm_id"])
        mi = sum(r["mi"] for r in p["per"].values())
        if key not in best or mi > best[key][0]:
            best[key] = (mi, p)
    polys = [best[k][1] for k in sorted(best)]
    live_names, live_missing = live_geom_index(registry, args.live_geom_dir)
    claim_all(polys, live_names)
    for p in polys:
        poly_flags(p, provinces)
    propose_ids(polys, set(registry["by_id"]))
    rows = []
    for p in sorted(polys, key=lambda q: (q["prov"], q["area_id"])):
        p["draft"] = "-"
        if status_of(p) != "new" or not p["clean"][0]:
            continue
        blocking = [f for f in p["flags"] if f in ("size", "private", "centre-gate", "outside-province")]
        if blocking:
            p["draft"] = "skipped:" + ",".join(blocking)
            continue
        row = draft_row(p, p["area_id"], shas)
        problems = prepare_problems(p, row)
        if problems:
            p["draft"], p["prepare_problems"] = "skipped:prepare-gate", problems
            continue
        p["draft"] = "row"
        rows.append(row)
    validate_rows(args.registry, rows, args.out_dir, inputs)
    cands = []
    for p in sorted(polys, key=lambda q: (q["prov"], q["area_id"])):
        if p["trails"] or p["reg"] is not None:
            cands.append(candidate_record(p))
            if p.get("prepare_problems"):
                cands[-1]["prepare_problems"] = p["prepare_problems"]
    outputs = {
        "candidates.json": _dump({"schema": SCHEMA, "candidates": cands}),
        "draft-rows.json": _dump({"schema": DRAFT_SCHEMA, "note": (
            "Draft ca-areas.json rows, every one hold=discovery-draft. Copy a row into the registry "
            "only after per-area curation; clearing hold is the ship decision."), "areas": rows}),
        "candidates.tsv": tsv_text(cands).encode("utf-8"),
        "summary.txt": summary_text(cands, rows, len(named)).encode("utf-8"),
    }
    here = Path(__file__).resolve().parent
    manifest = {
        "schema": SCHEMA + "-manifest",
        "inputs": [{"province": p, "file": pbfs[p].name, "bytes": pbfs[p].stat().st_size, "sha256": shas[p],
                    "filtered_cache": cache_files[p]} for p in sorted(pbfs)],
        "registry_sha256": sha256_file(args.registry),
        "live_geoms_missing": live_missing,
        "code_sha256": {n: sha256_file(here / n) for n in ("ca_discover.py", "ca_trail_filter.py", "ca_publish.py")},
        "tools": _tool_versions(osmium_bin),
        "params": {"filter": list(FILTER_ARGS), "min_trail_mi": ctf.MIN_TRAIL_MI, "floor_mi": FLOOR_MI,
                   "floor_few": FLOOR_FEW, "mtb_share": MTB_SHARE, "flag_share": FLAG_SHARE,
                   "centre_gate_deg": ca.CENTRE_GATE_DEG, "span_deg": [ca.REL_SPAN_MIN, ca.REL_SPAN_MAX],
                   "live_pad_deg": LIVE_PAD_DEG},
        "outputs": {name: hashlib.sha256(data).hexdigest() for name, data in sorted(outputs.items())},
    }
    outputs["manifest.json"] = _dump(manifest)
    for name, data in outputs.items():
        safe_write(args.out_dir / name, data, inputs)
    return {"candidates": len(cands), "rows": len(rows)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--pbf-dir", type=Path, required=True, help="dir holding <geofabrik-slug>.osm.pbf (read only)")
    ap.add_argument("--cache-dir", type=Path, required=True, help="filtered-extract cache (outside --pbf-dir)")
    ap.add_argument("--out-dir", type=Path, required=True, help="output dir (outside --pbf-dir)")
    ap.add_argument("--provinces", default=",".join(sorted(ca.PROVINCES)))
    ap.add_argument("--registry", type=Path, default=ca.REGISTRY_PATH)
    ap.add_argument("--live-geom-dir", type=Path, default=LIVE_GEOM_DIR)
    ap.add_argument("--osmium", default=None, help="osmium CLI path (default: on PATH)")
    args = ap.parse_args(argv)
    try:
        result = run(args)
    except (DiscoverError, ca.CaError) as error:
        print(f"ca_discover: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
