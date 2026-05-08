import { useEffect, useSyncExternalStore } from "react";
import { supabase } from "@/integrations/supabase/client";
import type { Trail } from "@/data/trails";
import { toggleTrail } from "@/lib/progress";

const KEY = "summit:active-recording";

export type GpsPoint = [number, number, number]; // [lat, lon, ts(ms)]

export interface ActiveRecording {
  areaId: string;
  startedAt: number; // ms
  path: GpsPoint[];
  distanceMi: number;
}

export interface FinishedRecording extends ActiveRecording {
  endedAt: number;
  durationS: number;
  walkedTrailIds: string[];
}

let active: ActiveRecording | null = null;
let watchId: number | null = null;
let lastError: string | null = null;
const listeners = new Set<() => void>();

function emit() {
  if (typeof window !== "undefined") {
    if (active) localStorage.setItem(KEY, JSON.stringify(active));
    else localStorage.removeItem(KEY);
  }
  listeners.forEach((l) => l());
}

function readLocal(): ActiveRecording | null {
  if (typeof window === "undefined") return null;
  try {
    return JSON.parse(localStorage.getItem(KEY) || "null");
  } catch {
    return null;
  }
}

// Haversine in meters
function distM(a: [number, number], b: [number, number]) {
  const R = 6371000;
  const dLa = ((b[0] - a[0]) * Math.PI) / 180;
  const dLo = ((b[1] - a[1]) * Math.PI) / 180;
  const la1 = (a[0] * Math.PI) / 180;
  const la2 = (b[0] * Math.PI) / 180;
  const x =
    Math.sin(dLa / 2) ** 2 +
    Math.cos(la1) * Math.cos(la2) * Math.sin(dLo / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(x), Math.sqrt(1 - x));
}

function appendPoint(lat: number, lon: number) {
  if (!active) return;
  const ts = Date.now();
  const last = active.path[active.path.length - 1];
  if (last) {
    const d = distM([last[0], last[1]], [lat, lon]);
    // Reject GPS jitter / no movement <3m, and absurd jumps >200m between samples
    if (d < 3 || d > 200) {
      // Always allow first ~5 points to anchor
      if (active.path.length > 5) return;
      if (d > 200) return;
    }
    active.distanceMi += d / 1609.344;
  }
  active.path.push([Number(lat.toFixed(6)), Number(lon.toFixed(6)), ts]);
  emit();
}

function startWatch() {
  if (typeof navigator === "undefined" || !navigator.geolocation) {
    lastError = "Geolocation not supported on this device.";
    emit();
    return;
  }
  if (watchId !== null) return;
  watchId = navigator.geolocation.watchPosition(
    (pos) => {
      lastError = null;
      appendPoint(pos.coords.latitude, pos.coords.longitude);
    },
    (err) => {
      lastError =
        err.code === err.PERMISSION_DENIED
          ? "Location permission denied. Enable it in your browser to record."
          : "Couldn't get your location. Make sure GPS is on.";
      emit();
    },
    { enableHighAccuracy: true, maximumAge: 2000, timeout: 15000 },
  );
}

function stopWatch() {
  if (watchId !== null && typeof navigator !== "undefined") {
    navigator.geolocation.clearWatch(watchId);
  }
  watchId = null;
}

export function startRecording(areaId: string) {
  active = { areaId, startedAt: Date.now(), path: [], distanceMi: 0 };
  lastError = null;
  startWatch();
  emit();
}

export function discardRecording() {
  stopWatch();
  active = null;
  lastError = null;
  emit();
}

// Coverage heuristic: a trail is "walked" if ≥55% of its sampled nodes
// fall within ~30m of some recorded GPS point.
const BUFFER_M = 30;
const COVERAGE = 0.55;

