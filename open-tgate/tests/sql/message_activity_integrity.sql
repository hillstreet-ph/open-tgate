-- Synthetic fixtures only; run after all migrations as migration administrator.
begin;
do $$
declare a uuid := gen_random_uuid(); b uuid := gen_random_uuid(); ids text[];
begin
  insert into public.open_tgate_tg_accounts(id,label) values(a,'Activity A'),(b,'Activity B');
  insert into public.open_tgate_tg_chats(account_id,chat_id,title,kind,is_visible,folder_ids)
    values(a,'1','Group A','group',true,array[2]),(b,'1','Personal B','user',true,array[2]),
      (a,'2','Hidden','group',false,array[2]);
  insert into public.open_tgate_tg_messages(account_id,chat_id,message_id,text,sent_at,deleted)
    values(a,'1',9223372036854775806,'Literal 100% _ query','2026-01-01',false),
      (a,'1',2,'Deleted secret','2026-01-04',true),
      (a,'2',3,'Hidden secret','2026-01-05',false),
      (b,'1',4,'Other account','2026-01-03',false);
  assert (select count(*)=2 from public.open_tgate_message_activity()), 'Hidden or deleted message leaked';
  assert (select count(*)=1 from public.open_tgate_message_activity(a,'group',2)), 'Account/type/folder isolation failed';
  assert (select account_label='Activity A' and chat_title='Group A' and message_id='9223372036854775806'
    from public.open_tgate_message_activity(a,null,null,'100% _')), 'Literal query or int64 precision failed';
  assert (select count(*)=0 from public.open_tgate_message_activity(a,null,null,'Other account')), 'Text search crossed accounts';
  select array_agg(message_id) into ids from public.open_tgate_message_activity(null,null,null,null,1,1);
  assert ids=array['9223372036854775806'], 'Newest-first offset pagination failed';
  -- Arrivals and edits to unseen older rows cannot shift the older page.
  insert into public.open_tgate_tg_messages(account_id,chat_id,message_id,text,sent_at,deleted)
    values(a,'1',5,'New arrival','2026-01-06',false);
  update public.open_tgate_tg_messages set edited_at='2026-01-07' where account_id=a and message_id=9223372036854775806;
  select array_agg(message_id) into ids from public.open_tgate_message_activity(
    null,null,null,null,50,0,'2026-01-03',4,b,'1');
  assert ids=array['9223372036854775806'], 'Arrival or edit shifted cursor page';
  begin
    perform public.open_tgate_message_activity(null,null,2);
    raise exception 'Unscoped folder accepted';
  exception when raise_exception then if sqlerrm<>'folder_account_required' then raise; end if; end;
  begin
    perform public.open_tgate_message_activity(null,null,null,null,null);
    raise exception 'Null limit accepted';
  exception when raise_exception then if sqlerrm<>'invalid_activity_query' then raise; end if; end;
  assert not has_function_privilege('anon','public.open_tgate_message_activity(uuid,text,integer,text,integer,integer,timestamptz,bigint,uuid,text)','execute'), 'Anonymous activity access';
  assert not has_function_privilege('authenticated','public.open_tgate_message_activity(uuid,text,integer,text,integer,integer,timestamptz,bigint,uuid,text)','execute'), 'Direct operator RPC bypass';
  assert has_function_privilege('service_role','public.open_tgate_message_activity(uuid,text,integer,text,integer,integer,timestamptz,bigint,uuid,text)','execute'), 'API lacks read access';
end $$;
rollback;
