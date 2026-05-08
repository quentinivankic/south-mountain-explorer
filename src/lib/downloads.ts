// Tiny in-memory download manager with pub/sub for the toast UI.
// One entry per active download (trail data or map tiles).

import { useSyncExternalStore } from "react";

export type DownloadKind = "trails" | "tiles";
export type DownloadStatus = "running" | "done" | "error";

export interface DownloadEntry {
  id: string;
  kind: DownloadKind;
  label: string;
  total: number; // 0 = indeterminate
  done: number;
  status: DownloadStatus;
  message?: string;
}

let entries: DownloadEntry[] = [];
const listeners = new Set<() => void>();

function emit() {
  entries = [...entries];
  listeners.forEach((l) => l());
}

function upsert(id: string, patch: Partial<DownloadEntry>) {
  const i = entries.findIndex((e) => e.id === id);
  if (i === -1) return;
  entries[i] = { ...entries[i], ...patch };
  emit();
}

export function startDownload(opts: {
  id: string;
  kind: DownloadKind;
  label: string;
  total?: number;
}) {
  // dedupe
  if (entries.some((e) => e.id === opts.id && e.status === "running")) return;
  entries = [
    ...entries.filter((e) => e.id !== opts.id),
    {
      id: opts.id,
      kind: opts.kind,
      label: opts.label,
      total: opts.total ?? 0,
      done: 0,
      status: "running",
    },
  ];
  emit();
}

export function updateDownload(id: string, done: number, total?: number) {
  upsert(id, { done, ...(total != null ? { total } : {}) });
}

export function finishDownload(id: string, message?: string) {
  upsert(id, { status: "done", message });
  scheduleRemove(id);
}

export function failDownload(id: string, message?: string) {
  upsert(id, { status: "error", message });
  scheduleRemove(id, 6000);
}

function scheduleRemove(id: string, ms = 2500) {
  setTimeout(() => {
    entries = entries.filter((e) => e.id !== id);
    emit();
  }, ms);
}

function subscribe(cb: () => void) {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

export function useDownloads(): DownloadEntry[] {
  return useSyncExternalStore(
    subscribe,
    () => entries,
    () => entries,
  );
}
