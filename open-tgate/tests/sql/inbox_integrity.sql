-- Run after inbox migration as migration administrator. All fixture writes roll back.
-- Reproduce PostgREST ON CONFLICT semantics for sparse realtime updates versus
-- full backfill rows, including delete-before-history and delayed edit races.
begin;
do $$
declare
  fixture_account uuid := gen_random_uuid();
  mirrored public.open_tgate_tg_messages%rowtype;
  original_edit timestamptz := '2026-10-08T20:00:00Z';
begin
  insert into public.open_tgate_tg_accounts (id, label) values (fixture_account, 'rollback-only integrity fixture');
  insert into public.open_tgate_tg_messages (account_id, chat_id, message_id, text, content_type, edited_at)
    values (fixture_account, '42', 100, 'first edit', 'messageText', original_edit);

  -- A sparse realtime content event omits edited_at: conflict UPDATE touches
  -- only the supplied columns, retaining existing edit timestamp and new text.
  insert into public.open_tgate_tg_messages (account_id, chat_id, message_id, text, content_type)
    values (fixture_account, '42', 100, 'latest realtime edit', 'messageText')
    on conflict (account_id, chat_id, message_id)
    do update set text = excluded.text, content_type = excluded.content_type;
  select * into mirrored from public.open_tgate_tg_messages
    where account_id = fixture_account and chat_id = '42' and message_id = 100;
  assert mirrored.text = 'latest realtime edit', 'Sparse realtime content was incorrectly reverted';
  assert mirrored.edited_at = original_edit, 'Sparse realtime event cleared edit timestamp';

  -- A delayed full history snapshot carrying no edit must retain newer content.
  insert into public.open_tgate_tg_messages (account_id, chat_id, message_id, text, content_type, edited_at)
    values (fixture_account, '42', 100, 'old backfill content', 'messageText', null)
    on conflict (account_id, chat_id, message_id)
    do update set text = excluded.text, content_type = excluded.content_type, edited_at = excluded.edited_at;
  select * into mirrored from public.open_tgate_tg_messages
    where account_id = fixture_account and chat_id = '42' and message_id = 100;
  assert mirrored.text = 'latest realtime edit', 'Stale history overwrote newer edit';
  assert mirrored.edited_at = original_edit, 'Stale history cleared newer edit timestamp';

  -- A complete message with a newer edit date must advance the mirror.
  update public.open_tgate_tg_messages set text = 'newer complete message', edited_at = original_edit + interval '1 second'
    where account_id = fixture_account and chat_id = '42' and message_id = 100;
  select * into mirrored from public.open_tgate_tg_messages
    where account_id = fixture_account and chat_id = '42' and message_id = 100;
  assert mirrored.text = 'newer complete message', 'Newer full edit was incorrectly reverted';

  -- Telegram edit dates have only second precision. A current full snapshot
  -- must win over repeated delayed history snapshots with the SAME timestamp.
  update public.open_tgate_tg_messages set text = 'second edit in same second',
      meta = '{"_mirror_source":"current"}'::jsonb
    where account_id = fixture_account and chat_id = '42' and message_id = 100;
  for retry in 1..2 loop
    insert into public.open_tgate_tg_messages
      (account_id, chat_id, message_id, text, content_type, edited_at, meta)
      values (fixture_account, '42', 100, 'delayed first edit', 'messageText',
              original_edit + interval '1 second', '{"_mirror_source":"history"}'::jsonb)
      on conflict (account_id, chat_id, message_id) do update
      set text = excluded.text, content_type = excluded.content_type,
          edited_at = excluded.edited_at, meta = excluded.meta;
    select * into mirrored from public.open_tgate_tg_messages
      where account_id = fixture_account and chat_id = '42' and message_id = 100;
    assert mirrored.text = 'second edit in same second', 'Equal-date stale history overwrote a current edit';
    assert mirrored.meta ->> '_mirror_source' = 'current', 'Rejected history cleared current provenance';
  end loop;
  update public.open_tgate_tg_messages set text = 'third current edit in same second',
      meta = '{"_mirror_source":"current"}'::jsonb
    where account_id = fixture_account and chat_id = '42' and message_id = 100;
  assert (select text = 'third current edit in same second' from public.open_tgate_tg_messages
    where account_id = fixture_account and chat_id = '42' and message_id = 100),
    'Equal-date current refresh could not advance the latest revision';

  -- Permanent delete may precede the first full history row.
  insert into public.open_tgate_tg_messages (account_id, chat_id, message_id, deleted)
    values (fixture_account, '42', 200, true);
  insert into public.open_tgate_tg_messages (account_id, chat_id, message_id, text, deleted)
    values (fixture_account, '42', 200, 'deleted historical message', false)
    on conflict (account_id, chat_id, message_id)
    do update set text = excluded.text, deleted = excluded.deleted;
  select * into mirrored from public.open_tgate_tg_messages
    where account_id = fixture_account and chat_id = '42' and message_id = 200;
  assert mirrored.deleted, 'Backfill resurrected a permanently deleted message';

  insert into public.open_tgate_tg_chats (account_id, chat_id, history_note)
    values (fixture_account, '42', 'Cannot safely advance local message anchor');
  assert (select not history_complete and history_note is not null
    from public.open_tgate_tg_chats where account_id = fixture_account and chat_id = '42'),
    'An incomplete history page lost its operator explanation';
  assert (select is_in_main and not is_in_archive and is_visible
    from public.open_tgate_tg_chats where account_id = fixture_account and chat_id = '42'),
    'The additive visibility migration hid an existing chat';
  update public.open_tgate_tg_chats set is_in_main = false, is_in_archive = false, is_visible = false
    where account_id = fixture_account and chat_id = '42';
  assert (select count(*) = 2 from public.open_tgate_tg_messages
    where account_id = fixture_account and chat_id = '42'),
    'Hiding a removed chat destroyed its historical mirror';

  update public.open_tgate_tg_chats set history_cursor = 1234, history_complete = false,
      recent_cursor = 800, recent_complete = false, recent_boundary = 100,
      recent_head = 900, latest_synced_message_id = 1000
    where account_id = fixture_account and chat_id = '42';
  perform public.open_tgate_prepare_recent_history(fixture_account);
  assert (select history_cursor = 1234 and not history_complete and recent_cursor = 0
      and recent_head = 0 and recent_boundary = 100 and not recent_complete
    from public.open_tgate_tg_chats where account_id = fixture_account and chat_id = '42'),
    'Restart lost older progress, advanced an unfinished boundary, or skipped a new offline gap';
  update public.open_tgate_tg_chats set recent_complete = true, latest_synced_message_id = 2000
    where account_id = fixture_account and chat_id = '42';
  perform public.open_tgate_prepare_recent_history(fixture_account);
  assert (select history_cursor = 1234 and not history_complete and recent_boundary = 2000
      and not recent_complete from public.open_tgate_tg_chats
    where account_id = fixture_account and chat_id = '42'),
    'Completed recent catch-up did not retain its watermark for the next restart';
  assert not has_function_privilege('anon', 'public.open_tgate_prepare_recent_history(uuid)', 'execute'),
    'Anonymous callers can alter history checkpoints';
  assert not has_function_privilege('authenticated', 'public.open_tgate_prepare_recent_history(uuid)', 'execute'),
    'Authenticated callers can alter history checkpoints';
  assert has_function_privilege('service_role', 'public.open_tgate_prepare_recent_history(uuid)', 'execute'),
    'Worker service cannot prepare recent catch-up';
end;
$$;
rollback;
