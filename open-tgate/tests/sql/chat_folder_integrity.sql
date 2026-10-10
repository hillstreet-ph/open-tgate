-- Run after 20261010115956 as migration administrator. Synthetic rows only;
-- every change rolls back, including account and entity fixtures.
begin;
do $$
declare
  a uuid := gen_random_uuid();
  b uuid := gen_random_uuid();
  row public.open_tgate_tg_chats;
  ordered text[];
begin
  insert into public.open_tgate_tg_accounts(id, label) values(a, 'Folder fixture A'), (b, 'Folder fixture B');
  perform public.open_tgate_chat_folder_position(a, '9', '7', 9223372036854775806);
  perform public.open_tgate_chat_folder_position(a, '9', '8', 12);
  perform public.open_tgate_chat_folder_position(a, '9', 'main', 9223372036854775807);
  perform public.open_tgate_chat_folder_position(a, '9', '7', 9223372036854775806);
  perform public.open_tgate_chat_folder_position(b, '9', '7', 1);
  select * into row from public.open_tgate_tg_chats where account_id = a and chat_id = '9';
  assert row.folder_ids = array[7,8], 'Sparse replay lost or duplicated another folder';
  assert row.folder_positions->>'main' = '9223372036854775807', 'Int64 order lost precision';
  assert row.folder_positions->>'8' = '0000000000000000012', 'Small orders need fixed-width lexical sorting';
  assert not row.is_visible and not row.is_in_main, 'Sparse unknown chat must remain hidden';
  perform public.open_tgate_chat_folder_position(a, '9', '7', 0);
  select * into row from public.open_tgate_tg_chats where account_id = a and chat_id = '9';
  assert row.folder_ids = array[8] and not row.folder_positions ? '7', 'Zero order did not remove only its folder';
  assert (select folder_ids = array[7] from public.open_tgate_tg_chats where account_id = b and chat_id = '9'), 'Cross-account mutation';

  perform public.open_tgate_chat_folder_position(a, '10', '8', 12);
  select array_agg(chat_id order by folder_positions->>'8' desc, telegram_sort_id desc) into ordered
    from public.open_tgate_tg_chats where account_id = a and folder_ids @> array[8];
  assert ordered = array['10','9'], 'Chat-id ties must use descending numeric Telegram IDs';
  begin
    perform public.open_tgate_chat_folder_position(a, '9', 'invalid', 1);
    raise exception 'Invalid chat-list key accepted';
  exception when raise_exception then
    if sqlerrm <> 'invalid_chat_list' then raise; end if;
  end;

  insert into public.open_tgate_tg_chats(account_id,chat_id,peer_user_id,kind) values(a,'99','99','user');
  insert into public.open_tgate_tg_entities(account_id,kind,tg_id) values(a,'bot','99');
  assert (select kind = 'bot' from public.open_tgate_tg_chats where account_id=a and chat_id='99'), 'Later bot entity did not classify private chat';
  insert into public.open_tgate_tg_entities(account_id,kind,tg_id) values(a,'bot','100');
  insert into public.open_tgate_tg_chats(account_id,chat_id,peer_user_id,kind) values(a,'100','100','user'),(b,'100','100','user');
  assert (select kind = 'bot' from public.open_tgate_tg_chats where account_id=a and chat_id='100'), 'Earlier bot entity did not classify private chat';
  assert (select kind = 'user' from public.open_tgate_tg_chats where account_id=b and chat_id='100'), 'Bot classification crossed account boundary';
  update public.open_tgate_tg_chats set kind='user' where account_id=a and chat_id='100';
  assert (select kind = 'bot' from public.open_tgate_tg_chats where account_id=a and chat_id='100'), 'Chat snapshot erased known bot classification';
  assert not has_function_privilege('authenticated','public.open_tgate_chat_folder_position(uuid,text,text,bigint)','execute'), 'Operators can mutate folder mirror';
  assert not has_function_privilege('anon','public.open_tgate_chat_folder_position(uuid,text,text,bigint)','execute'), 'Anonymous folder mutation';
  assert has_function_privilege('service_role','public.open_tgate_chat_folder_position(uuid,text,text,bigint)','execute'), 'Worker lacks folder projection access';
end;
$$;
rollback;
