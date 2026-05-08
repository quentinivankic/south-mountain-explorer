import { useEffect, useState } from "react";
import { loadAreas, getCachedAreas, type AreaSummary } from "@/data/trails";

/** Returns the area index (loaded from /areas/index.json + IDB cache). */
export function useAreas(): AreaSummary[] {
  const [areas, setAreas] = useState<AreaSummary[]>(getCachedAreas);
  useEffect(() => {
    let cancelled = false;
    loadAreas().then((list) => {
      if (!cancelled) setAreas(list);
    });
    return () => {
      cancelled = true;
    };
  }, []);
  return areas;
}
