import raw from "./southMountainTrails.json";

export type Difficulty = "Easy" | "Moderate" | "Hard";

export interface Trail {
  id: string;
  name: string;
  distanceMi: number;
  difficulty: Difficulty;
  coords: [number, number][];
}

export interface Area {
  id: string;
  name: string;
  subtitle: string;
  location: string;
  center: [number, number];
  zoom: number;
  trails: Trail[];
}

const southMountainTrails: Trail[] = (raw as Array<{
  id: string;
  name: string;
  distanceMi: number;
  difficulty: Difficulty;
  coords: [number, number][];
}>)
  .filter((t) => !/^Unnamed\s/i.test(t.name) && t.distanceMi >= 0.3)
  .map((t) => ({ ...t }));

export const areas: Area[] = [
  {
    id: "south-mountain",
    name: "South Mountain",
    subtitle: "Phoenix, Arizona",
    location: "South Mountain Park & Preserve",
    center: [33.355, -112.04],
    zoom: 12,
    trails: southMountainTrails,
  },
];

export function getArea(id: string): Area | undefined {
  return areas.find((a) => a.id === id);
}
