import { useEffect, useSyncExternalStore } from "react";
import { supabase } from "@/integrations/supabase/client";

const KEY = "summit:favorites";

let favorites: Set<string> = new Set();
let userId: string | null = null;
let hydrated = false;
const listeners = new Set<() => void>();

function emit() {
  // new reference so useSyncExternalStore notices
  favorites = new Set(favorites);
  listeners.forEach((l) => l());
}

function readLocal(): string[] {
  if (typeof window === "undefined") return [];
  try {
    return JSON.parse(localStorage.getItem(KEY) || "[]");
  } catch {
    return [];
  }
}

function writeLocal() {
  if (typeof window !== "undefined") {
    localStorage.setItem(KEY, JSON.stringify([...favorites]));
  }
}

async function syncFromServer(uid: string) {
  const { data, error } = await supabase
    .from("area_favorites")
    .select("area_id")
    .eq("user_id", uid);
  if (error) {
    console.error("load favorites failed", error);
    return;
  }
  favorites = new Set(data?.map((r) => r.area_id) ?? []);
  writeLocal();
  emit();
}

async function pushLocalToServer(uid: string) {
  const local = readLocal();
  if (local.length === 0) return;
  const rows = local.map((area_id) => ({ user_id: uid, area_id }));
  const { error } = await supabase
    .from("area_favorites")
    .upsert(rows, { onConflict: "user_id,area_id", ignoreDuplicates: true });
  if (error) console.error("favorites sync up failed", error);
}

async function handleSession(uid: string | null) {
  userId = uid;
  if (uid) {
    await pushLocalToServer(uid);
    await syncFromServer(uid);
  }
  emit();
}

export function initFavorites() {
  if (hydrated || typeof window === "undefined") return;
  hydrated = true;
  favorites = new Set(readLocal());
  emit();
  supabase.auth.getSession().then(({ data }) => {
    handleSession(data.session?.user.id ?? null);
  });
  supabase.auth.onAuthStateChange((_evt, session) => {
    handleSession(session?.user.id ?? null);
  });
}

export async function toggleFavorite(areaId: string) {
  const isFav = favorites.has(areaId);
  if (isFav) {
    favorites.delete(areaId);
  } else {
    favorites.add(areaId);
  }
  writeLocal();
  emit();

  if (userId) {
    if (isFav) {
      const { error } = await supabase
        .from("area_favorites")
        .delete()
        .eq("user_id", userId)
        .eq("area_id", areaId);
      if (error) console.error(error);
    } else {
      const { error } = await supabase
        .from("area_favorites")
        .insert({ user_id: userId, area_id: areaId });
      if (error) console.error(error);
    }
  }
}

function subscribe(cb: () => void) {
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
}

export function useFavorites() {
  useEffect(() => {
    initFavorites();
  }, []);
  return useSyncExternalStore(
    subscribe,
    () => favorites,
    () => favorites,
  );
}
