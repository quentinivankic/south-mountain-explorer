# Parking adjudication — tools and data (task #53)

Per-lot KEEP/DROP verdicts for parking, from OSM tags plus aerial imagery.
**Start with `docs/parking-adjudication-handoff.md` at the repo root** — it is the
full brief. This file only covers running the code in this directory.

Graduated here from `/mnt/raid/trekdex/parking-adjud/` on 2026-09-13 so the logic
and the verdicts travel with the repo instead of living on one machine.

## Layout

```
scripts/parking-adjud/
  tools/     pipeline tools, including national_census.py and the candidate-only baseline sealer
  approved-production-baselines-v1.json  reviewed approval registry, independently hash-pinned in code
  production-baseline-v1.json  current self-hashed exact authority for a complete US census
  requirements.txt  exact national-census geometry dependency (`Shapely==2.0.6`)
  publication-trust-root-v1.json  exact canonical corpus root; SHA-256 literally pinned in scripts/build-parking-verdicts.py
  proofless-source-baseline-v1.json  schema-v2 exact 1,273-row and 11-dossier legacy baseline
  data/      five stores, five publication floors, 11 rooted dossiers, and supporting adjudication data
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

## Paths — all five are environment variables

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
homelab-only.** Read-only verdict/scoring/build operations need no extracts.
Mutation and crash-recovery paths require POSIX `dir_fd`, `O_DIRECTORY`,
`O_NOFOLLOW`, no-follow stat, descriptor-relative replace, working fsync
semantics, owner-controlled non-group/world-writable parents, and trivial
inspectable ACLs; they fail closed where those guarantees are unavailable.

## Nationwide census: production authorization and mandatory real-PBF gate

Install the exact geometry engine with the same interpreter that runs the
census:

```bash
/absolute/path/to/python -m pip install -r scripts/parking-adjud/requirements.txt
/absolute/path/to/python -c 'import shapely; assert shapely.__version__ == "2.0.6"'
```

`tools/national_census.py` has two structurally distinct modes:

- GeoJSON-sequence fixtures and subnational PBF rehearsals emit
  `status: "non_authoritative"`; they can never claim production completion.
- A production run must use `--parking-pbf --authoritative`, a canonical
  self-hashed baseline whose self digest and complete-file digest both appear in
  the independently hash-pinned `approved-production-baselines-v1.json`, the
  national identity floor, an absolute osmium path plus expected executable
  SHA-256, a positive finite per-command osmium timeout, and an absolute
  operator-owned mode-`0700` artifact directory. The current approved v1 pair is
  self SHA-256 `08d869a9d877432089ff052034fda0f9f27b37b8c377cb1df61f14da5ae755bc`
  and canonical file SHA-256
  `0379418d36dceb9fdd9b804b8e387c40ecf2a81f72f467ba8f110c40ed673aa9`.
  Any other internally valid baseline remains non-authoritative. The baseline
  pins bundle
  `6a4ca469...0af3`, geometry authority `4364a8ef...b04`, 9,074 areas, 51
  jurisdictions, 92,442 trails (including five content-bound anonymous refs),
  245,026 endpoint occurrences, 172,830 unique endpoints, the exact 30,934-row
  live pool, and the 1,253-row 874/372/7 verdict sidecar.

Routine adjudication changes the live/verdict digests, so rotate rather than
hand-editing a seal. The sealer writes an exact canonical, self-hashed successor
with `version == N`, `name == production-baseline-vN`, and
`predecessor_self_sha256` naming the approved baseline it supersedes:

```bash
/absolute/path/to/the/Shapely-2.0.6/python \
  scripts/parking-adjud/tools/seal_production_baseline.py \
  --predecessor scripts/parking-adjud/production-baseline-v1.json \
  --bundle ios/SouthMountainExplorer/Resources/areas-index.json \
  --geom-dir public/areas/geom \
  --live-pool /absolute/path/to/new-live-pool.json \
  --verdicts public/areas/parking-verdicts.json \
  --output /absolute/operator-owned/Archive/production-baseline-v2.candidate.json
```

The result is deliberately **not approved**. Promotion requires one reviewed
change that (1) checks the candidate inputs and canonical bytes, (2) appends its
version/name/self SHA/canonical-file SHA/predecessor SHA to the contiguous
`approved-production-baselines-v1.json` lineage, (3) recomputes that registry's
self-hash, and (4) updates `APPROVED_BASELINE_REGISTRY_SHA256` in
`national_census.py`. The sealer never edits either trust root and authoritative
mode rejects the candidate until that review lands.

The laptop's mocked tests are necessary but are **not** the real-osmium gate.
Before calling a national run ready, execute this homelab-only integration with
the actual national input PBF. Do not substitute a fabricated binary fixture:

```bash
set -euo pipefail
PY=/absolute/path/to/the/Shapely-2.0.6/python
OSMIUM=$(command -v osmium)
OSMIUM=$(cd "$(dirname "$OSMIUM")" && pwd -P)/$(basename "$OSMIUM")
OSMIUM_SHA=$(shasum -a 256 "$OSMIUM" | cut -d' ' -f1)
ARTIFACT_ROOT=/absolute/operator-owned/Archive/trekdex-national-parking-census
mkdir -m 700 "$ARTIFACT_ROOT"
LIVE="$ARTIFACT_ROOT/live-pool-30934.json"
"$PY" scripts/build-parking-pool.py --out "$LIVE" \
  --extra public/areas/parking-pool.json \
  --verdicts public/areas/parking-verdicts.json --add-keeps
printf '66ae82a88233b7e7414c4234819e1e253415680a0f73fb12eed29a9550aaeb2c  %s\n' \
  "$LIVE" | shasum -a 256 -c -
test "$(wc -c < "$LIVE" | tr -d ' ')" = 1398828
/usr/bin/time -v -o "$ARTIFACT_ROOT/census-peak-rss.txt" \
"$PY" scripts/parking-adjud/tools/national_census.py \
  --parking-pbf /absolute/path/to/current-us.osm.pbf \
  --authoritative \
  --production-baseline scripts/parking-adjud/production-baseline-v1.json \
  --pbf-artifact-dir "$ARTIFACT_ROOT" \
  --osmium "$OSMIUM" --expected-osmium-sha256 "$OSMIUM_SHA" \
  --osmium-timeout-seconds 21600 \
  --live-pool "$LIVE" --verdicts public/areas/parking-verdicts.json \
  --output-dir "$ARTIFACT_ROOT/census-output"
