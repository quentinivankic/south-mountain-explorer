import { createFileRoute, Link, notFound } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { ArrowLeft, Check, RotateCcw } from "lucide-react";
import { loadArea, type Area, type Difficulty } from "@/data/trails";
import {
  resetArea,
  toggleTrail,
  useAreaProgress,
  useAuthState,
} from "@/lib/progress";
import { TrailMapClient } from "@/components/TrailMapClient";

export const Route = createFileRoute("/area/$areaId")({
  loader: async ({ params }) => {
    const area = await loadArea(params.areaId);
    if (!area) throw notFound();
    return { area };
  },
  head: ({ loaderData }) => ({
    meta: loaderData
      ? [
          { title: `${loaderData.area.name} — Summit` },
          {
            name: "description",
            content: `Complete every trail in ${loaderData.area.name}, ${loaderData.area.subtitle}.`,
          },
          { property: "og:title", content: `${loaderData.area.name} — Summit` },
        ]
      : [],
  }),
  notFoundComponent: () => (
    <div className="min-h-screen flex items-center justify-center p-6 text-center">
      <div>
        <h1 className="text-2xl font-bold">Area not found</h1>
        <Link to="/" className="text-primary mt-2 inline-block">← Back</Link>
      </div>
    </div>
  ),
  errorComponent: ({ error }) => (
    <div className="min-h-screen flex items-center justify-center p-6 text-center">
      <div>
        <h1 className="text-xl font-bold">Unable to load this area</h1>
        <p className="text-muted-foreground text-sm mt-2">Please try again.</p>
        {import.meta.env.DEV && (
          <pre className="mt-3 text-xs text-left text-muted-foreground whitespace-pre-wrap">{error.message}</pre>
        )}
      </div>
    </div>
  ),
  component: AreaPage,
});

const diffStyles: Record<Difficulty, string> = {
  Easy: "bg-[oklch(0.92_0.06_145)] text-[oklch(0.35_0.09_145)]",
  Moderate: "bg-[oklch(0.92_0.08_75)] text-[oklch(0.4_0.13_60)]",
  Hard: "bg-[oklch(0.92_0.08_30)] text-[oklch(0.42_0.17_30)]",
};

