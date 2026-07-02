# Analytics + Feedback Pipelines (PostHog)

How usage analytics, in-app feedback, and crash capture get off the
device. TrekDex has always been backend-less; this is the first data
egress, so read the **Privacy** section before shipping any of it.

## Decisions (locked)

- **Analytics:** PostHog (third-party SDK).
- **Feedback:** PostHog too — no separate backend. Either a custom
  in-app form that captures a `feedback_submitted` event, and/or
  PostHog Surveys. We build the custom form (always-available "Send
  Feedback" in Settings); Surveys stay available for targeted prompts
  later.
- **Crashes:** PostHog error tracking is weak (no dSYM symbolication),
  so pair it with **MetricKit** (Apple first-party, on-device) for real
  crash/hang/performance payloads.

One SDK covers analytics + feedback; MetricKit covers crashes. No
Cloudflare Worker / D1 needed after all.

## Architecture — swappable backend

Call sites NEVER import PostHog. They talk to a thin facade:

```
AnalyticsService.shared.capture(.hikeSaved(...))
          │
          ▼
   AnalyticsBackend (protocol)
     ├─ NoopAnalyticsBackend   ← default; ships until a key is set
     └─ PostHogBackend         ← wraps PostHogSDK, added in Phase 3
```

Benefits: the app compiles + tests green with no SDK dependency, PostHog
is confined to one file, and swapping vendors later touches one file.

## Rollout phases

- **Phase 1 (this PR) — foundation, no SDK, no key.**
  - `Services/AnalyticsService.swift`: the facade singleton +
    `AnalyticsBackend` protocol + `NoopAnalyticsBackend` +
    `AnalyticsEvent` type with typed factories (stable snake_case
    names).
  - Tests pinning event names/properties.
  - Nothing collected yet — privacy declarations UNCHANGED.

- **Phase 2 — instrument + feedback UI (no key needed).**
  - Call `AnalyticsService.shared.capture(...)` at the meaningful sites
    (mirrors the existing `ActivityLogService.log` sites): app launch,
    area opened, hike started/saved/discarded, trail completed, area
    100%, Dex opened, units/theme changed, export/import, data download.
  - `Views/Settings/FeedbackView.swift`: a "Send Feedback" form
    (category picker + free text + optional email) that captures
    `feedback_submitted`. Replaces the current TestFlight-only hint in
    Settings → Feedback (that copy is dead once we're on the App Store).
  - Still routes to the no-op backend, so still no egress — safe to
    ship ahead of the key.

- **Phase 3 — turn PostHog on (needs the key).** Requires: PostHog
  **project API key** (client-side, safe to ship) + **region host**
  (`us.i.posthog.com` or `eu.i.posthog.com`).
  - Add `posthog-ios` via SPM in `ios/project.yml`.
  - `Services/PostHogBackend.swift` conforming to `AnalyticsBackend`.
  - Configure at launch: replay OFF, autocapture of PII OFF, person
    profiles as decided. Inject key via build config (not hardcoded).
  - `Services/CrashReporter.swift`: MetricKit `MXMetricManager`
    subscriber; forward crash/hang diagnostics as events.
  - **Privacy updates land in this same PR** (see below).

## Privacy — MUST change when Phase 3 lands

Today everything is "Data Not Collected" (`PrivacyInfo.xcprivacy` empty,
ASC nutrition label "No", policy says nothing leaves the device). PostHog
+ feedback change that:

- **`PrivacyInfo.xcprivacy`** — add collected types: Product Interaction
  / Usage Data, Crash Data + Performance Data (MetricKit), "Other User
  Content" (feedback text) and Contact Info (feedback email, if given).
  PostHog's SDK also ships its own bundled privacy manifest.
- **Tracking:** keep `NSPrivacyTracking` false as long as we don't use
  data for cross-app tracking / IDFA and don't share with data brokers.
  Configure PostHog accordingly (no ad IDs).
- **ASC App Privacy nutrition label** — flip to "Yes", declare the above
  as Not Linked / Not Used for Tracking, purpose = Analytics + App
  Functionality. (You do this; draft to follow in
  `app-store-submission.md`.)
- **Privacy policy** — add what's collected, why, the processor
  (PostHog + region), and retention. (You do this on trekdex.app.)

Pick the **data region** deliberately — it goes in the policy.

## Event taxonomy (initial)

snake_case, no coordinates ever, string properties only (mirrors
ActivityLogService's privacy discipline). See `AnalyticsEvent` factories
for the source of truth.

| Event | Properties |
|---|---|
| `app_launched` | `build` |
| `area_opened` | `area_id` |
| `hike_started` | `area_id`, `mode` (trail/roam) |
| `hike_saved` | `area_id`, `distance_bucket`, `duration_bucket`, `mode` |
| `hike_discarded` | `area_id` |
| `trail_completed` | `area_id` |
| `area_completed` | `area_id` |
| `dex_opened` | `area_id` |
| `units_changed` | `value` (imperial/metric) |
| `theme_changed` | `value` |
| `data_exported` | — |
| `data_imported` | — |
| `feedback_submitted` | `category`, `has_email` (`true`/`false`) |

Note: distance/duration are **bucketed** (e.g. "1-3mi", "30-60min")
rather than raw, to keep analytics coarse and non-identifying.

## What you provision

1. Create the PostHog project → send the **project API key** + **region**.
2. (Later, optional) Author a PostHog Survey for targeted prompts.
3. Update the ASC nutrition label + trekdex.app privacy policy when
   Phase 3 ships (drafts provided).
