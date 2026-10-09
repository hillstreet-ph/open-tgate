-- Run after the audit migration. All fixture writes are rolled back.
begin;
do $$
declare a uuid:=gen_random_uuid(); row public.open_tgate_tg_messages;
begin
 insert into public.open_tgate_tg_accounts(id,label) values(a,'rollback-only audit fixture');
 insert into public.open_tgate_tg_messages(account_id,chat_id,message_id,text,content_type) values(a,'42',1,'captured unedited text','messageText');
 update public.open_tgate_tg_messages set deleted=true where account_id=a and message_id=1;
 select * into row from public.open_tgate_tg_messages where account_id=a and message_id=1;
 assert row.deleted and row.text='captured unedited text','Sparse delete lost captured text';
 assert exists(select 1 from public.open_tgate_audit_events where account_id=a and message_id='1' and event_type='message_deleted' and snapshot->>'text'='captured unedited text'),'Deletion snapshot missing';
 insert into public.open_tgate_tg_messages(account_id,chat_id,message_id,deleted) values(a,'42',2,true);
 update public.open_tgate_tg_messages set text='queued pre-delete snapshot',content_type='messageText',deleted=false where account_id=a and message_id=2;
 select * into row from public.open_tgate_tg_messages where account_id=a and message_id=2;
 assert row.deleted and row.text='queued pre-delete snapshot','Late snapshot lost or resurrected deleted message';
 assert exists(select 1 from public.open_tgate_audit_events where account_id=a and message_id='2' and event_type='deleted_snapshot_captured'),'Delayed capture missing';
 insert into public.open_tgate_tg_messages(account_id,chat_id,message_id,text,content_type) values(a,'42',3,'account removal snapshot','messageText');
 delete from public.open_tgate_tg_accounts where id=a;
 assert exists(select 1 from public.open_tgate_audit_events where account_id=a and message_id='3' and event_type='message_record_removed' and snapshot->>'text'='account removal snapshot'),'Account cascade lost message history';
 assert not has_table_privilege('service_role','public.open_tgate_audit_events','UPDATE'),'Audit UPDATE privilege leaked';
 assert not has_table_privilege('service_role','public.open_tgate_audit_events','DELETE'),'Audit DELETE privilege leaked';
 assert not has_table_privilege('anon','public.open_tgate_audit_events','SELECT'),'Anonymous audit access leaked';
end $$;
rollback;
