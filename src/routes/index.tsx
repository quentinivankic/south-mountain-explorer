import { createFileRoute, Link } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { useAreas } from "@/hooks/useAreas";
import { Mountain, MapPin, ChevronRight, LogOut, LogIn, Search, Star, History, Navigation, Settings as SettingsIcon } from "lucide-react";
import { useAllProgress, useAuthState } from "@/lib/progress";
import { useFavorites } from "@/lib/favorites";
import { supabase } from "@/integrations/supabase/client";
import { LocationPrompt } from "@/components/LocationPrompt";
import { useLocation, distanceMi, requestLocation } from "@/lib/location";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "Summit — Finish every trail in your favorite places" },
      {
        name: "description",
        content:
          "Pick an area, complete every trail, become a local legend. Start with South Mountain in Phoenix, Arizona.",
      },
      { property: "og:title", content: "Summit — Finish every trail" },
      {
        property: "og:description",
        content: "Complete every trail in your favorite hiking areas.",
      },
    ],
  }),
  component: Home,
});

function Home() {
  const progress = useAllProgress();
  const userId = useAuthState();
  const favorites = useFavorites();
  const areas = useAreas();
  const loc = useLocation();
  const favoriteAreas = areas.filter((a) => favorites.has(a.id));
  const [locBusy, setLocBusy] = useState(false);
  const [locError, setLocError] = useState<string | null>(null);

  async function handleEnableLocation() {
    setLocError(null);
    setLocBusy(true);
    try {
      // Check permission state first so we can give a useful message
      // when the browser has previously denied access.
      let permState: PermissionState | undefined;
      try {
        const p = await (navigator as Navigator & {
          permissions?: { query: (q: { name: string }) => Promise<{ state: PermissionState }> };
        }).permissions?.query({ name: "geolocation" });
        permState = p?.state;
      } catch {
        // ignore
      }
      if (permState === "denied") {
        setLocError(
          "Location is blocked in your browser settings. Tap the lock icon in the address bar → Site settings → allow Location, then try again.",
        );
        return;
      }
      const result = await requestLocation();
      if (result.status === "denied") {
        setLocError("Permission denied. Allow location in your browser to see areas near you.");
      } else if (result.status === "unsupported") {
        setLocError("Your browser doesn't support location.");
      }
    } finally {
      setLocBusy(false);
    }
  }

  const nearby = useMemo(() => {
    if (loc.status !== "granted" || loc.lat == null || loc.lon == null) return [];
    const me: [number, number] = [loc.lat, loc.lon];
    return areas
      .map((a) => ({ a, d: distanceMi(me, a.center) }))
      .sort((x, y) => x.d - y.d)
      .slice(0, 8);
  }, [areas, loc]);

  return (
    <div className="min-h-screen bg-background">
      {/* Hero */}
      <header className="relative overflow-hidden">
        <div
          className="absolute inset-0"
          style={{ background: "var(--gradient-sunrise)" }}
          aria-hidden
        />
        <div className="relative px-6 pt-14 pb-16 text-primary-foreground">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2 text-sm/none opacity-90">
              <Mountain className="size-4" />
              <span className="uppercase tracking-[0.2em] text-xs">Summit</span>
            </div>
            <div className="flex items-center gap-3">
              <Link
                to="/settings"
                aria-label="Settings"
                className="opacity-90 hover:opacity-100"
              >
                <SettingsIcon className="size-4" />
              </Link>
              {userId ? (
                <button
                  onClick={() => supabase.auth.signOut()}
                  className="flex items-center gap-1 text-xs font-semibold opacity-90 hover:opacity-100"
                  aria-label="Sign out"
                >
                  <LogOut className="size-3.5" /> Sign out
                </button>
              ) : (
                <Link
                  to="/auth"
                  search={{ redirect: "/" }}
                  className="flex items-center gap-1 text-xs font-semibold opacity-90 hover:opacity-100"
                >
                  <LogIn className="size-3.5" /> Sign in
                </Link>
              )}
            </div>
          </div>
          <h1 className="mt-6 text-4xl font-black leading-[1.05] max-w-[14ch]">
            Finish every trail. Become a local.
          </h1>
          <p className="mt-3 text-base/relaxed opacity-90 max-w-[34ch]">
            Pick an area. Tick off hikes. Watch your map fill in.
          </p>
        </div>
      </header>

      {/* Areas */}
      <main className="px-5 pt-6 pb-24 space-y-4">
        <Link
          to="/browse"
          className="flex items-center gap-3 rounded-2xl bg-card border border-border/50 px-4 py-3 shadow-sm active:scale-[0.99] transition-transform"
        >
          <Search className="size-4 text-muted-foreground" />
          <span className="text-sm text-muted-foreground flex-1">Search areas…</span>
          <span className="text-xs font-semibold text-primary">Browse</span>
        </Link>

        {userId && (
          <Link
            to="/history"
            className="flex items-center gap-3 rounded-2xl bg-card border border-border/50 px-4 py-3 shadow-sm active:scale-[0.99] transition-transform"
          >
            <History className="size-4 text-muted-foreground" />
            <span className="text-sm flex-1 font-medium">Hike history</span>
            <ChevronRight className="size-4 text-muted-foreground" />
          </Link>
        )}

        {/* Near you */}
        {loc.status === "granted" && nearby.length > 0 && (
          <section className="space-y-2">
            <h2 className="text-xs font-bold uppercase tracking-wider text-muted-foreground px-1 pt-2 flex items-center gap-1.5">
              <Navigation className="size-3" /> Near you
            </h2>
            {nearby.map(({ a, d }) => (
              <Link
                key={a.id}
                to="/area/$areaId"
                params={{ areaId: a.id }}
                className="flex items-center gap-3 rounded-2xl bg-card border border-border/50 p-3 active:scale-[0.99] transition-transform"
              >
                <div className="flex-1 min-w-0">
                  <div className="font-semibold text-foreground truncate">{a.name}</div>
                  <div className="text-xs text-muted-foreground truncate">
                    {a.subtitle}
                  </div>
                </div>
                <div className="text-xs font-semibold text-primary tabular-nums whitespace-nowrap">
                  {d < 10 ? d.toFixed(1) : Math.round(d)} mi
                </div>
                <ChevronRight className="size-4 text-muted-foreground" />
              </Link>
            ))}
          </section>
        )}

        {(loc.status === "denied" || loc.status === "unsupported") && (
          <div className="space-y-2">
            <button
              onClick={handleEnableLocation}
              disabled={locBusy}
              className="flex items-center gap-3 w-full rounded-2xl bg-card border border-dashed border-border px-4 py-3 text-left disabled:opacity-60"
            >
              <Navigation className="size-4 text-muted-foreground" />
              <span className="text-sm flex-1 text-muted-foreground">
                Show areas near me
              </span>
              <span className="text-xs font-semibold text-primary">
                {locBusy ? "Asking…" : "Enable"}
              </span>
            </button>
            {locError && (
              <p className="px-1 text-xs text-destructive leading-relaxed">{locError}</p>
            )}
          </div>
        )}

        <h2 className="text-xs font-bold uppercase tracking-wider text-muted-foreground px-1 pt-2">
          Your favorites
        </h2>

        {favoriteAreas.length === 0 ? (
          <div className="rounded-3xl border border-dashed border-border p-6 text-center">
            <Star className="size-6 mx-auto text-muted-foreground mb-2" />
            <p className="text-sm text-foreground font-medium">No favorites yet</p>
            <p className="text-xs text-muted-foreground mt-1 mb-3">
              Find areas you want to complete and tap the star.
            </p>
            <Link
              to="/browse"
              className="inline-flex items-center gap-1 text-sm font-semibold text-primary"
            >
              <Search className="size-4" /> Browse areas
            </Link>
          </div>
        ) : (
          favoriteAreas.map((area) => {
            const done = Object.keys(progress[area.id] ?? {}).length;
            const total = area.trailCount;
            const pct = total ? Math.round((done / total) * 100) : 0;
            const totalMi = area.totalMi;
            return (
              <Link
                key={area.id}
                to="/area/$areaId"
                params={{ areaId: area.id }}
                className="block rounded-3xl p-5 shadow-[var(--shadow-elev)] bg-card border border-border/50 active:scale-[0.99] transition-transform"
                style={{ background: "var(--gradient-card)" }}
              >
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <h3 className="text-2xl font-bold text-foreground">{area.name}</h3>
                    <div className="mt-1 flex items-center gap-1 text-sm text-muted-foreground">
                      <MapPin className="size-3.5" />
                      {area.subtitle}
                    </div>
                  </div>
                  <ChevronRight className="size-5 text-muted-foreground mt-2" />
                </div>

                <div className="mt-5 space-y-2">
                  <div className="flex items-baseline justify-between">
                    <span className="text-sm font-medium text-foreground">
                      {done} <span className="text-muted-foreground">/ {total} trails</span>
                    </span>
                    <span className="text-sm font-bold text-primary tabular-nums">{pct}%</span>
                  </div>
                  <div className="h-2 rounded-full bg-muted overflow-hidden">
                    <div
                      className="h-full rounded-full transition-[width] duration-500"
                      style={{
                        width: `${pct}%`,
                        background: "var(--gradient-sunrise)",
                      }}
                    />
                  </div>
                  <div className="text-xs text-muted-foreground tabular-nums">
                    {totalMi.toFixed(1)} miles total
                  </div>
                </div>
              </Link>
            );
          })
        )}
      </main>
      <LocationPrompt />
    </div>
  );
}
