-- Read the existing conversation mirror; do not duplicate messages or invent
-- capture timestamps for historical records. Account/folder joins stay scoped.
create index if not exists open_tgate_message_activity_idx
  on public.open_tgate_tg_messages ((greatest(sent_at, edited_at)) desc, message_id desc)
  where not deleted;
create or replace function public.open_tgate_message_activity(
  account uuid default null, chat_kind text default null, folder integer default null,
  term text default null, result_limit integer default 50, result_offset integer default 0)
returns table(account_id uuid, account_label text, account_type text, chat_id text,
  chat_title text, kind text, folder_ids integer[], message_id text, text text,
  content_type text, sender_id text, is_outgoing boolean, sent_at timestamptz, edited_at timestamptz)
language plpgsql stable security invoker set search_path = '' as $$
begin
  if chat_kind is not null and chat_kind not in ('user','group','channel','bot') then
    raise exception 'invalid_chat_kind';
  end if;
  if folder is not null and (folder <= 0 or account is null) then
    raise exception 'folder_account_required';
  end if;
  if result_limit is null or result_offset is null
     or result_limit not between 1 and 200 or result_offset not between 0 and 100000
     or char_length(coalesce(term,'')) > 1000 then raise exception 'invalid_activity_query'; end if;
  return query select m.account_id,a.label,a.account_type,m.chat_id,c.title,c.kind,c.folder_ids,
    m.message_id::text,m.text,m.content_type,m.sender_id,m.is_outgoing,m.sent_at,m.edited_at
  from public.open_tgate_tg_messages m
  join public.open_tgate_tg_chats c on c.account_id=m.account_id and c.chat_id=m.chat_id
  join public.open_tgate_tg_accounts a on a.id=m.account_id
  where not m.deleted and c.is_visible
    and (account is null or m.account_id=account)
    and (chat_kind is null or c.kind=chat_kind)
    and (folder is null or folder=any(c.folder_ids))
    and (nullif(btrim(term),'') is null or strpos(lower(coalesce(m.text,'')),lower(btrim(term))) > 0)
  order by greatest(m.sent_at,m.edited_at) desc nulls last,m.message_id desc,m.account_id,m.chat_id
  limit result_limit offset result_offset;
end $$;
revoke all on function public.open_tgate_message_activity(uuid,text,integer,text,integer,integer)
  from public,anon,authenticated;
grant execute on function public.open_tgate_message_activity(uuid,text,integer,text,integer,integer)
  to service_role;
notify pgrst,'reload schema';
