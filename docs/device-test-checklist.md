# Device Test Checklist

On-device verification pass for each TestFlight build. Things that can't
be caught by CI (CI only compiles + runs unit tests — no real GPS, no
device UI). Reset the checkboxes when a new build lands.

## Current build

### Recording — pace / ETA (the ms→s fix, #214)
- [x] Live **Pace** fills within ~30–60 s of moving. *(Confirmed — but
  values were truncating; fixed, see below.)*
- [ ] **ETA** line appears in trail mode (start a hike bound to a trail).
- [ ] Live elevation strip renders during recording.
- [ ] ⚠️ **Recording stats no longer clip** — Distance / Duration / Pace
  show full values (`0.05 mi`, `25:41 /mi`), not `0.05…` / `25:4…`.
  *(Fix in this PR — verify on the next build.)*

### Units toggle (#213) — flip Settings → Display → Units → Metric
- [ ] Area sheet header total switches mi → km.
- [ ] Settings → Your Activity "miles" stat → "km".
- [ ] Hike detail distance → km.
- [ ] Shared hike card distance → km (hike detail → share).

### Area header (#213)
- [ ] Header is one line: `48 trails · 104.6 mi · 5 of 48 completed`.
- [ ] Line turns green when the area is 100% complete.
- [ ] No OSM caption in the header (it lives in Settings → About).

### App Store changes
- [ ] Account deletion: Settings → Account → Delete Account → dialog says
  data is kept → confirm → signed out, hikes/Dex intact.
- [ ] OSM credit in Settings → About (taps to the OSM copyright page).
- [ ] Privacy Policy → trekdex.app/privacy-policy; Terms → …/terms-of-service.
- [ ] First location prompt is "While Using the App" only (no "Always").
- [ ] Background recording: lock screen mid-hike → track stays continuous.
  *(Confirmed on build 197.)*

### Analytics (PostHog)
- [ ] Events land from this build under the stable device person id, on
  the real build number (not `1` — those were CI simulators, fixed #216).
- [ ] Send Feedback → arrives as a `feedback_submitted` event with the
  message.

### Regression smoke
- [ ] Record → save a hike. Dex tab. Stats tab. Export (substantial file).

## Known this build
- **Recording stats truncation** — Distance/Pace clipped to `…` once the
  Pace column was added; the three columns overflowed the row. Fixed by
  distributing columns evenly + shrink-to-fit values (this PR). Re-verify
  next build.
