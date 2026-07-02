# Mid-hike recommendations — design notes

Context + findings from a deep read of the recommendation path (as of
#214/#220). Preserved here so the analysis isn't lost. See the TODO's
"Smarter mid-hike recommendations" + "Suggest a hike" items.

## What exists today

Two banners appear above the recording panel while recording (Trails
segment only):

- **Retarget banner** (`RetargetTrailBanner`) — when you manually tap a
  trail different from the one you're recording, offers "Switch." Not
  pace-gated.
- **Suggestion banner** (`SuggestionBanner`) — the proactive one:
  *"Add Alta · 0.2 mi detour, ~6 min."* Driven by
  `TrailSuggestionEngine.candidates` (`Utilities/TrailSuggestion.swift`).

The engine, per trail, filters:
1. not your current trail,
2. not already complete (lifetime coverage ≥ 0.95),
3. within **300 m** detour (perpendicular projection of you onto the
   trail polyline),
4. ≤ **1.5 mi** *uncovered* length remaining (`total × (1 − coverage)`),

then ranks survivors by **extra time** = (detour + remaining) ÷ pace and
shows the top one. It's pure + has 16 unit tests.

## Key finding: it was silently DEAD until #214

The engine's first line is a hard pace gate:
`guard let pace = paceMetersPerSec, pace > 0.1 else { return [] }`.
`smoothedPaceMetersPerSec()` returned **nil on every hike** until the
ms→s fix (#214) — the same bug that killed the live pace field — so the
engine returned an empty list every time and the banner **never
mounted** on builds ≤197. #214 should revive it, but this is **unverified
on device**. (The 16 engine tests are green because they pass pace in
directly; nothing covered the caller feeding nil — green unit tests,
dead integration.)

**Step 1 before any enhancement: verify the banner actually fires on a
#214+ build** (walk 1–2 min near a cluster of incomplete trails).

## Verified NOT broken
- `CoverageService.coverage(for:)` returns **lifetime** coverage (not
  since-completion), so completed trails are correctly skipped — no
  re-suggestion loop.

## Frequency / the caps tradeoff
The 300 m detour + 1.5 mi remaining caps make it fire in a narrow
"trivial add" window (by design). Notes:
- The **300 m detour is the bigger limiter** than the 1.5 mi.
- It **self-adjusts toward area completion**: the 1.5 mi cap is on
  *uncovered* length, so early on (all-unwalked) only short trails
  qualify, but as you cover more of an area more trails drop under
  1.5 mi remaining — chattier exactly when you're near finishing.
- **Density matters**: dense webs (South Mountain, 48 interlacing
  trails) fire far more than sparse areas.
- Both are **per-call parameters** (`maxDetourMeters`,
  `maxRemainingMiles`) — trivially tunable.

Loosening the caps is the **wrong lever**: the engine has no sense of
heading, so more firing = more *irrelevant* pops (incl. trails behind
you). The fix for "often AND relevant" is anchoring to the fork you're
approaching (below).

## Gaps vs the desired experience

Wanted: *"the fork coming up gets you more completion and it's an easy,
flat path."* Missing today:

1. **No terrain/difficulty awareness.** Ranking is purely time;
   `Trail.difficulty` + elevation are ignored. Steep-hard ranks the
   same as flat-easy.
2. **No heading / upcoming-fork awareness.** Projects your *current*
   point onto every trail (perpendicular = detour); no direction of
   travel, no junction detection. A trail behind you scores the same as
   one ahead. "The fork coming up" is impossible with this math.
3. **Ranks by time, not completion value.** Doesn't weight "how much
   completion does this add."

## Directions

- **Cheap knob (stopgap):** bump the caps (detour 300→500 m, remaining
  1.5→3 mi) so it fires more. Throwaway if we do the redesign.
- **Fork-anchored redesign (the real thing):** infer bearing from recent
  path samples (pace works now), detect trail **junctions** (where
  polylines meet), rank trails branching off a point *ahead* of you,
  fold in difficulty (+ later elevation for "flat"), rank by completion
  gained per minute, and richer copy: *"Easy detour — the fork ahead
  finishes Alta (+1 trail, ~6 min, flat)."*

## Sibling feature: "Suggest a hike" (coverage-optimized route)
Distinct from the reactive mid-hike nudges: a **proactive planned route**
built to maximize *area coverage/completion*, not individual trails —
e.g. "This 4.2 mi loop hits 3 uncompleted trails and takes you from
5/48 → 8/48." Needs a trail-graph + route search over uncompleted
segments (a coverage-weighted routing problem), and a way to present +
start it. Bigger than the banner; shares the junction/graph groundwork
the fork redesign needs. See the TODO feature entry.
