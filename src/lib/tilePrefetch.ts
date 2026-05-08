// Offline map tile prefetching using the Cache Storage API.
// No service worker required — a custom Leaflet TileLayer (TrailMap.tsx)
// checks this same cache before hitting the network at runtime.

import { startDownload, updateDownload, finishDownload, failDownload } from "./downloads";

export const TILE_CACHE = "summit-tiles-v1";

// Match the URL template used by TrailMap.tsx <TileLayer>
// (subdomain {s} cycles through a-c-d on CARTO).
function tileUrl(z: number, x: number, y: number): string {
  const sub = ["a", "b", "c", "d"][(x + y) % 4];
  // Use 1x (no @2x) for prefetch to keep size sane.
  return `https://${sub}.basemaps.cartocdn.com/rastertiles/voyager/${z}/${x}/${y}.png`;
}

// Lon/lat → tile xy
function lonToX(lon: number, z: number) {
  return Math.floor(((lon + 180) / 360) * 2 ** z);
}
function latToY(lat: number, z: number) {
  const r = (lat * Math.PI) / 180;
  return Math.floor(((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2) * 2 ** z);
}

export interface Bbox {
  minLat: number;
  minLon: number;
  maxLat: number;
  maxLon: number;
}

function tilesForBbox(bbox: Bbox, minZ: number, maxZ: number): Array<[number, number, number]> {
  const out: Array<[number, number, number]> = [];
  for (let z = minZ; z <= maxZ; z++) {
    const xMin = lonToX(bbox.minLon, z);
    const xMax = lonToX(bbox.maxLon, z);
    const yMin = latToY(bbox.maxLat, z);
    const yMax = latToY(bbox.minLat, z);
    for (let x = xMin; x <= xMax; x++) {
      for (let y = yMin; y <= yMax; y++) {
        out.push([z, x, y]);
      }
    }
  }
  return out;
}

async function cacheOpen(): Promise<Cache | null> {
  if (typeof caches === "undefined") return null;
  try {
    return await caches.open(TILE_CACHE);
  } catch {
    return null;
  }
}

/**
 * Prefetch every tile that overlaps the area's bbox for zooms minZ..maxZ.
 * Reports progress via the download manager. Idempotent — already-cached
 * tiles are skipped.
 */
export async function prefetchAreaTiles(opts: {
  areaId: string;
  areaName: string;
  bbox: Bbox;
  minZ?: number;
  maxZ?: number;
}) {
  const minZ = opts.minZ ?? 8;
  const maxZ = opts.maxZ ?? 14;
  const cache = await cacheOpen();
  if (!cache) return;
  const tileCache: Cache = cache;

  const tiles = tilesForBbox(opts.bbox, minZ, maxZ);
  // Safety: cap absurdly large bboxes (>4000 tiles) — skip top zoom.
  const capped = tiles.length > 4000 ? tilesForBbox(opts.bbox, minZ, maxZ - 1) : tiles;

  const id = `tiles:${opts.areaId}`;
  startDownload({ id, kind: "tiles", label: opts.areaName, total: capped.length });

  let done = 0;
  let errors = 0;
  // Limit concurrency to be polite to the tile host.
  const CONCURRENCY = 6;
  let cursor = 0;

  async function worker() {
    while (cursor < capped.length) {
      const i = cursor++;
      const [z, x, y] = capped[i];
      const url = tileUrl(z, x, y);
      try {
        const existing = await tileCache.match(url);
        if (!existing) {
          const res = await fetch(url, { mode: "cors" });
          if (res.ok) await tileCache.put(url, res.clone());
          else errors++;
        }
      } catch {
        errors++;
      } finally {
        done++;
        if (done % 8 === 0 || done === capped.length) {
          updateDownload(id, done);
        }
      }
    }
  }

  await Promise.all(Array.from({ length: CONCURRENCY }, worker));

  if (errors > capped.length / 2) {
    failDownload(id, `${errors} tiles failed`);
  } else {
    finishDownload(id);
  }
}

/** Try to serve a tile URL from the cache; null if missing. */
export async function getCachedTile(url: string): Promise<Blob | null> {
  const cache = await cacheOpen();
  if (!cache) return null;
  const hit = await tileCache.match(url);
  if (!hit) return null;
  try {
    return await hit.blob();
  } catch {
    return null;
  }
}

/** Write a tile fetched at runtime into the cache. */
export async function storeTile(url: string, response: Response) {
  const cache = await cacheOpen();
  if (!cache) return;
  try {
    await cache.put(url, response.clone());
  } catch {
    /* ignore */
  }
}
