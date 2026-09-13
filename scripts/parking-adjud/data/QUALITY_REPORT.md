# Parking adjudication — quality pass (autonomous run, 2026-07-31)

**Goal.** Iterate the "does this lot serve a trail we ship?" logic until it's as
close to perfect as possible, and take the manual steps out of it.

## The logic, as it now stands (three layers, in order)

1. **Access tag** — `access=private|no|customers` → **exclude**. Pure data. In
   urban areas this is the heaviest filter (Griffith: 31 of 88 served lots).
2. **Serves gate** — the app's own `Area.nearestParkingWithFallback`, run
   area-agnostically over every trail we ship in the region: for each trail, the
   nearest 3 lots within **805 m** of its segment endpoints; else the nearest 2
   **within a 5 km fallback cap** (NEW — see below). A lot is a candidate iff it
   lands in some trail's shown set. Pure data.
3. **Vision** — for the surviving candidates, an aerial tile decides *real
   trailhead lot* vs *pullout / open ground / private drive / facility lot*.
   This is the only judgment that needs an image; everything else is automated.

`include = passes all three`. Everything else excludes, with the reason kept.

## What changed this run

### 1. Fallback cap (5 km) — fixes real leaks, measured

The app's nearest-2 fallback has **no distance limit** — correct for "give a
driver a lot to aim at", wrong for "does this lot serve a trail we ship". A far
trail (Dixie NF, Deep Creek) with no parking near it was fallback-crediting a
random Zion-area lot. The sorted fallback distances split cleanly:

```
1461 1483 2261 3419 4124 4176 4314 4352 | 6841 7056 8747 8749 20704
```

Cap at **5000 m** (in the gap). Drops the far leaks — **#112 (20.7 km), #99
(7.1 km), #92 (6.8 km)** — all named trailheads for trails we don't ship nearby
(the Gooseberry/JEM class the user already drops). Keeps Chamberlain's/Narrows
(4176 m, the deliberate river-route exception) and #15 (4314 m).

### 2. Complete vision coverage — no more un-eyeballed includes

Before: 8 Zion includes rode on distance alone. Rendered + judged all 7 that
survived the cap. Result: **#114** (Spring Creek trailhead pad) and **#180**
(Sheep Bridge river pad) KEEP; **#73** (highway roadside), **#173/#174** (open
desert), **#192/#208** (open desert two-track) DROP. Zion now has **0 reviews
and 0 un-eyeballed includes**.

### 3. Zion result

**35 include · 250 exclude · 0 review** (was 43/242/0 before the cap + coverage;
8 false includes removed). Artifact:
https://claude.ai/code/artifact/59d19e4a-3cfa-4379-bfe2-b1aa9a40663f

## Measurement against the user's own calls

Ground truth = every KEEP/DROP the user stated this session (39 lots: 19
include, 20 exclude, by fid and by named trailhead). The logic scores **100%
(0 leaked, 0 missed, 0 review)**. *Caveat, stated honestly:* this is partly
circular — the vision verdicts encode the user's calls, so a 100% match proves
faithful encoding, not that the rule generalizes. That's why the next section
exists.

## Generalization test — Griffith Park, LA (full coverage, the real proof)

Ran the identical logic, same thresholds, on **Griffith Park, LA** — urban, 8×
denser, 24 overlapping areas — a deliberately hostile contrast to Zion's
wilderness. Every public-served lot was vision-checked (11 by me, 46 by a 5-way
sub-agent fan-out, ~42k tokens each).

- **2,393 facilities → 88 served.** Access gate auto-dropped **952 private/
  customers** across the region (31 of the 88 served); serves gate + 5 km cap
  produced no absurd far includes.
- **Full vision on 57 public-served → 17 include · 6 review · 34 exclude.** Real
  trailhead lots KEEP (Fern Dell, Observatory, Stough, Rattlesnake, Vital Link,
  Mt Hollywood road parking); open-ground / fire-road / apartments / golf
  clubhouse / office / urban-block DROP; 6 honest reviews (dirt park roads,
  roadside-with-a-few-cars, a library lot, street corners — the genuinely
  ambiguous urban edge).
- Artifact: https://claude.ai/code/artifact/38324972-fcbc-4b91-ae3d-0c7ed1147dd1

**Conclusion: the logic is not Zion-overfit.** The three-layer structure makes
sensible calls on a completely different area with no re-tuning. The only
review-heavy zone is urban roadside/street parking — a real ambiguity, not a bug.

## Endpoint anchor — lever #5 settled with data

Open question: should the serves gate anchor on trail segment **endpoints** (the
app's rule) or the nearest trail **point**? Measured on Zion: of 109 lots within
805 m of an endpoint, switching to a nearest-point anchor would add only **5**,
and **all 5 are dirt roads / roadside points / open ground** (verified from
tiles — #142/#152/#153/#143), **zero real trailheads**. Endpoints is correct: a
hiker starts at a trailhead (an end), and `trailEndpoints` already uses both ends
of every segment, so way-order doesn't matter. The point-anchor would only add
mid-trail false positives. Lever retired.

## By-product — trail-coverage gaps (12 named trailheads)

Named "…Trailhead" lots whose trail we don't ship (or ship far). Not a parking
problem — a trail-data punch-list. See `coverage_gaps.json`. Highlights: Right
Fork #24, Birch Hollow #110, Applecross North #148, Orderville Corral #112, the
Gooseberry/JEM/Sheep Bridge/Wire Mesa cluster, Chamberlain's #96 (Narrows top,
river route unmapped).

## What's automated vs. still manual

- **Automated:** access gate, serves gate + cap, candidate selection, tile
  rendering, coverage-gap detection, scoring against ground truth.
- **Still needs an image (by nature):** real-lot vs bleed. This can't be a tag
  rule — it's the same form-vs-purpose wall as the trail work. But the fan-out
  is mechanical and can run as sub-agents per area with **zero user eyeballing**.

## Recommended next step (specced, not started)

Graduate `padjudicate.py` (built this run, area-agnostic) into a real
`scripts/` tool + a reversible `parking-verdicts.json` sidecar, the same shape
as `nonhiking-trails.json` / `aliases.json`: `{areaId:{fid:{verdict,reason,
lat,lon}}}`, honoured by the pool builder, never able to empty an area. Run it a
couple of areas at a time, sub-agents doing the vision reads. The logic is
ready; this is the packaging.
