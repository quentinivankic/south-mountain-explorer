CREATE TABLE public.area_suggestions (
  id UUID NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id UUID,
  name TEXT NOT NULL,
  region TEXT,
  notes TEXT,
  email TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE public.area_suggestions ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Anyone can suggest an area"
ON public.area_suggestions
FOR INSERT
TO anon, authenticated
WITH CHECK (length(name) BETWEEN 1 AND 200);
-- Intentionally no SELECT policy — owner reviews in backend dashboard.