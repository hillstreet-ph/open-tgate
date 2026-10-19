-- A reusable operator slot must never silently adopt another Telegram identity.
-- New identity verification is performed by the worker before invoking this RPC.
create or replace function public.open_tgate_bind_identity(account uuid, identity bigint)
returns boolean language plpgsql security invoker set search_path to '' as $$
declare bound boolean;
begin
  if identity is null or identity <= 0 then return false; end if;
  update public.open_tgate_tg_accounts as slot set tg_user_id = identity::text
  where slot.id = account and (
    slot.tg_user_id = identity::text or (
      slot.tg_user_id is null
      and not exists (select 1 from public.open_tgate_tg_entities where account_id = account)
      and not exists (select 1 from public.open_tgate_tg_chats where account_id = account)
      and not exists (select 1 from public.open_tgate_tg_messages where account_id = account)
    )
  ) returning true into bound;
  return coalesce(bound, false);
end;
$$;
revoke all on function public.open_tgate_bind_identity(uuid, bigint) from public, anon, authenticated;
grant execute on function public.open_tgate_bind_identity(uuid, bigint) to service_role;

create or replace function public.open_tgate_preserve_identity()
returns trigger language plpgsql set search_path to '' as $$
begin
  if old.tg_user_id is not null and new.tg_user_id is distinct from old.tg_user_id then
    raise exception 'account_identity_changed_use_new_slot' using errcode = '23514';
  end if;
  return new;
end;
$$;
revoke all on function public.open_tgate_preserve_identity() from public, anon, authenticated;
create or replace trigger open_tgate_preserve_identity
  before update of tg_user_id on public.open_tgate_tg_accounts
  for each row execute function public.open_tgate_preserve_identity();

alter table public.open_tgate_tg_chats
  add column if not exists last_message_id bigint not null default 0;

-- A fetched edit can arrive after a newer message: only the still-latest ID wins.
create or replace function public.open_tgate_refresh_last_preview(account uuid, chat text, message bigint, preview text)
returns void language sql security invoker set search_path to '' as $$
  update public.open_tgate_tg_chats as summary set last_message = coalesce(preview, '')
  where summary.account_id = account and summary.chat_id = chat
    and summary.last_message_id = message
    and not exists (select 1 from public.open_tgate_tg_messages as mirrored
      where mirrored.account_id = account and mirrored.chat_id = chat
        and mirrored.message_id = message and mirrored.deleted);
$$;
revoke all on function public.open_tgate_refresh_last_preview(uuid, text, bigint, text) from public, anon, authenticated;
grant execute on function public.open_tgate_refresh_last_preview(uuid, text, bigint, text) to service_role;

-- Bots do not emit last-message updates. Clear inaccessible latest previews and
-- prevent delayed chat snapshots/edits from exposing a tombstoned message again.
create or replace function public.open_tgate_clear_deleted_preview()
returns trigger language plpgsql set search_path to '' as $$
begin
  if new.deleted then
    update public.open_tgate_tg_chats set last_message = ''
    where account_id = new.account_id and chat_id = new.chat_id and last_message_id = new.message_id;
  end if;
  return new;
end;
$$;
revoke all on function public.open_tgate_clear_deleted_preview() from public, anon, authenticated;
create or replace trigger open_tgate_clear_deleted_preview
  after insert or update of deleted on public.open_tgate_tg_messages
  for each row execute function public.open_tgate_clear_deleted_preview();

create or replace function public.open_tgate_preserve_deleted_preview()
returns trigger language plpgsql set search_path to '' as $$
begin
  if exists (select 1 from public.open_tgate_tg_messages as mirrored
    where mirrored.account_id = new.account_id and mirrored.chat_id = new.chat_id
      and mirrored.message_id = new.last_message_id and mirrored.deleted) then
    new.last_message = '';
  end if;
  return new;
end;
$$;
revoke all on function public.open_tgate_preserve_deleted_preview() from public, anon, authenticated;
create or replace trigger open_tgate_preserve_deleted_preview
  before insert or update of last_message, last_message_id on public.open_tgate_tg_chats
  for each row execute function public.open_tgate_preserve_deleted_preview();
notify pgrst, 'reload schema';
