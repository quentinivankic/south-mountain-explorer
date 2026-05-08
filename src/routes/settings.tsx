import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { ChevronLeft, RotateCcw, AlertTriangle } from "lucide-react";
import { useAuthState } from "@/lib/progress";
import { resetEverything } from "@/lib/reset";

export const Route = createFileRoute("/settings")({
  head: () => ({
    meta: [{ title: "Settings — Summit" }],
  }),
  component: SettingsPage,
});

function SettingsPage() {
  const userId = useAuthState();
  const navigate = useNavigate();
  const [confirming, setConfirming] = useState(false);
  const [wipeServer, setWipeServer] = useState(true);
  const [signOut, setSignOut] = useState(true);
  const [busy, setBusy] = useState(false);

  async function doReset() {
    setBusy(true);
    try {
      await resetEverything({
        wipeServer: wipeServer && !!userId,
        signOut: signOut && !!userId,
      });
      // Hard reload to re-trigger first-launch flows.
      window.location.assign("/");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="min-h-screen bg-background">
      <header className="px-5 pt-12 pb-4 flex items-center gap-3">
        <Link
          to="/"
          aria-label="Back"
          className="rounded-full p-2 -ml-2 active:bg-muted"
        >
          <ChevronLeft className="size-5" />
        </Link>
        <h1 className="text-2xl font-bold">Settings</h1>
      </header>

      <main className="px-5 pb-24 space-y-6">
        <section className="rounded-2xl border border-border/50 bg-card p-5">
          <div className="flex items-start gap-3">
            <RotateCcw className="size-5 text-primary mt-0.5" />
            <div className="flex-1">
              <h2 className="text-lg font-bold">Reset app data</h2>
              <p className="mt-1 text-sm text-muted-foreground">
                Clears your local cache and (optionally) your saved progress
                so you can experience the app as a brand-new user.
              </p>
            </div>
          </div>

          <div className="mt-4 space-y-2">
            {userId && (
              <>
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={wipeServer}
                    onChange={(e) => setWipeServer(e.target.checked)}
                  />
                  Delete saved favorites, completions, coverage & recordings
                </label>
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={signOut}
                    onChange={(e) => setSignOut(e.target.checked)}
                  />
                  Sign out after reset
                </label>
              </>
            )}
          </div>

          {!confirming ? (
            <button
              onClick={() => setConfirming(true)}
              className="mt-4 w-full rounded-xl bg-destructive text-destructive-foreground font-semibold py-3 active:scale-[0.99]"
            >
              Reset app data
            </button>
          ) : (
            <div className="mt-4 rounded-xl border border-destructive/30 bg-destructive/5 p-3">
              <div className="flex items-start gap-2 text-sm">
                <AlertTriangle className="size-4 text-destructive mt-0.5" />
                <p>
                  This can't be undone
                  {wipeServer && userId ? " — your saved data will be deleted." : "."}
                </p>
              </div>
              <div className="mt-3 flex gap-2">
                <button
                  disabled={busy}
                  onClick={() => setConfirming(false)}
                  className="flex-1 rounded-lg border border-border py-2 text-sm font-medium"
                >
                  Cancel
                </button>
                <button
                  disabled={busy}
                  onClick={doReset}
                  className="flex-1 rounded-lg bg-destructive text-destructive-foreground py-2 text-sm font-semibold disabled:opacity-60"
                >
                  {busy ? "Resetting…" : "Yes, reset"}
                </button>
              </div>
            </div>
          )}
        </section>
      </main>
    </div>
  );
}
