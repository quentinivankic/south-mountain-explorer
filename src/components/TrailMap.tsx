import { useEffect } from "react";
import { MapContainer, TileLayer, Polyline, useMap } from "react-leaflet";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import type { Trail } from "@/data/trails";

interface Props {
  center: [number, number];
  zoom: number;
  trails: Trail[];
  completedIds: Set<string>;
  highlightedId?: string | null;
  onSelect?: (id: string) => void;
  className?: string;
}

function FitBounds({ trails }: { trails: Trail[] }) {
  const map = useMap();
  useEffect(() => {
    const all = trails.flatMap((t) => t.segments.flat());
    if (all.length < 2) return;
    const bounds = L.latLngBounds(all.map(([la, lo]) => L.latLng(la, lo)));
    map.fitBounds(bounds, { padding: [24, 24] });
  }, [map, trails]);
  return null;
}

export function TrailMap({
  center,
  zoom,
  trails,
  completedIds,
  highlightedId,
  onSelect,
  className,
}: Props) {
  return (
    <MapContainer
      center={center}
      zoom={zoom}
      className={className}
      style={{ width: "100%", height: "100%" }}
      scrollWheelZoom
      zoomSnap={0.25}
      zoomDelta={0.25}
      wheelPxPerZoomLevel={140}
      zoomControl={false}
    >
      <TileLayer
        attribution='&copy; OpenStreetMap, &copy; CARTO'
        url="https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png"
      />
      <FitBounds trails={trails} />
      {trails.map((t) => {
        const done = completedIds.has(t.id);
        const isHi = highlightedId === t.id;
        return (
          <Polyline
            key={t.id}
            positions={t.segments}
            pathOptions={{
              color: done
                ? "oklch(0.5 0.09 145)"
                : isHi
                  ? "oklch(0.55 0.17 35)"
                  : "oklch(0.45 0.06 40)",
              weight: isHi ? 6 : done ? 4 : 3,
              opacity: done ? 0.95 : 0.85,
              dashArray: done ? undefined : "1,0",
            }}
            eventHandlers={{
              click: () => onSelect?.(t.id),
            }}
          />
        );
      })}
    </MapContainer>
  );
}
