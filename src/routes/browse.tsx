import { createFileRoute, Link } from "@tanstack/react-router";
import { useDeferredValue, useMemo, useState } from "react";
import { useAreas } from "@/hooks/useAreas";
import { useAreaDetails } from "@/hooks/useAreaDetails";
import { useFavorites, toggleFavorite } from "@/lib/favorites";
import { Search, Star, ArrowLeft, MapPin } from "lucide-react";
import { SuggestArea } from "@/components/SuggestArea";

export const Route = createFileRoute("/browse")({
  head: () => ({
    meta: [
      { title: "Browse hiking areas — Summit" },
      { name: "description", content: "Search hiking areas and favorite the ones you want to complete." },
    ],
  }),
  component: Browse,
});

const MAX_VISIBLE = 60;

function Browse() {
  const [q, setQ] = useState("");
  const deferredQ = useDeferredValue(q);
  const favorites = useFavorites();
  const areas = useAreas();

  const { visible, totalMatches } = useMemo(() => {
    const s = deferredQ.trim().toLowerCase();
    const matches = s ? areas.filter((a) => a.search.includes(s)) : areas;
    return { visible: matches.slice(0, MAX_VISIBLE), totalMatches: matches.length };
  }, [deferredQ, areas]);

  // Lazily load trail count/miles for the small set of visible items.
  const visibleIds = useMemo(() => visible.map((a) => a.id), [visible]);
  const details = useAreaDetails(visibleIds);

  return (
    <div className="min-h-screen bg-background">
      <header className="px-5 pt-12 pb-4 sticky top-0 z-10 bg-background/95 backdrop-blur border-b border-border/50">
        <div className="flex items-center gap-3 mb-4">
          <Link
            to="/"
            className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="size-4" /> Home
          </Link>
          <h1 className="text-lg font-bold">Browse areas</h1>
        </div>
        <div className="relative">
          <Search className="size-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
          <input
            type="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search by name or location"
            className="w-full pl-9 pr-3 py-2.5 rounded-full bg-muted text-sm focus:outline-none focus:ring-2 focus:ring-primary"
            autoFocus
          />
        </div>
        {areas.length > 0 && (
          <p className="mt-2 text-[11px] text-muted-foreground">
            {totalMatches.toLocaleString()} {totalMatches === 1 ? "area" : "areas"}
            {totalMatches > MAX_VISIBLE && ` · showing top ${MAX_VISIBLE} — keep typing to narrow`}
          </p>
        )}
      </header>

      <main className="px-5 py-4 pb-24 space-y-2">
        {visible.length === 0 ? (
          <p className="text-center text-sm text-muted-foreground py-12">
            No areas match "{q}". Try a different search or suggest one below.
          </p>
        ) : (
          visible.map((area) => {
            const isFav = favorites.has(area.id);
            const det = details[area.id];
            return (
              <div
                key={area.id}
                className="flex items-center gap-2 rounded-2xl bg-card border border-border/50 p-3"
              >
                <Link
                  to="/area/$areaId"
                  params={{ areaId: area.id }}
                  className="flex-1 min-w-0"
                >
                  <div className="font-semibold text-foreground truncate">{area.name}</div>
                  <div className="flex items-center gap-1 text-xs text-muted-foreground truncate">
                    <MapPin className="size-3" />
                    {area.subtitle}
                  </div>
                  {det && det.trailCount > 0 && (
                    <div className="mt-1 text-[11px] text-muted-foreground tabular-nums">
                      {det.trailCount} {det.trailCount === 1 ? "trail" : "trails"}
                      {det.totalMi > 0 && ` · ${det.totalMi.toFixed(1)} mi`}
                    </div>
                  )}
                </Link>
                <button
                  onClick={() => toggleFavorite(area.id)}
                  aria-label={isFav ? "Unfavorite" : "Favorite"}
                  className="p-2 rounded-full hover:bg-muted transition-colors"
                >
                  <Star
                    className={`size-5 ${isFav ? "fill-primary text-primary" : "text-muted-foreground"}`}
                  />
                </button>
              </div>
            );
          })
        )}

        <div className="pt-4">
          <SuggestArea />
        </div>
      </main>
    </div>
  );
}
