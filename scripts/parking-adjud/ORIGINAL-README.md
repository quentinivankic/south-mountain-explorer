> **Archived.** This is the original README from `/mnt/raid/trekdex/parking-adjud/`,
> kept verbatim for provenance — it is where the results tables and the first
> statement of the principles were written. The live documents are
> `docs/parking-adjudication-handoff.md` and `README.md` beside this file.

# Parking adjudication — task #53

Decide, per parking lot, **"is this real, public parking that serves a trail we
ship?"** using OSM data + aerial vision. Durable home for the tooling, data, and
lessons (the working copies lived in an ephemeral Claude job tmp). Nothing here
ships to the app yet — the endpoint is a reversible `parking-verdicts.json` sidecar
keyed by OSM id.

Built + exercised 2026-07-29 → 2026-08-01 on: **Zion Wilderness, Griffith Park (LA),
Camelback/Echo Canyon, Phoenix Mountains Preserve, Pinnacle Peak Park, Usery Mountain.**

## The verdict — how a lot is judged

Three independent axes; a lot is **KEEP** only if all three pass.

```
EXISTS   is there a real place to leave a car? (a lot/graded pad/street parking,
         NOT a pullout, open ground, or a turnaround)
PUBLIC   may an ordinary hiker park there? (not private / gated / customers-only)
SERVES   does a trail we ship plausibly explain it?
```

**DROP** = fails one axis: private (`access` tag), not-real-parking (vision), or a
NON-public facility lot (apartment/resort/church garage) or one too far to be
overflow (>1 mile walk). **KEEP** = any public lot the serves-gate surfaces within
~1 mile — park lot, street parking, or a public-facility lot — facility-adjacent or
not.

### Load-bearing principles (each learned from a real correction)

1. **SERVES = the app's own rule, run area-agnostically.** `Area.nearestParkingWithFallback`:
   for each trail, the nearest 3 lots within **805 m** of its segment endpoints, else
   the nearest 2 within a **5 km fallback cap** (the cap is ours — the app has none —
   to stop a far Dixie-NF trail crediting a random lot). Relative, not absolute:
   Big Bend is within a mile of trails but never the nearest → drop.
2. **Overflow is real, and it dominates.** A public lot the app surfaces for a trail
   is a KEEP even if it primarily serves a ball field / pool / picnic area — a
   determined hiker uses it when the main lot is full. So **facility-adjacency is
   NOT a drop or even a flag signal.** (Killed a whole "flagged" experiment: #58,
   #1548 are keeps.)
3. **"Next to a building" is not a drop — classify the neighbour from OSM.** A
   visitor center is a building too. `context_classify.py` reads the lot's OSM
   context: SUPPORT (`nature_reserve`, `tourism=information/picnic_site`,
   `amenity=ranger_station/toilets`) vs FACILITY (`place_of_worship`, `hotel/resort`,
   `leisure=golf_course/pitch/horse_riding`, `building=apartments/commercial`,
   `landuse=residential/…`). Used for the DROP side (private/commercial), never to
   flag public overflow.
   - **Adjacency, not just containment.** #946 sits 11 m from a `leisure=pitch` but
     inside the broad `leisure=park`; containment-only missed it. Use nearest
     facility AREA by edge distance (≤45 m).
   - **`leisure=park` and a big `nature_reserve` are WEAK** — a lot merely inside a
     park boundary is not a trailhead; the whole park is a reserve.
4. **Burden of proof: a surveyed lot stays unless imagery positively contradicts
   it.** `amenity=parking` + any descriptive tag (`surface`/`fee`/`capacity`/…) =
   someone stood there. "I can't see it" → zoom in or REVIEW, never DROP. Empty ≠
   absent; a blank tag ≠ "no".
5. **Zoom ladder + show the tags.** The #1596 miss (called a striped, car-filled
   `street_side` lot "trees") came from a 480 m frame with the tags hidden. Fix:
   Z1 600 m / **Z2 220 m ≈ 0.21 m/px** / Z3 140 m ≈ 0.14 m/px, the mapped polygon
   drawn, tags fed to the judge. **NAIP is the primary imagery** (ESRI throttles
   under burst); Z2 alone resolves almost everything.