"$PY" - <<'PY'
import json
from pathlib import Path
root = Path("/absolute/operator-owned/Archive/trekdex-national-parking-census/census-output")
manifest = json.loads((root / "manifest.json").read_bytes())
assert manifest["status"] == "complete"
assert manifest["production_authorization"]["authoritative"] is True
inventory = manifest["sources"]["parking"]["inventory"]
assert inventory["exact_filter_identity_reconciliation"] is True
assert inventory["exact_filter_membership_reconciliation"] is True
assert inventory["exact_identity_reconciliation"] is True
assert inventory["input_forms"]["node"] > 0
assert inventory["input_forms"]["way"] > 0
assert inventory["input_forms"]["relation"] > 0
assert manifest["sources"]["parking"]["filtered_pbf"]["sha256"]
assert (root / "publication-receipt.json").is_file()
PY
test -s "$ARTIFACT_ROOT/census-peak-rss.txt"
grep 'Maximum resident set size' "$ARTIFACT_ROOT/census-peak-rss.txt"
```

`census-peak-rss.txt` is a mandatory homelab gate artifact. Record its maximum
resident set size, host memory, and per-pass durations with the run report. RSS
is intentionally nondeterministic operational evidence and never enters the
manifest, run ID, baseline, or other authority bytes.

The focused pytest lane also has a real-osmium integration test. When osmium is
installed it creates tiny OSM XML and PBF files under pytest's temporary
directory, then executes the real source inventory, filter, filtered inventory,
and export commands for a parking node, open way, closed `area=yes` way, closed
ways using each standard false OSM boolean (`area=no`, `area=false`, and
`area=0`), and a multipolygon relation. It skips only when osmium is absent;
no binary fixture is committed. Inventory normalizes declared `area` values
with strip/casefold. Open ways and closed ways whose normalized value is one of
`0`, `false`, or `no` require a `LineString`; derived polygonal copies for those
ways are reconciled but ignored before endpoint association. Other closed ways
require a `Polygon` or `MultiPolygon`. The same ordered false-value set and
suppression stage are bound into filtered-artifact policy and inventory
provenance.

The input-side identity inventory is independently produced with osmium
`tags-filter -R -f opl`, then reconciled exactly to both the normal filtered
snapshot and exported node/way/relation forms. The filtered PBF and its
self-hashed provenance manifest are content-address installed below
`ARTIFACT_ROOT`; source, tool, filtered bytes, forms, and relation memberships
all bind the run ID. Capture also intersects the complete parking export with
the strict sidecar's OSM alias allow-set before the 5 km envelope discard. Only
that sidecar-bounded seen set survives export reconciliation; its exact count
and hash bind the manifest and run ID. Every seen alias reserves its sidecar key
globally, so an envelope-dropped exact lot remains a tombstone and can never be
reassigned to a positional neighbour. Conflicting sidecar ownership of one OSM
alias fails closed.

An exact rerun scans installed content addresses before creating a stage and
reuses one only after no-follow owner/mode checks, canonical
manifest and self-hash validation, exact source/osmium/filter/input-inventory
matching, filtered byte hash/length verification, and fresh filtered/export
reconciliation. It does not rerun `tags-filter` or create a stage. A matching
filtered digest with different provenance fails and names the existing artifact.
If another process wins promotion with the exact artifact, the winner is fully
verified and reused; the losing stage remains preserved. Missing replication
headers are allowed only for a non-authoritative local extract.

Every failed output or PBF stage is retained with a dated diagnostic name. The
tool never removes or prunes stages. After diagnosis, the operator must move a
failed stage into an owner-only `Archive` path; if promotion happened without a
publication receipt, verify/recover it in place or archive it before retrying.
Only `publication-receipt.json`, written after the parent-directory fsync,
reports successful durability.

Large parking footprints use a power-of-two multi-resolution point index; each
query checks every active level and exact geometry remains the decision. The
40 m duplicate-review lane uses a dateline-safe sweep-line candidate generator.
Neither path truncates a footprint; only total index-association, query-candidate,
and candidate-pair caps fail closed. Service closure builds one immutable
endpoint/component/sorted-distance graph and precomputes raw/current selections
and bindings once. Fixed-point rounds revisit only verdict-dependent effective
selection and merge those bindings; manifest counters prove zero association-row
rebuilds or re-sorts per round. The unassigned total is the exact disjoint sum
of `area_less_tombstones`, `retired_area_tombstones`, and
`unassigned_candidate_clusters`; no residual bucket is labelled area-less.

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
# Re-run the same command after every wave: while holding the area lock it
# opens each continuation with O_NOFOLLOW, captures and validates those bytes
# once, builds every draft/checkpoint proposal in memory, rechecks entry
# identity, commits only the captured image, archives that same image under its
# full hash, then moves—not deletes—the live entry into owner-only quarantine.
# If newer agent work wins the final retirement race, its bytes remain in the
# reported quarantine path and the command fails recovery-required rather than
# losing them. Changed/symlinked continuations fail before canonical writes.
# Pre-checkpoint legacy drafts require one independently validated adoption run:
#   ...same command... --adopt-existing
python3 tools/merge_drafts.py data/co_verdicts_osm.json $SLUG      # validate only; no --write
python3 tools/replay_trust.py --work-area $SLUG --tmp $PADJ_TMP \
  --out $PADJ_TMP/shadow/${SLUG}_trust-shadow-v1.json --format summary
# The orchestration agent consumes report.items without showing the primary call:
#   AUTONOMOUS_REFRESH         -> regenerate that primary row under current inputs
#   AUTONOMOUS_BLIND_CHALLENGE -> independent challenger, known family + hashes
#   AUTONOMOUS_ARBITER         -> use host-frozen evidence; dispatch with
#                                 browser/network tools disabled
#   HUMAN_EXCEPTION            -> only then open the human-only review sheet
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

Preparation schema v4 is path-independent. Its run ID binds the complete
source/checkpoint vectors, model configs, fid/chunk/row/route set, primary
records, host-frozen evidence catalog, prompt templates, and normative-document
hashes, but never a workstation root. Packet tile refs and generated prompt
packet/output/rule/catalog refs are canonical run-relative POSIX paths. The
absolute work and run roots live only in `runtime-context.json`, which loaders
receive out of band and exclude from authority and publication proof bytes.
Identical inputs prepared under different absolute roots therefore produce the
same run ID, `prepare.json`, packets, and prompts. Prepare v2/v3 retain their
historical path-bound replay contracts but cannot authorize new live work.
Prepare holds the canonical area `LOCK_EX` from source capture through
completed-run reload. `prepare.json` is strict duplicate-free canonical JSON;
whitespace-only or duplicate-key rewrites fail before review, authority, or
publication. The host copies every assigned evidence artifact, source manifest,
and source metadata file into content-addressed run paths. A loaded run must
live in its hash-named directory and every packet, prompt, evidence, manifest,
and metadata artifact is re-read before it is authority. Persisted resolver-v1
rows still replay under their frozen policy. Area slugs are separator-free.
Canonical resolver/authority/publication target installation opens and hashes the source
once, copies those captured bytes into a fresh unpredictable
`O_EXCL|O_NOFOLLOW` single-link inode in the trusted target directory, fsyncs
and verifies it, renames that private entry, then verifies the installed inode
and bytes. The deterministic source stage is never renamed. This checked POSIX
primitive covers resolver canonical drafts, authority receipt/draft/checkpoint,
publication proof/floor/store/candidate, sidecar output, and
authority/publication journal archival; auxiliary writers use the same
`trusted_filesystem.py` primitives where they mutate canonical state. Every
target/lock parent must be owned by the current euid and not group/world
writable, with trivial descriptor-bound ACLs; unsupported or failed ACL
inspection and inherited/extended ACLs fail closed. Lock files are regular,
single-link, mode 0600 and descriptor-matched to their live entry. Canonical legacy 0644 lock
inodes are tightened in place without inode rotation. Portable POSIX still
cannot protect a private basename from a malicious same-UID peer inside that
trusted directory, so every such process must obey the canonical lock.

The run also retains immutable pre-apply packet, draft, and checkpoint source
buffers; route rechecks always use those captures, so applying one chunk cannot
change a sibling's prepared route. The host first reads each packet JSON, dossier, judge set, draft, and checkpoint
once into an immutable buffer; parsing, normalized publication fields, routing,
and recorded hashes all derive from that same read. Before any checkpoint or
replay logic can inspect imagery, the host opens each Z1/Z2/Z3 PNG beneath the
canonical ladder directory descriptor with `O_NOFOLLOW`; pre/post fd and live
entry device/inode/mode/link/size/mtime/ctime must remain identical around the
single read. It then writes content-addressed run-local `tiles/` bytes and
frozen packet paths. No later stage rereads mutable source imagery. External
evidence manifest, artifact, and metadata files use the same one-read stable
capture; run materialization consumes only those buffers, and prepare fully
reloads/validates the completed run before returning a dispatchable path.
The arbiter reads the bound `evidence/catalog.json` and may output only an assigned
source `id` plus structured `supports`; it cannot supply paths, URLs,
timestamps, or hashes. These Python tools prepare files but do not launch a
network sandbox. Orchestration MUST dispatch only the generated prompt and its
assigned run-local files with browser/network tools disabled. Packet tags may
contain URL strings and the frozen catalog retains the host's HTTPS audit
locator; neither is permission to fetch. `judge_review_sheet.py` is human-only
and renders a deterministic self-contained sheet from frozen run-local PNGs;
it displays OSM IDs/coordinates as labels without live map or file evidence.
The host persists each source's HTTPS locator, required UTC `retrieved_at`,
optional update time, content/manifest/metadata hashes, content-addressed paths,
structured support, and axis-local `[external:id]` citation. `retrieved_at`
MUST be at or after manifest `fetched_at` and at or before the host capture/wall
clock; source metadata may not claim an update after retrieval. An unassigned
ID, duplicate support, mismatched call, wrong-axis citation, impossible
chronology, or changed frozen byte is invalid. This provenance
improves auditability; it never turns a same-family conclusion into authority.

Repeat status/dispatch until the **whole run** is READY, then apply one chunk
at a time. A ready-looking chunk cannot plan, write a preservation receipt, or
start a transaction while any sibling chunk is PENDING, BLOCKED, or in recovery;
only an already-durable journal may finish recovery. Distinct-ID,
distinct-family confident agreement requires the same verdict and all three
axis calls. An arbiter may resolve only when its verdict/axis tuple exactly
matches a confident parent and its model ID and family differ from **every**
available parent. Any duplicate model ID or family anywhere in a participating
v2 triad is a human exception, even when a third arbiter is independent. Under
v2, every same-family outcome is a human exception:
external evidence can improve the review packet, but no novel hash, citation,
or same-family vote establishes independent authority. A nonbinary arbiter,
parent/arbiter mismatch, or unresolved lot-level right also becomes a human
exception. For those fids only, create immutable human review evidence from
the exact authority run:

```bash
python3 tools/judge_review_sheet.py "$SLUG" \
  --authority-run "$RUN" --sample 20 --open