function AreaPage() {
  const { area } = Route.useLoaderData() as { area: Area };
  const progress = useAreaProgress(area.id);
  const userId = useAuthState();
  
  const [highlighted, setHighlighted] = useState<string | null>(null);
  const [sort, setSort] = useState<"name" | "distance" | "difficulty">("distance");

  const handleToggle = (trailId: string) => {
    toggleTrail(area.id, trailId);
  };

  const completedIds = useMemo(() => new Set(Object.keys(progress)), [progress]);
  const done = completedIds.size;
  const total = area.trails.length;
  const pct = total ? Math.round((done / total) * 100) : 0;
  const milesDone = area.trails
    .filter((t) => completedIds.has(t.id))
    .reduce((s, t) => s + t.distanceMi, 0);
  const totalMi = area.trails.reduce((s, t) => s + t.distanceMi, 0);

  const sorted = useMemo(() => {
    const arr = [...area.trails];
    if (sort === "name") arr.sort((a, b) => a.name.localeCompare(b.name));
    if (sort === "distance") arr.sort((a, b) => b.distanceMi - a.distanceMi);
    if (sort === "difficulty") {
      const order: Record<Difficulty, number> = { Easy: 0, Moderate: 1, Hard: 2 };
      arr.sort((a, b) => order[a.difficulty] - order[b.difficulty]);
    }
    return arr;
  }, [area.trails, sort]);

  return (
    <div className="min-h-screen bg-background flex flex-col">
      {/* Top bar */}
      <div className="sticky top-0 z-30 backdrop-blur-md bg-background/85 border-b border-border/60">
        <div className="flex items-center justify-between px-4 py-3">
          <Link
            to="/"
            className="flex items-center gap-1 text-sm font-medium text-foreground"
          >
            <ArrowLeft className="size-5" />
            <span className="sr-only">Back</span>
          </Link>
          <div className="text-center">
            <div className="text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
              {area.subtitle}
            </div>
            <div className="text-base font-bold leading-tight">{area.name}</div>
          </div>
          <button
            onClick={() => {
              if (done > 0 && confirm("Reset all progress for this area?"))
                resetArea(area.id);
            }}
            className="text-muted-foreground p-1"
            aria-label="Reset progress"
          >
            <RotateCcw className="size-4" />
          </button>
        </div>
      </div>

      {/* Map */}
      <div className="relative h-[42vh] min-h-[260px] w-full bg-muted">
        <TrailMapClient
          center={area.center}
          zoom={area.zoom}
          trails={area.trails}
          completedIds={completedIds}
          highlightedId={highlighted}
          onSelect={(id) => {
            setHighlighted(id);
            document.getElementById(`t-${id}`)?.scrollIntoView({
              behavior: "smooth",
              block: "center",
            });
          }}
        />
        {/* Progress badge overlay */}
        <div className="pointer-events-none absolute left-4 right-4 bottom-4 rounded-2xl bg-card/95 backdrop-blur-sm shadow-[var(--shadow-elev)] border border-border/60 p-4">
          <div className="flex items-baseline justify-between">
            <div>
              <div className="text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
                Area completion
              </div>
              <div className="mt-0.5 text-2xl font-black text-foreground tabular-nums">
                {pct}<span className="text-base text-muted-foreground">%</span>
              </div>
            </div>
            <div className="text-right">
              <div className="text-sm font-semibold tabular-nums">
                {done} / {total}
              </div>
              <div className="text-[11px] text-muted-foreground tabular-nums">
                {milesDone.toFixed(1)} / {totalMi.toFixed(1)} mi
              </div>
            </div>
          </div>
          <div className="mt-2 h-1.5 rounded-full bg-muted overflow-hidden">
            <div
              className="h-full rounded-full transition-[width] duration-500"
              style={{ width: `${pct}%`, background: "var(--gradient-sunrise)" }}
            />
          </div>
        </div>
      </div>

      {/* List */}
      <main className="flex-1 px-4 pt-5 pb-12">
        {!userId && done > 0 && (
          <Link
            to="/auth"
            search={{ redirect: `/area/${area.id}` }}
            className="mb-4 block rounded-2xl border border-border/60 bg-card p-3 text-sm text-foreground"
          >
            <div className="font-semibold">Back up your progress</div>
            <div className="text-muted-foreground text-xs mt-0.5">
              Sign in to sync across devices →
            </div>
          </Link>
        )}
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-bold">Trails</h2>
          <div className="flex gap-1 text-xs">
            {(["distance", "name", "difficulty"] as const).map((k) => (
              <button
                key={k}
                onClick={() => setSort(k)}
                className={`px-2.5 py-1 rounded-full font-medium capitalize transition ${
                  sort === k
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:text-foreground"
                }`}
              >
                {k}
              </button>
            ))}
          </div>
        </div>

        <ul className="space-y-2">
          {sorted.map((t) => {
            const isDone = completedIds.has(t.id);
            return (
              <li
                key={t.id}
                id={`t-${t.id}`}
                className={`flex items-center gap-3 p-3 rounded-2xl border transition ${
                  isDone
                    ? "bg-[oklch(0.96_0.05_145)] border-[oklch(0.85_0.08_145)]"
                    : "bg-card border-border/60"
                } ${highlighted === t.id ? "ring-2 ring-primary/50" : ""}`}
                onClick={() => setHighlighted(t.id)}
              >
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    handleToggle(t.id);
                  }}
                  aria-label={isDone ? "Mark incomplete" : "Mark complete"}
                  className={`shrink-0 size-7 rounded-full border-2 flex items-center justify-center transition ${
                    isDone
                      ? "bg-[var(--saguaro)] border-[var(--saguaro)] text-white"
                      : "border-border bg-background"
                  }`}
                >
                  {isDone && <Check className="size-4" strokeWidth={3} />}
                </button>
                <div className="flex-1 min-w-0">
                  <div
                    className={`font-semibold leading-tight truncate ${
                      isDone ? "line-through text-muted-foreground" : "text-foreground"
                    }`}
                  >
                    {t.name}
                  </div>
                  <div className="mt-0.5 flex items-center gap-2 text-xs text-muted-foreground tabular-nums">
                    <span>{t.distanceMi.toFixed(1)} mi</span>
                    <span
                      className={`px-1.5 py-0.5 rounded-full text-[10px] font-semibold ${
                        diffStyles[t.difficulty]
                      }`}
                    >
                      {t.difficulty}
                    </span>
                  </div>
                </div>
              </li>
            );
          })}
        </ul>

        {pct === 100 && (
          <div className="mt-8 p-6 rounded-3xl text-center text-primary-foreground" style={{ background: "var(--gradient-sunrise)" }}>
            <div className="text-3xl">🏔️</div>
            <div className="mt-2 text-xl font-black">You finished {area.name}!</div>
            <div className="opacity-90 text-sm mt-1">All {total} trails. {totalMi.toFixed(1)} miles. Legend status.</div>
          </div>
        )}
      </main>
    </div>
  );
}
