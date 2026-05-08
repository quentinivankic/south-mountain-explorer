// Fetch trails for every area in scripts/areas.json from OpenStreetMap.
// Writes:
//   public/areas/index.json           — lightweight metadata + summary
//   public/areas/<areaId>.json        — per-area trail geometry (lazy-loaded)
// Usage: bun scripts/fetchTrails.mjs           # all areas
//        bun scripts/fetchTrails.mjs <areaId>  # one area
import { readFileSync, writeFileSync, existsSync, mkdirSync, readdirSync } from "node:fs";
import { join } from "node:path";

const OUT_DIR = "public/areas";
const INDEX = join(OUT_DIR, "index.json");
mkdirSync(OUT_DIR, { recursive: true });
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

  // Bucket every named-trail node into a ~10m grid so we can cheaply test
  // whether an unnamed segment's endpoint touches a named trail.
  // 0.0001 deg lat ≈ 11m; close enough for "connects to".
  const CELL = 0.0001;
  const namedNodes = new Set();
  const key = (lat, lon) =>
    `${Math.round(lat / CELL)}:${Math.round(lon / CELL)}`;
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
      // Unnamed: only keep if an endpoint touches a named trail.
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
  for (const [name, { tags, segments }] of byName) {
    segments.sort((a, b) => distMi(b) - distMi(a));
    const totalMi = segments.reduce((s, c) => s + distMi(c), 0);
    // Drop anything under 0.59 mi — too short to count as a real trail.
    if (totalMi < 0.59) continue;
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

for (const area of targets) {
  process.stderr.write(`→ ${area.id} ... `);
  try {
    const json = await fetchArea(area);
    const trails = buildTrails(json);
    const { osm: _omit, ...meta } = area;
    writeFileSync(
      join(OUT_DIR, `${area.id}.json`),
      JSON.stringify({ ...meta, trails }),
    );
    process.stderr.write(`${trails.length} trails\n`);
  } catch (e) {
    process.stderr.write(`FAILED (${e.message})\n`);
    const path = join(OUT_DIR, `${area.id}.json`);
    if (!existsSync(path)) {
      const { osm: _omit, ...meta } = area;
      writeFileSync(path, JSON.stringify({ ...meta, trails: [] }));
    }
  }
  if (targets.length > 1) await new Promise((r) => setTimeout(r, 1500));
}

// Rebuild lightweight index from whatever per-area files exist.
const index = [];
for (const area of areas) {
  const path = join(OUT_DIR, `${area.id}.json`);
  if (!existsSync(path)) continue;
  const data = JSON.parse(readFileSync(path, "utf8"));
  const totalMi = data.trails.reduce((s, t) => s + t.distanceMi, 0);
  const { osm: _omit, ...meta } = area;
  index.push({
    ...meta,
    trailCount: data.trails.length,
    totalMi: Number(totalMi.toFixed(1)),
  });
}
writeFileSync(INDEX, JSON.stringify(index, null, 2));
console.error(`wrote index (${index.length} areas)`);

