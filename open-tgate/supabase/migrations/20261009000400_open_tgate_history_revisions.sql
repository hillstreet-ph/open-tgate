-- Current snapshots beat stale history even when Telegram edit dates share a
-- second. Preserve provenance too, so repeated delayed pages remain harmless.
create or replace function public.open_tgate_preserve_message_updates()
returns trigger language plpgsql set search_path to '' as $$
begin
  if old.deleted then new.deleted = true; end if;
  if old.edited_at is not null and (
    new.edited_at is null or old.edited_at > new.edited_at or (
      old.edited_at = new.edited_at
      and coalesce(old.meta ->> '_mirror_source', 'current') = 'current'
      and new.meta ->> '_mirror_source' = 'history'
    )
  ) then
    new.text = old.text;
    new.content_type = old.content_type;
    new.edited_at = old.edited_at;
    new.meta = old.meta;
  end if;
  return new;
end;
$$;
revoke all on function public.open_tgate_preserve_message_updates() from public, anon, authenticated;

-- Recent offline-gap catch-up is independent of the oldest durable checkpoint.
-- Restarting a recent scan must never throw away unfinished older backfill.
alter table public.open_tgate_tg_chats
  add column if not exists recent_cursor bigint not null default 0,
  add column if not exists recent_complete boolean not null default false,
  add column if not exists recent_boundary bigint not null default 0,
  add column if not exists recent_head bigint not null default 0,
  add column if not exists latest_synced_message_id bigint not null default 0,
  add column if not exists recent_synced_at timestamptz,
  add column if not exists recent_note text;

create or replace function public.open_tgate_prepare_recent_history(account uuid)
returns void language sql security invoker set search_path to '' as $$
  update public.open_tgate_tg_chats as chat
  set recent_cursor = 0,
      recent_head = 0,
      recent_boundary = case when chat.recent_complete
        then chat.latest_synced_message_id else chat.recent_boundary end,
      recent_complete = false,
      recent_synced_at = null,
      recent_note = 'Catching up Telegram history after worker startup.'
  where chat.account_id = account;
$$;
revoke all on function public.open_tgate_prepare_recent_history(uuid) from public, anon, authenticated;
grant execute on function public.open_tgate_prepare_recent_history(uuid) to service_role;
notify pgrst, 'reload schema';
