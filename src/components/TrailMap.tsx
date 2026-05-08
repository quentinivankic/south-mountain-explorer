import { useEffect } from "react";
import { MapContainer, Polyline, Marker, useMap } from "react-leaflet";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import type { Trail } from "@/data/trails";
import { getCachedTile, storeTile } from "@/lib/tilePrefetch";

// Custom tile layer: try Cache Storage first, fall back to network and
// write through. Lets favorited areas render their basemap fully offline.
const CachedTileLayer = L.TileLayer.extend({
  createTile(coords: L.Coords, done: (err: Error | null, tile: HTMLImageElement) => void) {
    const tile = document.createElement("img");
    tile.alt = "";
    tile.setAttribute("role", "presentation");
    const url = (this as unknown as L.TileLayer).getTileUrl(coords);
    (async () => {
      try {
        const blob = await getCachedTile(url);
        if (blob) {
          tile.src = URL.createObjectURL(blob);
          tile.onload = () => done(null, tile);
          return;
        }
        const res = await fetch(url, { mode: "cors" });
        if (res.ok) {
          storeTile(url, res.clone());
          tile.src = URL.createObjectURL(await res.blob());
          tile.onload = () => done(null, tile);
        } else {
          tile.src = url; // last resort
          tile.onload = () => done(null, tile);
        }
      } catch {
        tile.src = url;
        tile.onload = () => done(null, tile);
      }
    })();
    return tile;
  },
});

interface Props {
  center: [number, number];
  zoom: number;
  trails: Trail[];
  completedIds: Set<string>;
  highlightedId?: string | null;
  onSelect?: (id: string) => void;
  className?: string;
  livePath?: [number, number][];
  liveCurrent?: [number, number] | null;
}

function CachedBasemap() {
  const map = useMap();
  useEffect(() => {
    const layer = new (CachedTileLayer as unknown as new (
      url: string,
      opts: L.TileLayerOptions,
    ) => L.TileLayer)(
      "https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png",
      {
        subdomains: ["a", "b", "c", "d"],
        attribution: "&copy; OpenStreetMap, &copy; CARTO",
        maxZoom: 19,
      },
    );
    layer.addTo(map);
    return () => {
      layer.remove();
    };
  }, [map]);
  return null;
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
  livePath,
  liveCurrent,
}: Props) {
  const liveIcon = L.divIcon({
    className: "",
    html: '<div style="width:18px;height:18px;border-radius:9999px;background:oklch(0.55 0.17 35);border:3px solid white;box-shadow:0 0 0 4px oklch(0.55 0.17 35 / 0.25);"></div>',
    iconSize: [18, 18],
    iconAnchor: [9, 9],
  });
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
      <CachedBasemap />
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
      {livePath && livePath.length > 1 && (
        <Polyline
          positions={livePath}
          pathOptions={{
            color: "oklch(0.55 0.17 35)",
            weight: 5,
            opacity: 0.9,
            dashArray: "2,6",
          }}
        />
      )}
      {liveCurrent && (
        <Marker position={liveCurrent} icon={liveIcon} interactive={false} />
      )}
    </MapContainer>
  );
}
