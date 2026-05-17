-- Wayline initial schema
-- Tables: users (with hidden persona ground truth) + events (behavioral stream)

create table if not exists users (
    user_id    uuid primary key default gen_random_uuid(),
    signup_ts  timestamptz not null,
    channel    text not null check (channel in ('organic','paid','referral')),
    plan_tier  text not null check (plan_tier in ('free','pro','business')),
    country    text,
    persona    text not null check (persona in ('power','activator','looker','bouncer'))
);

create table if not exists events (
    event_id    uuid primary key default gen_random_uuid(),
    user_id     uuid not null references users(user_id) on delete cascade,
    event_name  text not null,
    ts          timestamptz not null,
    session_id  uuid,
    properties  jsonb not null default '{}'::jsonb
);

create index if not exists idx_events_user_ts on events (user_id, ts);
create index if not exists idx_events_name_ts on events (event_name, ts);
create index if not exists idx_events_ts on events (ts);
