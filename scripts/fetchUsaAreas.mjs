// Discover hiking areas across the USA from OpenStreetMap and write them
// to public/areas/index.json. Each entry has id, name, subtitle, center,
// zoom, bbox — enough to render a card and lazily fetch trails on demand.
//
// Usage:
//   bun scripts/fetchUsaAreas.mjs            # all states (slow, ~10 min)
//   bun scripts/fetchUsaAreas.mjs CA NV      # only listed states
//
// Strategy: query Overpass per state for relations tagged as protected
// areas / national parks / forests / state parks / wilderness. Compute a
// bbox from the relation's outer way nodes. Dedupe by (name + state).

import { writeFileSync, readFileSync, existsSync, mkdirSync } from "node:fs";

const OUT = "public/areas/index.json";
mkdirSync("public/areas", { recursive: true });

// Rough bbox per US state (s, w, n, e). Not perfect — Overpass clips to area tags.
const STATES = {
  AL: [30.14, -88.47, 35.01, -84.89, "Alabama"],
  AK: [51.21, -179.15, 71.44, -129.97, "Alaska"],
  AZ: [31.33, -114.82, 37.0, -109.04, "Arizona"],
  AR: [33.0, -94.62, 36.5, -89.64, "Arkansas"],
  CA: [32.53, -124.48, 42.01, -114.13, "California"],
  CO: [36.99, -109.06, 41.0, -102.04, "Colorado"],
  CT: [40.95, -73.73, 42.05, -71.78, "Connecticut"],
  DE: [38.45, -75.79, 39.84, -75.05, "Delaware"],
  FL: [24.5, -87.63, 31.0, -79.97, "Florida"],
  GA: [30.36, -85.61, 35.0, -80.84, "Georgia"],
  HI: [18.91, -160.25, 22.24, -154.81, "Hawaii"],
  ID: [42.0, -117.24, 49.0, -111.04, "Idaho"],
  IL: [36.97, -91.51, 42.51, -87.5, "Illinois"],
  IN: [37.77, -88.1, 41.76, -84.78, "Indiana"],
  IA: [40.38, -96.64, 43.5, -90.14, "Iowa"],
  KS: [36.99, -102.05, 40.0, -94.59, "Kansas"],
  KY: [36.5, -89.57, 39.15, -81.96, "Kentucky"],
  LA: [28.93, -94.04, 33.02, -88.81, "Louisiana"],
  ME: [43.06, -71.08, 47.46, -66.95, "Maine"],
  MD: [37.91, -79.49, 39.72, -75.05, "Maryland"],
  MA: [41.24, -73.5, 42.89, -69.93, "Massachusetts"],
  MI: [41.7, -90.42, 48.31, -82.41, "Michigan"],
  MN: [43.5, -97.24, 49.38, -89.49, "Minnesota"],
  MS: [30.17, -91.66, 35.0, -88.1, "Mississippi"],
  MO: [35.99, -95.77, 40.61, -89.1, "Missouri"],
  MT: [44.36, -116.05, 49.0, -104.04, "Montana"],
  NE: [40.0, -104.05, 43.0, -95.31, "Nebraska"],
  NV: [35.0, -120.01, 42.0, -114.04, "Nevada"],
  NH: [42.7, -72.56, 45.31, -70.61, "New Hampshire"],
  NJ: [38.93, -75.56, 41.36, -73.89, "New Jersey"],
  NM: [31.33, -109.05, 37.0, -103.0, "New Mexico"],
  NY: [40.5, -79.76, 45.02, -71.86, "New York"],
  NC: [33.84, -84.32, 36.59, -75.46, "North Carolina"],
  ND: [45.94, -104.05, 49.0, -96.55, "North Dakota"],
  OH: [38.4, -84.82, 42.32, -80.52, "Ohio"],
  OK: [33.62, -103.0, 37.0, -94.43, "Oklahoma"],
  OR: [41.99, -124.57, 46.29, -116.46, "Oregon"],
  PA: [39.72, -80.52, 42.27, -74.69, "Pennsylvania"],
  RI: [41.15, -71.86, 42.02, -71.12, "Rhode Island"],
  SC: [32.03, -83.35, 35.22, -78.54, "South Carolina"],
  SD: [42.48, -104.06, 45.94, -96.44, "South Dakota"],
  TN: [34.98, -90.31, 36.68, -81.65, "Tennessee"],
  TX: [25.84, -106.65, 36.5, -93.51, "Texas"],
  UT: [37.0, -114.05, 42.0, -109.04, "Utah"],
  VT: [42.73, -73.44, 45.02, -71.46, "Vermont"],
  VA: [36.54, -83.68, 39.47, -75.24, "Virginia"],
  WA: [45.54, -124.78, 49.0, -116.92, "Washington"],
  WV: [37.2, -82.65, 40.64, -77.72, "West Virginia"],
  WI: [42.49, -92.89, 47.08, -86.25, "Wisconsin"],
  WY: [40.99, -111.06, 45.01, -104.05, "Wyoming"],
};

const ENDPOINTS = [
  "https://overpass-api.de/api/interpreter",
  "https://overpass.kumi.systems/api/interpreter",
];

function slug(s) {
  return s
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 60);
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
        lastErr = new Error(`${res.status} ${url}`);
        await new Promise((r) => setTimeout(r, 3000));
        continue;
      }
      return await res.json();
    } catch (e) {
      lastErr = e;
      await new Promise((r) => setTimeout(r, 3000));
    }
  }
  throw lastErr;
}

