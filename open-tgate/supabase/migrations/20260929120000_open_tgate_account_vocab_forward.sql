-- Open-TGate — reconcile account/command vocabulary forward to production.
--
-- Production (open-operations) was migrated out-of-band to a newer vocabulary
-- than the tracked migrations carried:
--   * open_tgate_tg_accounts.account_type: 'user' | 'bot'   (was 'personal' | 'bot')
--   * open_tgate_login_commands.action adds 'start_bot_token' and 'revoke_bot'
--     (the bot action was previously 'start_bot')
--   * open_tgate_tg_accounts.status adds 'validating_token' and 'bot_authorized'
--
-- This migration reconciles the repository forward to that live schema. It is
-- idempotent and forward-only: it drops/re-adds CHECK constraints and migrates
-- any legacy row values in place. It intentionally does NOT touch any columns
-- outside this vocabulary (no data is dropped).

-- 1) account_type: personal -> user, default 'user', CHECK ('user','bot').
update public.open_tgate_tg_accounts
  set account_type = 'user'
  where account_type = 'personal';

alter table public.open_tgate_tg_accounts
  alter column account_type set default 'user';

alter table public.open_tgate_tg_accounts
  drop constraint if exists open_tgate_tg_accounts_account_type_check;
alter table public.open_tgate_tg_accounts
  add constraint open_tgate_tg_accounts_account_type_check
  check (account_type in ('user', 'bot'));

comment on column public.open_tgate_tg_accounts.account_type is
  'Login identity type: user (phone/QR personal account) or bot (transient bot-token authorization).';

-- 2) account status: extend the allowed set with the bot-login lifecycle states.
alter table public.open_tgate_tg_accounts
  drop constraint if exists open_tgate_tg_accounts_status_check;
alter table public.open_tgate_tg_accounts
  add constraint open_tgate_tg_accounts_status_check
  check (status in (
    'pending', 'initializing', 'awaiting_phone', 'awaiting_qr_scan',
    'awaiting_code', 'awaiting_password', 'authorized', 'logged_out', 'error',
    'validating_token', 'bot_authorized'
  ));

-- 3) login command actions: rename legacy 'start_bot' rows and align the CHECK
--    with production (adds 'start_bot_token' and 'revoke_bot').
update public.open_tgate_login_commands
  set action = 'start_bot_token'
  where action = 'start_bot';

alter table public.open_tgate_login_commands
  drop constraint if exists open_tgate_login_commands_action_check;
alter table public.open_tgate_login_commands
  add constraint open_tgate_login_commands_action_check
  check (action in (
    'start_phone', 'start_qr', 'submit_code', 'submit_password', 'logout',
    'start_bot_token', 'revoke_bot'
  ));
