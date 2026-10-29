# Mols Bjerge R2 publication contract

This path publishes only `nationalpark-mols-bjerge-dk`. Both workflows remain manual-only (`workflow_dispatch`), and `trailforge-publish.yml` remains `dry_run: true` by default.

## Reviewed plan, then apply

A non-dry-run Trailforge publication seals the exact commit produced after any rebase and successful normal push. It records the full commit SHA and tree and dispatches `sync-geom-to-r2.yml` with the complete required input set. That chained dispatch always passes `plan_only=true`; it cannot write R2 and does not accept an approved manifest hash.

The plan run checks out the named commit detached, verifies its tree, obtains the complete reviewed inventory, constructs all four global producer bytes, and emits the canonical upload manifest. The scoped contract compares every producer with the inventory and refuses the plan if any changed key falls outside:

- `nationalpark-mols-bjerge-dk.json`
- `silhouettes/nationalpark-mols-bjerge-dk.json`
- `index.json`

Byte-identical objects, including `parking.json`, `trail-search.json`, and `trail-shapes.json`, are proven no-ops and omitted. The index preflight parses both versions and proves every row except `nationalpark-mols-bjerge-dk` is identical and remains in the same order.

A separate reviewed manual dispatch names the exact manifest SHA-256 and sets `plan_only=false`. It repeats source, inventory, producer, manifest, and approval checks. No branch name or mutable tip substitutes for the full source commit and tree.

## Conditional forward writes

The first publication creates only the two exact Mols object keys with `IF_NONE_MATCH`. It replaces the canonical index only with `IF_MATCH` against the reviewed ETag after preflight verifies the complete ETag, version, byte count, and SHA-256 tuple. Each request carries the new byte identity, source commit, approved manifest hash, condition mode, and a canonical reference to the complete preimage. Provider success must include the exact expected ETag, checksum, and version, followed by exact-version HEAD and GET verification. Missing or ambiguous success evidence is indeterminate, never assumed successful.

Forward order is geom, silhouette, then index. The new area therefore cannot become discoverable before both object bodies verify.

## One shared R2 writer lock

Every workflow holding R2 write credentials uses the concurrency group `sync-geom-to-r2` for its entire job:

- `.github/workflows/sync-geom-to-r2.yml`, which invokes `scripts/stage-changed-r2.py`
- `.github/workflows/cleanup-r2-orphans.yml`, which invokes `scripts/cleanup-r2-orphans.py`

The only production scripts containing direct `put-object`, `copy-object`, or `delete-object` operations are those two scripts. Other workflows may dispatch `sync-geom-to-r2.yml`, but hold no R2 credentials and perform no R2 object operation themselves. Offline contract tests enumerate all workflows and production scripts in both directions so a new unlocked writer fails CI.

## First-class rollback

Before the first effect, preflight downloads each existing changed object by exact version and ETag. The rollback manifest binds those frozen bytes, SHA-256, MD5/ETag, byte count, version ID, source commit, approved forward manifest, exact two-key delete allowlist, and the Mols-only index delta proof.

If a later forward condition is deterministically refused, known successful effects roll back in reverse order while the same job still holds the shared lock:

1. Restore `index.json` from frozen pre-publication bytes using `IF_MATCH` against this run's exact post-publish index identity.
2. Restore any previously existing Mols object using `IF_MATCH` against this run's exact post-write identity.
3. For an absent baseline, delete only one of the two exact Mols keys and only when the rollback row proves this run created it.

Immediately before either allowed delete, the adapter HEADs the exact key and requires this run's ETag, byte count, version, and SHA-256. If HEAD lacks a checksum, an exact-version conditional GET is hashed under the lock. A mismatch, absence, malformed response, or ambiguous read prevents deletion. The adapter issues at most one conditional delete for that row and then requires a 404. Wildcards, prefixes, batches, global keys, the index, and every other area are rejected by both manifest validation and the provider adapter.

The accepted residual race is bounded by the shared writer lock and the immediate identity check. No unconditional or broad delete authority exists.

Final verification requires the exact baseline index bytes/hash/ETag, absence of every newly created Mols key, and the sealed proof that all non-Mols index rows were unchanged. A partial or indeterminate forward/rollback response records a write-ahead receipt and stops. It is never retried blindly; first-hand reconciliation of the pending exact key and identity is required.

## Secrets and permissions

The workflows use existing R2 secret names only. They do not print or persist secret values, alter ACLs, weaken authentication, or grant recursive/bucket-wide write authority. Repository CI has no secrets and performs no provider requests.
