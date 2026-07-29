#!/usr/bin/env python3
"""Build the LOCAL answers to everything add-parking.py currently asks Overpass.

WHY. Measured 2026-07-29, one state at a time: Overpass is 87% of Vermont's
parking run and 71% of Colorado's, and the road gate alone is 34.7 s of 43.1 s
(VT) and 175.6 s of 212.7 s (CO). `trailforge-parking.yml` then caps itself at 8
parallel jobs purely to avoid stampeding Overpass, which is where the ~4-hour
national estimate comes from. None of that is compute — the homelab already holds
the data these queries are asking for, and has all along.

WHAT IT BUILDS, under `$TREKDEX_OSM_DIR/cache/`:

  parking.jsonl        every amenity=parking + highway=trailhead feature in the
                       US, as {lat, lon, type, tags}. Replaces the per-state
                       `area["ISO3166-2"]` query. From us-access.osm.pbf.
  boundaries.geojsonseq   area boundary polygons for every osm_relation_id in
                       shipped geom. Replaces the batched `out geom` boundary
                       query. From us-latest.osm.pbf.
  road-gate.json       the road gate's verdict for every federal candidate
                       point nationally. Replaces the chunked `around:250`
                       queries — the single most expensive call in the roll.
                       From us-access.osm.pbf + one ArcGIS fetch.

The first two are pure OSM. `road-gate.json` needs ONE ArcGIS call (~17 s) to
learn which points to judge; ArcGIS has been reliable where Overpass has not, and
there is no local copy of the Forest Service's own inventory.

    python3 scripts/build-local-osm-cache.py --only parking
    python3 scripts/build-local-osm-cache.py            # all three

Rebuild after `fetch-us-extract.sh --update`. Each artifact is written to a
temporary file and renamed, so an interrupted build never leaves a half-file that
a roll would read as authoritative.
"""
from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
OSM_DIR = Path(os.environ.get("TREKDEX_OSM_DIR", "/mnt/raid/trekdex/osm"))
CACHE_DIR = OSM_DIR / "cache"
ACCESS = OSM_DIR / "us-access.osm.pbf"
LATEST = OSM_DIR / "us-latest.osm.pbf"
GEOM = _ROOT / "public" / "areas" / "geom"

# The whole point of the cache is that a stale one is worse than none: it would
# answer confidently from last month's OSM. Every artifact records the source
# extract's replication timestamp so a reader can refuse a mismatch.
CACHE_VERSION = 1

_spec = importlib.util.spec_from_file_location(
    "add_parking", Path(__file__).resolve().parent / "add-parking.py")
_ap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ap)

US_BBOX = [-179.5, 17.5, -64.0, 72.0]     # includes AK and HI
CELL = 0.0025                              # ~278 m lat; the road gate is 250 m


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def run(cmd: list[str]) -> None:
    """Run an osmium command and SHOW ITS ERROR if it fails.

    `subprocess.run(check=True)` raises a CalledProcessError that prints the
    argv and nothing else, so a failed build reported only "returned non-zero
    exit status 1" while osmium's actual complaint went unread. The message is
    the whole point of the failure.
    """
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        sys.stderr.write((out.stderr or out.stdout or "").strip()[-2000:] + "\n")
        raise SystemExit(f"{cmd[1]} failed (exit {out.returncode}): "
                         f"{' '.join(cmd[:6])} ...")


def extract_stamp(pbf: Path) -> str:
    """The extract's OSM replication timestamp — the cache's freshness key."""
    out = subprocess.run(["osmium", "fileinfo", "-g", "header.option.timestamp",
                          str(pbf)], capture_output=True, text=True)
    return (out.stdout or "").strip() or "unknown"


def _canonical_id(oid: str) -> str:
    """Collapse osmium's AREA ids back onto the object they were built from.

    `--add-unique-id=type_id` labels the polygon copy of a closed way `a<2*wid>`
    and the linestring copy `w<wid>`, so deduping on the raw id removes nothing —
    the first build doubled every parking way and the count still looked
    plausible. Area ids encode the source: even = way, id//2; odd = relation,
    (id-1)//2.
    """
    if not oid or oid[0] != "a":
        return oid
    try:
        n = int(oid[1:])
    except ValueError:
        return oid
    return f"w{n // 2}" if n % 2 == 0 else f"r{(n - 1) // 2}"


