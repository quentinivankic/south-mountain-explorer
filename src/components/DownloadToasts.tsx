import { Check, Download, AlertCircle, MapPin, Route as RouteIcon } from "lucide-react";
import { useDownloads, type DownloadEntry } from "@/lib/downloads";

export function DownloadToasts() {
  const items = useDownloads();
  if (items.length === 0) return null;
  return (
    <div className="pointer-events-none fixed left-1/2 -translate-x-1/2 bottom-4 z-[3000] flex w-[min(92vw,420px)] flex-col gap-2">
      {items.map((d) => (
        <Row key={d.id} d={d} />
      ))}
    </div>
  );
}

function Row({ d }: { d: DownloadEntry }) {
  const pct = d.total > 0 ? Math.min(100, Math.round((d.done / d.total) * 100)) : null;
  const Icon =
    d.status === "error"
      ? AlertCircle
      : d.status === "done"
        ? Check
        : d.kind === "tiles"
          ? MapPin
          : RouteIcon;

  return (
    <div className="pointer-events-auto rounded-2xl border border-border/60 bg-card/95 backdrop-blur-md shadow-[var(--shadow-elev)] p-3">
      <div className="flex items-center gap-3">
        <div
          className={`shrink-0 size-8 rounded-full flex items-center justify-center ${
            d.status === "error"
              ? "bg-destructive/10 text-destructive"
              : d.status === "done"
                ? "bg-[oklch(0.92_0.06_145)] text-[oklch(0.35_0.09_145)]"
                : "bg-primary/10 text-primary"
          }`}
        >
          <Icon className="size-4" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-sm font-semibold truncate">
            {d.status === "done"
              ? `Saved ${d.label}`
              : d.status === "error"
                ? `Couldn't save ${d.label}`
                : `Saving ${d.label}`}
          </div>
          <div className="text-[11px] text-muted-foreground tabular-nums truncate">
            {d.status === "running"
              ? d.kind === "tiles"
                ? `Map tiles · ${d.done}${d.total ? ` / ${d.total}` : ""}`
                : "Trail data"
              : d.message ?? (d.kind === "tiles" ? "Map ready offline" : "Available offline")}
          </div>
        </div>
        {d.status === "running" && pct === null && (
          <Download className="size-4 text-muted-foreground animate-pulse" />
        )}
      </div>
      {d.status === "running" && (
        <div className="mt-2 h-1 rounded-full bg-muted overflow-hidden">
          <div
            className={`h-full rounded-full ${pct === null ? "animate-pulse w-1/3" : "transition-[width]"}`}
            style={{
              width: pct === null ? undefined : `${pct}%`,
              background: "var(--gradient-sunrise)",
            }}
          />
        </div>
      )}
    </div>
  );
}
