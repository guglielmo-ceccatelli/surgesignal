-- SurgeSignal schema. Run once in the Supabase SQL editor.
-- Only the worker writes (service key, bypasses RLS). The public console reads through the
-- anon key, which RLS limits to the two views at the bottom (eng review 2A).

create table if not exists state (
  name        text primary key,          -- alerts | tracker | settings | telegram | problems
  value       jsonb not null,
  updated_at  timestamptz not null default now()
);

create table if not exists checkins (
  id           bigint generated always as identity primary key,
  station_id   text not null,
  status       text not null check (status in ('busy', 'normal', 'dead')),
  ts           timestamptz not null,
  driver_hash  text not null              -- salted hash; raw Telegram ids are never stored
);
create index if not exists checkins_ts on checkins (ts);

create table if not exists disruptions_current (
  key            text primary key,
  line           text not null,
  line_name      text not null,
  severity_class text not null,
  is_unplanned   boolean not null,
  station_ids    text[] not null,
  t0             timestamptz not null,
  resolved_at    timestamptz,
  line_wide      boolean not null,
  area_uncertain boolean not null
);

alter table state enable row level security;
alter table checkins enable row level security;
alter table disruptions_current enable row level security;
-- No anon policies on the tables: the anon key sees nothing directly.

create or replace view public_disruptions with (security_invoker = false) as
  select key, line_name, severity_class, is_unplanned, station_ids, t0, resolved_at, line_wide, area_uncertain
  from disruptions_current;

create or replace view public_checkins with (security_invoker = false) as
  select station_id, status, ts from checkins where ts > now() - interval '2 hours';

grant select on public_disruptions to anon;
grant select on public_checkins to anon;
