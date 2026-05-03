import { createFileRoute, Link, useNavigate, useSearch } from "@tanstack/react-router";
import { useState } from "react";
import { ArrowLeft, Mountain } from "lucide-react";
import { supabase } from "@/integrations/supabase/client";
import { lovable } from "@/integrations/lovable/index";

export const Route = createFileRoute("/auth")({
  validateSearch: (s: Record<string, unknown>) => ({
    redirect: typeof s.redirect === "string" ? s.redirect : "/",
  }),
  head: () => ({
    meta: [
      { title: "Sign in — Summit" },
      { name: "description", content: "Sign in to save your trail progress across devices." },
    ],
  }),
  component: AuthPage,
});

function AuthPage() {
  const navigate = useNavigate();
  const { redirect } = useSearch({ from: "/auth" });
  const [mode, setMode] = useState<"signin" | "signup">("signup");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  const handleEmail = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    setInfo(null);
    try {
      if (mode === "signup") {
        const { error } = await supabase.auth.signUp({
          email,
          password,
          options: { emailRedirectTo: window.location.origin },
        });
        if (error) throw error;
        setInfo("Check your email to confirm your account, then sign in.");
        setMode("signin");
      } else {
        const { error } = await supabase.auth.signInWithPassword({ email, password });
        if (error) throw error;
        navigate({ to: redirect });
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setLoading(false);
    }
  };

  const handleGoogle = async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await lovable.auth.signInWithOAuth("google", {
        redirect_uri: window.location.origin + redirect,
      });
      if (result.error) throw result.error;
      if (result.redirected) return;
      navigate({ to: redirect });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Google sign-in failed");
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-background flex flex-col">
      <div className="px-4 py-3">
        <Link to="/" className="inline-flex items-center gap-1 text-sm font-medium text-foreground">
          <ArrowLeft className="size-5" />
          <span className="sr-only">Back</span>
        </Link>
      </div>

      <main className="flex-1 px-6 pb-12 max-w-md mx-auto w-full flex flex-col justify-center">
        <div className="flex items-center gap-2 text-primary">
          <Mountain className="size-5" />
          <span className="uppercase tracking-[0.2em] text-xs font-semibold">Summit</span>
        </div>
        <h1 className="mt-4 text-3xl font-black leading-tight">
          {mode === "signup" ? "Save your progress." : "Welcome back."}
        </h1>
        <p className="mt-2 text-muted-foreground text-sm">
          {mode === "signup"
            ? "Create a free account to bag unlimited trails and sync across devices."
            : "Sign in to continue tracking your trails."}
        </p>

        <button
          onClick={handleGoogle}
          disabled={loading}
          className="mt-6 w-full h-11 rounded-full border border-border bg-card font-semibold flex items-center justify-center gap-2 hover:bg-muted transition disabled:opacity-50"
        >
          <svg className="size-4" viewBox="0 0 24 24" aria-hidden>
            <path fill="#EA4335" d="M12 11v3.2h4.5c-.2 1.2-1.5 3.6-4.5 3.6-2.7 0-4.9-2.2-4.9-5s2.2-5 4.9-5c1.5 0 2.6.7 3.2 1.2l2.2-2.1C15.9 5.5 14.1 4.7 12 4.7 7.9 4.7 4.6 8 4.6 12s3.3 7.3 7.4 7.3c4.3 0 7.1-3 7.1-7.2 0-.5 0-.8-.1-1.1H12z"/>
          </svg>
          Continue with Google
        </button>

        <div className="my-5 flex items-center gap-3 text-xs text-muted-foreground">
          <div className="flex-1 h-px bg-border" /> or <div className="flex-1 h-px bg-border" />
        </div>

        <form onSubmit={handleEmail} className="space-y-3">
          <input
            type="email"
            required
            placeholder="you@example.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full h-11 px-4 rounded-xl border border-border bg-card text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/40"
          />
          <input
            type="password"
            required
            minLength={6}
            placeholder="Password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full h-11 px-4 rounded-xl border border-border bg-card text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/40"
          />
          {error && <p className="text-sm text-[oklch(0.5_0.18_25)]">{error}</p>}
          {info && <p className="text-sm text-[oklch(0.4_0.13_145)]">{info}</p>}
          <button
            type="submit"
            disabled={loading}
            className="w-full h-11 rounded-full font-bold text-primary-foreground disabled:opacity-50"
            style={{ background: "var(--gradient-sunrise)" }}
          >
            {loading ? "..." : mode === "signup" ? "Create account" : "Sign in"}
          </button>
        </form>

        <button
          onClick={() => {
            setMode(mode === "signup" ? "signin" : "signup");
            setError(null);
            setInfo(null);
          }}
          className="mt-4 text-sm text-muted-foreground hover:text-foreground"
        >
          {mode === "signup" ? "Already have an account? Sign in" : "New here? Create an account"}
        </button>
      </main>
    </div>
  );
}
