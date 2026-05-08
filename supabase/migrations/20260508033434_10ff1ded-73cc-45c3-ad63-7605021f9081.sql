
create table public.areas (
  id text primary key,
  name text not null,
  state text not null,
  center_lat double precision not null,
  center_lon double precision not null,
  zoom integer not null default 13,
  osm_relation text,
  bbox jsonb,
  trails jsonb,
  trail_count integer,
  total_mi numeric,
  cached_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.areas enable row level security;

create policy "Areas are publicly readable"
  on public.areas for select
  to anon, authenticated
  using (true);

create index areas_state_name_idx on public.areas (state, name);
create index areas_cached_at_idx on public.areas (cached_at);
