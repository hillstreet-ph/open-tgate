-- All fixtures and role changes are confined to a rollback transaction.
begin;
insert into public.open_tgate_operators (email) values ('tgate-rls-check@example.invalid');
insert into public.open_tgate_knowledge_sources (title,content)
values ('rollback-only RLS fixture','operator-visible fixture');
set local role authenticated;
select set_config('request.jwt.claims', '{"email":"tgate-nonoperator@example.invalid"}', true);
do $$ begin
  assert not public.open_tgate_is_operator(), 'Nonoperator predicate allowed';
  assert (select count(*) = 0 from public.open_tgate_knowledge_sources), 'Nonoperator saw knowledge';
  assert (select count(*) = 0 from public.open_tgate_tg_chats), 'Nonoperator saw chats';
  assert (select count(*) = 0 from public.open_tgate_tg_messages), 'Nonoperator saw messages';
  assert not has_table_privilege(current_user, 'public.open_tgate_api_keys','SELECT'), 'Key hash exposed';
  assert not has_table_privilege(current_user, 'public.open_tgate_tg_messages','INSERT'), 'Operator write granted';
end $$;
select set_config('request.jwt.claims', '{"email":"tgate-rls-check@example.invalid"}', true);
do $$ begin
  assert public.open_tgate_is_operator(), 'Operator predicate denied';
  assert (select count(*) = 1 from public.open_tgate_knowledge_sources where title='rollback-only RLS fixture'), 'Operator read denied';
end $$;
reset role;
do $$ begin
  assert not has_table_privilege('anon', 'public.open_tgate_tg_messages','SELECT'), 'Anonymous history exposed';
  assert not has_table_privilege('anon', 'public.open_tgate_knowledge_sources','SELECT'), 'Anonymous knowledge exposed';
  assert not has_table_privilege('anon', 'public.open_tgate_api_keys','SELECT'), 'Anonymous key hashes exposed';
end $$;
rollback;
