-- Operator access model for Open-TGate (idempotent, self-contained).
--
-- This must run BEFORE any migration that references public.open_tgate_is_operator()
-- (the Telegram-accounts and heartbeat-grant migrations), so a database
-- provisioned purely from these checked-in migrations (`supabase db push`) can
-- bootstrap from empty. It creates the operator allowlist table, the identity
-- predicate, RLS, and grants. Operator *rows* (the seed) are managed as data
-- out-of-band (service role), not committed here, so no personal emails live in
-- the repository.

-- Allowlist of console operators.
create table if not exists public.open_tgate_operators (
  id bigint generated always as identity primary key,
  email text not null unique,
  role text not null default 'operator' check (role in ('owner', 'admin', 'member', 'operator')),
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.open_tgate_operators enable row level security;
revoke all on public.open_tgate_operators from anon, authenticated;
grant select on public.open_tgate_operators to authenticated;

-- A signed-in user may read only their own allowlist row (used by the console
-- to confirm operator status). Service role bypasses RLS to manage rows.
drop policy if exists open_tgate_operators_self_select on public.open_tgate_operators;
create policy open_tgate_operators_self_select on public.open_tgate_operators
  for select to authenticated
  using (lower(email) = lower(coalesce(auth.jwt() ->> 'email', '')));

-- Identity predicate: true when the caller's JWT email is an active operator.
-- SECURITY INVOKER (default) + fixed search_path; STABLE for planner reuse.
create or replace function public.open_tgate_is_operator()
returns boolean
language sql
stable
set search_path to 'public'
as $$
  select exists (
    select 1 from public.open_tgate_operators o
    where o.is_active
      and lower(o.email) = lower(coalesce(auth.jwt() ->> 'email', ''))
  );
$$;

grant execute on function public.open_tgate_is_operator() to authenticated;

comment on table public.open_tgate_operators is
  'Open-TGate console operator allowlist. RLS: a user reads only their own row; service role manages rows. Seed managed as data, not in migrations.';
