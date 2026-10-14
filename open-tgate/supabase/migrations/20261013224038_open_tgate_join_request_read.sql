-- Consent-only Telegram join-request observation.
--
-- The worker stores the native subject identifier so a later independently
-- reviewed approval path can address one exact request.  Operator/API reads
-- receive only request_ref and bounded status timestamps; the private subject
-- binding remains service-role-only.

create table if not exists public.open_tgate_join_requests (
  request_ref uuid primary key default gen_random_uuid(),
  account_id uuid not null references public.open_tgate_tg_accounts(id) on delete cascade,
  chat_id text not null check (chat_id ~ '^-?[0-9]+$'),
  requester_tg_id text not null check (requester_tg_id ~ '^[1-9][0-9]*$'),
  requested_at timestamptz not null,
  observed_at timestamptz not null default now(),
  status text not null default 'pending'
    check (status in ('pending', 'approved', 'declined', 'expired')),
  unique (account_id, chat_id, requester_tg_id)
);

create index if not exists open_tgate_join_requests_pending_idx
  on public.open_tgate_join_requests (account_id, chat_id, requested_at, request_ref)
  where status = 'pending';

alter table public.open_tgate_join_requests enable row level security;
revoke all on public.open_tgate_join_requests from public, anon, authenticated;
grant all on public.open_tgate_join_requests to service_role;

comment on table public.open_tgate_join_requests is
  'Service-only native subject binding for consent-gated Telegram join requests. Reader APIs expose only opaque references.';

notify pgrst, 'reload schema';
