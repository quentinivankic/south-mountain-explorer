#!/usr/bin/env python3
"""Build the per-lot judge packets for one area, and the fan-out chunks.

A packet is everything `judge_protocol.md` step 0 says a judge must read BEFORE
opening an image: prior, tags (union and per member), footprint area, the
serving trail with its edge distance and fallback flag, the foot-network walk,
trailhead nodes within 120 m, footways, building overlap, the OSM context
category — plus the paths of the pre-rendered Z1/Z2/Z3 tiles. One packet per
PUBLIC-SERVED lot (served by the edge gate, not tagged private/no/customers),
which is the same judge set `ladder_tiles.py all-served` renders.

    python3 judge_packets.py <slug> [--chunk N] [--tiles DIR]

Writes, in PADJ_TMP:
    <slug>_pub.txt            judge fids, comma-separated (the convention the
                              merge tooling has always used)
    <slug>_packets.json       {fid: packet}
    <slug>_chunk_<k>.json     [packet, ...] in chunks of N (default 15) for the
                              per-chunk judge agents
    <slug>_prompt_<k>.txt     the brief for chunk k: `judge_agent_prompt.md`
                              (the text after its front matter) with the
                              protocol, lessons, chunk and output paths filled
                              in. Hand one to each judge agent verbatim; it
                              writes <slug>_verdict_draft_<k>.json.

`--tiles` rewrites the tile paths for wherever the tiles will be READ (the
judge agents run on a different machine from the renderer). `--skip-judged
STORE` is repeatable, one store per flag.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
PADJ_TMP = os.environ.get("PADJ_TMP") or os.path.join(_HERE, "..", "work")

NON_PUBLIC_ACCESS = ("private", "no", "customers")
PROMPT_TEMPLATE = os.path.join(_HERE, "judge_agent_prompt.md")


def render_prompt_template(path: str = PROMPT_TEMPLATE) -> str:
    """The brief proper: everything after the template's front-matter rule."""
    text = open(path).read()
    marker = "\n---\n"
    if marker in text:
        text = text.split(marker, 1)[1]
    for ph in ("{PROTOCOL_PATH}", "{LESSONS_PATH}", "{CHUNK_PATH}", "{OUT_PATH}"):
        if ph not in text:
            sys.exit(f"{path} lost its {ph} placeholder")
    return text


