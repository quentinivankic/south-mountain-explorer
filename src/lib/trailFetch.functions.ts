// Server function that fetches trail geometry from OpenStreetMap on demand
// for areas that don't ship as a pre-built JSON file in /public/areas/.
// Uses the same Overpass + dedupe logic as the build-time script.
import { createServerFn } from "@tanstack/react-start";

const HIGHWAY = `["highway"~"^(path|footway|track|bridleway)$"]`;
const ENDPOINTS = [
  "https://overpass-api.de/api/interpreter",
  "https://overpass.kumi.systems/api/interpreter",
];

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
  if (mi > 2 || tags?.trail_visibility === "intermediate")
    return "Moderate" as const;
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

export const fetchAreaTrails = createServerFn({ method: "POST" })
  .inputValidator(
    (input: {
      relation?: string;
      bbox?: [number, number, number, number];
    }) => {
      if (!input.relation && !input.bbox) {
        throw new Error("relation or bbox required");
      }
      return input;
    },
  )
  .handler(async ({ data }) => {
    let query: string;
    if (data.bbox) {
      const [s, w, n, e] = data.bbox;
      query = `[out:json][timeout:90];
(way${HIGHWAY}(${s},${w},${n},${e}););
out tags geom;`;
    } else {
      const name = data.relation!.replace(/"/g, '\\"');
      query = `[out:json][timeout:90];
relation["name"="${name}"];
map_to_area->.a;
(way${HIGHWAY}(area.a););
out tags geom;`;
    }
    const json = await overpass(query);
    const trails = buildTrails(json);
    const totalMi = trails.reduce((s, t) => s + t.distanceMi, 0);
    return {
      trails,
      trailCount: trails.length,
      totalMi: Number(totalMi.toFixed(1)),
    };
  });