def _atomic_write(path: Path, write) -> None:
    tmp = path.with_suffix(path.suffix + ".part")
    with open(tmp, "w") as fh:
        write(fh)
    tmp.replace(path)


# --------------------------------------------------------------- 1. parking
def build_parking() -> None:
    """Every parking / trailhead feature in the US, as Overpass-shaped rows.

    Two stages so the expensive pass happens once: tags-filter cuts 3.5 GB down
    to the parking subset, then `osmium export` resolves geometry on the small
    result. Way and relation features get a BBOX CENTRE, not a centroid, because
    that is what Overpass `out center` returns and the caller's `_point()` reads
    it the same way — a centroid would silently move every multi-polygon lot.
    """
    subset = CACHE_DIR / "parking-only.osm.pbf"
    if not subset.exists():
        log(f"tags-filter {ACCESS.name} -> parking subset")
        t = time.time()
        run(["osmium", "tags-filter", str(ACCESS),
             "n/amenity=parking", "w/amenity=parking", "r/amenity=parking",
             "n/highway=trailhead", "w/highway=trailhead", "r/highway=trailhead",
             "-o", str(subset), "--overwrite"])
        log(f"  subset written in {time.time() - t:.0f}s "
            f"({subset.stat().st_size / 1e6:.0f} MB)")

    # ALL geometry types, not just point+polygon. `osmium export` decides
    # area-ness from its own tag list, and `amenity=parking` is not on it — a
    # point,polygon export returned 49,429 features from a subset holding
    # 1,392,662 parking WAYS, silently, exit 0. A closed way arriving as a
    # LineString still carries every coordinate, which is all a bbox centre
    # needs, so accepting linestrings costs nothing and is the fix.
    log("exporting parking geometry")
    t = time.time()
    proc = subprocess.Popen(
        ["osmium", "export", str(subset), "-f", "geojsonseq",
         "--geometry-types=point,linestring,polygon",
         "--add-unique-id=type_id"],
        stdout=subprocess.PIPE, text=True, bufsize=1 << 20)
    rows: list[dict] = []
    kinds = collections.Counter()
    # A closed way comes out TWICE with these geometry types — once as a
    # LineString and once as a Polygon — which doubled the first build to
    # 2,850,284 features for 1,392,662 parking ways. The unique id is what makes
    # that visible; first occurrence wins.
    seen_ids: set[str] = set()

    def _coords(g):
        t_ = g.get("type")
        c = g.get("coordinates")
        if t_ == "Point":
            return [c]
        if t_ == "LineString":
            return c
        if t_ == "Polygon":
            return c[0]
        if t_ == "MultiPolygon":
            return c[0][0]
        return []

    for line in proc.stdout:
        line = line.strip().lstrip("\x1e")
        if not line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        tags = d.get("properties") or {}
        if tags.get("amenity") != "parking" and tags.get("highway") != "trailhead":
            continue
        oid = _canonical_id(str(d.get("id") or ""))
        if oid:
            if oid in seen_ids:
                continue
            seen_ids.add(oid)
        g = d.get("geometry") or {}
        pts = _coords(g)
        if not pts:
            continue
        if g.get("type") == "Point":
            lon, lat = pts[0]
            kind = "node"
        else:
            # BBOX centre, not centroid — Overpass `out center` returns the
            # bbox centre and `_point()` reads it the same way, so a centroid
            # would quietly move every multi-node lot relative to the answer
            # this replaces.
            lons = [p[0] for p in pts]
            lats = [p[1] for p in pts]
            lon = (min(lons) + max(lons)) / 2
            lat = (min(lats) + max(lats)) / 2
            kind = "way"
        kinds[kind] += 1
        rows.append({"type": kind, "lat": round(lat, 7), "lon": round(lon, 7),
                     "tags": tags})
    if proc.wait() != 0:
        raise SystemExit("osmium export failed")
    # A national parking layer is ~1.4M features. Anything near 50k means the
    # export dropped the ways again, which is exactly the failure this comment
    # exists for: it exits 0 and looks like a smaller country.
    if len(rows) < 500_000:
        raise SystemExit(f"only {len(rows):,} parking features nationally — "
                         "expected >1M. The export dropped a geometry type; "
                         "refusing to write a cache that would silently "
                         "under-answer every state.")

    out = CACHE_DIR / "parking.jsonl"

    def write(fh):
        fh.write(json.dumps({"_meta": {"version": CACHE_VERSION,
                                       "source": ACCESS.name,
                                       "stamp": extract_stamp(ACCESS),
                                       "count": len(rows)}}) + "\n")
        for r in rows:
            fh.write(json.dumps(r, separators=(",", ":")) + "\n")

    _atomic_write(out, write)
    log(f"  parking.jsonl: {len(rows):,} features {dict(kinds)} "
        f"in {time.time() - t:.0f}s ({out.stat().st_size / 1e6:.0f} MB)")


