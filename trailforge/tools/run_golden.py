#!/usr/bin/env python3
"""Run the golden-trail suite end to end (homelab: needs osmium + subset).

For each golden trail: cut a bbox around it from the hiking subset, run the
assembler, evaluate reach/length/fragmentation, print a pass/fail table,
and write a merged FeatureCollection the QA viewer can load. Uses
golden.snapped.json if present (see verify_golden --snap), else golden.json.

    python3 tools/run_golden.py [--hiking data/hiking.osm.pbf]

Pure evaluation lives in golden_eval.py (unit-tested); this file is the
osmium/assembler orchestration and is exercised on the box.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "assemble"))
import golden_eval as ge  # noqa: E402


def _refs(entry):
    if entry["kind"] == "destination":
        return [(entry["destination"]["lon"], entry["destination"]["lat"])]
    return [(e["lon"], e["lat"]) for e in entry["endpoints"]]


def _bbox(entry):
    pts = _refs(entry)
    exp = entry.get("expected_one_way_mi", 3.0)
    pad = max(0.05, min(exp / 69.0 * 0.75, 1.5))  # deg; grows with trail length
    lons = [p[0] for p in pts]
    lats = [p[1] for p in pts]
    return (min(lons) - pad, min(lats) - pad, max(lons) + pad, max(lats) + pad)


def run(hiking: Path, golden: dict, out_geojson: Path) -> list[dict]:
    results, all_feats = [], []
    assembler = ROOT / "assemble" / "assemble.py"
    with tempfile.TemporaryDirectory() as td:
        for e in golden["trails"]:
            x0, y0, x1, y1 = _bbox(e)
            aoi = Path(td) / f"{e['id']}.osm.pbf"
            tg = Path(td) / f"{e['id']}.geojson"
            try:
                subprocess.run(["osmium", "extract", "--bbox",
                                f"{x0},{y0},{x1},{y1}", str(hiking),
                                "-o", str(aoi), "--overwrite"],
                               check=True, capture_output=True)
                subprocess.run([sys.executable, str(assembler),
                                "--in", str(aoi), "--out", str(tg)],
                               check=True, capture_output=True)
                fc = json.loads(tg.read_text())
            except subprocess.CalledProcessError as exc:
                results.append({"id": e["id"], "name": e["name"], "kind": e["kind"],
                                "passed": False,
                                "reasons": [f"pipeline error: {exc.stderr.decode()[:120]}"]})
                continue
            r = ge.evaluate(e, fc, golden.get("defaults"))
            results.append(r)
            for f in fc["features"]:
                f["properties"]["_golden"] = e["id"]
            all_feats.extend(fc["features"])
    out_geojson.write_text(json.dumps({"type": "FeatureCollection",
                                       "features": all_feats}))
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run the golden-trail regression suite")
    ap.add_argument("--hiking", default="data/hiking.osm.pbf")
    ap.add_argument("--out", default="data/golden.trails.geojson")
    args = ap.parse_args(argv)

    snapped = ROOT / "golden" / "golden.snapped.json"
    gpath = snapped if snapped.exists() else ROOT / "golden" / "golden.json"
    print(f"golden source: {gpath.name}", file=sys.stderr)
    golden = json.loads(gpath.read_text())

    hiking = Path(args.hiking)
    if not hiking.exists():
        print(f"ERROR: {hiking} not found — run `make prefilter` first", file=sys.stderr)
        return 2

    results = run(hiking, golden, Path(args.out))
    print(ge.summarize(results))
    return 0 if all(r["passed"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
