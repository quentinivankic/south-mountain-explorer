# Parking trust resolver v3

This is the executable contract for `resolve_trust.py`. The resolver turns a
complete primary draft plus blind challenger/arbiter outputs into a
provenance-preserving canonical draft. It never writes a verdict store,
sidecar, geom, pool, workflow, or live object.

## Commands

```bash
RUN=$(python3 tools/resolve_trust.py prepare "$SLUG" --tmp "$PADJ_TMP" \
  --primary-model primary-id:family-a \
  --challenger-model challenger-id:family-b \
  --arbiter-model arbiter-id:family-c)
python3 tools/resolve_trust.py status --run "$RUN"
python3 tools/resolve_trust.py apply --run "$RUN" --chunk 0       # plan only
python3 tools/resolve_trust.py apply --run "$RUN" --chunk 0 --apply
```

Model IDs and families must describe the actual underlying models. Changing a
role name or prompt does not make the same model family independent.

## Prepare

`prepare` requires complete canonical drafts, matching packet/checkpoint files,
no live continuation, all Z1/Z2/Z3 bytes, and a valid current shadow route. It
writes only an ignored deterministic run directory:

```text
work/trust-resolver/<area>/<run-id>/
  prepare.json
  source/packets.json             # immutable pre-apply packet source bytes
  source/draft-NN.json            # immutable pre-apply route replay bytes
  source/checkpoint-NN.json       # immutable prepared checkpoint bytes
  packets/fid-NNNN.json          # rendered with frozen run-local tile paths
  tiles/<sha256>.png             # host-captured once; never reread from source
  evidence/catalog.json         # host-assigned IDs and frozen provenance
  evidence/<sha256>.*           # retained source/manifest/metadata bytes
  prompts/fid-NNNN.challenger.txt
  prompts/fid-NNNN.arbiter.txt
  inbox/                         # agent-owned outputs until apply
  output-seal.json               # whole-run immutable output hash map
  sealed-inbox/                  # host-owned bytes used by apply/recovery
  backups/                       # populated only by --apply
  staged/                        # populated only by --apply
  transactions/                  # live recovery journal
  receipts/                      # committed apply receipt
  Archive/transactions/          # completed journals; never deleted
```

The v3 run ID binds the absolute source and run-root directories; packet, draft,
checkpoint, primary-decision, and decision-vector content; every model config;
every fid/chunk/row/route; static assignment fields; the host evidence catalog;
all role-prompt template hashes; and the exact byte hashes of `judge_protocol.md` and `judge_lessons.md`.
`prepare.source.source_generation` is a closed portable object with exactly
`manifest_path`, `manifest_sha256`, and `artifact_sha256`. `manifest_path` is
exactly `<area>_generation.json`; `artifact_sha256` contains exactly lowercase
SHA-256 values for `dossier`, `serves`, `context`, and `walk`. The identical
binding appears in every packet, current checkpoint, and challenger/arbiter
assignment, and therefore rotates the decision fingerprint, checkpoint, run ID,
and assignment IDs even when projected facility fields are unchanged.
`prepare.json` uses closed schemas for models, sources, items, and assignments;
fids and chunk/row coordinates are unique and complete. The source identity uses
the immutable generation capture's dossier document and exact dossier hash,
binds `_pub.txt` bytes, and normalizes publication fields for every fid:
complete OSM aliases, lat/lon, rings, and name. It never reopens the dossier
outside that capture. Its parent directory must be named by the run ID. Area
slugs are separator-free. Prepare acquires dossier-inventory `LOCK_SH` before
the canonical area `LOCK_EX` and retains both from generation/packet capture
through every run-artifact write and completed-run reload.
`prepare.json` is strict duplicate-free JSON and its exact bytes must equal the
canonical serialization; whitespace-only and duplicate-key rewrites fail
before review, authority, or publication. Canonical
resolver/authority/publication target installation opens and hashes source bytes
once, copies them into a fresh unpredictable `O_EXCL|O_NOFOLLOW` single-link
inode in the trusted target directory, fsyncs and verifies it, renames that
private entry, then verifies the installed inode and bytes. Shared
`trusted_filesystem.py` primitives require target/lock parents owned by the
current euid with no group/world write and trivial descriptor-bound ACLs;
unsupported or failed ACL inspection and inherited/extended ACLs fail closed.
Lock inodes must be regular, single-link, mode 0600, with repeated fd↔entry
checks. Canonical legacy 0644 locks are tightened
in place without inode rotation. Portable POSIX cannot protect a private name
from a malicious same-UID peer inside that trusted directory; every such
process must obey the canonical lock. Loading a run re-reads every frozen packet, tile,
and rendered prompt and recomputes all derived assignments. It also requires
the parsed frozen checkpoint to equal `source.drafts[].checkpoint_value`,
validates that value against the bound chunk vectors, and compares each
run-local per-item packet payload with the corresponding packet in frozen
`source/packets.json`. Packet JSON, judge set, draft, and checkpoint are each read once; dossier publication fields come
only from the manifest-verified immutable quartet capture, whose exact artifact
hash vector is compared with every packet/checkpoint/prepare boundary. Parsing,
normalization, and source hashes share those immutable buffers. Before any
checkpoint/replay tile access, source PNGs are opened beneath the canonical
ladder descriptor with `O_NOFOLLOW`; pre/post fd and directory-entry
(device/inode/mode/link/size/mtime/ctime) signatures must remain identical
around the single content read. External evidence manifest, artifact, and
metadata files use the same stable one-read capture. Prepare materializes only
captured buffers, fully reloads/validates the completed run, then returns a
dispatchable path. It writes `tiles/<sha256>.png` and points frozen
packets only at run-local bytes; all later routing/evaluation/proof uses
the snapshot. Existing artifacts
must be byte-identical; prepare never overwrites drift. New live prepare,
status, apply, terminal publication, and human-authority operations require
source-generation-bound prepare v3. Unbound prepare v2 remains accepted only
inside historical publication/review proof replay; it cannot authorize new
live work. Already-persisted resolver-v1 rows retain their separate replay
contract.