# ------------------------------------------------------------ 2. boundaries
def shipped_relation_ids() -> list[int]:
    ids = []
    for f in sorted(GEOM.glob("*.json")):
        try:
            rid = json.loads(f.read_text()).get("osm_relation_id")
        except Exception:                   # noqa: BLE001
            continue
        if rid:
            ids.append(int(rid))
    return sorted(set(ids))


def build_boundaries() -> None:
    """Boundary polygons for every relation shipped geom names.

    `osmium getid -r -t` pulls the relations plus the ways and nodes they need;
    `osmium export` then assembles them. osmium area ids encode the source —
    odd ids are relations, relation_id = (area_id - 1) // 2 — which is how the
    export maps back to `osm_relation_id`.
    """
    rel_ids = shipped_relation_ids()
    log(f"{len(rel_ids):,} relation ids in shipped geom")
    idfile = CACHE_DIR / "relids.txt"
    idfile.write_text("".join(f"r{r}\n" for r in rel_ids))

    subset = CACHE_DIR / "boundaries.osm.pbf"
    if subset.exists() and subset.stat().st_size > 0:
        log(f"reusing {subset.name} ({subset.stat().st_size / 1e6:.0f} MB) — "
            "delete it to force a re-cut")
    else:
        log(f"osmium getid over {LATEST.name} (this is the slow one, ~10 min)")
        t = time.time()
        run(["osmium", "getid", "-r", "-t", "--id-file", str(idfile),
             str(LATEST), "-o", str(subset), "--overwrite"])
        log(f"  subset written in {time.time() - t:.0f}s "
            f"({subset.stat().st_size / 1e6:.0f} MB)")

    out = CACHE_DIR / "boundaries.geojsonseq"
    log("exporting boundary polygons")
    t = time.time()
    with open(out.with_suffix(".part"), "w") as fh:
        rc = subprocess.run(
            ["osmium", "export", str(subset), "-f", "geojsonseq",
             "--geometry-types=polygon", "--add-unique-id=type_id"],
            stdout=fh).returncode
    if rc != 0:
        raise SystemExit("osmium export failed")
    out.with_suffix(".part").replace(out)
    n = sum(1 for _ in open(out))
    log(f"  boundaries.geojsonseq: {n:,} features in {time.time() - t:.0f}s "
        f"({out.stat().st_size / 1e6:.0f} MB)")


