import { useEffect, useSyncExternalStore } from "react";
import { supabase } from "@/integrations/supabase/client";

const KEY = "summit:completed";

type State = Record<string, Record<string, string>>; // areaId -> trailId -> ISO date
const EMPTY_AREA_PROGRESS: Record<string, string> = {};

function readLocal(): State {
  if (typeof window === "undefined") return {};
  try {
    return JSON.parse(localStorage.getItem(KEY) || "{}");
  } catch {
    return {};
  }
}

let state: State = readLocal();
let userId: string | null = null;
let hydrated = false;
const listeners = new Set<() => void>();

function emit() {
  listeners.forEach((l) => l());
}

function persistLocal() {
  if (typeof window !== "undefined") {
    localStorage.setItem(KEY, JSON.stringify(state));
  }
}

function setState(next: State) {
  state = next;
  persistLocal();
  emit();
}

export function isSignedIn() {
  return !!userId;
}

async function syncFromServer(uid: string) {
  const { data, error } = await supabase
    .from("trail_completions")
    .select("area_id, trail_id, completed_at")
    .eq("user_id", uid);
  if (error) {
    console.error("load completions failed", error);
    return;
  }
  // Merge: server is source of truth, but include any local-only items (already pushed below).
  const next: State = {};
  for (const row of data ?? []) {
    next[row.area_id] = next[row.area_id] ?? {};
    next[row.area_id][row.trail_id] = row.completed_at;
  }
  setState(next);
}

async function pushLocalToServer(uid: string) {
  const local = readLocal();
  const rows: { user_id: string; area_id: string; trail_id: string; completed_at: string }[] = [];
  for (const [areaId, trails] of Object.entries(local)) {
    for (const [trailId, completedAt] of Object.entries(trails)) {
      rows.push({ user_id: uid, area_id: areaId, trail_id: trailId, completed_at: completedAt });
    }
  }
  if (rows.length === 0) return;
  const { error } = await supabase
    .from("trail_completions")
    .upsert(rows, { onConflict: "user_id,area_id,trail_id", ignoreDuplicates: true });
  if (error) console.error("sync up failed", error);
}

async function handleSession(uid: string | null) {
  userId = uid;
  if (uid) {
    await pushLocalToServer(uid);
    await syncFromServer(uid);
  }
  emit();
}

export function initProgress() {
  if (hydrated || typeof window === "undefined") return;
  hydrated = true;
  setState(readLocal());
  supabase.auth.getSession().then(({ data }) => {
    handleSession(data.session?.user.id ?? null);
  });
  supabase.auth.onAuthStateChange((_evt, session) => {
    handleSession(session?.user.id ?? null);
  });
}

export async function toggleTrail(areaId: string, trailId: string) {
  const area = state[areaId] ?? {};
  const wasDone = !!area[trailId];

  if (wasDone) {
    const { [trailId]: _, ...rest } = area;
    setState({ ...state, [areaId]: rest });
    if (userId) {
      const { error } = await supabase
        .from("trail_completions")
        .delete()
        .eq("user_id", userId)
        .eq("area_id", areaId)
        .eq("trail_id", trailId);
      if (error) console.error(error);
    }
  } else {
    const completedAt = new Date().toISOString();
    setState({ ...state, [areaId]: { ...area, [trailId]: completedAt } });
    if (userId) {
      const { error } = await supabase
        .from("trail_completions")
        .insert({ user_id: userId, area_id: areaId, trail_id: trailId, completed_at: completedAt });
      if (error) console.error(error);
    }
  }
}

export async function resetArea(areaId: string) {
  setState({ ...state, [areaId]: {} });
  if (userId) {
    const { error } = await supabase
      .from("trail_completions")
      .delete()
      .eq("user_id", userId)
      .eq("area_id", areaId);
    if (error) console.error(error);
  }
}

function subscribe(cb: () => void) {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

export function useAreaProgress(areaId: string) {
  useEffect(() => {
    initProgress();
  }, []);
  return useSyncExternalStore(
    subscribe,
    () => state[areaId] ?? EMPTY_AREA_PROGRESS,
    () => EMPTY_AREA_PROGRESS,
  );
}

export function useAllProgress() {
  useEffect(() => {
    initProgress();
  }, []);
  return useSyncExternalStore(
    subscribe,
    () => state,
    () => state,
  );
}

export function useAuthState() {
  useEffect(() => {
    initProgress();
  }, []);
  return useSyncExternalStore(
    subscribe,
    () => userId,
    () => null,
  );
}
