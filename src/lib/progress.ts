import { useEffect, useSyncExternalStore } from "react";

const KEY = "summit:completed";

type State = Record<string, Record<string, string>>; // areaId -> trailId -> ISO date

function read(): State {
  if (typeof window === "undefined") return {};
  try {
    return JSON.parse(localStorage.getItem(KEY) || "{}");
  } catch {
    return {};
  }
}

let state: State = read();
const listeners = new Set<() => void>();

function emit() {
  listeners.forEach((l) => l());
}

function persist() {
  localStorage.setItem(KEY, JSON.stringify(state));
  emit();
}

export function toggleTrail(areaId: string, trailId: string) {
  const area = state[areaId] ?? {};
  if (area[trailId]) {
    delete area[trailId];
  } else {
    area[trailId] = new Date().toISOString();
  }
  state = { ...state, [areaId]: { ...area } };
  persist();
}

export function resetArea(areaId: string) {
  state = { ...state, [areaId]: {} };
  persist();
}

function subscribe(cb: () => void) {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

export function useAreaProgress(areaId: string) {
  // Hydrate from localStorage on client mount (SSR-safe)
  useEffect(() => {
    state = read();
    emit();
  }, []);
  return useSyncExternalStore(
    subscribe,
    () => state[areaId] ?? {},
    () => ({}),
  );
}
