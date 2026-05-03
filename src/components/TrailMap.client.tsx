import { ClientOnly } from "@tanstack/react-router";
import { lazy, Suspense } from "react";
import type { Trail } from "@/data/trails";

const TrailMapLazy = lazy(async () => {
  const m = await import("./TrailMap");
  return { default: m.TrailMap };
});

interface Props {
  center: [number, number];
  zoom: number;
  trails: Trail[];
  completedIds: Set<string>;
  highlightedId?: string | null;
  onSelect?: (id: string) => void;
  className?: string;
}

export function TrailMapClient(props: Props) {
  const fallback = <div className="h-full w-full bg-muted animate-pulse" />;
  return (
    <ClientOnly fallback={fallback}>
      <Suspense fallback={fallback}>
        <TrailMapLazy {...props} />
      </Suspense>
    </ClientOnly>
  );
}
