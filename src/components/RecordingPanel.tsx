import { useEffect, useState } from "react";
import { Square, Trash2, Loader2 } from "lucide-react";
import { stopRecording, discardRecording, type ActiveRecording, type FinishedRecording } from "@/lib/recorder";
import type { Trail } from "@/data/trails";

function fmtDur(s: number) {
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return h > 0
    ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`
    : `${m}:${String(sec).padStart(2, "0")}`;
}

interface Props {
  active: ActiveRecording;
  trails: Trail[];
  error: string | null;
  onFinish: (r: FinishedRecording) => void;
}

export function RecordingPanel({ active, trails, error, onFinish }: Props) {
  const [now, setNow] = useState(Date.now());
  const [stopping, setStopping] = useState(false);

  useEffect(() => {
    const i = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(i);
  }, []);

  const dur = Math.max(0, Math.round((now - active.startedAt) / 1000));

  const handleStop = async () => {
    if (stopping) return;
    setStopping(true);
    const r = await stopRecording(trails);
    setStopping(false);
    if (r) onFinish(r);
  };

  const handleDiscard = () => {
    if (confirm("Discard this recording? It won't be saved.")) {
      discardRecording();
    }
  };

  return (
    <div className="pointer-events-auto absolute z-[1000] left-4 right-4 bottom-4 rounded-2xl bg-card/95 backdrop-blur-sm shadow-[var(--shadow-elev)] border border-border/60 p-4">
      <div className="flex items-center gap-2">
        <span className="relative flex size-2.5">
          <span className="absolute inline-flex h-full w-full rounded-full bg-destructive/60 animate-ping" />
          <span className="relative inline-flex size-2.5 rounded-full bg-destructive" />
        </span>
        <span className="text-[11px] uppercase tracking-[0.18em] font-bold text-destructive">
          Recording
        </span>
      </div>
      <div className="mt-2 grid grid-cols-3 gap-2">
        <div>
          <div className="text-[10px] uppercase tracking-wider text-muted-foreground">Time</div>
          <div className="text-xl font-black tabular-nums">{fmtDur(dur)}</div>
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-wider text-muted-foreground">Distance</div>
          <div className="text-xl font-black tabular-nums">{active.distanceMi.toFixed(2)}<span className="text-xs text-muted-foreground"> mi</span></div>
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-wider text-muted-foreground">Points</div>
          <div className="text-xl font-black tabular-nums">{active.path.length}</div>
        </div>
      </div>
      {error && (
        <div className="mt-2 text-xs text-destructive">{error}</div>
      )}
      <div className="mt-3 flex gap-2">
        <button
          onClick={handleStop}
          disabled={stopping}
          className="flex-1 inline-flex items-center justify-center gap-2 rounded-full bg-primary text-primary-foreground py-2.5 font-bold text-sm disabled:opacity-60"
        >
          {stopping ? <Loader2 className="size-4 animate-spin" /> : <Square className="size-4 fill-current" />}
          {stopping ? "Saving…" : "Finish hike"}
        </button>
        <button
          onClick={handleDiscard}
          className="rounded-full border border-border bg-background p-2.5 text-muted-foreground"
          aria-label="Discard"
        >
          <Trash2 className="size-4" />
        </button>
      </div>
    </div>
  );
}
