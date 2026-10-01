# Parking adjudication — tools and data (task #53)

Per-lot KEEP/DROP verdicts for parking, from OSM tags plus aerial imagery.
**Start with `docs/parking-adjudication-handoff.md` at the repo root** — it is the
full brief. This file only covers running the code in this directory.

Graduated here from `/mnt/raid/trekdex/parking-adjud/` on 2026-09-13 so the logic
and the verdicts travel with the repo instead of living on one machine.

## Layout

```
scripts/parking-adjud/
  tools/     pipeline tools, two shell drivers, and judge/resolver documents
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

Preparation schema v2 binds the absolute run root; complete
source/checkpoint vectors; all model configs; every fid/chunk/row/route; the
full primary decision; role assignments; the host-frozen evidence catalog; and
the exact bytes of all role prompts plus `judge_protocol.md` and
`judge_lessons.md`. Prepare holds the canonical area `LOCK_EX` from source
capture through completed-run reload. `prepare.json` must be strict
duplicate-free JSON whose exact bytes equal the canonical serialization;
whitespace-only or duplicate-key rewrites fail before review, authority, or
publication. The host copies every assigned evidence artifact, source
manifest, and source metadata file into content-addressed paths under the run.
A loaded run must live in its hash-named root and every packet, prompt, evidence,
manifest, and metadata artifact is re-read before it is authority. Old
preparation manifests must be regenerated; persisted resolver-v1 rows still
replay under their frozen v1 policy. Area slugs are separator-free. Canonical
resolver/authority/publication target installation opens and hashes the source
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
The host persists each source's HTTPS locator, UTC
retrieval/update timestamps, content/manifest/metadata hashes, content-addressed
paths, structured support, and axis-local `[external:id]` citation. An
unassigned ID, duplicate support, mismatched call, wrong-axis citation,
impossible timestamp, or changed frozen byte is invalid. This provenance
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

The sheet embeds each exact primary decision, the complete frozen packet payload
except replaced tile paths, the full normalized publication facility as
canonical JSON, and the frozen run-local PNG bytes. Its self-hashed receipt
binds the complete prepared item/source vector and exact sheet. There is no
arbitrary `--out`. Artifact creation holds the area lock exclusively through
both immutable owner-only single-link installs, so different sample requests
cannot interleave a mixed pair. A sheet-only crash is non-authoritative; exact
retry completes the pair without rotating an already-correct inode. For
`calibration.py add --reviewed KEEP=sample`, `--review-receipt` must be the
canonical absolute receipt path. The mutually exclusive
`--legacy-sample-manifest` path exists only for explicit replay of the obsolete
manifest.
The unbound `--set` CLI is disabled; its helper remains solely to replay
already-persisted legacy overrides. New flips use one `--decide FID=VERDICT
--axis AXIS=CALL --axis-evidence AXIS=TEXT --note ... --reviewer ID
--authority-run PATH --review-receipt PATH --judged YYYY-MM-DD` invocation so
the exact reviewed item, full original decision, and coherent effective axes
are bound. Use one `--confirm FID=VERDICT --note ... --reviewer ID
--authority-run PATH --review-receipt PATH --judged YYYY-MM-DD` invocation when
the human explicitly affirms the same binary call. The helpers are pure
planners; the CLI
operations mutate one receipt/draft/checkpoint transaction. Under the global
store lock and area lock, the CLI independently reconstructs the request from
the frozen authority run, validates packet/schema/overlap and whole-area source
state, stages exact backups/after-images, writes a durable journal, then installs
receipt → draft → checkpoint. Only `draft_sha256` changes in the checkpoint.
The authority receipt binds prepare bytes, primary envelope/assignment, packet,
source run/path, reviewer/date/note, effective decision/evidence, and the exact
review receipt/sheet/item hashes. New confirmation v2 and override v3 authority
therefore cannot be created or recovered with an omitted, stale, altered,
different-run, or wrong-fid review artifact. A failed preflight leaves canonical
targets unchanged. A crash leaves a live area journal that blocks merge, packet generation,
calibration/review-sheet writes, resolver work, and replay. Recovery has no
generic command: rerun the complete original `--decide` or `--confirm` command,
including the same `--review-receipt` and explicit `--judged` date. The current invocation must
independently reconstruct the byte-identical plan; any changed request/run or
unknown target/stage/backup state fails without advancing canonical files.

Store merge reopens the receipt's source run and the real packet/Z1/Z2/Z3 bytes
for every v2 authority row. It expands each complete transitive alias-connected component across store and
dossier IDs before comparing full decision/authority signatures. Every holder,
including same-area aliases, participates; conflicts block. An agreeing `user*`
record already in the store takes precedence over incoming machine wrappers on
the real terminal-run path, while location/rings/name and the complete alias set
are rebuilt from the run-bound publication source. Published v2 rows reference a separate canonical
`<store>_publication_proofs.json` entry rather than trusting a row to attest
itself. That proof contains the actual prepare, output seal, sealed outputs,
chunk receipts, packet bindings, and any human receipt/source-prepare chain;
canonical replay validates the full external chain before crediting authority.

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
machine or receipt-bound human authority. Merge writes a row attestation plus a
separate canonical `<store>_publication_proofs.json` entry containing the actual
prepare, whole-run seal, byte-exact base64 sealed outputs/rendered prompts and
the frozen prompt-template/normative-rule bytes used to reconstruct them,
chunk receipts with complete per-fid terminal vectors, packet bindings, exact
final decision/authority rows, and each current human receipt plus its exact
review receipt, sheet bytes, and frozen review-source bundle. New publication
proof/attestation v2 independently reconstructs that review evidence; legacy
v1 proof remains replay-only. Canonical replay
independently derives assignment IDs/static fields/prompt hashes, reconstructs
every persisted envelope from sealed output, recomputes the all-items READY
snapshot, requires each fid in one strict complete chunk terminal vector,
exact-compares final rows and preserved primary decisions, reconstructs the
complete checkpoint manifest from its prepared base plus terminal rows, verifies
terminal draft/checkpoint byte hashes, replays machine consensus, and validates
human primary/terminal membership before preserving v2 authority.

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

Publication is one journaled transaction with one valid install prefix: proof
→ floor → store → non-authoritative successor candidate. The floor preserves
every admitted key's first authority, attestation, and proof identity. Every
stage is exact-byte verified before journal archival. A crash after proof,
floor, store, candidate, or archive installation recovers only by rerunning the
exact original single-area command with the same terminal run and explicit
`--judged YYYY-MM-DD`; different inputs or malformed
stages/backups/candidates/archives fail closed. The tracked root and literal pin
never change automatically. After publication, review the candidate named by
the archived journal, replace the tracked root with those exact bytes, and
update the literal pin plus pin assertions in one reviewed Git change. Until
that promotion, replay/build and every second publication reject the live image
against the old root. A PENDING/BLOCKED run cannot publish primary rows around
the resolver.

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

**Operational/manual freeze — not a code bypass:** do not publish
`bear-creek-lake-park-co` in this task. Its available resolver run and four
historical human decisions predate the current prepare/review-receipt authority
schemas and cannot be newly published; regenerate/re-evaluate all 50 rows,
rebind accepted decisions through a current frozen review receipt, close every
human exception, pass the hostile gate, and obtain an explicit thaw before
store merge, sidecar, geom, pool, R2, or live publication.

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
are also rooted. Current authority cannot downgrade back to that baseline.
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
single-line escaped.

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

## Known state

- `serves_relative.py` reads a pre-protocol `zion_ctx.json` that no longer
  exists. It is **superseded by `dossier.py`**, which runs the same gate with
  polygon-edge distances. Kept because it is the clearest single statement of the
  serves rule.
- `padjudicate.py` is likewise superseded by `dossier.py`.
- `make_ne_review.py` is superseded by `ne_review2.py`, and both by
  `judge_review_sheet.py`, which works for any area from its packets.
- `merge_ne.py` is superseded by `merge_drafts.py` (any area, any store).
