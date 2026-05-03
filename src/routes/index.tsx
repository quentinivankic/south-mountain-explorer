import { createFileRoute, Link } from "@tanstack/react-router";
import { areas } from "@/data/trails";
import { Mountain, MapPin, ChevronRight, LogOut, LogIn } from "lucide-react";
import { useAllProgress, useAuthState } from "@/lib/progress";
import { supabase } from "@/integrations/supabase/client";

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

  return (
    <div className="min-h-screen bg-background">
      {/* Hero */}
      <header className="relative overflow-hidden">
        <div
          className="absolute inset-0"
          style={{ background: "var(--gradient-sunrise)" }}
          aria-hidden
        />
        <div className="relative px-6 pt-14 pb-10 text-primary-foreground">
          <div className="flex items-center gap-2 text-sm/none opacity-90">
            <Mountain className="size-4" />
            <span className="uppercase tracking-[0.2em] text-xs">Summit</span>
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
      <main className="px-5 -mt-6 pb-24 space-y-4">
        <h2 className="sr-only">Hiking areas</h2>
        {areas.map((area) => {
          const done = Object.keys(progress[area.id] ?? {}).length;
          const total = area.trails.length;
          const pct = total ? Math.round((done / total) * 100) : 0;
          const milesDone = area.trails
            .filter((t) => progress[area.id]?.[t.id])
            .reduce((s, t) => s + t.distanceMi, 0);
          const totalMi = area.trails.reduce((s, t) => s + t.distanceMi, 0);
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
                  <h3 className="text-2xl font-bold text-foreground">
                    {area.name}
                  </h3>
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
                  <span className="text-sm font-bold text-primary tabular-nums">
                    {pct}%
                  </span>
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
                  {milesDone.toFixed(1)} of {totalMi.toFixed(1)} miles complete
                </div>
              </div>
            </Link>
          );
        })}

        <p className="pt-4 text-center text-xs text-muted-foreground">
          More areas coming soon · Camelback · Piestewa Peak · McDowell Sonoran
        </p>
      </main>
    </div>
  );
}
