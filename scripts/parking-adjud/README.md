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

## Judging an area by fan-out (the Colorado way, since 2026-09-13)

The judge set is every public-served lot (50–150 per area), split into chunks
of 15 and handed to parallel sub-agents. Dossiers stay on the homelab; only the
store and the sidecar are committed.

```bash
# homelab: dossier/serves/context/walk into work/co, then every served lot's Z1/Z2/Z3
bash tools/run_co.sh $SLUG
export PADJ_TMP=$PWD/work/co PADJ_GEOM=$PWD/../../public/areas/geom
python3 tools/ladder_tiles.py $SLUG all-served        # NAIP-primary, ESRI fallback

# laptop: pull the four JSONs and the *_z[123].png frames into work/co, then
export PADJ_TMP=$PWD/work/co
python3 tools/judge_packets.py $SLUG --tiles $PADJ_TMP/${SLUG}_ladder \
  --skip-judged data/co_verdicts_osm.json --skip-judged data/phx_verdicts_osm.json \
  --skip-judged data/ne_verdicts_osm.json               # one flag per store
#   -> <slug>_pub.txt, <slug>_packets.json, <slug>_chunk_NN.json and
#      <slug>_prompt_NN.txt. The command prints PENDING_PROMPT records only for
#      chunks that still need work; COMPLETE chunks schedule nothing.
#   ... one judge agent per pending prompt. Agents write ONLY the named
#       <slug>_verdict_continue_NN.json, checkpointing after every lot. The
#       canonical <slug>_verdict_draft_NN.json files are host-owned ...
# Re-run the same command after every wave: it validates each continuation,
# appends it atomically to the unchanged canonical prefix, archives the
# continuation, refreshes fingerprints, and emits precise RESUME prompts.
# Pre-checkpoint legacy drafts require one independently validated adoption run:
#   ...same command... --adopt-existing
python3 tools/merge_drafts.py data/co_verdicts_osm.json $SLUG      # validate only; no --write
python3 tools/replay_trust.py --work-area $SLUG --tmp $PADJ_TMP \
  --out $PADJ_TMP/shadow/${SLUG}_trust-shadow-v1.json --format summary
# The orchestration agent consumes report.items without showing the primary call:
#   AUTONOMOUS_REFRESH         -> regenerate that primary row under current inputs
#   AUTONOMOUS_BLIND_CHALLENGE -> independent challenger, known family + hashes
#   AUTONOMOUS_ARBITER         -> fetch more evidence and resolve disagreement
#   HUMAN_EXCEPTION            -> only then open the review sheet for the user
```

The provenance-preserving resolver now consumes those machine routes without
writing any publish artifact. Bind the **actual underlying model identities**;
a renamed role or prompt is not a different model family:

```bash
RUN=$(python3 tools/resolve_trust.py prepare $SLUG --tmp $PADJ_TMP \
  --primary-model primary-id:family-a \
  --challenger-model challenger-id:family-b \
  --arbiter-model arbiter-id:family-c)

# The orchestration agent dispatches only the exact prompt paths in
# $RUN/prepare.json. Agents write only their assigned $RUN/inbox files.
python3 tools/resolve_trust.py status --run "$RUN"       # 3=pending, 2=blocked, 0=ready
python3 tools/resolve_trust.py apply --run "$RUN" --chunk 0          # plan, no write
python3 tools/resolve_trust.py apply --run "$RUN" --chunk 0 --apply  # one atomic chunk
```

Repeat status/dispatch until each chunk is READY, then apply one chunk at a
time. Distinct-family confident agreement resolves directly. Disagreement,
exception-sensitive cases, or same-family agreement require an arbiter; a
same-family arbiter must add evidence whose hash is new relative to packet/prior
inputs and whose canonical stable ID is cited as `[external:id]` in the selected
decision. Only an arbiter
that remains REVIEW/contradictory produces `HUMAN_EXCEPTION`. For those few fids
only, open the review sheet and use the existing `merge_drafts.py --set`; the
human override stays outside the machine-resolution wrapper.

