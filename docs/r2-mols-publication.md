# Mols Bjerge R2 publication contract

This path publishes only `nationalpark-mols-bjerge-dk`. Both workflows remain manual-only (`workflow_dispatch`). `trailforge-publish.yml` remains `dry_run: true` by default, and its ordinary US state path is unchanged when no Mols release receipt is supplied.

## Signed input gate before publication effects

A Mols publication supplies one canonical base64-encoded `trekdex-mols-release-input-approval-v1` receipt. The receipt is HMAC-verified with the existing root approval verification key before package installation, downloads, generation, commit, or push. It binds this exact tuple:

- extract: `europe/denmark`
- state: `Denmark`
- region code: `dk`
- area ID: `nationalpark-mols-bjerge-dk`
- area name: `Nationalpark Mols Bjerge`
- OSM relation: `7046785`
- bbox: `10.40,56.08,10.90,56.35`
- reviewed root approval, provider-account, provider-endpoint, and inventory-anchor references
- reviewed authoritative object denominator
- exact workflow source commit and tree
- reviewed one-row center: latitude `56.2`, longitude `10.6`

The source binding prevents replay after the workflow code or main tree changes. Before checkout or any secret-bearing step, a non-secret gate constrains every dispatch value to its expected shape. A nonempty receipt requires the exact Mols tuple and canonical Base64 characters; generic US-state values keep their existing defaults and behavior. Every later shell command receives values through `env:` and references quoted shell variables. No `run:` scalar embeds an input, event value, or step output as GitHub expression source.

Only the conditional receipt-verification step receives the HMAC key. It consumes outputs from the non-secret shape gate, not raw dispatch expressions, and requires `dry_run=false`, `no_routes=false`, `touch_report=false`, `elevation=false`, and no read-only pilot selector. Denmark publication without this receipt fails before publication work. Ordinary non-Denmark dispatches with a blank receipt never enter the HMAC step and retain the existing state-wide behavior. Checkout credentials are not persisted; the GitHub token is scoped separately to commit/push and plan-dispatch steps.

The verifier emits safe fields as step outputs. The chained sync dispatch consumes only those verified outputs plus the commit step's exact pushed SHA and tree. No release reference is copied directly from a free-form workflow input. `sync-geom-to-r2.yml` likewise validates every raw dispatch value in a non-secret first step; its credentialed provider step consumes only those validated outputs.

## Exact repository effect

Verified Mols mode materializes a runner-local one-row index directly from the signed receipt and passes that file plus `--only-area nationalpark-mols-bjerge-dk` to the state publisher. The publisher must return that same row with positive trail count and mileage. A guarded merge inserts or replaces only that row in `public/areas/index.json`, without changing or reordering any other row. Generic US mode keeps using the canonical state-wide index and receives no exact-area selector.

After generation, the workflow stages its existing publication roots. The merge, staged-diff, and committed-diff guards consume the receipt SHA-256 emitted by the sole HMAC verifier and recheck exact Mols constants; they do not receive the HMAC key or raw receipt. Commit and push are refused unless the staged file set is exactly:

- `public/areas/geom/nationalpark-mols-bjerge-dk.json`
- `public/areas/silhouettes/nationalpark-mols-bjerge-dk.json`
- `public/areas/index.json`
- `ios/SouthMountainExplorer/Resources/areas-index.json`

The guard parses both baseline and staged indexes in their producer's exact JSON framing. It requires one genuinely added or updated Mols row with the exact state/name/relation identity and byte-semantically identical non-Mols rows in the same order. Deletions, wildcards, prefixes, other areas, other globals, malformed geom/silhouette objects, or any extra staged path fail before commit.

After a normal push, the workflow seals the resulting commit and tree. The chained `sync-geom-to-r2.yml` dispatch is always `plan_only=true`. A separate reviewed manual apply names the exact manifest SHA-256.

## Byte-different R2 scope

The plan run checks out the named commit detached, verifies its tree, obtains the complete reviewed inventory, constructs all producer bytes, and rejects any byte difference outside:

- `nationalpark-mols-bjerge-dk.json`
- `silhouettes/nationalpark-mols-bjerge-dk.json`
- `index.json`
- `trail-search.json`
- `trail-shapes.json`

