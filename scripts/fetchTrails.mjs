// Fetch trails for every area in scripts/areas.json from OpenStreetMap.
// Writes a single bundle to src/data/areasData.json.
// Usage: bun scripts/fetchTrails.mjs           # all areas
//        bun scripts/fetchTrails.mjs <areaId>  # one area
import { readFileSync, writeFileSync, existsSync } from "node:fs";

const OUT = "src/data/areasData.json";
const areas = JSON.parse(readFileSync("scripts/areas.json", "utf8"));
const onlyId = process.argv[2];
const targets = onlyId ? areas.filter((a) => a.id === onlyId) : areas;
if (targets.length === 0) {
  console.error(`No matching areas`);
  process.exit(1);
}

const HIGHWAY = `["highway"~"^(path|footway|track|bridleway)$"]`;

function buildQuery(area) {
  if (area.osm.bbox) {
    const [s, w, n, e] = area.osm.bbox;
    return `[out:json][timeout:90];
(way${HIGHWAY}(${s},${w},${n},${e}););
out tags geom;`;
  }
  if (area.osm.relation) {
    const name = area.osm.relation.replace(/"/g, '\\"');
    return `[out:json][timeout:90];
relation["name"="${name}"];
map_to_area->.a;
(way${HIGHWAY}(area.a););
out tags geom;`;
  }
  throw new Error(`area ${area.id} missing osm.bbox or osm.relation`);
}

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
  const t = tags?.trail_visibility;
  if (mi > 4) return "Hard";
  if (mi > 2 || t === "intermediate") return "Moderate";
  return "Easy";
}

function slug(s) {
  return s
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 60);
}

async function fetchArea(area) {
  const query = buildQuery(area);
  // Try a couple of mirrors for resilience.
  const endpoints = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
  ];
  let lastErr;
  for (const url of endpoints) {
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
        lastErr = new Error(`Overpass ${res.status} from ${url}`);
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
    (e) => e.type === "way" && e.geometry?.length > 1,
  );
  const byName = new Map();
  for (const w of ways) {
    const name = w.tags?.name?.trim() || `Unnamed ${w.id}`;
    const coords = w.geometry.map((p) => [p.lat, p.lon]);
    if (!byName.has(name)) byName.set(name, { tags: w.tags, segments: [] });
    byName.get(name).segments.push(coords);
  }
  const trails = [];
  for (const [name, { tags, segments }] of byName) {
    segments.sort((a, b) => distMi(b) - distMi(a));
    const totalMi = segments.reduce((s, c) => s + distMi(c), 0);
    if (totalMi < 0.05) continue;
    trails.push({
      id: slug(name) + "-" + (tags?.["@id"] || trails.length),
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

// Load existing bundle so single-area runs don't blow away other areas.
let existing = {};
if (existsSync(OUT)) {
  try {
    existing = JSON.parse(readFileSync(OUT, "utf8"));
  } catch {
    existing = {};
  }
}

const out = { ...existing };
for (const area of targets) {
  process.stderr.write(`→ ${area.id} ... `);
  try {
    const json = await fetchArea(area);
    const trails = buildTrails(json);
    out[area.id] = { ...area, trails };
    process.stderr.write(`${trails.length} trails\n`);
  } catch (e) {
    process.stderr.write(`FAILED (${e.message})\n`);
    if (!existing[area.id]) {
      // First-time fetch failure: write a stub so the app still builds.
      out[area.id] = { ...area, trails: [] };
    }
  }
  // Be polite to Overpass.
  if (targets.length > 1) await new Promise((r) => setTimeout(r, 1500));
}

writeFileSync(OUT, JSON.stringify(out));
console.error(`wrote ${OUT} (${Object.keys(out).length} areas)`);
