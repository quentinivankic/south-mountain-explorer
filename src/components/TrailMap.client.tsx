import { lazy, Suspense, useEffect, useState } from "react";
import type { ComponentProps } from "react";
import type { TrailMap as TrailMapType } from "./TrailMap";

const TrailMapLazy = lazy(() =>
  import("./TrailMap").then((m) => ({ default: m.TrailMap })),
);

type Props = ComponentProps<typeof TrailMapType>;

export function TrailMapClient(props: Props) {
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  if (!mounted) {
    return (
      <div className="h-full w-full bg-muted animate-pulse" />
    );
  }
  return (
    <Suspense fallback={<div className="h-full w-full bg-muted animate-pulse" />}>
      <TrailMapLazy {...props} />
    </Suspense>
  );
}
