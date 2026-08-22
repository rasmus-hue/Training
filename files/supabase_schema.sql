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

-- Training plan: the macro (weekly) targets and the day-by-day sessions.
-- Populated/refreshed by files/generate_plan.py, read by the dashboard.

create table if not exists plan_weekly (
  week_start date primary key,
  week_number integer,
  phase text,
  cutback boolean,
  run_sessions integer,
  run_km_target real,
  long_run_km real,
  key_session text,
  easy_pace text,
  bike_sessions integer,
  bike_minutes_target integer,
  strength_push integer,
  strength_pull integer,
  strength_legs integer,
  steps_goal integer,
  notes text
);

create table if not exists plan_daily (
  date date not null,
  discipline text not null,
  title text,
  prescription text,
  target_distance_km real,
  target_duration_min real,
  completed boolean default false,
  notes text,
  primary key (date, discipline)
);

alter table plan_weekly enable row level security;
alter table plan_daily enable row level security;

create policy "public read plan_weekly" on plan_weekly
  for select using (true);

create policy "public read plan_daily" on plan_daily
  for select using (true);

-- Strength log: what you actually lifted, entered from the dashboard itself.
-- Unlike the tables above, the dashboard needs to WRITE here (not just read),
-- so the anon key gets insert/update rights on this one table only -- anyone
-- with the page link and passphrase can log a lift, same trust level as the
-- rest of the site. Read access stays public like everything else.

create table if not exists strength_log (
  date date not null,
  exercise text not null,
  discipline text,
  value real,
  unit text,
  updated_at timestamptz default now(),
  primary key (date, exercise)
);

alter table strength_log enable row level security;

create policy "public read strength_log" on strength_log
  for select using (true);

create policy "public insert strength_log" on strength_log
  for insert with check (true);

create policy "public update strength_log" on strength_log
  for update using (true) with check (true);
