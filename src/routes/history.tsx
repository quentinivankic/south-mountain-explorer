import { createFileRoute, Link, redirect } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { ArrowLeft, Trash2, MapPin } from "lucide-react";
import { listRecordings, deleteRecording, type SavedRecording } from "@/lib/recorder";
import { areas } from "@/data/trails";
import { supabase } from "@/integrations/supabase/client";

export const Route = createFileRoute("/history")({
  head: () => ({
    meta: [{ title: "Hike History — Summit" }],
  }),
  beforeLoad: async () => {
    const { data } = await supabase.auth.getSession();
    if (!data.session) {
      throw redirect({ to: "/auth", search: { redirect: "/history" } });
    }
  },
  component: HistoryPage,
});

function fmtDur(s: number) {
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

function HistoryPage() {
  const [items, setItems] = useState<SavedRecording[] | null>(null);

  useEffect(() => {
    listRecordings().then(setItems);
  }, []);

  const handleDelete = async (id: string) => {
    if (!confirm("Delete this hike?")) return;
    await deleteRecording(id);
    setItems((prev) => prev?.filter((r) => r.id !== id) ?? null);
  };

  const totalMi = items?.reduce((s, r) => s + Number(r.distance_mi), 0) ?? 0;
  const totalCount = items?.length ?? 0;

  return (
    <div className="min-h-screen bg-background">
      <div className="sticky top-0 z-30 backdrop-blur-md bg-background/85 border-b border-border/60">
        <div className="flex items-center justify-between px-4 py-3">
          <Link to="/" className="flex items-center gap-1 text-sm font-medium">
            <ArrowLeft className="size-5" />
            <span className="sr-only">Back</span>
          </Link>
          <div className="text-base font-bold">Hike History</div>
          <div className="w-5" />
        </div>
      </div>

      <main className="px-5 pt-5 pb-24 space-y-3">
        {items === null ? (
          <div className="text-sm text-muted-foreground">Loading…</div>
        ) : items.length === 0 ? (
          <div className="rounded-3xl border border-dashed border-border p-8 text-center">
            <div className="text-3xl">🥾</div>
            <p className="mt-2 text-sm font-medium">No recorded hikes yet</p>
            <p className="text-xs text-muted-foreground mt-1">
              Open an area and tap "Record this hike" to start.
            </p>
          </div>
        ) : (
          <>
            <div
              className="rounded-3xl p-5 text-primary-foreground"
              style={{ background: "var(--gradient-sunrise)" }}
            >
              <div className="text-[10px] uppercase tracking-[0.2em] opacity-80">Lifetime</div>
              <div className="mt-1 grid grid-cols-2 gap-3">
                <div>
                  <div className="text-3xl font-black tabular-nums">{totalCount}</div>
                  <div className="text-xs opacity-90">hikes</div>
                </div>
                <div>
                  <div className="text-3xl font-black tabular-nums">{totalMi.toFixed(1)}</div>
                  <div className="text-xs opacity-90">miles</div>
                </div>
              </div>
            </div>

            {items.map((r) => {
              const area = areas.find((a) => a.id === r.area_id);
              const date = new Date(r.started_at);
              return (
                <div
                  key={r.id}
                  className="rounded-2xl bg-card border border-border/60 p-4"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="font-bold truncate">{area?.name ?? r.area_id}</div>
                      <div className="text-xs text-muted-foreground flex items-center gap-1 mt-0.5">
                        <MapPin className="size-3" />
                        {date.toLocaleDateString(undefined, {
                          month: "short",
                          day: "numeric",
                          year: "numeric",
                        })}
                        {" · "}
                        {date.toLocaleTimeString(undefined, {
                          hour: "numeric",
                          minute: "2-digit",
                        })}
                      </div>
                    </div>
                    <button
                      onClick={() => handleDelete(r.id)}
                      className="text-muted-foreground p-1"
                      aria-label="Delete"
                    >
                      <Trash2 className="size-4" />
                    </button>
                  </div>
                  <div className="mt-3 grid grid-cols-3 gap-2 text-center">
                    <div>
                      <div className="text-lg font-black tabular-nums">
                        {Number(r.distance_mi).toFixed(2)}
                      </div>
                      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
                        Miles
                      </div>
                    </div>
                    <div>
                      <div className="text-lg font-black tabular-nums">
                        {fmtDur(r.duration_s)}
                      </div>
                      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
                        Time
                      </div>
                    </div>
                    <div>
                      <div className="text-lg font-black tabular-nums">
                        {r.completed_trail_ids.length}
                      </div>
                      <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
                        Trails
                      </div>
                    </div>
                  </div>
                </div>
              );
            })}
          </>
        )}
      </main>
    </div>
  );
}
