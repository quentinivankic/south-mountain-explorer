// Reset all local + (optionally) server data for the current user.
import { supabase } from "@/integrations/supabase/client";

const LOCAL_KEYS = [
  "summit:location",
  "summit:active-recording",
  "summit:completed",
  "summit:favorites",
  "summit:coverage",
];

const IDB_DBS = ["summit-trails"];

export async function resetLocal(): Promise<void> {
  if (typeof window === "undefined") return;
  for (const k of LOCAL_KEYS) localStorage.removeItem(k);
  // Also clear anything else namespaced under summit:
  for (let i = localStorage.length - 1; i >= 0; i--) {
    const k = localStorage.key(i);
    if (k && k.startsWith("summit:")) localStorage.removeItem(k);
  }
  await Promise.all(
    IDB_DBS.map(
      (name) =>
        new Promise<void>((resolve) => {
          try {
            const req = indexedDB.deleteDatabase(name);
            req.onsuccess = () => resolve();
            req.onerror = () => resolve();
            req.onblocked = () => resolve();
          } catch {
            resolve();
          }
        }),
    ),
  );
}

export async function resetServerForUser(userId: string): Promise<void> {
  await Promise.all([
    supabase.from("trail_completions").delete().eq("user_id", userId),
    supabase.from("trail_coverage").delete().eq("user_id", userId),
    supabase.from("hike_recordings").delete().eq("user_id", userId),
    supabase.from("area_favorites").delete().eq("user_id", userId),
  ]);
}

export async function resetEverything(opts: {
  signOut?: boolean;
  wipeServer?: boolean;
}): Promise<void> {
  if (opts.wipeServer) {
    const { data } = await supabase.auth.getUser();
    if (data.user) await resetServerForUser(data.user.id);
  }
  if (opts.signOut) {
    await supabase.auth.signOut();
  }
  await resetLocal();
}
