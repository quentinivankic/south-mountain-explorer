// Filter public/areas/index.json down to entries that look like real,
// hike-worthy areas. Removes:
//   - names that are just numbers, or shorter than 3 chars
//   - names with no letters (e.g. "26th" alone — but "26th Street Woods" is kept)
//   - obvious non-park names (cemeteries, golf courses, playgrounds, ball fields)
//   - duplicates by (lowercased name + state)
//
// Run: bun scripts/filterAreas.mjs
import { readFileSync, writeFileSync } from "node:fs";

const PATH = "public/areas/index.json";
const all = JSON.parse(readFileSync(PATH, "utf8"));

const BAD_RE =
  /\b(cemetery|graveyard|golf|country club|playground|playfield|ball ?field|ball ?park|baseball|softball|soccer|tennis|skate ?park|dog ?park|pool|aquatic|community center|water tower|substation|parking|rest area|mini[- ]park|pocket park|tot lot|memorial garden|plaza|square|town green|village green)\b/i;

// Names that strongly suggest a hike-worthy place. We keep everything that
// matches at least one of these tokens. This drops generic "Park" entries
// that are usually city pocket parks.
const GOOD_RE =
  /\b(National Park|National Forest|National Monument|National Seashore|National Recreation|National Wildlife|National Preserve|National Lakeshore|National Memorial|National Battlefield|National Historic|State Park|State Forest|State Recreation|State Wildlife|State Natural|Wilderness|Wildlife Refuge|Wildlife Management|Wildlife Area|Conservation Area|Nature Reserve|Nature Preserve|Nature Center|Preserve|Greenway|Trail|Trails|Forest|Mountain|Mountains|Peak|Canyon|Gorge|Mesa|Butte|Ridge|Bluff|Hills|Valley|Falls|Lake|River|Creek|Springs|Wash|Marsh|Swamp|Wetland|Bog|Heath|Glade|Prairie|Meadow|Woods|Woodland|Sanctuary|Refuge|Reservation|Recreation Area|Heritage|Memorial Forest|Open Space|Land|Lands|Wild|Headlands|Highlands|Dunes|Beach|Seashore|Coast|Cape|Point|Island|Islands|Cave|Caverns|Volcano|Crater|Hot Springs|Hollow|Pines|Oaks|Cedars)\b/i;

function looksReal(name) {
  const n = name.trim();
  if (n.length < 6) return false;
  if (!/[A-Za-z]{3,}/.test(n)) return false;
  if (/^\d+$/.test(n)) return false;
  if (/^\d+(st|nd|rd|th)?$/i.test(n)) return false;
  if (BAD_RE.test(n)) return false;
  if (!GOOD_RE.test(n)) return false;
  return true;
}

const seen = new Set();
const kept = [];
let dropped = 0;
for (const a of all) {
  if (!looksReal(a.name)) {
    dropped++;
    continue;
  }
  const key = `${a.name.toLowerCase()}|${a.subtitle}`;
  if (seen.has(key)) {
    dropped++;
    continue;
  }
  seen.add(key);
  kept.push(a);
}

kept.sort((a, b) => a.name.localeCompare(b.name));
writeFileSync(PATH, JSON.stringify(kept));
console.error(`kept ${kept.length}, dropped ${dropped} → ${PATH}`);
