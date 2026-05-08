import { createFileRoute, Link, notFound } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { ArrowLeft, Check, RotateCcw, Play, History as HistoryIcon, Compass, MapPinned, X } from "lucide-react";
import { loadArea, type Area, type Difficulty } from "@/data/trails";
import {
  resetArea,
  toggleTrail,
  useAreaProgress,
  useAuthState,
} from "@/lib/progress";
import { TrailMapClient } from "@/components/TrailMapClient";
import { startRecording, useRecorder, type FinishedRecording } from "@/lib/recorder";
import { RecordingPanel } from "@/components/RecordingPanel";
import { RecordingSummary } from "@/components/RecordingSummary";
import { useAreaCoverage } from "@/lib/coverage";
import { useLiveLocation, useLocation } from "@/lib/location";

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
  const coverage = useAreaCoverage(area.id);
  const userId = useAuthState();
  const { active: recording, error: recError } = useRecorder();
  const [finished, setFinished] = useState<FinishedRecording | null>(null);
  const [picking, setPicking] = useState(false);

  const [highlighted, setHighlighted] = useState<string | null>(null);
  const [sort, setSort] = useState<"name" | "distance" | "difficulty">("distance");

  const handleToggle = (trailId: string) => {
    toggleTrail(area.id, trailId);
  };

  const isRecordingThisArea = recording?.areaId === area.id;
  const livePath: [number, number][] | undefined = isRecordingThisArea
    ? recording!.path.map((p) => [p[0], p[1]] as [number, number])
    : undefined;
  const storedLoc = useLocation();
  // Watch the device location whenever the user has granted permission, so
  // the "you are here" dot shows even before they start recording.
  const liveLoc = useLiveLocation(storedLoc.status === "granted");
  const liveCurrent: [number, number] | null = livePath && livePath.length
    ? livePath[livePath.length - 1]
    : liveLoc;

  const beginStart = (mode: "roam" | "trail", trailId?: string) => {
    if (recording && recording.areaId !== area.id) {
      if (
        !confirm(
          "You have a recording in progress for another area. Discard it and start here?",
        )
      )
        return;
    }
    startRecording(area.id, mode, trailId);
    setPicking(false);
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
          <div className="flex items-center gap-1">
            {userId && (
              <Link to="/history" className="text-muted-foreground p-1" aria-label="History">
                <HistoryIcon className="size-4" />
              </Link>
            )}
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
      </div>

      {/* Map */}
      <div className="relative h-[42vh] min-h-[260px] w-full bg-muted">
        <TrailMapClient
          center={area.center}
          zoom={area.zoom}
          trails={area.trails}
          completedIds={completedIds}
          highlightedId={highlighted}
          livePath={livePath}
          liveCurrent={liveCurrent}
          onSelect={(id) => {
            setHighlighted(id);
            document.getElementById(`t-${id}`)?.scrollIntoView({
              behavior: "smooth",
              block: "center",
            });
          }}
        />
        {isRecordingThisArea && recording ? (
          <RecordingPanel
            active={recording}
            trails={area.trails}
            error={recError}
            onFinish={(r) => setFinished(r)}
          />
        ) : (
          <div className="pointer-events-none absolute z-[1000] left-4 right-4 bottom-4 rounded-2xl bg-card/95 backdrop-blur-sm shadow-[var(--shadow-elev)] border border-border/60 p-4">
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
            <button
              onClick={() => setPicking(true)}
              className="pointer-events-auto mt-3 w-full inline-flex items-center justify-center gap-2 rounded-full bg-primary text-primary-foreground py-2.5 font-bold text-sm"
            >
              <Play className="size-4 fill-current" /> Record a hike
            </button>
          </div>
        )}
      </div>

      {finished && (
        <RecordingSummary
          finished={finished}
          trails={area.trails}
          onClose={() => setFinished(null)}
        />
      )}

      {picking && (
        <div
          className="fixed inset-0 z-[2000] flex items-end sm:items-center justify-center bg-black/50 p-4"
          onClick={() => setPicking(false)}
        >
          <div
            className="w-full max-w-md rounded-3xl bg-card border border-border shadow-[var(--shadow-elev)] overflow-hidden"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between p-4 border-b border-border/60">
              <div className="font-bold">Start a hike</div>
              <button
                onClick={() => setPicking(false)}
                className="text-muted-foreground p-1"
                aria-label="Close"
              >
                <X className="size-4" />
              </button>
            </div>
            <div className="p-4 space-y-2">
              <button
                onClick={() => beginStart("roam")}
                className="w-full flex items-start gap-3 text-left p-4 rounded-2xl border border-border/60 hover:border-primary/50 transition"
              >
                <Compass className="size-5 mt-0.5 text-primary shrink-0" />
                <div>
                  <div className="font-bold">Free roam</div>
                  <div className="text-xs text-muted-foreground mt-0.5">
                    Wander wherever. Any trail you walk gets credit — finish a partial trail across multiple hikes.
                  </div>
                </div>
              </button>
              <div>
                <div className="px-1 pt-2 pb-1 text-[10px] uppercase tracking-wider text-muted-foreground font-bold">
                  Or pick a specific trail
                </div>
                <div className="max-h-[40vh] overflow-y-auto rounded-2xl border border-border/60 divide-y divide-border/60">
                  {sorted
                    .filter((t) => !completedIds.has(t.id))
                    .map((t) => (
                      <button
                        key={t.id}
                        onClick={() => beginStart("trail", t.id)}
                        className="w-full flex items-center gap-3 p-3 text-left hover:bg-muted/50"
                      >
                        <MapPinned className="size-4 text-muted-foreground shrink-0" />
                        <div className="flex-1 min-w-0">
                          <div className="text-sm font-semibold truncate">{t.name}</div>
                          <div className="text-[11px] text-muted-foreground tabular-nums">
                            {t.distanceMi.toFixed(1)} mi · {t.difficulty}
                          </div>
                        </div>
                      </button>
                    ))}
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

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
            const cov = coverage[t.id] ?? 0;
            const covPct = Math.round(cov * 100);
            return (
              <li
                key={t.id}
                id={`t-${t.id}`}
                className={`p-3 rounded-2xl border transition ${
                  isDone
                    ? "bg-[oklch(0.96_0.05_145)] border-[oklch(0.85_0.08_145)]"
                    : "bg-card border-border/60"
                } ${highlighted === t.id ? "ring-2 ring-primary/50" : ""}`}
                onClick={() => setHighlighted(t.id)}
              >
                <div className="flex items-center gap-3">
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
                      {!isDone && cov > 0 && (
                        <span className="text-primary font-semibold">{covPct}%</span>
                      )}
                    </div>
                  </div>
                  {!isRecordingThisArea && !isDone && (
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        beginStart("trail", t.id);
                      }}
                      aria-label="Record this trail"
                      className="shrink-0 size-8 rounded-full bg-primary/10 text-primary flex items-center justify-center"
                    >
                      <Play className="size-3.5 fill-current" />
                    </button>
                  )}
                </div>
                {!isDone && cov > 0 && (
                  <div className="mt-2 h-1 rounded-full bg-muted overflow-hidden">
                    <div
                      className="h-full rounded-full"
                      style={{
                        width: `${covPct}%`,
                        background: "var(--gradient-sunrise)",
                      }}
                    />
                  </div>
                )}
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