Each prompt points only at its frozen packet and imagery. It explicitly forbids
reading primary/other decisions and states the bound hashes of both normative
judge documents. The preparer does not create a network sandbox: orchestration
must assign only the generated prompt/run-local files and disable browser and
network tools. Packet tags may contain URL strings and the evidence catalog
retains the host's HTTPS audit locator; neither grants fetch permission. The
host binds model identity and exact prompt, packet,
normative document, and assignment hashes in `prepare.json` and each v2
envelope.

## Agent output

An agent writes only its assigned inbox file:

```json
{
  "assignment_id": "64 lowercase hex",
  "decision": {
    "fid": 1,
    "area": "area-slug",
    "osm": ["way/1"],
    "verdict": "KEEP",
    "prior": "surveyed",
    "exists": {"call": "yes", "evidence": "specific evidence"},
    "public": {"call": "yes", "evidence": "specific evidence"},
    "serves": {"call": "yes", "evidence": "specific evidence"},
    "frames_used": ["z1", "z2", "z3"],
    "tags_cited": {},
    "confidence": "strong",
    "resolve_hint": null
  },
  "external_evidence": []
}
```

An arbiter reads the host-written `evidence/catalog.json` and names assigned
evidence without supplying its own path or provenance:

```json
{
  "id": "stable-slug",
  "supports": [
    {"axis": "public", "call": "yes", "claim": "specific source fact"}
  ]
}
```

Before dispatch, the host validates one area evidence manifest and copies each
source artifact, source manifest, and source metadata file to content-addressed
run paths. The run/catalog/assignment/envelope bind those hashes plus the HTTPS
source locator and UTC retrieval/update timestamps. `source_updated_at` may be
`null` only when the host source exposes no update timestamp; that uncertainty
remains durable. For v2 challenger and arbiter envelopes, bracketed external
references are parsed case-insensitively from each of the three axis evidence
strings and normalized to `[external:<canonical-id>]`. Prefix/ID case variants
remain replay-compatible, but whitespace, an empty/noncanonical ID, bad
characters, and unclosed external-like forms are invalid. Cited `(axis, id)`
pairs must equal declared support pairs exactly: each support call must equal
that axis call; each source may declare an axis once; every declaration must be
cited once in that same axis; and undeclared, wrong-axis, duplicate-normalized,
or outside-axis tokens fail closed. Challengers declare no external evidence
and therefore may cite none; an arbiter with no declarations may cite none.
Agent-authored v2 challenger/arbiter decision strings and support claims may not
contain `http://`, `https://`, or `www.` URL-like locators. The host-owned
canonical `source_locator` remains valid audit provenance and is never agent
authority. An unassigned ID, changed frozen byte, malformed chronology, or
empty/duplicate support is also invalid. Valid external evidence is audit
context only; under v2 it cannot authorize a same-family outcome. Persisted v1
envelopes retain their frozen legacy citation/text behavior.

