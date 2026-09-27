# Parking trust resolver v1

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
  packets/fid-NNNN.json
  prompts/fid-NNNN.challenger.txt
  prompts/fid-NNNN.arbiter.txt
  inbox/                         # agent-owned outputs
  backups/                       # populated only by --apply
  staged/                        # populated only by --apply
  transactions/                  # live recovery journal
  receipts/                      # committed apply receipt
  Archive/transactions/          # completed journals; never deleted
```

The run ID binds the absolute source directory, source packet/draft/checkpoint
hashes, policy, model configs, and every fid route. Existing run artifacts must
be byte-identical; prepare never overwrites drift.

Each prompt points only at its frozen packet and imagery. It explicitly forbids
reading primary/other decisions. The host binds model identity and exact prompt,
packet, and assignment hashes in `prepare.json`.

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

An arbiter that adds evidence uses
`{"id":"stable-slug","path":"/absolute/frozen/file","sha256":"..."}`.
The resolver reads and hashes those exact bytes, then persists only canonical
stable ID + hash. The selected axis evidence must cite each source exactly as
`[external:stable-slug]`. A claimed hash without matching bytes, an ID alias,
or any packet/tile/previous-evidence hash is invalid.

## Resolution policy

- Explicit human authority is preserved and never machine-overwritten.
- A current-schema clean primary goes to a blind challenger.
- Distinct-ID, distinct-family, confident binary agreement resolves
  autonomously.
- Disagreement, same-family agreement, leaning/REVIEW, fallback, no-route, and
  coverage-gap routes require an arbiter.
- An arbiter resolves when it agrees with a confident decision from a distinct
  model family.
- A correlated arbiter can resolve only by adding external evidence whose hash
  is absent from every packet/prior envelope and whose stable ID is explicitly
  cited in the selected axis evidence.
- A REVIEW arbiter or correlated result without new evidence becomes the only
  human-exception path and blocks apply.
- `AUTONOMOUS_REFRESH` blocks until the ordinary primary checkpoint flow
  produces a fresh current-schema primary.
- Direct model automation remains governed separately by the shadow engine's
  promotion-grade reference/error/breadth gates.

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
`machine_decision_projection()` returns the selected pre-human row. A later
`merge_drafts.py --set` remains an outer `override.by=human`; calibration keeps
primary-vs-human flips and separately records machine-vs-human exceptions.

## Atomic apply and recovery

Only `apply --apply` mutates canonical state, exactly one chunk per invocation.
Before any write it validates every source hash, assignment, decision,
provenance envelope, consensus result, resulting row, immutable primary hash
vector, and checkpoint plan.

Apply then:

1. writes a durable backup and staged replacement under the ignored run;
2. writes/fsyncs a PREPARED transaction journal;
3. rechecks the canonical draft compare-and-swap hash;
4. atomically replaces that one draft;
5. writes the checkpoint with unchanged `judge_row_sha256`, changed
   `resolution_row_sha256`, and the new draft hash;
6. writes a receipt and archives (never deletes) the journal.

A retry with a live journal re-derives every path, decision, transaction hash,
replacement byte, and checkpoint value from the bound prepare manifest and
validated inbox outputs; mutable journal/receipt fields are never authority. It
accepts only the exact before or after draft hash. Before finishes the
replacement; after finishes checkpoint/receipt archival. A third hash fails
closed. Every resolver/judge run targeting an area shares its canonical area
lock. Writers of `calibration.json` or a verdict store first take a lock derived
from that global resource's absolute path, then the sorted area lock(s); this
fixed global→area order prevents cross-area lost updates and deadlock. Both
draft and checkpoint CAS checks occur while the area lock is held. An applied receipt is accepted only after status reconstructs the
same canonical plan from prepare+inbox+backup and exact-compares its schema,
persisted rows, after hash, and checkpoint hash.

A live journal always dominates a receipt and forces `RECOVERY_REQUIRED` until
validated retry archives it. A preserved-only chunk writes a durable no-op
receipt, so later status reports the chunk terminal rather than READY again.

## Status and separation from publishing

`status` is read-only: exit 0 for READY/APPLIED, 3 for PENDING, and 2 for
BLOCKED/RECOVERY_REQUIRED. `apply` without `--apply` returns a plan and changes
nothing.

After every chunk is APPLIED (or durably PRESERVED), the existing explicit
`merge_drafts.py --write`, sidecar build, sweep, pool build, git review, and R2
sync remain separate. For any machine-resolved row, store merge revalidates the
full packet/tile bytes plus the checkpoint's primary and machine vectors before
writing; self-rehashed provenance cannot bypass it. The resolver cannot invoke
any publishing step.
