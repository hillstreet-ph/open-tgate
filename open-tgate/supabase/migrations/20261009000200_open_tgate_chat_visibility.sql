-- Keep the historical mirror while following Telegram's visible chat lists.
-- Existing rows stay visible until the worker refreshes their full positions.
alter table public.open_tgate_tg_chats
  add column if not exists is_in_main boolean not null default true,
  add column if not exists is_in_archive boolean not null default false,
  add column if not exists is_visible boolean not null default true;

comment on column public.open_tgate_tg_chats.is_visible is
  'True when Telegram reports membership in its main or archive list. Hidden chat history is retained.';

notify pgrst, 'reload schema';