## Resolution policy

- Explicit human authority is preserved and never machine-overwritten.
- A current-schema clean primary goes to a blind challenger.
- Distinct-ID, distinct-family, confident binary agreement on verdict plus all
  three axis calls resolves autonomously.
- Distinct-family disagreement, same-family agreement, leaning/REVIEW,
  fallback, no-route, and coverage-gap routes may require an arbiter.
- A v2 same-family parent disagreement or nonbinary direct primary becomes a
  human exception immediately; another same-family role cannot supply
  independence.
- V2 arbitration requires exact agreement on verdict plus EXISTS/PUBLIC/SERVES
  calls with a confident parent, and the arbiter's model ID and family must
  differ from **every** available parent. Any duplicate model ID or family among
  participating v2 votes is a human exception, including same-family parents
  plus an otherwise independent arbiter.
- Every remaining v2 same-family result is a human exception, including exact
  agreement backed by novel structured evidence. External evidence can improve
  the human review packet but cannot establish independent authority.
- A REVIEW arbiter, parent/arbiter axis mismatch, incomplete independence, or
  unresolved lot-level right becomes the human-exception path and blocks the
  whole run.
- Persisted v1 resolutions continue to replay under their frozen legacy
  hash-plus-citation policy; all new runs use v2.
- `AUTONOMOUS_REFRESH` blocks until the ordinary primary checkpoint flow
  produces a fresh current-schema primary.
- Direct model automation is governed separately by shadow report/policy v2.
  Promotion authority is scoped only to the current row's exact canonical
  `{policy_id, model_family, model_id, prompt_sha256}` identity; decision and
  packet hashes bind each reference record but are not class identity. Wilson
  error, reviewed-area breadth, and reviewer-family breadth are computed within
  that exact identity. Aggregate verdict metrics are diagnostic and expose no
  authorizing `promotion_ready` bit. A ready model/family cannot transfer
  authority to another model, family, or prompt.
- Canonical reviewer IDs are corpus-wide identities. If locally valid
  promotion-grade records assign one reviewer ID to multiple families, every
  such record loses promotion credit symmetrically and the sorted conflict is
  reported; historical review/error diagnostics remain intact. A ledger with
  no `blind_reviews` remains valid and nonpromoting. Missing or malformed exact
  primary identity routes to a blind challenge rather than direct promotion.
- Current primary `judge_provenance` does not authenticate provider, build, or
  normative-document hashes. Shadow v2 never fills those dimensions with an
  `unknown`/wildcard value. A provider, build, or normative-policy change must
  rotate authenticated `model_id` or `prompt_sha256` before prior promotion
  credit can apply.

Same-model agreement is stability evidence, not independent calibration.
Autonomous challenger/arbiter outcomes never enter `blind_reviews` or human
Wilson denominators.

## Persisted row

The selected complete decision becomes the top-level row, including selected
axis calls/evidence and selected `judge_provenance`. `trust_resolution` retains:

- policy/run/transaction IDs and a hash of the complete wrapper;
- original shadow route and result basis;
- selected role and decision hash;
- full immutable primary, challenger, and optional arbiter envelopes, each with
  its own complete-envelope hash;
- canonical model ID/family correlation summary.

`original_judge_projection()` returns the nested primary, so original checkpoint
hashes, calibration classes, and immutable KEEP samples do not change.
`machine_decision_projection()` returns the selected pre-human row. Already
persisted legacy `--set` overrides remain replayable, but the unbound `--set`
CLI is disabled. Current human authority starts by rendering immutable evidence:

```bash
python3 tools/judge_review_sheet.py "$SLUG" --authority-run "$RUN" --sample 20
python3 tools/merge_drafts.py STORE "$SLUG" --confirm FID=VERDICT \
  --authority-run "$RUN" --review-receipt RECEIPT \
  --reviewer ID --note TEXT --judged YYYY-MM-DD
```

