import { useEffect, useState } from "react";
import { loadArea, getCachedArea, type Area } from "@/data/trails";

/** Loads full area data for the given ids.
 *  - cacheOnly=true (default for lists): only reads IDB, never hits the server.
 *  - cacheOnly=false: also fetches/refreshes missing entries (for favorites).
 *  Returns a map keyed by id; missing entries appear as the data loads. */
export function useAreaDetails(
  ids: string[],
  opts: { cacheOnly?: boolean } = { cacheOnly: true },
): Record<string, Area> {
  const [map, setMap] = useState<Record<string, Area>>({});
  const key = ids.slice().sort().join(",");
  const cacheOnly = opts.cacheOnly !== false;
  useEffect(() => {
    let cancelled = false;
    (async () => {
      for (const id of ids) {
        const a = cacheOnly ? await getCachedArea(id) : await loadArea(id);
        if (cancelled || !a) continue;
        setMap((prev) => (prev[id] ? prev : { ...prev, [id]: a }));
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, cacheOnly]);
  return map;
}
