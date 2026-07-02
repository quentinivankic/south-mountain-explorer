# Stability audit — crash-risk + concurrency

Static audits done from the repo to shrink the field-crash surface ahead
of App Store submission (the in-repo half of the "stability pass" item).
A static read can't see runtime/UI/MapKit/SDK crashes — **MetricKit
(#212)** is the field net for those (they surface as `crash_detected`
events in PostHog).

## Crash-risk audit (#222)

Swept app code for the patterns that actually trap in the field:

| Pattern | Result |
|---|---|
| `try!` / `as!` | **none** |
| `fatalError` / `preconditionFailure` | **none** |
| force-unwrap `)!` / `]!` | all literal `URL(string:)!`, guarded subscripts (`if x[k]==nil{…}` above), or safe round-trips (`Double(String(format:))!`) |
| `.first!` / `.last!` / `.min()!` / `.max()!` | all behind `guard count >= …` — **except** the one fixed below |
| `a...b` / `a..<b` ranges | provably `lower ≤ upper` (`PolylineDecimator` guards `end-start>1`; `ElevationStats` clamps `lo≤hi`) |
| integer `/` `%` by zero | `% endpoints.count` is a literal 2-element array; `/lengthSq`, `/segLen` are `==0` guarded; rest are Double (→ inf, not a trap) |

**One fix (#222):** `ElevationProfileView`'s Y axis force-unwrapped
`ticksM.first!...last!`; a hike with NaN/corrupt GPS altitudes yields an
empty tick array → trap. Now routes through a pure, tested
`elevationAxisDomain(ticks:fallbackBase:)` that falls back to a tiny
valid domain. No behavior change for valid data.

## Concurrency / threading audit

Builds under Swift 6 `SWIFT_STRICT_CONCURRENCY: complete`, so the
compiler already rejects most data races. This reviewed the manual
escape hatches. **No races or concurrency crashes found.**

**The model is inherently safe:** every stateful service is
`@MainActor @Observable` (Recording, Coverage, Progress, Favorites,
Location, AreaData, AreaSilhouette, AreaIndex, Activity, ActivityLog,
Auth, Network, Notification, MapDiagnostics, Analytics). All mutable app
state lives on the main actor — compiler-enforced race-free. The only
non-MainActor types are stateless pure `enum`s (`AchievementEngine`,
`PolylineDecimator`, `DiagnosticsService`) + `CrashReporter`.

Escape hatches reviewed:
- **Background-queue delegate callbacks** (CLLocationManager,
  NWPathMonitor, UNUserNotificationCenter, MetricKit) are all
  `nonisolated`, read **Sendable locals** off the callback, then hop via
  `Task { @MainActor in … }` to touch state. Textbook-correct in every
  case (NotificationService even calls its `completionHandler` outside
  the hop, with a comment on the Swift 6 task-isolation reason).
- **`CrashReporter: @unchecked Sendable`** — verified genuinely
  stateless (no stored properties), so the singleton is safe from any
  thread; the `@unchecked` is truthful.
- **`AuthService` continuation** — single-resume guaranteed by
  `ASAuthorizationController`'s contract; `CheckedContinuation` traps a
  double-resume intentionally (surfacing a bug, not hiding one).
- **`nonisolated(unsafe) var delegateKey`** — only its *address* is
  taken (`&delegateKey` for `objc_setAssociatedObject`); the value is
  never read/written. Safe.
- **No `Task.detached`** anywhere — every `Task {}` inherits its actor
  context (View tasks inherit `@MainActor` from SwiftUI; service tasks
  inherit the service's `@MainActor`).
- **Timers** (`RecordingPanel`, `ActiveRecordingBanner`) — the
  `@Sendable` fire closure hops to `Task { @MainActor in … }` before
  touching `@State`.

### Non-crash notes (out of scope, logged for later)
- **Task ordering:** rapid delegate callbacks enqueue multiple
  `Task { @MainActor in }` hops with no strict ordering guarantee —
  worst case a one-frame-stale value (e.g. location). Not a crash.
- **`ActivityLogService.writeNow()`** encodes + writes the log to disk on
  the main actor (inside the debounced task, which inherits `@MainActor`).
  Debounced (1 s) and small (≤5000 entries), but it is main-thread file
  I/O — a candidate to move off-main if drag jank ever resurfaces. Perf,
  not correctness.

## Bottom line
Both the crash-risk and concurrency surfaces are in strong shape — one
real force-unwrap fixed, otherwise clean. The remaining risk is the
runtime/UI class a static pass can't see, covered by the on-device QA
sweep + MetricKit reporting.
