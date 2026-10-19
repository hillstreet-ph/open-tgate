-- Run as migration administrator; validate index-supported global/account order.
-- No production account data is changed. Planner settings roll back.
begin;
set local enable_seqscan = off;
set local enable_bitmapscan = off;
do $$
declare
  plan jsonb;
  description text;
begin
  execute 'explain (format json) select account_id, chat_id, title from public.open_tgate_tg_chats
    where is_visible = true and is_archived = false
    order by last_message_at desc nulls last, chat_id asc, account_id asc limit 50' into plan;
  description := plan::text;
  assert description like '%open_tgate_chats_visible_global_order_idx%', 'Global inbox order has no matching index';
  assert description not like '%"Node Type": "Sort"%', 'Global inbox requires an explicit sort';
  assert description not like '%"Node Type": "Incremental Sort"%', 'Global inbox requires an incremental sort';

  execute 'explain (format json) select account_id, chat_id, title from public.open_tgate_tg_chats
    where is_visible = true and account_id = ''00000000-0000-0000-0000-000000000001''::uuid
    order by last_message_at desc nulls last, chat_id asc, account_id asc limit 50' into plan;
  description := plan::text;
  assert description like '%open_tgate_chats_visible_account_order_idx%', 'Account inbox order has no matching index';
  assert description not like '%"Node Type": "Sort"%', 'Account inbox requires an explicit sort';
  assert description not like '%"Node Type": "Incremental Sort"%', 'Account inbox requires an incremental sort';
end;
$$;
rollback;
