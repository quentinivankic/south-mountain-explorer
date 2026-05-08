## Quick answer

No — right now downloads are silent. Toggling a favorite triggers `downloadFavorites()` in the background with no UI feedback. I'll add a visible progress system, then layer Phase 3 (Protomaps tiles) on top so map tiles also download with progress.

---

## What I'll build

### 1. Download manager (foundation for both)

**New: `src/lib/downloads.ts`**
- Tiny pub/sub store: `{ id, label, kind: "trails" | "tiles", total, done, status: "running" | "done" | "error" }`.
- Exports `startDownload()`, `updateDownload()`, `finishDownload()`, and a `useDownloads()` hook.
- Auto-removes finished entries after ~3s so the pill clears itself.

**New: `src/components/DownloadToasts.tsx`** — mounted in `__root.tsx`.
- Fixed bottom-center pill, stacks one row per active download:
  - Trail data: "Saving Yosemite Valley · trails"
  - Tiles: "Saving Yosemite Valley · map · 134 / 512"
- Thin progress bar + check on done, error state on failure.

### 2. Wire trail downloads into the manager

In `src/lib/favorites.ts → downloadFavorites()`:
- Look up the area's display name from the slim index.
- `startDownload({ kind: "trails", label: area.name })` before `refreshArea`.
- For trails we don't get byte progress, so it's an indeterminate spinner that resolves to "Saved".

### 3. Phase 3 — Protomaps hosted tiles

**Swap basemap source.** Replace the CARTO raster `<TileLayer>` in `src/components/TrailMap.tsx` with vector tiles via `protomaps-leaflet`:
- `bun add protomaps-leaflet`
- Use Protomaps' hosted XYZ endpoint with a public API key (key is meant to be public, restricted by referrer).
- Drop in their default `light` theme so the map keeps its current calm look.

**Offline caching with Cache Storage.**
- Register a tiny service worker (`public/sw.js`) that intercepts requests to `api.protomaps.com` and serves cache-first, falling back to network and writing through.
- On favorite-add, prefetch the area's bbox tiles for zooms 8–14 (≈4–10 MB per Yosemite-sized area as discussed) into the same cache bucket. Each tile fetch updates the download manager so the user sees `134 / 512`.
- On unfavorite, evict that area's tile keys.

**Shape: typical Yosemite-sized prefetch ≈ 600 tiles, ~6 MB.**

### 4. API key

Protomaps needs an API key (free tier covers everything we need). The key is **public** — it lives in client code and is rate-limited by HTTP referrer, not kept secret. I'll add it as `VITE_PROTOMAPS_KEY` in `.env` once you give me one from https://protomaps.com (sign in, create a key, restrict to your Lovable preview + custom domain).

If you'd rather I just stub it for now and you'll paste the key in later, I can do that too.

---

## Files

- **New**: `src/lib/downloads.ts`, `src/components/DownloadToasts.tsx`, `src/lib/tilePrefetch.ts`, `public/sw.js`
- **Edited**: `src/lib/favorites.ts`, `src/components/TrailMap.tsx`, `src/routes/__root.tsx`, possibly `src/main.tsx` (SW register)

---

## One question before I start

Do you have a Protomaps API key handy, or should I scaffold with a placeholder constant you'll fill in?