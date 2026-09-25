-- Open-TGate — bot-token login and account-label hardening.
--
-- Bot tokens are accepted only through the transient command queue. The worker
-- clears command payloads immediately after consumption; no token is copied to
-- the account row, logs, or TDLib session metadata.

alter table public.open_tgate_tg_accounts
  add column if not exists account_type text not null default 'personal';

alter table public.open_tgate_tg_accounts
  drop constraint if exists open_tgate_tg_accounts_account_type_check;
alter table public.open_tgate_tg_accounts
  add constraint open_tgate_tg_accounts_account_type_check
  check (account_type in ('personal', 'bot'));

alter table public.open_tgate_tg_accounts
  drop constraint if exists open_tgate_tg_accounts_label_check;
alter table public.open_tgate_tg_accounts
  add constraint open_tgate_tg_accounts_label_check
  check (char_length(btrim(label)) between 1 and 80);

alter table public.open_tgate_login_commands
  drop constraint if exists open_tgate_login_commands_action_check;
alter table public.open_tgate_login_commands
  add constraint open_tgate_login_commands_action_check
  check (action in (
    'start_phone', 'start_qr', 'start_bot',
    'submit_code', 'submit_password', 'logout'
  ));

comment on column public.open_tgate_tg_accounts.account_type is
  'Login identity type: personal (phone/QR) or bot (transient bot-token authorization).';
