import { useEffect, useMemo, useState } from "react";
import { X, Mountain, Clock, Route as RouteIcon, TrendingUp, Activity } from "lucide-react";
import type { Trail } from "@/data/trails";
import { elevationsFor, gainFromElevations } from "@/lib/elevation";

interface Props {
  trail: Trail;
  completed: boolean;
  coverage?: number;
  onClose: () => void;
}

function distM(a: [number, number], b: [number, number]) {
  const R = 6371000;
  const dLa = ((b[0] - a[0]) * Math.PI) / 180;
  const dLo = ((b[1] - a[1]) * Math.PI) / 180;
  const la1 = (a[0] * Math.PI) / 180;
  const la2 = (b[0] * Math.PI) / 180;
  const x =
    Math.sin(dLa / 2) ** 2 +
    Math.cos(la1) * Math.cos(la2) * Math.sin(dLo / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(x), Math.sqrt(1 - x));
}

function fmtTime(hours: number) {
  const h = Math.floor(hours);
  const m = Math.round((hours - h) * 60);
  if (h === 0) return `${m}m`;
  if (m === 0) return `${h}h`;
  return `${h}h ${m}m`;
}

export function TrailStatsCard({ trail, completed, coverage, onClose }: Props) {
  // Sample points along the trail (cap to ~80 for elevation lookup).
  const sampled = useMemo(() => {
    const flat: [number, number][] = trail.segments.flatMap((s) => s);
    if (flat.length <= 80) return flat;
    const step = Math.ceil(flat.length / 80);
    return flat.filter((_, i) => i % step === 0);
  }, [trail]);

  // Loop vs out-and-back: same start & end point within 80m?
  const shape = useMemo(() => {
    const seg = trail.segments[0];
    if (!seg || seg.length < 2) return "Trail";
    const start = seg[0];
    const lastSeg = trail.segments[trail.segments.length - 1];
    const end = lastSeg[lastSeg.length - 1];
    return distM(start, end) < 80 ? "Loop" : "Point-to-point";
  }, [trail]);

  // Naede pace assumption baked into time estimate via Naismith's rule
  // (1 hr per 5 km + 1 hr per 600m gain).
  const [gainM, setGainM] = useState<number | null>(null);
  const [loadingElev, setLoadingElev] = useState(true);
  useEffect(() => {
    let cancelled = false;
    setLoadingElev(true);
    elevationsFor(sampled).then((elevs) => {
      if (cancelled) return;
      setGainM(Math.round(gainFromElevations(elevs)));
      setLoadingElev(false);
    });
    return () => {
      cancelled = true;
    };
  }, [sampled]);

  const distanceKm = trail.distanceMi * 1.60934;
  const timeHr =
    distanceKm / 5 + (gainM != null ? gainM / 600 : 0);
  const gainFt = gainM != null ? Math.round(gainM * 3.28084) : null;

  return (
    <div className="pointer-events-auto absolute z-[1000] left-4 right-4 bottom-4 rounded-2xl bg-card/95 backdrop-blur-sm shadow-[var(--shadow-elev)] border border-border/60 overflow-hidden">
      <div className="flex items-start justify-between gap-2 p-4 pb-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            {completed && (
              <span className="text-[10px] font-bold uppercase tracking-wider rounded-full bg-[oklch(0.92_0.06_145)] text-[oklch(0.35_0.09_145)] px-2 py-0.5">
                Done
              </span>
            )}
            <span className="text-[10px] font-bold uppercase tracking-wider rounded-full bg-muted text-muted-foreground px-2 py-0.5">
              {trail.difficulty}
            </span>
          </div>
          <h3 className="mt-1.5 text-lg font-bold leading-tight text-foreground truncate">
            {trail.name}
          </h3>
        </div>
        <button
          onClick={onClose}
          aria-label="Close trail details"
          className="rounded-full p-1.5 -mr-1 -mt-1 text-muted-foreground hover:bg-muted"
        >
          <X className="size-4" />
        </button>
      </div>

      <div className="grid grid-cols-4 gap-2 px-4 pb-4">
        <Stat
          icon={<RouteIcon className="size-3.5" />}
          label="Distance"
          value={`${trail.distanceMi.toFixed(1)} mi`}
        />
        <Stat
          icon={<TrendingUp className="size-3.5" />}
          label="Gain"
          value={
            loadingElev
              ? "…"
              : gainFt != null
                ? `${gainFt.toLocaleString()} ft`
                : "—"
          }
        />
        <Stat
          icon={<Clock className="size-3.5" />}
          label="Est. time"
          value={fmtTime(timeHr)}
        />
        <Stat
          icon={shape === "Loop" ? <Activity className="size-3.5" /> : <Mountain className="size-3.5" />}
          label="Shape"
          value={shape === "Point-to-point" ? "P2P" : shape}
        />
      </div>

      {coverage != null && coverage > 0 && (
        <div className="px-4 pb-4">
          <div className="flex justify-between text-[11px] text-muted-foreground mb-1">
            <span>Your coverage</span>
            <span className="font-semibold tabular-nums text-foreground">
              {Math.round(coverage * 100)}%
            </span>
          </div>
          <div className="h-1.5 rounded-full bg-muted overflow-hidden">
            <div
              className="h-full rounded-full"
              style={{
                width: `${Math.min(100, coverage * 100)}%`,
                background: "var(--gradient-sunrise)",
              }}
            />
          </div>
        </div>
      )}
    </div>
  );
}

function Stat({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
}) {
  return (
    <div className="rounded-xl bg-muted/50 px-2 py-2">
      <div className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-muted-foreground">
        {icon}
        <span className="truncate">{label}</span>
      </div>
      <div className="mt-0.5 text-sm font-bold text-foreground tabular-nums truncate">
        {value}
      </div>
    </div>
  );
}
