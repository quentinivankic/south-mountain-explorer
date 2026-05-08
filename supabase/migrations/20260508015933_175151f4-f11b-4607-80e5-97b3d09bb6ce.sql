CREATE TABLE public.hike_recordings (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL,
  area_id TEXT NOT NULL,
  started_at TIMESTAMPTZ NOT NULL,
  ended_at TIMESTAMPTZ NOT NULL,
  distance_mi NUMERIC(8,2) NOT NULL DEFAULT 0,
  duration_s INTEGER NOT NULL DEFAULT 0,
  path JSONB NOT NULL DEFAULT '[]'::jsonb,
  completed_trail_ids TEXT[] NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_hike_recordings_user ON public.hike_recordings(user_id, started_at DESC);

ALTER TABLE public.hike_recordings ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users view own recordings"
  ON public.hike_recordings FOR SELECT TO authenticated
  USING (auth.uid() = user_id);

CREATE POLICY "Users insert own recordings"
  ON public.hike_recordings FOR INSERT TO authenticated
  WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users delete own recordings"
  ON public.hike_recordings FOR DELETE TO authenticated
  USING (auth.uid() = user_id);