Byte-identical allowed objects are proven no-ops and omitted only when the complete scoped output is an exact zero-write repeat. Any nonzero scoped plan must contain all five keys in the order above; partial state is recoverable only through its sealed journal and is never reclassified as a smaller fresh plan. `parking.json` is never in the scoped allowlist, so any parking byte difference fails closed. The app index uses exact compact producer framing, and the contract proves every non-Mols row is unchanged.

Before the reviewed manifest is sealed, the plan reads the existing `trail-search.json` and `trail-shapes.json` with `If-Match` against each inventory ETag. Each manifest row binds the exact baseline ETag, byte count, and SHA-256. The semantic receipts then prove that filtering the candidate back to non-Mols identities yields the baseline records in the same order with identical content, and that the candidate Mols records are exactly the deterministic search rows and thumbnail shapes derived from every trail in the staged Mols geometry. This permits either a first-time `ADD` from zero baseline Mols records or an exact `REPLACE` of existing Mols records. A modified, removed, added, or reordered non-Mols record, an extra Mols record, malformed producer framing, a stale ETag, or an unqualified Mols trail rejects the plan before approval. Apply preflight downloads the baselines again under the same ETags and must reproduce the reviewed semantic receipts before any write.

Forward order is geom, silhouette, index, trail-search, then trail-shapes. The two absent Mols objects use atomic `If-None-Match: *`. All three globals use atomic `If-Match` against their exact reviewed ETags. Every successful write requires immediate HEAD plus conditional GET under the lock to prove the returned ETag and exact byte count/SHA-256.

## Provider behavior basis and pinned client

No conformance bucket or credential is created. The implementation uses the behavior publicly documented by Cloudflare:

- [R2 S3 API compatibility](https://developers.cloudflare.com/r2/api/s3/api/) documents `HeadObject`, conditional `GetObject`, `PutObject`, and `DeleteObject`, and explicitly states that `PutObject` conditional headers are evaluated atomically with the commit.
- [R2 S3 extensions](https://developers.cloudflare.com/r2/api/s3/extensions/) documents atomic `If-Match` and `If-None-Match` for `PutObject`.
- [R2 consistency model](https://developers.cloudflare.com/r2/reference/consistency/) documents strongly consistent object operations and conditional writes.

R2 bucket versioning is not used. No request or receipt depends on an object-version field, and GET/DELETE requests contain no version selector.

The sync workflow downloads AWS CLI `2.39.1` from the exact versioned AWS URL and verifies archive SHA-256 `5b6c10e0a8ccdcf30c84bd643fea511f103d257a4787a7cef878691d63169804` before a runner-local installation. It rechecks `aws --version` in the credentialed step. Every scoped receipt records this version and archive hash.

A forward PUT counts as successful only when the provider response contains a nonempty ETag and immediate HEAD plus conditional GET under the lock proves the exact ETag, byte count, and SHA-256. Extra response members are ignored. Missing ETag, malformed JSON, 404, 5xx, 412, post-write mismatch, or timeout never becomes success. A 412 is a proven no-effect refusal; every ambiguous outcome remains pending for reconciliation.

## One shared R2 writer lock

Every workflow holding R2 write credentials uses concurrency group `sync-geom-to-r2` for its entire job:

- `.github/workflows/sync-geom-to-r2.yml`, invoking `scripts/stage-changed-r2.py`
- `.github/workflows/cleanup-r2-orphans.yml`, invoking `scripts/cleanup-r2-orphans.py`

The only production scripts containing direct R2 put/copy/delete calls are those two scripts. The cleanup adapter has copy authority but keeps real source removal disabled pending its own separately approved documented primitive; the accepted plain-delete race is not generalized beyond the two Mols rollback keys. The state publication workflow uses only the existing approval-verification key and GitHub token; it has no R2 access key. Offline tests enumerate workflows, write credentials, command sites, and lock values in both directions.

## First-class rollback

Before the first effect, preflight downloads every existing changed object under its exact ETag. The rollback manifest binds frozen bytes, SHA-256, MD5/ETag, byte count, source commit, approved forward manifest, exact two-key delete allowlist, and the Mols-only index transition proof.

Known successful effects roll back in reverse order while the same job holds the lock:

1. Restore `trail-shapes.json`, when written, from frozen bytes using `If-Match` against this run's exact post-publish identity.
2. Restore `trail-search.json`, when written, the same way.
3. Restore `index.json`, when written, the same way.
4. Restore a previously existing Mols geom or silhouette the same way.
5. Restore an absent Mols baseline by deleting only that exact created Mols key.

Every restore requires immediate HEAD plus conditional GET to prove exact baseline ETag, byte count, SHA-256, and byte-for-byte equality with the frozen file. The final receipt names every restored global explicitly. For step 5, the root-authorized Option 1 is implemented literally. Immediately before deletion, HEAD and full GET must prove ETag, byte count, and SHA-256 equal this run's upload. The adapter then issues one plain single-key `DeleteObject` for the exact manifest-derived allowlisted key and requires a subsequent HEAD 404. It never issues a wildcard, prefix, batch, global, index, or other-area delete. The accepted residual race is bounded by the shared writer lock and immediate full-byte proof. The delete allowlist remains exactly the Mols geom and silhouette; adding the two global restores does not expand it.

## Crash and timeout reconciliation

Every forward and rollback effect has a durable write-ahead journal. `reconcile-scoped` consumes the exact `trekdex-r2-scoped-release-terminal-v3` pending journal, reviewed manifest, frozen rollback files, source identity, approval, provider identity, and pinned tool identity. Every completed result has an exact deterministic transaction ID over its execution ID, permitted original/reconciliation attempt ID, phase, operation, row index, key, and verified object identity. The whole checkpoint is domain-separated HMAC-authenticated with the existing approval key; recovery verifies approval and this authentication before writer attestation, provider observation, or terminal return. Recomputing the unkeyed result-list and receipt hashes cannot authenticate rewritten history. Rollback results are bound to the exact known forward-effect prefix in reverse manifest order; a four-effect forward prefix therefore requires exactly four matching rollback results, not five. Before `ROLLED_BACK`, the adapter independently verifies all five manifest rows: every reversed row equals its frozen baseline or reviewed absence, and every never-written row still equals that same baseline or absence. A missing, extra, reordered, foreign, or mismatched result fails closed, and any row that cannot be proven at baseline remains `INDETERMINATE`.

Under the same workflow concurrency lock it re-observes the pending key:

- exact output plus this attempt's bound metadata means the effect happened;
- 404 after a pending Mols deletion means the deletion happened;
- an unchanged exact preimage, the created object still present after a pending deletion, a mismatch, or ambiguity causes an operator-review terminal with no retry.

Only a proven completed effect allows the state machine to append its result and resume the remaining forward or rollback sequence once. A reconciliation run can retrieve the exact prior evidence artifact by `reconcile_run_id`. Repeating reconciliation with its terminal receipt returns the same receipt and performs no provider call, but only after the CLI HMAC-authenticates both the original pending journal and the existing terminal with the current approval key. It then binds the terminal to the journal's execution, approval, provider/tool identity, preflight, pending phase/operation/key, source-journal digest, and exact attempt/transaction result prefixes; the resumed pending result must carry the journal's exact attempt ID and its deterministic execution-bound transaction ID. A missing key or signature, invalid authentication, rewritten history, or cross-run terminal fails before writer construction.

The early-return audit covers every R2 CLI path. `apply-manifest` and `apply-quarantine` never trust or return an existing receipt; both reject any pre-existing terminal/journal before provider construction. `reconcile-scoped` is the only idempotent existing-terminal path, and its forward-resume and rollback-resume branches share the keyed validation above. The contract-level already-reconciled return authenticates its single terminal input before returning it. Quarantine reconciliation has no existing-terminal return: it accepts only a write-ahead receipt plus explicit observation evidence and always produces an operator-review terminal.

The offline fake recovery oracle reconstructs the same six metadata fields as production (object SHA-256, source SHA, manifest SHA-256, attempt ID, preimage reference, and conditional mode) and uses the same absent, exact-postimage, exact-baseline, and foreign-object classifications for forward, restore, and delete observations. A table-driven parity campaign mutates every field and state and requires identical observation state, terminal state, receipt effect counts, and provider-effect deltas.

Final rollback verification requires exact baseline bytes/hash/ETag for every restored global (`index.json`, `trail-search.json`, and `trail-shapes.json` when written), absence of every newly created Mols key, and the sealed proof that all non-Mols index/search/shape records stayed unchanged. Partial or ambiguous recovery remains `INDETERMINATE`.

## Secrets and permissions

The workflows use existing secret names only. They never print or persist secret values, alter ACLs, weaken authentication, create credentials or buckets, or grant recursive/bucket-wide write authority. Offline CI has no secrets and performs no provider requests. This repair and its tests perform no live R2 write or non-plan workflow dispatch.