6. **Trailhead-not-served = a trail-coverage gap, not a parking miss.** Named
   trailheads far from our nearest trail (Narrows, Right Fork, Zion #190) mean our
   trail geom is missing/short there — a by-product punch-list, not a drop.

Full per-lot checklist: `tools/judge_protocol.md`.

## Pipeline — run one area end to end

Extracts live on **/mnt/raid** (root disk runs ~95% full; never put pbfs there).

```bash
D=/mnt/raid/trekdex/parking-adjud
# 1. per-area OSM extract from us-latest (footways + context), ~3.5 min. dossier.py
#    and foot_route_area.py both read <slug>_ctx.osm.pbf FROM THEIR TMP DIR, so name
#    it that and put it where the scripts' TMP points (or symlink).
osmium extract --bbox=<lon0,lat0,lon1,lat1> -o <TMP>/<slug>_ctx.osm.pbf \
    /mnt/raid/trekdex/osm/us-latest.osm.pbf
# 2. dossier: full tags + ids + polygons (parking from parking-only.pbf, context from
#    <slug>_ctx.osm.pbf) + edge-distance serves gate. Emits <slug>_dossier.json,
#    <slug>_serves2.json.  (If <slug>_ctx.osm.pbf is absent it self-extracts from us-access.)
python3 tools/dossier.py <slug>
# 3. foot-network walk to our shipped trails (reads <slug>_ctx.osm.pbf)
python3 tools/foot_route_area.py <slug>    # -> <slug>_walk.json, joined to dossier
# 4. OSM context classifier — pass the pbf path explicitly
python3 tools/context_classify.py <slug> <TMP>/<slug>_ctx.osm.pbf   # -> <slug>_context.json
# 5. zoom tiles (NAIP primary) for the public-served set
python3 tools/z2render.py                  # or ladder_tiles.py for Z1/Z2/Z3
# 6. JUDGE each served lot (vision, by hand or sub-agent fan-out using judge_protocol.md)
#    store verdicts keyed by OSM id
# 7. artifact
python3 tools/padjart2.py <slug> "Display Name"     # -> <slug>_parking_v2.html
```

`review_queue.py` scans every area's verdicts and builds one cross-area REVIEW-only
page (undecided lots + any KEEP beyond 1 mile). `score2.py` scores verdicts against
`groundtruth.json` — bar is **0 confident-wrong**, not 100% match.

**Resurrecting after the job tmp is wiped:** the scripts have a hardcoded
`TMP="/home/quentin/.claude/jobs/<id>/tmp"` at the top (their original working dir).
Repoint `TMP` to a dir holding the `data/` JSONs (or copy `data/*` there) before
re-running. The `data/` + `osm/` here are the durable inputs; the scripts are the
durable logic.

## Tooling (`tools/`)

| file | role |
|---|---|
| `dossier.py` | per-lot evidence: full OSM tags/ids/polygons, edge-distance serves gate (+5 km fallback cap), OSM context join |
| `serves_relative.py` | the standalone serves gate (app rule + cap), keyed by fid |
| `foot_route_area.py` | multi-source Dijkstra on the OSM foot network → walk_m to our nearest shipped trail |
| `context_classify.py` | classify each lot's neighbourhood SUPPORT / FACILITY / PARK / NEUTRAL from OSM tags (edge-adjacency) |
| `ladder_tiles.py`, `z2render.py` | render Z1/Z2/Z3 aerials, polygon overlay, trails yellow; NAIP primary + ESRI fallback |
| `judge_protocol.md` | the per-lot checklist + verdict JSON schema (also the fan-out prompt) |
| `padjart2.py` | per-area artifact (map + per-axis cards) |
| `review_queue.py` | cross-area REVIEW-only artifact |
| `score2.py` | verdicts vs `groundtruth.json` |
| `phx_apply.py` | map an OSM-id-keyed verdict store onto per-area fids |
| `padjudicate.py` | early area-agnostic serves-only builder (superseded by dossier.py) |

## Data (`data/`) + identity

- `<slug>_dossier.json` — facilities (fid, **osm ids**, lat/lon, tags_union, ring,
  area_m2, prior, trailhead_nodes, walk), bbox.
- `<slug>_serves2.json` — edge-distance serves gate result.
- `<slug>_context.json` — OSM context category + evidence per fid.
- `<slug>_verdicts2.json` (Zion, Griffith — fid-keyed) / `phx_verdicts_osm.json`
  (Phoenix — OSM-id-keyed) — the verdicts. **The eventual sidecar must key on OSM
  ids** (fids are run-local; re-clustering shifts them). The phx store already does;
  the Zion/Griffith files are fid-keyed but every entry carries its `osm` ids.
- `groundtruth.json` — the user's own calls + confidence tiers, the test set.
- `coverage_gaps.json`, `QUALITY_REPORT.md` — by-products.

## Results (0 reviews left; all adjudicated)

| area | keep | drop | artifact |
|---|---|---|---|
| Zion Wilderness | 40 | 17 | https://claude.ai/code/artifact/3d033edd-b690-4ff4-8ab9-493618213fca |
| Griffith Park | 43 | 14 | https://claude.ai/code/artifact/645113ca-5e04-4b98-aee5-ecc7e9ea816f |
| Phoenix Mountains Preserve | 28 | 25 | https://claude.ai/code/artifact/1e9ae32b-59c2-4266-9977-2bd76b372b28 |
| Pinnacle Peak Park | 11 | 6 | https://claude.ai/code/artifact/263348bc-8dfb-4b08-9ce8-a48a3ef8a32d |
| Usery Mountain | 21 | 7 | https://claude.ai/code/artifact/12073484-43de-425e-866c-7afb9933a14c |

Camelback / Echo Canyon was also judged (**16 keep / 6 drop**, including the 3
Phoenician Cholla resort-garage drops), but its buffered bbox overlaps the Preserve
so its lots are mostly the same physical lots — not published as a separate artifact.
Review queue (now empty): https://claude.ai/code/artifact/b6d8d71e-9f8c-45d2-8676-ce86895fbb85

Griffith/Zion generalization measured **0 confident-wrong** vs the user's calls —
same protocol, no re-tuning across a wilderness and a dense city.

## Next steps

1. Graduate the accepted verdicts into a reversible `public/areas/parking-verdicts.json`
   sidecar keyed by OSM id, honoured by the pool builder (shape of `nonhiking-trails.json`);
   never able to empty an area.
2. Package `dossier.py`+`context_classify.py`+`judge_protocol.md` into `scripts/` as a
   real per-area tool; sub-agents inherit `judge_protocol.md` for scale.
3. Feed the trailhead coverage-gaps (Narrows, Right Fork, Zion #190 spur) to the trail work.
