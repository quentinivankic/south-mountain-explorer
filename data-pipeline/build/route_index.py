#!/usr/bin/env python3
"""Map OSM ways → hiking-route-relation membership (global "official" signal).

The single most portable "this is a recognised trail" signal in OSM is
membership of a walking/hiking ROUTE RELATION (`type=route`,
`route=hiking|foot|walking|running`), plus that relation's `network`
grade (iwn > nwn > rwn > lwn) and `operator`. This exists in EVERY
country's OSM with no per-country data source — the same signal mature
hiking renderers (Waymarked Trails etc.) rank on, and what lets a new
country be a config row instead of a bespoke authoritative-source wiring.

Route tags live on the RELATION, not the member ways, so `osmium export`
(ways only) can't see them. This reads the PBF with pyosmium and emits a
JSON map keyed by way id ("w<id>", matching osmium export's
--add-unique-id=type_id):

    { "w123": {"in_route": true, "network": "nwn",
               "route_name": "Te Araroa", "route_operator": "..."}, ... }

stage_osm consumes it to set the in_route_relation / network signals.

SUPER-RELATIONS: the marquee long trails (Te Araroa, the Appalachian
Trail, the GR network …) are modelled as a route SUPER-RELATION whose
members are per-section route relations — the actual ways hang off those
child relations, not the parent. A single-pass "iterate this relation's
way members" therefore misses ~all of Te Araroa's ways. So we collect
every relation first, then walk each route relation transitively through
its member RELATIONS, stamping every way reachable underneath it. A way
that appears under several routes keeps the strongest network.
"""
from __future__ import annotations

import argparse
import json
import sys

_ROUTE_KINDS = {"hiking", "foot", "walking", "running"}
# network grade → rank, so a way in several routes keeps the strongest.
_NET_RANK = {"iwn": 4, "nwn": 3, "rwn": 2, "lwn": 1}


def _net_rank(n: str) -> int:
    return _NET_RANK.get((n or "").strip().lower(), 0)


def build_index(pbf_path: str) -> dict[str, dict]:
    import osmium

    # Pass 1: record EVERY relation's route info (if it is a walking route)
    # and its way + child-relation members. Member handles are only valid
    # inside the callback, so we copy the refs out immediately.
    relations: dict[int, dict] = {}

    class Handler(osmium.SimpleHandler):
        def relation(self, r):
            tags = r.tags
            info = None
            if (tags.get("type") == "route"
                    and (tags.get("route") or "").strip().lower() in _ROUTE_KINDS):
                info = {
                    "in_route": True,
                    "network": (tags.get("network") or "").strip().lower(),
                    "route_name": tags.get("name") or "",
                    "route_operator": tags.get("operator") or "",
                }
            ways, rels = [], []
            for m in r.members:
                if m.type == "w":
                    ways.append(m.ref)
                elif m.type == "r":
                    rels.append(m.ref)
            relations[r.id] = {"info": info, "ways": ways, "rels": rels}

    Handler().apply_file(pbf_path)

    # Pass 2: from each route relation, walk transitively through member
    # relations and stamp every way reached with that route's info. Both
    # a super-relation and its children are route relations, so a way gets
    # visited from several seeds — keep the strongest-network one.
    way_route: dict[str, dict] = {}

    def _assign(wid: str, info: dict) -> None:
        prev = way_route.get(wid)
        if prev is None or _net_rank(info["network"]) > _net_rank(prev["network"]):
            way_route[wid] = info

    for rid, node in relations.items():
        info = node["info"]
        if info is None:
            continue
        seen: set[int] = set()
        stack = [rid]
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            cnode = relations.get(cur)
            if cnode is None:
                continue
            for wref in cnode["ways"]:
                _assign(f"w{wref}", info)
            for rref in cnode["rels"]:
                if rref not in seen:
                    stack.append(rref)

    return way_route


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="OSM ways -> hiking route-relation membership")
    ap.add_argument("--pbf", required=True, help="OSM .pbf (or .osm) with route relations kept")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    idx = build_index(args.pbf)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(idx, fh)
    nets: dict[str, int] = {}
    for v in idx.values():
        nets[v["network"] or "(none)"] = nets.get(v["network"] or "(none)", 0) + 1
    print(f"route index: {len(idx):,} ways in hiking routes; by network: {nets}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