The renderer validates the prepare and uses only its frozen run. For every item
it embeds the complete packet payload except replaced tile paths, the full
normalized publication facility as canonical JSON, the exact primary decision,
and content-addressed PNG bytes. Under one area `LOCK_EX` it installs
deterministic owner-only, single-link `review.html` and self-hashed
`review-receipt.json` under `.review-artifacts/<area>/<run-id>/`; arbitrary
output paths and live map/file evidence are forbidden. The receipt binds every
prepared item/source hash and the exact sheet bytes. A sheet-only crash grants
no authority; exact retry completes the immutable pair without rotating an
already-correct inode. `calibration.py add --reviewed KEEP=sample` requires the
canonical absolute `--review-receipt`; the mutually exclusive
`--legacy-sample-manifest` flag exists only for explicit replay of the obsolete
manifest.

New flips use `--decide` and outer override v3; same-verdict affirmations use
confirmation v2. Both require the exact canonical `--review-receipt`, preserve
the complete original decision, and bind source/effective decision/evidence,
packet/run, reviewer/date/note, plus review receipt/sheet/item hashes. Legacy
confirmation v1, override v2, and authority receipt v1 remain replayable but
cannot be newly emitted or published as current authority.

Before either command changes canonical state, pure planner helpers populate
caller-owned staging. The CLI command is a mutating three-target transaction.
It acquires the dossier-inventory shared lock with the other globally sorted
resources before the area lock, captures the current generation once, and
retains both locks through commit/recovery. It independently reconstructs the
exact human request and reviewed item from the frozen run, validates schema,
overlap, `_pub.txt`, packets/tiles, the complete review chain, and whole-area
packet/draft/checkpoint/source-generation/public-set freshness, stages exact
after-images/backups, and writes a durable area journal. It installs receipt →
draft → checkpoint, changing only `draft_sha256`, verifies exact final bytes,
and archives the journal. Authority receipt v2 lives under
`.human-authority/<area>/<sha>.json` and binds the full source and review chain.

A failed preflight leaves canonical receipt/draft/checkpoint unchanged. A live
authority journal blocks merge, judge packet generation, calibration/review
sheet writes, resolver prepare/status/apply, and work replay. It is not
self-authorizing: recovery requires the complete original `--decide` or
`--confirm` invocation, including every axis/evidence field, reviewer, note,
authority run, canonical review receipt, and explicit `--judged YYYY-MM-DD`.
That invocation reconstructs exact bytes without trusting journal fields or
partial live state. Any changed request/run/review, malformed artifact, or
target matching neither trusted image fails before replacement. Valid
confirmations route as preserved human authority and are stripped from
machine/primary projections. Calibration keeps primary-vs-human flips and
machine-vs-human exceptions, but confirmations do not create a review
denominator or synthetic flip.

## Atomic apply and recovery

Only `apply --apply` mutates canonical state, exactly one chunk per invocation.
Before any new plan, preservation receipt, or transaction, the **entire run**
must be READY; a PENDING, BLOCKED, or recovery-required sibling prevents every
other chunk from moving. The sole exception is completion of an already-durable,
exactly reconstructable journal/receipt recovery. On the first real `--apply`,
the host copies every present/missing role output into `sealed-inbox/` and writes
a hash-bound `output-seal.json` for the whole READY run. The seal also binds the
all-items resolution/count snapshot. All later plans, receipts, status
reconstruction, and recovery ignore mutable live inbox bytes. A live journal
without that exact seal, with a changed resolution snapshot, or with any
unresolved sealed sibling is recovery-only and cannot write canonical state.
Before any canonical write the resolver validates every source hash, assignment,
decision, provenance envelope, consensus result, resulting row, immutable
primary hash vector, seal, and checkpoint plan.

Apply then:

1. writes the whole-run output seal before deriving the canonical plan;
2. writes a durable backup and staged replacement under the ignored run;
3. writes/fsyncs a PREPARED transaction journal bound to the seal;
4. rechecks the canonical draft compare-and-swap hash;
5. atomically replaces that one draft;
6. writes the generation-bound checkpoint v2 with unchanged
   `source_generation` and `judge_row_sha256`, changed
   `resolution_row_sha256`, and the new draft hash;
7. writes a receipt and archives (never deletes) the journal.

