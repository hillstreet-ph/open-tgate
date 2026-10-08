-- Match the global and account-scoped visible inbox order, including NULLS LAST
-- and both tie-breakers. Retained invisible history stays outside these indexes.
create index if not exists open_tgate_chats_visible_global_order_idx
  on public.open_tgate_tg_chats (last_message_at desc nulls last, chat_id asc, account_id asc)
  where is_visible;
create index if not exists open_tgate_chats_visible_account_order_idx
  on public.open_tgate_tg_chats (account_id asc, last_message_at desc nulls last, chat_id asc)
  where is_visible;
