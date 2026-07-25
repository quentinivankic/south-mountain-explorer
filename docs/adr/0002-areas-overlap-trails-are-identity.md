# ADR 0002 — Areas overlap; the trail is the unit of identity

- **Status:** Accepted — A shipped, B+D in progress, C deferred (north star)
- **Date:** 2026-07-25
- **Applies to:** `ios/` (ProgressService, CoverageService, AreaIndexService, Browse),
  `public/areas/`, `trailforge/`, `scripts/detect-duplicate-areas.py`

## Context

A tester's completion of "South Mountain" vanished: there are two areas —
`south-mountain-preserve-az` and `south-mountain-park-and-preserve-az` — with
byte-identical trail sets, and progress recorded under one twin was invisible
under the other.

The root cause is not a duplicate area. It is that **user progress is keyed by
`(areaId, trailId)`, while the same physical trail lives inside several area
containers.** Areas overlapping, nesting, and sharing trails is legitimate and
common — Saguaro National Park contains its two districts contains its
wilderness; Glacier National Park sits inside Waterton-Glacier Peace Park. Any
"pick one area, delete the other" rule destroys real trails to fix bookkeeping.

`#471`/`#474` made **completion** follow the physical trail by geometry
fingerprint (SHA256 of coords rounded to 5dp). Coverage and the walked-here
halo still do not cross.

## Options

- **A — Fingerprint cross-credit at display sites** (shipped `#471`/`#474`).
  Pro: done, zero risk; completion + count + map cyan cross the twin.
  Con: completion only, not coverage/halo; leaks when a shared trail is clipped
  at two different boundaries (different fingerprint).
- **B — Collapse byte-identical twins** (union trails, alias the duplicate away).
  Pro: kills the cause; low risk since the survivor shows the same trails.
  Con: identical twins only; needs a wire-safe hide + additive progress carry.
- **C — Global trail identity** — progress keys on a stable trail id; areas
  become overlapping reference collections.
  Pro: overlap / nesting / duplication all become harmless; stored once, scales;
  **zero per-pair adjudication.**
  Con: needs a STABLE trail id the pipeline does not produce today — trailforge
  ids drift across runs (the build-8 rekey migration), OSM "trails" are
  name-stitched composites that drift, and geometry-hash breaks on clipping.
  Keying irreplaceable progress on a drifting id is dangerous. It is also a
  serve/publish/bundle/R2 re-plumb, not a key swap.
- **D — Nested/overlap as SEARCH dedup** — hide redundant re-listings from the
  top-level list; keep both areas openable and searchable by name.
  Pro: fixes "Saguaro returns 4" cheaply; deletes nothing.
  Con: cosmetic; canonical choice is subtle (see the trap below).

## Decision

- **A:** shipped.
- **B + D now**, via a **reversible alias side-car** — never a destructive edit
  to the positional index.
- **C: deferred** as the north star, revisited only after (1) trail identity is
  made stable and (2) we measure that progress actually crosses *genuinely
  different* overlapping areas often enough to justify the rebuild.

## What the data says

`scripts/detect-duplicate-areas.py` over 9,060 areas with trail geom, identity =
per-trail fingerprint (matches the app's `Trail.completionFingerprint`):

- **Duplicates — identical trail multiset:** 145 groups, **186 areas** aliased
  away. Safe; the survivor renders the same trails. Canonical rule: prefer an
  OSM relation id, then the longer display name, then lexical id.
- **Nested — strict subset:** 1,248 raw candidates is **far too broad** — it
  hides distinct named destinations (Salome Wilderness, 3 trails, sits inside
  Tonto National Forest, 380). Gate on `ratio = A_trails / container_trails`.
  At **ratio ≥ 0.75 → 28 areas**, exactly the "national park re-listed as its
  wilderness/natural-area twin" pattern (Congaree NP Wilderness → Congaree NP;
  Frozen Head State Natural Area → State Park). This is the true Saguaro-class
  redundancy.

### The canonical trap (why D must not auto-execute)

For nested areas, **"canonical = the container" is wrong.** Glacier National
Park (156 trails) is a strict subset of Waterton-Glacier International Peace Park
(158), so a size-based rule would *hide Glacier National Park*. Caesars Head
State Park would vanish behind Mountain Bridge Wilderness. Canonical must be the
**more iconic designation** (National Park > Peace Park > National Monument >
Wilderness > State Park > Natural Area …), independent of which polygon contains
which. 28 cases — small enough to hand-review behind a designation-priority rule.

## Mechanism (wire-safe, reversible)

`public/areas/index.json` is a **positional array** shared with old app builds —
appending per-row fields risks their strict decoders. So:

- Ship a side-car **`public/areas/aliases.json`** = `{ id: { canonical, kind } }`.
  Old apps ignore an unknown file; new apps read it.
- **Browse/search** hides aliased ids, so each place appears once. Reversible:
  delete the file.
- **Progress:** completion already crosses via fingerprint. Coverage and the
  halo resolve an aliased id to its canonical **additively** — copy, never
  delete; `touchedAreaIds` is computed, so tag the canonical rather than rewrite.
- **Nothing deletes an area or a trail.** An area with even one trail no other
  area has is never aliased — the exact loss the tester worried about.

## Consequences / open

- Nested canonical needs the designation ranking wired before execute (the
  Glacier trap). Duplicates are safe now.
- Coverage + halo cross-resolution is the remaining client work; until it lands,
  only completion crosses an alias.
- If measurement later shows progress crossing genuinely-distinct overlaps is
  common, escalate to C — but only once a stable trail id exists.