A retry with a live journal re-derives every path, decision, transaction hash,
replacement byte, and checkpoint value from the bound prepare manifest and
sealed output bytes; mutable live inbox/journal/receipt fields are never
authority. It
accepts only the exact before or after draft hash. Before finishes the
replacement; after finishes checkpoint/receipt archival. A third hash fails
closed. `judge_packets` captures each agent-owned continuation once through a
stable `O_NOFOLLOW` descriptor, validates and builds all draft/checkpoint
proposals from that image before writing and archives the same bytes under their
full hash. Retirement moves—not unlinks—the live entry into owner-only
quarantine; a newer race winner is preserved at a reported recovery path. The
human review artifact writer holds the area lock exclusively through sheet and
receipt installation, so sample variants cannot interleave. Judge packet,
resolver prepare/status/apply, work replay, terminal publication, and human
authority all acquire the dossier-inventory resource before any area lock and
use `capture_generation_locked` without nested reacquisition. Judge and prepare
retain the inventory lease through every derived write/reload; apply and
publication retain it through every authority mutation. Writers of
`calibration.json` or a verdict store include that resource in the same globally
sorted lock set before sorted area locks; this fixed global→area order prevents
cross-area lost updates and deadlock.
`merge_drafts` registers each opened resource/area handle in one outer ownership
scope; normal return, unexpected post-lock exceptions, and partial open/`flock`
failures all drain every acquired handle while preserving the original failure.
Both draft and checkpoint CAS checks occur while the area lock is held. An applied receipt is accepted only after status reconstructs the
same canonical plan from prepare+sealed outputs+backup and exact-compares its
schema, persisted rows, after hash, and checkpoint hash.

A live journal always dominates a receipt and forces `RECOVERY_REQUIRED` until
validated retry archives it. A preserved-only chunk writes a durable no-op
receipt, so later status reports the chunk terminal rather than READY again.

## Status and separation from publishing

`status` is read-only: exit 0 for READY/APPLIED, 3 for PENDING, and 2 for
BLOCKED/RECOVERY_REQUIRED. `apply` without `--apply` returns a plan and changes
nothing.

After every chunk is APPLIED (or durably PRESERVED), the explicit single-area
`merge_drafts.py --write --resolver-run <run>` transaction, successor-root
review/promotion, sidecar build, sweep, pool build, git review, and R2 sync
remain separate. The store transaction does not authorize or perform root
promotion, and no second store publication may start while the live image is
ahead of the pinned root. Every store write requires
the named run to target the exact area/work directory/fid set, current complete
source generation, and exact bound judge-set bytes; report only terminal
applied/preserved chunks; and leave every row with current machine or
receipt-bound human authority. Merge
uses the run's normalized OSM aliases/location/rings/name rather than rereading
mutable publication values. A PENDING/BLOCKED run therefore cannot publish its
untouched primary rows or add an unreviewed dossier alias. Multi-area writes are
rejected; separate one-area invocations make each later area see the prior store
mutation.

For every current machine resolution, review-bound human override/confirmation,
store merge requires the current full packet and readable Z1/Z2/Z3 bytes. Machine-resolved chunks also revalidate the checkpoint's
primary and machine vectors. Store overlap validation expands the complete
transitive alias-connected component and checks every distinct holder, including
same-area records; verdict or authority-provenance differences block. Drafts may not stamp `src`, `judged`, geometry, or other host-owned
persistence fields. Every existing holder participates, including same-area
aliases. An agreeing direct-human `user*` source already in the store outranks
automated holders only when complete effective decisions agree, and all known
aliases are rewritten to the selected record with host-owned geometry rebuilt
from the bound terminal source. Sidecar compilation and replay both propagate
aliases across the full transitive component. Canonical OSM store keys must
already occur in the row's attested alias vector; only explicit legacy fid keys
are excluded. `judge_provenance` and exact emitted source/date defaults are part
of fold equality. Builder and replay hold one sorted shared lock set over the
code-pinned publication root, all configured
store/proof-registry-state/floor/journal resources, the baseline, dossier
inventory, and any output through final sidecar check/write. Any live journal
blocks. Pin-first exact-image validation precedes semantic proof/floor/baseline
validation. Proofless rows must exactly match the separately self-pinned
schema-v2 1,273-row/11-dossier `proofless-source-baseline-v1.json`, whose exact
file bytes and dossier inventory are also rooted; current keys cannot downgrade
to that baseline through publication. Dossier placement uses only no-follow
captured bytes.