// Query each state: relations tagged as a hiking-relevant protected area.
// We rely on `out center bb tags;` so we don't have to download geometry.
function queryFor(s, w, n, e) {
  return `[out:json][timeout:180];
(
  relation["boundary"="national_park"](${s},${w},${n},${e});
  relation["boundary"="protected_area"]["protect_class"~"^[1-6]$"](${s},${w},${n},${e});
  relation["leisure"="nature_reserve"](${s},${w},${n},${e});
  relation["leisure"="park"]["park:type"~"state|national|regional"](${s},${w},${n},${e});
);
out tags center bb;`;
}

function bboxArea([s, w, n, e]) {
  // rough km² using lat 1°≈111km, lon scaled by cos(midLat)
  const dLat = (n - s) * 111;
  const dLon = (e - w) * 111 * Math.cos(((n + s) / 2) * (Math.PI / 180));
  return Math.abs(dLat * dLon);
}

function bboxToZoom(bbox) {
  const a = bboxArea(bbox);
  if (a > 5000) return 9;
  if (a > 1000) return 10;
  if (a > 200) return 11;
  if (a > 50) return 12;
  if (a > 10) return 13;
  return 14;
}

const onlyStates = process.argv.slice(2);
const targetStates = onlyStates.length
  ? onlyStates.map((s) => s.toUpperCase())
  : Object.keys(STATES);

// Load existing index so we don't lose curated entries.
const existing = existsSync(OUT)
  ? JSON.parse(readFileSync(OUT, "utf8"))
  : [];
const byId = new Map(existing.map((a) => [a.id, a]));

let added = 0;
let skipped = 0;

for (const code of targetStates) {
  const def = STATES[code];
  if (!def) {
    console.error(`unknown state ${code}`);
    continue;
  }
  const [s, w, n, e, stateName] = def;
  process.stderr.write(`→ ${code} (${stateName}) ... `);
  let json;
  try {
    json = await overpass(queryFor(s, w, n, e));
  } catch (err) {
    process.stderr.write(`FAILED ${err.message}\n`);
    continue;
  }

  const BAD_RE =
    /\b(cemetery|graveyard|golf|country club|playground|playfield|ball ?field|ball ?park|baseball|softball|soccer|tennis|skate ?park|dog ?park|pool|aquatic|community center|water tower|substation|parking|rest area|mini[- ]park|pocket park|tot lot|memorial garden|plaza|square|town green|village green)\b/i;
  const GOOD_RE =
    /\b(National Park|National Forest|National Monument|National Seashore|National Recreation|National Wildlife|National Preserve|National Lakeshore|National Memorial|National Battlefield|National Historic|State Park|State Forest|State Recreation|State Wildlife|State Natural|Wilderness|Wildlife Refuge|Wildlife Management|Wildlife Area|Conservation Area|Nature Reserve|Nature Preserve|Nature Center|Preserve|Greenway|Trail|Trails|Forest|Mountain|Mountains|Peak|Canyon|Gorge|Mesa|Butte|Ridge|Bluff|Hills|Valley|Falls|Lake|River|Creek|Springs|Wash|Marsh|Swamp|Wetland|Bog|Heath|Glade|Prairie|Meadow|Woods|Woodland|Sanctuary|Refuge|Reservation|Recreation Area|Heritage|Memorial Forest|Open Space|Land|Lands|Wild|Headlands|Highlands|Dunes|Beach|Seashore|Coast|Cape|Point|Island|Islands|Cave|Caverns|Volcano|Crater|Hot Springs|Hollow|Pines|Oaks|Cedars)\b/i;
  function looksReal(name) {
    const n = name.trim();
    if (n.length < 6) return false;
    if (!/[A-Za-z]{3,}/.test(n)) return false;
    if (/^\d+(st|nd|rd|th)?$/i.test(n)) return false;
    if (BAD_RE.test(n)) return false;
    if (!GOOD_RE.test(n)) return false;
    return true;
  }

  let count = 0;
  for (const el of json.elements || []) {
    if (el.type !== "relation") continue;
    const t = el.tags || {};
    const name = (t.name || t["name:en"] || "").trim();
    if (!name) continue;
    if (!el.center) continue;
    const isProtected =
      t.boundary === "national_park" ||
      t.boundary === "protected_area" ||
      t.leisure === "nature_reserve" ||
      t.leisure === "park";
    if (!isProtected) continue;
    if (!looksReal(name)) {
      skipped++;
      continue;
    }
    const id = `${slug(name)}-${code.toLowerCase()}`;
    if (byId.has(id)) continue;
    byId.set(id, {
      id,
      name,
      subtitle: stateName,
      location: name,
      center: [
        Number(el.center.lat.toFixed(5)),
        Number(el.center.lon.toFixed(5)),
      ],
      zoom: 13,
      osmRelation: name,
      trailCount: 0,
      totalMi: 0,
    });
    count++;
    added++;
  }
  process.stderr.write(`+${count} (skipped ${skipped})\n`);

  // Be polite to Overpass mirrors.
  await new Promise((r) => setTimeout(r, 2500));
}

const out = [...byId.values()].sort((a, b) => a.name.localeCompare(b.name));
writeFileSync(OUT, JSON.stringify(out));
console.error(`wrote ${out.length} areas (added ${added}) → ${OUT}`);
