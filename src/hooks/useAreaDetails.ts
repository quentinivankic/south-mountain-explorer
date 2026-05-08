import { useEffect, useState } from "react";
import { loadArea, type Area } from "@/data/trails";

/** Loads full area data (from IDB cache or server) for the given ids.
 *  Returns a map keyed by id; missing entries appear as the data loads. */
export function useAreaDetails(ids: string[]): Record<string, Area> {
  const [map, setMap] = useState<Record<string, Area>>({});
  // Stable key so the effect only re-runs when the set of ids changes.
  const key = ids.slice().sort().join(",");
  useEffect(() => {
    let cancelled = false;
    (async () => {
      for (const id of ids) {
        const a = await loadArea(id);
        if (cancelled || !a) continue;
        setMap((prev) => ({ ...prev, [id]: a }));
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return map;
}
