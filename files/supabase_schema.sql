-- Run this once in Supabase: Project -> SQL Editor -> New query -> paste -> Run.
-- Creates the two tables the sync script writes to, and makes them
-- read-only to the public "anon" key (used by the dashboard) while the
-- secret "service_role" key (used only by the sync script) can still write.

create table if not exists garmin_wellness (
  date date primary key,
  resting_hr integer,
  hrv real,
  sleep_hours real,
  sleep_score integer,
  body_battery_low integer,
  body_battery_high integer,
  stress_avg integer,
  steps integer,
  training_readiness integer
);

create table if not exists garmin_activities (
  id bigint primary key,
  name text,
  type text,
  start text,
  duration_min real,
  distance_km real,
  avg_hr integer,
  calories integer
);

alter table garmin_wellness enable row level security;
alter table garmin_activities enable row level security;

-- The anon key (public, embedded in the dashboard page) may only read.
-- Only the secret service_role key -- used solely by the sync script -- can write;
-- it bypasses row level security entirely, so no insert/update policy is needed.
create policy "public read wellness" on garmin_wellness
  for select using (true);

create policy "public read activities" on garmin_activities
  for select using (true);
