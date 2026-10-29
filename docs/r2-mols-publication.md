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

The source binding prevents replay after the workflow code or main tree changes. The verifier also requires `dry_run=false`, `no_routes=false`, `touch_report=false`, `elevation=false`, and no read-only pilot selector. Denmark publication without this receipt fails before publication work. Ordinary non-Denmark dispatches with a blank receipt retain the existing state-wide behavior.

The verifier emits safe fields as step outputs. The chained sync dispatch consumes only those verified outputs plus the commit step's exact pushed SHA and tree. No release reference is copied directly from a free-form workflow input.

## Exact repository effect

Verified Mols mode materializes a runner-local one-row index directly from the signed receipt and passes that file plus `--only-area nationalpark-mols-bjerge-dk` to the state publisher. The publisher must return that same row with positive trail count and mileage. A guarded merge inserts or replaces only that row in `public/areas/index.json`, without changing or reordering any other row. Generic US mode keeps using the canonical state-wide index and receives no exact-area selector.

After generation, the workflow stages its existing publication roots and then runs a second verification of the same signed receipt. Commit and push are refused unless the staged file set is exactly:

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

Byte-identical objects, including `parking.json`, `trail-search.json`, and `trail-shapes.json`, are proven no-ops and omitted. The app index uses exact compact producer framing, and the contract proves every non-Mols row is unchanged.

Forward order is geom, silhouette, then index. The two absent Mols objects use atomic `If-None-Match: *`. The index replacement uses atomic `If-Match` against its exact reviewed ETag. Each request binds the new SHA-256/bytes, source commit, approved manifest, condition mode, and full reviewed preimage reference.

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

1. Restore `index.json` from frozen bytes using `If-Match` against this run's exact post-publish ETag.
2. Restore a previously existing Mols object the same way.
3. Restore an absent Mols baseline by deleting only that exact created Mols key.

For step 3, the root-authorized Option 1 is implemented literally. Immediately before deletion, HEAD and full GET must prove ETag, byte count, and SHA-256 equal this run's upload. The adapter then issues one plain single-key `DeleteObject` for the exact manifest-derived allowlisted key and requires a subsequent HEAD 404. It never issues a wildcard, prefix, batch, global, index, or other-area delete. The accepted residual race is bounded by the shared writer lock and immediate full-byte proof.

## Crash and timeout reconciliation

Every forward and rollback effect has a durable write-ahead journal. `reconcile-scoped` consumes the exact `trekdex-r2-scoped-release-terminal-v1` pending journal, reviewed manifest, frozen rollback files, source identity, approval, provider identity, and pinned tool identity.

Under the same workflow concurrency lock it re-observes the pending key:

- exact output plus this attempt's bound metadata means the effect happened;
- 404 after a pending Mols deletion means the deletion happened;
- an unchanged exact preimage, the created object still present after a pending deletion, a mismatch, or ambiguity causes an operator-review terminal with no retry.

Only a proven completed effect allows the state machine to append its result and resume the remaining forward or rollback sequence once. A reconciliation run can retrieve the exact prior evidence artifact by `reconcile_run_id`. Repeating reconciliation with its terminal receipt returns the same receipt and performs no provider call.

Final rollback verification requires exact baseline index bytes/hash/ETag, absence of every newly created Mols key, and the sealed proof that all non-Mols index rows stayed unchanged. Partial or ambiguous recovery remains `INDETERMINATE`.

## Secrets and permissions

The workflows use existing secret names only. They never print or persist secret values, alter ACLs, weaken authentication, create credentials or buckets, or grant recursive/bucket-wide write authority. Offline CI has no secrets and performs no provider requests. This repair and its tests perform no live R2 write or non-plan workflow dispatch.
