# Judge protocol — one parking facility, one verdict

Follow this checklist per lot, in order. The final output is one JSON object per
lot (schema at the bottom). This document is the verbatim instruction set for any
judge — me today, fan-out agents later. No step is skippable; speed comes from
parallelism, never from skipping rungs.

## 0. Read the dossier FIRST and pre-register the prior

Before opening any image, write down (in the verdict draft):

- `prior`: `surveyed` (amenity=parking + any of surface/parking/fee/capacity/name/
  operator) or `bare` (naked amenity=parking).
- The serving trail, its edge distance, fallback flag; walk_m and connection if present;
  trailhead nodes ≤120 m; footway presence; building overlap; mixed-access flag;
  polygon area m².

The image is then read against this expectation. You are NOT allowed to first look
and then decide what the tags "must have meant."

## 1. Burden of proof (memorize)

- **Surveyed prior → EXISTS defaults to yes.** Only a POSITIVE contradiction flips it:
  the mapped footprint is clearly a building, lawn, water, or nothing but through-lanes.
  "I can't see a lot" NEVER flips a surveyed prior — escalate (Z3, NAIP) or REVIEW.
- **Bare prior → vision decides EXISTS honestly**, still through the full ladder.
- **Empty ≠ absent.** No cars on capture day is zero evidence against a lot.
- **Blank tag ≠ no.** Absence of access/surface says nothing.

## 2. The ladder (all frames pre-rendered; red = mapped lot, yellow = our trails)

- **Z1 (600 m)** — context: what does this plausibly serve? Name every plausible
  non-trail owner you can see (school, golf, apartments, office, picnic area…).
- **Z2 (220 m)** — the lot: delineation, aisles, road relationship, neighbours.
- **Z3 (140 m, 0.14 m/px)** — confirmation: stalls, stripes, individual cars, surface.
  MANDATORY before any DROP of a surveyed prior, and before any KEEP that Z2 didn't
  already prove with cars/stripes.
- **NAIP variant** — different sensor/date. Fetch when ESRI is canopy-blind, shadowed,
  or stale-looking. Still blind after NAIP → EXISTS stays on its prior; if the prior is
  bare → REVIEW.
- **Misregistration:** read the ~15 m neighbourhood of the red outline, not the exact
  pixel. A lot 10 px off the outline is the lot.

## 3. The three axes

- **EXISTS** — physical place to leave a car? Evidence strength: parked cars >
  painted stripes > delineated graded surface > bare clearing. State which you saw
  and in which frame, or which prior carried it.
- **PUBLIC** — data first (access tags decide when present; mixed-access clusters:
  judge the member(s) nearest the trail). From the air: gates, "residents only"
  geometry (a driveway of one house), fee booths are fine (fee ≠ private).
- **SERVES** — the gate says a trail claims it. Ask: does anything visible explain
  this lot BETTER than the trail? Rules:
  - **Dual-use is KEEP.** A picnic/nature-center/facility lot where the trail starts
    at or beside the grounds serves both. Facility explanation wins only when the
    trail neither starts there nor approaches walkably better than a closer lot.
  - **Overflow is real.** "The trailhead already has a lot" never drops the next one.
  - Use walk_m: a finite walk ≤ ~1 mile supports SERVES; no-route + far supports the
    alternative explanation.

## 4. Verdict

- KEEP = all three yes. DROP = any axis confidently no (cite the evidence).
  REVIEW = an axis is genuinely undecidable after the FULL ladder — and the verdict
  must name what would resolve it ("NAIP leaf-off", "needs ground visit").
- Confidence: `certain` (cars/stripes seen, or hard tag rule) / `strong` (clear read) /
  `leaning` (plausible but soft).

## 5. Output schema (one JSON object per lot, no prose outside it)

```json
{"fid": 1596, "osm": ["way/896741459"], "verdict": "KEEP",
 "prior": "surveyed",
 "exists": {"call": "yes", "evidence": "Z3: painted stalls + 2 cars; NAIP: 5 cars"},
 "public": {"call": "yes", "evidence": "access=yes; no gate visible Z3"},
 "serves": {"call": "yes", "evidence": "Bird Sanctuary Loop edge 50 m; walk 46 m; nothing else adjacent"},
 "frames_used": ["z1","z3","z3_naip"],
 "tags_cited": {"parking": "street_side", "surface": "asphalt", "fee": "no"},
 "confidence": "certain",
 "resolve_hint": null}
```
`resolve_hint` is required (non-null) when verdict=REVIEW.
