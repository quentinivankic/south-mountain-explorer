// Privacy-first geolocation: ask once, store outcome locally.
// Coordinates never leave the device — used only for client-side ranking.
import { useEffect, useState, useSyncExternalStore } from "react";

const KEY = "summit:location";

export type LocationStatus = "unset" | "granted" | "denied" | "unsupported";

export interface StoredLocation {
  status: LocationStatus;
  lat?: number;
  lon?: number;
  /** ms since epoch */
  at?: number;
}

const listeners = new Set<() => void>();
let cached: StoredLocation = { status: "unset" };
let hydrated = false;

function read(): StoredLocation {
  if (typeof window === "undefined") return { status: "unset" };
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return { status: "unset" };
    return JSON.parse(raw) as StoredLocation;
  } catch {
    return { status: "unset" };
  }
}

function snapshot(): StoredLocation {
  if (!hydrated && typeof window !== "undefined") {
    cached = read();
    hydrated = true;
  }
  return cached;
}

function write(v: StoredLocation) {
  if (typeof window !== "undefined") {
    localStorage.setItem(KEY, JSON.stringify(v));
  }
  listeners.forEach((l) => l());
}

export function getLocation(): StoredLocation {
  return read();
}

export async function requestLocation(): Promise<StoredLocation> {
  if (typeof navigator === "undefined" || !navigator.geolocation) {
    const v: StoredLocation = { status: "unsupported", at: Date.now() };
    write(v);
    return v;
  }
  return new Promise((resolve) => {
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        const v: StoredLocation = {
          status: "granted",
          lat: Number(pos.coords.latitude.toFixed(4)),
          lon: Number(pos.coords.longitude.toFixed(4)),
          at: Date.now(),
        };
        write(v);
        resolve(v);
      },
      () => {
        const v: StoredLocation = { status: "denied", at: Date.now() };
        write(v);
        resolve(v);
      },
      { enableHighAccuracy: false, maximumAge: 60 * 60 * 1000, timeout: 10000 },
    );
  });
}

export function skipLocation() {
  write({ status: "denied", at: Date.now() });
}

export function clearLocation() {
  if (typeof window !== "undefined") localStorage.removeItem(KEY);
  listeners.forEach((l) => l());
}

function subscribe(cb: () => void) {
  listeners.add(cb);
  if (typeof window !== "undefined") {
    const onStorage = (e: StorageEvent) => {
      if (e.key === KEY) cb();
    };
    window.addEventListener("storage", onStorage);
    return () => {
      listeners.delete(cb);
      window.removeEventListener("storage", onStorage);
    };
  }
  return () => listeners.delete(cb);
}

export function useLocation(): StoredLocation {
  return useSyncExternalStore(
    subscribe,
    () => read(),
    () => ({ status: "unset" as const }),
  );
}

/** Haversine distance in miles. */
export function distanceMi(
  a: [number, number],
  b: [number, number],
): number {
  const R = 3958.8;
  const dLa = ((b[0] - a[0]) * Math.PI) / 180;
  const dLo = ((b[1] - a[1]) * Math.PI) / 180;
  const la1 = (a[0] * Math.PI) / 180;
  const la2 = (b[0] * Math.PI) / 180;
  const x =
    Math.sin(dLa / 2) ** 2 +
    Math.cos(la1) * Math.cos(la2) * Math.sin(dLo / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(x), Math.sqrt(1 - x));
}

/** Hook that auto-prompts on first launch unless already answered. */
export function useFirstLaunchLocationPrompt() {
  const [shouldShow, setShouldShow] = useState(false);
  useEffect(() => {
    const v = read();
    if (v.status === "unset") setShouldShow(true);
  }, []);
  return {
    shouldShow,
    dismiss: () => {
      skipLocation();
      setShouldShow(false);
    },
    grant: async () => {
      await requestLocation();
      setShouldShow(false);
    },
  };
}
