-- Open-TGate — Bot token accounts, Notion sync tracking, and label editing.
--
-- This migration extends the platform to support:
--   * Bot accounts (via Telegram Bot API token, NOT TDLib/MTProto)
--   * Notion integration state tracking per account
--   * Enhanced label management (labels are already UPDATE-able via RLS)
--
-- Bot accounts are a separate authentication track from personal accounts:
--   * Personal accounts use TDLib (MTProto) — phone/QR login, full sync
--   * Bot accounts use the Telegram Bot API (HTTPS) — token-based, limited scope
--
-- Idempotent: uses ADD COLUMN IF NOT EXISTS, IF NOT EXISTS for constraints.

-- ---------------------------------------------------------------------------
-- 1. Account type: personal vs bot
-- ---------------------------------------------------------------------------
alter table public.open_tgate_tg_accounts
  add column if not exists account_type text not null default 'personal'
    check (account_type in ('personal', 'bot'));

comment on column public.open_tgate_tg_accounts.account_type is
  'Account authentication track: personal (TDLib/MTProto, phone/QR) or bot (Bot API, token).';

-- ---------------------------------------------------------------------------
-- 2. Bot-specific columns
-- ---------------------------------------------------------------------------
-- We store a SHA-256 hash of the bot token, NEVER the token itself.
-- The plaintext token is submitted via login_commands and consumed immediately.
alter table public.open_tgate_tg_accounts
  add column if not exists bot_token_hint text;

comment on column public.open_tgate_tg_accounts.bot_token_hint is
  'Last 4 characters of the bot token for identification. Full token is NEVER stored.';

alter table public.open_tgate_tg_accounts
  add column if not exists bot_username text;

comment on column public.open_tgate_tg_accounts.bot_username is
  'Bot @username from getMe (Bot API). NULL for personal accounts.';

alter table public.open_tgate_tg_accounts
  add column if not exists bot_can_read_messages boolean not null default false;

comment on column public.open_tgate_tg_accounts.bot_can_read_messages is
  'Whether the bot has group privacy mode disabled (can read all messages). From getMe.';

-- ---------------------------------------------------------------------------
-- 3. Notion sync tracking
-- ---------------------------------------------------------------------------
alter table public.open_tgate_tg_accounts
  add column if not exists notion_synced_at timestamptz;

comment on column public.open_tgate_tg_accounts.notion_synced_at is
  'Last time entities for this account were pushed to Notion. NULL = never synced.';

alter table public.open_tgate_tg_accounts
  add column if not exists notion_database_id text;

comment on column public.open_tgate_tg_accounts.notion_database_id is
  'Notion database ID where this account''s entities are stored. Set by the sync job.';

alter table public.open_tgate_tg_accounts
  add column if not exists notion_sync_error text;

comment on column public.open_tgate_tg_accounts.notion_sync_error is
  'Last Notion sync error message, if any. Cleared on successful sync.';

-- ---------------------------------------------------------------------------
-- 4. Expand the status check constraint for bot-specific states
-- ---------------------------------------------------------------------------
-- Drop the old check and recreate with bot states included.
-- The column already has a check, so we replace it.
do $$
begin
  -- Drop existing check constraint on status (name varies by creation)
  execute (
    select 'alter table public.open_tgate_tg_accounts drop constraint ' || quote_ident(conname)
    from pg_constraint
    where conrelid = 'public.open_tgate_tg_accounts'::regclass
      and contype = 'c'
      and pg_get_constraintdef(oid) like '%status%'
    limit 1
  );
exception when others then
  null; -- No constraint found, fine
end;
$$;

alter table public.open_tgate_tg_accounts
  add constraint open_tgate_tg_accounts_status_check
  check (status in (
    'pending', 'initializing', 'awaiting_phone', 'awaiting_qr_scan',
    'awaiting_code', 'awaiting_password', 'authorized', 'logged_out', 'error',
    -- Bot-specific states:
    'validating_token', 'bot_authorized'
  ));

-- ---------------------------------------------------------------------------
-- 5. Expand login_commands action constraint for bot token flow
-- ---------------------------------------------------------------------------
do $$
begin
  execute (
    select 'alter table public.open_tgate_login_commands drop constraint ' || quote_ident(conname)
    from pg_constraint
    where conrelid = 'public.open_tgate_login_commands'::regclass
      and contype = 'c'
      and pg_get_constraintdef(oid) like '%action%'
    limit 1
  );
exception when others then
  null;
end;
$$;

alter table public.open_tgate_login_commands
  add constraint open_tgate_login_commands_action_check
  check (action in (
    'start_phone', 'start_qr', 'submit_code', 'submit_password', 'logout',
    -- Bot-specific actions:
    'start_bot_token', 'revoke_bot'
  ));

-- ---------------------------------------------------------------------------
-- 6. Entity-level Notion tracking
-- ---------------------------------------------------------------------------
alter table public.open_tgate_tg_entities
  add column if not exists notion_page_id text;

comment on column public.open_tgate_tg_entities.notion_page_id is
  'Notion page ID if this entity has been synced to Notion. Used for update-in-place.';

-- ---------------------------------------------------------------------------
-- 7. Index for Notion sync queries
-- ---------------------------------------------------------------------------
create index if not exists open_tgate_tg_accounts_notion_sync_idx
  on public.open_tgate_tg_accounts (notion_synced_at nulls first)
  where account_type = 'personal' or account_type = 'bot';

create index if not exists open_tgate_tg_entities_notion_idx
  on public.open_tgate_tg_entities (account_id)
  where notion_page_id is null;

-- ---------------------------------------------------------------------------
-- 8. Bot accounts index (for quick filtering)
-- ---------------------------------------------------------------------------
create index if not exists open_tgate_tg_accounts_type_idx
  on public.open_tgate_tg_accounts (account_type)
  where account_type = 'bot';
