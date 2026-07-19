# ADR 0001 — The data factory and the app meet at a CDN, not in code

- **Status:** Accepted (retroactive — documents the architecture as built)
- **Date:** 2026-07-19
- **Applies to:** `trailforge/`, `public/areas/`, `ios/`, `.github/workflows/`

## Context

TrekDex is an iOS hiking tracker (SwiftUI, iOS 18+, live on public TestFlight)
that needs global, AllTrails-quality trail coverage. Three constraints shaped
the architecture:

1. **The data is built on a modest homelab**, not a cluster. A full OSM planet
   database is out of reach, so the pipeline must be filter-first and streaming.
2. **Trail coverage changes constantly** — new areas, corrections, curation
   fixes. Gating every data change on App Store review would make coverage work
   unshippable.
3. **The curation rules are empirical.** Each drop rule was learned from a real
   discovered bad example (`mtb:scale:imba` once ate all of South Mountain), so
   the data layer must be re-runnable and reviewable independently of the app.

An app that parsed OSM directly, or that bundled its trail data, would violate
all three.

## Decision

Split the system in two and let them communicate **only** through versioned JSON
artifacts served from a CDN.

### 1. The data factory — `trailforge/` (Python)

Filter-first, never a full planet DB:

```
OSM extract → prefilter (hiking-only PBF) → aoi (bbox cut, osmium
  --strategy=smart so park boundaries stay whole) → assemble (relations →
  name-stitch → spur-attach → merge → curation) → serve/publish_areas.py
  (boundary clip + convert + validate + DEM elevation) → artifacts
```

- `extract/` — prefilter and per-area bbox cuts.
- `assemble/model.py` — stitches ways into named trails and applies **curation**.
  This is where domain judgment lives, not in the app.
- `serve/publish_areas.py` — clips trails to each area's boundary, validates,
  bakes DEM-sampled elevation gain and difficulty.
- `golden/` + `viewer/` — a regression set and a QA UI with per-category
  "removed" buckets, so curation changes are reviewed after the fact.

### 2. The contract — `public/areas/`

- `index.json` — master catalog, ~29,852 rows.
  Row shape: `[id, name, state, lat, lon, trail_count, total_mi, osm_relation_id]`.
- `geom/<id>.json` — one area's trails.
- `silhouettes/<id>.json` — card art derived from geom.

This is the **entire** interface between the two systems. Neither side knows
anything else about the other.

### 3. Distribution — Cloudflare R2 (`cdn.trekdex.app`)

`sync-geom-to-r2.yml` publishes geom and silhouettes on merge. The app fetches
from R2 with **ETag revalidation** and falls back to a bundle copy when offline.

### 4. The app — `ios/` (88 Swift files)

Four tabs (Explore · Browse · Stats · Settings). The architecture lives in the
**24 services**, not the 36 views:

- `AreaIndexService` / `AreaDataService` — fetch the catalog and geom from R2,
  prefer the network copy, fall back to the bundle.
- `RecordingService` — GPS tracking with app-kill recovery (12 h window).
- `CoverageService` / `ProgressService` — trail completion, 95% threshold.
- Five `@Observable` singletons each expose `resetAll()`/`reload()`, which is
  what makes backup/restore possible.

## Consequences

### Positive

- **Coverage ships without an App Store build.** New and corrected areas reach
  already-installed apps on next launch. This is the single most valuable
  property of the design — a whole-US republish is a data operation, not a
  release.
- **The pipeline can be rewritten without touching Swift**, and was: the entire
  System-1 trail builder was replaced by trailforge with no app change.
- **Curation is reviewable in isolation** via the viewer and golden set, instead
  of being discovered by users.
- **Offline works by construction** — the bundled fallback is the same shape as
  the served artifact, so there is no separate offline code path.

### Negative

- **Two deploy paths with different latencies.** Data is minutes; app code is a
  TestFlight/App Store build. Any feature needing both must be sequenced, and
  mixing them up produces "I shipped it but users don't see it" confusion.
  Parking hit exactly this: the data was live for Arizona while the rendering
  code sat in an unshipped build.
- **The artifact shape is a real contract.** Adding a field means the app must
  tolerate its absence, because old apps read new data.
- **No CI gate on the data side.** Pipeline paths have no automated test; the
  gate is a dry-run plus human review, which is why every publish workflow
  defaults to `dry_run=true`.
- **Silent-skip failure modes.** An area that fails to assemble simply doesn't
  appear. This masked an 810-area coverage gap until it was audited directly.

## Related decisions

- **Publishing is dispatch-only, `dry_run=true` by default** (three workflows:
  single-state, batch, whole-US). A bad publish silently changes what every user
  sees, so the default is always "show me first."
- **TestFlight uploads are manual dispatch only** — never automatic on push.
- **Curation policy** lives in `assemble/model.py`; rules are only added with a
  real failing example and a regression entry. See `CLAUDE.md`.
- **Elevation profiles are direction-oriented in the app, not the data** — the
  pipeline ships a distance-even elevation array and the app decides which end
  is "start." Keeps a display concern out of the artifact.
