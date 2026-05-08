// Cache-through server function: returns trails for an area.
// First checks Supabase `areas.trails` cache; on miss, fetches Overpass,
// stores the result, and returns it. Subsequent callers are fast.
import { createServerFn } from "@tanstack/react-start";
import { supabaseAdmin } from "@/integrations/supabase/client.server";

const HIGHWAY = `["highway"~"^(path|footway|track|bridleway)$"]`;
const ENDPOINTS = [
  "https://overpass-api.de/api/interpreter",
  "https://overpass.kumi.systems/api/interpreter",
];
const STALE_DAYS = 30;

interface OsmWay {
  type: "way";
  id: number;
  tags?: Record<string, string>;
  geometry?: { lat: number; lon: number }[];
}

function distMi(coords: [number, number][]) {
  let m = 0;
  for (let i = 1; i < coords.length; i++) {
    const [la1, lo1] = coords[i - 1];
    const [la2, lo2] = coords[i];
    const R = 6371000;
    const dLa = ((la2 - la1) * Math.PI) / 180;
    const dLo = ((lo2 - lo1) * Math.PI) / 180;
    const a =
      Math.sin(dLa / 2) ** 2 +
      Math.cos((la1 * Math.PI) / 180) *
        Math.cos((la2 * Math.PI) / 180) *
        Math.sin(dLo / 2) ** 2;
    m += R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  }
  return m / 1609.344;
}

function difficulty(tags: Record<string, string> | undefined, mi: number) {
  const sac = tags?.sac_scale;
  if (sac && sac !== "hiking") return "Hard" as const;
  if (mi > 4) return "Hard" as const;
  if (mi > 2 || tags?.trail_visibility === "intermediate") return "Moderate" as const;
  return "Easy" as const;
}

function slug(s: string) {
  return s
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 60);
}

async function overpass(query: string) {
  let lastErr: unknown;
  for (const url of ENDPOINTS) {
    try {
      const res = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/x-www-form-urlencoded",
          "User-Agent": "summit-app/1.0",
        },
        body: "data=" + encodeURIComponent(query),
      });
      if (!res.ok) {
        lastErr = new Error(`Overpass ${res.status}`);
        continue;
      }
      return (await res.json()) as { elements: OsmWay[] };
    } catch (e) {
      lastErr = e;
    }
  }
  throw lastErr;
}

function buildTrails(json: { elements: OsmWay[] }) {
  const ways = (json.elements || []).filter(
    (e) => e.type === "way" && (e.geometry?.length ?? 0) > 1,
  );
  const CELL = 0.0001;
  const namedNodes = new Set<string>();
  const key = (lat: number, lon: number) =>
    `${Math.round(lat / CELL)}:${Math.round(lon / CELL)}`;
  const neighborKeys = (lat: number, lon: number) => {
    const keys: string[] = [];
    const r = Math.round(lat / CELL);
    const c = Math.round(lon / CELL);
    for (let dr = -1; dr <= 1; dr++)
      for (let dc = -1; dc <= 1; dc++) keys.push(`${r + dr}:${c + dc}`);
    return keys;
  };
  for (const w of ways) {
    const name = w.tags?.name?.trim();
    if (!name) continue;
    for (const p of w.geometry!) namedNodes.add(key(p.lat, p.lon));
  }

  const byName = new Map<
    string,
    { tags?: Record<string, string>; segments: [number, number][][] }
  >();
  for (const w of ways) {
    const rawName = w.tags?.name?.trim();
    if (!rawName) {
      const endpoints = [w.geometry![0], w.geometry![w.geometry!.length - 1]];
      const touches = endpoints.some((p) =>
        neighborKeys(p.lat, p.lon).some((k) => namedNodes.has(k)),
      );
      if (!touches) continue;
    }
    const name = rawName || `Unnamed ${w.id}`;
    const coords: [number, number][] = w.geometry!.map((p) => [p.lat, p.lon]);
    if (!byName.has(name)) byName.set(name, { tags: w.tags, segments: [] });
    byName.get(name)!.segments.push(coords);
  }

  const trails: {
    id: string;
    name: string;
    distanceMi: number;
    difficulty: "Easy" | "Moderate" | "Hard";
    segments: [number, number][][];
  }[] = [];
  for (const [name, { tags, segments }] of byName) {
    segments.sort((a, b) => distMi(b) - distMi(a));
    const totalMi = segments.reduce((s, c) => s + distMi(c), 0);
    if (totalMi < 0.59) continue;
    trails.push({
      id: slug(name) + "-" + ((tags as { ["@id"]?: string })?.["@id"] || trails.length),
      name,
      distanceMi: Number(totalMi.toFixed(2)),
      difficulty: difficulty(tags, totalMi),
      segments: segments.map(
        (seg) =>
          seg.map(([la, lo]) => [
            Number(la.toFixed(5)),
            Number(lo.toFixed(5)),
          ]) as [number, number][],
      ),
    });
  }
  trails.sort((a, b) => b.distanceMi - a.distanceMi);
  return trails;
}