# -------------------------------------------------------------- 3. road gate
def build_road_gate() -> None:
    """The road gate's verdict for every federal candidate point nationally.

    Done once for the whole country rather than per state, because the scan cost
    is the extract, not the number of points: 345M node lines either way. One
    11-minute pass replaces 51 states' worth of chunked `around:250` queries.

    Roads-only means every node in the filtered extract IS a road node, so no way
    membership has to be rebuilt. `_road_gate_filter` still does the arithmetic.
    """
    hw = ("motorway,trunk,primary,secondary,tertiary,unclassified,residential,"
          "service,track,road")
    links = ",".join(f"{h}_link" for h in hw.split(","))
    roads = CACHE_DIR / "roads-only.osm.pbf"
    if not roads.exists():
        log("tags-filter -> drivable roads only")
        t = time.time()
        run(["osmium", "tags-filter", str(ACCESS),
             f"w/highway={hw}", f"w/highway={links}",
             "-o", str(roads), "--overwrite"])
        log(f"  roads written in {time.time() - t:.0f}s")

    log("fetching federal candidate points (ArcGIS)")
    fed, failed = _ap.fetch_federal(US_BBOX)
    if failed:
        raise SystemExit(f"federal source(s) failed: {sorted(failed)} — "
                         "refusing to cache a partial gate")
    if len(fed) < 2000:
        # A readable ArcGIS layer still returns 0 for a bad envelope without
        # raising. A tiny national answer is a bug, not an answer.
        raise SystemExit(f"only {len(fed)} federal points nationally — "
                         "expected thousands; refusing to cache")
    log(f"  {len(fed):,} federal points")

    wanted = set()
    for f in fed:
        ci, cj = int(f["lat"] / CELL), int(f["lon"] / CELL)
        for i in range(ci - 1, ci + 2):
            for j in range(cj - 1, cj + 2):
                wanted.add((i, j))

    log("scanning road node coordinates")
    t = time.time()
    proc = subprocess.Popen(["osmium", "cat", "-f", "opl", str(roads)],
                            stdout=subprocess.PIPE, text=True, bufsize=1 << 20)
    near: dict[tuple[int, int], list[tuple[float, float]]] = collections.defaultdict(list)
    n_lines = 0
    for line in proc.stdout:
        if line[0] != "n":
            continue
        n_lines += 1
        xi = line.find(" x")
        if xi < 0:
            continue
        yi = line.find(" y", xi)
        if yi < 0:
            continue
        try:
            lon = float(line[xi + 2:yi])
            lat = float(line[yi + 2:].split(" ", 1)[0])
        except ValueError:
            continue
        key = (int(lat / CELL), int(lon / CELL))
        if key in wanted:
            near[key].append((lat, lon))
    if proc.wait() != 0:
        raise SystemExit("osmium cat failed")
    log(f"  scanned {n_lines:,} node lines in {time.time() - t:.0f}s; "
        f"{sum(len(v) for v in near.values()):,} in a candidate neighbourhood")

    verdicts: dict[str, int] = {}
    for f in fed:
        ci, cj = int(f["lat"] / CELL), int(f["lon"] / CELL)
        local = [p for i in range(ci - 1, ci + 2) for j in range(cj - 1, cj + 2)
                 for p in near.get((i, j), ())]
        ok = bool(local and _ap._road_gate_filter([f], local, _ap._ROAD_GATE_MAX_M))
        verdicts[f"{f['lat']:.6f},{f['lon']:.6f}"] = 1 if ok else 0
    kept = sum(verdicts.values())
    log(f"  road gate kept {kept:,}/{len(verdicts):,} "
        f"({100 * kept / len(verdicts):.1f}%)")

    out = CACHE_DIR / "road-gate.json"
    _atomic_write(out, lambda fh: json.dump(
        {"version": CACHE_VERSION, "gate_m": _ap._ROAD_GATE_MAX_M,
         "source": ACCESS.name, "stamp": extract_stamp(ACCESS),
         "verdicts": verdicts}, fh, separators=(",", ":")))
    log(f"  road-gate.json: {len(verdicts):,} verdicts "
        f"({out.stat().st_size / 1e6:.1f} MB)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", choices=["parking", "boundaries", "road-gate"],
                    action="append", default=[],
                    help="build just this artifact (repeatable)")
    args = ap.parse_args(argv)

    for p in (ACCESS, LATEST):
        if not p.exists():
            raise SystemExit(f"missing {p} — run trailforge/extract/fetch-us-extract.sh")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    want = set(args.only) or {"parking", "boundaries", "road-gate"}
    t0 = time.time()
    if "parking" in want:
        build_parking()
    if "boundaries" in want:
        build_boundaries()
    if "road-gate" in want:
        build_road_gate()
    log(f"done in {time.time() - t0:.0f}s -> {CACHE_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
