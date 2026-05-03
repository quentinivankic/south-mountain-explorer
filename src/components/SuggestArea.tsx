import { useState } from "react";
import { Plus, Send, X } from "lucide-react";
import { supabase } from "@/integrations/supabase/client";

export function SuggestArea() {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [region, setRegion] = useState("");
  const [notes, setNotes] = useState("");
  const [email, setEmail] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reset = () => {
    setName("");
    setRegion("");
    setNotes("");
    setEmail("");
    setDone(false);
    setError(null);
  };

  const close = () => {
    setOpen(false);
    setTimeout(reset, 200);
  };

  const submit = async () => {
    const trimmed = name.trim();
    if (!trimmed || trimmed.length > 200) return;
    setSubmitting(true);
    setError(null);
    const { data: sess } = await supabase.auth.getSession();
    const { error } = await supabase.from("area_suggestions").insert({
      name: trimmed,
      region: region.trim() || null,
      notes: notes.trim() || null,
      email: email.trim() || null,
      user_id: sess.session?.user.id ?? null,
    });
    setSubmitting(false);
    if (error) {
      setError("Couldn't send. Try again?");
      return;
    }
    setDone(true);
  };

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="mt-2 w-full flex items-center justify-center gap-2 rounded-2xl border border-dashed border-border/70 bg-card/50 px-4 py-4 text-sm font-semibold text-muted-foreground hover:text-foreground hover:border-border transition"
      >
        <Plus className="size-4" />
        Suggest an area
      </button>

      {open && (
        <div
          className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/40 backdrop-blur-sm p-0 sm:p-4"
          onClick={close}
        >
          <div
            className="w-full sm:max-w-md bg-card rounded-t-3xl sm:rounded-3xl p-5 shadow-[var(--shadow-elev)] border border-border/60"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between mb-1">
              <h2 className="text-lg font-bold">Suggest an area</h2>
              <button onClick={close} aria-label="Close" className="text-muted-foreground p-1">
                <X className="size-5" />
              </button>
            </div>
            <p className="text-xs text-muted-foreground mb-4">
              Tell us where to add next. Park or preserve names work best.
            </p>

            {done ? (
              <div className="py-6 text-center">
                <div className="text-3xl">🗺️</div>
                <div className="mt-2 font-semibold">Got it — thanks!</div>
                <div className="text-xs text-muted-foreground mt-1">
                  We'll review and add it in an upcoming update.
                </div>
                <button
                  onClick={close}
                  className="mt-4 text-sm text-primary font-semibold"
                >
                  Close
                </button>
              </div>
            ) : (
              <>
                <input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Area name (e.g. Mt. Baldy)"
                  maxLength={200}
                  className="w-full rounded-xl border border-border/60 bg-background p-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary/40"
                />
                <input
                  value={region}
                  onChange={(e) => setRegion(e.target.value)}
                  placeholder="City / region (optional)"
                  maxLength={200}
                  className="mt-2 w-full rounded-xl border border-border/60 bg-background p-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary/40"
                />
                <textarea
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  placeholder="Anything we should know? (optional)"
                  rows={3}
                  maxLength={1000}
                  className="mt-2 w-full rounded-xl border border-border/60 bg-background p-3 text-sm resize-none focus:outline-none focus:ring-2 focus:ring-primary/40"
                />
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="Email (optional)"
                  className="mt-2 w-full rounded-xl border border-border/60 bg-background p-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary/40"
                />

                {error && <div className="mt-2 text-xs text-destructive">{error}</div>}

                <button
                  onClick={submit}
                  disabled={!name.trim() || submitting}
                  className="mt-4 w-full flex items-center justify-center gap-2 rounded-xl bg-primary text-primary-foreground font-semibold py-3 disabled:opacity-50 active:scale-[0.98] transition"
                >
                  <Send className="size-4" />
                  {submitting ? "Sending…" : "Submit suggestion"}
                </button>
              </>
            )}
          </div>
        </div>
      )}
    </>
  );
}
