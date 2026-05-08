// Seeds the public.areas table from public/areas/index.json.
// Idempotent — uses upsert on the primary key.
import { createClient } from "@supabase/supabase-js";
import { readFileSync } from "node:fs";

const url = process.env.SUPABASE_URL;
const key = process.env.SUPABASE_SERVICE_ROLE_KEY;
if (!url || !key) {
  console.error("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY");
  process.exit(1);
}
const supa = createClient(url, key, { auth: { persistSession: false } });

const areas = JSON.parse(readFileSync("public/areas/index.json", "utf8"));
console.log(`Loaded ${areas.length} areas from index.json`);

const rows = areas.map((a) => ({
  id: a.id,
  name: a.name,
  state: a.subtitle || "",
  center_lat: a.center[0],
  center_lon: a.center[1],
  zoom: a.zoom ?? 13,
  osm_relation: a.osmRelation ?? null,
  bbox: a.bbox ?? null,
}));

const CHUNK = 500;
let inserted = 0;
for (let i = 0; i < rows.length; i += CHUNK) {
  const slice = rows.slice(i, i + CHUNK);
  const { error } = await supa.from("areas").upsert(slice, { onConflict: "id" });
  if (error) {
    console.error("chunk failed at", i, error);
    process.exit(1);
  }
  inserted += slice.length;
  if (inserted % 2500 === 0 || inserted === rows.length) {
    console.log(`  ${inserted}/${rows.length}`);
  }
}
console.log("Done.");
