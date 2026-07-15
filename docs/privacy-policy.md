# TrekDex — Privacy Policy

**Last updated: 2026-07-15**

> Canonical source for the policy published at
> **https://trekdex.app/privacy-policy**. Edit here, then publish. Keep the
> "Last updated" date in sync with the live page and with the App Store
> Connect **App Privacy** answers (see `app-store-submission.md`).
>
> **Corrections in this revision (vs the June 30, 2026 live version):**
> 1. The app now uses **PostHog** analytics + **MetricKit** crash counts and
>    an in-app **feedback/waitlist** form — the old "we use no analytics /
>    collect nothing" language was no longer accurate (and an App Review
>    risk).
> 2. Removed the **iCloud/CloudKit cross-device sync** description — that
>    feature is not built (no CloudKit code or entitlement; the in-app Backup
>    screen says "Cloud sync is coming in a future update"). Sign in with
>    Apple is local-only.
>
> **Confirm before publishing:** the operator is named **Trekdex LLC**. v1
> ships under an **Individual** Apple Developer account, so the App Store
> *seller name* will be a personal legal name — make sure you're comfortable
> with the policy naming the LLC as operator while the listing shows a
> person. Not legal advice.

Trekdex LLC ("Trekdex," "we," "us," or "our") operates the Trekdex mobile
application (the "App"). This Privacy Policy explains what information the
App handles and how.

**The short version:** Trekdex keeps your hiking data on your device. We do
not operate a backend server that stores your hikes, routes, or location —
those never leave your device. The only information that leaves your device
is a small amount of **anonymous** usage and crash data (via PostHog, our
analytics provider), plus anything you choose to send us through the in-app
feedback form or region waitlist. We do not track you and we do not sell
your data.

## Who we are

Trekdex LLC
quentin@trekdex.app

## How your data is handled

### Location data

Trekdex uses your device's GPS to record hikes locally on your device while
you are actively recording. This location data is processed entirely on your
device. We do not operate a server that receives, collects, or stores your
location or hike routes.

Tracking continues if you switch to another app or lock your phone during an
active recording session, so your route isn't cut short, and stops when you
end the recording. You can deny or revoke location permissions at any time in
your device's Settings; doing so will prevent hike recording and
area-completion features from working, since they depend on location data.

### Sign in with Apple

If you choose to sign in with your Apple Account, Trekdex stores only an
**anonymous Apple user identifier** in your device's Keychain to keep you
signed in. We do **not** receive or store your name or email address from
Apple. You are not required to sign in to use the App.

Trekdex does **not** currently sync your data across devices — your hikes and
progress live on the device that recorded them. (Cloud sync is planned for a
future update; if and when it ships, this policy will be updated to describe
it.) You can back up your data using your device's standard iCloud Backup,
which is operated by Apple under Apple's privacy practices
(apple.com/legal/privacy) — we are not involved in and cannot see that
backup.

### Analytics and crash data (PostHog)

To understand which features are used and to fix stability problems, the App
sends a small amount of data to **PostHog**, a third-party analytics provider
(data processed in the **United States**):

- **Anonymous usage events** — e.g. that a feature was opened. These are
  bucketed and **not linked to your identity**; we never call an identify
  API, so PostHog only ever sees anonymous events.
- **Crash and hang counts** — derived from Apple's MetricKit. We send only a
  **count**, never the crash log or its contents.

See PostHog's privacy policy at https://posthog.com/privacy. PostHog's SDK
ships its own privacy manifest for any identifiers/diagnostics it adds.

### Trail and area map data

To display trails and geographic areas, the App requests general map/trail
data from our content delivery provider (Cloudflare) and from
OpenStreetMap-based lookup services (such as Overpass and Nominatim). These
requests carry map coordinates only — they do not include your identity or
information that identifies you personally. Trail geometry is derived from
OpenStreetMap, © OpenStreetMap contributors (ODbL).

### Information you provide directly

- **Hike names and notes** you enter are stored locally on your device. We do
  not have access to this content.
- **Feedback and trail reports.** If you use "Send Feedback" or "Report a
  problem with this trail," we receive the message you write. You may
  **optionally** include an email address if you'd like a reply; if you
  don't, your feedback is sent without it.
- **Region waitlist.** If your region isn't yet supported and you join the
  waitlist, we receive your country and the email address you provide, so we
  can let you know when the App is available where you are.

## What we don't do

- We do **not** operate a backend database of your hikes, routes, or
  location.
- We do **not** collect or store your location or hike history off your
  device.
- We do **not** track you — no advertising identifier (IDFA), no cross-app or
  cross-site tracking, no data brokers.
- We do **not** sell your personal information, and we do **not** share your
  data with third parties for advertising.

## Data retention and deletion

- **On-device data** (hikes, progress) is yours — delete it in-app via
  **Reset All Progress**, or by deleting the App.
- **Sign in with Apple**: **Settings → Account → Delete Account** removes the
  stored Apple identifier from your device.
- **Analytics** are anonymous and aggregated, so there is no per-user
  analytics record for us to look up or delete. If you submitted feedback
  with an email and want it removed, contact us at quentin@trekdex.app.

## Your choices and rights

- **Location permissions:** manage anytime in your device Settings.
- **Feedback email:** optional — omit it and we have no way to contact or
  identify you.

Because we hold almost no personal data, most requests under frameworks like
the GDPR or CCPA are satisfied by the on-device controls above. For anything
else, contact us below.

## Children's privacy

Trekdex is not directed at children under 13, and we do not knowingly collect
personal information from them.

## Changes to this policy

We may update this Privacy Policy if how the App handles data changes — for
example, if we introduce cloud sync or a backend service in the future. If we
make material changes, we will notify you through the App or by updating the
"Last updated" date above.

## Contact us

If you have questions about this Privacy Policy, contact us at
quentin@trekdex.app.

© 2026 Trekdex LLC
