// Repull all trails for areas in public/areas/index.json using the same
// criteria as src/lib/areaData.functions.ts → buildTrails().
// Output: public/areas/all-trails.v2-<rand>.json + index.v2-<rand>.json
import fs from "node:fs/promises";
import path from "node:path";
import crypto from "node:crypto";

const HIGHWAY = `["highway"~"^(path|footway|track|bridleway)$"]`;
const ENDPOINTS = [
  "https://overpass-api.de/api/interpreter",
  "https://overpass.kumi.systems/api/interpreter",
];

function distMi(coords) {
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

function difficulty(tags, mi) {
  const sac = tags?.sac_scale;
  if (sac && sac !== "hiking") return "Hard";
  if (mi > 4) return "Hard";
  if (mi > 2 || tags?.trail_visibility === "intermediate") return "Moderate";
  return "Easy";
}

function slug(s) {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 60);
}

async function overpass(query) {
  let lastErr;
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
      return await res.json();
    } catch (e) {
      lastErr = e;
    }
  }
  throw lastErr;
}

function buildTrails(json) {
  const ways = (json.elements || []).filter(
    (e) => e.type === "way" && (e.geometry?.length ?? 0) > 1,
  );
  const CELL = 0.0001;
  const namedNodes = new Set();
  const key = (lat, lon) => `${Math.round(lat / CELL)}:${Math.round(lon / CELL)}`;
  const neighborKeys = (lat, lon) => {
    const keys = [];
    const r = Math.round(lat / CELL);
    const c = Math.round(lon / CELL);
    for (let dr = -1; dr <= 1; dr++)
      for (let dc = -1; dc <= 1; dc++) keys.push(`${r + dr}:${c + dc}`);
    return keys;
  };
  for (const w of ways) {
    const name = w.tags?.name?.trim();
    if (!name) continue;
    for (const p of w.geometry) namedNodes.add(key(p.lat, p.lon));
  }
  const byName = new Map();
  for (const w of ways) {
    const rawName = w.tags?.name?.trim();
    if (!rawName) {
      const endpoints = [w.geometry[0], w.geometry[w.geometry.length - 1]];
      const touches = endpoints.some((p) =>
        neighborKeys(p.lat, p.lon).some((k) => namedNodes.has(k)),
      );
      if (!touches) continue;
    }
    const name = rawName || `Unnamed ${w.id}`;
    const coords = w.geometry.map((p) => [p.lat, p.lon]);
    if (!byName.has(name)) byName.set(name, { tags: w.tags, segments: [] });
    byName.get(name).segments.push(coords);
  }
  const trails = [];
  let idx = 0;
  for (const [name, { tags, segments }] of byName) {
    segments.sort((a, b) => distMi(b) - distMi(a));
    const totalMi = segments.reduce((s, c) => s + distMi(c), 0);
    if (totalMi < 0.59) continue;
    trails.push({
      id: `${slug(name)}-${idx++}`,
      name,
      distanceMi: Number(totalMi.toFixed(2)),
      difficulty: difficulty(tags, totalMi),
      segments: segments.map((seg) =>
        seg.map(([la, lo]) => [Number(la.toFixed(5)), Number(lo.toFixed(5))]),
      ),
    });
  }
  trails.sort((a, b) => b.distanceMi - a.distanceMi);
  return trails;
}

async function fetchArea(area) {
  const { id, name, lat, lon } = area;
  // Try OSM relation by name first.
  const escName = name.replace(/"/g, '\\"');
  const relQuery = `[out:json][timeout:90];
relation["name"="${escName}"];
map_to_area->.a;
(way${HIGHWAY}(area.a););
out tags geom;`;
  let trails = [];
  try {
    const json = await overpass(relQuery);
    trails = buildTrails(json);
  } catch (e) {
    console.warn(`  relation query failed: ${e?.message || e}`);
  }
  if (trails.length === 0) {
    // Fallback: small bbox around center (~5km square).
    const d = 0.045;
    const bboxQuery = `[out:json][timeout:90];
(way${HIGHWAY}(${lat - d},${lon - d},${lat + d},${lon + d}););
out tags geom;`;
    try {
      const json = await overpass(bboxQuery);
      trails = buildTrails(json);
    } catch (e) {
      console.warn(`  bbox query failed: ${e?.message || e}`);
    }
  }
  return trails;
}

async function main() {
  const idxPath = path.resolve("public/areas/index.json");
  const raw = JSON.parse(await fs.readFile(idxPath, "utf8"));
  // tuples: [id, name, state, lat, lon, count, miles]
  const areas = raw.map(([id, name, state, lat, lon]) => ({ id, name, state, lat, lon }));
  console.log(`Pulling ${areas.length} areas...`);

  const rand = crypto.randomBytes(3).toString("hex");
  const outAreas = [];
  const newIndex = [];

  for (let i = 0; i < areas.length; i++) {
    const a = areas[i];
    const t0 = Date.now();
    process.stdout.write(`[${i + 1}/${areas.length}] ${a.id} ... `);
    let trails = [];
    try {
      trails = await fetchArea(a);
    } catch (e) {
      console.log(`FAIL ${e?.message || e}`);
    }
    const totalMi = Number(trails.reduce((s, t) => s + t.distanceMi, 0).toFixed(1));
    console.log(`${trails.length} trails, ${totalMi} mi  (${((Date.now() - t0) / 1000).toFixed(1)}s)`);
    outAreas.push({ id: a.id, trails });
    newIndex.push({
      id: a.id,
      name: a.name,
      state: a.state,
      lat: a.lat,
      lon: a.lon,
      trailCount: trails.length,
      totalMi,
    });
    // Be polite to Overpass.
    await new Promise((r) => setTimeout(r, 1500));
  }

  const bundlePath = `public/areas/all-trails.v2-${rand}.json`;
  const newIdxPath = `public/areas/index.v2-${rand}.json`;
  await fs.writeFile(bundlePath, JSON.stringify(outAreas));
  await fs.writeFile(newIdxPath, JSON.stringify(newIndex, null, 2));
  console.log(`\nWrote ${bundlePath}`);
  console.log(`Wrote ${newIdxPath}`);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
