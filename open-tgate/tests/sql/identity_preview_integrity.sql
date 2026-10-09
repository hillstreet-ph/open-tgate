-- Identity and delayed-preview races; no fixture data survives this transaction.
begin;
do $$
declare
  slot uuid := gen_random_uuid();
  unknown_slot uuid := gen_random_uuid();
  missing_slot uuid := gen_random_uuid();
begin
  insert into public.open_tgate_tg_accounts (id, label) values
    (slot, 'rollback-only identity fixture'), (unknown_slot, 'rollback-only unknown legacy fixture');
  assert public.open_tgate_bind_identity(slot, 901), 'Empty slot could not bind its first verified identity';
  assert public.open_tgate_bind_identity(slot, 901), 'Same identity could not reconnect';
  assert not public.open_tgate_bind_identity(slot, 902), 'Different identity was allowed to reuse a slot';
  assert not public.open_tgate_bind_identity(slot, 0), 'Invalid identity was accepted';
  assert not public.open_tgate_bind_identity(missing_slot, 901), 'Missing slot was treated as bound';
  begin
    update public.open_tgate_tg_accounts set tg_user_id = '902' where id = slot;
    raise exception 'Identity mutation bypassed the guard';
  exception when check_violation then null;
  end;
  begin
    update public.open_tgate_tg_accounts set tg_user_id = null where id = slot;
    raise exception 'Identity clearing bypassed the guard';
  exception when check_violation then null;
  end;
  update public.open_tgate_tg_accounts set label = 'Normal metadata updates remain permitted' where id = slot;
  assert (select tg_user_id = '901' from public.open_tgate_tg_accounts where id = slot),
    'Metadata update changed the immutable identity';
  insert into public.open_tgate_tg_chats (account_id, chat_id) values (unknown_slot, '42');
  assert not public.open_tgate_bind_identity(unknown_slot, 903),
    'Unbound slot silently adopted unknown pre-existing mirror rows';

  insert into public.open_tgate_tg_chats
    (account_id, chat_id, last_message_id, last_message, last_message_at)
    values (slot, '42', 100, 'original latest', '2026-10-09T00:00:00Z');
  insert into public.open_tgate_tg_messages (account_id, chat_id, message_id, text)
    values (slot, '42', 100, 'edited latest');
  perform public.open_tgate_refresh_last_preview(slot, '42', 100, 'edited latest');
  assert (select last_message = 'edited latest' and last_message_at = '2026-10-09T00:00:00Z'
    from public.open_tgate_tg_chats where account_id = slot and chat_id = '42'),
    'Still-current revision did not refresh preview or changed message time';
  update public.open_tgate_tg_chats set last_message_id = 101, last_message = 'newer latest'
    where account_id = slot and chat_id = '42';
  perform public.open_tgate_refresh_last_preview(slot, '42', 100, 'delayed older revision');
  assert (select last_message = 'newer latest' from public.open_tgate_tg_chats
    where account_id = slot and chat_id = '42'), 'Delayed older revision replaced a newer preview';
  update public.open_tgate_tg_messages set deleted = true
    where account_id = slot and chat_id = '42' and message_id = 100;
  assert (select last_message = 'newer latest' from public.open_tgate_tg_chats
    where account_id = slot and chat_id = '42'), 'Deletion of an older message cleared the newer preview';
  insert into public.open_tgate_tg_messages (account_id, chat_id, message_id, deleted)
    values (slot, '42', 101, true);
  assert (select last_message = '' from public.open_tgate_tg_chats
    where account_id = slot and chat_id = '42'), 'Inaccessible last message remained exposed in preview';
  perform public.open_tgate_refresh_last_preview(slot, '42', 101, 'forbidden late edit');
  update public.open_tgate_tg_chats set last_message = 'forbidden late snapshot'
    where account_id = slot and chat_id = '42';
  assert (select last_message = '' from public.open_tgate_tg_chats
    where account_id = slot and chat_id = '42'), 'Delayed edit/snapshot resurrected deleted preview';

  assert not has_function_privilege('anon', 'public.open_tgate_bind_identity(uuid,bigint)', 'execute')
    and not has_function_privilege('authenticated', 'public.open_tgate_bind_identity(uuid,bigint)', 'execute')
    and has_function_privilege('service_role', 'public.open_tgate_bind_identity(uuid,bigint)', 'execute'),
    'Identity binding RPC grants are incorrect';
  assert not has_function_privilege('anon', 'public.open_tgate_refresh_last_preview(uuid,text,bigint,text)', 'execute')
    and not has_function_privilege('authenticated', 'public.open_tgate_refresh_last_preview(uuid,text,bigint,text)', 'execute')
    and has_function_privilege('service_role', 'public.open_tgate_refresh_last_preview(uuid,text,bigint,text)', 'execute'),
    'Conditional preview RPC grants are incorrect';
end;
$$;
rollback;
