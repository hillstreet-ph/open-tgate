-- Extend the existing mirror in place; preserve its current RLS, identities and
-- session storage. Canonical schema reconciliation remains a separate task.
alter table public.open_tgate_tg_accounts
  add column if not exists chat_folders jsonb not null default '[]'::jsonb,
  add column if not exists main_chat_list_position integer not null default 0;
alter table public.open_tgate_tg_chats
  add column if not exists folder_ids integer[] not null default '{}',
  add column if not exists folder_positions jsonb not null default '{}'::jsonb,
  add column if not exists peer_user_id text,
  add column if not exists telegram_sort_id numeric generated always as
    (case when chat_id ~ '^-?[0-9]+$' then chat_id::numeric else null end) stored;
create index if not exists open_tgate_chat_folder_membership
  on public.open_tgate_tg_chats using gin(folder_ids);

-- A sparse update must not erase memberships in other folders, including after
-- worker restart. Service-only, idempotent FIFO replay; no Telegram mutations.
create or replace function public.open_tgate_chat_folder_position(
  account uuid, chat text, list_key text, position_order bigint)
returns void language plpgsql security invoker set search_path to '' as $$
begin
  if list_key not in ('main', 'archive') and list_key !~ '^[1-9][0-9]{0,9}$' then
    raise exception 'invalid_chat_list';
  end if;
  if position_order < 0 then raise exception 'invalid_chat_order'; end if;
  insert into public.open_tgate_tg_chats(account_id, chat_id, is_visible, is_in_main)
  values (account, chat, false, false) on conflict(account_id, chat_id) do nothing;
  update public.open_tgate_tg_chats as c set
    folder_positions = case when position_order > 0 then
      jsonb_set(c.folder_positions, array[list_key], to_jsonb(lpad(position_order::text, 19, '0')), true)
      else c.folder_positions - list_key end,
    folder_ids = case when list_key in ('main', 'archive') then c.folder_ids
      when position_order > 0 then array(select distinct id from unnest(c.folder_ids || list_key::integer) as id order by id)
      else array_remove(c.folder_ids, list_key::integer) end
  where c.account_id = account and c.chat_id = chat;
end;
$$;
revoke all on function public.open_tgate_chat_folder_position(uuid, text, text, bigint) from public, anon, authenticated;
grant execute on function public.open_tgate_chat_folder_position(uuid, text, text, bigint) to service_role;

-- Private bot conversations use the same known Telegram peer identity as the
-- existing entity mirror, regardless of which snapshot arrives first.
create or replace function public.open_tgate_chat_peer_kind()
returns trigger language plpgsql security invoker set search_path to '' as $$
begin
  if new.peer_user_id is not null then
    new.kind = case when exists(select 1 from public.open_tgate_tg_entities as e
      where e.account_id = new.account_id and e.tg_id = new.peer_user_id
      and e.kind = 'bot') then 'bot' else 'user' end;
  end if;
  return new;
end;
$$;
revoke all on function public.open_tgate_chat_peer_kind() from public, anon, authenticated;
create or replace trigger open_tgate_chat_peer_kind before insert or update of peer_user_id,kind
  on public.open_tgate_tg_chats for each row execute function public.open_tgate_chat_peer_kind();
create or replace function public.open_tgate_refresh_bot_peer()
returns trigger language plpgsql security invoker set search_path to '' as $$
begin
  if new.kind = 'bot' then
    update public.open_tgate_tg_chats set kind = 'bot'
      where account_id = new.account_id and peer_user_id = new.tg_id and kind is distinct from 'bot';
  end if;
  return new;
end;
$$;
revoke all on function public.open_tgate_refresh_bot_peer() from public, anon, authenticated;
create or replace trigger open_tgate_refresh_bot_peer after insert or update
  on public.open_tgate_tg_entities for each row execute function public.open_tgate_refresh_bot_peer();
notify pgrst, 'reload schema';
