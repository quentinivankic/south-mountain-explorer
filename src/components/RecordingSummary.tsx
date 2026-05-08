import { Check, X, Sparkles } from "lucide-react";
import type { FinishedRecording } from "@/lib/recorder";
import type { Trail } from "@/data/trails";

interface Props {
  finished: FinishedRecording;
  trails: Trail[];
  onClose: () => void;
}

function fmtDur(s: number) {
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

export function RecordingSummary({ finished, trails, onClose }: Props) {
  const completed = trails.filter((t) =>
    finished.newlyCompletedTrailIds.includes(t.id),
  );
  // Trails this session contributed to but didn't finish off
  const partials = Object.entries(finished.coverageDelta)
    .filter(([id]) => !finished.newlyCompletedTrailIds.includes(id))
    .map(([id, frac]) => ({ trail: trails.find((t) => t.id === id), frac }))
    .filter((x): x is { trail: Trail; frac: number } => !!x.trail)
    .sort((a, b) => b.frac - a.frac)
    .slice(0, 8);

  return (
    <div className="fixed inset-0 z-[2000] flex items-end sm:items-center justify-center bg-black/50 p-4">
      <div className="w-full max-w-md rounded-3xl bg-card border border-border shadow-[var(--shadow-elev)] overflow-hidden">
        <div
          className="p-6 text-primary-foreground relative"
          style={{ background: "var(--gradient-sunrise)" }}
        >
          <button
            onClick={onClose}
            className="absolute top-3 right-3 p-1 rounded-full opacity-80 hover:opacity-100"
            aria-label="Close"
          >
            <X className="size-5" />
          </button>
          <div className="text-3xl">🥾</div>
          <div className="mt-1 text-2xl font-black">Hike saved!</div>
          <div className="mt-3 grid grid-cols-2 gap-2">
            <div>
              <div className="text-[10px] uppercase tracking-wider opacity-80">Distance</div>
              <div className="text-2xl font-black tabular-nums">
                {finished.distanceMi.toFixed(2)}
                <span className="text-sm opacity-80"> mi</span>
              </div>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-wider opacity-80">Time</div>
              <div className="text-2xl font-black tabular-nums">
                {fmtDur(finished.durationS)}
              </div>
            </div>
          </div>
        </div>
        <div className="p-5 space-y-4 max-h-[60vh] overflow-y-auto">
          <div>
            <div className="text-xs font-bold uppercase tracking-wider text-muted-foreground mb-2">
              Trails completed ({completed.length})
            </div>
            {completed.length === 0 ? (
              <div className="text-sm text-muted-foreground">
                None this hike — keep at it, partial progress is saved below.
              </div>
            ) : (
              <ul className="space-y-1.5">
                {completed.map((t) => (
                  <li key={t.id} className="flex items-center gap-2 text-sm">
                    <span className="size-5 rounded-full bg-[var(--saguaro)] text-white flex items-center justify-center shrink-0">
                      <Check className="size-3" strokeWidth={3} />
                    </span>
                    <span className="truncate">{t.name}</span>
                    <span className="ml-auto text-xs text-muted-foreground tabular-nums">
                      {t.distanceMi.toFixed(1)} mi
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>

          {partials.length > 0 && (
            <div>
              <div className="text-xs font-bold uppercase tracking-wider text-muted-foreground mb-2 flex items-center gap-1">
                <Sparkles className="size-3" /> Progress on
              </div>
              <ul className="space-y-2">
                {partials.map(({ trail, frac }) => (
                  <li key={trail.id}>
                    <div className="flex items-center justify-between gap-2 text-sm">
                      <span className="truncate">{trail.name}</span>
                      <span className="text-xs text-muted-foreground tabular-nums">
                        {Math.round(frac * 100)}%
                      </span>
                    </div>
                    <div className="mt-1 h-1.5 rounded-full bg-muted overflow-hidden">
                      <div
                        className="h-full rounded-full"
                        style={{
                          width: `${Math.round(frac * 100)}%`,
                          background: "var(--gradient-sunrise)",
                        }}
                      />
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <button
            onClick={onClose}
            className="w-full rounded-full bg-primary text-primary-foreground font-bold py-3 text-sm"
          >
            Done
          </button>
        </div>
      </div>
    </div>
  );
}
