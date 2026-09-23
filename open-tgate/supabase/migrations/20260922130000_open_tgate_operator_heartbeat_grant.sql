-- Fix: the operator console (/app) failed with
--   "permission denied for table open_tgate_worker_heartbeats"
-- because the table had the operator RLS SELECT *policy* but no table-level
-- GRANT to the `authenticated` role. PostgREST checks the table privilege
-- before RLS, so the read was rejected for every signed-in operator.
--
-- Grant SELECT to authenticated; the RLS policy still restricts the visible
-- rows to allowlisted, active operators (public.open_tgate_is_operator()).
grant select on public.open_tgate_worker_heartbeats to authenticated;

-- Ensure the operator SELECT policy exists as well, so a database provisioned
-- from these migrations is self-contained rather than depending on live-only
-- state. The operator-identity function public.open_tgate_is_operator() and the
-- allowlist table are provisioned by the operator-access migration; if that has
-- not run yet (fresh bootstrap), skip creating the policy — the GRANT above is
-- still valid and the policy is (re)created once the function exists.
do $$
begin
  if exists (
    select 1 from pg_proc p
    join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'public' and p.proname = 'open_tgate_is_operator'
  ) then
    drop policy if exists open_tgate_heartbeats_operator_select
      on public.open_tgate_worker_heartbeats;
    create policy open_tgate_heartbeats_operator_select
      on public.open_tgate_worker_heartbeats
      for select to authenticated
      using (public.open_tgate_is_operator());
  end if;
end $$;
