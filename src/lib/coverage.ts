import { useEffect, useSyncExternalStore } from "react";
import { supabase } from "@/integrations/supabase/client";

const KEY = "summit:coverage";

// areaId -> trailId -> coverage (0..1)
type State = Record<string, Record<string, number>>;
const EMPTY: Record<string, number> = {};

let state: State = {};
let userId: string | null = null;
let hydrated = false;
const listeners = new Set<() => void>();

function readLocal(): State {
  if (typeof window === "undefined") return {};
  try {
    return JSON.parse(localStorage.getItem(KEY) || "{}");
  } catch {
    return {};
  }
}

function writeLocal() {
  if (typeof window !== "undefined") {
    localStorage.setItem(KEY, JSON.stringify(state));
  }
}

function emit() {
  writeLocal();
  listeners.forEach((l) => l());
}

async function syncFromServer(uid: string) {
  const { data, error } = await supabase
    .from("trail_coverage")
    .select("area_id, trail_id, coverage")
    .eq("user_id", uid);
  if (error) {
    console.error("load coverage failed", error);
    return;
  }
  const next: State = {};
  for (const row of data ?? []) {
    next[row.area_id] = next[row.area_id] ?? {};
    next[row.area_id][row.trail_id] = Math.max(
      next[row.area_id][row.trail_id] ?? 0,
      Number(row.coverage),
    );
  }
  // merge with anything local that's higher (in-flight changes)
  for (const [aid, trails] of Object.entries(state)) {
    next[aid] = next[aid] ?? {};
    for (const [tid, v] of Object.entries(trails)) {
      next[aid][tid] = Math.max(next[aid][tid] ?? 0, v);
    }
  }
  state = next;
  emit();
}

async function pushLocalToServer(uid: string) {
  const rows: { user_id: string; area_id: string; trail_id: string; coverage: number }[] = [];
  for (const [aid, trails] of Object.entries(state)) {
    for (const [tid, v] of Object.entries(trails)) {
      if (v > 0) rows.push({ user_id: uid, area_id: aid, trail_id: tid, coverage: v });
    }
  }
  if (rows.length === 0) return;
  const { error } = await supabase
    .from("trail_coverage")
    .upsert(rows, { onConflict: "user_id,area_id,trail_id" });
  if (error) console.error("coverage sync up failed", error);
}

export function initCoverage() {
  if (hydrated || typeof window === "undefined") return;
  hydrated = true;
  state = readLocal();
  emit();
  supabase.auth.getSession().then(async ({ data }) => {
    userId = data.session?.user.id ?? null;
    if (userId) {
      await pushLocalToServer(userId);
      await syncFromServer(userId);
    }
  });
  supabase.auth.onAuthStateChange(async (_evt, session) => {
    userId = session?.user.id ?? null;
    if (userId) {
      await pushLocalToServer(userId);
      await syncFromServer(userId);
    }
  });
}

export function getCoverage(): State {
  return state;
}

/** Merge incremental coverage from a recording. Persists to server. */
export async function mergeCoverage(
  areaId: string,
  delta: Record<string, number>,
) {
  const area = { ...(state[areaId] ?? {}) };
  const rows: { user_id: string; area_id: string; trail_id: string; coverage: number }[] = [];
  for (const [tid, v] of Object.entries(delta)) {
    const next = Math.min(1, Math.max(area[tid] ?? 0, v));
    if (next !== area[tid]) {
      area[tid] = next;
      if (userId) {
        rows.push({ user_id: userId, area_id: areaId, trail_id: tid, coverage: next });
      }
    }
  }
  state = { ...state, [areaId]: area };
  emit();
  if (userId && rows.length) {
    const { error } = await supabase
      .from("trail_coverage")
      .upsert(rows, { onConflict: "user_id,area_id,trail_id" });
    if (error) console.error(error);
  }
}

export async function resetAreaCoverage(areaId: string) {
  state = { ...state, [areaId]: {} };
  emit();
  if (userId) {
    await supabase
      .from("trail_coverage")
      .delete()
      .eq("user_id", userId)
      .eq("area_id", areaId);
  }
}

function subscribe(cb: () => void) {
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
}

export function useAreaCoverage(areaId: string) {
  useEffect(() => {
    initCoverage();
  }, []);
  return useSyncExternalStore(
    subscribe,
    () => state[areaId] ?? EMPTY,
    () => EMPTY,
  );
}
