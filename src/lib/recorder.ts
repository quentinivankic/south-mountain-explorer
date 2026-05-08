import { useEffect, useSyncExternalStore } from "react";
import { supabase } from "@/integrations/supabase/client";
import type { Trail } from "@/data/trails";
import { toggleTrail } from "@/lib/progress";
import { mergeCoverage, getCoverage } from "@/lib/coverage";

const KEY = "summit:active-recording";

export type GpsPoint = [number, number, number]; // [lat, lon, ts(ms)]
export type RecordingMode = "roam" | "trail";

export interface ActiveRecording {
  areaId: string;
  mode: RecordingMode;
  /** When mode === "trail", the trail being targeted. */
  trailId?: string;
  startedAt: number;
  path: GpsPoint[];
  distanceMi: number;
}

export interface FinishedRecording extends ActiveRecording {
  endedAt: number;
  durationS: number;
  /** Trails that crossed the completion threshold during/after this hike. */
  newlyCompletedTrailIds: string[];
  /** Per-trail coverage delta this session contributed (0..1). */
  coverageDelta: Record<string, number>;
}

const COMPLETE_AT = 0.9; // ≥90% covered = mark complete
const BUFFER_M = 30;

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
    if (active.path.length > 5) {
      if (d < 3) return; // jitter
      if (d > 200) return; // bad fix
    } else if (d > 200) return;
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

export function startRecording(
  areaId: string,
  mode: RecordingMode,
  trailId?: string,
) {
  active = {
    areaId,
    mode,
    trailId: mode === "trail" ? trailId : undefined,
    startedAt: Date.now(),
    path: [],
    distanceMi: 0,
  };
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

/**
 * For each trail, compute fraction of its sampled nodes that lie within
 * BUFFER_M of any recorded GPS point. In "trail" mode, only the targeted
 * trail is considered. In "roam" mode, all trails get measured.
 */
function measureCoverage(
  rec: ActiveRecording,
  trails: Trail[],
): Record<string, number> {
  if (rec.path.length < 3) return {};
  const CELL = 0.0003;
  const grid = new Map<string, [number, number][]>();
  const key = (la: number, lo: number) =>
    `${Math.round(la / CELL)}:${Math.round(lo / CELL)}`;
  for (const [la, lo] of rec.path) {
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

  const candidates =
    rec.mode === "trail" && rec.trailId
      ? trails.filter((t) => t.id === rec.trailId)
      : trails;

  const out: Record<string, number> = {};
  for (const t of candidates) {
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
    if (total > 0) {
      const frac = covered / total;
      if (frac > 0.02) out[t.id] = frac;
    }
  }
  return out;
}

export async function stopRecording(
  trails: Trail[],
): Promise<FinishedRecording | null> {
  if (!active) return null;
  stopWatch();
  const endedAt = Date.now();
  const sessionCoverage = measureCoverage(active, trails);

  const prior = getCoverage()[active.areaId] ?? {};
  // merged coverage = max(prior, session)
  const merged: Record<string, number> = {};
  const newlyCompleted: string[] = [];
  for (const [tid, v] of Object.entries(sessionCoverage)) {
    const m = Math.max(prior[tid] ?? 0, v);
    merged[tid] = m;
    if ((prior[tid] ?? 0) < COMPLETE_AT && m >= COMPLETE_AT) {
      newlyCompleted.push(tid);
    }
  }

  await mergeCoverage(active.areaId, merged);
  for (const tid of newlyCompleted) {
    await toggleTrail(active.areaId, tid).catch(() => {});
  }

  const finished: FinishedRecording = {
    ...active,
    endedAt,
    durationS: Math.round((endedAt - active.startedAt) / 1000),
    newlyCompletedTrailIds: newlyCompleted,
    coverageDelta: sessionCoverage,
  };

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
      completed_trail_ids: newlyCompleted,
    });
    if (error) console.error("save recording failed", error);
  }

  active = null;
  emit();
  return finished;
}

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
