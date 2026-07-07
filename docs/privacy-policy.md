# TrekDex Privacy Policy

**Last updated: July 7, 2026**

TrekDex ("we," "us," the "app") is a hiking tracker for iOS. This policy
explains what data the app does and does not collect, who processes it,
and the choices you have. We keep it plain: **almost everything you create
in TrekDex stays on your device.**

## The short version

- Your **hikes, GPS tracks, trail completions, and progress never leave
  your device.** We can't see them.
- **Sign in with Apple** is optional and stored only in your device's
  Keychain. We never receive your name or email through it.
- We collect a small amount of **anonymous** usage and crash data to fix
  bugs and improve the app, plus anything you choose to send us via the
  in-app feedback form.
- We **do not** sell your data, show ads, or track you across other apps
  or websites. There is no advertising identifier (IDFA) use.

## What stays on your device (never collected)

- **Hike recordings and GPS location.** Location is used only while you're
  actively recording a hike, to draw your route and compute distance,
  pace, and elevation. It is stored in the app's local files and is never
  transmitted to us or anyone else.
- **Trail completions, coverage, favorites, and stats.** Computed and
  stored locally.
- **Sign in with Apple credential.** If you sign in, the identifier Apple
  provides is stored only in your device Keychain to keep you signed in.
  We do not send it anywhere and do not maintain any server-side account.

You control this data directly in the app: **Settings → Data → Export** to
back it up, and **Reset All Progress** to erase it. If you signed in,
**Settings → Account → Delete Account** removes the local credential.

## What we collect

We use a single analytics and product-feedback processor, **PostHog**, and
Apple's built-in diagnostics. Data is processed in the **United States**.

| Data | What it is | Why | Identifiable? |
|---|---|---|---|
| **Product interaction** | Anonymous events about which screens and features are used, plus basic technical context (device model, iOS version, app version) | Understand which features matter and find broken flows | No — anonymous; we never link events to you |
| **Feedback message** | The text you type into the in-app feedback form | To read and act on your feedback | Only if you include identifying info in the message |
| **Email address (optional)** | If you add an email to a feedback message, or join the out-of-region waitlist | To reply to you, or to notify you when your region is supported | Provided voluntarily by you |
| **Crash & performance diagnostics** | Crash, hang, and performance reports (via Apple MetricKit) | Diagnose and fix stability problems | No |

- **Analytics are anonymous.** We do not call any "identify" function in
  PostHog, so usage events are not tied to a personal profile, your Apple
  ID, or your hikes.
- **We do not track you.** No IDFA, no cross-app or cross-site tracking,
  no data brokers, no advertising.

## Third parties

- **PostHog** processes the analytics, feedback, and crash data described
  above on our behalf, in the United States. See PostHog's privacy
  practices at https://posthog.com/privacy.
- **Apple** provides Sign in with Apple and MetricKit diagnostics under
  Apple's own privacy terms.
- **Trail map data** is © OpenStreetMap contributors, provided under the
  Open Database License. Map imagery is provided by Apple Maps.

We do not sell or rent your data to anyone.

## Data retention and your choices

- **On-device data** (hikes, progress, credential) stays until you remove
  it with Export/Reset/Delete Account, or delete the app.
- **Analytics and feedback data** held by PostHog is retained only as long
  as needed for the purposes above. Because analytics are anonymous, we
  generally cannot single out an individual's events — but if you've sent
  feedback or joined the waitlist with an email, you can ask us to delete
  that email and message (see Contact).

## Children

TrekDex is not directed to children under 13 and does not knowingly
collect personal information from them. It is rated 4+.

## Changes to this policy

We may update this policy as the app changes. Material changes will be
reflected here with a new "Last updated" date. Continued use of the app
after an update means you accept the revised policy.

## Contact

Questions or requests (including data deletion): **support@trekdex.app**

<!--
PUBLISH CHECKLIST (delete before publishing):
- Set the contact email to a real, monitored inbox and update it above +
  in the app's Support URL.
- If the LLC has a public legal name you want shown, add it near the top
  ("TrekDex is operated by <LLC Legal Name>.").
- Host at https://trekdex.app/privacy-policy so it matches the in-app
  link (SettingsView.swift) and the ASC Privacy Policy URL.
- This must be live and reachable BEFORE you submit — App Review opens it.
-->
