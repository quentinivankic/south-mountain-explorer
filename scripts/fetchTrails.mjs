// Fetch all trails inside South Mountain Park & Preserve from OpenStreetMap.
// Usage: bun scripts/fetchTrails.mjs
import { writeFileSync } from "node:fs";

const QUERY = `
[out:json][timeout:60];
// South Mountain Park & Preserve boundary
relation["name"="South Mountain Park and Preserve"];
map_to_area->.smp;
(
  way["highway"~"^(path|footway|track|bridleway)$"](area.smp);
);
out tags geom;
`;

const res = await fetch("https://overpass-api.de/api/interpreter", {
  method: "POST",
  headers: { "Content-Type": "application/x-www-form-urlencoded", "User-Agent": "summit-app/1.0" },
  body: "data=" + encodeURIComponent(QUERY),
});
if (!res.ok) throw new Error(`Overpass ${res.status}`);
const json = await res.json();

const ways = json.elements.filter((e) => e.type === "way" && e.geometry?.length > 1);
console.error(`fetched ${ways.length} ways`);

// Group by name so multi-segment trails become one entry
const byName = new Map();
for (const w of ways) {
  const name = w.tags?.name?.trim() || `Unnamed ${w.id}`;
  const coords = w.geometry.map((p) => [p.lat, p.lon]);
  if (!byName.has(name)) byName.set(name, { tags: w.tags, segments: [] });
  byName.get(name).segments.push(coords);
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

const trails = [];
for (const [name, { tags, segments }] of byName) {
  // pick longest segment as the canonical polyline (keeps map clean)
  segments.sort((a, b) => distMi(b) - distMi(a));
  const totalMi = segments.reduce((s, c) => s + distMi(c), 0);
  if (totalMi < 0.05) continue;
  trails.push({
    id: slug(name) + "-" + (tags?.["@id"] || trails.length),
    name,
    distanceMi: Number(totalMi.toFixed(2)),
    difficulty: difficulty(tags, totalMi),
    segments,
  });
}

trails.sort((a, b) => b.distanceMi - a.distanceMi);
console.error(`writing ${trails.length} trails`);
writeFileSync(
  "src/data/southMountainTrails.json",
  JSON.stringify(trails, null, 2),
);
