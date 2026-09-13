# Parking adjudication — tools and data (task #53)

Per-lot KEEP/DROP verdicts for parking, from OSM tags plus aerial imagery.
**Start with `docs/parking-adjudication-handoff.md` at the repo root** — it is the
full brief. This file only covers running the code in this directory.

Graduated here from `/mnt/raid/trekdex/parking-adjud/` on 2026-09-13 so the logic
and the verdicts travel with the repo instead of living on one machine.

## Layout

```
scripts/parking-adjud/
  tools/     the pipeline (14 python + 2 shell drivers) and judge_protocol.md
  data/      dossiers, serves gates, contexts, walks, verdicts, groundtruth
  work/      created on demand; scratch for a run (git-ignored)
```

Aerial tiles are NOT in the repo — 91 MB, 139 files. They are reproducible:
NAIP returns identical pixels for the same bbox (measured 0.0/255 difference
against an August frame), and the overlays are drawn by `z2render.py` from the
committed dossier and geom. A lost Arizona tile was rebuilt from the repo alone
as proof. About a second per tile. Surviving originals are at
`/mnt/raid/trekdex/parking-adjud/data/<slug>_ladder/`.

The review artifacts (35 MB of HTML) and the OSM extracts (1.4 GB) are out for
the same reason — derived or regenerable. See section 21 of the handoff.

## Paths — all four are environment variables

Every tool resolves its paths through these, falling back to repo-relative
defaults, so nothing is pinned to one machine:

| Variable | Default | What it is |
|---|---|---|
| `PADJ_TMP` | `scripts/parking-adjud/work` | Working dir. Tools read AND write their JSON here. |
| `PADJ_GEOM` | `public/areas/geom` | Shipped trail geom. |
| `PADJ_PARKING_PBF` | a region pbf in `PADJ_TMP`, else `/mnt/raid/trekdex/osm/cache/parking-only.osm.pbf` | The `amenity=parking` extract. |
| `PADJ_US` | `/mnt/raid/trekdex/osm/us-access.osm.pbf` | What per-area context is cut from. |
| `PADJ_OSM` | `/mnt/raid/trekdex/parking-adjud/osm` | Per-area `_ctx.osm.pbf` extracts. |

`score2.py` reads `groundtruth.json` from `PADJ_TMP` too, defaulting to the
sibling `data/` directory, so it scores without any environment set at all.

To work against the committed data, point `PADJ_TMP` at `data/`:

```bash
cd scripts/parking-adjud
export PADJ_TMP=$PWD/data
python3 tools/merge_ne.py            # validates the NE drafts, prints every DROP
```

The last two default to `/mnt/raid` because the OSM extracts are tens of GB and
belong with the rest of the pipeline's extracts. **Everything that needs them is
homelab-only.** Everything else — reading verdicts, scoring, building artifacts,
judging from already-rendered tiles — runs anywhere.

## Speed: use a regional parking extract

`dossier.py` scans the whole parking pbf per area. Measured on
`grafton-notch-state-park-me` 2026-09-13: **6 m 50 s** against the 120 MB
national `parking-only.osm.pbf`, **14.7 s** against a regional one — 28x, same
result. Always set `PADJ_PARKING_PBF` for a batch.

```bash
osmium tags-filter -o region_parking.osm.pbf <region>.osm.pbf \\
  n/amenity=parking w/amenity=parking r/amenity=parking
export PADJ_PARKING_PBF=$PWD/region_parking.osm.pbf
```

## Running one area end to end

Needs `osmium`, `shapely`, `Pillow`, and the OSM extracts (so: homelab).

```bash
cd scripts/parking-adjud
export PADJ_TMP=$PWD/work
SLUG=<area-slug>                       # matches public/areas/geom/<slug>.json

# bbox = the geom bbox expanded 0.06 deg on every side (dossier.py's BUF)
read X0 Y0 X1 Y1 < <(python3 -c "import json;b=json.load(open('../../public/areas/geom/$SLUG.json'))['bbox'];print(b[0]-0.06,b[1]-0.06,b[2]+0.06,b[3]+0.06)")
osmium extract --bbox=$X0,$Y0,$X1,$Y1 -o $PADJ_TMP/${SLUG}_ctx.osm.pbf \
  /mnt/raid/trekdex/osm/us-latest.osm.pbf

python3 tools/dossier.py          $SLUG    # lots + tags + osm ids + serves gate
python3 tools/foot_route_area.py  $SLUG    # walk_m to our nearest shipped trail
python3 tools/context_classify.py $SLUG $PADJ_TMP/${SLUG}_ctx.osm.pbf
python3 tools/z2render.py                  # aerial tiles for the served set
#   ... judge each lot (see tools/judge_protocol.md) ...
python3 tools/padjart2.py         $SLUG "Display Name"
```

`tools/run_ne.sh <slug>` and `tools/run_co.sh <slug>` are the batch drivers; both
now resolve their own directory rather than a hardcoded path.

## Shipping verdicts (runs anywhere, no extracts needed)

The stores in `data/` are the source; `public/areas/parking-verdicts.json` is
the committed sidecar the pipeline reads. After a store changes:

```bash
python3 scripts/build-parking-verdicts.py          # stores -> sidecar (deterministic)
python3 scripts/sweep-parking-verdicts.py --dry-run # which shipped lots the DROPs hit
python3 scripts/sweep-parking-verdicts.py           # remove them from published geom
python3 scripts/build-parking-pool.py --out /tmp/parking.json \
  --extra public/areas/parking-pool.json            # pool honours the DROPs, lists
                                                    # judged KEEPs it lacks (--add-keeps adds)
```

Commit the sidecar and the swept geom together; `sync-geom-to-r2.yml` rebuilds
the pool from them. `add-parking.py` reads the sidecar too, so a parking roll
cannot bring a judged-out lot back. How a shipped lot is matched to a verdict
(a judged `osm` id exactly — lots rolled after 2026-09-13 carry one — else by
footprint: ring bbox-centre, inside a ring, within 10 m of an edge, within
20 m of the position) is one function in `scripts/_parking_verdicts.py`; the
decision behind it is in `TASKS.md` #53.

## Known state

- `serves_relative.py` reads a pre-protocol `zion_ctx.json` that no longer
  exists. It is **superseded by `dossier.py`**, which runs the same gate with
  polygon-edge distances. Kept because it is the clearest single statement of the
  serves rule.
- `padjudicate.py` is likewise superseded by `dossier.py`.
- `make_ne_review.py` is superseded by `ne_review2.py`.
