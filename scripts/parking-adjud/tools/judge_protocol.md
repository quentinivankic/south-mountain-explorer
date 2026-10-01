# Judge protocol — one parking facility, one verdict

Follow this checklist per lot, in order. The final output is one JSON object per
lot (schema at the bottom). This document is the verbatim instruction set for any
judge — me today, fan-out agents later. No step is skippable; speed comes from
parallelism, never from skipping rungs.

**Network boundary:** judges never use a browser, URL, network tool, or
unassigned local file. The host prefetches and freezes every packet, image, and
external source. If required evidence is absent, return REVIEW with a precise
request for the next host-prepared run; do not fetch it.

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
- **NAIP variant** — different sensor/date. The host pre-renders and freezes it
  when ESRI is canopy-blind, shadowed, or stale-looking. Agents never fetch it
  themselves; if the assigned packet lacks the needed alternate frame, return
  REVIEW with a `resolve_hint` requesting that host-frozen artifact. Still blind
  after assigned NAIP → EXISTS stays on its prior; if the prior is bare → REVIEW.
- **Misregistration:** read the ~15 m neighbourhood of the red outline, not the exact
  pixel. A lot 10 px off the outline is the lot.

## 3. The three axes

- **EXISTS** — physical place to leave a car? Evidence strength: parked cars >
  painted stripes > delineated graded surface > bare clearing. State which you saw
  and in which frame, or which prior carried it. A road right-of-way, trail
  intersection, widened-looking lane, or light patch is not a parking facility
  without a vehicle-access throat plus a bay outside the travel lane, parked
  vehicles, stalls, or an independently delineated graded surface. A continuous
  curb/sidewalk/verge is a positive non-lot signal.
- **PUBLIC** — data first. Explicit lot-level `access=yes|permissive`, a named
  official trailhead/access record at the lot, or a source explicitly granting
  public parking can decide yes. Government ownership, a public/open enclosing
  park or venue, road right-of-way, an ungated driveway, or absence of a
  restrictive tag/sign does **not** by itself establish general parking rights.
  Whole-property `PUBLIC_ACCESS=Open` does not make internal VIP, pit, school,
  church, event, employee, resident, or customer spaces public trail parking.
  Mixed-access clusters: judge the member(s) nearest the trail. Fee booths are
  fine when public access is otherwise proved (fee ≠ private).
- **SERVES** — the gate says a trail claims it, but distance is only a candidate
  signal. Ask whether anything visible or authoritative explains this lot better.
  Rules:
  - **Dual-use/overflow comes last.** First prove PUBLIC at the lot level. Then
    require a trail to start at/inside the lot, a named/official access record,
    a direct walkable trail approach, or other evidence that hikers can actually
    use this facility. A finite walk ≤ ~1 mile alone does not prove SERVES.
  - A school, church, commercial, residential, restaurant, event, transit, or
    motorsport lot keeps its direct owner as the better explanation unless the
    lot-level public/trail evidence above independently establishes dual use.
  - "The trailhead already has a lot" never drops a genuinely public trail lot,
    but a separate closer designated lot is evidence against calling a
    facility-specific lot trail overflow.
  - No-route + far supports the alternative explanation; fallback requires
    especially strong named/official trailhead evidence.

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
