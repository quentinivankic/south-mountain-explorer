CREATE TABLE public.trail_coverage (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL,
  area_id TEXT NOT NULL,
  trail_id TEXT NOT NULL,
  coverage NUMERIC(4,3) NOT NULL DEFAULT 0,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(user_id, area_id, trail_id)
);
CREATE INDEX idx_trail_coverage_user_area ON public.trail_coverage(user_id, area_id);
ALTER TABLE public.trail_coverage ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users view own coverage" ON public.trail_coverage
  FOR SELECT TO authenticated USING (auth.uid() = user_id);
CREATE POLICY "Users insert own coverage" ON public.trail_coverage
  FOR INSERT TO authenticated WITH CHECK (auth.uid() = user_id);
CREATE POLICY "Users update own coverage" ON public.trail_coverage
  FOR UPDATE TO authenticated USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);
CREATE POLICY "Users delete own coverage" ON public.trail_coverage
  FOR DELETE TO authenticated USING (auth.uid() = user_id);