-- Additive inbox mirror. Sessions remain on the existing worker volume.
alter table public.open_tgate_tg_accounts add column if not exists connection_state text;
alter table public.open_tgate_tg_accounts add column if not exists last_activity_at timestamptz;

create table if not exists public.open_tgate_tg_chats (
  account_id uuid not null references public.open_tgate_tg_accounts(id) on delete cascade,
  chat_id text not null,
  title text,
  kind text,
  unread_count integer not null default 0,
  last_message text,
  last_message_at timestamptz,
  is_archived boolean not null default false,
  synced_at timestamptz not null default now(),
  history_cursor bigint not null default 0,
  history_complete boolean not null default false,
  history_synced_at timestamptz,
  history_note text,
  last_read_inbox_message_id bigint not null default 0,
  last_read_outbox_message_id bigint not null default 0,
  is_marked_unread boolean not null default false,
  primary key (account_id, chat_id)
);
create index if not exists open_tgate_chats_latest_idx on public.open_tgate_tg_chats (account_id, last_message_at desc);

create table if not exists public.open_tgate_tg_messages (
  account_id uuid not null references public.open_tgate_tg_accounts(id) on delete cascade,
  chat_id text not null,
  message_id bigint not null,
  text text,
  content_type text,
  sender_id text,
  is_outgoing boolean not null default false,
  sent_at timestamptz,
  edited_at timestamptz,
  deleted boolean not null default false,
  meta jsonb not null default '{}'::jsonb,
  primary key (account_id, chat_id, message_id)
);
create index if not exists open_tgate_messages_history_idx on public.open_tgate_tg_messages (account_id, chat_id, message_id desc) where not deleted;

-- History backfill must not resurrect deletes or overwrite a more recent edit.
create or replace function public.open_tgate_preserve_message_updates()
returns trigger language plpgsql set search_path to '' as $$
begin
  if old.deleted then new.deleted = true; end if;
  if old.edited_at is not null and (new.edited_at is null or old.edited_at > new.edited_at) then
    new.text = old.text;
    new.content_type = old.content_type;
    new.edited_at = old.edited_at;
  end if;
  return new;
end;
$$;
revoke all on function public.open_tgate_preserve_message_updates() from public, anon, authenticated;
drop trigger if exists open_tgate_preserve_message_updates on public.open_tgate_tg_messages;
create trigger open_tgate_preserve_message_updates before update on public.open_tgate_tg_messages
for each row execute function public.open_tgate_preserve_message_updates();

alter table public.open_tgate_tg_chats enable row level security;
alter table public.open_tgate_tg_messages enable row level security;
revoke all on public.open_tgate_tg_chats, public.open_tgate_tg_messages from anon, authenticated;
grant select on public.open_tgate_tg_chats, public.open_tgate_tg_messages to authenticated;
grant all on public.open_tgate_tg_chats, public.open_tgate_tg_messages to service_role;
drop policy if exists open_tgate_chats_operator_read on public.open_tgate_tg_chats;
create policy open_tgate_chats_operator_read on public.open_tgate_tg_chats for select to authenticated
using (public.open_tgate_is_operator());
drop policy if exists open_tgate_messages_operator_read on public.open_tgate_tg_messages;
create policy open_tgate_messages_operator_read on public.open_tgate_tg_messages for select to authenticated
using (public.open_tgate_is_operator() and not deleted);

create table if not exists public.open_tgate_knowledge_sources (
  id uuid primary key default gen_random_uuid(),
  title text not null check (char_length(title) between 1 and 160),
  content text not null check (char_length(content) between 1 and 100000),
  source_type text not null default 'text' check (source_type in ('text', 'file')),
  approved boolean not null default false,
  enabled boolean not null default true,
  created_by uuid references auth.users(id) on delete set null,
  created_at timestamptz not null default now()
);
alter table public.open_tgate_knowledge_sources enable row level security;
revoke all on public.open_tgate_knowledge_sources from anon, authenticated;
grant select on public.open_tgate_knowledge_sources to authenticated;
grant all on public.open_tgate_knowledge_sources to service_role;
drop policy if exists open_tgate_knowledge_operator_read on public.open_tgate_knowledge_sources;
create policy open_tgate_knowledge_operator_read on public.open_tgate_knowledge_sources
for select to authenticated using (public.open_tgate_is_operator());

-- Key hash never leaves the API service. Operators see only their key metadata
-- through the authenticated API, which verifies current operator status.
create table if not exists public.open_tgate_api_keys (
  id uuid primary key default gen_random_uuid(),
  created_by uuid not null references auth.users(id) on delete cascade,
  owner_email text not null,
  name text not null check (char_length(name) between 1 and 80),
  prefix text not null,
  token_hash text not null unique check (char_length(token_hash) = 64),
  scopes text[] not null default array['read', 'knowledge:read']::text[]
    check (scopes <@ array['read', 'knowledge:read']::text[] and cardinality(scopes) > 0),
  created_at timestamptz not null default now(),
  revoked_at timestamptz
);
alter table public.open_tgate_api_keys enable row level security;
revoke all on public.open_tgate_api_keys from anon, authenticated;
grant all on public.open_tgate_api_keys to service_role;

comment on table public.open_tgate_tg_messages is 'Read-only operator workspace mirror; tombstones prevent stale history from resurrecting deleted messages.';
comment on table public.open_tgate_knowledge_sources is 'Shared existing operator workspace. Only enabled approved sources are retrieved by AI or MCP.';
comment on table public.open_tgate_api_keys is 'Service-only SHA-256 integration key hashes; owner activation checked on every request; no send/write scopes.';
notify pgrst, 'reload schema';
