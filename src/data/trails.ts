// Trails are stored as static JSON in /public/areas/, fetched on demand,
// and cached in IndexedDB so visited areas work offline.
// Index is fetched once on app start (also cached).

export type Difficulty = "Easy" | "Moderate" | "Hard";

export interface Trail {
  id: string;
  name: string;
  distanceMi: number;
  difficulty: Difficulty;
  segments: [number, number][][];
}

export interface AreaSummary {
  id: string;
  name: string;
  subtitle: string;
  location: string;
  center: [number, number];
  zoom: number;
  trailCount: number;
  totalMi: number;
  /** Optional bbox [s,w,n,e] used by on-demand trail fetcher. */
  bbox?: [number, number, number, number];
  /** OSM relation name — used to fetch trails on demand for areas
   * discovered from OSM that don't ship as static JSON. */
  osmRelation?: string;
}

export interface Area extends AreaSummary {
  trails: Trail[];
}

// ---------- IndexedDB cache ----------
const DB_NAME = "summit-trails";
const STORE = "json";
const VERSION = 1;

function openDB(): Promise<IDBDatabase | null> {
  if (typeof indexedDB === "undefined") return Promise.resolve(null);
  return new Promise((resolve) => {
    const req = indexedDB.open(DB_NAME, VERSION);
    req.onupgradeneeded = () => req.result.createObjectStore(STORE);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => resolve(null);
  });
}

async function idbGet<T>(key: string): Promise<T | null> {
  const db = await openDB();
  if (!db) return null;
  return new Promise((resolve) => {
    const tx = db.transaction(STORE, "readonly").objectStore(STORE).get(key);
    tx.onsuccess = () => resolve((tx.result as T) ?? null);
    tx.onerror = () => resolve(null);
  });
}

async function idbPut(key: string, value: unknown): Promise<void> {
  const db = await openDB();
  if (!db) return;
  return new Promise((resolve) => {
    const tx = db.transaction(STORE, "readwrite");
    tx.objectStore(STORE).put(value, key);
    tx.oncomplete = () => resolve();
    tx.onerror = () => resolve();
  });
}

async function fetchJSON<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} -> ${res.status}`);
  return (await res.json()) as T;
}

// ---------- Index ----------
let indexCache: AreaSummary[] | null = null;
let indexPromise: Promise<AreaSummary[]> | null = null;

export function getCachedAreas(): AreaSummary[] {
  return indexCache ?? [];
}

export async function loadAreas(): Promise<AreaSummary[]> {
  if (indexCache) return indexCache;
  if (indexPromise) return indexPromise;
  indexPromise = (async () => {
    // Fast path: cached copy from previous session.
    const cached = await idbGet<AreaSummary[]>("index");
    if (cached) {
      indexCache = cached;
      // Refresh in background.
      fetchJSON<AreaSummary[]>("/areas/index.json")
        .then((fresh) => {
          indexCache = fresh;
          idbPut("index", fresh);
        })
        .catch(() => {});
      return cached;
    }
    const fresh = await fetchJSON<AreaSummary[]>("/areas/index.json");
    indexCache = fresh;
    idbPut("index", fresh);
    return fresh;
  })();
  return indexPromise;
}

export async function getAreaSummary(
  id: string,
): Promise<AreaSummary | undefined> {
  const list = await loadAreas();
  return list.find((a) => a.id === id);
}

// ---------- Per-area trails ----------
export async function loadArea(id: string): Promise<Area | undefined> {
  const cacheKey = `area:${id}`;
  const cached = await idbGet<Area>(cacheKey);
  if (cached) {
    // Refresh static file in background — silently skip on failure (offline ok).
    fetchJSON<Area>(`/areas/${id}.json`)
      .then((fresh) => idbPut(cacheKey, fresh))
      .catch(() => {});
    return cached;
  }
  // 1. Try the prebuilt static file.
  try {
    const fresh = await fetchJSON<Area>(`/areas/${id}.json`);
    idbPut(cacheKey, fresh);
    return fresh;
  } catch {
    // 2. Fall back to fetching from OSM via server function.
    const summary = await getAreaSummary(id);
    if (!summary) return undefined;
    if (!summary.osmRelation && !summary.bbox) return undefined;
    try {
      const { fetchAreaTrails } = await import("@/lib/trailFetch.functions");
      const { trails, trailCount, totalMi } = await fetchAreaTrails({
        data: {
          relation: summary.osmRelation,
          bbox: summary.bbox,
        },
      });
      const area: Area = { ...summary, trailCount, totalMi, trails };
      idbPut(cacheKey, area);
      // Also patch the cached index so cards show counts next time.
      if (indexCache) {
        const i = indexCache.findIndex((a) => a.id === id);
        if (i >= 0) {
          indexCache[i] = { ...indexCache[i], trailCount, totalMi };
          idbPut("index", indexCache);
        }
      }
      return area;
    } catch (e) {
      console.error("on-demand trail fetch failed", e);
      return undefined;
    }
  }
}

