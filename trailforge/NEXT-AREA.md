# NEXT-AREA.md — running trailforge on a new area

The playbook distilled from the first real area (South Mountain, Phoenix).
Read this before pointing the pipeline at a new park/region. It exists so
the hard-won lessons travel with the repo instead of being relearned each
session. See `SPEC.md` for the extraction/assembly design; this is the
operational QA loop that sits on top of it.

## The core problem: what's the oracle?

South Mountain worked because a **local** (the repo owner) could look at the
output and say "Bursera's missing," "National should be one trail," "that's
a road, not a trail." **You will not have a local for most areas.** So the
eval strategy has to change as we scale. There are two oracles; use whichever
you have:

1. **The golden suite (portable, no local needed).** `golden/golden.json`
   holds ~20 world-famous trails with known destination coords + expected
   lengths from public sources. In an area that contains one, `make golden`
   objectively asserts reach + length + fragmentation. **This is the eval
   that scales.** Prefer new areas that light up a golden trail so you get
   pass/fail for free.
2. **A knowledgeable human (only where you have one).** For an area nobody
   on the team knows, you *cannot* judge completeness by eye. Don't pretend
   to. Fall back to the golden suite + the structural checks below, and mark
   completeness as "unverified" rather than asserting it.

**Corollary:** the golden suite is the completeness oracle for scale. If we
want confidence in a new region, the highest-leverage move is often to *add
a golden trail there* first, not to hand-inspect the whole output.

## Order of operations (found the hard way)

1. **Cut the AOI bbox with generous padding.** Bursera Canyon went "missing"
   only because a tight east edge clipped it out of the extract. Set `BBOX`
   in the `make aoi` call (`lon_min,lat_min,lon_max,lat_max`) so the box
   comfortably contains the *entire* area boundary polygon plus a margin — a
   "missing" trail is outside the cut until proven otherwise. Confirm the
   PBF's fileinfo bbox actually surrounds the whole park.
2. **Assemble, then run the once-over dump.** This catches the **identity**
   layer — everything text-visible: name duplicates (`X` vs `X Trail`),
   `CLOSED` flags, sub-length stubs, obviously-wrong names. Sort by length
   and skim.
3. **Then open the viewer and *look*.** This catches the **spatial** layer —
   everything the dump is blind to: roads mis-tagged as trails, boundary
   straddlers, trails that stop short, legit trails the clip trimmed too far.
   **This pass is not optional and cannot be replaced by the dump.** At South
   Mountain the two worst defects (a two-lane sand road named after the park;
   a "trail" that was half a neighborhood sidewalk) were invisible in the
   text dump and obvious on the map.
4. **Get an objective verdict.** `make golden` for any golden trails in the
   area; the local's completeness read if you have one; the structural checks
   below if you don't.

## Operational pre-flight (do once, up front)

These ate real time at South Mountain. Front-load them.

- **Browse the viewer at the box's LAN IP in a Chromium browser**
  (`http://<box-ip>:8000/viewer/?aoi=<name>`). Firefox renders the raster
  basemap blank (a known WebGL/raster bug); Edge/Chrome are fine. Don't
  `ssh -L` a tunnel to the box and then browse — the server already binds all
  interfaces; a tunnel just causes port confusion.
- **After every `git pull`, re-run `make assemble` before reloading.** The
  viewer reads the on-disk geojson; stale data reads as "nothing changed."
- **Uncheck the grey "OSM raw" layer** in the viewer once you're looking at
  assembled trails — it's the unfiltered everything-layer and looks like the
  pipeline did nothing.
- `git pull` from the **repo root**, not a subdirectory.

## Triage before touching the code

Every "why isn't trail X here?" at South Mountain first read as "our
assembler dropped it" — and was almost always something else. **Diagnose with
data before editing `model.py`.** Walk this in order:

1. **Is it in the extract at all?** Grep the AOI PBF / check `coverage`
   stats. `raw = 0` means bbox or prefilter, not assembly (→ Bursera).
2. **Is OSM itself wrong?** A duplicate way traced twice (→ Tondum/Thondum),
   a road mis-tagged as a trail (→ the sand road), a bad name. If so, the fix
   is *upstream in OSM* — **do not** add fuzzy-matching to paper over it; that
   risks fusing genuinely distinct trails.
3. **Is it a boundary artifact?** A straddler clipped out, a sliver dropped by
   the floor (→ DC-Ray). That's the `--only-area` clip, not the parser.
4. **Only now consider the assembler.** Reach for `model.py` last.

## When to stop tuning and re-model

DC-Ray took three tries — point-in-polygon → majority (0.5) → gap-fit (0.25)
→ **clip**. Each threshold "worked" for the case in front of us and broke on
the next. That thrash was the signal: **a straddling trail isn't a magnitude
to tune, it's a structure to model** (an in-park connector fused to an
out-of-park access tail). **Rule: if you adjust the same constant more than
once for the same class of bug, stop and re-model.** The clip dissolved the
threshold question entirely.

Also: **verify your own fixtures and estimates.** A wrong seeded golden
coordinate manufactured a fake Devils Bridge failure; a back-of-envelope
"53% inside" was really 44% and changed the decision. Check the fixture and
the estimate against a real computed value before acting on a red.

## Structural checks (no local required)

Cheap, portable sanity checks that flag likely defects anywhere:

- **Exact-duplicate geometry** — two trails whose lines overlap ~100% (→ an
  OSM double-trace like Tondum/Thondum). Flag, don't auto-merge.
- **Road-like tracks** — `_road_like_track` already demotes `motor_vehicle`,
  `motorcar`, `lanes>=2`, and road-name-word tracks. New areas may surface
  new road idioms; extend the guard, don't special-case names.
- **Boundary slivers** — a clipped remnant near `MININ`; usually a trail that
  only grazes the park.
- **`coverage` block** in the geojson — compare `raw_trailish_ways` /
  `named_trailish_ways` / route-relation counts against expectation to tell
  "OSM is thin here" apart from "we dropped things."

## Knobs (all per-area, no code change needed)

- `NAME=<slug>` `AREA="<name substr>"` — the AOI slug and the boundary to
  clip to (substring, case-insensitive, unions all matches).
- `BBOX=lon_min,lat_min,lon_max,lat_max` (on `make aoi`) — pad generously.
- `MINLEN=0.1` — drop assembled trails shorter than this (full length, miles).
- `MININ=0.05` — drop a clipped trail whose in-park remnant is shorter than
  this (boundary-sliver guard).

## What a new area tends to *teach*

Each area exposes the next structural refinement, not just data to clean.
South Mountain forced boundary clipping. The two things still deferred in
`SPEC.md` §6b are the likely next teachers — expect them when the new area
has these shapes:

- **Adjacent areas that share trail names** (two "Loop Trail"s in two parks) →
  forces **per-area merge** (merge same-name *within* an area, not AOI-wide).
- **A long thru-route crossing the park** (a county/regional route like
  Maricopa) → forces **long-route suppression** in a park view.

Treat the first genuinely weird thing a new area shows you as the teacher for
the next model fix — not as a threshold to tune.
