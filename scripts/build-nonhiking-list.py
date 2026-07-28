#!/usr/bin/env python3
"""Identify shipped trails the LANDOWNER says are not for walking, and write the
verdicts to a reviewable sidecar.

WHY A SIDECAR AND NOT A DELETE. The evidence lives in an external agency dataset,
so `model.py` cannot evaluate it at assemble time without network. Recording the
verdicts in `public/areas/nonhiking-trails.json` lets the sweep apply them now and
lets `publish_areas.py` honour them later with no network at all — the same
reversible arrangement as `aliases.json` for duplicate areas. Nothing is deleted
from OSM's side of the pipeline and every entry carries its evidence, so a wrong
call is one line to remove rather than an archaeology exercise.

THE ONLY SIGNAL USED HERE IS `trail_type = SNOW`.
`EDW_TrailNFSPublish_01` is the Forest Service's own inventory of its trails and
carries what each one is FOR. A SNOW trail is a ski or snowmobile route: real,
signed, maintained, and not a hike. 4W653 Bethel Oak is the case that started
this — it matched BETHEL RIDGE SNOWMOBILE at 83%, which is why it reads as a
truck road on summer imagery, carries `highway=track` and TIGER A51, and is
absent from AllTrails. Every source was right; none of them said "snowmobile".

DELIBERATELY NOT USED — measured and rejected 2026-07-27:
  * "no hiker use recorded" (2,346 matches). A blank field is an unpopulated
    attribute, not a negative. It would have deleted Eagle National Recreation
    Trail (23.3 mi), General Crook Trail #140 (22.6 mi) and Overland Trail #615
    — a federally designated National Recreation Trail among them.
  * `hiker_pedestrian_managed` vs `_accpt_disc`. These hold seasonal DATE RANGES
    ("01/01-12/31", "05/15-09/15"), so the distinction is about WHEN, not
    whether. Useless as a keep/drop signal.
  * TIGER `cfcc`, `foot`, name patterns. Checked by eye across four evidence
    buckets; every bucket held both real trails and truck roads, so the
    distinction is not in OSM's tags at all.

Reads the NFS layer from the cache `nfs_match.py` populated, so this needs no
network. Writes only the sidecar; use `sweep-nonhiking-trails.py` to apply it.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
from collections import Counter, defaultdict

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEOM = os.path.join(_ROOT, "public", "areas", "geom")
OUT = os.path.join(_ROOT, "public", "areas", "nonhiking-trails.json")
CACHE = "/mnt/raid/trekdex/nfs_cache"
MATCH_M = 25.0          # our node must sit this close to the agency line
MIN_SNOW_SHARE = 0.70   # share of our nodes that must land on a snow route
MAX_TERRA_SHARE = 0.25  # …and a summer trail must NOT share the tread

# The agency's own name for its own asset. `trail_type = SNOW` alone is not
# enough: it records *a* use, not the only use, and the Forest Service grooms
# plenty of genuine hiking trails in winter. Requiring the NAME to say snow too
# means three independent things agree before anything is dropped — FS types it
# SNOW, FS names it snow, and no summer trail shares the ground.
SNOW_NAME = re.compile(
    r"\bsnowmobile\b|\bx-?c\s*ski\b|\bski\b|\bwinter\b|\bsnow[- ]|"
    r"\bgroomed\b|\bsnowshoe\b", re.IGNORECASE)


def hav(a1, o1, a2, o2):
    R, p = 6371000.0, math.radians
    x = (math.sin(p(a2 - a1) / 2) ** 2
         + math.cos(p(a1)) * math.cos(p(a2)) * math.sin(p(o2 - o1) / 2) ** 2)
    return 2 * R * math.asin(min(1.0, math.sqrt(x)))


class Index:
    C = 0.0025

    def __init__(self, feats):
        self.g = defaultdict(list)
        for i, f in enumerate(feats):
            for la, lo in f["c"]:
                self.g[(int(la / self.C), int(lo / self.C))].append((la, lo, i))

    def hit(self, la, lo):
        ci, cj = int(la / self.C), int(lo / self.C)
        sp = int(MATCH_M / (self.C * 111_320)) + 1
        for i in range(ci - sp, ci + sp + 1):
            for j in range(cj - sp, cj + sp + 1):
                for pla, plo, k in self.g.get((i, j), ()):
                    if hav(la, lo, pla, plo) <= MATCH_M:
                        return k
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cache", default=CACHE)
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    verdicts: dict[str, dict[str, dict]] = {}
    stats = Counter()
    for f in sorted(os.listdir(GEOM)):
        if not f.endswith(".json"):
            continue
        slug = f[:-5]
        cpath = os.path.join(args.cache, slug + ".json")
        if not os.path.exists(cpath):
            continue                      # not Forest Service land, or not fetched
        try:
            feats = json.load(open(cpath))
            g = json.load(open(os.path.join(GEOM, f)))
        except Exception:                  # noqa: BLE001
            continue
        snow = [x for x in feats
                if (x["p"].get("trail_type") or "").strip().upper() == "SNOW"
                and SNOW_NAME.search(x["p"].get("trail_name") or "")]
        terra = [x for x in feats
                 if (x["p"].get("trail_type") or "").strip().upper() != "SNOW"]
        if not snow:
            continue
        # Index SNOW and non-SNOW separately. Two corrections a first pass got
        # wrong, both found by reading the output:
        #  1. Indexing only the SNOW subset flags any trail that merely OVERLAPS
        #     a nearby winter route. Scoring both and requiring SNOW to be the
        #     BETTER match is what the tally did (244, not 347).
        #  2. DUAL USE is the norm in the Midwest and mountain west: the same
        #     tread is a groomed ski route in winter and a hiking trail in
        #     summer. Chequamegon's Lauterman, Anvil Ninemile and "ROCK LAKE
        #     NATIONAL REC" are all that. If a TERRA trail covers the same
        #     ground, the Forest Service has a summer trail there and we keep it.
        idx = Index(snow)
        tidx = Index(terra) if terra else None
        names = [x["p"].get("trail_name") for x in snow]
        for t in g.get("trails") or []:
            nodes = [n for s in (t.get("segments") or []) for n in s[::2]
                     if len(n) >= 2]
            if not nodes:
                continue
            good = [h for h in (idx.hit(n[0], n[1]) for n in nodes)
                    if h is not None]
            snow_share = len(good) / len(nodes)
            if snow_share < MIN_SNOW_SHARE:
                continue
            terra_share = 0.0
            if tidx is not None:
                th = [h for h in (tidx.hit(n[0], n[1]) for n in nodes)
                      if h is not None]
                terra_share = len(th) / len(nodes)
            # A MARGIN, not a mere edge. Requiring snow only to beat terra kept
            # Howlock Mountain (51% vs 50%) and Thielsen Creek (63% vs 59%) —
            # both real Mount Thielsen hiking trails that are groomed in winter.
            # A near-tie is a coin flip on exactly the cases that matter.
            if terra_share > MAX_TERRA_SHARE:
                stats["dual-use kept"] += 1
                continue
            best = Counter(good).most_common(1)[0][0]
            verdicts.setdefault(slug, {})[t["id"]] = {
                "reason": "usfs-snow-trail",
                "evidence": names[best],
                "share": round(snow_share, 3),
                "terraShare": round(terra_share, 3),
                "name": t.get("name"),
                "distanceMi": t.get("distanceMi"),
            }
            stats["flagged"] += 1
        stats["areas"] += 1

    print(f"{'DRY-RUN — ' if args.dry_run else ''}flagged {stats['flagged']} trail(s) "
          f"across {len(verdicts)} area(s) as usfs-snow-trail")
    for slug, tr in sorted(verdicts.items())[:12]:
        for tid, v in list(tr.items())[:3]:
            print(f"   {v['share']:.0%} {str(v['distanceMi']):6} mi  "
                  f"{str(v['name'])[:32]:34} -> {v['evidence']}  ({slug})")
    if not args.dry_run:
        json.dump(verdicts, open(args.out, "w"), indent=1, sort_keys=True)
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
