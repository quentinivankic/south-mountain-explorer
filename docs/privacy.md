---
layout: default
title: TrekDex Privacy Policy
---

# TrekDex Privacy Policy

**Last updated: 2026-05-18**

TrekDex is a trail-completionist hiking companion built and maintained by Quentin Ivankic. This policy explains what data the app handles, where it lives, and what we share (spoiler: nothing leaves your phone unless you explicitly export it).

## What we collect

- **Precise location** — used to show your position on the map, snap your recorded path to nearby trails, and compute distance / elevation stats during a hike. Location is read only while the app is open or while a recording is in progress.

That's it. No analytics, no advertising IDs, no crash-reporting third parties, no telemetry.

## Where it lives

All data stays on your device, inside the app's sandboxed container:

- **UserDefaults** — coverage state, completion timestamps, location-permission status, in-progress recording snapshot.
- **Documents directory** — `hike-history.json` (every saved hike with its GPS path), per-area cached geometries downloaded from our public CDN.
- **Caches directory** — temporary diagnostics bundle artifacts (only when you tap "Send Diagnostics").

Deleting the app deletes all of this. There is no cloud sync, no account system, no server-side copy.

## What we share

Nothing — by default. Two opt-in actions can leave the device:

1. **Send Diagnostics** (Settings → Send Diagnostics) — emails us a zip containing recent activity log entries, app version, and coverage state summary. The bundle does NOT include your GPS path data. You see and approve the email before it sends.
2. **Export GPX** (hike summary → Share) — produces a standard GPX file you can save to Files or share via any iOS share-sheet target. We never see it.

## Third-party services

None. TrekDex includes no third-party SDKs (no Firebase, no analytics, no ad networks, no crash reporters). Map tiles and trail geometry come from public CDNs (Apple Maps for the basemap; our Cloudflare R2 bucket for trail data) — those services see standard HTTPS request metadata but nothing tied to your identity.

## Children

TrekDex is not directed at children under 13 and does not knowingly collect data from them.

## Your rights

All your data is on your device. You can delete it any time by deleting the app. There is no account to close, no server to email.

## Changes to this policy

We'll update the "Last updated" date at the top whenever this policy materially changes. If you have a copy of TrekDex installed and the app's data-handling behavior ever expands, that change will require a TestFlight or App Store update — which gives you the chance to review what's new.

## Contact

Questions about this policy? Email quentinivankic@gmail.com.