# The command prints canonical review.html and review-receipt.json paths under:
# $PADJ_TMP/.review-artifacts/$SLUG/<run-id>/
```

The v2 renderer streams each frozen PNG through incremental base64 and writes
the human-visible sheet without retaining the complete HTML or any large image
set. The portable self-hashed receipt binds every prepared item, the complete
relative source-object closure, renderer/format version, logical
`review.html`, and exact reconstructed length/SHA-256; it contains no source or
sheet absolute path. Replay invokes only the closed trusted v2 renderer and
hashes its output without writing or storing a sheet object. Artifact creation
holds the area lock exclusively through the owner-only single-link sheet and
receipt installs, so sample requests cannot interleave. A sheet-only crash is
non-authoritative; exact retry keeps an already-correct inode. Equivalent runs
under different roots share receipt/sheet authority while append-only local
runtime locators remain separate. `calibration.py add --reviewed KEEP=sample`
still takes the canonical absolute receipt path and resolves an operational run
only through those non-authoritative locators. `--legacy-sample-manifest` is
replay-only.

#### REVIEW confirmation is a non-adding, non-dropping shield

The unbound `--set` CLI is disabled; its helper remains solely to replay
already-persisted legacy overrides. New decision changes—including binary
flips and binary-to-REVIEW safety deferrals—use one `--decide FID=VERDICT
--axis AXIS=CALL --axis-evidence AXIS=TEXT --note ... --reviewer ID
--authority-run PATH --review-receipt PATH --judged YYYY-MM-DD` invocation so
the exact reviewed item, full original decision, and coherent effective axes
are bound. Use one `--confirm FID=VERDICT --note ... --reviewer ID
--authority-run PATH --review-receipt PATH --judged YYYY-MM-DD` invocation when
the human explicitly affirms the same KEEP/DROP call or defers an existing
REVIEW for later adjudication. An effective REVIEW from either path remains a
non-adding, non-dropping shield; it is not silently promoted to KEEP or DROP.
The helpers are pure planners; the CLI
operations mutate one receipt/draft/checkpoint transaction. Under the global
store lock and area lock, the CLI independently reconstructs the request from
the frozen authority run, validates packet/schema/overlap and whole-area source
state, stages exact backups/after-images, writes a durable journal, then installs
receipt → draft → checkpoint. Only `draft_sha256` changes in the checkpoint.
The portable authority receipt binds prepare bytes, primary
envelope/assignment, packet and run IDs, reviewer/date/note, effective
decision/evidence, and review receipt/sheet/item hashes. New confirmation v3,
override v4, and authority receipt v3 contain no workstation-absolute path;
the supplied absolute authority run is operational context checked out of band.
They cannot be created or recovered with an omitted, stale, altered, or
wrong-fid review artifact. Confirmation v1/v2, override v2/v3, authority
receipt v1/v2, and their prepare/review formats remain replay-only. Historical
override v3 stays classified as structured human authority in
`trust_engine.analyse_item` only when its path-bound receipt and packet/review
hashes validate. This preserves the classification of already-reviewed rows and
prevents calibration from relabelling them as model-only; it does not permit new
v3 emission or weaken the portable v4 requirement.
A failed preflight leaves canonical targets unchanged. A crash leaves a live area journal that blocks merge, packet generation,
calibration/review-sheet writes, resolver work, and replay. Recovery has no
generic command: rerun the complete original `--decide` or `--confirm` command,
including the same `--review-receipt` and explicit `--judged` date. The current invocation must
independently reconstruct the byte-identical plan; any changed request/run or
unknown target/stage/backup state fails without advancing canonical files.

Store merge resolves portable authority through the supplied terminal run and
locally validated operational locators; those paths never enter receipt or proof
identity. It expands each complete transitive alias-connected component across
store and dossier IDs before comparing full decision/authority signatures.
Every holder, including same-area aliases, participates; conflicts block. An
agreeing `user*` record already in the store takes precedence over incoming
machine wrappers, and no unused proof is appended for a fully shadowed result.
Location/rings/name and aliases come from the bound terminal source. Published
attestation v3 rows reference a proof-manifest v4 digest in compact registry v2,
not a row self-assertion. Historical inline registry v1 and proof v1-v3 remain
replay-only.

`prepare` writes only the ignored resolver run. `status` is read-only. `apply`
without `--apply` is read-only. With it, a whole-run READY check first copies
all present/missing role outputs into a hashed host-owned `output-seal.json` and
`sealed-inbox/`; the seal binds the all-items READY resolution snapshot.
Subsequent planning, recovery, and receipts ignore mutable live inbox files, and
a journal without that matching seal—or with any unresolved sealed sibling—can
never recover into a canonical write. The resolver then changes exactly one canonical draft/checkpoint
through the area lock, source+authority recheck, derived-path compare-and-swap
journal, durable backup, atomic replacement, reconstructed receipt, and
retry-safe recovery. `judge_packets` shares the area lock. Calibration and store writers take their global-resource
lock first, then area lock(s), preventing cross-area whole-file lost updates;
human overrides run inside the store+area locks. `merge_drafts` registers every
opened resource/area handle in one outer ownership scope, so normal completion,
unexpected post-lock exceptions, and partial open/`flock` failures unlock and
close every acquired handle. A live journal always forces
recovery, while preserve-only chunks get a durable no-op receipt. The resolver
never invokes store merge or publishing. Once all chunks are applied (and any true human
exceptions resolved), validate and write the store explicitly:

```bash
python3 tools/merge_drafts.py data/co_verdicts_osm.json $SLUG
python3 tools/merge_drafts.py data/co_verdicts_osm.json $SLUG \
  --resolver-run "$FINAL_RUN" --judged YYYY-MM-DD --write
