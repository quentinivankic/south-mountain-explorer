# Trailhead parking extraction — design & evidence

How the app answers "where do I park?" — `scripts/add-parking.py` enriches
each published area's geom with an `amenity=parking` layer from OpenStreetMap,
so the app can draw parking pins. This doc records the evidence behind each
extraction choice so the rules are defensible, not vibes.

## Why this shape

The correct home for this is the trailforge **publish** pipeline, where the
park **boundary polygon** and a routing graph exist (so you could do "lot
inside/at the edge of the boundary, connected to a trail by a footway"). We
don't have those in the published geom — only a bbox and the curated trails —
and the goal was to ship parking without a homelab re-run. So this is a
**post-process** over the published geom via Overpass (reachable from CI),
mirroring how DEM elevation started. Fold into publish once proven.

## Extraction rules (with citations)

**Query `amenity=parking`, never `amenity=parking_space`.** `parking_space`
tags an *individual stall* inside a lot and is "an addition, not a
replacement" for the lot-level `amenity=parking`; each stall is a separate
feature. Using it as the lot would shatter one lot into many false pins.
Verified against the OSM wiki (adversarial deep-research, 3-0).
· <https://wiki.openstreetmap.org/wiki/Tag:amenity=parking_space>

**Drop non-public `access`.** For public trailhead use we drop
`access ∈ {private, no, customers, permit}` and **keep untagged + permissive**:
- `customers` = a store's lot ("only customers may park there") — not a
  trailhead. <https://wiki.openstreetmap.org/wiki/Tag:access=customers>
- `permissive` = "open for general public use" (revocable) — this is exactly
  how a lot of private-but-public trailhead land is tagged, so we **keep** it.
  <https://wiki.openstreetmap.org/wiki/Tag:access=permissive>
- Most real trailhead lots carry **no** access tag → kept.

**Drop on-street `parking=*`.** `street_side / lane / on_kerb / half_on_kerb /
shoulder` are on-street parking *positions* (linear, along a road), not a
trailhead lot; `surface / underground / multi-storey` are real lots.
Excluding the street values removes false "lots" near urban-adjacent trails.
· <https://wiki.openstreetmap.org/wiki/Street_parking>
· <https://wiki.openstreetmap.org/wiki/Tag:parking=street_side>

**Use `highway=trailhead` as a corroborating signal.** It's a *de-facto*
established tag marking "where a road or urban path meets a trail," and
"parking space will usually be available" — some mappers tag the parking area
itself as `highway=trailhead`. So a lot at/near a trailhead is **confirmed**
trailhead parking (higher confidence than mere trail proximity), and we keep
such a lot even if the curated trail geometry is a bit far. Reported as a
precision metric (% of lots trailhead-corroborated).
· <https://wiki.openstreetmap.org/wiki/Tag:highway=trailhead>

## Association: proximity, honestly a heuristic

There is **no authoritative OSM distance** for linking a lot to a trail. We
keep a lot if it's within **`PARKING_TRAIL_MAX_M` (250 m)** of a trail vertex
**or** within **`TRAILHEAD_COINCIDE_M` (80 m)** of a `highway=trailhead`. The
250 m is a guess — so every run prints the **lot→nearest-trail distance
histogram**, and we tune the cutoff from where real lots actually sit rather
than from the armchair. Connectivity (a footway physically linking lot→trail)
would be more accurate and is the eventual upgrade; proximity is the
available approximation.

Known limits: overlapping area bboxes can attach one lot to two areas
(no cross-area dedup); a large/irregular lot's centroid can land off the lot;
park-and-ride is not specially handled.

## How other apps solve this (and what it validates)

Researched the majors to sanity-check our model (WebSearch; proprietary apps,
so this is documented behavior, not internals):

- **AllTrails** — a **"Directions" button that hands off to Google/Apple Maps**
  to drive you to the trail start; it does NOT do in-app driving navigation.
  Trail pages supplement with **editorial + community notes** on restrooms,
  parking, and fees.
  <https://support.alltrails.com/hc/en-us/articles/37200401098516-Getting-started-on-AllTrails>
- **Gaia GPS** — "Guide Me" on a waypoint **links out to your streets app**
  for directions to the trail start; users **drop a waypoint** to mark their
  parked car. <https://blog.gaiagps.com/top-10-ways-to-use-waypoints/>
- **onX** — no direct handoff: copy the waypoint coordinates, paste into
  Google Maps. <https://www.territorysupply.com/onx-vs-gaia-gps>

What this validates for us:
1. **Nobody does in-app driving nav — everyone hands off to the phone's maps
   app.** So the eventual "Directions" button is just an Apple Maps handoff
   (`MKMapItem.openInMaps` to the parking point) — the industry norm, and why
   deferring driving directions is the right call.
2. **Parking is a single point / waypoint**, not a rendered polygon — validates
   the centroid representation.
3. **`fee` is worth surfacing** (AllTrails calls it out); we carry it from OSM.

The honest gap: AllTrails fills parking info with **human editorial + community
notes**, so its coverage beats raw OSM where OSM is thin. We can't match that
automatically — OSM `amenity=parking` + `fee` is a strong automated baseline,
and closing the gap is a future **community "report parking"** flow (tied to
the existing report-a-problem flywheel), not something to fake now.

## Validation is automated (no eyeballing)

Eyeballing 247 AZ areas doesn't scale, so quality is judged from numbers:

1. **Per-run report** — coverage (% areas with a lot, % with zero), lots/area
   median/p95/max, the distance histogram, % named, % trailhead-corroborated,
   and the 10 highest-count areas (bbox-bleed suspects). `--dry-run` prints
   this and writes nothing.
2. **Golden regression gate** — `scripts/golden-parking.json` asserts known
   AZ parks land in a `[min,max]` lot range; the script exits non-zero on a
   miss, so a broken extraction (0 lots at South Mountain) or a blown-out
   threshold fails loudly. Unit tests cover the pure transforms
   (`scripts/test_add_parking.py`).

**Calibration flow:** run `--dry-run` first → read the report + the golden
counts → tighten `golden-parking.json` bounds to observed ±, and adjust
`PARKING_TRAIL_MAX_M` if the histogram shows lots clustering well under 250 m
→ then the real write. The current golden bounds are loose placeholders
pending that first real run.
