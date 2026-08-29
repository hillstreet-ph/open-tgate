create table if not exists public.worker_heartbeats (
  worker_id text primary key,
  service text not null,
  status text not null check (status in ('starting','running','degraded','stopped')),
  last_seen_at timestamptz not null default now(),
  metadata jsonb not null default '{}'::jsonb
);

alter table public.worker_heartbeats enable row level security;
revoke all on public.worker_heartbeats from anon, authenticated;

create index if not exists worker_heartbeats_last_seen_idx
  on public.worker_heartbeats (last_seen_at desc);

comment on table public.worker_heartbeats is
  'Service-only runtime liveness. Written with the Supabase secret key.';