```

`--write` accepts exactly one area and always requires that explicit terminal
resolver run. Preparation binds the exact dossier bytes, judge-set bytes, and
normalized publication fields (complete OSM aliases, location, footprint, and
name); merge refuses later drift and writes those bound fields rather than a
mutable dossier. The run must target the same area/work directory/fid set,
report only APPLIED or durably PRESERVED chunks, and leave every row with current
machine or receipt-bound human authority. Producer-valid dossier `rings: null`
(and `ring: null`) intentionally means node-only geometry: prepare normalizes it
to `rings: []`, publication retains the parking row, and replay verifies that
empty footprint. It is not interpreted as a DROP or as permission to invent a
polygon. Malformed non-null ring values still fail closed. Merge writes
attestation v3 plus compact
`<store>_publication_proofs.json` registry v2. Each append-only registry entry is
only `{path,length,sha256}` for one canonical proof-manifest v4 object. The
manifest contains a sorted logical-object table for prepares, seals, outputs,
prompts, templates/rules, packets, terminal drafts/checkpoints/receipts, review
sources, and human receipts. Raw leaves live once under
`data/publication-proof-objects/sha256/HH/<62-hex>`; reads are at most 1 MiB and
leaves at most 32 MiB. Every descriptor binds canonical repository-relative
path, exact length, and SHA-256; logical objects bind ordered leaves plus total
length/hash. Registry size is capped at 1 MiB at the streaming read edge for
both legacy and compact loaders. Every structured proof source is owner-only,
single-link, run-contained, inode-stable, and capped at 4 MiB before byte join,
JSON parse, or object staging; each manifest is also capped at 4 MiB.

Review HTML is never a proof object. Manifest v4 stores a closed renderer-v2
recipe, complete source-object map, receipt ref, and expected sheet
length/SHA-256. Offline replay streams the trusted renderer and fails on any
byte difference. It also reconstructs assignments/prompts/envelopes, READY and
terminal states, checkpoint semantics, final rows, and human review/authority
bindings. Packet-map bytes are hashed incrementally in canonical key order, and
each decoded challenger/arbiter envelope is released after its compact immutable
READY/terminal summary is recorded; decoded assignment outputs are never
retained run-wide. Inline registry v1 proof formats v1-v3 and path-bound prepare,
receipt, confirmation, override, and attestation versions remain replay-only.
A nonempty v1 registry has no proof-ID alias migration. The only forward
boundary is manual and preserving: archive the exact v1 store/registry/floor
publication and its generation-1 root, restore the reviewed generation-1
before-image, then regenerate model decisions, human review/authority, and the
publication under the portable schemas. New proof IDs are expected; never map
an old proof ID to newly generated authority.

### Oversized registry read block and generation-1 restore

The 1 MiB registry limit is an unconditional read **and** write boundary. There
is no legacy override: an operator seeing `publication proof registry exceeds 1
MiB` must follow this section rather than increase the cap. The known
841,565,189-byte Colorado v1 registry is blocked before materialization or JSON
parse. Its parent-owned forensic rollback is the same manual generation
boundary described above.

Use an exclusive maintenance window and preserve, never remove, the current
image. Set `BASE=140f8c935864ca5fe9b1070256193eee807691e3` and an owner-only durable
`ARCHIVE_ROOT` whose path contains `Archive`. Before restoring anything:

1. Record hashes, lengths, and names for the current Colorado store, proof
   registry, floor, tracked root, tracked builder pin, publication transaction
   tree, and any object tree. Move each complete current path into
   `ARCHIVE_ROOT`; do not unlink, truncate, overwrite, or omit the
   841,565,189-byte registry. Preserve relative names and move the transaction
   journals with their object-stage directories. From the repository root, the
   move-only restore skeleton is:

   ```bash
   set -euo pipefail
   BASE=140f8c935864ca5fe9b1070256193eee807691e3
   REPO_ROOT=$(pwd -P)
   ARCHIVE_ROOT=/durable/Archive/trekdex-colorado-legacy-$(date +%Y%m%dT%H%M%S)
   case "$ARCHIVE_ROOT" in
     */Archive/*) ;;
     *) echo "archive destination must contain an Archive component: $ARCHIVE_ROOT" >&2; exit 1 ;;
   esac
   if test -e "$ARCHIVE_ROOT"; then
     echo "archive destination already exists: $ARCHIVE_ROOT" >&2
     exit 1
   fi
   mkdir -m 700 "$ARCHIVE_ROOT"
   set -C  # noclobber: every redirected evidence file must be new
   mkdir -m 700 "$ARCHIVE_ROOT/scripts"
   mkdir -m 700 "$ARCHIVE_ROOT/scripts/parking-adjud"
   mkdir -m 700 "$ARCHIVE_ROOT/scripts/parking-adjud/data"
   for path in \
     scripts/parking-adjud/data/co_verdicts_osm.json \
     scripts/parking-adjud/data/co_verdicts_osm_publication_proofs.json \
     scripts/parking-adjud/data/co_verdicts_osm_publication_floor.json \
     scripts/parking-adjud/publication-trust-root-v1.json \
     scripts/build-parking-verdicts.py; do
     bytes=$(wc -c < "$path")
     printf '%s %s\n' "$path" "$bytes"
   done > "$ARCHIVE_ROOT/current-image.lengths"
   shasum -a 256 \
     scripts/parking-adjud/data/co_verdicts_osm.json \
     scripts/parking-adjud/data/co_verdicts_osm_publication_proofs.json \
     scripts/parking-adjud/data/co_verdicts_osm_publication_floor.json \
     scripts/parking-adjud/publication-trust-root-v1.json \
     scripts/build-parking-verdicts.py \
     > "$ARCHIVE_ROOT/current-image.sha256"
   ARCHIVE_REPORT="$ARCHIVE_ROOT/archive-report"
   ARCHIVE_BATCH_ARGS=(
     archive-batch
     --report-directory "$ARCHIVE_REPORT"
     --resource "$REPO_ROOT/scripts/parking-adjud/publication-trust-root-v1.json"
     --resource "$REPO_ROOT/scripts/parking-adjud/data/publication-proof-objects"
   )
   for STORE_NAME in \
     phx_verdicts_osm.json \
     ne_verdicts_osm.json \
     zion-wilderness-ut_verdicts2.json \
     griffith-park-ca_verdicts2.json \
     co_verdicts_osm.json; do
     STORE_STEM=${STORE_NAME%.json}
     ARCHIVE_BATCH_ARGS+=(
       --resource "$REPO_ROOT/scripts/parking-adjud/data/$STORE_NAME"
       --resource "$REPO_ROOT/scripts/parking-adjud/data/${STORE_STEM}_publication_proofs.json"
       --resource "$REPO_ROOT/scripts/parking-adjud/data/${STORE_STEM}_publication_floor.json"
       --resource "$REPO_ROOT/scripts/parking-adjud/data/.trekdex-publication-transactions/$STORE_NAME.journal.json"
     )
   done
   ARCHIVE_BATCH_ARGS+=(
     --pair
     "$REPO_ROOT/scripts/parking-adjud/data/co_verdicts_osm.json"
     "$ARCHIVE_ROOT/scripts/parking-adjud/data/co_verdicts_osm.json"
     --pair
     "$REPO_ROOT/scripts/parking-adjud/data/co_verdicts_osm_publication_proofs.json"
     "$ARCHIVE_ROOT/scripts/parking-adjud/data/co_verdicts_osm_publication_proofs.json"
     --pair
     "$REPO_ROOT/scripts/parking-adjud/data/co_verdicts_osm_publication_floor.json"
     "$ARCHIVE_ROOT/scripts/parking-adjud/data/co_verdicts_osm_publication_floor.json"
     --pair
     "$REPO_ROOT/scripts/parking-adjud/publication-trust-root-v1.json"
     "$ARCHIVE_ROOT/scripts/parking-adjud/publication-trust-root-v1.json"
     --pair
     "$REPO_ROOT/scripts/build-parking-verdicts.py"
     "$ARCHIVE_ROOT/scripts/build-parking-verdicts.py"
   )
   TRANSACTION_TREE="$REPO_ROOT/scripts/parking-adjud/data/.trekdex-publication-transactions"
   OBJECT_TREE="$REPO_ROOT/scripts/parking-adjud/data/publication-proof-objects"
   ARCHIVE_BATCH_ARGS+=(
     --optional-pair "$TRANSACTION_TREE"
     "$ARCHIVE_ROOT/scripts/parking-adjud/data/.trekdex-publication-transactions"
     --optional-pair "$OBJECT_TREE"
     "$ARCHIVE_ROOT/scripts/parking-adjud/data/publication-proof-objects"
   )
   python3 "$REPO_ROOT/scripts/parking-adjud/tools/publication_objects.py" \
     "${ARCHIVE_BATCH_ARGS[@]}"
   python3 "$REPO_ROOT/scripts/parking-adjud/tools/publication_objects.py" \
     restore-generation-1 \
     --report-directory "$ARCHIVE_REPORT" \
     --repo-root "$REPO_ROOT" \
     --source-commit "$BASE"
   (cd "$ARCHIVE_ROOT" && shasum -a 256 -c current-image.sha256)
   shasum -a 256 scripts/parking-adjud/data/co_verdicts_osm.json | \
     grep -q '^44f3ac4c6793e1be66e301f6ee46875de92b8e1952351c607ae4e658d861a0cb '
   shasum -a 256 scripts/parking-adjud/data/co_verdicts_osm_publication_floor.json | \
     grep -q '^5b5d134bec54d6e3e4862cea31231e1701b5d116c4866819128b6e59b40f3cad '
   shasum -a 256 scripts/parking-adjud/publication-trust-root-v1.json | \
     grep -q '^43db143447d90f506d42b273c425e0d048e5580e605ac211750114ba350dfd85 '
   shasum -a 256 scripts/build-parking-verdicts.py | \
     grep -q '^a2ab1d97bb949422e539c7769223977090c4a5d449ce71e8e22b00764c9e3fdb '
   RESTORED_ROOT_SHA256=$(shasum -a 256 \
     scripts/parking-adjud/publication-trust-root-v1.json)
   RESTORED_ROOT_SHA256=${RESTORED_ROOT_SHA256%% *}
   grep -Fqx "    \"$RESTORED_ROOT_SHA256\"" scripts/build-parking-verdicts.py
   test ! -e scripts/parking-adjud/data/co_verdicts_osm_publication_proofs.json
   ```

   `archive-batch` accepts canonical absolute required and optional
   source/destination pairs plus the complete explicit publication resource set.
   Every optional source, destination, and derived safety path is persisted in
   PLANNED and added to the globally sorted exclusive lock set before the source
   is inspected. Only under that lease is each optional source classified as
   `present` or `absent`: present sources get the same safety-copy, move, and
   evidence checks as required sources; absent sources get an ordered durable
   resolution checkpoint and all three paths must remain absent through the
   terminal report and `verify-archive-report`. It maps every resource through
   the same `trust_resolution.resource_lock_path` contract as terminal writers,
   collision-checks and globally sorts the lock set, then holds every lock
   exclusively through all preflight, moves, syncs, absence checks, and
   postconditions. All destinations are proved absent before the first source is
   retired. Each moved child uses macOS `renameatx_np(..., RENAME_EXCL)` or Linux
   `renameat2(..., RENAME_NOREPLACE)` with no fallback.

   POSIX rename is name-bound, not descriptor-bound. The locks exclude every
   cooperating Trekdex writer, but cannot exclude a same-UID process that ignores
   the lock contract. Exact identity-bound no-loss semantics against a malicious
   same-UID process that can also tamper with the archive or report namespace are
   impossible on portable POSIX and are not claimed. Before taking locks, the
   command creates the new owner-only `archive-report` namespace without
   clobbering any path. Before any rename it creates and cryptographically
   verifies an independent sealed safety image for every required or
   present-optional source at the hidden `.*trekdex-archive-safety-*` path, then
   fsyncs canonical `plan.json` and `prepared.json` documents containing every
   safety-image digest and every optional present/absent resolution. Retain those
   images and the report namespace with the archive.

   After each rename it fsyncs destination content, then the destination parent
   and source parent; recomputes complete destination file length/SHA-256 or
   recursive tree evidence from held descriptors; reverifies the
   safety image; and compares both exactly with PREPARED evidence. Only then does
   it create and fsync that move's new no-clobber checkpoint. It durably creates
   `terminal.json` with `committed`, `not-committed`, `partially-committed`, or
   `committed-indeterminate` before releasing locks whenever the process remains
   alive. SIGKILL can omit the terminal or next checkpoint, but the retained
   PREPARED/checkpoint chain proves the last durable prefix. Stdout is never the
   authority. Every non-committed state sets `blind_retry_forbidden: true`;
   preserve every named path and reconcile explicitly. Never delete a safety
   image, overwrite a destination, edit a report, or rerun blindly.
   `verify-archive-report` uses its first validated plan read only to reconstruct
   the exact globally sorted exclusive lock set. After acquiring every lock, it
   rereads and revalidates the complete report chain, rejects a plan or state
   flip, and holds the lease through every source-absence, destination, safety
   image, and optional-resolution evidence check. Lock acquisition failure
   refuses verification and does not alter archive or report state.
   `restore-generation-1` is the sole generation-1 restore boundary. It derives
   and acquires the exact globally sorted exclusive lock set from the validated
   archive plan, rereads and revalidates the complete report chain under that
   lease, and holds the same lease through restore and final evidence checks.
   It accepts no target paths: the contract fixes the four restore paths, the
   legacy proof path, both optional trees, commit
   `140f8c935864ca5fe9b1070256193eee807691e3`, and all four expected SHA-256
   values. It reads those blobs directly from the immutable commit without
   checkout or proof execution, stages and verifies all four before installing
   any, requires the staged builder's literal root pin to equal the restored
   root hash, atomically installs each absent target with no replacement,
   verifies final hashes and required absences, and appends no-clobber
   `generation-1-restore-prepared.json` and
   `generation-1-restore-terminal.json` with evidence for all four targets
   before releasing the locks. Never run `git restore`, a checkout, or any other
   restore outside this lease-held command.

2. Restore these four tracked generation-1 blobs from `BASE`, only after their
   current versions are in the archive:
   `scripts/parking-adjud/data/co_verdicts_osm.json`,
   `scripts/parking-adjud/data/co_verdicts_osm_publication_floor.json`,
   `scripts/parking-adjud/publication-trust-root-v1.json`, and
   `scripts/build-parking-verdicts.py`. The generation-1 image intentionally has
   no `co_verdicts_osm_publication_proofs.json`; moving the legacy file to
   Archive establishes absence without deleting it.
3. Verify exact generation-1 identities: store
   `44f3ac4c6793e1be66e301f6ee46875de92b8e1952351c607ae4e658d861a0cb`,
   floor `5b5d134bec54d6e3e4862cea31231e1701b5d116c4866819128b6e59b40f3cad`,
   root `43db143447d90f506d42b273c425e0d048e5580e605ac211750114ba350dfd85`,
   builder `a2ab1d97bb949422e539c7769223977090c4a5d449ce71e8e22b00764c9e3fdb`,
   root `generation: 1` with `parent_root_sha256: null`, absent Colorado proof
   registry, no live publication journal, and the builder's literal pin equal
   to the restored root hash. A mismatch stops the restore.
4. Start a new current-schema resolver run from that restored image. Regenerate
   every affected model decision and every needed human review/authority
   receipt from frozen portable sources; do not copy a legacy envelope, reuse a
   legacy path-bound receipt as new authority, or alias an old proof ID. Apply
   every chunk through its normal transaction, then publish normally.

The migration is complete only when the durable archive still verifies; the
live registry is canonical v2 and at most 1,048,576 bytes; every new manifest is
v4 and every new row attestation is v3; the successor root is exactly generation
2 with parent hash
`43db143447d90f506d42b273c425e0d048e5580e605ac211750114ba350dfd85`;
that candidate and literal pin are separately reviewed and promoted together;
a full build/replay succeeds; and the five deferred production-corpus tests run
without an oversized-registry skip. Until every condition holds, the rollback
or regeneration is incomplete and publication remains blocked.

Object files must be current-owner, trivial-ACL, regular, single-link, and
non-writable. A normal Git checkout may restore `0644`, shared-group `0664`, or
execute bits, so replay rejects it until the explicit object seal operation
verifies path/length/hash and streams the bytes into a fresh read-only
single-link inode. The atomic replacement retires the checkout inode; a writer
opened before sealing remains attached only to that retired inode and cannot
alter canonical bytes after success. The seal clears all write and execute bits
while preserving read bits. Reads never repair permissions implicitly.
Run the seal explicitly for each compact registry after checkout and before
replay/build:

```bash
python3 tools/publication_objects.py seal \
  data/co_verdicts_osm_publication_proofs.json
```

The command accepts registry v2 only, acquires the exact registry and
`publication-proof-objects` resources through the same globally sorted
`resource_lock_path` lease used by archive and terminal writers, and holds both
locks through registry read, manifest/object replacement, and final closure
verification. It seals each manifest first, then its exact transitive leaf
closure, and prints verified object counts and bytes.

The strict canonical `publication-trust-root-v1.json` is the Git-reviewed trust
anchor for the complete publication image. Its exact bytes are pinned by the
literal `PUBLICATION_TRUST_ROOT_SHA256` in `build-parking-verdicts.py`. Schema
v1 binds generation/parent lineage, all five configured store images, all five
proof-registry states (an exact `null` means absent), all five self-hashed
append-only publication floors, the exact schema-v2 1,273-row/11-dossier legacy
baseline, and the sorted exact 11-dossier byte inventory. The generated sidecar
is derived and deliberately not rooted. Replay/build take root `LOCK_SH`;
terminal publication takes root `LOCK_EX`, target publication resources
exclusively, and sibling resources shared.

Publication is one journaled append-only transaction. It stages every exact
object first, then writes the durable journal before installing any object. The
only valid authority prefix is objects (in sorted descriptor order) → registry
→ floor → store → non-authoritative successor candidate. Existing object paths
are accepted only after exact verification and are never overwritten;
unreferenced exact objects left by a crash grant no authority. Each sealed stage
is bound into the journal; installation hard-links an absent object path, fsyncs
it, reduces the object to one link, then retains an exact stage on an independent
sealed inode. A crash at either cut leaves an exact recoverable prefix, so every
before/after-object cut retries the identical plan without overwriting an object.
The registry transition may only retain byte-identical entries and append new
ones; proof removal or rewrite fails. The floor preserves every admitted key's
first authority, attestation, and proof identity. Every target and complete
object closure is verified before journal archival. Recovery reruns only the
exact original single-area command with the same terminal run and explicit
`--judged YYYY-MM-DD`; changed inputs or malformed objects,
stages/backups/candidates/archives fail closed. The tracked root and literal pin
never change automatically. After publication, review the candidate named by
the archived journal, replace the tracked root with those exact bytes, and
update the literal pin plus pin assertions in one reviewed Git change. Until
that promotion, replay/build and every second publication reject the live image
against the old root. A PENDING/BLOCKED run cannot publish primary rows around
the resolver.

Object-stage retention is explicit and conservative. A stage directory MUST
remain in `object-stages/<run-id>/` while its journal is live, through journal
archival, and until the successor root is separately promoted and a full replay
passes. No writer or background task reaps it. The read-only
`publication_objects.py verify-stage ARCHIVED_JOURNAL STAGE_DIRECTORY` command
validates the canonical archived journal and transaction hash, enumerates the exact stage file
set, verifies every descriptor/path/length/SHA-256 and sealed inode, and reports
`descriptor_count`, `file_count`, `unique_bytes`, and
`hash_closure_sha256`. It never moves, replaces, or unlinks a file.

After the promotion/replay gates, reclaim active disk only with this move-only
procedure. `ARCHIVED_JOURNAL` is the journal already under the transaction
`Archive/`, `STAGE_DIRECTORY` is its complete
`object-stages/<run-id>/`, and `DURABLE_ARCHIVE` must be a new owner-only external
path containing an `Archive` component:

```bash
set -euo pipefail
REPO_ROOT=$(cd ../.. && pwd -P)
DATA_ROOT="$REPO_ROOT/scripts/parking-adjud/data"
: "${ARCHIVED_JOURNAL:?set the exact archived journal path}"
: "${STAGE_DIRECTORY:?set its complete object-stage directory}"
TRANSACTION_ID=replace-with-the-64-character-lowercase-transaction-hash
DURABLE_ARCHIVE="/durable/Archive/trekdex-publication-$TRANSACTION_ID"
case "$DURABLE_ARCHIVE" in
  */Archive/*) ;;
  *) echo "archive destination must contain an Archive component: $DURABLE_ARCHIVE" >&2; exit 1 ;;
esac
if test -e "$DURABLE_ARCHIVE"; then
  echo "archive destination already exists: $DURABLE_ARCHIVE" >&2
  exit 1
fi
JOURNAL_NAME=$(basename "$ARCHIVED_JOURNAL")
STAGE_NAME=$(basename "$STAGE_DIRECTORY")
if test "$JOURNAL_NAME" = "$STAGE_NAME"; then
  echo "journal and stage destination basenames collide: $JOURNAL_NAME" >&2
  exit 1
fi
mkdir -m 700 "$DURABLE_ARCHIVE"
set -C  # noclobber: verify-before.json and verify-after.json must be new
python3 tools/publication_objects.py verify-stage \
  "$ARCHIVED_JOURNAL" "$STAGE_DIRECTORY" \
  > "$DURABLE_ARCHIVE/verify-before.json"
ARCHIVE_REPORT="$DURABLE_ARCHIVE/archive-report"
ARCHIVE_BATCH_ARGS=(
  archive-batch
  --report-directory "$ARCHIVE_REPORT"
  --resource "$REPO_ROOT/scripts/parking-adjud/publication-trust-root-v1.json"
  --resource "$DATA_ROOT/publication-proof-objects"
)
for STORE_NAME in \
  phx_verdicts_osm.json \
  ne_verdicts_osm.json \
  zion-wilderness-ut_verdicts2.json \
  griffith-park-ca_verdicts2.json \
  co_verdicts_osm.json; do
  STORE_STEM=${STORE_NAME%.json}
  ARCHIVE_BATCH_ARGS+=(
    --resource "$DATA_ROOT/$STORE_NAME"
    --resource "$DATA_ROOT/${STORE_STEM}_publication_proofs.json"
    --resource "$DATA_ROOT/${STORE_STEM}_publication_floor.json"
    --resource "$DATA_ROOT/.trekdex-publication-transactions/$STORE_NAME.journal.json"
  )
done
ARCHIVE_BATCH_ARGS+=(
  --pair "$STAGE_DIRECTORY" "$DURABLE_ARCHIVE/$STAGE_NAME"
  --pair "$ARCHIVED_JOURNAL" "$DURABLE_ARCHIVE/$JOURNAL_NAME"
)
python3 tools/publication_objects.py "${ARCHIVE_BATCH_ARGS[@]}"
python3 tools/publication_objects.py verify-archive-report "$ARCHIVE_REPORT"
python3 tools/publication_objects.py verify-stage \
  "$DURABLE_ARCHIVE/$JOURNAL_NAME" \
  "$DURABLE_ARCHIVE/$STAGE_NAME" \
  > "$DURABLE_ARCHIVE/verify-after.json"
python3 - "$DURABLE_ARCHIVE/verify-before.json" \
  "$DURABLE_ARCHIVE/verify-after.json" <<'PY'
import json
import sys

before, after = (json.load(open(path)) for path in sys.argv[1:])
keys = (
    "transaction_id", "journal_sha256", "stage_run_id",
    "descriptor_count", "file_count", "unique_bytes",
    "hash_closure_sha256",
)
if any(before[key] != after[key] for key in keys):
    raise SystemExit("archived stage verification differs from source")
if after["stage_is_in_archive"] is not True:
    raise SystemExit("destination stage is not under an Archive path")
PY
```

Compare the before/after `transaction_id`, `journal_sha256`, `stage_run_id`,
`descriptor_count`, `file_count`, `unique_bytes`, and
`hash_closure_sha256`; every value must be identical and the after report must
say `stage_is_in_archive: true`. The before/after stage reports, immutable
`archive-report` plan/PREPARED/checkpoint/terminal chain, and hidden verified
safety images remain beside the moved stage and journal. `verify-archive-report`
is mandatory immediately after the batch: it accepts only terminal
`committed`, rejects missing/extra/reordered or hash-invalid moves, and reopens
every destination and safety image against its exact recorded
evidence. A failure before any rename durably reports `not-committed`; failure
after an earlier checkpoint reports `partially-committed`; and any failure after
a rename without a matching durable checkpoint reports
`committed-indeterminate`. Namespace and fsync durability may already have
changed in the latter two states, so do not claim that both original paths
remain. Preserve every path named by the report and investigate; do not retry by
deleting, copying over, or selectively moving files. If that exact external
archive cannot remain readable and verified, keep the stage in place. Budget
roughly one additional closure byte per unique staged object for the required
safety image until archival. Installed content-addressed objects remain
authoritative; the archived stage, journal, and safety images remain
forensic-only.

The exact schemas, hash identities, consensus rules, and recovery states are in
`tools/trust_resolver_schema.md`.

### Crash recovery: rerun the exact writer

There is no generic `recover` command and journals never authorize themselves:

| Live transaction | Only supported recovery |
|---|---|
| Judge continuation | Rerun the identical `judge_packets.py SLUG ...` command. |
| Resolver chunk apply | Rerun `resolve_trust.py apply --run RUN --chunk N --apply`. Read-only status/plan calls do not finish it. |
| Human authority | Rerun the complete original `merge_drafts.py ... --confirm/--decide ... --authority-run RUN --review-receipt RECEIPT --judged DATE` command. |
| Store publication | Rerun the complete original `merge_drafts.py STORE SLUG --resolver-run RUN --judged DATE --write` command. |

Every retry reconstructs expected bytes from trusted inputs and accepts only its
own exact before/after states. Do not edit, remove, or hand-build journals,
stages, backups, candidates, or archives. Publication recovery completes the
proof/floor/store/candidate transaction but does not promote the candidate;
build/replay and another publication remain blocked until a separate reviewed
Git change installs the candidate as the tracked root and updates the literal
pin.

The shipping section below applies only after every resolver chunk has an
APPLIED receipt, any true human exceptions are resolved, the explicit store
transaction passes, and its successor root has been separately reviewed and
promoted with the literal code pin. `merge_drafts.py` still refuses `--write` while schema or coverage
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
to an arbiter dispatched with browser/network tools disabled over one host-frozen
evidence pack; legacy/schema-deficient rows get a fresh judge
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

**Bear Creek freeze satisfied:** the original path-bound
`bear-creek-lake-park-co` run and four historical human decisions were not
publishable under the portable authority schemas. Quentin explicitly thawed
this scope on 2026-10-03/04 after all 50 rows were regenerated, 19 decisions
were rebound through current frozen review receipts, every human exception was
closed, and the hostile publication gates passed. The rooted generation-2
closure and sweep receipt are recorded in the publication-accounting section
below; future revisions still require the normal resolver/review gates.

The stores in `data/` are authoritative inputs;
`public/areas/parking-verdicts.json` is the committed derived sidecar. Builder
and replay hold sorted shared locks over the pinned root, every
store/proof-registry-state/floor/journal, the schema-v2 baseline, the dossier
inventory, and any output through final comparison/write. The root pin and
exact corpus image are checked before deeper semantics. Any live publication
journal blocks. Current rows require a complete attestation/proof and their OSM
dictionary key must be inside the attested alias vector. Proofless rows are
accepted only when store+key+full row exactly match the separately self-pinned
1,273-row/11-dossier `proofless-source-baseline-v1.json`, whose exact file bytes
are also rooted. Current authority cannot downgrade back to that baseline. The
corroboration test reads all five store blobs at pinned commit
`140f8c935864ca5fe9b1070256193eee807691e3`, verifies all five exact store
hashes, and recomputes all 1,273 row hashes. It intentionally fails if the
commit/blob is unavailable: this production corroboration test requires full,
non-shallow Git history and cannot run from a source tarball. It never derives
legacy authority from current proof-backed rows.
Captured dossiers and emitted source/date defaults feed the same
provenance-complete alias fold in builder and replay. A tail-crashed, forged,
key-extended, coherently rolled-back, or authority-stripped image therefore
cannot reach the sidecar.

After the exact store transaction completes, inspect the archived journal's
candidate path and hash. Review the candidate, replace the tracked
`publication-trust-root-v1.json` with those exact bytes, and update
`PUBLICATION_TRUST_ROOT_SHA256` plus its literal test assertions in the same
reviewed Git change. Do not begin a second store publication before that
promotion. Only after promotion run:

```bash
python3 scripts/build-parking-verdicts.py           # validates root, writes sidecar
python3 scripts/build-parking-verdicts.py --check   # exact sidecar postcondition
python3 scripts/parking-adjud/tools/replay_trust.py --format summary
python3 scripts/sweep-parking-verdicts.py --dry-run # which shipped lots the DROPs hit
python3 scripts/sweep-parking-verdicts.py           # remove them from published geom
python3 scripts/build-parking-pool.py --out /tmp/parking.json \
  --extra public/areas/parking-pool.json             # pool honours the DROPs, lists
                                                     # judged KEEPs it lacks (--add-keeps adds)
```

The sweep refuses to empty an area by default. Its only exceptions are the
exact `(OSM verdict key, DROP reason, multiplicity)` signatures committed in
the code-approved `reviewed-empty-v1` policy registry; these are case-specific
reviewed approvals, not a force flag, and the CLI has no bypass. Every mapped
footprint is an explicitly closed polygon with at least four points and three
distinct vertices, nonzero area, finite coordinates, at most a 2,000 m
diagonal, and a verdict position inside it or within 100 m of its edge. Empty
footprints remain valid. Sidecar and geom strings containing control, format,
or line-separator characters are rejected, and report fields are still
single-line escaped. Since sweep preflight validates the complete geom corpus
before any mutation, pre-existing format characters must be repaired as
text-only prerequisites. The current corpus required removal of leading U+200B
characters from the St. Vrain State Park area name and one embedded U+200B from
the Ann Arbor Hot Springs trail name; those repairs change no parking or trail
coordinates.

`--dry-run` is recursively nonmutating and begins `DRY-RUN — would remove`.
For an approved empty case it prints the exact `REVIEWED-EMPTY ... exact
signature` fact. Any refused area blocks the complete apply: the command exits
2, changes zero geom bytes, begins `NOT APPLIED — 0 lots removed; planned N`,
uses `planned removals:`, and retains each exact `REFUSING ...` fact on stderr.
A changed matched key, reason, or count restores refusal. Evidence and
confidence are not part of the reviewed-empty signature.

Current reviewed cases are `mesa-valley-open-space-co` and
`sondermann-park-co` (`way/58294967:not-public`), plus
`promntory-point-open-space-co` (`way/1206954210:not-public`). Run the sweep
before rebuilding the global pool.

A real sweep writes and verifies exact BEFORE backups, AFTER stages, an
owner-only exact sidecar snapshot, and the durable PREPARED journal before the
first geom replacement. PREPARED recovery derives every path and transaction
ID independently, accepts only the hardcoded historical policy version and
bytes, reconstructs from the retained sidecar plus approved policy, and
classifies the complete geom inventory as one exact AFTER prefix/BEFORE suffix.
A refreshed or newer current sidecar/policy may require a later sweep but cannot
strand this one. Recovery remains the exact original sweep command; never edit
or remove its journal or artifacts.

`add-parking.py`, `merge-published-geom.py`, the federal, degenerate, name,
and nonhiking sweeps, `backfill-area-boundary-ids.py`,
`recompute-difficulty.py`, standalone `add-elevation.py`, non-cache-only
`build-trail-counts.py` plus its low-level geom helper, canonical
`publish_areas.py`, direct canonical `to_app_json.py` output, and the canonical
sidecar builder share the same globally ordered geom/sidecar/live-journal gate.
Artifact and derived output directories stay outside the geom gate. A live
journal blocks every cooperating writer. `add-parking.py` additionally
recaptures the exact sidecar bytes and every geom source under its single commit
lease before its first write. The sweep retains the live journal through target
and receipt durability, emits and flushes the bound human report, and only then
archives the journal; an interrupted report is replayed by the exact rerun.

Commit the sidecar and the swept geom together; `sync-geom-to-r2.yml` rebuilds
the pool from them. `add-parking.py` reads the sidecar too, so a parking roll
cannot bring a judged-out lot back. How a shipped lot is matched to a verdict
(a judged `osm` id exactly — lots rolled after 2026-09-13 carry one — else by
footprint: ring bbox-centre, inside a ring, within 10 m of an edge, within
20 m of the position) is one function in `scripts/_parking_verdicts.py`; the
decision behind it is in `TASKS.md` #53.

### Publication accounting: adjudications versus source rows

Publication reports both semantic adjudications and alias-expanded source rows.
Bear Creek contains **50 adjudicated fid clusters** but produces **53 Colorado
store/floor rows**: fid 1012 has three OSM aliases and fid 1014 has two. All 53
rows carry publication attestation v3 and share their cluster's exact decision,
authority, proof, and attestation identities. The 53 floor rows divide into 34
`machine_v2`, 16 `human_confirmation_v3`, and 3 `override_v4` entries. Never
compare the 50-cluster verdict totals (`11 KEEP / 32 DROP / 7 REVIEW`) directly
to alias-expanded store-key counts.

Path-free publication receipt: archived transaction
`1e0644679d07f4fe17326bb404b6461fd8e461c697da5e2688e3655107ffe7e9`
installed store
`c2713639b10045a902b1acb5a2b1a950f54df61780d0c7473e974c6e449abc82`,
registry `4f0828917dc9e3ed9b60850fdbdb3677203ce91e3f9492ccc8e4d3f113f55012`,
floor bytes
`24277e7dc21425d61c72d89f81f6415ca74122967eb1c431e3af03c2895d7675`
(self-hash
`c8884282d397f0b78a74f85811427e05fe0780cc898a5a8b0f2e5cfdad6bd02d`),
and proof manifest
`87a4e0f3681d7e003b7aca61694c4385946ebb1f8095393947ebb0eb60cd18f1`;
the promoted generation-2 root is
`f0274ec06ff28b053cf6ce0c68d8b5ae29f51329e1c5ca6a013df02e34846a91`
over parent
`43db143447d90f506d42b273c425e0d048e5580e605ac211750114ba350dfd85`.
The permanent content-addressed proof closure is 395 blobs / 88,854,223 bytes
(394 leaves plus one manifest); this repository-size cost is intentional so a
fresh clone can replay authority without an external object service.

The rooted sweep input is `public/areas/parking-verdicts.json`, SHA-256
`6e109573d860d07c70d9f26f8fcda9695005e87c8d840ffd16f91a086db7be1d`
(1,888,918 bytes; 1,253 clusters). The `parking-verdict-sweep-v2` transaction
reached `APPLIED` over a 9,074-file geom inventory and removed 16 exact
DROP matches: 5 from `bear-valley-open-space-co`, 1 from
`red-rocks-park-co`, and 10 from `william-frederick-hayden-park-co`
(13 `not-public`, 2 `not-a-lot`, 1 `too-far`). A second pure sweep plan removes
zero. Bear Creek authority intentionally reaches neighbouring area files through
the area-agnostic OSM-id/footprint/position matcher; it also reaches
`matthewswinters-park-co`, where REVIEW fid 391 shields the existing lot and
keeps that area's parking count at 4.

The R2-style runtime pool is a derived preview, not the sweep input or a tracked
artifact. Reproduce it with:

```bash
python3 scripts/build-parking-pool.py --out <scratch>/parking.json \
  --extra public/areas/parking-pool.json \
  --verdicts public/areas/parking-verdicts.json --add-keeps
```

For this closure it emits 30,934 lots with SHA-256
`66ae82a88233b7e7414c4234819e1e253415680a0f73fb12eed29a9550aaeb2c`:
195 certain/strong KEEPs added, 2 merged into neighbouring pins, and 62 leaning
KEEPs held.

## Known state

- `serves_relative.py` reads a pre-protocol `zion_ctx.json` that no longer
  exists. It is **superseded by `dossier.py`**, which runs the same gate with
  polygon-edge distances. Kept because it is the clearest single statement of the
  serves rule.
- `padjudicate.py` is likewise superseded by `dossier.py`.
- `make_ne_review.py` is superseded by `ne_review2.py`, and both by
  `judge_review_sheet.py`, which works for any area from its packets.
- `merge_ne.py` is superseded by `merge_drafts.py` (any area, any store).
