import indexJson from "./areas/index.json";

export type Difficulty = "Easy" | "Moderate" | "Hard";

export interface Trail {
  id: string;
  name: string;
  distanceMi: number;
  difficulty: Difficulty;
  segments: [number, number][][];
}

export interface AreaSummary {
  id: string;
  name: string;
  subtitle: string;
  location: string;
  center: [number, number];
  zoom: number;
  trailCount: number;
  totalMi: number;
}

export interface Area extends AreaSummary {
  trails: Trail[];
}

export const areas: AreaSummary[] = indexJson as AreaSummary[];

export function getAreaSummary(id: string): AreaSummary | undefined {
  return areas.find((a) => a.id === id);
}

// Lazy-load full area data (geometry) only when needed.
// Vite turns this into one chunk per area thanks to the dynamic glob import.
const loaders = import.meta.glob<{ default: Area }>("./areas/*.json");

export async function loadArea(id: string): Promise<Area | undefined> {
  const key = `./areas/${id}.json`;
  if (key.endsWith("/index.json")) return undefined;
  const loader = loaders[key];
  if (!loader) return undefined;
  const mod = await loader();
  return mod.default;
}
