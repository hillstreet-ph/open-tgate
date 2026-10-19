-- Immutable observed history. Retains records even if an account is removed.
-- Deliberately contains no login payload, token, code, password or raw TDLib event.
create table public.open_tgate_audit_events (
 id bigint generated always as identity primary key, account_id uuid not null,
 chat_id text, message_id text, event_type text not null,
 observed_at timestamptz not null default now(), snapshot jsonb not null default '{}'
);
create index on public.open_tgate_audit_events(account_id,id desc);
create index on public.open_tgate_audit_events(account_id,chat_id,id desc);
alter table public.open_tgate_audit_events enable row level security;
revoke all on public.open_tgate_audit_events from public,anon,authenticated,service_role;
grant select,insert on public.open_tgate_audit_events to service_role;
revoke all on sequence public.open_tgate_audit_events_id_seq from public,anon,authenticated,service_role;
grant usage,select on sequence public.open_tgate_audit_events_id_seq to service_role;

create or replace function public.open_tgate_preserve_message_updates()
returns trigger language plpgsql set search_path='' as $$
begin
 if old.deleted then new.deleted=true; end if;
 -- A sparse delete must retain the last captured snapshot, including unedited text.
 if (new.deleted and (old.text is not null or old.content_type is not null)) or (old.edited_at is not null and (
 new.edited_at is null or old.edited_at>new.edited_at or (old.edited_at=new.edited_at
 and coalesce(old.meta->>'_mirror_source','current')='current' and new.meta->>'_mirror_source'='history')))
 then
  new.text=old.text; new.content_type=old.content_type; new.edited_at=old.edited_at;
  new.meta=old.meta; new.sent_at=old.sent_at; new.sender_id=old.sender_id; new.is_outgoing=old.is_outgoing;
 end if;
 return new;
end $$;

create function public.open_tgate_capture_audit_event()
returns trigger language plpgsql security definer set search_path='' as $$
declare kind text; a uuid; c text; m text; data jsonb;
begin
 if tg_table_name='open_tgate_tg_messages' then
  if tg_op='DELETE' then
   a=old.account_id;c=old.chat_id;m=old.message_id::text;kind='message_record_removed';
   data=jsonb_build_object('text',old.text,'content_type',old.content_type,'sent_at',old.sent_at,'edited_at',old.edited_at,'sender_id',old.sender_id,'is_outgoing',old.is_outgoing,'captured',old.text is not null);
  else
  a=new.account_id;c=new.chat_id;m=new.message_id::text;
  if tg_op='INSERT' and new.deleted then kind='message_deleted';
  elsif tg_op='UPDATE' and new.deleted and not old.deleted then kind='message_deleted';
  elsif tg_op='UPDATE' and new.deleted and old.text is null and new.text is not null then kind='deleted_snapshot_captured';
  elsif tg_op='UPDATE' and not new.deleted and (new.text,new.edited_at,new.content_type) is distinct from (old.text,old.edited_at,old.content_type) then kind='message_edited';
  else return new; end if;
  data=jsonb_build_object('text',new.text,'content_type',new.content_type,'sent_at',new.sent_at,'edited_at',new.edited_at,'sender_id',new.sender_id,'is_outgoing',new.is_outgoing,'captured',new.text is not null);
  if kind='message_edited' then data=data||jsonb_build_object('previous_text',old.text); end if;
  end if;
 elsif tg_table_name='open_tgate_tg_chats' then
  if tg_op='DELETE' then a=old.account_id;c=old.chat_id;kind='chat_record_removed';data=jsonb_build_object('title',old.title);
  elsif tg_op='UPDATE' and old.is_visible and not new.is_visible then a=new.account_id;c=new.chat_id;kind='chat_removed';data=jsonb_build_object('title',new.title);
  else return new; end if;
 elsif tg_table_name='open_tgate_tg_accounts' then
  if tg_op='DELETE' then a=old.id;kind='account_removed';data=jsonb_build_object('label',old.label,'status',old.status);
  elsif tg_op='INSERT' then a=new.id;kind='account_created';data=jsonb_build_object('label',new.label,'status',new.status);
  elsif (new.status,new.connection_state,new.sync_step) is distinct from (old.status,old.connection_state,old.sync_step) then
   a=new.id;kind='account_state_changed';data=jsonb_build_object('label',new.label,'status',new.status,'previous_status',old.status,'connection_state',new.connection_state,'sync_step',new.sync_step);
  else return new; end if;
 else
  if tg_op='UPDATE' and new.status is not distinct from old.status then return new; end if;
  a=new.account_id;kind='login_command';data=jsonb_build_object('action',new.action,'status',new.status,'command_id',new.id::text);
 end if;
 insert into public.open_tgate_audit_events(account_id,chat_id,message_id,event_type,snapshot) values(a,c,m,kind,data);
 if tg_op='DELETE' then return old; end if;
 return new;
end $$;
revoke all on function public.open_tgate_capture_audit_event() from public,anon,authenticated;
create trigger open_tgate_message_audit after insert or update or delete on public.open_tgate_tg_messages for each row execute function public.open_tgate_capture_audit_event();
create trigger open_tgate_chat_audit after update or delete on public.open_tgate_tg_chats for each row execute function public.open_tgate_capture_audit_event();
create trigger open_tgate_account_audit after insert or update or delete on public.open_tgate_tg_accounts for each row execute function public.open_tgate_capture_audit_event();
create trigger open_tgate_login_audit after insert or update on public.open_tgate_login_commands for each row execute function public.open_tgate_capture_audit_event();
-- Existing tombstones are marked as historical imports, never given invented deletion times.
insert into public.open_tgate_audit_events(account_id,chat_id,message_id,event_type,snapshot)
select account_id,chat_id,message_id::text,'existing_deletion_marker',jsonb_build_object('text',text,'captured',text is not null,'deletion_time_known',false)
from public.open_tgate_tg_messages where deleted;
notify pgrst,'reload schema';
