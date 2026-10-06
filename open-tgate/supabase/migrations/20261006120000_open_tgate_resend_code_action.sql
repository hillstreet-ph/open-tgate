-- Open-TGate — add the 'resend_code' login command action.
--
-- The operator console can re-request a login code from Telegram while an
-- account sits in 'awaiting_code'. The worker handles it by calling TDLib's
-- resendAuthenticationCode. Idempotent and forward-only: it only widens the
-- action CHECK constraint, no rows are rewritten.

alter table public.open_tgate_login_commands
  drop constraint if exists open_tgate_login_commands_action_check;
alter table public.open_tgate_login_commands
  add constraint open_tgate_login_commands_action_check
  check (action in (
    'start_phone', 'start_qr', 'submit_code', 'submit_password', 'logout',
    'start_bot_token', 'revoke_bot', 'resend_code'
  ));

comment on column public.open_tgate_login_commands.action is
  'Login lifecycle command: phone/QR start, code/password submission (incl. resend_code), logout, bot token start/revoke.';