`prepare` writes only the ignored resolver run. `status` is read-only. `apply`
without `--apply` is read-only; with it, the resolver changes exactly one
canonical draft and checkpoint through a canonical area lock, source+authority
recheck, derived-path compare-and-swap journal, durable backup, atomic
replacement, reconstructed receipt, and retry-safe recovery. `judge_packets`
shares the area lock. Calibration and store writers take their global-resource
lock first, then area lock(s), preventing cross-area whole-file lost updates;
human overrides run inside the store+area locks. A live journal always forces
recovery, while preserve-only chunks get a durable no-op receipt. The resolver
never invokes store merge or publishing. Once all chunks are applied (and any true human
exceptions resolved), validate and write the store explicitly:

```bash
python3 tools/merge_drafts.py data/co_verdicts_osm.json $SLUG
python3 tools/merge_drafts.py data/co_verdicts_osm.json $SLUG --write
```

The exact schemas, hash identities, consensus rules, and recovery states are in
`tools/trust_resolver_schema.md`.

The shipping section below applies only after every resolver chunk has an
APPLIED receipt, any true human exceptions are resolved, and the explicit store
merge passes. `merge_drafts.py` still refuses `--write` while schema or coverage
issues stand; a surveyed-prior DROP must list `z3`.

## Shadow trust replay (read-only)

Run the trust policy before expanding another batch. It reads the five
authoritative stores, verifies they still compile byte-for-byte to the committed
sidecar, reconstructs original judge calls, and separates real review evidence
from agent-only outcomes:

```bash
python3 tools/replay_trust.py --format summary
python3 tools/replay_trust.py --include-items \
  --out work/shadow/parking-trust-shadow-v1.json --format summary
```

The default is deterministic JSON on stdout and writes nothing. `--out` is
accepted only outside the repository or under the ignored
`scripts/parking-adjud/work/` tree. There is deliberately no `--write`, `--set`,
store, sidecar, sweep, pool, workflow, or publish option.

Routes are autonomous by default: current-schema certain/strong rows go to a
blind challenger; leaning, REVIEW, fallback, no-route, and coverage-gap cases go
to an evidence-fetching arbiter; legacy/schema-deficient rows get a fresh judge
pass. Existing explicit human decisions remain authority. A human exception is
created only after those autonomous stages still disagree—the committed-corpus
replay itself creates zero direct user work.

Direct model promotion is stricter than historical agreement. The exact class
must meet its own 95% error bound (KEEP ≤1%, DROP ≤2%) from per-decision
`blind_reviews` records across at least three areas and two verified reviewer
families. Each primary must carry `judge_provenance`; each reference record
must carry the complete reference decision and bind known reviewer
identity/family/kind, frozen primary and reference decision hashes, a shared
packet hash, and distinct prompt/evidence hashes. Identity keys are trimmed,
case-folded, and restricted to a slug alphabet before equality or breadth is
counted. Missing/unknown/noncanonical identities, a reviewer ID/family matching
the primary, model-visible area booleans, mismatched packets, and malformed or
mismatched hashes cannot promote anything. Same-model votes never count as
independent.

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

The sweep refuses to empty an area by default. Its only exceptions are the
exact `(OSM verdict key, DROP reason, multiplicity)` signatures committed in
`_REVIEWED_EMPTY_SIGNATURES`; these are case-specific reviewed approvals, not
a force flag, and the CLI has no bypass. In `--dry-run`, require one
`REVIEWED-EMPTY ... exact signature` line per approved case and no `REFUSING`
line; check both stdout and stderr because a refusal does not make the command
fail. A changed matched key, reason, or count restores refusal. Evidence and
confidence are not part of the signature.

Current reviewed cases are `mesa-valley-open-space-co` and
`sondermann-park-co` (`way/58294967:not-public`), plus
`promntory-point-open-space-co` (`way/1206954210:not-public`). Run the sweep
before rebuilding the global pool.

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
- `make_ne_review.py` is superseded by `ne_review2.py`, and both by
  `judge_review_sheet.py`, which works for any area from its packets.
- `merge_ne.py` is superseded by `merge_drafts.py` (any area, any store).
