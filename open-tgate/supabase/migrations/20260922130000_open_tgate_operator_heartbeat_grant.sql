-- Fix: the operator console (/app) failed with
--   "permission denied for table open_tgate_worker_heartbeats"
-- because the table had the operator RLS SELECT *policy* but no table-level
-- GRANT to the `authenticated` role. PostgREST checks the table privilege
-- before RLS, so the read was rejected for every signed-in operator.
--
-- Grant SELECT to authenticated; the existing
-- `open_tgate_heartbeats_operator_select` policy still restricts the visible
-- rows to allowlisted, active operators (public.open_tgate_is_operator()).
grant select on public.open_tgate_worker_heartbeats to authenticated;
