import { Check, X } from "lucide-react";
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
  const walked = trails.filter((t) => finished.walkedTrailIds.includes(t.id));
  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/50 p-4">
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
                {finished.distanceMi.toFixed(2)}<span className="text-sm opacity-80"> mi</span>
              </div>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-wider opacity-80">Time</div>
              <div className="text-2xl font-black tabular-nums">{fmtDur(finished.durationS)}</div>
            </div>
          </div>
        </div>
        <div className="p-5">
          <div className="text-xs font-bold uppercase tracking-wider text-muted-foreground mb-2">
            Trails completed ({walked.length})
          </div>
          {walked.length === 0 ? (
            <div className="text-sm text-muted-foreground py-3">
              No trails were walked end-to-end this time. You can still mark them complete manually.
            </div>
          ) : (
            <ul className="space-y-1.5 max-h-60 overflow-y-auto">
              {walked.map((t) => (
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
          <button
            onClick={onClose}
            className="mt-5 w-full rounded-full bg-primary text-primary-foreground font-bold py-3 text-sm"
          >
            Done
          </button>
        </div>
      </div>
    </div>
  );
}
