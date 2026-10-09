-- Match the shared workspace contacts predicate and complete page order.
-- Non-contact entities do not occupy these indexes. With an account equality
-- filter, account_id is constant and the remaining title/tg_id order is enough.
create index if not exists open_tgate_contacts_global_order_idx
  on public.open_tgate_tg_entities (title asc nulls last, tg_id asc, account_id asc)
  where kind = 'contact';
create index if not exists open_tgate_contacts_account_order_idx
  on public.open_tgate_tg_entities (account_id asc, title asc nulls last, tg_id asc)
  where kind = 'contact';
