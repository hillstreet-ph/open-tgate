-- An unknown Telegram last message can suppress live message updates. Request
-- one chat's catch-up atomically without discarding an unfinished scan.
create or replace function public.open_tgate_request_recent_history(account uuid, chat text)
returns void language sql security invoker set search_path to '' as $$
  update public.open_tgate_tg_chats as summary
  set recent_cursor = case when summary.recent_complete then 0 else summary.recent_cursor end,
      recent_head = case when summary.recent_complete then 0 else summary.recent_head end,
      recent_boundary = case when summary.recent_complete
        then summary.latest_synced_message_id else summary.recent_boundary end,
      recent_restart_pending = not summary.recent_complete,
      recent_synced_at = case when summary.recent_complete then null else summary.recent_synced_at end,
      recent_note = 'Catching up after Telegram reported an unknown last message.',
      recent_complete = false
  where summary.account_id = account and summary.chat_id = chat;
$$;
revoke all on function public.open_tgate_request_recent_history(uuid, text) from public, anon, authenticated;
grant execute on function public.open_tgate_request_recent_history(uuid, text) to service_role;
notify pgrst, 'reload schema';