Each newly published current row references publication proof v3 in the
canonical `<store>_publication_proofs.json` registry. The proof contains the
actual generation-bound prepare, output seal, byte-exact base64 sealed outputs,
rendered prompts, and frozen template/normative bytes; chunk receipts with a
strict complete per-fid terminal vector; packet bindings; exact final
rows/preserved primaries; terminal draft/checkpoint snapshots; and every current
human authority receipt. Replay requires exact source-generation equality among
the prepare, every packet binding, and every final checkpoint. It does not read
the original work manifest or quartet, so a rooted proof remains self-contained
after those work files are unavailable. Historical proof v1 requires unbound prepare v2
with authority receipt v1 and no review bundle. Historical proof v2 requires
unbound prepare v2 with authority receipt v2 and its review bundle. Proof v3
requires generation-bound prepare v3, authority receipt v2, and generation-bound
nested review/source prepares; changing a v3 proof's version cannot downgrade
it.
For human rows it also embeds a deduplicated exact review bundle: review receipt,
sheet bytes, prepare bytes, and the closed frozen source-artifact map needed to
reconstruct both. Canonical replay independently derives assignment/static/prompt hashes,
reconstructs persisted envelopes and exact rendered prompts from sealed outputs
plus frozen template/rule bytes, recomputes the whole-run
READY snapshot, requires globally exact packet/final-row/human sets and strict
chunk receipt schemas/vectors, exact-compares final and preserved-primary rows,
validates terminal draft/checkpoint byte hashes and reconstructs the full
checkpoint manifest—including completed, draft, primary, resolution, and
unchanged source fields—then replays v2 consensus and human primary/source
bindings before crediting
authority. A coherently rehashed row or prepare assignment with the original
sealed output fails.

`publication-trust-root-v1.json` is strict canonical schema v1 with no
self-hash. Its exact file SHA-256 is pinned by the literal
`PUBLICATION_TRUST_ROOT_SHA256` in `build-parking-verdicts.py`. It binds its
generation and parent; every configured store identity and exact store byte
hash; each derived proof-registry state/hash, where `null` requires absence;
each self-hashed publication-floor byte image; the exact schema-v2 baseline
file; and the sorted exact dossier inventory and hashes. The generated sidecar
is deliberately outside the root because it is deterministic derived output.
Replay/build hold root `LOCK_SH`. A rooted terminal publication holds root
`LOCK_EX`, target store/proof/floor/journal resources exclusively, sibling
publication resources shared, and the shared baseline/dossier resources in the
same globally sorted lock set.

Publication pre-serializes exact proof, append-only floor, store, and successor
candidate after-images; stages and backs up the canonical triple; and writes a
terminal-run-bound journal containing parent/candidate identity. The only valid
install prefix is proof → floor → store → candidate. The floor preserves each
admitted key's first authority/attestation/proof tuple. The candidate increments
generation, names the pinned parent hash, changes only the target triple, and is
non-authoritative. Every target is exact-byte verified before journal archival.
Recovery reruns the complete original single-area `merge_drafts.py STORE SLUG
--resolver-run RUN --judged YYYY-MM-DD --write` command. The current terminal
invocation must independently reconstruct the same root-anchored before-image,
plan, after-images, and candidate; a different run/date/image, non-prefix
canonical state, malformed stage/backup/candidate, or mismatched archive fails
closed without selecting authority from the journal.

The transaction never writes the tracked root or code pin. After completion,
replay/build and every second publication remain blocked against the old root
until a separate reviewed Git change installs the candidate's exact bytes as
the tracked root and updates the literal pin and pin assertions together.
Publication proof/attestation v1 and legacy human receipt schemas remain
replay-only; new publication emits v2 and will not upgrade unreviewed legacy
human authority implicitly. Legacy unversioned overrides and persisted
resolver-v1 rows otherwise retain their frozen behavior. The resolver cannot
invoke publication or root promotion.

## Recovery command matrix

| Live transaction | Only supported recovery |
|---|---|
| Judge continuation | Rerun the identical `judge_packets.py SLUG ...` invocation; classification and commit use one captured continuation image. |
| Resolver chunk | Rerun `resolve_trust.py apply --run RUN --chunk N --apply`. |
| Human authority | Rerun the complete original `merge_drafts.py ... --confirm/--decide ... --authority-run RUN --review-receipt RECEIPT --judged DATE` invocation. |
| Publication | Rerun the complete original one-area `merge_drafts.py STORE SLUG --resolver-run RUN --judged DATE --write` invocation. |

Read-only status/plan calls never finish a writer's transaction. Do not edit,
remove, or hand-build journals, stages, backups, candidates, or archives.
Publication recovery finishes the exact transaction but never promotes its
candidate; replay/build and another publication remain blocked until separate
reviewed Git promotion updates the tracked root and literal pin.
