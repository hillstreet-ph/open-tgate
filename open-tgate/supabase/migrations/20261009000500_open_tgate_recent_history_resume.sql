-- Finish the durable recent scan before starting a newer restart gap. The flag
-- coalesces repeated restarts without discarding the middle of an offline gap.
alter table public.open_tgate_tg_chats
  add column if not exists recent_restart_pending boolean not null default false;

create or replace function public.open_tgate_prepare_recent_history(account uuid)
returns void language sql security invoker set search_path to '' as $$
  update public.open_tgate_tg_chats as chat
  set recent_cursor = case when chat.recent_complete then 0 else chat.recent_cursor end,
      recent_head = case when chat.recent_complete then 0 else chat.recent_head end,
      recent_boundary = case when chat.recent_complete
        then chat.latest_synced_message_id else chat.recent_boundary end,
      recent_restart_pending = not chat.recent_complete,
      recent_synced_at = case when chat.recent_complete then null else chat.recent_synced_at end,
      recent_note = 'Catching up Telegram history after worker startup.',
      recent_complete = false
  where chat.account_id = account;
$$;
revoke all on function public.open_tgate_prepare_recent_history(uuid) from public, anon, authenticated;
grant execute on function public.open_tgate_prepare_recent_history(uuid) to service_role;

create or replace function public.open_tgate_complete_recent_history(account uuid, chat text, head bigint)
returns void language sql security invoker set search_path to '' as $$
  update public.open_tgate_tg_chats as summary
  set latest_synced_message_id = greatest(summary.latest_synced_message_id, head),
      recent_boundary = case when summary.recent_restart_pending
        then greatest(summary.latest_synced_message_id, head) else summary.recent_boundary end,
      recent_cursor = case when summary.recent_restart_pending then 0 else summary.recent_cursor end,
      recent_head = case when summary.recent_restart_pending then 0 else greatest(summary.recent_head, head) end,
      recent_complete = not summary.recent_restart_pending,
      recent_synced_at = case when summary.recent_restart_pending then null else now() end,
      recent_note = case when summary.recent_restart_pending
        then 'Catching up newer Telegram history after finishing the retained scan.' else null end,
      recent_restart_pending = false
  where summary.account_id = account and summary.chat_id = chat and head >= 0;
$$;
revoke all on function public.open_tgate_complete_recent_history(uuid, text, bigint) from public, anon, authenticated;
grant execute on function public.open_tgate_complete_recent_history(uuid, text, bigint) to service_role;
notify pgrst, 'reload schema';
