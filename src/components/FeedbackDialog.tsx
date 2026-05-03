import { useState } from "react";
import { MessageSquare, Send, X } from "lucide-react";
import { supabase } from "@/integrations/supabase/client";

export function FeedbackDialog() {
  const [open, setOpen] = useState(false);
  const [message, setMessage] = useState("");
  const [email, setEmail] = useState("");
  const [rating, setRating] = useState<number | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reset = () => {
    setMessage("");
    setEmail("");
    setRating(null);
    setDone(false);
    setError(null);
  };

  const close = () => {
    setOpen(false);
    setTimeout(reset, 200);
  };

  const submit = async () => {
    if (!message.trim()) return;
    setSubmitting(true);
    setError(null);
    const { data: sess } = await supabase.auth.getSession();
    const { error } = await supabase.from("feedback").insert({
      message: message.trim(),
      email: email.trim() || null,
      rating,
      page: typeof window !== "undefined" ? window.location.pathname : null,
      user_agent: typeof navigator !== "undefined" ? navigator.userAgent : null,
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
        className="fixed bottom-5 right-5 z-40 flex items-center gap-2 rounded-full bg-primary text-primary-foreground px-4 py-3 shadow-[var(--shadow-elev)] text-sm font-semibold active:scale-95 transition"
        aria-label="Send feedback"
      >
        <MessageSquare className="size-4" />
        Feedback
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
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-lg font-bold">Send feedback</h2>
              <button onClick={close} aria-label="Close" className="text-muted-foreground p-1">
                <X className="size-5" />
              </button>
            </div>

            {done ? (
              <div className="py-6 text-center">
                <div className="text-3xl">🙏</div>
                <div className="mt-2 font-semibold">Thanks for the feedback!</div>
                <button
                  onClick={close}
                  className="mt-4 text-sm text-primary font-semibold"
                >
                  Close
                </button>
              </div>
            ) : (
              <>
                <div className="mb-3">
                  <div className="text-xs uppercase tracking-wider text-muted-foreground mb-2">
                    How's it going?
                  </div>
                  <div className="flex gap-2">
                    {[
                      { v: 1, e: "😞" },
                      { v: 2, e: "😐" },
                      { v: 3, e: "🙂" },
                      { v: 4, e: "😍" },
                    ].map((o) => (
                      <button
                        key={o.v}
                        onClick={() => setRating(o.v)}
                        className={`flex-1 py-2 text-2xl rounded-xl border transition ${
                          rating === o.v
                            ? "border-primary bg-primary/10"
                            : "border-border/60 bg-background"
                        }`}
                        aria-label={`Rating ${o.v}`}
                      >
                        {o.e}
                      </button>
                    ))}
                  </div>
                </div>

                <textarea
                  value={message}
                  onChange={(e) => setMessage(e.target.value)}
                  placeholder="What's on your mind? Bugs, ideas, anything…"
                  rows={4}
                  className="w-full rounded-xl border border-border/60 bg-background p-3 text-sm resize-none focus:outline-none focus:ring-2 focus:ring-primary/40"
                />

                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="Email (optional, if you want a reply)"
                  className="mt-2 w-full rounded-xl border border-border/60 bg-background p-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary/40"
                />

                {error && (
                  <div className="mt-2 text-xs text-destructive">{error}</div>
                )}

                <button
                  onClick={submit}
                  disabled={!message.trim() || submitting}
                  className="mt-4 w-full flex items-center justify-center gap-2 rounded-xl bg-primary text-primary-foreground font-semibold py-3 disabled:opacity-50 active:scale-[0.98] transition"
                >
                  <Send className="size-4" />
                  {submitting ? "Sending…" : "Send feedback"}
                </button>
              </>
            )}
          </div>
        </div>
      )}
    </>
  );
}