def build_packets(slug: str, tmp: str, tiles_dir: str | None) -> dict[int, dict]:
    dos = json.load(open(os.path.join(tmp, f"{slug}_dossier.json")))
    srv = json.load(open(os.path.join(tmp, f"{slug}_serves2.json")))
    ctx = json.load(open(os.path.join(tmp, f"{slug}_context.json")))
    walk = json.load(open(os.path.join(tmp, f"{slug}_walk.json")))
    tiles = tiles_dir or os.path.join(tmp, f"{slug}_ladder")
    packets: dict[int, dict] = {}
    for fac in dos["facilities"]:
        fid = fac["fid"]
        s = srv.get(str(fid)) or {}
        if not s.get("served"):
            continue
        if (fac.get("tags_union") or {}).get("access") in NON_PUBLIC_ACCESS:
            continue
        w = walk.get(str(fid)) or fac.get("walk") or {}
        c = ctx.get(str(fid)) or {}
        packets[fid] = {
            "fid": fid,
            "area": slug,
            "osm": fac.get("osm") or [],
            "prior": fac.get("prior"),
            "descriptive_tags": fac.get("descriptive") or [],
            "tags_union": fac.get("tags_union") or {},
            "members": [{"osm": m.get("osm"), "tags": m.get("tags") or {}}
                        for m in fac.get("members") or []],
            "mixed_access": bool(fac.get("mixed_access")),
            "footprint": "polygon" if (fac.get("ring") or fac.get("rings")) else "node-only",
            "area_m2": fac.get("area_m2"),
            "serves": {"trail": s.get("trail"), "edge_m": s.get("dist_m"),
                       "fallback": bool(s.get("fallback")), "n_trails_in_range": s.get("n")},
            "walk": {"walk_m": w.get("walk_m"), "conn": w.get("conn"), "trail": w.get("trail")},
            "trailhead_nodes_120m": fac.get("trailhead_nodes") or [],
            "footways_60m": fac.get("footways_60m"),
            "building_overlap": bool(fac.get("building_overlap")),
            "context": {"category": c.get("category"), "evidence": c.get("evidence"),
                        "facility": c.get("fac_label"), "facility_edge_m": c.get("fac_area_m")},
            "tiles": {z: os.path.join(tiles, f"{fid:04d}_{z}.png") for z in ("z1", "z2", "z3")},
        }
    return packets


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("slug")
    ap.add_argument("--chunk", type=int, default=15)
    ap.add_argument("--tiles", default=None, help="tile dir as the judges will see it")
    ap.add_argument("--tmp", default=PADJ_TMP)
    ap.add_argument("--skip-judged", action="append", default=[], metavar="STORE",
                    help="OSM-id-keyed verdict store; lots whose ids it already holds are "
                         "left out of the judge set. Repeatable. Areas overlap heavily "
                         "(a wilderness's region reaches the next park's trailheads), so "
                         "without this every batch re-judges its neighbours' lots.")
    args = ap.parse_args(argv)

    packets = build_packets(args.slug, args.tmp, args.tiles)
    already: set[str] = set()
    for store in args.skip_judged:
        if os.path.exists(store):
            already |= set(json.load(open(store)))
    skipped = [f for f, p in packets.items() if any(o in already for o in p["osm"])]
    for f in skipped:
        del packets[f]
    fids = sorted(packets)
    with open(os.path.join(args.tmp, f"{args.slug}_pub.txt"), "w") as fh:
        fh.write(",".join(str(f) for f in fids))
    json.dump({str(f): packets[f] for f in fids},
              open(os.path.join(args.tmp, f"{args.slug}_packets.json"), "w"), indent=1)
    chunks = [fids[i:i + args.chunk] for i in range(0, len(fids), args.chunk)]
    template = render_prompt_template()
    for k, ch in enumerate(chunks):
        chunk_path = os.path.join(args.tmp, f"{args.slug}_chunk_{k:02d}.json")
        json.dump([packets[f] for f in ch], open(chunk_path, "w"), indent=1)
        # The brief each judge agent is handed, verbatim: the template with its
        # four placeholders filled with absolute paths.
        out_path = os.path.join(args.tmp, f"{args.slug}_verdict_draft_{k:02d}.json")
        prompt = (template.replace("{PROTOCOL_PATH}", os.path.join(_HERE, "judge_protocol.md"))
                          .replace("{LESSONS_PATH}", os.path.join(_HERE, "judge_lessons.md"))
                          .replace("{CHUNK_PATH}", os.path.abspath(chunk_path))
                          .replace("{OUT_PATH}", os.path.abspath(out_path)))
        with open(os.path.join(args.tmp, f"{args.slug}_prompt_{k:02d}.txt"), "w") as fh:
            fh.write(prompt)
    surveyed = sum(1 for f in fids if packets[f]["prior"] == "surveyed")
    fb = sum(1 for f in fids if packets[f]["serves"]["fallback"])
    missing_tiles = [f for f in fids if not all(os.path.exists(p) for p in packets[f]["tiles"].values())]
    print(f"{args.slug}: {len(fids)} public-served lots to judge "
          f"({surveyed} surveyed prior, {fb} fallback-served), {len(chunks)} chunk(s) of ≤{args.chunk}"
          + (f"; {len(skipped)} already judged in another area, skipped" if skipped else ""))
    if args.tiles is None or os.path.isdir(args.tiles or ""):
        print(f"  tiles missing for {len(missing_tiles)} lot(s)"
              + (f": {missing_tiles[:12]}" if missing_tiles else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