export interface AreaPayload {
  id: string;
  name: string;
  subtitle: string;
  center: [number, number];
  zoom: number;
  bbox?: [number, number, number, number];
  trails: {
    id: string;
    name: string;
    distanceMi: number;
    difficulty: "Easy" | "Moderate" | "Hard";
    segments: [number, number][][];
  }[];
  trailCount: number;
  totalMi: number;
  cachedAt: string | null;
}

export const getAreaData = createServerFn({ method: "POST" })
  .inputValidator((input: { id: string; force?: boolean }) => {
    if (!input.id || typeof input.id !== "string" || input.id.length > 200) {
      throw new Error("invalid id");
    }
    return input;
  })
  .handler(async ({ data }): Promise<AreaPayload | null> => {
    const { data: row, error } = await supabaseAdmin
      .from("areas")
      .select(
        "id, name, state, center_lat, center_lon, zoom, osm_relation, bbox, trails, trail_count, total_mi, cached_at",
      )
      .eq("id", data.id)
      .maybeSingle();

    if (error) throw new Error(error.message);
    if (!row) return null;

    const fresh =
      !data.force &&
      row.trails &&
      row.cached_at &&
      Date.now() - new Date(row.cached_at).getTime() <
        STALE_DAYS * 24 * 60 * 60 * 1000;

    if (fresh) {
      return {
        id: row.id,
        name: row.name,
        subtitle: row.state,
        center: [row.center_lat, row.center_lon],
        zoom: row.zoom,
        bbox: (row.bbox as [number, number, number, number] | null) ?? undefined,
        trails: row.trails as AreaPayload["trails"],
        trailCount: row.trail_count ?? 0,
        totalMi: Number(row.total_mi ?? 0),
        cachedAt: row.cached_at,
      };
    }

    // Cache miss / stale → hit Overpass.
    let query: string;
    if (row.bbox) {
      const [s, w, n, e] = row.bbox as [number, number, number, number];
      query = `[out:json][timeout:90];
(way${HIGHWAY}(${s},${w},${n},${e}););
out tags geom;`;
    } else if (row.osm_relation) {
      const name = row.osm_relation.replace(/"/g, '\\"');
      query = `[out:json][timeout:90];
relation["name"="${name}"];
map_to_area->.a;
(way${HIGHWAY}(area.a););
out tags geom;`;
    } else {
      // Tiny bbox around center (~5km square) as last resort.
      const lat = row.center_lat;
      const lon = row.center_lon;
      const d = 0.045;
      query = `[out:json][timeout:90];
(way${HIGHWAY}(${lat - d},${lon - d},${lat + d},${lon + d}););
out tags geom;`;
    }

    let trails: AreaPayload["trails"] = [];
    try {
      const json = await overpass(query);
      trails = buildTrails(json);
    } catch (e) {
      console.error("overpass failed for", data.id, e);
      // Fall back to whatever we had cached, even if stale.
      if (row.trails) {
        return {
          id: row.id,
          name: row.name,
          subtitle: row.state,
          center: [row.center_lat, row.center_lon],
          zoom: row.zoom,
          bbox: (row.bbox as [number, number, number, number] | null) ?? undefined,
          trails: row.trails as AreaPayload["trails"],
          trailCount: row.trail_count ?? 0,
          totalMi: Number(row.total_mi ?? 0),
          cachedAt: row.cached_at,
        };
      }
      throw e;
    }

    const totalMi = Number(trails.reduce((s, t) => s + t.distanceMi, 0).toFixed(1));
    const cachedAt = new Date().toISOString();

    await supabaseAdmin
      .from("areas")
      .update({
        trails,
        trail_count: trails.length,
        total_mi: totalMi,
        cached_at: cachedAt,
        updated_at: cachedAt,
      })
      .eq("id", data.id);

    return {
      id: row.id,
      name: row.name,
      subtitle: row.state,
      center: [row.center_lat, row.center_lon],
      zoom: row.zoom,
      bbox: (row.bbox as [number, number, number, number] | null) ?? undefined,
      trails,
      trailCount: trails.length,
      totalMi,
      cachedAt,
    };
  });
