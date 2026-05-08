// Lazy elevation lookups via open-meteo (free, no key, 100 points/req).
// Results cached in-memory + sessionStorage so we don't re-hit the API.

const CACHE_KEY = "summit:elev-cache";
type Cache = Record<string, number>; // "lat,lon" -> meters
let mem: Cache | null = null;

function load(): Cache {
  if (mem) return mem;
  if (typeof sessionStorage === "undefined") return (mem = {});
  try {
    mem = JSON.parse(sessionStorage.getItem(CACHE_KEY) || "{}");
  } catch {
    mem = {};
  }
  return mem!;
}

function save() {
  if (typeof sessionStorage === "undefined" || !mem) return;
  try {
    sessionStorage.setItem(CACHE_KEY, JSON.stringify(mem));
  } catch {
    // quota — ignore
  }
}

function k(lat: number, lon: number) {
  return `${lat.toFixed(4)},${lon.toFixed(4)}`;
}

/** Look up elevations (m) for many points, batching ~100 per request. */
export async function elevationsFor(
  points: [number, number][],
): Promise<(number | null)[]> {
  const cache = load();
  const result: (number | null)[] = points.map(
    ([la, lo]) => cache[k(la, lo)] ?? null,
  );
  const missingIdx: number[] = [];
  points.forEach((_, i) => {
    if (result[i] == null) missingIdx.push(i);
  });
  if (missingIdx.length === 0) return result;

  const BATCH = 90;
  for (let i = 0; i < missingIdx.length; i += BATCH) {
    const slice = missingIdx.slice(i, i + BATCH);
    const lats = slice.map((j) => points[j][0].toFixed(4)).join(",");
    const lons = slice.map((j) => points[j][1].toFixed(4)).join(",");
    try {
      const res = await fetch(
        `https://api.open-meteo.com/v1/elevation?latitude=${lats}&longitude=${lons}`,
      );
      if (!res.ok) continue;
      const json = (await res.json()) as { elevation?: number[] };
      const elevs = json.elevation ?? [];
      slice.forEach((origIdx, k2) => {
        const m = elevs[k2];
        if (typeof m === "number") {
          result[origIdx] = m;
          cache[k(points[origIdx][0], points[origIdx][1])] = m;
        }
      });
    } catch {
      // network — leave nulls
    }
  }
  save();
  return result;
}

/** Sum positive deltas along a sequence of elevations (meters). */
export function gainFromElevations(elevs: (number | null)[]): number {
  let gain = 0;
  let prev: number | null = null;
  for (const e of elevs) {
    if (e == null) {
      prev = null;
      continue;
    }
    if (prev != null) {
      const d = e - prev;
      if (d > 1) gain += d; // ignore <1m noise
    }
    prev = e;
  }
  return gain;
}
