-- Run after knowledge-search migration as migration administrator. Fixtures roll back.
begin;
do $$
declare
  old_source uuid := gen_random_uuid();
  disabled_source uuid := gen_random_uuid();
  pending_source uuid := gen_random_uuid();
  excluded_source uuid := gen_random_uuid();
  fixture_email text := 'rollback-only-' || gen_random_uuid()::text || '@example.invalid';
  row_count integer;
  sample record;
begin
  insert into public.open_tgate_knowledge_sources (id, title, content, approved, enabled, created_at)
    values (old_source, 'Rollback-only old policy', 'zqfixture zqfixturepolicies refunds require a receipt.', true, true, now() - interval '1 year');
  -- More than the former 200-row window of newer unrelated sources.
  insert into public.open_tgate_knowledge_sources (title, content, approved, enabled)
    select 'Rollback-only newer source ' || series, 'irrelevant newer facts', true, true
    from generate_series(1, 205) as series;
  insert into public.open_tgate_knowledge_sources (id, title, content, approved, enabled)
    values (disabled_source, 'Rollback-only disabled policy', 'zqfixture refunds unsupported', true, false),
           (pending_source, 'Rollback-only pending policy', 'zqfixture refunds unsupported', false, true),
           (excluded_source, 'Rollback-only approved policy', 'nonmatching words', true, true);
  assert exists (select 1 from public.open_tgate_search_knowledge('zqfixture', 5) where id = old_source),
    'Approved source older than 200 newer sources disappeared from retrieval';
  assert not exists (select 1 from public.open_tgate_search_knowledge('zqfixture', 5)
    where id in (disabled_source, pending_source, excluded_source)),
    'Disabled, unapproved or irrelevant source participated in retrieval';
  assert exists (select 1 from public.open_tgate_search_knowledge('zqfixturepolicy', 5) where id = old_source),
    'Lexical/stemmed business term did not retrieve approved policy';
  assert not exists (select 1 from public.open_tgate_search_knowledge('', 5)),
    'Empty query returned knowledge';
  assert not exists (select 1 from public.open_tgate_search_knowledge('the and is', 5)),
    'Stopword-only query returned knowledge';
  select count(*) into row_count from public.open_tgate_search_knowledge('rollback', 999);
  assert row_count <= 5, 'Retrieval result cap was not enforced';
  for sample in select * from public.open_tgate_search_knowledge('zqfixture', 5) loop
    assert char_length(sample.excerpt) <= 1600, 'Snippet exceeded length bound';
  end loop;
  assert not has_function_privilege('anon', 'public.open_tgate_search_knowledge(text,integer)', 'execute'),
    'Anonymous callers can execute service-only retrieval';
  assert not has_function_privilege('authenticated', 'public.open_tgate_search_knowledge(text,integer)', 'execute'),
    'Authenticated callers can bypass operator validation';
  assert has_function_privilege('service_role', 'public.open_tgate_search_knowledge(text,integer)', 'execute'),
    'API service cannot execute retrieval';
  insert into public.open_tgate_operators (email, is_active) values (upper(fixture_email), true);
  assert public.open_tgate_api_key_owner_active(fixture_email), 'Mixed-case active owner was rejected';
  update public.open_tgate_operators set is_active = false where email = upper(fixture_email);
  assert not public.open_tgate_api_key_owner_active(fixture_email), 'Inactive owner was accepted';
  assert not public.open_tgate_api_key_owner_active('missing-' || fixture_email), 'Absent owner was accepted';
  assert not has_function_privilege('anon', 'public.open_tgate_api_key_owner_active(text)', 'execute'),
    'Anonymous callers can query key-owner activation';
  assert not has_function_privilege('authenticated', 'public.open_tgate_api_key_owner_active(text)', 'execute'),
    'Authenticated callers can query key-owner activation';
  assert has_function_privilege('service_role', 'public.open_tgate_api_key_owner_active(text)', 'execute'),
    'API service cannot query key-owner activation';
end;
$$;
rollback;
