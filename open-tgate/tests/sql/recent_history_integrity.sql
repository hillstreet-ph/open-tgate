-- Run as migration administrator; fixture writes and checkpoints all roll back.
begin;
do $$
declare
  fixture_account uuid := gen_random_uuid();
  other_account uuid := gen_random_uuid();
begin
  insert into public.open_tgate_tg_accounts (id, label) values
    (fixture_account, 'rollback-only targeted-history fixture'),
    (other_account, 'rollback-only target-isolation fixture');
  insert into public.open_tgate_tg_chats
    (account_id, chat_id, history_cursor, history_complete, recent_cursor,
     recent_head, recent_boundary, recent_complete, latest_synced_message_id, recent_synced_at)
  values
    (fixture_account, '42', 1234, true, 700, 900, 100, true, 900, now()),
    (fixture_account, '43', 2222, true, 600, 800, 200, true, 800, now()),
    (other_account, '42', 3333, true, 500, 700, 300, true, 700, now());

  perform public.open_tgate_request_recent_history(fixture_account, '42');
  assert (select history_cursor = 1234 and history_complete and recent_cursor = 0
      and recent_head = 0 and recent_boundary = 900 and not recent_complete
      and not recent_restart_pending and recent_synced_at is null
    from public.open_tgate_tg_chats where account_id = fixture_account and chat_id = '42'),
    'Targeted catch-up did not reopen the completed recent lane while retaining older history';
  assert (select recent_complete and recent_cursor = 600 and recent_boundary = 200
    from public.open_tgate_tg_chats where account_id = fixture_account and chat_id = '43'),
    'Targeted catch-up touched another chat';
  assert (select recent_complete and recent_cursor = 500 and recent_boundary = 300
    from public.open_tgate_tg_chats where account_id = other_account and chat_id = '42'),
    'Targeted catch-up touched another account';

  update public.open_tgate_tg_chats set recent_cursor = 1100, recent_head = 1200
    where account_id = fixture_account and chat_id = '42';
  perform public.open_tgate_request_recent_history(fixture_account, '42');
  perform public.open_tgate_request_recent_history(fixture_account, '42');
  assert (select recent_cursor = 1100 and recent_head = 1200 and recent_boundary = 900
      and not recent_complete and recent_restart_pending
    from public.open_tgate_tg_chats where account_id = fixture_account and chat_id = '42'),
    'Repeated targeted requests discarded an active cursor instead of coalescing a newer gap';
  perform public.open_tgate_complete_recent_history(fixture_account, '42', 1200);
  assert (select recent_cursor = 0 and recent_head = 0 and recent_boundary = 1200
      and latest_synced_message_id = 1200 and not recent_complete and not recent_restart_pending
    from public.open_tgate_tg_chats where account_id = fixture_account and chat_id = '42'),
    'Retained scan completion did not stage its queued targeted gap';
  perform public.open_tgate_complete_recent_history(fixture_account, '42', 1300);
  assert (select recent_complete and latest_synced_message_id = 1300 and recent_note is null
    from public.open_tgate_tg_chats where account_id = fixture_account and chat_id = '42'),
    'Targeted scan could not finish normally';
  perform public.open_tgate_request_recent_history(fixture_account, 'missing');
  assert (select recent_complete from public.open_tgate_tg_chats
    where account_id = fixture_account and chat_id = '42'),
    'Request for a missing chat mutated existing checkpoints';

  assert not has_function_privilege('anon', 'public.open_tgate_request_recent_history(uuid,text)', 'execute')
      and not has_function_privilege('authenticated', 'public.open_tgate_request_recent_history(uuid,text)', 'execute'),
    'Untrusted callers can reset targeted history checkpoints';
  assert has_function_privilege('service_role', 'public.open_tgate_request_recent_history(uuid,text)', 'execute'),
    'Worker service cannot request targeted catch-up';
end;
$$;
rollback;
