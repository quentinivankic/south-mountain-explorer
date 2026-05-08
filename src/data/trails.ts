// Areas are stored in two places:
//  1. /public/areas/index.json — slim tuple list of every area
//     (~17k entries, ~350 KB gzipped). Cached in IndexedDB.
//  2. Supabase `areas` table — full per-area data (trails, bbox, etc).
//     Fetched on demand via the getAreaData server function and cached
//     in IndexedDB so visited areas work offline.

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
  /** US state — kept under `subtitle` for backwards compatibility with UI. */
  subtitle: string;
  /** Lowercased "name state" string for fast substring search. */
  search: string;
  center: [number, number];
}

export interface Area {
  id: string;
  name: string;
  subtitle: string;
  center: [number, number];
  zoom: number;
  bbox?: [number, number, number, number];
  trails: Trail[];
  trailCount: number;
  totalMi: number;
  cachedAt?: string | null;
}

// Tuple shape coming off the wire: [id, name, state, lat, lon]
type AreaTuple = [string, string, string, number, number];

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

function inflate(t: AreaTuple): AreaSummary {
  return {
    id: t[0],
    name: t[1],
    subtitle: t[2],
    search: `${t[1]} ${t[2]}`.toLowerCase(),
    center: [t[3], t[4]],
  };
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
    const cached = await idbGet<AreaTuple[]>("index-v2");
    if (cached) {
      indexCache = cached.map(inflate);
      // Refresh in background.
      fetchJSON<AreaTuple[]>("/areas/index.json")
        .then((fresh) => {
          indexCache = fresh.map(inflate);
          idbPut("index-v2", fresh);
        })
        .catch(() => {});
      return indexCache;
    }
    const fresh = await fetchJSON<AreaTuple[]>("/areas/index.json");
    indexCache = fresh.map(inflate);
    idbPut("index-v2", fresh);
    return indexCache;
  })();
  return indexPromise;
}

export async function getAreaSummary(
  id: string,
): Promise<AreaSummary | undefined> {
  const list = await loadAreas();
  return list.find((a) => a.id === id);
}

// ---------- Per-area data ----------
/** Read-only IDB lookup — never hits the network. */
export async function getCachedArea(id: string): Promise<Area | undefined> {
  const cached = await idbGet<Area>(`area:${id}`);
  return cached ?? undefined;
}

export async function loadArea(id: string): Promise<Area | undefined> {
  const cacheKey = `area:${id}`;
  const cached = await idbGet<Area>(cacheKey);
  if (cached) {
    // Refresh in background.
    refreshArea(id).catch(() => {});
    return cached;
  }
  return refreshArea(id);
}

export async function refreshArea(id: string): Promise<Area | undefined> {
  try {
    const { getAreaData } = await import("@/lib/areaData.functions");
    const payload = await getAreaData({ data: { id } });
    if (!payload) return undefined;
    const area: Area = {
      id: payload.id,
      name: payload.name,
      subtitle: payload.subtitle,
      center: payload.center,
      zoom: payload.zoom,
      bbox: payload.bbox,
      trails: payload.trails,
      trailCount: payload.trailCount,
      totalMi: payload.totalMi,
      cachedAt: payload.cachedAt,
    };
    idbPut(`area:${id}`, area);
    return area;
  } catch (e) {
    console.error("loadArea failed", id, e);
    return undefined;
  }
}
