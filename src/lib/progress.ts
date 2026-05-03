import { useEffect, useSyncExternalStore } from "react";
import { supabase } from "@/integrations/supabase/client";

const KEY = "summit:completed";
export const GUEST_LIMIT = 3;

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
  if (!userId) persistLocal();
  emit();
}

function countAll(s: State) {
  return Object.values(s).reduce((n, area) => n + Object.keys(area).length, 0);
}

export function totalCompleted(s: State = state) {
  return countAll(s);
}

export function isAtGuestLimit() {
  return !userId && countAll(state) >= GUEST_LIMIT;
}

export function isSignedIn() {
  return !!userId;
}

async function loadFromServer(uid: string) {
  const { data, error } = await supabase
    .from("trail_completions")
    .select("area_id, trail_id, completed_at")
    .eq("user_id", uid);
  if (error) {
    console.error("load completions failed", error);
    return;
  }
  const next: State = {};
  for (const row of data ?? []) {
    next[row.area_id] = next[row.area_id] ?? {};
    next[row.area_id][row.trail_id] = row.completed_at;
  }
  setState(next);
}

async function migrateLocalToServer(uid: string) {
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
  if (error) {
    console.error("migrate failed", error);
    return;
  }
  if (typeof window !== "undefined") localStorage.removeItem(KEY);
}

async function handleSession(uid: string | null) {
  const wasGuest = !userId;
  userId = uid;
  if (uid) {
    if (wasGuest) await migrateLocalToServer(uid);
    await loadFromServer(uid);
  } else {
    setState(readLocal());
  }
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

export async function toggleTrail(areaId: string, trailId: string): Promise<{ blocked?: boolean }> {
  const area = state[areaId] ?? {};
  const wasDone = !!area[trailId];

  if (userId) {
    if (wasDone) {
      const { error } = await supabase
        .from("trail_completions")
        .delete()
        .eq("user_id", userId)
        .eq("area_id", areaId)
        .eq("trail_id", trailId);
      if (error) {
        console.error(error);
        return {};
      }
      const { [trailId]: _, ...rest } = area;
      setState({ ...state, [areaId]: rest });
    } else {
      const completedAt = new Date().toISOString();
      const { error } = await supabase
        .from("trail_completions")
        .insert({ user_id: userId, area_id: areaId, trail_id: trailId, completed_at: completedAt });
      if (error) {
        console.error(error);
        return {};
      }
      setState({ ...state, [areaId]: { ...area, [trailId]: completedAt } });
    }
    return {};
  }

  // Guest
  if (wasDone) {
    const { [trailId]: _, ...rest } = area;
    setState({ ...state, [areaId]: rest });
    return {};
  }
  if (countAll(state) >= GUEST_LIMIT) {
    return { blocked: true };
  }
  setState({ ...state, [areaId]: { ...area, [trailId]: new Date().toISOString() } });
  return {};
}

export async function resetArea(areaId: string) {
  if (userId) {
    const { error } = await supabase
      .from("trail_completions")
      .delete()
      .eq("user_id", userId)
      .eq("area_id", areaId);
    if (error) {
      console.error(error);
      return;
    }
  }
  setState({ ...state, [areaId]: {} });
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