function findWalkedTrails(path: GpsPoint[], trails: Trail[]): string[] {
  if (path.length < 5) return [];
  // Bucket recorded points into a ~30m grid for cheap nearest lookup.
  const CELL = 0.0003; // ~33m
  const grid = new Map<string, [number, number][]>();
  const key = (la: number, lo: number) =>
    `${Math.round(la / CELL)}:${Math.round(lo / CELL)}`;
  for (const [la, lo] of path) {
    const k = key(la, lo);
    const list = grid.get(k);
    if (list) list.push([la, lo]);
    else grid.set(k, [[la, lo]]);
  }
  const neighborsOf = (la: number, lo: number) => {
    const r = Math.round(la / CELL);
    const c = Math.round(lo / CELL);
    const out: [number, number][] = [];
    for (let dr = -1; dr <= 1; dr++)
      for (let dc = -1; dc <= 1; dc++) {
        const list = grid.get(`${r + dr}:${c + dc}`);
        if (list) out.push(...list);
      }
    return out;
  };
  const walked: string[] = [];
  for (const t of trails) {
    let total = 0;
    let covered = 0;
    for (const seg of t.segments) {
      for (const [la, lo] of seg) {
        total++;
        const cands = neighborsOf(la, lo);
        for (const p of cands) {
          if (distM([la, lo], p) <= BUFFER_M) {
            covered++;
            break;
          }
        }
      }
    }
    if (total > 0 && covered / total >= COVERAGE) walked.push(t.id);
  }
  return walked;
}

export async function stopRecording(
  trails: Trail[],
): Promise<FinishedRecording | null> {
  if (!active) return null;
  stopWatch();
  const endedAt = Date.now();
  const walked = findWalkedTrails(active.path, trails);
  const finished: FinishedRecording = {
    ...active,
    endedAt,
    durationS: Math.round((endedAt - active.startedAt) / 1000),
    walkedTrailIds: walked,
  };

  // Auto-mark trails complete
  for (const tid of walked) {
    await toggleTrail(finished.areaId, tid).catch(() => {});
  }

  // Save to server if signed in
  const { data } = await supabase.auth.getSession();
  const uid = data.session?.user.id;
  if (uid) {
    const { error } = await supabase.from("hike_recordings").insert({
      user_id: uid,
      area_id: finished.areaId,
      started_at: new Date(finished.startedAt).toISOString(),
      ended_at: new Date(finished.endedAt).toISOString(),
      distance_mi: Number(finished.distanceMi.toFixed(2)),
      duration_s: finished.durationS,
      path: finished.path,
      completed_trail_ids: walked,
    });
    if (error) console.error("save recording failed", error);
  }

  active = null;
  emit();
  return finished;
}

// Re-attach watch if a recording was in progress (page reload)
if (typeof window !== "undefined") {
  active = readLocal();
  if (active) startWatch();
}

function subscribe(cb: () => void) {
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
}

export function useRecorder() {
  useEffect(() => {
    // ensure watch is running if there's an active recording
    if (active && watchId === null) startWatch();
  }, []);
  const snap = useSyncExternalStore(
    subscribe,
    () => active,
    () => null,
  );
  return { active: snap, error: lastError };
}

export interface SavedRecording {
  id: string;
  area_id: string;
  started_at: string;
  ended_at: string;
  distance_mi: number;
  duration_s: number;
  completed_trail_ids: string[];
  path: GpsPoint[];
}

export async function listRecordings(): Promise<SavedRecording[]> {
  const { data: sess } = await supabase.auth.getSession();
  if (!sess.session?.user) return [];
  const { data, error } = await supabase
    .from("hike_recordings")
    .select(
      "id, area_id, started_at, ended_at, distance_mi, duration_s, completed_trail_ids, path",
    )
    .order("started_at", { ascending: false });
  if (error) {
    console.error(error);
    return [];
  }
  return (data ?? []) as unknown as SavedRecording[];
}

export async function deleteRecording(id: string) {
  const { error } = await supabase.from("hike_recordings").delete().eq("id", id);
  if (error) console.error(error);
}
