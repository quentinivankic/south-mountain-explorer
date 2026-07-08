# Unified Trail Filter — spec

One country-agnostic model that decides which OSM ways are real hiking
trails worth showing / counting. It **merges** three things:

1. The **graded confidence model** (`build/scoring_reference.py` +
   `TrailScoring.swift`) — flexibility, no hard-coded per-country rules.
2. The **production seed pipeline's** cheap structural guards
   (`scripts/build-trail-counts.py`, `scripts/_seed_constants.py`) — which
   already ship and catch what pure scoring misses.
3. The **research findings** (`nz-curation-review` + the sourcing review):
   route relations are the industry-standard OSM "recognised trail"
   signal; WDPA is commercially unusable; "inside a park ≠ a trail".

**Philosophy (unchanged, spec §4/§7.1):** the licensing gate is the ONLY
thing that removes a way from the tiles. This score only informs
**curation** — which trails the app shows/counts on-device. Everything
below is the score, not a tile-time drop.

## Pipeline order

```
licensing gate → stage (OSM tags) → route index → dedup (name+connectivity)
              → score → band → trail↔area spatial join
```

## Score

`score = clamp(base + Σ weightᵢ·signalᵢ, 0, 100)`, base **30**.
Bands: **high ≥ 70**, **medium ≥ 45**, **low < 45**.

### Positive signals

| signal | weight | source | note |
|---|---|---|---|
| `in_route_relation` | +40 | new (shipped) | member of an OSM hiking route relation — the global "official" |
| `network_national` (iwn/nwn) | +15 | new (shipped) | marquee national/international routes |
| `authoritative_match` | +40 | new (shipped) | **optional** per-country gov conflation; not required |
| `has_name` | +40 | both | now guarded by the road-word negative below |
| `has_known_operator` | +20 | both | a route's operator counts |
| `connected_to_named_trail` | **+25** | **from production** | unnamed way whose endpoint touches a named trail's nodes — rescues real unnamed segments without trusting all unnamed ways |
| `inside_protected_area` | **+10** | **research-demoted** | was `in_official_whitelist +40`; "inside a park ≠ a trail" (PCL is land, not trails). Split from an actual track-register match |
| `region_trust_high` | +5 | new (shipped) | mild |

### Negative signals

| signal | weight | source | note |
|---|---|---|---|
| `vehicle_or_utility_road` | **−50** | **from production** | `highway=track` AND (name contains road/drive/avenue/canal/drain/ditch/boulevard/highway/freeway **OR** `motor_vehicle=yes`/`motorcar=yes`). A car/utility road, not a hike |
| `access_restricted` (no/private/discouraged) | −45 | both | |
| `informal` | −40 | new (shipped) | |
| `lifecycle` abandoned/disused | −60 | new (shipped) | |
| `trail_visibility` bad/horrible/no | −20 | new (shipped) | |
| `sac_scale ≥ demanding_mountain_hiking` | **0** | **research** | was −10; NZ's marquee hikes ARE demanding — remove the penalty |
| `tiger_unreviewed` | −15 | new (shipped) | US-only |
| `recently_edited / low_trust_editor` | −10 | new (shipped) | |

### Post-dedup length filter (from production)

After name+connectivity dedup, a trail's **total** length must be
**≥ `MIN_TRAIL_MI` (0.59 mi)** or it is forced to LOW. Length is a
whole-trail property, so this runs AFTER merge — a 30 m named stub no
longer scores 70.

## Why these specific merges

The production guards close **false-keeps** the new model had:

- `"Irrigation Canal"` (named track) → `+40 name −50 road` = **20 → low** ✓ (was 70/high)
- motor-vehicle `"Smith Rd"` track → **low** ✓
- 30 m named stub → dropped by the length filter ✓

The new signals close production's **false-drops**:

- unnamed backcountry track that's a hiking-route member → `+40 route` = **high** ✓ (production had no route signal)
- `connected_to_named_trail` keeps the unnamed real segments production
  also kept — but graded, not binary.

## Trail ↔ area association

Separate from the filter, part of the unified data model. Spatial-join
each trail against the **OSM area polygons the pipeline already extracts**
(point-in-polygon / overlap). Trail in multiple areas → assign to all;
outside any → "ungrouped".

**Boundary sources:** OSM (ODbL, with attribution) or national open-gov
(e.g. US **PAD-US**, public domain). **Never WDPA / Protected Planet** —
its licence prohibits commercial use, redistribution via apps, and
sub-licensing without a paid UNEP-WCMC agreement.

## Build inventory

Already shipped: route relations, `network`, name/operator,
access/informal/abandoned/visibility/sac, name+connectivity dedup, graded
bands.

**To add (this spec):**
1. `vehicle_or_utility_road` guard (road-words + motor tags).
2. `connected_to_named_trail` as a graded signal.
3. Post-dedup **length filter** (`MIN_TRAIL_MI`).
4. Demote `inside_protected_area` +40 → +10 (split "in park" from "matched a register").
5. Zero out the `sac_scale` penalty.
6. The **trail ↔ area spatial join**.

Each scoring change lands in lockstep across `weights.default.json`,
`ScoringWeights.default`, and both conformance suites.

## Open tuning knobs

- `MIN_TRAIL_MI` (0.59 default).
- `inside_protected_area` +10 vs 0.
- Whether `connected_to_named_trail` should be strong enough to reach
  "medium" alone.
- Whether the road-word guard should be a **hard exclude** (drop from
  tiles) vs the −50 signal (tile, curate out) — currently a signal, to
  preserve the "gate is the only remover" philosophy.
