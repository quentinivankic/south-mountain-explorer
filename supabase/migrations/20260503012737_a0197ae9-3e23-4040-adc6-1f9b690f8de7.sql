CREATE TABLE public.trail_completions (
  id UUID NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  area_id TEXT NOT NULL,
  trail_id TEXT NOT NULL,
  completed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id, area_id, trail_id)
);

CREATE INDEX trail_completions_user_idx ON public.trail_completions(user_id);

ALTER TABLE public.trail_completions ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users view own completions"
  ON public.trail_completions FOR SELECT
  TO authenticated USING (auth.uid() = user_id);

CREATE POLICY "Users insert own completions"
  ON public.trail_completions FOR INSERT
  TO authenticated WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users delete own completions"
  ON public.trail_completions FOR DELETE
  TO authenticated USING (auth.uid() = user_id);